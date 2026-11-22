"""M14 Task 5d: `pam start`'s job routes (design 8.2 U11, U12, U18, U21; `pam/webui/job_api.py`).

`POST /api/job/read` runs READING - `scan_locked`, then `plan_locked` - as one job; `GET /api/job`
reports it; `POST /api/job/cancel` stops a read between files and is the one write served while a
job runs. Ping and bind name the bound folder (W23: the page never shows a path the server did
not send).

EVERY WAIT IS A BARRIER WITH A DEADLINE, never a `time.sleep` (CLAUDE.md), and the ETA is measured
on an INJECTED clock: a real one across near-instant fake work reads 0.0 on the hosted runner.

THE SENTENCES AND THE NUMBERS ARE SPELLED HERE, from U11 and U21 and from the tiny library's own
count, never imported from the code under test.
"""

import dataclasses
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
from archives import (
    DATES,
    FAKE_COMMAND,
    build_archive,
    catalog,
    materialise_tiny_library,
    planned,
)

from pam.naming import planner
from pam.paths import ARCHIVE_POINTER, workspace_if_exists
from pam.scan import runner
from pam.webui import job_api, launcher, lookalikes
from pam.webui.api import ApiError
from pam.webui.jobs import Finished
from pam.webui.launcher import StartServer, start_server

DEADLINE = 30.0

# U11 and U21, word for word.
BUSY_READING = "pam is busy reading your photos. Try again when it finishes."
NOTHING_NEW = "Nothing new to read."
ENRICH_IN_CONSOLE = "Places and people come from pam enrich, in the console."
# U11's stopped sentence over the tiny library, cancelled at file 40 of 100 (by hand).
STOPPED_40_OF_100 = "Stopped. pam read 40 of 100; the rest will be read next time."
# U10 and R1-F1, word for word.
CHOOSE_A_FOLDER = "Choose a folder first."
FOLDER_CHANGED = "pam is working on another folder now. Reload the page."
# Task 0D's copy, for a field the read's empty body does not know.
UNKNOWN_PATH = 'path: "path" is not something pam can filter by.'

# U11's body plus U12's recovery line, when there is no job; and (M14 Task 6b) the session a
# rename or undo wrote - none for a read.
IDLE = {
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

needs_exiftool = pytest.mark.skipif(
    shutil.which("exiftool") is None, reason="the tiny library is pinned against REAL exiftool"
)
# The tiny library's own counts (tests/test_tiny_library.py: READ = 100), spelled here.
TINY_LIBRARY_READ = 100
CANCEL_AT = 40


class Reply(NamedTuple):
    status: int
    body: bytes

    def json(self):
        return json.loads(self.body)


def _logger() -> logging.Logger:
    logger = logging.getLogger("pam.tests.webui_read_job")
    logger.propagate = False
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    return logger


@pytest.fixture
def started() -> Iterator[StartServer]:
    """`pam start`'s server, unbound and serving, scanning with the suite's fake exiftool."""
    server = start_server(_logger())
    server.exiftool_command = FAKE_COMMAND
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


def read(server) -> Reply:
    """The page's `Start reading`: `POST /api/job/read`, carrying the page's binding."""
    return fetch(server, "job/read", "POST", {}, binding=server.binding)


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


def read_to_the_end(server) -> dict:
    reply = read(server)
    assert reply.status == 200, reply
    return await_state(server, "done", "failed", "cancelled")


def _open_plans(root) -> list[int]:
    """The catalog's open plans, by the SUITE'S OWN SQL."""
    conn = catalog(root)
    try:
        return [r[0] for r in conn.execute("SELECT id FROM plans WHERE status = 'open'")]
    finally:
        conn.close()


def _add_photo(root, name: str) -> None:
    """One more photo, the way `archives.build_archive` lays one down."""
    (root / name).write_bytes(f"the only copy of {name}".encode())
    (root / f"{name}.tags.json").write_text(
        json.dumps({"DateTimeOriginal": DATES[name]}), encoding="utf-8"
    )


class Blocked:
    """A job body that blocks until the test releases it."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.saw_stop: bool | None = None

    def __call__(self, held, progress, should_stop, logger):
        self.entered.set()
        assert self.release.wait(DEADLINE), "the test never released the job"
        self.saw_stop = should_stop()
        return Finished()

    def start(self, server, kind="read") -> None:
        server.jobs.start(kind, self)
        assert self.entered.wait(DEADLINE), "the job body never started"


# --- POST /api/job/read: the job (Step 6) -------------------------------------------------------


def test_a_new_plan_does_not_serve_the_old_plans_look_alikes(tmp_path, started):
    """T5.6, Q5's shape: a reader filled plan P's look-alike memory, a read made plan Q; P's
    entry is gone and Q's is computed fresh, on Q's first request."""
    root, p = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    launcher.bind(started, root)
    assert fetch(started, f"plan/{p}/items").status == 200
    assert set(lookalikes._MEMORY[started]) == {p}

    assert read_to_the_end(started)["state"] == "done"

    q = started.plan_id
    assert q is not None and q != p
    assert set(lookalikes._MEMORY.get(started, {})) == set()
    assert fetch(started, f"plan/{q}/items").status == 200
    assert set(lookalikes._MEMORY[started]) == {q}


def test_a_second_read_of_an_unchanged_folder_hashes_nothing(tmp_path, started, monkeypatch):
    """T5.7, P2 from the page: a spy on the scanner's hasher. The positive control first - a new
    photo is hashed (1 call: the fake exiftool's `.tags.json` sidecar is not a photo pam reads) - then the same folder again: 0 calls and
    U11's `Nothing new to read.`"""
    root, _ = planned(tmp_path, ["IMG_1.JPG"])
    launcher.bind(started, root)
    hashed: list[str] = []
    real = runner.sha256_file
    monkeypatch.setattr(
        runner, "sha256_file", lambda path: hashed.append(path.name) or real(path)
    )
    _add_photo(root, "IMG_2.JPG")

    first = read_to_the_end(started)
    assert hashed == ["IMG_2.JPG"]
    assert NOTHING_NEW not in first["lines"]

    hashed.clear()
    second = read_to_the_end(started)
    assert (second["state"], hashed) == ("done", [])
    assert NOTHING_NEW in second["lines"]


@needs_exiftool
def test_a_stopped_read_never_plans_and_the_next_read_finishes_then_plans(
    tmp_path, started, monkeypatch
):
    """C5-4: a read stopped at file 40 of the tiny library's 100 ends `cancelled` with U11's
    sentence and its own counts, and `plan_locked` is never CALLED. The next read reads the other
    60 and plans."""
    root = tmp_path / "library"
    materialise_tiny_library(root)
    started.exiftool_command = None  # the tiny library is pinned against real exiftool
    launcher.bind(started, root)  # never scanned: chosen; the read binds it (U10)
    plans: list[object] = []
    real_plan = job_api.plan_locked
    monkeypatch.setattr(
        job_api, "plan_locked", lambda *a, **k: plans.append(a[0]) or real_plan(*a, **k)
    )
    tick = started.jobs._progress

    def cancel_at_40(done, total):
        tick(done, total)
        if done == CANCEL_AT:
            started.jobs.cancel()

    monkeypatch.setattr(started.jobs, "_progress", cancel_at_40)

    stopped = read_to_the_end(started)
    assert stopped == {
        **IDLE,
        "kind": "read",
        "state": "cancelled",
        "done": 40,
        "total": 100,
        "lines": [STOPPED_40_OF_100],
    }
    assert plans == []
    assert started.plan_id is None
    assert _open_plans(root) == []

    monkeypatch.setattr(started.jobs, "_progress", tick)
    finished = read_to_the_end(started)
    assert (finished["state"], finished["done"], finished["total"]) == ("done", 60, 60)
    assert plans == [started.held]
    assert _open_plans(root) == [started.plan_id]


def _stored_grouping(root) -> set[tuple[str, frozenset[str]]]:
    """The catalog's companion groups as {(kind, member rel_paths)}, by the SUITE'S OWN SQL."""
    conn = catalog(root)
    try:
        groups: dict[int, tuple[str, set[str]]] = {}
        for r in conn.execute(
            "SELECT g.id, g.kind, f.rel_path FROM companion_groups g"
            " JOIN files f ON f.companion_group_id = g.id"
        ):
            groups.setdefault(r["id"], (r["kind"], set()))[1].add(r["rel_path"])
        return {(kind, frozenset(members)) for kind, members in groups.values()}
    finally:
        conn.close()


def _unread(root) -> set[str]:
    """The images and videos whose bytes no scan has read yet (sha256 NULL), by own SQL."""
    conn = catalog(root)
    try:
        return {
            r[0]
            for r in conn.execute(
                "SELECT rel_path FROM files"
                " WHERE media_type IN ('image', 'video') AND sha256 IS NULL"
            )
        }
    finally:
        conn.close()


@needs_exiftool
def test_a_stopped_read_stores_the_grouping_a_full_scan_stores_and_still_never_plans(
    tmp_path, started, monkeypatch
):
    """R1-F1 (vision S3), C5-4 as amended 2026-11-13: a read stopped at file 40 of the tiny
    library's 100 still GROUPS before it returns. Its stored grouping is the one a full, unstopped
    scan of a COPY of the library stores in a second workspace - an expectation measured, never
    computed by the grouping function under test - and the stopped read still plans nothing."""
    root = tmp_path / "library"
    materialise_tiny_library(root)
    copy = tmp_path / "copy"
    shutil.copytree(root, copy)
    started.exiftool_command = None  # the tiny library is pinned against real exiftool
    launcher.bind(started, root)
    plans: list[object] = []
    real_plan = job_api.plan_locked
    monkeypatch.setattr(
        job_api, "plan_locked", lambda *a, **k: plans.append(a[0]) or real_plan(*a, **k)
    )
    tick = started.jobs._progress

    def cancel_at_40(done, total):
        tick(done, total)
        if done == CANCEL_AT:
            started.jobs.cancel()

    monkeypatch.setattr(started.jobs, "_progress", cancel_at_40)
    stopped = read_to_the_end(started)
    assert (stopped["state"], stopped["done"]) == ("cancelled", CANCEL_AT)

    full = runner.run_scan(copy.resolve())
    assert (full.stopped, full.read, full.errors) == (False, TINY_LIBRARY_READ, [])
    expected = _stored_grouping(copy)
    assert expected  # the tiny library has companion groups, or this test is vacuous
    # ...and the stop left some of their members unread, which is the case R1-F1 is about.
    assert _unread(root) & {m for _kind, members in expected for m in members}
    assert _stored_grouping(root) == expected
    assert plans == []
    assert _open_plans(root) == []


def test_the_read_job_runs_the_lock_held_bodies_never_the_commands(
    tmp_path, started, monkeypatch
):
    """5c concern 4: `run_scan` / `run_plan` take the lock the server holds and open their own log,
    which would end the server's. The job calls `scan_locked`, then `plan_locked`, under the
    server's proof - watched on the calls themselves."""
    root, _ = planned(tmp_path, ["IMG_1.JPG"])
    launcher.bind(started, root)
    calls: list[tuple[str, object]] = []
    real_scan, real_plan = job_api.scan_locked, job_api.plan_locked
    monkeypatch.setattr(
        job_api,
        "scan_locked",
        lambda *a, **k: calls.append(("scan_locked", a[0])) or real_scan(*a, **k),
    )
    monkeypatch.setattr(
        job_api,
        "plan_locked",
        lambda *a, **k: calls.append(("plan_locked", a[0])) or real_plan(*a, **k),
    )
    for module, name in ((runner, "run_scan"), (planner, "run_plan")):
        monkeypatch.setattr(module, name, lambda *a, n=name, **k: calls.append((n, None)))

    assert read_to_the_end(started)["state"] == "done"
    assert calls == [("scan_locked", started.held), ("plan_locked", started.held)]


def test_after_a_read_the_server_serves_the_new_open_plan(tmp_path, started):
    """Step 6: the server's `plan_id` is the plan the read made - the catalog's one open plan,
    read by the suite's own SQL - and ping says so; the old plan is no longer open."""
    root, p = planned(tmp_path, ["IMG_1.JPG"])
    launcher.bind(started, root)

    read_to_the_end(started)

    (q,) = _open_plans(root)
    assert q != p
    assert started.plan_id == q
    ping = fetch(started, "ping").json()
    assert (ping["plan_id"], ping["status"]) == (q, "open")


def test_the_enrich_sentence_is_said_only_when_no_place_fact_exists(tmp_path, started):
    """U21: no live `place` fact -> the sentence; one live `place` fact -> not said."""
    root, _ = planned(tmp_path, ["IMG_1.JPG"])
    launcher.bind(started, root)
    assert ENRICH_IN_CONSOLE in read_to_the_end(started)["lines"]

    conn = catalog(root)
    try:
        sha = conn.execute("SELECT sha256 FROM files WHERE rel_path = 'IMG_1.JPG'").fetchone()[
            0
        ]
        conn.execute(
            "INSERT INTO inferred_facts (sha256, kind, value_json, confidence, model_name,"
            " model_version, generated_at) VALUES (?, 'place', ?, NULL, 'geocode', '1.0',"
            " '2026-11-11T09:00:00')",
            (sha, json.dumps({"place": "Place A"})),
        )
        conn.commit()
    finally:
        conn.close()

    lines = read_to_the_end(started)["lines"]
    assert ENRICH_IN_CONSOLE not in lines
    assert lines == [NOTHING_NEW]


def test_a_chosen_folder_is_bound_by_its_first_read(tmp_path, started):
    """U10: `Start reading` on a never-scanned folder IS the bind - the workspace is created, the
    lock taken and the archive pointer written - and then the read runs. Nothing chosen: 409."""
    assert (read(started).status, read(started).json()) == (409, {"error": CHOOSE_A_FOLDER})
    root = build_archive(tmp_path, ["IMG_1.JPG"])
    launcher.bind(started, root)
    assert workspace_if_exists(root) is None

    reply = read(started)

    # the choice counted 1, the bind that reading it IS counts 2 (Task 5d concern 2)
    expected = {"job": 1, "binding": 2, "folder": str(root.resolve())}
    assert (reply.status, reply.json()) == (200, expected)
    assert await_state(started, "done", "failed")["state"] == "done"
    ws = workspace_if_exists(root)
    assert ws is not None
    assert (ws.root / ARCHIVE_POINTER).read_text(encoding="utf-8").strip() == str(
        root.resolve()
    )
    assert int(ws.lock_path.read_text(encoding="ascii")) == os.getpid()
    assert _open_plans(root) == [started.plan_id]


def test_a_read_naming_an_earlier_choice_is_refused_and_creates_nothing(tmp_path, started):
    """Task 5d concern 2: two pages on one `pam start` server. Page A chooses folder X, page B
    then chooses folder Y. A's `Start reading` names the binding A's choice returned, so it is
    refused with R1-F1's sentence - it must NOT create a workspace in Y and read it. B's own
    read, naming the current binding, is served and reads Y."""
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    x = build_archive(tmp_path / "x", ["IMG_1.JPG"])
    y = build_archive(tmp_path / "y", ["IMG_1.JPG"])
    page_a = launcher.bind(started, x)["binding"]
    page_b = launcher.bind(started, y)["binding"]
    assert page_a != page_b

    stale = fetch(started, "job/read", "POST", {}, binding=page_a)

    assert (stale.status, stale.json()) == (
        409,
        {"error": "pam is working on another folder now. Reload the page."},
    )
    assert workspace_if_exists(x) is None
    assert workspace_if_exists(y) is None
    assert started.jobs.snapshot() == IDLE

    fresh = fetch(started, "job/read", "POST", {}, binding=page_b)
    assert fresh.status == 200
    assert fresh.json()["folder"] == str(y.resolve())
    assert await_state(started, "done", "failed")["state"] == "done"


def test_the_chosen_folder_and_the_binding_are_read_inside_the_bind_lock(tmp_path, started):
    """Task 5d concern 2, the race half: a choice made by another page AFTER the request's
    outside check and BEFORE the bind must be refused, so `bind_chosen` reads the chosen folder
    and the page's binding inside `db_lock`. Driven by calling the seam with the binding the page
    held before the second choice."""
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    x = build_archive(tmp_path / "x", ["IMG_1.JPG"])
    y = build_archive(tmp_path / "y", ["IMG_1.JPG"])
    held_by_page = launcher.bind(started, x)["binding"]
    launcher.bind(started, y)
    started.sent.binding = held_by_page
    try:
        with pytest.raises(ApiError) as refused:
            started.bind_chosen()
    finally:
        started.sent.binding = None
    assert str(refused.value) == "pam is working on another folder now. Reload the page."
    assert workspace_if_exists(x) is None
    assert workspace_if_exists(y) is None
    assert started.chosen == y.resolve()


class _WithdrawOnEntry:
    """`db_lock` stand-in: on its first entry, before the holder runs, another page's FAILED bind
    has just run (`_release`: the choice withdrawn, no binding counted). Reproduces the
    interleaving between a read of `chosen` taken OUTSIDE the lock and the bind's lock."""

    def __init__(self, server: StartServer) -> None:
        self.server, self.inner, self.pending = server, server.db_lock, True

    def __enter__(self) -> None:
        self.inner.__enter__()
        if self.pending:
            self.pending = False
            launcher._release(self.server)

    def __exit__(self, *exc: object) -> None:
        self.inner.__exit__(*exc)


def test_a_choice_withdrawn_just_before_the_bind_lock_is_not_read(tmp_path, started):
    """M3070: another page's failed bind withdraws the choice WITHOUT counting a binding, so the
    page's own number still matches. Only reading `chosen` inside the lock sees the withdrawal:
    the read is refused with "Choose a folder first." and no workspace is created."""
    (tmp_path / "x").mkdir()
    x = build_archive(tmp_path / "x", ["IMG_1.JPG"])
    held_by_page = launcher.bind(started, x)["binding"]
    started.sent.binding = held_by_page
    started.db_lock = _WithdrawOnEntry(started)  # type: ignore[assignment]
    try:
        with pytest.raises(ApiError) as refused:
            started.bind_chosen()
    finally:
        started.db_lock = started.db_lock.inner
        started.sent.binding = None
    assert str(refused.value) == "Choose a folder first."
    assert workspace_if_exists(x) is None
    assert started.chosen is None
    assert started.binding == held_by_page


def test_a_read_body_with_an_unknown_field_is_refused_and_starts_nothing(tmp_path, started):
    """Task 0D's copy for a field the read's (empty) body does not know; no job starts."""
    root, p = planned(tmp_path, ["IMG_1.JPG"])
    launcher.bind(started, root)
    reply = fetch(started, "job/read", "POST", {"path": "D:/"}, binding=started.binding)
    assert reply.status == 400
    assert reply.json()["error"] == UNKNOWN_PATH
    assert started.jobs.snapshot() == IDLE
    assert _open_plans(root) == [p]


def test_the_job_routes_carry_the_binding_and_the_busy_refusal(tmp_path, started):
    """Both new POSTs go through `_api`: no binding header, or another binding, is R1-F1's 409;
    a read while a job runs is U11's 409. Cancel is the exception to busy, not to the binding."""
    planned_root, _ = planned(tmp_path, ["IMG_1.JPG"])
    launcher.bind(started, planned_root)
    for route in ("job/read", "job/cancel"):
        for binding in (None, started.binding + 1):
            reply = fetch(started, route, "POST", {}, binding=binding)
            assert (reply.status, reply.json()) == (409, {"error": FOLDER_CHANGED}), route
    job = Blocked()
    job.start(started)
    try:
        reply = read(started)
        assert (reply.status, reply.json()) == (409, {"error": BUSY_READING})
    finally:
        job.release.set()
    await_state(started, "done")


# --- POST /api/job/cancel -----------------------------------------------------------------------


def test_cancel_is_served_while_busy_and_ignored_on_a_job_that_cannot_stop(tmp_path, started):
    """5c's named exemption: cancel is served while a job runs. A read is asked to stop
    (`stopping: true`); a rename is not (`stopping: false`, nothing set); idle is `false`."""
    root, _ = planned(tmp_path, ["IMG_1.JPG"])
    launcher.bind(started, root)

    def cancel() -> Reply:
        return fetch(started, "job/cancel", "POST", {}, binding=started.binding)

    assert (cancel().status, cancel().json()) == (200, {"stopping": False})
    for kind, expected in (("read", True), ("rename", False)):
        job = Blocked()
        job.start(started, kind)
        reply = cancel()
        job.release.set()
        assert (reply.status, reply.json()) == (200, {"stopping": expected}), kind
        await_state(started, "done", "cancelled")
        assert job.saw_stop is expected, kind


# --- GET /api/job -------------------------------------------------------------------------------


def test_the_job_is_idle_unbound_and_bound_with_no_job(tmp_path, started):
    """U18's `[unbound ok]`: an unbound server answers idle, and so does a bound one with no job."""
    assert (fetch(started, "job").status, fetch(started, "job").json()) == (200, IDLE)
    root, _ = planned(tmp_path, ["IMG_1.JPG"])
    launcher.bind(started, root)
    assert fetch(started, "job").json() == IDLE


class Clock:
    """An injected clock that answers the next of `times`, under a lock."""

    def __init__(self, times: list[float]) -> None:
        self._times = iter(times)
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return next(self._times)


def test_the_job_reports_running_counts_and_the_eta_from_eta_py_s_formula(tmp_path, started):
    """`eta_s` is spec 8.1's ETA (`enrich/eta.py`), measured per file on an injected clock: None
    until 20 files are measured (a scan has no prior), then `per_file_ms x remaining / 1000`.

    By hand. The clock reads 0.0 when the job starts and 0.5 s more at each of files 1..20, so
    every file took 500 ms: at file 19 there is no ETA; at file 20 it is 80 x 500 ms = 40.0 s.
    File 21 then takes 1500 ms: EWMA = 0.1 x 1500 + 0.9 x 500 = 600 ms; 79 x 600 ms = 47.4 s.
    """
    root, _ = planned(tmp_path, ["IMG_1.JPG"])
    launcher.bind(started, root)
    started.jobs.clock = Clock([0.0] + [0.5 * k for k in range(1, 21)] + [11.5])
    reached = {k: threading.Event() for k in (19, 20, 21)}
    go = {k: threading.Event() for k in (19, 20, 21)}

    def body(held, progress, should_stop, logger):
        for k in range(1, 22):
            progress(k, 100)
            if k in reached:
                reached[k].set()
                assert go[k].wait(DEADLINE), "the test never let the job go on"
        return Finished(lines=("finished",))

    started.jobs.start("read", body)
    running = {**IDLE, "kind": "read", "state": "running", "total": 100}
    for k, eta in ((19, None), (20, 40.0), (21, 47.4)):
        assert reached[k].wait(DEADLINE), k
        reply = fetch(started, "job")
        assert (reply.status, reply.json()) == (200, {**running, "done": k, "eta_s": eta}), k
        go[k].set()
    done = await_state(started, "done")
    assert done == {**running, "state": "done", "done": 21, "lines": ["finished"]}


# --- ping and bind name the bound folder (W23) --------------------------------------------------


def test_ping_and_bind_name_the_bound_folder(tmp_path, started):
    """`folder`: the bound archive root as a string, or null when unbound - a chosen folder is not
    bound, so bind's `chosen` answer says `null` (its `archive_root` names the chosen folder)."""
    assert fetch(started, "ping").json()["folder"] is None
    chosen_root = tmp_path / "chosen"
    chosen_root.mkdir()
    chosen = launcher.bind(started, chosen_root)
    assert (chosen["state"], chosen["folder"]) == ("chosen", None)
    assert fetch(started, "ping").json()["folder"] is None

    root, _ = planned(tmp_path, ["IMG_1.JPG"])
    bound = launcher.bind(started, root)
    assert (bound["state"], bound["folder"]) == ("bound", str(root.resolve()))
    assert fetch(started, "ping").json()["folder"] == str(root.resolve())


# --- Task 5 review R1: what the read job's own tests could not see ------------------------------

# `pam scan`'s own refusal (`scan/exiftool.default_command`), and `parse_template`'s, spelled.
NO_EXIFTOOL = "exiftool was not found on PATH, and scanning needs it to read photo metadata. Run: pam doctor"
REPEATS_A_TOKEN = "Naming template repeats a token."


def test_a_read_job_writes_into_the_server_s_one_log(tmp_path, started):
    """M3096 (U23). The READ job's bodies log into the server's one log - the job's own `job
    start` / `job end`, and the scan's and the plan's lines between them, in order - and the
    `pam` logger keeps the same handler object. A body that opened a session log of its own would
    clear that handler and so end the server's log (M628a)."""
    root, _ = planned(tmp_path, ["IMG_1.JPG"])
    launcher.bind(started, root)
    pam_logger = logging.getLogger("pam")
    (handler,) = pam_logger.handlers

    assert read_to_the_end(started)["state"] == "done"

    assert pam_logger.handlers == [handler]
    text = started.log_path.read_text(encoding="utf-8")
    marks = (
        "job start: read session=1",
        "scan start: root=",
        "plan start: root=",
        "job end: done",
    )
    assert all(mark in text for mark in marks), text
    assert [text.index(mark) for mark in marks] == sorted(text.index(mark) for mark in marks)


class _Recording:
    """`db_lock` stand-in: what the server's plan id and look-alike memory were when each holder
    took the lock and when it let go."""

    def __init__(self, server: StartServer) -> None:
        self.server, self.inner, self.spans = server, server.db_lock, []

    def _now(self) -> tuple:
        return (self.server.plan_id, self.server in lookalikes._MEMORY)

    def __enter__(self) -> None:
        self.inner.__enter__()
        self.entered = self._now()

    def __exit__(self, *exc: object) -> None:
        self.spans.append((self.entered, self._now()))
        self.inner.__exit__(*exc)


def test_a_read_publishes_its_plan_inside_the_request_lock(tmp_path, started):
    """M3098. The read's new plan id and the dropped look-alike memory are published INSIDE
    `db_lock` (5d), so no request sees the new plan id beside the old plan's memory. Watched on the
    lock itself: one holder took it with (P, memory) and let go with (Q, no memory)."""
    root, p = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    launcher.bind(started, root)
    assert fetch(started, f"plan/{p}/items").status == 200
    recording = _Recording(started)
    started.db_lock = recording  # type: ignore[assignment]
    try:
        assert read_to_the_end(started)["state"] == "done"
    finally:
        started.db_lock = recording.inner
    q = started.plan_id
    assert q is not None and q != p
    assert ((p, True), (q, False)) in recording.spans, recording.spans


def test_a_read_that_rereads_a_changed_photo_does_not_say_nothing_new(tmp_path, started):
    """M3102. U11's P2 sentence is about the files the scan READ: a photo changed on disk is read
    again (`new` 0, `read` 1), so the read must not say `Nothing new to read.`"""
    root, _ = planned(tmp_path, ["IMG_1.JPG"])
    launcher.bind(started, root)
    (root / "IMG_1.JPG").write_bytes(b"the only copy of IMG_1.JPG, edited since")

    lines = read_to_the_end(started)["lines"]

    assert NOTHING_NEW not in lines
    assert NOTHING_NEW in read_to_the_end(started)["lines"]  # the control: now nothing is new


def test_a_read_without_exiftool_is_refused_before_a_chosen_folder_is_bound(
    tmp_path, started, monkeypatch
):
    """M3112. exiftool is asked BEFORE the chosen folder is bound (`run_scan` refuses early too,
    spec 5.3): `pam scan`'s own sentence, 409, no workspace created, no job."""
    root = build_archive(tmp_path, ["IMG_1.JPG"])
    launcher.bind(started, root)  # never scanned: chosen
    started.exiftool_command = None
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)

    reply = read(started)

    assert (reply.status, reply.json()) == (409, {"error": NO_EXIFTOOL})
    assert workspace_if_exists(root) is None
    assert started.chosen == root.resolve()
    assert started.jobs.snapshot() == IDLE


def test_a_template_pam_cannot_parse_is_refused_and_starts_nothing(tmp_path, started):
    """M3113. The template is parsed before the job starts (`run_plan` parses it before any state
    is touched): one that does not parse is a 409 with its own sentence, and no job starts."""
    root, p = planned(tmp_path, ["IMG_1.JPG"])
    launcher.bind(started, root)
    started._config = dataclasses.replace(
        started.config, template="{date}_{date}"
    )  # as if read at bind

    reply = read(started)

    assert (reply.status, reply.json()) == (409, {"error": REPEATS_A_TOKEN})
    assert started.jobs.snapshot() == IDLE
    assert _open_plans(root) == [p]
