"""M13 Task 6 (C3; design 6.4, S10): a unit whose members' own camera dates disagree says so.

`m12.md` §4 Q2: "the planner never sees a non-primary member's own camera date" - so an
OCR-dated display primary named a unit whose RAW said 2003, with no reason shown. S10: such a
unit carries a trust reason naming the member and its date. A DISCLOSURE ONLY - no cap, no
penalty - on every member's item, because a unit's reasons are shared.

EVERY EXPECTATION IS SPELLED BY HAND from the spec: the dates are literals, the sentence is the
brief's text and not `consistency.UNIT_DATE_CONFLICT`, and the objective sources are S10's list
(`exif`, `qt_local`, `qt_utc`), never the production constant. The catalog is read back with the
suite's own SQL. The fake exiftool reads `<path>.tags.json`; a file with no tags has no EXIF.
"""

import json
import os
import sqlite3
from datetime import datetime

import pytest
from archives import FAKE_COMMAND

from pam.catalog.db import connect
from pam.naming.planner import run_plan
from pam.paths import workspace_for
from pam.scan.runner import run_scan

MTIME = datetime(2024, 5, 1, 12, 0, 0)  # every file's mtime: a `file_mtime` date of 2024-05-01


def conflict(rel_path, date):
    """S10's sentence, spelled from the brief (Task 6 Step 1)."""
    return (
        f"Another file in this group, {rel_path}, was dated {date} by its camera"
        " - more than a year from the date in this name."
    )


# --------------------------------------------------------------------------- the harness


def archive(tmp_path, files, *, config=None):
    """`files`: rel -> fake-exiftool tags (None = no EXIF at all). Scanned once."""
    root = tmp_path / "photos"
    root.mkdir(parents=True)
    for rel, tags in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"the only copy of {rel}".encode())
        (root / f"{rel}.tags.json").write_text(
            json.dumps({**(tags or {}), "PamTestSidecar": rel}), encoding="utf-8"
        )
        os.utime(path, (MTIME.timestamp(), MTIME.timestamp()))
    run_scan(root, exiftool_command=FAKE_COMMAND)
    if config is not None:
        workspace_for(root).config_path.write_text(config, encoding="utf-8")
    return root


def _conn(root):
    conn = connect(workspace_for(root).db_path)
    conn.row_factory = sqlite3.Row
    return conn


def capture(root, rel):
    conn = _conn(root)
    try:
        row = conn.execute(
            "SELECT c.capture_dt, c.capture_dt_source FROM capture c"
            " JOIN files f ON f.id = c.file_id WHERE f.rel_path = ?",
            (rel,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, rel
    return row["capture_dt"], row["capture_dt_source"]


def plan(root, template=None):
    """`pam plan`; returns `{old_rel_path: item}`, read with the suite's own SQL."""
    summary = run_plan(root, template)
    assert summary.plan_id is not None
    conn = _conn(root)
    try:
        rows = conn.execute(
            "SELECT old_rel_path, generated_filename, unit_key, tier, trust_score,"
            " trust_reasons_json FROM plan_items WHERE plan_id = ?",
            (summary.plan_id,),
        ).fetchall()
    finally:
        conn.close()
    return {
        r["old_rel_path"]: {
            "name": r["generated_filename"],
            "unit": r["unit_key"],
            "tier": r["tier"],
            "score": r["trust_score"],
            "reasons": json.loads(r["trust_reasons_json"]),
        }
        for r in rows
    }


def exif(stamp):
    return {"DateTimeOriginal": stamp}


def raw_pair(tmp_path, jpg_stamp, nef_tags):
    """A RAW+JPEG unit: `IMG_0001.JPG` (the primary, design 5.4: display > RAW) dated
    `jpg_stamp` by EXIF, and `IMG_0001.NEF` with `nef_tags`."""
    root = archive(tmp_path, {"IMG_0001.JPG": exif(jpg_stamp), "IMG_0001.NEF": nef_tags})
    got = plan(root)
    assert set(got) == {"IMG_0001.JPG", "IMG_0001.NEF"}
    assert got["IMG_0001.JPG"]["unit"] == got["IMG_0001.NEF"]["unit"]  # one unit
    return root, got


def unit_conflicts(item):
    """The item's sentences of S10's shape - located by their fixed prefix, spelled here."""
    return [r for r in item["reasons"] if r.startswith("Another file in this group, ")]


# --------------------------------------------------------------------------- (a) M12 Q2


def test_Q2_an_ocr_dated_display_primary_over_a_raw_dated_2003_names_the_raw(tmp_path):
    """`m12.md` §4 Q2's construction: the PNG has no EXIF and is named by the date pam reads
    in it (1990-01-01); the RAW beside it was dated 2003 by its camera."""
    root = archive(
        tmp_path,
        {"Scans/print.png": None, "Scans/print.NEF": exif("2003:06:15 10:00:00")},
        config='[enrich.ocr]\nfolders = ["Scans"]\n',
    )
    assert capture(root, "Scans/print.NEF") == ("2003-06-15T10:00:00", "exif")
    conn = _conn(root)
    try:
        sha = conn.execute(
            "SELECT sha256 FROM files WHERE rel_path = 'Scans/print.png'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO inferred_facts (sha256, kind, value_json, confidence, model_name,"
            " model_version, generated_at) VALUES (?, 'ocr_text', ?, NULL, 'ppocr-v4',"
            " '0123456789ab', '2026-09-26T09:00:00')",
            (sha, json.dumps({"lines": [{"text": "1990-01-01", "score": 0.95}]})),
        )
        conn.commit()
    finally:
        conn.close()
    got = plan(root)
    assert got["Scans/print.png"]["name"] == "1990-01-01_001.png"
    assert got["Scans/print.NEF"]["name"] == "1990-01-01_001.NEF"
    expected = conflict("Scans/print.NEF", "2003-06-15")
    for rel in ("Scans/print.png", "Scans/print.NEF"):
        assert unit_conflicts(got[rel]) == [expected], rel


# --------------------------------------------------------------------------- the tolerance


def test_365_calendar_days_apart_is_NOT_more_than_a_year(tmp_path):
    """(b) The rule is `>`. 2019-03-01 to 2020-02-29 is 365 days. The times of day put the two
    instants MORE than 365 days apart - the rule reads calendar dates, so still no reason."""
    _, got = raw_pair(tmp_path, "2019:03:01 08:00:00", exif("2020:02:29 23:00:00"))
    assert got["IMG_0001.JPG"]["name"] == "2019-03-01_001.JPG"
    for item in got.values():
        assert unit_conflicts(item) == []


def test_366_calendar_days_apart_IS_more_than_a_year(tmp_path):
    """(c) 2019-03-01 to 2020-03-01 is 366 days."""
    _, got = raw_pair(tmp_path, "2019:03:01 08:00:00", exif("2020:03:01 08:00:00"))
    assert got["IMG_0001.JPG"]["name"] == "2019-03-01_001.JPG"
    assert unit_conflicts(got["IMG_0001.JPG"]) == [conflict("IMG_0001.NEF", "2020-03-01")]


def test_a_member_dated_EARLIER_than_the_name_is_compared_too(tmp_path):
    """The distance is absolute: a RAW 366 days BEFORE the name's date is reported."""
    _, got = raw_pair(tmp_path, "2020:03:01 08:00:00", exif("2019:03:01 08:00:00"))
    assert unit_conflicts(got["IMG_0001.JPG"]) == [conflict("IMG_0001.NEF", "2019-03-01")]


def test_the_reason_costs_nothing_its_tier_and_score_equal_an_agreeing_units(tmp_path):
    """(c2) A disclosure, not a `Flag`: no cap and no penalty."""
    _, disagree = raw_pair(tmp_path / "c", "2019:03:01 08:00:00", exif("2020:03:01 08:00:00"))
    _, agree = raw_pair(tmp_path / "e", "2019:03:01 08:00:00", exif("2019:03:01 08:00:01"))
    assert unit_conflicts(disagree["IMG_0001.JPG"]) != []
    assert unit_conflicts(agree["IMG_0001.JPG"]) == []
    for rel in ("IMG_0001.JPG", "IMG_0001.NEF"):
        assert (disagree[rel]["tier"], disagree[rel]["score"]) == (
            agree[rel]["tier"],
            agree[rel]["score"],
        )
    # Spelled from the spec, so the equality is not between two equally wrong values: an
    # EXIF-dated unit with nothing else in its name is `high` and loses no point.
    assert (agree["IMG_0001.JPG"]["tier"], agree["IMG_0001.JPG"]["score"]) == ("high", 100)


# --------------------------------------------------------------------------- objective only


def test_a_member_dated_by_its_FILENAME_five_years_away_is_not_compared(tmp_path):
    """(d) `filename` is not a camera's date. The NEF has no EXIF, so its date is its name's."""
    root = archive(
        tmp_path,
        {"IMG_20140301.JPG": exif("2019:03:01 08:00:00"), "IMG_20140301.NEF": None},
    )
    assert capture(root, "IMG_20140301.NEF") == ("2014-03-01T00:00:00", "filename")
    got = plan(root)
    assert got["IMG_20140301.JPG"]["name"] == "2019-03-01_001.JPG"
    for item in got.values():
        assert unit_conflicts(item) == []


def test_a_member_dated_by_its_MTIME_years_away_is_not_compared(tmp_path):
    """`file_mtime` is not a camera's date either (2024-05-01 against a 2019 name)."""
    root, got = raw_pair(tmp_path, "2019:03:01 08:00:00", None)
    assert capture(root, "IMG_0001.NEF") == ("2024-05-01T12:00:00", "file_mtime")
    for item in got.values():
        assert unit_conflicts(item) == []


# S10's objective sources besides `exif` (covered above), each with tags that produce it and
# the calendar date it yields. Written from design 5.5 and S10, independent of production.
VIDEO_SOURCES = [
    ("qt_utc", {"CreateDate": "2021:06:01 10:00:00"}, "2021-06-01"),
    ("qt_local", {"CreationDate": "2021:06:01 10:00:00-05:00"}, "2021-06-01"),
]
assert len(VIDEO_SOURCES) == 2  # an empty parametrize would SKIP, not fail


@pytest.mark.parametrize(("source", "tags", "date"), VIDEO_SOURCES, ids=lambda v: str(v))
def test_a_live_photos_video_dated_by_quicktime_is_compared(tmp_path, source, tags, date):
    root = archive(
        tmp_path, {"IMG_0001.JPG": exif("2019:03:01 08:00:00"), "IMG_0001.MOV": tags}
    )
    assert capture(root, "IMG_0001.MOV")[1] == source
    got = plan(root)
    assert got["IMG_0001.JPG"]["name"] == "2019-03-01_001.JPG"
    for item in got.values():
        assert unit_conflicts(item) == [conflict("IMG_0001.MOV", date)]


# --------------------------------------------------------------------------- (e) and (f)


def test_a_unit_whose_members_agree_has_no_reason(tmp_path):
    """(e)"""
    _, got = raw_pair(tmp_path, "2019:03:01 08:00:00", exif("2019:03:01 08:00:01"))
    for item in got.values():
        assert unit_conflicts(item) == []


def test_the_reason_is_on_EVERY_members_item(tmp_path):
    """(f) A unit is decided and named as one (design 6.3, 7.3): its reasons are too."""
    _, got = raw_pair(tmp_path, "2019:03:01 08:00:00", exif("2003:06:15 10:00:00"))
    expected = [conflict("IMG_0001.NEF", "2003-06-15")]
    assert unit_conflicts(got["IMG_0001.JPG"]) == expected
    assert unit_conflicts(got["IMG_0001.NEF"]) == expected


def test_a_name_with_NO_date_has_nothing_to_disagree_with(tmp_path):
    """Design 6.4 (S10's row): "a name with no date has nothing to disagree with". A template
    with no `{date}`, over a RAW+JPEG unit whose RAW's camera says 2003 and whose JPEG's says
    2019: the plan is built, and no member's item carries S10's sentence.

    The Task 6 review's M2468 (the no-date guard deleted) SURVIVED the whole suite: `pam plan`
    then raised a TypeError on exactly this unit, and no test planned a dated unit under a
    date-less template. This is the closing test."""
    root = archive(
        tmp_path,
        {
            "IMG_0001.JPG": exif("2019:03:01 08:00:00"),
            "IMG_0001.NEF": exif("2003:06:15 10:00:00"),
        },
    )
    got = plan(root, "{orig}_{seq}")
    assert got["IMG_0001.JPG"]["name"] == "IMG_0001_001.JPG"
    assert got["IMG_0001.JPG"]["unit"] == got["IMG_0001.NEF"]["unit"]  # one unit
    for item in got.values():
        assert unit_conflicts(item) == []
