import pytest

from pam.naming.consistency import RULES, Flag, RuleContext, apply_rules, score, token_tiers
from pam.naming.template import EmittedToken
from pam.naming.tokens import TokenFact


def ctx(**kw):
    base = dict(
        capture_dt="2019-07-05T01:23:11",
        capture_dt_source="qt_utc",
        filename="clip.mp4",
        emitted=(EmittedToken("date", "date", "2019-07-05", True),),
        # M10 Task 9: spec 6.4 rev 17's three operands. Defaulted HERE, in the suite's own
        # helper, and deliberately not on `RuleContext` itself - a production caller that
        # forgets to look a fact up must get a TypeError, while a test about the video-date
        # rule should not have to spell three fields it is not exercising.
        scene=None,
        place=None,
        known_places=frozenset(),
    )
    base.update(kw)
    return RuleContext(**base)


DATE_EMITTED = (EmittedToken("date", "date", "2019-07-05", True),)


def facts(date_tier="medium"):
    return {
        "date": TokenFact("2019-07-05", "capture", 1, date_tier),
        "seq": TokenFact("001", "planner", None, "high"),
    }


def test_video_utc_vs_filename_date_is_flagged():
    # spec 6.4 (rev 3): a UTC video stamp that disagrees with the filename's date
    # is surfaced for review, never auto-resolved.
    flags = apply_rules(ctx(filename="VID_20190704_182311.mp4"))
    assert [(f.token, f.cap) for f in flags] == [("date", "low")]
    assert "UTC" in flags[0].reason


def test_matching_filename_date_is_not_flagged():
    assert apply_rules(ctx(filename="VID_20190705_012311.mp4")) == []


def test_non_utc_sources_are_never_date_flagged():
    # The rule is scoped to qt_utc: an exif date outranks a filename date anyway.
    assert apply_rules(ctx(capture_dt_source="exif", filename="VID_20190704_182311.mp4")) == []


def test_filename_without_a_date_is_not_flagged():
    assert apply_rules(ctx(filename="clip.mp4")) == []


def test_truncated_token_is_flagged():
    # spec 6.4 (rev 4): truncation is a trust event, capped at medium.
    flags = apply_rules(ctx(emitted=(EmittedToken("date", "date", "2019-07", False),)))
    assert [(f.token, f.cap, f.penalty) for f in flags] == [("date", "medium", 10)]


def test_the_reserved_name_rewrite_is_not_a_truncation():
    # Finding 2: 'con' -> 'con-x' LENGTHENS the value. Flagging it earned the item
    # the wrong sentence ("Name shortened to fit the Windows path limit"), a -10 it
    # did not deserve, and a medium cap that would have sent it to manual review.
    emitted = (EmittedToken("orig", "orig", "con-x", True, rewritten=True),)
    flags = apply_rules(ctx(capture_dt_source="exif", emitted=emitted))
    assert flags == []
    assert (
        score(facts(), flags, date_source="exif", emitted=emitted, date_deductions=())[0] == 100
    )
    assert token_tiers({}, emitted, flags) == {"orig": "high"}  # no medium cap


def test_a_budget_cut_still_gets_the_flag_the_penalty_and_the_cap():
    # The control for the test above: a genuine truncation keeps all three.
    emitted = (EmittedToken("orig", "orig", "con-", False, rewritten=False),)
    flags = apply_rules(ctx(capture_dt_source="exif", emitted=emitted))
    assert [(f.token, f.cap, f.penalty) for f in flags] == [("orig", "medium", 10)]
    assert (
        score(facts(), flags, date_source="exif", emitted=emitted, date_deductions=())[0] == 90
    )
    assert token_tiers({}, emitted, flags) == {"orig": "medium"}


def test_the_no_room_for_x_branch_is_both_a_rewrite_and_a_truncation():
    # template._finalize's "shorten until -x fits" branch sets BOTH flags, and the
    # truncation half must still reach the user - the value really did get cut.
    emitted = (EmittedToken("seq", "seq", "00", False, rewritten=True),)
    flags = apply_rules(ctx(capture_dt_source="exif", emitted=emitted))
    assert [(f.token, f.cap, f.penalty) for f in flags] == [("seq", "medium", 10)]


def test_truncation_penalty_replaces_the_generic_fifteen():
    # rev 4 amendment: -10 instead of the generic -15, not on top of it.
    cut = (EmittedToken("date", "date", "2019-07", False),)
    flags = apply_rules(ctx(capture_dt_source="exif", emitted=cut))
    points, reasons = score(facts(), flags, date_source="exif", emitted=cut, date_deductions=())
    assert points == 90 and len(reasons) == 1


def test_score_deductions():
    assert (
        score(facts(), [], date_source="exif", emitted=DATE_EMITTED, date_deductions=())[0]
        == 100
    )
    assert (
        score(facts(), [], date_source="qt_utc", emitted=DATE_EMITTED, date_deductions=())[0]
        == 90
    )
    assert (
        score(facts(), [], date_source="file_mtime", emitted=DATE_EMITTED, date_deductions=())[
            0
        ]
        == 75
    )
    absent = {"date": TokenFact("", "capture", 1, "low")}
    assert (
        score(absent, [], date_source="none", emitted=DATE_EMITTED, date_deductions=())[0] == 60
    )


def test_score_is_clamped_and_gives_a_reason_per_deduction():
    flags = apply_rules(ctx(filename="VID_20190704_182311.mp4"))
    points, reasons = score(
        facts(), flags, date_source="qt_utc", emitted=DATE_EMITTED, date_deductions=()
    )
    assert points == 75  # 100 - 10 (qt_utc) - 15 (the flag)
    assert len(reasons) == 2 and all(r.endswith(".") for r in reasons)
    many = [flags[0]] * 20
    assert (
        score(facts(), many, date_source="none", emitted=DATE_EMITTED, date_deductions=())[0]
        == 0
    )


# --- N2: the score may only charge for a claim the FILENAME actually makes -----

ORIG_EMITTED = (EmittedToken("orig", "orig", "clip", True),)


def test_a_date_deduction_needs_a_date_token_in_the_name():
    # N2, the measured reproduction: --template "{orig}" on a qt_utc video scored
    # 90 with "the local date may differ" attached to a filename containing no
    # date at all, while token_tiers - which does read `emitted` - rated it high.
    # spec 6.4: the score derives from tokens_json, which holds ONLY emitted
    # tokens. Score and tier now agree.
    points, reasons = score(
        facts(), [], date_source="qt_utc", emitted=ORIG_EMITTED, date_deductions=()
    )
    assert (points, reasons) == (100, [])
    assert token_tiers(facts(), ORIG_EMITTED, []) == {"orig": "high"}


def test_every_date_deduction_is_conditional_on_the_date_token():
    # All THREE date rows of spec 6.4's table, not just qt_utc.
    absent = {"date": TokenFact("", "capture", 1, "low")}
    assert score(absent, [], date_source="none", emitted=ORIG_EMITTED, date_deductions=()) == (
        100,
        [],
    )
    assert score(
        facts(), [], date_source="file_mtime", emitted=ORIG_EMITTED, date_deductions=()
    ) == (100, [])
    assert score(
        facts(), [], date_source="qt_utc", emitted=ORIG_EMITTED, date_deductions=()
    ) == (100, [])
    # ... and every one of them still fires when the date IS in the name.
    assert (
        score(absent, [], date_source="none", emitted=DATE_EMITTED, date_deductions=())[0] == 60
    )
    assert (
        score(facts(), [], date_source="file_mtime", emitted=DATE_EMITTED, date_deductions=())[
            0
        ]
        == 75
    )
    assert (
        score(facts(), [], date_source="qt_utc", emitted=DATE_EMITTED, date_deductions=())[0]
        == 90
    )


def test_a_flag_on_an_unemitted_token_costs_nothing():
    # The same predicate token_tiers applies to caps, applied to the deductions.
    # A rule can flag a token the budget then dropped from the name; charging for
    # it would deduct 15 points for a claim the filename does not make.
    flags = [Flag("place", "low", "Place looks wrong.", 15)]
    assert score(
        facts(), flags, date_source="exif", emitted=DATE_EMITTED, date_deductions=()
    ) == (100, [])
    emitted_with_place = (*DATE_EMITTED, EmittedToken("place", "place", "Tahoe", True))
    assert score(
        facts(), flags, date_source="exif", emitted=emitted_with_place, date_deductions=()
    ) == (
        85,
        ["Place looks wrong."],
    )


def test_token_tiers_apply_the_caps():
    emitted = (
        EmittedToken("date", "date", "2019-07-05", True),
        EmittedToken("seq", "seq", "001", True),
    )
    flags = apply_rules(ctx(filename="VID_20190704_182311.mp4", emitted=emitted))
    tiers = token_tiers(facts(date_tier="high"), emitted, flags)
    assert tiers == {"date": "low", "seq": "high"}  # the cap beat the base tier


def test_token_tiers_default_to_high_for_a_token_absent_from_facts():
    # A token can be emitted (e.g. {orig}, always present) without a facts entry;
    # token_tiers must not KeyError, and the missing base tier defaults to "high"
    # so it never drags an item's tier down on its own.
    emitted = (EmittedToken("orig", "orig", "IMG_001", True),)
    assert token_tiers({}, emitted, []) == {"orig": "high"}


def test_token_tiers_fold_multiple_flags_on_the_same_token():
    # Two rules can flag the same token; the caps accumulator must fold them with
    # min_tier rather than letting the later flag overwrite the earlier one. The
    # first flag here is already the more restrictive of the two, so a plain
    # overwrite (last flag wins) would wrongly report "medium" instead of "low".
    emitted = (EmittedToken("date", "date", "2019-07-05", True),)
    flags = [
        Flag("date", "low", "first flag", 15),
        Flag("date", "medium", "second flag", 15),
    ]
    tiers = token_tiers(facts(date_tier="high"), emitted, flags)
    assert tiers == {"date": "low"}


def test_token_tiers_keep_the_base_tier_when_it_is_already_lower_than_the_cap():
    # The item's tier is min(base, cap) both directions: test_token_tiers_apply_the_caps
    # only proves the cap can pull a high base tier down. Here the base tier is
    # already lower than the cap, so the cap must not win outright.
    emitted = (EmittedToken("date", "date", "2019-07-05", True),)
    flags = [Flag("date", "medium", "some cap", 15)]
    tiers = token_tiers(facts(date_tier="low"), emitted, flags)
    assert tiers == {"date": "low"}


def test_trust_constants_are_the_single_source_of_the_deductions():
    # These get re-balanced from users' archives; re-tuning must not mean grepping.
    from pam.naming import consistency as c

    assert (c.TRUST_NO_DATE, c.TRUST_FILE_MTIME, c.TRUST_CONSISTENCY) == (40, 25, 15)
    assert (c.TRUST_QT_UTC, c.TRUST_TRUNCATED) == (10, 10)
    flags = apply_rules(ctx(emitted=(EmittedToken("date", "date", "2019-07", False),)))
    assert flags[0].penalty == c.TRUST_TRUNCATED


def test_the_rule_table_is_the_extension_point():
    # spec 6.4: "adding a rule is a one-row change". M10 Task 9 landed the third -
    # the caption-vs-geocode rule, dead since rev 2 because it needed two enrichers
    # that never coexisted. Three more arrive with their enrichers (M11-M12); nothing
    # is stubbed for them now.
    assert len(RULES) == 3


# Spec 6.4's flag sentence is stored in `trust_reasons_json` and read back by the review
# UI, so which of two matching places it names must be the same on every run. The
# implementation takes the alphabetically first.
#
# TWELVE PAIRS, NOT ONE, AND THAT IS THE POINT. The end-to-end pin in
# `test_scene_naming.py` uses a single pair, and a `frozenset` of two strings iterates in
# an order that depends on Python's per-process string hash seed - measured over eight
# fresh interpreters, dropping `sorted()` failed that pin in **two** of them and passed in
# six. One coin flip is not a guard. Twelve independent pairs make an unsorted
# implementation agree by chance with probability 2**-12, on every run and under every
# seed.
#
# Asserted non-empty at collection time (M7's M743: pytest SKIPS an empty parametrize).
DETERMINISM_PAIRS = [
    ("Kyoto", "Paris"),
    ("Aachen", "Zurich"),
    ("Bergen", "Yalta"),
    ("Cairo", "Xanthi"),
    ("Delhi", "Wexford"),
    ("Essen", "Vienna"),
    ("Faro", "Utrecht"),
    ("Genoa", "Turin"),
    ("Hanoi", "Sydney"),
    ("Ibiza", "Rennes"),
    ("Jaipur", "Quito"),
    ("Kobe", "Perth"),
]
assert len(DETERMINISM_PAIRS) == 12, "the determinism table lost or gained a pair"


@pytest.mark.parametrize("first,second", DETERMINISM_PAIRS)
def test_two_matching_places_always_report_the_alphabetically_first(first, second):
    """Spec 6.2 rule 6's determinism applied to the SENTENCE: "a set's iteration order is
    not a rule". Both orderings of the caption are given so the caption's own word order
    cannot be what decides it, and the expected name is `min` of the two spelled here
    rather than read back from the rule."""
    for label in (f"{first} and {second}", f"{second} and {first}"):
        flags = apply_rules(
            ctx(
                scene=label,
                place="Tokyo",
                known_places=frozenset({first, second, "Tokyo"}),
            )
        )
        assert len(flags) == 1
        assert min(first, second) in flags[0].reason
        assert max(first, second) not in flags[0].reason
