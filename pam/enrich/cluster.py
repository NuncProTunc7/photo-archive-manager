"""DBSCAN over face embeddings, hand-rolled, and A21's scale boundary in user terms.

WHY THIS IS A PURE FUNCTION AND NOT A METHOD ON THE ENRICHER. Design 8.1: "clustering is
a separate command (`pam faces cluster`, DBSCAN over embeddings, cosine e from config)
since it is corpus-wide, not per-file". The enricher's unit of work is one photograph;
this one's is the whole archive. Nothing here touches a catalog, a file or a model: it is
arithmetic over vectors, which is what lets the second implementation in
`tests/test_cluster.py` be written from the algorithm's definition and compared against it.

NO SCIKIT-LEARN, AND THE REASON IS NOT PURITY. `sklearn` is ~30 MB of compiled code and a
sixth runtime dependency (or a second optional extra) for one textbook algorithm of about
forty lines. Design 1's dependency table sanctions `onnxruntime` and `numpy` for the faces
stack and nothing else, and a new dependency needs a spec amendment. `numpy` itself is
imported INSIDE the functions that use it, on `pam/enrich/phash.py`'s measured precedent -
`EAGER_IMPORT_CLOSURE` stays `{click, pam}` and `tests/test_faces_model.py` walks the AST
of `pam/` to refuse a module-scope import of it.

NORMALISATION IS A MEASURED REQUIREMENT, NOT A PRECAUTION (spike section 3). SFace's `fc1`
output has an L2 norm of **13.377**, not 1.0, so `1 - dot` is not a cosine distance until
the vectors are unit length. `pam/enrich/faces.py::_embed` normalises before it stores, so
rows this product wrote are already unit length - but this function's contract is over
embeddings, not over that writer, and an exported-and-reimported catalog, a hand-written
fixture or a future second producer are all inside it. Normalising here is idempotent for
a unit vector and load-bearing for every other one.

THE ZERO VECTOR IS NOISE, DELIBERATELY. A vector of length 0 has no direction, so its
cosine distance to anything - itself included - is undefined. It is left as zeros, which
puts it at distance 1.0 from everything and makes it noise under any epsilon below 1. The
enricher already drops one before it is stored (`_embed` refuses a zero embedding), so
this is the second layer rather than the first.

BOUNDED MEMORY IS TWO PASSES, NOT ONE MATRIX (A21). The obvious implementation builds the
full n x n distance matrix and reads neighbour lists off it: at 100,000 faces that is 40 GB
in float32, and the neighbour lists themselves can be another n^2 of indices. Instead:

  pass 1  counts each face's neighbours in blocks of `BLOCK_ROWS` rows, which is what
          decides which faces are CORE. Peak extra memory is one block, not one matrix.
  pass 2  expands each cluster from its core points, recomputing ONE row at a time.

That costs roughly twice the arithmetic and turns the memory from quadratic into linear,
which is the trade A21 asks for by name: "bounded blocks fix memory, not time - the
comparison count stays quadratic - so the ceiling is a MEASURED face count".
"""

import array
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

# Design clarification 1: `min_samples = 2`, a code constant - design 8.1 names only
# epsilon. A face seen once is noise and is never offered for labeling, and
# `pam faces cluster` says so rather than leaving a user to infer it from a missing
# cluster. Counted INCLUSIVE of the point itself, which is DBSCAN's own definition: a
# lone face has |N| = 1 < 2 and is noise; a pair within epsilon has |N| = 2 and is not.
MIN_SAMPLES = 2

# Rows of the distance matrix computed at once in pass 1. 256 x 100,000 float32 is 98 MB,
# which is the largest single allocation this algorithm makes at the tested ceiling.
BLOCK_ROWS = 256

# Bytes per stored embedding component. Design 4's column is a BLOB of raw float32, which
# is what `pam/enrich/faces.py` writes (`.astype(numpy.float32).tobytes()`) and what A3's
# manifest declares (`embedding_dtype = "float32"`). NATIVE byte order on both sides:
# `numpy.tobytes()` and `array.array("f", ...)` agree, and a catalog is not moved between
# machines of different endianness (every platform pam supports is little-endian anyway).
FLOAT32_BYTES = 4

# A21's TESTED CEILING, in faces. `tools/bench_cluster.py` RAN this many synthesised
# embeddings on this machine and the ladder is recorded in
# `docs/implementation-notes/m11-progress.md`, Task 9 Step 5:
#
#     faces      pairs            seconds
#      1,000        499,500          0.03
#      5,000     12,497,500          0.89
#     20,000    199,990,000          4.51
#     50,000  1,249,975,000         20.51
#    100,000  4,999,950,000         59.61
#
# It is a number pam has actually run, not a number that seemed round. Above it the
# command REFUSES rather than promising a time nobody has measured - which is a promise
# boundary and not a performance guard: the refusal is expected never to fire on a
# personal archive, because 100,000 faces is on the order of 50,000 photographs.
TESTED_CEILING = 100_000

# Seconds per million pairwise comparisons. Measured per-pair cost FALLS as n rises (BLAS
# amortises: 0.060 s/Mpair at n=1,000 down to 0.012 at n=100,000), so no single constant
# fits the whole ladder and this one is chosen to be honest where the number matters - at
# the top end, and erring HIGH. Against the measurements above it predicts 6 s for 20,000
# (actual 4.5), 38 s for 50,000 (actual 21) and 150 s for 100,000 (actual 60): within
# ~2.5x and always over, on a machine that may well be faster than the user's. Design 8.1
# amendment A2's lesson is the one being avoided - an ETA that read 87.5 s for a run that
# took 0.08 s, a 1093x overstatement, because a formula was implemented literally and
# never measured against a clock.
SECONDS_PER_MILLION_PAIRS = 0.03

# The embedding width the estimate assumes when it has none to hand (A3: this pack's
# `embedding_dim`). Only the memory figure uses it, and only to be honest about the
# matrix; the caller passes the real width when it knows it.
DEFAULT_DIM = 128


@dataclass(frozen=True)
class ScaleEstimate:
    """What A21 requires `pam faces cluster` to publish BEFORE it starts.

    "Publishes the face count, the estimated time and memory, and the tested ceiling."
    All four are here so the sentences that print them cannot disagree with the number
    the refusal uses - `refusal` and `lines` below read this same object.
    """

    faces: int
    dim: int
    comparisons: int
    seconds: float
    peak_bytes: int
    ceiling: int


def estimate(faces: int, dim: int = DEFAULT_DIM) -> ScaleEstimate:
    """The face count, the pair count, and what this machine measured them to cost.

    `comparisons` is the number of UNORDERED pairs, n(n-1)/2, which is the number a user
    can check by hand - the implementation computes each ordered pair twice in pass 1 and
    some of them again in pass 2, and `SECONDS_PER_MILLION_PAIRS` was measured against
    this same n(n-1)/2 denominator so the two stay consistent.
    """
    comparisons = faces * (faces - 1) // 2
    peak = faces * dim * FLOAT32_BYTES + BLOCK_ROWS * faces * FLOAT32_BYTES + faces * 16
    return ScaleEstimate(
        faces=faces,
        dim=dim,
        comparisons=comparisons,
        seconds=comparisons / 1_000_000 * SECONDS_PER_MILLION_PAIRS,
        peak_bytes=peak,
        ceiling=TESTED_CEILING,
    )


def _mb(count: int) -> str:
    return f"{count / (1024 * 1024):,.0f} MB"


def _duration(seconds: float) -> str:
    if seconds < 1:
        return "under a second"
    if seconds < 90:
        return f"about {seconds:,.0f} seconds"
    return f"about {seconds / 60:,.0f} minutes"


def lines(scale: ScaleEstimate) -> list[str]:
    """A21's preamble. Printed BEFORE the work, which is the whole point of it.

    A progress bar that appears after the expensive part has already run tells a user
    nothing they can act on. These three lines are what let somebody decide to raise the
    floor, or to go and make coffee, before the command commits to the arithmetic.
    """
    return [
        f"Clustering {scale.faces:,} faces ({scale.comparisons:,} pairs to compare).",
        f"  Estimated {_duration(scale.seconds)} and about {_mb(scale.peak_bytes)} of memory.",
        f"  Tested up to {scale.ceiling:,} faces.",
    ]


def refusal(scale: ScaleEstimate) -> str | None:
    """A21's refusal above the ceiling, naming the number and the remedy. Or None.

    TWO REMEDIES, because the two are genuinely different situations. Raising the floor
    is the knob for an archive full of low-confidence detections; naming clusters is the
    one for an archive that really does hold that many faces, and it works because a
    named face leaves the pass (`faces_for_clustering` reads `person_id IS NULL`). Both
    are reversible, which is why neither is "purge".
    """
    if scale.faces <= scale.ceiling:
        return None
    return (
        f"this archive has {scale.faces:,} faces to cluster and pam faces cluster is"
        f" tested up to {scale.ceiling:,}. The number of comparisons grows with the"
        " SQUARE of the face count, so pam will not promise a time it has not measured."
        " Two ways down, and both are reversible: raise [tiers] faces.floor in pam.toml"
        " to consider fewer detections, or name some clusters with pam faces label -"
        " a face that belongs to a person you have named is left out of the next pass."
        " Indexing for larger collections is planned for a later milestone."
    )


def decode_embedding(blob: bytes) -> tuple[float, ...]:
    """Design 4's BLOB as floats. `array`, not `numpy` - this runs per row.

    Raises `ValueError` on a blob that is not a whole number of float32s, which is the
    same class `array.array` raises itself; a caller reading a hand-edited catalog gets
    an error naming the row rather than a silently truncated vector.
    """
    if len(blob) % FLOAT32_BYTES:
        raise ValueError(f"{len(blob)} bytes is not a whole number of float32 values")
    return tuple(array.array("f", blob))


def dbscan(
    embeddings: Mapping[int, Sequence[float]],
    *,
    epsilon: float,
    min_samples: int,
) -> dict[int, int | None]:
    """DBSCAN over cosine distance. Every key gets a cluster number or `None` for noise.

    `epsilon` and `min_samples` are REQUIRED KEYWORD ARGUMENTS and have no defaults. A
    default epsilon is exactly the thing A12 refuses to ship - "the value is not normative
    until the evidence exists" - and a default that lived here would be one this function's
    callers could reach without ever reading design 11's key.

    COSINE DISTANCE IS `1 - dot` OVER L2-NORMALISED VECTORS. Both halves matter: see the
    module docstring for why the normalisation is a measured requirement rather than a
    defensive one.

    TRANSITIVITY IS A CHOICE, AND DBSCAN'S ANSWER IS NOT THE OBVIOUS ONE. Given a chain
    A - B - C where A-B and B-C are both within epsilon but A-C is NOT, all three land in
    ONE cluster, because B is a core point and density-connectedness runs through it. That
    is deliberate and is what makes DBSCAN find a person photographed across a range of
    poses, where no single pair spans the range. It is pinned by name in
    `tests/test_cluster.py` with hand-computed distances so nobody "fixes" it later.

    CLUSTER NUMBERS ARE 1-BASED AND DETERMINISTIC. Faces are visited in ascending id
    order and a new cluster takes the next number, so an unchanged input produces an
    identical output - not merely an equivalent partition. Design 6.2 rule 6's determinism
    is inherited the moment a cluster's name can reach a filename (design 6.1's
    `{people}`), and "whichever row came back first" is not a rule.
    """
    import numpy

    ids = sorted(embeddings)
    if not ids:
        return {}
    matrix = numpy.asarray([list(embeddings[i]) for i in ids], dtype=numpy.float32)
    norms = numpy.linalg.norm(matrix, axis=1, keepdims=True)
    # `where=norms > 0` leaves a zero vector as zeros rather than producing NaN: see the
    # module docstring. `out=` is required by `numpy.divide` whenever `where=` is given -
    # without it the masked-out entries are uninitialised memory, not zeros.
    unit = numpy.divide(matrix, norms, out=numpy.zeros_like(matrix), where=norms > 0)
    count = len(ids)

    # PASS 1 - who is a core point. Blocked, so peak extra memory is one block of rows
    # and not the whole matrix (A21).
    neighbours = numpy.zeros(count, dtype=numpy.int64)
    for start in range(0, count, BLOCK_ROWS):
        block = unit[start : start + BLOCK_ROWS]
        neighbours[start : start + len(block)] = numpy.count_nonzero(
            (1.0 - block @ unit.T) <= epsilon, axis=1
        )
    core = neighbours >= min_samples

    # PASS 2 - expand each cluster from its core points, one region query at a time.
    labels: list[int | None] = [None] * count
    cluster = 0
    for seed in range(count):
        if not core[seed] or labels[seed] is not None:
            continue
        cluster += 1
        labels[seed] = cluster
        queue = deque([seed])
        while queue:
            current = queue.popleft()
            row = numpy.flatnonzero((1.0 - unit[current] @ unit.T) <= epsilon)
            for other in row.tolist():
                if labels[other] is not None:
                    continue
                # A border point joins the cluster but never extends it: only a core
                # point goes back on the queue. Deleting that condition is what turns
                # DBSCAN into single-linkage and chains two people together through one
                # ambiguous face.
                labels[other] = cluster
                if core[other]:
                    queue.append(other)
    return {face_id: labels[index] for index, face_id in enumerate(ids)}
