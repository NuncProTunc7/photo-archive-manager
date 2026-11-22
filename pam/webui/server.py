"""The review server: stdlib `ThreadingHTTPServer`, loopback only, token-gated (spec 8.2).

WHY STDLIB AND NOT FastAPI + uvicorn (rev 12, amendment A1). The runtime dependency
closure had been measured at every milestone gate since M4 and was exactly
`{click, pam}` when this server was written. The vision treats that as a property rather
than an accident, and a page that serves a JSON API to one browser on one machine is not
worth breaking it. (M9 Task 6 spent the closure on design 1's four sanctioned image and
geocoding packages - `tests/test_deps.py` now holds the list, asserted in both
directions. It did NOT make a web framework affordable: nothing about a stdlib
`ThreadingHTTPServer` becomes wrong because `Pillow` is installed, and the M7 argument
above stands unchanged.)
`ThreadingHTTPServer` binds `("127.0.0.1", 0)`, which is the whole server-side story:
the OS picks a free port and pam reads it back.

THE TOKEN IS THE ENTIRE AUTHORIZATION MECHANISM (spec 8.2 "Token gate", amendment A3).
Loopback binding is a PRIVACY boundary, not an authorization one - any page the user
has open can POST to `127.0.0.1`, and what these routes will write in M7 Task 8 is what
`pam apply` renames irreplaceable files to. So every route lives under `/t/<secret>/`,
`<secret>` is a per-session `secrets.token_hex(16)`, and the comparison is
`secrets.compare_digest`. Deliberately nothing more: no cookies, no CORS machinery, no
CSRF framework, no authentication system.

**Any other path is 404 with an empty body** - never 401, never 403, never a redirect,
and never a 501 for an unhandled method either, which is why `do_POST` exists here
before any POST route does. A distinguishable rejection tells a probing page that pam
is listening, and the empty 404 withholds that for free.

THE TOKEN APPEARS IN EXACTLY TWO PLACES - the printed URL and the browser's address
bar. Never in the session log, never in a response body, never in a response header.
`elide` is the one implementation of that promise and every path that could write a
request line or an error detail goes through it; `tests/test_webui_server.py` sweeps
all of them at once. The static page carries no token either: it fetches `api/ping`
RELATIVELY, so the browser supplies the prefix from the address bar.

THE CONNECTION MODEL: ONE CONNECTION PER REQUEST (M7 plan clarification 5). Handlers
run on worker threads, and a connection opened on the main thread is not legal on them
- `check_same_thread` is `True` and STAYS `True`. Overriding it to share one handle
would trade a structural protection for a discipline promise. So `connection()` opens
one through `catalog/db.py`'s factory inside the server's single `threading.Lock` and
closes it before the lock releases; the lock serializes all DB work. It does not
commit: one request is one transaction and the handler owns it, so a handler that
raises leaves nothing written.

AN UNBOUND SERVER (M14 Task 4, design 8.2 U10). `pam start` starts this server before the user
has chosen a folder: `workspace=None`, so it holds no workspace, no lock and no configuration.
While unbound it serves the static files and the routes in `UNBOUND_ROUTES` (ping, the folder
chooser, bind, the idle job); EVERY OTHER ROUTE that matches answers 409 `Choose a folder
first.`, decided in `_api` after the route has matched and BEFORE its body is parsed. Never the
empty 404: that stays the token gate's (a path that matches no route is still the empty 404, bound
or not). The configuration is loaded when a workspace is attached (`_attach`), which `pam review`
does at construction, exactly as before, and a later bind will do when the user chooses a folder.

JOBS (M14 Task 5, design 8.2 U11): `self.jobs` is this server's `webui/jobs.py` registry. Three
calls reach it, and the logic is all there: `_dispatch` heals a job that died before the request
does anything else (`settle`); `_api` refuses a write while a job runs before its handler is called
(`gate`, outside the DB lock); `connection()` refuses it again INSIDE the DB lock
(`check_in_lock`), where no job can start between the check and the write.

STATIC FILES ARE RESOLVED AGAINST AN ALLOWLIST, never by joining a request path onto a
directory. `../` is therefore unreachable by construction rather than by a normalizer
somebody has to keep correct - `/t/<token>/../secret` simply is not a name in
`STATIC_FILES`, and 404 is what a name that is not in it gets.
"""

import json
import logging
import secrets
import sqlite3
import threading
import traceback
import webbrowser
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

from pam.catalog import repo  # noqa: F401 - re-exported: a test patches `server.repo`
from pam.catalog.db import connect
from pam.config import Config, load_config
from pam.errors import PamError
from pam.paths import Workspace
from pam.webui import lookalikes
from pam.webui.api import (
    CONFLICT_STATUS,
    HANDLERS,
    ApiError,
    ApiRequest,
    RawResponse,
    error_body,
    parse_body,
)
from pam.webui.assets import (  # noqa: F401 - re-exported: every importer of server is unchanged
    BAR_SCRIPT,
    CHOOSER_SCRIPT,
    CONFIRM_SCRIPT,
    CORE_SCRIPT,
    DETAIL_SCRIPT,
    DONE_SCRIPT,
    FACES_INDEX,
    FACES_SCRIPT,
    HISTORY_SCRIPT,
    INDEX,
    KEYS_SCRIPT,
    LIST_SCRIPT,
    READING_SCRIPT,
    ROW_SCRIPT,
    SCRIPT,
    SELECTION_SCRIPT,
    STATIC_FILES,
    STOPPED_SCRIPT,
    STYLESHEET,
    TRUST_SCRIPT,
    WELCOME_SCRIPT,
)
from pam.webui.confirm_api import refused_before_parse
from pam.webui.faces_api import FACES_HANDLERS
from pam.webui.faces_manage_api import FACES_MANAGE_HANDLERS
from pam.webui.faces_split_api import FACES_SPLIT_HANDLERS, SplitMemory
from pam.webui.jobs import JOB_EXEMPT, JobRegistry
from pam.webui.nav import NAV_HANDLERS

# M14 Task 6a (R1-F7): moved to `routing.py` verbatim and imported back, so every importer of
# `server` (and `CHOOSE_A_FOLDER`'s, in `launcher.py`) is unchanged.
from pam.webui.routing import (  # noqa: F401 - CHOOSE_A_FOLDER re-exported for `launcher.py`
    CHOOSE_A_FOLDER,
    _api_ping,
    _choose_a_folder,
    _match_route,
)
from pam.webui.write_api import WRITE_HANDLERS

LOOPBACK_HOST = "127.0.0.1"

# 128 random bits, which spec 8.2 calls enough for a server that lives for one review
# session. Bytes, not characters: `token_hex(16)` is a 32-character string.
TOKEN_BYTES = 16

# What the session log writes where the token was. The token itself never reaches it.
TOKEN_PLACEHOLDER = "<token>"

JSON_TYPE = "application/json; charset=utf-8"

OK_STATUS = 200
NOT_FOUND_STATUS = 404
SERVER_ERROR_STATUS = 500

# M14 Task 4 review R1-F1 (the controller's ruling, pending the design model at the M14 close):
# a page still showing the folder pam has just left. Plan and item ids start at 1 in EVERY
# catalog, so without this a page loaded on folder A would approve and name items in folder B.
FOLDER_CHANGED = "pam is working on another folder now. Reload the page."
# The header a page sends its binding in: the number `bind` and `ping` gave it (`ReviewServer.binding`).
BINDING_HEADER = "X-Pam-Binding"

# The routes an UNBOUND server answers (Task 4 Step 2; U18's `[unbound ok]`): ping, the folder
# chooser, bind, and the job - idle until there is a folder. Every other route is the 409 above.
# `folders` and `bind` are registered by Task 4's later slices and `job` by Task 5; a key here
# with no handler behind it is a route that does not exist yet, which is the empty 404.
UNBOUND_ROUTES = frozenset(
    {("GET", "ping"), ("GET", "folders"), ("POST", "bind"), ("GET", "job")}
)

# What a response sends beyond its Content-Type, as `(name, value)` pairs. Almost always
# empty: M11 Task 10's crop endpoint is the first route that needs one (spec 8.2's
# `Cache-Control: no-store`), and it supplies it through `api.RawResponse.headers` rather
# than by the router learning anything about paths.
NO_HEADERS: tuple[tuple[str, str], ...] = ()

# A route's answer: HTTP status, Content-Type (None sends none), body bytes, extra headers.
Response = tuple[int, str | None, bytes, tuple[tuple[str, str], ...]]

# The rejection, spelled once: 404, no Content-Type, empty body (amendment A3).
NOT_FOUND: Response = (NOT_FOUND_STATUS, None, b"", NO_HEADERS)

# What every API handler is: the server it belongs to, plus this call's path parameters
# and query string. Named here rather than spelled inline three times.
ApiHandler = Callable[["ReviewServer", ApiRequest], Any]


class ReviewHandler(BaseHTTPRequestHandler):
    """One request, routed. Every failure inside it is caught and the server lives on."""

    protocol_version = "HTTP/1.1"
    # The defaults announce the interpreter's version in every `Server:` header. pam
    # has no use for that and a review session has no use for advertising it.
    server_version = "pam"
    sys_version = ""

    @property
    def review(self) -> "ReviewServer":
        """`self.server`, typed. `BaseHTTPRequestHandler` declares it as the base class."""
        return cast("ReviewServer", self.server)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's contract
        self._route("GET")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's contract
        # READ, THEN ROUTE - and read UNCONDITIONALLY, whatever the path turns out to
        # be. An HTTP/1.1 keep-alive connection reads an undrained body as the next
        # request line, so "read only when a route wants it" would turn a POST to an
        # unknown path into a corrupted next request rather than amendment A3's silent
        # 404. Task 7 drained and discarded here for exactly that reason; Task 9 keeps
        # the read and stops discarding it.
        self._route("POST", self._read_request_body())

    def log_message(self, format: str, *args: Any) -> None:
        """Silence. The base class writes every request to stderr.

        UX spec 2 keeps the console quiet, and this console is the one the user is
        watching for the Ctrl+C hint. The session log is where requests are recorded -
        see `ReviewServer.log_http_request`, which is called with the token elided.
        """

    def _read_request_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length > 0 else b""

    def _route(self, method: str, raw_body: bytes = b"") -> None:
        split = urlsplit(self.path)
        try:
            try:
                status, content_type, body, headers = self._dispatch(
                    method, split.path, split.query, raw_body
                )
            finally:
                # S1-F7: counted AFTER the handler, served or refused, so a read that took the
                # count before this write finished can never keep what it computed.
                if method == "POST":
                    self.review.count_write()
        except ApiError as refusal:
            # A refusal the browser is meant to read (400 or 404 with `{error}`). Elided
            # for the same reason a 500 is: the sentence is built here, but the promise
            # that no response body carries the token is held in ONE place.
            status, content_type = refusal.status, JSON_TYPE
            body = error_body(self.review.elide(refusal.message), refusal.extra)
            headers = NO_HEADERS
        except Exception as error:
            # A handler bug must not kill a half-reviewed session: 500, logged, and the
            # server keeps serving. `Exception`, never `BaseException` - a SystemExit or
            # a Ctrl+C is not a finding about this request.
            status, content_type, body, headers = self._as_server_error(error)
        self._respond(status, content_type, body, headers)
        self.review.log_http_request(method, split.path, status)

    def _dispatch(self, method: str, path: str, query: str, raw_body: bytes) -> Response:
        """The token gate, then the three route families. Everything else is 404."""
        parts = path.strip("/").split("/")
        if len(parts) < 2 or parts[0] != "t":
            return NOT_FOUND
        # Compared as BYTES: `compare_digest` refuses a str carrying non-ASCII, and a
        # request for `/t/token/` would then be a 500 instead of the 404 it must be.
        if not secrets.compare_digest(
            parts[1].encode("utf-8"), self.review.token.encode("utf-8")
        ):
            return NOT_FOUND
        # M14 Task 5 (U11): a job that died is healed before this request does anything else.
        self.review.jobs.settle()
        rest = parts[2:]
        if not rest:
            return self._static_file(INDEX)
        if rest[0] == "api":
            return self._api(method, rest[1:], query, raw_body)
        if len(rest) == 1 and rest[0] in STATIC_FILES:
            return self._static_file(rest[0])
        return NOT_FOUND

    def _api(self, method: str, segments: list[str], query: str, raw_body: bytes) -> Response:
        """Match the request against the route table, then run the one handler that fits.

        M7 Task 8 widened this from an exact-name lookup, and the reason is in spec 8.2's
        own API block: every real endpoint carries the plan id IN THE PATH and the items
        endpoint takes seven query parameters, neither of which a `(method, name)` key can
        express. The TABLE is still what routes - a task adds rows, not branches - and
        `{name}` is the only pattern syntax there is.

        THE BODY IS PARSED ONLY AFTER A ROUTE HAS MATCHED (Task 9). A malformed body on a
        path with no route must stay amendment A3's silent empty 404: a 400 saying "that
        is not JSON" would tell a probing page that pam is listening, which is the one
        thing the empty 404 exists to withhold.
        """
        for (route_method, template), handler in self.review.api_handlers.items():
            if route_method != method:
                continue
            params = _match_route(template, segments)
            if params is None:
                continue
            # U14 (M14 Task 6b): a move route's Content-Type / Origin / Host, before the body.
            if refused_before_parse(self.review, (route_method, template), self.headers):
                return NOT_FOUND
            # U10: a route that needs an archive, on a server with none, is refused with a
            # sentence - before the body is parsed, so a malformed body cannot turn it into a 400.
            if not self.review.bound and not self.review.serves_unbound(
                (route_method, template)
            ):
                raise _choose_a_folder()
            # R1-F1: a page on a folder pam has left is refused, also before the body is parsed.
            expected = None
            if (route_method, template) not in UNBOUND_ROUTES:
                expected = self._binding_sent(method)
            # U11: a write while a job runs is refused here, and again inside `connection()`.
            self.review.jobs.gate(method == "POST" and (method, template) not in JOB_EXEMPT)
            request = ApiRequest(
                params=params,
                query=parse_qs(query, keep_blank_values=True),
                body=parse_body(raw_body),
            )
            self.review.sent.binding = expected
            try:
                answer = handler(self.review, request)
            finally:
                self.review.sent.binding = None
            # Task 10's thumbnail route hands back bytes and the type to send them as;
            # every other handler hands back something `json.dumps` understands. The
            # branch is on what the HANDLER returned, not on the path, so the route
            # table stays the only thing that routes.
            if isinstance(answer, RawResponse):
                return OK_STATUS, answer.content_type, answer.body, answer.headers
            return OK_STATUS, JSON_TYPE, json.dumps(answer).encode("utf-8"), NO_HEADERS
        return NOT_FOUND

    def _binding_sent(self, method: str) -> int | None:
        """The binding this request says its page was loaded on, checked now (R1-F1).

        A page sends `BINDING_HEADER` on every request it makes with `fetch`; an `<img>` cannot,
        and a thumbnail is a read. So: a header that names another binding is refused; a WRITE
        with no header is refused on a server that can change folder (`rebindable`); a read with
        no header is served. The number returned is checked AGAIN inside `connection()`'s lock,
        so a change of folder between here and the handler's catalog read is refused too.
        """
        sent = self.headers.get(BINDING_HEADER)
        if sent is None:
            if method == "POST" and self.review.rebindable:
                raise ApiError(CONFLICT_STATUS, FOLDER_CHANGED)
            return None
        if sent != str(self.review.binding):
            raise ApiError(CONFLICT_STATUS, FOLDER_CHANGED)
        return self.review.binding

    def _static_file(self, name: str) -> Response:
        """Read from the package, by NAME chosen from `STATIC_FILES` and never joined.

        `importlib.resources` rather than a path relative to `__file__`, so an installed
        wheel serves the same bytes the source tree does - which is what
        `pyproject.toml`'s `webui/static/*` package-data entry is for.
        """
        data = (resources.files("pam.webui") / "static" / name).read_bytes()
        return OK_STATUS, STATIC_FILES[name], data, NO_HEADERS

    def _as_server_error(self, error: Exception) -> Response:
        """500 + `{error}`, with the token elided out of both the body and the log.

        The elision is not belt-and-braces. An exception raised inside a handler can
        carry anything the handler was holding, and the handler is holding the request
        path - so the one place a token could plausibly re-enter a response body is
        exactly here.

        THE CLASS NAME IS PRINTED FOR EXACTLY ONE HALF OF THE EXCEPTIONS, and the split
        is spec 13's. A `PamError` already carries a finished user-facing sentence - spec
        13 says the CLI "prints one clear line" and prints nothing else - so prefixing it
        yielded `ExifToolError: exiftool was not found...`, which leaks a class name into
        copy UX spec 8 says carries no jargon. An UNEXPECTED exception has no such
        sentence: `[Errno 13] Permission denied` alone does not say what kind of failure
        it is, and spec 13 answers that case with a traceback, so the type is the
        smallest honest equivalent in a JSON body. Both halves are diagnostics for the
        session log too, which gets the full traceback either way.
        """
        message = (
            str(error) if isinstance(error, PamError) else f"{type(error).__name__}: {error}"
        )
        detail = self.review.elide(message)
        self.review.logger.error(
            "review request failed: %s", self.review.elide(traceback.format_exc())
        )
        body = json.dumps({"error": detail}).encode("utf-8")
        return SERVER_ERROR_STATUS, JSON_TYPE, body, NO_HEADERS

    def _respond(
        self,
        status: int,
        content_type: str | None,
        body: bytes,
        headers: tuple[tuple[str, str], ...] = NO_HEADERS,
    ) -> None:
        self.send_response(status)
        if content_type is not None:
            self.send_header("Content-Type", content_type)
        # A handler's own headers, sent before the length. Only a `RawResponse` can carry
        # any, so this loop is empty for every JSON route and for every static file.
        for name, value in headers:
            self.send_header(name, value)
        # Always sent, empty body included: HTTP/1.1 keeps the connection open and a
        # response with no length is a response the browser waits forever for.
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ReviewServer(ThreadingHTTPServer):
    """A token-gated loopback server for one review session. This base renames nothing; since M14
    Task 6 the page renames through `launcher.StartServer`, which both `pam start` and `pam review`
    serve (ruling B1)."""

    daemon_threads = True
    # Whether this server can change folder (`pam start`'s can; R1-F1's write rule reads it).
    rebindable = False

    def __init__(
        self,
        *,
        workspace: Workspace | None,
        plan_id: int | None,
        logger: logging.Logger,
        token: str | None = None,
    ) -> None:
        # Port 0: the OS picks a free one. `127.0.0.1` EXACTLY - never `0.0.0.0`, never
        # a hostname, which would put the user's archive on the local network.
        super().__init__((LOOPBACK_HOST, 0), ReviewHandler)
        host, port = cast(tuple[str, int], self.server_address)
        self.host = host
        self.port = int(port)
        self.token = secrets.token_hex(TOKEN_BYTES) if token is None else token
        # The WORKSPACE, not a connection: see the module docstring. Everything a
        # handler needs to open its own connection, and nothing that outlives a request.
        # `None` is an UNBOUND server (M14 Task 4, U10): see `bound` and `_attach`.
        self._workspace: Workspace | None = None
        self._config: Config | None = None
        # R1-F1: which bind this is, counted by `_attach` and never reused. A page learns it from
        # `ping` or `bind` and sends it back (`BINDING_HEADER`); `sent` is that number, per thread.
        self.binding = 0
        self.sent = threading.local()
        # `None` IS A SERVED STATE, not a missing argument (spec 8.2 rev 22 / A18).
        # `pam review` always has a plan; `pam faces label` never does, and both drive this
        # one server. Every handler in `api.py`, `write_api.py` and `decide_api.py` takes
        # its plan id from the ROUTE and none of them reads this attribute, so the widening
        # reaches exactly two lines: `_api_ping` (in `routing.py`), and this one.
        self.plan_id: int | None = plan_id
        if workspace is not None:
            self._attach(workspace)
        self.logger = logger
        self.db_lock = threading.Lock()
        # M14 Task 5 (U11): this server's job. `pam review`'s never starts one, so for it every
        # check below is a no-op and nothing it does changed.
        self.jobs = JobRegistry(self)
        # (method, route template) -> handler, for routes under `/t/<token>/api/`. A
        # `{name}` segment is a path parameter; see `_match_route`. Task 9 registered its
        # four POST routes by adding ROWS - `WRITE_HANDLERS` - and not by editing the
        # router; the two tables are separate because `write_api` imports the request
        # primitives out of `api`, so `api` cannot import it back (see that table's
        # comment). Merging them here is the one line that knows about both.
        # M11 TASK 10 ADDS A THIRD TABLE, `faces_api.FACES_HANDLERS`, for the same
        # mechanical reason and by the same mechanism: `faces_api` imports the request
        # primitives out of `api`, so `api` cannot import it back. Still rows, still no
        # router branch - `_api` and `_match_route` were not touched for it either.
        self.api_handlers: dict[tuple[str, str], ApiHandler] = {
            ("GET", "ping"): _api_ping,
            **HANDLERS,
            # M14 Task 2: the nav tree's row moved to `webui/nav.py` (a pure move).
            **NAV_HANDLERS,
            **WRITE_HANDLERS,
            **FACES_HANDLERS,
            **FACES_MANAGE_HANDLERS,
            **FACES_SPLIT_HANDLERS,
        }
        # Split's previews and its one undo, in SERVER MEMORY (maintainer's ruling, 2026-09-12):
        # lost when the server stops, and touched only inside `connection()`'s lock.
        self.split_memory = SplitMemory()
        # S1-F7: every POST this server finished (`count_write`), and `nav`'s footer memo.
        self.writes = 0
        self.renames_memo: tuple[object, int] | None = None

    def count_write(self) -> None:
        """One more POST finished: what the catalog says may have changed (S1-F7)."""
        with self.db_lock:
            self.writes += 1

    def _attach(self, workspace: Workspace) -> None:
        """Bind this server to an archive's workspace, and read its configuration NOW.

        The configuration is read once per BIND rather than once per request (M14 Task 4: at
        bind, not at construction - `pam review` binds at construction, so for it nothing
        moved). `faces_api` needs `[tiers] faces` floor on every read it serves (A11 applies
        the floor at READ time), and a TOML parse per crop request would be a parse per face
        on a contact sheet. A workspace with no `pam.toml` yields the defaults, so this cannot
        fail for an archive that has only ever been scanned.
        """
        self._config = load_config(workspace.config_path)
        self._workspace = workspace
        self.binding += 1

    def _detach(self) -> None:
        """Back to UNBOUND: no workspace, no configuration, no plan (U10's change of folder).

        The lock is the binder's (`webui/launcher.py`), released by it BEFORE this is called."""
        self._workspace = None
        self._config = None
        self.plan_id = None
        # R1-F2: what the server remembers about the folder it leaves goes with it.
        lookalikes.forget(self)
        self.split_memory = SplitMemory()

    def serves_unbound(self, route: tuple[str, str]) -> bool:
        """Whether `(method, template)` is answered while unbound (`UNBOUND_ROUTES`)."""
        return route in UNBOUND_ROUTES

    @property
    def bound(self) -> bool:
        """Whether a folder has been chosen. `pam review`'s server always is."""
        return self._workspace is not None

    @property
    def workspace(self) -> Workspace:
        """The bound archive's workspace. Unbound, U10's 409 - never an AttributeError or a
        catalog path built from nothing; `_api` refuses first, so this is the second line."""
        if self._workspace is None:
            raise _choose_a_folder()
        return self._workspace

    @property
    def config(self) -> Config:
        """The bound archive's configuration, read at bind. Unbound, U10's 409."""
        if self._config is None:
            raise _choose_a_folder()
        return self._config

    @property
    def url(self) -> str:
        """The one address the user is given, and the only place the token is printed."""
        return f"http://{self.host}:{self.port}/t/{self.token}/"

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """One request, one connection, opened and closed inside the single DB lock.

        No commit here on purpose: one request is one transaction (clarification 5) and
        the handler owns it, so a handler that raises after writing leaves nothing
        behind - sqlite3 rolls back what was never committed when the handle closes.
        """
        with self.db_lock:
            expected = getattr(self.sent, "binding", None)
            if expected is not None and expected != self.binding:
                raise ApiError(CONFLICT_STATUS, FOLDER_CHANGED)
            # U11, INSIDE the lock: no job can start between this check and the write.
            self.jobs.check_in_lock()
            conn = connect(self.workspace.db_path)
            try:
                yield conn
            finally:
                conn.close()

    def elide(self, text: str) -> str:
        """THE single implementation of spec 8.2's "never in the session log" promise."""
        return text.replace(self.token, TOKEN_PLACEHOLDER)

    def log_http_request(self, method: str, path: str, status: int) -> None:
        """One session-log line per request, with the token elided out of the path."""
        self.logger.info("review %s %s -> %s", method, self.elide(path), status)


def open_in_browser(url: str, opener: Callable[[str], bool] | None = None) -> bool:
    """Try to open the user's browser. THE PRINTED URL IS AUTHORITATIVE; this is a
    convenience, so every way it can fail is a false return rather than an exception.

    `webbrowser.open` reports failure two ways - `False`, and an exception out of
    whichever launcher it picked - and on a headless or unusually configured machine
    both are ordinary. Neither may end a review session that is otherwise working, so
    both arrive here as `False` and the caller prints the address again.

    `Exception`, not `BaseException`: a Ctrl+C typed while the browser is launching is
    the user stopping pam, not a browser that would not open.
    """
    launch = webbrowser.open if opener is None else opener
    try:
        return bool(launch(url))
    except Exception:
        return False


def serve_until_interrupted(server: ReviewServer) -> None:
    """Serve until Ctrl+C or a programmatic `shutdown()`. Both are DELIBERATE ends.

    The KeyboardInterrupt is absorbed here rather than unwound, which is what makes
    clarification 4's exit-code split work: stopping the server is the designed way out
    of `pam review`, so it takes the closing-summary path and exits 0. An unexpected
    exception is NOT caught here - it propagates to `cli.py`'s spec 13 handler, which
    prints a traceback and exits 2, so a crash can never masquerade as a finished
    review session.
    """
    with suppress(KeyboardInterrupt):
        server.serve_forever()
