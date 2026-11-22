"""Undo (spec 7.4): put an applied session's files back, verifiably or not at all.

Every scenario applies a real plan to an archive of real files and then undoes it through the
same `run_undo` the CLI calls. Nothing is mocked, and the assertions are always pairs:
what is on disk AND what the catalog says about it, because undo's whole promise is
that those two agree afterwards.

The invariants this module owns, by name (spec 7.0):

  J2 - the journal is append-only. Undo ADDS `undo_rename` rows; the `rename` rows it
       is undoing keep their `done` state forever, which is what makes undo repeatable
       and spec 7.4's indefinite-undo promise possible.
  J4 - `done` means pam performed that rename AS OF RESOLUTION. It promises nothing
       about the file now, so undo verifies against disk and SKIPS on mismatch.
  J6 - a companion unit is undone together or not at all, exactly as it is applied.
"""

import json
import logging

import pytest
from archives import (
    FAKE_COMMAND,
    approve_and_apply,
    catalog,
    hashes,
    item_statuses,
    journal_pairs,
    planned,
)

from pam.catalog import repo
from pam.cli import main
from pam.errors import PamError
from pam.execute.recover import recover
from pam.execute.undo import _unknown_session_message, run_undo
from pam.naming.planner import run_plan
from pam.scan.runner import run_scan

LOGGER = logging.getLogger("tests.undo")


def companion_archive(tmp_path, names=("IMG_1.JPG", "IMG_1.MOV")):
    """One stem, several extensions: one companion group, one unit (spec 5.4)."""
    root = tmp_path / "photos"
    root.mkdir()
    for name in names:
        (root / name).write_bytes(f"the only copy of {name}".encode())
        (root / f"{name}.tags.json").write_text(
            json.dumps({"DateTimeOriginal": "2019:07:04 18:23:11"}), encoding="utf-8"
        )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    summary = run_plan(root)
    assert summary.plan_id is not None
    return root, summary.plan_id


def archive_names(root):
    """What is really on disk, minus the fake-exiftool sidecars."""
    return sorted(p.name for p in root.iterdir() if not p.name.endswith(".tags.json"))


def catalog_paths(root):
    """Every rel_path the catalog claims, minus the fake-exiftool sidecars."""
    conn = catalog(root)
    try:
        return sorted(p for p in repo.all_files(conn) if not p.endswith(".tags.json"))
    finally:
        conn.close()


def recover_now(root):
    conn = catalog(root)
    try:
        return recover(conn, root, LOGGER)
    finally:
        conn.close()


# --- the whole point: an applied session goes back, exactly ------------------------


def test_undo_restores_the_exact_original_names_and_the_exact_bytes(tmp_path):
    """spec 7.4's headline promise, and UX 4's copy: "Undo restores exact original
    names." Asserted three ways at once - the names on disk, the SHA-256 of every
    file (pam renames, it never rewrites a byte), and the catalog agreeing with both.
    A rename that lost a byte would pass the first assertion alone."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG", "IMG_3.JPG"])
    before_names = archive_names(root)
    before_hashes = hashes(root)
    applied = approve_and_apply(plan_id, root)
    assert applied.renamed == 3

    summary = run_undo(applied.session_id, root)

    assert (summary.restored, summary.failed) == (3, 0)
    assert not summary.had_errors
    assert archive_names(root) == before_names
    assert hashes(root) == before_hashes
    assert catalog_paths(root) == sorted(before_names)


def test_undo_adds_rows_and_deletes_none_of_the_ones_it_undoes(tmp_path):
    """spec 7.0 J2. The `rename` rows stay `done` forever - that is what "pam did
    perform that rename" means, and deleting or rewinding them is what would make
    spec 7.4's indefinite-undo promise a lie. Undo is a second set of rows, in its own
    session, saying what it did."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    applied = approve_and_apply(plan_id, root)

    summary = run_undo(applied.session_id, root)

    assert summary.session_id != applied.session_id
    conn = catalog(root)
    try:
        original = repo.session_rows(conn, applied.session_id)
        assert [(r.op, r.state) for r in original] == [("rename", "done")]
        undo_rows = repo.session_rows(conn, summary.session_id)
        assert [(r.op, r.state) for r in undo_rows] == [("undo_rename", "done")]
        # The undo row records the move it actually made: applied name -> original.
        assert [(r.old_rel_path, r.new_rel_path) for r in undo_rows] == [
            ("2019-07-04_001.JPG", "IMG_1.JPG")
        ]
        assert item_statuses(conn, plan_id) == {"IMG_1.JPG": "undone"}
        assert repo.rows_in_state(conn, "intent") == []
    finally:
        conn.close()


def test_a_companion_unit_is_undone_together(tmp_path):
    """spec 7.3 backwards. The unit that was applied as one is undone as one, under
    the same `unit_key`, so spec 7.2's consistency pass can heal a crashed undo the
    same way it heals a crashed apply."""
    root, plan_id = companion_archive(tmp_path)
    applied = approve_and_apply(plan_id, root)
    assert archive_names(root) == ["2019-07-04_001.JPG", "2019-07-04_001.MOV"]

    summary = run_undo(applied.session_id, root)

    assert (summary.restored, summary.failed) == (2, 0)
    assert archive_names(root) == ["IMG_1.JPG", "IMG_1.MOV"]
    conn = catalog(root)
    try:
        undo_rows = repo.session_rows(conn, summary.session_id)
        assert {r.state for r in undo_rows} == {"done"}
        # One journalled unit, keyed by the plan's frozen unit_key - never by
        # files.companion_group_id, which a scan between apply and undo would rebuild.
        assert repo.split_units(conn) == []
    finally:
        conn.close()


def test_undo_replays_a_session_in_reverse_order(tmp_path):
    """spec 7.4: "in reverse order". The archive walks backwards through exactly the
    states it walked forwards through, so no file is ever briefly absent from both of
    its names. Read out of the session log, the way the apply-side revert-order test
    does it: journal ids are insertion order and read the same either way round, so
    only the order the filesystem was ASKED to move things in can distinguish them.

    Read from the log FILE rather than through caplog, because `open_session_log` sets
    `propagate = False` on the shared 'pam' logger and caplog only sees what reaches
    the root.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG", "IMG_3.JPG"])
    applied = approve_and_apply(plan_id, root)

    summary = run_undo(applied.session_id, root)

    log = summary.log_path.read_text(encoding="utf-8")
    moves = [
        line.split("renamed: ")[1].strip() for line in log.splitlines() if "renamed: " in line
    ]
    assert moves == [
        "2021-09-06_001.JPG -> IMG_3.JPG",
        "2020-08-05_001.JPG -> IMG_2.JPG",
        "2019-07-04_001.JPG -> IMG_1.JPG",
    ]


# --- spec 7.0 J4: verify against disk, skip on mismatch, never force ---------------


def test_a_file_renamed_outside_pam_is_skipped_and_the_rest_are_restored(tmp_path):
    """spec 7.0 J4 and vision A8: restore everything verifiable, guess nothing.

    `done` says pam performed that rename, not that the file is still there. One file
    was renamed outside pam afterwards, so undo cannot verify it and leaves it exactly
    where it is - and, crucially, leaves its journal row `done`, so a later undo can
    still succeed once the user puts it back.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG", "IMG_3.JPG"])
    applied = approve_and_apply(plan_id, root)
    (root / "2020-08-05_001.JPG").rename(root / "MY_OWN_NAME.JPG")

    summary = run_undo(applied.session_id, root)

    assert (summary.restored, summary.failed) == (2, 1)
    assert summary.had_errors
    reason = dict(summary.failures)["2020-08-05_001.JPG"]
    assert "2020-08-05_001.JPG" in reason
    assert archive_names(root) == ["IMG_1.JPG", "IMG_3.JPG", "MY_OWN_NAME.JPG"]
    conn = catalog(root)
    try:
        # The row it could not undo is STILL `done` - the promise that a later undo
        # can still succeed rests on exactly this (spec 7.0 J2, J4).
        original = {
            r.new_rel_path: r.state for r in repo.session_rows(conn, applied.session_id)
        }
        assert original["2020-08-05_001.JPG"] == "done"
        assert item_statuses(conn, plan_id) == {
            "IMG_1.JPG": "undone",
            "IMG_2.JPG": "applied",
            "IMG_3.JPG": "undone",
        }
    finally:
        conn.close()


def test_a_taken_original_name_is_refused_rather_than_overwritten(tmp_path):
    """spec 7.0 J8: pam never overwrites, and undo is the command where doing so would
    destroy the file the user actually has. Something else is sitting on the original
    name, so the undo of that file is refused and the file stays where it is."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    applied = approve_and_apply(plan_id, root)
    (root / "IMG_1.JPG").write_bytes(b"something else entirely")

    summary = run_undo(applied.session_id, root)

    assert (summary.restored, summary.failed) == (0, 1)
    assert "never overwrites" in dict(summary.failures)["2019-07-04_001.JPG"]
    assert (root / "IMG_1.JPG").read_bytes() == b"something else entirely"
    assert (root / "2019-07-04_001.JPG").exists()


def test_a_companion_unit_that_cannot_be_undone_whole_is_not_undone_at_all(tmp_path):
    """spec 7.3 backwards, and the reason `revert_unit` is shared by three callers.

    The unit is undone in reverse, so the `.MOV` goes back first and the `.JPG`'s
    original name turns out to be taken. The `.MOV` is therefore renamed FORWARD again
    and the group ends exactly where it started - together, under its applied stem.
    Half a group put back is the split vision S3 forbids, and it is the same code path
    that protects an apply.
    """
    root, plan_id = companion_archive(tmp_path)
    applied = approve_and_apply(plan_id, root)
    before = hashes(root)
    (root / "IMG_1.JPG").write_bytes(b"in the way")

    summary = run_undo(applied.session_id, root)

    assert summary.restored == 0
    assert archive_names(root) == [
        "2019-07-04_001.JPG",
        "2019-07-04_001.MOV",
        "IMG_1.JPG",
    ]
    reasons = dict(summary.failures)
    # The member that moved and moved back gets its OWN sentence, in undo's words -
    # telling the user it "was renamed and then put back" would describe the opposite
    # of what they watched happen.
    assert "put back and then renamed again" in reasons["2019-07-04_001.MOV"]
    assert "never overwrites" in reasons["2019-07-04_001.JPG"]
    conn = catalog(root)
    try:
        assert repo.split_units(conn) == []
        # Both items are back at `applied`, which is where the files are.
        assert set(item_statuses(conn, plan_id).values()) == {"applied"}
    finally:
        conn.close()
    # The interloper is the only new content; nothing pam touched was rewritten.
    assert set(before) <= set(hashes(root))


# --- a second undo does nothing, and above all does not re-apply -------------------


def test_undoing_twice_finds_nothing_to_do_and_does_not_re_apply(tmp_path):
    """The dangerous one. The `rename` rows stay `done` forever (J2), so a second
    `pam undo` of the same session sees exactly what the first one saw. It must not
    replay anything - and it must not write a plan_item status the disk contradicts,
    which is what re-attempting an already-undone item would do."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    applied = approve_and_apply(plan_id, root)
    first = run_undo(applied.session_id, root)
    assert first.restored == 2
    names_after_first = archive_names(root)

    second = run_undo(applied.session_id, root)

    assert second.restored == 0
    assert second.failed == 0
    assert not second.had_errors  # nothing went wrong; there was nothing to do
    assert sorted(second.already_undone) == ["IMG_1.JPG", "IMG_2.JPG"]
    assert archive_names(root) == names_after_first
    conn = catalog(root)
    try:
        assert set(item_statuses(conn, plan_id).values()) == {"undone"}
        # No journal row was written for a second time, in either direction.
        assert repo.session_rows(conn, second.session_id) == []
    finally:
        conn.close()


def test_undoing_an_undo_session_is_refused_with_a_route_forward(tmp_path):
    """spec 4's journal `op` vocabulary is two values, and `journal_ops`' two status
    maps are keyed by it - so re-applying would have to write either `undone` for a
    file at its applied name or `failed` for one at its original name. Both are
    statuses the disk contradicts, which is the exact defect class this milestone
    exists to prevent. So it is refused, and the message names the supported route."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    applied = approve_and_apply(plan_id, root)
    undone = run_undo(applied.session_id, root)

    with pytest.raises(PamError) as refusal:
        run_undo(undone.session_id, root)

    assert "pam plan" in str(refusal.value)
    assert archive_names(root) == ["IMG_1.JPG"]


# --- plans.status after an undo (spec 4's enum already carries `undone`) -----------


def test_a_completed_undo_marks_the_plan_undone(tmp_path):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    applied = approve_and_apply(plan_id, root)
    conn = catalog(root)
    try:
        assert repo.plan_status(conn, plan_id) == "applied"
    finally:
        conn.close()

    summary = run_undo(applied.session_id, root)

    assert summary.plan_status == "undone"
    conn = catalog(root)
    try:
        assert repo.plan_status(conn, plan_id) == "undone"
    finally:
        conn.close()


def test_a_partial_undo_leaves_the_plan_where_it_was(tmp_path):
    """The half a later reader will simplify away. One file is still under its applied
    name, so the plan is NOT undone - calling it so would tell the user their archive
    is back to how it was while one of their photos is not."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG", "IMG_3.JPG"])
    applied = approve_and_apply(plan_id, root)
    (root / "2020-08-05_001.JPG").rename(root / "MY_OWN_NAME.JPG")

    summary = run_undo(applied.session_id, root)

    assert summary.restored == 2
    assert summary.plan_status is None  # untouched, not rewritten
    conn = catalog(root)
    try:
        assert repo.plan_status(conn, plan_id) == "applied"
    finally:
        conn.close()


def test_a_plan_whose_applied_items_are_all_undone_is_undone(tmp_path):
    """An item that FAILED at apply time was never renamed, so it cannot block the
    plan from being undone: nothing pam did to this archive survives."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    (root / "2020-08-05_001.JPG").write_bytes(b"in the way")
    applied = approve_and_apply(plan_id, root)
    assert (applied.renamed, applied.failed) == (1, 1)

    summary = run_undo(applied.session_id, root)

    assert summary.restored == 1
    assert summary.plan_status == "undone"
    conn = catalog(root)
    try:
        assert item_statuses(conn, plan_id) == {
            "IMG_1.JPG": "undone",
            "IMG_2.JPG": "failed",
        }
    finally:
        conn.close()


# --- crash and recovery (spec 7.2 covers undo too, because undo is journaled) ------


def test_a_crash_mid_undo_is_recovered_and_the_next_undo_converges(tmp_path, set_fault):
    """spec 7.4: "Undo is itself journaled, so a crashed undo is recovered by the same
    spec 7.2 pass." The crash lands after the rename and before the resolution, so the
    file is at its original name with the undo row still `intent` - the adoption branch
    - and a second undo then has nothing left to do for that file."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    applied = approve_and_apply(plan_id, root)
    set_fault.arm("crash:after_rename:1")
    with pytest.raises(SystemExit):
        run_undo(applied.session_id, root)
    assert set_fault.hits("after_rename") == 1
    set_fault.next_invocation()

    recovered = recover_now(root)

    assert recovered.adopted == ["IMG_2.JPG"]  # reverse order: IMG_2 goes back first
    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "intent") == []
        assert item_statuses(conn, plan_id)["IMG_2.JPG"] == "undone"
    finally:
        conn.close()

    summary = run_undo(applied.session_id, root)

    assert summary.restored == 1
    assert summary.already_undone == ["IMG_2.JPG"]
    assert archive_names(root) == ["IMG_1.JPG", "IMG_2.JPG"]
    assert summary.plan_status == "undone"


def test_a_crash_mid_undo_of_a_unit_is_healed_by_the_consistency_pass(tmp_path, set_fault):
    """The undo half of spec 7.2 phase 2. A crash between the members of an undone
    unit leaves one file at its original name and one at its applied name - a group
    split across two stems, the outcome vision S3 forbids - and recovery puts it
    back together. `_STATUS_AFTER_NO_CHANGE['undo_rename']` is `applied`, which is
    what the files then are."""
    root, plan_id = companion_archive(tmp_path, ("IMG_1.JPG", "IMG_1.MOV"))
    applied = approve_and_apply(plan_id, root)
    set_fault.arm("crash:mid_companion_unit:1")
    with pytest.raises(SystemExit):
        run_undo(applied.session_id, root)
    assert set_fault.hits("mid_companion_unit") == 1
    set_fault.next_invocation()

    recovered = recover_now(root)

    assert recovered.reverted or recovered.failed
    assert archive_names(root) == ["2019-07-04_001.JPG", "2019-07-04_001.MOV"]
    conn = catalog(root)
    try:
        assert repo.split_units(conn) == []
        assert repo.rows_in_state(conn, "intent") == []
        assert set(item_statuses(conn, plan_id).values()) == {"applied"}
    finally:
        conn.close()


def test_undo_recovers_before_it_reads_the_session(tmp_path, set_fault):
    """Recovery runs inside the lock and before the command's own work, for undo as
    for every other archive-scoped command. It matters more here than anywhere: undo
    reads journal STATES to decide what to replay, and an unresolved `intent` row is
    a rename whose outcome nobody has decided yet."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    set_fault.arm("crash:after_rename:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()
    conn = catalog(root)
    try:
        session_id = repo.rows_in_state(conn, "intent")[0].session_id
    finally:
        conn.close()

    summary = run_undo(session_id, root)

    # The interrupted apply's row was adopted by recovery, so undo found it `done`
    # and put the file back in the same invocation.
    assert summary.restored == 1
    assert archive_names(root) == ["IMG_1.JPG"]


# --- refusals (UX 1.4: a refusal that does not say what to do next is a dead end) --


def test_an_unknown_session_lists_the_sessions_pam_knows_and_names_the_archive(tmp_path):
    """The archive is named because pam can know about several, and a session id from
    one of them is simply absent from another - "no such session" without saying WHERE
    pam looked sends the user hunting for a typo they did not make."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    applied = approve_and_apply(plan_id, root)

    with pytest.raises(PamError) as refusal:
        run_undo("s-19990101-000000", root)

    message = str(refusal.value)
    assert str(root) in message
    assert applied.session_id in message
    assert "2 renamed" in message


def _listed_line(message, session_id):
    """The one line of the chooser that describes `session_id`.

    Matched on `id + " "` rather than by substring: `s-X` is a prefix of `s-X-2`, which
    is the very suffix `next_session_id` hands out when two sessions land in the same
    second - so a substring match would silently read the wrong line.
    """
    (line,) = [
        line for line in message.splitlines() if line.strip().startswith(session_id + " ")
    ]
    return line


def test_the_session_chooser_tells_applied_undone_and_unresolved_apart(tmp_path):
    """M5 open question 7, end to end and in the words the user actually reads.

    Three states, one catalog. Before `repo.SessionCounts` the middle one printed
    exactly what the first did (spec 7.0 J2 keeps a `rename` row `done` forever, so
    undoing a session changes nothing the old query looked at), and the third printed
    `(0 renamed, 0 failed)` - a session with work outstanding, described as a session
    with no work at all. A chooser cannot be built on that (UX 1.4).

    The unresolved rows are written directly, and they have to be: spec 7.2's recovery
    runs at the start of every archive-scoped command, `run_undo` included, so the
    state exists precisely in the window no end-to-end call can hold open.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    put_back = approve_and_apply(plan_id, root)
    run_undo(put_back.session_id, root)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    second_plan = run_plan(root).plan_id
    assert second_plan is not None, "the fixture must produce a second plan to apply"
    still_applied = approve_and_apply(second_plan, root)

    conn = catalog(root)
    try:
        item = repo.plan_items(conn, second_plan)[0]
        repo.insert_intents(
            conn,
            "s-19990101-000000",
            "rename",
            [
                repo.JournalEntry(
                    plan_item_id=item.id,
                    old_rel_path="whatever.jpg",
                    new_rel_path="whatever_renamed.jpg",
                    size=1,
                    mtime_ns=1,
                )
            ],
            now="1999-01-01T00:00:00",
        )
        conn.commit()
        message = _unknown_session_message(conn, "s-20991231-235959", root)
    finally:
        conn.close()

    applied_line = _listed_line(message, still_applied.session_id)
    undone_line = _listed_line(message, put_back.session_id)
    unresolved_line = _listed_line(message, "s-19990101-000000")

    assert "2 renamed" in applied_line
    assert "undone" not in applied_line and "unresolved" not in applied_line
    assert "2 renamed" in undone_line and "already undone" in undone_line
    assert "1 unresolved" in unresolved_line and "run any pam command" in unresolved_line
    # The NEGATIVE half, and it is what makes the middle assertion mean anything (M6
    # Task 2 review, mutation M273). `_session_line` guards "already undone" with
    # `counts.done and ...`; drop that one operand and `0 >= 0` holds, so a session
    # left half-finished by a crash renders `(0 renamed, already undone, 1 unresolved
    # - run any pam command)` - work outstanding, described as work already put back.
    # That is worse than open question 7's original bug, which was merely ambiguous,
    # and all 997 tests stayed green without this line.
    assert "already undone" not in unresolved_line
    assert len({applied_line, undone_line, unresolved_line}) == 3


def test_the_chooser_calls_an_undo_session_a_put_back_not_a_rename(tmp_path):
    """M6 Task 5's ruling, at the site of the defect it fixes.

    An undo writes its own session, and that session appears in this chooser. Before
    `SessionCounts.ops`, its line read "2 renamed, already undone" - the verb false
    (those files were PUT BACK) and the offer false with it, because `run_undo` refuses
    an undo session outright with `REDO_REFUSAL`. A chooser cannot list an option the
    command it feeds will reject; that is the dead end UX 1.4 forbids.

    Both halves are asserted: the undo's line, and that the apply's line beside it still
    says "renamed" - a fix that renamed everything "put back" would pass a one-sided
    check while making the chooser exactly as wrong in the other direction.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    applied = approve_and_apply(plan_id, root)
    undone = run_undo(applied.session_id, root)

    conn = catalog(root)
    try:
        message = _unknown_session_message(conn, "s-20991231-235959", root)
    finally:
        conn.close()

    undo_line = _listed_line(message, undone.session_id)
    apply_line = _listed_line(message, applied.session_id)

    assert "2 put back" in undo_line
    assert "renamed" not in undo_line
    assert "an undo cannot itself be undone" in undo_line
    assert "2 renamed" in apply_line and "put back" not in apply_line


def test_an_archive_pam_has_never_scanned_is_refused(tmp_path):
    root = tmp_path / "photos"
    root.mkdir()

    with pytest.raises(PamError) as refusal:
        run_undo("s-19990101-000000", root)

    assert "pam scan" in str(refusal.value)


# --- the CLI (spec 12: `pam undo SESSION_ID`) --------------------------------------


def test_the_cli_undoes_a_session_and_exits_zero(tmp_path):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    applied = approve_and_apply(plan_id, root)

    assert main(["undo", applied.session_id, "--archive", str(root)]) == 0

    assert archive_names(root) == ["IMG_1.JPG"]


def test_the_cli_exits_one_when_a_file_could_not_be_put_back(tmp_path):
    """spec 13 rev 6: a command that recorded per-file errors exits 1, not 0. A script
    wrapping `pam undo` must be able to tell a complete restore from a partial one, and
    a human loses nothing - the summary prints in full either way."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    applied = approve_and_apply(plan_id, root)
    (root / "IMG_1.JPG").write_bytes(b"something else entirely")

    assert main(["undo", applied.session_id, "--archive", str(root)]) == 1


def test_the_cli_exits_zero_when_there_was_simply_nothing_left_to_undo(tmp_path):
    """The benign case must NOT exit 1. Nothing went wrong - the user asked for
    something that had already happened, which is not a per-file error."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    applied = approve_and_apply(plan_id, root)
    run_undo(applied.session_id, root)

    assert main(["undo", applied.session_id, "--archive", str(root)]) == 0


def test_the_cli_reports_an_unknown_session_as_one_clear_line(tmp_path, capsys):
    """spec 13: a PamError is one clear line and exit 1, never a traceback."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    approve_and_apply(plan_id, root)
    capsys.readouterr()

    assert main(["undo", "s-19990101-000000", "--archive", str(root)]) == 1

    printed = capsys.readouterr().err
    assert "Traceback" not in printed
    assert str(root) in printed


def test_a_file_edited_since_the_apply_is_left_alone(tmp_path):
    """spec 7.4: the `(size, mtime_ns)` undo verifies against come from the JOURNAL
    ROW, never from `files`.

    The file was edited outside pam and then re-scanned, so the `files` row now agrees
    with the edited file and only the journal still remembers what pam renamed. Undo
    must refuse it: putting an edited file back would move it on top of a name pam has
    not looked at since, and the user's edit would end up under a name that describes a
    photo they no longer have. This is the only scenario in which the two sources of
    the snapshot disagree, and it is why they are journalled at all.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    applied = approve_and_apply(plan_id, root)
    (root / "2020-08-05_001.JPG").write_bytes(b"edited outside pam, and rather longer")
    run_scan(root, exiftool_command=FAKE_COMMAND)

    summary = run_undo(applied.session_id, root)

    assert (summary.restored, summary.failed) == (1, 1)
    assert "changed on disk" in dict(summary.failures)["2020-08-05_001.JPG"]
    assert archive_names(root) == ["2020-08-05_001.JPG", "IMG_1.JPG"]
    conn = catalog(root)
    try:
        assert item_statuses(conn, plan_id)["IMG_2.JPG"] == "applied"
    finally:
        conn.close()


def test_a_unit_whose_item_statuses_disagree_is_left_alone(tmp_path):
    """Half a companion group put back is the split vision S3 forbids, and pam has no
    way to tell which half of a disagreeing record is right.

    Not reachable through pam - a crash mid-undo is healed by spec 7.2 phase 2 back to
    a unit that agrees - so the state is built by hand. That is the point: the guard
    exists for a catalog somebody else damaged, and `pam verify` is what diagnoses it.
    """
    root, plan_id = companion_archive(tmp_path)
    applied = approve_and_apply(plan_id, root)
    conn = catalog(root)
    try:
        first = repo.plan_items(conn, plan_id)[0]
        repo.set_plan_item_status(conn, first.id, "undone")
        conn.commit()
    finally:
        conn.close()

    summary = run_undo(applied.session_id, root)

    assert summary.restored == 0
    assert summary.failed == 2
    assert "records disagree" in dict(summary.failures)["2019-07-04_001.MOV"]
    assert archive_names(root) == ["2019-07-04_001.JPG", "2019-07-04_001.MOV"]
    conn = catalog(root)
    try:
        assert repo.session_rows(conn, summary.session_id) == []
    finally:
        conn.close()


def test_the_hash_helper_reads_the_files_that_are_really_there(tmp_path):
    """`archives.hashes` is what three test modules use to prove pam never rewrites a
    byte, and every one of those assertions compares it against itself - so a helper
    that returned nothing would leave them all passing while asserting nothing at all.
    A helper cannot pin its own reach; this does."""
    root, _plan_id = planned(tmp_path, ["IMG_1.JPG"])

    found = hashes(root)

    assert len(found) == 2  # the photo and its fake-exiftool sidecar
    (root / "IMG_1.JPG").write_bytes(b"different bytes entirely")
    assert hashes(root) != found


def test_an_undo_revert_that_fails_names_the_file_it_left_behind(tmp_path, allow_split_unit):
    """spec 7.3's stranding case, on the undo side: name the exact file, never guess.

    The `.MOV` is put back, the `.JPG`'s original name turns out to be taken, and
    something outside pam takes the `.MOV`'s applied name at the instant it is
    vacated - so pam cannot put the `.MOV` forward again either. Its journal row stays
    `done` (spec 7.0 J4: pam DID perform that move) and the file is reported by name.

    The J6 tripwire is sanctioned here for the same reason as its three other holders,
    and no new one: pam did not produce this split, the outside world did, and pam's
    only honest answer is to report it on every invocation until a human acts. The
    interference is injected through a logging FILTER rather than by patching pam, so
    pam's own code runs untouched and the file really is created by something else, at
    the only instant that can produce this state.
    """
    root, plan_id = companion_archive(tmp_path)
    applied = approve_and_apply(plan_id, root)
    (root / "IMG_1.JPG").write_bytes(b"in the way")

    class TakeTheAppliedNameBack(logging.Filter):
        def filter(self, record):
            if record.getMessage().startswith("renamed: 2019-07-04_001.MOV -> IMG_1.MOV"):
                (root / "2019-07-04_001.MOV").write_bytes(b"something else entirely")
            return True

    interference = TakeTheAppliedNameBack()
    logging.getLogger("pam").addFilter(interference)
    try:
        summary = run_undo(applied.session_id, root)
    finally:
        logging.getLogger("pam").removeFilter(interference)

    assert summary.stranded == ["IMG_1.MOV"]
    assert summary.restored == 0
    stranded_reason = dict(summary.failures)["2019-07-04_001.MOV"]
    assert "IMG_1.MOV" in stranded_reason
    assert "could not undo that" in stranded_reason
    assert archive_names(root) == [
        "2019-07-04_001.JPG",
        "2019-07-04_001.MOV",
        "IMG_1.JPG",
        "IMG_1.MOV",
    ]
    conn = catalog(root)
    try:
        # Left `done`, because it is: pam did put that file back (spec 7.0 J4).
        assert journal_pairs(conn, "done") == [
            ("IMG_1.JPG", "2019-07-04_001.JPG"),
            ("IMG_1.MOV", "2019-07-04_001.MOV"),
            ("2019-07-04_001.MOV", "IMG_1.MOV"),
        ]
    finally:
        conn.close()
