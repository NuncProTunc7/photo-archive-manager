"""Spec 8.2's thumbnails: extracted embedded previews, over real loopback HTTP (Task 10).

THE FIXTURE FILES ARE REAL BYTES AND THE EXTRACTOR IS THE REAL EXIFTOOL. A stand-in
cannot be used here the way `fake_exiftool.py` stands in for the scanner: what is under
test is whether the frozen `-b` arguments pull the bytes a camera actually embedded out
of a file that actually embeds them, and a fake that returned bytes on request would
confirm only what this module's author already believed. So the archive files below are
assembled from hand-written JPEG/TIFF bytes - no Pillow, following `tools/build_tiny_
library.py`'s precedent - and real exiftool reads them.

WHY THEY ARE NOT IN THE TINY LIBRARY. Nothing in `tests/tiny_library/` carries an
embedded preview of any kind: sweeping all 106 files with `-if '$ThumbnailImage'`,
`-if '$PreviewImage'` and `-if '$JpgFromRaw'` returns nothing for all three. The plan
says to check that before building a fixture, and to add one HERE rather than to the
tiny library if the check comes back empty, because that library's manifest is pinned.

THE THREE IMAGE FIXTURES ARE THREE DIFFERENT ANSWERS, and the middle one is the point:
- `WITHTHUMB.JPG` carries an EXIF IFD1 thumbnail -> `-ThumbnailImage` finds it.
- `LARGEONLY.JPG` carries a preview that is NOT a thumbnail (`JpgFromRaw`) -> the grid
  finds nothing and 404s to a glyph card, while the overlay's chain finds it on its
  second link. That pairing IS amendment A2's first amendment, exercised rather than
  asserted, and it is also the only test here that proves the chain has more than one
  working link.
- `NOPREVIEW.JPG` is a structurally complete JPEG carrying nothing -> 404 for both.

THE BROWSER IS PATCHED FOR EVERY TEST IN THIS MODULE by an autouse fixture, and the
module's logger is its own with `propagate = False`: `open_session_log` clears the `pam`
logger's handlers, so a module-scoped fixture that borrowed it would have its log file
silently killed by the next command-driving test (finding M628a).
"""

import dataclasses
import json
import logging
import os
import shutil
import struct
import subprocess
import threading
import webbrowser
from http.client import HTTPConnection
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from archives import FAKE_COMMAND

from pam.catalog import repo
from pam.catalog.db import connect
from pam.catalog.repo.files import FileRow
from pam.catalog.repo.review import ReviewItem
from pam.errors import ExifToolError
from pam.naming.planner import run_plan
from pam.paths import Workspace, workspace_if_exists
from pam.scan import exiftool
from pam.scan.runner import run_scan
from pam.verify import checks
from pam.verify.checks import _thumb_key
from pam.webui import thumbs
from pam.webui.server import ReviewServer

_REAL_WEBBROWSER_OPEN = webbrowser.open

# Extraction is the real binary or it is nothing (see the module docstring), so the
# tests that extract skip where it is absent - `tests/test_video_real_exiftool.py`'s
# precedent. It is applied test by test rather than to the module, because every
# REFUSAL below returns before exiftool is reached and those are the tests that must
# never be quietly skipped: the placeholder one is vision R2's promise.
needs_exiftool = pytest.mark.skipif(
    shutil.which("exiftool") is None, reason="needs the real exiftool on PATH"
)


@pytest.fixture(autouse=True)
def no_real_browser(monkeypatch):
    """Every test in this module gets a stand-in for `webbrowser.open`."""
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: True)


def test_the_browser_patch_is_autouse_and_not_opt_in():
    """Takes no fixture argument, so it is only ever patched by the autouse above."""
    assert webbrowser.open is not _REAL_WEBBROWSER_OPEN


# --- hand-written image bytes --------------------------------------------------------


def _segment(marker: bytes, payload: bytes) -> bytes:
    return marker + struct.pack(">H", len(payload) + 2) + payload


def baseline_jpeg(exif: bytes | None, comment: str) -> bytes:
    """A structurally complete baseline JPEG: SOI, APP1, COM, DQT, SOF0, SOS, EOI.

    Complete rather than minimal for `tools/build_tiny_library.py`'s reason: two bytes
    are enough for exiftool to say JPEG, and also enough to earn a warning on every
    file, which makes a warning on ONE file invisible.
    """
    parts = [b"\xff\xd8"]
    if exif is not None:
        parts.append(_segment(b"\xff\xe1", b"Exif\x00\x00" + exif))
    parts.append(_segment(b"\xff\xfe", comment.encode("utf-8")))
    parts.append(_segment(b"\xff\xdb", b"\x00" + bytes([16] * 64)))
    parts.append(
        _segment(b"\xff\xc0", b"\x08" + struct.pack(">HH", 48, 64) + b"\x01\x01\x11\x00")
    )
    parts.append(_segment(b"\xff\xda", b"\x01\x01\x00\x00\x3f\x00"))
    parts.append(b"\x3f\xd0\x00\x00\xff\xd9")
    return b"".join(parts)


def _entry(tag: int, kind: int, count: int, payload: bytes) -> bytes:
    """One 12-byte IFD entry. `payload` is the 4-byte value or offset field."""
    return struct.pack(">HHI", tag, kind, count) + payload


def _padded(text: bytes) -> bytes:
    value = text + b"\x00"
    return value if len(value) % 2 == 0 else value + b"\x00"


def exif_with_thumbnail(thumbnail: bytes, description: bytes) -> bytes:
    """A big-endian TIFF block whose IFD1 is a real EXIF thumbnail.

    IFD1 is where every camera writes the small preview and where exiftool reads
    `ThumbnailImage` from: tag 0x0201 is the offset of the JPEG and 0x0202 its length,
    both relative to the start of the TIFF header.
    """
    desc = _padded(description)
    ifd0_at = 8
    ifd0_size = 2 + 12 + 4
    desc_at = ifd0_at + ifd0_size
    ifd1_at = desc_at + len(desc)
    thumbnail_at = ifd1_at + 2 + 12 * 3 + 4
    ifd0 = (
        struct.pack(">H", 1)
        + _entry(0x010E, 2, len(desc), struct.pack(">I", desc_at))
        + struct.pack(">I", ifd1_at)
    )
    ifd1 = (
        struct.pack(">H", 3)
        + _entry(0x0103, 3, 1, struct.pack(">HH", 6, 0))
        + _entry(0x0201, 4, 1, struct.pack(">I", thumbnail_at))
        + _entry(0x0202, 4, 1, struct.pack(">I", len(thumbnail)))
        + struct.pack(">I", 0)
    )
    return b"MM\x00\x2a" + struct.pack(">I", ifd0_at) + ifd0 + desc + ifd1 + thumbnail


def exif_with_large_preview_only(preview: bytes, description: bytes) -> bytes:
    """A TIFF block carrying a preview that is NOT a thumbnail.

    The same 0x0201/0x0202 pair, one IFD further along the chain: in IFD1 exiftool calls
    it `ThumbnailImage`, in IFD2 it calls it `JpgFromRaw`. That is what makes this file
    the A2-amendment-1 case - the grid's `-ThumbnailImage` finds nothing in it, and the
    overlay's chain finds it on its second link. IFD1 is present but carries only an
    ImageWidth, so there is genuinely no thumbnail rather than merely no IFD1.
    """
    desc = _padded(description)
    ifd0_at = 8
    desc_at = ifd0_at + 2 + 12 + 4
    ifd1_at = desc_at + len(desc)
    ifd2_at = ifd1_at + 2 + 12 + 4
    preview_at = ifd2_at + 2 + 12 * 3 + 4
    ifd0 = (
        struct.pack(">H", 1)
        + _entry(0x010E, 2, len(desc), struct.pack(">I", desc_at))
        + struct.pack(">I", ifd1_at)
    )
    ifd1 = (
        struct.pack(">H", 1)
        + _entry(0x0100, 3, 1, struct.pack(">HH", 64, 0))
        + struct.pack(">I", ifd2_at)
    )
    ifd2 = (
        struct.pack(">H", 3)
        + _entry(0x0103, 3, 1, struct.pack(">HH", 6, 0))
        + _entry(0x0201, 4, 1, struct.pack(">I", preview_at))
        + _entry(0x0202, 4, 1, struct.pack(">I", len(preview)))
        + struct.pack(">I", 0)
    )
    return b"MM\x00\x2a" + struct.pack(">I", ifd0_at) + ifd0 + desc + ifd1 + ifd2 + preview


# The two embedded images, byte-for-byte what a 200 must hand back.
EMBEDDED_THUMBNAIL = baseline_jpeg(None, "pam embedded thumbnail")
EMBEDDED_PREVIEW = baseline_jpeg(None, "pam large embedded preview, not a thumbnail")

WITH_THUMB = "WITHTHUMB.JPG"
LARGE_ONLY = "LARGEONLY.JPG"
NO_PREVIEW = "NOPREVIEW.JPG"
CLIP = "CLIP.MOV"
SIDECAR = "WITHTHUMB.xmp"
OTHER = "NOTES.txt"

ARCHIVE: dict[str, bytes] = {
    WITH_THUMB: baseline_jpeg(
        exif_with_thumbnail(EMBEDDED_THUMBNAIL, b"pam thumbnail fixture"), "pam fixture"
    ),
    LARGE_ONLY: baseline_jpeg(
        exif_with_large_preview_only(EMBEDDED_PREVIEW, b"pam preview fixture"),
        "pam fixture",
    ),
    NO_PREVIEW: baseline_jpeg(None, "pam fixture with nothing embedded"),
    CLIP: b"not really a movie, and nothing reads these bytes",
    SIDECAR: b"<x:xmpmeta/>",
    OTHER: b"a text file pam types as `other`",
}

TAGS = {
    WITH_THUMB: {"DateTimeOriginal": "2019:07:04 18:23:11"},
    LARGE_ONLY: {"DateTimeOriginal": "2020:08:05 09:15:00"},
    NO_PREVIEW: {"DateTimeOriginal": "2021:09:06 11:02:03"},
    CLIP: {"CreateDate": "2022:03:03 10:00:00"},
}


# --- the archive, the server, and one connection per request -------------------------


class Live(NamedTuple):
    server: ReviewServer
    root: Path
    db_path: Path
    thumbs_dir: Path
    plan_id: int
    token: str
    file_ids: dict[str, int]  # relative filename -> files.id


def _put(directory: Path, name: str, payload: bytes) -> None:
    (directory / name).write_bytes(payload)
    (directory / f"{name}.tags.json").write_text(
        json.dumps({**TAGS.get(name, {}), "PamTestSidecar": name}), encoding="utf-8"
    )


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """A real scanned archive, a real plan, and a real server serving them.

    PAM_HOME is set and restored INSIDE the setup, never held across the yield:
    `conftest.py`'s `isolated_pam_home` is function-scoped and would fight it.
    """
    home = tmp_path_factory.mktemp("thumbs-home")
    previous = os.environ.get("PAM_HOME")
    os.environ["PAM_HOME"] = str(home)
    try:
        root = tmp_path_factory.mktemp("thumbs-archive") / "photos"
        root.mkdir(parents=True)
        for name, payload in ARCHIVE.items():
            _put(root, name, payload)
        run_scan(root, exiftool_command=FAKE_COMMAND)
        plan = run_plan(root)
        assert plan.plan_id is not None
        ws = workspace_if_exists(root)
        assert ws is not None
    finally:
        if previous is None:
            os.environ.pop("PAM_HOME", None)
        else:
            os.environ["PAM_HOME"] = previous

    conn = connect(ws.db_path)
    try:
        file_ids = {row.rel_path: row.id for row in repo.all_files(conn).values()}
    finally:
        conn.close()

    logger = logging.getLogger("pam.tests.webui_thumbs")
    logger.propagate = False
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    server = ReviewServer(workspace=ws, plan_id=plan.plan_id, logger=logger)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Live(
        server=server,
        root=root,
        db_path=ws.db_path,
        thumbs_dir=ws.thumbs_dir,
        plan_id=plan.plan_id,
        token=server.token,
        file_ids=file_ids,
    )
    server.shutdown()
    server.server_close()
    thread.join(timeout=20)


class Reply(NamedTuple):
    status: int
    content_type: str | None
    body: bytes


def fetch(live: Live, path: str, **params: Any) -> Reply:
    """One GET to the live server, RAW. The body of a 200 here is an image."""
    query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    url = f"/t/{live.token}/api{path}" + (f"?{query}" if query else "")
    conn = HTTPConnection(live.server.host, live.server.port, timeout=20)
    try:
        conn.request("GET", url)
        response = conn.getresponse()
        return Reply(response.status, response.getheader("Content-Type"), response.read())
    finally:
        conn.close()


def message(reply: Reply) -> str:
    return str(json.loads(reply.body)["error"])


@pytest.fixture
def empty_cache(live):
    """An empty thumbnail cache before the test and after it.

    Isolation by FIXTURE, never by ordering: the archive and the server are module
    scoped, and a test that asserted "the cache now holds one file" would otherwise be
    reading whatever the test before it left behind.
    """
    _clear(live.thumbs_dir)
    yield live.thumbs_dir
    _clear(live.thumbs_dir)


def _clear(directory: Path) -> None:
    for path in directory.iterdir():
        if path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)


@pytest.fixture
def restored_archive(live):
    """Put every archive file back afterwards, for tests that delete one on purpose."""
    yield live.root
    for name, payload in ARCHIVE.items():
        (live.root / name).write_bytes(payload)


# --- the frozen arguments, spelled from spec 8.2 rather than read from the module ----

# Spec 8.2 rev 12: "`-b -ThumbnailImage` for the grid; `-b -PreviewImage` falling back
# to `-b -JpgFromRaw` then `-b -ThumbnailImage` for the overlay." Written out here as
# the spec writes it, NOT built by calling `thumbs.attempts` - an oracle built from the
# definition it checks has cost this project four measured findings.
SPEC_GRID_ATTEMPTS = (("-b", "-ThumbnailImage"),)
SPEC_FULL_ATTEMPTS = (
    ("-b", "-PreviewImage"),
    ("-b", "-JpgFromRaw"),
    ("-b", "-ThumbnailImage"),
)

# exiftool's writing and file-producing options. Any one of them in a frozen tuple would
# mean pam could modify or duplicate a photograph while "reading" it, which is the claim
# spec 5.3 has made since M2 and which amendment A7 extends to this third surface.
WRITE_FLAGS = (
    "-overwrite_original",
    "-overwrite_original_in_place",
    "-delete_original",
    "-restore_original",
    "-tagsfromfile",
    "-geotag",
    "-o",
    "-out",
    "-w",
    "-W",
    "-P",
)


def test_the_grid_arguments_are_exactly_what_spec_8_2_freezes():
    assert thumbs.attempts(thumbs.GRID) == SPEC_GRID_ATTEMPTS


def test_the_overlay_chain_is_exactly_what_spec_8_2_freezes_in_that_order():
    assert thumbs.attempts(thumbs.FULL) == SPEC_FULL_ATTEMPTS


def test_the_grid_never_reaches_for_a_larger_tag_than_the_thumbnail():
    """Amendment A2's first amendment, as a rule about the arguments themselves."""
    named = {arg for attempt in thumbs.attempts(thumbs.GRID) for arg in attempt}
    assert "-PreviewImage" not in named
    assert "-JpgFromRaw" not in named


def assert_read_only(attempt: tuple[str, ...]) -> None:
    """One argument tuple, held to spec 5.3's read-only claim.

    A function rather than an inline loop so that the check itself can be driven with a
    PLANTED violation below. An oracle that is only ever pointed at code known to be
    clean cannot tell "no write flag is present" from "this check no longer works".
    """
    for arg in attempt:
        assert arg not in WRITE_FLAGS, f"{arg} is one of exiftool's writing options"
        # `-TAG=value` is how exiftool is told to WRITE a tag. A read never has one.
        assert "=" not in arg, f"{arg} assigns a tag value"


@pytest.mark.parametrize("variant", ["grid", "full"])
def test_no_frozen_argument_can_write_anything(variant):
    """The read-only pin spec 5.3's `FROZEN_ARGS` got in M2, for A7's third surface."""
    for attempt in thumbs.attempts(variant):
        assert_read_only(attempt)


@pytest.mark.parametrize(
    "planted",
    [
        ("-b", "-ThumbnailImage", "-overwrite_original"),
        ("-b", "-ThumbnailImage", "-delete_original"),
        ("-b", "-ThumbnailImage", "-tagsfromfile"),
        ("-b", "-ThumbnailImage", "-w"),
        ("-b", "-o", "copy.jpg"),
        # The other spelling: assigning a tag rather than naming a write option.
        ("-b", "-Orientation=1"),
    ],
)
def test_the_read_only_check_fires_on_a_planted_write_flag(planted):
    """The guard proven ENFORCING, one row per route in - `move_call_sites`' pattern.

    `WRITE_FLAGS` is the entire oracle for A7's third frozen surface, and emptying it
    leaves the test above passing for any arguments at all. This is what stops that
    being invisible.
    """
    with pytest.raises(AssertionError):
        assert_read_only(planted)


def test_the_variants_are_the_two_spec_8_2_names():
    assert thumbs.VARIANTS == ("grid", "full")


# --- the cache key -------------------------------------------------------------------

SHA = "a" * 64


def test_the_cache_filename_begins_with_the_sha256_and_nothing_else(tmp_path):
    """Amendment A2's second amendment, spelled out rather than computed.

    `verify/checks.py::_thumb_key` reads the LEADING 64 hex characters and spec 7.5
    check 7 reports anything else as belonging to no known file - so this is the
    property that keeps `pam verify` from calling the whole cache stale.
    """
    workspace = Workspace(root=tmp_path, archive_root=tmp_path / "photos")
    path = thumbs.cache_path(workspace, SHA, "grid")
    assert path.parent == workspace.thumbs_dir
    assert path.name.startswith(SHA)
    assert path.name == f"{SHA}.grid.{thumbs.EXTRACTOR_VERSION}.jpg"


@pytest.mark.parametrize("variant", ["grid", "full"])
def test_the_verify_checks_thumb_key_reads_the_cache_name_this_module_writes(tmp_path, variant):
    """The two halves of A2's contract, driven against each other end to end."""
    workspace = Workspace(root=tmp_path, archive_root=tmp_path / "photos")
    assert _thumb_key(thumbs.cache_path(workspace, SHA, variant).name) == SHA


def test_each_variant_gets_its_own_cache_entry(tmp_path):
    workspace = Workspace(root=tmp_path, archive_root=tmp_path / "photos")
    assert thumbs.cache_path(workspace, SHA, "grid") != thumbs.cache_path(
        workspace, SHA, "full"
    )


# --- the refusals, one operand at a time ---------------------------------------------


# Every refusal below points at the ONE archive file that really does have an embedded
# thumbnail, so the only thing stopping extraction is the operand under test. A row
# built from the same file with all three operands satisfied is the control.
def _row(live: Live, name: str, **overrides: Any) -> FileRow:
    return FileRow(
        id=live.file_ids[name],
        rel_path=name,
        filename=name,
        ext=name.rsplit(".", 1)[1],
        media_type=overrides.pop("media_type", "image"),
        size=len(ARCHIVE[name]),
        mtime_ns=0,
        sha256=overrides.pop("sha256", SHA),
        status="present",
        placeholder=overrides.pop("placeholder", False),
        companion_group_id=None,
    )


@needs_exiftool
def test_the_control_row_really_does_produce_a_thumbnail(live, empty_cache):
    """Without this, every refusal below could pass for the wrong reason."""
    got = thumbs.get_thumb(live.server.workspace, _row(live, WITH_THUMB), "grid")
    assert got is not None
    assert got.read_bytes() == EMBEDDED_THUMBNAIL


def test_a_cloud_placeholder_is_refused_without_touching_the_disk(live, empty_cache):
    """Vision R2: READING A PLACEHOLDER HYDRATES IT, so pam must not read one.

    The one refusal that is a safety rule rather than an optimisation, and the reason
    the placeholder check sits before the cache lookup as well as before the extraction.
    """
    row = _row(live, WITH_THUMB, placeholder=True)
    assert thumbs.get_thumb(live.server.workspace, row, "grid") is None
    assert list(empty_cache.iterdir()) == []


def test_a_file_with_no_sha256_is_refused(live, empty_cache):
    """The sha256 IS the cache key, so a file without one cannot be cached at all."""
    row = _row(live, WITH_THUMB, sha256=None)
    assert thumbs.get_thumb(live.server.workspace, row, "grid") is None
    assert list(empty_cache.iterdir()) == []


@pytest.mark.parametrize("media_type", ["video", "sidecar", "other"])
def test_every_non_image_media_type_is_refused(live, empty_cache, media_type):
    """Spec 8.2: every video, every sidecar, every `other`.

    Each row carries a real sha256 and is not a placeholder, so the media type is the
    only operand doing the refusing - the real `.MOV` and `.xmp` rows in the catalog
    have no sha256 either and would be refused twice over.
    """
    row = _row(live, WITH_THUMB, media_type=media_type)
    assert thumbs.get_thumb(live.server.workspace, row, "grid") is None
    assert list(empty_cache.iterdir()) == []


def test_a_source_that_is_no_longer_on_disk_is_none_and_not_an_exception(live, empty_cache):
    gone = dataclasses.replace(_row(live, WITH_THUMB), rel_path="no_such_file.JPG")
    assert thumbs.get_thumb(live.server.workspace, gone, "grid") is None
    assert list(empty_cache.iterdir()) == []


@needs_exiftool
@pytest.mark.parametrize("variant", ["grid", "full"])
def test_a_photo_with_no_embedded_preview_at_all_is_none(live, empty_cache, variant):
    """P11: still `None`, and now REMEMBERED as `None` - one zero-byte marker, no JPEG.

    This assertion used to be `iterdir() == []`. That was the defect HT-M7 measured, not
    a property: on a human test's library almost every promised thumbnail 404'd and every
    scroll past the same HEIC paid ~0.27s to re-learn it. The marker's NAME is asserted
    from the key spelled out here rather than from `empty_marker_path`, so a builder that
    stopped putting the sha256 first - which spec 7.5 check 7 reads - would fail here.
    """
    row = _row(live, NO_PREVIEW)
    assert thumbs.get_thumb(live.server.workspace, row, variant) is None
    marker = f"{row.sha256}.{variant}.{thumbs.EXTRACTOR_VERSION}.none"
    assert [path.name for path in empty_cache.iterdir()] == [marker]
    assert (empty_cache / marker).read_bytes() == b""


# --- a refusal reaches NEITHER exiftool NOR a cache entry it already has --------------

# Spec 8.2's never-extract list, written out as the operand each class flips on the ONE
# file that really does have an embedded thumbnail. Spelled from the spec's sentence
# ("every video, every sidecar, every `other`, every file with no sha256, and every
# cloud placeholder"), not from `thumbs.extractable`.
REFUSALS = {
    "cloud-placeholder": {"placeholder": True},
    "no-sha256": {"sha256": None},
    "video": {"media_type": "video"},
    "sidecar": {"media_type": "sidecar"},
    "other": {"media_type": "other"},
}


@pytest.fixture
def spied_run(monkeypatch):
    """Every one-shot exiftool call the extractor makes, recorded and then performed.

    The refusal tests below assert on what pam DID, not only on what it returned. A
    refusal that returns `None` after handing the file to exiftool is indistinguishable
    from a real refusal at the return value and at the cache - and on a cloud placeholder
    it is the difference between a glyph card and a hydrated file (vision R2).
    """
    calls: list[tuple[str, ...]] = []
    real = exiftool._run_binary

    def spy(command, args, source):
        calls.append(args)
        return real(command, args, source)

    monkeypatch.setattr(exiftool, "_run_binary", spy)
    return calls


@needs_exiftool
def test_the_exiftool_spy_sees_the_extraction_it_is_watching_for(live, empty_cache, spied_run):
    """The control. A spy that recorded nothing would pass every refusal row below."""
    row = _row(live, WITH_THUMB)
    assert thumbs.get_thumb(live.server.workspace, row, "grid") is not None
    assert spied_run == [("-b", "-ThumbnailImage")]


@pytest.mark.parametrize("overrides", REFUSALS.values(), ids=list(REFUSALS))
def test_a_refused_file_is_never_handed_to_exiftool_at_all(
    live, empty_cache, spied_run, overrides
):
    """Spec 8.2's refusals are checked BEFORE the extraction, not after it.

    `None` with an empty cache is not enough to prove a refusal: the read has already
    happened by then. For the placeholder row this is vision R2 itself - reading one
    hydrates it - and for the rest it is a review session that shells out once per card
    to throw the answer away.
    """
    row = _row(live, WITH_THUMB, **overrides)
    assert thumbs.get_thumb(live.server.workspace, row, "grid") is None
    assert spied_run == []
    assert list(empty_cache.iterdir()) == []


# The sha256 row cannot express this one: with no sha256 there is no cache path to
# pre-populate, so its refusal is the only thing that could ever be under test.
CACHEABLE_REFUSALS = {k: v for k, v in REFUSALS.items() if "sha256" not in v}


@needs_exiftool
@pytest.mark.parametrize("overrides", CACHEABLE_REFUSALS.values(), ids=list(CACHEABLE_REFUSALS))
def test_a_refused_file_is_not_served_the_cache_entry_it_already_has(
    live, empty_cache, overrides
):
    """The refusals sit above the cache lookup, which is what both docstrings claim.

    The entry is written by the REAL path first, so no filename is predicted here; then
    the same file comes back with one operand flipped and must be refused anyway. A file
    that was extractable when it was cached and is a cloud placeholder now is an ordinary
    day on OneDrive, not a contrived state.
    """
    assert thumbs.get_thumb(live.server.workspace, _row(live, WITH_THUMB), "grid") is not None
    assert len(list(empty_cache.iterdir())) == 1

    refused = _row(live, WITH_THUMB, **overrides)
    assert thumbs.get_thumb(live.server.workspace, refused, "grid") is None


# --- `extractable` and `thumbnailable` are two spellings of one rule ------------------

# Spec 8.2's never-extract list, as a truth table over the three operands, written from
# the spec's own sentence: an image, with a sha256, that is not a cloud placeholder.
MEDIA_TYPES = ("image", "video", "sidecar", "other")


def _review_item(media_type: str, has_sha256: bool, placeholder: bool) -> ReviewItem:
    return ReviewItem(
        id=1,
        file_id=1,
        old_rel_path="x.JPG",
        rel_dir="",
        old_filename="x.JPG",
        unit_key="u",
        generated_filename="y.JPG",
        new_filename="y.JPG",
        tier="high",
        trust_score=100,
        status="proposed",
        tokens=[],
        trust_reasons=[],
        filename="x.JPG",
        ext="JPG",
        media_type=media_type,
        placeholder=placeholder,
        has_sha256=has_sha256,
        orientation=None,
        event_id=None,
        event_label=None,
    )


@pytest.mark.parametrize("media_type", MEDIA_TYPES)
@pytest.mark.parametrize("has_sha256", [True, False])
@pytest.mark.parametrize("placeholder", [True, False])
def test_extractable_matches_spec_8_2s_own_sentence(media_type, has_sha256, placeholder):
    row = FileRow(
        id=1,
        rel_path="x.JPG",
        filename="x.JPG",
        ext="JPG",
        media_type=media_type,
        size=1,
        mtime_ns=0,
        sha256=SHA if has_sha256 else None,
        status="present",
        placeholder=placeholder,
        companion_group_id=None,
    )
    expected = media_type == "image" and has_sha256 and not placeholder
    assert thumbs.extractable(row) is expected


@pytest.mark.parametrize("media_type", MEDIA_TYPES)
@pytest.mark.parametrize("has_sha256", [True, False])
@pytest.mark.parametrize("placeholder", [True, False])
def test_the_cheap_half_and_the_real_half_never_disagree(media_type, has_sha256, placeholder):
    """`has_thumb` on a card and `extractable` in the extractor are one rule, twice.

    Two implementations over two row types, checked against each other rather than one
    being derived from the other: a card that says `has_thumb: true` for a file the
    extractor would refuse would spend a request per card to be told 404, and a card
    that said `false` for a file that has a preview would never ask.
    """
    row = FileRow(
        id=1,
        rel_path="x.JPG",
        filename="x.JPG",
        ext="JPG",
        media_type=media_type,
        size=1,
        mtime_ns=0,
        sha256=SHA if has_sha256 else None,
        status="present",
        placeholder=placeholder,
        companion_group_id=None,
    )
    item = _review_item(media_type, has_sha256, placeholder)
    assert thumbs.extractable(row) is item.thumbnailable


# --- over the wire -------------------------------------------------------------------


@needs_exiftool
def test_the_first_request_extracts_and_the_second_never_touches_the_archive(
    live, empty_cache, restored_archive
):
    """Extract-on-miss and cache-hit, proven as one pair (the plan's own design).

    The source is DELETED between the two requests. A second 200 is therefore only
    possible if the first one wrote the cache and the second one read it - there is no
    longer a file in the archive to extract from.
    """
    file_id = live.file_ids[WITH_THUMB]
    first = fetch(live, f"/thumb/{file_id}", variant="grid")
    assert first.status == 200
    assert first.content_type == "image/jpeg"
    assert first.body == EMBEDDED_THUMBNAIL

    cached = list(empty_cache.iterdir())
    assert len(cached) == 1
    assert cached[0].read_bytes() == EMBEDDED_THUMBNAIL

    (live.root / WITH_THUMB).unlink()
    second = fetch(live, f"/thumb/{file_id}", variant="grid")
    assert second.status == 200
    assert second.body == EMBEDDED_THUMBNAIL


@needs_exiftool
def test_the_cached_file_is_named_the_way_amendment_a2_requires(live, empty_cache):
    """Spelled from A2's rule, not from `cache_path` - the whole point of the rule."""
    conn = connect(live.db_path)
    try:
        row = repo.file_by_id(conn, live.file_ids[WITH_THUMB])
    finally:
        conn.close()
    assert row is not None and row.sha256 is not None

    assert fetch(live, f"/thumb/{row.id}", variant="grid").status == 200
    names = [path.name for path in empty_cache.iterdir()]
    assert names == [f"{row.sha256}.grid.{thumbs.EXTRACTOR_VERSION}.jpg"]


@needs_exiftool
def test_bumping_the_extractor_version_stops_the_old_entry_being_served(
    live, empty_cache, restored_archive, monkeypatch
):
    """The versioned key, proven by invalidation rather than by string comparison.

    The old entry is written, the source is deleted, and the version is bumped. If the
    version were not part of the key the old bytes would still be served; because it is,
    there is nothing to serve and no source to re-extract from, so the answer is None.
    """
    row = _row(live, WITH_THUMB, sha256=_sha256_of(live, WITH_THUMB))
    first = thumbs.get_thumb(live.server.workspace, row, "grid")
    assert first is not None and first.is_file()

    (live.root / WITH_THUMB).unlink()
    monkeypatch.setattr(thumbs, "EXTRACTOR_VERSION", "e999")
    assert thumbs.get_thumb(live.server.workspace, row, "grid") is None
    assert first.is_file(), "the old entry is not served, and is not deleted either"


def _sha256_of(live: Live, name: str) -> str:
    conn = connect(live.db_path)
    try:
        row = repo.file_by_id(conn, live.file_ids[name])
    finally:
        conn.close()
    assert row is not None and row.sha256 is not None
    return row.sha256


@needs_exiftool
def test_a_preview_too_large_for_the_grid_is_a_glyph_in_the_grid_and_an_image_in_the_overlay(
    live, empty_cache
):
    """Amendment A2's first amendment, end to end over HTTP.

    `LARGEONLY.JPG` has a preview and no thumbnail. The grid must 404 to a glyph card
    rather than shipping the larger image into a ~96px cell, and the overlay must show
    the real picture - which also proves the chain's SECOND link works, not just its
    first.
    """
    file_id = live.file_ids[LARGE_ONLY]
    assert fetch(live, f"/thumb/{file_id}", variant="grid").status == 404
    overlay = fetch(live, f"/thumb/{file_id}", variant="full")
    assert overlay.status == 200
    assert overlay.content_type == "image/jpeg"
    assert overlay.body == EMBEDDED_PREVIEW


@needs_exiftool
@pytest.mark.parametrize("variant", ["grid", "full"])
def test_a_photo_with_nothing_embedded_is_a_404_in_both_variants(live, variant):
    reply = fetch(live, f"/thumb/{live.file_ids[NO_PREVIEW]}", variant=variant)
    assert reply.status == 404
    assert variant in message(reply)


@pytest.mark.parametrize("name", [CLIP, SIDECAR, OTHER])
def test_the_never_extracted_classes_are_404_over_http(live, name):
    """A video, a sidecar and an `other`, as the catalog really recorded them."""
    assert fetch(live, f"/thumb/{live.file_ids[name]}", variant="grid").status == 404


@needs_exiftool
def test_a_cloud_placeholder_is_404_over_http(live, empty_cache):
    """The placeholder flag is on the files row, so this drives the real route.

    Set and restored inside the test with the suite's own SQL: no repo function writes
    `placeholder` outside the scanner, and none should.
    """
    file_id = live.file_ids[WITH_THUMB]
    assert fetch(live, f"/thumb/{file_id}", variant="grid").status == 200
    _clear(live.thumbs_dir)
    conn = connect(live.db_path)
    try:
        conn.execute("UPDATE files SET placeholder = 1 WHERE id = ?", (file_id,))
        conn.commit()
        assert fetch(live, f"/thumb/{file_id}", variant="grid").status == 404
        assert list(live.thumbs_dir.iterdir()) == []
    finally:
        conn.execute("UPDATE files SET placeholder = 0 WHERE id = ?", (file_id,))
        conn.commit()
        conn.close()


@needs_exiftool
def test_the_default_variant_is_the_grid(live, empty_cache):
    assert fetch(live, f"/thumb/{live.file_ids[WITH_THUMB]}").status == 200
    names = [path.name for path in empty_cache.iterdir()]
    assert len(names) == 1
    assert ".grid." in names[0]


def test_an_unknown_variant_is_a_400_naming_the_parameter(live):
    reply = fetch(live, f"/thumb/{live.file_ids[WITH_THUMB]}", variant="huge")
    assert reply.status == 400
    assert message(reply).startswith("variant:")


def test_an_unknown_file_id_is_a_404_with_a_sentence(live):
    reply = fetch(live, "/thumb/999999", variant="grid")
    assert reply.status == 404
    assert "999999" in message(reply)


def test_a_file_id_that_is_not_a_number_is_a_404_with_a_sentence(live):
    reply = fetch(live, "/thumb/not-a-number", variant="grid")
    assert reply.status == 404
    assert "not-a-number" in message(reply)


def test_the_thumb_route_needs_the_session_token_like_every_other_route(live):
    """Spec 8.2's token gate: any other path is 404 with an EMPTY body."""
    conn = HTTPConnection(live.server.host, live.server.port, timeout=20)
    try:
        conn.request("GET", f"/t/wrong/api/thumb/{live.file_ids[WITH_THUMB]}")
        response = conn.getresponse()
        body = response.read()
    finally:
        conn.close()
    assert response.status == 404
    assert body == b""


@needs_exiftool
def test_the_extraction_runs_outside_the_db_lock(live, empty_cache, monkeypatch):
    """Plan clarification 5: the row lookup is inside the lock, the subprocess is not."""
    seen: dict[str, bool] = {}
    real = thumbs.get_thumb

    def spy(workspace, file_row, variant):
        seen["locked"] = live.server.db_lock.locked()
        return real(workspace, file_row, variant)

    monkeypatch.setattr(thumbs, "get_thumb", spy)
    assert fetch(live, f"/thumb/{live.file_ids[WITH_THUMB]}", variant="grid").status == 200
    assert seen["locked"] is False


# --- the two exiftool failure modes, which are deliberately NOT the same answer -------


@needs_exiftool
def test_a_binary_that_fails_at_the_call_is_a_glyph_card_and_not_an_exception(
    live, empty_cache, monkeypatch
):
    """One file could not be read. That is a 404 and a glyph, exactly like a photo whose
    camera embedded nothing - `_run` says so and nothing held it to it until now.

    A review session must not fall over because one card's preview could not be
    produced: the other 899 cards are still the user's afternoon.
    """

    def vanished(*args, **kwargs):
        raise OSError(2, "No such file or directory")

    monkeypatch.setattr(exiftool.subprocess, "run", vanished)
    assert thumbs.get_thumb(live.server.workspace, _row(live, WITH_THUMB), "grid") is None
    assert list(empty_cache.iterdir()) == []


@needs_exiftool
def test_an_extraction_that_hangs_is_a_glyph_card_rather_than_a_dead_thread(
    live, empty_cache, monkeypatch
):
    """Task 10 review Q4. A hung exiftool used to pin its worker thread for ever.

    `ThreadingHTTPServer` never answers that request, so the card spins with no error, no
    log line and no way out but Ctrl+C - and it is reachable rather than theoretical, on a
    network-mounted or OneDrive-throttled archive. An extraction that will not finish is a
    file with no image pam can serve, which is what the glyph card already says, so it
    joins `OSError` rather than earning a fifth outcome of its own.
    """

    def hangs(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="exiftool", timeout=exiftool.EXTRACT_TIMEOUT_S)

    monkeypatch.setattr(exiftool.subprocess, "run", hangs)
    assert thumbs.get_thumb(live.server.workspace, _row(live, WITH_THUMB), "grid") is None
    assert list(empty_cache.iterdir()) == []


@needs_exiftool
def test_every_extraction_call_carries_the_timeout(live, empty_cache, monkeypatch):
    """The bound is ARMED, not merely defined. Deleting `timeout=` from the call leaves the
    test above green - it patches `run` and never reaches the real argument list - so the
    hang would come back with its own regression test still passing.

    Asserted on EVERY attempt rather than the first: `full` runs the three-tag chain, and a
    timeout applied to only the first link still hangs on a file whose preview is reached
    by the second. Spelled from spec 6.2's habit of pinning the arithmetic, not the value:
    the assertion is that the constant arrives, whatever the owner retunes it to.
    """
    seen: list[object] = []

    def record(*args, **kwargs):
        seen.append(kwargs.get("timeout", "NO TIMEOUT PASSED"))
        raise OSError(2, "stop here - the argument list is what is under test")

    monkeypatch.setattr(exiftool.subprocess, "run", record)
    assert thumbs.get_thumb(live.server.workspace, _row(live, WITH_THUMB), "full") is None
    assert len(seen) == len(thumbs.attempts("full")), "not every attempt was bounded"
    assert seen == [exiftool.EXTRACT_TIMEOUT_S] * len(seen)


def test_a_missing_exiftool_is_a_500_that_names_pam_doctor(live, empty_cache, monkeypatch):
    """The tool is GONE. That is not one file's glyph - it is a setup problem, and spec
    13 says a refusal names what to do about it.

    The asymmetry with the test above is the point, and it is the reason both are here:
    a 404 for this would draw 900 glyph cards and never tell the user that `pam doctor`
    is one command away from fixing all of them.
    """

    def gone():
        raise ExifToolError(
            "exiftool was not found on PATH, and reading photo metadata needs it. "
            "Run: pam doctor"
        )

    monkeypatch.setattr(exiftool, "default_command", gone)
    reply = fetch(live, f"/thumb/{live.file_ids[WITH_THUMB]}", variant="grid")
    assert reply.status == 500
    assert "pam doctor" in message(reply)
    assert list(empty_cache.iterdir()) == []


# --- P11: the negative cache (M8 Task 6 Step 7) --------------------------------------
#
# HT-M7 measured the defect on a human test's library: most items promised a thumbnail, few
# delivered one, 170 returned 404, and each of those re-ran a ~0.27s extraction on every
# scroll past the same card. Design 8.2 rev 14 / A9 records why that is the ORDINARY
# case rather than an edge one - only a few percent of items can show a thumbnail at all.
#
# EVERY TEST BELOW WATCHES THE EXIFTOOL CALL, never only the return value. "Did not
# re-extract" and "re-extracted and still returned None" are the same 404 and the same
# empty response, and only the spy can tell them apart (M697's lesson).


@needs_exiftool
@pytest.mark.parametrize("variant", ["grid", "full"])
def test_a_known_empty_extraction_is_not_run_a_second_time(
    live, empty_cache, spied_run, variant
):
    """P11's fix, stated as the dangerous call: exiftool runs ONCE for this file."""
    row = _row(live, NO_PREVIEW)
    assert thumbs.get_thumb(live.server.workspace, row, variant) is None
    first = list(spied_run)
    assert first, "the control: the first request must really have run exiftool"
    del spied_run[:]
    assert thumbs.get_thumb(live.server.workspace, row, variant) is None
    assert spied_run == [], "the second request re-ran the extraction P11 is about"


@needs_exiftool
def test_the_negative_cache_is_per_variant_and_never_answers_for_the_other(
    live, empty_cache, spied_run
):
    """A2 amendment 1: the grid asks for `-ThumbnailImage` alone and the overlay asks for
    the bigger preview, so a file that has no grid thumbnail may still have a full one.
    A marker that answered for both variants would turn `LARGEONLY.JPG` - which HAS a
    preview - into a permanent glyph in the overlay after one grid miss."""
    row = _row(live, LARGE_ONLY)
    assert thumbs.get_thumb(live.server.workspace, row, "grid") is None
    del spied_run[:]
    assert thumbs.get_thumb(live.server.workspace, row, "full") is not None
    assert spied_run, "the grid's marker suppressed the overlay's own extraction"


def test_a_placeholder_is_given_no_negative_marker_either(live, empty_cache, spied_run):
    """Vision R2. A marker is a claim about bytes, and pam has not read a placeholder's -
    reading them is what hydrates it. The refusal must reach NEITHER exiftool nor the
    cache, positive or negative."""
    row = _row(live, WITH_THUMB, placeholder=True)
    assert thumbs.get_thumb(live.server.workspace, row, "grid") is None
    assert spied_run == []
    assert list(empty_cache.iterdir()) == []


def test_a_source_that_is_no_longer_on_disk_is_asked_about_again(live, empty_cache, spied_run):
    """A missing file HEALS - spec 7.5 check 1 reports it and the user puts it back. A
    marker written now would answer for the file after it returned, which is exactly the
    "ask again later" reading `get_thumb`'s docstring says the cache never carries."""
    gone = dataclasses.replace(_row(live, WITH_THUMB), rel_path="no_such_file.JPG")
    assert thumbs.get_thumb(live.server.workspace, gone, "grid") is None
    assert list(empty_cache.iterdir()) == []
    assert spied_run == [], "a missing source must not reach exiftool at all"


@pytest.mark.parametrize(
    "failure",
    [
        OSError("exiftool went away between default_command() and here"),
        subprocess.TimeoutExpired(cmd="exiftool", timeout=exiftool.EXTRACT_TIMEOUT_S),
    ],
    ids=["oserror", "timeout"],
)
def test_an_extraction_that_never_COMPLETED_is_not_remembered_as_no_preview(
    live, empty_cache, monkeypatch, failure
):
    """A CACHE ENTRY IS FOREVER, and neither of these learned anything about the file.

    Both still draw the glyph card - a review session must not 500 because a thumbnail
    could not be produced - so the return value cannot tell them from a real empty
    extraction. Remembering one would permanently suppress a preview the file really has
    after a single throttled read of a OneDrive archive, and HT-M7 ran against a folder
    inside OneDrive. `WITH_THUMB` is the file that DOES have a preview, so the second
    call proves the suppression would have been wrong as well as unsound.
    """

    def fails(*args, **kwargs):
        raise failure

    monkeypatch.setattr(exiftool.subprocess, "run", fails)
    assert thumbs.get_thumb(live.server.workspace, _row(live, WITH_THUMB), "grid") is None
    assert list(empty_cache.iterdir()) == []
    monkeypatch.undo()
    assert thumbs.get_thumb(live.server.workspace, _row(live, WITH_THUMB), "grid")


@needs_exiftool
def test_a_marker_written_by_another_extractor_version_is_ignored(
    live, empty_cache, spied_run, monkeypatch
):
    """`EXTRACTOR_VERSION`'s contract, extended to the negative half: bump it and the old
    verdicts stop being consulted, exactly as the old bytes stop being served. A marker
    that outlived the chain that produced it would be a permanent glyph card for a file a
    new chain can read."""
    row = _row(live, NO_PREVIEW)
    assert thumbs.get_thumb(live.server.workspace, row, "grid") is None
    assert spied_run
    del spied_run[:]
    monkeypatch.setattr(thumbs, "EXTRACTOR_VERSION", thumbs.EXTRACTOR_VERSION + "-next")
    assert thumbs.get_thumb(live.server.workspace, row, "grid") is None
    assert spied_run, "the old version's marker answered for the new chain"


@needs_exiftool
def test_pam_verify_check_7_reads_a_negative_marker_as_a_file_it_knows(live, empty_cache):
    """Amendment A2's "the filename begins with the sha256 and nothing else", which is
    normative BECAUSE of `verify/checks.py::_thumb_key`.

    Getting this wrong would make `pam verify` report the WHOLE thumbnail cache as stale
    on a perfectly healthy archive - a worse defect than the one P11 fixes, and one that
    arrives as a warning the user cannot clear. The control below is a file named the
    wrong way, which must still be reported: a check that passed over both would prove
    nothing about the marker.
    """
    conn = connect(live.db_path)
    try:
        # The REAL catalog row, sha256 included - `_row` builds a stand-in whose hash
        # pam legitimately does not know, which is the very thing check 7 reports.
        real = next(r for r in repo.all_files(conn).values() if r.rel_path == NO_PREVIEW)
        assert thumbs.get_thumb(live.server.workspace, real, "grid") is None
        marker = next(empty_cache.iterdir())
        assert marker.name.startswith(real.sha256 or "")
        ctx = checks.CheckContext(conn=conn, archive_root=live.root, thumbs_dir=empty_cache)
        assert checks.check_7_thumbnail_cache(ctx).count == 0, marker.name
        (empty_cache / "not-a-sha256.grid.e1.none").write_bytes(b"")
        result = checks.check_7_thumbnail_cache(ctx)
        assert result.count == 1
        assert "not-a-sha256.grid.e1.none" in " ".join(result.details)
    finally:
        conn.close()
