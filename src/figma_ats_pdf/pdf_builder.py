"""Render a PageDoc to a real, text-extractable PDF.

Two things matter for ATS-readability, both handled here rather than left
to Figma's own PDF engine:

1. Real embedded TTF fonts (via fonts.FontRegistry) instead of vector
   outlines -- text stays text.
2. Tracked/letter-spaced runs use the PDF `Tc` (character spacing)
   operator (reportlab `canvas.setCharSpace`) so the whole run is a single
   `Tj` text-showing operation with a real space character at the real
   word boundary, instead of Figma's per-glyph positioning array that
   breaks gap-based word-boundary heuristics in extractors.

Coordinate conversion: Figma frames used for print layouts (like a CV)
are authored at 1 unit = 1 pt, matching PDF page units directly -- so no
px->pt rescaling is applied. If a source file turns out to be authored at
96dpi CSS-px semantics instead, adjust PX_TO_PT below.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from reportlab.pdfgen import canvas
from reportlab.pdfgen.canvas import Canvas

from .fonts import FontRegistry
from .model import PageDoc, TextRun, VectorIcon
from .svgpath import apply_path_to_reportlab

PX_TO_PT = 1.0

# Rough ascent ratio (baseline offset from the top of a line box) for the
# bundled fonts. Not exact font metrics -- good enough
# for visual placement; text-extractability doesn't depend on this at all.
_ASCENT_RATIO = 0.8

# Points of hanging indent per Figma list-indentation level. Figma reports
# indentation as an integer level, not px/pt, so this is a visual estimate
# rather than a value read from the source.
_INDENT_PT_PER_LEVEL = 16.0


def _wrap_line(
    text: str, font_name: str, font_size: float, letter_spacing: float, max_width: float, word_space: float = 0.0
) -> list[str]:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    def width(s: str) -> float:
        base = stringWidth(s, font_name, font_size)
        return base + letter_spacing * max(len(s) - 1, 0) + word_space * s.count(" ")

    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and width(candidate) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _line_width(text: str, font_name: str, font_size: float, letter_spacing: float, word_space: float = 0.0) -> float:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    return stringWidth(text, font_name, font_size) + letter_spacing * max(len(text) - 1, 0) + word_space * text.count(" ")


def _list_marker(line_type: str, ordinal: int) -> str:
    if line_type == "UNORDERED":
        return "•"  # bullet
    if line_type == "ORDERED":
        return f"{ordinal}."
    return ""


@dataclass
class _VisualLine:
    text: str
    text_indent: float  # from run.x, in pt
    marker: str | None = None  # drawn to the left of text_indent, if set
    marker_indent: float = 0.0  # from run.x, in pt -- where the marker itself starts


def _layout_lines(
    run: TextRun, font_name: str, font_size: float, letter_spacing: float, max_width: float, word_space: float = 0.0
) -> list[_VisualLine]:
    """Split run.text into visual lines, applying Figma's per-logical-line
    lineTypes (bullet/ordered markers) and lineIndentations (hanging
    indent) where present. Falls back to plain word-wrap when the node has
    no list metadata.
    """
    logical_lines = run.text.split("\n")
    visual: list[_VisualLine] = []
    ordinal_by_level: dict[int, int] = {}

    for i, logical in enumerate(logical_lines):
        line_type = run.line_types[i] if i < len(run.line_types) else "NONE"
        level = run.line_indentations[i] if i < len(run.line_indentations) else 0
        indent = level * _INDENT_PT_PER_LEVEL

        marker = ""
        if line_type == "ORDERED":
            ordinal_by_level[level] = ordinal_by_level.get(level, 0) + 1
            marker = _list_marker(line_type, ordinal_by_level[level])
        elif line_type == "UNORDERED":
            marker = _list_marker(line_type, 0)

        # Bake a real trailing space into the marker's own text (rather
        # than relying on visual gap alone) so naive stream-order text
        # extractors -- which don't reorder by X position, only append in
        # draw order -- still get a real word boundary between marker and
        # text, as long as the marker is drawn before the text (see below).
        marker_text = f"{marker}  " if marker else ""
        marker_width = _line_width(marker_text, font_name, font_size, letter_spacing) if marker else 0.0
        text_indent = indent + marker_width

        # Figma soft line breaks (Shift+Enter) use U+2028 LINE SEPARATOR
        # (and occasionally U+2029 PARAGRAPH SEPARATOR), not \n.
        sub_lines: list[str] = []
        for soft_line in re.split("[\n\u2028\u2029]", logical):
            sub_lines.extend(_wrap_line(soft_line, font_name, font_size, letter_spacing, max_width - text_indent, word_space))

        for j, sub in enumerate(sub_lines):
            visual.append(
                _VisualLine(
                    text=sub,
                    text_indent=text_indent,
                    marker=marker_text if (marker and j == 0) else None,
                    marker_indent=indent,
                )
            )

    return visual


def _draw_text_run(
    c: Canvas,
    run: TextRun,
    page_height: float,
    fonts: FontRegistry,
    tighten_word_gaps: bool = False,
    tracked_lines: list[str] | None = None,
) -> None:
    font_name = fonts.resolve(run.font_family, run.font_weight, run.italic)
    font_size = run.font_size * PX_TO_PT
    letter_spacing = run.letter_spacing * PX_TO_PT
    # PDF `Tw`: cancels one side of the tracking on word gaps (see tracking.py).
    word_space = -letter_spacing if (tighten_word_gaps and letter_spacing > 0) else 0.0
    max_width = run.width * PX_TO_PT
    # Auto-width (hug-content) nodes never wrap in Figma -- `run.width` is
    # just the box Figma happened to size around the text, not a
    # constraint, so don't treat it as a wrap constraint here either (see
    # TextRun.auto_width). Alignment math still uses the finite max_width
    # below since these nodes have no leftover space to center/right-align
    # into anyway.
    wrap_width = float("inf") if run.auto_width else max_width
    line_height = (run.line_height or run.font_size * 1.2) * PX_TO_PT
    top = run.y * PX_TO_PT
    x0 = run.x * PX_TO_PT

    visual_lines = _layout_lines(run, font_name, font_size, letter_spacing, wrap_width, word_space)

    for i, vline in enumerate(visual_lines):
        line_top = top + i * line_height
        baseline_y = page_height - line_top - line_height * _ASCENT_RATIO
        base_x = x0 + vline.text_indent
        available_width = max_width - vline.text_indent

        if run.align_horizontal == "CENTER":
            line_x = base_x + available_width / 2 - (_line_width(vline.text, font_name, font_size, letter_spacing, word_space) / 2)
        elif run.align_horizontal == "RIGHT":
            line_x = base_x + available_width - _line_width(vline.text, font_name, font_size, letter_spacing, word_space)
        else:
            line_x = base_x

        # Marker drawn *before* the line's own text, in content-stream
        # order -- naive stream-order extractors (pypdf) concatenate text
        # in draw order, not by X position, so if the marker were drawn
        # after the text it would get appended to the end of that visual
        # line instead of read as a prefix on the next.
        if vline.marker:
            mt = c.beginText(x0 + vline.marker_indent, baseline_y)
            mt.setFont(font_name, font_size)
            mt.setCharSpace(letter_spacing)
            mt.setWordSpace(word_space)
            mt.setFillColorRGB(run.color.r, run.color.g, run.color.b, alpha=run.color.a)
            mt.textOut(vline.marker)
            c.drawText(mt)

        # Use a text object (not drawString) so we can set real Tc
        # character spacing -- this keeps a tracked run as one Tj
        # text-showing operation with a real word-boundary space, instead
        # of Figma's per-glyph offset array.
        t = c.beginText(line_x, baseline_y)
        t.setFont(font_name, font_size)
        t.setCharSpace(letter_spacing)
        t.setWordSpace(word_space)
        t.setFillColorRGB(run.color.r, run.color.g, run.color.b, alpha=run.color.a)
        # Trailing space = run separator. Without it, pypdf fuses the last word of a run
        # with the first word of the next when the next run starts further left on the
        # same baseline ("FIRST" + "LEFT" -> "FIRSTLEFT"). Invisible, and geometry below
        # is measured from vline.text without it.
        t.textOut(vline.text + " ")
        c.drawText(t)

        line_w = _line_width(vline.text, font_name, font_size, letter_spacing, word_space)
        if tracked_lines is not None and letter_spacing > 0 and vline.text.strip():
            tracked_lines.append(vline.text)
        if run.underline:
            underline_y = baseline_y - font_size * 0.08
            c.setLineWidth(max(font_size * 0.05, 0.5))
            c.setStrokeColorRGB(run.color.r, run.color.g, run.color.b, alpha=run.color.a)
            c.line(line_x, underline_y, line_x + line_w, underline_y)
        if run.url:
            # Real PDF link annotation, not just visual styling -- lets a
            # human (or ATS that follows links) reach the URL directly.
            c.linkURL(
                run.url,
                (line_x, baseline_y - font_size * 0.25, line_x + line_w, baseline_y + font_size),
                relative=0,
            )


def _draw_vector(c: Canvas, vec: VectorIcon, page_height: float, warnings: list[str] | None = None) -> None:
    origin_x = vec.x * PX_TO_PT
    origin_y = vec.y * PX_TO_PT

    def to_pdf(x: float, y: float) -> tuple[float, float]:
        return (origin_x + x * PX_TO_PT, page_height - origin_y - y * PX_TO_PT)

    c.setFillColorRGB(vec.color.r, vec.color.g, vec.color.b, alpha=vec.color.a)
    for d, winding_rule in vec.paths:
        path_obj = c.beginPath()
        ok = apply_path_to_reportlab(path_obj, d, to_pdf)
        if not ok and warnings is not None:
            warnings.append(
                f"VECTOR {vec.node_id} ({vec.node_name!r}) has path data this "
                "renderer couldn't fully parse -- icon may be incomplete."
            )
        c.drawPath(path_obj, fill=1, stroke=0, fillMode=(0 if winding_rule == "EVENODD" else 1))


def build_pdf(
    doc: PageDoc,
    output_path: str,
    fonts: FontRegistry | None = None,
    warnings: list[str] | None = None,
    tighten_word_gaps: bool = False,
) -> list[str]:
    """Render `doc` to `output_path`. Returns the visual lines of letter-spaced
    text, so verify() can check each one came back whole on a single line.
    """
    fonts = fonts or FontRegistry()
    page_width = doc.width * PX_TO_PT
    page_height = doc.height * PX_TO_PT

    tracked_lines: list[str] = []
    c = canvas.Canvas(output_path, pagesize=(page_width, page_height))

    for rect in doc.rects:
        c.setFillColorRGB(rect.color.r, rect.color.g, rect.color.b, alpha=rect.color.a)
        y = page_height - (rect.y + rect.height) * PX_TO_PT
        x, w, h = rect.x * PX_TO_PT, rect.width * PX_TO_PT, rect.height * PX_TO_PT
        if rect.shape == "ellipse":
            c.ellipse(x, y, x + w, y + h, fill=1, stroke=0)
        elif rect.corner_radius > 0:
            c.roundRect(x, y, w, h, min(rect.corner_radius * PX_TO_PT, w / 2, h / 2), fill=1, stroke=0)
        else:
            c.rect(x, y, w, h, fill=1, stroke=0)

    for vec in doc.vectors:
        _draw_vector(c, vec, page_height, warnings)

    for run in doc.text_runs:
        _draw_text_run(c, run, page_height, fonts, tighten_word_gaps, tracked_lines)

    c.showPage()
    c.save()
    return tracked_lines
