"""Verify a generated PDF is actually ATS-readable text, not just
visually correct.

The bar this project sets: two independent extractors
(pypdf and poppler's pdftotext) must agree with the source Figma text, not
just "look right" in Preview -- Preview's copy/paste uses a much more
forgiving glyph-metric-aware reconstruction than real ATS backends.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field

from pypdf import PdfReader

from .model import PageDoc

_WORD_RE = re.compile(r"\S+")


@dataclass
class VerifyResult:
    engine: str
    missing_words: list[str] = field(default_factory=list)
    extra_words: list[str] = field(default_factory=list)
    fragmented_word_count: int = 0
    # Tracked source lines this extractor did not return on a single line.
    split_lines: list[str] = field(default_factory=list)
    raw_text: str = ""

    @property
    def ok(self) -> bool:
        return not self.missing_words and self.fragmented_word_count == 0 and not self.split_lines


def _source_words(doc: PageDoc) -> list[str]:
    words: list[str] = []
    for run in doc.text_runs:
        words.extend(_WORD_RE.findall(run.text))
    return words


def _extracted_words(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def _single_letter_runs(tokens: list[str]) -> int:
    """Count runs of 3+ consecutive single-letter tokens ("D E S I G N").
    Digits and punctuation are ignored so legitimate text such as
    "TEAM OF 4 · 3 WEEKS" is not mistaken for a shattered word.
    """
    runs = 0
    i = 0
    n = len(tokens)
    while i < n:
        if len(tokens[i]) == 1 and tokens[i].isalpha():
            j = i
            while j < n and len(tokens[j]) == 1 and tokens[j].isalpha():
                j += 1
            if j - i >= 3:
                runs += 1
            i = j
        else:
            i += 1
    return runs


def _count_fragmented(source_words: list[str], extracted_words: list[str]) -> int:
    """Heuristic for the known failure mode: a tracked word like "DESIGN"
    coming back as single-letter tokens "D" "E" "S" "I" "G" "N". Counts runs
    of single-letter tokens in the extraction beyond any that already exist
    in the source text.
    """
    return max(0, _single_letter_runs(extracted_words) - _single_letter_runs(source_words))


def _extract_pypdf(pdf_path: str) -> str:
    reader = PdfReader(pdf_path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_pdftotext(pdf_path: str) -> str | None:
    if shutil.which("pdftotext") is None:
        return None
    result = subprocess.run(
        ["pdftotext", pdf_path, "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def verify(pdf_path: str, doc: PageDoc, tracked_lines: list[str] | None = None) -> list[VerifyResult]:
    source_words = _source_words(doc)
    source_multiset = _multiset(source_words)

    results: list[VerifyResult] = []

    pypdf_text = _extract_pypdf(pdf_path)
    results.append(_build_result("pypdf", pypdf_text, source_words, source_multiset, tracked_lines))

    pdftotext_text = _extract_pdftotext(pdf_path)
    if pdftotext_text is not None:
        results.append(_build_result("pdftotext", pdftotext_text, source_words, source_multiset, tracked_lines))
    else:
        results.append(VerifyResult(engine="pdftotext", raw_text="[poppler pdftotext not found on PATH]"))

    return results


def _multiset(words: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for w in words:
        counts[w] = counts.get(w, 0) + 1
    return counts


def _find_split_lines(text: str, tracked_lines: list[str] | None) -> list[str]:
    """Tracked lines whose words did not come back together on one line."""
    if not tracked_lines:
        return []
    extracted = [" ".join(line.split()) for line in text.splitlines()]
    return [t for t in tracked_lines if not any(" ".join(t.split()) in line for line in extracted)]


def _build_result(
    engine: str,
    text: str,
    source_words: list[str],
    source_multiset: dict[str, int],
    tracked_lines: list[str] | None = None,
) -> VerifyResult:
    extracted_words = _extracted_words(text)
    extracted_multiset = _multiset(extracted_words)

    missing = []
    for word, count in source_multiset.items():
        have = extracted_multiset.get(word, 0)
        if have < count:
            missing.extend([word] * (count - have))

    extra = []
    for word, count in extracted_multiset.items():
        expected = source_multiset.get(word, 0)
        if count > expected:
            extra.extend([word] * (count - expected))

    fragmented = _count_fragmented(source_words, extracted_words)

    return VerifyResult(
        engine=engine,
        missing_words=missing,
        extra_words=extra,
        fragmented_word_count=fragmented,
        split_lines=_find_split_lines(text, tracked_lines),
        raw_text=text,
    )
