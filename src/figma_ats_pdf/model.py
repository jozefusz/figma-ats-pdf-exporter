"""Flat drawable primitives extracted from a Figma node tree.

Kept deliberately minimal: only what the PDF builder needs to place real
text and simple decorative rectangles at exact coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RGBColor:
    r: float  # 0-1, as Figma returns them
    g: float
    b: float
    a: float = 1.0

    def as_255(self) -> tuple[float, float, float]:
        return (self.r * 255, self.g * 255, self.b * 255)


@dataclass
class TextRun:
    text: str
    x: float
    y: float  # top-left, Figma px space
    width: float
    height: float
    font_family: str
    font_weight: int
    italic: bool
    font_size: float
    letter_spacing: float  # px
    line_height: float  # px
    color: RGBColor
    align_horizontal: str  # LEFT | CENTER | RIGHT | JUSTIFIED
    node_name: str
    node_id: str
    url: str | None = None
    underline: bool = False
    # True when Figma's textAutoResize is WIDTH_AND_HEIGHT (the box hugs
    # its content on both axes). `width` is then just wherever the text
    # happened to land, not a wrap constraint -- Figma never wraps these,
    # so word-wrapping against it here would force wraps Figma never had
    # (e.g. from small, expected differences between our re-instanced
    # variable-font glyph widths and Figma's own).
    auto_width: bool = False
    # Per-`\n`-separated-line Figma list metadata (lineTypes / lineIndentations),
    # aligned index-for-index with text.split("\n"). Empty when the node has
    # no lists at all.
    line_types: list[str] = field(default_factory=list)
    line_indentations: list[int] = field(default_factory=list)


@dataclass
class Rect:
    x: float
    y: float
    width: float
    height: float
    color: RGBColor
    node_name: str
    node_id: str
    corner_radius: float = 0.0  # uniform radius only, in px
    shape: str = "rect"  # "rect" | "ellipse"


@dataclass
class VectorIcon:
    x: float
    y: float
    width: float
    height: float
    paths: list[tuple[str, str]]  # (svg path `d`, windingRule)
    color: RGBColor
    node_name: str
    node_id: str


@dataclass
class PageDoc:
    width: float
    height: float
    text_runs: list[TextRun]
    rects: list[Rect]
    vectors: list[VectorIcon] = field(default_factory=list)
