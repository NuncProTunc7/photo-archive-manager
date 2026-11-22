"""The review card's look-alikes, over real loopback HTTP (M13 Task 10, amendment H3).

Each item in `/api/plan/{id}/items` carries `look_alikes` - up to five `rel_path`s of OTHER
plan items in its look-alike group, never its own companion unit's - and `look_alikes_more`.
Every archive below is scanned by the real scanner, fingerprinted by the real `pam enrich
phash`, planned by the real `pam plan` and served by a real `ReviewServer`. Every expected
list is spelled out by hand from H3's sentence, never read back from `pam/webui/lookalikes.py`.

The pictures are `tests/test_report_duplicates.py`'s: the tiny library's 32x32 pattern, which
keeps its fingerprint when it is resized and re-encoded, and a RAW whose embedded preview IS
its JPEG companion (one unit, one picture - exiftool reads the preview, so absent exiftool the
precondition below is RED, never skipped).
"""

import io
import json
import logging
import sqlite3
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

from archives import FAKE_COMMAND, put
from PIL import Image

from pam.enrich.command import run_enrich
from pam.naming.planner import run_plan
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan
from pam.webui.server import ReviewServer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import build_tiny_library  # noqa: E402

LIBRARY = Path(__file__).resolve().parent / "tiny_library"
BASE = (LIBRARY / "phash_pattern_base.png").read_bytes()
FAR = (LIBRARY / "phash_pattern_far.png").read_bytes()  # 64 bits from BASE
RAW = (LIBRARY / "raw_preview.ARW").read_bytes()
RAW_PREVIEW = build_tiny_library.raw_preview_payloads("raw_preview.ARW")["PreviewImage"]


def _resized_jpeg(payload: bytes, factor: int) -> bytes:
    """The same picture at another size and in another format: new bytes, one shot."""
    with Image.open(io.BytesIO(payload)) as image:
        rgb = image.convert("RGB")
    larger = rgb.resize((rgb.width * factor, rgb.height * factor))
    out = io.BytesIO()
    larger.save(out, "JPEG", quality=90)
    return out.getvalue()


def _archive(tmp_path: Path, files: dict[str, bytes]) -> tuple[Path, int]:
    """Scan with the fake exiftool, fingerprint with the real enricher, plan. -> (root, plan)."""
    root = tmp_path / "photos"
    root.mkdir(parents=True)
    for name, payload in files.items():
        put(root, name, content=payload)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    run_enrich("phash", root)
    planned = run_plan(root)
    assert planned.plan_id is not None
    return root, planned.plan_id


def _db(root: Path) -> Path:
    ws = workspace_if_exists(root)
    assert ws is not None
    return ws.db_path


def _fingerprinted(root: Path) -> set[str]:
    """Which files have a live phash fact - the precondition, read through the suite's SQL."""
    conn = sqlite3.connect(_db(root))
    try:
        rows = conn.execute(
            "SELECT f.rel_path FROM files f JOIN inferred_facts i ON i.sha256 = f.sha256"
            " WHERE i.kind = 'phash' AND i.superseded_by IS NULL"
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


@contextmanager
def _serving(root: Path, plan_id: int) -> Iterator[ReviewServer]:
    ws = workspace_if_exists(root)
    assert ws is not None
    logger = logging.getLogger("pam.tests.webui_lookalikes")
    logger.propagate = False
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    server = ReviewServer(workspace=ws, plan_id=plan_id, logger=logger)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=20)


def _cards(server: ReviewServer, plan_id: int) -> dict[str, dict[str, Any]]:
    """Every card of the plan, by its on-disk path, through one real GET."""
    url = f"/t/{server.token}/api/plan/{plan_id}/items?limit=200"
    conn = HTTPConnection(server.host, server.port, timeout=20)
    try:
        conn.request("GET", url)
        response = conn.getresponse()
        raw = response.read()
        assert response.status == 200, raw
        body = json.loads(raw)
    finally:
        conn.close()
    assert body["total"] == len(body["items"]), "one page holds the whole plan"
    return {card["old_rel_path"]: card for card in body["items"]}


def _alikes(cards: dict[str, dict[str, Any]]) -> dict[str, tuple[list[str], int]]:
    return {path: (c["look_alikes"], c["look_alikes_more"]) for path, c in cards.items()}


# Two look-alike singles, one companion pair that looks like neither, and one photo that
# looks like nothing. `IMG_7.ARW`'s embedded preview IS `IMG_7.JPG`, so the pair is one
# content-alike unit: each member would name the other if own-unit members counted.
SINGLES_AND_A_PAIR = {
    "base.png": BASE,
    "base_large.JPG": _resized_jpeg(BASE, 2),
    "IMG_7.ARW": RAW,
    "IMG_7.JPG": RAW_PREVIEW,
    "far.png": FAR,
}


def test_each_single_names_the_other_and_the_pair_names_neither_each_other_nor_them(tmp_path):
    root, plan_id = _archive(tmp_path, SINGLES_AND_A_PAIR)
    assert _fingerprinted(root) == set(SINGLES_AND_A_PAIR)

    with _serving(root, plan_id) as server:
        cards = _cards(server, plan_id)

    assert cards["IMG_7.ARW"]["unit_key"] == cards["IMG_7.JPG"]["unit_key"], "one unit"
    assert _alikes(cards) == {
        "base.png": (["base_large.JPG"], 0),
        "base_large.JPG": (["base.png"], 0),
        "IMG_7.ARW": ([], 0),
        "IMG_7.JPG": ([], 0),
        "far.png": ([], 0),
    }


def test_a_pair_that_looks_like_another_photo_names_it_and_still_not_each_other(tmp_path):
    """The other half of "unless they look alike": the unit rule removes the unit's OWN
    members, never a photo outside it - and the photo outside names both members."""
    files = {
        "IMG_7.ARW": RAW,
        "IMG_7.JPG": RAW_PREVIEW,
        "print.JPG": _resized_jpeg(RAW_PREVIEW, 2),
    }
    root, plan_id = _archive(tmp_path, files)
    assert _fingerprinted(root) == set(files)

    with _serving(root, plan_id) as server:
        cards = _cards(server, plan_id)

    assert _alikes(cards) == {
        "IMG_7.ARW": (["print.JPG"], 0),
        "IMG_7.JPG": (["print.JPG"], 0),
        "print.JPG": (["IMG_7.ARW", "IMG_7.JPG"], 0),
    }


def test_at_most_five_paths_are_listed_and_the_rest_are_counted(tmp_path):
    """Eight look-alikes: each card has seven others - five listed in path order, two more."""
    files = {"p1.png": BASE} | {f"p{k}.JPG": _resized_jpeg(BASE, k) for k in range(2, 9)}
    root, plan_id = _archive(tmp_path, files)
    assert _fingerprinted(root) == set(files)

    with _serving(root, plan_id) as server:
        cards = _cards(server, plan_id)

    assert _alikes(cards)["p1.png"] == (["p2.JPG", "p3.JPG", "p4.JPG", "p5.JPG", "p6.JPG"], 2)
    assert _alikes(cards)["p8.JPG"] == (["p1.png", "p2.JPG", "p3.JPG", "p4.JPG", "p5.JPG"], 2)
    assert _alikes(cards)["p4.JPG"] == (["p1.png", "p2.JPG", "p3.JPG", "p5.JPG", "p6.JPG"], 2)


def test_a_placeholder_item_is_neither_listed_nor_given_look_alikes(tmp_path):
    """A file that became a cloud placeholder after it was planned keeps its fingerprint;
    H2's "present, non-placeholder files" applies to the card as to the report."""
    files = {"base.png": BASE, "base_large.JPG": _resized_jpeg(BASE, 2)}
    root, plan_id = _archive(tmp_path, files)
    conn = sqlite3.connect(_db(root))
    try:
        with conn:
            changed = conn.execute(
                "UPDATE files SET placeholder = 1 WHERE rel_path = 'base_large.JPG'"
            ).rowcount
    finally:
        conn.close()
    assert changed == 1

    with _serving(root, plan_id) as server:
        cards = _cards(server, plan_id)

    assert _alikes(cards) == {"base.png": ([], 0), "base_large.JPG": ([], 0)}


def test_the_look_alikes_are_computed_once_per_server(tmp_path):
    """The server holds the workspace lock, so the answer is computed on the first request
    and remembered: a fingerprint withdrawn behind its back (which no pam command can do
    while it runs) does not change the second answer. A NEW server reads afresh."""
    files = {"base.png": BASE, "base_large.JPG": _resized_jpeg(BASE, 2)}
    root, plan_id = _archive(tmp_path, files)
    expected = {"base.png": (["base_large.JPG"], 0), "base_large.JPG": (["base.png"], 0)}

    with _serving(root, plan_id) as server:
        assert _alikes(_cards(server, plan_id)) == expected
        conn = sqlite3.connect(_db(root))
        try:
            with conn:
                conn.execute("DELETE FROM inferred_facts WHERE kind = 'phash'")
        finally:
            conn.close()
        assert _alikes(_cards(server, plan_id)) == expected

    with _serving(root, plan_id) as server:
        assert _alikes(_cards(server, plan_id)) == {
            "base.png": ([], 0),
            "base_large.JPG": ([], 0),
        }


# --- Task 10 review: closing tests (M13 review of H3) ---------------------------------------


def _set_file(root: Path, rel_path: str, column: str, value: object) -> None:
    conn = sqlite3.connect(_db(root))
    try:
        with conn:
            changed = conn.execute(
                f"UPDATE files SET {column} = ? WHERE rel_path = ?", (value, rel_path)
            ).rowcount
    finally:
        conn.close()
    assert changed == 1


def test_a_placeholder_whose_bytes_a_present_file_still_holds_is_not_listed(tmp_path):
    """The card's OWN placeholder filter, not the repo's: `copy.png` is byte-identical to
    `base.png`, so its content keeps a live fingerprint through a present file, and only
    the item-level rule can keep the placeholder off both cards (H2/H3, vision R2)."""
    files = {"base.png": BASE, "copy.png": BASE, "large.JPG": _resized_jpeg(BASE, 2)}
    root, plan_id = _archive(tmp_path, files)
    _set_file(root, "copy.png", "placeholder", 1)

    with _serving(root, plan_id) as server:
        cards = _cards(server, plan_id)

    assert _alikes(cards) == {
        "base.png": (["large.JPG"], 0),
        "large.JPG": (["base.png"], 0),
        "copy.png": ([], 0),
    }


def test_a_missing_file_whose_bytes_a_present_file_still_holds_is_not_listed(tmp_path):
    """The same corner for `status`: H2's "present" files, decided per ITEM."""
    files = {"base.png": BASE, "copy.png": BASE, "large.JPG": _resized_jpeg(BASE, 2)}
    root, plan_id = _archive(tmp_path, files)
    _set_file(root, "copy.png", "status", "missing")

    with _serving(root, plan_id) as server:
        cards = _cards(server, plan_id)

    assert _alikes(cards) == {
        "base.png": (["large.JPG"], 0),
        "large.JPG": (["base.png"], 0),
        "copy.png": ([], 0),
    }


def test_one_server_answers_each_plan_for_that_plan(tmp_path):
    """The memory is per server AND plan: a second plan's items have other ids, so an
    answer remembered for the first plan alone would leave every card of the second bare."""
    files = {"base.png": BASE, "base_large.JPG": _resized_jpeg(BASE, 2)}
    root, first = _archive(tmp_path, files)
    second = run_plan(root).plan_id
    assert second is not None and second != first
    expected = {"base.png": (["base_large.JPG"], 0), "base_large.JPG": (["base.png"], 0)}

    with _serving(root, second) as server:
        assert _alikes(_cards(server, first)) == expected
        assert _alikes(_cards(server, second)) == expected


def test_the_archive_s_configured_distance_decides_the_groups(tmp_path):
    """`[enrich.phash] near_dupe_distance`, as the report reads it (H2): at 64 every pair
    qualifies, so two photos 64 bits apart - strangers at the default 8 - name each other."""
    files = {"base.png": BASE, "far.png": FAR}
    root, plan_id = _archive(tmp_path, files)
    ws = workspace_if_exists(root)
    assert ws is not None
    ws.config_path.write_text("[enrich.phash]\nnear_dupe_distance = 64\n", encoding="utf-8")

    with _serving(root, plan_id) as server:
        cards = _cards(server, plan_id)

    assert _alikes(cards) == {"base.png": (["far.png"], 0), "far.png": (["base.png"], 0)}
