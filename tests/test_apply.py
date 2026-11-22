"""`pam apply`'s orchestration (spec 7.1 rev 7): the loop that renames real photos.

Every test here runs an ARCHIVE through the whole chain - scan, plan, apply - on a
real temp filesystem with the fake exiftool, and asserts against the files that are
actually on disk afterwards. Nothing is mocked. What is under test is a pairing:
what the catalog says AND what the disk holds, and a mock can only confirm what the
test author already believed.

The invariants this task owns, and where they are pinned:

  J1 (write-ahead)  - `test_a_crash_before_the_intent_commit_moves_no_file` and
                      `test_a_crash_right_after_the_intent_finds_every_file_unmoved`.
                      Both fail if apply ever renames before `begin()` commits.
  J2 (append-only)  - `test_no_journal_row_is_ever_deleted_or_walked_backwards`.
  J5 (per item)     - `test_a_crash_between_items_leaves_resolved_items_true_to_disk`.
  J8 (never overwrite) - `test_an_occupied_target_fails_the_item_and_spares_the_occupant`.
"""

import json
import sqlite3
import time
from pathlib import Path

import pytest
from archives import (
    DATES,
    FAKE_COMMAND,
    approve_and_apply,
    assert_verify_agrees,
    catalog,
    companion_archive,
    hashes,
    item_statuses,
    journal_pairs,
    planned,
)
from guards import move_call_sites, resolve_call_sites

from pam.catalog import repo
from pam.errors import PamError
from pam.execute.apply import run_apply
from pam.naming.planner import run_plan
from pam.scan.runner import run_scan


def image_names(root):
    return sorted(p.name for p in root.iterdir() if p.suffix == ".JPG")


# --- the happy path ---------------------------------------------------------------


def test_a_clean_apply_renames_every_file_and_completes_the_plan(tmp_path):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    before = hashes(root)

    summary = approve_and_apply(plan_id, root)

    assert (summary.renamed, summary.failed, summary.failures) == (2, 0, [])
    assert summary.had_errors is False
    assert summary.log_path is not None and summary.log_path.exists()
    # Task 10's reporter.py now writes the session report (plan clarification 13).
    # Until it did, this asserted `is None` - apply must not invent a path for a file
    # nothing has written, and flipping this line is the record that something does.
    assert summary.report_path is not None and summary.report_path.exists()
    assert image_names(root) == ["2019-07-04_001.JPG", "2020-08-05_001.JPG"]
    assert hashes(root) == before

    conn = catalog(root)
    try:
        assert repo.plan_status(conn, plan_id) == "applied"
        assert item_statuses(conn, plan_id) == {"IMG_1.JPG": "applied", "IMG_2.JPG": "applied"}
        assert journal_pairs(conn, "done") == [
            ("IMG_1.JPG", "2019-07-04_001.JPG"),
            ("IMG_2.JPG", "2020-08-05_001.JPG"),
        ]
        assert repo.rows_in_state(conn, "intent") == []
        assert set(repo.all_files(conn)) >= {"2019-07-04_001.JPG", "2020-08-05_001.JPG"}
        assert repo.all_files(conn)["2019-07-04_001.JPG"].filename == "2019-07-04_001.JPG"
    finally:
        conn.close()


def test_apply_renames_files_inside_subdirectories(tmp_path):
    """An archive of real files has folders. A human test's library does, and every
    other test here uses a FLAT one - so nothing proved apply handles a nested path
    at all. Breaking `validator.new_rel_path` to drop the directory left all 739
    tests green; this is the test that notices.

    The failure that mutation produces is not silent corruption - `rename.py`'s
    same-directory refusal catches it and every nested file fails - so the second
    layer is real. But "every nested file fails" is a broken product, and the suite
    had no way to say so.
    """
    root = tmp_path / "photos"
    (root / "2019" / "trip").mkdir(parents=True)
    for rel in ("2019/IMG_1.JPG", "2019/trip/IMG_2.JPG"):
        (root / rel).write_bytes(f"the only copy of {rel}".encode())
        (root / f"{rel}.tags.json").write_text(
            json.dumps({"DateTimeOriginal": DATES[Path(rel).name]}), encoding="utf-8"
        )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    assert plan_id is not None
    before = hashes(root)

    summary = approve_and_apply(plan_id, root)

    assert (summary.renamed, summary.failed) == (2, 0)
    # Each file renamed WHERE IT LIVES - the directory came along, and nothing was
    # hoisted to the archive root.
    assert (root / "2019" / "2019-07-04_001.JPG").exists()
    assert (root / "2019" / "trip" / "2020-08-05_001.JPG").exists()
    assert hashes(root) == before

    conn = catalog(root)
    try:
        assert repo.plan_status(conn, plan_id) == "applied"
        # The catalog agrees with the disk, full relative path and all.
        assert set(repo.all_files(conn)) >= {
            "2019/2019-07-04_001.JPG",
            "2019/trip/2020-08-05_001.JPG",
        }
        assert journal_pairs(conn, "done") == [
            ("2019/IMG_1.JPG", "2019/2019-07-04_001.JPG"),
            ("2019/trip/IMG_2.JPG", "2019/trip/2020-08-05_001.JPG"),
        ]
    finally:
        conn.close()


def test_one_session_id_covers_the_whole_invocation(tmp_path):
    """spec 7.4's undo replays a session, so an apply that spread itself over two
    session ids would only be half undoable."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG", "IMG_3.JPG"])
    summary = approve_and_apply(plan_id, root)

    conn = catalog(root)
    try:
        rows = repo.rows_in_state(conn, "done")
        assert {row.session_id for row in rows} == {summary.session_id}
        assert len(rows) == 3
    finally:
        conn.close()
    # spec 9.3 / plan clarification 7: 's-YYYYMMDD-HHMMSS', seconds not minutes.
    assert len(summary.session_id) == len("s-20260731-103055")
    assert summary.session_id.startswith("s-")


def test_every_journal_transition_reaches_the_session_log(tmp_path):
    """spec 13: every journal transition is logged, in the log the session ends by
    naming."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    summary = approve_and_apply(plan_id, root)
    text = summary.log_path.read_text(encoding="utf-8")
    assert "journal intent" in text
    assert "journal done" in text
    assert summary.session_id in text
    assert "apply start" in text and "apply done" in text


# --- per-file failures: record, count, continue (vision principle 9) ---------------


def test_a_file_that_changed_since_the_scan_fails_and_the_run_continues(tmp_path):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    edited = b"edited outside pam after the scan, so the snapshot no longer matches"
    (root / "IMG_1.JPG").write_bytes(edited)

    summary = approve_and_apply(plan_id, root)

    assert (summary.renamed, summary.failed) == (1, 1)
    assert summary.failures[0][0] == "IMG_1.JPG"
    assert "changed on disk" in summary.failures[0][1]
    assert summary.had_errors is True
    # Untouched on disk: same name, same bytes as the user left them.
    assert (root / "IMG_1.JPG").read_bytes() == edited
    assert (root / "2020-08-05_001.JPG").exists()

    conn = catalog(root)
    try:
        assert repo.plan_status(conn, plan_id) == "partially_applied"
        assert item_statuses(conn, plan_id) == {"IMG_1.JPG": "failed", "IMG_2.JPG": "applied"}
        assert journal_pairs(conn, "failed") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
        assert journal_pairs(conn, "done") == [("IMG_2.JPG", "2020-08-05_001.JPG")]
        assert repo.all_files(conn)["IMG_1.JPG"].rel_path == "IMG_1.JPG"
    finally:
        conn.close()


def test_an_occupied_target_fails_the_item_and_spares_the_occupant(tmp_path):
    """spec 7.0 J8: pam never overwrites. The occupant arrived after the scan, so the
    catalog does not know about it and only the disk check can see it."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    (root / "2019-07-04_001.JPG").write_bytes(b"a different photo entirely")
    before = hashes(root)

    summary = approve_and_apply(plan_id, root)

    assert (summary.renamed, summary.failed) == (1, 1)
    assert "already there" in summary.failures[0][1]
    assert (root / "2019-07-04_001.JPG").read_bytes() == b"a different photo entirely"
    assert (root / "IMG_1.JPG").read_bytes() == b"the only copy of IMG_1.JPG"
    # Hash invariance across the whole run, failures included.
    assert hashes(root) == before

    conn = catalog(root)
    try:
        assert repo.plan_status(conn, plan_id) == "partially_applied"
        assert journal_pairs(conn, "failed") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
    finally:
        conn.close()


def test_a_ghost_files_row_fails_only_its_own_item(tmp_path):
    """Layer 2 of the workspace-bricking defect, end to end (see validator.py).

    The rename this refuses would SUCCEED on disk and then raise IntegrityError in
    `set_file_path`, leaving a journal row in `intent` that spec 7.2's recovery can
    never resolve - spec 7.0 J7 broken on a pass that runs at the start of every
    invocation. Refusing the item costs the user one file's rename; not refusing it
    costs them the workspace.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    conn = catalog(root)
    try:
        # A photo deleted outside pam: the row survives, holding its name forever.
        ghost = repo.upsert_file(
            conn,
            rel_path="2019-07-04_001.JPG",
            filename="2019-07-04_001.JPG",
            ext="jpg",
            media_type="image",
            size=1,
            mtime_ns=1,
            placeholder=False,
            now="2026-08-01T09:00:00",
        )
        repo.mark_missing(conn, [ghost])
        conn.commit()
    finally:
        conn.close()

    summary = approve_and_apply(plan_id, root)

    assert (summary.renamed, summary.failed) == (1, 1)
    assert summary.failures[0][0] == "IMG_1.JPG"
    assert "records still show a file named" in summary.failures[0][1]
    assert (root / "IMG_1.JPG").exists()
    assert (root / "2020-08-05_001.JPG").exists()

    conn = catalog(root)
    try:
        # No journal row for the refused item: spec 7.0 J1 makes a journal row a
        # promise that pam is about to move a file, and this one was never touched.
        assert [r.old_rel_path for r in repo.rows_in_state(conn, "failed")] == []
        assert journal_pairs(conn, "done") == [("IMG_2.JPG", "2020-08-05_001.JPG")]
        assert item_statuses(conn, plan_id)["IMG_1.JPG"] == "failed"
        assert repo.plan_status(conn, plan_id) == "partially_applied"
    finally:
        conn.close()


def test_an_item_the_catalog_no_longer_describes_is_left_alone(tmp_path):
    """The plan and the catalog must still agree about WHICH path the item is about.

    Reachable through spec 7.2's recovery, which rewrites `files.rel_path` for a
    rename it adopts. If the two disagree, the item's `(size, mtime_ns)` snapshot
    would be verified against a path the item was not written for - and if some other
    file happens to be sitting at that path with a matching size and mtime, the wrong
    file gets renamed. Cheap to refuse, unrecoverable to get wrong.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    conn = catalog(root)
    try:
        file_id = repo.all_files(conn)["IMG_1.JPG"].id
        repo.set_file_path(conn, file_id, "ADOPTED_BY_RECOVERY.JPG")
        conn.commit()
    finally:
        conn.close()

    summary = approve_and_apply(plan_id, root)

    assert (summary.renamed, summary.failed) == (1, 1)
    assert "somewhere else" in summary.failures[0][1]
    assert (root / "IMG_1.JPG").exists()
    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "failed") == []  # nothing was journaled
        assert item_statuses(conn, plan_id)["IMG_1.JPG"] == "failed"
    finally:
        conn.close()


# Companion-unit atomicity moved to tests/test_execute_unit.py with the code it
# tests (spec 7.1 rev 7's decomposition, M5 Task 7). What stays here is the run:
# the order of validate -> approve -> per unit -> finalize, and the totals.


# --- refusals at the plan level ---------------------------------------------------


def test_applying_the_same_plan_twice_is_refused(tmp_path):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    approve_and_apply(plan_id, root)
    with pytest.raises(PamError, match="already been applied"):
        run_apply(plan_id, root)


def test_a_refused_plan_leaves_every_item_proposed(tmp_path):
    """Validation comes FIRST. Writing anything before validating would leave a plan
    nobody can apply carrying state nobody chose. Until M13 apply approved every
    `proposed` item itself, so this was a consent record for a thing that never
    happened; since F1 (M13 Task 1) apply approves nothing except `--all`'s choices,
    written after the confirmation - and a refused plan still keeps every item as
    `pam plan` left it."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    conn = catalog(root)
    try:
        repo.set_plan_status(conn, plan_id, "superseded")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(PamError, match="superseded"):
        run_apply(plan_id, root)

    conn = catalog(root)
    try:
        assert set(item_statuses(conn, plan_id).values()) == {"proposed"}
        assert repo.rows_in_state(conn, "intent") == []
        assert repo.plan_status(conn, plan_id) == "superseded"
    finally:
        conn.close()
    assert image_names(root) == ["IMG_1.JPG", "IMG_2.JPG"]


def test_applying_a_folder_pam_has_not_scanned_is_refused(tmp_path):
    unknown = tmp_path / "never-scanned"
    unknown.mkdir()
    with pytest.raises(PamError, match="has not scanned"):
        run_apply(1, unknown)


# --- J1: no file moves before its intent is committed -----------------------------


def test_a_crash_before_the_intent_commit_moves_no_file(tmp_path, set_fault):
    """spec 7.0 J1 at its strictest: nothing in the journal, nothing moved on disk."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    set_fault.arm("crash:before_intent_commit:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    assert set_fault.hits("before_intent_commit") == 1
    set_fault.next_invocation()

    assert image_names(root) == ["IMG_1.JPG", "IMG_2.JPG"]
    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "intent") == []
        assert repo.rows_in_state(conn, "done") == []
    finally:
        conn.close()


def test_a_crash_right_after_the_intent_finds_every_file_unmoved(tmp_path, set_fault, capsys):
    """The other half of spec 7.0 J1. The intent is durable and NO rename has been
    attempted yet - which is exactly what fails if apply ever calls the rename before
    `begin()`: the file would already be sitting at its new name here.

    This also pins the SystemExit half of spec 13's unconditional log path. `run_apply`
    catches BaseException, not Exception, deliberately: a `crash:` checkpoint and a
    real Ctrl+C both raise outside the Exception hierarchy, and they are the two cases
    where the user most needs the session id to hand to `pam undo`. Narrowing the
    catch to `except Exception` otherwise breaks nothing - six crash tests existed and
    not one looked at stderr.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    set_fault.arm("crash:after_intent:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    assert set_fault.hits("after_intent") == 1
    set_fault.next_invocation()

    err = capsys.readouterr().err
    assert "Session:" in err and "Log:" in err

    assert image_names(root) == ["IMG_1.JPG", "IMG_2.JPG"]
    conn = catalog(root)
    try:
        assert journal_pairs(conn, "intent") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
    finally:
        conn.close()


# --- J5: one transaction per item -------------------------------------------------


def test_a_crash_between_items_leaves_resolved_items_true_to_disk(tmp_path, set_fault):
    """spec 7.0 J5. Three items, the crash landing just after the SECOND rename: the
    first item is fully resolved, and its catalog state must match the disk exactly.
    Batching the resolutions to the end of the run for speed is what this forbids -
    it would leave the first item's `files` row naming a path that no longer exists.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG", "IMG_3.JPG"])
    set_fault.arm("crash:after_rename:2")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    assert set_fault.hits("after_rename") == 2
    set_fault.next_invocation()

    conn = catalog(root)
    try:
        # Item 1: resolved. Journal, plan item, files row and disk all agree.
        assert journal_pairs(conn, "done") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
        assert item_statuses(conn, plan_id)["IMG_1.JPG"] == "applied"
        files = repo.all_files(conn)
        assert "2019-07-04_001.JPG" in files
        assert (root / "2019-07-04_001.JPG").exists()
        assert not (root / "IMG_1.JPG").exists()
        # J5 stated in general: for EVERY resolved item, the catalog and the disk
        # agree. (Not for every `files` row - item 2 is mid-window below, which is
        # spec 7.0 J3: an `intent` row tells you nothing about disk.)
        for row in repo.rows_in_state(conn, "done"):
            assert files[row.new_rel_path].id is not None
            assert (root / row.new_rel_path).exists()
            assert not (root / row.old_rel_path).exists()

        # Item 2: the window spec 7.2 adopts - renamed on disk, still `intent`.
        assert journal_pairs(conn, "intent") == [("IMG_2.JPG", "2020-08-05_001.JPG")]
        assert (root / "2020-08-05_001.JPG").exists()
        # Item 3: never reached, so it was never journaled.
        assert item_statuses(conn, plan_id)["IMG_3.JPG"] == "approved"
        assert (root / "IMG_3.JPG").exists()
    finally:
        conn.close()


def test_a_crash_at_the_first_rename_leaves_that_item_recoverable(tmp_path, set_fault):
    """The literal `crash:after_rename:1` case, stated honestly: the FIRST item is the
    interrupted one, so its catalog state is deliberately NOT yet true to disk - the
    file has moved and its row still names the old path. That is the state spec 7.2's
    recovery adopts (new exists, old absent -> mark `done`, fix the files row), and it
    is reachable only because the intent was committed first (spec 7.0 J1).
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG", "IMG_3.JPG"])
    set_fault.arm("crash:after_rename:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()

    assert (root / "2019-07-04_001.JPG").exists()
    conn = catalog(root)
    try:
        assert journal_pairs(conn, "intent") == [("IMG_1.JPG", "2019-07-04_001.JPG")]
        assert repo.rows_in_state(conn, "done") == []
        assert repo.all_files(conn)["IMG_1.JPG"].rel_path == "IMG_1.JPG"
    finally:
        conn.close()


def test_a_crash_before_the_plan_is_finalized_interrupts_no_work(tmp_path, set_fault):
    """spec 9.3 rev 7's `before_plan_finalize`, pinned to its position.

    This checkpoint is the odd one out: every other fault interrupts work in progress,
    and this one interrupts NOTHING. Every rename succeeded and every journal row is
    resolved; only the plan's terminal status is missing. It exists so spec 7.2's
    recovery can be shown to conclude "nothing to do" - a recovery pass that undoes
    completed work is worse than one that misses a crash. Moved even one line later,
    it would stop creating that state and the whole assertion would be vacuous.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    set_fault.arm("crash:before_plan_finalize:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    assert set_fault.hits("before_plan_finalize") == 1
    set_fault.next_invocation()

    assert image_names(root) == ["2019-07-04_001.JPG", "2020-08-05_001.JPG"]
    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "intent") == []
        assert len(repo.rows_in_state(conn, "done")) == 2
        assert set(item_statuses(conn, plan_id).values()) == {"applied"}
        assert repo.plan_status(conn, plan_id) == "applying"
    finally:
        conn.close()


# --- J2: the journal only ever gains information ----------------------------------


def test_no_journal_row_is_ever_deleted_or_walked_backwards(tmp_path):
    """spec 7.0 J2. One run with a success and a failure: both rows survive, in
    terminal states, and the count never goes down."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    (root / "2019-07-04_001.JPG").write_bytes(b"in the way")
    approve_and_apply(plan_id, root)

    conn = catalog(root)
    try:
        every = [
            (row.old_rel_path, row.state)
            for state in ("intent", "done", "failed", "reverted")
            for row in repo.rows_in_state(conn, state)
        ]
        assert sorted(every) == [("IMG_1.JPG", "failed"), ("IMG_2.JPG", "done")]
    finally:
        conn.close()


# --- spec 13: a session that ends badly still names its log and its session --------


def test_an_unexpected_failure_mid_run_still_names_the_session_and_the_log(
    tmp_path, set_fault, capsys
):
    """pam/cli.py's `except Exception` prints a traceback with no log path at all, so
    mid-apply on an archive of real files the user would be left with nothing to read and no
    session id to hand to `pam undo`. Task 10 owns the CLI, so apply is self-sufficient.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    set_fault.arm("dbfail:before_done_commit:1")
    with pytest.raises(sqlite3.OperationalError):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()

    err = capsys.readouterr().err
    session_line = next(line for line in err.splitlines() if line.startswith("Session: "))
    log_line = next(line for line in err.splitlines() if line.startswith("Log: "))
    session_id = session_line.removeprefix("Session: ").strip()
    log_path = Path(log_line.removeprefix("Log: ").strip())

    assert session_id.startswith("s-")
    assert log_path.exists()
    # Durable, not merely printed: a real power cut skips the `finally` that printed
    # those two lines, so the same facts have to be in the log and in the journal.
    assert session_id in log_path.read_text(encoding="utf-8")
    conn = catalog(root)
    try:
        assert {row.session_id for row in repo.rows_in_state(conn, "intent")} == {session_id}
    finally:
        conn.close()


# --- HT-M5's finding: what `in Ns` is a measurement OF -----------------------------


def test_the_time_the_user_spends_at_the_confirmation_is_not_reported_as_work(tmp_path):
    """HT-M5's one finding, and it was invisible to every existing test in this suite
    because all ~40 of them pass `confirm=None` and so never wait for anything.

    The real run printed `Done. N renamed, 0 failed, in 11s` for renaming the session
    log timed at 1.218 s; the other ~9.8 s was the owner reading UX 4's block. UX 4's
    closing line is normative copy and "in 11s" was not true of the renaming.

    The threshold is deliberately far below the wait: this asserts that the pause was
    EXCLUDED, not that apply is fast. A machine slow enough to spend 1 s renaming two
    files would fail this for the wrong reason, and 1.5 s of headroom over an
    already-excluded 2 s wait is the margin that keeps it honest without being flaky.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])

    def slow_confirm(_confirmation):
        time.sleep(2.0)
        return True

    summary = approve_and_apply(plan_id, root, confirm=slow_confirm)

    assert summary.renamed == 2, "the confirmation must have been accepted"
    assert summary.elapsed_s < 1.5


def test_a_cancelled_apply_still_reports_the_time_it_spent(tmp_path):
    """The clock is restarted only when the user says yes. A cancelled run did no work
    to measure, and restarting the clock on the way out of a `False` would report a
    duration for a session that never started - the same fiction pointing the other
    way. `elapsed_s` here covers the resolution, the recovery pass and the preflight,
    which is real work the user waited for before being asked anything.

    THE REFUSAL SLEEPS, and that sleep is the entire discriminator (M6 Task 2 review,
    mutation M282). `elapsed_s > 0.0` was the original assertion and it cannot see the
    defect it was written for: adding a `clock.restart()` just above the cancelled
    `return` still leaves `elapsed_s` positive, so the suite stayed green at 997 with
    the cancelled path restarting too - a test passing for exactly the wrong reason.
    A sleeping refusal puts a measurable amount of time on the near side of where that
    restart would sit, so the assertion can only hold if no restart happened.

    What this pins is `restart()`'s CALL SITE, not a rendered number: `pam/cli.py`
    prints "Cancelled. Nothing was renamed." and never reads `elapsed_s`, and no
    session report is written for a cancellation. That is why the sleep may be counted
    here without dishonesty - and why the property still has to be pinned, because the
    next command to grow a confirmation will copy whichever shape it finds.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])

    def slow_refusal(_confirmation):
        time.sleep(1.0)
        return False

    summary = approve_and_apply(plan_id, root, confirm=slow_refusal)

    assert summary.cancelled is True
    assert summary.elapsed_s >= 0.5


# --- spec 7.3 rev 15: a companion unit is DECIDED as a unit (vision S3) -----------
# The M8 architecture review built the violation end to end in about forty lines, which
# is what a criterion under a heading reading "any failure blocks release" cannot
# survive. These are that construction, kept.


def stems_on_disk(root):
    """The distinct stems really on disk, minus the fake exiftool's sidecars.

    STEMS, not counts and not statuses, because that is the only reading that can see
    the defect. `pam verify` reports a clean 12/12 over a split unit - check 4e reads
    the journal (nothing failed) and check 3 reads group MEMBERSHIP - so a test that
    asked either of them would have agreed that a split archive was healthy.
    """
    return sorted({p.stem for p in root.iterdir() if not p.name.endswith(".tags.json")})


def skip_item(root, plan_id, old_rel_path):
    """Decide one member of a unit out, the way spec 8.2's `skip` action does."""
    conn = catalog(root)
    try:
        for item in repo.plan_items(conn, plan_id):
            if item.old_rel_path == old_rel_path:
                repo.set_plan_item_status(conn, item.id, "skipped")
        conn.commit()
    finally:
        conn.close()


def test_apply_never_renames_a_proper_subset_of_a_companion_unit(tmp_path):
    """THE case: approve the photo, skip its video, apply. Nothing moves.

    Before spec 7.3 rev 15 this renamed the photo and left the video, so one unit
    became two stems on disk and `pam verify` still reported 12/12. The containment
    machinery already existed in the preflight - `COMPANION_REASON` and its
    `blocked_units` propagation - but it was computed over the proposed/approved
    subset, a set the skipped member had already left.

    Asserted on the stems, per the reproduction, and on the archive being byte-for-byte
    what it was: a refusal at preflight has moved nothing, so there is nothing to
    revert and no journal row to resolve (spec 7.0 J1).
    """
    root, plan_id = companion_archive(tmp_path)
    before = hashes(root)
    skip_item(root, plan_id, "IMG_1.MOV")

    summary = approve_and_apply(plan_id, root)

    assert stems_on_disk(root) == ["IMG_1"]
    assert sorted(p.name for p in root.iterdir() if not p.name.endswith(".tags.json")) == [
        "IMG_1.JPG",
        "IMG_1.MOV",
    ]
    assert hashes(root) == before
    assert (summary.renamed, summary.failed) == (0, 1)
    assert summary.failures[0][0] == "IMG_1.JPG"
    assert "was skipped" in summary.failures[0][1]
    assert "Run: pam plan" in summary.failures[0][1]
    assert_verify_agrees(root, where="after a unit whose companion was skipped")

    conn = catalog(root)
    try:
        assert item_statuses(conn, plan_id) == {"IMG_1.JPG": "failed", "IMG_1.MOV": "skipped"}
        assert all(
            repo.rows_in_state(conn, state) == []
            for state in ("intent", "done", "failed", "reverted")
        )
    finally:
        conn.close()


def test_a_unit_the_user_decided_out_still_groups_and_still_shares_a_stem_next_time(
    tmp_path,
):
    """The other half of the guarantee: refusing costs the user nothing permanent.

    Re-scan and re-plan after the refusal. The companion group is intact, and the next
    plan proposes ONE stem for both members - `_001`, not `_001` and `_002`. This is
    the assertion the pre-rev-15 behaviour could not make (see the regression test
    below for what it produced instead).
    """
    root, plan_id = companion_archive(tmp_path)
    skip_item(root, plan_id, "IMG_1.MOV")
    approve_and_apply(plan_id, root)

    run_scan(root, exiftool_command=FAKE_COMMAND)
    summary = run_plan(root)

    conn = catalog(root)
    try:
        groups = {
            row.rel_path: row.companion_group_id
            for row in repo.all_files(conn).values()
            if row.rel_path in ("IMG_1.JPG", "IMG_1.MOV")
        }
        assert len(set(groups.values())) == 1 and None not in groups.values()
        proposed = {
            i.old_rel_path: i.new_filename for i in repo.plan_items(conn, summary.plan_id)
        }
    finally:
        conn.close()
    assert proposed == {
        "IMG_1.JPG": "2019-07-04_001.JPG",
        "IMG_1.MOV": "2019-07-04_001.MOV",
    }


def test_a_unit_split_across_two_stems_is_unrecoverable_by_any_pam_command(tmp_path):
    """WHY the refusal above is worth a failed item: the split is permanent.

    The split is manufactured here by renaming one member OUTSIDE pam, because after
    spec 7.3 rev 15 `pam apply` can no longer produce it - which is the whole point of
    the task. The disk state is identical either way, and it is the disk state that
    does the damage:

      * spec 5.4 groups companions by STEM, so once the stems differ
        `companion_group_id` goes NULL for both and the group is gone; and
      * the next plan proposes `_002` for the orphan, because `_001` is owned by the
        photo it used to belong to.

    After that the pair is separated, differently numbered, and no pam command proposes
    reuniting them. `pam undo` reverses a session; it cannot reverse this convergence.
    Pinned as its own test so that a future change to either mechanism has to look at
    the consequence before deciding it is harmless.
    """
    root, _plan_id = companion_archive(tmp_path)
    (root / "IMG_1.JPG").rename(root / "2019-07-04_001.JPG")
    (root / "IMG_1.JPG.tags.json").rename(root / "2019-07-04_001.JPG.tags.json")

    run_scan(root, exiftool_command=FAKE_COMMAND)
    summary = run_plan(root)

    conn = catalog(root)
    try:
        live = {
            row.rel_path: row.companion_group_id
            for row in repo.all_files(conn).values()
            if row.status != "missing" and not row.rel_path.endswith(".tags.json")
        }
        assert live == {"2019-07-04_001.JPG": None, "IMG_1.MOV": None}
        proposed = {
            i.old_rel_path: i.new_filename for i in repo.plan_items(conn, summary.plan_id)
        }
    finally:
        conn.close()
    assert proposed == {"IMG_1.MOV": "2019-07-04_002.MOV"}


# --- the architectural guard (plan Step 2) ----------------------------------------


def test_apply_orchestrates_and_never_moves_a_file_or_resolves_a_journal_row():
    """spec 7.1 rev 7: `apply.py` is orchestration only.

    The filesystem operation belongs to `rename.py` and the journal state machine to
    `journal_ops.py`. A second implementation of either, added here because it was
    convenient, is how apply, undo and recovery drift into three answers to "check,
    then move an irreplaceable file" by M8.
    """
    from pam.execute import apply

    source = Path(apply.__file__).read_text(encoding="utf-8")
    assert move_call_sites(source) == []
    assert resolve_call_sites(source) == []


def test_the_resolve_guard_catches_every_route_to_the_journal_state_machine():
    """The guard proven enforcing, not merely present."""
    planted = [
        "from pam.catalog import repo\ndef f():\n    repo.resolve(conn, 1, 'done', now=n)\n",
        "from pam.catalog.repo import resolve\ndef f():\n    resolve(conn, 1, 'done', now=n)\n",
        "from pam.catalog.repo.journal import resolve as r\ndef f():\n    r(c, 1, 'done', now=n)\n",
    ]
    assert all(resolve_call_sites(source) != [] for source in planted)
    benign = (
        '"""No journal row is resolved here; repo.resolve lives elsewhere."""\n'
        "def f(path):\n    return path.resolve()\n"
    )
    assert resolve_call_sites(benign) == []
