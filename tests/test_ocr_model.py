"""`pam/enrich/ocr_model.py`: the pack, the lazy `Engine`, its lock, and the real fixtures.

THREE LAYERS, and only the last needs the pack (M12 Task 12 Part B, Steps B6-B8):

1. UNMARKED - the REAL `Engine`, driven by `tests/ocr_stack.FakeFactory` (R10). What pam passes
   to the session constructor, each session's provider assertion on its own, the charset check,
   the pack validation inside `read`, the absence of any score filter (a 0.45 line comes back),
   `describe`'s Q4 sentences verbatim, and `identity`'s digest inputs one by one.
2. UNMARKED - I17's lock, on the real `Engine` with the fake factory: eight threads on a cold
   engine build each session ONCE and never overlap a `run`; a failed initialisation releases the
   lock and the waiter initialises and succeeds.
3. `ocr`-MARKED - the real sessions on the committed fixtures. `tests/fixtures/ocr/truth.json` is
   the CORRECTNESS ORACLE, hand-written before any run; `baseline.json` is a REGRESSION baseline
   and says only "something moved" (R3). Under rev 4.2's R42-A the correctness test governs the
   `k11: false` fixtures, comparing each line's text with ALL whitespace removed and computing the
   date from the RAW lines; the `k11: true` fixtures are held to K11's one rule, that none yields
   a date truth does not name (R42-D).

The fixture INVENTORY is unmarked on purpose: it needs no stack, and a guard that only runs in
the `ocr` job would leave a deleted fixture invisible on every push.
"""

import hashlib
import json
import math
import os
import subprocess
import sys
import threading
from pathlib import Path

import numpy
import pytest
from ocr_stack import FakeFactory, fake_pack
from PIL import Image

from pam import __version__
from pam.enrich import faces_model, ocr_detect, ocr_model, ocr_recognize
from pam.naming.ocr_dates import OcrLine, find_date


# The pip route to an optional extra, spelled from the maintainer's decision 4 (2026-11-21), never read
# from `pam.install_method`: the release's own wheel for the running version, never a bare PyPI name.
def _pip(extra: str) -> str:
    v = __version__
    return (
        f'pip install "photo-archive-manager[{extra}] @ https://github.com/NuncProTunc7/'
        f'photo-archive-manager/releases/download/v{v}/photo_archive_manager-{v}-py3-none-any.whl"'
    )


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "ocr"
TRUTH = FIXTURES / "truth.json"
BASELINE = FIXTURES / "baseline.json"

# Q4's second line, spelled out HERE from the design spec's words, not read from the module.
PUT = (
    "  Put ch_PP-OCRv4_det_infer.onnx and ch_PP-OCRv4_rec_infer.onnx from the"
    " rapidocr_onnxruntime 1.4.4 wheel on PyPI in one folder and set model_dir to it"
    " (pam never downloads a model)."
)


def _image(h=100, w=200):
    return Image.new("RGB", (w, h), (255, 255, 255))


def _bounded(call, *args):
    """`call(*args)` on another thread, FAILING after 10 s instead of hanging (M2227): a read
    that leaves the engine's lock held deadlocks the next read on the same thread, and a hung
    suite reports nothing. The call's own exception is re-raised here."""
    outcome = {}

    def target():
        try:
            outcome["value"] = call(*args)
        except BaseException as error:  # re-raised on the test's thread below
            outcome["error"] = error

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout=10)
    if thread.is_alive():
        pytest.fail("the read did not return within 10 s: the engine's lock was not released")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


@pytest.fixture
def pack(tmp_path, monkeypatch):
    return fake_pack(tmp_path / "pack", monkeypatch)


# --- 1. the Engine, on the fake factory (unmarked) ---------------------------------------------


def test_constructing_an_engine_builds_nothing(pack):
    factory = FakeFactory()
    engine = ocr_model.Engine(pack, session_factory=factory)
    assert factory.calls == []
    assert engine.receipt() is None


def test_each_session_is_built_with_exactly_the_cpu_provider_list(pack):
    """A5, on the recorded CALL: both constructions pass `providers=["CPUExecutionProvider"]`."""
    factory = FakeFactory()
    ocr_model.Engine(pack, session_factory=factory).read(_image())
    assert factory.calls == [
        (str(pack / "ch_PP-OCRv4_det_infer.onnx"), ["CPUExecutionProvider"]),
        (str(pack / "ch_PP-OCRv4_rec_infer.onnx"), ["CPUExecutionProvider"]),
    ]


@pytest.mark.parametrize("role", ["detector", "recognizer"])
def test_a_session_reporting_azure_is_refused_runs_nothing_and_is_retried(pack, role):
    """Each session's `get_providers()` assertion on its OWN: a fake whose ONE session reports
    Azure is refused, NO `run` is made, and the engine stays uninitialised - so the next read
    builds again (and is refused again) rather than using what it built."""
    azure = ["AzureExecutionProvider", "CPUExecutionProvider"]
    factory = FakeFactory(**{f"{role}_providers": azure})
    engine = ocr_model.Engine(pack, session_factory=factory)
    with pytest.raises(ocr_model.OcrModelError, match="Azure"):
        _bounded(engine.read, _image())
    first = len(factory.calls)
    with pytest.raises(ocr_model.OcrModelError, match="Azure"):
        _bounded(engine.read, _image())
    assert factory.runs == []
    assert len(factory.calls) == 2 * first  # the second read RE-BUILT: still uninitialised
    assert engine.receipt() is None


def test_a_charset_that_does_not_match_the_output_is_refused(pack):
    factory = FakeFactory()
    factory.classes = 6  # the dictionary gives 3 + 2 = 5 classes
    engine = ocr_model.Engine(pack, session_factory=factory)
    with pytest.raises(ocr_model.OcrModelError, match="5 classes and its output has 6"):
        engine.read(_image())
    assert factory.runs == []
    assert engine.receipt() is None


def test_a_pack_that_fails_validation_is_refused_inside_read_before_the_factory(tmp_path):
    """R10: a direct caller cannot skip the checks `describe` makes. The factory is a SPY -
    a refusal that built a session first and then refused would look the same from outside."""
    factory = FakeFactory()
    engine = ocr_model.Engine(tmp_path / "not-a-pack", session_factory=factory)
    with pytest.raises(ocr_model.OcrModelError, match="names a folder without"):
        engine.read(_image())
    assert factory.calls == []


def test_a_line_scoring_0_45_is_returned_because_there_is_no_score_filter(pack):
    """I16 / R11: no score filter of any kind anywhere in `Engine.read`. The fake recogniser
    says "a" at 0.45, blank, "b" at 0.45, blank: one line "ab" whose score is the mean of two
    float32 0.45s = float(numpy.float32(0.45)) = 0.44999998807907104 - below the plan-time floor
    of 0.5, and returned all the same, because the floor is applied at PLAN time."""
    engine = ocr_model.Engine(pack, session_factory=FakeFactory())
    assert engine.read(_image()) == (OcrLine("ab", 0.44999998807907104),)


def test_the_receipt_names_the_identity_and_each_sessions_providers(pack):
    engine = ocr_model.Engine(pack, session_factory=FakeFactory())
    engine.read(_image())
    assert engine.receipt() == (
        f"ocr model ppocr-v4 {ocr_model.identity(pack)[1]};"
        " detector providers ['CPUExecutionProvider'];"
        " recognizer providers ['CPUExecutionProvider']"
    )


def test_a_failure_after_the_providers_check_publishes_no_receipt_and_is_retried(pack):
    """B4: `receipt()` is None until initialisation has SUCCEEDED (Part B review, F1). The
    recogniser's `get_inputs()` - the last read `_initialise` makes - raises once: the first
    read raises, no receipt is published for an engine that is still uninitialised, and the
    retry builds again and succeeds with a receipt."""

    class LateFailure(FakeFactory):
        def __call__(self, path, *, providers):
            session = super().__call__(path, providers=providers)
            if session.role == "recognizer" and not hasattr(self, "failed"):
                self.failed = True

                def get_inputs():
                    raise RuntimeError("the fake recogniser's inputs could not be read")

                session.get_inputs = get_inputs
            return session

    factory = LateFailure()
    engine = ocr_model.Engine(pack, session_factory=factory)
    with pytest.raises(RuntimeError, match="inputs could not be read"):
        _bounded(engine.read, _image())
    assert engine.receipt() is None
    assert factory.runs == []
    assert _bounded(engine.read, _image()) == (OcrLine("ab", 0.44999998807907104),)
    assert len(factory.calls) == 4  # the second read RE-BUILT both sessions
    assert engine.receipt() is not None


def test_an_image_below_min_side_reads_as_no_lines_and_runs_no_graph(pack):
    factory = FakeFactory()
    assert ocr_model.Engine(pack, session_factory=factory).read(_image(h=29, w=300)) == ()
    assert factory.runs == []


def test_ocr_providers_ARE_the_faces_providers_object():
    """A5: imported, never copied - a list written twice disagrees with itself once."""
    assert ocr_model.PROVIDERS is faces_model.PROVIDERS


# --- describe: Q4's sentences, verbatim ---------------------------------------------------------


@pytest.fixture
def extra_present(monkeypatch):
    monkeypatch.setattr(ocr_model, "_missing_extra", lambda: [])


def test_describe_an_unset_model_dir(extra_present):
    assert ocr_model.describe("") == (False, f"[enrich.ocr] model_dir is not set.\n{PUT}")
    assert ocr_model.describe("   ") == (False, f"[enrich.ocr] model_dir is not set.\n{PUT}")


def test_describe_a_folder_without_a_file(extra_present, tmp_path):
    assert ocr_model.describe(tmp_path) == (
        False,
        "[enrich.ocr] model_dir names a folder without ch_PP-OCRv4_det_infer.onnx.\n" + PUT,
    )
    (tmp_path / "ch_PP-OCRv4_det_infer.onnx").write_bytes(b"x")
    ok, sentence = ocr_model.describe(tmp_path)
    assert ok is False
    # the detector is checked first; its bytes do not match, so ITS mismatch is named
    assert "holds a ch_PP-OCRv4_det_infer.onnx whose SHA-256 is" in sentence


def test_describe_a_file_whose_bytes_are_not_the_pinned_ones(extra_present, tmp_path):
    """The hash mismatch, on a REAL temporary file, hashed here by hashlib independently."""
    (tmp_path / "ch_PP-OCRv4_det_infer.onnx").write_bytes(b"not a model")
    got = hashlib.sha256(b"not a model").hexdigest()
    assert ocr_model.describe(tmp_path) == (
        False,
        "[enrich.ocr] model_dir holds a ch_PP-OCRv4_det_infer.onnx whose SHA-256 is"
        f" {got}, not d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9.\n{PUT}",
    )


def test_describe_the_recogniser_is_checked_too(extra_present, tmp_path, monkeypatch):
    (tmp_path / "ch_PP-OCRv4_det_infer.onnx").write_bytes(b"d")
    monkeypatch.setattr(ocr_model, "DETECTOR_SHA256", hashlib.sha256(b"d").hexdigest())
    assert ocr_model.describe(tmp_path) == (
        False,
        "[enrich.ocr] model_dir names a folder without ch_PP-OCRv4_rec_infer.onnx.\n" + PUT,
    )


def test_describe_names_a_recogniser_whose_bytes_are_not_the_pinned_ones(
    extra_present, tmp_path, monkeypatch
):
    """The detector hashes right, the recogniser does not (Part B review, M2246): without this
    the recogniser's hash check was covered only by sharing a loop with the detector's."""
    (tmp_path / "ch_PP-OCRv4_det_infer.onnx").write_bytes(b"d")
    monkeypatch.setattr(ocr_model, "DETECTOR_SHA256", hashlib.sha256(b"d").hexdigest())
    (tmp_path / "ch_PP-OCRv4_rec_infer.onnx").write_bytes(b"not a model")
    got = hashlib.sha256(b"not a model").hexdigest()
    assert ocr_model.describe(tmp_path) == (
        False,
        "[enrich.ocr] model_dir holds a ch_PP-OCRv4_rec_infer.onnx whose SHA-256 is"
        f" {got}, not 48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b.\n{PUT}",
    )


def test_describe_accepts_a_pack_that_hashes_to_the_pins(extra_present, pack):
    ok, sentence = ocr_model.describe(pack)
    assert ok is True
    assert "ppocr-v4" in sentence


def test_describe_names_the_extra_when_it_is_missing(monkeypatch, pack):
    """Q4's missing-extra sentence, spelled from design 12 rev 28 - WITH its final period
    (`Run: pip install "photo-archive-manager[ocr]".`), which the Task 12 review's F4 found missing."""
    monkeypatch.setattr(ocr_model, "_missing_extra", lambda: ["onnxruntime"])
    ok, sentence = ocr_model.describe(pack)
    assert ok is False
    assert sentence == (
        f"the optional [ocr] extra is not installed (onnxruntime missing). Run: {_pip('ocr')}."
    )


def test_the_pinned_pack_constants_are_q1s():
    """Spelled from design 1 rev 29 (Q1), never from the module."""
    assert ocr_model.DETECTOR_FILE == "ch_PP-OCRv4_det_infer.onnx"
    assert ocr_model.DETECTOR_SHA256 == (
        "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9"
    )
    assert ocr_model.RECOGNIZER_FILE == "ch_PP-OCRv4_rec_infer.onnx"
    assert ocr_model.RECOGNIZER_SHA256 == (
        "48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b"
    )
    assert ocr_model.MODEL_NAME == "ppocr-v4"
    assert ocr_model.OCR_PIPELINE_VERSION == "1"
    assert (
        "971d7d5f223a7a808662229df1ef69893809d8457d834e6373d3854bc1782cbf" in ocr_model.SOURCE
    )


# --- identity: Q2's inputs, each one ------------------------------------------------------------

# Q2's digest inputs, BY HAND from the design spec: both pack hashes, the provider list, every
# constant in 8.1's pipeline table (P-1 to P-12, the channel orders included), and
# OCR_PIPELINE_VERSION - each with the module that owns it, so the test can move it.
IDENTITY_INPUTS = {
    "OCR_PIPELINE_VERSION": ocr_model,
    "DETECTOR_SHA256": ocr_model,
    "RECOGNIZER_SHA256": ocr_model,
    "PROVIDERS": ocr_model,
    "MAX_SIDE": ocr_detect,
    "MIN_SIDE": ocr_detect,
    "RESAMPLE": ocr_detect,
    "DET_LIMIT": ocr_detect,
    "DET_MAX_SIDE": ocr_detect,
    "DET_CHANNELS": ocr_detect,
    "THRESH": ocr_detect,
    "DILATE": ocr_detect,
    "CONNECTIVITY": ocr_detect,
    "MAX_CANDIDATES": ocr_detect,
    "MIN_SIZE": ocr_detect,
    "BOX_THRESH": ocr_detect,
    "UNCLIP": ocr_detect,
    "ORDER_TOL": ocr_detect,
    "ROTATE_RATIO": ocr_recognize,
    "REC_H": ocr_recognize,
    "REC_MIN_W": ocr_recognize,
    "REC_CHANNELS": ocr_recognize,
    "BLANK": ocr_recognize,
}
assert IDENTITY_INPUTS, "the identity-input list is empty; the digest test would test nothing"
assert (
    len(IDENTITY_INPUTS) == 23
)  # 4 + P-1 3 + P-2 3 + P-3 2 + P-4 2 + P-5/6 3 + P-8 + P-9 + P-10 3 + P-11


def test_the_identity_rows_are_exactly_q2s_inputs_both_ways():
    rows = [row.split("=", 1)[0] for row in ocr_model.identity_rows()]
    assert len(rows) == len(set(rows)), rows
    assert set(rows) - set(IDENTITY_INPUTS) == set(), "digested but not written down"
    assert set(IDENTITY_INPUTS) - set(rows) == set(), "written down but not digested"


def _moved(value):
    if isinstance(value, tuple):
        return (*value, "AzureExecutionProvider")
    if isinstance(value, str):
        return value + "x"
    if isinstance(value, float):
        return value + 0.125
    return value + 1


@pytest.mark.parametrize("name", sorted(IDENTITY_INPUTS))
def test_the_identity_moves_when_each_input_moves(name, monkeypatch):
    before = ocr_model.identity("anywhere")
    module = IDENTITY_INPUTS[name]
    monkeypatch.setattr(module, name, _moved(getattr(module, name)))
    assert ocr_model.identity("anywhere") != before, f"{name} is not a digest input"


def test_the_identity_is_the_same_for_every_folder():
    """A pack is admitted only if its bytes hash to the pins, so a folder name moves nothing."""
    assert ocr_model.identity("a") == ocr_model.identity("somewhere/else")
    name, version = ocr_model.identity("a")
    assert name == "ppocr-v4"
    assert len(version) == 12


def test_the_ocr_modules_never_reach_the_eager_import_closure():
    """In a FRESH interpreter: importing the three OCR modules costs neither onnxruntime nor numpy
    (the session may already hold numpy through Pillow or imagehash)."""
    probe = (
        "import sys, pam.enrich.ocr_model, pam.enrich.ocr_detect, pam.enrich.ocr_recognize\n"
        "print(int('onnxruntime' in sys.modules), int('numpy' in sys.modules))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=120,
        check=True,
    )
    assert result.stdout.split() == ["0", "0"], result.stdout


# --- 2. I17: the lock (unmarked) -----------------------------------------------------------------


def test_eight_threads_on_a_cold_engine_build_each_session_once_and_never_overlap(pack):
    factory = FakeFactory()
    factory.run_delay = 0.01  # long enough that two unlocked runs WOULD overlap
    engine = ocr_model.Engine(pack, session_factory=factory)
    barrier = threading.Barrier(8)
    results, errors = [], []

    def worker():
        try:
            barrier.wait(timeout=10)
            results.append(engine.read(_image()))
        except Exception as error:  # pragma: no cover - reported below
            errors.append(error)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    assert len(results) == 8
    assert len(factory.constructions("detector")) == 1
    assert len(factory.constructions("recognizer")) == 1
    assert factory.max_active == 1, (
        "two `run` calls overlapped: the lock does not cover inference"
    )
    assert factory.runs.count("detector") == 8


def test_a_failed_initialisation_releases_the_lock_and_the_waiter_initialises(pack):
    """The first read's factory raises while a second thread waits. The raise must release the
    lock (else the waiter hangs) and leave the engine uninitialised (so the waiter BUILDS)."""
    entered, release, waiter_started = threading.Event(), threading.Event(), threading.Event()

    class Gated(FakeFactory):
        def __call__(self, path, *, providers):
            if not entered.is_set():
                entered.set()
                assert release.wait(10)
            return super().__call__(path, providers=providers)

    factory = Gated(fail_times=1)
    engine = ocr_model.Engine(pack, session_factory=factory)
    outcome = {}

    def first():
        try:
            engine.read(_image())
        except RuntimeError as error:
            outcome["first"] = error

    def second():
        waiter_started.set()
        outcome["second"] = engine.read(_image())

    # daemon: a thread stuck on a held lock must fail this test, not hang interpreter exit (M2250)
    a = threading.Thread(target=first, daemon=True)
    a.start()
    assert entered.wait(10)
    b = threading.Thread(target=second, daemon=True)
    b.start()
    assert waiter_started.wait(10)
    b.join(timeout=0.2)  # give the waiter time to block on the lock
    release.set()
    a.join(timeout=10)
    b.join(timeout=10)
    assert not a.is_alive() and not b.is_alive(), "a thread is stuck: the lock was not released"
    assert isinstance(outcome.get("first"), RuntimeError)
    assert outcome.get("second") == (OcrLine("ab", 0.44999998807907104),)
    # the failed detector attempt, then the waiter's detector and recognizer
    assert len(factory.calls) == 3
    assert engine.receipt() is not None


# --- 3. the fixtures ---------------------------------------------------------------------------

# The committed fixtures, BY HAND. Compared both ways with the PNGs on disk, truth.json's keys and
# baseline.json's keys. low_contrast.png was retired by rev 4.2's R42-B.
FIXTURE_NAMES = (
    "blank.png",
    "border.png",
    "colour_blue_on_red.png",
    "colour_red_on_blue.png",
    "date_imprint.png",
    "date_iso.png",
    "date_words.png",
    "inverted.png",
    "mixed.png",
    "order.png",
    "solid_red.png",
    "tilt_3.png",
    "tilt_8.png",
)
assert len(FIXTURE_NAMES) == 13
# How many fixtures truth marks `k11: false` - the ones the correctness test governs (R42-A).
GOVERNED_COUNT = 8


def _truth():
    return json.loads(TRUTH.read_text(encoding="utf-8"))["fixtures"]


def _governed(truth):
    """The fixtures K4 and the correctness test govern: truth's own `k11: false` partition."""
    return sorted(name for name, entry in truth.items() if entry["k11"] is False)


def _k11(truth):
    return sorted(name for name, entry in truth.items() if entry["k11"] is True)


def test_the_fixture_inventory_is_the_same_thirteen_everywhere():
    on_disk = {path.name for path in FIXTURES.glob("*.png")}
    truth = _truth()
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["fixtures"]
    written = set(FIXTURE_NAMES)
    for label, found in (
        ("on disk", on_disk),
        ("truth.json", set(truth)),
        ("baseline", set(baseline)),
    ):
        assert found - written == set(), f"{label} has fixtures nobody wrote down"
        assert written - found == set(), f"{label} is missing fixtures"
    assert len(_governed(truth)) == GOVERNED_COUNT
    assert len(_k11(truth)) == len(FIXTURE_NAMES) - GOVERNED_COUNT


def _squeeze(text):
    return "".join(text.split())


def _read(engine, name):
    with Image.open(FIXTURES / name) as image:
        rgb = image.convert("RGB")
    return engine.read(rgb)


@pytest.fixture(scope="module")
def engine():
    from ocr_stack import model_dir

    return ocr_model.Engine(model_dir())


@pytest.mark.ocr
def test_every_governed_fixture_meets_truth_json(engine):
    """R42-A: line count and order exact; each line's TEXT compared with all whitespace removed
    from both sides (a list entry accepts any of its forms); the date is `find_date` over the RAW
    lines at floor 0.5 - never over normalised text, which is where whitespace is load-bearing."""
    truth = _truth()
    governed = _governed(truth)
    assert governed, "no fixture is governed; the correctness test would check nothing"
    failures = []
    for name in governed:
        expected = truth[name]
        lines = _read(engine, name)
        if len(lines) != len(expected["lines"]):
            failures.append(
                f"{name}: {len(lines)} lines {lines}, truth has {expected['lines']}"
            )
            continue
        for line, want in zip(lines, expected["lines"], strict=True):
            forms = want if isinstance(want, list) else [want]
            if _squeeze(line.text) not in {_squeeze(form) for form in forms}:
                failures.append(f"{name}: read {line.text!r}, truth accepts {forms}")
        found = find_date(lines, floor=0.5)
        if (found.date if found else None) != expected["date"]:
            failures.append(f"{name}: date {found}, truth says {expected['date']}")
    assert failures == []


@pytest.mark.ocr
def test_no_k11_fixture_yields_a_date_truth_does_not_name(engine):
    """K11's WRONG = 0, pinned in CI (R42-D): a miss is tolerated, a wrong date never is."""
    truth = _truth()
    k11 = _k11(truth)
    assert k11, "no k11 fixture; the wrong-date check would check nothing"
    wrong = []
    for name in k11:
        found = find_date(_read(engine, name), floor=0.5)
        if found is not None and found.date != truth[name]["date"]:
            wrong.append(f"{name}: {found.date}, truth says {truth[name]['date']}")
    assert wrong == []


@pytest.mark.ocr
def test_every_fixture_matches_the_regression_baseline(engine):
    """Texts exact, scores within 1e-6 absolute: K6 measured them EXACT; the tolerance exists
    only for another CPU in CI. A failure here says SOMETHING MOVED, never that it is wrong."""
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["fixtures"]
    moved = []
    for name in FIXTURE_NAMES:
        got = [(line.text, line.score) for line in _read(engine, name)]
        want = [(entry["text"], entry["score"]) for entry in baseline[name]]
        same = len(got) == len(want) and all(
            g[0] == w[0] and math.isclose(g[1], w[1], rel_tol=0, abs_tol=1e-6)
            for g, w in zip(got, want, strict=True)
        )
        if not same:
            moved.append(f"{name}: {got} != {want}")
    assert moved == []


@pytest.mark.ocr
def test_the_real_sessions_run_on_the_cpu_provider_only(engine):
    _read(engine, "date_iso.png")
    assert engine.receipt() == (
        f"ocr model ppocr-v4 {ocr_model.identity('')[1]};"
        " detector providers ['CPUExecutionProvider'];"
        " recognizer providers ['CPUExecutionProvider']"
    )


# --- the marked set is a decision, not an accident (A2) ---------------------------------------

# Every `ocr`-marked test in the repository, BY HAND. Compared both ways with what `-m ocr`
# collects, and in `tests/test_ci_lanes.py` with `OCR_JOB_MUST_PASS`, the second hand spelling.
OCR_MARKED_TESTS = {
    "tests/test_ocr_model.py::test_every_governed_fixture_meets_truth_json",
    "tests/test_ocr_model.py::test_no_k11_fixture_yields_a_date_truth_does_not_name",
    "tests/test_ocr_model.py::test_every_fixture_matches_the_regression_baseline",
    "tests/test_ocr_model.py::test_the_real_sessions_run_on_the_cpu_provider_only",
    "tests/test_net_canary.py::test_the_ocr_engine_loads_and_reads_without_a_python_socket",
    "tests/test_ocr_enricher.py::test_the_real_engine_reads_a_committed_imprint_and_the_plan_dates_the_scan",  # M12 Task 13
    "tests/test_t1_ocr_chain.py::test_the_m12_chain_on_the_REAL_pack_reads_the_rendered_imprints",  # M12 Task 14
}
assert OCR_MARKED_TESTS, "the ocr-marked inventory is empty; the skip count is unpinned"


def test_the_ocr_marked_tests_are_exactly_the_ones_written_down():
    """NEVER SKIPS: it asks pytest what `-m ocr` would collect, which needs no pack."""
    result = subprocess.run(
        # `test_ci_lanes.py`'s probe fixture writes and deletes a file under `tests/`; a
        # concurrent run deleting it mid-walk failed this collection (M15, found by
        # sharded mutation runs). Only that one name is ignored.
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-m",
            "ocr",
            "--ignore-glob=*test_zz_ci_lane_probe_*",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=600,
        env={k: v for k, v in os.environ.items() if k != "PAM_OCR_REQUIRED"},
    )
    assert result.returncode == 0, result.stdout[-2000:]
    collected = {
        line.strip().replace("\\", "/").split("[")[0]
        for line in result.stdout.splitlines()
        if "::" in line
    }
    assert collected == OCR_MARKED_TESTS, (
        f"marked but not written down: {sorted(collected - OCR_MARKED_TESTS)};"
        f" written down but not marked: {sorted(OCR_MARKED_TESTS - collected)}"
    )


def test_the_fake_recogniser_output_really_decodes_to_one_line_below_the_floor():
    """The fake's own script, decoded by hand's rule - so the 0.45 test above is about the
    ENGINE, not about a fake that happened to emit something else."""
    output = FakeFactory().recognizer_output
    assert output.shape == (4, 5)
    assert [int(i) for i in numpy.argmax(output, axis=1)] == [1, 0, 2, 0]
    assert float(output[0, 1]) == 0.44999998807907104
