"""The review server's job: one long piece of work on its own thread (M14 Task 5, design 8.2 U11).

Reading (scan, then plan), renaming and undoing run here, ONE AT A TIME PER SERVER, so the page
stays responsive while they run. This module is the registry: what is running, how far it has
got, whether it may be stopped, and what happens when it dies. The routes that start a job and
report it (`POST /api/job/read`, `GET /api/job`, `POST /api/job/cancel`) are Task 5d's; they call
`start`, `snapshot` and `cancel` below.

THE JOB NEVER USES `server.connection()`. Its body (`scan_locked`, `plan_locked`, and Task 6's
`apply_locked` / `undo_locked`) opens its OWN catalog connection under the workspace lock the
server already holds, proved by the server's `HeldWorkspace`. The rename and undo bodies live in
`webui/rename_jobs.py`, U19's one door, cut from this module by M15 Task 4 (`m14.md` C5); the
registry below runs them and heals one that dies, so the join-then-recover stays here. A job
takes `server.db_lock` at most once, at its very end and holding nothing else, to publish what it
changed (the read job's new plan id, Task 5d).

THE ETA (`eta_s`, Task 5d) is spec 8.1's (`enrich/eta.py`), measured on `clock` - injectable, because
a real clock across near-instant test work reads 0.0 on the hosted runner. Each `progress` call times
the files since the last one; a scan has no `cost_estimate_ms`, so there is no prior to show, and
`eta_s` is None until `PRIOR_FILES` files have been measured, and whenever no job is running.

WHILE A JOB RUNS, EVERY WRITE IS REFUSED (U11): 409 `pam is busy <doing>. Try again when it
finishes.` and nothing is written. Two checks, and both are needed:

1. `gate`, before a write's handler runs and OUTSIDE the DB lock - so a write whose handler never
   calls `connection()` is refused too;
2. `check_in_lock`, INSIDE `connection()`'s DB lock, where the binding is re-checked too. The first
   check alone leaves a window: a job may start between it and the write. It cannot start while a
   write holds the DB lock, because `start` takes that lock to begin a job. So the second check and
   the write are atomic with respect to every job start, and a write that passes it cannot overlap
   a job.

Exempt: every GET, and the routes in `JOB_EXEMPT` (`POST job/cancel`, which only sets an event).

A JOB THAT DIES (U11). When the job thread ends by any `BaseException` - a real failure, or a
`PAM_FAULT` `crash:` checkpoint, which raises `SystemExit` - the registry records `failed` and that
the death is UNRECOVERED. `SystemExit` raised in a thread ends THAT THREAD, not the process:
`threading` hands an uncaught exception to `threading.excepthook`, whose default ignores
`SystemExit` silently and prints any other to stderr. The job catches it before that, so nothing is
printed; the session log gets the traceback. The NEXT request of any kind then, before anything
else (`settle`):

1. JOINS the thread - it has already ended, so the join returns at once;
2. runs spec 7.2 `run_recovery` with a fresh connection, under the lock the server still holds,
   while holding the DB lock so no request reads or writes half-healed state;
3. records recovery's own line (U12, `recover.recovery_line`) for the page;
4. clears the failure (the state stays `failed`, so the page can say so; it is no longer pending).

THE JOIN COMES FIRST, AND J10 IS WHY: a plan recovery finds in `applying` belongs to a process that
is gone. Inside a live server that is true only of a JOINED job thread. A write never runs before
this: every write passes `check_in_lock`, which heals first. If recovery itself raises, the failure
stays pending, the request that tried gets the error as a 500 (logged, and its handler never ran),
and the next request tries recovery again - so no write is served over an unhealed death.

THE LOCKS. The registry's own `_lock` guards its state: request threads read it and the job thread
writes it. It cannot deadlock against the server's `db_lock` because the ORDER is fixed: `db_lock`
first, then `_lock`, never the reverse. The job thread takes only `_lock` (never `db_lock`), and
holds it for an assignment at a time; nothing waits for anything while holding `_lock` - in
particular the join in `close` and `_heal` happens with `_lock` released.

ONE LOG (U23, ruling C5-5). The job body is called with the server's logger and never calls
`open_session_log` - that clears the `pam` logger's handlers, which would end the server's own log
(M628a). `job start: <kind> session=<n>` and `job end: <state>` bracket every job; `<n>` is this
server's job counter, from 1, never reused (the log file already names the page session, so the
counter is what tells one job from the next inside it).
"""

import logging
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from pam.catalog.db import connect
from pam.enrich.eta import PRIOR_FILES, Eta
from pam.errors import PamError
from pam.execute.recover import recovery_line, run_recovery
from pam.lock import HeldWorkspace
from pam.webui.api import CONFLICT_STATUS, ApiError

if TYPE_CHECKING:  # pragma: no cover - the type only
    from pam.paths import Workspace

# U11's busy refusal, with the running job's own words.
BUSY = "pam is busy {doing}. Try again when it finishes."

# Each kind of job and U11's words for it. Only these may be started.
READ = "read"
RENAME = "rename"
UNDO = "undo"
DOING = {READ: "reading your photos", RENAME: "renaming", UNDO: "putting names back"}
# U11: only reading may be stopped (between files). Planning runs to its commit; renaming and
# undoing run to the end of the plan.
CANCELLABLE = frozenset({READ})

IDLE = "idle"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

# Writes that are served while a job runs: stopping it is the one thing a page may ask then.
JOB_EXEMPT = frozenset({("POST", "job/cancel")})


@dataclass(frozen=True)
class Finished:
    """What a job body returns: the sentences the page shows, whether it stopped early, and the
    rename session it wrote (a rename or an undo; None for a read and for one that wrote none)."""

    lines: tuple[str, ...] = ()
    stopped: bool = False
    session: str | None = None
    # The run's own counts for the Done screens (the controller's ruling R6-2, M14 Task 6): a
    # rename's `{renamed, failed, not_approved, elapsed_s}`, an undo's `{restored, failed,
    # already_undone}`, from the summary the CLI renders. None for a read.
    result: dict[str, float] | None = None


Progress = Callable[[int, int], None]
ShouldStop = Callable[[], bool]
# `fn(held, progress, should_stop, logger)`: run under `held`, report `(done, total)` to
# `progress`, check `should_stop()` between files, log to `logger`. None means "done, no lines".
JobBody = Callable[[HeldWorkspace, Progress, ShouldStop, logging.Logger], Finished | None]


class JobServer(Protocol):
    """What the registry needs of its server (`ReviewServer`; `held` is `StartServer`'s)."""

    db_lock: Any  # threading.Lock
    logger: logging.Logger

    @property
    def workspace(self) -> "Workspace": ...

    def elide(self, text: str) -> str: ...


@dataclass
class _Job:
    """One job's record. Read and written only under `JobRegistry._lock`."""

    number: int
    kind: str
    thread: threading.Thread
    held: HeldWorkspace
    state: str = RUNNING
    done: int = 0
    total: int = 0
    lines: list[str] = field(default_factory=list)
    session: str | None = None
    result: dict[str, float] | None = None
    # The ETA (module docstring): its EWMA, and when the last measured file ended.
    eta: Eta = field(default_factory=lambda: Eta(prior_ms=0))
    ticked_at: float = 0.0


class JobRegistry:
    """One server's job: at most one, running or remembered. See the module docstring."""

    def __init__(self, server: JobServer) -> None:
        self._server = server
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._job: _Job | None = None
        self._count = 0
        # True between a job's death and the recovery that heals it.
        self._unrecovered = False
        # Recovery's own line after a job died (U12), or None.
        self._recovery: str | None = None
        # Whether THIS thread's current request is a write (set by `gate`, read in the DB lock).
        self._local = threading.local()
        # The ETA's clock (module docstring). Tests inject one; nothing else may read time here.
        self.clock: Callable[[], float] = time.monotonic

    # --- what the page and the server ask ------------------------------------------------------

    def moving(self) -> bool:
        """Whether a RENAME or UNDO job is running now: files are being moved (U11, Step 8)."""
        with self._lock:
            job = self._job
            return job is not None and job.state == RUNNING and job.kind in (RENAME, UNDO)

    def busy_with(self) -> str | None:
        """The running job's U11 words, or None when no job is running."""
        with self._lock:
            job = self._job
            return DOING[job.kind] if job is not None and job.state == RUNNING else None

    def snapshot(self) -> dict[str, Any]:
        """`GET /api/job`'s body (U11, plus U12's `recovery`):

        `{kind: str|None, state: idle|running|done|failed|cancelled, done: int, total: int,
        eta_s: float|None, lines: [str], recovery: str|None, session: str|None,
        result: {str: number}|None}`. `kind` is
        `read`/`rename`/`undo`; `done`/`total` are the last progress the body reported (a rename's
        and an undo's too, R6-1); `result` the run's counts (R6-2, `Finished.result`); `eta_s` the
        seconds left, to one decimal (module docstring); `lines` what it returned; `recovery`
        recovery's own line after a job died (None until that recovery has run, and when it found
        nothing); `session` the rename session a finished rename or undo wrote (Task 6b), the id
        the page's `GET /api/session/{id}` takes.
        """
        with self._lock:
            job = self._job
            if job is None:
                return {
                    "kind": None,
                    "state": IDLE,
                    "done": 0,
                    "total": 0,
                    "eta_s": None,
                    "lines": [],
                    "recovery": None,
                    "session": None,
                    "result": None,
                }
            return {
                "kind": job.kind,
                "state": job.state,
                "done": job.done,
                "total": job.total,
                "eta_s": _eta_s(job),
                "lines": list(job.lines),
                "recovery": self._recovery,
                "session": job.session,
                "result": job.result,
            }

    def start(self, kind: str, fn: JobBody) -> int:
        """Start `fn` as this server's job; its number. 409 U11's sentence if one is running.

        Takes the DB lock to begin, so no job starts while a write holds it (module docstring).
        A dead job is healed first. Call it from a route handler, NEVER inside `connection()`
        (the DB lock is not re-entrant). The body runs under the server's `held` proof (read
        inside the DB lock, where a bind changes it); a server with none is a caller's bug.
        """
        if kind not in DOING:
            raise ValueError(f"pam has no job called {kind!r}")
        with self._server.db_lock:
            self.check_in_lock(write=True)
            held: HeldWorkspace | None = getattr(self._server, "held", None)
            if held is None or not held.held:
                raise RuntimeError("a job needs the bound folder's held workspace lock")
            with self._lock:
                self._count += 1
                self._stop.clear()
                self._recovery = None
                thread = threading.Thread(
                    target=self._run,
                    args=(self._count, kind, fn, held, self._server.logger),
                    name=f"pam-job-{self._count}",
                    # A Ctrl+C that ends the process ends the job with it: a crash, healed by
                    # the next command's recovery (U11).
                    daemon=True,
                )
                self._job = _Job(number=self._count, kind=kind, thread=thread, held=held)
                self._job.ticked_at = self.clock()
                thread.start()
                return self._count

    def cancel(self) -> bool:
        """Ask a READING job to stop between files. True if asked.

        A job that cannot be stopped (renaming, undoing - U11) is IGNORED: nothing is set and the
        answer is False, as it is when no job runs. The page offers no Stop for those.
        """
        with self._lock:
            job = self._job
            if job is None or job.state != RUNNING or job.kind not in CANCELLABLE:
                return False
            self._stop.set()
            return True

    def close(self) -> None:
        """For `server_close`: stop a reading job and WAIT for any job to end.

        The workspace lock must never be released under a running job, so the caller releases it
        only after this returns. A reading job stops between files; a renaming or undoing job runs
        to its end (U11). A dead job is healed by the caller's `heal` under the DB lock.
        """
        with self._lock:
            job = self._job
            if job is not None and job.state == RUNNING and job.kind in CANCELLABLE:
                self._stop.set()
        if job is not None:
            job.thread.join()

    # --- the busy refusal and join-then-recover ------------------------------------------------

    def settle(self) -> None:
        """At the start of every request: heal a dead job before anything else (module docstring)."""
        with self._lock:
            pending = self._unrecovered
        if pending:
            with self._server.db_lock:
                self.heal()

    def gate(self, write: bool) -> None:
        """Before a route's handler, OUTSIDE the DB lock: refuse a write while a job runs."""
        self._local.write = write
        if write:
            self._refuse_if_running()

    def check_in_lock(self, write: bool | None = None) -> None:
        """INSIDE the DB lock (the caller holds it): heal a dead job, then refuse a write while a
        job runs. `write` None means "what `gate` recorded for this thread's request"."""
        self.heal()
        if write if write is not None else getattr(self._local, "write", False):
            self._refuse_if_running()

    def heal(self) -> None:
        """Join a dead job, then recover under the held lock. The caller holds the DB lock."""
        with self._lock:
            job = self._job
            if not self._unrecovered or job is None:
                return
        job.thread.join()  # it has ended: `_unrecovered` is set as its last act
        ws = job.held.require(self._server.workspace)
        conn = connect(ws.db_path)
        try:
            summary = run_recovery(conn, ws.archive_root, self._server.logger)
            conn.commit()
        finally:
            conn.close()
        with self._lock:
            self._recovery = recovery_line(summary)
            self._unrecovered = False

    def _refuse_if_running(self) -> None:
        doing = self.busy_with()
        if doing is not None:
            raise ApiError(CONFLICT_STATUS, BUSY.format(doing=doing))

    # --- the job thread ------------------------------------------------------------------------

    def _run(
        self, number: int, kind: str, fn: JobBody, held: HeldWorkspace, logger: logging.Logger
    ) -> None:
        """The thread's body: bracket, run, record. Catches `BaseException` (module docstring)."""
        logger.info("job start: %s session=%s", kind, number)
        state = FAILED
        lines: tuple[str, ...] = ()
        session: str | None = None
        result: dict[str, float] | None = None
        try:
            finished = fn(held, self._progress, self._stop.is_set, logger) or Finished()
            lines, session, result = finished.lines, finished.session, finished.result
            state = CANCELLED if finished.stopped else DONE
        except PamError as refusal:
            # A refusal is a sentence for the page, as the CLI prints it (spec 13): the job's
            # failure line (Task 5h review 5h-F5). Task 6's apply and undo refuse this way.
            lines = (str(refusal),)
            logger.error("job refused: %s", self._server.elide(str(refusal)))
        except BaseException as died:
            # S2-F1 (Task 6 Stage 2): a rename that dies after it began still names its session
            # (`renaming` tags the exception), so the page can offer what recovery left undoable.
            session = getattr(died, "pam_session", None)
            logger.error("job failed: %s", self._server.elide(traceback.format_exc()))
        # `job end` is written BEFORE the state is published, so whoever sees the state can read it.
        logger.info("job end: %s", state)
        with self._lock:
            job = self._job
            if job is not None and job.number == number:
                job.state = state
                job.lines = list(lines)
                job.session = session
                job.result = result
            if state == FAILED:
                self._unrecovered = True

    def _progress(self, done: int, total: int) -> None:
        now = self.clock()
        with self._lock:
            job = self._job
            if job is not None:
                files = done - job.done
                if files > 0:
                    for _ in range(files):
                        job.eta.observe((now - job.ticked_at) * 1000.0 / files)
                    job.ticked_at = now
                job.done, job.total = done, total


def _eta_s(job: _Job) -> float | None:
    """Seconds left, to one decimal: `eta.py`'s formula over one worker, once it has data."""
    if job.state != RUNNING or job.eta.observed < PRIOR_FILES:
        return None
    return round(job.eta.remaining_ms(job.total - job.done, workers=1) / 1000.0, 1)
