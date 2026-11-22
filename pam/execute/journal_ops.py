"""RenameTransaction: the only place journal states change (spec 7.0 J1, J2, J5).

Nobody calls `repo.resolve` outside this module. The protocol needs two commits per
item - the intent before the rename and the resolution after it - and a type that
owns both is how nobody forgets one of them six months from now.

WHY THERE IS NO `with` STATEMENT AND NO `finally` HERE. `crash:` faults raise
SystemExit, which UNWINDS the stack rather than killing the process: every `finally`
runs, and `with conn:` would roll back on the way out. A real power cut runs neither.
So a design that leaned on either would test green under the fault matrix and be
broken in production - and `with conn:` specifically would roll back the very intent
row spec 7.0 J1 requires to be durable, turning the `after_intent` checkpoint into
the opposite of what it exists to prove. Every commit below is an explicit
`conn.commit()` on the happy path.

The `except BaseException: rollback; raise` blocks are the mirror image of that and
are not a durability mechanism: they discard writes that were NEVER committed, which
is exactly what a power cut would do to them. They are placed so that no rollback can
ever follow a commit - the commit is the last statement inside each guarded block.
Without them a `dbfail:` (caught in-process, so the connection lives on) would leave
uncommitted INSERTs pending on the connection for the NEXT item's commit to sweep in.
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from pam.catalog import repo
from pam.catalog.repo.journal import JournalInvariantError
from pam.faults import maybe_fault

# What a resolved item's plan_items.status becomes, per operation (spec 4's enum).
#
# When the rename SUCCEEDED: `applied` going forward, `undone` going backward.
#
# When it did not - failed, or performed and then reverted because a companion of
# the same unit failed (spec 7.3) - the answer differs by operation, and both halves
# say the same thing: the file is where it was before this transaction started, so
# the item's status must be too. For an apply that is `failed` (spec 7.3: the whole
# unit is failed, and the session report and the plan's terminal status both read
# this column). For an UNDO it is `applied`: the file is sitting at its applied name,
# exactly as it was before the undo tried and stopped. Writing `failed` there would
# claim the original apply failed, contradict the file on disk, and show up as a
# discrepancy in spec 7.5's check 4 - all while nothing had gone wrong except an undo
# that declined to guess (vision A8).
_STATUS_AFTER_DONE = {"rename": "applied", "undo_rename": "undone"}
_STATUS_AFTER_NO_CHANGE = {"rename": "failed", "undo_rename": "applied"}

# These two maps are the second half of a contract whose first half lives in
# repo.OPS. A third operation added there without a row here would KeyError at the
# first apply that used it; this fails at import instead, naming the file to fix.
assert set(_STATUS_AFTER_DONE) == set(_STATUS_AFTER_NO_CHANGE) == set(repo.OPS)


@dataclass(frozen=True)
class RenameItem:
    """One member of a rename unit: what moves, and which catalog rows own it.

    `size` and `mtime_ns` are the snapshot the rename is verified against, journaled
    with the intent so spec 7.4's undo can re-verify against them years later rather
    than trusting a `files` row that has been rewritten since.
    """

    plan_item_id: int
    file_id: int
    old_rel_path: str
    new_rel_path: str
    size: int
    mtime_ns: int


class RenameTransaction:
    """One journaled rename unit, from intent to resolution (spec 7.0 J1, J2, J5).

    Usage is linear and total - every path ends in exactly one of commit(), fail() or
    revert() per member, and the type exists so that six months from now nobody
    forgets one of the two commits the protocol requires.

    A unit rather than a single item because spec 7.1's loop is "for each plan_item,
    grouped by companion unit ... INSERT journal row(s); COMMIT", and spec 7.3 spells
    it out: a companion group's intents are all committed together, then the renames
    execute member-by-member. `catalog/repo/journal.insert_intents` was written to
    that contract. A lone file is a unit of one, which is the common case and needs no
    special path.

    The RESOLUTIONS stay per-member (spec 7.0 J5, one transaction per item): a crash
    between members must leave the catalog consistent with disk for every member
    already resolved, so batching them for speed is forbidden, not merely discouraged.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        session_id: str,
        op: str,
        items: list[RenameItem],
        logger: logging.Logger,
    ) -> None:
        if op not in repo.OPS:
            raise JournalInvariantError(
                f"unknown journal op {op!r} (legal ops: {list(repo.OPS)})"
            )
        if not items:
            raise JournalInvariantError("a rename transaction needs at least one item")
        self._conn = conn
        self._session_id = session_id
        self._op = op
        # Copied, not aliased: a caller that mutated the list after begin() would
        # desync it from _journal_ids, and every resolution reads both.
        self._items = tuple(items)
        self._logger = logger
        self._journal_ids: dict[int, int] = {}

    @classmethod
    def adopt(
        cls,
        conn: sqlite3.Connection,
        rows: list[repo.JournalRow],
        *,
        logger: logging.Logger,
    ) -> "RenameTransaction":
        """Bind journal rows that ALREADY exist (spec 7.2's phases 1 and 2).

        The alternate constructor recovery needs. `__init__` + `begin()` is for a run
        that is about to move files and must write its own write-ahead record; recovery
        is the opposite case - the rows were written by a process that is gone, and
        writing a second set would orphan the first (rows nothing can ever resolve).
        `begin()` is refused after this for free, by its own `if self._journal_ids:`
        guard.

        Its existence is what lets spec 7.2's unit-consistency pass call the SAME
        `unit.revert_unit` apply and undo call, so there is exactly one revert loop
        rather than three implementations of "put an irreplaceable file back".

        `session_id` and `op` come from the rows and must agree across them: a unit is
        the pair `(session_id, unit_key)`, and two sessions may legitimately hold rows
        for one unit_key (an apply and its later undo). Mixing them into one
        transaction would resolve one session's rows under the other's rules -
        `_STATUS_AFTER_NO_CHANGE` alone differs between `rename` and `undo_rename`.

        The `file_id` a resolution needs is not on the journal row; it comes from
        `plan_items`, whose id the row does carry. A missing one is catalog corruption
        (the column is a foreign key and nothing deletes a plan_items row), so it
        raises rather than being papered over - callers that must survive it check
        first, which spec 7.2's phase 1 does.
        """
        if not rows:
            raise JournalInvariantError("adopt() needs at least one journal row")
        session_ids = {row.session_id for row in rows}
        ops = {row.op for row in rows}
        if len(session_ids) != 1 or len(ops) != 1:
            raise JournalInvariantError(
                f"adopt() takes rows from ONE session and ONE op, got {sorted(session_ids)}"
                f" / {sorted(ops)}"
            )
        plan_item_ids = [row.plan_item_id for row in rows]
        if len(set(plan_item_ids)) != len(plan_item_ids):
            raise JournalInvariantError(
                f"adopt() got two rows for one plan_item in session {rows[0].session_id!r};"
                " a transaction is keyed by plan_item_id and could resolve only one"
            )
        plan_rows = repo.plan_items_by_id(conn, plan_item_ids)
        missing = [pid for pid in plan_item_ids if pid not in plan_rows]
        if missing:
            raise JournalInvariantError(
                f"journal rows name plan_items {missing} that do not exist; the catalog"
                " is corrupt (plan_item_id is a foreign key and plan_items are never"
                " deleted). Run: pam verify"
            )
        txn = cls(
            conn,
            session_id=rows[0].session_id,
            op=rows[0].op,
            items=[
                RenameItem(
                    plan_item_id=row.plan_item_id,
                    file_id=plan_rows[row.plan_item_id].file_id,
                    old_rel_path=row.old_rel_path,
                    new_rel_path=row.new_rel_path,
                    # The snapshot taken when the INTENT was written, not a fresh read
                    # of `files`: recovery may run days later, and a file edited since
                    # the crash must be left alone rather than moved on top of whatever
                    # is now at the other name (spec 7.4's rule, reused here).
                    size=row.size,
                    mtime_ns=row.mtime_ns,
                )
                for row in rows
            ],
            logger=logger,
        )
        txn._journal_ids = {row.plan_item_id: row.id for row in rows}
        return txn

    @property
    def items(self) -> tuple[RenameItem, ...]:
        """The members this transaction covers, in the caller's (or the rows') order.

        `adopt()` builds its own items, so its caller needs them back to resolve
        anything - and they must be the SAME frozen values, because `_journal_id`
        compares by value.
        """
        return self._items

    def begin(self) -> None:
        """Write every member's intent row and COMMIT it (spec 7.0 J1, write-ahead).

        When this returns, the journal durably says pam MEANT to perform these
        renames, and only then may a caller touch the filesystem. There is no
        ordering in which the filesystem moves first.

        The two checkpoints assert different things and both are load-bearing:
        `before_intent_commit` - a crash here must leave no trace at all, no journal
        row and no renamed file; `after_intent` - the intent is durable and spec 7.2's
        recovery must find it and resolve it.
        """
        now = _now()
        entries = [
            repo.JournalEntry(
                plan_item_id=item.plan_item_id,
                old_rel_path=item.old_rel_path,
                new_rel_path=item.new_rel_path,
                size=item.size,
                mtime_ns=item.mtime_ns,
            )
            for item in self._items
        ]
        # `journal` has no UNIQUE on plan_item_id, so a second begin() would write a
        # second set of intent rows and orphan the first - rows nothing can ever
        # resolve, which spec 7.2's recovery would later adopt as extra `done` rows
        # for one plan_item. That is the one misuse repo.resolve's SQL cannot make
        # loud, so it is made loud here.
        if self._journal_ids:
            raise JournalInvariantError("begin() has already run for this transaction")
        try:
            ids = repo.insert_intents(self._conn, self._session_id, self._op, entries, now=now)
            maybe_fault("before_intent_commit")
            self._conn.commit()
        except BaseException:
            # Discards writes that were NEVER committed - which is what a power cut
            # does to them - so this is not a durability mechanism. It matters for
            # `dbfail:`, which is caught in-process: without it the abandoned INSERTs
            # stay pending and the NEXT item's commit sweeps them in.
            try:
                self._conn.rollback()
            except Exception:
                # A failing rollback must not replace the exception being diagnosed.
                self._logger.exception("rollback failed while unwinding begin()")
            raise
        self._journal_ids = {
            item.plan_item_id: journal_id
            for item, journal_id in zip(self._items, ids, strict=True)
        }
        for item in self._items:
            self._logger.info(
                "journal intent [%s %s]: %s -> %s",
                self._session_id,
                self._op,
                item.old_rel_path,
                item.new_rel_path,
            )
        maybe_fault("after_intent")

    def commit(self, item: RenameItem) -> str | None:
        """The rename happened: journal `done`, item status, files row - ONE commit.

        spec 7.0 J5. The three writes are one transaction because any split leaves a
        window a crash can land in that spec 7.2 cannot repair: a `done` journal row
        whose `files.rel_path` still names the old path is invisible to recovery,
        which only ever looks at rows in `intent`.

        `before_done_commit` sits immediately before the commit, so a fault there
        leaves the row `intent` with the rename physically done - precisely the state
        spec 7.2 adopts (new exists, old absent -> mark done, fix the files row).

        Returns the catalog discrepancy `_point_files_row_at` could not write, or None
        on the ordinary path. See that method for why a discrepancy is a note rather
        than an exception (spec 7.0 J11).
        """
        now = _now()
        journal_id = self._journal_id(item)
        try:
            repo.resolve(self._conn, journal_id, "done", now=now)
            repo.set_plan_item_status(
                self._conn, item.plan_item_id, _STATUS_AFTER_DONE[self._op]
            )
            discrepancy = self._point_files_row_at(item, item.new_rel_path)
            maybe_fault("before_done_commit")
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise
        self._logger.info(
            "journal done [%s %s]: %s -> %s",
            self._session_id,
            self._op,
            item.old_rel_path,
            item.new_rel_path,
        )
        return discrepancy

    def _point_files_row_at(self, item: RenameItem, rel_path: str) -> str | None:
        """Move the `files` row to where the file now is - or say why it could not.

        spec 7.0 **J11**: `files.rel_path` is UNIQUE and `files` rows are never deleted,
        so a path can be owned by a row describing a file that is gone. That must never
        prevent a journal row from reaching a terminal state, and must never leave a
        file moved while its resolution rolled back. Both halves were reproduced in the
        2026-08-06 design review: a stranded member's own message tells the user to run
        `pam scan`, which creates a row at the old path, and then the revert's
        `set_file_path` raised sqlite3.IntegrityError - the file had already moved, the
        whole transaction rolled back, and the IntegrityError escaped a pass that runs
        at the start of every invocation.

        So: ask first, and guard the write anyway. Both layers are here rather than in
        recovery because this is a property of the journal state machine, not of one
        caller - and because `files` is a cache the next scan rebuilds, while the
        journal is the record of what pam actually did. The resolution wins; the
        catalog waits for `pam scan`.

        In apply this is inert - `validator._ghost_name_refusals` refuses such an item
        before it is ever journaled - which is the right shape for a safety net.
        """
        owner = repo.path_owner(self._conn, rel_path)
        if owner is not None and owner != item.file_id:
            return self._discrepancy(item, rel_path, "another catalog row already owns it")
        try:
            repo.set_file_path(self._conn, item.file_id, rel_path)
        except sqlite3.IntegrityError as exc:
            return self._discrepancy(item, rel_path, str(exc))
        return None

    def _discrepancy(self, item: RenameItem, rel_path: str, detail: str) -> str:
        note = (
            f"pam's records still need updating for {rel_path}: {detail}."
            " The file itself is fine. Run: pam scan"
        )
        self._logger.error(
            "catalog discrepancy [%s %s]: files row %s could not be pointed at %s: %s",
            self._session_id,
            self._op,
            item.file_id,
            rel_path,
            detail,
        )
        return note

    def fail(self, item: RenameItem, reason: str) -> None:
        """The rename did not happen: journal `failed`, item status, one commit.

        The file is untouched on disk, so the `files` row is left exactly as it is -
        it already describes what is there. Recording the reason is the caller's job
        (the session report); this records the state transition and logs it.

        The item's status comes from `_STATUS_AFTER_NO_CHANGE`: `failed` for an apply,
        `applied` for an undo that did not manage to put the file back.
        """
        now = _now()
        journal_id = self._journal_id(item)
        try:
            repo.resolve(self._conn, journal_id, "failed", now=now)
            repo.set_plan_item_status(
                self._conn, item.plan_item_id, _STATUS_AFTER_NO_CHANGE[self._op]
            )
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise
        self._logger.warning(
            "journal failed [%s %s]: %s -> %s: %s",
            self._session_id,
            self._op,
            item.old_rel_path,
            item.new_rel_path,
            reason,
        )

    def revert(self, item: RenameItem) -> str | None:
        """Called AFTER the file has physically been renamed back (spec 7.3, J6).

        Only ever reached from `done`: the member was renamed, a later member of the
        same unit failed, and the unit is being put back so a companion group is never
        left split across two stems (vision S3). spec 7.0 J2 allows `done -> reverted`
        and nothing else, and the repo layer enforces that in SQL.

        The item's status goes to `_STATUS_AFTER_NO_CHANGE`, not back to `approved`:
        for an apply that is `failed`, because spec 7.3 says the WHOLE unit is failed
        and this member is part of that unit's outcome. The `files` row goes back to
        the old path in the same transaction, because the file is at the old path
        again and the catalog must agree with disk.

        Returns a catalog discrepancy note or None, for the reason on `commit`.
        """
        now = _now()
        journal_id = self._journal_id(item)
        try:
            repo.resolve(self._conn, journal_id, "reverted", now=now)
            repo.set_plan_item_status(
                self._conn, item.plan_item_id, _STATUS_AFTER_NO_CHANGE[self._op]
            )
            discrepancy = self._point_files_row_at(item, item.old_rel_path)
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise
        self._logger.warning(
            "journal reverted [%s %s]: %s -> %s",
            self._session_id,
            self._op,
            item.new_rel_path,
            item.old_rel_path,
        )
        return discrepancy

    def _journal_id(self, item: RenameItem) -> int:
        """The intent row this member is resolving, or a loud error.

        Resolving before `begin()` would mean a rename was performed with no
        write-ahead record (spec 7.0 J1) - pam contradicting itself, never a user
        error - so it raises the same type an illegal transition does, outside both
        PamError and ValueError where nothing can accidentally swallow it.
        """
        # _journal_ids is keyed by plan_item_id alone, so an item carrying a matching
        # id but a different new_rel_path would resolve the right row with the wrong
        # path and write a false files.rel_path. RenameItem is frozen, so `in`
        # compares by value and closes that for free.
        if item not in self._items:
            raise JournalInvariantError(
                f"plan_item {item.plan_item_id} is not a member of this transaction"
            )
        journal_id = self._journal_ids.get(item.plan_item_id)
        if journal_id is None:
            raise JournalInvariantError(
                f"plan_item {item.plan_item_id} has no intent row in this transaction;"
                " begin() must be called, and must have committed, before any rename"
            )
        return journal_id


def _now() -> str:
    """The project's timestamp format, matching every other `now=` in the catalog."""
    return datetime.now().isoformat(timespec="seconds")
