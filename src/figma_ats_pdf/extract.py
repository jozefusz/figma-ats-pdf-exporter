"""Walk a Figma node tree (as returned by figma_client.get_node) and flatten
it into the drawable primitives defined in model.py.

Deliberately simple for Phase 1: single dominant style per text node (no
support yet for mixed-style runs via styleOverrideTable), and only SOLID
fills. Both are documented gaps, not silent failures -- extract() will
raise if it hits a text node it can't represent faithfully, rather than
guessing.
"""

from __future__ import annotations

import re

from .model import PageDoc, Rect, RGBColor, TextRun, VectorIcon


class ExtractError(RuntimeError):
    pass


# The bundled fonts (Bricolage Grotesque / Fragment Mono) have no emoji
# glyph coverage, so any emoji would otherwise vanish silently on export
# (a real gap between source and rendered text, invisible unless you diff
# them). Stripping them here, at the text-content level, keeps source and
# rendered text in sync and keeps punctuation/spacing sane.
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # symbols & pictographs, emoticons, transport, supplemental
    "\U00002600-\U000027BF"  # misc symbols & dingbats
    "\U0001F1E6-\U0001F1FF"  # regional indicators (flag letters)
    "\U00002B00-\U00002BFF"  # misc symbols and arrows (stars, etc.)
    "\U0000FE00-\U0000FE0F"  # variation selectors (emoji presentation)
    "\U0000200D"  # zero-width joiner
    "]+"
)


def _strip_emoji(text: str) -> str:
    text = _EMOJI_RE.sub("", text)
    # Collapse the whitespace an emoji's removal can leave behind (e.g.
    # "Hello🔥, and" -> "Hello, and") without otherwise reflowing the text.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text


def _apply_text_case(text: str, text_case: str) -> str:
    """Figma stores the author's original mixed-case characters and applies
    a display-only `textCase` transform (UPPER/LOWER/TITLE/SMALL_CAPS...).
    The Figma API only ever returns the original characters, so without
    this the PDF renders the untransformed text -- e.g. "Project overview &
    goals" instead of the "PROJECT OVERVIEW & GOALS" actually shown on the
    canvas. We bake the transform into the drawn string since
    there's no small-caps glyph variant in the embedded fonts to fall back
    on for SMALL_CAPS/SMALL_CAPS_FORCED -- upper-casing is the closest
    visual match available.
    """
    if text_case == "UPPER":
        return text.upper()
    if text_case == "LOWER":
        return text.lower()
    if text_case == "TITLE":
        return text.title()
    if text_case in ("SMALL_CAPS", "SMALL_CAPS_FORCED"):
        return text.upper()
    return text


def _first_solid_fill(node: dict) -> RGBColor | None:
    for fill in node.get("fills") or []:
        if fill.get("visible", True) and fill.get("type") == "SOLID":
            c = fill["color"]
            return RGBColor(c["r"], c["g"], c["b"], fill.get("opacity", c.get("a", 1.0)))
    return None


def _visible_fills(node: dict) -> list[dict]:
    return [f for f in node.get("fills") or [] if f.get("visible", True)]


def _has_visible_stroke(node: dict) -> bool:
    return bool(node.get("strokeWeight")) and any(s.get("visible", True) for s in node.get("strokes") or [])


def _describe(node: dict) -> str:
    return f"{node.get('type')} {node.get('name')!r} (id {node.get('id')})"


def _node_visible(node: dict) -> bool:
    return node.get("visible", True) and node.get("opacity", 1.0) > 0


def _has_effects(node: dict) -> bool:
    return any(e.get("visible", True) for e in node.get("effects") or [])


def _resolve_style(node: dict, warnings: list[str]) -> dict:
    """Merge in a character style override, when the whole run uses at
    most one *meaningful* override (the common case for a hyperlinked
    run, which Figma represents as a styleOverrideTable entry even though
    every character maps to the same one). Index 0 means "base style";
    an override entry that's an empty dict carries no actual property
    differences and is treated as a no-op. Raises only if a run mixes two
    or more genuinely different, non-empty overrides -- that's not yet
    supported.
    """
    style = dict(node.get("style", {}))
    overrides = node.get("characterStyleOverrides")
    if not overrides:
        return style

    table = node.get("styleOverrideTable", {})
    meaningful: list[tuple[int, dict]] = []
    for idx in set(overrides):
        if idx == 0:
            continue
        entry = table.get(str(idx))
        if entry is None:
            raise ExtractError(
                f"Text node {node.get('id')} ({node.get('name')!r}) references "
                f"override index {idx} missing from styleOverrideTable."
            )
        if entry:
            meaningful.append((idx, entry))
        else:
            warnings.append(
                f"Text node {node.get('id')} ({node.get('name')!r}) has an "
                f"empty style override (index {idx}) -- ignoring it and "
                "using the base style for those characters."
            )

    if len(meaningful) > 1:
        raise ExtractError(
            f"Text layer {node.get('name')!r} (id {node.get('id')}) mixes two or more "
            "different inline styles (e.g. a bold word and a coloured word), which is not "
            "supported. Fix: split it into separate text layers, one style per layer."
        )
    if meaningful:
        chars = node.get("characters", "")
        fully_covered = len(overrides) >= len(chars) and 0 not in overrides[: len(chars)]
        if not fully_covered:
            if set(meaningful[0][1]) <= {"hyperlink"}:
                warnings.append(
                    f"Text layer {node.get('name')!r} (id {node.get('id')}) links only part of its "
                    "text; the whole line will be linked. Put the link in its own text layer."
                )
            else:
                raise ExtractError(
                    f"Text layer {node.get('name')!r} (id {node.get('id')}) styles only part of its "
                    "text differently (e.g. one bold word). Applying that style to the whole layer "
                    "would silently restyle it, so this is refused. Fix: split it into separate "
                    "text layers, one style per layer."
                )
        _, override_style = meaningful[0]
        style = {**style, **override_style}
    return style


def _extract_text(node: dict, origin_x: float, origin_y: float, warnings: list[str]) -> TextRun:
    style = _resolve_style(node, warnings)
    box = node["absoluteBoundingBox"]
    color = _first_solid_fill(node) or RGBColor(0, 0, 0, 1)
    fills = _visible_fills(node)
    if fills and _first_solid_fill(node) is None:
        kinds = "/".join(sorted({f.get("type", "?") for f in fills}))
        warnings.append(
            f"{_describe(node)} has a {kinds} fill, which is not supported: its text is drawn "
            "in solid black. Fix: use a solid colour."
        )
    if _has_visible_stroke(node):
        warnings.append(f"{_describe(node)} has a text stroke, which is ignored. Fix: remove the stroke.")
    hyperlink = style.get("hyperlink")
    url = hyperlink.get("url") if isinstance(hyperlink, dict) else None
    text = _apply_text_case(node.get("characters", ""), style.get("textCase", "ORIGINAL"))
    return TextRun(
        text=_strip_emoji(text),
        x=box["x"] - origin_x,
        y=box["y"] - origin_y,
        width=box["width"],
        height=box["height"],
        font_family=style.get("fontFamily", "Helvetica"),
        font_weight=int(style.get("fontWeight", 400)),
        italic=bool(style.get("italic", False)),
        font_size=float(style.get("fontSize", 12)),
        letter_spacing=float(style.get("letterSpacing", 0.0)),
        line_height=float(style.get("lineHeightPx", style.get("fontSize", 12) * 1.2)),
        color=color,
        align_horizontal=style.get("textAlignHorizontal", "LEFT"),
        node_name=node.get("name", ""),
        node_id=node.get("id", ""),
        url=url,
        underline=style.get("textDecoration") == "UNDERLINE",
        auto_width=style.get("textAutoResize") == "WIDTH_AND_HEIGHT",
        line_types=list(node.get("lineTypes") or []),
        line_indentations=list(node.get("lineIndentations") or []),
    )


def _extract_rect(
    node: dict, origin_x: float, origin_y: float, warnings: list[str], is_container: bool = False
) -> Rect | None:
    """A filled rectangle, rounded rectangle or ellipse. Containers (frames,
    groups) usually have no fill, so a missing fill is only reported for
    shapes."""
    color = _first_solid_fill(node)
    fills = _visible_fills(node)
    stroked = _has_visible_stroke(node)
    if color is None:
        if not is_container and (node.get("type") == "LINE" or stroked):
            warnings.append(
                f"{_describe(node)} is a line or outline with no solid fill, so it is NOT drawn. "
                "Fix: use a filled rectangle instead (e.g. a 1px-tall rectangle for a divider)."
            )
        elif fills:
            kinds = "/".join(sorted({f.get("type", "?") for f in fills}))
            warnings.append(f"{_describe(node)} has a {kinds} fill, which is not supported, so it is NOT drawn. Fix: use a solid colour.")
        return None
    if stroked:
        warnings.append(f"{_describe(node)} has a stroke, which is ignored (only the fill is drawn).")
    radius = float(node.get("cornerRadius") or 0.0)
    radii = node.get("rectangleCornerRadii")
    if radii and len(set(radii)) > 1:
        warnings.append(f"{_describe(node)} has different radii per corner, which is not supported; drawn square.")
        radius = 0.0
    box = node["absoluteBoundingBox"]
    return Rect(
        x=box["x"] - origin_x,
        y=box["y"] - origin_y,
        width=box["width"],
        height=box["height"],
        color=color,
        node_name=node.get("name", ""),
        node_id=node.get("id", ""),
        corner_radius=radius,
        shape="ellipse" if node.get("type") == "ELLIPSE" else "rect",
    )


def _extract_vector(node: dict, origin_x: float, origin_y: float) -> VectorIcon | None:
    geometry = node.get("fillGeometry")
    if not geometry:
        return None
    color = _first_solid_fill(node) or RGBColor(0, 0, 0, 1)
    box = node["absoluteBoundingBox"]
    paths = [(g["path"], g.get("windingRule", "NONZERO")) for g in geometry if g.get("path")]
    if not paths:
        return None
    return VectorIcon(
        x=box["x"] - origin_x,
        y=box["y"] - origin_y,
        width=box["width"],
        height=box["height"],
        paths=paths,
        color=color,
        node_name=node.get("name", ""),
        node_id=node.get("id", ""),
    )


_RECT_LIKE_TYPES = {"RECTANGLE", "LINE", "ELLIPSE"}
_VECTOR_TYPES = {"VECTOR", "BOOLEAN_OPERATION", "STAR", "REGULAR_POLYGON"}
_CONTAINER_TYPES = {"FRAME", "GROUP", "COMPONENT", "INSTANCE", "SECTION", "CANVAS"}


def extract(root: dict, warnings: list[str] | None = None) -> PageDoc:
    """Flatten the tree rooted at `root` into a PageDoc.

    `root` must have an absoluteBoundingBox (true for frames/canvas nodes
    returned by the REST API's /nodes endpoint) -- its top-left becomes the
    PDF page origin.
    """
    if warnings is None:
        warnings = []

    box = root.get("absoluteBoundingBox")
    if box is None:
        raise ExtractError(
            f"Root node {root.get('id')} has no absoluteBoundingBox -- pick "
            "a FRAME/COMPONENT node, not a bare group or the document root."
        )
    origin_x, origin_y = box["x"], box["y"]

    text_runs: list[TextRun] = []
    rects: list[Rect] = []
    vectors: list[VectorIcon] = []

    def walk(node: dict) -> None:
        if not _node_visible(node):
            return
        if node.get("isMask"):
            # Clip/mask geometry -- not visible content, don't draw it.
            return
        node_type = node.get("type")

        if _has_effects(node):
            warnings.append(
                f"{node.get('type')} {node.get('id')} ({node.get('name')!r}) "
                "has a visible effect (shadow/blur/blend) -- Figma would "
                "rasterize this on native export; verify it renders as "
                "intended here since we draw it as vector/text instead."
            )

        if abs(node.get("rotation") or 0) > 1e-6 and (
            node_type == "TEXT" or node_type in _RECT_LIKE_TYPES or node_type in _VECTOR_TYPES
        ):
            warnings.append(
                f"{_describe(node)} is rotated, but rotation is not supported: it is drawn upright "
                "at the position of its rotated bounding box. Fix: keep text and shapes unrotated."
            )

        if node_type == "TEXT":
            if not node.get("characters"):
                return
            text_runs.append(_extract_text(node, origin_x, origin_y, warnings))
            return

        if node_type in _RECT_LIKE_TYPES:
            r = _extract_rect(node, origin_x, origin_y, warnings)
            if r is not None:
                rects.append(r)
            # rectangles can still have children in weird files; fall through

        if node_type in _VECTOR_TYPES:
            v = _extract_vector(node, origin_x, origin_y)
            if v is not None:
                vectors.append(v)
                return
            # No fillGeometry on this node (e.g. an empty boolean-op group)
            # -- fall through and recurse into children instead.

        if node_type in _CONTAINER_TYPES:
            # Frame/group backgrounds (cards, sidebars, page colour) are drawn as rectangles.
            r = _extract_rect(node, origin_x, origin_y, warnings, is_container=True)
            if r is not None:
                rects.append(r)

        for child in node.get("children") or []:
            walk(child)

    walk(root)

    if vectors:
        names = ", ".join(repr(v.node_name) for v in vectors[:5])
        more = f" and {len(vectors) - 5} more" if len(vectors) > 5 else ""
        warnings.append(
            f"{len(vectors)} vector layer(s) drawn as shapes, not text: {names}{more}. If any of these "
            "is outlined text, it will NOT be extractable. Fix: keep text as live text layers."
        )

    return PageDoc(width=box["width"], height=box["height"], text_runs=text_runs, rects=rects, vectors=vectors)
