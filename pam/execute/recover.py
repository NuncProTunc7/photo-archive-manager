"""Crash recovery (spec 7.2 rev 8): four phases, at the start of every command.

After the phases, E6 (M15 Task 7): the report of every interrupted renaming run that has
none (`recovery_report.py`).

Runs INSIDE the workspace lock, after spec 3's archive resolution, and BEFORE the
command's own work - for `pam scan` that means before the walk. That ordering is not
tidiness: a scan that runs first records a completed-but-unresolved rename as one new
file and one missing file, and phase 1's adoption branch then collides with the row
the scan just created (reproduced in the 2026-08-06 design review).

The four phases run in this order and may not be reordered:

  0. PRECONDITION (J9) - the archive root must be a directory pam can see. If it is
     not, recovery does nothing at all and says so. `intent -> failed` is terminal
     (J2), so a decision made about an invisible disk can never be taken back.
  1. THE `intent` ROWS - branch on the DISK, never on the catalog (J3).
  2. THE UNIT-CONSISTENCY PASS (J6) - a unit holding a `done` member beside a
     `failed`/`reverted` one has its `done` members put back. Strictly AFTER phase 1:
     a unit holding `done` + `intent` is not yet mixed, so inspecting it first would
     skip it, phase 1 would then resolve the `intent` row, and the split would survive
     the invocation. `crash:mid_companion_unit:1` produces exactly that shape.
  3. PLAN FINALIZATION (J10) - every plan left `applying` is given a terminal status
     derived from its item statuses. No plan in any other status is ever changed.

WHY THE DERIVATION LIVES HERE AND APPLY IMPORTS IT. `apply._finalize_plan` used to
derive the terminal status from the run's own counters, which recovery does not have.
The two must never disagree about what "applied" means, so the sharing goes this way
round: recovery cannot borrow apply's counters, so apply borrows recovery's derivation.

WHAT THIS MODULE NEVER DOES. It never completes a failed unit forwards (apply already
decided that unit fails, and no `intent` row exists for those renames - J1). It never
deletes, never overwrites, never forces, and never raises for a state it cannot heal:
it runs at the start of every invocation, so an exception here takes the whole tool
down for that archive. States it cannot heal are reported by name, on every invocation
until a human resolves them, which is spec 7.0's rule for states pam cannot produce.

J7's convergence comes from re-querying each pass, never from re-issuing `repo.resolve`
(which is deliberately not idempotent). Every branch below either resolves a row to a
terminal state - removing it from the set the next pass queries - or changes nothing.
"""

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import click

from pam.catalog import repo
from pam.execute.journal_ops import RenameTransaction
from pam.execute.recovery_report import report_interrupted_runs
from pam.execute.rename import DirectoryNames
from pam.execute.unit import revert_unit

# The three phase-1 sentences, one per disk state (spec 7.2). Named constants because
# they end up in the session log and in the summary a human reads, and because the
# third one is the only place pam says out loud that it refused to guess.
NEVER_HAPPENED_REASON = (
    "{old_rel_path} still has its original name: pam was interrupted before it could"
    " rename this file, so nothing was changed."
)
AMBIGUOUS_REASON = (
    "{old_rel_path} needs your attention: pam was interrupted while renaming it to"
    " {new_rel_path}, and what is on disk now does not say whether the rename happened."
    " pam has changed nothing. Look at both names, keep the file you want, then run:"
    " pam scan"
)
CORRUPT_PLAN_ITEM_REASON = (
    "{old_rel_path} could not be recovered: pam's records for it are damaged. Run: pam verify"
)

# spec 4's plans.status enum, for the three values the apply/recover pair writes.
# They live HERE rather than in apply.py because both modules need them and apply
# imports recover (it runs recovery before its own work), so the other direction
# would be an import cycle.
STATUS_APPLYING = "applying"
STATUS_APPLIED = "applied"
STATUS_PARTIALLY_APPLIED = "partially_applied"

# Item statuses that mean "this item was never attempted". A plan holding one of these
# after a run did not finish its work, whatever else it managed.
_UNATTEMPTED = ("proposed", "approved")


@dataclass
class RecoverySummary:
    """What one recovery pass did. Empty on the overwhelmingly common path.

    Every list holds rel_paths a human can look for in their own folder, because the
    one thing this summary is for is telling a person what pam found and what it left
    for them.
    """

    # J9: the archive was not there, so nothing was inspected and nothing decided.
    archive_unreachable: bool = False
    adopted: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    needs_attention: list[str] = field(default_factory=list)
    reverted: list[str] = field(default_factory=list)
    stranded: list[str] = field(default_factory=list)
    discrepancies: list[str] = field(default_factory=list)
    finalized: list[tuple[int, str]] = field(default_factory=list)
    # The sessions the `needs_attention` files belong to, in the order they were met and
    # each named once. Carried so the one line this prints can point at the command that
    # will still answer the question next Saturday (M7 Task 4): spec 7.0 says these
    # states are reported for human attention and acted on never, and "reported once, on
    # stderr" is not what that means.
    attention_sessions: list[str] = field(default_factory=list)
    # E6: the sessions whose report this pass wrote. Not part of `did_anything`: a report
    # written for a run an earlier pass already resolved is not news worth a line on stderr.
    reported: list[str] = field(default_factory=list)
    # F1 (Task 7 Stage 2): the sessions phase 1 resolved a row of or phase 2 put a member of back.
    # With `finalized`'s plans, the only sessions whose report may say the run stopped part-way.
    recovered_sessions: set[str] = field(default_factory=set)

    @property
    def did_anything(self) -> bool:
        """Was there anything to recover? Recovery is SILENT when there was not."""
        return bool(
            self.archive_unreachable
            or self.adopted
            or self.failed
            or self.needs_attention
            or self.reverted
            or self.stranded
            or self.discrepancies
            or self.finalized
        )


def derive_plan_status(items: list[repo.PlanItemRow]) -> str:
    """spec 7.2 phase 3 / spec 7.1: a plan's terminal status, from its item statuses.

    `applied` when no item is `failed` and none is still waiting to be attempted;
    `partially_applied` otherwise - including the case where NOTHING was attempted,
    deliberately. A crash immediately after the `applying` write leaves every item
    `approved`, and calling that `applied` would claim work that never happened, while
    calling it `superseded` would overload a word spec 6.3 rule 5 defines as "a newer
    plan replaced it" and `open` would be resumption by another name (out of scope for
    v1.0 - spec 7.2).

    The one derivation, shared by apply's end-of-session finalization and recovery's
    phase 3, so a plan finished by apply and a plan finished by recovery cannot
    disagree about what "applied" means. It reads item statuses rather than a run's
    counters because recovery has no counters - and because this is the form that
    generalizes to M7's `skipped`.
    """
    if any(item.status == "failed" or item.status in _UNATTEMPTED for item in items):
        return STATUS_PARTIALLY_APPLIED
    return STATUS_APPLIED


def recover(
    conn: sqlite3.Connection, archive_root: Path, logger: logging.Logger
) -> RecoverySummary:
    """The whole of spec 7.2, in order. Returns what it found; raises for nothing."""
    summary = RecoverySummary()
    if not archive_root.is_dir():
        # Phase 0 (J9). Second layer: spec 3's resolution already refuses to hand a
        # command an unreachable root, and this is the layer that survives a
        # regression there.
        summary.archive_unreachable = True
        logger.error(
            "recovery skipped: pam cannot see %s right now, so no journal row was"
            " resolved and no plan was finalized (spec 7.0 J9)",
            archive_root,
        )
        return summary
    _resolve_intent_rows(conn, archive_root, summary, logger)
    # X4: the pass's OWN memory of the folders, built from fresh listings. Never one
    # inherited from a run that crashed - that run's memory died with it.
    _heal_split_units(conn, archive_root, summary, logger, DirectoryNames())
    _finalize_applying_plans(conn, summary, logger)
    # E6 (M15 Task 7): AFTER all three phases, never between them - phase 2 can put back a
    # member phase 1 just adopted. Reads only (and writes a report file); never raises for a
    # report it could not write.
    summary.reported = report_interrupted_runs(
        conn,
        archive_root,
        logger,
        summary.recovered_sessions,
        {plan_id for plan_id, _ in summary.finalized},
    )
    return summary


def run_recovery(
    conn: sqlite3.Connection, archive_root: Path, logger: logging.Logger
) -> RecoverySummary:
    """`recover()` plus its one line on stderr. What every command actually calls.

    Silent when there was nothing to recover, which is almost always: the whole point
    of running this before every command is that the user never notices it. When there
    WAS something, one line says so and the session log carries the detail, naming
    every file left needing human attention (spec 7.2's reporting rule).
    """
    summary = recover(conn, archive_root, logger)
    line = recovery_line(summary)
    if line is not None:
        click.echo(line, err=True)
    return summary


def recovery_line(summary: RecoverySummary) -> str | None:
    """The sentence `run_recovery` prints, or None when there was nothing to recover.

    Public for the review page (M14 Task 4, U12): a bind records it so the page can show
    recovery's OWN words rather than a second spelling of them.
    """
    return _announcement(summary) if summary.did_anything else None


def _announcement(summary: RecoverySummary) -> str:
    """One sentence naming what recovery found. Counts only - the log has the names."""
    if summary.archive_unreachable:
        return (
            "! pam could not check for interrupted work: it cannot see the photo folder"
            " right now. Nothing was changed."
        )
    parts = []
    if summary.adopted:
        parts.append(f"{len(summary.adopted)} rename(s) kept")
    if summary.reverted:
        parts.append(f"{len(summary.reverted)} file(s) put back")
    if summary.failed:
        parts.append(f"{len(summary.failed)} rename(s) not started")
    if summary.stranded:
        parts.append(f"{len(summary.stranded)} file(s) NEEDING YOUR ATTENTION")
    if summary.needs_attention:
        parts.append(f"{len(summary.needs_attention)} file(s) needing your attention")
    if not parts:
        # Only a plan status was tidied up: true, uninteresting, and still worth one
        # line rather than a silent write to a table the user cannot see.
        parts.append(f"{len(summary.finalized)} interrupted plan(s) closed")
    message = "pam finished tidying up after an interrupted run: " + ", ".join(parts) + "."
    # One runnable line per session, rather than one line listing several ids: a user
    # copies what pam prints, and "pam report session a, b" is not a command. In practice
    # this is one line - a crash is one session - and the loop is what keeps it honest
    # when it is not.
    for session_id in summary.attention_sessions:
        message += f"\n  Details anytime: pam report session {session_id}"
    return message


def _resolve_intent_rows(
    conn: sqlite3.Connection,
    archive_root: Path,
    summary: RecoverySummary,
    logger: logging.Logger,
) -> None:
    """Phase 1: every row in `intent`, branching on the DISK and never the catalog (J3).

    An `intent` row says pam MEANT to rename; it says nothing about disk (J3 spells out
    that all four disk states are reachable), so the file system is the only witness
    worth asking. `Path.exists()` deliberately, rather than the case-insensitive scan
    `rename.py` uses: there the question is "may I create this name", which must be
    conservative on every filesystem, while here it is "is the file at this path", and
    the answer that matters is the one the user's own filesystem would give.

    One row at a time (spec 7.0 J5), because a crash partway through recovery must
    leave every row it already resolved consistent with the disk.
    """
    rows = repo.rows_in_state(conn, "intent")
    if not rows:
        return
    # Looked up in one query for the whole batch, and BEFORE any resolution: a row
    # whose plan_item is gone cannot be resolved (the resolution has to point a `files`
    # row somewhere), and finding that out mid-loop would leave the batch half done.
    plan_rows = repo.plan_items_by_id(conn, [row.plan_item_id for row in rows])
    for row in rows:
        if row.plan_item_id not in plan_rows:
            # Unreachable through pam: `journal.plan_item_id` is a foreign key and
            # nothing deletes a plan_items row. Reported rather than raised because
            # this runs at the start of every command, and a catalog corruption must
            # not lock the user out of the tool that would tell them about it.
            reason = CORRUPT_PLAN_ITEM_REASON.format(old_rel_path=row.old_rel_path)
            _needs_attention(summary, row)
            logger.error("recovery could not resolve journal row %s: %s", row.id, reason)
            continue
        txn = RenameTransaction.adopt(conn, [row], logger=logger)
        (item,) = txn.items
        summary.recovered_sessions.add(row.session_id)  # every branch below resolves the row
        old_there = (archive_root / row.old_rel_path).exists()
        new_there = (archive_root / row.new_rel_path).exists()
        if old_there and not new_there:
            txn.fail(item, NEVER_HAPPENED_REASON.format(old_rel_path=row.old_rel_path))
            summary.failed.append(row.old_rel_path)
        elif new_there and not old_there:
            _record(summary, txn.commit(item))
            summary.adopted.append(row.new_rel_path)
            logger.warning(
                "recovery adopted a rename an interrupted run completed: %s -> %s",
                row.old_rel_path,
                row.new_rel_path,
            )
        else:
            # Both there, or neither. spec 7.0's closing paragraph: pam cannot produce
            # this, something outside pam can, and the answer is to change NOTHING on
            # disk, resolve the row so J7 holds, and name the file for a human. Being
            # clever here is the one thing that could lose a photograph.
            reason = AMBIGUOUS_REASON.format(
                old_rel_path=row.old_rel_path, new_rel_path=row.new_rel_path
            )
            txn.fail(item, reason)
            _needs_attention(summary, row)
            logger.error("recovery found an ambiguous rename: %s", reason)


def _needs_attention(summary: RecoverySummary, row: repo.JournalRow) -> None:
    """Name one file for a human, and remember which session it can be looked up under.

    The single entry point for both, so the summary's pointer can never name a session
    whose file is not in the list, nor go missing for one that is.
    """
    summary.needs_attention.append(row.old_rel_path)
    if row.session_id not in summary.attention_sessions:
        summary.attention_sessions.append(row.session_id)


def _record(summary: RecoverySummary, discrepancy: str | None) -> None:
    """Fold a J11 catalog discrepancy into the summary, if there was one."""
    if discrepancy is not None:
        summary.discrepancies.append(discrepancy)


def _heal_split_units(
    conn: sqlite3.Connection,
    archive_root: Path,
    summary: RecoverySummary,
    logger: logging.Logger,
    names: DirectoryNames,
) -> None:
    """Phase 2, the unit-consistency pass (spec 7.2 rev 8, spec 7.0 J6, vision S3).

    A companion unit is the pair `(journal.session_id, plan_items.unit_key)` - never
    `files.companion_group_id`, which pam scan recreates from scratch every scan. A
    unit holding a `done` member beside a `failed` or `reverted` one is a group split
    across two stems, and this puts the `done` members back, newest first, through the
    SAME `perform_verified_rename` apply and undo use. Branching on the disk (J3) is
    therefore free: that function stats the real file and refuses when the old name is
    taken, when the file is not where the journal says, or when it changed since the
    intent snapshot.

    Runs strictly AFTER phase 1. A unit holding `done` + `intent` is not yet mixed in
    this sense, so inspecting it first would skip it, phase 1 would then resolve the
    `intent` row to `failed`, and the split would survive the invocation -
    `crash:mid_companion_unit:1` produces exactly that shape.

    ONE ATTEMPT PER MEMBER, no loop and no retry inside an invocation. A member that
    will not go back is left `done` (J4: pam DID perform that rename) and reported by
    name. Across invocations it is tried again, which is wanted: when the user removes
    whatever is in the way, the next pam command heals the unit with no special action.
    Reporting the split every time until then is the loud failure vision principle 9
    asks for.

    WHAT IT NEVER DOES: never completes a failed unit FORWARDS (apply already decided
    that unit fails and no `intent` row exists for those renames - J1); never resolves
    a row `reverted` unless the file physically went back; never touches a unit whose
    members are all `done`; never raises for a state it cannot heal.
    """
    for session_id, unit_key in repo.split_units(conn):
        rows = [
            row for row in repo.unit_rows(conn, session_id, unit_key) if row.state == "done"
        ]
        # Ascending journal id, which is the order they were renamed in; `revert_unit`
        # reverses it so the last file moved is the first put back.
        plan_rows = repo.plan_items_by_id(conn, [row.plan_item_id for row in rows])
        revertable = []
        for row in rows:
            if row.plan_item_id not in plan_rows:
                _strand(summary, logger, row.new_rel_path, "pam's records for it are damaged")
                continue
            # J11 layer 1, and the only check here that is about the CATALOG rather
            # than the disk. Another `files` row owning the old path cannot be
            # discovered after the fact: the rename would succeed, `set_file_path`
            # would fail, and the file would be back at a name the catalog says holds
            # something else. Holding the workspace lock makes this airtight for the
            # invocation. It does not replace the disk check - that still decides
            # whether the rename is possible; this decides whether pam can record it
            # honestly.
            owner = repo.path_owner(conn, row.old_rel_path)
            if owner is not None and owner != plan_rows[row.plan_item_id].file_id:
                _strand(
                    summary,
                    logger,
                    row.new_rel_path,
                    f"pam's records still show another file at {row.old_rel_path}",
                )
                summary.discrepancies.append(
                    f"pam's records still show a file at {row.old_rel_path}, so"
                    f" {row.new_rel_path} was left where it is. Run: pam scan"
                )
                continue
            revertable.append(row)
        if not revertable:
            continue
        txn = RenameTransaction.adopt(conn, revertable, logger=logger)
        stranded = revert_unit(
            txn,
            archive_root=archive_root,
            performed=list(txn.items),
            logger=logger,
            names=names,
        )
        # Reported in the order the work actually happened - newest first, the same
        # order `revert_unit` moved them in - so the summary and the session log read
        # as one account of one repair rather than two orderings of it.
        for item in reversed(txn.items):
            if item in stranded:
                _strand(summary, logger, item.new_rel_path, "pam could not put it back")
            else:
                summary.reverted.append(item.old_rel_path)
                summary.recovered_sessions.add(session_id)
        logger.warning(
            "recovery put a split unit back: session=%s unit=%s reverted=%s stranded=%s",
            session_id,
            unit_key,
            len(revertable) - len(stranded),
            len(stranded),
        )


def _strand(
    summary: RecoverySummary, logger: logging.Logger, new_rel_path: str, why: str
) -> None:
    """Name a member recovery left under its new name (spec 7.3: never guess).

    Its journal row stays `done`, which is the truth, and its unit stays split - so
    this is reported again on the next invocation, and the one after that, until a
    human resolves it. That repetition is intended (spec 7.0).
    """
    summary.stranded.append(new_rel_path)
    logger.error(
        "recovery left %s under its new name and its group split: %s", new_rel_path, why
    )


def _finalize_applying_plans(
    conn: sqlite3.Connection, summary: RecoverySummary, logger: logging.Logger
) -> None:
    """Phase 3 (J10): every plan left `applying`, and no other plan, ever.

    `applying` names a run in progress. Recovery holds the workspace lock and only one
    pam process may run against an archive, so a plan found in `applying` necessarily
    belongs to a process that is gone. Leaving it there strands the plan in a state no
    query owns and no check reports: `open_plan()` returns only `open` plans,
    `supersede_open_plans` touches only `open` ones, and spec 7.5's check 4 used to
    look only at `intent` rows and *applied* plans.
    """
    for plan_id in repo.plans_in_status(conn, STATUS_APPLYING):
        status = derive_plan_status(repo.plan_items(conn, plan_id))
        repo.set_plan_status(conn, plan_id, status)
        conn.commit()
        summary.finalized.append((plan_id, status))
        logger.warning(
            "recovery finalized plan %s left `applying` by an interrupted run: %s",
            plan_id,
            status,
        )
