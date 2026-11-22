"""M13 Task 5 (C2; design 5.1/6.1/6.4, S9 + H8): a name pam wrote is not evidence.

`m12.md` §2.1's R1, built end to end with the real commands: `pam plan`, `pam apply`, an
`os.utime` on the renamed file - content unchanged, the way a sync client or a restore touches
a file (Q5 widened: the writers include the OPERATING SYSTEM) - and `pam scan`. Before the fix
the touch made the scanner re-read the file and take pam's own proposal back as a `filename`
date. H8's reading: the filename step parses the name the file had BEFORE pam renamed it.

EVERY EXPECTATION IS SPELLED BY HAND from the spec - dates, sources, names, sentences - and the
catalog is read back with the suite's own SQL. The fake exiftool reads `<path>.tags.json`, so a
file with no sidecar tags (or one whose sidecar no longer matches its renamed path) has no EXIF:
its date comes from its name or its mtime, which is exactly the case S9 is about.
"""

import json
import os
import shutil
import sqlite3
from datetime import datetime

import pytest
from archives import FAKE_COMMAND, approve_and_apply

from pam.catalog import repo
from pam.catalog.db import connect
from pam.execute.undo import run_undo
from pam.naming.planner import run_plan
from pam.paths import workspace_for
from pam.scan.runner import run_scan

MTIME = datetime(2024, 5, 1, 12, 0, 0)  # every file's mtime before the first scan
TOUCH = datetime(2025, 3, 3, 12, 0, 0)  # the sync client's touch, after `pam apply`
TOUCHED = (
    "2025-03-03T12:00:00",
    "file_mtime",
)  # what the touched file is dated by, if not a name
SCANS = '[enrich.ocr]\nfolders = ["Scans"]\n'
# design 6.4's "video date ambiguous" sentence, as `pam` prints it.
AMBIGUOUS = (
    "Video time is recorded in UTC and the old filename suggests a different day - "
    "please check this one."
)


# --------------------------------------------------------------------------- the harness


def put(root, rel, *, tags=None, stamp=MTIME, content=None):
    """One archive file. `tags` -> its fake-exiftool sidecar; no tags means no EXIF at all."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if content is not None else f"the only copy of {rel}".encode())
    (root / f"{rel}.tags.json").write_text(
        json.dumps({**(tags or {}), "PamTestSidecar": rel}), encoding="utf-8"
    )
    touch(root / rel, stamp)


def touch(path, when):
    """An mtime-only change - the bytes are untouched, as a sync client leaves them."""
    os.utime(path, (when.timestamp(), when.timestamp()))


def scan(root):
    return run_scan(root, exiftool_command=FAKE_COMMAND)


def archive(tmp_path, files, *, config=None):
    root = tmp_path / "photos"
    root.mkdir()
    for rel, tags in files.items():
        put(root, rel, tags=tags)
    scan(root)
    if config is not None:
        workspace_for(root).config_path.write_text(config, encoding="utf-8")
    return root


def _conn(root):
    conn = connect(workspace_for(root).db_path)
    conn.row_factory = sqlite3.Row
    return conn


def capture(root, rel):
    """`(capture_dt, capture_dt_source)` of the file at `rel`, with the suite's own SQL."""
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


def file_id(root, rel):
    conn = _conn(root)
    try:
        return conn.execute("SELECT id FROM files WHERE rel_path = ?", (rel,)).fetchone()["id"]
    finally:
        conn.close()


def plan(root, template=None):
    """`pam plan`; returns `(plan_id, {old_rel_path: item})`, read with the suite's own SQL."""
    summary = run_plan(root, template)
    assert summary.plan_id is not None
    conn = _conn(root)
    try:
        rows = conn.execute(
            "SELECT old_rel_path, generated_filename, tokens_json, tier, trust_reasons_json"
            " FROM plan_items WHERE plan_id = ?",
            (summary.plan_id,),
        ).fetchall()
    finally:
        conn.close()
    return summary.plan_id, {
        r["old_rel_path"]: {
            "name": r["generated_filename"],
            "tokens": json.loads(r["tokens_json"]),
            "tier": r["tier"],
            "reasons": json.loads(r["trust_reasons_json"]),
        }
        for r in rows
    }


def apply_plan(root, template=None):
    """Plan, approve every item with the suite's own SQL, apply. Returns the plan's items."""
    plan_id, got = plan(root, template)
    summary = approve_and_apply(plan_id, root)
    assert summary.failed == 0 and summary.renamed == len(got)
    return summary, got


def rescan_touched(root, rel):
    """Q5's writer: touch the renamed file, then `pam scan`. It MUST re-read exactly that file."""
    touch(root / rel, TOUCH)
    summary = scan(root)
    assert (summary.changed, summary.read) == (1, 1)


# --------------------------------------------------------------------------- R1 and its twin


def test_R1_an_ocr_dated_name_pam_wrote_is_not_read_back_as_a_filename_date(tmp_path):
    """`m12.md` §2.1, the eight lines, with the fix's acceptance at steps 4 and 5."""
    root = archive(tmp_path, {"Scans/print.png": None}, config=SCANS)
    conn = _conn(root)
    try:
        sha = conn.execute(
            "SELECT sha256 FROM files WHERE rel_path = 'Scans/print.png'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO inferred_facts (sha256, kind, value_json, confidence, model_name,"
            " model_version, generated_at) VALUES (?, 'ocr_text', ?, NULL, 'ppocr-v4',"
            " '0123456789ab', '2026-09-26T09:00:00')",
            (sha, json.dumps({"lines": [{"text": "1990-01-01", "score": 0.8}]})),
        )
        conn.commit()
    finally:
        conn.close()
    # Steps 1-2: the OCR date names the file, and pam applies it.
    _, got = apply_plan(root)
    assert got["Scans/print.png"]["name"] == "1990-01-01_001.png"
    assert (root / "Scans" / "1990-01-01_001.png").exists()
    assert capture(root, "Scans/1990-01-01_001.png") == ("2024-05-01T12:00:00", "file_mtime")
    # Step 3, the control: nothing touched, nothing re-read (P2).
    assert scan(root).read == 0
    # Step 4: the touch. The name pam wrote is NOT evidence; the pre-pam name has no date.
    rescan_touched(root, "Scans/1990-01-01_001.png")
    assert capture(root, "Scans/1990-01-01_001.png") == TOUCHED
    # Step 5: a template change produces an item, and it is still the OCR date - `medium`,
    # its reasons shown, and in `attention` (design 8.2 rev 27: the date token's source_table
    # is `inferred_facts`).
    _, got = plan(root, "{date}")
    item = got["Scans/1990-01-01_001.png"]
    assert item["name"] == "1990-01-01.png"
    assert item["tier"] == "medium"
    (date,) = [t for t in item["tokens"] if t["resolved_from"] == "date"]
    assert (date["source_table"], date["value"]) == ("inferred_facts", "1990-01-01")
    assert item["reasons"] == [
        'pam read "1990-01-01" in the photo as 1990-01-01 (80% confidence).',
        "Date read from text in the photo - no camera or filename date.",
        "That reading is below pam's high-confidence mark ([tiers] ocr high = 0.9).",
    ]


def test_the_file_mtime_twin_a_dated_name_from_an_mtime_is_not_evidence(tmp_path):
    root = archive(tmp_path, {"IMG_0001.jpg": None})
    assert capture(root, "IMG_0001.jpg") == ("2024-05-01T12:00:00", "file_mtime")
    _, got = apply_plan(root)
    assert got["IMG_0001.jpg"]["name"] == "2024-05-01_001.jpg"
    rescan_touched(root, "2024-05-01_001.jpg")
    assert capture(root, "2024-05-01_001.jpg") == TOUCHED


def test_H8_a_filename_date_the_users_own_name_carried_survives_pams_rename(tmp_path):
    """M13-D3 / H8: the pre-pam name is the evidence, so its date is not lost."""
    root = archive(tmp_path, {"IMG_20190704_1.jpg": None})
    assert capture(root, "IMG_20190704_1.jpg") == ("2019-07-04T00:00:00", "filename")
    _, got = apply_plan(root)
    assert got["IMG_20190704_1.jpg"]["name"] == "2019-07-04_001.jpg"
    rescan_touched(root, "2019-07-04_001.jpg")
    assert capture(root, "2019-07-04_001.jpg") == ("2019-07-04T00:00:00", "filename")


# --------------------------------------------------------------------------- the user's names


def test_negative_control_a_name_the_USER_gave_a_file_pam_renamed_is_evidence(tmp_path):
    """The file has journal history, and the user renamed it after pam: that name is theirs."""
    root = archive(tmp_path, {"IMG_0001.jpg": None})
    apply_plan(root)
    os.rename(root / "2024-05-01_001.jpg", root / "2018-03-02_party.jpg")
    scan(root)
    assert capture(root, "2018-03-02_party.jpg") == ("2018-03-02T00:00:00", "filename")


def test_negative_control_a_name_the_USER_gave_a_file_pam_never_touched_is_evidence(tmp_path):
    root = archive(tmp_path, {"IMG_0001.jpg": None})
    os.rename(root / "IMG_0001.jpg", root / "2018-03-02_party.jpg")
    scan(root)
    assert capture(root, "2018-03-02_party.jpg") == ("2018-03-02T00:00:00", "filename")


def test_an_undone_rename_gives_the_name_back_to_the_user(tmp_path):
    """After `pam undo` the file is at the user's name again, and that name is evidence."""
    root = archive(tmp_path, {"IMG_20190704_1.jpg": None})
    summary, _ = apply_plan(root)
    undone = run_undo(summary.session_id, root)
    assert undone.failed == 0
    assert (root / "IMG_20190704_1.jpg").exists()
    rescan_touched(root, "IMG_20190704_1.jpg")
    assert capture(root, "IMG_20190704_1.jpg") == ("2019-07-04T00:00:00", "filename")


def test_undoing_the_SECOND_rename_of_a_chain_leaves_the_first_one_pams(tmp_path):
    """Plan A, then plan B, then `pam undo` of B: the file is back at A's name - still pam's.

    The undo cancels B's rename and ONLY B's. Ignore the cancellation and the map says the
    file belongs at B's name; it is at A's, so A's name - pam's own `2024-05-01` - would be
    read back as a `filename` date.
    """
    root = archive(tmp_path, {"IMG_0001.jpg": None})
    apply_plan(root)
    second, got = apply_plan(root, "{date}")
    assert got["2024-05-01_001.jpg"]["name"] == "2024-05-01.jpg"
    assert run_undo(second.session_id, root).failed == 0
    assert (root / "2024-05-01_001.jpg").exists()
    rescan_touched(root, "2024-05-01_001.jpg")
    assert capture(root, "2024-05-01_001.jpg") == TOUCHED


# --------------------------------------------------------------------------- chains and identity


def test_a_chain_resolves_to_the_name_the_file_had_before_pams_FIRST_rename(tmp_path):
    root = archive(tmp_path, {"IMG_0001.jpg": None})
    apply_plan(root)
    _, got = apply_plan(root, "{date}")
    assert got["2024-05-01_001.jpg"]["name"] == "2024-05-01.jpg"
    size = (root / "2024-05-01.jpg").stat().st_size
    conn = _conn(root)
    try:
        written = repo.pre_pam_names(conn)
    finally:
        conn.close()
    assert written == {
        file_id(root, "2024-05-01.jpg"): repo.PrePamName(
            pam_path="2024-05-01.jpg", pre_pam_path="IMG_0001.jpg", size=size
        )
    }
    rescan_touched(root, "2024-05-01.jpg")
    assert capture(root, "2024-05-01.jpg") == TOUCHED


def test_a_failed_rename_is_not_a_name_pam_wrote(tmp_path, set_fault):
    """Only `done` rows are names pam wrote. Plan B's rename crashes after its intent row and
    spec 7.2's recovery resolves that row `failed`: the file never left plan A's name, which is
    still pam's. Read the failed row as pam's latest name and A's name becomes evidence.

    (An `intent` row cannot be read here at all: `pam scan` and `pam plan` both run recovery,
    which resolves every one, before they read the journal.)
    """
    root = archive(tmp_path, {"IMG_0001.jpg": None})
    apply_plan(root)
    plan_id, _ = plan(root, "{date}")
    set_fault("crash:after_intent:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()
    rescan_touched(root, "2024-05-01_001.jpg")  # the scan's recovery runs first
    conn = _conn(root)
    try:
        states = conn.execute(
            "SELECT old_rel_path, new_rel_path, state FROM journal WHERE op = 'rename'"
            " ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert [tuple(r) for r in states] == [
        ("IMG_0001.jpg", "2024-05-01_001.jpg", "done"),
        ("2024-05-01_001.jpg", "2024-05-01.jpg", "failed"),
    ]
    assert capture(root, "2024-05-01_001.jpg") == TOUCHED


def test_a_DIFFERENT_file_at_pams_path_is_the_users_and_its_name_is_evidence(tmp_path):
    """A path is not an identity. pam's file leaves the archive and another file - another size
    - is put at that exact path. `files.rel_path` is UNIQUE, so it is the SAME `files` row (the
    same file_id) holding different bytes: only the journalled size tells them apart.
    """
    root = tmp_path / "photos"
    root.mkdir()
    put(root, "IMG_0001.jpg", stamp=datetime(2017, 1, 1, 12, 0, 0))
    scan(root)
    apply_plan(root)
    pam_path = root / "2017-01-01_001.jpg"
    before = file_id(root, "2017-01-01_001.jpg")
    old_size = pam_path.stat().st_size
    shutil.move(pam_path, tmp_path / "moved-away.jpg")
    pam_path.write_bytes(b"a different photograph, put here by the user, longer")
    assert pam_path.stat().st_size != old_size
    touch(pam_path, MTIME)
    scan(root)
    assert file_id(root, "2017-01-01_001.jpg") == before
    assert capture(root, "2017-01-01_001.jpg") == ("2017-01-01T00:00:00", "filename")


# --------------------------------------------------------------------------- the video rule


def test_the_video_rule_compares_with_the_name_before_pam_not_pams_own(tmp_path):
    """design 6.4 (rev 3, H8): a `qt_utc` date the pre-pam name disagrees with stays flagged.

    The UTC stamp is 2023-08-29; the camera's own name says 2023-08-28. pam names the video
    from the stamp, so its own name says 2023-08-29 - comparing the date with pam's name
    compares it with itself, and the flag would vanish on every re-plan after `pam apply`.
    """
    root = archive(tmp_path, {"VID_20230828_235959.MOV": {"CreateDate": "2023:08:29 06:59:59"}})
    assert capture(root, "VID_20230828_235959.MOV") == ("2023-08-29T06:59:59", "qt_utc")
    _, got = apply_plan(root)
    first = got["VID_20230828_235959.MOV"]
    assert first["name"] == "2023-08-29_001.MOV"
    assert AMBIGUOUS in first["reasons"] and first["tier"] == "low"
    _, got = plan(root, "{date}")
    again = got["2023-08-29_001.MOV"]
    assert again["name"] == "2023-08-29.MOV"
    assert AMBIGUOUS in again["reasons"]
    assert again["tier"] == "low"


def test_video_rule_control_a_pre_pam_name_with_the_SAME_day_is_not_flagged(tmp_path):
    root = archive(tmp_path, {"VID_20230829_065959.MOV": {"CreateDate": "2023:08:29 06:59:59"}})
    _, got = apply_plan(root)
    assert AMBIGUOUS not in got["VID_20230829_065959.MOV"]["reasons"]
    _, got = plan(root, "{date}")
    assert AMBIGUOUS not in got["2023-08-29_001.MOV"]["reasons"]


# --------------------------------------------------------------------------- the Task 5 review
# Four operands no test above could reach, each the closing test of a surviving mutation in
# `docs/mutations/m13.md` (M2440, M2450, M2451, M2452), plus the companion-unit construction.


def test_a_reverted_rename_is_not_a_name_pam_wrote(tmp_path, set_fault):
    """M2440. Plan B crashes between the two members of a unit; spec 7.2's recovery puts the
    first member back and resolves its row `reverted`. The file is at plan A's name - still
    pam's. Read the reverted row as pam's latest name and A's name becomes evidence.
    """
    root = archive(tmp_path, {"IMG_0001.JPG": None, "IMG_0001.MOV": None})
    apply_plan(root)
    plan_id, got = plan(root, "{date}")
    assert got["2024-05-01_001.JPG"]["name"] == "2024-05-01.JPG"
    set_fault("crash:mid_companion_unit:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()
    scan(root)  # recovery puts the unit back together at plan A's names
    conn = _conn(root)
    try:
        reverted = conn.execute(
            "SELECT old_rel_path, new_rel_path FROM journal"
            " WHERE op = 'rename' AND state = 'reverted'"
        ).fetchall()
    finally:
        conn.close()
    assert [tuple(r) for r in reverted] == [("2024-05-01_001.JPG", "2024-05-01.JPG")]
    rescan_touched(root, "2024-05-01_001.JPG")
    assert capture(root, "2024-05-01_001.JPG") == TOUCHED


def test_the_pre_pam_NAME_is_parsed_never_the_folder_it_sits_in(tmp_path):
    """M2450. H8 parses the name the file had before pam - its basename. The folder is the
    user's too, but it was never the filename step's input: before pam it gave no date."""
    root = archive(tmp_path, {"2019-07-04 party/IMG_0001.jpg": None})
    assert capture(root, "2019-07-04 party/IMG_0001.jpg") == (
        "2024-05-01T12:00:00",
        "file_mtime",
    )
    _, got = apply_plan(root)
    assert got["2019-07-04 party/IMG_0001.jpg"]["name"] == "2024-05-01_001.jpg"
    rescan_touched(root, "2019-07-04 party/2024-05-01_001.jpg")
    assert capture(root, "2019-07-04 party/2024-05-01_001.jpg") == TOUCHED


def test_the_video_rule_reads_the_pre_pam_name_in_a_subfolder_too(tmp_path):
    """M2451. The planner's identity check compares the primary's PATH, not its bare name."""
    root = archive(
        tmp_path, {"Phone/VID_20230828_235959.MOV": {"CreateDate": "2023:08:29 06:59:59"}}
    )
    apply_plan(root)
    _, got = plan(root, "{date}")
    again = got["Phone/2023-08-29_001.MOV"]
    assert again["name"] == "2023-08-29.MOV"
    assert AMBIGUOUS in again["reasons"]
    assert again["tier"] == "low"


def test_a_chain_checks_the_size_pam_journalled_at_its_LAST_rename(tmp_path):
    """M2452. The file is edited in place between plan A and plan B (an editor's save: new
    bytes, a new size), so plan B journals the NEW size. Plan B's name is pam's; the identity
    check must compare with B's size, not A's.

    (Between the edit and plan B, H8's size operand makes A's name the user's - `filename`
    2024-05-01 - which is why plan B names the file from it. That is H8 as adopted.)
    """
    root = archive(tmp_path, {"IMG_0001.jpg": None})
    apply_plan(root)
    path = root / "2024-05-01_001.jpg"
    path.write_bytes(path.read_bytes() + b", edited in place and saved longer")
    touch(path, MTIME)
    scan(root)
    _, got = apply_plan(root, "{date}")
    assert got["2024-05-01_001.jpg"]["name"] == "2024-05-01.jpg"
    rescan_touched(root, "2024-05-01.jpg")
    assert capture(root, "2024-05-01.jpg") == TOUCHED


def test_every_member_of_a_unit_pam_renamed_together_is_judged_alike(tmp_path):
    """Vision S3 / the construction hunt: a unit's members are renamed by one plan, each with
    its own journal row. Touch BOTH and re-scan: neither member reads pam's name back."""
    root = archive(tmp_path, {"IMG_0001.JPG": None, "IMG_0001.MOV": None})
    _, got = apply_plan(root)
    assert {got[k]["name"] for k in ("IMG_0001.JPG", "IMG_0001.MOV")} == {
        "2024-05-01_001.JPG",
        "2024-05-01_001.MOV",
    }
    touch(root / "2024-05-01_001.JPG", TOUCH)
    touch(root / "2024-05-01_001.MOV", TOUCH)
    summary = scan(root)
    assert (summary.changed, summary.read) == (2, 2)
    assert capture(root, "2024-05-01_001.JPG") == TOUCHED
    assert capture(root, "2024-05-01_001.MOV") == TOUCHED
