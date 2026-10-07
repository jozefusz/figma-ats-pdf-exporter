from conftest import make_run

from figma_ats_pdf.model import PageDoc
from figma_ats_pdf.tracking import (
    LETTERS,
    TEN_PERCENT,
    WORDS,
    find_risky_runs,
    format_ten_percent_hint,
    format_warnings,
)


def doc_of(*runs):
    return PageDoc(width=100, height=100, text_runs=list(runs), rects=[])


def kinds(doc, fonts, tightened=False):
    return [r.kind for r in find_risky_runs(doc, fonts, tightened)]


def test_untracked_and_modest_tracking_are_fine(fonts):
    doc = doc_of(make_run("HELLO WORLD", letter_spacing=0), make_run("HELLO WORLD", letter_spacing=1.5))
    assert kinds(doc, fonts) == []


def test_letter_level_split_applies_to_single_words_too(fonts):
    assert kinds(doc_of(make_run("SPREAD", letter_spacing=4.5)), fonts) == [LETTERS]


def test_wide_word_gaps_are_flagged_and_tightening_fixes_borderline_runs(fonts):
    # Helvetica space 0.278em + 2 * 0.35em = 0.978em >= 0.95; tightened: 0.628em.
    doc = doc_of(make_run("TWO WORDS", letter_spacing=3.5))
    assert kinds(doc, fonts) == [WORDS]
    assert kinds(doc, fonts, tightened=True) == []


def test_tightening_does_not_help_letter_level_splits(fonts):
    assert kinds(doc_of(make_run("TWO WORDS", letter_spacing=5.0)), fonts, tightened=True) == [LETTERS]


def test_exactly_ten_percent_is_only_a_post_render_hint(fonts):
    doc = doc_of(make_run("QUIRKY TEXT", size=12, letter_spacing=1.2))
    risks = find_risky_runs(doc, fonts, False)
    assert [r.kind for r in risks] == [TEN_PERCENT]
    assert format_warnings(risks, False) == []
    assert format_ten_percent_hint(risks, set()) == []
    assert format_ten_percent_hint(risks, {"QUIRKY TEXT"})


def test_nearby_percentages_are_not_the_quirk(fonts):
    for ls in (1.08, 1.32):  # 9% and 11% of 12px
        assert kinds(doc_of(make_run("QUIRKY TEXT", size=12, letter_spacing=ls)), fonts) == []
