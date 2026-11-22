"""pam apply orchestration: validate -> confirm -> record -> rename each unit -> finalize.

**Orchestration only** (spec 7.1 rev 7's normative decomposition). The filesystem
operation lives in `rename.py`, the journal state machine in `journal_ops.py`, plan
preflight in `validator.py`, and the report and its UX spec 4 copy in `reporter.py`
(Task 10). This module decides the ORDER those happen in and counts what happened.
It contains no `os.rename`, no `os.replace` and no `repo.resolve` call, and
`tests/test_apply.py` asserts all three against this file's source.

The order is the safety property, and every step of it is load-bearing:

  1. validate      - refuse a plan that cannot be applied, before anything is written
  2. record        - --all's approvals (if any), the refusals and the no-ops, in ONE
                     transaction, AFTER validation and AFTER the confirmation. The typed
                     `apply` confirms; it no longer approves (F1 R1) - apply renames
                     only what the user approved, and `proposed` is left as it is.
  3. 'applying'    - the plan says out loud that a run is in progress
  4. per unit      - AM2's probe of every member (a unit with one cloud-only NOW is
                     skipped whole), then intents committed, then renames, then
                     per-item resolutions
  5. finalize      - 'applied' or 'partially_applied'

spec 7.0 J1 is why step 4 can never be reordered: `RenameTransaction.begin()` commits
the intent rows, and only then may `perform_verified_rename` touch the disk. spec 7.0
J5 is why the resolutions are per item and never batched at the end of the run.
"""

import hashlib
import json
import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import click

from pam.catalog import repo
from pam.catalog.db import migrate, open_catalog
from pam.errors import PamError
from pam.execute.recover import (
    STATUS_APPLYING,
    RecoverySummary,
    derive_plan_status,
    run_recovery,
)
from pam.execute.rename import DirectoryNames
from pam.execute.reporter import (
    Confirmation,
    announce_interrupted,
    progress_line,
    write_session_report,
)
from pam.execute.reporter import done_lines as done_lines
from pam.execute.unit import UnitResult, execute_unit
from pam.execute.validator import PlanPreflight, validate_plan
from pam.faults import maybe_fault
from pam.lock import HeldWorkspace, WorkspaceLock
from pam.paths import resolve_archive, workspace_if_exists
from pam.scan.walker import detect_sync_root, placeholder_now
from pam.sessionlog import close_session_log, open_session_log

# What `pam apply` asks before it writes anything. `None` means "do not ask", which is
# what the ~40 tests driving `run_apply` directly need; the CLI - the one caller a human
# reaches - always passes the real one, and a test asserts that structurally.
ConfirmFn = Callable[[Confirmation], bool]

# AM2 (M15 Task 2): the reason a unit's items are `failed` when a member was cloud-only at the
# moment of its rename. Logged, never printed: the person reads AM2's sentence instead
# (`reporter.cloud_only_lines`), and `plan_items` has no reason column.
CLOUD_ONLY_NOW = "cloud_only_now"
# `done_lines` (imported above) is reached through this module by `webui/rename_jobs.py`, U19's
# one door, as `write_session_report` is.


class _Clock:
    """How long the run took, EXCLUDING the seconds the user spends at UX 4's prompt.

    HT-M5's one finding. `started` used to be a plain local taken at the top of
    `run_apply`, and the typed confirmation runs inside that window - so a real run
    printed `Done. N renamed, 0 failed, in 11s` for renaming the session log timed at
    about a second. The other ~10 s was a person reading the block.
    UX 4's closing line is normative copy and "in 11s" is not true of the renaming.

    Restarted, never subtracted: subtracting a prompt duration would mean measuring the
    prompt, and there is nothing else in the run to exclude.

    What it still INCLUDES is deliberate: spec 7.2's recovery pass, the preflight and
    every rename happen while the user waits, and they are pam's own work. Timing only
    the renames and calling that the session would be the same dishonesty pointing the
    other way.

    What it EXCLUDES, as of M6 Task 6, is writing the session report and closing the
    log. Not because those are not pam's work, but because the report is one of the two
    places this number is printed: a duration that included the writing of the file it
    is written into cannot be measured before that file is written, which is why the
    report said `took 0.0s` for every session pam had ever applied. One session, one
    number, read the same in the report and on the screen - and the report is the
    artifact a user forwards when something went wrong, so the two must not disagree.
    """

    def __init__(self) -> None:
        self.started = time.monotonic()

    def restart(self) -> None:
        self.started = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self.started


@dataclass
class ApplySummary:
    """What one `pam apply` invocation did. Task 10 renders it (UX spec 4)."""

    session_id: str = ""
    renamed: int = 0
    # F1 R4/R5: how many items this run found approved (after `--all`'s choices) and how
    # many it left undecided. `not_approved` is the frozen interface field; `approved` is
    # what `reporter.LEFT_LINE` prints beside it ("k approved, m not approved ...").
    approved: int = 0
    not_approved: int = 0
    failed: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    # Files a failed revert left under their new names while their unit failed
    # (spec 7.3). Empty on every path except external interference during a run, and
    # the one thing in this summary that needs a human rather than a re-run.
    stranded: list[str] = field(default_factory=list)
    # What spec 7.2's recovery pass found on the way in and could not decide about.
    # It belongs in THIS session's report (plan clarification 13): recovery runs at the
    # start of `pam apply`, and otherwise the only record is a log line the user has no
    # reason to open.
    recovery_notes: list[str] = field(default_factory=list)
    # True when the user typed anything other than `apply` at UX 4's confirmation.
    # Not an error: nothing was written and the exit code is 0.
    cancelled: bool = False
    elapsed_s: float = 0.0
    report_path: Path | None = None
    log_path: Path | None = None
    # The three facts M6 Task 6 adds to the session report, so that a report read six
    # months later says WHICH folder, WHEN and from which plan. None of them can be
    # recovered from the file itself afterwards: the report lives in a workspace whose
    # directory name is a one-way hash of the archive path (spec 3).
    archive_root: Path | None = None
    plan_id: int = 0
    started_at: str = ""
    # AM2 (M15 Task 2): units skipped whole because a member was cloud-only at the moment of
    # their rename, and the FILES in them - every member of the unit's frozen membership, no-op
    # members included. Neither is in `failed`: the skip is not a per-file error, it is a
    # route (make the files available offline, read the folder again).
    cloud_only_files: int = 0
    cloud_only_units: int = 0

    @property
    def had_errors(self) -> bool:
        """spec 13 rev 6: a command that recorded per-file errors exits 1, not 0."""
        return self.failed > 0

    def record_failure(self, rel_path: str, reason: str) -> None:
        """The single entry point, so the count and the detail can never disagree."""
        self.failed += 1
        self.failures.append((rel_path, reason))


def run_apply(
    plan_id: int,
    archive: Path | None = None,
    *,
    confirm: ConfirmFn | None = None,
    accept_all: bool = False,
) -> ApplySummary:
    """Apply one plan to one archive. THE mutating path in pam.

    `archive` follows spec 3's resolution: the given folder, else the single known
    archive, else ask. The workspace must already exist - only `pam scan` may create
    one - so a typo'd path is a refusal, never a silent "nothing to do".

    `confirm` is UX 4's typed confirmation, and it runs INSIDE the workspace lock and
    after the preflight: inside, so nothing can change between the counts the user
    reads and the renames pam performs; after, so pam never asks about a plan it would
    have refused anyway. `None` skips it, which is what tests driving this directly
    need - `pam apply` itself always passes one.

    `accept_all` is `pam apply --all` (F1 R4): accept every undecided name except
    low-confidence ones (`naming/accept.py`), written AFTER the confirmation.
    """
    clock = _Clock()
    root = resolve_archive(archive)
    ws = workspace_if_exists(root)
    if ws is None:
        raise PamError(f"pam has not scanned {root} yet. Run: pam scan {root}")

    summary = ApplySummary(archive_root=root, plan_id=plan_id)
    with WorkspaceLock(ws.lock_path, ws) as held:
        logger, log_path = open_session_log(ws.logs_dir, suffix="apply")
        summary.log_path = log_path
        try:
            conn = open_catalog(ws.db_path)
            try:
                migrate(conn)
                apply_locked(
                    held, conn, root, plan_id, summary, logger, confirm, clock, accept_all
                )
                # EXACTLY ONE assignment, here: the run is over and the report is not
                # written yet. It used to be taken after the `with` block, which is
                # after `write_session_report` has already read `elapsed_s` - so every
                # report pam has ever written says `took 0.0s`. See `_Clock`.
                summary.elapsed_s = clock.elapsed()
                if not summary.cancelled:
                    summary.report_path = write_session_report(ws.reports_dir, summary, logger)
            finally:
                conn.close()
        except BaseException:
            # spec 13: EVERY session ends with the log path on stderr - and this is
            # the case where it matters most. pam/cli.py's `except Exception` prints
            # a traceback with no log path at all, so mid-apply on an archive of real files the
            # user would be left with no log to read and no session id to hand to
            # `pam undo`. Task 10 owns the CLI, so apply is self-sufficient instead.
            # This is NOT a durability mechanism (a `finally` is exactly what a power
            # cut skips): the session id is durable in the journal rows themselves,
            # and the log path was logged at the start of the run.
            announce_interrupted(summary.session_id, summary.log_path)
            raise
        finally:
            close_session_log(logger)
    return summary


def apply_locked(
    held: HeldWorkspace,
    conn: sqlite3.Connection,
    root: Path,
    plan_id: int,
    summary: ApplySummary,
    logger: logging.Logger,
    confirm: ConfirmFn | None,
    clock: _Clock,
    accept_all: bool,
    progress: Callable[[int, int], None] | None = None,
) -> None:
    """The apply's body, under a lock the caller proves it holds (M14 Task 6, Step 1).

    `progress(done, total)` (the controller's ruling R6-1, M14 Task 6) is told the files
    finished after each unit, of the files the preflight made actionable - the page's
    `Renaming… 312 of 1,204`. None, the CLI's value, reports nothing.

    `run_apply`'s old `_apply`, renamed and given `held` - the order below is unchanged. `held`
    must name `root`'s workspace and still be held, or this raises AssertionError before
    recovery, the preflight or the confirmation run. The caller owns the lock, the connection,
    the session log and the report; the review page's job thread is its second caller (U19).
    """
    held.require(workspace_if_exists(root))
    # spec 7.2: recovery runs INSIDE the workspace lock and BEFORE the command's own
    # work. Before validation specifically, because recovery finalizes a plan left
    # `applying` by an interrupted run - and a plan apply is about to refuse for that
    # exact reason must be given its terminal status first, or the refusal names a
    # state no process is any longer in.
    summary.recovery_notes = _recovery_notes(run_recovery(conn, root, logger))
    preflight = validate_plan(conn, plan_id, root=root, accept_all=accept_all)
    summary.approved = sum(1 for item in preflight.items if item.status == "approved")
    summary.not_approved = _not_approved(preflight)
    # UX 4's one dangerous moment. AFTER the preflight, so pam never asks about a plan
    # it would refuse; BEFORE anything is written, so cancelling leaves the catalog and
    # the archive exactly as they were.
    if confirm is not None:
        if not confirm(_confirmation(conn, root, plan_id, preflight)):
            summary.cancelled = True
            logger.info("apply cancelled at the confirmation: plan=%s", plan_id)
            return
        # HT-M5's finding: the seconds the user spent reading UX 4's block are not work
        # pam did, and `elapsed_s` is rendered as "in Ns" next to the rename counts.
        # Inside the `is not None` branch on purpose - `confirm=None` is the ~40 tests
        # and the benchmark, and there was never a wait to exclude on that path.
        clock.restart()
    _record_preflight(conn, preflight, summary, logger)

    repo.set_plan_status(conn, plan_id, STATUS_APPLYING)
    conn.commit()

    # Allocated HERE, immediately before the first intent it will be written into.
    # `next_session_id` derives uniqueness from journal rows alone, so an id held
    # across a long preflight can be handed out again by a concurrent-in-time run
    # that journals first - and two runs sharing an id merge into one undoable
    # session.
    # One timestamp for both, so "when did this run" and the session id can never
    # disagree by the second they were each taken in.
    now = _now()
    summary.started_at = now
    summary.session_id = repo.next_session_id(conn, now)
    logger.info(
        "apply start: plan=%s session=%s archive=%s log=%s",
        plan_id,
        summary.session_id,
        root,
        summary.log_path,
    )

    files_by_id = {row.id: row for row in repo.all_files(conn).values()}
    # X4: ONE per run, so a folder is listed once and not once per rename. Built here,
    # after recovery, which builds its own and never shares one with this run.
    names = DirectoryNames()
    units = _units_of(preflight)
    members = _members_by_unit(preflight)
    total = sum(len(unit) for unit in units)
    finished = 0
    for unit in units:
        whole = members[unit[0].unit_key]
        # AM2: BEFORE the unit's first intent row, every member of its COMPLETE frozen
        # membership - no-op members included, which `unit` does not hold. A stat, never a
        # read: `placeholder_now` reads a directory entry and hydrates nothing (vision R2).
        if any(placeholder_now(root / member.old_rel_path) for member in whole):
            _skip_cloud_only(conn, unit, whole, summary, logger)
        else:
            _fold(
                summary,
                execute_unit(
                    conn,
                    archive_root=root,
                    session_id=summary.session_id,
                    items=unit,
                    files_by_id=files_by_id,
                    logger=logger,
                    names=names,
                ),
            )
        finished += len(unit)
        if progress is not None:
            progress(finished, total)
    _finalize_plan(conn, plan_id, summary, logger)


def confirmation_for(conn: sqlite3.Connection, root: Path, plan_id: int) -> Confirmation:
    """UX 4's facts for `plan_id` NOW, as `apply_locked` would show them. WRITES NOTHING.

    M14 Task 6 (U6, U14): the review page's Confirm screen. `validate_plan` reads and never
    writes (its own docstring, and nothing below it writes); a whole-plan refusal raises its
    `PamError`, exactly as `pam apply` would refuse. No recovery runs here - recovery writes,
    and the page's server healed on bind and heals a dead job before any request.
    """
    return _confirmation(conn, root, plan_id, validate_plan(conn, plan_id, root=root))


def actionable_digest(items: list[repo.PlanItemRow]) -> str:
    """sha256 of the sorted `(item id, new filename)` of the items a run will rename (U14).

    The page's nonce is issued for it and the job's confirmation recomputes it inside the
    preflight: a name approved, skipped or edited in between changes it, and the run is
    cancelled before anything is written.
    """
    pairs = sorted((item.id, item.new_filename) for item in items)
    return hashlib.sha256(json.dumps(pairs).encode("utf-8")).hexdigest()


def _confirmation(
    conn: sqlite3.Connection, root: Path, plan_id: int, preflight: PlanPreflight
) -> Confirmation:
    """UX 4's counts, from the preflight rather than from the plan as it was written.

    `low_confidence` counts only the items pam is actually about to rename: warning
    about a low-tier item that preflight already refused would ask the user to worry
    about a file nothing is going to touch.
    """
    actionable = preflight.actionable()
    return Confirmation(
        plan_id=plan_id,
        # Exactly what `resolve_archive` returned - never shortened, never relativized
        # (UX rev 4, finding F4). It is the fact the user must check first.
        archive_root=root,
        # F1 R5: approved items only - the ones pam is about to rename.
        approved=len(actionable),
        proposed=len(preflight.items),
        not_approved=_not_approved(preflight),
        excluded_low=preflight.excluded_low,
        skipped_by_you=sum(1 for item in preflight.items if item.status == "skipped"),
        low_confidence=sum(1 for item in actionable if item.tier == "low"),
        sync_provider=detect_sync_root(root),
        # THE NO-OP ITEMS ARE COUNTED HERE, not in `approved` (spec 7.0 J12, spec 6.2
        # rule 7). `approved` reads "N renames approved" and the user types `apply`
        # against it at the one dangerous moment, so a count including files nothing
        # will touch misleads exactly where it costs most. "already conforming" is the
        # phrase UX 4 already uses and it is literally true of a no-op item's file: it
        # has the name the plan proposes. No new line and no new copy - the plan-time
        # figure counts the files that got no item, and this adds the files that got one
        # only because spec 7.3 rev 16 keeps their unit whole.
        already_conforming=repo.plan_already_conforming(conn, plan_id) + len(preflight.noops()),
        digest=actionable_digest(actionable),
    )


def _not_approved(preflight: PlanPreflight) -> int:
    """F1 R5: the items left UNDECIDED - `proposed` after `--all`'s in-memory choices.

    A skip is a decision and is counted on its own line ("skipped by you"), never here.
    """
    return sum(1 for item in preflight.items if item.status == "proposed")


def _recovery_notes(recovered: RecoverySummary) -> list[str]:
    """What spec 7.2 left for a human, in sentences the session report can print.

    Only the two categories that need a person: a rename recovery could not decide
    about, and a member it could not put back. Rows it resolved cleanly are the
    protocol working and belong in the log, not in a report the user reads when
    something has gone wrong.
    """
    notes = [
        f"{rel_path} - pam could not tell whether an interrupted rename happened."
        " Look at both names, keep the file you want, then run: pam scan"
        for rel_path in recovered.needs_attention
    ]
    notes += [
        f"{rel_path} - left under its new name while the group it belongs to is not."
        " Nothing was lost."
        for rel_path in recovered.stranded
    ]
    notes += recovered.discrepancies
    return notes


def _record_preflight(
    conn: sqlite3.Connection,
    preflight: PlanPreflight,
    summary: ApplySummary,
    logger: logging.Logger,
) -> None:
    """`--all`'s approvals, the preflight refusals and the no-ops, in ONE transaction.

    The typed `apply` confirms; it no longer approves (F1 R1). Until M13 this function
    flipped every `proposed` item to `approved` (M5 plan clarification 2), so a user who
    approved 5 names in the review page watched 399 change (HT-M12 F1). It now writes
    `approved` for exactly `preflight.accepted` - empty unless `--all` - and nothing else.
    `--all`'s approvals are written here, after the confirmation and in the refusals'
    transaction, so a cancelled `--all` leaves no approval nobody confirmed.

    A refused item is marked `failed` here and NEVER journaled: spec 7.0 J1 makes a
    journal row a promise that pam is about to move a file, and these files are not
    touched. spec 7.1 has the same shape for its own verify step ("mismatch -> item
    'failed', reason recorded, continue").
    """
    for item_id in sorted(preflight.accepted):
        repo.set_plan_item_status(conn, item_id, "approved")
    for item in preflight.items:
        reason = preflight.refusals.get(item.id)
        if reason is None:
            continue
        repo.set_plan_item_status(conn, item.id, "failed")
        summary.record_failure(item.old_rel_path, reason)
        logger.warning("preflight refused: %s", reason)
    # spec 7.0 J12: a no-op item is resolved HERE, at preflight, with NO journal row and
    # no `os.rename`. J1 is not weakened - a journal row is a promise pam is about to
    # move a file, and this file is not moved. `applied` is its terminal status because
    # the file has the name the plan proposes for it; spec 4's six `plan_items.status`
    # values gain none (a seventh would be a migration, and no consumer would know it).
    # It contributes to neither `renamed` nor `failed`, which is why nothing is counted
    # here: J12 says so in as many words.
    for item in preflight.noops():
        repo.set_plan_item_status(conn, item.id, "applied")
        logger.info(
            "no-op item resolved without a rename: %s already has the name the plan"
            " proposes for it (spec 7.0 J12)",
            item.old_rel_path,
        )
    conn.commit()


def _units_of(preflight: PlanPreflight) -> list[list[repo.PlanItemRow]]:
    """The actionable items grouped by `plan_items.unit_key` (spec 7.3 rev 6).

    NEVER by `files.companion_group_id`: `pam scan` clears and recreates those groups
    on every scan, so a scan between plan and apply would regroup the files and split
    a group across two stems (vision S3) - the bug this milestone exists to prevent.
    `unit_key` is the grouping frozen into the plan at plan time.

    Groups keep the order of their first item, so a plan applies in plan order and a
    session log reads top to bottom against it.
    """
    units: dict[str, list[repo.PlanItemRow]] = {}
    for item in preflight.actionable():
        units.setdefault(item.unit_key, []).append(item)
    return list(units.values())


def _members_by_unit(preflight: PlanPreflight) -> dict[str, list[repo.PlanItemRow]]:
    """EVERY item of the plan, grouped by `unit_key`: a unit's complete frozen membership.

    AM2's probe needs what `_units_of` deliberately drops - the no-op members (J12), which
    `actionable()` excludes. A unit whose only cloud-only member is its no-op is still a unit
    with a member cloud-only now, and renaming the rest would ask the sync client to replay a
    change to a group one of whose files is not on this device (`m14.md` 2.3, Q8-D1).
    """
    members: dict[str, list[repo.PlanItemRow]] = {}
    for item in preflight.items:
        members.setdefault(item.unit_key, []).append(item)
    return members


def _skip_cloud_only(
    conn: sqlite3.Connection,
    unit: list[repo.PlanItemRow],
    whole: list[repo.PlanItemRow],
    summary: ApplySummary,
    logger: logging.Logger,
) -> None:
    """AM2: a unit with a member cloud-only NOW is skipped whole - no journal row, no rename.

    Its items are `failed` (reason `cloud_only_now`), not left `approved`: `validate_plan`
    refuses any plan that is not `open`, and the plan ends `partially_applied`, which design
    7.2 makes terminal - so `approved` would promise a retry nothing can perform. The retry is
    the route AM2's sentence names: make the files available offline, read the folder again,
    review, rename. A no-op member keeps the `applied` J12 gave it at the preflight; its file
    has the name the plan proposes. The FILES counted are the whole unit's.
    """
    for item in unit:
        repo.set_plan_item_status(conn, item.id, "failed")
    conn.commit()
    summary.cloud_only_files += len(whole)
    summary.cloud_only_units += 1
    logger.warning(
        "unit not renamed (%s): a member is cloud-only now: %s",
        CLOUD_ONLY_NOW,
        ", ".join(member.old_rel_path for member in whole),
    )


def _fold(summary: ApplySummary, result: UnitResult) -> None:
    """One unit's outcome into the run's totals.

    Folded through `record_failure` rather than assigned, so the count and the detail cannot
    disagree - `unit.py` owns the unit, this owns the run's totals. UX 4's per-1000 progress:
    nothing is printed for a small archive, which is every archive in the test suite and a
    human test's library - it is for the 98,210-file case the UX spec is written against.
    """
    line = progress_line(summary.renamed, summary.renamed + result.renamed)
    if line is not None:
        click.echo(line)
    summary.renamed += result.renamed
    for rel_path, reason in result.failures:
        summary.record_failure(rel_path, reason)
    summary.stranded.extend(result.stranded)


def _finalize_plan(
    conn: sqlite3.Connection,
    plan_id: int,
    summary: ApplySummary,
    logger: logging.Logger,
) -> None:
    """spec 7.1: end of session, the plan is `applied` (all) or `partially_applied`.

    `before_plan_finalize` (spec 9.3 rev 7) sits immediately before the write. A
    crash here interrupts NOTHING - every rename succeeded and every journal row is
    resolved - so spec 7.2's recovery must conclude there is nothing to do. It is the
    checkpoint that catches a recovery pass eager enough to undo completed work.

    The status comes from `recover.derive_plan_status`, reading the ITEM statuses,
    rather than from this run's counters. spec 7.2 phase 3 finalizes plans this run
    never got to finish, and it has no counters - so a plan closed by apply and one
    closed by recovery could otherwise disagree about what `applied` means. The sharing
    goes this way round because only one direction is possible: apply imports recovery
    (it runs recovery before its own work), so recovery cannot import apply.
    """
    maybe_fault("before_plan_finalize")
    status = derive_plan_status(repo.plan_items(conn, plan_id))
    repo.set_plan_status(conn, plan_id, status)
    conn.commit()
    logger.info(
        "apply done: plan=%s session=%s status=%s renamed=%s failed=%s cloud_only=%s/%s",
        plan_id,
        summary.session_id,
        status,
        summary.renamed,
        summary.failed,
        summary.cloud_only_files,
        summary.cloud_only_units,
    )


def _now() -> str:
    """The project's timestamp format, matching every other `now=` in the catalog."""
    return datetime.now().isoformat(timespec="seconds")
