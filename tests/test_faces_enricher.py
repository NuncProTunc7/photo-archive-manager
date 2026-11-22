"""M11 Task 8 Steps 7-9 and 11: the `faces` enricher, its two refusals, and its wiring.

WHAT THIS MODULE IS ABOUT. `tests/test_faces_sink.py` owns the ROUTE a detection takes
once an enricher has produced one; this owns the enricher that produces it - the two
refusals in `wants()`, the constants the decode consumes, A6's generation refusal, A9's
refresh refusal, A11's read-time floor, and the four entries Step 9 adds to `command.py`.

THE ORACLE RULE, AND IT IS THE REASON HALF THIS FILE IS LITERALS. Expectations are
spelled from the SPEC and from the recorded fixture, never read back from the production
symbol they check. `command.BUILDERS`' contents are compared against a hand-written
literal; the geometry document a real detection produces is compared against
`tests/fixtures/faces_parity.json`, which was recorded by `tests/test_faces_model.py`'s
own independent decode (`_detect`) and not by `pam/enrich/faces.py`. That second
implementation is the point: two readings of YuNet's twelve tensors that agree are worth
something, and one reading compared against itself is worth nothing (M5's `open_plan`,
M6's `ENRICHERS`, M8's partition).

WHAT NEEDS THE REAL STACK AND WHAT DOES NOT. `enrich()` imports numpy and runs two ONNX
sessions, so every test that calls it is `faces`-marked and is registered in
`tests/test_faces_model.py::FACES_MARKED_TESTS` (A2: the skip count is all-or-none per
lane, and a marked test nobody wrote down is a count nobody can reconcile). Everything
else - `wants()`, `available()`'s refusal, the identity fallback, both refusals, the
floor, the wiring - runs on the push lane, where a mistake actually gets caught.
"""

import ast
import logging
import sqlite3
from pathlib import Path
from unittest import mock

import pytest
from faces_stack import STACK_READY, model_dir

from pam.catalog import repo
from pam.catalog.db import connect, migrate
from pam.config import Config
from pam.enrich import InferredFact, faces_model, run_enricher
from pam.enrich import faces as faces_module
from pam.enrich.base import FileRecord
from pam.enrich.command import BUILDERS, DISABLEABLE, WORKER_OVERRIDES
from pam.enrich.faces import CANDIDATE_SCORE, GEOMETRY_VERSION, UNKNOWN_VERSION, FacesEnricher
from pam.enrich.sink import generation_refusal, refresh_refusal

NOW = "2026-09-10T10:00:00"

# A newline, named, so the planted source in the AST controls below stays readable.
NL = chr(10)

FIXTURE_IMAGE = Path(__file__).parent / "fixtures" / "faces_parity.png"
FIXTURE_VALUES = Path(__file__).parent / "fixtures" / "faces_parity.json"

# Step 9, TRANSCRIBED from the plan ("`BUILDERS` gains `faces` AFTER `phash` and
# `geocode`", recorded clarification 4) plus the three enrichers M9/M10 shipped. Compared
# against `BUILDERS` as an ORDERED list, because the order is the thing HT-M10's F1 was
# about and a set comparison cannot see it.
# M12 Task 13 adds `ocr` LAST (M12 plan, recorded clarification 2: "`phash`, `geocode`,
# `caption`, `faces`, `ocr` - cheapest first").
SHIPPED_IN_ORDER = ["phash", "geocode", "caption", "faces", "ocr"]

# Design 8.1 rev 17's `enabled` keys and design 11's per-enricher worker counts, spelled
# from the spec rather than from `Config`'s attribute names.
SPEC_DISABLEABLE = {
    "caption": "[enrich.caption] enabled",
    "faces": "[enrich.faces] enabled",
    "ocr": "[enrich.ocr] enabled",  # design 8.1 rev 17 / 11 rev 27 (M12 Task 13 Step 5)
}
SPEC_WORKER_OVERRIDES = {"caption": 7, "faces": 5, "ocr": 3}


def file_record(**overrides) -> FileRecord:
    """One `FileRecord` with the fields `wants()` reads, and nothing decided for it."""
    fields = {
        "id": 1,
        "rel_path": "a.jpg",
        "filename": "a.jpg",
        "ext": "jpg",
        "media_type": "image",
        "size": 10,
        "mtime_ns": 1,
        "sha256": "sha-a",
        "placeholder": False,
        "companion_group_id": None,
        "status": "present",
    }
    fields.update(overrides)
    known = {f: fields[f] for f in FileRecord.__dataclass_fields__ if f in fields}
    return FileRecord(**known)


@pytest.fixture()
def archive(tmp_path):
    path = tmp_path / "catalog.db"
    conn = connect(path)
    migrate(conn)
    yield conn, path
    conn.close()


def add_image(conn, name, *, placeholder=False, media_type="image"):
    file_id = repo.upsert_file(
        conn,
        rel_path=name,
        filename=name,
        ext=name.rsplit(".", 1)[-1],
        media_type=media_type,
        size=10,
        mtime_ns=1,
        placeholder=placeholder,
        now=NOW,
    )
    repo.set_sha256(conn, file_id, f"sha-{name}")
    conn.commit()
    return file_id


# --- Step 7: the two refusals, and the placeholder one watches the OPEN ----------------


class WantsProbe:
    """The REAL `wants()`, in front of an `enrich()` whose FIRST act is to open the file.

    THIS SHAPE IS THE WHOLE TEST, so it is worth reading before the two tests below. The
    dangerous act is the OPEN - reading a cloud placeholder hydrates it, and vision R2
    forbids that absolutely - and an implementation that opened the file and then returned
    no facts leaves a catalog indistinguishable from a correct refusal's (M8's M697:
    1718 tests green over a placeholder pam had just pulled off the network).

    Driving the real `FacesEnricher` here would NOT watch that, on the push lane: its
    `enrich()` imports numpy before it touches the loader, so on a machine without the
    `[faces]` extra a `wants()` that wrongly said yes would fail on the import and never
    reach the open. The test would pass for the wrong reason, on the lane that runs on
    every push. So the predicate under test is the production one, verbatim, and the body
    behind it is the smallest thing that would commit the offence.
    """

    name = "faces"
    model_name = "yunet+sface"
    model_version = "probe"

    def __init__(self):
        self._real = FacesEnricher("")

    def available(self):
        return True, "the probe needs no pack"

    def cost_estimate_ms(self):
        return 1

    def wants(self, file):
        return self._real.wants(file)

    def enrich(self, file, image):
        image(file)
        return []


def test_wants_refuses_a_cloud_placeholder_and_no_file_is_ever_opened(archive):
    """Vision R2 and M697: the assertion is about the ACT OF READING, not the answer.

    The archive holds one file and it is a placeholder. If `FacesEnricher.wants` said yes,
    the probe's `enrich()` would open it on the next line and the spy would hold its path -
    on every lane, with or without the extra. The assertion is that the list is empty, and
    `skipped_wants` is the second reading of the same event from the runner's side.
    """
    conn, _path = archive
    add_image(conn, "cloud.jpg", placeholder=True)
    opened: list[str] = []

    def spy(file):
        opened.append(file.rel_path)

    summary = run_enricher(conn, WantsProbe(), spy, workers=1)

    assert opened == [], f"pam opened a cloud placeholder and hydrated it: {opened}"
    assert summary.skipped_wants == 1
    assert summary.failed == 0


def test_the_open_watch_would_actually_see_an_open(archive):
    """THE NEGATIVE CONTROL, and without it the test above proves nothing.

    A spy nothing calls, a loader the runner never reaches, an enricher that fails before
    the open - each produces the same empty list for a pam that hydrates every placeholder
    it is given. So: the same probe, the same spy, the same run, with the ONE field that
    decides the refusal flipped. The open must happen.
    """
    conn, _path = archive
    add_image(conn, "real.jpg", placeholder=False)
    opened: list[str] = []

    def spy(file):
        opened.append(file.rel_path)

    run_enricher(conn, WantsProbe(), spy, workers=1)

    assert opened == ["real.jpg"]


@pytest.mark.parametrize(
    ("media_type", "wanted"),
    [("image", True), ("video", False), ("sidecar", False), ("other", False)],
)
def test_wants_is_an_allowlist_of_image_and_not_a_denylist_of_video(media_type, wanted):
    """Design 8.1 rev 3: every pixel-reading enricher refuses videos.

    Written as an ALLOWLIST, and the parametrize is what says so: `sidecar` and `other`
    are refused as well, so a media type nobody has thought of yet defaults to not being
    fed to a decoder. A denylist of `video` would pass a two-row version of this test and
    hand `.aae` files to onnxruntime the day a fifth type exists.
    """
    assert FacesEnricher("").wants(file_record(media_type=media_type)) is wanted


def test_a_placeholder_video_is_refused_by_each_operand_on_its_own():
    """Both refusals are load-bearing on their own, which a single case cannot show.

    A guard deleted wholesale is credited with kills its individual operands do not have
    (CLAUDE.md, measured 8 vs 1), so each operand is exercised where the other would say
    yes: a placeholder IMAGE and a real VIDEO.
    """
    assert FacesEnricher("").wants(file_record(placeholder=True, media_type="image")) is False
    assert FacesEnricher("").wants(file_record(placeholder=False, media_type="video")) is False
    assert FacesEnricher("").wants(file_record(placeholder=False, media_type="image")) is True


# --- Step 7: availability, identity, and the constants the decode consumes -------------


def test_a_machine_with_no_pack_builds_the_enricher_and_is_told_what_is_missing():
    """Construction is TOTAL and `available()` is the gate, which is A1's own shape.

    Every machine has no pack until the user puts two files somewhere and says where, so
    building the enricher must not raise - `pam enrich` builds it in order to ask. What
    the user gets is a sentence, and the sentence has to name something they can act on:
    either the extra to install or the key to set. Which of the two depends on the lane
    this test runs in, and asserting either one by itself would make this test lane-
    dependent - so it asserts that one of them is named, and that the answer is False.
    """
    enricher = FacesEnricher("")
    ok, sentence = enricher.available()

    assert ok is False
    assert "model_dir" in sentence or "[faces]" in sentence, sentence
    assert enricher.model_name == "yunet+sface"
    assert enricher.model_version == UNKNOWN_VERSION


def test_the_candidate_threshold_sits_below_the_configured_floor_which_is_what_A11_needs():
    """A11 is only reversible DOWNWARDS if there is something below the floor to recover.

    `CANDIDATE_SCORE` is the decode's noise floor and `[tiers] faces` floor is the read-
    time threshold; if the first were at or above the second, lowering the floor would
    recover nothing, every sub-floor detection would already have been discarded at write
    time, and A11 would be a comment rather than a behaviour. The default floor is spelled
    from design 11 (`faces = { floor = 0.4 }`), not read off `Config`.
    """
    assert Config().tiers["faces"].floor == 0.4
    assert 0.0 < CANDIDATE_SCORE < 0.4


# THE MATCHER IS A HELPER SO THAT THE NEGATIVE CONTROL CAN DRIVE THE REAL ONE. Task 7's F3
# was this defect twice; Task 8 stage 1 measured it again as M1773 - narrowing the collector
# to `(bytes,)` made it collect nothing and all 3129 tests passed over a guard that had
# stopped looking. One function, two callers: the guard and the control below it.


def _scalar_literals(tree: ast.AST) -> set[object]:
    """Every int, float and str literal in a parsed module. The guard's own matcher."""
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str))
    }


def test_the_enricher_re_types_none_of_the_packs_measured_constants():
    """A4's digest is over `faces_model`'s constants; a second spelling defeats it.

    The decode formulae are what YuNet's twelve tensors mean, and they are digest inputs
    precisely because changing one changes every box pam has ever stored. If `faces.py`
    held its own `640`, its own `0.3` or its own `"BGR"`, `identity()` would be a digest
    of the OTHER copy and a pack swap would not invalidate anything. So the module's
    source is walked for those literals.

    `CANDIDATE_SCORE` and `GEOMETRY_VERSION` are this module's OWN constants and are not
    in that digest - see `CANDIDATE_SCORE`'s comment, which names `PIPELINE_VERSION` as
    the digest input that covers them.
    """
    source = (Path(__file__).parents[1] / "pam" / "enrich" / "faces.py").read_text("utf-8")
    literals = _scalar_literals(ast.parse(source))
    # The channel-order STRINGS are deliberately absent from this list. `_nchw` compares
    # against `"BGR"` to decide whether to reverse the axis, which is an INTERPRETATION of
    # the manifest's value and not a second declaration of what the model wants - the
    # declaration is `faces_model.DETECTOR_CHANNELS`, which is what the digest covers and
    # what `_detect` passes in. `tests/test_faces_model.py`'s parity fixture is what would
    # catch a swap, by value.
    for forbidden in (640, 112, 0.3, "fc1", "input", "data"):
        assert forbidden not in literals, (
            f"{forbidden!r} is spelled in faces.py as well as in faces_model.py; A4's"
            " identity digest covers the constant in faces_model.py and would not move"
            " when this copy did"
        )


def test_the_literal_walk_would_actually_see_a_re_typed_constant():
    """THE NEGATIVE CONTROL, and it drives `_scalar_literals` itself (M1773).

    The guard above asserts an ABSENCE, and an absence is what a matcher that matches
    nothing reports for every input in the world. So the same function is driven over a
    planted module that DOES re-type the pack's numbers, and the literals it is supposed
    to find must be in the answer - by value, and including the string ones, because the
    guard's forbidden list holds both.
    """
    planted = ast.parse(
        "DETECTOR_INPUT = (640, 640)" + NL + "OUT = 'fc1'" + NL + "IOU = 0.3" + NL
    )
    found = _scalar_literals(planted)

    assert {640, "fc1", 0.3} <= found, found
    assert _scalar_literals(ast.parse("X = None" + NL)) == set()


# --- Step 7: the zero-length embedding, which nothing constructed ---------------------
#
# THE EXECUTOR DISCLOSED THIS AND STAGE 1 OWED IT A TEST. `_embed` returns None for a
# vector it cannot normalise, `enrich()` logs and drops that detection, and the marker
# must not count it - and NO TEST BUILT THE CASE. `_embed`'s own docstring says the branch
# "is not a `cannot happen`", which is CLAUDE.md's untested-docstring pattern pointed
# directly at itself. Both halves are exercised on the PUSH lane: the degenerate-crop
# return needs no numpy and no session at all (it happens before either is touched), and
# the caller's drop is driven with the module's three seams stubbed.


def test_a_degenerate_box_embeds_to_nothing_rather_than_to_a_vector():
    """The half of `_embed` that runs before numpy is ever touched, so it needs no pack.

    A box of zero width or zero height denormalises to an empty crop, and there is no
    embedding of an empty crop - `None` is the answer, not a zero vector that would
    cluster with every other zero vector in the archive. `numpy` and `sessions` are passed
    as `None` deliberately: if this path ever starts touching either, this test says so by
    raising rather than by quietly passing.
    """
    from PIL import Image

    rgb = Image.new("RGB", (100, 80))

    assert faces_module._embed(None, None, rgb, [0.5, 0.5, 0.0, 0.4]) is None
    assert faces_module._embed(None, None, rgb, [0.5, 0.5, 0.4, 0.0]) is None


def test_a_detection_that_embeds_to_nothing_is_dropped_logged_and_not_counted(caplog):
    """The caller's half: `enrich()` drops it, says so, and the marker does not count it.

    THE MARKER IS THE POINT. A dropped detection that was still counted would make
    coverage claim a face `face_instances` does not hold - A8's failure, arriving from the
    enricher's side rather than the sink's. So the marker must read 0 with one detection
    found and none embedded.

    Driven with the module's three seams stubbed - `_numpy`, the enricher's sessions, and
    `_detect`/`_embed` - so it runs on the push lane. The production code under test is
    `enrich()`'s own loop, unmodified.
    """
    from PIL import Image

    enricher = FacesEnricher("")
    enricher._sessions = object()
    detection = (0.9, [0.1, 0.1, 0.2, 0.2], [[0.1, 0.1]] * 5)

    with mock.patch.object(faces_module, "_numpy", lambda: object()):
        with mock.patch.object(faces_module, "_detect", lambda *a: [detection]):
            with mock.patch.object(faces_module, "_embed", lambda *a: None):
                with caplog.at_level(logging.WARNING, logger="pam"):
                    facts = enricher.enrich(
                        file_record(rel_path="zero.jpg"),
                        lambda file: Image.new("RGB", (40, 30)),
                    )

    assert [f.kind for f in facts] == ["faces"]
    assert facts[0].value == {"count": 0}
    assert any("zero.jpg" in r.getMessage() for r in caplog.records), caplog.records


# --- Step 9: the wiring, spelled from the plan ----------------------------------------


def test_BUILDERS_gains_faces_after_phash_and_geocode_in_that_order():
    """Recorded clarification 4, and ORDER is asserted because order has bitten here.

    HT-M10's F1 was `BUILDERS`' insertion order being silently load-bearing for
    correctness. The hole itself is closed - `pil_image_loader` registers the HEIF opener
    and `tests/test_phash.py` iterates every pixel builder in a fresh interpreter - but the
    order is still a decision somebody made, and a decision nothing asserts is a decision
    the next refactor makes differently.
    """
    assert list(BUILDERS) == SHIPPED_IN_ORDER


@pytest.mark.parametrize("name", ["caption", "faces", "ocr"])
def test_every_disableable_enricher_names_the_key_that_turns_it_on(name):
    """Design 8.1 rev 17's third state, per enricher, and BOTH ship `enabled = false`.

    AND THIS TEST'S NODE ID IS LOAD-BEARING FOR SOMETHING ELSE. It is parametrized so that
    one of its ids is literally `[faces]`, UNMARKED - which is the exact condition
    `tests/test_faces_model.py::test_a_test_whose_NAME_mentions_faces_is_not_skipped_by_the_marker_hook`
    exists to watch. That control used to point at
    `test_pam_enrich_refuses_an_enricher_no_milestone_has_built_yet[faces]`, and M11 Task 8
    shipped `faces` and took that id away; a control whose subject has left is a control
    that passes over nothing (M7's M743). Do not un-parametrize this test without moving
    that control to another unmarked node whose id says `faces`.
    """
    key, is_enabled = DISABLEABLE[name]
    assert key == SPEC_DISABLEABLE[name]
    assert is_enabled(Config()) is False, (
        f"{key} no longer ships false; design 8.1 rev 17 says caption, faces and ocr all do"
    )


def test_faces_is_disableable_and_has_its_own_worker_count():
    """Design 8.1 rev 17's third state, and design 11's per-enricher workers.

    `[enrich.faces] enabled` ships FALSE, so a default install must report `faces` as a
    row that exists, is named, and says which key turns it on - and must exit 0 doing it.
    The key string is what the user is shown, so it is compared against a literal.
    """
    assert {name: key for name, (key, _) in DISABLEABLE.items()} == SPEC_DISABLEABLE
    assert set(WORKER_OVERRIDES) == set(SPEC_WORKER_OVERRIDES)

    config = Config(caption_workers=7, faces_workers=5, ocr_workers=3, workers=31)
    assert {name: fn(config) for name, fn in WORKER_OVERRIDES.items()} == SPEC_WORKER_OVERRIDES
    # And the global count is what everything else gets - the override is an override.
    assert "phash" not in WORKER_OVERRIDES and "geocode" not in WORKER_OVERRIDES


def test_the_faces_builder_takes_the_shared_decoder_and_survives_an_unset_model_dir():
    """I4 at the wiring, and the condition `tests/test_phash.py`'s guard builds it under.

    That guard constructs every pixel builder with a DEFAULT `Config()` - `model_dir` is
    empty - in a fresh interpreter, and asks Pillow whether HEIC is registered. It can only
    do that if building `faces` with no pack neither raises nor decides to skip the shared
    loader.
    """
    conn = sqlite3.connect(":memory:")
    try:
        enricher, loader = BUILDERS["faces"](
            conn, Path("."), Config(), logging.getLogger("probe")
        )
    finally:
        conn.close()

    assert isinstance(enricher, FacesEnricher)
    assert enricher.name == "faces"
    # The shared loader, not a second decoder: it is the closure `base.pil_image_loader`
    # returns, and its own module is where the HEIF registration lives.
    assert loader.__module__ == "pam.enrich.base"


# --- Step 8: A6's generation refusal and A9's refresh refusal --------------------------


class SinkedEnricher:
    """The smallest thing with `faces`' name and a chosen identity. No pack, no pixels."""

    name = "faces"

    def __init__(self, model_name="yunet+sface", model_version="gen-1"):
        self.model_name = model_name
        self.model_version = model_version

    def available(self):
        return True, "fake"

    def cost_estimate_ms(self):
        return 1

    def wants(self, file):
        return True

    def enrich(self, file, image):
        return [InferredFact(kind="faces", value={"count": 0})]


def insert_generation(conn, model_name, model_version, count=2):
    for index in range(count):
        repo.insert_face(
            conn,
            sha256=f"sha-{model_version}-{index}",
            bbox_json="{}",
            embedding=b"\x00\x01\x02\x03",
            det_confidence=0.9,
            model_name=model_name,
            model_version=model_version,
        )
    conn.commit()


def test_an_archive_holding_another_generation_is_refused_and_the_route_is_named(archive):
    """A6. The refusal names what is there, how much of it, and the three-step route.

    "One archive, one generation" is not a tidiness rule: cosine distances between two
    embedding spaces are meaningless numbers that cluster into confident, wrong people,
    and `face_instances` has no `superseded_by` to mark either side stale.
    """
    conn, _path = archive
    insert_generation(conn, "yunet+sface", "old-generation", count=3)

    sentence = generation_refusal(conn, SinkedEnricher(model_version="gen-1"))

    assert sentence is not None
    assert "old-generation" in sentence
    assert "3 faces" in sentence
    assert "pam faces export" in sentence and "pam faces purge" in sentence


def test_an_archive_holding_the_SAME_generation_is_not_refused(archive):
    """The other direction, which is the one a second `pam enrich faces` run takes.

    Without this the refusal could be "always refuse when any face exists", which would
    make the enricher a one-shot command and would pass the test above.
    """
    conn, _path = archive
    insert_generation(conn, "yunet+sface", "gen-1")

    assert generation_refusal(conn, SinkedEnricher(model_version="gen-1")) is None


def test_an_empty_archive_is_not_refused(archive):
    conn, _path = archive
    assert generation_refusal(conn, SinkedEnricher()) is None


def test_a_differing_model_NAME_is_refused_too_not_only_a_differing_version(archive):
    """A6 says `(model_name, model_version)`, both halves.

    A check on `model_version` alone passes every test above and lets a second STACK -
    a different detector and embedder entirely - write into the same table, which is a
    worse version of the failure A6 describes rather than a lesser one.
    """
    conn, _path = archive
    insert_generation(conn, "some-other-stack", "gen-1")

    sentence = generation_refusal(conn, SinkedEnricher(model_name="yunet+sface"))
    assert sentence is not None and "some-other-stack" in sentence


def test_the_generation_refusal_is_keyed_on_the_sink_and_not_on_the_name_faces(archive):
    """A6/A9's own wording: keyed on the SINK. An enricher with none is waved through.

    `phash` writing over an archive full of faces is not a generation conflict and must
    not be reported as one - it writes to `inferred_facts` and cannot reach
    `face_instances` at all.
    """
    conn, _path = archive
    insert_generation(conn, "yunet+sface", "old-generation")

    phash_shaped = SinkedEnricher(model_version="whatever")
    phash_shaped.name = "phash"
    assert generation_refusal(conn, phash_shaped) is None
    assert refresh_refusal(phash_shaped) is None


def test_pam_enrich_faces_over_a_second_generation_refuses_end_to_end(archive, tmp_path):
    """A6 through the command, where a user meets it: a refusal, not a traceback.

    The archive is intact and the user has a route, so this is `EnricherOutcome.refusal` -
    the same channel a missing pack uses - and it reaches the exit code through
    `had_errors` without ending the run for the other enrichers.
    """
    from pam.enrich.command import _run_one

    conn, _path = archive
    insert_generation(conn, "yunet+sface", "old-generation")

    with mock.patch.dict(
        BUILDERS, {"faces": lambda c, r, cfg, log: (SinkedEnricher(), lambda f: None)}
    ):
        outcome = _run_one(
            conn,
            "faces",
            tmp_path,
            Config(faces_enabled=True),
            None,
            logging.getLogger("probe"),
        )

    assert outcome.result is None
    assert outcome.refusal is not None and "pam faces purge" in outcome.refusal


def test_refresh_older_than_model_is_refused_for_an_enricher_with_a_sink(archive):
    """A9, at the runner, so no caller can reach it by going around `pam enrich`.

    A refresh points an old fact at its replacement; `face_instances` has no
    `superseded_by` to point with, so a refresh would leave two generations side by side
    with nothing marking either stale - the state A6 exists to keep out, arrived at
    through a command-line flag.
    """
    conn, _path = archive
    add_image(conn, "a.jpg")

    with pytest.raises(ValueError) as error:
        run_enricher(
            conn,
            SinkedEnricher(model_version="9.0"),
            lambda f: None,
            workers=1,
            refresh_older_than_model="1.0",
        )

    assert "face_instances" in str(error.value)
    assert "superseded_by" in str(error.value)


def test_refresh_still_works_for_an_enricher_without_a_sink(archive):
    """The other direction. A9 is keyed on the sink, so `phash`'s refresh is untouched -
    and a refusal that fired for everybody would pass the test above."""
    conn, _path = archive
    add_image(conn, "a.jpg")
    plain = SinkedEnricher(model_version="9.0")
    plain.name = "phash"

    summary = run_enricher(
        conn, plain, lambda f: None, workers=1, refresh_older_than_model="1.0"
    )
    assert summary.walked == 1


# --- Step 11: A11, the floor at READ time, in both directions --------------------------


class PlannedFaces:
    """A `faces` enricher emitting exactly the confidences a test names. No pack."""

    name = "faces"
    model_name = "yunet+sface"
    model_version = "gen-1"

    def __init__(self, confidences):
        self.confidences = confidences

    def available(self):
        return True, "fake"

    def cost_estimate_ms(self):
        return 1

    def wants(self, file):
        return True

    def enrich(self, file, image):
        facts = [InferredFact(kind="faces", value={"count": len(self.confidences)})]
        for index, confidence in enumerate(self.confidences):
            facts.append(
                InferredFact(
                    kind="face",
                    value={
                        "geometry": {
                            "v": GEOMETRY_VERSION,
                            "bbox": [0.1, 0.1, 0.2, 0.2],
                            "landmarks": [[0.1, 0.1]] * 5,
                            "source_w": 640,
                            "source_h": 640,
                            "orientation": 1,
                        },
                        "embedding": bytes([index, index, index, index]),
                    },
                    confidence=confidence,
                )
            )
        return facts


def test_a_sub_floor_detection_is_STORED_and_the_floor_is_applied_when_it_is_READ(archive):
    """A11, and the raise-and-lower is the half that makes it a behaviour.

    Rev 2 of this plan dropped sub-floor detections BEFORE storage, so raising the floor
    left old rows behind and lowering it could not recover what had been discarded - a
    documented control that only worked once, whose sole recovery was a destructive purge.
    A11 moved the threshold to read time for exactly that reason (I3: every correction is
    reversible without a purge).

    So: three detections at 0.20, 0.50 and 0.95 are written, ALL THREE are in the table,
    and the same rows answer three different floors three different ways - including the
    floor coming back DOWN, which is the direction the old design could not do.
    """
    conn, path = archive
    add_image(conn, "a.jpg")
    run_enricher(conn, PlannedFaces([0.20, 0.50, 0.95]), lambda f: None, workers=1)

    stored = sqlite3.connect(path)
    stored.row_factory = sqlite3.Row
    try:
        rows = [
            r["det_confidence"]
            for r in stored.execute("SELECT det_confidence FROM face_instances ORDER BY id")
        ]
    finally:
        stored.close()
    assert rows == [0.20, 0.50, 0.95], (
        "a detection was dropped before storage; A11 says every structurally valid"
        " detection is stored with its confidence and the floor is applied at read time"
    )

    assert [f.det_confidence for f in repo.faces_for_clustering(conn, 0.0)] == [
        0.20,
        0.50,
        0.95,
    ]
    assert [f.det_confidence for f in repo.faces_for_clustering(conn, 0.4)] == [0.50, 0.95]
    assert [f.det_confidence for f in repo.faces_for_clustering(conn, 0.9)] == [0.95]
    # DOWN AGAIN, over the same rows, with no re-enrichment: this is the direction the
    # write-time floor could not express, and it is why the column holds 0.20 at all.
    assert [f.det_confidence for f in repo.faces_for_clustering(conn, 0.1)] == [
        0.20,
        0.50,
        0.95,
    ]


def test_the_marker_counts_the_sub_floor_detection_too(archive):
    """Coverage counts files PROCESSED and rows ACCEPTED, not rows above a threshold.

    A marker that counted only the faces above the floor would move when the floor moved,
    which would make `pam status`' coverage a function of a display setting.
    """
    conn, path = archive
    add_image(conn, "a.jpg")
    run_enricher(conn, PlannedFaces([0.20, 0.95]), lambda f: None, workers=1)

    stored = sqlite3.connect(path)
    stored.row_factory = sqlite3.Row
    try:
        marker = stored.execute(
            "SELECT value_json FROM inferred_facts WHERE kind = 'faces'"
        ).fetchone()
    finally:
        stored.close()
    assert marker["value_json"] == '{"count": 2}'


# --- Step 11: the real stack, against an independently recorded fixture ----------------
#
# Everything below runs the actual sessions and is `faces`-marked. The oracle is
# `tests/fixtures/faces_parity.json`, recorded by `tests/test_faces_model.py`'s own decode
# - a SECOND IMPLEMENTATION of YuNet's twelve tensors, written for Task 7 and not by this
# task's author. Comparing `pam/enrich/faces.py` against it is the only comparison here
# that could catch a decode that is self-consistently wrong.


@pytest.fixture(scope="module")
def real():
    if not STACK_READY:  # pragma: no cover - the marker skips these before the fixture
        pytest.skip("faces stack unavailable")
    import json

    return FacesEnricher(str(model_dir())), json.loads(FIXTURE_VALUES.read_text("utf-8"))


def loader_over(path):
    """A `PILImageLoader` serving one committed PNG. The shared decoder's contract."""
    from PIL import Image

    def load(file):
        return Image.open(path)

    return load


@pytest.mark.faces
def test_a_real_photograph_produces_one_marker_and_one_face_matching_the_fixture(real):
    """Step 11's headline, with the expectation SPELLED from the recorded fixture.

    The fixture's box is in the detector's 640-pixel frame and the geometry document is
    normalised to the raster, so the expected numbers are the fixture's divided by 640 -
    written out here rather than fetched from `faces.py`, which is the whole point. The
    fixture image IS 640x640, so this comparison is exact rather than approximate about
    the resize.
    """
    enricher, expected = real
    facts = enricher.enrich(
        file_record(rel_path="faces_parity.png"), loader_over(FIXTURE_IMAGE)
    )

    assert [f.kind for f in facts] == ["faces", "face"]
    assert facts[0].value == {"count": 1}

    want = expected["detections"][0]
    geometry = facts[1].value["geometry"]
    assert geometry["v"] == 1
    assert geometry["source_w"] == 640 and geometry["source_h"] == 640
    assert geometry["orientation"] == 1
    for got, wanted in zip(geometry["bbox"], [v / 640.0 for v in want["box"]], strict=True):
        assert got == pytest.approx(wanted, abs=1e-4)
    assert len(geometry["landmarks"]) == 5
    for got, wanted in zip(geometry["landmarks"], want["landmarks"], strict=True):
        assert got[0] == pytest.approx(wanted[0] / 640.0, abs=1e-4)
        assert got[1] == pytest.approx(wanted[1] / 640.0, abs=1e-4)
    assert facts[1].confidence == pytest.approx(want["score"], abs=1e-4)


@pytest.mark.faces
def test_the_stored_embedding_is_128_float32s_and_is_unit_LENGTH(real):
    """The raw `fc1` vector has L2 norm ~13.4 (measured at the spike); pam stores a UNIT
    one, so that every consumer's cosine distance is a dot product and no consumer can
    forget the step. 128 float32s is 512 bytes, and the width is design 4's - whose own
    `float32[512]` comment is wrong for this stack and is recorded as such in A3."""
    import struct

    enricher, _expected = real
    facts = enricher.enrich(
        file_record(rel_path="faces_parity.png"), loader_over(FIXTURE_IMAGE)
    )
    blob = facts[1].value["embedding"]

    assert isinstance(blob, bytes)
    assert len(blob) == faces_model.EMBEDDING_DIM * 4 == 512
    vector = struct.unpack(f"<{faces_model.EMBEDDING_DIM}f", blob)
    assert sum(v * v for v in vector) == pytest.approx(1.0, abs=1e-4)
    assert max(abs(v) for v in vector) < 1.0, "a unit vector has no component past 1"


@pytest.mark.faces
def test_the_confidence_of_every_detection_is_inside_design_6_4s_range(real):
    """Design 6.4: confidence is NULL or in [0, 1], and `face` REQUIRES one.

    A detection whose score fell outside the range would be dropped by
    `validate.fact_error` and would vanish from `face_instances` while the marker still
    counted it - except that A8 recomputes the marker, so it would vanish silently. The
    score fusion is `sqrt(cls*obj)` over two clipped probabilities, which is why this
    holds; it is asserted because the clip is one of the things a mutation removes.
    """
    enricher, _expected = real
    facts = enricher.enrich(
        file_record(rel_path="faces_parity.png"), loader_over(FIXTURE_IMAGE)
    )

    for fact in facts[1:]:
        assert fact.confidence is not None
        assert 0.0 <= fact.confidence <= 1.0
    assert facts[1].confidence >= CANDIDATE_SCORE


@pytest.mark.faces
def test_a_real_pack_gives_the_enricher_the_identity_the_digest_computes(real):
    """The identity a face row is stamped with is A4's digest, never a directory name.

    Compared against `faces_model.identity()` - the function A4 names - rather than
    against a hard-coded digest, because the digest is a function of the pack the machine
    happens to have and pinning its VALUE here would pin the pack instead of the wiring.
    `tests/test_faces_model.py` is where the pack's own hashes are pinned.
    """
    enricher, _expected = real
    assert (enricher.model_name, enricher.model_version) == faces_model.identity(model_dir())
    assert enricher.model_version != UNKNOWN_VERSION
    assert len(enricher.model_version) == 12


@pytest.mark.faces
def test_available_is_true_with_a_real_pack_and_names_the_two_files(real):
    """The positive half of `available()`. It must not load a session to answer - that is
    `describe`'s contract - and it must say what it found, because a green line that names
    nothing is a green line nobody can check."""
    enricher, _expected = real
    ok, sentence = enricher.available()
    assert ok is True
    assert faces_model.DETECTOR_FILE in sentence
    assert faces_model.RECOGNIZER_FILE in sentence


# --- the controls this file must not lose ---------------------------------------------
#
# M1670: a control nobody registered is a control the next task deletes with the suite
# green - measured at the M10 close, where four canary rows proving vision S4 for the
# captioner could be deleted with 2955 tests passing and the canary's own floor happy.
# Hand-spelled, never derived from `globals()`: a list built from the module's own
# contents is satisfied by whatever the module happens to contain.

LOAD_BEARING_CONTROLS = {
    "test_wants_refuses_a_cloud_placeholder_and_no_file_is_ever_opened": (
        "vision R2 and M697: the ONLY assertion in the repository that pam does not"
        " hydrate a placeholder by handing it to the face detector, and it watches the"
        " OPEN rather than the answer"
    ),
    "test_the_open_watch_would_actually_see_an_open": (
        "the negative control on that watch - an empty list from a spy nothing calls"
        " looks identical to an empty list from a correct refusal"
    ),
    "test_a_placeholder_video_is_refused_by_each_operand_on_its_own": (
        "operand-by-operand, because a whole-guard deletion is credited with kills its"
        " operands do not have (measured 8 vs 1)"
    ),
    "test_a_sub_floor_detection_is_STORED_and_the_floor_is_applied_when_it_is_READ": (
        "A11 in BOTH directions, over the same rows and with no re-enrichment: the"
        " lowering is the half rev 2's write-time floor could not express"
    ),
    "test_the_candidate_threshold_sits_below_the_configured_floor_which_is_what_A11_needs": (
        "the property that makes A11 reversible downwards at all; a candidate threshold"
        " raised to the floor would silently undo it with every other test still green"
    ),
    "test_the_enricher_re_types_none_of_the_packs_measured_constants": (
        "A4's digest covers `faces_model`'s constants; a second spelling in `faces.py`"
        " would make a pack swap invalidate nothing"
    ),
    "test_the_generation_refusal_is_keyed_on_the_sink_and_not_on_the_name_faces": (
        "A6/A9 are keyed on the SINK, in the amendments' own words; a check on the"
        " string 'faces' passes every other test in this file"
    ),
    "test_a_differing_model_NAME_is_refused_too_not_only_a_differing_version": (
        "A6 says (model_name, model_version); a version-only check lets a second STACK"
        " write into the same table"
    ),
    "test_refresh_still_works_for_an_enricher_without_a_sink": (
        "the other direction of A9 - a refusal that fired for everybody would pass the"
        " refusal test and break `pam enrich phash --refresh-older-than-model`"
    ),
    "test_every_disableable_enricher_names_the_key_that_turns_it_on": (
        "its `[faces]` node id is the SUBJECT of test_faces_model.py's marker-hook"
        " negative control, which M11 Task 8 would otherwise have left pointing at a"
        " parametrize id that no longer exists (M743's shape)"
    ),
    "test_BUILDERS_gains_faces_after_phash_and_geocode_in_that_order": (
        "HT-M10's F1 was this map's ORDER being silently load-bearing; a set comparison"
        " cannot see order"
    ),
    "test_the_faces_builder_takes_the_shared_decoder_and_survives_an_unset_model_dir": (
        "I4 at the wiring, and the precondition `tests/test_phash.py`'s pixel-builder"
        " guard needs in order to cover `faces` without being edited"
    ),
    "test_a_real_photograph_produces_one_marker_and_one_face_matching_the_fixture": (
        "the only comparison of this module's decode against an INDEPENDENT second"
        " implementation (Task 7's `_detect`, via the recorded fixture)"
    ),
    "test_the_stored_embedding_is_128_float32s_and_is_unit_LENGTH": (
        "pam normalises and the model does not; a stored raw vector would make every"
        " cosine distance in Task 9 a different number and nothing else would notice"
    ),
    "test_the_literal_walk_would_actually_see_a_re_typed_constant": (
        "the negative control on the walk above (M1773): narrowed to a type nothing is"
        " spelled with it collects nothing, and an absence assertion is green for ever"
    ),
    "test_a_degenerate_box_embeds_to_nothing_rather_than_to_a_vector": (
        "`_embed`'s own docstring says the zero-length branch is not a 'cannot happen';"
        " nothing constructed it until stage 1 (owed disclosure 1)"
    ),
    "test_a_detection_that_embeds_to_nothing_is_dropped_logged_and_not_counted": (
        "the caller's half of the same branch, and the MARKER half of it: a dropped"
        " detection that was still counted makes coverage claim a row that does not exist"
    ),
}

assert LOAD_BEARING_CONTROLS, "this module's control list is empty; it protects nothing"


def test_no_load_bearing_control_in_this_file_can_be_deleted_without_saying_so():
    """The anti-deletion floor. Removing one of these is a deliberate edit to
    `LOAD_BEARING_CONTROLS`, with the reason it was kept written beside it, in the same
    commit - which is M10's condition C4 applied as the control lands rather than after."""
    present = {
        node.name
        for node in ast.parse(Path(__file__).read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef)
    }
    missing = sorted(set(LOAD_BEARING_CONTROLS) - present)
    assert not missing, (
        f"a load-bearing control was deleted or renamed: {missing}"
        f" ({[LOAD_BEARING_CONTROLS[m] for m in missing]})."
    )
