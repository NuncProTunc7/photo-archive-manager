"""`pam start`'s job routes (design 8.2 U11, U18; M14 Task 5d): read, report, stop.

    GET  /api/job          -> the job's snapshot (U11, U12): idle when there is none   [unbound ok]
    POST /api/job/read     -> start READING: scan, then plan - one job, two phases
    POST /api/job/cancel   -> ask a running read to stop between files; served while busy

Registered by `StartServer` only (`webui/launcher.py`), beside `bind`: `pam review`'s server never
starts a job, and its route table is unchanged.

THE READ JOB CALLS THE LOCK-HELD BODIES, NEVER THE COMMANDS. `scan_locked`, then `plan_locked`,
under the server's `HeldWorkspace`, writing into the server's ONE log (U23). `run_scan` / `run_plan`
would take the lock the server already holds, and open their own session log - which clears the
`pam` logger's handlers and so ends the server's (M628a; 5c concern 4).

THE PHASES. The job's `done` / `total` are the SCAN's: one tick per file it reads (`_scan`'s
`progress`). Planning reports no progress of its own - it is not cancellable and takes a fraction of
a second at 100,000 files (U11) - so while it runs the counts stay at the scan's last `done == total`.

THE RULINGS (the controller's Task 5 rulings and this slice's, pending the design model at the M14
close - design 8.2 "Jobs" records them):

- **A stopped scan never plans** (C5-4): the job ends `cancelled`, with U11's sentence and the
  job's own counts. The next read finishes the scan, then plans.
- **A read that read nothing still plans** (5d). U21 and Step 6 say reading IS scan, then plan, and
  `Nothing new to read.` is said about the SCAN (U11's P2 sentence). Planning is what makes a plan
  when none is open, and what lets a console `pam enrich` reach the names; it supersedes an open
  plan exactly as the console's `pam plan` does.
- **At the end the server's `plan_id` is the plan this read made** (None when it planned nothing),
  set under `db_lock`, and the look-alike memory is dropped - every plan id's, since the new plan's
  is computed on its first request (U11: caches that outlive a plan are keyed by plan id). Split's
  memory is NOT plan-keyed (it is keyed by cluster and
  token, and a read writes no face row), so it stays.
- **A chosen folder is bound by its first read** (U10: "Pressing it is the bind: pam creates the
  workspace, takes the lock and records the archive pointer in `run_scan`'s own order, then reads").
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any, cast

from pam.catalog import repo
from pam.catalog.db import connect
from pam.errors import PamError
from pam.lock import HeldWorkspace
from pam.naming.planner import plan_locked
from pam.naming.template import parse_template
from pam.scan import exiftool
from pam.scan.runner import scan_locked
from pam.webui import lookalikes
from pam.webui.api import CONFLICT_STATUS, ApiError, ApiRequest
from pam.webui.decide_api import _refuse_unknown_names
from pam.webui.jobs import READ, Finished

if TYPE_CHECKING:  # pragma: no cover - import for typing only, never at runtime
    import logging

    from pam.webui.launcher import StartServer
    from pam.webui.server import ReviewServer

# U11, word for word, with the job's own numbers.
STOPPED = "Stopped. pam read {done:,} of {total:,}; the rest will be read next time."
# U11's P2 sentence: the scan read 0 files.
NOTHING_NEW = "Nothing new to read."
# U21, word for word: said when the catalog holds no `place` facts after the read.
ENRICH_IN_CONSOLE = "Places and people come from pam enrich, in the console."


def api_job(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """`GET /api/job`: U11's `{kind, state, done, total, eta_s, lines}` plus U12's `recovery`."""
    return server.jobs.snapshot()


def api_job_cancel(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """`POST /api/job/cancel`: `{"stopping": true}` when a running READ was asked to stop.

    `false` when nothing was asked: no job, a finished one, or one that cannot be stopped
    (renaming, undoing - U11; 5c's ruling: ignored, nothing set).
    """
    _refuse_unknown_names(request.body, ())
    return {"stopping": server.jobs.cancel()}


def api_job_read(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """`POST /api/job/read`: start reading. `{job, binding, folder}`.

    The body and exiftool (`run_scan` refuses early too, spec 5.3) are asked BEFORE a workspace
    is created. A chosen folder is then bound (U10), which can itself refuse (another pam holds
    its lock); the template is parsed before the job starts (`run_plan` parses it before any
    state is touched). `binding` is the number the page sends from now on: binding a chosen
    folder counts a new one.
    """
    _refuse_unknown_names(request.body, ())
    start = cast("StartServer", server)
    try:
        command = start.exiftool_command or exiftool.default_command()
    except PamError as refusal:
        raise ApiError(CONFLICT_STATUS, str(refusal)) from None
    if not start.bound:
        start.bind_chosen()
    template = start.config.template
    try:
        order = parse_template(template)
    except PamError as refusal:
        raise ApiError(CONFLICT_STATUS, str(refusal)) from None
    body = partial(_read, start, ReadPlan(command, template, order))
    number = start.jobs.start(READ, body)
    return {
        "job": number,
        "binding": start.binding,
        "folder": str(start.workspace.archive_root),
    }


@dataclass(frozen=True)
class ReadPlan:
    """What the read job needs that was settled before it started."""

    command: list[str]
    template: str
    order: tuple[str, ...]


def _read(
    server: "StartServer",
    what: ReadPlan,
    held: HeldWorkspace,
    progress: Callable[[int, int], None],
    should_stop: Callable[[], bool],
    logger: "logging.Logger",
) -> Finished:
    """The job: scan, then (unless the scan stopped) plan, then publish the plan (module doc)."""
    ws = server.workspace
    root, config = ws.archive_root, server.config
    counts = [0, 0]

    def tick(done: int, total: int) -> None:
        counts[:] = [done, total]
        progress(done, total)

    scan = scan_locked(
        held, root, config, what.command, logger, progress=tick, should_stop=should_stop
    )
    if scan.stopped:
        return Finished(lines=(STOPPED.format(done=counts[0], total=counts[1]),), stopped=True)
    plan = plan_locked(held, root, config, what.template, what.order, logger)
    lines = [NOTHING_NEW] if scan.read == 0 else []
    conn = connect(ws.db_path)
    try:
        if not repo.live_place_names(conn):
            lines.append(ENRICH_IN_CONSOLE)
    finally:
        conn.close()
    with server.db_lock:
        server.plan_id = plan.plan_id
        lookalikes.forget(server)
    return Finished(lines=tuple(lines))


# The route that, on a chosen folder, is also its bind (`StartServer.serves_unbound`).
READ_ROUTE = ("POST", "job/read")

JOB_HANDLERS = {
    ("GET", "job"): api_job,
    READ_ROUTE: api_job_read,
    ("POST", "job/cancel"): api_job_cancel,
}
