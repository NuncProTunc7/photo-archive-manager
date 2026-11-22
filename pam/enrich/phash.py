"""Design 8.1's `phash` enricher: dHash over a decoded image. The first pixels pam reads.

WHAT IS STORED AND WHAT IS NOT. One fact per file, `{"hash": "<16 hex digits>"}` - the
shape design 6.4's table already declares for this kind. NEAR-DUPE GROUPS ARE NOT STORED:
design 8.1 says they are "derived at report/review time by hamming distance <= threshold",
and `near_dupe_groups` below is that derivation, a pure function over hashes somebody else
has already read out of the catalog. Exact duplicates are not this enricher's business at
all - `files.sha256` answers that question exactly, for videos too, and a perceptual hash
asked to prove equality would only be a worse `sha256`.

EVERY THIRD-PARTY IMPORT IN THIS MODULE IS DEFERRED, AND THAT IS MEASURED RATHER THAN
ASSUMED. Measured at M9 Task 8, best of seven: `pam --version` costs 0.181 s, of which
0.044 s is the interpreter itself; importing `PIL.Image`, `pillow_heif` and `imagehash`
adds 0.066 s on top. At module scope that is a ~36 % tax on EVERY pam command, every shell
completion and every `pam doctor`, and nothing in `pam scan`, `pam plan`, `pam apply`,
`pam undo` or `pam review` decodes a pixel - only `pam enrich` should pay for a decoder. `tests/test_deps.py::EAGER_IMPORT_CLOSURE` is
still `{click, pam}` because of this, and `tests/test_phash.py` re-proves it for this
module alone in a fresh interpreter: the closure test would catch a module-scope import
here, but it would report it as "the whole package got heavier", and the file that did it
is the thing worth naming.

`pillow_heif.register_heif_opener()` IS CALLED HERE RATHER THAN IN THE LOADER. The loader
is the runner's argument and this enricher does not own it; a `phash` that decoded HEIC
only when handed one particular loader would be a `phash` that silently skipped every HEIC
photograph under any other one. Registration is a global, idempotent mutation of
Pillow's format registry, so doing it before every `image(file)` costs a dictionary lookup
after the first call.

WHAT NO TEST IN THIS PROJECT PROVES, STATED HERE RATHER THAN DISCOVERED LATER: that a REAL
HEIC file decodes. `tests/tiny_library/`'s HEIC files are ISO-BMFF containers with the
right brand and no coded image in them, which is a limit of writing a library by hand and
is recorded in its README. What the suite proves is that the opener is registered and that
`.heic` is a format Pillow will attempt. The decode itself is HT-M9 Leg A's, on a real-world
library.
"""

import importlib
import logging
from collections.abc import Mapping
from typing import Any

from pam.enrich.base import FileRecord, InferredFact, PILImageLoader

NAME = "phash"

# The model identity the runner's cache and resume are keyed on. dHash is an algorithm and
# not a model, so `model_name` names the algorithm and `model_version` moves when the
# OUTPUT would move - a different `HASH_SIZE`, a different resampling, a different library.
# It is not `imagehash.__version__`: a patch release that changes nothing about dHash would
# invalidate the whole archive's cache and re-run 20,000 decodes for identical answers.
MODEL_NAME = "dhash"
MODEL_VERSION = "1.0"

# M10 TASK 6 DID NOT BUMP `MODEL_VERSION`, AND THE REASON IS THE COMMENT ABOVE. Amendment
# A4 changed what the loader hands this enricher for a RAW file - the camera's embedded
# preview instead of an `UnidentifiedImageError` - so the OUTPUT for a RAW file did move,
# from nothing to a hash. It moved FROM NOTHING: every file that branch reaches failed,
# and a failed file stores no fact, so there is no cached answer to invalidate and no
# archive that re-enriches. Bumping it would re-run twenty thousand decodes to arrive at
# identical answers for every file that already has one, which is the exact trade the
# paragraph above forbids. NOT DECODABLE -> DECODABLE IS A GAINED FILE, NEVER A CHANGED
# HASH; the day the CHAIN's order changes and a RAW's hash moves from one real rendering
# to another, this is the constant that has to move with it.

# dHash's grid. 8 gives the 64-bit hash the whole near-dupe literature is calibrated on,
# and 16 hex characters in `value_json`. Changing it changes every hash, which is what
# MODEL_VERSION is for.
HASH_SIZE = 8
HASH_BITS = HASH_SIZE * HASH_SIZE

# Per-image cost for the ETA's prior (design 8.1). A decode plus a resize on this machine
# is single-digit milliseconds for a JPEG and more for a HEIC; the runner replaces this
# with a measured moving average after twenty files, so it only has to be the right order
# of magnitude.
COST_ESTIMATE_MS = 25

# (distribution, import name). The distribution name is what a user types to install it,
# so it is what `available()` must say - `pip install pillow_heif` is not the sentence.
_REQUIREMENTS = (
    ("Pillow", "PIL.Image"),
    ("pillow-heif", "pillow_heif"),
    ("imagehash", "imagehash"),
)


class PhashUnavailableError(RuntimeError):
    """One of design 1's three image dependencies is not importable. Carries the reason."""


def _load() -> tuple[Any, Any]:
    """Import Pillow, pillow-heif and imagehash, register the HEIF opener, and return two.

    Raises `PhashUnavailableError` naming the DISTRIBUTION rather than letting an
    `ImportError` for `pillow_heif` reach a user who has never heard the name.
    """
    modules = {}
    for distribution, module_name in _REQUIREMENTS:
        try:
            modules[module_name] = importlib.import_module(module_name)
        except ImportError as error:
            raise PhashUnavailableError(
                f"{distribution} is not installed ({error}); pam doctor lists it"
            ) from error
    modules["pillow_heif"].register_heif_opener()
    return modules["PIL.Image"], modules["imagehash"]


class PhashEnricher:
    """Design 8.1's `phash`. Satisfies `enrich.Enricher`; the runner owns everything else."""

    name = NAME
    model_name = MODEL_NAME
    model_version = MODEL_VERSION

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger("pam")

    def available(self) -> tuple[bool, str]:
        """Design 8.1's dependency check. A missing package is a sentence, never a crash."""
        try:
            _load()
        except PhashUnavailableError as error:
            return False, str(error)
        return True, f"dHash {HASH_BITS}-bit over Pillow; HEIC support registered"

    def cost_estimate_ms(self) -> int:
        return COST_ESTIMATE_MS

    def wants(self, file: FileRecord) -> bool:
        """Images that are really on this disk. Two refusals, each for its own reason.

        A CLOUD PLACEHOLDER IS REFUSED BECAUSE READING ONE HYDRATES IT, which vision R2
        forbids absolutely - the file is a pointer, and opening it makes OneDrive download
        gigabytes the user did not ask for. This is a decision about the ACT of reading,
        not about the answer, so `tests/test_phash.py` watches the open rather than the
        hash: a refusal that read the bytes and then discarded them would look identical
        from the outside (M8's M697 is the precedent).

        VIDEOS ARE REFUSED because design 8.1 rev 3 says every pixel-reading enricher does:
        no video decoding exists in v1.0. It is written as an ALLOWLIST of `image` rather
        than a denylist of `video` so that a media type nobody has thought of yet - and
        `sidecar` and `other`, which pam does not hash at all - default to not being fed to
        a decoder.
        """
        if file.placeholder:
            return False
        return file.media_type == "image"

    def enrich(self, file: FileRecord, image: PILImageLoader) -> list[InferredFact]:
        """One dHash. `image` is the runner's loader; this never opens a path itself.

        No confidence: a dHash is a measurement, not an inference, and design 6.4 says
        confidence is NULL or in [0, 1]. NULL is the honest value and the tier config's
        `max_tier_when_no_confidence` is what turns it into a tier.
        """
        _, imagehash_module = _load()
        with image(file) as decoded:
            digest = imagehash_module.dhash(decoded, hash_size=HASH_SIZE)
        return [InferredFact(kind="phash", value={"hash": str(digest)}, confidence=None)]


def hamming_distance(left: str, right: str) -> int:
    """Bits differing between two `phash` hex digests (design 8.1's "hamming distance").

    Hex in, integer out, with no `imagehash` import: this is the report-time half of design
    8.1 and it runs against strings read out of `inferred_facts.value_json`, where a hash
    is text. Rebuilding an `ImageHash` object to subtract two of them would make a pure
    comparison depend on the decoder.

    Two digests of different lengths are a programming error rather than a distance of
    whatever the longer one has left, so it raises.
    """
    if len(left) != len(right):
        raise ValueError(f"hash widths differ: {len(left)} != {len(right)}")
    return bin(int(left, 16) ^ int(right, 16)).count("1")


def near_dupe_groups(hashes: Mapping[int, str], *, max_distance: int) -> list[frozenset[int]]:
    """Design 8.1's near-dupe groups, DERIVED and never stored.

    `hashes` is id -> hex digest; the result is every group of two or more ids that hang
    together under "hamming distance <= max_distance", transitively, ordered by each group's
    smallest id. Singletons are dropped: a group of one is not a duplicate of anything and a
    caller that wanted the partition already has the input.

    TRANSITIVE, AND THAT IS A CHOICE WITH A COST. A and B within the threshold, B and C
    within it, A and C outside it - all three still land in one group. The alternative is
    reporting overlapping pairs, which asks a user to reconcile "these two are duplicates"
    three times for one burst of shots. A burst IS one group, so the union-find reading is
    the one that matches what the user is looking at; the price is that a long chain of
    small differences can walk a group somewhere its first member would not recognise, and
    a report showing groups must show the images.

    EXACT, NOT APPROXIMATE (amendment H2): the groups equal `_near_dupe_groups_all_pairs`,
    the definition, for every input. The search is the pigeonhole one. Split the
    `HASH_BITS`-bit hash into `max_distance + 1` contiguous bands; two hashes at most
    `max_distance` bits apart differ in at most `max_distance` bands, so they agree EXACTLY
    on at least one. Only ids that share a (band, value) bucket are compared, and each
    comparison is still the real `hamming_distance <= max_distance` - a shared band is a
    reason to look, never a verdict.

    THE GAIN IS ABOUT TEN TIMES AT THE DEFAULT DISTANCE, NOT MORE, and it is arithmetic,
    not a promise: at 8, nine bands of about seven bits each make roughly 6 % of the pairs
    of uniform random hashes candidates, and real dHashes cluster, so they do worse. It
    is still O(n^2) in the worst case - an archive of near-identical images is all one
    bucket - because that is what an exact threshold search costs. HT-M13 Leg C records
    what `pam report duplicates` costs on a real-world library. At `max_distance >=
    HASH_BITS` every pair qualifies and no band can be empty, so the definition runs as is.
    So it does for a digest that is not `HASH_BITS` wide: the definition refuses one by
    raising in `hamming_distance`, and a search that never compared it must not quietly
    accept it.
    """
    if max_distance < 0:
        raise ValueError(f"max_distance must not be negative: {max_distance}")
    width = HASH_BITS // 4
    if max_distance >= HASH_BITS or any(len(h) != width for h in hashes.values()):
        return _near_dupe_groups_all_pairs(hashes, max_distance=max_distance)
    parent: dict[int, int] = {node: node for node in hashes}

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    ids = sorted(hashes)
    values = {node: int(hashes[node], 16) for node in ids}
    for shift, bits in _bands(max_distance + 1):
        mask = (1 << bits) - 1
        buckets: dict[int, list[int]] = {}
        for node in ids:
            buckets.setdefault((values[node] >> shift) & mask, []).append(node)
        for members in buckets.values():
            for index, left in enumerate(members):
                for right in members[index + 1 :]:
                    root_left, root_right = find(left), find(right)
                    # Already one group: the comparison could only confirm it.
                    if root_left == root_right:
                        continue
                    if hamming_distance(hashes[left], hashes[right]) <= max_distance:
                        parent[max(root_left, root_right)] = min(root_left, root_right)

    groups: dict[int, set[int]] = {}
    for node in ids:
        groups.setdefault(find(node), set()).add(node)
    return [frozenset(members) for _, members in sorted(groups.items()) if len(members) > 1]


def _bands(count: int) -> list[tuple[int, int]]:
    """(shift, width) of `count` contiguous bands tiling `HASH_BITS`, widths differing by <= 1."""
    base, extra = divmod(HASH_BITS, count)
    bands, shift = [], 0
    for index in range(count):
        bits = base + (1 if index < extra else 0)
        bands.append((shift, bits))
        shift += bits
    return bands


def _near_dupe_groups_all_pairs(
    hashes: Mapping[int, str], *, max_distance: int
) -> list[frozenset[int]]:
    """THE DEFINITION (design 8.1, H2): every pair compared, transitive, by smallest id.

    Kept as the oracle `near_dupe_groups` is tested against, and as its fallback where no
    band can help. Not otherwise called by production: O(n^2) comparisons always.
    """
    if max_distance < 0:
        raise ValueError(f"max_distance must not be negative: {max_distance}")
    parent: dict[int, int] = {file_id: file_id for file_id in hashes}

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    ids = sorted(hashes)
    for index, left in enumerate(ids):
        for right in ids[index + 1 :]:
            if hamming_distance(hashes[left], hashes[right]) <= max_distance:
                low, high = sorted((find(left), find(right)))
                parent[high] = low

    groups: dict[int, set[int]] = {}
    for file_id in ids:
        groups.setdefault(find(file_id), set()).add(file_id)
    return [frozenset(members) for _, members in sorted(groups.items()) if len(members) > 1]
