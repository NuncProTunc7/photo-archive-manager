"""Companion atomicity (spec 7.3, spec 7.0 J6): a group renames together or not at all.

`pam/execute/unit.py` owns this, and every test here drives it the way apply does -
an archive of real files on a real temp filesystem, scanned, planned and applied, asserted
against the files that are actually on disk afterwards. Nothing is mocked.

The invariant this module owns:

  J6 (unit atomicity) - for one `unit_key`, either every member is `done` or every
  member is `failed`/`reverted`. Pinned per-scenario below AND suite-wide by
  `tests/conftest.py::_no_unit_was_left_split`, which reads every catalog a test
  touched. The suite-wide tripwire is what makes J6 a property of pam rather than a
  property of the scenarios someone remembered to write.
"""

import logging

import pytest
from archives import (
    FAKE_COMMAND,
    approve_and_apply,
    archive_names,
    catalog,
    companion_archive,
    hashes,
    item_statuses,
    journal_pairs,
    planned,
    put,
)

from pam.catalog import repo
from pam.execute.journal_ops import RenameItem, RenameTransaction
from pam.execute.recover import recover
from pam.execute.rename import perform_verified_rename
from pam.execute.unit import revert_unit
from pam.naming.planner import run_plan
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan

# --- the whole unit succeeds, or nothing in it moves -------------------------------


def test_a_companion_unit_renames_every_member_under_one_stem(tmp_path):
    """The common real case - a phone library is mostly live-photo groups. One
    `unit_key`, one RenameTransaction, all intents committed together (spec 7.3),
    then the renames member by member."""
    root, plan_id = companion_archive(tmp_path)

    summary = approve_and_apply(plan_id, root)

    assert (summary.renamed, summary.failed) == (2, 0)
    assert (root / "2019-07-04_001.JPG").exists()
    assert (root / "2019-07-04_001.MOV").exists()
    conn = catalog(root)
    try:
        assert repo.plan_status(conn, plan_id) == "applied"
        unit_keys = {item.unit_key for item in repo.plan_items(conn, plan_id)}
        assert len(unit_keys) == 1
        assert {r.session_id for r in repo.rows_in_state(conn, "done")} == {summary.session_id}
    finally:
        conn.close()


def test_a_unit_blocked_before_any_rename_leaves_every_member_where_it_was(tmp_path):
    """spec 7.3: a group is renamed together or not at all (vision S3).

    Blocked BEFORE `begin()`, so no file has moved and no journal row exists - which
    is why this case needs no revert at all. The catalog no longer describes the .MOV
    where the plan said it was, and that disagreement fails the whole unit before the
    first rename, when doing so is free.
    """
    root, plan_id = companion_archive(tmp_path)
    conn = catalog(root)
    try:
        repo.set_file_path(conn, repo.all_files(conn)["IMG_1.MOV"].id, "ADOPTED.MOV")
        conn.commit()
    finally:
        conn.close()

    summary = approve_and_apply(plan_id, root)

    assert (summary.renamed, summary.failed) == (0, 2)
    assert {name for name, _ in summary.failures} == {"IMG_1.JPG", "IMG_1.MOV"}
    assert archive_names(root) == ["IMG_1.JPG", "IMG_1.MOV"]
    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "intent") == []
        assert set(item_statuses(conn, plan_id).values()) == {"failed"}
        assert repo.plan_status(conn, plan_id) == "partially_applied"
    finally:
        conn.close()


def test_the_rest_of_a_unit_is_not_attempted_once_a_member_fails(tmp_path):
    """spec 7.3: when a member fails, the WHOLE unit is failed.

    The FIRST member's target is occupied, so nothing in this unit is ever renamed -
    and the remaining member must be resolved rather than left in `intent`, or spec
    7.2's recovery would find an intent row for a rename that was never attempted and
    spec 7.0 J7 would have work waiting on every future invocation.
    """
    root, plan_id = companion_archive(tmp_path)
    (root / "2019-07-04_001.JPG").write_bytes(b"in the way")

    summary = approve_and_apply(plan_id, root)

    assert (summary.renamed, summary.failed) == (0, 2)
    assert (root / "IMG_1.JPG").exists() and (root / "IMG_1.MOV").exists()
    assert not (root / "2019-07-04_001.MOV").exists()
    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "intent") == []
        assert journal_pairs(conn, "failed") == [
            ("IMG_1.JPG", "2019-07-04_001.JPG"),
            ("IMG_1.MOV", "2019-07-04_001.MOV"),
        ]
        assert set(item_statuses(conn, plan_id).values()) == {"failed"}
    finally:
        conn.close()
    # Nothing moved, so there was nothing to put back.
    assert "journal reverted" not in summary.log_path.read_text(encoding="utf-8")


# --- the revert loop (spec 7.3, spec 7.0 J6) ---------------------------------------


def test_a_member_failing_after_an_earlier_one_moved_puts_the_earlier_one_back(tmp_path):
    """spec 7.0 J6, the case the whole module exists for.

    The .JPG is renamed, then the .MOV's target turns out to be occupied. Leaving the
    .JPG at its new name would split one companion group across two stems - vision S3,
    the outcome spec 7.3 exists to prevent - so it is renamed BACK and the whole unit
    is `failed`.

    This replaces M5 Task 6's `test_a_member_failing_after_an_earlier_one_moved_is_
    reported_not_hidden`, which recorded the interim behaviour (earlier members left
    renamed, the split merely logged) as a decision on the record until this task.
    """
    root, plan_id = companion_archive(tmp_path)
    (root / "2019-07-04_001.MOV").write_bytes(b"in the way")
    before = hashes(root)

    summary = approve_and_apply(plan_id, root)

    assert (summary.renamed, summary.failed) == (0, 2)
    # Both members back under their original names, beside the blocker that arrived
    # after the scan. Nothing was rewritten - pam renames, it never edits bytes.
    assert archive_names(root) == ["2019-07-04_001.MOV", "IMG_1.JPG", "IMG_1.MOV"]
    assert hashes(root) == before

    # The member that moved and moved back gets its OWN sentence. Asserted because
    # swapping UNIT_REVERTED_REASON for UNIT_ALREADY_FAILED_REASON left the suite
    # green, reporting a file the user watched move as one pam never touched.
    reasons = dict(summary.failures)
    assert "was renamed and then put back" in reasons["IMG_1.JPG"]
    assert "never overwrites" in reasons["IMG_1.MOV"]  # its own rename is what failed

    conn = catalog(root)
    try:
        # spec 7.0 J2: `done -> reverted` is a legal transition and the row survives
        # in the journal saying exactly what happened.
        assert journal_pairs(conn, "reverted") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
        assert journal_pairs(conn, "failed") == [("IMG_1.MOV", "2019-07-04_001.MOV")]
        assert repo.rows_in_state(conn, "done") == []
        assert repo.rows_in_state(conn, "intent") == []
        assert set(item_statuses(conn, plan_id).values()) == {"failed"}
        # The catalog agrees with the disk again.
        assert repo.all_files(conn)["IMG_1.JPG"].rel_path == "IMG_1.JPG"
        assert repo.plan_status(conn, plan_id) == "partially_applied"
    finally:
        conn.close()


def test_a_three_member_unit_blocked_at_its_second_member_puts_the_first_back(tmp_path):
    """The plan's headline scenario: all three original filenames back on disk.

    Member 1 (.JPG) is renamed, member 2 (.MOV) finds its target occupied, member 3
    (.XMP) is never attempted. Every journal row still reaches a terminal state -
    `reverted` for the one that moved, `failed` for the two that did not - so spec
    7.2's recovery has nothing left to find (spec 7.0 J7).
    """
    root, plan_id = companion_archive(tmp_path, ("IMG_1.JPG", "IMG_1.MOV", "IMG_1.XMP"))
    (root / "2019-07-04_001.MOV").write_bytes(b"in the way")
    before = hashes(root)

    summary = approve_and_apply(plan_id, root)

    assert (summary.renamed, summary.failed) == (0, 3)
    assert archive_names(root) == [
        "2019-07-04_001.MOV",
        "IMG_1.JPG",
        "IMG_1.MOV",
        "IMG_1.XMP",
    ]
    assert hashes(root) == before
    assert summary.stranded == []

    # All three sentences at once, which is the only place they can be told apart:
    # the .JPG moved and moved back, the .MOV's own rename is what failed, and the
    # .XMP was never attempted. Three histories, three sentences - and each one used
    # to be swappable for another with the suite green.
    reasons = dict(summary.failures)
    assert "was renamed and then put back" in reasons["IMG_1.JPG"]
    assert "never overwrites" in reasons["IMG_1.MOV"]
    assert "was left alone" in reasons["IMG_1.XMP"]

    conn = catalog(root)
    try:
        assert journal_pairs(conn, "reverted") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
        assert journal_pairs(conn, "failed") == [
            ("IMG_1.MOV", "2019-07-04_001.MOV"),
            ("IMG_1.XMP", "2019-07-04_001.XMP"),
        ]
        assert repo.rows_in_state(conn, "done") == []
        assert repo.rows_in_state(conn, "intent") == []
        assert set(item_statuses(conn, plan_id).values()) == {"failed"}
    finally:
        conn.close()


def test_a_unit_failed_before_any_rename_is_durable_without_a_later_commit(tmp_path, set_fault):
    """`_fail_unit_before_any_rename` commits its OWN writes (spec 7.0 J5).

    Deleting that commit left the suite green, because `_finalize_plan`'s commit swept
    the pending statuses in a moment later - the exact "uncommitted writes carried into
    somebody else's transaction" pattern `journal_ops.py`'s module docstring exists to
    forbid. A crash between the two is what separates them: one unit only, so nothing
    else commits in between, and `crash:` unwinds through `conn.close()`, which rolls
    back anything still pending.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    conn = catalog(root)
    try:
        # The catalog and the plan now disagree about which path this item is about,
        # which fails the unit before a single file is touched.
        repo.set_file_path(conn, repo.all_files(conn)["IMG_1.JPG"].id, "ADOPTED.JPG")
        conn.commit()
    finally:
        conn.close()
    set_fault.arm("crash:before_plan_finalize:1")

    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)

    assert set_fault.hits("before_plan_finalize") == 1
    set_fault.next_invocation()

    conn = catalog(root)
    try:
        assert item_statuses(conn, plan_id)["IMG_1.JPG"] == "failed"
    finally:
        conn.close()


def test_the_revert_is_attempted_in_reverse_order(tmp_path):
    """Newest first, so the archive walks backwards through the states it walked
    forwards through and no member is ever absent from both of its names at once.

    Asserted against the session log's rename lines, which record what the filesystem
    was actually asked to do - not against journal row ids, which are insertion order
    and would read the same whichever way round the revert ran.
    """
    root, plan_id = companion_archive(tmp_path, ("IMG_1.JPG", "IMG_1.MOV", "IMG_1.XMP"))
    (root / "2019-07-04_001.XMP").write_bytes(b"in the way")

    summary = approve_and_apply(plan_id, root)

    assert (summary.renamed, summary.failed) == (0, 3)
    log = summary.log_path.read_text(encoding="utf-8")
    put_back_mov = log.index("renamed: 2019-07-04_001.MOV -> IMG_1.MOV")
    put_back_jpg = log.index("renamed: 2019-07-04_001.JPG -> IMG_1.JPG")
    assert put_back_mov < put_back_jpg, "the LAST member renamed is the FIRST put back"

    conn = catalog(root)
    try:
        assert {pair[0] for pair in journal_pairs(conn, "reverted")} == {
            "IMG_1.JPG",
            "IMG_1.MOV",
        }
    finally:
        conn.close()


def test_a_unit_and_a_lone_file_in_the_same_plan_are_independent(tmp_path):
    """spec 7.3's atomicity is per unit, never per plan. A failed companion group
    must not hold back an unrelated file, and vision principle 9 is explicit that a
    run records, counts and continues."""
    root = tmp_path / "photos"
    root.mkdir()
    put(root, "IMG_1.JPG")
    put(root, "IMG_1.MOV")
    put(root, "IMG_2.JPG", date="2020:08:05 09:15:00")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    assert plan_id is not None
    (root / "2019-07-04_001.MOV").write_bytes(b"in the way")

    summary = approve_and_apply(plan_id, root)

    assert (summary.renamed, summary.failed) == (1, 2)
    # The unit is back where it started; the lone file went through.
    assert archive_names(root) == [
        "2019-07-04_001.MOV",
        "2020-08-05_001.JPG",
        "IMG_1.JPG",
        "IMG_1.MOV",
    ]
    conn = catalog(root)
    try:
        assert journal_pairs(conn, "done") == [("IMG_2.JPG", "2020-08-05_001.JPG")]
        assert item_statuses(conn, plan_id)["IMG_2.JPG"] == "applied"
        assert repo.plan_status(conn, plan_id) == "partially_applied"
    finally:
        conn.close()


# --- the unit is the one FROZEN in unit_key (spec 7.3 rev 6, vision S3) ------------


def test_the_unit_applied_is_the_one_frozen_in_the_plan_not_the_live_grouping(tmp_path):
    """The rev 6 amendment's whole point, driven at its second layer.

    `IMG_1.JPG` and `IMG_1_edit.MOV` are one companion group only while
    `scan.ignore_stem_suffixes` says `_edit` is an editor suffix. The plan freezes
    that grouping into `unit_key`. Then the config is removed and two sidecars
    arrive, so a re-scan regroups the same two files into **two different**
    `companion_group_id`s - and the failure this amendment exists to prevent is apply
    consulting that live grouping and renaming half the pair.

    Layer 1 (the scan superseding the open plan on a grouping-membership change) also
    fires here and is asserted; the plan is then put back to `open` on purpose, to
    reach layer 2 the way a plan built before that amendment would. Both layers are
    load-bearing and only one of them is this module's.
    """
    root = tmp_path / "photos"
    root.mkdir()
    put(root, "IMG_1.JPG")
    put(root, "IMG_1_edit.MOV")
    run_scan(root, exiftool_command=FAKE_COMMAND)

    ws = workspace_if_exists(root)
    assert ws is not None
    ws.config_path.write_text('[scan]\nignore_stem_suffixes = ["_edit"]\n', encoding="utf-8")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    assert plan_id is not None

    conn = catalog(root)
    try:
        assert len({item.unit_key for item in repo.plan_items(conn, plan_id)}) == 1
    finally:
        conn.close()

    put(root, "IMG_1.XMP")
    put(root, "IMG_1_edit.XMP")
    ws.config_path.unlink()
    run_scan(root, exiftool_command=FAKE_COMMAND)

    conn = catalog(root)
    try:
        # Layer 1: the scan noticed the grouping changed and superseded the plan.
        assert repo.plan_status(conn, plan_id) == "superseded"
        files = repo.all_files(conn)
        # The live grouping now says these two files have nothing to do with each other.
        assert (
            files["IMG_1.JPG"].companion_group_id != files["IMG_1_edit.MOV"].companion_group_id
        )
        repo.set_plan_status(conn, plan_id, "open")
        conn.commit()
    finally:
        conn.close()

    (root / "2019-07-04_001.MOV").write_bytes(b"in the way")
    summary = approve_and_apply(plan_id, root)

    # Grouped by unit_key, the .JPG's rename is reverted when the .MOV fails. Grouped
    # by the live companion_group_id they are two independent units, the .JPG keeps
    # its new name, and the pair is left split across two stems (vision S3).
    assert (summary.renamed, summary.failed) == (0, 2)
    assert archive_names(root) == [
        "2019-07-04_001.MOV",
        "IMG_1.JPG",
        "IMG_1.XMP",
        "IMG_1_edit.MOV",
        "IMG_1_edit.XMP",
    ]
    conn = catalog(root)
    try:
        assert journal_pairs(conn, "reverted") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
    finally:
        conn.close()


# --- spec 9.3's mid_companion_unit checkpoint -------------------------------------


def test_a_crash_between_members_leaves_one_done_and_the_next_in_intent(tmp_path, set_fault):
    """The state spec 7.3 hands to spec 7.2's unit-consistency pass: a mixed unit.

    spec 7.0 J6 calls this a TRANSIENT state, legal only until recovery resolves it -
    which is why the checkpoint sits between members, after one is fully resolved and
    before the next is touched.
    """
    root, plan_id = companion_archive(tmp_path)
    set_fault.arm("crash:mid_companion_unit:1")

    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)

    assert set_fault.hits("mid_companion_unit") == 1
    set_fault.next_invocation()

    assert archive_names(root) == ["2019-07-04_001.JPG", "IMG_1.MOV"]
    conn = catalog(root)
    try:
        assert journal_pairs(conn, "done") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
        assert journal_pairs(conn, "intent") == [("IMG_1.MOV", "2019-07-04_001.MOV")]
    finally:
        conn.close()


def test_the_between_members_checkpoint_never_fires_for_lone_files(tmp_path, set_fault):
    """It is the COMPANION checkpoint. A plan of single-file units has no "between",
    so a scenario armed with it must run to completion rather than crash - and a
    checkpoint that fired before the FIRST member would make every crash test written
    against it prove something else entirely."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    set_fault.arm("crash:mid_companion_unit:1")

    summary = approve_and_apply(plan_id, root)

    assert (summary.renamed, summary.failed) == (2, 0)
    assert set_fault.hits("mid_companion_unit") == 0
    set_fault.next_invocation()


def test_a_crash_mid_revert_leaves_a_split_unit_that_recovery_heals(tmp_path, set_fault):
    """spec 9.3 rev 8's `mid_unit_revert`, and the state only spec 7.2 phase 2 can heal.

    Three members: 1 and 2 are renamed, 3's target is occupied, and the crash lands
    after member 2 has gone back but before member 1 does. What survives is a unit
    holding a `reverted` beside a `done` and a `failed` - and **zero `intent` rows**,
    which is what makes it unreachable by spec 7.2's phase 1.

    That is the whole reason the checkpoint exists. Under `mid_companion_unit` the
    split exists only because phase 1 turned an `intent` row into a `failed` one, so a
    consistency pass wrongly implemented as a follow-up inside the intent loop would
    still pass that scenario. It cannot pass this one.

    Until M5 Task 8 this test asserted the split and carried an `allow_split_unit`
    sanction, because nothing could yet resolve it. **Phase 2 exists now, so the
    sanction is gone**: recovery is run here and the unit must come back whole. An
    escape hatch that quietly accumulates users is what the tripwire's own review
    caught, so it keeps exactly one - the test that manufactures interference pam
    genuinely cannot undo.
    """
    root, plan_id = companion_archive(tmp_path, ("IMG_1.JPG", "IMG_1.MOV", "IMG_1.XMP"))
    (root / "2019-07-04_001.XMP").write_bytes(b"in the way")
    before = hashes(root)
    set_fault.arm("crash:mid_unit_revert:2")

    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)

    assert set_fault.hits("mid_unit_revert") == 2
    set_fault.next_invocation()

    # Member 2 (.MOV) went back; member 1 (.JPG) did not - the archive is split.
    assert archive_names(root) == [
        "2019-07-04_001.JPG",
        "2019-07-04_001.XMP",
        "IMG_1.MOV",
        "IMG_1.XMP",
    ]
    conn = catalog(root)
    try:
        assert journal_pairs(conn, "done") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
        assert journal_pairs(conn, "reverted") == [("IMG_1.MOV", "2019-07-04_001.MOV")]
        assert journal_pairs(conn, "failed") == [("IMG_1.XMP", "2019-07-04_001.XMP")]
        # The point: nothing for phase 1 to iterate, and the plan never finalized.
        assert repo.rows_in_state(conn, "intent") == []
        assert repo.plan_status(conn, plan_id) == "applying"
    finally:
        conn.close()

    conn = catalog(root)
    try:
        summary = recover(conn, root, logging.getLogger("tests.mid_revert"))
    finally:
        conn.close()

    assert summary.reverted == ["IMG_1.JPG"]
    assert summary.stranded == []
    assert archive_names(root) == [
        "2019-07-04_001.XMP",
        "IMG_1.JPG",
        "IMG_1.MOV",
        "IMG_1.XMP",
    ]
    assert hashes(root) == before
    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "done") == []
        assert journal_pairs(conn, "reverted") == [
            ("IMG_1.JPG", "2019-07-04_001.JPG"),
            ("IMG_1.MOV", "2019-07-04_001.MOV"),
        ]
        assert repo.all_files(conn)["IMG_1.JPG"].rel_path == "IMG_1.JPG"
        assert repo.plan_status(conn, plan_id) == "partially_applied"
    finally:
        conn.close()


def test_the_revert_checkpoint_never_fires_when_no_member_was_renamed(tmp_path, set_fault):
    """It is the REVERT checkpoint. A unit whose FIRST member fails renames nothing,
    so there is nothing to put back and the loop body is never entered - and a
    checkpoint that fired anyway would make every scenario written against it prove
    something else entirely (the lesson `mid_companion_unit`'s guard already paid for).
    """
    root, plan_id = companion_archive(tmp_path)
    (root / "2019-07-04_001.JPG").write_bytes(b"in the way")
    set_fault.arm("crash:mid_unit_revert:1")

    summary = approve_and_apply(plan_id, root)

    assert (summary.renamed, summary.failed) == (0, 2)
    assert set_fault.hits("mid_unit_revert") == 0
    set_fault.next_invocation()


# --- a revert that cannot put the file back (spec 7.3: name it, never guess) -------


def test_revert_unit_leaves_a_blocked_file_done_and_returns_it(tmp_path):
    """spec 7.0 J4 in the one place it bites: `done` means pam PERFORMED the rename.

    Something outside pam took the old name back while pam was working - the class of
    state spec 7.0 describes as reachable by external interference, reported for human
    attention and acted on never. Resolving the row `reverted` would be a lie: the
    file is not back. It stays `done`, which is the truth, and the caller is told
    exactly which file so the report can name it.
    """
    root, plan_id = companion_archive(tmp_path)
    conn = catalog(root)
    try:
        item = repo.plan_items(conn, plan_id)[0]
        file_row = repo.all_files(conn)[item.old_rel_path]
        rename_item = RenameItem(
            plan_item_id=item.id,
            file_id=file_row.id,
            old_rel_path="IMG_1.JPG",
            new_rel_path="2019-07-04_001.JPG",
            size=file_row.size,
            mtime_ns=file_row.mtime_ns,
        )
        logger = logging.getLogger("test_revert_unit")
        txn = RenameTransaction(
            conn,
            session_id="s-20260806-120000",
            op="rename",
            items=[rename_item],
            logger=logger,
        )
        txn.begin()
        assert perform_verified_rename(
            archive_root=root,
            old_rel=rename_item.old_rel_path,
            new_rel=rename_item.new_rel_path,
            expect_size=rename_item.size,
            expect_mtime_ns=rename_item.mtime_ns,
            logger=logger,
        ).ok
        txn.commit(rename_item)

        # The interference: the old name is taken again before the revert runs.
        (root / "IMG_1.JPG").write_bytes(b"something else entirely")

        stranded = revert_unit(txn, archive_root=root, performed=[rename_item], logger=logger)

        assert stranded == [rename_item]
        assert (root / "2019-07-04_001.JPG").exists()
        assert (root / "IMG_1.JPG").read_bytes() == b"something else entirely"
        assert journal_pairs(conn, "done") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
        assert repo.rows_in_state(conn, "reverted") == []
    finally:
        conn.close()


def test_a_revert_that_fails_names_the_file_it_left_under_its_new_name(
    tmp_path, allow_split_unit
):
    """spec 7.3: the report names the exact files left in the intermediate state.

    The interference is injected through a logging handler rather than by patching
    pam: pam's own code runs untouched and the file really is created by something
    else, at the only instant that can produce this state - after the .JPG's rename
    and before the .MOV's failure. It is the one path that can leave a unit split, so
    the suite-wide J6 tripwire has to be told this test means to reach it.
    """
    root, plan_id = companion_archive(tmp_path)
    (root / "2019-07-04_001.MOV").write_bytes(b"in the way")

    class TakeTheOldNameBack(logging.Filter):
        def filter(self, record):
            if record.getMessage().startswith("renamed: IMG_1.JPG"):
                (root / "IMG_1.JPG").write_bytes(b"something else entirely")
            return True

    # A FILTER on the shared 'pam' logger, not a handler: `open_session_log` calls
    # `logger.handlers.clear()`, so a handler attached before `run_apply` would be
    # thrown away before the first rename.
    interference = TakeTheOldNameBack()
    logging.getLogger("pam").addFilter(interference)
    try:
        summary = approve_and_apply(plan_id, root)
    finally:
        logging.getLogger("pam").removeFilter(interference)

    assert summary.stranded == ["2019-07-04_001.JPG"]
    assert (summary.renamed, summary.failed) == (0, 2)
    stranded_reason = dict(summary.failures)["IMG_1.JPG"]
    assert "2019-07-04_001.JPG" in stranded_reason
    assert "could not put it back" in stranded_reason
    assert "is split" in summary.log_path.read_text(encoding="utf-8")

    conn = catalog(root)
    try:
        # Left `done`, because it is: pam performed that rename (spec 7.0 J4).
        assert journal_pairs(conn, "done") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
        assert journal_pairs(conn, "failed") == [("IMG_1.MOV", "2019-07-04_001.MOV")]
    finally:
        conn.close()


# --- the guards, proven enforcing rather than merely present ----------------------


def test_unit_py_never_resolves_a_journal_row_itself():
    """spec 7.1 rev 7: the journal state machine belongs to `journal_ops.py` alone.

    This module is the one most tempted to reach past it - it already knows the
    journal id it wants and the state it wants next - and a second implementation of
    `intent -> done` here is how apply, undo and recovery drift into three answers.
    """
    from pathlib import Path

    from guards import resolve_call_sites

    from pam.execute import unit

    assert resolve_call_sites(Path(unit.__file__).read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("second_state", ["failed", "reverted"])
def test_the_j6_tripwire_notices_a_unit_holding_a_done_and_a_resolved_back_member(
    tmp_path, second_state
):
    """`conftest._no_unit_was_left_split` proven to fail on the state it forbids.

    BOTH terminal states, because both are reachable and only one used to be tested:
    narrowing the tripwire's `{"failed", "reverted"}` to `{"failed"}` left the suite
    green. `done` + `reverted` is what a three-member unit produces when members 1 and
    2 are renamed, member 3 fails, member 2 goes back and member 1's revert is blocked
    - exactly the stranding case above, at one member more.

    Built outside PAM_HOME on purpose, so the autouse tripwire does not scan this
    deliberately-broken catalog and fail the test that exists to check it.
    """
    from conftest import _split_units

    from pam.catalog.db import connect, migrate

    db_path = tmp_path / "not-under-pam-home" / "catalog.db"
    db_path.parent.mkdir()
    conn = connect(db_path)
    try:
        migrate(conn)
        file_id = repo.upsert_file(
            conn,
            rel_path="IMG_1.JPG",
            filename="IMG_1.JPG",
            ext="jpg",
            media_type="image",
            size=1,
            mtime_ns=1,
            placeholder=False,
            now="2026-08-06T09:00:00",
        )
        plan_id = repo.create_plan(
            conn,
            template="{date}_{seq}",
            config_json="{}",
            now="2026-08-06T09:00:00",
            app_version="0",
            schema_version=4,
        )
        repo.insert_plan_items(
            conn,
            plan_id,
            [
                repo.PlanItemDraft(
                    file_id=file_id,
                    old_rel_path=f"IMG_{n}.JPG",
                    unit_key="one-unit",
                    generated_filename=f"2019-07-04_00{n}.JPG",
                    tokens_json="[]",
                    tier="high",
                    trust_score=100,
                    trust_reasons_json="[]",
                )
                for n in (1, 2)
            ],
        )
        items = repo.plan_items(conn, plan_id)
        ids = repo.insert_intents(
            conn,
            "s-20260806-090000",
            "rename",
            [
                repo.JournalEntry(
                    plan_item_id=item.id,
                    old_rel_path=item.old_rel_path,
                    new_rel_path=item.new_filename,
                    size=1,
                    mtime_ns=1,
                )
                for item in items
            ],
            now="2026-08-06T09:00:00",
        )
        assert _split_units(db_path) == []  # both still `intent`: legal and transient
        repo.resolve(conn, ids[0], "done", now="2026-08-06T09:00:01")
        if second_state == "reverted":
            # spec 7.0 J2: `reverted` is only ever reached from `done`.
            repo.resolve(conn, ids[1], "done", now="2026-08-06T09:00:01")
        repo.resolve(conn, ids[1], second_state, now="2026-08-06T09:00:02")
        conn.commit()
    finally:
        conn.close()

    assert _split_units(db_path) == [("s-20260806-090000", "one-unit")]


def test_the_j6_tripwire_reaches_the_catalog_an_apply_wrote(tmp_path, isolated_pam_home):
    """The tripwire's REACH, pinned from outside the tripwire.

    A suite-wide invariant that scans zero catalogs asserts nothing while every test
    stays green, and this one is the milestone's headline deliverable: pointing its
    glob at a name that matches nothing left all 752 tests passing. It cannot pin its
    own reach, so this does - and any later milestone that moves the workspace layout
    fails here rather than silently switching J6 off.
    """
    from conftest import catalogs_under

    root, plan_id = companion_archive(tmp_path)
    approve_and_apply(plan_id, root)

    found = catalogs_under(isolated_pam_home)
    assert found, "the J6 tripwire would scan nothing and assert nothing"
    assert workspace_if_exists(root).db_path in found
