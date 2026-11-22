"""The rename and undo jobs, through U19's one door (M14 Task 6b; cut from `jobs.py` by M15 Task 4).

THIS MODULE IS THE ONLY WEBUI MODULE THAT IMPORTS `pam.execute.apply` / `undo` (U19,
`tests/guards.py::ALLOWED_WEBUI_EXECUTE_IMPORTS`). The page renames through `apply_locked` and
puts names back through `undo_locked` - the console's own bodies, under the server's held lock,
with a connection of their own - and reaches `perform_verified_rename` only through them. The
confirmation's facts and the session's are read here for the same reason: they are the
preflight's and the CLI's, and `confirm_api.py` may not import either.

The bodies below are `JobBody`s: `webui/jobs.py`'s registry runs them on its thread, records how
they end, and heals one that dies (join-then-recover, which stays in `jobs.py`). This module
starts no thread and holds no registry state. M15 Task 4 (`m14.md` C5) cut it from `jobs.py`
along the line the M14 review named; the two guard pairs moved with it and nothing else did.
"""

import hashlib
import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pam.catalog import repo
from pam.catalog.db import connect
from pam.execute import apply, undo
from pam.lock import HeldWorkspace
from pam.paths import workspace_if_exists
from pam.webui.jobs import Finished, JobBody, Progress, ShouldStop

# U14, word for word: a rename or undo whose confirmation no longer matches what is about to run.
CHANGED = "Something changed since you looked. Check the names again."

ConfirmFn = Callable[["apply.Confirmation"], bool]
# `fn(root, digest)`: whether the undo's nonce was issued for this folder and these rows (U14).
UndoConfirmFn = Callable[[Path, str], bool]


def confirmation(conn: sqlite3.Connection, root: Path, plan_id: int) -> "apply.Confirmation":
    """U6's facts and the U14 digest for `plan_id` now: `apply.confirmation_for`. Writes nothing;
    a whole-plan refusal raises its `PamError`, as `pam apply` refuses."""
    return apply.confirmation_for(conn, root, plan_id)


@dataclass(frozen=True)
class SessionFacts:
    """One rename session as the page's session screen shows it, and its U14 digest."""

    counts: repo.SessionCounts
    digest: str

    @property
    def undoable(self) -> bool:
        """Design 7.4, as `pam status` applies it: an apply session with `done > undone`."""
        return self.counts.done > self.counts.undone


def session_facts(conn: sqlite3.Connection, root: Path, session_id: str) -> SessionFacts:
    """The session's counts and digest; the CLI's refusal (unknown, or an undo) as a `PamError`.

    The digest is over the session's journal rows and the status of each row's plan item, so an
    undo of the same session in between - any change to what an undo would replay - changes it.
    """
    rows = undo.undoable_rows(conn, root, session_id)
    items = repo.plan_items_by_id(conn, [row.plan_item_id for row in rows])
    marks = sorted(
        (row.id, row.state, getattr(items.get(row.plan_item_id), "status", None))
        for row in rows
    )
    digest = hashlib.sha256(json.dumps(marks).encode("utf-8")).hexdigest()
    counts = next(c for c in repo.sessions(conn) if c.session_id == session_id)
    return SessionFacts(counts=counts, digest=digest)


def renaming(root: Path, plan_id: int, confirm: ConfirmFn, log_path: Path | None) -> JobBody:
    """The RENAME job: `apply_locked(..., confirm=confirm)`, then the session report, as
    `run_apply` writes it. `confirm` False is a cancelled apply: nothing written, U14's line."""

    def body(
        held: HeldWorkspace, progress: Progress, should_stop: ShouldStop, logger: logging.Logger
    ) -> Finished:
        ws = held.require(workspace_if_exists(root))
        summary = apply.ApplySummary(archive_root=root, plan_id=plan_id, log_path=log_path)
        clock = apply._Clock()
        conn = connect(ws.db_path)
        try:
            try:
                apply.apply_locked(
                    held, conn, root, plan_id, summary, logger, confirm, clock, False, progress
                )
                if summary.cancelled:
                    return Finished(lines=(CHANGED,), stopped=True)
                summary.elapsed_s = clock.elapsed()
                summary.report_path = apply.write_session_report(
                    ws.reports_dir, summary, logger
                )
            except BaseException as died:
                # S2-F1: the session the run journalled before it died, for `_run` - also when it
                # died writing the report, after the renames (Task 7 Stage 2 F3).
                died.pam_session = summary.session_id  # type: ignore[attr-defined]
                raise
        finally:
            conn.close()
        # The failures' reasons, then AM2's sentence when a unit was skipped (`reporter.done_lines`).
        reasons = apply.done_lines(summary)
        result: dict[str, float] = {
            "renamed": summary.renamed,
            "failed": summary.failed,
            "not_approved": summary.not_approved,
            "elapsed_s": round(summary.elapsed_s, 1),
            "cloud_only": summary.cloud_only_files,
            "cloud_only_units": summary.cloud_only_units,
        }
        return Finished(lines=reasons, session=summary.session_id, result=result)

    return body


def undoing(root: Path, session_id: str, confirm: UndoConfirmFn) -> JobBody:
    """The UNDO job: the session's facts recomputed under the held lock, then `undo_locked`.

    `confirm(root, digest)` False is a cancelled undo: nothing written, U14's line."""

    def body(
        held: HeldWorkspace, progress: Progress, should_stop: ShouldStop, logger: logging.Logger
    ) -> Finished:
        ws = held.require(workspace_if_exists(root))
        conn = connect(ws.db_path)
        try:
            if not confirm(root, session_facts(conn, root, session_id).digest):
                return Finished(lines=(CHANGED,), stopped=True)
            summary = undo.UndoSummary(undone_session_id=session_id)
            undo.undo_locked(held, conn, root, session_id, summary, logger, progress)
        finally:
            conn.close()
        reasons = tuple(reason for _, reason in summary.failures)
        result: dict[str, float] = {
            "restored": summary.restored,
            "failed": summary.failed,
            "already_undone": len(summary.already_undone),
        }
        return Finished(lines=reasons, session=summary.session_id, result=result)

    return body
