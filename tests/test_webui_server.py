"""`pam review` and the token-gated loopback server behind it (spec 8.2, M7 Task 7).

ONE MODULE-SCOPED LIVE SERVER, not one per test (M7 plan clarification 10). The suite
sits at ~100 s against a 120 s re-flag threshold, and the expensive half of a live
server is not the socket - it is a real-world library behind it, which is scanned and
planned once here.

THE BROWSER IS PATCHED FOR EVERY TEST IN THIS MODULE, by an autouse fixture rather than
by each test remembering. A suite that opens real browser tabs is a broken suite, and
the failure mode is silent on the machine of whoever wrote the test and loud on
everybody else's.

NO TEST HERE REACHES ANYTHING BUT 127.0.0.1, which is a property of the file rather
than an accident of what it asserts: `test_the_server_binds_loopback_and_nothing_else`
reads the bound address off the live server object, and every client below dials that
same address. The privacy canary deliberately does NOT cover `pam review` - it asserts
zero sockets across scan -> plan -> apply -> undo -> verify, and a loopback listener
would trip it by design - so this assertion is where "nothing binds beyond 127.0.0.1"
is actually held.
"""

import json
import logging
import sqlite3
import threading
import time
import webbrowser
from http.client import HTTPConnection
from importlib import resources
from pathlib import Path
from typing import NamedTuple

import pytest
from archives import approve_and_apply, catalog, open_plan, planned
from guards import (
    ALLOWED_MOVE_SITES,
    ALLOWED_WEBUI_EXECUTE_IMPORTS,
    FORBIDDEN_WEBUI_PACKAGE,
    move_call_sites,
    webui_execute_imports,
    webui_execute_reaches,
)

import pam.webui
from pam.catalog import repo
from pam.cli import main
from pam.errors import PamError, WorkspaceLockedError
from pam.paths import workspace_if_exists
from pam.sessionlog import close_session_log
from pam.verify import run_verify
from pam.webui import review_summary_lines, run_review
from pam.webui.server import (
    LOOPBACK_HOST,
    STATIC_FILES,
    ReviewServer,
    open_in_browser,
    serve_until_interrupted,
)

# --- the browser, never opened for real ---------------------------------------------

# Captured at import, before any fixture has run, so the patch can be checked from the
# outside. See `test_the_browser_patch_is_autouse_and_not_opt_in`.
_REAL_WEBBROWSER_OPEN = webbrowser.open


@pytest.fixture(autouse=True)
def opened_urls(monkeypatch):
    """Every test in this module gets a recording stand-in for `webbrowser.open`."""
    urls: list[str] = []

    def recorder(url, *args, **kwargs):
        urls.append(url)
        return True

    monkeypatch.setattr(webbrowser, "open", recorder)
    return urls


def test_the_browser_patch_is_autouse_and_not_opt_in():
    """REVIEW FINDING M619a. `autouse=True` above was held by nothing.

    The one test that would notice it going away - `test_the_browser_is_opened_with
    _exactly_the_printed_url` - requests `opened_urls` BY NAME, so it patches itself.
    Changing the decorator to a plain `@pytest.fixture()` left all 1408 tests green
    while four `review_with` calls reached the real `webbrowser.open`: measured, with a
    logging shim standing in for the browser, at four launches and 80/80 green.

    This test takes no fixture argument on purpose. It is therefore only ever patched
    by the autouse, and it fails the moment the autouse stops.
    """
    assert webbrowser.open is not _REAL_WEBBROWSER_OPEN


# --- one live server for the whole module -------------------------------------------


class Live(NamedTuple):
    server: ReviewServer
    root: Path
    plan_id: int
    token: str
    log_path: Path
    logger: logging.Logger


class Reply(NamedTuple):
    status: int
    headers: dict[str, str]
    body: bytes


def fetch(live: Live, path: str, method: str = "GET") -> Reply:
    """One request to the live server, on a connection that is closed straight after."""
    conn = HTTPConnection(live.server.host, live.server.port, timeout=20)
    try:
        conn.request(method, path)
        response = conn.getresponse()
        return Reply(response.status, dict(response.getheaders()), response.read())
    finally:
        conn.close()


def await_log(live: Live, *fragments: str, timeout: float = 20.0) -> str:
    """The session log, read once every fragment the caller named is in it.

    THE SERVER LOGS AFTER IT RESPONDS. `_route` calls `_respond` and only then
    `log_http_request`, so `fetch` returns - and a test can read the file - before the
    serving thread has written that request's line. THE LAST REQUEST IS THE EXPOSED ONE:
    nothing serializes behind it, and flushing the handler cannot flush a line that has
    not been emitted yet.

    That race was lost by the hosted GATE lane twice out of two while every local run won
    it (~118 s local against 225-369 s hosted; the faster fast lane of the day passed both
    times - CI ran two lanes until M11 Task 1 retired the fast one, and there is now a
    single lane), so the M7 close tagged `v0.1-m7` over a red gate. Reproduced locally by delaying
    `log_http_request` (mutation M788), which is how this function was verified.

    A BARRIER, NOT A `time.sleep`: it returns the moment the lines are there, so a fast
    machine pays nothing, and it FAILS LOUDLY at the deadline naming what is missing. A
    fixed sleep would re-lose the same race on a slower runner and turn a red gate into an
    intermittent one, which is harder to diagnose than red.

    `fragments` MUST be non-empty, and that is M743's lesson rather than defensiveness:
    the caller's fragments are the anti-vacuity check that stops `token not in log`
    passing over a log that is empty. Emptying the list must FAIL here, not quietly wait
    for nothing and hand back a file with no requests in it.

    The timeout message names the MISSING FRAGMENTS and the line count, never the log's
    contents: this helper exists to serve a test about a secret reaching the log, and a
    failure that dumps the log into CI output would publish the very thing under test.
    """
    assert fragments, "await_log with no fragments is a barrier that checks nothing"
    deadline = time.monotonic() + timeout
    while True:
        for handler in live.logger.handlers:
            handler.flush()
        text = live.log_path.read_text(encoding="utf-8")
        missing = [fragment for fragment in fragments if fragment not in text]
        if not missing:
            return text
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"{len(missing)} log line(s) never arrived within {timeout}s: {missing} "
                f"({len(text.splitlines())} line(s) in the log)"
            )
        time.sleep(0.01)


def _module_scoped_logger(logs_dir):
    """A log this module OWNS, not the shared `pam` one. REVIEW FINDING M628a.

    `open_session_log` hands back `logging.getLogger("pam")` after calling
    `handlers.clear()` on it, and `close_session_log` empties it again. That is right
    for a command, which owns the process; it is wrong for a MODULE-SCOPED fixture,
    because the very next `run_review` test tears the live server's file handler off
    the shared logger and never puts it back. From that moment the server logs nowhere,
    and `test_the_token_reaches_no_log_no_body_and_no_header` - the test that holds spec
    8.2's "never in the session log" promise - reads a file that stopped growing.

    Measured, not feared: moving that one test below the `run_review` tests turned it
    red at full-suite scale. Its greenness was an artefact of its position in the file
    plus `pytest.ini`'s no-random-ordering ruling. An isolated, non-propagating logger
    makes the fixture independent of what any other test does to the `pam` logger.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "webui-live-server.log"
    logger = logging.getLogger("pam.tests.webui_live")
    logger.setLevel(logging.DEBUG)
    # Not propagated to `pam`: these lines belong to this module, and `pam`'s handlers
    # are whatever the last command to open a session log left behind.
    logger.propagate = False
    logger.handlers.clear()
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    return logger, path


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """A real scanned+planned archive with a real server serving it, built once.

    PAM_HOME IS SET AND RESTORED INSIDE THE SETUP, never held across the yield.
    `conftest.py`'s autouse `isolated_pam_home` is function-scoped, so a module-scoped
    fixture that held the variable would fight it and could restore a deleted per-test
    directory over the top of the real one at module teardown. The server needs no
    PAM_HOME afterwards: it holds the workspace's absolute paths.
    """
    import os

    home = tmp_path_factory.mktemp("webui-home")
    previous = os.environ.get("PAM_HOME")
    os.environ["PAM_HOME"] = str(home)
    try:
        root, plan_id = planned(tmp_path_factory.mktemp("webui-archive"), ["IMG_1.JPG"])
        ws = workspace_if_exists(root)
        assert ws is not None
    finally:
        if previous is None:
            os.environ.pop("PAM_HOME", None)
        else:
            os.environ["PAM_HOME"] = previous

    logger, log_path = _module_scoped_logger(ws.logs_dir)
    server = ReviewServer(workspace=ws, plan_id=plan_id, logger=logger)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Live(server, root, plan_id, server.token, log_path, logger)
    server.shutdown()
    server.server_close()
    thread.join(timeout=20)
    close_session_log(logger)


# --- what binds, and where --------------------------------------------------------


def test_the_server_binds_loopback_and_nothing_else(live):
    """Read off the LIVE object and compared to the LITERAL spec 8.2 spells.

    `0.0.0.0` or a hostname would put the user's archive - and the routes that decide
    what `pam apply` renames it to - on the local network. The port is OS-assigned, so
    it is asserted only to be a real one.

    REVIEW FINDING M608a. Every assertion here used to read `LOOPBACK_HOST`, which is
    the constant that DECIDES the bound address - an oracle built from the definition
    it checks, the `repo.ENRICHERS` shape. Setting `LOOPBACK_HOST = "0.0.0.0"` left
    this test green; the 22 failures that run produced were all `OSError` out of
    `fetch`, because Windows refuses `connect()` to `0.0.0.0` as a destination. That is
    a platform accident, not a pin, and it would not exist on a POSIX runner.
    """
    assert LOOPBACK_HOST == "127.0.0.1"
    assert live.server.server_address[0] == "127.0.0.1"
    assert live.server.socket.getsockname()[0] == "127.0.0.1"
    assert live.server.port > 0
    assert live.server.url == f"http://127.0.0.1:{live.server.port}/t/{live.token}/"


# --- the token gate ---------------------------------------------------------------


def test_the_index_page_is_served_under_the_token(live):
    reply = fetch(live, f"/t/{live.token}/")
    packaged = (resources.files("pam.webui") / "static" / "index.html").read_bytes()
    assert reply.status == 200
    assert reply.body == packaged
    assert reply.headers["Content-Type"] == "text/html; charset=utf-8"


@pytest.mark.parametrize("name", sorted(STATIC_FILES))
def test_every_static_file_is_served_by_name_byte_identical_to_what_ships(live, name):
    """M7 Task 11 widened this from `index.html` to all three shipped files.

    BYTE-IDENTICAL, not merely 200. `tests/test_webui_static.py` reads these files off
    disk and cross-checks them against each other - ids, tokens, copy - and every one of
    those assertions is about the file the BROWSER gets only if the route hands the
    packaged bytes back unaltered. A route that re-encoded, minified or line-ending
    normalized on the way out would leave that whole module checking a different
    document from the one under test.
    """
    reply = fetch(live, f"/t/{live.token}/{name}")
    packaged = (resources.files("pam.webui") / "static" / name).read_bytes()
    assert reply.status == 200
    assert reply.body == packaged
    assert reply.headers["Content-Type"] == STATIC_FILES[name]


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/index.html",
        "/api/ping",
        "/t",
        "/t/",
        "/t/not-the-token/",
        "/t/not-the-token/api/ping",
        # A prefix and an extension of the real token: `compare_digest` compares the
        # whole string, and a `startswith` would let the first of these through.
        "/t/{token_prefix}/",
        "/t/{token}x/",
        # REVIEW FINDING M605a. The RIGHT token under the WRONG route prefix. Spec 8.2
        # is normative that every route lives under `/t/<secret>/` - the API, the static
        # files and M11's `/faces/*` - so `/x/<token>/` is not a spelling of it. Without
        # these two rows the `parts[0] != "t"` half of the gate is held by nothing: the
        # whole-guard deletion is caught by the one-segment rows above, and dropping
        # this operand alone left all 1408 tests green.
        "/x/{token}/",
        "/x/{token}/api/ping",
    ],
)
def test_a_request_without_the_token_is_404_with_an_empty_body(live, path):
    """Amendment A3: never 401, never 403, never a redirect, and no body to read.

    A distinguishable rejection tells a page probing 127.0.0.1 that pam is listening.
    """
    reply = fetch(live, path.format(token=live.token, token_prefix=live.token[:8]))
    assert reply.status == 404
    assert reply.body == b""
    assert "Location" not in reply.headers
    assert "WWW-Authenticate" not in reply.headers
    assert reply.headers["Content-Length"] == "0"


def test_a_non_ascii_token_is_a_404_and_not_a_crash(live):
    """`secrets.compare_digest` REFUSES a str carrying non-ASCII (TypeError).

    Compared as bytes for exactly this reason: a request for `/t/token/` must be
    the ordinary 404, not a 500 that tells the caller its input reached a comparison.
    """
    reply = fetch(live, "/t/t%C3%B6ken/")
    assert (reply.status, reply.body) == (404, b"")


def test_a_raw_non_ascii_byte_in_the_request_line_is_a_404_and_not_a_crash(live):
    """REVIEW FINDING M607a. The test above CANNOT reach the branch it describes.

    `urlsplit(...).path` never percent-decodes, so `/t/t%C3%B6ken/` arrives at
    `compare_digest` as the pure-ASCII string `t%C3%B6ken` and a `str` comparison is
    perfectly happy with it. Dropping both `.encode("utf-8")` calls left the whole
    suite green - the bytes comparison was reasoned about correctly in the code and
    pinned by nothing.

    A raw byte above 0x7F cannot be sent through `http.client`, which encodes the
    request line as ASCII, so this speaks the protocol on a socket. `BaseHTTPRequest
    Handler` decodes the request line as latin-1, which turns `\\xf6` into a non-ASCII
    `str`; `secrets.compare_digest` raises `TypeError` on one, and the handler turns
    that into a 500 carrying `comparing strings with non-ASCII characters is not
    supported` - a distinguishable rejection, which is exactly what amendment A3
    forbids. Measured on the mutant before this test existed.

    `socket` is imported here rather than in `pam/`: spec 10's capability guard walks
    the production package only, and speaking HTTP by hand is the point of the test.
    """
    import socket

    client = socket.create_connection((live.server.host, live.server.port), timeout=20)
    try:
        client.sendall(b"GET /t/t\xf6ken/ HTTP/1.1\r\nHost: pam\r\nConnection: close\r\n\r\n")
        raw = b""
        while chunk := client.recv(4096):
            raw += chunk
    finally:
        client.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    assert head.split(b"\r\n")[0] == b"HTTP/1.1 404 Not Found", head
    assert body == b""
    # ...and the server is still serving, which a 500 out of the router would also be -
    # so the assertion that matters is the status above, not this one on its own.
    assert fetch(live, f"/t/{live.token}/api/ping").status == 200


@pytest.mark.parametrize(
    "path",
    [
        "/t/{token}/../secret",
        "/t/{token}/%2e%2e/secret",
        "/t/{token}/static/index.html",
        "/t/{token}/index.html/extra",
        "/t/{token}/catalog.db",
    ],
)
def test_a_static_name_outside_the_allowlist_is_404(live, path):
    """Traversal is unreachable BY CONSTRUCTION: names are looked up in `STATIC_FILES`,
    never joined onto a directory, so `..` is simply not a name that is in it."""
    reply = fetch(live, path.format(token=live.token))
    assert (reply.status, reply.body) == (404, b"")


# --- the API route ----------------------------------------------------------------


def test_api_ping_reports_the_plan_the_server_is_serving(live):
    """Read back through a per-request connection on a WORKER thread.

    That is the half of clarification 5 a shared connection would fail outright:
    `check_same_thread` is True and stays True, so a handle opened on the main thread
    and used here would raise instead of answering.
    """
    reply = fetch(live, f"/t/{live.token}/api/ping")
    assert reply.status == 200
    # M14 Task 5d (W23): ping names the bound folder, the archive this fixture planned.
    assert json.loads(reply.body) == {
        "plan_id": live.plan_id,
        "status": "open",
        "binding": 1,
        "folder": str(live.root.resolve()),
        "recovery": None,
    }


def test_an_unknown_api_name_is_404(live):
    assert fetch(live, f"/t/{live.token}/api/nope").status == 404


def test_a_post_to_an_unknown_path_is_404_and_not_501(live):
    """The base class answers an unimplemented method with 501, which is exactly the
    distinguishable rejection amendment A3 forbids. `do_POST` exists to prevent it,
    before any POST route does."""
    reply = fetch(live, f"/t/{live.token}/api/ping", method="POST")
    assert (reply.status, reply.body) == (404, b"")


def test_a_post_body_is_drained_and_does_not_become_the_next_request(live):
    """REVIEW FINDING M621a. `do_POST`'s `_drain_request_body()` was pinned by nothing.

    The test above sends NO body - `http.client` writes `Content-Length: 0` for a
    bodyless POST - and closes the connection immediately, so neither half of the
    hazard was exercised and deleting the drain left all 1408 tests green. The comment
    on the call describes the failure exactly, which is the "this cannot happen" shape
    this project has learned to treat as a test nobody wrote.

    HTTP/1.1 keeps the connection open, so an undrained body is read as the NEXT
    request line: the second request below comes back as a 400 (or as no response at
    all) instead of the plan. Task 8 ships the first real POST routes, and every one of
    them carries a JSON body.
    """
    conn = HTTPConnection(live.server.host, live.server.port, timeout=20)
    try:
        conn.request("POST", f"/t/{live.token}/api/ping", body=b'{"item_ids": [1, 2, 3]}')
        first = conn.getresponse()
        first.read()
        assert first.status == 404
        # The same connection, deliberately: this is what the drain exists for.
        conn.request("GET", f"/t/{live.token}/api/ping")
        second = conn.getresponse()
        body = second.read()
        assert second.status == 200, "the POST body was read as the next request line"
        assert json.loads(body) == {
            "plan_id": live.plan_id,
            "status": "open",
            "binding": 1,
            "folder": str(live.root.resolve()),
            "recovery": None,
        }
    finally:
        conn.close()


def test_a_failing_handler_is_a_500_and_the_server_keeps_serving(live):
    """A handler bug must not end a half-reviewed session (M7 plan Task 7 Step 3)."""

    def exploding(server, request):
        raise RuntimeError("this handler is deliberately broken")

    live.server.api_handlers[("GET", "boom")] = exploding
    try:
        reply = fetch(live, f"/t/{live.token}/api/boom")
        assert reply.status == 500
        assert "deliberately broken" in json.loads(reply.body)["error"]
        # The point of the test: the NEXT request still works.
        assert fetch(live, f"/t/{live.token}/api/ping").status == 200
    finally:
        del live.server.api_handlers[("GET", "boom")]


def test_a_pam_error_reaches_the_page_as_its_sentence_and_nothing_else(live):
    """M7 Task 13, inherited from Task 10's review. `_as_server_error` prefixed EVERY
    exception with its class name, so an exiftool failure during thumbnail extraction
    rendered as `ExifToolError: exiftool was not found...` in the page's own banner -
    a class name in copy UX 8 says carries no jargon.

    Spec 13 draws the line this test pins: a `PamError` already IS the one clear line,
    so nothing is added to it.
    """
    from pam.errors import ExifToolError

    def missing_tool(server, request):
        raise ExifToolError("exiftool was not found. Install it, then run: pam doctor")

    live.server.api_handlers[("GET", "notool")] = missing_tool
    try:
        detail = json.loads(fetch(live, f"/t/{live.token}/api/notool").body)["error"]
    finally:
        del live.server.api_handlers[("GET", "notool")]

    assert detail == "exiftool was not found. Install it, then run: pam doctor"
    assert "ExifToolError" not in detail
    assert "Error:" not in detail


def test_an_unexpected_exception_keeps_its_type_because_it_has_no_sentence(live):
    """THE OTHER HALF, and the reason the fix above is not `str(error)` for everything.

    Spec 13 answers an unexpected exception with a traceback precisely because its
    message is not user-facing copy: `[Errno 13] Permission denied` does not say what
    kind of failure it is. Dropping the type would leave the page's banner saying less
    than the thing it is reporting, and the class name is the smallest honest
    substitute for the traceback a JSON body cannot carry.
    """

    def surprising(server, request):
        raise PermissionError(13, "Permission denied")

    live.server.api_handlers[("GET", "surprise")] = surprising
    try:
        detail = json.loads(fetch(live, f"/t/{live.token}/api/surprise").body)["error"]
    finally:
        del live.server.api_handlers[("GET", "surprise")]

    assert detail.startswith("PermissionError: ")
    assert "Permission denied" in detail


# --- the token appears in exactly two places --------------------------------------


def test_the_static_page_carries_no_token_and_no_external_url():
    """The page fetches `api/...` RELATIVELY, so the browser supplies the token prefix.

    And spec 8.2 rev 12: no external URL in any static file - no CDN, no font, no
    `https://` anywhere. A page that fetches a font announces the review session to a
    third party, and a canary watching pam's own sockets cannot see the browser's.

    M7 Task 11 MOVED the evidence, and this test was re-pointed rather than relaxed. Task
    7's placeholder page carried its one call inline, so `fetch("api/ping")` in
    `index.html` was the whole proof; the real page keeps every call in `app.js`. The
    replacement is STRICTLY STRONGER than the literal it replaces, because it pins the
    BUG CLASS rather than one spelling of it: a ROOT-RELATIVE path (`/api/...`) is the way
    relative fetching actually breaks. It parses, it looks right, and it drops the
    `/t/<token>/` prefix the browser was supplying - so every request 404s under spec
    8.2's silent-empty rejection, which tells the page nothing about why.

    The `://` loop above already refuses an absolute URL, and it grew to cover
    `style.css` and `app.js` by itself when `STATIC_FILES` did. Only this half needed
    moving.
    """
    for name in STATIC_FILES:
        text = (resources.files("pam.webui") / "static" / name).read_bytes().decode("utf-8")
        assert "://" not in text, f"{name} names an external URL"
        assert "/t/" not in text, f"{name} carries a token prefix"
        # `"/api`, `'/api` and the backtick form: every way a JS string literal can open
        # onto a root-relative API path. Checked in EVERY static file, not only the
        # script, because a root-relative URL in markup or CSS breaks the same way.
        for quote in ('"', "'", "`"):
            assert f"{quote}/api" not in text, (
                f"{name} fetches a ROOT-RELATIVE api path, which drops the token prefix"
            )

    # M14 Task 1: the review page is five scripts, so the positive half reads all of them.
    script = "\n".join(
        (resources.files("pam.webui") / "static" / name).read_bytes().decode("utf-8")
        for name in (
            "pam-core.js",
            "pam-trust.js",
            "pam-keys.js",
            "pam-row.js",
            "pam-selection.js",
            "pam-list.js",
            "pam-bar.js",
            "pam-detail.js",
            "pam-chooser.js",
            "pam-reading.js",
            "pam-welcome.js",
            "pam-confirm.js",
            "pam-done.js",
            "pam-stopped.js",
            "pam-history.js",
            "app.js",
        )
    )
    # And the positive half, so the two assertions above cannot both pass vacuously on a
    # page that fetches nothing at all. Spelled as the prefix every endpoint shares.
    assert "api/" in script, "app.js reaches no API path at all - the negatives are vacuous"


def test_every_shipped_static_file_is_routable_and_every_routable_name_ships():
    """Both directions. A file with no entry is unreachable; an entry with no file is a
    500 waiting for the request that asks for it."""
    on_disk = {
        entry.name
        for entry in (resources.files("pam.webui") / "static").iterdir()
        if entry.is_file()
    }
    assert on_disk == set(STATIC_FILES)


# --- the connection model ---------------------------------------------------------


def test_a_request_connection_is_closed_before_the_lock_releases(live):
    """One request, one connection, opened and closed inside the single DB lock."""
    with live.server.connection() as conn:
        assert live.server.db_lock.locked()
        assert repo.plan_status(conn, live.plan_id) == "open"
    assert not live.server.db_lock.locked()
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        conn.execute("SELECT 1")


# --- the serve loop's two ends ----------------------------------------------------


class _FakeServer:
    def __init__(self, error):
        self.error = error
        self.served = 0

    def serve_forever(self):
        self.served += 1
        raise self.error


def test_ctrl_c_ends_the_serve_loop_without_raising():
    """Clarification 4: stopping the server is the DESIGNED way out, so it is not an
    interruption and the caller goes on to print its summary and exit 0."""
    fake = _FakeServer(KeyboardInterrupt())
    assert serve_until_interrupted(fake) is None
    assert fake.served == 1


def test_an_unexpected_error_in_the_serve_loop_is_not_absorbed():
    """The other half of the split: a crash must not masquerade as a finished session.
    It propagates to cli.py's spec 13 handler - traceback, `Log:`, exit 2."""
    with pytest.raises(RuntimeError, match="the socket died"):
        serve_until_interrupted(_FakeServer(RuntimeError("the socket died")))


# --- opening the browser ----------------------------------------------------------


def test_the_browser_is_opened_with_the_url_and_reports_success():
    seen = []
    assert open_in_browser("http://127.0.0.1:1/t/x/", lambda url: seen.append(url) or True)
    assert seen == ["http://127.0.0.1:1/t/x/"]


@pytest.mark.parametrize(
    "opener",
    [
        pytest.param(lambda url: False, id="returns-false"),
        pytest.param(
            lambda url: (_ for _ in ()).throw(OSError("no browser here")), id="raises"
        ),
    ],
)
def test_a_browser_that_will_not_open_is_a_false_and_never_an_exception(opener):
    """`webbrowser.open` reports failure BOTH ways and on a headless machine both are
    ordinary. Neither may end a review session that is otherwise working."""
    assert open_in_browser("http://127.0.0.1:1/t/x/", opener) is False


# --- the guard: nothing under pam/webui/ can reach a rename primitive -------------


def webui_sources(webui_dir=None):
    """Every module under `pam/webui/` (or a scratch copy of it), as (relative posix path,
    package, source)."""
    webui_dir = Path(pam.webui.__file__).parent if webui_dir is None else webui_dir
    found = []
    for path in sorted(webui_dir.rglob("*.py")):
        relative = path.relative_to(webui_dir)
        package = ".".join(["pam", "webui", *relative.parts[:-1]])
        found.append((relative.as_posix(), package, path.read_text(encoding="utf-8")))
    return found


def test_the_webui_walk_reaches_the_modules_it_claims_to_guard():
    """A guard that has quietly stopped looking is invisible from inside itself
    (review finding M460's lesson, applied to the newest guard rather than re-learned).

    REVIEW FINDING M618a. This test used to assert `{"__init__.py", "server.py"} <=
    names`, which is a hardcoded subset and therefore true of ANY walk that keeps
    today's two modules. Freezing `webui_sources()` to exactly those two names left all
    1408 tests green - and `pam/webui/api.py` arrives in Task 8, at which point the
    guard would silently stop covering the module that serves the decide endpoints.

    So the reach is checked against an INDEPENDENT enumeration - `os.walk` rather than
    `Path.rglob` - exactly as `test_net.py::test_the_walk_really_covers_the_whole
    _package` does for the network guard. A mutation to one is not a mutation to the
    other. The named pair below stays as a floor for the case where both break the
    same way.
    """
    import os

    webui_dir = Path(pam.webui.__file__).parent
    walked = {
        (Path(dirpath) / filename).relative_to(webui_dir).as_posix()
        for dirpath, _, filenames in os.walk(webui_dir)
        for filename in filenames
        if filename.endswith(".py")
    }
    names = {name for name, _package, _source in webui_sources()}
    assert names == walked
    assert {"__init__.py", "server.py"} <= names


def webui_offenders(webui_dir=None):
    """(module, reaches) for every webui module that reaches a rename primitive; `[]` is the
    rule. Each module is judged against ITS OWN pairs in `ALLOWED_WEBUI_EXECUTE_IMPORTS` (U19)."""
    return [
        (name, reaches)
        for name, package, source in webui_sources(webui_dir)
        if (reaches := webui_execute_reaches(source, f"webui/{name}", package))
    ]


def test_no_module_under_webui_can_reach_a_rename_primitive():
    """SPEC 8.2's headline claim, as a red test rather than as a sentence: "the UI reads
    and approves; it never applies" - and since M14's U19, it applies through ONE door.

    Stronger than `ALLOWED_MOVE_SITES`, which counts CALL sites - a webui module that
    imported `perform_verified_rename` and called it would be counted against
    `execute/rename.py`'s blessed entry and pass. This forbids the reach.
    """
    assert webui_offenders() == []
    movers = [
        (name, sites)
        for name, _package, source in webui_sources()
        if (sites := move_call_sites(source))
    ]
    assert movers == []


@pytest.mark.parametrize(
    "planted",
    [
        "import pam.execute.rename\n",
        "import pam.execute.rename as r\n",
        "import pam.execute\n",
        "from pam.execute import rename\n",
        "from pam.execute import rename as r\n",
        "from pam.execute import recover, apply\n",
        "from pam.execute.rename import perform_verified_rename\n",
        "from pam.execute.unit import execute_rename_unit\n",
        "from pam.execute.journal_ops import RenameTransaction\n",
        "from pam.execute.undo import run_undo\n",
        # The relative spellings, which the NETWORK guard deliberately skips and this
        # one deliberately does not - inside `pam/webui/` they are the shape somebody
        # reaches for when a plain import trips a guard.
        "from ..execute import rename\n",
        "from ..execute.rename import perform_verified_rename\n",
        # And the two ways around a guard that only reads import statements.
        'import importlib\nx = importlib.import_module("pam.execute.apply")\n',
        'x = __import__("pam.execute.rename")\n',
        # Naming the filesystem move directly, call or not.
        "import os\nos.rename(a, b)\n",
        "import os\nhandler = os.rename\n",
        "import os as o\no.replace(a, b)\n",
        "import shutil\nshutil.move(a, b)\n",
        "from os import rename\n",
        "from os import replace as swap\n",
        "from shutil import move\n",
    ],
)
def test_the_webui_guard_catches_every_route_to_a_rename(planted):
    """The guard proven ENFORCING, one row per route in. A function returning `[]`
    unconditionally would pass the test above, because the package it walks is clean.
    Planted in `webui/api.py`, a module with NO pair in the allowlist (U19)."""
    assert webui_execute_reaches(planted, "webui/api.py") != []


@pytest.mark.parametrize(
    "benign",
    [
        # `webui/__init__.py`'s two pairs, each with its reason in `ALLOWED_WEBUI_EXECUTE
        # _IMPORTS`. Neither can move a file, and the recovery one is REQUIRED by
        # `test_recovery_is_wired_into_exactly_the_archive_scoped_commands`.
        "from pam.execute.recover import run_recovery\n",
        "from pam.execute.reporter import announce_interrupted\n",
        "from pam.execute import recover, reporter\n",
        "from ..execute.recover import run_recovery\n",
        # Ordinary code that a rule over bare attribute names would fire on. A guard
        # that fires on ordinary code gets routed around rather than obeyed.
        'text = path.replace(token, "<token>")\n',
        '"""This docstring names os.rename, os.replace and shutil.move out loud."""\n',
        "from pam.catalog import repo\n",
        "from pam.naming.planner import run_plan\n",
    ],
)
def test_the_webui_guard_does_not_fire_on_the_blessed_imports_or_on_prose(benign):
    assert webui_execute_reaches(benign, "webui/__init__.py") == []


def test_the_webui_execute_allowances_are_named_pairs_under_the_forbidden_package():
    """Pinned so that widening the list is a deliberate edit, and so that an entry that
    does not actually sit under `pam.execute` cannot quietly bless nothing. M14 U19: PAIRS,
    spelled here from U19 and from the modules that imported `recover` / `reporter` at
    `87e7f8e` - never read back from the list. 2 -> 6; the M14 close ratifies it. M15 Task 4
    (`m14.md` C5): U19's two door pairs moved, as the M14 close pre-authorised, from `jobs.py` to
    `rename_jobs.py` with the job bodies; nothing else moved, and it is still six."""
    assert ALLOWED_WEBUI_EXECUTE_IMPORTS == [
        ("webui/__init__.py", "pam.execute.recover"),
        ("webui/__init__.py", "pam.execute.reporter"),
        ("webui/jobs.py", "pam.execute.recover"),
        ("webui/launcher.py", "pam.execute.recover"),
        ("webui/rename_jobs.py", "pam.execute.apply"),
        ("webui/rename_jobs.py", "pam.execute.undo"),
    ]
    for module, imported in ALLOWED_WEBUI_EXECUTE_IMPORTS:
        assert module.startswith("webui/") and module.endswith(".py")
        assert imported.startswith(FORBIDDEN_WEBUI_PACKAGE + ".")


# U19's per-module rule: one row per (importing module, source, what the guard must report),
# written by hand from U19 - `rename_jobs.py` is the ONE door to `apply` / `undo` (since M15
# Task 4; `jobs.py` was until then), and every older pair stays with the module that imports it.
# Its length is asserted below: pytest SKIPS an empty parametrize rather than failing it.
U19_ROWS = [
    # The door: `rename_jobs.py` may import the two lock-held entry points, in any spelling.
    ("webui/rename_jobs.py", "from pam.execute.apply import apply_locked\n", []),
    ("webui/rename_jobs.py", "from pam.execute.undo import undo_locked\n", []),
    ("webui/rename_jobs.py", "from pam.execute import apply, undo\n", []),
    ("webui/rename_jobs.py", "from ..execute.apply import apply_locked\n", []),
    # ...and nothing else under `pam.execute` beyond its pairs - not even `jobs.py`'s recover.
    (
        "webui/rename_jobs.py",
        "from pam.execute.unit import execute_rename_unit\n",
        ["pam.execute.unit"],
    ),
    (
        "webui/rename_jobs.py",
        "from pam.execute.rename import perform_verified_rename\n",
        ["pam.execute.rename"],
    ),
    ("webui/rename_jobs.py", "from pam.execute import apply, rename\n", ["pam.execute.rename"]),
    (
        "webui/rename_jobs.py",
        "from pam.execute.reporter import progress_line\n",
        ["pam.execute.reporter"],
    ),
    (
        "webui/rename_jobs.py",
        "from pam.execute import apply, undo, recover\n",
        ["pam.execute.recover"],
    ),
    ("webui/rename_jobs.py", "import pam.execute\n", ["pam.execute"]),
    ("webui/rename_jobs.py", "import os\nos.rename(a, b)\n", ["os.rename"]),
    # M15 Task 4: `jobs.py` keeps its join-then-recover pair and is no longer the door.
    ("webui/jobs.py", "from pam.execute.recover import run_recovery\n", []),
    ("webui/jobs.py", "from pam.execute.apply import apply_locked\n", ["pam.execute.apply"]),
    ("webui/jobs.py", "from pam.execute import undo\n", ["pam.execute.undo"]),
    # A SECOND webui module reaching the door is a failure, whatever the spelling.
    ("webui/server.py", "from pam.execute.apply import apply_locked\n", ["pam.execute.apply"]),
    ("webui/server.py", "from ..execute.apply import apply_locked\n", ["pam.execute.apply"]),
    ("webui/job_api.py", "from pam.execute.undo import undo_locked\n", ["pam.execute.undo"]),
    ("webui/launcher.py", "from pam.execute.apply import run_apply\n", ["pam.execute.apply"]),
    ("webui/__init__.py", "from pam.execute import apply\n", ["pam.execute.apply"]),
    (
        "webui/routing.py",
        'import importlib\nx = importlib.import_module("pam.execute.undo")\n',
        ["pam.execute.undo"],
    ),
    # The older names are keyed too: `reporter` is `__init__.py`'s; `recover` is not everyone's.
    (
        "webui/launcher.py",
        "from pam.execute.reporter import announce_interrupted\n",
        ["pam.execute.reporter"],
    ),
    (
        "webui/routing.py",
        "from pam.execute.recover import run_recovery\n",
        ["pam.execute.recover"],
    ),
]
assert len(U19_ROWS) == 22


@pytest.mark.parametrize(("module", "source", "expected"), U19_ROWS)
def test_the_webui_guard_holds_each_execute_import_to_the_module_it_is_listed_for(
    module, source, expected
):
    assert webui_execute_reaches(source, module) == expected


def _scratch_webui(tmp_path):
    """A scratch copy of `pam/webui/`'s modules, to plant into without touching the package."""
    copy = tmp_path / "webui"
    copy.mkdir()
    for path in Path(pam.webui.__file__).parent.glob("*.py"):
        (copy / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return copy


def test_an_unlisted_execute_import_planted_in_a_scratch_copy_fails_the_walk(tmp_path):
    """U19's negative control over the WHOLE walk, not one string: the walk over an untouched
    copy is clean; one unlisted import appended to a copied module is named, module and import;
    and the import U19 allows `rename_jobs.py` is still refused in `server.py` - and, since M15
    Task 4, in `jobs.py`, the module it was cut from. (CLAUDE.md: a guard that
    walks the wrong AST node passes everything.)"""
    copy = _scratch_webui(tmp_path)
    assert webui_offenders(copy) == []

    def plant(name, line):
        target = copy / name
        target.write_text(target.read_text(encoding="utf-8") + line, encoding="utf-8")

    plant("server.py", "from pam.execute.apply import apply_locked\n")
    assert webui_offenders(copy) == [("server.py", ["pam.execute.apply"])]

    plant("rename_jobs.py", "from pam.execute.apply import apply_locked\n")  # the door: allowed
    assert webui_offenders(copy) == [("server.py", ["pam.execute.apply"])]

    plant("rename_jobs.py", "from pam.execute.unit import execute_rename_unit\n")
    assert webui_offenders(copy) == [
        ("rename_jobs.py", ["pam.execute.unit"]),
        ("server.py", ["pam.execute.apply"]),
    ]

    plant("jobs.py", "from pam.execute.apply import apply_locked\n")  # no longer the door
    assert webui_offenders(copy) == [
        ("jobs.py", ["pam.execute.apply"]),
        ("rename_jobs.py", ["pam.execute.unit"]),
        ("server.py", ["pam.execute.apply"]),
    ]


def test_every_webui_execute_allowance_is_used_except_the_two_task_6b_will_use():
    """An allowance nothing uses is a door left open. Every pair is an import a module really
    makes: Task 6a listed `jobs.py`'s `apply` and `undo` (`rename_jobs.py`'s since M15 Task 4)
    ahead of 6b's job bodies, and Task 6b
    EMPTIED THE SET BELOW in the commit that added those imports."""
    used = {
        (f"webui/{name}", imported)
        for name, package, source in webui_sources()
        for imported in webui_execute_imports(source, package)
    }
    assert used <= set(ALLOWED_WEBUI_EXECUTE_IMPORTS)
    assert set(ALLOWED_WEBUI_EXECUTE_IMPORTS) - used == set()


def test_u19_leaves_the_move_sites_exactly_as_they_were():
    """U19: "`ALLOWED_MOVE_SITES` does not change" - the page reaches the rename only through
    `apply_locked` / `undo_locked`. Spelled from `v0.1-m13`'s list: three entries."""
    assert ALLOWED_MOVE_SITES == [
        ("execute/rename.py", "perform_verified_rename", "os.rename"),
        ("faces/export.py", "_write_pair", "os.replace"),
        ("paths.py", "record_archive_root", "os.replace"),
    ]


# --- `pam review` end to end -------------------------------------------------------


def review_with(root, plan_id=None, on_serve=None, **kwargs):
    """Run one review session whose serve loop returns as soon as `on_serve` has run."""
    served = []

    def serve(server):
        served.append(server)
        if on_serve is not None:
            on_serve(server)

    summary = run_review(plan_id, root, serve=serve, **kwargs)
    assert len(served) == 1, "the serve loop ran once"
    return summary, served[0]


def test_a_review_session_serves_prints_and_closes(tmp_path, capsys):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    summary, server = review_with(root)
    out = capsys.readouterr()

    assert summary.plan_id == plan_id
    assert summary.url == server.url
    assert f"Review at {summary.url}" in out.out
    assert pam.webui.PAGE_PURPOSE in out.out
    assert pam.webui.STOP_HINT in out.out
    assert out.err.strip().splitlines()[-1] == f"Log: {summary.log_path}"
    assert summary.log_path is not None and summary.log_path.exists()


def test_the_browser_is_opened_with_exactly_the_printed_url(tmp_path, opened_urls):
    root, _plan_id = planned(tmp_path, ["IMG_1.JPG"])
    summary, _server = review_with(root)
    assert opened_urls == [summary.url]


def test_a_browser_that_will_not_open_prints_the_address_again(tmp_path, capsys):
    """The auto-open is a convenience; THE PRINTED URL IS AUTHORITATIVE."""
    root, _plan_id = planned(tmp_path, ["IMG_1.JPG"])
    summary, _server = review_with(root, opener=lambda url: False)
    out = capsys.readouterr().out
    assert pam.webui.BROWSER_FALLBACK.format(url=summary.url) in out


def test_the_server_holds_the_lock_for_the_whole_session(tmp_path):
    """M7 plan clarification 2, and the consequence is deliberate: a second pam command
    against this archive is refused until the user stops the server."""
    root, _plan_id = planned(tmp_path, ["IMG_1.JPG"])
    refusals = []

    def while_serving(server):
        with pytest.raises(WorkspaceLockedError) as refused:
            run_verify(root)
        refusals.append(str(refused.value))

    review_with(root, on_serve=while_serving)
    assert len(refusals) == 1
    assert "Another pam command is already running" in refusals[0]
    # And the lock is released afterwards, or `pam apply` could never follow.
    run_verify(root)


def test_the_closing_counts_are_read_after_the_session_not_before(tmp_path, capsys):
    """The user's decisions are made in the browser DURING the session. A count carried
    since startup would report the state the session began in (vision principle 9)."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG", "IMG_3.JPG"])

    def while_serving(server):
        with server.connection() as conn:
            items = repo.plan_items(conn, plan_id)
            repo.set_plan_item_status(conn, items[0].id, "approved")
            repo.set_plan_item_status(conn, items[1].id, "skipped")
            conn.commit()

    summary, _server = review_with(root, on_serve=while_serving)
    assert (summary.approved, summary.skipped, summary.remaining) == (1, 1, 1)
    out = capsys.readouterr().out
    assert f"Plan {plan_id}: 1 approved, 1 skipped, 1 still to review." in out
    assert f"Next: pam apply {plan_id}" in out


def test_ux_3s_three_command_chain_holds_end_to_end(tmp_path, capsys):
    """M7 Task 13 Step 1. UX 3's first-run journey is `plan -> review -> apply`, and
    until M7 the middle link did not exist: `pam plan` said `Next: pam apply`.

    ONE TEST FOR BOTH HANDOFFS, because the chain is the claim and neither half is one
    on its own. `pam plan` naming `pam review` is worthless if `pam review` then names
    something else, and a review summary naming `pam apply` is worthless if nothing
    sends the user to review. The plan id is asserted to be the SAME id all the way
    through - a chain that names the right commands with the wrong number is a chain
    the user cannot follow.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    capsys.readouterr()

    assert main(["plan", "--archive", str(root)]) == 0
    planned_out = capsys.readouterr().out
    replanned = open_plan(catalog(root)).id
    assert f"Next: pam review {replanned}" in planned_out

    def approve_one(server):
        with server.connection() as conn:
            repo.set_plan_item_status(conn, repo.plan_items(conn, replanned)[0].id, "approved")
            conn.commit()

    summary, _server = review_with(root, on_serve=approve_one)
    review_out = capsys.readouterr().out
    assert summary.plan_id == replanned
    assert f"Next: pam apply {replanned}" in review_out
    # And the link the chain does NOT have: review is the middle step, so its closing
    # block must not send the user back round to `pam plan`.
    assert "pam plan" not in review_out


def test_the_closing_counts_belong_to_the_reviewed_plan_and_no_other(tmp_path):
    """REVIEW FINDING M622a. `repo.plan_item_status_counts` lost its `WHERE plan_id = ?`
    and all 1408 tests stayed green, because every archive the webui tests build holds
    exactly one plan.

    It is reachable in ordinary use, not in theory: re-planning supersedes the open plan
    and **leaves its `plan_items` rows in the table** (J2 never deletes a row), so any
    archive that has been planned twice would have every one of its old items counted
    into the summary the user reads before typing `pam apply`.
    """
    from pam.naming.planner import run_plan

    root, first = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG", "IMG_3.JPG"])
    second = run_plan(root).plan_id
    assert second is not None and second != first

    conn = catalog(root)
    try:
        assert repo.plan_status(conn, first) == "superseded"
        # Asserted, not assumed: if superseding ever deleted the items, this test would
        # pass for the wrong reason and the filter would be unpinned again.
        assert len(repo.plan_items(conn, first)) == 3
    finally:
        conn.close()

    summary, _server = review_with(root)
    assert summary.plan_id == second
    assert (summary.approved, summary.skipped, summary.remaining) == (0, 0, 3)


def test_a_session_that_approved_nothing_points_back_at_review(tmp_path, capsys):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    summary, _server = review_with(root)
    out = capsys.readouterr().out
    assert (summary.approved, summary.skipped, summary.remaining) == (0, 0, 1)
    assert "Nothing is approved yet, so pam apply has nothing to do." in out
    assert f"Next: pam review {plan_id}" in out


def test_the_summary_lines_end_with_a_next_line_either_way():
    """UX spec 2: `pam verify` is the ONLY command allowed to end with a verdict
    instead of `Next:`, and `pam review` is not it."""
    for approved in (0, 4):
        summary = pam.webui.ReviewSummary(plan_id=7, approved=approved, remaining=2)
        assert review_summary_lines(summary)[-1].startswith("Next: ")


# --- the refusals (M7 plan clarification 3) ----------------------------------------


def test_review_refuses_when_no_plan_is_open(tmp_path):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    conn = catalog(root)
    try:
        repo.set_plan_status(conn, plan_id, "superseded")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PamError, match="no open plan") as refused:
        run_review(None, root, serve=lambda server: None)
    assert "Run: pam plan" in str(refused.value)


def test_review_refuses_an_unknown_plan_id(tmp_path):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    with pytest.raises(PamError, match=f"does not know a plan with id {plan_id + 99}"):
        run_review(plan_id + 99, root, serve=lambda server: None)


def test_review_refuses_when_more_than_one_plan_is_open(tmp_path):
    """Unreachable through pam - every scan and every plan supersedes the open ones -
    so pam says which ids it found and asks, rather than picking the first."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    conn = catalog(root)
    try:
        second = repo.create_plan(
            conn,
            now="2026-08-14T00:00:00",
            template="{date}",
            config_json="{}",
            app_version="0.1.0",
            schema_version=5,
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PamError, match="more than one open plan") as refused:
        run_review(None, root, serve=lambda server: None)
    assert f"{plan_id}, {second}" in str(refused.value)
    assert "pam review <PLAN_ID>" in str(refused.value)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("superseded", "Run: pam plan"),
        ("applied", "pam report session"),
        ("partially_applied", "pam report session"),
        ("undone", "pam report session"),
    ],
)
def test_review_refuses_a_plan_that_is_not_open_and_names_what_to_run(
    tmp_path, status, expected
):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    conn = catalog(root)
    try:
        repo.set_plan_status(conn, plan_id, status)
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(PamError) as refused:
        run_review(plan_id, root, serve=lambda server: None)
    message = str(refused.value)
    assert f"Plan {plan_id} is {pam.webui._HUMAN_STATUS[status]}" in message
    assert expected in message


def test_the_applying_refusal_exists_even_though_recovery_gets_there_first(tmp_path):
    """`applying` IS in the vocabulary and IS answered - but it cannot be reached
    through `run_review`, and that is worth writing down rather than leaving as a
    parametrised case that quietly tests something else.

    Spec 7.2's recovery runs at the START of every archive-scoped command and finalizes
    every plan left `applying`, so by the time `_resolve_plan` reads the status it is
    `applied` or `partially_applied`. The sentence is kept for the same reason
    `execute/validator.py` keeps its own: the vocabulary is spec 4's, not a list of the
    values one caller happens to observe.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    conn = catalog(root)
    try:
        repo.set_plan_status(conn, plan_id, "applying")
        conn.commit()
        message = pam.webui._plan_refusal(conn, plan_id, "applying")
    finally:
        conn.close()
    assert "half-applied after an interruption" in message
    assert "Run: pam plan" in message

    # ...and the reason it is not driven end to end, asserted rather than asserted-about:
    # recovery has already moved the plan on before `_resolve_plan` reads its status, so
    # the refusal the user gets is the APPLIED one.
    with pytest.raises(PamError, match="nothing left to review"):
        run_review(plan_id, root, serve=lambda server: None)
    conn = catalog(root)
    try:
        assert repo.plan_status(conn, plan_id) in ("applied", "partially_applied")
    finally:
        conn.close()


def test_an_applied_plan_with_a_rename_session_names_that_session(tmp_path):
    """The useful half of the refusal: the id `pam report session` actually wants."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    applied = approve_and_apply(plan_id, root, confirm=lambda confirmation: True)
    with pytest.raises(PamError) as refused:
        run_review(plan_id, root, serve=lambda server: None)
    assert f"pam report session {applied.session_id}" in str(refused.value)


def test_review_refuses_an_archive_that_was_never_scanned(tmp_path):
    root = tmp_path / "never-scanned"
    root.mkdir()
    with pytest.raises(PamError, match="has not scanned"):
        run_review(None, root, serve=lambda server: None)


def test_the_token_reaches_no_log_no_body_and_no_header(live):
    """Spec 8.2, swept over every site at once rather than pinned one at a time.

    The injected handler LEAKS THE TOKEN INTO ITS EXCEPTION MESSAGE on purpose. An
    exception raised inside a handler carries whatever the handler was holding, and the
    handler is holding the request path - so this is the one place a token could
    plausibly re-enter a response body, and a test that raised a token-free error would
    pass without exercising the elision at all.

    BOTH BRANCHES OF `_as_server_error`, and that is REVIEW FINDING M761. M7 Task 13 gave
    that method two paths where it had one - a `PamError` is rendered as its own sentence,
    anything else keeps its class name - and this test leaked the token through the
    UNEXPECTED path alone. Eliding only the unexpected half then passed the whole suite.
    A `PamError` is exactly the kind that carries a path: `WorkspaceLockedError` and
    `ExifToolError` are built from what the caller was holding.

    The two handlers are raised in ONE unparametrized test on purpose (M743's lesson): a
    control parametrized over the very set it protects is SKIPPED, not failed, when that
    set is emptied, and the run still reads green.
    """

    def leaking(server, request):
        raise RuntimeError(f"the token is {server.token} and here it is again")

    def leaking_pam_error(server, request):
        raise WorkspaceLockedError(f"another pam command is running at /t/{server.token}/")

    live.server.api_handlers[("GET", "leak")] = leaking
    live.server.api_handlers[("GET", "leak-expected")] = leaking_pam_error
    try:
        replies = [
            fetch(live, f"/t/{live.token}/"),
            fetch(live, f"/t/{live.token}/api/ping"),
            fetch(live, "/t/not-the-token/api/ping"),
            fetch(live, f"/t/{live.token}/api/leak"),
            fetch(live, f"/t/{live.token}/api/leak-expected"),
        ]
    finally:
        del live.server.api_handlers[("GET", "leak")]
        del live.server.api_handlers[("GET", "leak-expected")]

    assert replies[-1].status == 500
    assert replies[-2].status == 500
    # ...and the elided body still SAYS something, or "the token is absent" is satisfied
    # by a branch that returned an empty string. The sentence survives; the token does not.
    assert "another pam command is running" in replies[-1].body.decode("utf-8")
    for reply in replies:
        assert live.token not in reply.body.decode("utf-8", "replace")
        for name, value in reply.headers.items():
            assert live.token not in value, f"the token reached the {name} header"

    # The three fragments are the ANTI-VACUITY CHECK - without them `token not in log`
    # is satisfied by a log with no requests in it - and they are also what `await_log`
    # waits for, because the server writes its access-log line AFTER it responds. One
    # list, so the wait and the check can never drift apart; `await_log` refuses an
    # empty one.
    log = await_log(
        live,
        "review GET /t/<token>/api/ping -> 200",
        "review GET /t/<token>/api/leak -> 500",
        "review GET /t/<token>/api/leak-expected -> 500",
    )
    assert live.token not in log
