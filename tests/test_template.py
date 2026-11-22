import pytest

from pam.errors import PamError, TemplateError
from pam.naming import template
from pam.naming.template import (
    _TRIM_ORDER,
    DROPPED_NOTE,
    FALLBACK_STEM,
    TOKEN_LABELS,
    TOKEN_NAMES,
    VALUE_TOKENS,
    build_stem,
    parse_template,
)


def test_default_template_parses():
    assert parse_template("{date}_{event-or-scene}_{people}_{seq}") == (
        "date",
        "event-or-scene",
        "people",
        "seq",
    )


def test_all_documented_tokens_are_valid():
    template = "{date}_{event}_{scene}_{people}_{place}_{seq}_{orig}"
    assert parse_template(template) == (
        "date",
        "event",
        "scene",
        "people",
        "place",
        "seq",
        "orig",
    )
    assert set(parse_template(template)) <= set(TOKEN_NAMES)


@pytest.mark.parametrize(
    "bad",
    ["", "plain", "{date", "{date}x{seq}", "{banana}", "{date}_{date}"],
)
def test_bad_templates_raise(bad):
    with pytest.raises(TemplateError):
        parse_template(bad)


def test_template_error_is_a_pam_error():
    # The CLI's uniform PamError handling (spec 13) must cover it in M4.
    assert issubclass(TemplateError, PamError)


ROOT = "C:\\archive"  # len 10
DEFAULT = ("date", "event-or-scene", "people", "seq")


def build(order, values, rel_dir="2019", exts=("jpg",)):
    return build_stem(order, values, archive_root_abs=ROOT, rel_dir=rel_dir, exts=exts)


def was_cut(result):
    """N7: StemResult.truncated is gone - "the budget cut something" is derived.

    EmittedToken.complete already carries it per token, which is the form the
    trust rules and tokens_json both read; a third representation of one fact is a
    third thing that can disagree with the other two.
    """
    return any(not token.complete for token in result.emitted)


def rel_dir_for_budget(budget, ext="jpg"):
    # Invert stem_budget(), then assert the inverse actually holds: if the budget
    # formula ever changes, this fails loudly instead of silently testing the
    # wrong budget (which is how a whole budget suite can go quietly green).
    rel_dir = "d" * (MAX_FULL_PATH - len(ROOT) - len(ext) - 1 - budget)
    assert stem_budget(ROOT, rel_dir, (ext,)) == budget
    return rel_dir


def test_full_assembly():
    r = build(
        DEFAULT,
        {
            "date": "2019-07-04",
            "event": "Fireworks Lake Tahoe",
            "people": "Emma",
            "seq": "001",
        },
    )
    assert r.stem == "2019-07-04_Fireworks-Lake-Tahoe_Emma_001"
    assert was_cut(r) is False


def test_missing_tokens_omit_their_separator():
    r = build(DEFAULT, {"date": "2019-07-04", "seq": "001"})
    assert r.stem == "2019-07-04_001"  # the vision's worst case, verbatim


def test_event_outranks_scene():
    r = build(DEFAULT, {"date": "2019-07-04", "event": "Camping", "scene": "Tent"})
    assert r.stem == "2019-07-04_Camping"


def test_scene_fills_in_when_event_empty():
    r = build(DEFAULT, {"date": "2019-07-04", "scene": "Fireworks"})
    assert r.stem == "2019-07-04_Fireworks"


def test_scene_fallback_applies_after_sanitization():
    r = build(DEFAULT, {"date": "2019-07-04", "event": "🎉🎉", "scene": "Party"})
    assert r.stem == "2019-07-04_Party"


def test_missing_date_becomes_undated():
    r = build(DEFAULT, {"scene": "Beach"})
    assert r.stem == "undated_Beach"


BUDGET_VALUES = {
    "date": "2019-07-04",
    "scene": "S" * 40,
    "place": "P" * 30,
    "people": "E" * 20,
    "seq": "001",
}
BUDGET_ORDER = ("date", "event-or-scene", "place", "people", "seq")
# Full stem: 10+1+40+1+30+1+20+1+3 = 107 chars.


def test_budget_drops_scene_first():
    r = build(BUDGET_ORDER, BUDGET_VALUES, rel_dir=rel_dir_for_budget(100))
    assert was_cut(r) is False
    assert r.stem == "2019-07-04_" + "P" * 30 + "_" + "E" * 20 + "_001"


def test_budget_drops_scene_then_place():
    r = build(BUDGET_ORDER, BUDGET_VALUES, rel_dir=rel_dir_for_budget(60))
    assert r.stem == "2019-07-04_" + "E" * 20 + "_001"


def test_budget_drops_all_three_then_fits():
    r = build(BUDGET_ORDER, BUDGET_VALUES, rel_dir=rel_dir_for_budget(30))
    assert was_cut(r) is False
    assert r.stem == "2019-07-04_001"


def test_budget_hard_truncates_as_last_resort():
    r = build(BUDGET_ORDER, BUDGET_VALUES, rel_dir=rel_dir_for_budget(8))
    assert was_cut(r) is True
    assert r.stem == "2019-07"  # '2019-07-04_001'[:8] with the trailing '-' stripped


def test_reserved_stem_gets_suffix():
    r = build(parse_template("{orig}"), {"orig": "con"})
    assert r.stem == "con-x"


def test_deterministic():
    values = {"date": "2019-07-04", "scene": "Beach day", "seq": "001"}
    assert build(DEFAULT, values) == build(DEFAULT, values)


from pam.naming.sanitize import (  # noqa: E402
    MAX_FULL_PATH,
    reserved_head,
    stem_budget,
)


def test_alias_excludes_its_own_parts():
    # C3: {event-or-scene} plus {scene} would emit the same value twice.
    for bad in ("{date}_{event-or-scene}_{scene}", "{event-or-scene}_{event}"):
        with pytest.raises(TemplateError):
            parse_template(bad)


def test_unknown_token_says_so():
    # C7 / item 17a: {DATE} used to report "malformed at position 0".
    with pytest.raises(TemplateError, match="Unknown template token"):
        parse_template("{DATE}")


def test_separator_is_cosmetic():
    # Item 12: the locked grammar's '_' is optional; the assembler always joins
    # with exactly one. '{date}__{seq}' is still an error.
    assert parse_template("{date}_") == ("date",)
    assert parse_template("{date}{seq}") == ("date", "seq")
    with pytest.raises(TemplateError):
        parse_template("{date}__{seq}")


def test_unknown_token_in_order_is_a_pam_error():
    # C7: not a bare KeyError - spec 13 would give exit 2 plus a traceback.
    with pytest.raises(TemplateError):
        build(("banana",), {})


def test_empty_stem_falls_back():
    # C1: build_stem used to return '', which would name the file '.jpg'.
    assert build(("scene",), {"scene": "🎉"}).stem == FALLBACK_STEM
    assert build(("scene", "people"), {}).stem == FALLBACK_STEM
    assert build(("date", "scene"), {}).stem == "undated"


@pytest.mark.parametrize(
    ("word", "budget"),
    [
        ("con-x", 3),
        ("connect", 3),
        ("Console", 3),
        ("Nullarbor", 3),
        ("Auxiliary", 3),
        ("Com1x", 4),
        ("prnt", 3),
    ],
)
def test_truncation_never_regenerates_a_device_name(word, budget):
    # C1: rule 4 must be re-applied AFTER rule 5's cut. Any word truncating to a
    # device-name prefix regenerates one, not just 'con-x'.
    r = build(("orig",), {"orig": word}, rel_dir=rel_dir_for_budget(budget))
    assert reserved_head(r.stem) is None
    assert len(r.stem) <= budget
    assert r.stem


def test_minimum_one_character_beats_the_budget():
    # C1 + item 13: the max(budget, 1) guard, finally pinned.
    for budget in (0, 1):
        r = build(("date",), {"date": "2019-07-04"}, rel_dir=rel_dir_for_budget(budget))
        assert r.stem == "2"


def test_exact_fit_boundary_in_both_directions():
    # Item 13: len(stem) == budget must not truncate; one over must.
    exact = build(("date",), {"date": "2019-07-04"}, rel_dir=rel_dir_for_budget(10))
    assert exact.stem == "2019-07-04" and was_cut(exact) is False
    over = build(("date",), {"date": "2019-07-04"}, rel_dir=rel_dir_for_budget(9))
    assert over.stem == "2019-07-0" and was_cut(over) is True


def test_cut_never_leaves_a_trailing_separator():
    r = build(
        ("date", "seq"), {"date": "2019-07-04", "seq": "001"}, rel_dir=rel_dir_for_budget(11)
    )
    assert r.stem == "2019-07-04"


def test_an_absent_trim_candidate_does_not_disturb_the_trim_of_a_present_one():
    # Item 13: 'place' is in _TRIM_ORDER but not in this template, so the drop
    # loop must skip over it without disturbing the trim of 'scene'.
    r = build(
        ("date", "scene", "seq"),
        {"date": "2019-07-04", "scene": "S" * 40, "seq": "001"},
        rel_dir=rel_dir_for_budget(20),
    )
    assert r.stem == "2019-07-04_001"


def test_a_part_the_budget_cuts_away_entirely_IS_DISCLOSED():
    """The INVERSION of M8's characterization test, landed by M9 Task 10.

    `_cut`'s budget-exhausted branch (room <= 0) removes a remaining part ENTIRELY
    rather than truncating it. Through M8 such a token landed in neither `emitted` nor
    any drop record, so nothing downstream fired: no medium cap, no reason, trust 100,
    tier high. That is reachable TODAY and by the tokens `resolve_tokens` really
    supplies - measured at M8 Task 1, order ("date", "seq") at budget 9 - which is why
    it was pinned as a known-open finding rather than left unwritten.

    Spec 6.2 rule 7's second half says "a token trimmed away entirely gets a sentence in
    `trust_reasons_json`". This cut trims a token away entirely and the path budget is
    what does it, so it gets the same sentence rule 5's trim-order drop gets. The stem,
    the emitted row and the missing digits are all unchanged - only the silence is gone.
    """
    r = build(
        ("date", "seq"),
        {"date": "2019-07-04", "seq": "001"},
        rel_dir=rel_dir_for_budget(9),
    )
    assert r.stem == "2019-07-0"
    assert [t.name for t in r.emitted] == ["date"]
    assert "001" not in r.stem
    # It is no longer without a trace: pam says the number went, and why.
    assert r.notes == ("pam left the number out of this name to fit the Windows path limit.",)


def test_the_OTHER_budget_exit_cuts_a_token_away_TOO_and_discloses_it():
    """`_cut` has TWO exits that remove a token entirely, and only one of them was pinned.

    M9 Task 10 review, M1325. The exit above (budget 9) is `len(value) > room`: the date
    is truncated and everything after it is abandoned. This one is `room <= 0`: the date
    fits EXACTLY, so there is no room left even for the separator and `{seq}` is
    abandoned untouched. Both leave a token out of the filename and out of `emitted`,
    both are the path budget doing it, and spec 6.2 rule 7 owes a sentence for both.

    The suite reached this branch only through
    ::test_cut_never_leaves_a_trailing_separator, which asserts the STEM and nothing
    else - so a change that let this exit lose its sentence was invisible. `complete` is
    asserted True here because that is what separates the two exits: nothing was
    shortened, there was simply no room.
    """
    r = build(
        ("date", "seq"), {"date": "2019-07-04", "seq": "001"}, rel_dir=rel_dir_for_budget(11)
    )
    assert r.stem == "2019-07-04"
    assert [(t.name, t.complete) for t in r.emitted] == [("date", True)]
    assert r.notes == (DROPPED_NOTE.format(label=TOKEN_LABELS["seq"]),)


# --------------------------------------- spec 6.2 rule 5's trim order, and rule 7's half
#
# EVERY TEST OF THE TRIM ORDER WAS THEORETICAL UNTIL M9 TASK 10. `resolve_tokens` supplied
# date/seq/orig only, so no candidate of {scene}/{place}/{people} could ever be emitted for
# an archive of real files; the tests below drive `build_stem` directly, which is the assembler's
# contract for every token spec 6.1 names and not only for the ones a planner can fill.
#
# The order is written out here FROM THE SPEC - spec 6.2 rule 5: "Trim, in order: {scene},
# {place}, {people} until it fits" - and never read from `template._TRIM_ORDER`, which
# would make every assertion below agree with whatever that tuple happened to say.
TRIM_ORDER_FROM_THE_SPEC = ("scene", "place", "people")

# Spec 6.1's token list minus the {event-or-scene} alias, which is a template SLOT and
# never a drop label: a drop is reported against the token that supplied the value.
# Hand-written from the spec for the same reason.
VALUE_TOKENS_FROM_THE_SPEC = ("date", "event", "scene", "people", "place", "seq", "orig")


def test_the_trim_order_is_the_specs_order():
    assert _TRIM_ORDER == TRIM_ORDER_FROM_THE_SPEC


# ONE ROW PER STEP OF THE SPEC'S ORDER, NAMED AT MODULE SCOPE AND PINNED BY AN EXACT
# LENGTH AT COLLECTION TIME. The M9 Task 10 review measured this on the decorator that
# used to sit inline: narrowing it to a single row (M1344) was completely silent, and
# pytest SKIPS an empty parametrize rather than failing it (M7's M743). The length is
# written against TRIM_ORDER_FROM_THE_SPEC, not against 3, because the table's job is to
# prove EVERY step of that order - so widening the order without widening this table is
# a red collection, not a quiet gap.
TRIM_STEPS = [
    # One drop is enough: {scene} goes and NOTHING ELSE DOES - the loop must stop
    # the moment the joined parts fit, not run its three steps out.
    (100, ("date", "place", "people", "seq")),
    (60, ("date", "people", "seq")),
    (30, ("date", "seq")),
]
assert len(TRIM_STEPS) == len(TRIM_ORDER_FROM_THE_SPEC), "one budget row per drop step"

DISCLOSURE_STEPS = [(1, 100), (2, 60), (3, 30)]
assert len(DISCLOSURE_STEPS) == len(TRIM_ORDER_FROM_THE_SPEC), "one row per drop step"


@pytest.mark.parametrize(("budget", "survivors"), TRIM_STEPS)
def test_the_budget_drops_scene_then_place_then_people(budget, survivors):
    r = build(BUDGET_ORDER, BUDGET_VALUES, rel_dir=rel_dir_for_budget(budget))
    assert tuple(t.name for t in r.emitted) == survivors


@pytest.mark.parametrize(("dropped_count", "budget"), DISCLOSURE_STEPS)
def test_each_step_of_the_trim_order_is_disclosed_in_the_order_it_was_taken(
    dropped_count, budget
):
    r = build(BUDGET_ORDER, BUDGET_VALUES, rel_dir=rel_dir_for_budget(budget))
    assert r.notes == tuple(
        DROPPED_NOTE.format(label=TOKEN_LABELS[name])
        for name in TRIM_ORDER_FROM_THE_SPEC[:dropped_count]
    )


def test_an_absent_trim_candidate_does_not_consume_a_drop_step():
    """The case an archive of real files hits on EVERY file, because {scene} and {people} do not
    exist yet - only {place} does, and it is third in nothing.

    A loop that advanced one step per candidate whether or not the candidate was there
    would 'spend' {scene} on nothing and stop with {place} still in a name that does not
    fit. Two shapes are checked: the FIRST candidate absent (so the drop that happens is
    the second one), and the first TWO absent (so it is the third).
    """
    values = {"date": "2019-07-04", "place": "P" * 30, "people": "E" * 20, "seq": "001"}
    order = ("date", "place", "people", "seq")
    # 10 + 1 + 30 + 1 + 20 + 1 + 3 = 66. A budget of 40 needs exactly one drop, and
    # {scene} is not in this template at all.
    r = build(order, values, rel_dir=rel_dir_for_budget(40))
    assert r.stem == "2019-07-04_" + "E" * 20 + "_001"
    assert r.notes == (DROPPED_NOTE.format(label=TOKEN_LABELS["place"]),)

    # Both {scene} and {place} absent: {people} still goes.
    r = build(
        ("date", "people", "seq"),
        {"date": "2019-07-04", "people": "E" * 20, "seq": "001"},
        rel_dir=rel_dir_for_budget(20),
    )
    assert r.stem == "2019-07-04_001"
    assert r.notes == (DROPPED_NOTE.format(label=TOKEN_LABELS["people"]),)


def test_a_name_that_fits_discloses_nothing():
    r = build(DEFAULT, {"date": "2019-07-04", "scene": "Beach", "people": "Emma", "seq": "001"})
    assert r.notes == ()


def test_a_truncated_token_is_not_a_dropped_one():
    """Spec 6.4 charges truncation -10 and rule 7 charges a drop nothing, so the two must
    never be reported as each other. A token the budget SHORTENED is in `emitted` with
    complete=False and gets no drop sentence."""
    r = build(
        ("date", "orig"),
        {"date": "2019-07-04", "orig": "O" * 40},
        rel_dir=rel_dir_for_budget(20),
    )
    assert [(t.name, t.complete) for t in r.emitted] == [("date", True), ("orig", False)]
    assert r.notes == ()


def test_every_token_the_spec_names_has_a_word_for_the_drop_sentence():
    """A label the map does not carry would put the raw token name in a user's Why? panel.

    The keys are checked against spec 6.1's token list written out above, not against
    `template.VALUE_TOKENS`, which is the production partition and would make this pass
    by construction.
    """
    assert set(TOKEN_LABELS) == set(VALUE_TOKENS_FROM_THE_SPEC)
    assert set(VALUE_TOKENS) == set(VALUE_TOKENS_FROM_THE_SPEC)


def test_the_drop_sentence_names_the_token_and_the_reason():
    # Spelled out once, so a reworded constant is a red test rather than a silent
    # change to copy a user reads (spec 6.2 rule 7's whole subject is what pam SAYS).
    assert (
        DROPPED_NOTE.format(label=TOKEN_LABELS["place"])
        == "pam left the place name out of this name to fit the Windows path limit."
    )


def test_emitted_records_what_is_in_the_stem():
    # C2: tokens_json provenance. Names, slots, values, completeness.
    r = build(DEFAULT, {"date": "2019-07-04", "scene": "Beach", "people": "Emma", "seq": "001"})
    assert [t.name for t in r.emitted] == ["date", "scene", "people", "seq"]
    assert [t.slot for t in r.emitted] == ["date", "event-or-scene", "people", "seq"]
    assert [t.value for t in r.emitted] == ["2019-07-04", "Beach", "Emma", "001"]
    assert all(t.complete for t in r.emitted)
    assert r.stem == "_".join(t.value for t in r.emitted)


def test_emitted_records_which_side_of_the_alias_won():
    r = build(DEFAULT, {"date": "2019-07-04", "event": "Fireworks"})
    assert [(t.name, t.slot) for t in r.emitted] == [
        ("date", "date"),
        ("event", "event-or-scene"),
    ]


def test_emitted_marks_the_token_truncation_mangled():
    # C2's load-bearing case: a tokens_json built from input values would claim
    # '2019-07-04' for a file actually named '2019-07'.
    r = build(BUDGET_ORDER, BUDGET_VALUES, rel_dir=rel_dir_for_budget(8))
    assert [(t.name, t.value, t.complete) for t in r.emitted] == [("date", "2019-07", False)]


def test_emitted_drops_tokens_the_cut_removed_entirely():
    r = build(
        ("date", "seq"), {"date": "2019-07-04", "seq": "001"}, rel_dir=rel_dir_for_budget(12)
    )
    assert [(t.value, t.complete) for t in r.emitted] == [("2019-07-04", True), ("0", False)]
    assert r.stem == "_".join(t.value for t in r.emitted)


def test_emitted_carries_the_reserved_suffix():
    # Finding 2: the -x rewrite LENGTHENED this value, so it is `rewritten`, never
    # `complete=False`. Reporting it as truncation earned the item spec 6.4's
    # "Name shortened to fit the Windows path limit", a -10 and a medium cap.
    r = build(("orig",), {"orig": "con"})
    assert [(t.value, t.complete, t.rewritten) for t in r.emitted] == [("con-x", True, True)]
    assert build(("scene",), {"scene": "🎉"}).emitted == ()


def test_a_budget_cut_is_complete_false_and_not_a_rewrite():
    # The other half of finding 2: the two signals must not be able to merge again.
    r = build(BUDGET_ORDER, BUDGET_VALUES, rel_dir=rel_dir_for_budget(8))
    assert [(t.name, t.value, t.complete, t.rewritten) for t in r.emitted] == [
        ("date", "2019-07", False, False)
    ]


def test_a_reserved_head_inside_a_multi_token_stem_is_rewritten_in_place():
    # Finding 5's coverage gap: _finalize's reserved-head branch was only ever
    # exercised on a single-token stem. A device name has no '.' and no '_', so a
    # multi-token stem can only be reserved via the FIRST token's dot-segment -
    # 'con.backup_001' is still CON to Win32, and the -x must land inside token 0.
    r = build(("orig", "seq"), {"orig": "con.backup", "seq": "001"})
    assert r.stem == "con-x.backup_001"
    assert [(t.name, t.value, t.complete, t.rewritten) for t in r.emitted] == [
        ("orig", "con-x.backup", True, True),
        ("seq", "001", True, False),
    ]


def test_the_stem_is_ALWAYS_the_emitted_values_joined_by_one_underscore():
    """The invariant `pam/naming/collision.py` rests on, pinned where it belongs.

    `_seq_family` splits a built stem into (prefix, digits, suffix) by reading
    `emitted` and re-joining around the `seq` token - so if the stem could ever differ
    from its own emitted values joined by '_', the seed would describe a family the
    engine does not emit and A7 could skip a free number, which is a changed filename
    on an irreplaceable file.

    That module carries a round-trip check as a tripwire, and it CANNOT FIRE: both of
    the things a reader expects to break this - rule 4's reserved-stem rewrite and
    `_finalize`'s edge-stripping - edit a part BEFORE it is emitted, so `emitted`
    always describes the stem that was actually built. An assumption that no test
    states is one a future template change can falsify silently, so it is stated here,
    over exactly the cases that look like counterexamples and are not: the reserved
    rewrite alone, the rewrite plus a truncation, a budget cut, an alias, and a token
    the sanitizer erases entirely.
    """
    hard = [
        (("orig",), {"orig": "con"}, "2019"),  # rule 4 lengthens a value
        (("orig", "seq"), {"orig": "con.backup", "seq": "001"}, "2019"),  # in place
        (
            ("orig", "seq"),  # rewrite AND truncation on one stem
            {"orig": "con.backup", "seq": "001"},
            rel_dir_for_budget(15),
        ),
        (BUDGET_ORDER, BUDGET_VALUES, rel_dir_for_budget(8)),  # a budget cut
        (DEFAULT, {"date": "2019-07-04", "event": "Fireworks"}, "2019"),  # an alias
        (("date", "seq"), {"date": "2019-07-04", "seq": "001"}, "2019"),  # the family
    ]
    for order, values, rel_dir in hard:
        r = build(order, values, rel_dir=rel_dir)
        assert r.emitted, (order, values)
        assert r.stem == "_".join(token.value for token in r.emitted), (order, values)

    # THE ONE EXCEPTION, and it is the reason the invariant is stated as "when anything
    # was emitted". A stem whose every token sanitizes away is not emitted as the empty
    # string - spec 6.2's legality postcondition forbids that - so `build_stem`
    # substitutes `unnamed`, a value no token supplied. `emitted` is then EMPTY and the
    # join is "", which is not the stem.
    #
    # Harmless to `_seq_family` and worth knowing exactly why: with no emitted tokens
    # there is no `seq` token either, so the family is refused on its first operand and
    # the caller steps by one. The exception is safe because it takes the invariant's
    # consumer out of play entirely, NOT because the invariant survives it.
    erased = build(("scene",), {"scene": "🎉"})
    assert erased.emitted == ()
    assert erased.stem == "unnamed"
    assert erased.stem != "_".join(token.value for token in erased.emitted)


def test_a_multi_token_reserved_head_with_no_room_shortens_the_last_token():
    # The same branch with the budget one character too tight for '-x': the LAST
    # token is shortened until the -x fits. That is genuinely both a rewrite and a
    # truncation, and the shortened token must carry both flags.
    r = build(
        ("orig", "seq"), {"orig": "con.backup", "seq": "001"}, rel_dir=rel_dir_for_budget(15)
    )
    assert r.stem == "con-x.backup_00"
    assert len(r.stem) == 15  # the -x fits inside the budget, it does not bust it
    assert [(t.name, t.value, t.complete, t.rewritten) for t in r.emitted] == [
        ("orig", "con-x.backup", True, True),
        ("seq", "00", False, True),
    ]


def test_longest_companion_extension_sets_the_budget():
    # C4: group members share a stem across differing extension lengths, so a
    # stem sized for .jpg would bust the budget for its .jpeg sibling.
    rel_dir = rel_dir_for_budget(14, ext="jpeg")
    values = {"date": "2019-07-04", "seq": "001"}
    assert (
        build(("date", "seq"), values, rel_dir=rel_dir, exts=("jpg", "jpeg")).stem
        == "2019-07-04_001"
    )


def test_extension_dot_is_optional():
    # C4: Path.suffix returns '.jpg' with the dot; the formula already reserves
    # a character for it, so both spellings must give the same budget.
    values = {"date": "2019-07-04"}
    rel_dir = rel_dir_for_budget(10)
    assert build(("date",), values, rel_dir=rel_dir, exts=(".jpg",)) == build(
        ("date",), values, rel_dir=rel_dir, exts=("jpg",)
    )


@pytest.mark.parametrize("budget", list(range(-2, 40)))
def test_postcondition_holds_across_the_budget_range(budget):
    # C1 as a property: whatever the budget, the stem is a legal filename.
    for values in (
        {"date": "2019-07-04", "scene": "Console", "seq": "001"},
        {"orig": "con"},
        {"scene": "🎉"},
        {"orig": "nul.backup"},
    ):
        order = tuple(k for k in ("date", "scene", "seq", "orig") if k in values)
        r = build(order, values, rel_dir=rel_dir_for_budget(budget))
        assert r.stem, f"empty stem at budget {budget}"
        assert reserved_head(r.stem) is None
        assert r.stem == r.stem.strip("-_.")
        if r.emitted:
            assert r.stem == "_".join(t.value for t in r.emitted)


# --- M5 open question 10: `_finalize`'s loop is bounded, and the bound is a tripwire ---


def _finalize_iterations(monkeypatch, order, values, **kwargs):
    """Count `_finalize`'s passes. `reserved_head` is called exactly once per pass
    (after the empty-parts early return), so counting its calls counts iterations
    without production code having to keep a counter for the tests' benefit."""
    calls = []
    real = template.reserved_head

    def counting(stem):
        calls.append(stem)
        return real(stem)

    monkeypatch.setattr(template, "reserved_head", counting)
    result = build(order, values, **kwargs)
    return result, len(calls)


def test_the_finalize_loop_does_not_churn(monkeypatch):
    """The bound must be unreachable on real input, so it can only ever report a bug.

    Asserted on the worst case the suite has - a reserved head with no room for
    '-x', which is the only input that reaches the shortening branch at all. Pinned
    as an exact count so a future change that makes the loop churn is visible here
    rather than only as a slow suite.
    """
    result, iterations = _finalize_iterations(
        monkeypatch,
        ("orig", "seq"),
        {"orig": "con.backup", "seq": "001"},
        rel_dir=rel_dir_for_budget(15),
    )
    assert result.stem == "con-x.backup_00"
    assert iterations == 3  # shorten, then rewrite with '-x', then accept
    # 3 passes against a bound of len(parts) + max(budget, 1) + 8 == 2 + 15 + 8.
    assert iterations < 2 + 15 + 8


def test_every_stem_the_suite_builds_stays_far_inside_the_bound(monkeypatch):
    """The same claim across the whole budget range, not just one representative."""
    for budget in range(-2, 40):
        for values in (
            {"date": "2019-07-04", "scene": "Console", "seq": "001"},
            {"orig": "con"},
            {"orig": "nul.backup"},
        ):
            order = tuple(k for k in ("date", "scene", "seq", "orig") if k in values)
            _, iterations = _finalize_iterations(
                monkeypatch, order, values, rel_dir=rel_dir_for_budget(budget)
            )
            assert iterations <= 4, f"budget {budget} values {values}: {iterations} passes"


def test_an_exhausted_finalize_raises_instead_of_hanging(monkeypatch):
    """The mutant that spun: make the shortening step a no-op and the loop can
    never make progress. Before the bound this hung forever with no output; now it
    raises, names the stem and names the budget."""
    monkeypatch.setattr(template, "_shorten_last", lambda parts: None)
    with pytest.raises(TemplateError) as excinfo:
        build(
            ("orig", "seq"),
            {"orig": "con.backup", "seq": "001"},
            rel_dir=rel_dir_for_budget(15),
        )
    message = str(excinfo.value)
    assert "con" in message  # the stem it could not settle
    assert "path budget 15" in message
    assert "bug in pam" in message


def test_the_exhaustion_error_reaches_spec_13s_cli_contract():
    """It must be a PamError, or `cli.main` prints a traceback and exits 2 where
    spec 13 asks for one clear line and exit 1. TemplateError is the engine's own
    type and is already a PamError, so the specific type costs nothing."""
    assert issubclass(TemplateError, PamError)
