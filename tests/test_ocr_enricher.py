"""M12 Task 13: the `ocr` enricher and `pam enrich ocr` (design 8.1 rev 27-28, 11 rev 28, 12 rev 28).

THE ENGINE IS REAL AND ITS SESSIONS ARE FAKE (R10). Unless a test says otherwise it drives the
real `OcrEnricher` over the real `ocr_model.Engine`, whose `session_factory` is
`tests/ocr_stack.FakeFactory` with a SCRIPTED recogniser - so the pipeline, the lock, the lazy
initialisation and the pack validation are all production code, and only onnxruntime is not.
The command-level tests patch `ocr_model._onnx_session` (the default factory) to the fake, and
`ocr_model._missing_extra` to "nothing missing", because the repo venv has no `[ocr]` extra.

EVERY EXPECTATION IS SPELLED FROM THE SPEC (P7, P13, Q2-Q4) OR BY HAND - never read back from a
production constant: the stored value is a literal, the sentences are literals, the dates are
literals. The one exception is the model VERSION, a digest of constants that
`tests/test_ocr_model.py` pins input by input; it is read from `ocr_model.identity` here.

WHAT THE DANGEROUS CALLS ARE, and each is WATCHED rather than inferred from an outcome:
the OPEN of a placeholder (a loader that FAILS THE TEST if called - M697), the factory's
constructions (an all-cached run builds no session; a run builds each session once), and the
argument `Engine.read` actually receives (upright, RGB - the enricher's job, not the loader's).

THREAD WAITS ARE BOUNDED. Where workers meet on a barrier or on the engine's lock, the barrier
has a timeout and the lock is swapped for one whose acquire gives up after 10 s - so a lock
regression FAILS a test (a file counted failed) instead of hanging the suite (M2227).
"""

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import numpy
import pytest
from archives import FAKE_COMMAND
from ocr_stack import FakeFactory, fake_pack
from PIL import Image, ImageOps

from pam import __version__
from pam.catalog import repo
from pam.catalog.db import connect
from pam.cli import main
from pam.config import Config, ConfigError, load_config
from pam.enrich import InferredFact, ocr_model, run_enricher
from pam.enrich import ocr as ocr_module
from pam.enrich.base import FileRecord, pil_image_loader
from pam.enrich.command import BUILDERS
from pam.enrich.ocr import OcrEnricher
from pam.enrich.sink import digest_refusal, refresh_refusal
from pam.errors import EXIT_INTERNAL
from pam.naming.ocr_dates import OcrLine
from pam.naming.planner import run_plan
from pam.paths import workspace_for
from pam.scan.runner import run_scan


# The pip route to an optional extra, spelled from the maintainer's decision 4 (2026-11-21), never read
# from `pam.install_method`: the release's own wheel for the running version, never a bare PyPI name.
def _pip(extra: str) -> str:
    v = __version__
    return (
        f'pip install "photo-archive-manager[{extra}] @ https://github.com/NuncProTunc7/'
        f'photo-archive-manager/releases/download/v{v}/photo_archive_manager-{v}-py3-none-any.whl"'
    )


FIXTURES = Path(__file__).parent / "fixtures" / "ocr"
MTIME = datetime(2024, 5, 1, 12, 0, 0)  # every mtime-dated file's catalog date: 2024-05-01

# Q4's sentences, spelled from design 12 rev 28 - not read from `ocr_model`.
PUT = (
    "  Put ch_PP-OCRv4_det_infer.onnx and ch_PP-OCRv4_rec_infer.onnx from the"
    " rapidocr_onnxruntime 1.4.4 wheel on PyPI in one folder and set model_dir to it"
    " (pam never downloads a model)."
)
# Q1's pinned hashes, spelled from design 1 rev 29.
DETECTOR_SHA = "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9"
RECOGNIZER_SHA = "48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b"
# P13's lines, spelled from design 12 rev 27.
NO_FOLDERS = [
    "ocr: not run - [enrich.ocr] folders is empty in pam.toml.",
    '  List the folders that hold scanned photos, for example folders = ["Scans"].',
]
DATES_LATER = "  Dates are read from that text when you run pam plan."
# P13 / D7: the refresh refusal - a new engine is used automatically; going back is not v1.0.
DIGEST_REFUSAL = (
    "--refresh-older-than-model cannot be used with ocr: its model version is a digest,"
    " which has no order, so no reading is older than another. A new ocr engine is used"
    " automatically - just run pam enrich ocr. Going back to an earlier engine's readings"
    " is not something pam v1.0 can do."
)


# --------------------------------------------------------------------------- the fakes


def script(factory, text, score):
    """Make the fake recogniser decode to exactly `text` at `score` (P-11 by hand).

    The charset is the text's own characters ("<blank>" first, " " last, as P-11 builds it);
    each character is one timestep at `score`, followed by a blank so repeats survive the CTC
    collapse. `score` should be exact in float32 (0.9375, 0.5) or the stored score is
    float32's nearest value, which is what P-11's mean of float32 probabilities gives.
    """
    chars = sorted(set(text) - {" "})
    classes = len(chars) + 2
    index = {char: position + 1 for position, char in enumerate(chars)}
    index[" "] = classes - 1
    rest = (1.0 - score) / (classes - 1)
    rows = []
    for char in text:
        row = [rest] * classes
        row[index[char]] = score
        rows.append(row)
        rows.append([1.0] + [0.0] * (classes - 1))
    factory.characters = "\n".join(chars)
    factory.classes = classes
    factory.recognizer_output = numpy.array(rows, dtype=numpy.float32)
    return factory


def no_text(height, width):
    """A detector map with nothing above THRESH: P-3 finds no component, so no line."""
    return numpy.zeros((1, 1, height, width), dtype=numpy.float32)


class BoundedLock:
    """The engine's lock with a 10 s acquire, so a lock that is never released FAILS a file."""

    def __init__(self):
        self._lock = threading.Lock()

    def acquire(self, blocking=True, timeout=-1):
        if not self._lock.acquire(timeout=10):
            raise TimeoutError("the OCR engine's lock was never released")
        return True

    def release(self):
        self._lock.release()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()


# --------------------------------------------------------------------------- the archive


def picture(path, *, size=(200, 100), colour=(255, 255, 255), mode="RGB", exif=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new(mode, size, colour if mode == "RGB" else colour[0])
    if exif is None:
        image.save(path)
    else:
        image.save(path, "JPEG", exif=exif, quality=95)
    return path


def build(tmp_path, files, *, name="photos"):
    """`{rel_path: None (mtime-dated) | "YYYY:MM:DD HH:MM:SS" (EXIF-dated) | bytes}`, scanned.

    Every picture is a distinct colour or size, so no two share a sha256. A file with no EXIF
    date and no date in its name is dated by its mtime (2024-05-01).
    """
    root = tmp_path / name
    for number, (rel, spec) in enumerate(files.items()):
        path = root / rel
        if isinstance(spec, bytes):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(spec)
        elif not path.exists():
            picture(path, colour=(number % 256, (number * 7) % 256, 200))
        tags = {"DateTimeOriginal": spec} if isinstance(spec, str) else {}
        (root / f"{rel}.tags.json").write_text(
            json.dumps({**tags, "PamTestSidecar": rel}), encoding="utf-8"
        )
        stamp = MTIME.timestamp()
        os.utime(path, (stamp, stamp))
    run_scan(root, exiftool_command=FAKE_COMMAND)
    return root


def configure(root, body):
    workspace_for(root).config_path.write_text(body, encoding="utf-8")


def ocr_config(pack, folders='["Scans"]', extra=""):
    return f"[enrich.ocr]\nenabled = true\nfolders = {folders}\nmodel_dir = '{pack}'\n{extra}"


def catalog(root):
    conn = connect(workspace_for(root).db_path)
    conn.row_factory = sqlite3.Row
    return conn


def file_id(root, rel):
    conn = catalog(root)
    try:
        return conn.execute("SELECT id FROM files WHERE rel_path = ?", (rel,)).fetchone()[0]
    finally:
        conn.close()


def readings(root):
    """`{rel_path: value_json}` of every live `ocr_text` row, read with the suite's own SQL."""
    conn = catalog(root)
    try:
        rows = conn.execute(
            "SELECT f.rel_path, i.value_json FROM inferred_facts i"
            " JOIN files f ON f.sha256 = i.sha256 WHERE i.kind = 'ocr_text'"
        ).fetchall()
    finally:
        conn.close()
    return {row["rel_path"]: row["value_json"] for row in rows}


def kinds(root):
    """Every `inferred_facts` row's kind - ALL kinds, not only OCR's (the partition lesson)."""
    conn = catalog(root)
    try:
        return sorted(row[0] for row in conn.execute("SELECT kind FROM inferred_facts"))
    finally:
        conn.close()


def enricher_for(root, pack, factory, folders=("Scans",), logger=None):
    conn = catalog(root)
    try:
        mtime = repo.file_ids_by_date_source(conn, "file_mtime")
    finally:
        conn.close()
    return OcrEnricher(str(pack), folders, mtime, logger, session_factory=factory)


class Kept(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def kept_logger():
    handler = Kept()
    logger = logging.getLogger(f"pam-test-ocr-{id(handler)}")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, handler


def run(root, enricher, *, loader=None, workers=1):
    conn = catalog(root)
    try:
        return run_enricher(conn, enricher, loader or pil_image_loader(root), workers=workers)
    finally:
        conn.close()


@pytest.fixture
def pack(tmp_path, monkeypatch):
    return fake_pack(tmp_path / "pack", monkeypatch)


@pytest.fixture
def cli_stack(monkeypatch, pack):
    """The command builds its own enricher: give it the fake factory and "the extra is here"."""
    factory = FakeFactory()
    monkeypatch.setattr(ocr_model, "_onnx_session", factory)
    monkeypatch.setattr(ocr_model, "_missing_extra", lambda: [])
    return factory


def enrich_cli(root, capsys, *extra):
    code = main(["enrich", "ocr", "--archive", str(root), *extra])
    out = capsys.readouterr()
    return code, out.out, out.err


def paragraph(out):
    """The `ocr` paragraph of `pam enrich`'s closing block, as lines."""
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("ocr: "))
    end = lines.index("", start)
    return lines[start:end]


# --- Step 2b: `[enrich.ocr] model_dir` (Q3) ---------------------------------------------------


def test_model_dir_is_empty_by_default_and_read_as_written(tmp_path):
    """Q3: empty means NOT CONFIGURED; a set value is its string; no existence check here."""
    assert load_config(tmp_path / "absent.toml").ocr_model_dir == ""
    assert Config().ocr_model_dir == ""
    toml = tmp_path / "pam.toml"
    toml.write_text("[enrich.ocr]\nmodel_dir = 'C:/no/such/folder'\n", encoding="utf-8")
    assert load_config(toml).ocr_model_dir == "C:/no/such/folder"


def test_a_model_dir_that_is_not_a_string_is_refused_like_every_other_key(tmp_path):
    toml = tmp_path / "pam.toml"
    toml.write_text("[enrich.ocr]\nmodel_dir = 7\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"^\[enrich\.ocr\] model_dir must be a string$"):
        load_config(toml)


# --- Step 2: the `wants()` input --------------------------------------------------------------


def test_file_ids_by_date_source_returns_exactly_the_mtime_dated_files(tmp_path):
    root = build(
        tmp_path,
        {"Scans/a.png": None, "Scans/b.png": "2019:07:04 18:23:11", "c.png": None},
    )
    conn = catalog(root)
    try:
        mtime = repo.file_ids_by_date_source(conn, "file_mtime")
        exif = repo.file_ids_by_date_source(conn, "exif")
    finally:
        conn.close()
    assert mtime == {file_id(root, "Scans/a.png"), file_id(root, "c.png")}
    assert exif == {file_id(root, "Scans/b.png")}


# --- Step 3: `wants()`, operand by operand -----------------------------------------------------


def record(**overrides):
    fields = {
        "id": 7,
        "rel_path": "Scans/a.png",
        "filename": "a.png",
        "ext": "png",
        "media_type": "image",
        "size": 10,
        "mtime_ns": 1,
        "sha256": "sha-a",
        "placeholder": False,
        "companion_group_id": None,
        "status": "present",
    }
    fields.update(overrides)
    return FileRecord(**{f: fields[f] for f in FileRecord.__dataclass_fields__ if f in fields})


# Each P7 operand ALONE false, every other one true. Hand-written, asserted non-empty (M743).
WANTS_REFUSALS = [
    ("a video", {"media_type": "video"}),
    ("a sidecar", {"media_type": "sidecar"}),
    ("a cloud placeholder", {"placeholder": True}),
    ("not dated by file_mtime", {"id": 8}),
    ("outside the folders", {"rel_path": "Desktop/a.png"}),
    ("in a folder whose name only STARTS like one", {"rel_path": "Scans-old/a.png"}),
]
assert len(WANTS_REFUSALS) == 6


def test_wants_is_true_when_all_four_operands_hold():
    enricher = OcrEnricher("", ("Scans",), frozenset({7}))
    assert enricher.wants(record()) is True


@pytest.mark.parametrize(
    ("why", "overrides"), WANTS_REFUSALS, ids=[w for w, _ in WANTS_REFUSALS]
)
def test_wants_refuses_each_operand_on_its_own(why, overrides):
    enricher = OcrEnricher("", ("Scans",), frozenset({7}))
    assert enricher.wants(record(**overrides)) is False, why


def test_wants_with_no_folders_wants_nothing():
    assert OcrEnricher("", (), frozenset({7})).wants(record()) is False


def spy_loader(opened):
    def load(file):
        opened.append(file.rel_path)
        pytest.fail(f"the OCR enricher opened {file.rel_path}, which it must not read")

    return load


def test_a_cloud_placeholder_in_scope_is_never_OPENED(tmp_path, pack):
    """Vision R2, by watching the OPEN (M697): the loader FAILS THE TEST if it is called.

    The file is in a declared folder and dated by mtime - every operand but the placeholder
    holds - so only `not placeholder` stands between it and a hydrating read.
    """
    root = build(tmp_path, {"Scans/cloud.png": None})
    conn = catalog(root)
    conn.execute("UPDATE files SET placeholder = 1 WHERE rel_path = 'Scans/cloud.png'")
    conn.commit()
    conn.close()
    opened = []

    summary = run(root, enricher_for(root, pack, FakeFactory()), loader=spy_loader(opened))

    assert opened == []
    assert summary.skipped_wants == 1 and summary.enriched == 0


def test_the_open_watch_would_actually_see_an_open(tmp_path, pack):
    """The control: the same spy over the same file, NOT a placeholder, is called - so the
    test above is green because nothing opened the file, not because the spy is blind."""
    root = build(tmp_path, {"Scans/local.png": None})
    opened = []

    with pytest.raises(pytest.fail.Exception):
        run(root, enricher_for(root, pack, FakeFactory()), loader=spy_loader(opened))

    assert opened == ["Scans/local.png"]


def test_out_of_folder_and_exif_dated_files_are_not_applicable(tmp_path, pack):
    root = build(
        tmp_path,
        {
            "Scans/scan.png": None,
            "Scans/camera.png": "2019:07:04 18:23:11",
            "Desktop/screenshot.png": None,
        },
    )
    factory = FakeFactory()

    summary = run(root, enricher_for(root, pack, factory))

    assert summary.enriched == 1
    assert sorted(readings(root)) == ["Scans/scan.png"]
    # The camera picture and the screenshot. (The fake exiftool's `.tags.json` sidecars are
    # catalogued unhashed, so the runner skips them as "never read from disk", not here.)
    assert summary.skipped_wants == 2


# --- Step 7: what is stored ------------------------------------------------------------------


def test_a_file_with_lines_stores_exactly_one_ocr_text_row_and_no_other_kind(tmp_path, pack):
    """P7: ONE fact per file read, value a literal, `confidence` NULL - and no other kind of
    row anywhere in the catalog, so a date computed at enrich time could not hide (M8)."""
    root = build(tmp_path, {"Scans/print.png": None})
    factory = script(FakeFactory(), "'98 7 4", 0.9375)

    summary = run(root, enricher_for(root, pack, factory))

    assert readings(root) == {
        "Scans/print.png": '{"lines": [{"score": 0.9375, "text": "\'98 7 4"}]}'
    }
    assert kinds(root) == ["ocr_text"]
    assert summary.facts_written_by_kind == {"ocr_text": 1}
    assert summary.facts_dropped == 0
    conn = catalog(root)
    try:
        row = conn.execute("SELECT confidence, model_name FROM inferred_facts").fetchone()
    finally:
        conn.close()
    assert row["confidence"] is None
    assert row["model_name"] == "ppocr-v4"  # Q2


def test_enrich_returns_exactly_one_ocr_text_fact_and_nothing_else(tmp_path, pack):
    """The enricher's own return value, before the runner validates or routes anything."""
    root = build(tmp_path, {"Scans/print.png": None})
    factory = script(FakeFactory(), "'98 7 4", 0.9375)
    enricher = enricher_for(root, pack, factory)
    conn = catalog(root)
    try:
        file = next(
            f for f in repo.files_to_enrich(conn, None) if f.rel_path == "Scans/print.png"
        )
    finally:
        conn.close()

    facts = enricher.enrich(file, pil_image_loader(root))

    assert facts == [
        InferredFact(kind="ocr_text", value={"lines": [{"text": "'98 7 4", "score": 0.9375}]})
    ]


def test_a_file_with_no_text_is_covered_and_skipped_on_the_second_run(tmp_path, pack):
    """I8: `{"lines": []}` is the reading AND the marker; the second run reads nothing."""
    root = build(tmp_path, {"Scans/blank.png": None})
    factory = FakeFactory()
    factory.detector_map = no_text

    first = run(root, enricher_for(root, pack, factory))
    runs_after_first = len(factory.runs)
    second = run(root, enricher_for(root, pack, factory))

    assert first.enriched == 1
    assert readings(root) == {"Scans/blank.png": '{"lines": []}'}
    assert second.enriched == 0 and second.skipped_cached == 1
    assert len(factory.runs) == runs_after_first


def test_an_engine_that_raises_stores_nothing_and_the_file_is_read_again(tmp_path, pack):
    """I8's other half: a RAISE is a per-file failure - no row, counted - never an empty
    reading, which would make the file look covered and never be read again."""
    root = build(tmp_path, {"Scans/print.png": None})
    factory = script(FakeFactory(), "'98 7 4", 0.9375)

    def boom(height, width):
        raise RuntimeError("the detector fell over")

    factory.detector_map = boom
    first = run(root, enricher_for(root, pack, factory))
    assert first.failed == 1 and first.enriched == 0
    assert readings(root) == {}

    del factory.detector_map  # the class's working map again
    second = run(root, enricher_for(root, pack, factory))
    assert second.enriched == 1 and second.failed == 0
    assert list(readings(root)) == ["Scans/print.png"]


class LinesEngine:
    """A stand-in engine returning fixed `OcrLine`s - for lines the real pipeline cannot make."""

    def __init__(self, lines):
        self._lines = lines

    def read(self, rgb):
        return self._lines

    def receipt(self):
        return "a stand-in engine"


@pytest.mark.parametrize("score", [1.5, True], ids=["score-1.5", "score-bool"])
def test_a_malformed_line_from_the_engine_is_dropped_and_counted(tmp_path, pack, score):
    """P7's validation (`line_lists`): score a number, not a bool, in [0, 1]."""
    root = build(tmp_path, {"Scans/print.png": None})
    enricher = enricher_for(root, pack, FakeFactory())
    enricher._engine = LinesEngine((OcrLine("'98 7 4", score),))

    summary = run(root, enricher)

    assert summary.facts_dropped == 1 and summary.facts_written == 0
    assert readings(root) == {}


class Emits:
    """A fake `ocr` enricher emitting one hand-written `ocr_text` value per file."""

    name = "ocr"
    model_name = "ppocr-v4"
    model_version = "000000000000"

    def __init__(self, value):
        self.value = value

    def available(self):
        return True, "fake"

    def cost_estimate_ms(self):
        return 1

    def wants(self, file):
        return file.rel_path.startswith("Scans/")

    def enrich(self, file, image):
        return [InferredFact(kind="ocr_text", value=self.value)]


def test_a_line_with_no_text_is_dropped_and_counted(tmp_path):
    """The third malformed shape, which no `OcrLine` can carry: an entry missing `text`."""
    root = build(tmp_path, {"Scans/print.png": None})

    summary = run(root, Emits({"lines": [{"score": 0.9}]}))

    assert summary.facts_dropped == 1
    assert readings(root) == {}


# --- what the engine RECEIVES (R9) -----------------------------------------------------------


def spy_on_read(enricher):
    received = []
    real = enricher._engine.read

    def read(rgb):
        received.append(rgb.copy())
        return real(rgb)

    enricher._engine.read = read
    return received


def test_an_orientation_6_picture_reaches_the_engine_UPRIGHT_and_RGB(tmp_path, pack):
    """R9: a red block in the STORED top-left of an orientation-6 JPEG is in the UPRIGHT
    top-right - 6 means "rotate 90 degrees clockwise to display" - and the raster is RGB."""
    exif = Image.Exif()
    exif[274] = 6
    root = tmp_path / "photos"
    stored = Image.new("RGB", (240, 120), (255, 255, 255))
    stored.paste((255, 0, 0), (0, 0, 60, 60))
    (root / "Scans").mkdir(parents=True)
    stored.save(root / "Scans" / "turned.jpg", "JPEG", exif=exif.tobytes(), quality=95)
    root = build(tmp_path, {"Scans/turned.jpg": None})
    enricher = enricher_for(root, pack, FakeFactory())
    received = spy_on_read(enricher)

    assert run(root, enricher).enriched == 1

    (rgb,) = received
    assert rgb.mode == "RGB"
    assert rgb.size == (120, 240)  # upright: the stored 240 x 120, turned
    red, white = (255, 0, 0), (255, 255, 255)
    near = lambda got, want: all(abs(a - b) < 40 for a, b in zip(got, want, strict=True))  # noqa: E731
    assert near(rgb.getpixel((110, 10)), red)  # upright top-RIGHT
    assert near(rgb.getpixel((10, 10)), white)  # upright top-left
    with Image.open(root / "Scans" / "turned.jpg") as original:
        expected = ImageOps.exif_transpose(original).convert("RGB")
    assert rgb.tobytes() == expected.tobytes()


def test_a_greyscale_picture_reaches_the_engine_as_RGB_with_equal_channels(tmp_path, pack):
    root = tmp_path / "photos"
    picture(root / "Scans" / "grey.png", mode="L", colour=(90, 90, 90))
    root = build(tmp_path, {"Scans/grey.png": None})
    enricher = enricher_for(root, pack, FakeFactory())
    received = spy_on_read(enricher)

    assert run(root, enricher).enriched == 1

    (rgb,) = received
    assert rgb.mode == "RGB"
    assert rgb.getpixel((5, 5)) == (90, 90, 90)


def test_the_upright_frame_and_the_RGB_copy_are_both_CLOSED_by_the_time_enrich_returns(
    tmp_path, pack, monkeypatch
):
    """The two rasters `enrich` makes are its to release. Pillow 12's `Image.__exit__` is
    `pass`, so a `with` on a plain `Image` releases NOTHING (M2268 measured it: the shipped
    `with frame.convert("RGB")` never closed the copy) - the CALL to `close` is watched, on
    `CLAUDE.md`'s rule, because no outcome shows a raster left for the collector (M2268/9)."""
    exif = Image.Exif()
    exif[274] = 6  # a turned picture, so `upright` makes a NEW frame and the finally runs
    root = tmp_path / "photos"
    (root / "Scans").mkdir(parents=True)
    Image.new("L", (60, 30), 200).save(root / "Scans" / "t.jpg", "JPEG", exif=exif.tobytes())
    root = build(tmp_path, {"Scans/t.jpg": None})
    enricher = enricher_for(root, pack, FakeFactory())
    closed, frames, received = [], [], []
    real_close, real_upright = Image.Image.close, ocr_module.upright

    def close(self):
        closed.append(self)
        real_close(self)

    def spy_upright(decoded):
        result = real_upright(decoded)
        frames.append((decoded, result.raster))
        return result

    real_read = enricher._engine.read

    def read(rgb):
        received.append(rgb)
        return real_read(rgb)

    monkeypatch.setattr(Image.Image, "close", close)
    monkeypatch.setattr(ocr_module, "upright", spy_upright)
    monkeypatch.setattr(enricher._engine, "read", read)

    assert run(root, enricher).enriched == 1

    ((decoded, frame),) = frames
    (rgb,) = received
    assert frame is not decoded and rgb is not frame  # control: three distinct rasters
    assert any(c is frame for c in closed), "the upright frame was left to the collector"
    assert any(c is rgb for c in closed), "the RGB copy was left to the collector"


def test_a_corrupt_picture_is_a_per_file_failure(tmp_path, pack):
    root = build(tmp_path, {"Scans/broken.png": b"\x89PNG\r\n\x1a\n not really a picture"})
    factory = FakeFactory()

    summary = run(root, enricher_for(root, pack, factory))

    assert summary.failed == 1 and summary.enriched == 0
    assert readings(root) == {}
    assert factory.calls == []  # nothing decoded, so nothing initialised


# --- the engine's lifecycle: lazy, once, and retried (R10) -----------------------------------


def test_a_run_builds_each_session_once_however_many_files_it_reads(tmp_path, pack):
    root = build(tmp_path, {f"Scans/{n}.png": None for n in range(4)})
    factory = FakeFactory()

    assert run(root, enricher_for(root, pack, factory)).enriched == 4

    assert len(factory.constructions("detector")) == 1
    assert len(factory.constructions("recognizer")) == 1


def test_an_all_cached_run_makes_ZERO_factory_calls(tmp_path, pack):
    """R10: a run that reads nothing builds no session - not at construction, not at all."""
    root = build(tmp_path, {"Scans/a.png": None, "Scans/b.png": None})
    run(root, enricher_for(root, pack, FakeFactory()))
    fresh = FakeFactory()

    enricher = enricher_for(root, pack, fresh)
    assert fresh.calls == []  # construction builds nothing
    summary = run(root, enricher)

    assert summary.skipped_cached == 2 and summary.enriched == 0
    assert fresh.calls == []
    assert enricher.loaded_model is None


def test_a_failed_initialisation_fails_one_file_and_the_next_read_retries_it(tmp_path, pack):
    """R10: the factory raises on its first construction; that file fails (no row), and the
    NEXT file's read initialises again and succeeds."""
    root = build(tmp_path, {"Scans/a.png": None, "Scans/b.png": None})
    factory = FakeFactory(fail_times=1)

    summary = run(root, enricher_for(root, pack, factory))

    assert summary.failed == 1 and summary.enriched == 1
    assert len(readings(root)) == 1
    assert len(factory.calls) == 3  # one failed construction, then detector + recognizer


def test_the_model_line_and_the_receipt_appear_only_after_a_successful_read(tmp_path, pack):
    root = build(tmp_path, {"Scans/a.png": None})
    logger, kept = kept_logger()
    enricher = enricher_for(root, pack, FakeFactory(), logger=logger)
    assert enricher.loaded_model is None

    run(root, enricher)

    version = ocr_model.identity("")[1]
    assert enricher.loaded_model == f"ppocr-v4 {version}"
    receipt = (
        f"enrich ocr: ocr model ppocr-v4 {version};"
        " detector providers ['CPUExecutionProvider'];"
        " recognizer providers ['CPUExecutionProvider']"
    )
    assert kept.messages.count(receipt) == 1


def test_a_run_whose_every_read_failed_loaded_nothing_and_logs_no_receipt(tmp_path, pack):
    root = build(tmp_path, {"Scans/a.png": None})
    logger, kept = kept_logger()
    enricher = enricher_for(root, pack, FakeFactory(fail_times=1), logger=logger)

    assert run(root, enricher).failed == 1

    assert enricher.loaded_model is None
    assert not any("ocr model" in message for message in kept.messages)


# --- I17 across workers: the same readings, the lock released, decoding overlapped ------------


def blocks_by_width(height, width):
    """A detector map with 1-3 separate text blocks, the number set by the map's width - so
    pictures of different widths read differently and a comparison can tell them apart."""
    prob = numpy.zeros((1, 1, height, width), dtype=numpy.float32)
    for block in range(1 + (width // 32) % 3):
        top = 20 + block * 60
        prob[0, 0, top : top + 30, width // 3 : 2 * width // 3] = 0.9
    return prob


def sized_archive(tmp_path, name):
    root = tmp_path / name
    for number, width in enumerate((40, 48, 56, 64, 72, 80)):
        picture(root / "Scans" / f"{number}.png", size=(width, 40))
    return build(tmp_path, {f"Scans/{n}.png": None for n in range(6)}, name=name)


def test_four_workers_store_the_same_readings_as_one_compared_by_content_hash(tmp_path, pack):
    stored = {}
    for name, workers in (("serial", 1), ("parallel", 4)):
        root = sized_archive(tmp_path, name)
        factory = FakeFactory()
        factory.detector_map = blocks_by_width
        enricher = enricher_for(root, pack, factory)
        enricher._engine._lock = BoundedLock()
        assert run(root, enricher, workers=workers).enriched == 6
        conn = catalog(root)
        try:
            stored[name] = {
                row["sha256"]: row["value_json"]
                for row in conn.execute(
                    "SELECT sha256, value_json FROM inferred_facts WHERE kind = 'ocr_text'"
                )
            }
        finally:
            conn.close()
        assert factory.max_active == 1, "two engine runs overlapped: I17's lock is not held"

    assert stored["serial"] == stored["parallel"]
    assert len({value.count('"text"') for value in stored["serial"].values()}) > 1, (
        "every picture read the same, so the comparison above could not tell them apart"
    )


def barrier_loader(root, parties, timeout=10):
    """The shared loader behind a barrier: every worker must be DECODING at once to pass."""
    barrier = threading.Barrier(parties, timeout=timeout)
    real = pil_image_loader(root)

    def load(file):
        barrier.wait()
        return real(file)

    return load


def test_the_bounded_lock_excludes_a_second_holder():
    """The control for `BoundedLock` (Task 13 review, M2295): every I17 test swaps it in for
    the engine's lock, and the fake session is too fast for workers to overlap inside it, so
    nothing else would notice a lock that does not exclude."""
    lock = BoundedLock()
    lock.acquire()
    entered = threading.Event()

    def second():
        with lock:
            entered.set()

    threading.Thread(target=second, daemon=True).start()
    try:
        assert not entered.wait(0.5), "a second holder got in while the first held the lock"
    finally:
        lock.release()
    assert entered.wait(10), "the second holder never got in after the release"


def test_the_barrier_loader_refuses_a_decode_that_arrives_alone(tmp_path):
    """The control for `barrier_loader` (Task 13 review, M2296): the decode-overlap test
    proves the lock is not held around decoding ONLY if a serial decode would break the
    barrier. The barrier is passed before the real loader is called, so no file is needed."""
    load = barrier_loader(tmp_path, 2, timeout=0.5)
    with pytest.raises(threading.BrokenBarrierError):
        load(None)


def test_the_lock_is_released_when_a_read_raises_and_the_waiter_completes(tmp_path, pack):
    """I17: the first read's initialisation raises while the second worker waits on the
    lock; the waiter initialises and reads, the failed file gets no reading, and the next
    run reads it."""
    root = build(tmp_path, {"Scans/a.png": None, "Scans/b.png": None})
    factory = FakeFactory(fail_times=1)
    enricher = enricher_for(root, pack, factory)
    enricher._engine._lock = BoundedLock()

    first = run(root, enricher, loader=barrier_loader(root, 2), workers=2)

    assert first.failed == 1 and first.enriched == 1
    assert len(readings(root)) == 1
    second = run(root, enricher_for(root, pack, factory))
    assert second.enriched == 1 and second.failed == 0
    assert sorted(readings(root)) == ["Scans/a.png", "Scans/b.png"]


def test_two_workers_decode_at_the_same_time(tmp_path, pack):
    """R9 / I17: the barrier opens only when BOTH workers are inside the loader at once - so
    the engine's lock is not held around decoding. A decode under the lock breaks the barrier
    (10 s) and both files fail."""
    root = build(tmp_path, {"Scans/a.png": None, "Scans/b.png": None})
    enricher = enricher_for(root, pack, FakeFactory())
    enricher._engine._lock = BoundedLock()

    summary = run(root, enricher, loader=barrier_loader(root, 2), workers=2)

    assert summary.failed == 0 and summary.enriched == 2


# --- interruption, then the scope widened across the saved cursor ------------------------------


def test_a_folder_added_after_an_interruption_is_read_on_the_run_AFTER_the_resumed_one(
    tmp_path, pack
):
    """RECORDED BEHAVIOUR, NOT CHANGED (the runner is not this task's): a resumed run walks
    only `id > cursor`, so a file a newly added folder brings into scope, with an id below the
    cursor, is NOT read by the resumed run. The run after it starts from the beginning and
    reads it. Nothing is lost - it is one run late.
    """
    files = {"A_other/x.png": None}
    files.update({f"Scans/{n:03d}.png": None for n in range(60)})
    root = build(tmp_path, files)
    early = file_id(root, "A_other/x.png")
    real = pil_image_loader(root)

    def interrupting(file):
        if file.rel_path == "Scans/055.png":
            raise KeyboardInterrupt
        return real(file)

    with pytest.raises(KeyboardInterrupt):
        run(root, enricher_for(root, pack, FakeFactory()), loader=interrupting)
    conn = catalog(root)
    try:
        cursor = conn.execute(
            "SELECT cursor_file_id FROM enrichment_runs WHERE finished_at IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    assert cursor is not None and early < cursor

    widened = ("Scans", "A_other")
    resumed = run(root, enricher_for(root, pack, FakeFactory(), folders=widened))
    assert "A_other/x.png" not in readings(root)

    after = run(root, enricher_for(root, pack, FakeFactory(), folders=widened))
    assert after.enriched == 1
    assert "A_other/x.png" in readings(root)
    assert resumed.walked < after.walked  # the resumed run started past the cursor


# --- the refresh refusals: both reachable, each by its own key ---------------------------------


class SinkedFaces:
    name = "faces"
    model_name = "yunet+sface"
    model_version = "e293d8f46f48"


def test_ocr_hits_the_digest_refusal_and_faces_still_hits_its_sink_refusal():
    ocr = OcrEnricher("", ("Scans",), frozenset())
    assert refresh_refusal(ocr) is None  # ocr has no sink
    assert digest_refusal(ocr) == DIGEST_REFUSAL
    faces_sentence = refresh_refusal(SinkedFaces())
    assert faces_sentence is not None and "face_instances" in faces_sentence
    # faces is a digest too, but its SINK refusal is the one the runner raises first.
    assert digest_refusal(SinkedFaces()) is not None


# "0" sorts BEFORE every digest, so the version comparison could never fire first on it alone;
# "zzzz" sorts AFTER every hex digest, so a refusal placed after the comparison would answer
# with the "is newer than" sentence comparing two hex strings instead (Stage 2, M2284).
@pytest.mark.parametrize("threshold", ["0", "zzzz"])
def test_run_enricher_refuses_a_refresh_for_ocr_before_any_version_comparison(
    tmp_path, pack, threshold
):
    root = build(tmp_path, {"Scans/a.png": None})
    factory = FakeFactory()
    conn = catalog(root)
    try:
        with pytest.raises(ValueError) as refusal:
            run_enricher(
                conn,
                enricher_for(root, pack, factory),
                pil_image_loader(root),
                workers=1,
                refresh_older_than_model=threshold,
            )
    finally:
        conn.close()
    assert str(refusal.value) == DIGEST_REFUSAL
    assert factory.calls == [] and readings(root) == {}


# --- the command (P13, Q4) --------------------------------------------------------------------


def test_BUILDERS_ends_with_ocr_and_it_takes_the_shared_loader():
    """Recorded clarification 2: `ocr` LAST. And I4: the shared decoder, built with a default
    config over an EMPTY connection - the condition `tests/test_phash.py`'s guard builds in."""
    assert list(BUILDERS)[-1] == "ocr"
    conn = sqlite3.connect(":memory:")
    try:
        enricher, loader = BUILDERS["ocr"](conn, Path("."), Config(), None)
    finally:
        conn.close()
    assert isinstance(enricher, OcrEnricher)
    assert loader.__module__ == "pam.enrich.base"


def test_enabled_with_no_folders_prints_p13s_two_lines_and_exits_zero(
    tmp_path, capsys, cli_stack
):
    root = build(tmp_path, {"Scans/a.png": None})
    configure(root, "[enrich.ocr]\nenabled = true\n")

    code, out, _err = enrich_cli(root, capsys)

    assert code == 0
    assert paragraph(out) == NO_FOLDERS
    assert "!" not in "\n".join(paragraph(out))
    assert cli_stack.calls == [] and readings(root) == {}


def test_a_default_install_reports_ocr_as_disabled_and_exits_zero(tmp_path, capsys, cli_stack):
    root = build(tmp_path, {"Scans/a.png": None})

    code, out, _err = enrich_cli(root, capsys)

    assert code == 0
    assert paragraph(out) == [
        "ocr: not run - [enrich.ocr] enabled is false in pam.toml.",
        "  Set [enrich.ocr] enabled = true to turn it on.",
    ]


def unusable_packs(tmp_path):
    """Q4's three variants: (model_dir, the expected first line). Hand-written."""
    wrong = tmp_path / "wrong"
    wrong.mkdir()
    (wrong / "ch_PP-OCRv4_det_infer.onnx").write_bytes(b"not the detector")
    (wrong / "ch_PP-OCRv4_rec_infer.onnx").write_bytes(b"not the recognizer")
    got = hashlib.sha256(b"not the detector").hexdigest()
    return [
        ("", "ocr: not run - [enrich.ocr] model_dir is not set."),
        (
            str(tmp_path / "empty"),
            "ocr: not run - [enrich.ocr] model_dir names a folder without"
            " ch_PP-OCRv4_det_infer.onnx.",
        ),
        (
            str(wrong),
            "ocr: not run - [enrich.ocr] model_dir holds a ch_PP-OCRv4_det_infer.onnx whose"
            f" SHA-256 is {got}, not {DETECTOR_SHA}.",
        ),
    ]


@pytest.mark.parametrize("variant", [0, 1, 2], ids=["unset", "missing-file", "wrong-hash"])
def test_an_unusable_pack_prints_q4s_sentence_exits_one_and_stores_nothing(
    tmp_path, capsys, monkeypatch, variant
):
    """Q4, each variant a literal - with the REAL pinned hashes (no fake pack here)."""
    monkeypatch.setattr(ocr_model, "_missing_extra", lambda: [])
    factory = FakeFactory()
    monkeypatch.setattr(ocr_model, "_onnx_session", factory)
    (tmp_path / "empty").mkdir()
    model_dir, first = unusable_packs(tmp_path)[variant]
    root = build(tmp_path, {"Scans/a.png": None})
    configure(
        root,
        f"[enrich.ocr]\nenabled = true\nfolders = [\"Scans\"]\nmodel_dir = '{model_dir}'\n",
    )

    code, out, _err = enrich_cli(root, capsys)

    assert code == 1
    assert paragraph(out) == [first, PUT]
    assert factory.calls == [] and readings(root) == {}


def test_a_missing_extra_keeps_the_doctor_pointer_and_exits_one(
    tmp_path, capsys, monkeypatch, pack
):
    """Q4's `not run -` form covers the PACK states only. A missing extra is the one state
    pam doctor checks, so it keeps the `!` form and its `Run: pam doctor` line (Stage 1 F2)."""
    monkeypatch.setattr(ocr_model, "_missing_extra", lambda: ["onnxruntime", "numpy"])
    root = build(tmp_path, {"Scans/a.png": None})
    configure(root, ocr_config(pack))

    code, out, _err = enrich_cli(root, capsys)

    assert code == 1
    lines = out.splitlines()
    start = lines.index(
        "! ocr did not run: the optional [ocr] extra is not installed (onnxruntime, numpy"
        f" missing). Run: {_pip('ocr')}."
    )
    assert lines[start + 1] == "  Run: pam doctor   (it checks every dependency pam can use)"
    assert not any(line.startswith("ocr: not run") for line in lines)


def test_the_refresh_refusal_is_p13s_sentence_and_exits_one(tmp_path, capsys, cli_stack, pack):
    root = build(tmp_path, {"Scans/a.png": None})
    configure(root, ocr_config(pack))

    code, _out, err = enrich_cli(root, capsys, "--refresh-older-than-model", "1.0")

    assert code == 1
    assert f"x {DIGEST_REFUSAL}" in err.splitlines()
    assert cli_stack.calls == [] and readings(root) == {}


def test_p13s_closing_lines_and_the_model_line(tmp_path, capsys, cli_stack, pack):
    root = build(tmp_path, {"Scans/a.png": None, "Desktop/b.png": None})
    configure(root, ocr_config(pack))

    code, out, _err = enrich_cli(root, capsys)

    assert code == 0
    # Four catalogued files: the two pictures and the fake exiftool's two unhashed
    # `.tags.json` sidecars ("never read from disk"). Desktop/b.png is "not applicable".
    assert paragraph(out) == [
        "ocr: 1 of 4 files read.",
        DATES_LATER,
        "  skipped: 0 already done, 1 not applicable, 0 identical to another file,"
        " 2 never read from disk.",
        f"  model: ppocr-v4 {ocr_model.identity('')[1]}",
    ]
    assert list(readings(root)) == ["Scans/a.png"]


def test_a_second_cli_run_is_all_cached_builds_nothing_and_prints_no_model(
    tmp_path, capsys, cli_stack, pack, monkeypatch
):
    root = build(tmp_path, {"Scans/a.png": None})
    configure(root, ocr_config(pack))
    enrich_cli(root, capsys)
    # Precondition: the FIRST run read the file. Without it, a file never in scope also gives
    # "0 of" and no factory call the second time (M2279: the source read as "exif" was green).
    assert list(readings(root)) == ["Scans/a.png"]
    fresh = FakeFactory()
    monkeypatch.setattr(ocr_model, "_onnx_session", fresh)

    code, out, _err = enrich_cli(root, capsys)

    assert code == 0
    assert fresh.calls == []
    assert paragraph(out)[0].startswith("ocr: 0 of ")
    assert not any(line.startswith("  model:") for line in paragraph(out))


def test_a_db_write_failure_leaves_no_half_written_reading(
    tmp_path, capsys, cli_stack, pack, monkeypatch
):
    """The write fails on the SECOND file of the batch: nothing of the batch is committed (read
    through a second connection), the run ends interrupted with its log named, and the next
    run reads both files."""
    root = build(tmp_path, {"Scans/a.png": None, "Scans/b.png": None})
    configure(root, ocr_config(pack))
    real, calls = repo.insert_fact, []

    def exploding(*args, **kwargs):
        calls.append(kwargs.get("kind"))
        if len(calls) == 2:
            raise sqlite3.OperationalError("disk I/O error")
        return real(*args, **kwargs)

    monkeypatch.setattr(repo, "insert_fact", exploding)
    code, _out, err = enrich_cli(root, capsys)
    monkeypatch.setattr(repo, "insert_fact", real)  # the re-run below writes for real

    assert code == EXIT_INTERNAL
    assert "Log: " in err
    assert calls == ["ocr_text", "ocr_text"]
    assert readings(root) == {}
    conn = catalog(root)
    try:
        unfinished = conn.execute(
            "SELECT COUNT(*) FROM enrichment_runs WHERE enricher = 'ocr' AND finished_at IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    assert unfinished == 1

    code, _out, _err = enrich_cli(root, capsys)
    assert code == 0
    assert sorted(readings(root)) == ["Scans/a.png", "Scans/b.png"]


# --- end to end: enrich -> plan (Task 10 meets Task 13) ----------------------------------------


def newest_item(root, rel):
    conn = catalog(root)
    try:
        row = conn.execute(
            "SELECT generated_filename, trust_reasons_json FROM plan_items"
            " WHERE old_rel_path = ? ORDER BY plan_id DESC LIMIT 1",
            (rel,),
        ).fetchone()
    finally:
        conn.close()
    return row["generated_filename"], json.loads(row["trust_reasons_json"])


def test_enrich_then_plan_names_the_scan_with_the_date_and_says_where_it_came_from(
    tmp_path, capsys, cli_stack, pack
):
    root = build(tmp_path, {"Scans/print.png": None})
    configure(root, ocr_config(pack))
    script(cli_stack, "1998-07-04", 0.9375)

    assert enrich_cli(root, capsys)[0] == 0
    run_plan(root, "{date}_{seq}")

    name, reasons = newest_item(root, "Scans/print.png")
    assert name.startswith("1998-07-04_")
    assert 'pam read "1998-07-04" in the photo as 1998-07-04 (94% confidence).' in reasons


def test_a_line_scored_0_45_is_stored_and_the_floor_decides_at_plan_time_only(
    tmp_path, capsys, cli_stack, pack
):
    """R11 / I16 end to end: the 0.45 line is STORED; at the default floor 0.5 the plan gives
    no OCR date, at floor 0.4 it does - and the engine is not called again."""
    root = build(tmp_path, {"Scans/print.png": None})
    configure(root, ocr_config(pack))
    script(cli_stack, "1998-07-04", 0.45)

    assert enrich_cli(root, capsys)[0] == 0
    assert readings(root) == {
        "Scans/print.png": '{"lines": [{"score": 0.44999998807907104, "text": "1998-07-04"}]}'
    }
    runs = len(cli_stack.runs)

    run_plan(root, "{date}_{seq}")
    assert newest_item(root, "Scans/print.png")[0].startswith("2024-05-01_")

    configure(root, ocr_config(pack, extra="\n[tiers]\nocr = { floor = 0.4 }\n"))
    run_plan(root, "{date}_{seq}")
    assert newest_item(root, "Scans/print.png")[0].startswith("1998-07-04_")
    assert len(cli_stack.runs) == runs


@pytest.mark.ocr
def test_the_real_engine_reads_a_committed_imprint_and_the_plan_dates_the_scan(
    tmp_path, capsys, monkeypatch
):
    """The same end to end on the REAL pack: `date_imprint.png` (Task 12 Part A's committed
    fixture, never rendered here), whose truth date is 1998-07-04 (`truth.json`)."""
    from ocr_stack import model_dir

    root = tmp_path / "photos"
    (root / "Scans").mkdir(parents=True)
    shutil.copy(FIXTURES / "date_imprint.png", root / "Scans" / "date_imprint.png")
    root = build(tmp_path, {"Scans/date_imprint.png": None})
    configure(root, ocr_config(model_dir()))

    code, out, _err = enrich_cli(root, capsys)
    assert code == 0, out
    assert paragraph(out)[0].startswith("ocr: 1 of ")
    run_plan(root, "{date}_{seq}")

    name, reasons = newest_item(root, "Scans/date_imprint.png")
    assert name.startswith("1998-07-04_")
    assert any(
        reason.startswith('pam read "') and " in the photo as 1998-07-04 (" in reason
        for reason in reasons
    )
