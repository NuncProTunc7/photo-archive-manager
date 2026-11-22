import pytest

from pam.naming.sanitize import (
    MAX_FULL_PATH,
    apply_reserved_stem_rule,
    full_path_length,
    reserved_head,
    sanitize_report,
    sanitize_token,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Fireworks", "Fireworks"),
        ("Lake  Tahoe", "Lake-Tahoe"),
        ("  padded  ", "padded"),
        ("tab\there", "tab-here"),
        ("Café", "Cafe"),
        ("Zoë's party", "Zoes-party"),
        ("emoji 🎉 day", "emoji-day"),
        ("家族写真", ""),
        ("a---b", "a-b"),
        ("a-_-b", "a-b"),
        # A run collapses to its FIRST character, so '-' vs '_' is preserved.
        ("a__b", "a_b"),
        ("a-_b", "a-b"),
        ("a_-b", "a_b"),
        ("__init__", "init"),
        # NBSP and ideographic space are \s (become '-'); ZWSP and BOM are not.
        ("a b", "a-b"),
        ("a　b", "a-b"),
        ("a​b", "ab"),
        # NUL and most controls are simply illegal, but \x1c-\x1f are \s to
        # Python, so they become '-' before the illegal-char strip runs.
        ("ctrl\x00\x01chars", "ctrlchars"),
        ("ctrl\x1fchars", "ctrl-chars"),
        ("..", ""),
        ("...dots...", "dots"),
        ("v1.2", "v1.2"),
        ("", ""),
        ('<>:"/\\|?*', ""),
    ],
)
def test_sanitize_token(raw, expected):
    assert sanitize_token(raw) == expected


@pytest.mark.parametrize("raw", ["Lake  Tahoe", "Café", "a-_-b", "  x  ", "v1.2"])
def test_sanitize_token_is_idempotent(raw):
    once = sanitize_token(raw)
    assert sanitize_token(once) == once


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("con", "con-x"),
        ("CON", "CON-x"),
        ("prn", "prn-x"),
        ("aux", "aux-x"),
        ("Com7", "Com7-x"),
        ("com9", "com9-x"),
        ("lpt9", "lpt9-x"),
        ("nul", "nul-x"),
        ("con.backup", "con-x.backup"),
        ("console", "console"),
        ("com10", "com10"),
        ("2019-07-04_001", "2019-07-04_001"),
        # Edge shapes: an empty device segment is not reserved, and the rule
        # is idempotent (an already-suffixed stem is left alone).
        ("", ""),
        (".", "."),
        (".con", ".con"),
        ("con-x", "con-x"),
    ],
)
def test_reserved_stems(stem, expected):
    assert apply_reserved_stem_rule(stem) == expected


def test_full_path_length_is_the_spec_formula():
    # spec 6.2 rule 5, taken literally: len(root) + len(rel_dir) + len(name).
    # It counts no separators, so it under-counts the real path by <=2; the
    # 20-char margin under Windows' 260 absorbs that.
    assert full_path_length("C:\\photos", "2019/july", "a.jpg") == 9 + 9 + 5
    assert full_path_length("C:\\photos", "", "a.jpg") == 9 + 0 + 5


def test_max_full_path_is_the_spec_budget():
    assert MAX_FULL_PATH == 240


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # C5: letters NFKD does not decompose - without the fold table these are
        # deleted outright, which mangles people's and place names.
        ("Møller", "Moller"),
        ("Bjørn", "Bjorn"),
        ("Straße", "Strasse"),
        ("Ægir", "AEgir"),
        ("Łukasz", "Lukasz"),
        ("Đuro", "Duro"),
        ("Þór", "THor"),
        ("Œuvre", "OEuvre"),
        # NFKD (not NFD) so compatibility forms fold instead of vanishing.
        ("ﬁne", "fine"),
        ("ＡＢ", "AB"),
        # Regression: decomposing diacritics still behave as they did in M3.
        ("Čech", "Cech"),
        ("naïve café", "naive-cafe"),
    ],
)
def test_sanitize_token_transliterates(raw, expected):
    assert sanitize_token(raw) == expected


@pytest.mark.parametrize("raw", ["Møller", "Straße", "ﬁne", "ＡＢ", "Ægir"])
def test_transliteration_is_idempotent(raw):
    once = sanitize_token(raw)
    assert sanitize_token(once) == once


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        # C6: Windows reserves the 0 variants too.
        ("com0", "com0-x"),
        ("lpt0", "lpt0-x"),
        ("COM0", "COM0-x"),
    ],
)
def test_reserved_stems_include_zero_variants(stem, expected):
    assert apply_reserved_stem_rule(stem) == expected


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("con", "con"),
        ("con.backup", "con"),
        ("CON", "CON"),
        # A device name contains no '_' and no '.', so a reserved head never
        # spans a separator - the property build_stem relies on.
        ("con_001", None),
        ("console", None),
        ("", None),
    ],
)
def test_reserved_head(stem, expected):
    assert reserved_head(stem) == expected


# --- spec 6.2 rule 7: which of rules 1-3 fired (M8 Task 6 Step 2) ---------------------

# One row per RULE, each written from spec 6.2's own sentence for that rule rather than
# from a call to `sanitize_report`. The `expected` set names every flag that must be
# true; every other flag must be false, which is what makes a row that fires two rules a
# failure rather than a pass. Asserted non-empty at collection: pytest SKIPS an empty
# parametrize, so a control built from a list can vanish with the list (M743).
RULE_ROWS = [
    # rule 1: a run of whitespace becomes a single '-'.
    ("Lake  Tahoe", "Lake-Tahoe", {"whitespace"}),
    # rule 2: the normative fold table (rev 4), which NFKD alone would delete outright.
    ("Ægir", "AEgir", {"folded"}),
    # rule 2 again, by NFKD + mark stripping rather than by the table.
    ("Café", "Cafe", {"folded"}),
    # rule 3: a character outside the A-Za-z0-9._- allowlist is deleted.
    ("a:b", "ab", {"removed"}),
    # rules 1-3's cleanup: a separator run collapses, and the edges are trimmed.
    ("a__b", "a_b", {"tidied"}),
    ("-a-", "a", {"tidied"}),
    # nothing at all: a sanitized token passes through and reports nothing.
    ("Fireworks", "Fireworks", set()),
    # several rules at once, each reported on its own.
    ("  Lake #2__Tahoe--", "Lake-2_Tahoe", {"whitespace", "removed", "tidied"}),
]
assert RULE_ROWS, "the sanitize-report table must not be empty"

ALL_FLAGS = ("whitespace", "folded", "removed", "tidied")


@pytest.mark.parametrize(("raw", "text", "expected"), RULE_ROWS)
def test_sanitize_report_names_the_rule_that_fired(raw, text, expected):
    """Spec 6.2 rule 7 needs WHICH rule, not THAT something changed.

    A caller could diff `raw` against `text` and learn the second; the three normative
    sentences differ by the first, which only the substitution sites can answer.
    """
    assert set(expected) <= set(ALL_FLAGS), "the row names a flag that does not exist"
    report = sanitize_report(raw)
    assert report.text == text
    assert {flag for flag in ALL_FLAGS if getattr(report, flag)} == expected
    assert report.changed is bool(expected)


@pytest.mark.parametrize(("raw", "text", "expected"), RULE_ROWS)
def test_sanitize_token_is_the_report_s_text(raw, text, expected):
    """One implementation of rules 1-3, so the report cannot describe a different one."""
    assert sanitize_token(raw) == sanitize_report(raw).text
