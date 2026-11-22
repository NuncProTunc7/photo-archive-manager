"""`pam/enrich/cluster.py`: DBSCAN over face embeddings (M11 Task 9, Steps 1, 2 and 5).

THE LITERAL TABLE IS THE ONLY THING STANDING BEHIND THE SENTENCE, and this module is
built around that. M8's `M922`/`M924` measured the alternative: co-mutating production and
an independently written second implementation IDENTICALLY left all twenty-four property
rows green, because two implementations that pin each other prove they AGREE and never
that either is RIGHT. So there are two instruments here and they are not interchangeable:

  * `EXACT_PAIRS` and the tests that read it are HAND-COMPUTED from the definition of
    cosine distance. Not one number in that table was produced by running `cluster.dbscan`
    or by running `reference_dbscan` below. They are arithmetic somebody can check on
    paper, and they are what a co-mutation cannot survive.
  * `reference_dbscan` is a second implementation written from the ALGORITHM's definition
    - textbook DBSCAN, pure Python, no numpy, no blocking, no cluster numbering scheme in
    common with production. It catches ASYMMETRIC drift over inputs nobody hand-computed,
    which is a real thing the table cannot do, and it is kept for that and only that.

HOW THE HAND-COMPUTED DISTANCES ARE EXACT IN FLOAT32, WHICH IS WHY THE BOUNDARY CASE CAN
BE ASSERTED AT ALL. A vector of `DIM = 16` components, each `+0.25` or `-0.25`, has L2
norm `sqrt(16 * 0.0625) = 1` EXACTLY - 0.25 and 0.0625 are both powers of two, so no
rounding occurs anywhere. Normalising divides by exactly 1.0 and changes nothing. The dot
product of two such vectors differing in `k` of their 16 signs is

    (16 - k) * 0.0625  -  k * 0.0625  =  1 - 0.125 * k

so the cosine distance is `0.125 * k`, exactly, for k = 0..16: 0, 0.125, 0.25, 0.375, 0.5,
and so on. Every number in `EXACT_PAIRS` below is that formula evaluated by hand. This is
what lets "a face EXACTLY at epsilon" be a real test rather than an approximate one - the
usual construction (a unit vector at some angle) cannot represent its own distance exactly
in binary floating point, so `<=` and `<` become indistinguishable and the mutation the
plan names for Step 7 would be a false survivor.
"""

import itertools
import math
import random
import struct
from pathlib import Path

import pytest

from pam.enrich import cluster

# The width of the hand-computed vectors. NOT `cluster.DEFAULT_DIM` - these vectors exist
# to make the arithmetic exact and checkable, and 128 of them would be neither.
DIM = 16

# The magnitude of every component. `1 / sqrt(DIM)`, spelled as the exact power of two it
# happens to be, so the reader can see that the norm is exactly 1 without trusting a call.
STEP = 0.25

# The cosine distance between two `signs` vectors that differ in exactly one position.
# `2 * STEP * STEP = 0.125`: flipping one sign moves the dot product by twice that
# component's contribution. Written out because it is the unit every expectation below is
# a multiple of.
PER_FLIP = 0.125


def signs(*flipped: int) -> tuple[float, ...]:
    """A `DIM`-component unit vector with `-STEP` at the given positions and `+STEP` elsewhere.

    Its L2 norm is exactly 1 (see the module docstring), so `cluster.dbscan`'s
    normalisation is a division by 1.0 and the distances below are the distances the
    algorithm sees, not approximations of them.
    """
    return tuple(-STEP if index in flipped else STEP for index in range(DIM))


def hand_distance(flips: int) -> float:
    """`0.125 * k` - the module docstring's formula, written once so it can be read."""
    return PER_FLIP * flips


# ---------------------------------------------------------------------------
# 1. THE LITERAL TABLE. Hand-computed; nothing here ran either implementation.
# ---------------------------------------------------------------------------

# `(name, a, b, flips, distance)`. `distance` is `0.125 * flips` written out as a literal
# rather than computed, so that a wrong `PER_FLIP` cannot make the table agree with itself.
EXACT_PAIRS = (
    ("identical", signs(), signs(), 0, 0.0),
    ("one flip", signs(), signs(0), 1, 0.125),
    ("two flips", signs(), signs(0, 1), 2, 0.25),
    ("three flips", signs(), signs(0, 1, 2), 3, 0.375),
    ("four flips", signs(), signs(0, 1, 2, 3), 4, 0.5),
    ("six flips", signs(), signs(0, 1, 2, 3, 4, 5), 6, 0.75),
    ("eight flips (orthogonal)", signs(), signs(0, 1, 2, 3, 4, 5, 6, 7), 8, 1.0),
)

assert EXACT_PAIRS, "the literal distance table is empty; every expectation below is vacuous"


def cosine_distance(a, b):
    """`1 - dot(a_hat, b_hat)`, from the DEFINITION, in pure Python.

    Written out rather than imported, because this is the oracle for the table and an
    oracle that called `cluster` would be the thing this project's notes call "an oracle
    built from the definition it checks".
    """
    norm_a = math.sqrt(sum(value * value for value in a))
    norm_b = math.sqrt(sum(value * value for value in b))
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return 1.0 - dot / (norm_a * norm_b)


@pytest.mark.parametrize(("name", "a", "b", "flips", "expected"), EXACT_PAIRS)
def test_the_hand_computed_distances_are_what_the_definition_gives(name, a, b, flips, expected):
    """The table checked against the DEFINITION of cosine distance, not against pam.

    This is the row that makes every other assertion in this module mean something: if
    `0.125 * k` were the wrong formula, every epsilon chosen below would be chosen against
    a fiction. Computed in Python floats from `sum(x*y)`, which shares no code with
    `cluster.dbscan`'s numpy matmul.
    """
    assert cosine_distance(a, b) == pytest.approx(expected, abs=1e-12)
    assert hand_distance(flips) == expected


def test_the_hand_computed_vectors_are_exactly_unit_length():
    """The premise the exactness of the table rests on, asserted rather than asserted-to.

    `==`, not `approx`: the point of `STEP = 0.25` at `DIM = 16` is that the norm is
    exactly representable, and an `approx` here would let a later widening of `DIM` to a
    non-square silently reintroduce the rounding this construction exists to avoid.
    """
    for _, a, b, _, _ in EXACT_PAIRS:
        assert sum(value * value for value in a) == 1.0
        assert sum(value * value for value in b) == 1.0


# ---------------------------------------------------------------------------
# 2. THE INDEPENDENT SECOND IMPLEMENTATION - textbook DBSCAN, no numpy.
# ---------------------------------------------------------------------------


def reference_dbscan(embeddings, *, epsilon, min_samples):
    """DBSCAN from its definition. Shares no line of code with `pam/enrich/cluster.py`.

    Deliberately the NAIVE shape: a full neighbour table computed up front, recursion
    through a work list, and `cosine_distance` above rather than a matmul. It has no
    blocking, no two-pass core detection and no `numpy`. Where production's cluster
    NUMBERS come from a visit order, this one's come from the same visit order by
    coincidence of both being sorted - which is why every comparison against it in this
    module is over a PARTITION (a set of frozensets) and never over the numbers.
    """
    ids = sorted(embeddings)
    neighbours = {
        i: [j for j in ids if cosine_distance(embeddings[i], embeddings[j]) <= epsilon]
        for i in ids
    }
    core = {i for i in ids if len(neighbours[i]) >= min_samples}
    labels: dict[int, int | None] = {i: None for i in ids}
    cluster_number = 0
    for seed in ids:
        if seed not in core or labels[seed] is not None:
            continue
        cluster_number += 1
        labels[seed] = cluster_number
        pending = [seed]
        while pending:
            current = pending.pop(0)
            for other in neighbours[current]:
                if labels[other] is not None:
                    continue
                labels[other] = cluster_number
                if other in core:
                    pending.append(other)
    return labels


def partition(labels):
    """A labelling as a SET OF FROZENSETS of ids, noise dropped.

    Ids may be renumbered; the partition may not. An assertion on the numbers passes for
    a renumbering and fails for nothing, which is the shape M11's Step 6 names by hand.
    """
    groups: dict[int, set[int]] = {}
    for face_id, number in labels.items():
        if number is not None:
            groups.setdefault(number, set()).add(face_id)
    return {frozenset(members) for members in groups.values()}


def noise(labels):
    return {face_id for face_id, number in labels.items() if number is None}


# ---------------------------------------------------------------------------
# 3. THE FOUR SHAPES THE PLAN NAMES BY HAND (Step 2).
# ---------------------------------------------------------------------------


def test_two_clusters_and_a_singleton():
    """The base shape, with every distance in the table above.

    A/B differ in one sign (0.125), D/E in one sign among a different block (0.125), and
    every cross pair differs in at least eight (>= 1.0). F differs from everything in at
    least four (>= 0.5). At epsilon 0.2: {A,B} and {D,E} are clusters, F is noise.
    """
    faces = {
        1: signs(),  # A
        2: signs(0),  # B - one flip from A: 0.125
        4: signs(8, 9, 10, 11, 12, 13, 14, 15),  # D - eight flips from A: 1.0
        5: signs(0, 8, 9, 10, 11, 12, 13, 14, 15),  # E - one flip from D: 0.125
        6: signs(0, 1, 2, 3),  # F - four flips from A: 0.5; five from D... see below
    }
    # The distances F sits at, hand-computed: F vs A/B four/three flips (0.5 / 0.375),
    # F vs D twelve flips (1.5 is impossible - the max is 2.0 at sixteen; twelve gives
    # 1.5), F vs E eleven flips (1.375). Every one of them is far outside epsilon 0.2.
    assert cosine_distance(faces[6], faces[1]) == pytest.approx(0.5, abs=1e-12)
    assert cosine_distance(faces[6], faces[2]) == pytest.approx(0.375, abs=1e-12)
    assert cosine_distance(faces[1], faces[4]) == pytest.approx(1.0, abs=1e-12)
    assert cosine_distance(faces[2], faces[5]) == pytest.approx(1.0, abs=1e-12)

    labels = cluster.dbscan(faces, epsilon=0.2, min_samples=2)
    assert partition(labels) == {frozenset({1, 2}), frozenset({4, 5})}
    assert noise(labels) == {6}


def test_a_chain_whose_ends_are_outside_epsilon_is_still_ONE_cluster():
    """TRANSITIVITY IS A CHOICE, and DBSCAN's answer is not the obvious one.

    A-B is two flips (0.25), B-C is two DIFFERENT flips (0.25), and A-C is four flips
    (0.5). At epsilon 0.3, A and C are NOT within epsilon of each other - and they still
    land in one cluster, because B has three neighbours (itself, A, C), is therefore a
    core point, and density-connectedness runs through it.

    This is what makes DBSCAN find a person photographed across a range of poses where no
    single pair spans the range, and it is the property somebody reading "but A and C are
    0.5 apart and epsilon is 0.3" would most plausibly "fix". Pinned with the distances
    written out so the fix cannot look like a correction.
    """
    a, b, c = signs(0, 1), signs(), signs(2, 3)
    assert cosine_distance(a, b) == pytest.approx(0.25, abs=1e-12)
    assert cosine_distance(b, c) == pytest.approx(0.25, abs=1e-12)
    assert cosine_distance(a, c) == pytest.approx(0.5, abs=1e-12)

    labels = cluster.dbscan({1: a, 2: b, 3: c}, epsilon=0.3, min_samples=2)
    assert partition(labels) == {frozenset({1, 2, 3})}
    # And the negative control: with B removed there is no core point to connect them and
    # the same epsilon leaves two faces at 0.5 as noise. Without this the row above would
    # pass for an implementation that put everything in one cluster.
    assert partition(cluster.dbscan({1: a, 3: c}, epsilon=0.3, min_samples=2)) == set()


def test_a_pair_EXACTLY_at_epsilon_is_inside_it():
    """`<= epsilon`, at a distance that is exactly representable (see the module docstring).

    Two flips is exactly 0.25 in float32 as well as in float64, so `epsilon = 0.25` puts
    this pair exactly ON the boundary. Under `<` they are two singletons; under `<=` they
    are one cluster. Both halves are asserted: the row below moves epsilon one exact step
    down and the same pair must fall apart, which is what stops this passing for an
    implementation that ignores epsilon entirely.
    """
    a, b = signs(), signs(0, 1)
    assert cosine_distance(a, b) == pytest.approx(0.25, abs=1e-12)

    at = cluster.dbscan({1: a, 2: b}, epsilon=0.25, min_samples=2)
    assert partition(at) == {frozenset({1, 2})}

    just_below = cluster.dbscan({1: a, 2: b}, epsilon=0.125, min_samples=2)
    assert partition(just_below) == set()
    assert noise(just_below) == {1, 2}


def test_an_empty_input_is_an_empty_answer():
    """No faces, no clusters, no exception - the state a fresh archive is in.

    `pam faces cluster` on an archive nobody has enriched must print a summary, not a
    traceback, and the numpy path below would divide by an empty axis if this were not
    short-circuited.
    """
    assert cluster.dbscan({}, epsilon=0.3, min_samples=2) == {}


def test_a_lone_face_is_noise_because_min_samples_is_two():
    """Design clarification 1: "a face seen once is noise and is never offered for labeling".

    Asserted at `cluster.MIN_SAMPLES` - the constant the command really passes - and NOT
    at a hand-written 2, because the thing under test is the shipped value. The literal 2
    is pinned separately, from the clarification, in the row below.
    """
    assert cluster.MIN_SAMPLES == 2
    labels = cluster.dbscan({7: signs()}, epsilon=0.5, min_samples=cluster.MIN_SAMPLES)
    assert labels == {7: None}


def test_min_samples_of_one_would_make_every_lone_face_its_own_cluster():
    """The negative control under the row above: `min_samples` really is what decides it.

    Without this, "a lone face is noise" passes for an implementation that never returns a
    cluster of one for any reason at all - including one that hard-codes noise for any
    input of size 1. Driven at `min_samples=1`, where the textbook answer is a cluster per
    face, and it is the mutation Step 7 names ("`min_samples` 2 -> 1") seen from the front.
    """
    labels = cluster.dbscan(
        {7: signs(), 9: signs(0, 1, 2, 3, 4, 5, 6, 7)}, epsilon=0.1, min_samples=1
    )
    assert partition(labels) == {frozenset({7}), frozenset({9})}


# ---------------------------------------------------------------------------
# 4. NORMALISATION - a MEASURED requirement (spike section 3), not a precaution.
# ---------------------------------------------------------------------------

# SFace's measured raw L2 norm. Recorded in `docs/implementation-notes/m11-model-spike.md`
# section 3 and transcribed here as a literal: the enricher normalises before it stores,
# so no row in a pam-written catalog carries this magnitude and only a hand-built input
# can exercise the requirement.
SFACE_RAW_L2 = 13.377


def test_the_partition_does_not_change_when_a_vector_arrives_unnormalised():
    """Scale one face by SFace's measured raw norm; the answer must be identical.

    Cosine distance is scale-invariant BY DEFINITION, so this is the property, not a
    tolerance. Skipping the normalisation does not merely perturb the answer: `1 - dot`
    over a vector scaled by 13.377 is negative against anything it is not orthogonal to,
    so the scaled face becomes a neighbour of faces it is nowhere near and drags them into
    its cluster. C is chosen at SIX flips from A (0.75, far outside epsilon 0.2) rather
    than at eight, and that choice is the whole test: an ORTHOGONAL C has a raw dot of
    exactly 0 no matter how A is scaled, so it would stay out under both implementations
    and this row would be a survivor. The third assertion writes the raw number out.
    """
    a, b, c = signs(), signs(0), signs(0, 1, 2, 3, 4, 5)
    unit = {1: a, 2: b, 3: c}
    scaled = {1: tuple(v * SFACE_RAW_L2 for v in a), 2: b, 3: c}
    assert cosine_distance(a, c) == pytest.approx(0.75, abs=1e-12)
    assert cosine_distance(b, c) == pytest.approx(0.625, abs=1e-12)

    assert partition(cluster.dbscan(scaled, epsilon=0.2, min_samples=2)) == partition(
        cluster.dbscan(unit, epsilon=0.2, min_samples=2)
    )
    assert partition(cluster.dbscan(scaled, epsilon=0.2, min_samples=2)) == {frozenset({1, 2})}
    # The raw dot product an unnormalised implementation would compute against C:
    # 13.377 * 0.25 = 3.34, so `1 - dot` is about -2.34, which is <= 0.2. Face 3 would
    # join and the partition would be {1,2,3}.
    raw_dot = sum(x * y for x, y in zip(scaled[1], c, strict=True))
    assert 1.0 - raw_dot < -2.0


def test_a_zero_vector_is_noise_rather_than_a_crash():
    """A vector with no direction has no cosine distance; it must not become a NaN.

    `pam/enrich/faces.py::_embed` refuses a zero embedding before it is stored, so this is
    the second layer. What it pins is that the OTHER faces are unaffected: a NaN row
    propagating through the block matmul would take the whole pass with it.
    """
    labels = cluster.dbscan(
        {1: signs(), 2: signs(0), 3: (0.0,) * DIM}, epsilon=0.3, min_samples=2
    )
    assert partition(labels) == {frozenset({1, 2})}
    assert noise(labels) == {3}


# ---------------------------------------------------------------------------
# 5. THE PROPERTY ROWS - production against the second implementation.
#
# M996's lesson is the one this section is built to avoid: a harness that FORCES each
# shape once and then randomises protects only the forced phase, while the free phase
# serves most of the draws. Both phases are here, both are compared, and the free phase
# has its OWN pin on the distribution of outcomes it produced.
# ---------------------------------------------------------------------------

# Deliberately spread across the exact grid: 0.125, 0.25, 0.375, 0.5 are all distances the
# hand-computed vectors can hit exactly, so boundary behaviour is inside the free phase
# rather than only in the forced one.
FREE_EPSILONS = (0.125, 0.25, 0.375, 0.5)
FREE_DRAWS = 60


def random_case(rng):
    """A random handful of `signs` vectors, so the free phase really is free."""
    count = rng.randint(2, 9)
    return {
        index + 1: signs(*rng.sample(range(DIM), rng.randint(0, DIM // 2)))
        for index in range(count)
    }


def test_production_and_the_second_implementation_agree_over_random_draws():
    """The free phase. Sixty draws, compared as PARTITIONS and as noise sets.

    This row catches ASYMMETRIC drift - production changing while the reference does not -
    and it catches nothing else: co-mutating both identically leaves it green, which M8's
    `M922`/`M924` measured. The literal table in section 1 is what stands behind the
    sentences; this stands behind the inputs nobody wrote down.
    """
    rng = random.Random(9110)
    for draw in range(FREE_DRAWS):
        faces = random_case(rng)
        epsilon = FREE_EPSILONS[draw % len(FREE_EPSILONS)]
        mine = cluster.dbscan(faces, epsilon=epsilon, min_samples=2)
        theirs = reference_dbscan(faces, epsilon=epsilon, min_samples=2)
        assert partition(mine) == partition(theirs), (draw, epsilon, faces)
        assert noise(mine) == noise(theirs), (draw, epsilon, faces)


def test_the_free_phase_really_produces_every_outcome_it_is_supposed_to_cover():
    """M996's pin ON THE FREE PHASE: the draws are not all one shape.

    A generator that degenerated - every draw one cluster, or every draw all noise - would
    leave the row above green while testing one case sixty times. This asserts the SHAPE of
    what those sixty draws produced: some with no cluster at all, some with exactly one,
    some with two or more, and some carrying noise alongside a cluster. Written against the
    reference implementation, so a degenerate PRODUCTION function cannot make its own
    coverage claim look healthy.
    """
    rng = random.Random(9110)
    seen: dict[str, int] = {"empty": 0, "one": 0, "many": 0, "mixed": 0}
    for draw in range(FREE_DRAWS):
        faces = random_case(rng)
        epsilon = FREE_EPSILONS[draw % len(FREE_EPSILONS)]
        labels = reference_dbscan(faces, epsilon=epsilon, min_samples=2)
        groups = partition(labels)
        if not groups:
            seen["empty"] += 1
        elif len(groups) == 1:
            seen["one"] += 1
        else:
            seen["many"] += 1
        if groups and noise(labels):
            seen["mixed"] += 1
    assert all(count > 0 for count in seen.values()), seen


def test_the_forced_phase_covers_every_shape_at_least_once():
    """The guarded phase, and it is exhaustive rather than sampled.

    Every subset of four positions out of six, as a four-face input, at each epsilon on the
    exact grid. 15 * 4 = 60 cases whose membership is decided entirely by the hand-computed
    formula, so this phase needs no distribution pin: it IS the distribution.
    """
    positions = list(itertools.combinations(range(6), 2))
    for epsilon in FREE_EPSILONS:
        for chosen in itertools.combinations(positions, 4):
            faces = {index + 1: signs(*pair) for index, pair in enumerate(chosen)}
            mine = cluster.dbscan(faces, epsilon=epsilon, min_samples=2)
            theirs = reference_dbscan(faces, epsilon=epsilon, min_samples=2)
            assert partition(mine) == partition(theirs), (epsilon, chosen)


def test_a_run_larger_than_one_block_gives_the_same_answer_as_the_reference():
    """The BLOCKING is a real code path and `BLOCK_ROWS` faces do not reach it.

    A26 - sorry, A21's bounded-memory blocks mean pass 1 is a loop, and a loop whose body
    runs once is not a loop anybody has tested. Driven at `BLOCK_ROWS + 7` faces, so the
    last block is a PARTIAL one: an off-by-one in the slice bound would leave the tail's
    neighbour counts at zero and turn the last seven faces into noise.
    """
    rng = random.Random(4242)
    count = cluster.BLOCK_ROWS + 7
    faces = {
        index + 1: signs(*rng.sample(range(DIM), rng.randint(0, 3))) for index in range(count)
    }
    mine = cluster.dbscan(faces, epsilon=0.25, min_samples=2)
    theirs = reference_dbscan(faces, epsilon=0.25, min_samples=2)
    assert partition(mine) == partition(theirs)
    assert noise(mine) == noise(theirs)
    # And the tail is really in a cluster rather than silently dropped, which is the
    # specific failure a wrong block bound produces.
    assert mine[count] is not None or count in noise(theirs)


def test_cluster_numbers_are_one_based_dense_and_deterministic():
    """The numbers themselves, since a caller offsets them (`faces/__init__.py`).

    `run_cluster` allocates above the retained maximum by ADDING to these numbers, so a
    zero-based or sparse numbering would silently collide with a named person's cluster.
    Asserted on the numbers, not on the partition, because that is what the caller relies
    on; every other row in this module asserts the partition instead.
    """
    faces = {
        3: signs(),
        1: signs(0),
        7: signs(0, 1, 2, 3, 4, 5, 6, 7),
        9: signs(0, 1, 2, 3, 4, 5, 6),
        5: signs(0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11),
    }
    labels = cluster.dbscan(faces, epsilon=0.2, min_samples=2)
    numbers = sorted({n for n in labels.values() if n is not None})
    assert numbers == list(range(1, len(numbers) + 1))
    # Ascending face-id order decides which cluster is 1: face 1 is in the first.
    assert labels[1] == 1


# ---------------------------------------------------------------------------
# 6. `decode_embedding` - design 4's BLOB as floats.
# ---------------------------------------------------------------------------


def test_decode_embedding_reads_the_bytes_the_enricher_writes():
    """Against `struct`, not against numpy: an independent second reader of the same bytes.

    `pam/enrich/faces.py` writes `numpy.float32(...).tobytes()`. This asserts the values
    come back through `array.array('f', ...)` identically to `struct.unpack('<4f', ...)`,
    which is the same claim spelled with a different stdlib module.
    """
    blob = struct.pack("<4f", 1.5, -2.25, 0.0, 1024.0)
    assert cluster.decode_embedding(blob) == (1.5, -2.25, 0.0, 1024.0)
    assert len(blob) // cluster.FLOAT32_BYTES == 4


def test_decode_embedding_refuses_a_blob_that_is_not_whole_float32s():
    """A hand-edited catalog gets an error, not a silently truncated vector."""
    with pytest.raises(ValueError, match="whole number of float32"):
        cluster.decode_embedding(b"\x00\x01\x02")


# ---------------------------------------------------------------------------
# 7. A21's SCALE BOUNDARY - the estimate, the preamble and the refusal (Step 5).
# ---------------------------------------------------------------------------


def test_the_pair_count_is_n_choose_2_spelled_from_the_formula():
    """`n(n-1)/2`, checked against an independently enumerated count at small n.

    `itertools.combinations` is the second implementation here, and at n = 40 it is cheap
    enough to enumerate. Without it the estimate could report `n * n` - four times the real
    figure at every size - and nothing in the suite would notice.
    """
    for n in (0, 1, 2, 40):
        assert cluster.estimate(n).comparisons == len(list(itertools.combinations(range(n), 2)))


def test_the_preamble_publishes_the_count_the_estimate_and_the_ceiling_BEFORE_the_work():
    """A21's three facts, each asserted by the number it must carry.

    "Publishes the face count, the estimated time and memory, and the tested ceiling."
    Asserted as text because that is the contract - a user reads these - and with the
    numbers formatted the way a user sees them.
    """
    text = "\n".join(cluster.lines(cluster.estimate(5_000)))
    assert "5,000 faces" in text
    assert "12,497,500 pairs" in text
    assert "memory" in text
    assert f"{cluster.TESTED_CEILING:,}" in text


def test_the_ceiling_refusal_fires_above_it_and_not_at_it():
    """Both sides of A21's boundary, at the exact face count.

    At the ceiling it runs; one face above it, it refuses. A `>=` here would refuse an
    archive pam has actually measured, which is the opposite failure and just as wrong.
    """
    assert cluster.refusal(cluster.estimate(cluster.TESTED_CEILING)) is None
    assert cluster.refusal(cluster.estimate(cluster.TESTED_CEILING - 1)) is None
    refusal = cluster.refusal(cluster.estimate(cluster.TESTED_CEILING + 1))
    assert refusal is not None


def test_the_ceiling_refusal_names_the_number_and_the_remedy():
    """A21: "refuses, naming the number and the remedy". Both, spelled from the amendment.

    The two remedies are asserted by name because they are what makes this refusal not a
    dead end (UX 1.4): raising the floor, and naming clusters - a named face leaves the
    next pass. "Purge" must NOT appear: it is destructive and neither remedy needs it.
    """
    refusal = cluster.refusal(cluster.estimate(cluster.TESTED_CEILING + 500))
    assert refusal is not None
    assert f"{cluster.TESTED_CEILING + 500:,}" in refusal
    assert f"{cluster.TESTED_CEILING:,}" in refusal
    assert "[tiers] faces.floor" in refusal
    assert "pam faces label" in refusal
    assert "purge" not in refusal


def test_the_estimate_grows_quadratically_and_the_memory_does_not():
    """A21's own sentence: "bounded blocks fix memory, not time".

    Ten times the faces is about a hundred times the time and about ten times the memory.
    If the memory term were quadratic - the one-big-matrix implementation this design
    rejects - the second assertion would fail by two orders of magnitude, which is the
    whole reason A21 asks for blocks.
    """
    small = cluster.estimate(1_000)
    large = cluster.estimate(10_000)
    assert large.seconds / small.seconds == pytest.approx(100, rel=0.01)
    assert large.peak_bytes / small.peak_bytes == pytest.approx(10, rel=0.01)


def test_the_published_estimate_is_not_off_by_ORDERS_OF_MAGNITUDE_against_a_real_clock():
    """The constant is MEASURED, and this is the only thing keeping it measured.

    Design 8.1 amendment A2's lesson: an ETA formula implemented literally and never
    checked against a clock displayed 87.5 s for a run that took 0.08 s - a 1093x
    overstatement nobody could see. This drives the real `dbscan` over 1,200 synthetic
    faces and compares the wall clock against what `estimate` publishes.

    THE NAME SAYS "ORDERS OF MAGNITUDE" BECAUSE THAT IS WHAT THE BAND CAN ACTUALLY CHECK,
    and claiming more would be a name a fixture cannot exercise. The ratio measured on the
    machine this constant was derived on is 0.36 (predicted 0.022 s, elapsed 0.061 s); CI
    hardware is ~3x slower, which pushes it to about 0.12. The band is 1/100 to 100, which
    a tighter reading would not survive on a shared runner and which A2's own defect -
    three orders of magnitude - fails by a wide margin.

    THE CLOCK IS `perf_counter`, NOT `monotonic` (HT-M15 F2, 2026-11-22). On Windows before
    Python 3.13, `time.monotonic()` is GetTickCount64, which advances in ~15.6 ms steps; the
    hosted runner's Python 3.12 finished this `dbscan` inside one step and read 0, failing
    `elapsed > 0` on two public CI runs. A WARM `dbscan` (an earlier test in this module has
    already run one) takes ~7 ms on the machine the ratio above was measured on - the 0.061 s
    is a cold first call - so it fits inside one step anywhere. `perf_counter` is
    QueryPerformanceCounter on every version. Flooring it to 15.6 ms steps fails this test
    (M5410), which is the bug reproduced.
    """
    import time

    rng = random.Random(77)
    faces = {
        index + 1: tuple(rng.gauss(0, 1) for _ in range(cluster.DEFAULT_DIM))
        for index in range(1_200)
    }
    started = time.perf_counter()
    cluster.dbscan(faces, epsilon=0.3, min_samples=2)
    elapsed = time.perf_counter() - started
    predicted = cluster.estimate(len(faces)).seconds
    assert elapsed > 0, "the clock did not move; this machine cannot measure this test"
    assert 0.01 <= predicted / elapsed <= 100, (predicted, elapsed)


# ---------------------------------------------------------------------------
# 8. THE REVIEW'S FIVE FINDINGS (Step 7's mutation pass). Each row below closes a
#    mutation that survived the whole suite, and each names the mutation by id.
# ---------------------------------------------------------------------------


def test_the_oracle_is_SCALE_INVARIANT_which_is_the_half_that_makes_it_cosine():
    """`M1789`: `cosine_distance` could drop its `/(|a||b|)` with all 3199 tests green.

    Every vector this module hands the oracle is unit length by construction, so no other
    row can express the difference - and because `reference_dbscan` CALLS this function,
    the "second" and "third" implementations degrade together. That makes this the one
    place the oracle's normalisation is checked, and it is checked against a HAND-COMPUTED
    literal rather than against itself: a `signs` vector scaled by SFace's measured raw
    norm is still exactly one flip from `signs(0)`, so the answer is exactly 0.125.
    """
    scaled = tuple(value * SFACE_RAW_L2 for value in signs())
    assert cosine_distance(scaled, signs(0)) == pytest.approx(0.125, abs=1e-12)
    assert cosine_distance(scaled, signs()) == pytest.approx(0.0, abs=1e-12)
    # And the raw un-normalised figure, spelled out, so the row cannot pass for `1 - dot`:
    # 13.377 * (15 * 0.0625 - 0.0625) is about 11.7, and `1 - 11.7` is nowhere near 0.125.
    raw = sum(x * y for x, y in zip(scaled, signs(0), strict=True))
    assert 1.0 - raw < -10.0


def test_pass_one_never_compares_more_than_BLOCK_ROWS_rows_at_once(monkeypatch):
    """`M1790`: A21's bounded memory had no test - `unit[start : start + BLOCK_ROWS]`
    could become `unit[start:]` with the suite green.

    THE ANSWER IS UNCHANGED BY THAT MUTATION, which is exactly why no partition assertion
    can see it: the tail is recomputed and re-assigned on every iteration and comes out
    identical. Only the peak allocation moves - at A21's ceiling from one 98 MB block to
    the 40 GB matrix the design rejects by name. So this WATCHES THE DANGEROUS CALL: the
    first dimension of the array actually handed to the comparison, on every iteration.
    """
    import numpy

    real = numpy.count_nonzero
    widths: list[int] = []

    def watched(condition, *args, **kwargs):
        widths.append(condition.shape[0])
        return real(condition, *args, **kwargs)

    monkeypatch.setattr(numpy, "count_nonzero", watched)
    rng = random.Random(1790)
    count = cluster.BLOCK_ROWS * 2 + 7
    faces = {
        index + 1: signs(*rng.sample(range(DIM), rng.randint(0, 3))) for index in range(count)
    }
    cluster.dbscan(faces, epsilon=0.25, min_samples=2)
    assert widths, "pass 1 made no blocked comparison at all"
    assert max(widths) <= cluster.BLOCK_ROWS, (
        f"pass 1 compared {max(widths)} rows at once against a block bound of"
        f" {cluster.BLOCK_ROWS}: A21's memory is quadratic again"
    )
    # And the loop really ran more than once, or the bound above is vacuous.
    assert len(widths) >= 3, widths


# `min_samples = 4` and the distances, all hand-computed on the 0.125 grid. Face 5 is
# within epsilon of exactly one member of each group (|N| = 3 < 4), so it is a BORDER
# point: it joins a cluster and must not extend one. Every other face has |N| >= 4.
BRIDGE_EPSILON = 0.375
BRIDGE_MIN_SAMPLES = 4
BRIDGE_LEFT = frozenset({1, 2, 3, 4})
BRIDGE_RIGHT = frozenset({6, 7, 8, 9})


def bridge_case():
    """Two dense groups 0.75 apart and one non-core face sitting between them."""
    return {
        1: signs(),
        2: signs(6),
        3: signs(7),
        4: signs(8),
        5: signs(0, 1, 2),
        6: signs(0, 1, 2, 3, 4, 5),
        7: signs(0, 1, 2, 3, 4, 5, 6),
        8: signs(0, 1, 2, 3, 4, 5, 7),
        9: signs(0, 1, 2, 3, 4, 5, 8),
    }


def test_a_BORDER_point_joins_one_cluster_and_never_BRIDGES_two():
    """`M1798`: deleting `if core[other]` turns DBSCAN into single-linkage, 3199 green.

    WHY NOTHING ELSE COULD SEE IT. At the shipped `MIN_SAMPLES = 2` a border point cannot
    exist - any face within epsilon of another has |N| >= 2 and is therefore core - so the
    guard is a difference no test driving the shipped constant can express. But `dbscan`
    takes `min_samples` as a required keyword with no default, and production's own
    comment makes a claim about it: "chains two people together through one ambiguous
    face", which is vision R6's failure. A claim nothing exercises is an untested test.

    Built by hand at `min_samples = 4` with every distance on the exact grid. Face 5 is
    0.375 from face 1 and 0.375 from face 6 and outside epsilon of everything else, so
    |N(5)| = 3 and it is not core. Correct DBSCAN keeps the two groups apart; single
    linkage returns one cluster of nine.
    """
    faces = bridge_case()
    assert cosine_distance(faces[5], faces[1]) == pytest.approx(0.375, abs=1e-12)
    assert cosine_distance(faces[5], faces[6]) == pytest.approx(0.375, abs=1e-12)
    assert cosine_distance(faces[1], faces[6]) == pytest.approx(0.75, abs=1e-12)
    within = [
        other for other in faces if cosine_distance(faces[5], faces[other]) <= BRIDGE_EPSILON
    ]
    assert sorted(within) == [1, 5, 6], within
    assert len(within) < BRIDGE_MIN_SAMPLES, "face 5 is core; the construction is wrong"

    labels = cluster.dbscan(faces, epsilon=BRIDGE_EPSILON, min_samples=BRIDGE_MIN_SAMPLES)
    groups = partition(labels)
    assert len(groups) == 2, groups
    # The two dense groups stay apart; face 5 belongs to exactly one of them.
    assert {frozenset(g - {5}) for g in groups} == {BRIDGE_LEFT, BRIDGE_RIGHT}, groups
    assert (
        partition(
            reference_dbscan(faces, epsilon=BRIDGE_EPSILON, min_samples=BRIDGE_MIN_SAMPLES)
        )
        == groups
    )


# A21's ladder, transcribed from the M11 progress record (Task 9 Step 5) - the run
# `tools/bench_cluster.py` actually made. The TOP RUNG is the ceiling.
MEASURED_TOP_RUNG_FACES = 100_000
MEASURED_TOP_RUNG_SECONDS = 59.61


def test_the_tested_ceiling_IS_THE_TOP_RUNG_THAT_WAS_ACTUALLY_RUN():
    """`M1800`: `TESTED_CEILING` could become 1,000 with all 3199 tests green.

    Every other assertion about the ceiling formats the constant back out of itself
    (`f"{cluster.TESTED_CEILING:,}"`), which agrees with any value of it - the `ENRICHERS`
    shape this project has measured before. The number is a MEASUREMENT: too low and pam
    refuses archives it has run, too high and it promises a time nobody has measured.
    Pinned here against the ladder. That the ladder is the one in the run's record is checked
    beside the record itself (M15 Task 14 moved that half, which reads a private document, to
    `tests/private/`), so the constant and the run it came from cannot drift apart silently.
    """
    assert cluster.TESTED_CEILING == MEASURED_TOP_RUNG_FACES
    assert cluster.SECONDS_PER_MILLION_PAIRS == 0.03


# The cross-module anchor that stood here, under `tests/test_faces_cluster.py`, M1796, was absorbed into
# `tests/census.py` at M12 Task 1 (m11.md condition C4): one hand-written inventory outside
# every guarded module, checked at collection by `conftest.py`.


# ---------------------------------------------------------------------------
# THE DELETION FLOOR (M10's C4, and `M1670`'s lesson about the instruments).
#
# HAND-SPELLED, NEVER DERIVED FROM `globals()`: an inventory that asks the module what it
# contains agrees with any value of itself, so deleting a test would delete its name with
# it and the floor would pass.
# ---------------------------------------------------------------------------

LOAD_BEARING_CONTROLS = {
    "test_the_hand_computed_distances_are_what_the_definition_gives": (
        "the row that makes the literal table mean anything. Without it every epsilon "
        "chosen in this module is chosen against a formula nothing checked, and M8's "
        "M922/M924 co-mutation is survivable again"
    ),
    "test_the_hand_computed_vectors_are_exactly_unit_length": (
        "the premise the exact-boundary test rests on. Without it a later change to DIM "
        "or STEP reintroduces float rounding and `<=` vs `<` stops being observable"
    ),
    "test_a_chain_whose_ends_are_outside_epsilon_is_still_ONE_cluster": (
        "transitivity is a CHOICE and this is the only place it is written down. "
        "Nothing else in the suite would notice DBSCAN turning into pairwise grouping"
    ),
    "test_a_pair_EXACTLY_at_epsilon_is_inside_it": (
        "the `<= epsilon` -> `<` mutation's only killer, and it only works because the "
        "distance is exactly representable"
    ),
    "test_the_partition_does_not_change_when_a_vector_arrives_unnormalised": (
        "spike section 3's measured requirement. Every embedding pam WRITES is already "
        "unit length, so no catalog-driven test can see the normalisation step at all"
    ),
    "test_the_free_phase_really_produces_every_outcome_it_is_supposed_to_cover": (
        "M996: the free phase serves most of the draws and needs its own pin. Without "
        "it a degenerate generator leaves the property row green over one shape"
    ),
    "test_a_run_larger_than_one_block_gives_the_same_answer_as_the_reference": (
        "the only row where pass 1's block loop runs more than once, and the only one "
        "with a partial final block"
    ),
    "test_the_oracle_is_SCALE_INVARIANT_which_is_the_half_that_makes_it_cosine": (
        "M1789. The only row that checks the oracle every other row is checked against "
        "is really cosine distance; without it `reference_dbscan` degrades with it"
    ),
    "test_pass_one_never_compares_more_than_BLOCK_ROWS_rows_at_once": (
        "M1790. A21's bounded memory, which no partition assertion can see because the "
        "unbounded implementation returns the identical answer"
    ),
    "test_a_BORDER_point_joins_one_cluster_and_never_BRIDGES_two": (
        "M1798. The only row driving `min_samples` above 2, which is the only regime in "
        "which the border-point guard is a difference at all - single linkage otherwise"
    ),
    "test_the_tested_ceiling_IS_THE_TOP_RUNG_THAT_WAS_ACTUALLY_RUN": (
        "M1800. Every other assertion about the ceiling reads the constant back out of "
        "itself and agrees with any value of it"
    ),
    "test_the_published_estimate_is_not_off_by_ORDERS_OF_MAGNITUDE_against_a_real_clock": (
        "A2's lesson: the only thing in the repository comparing A21's published "
        "estimate against a clock. Without it the constant is a number in a comment"
    ),
}

assert LOAD_BEARING_CONTROLS, "this module's control list is empty; it protects nothing"


def test_no_load_bearing_control_in_this_file_can_be_deleted_without_saying_so():
    """The floor under this module's own controls."""
    present = {name for name in globals() if name.startswith("test_")}
    missing = sorted(set(LOAD_BEARING_CONTROLS) - present)
    assert missing == [], (
        f"a load-bearing control has been deleted from {Path(__file__).name}:"
        f" {missing}. Each is why another assertion here means anything"
        f" ({[LOAD_BEARING_CONTROLS[m] for m in missing]})."
    )
