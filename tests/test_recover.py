"""Crash recovery (spec 7.2 rev 8): four phases, run at the start of every command.

Every scenario here reaches its state the way a real crash would - an archive of real files on
a real temp filesystem, scanned, planned and applied under `PAM_FAULT`, then recovered
by the same `recover()` the CLI calls. Nothing is mocked and no journal row is written
by hand, because a hand-built row proves only that the recovery code agrees with the
test author about what a crash leaves behind.

The invariants this module owns, by name (spec 7.0):

  J3  - branch on the DISK, never on the catalog.
  J6  - unit atomicity, healed by phase 2 (also asserted suite-wide by conftest).
  J7  - recovery is idempotent and convergent; after it no row remains in `intent`.
  J9  - no row is resolved and no plan finalized while the archive is unreachable.
  J10 - no plan stays `applying`, and no plan in any other status is ever touched.
  J11 - a resolution is never blocked by the catalog.
"""

import logging
import sqlite3

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
)

from pam.catalog import repo
from pam.cli import main
from pam.errors import EXIT_OK, PamError
from pam.execute.apply import run_apply
from pam.execute.recover import derive_plan_status, recover, run_recovery
from pam.naming.planner import run_plan
from pam.scan.runner import run_scan

LOGGER = logging.getLogger("tests.recover")


def recover_now(root):
    """One recovery pass against a real workspace, as the CLI runs it."""
    conn = catalog(root)
    try:
        return recover(conn, root, LOGGER)
    finally:
        conn.close()


def snapshot(root, plan_id):
    """Everything a recovery pass could change: disk, journal, items, plan status."""
    conn = catalog(root)
    try:
        return (
            archive_names(root),
            {
                state: journal_pairs(conn, state)
                for state in ("intent", "done", "failed", "reverted")
            },
            item_statuses(conn, plan_id),
            repo.plan_status(conn, plan_id),
            {row.rel_path: row.status for row in repo.all_files(conn).values()},
        )
    finally:
        conn.close()


# --- nothing to recover -------------------------------------------------------------


def test_recovery_says_nothing_happened_when_nothing_was_interrupted(tmp_path):
    """The overwhelmingly common case: every command runs this, almost always for
    nothing. It must be silent, and it must not touch a single row."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    before = snapshot(root, plan_id)

    summary = recover_now(root)

    assert not summary.did_anything
    assert snapshot(root, plan_id) == before


def test_the_no_op_path_reads_four_times_and_writes_nothing(tmp_path):
    """Structural, not behavioural: one query per phase, one for E6's discovery, no write.

    M15 Task 7 (E6) adds the fourth read: every pass asks the journal which rename sessions
    are terminal, so a report a crash prevented is written by the next command.

    The M5 plan asked for "one indexed query"; spec 7.2 rev 8 gives recovery FOUR
    phases, and phases 2 and 3 must run even when phase 1 finds nothing (that is the
    whole point of the `mid_unit_revert` checkpoint). So the honest assertion is one
    query per phase that queries, and no write - which is what makes recovery free to
    run at the start of every invocation.
    """
    root, _plan_id = planned(tmp_path, ["IMG_1.JPG"])
    statements = []
    conn = catalog(root)
    try:
        conn.set_trace_callback(statements.append)
        recover(conn, root, LOGGER)
        conn.set_trace_callback(None)
    finally:
        conn.close()

    assert len(statements) == 4, statements
    assert all(sql.strip().upper().startswith("SELECT") for sql in statements), statements


# --- phase 1: the `intent` rows, branching on the disk (J3) -------------------------


def test_a_crash_before_the_intent_commit_leaves_nothing_at_all_to_recover(tmp_path, set_fault):
    """spec 7.0 J1 from the other side: no journal row, so nothing to branch on.

    The write-ahead rule says the intent row is COMMITTED before any file moves, so a
    crash on the wrong side of that commit must leave the archive exactly as it was and
    the journal empty. All recovery has left to do is finalize the plan.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    set_fault.arm("crash:before_intent_commit:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    assert set_fault.hits("before_intent_commit") == 1
    set_fault.next_invocation()

    summary = recover_now(root)

    assert (summary.adopted, summary.failed, summary.needs_attention) == ([], [], [])
    assert archive_names(root) == ["IMG_1.JPG"]
    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "intent") == []
        assert repo.session_rows(conn, "s-any") == []
        assert repo.plan_status(conn, plan_id) == "partially_applied"
    finally:
        conn.close()


def test_a_crash_after_the_intent_but_before_the_rename_keeps_the_original_name(
    tmp_path, set_fault
):
    """The safe branch: old path there, new path absent, so the rename never happened.

    Marked `failed`, which is terminal - pam does not retry a rename the user has not
    re-approved. The file is untouched, so the `files` row is left exactly as it is.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    set_fault.arm("crash:after_intent:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    assert set_fault.hits("after_intent") == 1
    set_fault.next_invocation()

    summary = recover_now(root)

    assert summary.failed == ["IMG_1.JPG"]
    assert summary.adopted == []
    assert archive_names(root) == ["IMG_1.JPG"]
    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "intent") == []
        assert journal_pairs(conn, "failed") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
        assert item_statuses(conn, plan_id) == {"IMG_1.JPG": "failed"}
        assert repo.all_files(conn)["IMG_1.JPG"].rel_path == "IMG_1.JPG"
        assert repo.plan_status(conn, plan_id) == "partially_applied"
    finally:
        conn.close()


@pytest.mark.parametrize("checkpoint", ["after_rename", "before_done_commit"])
def test_a_rename_that_happened_before_the_crash_is_adopted(tmp_path, set_fault, checkpoint):
    """The adoption branch: new path there, old path gone, so the rename DID happen.

    Both checkpoints leave the identical disk state and the identical `intent` row -
    `after_rename` crashes between the os.rename and the resolution, and
    `before_done_commit` crashes with the resolution written but not committed, which
    the rollback in `RenameTransaction.commit` discards. Recovery cannot tell them
    apart and must not try: it reads the disk (J3).
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    before = hashes(root)
    set_fault.arm(f"crash:{checkpoint}:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    assert set_fault.hits(checkpoint) == 1
    set_fault.next_invocation()

    summary = recover_now(root)

    assert summary.adopted == ["2019-07-04_001.JPG"]
    assert summary.failed == []
    assert archive_names(root) == ["2019-07-04_001.JPG"]
    assert hashes(root) == before  # pam renames; it never rewrites a byte
    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "intent") == []
        assert journal_pairs(conn, "done") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
        assert item_statuses(conn, plan_id) == {"IMG_1.JPG": "applied"}
        # The whole point of the branch: the catalog now agrees with the disk.
        assert "2019-07-04_001.JPG" in repo.all_files(conn)
        assert "IMG_1.JPG" not in repo.all_files(conn)
        assert repo.plan_status(conn, plan_id) == "applied"
    finally:
        conn.close()


def test_a_db_failure_before_the_resolution_leaves_a_recoverable_state(tmp_path, set_fault):
    """`dbfail:` is caught in-process, so the connection lives on - a different
    signature from `crash:` and the one that can leave writes pending on a connection
    somebody else will commit. The run must not end half-committed: the journal row
    stays `intent` (not a half-written `done`) and recovery adopts it normally."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    set_fault.arm("dbfail:before_done_commit:1")
    with pytest.raises(sqlite3.OperationalError):
        approve_and_apply(plan_id, root)
    assert set_fault.hits("before_done_commit") == 1
    set_fault.next_invocation()

    conn = catalog(root)
    try:
        assert journal_pairs(conn, "intent") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
        assert repo.rows_in_state(conn, "done") == []
    finally:
        conn.close()

    summary = recover_now(root)

    assert summary.adopted == ["2019-07-04_001.JPG"]
    conn = catalog(root)
    try:
        assert journal_pairs(conn, "done") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
        assert repo.rows_in_state(conn, "intent") == []
    finally:
        conn.close()


def test_both_names_present_is_reported_and_never_guessed_at(tmp_path, set_fault):
    """spec 7.2's third branch, and spec 7.0's closing paragraph.

    Something outside pam put a file back at the old name after the rename. pam did not
    create that state and cannot know which file the user wants where, so it touches
    NOTHING on disk, resolves the row `failed`, and names the file for a human. Guessing
    here is the one thing that could lose a photograph.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    set_fault.arm("crash:after_rename:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()
    (root / "IMG_1.JPG").write_bytes(b"something else entirely")

    summary = recover_now(root)

    assert summary.needs_attention == ["IMG_1.JPG"]
    assert summary.adopted == []
    # Nothing moved: both files are still exactly where they were found.
    assert archive_names(root) == ["2019-07-04_001.JPG", "IMG_1.JPG"]
    assert (root / "IMG_1.JPG").read_bytes() == b"something else entirely"
    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "intent") == []
        assert journal_pairs(conn, "failed") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
    finally:
        conn.close()


def test_neither_name_present_is_reported_and_never_guessed_at(tmp_path, set_fault):
    """The other half of the same branch: the file is gone from both names.

    Deleted outside pam between the crash and the recovery. There is nothing to adopt
    and nothing to leave alone, so the row is resolved `failed` and the file is named
    for a human - pam never invents a file it cannot see.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    set_fault.arm("crash:after_intent:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()
    (root / "IMG_1.JPG").unlink()

    summary = recover_now(root)

    assert summary.needs_attention == ["IMG_1.JPG"]
    assert summary.failed == []
    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "intent") == []
        assert journal_pairs(conn, "failed") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
    finally:
        conn.close()


def test_a_crash_mid_plan_adopts_what_moved_and_leaves_the_rest_approved(tmp_path, set_fault):
    """Three single-file units, a crash after the FIRST rename. spec 7.0 J5 in the
    large: every item already resolved is consistent with disk, and the two nobody
    reached are untouched - which is what makes the plan `partially_applied` rather
    than `applied`, from the item statuses alone."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG", "IMG_3.JPG"])
    set_fault.arm("crash:after_rename:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()

    summary = recover_now(root)

    assert summary.adopted == ["2019-07-04_001.JPG"]
    assert archive_names(root) == ["2019-07-04_001.JPG", "IMG_2.JPG", "IMG_3.JPG"]
    conn = catalog(root)
    try:
        assert item_statuses(conn, plan_id) == {
            "IMG_1.JPG": "applied",
            "IMG_2.JPG": "approved",
            "IMG_3.JPG": "approved",
        }
        assert repo.plan_status(conn, plan_id) == "partially_applied"
    finally:
        conn.close()


# --- phase 2: the unit-consistency pass (spec 7.0 J6, vision S3) --------------------


def crashed_mid_revert(tmp_path, set_fault):
    """A split unit with ZERO `intent` rows - the state only phase 2 can reach.

    Two members; the .MOV's target is occupied so the unit fails; the crash lands
    inside spec 7.3's revert loop before the .JPG goes back. What survives is `done`
    beside `failed`, no `intent` row anywhere, and the plan still `applying`.
    """
    root, plan_id = companion_archive(tmp_path)
    (root / "2019-07-04_001.MOV").write_bytes(b"in the way")
    set_fault.arm("crash:mid_unit_revert:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    assert set_fault.hits("mid_unit_revert") == 1
    set_fault.next_invocation()
    assert archive_names(root) == ["2019-07-04_001.JPG", "2019-07-04_001.MOV", "IMG_1.MOV"]
    return root, plan_id


def test_a_crash_between_unit_members_puts_the_whole_unit_back(tmp_path, set_fault):
    """spec 7.3 across a crash: a group is renamed together or not at all (vision S3).

    Three members, the crash between member 1 and member 2. Phase 1 resolves members 2
    and 3 (`intent`, never renamed) to `failed`; the unit is then mixed, and phase 2
    puts member 1 back. Neither phase alone is enough, which is why the order is
    normative.
    """
    root, plan_id = companion_archive(tmp_path, ("IMG_1.JPG", "IMG_1.MOV", "IMG_1.XMP"))
    before = hashes(root)
    set_fault.arm("crash:mid_companion_unit:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    assert set_fault.hits("mid_companion_unit") == 1
    set_fault.next_invocation()

    summary = recover_now(root)

    assert summary.reverted == ["IMG_1.JPG"]
    assert summary.stranded == []
    assert archive_names(root) == ["IMG_1.JPG", "IMG_1.MOV", "IMG_1.XMP"]
    assert hashes(root) == before
    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "intent") == []
        assert repo.rows_in_state(conn, "done") == []
        assert journal_pairs(conn, "reverted") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
        assert set(item_statuses(conn, plan_id).values()) == {"failed"}
        assert repo.all_files(conn)["IMG_1.JPG"].rel_path == "IMG_1.JPG"
        assert repo.plan_status(conn, plan_id) == "partially_applied"
    finally:
        conn.close()


def test_a_split_unit_with_no_intent_row_is_healed(tmp_path, set_fault):
    """The scenario that proves phase 2 is not a side effect of phase 1.

    Under `mid_companion_unit` the split exists only because phase 1 turned an `intent`
    row into a `failed` one, so a consistency pass wrongly written as a follow-up
    INSIDE the intent loop would still pass that test. Here phase 1 has literally
    nothing to iterate and phase 2 must do the whole repair by itself.
    """
    root, plan_id = crashed_mid_revert(tmp_path, set_fault)

    summary = recover_now(root)

    assert summary.reverted == ["IMG_1.JPG"]
    assert summary.failed == []  # phase 1 had nothing to do
    assert archive_names(root) == ["2019-07-04_001.MOV", "IMG_1.JPG", "IMG_1.MOV"]
    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "done") == []
        assert journal_pairs(conn, "reverted") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
        assert repo.all_files(conn)["IMG_1.JPG"].rel_path == "IMG_1.JPG"
        assert repo.plan_status(conn, plan_id) == "partially_applied"
    finally:
        conn.close()


def test_phase_two_never_touches_a_unit_whose_members_all_succeeded(tmp_path, set_fault):
    """`crash:before_plan_finalize:1` - the crash-AFTER-success case.

    Every rename worked, every journal row is `done`, only the plan's terminal status
    was never written. Recovery must conclude there is nothing to undo. This is the
    opposite assertion from every other scenario here: the others catch work left
    unfinished, this one catches a pass that mistakes FINISHED work for unfinished and
    helpfully undoes renames the user already has - which is worse than missing a crash.
    """
    root, plan_id = companion_archive(tmp_path, ("IMG_1.JPG", "IMG_1.MOV"))
    set_fault.arm("crash:before_plan_finalize:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    assert set_fault.hits("before_plan_finalize") == 1
    set_fault.next_invocation()

    summary = recover_now(root)

    assert (summary.reverted, summary.stranded, summary.failed) == ([], [], [])
    assert summary.finalized == [(plan_id, "applied")]
    assert archive_names(root) == ["2019-07-04_001.JPG", "2019-07-04_001.MOV"]
    conn = catalog(root)
    try:
        assert journal_pairs(conn, "done") == [
            ("IMG_1.JPG", "2019-07-04_001.JPG"),
            ("IMG_1.MOV", "2019-07-04_001.MOV"),
        ]
        assert repo.rows_in_state(conn, "reverted") == []
        assert set(item_statuses(conn, plan_id).values()) == {"applied"}
    finally:
        conn.close()


def test_a_done_member_changed_on_disk_is_left_exactly_where_it_is(
    tmp_path, set_fault, allow_split_unit
):
    """spec 7.2 phase 2 verifies against the JOURNAL's (size, mtime_ns) snapshot.

    Something edited the file after the crash. Putting it back would move an edited
    file on top of a name pam has not looked at since - so the member is left where it
    is, left `done` (J4: pam DID perform that rename), and named for a human. The unit
    stays split, which is why the tripwire is sanctioned: pam did not produce this, the
    outside world did, and it is reported on every invocation until a human acts.
    """
    root, _plan_id = crashed_mid_revert(tmp_path, set_fault)
    (root / "2019-07-04_001.JPG").write_bytes(b"edited by something else, longer now")

    summary = recover_now(root)

    assert summary.stranded == ["2019-07-04_001.JPG"]
    assert summary.reverted == []
    assert archive_names(root) == [
        "2019-07-04_001.JPG",
        "2019-07-04_001.MOV",
        "IMG_1.MOV",
    ]
    conn = catalog(root)
    try:
        assert journal_pairs(conn, "done") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
        assert repo.rows_in_state(conn, "reverted") == []
    finally:
        conn.close()


def test_a_stranded_member_is_reported_identically_on_the_first_and_tenth_run(
    tmp_path, set_fault, allow_split_unit
):
    """spec 7.0 J7 where it is hardest: a repair that CANNOT succeed must be inert.

    Something outside pam is sitting on the old name. Ten passes must leave exactly the
    state one pass left - no retry loop, no second attempt inside an invocation, and no
    row nudged towards a state the disk does not justify.
    """
    root, plan_id = crashed_mid_revert(tmp_path, set_fault)
    (root / "IMG_1.JPG").write_bytes(b"something else entirely")

    first = recover_now(root)
    after_first = snapshot(root, plan_id)
    for _ in range(9):
        last = recover_now(root)

    assert first.stranded == ["2019-07-04_001.JPG"]
    assert last.stranded == first.stranded
    assert last.reverted == []
    assert snapshot(root, plan_id) == after_first


def test_a_stranded_member_heals_on_the_next_run_once_the_way_is_clear(tmp_path, set_fault):
    """The repetition is the point: the user removes the interloping file, runs any pam
    command, and the group is put back with no special action. The run after that is a
    no-op - the pass neither retries forever nor gives up permanently."""
    root, plan_id = crashed_mid_revert(tmp_path, set_fault)
    (root / "IMG_1.JPG").write_bytes(b"something else entirely")
    assert recover_now(root).stranded == ["2019-07-04_001.JPG"]

    (root / "IMG_1.JPG").unlink()
    healed = recover_now(root)
    after = snapshot(root, plan_id)
    nothing_left = recover_now(root)

    assert healed.reverted == ["IMG_1.JPG"]
    assert healed.stranded == []
    assert not nothing_left.did_anything
    assert snapshot(root, plan_id) == after
    assert archive_names(root) == ["2019-07-04_001.MOV", "IMG_1.JPG", "IMG_1.MOV"]


# --- spec 7.0 J11: a resolution is never blocked by the catalog ---------------------


def ghost_row(root, rel_path):
    """A `files` row owning a path with no file behind it - what `pam scan` leaves
    when a photo is deleted outside pam, and what the stranded-member message asks the
    user to create when it tells them to run `pam scan`."""
    conn = catalog(root)
    try:
        file_id = repo.upsert_file(
            conn,
            rel_path=rel_path,
            filename=rel_path.rpartition("/")[2],
            ext="jpg",
            media_type="image",
            size=1,
            mtime_ns=1,
            placeholder=False,
            now="2026-08-06T10:00:00",
        )
        repo.mark_missing(conn, [file_id])
        conn.commit()
    finally:
        conn.close()


def test_the_revert_refuses_when_another_catalog_row_owns_the_old_path(
    tmp_path, set_fault, allow_split_unit
):
    """J11, reproduced defect 1: the shared revert loop moved a file and then rolled
    back its own resolution.

    `files.rel_path` is UNIQUE and `files` rows are never deleted, and pam's own
    stranded message tells the user to run `pam scan` - which creates a row at the old
    path. Without the precondition the rename SUCCEEDS, `set_file_path` raises
    IntegrityError, the whole transaction rolls back, and an exception escapes a pass
    that runs at the start of every command. So the file is not moved at all: the
    member is reported, and nothing is left half-done.
    """
    root, _plan_id = crashed_mid_revert(tmp_path, set_fault)
    ghost_row(root, "IMG_1.JPG")

    summary = recover_now(root)

    assert summary.stranded == ["2019-07-04_001.JPG"]
    assert summary.discrepancies and "IMG_1.JPG" in summary.discrepancies[0]
    # The file did NOT move, and its row still says where it really is.
    assert archive_names(root) == [
        "2019-07-04_001.JPG",
        "2019-07-04_001.MOV",
        "IMG_1.MOV",
    ]
    conn = catalog(root)
    try:
        assert journal_pairs(conn, "done") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
        assert "2019-07-04_001.JPG" in repo.all_files(conn)
    finally:
        conn.close()


def test_the_adoption_resolves_even_when_another_catalog_row_owns_the_new_path(
    tmp_path, set_fault
):
    """J11, reproduced defect 2: a scan that ran before recovery.

    The scan recorded the renamed file as NEW, so a second `files` row already owns the
    path the adoption wants to write. The journal row must still reach `done` - the
    rename really did happen, and `intent` rows that can never resolve break J7 on a
    pass that runs at the start of every invocation. The catalog is a cache the next
    scan rebuilds; the journal is the record of what pam did.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    set_fault.arm("crash:after_rename:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()
    ghost_row(root, "2019-07-04_001.JPG")

    summary = recover_now(root)

    assert summary.adopted == ["2019-07-04_001.JPG"]
    assert summary.discrepancies and "2019-07-04_001.JPG" in summary.discrepancies[0]
    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "intent") == []
        assert journal_pairs(conn, "done") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
        assert item_statuses(conn, plan_id) == {"IMG_1.JPG": "applied"}
    finally:
        conn.close()


# --- spec 7.0 J7: convergence -------------------------------------------------------


@pytest.mark.parametrize(
    "checkpoint",
    [
        "before_intent_commit",
        "after_intent",
        "after_rename",
        "before_done_commit",
        "mid_companion_unit",
        "before_plan_finalize",
    ],
)
def test_ten_recovery_passes_leave_what_one_pass_left(tmp_path, set_fault, checkpoint):
    """spec 7.0 J7. Idempotence implies convergence mathematically, which is exactly
    why the test is worth writing: what it catches is an implementation that is not
    actually idempotent on ONE of its branches. `repo.resolve` is deliberately not
    idempotent, so a pass that re-resolved instead of re-querying would raise here."""
    root, plan_id = companion_archive(tmp_path, ("IMG_1.JPG", "IMG_1.MOV"))
    set_fault.arm(f"crash:{checkpoint}:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    assert set_fault.hits(checkpoint) == 1
    set_fault.next_invocation()

    recover_now(root)
    after_first = snapshot(root, plan_id)
    for _ in range(9):
        recover_now(root)

    assert snapshot(root, plan_id) == after_first
    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "intent") == []
    finally:
        conn.close()


def test_ten_recovery_passes_converge_after_a_crash_inside_the_repair(tmp_path, set_fault):
    """The same property for the split with no `intent` row - J7 at its hardest,
    because the only thing that can change the state is phase 2 itself."""
    root, plan_id = crashed_mid_revert(tmp_path, set_fault)

    recover_now(root)
    after_first = snapshot(root, plan_id)
    for _ in range(9):
        recover_now(root)

    assert snapshot(root, plan_id) == after_first


# --- phase 0 (J9) and phase 3 (J10) ------------------------------------------------


def test_recovery_against_an_unreachable_archive_changes_nothing(tmp_path, set_fault):
    """spec 7.0 J9. `intent -> failed` is terminal, so a decision made about a disk pam
    cannot see can never be taken back - and every `intent` row would be resolved
    `failed` for renames that may well have completed on that drive. The archive is
    reported and left exactly as it is."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    set_fault.arm("crash:after_intent:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()
    before = snapshot(root, plan_id)

    conn = catalog(root)
    try:
        summary = recover(conn, root.parent / "not-a-folder", LOGGER)
    finally:
        conn.close()

    assert summary.archive_unreachable
    assert summary.did_anything  # it must SAY so, not pass silently
    assert (summary.failed, summary.adopted, summary.finalized) == ([], [], [])
    assert snapshot(root, plan_id) == before


def test_recovery_finalizes_a_plan_left_applying_with_nothing_attempted(tmp_path, set_fault):
    """`partially_applied` covers "none of them", deliberately (spec 7.2 phase 3).

    Zero renames happened, so calling it `applied` would claim work that never
    occurred. M6's `pam status` must render such a plan by its counts, never by the
    status word.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    set_fault.arm("crash:before_intent_commit:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()

    summary = recover_now(root)

    assert summary.finalized == [(plan_id, "partially_applied")]
    conn = catalog(root)
    try:
        assert repo.plan_status(conn, plan_id) == "partially_applied"
        assert set(item_statuses(conn, plan_id).values()) == {"approved"}
    finally:
        conn.close()


@pytest.mark.parametrize("status", ["open", "applied", "partially_applied", "superseded"])
def test_recovery_changes_the_status_of_no_plan_that_is_not_applying(tmp_path, status):
    """spec 7.0 J10's second half, and the one that matters most: `applying` names a
    run in progress and recovery holds the workspace lock, so a plan it finds there
    belongs to a dead process. Every OTHER status is somebody else's business - and a
    recovery that finalized an `open` plan would silently cancel work the user is
    still choosing about."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    conn = catalog(root)
    try:
        repo.set_plan_status(conn, plan_id, status)
        conn.commit()
    finally:
        conn.close()

    summary = recover_now(root)

    assert summary.finalized == []
    conn = catalog(root)
    try:
        assert repo.plan_status(conn, plan_id) == status
    finally:
        conn.close()


def item(status):
    """A plan_items row carrying nothing but the status the derivation reads."""
    return repo.PlanItemRow(
        id=1,
        file_id=1,
        old_rel_path="IMG_1.JPG",
        unit_key="u",
        generated_filename="x.JPG",
        new_filename="x.JPG",
        tokens_json="[]",
        tier="high",
        trust_score=100,
        trust_reasons_json="[]",
        status=status,
    )


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], "applied"),
        (["applied"], "applied"),
        (["applied", "applied"], "applied"),
        (["applied", "skipped"], "applied"),
        (["undone"], "applied"),
        (["failed"], "partially_applied"),
        (["applied", "failed"], "partially_applied"),
        (["approved"], "partially_applied"),
        (["proposed"], "partially_applied"),
        (["applied", "approved"], "partially_applied"),
    ],
)
def test_the_terminal_status_derivation(statuses, expected):
    """spec 7.2 phase 3's rule, driven directly - the ONE place apply and recovery
    both read, so that a plan closed by a run and a plan closed by a recovery pass can
    never disagree about what `applied` means. Driven here rather than only through
    scenarios because the two callers agree on every state M5 can currently reach, so
    an end-to-end test cannot tell the derivation from apply's old counter."""
    assert derive_plan_status([item(s) for s in statuses]) == expected


# --- wiring: recovery runs inside the lock, before the command's own work ----------


def test_a_scan_after_a_crash_recovers_before_it_walks(tmp_path, set_fault):
    """The ordering finding, reproduced as a regression test.

    A scan that walked FIRST would see the renamed file as new and the original as
    missing, write a second `files` row, and recovery's adoption would then collide
    with the row the scan just made - a discrepancy pam manufactured for itself. One
    row, at the new path, present, is what proves recovery ran before the walk.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    set_fault.arm("crash:after_rename:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()

    run_scan(root, exiftool_command=FAKE_COMMAND)

    conn = catalog(root)
    try:
        photos = {
            path: row.status
            for path, row in repo.all_files(conn).items()
            if path.endswith(".JPG")
        }
        assert photos == {"2019-07-04_001.JPG": "present"}
        assert journal_pairs(conn, "done") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
        assert repo.rows_in_state(conn, "intent") == []
    finally:
        conn.close()


def test_a_plan_run_after_a_crash_recovers_first(tmp_path, set_fault):
    """Every archive-scoped command runs recovery, not only `pam apply`. A plan built
    over an uncorrected catalog would describe files by names they no longer have."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    set_fault.arm("crash:after_rename:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()

    run_plan(root)

    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "intent") == []
        assert journal_pairs(conn, "done") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
        # Its one item was adopted, so the plan really did finish - from the item
        # statuses alone, which is all recovery has to go on.
        assert repo.plan_status(conn, plan_id) == "applied"
    finally:
        conn.close()


def test_an_apply_recovers_before_it_validates(tmp_path, set_fault):
    """Recovery runs before the preflight, so a plan left `applying` is given its
    terminal status before apply refuses it for being in one. Otherwise the refusal
    names a state no process is any longer in, and the plan is never finalized at all
    because nothing else ever reaches it."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    set_fault.arm("crash:after_rename:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()

    with pytest.raises(PamError) as refusal:
        run_apply(plan_id, root)

    assert "already been applied in part" in str(refusal.value)
    conn = catalog(root)
    try:
        assert repo.plan_status(conn, plan_id) == "partially_applied"
        assert repo.rows_in_state(conn, "intent") == []
    finally:
        conn.close()


def test_recovery_is_wired_into_exactly_the_archive_scoped_commands():
    """spec 7.2 "at the start of every invocation", pinned structurally.

    `cli.main` cannot host it generically - it has no archive at that point, and spec
    3's resolution is what decides which one a command means. So each archive-scoped
    command calls it after resolving its archive, and `pam doctor`, which has no
    archive, runs none. A new archive-scoped command that forgets the call is a crash
    left unrecovered on every run of it, so the list is asserted rather than trusted.

    `verify.py` joined the set at M6 Task 3, and it is the interesting entry: `pam
    verify` is read-only, so the temptation is to let it skip recovery. It must not -
    a crash left by an interrupted apply would then be reported by every check as
    damage while the machinery that heals it sat one command away. It runs recovery
    through a WRITE connection inside the lock, closes it, and does its checking
    through a read-only one (spec 7.5, M6 plan clarification 4).

    M6 TASK 5 ADDED TWO COMMANDS AND NEITHER IS IN THIS SET, deliberately. `pam status`
    is not archive-scoped: with no `--archive` it reports on EVERY known archive at once
    (spec 3 rev 8 refuses to auto-select), including the unreachable ones, and spec 7.0
    J9 forbids running recovery against an archive pam cannot see - so the alternative
    is N recovery passes, some of them blind, from a command the user believes is a
    summary. It would also need N workspace locks and so could not answer at all while
    an apply was running. It reads every catalog `mode=ro` instead and reports what
    recovery WOULD heal rather than healing it; `test_status_runs_no_recovery_and_
    writes_nothing` pins that from the other side. `pam forget` operates on PAM_HOME
    rather than on an archive, and its answer to an unresolved journal is to REFUSE,
    naming the rows - a command that repaired the thing it is about to stop pointing at
    would be doing the repair behind the user's back.

    It looks for the CALL, not the import. The first version of this guard read
    `ImportFrom` nodes, and M5 Task 9's mutation pass found the hole by predicting a
    failure that did not happen: deleting the call while leaving the import in place
    passed it. An import is not a wiring.

    M7 TASK 7 ADDS `pam review`, which is archive-scoped like the rest: it resolves an
    archive, takes the workspace lock and holds it for the server's whole lifetime, and
    runs recovery before it serves anything. A review page built over an uncorrected
    catalog would show the user names for files that are already sitting under different
    ones - and the decisions taken on that page are what `pam apply` then acts on.

    THE PATHS ARE PACKAGE-RELATIVE, not bare filenames. `verify.py` became the package
    `pam/verify/` at M6 Task 4, so its entry is `verify/__init__.py`; and a bare name
    was always the weaker assertion, because two modules in different subpackages can
    share one - `runner.py` would have matched a `pam/execute/runner.py` that never
    existed. The set below says where each call is, not merely what its file is called.
    """
    import ast
    from pathlib import Path

    import pam

    pam_dir = Path(pam.__file__).parent
    callers = set()
    for path in sorted(pam_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            called = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if called == "run_recovery":
                callers.add(path.relative_to(pam_dir).as_posix())
    # The guard's OWN floor (M14 Task 4, T4.9): a guard pointed at the wrong name finds no call
    # at all, and an empty set must never be mistaken for "nothing is archive-scoped".
    assert callers, "the guard found no call to run_recovery: is it looking for the right name?"
    assert callers == {
        "scan/runner.py",
        "naming/planner.py",
        "execute/apply.py",
        "execute/undo.py",
        "verify/__init__.py",
        "webui/__init__.py",
        # M9 TASK 12 ADDS `pam enrich`, and the guard fired on it exactly as designed -
        # it was written to make a new archive-scoped command's missing call a red
        # suite, and this is the first time that has happened. It belongs here for the
        # `pam review` reason rather than the `pam plan` one: enrichment is keyed by
        # CONTENT hash, so a half-finished rename cannot corrupt a fact - but the file
        # rows the runner walks, and the `rel_path` it opens to decode a pixel, are
        # exactly what an unrecovered crash leaves pointing at the wrong name. It
        # resolves an archive, takes the workspace lock, and recovers inside it before
        # the first `wants()` is asked.
        "enrich/command.py",
        # M11 TASK 9 ADDS `pam faces cluster`, and the guard fired on it too - which is
        # twice now that a new archive-scoped command has been caught by this list rather
        # than by a reviewer. Clustering is the weakest case in the set and belongs
        # anyway: it is keyed by CONTENT hash, opens no photograph and reads no
        # `rel_path`, so a half-finished rename cannot change a single one of its answers.
        # What puts it here is the `pam verify` argument rather than the `pam plan` one -
        # it takes the workspace lock and holds it for the whole pass, so a command that
        # skipped recovery would leave a crash unhealed while occupying the one lock the
        # machinery that heals it needs, and hand the user a clean report on the way out.
        "faces/__init__.py",
        # M11 TASK 13 ADDS `pam faces export` AND `pam faces purge`, and they belong here
        # for the same reason `pam faces cluster` does rather than for `pam plan`'s. Both
        # are keyed by CONTENT: an export reads `face_instances` and a purge deletes it,
        # and a half-finished rename can change neither. What puts them in the set is the
        # LOCK - each holds the workspace lock for its whole run, so a command that
        # skipped recovery would occupy the one lock the healing machinery needs while
        # leaving a crash unhealed. The export has a second reason of its own: it
        # publishes `rel_paths` out of `files`, which is exactly the column an unrecovered
        # crash leaves pointing at names that are not on disk, into a document that
        # outlives the catalog it was read from.
        "faces/export.py",
        "faces/purge.py",
        # M14 TASK 4 ADDS THE PAGE'S BIND (`pam start`, U10): choosing a scanned folder takes its
        # workspace lock and holds it for the server's life, exactly as `pam review` does, so it
        # recovers under that lock before the page reads anything - for `pam review`'s reason.
        "webui/launcher.py",
        # M14 TASK 5 ADDS A JOB THAT DIED (U11): a job thread that ends by an exception (a real
        # failure, or a `PAM_FAULT` `crash:` checkpoint) leaves its crash in a server that still
        # holds the folder's lock, so the server JOINS the thread and recovers under that lock
        # before the next request writes (`webui/jobs.py::JobRegistry.heal`). J10 is why the join
        # comes first: a plan in `applying` belongs to a gone process only once its thread is joined.
        "webui/jobs.py",
        # NOT HERE, ON PURPOSE - the exemption side, beside `pam status` (see the docstring):
        # M13 TASK 14's `pam faces calibrate` (`faces/calibrate.py`) is a LOCK-FREE READER. It
        # opens the catalog `mode=ro`, makes its reads inside one snapshot (S13), takes no
        # workspace lock and writes nothing - so it cannot heal a crash, and it holds no lock
        # the healing machinery needs while it runs. Its table is keyed by content (face
        # embeddings); `--exclude-path` matches `rel_paths` a crash may leave stale, which the
        # next archive-scoped command heals. Adding the call would need the lock it must not
        # take.
    }


def test_recovery_never_moves_a_file_or_resolves_a_journal_row_itself():
    """spec 7.1 rev 7: `rename.py` owns the single verified move and `journal_ops.py`
    owns the single journal state machine. Recovery is the module most tempted to
    reach past both - it already knows the paths it wants and the state it wants next
    - and a second implementation of either here is how apply, undo and recovery drift
    into three answers to "check, then move an irreplaceable file"."""
    from pathlib import Path

    from guards import move_call_sites, resolve_call_sites

    from pam.execute import recover as recover_module

    source = Path(recover_module.__file__).read_text(encoding="utf-8")
    assert move_call_sites(source) == []
    assert resolve_call_sites(source) == []


# --- reporting ----------------------------------------------------------------------


def test_a_recovery_that_did_nothing_prints_nothing(tmp_path, capsys):
    """It runs before every command, so anything it prints when there was nothing to
    do is noise on every invocation forever."""
    root, _plan_id = planned(tmp_path, ["IMG_1.JPG"])
    capsys.readouterr()

    conn = catalog(root)
    try:
        run_recovery(conn, root, LOGGER)
    finally:
        conn.close()

    assert capsys.readouterr().err == ""


def test_a_recovery_that_did_something_says_so_on_stderr(tmp_path, set_fault, capsys):
    """spec 7.2's reporting rule: one clear line, and the detail in the session log."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    set_fault.arm("crash:after_rename:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()
    capsys.readouterr()

    conn = catalog(root)
    try:
        run_recovery(conn, root, LOGGER)
    finally:
        conn.close()

    printed = capsys.readouterr().err
    assert "interrupted run" in printed
    assert "1 rename(s) kept" in printed
    # Nothing needs a human here, so there is nothing durable to point at and the line
    # stays one line. The pointer below is for the case that does.
    assert "Details anytime" not in printed


def test_the_line_about_a_file_needing_attention_names_where_to_look_later(
    tmp_path, set_fault, capsys
):
    """spec 7.0: a state pam cannot produce is reported for human attention and acted on
    never - but until M7 Task 4 it was reported ONCE, on stderr, and then only lived in
    the session log. The user who reads the line at 11pm and comes back on Saturday had
    nowhere to ask again.

    The command in the pointer is EXTRACTED from what pam printed and driven through the
    CLI, not asserted as a substring: a substring match passes for a pointer at a command
    that was renamed, which is the failure mode (M4 finding 12).
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    set_fault.arm("crash:after_rename:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()
    # Something outside pam puts a file back under the old name: spec 7.2's third branch.
    (root / "IMG_1.JPG").write_bytes(b"something else entirely")
    capsys.readouterr()

    conn = catalog(root)
    try:
        summary = run_recovery(conn, root, LOGGER)
    finally:
        conn.close()

    assert summary.needs_attention == ["IMG_1.JPG"]
    printed = capsys.readouterr().err
    pointers = [
        line.split("Details anytime:", 1)[1].strip()
        for line in printed.splitlines()
        if "Details anytime:" in line
    ]
    assert len(pointers) == 1, "one line per session, and this crash touched one session"

    code = main([*pointers[0].split()[1:], "--archive", str(root)])
    captured = capsys.readouterr()

    assert code == EXIT_OK, "the command recovery names does not resolve"
    assert "IMG_1.JPG" in captured.out, "it does not answer the question it was offered for"


def test_the_other_needs_attention_branch_points_at_the_report_too(tmp_path, set_fault, capsys):
    """`_needs_attention` has TWO call sites, and the pointer must come from both.

    The corrupt-plan-item branch is unreachable through pam - `journal.plan_item_id` is
    a foreign key and nothing deletes a `plan_items` row - so it had no test at all, and
    reverting it to the pre-task `summary.needs_attention.append(...)` left the whole
    suite green (M580, reviewer's finding, closed here). That mutation makes
    `_needs_attention`'s own promise false: a file named for a human, with no `Details
    anytime:` line and no session in `attention_sessions` to produce one.

    The corruption is made with the suite's own SQL, which is the only way in. Everything
    before it is real: a real plan, a real apply, a real crash after the intent row.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    set_fault.arm("crash:after_intent:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()
    capsys.readouterr()

    conn = catalog(root)
    try:
        session_id = repo.sessions(conn)[0].session_id
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM plan_items")
        conn.commit()
        summary = run_recovery(conn, root, LOGGER)
    finally:
        conn.close()

    assert summary.needs_attention == ["IMG_1.JPG"]
    assert summary.attention_sessions == [session_id], (
        "a file named for a human with nowhere to look it up again"
    )
    printed = capsys.readouterr().err
    assert f"Details anytime: pam report session {session_id}" in printed


def test_two_files_needing_attention_in_one_session_get_ONE_pointer(
    tmp_path, set_fault, capsys
):
    """One line per SESSION, not one per file - the promise the dedupe in
    `_needs_attention` makes, and which no test could tell apart from "one per file"
    because every other case in the suite has exactly one attention file (M581,
    reviewer's finding, closed here).

    A companion unit is the shortest route to two unresolved rows in one session: spec
    7.3 writes both members' intents and commits them together, so a crash straight
    after that commit leaves the pair in `intent`.
    """
    root, plan_id = companion_archive(tmp_path)
    set_fault.arm("crash:after_intent:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()
    capsys.readouterr()

    conn = catalog(root)
    try:
        session_id = repo.sessions(conn)[0].session_id
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM plan_items")
        conn.commit()
        summary = run_recovery(conn, root, LOGGER)
    finally:
        conn.close()

    assert len(summary.needs_attention) == 2, "both members of the unit must be unresolved"
    assert summary.attention_sessions == [session_id], "the session is named once, not per file"
    printed = capsys.readouterr().err
    assert printed.count("Details anytime:") == 1


# --- the J6 tripwire and the production query it does NOT share ---------------------


def test_the_tripwire_and_the_production_split_unit_query_agree(tmp_path, set_fault):
    """`conftest._split_units` is deliberately a SECOND implementation of
    `repo.split_units`, not a caller of it: a tripwire that calls the code it guards
    cannot catch that code being wrong, and this one carries four of Task 7's mutation
    kills. Two implementations only help while they agree, so that is asserted here -
    over a real split unit and again over the healed state, so neither "both say
    everything is split" nor "both say nothing is" can pass it."""
    from conftest import _split_units

    from pam.paths import workspace_if_exists

    root, _plan_id = crashed_mid_revert(tmp_path, set_fault)
    ws = workspace_if_exists(root)
    assert ws is not None

    conn = catalog(root)
    try:
        production = repo.split_units(conn)
    finally:
        conn.close()
    assert production == _split_units(ws.db_path)
    assert len(production) == 1

    recover_now(root)  # heals it

    conn = catalog(root)
    try:
        assert repo.split_units(conn) == _split_units(ws.db_path) == []
    finally:
        conn.close()


def test_the_adoption_refuses_a_catalog_row_differing_only_in_case(tmp_path, set_fault):
    """The half of J11 an IntegrityError can never catch.

    `files.rel_path`'s UNIQUE index is BINARY, so a row at `2019-07-04_001.jpg` does
    not collide with `2019-07-04_001.JPG` at all: the write SUCCEEDS and leaves two
    rows whose paths differ only in case, on a filesystem where both names cannot
    exist. spec 7.5's check 6 calls that catalog corruption on Windows. Asking first,
    case-insensitively, is what stops it - so the check is not redundant with the
    exception guard behind it, and this is the test that says which layer did the work.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    set_fault.arm("crash:after_rename:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()
    ghost_row(root, "2019-07-04_001.jpg")

    summary = recover_now(root)

    assert summary.adopted == ["2019-07-04_001.JPG"]
    assert summary.discrepancies
    conn = catalog(root)
    try:
        assert journal_pairs(conn, "done") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
        paths = sorted(p for p in repo.all_files(conn) if p.lower().endswith(".jpg"))
        assert paths == ["2019-07-04_001.jpg", "IMG_1.JPG"]
    finally:
        conn.close()


def test_the_convergence_snapshot_notices_a_change_in_the_journal_alone(tmp_path, set_fault):
    """`snapshot()` is what every J7 test compares, so a snapshot that quietly stopped
    reading the journal would leave those tests passing while asserting far less than
    they claim. A helper cannot pin its own reach - this does.

    The journal row is moved BY HAND rather than by running recovery, and that is the
    whole point: a recovery pass also changes the item status and the plan status, so a
    snapshot blind to the journal would still see a difference and this test would pass
    for the wrong reason. It did, on the first attempt - deleting the journal dict left
    all 818 tests green. The one dimension under test has to be the only one that moves.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    set_fault.arm("crash:after_intent:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()

    before = snapshot(root, plan_id)
    conn = catalog(root)
    try:
        row = repo.rows_in_state(conn, "intent")[0]
        repo.resolve(conn, row.id, "failed", now="2026-08-06T12:00:00")
        conn.commit()
    finally:
        conn.close()

    assert snapshot(root, plan_id) != before


def test_a_unit_with_two_done_members_goes_back_newest_first(tmp_path, set_fault, caplog):
    """More than one member to put back - the case a single-member split cannot cover.

    Three members, the crash between members 2 and 3, so phase 1 fails member 3 and
    phase 2 has TWO `done` members to revert. Reverse order is asserted from the log
    the way the apply-side test does it: journal ids are insertion order and read the
    same either way round, so only the order the filesystem was ASKED to move things in
    can distinguish them. The archive walks backwards through exactly the states it
    walked forwards through, and no member is ever briefly absent from both names.
    """
    root, plan_id = companion_archive(tmp_path, ("IMG_1.JPG", "IMG_1.MOV", "IMG_1.XMP"))
    set_fault.arm("crash:mid_companion_unit:2")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    assert set_fault.hits("mid_companion_unit") == 2
    set_fault.next_invocation()
    assert archive_names(root) == ["2019-07-04_001.JPG", "2019-07-04_001.MOV", "IMG_1.XMP"]

    with caplog.at_level(logging.INFO, logger=LOGGER.name):
        summary = recover_now(root)

    assert summary.reverted == ["IMG_1.MOV", "IMG_1.JPG"]
    assert archive_names(root) == ["IMG_1.JPG", "IMG_1.MOV", "IMG_1.XMP"]
    renamed_back = [
        message for message in caplog.messages if message.startswith("renamed: 2019-07-04_001.")
    ]
    assert renamed_back == [
        "renamed: 2019-07-04_001.MOV -> IMG_1.MOV",
        "renamed: 2019-07-04_001.JPG -> IMG_1.JPG",
    ]
    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "done") == []
        assert journal_pairs(conn, "reverted") == [
            ("IMG_1.JPG", "2019-07-04_001.JPG"),
            ("IMG_1.MOV", "2019-07-04_001.MOV"),
        ]
    finally:
        conn.close()


def test_the_convergence_snapshot_notices_a_change_in_the_files_table_alone(tmp_path):
    """The other dimension of the same helper, pinned the same way and for the same
    reason. J11's guard deliberately leaves a `files` row stale, so a later pass that
    "helpfully" wrote it would be non-idempotent in exactly the place the convergence
    tests are meant to be watching - and a snapshot blind to `files` would not see it."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])

    before = snapshot(root, plan_id)
    conn = catalog(root)
    try:
        repo.mark_missing(conn, [repo.all_files(conn)["IMG_1.JPG"].id])
        conn.commit()
    finally:
        conn.close()

    assert snapshot(root, plan_id) != before
