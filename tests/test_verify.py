from figma_ats_pdf.verify import _count_fragmented, _find_split_lines


def test_digits_and_punctuation_are_not_letter_splitting():
    words = "TEAM OF 4 · 3 WEEKS".split()
    assert _count_fragmented(words, words) == 0


def test_shattered_tracked_word_is_detected():
    assert _count_fragmented(["DESIGN", "LEADERSHIP"], "D E S I G N L E A D E R S H I P".split()) == 1


def test_runs_already_in_the_source_are_not_counted():
    assert _count_fragmented(["A", "B", "C"], ["A", "B", "C"]) == 0


def test_split_lines_detects_words_returned_on_separate_lines():
    assert _find_split_lines("BOOTCAMP\n\nPROJECTS\n", ["BOOTCAMP PROJECTS"]) == ["BOOTCAMP PROJECTS"]
    assert _find_split_lines("x BOOTCAMP   PROJECTS y\n", ["BOOTCAMP PROJECTS"]) == []
    assert _find_split_lines("W I D E\n", ["WIDE"]) == ["WIDE"]
    assert _find_split_lines("anything", None) == []
