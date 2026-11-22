"""`pam review` - the archive-scoped command that serves spec 8.2's review UI.

IT IS ARCHIVE-SCOPED AND BEHAVES LIKE ONE (M7 plan clarification 2). Spec 3's
resolution, the workspace lock, spec 7.2's recovery, a session log, and spec 13's
`Log:` line on stderr at exit - including on Ctrl+C, which is the NORMAL way a review
session ends. The shape is `pam verify`'s, deliberately: a fifth spelling of "resolve,
lock, recover, log" is a fifth place for one of the four to be forgotten, and
`tests/test_recover.py::test_recovery_is_wired_into_exactly_the_archive_scoped_commands`
asserts the wiring by walking `Call` nodes rather than imports.

THE SERVER HOLDS THE LOCK FOR ITS WHOLE LIFETIME, and the consequence is deliberate:
`pam apply` in a second console is refused with "another pam command is running" until
the user stops the server. A review session is a window during which the plan is being
edited; an apply against a plan half-way through being decided is the case the lock
exists for.

THE EXIT-CODE SPLIT, so a crash cannot masquerade as success (clarification 4). Ctrl+C
and a programmatic shutdown are DELIBERATE ends - `serve_until_interrupted` absorbs the
KeyboardInterrupt, this function prints its closing summary and exits 0. An unexpected
exception outside request handling propagates to `cli.py`'s spec 13 handler (traceback,
`Log:`, exit 2). An exception INSIDE a request handler never reaches here at all: it is
a 500 and the server keeps serving, because a handler bug must not end a half-reviewed
session. Spec 13's refusals - no open plan, a plan that is not `open` - exit 1.

WHY THE COUNTS ARE READ AFTER THE SERVER STOPS, from a new connection. They are what
the user just did in the browser, written by worker threads through their own
connections; a number this function had been carrying since startup would report the
state the session began in. Vision principle 9: never summarize failure as success.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import click

from pam.catalog import repo
from pam.catalog.db import migrate, open_catalog
from pam.errors import PamError
from pam.execute.recover import recovery_line, run_recovery
from pam.execute.reporter import announce_interrupted
from pam.lock import WorkspaceLock
from pam.paths import Workspace, resolve_archive, workspace_if_exists
from pam.sessionlog import close_session_log, open_session_log
from pam.status import NextStep, step_lines
from pam.webui.server import (
    FACES_INDEX as FACES_PAGE,
)
from pam.webui.server import (
    ReviewServer,
    open_in_browser,
    serve_until_interrupted,
)

# What the console says while the server runs. UX spec 2: the URL is authoritative, the
# purpose line says what the page does and what it does NOT do, and the user is told how to
# stop. W17 (U8's table), word for word, since M14 Task 6: the page renames, through the same
# orchestration `pam apply` uses (U5), and only after the Confirm screen (U6). ONE constant for
# `pam review` and `pam start`, which serve the same page on the same server (ruling B1).
PAGE_PURPOSE = (
    "Review names and rename them in your browser. Nothing is renamed until you confirm there."
)
# UX 6's page says what IT does. A18: `pam faces label` starts this same server with NO
# PLAN, so the sentence cannot be the review page's - there is nothing to approve on it.
FACES_PAGE_PURPOSE = "Name the people pam found. Nothing is renamed from the browser."
STOP_HINT = "Press Ctrl+C here when you are done."
BROWSER_FALLBACK = "Could not open your browser - open this address yourself: {url}"

NOT_SCANNED = "pam has not scanned {root} yet. Run: pam scan {root}"
NO_OPEN_PLAN = "pam has no open plan for {root}. Run: pam plan"
UNKNOWN_PLAN = "pam does not know a plan with id {plan_id}. Run: pam plan"

# Unreachable through pam today - `repo.supersede_open_plans` runs on every scan and
# every plan, so at most one plan is `open` - and therefore NOT written as an assertion.
# A catalog is a file on the user's disk; the honest answer to "there are two" is to say
# so and ask which, not to crash or to pick the first.
SEVERAL_OPEN_PLANS = (
    "pam has more than one open plan for {root} ({ids}). Say which one you mean:"
    " pam review <PLAN_ID>"
)

# Spec 4's `plans.status` vocabulary minus `open`, in the words a person reads. The
# three below that MOVED FILES are answered differently from the two that did not
# (clarification 3): what an applied plan did is a question `pam report session`
# answers, and a stale plan is one `pam plan` replaces.
_APPLIED_STATUSES = ("applied", "partially_applied", "undone")
_HUMAN_STATUS = {
    "applied": "already applied",
    "partially_applied": "already applied in part",
    "undone": "applied and then undone",
    "superseded": "superseded - a newer scan or plan replaced it",
    "applying": "half-applied after an interruption",
}


@dataclass
class ReviewSummary:
    """What one `pam review` session was, and what the user left behind in it."""

    archive_root: Path | None = None
    plan_id: int | None = None
    url: str = ""
    approved: int = 0
    skipped: int = 0
    remaining: int = 0
    # M14 Task 6 (S1-F1): the plan's status when the server stopped. The page renames and undoes
    # now (U5), so a review can end on a plan that is no longer `open`; `sessions` are its rename
    # sessions, oldest first (`repo.rename_sessions_for_plan`; C6.4: the page may rename one plan
    # in several sessions, and the closing names each).
    status: str = "open"
    sessions: tuple[str, ...] = ()
    # The NEWEST of `sessions` that `pam undo` would still put files back for - `pam status`'s
    # own test (`ArchiveFacts.undoable_session`: a rename session with `done > undone`), or None.
    # A page job that died after its first intent leaves a session that renamed nothing (S1P-F1).
    undo_session: str | None = None
    # The newest `open` plan when the server stopped: a page read supersedes this one and plans
    # again, so a superseded plan's closing names the new one (S1-F1).
    open_plan: int | None = None
    log_path: Path | None = None
    # Step 8 (U11): the serve loop ended while a rename or undo job was moving files - a crash.
    stopped_while_renaming: bool = False


def run_review(
    plan_id: int | None = None,
    archive: Path | None = None,
    *,
    serve: Callable[[ReviewServer], None] | None = None,
    opener: Callable[[str], bool] | None = None,
) -> ReviewSummary:
    """Serve one archive's open plan on the page that runs the whole workflow (U5, ruling B1).

    The server is `pam start`'s, bound to this archive (`launcher.review_server`): the page
    reviews, and renames and undoes through the same orchestration `pam apply` / `pam undo` use.
    The refusals and their order are unchanged (a frozen interface): the archive, NOT_SCANNED,
    the lock, recovery, then `_resolve_plan`. The lock is THIS function's, held for the server's
    whole life as before M14.

    `serve` and `opener` are test seams and default to the real ones. They are resolved
    by NAME inside the body rather than as default arguments, so that monkeypatching
    `pam.webui.serve_until_interrupted` reaches a call that came through `cli.main`.
    """
    # Imported here: `launcher` imports this module (`_announce`), so a top-level import would
    # be circular.
    from pam.webui import launcher

    root = resolve_archive(archive)
    ws = workspace_if_exists(root)
    if ws is None:
        raise PamError(NOT_SCANNED.format(root=root))

    summary = ReviewSummary(archive_root=root)
    lock = WorkspaceLock(ws.lock_path, ws)
    held = lock.__enter__()
    try:
        logger, log_path = open_session_log(ws.logs_dir, suffix="review")
        summary.log_path = log_path
        try:
            # Recovery WRITES, so it gets its own connection and that connection is
            # closed before anything is served - the same reasoning `pam verify` records
            # for its two connections. Nothing long-lived is handed to the server.
            conn = open_catalog(ws.db_path)
            try:
                migrate(conn)
                recovered = run_recovery(conn, root, logger)
                conn.commit()
                reviewing = _resolve_plan(conn, root, plan_id)
            finally:
                conn.close()
            summary.plan_id = reviewing

            server = launcher.review_server(
                ws, reviewing, logger, held, log_path, recovery_line(recovered)
            )
            summary.url = server.url
            try:
                _announce(server.url, opener)
                serve_loop = serve_until_interrupted if serve is None else serve
                serve_loop(server)
            finally:
                # Step 8 (U11), `run_start`'s rule: a rename or undo job is NOT waited for and
                # NOT stopped - Ctrl+C during one is a crash. Only the socket closes; the lock
                # stays held until this process ends, and the next pam's recovery heals the rest.
                summary.stopped_while_renaming = server.jobs.moving()
                if summary.stopped_while_renaming:
                    ReviewServer.server_close(server)
                else:
                    server.server_close()

            if summary.stopped_while_renaming:
                click.echo(launcher.STOPPED_WHILE_RENAMING)
                click.echo(f"Log: {log_path}", err=True)
            else:
                _read_counts(ws, reviewing, summary)
                print_review_summary(summary)
        except BaseException:
            # Spec 13's log path is unconditional and `cli.py`'s traceback path prints
            # no `Log:` line. `BaseException` because a SystemExit and a Ctrl+C that
            # escaped the serve loop are the two cases where the user most needs it.
            announce_interrupted("", log_path)
            raise
        finally:
            if not summary.stopped_while_renaming:
                close_session_log(logger)
    finally:
        # Stopped while renaming: the job thread still moving files dies with the process,
        # under this lock (the lockfile names a dead pid, which the next pam removes).
        if not summary.stopped_while_renaming:
            lock.__exit__(None, None, None)
    return summary


def _resolve_plan(conn: sqlite3.Connection, root: Path, plan_id: int | None) -> int:
    """Which plan is being reviewed. `open` plans only (M7 plan clarification 3).

    Every refusal names the command that moves the user forward - UX spec 1.4: a
    refusal that does not say what to do next is a dead end.
    """
    if plan_id is None:
        open_ids = repo.plans_in_status(conn, "open")
        if not open_ids:
            raise PamError(NO_OPEN_PLAN.format(root=root))
        if len(open_ids) > 1:
            ids = ", ".join(str(i) for i in open_ids)
            raise PamError(SEVERAL_OPEN_PLANS.format(root=root, ids=ids))
        return open_ids[0]

    status = repo.plan_status(conn, plan_id)
    if status is None:
        raise PamError(UNKNOWN_PLAN.format(plan_id=plan_id))
    if status != "open":
        raise PamError(_plan_refusal(conn, plan_id, status))
    return plan_id


def _plan_refusal(conn: sqlite3.Connection, plan_id: int, status: str) -> str:
    """The sentence for a plan that is not `open`: what it is, and what to run instead."""
    described = _HUMAN_STATUS.get(status, status)
    if status in _APPLIED_STATUSES:
        session = repo.rename_session_for_plan(conn, plan_id)
        # No rename session means nothing was ever journalled against this plan, so
        # there is no id to hand the user. Naming the command with a placeholder still
        # points somewhere; inventing an id would not.
        target = f"pam report session {session}" if session else "pam report session"
        return (
            f"Plan {plan_id} is {described}, so there is nothing left to review."
            f" To see what it did, run: {target}"
        )
    return (
        f"Plan {plan_id} is {described}, so it no longer describes what pam would"
        " rename. Run: pam plan"
    )


def _announce(
    url: str,
    opener: Callable[[str], bool] | None,
    opening: str = "Review at",
    purpose: str = PAGE_PURPOSE,
) -> None:
    """The three lines UX spec 2 owes the user, then the browser as a convenience.

    TWO COMMANDS, ONE ANNOUNCER (M11 Task 10). `pam faces label` opens the same server at a
    different page with a different purpose, and the alternative to two parameters was a
    second copy of this function - which is a second place for the Ctrl+C hint to be
    forgotten, and the hint is the only thing on screen telling the user how to get their
    console back.
    """
    click.echo(f"{opening} {url}")
    click.echo(purpose)
    click.echo(STOP_HINT)
    if not open_in_browser(url, opener):
        click.echo(BROWSER_FALLBACK.format(url=url))


def _read_counts(ws: Workspace, plan_id: int, summary: ReviewSummary) -> None:
    """The closing counts, read FRESH from the catalog after the server has stopped."""
    conn = open_catalog(ws.db_path)
    try:
        counts = repo.plan_item_status_counts(conn, plan_id)
        summary.status = repo.plan_status(conn, plan_id) or "open"
        summary.sessions = tuple(repo.rename_sessions_for_plan(conn, plan_id))
        undoable = {
            c.session_id
            for c in repo.sessions(conn)
            if c.ops == (repo.OP_RENAME,) and c.done > c.undone
        }
        summary.undo_session = next(
            (s for s in reversed(summary.sessions) if s in undoable), None
        )
        open_ids = repo.plans_in_status(conn, "open")
        summary.open_plan = open_ids[-1] if open_ids else None
    finally:
        conn.close()
    summary.approved = counts.get("approved", 0)
    summary.skipped = counts.get("skipped", 0)
    # Everything else, rather than `counts["proposed"]`: an open plan's items are
    # proposed, approved or skipped today, and a status this function had not heard of
    # would otherwise vanish from a summary whose whole job is to account for the plan.
    summary.remaining = sum(counts.values()) - summary.approved - summary.skipped


def review_summary_lines(summary: ReviewSummary) -> list[str]:
    """The closing block. UX spec 2: numbers humanized, and it ends with `Next:`.

    Rendered through `status.step_lines` like every other closing block (M8 amendment
    B2: the `Next:` block has one format). These two lines were the last reasonless
    `Next:` in the product - B2 measured them and no M9 task's file list reached them.
    The reasons are the ones `pam status` and `pam plan` already give for the same two
    commands, because B2's point is that two surfaces naming one command must not
    describe it differently.
    """
    if summary.status != "open":
        return _closed_plan_lines(summary)
    lines = [
        f"Plan {summary.plan_id}: {summary.approved:,} approved,"
        f" {summary.skipped:,} skipped, {summary.remaining:,} still to review."
    ]
    if summary.approved:
        step = NextStep(f"pam apply {summary.plan_id}", "renames only what you approve")
    else:
        lines.append("Nothing is approved yet, so pam apply has nothing to do.")
        step = NextStep(f"pam review {summary.plan_id}", "see & approve the plan")
    lines.extend(step_lines((step,)))
    return lines


def _closed_plan_lines(summary: ReviewSummary) -> list[str]:
    """The closing block when the page renamed or undid this plan (M14 Task 6, S1-F1).

    Counting its items as approved / skipped / still to review would call renamed files "still
    to review" and name a `pam review` that `_resolve_plan` refuses. It says what the plan is, in
    `_plan_refusal`'s words, and the steps `pam status` gives for the same state: what each
    session did, and - while the newest undoable one's files are still renamed (S1P-F1) - how
    to put them back. C6.4 (owner, 2026-11-16): a plan renamed in more than one session says how
    many, `Plan 3 is applied (2 sessions).`, and names one report step per session.
    """
    described = _HUMAN_STATUS.get(summary.status, summary.status)
    applied = summary.status in _APPLIED_STATUSES
    count = len(summary.sessions) if applied else 0
    several = f" ({count} sessions)" if count > 1 else ""
    lines = [f"Plan {summary.plan_id} is {described}{several}."]
    if applied and summary.sessions:
        steps = [
            NextStep(f"pam report session {session}", "see what was renamed")
            for session in summary.sessions
        ]
        if summary.undo_session is not None:
            steps.append(
                NextStep(
                    f"pam undo {summary.undo_session}",
                    "put those files back under their old names",
                )
            )
    elif summary.open_plan is not None:
        # A page read scanned and planned again: the new plan is the one to review.
        steps = [NextStep(f"pam review {summary.open_plan}", "see & approve the plan")]
    else:
        steps = [NextStep("pam plan", "propose new names - renames nothing")]
    lines.extend(step_lines(tuple(steps)))
    return lines


def print_review_summary(summary: ReviewSummary) -> None:
    """The closing block on stdout, then spec 13's log path on stderr."""
    for line in review_summary_lines(summary):
        click.echo(line)
    if summary.log_path is not None:
        click.echo(f"Log: {summary.log_path}", err=True)


@dataclass
class FacesLabelSummary:
    """What one `pam faces label` session was. NOT a `ReviewSummary` and not a subset of one.

    The two commands share a server and share almost nothing else. A review session is
    about a PLAN and closes on approved / skipped / still to review; a labeling session is
    about the whole archive's people and closes on named / still unnamed. Folding the
    second into the first would mean a summary whose fields are three zeroes and a plan id
    of `None`, which is a summary that has to be read past rather than read.
    """

    archive_root: Path | None = None
    url: str = ""
    named: int = 0
    remaining: int = 0
    log_path: Path | None = None


def run_faces_label(
    archive: Path | None = None,
    *,
    serve: Callable[[ReviewServer], None] | None = None,
    opener: Callable[[str], bool] | None = None,
) -> FacesLabelSummary:
    """Serve UX 6's people-labeling page for one archive. Renames nothing (A18).

    IT DOES NOT GO THROUGH `run_review`, AND THAT IS THE POINT OF THE AMENDMENT.
    `run_review` calls `_resolve_plan`, which ALWAYS returns an `int` - it resolves the
    latest open plan when given `None` and REFUSES when there is none - so reusing it would
    make a corpus-wide labeling page refuse to start because no plan is open, which is
    exactly what spec 8.2 rev 22 forbids in as many words. `_read_counts` is plan-only for
    the same reason. What IS reused is everything that is not about a plan: the archive
    resolution, the lock, recovery, the session log, `ReviewServer`, `_announce` and
    `serve_until_interrupted`.

    IT IS ARCHIVE-SCOPED AND BEHAVES LIKE ONE, which is why `run_recovery` is here:
    `tests/test_recover.py::test_recovery_is_wired_into_exactly_the_archive_scoped_commands`
    lists `webui/__init__.py` and this is the second command in this file. The `pam review`
    argument applies unchanged - it takes the workspace lock and holds it for the server's
    whole lifetime, so a command that skipped recovery would sit on the one lock the
    machinery that heals a crash needs.
    """
    root = resolve_archive(archive)
    ws = workspace_if_exists(root)
    if ws is None:
        raise PamError(NOT_SCANNED.format(root=root))

    summary = FacesLabelSummary(archive_root=root)
    with WorkspaceLock(ws.lock_path):
        logger, log_path = open_session_log(ws.logs_dir, suffix="faces-label")
        summary.log_path = log_path
        try:
            # Recovery WRITES and its connection is closed before anything is served -
            # `run_review`'s reasoning, unchanged. Nothing long-lived reaches the server.
            conn = open_catalog(ws.db_path)
            try:
                migrate(conn)
                run_recovery(conn, root, logger)
                conn.commit()
            finally:
                conn.close()

            server = ReviewServer(workspace=ws, plan_id=None, logger=logger)
            # The faces PAGE, not the review page. Both are served by one server under one
            # token; `server.url` is the token prefix and the page is a name in
            # `STATIC_FILES` beneath it.
            summary.url = server.url + FACES_PAGE
            try:
                _announce(summary.url, opener, "Name people at", FACES_PAGE_PURPOSE)
                serve_loop = serve_until_interrupted if serve is None else serve
                serve_loop(server)
            finally:
                server.server_close()

            _read_people_counts(ws, server.config.tiers["faces"].floor, summary)
            print_faces_label_summary(summary)
        except BaseException:
            announce_interrupted("", log_path)
            raise
        finally:
            close_session_log(logger)
    return summary


def _read_people_counts(ws: Workspace, floor: float, summary: FacesLabelSummary) -> None:
    """The closing counts, read FRESH after the server has stopped (`_read_counts`'s rule).

    Clusters, not faces: UX 6's progress line counts clusters ("7 named - 16 remaining")
    because a cluster is one decision. A11's floor is passed in, so the console and the
    page are counting the same set.
    """
    conn = open_catalog(ws.db_path)
    try:
        summaries = repo.clusters_summary(conn, floor)
    finally:
        conn.close()
    summary.named = sum(1 for row in summaries if row.person_id is not None)
    summary.remaining = sum(1 for row in summaries if row.person_id is None)


def faces_label_summary_lines(summary: FacesLabelSummary) -> list[str]:
    """The closing block. UX 6's done state, in the console's words, ending with `Next:`."""
    lines = [f"{summary.named:,} group(s) named, {summary.remaining:,} still unnamed."]
    if summary.named:
        step = NextStep("pam plan", "put the names you gave into the proposed filenames")
    else:
        lines.append("No one is named yet, so no filename will carry a person's name.")
        step = NextStep("pam faces label", "name the people pam found")
    lines.extend(step_lines((step,)))
    return lines


def print_faces_label_summary(summary: FacesLabelSummary) -> None:
    """The closing block on stdout, then spec 13's log path on stderr."""
    for line in faces_label_summary_lines(summary):
        click.echo(line)
    if summary.log_path is not None:
        click.echo(f"Log: {summary.log_path}", err=True)
