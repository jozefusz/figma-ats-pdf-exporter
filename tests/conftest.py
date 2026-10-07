import pytest

from figma_ats_pdf.model import RGBColor, TextRun


class FakeFonts:
    """Stands in for FontRegistry so tests never touch the network. Helvetica's
    space is 0.278 em wide."""

    def resolve(self, family, weight, italic):
        return "Helvetica"


@pytest.fixture
def fonts():
    return FakeFonts()


def make_run(text, size=10.0, letter_spacing=0.0, name="layer"):
    return TextRun(
        text=text, x=0, y=0, width=300, height=size * 1.4, font_family="X", font_weight=400, italic=False,
        font_size=size, letter_spacing=letter_spacing, line_height=size * 1.4, color=RGBColor(0, 0, 0),
        align_horizontal="LEFT", node_name=name, node_id="1:1",
    )
