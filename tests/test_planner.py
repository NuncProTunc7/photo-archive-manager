import json
import logging
import os
import sys
from pathlib import Path

import pytest
from archives import open_plan

from pam.catalog import repo
from pam.catalog.db import connect
from pam.config import TierBounds
from pam.errors import PamError, TemplateError
from pam.naming import collision
from pam.naming.dates import ResolvedDate
from pam.naming.planner import PlanSummary, _plan_unit, run_plan
from pam.naming.sanitize import stem_budget
from pam.naming.units import Unit
from pam.paths import workspace_for
from pam.scan.runner import run_scan

FAKE = Path(__file__).parent / "fake_exiftool.py"
FAKE_COMMAND = [sys.executable, str(FAKE)]


def tags(**kw):
    return json.dumps(kw)


def make_archive(tmp_path):
    root = tmp_path / "photos"
    (root / "2019").mkdir(parents=True)
    (root / "2019" / "IMG_0001.jpg").write_bytes(b"a")
    (root / "2019" / "IMG_0001.jpg.tags.json").write_text(
        tags(DateTimeOriginal="2019:07:04 18:23:11"), encoding="utf-8"
    )
    (root / "2019" / "IMG_0002.jpg").write_bytes(b"b")
    (root / "2019" / "IMG_0002.jpg.tags.json").write_text(
        tags(DateTimeOriginal="2019:07:04 19:00:00"), encoding="utf-8"
    )
    (root / "2019" / "IMG_0003.jpg").write_bytes(b"c")
    (root / "2019" / "IMG_0003.jpg.tags.json").write_text(
        tags(DateTimeOriginal="2019:08:01 09:00:00"), encoding="utf-8"
    )
    return root


def scanned(tmp_path):
    root = make_archive(tmp_path)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    return root


def items(root):
    conn = connect(workspace_for(root).db_path)
    try:
        plan = open_plan(conn)
        assert plan is not None
        return {i.old_rel_path: i for i in repo.plan_items(conn, plan.id)}
    finally:
        conn.close()


def test_plan_proposes_dated_names_with_a_per_date_sequence(tmp_path):
    # The realistic M4 output: {date}_{seq}. Seq is scoped per date and ordered
    # by (capture_dt, old filename), so 07-04 gets 001/002 and 08-01 restarts.
    root = scanned(tmp_path)
    summary = run_plan(root)
    assert summary.planned == 3 and summary.plan_id is not None
    got = {p: i.generated_filename for p, i in items(root).items()}
    assert got == {
        "2019/IMG_0001.jpg": "2019-07-04_001.jpg",
        "2019/IMG_0002.jpg": "2019-07-04_002.jpg",
        "2019/IMG_0003.jpg": "2019-08-01_001.jpg",
    }


def test_already_conforming_files_are_excluded(tmp_path):
    # spec 6.3.3: a file whose proposed name equals its current name is reported,
    # not planned. Running plan twice over an applied archive plans nothing.
    root = make_archive(tmp_path)
    (root / "2019" / "IMG_0001.jpg").rename(root / "2019" / "2019-07-04_001.jpg")
    (root / "2019" / "IMG_0001.jpg.tags.json").rename(
        root / "2019" / "2019-07-04_001.jpg.tags.json"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    summary = run_plan(root)
    assert summary.already_conforming == 1
    assert "2019/2019-07-04_001.jpg" not in items(root)


def test_companion_group_gets_one_stem_and_keeps_extensions(tmp_path):
    # spec 5.4/6.3: members rename to the same stem as one unit, and the group is
    # named from its primary's facts (the display image, not the RAW or the MOV).
    # The .CR2 is uppercase on purpose: "keeping their own extensions" is
    # case-preserving (spec 5.4 rev 6), and a member keeps its OWN case, not the
    # primary's. Almost every file in a phone library is uppercase.
    root = tmp_path / "photos"
    root.mkdir()
    (root / "IMG_9.jpg").write_bytes(b"a")
    (root / "IMG_9.CR2").write_bytes(b"b")
    (root / "IMG_9.jpg.tags.json").write_text(
        tags(DateTimeOriginal="2019:07:04 18:23:11"), encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    run_plan(root)
    got = {p: i.generated_filename for p, i in items(root).items()}
    assert got == {"IMG_9.jpg": "2019-07-04_001.jpg", "IMG_9.CR2": "2019-07-04_001.CR2"}


def test_no_proposed_name_is_a_name_still_on_disk(tmp_path):
    # Clarification 15 and spec 6.3.2. IMG_1 is the earlier photo so the sequence
    # gives it 001 - but 2019-07-04_001.jpg is already taken by the later photo,
    # and taking it would make the plan a swap that apply's "target absent" check
    # (spec 7.1) would reject on both halves. So the sequence increments instead.
    root = tmp_path / "photos"
    root.mkdir()
    (root / "IMG_1.jpg").write_bytes(b"a")
    (root / "IMG_1.jpg.tags.json").write_text(
        tags(DateTimeOriginal="2019:07:04 18:23:11"), encoding="utf-8"
    )
    # The blocking name is .JPG: the collision check is case-insensitive (spec
    # 6.3.2/7.1), so an uppercase name on disk blocks a lowercase proposal.
    (root / "2019-07-04_001.JPG").write_bytes(b"later")
    (root / "2019-07-04_001.JPG.tags.json").write_text(
        tags(DateTimeOriginal="2019:07:04 20:00:00"), encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    run_plan(root)
    got = {p: i.generated_filename for p, i in items(root).items()}
    assert got == {
        "2019-07-04_001.JPG": "2019-07-04_002.JPG",  # the later photo is #2
        "IMG_1.jpg": "2019-07-04_003.jpg",  # 001 and 002 are both held
    }
    on_disk = {p.name.lower() for p in root.iterdir()}
    for item in items(root).values():
        assert item.generated_filename.lower() not in on_disk


def test_two_files_never_get_the_same_proposed_name(tmp_path):
    # Mixed extension case on purpose: uniqueness is case-insensitive, so a
    # 'x.JPG' and a 'x.jpg' proposal must not both be counted as free.
    root = tmp_path / "photos"
    root.mkdir()
    for i in range(5):
        ext = "JPG" if i % 2 else "jpg"
        (root / f"x{i}.{ext}").write_bytes(bytes([i]))
        (root / f"x{i}.{ext}.tags.json").write_text(
            tags(DateTimeOriginal="2019:07:04 18:23:11"), encoding="utf-8"
        )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    run_plan(root)
    names = [i.generated_filename.lower() for i in items(root).values()]
    assert len(names) == len(set(names)) == 5


def test_placeholders_are_skipped_with_a_count(tmp_path, monkeypatch):
    # Clarification 7: placeholders have no capture row (never read), so every
    # proposal would be 'undated_*' - a guess wearing a filename.
    root = scanned(tmp_path)
    monkeypatch.setattr("pam.scan.walker.is_placeholder", lambda st: True)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    summary = run_plan(root)
    assert summary.skipped_placeholders == 3 and summary.planned == 0
    assert summary.plan_id is None  # nothing to rename: no plan row is written


def test_a_placeholder_in_a_companion_group_skips_every_member(tmp_path):
    # planner.py:90-94 / clarification 7: ONE placeholder member skips the WHOLE
    # companion unit, adding len(members) - not 1 - to skipped_placeholders.
    # Renaming only the non-placeholder half would break the group invariant
    # (every extension present under one shared stem). A real-world library
    # is mostly live-photo groups, so "one dehydrated member" is the ordinary case
    # for a OneDrive user, not an edge case - and a group with the display image
    # dehydrated is the one clarification 7 exists to stop from being renamed.
    root = tmp_path / "photos"
    root.mkdir()
    (root / "IMG_9.jpg").write_bytes(b"a")
    (root / "IMG_9.cr2").write_bytes(b"b")
    (root / "IMG_9.jpg.tags.json").write_text(
        tags(DateTimeOriginal="2019:07:04 18:23:11"), encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    conn = connect(workspace_for(root).db_path)
    try:
        conn.execute("UPDATE files SET placeholder = 1 WHERE rel_path = 'IMG_9.cr2'")
        conn.commit()
    finally:
        conn.close()
    summary = run_plan(root)
    assert summary.skipped_placeholders == 2  # len(members): both, not just the one
    assert summary.planned == 0
    assert summary.plan_id is None  # nothing to rename: no plan row is written
    conn = connect(workspace_for(root).db_path)
    try:
        assert open_plan(conn) is None  # and no plan_items either
    finally:
        conn.close()


def test_a_unit_that_cannot_get_a_free_name_is_dropped_loudly(tmp_path, monkeypatch):
    # planner.py:142-147: when assign_unique_stem gives up (spec 6.3.2's
    # MAX_ATTEMPTS exhausted), the unit is dropped from the plan - but it must
    # not vanish silently. A tool that promises "never lose a file" needs that
    # drop recorded in summary.errors, naming the file, loud enough to reach
    # the user. Force the give-up for one file only, so the other two succeed
    # and the drop is visible as an absence, not just an empty plan.
    root = scanned(tmp_path)
    real_assign = collision.assign_unique_stem

    def fail_for_one_file(order, values, **kwargs):
        if "IMG_0002.jpg" in kwargs["own_names"]:
            return None
        return real_assign(order, values, **kwargs)

    monkeypatch.setattr("pam.naming.collision.assign_unique_stem", fail_for_one_file)
    summary = run_plan(root)
    assert summary.planned == 2
    assert "2019/IMG_0002.jpg" not in items(root)
    assert any("IMG_0002.jpg" in err for err in summary.errors)


def test_tokens_json_records_what_is_in_the_filename(tmp_path):
    # spec 6.1 rev 4: provenance must match the artifact.
    root = scanned(tmp_path)
    run_plan(root)
    item = items(root)["2019/IMG_0001.jpg"]
    tokens = json.loads(item.tokens_json)
    assert [t["token"] for t in tokens] == ["date", "seq"]
    assert [t["value"] for t in tokens] == ["2019-07-04", "001"]
    assert item.generated_filename.startswith("_".join(t["value"] for t in tokens))
    date_token = tokens[0]
    assert date_token["source_table"] == "capture"
    assert date_token["tier"] == "high"  # exif
    assert date_token["truncated"] is False


def test_trust_score_and_tier_come_from_the_date_source(tmp_path):
    # No tags at all -> file_mtime -> low tier, 75 points, one reason.
    root = tmp_path / "photos"
    root.mkdir()
    (root / "undated.jpg").write_bytes(b"a")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    run_plan(root)
    item = items(root)["undated.jpg"]
    assert item.tier == "low" and item.trust_score == 75
    reasons = json.loads(item.trust_reasons_json)
    assert len(reasons) == 1 and "modification time" in reasons[0]


def test_a_new_plan_supersedes_the_previous_open_one(tmp_path):
    root = scanned(tmp_path)
    first = run_plan(root).plan_id
    second = run_plan(root).plan_id
    assert first != second
    conn = connect(workspace_for(root).db_path)
    try:
        assert repo.plan_status(conn, first) == "superseded"
        assert repo.plan_status(conn, second) == "open"
    finally:
        conn.close()


def test_template_override_changes_the_names(tmp_path):
    root = scanned(tmp_path)
    run_plan(root, template_override="{orig}_{date}")
    got = items(root)["2019/IMG_0001.jpg"].generated_filename
    assert got == "IMG_0001_2019-07-04.jpg"


def test_a_bad_template_raises_before_anything_is_written(tmp_path):
    # M3 clarification 13: load_config does not validate the template; the
    # planner surfaces TemplateError at plan time (spec 13 -> exit 1).
    root = scanned(tmp_path)
    with pytest.raises(TemplateError):
        run_plan(root, template_override="{banana}")
    conn = connect(workspace_for(root).db_path)
    try:
        assert open_plan(conn) is None
    finally:
        conn.close()


def test_left_behind_stem_mates_are_counted(tmp_path):
    # spec 6.3.3a: an unknown stem-mate is not renamed and not ignored either.
    # .thm is not in sidecar_exts, so it stays while its .mp4 partner moves.
    # Uppercase throughout: this is how action cameras actually write them.
    root = tmp_path / "photos"
    root.mkdir()
    (root / "CLIP.MP4").write_bytes(b"v")
    (root / "CLIP.MP4.tags.json").write_text(
        tags(CreateDate="2019:07:05 01:23:11"), encoding="utf-8"
    )
    (root / "CLIP.THM").write_bytes(b"t")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    summary = run_plan(root)
    assert summary.planned == 1  # only the .MP4
    assert summary.left_behind == 1  # the .THM, reported not renamed
    assert items(root)["CLIP.MP4"].generated_filename == "2019-07-05_001.MP4"


def test_aae_sidecars_rename_with_their_partner(tmp_path):
    # C15: .aae IS in sidecar_exts, so it joins the group and is renamed - the
    # left-behind counter must stay at zero for it.
    root = tmp_path / "photos"
    root.mkdir()
    (root / "IMG_1.heic").write_bytes(b"h")
    (root / "IMG_1.heic.tags.json").write_text(
        tags(DateTimeOriginal="2019:07:04 18:23:11"), encoding="utf-8"
    )
    (root / "IMG_1.aae").write_bytes(b"a")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    summary = run_plan(root)
    assert summary.left_behind == 0
    got = {p: i.generated_filename for p, i in items(root).items()}
    assert got == {
        "IMG_1.heic": "2019-07-04_001.heic",
        "IMG_1.aae": "2019-07-04_001.aae",
    }


def test_a_failure_mid_write_leaves_the_previous_plan_intact(tmp_path, monkeypatch):
    # Superseding the old plan, creating the new one and writing its items are one
    # transaction. If any part fails the user must not be left with no active plan
    # - losing a reviewed plan to a half-written replacement is a real data loss.
    root = scanned(tmp_path)
    first = run_plan(root).plan_id

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr("pam.catalog.repo.insert_plan_items", boom)
    with pytest.raises(RuntimeError):
        run_plan(root)
    conn = connect(workspace_for(root).db_path)
    try:
        plan = open_plan(conn)
        assert plan is not None and plan.id == first
        assert repo.plan_status(conn, first) == "open"  # NOT superseded
        assert len(repo.plan_items(conn, first)) == 3  # its items survived
        assert conn.execute("SELECT COUNT(*) AS n FROM plans").fetchone()["n"] == 1
    finally:
        conn.close()


def test_end_to_end_plan_is_exactly_this(tmp_path):
    # One golden-master assertion over the whole stack: scan -> plan -> DB. The
    # unit tests cover each module in isolation; this is the only test that would
    # catch a wrong *interaction* between repo, tokens, template, collision,
    # consistency and the planner. When a deliberate change breaks it, update it
    # here in one place - do not weaken the assertions.
    root = scanned(tmp_path)
    summary = run_plan(root)
    assert (summary.total, summary.planned, summary.already_conforming) == (3, 3, 0)
    assert summary.skipped_placeholders == 0 and summary.errors == []
    assert summary.tiers == {"high": 3}
    assert summary.template == "{date}_{event-or-scene}_{people}_{seq}"
    assert summary.examples == [
        ("IMG_0001.jpg", "2019-07-04_001.jpg"),
        ("IMG_0002.jpg", "2019-07-04_002.jpg"),
        ("IMG_0003.jpg", "2019-08-01_001.jpg"),
    ]
    conn = connect(workspace_for(root).db_path)
    try:
        plan = open_plan(conn)
        assert plan is not None
        assert plan.template == "{date}_{event-or-scene}_{people}_{seq}"
        rows = repo.plan_items(conn, plan.id)
        assert [r.old_rel_path for r in rows] == [
            "2019/IMG_0001.jpg",
            "2019/IMG_0002.jpg",
            "2019/IMG_0003.jpg",
        ]
        item = rows[0]
        assert item.generated_filename == item.new_filename == "2019-07-04_001.jpg"
        assert (item.tier, item.trust_score, item.status) == ("high", 100, "proposed")
        assert json.loads(item.trust_reasons_json) == []
        assert json.loads(item.tokens_json) == [
            {
                "token": "date",
                "resolved_from": "date",
                "value": "2019-07-04",
                "source_table": "capture",
                "source_id": item.file_id,
                "tier": "high",
                "truncated": False,
            },
            {
                "token": "seq",
                "resolved_from": "seq",
                "value": "001",
                "source_table": "planner",
                "source_id": None,
                "tier": "high",
                "truncated": False,
            },
        ]
    finally:
        conn.close()


def test_tokens_json_records_a_genuine_budget_truncation(tmp_path):
    # planner.py:174 writes 'truncated: not token.complete' into tokens_json.
    # {orig} is never in template._TRIM_ORDER, so it can't be dropped - only
    # hard-cut by the path budget (spec 6.2 rule 5). This is deliberately NOT
    # the reserved-device-name -x case: EmittedToken.complete conflates the two
    # (open finding), and entrenching that conflation in a test would be wrong.
    # Compute the real budget instead of guessing, so this doesn't depend on
    # how long the pytest tmp_path happens to be on this machine.
    root = tmp_path / "photos"
    root.mkdir()
    budget = stem_budget(str(root.resolve()), "", (".jpg",))
    stem_len = budget + 10
    name = "A" * stem_len + ".jpg"
    (root / name).write_bytes(b"a")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    run_plan(root, template_override="{orig}")
    item = items(root)[name]
    tokens = json.loads(item.tokens_json)
    assert tokens[0]["token"] == "orig"
    assert tokens[0]["truncated"] is True
    assert len(tokens[0]["value"]) == budget  # genuinely cut, not merely flagged
    assert item.generated_filename == "A" * budget + ".jpg"


def test_units_are_processed_in_rel_path_order_group_and_lone_mixed(tmp_path):
    # planner.py:98 sorts units by primary rel_path before collision resolution
    # runs. _units() otherwise appends every companion-group unit after every
    # lone-file unit (see the finding), so without the sort a group is always
    # processed after a lone file regardless of its name - changing which unit
    # reserves a contested name first. Same reserve-race shape as
    # test_no_proposed_name_is_a_name_still_on_disk, but the early-sorting side
    # (whose current name blocks '_001' from the start) is a companion group.
    root = tmp_path / "photos"
    root.mkdir()
    (root / "2019-07-04_001.jpg").write_bytes(b"a")
    (root / "2019-07-04_001.cr2").write_bytes(b"b")
    (root / "2019-07-04_001.jpg.tags.json").write_text(
        tags(DateTimeOriginal="2019:07:04 20:00:00"), encoding="utf-8"
    )
    (root / "IMG_1.jpg").write_bytes(b"c")
    (root / "IMG_1.jpg.tags.json").write_text(
        tags(DateTimeOriginal="2019:07:04 18:23:11"), encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    run_plan(root)
    got = {p: i.generated_filename for p, i in items(root).items()}
    # Sorted order processes the group first ('2019-07-04_001...' < 'IMG_1.jpg'):
    # it claims the free '_002' name, so IMG_1 (blocked at '_001' by the group's
    # own current name, then at '_002' by the group's reservation) falls to '_003'.
    assert got == {
        "2019-07-04_001.jpg": "2019-07-04_002.jpg",
        "2019-07-04_001.cr2": "2019-07-04_002.cr2",
        "IMG_1.jpg": "2019-07-04_003.jpg",
    }


def test_an_already_conforming_unit_with_uppercase_extensions_plans_nothing(tmp_path):
    # N1 + finding 7, the exact reproduction from the M4 review. Before the fix
    # this archive produced ONE tier-high score-100 plan item that was not a
    # rename at all ('...JPG' -> '...jpg'), which spec 7.1's case-insensitive
    # absence check is guaranteed to mark failed - and spec 7.3 then reverts its
    # whole companion unit. The same run also counted the .mov as BOTH
    # already-conforming and left-behind, printing a false warning about pam's own
    # spec 5.4 guarantee. almost every item of a phone library took this
    # path. One test, both findings.
    root = tmp_path / "photos"
    root.mkdir()
    (root / "2022-06-01_001.JPG").write_bytes(b"a")
    (root / "2022-06-01_001.JPG.tags.json").write_text(
        tags(DateTimeOriginal="2022:06:01 12:00:00"), encoding="utf-8"
    )
    (root / "2022-06-01_001.mov").write_bytes(b"v")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    summary = run_plan(root)
    assert (summary.planned, summary.already_conforming, summary.left_behind) == (0, 2, 0)
    assert summary.plan_id is None


def test_an_uppercase_extension_survives_a_real_rename(tmp_path):
    # spec 5.4 rev 6, the maintainer's ruling: preserve the on-disk case. files.ext is
    # lowercased for typing and matching and must never be the source of a
    # proposed filename; files.filename carries the truth.
    root = tmp_path / "photos"
    root.mkdir()
    (root / "IMG_0001.JPG").write_bytes(b"a")
    (root / "IMG_0001.JPG.tags.json").write_text(
        tags(DateTimeOriginal="2022:06:01 12:00:00"), encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    run_plan(root)
    assert items(root)["IMG_0001.JPG"].generated_filename == "2022-06-01_001.JPG"


def test_each_live_photo_member_keeps_its_own_extension_case(tmp_path):
    # The ordinary iPhone shape: .HEIC + .MOV, both uppercase, one stem. Each
    # member keeps ITS OWN case - the stem is shared, the extension is not.
    root = tmp_path / "photos"
    root.mkdir()
    (root / "IMG_5830.HEIC").write_bytes(b"h")
    (root / "IMG_5830.HEIC.tags.json").write_text(
        tags(DateTimeOriginal="2022:06:01 12:00:00"), encoding="utf-8"
    )
    (root / "IMG_5830.MOV").write_bytes(b"v")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    run_plan(root)
    got = {p: i.generated_filename for p, i in items(root).items()}
    assert got == {
        "IMG_5830.HEIC": "2022-06-01_001.HEIC",
        "IMG_5830.MOV": "2022-06-01_001.MOV",
    }


def test_a_video_with_an_aae_sidecar_is_named_from_the_video(tmp_path):
    # Findings 6 + 4 end to end, in the shape measured on a real-world library.
    # Before the fix the .AAE was elected primary (it sorts first and carried no
    # facts), so both members became 'undated_001.*' while the video's real
    # qt_utc date sat in the catalog, and tokens_json pointed 'source_table:
    # capture' at the .AAE's id - a row that does not exist.
    root = tmp_path / "photos"
    root.mkdir()
    (root / "IMG_5678.MOV").write_bytes(b"v")
    (root / "IMG_5678.MOV.tags.json").write_text(
        tags(CreateDate="2023:05:05 12:00:00"), encoding="utf-8"
    )
    (root / "IMG_5678.AAE").write_bytes(b"a")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    run_plan(root)
    planned = items(root)
    assert {p: i.generated_filename for p, i in planned.items()} == {
        "IMG_5678.MOV": "2023-05-05_001.MOV",
        "IMG_5678.AAE": "2023-05-05_001.AAE",
    }
    item = planned["IMG_5678.MOV"]
    date_token = json.loads(item.tokens_json)[0]
    assert date_token["value"] == "2023-05-05"  # the video's own date, not 'undated'
    assert date_token["tier"] == "medium"  # spec 6.1: qt_utc caps at medium
    assert item.tier == "medium"
    assert date_token["source_table"] == "capture"
    conn = connect(workspace_for(root).db_path)
    try:
        mov_id = conn.execute(
            "SELECT id FROM files WHERE rel_path = 'IMG_5678.MOV'"
        ).fetchone()["id"]
        # Provenance names the row that actually holds the fact, and it exists.
        assert date_token["source_id"] == mov_id
        rows = conn.execute("SELECT * FROM capture WHERE file_id = ?", (mov_id,)).fetchall()
        assert len(rows) == 1 and rows[0]["capture_dt_source"] == "qt_utc"
        # The .AAE is a member of the same unit and has no capture row at all.
        aae_id = planned["IMG_5678.AAE"].file_id
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM capture WHERE file_id = ?", (aae_id,)
            ).fetchone()["n"]
            == 0
        )
    finally:
        conn.close()


def test_a_file_with_no_capture_row_never_claims_one(tmp_path):
    # spec 6.1 rev 6 / finding 4. A file row with no capture row is reachable:
    # scan commits the file row before the capture row (P1 crash safety), so a
    # crash in between leaves exactly this state, and pam plan runs on it. The
    # provenance must then name 'planner', not a capture row that is not there.
    root = scanned(tmp_path)
    conn = connect(workspace_for(root).db_path)
    try:
        conn.execute(
            "DELETE FROM capture WHERE file_id ="
            " (SELECT id FROM files WHERE rel_path = '2019/IMG_0001.jpg')"
        )
        conn.commit()
    finally:
        conn.close()
    run_plan(root)
    item = items(root)["2019/IMG_0001.jpg"]
    date_token = json.loads(item.tokens_json)[0]
    assert date_token["value"] == "undated"
    assert date_token["source_table"] == "planner" and date_token["source_id"] is None
    assert all(t["source_table"] != "capture" for t in json.loads(item.tokens_json))


def test_a_name_carrying_no_facts_at_all_is_low_tier(tmp_path):
    # spec 6.3.4 rev 6 / finding 3, end to end. '---' sanitizes to nothing, so
    # under --template "{orig}" every token drops and build_stem falls back to
    # 'unnamed' with an EMPTY emitted tuple. min_tier(()) used to answer 'high',
    # which is the tier M7's bulk approval is allowed to sweep without review.
    root = tmp_path / "photos"
    root.mkdir()
    (root / "---.jpg").write_bytes(b"a")
    (root / "---.jpg.tags.json").write_text(
        tags(DateTimeOriginal="2019:07:04 18:23:11"), encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    summary = run_plan(root, template_override="{orig}")
    item = items(root)["---.jpg"]
    assert item.generated_filename == "unnamed.jpg"
    assert json.loads(item.tokens_json) == []
    assert item.tier == "low"
    assert summary.tiers == {"low": 1}


def test_a_case_only_difference_is_already_conforming_not_a_rename(tmp_path):
    # Mutation-gate finding, closed here. Reverting the already-conforming compare
    # to `==` left the whole suite green, because with ext_as_written the two
    # strings now agree exactly wherever the planner can currently reach. That is
    # the point of the M4 ruling: the comparison is defence in depth, and the two
    # modules "must not be able to disagree again". So drive the disagreement
    # directly - a PlanFile whose ext_as_written has drifted from its filename is
    # exactly the pre-fix world (files.ext lowercased vs files.filename), and the
    # planner must still refuse to emit the item. A case-only item is one spec 7.1
    # is guaranteed to fail, and spec 7.3 then reverts its whole companion unit.
    drifted = repo.PlanFile(
        id=1,
        rel_path="IMG_1.JPG",
        filename="IMG_1.JPG",
        ext="jpg",
        ext_as_written="jpg",  # drifted from the filename above
        media_type="image",
        placeholder=False,
        companion_group_id=None,
        capture_dt="2019-07-04T18:23:11",
        capture_dt_source="exif",
        has_capture=True,
    )
    summary = PlanSummary()
    drafts = _plan_unit(
        Unit(primary=drifted, members=(drifted,)),
        seq=1,
        event=None,
        place=None,
        scene=None,
        people=(),
        order=("orig",),
        root=tmp_path,
        taken={},
        summary=summary,
        logger=logging.getLogger("test"),
        caption_tier="medium",
        max_people=3,
        known_places=frozenset(),
        # M12 Task 10: an exif file - P9 never applies, so the date carries nothing.
        date=ResolvedDate("2019-07-04T18:23:11", "exif", None, None, None, ()),
        ocr_bounds=TierBounds(0.5, 0.7, 0.9),
        name_evidence=None,  # M13 Task 5: pam wrote no name here
    )
    assert drafts == []  # 'IMG_1.jpg' vs 'IMG_1.JPG' is not a rename
    assert summary.already_conforming == 1


def test_a_unit_with_two_members_of_one_extension_is_recorded_not_crashed(tmp_path, caplog):
    # The caller side of collision.assign_unique_stem's new local uniqueness guard.
    # spec 5.4 safety property 1 makes this group unreachable through companions.py,
    # which is exactly why the planner's handling of it has never been exercised:
    # drive it directly. assign_unique_stem returns None, and the planner must do
    # what it does with any unit it cannot name - record it, log it, plan nothing -
    # never propose one filename for two files and never raise.
    def member(file_id, name):
        return repo.PlanFile(
            id=file_id,
            rel_path=name,
            filename=name,
            ext="jpg",
            ext_as_written="jpg",
            media_type="image",
            placeholder=False,
            companion_group_id=7,
            capture_dt="2019-07-04T18:23:11",
            capture_dt_source="exif",
            has_capture=True,
        )

    a, b = member(1, "a.jpg"), member(2, "b.jpg")
    summary = PlanSummary()
    with caplog.at_level(logging.ERROR):
        drafts = _plan_unit(
            Unit(primary=a, members=(a, b)),
            seq=1,
            event=None,
            place=None,
            scene=None,
            people=(),
            order=("date", "seq"),
            root=tmp_path,
            taken={},
            summary=summary,
            logger=logging.getLogger("test"),
            caption_tier="medium",
            max_people=3,
            known_places=frozenset(),
            date=ResolvedDate(None, "exif", None, None, None, ()),
            ocr_bounds=TierBounds(0.5, 0.7, 0.9),
            name_evidence=None,  # M13 Task 5: pam wrote no name here
        )
    assert drafts == []
    assert summary.error_files == 2  # FILES, not units - record_error moves both
    assert len(summary.errors) == 1 and summary.had_errors
    assert "a.jpg" in caplog.text


def test_planning_the_same_catalog_twice_gives_the_same_names(tmp_path):
    # Renamed from test_plan_is_deterministic: this proves repeatability (same
    # unchanged DB, queried twice) not order-independence - the old name implied
    # more than the assertions check.
    root = scanned(tmp_path)
    run_plan(root)
    first = {p: i.generated_filename for p, i in items(root).items()}
    run_plan(root)
    assert {p: i.generated_filename for p, i in items(root).items()} == first


# --- M5 Task 2: unit_key, plan lifecycle, workspace and template ---------------


def plan_items_of(root, plan_id):
    conn = connect(workspace_for(root).db_path)
    try:
        return {i.old_rel_path: i for i in repo.plan_items(conn, plan_id)}
    finally:
        conn.close()


def test_a_companion_units_members_share_one_unit_key(tmp_path):
    # spec 6.3 rule 6: the two members of a live-photo pair are ONE rename unit, so
    # they carry one unit_key; the unrelated lone file carries a different one.
    # spec 7.3 groups the apply by this key, so a pair that did not share it would
    # be applied as two independent renames - a group split across two stems.
    root = tmp_path / "photos"
    root.mkdir()
    (root / "IMG_1.HEIC").write_bytes(b"h")
    (root / "IMG_1.MOV").write_bytes(b"m")
    (root / "IMG_1.HEIC.tags.json").write_text(
        tags(DateTimeOriginal="2019:07:04 18:23:11"), encoding="utf-8"
    )
    (root / "OTHER.JPG").write_bytes(b"o")
    (root / "OTHER.JPG.tags.json").write_text(
        tags(DateTimeOriginal="2019:07:04 19:00:00"), encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    run_plan(root)
    got = items(root)

    assert got["IMG_1.HEIC"].unit_key == got["IMG_1.MOV"].unit_key
    assert got["IMG_1.HEIC"].unit_key != got["OTHER.JPG"].unit_key
    # The primary names the unit (spec 5.4: the display image, not the video).
    assert got["IMG_1.HEIC"].unit_key == "\x1fIMG_1.HEIC"
    assert got["OTHER.JPG"].unit_key == "\x1fOTHER.JPG"


def test_unit_key_survives_a_rescan_that_changes_companion_group_id(tmp_path):
    # THE property the whole amendment exists for. files.companion_group_id is
    # recomputed from scratch on every scan, so it is not a stable identity; the
    # plan's own frozen unit_key is. Plan, then rescan with a new stem-mate
    # present, then read the SAME plan back.
    root = tmp_path / "photos"
    (root / "a").mkdir(parents=True)
    (root / "a" / "IMG_9.jpg").write_bytes(b"j")
    (root / "a" / "IMG_9.CR2").write_bytes(b"r")
    (root / "a" / "IMG_9.jpg.tags.json").write_text(
        tags(DateTimeOriginal="2019:07:04 18:23:11"), encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    before = {p: i.unit_key for p, i in plan_items_of(root, plan_id).items()}
    assert set(before) == {"a/IMG_9.jpg", "a/IMG_9.CR2"}
    assert len(set(before.values())) == 1

    conn = connect(workspace_for(root).db_path)
    try:
        group_before = {r.rel_path: r.companion_group_id for r in repo.all_files(conn).values()}
    finally:
        conn.close()

    # A new pair that sorts ahead of the old one: grouping is recreated in member
    # order, so the old pair's group id moves.
    (root / "a" / "AAA.jpg").write_bytes(b"x")
    (root / "a" / "AAA.CR2").write_bytes(b"y")
    (root / "a" / "AAA.jpg.tags.json").write_text(
        tags(DateTimeOriginal="2019:07:04 09:00:00"), encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)

    conn = connect(workspace_for(root).db_path)
    try:
        group_after = {r.rel_path: r.companion_group_id for r in repo.all_files(conn).values()}
    finally:
        conn.close()
    assert group_after["a/IMG_9.jpg"] != group_before["a/IMG_9.jpg"], (
        "the fixture no longer moves the group id, so it no longer tests anything"
    )

    after = {p: i.unit_key for p, i in plan_items_of(root, plan_id).items()}
    assert after == before


def test_nothing_to_rename_still_supersedes_the_open_plan(tmp_path):
    # N3, verbatim reproduction. Plan, rename by hand exactly as proposed, rescan,
    # re-plan. Before the fix the run reported "nothing to rename" while leaving an
    # open plan whose item pointed at a file the catalog had marked missing.
    root = tmp_path / "photos"
    root.mkdir()
    (root / "IMG_0001.jpg").write_bytes(b"a")
    (root / "IMG_0001.jpg.tags.json").write_text(
        tags(DateTimeOriginal="2019:07:04 18:23:11"), encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    first = run_plan(root)
    assert first.planned == 1

    (root / "IMG_0001.jpg").rename(root / "2019-07-04_001.jpg")
    (root / "IMG_0001.jpg.tags.json").rename(root / "2019-07-04_001.jpg.tags.json")
    run_scan(root, exiftool_command=FAKE_COMMAND)

    second = run_plan(root)
    assert second.planned == 0 and second.plan_id is None
    assert second.already_conforming == 1
    conn = connect(workspace_for(root).db_path)
    try:
        assert open_plan(conn) is None
        assert repo.plan_status(conn, first.plan_id) == "superseded"
    finally:
        conn.close()


def test_a_nothing_to_rename_run_supersedes_with_no_scan_in_between(tmp_path):
    # The N3 fix ISOLATED. The reproduction above no longer distinguishes: renaming
    # by hand forces a rescan, and Step 4's scan-side supersede fires first, so the
    # planner's own unconditional supersede could be deleted and that test would
    # still pass (mutation M20 survived it). Here nothing touches disk between the
    # two runs - the second run plans nothing only because {orig} reproduces every
    # current name - so the planner is the ONLY thing that can supersede.
    # A fresh connection also proves the supersede was committed, not rolled back
    # when run_plan closed its own (mutation M21).
    root = scanned(tmp_path)
    first = run_plan(root).plan_id
    assert first is not None

    second = run_plan(root, template_override="{orig}")
    assert second.plan_id is None and second.planned == 0
    assert second.already_conforming == 3

    conn = connect(workspace_for(root).db_path)
    try:
        assert repo.plan_status(conn, first) == "superseded"
        assert open_plan(conn) is None
    finally:
        conn.close()


def test_a_deleted_files_name_is_never_proposed_to_another_file(tmp_path):
    # files.rel_path is UNIQUE and rows are NEVER deleted, so a file removed outside
    # pam leaves a `missing` row holding its name forever. Proposing that name plans
    # a rename that SUCCEEDS on disk and then fails in set_file_path with an
    # IntegrityError - journal `intent` beside a completed rename. spec 7.2's
    # recovery adopts it and hits the same constraint, so the row can never be
    # resolved: J7 ("after recovery no row remains in intent") is broken on a pass
    # that runs at the start of EVERY invocation. Reproduced end-to-end before the
    # fix; the planner's `taken` set had to stop skipping `missing` rows.
    root = scanned(tmp_path)
    run_plan(root)

    def new_rel(item):
        head, _, _ = item.old_rel_path.rpartition("/")
        return f"{head}/{item.new_filename}" if head else item.new_filename

    proposed = {i.old_rel_path: new_rel(i) for i in items(root).values()}

    # The user applies the plan by hand and re-scans, so the catalog now owns the new
    # names. Then they delete one of those files in Explorer and re-scan again: the
    # row is marked `missing` and keeps its rel_path forever.
    for old_rel, new_path in proposed.items():
        (root / old_rel).rename(root / new_path)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    ghost = proposed["2019/IMG_0001.jpg"]
    (root / ghost).unlink()
    run_scan(root, exiftool_command=FAKE_COMMAND)

    # A new photo from the same day arrives and re-plans. The ghost's name looks free
    # - nothing is on disk holding it - but the catalog row still owns it.
    (root / "2019" / "NEW_0001.jpg").write_bytes(b"z")
    (root / "2019" / "NEW_0001.jpg.tags.json").write_text(
        tags(DateTimeOriginal="2019:07:04 18:23:11"), encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    run_plan(root)

    conn = connect(workspace_for(root).db_path)
    try:
        catalog_names = {row.rel_path for row in repo.all_files(conn).values()}
        assert ghost in catalog_names, "the fixture no longer leaves a ghost row"
        assert any(r.status == "missing" for r in repo.all_files(conn).values())
        plan = open_plan(conn)
        assert plan is not None
        proposals = [new_rel(i) for i in repo.plan_items(conn, plan.id)]
    finally:
        conn.close()
    assert ghost not in proposals


def test_the_nothing_to_rename_supersede_is_committed(tmp_path):
    # The supersede is the entire result of a nothing-to-rename run, so it has to
    # survive the connection closing - an uncommitted UPDATE would roll back and
    # the whole fix would be invisible outside the process.
    root = scanned(tmp_path)
    first = run_plan(root).plan_id
    for name in ("IMG_0001", "IMG_0002", "IMG_0003"):
        (root / "2019" / f"{name}.jpg").unlink()
        (root / "2019" / f"{name}.jpg.tags.json").unlink()
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert run_plan(root).plan_id is None

    conn = connect(workspace_for(root).db_path)  # a fresh connection
    try:
        assert repo.plan_status(conn, first) == "superseded"
    finally:
        conn.close()


def test_config_json_freezes_the_grouping_relevant_config(tmp_path):
    # spec 6.3 rule 5a rev 6: sidecar_exts and ignore_stem_suffixes decide companion
    # membership, so a plan that does not record them cannot be shown to have been
    # built under the rules now in force.
    root = scanned(tmp_path)
    ws = workspace_for(root)
    ws.config_path.write_text(
        '[scan]\nsidecar_exts = ["xmp"]\nignore_stem_suffixes = [".acr_"]\n',
        encoding="utf-8",
    )
    plan_id = run_plan(root).plan_id
    conn = connect(ws.db_path)
    try:
        raw = conn.execute("SELECT config_json FROM plans WHERE id = ?", (plan_id,)).fetchone()[
            "config_json"
        ]
    finally:
        conn.close()
    assert json.loads(raw) == {
        "ignore_stem_suffixes": [".acr_"],
        "max_people": 3,
        "sidecar_exts": ["xmp"],
        "template": "{date}_{event-or-scene}_{people}_{seq}",
        "gap_hours": 4.0,
        "gps_km": 50.0,
        # rev 27 (P10): the shipped defaults - no folders, floor 0.5, grammar version 1.
        "ocr_folders": [],
        "ocr_floor": 0.5,
        "ocr_grammar_version": 1,
    }


def test_plan_on_an_unscanned_folder_refuses_and_creates_no_workspace(tmp_path):
    # N4: creating a workspace as a side effect of a read-only command makes a
    # typo'd --archive indistinguishable from a genuine all-conforming archive.
    home = Path(os.environ["PAM_HOME"])
    root = tmp_path / "never-scanned"
    root.mkdir()
    (root / "IMG_1.jpg").write_bytes(b"a")

    with pytest.raises(PamError) as excinfo:
        run_plan(root)
    assert str(excinfo.value) == (
        f"pam has not scanned {root.resolve()} yet. Run: pam scan {root.resolve()}"
    )
    assert not home.exists() or list(home.rglob("*")) == []


def test_plan_refuses_a_workspace_directory_with_no_catalog(tmp_path):
    # An interrupted first scan can leave the directory behind with no catalog.db.
    root = tmp_path / "photos"
    root.mkdir()
    workspace_for(root)  # directory only, no catalog
    with pytest.raises(PamError, match="has not scanned"):
        run_plan(root)


def utc_video(tmp_path):
    # A QuickTime UTC date and a filename that carries no date of its own, so the
    # only trust signal in play is the date source.
    root = tmp_path / "photos"
    root.mkdir()
    (root / "MY CLIP.MOV").write_bytes(b"v")
    (root / "MY CLIP.MOV.tags.json").write_text(
        tags(CreateDate="2023:05:05 12:00:00"), encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    return root


def test_a_name_with_no_date_in_it_is_not_charged_for_the_date(tmp_path):
    # N2, the measured reproduction, end to end. --template "{orig}" on a qt_utc
    # video shipped tier high WITH score 90 and the reason "Video time is recorded
    # in UTC - the local date may differ" - attached to a filename containing no
    # date at all. spec 6.4 derives the score from tokens_json, which holds only
    # emitted tokens, so token_tiers was right and score was wrong. They now agree.
    root = utc_video(tmp_path)
    run_plan(root, template_override="{orig}")
    item = items(root)["MY CLIP.MOV"]
    assert item.generated_filename == "MY-CLIP.MOV"
    assert [t["token"] for t in json.loads(item.tokens_json)] == ["orig"]
    assert item.trust_score == 100
    assert json.loads(item.trust_reasons_json) == []
    assert item.tier == "high"


def test_the_same_file_named_from_its_date_still_pays_for_the_utc(tmp_path):
    # The control: the deduction was not removed, it was made conditional on the
    # claim actually being in the name. Same file, same catalog, {date} emitted.
    root = utc_video(tmp_path)
    run_plan(root, template_override="{date}_{seq}")
    item = items(root)["MY CLIP.MOV"]
    assert item.generated_filename == "2023-05-05_001.MOV"
    assert item.trust_score == 90
    reasons = json.loads(item.trust_reasons_json)
    assert len(reasons) == 1 and "UTC" in reasons[0]
    assert item.tier == "medium"  # spec 6.1: qt_utc caps the date token at medium


DATE_WORDS = ("date", "UTC", "undated", "modification time")


def test_a_date_reason_always_implies_a_date_token_in_the_name(tmp_path):
    # N2 as a standing invariant rather than two examples: across every date
    # source pam can produce (exif, qt_utc, file_mtime) and every template shape,
    # no item may carry a reason ABOUT the date unless the date is in the name.
    root = tmp_path / "photos"
    root.mkdir()
    (root / "EXIF SHOT.jpg").write_bytes(b"a")
    (root / "EXIF SHOT.jpg.tags.json").write_text(
        tags(DateTimeOriginal="2019:07:04 18:23:11"), encoding="utf-8"
    )
    (root / "UTC CLIP.MOV").write_bytes(b"v")
    (root / "UTC CLIP.MOV.tags.json").write_text(
        tags(CreateDate="2023:05:05 12:00:00"), encoding="utf-8"
    )
    (root / "NO TAGS.jpg").write_bytes(b"c")  # falls through to file_mtime
    (root / "VID_20190704_182311.MOV").write_bytes(b"d")  # the filename/UTC clash
    (root / "VID_20190704_182311.MOV.tags.json").write_text(
        tags(CreateDate="2023:05:05 12:00:00"), encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)

    seen = 0
    checked = 0
    for template in ("{orig}", "{date}_{seq}", "{date}_{orig}", "{seq}_{orig}"):
        run_plan(root, template_override=template)
        for rel_path, item in items(root).items():
            seen += 1
            named = {t["resolved_from"] for t in json.loads(item.tokens_json)}
            for reason in json.loads(item.trust_reasons_json):
                if any(word in reason for word in DATE_WORDS):
                    checked += 1
                    assert "date" in named, f"{template} {rel_path}: {reason}"
    assert seen >= 12, "the fixture stopped producing items, so it proves nothing"
    # DATE_WORDS matches case-sensitively by substring, so "Date read from the
    # file's modification time..." does NOT match "date" - it survives only via
    # "modification time". Reword a reason and the loop above would silently check
    # nothing while still passing. This pins the predicate itself.
    assert checked > 0, "no date reason was examined - DATE_WORDS no longer matches the copy"


def test_appending_seq_starts_at_the_units_own_number(tmp_path):
    # N6 / spec 6.3.2 rev 6, end to end from the M4 review's reproduction. Three
    # same-day photos under --template "{date}" produced 2019-07-04, _003, _004:
    # the append and the increment happened in one step, so _001 and _002 never
    # appeared. Safe but inexplicable, and pam's names must be explicable.
    root = scanned(tmp_path)
    run_plan(root, template_override="{date}")
    got = {p: i.generated_filename for p, i in items(root).items()}
    assert got == {
        "2019/IMG_0001.jpg": "2019-07-04.jpg",
        "2019/IMG_0002.jpg": "2019-07-04_002.jpg",
        "2019/IMG_0003.jpg": "2019-08-01.jpg",  # a new date restarts the scope
    }


def test_an_empty_template_override_is_refused_not_substituted(tmp_path):
    # N5: `template_override or config.template` treats "" as "not given". An empty
    # template is a request parse_template must refuse, not one to silently ignore.
    root = scanned(tmp_path)
    with pytest.raises(TemplateError, match="no tokens"):
        run_plan(root, template_override="")


# ---------------------------------------------------------------- events (M8 Task 8)

# spec 6.3 rule 5a, rev 6 + rev 14, written out from the SPEC and not from the dict
# the planner builds: `template` and `max_people` (rev 6), `scan.sidecar_exts` and
# `scan.ignore_stem_suffixes` (rev 6), `events.gap_hours` and `events.gps_km` (rev 14).
# The section prefixes in rule 5a's prose identify pam.toml keys; this dict has spelled
# them bare since M5, as it does for [naming] max_people. `set(frozen) == set(built)`
# would pass for any dict the planner happened to write, which is the shape this project
# has been bitten by repeatedly - so the nine names are literals here.
FROZEN_CONFIG_KEYS = frozenset(
    {
        "template",
        "max_people",
        "sidecar_exts",
        "ignore_stem_suffixes",
        "gap_hours",
        "gps_km",
        # rev 27 (P10): the plan-time OCR date's three inputs that are not facts.
        "ocr_folders",
        "ocr_floor",
        "ocr_grammar_version",
    }
)


def stored_events(root):
    """The archive's events and their membership, read with the suite's own SQL.

    There is deliberately no production reader for these tables yet (M8 Task 7 left
    them write-only), and there should not be one here either: an oracle built from
    the query it is checking agrees with any value of that query - the `repo.open_plan`
    lesson M5 settled and `repo.ENRICHERS` repeated. Membership comes back as rel_paths
    rather than row ids so an expectation can be written from the fixture.
    """
    conn = connect(workspace_for(root).db_path)
    try:
        names = {row.id: row.rel_path for row in repo.all_files(conn).values()}
        members: dict[int, list[str]] = {}
        for row in conn.execute("SELECT event_id, file_id FROM file_events"):
            members.setdefault(int(row["event_id"]), []).append(names[int(row["file_id"])])
        return [
            (r["start_dt"], r["end_dt"], sorted(members.get(int(r["id"]), [])))
            for r in conn.execute(
                "SELECT id, start_dt, end_dt FROM events ORDER BY start_dt, id"
            )
        ]
    finally:
        conn.close()


def config_json_of(root, plan_id):
    conn = connect(workspace_for(root).db_path)
    try:
        row = conn.execute("SELECT config_json FROM plans WHERE id = ?", (plan_id,)).fetchone()
        return json.loads(row["config_json"])
    finally:
        conn.close()


def test_plan_groups_the_archive_into_events_as_its_step_0(tmp_path):
    # spec 6.3 rule 5b rev 14 / spec 8.3 rev 14. The spans and the membership are
    # spelled out from the FIXTURE'S dates and spec 8.3's rule, not read back from
    # whatever grouping emitted: 18:23:11 -> 19:00:00 is 36m49s and does not exceed
    # the 4-hour default, so those two stay one event; 07-04 -> 08-01 is four weeks
    # and is a second. The .tags.json stem-mates carry no capture row, so spec 8.3's
    # "sidecars fall out naturally" is visible here as their absence.
    root = scanned(tmp_path)
    summary = run_plan(root)
    assert summary.events == 2
    assert stored_events(root) == [
        (
            "2019-07-04T18:23:11",
            "2019-07-04T19:00:00",
            ["2019/IMG_0001.jpg", "2019/IMG_0002.jpg"],
        ),
        ("2019-08-01T09:00:00", "2019-08-01T09:00:00", ["2019/IMG_0003.jpg"]),
    ]


def test_grouping_happens_before_the_first_name_is_generated(tmp_path, monkeypatch):
    # spec 6.3 rule 5b rev 14: grouping runs "FIRST ... before any name is generated".
    # F1 of the Task 8 review: only the "in this command's transaction" half of that
    # sentence was under test, and M935 moved the whole call to BELOW the naming loop
    # with all 2132 tests green - the rows and the count are identical either way, so
    # nothing that reads the OUTCOME can see the order. This watches the two calls
    # themselves: repo.replace_events is the last thing step 0 does, and
    # collision.assign_unique_stem is where a name is generated.
    #
    # It matters before Task 9 makes it observable, not after: {event} and {seq}'s
    # scope are read off the partition, so a name generated first is a name generated
    # from the PREVIOUS run's events, frozen into a plan whose config_json claims the
    # new parameters. Both anchors are asserted PRESENT - an order test over an empty
    # list, or over a list missing the naming call, agrees with anything.
    root = scanned(tmp_path)
    calls: list[str] = []
    real_replace, real_assign = repo.replace_events, collision.assign_unique_stem

    def spy_replace(*args, **kwargs):
        calls.append("grouped")
        return real_replace(*args, **kwargs)

    def spy_assign(*args, **kwargs):
        calls.append("named")
        return real_assign(*args, **kwargs)

    monkeypatch.setattr("pam.catalog.repo.replace_events", spy_replace)
    monkeypatch.setattr("pam.naming.collision.assign_unique_stem", spy_assign)
    run_plan(root)

    assert "grouped" in calls and "named" in calls, calls
    assert calls[0] == "grouped", calls
    assert calls.index("grouped") < calls.index("named"), calls


def test_grouping_runs_on_the_nothing_to_rename_path_too(tmp_path):
    # spec 8.3 rev 14 groups "all present, dated files" - a statement about the
    # CATALOG, not about the plan - and spec 6.3 rule 5b makes it the planner's step
    # 0 with no condition attached. An archive whose files all already conform plans
    # nothing and is still an archive with events in it, which `pam review`'s nav
    # reads from the catalog. So step 0 sits above the early return, exactly as rule
    # 5's unconditional supersede does, and the early return commits both.
    root = make_archive(tmp_path)
    for old, new in (
        ("IMG_0001.jpg", "2019-07-04_001.jpg"),
        ("IMG_0002.jpg", "2019-07-04_002.jpg"),
        ("IMG_0003.jpg", "2019-08-01_001.jpg"),
    ):
        (root / "2019" / old).rename(root / "2019" / new)
        (root / "2019" / f"{old}.tags.json").rename(root / "2019" / f"{new}.tags.json")
    run_scan(root, exiftool_command=FAKE_COMMAND)

    summary = run_plan(root)
    assert summary.plan_id is None and summary.already_conforming == 3
    assert summary.events == 2
    assert [(start, end) for start, end, _ in stored_events(root)] == [
        ("2019-07-04T18:23:11", "2019-07-04T19:00:00"),
        ("2019-08-01T09:00:00", "2019-08-01T09:00:00"),
    ]


def test_a_failure_between_grouping_and_the_plan_write_leaves_no_events(tmp_path, monkeypatch):
    # spec 6.3 rule 5b rev 14: grouping runs "inside the plan's own transaction".
    # M697's lesson applies literally - prove the TRANSACTION, not the row count - so
    # this asserts on the rows the dangerous window would have left behind, over a
    # database that has never held an events row. "None" here cannot be confused with
    # "the previous run's", and both tables are checked: replace_events writes
    # file_events too, and a half-committed step 0 could leave either.
    root = scanned(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr("pam.catalog.repo.create_plan", boom)
    with pytest.raises(RuntimeError):
        run_plan(root)

    assert stored_events(root) == []
    conn = connect(workspace_for(root).db_path)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM file_events").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM plans").fetchone()["n"] == 0
    finally:
        conn.close()

    # F3 of the Task 8 review - the anti-vacuity floor this half was missing, and the
    # exact question M697 says to ask: an empty table cannot on its own tell "rolled
    # back" from "there was never anything to write". M954 made
    # dated_files_for_events select nothing and this test stayed GREEN while thirteen
    # others fired. So the same run is repeated unobstructed, and it must really
    # produce the partition whose absence was just asserted.
    monkeypatch.undo()
    assert run_plan(root).events == 2
    assert len(stored_events(root)) == 2


def test_a_failure_mid_write_leaves_the_previous_runs_events_intact(tmp_path, monkeypatch):
    # The other half of the same property, and the one an empty-database test cannot
    # reach: replace_events DELETES before it inserts, so a step 0 that committed
    # while the plan write failed would leave the archive regrouped under parameters
    # no plan records, with the still-open previous plan frozen under a partition that
    # no longer exists. The [events] edit is what makes the rollback observable - a
    # 30-minute gap splits the two July photographs that 4 hours keeps together.
    root = scanned(tmp_path)
    run_plan(root)
    before = stored_events(root)
    assert len(before) == 2, "the fixture no longer groups, so this test proves nothing"

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    workspace_for(root).config_path.write_text("[events]\ngap_hours = 0.5\n", encoding="utf-8")
    monkeypatch.setattr("pam.catalog.repo.insert_plan_items", boom)
    with pytest.raises(RuntimeError):
        run_plan(root)

    assert stored_events(root) == before


def test_the_frozen_config_key_set_is_exactly_the_specs_list(tmp_path):
    root = scanned(tmp_path)
    plan_id = run_plan(root).plan_id
    assert set(config_json_of(root, plan_id)) == FROZEN_CONFIG_KEYS


def test_a_non_default_events_config_reaches_both_the_grouping_and_the_freeze(tmp_path):
    # One edit, two records that must agree: the partition it produced, and what
    # plans.config_json says that partition was produced under (spec 6.3 rule 5a rev
    # 14). 0.5 h splits the two July photographs; 4 h does not.
    root = scanned(tmp_path)
    workspace_for(root).config_path.write_text(
        "[events]\ngap_hours = 0.5\ngps_km = 25\n", encoding="utf-8"
    )
    summary = run_plan(root)
    assert summary.events == 3
    frozen = config_json_of(root, summary.plan_id)
    assert (frozen["gap_hours"], frozen["gps_km"]) == (0.5, 25.0)


def test_an_integer_parameter_freezes_as_the_number_the_identity_records(tmp_path):
    # pam.toml may hold `gap_hours = 4` where the default is 4.0, and json.dumps
    # writes those as `4` and `4.0`. Both of one run's records - plans.config_json and
    # events.params_json - must say the same thing about the same number, or a reader
    # comparing them sees a parameter change that never happened.
    # repo/events.py::_params_json coerces for the identity (M912); the planner
    # coerces for the freeze. The expected identity string is spelled out rather than
    # read back from the writer, which would agree with any coercion at all.
    root = scanned(tmp_path)
    ws = workspace_for(root)
    ws.config_path.write_text("[events]\ngap_hours = 4\ngps_km = 50\n", encoding="utf-8")
    summary = run_plan(root)

    frozen = config_json_of(root, summary.plan_id)
    assert (frozen["gap_hours"], frozen["gps_km"]) == (4.0, 50.0)
    conn = connect(ws.db_path)
    try:
        raw_config = conn.execute(
            "SELECT config_json FROM plans WHERE id = ?", (summary.plan_id,)
        ).fetchone()["config_json"]
        identities = {r["params_json"] for r in conn.execute("SELECT params_json FROM events")}
    finally:
        conn.close()
    assert identities == {'{"gap_hours": 4.0, "gps_km": 50.0}'}
    # F2 of the Task 8 review. The two assertions above are numeric, and `4 == 4.0` in
    # Python - so json.loads() hands back a value that agrees with EITHER spelling and
    # M942 removed the planner's float() with the whole suite green. The claim the
    # coercion actually makes is about the TEXT the two records carry, so the text is
    # what has to be read. Spelled out from the spec side, not built from the value.
    assert '"gap_hours": 4.0' in raw_config, raw_config
    assert '"gps_km": 50.0' in raw_config, raw_config


def test_editing_the_events_config_does_not_supersede_on_a_rescan_but_a_replan_does(tmp_path):
    # spec 6.3 rule 5b rev 14, pinned so a later reader does not add a supersede rule
    # that already exists elsewhere. `pam scan` has exactly two supersede triggers
    # (spec 5.1 rev 6): file changes, and a companion-GROUPING membership change.
    # An [events] edit is neither, because scan does not compute events at all - so
    # the open plan survives the scan. It is not stale in the meantime either: rule 5
    # supersedes unconditionally on the next plan, which is the only command that
    # reads those keys.
    root = scanned(tmp_path)
    first = run_plan(root).plan_id
    ws = workspace_for(root)
    ws.config_path.write_text("[events]\ngap_hours = 0.5\n", encoding="utf-8")

    run_scan(root, exiftool_command=FAKE_COMMAND)
    conn = connect(ws.db_path)
    try:
        assert repo.plan_status(conn, first) == "open"
    finally:
        conn.close()
    # ... and the scan did not regroup either, for the same reason.
    assert len(stored_events(root)) == 2

    second = run_plan(root).plan_id
    conn = connect(ws.db_path)
    try:
        assert repo.plan_status(conn, first) == "superseded"
        assert repo.plan_status(conn, second) == "open"
    finally:
        conn.close()
    assert len(stored_events(root)) == 3  # and only now are the new parameters in force


# ---------------------------------------------------------------------------
# C11 (m10.md): the skipped `{seq}` number, and the family that explains it.
# ---------------------------------------------------------------------------


def _draft(old_rel_path, generated_filename):
    return repo.PlanItemDraft(
        file_id=1,
        old_rel_path=old_rel_path,
        unit_key="u",
        generated_filename=generated_filename,
        tokens_json="[]",
        tier="high",
        trust_score=100,
        trust_reasons_json="[]",
    )


def test_the_family_spells_every_LOWER_number_and_never_its_own(tmp_path):
    """Spec 6.1: `{seq}` is 3-digit, and a stem is its token values joined by one `_`.

    Spelled here from those two sentences, not from `collision._seq_family` or
    `_number_is_blocked` - and this test is the independent third reading of the same
    definition, so a co-mutation of the production function and one oracle still moves
    something. The assigned number's own name is deliberately absent: it is the name the
    plan proposes, not a name that was skipped.
    """
    from pam.naming.families import proposed_families as _proposed_families

    families = _proposed_families([_draft("2019/IMG_1.jpg", "2019-07-04_004.jpg")])
    assert families == {
        "2019/2019-07-04_001.jpg",
        "2019/2019-07-04_002.jpg",
        "2019/2019-07-04_003.jpg",
    }


def test_the_family_is_scoped_to_the_items_OWN_DIRECTORY(tmp_path):
    """`{seq}` is unique per DIRECTORY (spec 6.3 rule 2's per-directory taken set).

    A ghost row of the same name in another folder blocks nothing and must not be named,
    so every name this returns carries the item's own `rel_path` directory.

    THE FIXTURE MUST HOLD TWO DIRECTORIES, AND IT DID NOT UNTIL THIS REVIEW (M1728,
    finding F1). It drove one ROOT-level draft, where `rel_dir` is `""` and the prefixing
    branch is a no-op - so the test named for the directory scope **passed with the
    directory scope deleted**, and the property was carried entirely by a sibling test's
    literals. `CLAUDE.md`: *a test name can claim a property its fixture cannot exercise*.
    Two directories, one identical filename, and an explicit refusal of the bare name.
    """
    from pam.naming.families import proposed_families as _proposed_families

    # Root level: no prefix, because there is no directory.
    assert _proposed_families([_draft("IMG_1.jpg", "2019-07-04_002.jpg")]) == {
        "2019-07-04_001.jpg"
    }
    # Two folders, the SAME proposed filename. Each family stays in its own folder, and
    # neither reaches into the other or into the root.
    families = _proposed_families(
        [
            _draft("2019/a/IMG_1.jpg", "2019-07-04_002.jpg"),
            _draft("2019/b/IMG_2.jpg", "2019-07-04_003.jpg"),
        ]
    )
    assert families == {
        "2019/a/2019-07-04_001.jpg",
        "2019/b/2019-07-04_001.jpg",
        "2019/b/2019-07-04_002.jpg",
    }
    # The bare name is the mutation this test exists to kill: an unprefixed family would
    # match a ghost row in ANY folder, including the root.
    assert "2019-07-04_001.jpg" not in families


def test_the_family_REFUSES_a_stem_whose_seq_position_is_ambiguous(tmp_path):
    """Under-report, never over-report: the asymmetry is the opposite of the search's.

    `_number_is_blocked` prefers to under-report because over-reporting skips a free number
    and changes a FILENAME. Nothing but a sentence is computed from this, so here
    over-reporting tells the user something false about their archive. Two three-digit
    parts make "which one is the number" unanswerable from the text alone.
    """
    from pam.naming.families import proposed_families as _proposed_families

    assert _proposed_families([_draft("a.jpg", "2019-07-04_007_003.jpg")]) == set()
    assert _proposed_families([_draft("a.jpg", "2019-07-04.jpg")]) == set()  # no seq
    assert _proposed_families([_draft("a.jpg", "2019-07-04_0003.jpg")]) == set()  # 4 wide
    assert _proposed_families([_draft("a.jpg", "no_extension_001")]) == set()


def test_the_family_of_number_one_is_EMPTY_because_nothing_below_it_was_skipped(tmp_path):
    # The ordinary archive. A guard that returned "every name in the family" rather than
    # "every name BELOW this one" would report a skip on every plan pam has ever built.
    from pam.naming.families import proposed_families as _proposed_families

    assert _proposed_families([_draft("a.jpg", "2019-07-04_001.jpg")]) == set()


def test_a_number_held_by_a_GHOST_ROW_is_reported_to_the_user(tmp_path):
    """C11 end to end, over the fixture that already reproduces the ghost row.

    `test_a_deleted_files_name_is_never_proposed_to_another_file` above proves the number
    is skipped and must be; this proves pam now SAYS SO. The two halves are deliberately
    separate tests over the same construction: the behaviour is J7's and must not move,
    the sentence is C11's and is new.
    """
    root = scanned(tmp_path)
    run_plan(root)

    def new_rel(item):
        head, _, _ = item.old_rel_path.rpartition("/")
        return f"{head}/{item.new_filename}" if head else item.new_filename

    proposed = {i.old_rel_path: new_rel(i) for i in items(root).values()}
    for old_rel, new_path in proposed.items():
        (root / old_rel).rename(root / new_path)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    ghost = proposed["2019/IMG_0001.jpg"]
    (root / ghost).unlink()
    run_scan(root, exiftool_command=FAKE_COMMAND)

    (root / "2019" / "NEW_0001.jpg").write_bytes(b"z")
    (root / "2019" / "NEW_0001.jpg.tags.json").write_text(
        tags(DateTimeOriginal="2019:07:04 18:23:11"), encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    summary = run_plan(root)

    assert summary.blocked_by_missing == [ghost], (
        f"expected the ghost {ghost!r} to be named, got {summary.blocked_by_missing}"
    )
    # And a PRESENT file holding a lower number is not named: it is on disk, nothing is
    # wrong with it, and telling the user about it would be noise on every archive.
    present_holder = proposed["2019/IMG_0002.jpg"]
    assert present_holder not in summary.blocked_by_missing


def test_an_archive_with_no_ghost_rows_reports_NOTHING_blocked(tmp_path):
    # The negative control the test above needs. Every plan in this repository's fixtures
    # would otherwise satisfy a guard that simply listed lower-numbered names.
    root = scanned(tmp_path)
    assert run_plan(root).blocked_by_missing == []


# ---------------------------------------------------------------------------
# THE DELETION FLOOR (reviewer's F2, on M1670 and on M1734).
#
# Measured by this review: deleting Task 6's whole C11 section left 2992 passed + 1 skipped, exit 0 - and with it every one of M1721-M1729's kills. `_proposed_families` is production code no other module tests.
#
# HAND-SPELLED, NEVER DERIVED FROM `globals()`. An inventory that asks the module what it
# contains passes the very mutation it exists to catch (`CLAUDE.md`'s `repo.ENRICHERS`
# lesson): delete the test and the name goes with it, and the two agree about less.
# ---------------------------------------------------------------------------

LOAD_BEARING_CONTROLS = {
    "test_the_family_spells_every_LOWER_number_and_never_its_own": (
        "the INDEPENDENT THIRD READING of spec 6.1's `{seq}` (3-digit) and its "
        "assembly rule. `_proposed_families` is itself a second implementation, "
        "written not to call the collision search; this literal set is what stands "
        "behind the sentence when both readings drift together (M8's M922/M924). "
        "It killed M1724 (03d -> 02d), M1727 (own number included) and M1728 "
        "(directory scope dropped)"
    ),
    "test_the_family_is_scoped_to_the_items_OWN_DIRECTORY": (
        "spec 6.3 rule 2's PER-DIRECTORY taken set. Rewritten by this review: its "
        "old root-level fixture could not express the property its name claims and "
        "passed with the scope deleted (M1728, finding F1)"
    ),
    "test_the_family_REFUSES_a_stem_whose_seq_position_is_ambiguous": (
        "the only fixture in the repository with two three-digit parts. The live "
        "pipeline cannot build that stem, so nothing but a hand-built case reaches "
        "the >= 2 branch (M1725); the sole guardian of under-reporting"
    ),
    "test_the_family_of_number_one_is_EMPTY_because_nothing_below_it_was_skipped": (
        "the ORDINARY archive. Without it a family that returned every name rather "
        "than every name BELOW its own would report a skip on every plan pam has "
        "ever built (M1727)"
    ),
    "test_a_number_held_by_a_GHOST_ROW_is_reported_to_the_user": (
        "C11 end to end, and the only place the `missing` status operand and the "
        "family operand are both live at once (M1721, M1722). It is also the only "
        "test that proves a PRESENT file holding a lower number is not named"
    ),
    "test_an_archive_with_no_ghost_rows_reports_NOTHING_blocked": (
        "its negative control. Every fixture in this repository would satisfy a "
        "guard that simply listed lower-numbered names"
    ),
}

assert LOAD_BEARING_CONTROLS, "this module's control list is empty; it protects nothing"


def test_no_seq_family_control_in_this_file_can_be_deleted_without_saying_so():
    """The floor under this module's C11 inventory (reviewer's F2)."""
    present = {name for name in globals() if name.startswith("test_")}
    missing = sorted(set(LOAD_BEARING_CONTROLS) - present)
    assert missing == [], (
        f"a load-bearing control has been deleted from {Path(__file__).name}:"
        f" {missing}. Each is why another assertion here means anything"
        f" ({[LOAD_BEARING_CONTROLS[m] for m in missing]})."
    )
