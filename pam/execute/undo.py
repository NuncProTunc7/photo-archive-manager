"""pam undo: put one applied session's files back under their original names (spec 7.4).

THE UNDO-SHAPED BUILDER. `unit.execute_rename_unit` is the one sequencing loop; this
module is its second caller, and it exists as a separate builder because undo's inputs
are journal rows while apply's are plan items. That is not a stylistic split - the M5
plan said undo should go "through the same `execute_unit`", and driven with exactly
what an undo has, `execute_unit` renames nothing and rewrites a correctly applied
plan's items from `applied` to `failed`. Four of the five mismatches are load-bearing:
the direction, the `op`, the `(size, mtime_ns)` source, and its catalog precondition.
The invariant the plan actually wanted - no second implementation of "check, then move
an irreplaceable file" - holds: one `perform_verified_rename`, one sequencing loop, one
`revert_unit`.

WHAT UNDO REPLAYS. That session's journal rows in state `done`, in REVERSE order,
grouped by the same `plan_items.unit_key` the apply used, journalled as new rows with
op `undo_rename`. The direction is reversed (the row's `new_rel_path` is where the file
is now; its `old_rel_path` is where it goes back to) and the `(size, mtime_ns)` come
from the JOURNAL ROW, never from `files` - spec 7.4 is explicit, and it is why they are
journalled at all: `files` has been rewritten since, possibly years ago.

VERIFICATION AND SKIPPING (spec 7.0 J4). `done` means pam performed that rename as of
the moment of resolution; it promises nothing about the file now. So every member is
verified against the disk by `perform_verified_rename` - the current path must exist
with the journalled size and mtime, and the original path must be free
case-insensitively - and a mismatch leaves the file exactly where it is and the
ORIGINAL journal row still `done`, so a later undo can still succeed once the user puts
things back. Never forced, never guessed at (vision A8).

WHY IT SELECTS ON `plan_items.status`. Only items currently `applied` are replayed.
This is choosing what work to do, exactly as apply chooses `proposed`/`approved` items,
and it is not spec 7.0 J3's "branch on the disk" (that governs how spec 7.2 RESOLVES an
`intent` row). It is load-bearing: `journal_ops._STATUS_AFTER_NO_CHANGE['undo_rename']`
is `applied`, and that is only true when the file was at its applied name before the
undo tried. Replaying an already-`undone` item - which a second `pam undo` of the same
session would do, because the `rename` rows stay `done` forever (spec 7.0 J2) - would
write `applied` for a file sitting at its original name. A plan_item status the disk
contradicts is the exact defect class this milestone exists to prevent.

PLANS.STATUS AFTER AN UNDO (owner-ratified 2026-08-06; `undone` is already in spec 4's
enum, so no amendment was needed). The plan becomes `undone` when no item of it is
`applied` any more - nothing pam renamed in this archive is still renamed. A PARTIAL
undo leaves the plan exactly where it was, deliberately: a plan whose files are partly
restored is not undone, and saying so would tell the user their archive is back to how
it was while one of their photos is not. Items that never applied (`failed`, `skipped`)
do not block it, because they were never renamed - the rule is about what survives, not
about how the plan was written.
"""

import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pam.catalog import repo
from pam.catalog.db import migrate, open_catalog
from pam.errors import PamError
from pam.execute.journal_ops import RenameItem
from pam.execute.recover import run_recovery
from pam.execute.rename import DirectoryNames
from pam.execute.reporter import announce_interrupted, describe_session
from pam.execute.unit import UNDO_REASONS, execute_rename_unit
from pam.execute.validator import is_noop
from pam.faults import maybe_fault
from pam.lock import HeldWorkspace, WorkspaceLock
from pam.paths import resolve_archive, workspace_if_exists
from pam.sessionlog import close_session_log, open_session_log

# spec 4's plans.status enum, for the one value undo can write.
STATUS_UNDONE = "undone"

# spec 4's journal `op` for a row that puts a file back. Aliased to the repo's constant
# rather than spelled again here: `SessionCounts.ops` made this value something several
# modules compare against, and two string literals is how they drift.
OP_UNDO = repo.OP_UNDO_RENAME

# A session pam has already undone. Not an error and not a failure - the user asked
# for something that has already happened, and saying so plainly is the whole answer.
ALREADY_UNDONE_REASON = (
    "{rel_path} is already back under its original name, so pam left it alone."
)

# The catalog disagreeing with itself about one group. Reported rather than resolved:
# undoing half a companion group is the split vision S3 forbids, and pam has no way to
# tell which half is right.
UNIT_DISAGREES_REASON = (
    "{rel_path} was left alone: pam's records disagree about whether the files it is"
    " grouped with have been put back. Run: pam scan and pam verify."
)

# Unreachable through pam - `journal.plan_item_id` is a foreign key and nothing deletes
# a plan_items row - so this is catalog corruption, reported rather than raised.
CORRUPT_PLAN_ITEM_REASON = (
    "{rel_path} could not be put back: pam's records for it are damaged. Run: pam verify"
)

REDO_REFUSAL = (
    "Session {session_id} is itself an undo, and pam cannot redo one.\n"
    "The files are already under their original names. To rename them again, run:"
    " pam plan, then pam apply."
)


@dataclass
class UndoSummary:
    """What one `pam undo` invocation did. Task 10's reporter renders it (UX 4)."""

    # The NEW session this undo journalled under - the one `pam undo` would take to
    # undo the undo, if that were supported. Distinct from the session being undone.
    session_id: str = ""
    undone_session_id: str = ""
    restored: int = 0
    failed: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    # Files that were already back where they started. NOT failures: nothing went
    # wrong, and a second `pam undo` of a finished session must not exit 1.
    already_undone: list[str] = field(default_factory=list)
    # Files a failed revert left under their ORIGINAL names while their group is not
    # (spec 7.3). Empty except under external interference; needs a human, not a re-run.
    stranded: list[str] = field(default_factory=list)
    # What the plan's status BECAME, or None when undo left it exactly as it was.
    plan_status: str | None = None
    elapsed_s: float = 0.0
    log_path: Path | None = None

    @property
    def had_errors(self) -> bool:
        """spec 13 rev 6: a command that recorded per-file errors exits 1, not 0."""
        return self.failed > 0

    def record_failure(self, rel_path: str, reason: str) -> None:
        """The single entry point, so the count and the detail can never disagree."""
        self.failed += 1
        self.failures.append((rel_path, reason))


def run_undo(session_id: str, archive: Path | None = None) -> UndoSummary:
    """Undo one session against one archive. The second mutating path in pam.

    `archive` follows spec 3's resolution: the given folder, else the single reachable
    known archive, else ask. The workspace must already exist - only `pam scan` may
    create one - so a typo'd path is a refusal, never a silent "nothing to do".
    """
    # A PLAIN clock, and correct as a plain clock: `pam undo` asks the user nothing, so
    # every second between here and the summary is time pam spent working. Apply's
    # `_Clock` exists only because UX 4's typed confirmation sits inside its window and
    # HT-M5 measured ~9.8 s of reading being reported as renaming time.
    # IF UNDO EVER GROWS A CONFIRMATION, copy apply's shape, not this one - a prompt
    # added above this line would put the same fiction into `UndoSummary.elapsed_s`,
    # and the summary line renders it identically ("in Ns").
    started = time.monotonic()
    root = resolve_archive(archive)
    ws = workspace_if_exists(root)
    if ws is None:
        raise PamError(f"pam has not scanned {root} yet. Run: pam scan {root}")

    summary = UndoSummary(undone_session_id=session_id)
    with WorkspaceLock(ws.lock_path, ws) as held:
        logger, log_path = open_session_log(ws.logs_dir, suffix="undo")
        summary.log_path = log_path
        try:
            conn = open_catalog(ws.db_path)
            try:
                migrate(conn)
                undo_locked(held, conn, root, session_id, summary, logger)
            finally:
                conn.close()
        except BaseException:
            # spec 13: every session ends with the log path on stderr, and this is the
            # case where it matters most - pam/cli.py's `except Exception` prints a
            # traceback with no log path, so mid-undo on an archive of real files the user would
            # be left with nothing to read. NOT a durability mechanism (a `finally` is
            # exactly what a power cut skips): the session id is durable in the journal
            # rows themselves. ONE implementation, in reporter.py - it was written
            # twice, verbatim, here and in apply.py, which is exactly the drift spec
            # 7.1 rev 7's decomposition exists to stop.
            announce_interrupted(summary.session_id, summary.log_path)
            raise
        finally:
            close_session_log(logger)
    summary.elapsed_s = time.monotonic() - started
    return summary


def undo_locked(
    held: HeldWorkspace,
    conn: sqlite3.Connection,
    root: Path,
    session_id: str,
    summary: UndoSummary,
    logger: logging.Logger,
    progress: Callable[[int, int], None] | None = None,
) -> None:
    """The undo's body, under a lock the caller proves it holds (M14 Task 6, Step 1).

    `progress(done, total)` (R6-1) is told the journal rows finished after each unit, of the
    session's `done` rows. None, the CLI's value, reports nothing.

    `run_undo`'s old `_undo`, renamed and given `held` - the order below is unchanged. `held`
    must name `root`'s workspace and still be held, or this raises AssertionError before
    recovery runs or a journal row is read. The caller owns the lock, the connection, the log
    and the clock; the review page's job thread is its second caller (U19).
    """
    held.require(workspace_if_exists(root))
    # spec 7.2: inside the lock, before this command's own work. It matters more here
    # than for any other command: undo reads journal STATES to decide what to replay,
    # and a row still in `intent` is a rename whose outcome nobody has decided yet.
    run_recovery(conn, root, logger)

    rows = undoable_rows(conn, root, session_id)
    done = [row for row in rows if row.state == "done"]
    plan_rows = repo.plan_items_by_id(conn, [row.plan_item_id for row in done])

    summary.session_id = repo.next_session_id(conn, _now())
    logger.info(
        "undo start: session=%s undoing=%s archive=%s log=%s",
        summary.session_id,
        session_id,
        root,
        summary.log_path,
    )

    # X4: ONE per run, so a folder is listed once and not once per restore. Built after
    # recovery, which builds its own and never shares one with this run.
    names = DirectoryNames()
    finished = 0
    for unit in _units_of(done, plan_rows):
        _undo_unit(conn, root, unit, plan_rows, summary, logger, names)
        finished += len(unit)
        if progress is not None:
            progress(finished, len(done))
    _finalize_plan(conn, done, summary, logger)

    logger.info(
        "undo done: session=%s restored=%s failed=%s already_undone=%s plan=%s",
        summary.session_id,
        summary.restored,
        summary.failed,
        len(summary.already_undone),
        summary.plan_status,
    )


def undoable_rows(
    conn: sqlite3.Connection, root: Path, session_id: str
) -> list[repo.JournalRow]:
    """The session's journal rows, or the CLI's refusal: no such session, or an undo (no redo).

    ONE definition for `undo_locked` and the review page's session screen (M14 Task 6, U14):
    the page refuses the same sessions with the same sentences. Reads only.
    """
    rows = repo.session_rows(conn, session_id)
    if not rows:
        raise PamError(_unknown_session_message(conn, session_id, root))
    if {row.op for row in rows} == {OP_UNDO}:
        raise PamError(REDO_REFUSAL.format(session_id=session_id))
    return rows


def _units_of(
    done: list[repo.JournalRow], plan_rows: dict[int, repo.PlanItemRow]
) -> list[list[repo.JournalRow]]:
    """The session's `done` rows, REVERSED, grouped by the plan's frozen `unit_key`.

    Reversed first and grouped second, so both orders are the reverse of the apply:
    the units come back in the opposite order to the one they went out in, and so do
    the members within each unit. The archive walks backwards through exactly the
    states it walked forwards through, and no file is ever briefly absent from both of
    its names.

    Grouped by `plan_items.unit_key` and never by `files.companion_group_id`, for the
    reason spec 7.3 rev 6 gives: a scan between the apply and the undo rebuilds those
    groups from scratch, and a regrouped unit would be undone in pieces (vision S3).
    Rows whose plan item is missing keep their own group of one, so the corruption is
    reported against that file rather than silently merging it into a real unit.
    """
    units: dict[str, list[repo.JournalRow]] = {}
    for row in reversed(done):
        item = plan_rows.get(row.plan_item_id)
        key = item.unit_key if item is not None else f"\x00missing-{row.plan_item_id}"
        units.setdefault(key, []).append(row)
    return list(units.values())


def _undo_unit(
    conn: sqlite3.Connection,
    root: Path,
    unit: list[repo.JournalRow],
    plan_rows: dict[int, repo.PlanItemRow],
    summary: UndoSummary,
    logger: logging.Logger,
    names: DirectoryNames,
) -> None:
    """One unit: decide whether it is undoable at all, then hand it to the shared loop.

    Everything refused HERE is refused with nothing journalled and nothing moved, which
    is the same shape spec 7.3 uses for a unit blocked before its first rename: a
    journal row is a promise that pam is about to move a file (spec 7.0 J1), and these
    files are not touched.
    """
    items = [plan_rows.get(row.plan_item_id) for row in unit]
    if any(item is None for item in items):
        for row in unit:
            summary.record_failure(
                row.new_rel_path, CORRUPT_PLAN_ITEM_REASON.format(rel_path=row.new_rel_path)
            )
            logger.error("undo could not read the plan item for journal row %s", row.id)
        return

    statuses = {item.status for item in items if item is not None}
    if statuses == {STATUS_UNDONE}:
        # The ordinary second-`pam undo` case. Reported, not counted as a failure.
        for row in unit:
            summary.already_undone.append(row.old_rel_path)
            logger.info("undo skipped %s: already put back", row.old_rel_path)
        return
    if statuses != {"applied"}:
        for row in unit:
            summary.record_failure(
                row.new_rel_path, UNIT_DISAGREES_REASON.format(rel_path=row.new_rel_path)
            )
            logger.error("undo refused a unit whose item statuses disagree: %s", statuses)
        return

    rename_items = [
        RenameItem(
            plan_item_id=row.plan_item_id,
            file_id=item.file_id,
            # REVERSED: the journal's `new_rel_path` is where the file is now.
            old_rel_path=row.new_rel_path,
            new_rel_path=row.old_rel_path,
            # From the JOURNAL ROW, never from `files` (spec 7.4). A rename changes
            # neither size nor mtime, so this snapshot still describes the file under
            # its applied name - and a file edited since is left alone rather than
            # moved on top of whatever is now at the original name.
            size=row.size,
            mtime_ns=row.mtime_ns,
        )
        for row, item in zip(unit, items, strict=True)
        if item is not None
    ]
    result = execute_rename_unit(
        conn,
        archive_root=root,
        session_id=summary.session_id,
        op=OP_UNDO,
        unit_key=next(item.unit_key for item in items if item is not None),
        rename_items=rename_items,
        reasons=UNDO_REASONS,
        logger=logger,
        names=names,
    )
    summary.restored += result.renamed
    for rel_path, reason in result.failures:
        summary.record_failure(rel_path, reason)
    summary.stranded.extend(result.stranded)


def _finalize_plan(
    conn: sqlite3.Connection,
    done: list[repo.JournalRow],
    summary: UndoSummary,
    logger: logging.Logger,
) -> None:
    """`undone` when nothing pam renamed for this plan is still renamed. See the module
    docstring for why a partial undo leaves the status alone instead.

    `before_plan_finalize` (spec 9.3) sits here for the same reason it sits in
    `apply._finalize_plan`, and spec 9.3 describes it in op-neutral terms: "before the
    plan's terminal status is written". Undo writes a plan's terminal status, so this
    window exists on this path too, and it was unreachable from a test until the M5
    review gate added this line - which made the fault matrix declare three cells
    unreachable that were only unreachable by accident.

    What a crash HERE leaves is stated in spec 7.4 rev 10: the plan reads `applied`
    while every item reads `undone` and every file is back at its original name.
    Recovery is deliberately inert (J10 confines spec 7.2 phase 3 to `applying`, which
    undo never sets), and a second `pam undo` of the same session finalizes it. The
    checkpoint is unconditional, as apply's is: it marks a position in the code, not a
    condition about the plan, and a conditional one would make hit counting depend on
    how much of the plan was undone.
    """
    maybe_fault("before_plan_finalize")
    plan_ids = repo.plan_ids_for_items(conn, [row.plan_item_id for row in done])
    if len(plan_ids) != 1:
        # Not reachable today - one apply session covers one plan - and if it ever is,
        # writing a status to whichever plan came first would be a guess.
        logger.warning("undo touched %s plans, so no plan status was written", len(plan_ids))
        return
    plan_id = plan_ids[0]
    # spec 7.4 rev 9 point 3: `undone` when no item of that plan is `applied` any more.
    # A NO-OP ITEM IS NOT ONE OF THEM (spec 7.0 J12). It is resolved `applied` at
    # preflight and undo never touches it - it has no journal row, so it is in no undo
    # session's `done` rows - and read literally the line above would let one file that
    # never moved keep a fully restored plan reading `applied` FOREVER, with spec 7.5
    # check 4d reporting the stale status at every `pam verify` and a second `pam undo`
    # unable to clear it. That is not a special case bolted onto the check: J12 states
    # the rule the check was always making - "the plan is `undone` when nothing of it is
    # still applied ON DISK, and a file that never moved was never applied to anything" -
    # and `is_noop` is the same predicate the preflight resolved it with.
    if any(
        item.status == "applied" and not is_noop(item)
        for item in repo.plan_items(conn, plan_id)
    ):
        return
    repo.set_plan_status(conn, plan_id, STATUS_UNDONE)
    conn.commit()
    summary.plan_status = STATUS_UNDONE
    logger.info("plan %s is undone: no item of it is applied any more", plan_id)


def _unknown_session_message(conn: sqlite3.Connection, session_id: str, root: Path) -> str:
    """UX 1.4: a refusal that does not say what to do next is a dead end.

    The archive is NAMED because pam can know about several, and a session id from one
    of them is simply absent from another - "no such session" without saying where pam
    looked sends the user hunting for a typo they did not make.
    """
    known = repo.sessions(conn)
    if not known:
        return (
            f"pam has not applied anything to this folder yet, so there is nothing to"
            f" undo:\n  {root}\nRun: pam plan, then pam apply <plan id>"
        )
    listed = "\n".join(_session_line(counts) for counts in known)
    return (
        f"pam has no session {session_id} for this folder:\n  {root}\n"
        f"Sessions pam has here:\n{listed}\nRun: pam undo <session id>"
    )


def _session_line(counts: repo.SessionCounts) -> str:
    """One session, in the words a person choosing between them needs (UX 1.4).

    The distinction this renders is the whole point of `repo.SessionCounts` (M5 open
    question 7): a session already put back and a session left half-finished by a crash
    are entirely different answers to "can I undo this?", and under the old two-number
    shape they printed the same line. The states are read from the counts, never
    guessed at from `done` and `failed` alone.

    The description itself moved to `reporter.describe_session` at M6 Task 5, when
    `pam status` became its second caller. This function is now the chooser's LAYOUT -
    the session id and the brackets around the description - and nothing else.
    """
    return f"  {counts.session_id}   ({describe_session(counts)})"


def _now() -> str:
    """The project's timestamp format, matching every other `now=` in the catalog."""
    return datetime.now().isoformat(timespec="seconds")
