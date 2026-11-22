"""Design 8.1's `phash` enricher (M9 Task 8): dHash, the two refusals, and the derivation.

THE REFUSAL TESTS WATCH THE OPEN, NOT THE ANSWER. A `wants()` that returned False after
reading the file would be indistinguishable from a correct one by any assertion about the
result - which is M8's M697 exactly: a mis-ordered refusal hydrated a cloud placeholder,
returned the same 404 the correct order returns, and 1718 tests stayed green. So
`test_a_cloud_placeholder_is_never_OPENED_...` wraps `builtins.open` (which is the call
`PIL.Image.open` makes) and asserts the placeholder's path never appears in the record,
while a real image's path does - the second half being the positive control that proves
the wrapper can see anything at all.

WHAT THE DISTANCES ARE SPELLED FROM. `tools/build_tiny_library.phash_rows()` says what its
three fixtures are: a ramp, the same ramp with one 4x4 block inverted, and the ramp
photometrically inverted. dHash compares each pixel with its right-hand neighbour, so a
full inversion flips every comparison and the distance to `base` is the whole width of the
hash. Those numbers are written out below as literals; nothing here asks the enricher what
it thinks the distance is and then agrees with it.

WHAT NO TEST HERE CAN PROVE: that a REAL HEIC decodes. The library's HEIC files are
containers with no coded image in them and Pillow cannot open one, which is asserted below
so that nobody reads a green run as HEIC coverage. Most phone photographs are HEIC.
HT-M9 Leg A is the only thing that reaches one.
"""

import builtins
import io
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import build_tiny_library  # noqa: E402

from pam import config  # noqa: E402
from pam.catalog import repo  # noqa: E402
from pam.catalog.db import connect, migrate  # noqa: E402
from pam.enrich import run_enricher  # noqa: E402
from pam.enrich.base import (  # noqa: E402
    RAW_EXTS,
    FileRecord,
    PreviewUnavailableError,
    pil_image_loader,
)
from pam.enrich.phash import (  # noqa: E402
    PhashEnricher,
    PhashUnavailableError,
    hamming_distance,
    near_dupe_groups,
)
from pam.enrich.validate import fact_error  # noqa: E402
from pam.errors import ConfigError  # noqa: E402
from pam.scan import exiftool  # noqa: E402

NOW = "2026-08-28T00:00:00"

LIBRARY = Path(__file__).resolve().parent / "tiny_library"

# Design 8.1's three enricher-interface attributes, written from the spec's own Protocol
# block rather than read off the class. A class that renamed `model_version` would satisfy
# `hasattr(cls, attr) for attr in cls.__annotations__` and fail this.
SPEC_ATTRIBUTES = ("name", "model_name", "model_version")
SPEC_METHODS = ("available", "cost_estimate_ms", "wants", "enrich")

# The three phash fixtures and what the builder's table says about them. `base` and `near`
# are the same picture; `far` is `base` inverted, and inverting every pixel flips every one
# of dHash's 64 neighbour comparisons.
BASE, NEAR, FAR = (
    "phash_pattern_base.png",
    "phash_pattern_near.png",
    "phash_pattern_far.png",
)
DISTANCE_BASE_NEAR = 2
DISTANCE_BASE_FAR = 64
DISTANCE_NEAR_FAR = 62

# The width design 6.4's `phash` row implies for an 8x8 dHash: 64 bits, 16 hex digits.
HEX_DIGITS = 16


def record(
    rel_path: str,
    *,
    media_type: str = "image",
    placeholder: bool = False,
    file_id: int = 1,
) -> FileRecord:
    """A `FileRecord` for `wants()`. Only four of its fields matter to this enricher."""
    return FileRecord(
        id=file_id,
        rel_path=rel_path,
        filename=rel_path.rsplit("/", 1)[-1],
        ext=rel_path.rsplit(".", 1)[-1].lower(),
        media_type=media_type,
        size=1024,
        mtime_ns=1,
        sha256=f"sha-{rel_path}",
        status="present",
        placeholder=placeholder,
        companion_group_id=None,
    )


def library_hash(name: str) -> str:
    """The dHash of one committed fixture, through the shipped enricher and loader."""
    facts = PhashEnricher().enrich(record(name), pil_image_loader(LIBRARY))
    return str(facts[0].value["hash"])


# --- 1. the interface design 8.1 declares ---------------------------------------------


def test_the_enricher_carries_exactly_the_interface_spec_8_1_declares():
    """Spelled from the spec's Protocol block, not from the class's own annotations."""
    enricher = PhashEnricher()
    for attribute in SPEC_ATTRIBUTES:
        assert isinstance(getattr(enricher, attribute), str), attribute
    for method in SPEC_METHODS:
        assert callable(getattr(enricher, method)), method
    assert enricher.name == "phash", "spec 8.1's `name` vocabulary is a closed list"
    assert repo.fact_kinds(enricher.name) == ("phash",)
    assert enricher.cost_estimate_ms() > 0, "the ETA's prior cannot be zero or negative"


def test_available_is_true_here_and_says_what_it_checked():
    ok, reason = PhashEnricher().available()
    assert ok is True
    assert reason, "spec 8.1 asks for a reason, and an empty one tells a user nothing"


def test_available_names_the_DISTRIBUTION_when_a_dependency_is_missing(monkeypatch):
    """A missing `pillow-heif` is a sentence, never an ImportError deep in a run.

    The message must name what a user can INSTALL. `pip install pillow_heif` is not a
    command anybody should have to guess at from an ImportError for a module name.
    """
    import importlib

    real = importlib.import_module

    def refuse(name, *args, **kwargs):
        if name == "pillow_heif":
            raise ImportError("No module named 'pillow_heif'")
        return real(name, *args, **kwargs)

    monkeypatch.setattr("pam.enrich.phash.importlib.import_module", refuse)
    ok, reason = PhashEnricher().available()
    assert ok is False
    assert "pillow-heif" in reason, reason
    assert "doctor" in reason, "the reason should point at the command that lists them"


def test_a_missing_dependency_raises_a_named_error_rather_than_an_ImportError(monkeypatch):
    """`enrich()` must fail in a way the runner's logging can explain, not with a bare name."""
    import importlib

    real = importlib.import_module

    def refuse(name, *args, **kwargs):
        if name == "imagehash":
            raise ImportError("No module named 'imagehash'")
        return real(name, *args, **kwargs)

    monkeypatch.setattr("pam.enrich.phash.importlib.import_module", refuse)
    with pytest.raises(PhashUnavailableError, match="imagehash"):
        PhashEnricher().enrich(record(BASE), pil_image_loader(LIBRARY))


# --- 2. wants(): the two refusals ------------------------------------------------------


# Design 4's `files.media_type` column comment is the vocabulary: 'image' | 'video' |
# 'sidecar' | 'other'. Written out from the SPEC rather than read off `walker.classify`,
# so a fifth media type would fail here instead of quietly not being asked about.
SPEC_MEDIA_TYPES = ("image", "video", "sidecar", "other")

# The answer for each, BY HAND - not `(kind, kind == "image")`, which would be the
# enricher's own rule restated and would agree with any allowlist it was given.
WANTS_BY_MEDIA_TYPE = (
    ("image", True),
    ("video", False),
    ("sidecar", False),
    ("other", False),
)
# M9 Task 8's review, FINDING 3: narrowing this table to `[("image", True)]` lost all three
# refusal rows with 2462 tests green, because pytest reports fewer cases and never a
# failure. A collection-time assert catches an EMPTIED parametrize; only comparing it with
# an independently written list catches a NARROWED one.
assert tuple(kind for kind, _ in WANTS_BY_MEDIA_TYPE) == SPEC_MEDIA_TYPES, (
    "the wants() table no longer covers design 4's media_type vocabulary exactly"
)


@pytest.mark.parametrize(("media_type", "wanted"), WANTS_BY_MEDIA_TYPE)
def test_wants_serves_images_and_refuses_every_other_media_type(media_type, wanted):
    """Spec 8.1 rev 3: a pixel enricher returns False for `media_type='video'`.

    The other two rows are here because pam does not even hash a sidecar or an `other`
    file, so there is nothing to file a fact against - and because a denylist of `video`
    would pass this test's video row while feeding a `.txt` to a decoder.
    """
    assert PhashEnricher().wants(record(f"x.{media_type}", media_type=media_type)) is wanted


def test_wants_refuses_a_cloud_placeholder_even_though_it_is_an_image():
    """Vision R2. The placeholder is an image by every other measure, and still refused."""
    enricher = PhashEnricher()
    assert enricher.wants(record("IMG_1.HEIC", placeholder=True)) is False
    assert enricher.wants(record("IMG_1.HEIC", placeholder=False)) is True


def test_wants_serves_heic_because_167_of_the_owners_389_real_files_are_heic():
    """A `phash` that refused HEIC by policy would look like a working enricher.

    This is the POLICY half. Whether a real HEIC actually decodes is HT-M9 Leg A's, and
    the test below says so out loud.
    """
    assert PhashEnricher().wants(record("IMG_0001.HEIC")) is True
    assert PhashEnricher().wants(record("IMG_0001.heic")) is True


# --- 3. the placeholder refusal, watched at the OPEN -----------------------------------


def _catalog_with_a_placeholder(tmp_path):
    """An archive of real files: one ordinary PNG and one PNG the catalog marks as a placeholder."""
    root = tmp_path / "archive"
    root.mkdir()
    for name in (BASE, NEAR):
        (root / name).write_bytes((LIBRARY / name).read_bytes())
    db_path = tmp_path / "catalog.db"
    conn = connect(db_path)
    migrate(conn)
    for name, placeholder in ((BASE, False), (NEAR, True)):
        file_id = repo.upsert_file(
            conn,
            rel_path=name,
            filename=name,
            ext="png",
            media_type="image",
            size=(root / name).stat().st_size,
            mtime_ns=1,
            placeholder=placeholder,
            now=NOW,
        )
        repo.set_sha256(conn, file_id, f"sha-{name}")
    conn.commit()
    return root, conn


def test_a_cloud_placeholder_is_never_OPENED_and_the_watch_proves_it_can_see(
    tmp_path, monkeypatch
):
    """Vision R2, asserted against the CALL. Reading a placeholder hydrates it.

    An assertion that "no fact was written for the placeholder" cannot tell "never read
    it" from "read it and threw the answer away", and hydrating a file is done by the
    time the answer is thrown away - the download has happened. So this records every
    `builtins.open`, which is the call `PIL.Image.open` makes, and asserts:

      * the placeholder's absolute path is NOT in the record - the property under test;
      * the ordinary image's absolute path IS - the control, without which a wrapper that
        recorded nothing at all would pass the first assertion.
    """
    root, conn = _catalog_with_a_placeholder(tmp_path)
    opened: list[str] = []
    real_open = builtins.open

    def watching_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", watching_open)
    try:
        summary = run_enricher(conn, PhashEnricher(), pil_image_loader(root), workers=1)
    finally:
        monkeypatch.setattr(builtins, "open", real_open)

    assert summary.skipped_wants == 1 and summary.enriched == 1
    assert str(root / BASE) in opened, (
        "the watch recorded nothing for a file that WAS read - it cannot see, so its"
        " silence about the placeholder means nothing"
    )
    assert str(root / NEAR) not in opened, (
        "pam opened a cloud placeholder; reading one hydrates it (vision R2)"
    )
    conn.close()


# --- 4. the hash itself ----------------------------------------------------------------


def test_enrich_returns_one_well_formed_phash_fact_that_spec_6_4_accepts():
    """A fact spec 6.4 rejects is a fact the runner drops - the enricher would emit nothing.

    `fact_error` is asserted directly rather than inferred from a green run, because the
    runner logs a drop and carries on: a wrong `value_json` shape would show up as an
    archive with no phash facts in it and a summary that said so in a field nobody read.
    """
    facts = PhashEnricher().enrich(record(BASE), pil_image_loader(LIBRARY))
    assert len(facts) == 1
    fact = facts[0]
    assert fact.kind == "phash"
    assert fact.confidence is None, "a measurement has no confidence; spec 6.4 allows NULL"
    assert set(fact.value) == {"hash"}
    digest = fact.value["hash"]
    assert isinstance(digest, str) and len(digest) == HEX_DIGITS
    int(digest, 16)  # raises if it is not hex
    assert fact_error("phash", fact.value, fact.confidence) is None


def test_the_three_library_fixtures_hash_to_the_distances_the_builder_declares():
    """The near-dupe claim, measured over committed bytes.

    The three numbers are written out from `build_tiny_library.phash_rows()`'s docstring:
    `near` is `base` with one 4x4 block inverted, and `far` is `base` with EVERY pixel
    inverted - which flips all 64 of dHash's neighbour comparisons.

    The distances are pinned rather than the hashes. A hash pins Pillow's resampling as
    well as pam's, and a Pillow release that changed a default filter would fail this file
    for a reason that has nothing to do with pam; the RELATIONSHIPS between three images
    survive that.
    """
    base, near, far = library_hash(BASE), library_hash(NEAR), library_hash(FAR)
    assert len({base, near, far}) == 3, "three different pictures, three different hashes"
    assert hamming_distance(base, near) == DISTANCE_BASE_NEAR
    assert hamming_distance(base, far) == DISTANCE_BASE_FAR
    assert hamming_distance(near, far) == DISTANCE_NEAR_FAR


def test_the_hash_is_a_statement_about_pixels_and_not_about_metadata():
    """Two files whose bytes differ only in their description must still hash alike.

    `phash_pattern_base.png` and a copy of it carrying a different tEXt chunk are the same
    PICTURE. If the enricher were hashing bytes it would be a worse `sha256`, which spec
    8.1 says is the tool for exact duplicates.
    """
    same_pixels = build_tiny_library.png(
        exif=None,
        description="a different description entirely",
        pixels=build_tiny_library.phash_rows("base"),
    )
    assert same_pixels != (LIBRARY / BASE).read_bytes(), "the fixture must differ in bytes"
    assert hamming_distance(library_hash(BASE), _hash_bytes(same_pixels)) == 0


def _hash_bytes(payload: bytes) -> str:
    """The dHash of an in-memory PNG, through the shipped enricher and a one-file loader."""
    import io

    from PIL import Image

    def loader(_file):
        image = Image.open(io.BytesIO(payload))
        image.load()
        return image

    return str(PhashEnricher().enrich(record("in-memory.png"), loader)[0].value["hash"])


def test_a_file_pillow_cannot_decode_fails_the_file_and_not_the_run(tmp_path):
    """Spec 8.1's runner counts an unreadable file as failed. The corrupt JPEG proves it.

    Design 9.1 puts a corrupt JPEG in the library for exactly this; before M9 nothing
    decoded, so nothing had ever asked it to fail.
    """
    root = tmp_path / "archive"
    root.mkdir()
    corrupt = "corrupt_broken.jpg"
    (root / corrupt).write_bytes((LIBRARY / corrupt).read_bytes())
    (root / BASE).write_bytes((LIBRARY / BASE).read_bytes())
    conn = connect(tmp_path / "catalog.db")
    migrate(conn)
    for name in (corrupt, BASE):
        file_id = repo.upsert_file(
            conn,
            rel_path=name,
            filename=name,
            ext=name.rsplit(".", 1)[1],
            media_type="image",
            size=(root / name).stat().st_size,
            mtime_ns=1,
            placeholder=False,
            now=NOW,
        )
        repo.set_sha256(conn, file_id, f"sha-{name}")
    conn.commit()
    summary = run_enricher(conn, PhashEnricher(), pil_image_loader(root), workers=1)
    assert (summary.failed, summary.enriched) == (1, 1)
    assert summary.facts_written == 1
    conn.close()


# --- 5. HEIC: what is proved, and what is explicitly NOT ------------------------------


def test_reading_a_file_registers_the_heif_opener_with_pillow():
    """Asked of Pillow, not of a spy on `register_heif_opener`.

    "The function was called" and "the function had its effect" are two different claims,
    and only the second one is worth anything: `register_heif_opener` belongs to a package
    this project does not own, and a spy would stay green if a release stopped registering
    anything. `registered_extensions()` is Pillow's own answer to "will you try a .heic".

    IT RUNS IN A FRESH INTERPRETER, AND THAT WAS MEASURED RATHER THAN ARGUED (M10 close,
    M1672). Registration is a global, process-wide mutation of Pillow's registry, so inside
    one pytest process the FIRST test to register satisfies every later assertion about it.
    Written in-process - which is how it stood from M9 until the M10 close - deleting
    `PhashEnricher._load`'s `register_heif_opener()` outright left **all 2962 tests green**,
    because by then `pil_image_loader` registered the opener for the whole run. The test
    asserted a true fact about Pillow and nothing whatever about phash.

    WHAT IT PINS, AND WHY IT IS NOT REDUNDANT WITH SECTION 5a. `phash.py` registers the
    opener ITSELF rather than relying on the loader, because `run_enricher` takes
    `image_loader` as a required argument and this enricher must decode HEIC under ANY
    loader it is handed - its own docstring's argument, and a sound one. Section 5a pins the
    opposite direction: that the loader pam ships registers for enrichers that are NOT
    phash. Neither subsumes the other, and M1672 is the proof that this one was not being
    pinned at all.
    """
    assert _registers_heic(
        "from pam.enrich.phash import PhashEnricher\nPhashEnricher().available()"
    ), (
        "PhashEnricher.available() does not register the HEIF opener, so phash decodes HEIC"
        " only when handed pam's own loader - which its module docstring is explicit it must"
        " not depend on."
    )


def test_the_tiny_librarys_HEIC_files_do_NOT_decode_and_are_not_heic_coverage():
    """A control against a false reading of a green suite, not a test of pam.

    `tests/tiny_library/`'s HEIC files are ISO-BMFF containers with the right brand and no
    coded image in them - writing a real one needs a HEIF encoder, which is a dependency
    the library refuses (design 9.1). This asserts they are UNREADABLE so that nobody
    later points at a passing phash run over the library and calls HEIC covered. Most phone
    photographs are HEIC and HT-M9 Leg A is what reaches one.

    It cannot skip: if a future pillow-heif learns to decode these containers, this test
    goes red and the sentence above needs rewriting, which is the correct outcome.
    """
    from PIL import Image, UnidentifiedImageError

    PhashEnricher().available()
    heic = LIBRARY / "2022-02-02_extcase_heic_lower.heic"
    assert heic.exists()

    # THE SUBJECT IS PINNED, which M9 Task 8's review FINDING 4 found it was not: aimed at
    # `corrupt_broken.jpg` instead, this test passed with 2465 green, and a negative test
    # that does not know what it is looking at claims nothing. Two pins, both from the
    # spec side. (a) the file really is an ISO-BMFF HEIC container, read from its own
    # `ftyp` box; (b) Pillow WILL attempt a `.heic`, so the failure below is about the
    # missing coded image and not about pam never having registered the format - which is
    # the difference between "the library cannot cover HEIC" and "HEIC is broken".
    header = heic.read_bytes()[:16]
    assert header[4:8] == b"ftyp" and header[8:12] == b"heic", (
        "the fixture this test names is no longer a HEIC container"
    )
    assert ".heic" in Image.registered_extensions()

    with pytest.raises((UnidentifiedImageError, OSError, ValueError)):
        with Image.open(heic) as image:
            image.load()

    # THE POSITIVE TWIN. Without it, "nothing in this library opens" would satisfy the
    # assertion above just as well as "this container has no coded image in it".
    with Image.open(LIBRARY / BASE) as control:
        control.load()
        assert control.size == (32, 32)


# --- 6. the import cost, measured rather than asserted ---------------------------------


def test_importing_this_module_does_not_import_pillow_or_imagehash():
    """The deferral, proved for THIS module in a fresh interpreter.

    `tests/test_deps.py::EAGER_IMPORT_CLOSURE` would also catch a module-scope import
    here, and it is the gate that matters for the package as a whole. This one names the
    file: the closure test reports "the package got heavier" and leaves whoever reads it
    to find out which of sixty-nine modules did it. Measured at Task 8, best of seven:
    `pam --version` costs 0.181 s and these three imports add 0.066 s to whatever imports
    them.
    """
    probe = (
        "import sys, pam.enrich.phash;"
        "print(' '.join(n for n in ('PIL','pillow_heif','imagehash') if n in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
        timeout=120,
        check=True,
    )
    assert result.stdout.split() == [], (
        "pam/enrich/phash.py imports a decoder at module scope; every pam command now"
        f" pays for it. Resident: {result.stdout.strip()}"
    )


# --- 7. the derivation spec 8.1 says is NOT stored -------------------------------------


@pytest.mark.parametrize(
    ("left", "right", "bits"),
    [
        ("0000000000000000", "0000000000000000", 0),
        ("0000000000000000", "0000000000000001", 1),
        ("0000000000000000", "0000000000000003", 2),
        ("0000000000000000", "ffffffffffffffff", 64),
        ("00000000ffffffff", "ffffffff00000000", 64),
        ("8000000000000000", "0000000000000000", 1),
    ],
)
def test_hamming_distance_counts_the_bits_that_differ(left, right, bits):
    """The definition, by hand. A second implementation of dHash would prove nothing here."""
    assert hamming_distance(left, right) == bits
    assert hamming_distance(right, left) == bits


def test_hamming_distance_refuses_two_hashes_of_different_widths():
    """A short digest is a bug upstream, not a distance of "whatever is left over"."""
    with pytest.raises(ValueError, match="widths differ"):
        hamming_distance("00", "0000")


def test_near_dupe_groups_returns_groups_of_two_or_more_within_the_threshold():
    """Spec 8.1: groups by "hamming distance <= threshold", derived and never stored."""
    hashes = {
        1: "0000000000000000",
        2: "0000000000000003",  # 2 bits from 1
        3: "ffffffffffffffff",  # 64 bits from 1
    }
    assert near_dupe_groups(hashes, max_distance=2) == [frozenset({1, 2})]
    assert near_dupe_groups(hashes, max_distance=1) == [], "a singleton is not a group"
    assert near_dupe_groups(hashes, max_distance=64) == [frozenset({1, 2, 3})]


def test_the_threshold_is_inclusive_which_is_what_the_spec_sentence_says():
    """ "<= threshold". A distance EQUAL to the threshold groups; one more does not."""
    hashes = {1: "0000000000000000", 2: "0000000000000003"}
    assert near_dupe_groups(hashes, max_distance=2) == [frozenset({1, 2})]
    assert near_dupe_groups(hashes, max_distance=1) == []


def test_near_dupe_groups_are_transitive_and_that_is_the_documented_choice():
    """A and B close, B and C close, A and C not: one group of three, not two pairs.

    Written down because it is a decision and not an accident - a burst of shots is one
    thing the user is looking at, not three overlapping pairs to reconcile.
    """
    hashes = {
        1: "0000000000000000",
        2: "0000000000000003",  # 2 from 1
        3: "000000000000000f",  # 2 from 2, 4 from 1
    }
    assert hamming_distance(hashes[1], hashes[3]) == 4
    assert near_dupe_groups(hashes, max_distance=2) == [frozenset({1, 2, 3})]


def test_near_dupe_groups_over_the_real_fixtures_at_the_configured_default():
    """End to end: three committed pictures, real decodes, the shipped default threshold.

    The expectation is spelled from the builder's table - `base` and `near` are the same
    picture and `far` is not - and not from whatever the grouping happened to return.
    """
    threshold = config.Config().phash_near_dupe_distance
    hashes = {1: library_hash(BASE), 2: library_hash(NEAR), 3: library_hash(FAR)}
    assert near_dupe_groups(hashes, max_distance=threshold) == [frozenset({1, 2})]


# The star, spelled by hand. `centre` is within the threshold of BOTH outliers and the two
# outliers are outside it of each other - which is transitivity again, but with the shared
# member NOT in the middle of the id order. `test_..._are_transitive_...` above cannot tell
# a correct union-find from a merge that skips its `find()`s, because its fixture is a
# CHAIN whose middle element is id 2 and the pair order visits it while it is still its own
# root. This is the M9 Task 8 review's FINDING 1 (M1258): `parent[find(right)] = find(left)`
# -> `parent[right] = left` passed all 2462 tests and drops a member of a real group.
STAR_OUTLIER_A = "0000000000000000"
STAR_OUTLIER_B = "000000000000000f"  # 4 bits from A - outside a threshold of 2
STAR_CENTRE = "0000000000000003"  # 2 bits from A, 2 bits from B - inside it of both
STAR_THRESHOLD = 2


def test_a_shared_NEIGHBOUR_joins_two_files_that_are_far_from_each_other():
    """One group of three, and the expected group is written out, not derived.

    The distances are asserted first so the fixture cannot silently stop being a star.
    """
    hashes = {1: STAR_OUTLIER_A, 2: STAR_OUTLIER_B, 3: STAR_CENTRE}
    assert hamming_distance(hashes[1], hashes[2]) > STAR_THRESHOLD
    assert hamming_distance(hashes[1], hashes[3]) <= STAR_THRESHOLD
    assert hamming_distance(hashes[2], hashes[3]) <= STAR_THRESHOLD
    assert near_dupe_groups(hashes, max_distance=STAR_THRESHOLD) == [frozenset({1, 2, 3})]


def transitive_closure(hashes, max_distance):
    """Spec 8.1's grouping, written a SECOND time and from the sentence, not the code.

    "near-dupe groups derived at report/review time by hamming distance <= threshold" is a
    connected-component problem; this is the breadth-first reading of it, with no union-find
    anywhere. It exists to catch asymmetric drift in `near_dupe_groups`' clever half - and,
    per this project's M922/M924 lesson, it is NOT the only thing standing behind the
    property: `test_a_shared_NEIGHBOUR_...` above spells one answer out by hand, and a
    co-mutation of both implementations would still have to get past that.
    """
    ids = sorted(hashes)
    seen: set[int] = set()
    groups = []
    for start in ids:
        if start in seen:
            continue
        component: set[int] = set()
        stack = [start]
        while stack:
            node = stack.pop()
            if node in component:
                continue
            component.add(node)
            for other in ids:
                if other in component:
                    continue
                if hamming_distance(hashes[node], hashes[other]) <= max_distance:
                    stack.append(other)
        seen |= component
        if len(component) > 1:
            groups.append(frozenset(component))
    return sorted(groups, key=min)


# Deterministic, seeded, and asserted non-empty at collection so an emptied or narrowed
# list cannot disarm this the way pytest silently skips an empty parametrize.
FUZZ_TRIALS = 400
assert FUZZ_TRIALS > 0, "a fuzz of zero cases is a test that does not run"


def test_near_dupe_groups_agrees_with_an_independent_transitive_closure():
    """400 seeded random cases, production against the second implementation.

    Both halves of FINDING 1 are caught here: dropping the `find()`s from the merge, and
    reducing `find()`'s loop to a single step. Neither is reachable at three ids, which is
    why every hand-written case in this file missed them.
    """
    rng = random.Random(20260828)
    discriminating = 0
    for _ in range(FUZZ_TRIALS):
        count = rng.randint(3, 8)
        hashes = {i + 1: format(rng.getrandbits(16), "016x") for i in range(count)}
        threshold = rng.randint(0, 6)
        expected = transitive_closure(hashes, threshold)
        assert sorted(near_dupe_groups(hashes, max_distance=threshold), key=min) == expected
        if any(len(group) > 2 for group in expected):
            discriminating += 1
    assert discriminating > 0, (
        "not one drawn case produced a group of three or more, so this run could not have"
        " told a correct union-find from a broken one - the generator has drifted"
    )


def test_near_dupe_groups_refuses_a_negative_threshold():
    with pytest.raises(ValueError, match="negative"):
        near_dupe_groups({1: "0" * 16}, max_distance=-1)


def test_nothing_about_a_near_dupe_group_reaches_the_catalog(tmp_path):
    """Spec 8.1: groups are derived at report time. The enricher writes ONE kind of row.

    A `near_dupe` table, or a group id on `inferred_facts`, would be a schema change M9
    has no migration for and a stored answer that goes stale the moment the threshold
    moves. This reads the catalog back after a real run and asserts the only rows are
    `phash` facts.
    """
    root = tmp_path / "archive"
    root.mkdir()
    for name in (BASE, NEAR, FAR):
        (root / name).write_bytes((LIBRARY / name).read_bytes())
    conn = connect(tmp_path / "catalog.db")
    migrate(conn)
    for name in (BASE, NEAR, FAR):
        file_id = repo.upsert_file(
            conn,
            rel_path=name,
            filename=name,
            ext="png",
            media_type="image",
            size=(root / name).stat().st_size,
            mtime_ns=1,
            placeholder=False,
            now=NOW,
        )
        repo.set_sha256(conn, file_id, f"sha-{name}")
    conn.commit()
    run_enricher(conn, PhashEnricher(), pil_image_loader(root), workers=1)

    kinds = [r["kind"] for r in conn.execute("SELECT kind FROM inferred_facts")]
    assert kinds == ["phash"] * 3
    values = [
        json.loads(r["value_json"])
        for r in conn.execute("SELECT value_json FROM inferred_facts")
    ]
    assert [sorted(v) for v in values] == [["hash"]] * 3, (
        "a phash row carries a hex digest under one key and nothing else"
    )
    tables = {
        r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert [t for t in tables if "dupe" in t] == [], (
        "near-dupe groups are derived at report time (spec 8.1); nothing stores them"
    )
    conn.close()


# --- 8. the configured threshold -------------------------------------------------------


def test_the_near_dupe_threshold_is_configurable_and_range_checked(tmp_path):
    """Design 8.1 says "threshold"; this project's thresholds live in `pam.toml`."""
    path = tmp_path / "pam.toml"
    path.write_text("[enrich.phash]\nnear_dupe_distance = 12\n", encoding="utf-8")
    assert config.load_config(path).phash_near_dupe_distance == 12

    path.write_text("[enrich.phash]\nnear_dupe_distance = 65\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="near_dupe_distance"):
        config.load_config(path)

    path.write_text("[enrich.phash]\nnear_dupe_distance = -1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="near_dupe_distance"):
        config.load_config(path)


# --- 9. the loader's RAW branch (M10 Task 6; design 8.1 rev 17 / amendment A4) ---------
#
# HT-M9's F1: half of the real `.NEF` files a human test tried failed `phash` with
# `UnidentifiedImageError`, silently, visible only in the log. Pillow has no RAW decoder
# and `pillow-heif` does not add one, so the loader now hands a pixel enricher the
# full-size preview the CAMERA embedded, pulled out by read-only exiftool.
#
# WHAT IS PROVED HERE and what is not. `tests/tiny_library/raw_preview.ARW` is the one RAW
# in the library with a coded image inside it; the other four are TIFF bodies with no
# pixels, which is what F1's shape looks like from the loader's side and is used as such
# below. Whether a REAL camera RAW carries a preview exiftool can read is HT-M10's, on the
# real `.NEF` files a human test tried.

needs_exiftool = pytest.mark.skipif(
    shutil.which("exiftool") is None, reason="the RAW branch needs the real exiftool"
)

# Spec 11's `scan.image_exts` default is
# ["jpg","jpeg","heic","heif","png","tif","tiff","cr2","nef","arw","dng"], and spec 8.1
# rev 17 names its RAW half as `.nef`, `.cr2`, `.arw`, `.dng`. WRITTEN OUT HERE FROM THE
# SPEC, never from `RAW_EXTS` itself: a test that asked the module what it thought would
# agree with any list it was given, which is this project's most repeated defect.
SPEC_RAW_EXTS = {"cr2", "nef", "arw", "dng"}
SPEC_DISPLAY_EXTS = {"jpg", "jpeg", "heic", "heif", "png", "tif", "tiff"}

# WHICH LINK OF THE CHAIN THE LOADER MUST RETURN, spelled from design 8.2's sentence
# ("`-b -PreviewImage` falling back to `-b -JpgFromRaw` then `-b -ThumbnailImage`") and
# from 8.1 rev 17, which hands that same chain to the RAW branch. Written here as the TAG
# NAME, not read off a run and not sliced out of `exiftool.PREVIEW_ATTEMPTS`.
SPEC_FIRST_PREVIEW_LINK = "PreviewImage"

RAW_WITH_PREVIEW = "raw_preview.ARW"
RAW_WITHOUT_PREVIEW = "raw_nikon.NEF"


def test_the_raw_extension_list_is_spec_11s_raw_half_and_nothing_else():
    """The branch is keyed on a DECLARED list, not on "whatever Pillow refused".

    A branch keyed on the failure it exists to prevent could not be tested for having the
    right members, and it would swallow a JPEG that failed to decode for some other
    reason - which is the very signal F1 was found by.
    """
    assert set(RAW_EXTS) == SPEC_RAW_EXTS
    assert set(RAW_EXTS) & SPEC_DISPLAY_EXTS == set()


@needs_exiftool
def test_a_raw_file_is_opened_as_the_preview_the_camera_embedded(tmp_path):
    """The fix for F1, on committed bytes: the RAW decodes, and it decodes as the PREVIEW.

    THE COMMENT ASSERTION IS THE ONE THAT ANSWERS "WHICH". `decoded.tobytes()` cannot:
    the fixture's three payloads are three different JPEGs, but they are three different
    ENCODINGS of one picture, so all three decode to one identical pixel buffer at one
    identical size - measured, `len({pixels}) == 1` - and every assertion about pixels
    passes for every ordering of the chain. Reordering the chain to try `-ThumbnailImage`
    first left this test GREEN (M10 review, M1508), which is exactly the M8/M993 shape: a
    test whose name claims a property its oracle cannot exercise.

    What separates the three IS the COM segment each carries, which Pillow surfaces as
    `info["comment"]`, so that is what is asserted - against a tag name written out from
    design 8.2 rather than sliced off the chain constant the loader itself uses.
    """
    from PIL import Image

    root = tmp_path / "archive"
    root.mkdir()
    (root / RAW_WITH_PREVIEW).write_bytes((LIBRARY / RAW_WITH_PREVIEW).read_bytes())
    payloads = build_tiny_library.raw_preview_payloads(RAW_WITH_PREVIEW)
    wanted = Image.open(io.BytesIO(payloads[SPEC_FIRST_PREVIEW_LINK]))
    wanted.load()

    with pil_image_loader(root)(record(RAW_WITH_PREVIEW)) as decoded:
        assert decoded.size == wanted.size
        assert decoded.tobytes() == wanted.tobytes()
        assert decoded.info.get("comment") == wanted.info.get("comment"), (
            "the loader returned some other link of the chain"
        )
        for tag, blob in payloads.items():
            if tag == SPEC_FIRST_PREVIEW_LINK:
                continue
            other = Image.open(io.BytesIO(blob))
            other.load()
            assert decoded.info.get("comment") != other.info.get("comment"), (
                f"the loader answered with {tag}, not {SPEC_FIRST_PREVIEW_LINK}"
            )


def test_the_fixtures_three_previews_are_one_picture_which_is_why_pixels_cannot_answer():
    """The control for the assertion above, and the reason it is not a pixel comparison.

    If these three ever became three DIFFERENT pictures a pixel oracle would start
    working, and this row would go red to say so - at which point the comment assertion
    is belt-and-braces rather than the only catch. Until then it is the only catch, and
    a reader about to delete it because "tobytes already covers that" needs to see this.
    """
    from PIL import Image

    decoded = []
    for blob in build_tiny_library.raw_preview_payloads(RAW_WITH_PREVIEW).values():
        image = Image.open(io.BytesIO(blob))
        image.load()
        decoded.append((image.size, image.tobytes()))
    assert len(set(decoded)) == 1, (
        "the three payloads now differ in PIXELS; the comment assertion above is no"
        " longer the only thing answering WHICH, and this control should be revisited"
    )


# `phash`'s fact identity, spelled by hand. `NAME` and `MODEL_NAME` are design 8.1's
# ("phash: dHash"); `MODEL_VERSION` is the project's own, and this pin is about THE COST
# OF MOVING IT rather than about its value being derivable from anything.
#
# WHY A LITERAL PIN AND NOT A COMMENT. `phash.py` argues at length that M10 Task 6 must
# NOT bump `MODEL_VERSION`: amendment A4 turned a RAW file's result from a FAILURE into a
# hash, and a failure stores no fact, so there is nothing cached to invalidate and no
# archive that re-enriches. The M10 review then bumped it to "1.1" and the whole suite
# stayed green (M1520). Every stored fact keys on (name, model_name, model_version), so a
# bump silently re-runs every decode in the archive - twenty thousand at the maintainer's
# target scale - to arrive at identical answers, and the cost lands on the user rather
# than on CI. A RECORDED REASON IS NOT A PIN. Moving this constant is sometimes right
# (the day the chain's ORDER changes, a RAW's hash really does move from one rendering to
# another); it has to be moved ON PURPOSE, with this row edited in the same commit.
SPEC_PHASH_IDENTITY = ("phash", "dhash", "1.0")


def test_the_phash_fact_identity_does_not_move_without_someone_deciding_it_should():
    from pam.enrich import phash as phash_module

    assert (
        phash_module.NAME,
        phash_module.MODEL_NAME,
        phash_module.MODEL_VERSION,
    ) == SPEC_PHASH_IDENTITY


@needs_exiftool
def test_the_raw_branch_is_taken_for_every_raw_extension_and_for_no_other(tmp_path):
    """One body, eleven names, and the EXTENSION decides which door it goes through.

    The RAW half really carries the preview; under a display extension the same bytes go
    to Pillow, which cannot identify them. Both halves are needed: a branch that fired on
    everything would pass the first, and one that fired on nothing would pass the second.
    """
    from PIL import UnidentifiedImageError

    root = tmp_path / "archive"
    root.mkdir()
    body = build_tiny_library.raw_with_previews("shared.ARW")
    load = pil_image_loader(root)
    for ext in sorted(SPEC_RAW_EXTS):
        (root / f"shared.{ext}").write_bytes(body)
        with load(record(f"shared.{ext}")) as decoded:
            assert decoded.size == (64, 48)
    for ext in sorted(SPEC_DISPLAY_EXTS):
        (root / f"shared.{ext}").write_bytes(body)
        with pytest.raises(UnidentifiedImageError):
            load(record(f"shared.{ext}"))


@needs_exiftool
def test_a_raw_with_no_embedded_preview_is_STILL_a_failure(tmp_path):
    """Spec 8.1 rev 17, in as many words. Do not swallow it.

    "A RAW carrying no embedded preview of any kind is still unreadable, is counted as a
    failure, and is named in the log." The honest count is what surfaced F1; a loader that
    quietly produced nothing would have hidden the finding it is fixing.
    """
    root = tmp_path / "archive"
    root.mkdir()
    (root / RAW_WITHOUT_PREVIEW).write_bytes((LIBRARY / RAW_WITHOUT_PREVIEW).read_bytes())
    with pytest.raises(PreviewUnavailableError, match=RAW_WITHOUT_PREVIEW):
        pil_image_loader(root)(record(RAW_WITHOUT_PREVIEW))


@needs_exiftool
def test_a_previewless_raw_is_counted_failed_by_the_runner_and_the_run_continues(tmp_path):
    """The count the user reads, and the files beside it that still get their hashes.

    This is the shape HT-M9 reported F1 in: `! N files could not be read`, with the rest
    of the run intact.
    """
    root = tmp_path / "archive"
    root.mkdir()
    conn = connect(tmp_path / "catalog.db")
    migrate(conn)
    for name in (RAW_WITHOUT_PREVIEW, RAW_WITH_PREVIEW, BASE):
        (root / name).write_bytes((LIBRARY / name).read_bytes())
        file_id = repo.upsert_file(
            conn,
            rel_path=name,
            filename=name,
            ext=name.rsplit(".", 1)[1].lower(),
            media_type="image",
            size=(root / name).stat().st_size,
            mtime_ns=1,
            placeholder=False,
            now=NOW,
        )
        repo.set_sha256(conn, file_id, f"sha-{name}")
    conn.commit()
    summary = run_enricher(conn, PhashEnricher(), pil_image_loader(root), workers=1)
    assert (summary.failed, summary.enriched) == (1, 2), (
        "the previewless RAW fails, and the RAW that HAS a preview is hashed beside it"
    )
    conn.close()


@needs_exiftool
def test_a_cloud_placeholder_that_is_a_RAW_never_reaches_exiftool_either(tmp_path, monkeypatch):
    """Vision R2 again, watched at the call the RAW branch would make.

    THE RAW BRANCH IS A SECOND ROUTE TO A READ and `builtins.open` cannot see it: the
    bytes would be read by a CHILD PROCESS. So this watches the argv exiftool is handed.
    A refusal that ran exiftool and discarded the output would hydrate the placeholder and
    look identical from outside - M8's M697, one process boundary further out.
    """
    root = tmp_path / "archive"
    root.mkdir()
    conn = connect(tmp_path / "catalog.db")
    migrate(conn)
    (root / RAW_WITH_PREVIEW).write_bytes((LIBRARY / RAW_WITH_PREVIEW).read_bytes())
    (root / "held.ARW").write_bytes(build_tiny_library.raw_with_previews("held.ARW"))
    for name, placeholder in ((RAW_WITH_PREVIEW, True), ("held.ARW", False)):
        file_id = repo.upsert_file(
            conn,
            rel_path=name,
            filename=name,
            ext="arw",
            media_type="image",
            size=(root / name).stat().st_size,
            mtime_ns=1,
            placeholder=placeholder,
            now=NOW,
        )
        repo.set_sha256(conn, file_id, f"sha-{name}")
    conn.commit()

    argv_seen: list[str] = []
    real = exiftool.subprocess.run

    def watching(argv, **kwargs):
        argv_seen.extend(str(part) for part in argv)
        return real(argv, **kwargs)

    monkeypatch.setattr(exiftool.subprocess, "run", watching)
    summary = run_enricher(conn, PhashEnricher(), pil_image_loader(root), workers=1)
    conn.close()

    assert (summary.skipped_wants, summary.enriched) == (1, 1)
    assert str(root / "held.ARW") in argv_seen, (
        "the watch recorded nothing for a RAW that WAS read - it cannot see, so its"
        " silence about the placeholder means nothing"
    )
    assert str(root / RAW_WITH_PREVIEW) not in argv_seen, (
        "pam handed a cloud placeholder to exiftool; reading one hydrates it (vision R2)"
    )


# --- 5a. HT-M10's F1: WHO registers, and it is only answerable in a fresh interpreter ----
#
# THE DEFECT, so the tests below read as regressions rather than as trivia. Until M10 Task
# 13, `register_heif_opener()` was called in exactly one place in the package - inside
# `PhashEnricher.available()` - and `pil_image_loader()`, which BOTH pixel enrichers take,
# never called it. `pam enrich` with no name worked, because `BUILDERS` happens to be
# ordered `phash, geocode, caption` and phash registered the opener process-wide for
# everyone downstream. `pam enrich caption` ALONE never built `PhashEnricher`, so every
# `.heic` raised `UnidentifiedImageError`: measured on a real-world library at HT-M10,
# almost every image in it silently unread by the milestone's headline enricher,
# invoked exactly as its own documentation says to invoke it.
#
# TWO REASONS THE SUITE MISSED IT, BOTH ADDRESSED HERE RATHER THAN LAMENTED:
#   1. `tests/tiny_library/`'s HEIC files carry no coded image, so a decode fails with the
#      SAME `UnidentifiedImageError` whether the opener is registered or not - measured at
#      the M10 close, not assumed. No decode-based test in this repository can tell the two
#      apart, which is what `test_the_tiny_librarys_HEIC_files_do_NOT_decode...` above
#      already says. Registration is therefore the only observable, and
#      `registered_extensions()` is Pillow's own answer to it.
#   2. Registration is process-global, so an in-process assertion is satisfied by whichever
#      test registered first. EVERY TEST BELOW RUNS IN A FRESH INTERPRETER. One that does
#      not is not a weaker version of these - it is a test of nothing.


def _registers_heic(setup: str) -> bool:
    """Run `setup` in a FRESH interpreter and ask Pillow whether `.heic` is registered.

    The subprocess is the whole point (see the note above), and the probe asks PILLOW
    rather than watching for a call - the same distinction
    `test_reading_a_file_registers_the_heif_opener_with_pillow` is built on.
    """
    probe = (
        "from pathlib import Path\n"
        f"{setup}\n"
        "from PIL import Image\n"
        "print('.heic' in Image.registered_extensions()"
        " and '.heif' in Image.registered_extensions())\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
        timeout=120,
        check=True,
    )
    return result.stdout.strip() == "True"


def test_the_shared_image_loader_registers_heif_with_no_enricher_built_at_all():
    """HT-M10's F1, at the loader. THE regression test: this is the line that was missing.

    Builds `pil_image_loader` and nothing else - no `PhashEnricher`, no `available()`, no
    `pam enrich` - and asks Pillow. Before M10 Task 13 this printed False.
    """
    assert _registers_heic(
        "from pam.enrich.base import pil_image_loader\npil_image_loader(Path('.'))"
    ), (
        "pam/enrich/base.py::pil_image_loader does not register the HEIF opener, so every"
        " enricher that takes this loader without PhashEnricher having run first is blind"
        " to HEIC. That is HT-M10's F1: almost every real image went unread."
    )


def test_pam_enrich_caption_alone_can_open_heic_which_is_HT_M10s_F1():
    """The same defect one layer out, at the REAL builder the CLI reaches.

    Drives `BUILDERS['caption']` exactly as `pam enrich caption` does - the builder, not the
    loader directly - so a future refactor that gave the captioner its own loader and left
    THAT one unregistered still goes red here. `phash` is never built in this interpreter,
    which is the entire condition F1 needs.
    """
    assert _registers_heic(
        "import sqlite3, logging\n"
        "from pam.config import Config\n"
        "from pam.enrich.command import BUILDERS\n"
        "BUILDERS['caption'](sqlite3.connect(':memory:'), Path('.'), Config(),"
        " logging.getLogger('probe'))"
    ), (
        "`pam enrich caption` run alone cannot open a HEIC: the caption builder's image"
        " loader has not registered the HEIF opener and PhashEnricher never ran. This is"
        " HT-M10's F1 exactly, and `pam enrich` with no name would still pass because"
        " BUILDERS is ordered phash-first."
    )


def test_the_order_of_BUILDERS_is_not_load_bearing_for_heic():
    """EVERY pixel enricher, one fresh interpreter each - the guard for M11 and M12.

    F1's real lesson is not the missing call, it is that `BUILDERS`' INSERTION ORDER was
    silently load-bearing for correctness: `phash` first meant the default invocation
    worked, and nothing anywhere said so. A milestone that re-spelled that map with
    `caption` first - which `command.py`'s own comment invites, since it is hand-written on
    purpose - would have broken `pam enrich` as well.

    So this asserts the property for each builder INDEPENDENTLY, in a process where no
    other builder has run. `geocode` is exempt by name AND by behaviour: it takes
    `_no_image`, reads catalog columns and opens no file. M11's `faces` and M12's `ocr` are
    pixel enrichers and are caught by this the day they join the map, which is the point of
    iterating the map rather than listing two names.
    """
    from pam.enrich.command import BUILDERS

    reads_no_pixels = {"geocode"}
    checked = sorted(set(BUILDERS) - reads_no_pixels)
    assert checked, "BUILDERS has no pixel enricher left; this guard now protects nothing"

    for name in checked:
        assert _registers_heic(
            "import sqlite3, logging\n"
            "from pam.config import Config\n"
            "from pam.enrich.command import BUILDERS\n"
            f"BUILDERS[{name!r}](sqlite3.connect(':memory:'), Path('.'), Config(),"
            " logging.getLogger('probe'))"
        ), (
            f"the {name!r} enricher's image loader does not register the HEIF opener when"
            f" it is the only enricher built. `pam enrich {name}` would be blind to every"
            " HEIC in the archive while `pam enrich` (which runs phash first) looked fine."
        )
