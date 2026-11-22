"""M12's chain: scan -> ENRICH (phash, geocode, ocr) -> plan -> review -> apply -> undo -> verify.

WHY THIS EXISTS BESIDE `tests/test_t1_enriched_chain.py` (M12 plan, Task 14 Step 1). That module
is M9's and M10's chain: a geocoded place and a model's caption reach DISK and come off again.
Nothing before this file ran `pam enrich ocr` inside a chain, and OCR is the first enricher whose
fact changes the one token every default name starts with - `{date}`. Everything between the
`ocr_text` reading and the filesystem (the plan-time grammar, the tier cap, the collision search,
`unit_key`, the journal, `perform_verified_rename`) had only ever been driven one command at a
time: `tests/test_ocr_naming.py` inserts readings by hand and stops at the plan, and
`tests/test_ocr_enricher.py` stops at the plan too.

THE CHAIN RUNS TWICE, as the plan says: over the spec 9.1 tiny library with OCR switched ON and no
`Scans/` folder in it (so OCR is enabled and must touch nothing), and over a scratch copy of the
same library WITH a `Scans/` folder of Pillow-rendered imprint images (synthetic, for the suite
only - HT-M12 Leg B is where real prints are read). At EVERY step `pam verify`'s twelve checks
must report nothing; after apply no companion unit may hold two stems on disk; after undo an
independent path-and-hash map must equal the one taken before the scan (`--exact`).

WHAT IS DRIVEN. `pam scan`, `pam enrich`, `pam plan`, `pam apply` and `pam undo` go through
`cli.main` with the arguments a user types, typed confirmation included, and the scan uses REAL
exiftool - the tiny library is pinned against it, and a rendered scan's lack of EXIF is only a
fact about the file when the real reader says so. `pam review` goes through `run_review` with its
`serve` seam (the real command blocks until Ctrl+C), exactly as the two sibling chains do.

THE OCR ENGINE IS THE REAL `ocr_model.Engine` (R10). In the default suite its two SESSIONS are a
fake (`tests/ocr_stack.FakeFactory`, extended here so each image the detector is shown gets its own
scripted line) and the pack is `fake_pack`'s, because the repository venv has no `[ocr]` extra.
The same chain is ALSO run on the real pack, marked `ocr`, so the `ocr` lane reads the rendered
imprints with the real models; there the scores are the model's and only the tier's CEILING
(never `high`) is asserted, not its exact value.

EVERY EXPECTED NAME, DATE AND TIER IS SPELLED BY HAND from the images rendered here and from
design 6.1 rev 27 / 6.4 rev 27 (P9, P11) - never read back from the planner, the grammar or a
production constant. The unit-stem check reads `plan_items.unit_key` and `files.rel_path` with the
suite's own SQL and then asks the DISK, and the `--exact` oracle is `archives.digests`, the same
instrument `tools/ht_manifest.py --exact` is.
"""

import json
import os
import shutil
import threading
import webbrowser
from datetime import datetime
from http.client import HTTPConnection
from pathlib import Path

import numpy
import pytest
from archives import assert_verify_agrees, catalog, digests, materialise_tiny_library, open_plan
from ocr_stack import FakeFactory, fake_pack
from PIL import Image, ImageDraw, ImageFont

from pam.cli import main
from pam.enrich import ocr_model
from pam.paths import workspace_if_exists
from pam.webui import run_review

needs_exiftool = pytest.mark.skipif(
    shutil.which("exiftool") is None,
    reason="the tiny library is pinned against REAL exiftool, and so is this chain",
)

# Every `Scans/` file's mtime, stamped before the scan: 2024-05-01 12:00 LOCAL time, so a scan
# with no readable date is named `2024-05-01_...` (design 6.1's `file_mtime` fallback).
MTIME = datetime(2024, 5, 1, 12, 0, 0)

# The `Scans/` folder, rendered below. Each entry: (the imprint drawn on the picture or None,
# the EXIF DateTimeOriginal written into it or None). The dates are chosen to fall on NO date
# the tiny library uses, so no scan can join one of its events and borrow a place label.
#   print_1987.png      - an imprint, no EXIF: OCR dates it.
#   print_1998.png      - an imprint, no EXIF, AND a `.xmp` sidecar: a two-member unit whose
#                         name is its primary's OCR date (vision S3, design 5.4).
#   blank.png           - no text at all: read, covered, no date -> the mtime date.
#   exif_dated.jpg      - an imprint AND a camera date: design 6.1 lets OCR replace `file_mtime`
#                         and nothing else (D5), so OCR must never even open it.
SCANS = {
    "Scans/print_1987.png": ("1987-11-23", None),
    "Scans/print_1998.png": ("1998-07-04", None),
    "Scans/blank.png": (None, None),
    "Scans/exif_dated.jpg": ("2011-02-03", "2003:03:14 10:00:00"),
}
SIDECAR = "Scans/print_1998.xmp"

# The order the `ocr` enricher reads its applicable files in: file-id order, which is scan order.
# ASSERTED against the catalog before the run, never assumed - the stub's script is keyed on it,
# as `test_t1_enriched_chain.py`'s captions are keyed on CAPTION_WALK_ORDER.
OCR_READ_ORDER = ("Scans/blank.png", "Scans/print_1987.png", "Scans/print_1998.png")

# What the stub's recogniser answers for each, in that order: (line text or None, score). The
# scores are exact in float32, so the stored score is the literal. Design 6.1 rev 27: `medium`
# at or above `[tiers] ocr.low` (0.7), never `high`.
STUB_SCRIPT = {
    "Scans/blank.png": (None, None),
    "Scans/print_1987.png": ("1987-11-23", 0.9375),
    "Scans/print_1998.png": ("1998-07-04", 0.75),
}

# The `ocr_text` readings the catalog must hold after `pam enrich`, spelled by hand. EXACTLY
# these three files: not the EXIF-dated scan (D5), not the sidecar (not an image), and not one
# of the tiny library's own mtime-dated pictures outside `Scans/` (P10 - folders are opt-in).
STUB_READINGS = {
    "Scans/blank.png": {"lines": []},
    "Scans/print_1987.png": {"lines": [{"text": "1987-11-23", "score": 0.9375}]},
    "Scans/print_1998.png": {"lines": [{"text": "1998-07-04", "score": 0.75}]},
}

# The name the planner owes each `Scans/` file under the DEFAULT template
# (`{date}_{event-or-scene}_{people}_{seq}`): no GPS, no caption and no face, so a scan's stem is
# its date and its seq. Extensions keep their on-disk case (design 5.4).
SCANS_PLANNED = {
    "Scans/print_1987.png": "Scans/1987-11-23_001.png",
    "Scans/print_1998.png": "Scans/1998-07-04_001.png",
    SIDECAR: "Scans/1998-07-04_001.xmp",
    "Scans/blank.png": "Scans/2024-05-01_001.png",
    "Scans/exif_dated.jpg": "Scans/2003-03-14_001.jpg",
}
# The items whose date came from OCR - the unit, both members. Each must be `attention`, and its
# tier must never be `high`.
OCR_DATED = {"Scans/print_1987.png", "Scans/print_1998.png", SIDECAR}
# The stub's exact tiers (the scores above, against design 6.1 rev 27's bounds).
STUB_TIERS = {
    "Scans/print_1987.png": "medium",
    "Scans/print_1998.png": "medium",
    SIDECAR: "medium",
}

OCR_ON = "[enrich.ocr]\nenabled = true\nfolders = [\"Scans\"]\nmodel_dir = '{pack}'\n"


# --------------------------------------------------------------------------- the pictures


def _imprint(text: str) -> Image.Image:
    """`text` in black on white, ImageFont.load_default(size=64), a 40 px margin - the shape of
    Task 12 Part A's committed `date_iso.png`, which the real pack reads (truth.json)."""
    font = ImageFont.load_default(size=64)
    left, top, right, bottom = ImageDraw.Draw(Image.new("RGB", (1, 1))).textbbox(
        (0, 0), text, font=font
    )
    image = Image.new("RGB", (right - left + 80, bottom - top + 80), (255, 255, 255))
    ImageDraw.Draw(image).text((40 - left, 40 - top), text, font=font, fill=(0, 0, 0))
    return image


def _render_scans(root: Path) -> None:
    """The `Scans/` folder: four pictures and one sidecar, every file's bytes unique."""
    scans = root / "Scans"
    scans.mkdir()
    for rel, (text, camera_date) in SCANS.items():
        path = root / rel
        image = _imprint(text) if text else Image.new("RGB", (400, 200), (255, 255, 255))
        if camera_date is None:
            image.save(path, format="PNG")
        else:
            exif = Image.Exif()
            exif.get_ifd(0x8769)[0x9003] = camera_date  # ExifIFD DateTimeOriginal
            image.save(path, format="JPEG", exif=exif, quality=95)
    (root / SIDECAR).write_text(
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><!-- print_1998 edits --></x:xmpmeta>\n',
        encoding="utf-8",
    )
    stamp = MTIME.timestamp()
    for path in scans.iterdir():
        os.utime(path, (stamp, stamp))


# --------------------------------------------------------------------------- the stub engine


class ImprintFactory(FakeFactory):
    """`FakeFactory` with ONE scripted line per image, in the order the detector sees them.

    The real `Engine` calls the detector once per image and then the recogniser once per box, all
    under its lock (I17) with one worker, so the detector's call count says which image is being
    read. A blank entry gets a detector map with nothing in it (no box, no recogniser call). The
    charset is the union of every scripted text's characters, because the engine reads it once,
    at initialisation. A detector call past the end of the script raises, which fails that file -
    so an extra file reaching the engine cannot pass quietly.
    """

    def __init__(self, script):
        super().__init__()
        self.script = list(script)
        self.seen = 0
        chars = sorted({c for text, _ in self.script if text for c in text} - {" "})
        self.characters = "\n".join(chars)
        self.classes = len(chars) + 2
        self._index = {char: position + 1 for position, char in enumerate(chars)}
        self._index[" "] = self.classes - 1

    def _encode(self, text, score):
        rest = (1.0 - score) / (self.classes - 1)
        blank = [1.0] + [0.0] * (self.classes - 1)
        rows = []
        for char in text:
            row = [rest] * self.classes
            row[self._index[char]] = score
            rows += [row, blank]
        return numpy.array(rows, dtype=numpy.float32)

    def detector_map(self, height, width):
        text, score = self.script[self.seen]
        self.seen += 1
        if text is None:
            return numpy.zeros((1, 1, height, width), dtype=numpy.float32)
        self.recognizer_output = self._encode(text, score)
        return super().detector_map(height, width)


# --------------------------------------------------------------------------- the drivers


@pytest.fixture(autouse=True)
def no_real_browser(monkeypatch):
    """`run_review` opens a browser. A suite that opens real tabs is a broken suite."""
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)
    return opened


def _request(server, method, path, body=None):
    """One request to the live review server, through the token prefix - the only way in."""
    payload = b"" if body is None else json.dumps(body).encode("utf-8")
    conn = HTTPConnection(server.host, server.port, timeout=20)
    try:
        conn.request(
            method,
            f"/t/{server.token}/api{path}",
            body=payload,
            headers={"Content-Type": "application/json", "Content-Length": str(len(payload))},
        )
        response = conn.getresponse()
        raw = response.read()
        assert response.status == 200, (method, path, response.status, raw)
        return json.loads(raw)
    finally:
        conn.close()


def _readings(root: Path) -> dict[str, dict]:
    """rel_path -> parsed `ocr_text` value, every live row, with the SUITE'S OWN SQL."""
    conn = catalog(root)
    try:
        rows = conn.execute(
            "SELECT f.rel_path AS rel_path, i.value_json AS value_json FROM inferred_facts i"
            " JOIN files f ON f.sha256 = i.sha256"
            " WHERE i.kind = 'ocr_text' AND i.superseded_by IS NULL"
        ).fetchall()
    finally:
        conn.close()
    return {row["rel_path"]: json.loads(row["value_json"]) for row in rows}


def _ocr_applicable_in_read_order(root: Path) -> tuple[str, ...]:
    """The images under `Scans/` with no camera or filename date, in file-id order - spelled as
    the suite's own SQL over `files` and `capture`, not through `OcrEnricher.wants`."""
    conn = catalog(root)
    try:
        rows = conn.execute(
            "SELECT f.rel_path FROM files f JOIN capture c ON c.file_id = f.id"
            " WHERE f.media_type = 'image' AND f.rel_path LIKE 'Scans/%'"
            " AND c.capture_dt_source = 'file_mtime' ORDER BY f.id"
        ).fetchall()
    finally:
        conn.close()
    return tuple(row[0] for row in rows)


def _units_on_disk(root: Path, plan_id: int) -> dict[str, set[str]]:
    """unit_key -> the set of on-disk STEMS of its members, after asking the disk each exists.

    The plan's `unit_key` and the catalog's current `rel_path`, read with the suite's own SQL;
    every path is then checked on the filesystem, so a catalog that agreed with itself about a
    rename that never happened cannot pass.
    """
    conn = catalog(root)
    try:
        rows = conn.execute(
            "SELECT p.unit_key AS unit_key, f.rel_path AS rel_path FROM plan_items p"
            " JOIN files f ON f.id = p.file_id WHERE p.plan_id = ?",
            (plan_id,),
        ).fetchall()
    finally:
        conn.close()
    return _stems_by_unit(root, [(row["unit_key"], row["rel_path"]) for row in rows])


def _stems_by_unit(root: Path, rows: list[tuple[str, str]]) -> dict[str, set[str]]:
    """`(unit_key, rel_path)` rows -> unit_key -> on-disk stems. Split out of `_units_on_disk` so
    the grouping has a control of its own (`test_the_unit_stem_oracle_sees_a_split_it_is_shown`,
    M2299: grouped by `rel_path`, the chain's S3 check passed by construction)."""
    units: dict[str, set[str]] = {}
    for unit_key, rel_path in rows:
        path = root / rel_path
        assert path.is_file(), f"the catalog says {rel_path} and the disk does not"
        units.setdefault(unit_key, set()).add(f"{Path(rel_path).parent.as_posix()}/{path.stem}")
    return units


def _session_id(root: Path) -> str:
    conn = catalog(root)
    try:
        rows = conn.execute("SELECT DISTINCT session_id FROM journal").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, rows
    return str(rows[0]["session_id"])


# --------------------------------------------------------------------------- the chain


def _chain(root: Path, pack, monkeypatch, capsys, opened, *, scans: bool, stub: bool):
    """The whole chain over `root`. `scans` says whether `Scans/` was rendered into it; `stub`
    whether the engine's sessions are the scripted fake or the real pack."""
    before = digests(root)

    # --- scan, through the CLI, with real exiftool ---------------------------------------------
    capsys.readouterr()
    assert main(["scan", str(root)]) == 0, capsys.readouterr().out
    capsys.readouterr()
    assert_verify_agrees(root, where="after pam scan")
    ws = workspace_if_exists(root)
    assert ws is not None
    ws.config_path.write_text(OCR_ON.format(pack=pack), encoding="utf-8")

    # The rendered scans really are what the table says: three mtime-dated pictures, in the
    # order the stub's script is keyed on, and the EXIF-dated one is NOT among them.
    assert _ocr_applicable_in_read_order(root) == (OCR_READ_ORDER if scans else ())

    # --- enrich, through the CLI: phash, geocode and ocr, every enabled enricher ---------------
    factory = None
    if stub:
        factory = ImprintFactory(STUB_SCRIPT[rel] for rel in OCR_READ_ORDER)
        monkeypatch.setattr(ocr_model, "_onnx_session", factory)
        monkeypatch.setattr(ocr_model, "_missing_extra", lambda: [])
    code = main(["enrich", "--archive", str(root)])
    enrich_out = capsys.readouterr().out
    # Exit 1 is spec 13's per-file-error code: the tiny library's HEIC files are containers with
    # no coded image ON PURPOSE, so `phash` counts them failed. OCR's own success is asserted by
    # the readings below, exactly - not by this code.
    assert code in (0, 1), enrich_out
    for enricher in ("phash:", "geocode:", "ocr:"):
        assert enricher in enrich_out, enrich_out
    assert "Next: pam plan" in enrich_out
    readings = _readings(root)
    if not scans:
        assert readings == {}, "OCR read a file outside [enrich.ocr] folders"
    elif stub:
        assert readings == STUB_READINGS
        assert factory is not None and factory.seen == len(OCR_READ_ORDER)
    else:
        assert set(readings) == set(OCR_READ_ORDER)
        assert readings["Scans/blank.png"] == {"lines": []}
    assert_verify_agrees(root, where="after pam enrich")

    # --- plan, through the CLI, under the DEFAULT template -------------------------------------
    assert main(["plan", "--archive", str(root)]) == 0
    plan_out = capsys.readouterr().out
    conn = catalog(root)
    try:
        plan = open_plan(conn)
    finally:
        conn.close()
    assert plan.template == "{date}_{event-or-scene}_{people}_{seq}"
    assert f"Next: pam review {plan.id}" in plan_out
    assert_verify_agrees(root, where="after pam plan")

    # --- review, through the server `pam review` runs ------------------------------------------
    seen: dict[str, dict] = {}

    def drive(server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            listing = _request(server, "GET", f"/plan/{plan.id}/items?limit=200")
            assert listing["total"] == len(listing["items"]), "the plan outgrew one page"
            seen.update({item["old_rel_path"]: item for item in listing["items"]})
            # The bulk preset, which leaves every `low` item undecided (spec 8.2 rev 12) ...
            bulk = _request(
                server,
                "POST",
                f"/plan/{plan.id}/decide-filter",
                {"action": "approve", "status": "proposed"},
            )
            # ... and then each `low` item by its id, as UX 5a's attention pass does.
            low = [item["id"] for item in listing["items"] if item["tier"] == "low"]
            assert bulk["excluded_low"] == len(low)
            if low:
                _request(
                    server,
                    "POST",
                    f"/plan/{plan.id}/decide",
                    {"action": "approve", "item_ids": low},
                )
        finally:
            server.shutdown()
            thread.join(timeout=20)

    summary = run_review(plan.id, root, serve=drive)
    capsys.readouterr()
    assert (summary.skipped, summary.remaining) == (0, 0)
    assert summary.approved == len(seen)
    assert opened[-1] == summary.url
    assert_verify_agrees(root, where="after pam review")

    if scans:
        got = {
            rel: f"{seen[rel]['rel_dir']}/{seen[rel]['new_filename']}" for rel in SCANS_PLANNED
        }
        assert got == SCANS_PLANNED
        for rel in OCR_DATED:
            item = seen[rel]
            date = [t for t in item["tokens"] if t["token"] == "date"]
            assert len(date) == 1 and date[0]["source_table"] == "inferred_facts", (rel, date)
            assert item["tier"] in ("low", "medium"), (rel, item["tier"])
            assert item["attention"] is True, rel
            if stub:
                assert item["tier"] == STUB_TIERS[rel], (rel, item["tier"])
        # The negative half: neither the camera-dated scan nor the blank one is OCR-dated.
        for rel in ("Scans/exif_dated.jpg", "Scans/blank.png"):
            date = [t for t in seen[rel]["tokens"] if t["token"] == "date"]
            assert date[0]["source_table"] == "capture", (rel, date)
    else:
        assert not any(rel.startswith("Scans/") for rel in seen)

    # --- apply, through the CLI, with UX 4's typed confirmation --------------------------------
    monkeypatch.setattr("builtins.input", lambda: "apply")
    assert main(["apply", str(plan.id), "--archive", str(root)]) == 0
    apply_out = capsys.readouterr().out
    assert f"{len(seen)} renamed, 0 failed" in apply_out, apply_out
    assert_verify_agrees(root, where="after pam apply")

    # Vision S3, ON DISK: every unit's members share one stem in one directory.
    units = _units_on_disk(root, plan.id)
    split = {key: stems for key, stems in units.items() if len(stems) != 1}
    assert split == {}, f"a companion unit was split across stems: {split}"
    # The oracle's OWN keys are the plan's units, and one of them has more than one member - so
    # the S3 check above grouped by unit and had a unit to check (M2299).
    members = _members(root, plan.id)
    assert set(units) == set(members)
    assert any(len(paths) > 1 for paths in members.values())
    if scans:
        for original, renamed in SCANS_PLANNED.items():
            assert (root / renamed).is_file(), f"{original} did not become {renamed}"
            assert not (root / original).exists(), f"{original} is still on disk"
    # `--bytes`: not one byte moved, and no file was lost or added.
    assert set(digests(root)) == set(before)

    # --- undo, through the CLI -----------------------------------------------------------------
    assert main(["undo", _session_id(root), "--archive", str(root)]) == 0
    assert f"{len(seen)} put back, 0 failed" in capsys.readouterr().out
    # `--exact`: every file back under its original path, byte for byte.
    assert digests(root) == before
    assert_verify_agrees(root, where="after pam undo")

    # --- verify, through the CLI ---------------------------------------------------------------
    assert main(["verify", "--archive", str(root)]) == 0
    capsys.readouterr()


def _members(root: Path, plan_id: int) -> dict[str, list[str]]:
    """unit_key -> member rel_paths, so the S3 check is shown to have had a unit to check."""
    conn = catalog(root)
    try:
        rows = conn.execute(
            "SELECT unit_key, old_rel_path FROM plan_items WHERE plan_id = ?", (plan_id,)
        ).fetchall()
    finally:
        conn.close()
    members: dict[str, list[str]] = {}
    for row in rows:
        members.setdefault(row["unit_key"], []).append(row["old_rel_path"])
    return members


# --------------------------------------------------------------------------- the tests


@needs_exiftool
def test_the_m12_chain_over_the_tiny_library_with_ocr_on_touches_nothing_outside_its_folders(
    tmp_path, monkeypatch, capsys, no_real_browser
):
    """Run one: the tiny library alone, `[enrich.ocr]` enabled for a `Scans/` it does not have.

    OCR is ON and its folder list names nothing that exists, so the chain must be exactly the
    chain without OCR: no reading stored, every name from the camera, the filename or the mtime,
    12/12 at every step, no unit split, `--exact` after undo.
    """
    root = materialise_tiny_library(tmp_path / "a")
    pack = fake_pack(tmp_path / "pack", monkeypatch)
    _chain(root, pack, monkeypatch, capsys, no_real_browser, scans=False, stub=True)


@needs_exiftool
def test_the_m12_chain_puts_an_OCR_date_on_disk_and_takes_it_off_again(
    tmp_path, monkeypatch, capsys, no_real_browser
):
    """Run two: a scratch copy of the tiny library WITH `Scans/`, the stub sessions."""
    root = materialise_tiny_library(tmp_path / "a")
    _render_scans(root)
    pack = fake_pack(tmp_path / "pack", monkeypatch)
    _chain(root, pack, monkeypatch, capsys, no_real_browser, scans=True, stub=True)


@pytest.mark.ocr
@needs_exiftool
def test_the_m12_chain_on_the_REAL_pack_reads_the_rendered_imprints(
    tmp_path, monkeypatch, capsys, no_real_browser
):
    """Run two again, on the real models: the rendered imprints must be READ, not scripted."""
    from ocr_stack import model_dir

    root = materialise_tiny_library(tmp_path / "a")
    _render_scans(root)
    _chain(root, model_dir(), monkeypatch, capsys, no_real_browser, scans=True, stub=False)


def test_the_rendered_scans_are_what_the_tables_say(tmp_path):
    """The fixture's own claims, asserted directly so a silent change cannot hollow the chain.

    Five files with five different digests (content-addressed facts would merge two equal
    ones); only the JPEG carries a camera date, and it is the date `SCANS` names; and the three
    tables keyed by path agree with each other about which files OCR reads.
    """
    root = tmp_path / "photos"
    root.mkdir()
    _render_scans(root)
    found = digests(root)
    assert sorted(found.values()) == sorted([*SCANS, SIDECAR])
    for rel, (_, camera_date) in SCANS.items():
        with Image.open(root / rel) as image:
            written = image.getexif().get_ifd(0x8769).get(0x9003)
        assert written == camera_date, rel
        assert datetime.fromtimestamp((root / rel).stat().st_mtime) == MTIME
    uncamera = {rel for rel, (_, camera) in SCANS.items() if camera is None}
    assert set(OCR_READ_ORDER) == uncamera == set(STUB_SCRIPT) == set(STUB_READINGS)
    assert OCR_DATED == {"Scans/print_1987.png", "Scans/print_1998.png", SIDECAR}


def test_the_stub_reads_each_image_its_own_line_and_nothing_past_the_script(
    tmp_path, monkeypatch
):
    """The stub factory, under the real `Engine`: one line per image in order, an empty map for
    a blank entry, and a raise past the end - so the chain's exact readings mean what they say."""
    pack = fake_pack(tmp_path / "pack", monkeypatch)
    factory = ImprintFactory([("1987-11-23", 0.9375), (None, None)])
    engine = ocr_model.Engine(pack, session_factory=factory)
    first = engine.read(_imprint("1987-11-23"))
    assert [(line.text, line.score) for line in first] == [("1987-11-23", 0.9375)]
    assert engine.read(_imprint("1987-11-23")) == ()
    with pytest.raises(IndexError):
        engine.read(_imprint("1987-11-23"))


def test_the_unit_stem_oracle_sees_a_split_it_is_shown(tmp_path):
    """The S3 oracle's control (M2299). Two members of ONE unit under two stems must come back as
    one unit with two stems; two lone files under two stems must come back as two clean units.
    Hand-built rows and files - no production call decides what a unit is here."""
    for rel in ("Scans/a_001.png", "Scans/a_001.xmp", "Scans/b_001.png", "Scans/c_002.xmp"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(rel.encode())
    together = [("u1", "Scans/a_001.png"), ("u1", "Scans/a_001.xmp")]
    split = [("u2", "Scans/b_001.png"), ("u2", "Scans/c_002.xmp")]
    assert _stems_by_unit(tmp_path, together + split) == {
        "u1": {"Scans/a_001"},
        "u2": {"Scans/b_001", "Scans/c_002"},
    }
    with pytest.raises(AssertionError, match="the disk does not"):
        _stems_by_unit(tmp_path, [("u3", "Scans/missing.png")])
