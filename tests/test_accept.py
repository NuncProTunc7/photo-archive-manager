"""`pam/naming/accept.py` - what "accept all" selects (F1 R2/R4, M13-D1), as a literal table.

EVERY EXPECTATION IS SPELLED BY HAND from the maintainer's ruling
(`docs/superpowers/specs/2026-10-08-ht-m12-f1-approval-amendment.md`) and M13-D1, never computed
from the function under test or from any constant in it:

  - only `proposed` items are candidates; a skip stays a skip;
  - a unit is accepted whole or not at all;
  - a unit holding any `low` member is held back whole, and its members are COUNTED (items);
  - a unit with any member not `proposed` is left exactly as it is, and is NOT counted.
"""

from typing import NamedTuple

import pytest

from pam.naming.accept import accept_all, exclude_low_units


class Row(NamedTuple):
    id: int
    unit_key: str
    tier: str
    status: str


# (name, rows, expected accepted ids, expected excluded_low) - spelled by hand.
ACCEPT_TABLE = [
    (
        "all proposed high units are accepted",
        [Row(1, "u1", "high", "proposed"), Row(2, "u1", "high", "proposed")],
        {1, 2},
        0,
    ),
    (
        "a unit with one low member is excluded whole and counted by its size",
        [
            Row(1, "u1", "high", "proposed"),
            Row(2, "u1", "low", "proposed"),
            Row(3, "u1", "high", "proposed"),
        ],
        set(),
        3,
    ),
    (
        "a unit with one skipped member is untouched and not counted",
        [Row(1, "u1", "high", "proposed"), Row(2, "u1", "high", "skipped")],
        set(),
        0,
    ),
    (
        "a unit one approved plus one proposed is untouched",
        [Row(1, "u1", "high", "approved"), Row(2, "u1", "high", "proposed")],
        set(),
        0,
    ),
    (
        "a lone low item is excluded and counted once",
        [Row(7, "u7", "low", "proposed")],
        set(),
        1,
    ),
    ("an empty plan accepts nothing", [], set(), 0),
    (
        "two units of the same tier are both accepted",
        [
            Row(1, "u1", "medium", "proposed"),
            Row(2, "u2", "medium", "proposed"),
            Row(3, "u2", "medium", "proposed"),
        ],
        {1, 2, 3},
        0,
    ),
    (
        "a medium unit is accepted",
        [Row(4, "u4", "medium", "proposed"), Row(5, "u4", "medium", "proposed")],
        {4, 5},
        0,
    ),
    (
        "a mixed plan: accepted, low-held, skipped-held and approved-held units side by side",
        [
            Row(1, "a", "high", "proposed"),
            Row(2, "b", "low", "proposed"),
            Row(3, "b", "high", "proposed"),
            Row(4, "c", "high", "skipped"),
            Row(5, "c", "high", "proposed"),
            Row(6, "d", "medium", "approved"),
            Row(7, "e", "medium", "proposed"),
        ],
        {1, 7},
        2,
    ),
    (
        "a low unit whose other member is skipped is decided, so it is NOT counted as low",
        [Row(1, "u1", "low", "proposed"), Row(2, "u1", "low", "skipped")],
        set(),
        0,
    ),
]

# Asserted at COLLECTION: pytest SKIPS an empty parametrize rather than failing it (M743).
assert len(ACCEPT_TABLE) >= 8, (
    "the accept_all table would be parametrized over (almost) nothing"
)


@pytest.mark.parametrize(
    ("rows", "accepted", "excluded_low"),
    [(rows, accepted, low) for _, rows, accepted, low in ACCEPT_TABLE],
    ids=[name for name, *_ in ACCEPT_TABLE],
)
def test_accept_all_selects_exactly_what_the_ruling_says(rows, accepted, excluded_low):
    result = accept_all(rows)
    assert result.ids == frozenset(accepted)
    assert result.excluded_low == excluded_low


def test_the_table_really_reaches_the_parametrized_test():
    """A wiring check (M12 Task 9 F2): the list being intact proves nothing if the parametrize
    no longer reads it."""
    marks = [
        m
        for m in test_accept_all_selects_exactly_what_the_ruling_says.pytestmark
        if m.name == "parametrize"
    ]
    assert len(marks) == 1
    assert len(marks[0].args[1]) == len(ACCEPT_TABLE)


def test_accept_all_takes_a_one_shot_iterable():
    """The CLI passes a tuple and the page will pass a list; a generator must work too."""
    result = accept_all(r for r in [Row(1, "u1", "high", "proposed")])
    assert result.ids == frozenset({1})


def test_exclude_low_units_keeps_order_and_counts_items_dropped():
    """decide-filter's unit-scoped exclusion, spelled by hand: a low member takes its unit."""
    rows = [
        Row(1, "a", "high", "proposed"),
        Row(2, "b", "high", "proposed"),
        Row(3, "b", "low", "proposed"),
        Row(4, "c", "medium", "skipped"),
    ]
    kept, dropped = exclude_low_units(rows, [4, 2, 1, 3])
    assert kept == [4, 1]
    assert dropped == 2


def test_exclude_low_units_with_no_low_unit_drops_nothing():
    rows = [Row(1, "a", "high", "proposed"), Row(2, "a", "medium", "proposed")]
    assert exclude_low_units(rows, [2, 1]) == ([2, 1], 0)


# --- M14 OD-1: `also_hold`, and a default that changes nothing ---------------------------


class LookRow(NamedTuple):
    """A row that ALSO carries `attention`, as the review page's `ReviewItem` does."""

    id: int
    unit_key: str
    tier: str
    status: str
    attention: bool


# What M13 selects, spelled from the F1 ruling (R4: "every undecided name except low-confidence
# ones"): a name that needs a look but is NOT low - OCR-dated, truncated, undated-but-medium -
# is accepted. Rows built so a default that read `attention` would hold units 1 and 3.
LOOK_ROWS = [
    LookRow(1, "u1", "medium", "proposed", True),
    LookRow(2, "u1", "medium", "proposed", False),
    LookRow(3, "u3", "high", "proposed", True),
    LookRow(4, "u4", "high", "proposed", False),
    LookRow(5, "u5", "low", "proposed", True),
]


def test_the_default_holds_nothing_but_low_exactly_as_m13_ruled():
    """T3 mutation 10 (the `also_hold` edit changes the default, so `--all` and M13's
    `Accept all` would now hold the needs-a-look set): spelled from M13, only Low is held."""
    result = accept_all(LOOK_ROWS)
    assert result.ids == frozenset({1, 2, 3, 4})
    assert (result.excluded_low, result.held) == (1, 0)
    assert accept_all(LOOK_ROWS, also_hold=None) == result


def test_also_hold_holds_a_unit_whole_when_any_member_needs_a_look():
    """OD-1: a unit is held when ANY member satisfies `also_hold`, and held whole. A held
    unit that is also low counts in `excluded_low` only; the rest count in `held` (items)."""
    result = accept_all(LOOK_ROWS, also_hold=lambda r: r.attention)
    assert result.ids == frozenset({4})
    assert (result.excluded_low, result.held) == (1, 3)


def test_also_hold_never_reaches_a_decided_unit():
    rows = [
        LookRow(1, "u1", "high", "skipped", True),
        LookRow(2, "u1", "high", "proposed", True),
    ]
    assert accept_all(rows, also_hold=lambda r: r.attention) == accept_all(rows)
    assert accept_all(rows).held == 0
