"""M12 Task 11 (P12): `attention`'s equivalence obligation, as a table built through `pam plan`.

Design 8.2 rev 27: *`attention` is tier `low` OR no date token OR any token truncated OR the date
token's `source_table` is `inferred_facts`* - and "the per-flag-class equivalence is a
hand-written table test ... spelled from 6.4 and 6.1, with a test that every
`consistency.RULES` entry has a row". UX 5a rev 13: a date read off the picture joins the set,
and it HIGHLIGHTS WITHOUT BLOCKING - `Approve High+Medium` still reaches a `medium` OCR date.

THE TABLE IS SPELLED FROM THE SPEC, NEVER FROM PRODUCTION. Each row names a flag class (design
6.4's three rows) or a date source (design 6.1's hierarchy, plus rev 27's OCR tiers), builds the
case with a real `pam scan` + `pam plan`, and states LITERALLY what the item must be: its
`attention`, its tier, the date token's `source_table`, whether any token was truncated, and -
for a flag row - the sentence 6.4 prints, which is the witness that the row exercises the class
it names rather than reaching `attention` some other way. Nothing here calls
`consistency.RULES`' rules, `resolve_date` or `ReviewItem.attention`'s definition to compute an
expectation; the property is only ever the thing OBSERVED. The flag-class names are spelled in
this file, and `test_every_consistency_rule_has_a_row` compares `consistency.RULES` against them
- a rule added without a row fails, and so does a row whose class stopped existing.

Two rows exist for the mutation gate rather than for the plan's table, and each says why:
- `planner` - the only reachable date token that is neither `capture` nor `inferred_facts`
  (a primary whose `capture` row is gone; design 6.1 rev 6 attributes it to `planner`). It
  is built because Step 5 asks for the row that would kill `source_table != "capture"`.
  MEASURED WHILE BUILDING IT: such a token's value is always empty (`capture_dt` lives on the
  `capture` row), so the name says `undated` and the item is already attention by the second
  operand. The row pins that - it cannot, on its own, tell the two readings apart.
- `scene from inferred_facts, exif date` - an `inferred_facts` token that is NOT the date, on
  an item nothing else selects. It is the negative control for the operand's
  `resolved_from == "date"` half: "ANY token read by an enricher" would wrongly select it.
"""

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from http.client import HTTPConnection

import pytest
from archives import FACES_FLOOR, FAKE_COMMAND

from pam.catalog import repo
from pam.catalog.db import connect
from pam.naming import consistency
from pam.naming.planner import run_plan
from pam.naming.sanitize import MAX_FULL_PATH
from pam.paths import workspace_for, workspace_if_exists
from pam.scan.runner import run_scan
from pam.webui.server import ReviewServer

MTIME = datetime(2024, 5, 1, 12, 0, 0)
SCANS = '[enrich.ocr]\nfolders = ["Scans"]\n'
OCR_MODEL = "ppocr-v4"  # design 8.1 rev 28 (Q2)
DATED = "{date}_{seq}"
SCENE = "{date}_{scene}_{seq}"
NO_DATE = "{orig}_{seq}"  # no `{date}` slot at all; `{orig}` alone is already conforming

# Design 6.4's three rows, by the names `consistency.RULES` gives them. SPELLED HERE.
VIDEO_AMBIGUOUS = "_video_date_ambiguous"
TRUNCATED = "_truncated_token"
CAPTION_CONTRADICTS = "_caption_contradicts_geocode"
FLAG_CLASSES = (VIDEO_AMBIGUOUS, TRUNCATED, CAPTION_CONTRADICTS)

# Design 6.4's sentences, the witness each flag row carries.
VIDEO_REASON = (
    "Video time is recorded in UTC and the old filename suggests a different day - "
    "please check this one."
)
TRUNCATED_REASON = "Name shortened to fit the Windows path limit."
CAPTION_REASON = (
    "The AI scene guess mentions Paris, but this photo's location reads Tokyo - "
    "please check this one."
)


# --------------------------------------------------------------------------- the archive


def _conn(root):
    conn = connect(workspace_for(root).db_path)
    conn.row_factory = sqlite3.Row
    return conn


def build(tmp_path, files, *, deep=None):
    """`{rel_path: tags}`; every file's bytes unique, every mtime `MTIME`, then `pam scan`.

    `deep` names one file to place in a directory long enough that design 6.2 rule 5's budget
    leaves `deep[1]` characters for its stem.
    """
    root = tmp_path / "photos"
    root.mkdir(parents=True)
    placed = dict(files)
    if deep is not None:
        name, budget, tags = deep
        pad = MAX_FULL_PATH - len(str(root)) - len(name.rpartition(".")[2]) - 1 - budget
        assert 0 < pad < 250, f"the temp path ({len(str(root))} chars) cannot host the budget"
        placed[f"{'d' * pad}/{name}"] = tags
    for rel, tags in placed.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"the only copy of {rel}".encode())
        (root / f"{rel}.tags.json").write_text(
            json.dumps({**tags, "PamTestSidecar": rel}), encoding="utf-8"
        )
        os.utime(path, (MTIME.timestamp(), MTIME.timestamp()))
    run_scan(root, exiftool_command=FAKE_COMMAND)
    workspace_for(root).config_path.write_text(SCANS, encoding="utf-8")
    return root


def add_fact(root, rel, kind, value, model):
    """One live `inferred_facts` row, with the SUITE'S OWN SQL."""
    conn = _conn(root)
    try:
        sha = conn.execute("SELECT sha256 FROM files WHERE rel_path = ?", (rel,)).fetchone()[0]
        conn.execute(
            "INSERT INTO inferred_facts (sha256, kind, value_json, confidence, model_name,"
            " model_version, generated_at) VALUES (?, ?, ?, NULL, ?, '1.0',"
            " '2026-09-26T09:00:00')",
            (sha, kind, json.dumps(value), model),
        )
        conn.commit()
    finally:
        conn.close()


def add_reading(root, rel, date, score):
    add_fact(root, rel, "ocr_text", {"lines": [{"text": date, "score": score}]}, OCR_MODEL)


def observed(root, template, rel):
    """(attention, tier, date source_table or None, any truncated, reasons) for `rel`'s item.

    `attention` is the production property, OBSERVED; the rest is read off the stored row.
    """
    plan_id = run_plan(root, template).plan_id
    assert plan_id is not None
    conn = _conn(root)
    try:
        rows = {r.old_rel_path: r for r in repo.plan_rows(conn, plan_id, floor=FACES_FLOOR)}
    finally:
        conn.close()
    item = rows[rel]
    dates = [t for t in item.tokens if t["resolved_from"] == "date"]
    source = dates[0]["source_table"] if dates else None
    truncated = any(t["truncated"] for t in item.tokens)
    return item.attention, item.tier, source, truncated, list(item.trust_reasons)


# --------------------------------------------------------------------------- the cases


def case_video_ambiguous(tmp_path):
    # 6.4: a `qt_utc` video whose filename says a different day.
    root = build(tmp_path, {"2018-01-01_VID.MOV": {"CreateDate": "2021:03:04 10:00:00"}})
    return root, DATED, "2018-01-01_VID.MOV"


def case_truncated(tmp_path):
    # 6.2 rule 5: twelve characters fit `2022-02-02_0` and cut `001` - dated, `medium`,
    # truncated, so only the truncation operand can select it.
    root = build(tmp_path, {}, deep=("T.JPG", 12, {"DateTimeOriginal": "2022:02:02 08:00:00"}))
    (rel,) = [r for r in _rels(root) if r.endswith("/T.JPG")]
    return root, DATED, rel


def case_caption_contradicts(tmp_path):
    # 6.4 rev 17's construction: two files, the caption naming the OTHER file's place.
    root = build(
        tmp_path,
        {
            "a.png": {"DateTimeOriginal": "2019:07:04 18:23:11"},
            "b.png": {"DateTimeOriginal": "2019:07:04 18:24:11"},
        },
    )
    add_fact(root, "a.png", "place", {"place": "Tokyo"}, "rg_cities1000")
    add_fact(root, "b.png", "place", {"place": "Paris"}, "rg_cities1000")
    add_fact(
        root, "a.png", "caption", {"label": "Paris rooftops", "sentence": "A."}, "qwen2.5vl"
    )
    return root, SCENE, "a.png"


def case_mtime(tmp_path):
    root = build(tmp_path, {"Other/plain.png": {}})
    return root, DATED, "Other/plain.png"


def case_ocr_medium(tmp_path):
    root = build(tmp_path, {"Scans/scan.png": {}})
    add_reading(root, "Scans/scan.png", "1998-07-04", 0.8)
    return root, DATED, "Scans/scan.png"


def case_ocr_low(tmp_path):
    root = build(tmp_path, {"Scans/scan.png": {}})
    add_reading(root, "Scans/scan.png", "1998-07-04", 0.6)
    return root, DATED, "Scans/scan.png"


def case_filename(tmp_path):
    root = build(tmp_path, {"Other/IMG_20100101_0001.png": {}})
    return root, DATED, "Other/IMG_20100101_0001.png"


def case_exif(tmp_path):
    root = build(tmp_path, {"exif.png": {"DateTimeOriginal": "2019:07:04 18:23:11"}})
    return root, DATED, "exif.png"


def case_qt_utc_plain(tmp_path):
    # 6.1: `qt_utc` is `medium`; the filename carries no date to disagree with.
    root = build(tmp_path, {"VID_PLAIN.MOV": {"CreateDate": "2021:03:03 10:00:00"}})
    return root, DATED, "VID_PLAIN.MOV"


def case_no_date_token(tmp_path):
    root = build(tmp_path, {"exif.png": {"DateTimeOriginal": "2019:07:04 18:23:11"}})
    return root, NO_DATE, "exif.png"


def case_planner(tmp_path):
    # A sidecar unit whose PRIMARY has no `capture` row (6.1 rev 6: attributed to `planner`).
    # The sidecar is never the primary (5.4), so it carries the primary's token.
    root = build(
        tmp_path, {"IMG_7.JPG": {"DateTimeOriginal": "2020:01:01 10:00:00"}, "IMG_7.xmp": {}}
    )
    conn = _conn(root)
    try:
        conn.execute(
            "DELETE FROM capture WHERE file_id = (SELECT id FROM files WHERE rel_path = ?)",
            ("IMG_7.JPG",),
        )
        conn.commit()
    finally:
        conn.close()
    return root, DATED, "IMG_7.xmp"


def case_scene_not_date(tmp_path):
    root = build(tmp_path, {"a.png": {"DateTimeOriginal": "2019:07:04 18:23:11"}})
    add_fact(root, "a.png", "caption", {"label": "Beach", "sentence": "A."}, "qwen2.5vl")
    return root, SCENE, "a.png"


def _rels(root):
    conn = _conn(root)
    try:
        return [r["rel_path"] for r in conn.execute("SELECT rel_path FROM files")]
    finally:
        conn.close()


# (class, case, attention, tier, date source_table, truncated, the witness sentence or None).
# EVERY EXPECTATION A LITERAL, from design 6.4 / 6.1 rev 27 / 8.2 rev 27.
TABLE = [
    (VIDEO_AMBIGUOUS, case_video_ambiguous, True, "low", "capture", False, VIDEO_REASON),
    (TRUNCATED, case_truncated, True, "medium", "capture", True, TRUNCATED_REASON),
    (
        CAPTION_CONTRADICTS,
        case_caption_contradicts,
        True,
        "low",
        "capture",
        False,
        CAPTION_REASON,
    ),
    ("date source file_mtime", case_mtime, True, "low", "capture", False, None),
    ("date source ocr, medium", case_ocr_medium, True, "medium", "inferred_facts", False, None),
    ("date source ocr, low", case_ocr_low, True, "low", "inferred_facts", False, None),
    ("date source filename", case_filename, False, "medium", "capture", False, None),
    ("date source exif", case_exif, False, "high", "capture", False, None),
    (
        "date source qt_utc, agreeing name",
        case_qt_utc_plain,
        False,
        "medium",
        "capture",
        False,
        None,
    ),
    ("no date token", case_no_date_token, True, "high", None, False, None),
    ("date token attributed to planner", case_planner, True, "low", "planner", False, None),
    (
        "scene from inferred_facts, exif date",
        case_scene_not_date,
        False,
        "medium",
        "capture",
        False,
        None,
    ),
]
# M743: pytest SKIPS an empty parametrize. The table is asserted at collection, both ways.
assert len(TABLE) == 12, "the attention-class table lost or gained a row"
assert {row[0] for row in TABLE} >= set(FLAG_CLASSES), "a flag class has no row"
assert sum(1 for row in TABLE if row[2] is False) == 4, (
    "the negative rows are the table's point"
)


@pytest.mark.parametrize(
    ("cls", "case", "attention", "tier", "source", "truncated", "witness"),
    TABLE,
    ids=[row[0] for row in TABLE],
)
def test_the_attention_class_table(
    tmp_path, cls, case, attention, tier, source, truncated, witness
):
    root, template, rel = case(tmp_path)
    got_attention, got_tier, got_source, got_truncated, reasons = observed(root, template, rel)
    assert (got_tier, got_source, got_truncated) == (tier, source, truncated), cls
    if witness is not None:
        assert witness in reasons, (cls, reasons)
    assert got_attention is attention, cls


def test_the_table_test_is_parametrized_over_the_WHOLE_table():
    """The collection asserts guard TABLE; this guards the WIRING (Task 11 review, F1).

    M2156: `parametrize(..., [], ids=[])` over the table test with TABLE intact passed all
    three asserts and left the suite green (4079 passed, one more skipped) - every row gone.
    Task 9's F2 (M2116) again, one module later: M743's shape, one line from the list.
    """
    marks = [m for m in test_the_attention_class_table.pytestmark if m.name == "parametrize"]
    assert len(marks) == 1
    assert list(marks[0].args[1]) == list(TABLE)


def test_every_consistency_rule_has_a_row():
    """Design 8.2 rev 27: a rule added to `consistency.RULES` without a row here fails."""
    assert {rule.__name__ for rule in consistency.RULES} == set(FLAG_CLASSES)
    assert len(FLAG_CLASSES) == 3


# --------------------------------------------------------------------------- Step 3: the API


@contextmanager
def review(root, plan_id):
    ws = workspace_if_exists(root)
    assert ws is not None
    logger = logging.getLogger("pam.tests.attention_classes")
    logger.propagate = False
    logger.handlers[:] = [logging.NullHandler()]
    server = ReviewServer(workspace=ws, plan_id=plan_id, logger=logger)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=20)


def call(server, method, path, body=None):
    payload = b"" if body is None else json.dumps(body).encode("utf-8")
    conn = HTTPConnection(server.host, server.port, timeout=20)
    try:
        conn.request(
            method,
            f"/t/{server.token}/api{path}",
            body=payload if method == "POST" else None,
            headers={"Content-Type": "application/json", "Content-Length": str(len(payload))}
            if method == "POST"
            else {},
        )
        response = conn.getresponse()
        raw = response.read()
        assert response.status == 200, raw
        return json.loads(raw)
    finally:
        conn.close()


def test_the_summary_count_and_the_attention_filter_move_together_and_the_operand_does_not_block(
    tmp_path,
):
    """UX 5a: the banner must not claim twenty while the filter shows nineteen - driven on the
    OCR `medium` case, a two-member unit. Then P12's "highlights; it does not block":
    `Approve High+Medium`'s scope (an unfiltered approve over `proposed`, UX 5) approves the
    OCR `medium` unit, BOTH members, and holds back only the `low` item."""
    root = build(
        tmp_path,
        {
            "Scans/scan.png": {},
            "Scans/scan.xmp": {},
            "Other/plain.png": {},
            "exif.png": {"DateTimeOriginal": "2019:07:04 18:23:11"},
        },
    )
    add_reading(root, "Scans/scan.png", "1998-07-04", 0.8)
    plan_id = run_plan(root, DATED).plan_id
    with review(root, plan_id) as server:
        summary = call(server, "GET", f"/plan/{plan_id}")
        flagged = call(server, "GET", f"/plan/{plan_id}/items?attention=1&limit=200")["items"]
        by_rel = {
            c["old_rel_path"]: c
            for c in call(server, "GET", f"/plan/{plan_id}/items?limit=200")["items"]
        }
        assert {by_rel[rel]["tier"] for rel in ("Scans/scan.png", "Scans/scan.xmp")} == {
            "medium"
        }
        # The OCR unit (two items) and the mtime-dated file; never the exif one.
        want = {"Scans/scan.png", "Scans/scan.xmp", "Other/plain.png"}
        assert {c["old_rel_path"] for c in flagged} == want
        assert summary["attention"] == len(flagged) == 3

        reply = call(
            server,
            "POST",
            f"/plan/{plan_id}/decide-filter",
            {"action": "approve", "status": "proposed"},
        )
        assert reply["excluded_low"] == 1
        after = {
            c["old_rel_path"]: c
            for c in call(server, "GET", f"/plan/{plan_id}/items?limit=200")["items"]
        }
    assert {rel: after[rel]["status"] for rel in sorted(after)} == {
        "Other/plain.png": "proposed",
        "Scans/scan.png": "approved",
        "Scans/scan.xmp": "approved",
        "exif.png": "approved",
    }
    # Highlighted, still: approval moves the status, not the attention.
    assert after["Scans/scan.png"]["attention"] is True
    assert after["Scans/scan.xmp"]["attention"] is True
