"""Companion-unit sequencing and revert (spec 7.3, spec 7.0 J6).

A companion unit is a set of `plan_items` sharing a `unit_key` - the grouping FROZEN
into the plan at plan time (spec 6.3 rule 6), never the live
`files.companion_group_id`, which `pam scan` clears and recreates on every scan. The
guarantee this module owns is vision S3: **a group is never left split across two
stems**. Either every member ends up under the new stem, or every member is back
where it started.

Its own module rather than part of `apply.py` (spec 7.1 rev 7's normative
decomposition) because three callers need it: apply, spec 7.4's undo, and spec 7.2's
unit-consistency pass. It therefore knows nothing about `pam apply` - it returns a
`UnitResult` the caller folds into whatever it is counting, instead of writing into
an apply-shaped summary. That is not only tidiness: `ApplySummary` lives in
`apply.py`, so taking one as a parameter would make `apply -> unit -> apply` a hard
import cycle.

WHAT MAKES THE ORDER SAFE. `RenameTransaction.begin()` commits every member's intent
before any file moves (spec 7.0 J1), so a crash at any point leaves a journal row
spec 7.2 can branch on. The renames then run member by member, each resolved in its
own transaction (spec 7.0 J5). When one fails, the members already renamed are put
BACK through `perform_verified_rename` - the same single verified rename apply, undo
and recovery all use - and resolved `done -> reverted`, which spec 7.0 J2 allows.
"""

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from pam.catalog import repo
from pam.execute.journal_ops import RenameItem, RenameTransaction
from pam.execute.rename import DirectoryNames, perform_verified_rename
from pam.execute.validator import COMPANION_REASON, new_rel_path
from pam.faults import maybe_fault

# A file the catalog no longer describes where the plan said it was. Not a disk
# check - that is `perform_verified_rename`'s job against the real file - but a
# check that the plan and the catalog still agree about which path this item is
# about. If they disagree, the (size, mtime_ns) snapshot would be verified against
# a path the item was not written for.
MOVED_SINCE_PLAN_REASON = (
    "{old_rel_path} was left alone: pam's records now show that file somewhere else,"
    " so the plan no longer describes it. Run: pam scan and pam plan again."
)

# spec 7.3: when one member of a companion unit fails, the rest are not attempted.
#
# Deliberately the SAME sentence the validator uses for its own companion refusals,
# imported rather than repeated: the user cannot tell which layer refused, and two
# copies of one user-facing string drift.
UNIT_ALREADY_FAILED_REASON = COMPANION_REASON

# A member that was renamed and then put back, because a later member of its unit
# failed (spec 7.3). Its own sentence rather than UNIT_ALREADY_FAILED_REASON's,
# because the two describe different histories and only this one had its file moved
# and moved back - which is what the user sees in a folder they are watching.
UNIT_REVERTED_REASON = (
    "{old_rel_path} was renamed and then put back, because another file it is grouped"
    " with could not be renamed and pam renames a group together or not at all."
    " Run: pam scan and pam plan again."
)

# The rare one: the rename was performed, a later member failed, and putting this file
# back did not work either - so it is sitting under its NEW name while its group is
# not. spec 7.0's "states pam cannot produce" paragraph is why this is reported and
# never retried or guessed at: reaching it means something outside pam wrote to the
# archive while pam was working, and pam's job then is to say exactly what it sees.
UNIT_STRANDED_REASON = (
    "{old_rel_path} is now named {new_rel_path} and pam could not put it back after"
    " another file in its group could not be renamed. Nothing was lost - the file is"
    " in the same folder under the new name. Run: pam scan and pam plan again."
)


# spec 7.4's mirror of the three sentences above. A companion unit is undone together
# or not at all, exactly as it is applied, so the same three outcomes exist - but the
# file the user is watching moves the other way, and telling them a file "was renamed
# and then put back" when what happened is that it went back to its original name and
# was then restored to the new one would be false.
UNDO_ALREADY_FAILED_REASON = (
    "{old_rel_path} was left alone: another file it is grouped with could not be put"
    " back, and pam undoes a group together or not at all."
)

UNDO_REVERTED_REASON = (
    "{old_rel_path} was put back and then renamed again, because another file it is"
    " grouped with could not be put back and pam undoes a group together or not at all."
)

UNDO_STRANDED_REASON = (
    "{new_rel_path} was put back but pam could not undo that after another file in its"
    " group could not be put back. Nothing was lost - the file is in the same folder"
    " under its original name. Run: pam scan."
)


@dataclass(frozen=True)
class UnitReasons:
    """The three sentences one operation needs for a unit's three outcomes.

    A frozen record with named fields rather than three loose arguments, and both sets
    defined side by side above, because the seam between apply and undo is exactly
    where two vocabularies for one outcome would grow: nothing stops a second caller
    inventing a fourth phrase for "the group failed, so this member was not attempted",
    except that this type says there are three outcomes and names them.

    They are per-operation and NOT shared, deliberately. The outcomes are the same
    three; the sentences cannot be, because the file moves the other way and the user
    reads them while looking at their own folder.
    """

    already_failed: str
    reverted: str
    stranded: str


APPLY_REASONS = UnitReasons(
    already_failed=UNIT_ALREADY_FAILED_REASON,
    reverted=UNIT_REVERTED_REASON,
    stranded=UNIT_STRANDED_REASON,
)

UNDO_REASONS = UnitReasons(
    already_failed=UNDO_ALREADY_FAILED_REASON,
    reverted=UNDO_REVERTED_REASON,
    stranded=UNDO_STRANDED_REASON,
)


@dataclass
class UnitResult:
    """What one unit did, in terms no caller-specific type appears in.

    `renamed` is only ever 0 or the whole unit's size: spec 7.3 has no partial
    success. `failures` are (rel_path, sentence) pairs for the session report, and
    `stranded` names the files a failed revert left under their new names - the
    report must name them exactly (spec 7.3: never guess, never retry silently).
    """

    renamed: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    stranded: list[str] = field(default_factory=list)


def execute_rename_unit(
    conn: sqlite3.Connection,
    *,
    archive_root: Path,
    session_id: str,
    op: str,
    unit_key: str,
    rename_items: list[RenameItem],
    reasons: UnitReasons,
    logger: logging.Logger,
    names: DirectoryNames | None = None,
) -> UnitResult:
    """THE sequencing loop: intents committed together, then renames member by member.

    Shared by `pam apply` (through `execute_unit` below) and spec 7.4's undo, which
    differ only in where their `RenameItem`s come from. **The M5 plan says undo goes
    "through the same `execute_unit`"; that is wrong and was proved wrong** - driven
    with exactly what an undo has, `execute_unit` renames nothing and rewrites a
    correctly applied plan's items from `applied` to `failed`, because its
    `MOVED_SINCE_PLAN_REASON` guard compares `files.rel_path` against the item's
    ORIGINAL path, which after an apply is never where the file is. Its `op` is
    hardcoded, its direction comes from `plan_items.new_filename`, and its (size,
    mtime_ns) come from `files` rather than from the journal. Four of those five are
    load-bearing.

    What the plan MEANT - "no second implementation" - is what this function is. One
    sequencing loop, one `revert_unit`, one `perform_verified_rename`; two builders,
    because the inputs genuinely differ. The alternative, letting undo keep its own
    loop, is the third implementation of "check, then move an irreplaceable file" that
    spec 7.1 rev 7's decomposition exists to prevent.

    `op` selects `journal_ops`' status maps, so passing the wrong one writes a
    plan_item status the disk contradicts. `reasons` keeps the two callers' sentences
    from drifting into two vocabularies for one outcome. `names` is the run's
    `DirectoryNames` (X4), handed to every rename and to `revert_unit`; `None` lists the
    folder per rename, as before X4.

    Returns a `UnitResult`; raises only for conditions that are pam's own fault (an
    illegal journal transition) or the caller's crash checkpoints. A file that cannot
    be renamed is an OUTCOME, recorded and counted (vision principle 9).
    """
    result = UnitResult()
    txn = RenameTransaction(
        conn, session_id=session_id, op=op, items=rename_items, logger=logger
    )
    txn.begin()  # spec 7.0 J1: the intents are COMMITTED before any file moves.

    performed: list[RenameItem] = []
    for index, rename_item in enumerate(rename_items):
        if index:
            # spec 9.3: between members, with an earlier member already renamed and
            # resolved and a later one still in `intent` - the mixed unit spec 7.3
            # hands to spec 7.2's unit-consistency pass.
            maybe_fault("mid_companion_unit")
        outcome = perform_verified_rename(
            archive_root=archive_root,
            old_rel=rename_item.old_rel_path,
            new_rel=rename_item.new_rel_path,
            expect_size=rename_item.size,
            expect_mtime_ns=rename_item.mtime_ns,
            logger=logger,
            names=names,
        )
        if outcome.ok:
            # spec 7.0 J5: journal row, item status and files row - one transaction,
            # per item. Never batched to the end of the run.
            txn.commit(rename_item)
            performed.append(rename_item)
            continue

        reason = outcome.reason or "pam could not rename this file."
        txn.fail(rename_item, reason)
        result.failures.append((rename_item.old_rel_path, reason))
        for later in rename_items[index + 1 :]:
            skipped = reasons.already_failed.format(old_rel_path=later.old_rel_path)
            txn.fail(later, skipped)
            result.failures.append((later.old_rel_path, skipped))
        _record_reverts(
            revert_unit(
                txn, archive_root=archive_root, performed=performed, logger=logger, names=names
            ),
            performed=performed,
            unit_key=unit_key,
            reasons=reasons,
            result=result,
            logger=logger,
        )
        return result

    result.renamed = len(rename_items)
    return result


def execute_unit(
    conn: sqlite3.Connection,
    *,
    archive_root: Path,
    session_id: str,
    items: list[repo.PlanItemRow],
    files_by_id: dict[int, repo.FileRow],
    logger: logging.Logger,
    names: DirectoryNames | None = None,
) -> UnitResult:
    """`pam apply`'s builder: plan items and `files` rows -> `RenameItem`s, then run.

    Everything here is apply-shaped and NOT shareable, which is the whole finding
    behind `execute_rename_unit`'s docstring: the forward direction comes from
    `plan_items.new_filename`, the (size, mtime_ns) snapshot from the `files` row the
    plan was built against, and the precondition below asks whether the catalog still
    agrees with the plan about where this file is - true before an apply, false after
    one, and undo runs after one.

    Returns a `UnitResult`; raises only for conditions that are pam's own fault (an
    illegal journal transition) or the caller's crash checkpoints. A file that cannot
    be renamed is an OUTCOME, recorded and counted (vision principle 9).
    """
    result = UnitResult()
    rename_items: list[RenameItem] = []
    for item in items:
        file_row = files_by_id.get(item.file_id)
        if file_row is None or file_row.rel_path != item.old_rel_path:
            _fail_unit_before_any_rename(
                conn,
                items=items,
                first=item,
                first_reason=MOVED_SINCE_PLAN_REASON.format(old_rel_path=item.old_rel_path),
                result=result,
                logger=logger,
            )
            return result
        rename_items.append(
            RenameItem(
                plan_item_id=item.id,
                file_id=item.file_id,
                old_rel_path=item.old_rel_path,
                new_rel_path=new_rel_path(item),
                # The snapshot the plan was built from. `perform_verified_rename`
                # compares it against a fresh os.stat of the real file, so a file
                # edited since the scan is left alone (spec 7.1 step 1).
                size=file_row.size,
                mtime_ns=file_row.mtime_ns,
            )
        )

    return execute_rename_unit(
        conn,
        archive_root=archive_root,
        session_id=session_id,
        op="rename",
        unit_key=items[0].unit_key,
        rename_items=rename_items,
        reasons=APPLY_REASONS,
        logger=logger,
        names=names,
    )


def revert_unit(
    txn: RenameTransaction,
    *,
    archive_root: Path,
    performed: list[RenameItem],
    logger: logging.Logger,
    names: DirectoryNames | None = None,
) -> list[RenameItem]:
    """Rename `performed` back, newest first. Returns the ones still at their new name.

    Best effort, in REVERSE order: the last file moved is the first put back, so the
    archive walks backwards through exactly the states it walked forwards through and
    a member is never briefly absent from both of its names.

    Each file that goes back is resolved `done -> reverted` (spec 7.0 J2) in the same
    transaction that set its `files` row back, so the catalog never disagrees with the
    disk. A file that will NOT go back is left `done` - which is the truth, spec 7.0
    J4: pam did perform that rename. It is returned to the caller so the report can
    name it; it is never retried and never guessed at (spec 7.3).

    THE SHARED-CALLER TENSION, stated rather than designed around. The plan shares
    this function with spec 7.2's unit-consistency pass (M5 Task 8), which reverts
    rows already `done` from a PREVIOUS invocation and so cannot hold the
    `RenameTransaction` that wrote them. Apply and spec 7.4's undo both can, and both
    exist today. Inventing a parameter for Task 8's shape before Task 8 is written
    would be speculation; the honest version is this one, and Task 8 decides then
    whether it can hold a transaction or whether this grows a second entry point.
    """
    stranded: list[RenameItem] = []
    for rename_item in reversed(performed):
        # spec 9.3 rev 8. UNCONDITIONAL, unlike the forward loop's `if index:` - what
        # precedes the first revert is a rename FAILURE, not a checkpoint, so there is
        # no equivalent of `after_intent` to reach that moment by. `:1` interrupts
        # before any member goes back, `:k` with k-1 already back.
        #
        # It is the only checkpoint that reaches spec 7.2's unit-consistency pass with
        # the `intent` loop having NOTHING to do: every other one leaves an `intent`
        # row behind, so every other scenario can be passed by a recovery whose
        # consistency pass is entangled with its intent loop. This one cannot.
        maybe_fault("mid_unit_revert")
        outcome = perform_verified_rename(
            archive_root=archive_root,
            old_rel=rename_item.new_rel_path,
            new_rel=rename_item.old_rel_path,
            # A rename changes neither size nor mtime, so the plan's snapshot still
            # describes the file under its new name. Verifying against it means a
            # file something else edited in the meantime is left alone rather than
            # moved on top of whatever is now at the old name.
            expect_size=rename_item.size,
            expect_mtime_ns=rename_item.mtime_ns,
            logger=logger,
            names=names,
        )
        if outcome.ok:
            txn.revert(rename_item)
            continue
        logger.error(
            "could not put %s back to %s: %s",
            rename_item.new_rel_path,
            rename_item.old_rel_path,
            outcome.reason,
        )
        stranded.append(rename_item)
    return stranded


def _record_reverts(
    stranded: list[RenameItem],
    *,
    performed: list[RenameItem],
    unit_key: str,
    reasons: UnitReasons,
    result: UnitResult,
    logger: logging.Logger,
) -> None:
    """Turn the revert's outcome into report lines (spec 7.3: name the exact files)."""
    for rename_item in performed:
        if rename_item in stranded:
            result.failures.append(
                (
                    rename_item.old_rel_path,
                    reasons.stranded.format(
                        old_rel_path=rename_item.old_rel_path,
                        new_rel_path=rename_item.new_rel_path,
                    ),
                )
            )
            result.stranded.append(rename_item.new_rel_path)
        else:
            result.failures.append(
                (
                    rename_item.old_rel_path,
                    reasons.reverted.format(old_rel_path=rename_item.old_rel_path),
                )
            )
    if stranded:
        # Vision principle 9: a half-done unit is reported, never hidden. This is the
        # only path that can leave one, and it needs a human.
        logger.error(
            "unit %s is split: %s could not be put back",
            unit_key,
            [i.new_rel_path for i in stranded],
        )


def _fail_unit_before_any_rename(
    conn: sqlite3.Connection,
    *,
    items: list[repo.PlanItemRow],
    first: repo.PlanItemRow,
    first_reason: str,
    result: UnitResult,
    logger: logging.Logger,
) -> None:
    """Fail a whole unit with nothing journaled and nothing moved (spec 7.3).

    Reached only before `begin()`, so there is no journal row to resolve and no file
    to put back. The whole unit fails because a companion group is renamed together
    or not at all - and doing it here costs nothing, which is exactly why the check
    that leads here happens before the first rename rather than after it.
    """
    for item in items:
        reason = (
            first_reason
            if item.id == first.id
            else UNIT_ALREADY_FAILED_REASON.format(old_rel_path=item.old_rel_path)
        )
        repo.set_plan_item_status(conn, item.id, "failed")
        result.failures.append((item.old_rel_path, reason))
        logger.warning("unit failed before any rename: %s", reason)
    conn.commit()
