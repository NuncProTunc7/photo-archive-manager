"""Design spec 6.2 rule 7's copy and its precedence (`pam/webui/disclosure.py`).

THE THREE SENTENCES ARE SPELLED OUT HERE FROM AMENDMENT A5, NOT IMPORTED. A test that
asserted `RESERVED_NOTE.format(...) in reply` would pass for any value of
`RESERVED_NOTE`, which is the vacuous-oracle shape this project has now measured six
times in M8 alone (`repo.ENRICHERS`, M743, M769/M776). The separator is A5's own em
dash: an earlier pass shipped an ASCII hyphen here and it was overturned in the Task 6
review - see `disclosure.py`'s constant block for why the output channel, not the source
language, is what binds a user-facing string.

The end-to-end half of rule 7 - a real POST to a real server, with an archive of real files under
it - lives in `test_webui_decide.py` beside the other rename tests, because it needs
that module's plan fixture. This file owns the copy and the choice between the three.
"""

import itertools

import pytest

from pam.webui import disclosure as mod

# Spec 6.2 rule 7 / amendment A5, transcribed. `{stem}` marks where the stored stem goes.
SPEC_RESERVED = 'pam adjusted that to "{stem}" — Windows reserves that name.'
SPEC_ILLEGAL = (
    'pam adjusted that to "{stem}" — names use letters, numbers, dots, dashes and underscores.'
)
SPEC_SHORTENED = 'pam shortened that to "{stem}" to fit the Windows path limit.'

# The precedence this module chose, written as a rule rather than as a table of outputs:
# reserved beats shortened beats the allowlist. Independently spelled from
# `disclosure()`'s docstring so that reordering the production `if`s fails here.
PRECEDENCE = ("reserved", "shortened", "illegal")
SPEC_SENTENCE = {
    "reserved": SPEC_RESERVED,
    "shortened": SPEC_SHORTENED,
    "illegal": SPEC_ILLEGAL,
}

# Every combination of the three flags: 8 rows, built here rather than taken from any
# production symbol. Asserted non-empty at collection - pytest SKIPS an empty
# parametrize rather than failing it, and a control that vanishes silently is M743.
FLAG_SETS = [
    dict(zip(PRECEDENCE, bits, strict=True))
    for bits in itertools.product((False, True), repeat=3)
]
assert len(FLAG_SETS) == 8, "the flag table must cover all three flags"


def test_the_three_sentences_are_the_spec_s():
    """The copy is normative: a reworded sentence is a spec deviation, not a typo."""
    assert mod.RESERVED_NOTE == SPEC_RESERVED
    assert mod.ILLEGAL_NOTE == SPEC_ILLEGAL
    assert mod.SHORTENED_NOTE == SPEC_SHORTENED


def test_the_two_sentences_that_take_a_separator_carry_the_SPEC_S_EM_DASH():
    """A5's typography, pinned - and this test used to assert the OPPOSITE.

    M8 Task 6 shipped an ASCII hyphen here on the argument that "every user-facing string
    in `pam/**/*.py` is ASCII". The measurement was true and the set was wrong: what binds
    a string is its OUTPUT CHANNEL, not the language it is composed in. Rule 7 renders only
    in a browser, beside `app.js`'s own em dashes and directly under the Why? panel's
    `pam proposed ...` line. Two different dashes for one clause on one screen was the
    defect the hyphen created.

    Asserted over the whole RENDERED sentence, not over the constant, because a separator
    could equally arrive through `format` - and `SHORTENED_NOTE` is excluded by name
    rather than by a loop that would quietly pass if it lost its dash, because A5 writes
    that one as a single clause with no separator at all.
    """
    for template in (mod.RESERVED_NOTE, mod.ILLEGAL_NOTE):
        rendered = template.format(stem="x")
        assert "—" in rendered, rendered
        assert " - " not in rendered, rendered
    assert "—" not in mod.SHORTENED_NOTE.format(stem="x")


@pytest.mark.parametrize("flags", FLAG_SETS)
def test_the_sentence_is_the_highest_precedence_rule_that_fired(flags):
    """One sentence, and it is the FIRST of reserved/shortened/illegal that is true.

    The expectation is computed from `PRECEDENCE` and `SPEC_SENTENCE` above - a second
    implementation of the choice - never from `mod.disclosure`.
    """
    fired = [rule for rule in PRECEDENCE if flags[rule]]
    expected = SPEC_SENTENCE[fired[0]].format(stem="CON-x") if fired else ""
    assert mod.disclosure("CON-x", **flags) == expected


def test_no_rule_fired_means_no_sentence():
    """Rule 7 speaks only when the stored name differs from the one asked for."""
    assert mod.disclosure("Zoo-trip", reserved=False, shortened=False, illegal=False) == ""


@pytest.mark.parametrize("rule", PRECEDENCE)
def test_each_rule_alone_quotes_the_stored_stem(rule):
    """The stem in the sentence is what pam STORED - the user is looking at what they
    typed, in the field the sentence sits under, so the sentence supplies the other half.
    """
    flags = dict.fromkeys(PRECEDENCE, False)
    flags[rule] = True
    assert mod.disclosure("2026-07-25_Zoo-trip", **flags) == SPEC_SENTENCE[rule].format(
        stem="2026-07-25_Zoo-trip"
    )


# --- rule 7's OTHER sentence: a decision that moved more files than it named ----------
#
# Spelled from amendment A1 / spec 8.2's three action names, not imported from
# `disclosure`. Until this block existed `decided_unit` had NO direct test at all - it
# was reached only through a live POST in `test_webui_decide.py`, where every unit has
# exactly two members, so the PLURAL branch was unreachable and deleting it left the
# whole suite green (M1055). A pure function is the cheapest place to build the case the
# archive fixture cannot.
SPEC_DECIDED_ONE = (
    "pam also {verb} 1 companion file — pam renames a group together or not at all."
)
SPEC_DECIDED_MANY = (
    "pam also {verb} {count} companion files — pam renames a group together or not at all."
)
# Spec 8.2's three actions, and the words UX 5 rev 8 puts on their controls. `reset` is
# not a verb a person reads, which is why it is not simply the column value.
SPEC_VERB = {
    "approve": "approved",
    "skip": "skipped",
    "reset": "returned to undecided",
}

assert SPEC_VERB, "the decided-unit control would be parametrized over nothing"


@pytest.mark.parametrize("action", sorted(SPEC_VERB))
def test_one_companion_is_singular_and_more_than_one_is_plural(action):
    """The plural branch, which no live fixture in this project can reach.

    Every companion unit the planner builds in the test archives holds exactly two
    members, so `companions` is always 1 on the wire and `s` is always "". A three-member
    unit is ordinary in an archive of real files - RAW + JPEG + XMP - and a user who bulk-approves
    one would read "pam also approved 2 companion file".
    """
    verb = SPEC_VERB[action]
    assert mod.decided_unit(action, 1) == SPEC_DECIDED_ONE.format(verb=verb)
    assert mod.decided_unit(action, 2) == SPEC_DECIDED_MANY.format(verb=verb, count=2)
    assert mod.decided_unit(action, 7) == SPEC_DECIDED_MANY.format(verb=verb, count=7)


@pytest.mark.parametrize("action", sorted(SPEC_VERB))
def test_a_decision_that_moved_only_what_it_named_says_nothing(action):
    """Rule 7 speaks only when pam did something the user did not ask for by name."""
    assert mod.decided_unit(action, 0) == ""


@pytest.mark.parametrize("action", ["", "Approve", "delete", "applied", None])
def test_an_action_outside_spec_8_2s_three_produces_no_sentence_rather_than_a_wrong_one(
    action,
):
    """`_action` refuses these with a 400 before any row is read, so this is unreachable
    through the endpoint. It is asserted anyway because the failure mode is silent: a
    decision reported with the WRONG verb is worse than one reported with none.
    """
    assert mod.decided_unit(action, 3) == ""


def test_the_verbs_this_module_offers_are_exactly_spec_8_2s_three():
    """The map is a partition of spec 8.2's action vocabulary, spelled out above.

    A fourth verb arriving without a decision about its copy, or one of the three
    quietly disappearing, is red here rather than an empty sentence on the page.
    """
    assert mod.DECIDED_VERB == SPEC_VERB
