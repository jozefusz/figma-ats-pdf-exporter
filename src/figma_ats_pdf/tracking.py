"""Detect letter-spacing that makes some text extractors split tracked text.

Measured against poppler's `pdftotext` (default mode), not assumed:

* Letters split. Once the letter-spacing reaches 0.40 em, a single word comes
  back as separate letters ("W I D E"). 0.39 em is intact at every size.
* Words split. Once the visible gap between two words reaches about 1.0 em,
  each word comes back on its own line. The gap is the font's space width
  plus the letter-spacing on both sides of the space (the PDF `Tc` operator
  adds it after every glyph, spaces included).
* Exactly 10%. A letter-spacing of exactly 10.0% of the font size, a very
  common Figma value, splits letters at many (not all) font sizes. 9.95% and
  10.05% are fine, so this is a boundary quirk, not a trend. Because it
  depends on the size, it is only reported after verification sees a split
  (see format_ten_percent_hint), never as a pre-flight warning.

`pypdf` is unaffected by all three and so are most real ATS parsers, but the
failure is silent, so we warn. `--tighten-word-gaps` cancels one side of the
extra spacing on word gaps (PDF `Tw`), which roughly doubles the tracking a
run can carry before its *words* split. It does not help the other two.
"""

from __future__ import annotations

from dataclasses import dataclass

from reportlab.pdfbase.pdfmetrics import stringWidth

from .fonts import FontRegistry
from .model import PageDoc, TextRun

LETTER_SPLIT_EM = 0.40
LETTER_WARN_EM = 0.38  # a margin below the observed split
WORD_SPLIT_GAP_EM = 1.0
WORD_WARN_GAP_EM = 0.95
TEN_PERCENT_EM = 0.10
TEN_PERCENT_TOLERANCE = 0.001  # Figma stores float32, so allow rounding noise

LETTERS, WORDS, TEN_PERCENT = "letters", "words", "ten-percent"
_MAX_LISTED = 5


@dataclass
class TrackingRisk:
    run: TextRun
    kind: str
    max_safe_px: float  # largest letter-spacing that avoids this risk (0 if n/a)


def _word_gap_em(space_em: float, letter_spacing_em: float, tightened: bool) -> float:
    return space_em + letter_spacing_em * (1 if tightened else 2)


def find_risky_runs(doc: PageDoc, fonts: FontRegistry, tightened: bool) -> list[TrackingRisk]:
    risks: list[TrackingRisk] = []
    for run in doc.text_runs:
        if run.letter_spacing <= 0 or not run.text.strip() or run.font_size <= 0:
            continue
        ls_em = run.letter_spacing / run.font_size

        if ls_em >= LETTER_WARN_EM:
            risks.append(TrackingRisk(run, LETTERS, (LETTER_WARN_EM - 0.03) * run.font_size))
            continue

        if " " in run.text.strip():
            font_name = fonts.resolve(run.font_family, run.font_weight, run.italic)
            space_em = stringWidth(" ", font_name, 1.0)
            if _word_gap_em(space_em, ls_em, tightened) >= WORD_WARN_GAP_EM:
                per_side = 1 if tightened else 2
                safe = max((WORD_WARN_GAP_EM - space_em) / per_side * run.font_size, 0.0)
                risks.append(TrackingRisk(run, WORDS, safe))
                continue

        if abs(ls_em - TEN_PERCENT_EM) <= TEN_PERCENT_TOLERANCE:
            risks.append(TrackingRisk(run, TEN_PERCENT, run.font_size * 0.09))
    return risks


def _describe(risk: TrackingRisk) -> str:
    r = risk.run
    pct = r.letter_spacing / r.font_size * 100
    return (
        f"  - {r.text.strip()[:40]!r} (layer {r.node_name!r}, {r.font_family} {r.font_size:g}px, "
        f"letter-spacing {r.letter_spacing:g}px = {pct:.1f}%)"
    )


def format_warnings(risks: list[TrackingRisk], tightened: bool) -> list[str]:
    risks = [x for x in risks if x.kind != TEN_PERCENT]
    if not risks:
        return []
    out: list[str] = []

    def group(kind: str, heading: str) -> list[TrackingRisk]:
        items = [x for x in risks if x.kind == kind]
        if items:
            out.append(f"warning: {len(items)} tracked text run(s) {heading}")
            for x in items[:_MAX_LISTED]:
                out.append(_describe(x) + f": use <= {x.max_safe_px:.1f}px ({x.max_safe_px / x.run.font_size * 100:.0f}%)"
                           if x.kind != TEN_PERCENT else _describe(x) + f": use 9% ({x.max_safe_px:.2f}px) or 11% instead")
            if len(items) > _MAX_LISTED:
                out.append(f"  ... and {len(items) - _MAX_LISTED} more")
        return items

    letters = group(LETTERS, "have letter-spacing of 40% of the font size or more. poppler's pdftotext will "
                             "read these as separate letters ('W I D E'), and some ATS parsers may too.")
    words = group(WORDS, "have very wide gaps between words. poppler's pdftotext will put each word on its "
                         "own line; pypdf and most ATS parsers are unaffected.")
    out.append("  Fixes, best first:")
    out.append("    1. Change the letter-spacing on those layers in Figma to the values above.")
    if words and not tightened:
        out.append(
            "    2. For word-gap warnings only: re-run with --tighten-word-gaps, which narrows the gap "
            "between tracked words (about doubles the tracking you can use) at a small cost in fidelity."
        )
    elif words and tightened:
        out.append("    (--tighten-word-gaps is already on and is not enough for the word-gap runs above.)")
    if letters:
        out.append("    (--tighten-word-gaps does not help letter-level splits.)")
    out.append("    Or accept it if your target parser is known to be layout-insensitive; verification "
               "below shows exactly what poppler and pypdf return.")
    return out


def format_ten_percent_hint(risks: list[TrackingRisk], split_lines: set[str]) -> list[str]:
    """After verification: if a line that split is exactly-10% tracked text,
    say so, since 9% or 11% is a one-click fix that looks the same."""
    split = {" ".join(t.split()) for t in split_lines}
    hits = [x for x in risks if x.kind == TEN_PERCENT and " ".join(x.run.text.split()) in split]
    if not hits:
        return []
    out = [
        f"hint: {len(hits)} of the split line(s) use exactly 10% letter-spacing, which triggers a "
        "poppler quirk at some font sizes. 9% or 11% looks the same and avoids it:"
    ]
    for x in hits[:_MAX_LISTED]:
        out.append(_describe(x) + f": use 9% ({x.max_safe_px:.2f}px) or 11% ({x.run.font_size * 0.11:.2f}px)")
    return out
