import pytest

from figma_ats_pdf.extract import ExtractError, extract

BOX = {"x": 0, "y": 0, "width": 100, "height": 20}
BLACK = [{"type": "SOLID", "color": {"r": 0, "g": 0, "b": 0}}]


def frame(*children, **extra):
    return {"id": "1:1", "name": "root", "type": "FRAME", "absoluteBoundingBox": {**BOX, "height": 100},
            "children": list(children), **extra}


def text(chars="Hello world", **extra):
    return {"id": "2:1", "name": "t", "type": "TEXT", "characters": chars, "absoluteBoundingBox": BOX,
            "style": {"fontFamily": "X", "fontSize": 10}, "fills": BLACK, **extra}


def shape(kind="RECTANGLE", **extra):
    return {"id": "3:1", "name": "s", "type": kind, "absoluteBoundingBox": BOX, **extra}


def run(node):
    warnings = []
    return extract(frame(node), warnings), warnings


def test_partial_inline_style_is_refused():
    node = text(characterStyleOverrides=[0] * 6 + [1] * 5, styleOverrideTable={"1": {"fontWeight": 800}})
    with pytest.raises(ExtractError, match="only part of its text"):
        run(node)


def test_two_inline_styles_are_refused():
    node = text(characterStyleOverrides=[1] * 5 + [2] * 6, styleOverrideTable={"1": {"fontWeight": 800}, "2": {"italic": True}})
    with pytest.raises(ExtractError, match="mixes two or more"):
        run(node)


def test_whole_layer_hyperlink_is_kept():
    link = {"type": "URL", "url": "https://example.com"}
    node = text(characterStyleOverrides=[1] * 11, styleOverrideTable={"1": {"hyperlink": link}})
    doc, warnings = run(node)
    assert doc.text_runs[0].url == "https://example.com" and warnings == []


def test_partial_hyperlink_warns_instead_of_failing():
    link = {"type": "URL", "url": "https://example.com"}
    node = text(characterStyleOverrides=[0] * 6 + [1] * 5, styleOverrideTable={"1": {"hyperlink": link}})
    _, warnings = run(node)
    assert any("links only part" in w for w in warnings)


def test_gradient_text_fill_warns():
    grad = [{"type": "GRADIENT_LINEAR", "gradientStops": []}]
    _, warnings = run(text(fills=grad))
    assert any("GRADIENT_LINEAR" in w for w in warnings)


def test_rotation_warns():
    _, warnings = run(text(rotation=1.57))
    assert any("rotated" in w for w in warnings)


def test_stroke_only_shapes_and_lines_are_reported_not_drawn():
    stroke = {"fills": [], "strokes": BLACK, "strokeWeight": 2}
    for node in (shape(**stroke), shape("LINE", **stroke)):
        doc, warnings = run(node)
        # only the root frame's own rect (none: it has no fill) -> nothing drawn
        assert doc.rects == [] and any("NOT drawn" in w for w in warnings)


def test_rounded_rectangles_ellipses_and_frame_backgrounds_are_drawn():
    solid = {"fills": BLACK}
    doc, warnings = run(shape(cornerRadius=8, **solid))
    assert doc.rects[0].corner_radius == 8 and warnings == []
    doc, _ = run(shape("ELLIPSE", **solid))
    assert doc.rects[0].shape == "ellipse"
    doc, _ = run(shape("FRAME", children=[], **solid))
    assert len(doc.rects) == 1


def test_outlined_text_vectors_are_reported():
    vec = shape("VECTOR", fills=BLACK, fillGeometry=[{"path": "M 0 0 L 1 1 Z", "windingRule": "NONZERO"}])
    doc, warnings = run(vec)
    assert len(doc.vectors) == 1 and any("vector layer" in w for w in warnings)
