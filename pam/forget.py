"""`pam forget` - retiring an archive pam should stop asking about (spec 3 rev 8).

WHY THIS IS NOT IN `status.py`, WHERE IT WAS WRITTEN. The cut is along WHO WRITES, not
along line count. `pam forget` takes the workspace lock, opens a session log and changes
PAM_HOME; `pam status`, the bare-`pam` guide and (M6 Task 6) `pam report` take no lock,
open no log and write nothing at all - which is not an implementation detail but the
argument `status.py`'s module docstring makes at length about why a read-only summary
must not behave like an archive-scoped command. Three readers and one writer in one
module would make that argument harder to keep true than to state, so the writer moved
out. Nothing about the command's behaviour changed in the move.

The mutating half is deliberately small: `run_forget` removes ONE pointer and nothing
else (M6 plan clarification 5), which is why `forget_lines` spends most of its words on
what was KEPT.
"""

from dataclasses import dataclass
from pathlib import Path

import click

from pam.catalog import repo
from pam.catalog.db import open_catalog_readonly
from pam.errors import PamError
from pam.execute.reporter import announce_interrupted
from pam.lock import WorkspaceLock
from pam.paths import Workspace, forget_archive, known_archives, pointer_for
from pam.sessionlog import close_session_log, open_session_log


@dataclass(frozen=True)
class ForgetResult:
    """What `pam forget` removed and what it kept. Both, always - see `forget_lines`."""

    root: Path
    workspace: Path
    # Spec 13's log path, carried out so the SUCCESS path can print it too. It shipped
    # only on the failure path, through `announce_interrupted`, which made an
    # unconditional promise conditional on the command going wrong (M6 Task 6).
    log_path: Path | None = None


def run_forget(path: Path) -> ForgetResult:
    """Remove one archive's PAM_HOME pointer. Touches nothing else (clarification 5).

    NOT `resolve_archive`: that refuses a folder pam cannot see, and an archive pam
    cannot see is the one this command exists for. The path is matched against the
    pointers themselves, so any spelling of a folder pam has scanned finds it.

    The workspace lock is held across the check and the deletion, so an apply cannot
    start writing `intent` rows into the window between them.
    """
    root = path.expanduser()
    pointer = pointer_for(root)
    if pointer is None:
        raise PamError(_unknown_archive_message(root))
    ws = Workspace(root=pointer.parent, archive_root=root)
    with WorkspaceLock(ws.lock_path):
        logger, log_path = open_session_log(ws.logs_dir, suffix="forget")
        try:
            if ws.db_path.exists():
                _refuse_if_mid_repair(ws, root)
            removed = forget_archive(root)
            if removed is None:  # pragma: no cover - the lock makes this a lost race
                raise PamError(_unknown_archive_message(root))
            logger.info("forgot the pointer to %s (workspace %s kept)", root, removed)
        except BaseException:
            # Spec 13's log path is unconditional, and `cli.py`'s `except Exception`
            # prints a traceback with no `Log:` line. The same shape apply, undo, scan,
            # plan and verify carry. The empty session id is the truth: forget has a
            # log, not an undoable session.
            announce_interrupted("", log_path)
            raise
        finally:
            close_session_log(logger)
    return ForgetResult(root=root, workspace=ws.root, log_path=log_path)


def _refuse_if_mid_repair(ws: Workspace, root: Path) -> None:
    """Refuse while spec 7.2 still has renames to resolve. The safety property.

    A journal row in `intent` is a rename nobody has decided the outcome of yet. The
    pointer is what every pam command uses to FIND this archive, so dropping it while
    the archive is mid-repair hides the repair - and spec 7.0 J2 makes the journal the
    only record that those renames were ever started.

    THE MESSAGE DIFFERS BY REACHABILITY, because the fix does. A folder pam can see is
    one command away from resolution: recovery runs at the start of every archive-scoped
    command. A folder pam cannot see cannot be recovered at all (J9 - recovery branches
    on the disk), so the only honest instruction is to reconnect it first, and saying
    "run any pam command" there would be the dead end UX 1.4 forbids.
    """
    conn = open_catalog_readonly(ws.db_path)
    try:
        unresolved = repo.rows_in_state(conn, "intent")
    finally:
        conn.close()
    if not unresolved:
        return
    named = "\n".join(f"  {row.old_rel_path} -> {row.new_rel_path}" for row in unresolved[:5])
    more = f"\n  ... and {len(unresolved) - 5:,} more" if len(unresolved) > 5 else ""
    fix = (
        f"Run any pam command against it first (for example: pam verify --archive {root});"
        "\npam finishes interrupted renames at the start of every command. Then run"
        " pam forget again."
        if root.is_dir()
        else (
            "pam cannot see this folder right now, so it cannot finish them. Connect the"
            "\ndrive or network folder and run any pam command against it, then run pam"
            " forget again."
        )
    )
    raise PamError(
        "pam will not forget this folder while it has renames it has not finished:\n"
        f"  {root}\n{len(unresolved):,} unfinished rename(s):\n{named}{more}\n{fix}"
    )


def _unknown_archive_message(root: Path) -> str:
    """UX 1.4: a refusal names what pam does know, so the user can pick from it.

    BOTH branches end in something to do. The empty one shipped stating a fact and
    stopping - "pam does not know about any photo folder yet" - which is a dead end
    dressed as an explanation, and the UX 1.4 sweep in `test_verify.py` could not see it
    because that sweep is driven from `ALL_CHECKS` and `pam forget` has none.
    """
    known = known_archives()
    if not known:
        return (
            f"pam has not scanned this folder, so there is nothing to forget:\n  {root}\n"
            "pam does not know about any photo folder yet.\n"
            "Run: pam scan <your photo folder>"
        )
    listed = "\n".join(
        f"  {a.path}" + ("" if a.reachable else "   (pam cannot see this one right now)")
        for a in known
    )
    return (
        f"pam has not scanned this folder, so there is nothing to forget:\n  {root}\n"
        f"Folders pam knows about:\n{listed}\nRun: pam forget <one of those folders>"
    )


def forget_lines(result: ForgetResult) -> list[str]:
    """What was removed AND what was kept. A command called "forget" owes both.

    The kept list is not reassurance copy - it is the operative fact. The catalog holds
    the journal, and spec 7.0 J2's promise that a rename can be undone indefinitely is
    a promise about that file. A user who read "forgot D:\\Photos" and inferred the
    history was gone would have been told something false by omission.
    """
    return [
        f"pam has forgotten {result.root}.",
        "  Removed: pam's pointer to this folder. pam will not offer it any more.",
        f"  Kept:    everything else - the catalog, the rename journal, the logs and"
        f" the reports, in\n           {result.workspace}",
        f"  Kept:    every file in {result.root}. pam has not touched it.",
        "",
        f"Next: pam scan {result.root}   (start using it again - the history is still there)",
    ]


def print_forget(result: ForgetResult) -> None:
    """The removed/kept block on stdout, then spec 13's log path on stderr, last.

    The same shape scan, plan, apply, undo and verify all carry. `forget` was the one
    command that opened a session log and never told the user where it was unless it
    failed - found by the command-list sweep in `tests/test_reporter.py`, which is
    written to fail for any command that is neither driven nor explicitly exempted.
    """
    for line in forget_lines(result):
        click.echo(line)
    if result.log_path is not None:
        click.echo(f"Log: {result.log_path}", err=True)
