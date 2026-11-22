"""M12 Task 10, the end-to-end half: the OCR date through `pam plan` and the review API.

THE SHIPPING GATE's own tests. The only M12 task that changes a proposed NAME is proven here
against the spec, with no model involved: every `ocr_text` reading is INSERTED BY HAND with the
suite's own SQL (the enricher is Tasks 12-13), and `pam scan`, `pam plan` and `pam review`'s
`GET items` are the real commands.

EVERY EXPECTED NAME AND SENTENCE IS SPELLED BY HAND from design 6.1 rev 27 (P9) and 6.4 rev 27
(P11) - never produced by calling `resolve_date`, `find_date` or a production constant. The
mtime-dated files are stamped `2024-05-01 12:00` local time before the scan, so their catalog
date is `2024-05-01`, written here as a literal. The OCR model is spelled `ppocr-v4`, design 8.1
rev 28's `model_name` (Q2), and read from nowhere else.
"""

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import threading
from contextlib import contextmanager
from datetime import datetime
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace

import pytest
from archives import FAKE_COMMAND

from pam.catalog import repo
from pam.catalog.db import connect
from pam.naming.planner import run_plan
from pam.paths import workspace_for, workspace_if_exists
from pam.scan.runner import run_scan
from pam.webui import provenance
from pam.webui.server import ReviewServer

MODEL = "ppocr-v4"  # design 8.1 rev 28 (Q2)
VERSION = "0123456789ab"
MTIME = datetime(2024, 5, 1, 12, 0, 0)  # the catalog date of every mtime-dated file: 2024-05-01
MTIME_REASON = "Date read from the file's modification time - no camera date present."
OCR_ROW = "Date read from text in the photo - no camera or filename date."
BELOW_HIGH = "That reading is below pam's high-confidence mark ([tiers] ocr high = 0.9)."
SCANS = '[enrich.ocr]\nfolders = ["Scans"]\n'
STATIC_DIR = Path(__file__).resolve().parent.parent / "pam" / "webui" / "static"
# M14 Task 1: `whyLines` moved, unchanged, from `app.js` into `pam-trust.js`; it reads
# `pam-core.js`'s `COPY` at call time, so the driver puts the core's names on `global` first.
TRUST_JS = STATIC_DIR / "pam-trust.js"
CORE_JS = STATIC_DIR / "pam-core.js"


def read_sentence(text, date, pct):
    """P11's provenance sentence, as the spec prints it."""
    return f'pam read "{text}" in the photo as {date} ({pct}% confidence).'


# --------------------------------------------------------------------------- the archive


def build(tmp_path, files, *, config=SCANS):
    """`{rel_path: None (mtime-dated) | "YYYY:MM:DD HH:MM:SS" (exif) | datetime (mtime)}`.

    Every file's bytes are unique, so no two share a sha256 - `inferred_facts` is
    CONTENT-addressed. The file's mtime is set before the scan, so a file with no EXIF and
    no date in its name is dated by it.
    """
    root = tmp_path / "photos"
    for rel, spec in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"the only copy of {rel}".encode())
        tags = {"DateTimeOriginal": spec} if isinstance(spec, str) else {}
        (root / f"{rel}.tags.json").write_text(
            json.dumps({**tags, "PamTestSidecar": rel}), encoding="utf-8"
        )
        stamp = (spec if isinstance(spec, datetime) else MTIME).timestamp()
        os.utime(path, (stamp, stamp))
    run_scan(root, exiftool_command=FAKE_COMMAND)
    configure(root, config)
    return root


def configure(root, body):
    workspace_for(root).config_path.write_text(body, encoding="utf-8")


def _conn(root):
    conn = connect(workspace_for(root).db_path)
    conn.row_factory = sqlite3.Row
    return conn


def add_reading(root, rel, lines=None, *, raw=None, version=VERSION, kind="ocr_text"):
    """One `ocr_text` row, with the SUITE'S OWN SQL. `lines` is [(text, score), ...]."""
    value = (
        raw
        if raw is not None
        else json.dumps({"lines": [{"text": text, "score": score} for text, score in lines]})
    )
    conn = _conn(root)
    try:
        sha = conn.execute("SELECT sha256 FROM files WHERE rel_path = ?", (rel,)).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO inferred_facts (sha256, kind, value_json, confidence, model_name,"
            " model_version, generated_at) VALUES (?, ?, ?, NULL, ?, ?, '2026-09-26T09:00:00')",
            (sha, kind, value, MODEL if kind == "ocr_text" else "qwen2.5vl", version),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def fact_count(root):
    conn = _conn(root)
    try:
        return conn.execute("SELECT COUNT(*) FROM inferred_facts").fetchone()[0]
    finally:
        conn.close()


def items(root, plan_id):
    """`{old_rel_path: item}` for one plan, read with the suite's own SQL."""
    conn = _conn(root)
    try:
        rows = conn.execute(
            "SELECT old_rel_path, generated_filename, tokens_json, tier, trust_score,"
            " trust_reasons_json FROM plan_items WHERE plan_id = ?",
            (plan_id,),
        ).fetchall()
    finally:
        conn.close()
    return {
        r["old_rel_path"]: {
            "name": r["generated_filename"],
            "tokens": json.loads(r["tokens_json"]),
            "tier": r["tier"],
            "score": r["trust_score"],
            "reasons": json.loads(r["trust_reasons_json"]),
        }
        for r in rows
    }


def date_token(item):
    (token,) = [t for t in item["tokens"] if t["resolved_from"] == "date"]
    return token


def planned(root, template=None):
    summary = run_plan(root, template)
    assert summary.plan_id is not None
    return summary, items(root, summary.plan_id)


def stored_events(root):
    """`[(event_id, start_dt, end_dt, [member rel_paths])]`, with the suite's own SQL."""
    conn = _conn(root)
    try:
        names = {r["id"]: r["rel_path"] for r in conn.execute("SELECT id, rel_path FROM files")}
        members = {}
        for r in conn.execute("SELECT event_id, file_id FROM file_events"):
            members.setdefault(r["event_id"], []).append(names[r["file_id"]])
        return [
            (r["id"], r["start_dt"], r["end_dt"], sorted(members.get(r["id"], [])))
            for r in conn.execute("SELECT id, start_dt, end_dt FROM events ORDER BY start_dt")
        ]
    finally:
        conn.close()


# --------------------------------------------------------------------------- the review API


@contextmanager
def review(root, plan_id):
    ws = workspace_if_exists(root)
    assert ws is not None
    logger = logging.getLogger("pam.tests.ocr_review")
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


def cards(root, plan_id):
    """`GET items` for the whole plan, `{old_rel_path: card}`."""
    with review(root, plan_id) as server:
        conn = HTTPConnection(server.host, server.port, timeout=20)
        try:
            conn.request("GET", f"/t/{server.token}/api/plan/{plan_id}/items?limit=200")
            response = conn.getresponse()
            raw = response.read()
            assert response.status == 200, raw
        finally:
            conn.close()
    return {card["old_rel_path"]: card for card in json.loads(raw)["items"]}


# Task 3's pattern: `app.js` (`pam-trust.js` since M14 Task 1) loaded by a real `node` behind a one-node document stub, its
# `whyLines` executed on the card the server served. This prints what the Why? panel renders.
WHY_JS = """
const NODE = {};
NODE.parentNode = NODE;
NODE.addEventListener = function () {};
NODE.appendChild = function () {};
NODE.style = {};
NODE.classList = { add() {}, remove() {}, toggle() {}, contains() { return false; } };
global.document = { getElementById: function () { return NODE; } };
Object.assign(global, require(process.argv[4]));
const app = require(process.argv[2]);
const card = JSON.parse(require("node:fs").readFileSync(process.argv[3], "utf-8"));
process.stdout.write(JSON.stringify(app.whyLines(card)));
"""


def why_lines(tmp_path, card):
    script = tmp_path / "why.js"
    script.write_text(WHY_JS, encoding="utf-8")
    data = tmp_path / "card.json"
    data.write_text(json.dumps(card), encoding="utf-8")
    run = subprocess.run(
        [shutil.which("node"), str(script), str(TRUST_JS), str(data), str(CORE_JS)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


# --------------------------------------------------------------------------- P9: all four hold

# (line score, the date token's tier, the provenance percentage, the trust score). Design 6.1
# rev 27: `low` below `[tiers] ocr.low` (0.7), else `medium`, NEVER `high`. Design 6.4 rev 27:
# -10 always, -10 more when the score is below `[tiers] ocr.high` (0.9).
ALL_FOUR = (
    (0.6, "low", 60, 80),
    (0.8, "medium", 80, 80),
    (0.9, "medium", 90, 90),
    (0.99, "medium", 99, 90),
)
assert len(ALL_FOUR) >= 4


@pytest.mark.parametrize(("line_score", "tier", "pct", "trust"), ALL_FOUR)
def test_all_four_conditions_hold_the_name_carries_the_ocr_date(
    tmp_path, line_score, tier, pct, trust
):
    root = build(tmp_path, {"Scans/scan.png": None})
    fact = add_reading(root, "Scans/scan.png", [("1998-07-04", line_score)])
    _, got = planned(root)
    item = got["Scans/scan.png"]
    assert item["name"] == "1998-07-04_001.png"
    assert date_token(item) == {
        "token": "date",
        "resolved_from": "date",
        "value": "1998-07-04",
        "source_table": "inferred_facts",
        "source_id": fact,
        "tier": tier,
        "truncated": False,
        "confidence": line_score,
    }
    assert item["tier"] == tier  # the seq token is `high`; the date is the minimum
    assert item["score"] == trust
    expected = [read_sentence("1998-07-04", "1998-07-04", pct), OCR_ROW]
    if line_score < 0.9:
        expected.append(BELOW_HIGH)
    assert item["reasons"] == expected


def test_no_other_token_gains_a_confidence_key(tmp_path):
    """The freeze: `"confidence"` is written only on a token that has one."""
    root = build(tmp_path, {"Scans/scan.png": None, "Other/plain.png": None})
    add_reading(root, "Scans/scan.png", [("1998-07-04", 0.8)])
    _, got = planned(root)
    keyed = [
        (rel, token["resolved_from"])
        for rel, item in got.items()
        for token in item["tokens"]
        if "confidence" in token
    ]
    assert keyed == [("Scans/scan.png", "date")]


# --------------------------------------------------------------------------- P9: each alone false


def test_condition_1_alone_false_a_filename_date_is_not_replaced(tmp_path):
    root = build(tmp_path, {"Scans/IMG_20100101_0001.png": None})
    add_reading(root, "Scans/IMG_20100101_0001.png", [("1998-07-04", 0.8)])
    item = planned(root)[1]["Scans/IMG_20100101_0001.png"]
    assert item["name"] == "2010-01-01_001.png"
    assert date_token(item)["source_table"] == "capture"
    assert not any(reason.startswith("pam read") for reason in item["reasons"])


def test_condition_2_alone_false_outside_the_folders(tmp_path):
    root = build(tmp_path, {"Screenshots/shot.png": None})
    add_reading(root, "Screenshots/shot.png", [("1998-07-04", 0.8)])
    item = planned(root)[1]["Screenshots/shot.png"]
    assert item["name"] == "2024-05-01_001.png"
    assert item["reasons"] == [MTIME_REASON]


def test_condition_3_alone_false_a_below_floor_date_is_suppressed_SILENTLY(tmp_path):
    root = build(tmp_path, {"Scans/scan.png": None})
    add_reading(root, "Scans/scan.png", [("1998-07-04", 0.45)])
    item = planned(root)[1]["Scans/scan.png"]
    assert item["name"] == "2024-05-01_001.png"
    assert item["reasons"] == [MTIME_REASON]


def test_condition_4_alone_false_a_later_date_is_not_used_and_the_plan_says_so(tmp_path):
    root = build(tmp_path, {"Scans/scan.png": None})
    add_reading(root, "Scans/scan.png", [("2024-05-02", 0.8)])
    item = planned(root)[1]["Scans/scan.png"]
    assert item["name"] == "2024-05-01_001.png"
    assert date_token(item)["source_table"] == "capture"
    assert item["reasons"] == [
        'pam read "2024-05-02" in the picture as 2024-05-02, which is later than the file'
        " itself, so it did not use it.",
        MTIME_REASON,
    ]


def test_condition_4_the_SAME_day_as_the_file_is_used(tmp_path):
    root = build(tmp_path, {"Scans/scan.png": None})
    fact = add_reading(root, "Scans/scan.png", [("2024-05-01", 0.8)])
    item = planned(root)[1]["Scans/scan.png"]
    assert item["name"] == "2024-05-01_001.png"
    assert (date_token(item)["source_table"], date_token(item)["source_id"]) == (
        "inferred_facts",
        fact,
    )
    assert item["reasons"][0] == read_sentence("2024-05-01", "2024-05-01", 80)


# --------------------------------------------------------------------------- B6 through the API


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_provenance_sentence_at_every_score_through_GET_items_and_the_why_panel(tmp_path):
    """B6: every emitted OCR date carries its confidence - at 0.6, at exactly `ocr.high`
    and at 0.99. The card has NO confidence note (the OCR token is scored) and still names
    the OCR model."""
    root = build(
        tmp_path, {"Scans/low.png": None, "Scans/high.png": None, "Scans/top.png": None}
    )
    readings = {
        "Scans/low.png": (("1998-07-04", 0.6), 60),
        "Scans/high.png": (("1997-03-02", 0.9), 90),
        "Scans/top.png": (("1996-01-05", 0.99), 99),
    }
    facts = {rel: add_reading(root, rel, [line]) for rel, (line, _) in readings.items()}
    summary, _ = planned(root)
    served = cards(root, summary.plan_id)
    for rel, ((date, _), pct) in readings.items():
        card = served[rel]
        sentence = read_sentence(date, date, pct)
        assert card["trust_reasons"][0] == sentence
        assert card["confidence_note"] is None
        assert card["fact_models"] == {str(facts[rel]): f"{MODEL} {VERSION}"}
        why = why_lines(tmp_path, card)
        assert why["reasons"][0] == sentence
        assert why["confidence"] is None


# --------------------------------------------------------------------------- I15: the lifecycle


def test_I15_the_newest_reading_decides_in_every_direction(tmp_path):
    root = build(tmp_path, {"Scans/scan.png": None})
    first = add_reading(root, "Scans/scan.png", [("1998-07-04", 0.8)], version="z1")
    plan1, got = planned(root)
    assert got["Scans/scan.png"]["name"] == "1998-07-04_001.png"

    # A newer reading of the same content with NO date: no OCR date, never the older one.
    add_reading(root, "Scans/scan.png", [("KODAK", 0.95)], version="z2")
    assert planned(root)[1]["Scans/scan.png"]["name"] == "2024-05-01_001.png"

    # A newer AMBIGUOUS reading: none either.
    add_reading(root, "Scans/scan.png", [("04/07/1998", 0.95)], version="z3")
    assert planned(root)[1]["Scans/scan.png"]["name"] == "2024-05-01_001.png"

    # A newer reading with a DIFFERENT date, whose identity string sorts LOWER than every
    # older one's: the highest id still wins (a digest has no order - P7).
    newest = add_reading(root, "Scans/scan.png", [("2001-02-03", 0.95)], version="a0")
    item = planned(root)[1]["Scans/scan.png"]
    assert item["name"] == "2001-02-03_001.png"
    assert date_token(item)["source_id"] == newest

    # The plan made before the newer readings keeps its provenance, and the row still exists.
    assert date_token(items(root, plan1.plan_id)["Scans/scan.png"])["source_id"] == first
    conn = _conn(root)
    try:
        row = conn.execute("SELECT kind FROM inferred_facts WHERE id = ?", (first,)).fetchone()
    finally:
        conn.close()
    assert row["kind"] == "ocr_text"


# --------------------------------------------------------------------------- I16: the floor


def test_I16_the_floor_moves_both_ways_without_a_new_reading(tmp_path):
    root = build(tmp_path, {"Scans/scan.png": None})
    add_reading(root, "Scans/scan.png", [("1998-07-04", 0.45)])
    before = fact_count(root)
    assert planned(root)[1]["Scans/scan.png"]["name"] == "2024-05-01_001.png"

    configure(root, SCANS + "\n[tiers]\nocr = { floor = 0.4 }\n")
    item = planned(root)[1]["Scans/scan.png"]
    assert item["name"] == "1998-07-04_001.png"
    assert date_token(item)["tier"] == "low"

    configure(root, SCANS)
    assert planned(root)[1]["Scans/scan.png"]["name"] == "2024-05-01_001.png"
    assert fact_count(root) == before


# --------------------------------------------------------------------------- I4: only mtime moves


def test_I4_only_the_mtime_dated_file_takes_the_ocr_date(tmp_path):
    root = build(
        tmp_path,
        {
            "Scans/camera.png": "2019:07:04 18:23:11",
            "Scans/IMG_20100101_0001.png": None,
            "Scans/scan.png": None,
        },
    )
    for rel in ("Scans/camera.png", "Scans/IMG_20100101_0001.png", "Scans/scan.png"):
        add_reading(root, rel, [("1998-07-04", 0.8)])
    got = planned(root)[1]
    assert {rel: item["name"] for rel, item in got.items()} == {
        "Scans/camera.png": "2019-07-04_001.png",
        "Scans/IMG_20100101_0001.png": "2010-01-01_001.png",
        "Scans/scan.png": "1998-07-04_001.png",
    }
    assert {rel: date_token(item)["source_table"] for rel, item in got.items()} == {
        "Scans/camera.png": "capture",
        "Scans/IMG_20100101_0001.png": "capture",
        "Scans/scan.png": "inferred_facts",
    }


# --------------------------------------------------------------------------- I3: event identity


def test_I3_one_scanning_session_years_apart_is_three_events(tmp_path):
    """Minutes apart on disk, years apart in the picture: three events, each spanning the
    date its name carries."""
    session = {
        "Scans/a.png": datetime(2024, 5, 1, 12, 0),
        "Scans/b.png": datetime(2024, 5, 1, 12, 5),
        "Scans/c.png": datetime(2024, 5, 1, 12, 10),
    }
    root = build(tmp_path, session)
    for rel, date in (
        ("Scans/a.png", "1985-03-02"),
        ("Scans/b.png", "1992-08-15"),
        ("Scans/c.png", "2003-12-24"),
    ):
        add_reading(root, rel, [(date, 0.8)])
    summary, got = planned(root)
    assert summary.events == 3
    events = stored_events(root)
    assert [(start, end, members) for _, start, end, members in events] == [
        ("1985-03-02T00:00:00", "1985-03-02T00:00:00", ["Scans/a.png"]),
        ("1992-08-15T00:00:00", "1992-08-15T00:00:00", ["Scans/b.png"]),
        ("2003-12-24T00:00:00", "2003-12-24T00:00:00", ["Scans/c.png"]),
    ]
    assert len({event_id for event_id, *_ in events}) == 3
    assert {rel: item["name"] for rel, item in got.items()} == {
        "Scans/a.png": "1985-03-02_001.png",
        "Scans/b.png": "1992-08-15_001.png",
        "Scans/c.png": "2003-12-24_001.png",
    }
    # The nav month the review page files each card under is its name's.
    served = cards(root, summary.plan_id)
    assert {rel: card["date"] for rel, card in served.items()} == {
        "Scans/a.png": "1985-03-02",
        "Scans/b.png": "1992-08-15",
        "Scans/c.png": "2003-12-24",
    }


def test_I3_control_without_the_ocr_dates_the_session_is_one_event(tmp_path):
    session = {
        "Scans/a.png": datetime(2024, 5, 1, 12, 0),
        "Scans/b.png": datetime(2024, 5, 1, 12, 5),
    }
    root = build(tmp_path, session, config="")
    add_reading(root, "Scans/a.png", [("1985-03-02", 0.8)])
    add_reading(root, "Scans/b.png", [("1992-08-15", 0.8)])
    planned(root)
    assert [(s, e, m) for _, s, e, m in stored_events(root)] == [
        ("2024-05-01T12:00:00", "2024-05-01T12:05:00", ["Scans/a.png", "Scans/b.png"]),
    ]


def test_I3_a_file_P9_does_NOT_upgrade_is_grouped_under_the_date_its_name_carries(tmp_path):
    """Task 10 Stage 2 (M2144): I3 from the side the other I3 tests do not reach - a reading
    that must NOT move the name must not move the EVENT either. Each file below has a dated
    reading and one of design 6.1's four conditions false; each is named AND grouped under
    its own catalog date. Only `used.png` (all four true) moves, on both sides. Before this,
    step 0 could read a below-floor line the name ignored (M2144) with the suite green."""
    root = build(
        tmp_path,
        {
            "Scans/exif.png": "2010:06:15 09:00:00",  # condition 1 false: an EXIF date
            "Outside/out.png": datetime(2021, 3, 3, 12, 0),  # condition 2 false
            "Scans/below.png": datetime(2022, 4, 4, 12, 0),  # condition 3: a 0.45 line
            "Scans/later.png": datetime(2023, 5, 5, 12, 0),  # condition 4: after the file
            "Scans/used.png": None,  # all four hold
        },
    )
    add_reading(root, "Scans/exif.png", [("1990-01-01", 0.95)])
    add_reading(root, "Outside/out.png", [("1990-01-02", 0.95)])
    add_reading(root, "Scans/below.png", [("1990-01-03", 0.45)])
    add_reading(root, "Scans/later.png", [("2023-05-06", 0.95)])
    add_reading(root, "Scans/used.png", [("1985-03-14", 0.95)])
    got = planned(root)[1]
    assert {rel: item["name"] for rel, item in got.items()} == {
        "Scans/exif.png": "2010-06-15_001.png",
        "Outside/out.png": "2021-03-03_001.png",
        "Scans/below.png": "2022-04-04_001.png",
        "Scans/later.png": "2023-05-05_001.png",
        "Scans/used.png": "1985-03-14_001.png",
    }
    assert [(s, e, m) for _, s, e, m in stored_events(root)] == [
        ("1985-03-14T00:00:00", "1985-03-14T00:00:00", ["Scans/used.png"]),
        ("2010-06-15T09:00:00", "2010-06-15T09:00:00", ["Scans/exif.png"]),
        ("2021-03-03T12:00:00", "2021-03-03T12:00:00", ["Outside/out.png"]),
        ("2022-04-04T12:00:00", "2022-04-04T12:00:00", ["Scans/below.png"]),
        ("2023-05-05T12:00:00", "2023-05-05T12:00:00", ["Scans/later.png"]),
    ]


def test_I3_a_unit_takes_ONE_stem_from_its_primary_and_each_member_keeps_its_own_event(
    tmp_path,
):
    """A two-member unit whose members' readings carry DIFFERENT dates. The display image
    is the primary (design 5.4), so the unit is named from ITS date; the RAW keeps its own
    event (M8's per-file membership)."""
    root = build(tmp_path, {"Scans/pair.png": None, "Scans/pair.nef": None})
    add_reading(root, "Scans/pair.png", [("1990-01-01", 0.8)])
    add_reading(root, "Scans/pair.nef", [("1995-06-06", 0.8)])
    got = planned(root)[1]
    assert {rel: item["name"] for rel, item in got.items()} == {
        "Scans/pair.png": "1990-01-01_001.png",
        "Scans/pair.nef": "1990-01-01_001.nef",
    }
    assert [(s, e, m) for _, s, e, m in stored_events(root)] == [
        ("1990-01-01T00:00:00", "1990-01-01T00:00:00", ["Scans/pair.png"]),
        ("1995-06-06T00:00:00", "1995-06-06T00:00:00", ["Scans/pair.nef"]),
    ]


def test_I7_a_units_date_token_is_its_PRIMARYS_reading_on_every_member(tmp_path):
    """Task 10 Stage 2 (M2147): the unit is named from its primary (design 5.4 - the display
    image), so the date token's PROVENANCE, tier, confidence and the provenance sentence are
    the primary's reading too, on every member's card. The RAW's own reading differs in date
    AND score (0.55 would make it `low`), so a card built from the wrong member says so."""
    root = build(tmp_path, {"Scans/pair.png": None, "Scans/pair.nef": None})
    primary = add_reading(root, "Scans/pair.png", [("1990-01-01", 0.95)])
    add_reading(root, "Scans/pair.nef", [("1995-06-06", 0.55)])
    summary, got = planned(root)
    for rel in ("Scans/pair.png", "Scans/pair.nef"):
        item = got[rel]
        token = date_token(item)
        assert (token["value"], token["source_table"], token["source_id"]) == (
            "1990-01-01",
            "inferred_facts",
            primary,
        ), rel
        assert (token["tier"], token["confidence"], item["tier"], item["score"]) == (
            "medium",
            0.95,
            "medium",
            90,
        ), rel
        assert item["reasons"] == [read_sentence("1990-01-01", "1990-01-01", 95), OCR_ROW], rel
    served = cards(root, summary.plan_id)
    assert served["Scans/pair.nef"]["trust_reasons"][0] == read_sentence(
        "1990-01-01", "1990-01-01", 95
    )


# --------------------------------------------------------------------------- {seq} order


def test_seq_orders_two_same_day_scans_by_their_OCR_date_not_their_mtime(tmp_path):
    """Both read 1998-07-04, so they tie on the effective date and `rel_path` breaks it:
    `a` before `z`. By mtime, `z` (09:00) would come before `a` (10:00)."""
    root = build(
        tmp_path,
        {"Scans/z.png": datetime(2024, 5, 1, 9, 0), "Scans/a.png": datetime(2024, 5, 1, 10, 0)},
    )
    add_reading(root, "Scans/z.png", [("1998-07-04", 0.8)])
    add_reading(root, "Scans/a.png", [("1998-07-04", 0.8)])
    got = planned(root)[1]
    assert {rel: item["name"] for rel, item in got.items()} == {
        "Scans/a.png": "1998-07-04_001.png",
        "Scans/z.png": "1998-07-04_002.png",
    }


def test_seq_control_by_mtime_z_comes_first(tmp_path):
    root = build(
        tmp_path,
        {"Scans/z.png": datetime(2024, 5, 1, 9, 0), "Scans/a.png": datetime(2024, 5, 1, 10, 0)},
        config="",
    )
    got = planned(root)[1]
    assert {rel: item["name"] for rel, item in got.items()} == {
        "Scans/z.png": "2024-05-01_001.png",
        "Scans/a.png": "2024-05-01_002.png",
    }


def test_seq_orders_by_the_EFFECTIVE_capture_dt_across_sources_in_one_scope(tmp_path):
    """Task 10 Stage 2 (M2143): an OCR-dated scan and an mtime-dated file OUTSIDE the scan
    folders share one `(event, date)` scope, and BOTH other orderings disagree with the
    effective one. The scan's effective date is `2001-01-01T00:00:00` (P9: day precision);
    `Albums/a.png`'s is its mtime, `2001-01-01T01:00:00` - an hour later, so one event
    (`gap_hours = 4`) and one date. By `(capture_dt, rel_path)` (design 6.1) the scan is
    `_001`. By rel_path alone `Albums/` sorts before `Scans/`, and by the scan's RAW mtime
    (2024) the album file is first - each wrong ordering names the album file `_001`."""
    root = build(
        tmp_path,
        {
            "Scans/s.png": datetime(2024, 5, 1, 10, 0),
            "Albums/a.png": datetime(2001, 1, 1, 1, 0),
        },
    )
    add_reading(root, "Scans/s.png", [("2001-01-01", 0.95)])
    got = planned(root)[1]
    assert {rel: item["name"] for rel, item in got.items()} == {
        "Scans/s.png": "2001-01-01_001.png",
        "Albums/a.png": "2001-01-01_002.png",
    }
    (event,) = [e for e in stored_events(root) if e[3] == ["Albums/a.png", "Scans/s.png"]]
    assert (event[1], event[2]) == ("2001-01-01T00:00:00", "2001-01-01T01:00:00")


# --------------------------------------------------------------------------- scope


def test_narrowing_the_folders_is_reversible_and_touches_no_reading(tmp_path):
    root = build(tmp_path, {"Scans/scan.png": None})
    fact = add_reading(root, "Scans/scan.png", [("1998-07-04", 0.8)])
    before = fact_count(root)
    assert planned(root)[1]["Scans/scan.png"]["name"] == "1998-07-04_001.png"
    configure(root, "[enrich.ocr]\nfolders = []\n")
    assert planned(root)[1]["Scans/scan.png"]["name"] == "2024-05-01_001.png"
    configure(root, SCANS)
    item = planned(root)[1]["Scans/scan.png"]
    assert item["name"] == "1998-07-04_001.png"
    assert date_token(item)["source_id"] == fact
    assert fact_count(root) == before


def test_adjacent_prefixes_are_not_the_folder_and_case_does_not_matter(tmp_path):
    root = build(
        tmp_path,
        {"Scans/a.png": None, "Scans-old/c.png": None},
        config='[enrich.ocr]\nfolders = ["SCANS"]\n',
    )
    add_reading(root, "Scans/a.png", [("1998-07-04", 0.8)])
    add_reading(root, "Scans-old/c.png", [("1998-07-04", 0.8)])
    got = planned(root)[1]
    assert got["Scans/a.png"]["name"] == "1998-07-04_001.png"
    assert got["Scans-old/c.png"]["name"] == "2024-05-01_001.png"


# --------------------------------------------------------------------------- malformed rows


def plan_log(summary):
    return Path(summary.log_path).read_text(encoding="utf-8")


def test_a_malformed_NEWEST_reading_over_a_valid_older_one_gives_no_date(tmp_path):
    """Pass 2, A1: selected first, validated second - never the older row's date."""
    root = build(tmp_path, {"Scans/scan.png": None})
    add_reading(root, "Scans/scan.png", [("1998-07-04", 0.8)], version="z1")
    bad = add_reading(root, "Scans/scan.png", raw='{"text": "1998-07-04"}', version="z2")
    summary, got = planned(root)
    assert got["Scans/scan.png"]["name"] == "2024-05-01_001.png"
    assert f"ocr_text fact {bad} is malformed" in plan_log(summary)


def test_malformed_rows_are_skipped_with_a_log_line_and_the_plan_completes(tmp_path):
    root = build(tmp_path, {"Scans/a.png": None, "Scans/b.png": None})
    not_a_list = add_reading(root, "Scans/a.png", raw='{"lines": "1998-07-04"}')
    rev_1 = add_reading(root, "Scans/b.png", raw='{"text": "1998-07-04"}')
    summary, got = planned(root)
    assert {rel: item["name"] for rel, item in got.items()} == {
        "Scans/a.png": "2024-05-01_001.png",
        "Scans/b.png": "2024-05-01_002.png",
    }
    log = plan_log(summary)
    assert f"ocr_text fact {not_a_list} is malformed" in log
    assert f"ocr_text fact {rev_1} is malformed" in log


# --------------------------------------------------------------------------- P11: N2 and freeze


def test_no_date_token_no_ocr_deduction_and_no_ocr_sentence(tmp_path):
    """N2: `--template "{orig}"` makes no claim about a date, so nothing about one is said."""
    root = build(tmp_path, {"Scans/old scan.png": None})
    add_reading(root, "Scans/old scan.png", [("1998-07-04", 0.6)])
    item = planned(root, "{orig}")[1]["Scans/old scan.png"]
    assert item["name"] == "old-scan.png"
    assert (item["score"], item["reasons"]) == (100, [])


def test_the_frozen_config_carries_all_three_ocr_keys(tmp_path):
    root = build(
        tmp_path,
        {"Scans/scan.png": None},
        config='[enrich.ocr]\nfolders = ["Scans", "Family/Old"]\n\n[tiers]\nocr = { floor = 0 }\n',
    )
    summary, _ = planned(root)
    conn = _conn(root)
    try:
        raw = conn.execute(
            "SELECT config_json FROM plans WHERE id = ?", (summary.plan_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    frozen = json.loads(raw)
    assert frozen["ocr_folders"] == ["Scans", "Family/Old"]
    assert frozen["ocr_floor"] == 0.0 and isinstance(frozen["ocr_floor"], float)
    assert frozen["ocr_grammar_version"] == 1


# --------------------------------------------------------------------------- P11: the card's note


def test_a_mixed_card_names_ONLY_the_capped_model_in_its_note(tmp_path):
    """A `medium` OCR date and a caption `{scene}` capped at `low` (`[tiers] caption`'s
    `max_tier_when_no_confidence = "low"`): P11's sentence names ONLY the caption model and
    Needs review - the scored OCR date is never swept into the caption's cap - and the OCR
    provenance sentence stands beside it."""
    root = build(
        tmp_path,
        {"Scans/scan.png": None},
        config=SCANS + '\n[tiers]\ncaption = { max_tier_when_no_confidence = "low" }\n',
    )
    ocr = add_reading(root, "Scans/scan.png", [("1998-07-04", 0.8)])
    caption = add_reading(
        root,
        "Scans/scan.png",
        raw=json.dumps({"label": "Blue Door", "sentence": "A blue door."}),
        kind="caption",
        version="7b",
    )
    summary, got = planned(root)
    assert got["Scans/scan.png"]["name"] == "1998-07-04_Blue-Door_001.png"
    card = cards(root, summary.plan_id)["Scans/scan.png"]
    assert card["confidence_note"] == (
        "No confidence score is available from qwen2.5vl 7b; its contribution to this name"
        " is capped at Needs review."
    )
    assert card["trust_reasons"][0] == read_sentence("1998-07-04", "1998-07-04", 80)
    assert card["fact_models"] == {
        str(ocr): f"{MODEL} {VERSION}",
        str(caption): "qwen2.5vl 7b",
    }


def test_an_ocr_date_scored_EXACTLY_zero_is_a_scored_token(tmp_path):
    """`0.0` is a score (pass 2, A2) - never a presence or truthiness test. With the floor at
    0 a 0.0 line is at the floor and read, so the planner itself writes `"confidence": 0.0`."""
    root = build(
        tmp_path,
        {"Scans/scan.png": None},
        config=SCANS + "\n[tiers]\nocr = { floor = 0 }\n",
    )
    fact = add_reading(root, "Scans/scan.png", [("1998-07-04", 0.0)])
    summary, got = planned(root)
    item = got["Scans/scan.png"]
    assert item["name"] == "1998-07-04_001.png"
    assert date_token(item)["confidence"] == 0.0
    assert item["reasons"][0] == read_sentence("1998-07-04", "1998-07-04", 0)
    card = cards(root, summary.plan_id)["Scans/scan.png"]
    assert card["confidence_note"] is None
    assert card["fact_models"] == {str(fact): f"{MODEL} {VERSION}"}


# (the token's `confidence` value, whether it counts as the token's OWN score). Design 6.4
# rev 27 / pass 2's A2: a real number in [0, 1], 0.0 included - never presence, never truth.
CONFIDENCE_VALUES = (
    (0.0, True),
    (0, True),
    (0.8, True),
    (1, True),
    (1.0, True),
    (True, False),
    (False, False),
    (1.5, False),
    (-0.1, False),
    ("0.8", False),
    (float("nan"), False),
    (None, False),
)
assert len(CONFIDENCE_VALUES) >= 12


@pytest.mark.parametrize(("value", "scored"), CONFIDENCE_VALUES)
def test_the_note_skips_a_token_ONLY_when_it_carries_a_valid_numeric_confidence(value, scored):
    token = {"source_table": "inferred_facts", "source_id": 5, "tier": "medium"}
    token["confidence"] = value
    facts = {5: repo.FactModel(model_name=MODEL, model_version=VERSION, confidence=None)}
    note = provenance.confidence_note(SimpleNamespace(tokens=[token]), facts)
    if scored:
        assert note is None
    else:
        assert note == (
            f"No confidence score is available from {MODEL} {VERSION}; its contribution to"
            " this name is capped at Good."
        )


def test_a_token_with_NO_confidence_key_is_not_scored():
    token = {"source_table": "inferred_facts", "source_id": 5, "tier": "low"}
    facts = {5: repo.FactModel(model_name=MODEL, model_version=VERSION, confidence=None)}
    assert provenance.confidence_note(SimpleNamespace(tokens=[token]), facts) == (
        f"No confidence score is available from {MODEL} {VERSION}; its contribution to"
        " this name is capped at Needs review."
    )


def test_two_capped_models_are_their_contribution():
    tokens = [
        {"source_table": "inferred_facts", "source_id": 1, "tier": "medium"},
        {"source_table": "inferred_facts", "source_id": 2, "tier": "low"},
    ]
    facts = {
        1: repo.FactModel(model_name="rg_cities1000", model_version="1.0", confidence=None),
        2: repo.FactModel(model_name="qwen2.5vl", model_version="7b", confidence=None),
    }
    assert provenance.confidence_note(SimpleNamespace(tokens=tokens), facts) == (
        "No confidence score is available from qwen2.5vl 7b and rg_cities1000 1.0; their"
        " contribution to this name is capped at Needs review."
    )
