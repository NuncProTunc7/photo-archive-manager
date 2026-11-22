import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from pam.catalog.db import connect
from pam.paths import workspace_for
from pam.scan import companions
from pam.scan.exiftool import FROZEN_ARGS_IMAGE, FROZEN_ARGS_VIDEO
from pam.scan.hashing import sha256_file as real_sha256
from pam.scan.runner import run_scan

FAKE = Path(__file__).parent / "fake_exiftool.py"
FAKE_COMMAND = [sys.executable, str(FAKE)]


def make_archive(tmp_path):
    root = tmp_path / "photos"
    (root / "2019").mkdir(parents=True)
    (root / "2019" / "IMG_0001.jpg").write_bytes(b"jpeg-bytes-1")
    (root / "2019" / "IMG_0001.cr2").write_bytes(b"raw-bytes-1")
    (root / "2019" / "IMG_0002.jpg").write_bytes(b"jpeg-bytes-2")
    (root / "2019" / "IMG_0002.jpg.tags.json").write_text(
        json.dumps({"DateTimeOriginal": "2019:07:04 18:23:11", "Make": "Canon"}),
        encoding="utf-8",
    )
    (root / "clip.mp4").write_bytes(b"video")
    (root / "notes.txt").write_bytes(b"text")
    return root


def db_rows(root, sql, params=()):
    conn = connect(workspace_for(root).db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_scan_populates_catalog(tmp_path):
    root = make_archive(tmp_path)
    summary = run_scan(root, exiftool_command=FAKE_COMMAND)
    assert summary.total == 6
    assert (summary.images, summary.videos, summary.other) == (3, 1, 2)
    assert summary.new == 6 and summary.read == 4 and summary.errors == []
    rows = {r["rel_path"]: r for r in db_rows(root, "SELECT * FROM files")}
    assert set(rows) == {
        "2019/IMG_0001.jpg",
        "2019/IMG_0001.cr2",
        "2019/IMG_0002.jpg",
        "2019/IMG_0002.jpg.tags.json",
        "clip.mp4",
        "notes.txt",
    }
    assert all(r["status"] == "present" for r in rows.values())
    expected_sha = hashlib.sha256(b"jpeg-bytes-1").hexdigest()
    assert rows["2019/IMG_0001.jpg"]["sha256"] == expected_sha
    assert rows["clip.mp4"]["sha256"] == hashlib.sha256(b"video").hexdigest()
    assert rows["notes.txt"]["sha256"] is None


def test_capture_rows_written_with_date_sources(tmp_path):
    root = make_archive(tmp_path)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert len(db_rows(root, "SELECT * FROM capture")) == 4  # one per image + video
    with_exif = db_rows(
        root,
        "SELECT c.* FROM capture c JOIN files f ON f.id = c.file_id WHERE f.rel_path = ?",
        ("2019/IMG_0002.jpg",),
    )[0]
    assert with_exif["capture_dt"] == "2019-07-04T18:23:11"
    assert with_exif["capture_dt_source"] == "exif"
    assert with_exif["camera_make"] == "Canon"
    without_exif = db_rows(
        root,
        "SELECT c.* FROM capture c JOIN files f ON f.id = c.file_id WHERE f.rel_path = ?",
        ("2019/IMG_0001.jpg",),
    )[0]
    assert without_exif["capture_dt_source"] == "file_mtime"


def test_companion_group_written(tmp_path):
    root = make_archive(tmp_path)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    rows = {r["rel_path"]: r for r in db_rows(root, "SELECT * FROM files")}
    group_id = rows["2019/IMG_0001.jpg"]["companion_group_id"]
    assert group_id is not None
    assert rows["2019/IMG_0001.cr2"]["companion_group_id"] == group_id
    assert rows["2019/IMG_0002.jpg"]["companion_group_id"] is None
    group = db_rows(root, "SELECT * FROM companion_groups WHERE id = ?", (group_id,))[0]
    assert group["kind"] == "raw_jpeg"
    assert group["primary_file_id"] == rows["2019/IMG_0001.jpg"]["id"]


def test_rescan_is_all_unchanged_and_does_zero_work(tmp_path, monkeypatch):
    # The defining M2 property (P2 gate): an unchanged re-scan re-reads nothing.
    root = make_archive(tmp_path)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    before = [tuple(r) for r in db_rows(root, "SELECT * FROM capture ORDER BY file_id")]

    def forbidden_hash(path):
        raise AssertionError("unchanged files must never be re-hashed")

    class ForbiddenExifTool:
        def __init__(self, *args, **kwargs):
            raise AssertionError("exiftool must not start when nothing changed")

    monkeypatch.setattr("pam.scan.runner.sha256_file", forbidden_hash)
    monkeypatch.setattr("pam.scan.runner.ExifTool", ForbiddenExifTool)
    summary = run_scan(root, exiftool_command=FAKE_COMMAND)
    assert summary.unchanged == 6 and summary.read == 0
    assert summary.new == 0 and summary.changed == 0 and summary.missing == 0
    after = [tuple(r) for r in db_rows(root, "SELECT * FROM capture ORDER BY file_id")]
    assert after == before  # capture rows byte-for-byte untouched


def test_changed_file_rehashed_reextracted_and_restatted(tmp_path):
    root = make_archive(tmp_path)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    target = root / "2019" / "IMG_0002.jpg"
    target.write_bytes(b"totally-new-bytes")
    st = target.stat()
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    (root / "2019" / "IMG_0002.jpg.tags.json").write_text(
        json.dumps({"DateTimeOriginal": "2021:12:25 09:00:00"}), encoding="utf-8"
    )
    summary = run_scan(root, exiftool_command=FAKE_COMMAND)
    # 2 changed: the image and its rewritten .tags.json fixture file.
    assert summary.changed == 2 and summary.read == 1
    row = db_rows(
        root,
        "SELECT * FROM files WHERE rel_path = ?",
        ("2019/IMG_0002.jpg",),
    )[0]
    assert row["sha256"] == hashlib.sha256(b"totally-new-bytes").hexdigest()
    assert row["mtime_ns"] == target.stat().st_mtime_ns
    cap = db_rows(
        root,
        "SELECT c.* FROM capture c JOIN files f ON f.id = c.file_id WHERE f.rel_path = ?",
        ("2019/IMG_0002.jpg",),
    )[0]
    assert cap["capture_dt"] == "2021-12-25T09:00:00"  # re-extracted, not stale


def test_deleted_file_marked_missing(tmp_path):
    root = make_archive(tmp_path)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    (root / "notes.txt").unlink()
    summary = run_scan(root, exiftool_command=FAKE_COMMAND)
    assert summary.missing == 1 and summary.total == 5
    row = db_rows(root, "SELECT status FROM files WHERE rel_path = ?", ("notes.txt",))[0]
    assert row["status"] == "missing"


def test_placeholder_files_are_flagged_never_read(tmp_path, monkeypatch):
    root = tmp_path / "photos"
    root.mkdir()
    (root / "cloud.jpg").write_bytes(b"do-not-read")
    monkeypatch.setattr("pam.scan.walker.is_placeholder", lambda st: True)

    def forbidden(path):
        raise AssertionError("placeholders must never be hashed")

    monkeypatch.setattr("pam.scan.runner.sha256_file", forbidden)
    summary = run_scan(root, exiftool_command=FAKE_COMMAND)
    assert summary.placeholders == 1 and summary.read == 0
    row = db_rows(root, "SELECT * FROM files")[0]
    assert row["placeholder"] == 1 and row["sha256"] is None
    assert db_rows(root, "SELECT * FROM capture") == []


def test_crash_mid_scan_loses_at_most_one_file(tmp_path, monkeypatch):
    # Vision P1: per-file commits mean a crash costs at most the in-flight file,
    # and a restart converges without redoing completed work.
    root = make_archive(tmp_path)
    calls = []

    def crashing(path):
        calls.append(path)
        if len(calls) == 2:
            raise RuntimeError("simulated crash (power loss)")
        return real_sha256(path)

    monkeypatch.setattr("pam.scan.runner.sha256_file", crashing)
    with pytest.raises(RuntimeError):
        run_scan(root, exiftool_command=FAKE_COMMAND)

    rescanned = []

    def recording(path):
        rescanned.append(str(path).replace("\\", "/"))
        return real_sha256(path)

    monkeypatch.setattr("pam.scan.runner.sha256_file", recording)
    summary = run_scan(root, exiftool_command=FAKE_COMMAND)
    # File 1 of 4 committed before the crash: it is skipped on restart.
    # The crashed file and the never-reached file are re-processed.
    assert summary.changed == 3 and summary.unchanged == 3
    assert summary.new == 0 and summary.errors == []
    assert len(rescanned) == 3
    assert not any(p.endswith("IMG_0001.cr2") for p in rescanned)
    assert db_rows(root, "SELECT COUNT(*) AS n FROM capture")[0]["n"] == 4
    assert (
        db_rows(root, "SELECT COUNT(*) AS n FROM files WHERE sha256 IS NULL")[0]["n"]
        == 2  # only txt/tags.json legitimately stay unhashed (videos hash now)
    )


def test_mixed_media_batches_read_every_file(tmp_path, monkeypatch):
    # C13: batching by media type must not drop a file at a batch boundary.
    root = tmp_path / "mixed"
    root.mkdir()
    for i in range(120):
        (root / f"img_{i:03d}.jpg").write_bytes(b"x")
    for i in range(120):
        (root / f"clip_{i:03d}.mp4").write_bytes(b"v")
    summary = run_scan(root, exiftool_command=FAKE_COMMAND)
    assert summary.read == 240
    assert db_rows(root, "SELECT COUNT(*) AS n FROM capture")[0]["n"] == 240
    assert db_rows(root, "SELECT COUNT(*) AS n FROM files WHERE sha256 IS NULL")[0]["n"] == 0
    # C13 end to end (finding 5): the frozen args are per media type, and the fake
    # echoes the argument lines it actually saw into the record, which lands
    # verbatim in capture.exif_json. -fast2 silently returns NO QuickTime metadata
    # when moov follows mdat - 193 of 194 real videos - so a video read with the
    # image args loses its date with no error anywhere. Nothing else in the suite
    # proves the runner wires the right arg list to the right batch.
    args_for = {
        row["rel_path"]: json.loads(row["exif_json"])["PamFakeArgs"]
        for row in db_rows(
            root,
            "SELECT f.rel_path, c.exif_json FROM capture c JOIN files f ON f.id = c.file_id"
            " WHERE f.rel_path IN ('img_000.jpg', 'clip_000.mp4')",
        )
    }
    assert args_for["img_000.jpg"] == list(FROZEN_ARGS_IMAGE)
    assert args_for["clip_000.mp4"] == list(FROZEN_ARGS_VIDEO)


def make_video_archive(tmp_path):
    root = tmp_path / "videos"
    root.mkdir()
    (root / "beach.mp4").write_bytes(b"mp4-bytes")
    (root / "beach.mp4.tags.json").write_text(
        json.dumps({"CreateDate": "2019:07:05 01:23:11"}), encoding="utf-8"
    )
    (root / "IMG_0007.heic").write_bytes(b"heic-bytes")
    (root / "IMG_0007.mov").write_bytes(b"live-photo-video")
    return root


def test_video_hashed_and_capture_written(tmp_path):
    root = make_video_archive(tmp_path)
    summary = run_scan(root, exiftool_command=FAKE_COMMAND)
    assert summary.read == 3  # heic + mp4 + mov
    row = db_rows(root, "SELECT * FROM files WHERE rel_path = ?", ("beach.mp4",))[0]
    assert row["sha256"] == hashlib.sha256(b"mp4-bytes").hexdigest()
    cap = db_rows(
        root,
        "SELECT c.* FROM capture c JOIN files f ON f.id = c.file_id WHERE f.rel_path = ?",
        ("beach.mp4",),
    )[0]
    assert cap["capture_dt"] == "2019-07-05T01:23:11"
    assert cap["capture_dt_source"] == "qt_utc"
    assert cap["tz_offset_min"] is None


def test_pre_rev3_video_rows_are_backfilled_once(tmp_path):
    # Simulates a catalog scanned before the rev 3 amendment: the video row
    # exists but was never hashed and has no capture row. One scan backfills
    # it; the next is zero-work again (P2 preserved).
    root = make_video_archive(tmp_path)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    conn = connect(workspace_for(root).db_path)
    try:
        conn.execute("UPDATE files SET sha256 = NULL WHERE rel_path = ?", ("beach.mp4",))
        conn.execute(
            "DELETE FROM capture WHERE file_id = (SELECT id FROM files WHERE rel_path = ?)",
            ("beach.mp4",),
        )
        conn.commit()
    finally:
        conn.close()
    summary = run_scan(root, exiftool_command=FAKE_COMMAND)
    assert summary.changed == 1 and summary.read == 1  # only the backfilled video
    assert db_rows(root, "SELECT COUNT(*) AS n FROM capture")[0]["n"] == 3
    summary = run_scan(root, exiftool_command=FAKE_COMMAND)
    assert summary.changed == 0 and summary.read == 0


def test_placeholder_video_never_read(tmp_path, monkeypatch):
    root = tmp_path / "videos"
    root.mkdir()
    (root / "cloud.mp4").write_bytes(b"do-not-read")
    monkeypatch.setattr("pam.scan.walker.is_placeholder", lambda st: True)

    def forbidden(path):
        raise AssertionError("placeholder videos must never be hashed")

    monkeypatch.setattr("pam.scan.runner.sha256_file", forbidden)
    summary = run_scan(root, exiftool_command=FAKE_COMMAND)
    assert summary.placeholders == 1 and summary.read == 0


# --- M5 Task 2 Step 4: a scan that changes the catalog supersedes open plans ---


def plan_statuses(root):
    return {r["id"]: r["status"] for r in db_rows(root, "SELECT id, status FROM plans")}


def acr_archive(tmp_path):
    """The real-archive shape: an ACR pair that only groups once configured."""
    root = tmp_path / "photos"
    root.mkdir()
    (root / "DSC_3579.NEF").write_bytes(b"raw")
    (root / "DSC_3579.acr_.jpg").write_bytes(b"jpeg")
    (root / "DSC_3579.NEF.tags.json").write_text(
        json.dumps({"DateTimeOriginal": "2026:07:01 10:00:00"}), encoding="utf-8"
    )
    (root / "DSC_3579.acr_.jpg.tags.json").write_text(
        json.dumps({"DateTimeOriginal": "2026:07:01 10:00:01"}), encoding="utf-8"
    )
    return root


def test_a_scan_that_adds_a_file_supersedes_an_open_plan(tmp_path):
    # spec 5.1 rev 6: an open plan is a statement about a catalog state, and this
    # scan just changed that state.
    from pam.naming.planner import run_plan

    root = make_archive(tmp_path)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    assert plan_statuses(root) == {plan_id: "open"}

    (root / "2019" / "IMG_0003.jpg").write_bytes(b"jpeg-bytes-3")
    summary = run_scan(root, exiftool_command=FAKE_COMMAND)
    assert summary.new == 1
    assert plan_statuses(root) == {plan_id: "superseded"}


def test_a_scan_that_loses_a_file_supersedes_an_open_plan(tmp_path):
    from pam.naming.planner import run_plan

    root = make_archive(tmp_path)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id

    (root / "2019" / "IMG_0002.jpg").unlink()
    summary = run_scan(root, exiftool_command=FAKE_COMMAND)
    assert summary.missing == 1
    assert plan_statuses(root) == {plan_id: "superseded"}


def test_a_config_only_regroup_supersedes_with_no_file_changed(tmp_path):
    # Found on a real-world library, and a file-change-only trigger
    # misses it entirely. Grouping depends on scan.ignore_stem_suffixes, so editing
    # pam.toml re-groups files without one byte changing on disk. The open plan
    # gave DSC_3579.NEF and DSC_3579.acr_.jpg DIFFERENT stems; afterwards they are
    # one raw_jpeg unit, so applying that plan would split the group (vision S3).
    from pam.naming.planner import run_plan

    root = acr_archive(tmp_path)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    stems = {
        r["old_rel_path"]: r["generated_filename"].rsplit(".", 1)[0]
        for r in db_rows(root, "SELECT old_rel_path, generated_filename FROM plan_items")
    }
    assert stems["DSC_3579.NEF"] != stems["DSC_3579.acr_.jpg"], (
        "the fixture must start with the two files in SEPARATE units"
    )

    workspace_for(root).config_path.write_text(
        '[scan]\nignore_stem_suffixes = [".acr_"]\n', encoding="utf-8"
    )
    summary = run_scan(root, exiftool_command=FAKE_COMMAND)

    assert (summary.new, summary.changed, summary.missing) == (0, 0, 0)
    assert summary.read == 0  # nothing on disk changed: this is a config-only regroup
    rows = {r["rel_path"]: r for r in db_rows(root, "SELECT * FROM files")}
    assert (
        rows["DSC_3579.NEF"]["companion_group_id"]
        == rows["DSC_3579.acr_.jpg"]["companion_group_id"]
        is not None
    )
    assert plan_statuses(root) == {plan_id: "superseded"}


def test_a_config_only_ungroup_supersedes_too(tmp_path):
    # The mirror image: removing the suffix splits an existing unit. Membership
    # comparison has to notice a group DISAPPEARING as well as one appearing.
    from pam.naming.planner import run_plan

    root = acr_archive(tmp_path)
    config_path = workspace_for(root).config_path
    run_scan(root, exiftool_command=FAKE_COMMAND)
    config_path.write_text('[scan]\nignore_stem_suffixes = [".acr_"]\n', encoding="utf-8")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id

    config_path.write_text("[scan]\nignore_stem_suffixes = []\n", encoding="utf-8")
    summary = run_scan(root, exiftool_command=FAKE_COMMAND)
    assert (summary.new, summary.changed, summary.missing) == (0, 0, 0)
    assert plan_statuses(root) == {plan_id: "superseded"}


def test_a_regroup_that_keeps_the_group_count_still_supersedes(tmp_path):
    # spec 5.1 rev 6 says MEMBERSHIP, and this is the shape that tells membership
    # apart from every cheaper proxy: one group before, one group after, same kind,
    # different members. Comparing group counts (or ids) passes this and misses a
    # regroup that would split a unit at apply time. Mutation M18 lived here.
    from pam.naming.planner import run_plan

    root = tmp_path / "photos"
    root.mkdir()
    (root / "IMG_1.jpg").write_bytes(b"j")
    (root / "IMG_1.aae").write_bytes(b"a")
    (root / "IMG_1.acr_.png").write_bytes(b"p")
    (root / "IMG_1.jpg.tags.json").write_text(
        json.dumps({"DateTimeOriginal": "2026:07:01 10:00:00"}), encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    before = db_rows(root, "SELECT id FROM companion_groups")
    assert len(before) == 1

    workspace_for(root).config_path.write_text(
        '[scan]\nignore_stem_suffixes = [".acr_"]\n', encoding="utf-8"
    )
    summary = run_scan(root, exiftool_command=FAKE_COMMAND)

    assert (summary.new, summary.changed, summary.missing) == (0, 0, 0)
    after = db_rows(root, "SELECT id, kind FROM companion_groups")
    assert len(after) == len(before)  # the count is unchanged: only members moved
    assert after[0]["kind"] == "sidecar"
    members = {
        r["rel_path"]
        for r in db_rows(
            root, "SELECT rel_path FROM files WHERE companion_group_id IS NOT NULL"
        )
    }
    assert members == {"IMG_1.jpg", "IMG_1.aae", "IMG_1.acr_.png"}
    assert plan_statuses(root) == {plan_id: "superseded"}


def test_a_scan_that_rewrites_a_files_bytes_supersedes_an_open_plan(tmp_path):
    """The `changed` operand of the file-change trigger, on its own.

    Mutation M30 (M5 Task 2 review) dropped `summary.changed` from the trigger and
    the whole suite stayed green: the add-a-file test pinned `new`, the lose-a-file
    test pinned `missing`, and nothing pinned `changed`. It is not the weakest of
    the three - rewriting a file's bytes re-extracts its capture facts, so the open
    plan's proposed name can be derived from a date the file no longer has.

    Nothing here is added, lost or regrouped, so `changed` is the only operand that
    can fire; the companion membership assertion below is what proves that.
    """
    from pam.naming.planner import run_plan

    root = make_archive(tmp_path)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    groups_sql = "SELECT rel_path, companion_group_id FROM files ORDER BY rel_path"
    groups_before = [tuple(r) for r in db_rows(root, groups_sql)]

    target = root / "2019" / "IMG_0002.jpg"
    target.write_bytes(b"re-edited-bytes")
    st = target.stat()
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))

    summary = run_scan(root, exiftool_command=FAKE_COMMAND)
    assert (summary.new, summary.missing) == (0, 0) and summary.changed == 1
    assert [tuple(r) for r in db_rows(root, groups_sql)] == groups_before  # no regroup
    assert plan_statuses(root) == {plan_id: "superseded"}


def test_a_crash_before_the_final_commit_still_supersedes(tmp_path, monkeypatch):
    """M5 Task 2 review, finding I1: the supersede must live in the SAME transaction
    as the writes that justify it.

    The window being interrupted: _scan commits four times. summary.new / changed /
    missing become durable at the file-state and upsert commits (and the per-file
    commits inside the read loop); the companion writes and the final commit come
    ~90 lines later. Here the scan is crashed inside that window - after
    IMG_0003.jpg has been upserted, hashed and its capture row committed, before
    group_companions runs.

    Re-scanning cannot heal this. IMG_0003.jpg is now recorded, hashed and complete,
    so walker.diff_catalog calls it `unchanged` and the file-change trigger is False
    forever after; it joined no companion group, so the grouping trigger is False
    too. With the supersede in the last transaction the plan stays `open`
    permanently, and M5 has no independent "has the catalog moved?" check to catch
    it - the supersede IS that check.
    """
    from pam.naming.planner import run_plan

    root = make_archive(tmp_path)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    assert plan_statuses(root) == {plan_id: "open"}

    real_group_companions = companions.group_companions
    crashed = []

    def crash_once(*args, **kwargs):
        # Crash the FIRST scan only; the re-scan below must run for real. (Do not
        # use monkeypatch.undo() here - it would also undo conftest's PAM_HOME.)
        if not crashed:
            crashed.append(True)
            raise RuntimeError("simulated crash (power loss) after the per-file commits")
        return real_group_companions(*args, **kwargs)

    (root / "2019" / "IMG_0003.jpg").write_bytes(b"jpeg-bytes-3")
    monkeypatch.setattr("pam.scan.runner.companions.group_companions", crash_once)
    with pytest.raises(RuntimeError):
        run_scan(root, exiftool_command=FAKE_COMMAND)

    # The crashed scan's file work IS durable - that is the whole asymmetry.
    row = db_rows(
        root, "SELECT status, sha256 FROM files WHERE rel_path = ?", ("2019/IMG_0003.jpg",)
    )[0]
    assert row["status"] == "present" and row["sha256"] is not None

    summary = run_scan(root, exiftool_command=FAKE_COMMAND)
    assert (summary.new, summary.changed, summary.missing) == (0, 0, 0)  # nothing to notice
    assert plan_statuses(root) == {plan_id: "superseded"}  # converged, not open


def test_a_zero_work_rescan_does_not_supersede(tmp_path, monkeypatch):
    # The P2 guard. Neither files nor grouping changed, so the membership
    # comparison must be a true no-op - and supersede_open_plans must not even be
    # reached, let alone write. Group ids are stable across a plain re-scan.
    from pam.naming.planner import run_plan

    root = make_archive(tmp_path)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    groups_sql = "SELECT rel_path, companion_group_id FROM files ORDER BY id"
    groups_before = [tuple(r) for r in db_rows(root, groups_sql)]

    def forbidden(conn):
        raise AssertionError("a zero-work re-scan must not supersede anything")

    monkeypatch.setattr("pam.catalog.repo.supersede_open_plans", forbidden)
    summary = run_scan(root, exiftool_command=FAKE_COMMAND)

    assert (summary.new, summary.changed, summary.missing, summary.read) == (0, 0, 0, 0)
    assert plan_statuses(root) == {plan_id: "open"}
    assert [tuple(r) for r in db_rows(root, groups_sql)] == groups_before


@pytest.mark.parametrize("status", ["applied", "partially_applied", "undone"])
def test_a_scan_never_supersedes_a_terminal_plan(tmp_path, status):
    # spec 5.1/6.3.5: applied plans are never touched, by either path. The repo SQL
    # is an allowlist on status = 'open', which is what makes this true for every
    # status the spec 4 enum gains later.
    from pam.catalog import repo
    from pam.naming.planner import run_plan

    root = acr_archive(tmp_path)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    conn = connect(workspace_for(root).db_path)
    try:
        repo.set_plan_status(conn, plan_id, status)
        conn.commit()
    finally:
        conn.close()

    (root / "IMG_NEW.jpg").write_bytes(b"new")  # the file-change path
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert plan_statuses(root) == {plan_id: status}

    workspace_for(root).config_path.write_text(  # the grouping path
        '[scan]\nignore_stem_suffixes = [".acr_"]\n', encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert plan_statuses(root) == {plan_id: status}


# --- exiftool said something about the file (M7 Task 5; spec 13 rev 6) ----------------


def record_archive(tmp_path, records):
    """An archive of real files, each with the exiftool record the fake will return.

    The fake exiftool merges `<path>.tags.json` into the record it emits, so a test can
    hand the scanner the EXACT record real exiftool produces for a file it could not
    identify - `{"Error": "File is empty"}`, with no `FileType` and no tags - and drive
    the whole scan through it. Building the record rather than the corrupt bytes is
    deliberate: what is under test is what pam does with exiftool's answer, and pinning
    that to a particular exiftool build's parser would test exiftool instead.
    """
    root = tmp_path / "photos"
    root.mkdir()
    for name, record in records.items():
        (root / name).write_bytes(f"the only copy of {name}".encode())
        (root / f"{name}.tags.json").write_text(json.dumps(record), encoding="utf-8")
    return root


def test_an_exiftool_error_is_recorded_counted_and_named(tmp_path):
    """spec 13 rev 6: a per-file error is recorded, counted and continued past.

    The text is exiftool's own, verbatim after the path, exactly as the walk errors and
    the hashing `OSError`s beside it - so the exit code, the '!' summary line and the
    log entry all come from the one list that already carries them.
    """
    root = record_archive(
        tmp_path,
        {
            "empty.jpg": {"Error": "File is empty"},
            "good.jpg": {"DateTimeOriginal": "2019:07:04 18:23:11"},
        },
    )

    summary = run_scan(root, exiftool_command=FAKE_COMMAND)

    assert summary.errors == ["empty.jpg: File is empty"]
    assert summary.had_errors


def test_a_file_exiftool_errored_on_is_still_inventoried_hashed_and_captured(tmp_path):
    """ "Skipped" is a REPORT classification, not a catalog status.

    Nothing else about the file changes: it keeps its `files` row, it is still hashed
    (the bytes are readable even when the format is not), and `build_capture` still runs
    the spec 6.1 hierarchy over the record and lands on `file_mtime`. Suppressing any of
    those would make a file pam can see vanish from the catalog, which is a far worse
    failure than the one this task fixes - and would need a schema change nobody
    authorised.
    """
    root = record_archive(tmp_path, {"empty.jpg": {"Error": "File is empty"}})

    run_scan(root, exiftool_command=FAKE_COMMAND)

    files = {r["rel_path"]: r for r in db_rows(root, "SELECT * FROM files")}
    assert files["empty.jpg"]["status"] == "present"
    assert files["empty.jpg"]["sha256"] is not None, "readable bytes are still hashed"
    rows = db_rows(
        root,
        "SELECT c.capture_dt_source FROM capture c JOIN files f ON f.id = c.file_id"
        " WHERE f.rel_path = ?",
        ("empty.jpg",),
    )
    assert [r["capture_dt_source"] for r in rows] == ["file_mtime"]


def test_a_warning_only_record_is_not_an_error_and_goes_to_the_log(tmp_path):
    """The half of this that is load-bearing rather than defensive.

    About half of the capture rows in a real-world library carry a `Warning` and every
    one of those files got its date. A reader that treated a warning as an error would
    make an ordinary scan exit 1 on half an archive and call those files unreadable, so
    the warning changes NO behaviour - it goes to the session log at debug level and
    nowhere else. Both halves are asserted here: no error, and the text really is in the
    log rather than merely not on the console.
    """
    root = record_archive(
        tmp_path,
        {
            "damaged.jpg": {"FileType": "JPEG", "Warning": "JPEG format error"},
            "chatty.MOV": {
                "CreateDate": "2023:08:28 11:59:43",
                "Warning": "[minor] The ExtractEmbedded option may find more tags",
            },
        },
    )

    summary = run_scan(root, exiftool_command=FAKE_COMMAND)

    assert summary.errors == []
    assert not summary.had_errors
    assert summary.log_path is not None
    log = summary.log_path.read_text(encoding="utf-8")
    assert "DEBUG" in log
    assert "damaged.jpg: JPEG format error" in log
    assert "chatty.MOV: [minor] The ExtractEmbedded option may find more tags" in log
    # And the warned-about video kept the date it really has: a warning is a partial
    # read, not a failed one.
    rows = db_rows(
        root,
        "SELECT c.capture_dt, c.capture_dt_source FROM capture c"
        " JOIN files f ON f.id = c.file_id WHERE f.rel_path = ?",
        ("chatty.MOV",),
    )
    assert [(r["capture_dt"], r["capture_dt_source"]) for r in rows] == [
        ("2023-08-28T11:59:43", "qt_utc")
    ]


def test_a_record_with_neither_key_is_untouched(tmp_path):
    """The negative case, so the new reader cannot pass by flagging everything."""
    root = record_archive(tmp_path, {"good.jpg": {"DateTimeOriginal": "2019:07:04 18:23:11"}})

    summary = run_scan(root, exiftool_command=FAKE_COMMAND)

    assert summary.errors == []
    assert summary.read == 1
    log = summary.log_path.read_text(encoding="utf-8") if summary.log_path else ""
    assert "exiftool warning" not in log and "exiftool error" not in log


def test_an_error_and_a_no_record_file_are_counted_separately(tmp_path):
    """Two different failures that must not be collapsed into one message.

    `unreadable.jpg` is the fake exiftool's contract for "no record came back at all",
    which is what a crashed or confused exiftool leaves behind; an `Error` record is
    exiftool answering, and saying what is wrong. The user gets exiftool's own words in
    the second case and pam's in the first, and both files exit 1.
    """
    root = record_archive(
        tmp_path,
        {"empty.jpg": {"Error": "File is empty"}, "unreadable.jpg": {}},
    )

    summary = run_scan(root, exiftool_command=FAKE_COMMAND)

    assert sorted(summary.errors) == [
        "empty.jpg: File is empty",
        "unreadable.jpg: exiftool could not read this file",
    ]


def test_a_record_carrying_both_keys_reports_the_error_and_still_logs_the_warning(tmp_path):
    """The reader's `else`, which must never become an `elif` (M7 Task 5 review, M591a).

    exiftool can say two things about one file, and the two keys then mean what they
    always mean: the `Error` is what the user is told, and the `Warning` is what the
    person reading the log needs to see beside it. Chaining the warning branch off the
    error branch loses the second half silently, and every OTHER fixture in this file
    carries exactly one of the two keys - so nothing here could tell.

    Found by mutating `if warning is not None:` to `if error is None and warning is not
    None:` and watching all 1323 tests pass.
    """
    root = record_archive(
        tmp_path,
        {"both.jpg": {"Error": "File is empty", "Warning": "JPEG format error"}},
    )

    summary = run_scan(root, exiftool_command=FAKE_COMMAND)

    assert summary.errors == ["both.jpg: File is empty"]
    assert summary.log_path is not None
    log = summary.log_path.read_text(encoding="utf-8")
    assert "exiftool error: both.jpg: File is empty" in log
    assert "exiftool warning: both.jpg: JPEG format error" in log
