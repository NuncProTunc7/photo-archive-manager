"""Spec 8.2's `/faces/*` read routes and `pam faces label` (M11 Task 10, Steps 3-5).

THE IMAGES ARE REAL AND ONE OF THEM IS A PORTRAIT, and that is the module's reason for
existing rather than a convenience. The hazard Task 10 was dispatched against is that
`pil_image_loader` returns the UN-ROTATED raster while every stored face geometry was
measured on the UPRIGHT one, so a crop endpoint that reuses the loader and cuts with the
stored box serves the wrong pixels on every phone portrait AND THE RIGHT ONES ON EVERY
LANDSCAPE. A square or landscape fixture cannot tell the two implementations apart - it is
the only shape where they agree - so the fixture here is a 400x300 JPEG carrying EXIF
Orientation 6, which decodes sideways and is 300x400 upright.

AND THE ORACLE IS THE PIXELS, NOT THE SIZE. The photograph has a magenta patch at a known
place in the UPRIGHT frame and is otherwise white; the assertion is that the served crop is
magenta. Asserting the crop's dimensions would pass for the wrong implementation, because
a box of the same normalised size cut out of the sideways raster is the same number of
pixels - just of the wrong part of the photograph.
`test_the_fixture_can_tell_the_two_implementations_apart` is the control: it cuts the
un-rotated raster with the same box by hand and shows the answer is white.

EVERY CATALOG ROW IS PLANTED WITH THE SUITE'S OWN SQL, `tests/test_faces_cluster.py`'s
rule, so nothing here is read back through the production query that wrote it.
"""

import io
import json
import logging
import sqlite3
import struct
import threading
import webbrowser
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

import pytest
from archives import FAKE_COMMAND
from click.testing import CliRunner
from PIL import Image

import pam.webui as webui_module
from pam.catalog import repo
from pam.catalog.db import connect
from pam.cli import cli
from pam.enrich.upright import upright
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan
from pam.webui import crop, faces_api, run_faces_label
from pam.webui.server import ReviewServer

# The fixture photograph, described once. STORED 400x300, DISPLAYED 300x400: EXIF
# Orientation 6 means "rotate 90 degrees clockwise to view", which is what a phone held
# upright writes and what makes this file the shape the hazard needs.
STORED_SIZE = (400, 300)
UPRIGHT_SIZE = (300, 400)
ORIENTATION_ROTATE_90 = 6

# Where the magenta patch sits in the UPRIGHT frame, as A13's normalised `[x, y, w, h]`.
# Deliberately off-centre in both axes: a box at the middle of the frame would land on
# itself under a 90-degree rotation of a square, and a patch that survived the wrong
# implementation would prove nothing.
PATCH_BOX = [0.1, 0.55, 0.3, 0.2]
MAGENTA = (255, 0, 255)
WHITE = (255, 255, 255)

MODEL = ("yunet+sface", "test-generation")

# The embedding column is NOT NULL and nothing here clusters, so every planted face gets
# the same four bytes. `tests/test_cluster.py` owns vectors that mean something.
EMBEDDING = struct.pack("<f", 1.0)


@pytest.fixture(autouse=True)
def no_real_browser(monkeypatch):
    """No test in this module opens a browser. `test_webui_thumbs.py`'s precedent."""
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: True)


def photograph(path: Path, *, orientation: int = ORIENTATION_ROTATE_90) -> None:
    """A white 400x300 JPEG with a magenta patch, carrying `orientation`.

    THE PATCH IS PAINTED IN THE UPRIGHT FRAME AND THEN UN-ROTATED, which is the only way
    to build this fixture honestly: `PATCH_BOX` is what a detector would have recorded,
    and a detector records against the raster `upright()` produces. So the patch is placed
    on a 300x400 canvas at `PATCH_BOX` and the canvas is turned back the other way before
    it is written, exactly undoing what `exif_transpose` will do on the way in.
    """
    width, height = UPRIGHT_SIZE
    canvas = Image.new("RGB", UPRIGHT_SIZE, WHITE)
    x, y, w, h = PATCH_BOX
    box = (
        round(x * width),
        round(y * height),
        round((x + w) * width),
        round((y + h) * height),
    )
    canvas.paste(Image.new("RGB", (box[2] - box[0], box[3] - box[1]), MAGENTA), box[:2])
    stored = canvas.transpose(Image.Transpose.ROTATE_90) if orientation == 6 else canvas
    exif = Image.Exif()
    exif[274] = orientation
    stored.save(path, format="JPEG", quality=95, exif=exif)


def archive(tmp_path, *, names=("IMG_1.JPG",), orientation=ORIENTATION_ROTATE_90) -> Path:
    """A really scanned archive holding real JPEGs. `pam.toml` pins the faces floor."""
    root = tmp_path / "photos"
    root.mkdir()
    for name in names:
        photograph(root / name, orientation=orientation)
        (root / f"{name}.tags.json").write_text(
            json.dumps({"DateTimeOriginal": "2019:07:04 18:23:11"}), encoding="utf-8"
        )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    ws = workspace_if_exists(root)
    assert ws is not None
    ws.config_path.write_text("[tiers]\nfaces = { floor = 0.4 }\n", encoding="utf-8")
    return root


def sha_of(root: Path, name: str) -> str:
    conn = _conn(root)
    try:
        row = conn.execute("SELECT sha256 FROM files WHERE filename = ?", (name,)).fetchone()
        assert row is not None and row["sha256"] is not None
        return str(row["sha256"])
    finally:
        conn.close()


def geometry(
    *,
    bbox=None,
    source=UPRIGHT_SIZE,
    orientation=ORIENTATION_ROTATE_90,
    version=1,
) -> dict[str, Any]:
    """A13's document, spelled out here from the SPEC rather than built by the enricher."""
    return {
        "v": version,
        "bbox": list(PATCH_BOX if bbox is None else bbox),
        "landmarks": [[0.5, 0.5]] * 5,
        "source_w": source[0],
        "source_h": source[1],
        "orientation": orientation,
    }


def plant_face(
    root: Path,
    sha256: str,
    *,
    geo: dict[str, Any] | None = None,
    confidence: float = 0.9,
    cluster_id: int | None = None,
    person_id: int | None = None,
    review_state: str | None = None,
) -> int:
    """One `face_instances` row, with the suite's own INSERT."""
    conn = _conn(root)
    try:
        cur = conn.execute(
            "INSERT INTO face_instances (sha256, bbox_json, embedding, det_confidence,"
            " cluster_id, person_id, review_state, model_name, model_version)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sha256,
                json.dumps(geometry() if geo is None else geo, sort_keys=True),
                EMBEDDING,
                confidence,
                cluster_id,
                person_id,
                review_state,
                MODEL[0],
                MODEL[1],
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


def plant_person(root: Path, label: str) -> int:
    conn = _conn(root)
    try:
        cur = conn.execute("INSERT INTO persons (label) VALUES (?)", (label,))
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


def _conn(root: Path) -> sqlite3.Connection:
    ws = workspace_if_exists(root)
    assert ws is not None
    handle = connect(ws.db_path)
    handle.row_factory = sqlite3.Row
    return handle


class Served:
    """A live `ReviewServer` over one archive, with `plan_id = None`."""

    def __init__(self, root: Path, plan_id: int | None = None) -> None:
        ws = workspace_if_exists(root)
        assert ws is not None
        logger = logging.getLogger("pam.tests.webui_faces")
        logger.propagate = False
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        self.server = ReviewServer(workspace=ws, plan_id=plan_id, logger=logger)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def request(self, method: str, path: str, body: Any = None):
        conn = HTTPConnection(self.server.host, self.server.port, timeout=20)
        try:
            payload = None if body is None else json.dumps(body).encode("utf-8")
            headers = {} if payload is None else {"Content-Type": "application/json"}
            conn.request(method, f"/t/{self.server.token}/api{path}", payload, headers)
            response = conn.getresponse()
            raw = response.read()
            return response.status, dict(response.getheaders()), raw
        finally:
            conn.close()

    def json(self, method: str, path: str, body: Any = None):
        status, headers, raw = self.request(method, path, body)
        return status, (json.loads(raw) if raw else None)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=20)


@pytest.fixture
def served(tmp_path):
    live = Served(archive(tmp_path))
    yield live
    live.close()


# --- the orientation hazard (invariant I4) -------------------------------------------


def crop_pixels(raw: bytes) -> list[tuple[int, int, int]]:
    """The distinct colours in a served crop, loosely quantised.

    JPEG is lossy and the crop is resampled, so an exact-colour assertion would be a test
    about the encoder. Quantising to the nearest 64 keeps magenta and white apart - they
    differ by 255 in the green channel - while surviving compression.
    """
    image = Image.open(io.BytesIO(raw)).convert("RGB")
    return _quantised(image)


def _quantised(image: Any) -> list[tuple[int, int, int]]:
    raw = image.convert("RGB").tobytes()
    return sorted(
        {
            (raw[i] // 64 * 64, raw[i + 1] // 64 * 64, raw[i + 2] // 64 * 64)
            for i in range(0, len(raw), 3)
        }
    )


def test_a_crop_of_a_portrait_photograph_is_the_pixels_the_detector_measured(tmp_path):
    """INVARIANT I4, on the only shape that can express it.

    The photograph decodes 400x300 and is 300x400 upright; the geometry was recorded
    against the upright frame. The implementation this test exists to refuse - reuse the
    shared loader, cut with the stored box - cuts the SIDEWAYS raster and is wrong on
    every phone portrait while staying right on every landscape, which is exactly the
    defect that ships.
    """
    root = archive(tmp_path)
    face_id = plant_face(root, sha_of(root, "IMG_1.JPG"))
    live = Served(root)
    try:
        status, headers, raw = live.request("GET", f"/faces/crop/{face_id}")
    finally:
        live.close()

    assert status == 200, raw
    assert headers["Content-Type"] == "image/jpeg"
    assert crop_pixels(raw) == [tuple(v // 64 * 64 for v in MAGENTA)]


def test_the_fixture_can_tell_the_two_implementations_apart(tmp_path):
    """THE CONTROL FOR THE TEST ABOVE, and without it that test proves nothing.

    A test that asserts a crop is magenta is worth exactly as much as the chance that the
    WRONG crop would have been magenta too. So the wrong implementation is written out
    here by hand - open the file, do not transpose, cut the same normalised box - and the
    answer is white. The two disagree, which is what makes the other test a measurement.
    """
    path = tmp_path / "one.jpg"
    photograph(path)
    stored = Image.open(path)
    assert stored.size == STORED_SIZE
    assert upright(stored).raster.size == UPRIGHT_SIZE

    width, height = stored.size
    x, y, w, h = PATCH_BOX
    wrong = stored.crop(
        (
            round(x * width),
            round(y * height),
            round((x + w) * width),
            round((y + h) * height),
        )
    ).convert("RGB")
    assert _quantised(wrong) == [tuple(v // 64 * 64 for v in WHITE)]


def test_the_enricher_and_the_crop_endpoint_call_one_shared_upright_function():
    """I4, structurally: ONE function, and both call sites are asserted by name.

    Written as an AST walk over the two modules rather than as an import check, because
    `CLAUDE.md`'s standing lesson is that an import is not a wiring - `enrich/faces.py`
    could import `upright` and still transpose inline, and every test above would pass
    because the inline copy would be right today. What this pins is that neither module
    calls `exif_transpose` itself.
    """
    import ast

    import pam

    package = Path(pam.__file__).parent
    callers = set()
    inline = set()
    for relative in ("enrich/faces.py", "webui/crop.py"):
        tree = ast.parse((package / relative).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else None
            )
            if name == "upright":
                callers.add(relative)
            if name == "exif_transpose":
                inline.add(relative)
    assert callers == {"enrich/faces.py", "webui/crop.py"}
    assert inline == set()


def test_a_geometry_recorded_against_another_raster_is_refused_not_served(tmp_path):
    """The frame check: a mismatch is a REFUSAL, never a confident crop of the wrong place.

    The document below says the face was measured on a 400x300 raster. It was not - this
    file is 300x400 upright - and the fractions in it therefore point somewhere else on
    the photograph. There is no repair available to a request handler, and the failure
    mode of serving anyway is a stranger's face under somebody's name (vision R6).
    """
    root = archive(tmp_path)
    face_id = plant_face(root, sha_of(root, "IMG_1.JPG"), geo=geometry(source=STORED_SIZE))
    live = Served(root)
    try:
        status, body = live.json("GET", f"/faces/crop/{face_id}")
    finally:
        live.close()

    assert status == 404
    assert "no longer decodes the way it did" in body["error"]
    assert "400x300" in body["error"] and "300x400" in body["error"]


def test_a_geometry_whose_width_alone_disagrees_is_refused(tmp_path):
    """The frame check's `source_w` OPERAND, separated from the other two (finding F3).

    The test above it changes BOTH axes at once - it records 400x300 against a 300x400
    raster - so the height operand alone refuses it and the width operand is never asked
    anything. Measured: dropping `source_w` from the comparison left the whole suite green
    (M1845). The state it would miss is ordinary rather than exotic - a photograph
    re-encoded at the same height and a different width, which is what a crop, a resize to
    a fixed height or an aspect-ratio correction produces - and the document's fractions
    then point at the wrong column of the picture while the row is right. What a wrong
    answer looks like here is a stranger's face under somebody's name (vision R6).

    So this records the TRUE upright height and a width that is 40 px wider, and nothing
    else. The refusal sentence must name both numbers, because the operand that fired is
    only visible in what the sentence says it compared.
    """
    root = archive(tmp_path)
    wider = (UPRIGHT_SIZE[0] + 40, UPRIGHT_SIZE[1])
    face_id = plant_face(root, sha_of(root, "IMG_1.JPG"), geo=geometry(source=wider))
    live = Served(root)
    try:
        status, body = live.json("GET", f"/faces/crop/{face_id}")
    finally:
        live.close()

    assert status == 404
    assert "no longer decodes the way it did" in body["error"]
    assert f"{wider[0]}x{wider[1]}" in body["error"]
    assert f"{UPRIGHT_SIZE[0]}x{UPRIGHT_SIZE[1]}" in body["error"]


def test_a_geometry_whose_height_alone_disagrees_is_refused(tmp_path):
    """The frame check's `source_h` OPERAND, separated from the other two (finding F3).

    M1845's measurement with the axes exchanged: dropping `source_h` from the comparison
    also left the whole suite green (M1846), because the only size-mismatch fixture in the
    repository differed in both axes and the surviving width operand still refused it.
    Two of the guard's three operands were free, which is `CLAUDE.md`'s measured rule
    reproduced exactly - a whole-guard deletion credits a guard with kills its individual
    operands do not have.
    """
    root = archive(tmp_path)
    taller = (UPRIGHT_SIZE[0], UPRIGHT_SIZE[1] + 40)
    face_id = plant_face(root, sha_of(root, "IMG_1.JPG"), geo=geometry(source=taller))
    live = Served(root)
    try:
        status, body = live.json("GET", f"/faces/crop/{face_id}")
    finally:
        live.close()

    assert status == 404
    assert "no longer decodes the way it did" in body["error"]
    assert f"{taller[0]}x{taller[1]}" in body["error"]
    assert f"{UPRIGHT_SIZE[0]}x{UPRIGHT_SIZE[1]}" in body["error"]


def test_a_geometry_recorded_under_another_orientation_is_refused(tmp_path):
    """The third operand of the frame check, on its own.

    Size and orientation are checked together and either alone would be a weaker guard: a
    90-degree rotation of a SQUARE photograph changes no dimension at all, so a size-only
    check would pass it and serve a face turned on its side.
    """
    root = archive(tmp_path)
    face_id = plant_face(root, sha_of(root, "IMG_1.JPG"), geo=geometry(orientation=1))
    live = Served(root)
    try:
        status, body = live.json("GET", f"/faces/crop/{face_id}")
    finally:
        live.close()

    assert status == 404
    assert "orientation" in body["error"]


def test_a_crop_never_reaches_the_thumbnail_extraction_chain(tmp_path, monkeypatch):
    """A18, AND `crop.py`'S OWN DOCSTRING SENTENCE, MADE GOOD ON (finding F2).

    The module header says it "deliberately shares no code with" `webui/thumbs.py`,
    because `-ThumbnailImage`, `-PreviewImage` and `-JpgFromRaw` all return ZERO BYTES on
    a real HEIC while Pillow decodes the same file at full size, and most phone
    photographs are HEIC. Until this test that sentence was an untested test - this
    project's own standing lesson - and it was measured: inserting an
    `thumbs.extract(source, thumbs.FULL)` first attempt into `crop._decoded`, with
    `pil_image_loader` kept only as a fallback, left the WHOLE SUITE GREEN (M1841). The
    plan named the killer it wanted, "a test on a real-shaped HEIC fixture", and every
    image in this repository is written by Pillow and carries no embedded preview at all,
    so the extraction returns `None` on all of them and the fallback fires every time.
    NOTHING IN THE SUITE COULD TELL THE MUTANT FROM THE ORIGINAL.

    So this watches the dangerous CALL instead of the answer, which is M8's M697 exactly:
    a test that asserts the OUTCOME is unchanged cannot tell "never did the dangerous
    thing" from "did it and got nothing back". The spy's own control is the second half -
    it is called by hand here, so `calls == []` cannot pass because the patch missed.

    The defect is real and user-visible, not theoretical: on a file that DOES carry a
    preview the extracted raster is a different size from the decoded one, so
    `_check_frame` turns every crop of that photograph into "this photograph no longer
    decodes the way it did" and the labeling page draws a broken card for it.
    """
    from pam.webui import thumbs

    calls: list[tuple[Path, str]] = []
    real_extract = thumbs.extract

    def spy(source, variant):
        calls.append((source, variant))
        return real_extract(source, variant)

    monkeypatch.setattr(thumbs, "extract", spy)

    root = archive(tmp_path)
    face_id = plant_face(root, sha_of(root, "IMG_1.JPG"))
    live = Served(root)
    try:
        status, _headers, raw = live.request("GET", f"/faces/crop/{face_id}")
    finally:
        live.close()

    assert status == 200 and raw
    assert calls == [], (
        "the crop path reached the thumbnail extraction chain: "
        f"{calls}. `crop.py` shares no code with `thumbs.py` by design (A18)"
    )

    # THE CONTROL UNDER THE ASSERTION ABOVE. Without it `calls == []` is satisfied just as
    # well by a spy that was never installed - `tests/test_webui_faces.py`'s own
    # `test_the_placeholder_spy_would_have_seen_an_open` is the precedent.
    thumbs.extract(root / "IMG_1.JPG", thumbs.FULL)
    assert len(calls) == 1, "the spy is not installed on the symbol the crop path would use"


def test_the_crop_module_names_the_thumbnail_module_nowhere_in_its_source():
    """The structural half of the test above, and it is the half that cannot be routed
    around: a spy pins the ONE call shape it patched, while this refuses the import
    itself, however the bytes were going to be reached (`thumbs.get_thumb`,
    `thumbs.attempts`, a re-exported name). An AST walk over the real parse, not a grep,
    because a hand-rolled reader cannot notice that the document is not the language.
    """
    import ast

    import pam

    tree = ast.parse((Path(pam.__file__).parent / "webui" / "crop.py").read_text("utf-8"))
    named = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            named.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            named.add(node.module or "")
            named.update(f"{node.module or ''}.{alias.name}" for alias in node.names)
    offenders = sorted(name for name in named if "thumbs" in name)
    assert offenders == [], (
        f"`webui/crop.py` now reaches `webui/thumbs.py` ({offenders}). Its header says it "
        "shares no code with that module because extraction returns zero bytes on HEIC"
    )


def test_a_crop_is_smaller_than_the_photograph_it_came_out_of(tmp_path):
    """The plan's named mutation: "`/faces/crop` returns the whole raster uncropped".

    Both dimensions, against the UPRIGHT size, and the patch's own normalised size is the
    expectation - so an endpoint that served the whole picture, or that served a box of
    some other size, is red rather than merely large.
    """
    root = archive(tmp_path)
    plant_face(root, sha_of(root, "IMG_1.JPG"))
    ws = workspace_if_exists(root)
    assert ws is not None
    conn = _conn(root)
    try:
        face = repo.face_by_id(conn, 1)
        rows = repo.files_by_sha256(conn, face.sha256)
    finally:
        conn.close()
    rendered = crop.render(root, rows[0], face.geometry)

    assert rendered.source == UPRIGHT_SIZE
    assert rendered.width == round(PATCH_BOX[2] * UPRIGHT_SIZE[0])
    assert rendered.height == round(PATCH_BOX[3] * UPRIGHT_SIZE[1])


# --- the resolver, and vision R2 ------------------------------------------------------


def test_the_resolver_picks_a_file_that_is_actually_there_among_duplicate_paths(tmp_path):
    """One photograph, two paths, one of them deleted. A face knows only its CONTENT.

    The catalog's ordering puts `a.jpg` first (both are `present` until the next scan and
    `rel_path` is the tiebreak), so an implementation that took `candidates[0]` and opened
    it would 404. The disk is what decides.
    """
    root = archive(tmp_path, names=("a.jpg", "b.jpg"))
    sha = sha_of(root, "a.jpg")
    assert sha == sha_of(root, "b.jpg"), "the two copies must be one content"
    (root / "a.jpg").unlink()
    face_id = plant_face(root, sha)

    live = Served(root)
    try:
        status, headers, raw = live.request("GET", f"/faces/crop/{face_id}")
    finally:
        live.close()

    assert status == 200, raw
    assert crop_pixels(raw) == [tuple(v // 64 * 64 for v in MAGENTA)]


def test_files_by_sha256_orders_present_before_missing_and_real_before_placeholder(tmp_path):
    """The repo query's ordering, which IS the pick. Spelled out from the rule, not read
    back off the query: `present` before `missing`, then not-placeholder before
    placeholder, then `rel_path` - and the last of those is what makes it deterministic."""
    root = archive(tmp_path, names=("a.jpg", "b.jpg", "c.jpg", "d.jpg"))
    sha = sha_of(root, "a.jpg")
    conn = _conn(root)
    try:
        conn.execute("UPDATE files SET status = 'missing' WHERE filename = 'a.jpg'")
        conn.execute("UPDATE files SET placeholder = 1 WHERE filename = 'b.jpg'")
        conn.commit()
        ordered = [row.filename for row in repo.files_by_sha256(conn, sha)]
    finally:
        conn.close()
    assert ordered == ["c.jpg", "d.jpg", "b.jpg", "a.jpg"]


def test_a_file_that_became_a_placeholder_since_the_scan_is_refused_and_never_opened(
    tmp_path, monkeypatch
):
    """VISION R2, and the assertion WATCHES THE DANGEROUS CALL rather than the outcome.

    The catalog says this file is not a placeholder - it was not one when the scan ran -
    and the live `os.stat` says it is. That is the case `thumbs.extractable` cannot see,
    because it trusts the scan-time column, and it is the case OneDrive produces by
    dehydrating a file between a scan and a request.

    M8's M697 is why the spy is on the LOADER and not on the response: a mis-ordered
    refusal there read a placeholder's bytes and returned the same 404 the correct order
    returns, with 1718 tests green. Asserting the 404 alone cannot tell "never opened it"
    from "opened it and then refused", and opening it IS the damage - reading a
    placeholder hydrates it.
    """
    root = archive(tmp_path)
    face_id = plant_face(root, sha_of(root, "IMG_1.JPG"))
    opened: list[str] = []

    real_loader = crop.pil_image_loader

    def spy(archive_root):
        inner = real_loader(archive_root)

        def load(file_row):
            opened.append(file_row.rel_path)
            return inner(file_row)

        return load

    monkeypatch.setattr(crop, "pil_image_loader", spy)
    monkeypatch.setattr(crop, "placeholder_now", lambda path: True)

    live = Served(root)
    try:
        status, body = live.json("GET", f"/faces/crop/{face_id}")
    finally:
        live.close()

    assert status == 404
    assert "stored online only" in body["error"]
    assert opened == [], "the placeholder's bytes were read, which hydrates it"


def test_the_placeholder_spy_would_have_seen_an_open(tmp_path, monkeypatch):
    """The control for the test above: with the probe answering False, the loader runs.

    Without this, `opened == []` is satisfied by a spy that was never installed correctly
    - a negative test passing for the wrong reason, which is the failure this project has
    measured more than once.
    """
    root = archive(tmp_path)
    face_id = plant_face(root, sha_of(root, "IMG_1.JPG"))
    opened: list[str] = []
    real_loader = crop.pil_image_loader

    def spy(archive_root):
        inner = real_loader(archive_root)

        def load(file_row):
            opened.append(file_row.rel_path)
            return inner(file_row)

        return load

    monkeypatch.setattr(crop, "pil_image_loader", spy)
    monkeypatch.setattr(crop, "placeholder_now", lambda path: False)

    live = Served(root)
    try:
        status, _headers, _raw = live.request("GET", f"/faces/crop/{face_id}")
    finally:
        live.close()

    assert status == 200
    assert opened == ["IMG_1.JPG"]


def test_a_photograph_that_has_moved_is_an_unavailable_state_and_not_a_crash(tmp_path):
    """The other unavailable state: the catalog knows the file, the disk does not."""
    root = archive(tmp_path)
    face_id = plant_face(root, sha_of(root, "IMG_1.JPG"))
    (root / "IMG_1.JPG").unlink()

    live = Served(root)
    try:
        status, body = live.json("GET", f"/faces/crop/{face_id}")
    finally:
        live.close()

    assert status == 404
    assert "not where pam last saw it" in body["error"]
    assert "pam scan" in body["error"]


def test_a_crop_is_never_stored_by_the_browser(tmp_path):
    """A19: pam has no crop cache it could inventory or purge, so nothing may keep one."""
    root = archive(tmp_path)
    face_id = plant_face(root, sha_of(root, "IMG_1.JPG"))
    live = Served(root)
    try:
        status, headers, _raw = live.request("GET", f"/faces/crop/{face_id}")
    finally:
        live.close()

    assert status == 200
    assert headers["Cache-Control"] == "no-store"


def test_no_other_response_carries_a_cache_control_header(tmp_path):
    """The header is the crop's, not the server's. A `RawResponse` with no headers sends
    none, so the thumbnail route and every JSON route are unchanged by Task 10."""
    root = archive(tmp_path)
    plant_face(root, sha_of(root, "IMG_1.JPG"), cluster_id=1)
    live = Served(root)
    try:
        _status, headers, _raw = live.request("GET", "/faces/clusters")
    finally:
        live.close()
    assert "Cache-Control" not in headers


# --- the read routes ------------------------------------------------------------------


def test_clusters_lists_the_unnamed_ones_biggest_first_with_the_progress_counts(tmp_path):
    """UX 6: one cluster at a time, biggest first, and a progress line over the WHOLE
    archive. The named cluster is absent from the queue and present in the count."""
    root = archive(tmp_path, names=("a.jpg", "b.jpg"))
    sha = sha_of(root, "a.jpg")
    for _ in range(2):
        plant_face(root, sha, cluster_id=1)
    for _ in range(5):
        plant_face(root, sha, cluster_id=2)
    person = plant_person(root, "Emma")
    plant_face(root, sha, cluster_id=3, person_id=person)

    live = Served(root)
    try:
        status, body = live.json("GET", "/faces/clusters")
    finally:
        live.close()

    assert status == 200
    assert [row["cluster_id"] for row in body["clusters"]] == [2, 1]
    assert [row["faces"] for row in body["clusters"]] == [5, 2]
    assert body["named"] == 1
    assert body["remaining"] == 2


def test_the_progress_counts_do_not_move_when_the_queue_is_paged(tmp_path):
    """`named` and `remaining` are about the archive; `offset`/`limit` are about the page.
    A progress line that fell as the user scrolled would be reporting the wrong thing."""
    root = archive(tmp_path)
    sha = sha_of(root, "IMG_1.JPG")
    for cluster_id in (1, 2, 3):
        plant_face(root, sha, cluster_id=cluster_id)

    live = Served(root)
    try:
        _status, first = live.json("GET", "/faces/clusters?limit=1")
        _status, second = live.json("GET", "/faces/clusters?limit=1&offset=2")
    finally:
        live.close()

    assert len(first["clusters"]) == 1 and len(second["clusters"]) == 1
    assert first["clusters"][0]["cluster_id"] != second["clusters"][0]["cluster_id"]
    assert (first["remaining"], first["total"]) == (3, 3)
    assert (second["remaining"], second["total"]) == (3, 3)


def test_the_sample_spreads_across_photographs_before_it_repeats_one(tmp_path):
    """A18: "chosen for detection quality and spread, NEVER ROW ORDER".

    Six faces in one cluster: five of them in `a.jpg` at high confidence and one in
    `b.jpg` at low. Row order and confidence order both put the five first; spread is what
    puts `b.jpg` second, so a user naming this cluster sees two photographs rather than
    five crops of one group shot.
    """
    root = archive(tmp_path, names=("a.jpg", "b.jpg"))
    conn = _conn(root)
    try:
        conn.execute("UPDATE files SET sha256 = 'sha-b' WHERE filename = 'b.jpg'")
        conn.commit()
    finally:
        conn.close()
    for _ in range(5):
        plant_face(root, sha_of(root, "a.jpg"), cluster_id=1, confidence=0.95)
    plant_face(root, "sha-b", cluster_id=1, confidence=0.5)

    live = Served(root)
    try:
        _status, body = live.json("GET", "/faces/clusters")
    finally:
        live.close()

    sample = body["clusters"][0]["sample"]
    assert len(sample) == faces_api.FACE_SAMPLE
    assert [row["filename"] for row in sample[:2]] == ["a.jpg", "b.jpg"]


def test_one_cluster_expands_to_every_face_in_it_and_never_to_its_embeddings(tmp_path):
    """`/faces/cluster/{id}`, and the negative half is the one worth writing down: the
    embedding is 512 bytes of biometric vector per face and the browser has no use for
    it, so no response field may carry it."""
    root = archive(tmp_path)
    sha = sha_of(root, "IMG_1.JPG")
    ids = [plant_face(root, sha, cluster_id=7) for _ in range(3)]

    live = Served(root)
    try:
        status, body = live.json("GET", "/faces/cluster/7")
    finally:
        live.close()

    assert status == 200
    assert [row["face_id"] for row in body["faces"]] == ids
    assert body["photographs"] == 1
    assert all("embedding" not in row for row in body["faces"])


def test_a_cluster_whose_faces_are_all_ignored_is_gone_rather_than_empty(tmp_path):
    """A13: a face in a non-NULL `review_state` "is never offered for labeling". The page
    must show that as a cluster that is no longer there, not as a cluster with no faces -
    which is why this is a 404 and not `{"faces": []}`."""
    root = archive(tmp_path)
    plant_face(root, sha_of(root, "IMG_1.JPG"), cluster_id=4, review_state="dismissed")

    live = Served(root)
    try:
        status, body = live.json("GET", "/faces/cluster/4")
        _status, queue = live.json("GET", "/faces/clusters")
    finally:
        live.close()

    assert status == 404
    assert "no unnamed cluster with id 4" in body["error"]
    assert queue["clusters"] == []


def test_a_face_below_the_configured_floor_is_not_offered_for_labeling(tmp_path):
    """A11's floor is applied at READ time, and this endpoint is one of the readers. The
    archive's `pam.toml` says 0.4; a 0.3 detection is stored and not shown."""
    root = archive(tmp_path)
    sha = sha_of(root, "IMG_1.JPG")
    plant_face(root, sha, cluster_id=1, confidence=0.3)
    kept = plant_face(root, sha, cluster_id=1, confidence=0.5)

    live = Served(root)
    try:
        _status, body = live.json("GET", "/faces/cluster/1")
    finally:
        live.close()

    assert [row["face_id"] for row in body["faces"]] == [kept]


def test_persons_serves_the_autocomplete_including_hidden_people(tmp_path):
    """UX 6's autocomplete exists so `Emma` and `emma` do not become two people. A hidden
    person is still a person pam knows: leaving them out would autocomplete a NEW row for
    a name that already exists, which is the exact duplicate this endpoint prevents."""
    root = archive(tmp_path)
    emma = plant_person(root, "Emma")
    conn = _conn(root)
    try:
        conn.execute("INSERT INTO persons (label, hidden) VALUES ('Sam', 1)")
        conn.commit()
    finally:
        conn.close()
    plant_face(root, sha_of(root, "IMG_1.JPG"), cluster_id=1, person_id=emma)

    live = Served(root)
    try:
        status, body = live.json("GET", "/faces/persons")
    finally:
        live.close()

    assert status == 200
    by_label = {row["label"]: row for row in body["persons"]}
    assert set(by_label) == {"Emma", "Sam"}
    assert by_label["Emma"]["faces"] == 1
    assert by_label["Sam"]["hidden"] is True


# --- label ----------------------------------------------------------------------------


def test_label_attaches_every_face_in_the_cluster_and_says_how_many_of_each(tmp_path):
    """Spec 8.2 / 6.2 rule 7: the response reports FACES and PHOTOGRAPHS, because "this is
    the action a user is most likely to regret" and forty faces in three photographs is a
    different mistake from forty faces in forty."""
    root = archive(tmp_path, names=("a.jpg", "b.jpg"))
    conn = _conn(root)
    try:
        conn.execute("UPDATE files SET sha256 = 'sha-b' WHERE filename = 'b.jpg'")
        conn.commit()
    finally:
        conn.close()
    for _ in range(3):
        plant_face(root, sha_of(root, "a.jpg"), cluster_id=1)
    plant_face(root, "sha-b", cluster_id=1)

    live = Served(root)
    try:
        status, body = live.json("POST", "/faces/cluster/1/label", {"person": "Emma"})
    finally:
        live.close()

    assert status == 200
    assert (body["faces"], body["photographs"]) == (4, 2)

    conn = _conn(root)
    try:
        named = conn.execute(
            "SELECT COUNT(*) AS n FROM face_instances WHERE person_id IS NOT NULL"
        ).fetchone()["n"]
    finally:
        conn.close()
    assert named == 4, "label must attach EVERY face in the cluster, not the first"


def test_label_matches_an_existing_person_case_insensitively(tmp_path):
    """Spec 8.2 in as many words: "so `Emma` and `emma` do not become two people". The
    existing row's spelling wins, so the archive keeps one name rather than whichever
    capitalisation was typed last."""
    root = archive(tmp_path)
    emma = plant_person(root, "Emma")
    plant_face(root, sha_of(root, "IMG_1.JPG"), cluster_id=1)

    live = Served(root)
    try:
        status, body = live.json("POST", "/faces/cluster/1/label", {"person": "emma"})
    finally:
        live.close()

    assert status == 200
    assert (body["person_id"], body["label"]) == (emma, "Emma")

    conn = _conn(root)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM persons").fetchone()["n"] == 1
    finally:
        conn.close()


def test_label_refuses_a_blank_name_by_naming_the_field(tmp_path):
    """UX 1.4: a refusal that does not say which control is wrong is a dead end. And a
    person whose label is three spaces is a row nothing could ever match again."""
    root = archive(tmp_path)
    plant_face(root, sha_of(root, "IMG_1.JPG"), cluster_id=1)

    live = Served(root)
    try:
        status, body = live.json("POST", "/faces/cluster/1/label", {"person": "   "})
    finally:
        live.close()

    assert status == 400
    assert body["error"].startswith("person:")


def test_label_never_writes_a_person_onto_an_ignored_face(tmp_path):
    """A13 again, from the write side: a face the user has said is not a face must not
    come back carrying somebody's name. `{people}` reads `review_state IS NULL`, so the
    name would be stored and then never used - a row that is wrong and invisible."""
    root = archive(tmp_path)
    sha = sha_of(root, "IMG_1.JPG")
    live_face = plant_face(root, sha, cluster_id=1)
    ignored = plant_face(root, sha, cluster_id=1, review_state="not_a_face")

    served_pages = Served(root)
    try:
        status, body = served_pages.json("POST", "/faces/cluster/1/label", {"person": "Emma"})
    finally:
        served_pages.close()

    assert status == 200 and body["faces"] == 1
    conn = _conn(root)
    try:
        rows = {
            int(r["id"]): r["person_id"]
            for r in conn.execute("SELECT id, person_id FROM face_instances")
        }
    finally:
        conn.close()
    assert rows[live_face] is not None
    assert rows[ignored] is None


# --- Step 5: no plan ------------------------------------------------------------------


def test_ping_reports_no_plan_without_asking_the_catalog_about_one(tmp_path, monkeypatch):
    """Spec 8.2 rev 22: "a type change is not a test", and this is why.

    `repo.plan_status(conn, None)` DOES NOT RAISE - `WHERE id = NULL` matches nothing -
    so the un-branched `_api_ping` returns the same `{"plan_id": null, "status": null}`
    this one does, by a lookup that could never have produced any other answer. The
    assertion is therefore on the CALL: with no plan, the catalog is not asked. Watching
    the response alone could not tell the two implementations apart.
    """
    from pam.webui import server as server_module

    def refuse(conn, plan_id):
        raise AssertionError(f"_api_ping asked the catalog about plan {plan_id!r}")

    monkeypatch.setattr(server_module.repo, "plan_status", refuse)

    root = archive(tmp_path)
    live = Served(root, plan_id=None)
    try:
        status, body = live.json("GET", "/ping")
    finally:
        live.close()

    assert status == 200
    # M14 Task 5d (W23): ping names the bound folder. M14 Task 6 S2-F2: and recovery's line from
    # a bind - None on a plain `ReviewServer`, which never binds.
    folder = str(root.resolve())
    assert body == {
        "plan_id": None,
        "status": None,
        "binding": 1,
        "folder": folder,
        "recovery": None,
    }


def test_pam_faces_label_serves_a_working_page_with_no_open_plan(tmp_path, monkeypatch):
    """A18: "a command whose whole job is corpus-wide people labeling must not refuse to
    start because no plan is open".

    `run_review` CANNOT serve this: `_resolve_plan` always returns an `int` and refuses
    when there is none. So the archive below has never been planned, and the assertion is
    that the faces page and its routes answer anyway.
    """
    root = archive(tmp_path)
    plant_face(root, sha_of(root, "IMG_1.JPG"), cluster_id=1)
    conn = _conn(root)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM plans").fetchone()["n"] == 0
    finally:
        conn.close()

    answers: dict[str, Any] = {}

    def drive(server):
        """Serve on a thread, drive the page from this one, then stop it.

        `run_faces_label` hands the server to whatever `serve` it was given INSTEAD of
        `serve_until_interrupted`, so a callback that only made requests would be asking a
        server that is not accepting any. This is the real serve loop, in a thread, with
        the requests where a user's browser would be.
        """
        assert server.plan_id is None
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for name, path in (
                ("page", f"/t/{server.token}/faces.html"),
                ("ping", f"/t/{server.token}/api/ping"),
                ("clusters", f"/t/{server.token}/api/faces/clusters"),
            ):
                connection = HTTPConnection(server.host, server.port, timeout=20)
                try:
                    connection.request("GET", path)
                    response = connection.getresponse()
                    answers[name] = (response.status, response.read())
                finally:
                    connection.close()
        finally:
            server.shutdown()
            thread.join(timeout=20)

    summary = run_faces_label(root, serve=drive, opener=lambda url: True)

    assert answers["page"][0] == 200
    assert b"<html" in answers["page"][1].lower() or b"<!doctype" in answers["page"][1].lower()
    # M14 Task 6 S2-F2: `recovery` is None on a plain `ReviewServer`, which never binds.
    ping = {
        "plan_id": None,
        "status": None,
        "binding": 1,
        "folder": str(root.resolve()),
        "recovery": None,
    }
    assert json.loads(answers["ping"][1]) == ping
    assert json.loads(answers["clusters"][1])["remaining"] == 1
    assert summary.url.endswith("faces.html")
    assert (summary.named, summary.remaining) == (0, 1)


def test_pam_faces_label_is_a_real_command_that_refuses_an_unscanned_folder(tmp_path):
    """Spec 13: the refusal exits 1 and names the command that moves the user forward."""
    folder = tmp_path / "not-scanned"
    folder.mkdir()
    result = CliRunner().invoke(cli, ["faces", "label", "--archive", str(folder)])
    assert result.exit_code != 0


def test_pam_faces_label_announces_the_faces_page_and_not_the_review_page(
    tmp_path, monkeypatch
):
    """The two commands serve one server at two pages, and the console says which. A
    labeling session announced as "Review at ..." would send the user to a page about a
    plan that does not exist."""
    import click

    root = archive(tmp_path)
    printed: list[str] = []
    original = click.echo

    def record(message="", **kwargs):
        printed.append(str(message))
        return original(message, **kwargs)

    monkeypatch.setattr(webui_module.click, "echo", record)
    run_faces_label(root, serve=lambda server: None, opener=lambda url: True)

    assert any(line.startswith("Name people at ") for line in printed)
    assert any("Name the people pam found" in line for line in printed)
    assert not any(line.startswith("Review at ") for line in printed)


# -------------------------------------------------------------------------------------
# THE LOAD-BEARING CONTROLS OF THIS MODULE (M10's C4).
#
# HAND-SPELLED, NEVER DERIVED FROM `globals()`. An inventory that asks the module what it
# contains passes the very mutation it exists to catch: delete the test and the name goes
# with it, and the two agree about less.
# -------------------------------------------------------------------------------------

LOAD_BEARING_CONTROLS = {
    "test_a_crop_of_a_portrait_photograph_is_the_pixels_the_detector_measured": (
        "invariant I4, and the ONLY test in the repository that can see the "
        "orientation hazard - a landscape or square fixture cannot, because that is "
        "the shape where the right and the wrong implementations agree"
    ),
    "test_the_fixture_can_tell_the_two_implementations_apart": (
        "the control under the test above. Without it, 'the crop is magenta' is worth "
        "the chance that the WRONG crop would have been magenta too"
    ),
    "test_the_enricher_and_the_crop_endpoint_call_one_shared_upright_function": (
        "I4 structurally, and it walks CALLS rather than imports - an import is not a "
        "wiring, and an inline `exif_transpose` beside the shared call would be right "
        "today and drift tomorrow"
    ),
    "test_a_file_that_became_a_placeholder_since_the_scan_is_refused_and_never_opened": (
        "vision R2, watching the LOADER and not the response. M8's M697: a mis-ordered "
        "refusal read a placeholder's bytes and returned the same 404, 1718 tests green"
    ),
    "test_the_placeholder_spy_would_have_seen_an_open": (
        "the control under the R2 test - it is what stops `opened == []` passing "
        "because the spy was never installed"
    ),
    "test_ping_reports_no_plan_without_asking_the_catalog_about_one": (
        "Step 5's whole point: `repo.plan_status(conn, None)` returns None rather than "
        "raising, so only an assertion about the CALL can see the explicit branch"
    ),
    "test_a_geometry_whose_width_alone_disagrees_is_refused": (
        "`_check_frame`'s `source_w` operand ALONE. The both-axes fixture beside it "
        "cannot reach it - dropping this operand was green over the whole suite (M1845)"
    ),
    "test_a_geometry_whose_height_alone_disagrees_is_refused": (
        "`_check_frame`'s `source_h` operand ALONE, M1845 with the axes exchanged "
        "(M1846). Two of the guard's three operands were free until this pair existed"
    ),
    "test_a_crop_never_reaches_the_thumbnail_extraction_chain": (
        "A18, watching the CALL. An extraction-first `_decoded` was green over 3372 "
        "tests (M1841) because no image in this repository carries an embedded preview"
    ),
    "test_the_crop_module_names_the_thumbnail_module_nowhere_in_its_source": (
        "the structural half of the test above - a spy pins one call shape, this "
        "refuses the import however the bytes were going to be reached"
    ),
    "test_label_attaches_every_face_in_the_cluster_and_says_how_many_of_each": (
        "the counting test the plan names - 'label attaches only the first face' is "
        "otherwise green, because one face is a cluster too"
    ),
    "test_a_geometry_recorded_under_another_orientation_is_refused": (
        "invariant I4's orientation proof, the third geometry refusal. MOVED here at M12 "
        "Task 1 from `tests/test_webui_api.py`'s FACES_API_ANCHORED_CONTROLS, the "
        "cross-module anchor under this file (M1836: deleted whole, 3343 passed, 0 failed)"
    ),
}

assert LOAD_BEARING_CONTROLS, "this module's control list is empty; it protects nothing"


def test_no_faces_api_control_in_this_file_can_be_deleted_without_saying_so():
    """The floor under this module's inventory (M10's C4, amendment R4)."""
    present = {name for name in globals() if name.startswith("test_")}
    missing = sorted(set(LOAD_BEARING_CONTROLS) - present)
    assert missing == [], (
        f"a load-bearing control has been deleted from {Path(__file__).name}:"
        f" {missing}. Each is why another assertion here means anything"
        f" ({[LOAD_BEARING_CONTROLS[m] for m in missing]})."
    )
