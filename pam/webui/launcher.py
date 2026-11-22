"""`pam start`'s server: binding the review server to a folder, and changing folder (U10).

A server `pam start` starts with no folder is UNBOUND (`server.py`): no workspace, no lock, no
configuration. `POST /api/bind {path}` is how the page chooses one. This module owns that route
and the state it leaves behind - the lock the server holds, the folder chosen but not yet read,
and recovery's line for the page (U12). `run_start` (slice 4c) builds the server with
`start_server` and serves it.

THE ORDER OF A BIND, and why each step sits where it does (design 8.2, U10):

1. **Heal a job that died, then refuse while a job runs** (U11): changing folder under a running
   scan would pull the catalog out from under it, and a dead job's crash must be recovered under
   the lock it ran under before that lock is released. `StartServer.busy_with` answers from the
   job registry (`webui/jobs.py`).
2. **Release the old lock FIRST** (`WorkspaceLock.__exit__`), then detach. A server never holds
   two archives' locks, and from this moment a second console may work on the old folder.
3. **Refuse a network location (U13), then resolve the folder the way `--archive` is resolved**
   (`paths.resolve_archive`, reused, never copied), then refuse again on the resolved path. The
   refusal is `folders.network_refusal`, the chooser's own, through `_network_refusal`; it runs
   on the path AS GIVEN first because resolving a share would already reach the network.
4. **A never-scanned folder is CHOSEN, NOT BOUND**: no workspace is created and no lock is taken -
   only a scan creates a workspace (N4 as U10 restates it). The page offers `Start reading`, and
   PRESSING IT IS THE BIND (U10; `bind_chosen`, called by `POST /api/job/read`): the workspace is
   created, its lock taken and the archive pointer written under it, in `run_scan`'s own order,
   then step 5 as for any scanned folder.
5. **A scanned folder**: take its workspace lock, then `migrate`, `run_recovery`, commit, close -
   `pam review`'s own order - resolve the open plan (none is fine), and only then `_attach`.

A FAILED BIND LEAVES THE SERVER UNBOUND, NEVER HALF-BOUND. Everything after step 2 that can fail
does so before `_attach`, and any failure after the lock is taken releases it again. So a refusal
leaves no workspace, no configuration and no lock - the old folder's included, released in step 2.

ONE LOG PER BOUND FOLDER (U23, ruling C5-5). An unbound server has no workspace, so no log: its
logger writes nothing. The server's ONE log opens at bind, right after the lock is taken and before
recovery, in the bound folder's logs dir (`open_session_log(..., suffix="start")`); it closes when
the server changes folder (a new one opens in the new folder) and at `server_close`. Every request
line and every job's `job start` / `job end` lines land in it. `run_start` ends with a `Log:` line
on stderr for each log the session opened (spec 13), the last of them last.

THE WHOLE BIND HOLDS `server.db_lock`, the lock every request's `connection()` takes, so no
request reads a half-changed server. `connection()` is never called inside it (the lock is not
re-entrant); recovery's connection is opened directly, as `run_review` opens its own.

`pam review` does not come through `bind`. Since M14 Task 6c (ruling B1) it serves this SAME
server, bound by `review_server` through `bind`'s own tail to the folder `run_review` has locked;
the lock is `run_review`'s, held for the server's whole life as before M14, and the server has no
bind route.
"""

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import click

from pam.catalog import repo
from pam.catalog.db import migrate, open_catalog
from pam.errors import CatalogTooNewError, PamError, WorkspaceLockedError
from pam.execute.recover import recovery_line, run_recovery
from pam.lock import HeldWorkspace, WorkspaceLock
from pam.paths import (
    Workspace,
    record_archive_root,
    resolve_archive,
    workspace_for,
    workspace_if_exists,
)
from pam.sessionlog import close_session_log, open_session_log
from pam.status import NextStep, step_lines
from pam.webui import _announce
from pam.webui.api import BAD_REQUEST_STATUS, CONFLICT_STATUS, ApiError, ApiRequest
from pam.webui.confirm_api import CONFIRM_HANDLERS, Nonces
from pam.webui.folders import PATH_REQUIRED, api_folders, network_refusal
from pam.webui.job_api import JOB_HANDLERS, READ_ROUTE
from pam.webui.jobs import BUSY
from pam.webui.server import (
    CHOOSE_A_FOLDER,
    FOLDER_CHANGED,
    ReviewServer,
    serve_until_interrupted,
)

# U10, word for word: the page's bind found the folder's lock held by another pam.
ANOTHER_PAM = "Another pam is working on this folder. Close it, then try again."
# Unreachable through pam (`repo.supersede_open_plans` leaves at most one `open`); said, not
# guessed - the same reasoning as `pam review`'s SEVERAL_OPEN_PLANS.
SEVERAL_OPEN_PLANS = "pam has more than one open plan for {root} ({ids})."
# U11 / Step 8: Ctrl+C while the page renames or puts names back is a CRASH, healed as one. The
# console says so in place of its closing block, and prints no counts (plan A23).
STOPPED_WHILE_RENAMING = (
    "pam stopped while renaming. Start pam again and it will put things right first."
)

BOUND = "bound"
CHOSEN = "chosen"

# The closing block's one step (Task 4 Step 6): what is left is to come back.
START_AGAIN = NextStep("pam start", "pick up where you left off")


class StartServer(ReviewServer):
    """The review server `pam start` runs: unbound until the page binds it, and it owns its lock.

    Unlike `pam review`'s, this server holds its archive's lock ITSELF (`held_lock`) - the lock
    outlives any one request and is released by the next bind or by `server_close`.
    """

    # It changes folder, so a write must say which binding its page was loaded on (R1-F1).
    rebindable = True

    def __init__(self, *, logger: logging.Logger, token: str | None = None) -> None:
        super().__init__(workspace=None, plan_id=None, logger=logger, token=token)
        self.held_lock: WorkspaceLock | None = None
        # The proof `held_lock.__enter__` returned (M14 Task 5): what a job thread passes to
        # `scan_locked` / `plan_locked`. Set and cleared together with `held_lock`.
        self.held: HeldWorkspace | None = None
        # A never-scanned folder the user chose (U10): known, not bound. Task 5 reads it.
        self.chosen: Path | None = None
        # Recovery's own sentence from the last bind, or None (U12).
        self.recovery: str | None = None
        # U23: the logger an unbound server writes to (nothing, for `pam start`'s), restored at
        # every change of folder; and the session logs this server has opened, in order.
        self._idle_logger = logger
        self.log_path: Path | None = None
        self.log_paths: list[Path] = []
        self.api_handlers[("POST", "bind")] = _api_bind
        # U13's chooser, answered bound or unbound (`server.UNBOUND_ROUTES`).
        self.api_handlers[("GET", "folders")] = api_folders
        # M14 Task 5d (U11, U18): reading, its report, and Stop (`webui/job_api.py`).
        self.api_handlers.update(JOB_HANDLERS)
        # M14 Task 6b (U6, U14, U18): confirm, rename, the session and its undo (`confirm_api.py`).
        self.api_handlers.update(CONFIRM_HANDLERS)
        self.nonces = Nonces()
        # The scanner's exiftool for the read job; None is `exiftool.default_command()`. A test seam,
        # as `run_scan`'s own `exiftool_command` is.
        self.exiftool_command: list[str] | None = None

    def busy_with(self) -> str | None:
        """What the running job is doing, in U11's words, or None when no job runs.

        Answered by the job registry (M14 Task 5): a change of folder is refused through it.
        """
        return self.jobs.busy_with()

    def serves_unbound(self, route: tuple[str, str]) -> bool:
        """`server.UNBOUND_ROUTES`, and `POST job/read` while a folder is chosen: it is the bind."""
        return super().serves_unbound(route) or (
            route == READ_ROUTE and self.chosen is not None
        )

    def bind_chosen(self) -> None:
        """U10: `Start reading` on a chosen folder binds it, CREATING its workspace."""
        bind(self, None, create=True, expect=getattr(self.sent, "binding", None))

    def server_close(self) -> None:
        """Stop serving, WAIT for a running job, heal a dead one, and only then release the lock.

        The workspace lock is never released under a running job (`JobRegistry.close`): a reading
        job is asked to stop between files, any other runs to its end (U11).
        """
        super().server_close()
        self.jobs.close()
        with self.db_lock:
            try:
                self.jobs.heal()
            finally:
                _release(self)


def start_server(logger: logging.Logger, token: str | None = None) -> StartServer:
    """An unbound server with the bind route. `run_start` (slice 4c) serves it."""
    return StartServer(logger=logger, token=token)


def _api_bind(server: ReviewServer, request: ApiRequest) -> dict[str, Any]:
    """`POST /api/bind {path}`: the route. The body is checked BEFORE anything is released."""
    text = request.body.get("path")
    path = Path(text.strip()).expanduser() if isinstance(text, str) and text.strip() else None
    # Absolute only: the server's working directory is not a folder the user chose, so a
    # relative path would resolve against something the page cannot see.
    if path is None or not path.is_absolute():
        raise ApiError(BAD_REQUEST_STATUS, PATH_REQUIRED)
    # Registered only by `StartServer.__init__`, so the server IS one.
    return bind(cast(StartServer, server), path)


def bind(
    server: StartServer, path: Path | None, *, create: bool = False, expect: int | None = None
) -> dict[str, Any]:
    """Bind `server` to the folder at `path`, or choose it if pam has never read it (U10).

    Returns `{state, archive_root, plan_id, recovery, binding, folder}`; `state` is `bound` or
    `chosen`. `binding` is the number the page sends back on every request
    (`server.BINDING_HEADER`); `folder` the bound archive root, None when chosen (W23). `create`
    is `Start reading` on a chosen folder (`bind_chosen`): the workspace is created and the
    archive pointer written under the lock, as `run_scan` does. Every refusal is an `ApiError`
    and leaves the server UNBOUND (module docstring).

    `path=None` binds the CHOSEN folder, read INSIDE the lock; `expect` is the binding the page
    sent, checked inside the lock too, so a choice made by another page between the request's
    check and this bind is refused rather than read (Task 5d concern 2). Every choice counts a
    new binding for the same reason.
    """
    with server.db_lock:
        if expect is not None and expect != server.binding:
            raise ApiError(CONFLICT_STATUS, FOLDER_CHANGED)
        if path is None:
            if server.chosen is None:
                raise ApiError(CONFLICT_STATUS, CHOOSE_A_FOLDER)
            path = server.chosen
        server.jobs.heal()
        doing = server.busy_with()
        if doing is not None:
            raise ApiError(CONFLICT_STATUS, BUSY.format(doing=doing))
        _release(server)
        refusal_text = _network_refusal(path)
        if refusal_text is not None:
            raise ApiError(CONFLICT_STATUS, refusal_text)
        try:
            root = resolve_archive(path)
        except PamError as refusal:
            raise ApiError(CONFLICT_STATUS, str(refusal)) from None
        refusal_text = _network_refusal(root)
        if refusal_text is not None:
            raise ApiError(CONFLICT_STATUS, refusal_text)
        ws = workspace_for(root) if create else workspace_if_exists(root)
        if ws is None:
            server.chosen = root
            server.binding += 1
            return {
                "state": CHOSEN,
                "archive_root": str(root),
                "plan_id": None,
                "recovery": None,
                "binding": server.binding,
                "folder": None,
            }
        lock = WorkspaceLock(ws.lock_path, ws)
        try:
            held = lock.__enter__()
        except WorkspaceLockedError:
            raise ApiError(CONFLICT_STATUS, ANOTHER_PAM) from None
        try:
            if create:
                record_archive_root(ws)  # UNDER the lock, as in `run_scan` (the M2 race fix)
            _open_log(server, ws)
            try:
                conn = open_catalog(ws.db_path)  # E1: before recovery; a refusal like any other
            except CatalogTooNewError as newer:
                raise ApiError(CONFLICT_STATUS, str(newer)) from None
            try:
                migrate(conn)
                summary = run_recovery(conn, root, server.logger)
                conn.commit()
                plan_id = _open_plan(conn, root)
            finally:
                conn.close()
            server._attach(ws)
        except BaseException:
            lock.__exit__(None, None, None)
            _close_log(server)
            raise
        _hold(server, lock, held, plan_id, recovery_line(summary))
        return {
            "state": BOUND,
            "archive_root": str(root),
            "plan_id": plan_id,
            "recovery": server.recovery,
            "binding": server.binding,
            "folder": str(root),
        }


def _hold(
    server: StartServer,
    lock: WorkspaceLock | None,
    held: HeldWorkspace,
    plan_id: int | None,
    recovery: str | None,
) -> None:
    """The tail of every bind, after `_attach`: the lock's proof, the open plan, recovery's line.

    `lock` is the lock the SERVER owns and releases (`_release`); None when its caller owns it
    and holds it for the server's whole life (`review_server`, `pam review`).
    """
    server.held_lock = lock
    server.held = held
    server.plan_id = plan_id
    server.recovery = recovery


def review_server(
    ws: Workspace,
    plan_id: int,
    logger: logging.Logger,
    held: HeldWorkspace,
    log_path: Path,
    recovery: str | None,
) -> StartServer:
    """`pam review`'s server (M14 Task 6c, ruling B1): the SAME server `pam start` runs, bound.

    U5 (adopted): `pam review` and `pam start` both start a server on which the page runs the
    whole workflow; U10: "`pam review` starts bound, as today". So `run_review` keeps its
    refusals and their order, resolves the plan, and then binds this server through `bind`'s
    own tail (`_attach`, `_hold`) to the folder whose lock IT holds - `run_review`'s lock, held
    for the server's whole life as before M14, so the server is given its proof (`held`, what a
    job runs under) and NOT the lock (`held_lock` stays None and `_release` releases nothing).

    It cannot change folder: the bind route is not served (the empty 404, as before M14) and
    `rebindable` is False, so a page and a client built before M14 still write without the
    binding header. A change of folder would release a lock this server does not own.
    """
    server = StartServer(logger=logger)
    del server.api_handlers[("POST", "bind")]
    server.rebindable = False
    server._attach(ws)
    _hold(server, None, held, plan_id, recovery)
    # The rename session's report names this log, as `run_apply`'s does (`rename_jobs.renaming`).
    server.log_path = log_path
    return server


def _network_refusal(path: Path) -> str | None:
    """U13's refusal of a UNC path or a network drive: the chooser's own (`folders`).

    Bind must not trust the page, so the chooser's refusal is applied here too.
    """
    return network_refusal(path)


def _open_plan(conn: sqlite3.Connection, root: Path) -> int | None:
    """The folder's open plan, or None - the page then offers `Start reading` (Task 5)."""
    open_ids = repo.plans_in_status(conn, "open")
    if len(open_ids) > 1:
        ids = ", ".join(str(i) for i in open_ids)
        raise ApiError(CONFLICT_STATUS, SEVERAL_OPEN_PLANS.format(root=root, ids=ids))
    return open_ids[0] if open_ids else None


def _release(server: StartServer) -> None:
    """Release the held lock (`__exit__`) FIRST, then forget the folder: back to unbound."""
    lock, server.held_lock = server.held_lock, None
    server.held = None
    if lock is not None:
        lock.__exit__(None, None, None)
    server._detach()
    server.chosen = None
    server.recovery = None
    _close_log(server)


def _open_log(server: StartServer, ws: Workspace) -> None:
    """The server's ONE log for this folder (U23): the `pam` logger, in the folder's logs dir."""
    server.logger, path = open_session_log(ws.logs_dir, suffix="start")
    server.log_path = path
    server.log_paths.append(path)


def _close_log(server: StartServer) -> None:
    """Close the folder's log, if one is open, and go back to the logger that writes nothing."""
    if server.log_path is not None:
        close_session_log(server.logger)
        server.log_path = None
    server.logger = server._idle_logger


@dataclass
class StartSummary:
    """What one `pam start` session was: its address, and the folder it was started on."""

    url: str = ""
    state: str | None = None  # `bound` / `chosen` when PATH was given, else None (Welcome)
    archive_root: Path | None = None
    # Step 8: the serve loop ended while a rename or undo job was moving files - a crash.
    stopped_while_renaming: bool = False


def run_start(
    path: Path | None = None,
    *,
    serve: Callable[[ReviewServer], None] | None = None,
    opener: Callable[[str], bool] | None = None,
) -> StartSummary:
    """`pam start [PATH]` (U9): serve the page unbound, or bound to PATH first.

    PATH is bound exactly as `POST /api/bind` binds it (`bind`, the same function), so it is
    refused with the same sentences - printed, exit 1, and nothing is served. A never-scanned
    PATH is chosen, not bound. Ctrl+C ends it: the server is CLOSED FIRST, which releases the
    folder's lock, and only then is the closing block printed.

    `serve` and `opener` are the test seams `run_review` has, resolved the same way.
    """
    summary = StartSummary()
    server = start_server(_start_logger())
    try:
        if path is not None:
            try:
                answer = bind(server, path.expanduser().absolute())
            except ApiError as refusal:
                raise PamError(refusal.message) from None
            summary.state = answer["state"]
            summary.archive_root = Path(answer["archive_root"])
        summary.url = server.url
        _announce(server.url, opener)
        serve_loop = serve_until_interrupted if serve is None else serve
        serve_loop(server)
    finally:
        # Step 8 (U11): a rename or undo job is NOT waited for and NOT stopped - Ctrl+C during one
        # is a crash. Only the listening socket closes; the lock stays held until this process
        # ends, the daemon job thread dies with it, and the next pam's recovery heals the rest.
        summary.stopped_while_renaming = server.jobs.moving()
        if summary.stopped_while_renaming:
            ReviewServer.server_close(server)
        else:
            server.server_close()
    if summary.stopped_while_renaming:
        click.echo(STOPPED_WHILE_RENAMING)
    else:
        for line in step_lines((START_AGAIN,)):
            click.echo(line)
    # spec 13: the session's log paths on stderr, last. None when no folder was ever bound.
    for path in server.log_paths:
        click.echo(f"Log: {path}", err=True)
    return summary


def _start_logger() -> logging.Logger:
    """The server's logger. pam start has no folder of its own to keep a session log in, so
    this one writes nothing (the design model's question; 4c's report)."""
    logger = logging.getLogger("pam.start")
    logger.propagate = False
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    return logger
