"""A stdlib HTTP server that answers like Ollama, from a script the test controls.

WHY THIS EXISTS AND WHY IT IS NOT OPTIONAL (M10 plan, clarification 7). The real Ollama
is installed on the developer's machine, and it is the one thing no test in this suite may
talk to. A test that reached it would pass here and mean nothing on any other machine;
worse, a test that SKIPPED when Ollama is absent would disarm itself silently on exactly
the machine where the check is worth having - which is `tests/test_static_syntax.py`'s
lesson, promoted into CLAUDE.md. So every caption test in M10 drives this stub, no test
skips for a missing Ollama, and `pam models pull` is never pointed at the real server.

IT LIVES IN `tests/`, WHICH THE NETWORK GUARD DOES NOT WALK. `tests/guards.py`'s
`ALLOWED_NETWORK_IMPORTS` is an allowlist over `pam/` - the shipped package - and it
stays at four entries through M10 because this file is not shipped. Binding a loopback
listener here is the same thing `tests/test_webui_server.py` and the canary's own
proof-of-sight test already do.

NO SLEEPS, ANYWHERE, AND THE READINESS RACE DOES NOT EXIST. `HTTPServer.__init__` binds
and listens before `__init__` returns, so the port is accepting connections before the
serving thread is even started - there is nothing to wait for and nothing to poll. The
one place a test could still race is reading back what the stub received, and
`_Handler` appends the request to `server.requests` BEFORE it writes the response, so a
client that has its response is guaranteed to see the record. That ordering is the exact
opposite of `webui/server.py::_route`'s (respond, then log), which is the latent hosted
failure CLAUDE.md records - hosted CI is ~3x slower than this machine and it is the
slower runner that loses such a race.

WHAT TASK 8 REUSES. `Reply` and `OllamaStub` are the whole interface: build a stub with a
script mapping a path to the replies it should give, drive pam at `stub.port`, then read
`stub.requests` to assert what pam sent. `/api/generate` is scripted here even though
Task 7 has no caption enricher, because Task 8's canonical four-layer fixture is a
`Reply` and nothing else about the harness needs to change to carry it.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# The three routes pam ever asks Ollama for. Written out so that a test scripting a path
# pam does not use fails loudly here rather than being served a quiet 404 that reads like
# a pam bug.
OLLAMA_ROUTES = ("/api/tags", "/api/pull", "/api/generate")


@dataclass
class Reply:
    """One scripted answer: a status, and a body given as bytes, text or JSON-able data.

    `body` is deliberately permissive because the negative cases matter as much as the
    happy one - Task 8 builds "a non-JSON answer" by handing this a `str`, and "JSON
    missing a field" by handing it a `dict`. A `dict` or `list` is serialised; a `str` is
    encoded as written and is NOT serialised, so a test can send text that is not JSON at
    all.
    """

    body: object = b""
    status: int = 200
    # Set to make the handler wait before answering, so a test can drive a client
    # timeout. It waits on an EVENT with its own deadline rather than sleeping a fixed
    # span: the test sets the event to release the handler, and the deadline means a
    # forgotten `set()` fails the test's own timeout instead of hanging the suite.
    block_until: threading.Event | None = None
    block_deadline_s: float = 30.0

    def encoded(self) -> bytes:
        if isinstance(self.body, bytes):
            return self.body
        if isinstance(self.body, str):
            return self.body.encode("utf-8")
        return json.dumps(self.body).encode("utf-8")


@dataclass(frozen=True)
class Received:
    """One request the stub was sent, recorded before it was answered."""

    method: str
    path: str
    body: bytes

    def json(self) -> object:
        return json.loads(self.body)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming
        self._serve(b"")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self._serve(self.rfile.read(length))

    def _serve(self, body: bytes) -> None:
        stub: OllamaStub = self.server.stub  # type: ignore[attr-defined]
        # Recorded FIRST. See the module docstring: the client seeing its response is
        # then proof that this append has happened, which is what lets a test read
        # `stub.requests` with no barrier and no sleep.
        stub.requests.append(Received(self.command, self.path, body))
        reply = stub.next_reply(self.path)
        if reply.block_until is not None:
            reply.block_until.wait(reply.block_deadline_s)
        payload = reply.encoded()
        self.send_response(reply.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        """Silence. The default writes every request to stderr, which would bury the
        output of any test that fails while a stub is running."""


class OllamaStub:
    """A loopback HTTP server answering pam's three Ollama routes from a script.

    Used as a context manager. `script` maps a path to the replies it should give, in
    order; the last reply is repeated if the client asks again, so the common case of
    "always answer this" is a one-element list.
    """

    def __init__(self, script: dict[str, list[Reply]] | None = None, host: str = "127.0.0.1"):
        for path in script or {}:
            if path not in OLLAMA_ROUTES:
                raise ValueError(
                    f"{path!r} is not a route pam asks Ollama for: {OLLAMA_ROUTES}"
                )
        self.script: dict[str, list[Reply]] = dict(script or {})
        self.requests: list[Received] = []
        self._served: dict[str, int] = {}
        self._server = ThreadingHTTPServer((host, 0), _Handler)
        self._server.stub = self  # type: ignore[attr-defined]
        self._server.daemon_threads = True
        self.host, self.port = self._server.server_address[0], self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def next_reply(self, path: str) -> Reply:
        replies = self.script.get(path)
        if not replies:
            return Reply(status=404, body={"error": f"stub has no script for {path}"})
        index = min(self._served.get(path, 0), len(replies) - 1)
        self._served[path] = index + 1
        return replies[index]

    def paths(self) -> list[str]:
        return [received.path for received in self.requests]

    def __enter__(self) -> OllamaStub:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=10)
        assert not self._thread.is_alive(), "the stub server thread did not stop"


def tags_reply(*names: str) -> Reply:
    """What `GET /api/tags` looks like, in Ollama's own shape.

    The `:latest` spelling is the caller's to choose, deliberately: finding O2 is about
    the difference between `qwen2.5vl` and `qwen2.5vl:latest` in this very listing, and a
    helper that normalised it would hide the thing the test is about.
    """
    return Reply(body={"models": [{"name": name, "model": name} for name in names]})


def scripted(*replies: Reply, tags: str = "qwen2.5vl:latest") -> OllamaStub:
    """A stub answering `/api/generate` from a script and `/api/tags` with one model.

    The shape almost every caption test wants, so that three modules do not each keep
    their own copy of it (M10 Task 11). A test needing a different `/api/tags` answer -
    nothing listening, a wrong tag, something that is not Ollama - builds `OllamaStub`
    directly, because that is the thing it is about.
    """
    return OllamaStub({"/api/generate": list(replies), "/api/tags": [tags_reply(tags)]})


def generate_reply(label: str, sentence: str, done_reason: str = "stop") -> Reply:
    """The plan's canonical four-layer fixture, confirmed against real Ollama 0.33.2.

    Layer 1 is this HTTP body; layer 2 is its `response` field, which is A JSON STRING
    because the request asks for `format=json`; layer 3 is that string parsed into
    `{label, sentence}`; layer 4 is spec 6.4's validation. Task 8 builds each negative
    case by editing ONE layer of this, so a failing test always says which layer it is
    about.
    """
    return Reply(
        body={
            "model": "qwen2.5vl",
            "created_at": "2026-09-01T12:00:00Z",
            "response": json.dumps({"label": label, "sentence": sentence}),
            "done": True,
            "done_reason": done_reason,
        }
    )
