"""M14 Task 5c: the review server's job registry (design 8.2 U11, U12, U23; `pam/webui/jobs.py`).

One job per server, on its own thread. While it runs every write is refused with U11's sentence and
nothing is written; a job that dies is JOINED, then recovered under the lock the server still holds,
before any later request writes; and the job writes into the server's ONE session log (U23), which
`pam start` opens at bind (ruling C5-5).

EVERY WAIT HERE IS A BARRIER WITH A DEADLINE, never a `time.sleep` (CLAUDE.md, the hosted-CI
gotcha): a job body blocks on an `Event` the test controls, and a test that reads what another
thread wrote waits for that effect and fails loudly at `DEADLINE`.

THE SENTENCES ARE SPELLED FROM U11, not imported from the code under test.
"""

import json
import logging
import os
import shutil
import threading
import time
from collections.abc import Iterator
from http.client import HTTPConnection
from typing import NamedTuple

import pytest
from archives import approve_archive, catalog, journal_pairs, materialise_tiny_library, planned

from pam.catalog import repo
from pam.catalog.db import connect, migrate
from pam.errors import PamError
from pam.execute import apply as apply_module
from pam.paths import workspace_for, workspace_if_exists
from pam.scan import exiftool
from pam.scan.runner import scan_locked
from pam.webui import jobs, launcher
from pam.webui.api import ApiError
from pam.webui.jobs import Finished
from pam.webui.launcher import StartServer, start_server

DEADLINE = 30.0

# U11, word for word.
BUSY_READING = "pam is busy reading your photos. Try again when it finishes."
BUSY_RENAMING = "pam is busy renaming. Try again when it finishes."
BUSY_UNDOING = "pam is busy putting names back. Try again when it finishes."
# recovery's own sentence (`recover._announcement`), spelled - the launcher's T4.1 spells it too.
RECOVERED = "pam finished tidying up after an interrupted run: 1 rename(s) not started."

# Every POST route a `pam start` server answers today, spelled from design 8.2's API block (M7's
# writes, M11's faces writes, M14 U17's selection, U10's bind) - NOT read from the route table.
# 5d added `job/read` (refused like any write) and `job/cancel`, the one write served while a job
# runs (`JOB_EXEMPT`; not in this list).
WRITE_ROUTES = frozenset(
    {
        "bind",
        "job/read",
        "plan/{id}/decide",
        "plan/{id}/decide-filter",
        "plan/{id}/selection",
        "plan/{id}/decide-selection",
        "item/{id}/rename",
        "item/{id}/restore",
        "faces/cluster/{id}/label",
        "faces/faces/state",
        "faces/faces/assign",
        "faces/person/{id}",
        "faces/person/{id}/merge",
        "faces/cluster/{id}/dismiss",
        "faces/cluster/{id}/split",
        # M14 Task 6b (U14, U18): the two routes that move files.
        "plan/{id}/apply",
        "session/{id}/undo",
    }
)
assert WRITE_ROUTES, "an empty route list would make the sweep below check nothing"

needs_exiftool = pytest.mark.skipif(
    shutil.which("exiftool") is None, reason="the tiny library is pinned against REAL exiftool"
)
# The tiny library's own count (tests/test_tiny_library.py: READ = 100), spelled here.
TINY_LIBRARY_READ = 100
CANCEL_AT = 40
LEFT_AFTER_THE_CANCEL = 60  # 100 - 40, by hand


class Reply(NamedTuple):
    status: int
    body: bytes


def _logger() -> logging.Logger:
    logger = logging.getLogger("pam.tests.webui_jobs")
    logger.propagate = False
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    return logger


@pytest.fixture
def started() -> Iterator[StartServer]:
    """`pam start`'s server, unbound and serving. Teardown stops it and releases its lock."""
    server = start_server(_logger())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=DEADLINE)


def fetch(server, path: str, method: str = "GET", body: object = None, binding=None) -> Reply:
    conn = HTTPConnection(server.host, server.port, timeout=DEADLINE)
    try:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        if binding is not None:
            headers["X-Pam-Binding"] = str(binding)
        conn.request(method, f"/t/{server.token}/api/{path}", body=data, headers=headers)
        response = conn.getresponse()
        return Reply(response.status, response.read())
    finally:
        conn.close()


def _decide(server, plan_id, item_ids, action="approve") -> Reply:
    body = {"action": action, "item_ids": item_ids}
    return fetch(server, f"plan/{plan_id}/decide", "POST", body, binding=server.binding)


def _item_ids(server, plan_id) -> list[int]:
    reply = fetch(server, f"plan/{plan_id}/items")
    assert reply.status == 200, reply
    return [item["id"] for item in json.loads(reply.body)["items"]]


def _statuses(root) -> list[str]:
    conn = catalog(root)
    try:
        return [r[0] for r in conn.execute("SELECT status FROM plan_items ORDER BY id")]
    finally:
        conn.close()


def await_state(server, *states: str) -> dict:
    """The job's snapshot, once its state is one of `states`. Fails loudly at the deadline."""
    deadline = time.monotonic() + DEADLINE
    while True:
        snap = server.jobs.snapshot()
        if snap["state"] in states:
            return snap
        if time.monotonic() > deadline:
            raise AssertionError(f"the job never reached {states}; it is {snap['state']}")
        threading.Event().wait(0.005)


def await_log(path, *fragments: str) -> str:
    """The log file's text, once every fragment is in it (the `await_log` pattern)."""
    assert fragments, "a barrier with no fragments checks nothing"
    deadline = time.monotonic() + DEADLINE
    while True:
        text = path.read_text(encoding="utf-8")
        if all(f in text for f in fragments):
            return text
        if time.monotonic() > deadline:
            missing = [f for f in fragments if f not in text]
            raise AssertionError(f"the log never got {missing}")
        threading.Event().wait(0.005)


class Blocked:
    """A job body that blocks until the test releases it, then finishes with one line."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.args: tuple = ()
        self.saw_stop: bool | None = None

    def __call__(self, held, progress, should_stop, logger):
        self.args = (held, progress, should_stop, logger)
        progress(3, 5)
        self.entered.set()
        assert self.release.wait(DEADLINE), "the test never released the job"
        self.saw_stop = should_stop()
        return Finished(lines=("finished",))

    def start(self, server, kind="read") -> None:
        server.jobs.start(kind, self)
        assert self.entered.wait(DEADLINE), "the job body never started"


def _bound(tmp_path, server, names=("IMG_1.JPG",)):
    root, plan_id = planned(tmp_path, list(names))
    launcher.bind(server, root)
    return root, plan_id


# --- Step 3: the busy refusal (T5.1, T5.2) -----------------------------------------------------


def test_a_decision_during_a_read_is_refused_and_writes_nothing(tmp_path, started, monkeypatch):
    """T5.1. A decision while a read runs is U11's 409, and the catalog write is never CALLED -
    watched on the repo call `decide` makes, not only on the rows after. Reads are still served,
    and once the job ends the same decision is served."""
    root, plan_id = _bound(tmp_path, started)
    ids = _item_ids(started, plan_id)
    writes: list[tuple] = []
    real = repo.set_plan_item_status
    monkeypatch.setattr(
        repo, "set_plan_item_status", lambda *a: writes.append(a[1:]) or real(*a)
    )
    job = Blocked()
    job.start(started)

    refused = _decide(started, plan_id, ids)
    read = fetch(started, f"plan/{plan_id}/items")

    assert (refused.status, json.loads(refused.body)) == (409, {"error": BUSY_READING})
    assert writes == []
    assert _statuses(root) == ["proposed"]
    assert read.status == 200
    job.release.set()
    await_state(started, "done")
    served = _decide(started, plan_id, ids)
    assert served.status == 200, served
    assert writes == [(ids[0], "approved")]
    assert _statuses(root) == ["approved"]


def test_a_write_that_passed_the_outside_check_is_refused_inside_the_lock(
    tmp_path, started, monkeypatch
):
    """T5.2. The window the in-lock check closes, built deterministically: the decision passes the
    check before dispatch (no job yet) and is held at the door of `connection()`; a job starts
    there; the decision then enters the DB lock and is refused, and writes nothing."""
    root, plan_id = _bound(tmp_path, started)
    ids = _item_ids(started, plan_id)
    at_the_lock, go = threading.Event(), threading.Event()
    real_connection = started.connection

    def held_at_the_door():
        at_the_lock.set()
        assert go.wait(DEADLINE), "the test never let the write in"
        return real_connection()

    monkeypatch.setattr(started, "connection", held_at_the_door)
    replies: list[Reply] = []
    writer = threading.Thread(target=lambda: replies.append(_decide(started, plan_id, ids)))
    writer.start()
    assert at_the_lock.wait(DEADLINE), "the write never reached connection()"
    job = Blocked()
    job.start(started)  # between the outside check and the write
    go.set()
    writer.join(DEADLINE)

    assert [(r.status, json.loads(r.body)) for r in replies] == [(409, {"error": BUSY_READING})]
    assert _statuses(root) == ["proposed"]
    job.release.set()
    await_state(started, "done")


def test_every_write_route_is_refused_while_a_job_runs_and_only_cancel_is_served(
    tmp_path, started, monkeypatch
):
    """Every POST route is refused BEFORE its handler is called (the outside check), so a write
    whose handler never calls `connection()` is refused too. GETs are served. `JOB_EXEMPT`'s one
    route, `job/cancel` (Task 5d's), is served and stops the read."""
    _bound(tmp_path, started)
    posts = {t for (m, t) in started.api_handlers if m == "POST"}
    assert posts == WRITE_ROUTES | {"job/cancel"}
    assert jobs.JOB_EXEMPT == frozenset({("POST", "job/cancel")})
    called: list[str] = []
    for key in list(started.api_handlers):
        real = started.api_handlers[key]
        started.api_handlers[key] = lambda s, r, k=key, f=real: called.append(k[1]) or f(s, r)
    job = Blocked()
    job.start(started)

    for template in sorted(WRITE_ROUTES):
        reply = fetch(started, template.replace("{id}", "1"), "POST", {}, started.binding)
        assert (reply.status, json.loads(reply.body)) == (409, {"error": BUSY_READING}), (
            template
        )
    assert called == []
    assert fetch(started, "ping").status == 200
    cancel = fetch(started, "job/cancel", "POST", {}, started.binding)
    assert (cancel.status, json.loads(cancel.body)) == (200, {"stopping": True})
    assert called == ["ping", "job/cancel"]
    job.release.set()
    assert await_state(started, "done")["state"] == "done"
    assert job.saw_stop is True


# --- Step 2: the registry ----------------------------------------------------------------------


def test_the_snapshot_reports_kind_state_progress_lines_and_recovery(tmp_path, started):
    idle = {
        "kind": None,
        "state": "idle",
        "done": 0,
        "total": 0,
        "eta_s": None,
        "lines": [],
        "recovery": None,
        "session": None,
        "result": None,  # R6-2: a read has no counts
    }
    assert started.jobs.snapshot() == idle
    _bound(tmp_path, started)
    job = Blocked()
    job.start(started)
    running = started.jobs.snapshot()
    assert running == {**idle, "kind": "read", "state": "running", "done": 3, "total": 5}
    job.release.set()
    done = await_state(started, "done")
    assert done == {**running, "state": "done", "lines": ["finished"]}


def test_a_job_refused_with_a_pam_error_shows_its_sentence_and_a_crash_shows_none(
    tmp_path, started
):
    """The Task 5h review's 5h-F5: a `PamError` is pam's refusal, a sentence the CLI prints
    (spec 13), so a job that raises one ends `failed` WITH that sentence as its line - the page
    draws a failed job's lines. Task 6's apply and undo refuse this way. Any other exception is a
    crash: `failed` with no line, its traceback in the log only."""
    _bound(tmp_path, started)

    def refuses(*_args):
        raise PamError("pam changed nothing. Nothing in this plan is approved.")

    started.jobs.start("rename", refuses)
    refused = await_state(started, "failed")
    assert refused["lines"] == ["pam changed nothing. Nothing in this plan is approved."]

    def crashes(*_args):
        raise RuntimeError("a crash, not a refusal")

    started.jobs.start("rename", crashes)
    crashed = await_state(started, "failed")
    assert crashed["lines"] == []


def test_the_job_body_gets_the_proof_the_logger_and_no_connection(
    tmp_path, started, monkeypatch
):
    """The body runs under the server's `HeldWorkspace`, with the server's logger; the job never
    opens a connection through `server.connection()`."""
    _bound(tmp_path, started)
    opened: list[str] = []
    real = started.connection
    monkeypatch.setattr(started, "connection", lambda: opened.append("c") or real())
    job = Blocked()
    job.start(started)
    held, _progress, should_stop, logger = job.args
    assert held is started.held and held.held
    assert logger is started.logger
    assert should_stop() is False
    job.release.set()
    await_state(started, "done")
    assert opened == []


def test_a_second_job_is_refused_while_one_runs(tmp_path, started):
    _bound(tmp_path, started)
    first = Blocked()
    first.start(started)
    with pytest.raises(ApiError) as refused:
        started.jobs.start("read", Blocked())
    assert (refused.value.status, refused.value.message) == (409, BUSY_READING)
    with pytest.raises(ApiError) as refused:
        started.jobs.start("undo", Blocked())
    assert refused.value.message == BUSY_READING
    first.release.set()
    await_state(started, "done")
    second = Blocked()
    second.start(started, "rename")  # served once the first has ended
    assert started.jobs.snapshot()["kind"] == "rename"
    second.release.set()
    await_state(started, "done")


def test_cancel_on_a_job_that_cannot_stop_is_ignored(tmp_path, started):
    """Ruling: renaming and undoing cannot be stopped (U11), so `cancel` IGNORES them - nothing
    is set and it answers False, as it does with no job at all."""
    assert started.jobs.cancel() is False
    _bound(tmp_path, started)
    for kind, sentence in (("rename", BUSY_RENAMING), ("undo", BUSY_UNDOING)):
        job = Blocked()
        job.start(started, kind)
        assert started.jobs.cancel() is False
        refused = _decide(started, 1, [1])
        assert (refused.status, json.loads(refused.body)) == (409, {"error": sentence})
        job.release.set()
        assert await_state(started, "done")["state"] == "done"
        assert job.saw_stop is False


@needs_exiftool
def test_cancel_stops_a_reading_job_between_files(tmp_path, started):
    """A read stopped at file 40 of the tiny library's 100 has read exactly 40, ends `cancelled`,
    and the next read reads exactly the other 60 (counted by hand)."""
    root = materialise_tiny_library(tmp_path / "lib").resolve()
    # An empty migrated catalog, so the folder binds (5d's `Start reading` will bind a never-read
    # folder itself); nothing in it has been read.
    ws = workspace_for(root)
    conn = connect(ws.db_path)
    try:
        migrate(conn)
    finally:
        conn.close()
    assert launcher.bind(started, root)["state"] == "bound"

    def read(cancel_at):
        def body(held, progress, should_stop, logger):
            def counted(done, total):
                progress(done, total)
                if done == cancel_at:
                    assert started.jobs.cancel() is True

            summary = scan_locked(
                held,
                root,
                started.config,
                exiftool.default_command(),
                logger,
                progress=counted,
                should_stop=should_stop,
            )
            return Finished(lines=(f"read {summary.read}",), stopped=summary.stopped)

        return body

    started.jobs.start("read", read(CANCEL_AT))
    first = await_state(started, "cancelled", "done", "failed")
    assert first == {
        "kind": "read",
        "state": "cancelled",
        "done": CANCEL_AT,
        "total": TINY_LIBRARY_READ,
        "eta_s": None,  # Task 5d: no ETA once the job has stopped running
        "lines": [f"read {CANCEL_AT}"],
        "recovery": None,
        "session": None,
        "result": None,  # R6-2: a read has no counts
    }
    started.jobs.start("read", read(None))
    second = await_state(started, "cancelled", "done", "failed")
    assert (second["state"], second["lines"]) == ("done", [f"read {LEFT_AFTER_THE_CANCEL}"])
    conn = connect(ws.db_path)
    try:
        read_files = conn.execute(
            "SELECT COUNT(*) FROM files f JOIN capture c ON c.file_id = f.id"
            " WHERE f.sha256 IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    assert read_files == TINY_LIBRARY_READ


def test_change_folder_is_refused_while_a_job_runs_through_busy_with(tmp_path, started):
    """Rebind while a job runs: refused with U11's words, by the route (the outside check) and by
    `bind` itself (`busy_with`, inside the DB lock). Nothing moves."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a, _ = _bound(tmp_path / "a", started)
    b, _ = planned(tmp_path / "b", ["IMG_2.JPG"])
    job = Blocked()
    job.start(started)
    assert started.busy_with() == "reading your photos"

    reply = fetch(started, "bind", "POST", {"path": str(b)}, started.binding)
    with pytest.raises(ApiError) as refused:
        launcher.bind(started, b)

    assert (reply.status, json.loads(reply.body)) == (409, {"error": BUSY_READING})
    assert (refused.value.status, refused.value.message) == (409, BUSY_READING)
    assert started.workspace.archive_root == a.resolve()
    assert workspace_if_exists(b) is not None and not workspace_if_exists(b).lock_path.exists()
    job.release.set()
    await_state(started, "done")
    assert started.busy_with() is None


def test_server_close_waits_for_a_running_read_before_releasing_the_lock(tmp_path):
    """Ruling: `server_close` asks a reading job to stop and WAITS for it; the lock is released
    only after the job has ended - never under it."""
    server = start_server(_logger())
    root, _ = _bound(tmp_path, server)
    lock_path = workspace_if_exists(root).lock_path
    saw_stop, release = threading.Event(), threading.Event()

    def body(held, progress, should_stop, logger):
        deadline = time.monotonic() + DEADLINE
        while not should_stop():
            assert time.monotonic() < deadline, "the stop never came"
            threading.Event().wait(0.005)
        saw_stop.set()
        assert release.wait(DEADLINE)
        return Finished(stopped=True)

    server.jobs.start("read", body)
    closer = threading.Thread(target=server.server_close)
    closer.start()
    assert saw_stop.wait(DEADLINE), "server_close never asked the read to stop"

    assert lock_path.read_text(encoding="ascii") == str(os.getpid())
    assert closer.is_alive()
    release.set()
    closer.join(DEADLINE)
    assert not closer.is_alive()
    assert not lock_path.exists()
    assert server.jobs.snapshot()["state"] == "cancelled"


# --- Step 4: join-then-recover (T5.4) ----------------------------------------------------------


def _dies_mid_rename(root, plan_id):
    """A job body that crashes the way `pam apply` does at `crash:after_intent:1`: the journal
    holds an `intent` row for a real rename and the plan is `applying`, then `SystemExit`.

    Apply is not a job until Task 6b, so this drives `execute/apply.py::apply_locked` - the body
    of `run_apply` inside its lock (`_apply` until M14 Task 6a) - under the SERVER's held lock,
    with a connection of its own."""

    def body(held, progress, should_stop, logger):
        ws = held.require(workspace_if_exists(root))
        conn = connect(ws.db_path)
        try:
            summary = apply_module.ApplySummary(archive_root=ws.archive_root, plan_id=plan_id)
            clock = apply_module._Clock()
            apply_module.apply_locked(
                held, conn, ws.archive_root, plan_id, summary, logger, None, clock, False
            )
        finally:
            conn.close()

    return body


def _intent_rows(root):
    conn = catalog(root)
    try:
        return journal_pairs(conn, "intent")
    finally:
        conn.close()


def test_a_job_that_dies_is_recovered_before_the_next_write(
    tmp_path, started, set_fault, monkeypatch
):
    """T5.4. A job dies with `SystemExit` after its intent row. The thread ends, the process does
    not, and nothing reaches `threading.excepthook`. The next write's handler runs only after
    `run_recovery` has run and resolved the row; U12's recovery line is recorded."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    approve_archive(root, plan_id)
    launcher.bind(started, root)
    hooked: list = []
    monkeypatch.setattr(threading, "excepthook", lambda args: hooked.append(args.exc_type))
    set_fault.arm("crash:after_intent:1")

    started.jobs.start("rename", _dies_mid_rename(root, plan_id))
    dead = await_state(started, "failed")
    assert set_fault.hits("after_intent") == 1
    set_fault.next_invocation()  # the counter this test owns, back to a fresh process's
    assert hooked == []
    assert dead["recovery"] is None
    assert _intent_rows(root), "the crash must leave an intent row to heal"

    order: list[tuple] = []
    real_recovery = jobs.run_recovery
    monkeypatch.setattr(
        jobs, "run_recovery", lambda *a: order.append(("recover",)) or real_recovery(*a)
    )
    key = ("POST", "plan/{id}/decide")
    real_decide = started.api_handlers[key]

    def decide(server, request):
        order.append(("write", _intent_rows(root)))
        return real_decide(server, request)

    started.api_handlers[key] = decide
    _decide(started, plan_id, [1], "skip")

    assert order == [("recover",), ("write", [])]
    assert started.jobs.snapshot()["recovery"] == RECOVERED
    assert started.jobs.snapshot()["state"] == "failed"
    conn = catalog(root)
    try:
        assert repo.plan_status(conn, plan_id) == "partially_applied"
    finally:
        conn.close()
    fetch(started, "ping")
    assert order == [("recover",), ("write", [])], "recovery runs once per death"


def test_the_dead_job_s_thread_is_joined_before_recovery_runs(
    tmp_path, started, set_fault, monkeypatch
):
    """T5.4's join (J10). The dead job's thread is held alive after it has recorded its death; the
    next request's recovery must wait for the JOIN. Recorded deterministically: the join releases
    the thread, and recovery records whether the thread is still alive when it runs."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    approve_archive(root, plan_id)
    launcher.bind(started, root)
    linger = threading.Event()
    real_run = started.jobs._run

    def lingering(*args):
        real_run(*args)
        assert linger.wait(DEADLINE)

    monkeypatch.setattr(started.jobs, "_run", lingering)
    set_fault.arm("crash:after_intent:1")
    started.jobs.start("rename", _dies_mid_rename(root, plan_id))
    await_state(started, "failed")
    set_fault.next_invocation()
    thread = started.jobs._job.thread
    assert thread.is_alive()
    order: list[str] = []
    real_join = thread.join

    def join(timeout=None):
        order.append("join")
        linger.set()
        real_join(timeout)

    thread.join = join
    real_recovery = jobs.run_recovery

    def recovery(*a):
        order.append("recover, thread alive" if thread.is_alive() else "recover")
        return real_recovery(*a)

    monkeypatch.setattr(jobs, "run_recovery", recovery)
    try:
        assert fetch(started, "ping").status == 200
    finally:
        linger.set()
    assert order == ["join", "recover"]
    assert _intent_rows(root) == []


def test_systemexit_in_a_thread_ends_the_thread_not_the_process(monkeypatch):
    """What U11 relies on: `threading` hands `SystemExit` to `threading.excepthook` (whose default
    ignores it silently) and the thread ends; this process - the one running the test - lives."""
    seen: list = []
    monkeypatch.setattr(threading, "excepthook", lambda args: seen.append(args.exc_type))

    def crash():
        raise SystemExit("PAM_FAULT: crashed at after_intent hit 1")

    thread = threading.Thread(target=crash)
    thread.start()
    thread.join(DEADLINE)
    assert not thread.is_alive()
    assert seen == [SystemExit]


# --- Step 5: one log (U23, C5-5; T5.5) --------------------------------------------------------


def test_a_job_writes_into_the_server_s_one_log_and_leaves_its_handler(tmp_path, started):
    """T5.5. A request logged during a job lands in the server's log; `job start` / `job end`
    bracket the job, in order; the `pam` logger's handler is the same object after the job."""
    _bound(tmp_path, started)
    pam_logger = logging.getLogger("pam")
    assert started.logger is pam_logger
    handlers = list(pam_logger.handlers)
    assert len(handlers) == 1
    job = Blocked()
    job.start(started)
    assert fetch(started, "ping").status == 200
    # The server logs a request AFTER it responds, so the reply can arrive before the line is
    # written (CLAUDE.md, hosted CI): wait for it before releasing the job, or `job end` can win
    # the race (hosted run 44967762026, 2026-11-22).
    await_log(started.log_path, "review GET /t/<token>/api/ping -> 200")
    job.release.set()
    await_state(started, "done")

    text = await_log(
        started.log_path,
        "job start: read session=1",
        "review GET /t/<token>/api/ping -> 200",
        "job end: done",
    )
    start = text.index("job start: read session=1")
    ping = text.index("review GET /t/<token>/api/ping -> 200")
    assert start < ping < text.index("job end: done")
    assert list(pam_logger.handlers) == handlers
    assert pam_logger.handlers[0] is handlers[0]
    assert started.logger is pam_logger


def test_pam_start_s_log_opens_at_bind_moves_with_the_folder_and_closes_at_server_close(
    tmp_path,
):
    """C5-5: unbound, no log; bind opens one in the bound folder's logs dir (`_start`); a change of
    folder closes it and opens one in the new folder; `server_close` closes it."""
    idle = _logger()
    server = start_server(idle)
    assert (server.log_path, server.log_paths, server.logger) == (None, [], idle)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    # Both planned BEFORE any bind: `pam scan` / `pam plan` in this process open their own
    # session logs on the shared `pam` logger, which would replace the server's handler.
    a, _ = planned(tmp_path / "a", ["IMG_1.JPG"])
    b, _ = planned(tmp_path / "b", ["IMG_2.JPG"])
    launcher.bind(server, a)
    first = server.log_path
    assert first.parent == workspace_if_exists(a).logs_dir
    assert first.name.endswith("_start.log") and first.exists()
    handler = logging.getLogger("pam").handlers[0]
    assert handler.baseFilename == str(first)

    launcher.bind(server, b)
    second = server.log_path
    assert handler.stream is None  # a's handler is closed
    assert second.parent == workspace_if_exists(b).logs_dir
    assert server.log_paths == [first, second]

    server.server_close()
    assert server.log_path is None
    assert logging.getLogger("pam").handlers == []
    assert server.logger is idle


def test_an_unbound_pam_start_prints_no_log_line(monkeypatch, capsys):
    """C5-5's one exception to spec 13: a `pam start` that never binds has no workspace and so no
    log, and prints no `Log:` line. (`pam start PATH` is driven by test_reporter's sweep.)"""
    from pam.cli import main

    monkeypatch.setattr("webbrowser.open", lambda url: True)
    monkeypatch.setattr(launcher, "serve_until_interrupted", lambda server: None)
    assert main(["start"]) == 0
    assert "Log:" not in capsys.readouterr().err


# --- Task 5 review R1: what the registry's own tests could not see ------------------------------


def test_the_page_s_poll_alone_heals_a_dead_job_and_a_new_job_starts_clean(
    tmp_path, started, set_fault
):
    """M3085, M3087 (U11, U12). After a job dies the page asks nothing but `GET /api/job` - that
    poll alone must heal the death (join, then recovery under the held lock) and carry recovery's
    own line, or the page shows U12 with no line and the intent row waits for a write. A job
    started afterwards reports no recovery line: the line belongs to that death, not to the
    server."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    approve_archive(root, plan_id)
    launcher.bind(started, root)
    set_fault.arm("crash:after_intent:1")
    started.jobs.start("rename", _dies_mid_rename(root, plan_id))
    assert await_state(started, "failed")["recovery"] is None
    set_fault.next_invocation()
    assert _intent_rows(root), "the crash must leave an intent row to heal"

    poll = fetch(started, "job")

    assert poll.status == 200
    body = json.loads(poll.body)
    assert (body["state"], body["recovery"]) == ("failed", RECOVERED)
    assert _intent_rows(root) == []
    job = Blocked()
    job.start(started)
    assert started.jobs.snapshot()["recovery"] is None
    job.release.set()
    await_state(started, "done")


def test_cancel_after_a_read_has_ended_asks_nothing(tmp_path, started):
    """M3089. `cancel` asks a RUNNING read to stop. Once the read has ended the answer is
    `stopping: false` - the page would otherwise wait for a `cancelled` that never comes."""
    _bound(tmp_path, started)
    job = Blocked()
    job.start(started)
    job.release.set()
    await_state(started, "done")

    reply = fetch(started, "job/cancel", "POST", {}, started.binding)

    assert (reply.status, json.loads(reply.body)) == (200, {"stopping": False})


def test_server_close_heals_a_job_that_died_before_releasing_the_lock(
    tmp_path, set_fault, monkeypatch
):
    """M3092, M3093. A job died and no request came after it: `server_close` itself joins and
    recovers it, WHILE the workspace lock is still held (J10: the plan in `applying` belongs to a
    joined thread of this process), and only then releases the lock."""
    server = start_server(_logger())
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    approve_archive(root, plan_id)
    launcher.bind(server, root)
    lock_path = workspace_if_exists(root).lock_path
    set_fault.arm("crash:after_intent:1")
    server.jobs.start("rename", _dies_mid_rename(root, plan_id))
    await_state(server, "failed")
    set_fault.next_invocation()
    assert _intent_rows(root), "the crash must leave an intent row to heal"
    lock_at_recovery: list[bool] = []
    real_recovery = jobs.run_recovery
    monkeypatch.setattr(
        jobs,
        "run_recovery",
        lambda *a: lock_at_recovery.append(lock_path.exists()) or real_recovery(*a),
    )

    server.server_close()

    assert lock_at_recovery == [True]
    assert not lock_path.exists()
    assert _intent_rows(root) == []
    conn = catalog(root)
    try:
        assert repo.plan_status(conn, plan_id) == "partially_applied"
    finally:
        conn.close()
