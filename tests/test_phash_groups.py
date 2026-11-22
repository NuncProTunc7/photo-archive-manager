"""`near_dupe_groups`' exact pigeonhole search (amendment H2, M13 Task 9 Step 1).

H2: "any faster search must return the same groups" as the all-pairs definition. Three kinds
of evidence, none of them a clock:

* a LITERAL table, hand-built from design 8.1's definition (Hamming distance <= the threshold,
  transitive) - the only thing that stands behind a sentence (CLAUDE.md, M922/M924);
* a PROPERTY run against `_near_dupe_groups_all_pairs`, the independent second implementation,
  over 300 seeded sets with planted near pairs, chains and just-too-far pairs;
* a COUNT of `hamming_distance` calls on 5,000 uniform hashes - the search really is
  sub-quadratic, by arithmetic rather than by timing.
"""

import random

import pytest

import pam.enrich.phash as phash
from pam.enrich.phash import HASH_BITS, _near_dupe_groups_all_pairs, near_dupe_groups


def _hex(value: int) -> str:
    return f"{value:016x}"


ZERO = _hex(0)

# (name, hashes, max_distance, expected) - every expectation spelled from the definition.
LITERAL_TABLE = [
    ("empty input has no groups", {}, 8, []),
    (
        "a transitive chain A-B-C with A-C beyond the distance is ONE group",
        {1: ZERO, 2: _hex(0b11), 3: _hex(0b1111)},
        2,
        [frozenset({1, 2, 3})],
    ),
    (
        "two hashes exactly max_distance apart are grouped",
        {1: ZERO, 2: _hex(0xFF)},
        8,
        [frozenset({1, 2})],
    ),
    ("two hashes max_distance + 1 apart are not", {1: ZERO, 2: _hex(0x1FF)}, 8, []),
    (
        "distance 0 groups identical hashes and nothing one bit away",
        {1: ZERO, 2: ZERO, 3: _hex(1)},
        0,
        [frozenset({1, 2})],
    ),
    (
        "two groups, ordered by smallest id, ids not in insertion order",
        {4: _hex(2**64 - 2), 3: _hex(1), 2: ZERO, 1: _hex(2**64 - 1)},
        1,
        [frozenset({1, 4}), frozenset({2, 3})],
    ),
    (
        "the two differing bits at opposite ends of the hash are still found",
        {1: ZERO, 2: _hex((1 << 63) | 1)},
        2,
        [frozenset({1, 2})],
    ),
]
assert len(LITERAL_TABLE) >= 6


@pytest.mark.parametrize(
    ("hashes", "max_distance", "expected"),
    [case[1:] for case in LITERAL_TABLE],
    ids=[case[0] for case in LITERAL_TABLE],
)
def test_the_literal_table(hashes, max_distance, expected):
    assert near_dupe_groups(hashes, max_distance=max_distance) == expected


def test_the_literal_table_also_holds_for_the_definition():
    """The oracle is checked against the spec too, so the two cannot drift onto one reading."""
    assert LITERAL_TABLE
    for name, hashes, max_distance, expected in LITERAL_TABLE:
        assert _near_dupe_groups_all_pairs(hashes, max_distance=max_distance) == expected, name


# The property run's seeds: 300, written out by count, asserted at collection (an empty list
# would make the loop below vacuously green - M743's shape).
PROPERTY_SEEDS = tuple(range(300))
assert len(PROPERTY_SEEDS) == 300


def _flip(value: int, rng: random.Random, bits: int) -> int:
    for position in rng.sample(range(HASH_BITS), bits):
        value ^= 1 << position
    return value


def _seeded_hashes(seed: int) -> tuple[dict[int, str], int]:
    """Uniform hashes plus planted near pairs, chains and just-too-far pairs."""
    rng = random.Random(seed)
    size = rng.randint(0, 400)
    max_distance = rng.randint(0, 12)
    values: list[int] = []
    while len(values) < size:
        roll = rng.random()
        if values and roll < 0.25:  # a near pair: 0..max_distance flips, the bound included
            flips = rng.choice([max_distance, rng.randint(0, max_distance)])
            values.append(_flip(rng.choice(values), rng, flips))
        elif values and roll < 0.35:  # a chain link off the newest hash
            values.append(_flip(values[-1], rng, rng.randint(0, max_distance)))
        elif values and roll < 0.45:  # just too far
            values.append(_flip(rng.choice(values), rng, min(max_distance + 1, HASH_BITS)))
        else:
            values.append(rng.getrandbits(HASH_BITS))
    ids = rng.sample(range(10 * size + 1), size)  # ids that are not 0..n-1 in order
    return {node: _hex(value) for node, value in zip(ids, values, strict=True)}, max_distance


def test_the_search_equals_the_all_pairs_definition_on_300_seeded_sets():
    assert PROPERTY_SEEDS, "no seeds: this test would compare nothing"
    grouped_sets = 0
    for seed in PROPERTY_SEEDS:
        hashes, max_distance = _seeded_hashes(seed)
        expected = _near_dupe_groups_all_pairs(hashes, max_distance=max_distance)
        assert near_dupe_groups(hashes, max_distance=max_distance) == expected, (
            f"seed {seed}: {len(hashes)} hashes at distance {max_distance}"
        )
        grouped_sets += bool(expected)
    # Not vacuous: the planted pairs really produced groups in most sets.
    assert grouped_sets > 200


def test_at_hash_bits_every_pair_qualifies_and_one_below_the_opposite_pair_does_not():
    hashes = {1: ZERO, 2: _hex(2**64 - 1)}  # 64 bits apart
    assert near_dupe_groups(hashes, max_distance=HASH_BITS) == [frozenset({1, 2})]
    assert near_dupe_groups(hashes, max_distance=HASH_BITS + 5) == [frozenset({1, 2})]
    assert near_dupe_groups(hashes, max_distance=HASH_BITS - 1) == []


def test_a_digest_of_the_wrong_width_is_refused_as_the_definition_refuses_it():
    with pytest.raises(ValueError, match="widths differ"):
        near_dupe_groups({1: ZERO, 2: "0" * 15}, max_distance=8)


SCALE_N = 5000
SCALE_DISTANCE = 8  # design 11's default


def test_the_search_compares_under_15_percent_of_all_pairs_at_5000_hashes(monkeypatch):
    """Not a clock: the comparisons themselves, counted. Nine bands of ~7 bits make ~6.6 %
    of uniform pairs candidates (H2's arithmetic); 15 % is the bound the plan states."""
    rng = random.Random(9)
    hashes = {node: _hex(rng.getrandbits(HASH_BITS)) for node in range(SCALE_N)}
    calls = []
    real = phash.hamming_distance

    def counting(left, right):
        calls.append(1)
        return real(left, right)

    monkeypatch.setattr(phash, "hamming_distance", counting)
    near_dupe_groups(hashes, max_distance=SCALE_DISTANCE)
    all_pairs = SCALE_N * (SCALE_N - 1) // 2
    assert calls, "the spy saw no comparison - it is not wired to the search"
    assert len(calls) < 0.15 * all_pairs, f"{len(calls)} of {all_pairs} pairs compared"


# --- Task 9 review: closing tests ---------------------------------------------------------


def test_a_digest_of_the_wrong_width_sharing_no_band_is_still_refused():
    """M2508: the existing width test's digests are both zero, so they share every band and
    `hamming_distance` raises either way. One that shares NO band with the other is refused
    only by the width fallback."""
    with pytest.raises(ValueError, match="widths differ"):
        near_dupe_groups({1: ZERO, 2: "f" * 15}, max_distance=8)


def test_groups_are_ordered_by_smallest_id_where_the_first_merge_is_not_the_smallest():
    """M2510: 1-4 and 3-4 are one bit apart, 1-3 two: a union that keeps the LATER root
    makes 3 the root of {1, 3, 4}, and sorting by root then puts {2, 5} first. Spelled from
    the docstring's "ordered by each group's smallest id", for BOTH implementations."""
    hashes = {
        1: _hex(0b00),
        2: _hex(0xFFFF << 48),
        3: _hex(0b11),
        4: _hex(0b01),
        5: _hex((0xFFFF << 48) | 1),
    }
    expected = [frozenset({1, 3, 4}), frozenset({2, 5})]
    assert near_dupe_groups(hashes, max_distance=1) == expected
    assert _near_dupe_groups_all_pairs(hashes, max_distance=1) == expected
