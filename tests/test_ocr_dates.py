"""M12 Task 9: the OCR date grammar, design 8.1 rev 27 (P8, owner decision D1).

THE LITERAL TABLE BELOW IS SPELLED FROM P8 AND NOTHING ELSE. It never calls a production symbol
to build an expectation (CLAUDE.md, "an oracle built from the definition it checks"). The
second implementation of the intersection rule further down is kept to catch asymmetric drift,
and it proves only that two readings AGREE (M922/M924). The table is what stands behind P8's
sentences.
"""

import ast
import itertools
import random
from pathlib import Path

import pytest

from pam.naming.ocr_dates import DATE_GRAMMAR_VERSION, FoundDate, OcrLine, find_date

MODULE = Path(__file__).resolve().parent.parent / "pam" / "naming" / "ocr_dates.py"

# (lines, floor, expected date or None, expected matched text or None). A line is (text, score).
# Unless the plan's table states otherwise, every score is 0.9 and the floor 0.5.
S = 0.9
F = 0.5
P8_TABLE: list[tuple[list[tuple[str, float]], float, str | None, str | None]] = [
    # --- G1 ---------------------------------------------------------------------------------
    ([("1998-07-04", S)], F, "1998-07-04", "1998-07-04"),
    ([("1998.7.4", S)], F, "1998-07-04", "1998.7.4"),
    # --- G2, the camera imprint order --------------------------------------------------------
    ([("'98 7 4", S)], F, "1998-07-04", "'98 7 4"),
    ([("’98.07.04", S)], F, "1998-07-04", "’98.07.04"),
    # --- the %y pivot: 69-99 -> 19xx, 00-68 -> 20xx ------------------------------------------
    ([("'05 12 25", S)], F, "2005-12-25", "'05 12 25"),
    ([("'69 1 1", S)], F, "1969-01-01", "'69 1 1"),
    ([("'68 1 1", S)], F, "2068-01-01", "'68 1 1"),
    # --- bare two-digit years: no row reads them ---------------------------------------------
    ([("98 7 4", S)], F, None, None),
    ([("7 4 98", S)], F, None, None),
    # --- G3 ------------------------------------------------------------------------------------
    ([("7 4 '98", S)], F, None, None),
    ([("13 4 '98", S)], F, "1998-04-13", "13 4 '98"),
    ([("5 5 '98", S)], F, "1998-05-05", "5 5 '98"),
    # --- G4 and the rule ---------------------------------------------------------------------
    ([("04/07/1998", S)], F, None, None),
    ([("04/07/1998", S), ("July 4, 1998", S)], F, "1998-07-04", "July 4, 1998"),
    ([("03/02/1998", S), ("Jan 1, 2001", S)], F, None, None),
    # --- G5 ------------------------------------------------------------------------------------
    ([("July 4, 1998", S)], F, "1998-07-04", "July 4, 1998"),
    ([("4 Jul 1998", S)], F, "1998-07-04", "4 Jul 1998"),
    ([("Sept 3 1987", S)], F, "1987-09-03", "Sept 3 1987"),
    ([("3rd March 1987", S)], F, "1987-03-03", "3rd March 1987"),
    # --- D1: not day precision ---------------------------------------------------------------
    ([("June 1988", S)], F, None, None),
    ([("Christmas 1994", S)], F, None, None),
    ([("1988", S)], F, None, None),
    # --- not a calendar date -----------------------------------------------------------------
    ([("1998-02-30", S)], F, None, None),
    # --- an adjacent digit voids a match -----------------------------------------------------
    ([("21998-07-04", S)], F, None, None),
    ([("1998-07-045", S)], F, None, None),
    # --- a month name ends at a word boundary ------------------------------------------------
    ([("Mayor 4 1998", S)], F, None, None),
    # --- the same date twice -----------------------------------------------------------------
    ([("1998-07-04", S), ("1998-07-04", S)], F, "1998-07-04", "1998-07-04"),
    # --- nothing read --------------------------------------------------------------------------
    ([], F, None, None),
    ([("", S)], F, None, None),
    # --- the floor (I16) -----------------------------------------------------------------------
    ([("1998-07-04", 0.45)], 0.5, None, None),
    ([("1998-07-04", 0.45)], 0.4, "1998-07-04", "1998-07-04"),
    ([("1998-07-04", 0.9), ("Jan 1, 2001", 0.45)], 0.5, "1998-07-04", "1998-07-04"),
    ([("1998-07-04", 0.9), ("Jan 1, 2001", 0.45)], 0.4, None, None),
    # P8's "AT OR ABOVE" the floor: a line scoring exactly the floor is read.
    ([("1998-07-04", 0.5)], 0.5, "1998-07-04", "1998-07-04"),
]

# Collection-time floors. pytest SKIPS an empty parametrize rather than failing it (M743), so an
# emptied table would vanish from the run and leave it green. 34 = the plan's 33 rows plus the
# exactly-at-the-floor row.
assert P8_TABLE, "the P8 table is empty - its parametrized rows would silently vanish"
assert len(P8_TABLE) >= 34, f"the P8 table lost rows: {len(P8_TABLE)} < 34"


def _lines(pairs: list[tuple[str, float]]) -> list[OcrLine]:
    return [OcrLine(text=text, score=score) for text, score in pairs]


@pytest.mark.parametrize(("lines", "floor", "date", "text"), P8_TABLE)
def test_the_p8_table_row_by_row(lines, floor, date, text):
    found = find_date(_lines(lines), floor=floor)
    if date is None:
        assert found is None
    else:
        assert found is not None
        assert (found.date, found.text) == (date, text)


# More rows spelled from P8's sentences that the plan's table leaves implicit.
SPEC_ROWS: list[tuple[str, str | None]] = [
    ("1900-01-01", "1900-01-01"),  # G1: year 1900-2099, lower edge
    ("2099-12-31", "2099-12-31"),  # upper edge
    ("1899-12-31", None),
    ("2100-01-01", None),
    ("1998 7 4", "1998-07-04"),  # G1's separators include a space
    ("1998/07/04", "1998-07-04"),
    ("13-04-1998", "1998-04-13"),  # G4 with '-', one reading valid
    ("13.04.1998", "1998-04-13"),  # G4 with '.'
    ("13 04 1998", None),  # G4's separators do NOT include a space
    ("July 4th, 1998", "1998-07-04"),  # G5: optional st/nd/rd/th
    ("2nd Feb 2001", "2001-02-02"),
    ("December 25 2000", "2000-12-25"),  # G5: optional comma, full name
    ("4 July 1998", "1998-07-04"),
    ("１９９８-０７-０４", "1998-07-04"),  # NFKC: full-width
    ("'98-7-4", "1998-07-04"),  # G2, '-' separators
    ("7.4.'98", None),  # G3 ambiguous, '.' separators
    ("Monday", None),  # weekday names are not recognised
    ("12:30", None),  # times are not recognised
    ("'98 2 30", None),  # G2, not a calendar date
    # P8's adjacent-digit rule for EVERY G-row, not only G1 (Task 9 review, F1: M2102-M2105 -
    # each row's leading guard could be deleted with the suite green). Each line holds a match
    # that is real if the guard is gone: `23 4 '98`, `23/4/1998`, `23 July 1998`, `'98 7 4`.
    ("5'98 7 4", None),  # G2: a digit before the apostrophe voids it
    ("123 4 '98", None),  # G3
    ("123/4/1998", None),  # G4
    ("123 July 1998", None),  # G5, day first
    # ...and the month name's LEADING word boundary (M2106): `may 4, 1998` inside a word.
    ("Remay 4, 1998", None),
]
assert SPEC_ROWS, "SPEC_ROWS is empty - its parametrized rows would silently vanish"
assert len(SPEC_ROWS) >= 24, f"SPEC_ROWS lost rows: {len(SPEC_ROWS)} < 24"


@pytest.mark.parametrize(("line", "date"), SPEC_ROWS)
def test_rows_spelled_from_p8s_sentences(line, date):
    found = find_date([OcrLine(line, 0.9)], floor=0.5)
    assert (found.date if found else None) == date


def test_each_table_test_is_parametrized_over_its_WHOLE_table():
    """The collection-time asserts guard the LISTS; this guards the WIRING (Task 9 review, F2).

    M2116: `@pytest.mark.parametrize(..., [])` over the table test, with `P8_TABLE` intact,
    passed every assert above and left the suite green (3911 passed, one more skipped) - pytest
    SKIPS an empty parametrize, so all 34 rows vanished. M743's shape, one line from the list.
    """
    for test, table in (
        (test_the_p8_table_row_by_row, P8_TABLE),
        (test_rows_spelled_from_p8s_sentences, SPEC_ROWS),
    ):
        marks = [m for m in test.pytestmark if m.name == "parametrize"]
        assert len(marks) == 1, test.__name__
        assert list(marks[0].args[1]) == list(table), test.__name__


def test_the_matched_text_is_the_substring_after_nfkc():
    found = find_date([OcrLine("taken １９９８-07-04 at noon", 0.9)], floor=0.5)
    assert found == FoundDate(date="1998-07-04", text="1998-07-04", confidence=0.9)


def test_a_match_inside_a_longer_line_yields_only_its_substring():
    found = find_date([OcrLine("Birthday party July 4, 1998 - Mom", 0.8)], floor=0.5)
    assert found == FoundDate(date="1998-07-04", text="July 4, 1998", confidence=0.8)


# --- confidence, text and the tie-break (P8) -----------------------------------------------------


def test_confidence_is_the_HIGHEST_scoring_line_holding_the_exact_candidate():
    lines = [OcrLine("1998-07-04", 0.6), OcrLine("1998/7/4", 0.9)]
    assert find_date(lines, floor=0.5) == FoundDate("1998-07-04", "1998/7/4", 0.9)


def test_equal_scores_go_to_the_EARLIEST_line():
    lines = [OcrLine("1998-07-04", 0.8), OcrLine("1998/7/4", 0.8)]
    assert find_date(lines, floor=0.5) == FoundDate("1998-07-04", "1998-07-04", 0.8)


def test_a_line_holding_only_an_AMBIGUOUS_candidate_gives_no_confidence():
    # 04/07/1998 contains 1998-07-04 but is not exactly {D}: its higher score is not used.
    lines = [OcrLine("04/07/1998", 0.95), OcrLine("July 4, 1998", 0.7)]
    assert find_date(lines, floor=0.5) == FoundDate("1998-07-04", "July 4, 1998", 0.7)


def test_floor_is_keyword_only_and_has_no_default():
    with pytest.raises(TypeError):
        find_date([OcrLine("1998-07-04", 0.9)])  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        find_date([OcrLine("1998-07-04", 0.9)], 0.5)  # type: ignore[misc]


def test_the_grammar_version_is_one():
    assert DATE_GRAMMAR_VERSION == 1


def test_the_module_imports_only_the_stdlib_the_plan_names():
    allowed = {"re", "datetime", "unicodedata", "dataclasses", "collections.abc"}
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported, "no import found - the walk is reading the wrong node"
    assert imported <= allowed, imported - allowed


# --- interpretations: P8 is silent, and these record the reading built -------------------------


def test_INTERPRETATION_a_month_name_is_read_in_any_case():
    # P8 says "English month name"; it does not say the case. OCR of printed text is often
    # upper case. Recorded for the design model, not derived from the spec.
    found = find_date([OcrLine("JULY 4, 1998", 0.9)], floor=0.5)
    assert found is not None and found.date == "1998-07-04"


def test_INTERPRETATION_a_match_with_NO_valid_reading_is_not_a_candidate():
    # P8: "each candidate is a set of one or two calendar dates" - an empty set is not one,
    # so an impossible date beside a real one neither supplies nor contradicts.
    lines = [OcrLine("1998-02-30", 0.9), OcrLine("1998-07-04", 0.9)]
    assert find_date(lines, floor=0.5) == FoundDate("1998-07-04", "1998-07-04", 0.9)


# --- Step 3: a second, independent implementation of the rule and of confidence ------------------

# Dates chosen so each ambiguous PAIR is one G4 line: MM/DD/YYYY reads {M-D, D-M}.
A, B, C, D = "1998-03-04", "1998-04-03", "1998-05-06", "1998-06-05"
LINE_FOR: dict[frozenset[str], str] = {
    frozenset({A}): "1998-03-04",
    frozenset({B}): "1998-04-03",
    frozenset({C}): "1998-05-06",
    frozenset({D}): "1998-06-05",
    frozenset({A, B}): "03/04/1998",
    frozenset({C, D}): "05/06/1998",
}
assert len(LINE_FOR) == 6, "the candidate universe lost a shape"


def reference_rule(candidates: list[frozenset[str]]) -> str | None:
    """P8's sentence, over sets: D iff some candidate is exactly {D} and every one contains D."""
    exact = {next(iter(c)) for c in candidates if len(c) == 1}
    winners = [d for d in exact if all(d in c for c in candidates)]
    return winners[0] if len(winners) == 1 else None


def reference_confidence(
    candidates: list[frozenset[str]], scores: list[float], date: str
) -> tuple[float, int]:
    """(score, index) of the first line with the highest score holding exactly {date}."""
    best: tuple[float, int] | None = None
    for index, (cand, score) in enumerate(zip(candidates, scores, strict=True)):
        if cand == frozenset({date}) and (best is None or score > best[0]):
            best = (score, index)
    assert best is not None
    return best


CANDIDATE_LISTS: list[tuple[frozenset[str], ...]] = [
    combo for n in range(5) for combo in itertools.product(LINE_FOR, repeat=n)
]
assert len(CANDIDATE_LISTS) == 1 + 6 + 36 + 216 + 1296, len(CANDIDATE_LISTS)


def test_every_candidate_list_up_to_four_lines_agrees_with_the_reference():
    rng = random.Random(20260925)
    emitted = 0
    for combo in CANDIDATE_LISTS:
        candidates = list(combo)
        scores = [rng.choice((0.6, 0.7, 0.8)) for _ in candidates]
        lines = [OcrLine(LINE_FOR[c], s) for c, s in zip(candidates, scores, strict=True)]
        expected = reference_rule(candidates)
        found = find_date(lines, floor=0.5)
        if expected is None:
            assert found is None, (candidates, found)
            continue
        emitted += 1
        score, index = reference_confidence(candidates, scores, expected)
        assert found == FoundDate(expected, LINE_FOR[candidates[index]], score), candidates
    # Both branches ran: an enumeration that never emits proves only the None half.
    assert 0 < emitted < len(CANDIDATE_LISTS)


def test_the_reference_rule_matches_p8s_worked_examples():
    # The reference is itself checked against P8's own sentences, spelled out.
    assert reference_rule([frozenset({A, B})]) is None
    assert reference_rule([frozenset({A, B}), frozenset({A})]) == A
    assert reference_rule([frozenset({A, B}), frozenset({C})]) is None
    assert reference_rule([frozenset({A}), frozenset({A})]) == A
    assert reference_rule([]) is None
