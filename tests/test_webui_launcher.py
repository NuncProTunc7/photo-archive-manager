"""The review server with no folder chosen yet: UNBOUND (M14 Task 4 Step 2, U10).

`pam start` (Task 4 Step 6) starts the review server before the user has chosen a folder. Such
a server holds no workspace, no lock and no configuration. Design 8.2 (U10) lets it serve only
Welcome, the folder chooser and the static files; every route that needs an archive answers
**409 `Choose a folder first.`** - a sentence the page can show - and NEVER the empty 404, which
the token gate reserves for requests without the token (amendment A3).

WHAT IS PINNED HERE, AND HOW:

- The archive routes are enumerated TWICE and the two must agree: from the live server's route
  table (what is actually served) and from a list spelled BY HAND from design 8.2's API block
  (what the spec says exists). A route registered without the spec knowing, or a spec route that
  stopped being served, is red; and every one of them, driven unbound, answers the 409.
- The unbound-allowed set (`server.UNBOUND_ROUTES`) is checked against a hand-spelled list
  from the plan (Task 4 Step 2) and U18's `[unbound ok]` marks. `folders`, `bind` and `job` have
  no handler until slices 4b/4c and Task 5; the test registers a stand-in for each, ON ITS OWN
  SERVER OBJECT, to show the gate lets them through while unbound.
- A bound server (what `pam review` builds) is unchanged: same routes, configuration loaded at
  construction, the archive routes answering 200.

BIND, CHANGE OF FOLDER AND A FAILED BIND (Task 4 Step 3, slice 4b; `webui/launcher.py`). The lock
is the property, so every lock test uses a REAL SECOND PROCESS holding or wanting the REAL lock,
and the order release-then-acquire is watched at the CALL (a spy on the new lock's `__enter__`
that runs the second console right then), not inferred from the end state: after a change of
folder the old lock is released under either order, so the end state cannot tell them apart.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from http.client import HTTPConnection
from importlib import resources
from typing import NamedTuple

import pytest
from archives import FAKE_COMMAND, approve_archive, catalog, journal_pairs, planned, put
from test_faults_matrix import REPO_ROOT, _assert_crashed_at, _child

from pam.config import Config
from pam.paths import app_root, archive_slug, workspace_if_exists
from pam.webui import launcher
from pam.webui.api import ApiError
from pam.webui.launcher import start_server
from pam.webui.server import STATIC_FILES, UNBOUND_ROUTES, ReviewServer

# U10, the sentence the plan spells (Task 4 Step 2) - spelled here, not imported.
CHOOSE = "Choose a folder first."
# R1-F1's refusal and header (the controller's ruling) - spelled here, not imported.
FOLDER_CHANGED = "pam is working on another folder now. Reload the page."
BINDING_HEADER = "X-Pam-Binding"

# Design 8.2's API block (rev 34), by hand: every route that reads or writes an archive. NOT
# read off `server.api_handlers` - that is the other half of the comparison.
ARCHIVE_ROUTES = frozenset(
    {
        ("GET", "plan/{id}"),
        ("GET", "plan/{id}/items"),
        ("GET", "plan/{id}/groups"),
        ("GET", "plan/{id}/list"),
        ("GET", "thumb/{file_id}"),
        ("POST", "plan/{id}/decide"),
        ("POST", "plan/{id}/decide-filter"),
        ("POST", "plan/{id}/selection"),
        ("POST", "plan/{id}/decide-selection"),
        ("POST", "item/{id}/rename"),
        ("POST", "item/{id}/restore"),
        ("GET", "faces/clusters"),
        ("GET", "faces/cluster/{id}"),
        ("GET", "faces/crop/{face_id}"),
        ("GET", "faces/persons"),
        ("GET", "faces/ignored"),
        ("GET", "faces/person/{id}"),
        ("POST", "faces/cluster/{id}/label"),
        ("POST", "faces/cluster/{id}/split"),
        ("POST", "faces/cluster/{id}/dismiss"),
        ("POST", "faces/faces/state"),
        ("POST", "faces/faces/assign"),
        ("POST", "faces/person/{id}"),
        ("POST", "faces/person/{id}/merge"),
    }
)
assert len(ARCHIVE_ROUTES) == 24, len(ARCHIVE_ROUTES)

# Task 4 Step 2 and U18's `[unbound ok]`: ping, the chooser, bind, and the (idle) job.
UNBOUND_OK = frozenset({("GET", "ping"), ("GET", "folders"), ("POST", "bind"), ("GET", "job")})
assert len(UNBOUND_OK) == 4, len(UNBOUND_OK)


class Reply(NamedTuple):
    status: int
    headers: dict[str, str]
    body: bytes


def _logger() -> logging.Logger:
    logger = logging.getLogger("pam.tests.webui_launcher")
    logger.propagate = False
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    return logger


def _serve(server: ReviewServer) -> Iterator[ReviewServer]:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=20)


@pytest.fixture
def unbound() -> Iterator[ReviewServer]:
    yield from _serve(ReviewServer(workspace=None, plan_id=None, logger=_logger()))


@pytest.fixture
def bound(tmp_path) -> Iterator[tuple[ReviewServer, int]]:
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    ws = workspace_if_exists(root)
    assert ws is not None
    server = ReviewServer(workspace=ws, plan_id=plan_id, logger=_logger())
    for running in _serve(server):
        yield running, plan_id


def fetch(
    server: ReviewServer, path: str, method: str = "GET", body: bytes = b"", binding=None
) -> Reply:
    conn = HTTPConnection(server.host, server.port, timeout=20)
    try:
        headers = {"Content-Type": "application/json"} if body else {}
        if binding is not None:
            headers[BINDING_HEADER] = str(binding)
        conn.request(method, path, body=body or None, headers=headers)
        response = conn.getresponse()
        return Reply(response.status, dict(response.getheaders()), response.read())
    finally:
        conn.close()


def _concrete(template: str) -> str:
    """`plan/{id}/items` -> `plan/1/items`: any value fills a path parameter."""
    return "/".join("1" if part.startswith("{") else part for part in template.split("/"))


def test_unbound_routes_say_choose_a_folder(unbound):
    """T4.7. Every archive route, enumerated from the live table AND spelled from 8.2, is 409."""
    served = set(unbound.api_handlers)
    assert served - set(UNBOUND_ROUTES) == ARCHIVE_ROUTES
    driven: list[tuple[str, str]] = []
    for method, template in sorted(ARCHIVE_ROUTES):
        driven.append((method, template))
        # A body the route would accept or refuse - the gate must answer before either.
        body = b"{}" if method == "POST" else b""
        reply = fetch(unbound, f"/t/{unbound.token}/api/{_concrete(template)}", method, body)
        assert reply.status == 409, (method, template, reply)
        assert reply.headers["Content-Type"] == "application/json; charset=utf-8"
        assert reply.body == b'{"error": "Choose a folder first."}', (method, template)
    # The loop's own reach (Task 4 review R1, M3013): every one of the 24 was driven, so a loop
    # that iterates less cannot pass as "every archive route answered the 409".
    assert len(driven) == 24 and set(driven) == ARCHIVE_ROUTES, driven
    # The token gate is unchanged: the wrong token on an archive route is the EMPTY 404.
    reply = fetch(unbound, "/t/not-the-token/api/plan/1")
    assert (reply.status, reply.body) == (404, b"")
    # And a path that is no route at all, under the right token, is still the empty 404.
    reply = fetch(unbound, f"/t/{unbound.token}/api/no-such-route")
    assert (reply.status, reply.body) == (404, b"")


def test_an_unbound_post_with_a_malformed_body_still_says_choose_a_folder(unbound):
    """The gate runs BEFORE the body is parsed: a 400 about JSON would hide the 409."""
    reply = fetch(unbound, f"/t/{unbound.token}/api/plan/1/decide", "POST", b"{not json")
    assert reply.status == 409
    assert CHOOSE.encode() in reply.body


def test_a_read_with_nothing_chosen_says_choose_a_folder_before_its_body_or_exiftool(
    started, monkeypatch
):
    """`job/read` passes the unbound gate ONLY while a folder is chosen (M14 Task 5 review R1,
    M3114). With nothing chosen the gate's sentence comes first: not the unknown-field refusal,
    and not exiftool's - which the handler asks before it binds."""
    url = f"/t/{started.token}/api/job/read"
    reply = fetch(started, url, "POST", b'{"surprise": 1}')
    assert (reply.status, CHOOSE.encode() in reply.body) == (409, True), reply.body
    monkeypatch.setattr(shutil, "which", lambda *_args, **_kwargs: None)
    reply = fetch(started, url, "POST", b"{}")
    assert (reply.status, CHOOSE.encode() in reply.body) == (409, True), reply.body
    assert (started.bound, started.jobs.snapshot()["state"]) == (False, "idle")


def test_the_unbound_allowed_set_is_spelled_and_answers(unbound):
    """ping and the static files answer for real; folders/bind/job pass the gate."""
    assert frozenset(UNBOUND_ROUTES) == UNBOUND_OK
    reply = fetch(unbound, f"/t/{unbound.token}/api/ping")
    assert reply.status == 200
    # An unbound server has bound nothing yet: binding 0 (R1-F1).
    # M14 Task 5d (W23): and names no folder. M14 Task 6 S2-F2: and no recovery line.
    assert reply.body == (
        b'{"plan_id": null, "status": null, "binding": 0, "folder": null, "recovery": null}'
    )
    for name in sorted(STATIC_FILES):
        reply = fetch(unbound, f"/t/{unbound.token}/{name}")
        packaged = (resources.files("pam.webui") / "static" / name).read_bytes()
        assert (reply.status, reply.body) == (200, packaged), name
    reply = fetch(unbound, f"/t/{unbound.token}/")
    assert reply.status == 200
    # The routes later slices build: a stand-in on THIS server proves the gate admits them.
    for method, template in sorted(UNBOUND_OK - {("GET", "ping")}):
        assert (method, template) not in unbound.api_handlers  # 4b/4c/Task 5 register them
        unbound.api_handlers[(method, template)] = lambda _server, _request: {"ok": True}
        body = b"{}" if method == "POST" else b""
        reply = fetch(unbound, f"/t/{unbound.token}/api/{template}", method, body)
        assert (reply.status, reply.body) == (200, b'{"ok": true}'), (method, template)


def test_an_unbound_server_has_no_workspace_and_no_configuration():
    """The configuration is loaded at BIND, not at construction - and an unbound server's
    `workspace`, `config` and `connection()` refuse with U10's sentence rather than reaching
    for a catalog that does not exist."""
    server = ReviewServer(workspace=None, plan_id=None, logger=_logger())
    try:
        assert server.bound is False
        for reach in (
            lambda: server.workspace,
            lambda: server.config,
            lambda: server.connection().__enter__(),
        ):
            with pytest.raises(ApiError) as refused:
                reach()
            assert (refused.value.status, refused.value.message) == (409, CHOOSE)
    finally:
        server.server_close()


def test_a_bound_pam_review_server_is_unchanged(bound, tmp_path):
    """The control: what `pam review` builds is bound at construction, as it always was."""
    server, plan_id = bound
    assert server.bound is True
    assert isinstance(server.config, Config)
    assert server.workspace.db_path.exists()
    assert set(server.api_handlers) == ARCHIVE_ROUTES | {("GET", "ping")}
    reply = fetch(server, f"/t/{server.token}/api/ping")
    assert reply.status == 200
    folder = str((tmp_path / "photos").resolve())  # `planned` lays the archive down there
    assert json.loads(reply.body) == {
        "plan_id": plan_id,
        "status": "open",
        "binding": 1,
        "folder": folder,
        "recovery": None,
    }
    reply = fetch(server, f"/t/{server.token}/api/plan/{plan_id}")
    assert reply.status == 200
    reply = fetch(server, f"/t/{server.token}/api/plan/{plan_id}/items")
    assert reply.status == 200


# --- bind, change of folder, a failed bind (Task 4 Step 3, slice 4b) --------------------------

# U10 and U11, spelled here - not imported from `launcher`.
ANOTHER_PAM = "Another pam is working on this folder. Close it, then try again."
BUSY_READING = "pam is busy reading your photos. Try again when it finishes."

# A second console: `pam scan` of one folder, in a fresh interpreter (the fake exiftool, as the
# suite's other scans). Exit 0 only if it got the folder's workspace lock and finished.
_SCAN_CHILD = r"""
import sys
from pathlib import Path
from pam.scan.runner import run_scan
run_scan(Path(sys.argv[1]), exiftool_command=[sys.executable, sys.argv[2]])
"""

# A second console holding a folder's lock until its stdin closes.
_HOLD_CHILD = r"""
import os, sys
from pathlib import Path
from pam.lock import WorkspaceLock
lock = WorkspaceLock(Path(sys.argv[1]))
lock.__enter__()
print("held", os.getpid(), flush=True)
sys.stdin.read()
lock.__exit__(None, None, None)
"""


@pytest.fixture
def started() -> Iterator[ReviewServer]:
    """`pam start`'s server, unbound, serving. `server_close` releases whatever it holds."""
    yield from _serve(start_server(_logger()))


def _scanned(base, name="IMG_1.JPG"):
    """A scanned and planned folder of its own, under `base`."""
    base.mkdir()
    return planned(base, [name])


def bind(server: ReviewServer, path) -> tuple[int, dict]:
    body = json.dumps({"path": str(path)}).encode()
    reply = fetch(server, f"/t/{server.token}/api/bind", "POST", body)
    return reply.status, json.loads(reply.body)


def _lock_file(root):
    ws = workspace_if_exists(root)
    assert ws is not None
    return ws.lock_path


def _holder(lock_path) -> int | None:
    """The pid written in a lockfile, or None when there is no lockfile."""
    return int(lock_path.read_text(encoding="ascii")) if lock_path.exists() else None


def _second_console_scans(root) -> int:
    proc = subprocess.run(
        [sys.executable, "-c", _SCAN_CHILD, str(root), FAKE_COMMAND[1]],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode


def test_bind_heals_a_crashed_apply(tmp_path, set_fault, started):
    """T4.1. An apply crashed in a SUBPROCESS after its intent row; binding the folder runs
    spec 7.2 recovery under the lock, and the page is handed recovery's own line (U12)."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    approve_archive(root, plan_id)
    report = _child(set_fault, "apply", root, plan_id, "crash:after_intent:1")
    _assert_crashed_at(report, "after_intent")
    conn = catalog(root)
    try:
        assert journal_pairs(conn, "intent"), "the crash must leave an intent row to heal"
    finally:
        conn.close()

    status, body = bind(started, root)

    assert status == 200, body
    assert (body["state"], body["archive_root"]) == ("bound", str(root.resolve()))
    # recovery's own sentence (`recover._announcement`), spelled here
    assert body["recovery"].startswith(
        "pam finished tidying up after an interrupted run: 1 rename(s) not started."
    ), body
    assert started.recovery == body["recovery"]
    conn = catalog(root)
    try:
        assert journal_pairs(conn, "intent") == []
    finally:
        conn.close()
    assert started.bound and _holder(_lock_file(root)) == os.getpid()
    # M3110 (R1, C5-5): the server's one log opens BEFORE recovery, so recovery's detail is in it.
    assert "recovery finalized plan" in started.log_path.read_text(encoding="utf-8")


def test_a_scanned_folder_binds_holds_its_lock_and_serves_its_plan(tmp_path, started):
    """The bound state: lock held by THIS process, configuration loaded, the open plan served,
    and a second console refused while the page holds the folder (U10)."""
    root, plan_id = _scanned(tmp_path / "a")
    status, body = bind(started, root)
    expected = {
        "state": "bound",
        "archive_root": str(root.resolve()),
        "plan_id": plan_id,
        "recovery": None,
        "binding": 1,
        "folder": str(root.resolve()),  # M14 Task 5d (W23)
    }
    assert (status, body) == (200, expected)
    assert _holder(_lock_file(root)) == os.getpid()
    assert isinstance(started.config, Config)
    reply = fetch(started, f"/t/{started.token}/api/plan/{plan_id}/items")
    assert reply.status == 200
    assert _second_console_scans(root) != 0


def test_change_folder_is_refused_while_reading(tmp_path, started, monkeypatch):
    """T4.2. While a job runs (Task 5's seam), a change of folder is U11's 409 and NOTHING moves:
    still bound to the first folder, its lock still ours, the second folder untouched."""
    a, _ = _scanned(tmp_path / "a")
    b, _ = _scanned(tmp_path / "b")
    assert bind(started, a)[0] == 200
    monkeypatch.setattr(started, "busy_with", lambda: "reading your photos")

    status, body = bind(started, b)

    assert (status, body) == (409, {"error": BUSY_READING})
    assert started.workspace.archive_root == a.resolve()
    assert _holder(_lock_file(a)) == os.getpid()
    assert _holder(_lock_file(b)) is None


def test_after_change_folder_a_second_console_can_scan_the_old_folder(
    tmp_path, started, monkeypatch
):
    """T4.3. The old lock is released BEFORE the new one is taken: at the very moment the new
    lock is being acquired, a real second process can scan the old folder."""
    a, _ = _scanned(tmp_path / "a")
    b, _ = _scanned(tmp_path / "b")
    assert bind(started, a)[0] == 200
    seen: list[int] = []

    class Watched(launcher.WorkspaceLock):
        def __enter__(self):
            seen.append(_second_console_scans(a))
            return super().__enter__()

    monkeypatch.setattr(launcher, "WorkspaceLock", Watched)

    status, body = bind(started, b)

    assert (status, body["state"]) == (200, "bound"), body
    assert seen == [0], "the old folder was still locked when the new lock was taken"
    assert _holder(_lock_file(b)) == os.getpid()
    assert _second_console_scans(a) == 0


@pytest.fixture
def held_by_another(tmp_path) -> Iterator[tuple]:
    """A scanned folder whose workspace lock a REAL second process holds until teardown."""
    root, _ = _scanned(tmp_path / "held")
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLD_CHILD, str(_lock_file(root))],
        cwd=str(REPO_ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        # Its OWN pid: on Windows a venv's python.exe is a launcher, so `proc.pid` is not the
        # interpreter that wrote the lockfile.
        word, pid = proc.stdout.readline().split()
        assert word == "held"
        yield root, int(pid)
    finally:
        assert proc.stdin is not None
        proc.stdin.close()
        proc.wait(timeout=30)


def test_a_failed_bind_leaves_the_server_unbound(tmp_path, started, held_by_another):
    """T4.8. Bound to one folder, the user changes to a folder another pam holds: U10's 409, and
    the server is UNBOUND - not still on the first folder, not half-bound to the second."""
    a, _ = _scanned(tmp_path / "a")
    held, holder_pid = held_by_another
    assert bind(started, a)[0] == 200

    status, body = bind(started, held)

    assert (status, body) == (409, {"error": ANOTHER_PAM})
    assert started.bound is False
    assert (started.held_lock, started.plan_id, started.chosen) == (None, None, None)
    for reach in (lambda: started.workspace, lambda: started.config):
        with pytest.raises(ApiError):
            reach()
    assert _holder(_lock_file(a)) is None  # released first, and not re-taken
    assert _holder(_lock_file(held)) == holder_pid  # the other pam's lock, untouched
    reply = fetch(started, f"/t/{started.token}/api/plan/1")
    assert (reply.status, reply.body) == (409, b'{"error": "Choose a folder first."}')


def test_a_bind_that_fails_after_taking_the_lock_releases_it(tmp_path, started, monkeypatch):
    """The other failure path: the lock was taken and recovery then failed - the lock is given
    back and nothing is attached."""
    root, _ = _scanned(tmp_path / "a")

    def broken(*_args, **_kwargs):
        raise RuntimeError("recovery failed")

    monkeypatch.setattr(launcher, "run_recovery", broken)
    status, _ = bind(started, root)
    assert status == 500
    assert started.bound is False
    assert started.held_lock is None
    assert _holder(_lock_file(root)) is None


def test_a_never_scanned_folder_is_chosen_not_bound(tmp_path, started, monkeypatch):
    """U10's paragraph: no workspace is created and no lock is taken - watched at the CALL - and
    a folder that was bound before is released."""
    a, _ = _scanned(tmp_path / "a")
    assert bind(started, a)[0] == 200
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    put(fresh, "IMG_9.JPG")
    entered: list[object] = []

    class Spy(launcher.WorkspaceLock):
        def __enter__(self):
            entered.append(self)
            return super().__enter__()

    monkeypatch.setattr(launcher, "WorkspaceLock", Spy)

    status, body = bind(started, fresh)

    expected = {
        "state": "chosen",
        "archive_root": str(fresh.resolve()),
        "plan_id": None,
        "recovery": None,
        "binding": 2,  # A's bind was 1; a CHOICE counts a new one too (Task 5d concern 2)
        "folder": None,  # M14 Task 5d (W23): a chosen folder is not bound
    }
    assert (status, body) == (200, expected)
    assert entered == []
    assert workspace_if_exists(fresh) is None
    assert not (app_root() / "archives" / archive_slug(fresh.resolve())).exists()
    assert started.bound is False
    assert started.held_lock is None
    assert started.chosen == fresh.resolve()
    assert _holder(_lock_file(a)) is None


@pytest.mark.parametrize(
    "body", [b"{}", b'{"path": ""}', b'{"path": 3}', b'{"path": "photos"}']
)
def test_a_bind_without_a_full_path_is_refused_and_changes_nothing(tmp_path, started, body):
    """The body is checked BEFORE anything is released: a bad request leaves the binding as it
    was."""
    a, _ = _scanned(tmp_path / "a")
    assert bind(started, a)[0] == 200
    reply = fetch(started, f"/t/{started.token}/api/bind", "POST", body)
    refusal = b'{"error": "path: give the full path of a folder."}'
    assert (reply.status, reply.body) == (400, refusal)
    assert started.workspace.archive_root == a.resolve()
    assert _holder(_lock_file(a)) == os.getpid()


def test_stopping_the_server_releases_its_lock(tmp_path):
    """`server_close` gives the lock back, so the next console is not refused by a dead page."""
    root, _ = _scanned(tmp_path / "a")
    server = start_server(_logger())
    served = _serve(server)
    next(served)
    try:
        assert bind(server, root)[0] == 200
        assert _holder(_lock_file(root)) == os.getpid()
    finally:
        for _ in served:
            pass
    assert _holder(_lock_file(root)) is None
    assert server.bound is False


def test_pam_review_servers_have_no_bind_route(bound):
    """`pam review`'s server is bound at construction and its lock is `run_review`'s: it must
    not be able to change folder (and so release a lock it does not own)."""
    server, _ = bound
    assert ("POST", "bind") not in server.api_handlers
    reply = fetch(server, f"/t/{server.token}/api/bind", "POST", b'{"path": "C:/"}')
    assert (reply.status, reply.body) == (404, b"")


# --- `pam start [PATH]` (Task 4 Step 6, U9 / OD-2) -----------------------------------------
#
# Driven through `pam.cli.main`, the way a console runs it. The serve loop is a seam: either a
# stand-in that inspects the live server and returns (the user pressing Ctrl+C), or the REAL
# `serve_until_interrupted` over a `serve_forever` that raises KeyboardInterrupt.

# W17 and the stop hint, word for word - spelled here, not imported.
START_PURPOSE = (
    "Review names and rename them in your browser. Nothing is renamed until you confirm there."
)
STOP_HINT = "Press Ctrl+C here when you are done."
START_AGAIN = "Next: pam start   (pick up where you left off)"
NETWORK = "pam works on folders on this computer. Copy the photos to this computer first."


def _start(monkeypatch, capsys, args, inspect=None) -> tuple[int, list[str], str, list[str]]:
    """Run `pam start ARGS`; `inspect(server)` runs while it serves. -> (exit, out, err, opened)"""
    from pam.cli import main

    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)

    def serve(server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            if inspect is not None:
                inspect(server)
        finally:
            server.shutdown()
            thread.join(timeout=20)

    monkeypatch.setattr(launcher, "serve_until_interrupted", serve)
    code = main(["start", *[str(a) for a in args]])
    captured = capsys.readouterr()
    return code, captured.out.splitlines(), captured.err, opened


def test_pam_start_announces_like_pam_review_with_its_own_purpose(monkeypatch, capsys):
    code, out, err, opened = _start(monkeypatch, capsys, [])
    assert code == 0, err
    assert len(opened) == 1
    url = opened[0]
    assert url.startswith("http://127.0.0.1:") and "/t/" in url and url.endswith("/")
    assert out == [f"Review at {url}", START_PURPOSE, STOP_HINT, START_AGAIN]


def test_pam_start_without_a_path_serves_an_unbound_server(monkeypatch, capsys):
    seen: dict = {}

    def inspect(server):
        seen["bound"] = server.bound
        seen["plan"] = fetch(server, f"/t/{server.token}/api/plan/1")
        seen["folders"] = fetch(server, f"/t/{server.token}/api/folders")

    code, _, err, _ = _start(monkeypatch, capsys, [], inspect)
    assert code == 0, err
    assert seen["bound"] is False
    assert (seen["plan"].status, seen["plan"].body) == (
        409,
        b'{"error": "Choose a folder first."}',
    )
    assert seen["folders"].status == 200


def test_pam_start_path_binds_a_scanned_folder(tmp_path, monkeypatch, capsys):
    root, plan_id = _scanned(tmp_path / "a")
    seen: dict = {}

    def inspect(server):
        seen["state"] = (server.bound, server.plan_id, server.workspace.archive_root)
        seen["holder"] = _holder(_lock_file(root))
        seen["items"] = fetch(server, f"/t/{server.token}/api/plan/{plan_id}/items").status

    code, out, err, _ = _start(monkeypatch, capsys, [root], inspect)
    assert code == 0, err
    assert seen == {
        "state": (True, plan_id, root.resolve()),
        "holder": os.getpid(),
        "items": 200,
    }
    assert out[-1] == START_AGAIN
    assert _holder(_lock_file(root)) is None


def test_pam_start_on_a_network_path_is_refused_with_the_sentence(
    tmp_path, monkeypatch, capsys
):
    """Refused exactly as `POST bind` refuses it: U13's sentence, exit 1, nothing served. Never
    skips: a UNC path on Windows, a folder under an `nfs` mount (a fake mount table) elsewhere."""
    from pam.webui import folders

    if sys.platform == "win32":
        target = r"\\server\share\photos"
    else:
        target = str(tmp_path)
        table = tmp_path / "mounts"
        table.write_text(f"server:/export {tmp_path} nfs rw 0 0\n", encoding="utf-8")
        monkeypatch.setattr(folders, "PROC_MOUNTS", str(table))

    def never(path):  # refused before the filesystem - and so the network - is asked
        raise AssertionError(f"resolve_archive reached for {path}")

    monkeypatch.setattr(launcher, "resolve_archive", never)
    served: list = []
    code, out, err, opened = _start(monkeypatch, capsys, [target], served.append)
    assert code == 1
    assert err.strip() == f"x {NETWORK}"
    assert (out, opened, served) == ([], [], [])


def test_ctrl_c_ends_pam_start_with_its_closing_block_and_releases_the_lock(
    tmp_path, monkeypatch, capsys
):
    """The REAL serve loop and a real Ctrl+C: exit 0, the closing block, and the folder's lock
    gone - a second console can scan it."""
    from pam.cli import main

    root, _ = _scanned(tmp_path / "a")
    monkeypatch.setattr("webbrowser.open", lambda url: True)
    held: list = []

    def interrupted(server, *args, **kwargs):
        held.append(_holder(_lock_file(root)))
        raise KeyboardInterrupt

    monkeypatch.setattr(launcher.StartServer, "serve_forever", interrupted)
    code = main(["start", str(root)])
    out = capsys.readouterr().out.splitlines()
    assert code == 0
    assert held == [os.getpid()]
    assert out[-1] == START_AGAIN
    assert _holder(_lock_file(root)) is None
    assert _second_console_scans(root) == 0


# --- Task 4 review R1: two findings, built by the review, fixed by the controller ---------------
#
# Both are about state that outlives a change of folder. Plan ids and item ids are per CATALOG, so
# folder A's plan 1 and folder B's plan 1 collide - and nothing in a request says which folder the
# page that sent it was showing. The review held each as a strict xfail; the fix removed the markers.
# The fix: every bind is numbered (`server.binding`), a page sends its number back in a header, a
# write with no number or another one is refused on a server that can change folder, and the number
# is checked again inside the catalog lock.


def test_a_page_on_the_old_folder_cannot_write_into_the_new_one(tmp_path, started):
    a, plan_a = _scanned(tmp_path / "a", "IMG_1.JPG")
    b, plan_b = _scanned(tmp_path / "b", "IMG_2.JPG")
    assert plan_a == plan_b  # the collision the finding is about
    assert bind(started, a)[0] == 200
    page = json.loads(fetch(started, f"/t/{started.token}/api/plan/{plan_a}/items").body)
    a_ids = [item["id"] for item in page["items"]]
    assert bind(started, b)[0] == 200  # another tab changes folder
    body = json.dumps({"action": "approve", "item_ids": a_ids}).encode()
    fetch(started, f"/t/{started.token}/api/plan/{plan_a}/decide", "POST", body)
    conn = catalog(b)
    try:
        statuses = [r[0] for r in conn.execute("SELECT status FROM plan_items")]
    finally:
        conn.close()
    # Nothing on B was shown to the user, so nothing on B may be approved.
    assert statuses == ["proposed"], statuses


def test_a_change_of_folder_forgets_the_old_folder_s_look_alikes(tmp_path, started):
    from test_webui_lookalikes import BASE, FAR, _archive, _resized_jpeg

    a, plan_a = _archive(tmp_path / "a", {"base.png": BASE, "big.JPG": _resized_jpeg(BASE, 2)})
    b, plan_b = _archive(tmp_path / "b", {"base.png": BASE, "far.png": FAR})
    assert plan_a == plan_b
    for root, expected in ((a, {"base.png": ["big.JPG"], "big.JPG": ["base.png"]}), (b, {})):
        assert bind(started, root)[0] == 200
        reply = fetch(started, f"/t/{started.token}/api/plan/{plan_a}/items?limit=200")
        cards = json.loads(reply.body)["items"]
        alikes = {c["old_rel_path"]: c["look_alikes"] for c in cards if c["look_alikes"]}
        assert alikes == expected, (root.name, alikes)


def _decide(server, plan_id, item_ids, binding):
    body = json.dumps({"action": "approve", "item_ids": item_ids}).encode()
    url = f"/t/{server.token}/api/plan/{plan_id}/decide"
    return fetch(server, url, "POST", body, binding=binding)


def _statuses(root):
    conn = catalog(root)
    try:
        return [r[0] for r in conn.execute("SELECT status FROM plan_items")]
    finally:
        conn.close()


def test_each_bind_is_numbered_and_ping_names_it(tmp_path, started):
    """R1-F1: the number a page sends back. 0 before any bind, +1 per bind, never reused."""
    a, _ = _scanned(tmp_path / "a")
    b, _ = _scanned(tmp_path / "b", "IMG_2.JPG")
    ping = f"/t/{started.token}/api/ping"
    assert json.loads(fetch(started, ping).body)["binding"] == 0
    assert bind(started, a)[1]["binding"] == 1
    assert json.loads(fetch(started, ping).body)["binding"] == 1
    assert bind(started, b)[1]["binding"] == 2
    assert bind(started, a)[1]["binding"] == 3  # the same folder again is a new binding
    assert json.loads(fetch(started, ping).body)["binding"] == 3


def test_a_write_naming_the_old_binding_is_refused_and_the_current_one_is_served(
    tmp_path, started
):
    """R1-F1, the header operand: a page that sends A's number after a change to B is refused
    with the sentence and writes nothing; the page that bound B, sending B's, is served."""
    a, plan = _scanned(tmp_path / "a")
    b, _ = _scanned(tmp_path / "b", "IMG_2.JPG")
    old = bind(started, a)[1]["binding"]
    new = bind(started, b)[1]["binding"]
    refused = _decide(started, plan, [1], old)
    assert refused.status == 409
    assert json.loads(refused.body) == {"error": FOLDER_CHANGED}
    assert _statuses(b) == ["proposed"]
    assert _decide(started, plan, [1], new).status == 200
    assert _statuses(b) == ["approved"]


def test_a_read_naming_the_old_binding_is_refused(tmp_path, started):
    """R1-F1: the page's own reads (`fetch`) carry the number too, so an old page cannot show
    the new folder's items as if they were the old folder's."""
    a, plan = _scanned(tmp_path / "a")
    b, _ = _scanned(tmp_path / "b", "IMG_2.JPG")
    old = bind(started, a)[1]["binding"]
    bind(started, b)
    reply = fetch(started, f"/t/{started.token}/api/plan/{plan}/items", binding=old)
    assert (reply.status, json.loads(reply.body)) == (409, {"error": FOLDER_CHANGED})


def test_a_read_with_no_binding_is_served_a_write_with_none_is_not(tmp_path, started):
    """R1-F1's split: an `<img>` cannot send a header and a thumbnail is a read, so a read with no
    number is served; a WRITE with none is refused on `pam start`'s server."""
    a, plan = _scanned(tmp_path / "a")
    bind(started, a)
    assert fetch(started, f"/t/{started.token}/api/plan/{plan}/items").status == 200
    refused = _decide(started, plan, [1], None)
    assert (refused.status, json.loads(refused.body)) == (409, {"error": FOLDER_CHANGED})
    assert _statuses(a) == ["proposed"]


def test_pam_review_s_server_serves_a_write_with_no_binding(bound):
    """`pam review` never changes folder (`rebindable` is False), so its pages and every client
    built before M14 keep writing without the header."""
    server, plan_id = bound
    assert _decide(server, plan_id, [1], None).status == 200


def test_the_binding_is_checked_again_inside_the_catalog_lock(tmp_path, started):
    """R1-F1: a change of folder AFTER a request's header was checked and BEFORE its handler opens
    the catalog is refused at `connection()`, under the lock the bind itself holds."""
    a, _ = _scanned(tmp_path / "a")
    b, _ = _scanned(tmp_path / "b", "IMG_2.JPG")
    old = bind(started, a)[1]["binding"]
    bind(started, b)
    started.sent.binding = old  # what `_api` recorded for a request checked before the bind
    try:
        with pytest.raises(ApiError) as refused:
            with started.connection():
                pass
    finally:
        started.sent.binding = None
    assert (refused.value.status, refused.value.message) == (409, FOLDER_CHANGED)
    started.sent.binding = started.binding  # the current number opens the catalog
    try:
        with started.connection():
            pass
    finally:
        started.sent.binding = None


def test_a_change_of_folder_forgets_the_split_memory(tmp_path, started):
    """R1-F2's second half: split's previews and undo are about the old folder's faces."""
    a, _ = _scanned(tmp_path / "a")
    b, _ = _scanned(tmp_path / "b", "IMG_2.JPG")
    bind(started, a)
    before = started.split_memory
    bind(started, b)
    assert started.split_memory is not before


def test_a_change_of_folder_mid_request_is_refused_at_the_catalog(tmp_path, started):
    """R1-F1's second check, driven through HTTP: a request whose header was checked while A was
    bound, then a change to B before its handler opens the catalog (another tab, between the two),
    is refused at `connection()` - never answered from B's catalog. M3021: unarmed, it was served."""
    a, plan = _scanned(tmp_path / "a")
    b, _ = _scanned(tmp_path / "b", "IMG_2.JPG")
    old = bind(started, a)[1]["binding"]
    key = ("GET", "plan/{id}/items")
    original = started.api_handlers[key]

    def changes_folder_first(server, request):
        launcher.bind(server, b)
        return original(server, request)

    started.api_handlers[key] = changes_folder_first
    reply = fetch(started, f"/t/{started.token}/api/plan/{plan}/items", binding=old)
    assert (reply.status, json.loads(reply.body)) == (409, {"error": FOLDER_CHANGED})
    assert started.binding == old + 1  # the change of folder did happen, in between
