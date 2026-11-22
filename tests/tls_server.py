"""A scripted HTTPS server on 127.0.0.1 for `pam update`'s real-transport tests (M15 Task 12).

Not a test module. It serves the TEST-ONLY certificates in `tests/fixtures/tls/` (SANs: E3's three
hosts) and answers by the request's `Host` and path, from a table each test writes. `pam/update/
fetch.py` reaches it through an injected resolver, so the client does a real lookup step, a real TCP
connect, a real TLS handshake with verification on, and real HTTP - and no byte reaches GitHub.

Each answer is a function `(conn, request)` that writes RAW bytes, so a test can send exactly what a
real server could: a short body, chunked encoding, a stall, a one-byte-at-a-time trickle. Every
request is recorded (`requests`), and so is every lookup the resolver is asked for (`lookups`).

`stop()` (or the context manager) ends every trickle and stall, closes the listener and joins the
threads. Nothing here sleeps to wait for the CLIENT: a stall waits on `stopping`, which `stop()` sets.
"""

import socket
import ssl
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

TLS_DIR = Path(__file__).resolve().parent / "fixtures" / "tls"
CA_FILE = str(TLS_DIR / "ca.pem")
TRUSTED = (TLS_DIR / "server.pem", TLS_DIR / "server.TEST-ONLY.key")
UNTRUSTED = (TLS_DIR / "untrusted-server.pem", TLS_DIR / "untrusted-server.TEST-ONLY.key")
WRONG_HOST_CA_FILE = str(TLS_DIR / "wrong-host-ca.pem")
WRONG_HOST = (TLS_DIR / "wrong-host-server.pem", TLS_DIR / "wrong-host-server.TEST-ONLY.key")
TRICKLE_S = 0.9  # under every socket timeout the tests set


@dataclass(frozen=True)
class Request:
    host: str
    path: str
    header_names: tuple[str, ...]  # lower-cased, in the order sent
    headers: dict[str, str]


Answer = Callable[[ssl.SSLSocket, Request], None]


class TlsServer:
    def __init__(self, certificate: tuple[Path, Path] = TRUSTED, *, handshake: bool = True):
        self.routes: dict[tuple[str, str], Answer] = {}
        self.requests: list[Request] = []
        self.lookups: list[str] = []
        self.stopping = threading.Event()
        self._handshake = handshake
        self._context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._context.load_cert_chain(str(certificate[0]), str(certificate[1]))
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        self._threads: list[threading.Thread] = []
        self._accepting = threading.Thread(target=self._accept, daemon=True)
        self._accepting.start()

    # -- the client's side ----------------------------------------------------------------------
    def resolver(self, host: str, port: int) -> list[tuple[str, int]]:
        """Every name -> this server. Records the name: a refused host must never appear here."""
        self.lookups.append(host)
        return [("127.0.0.1", self.port)]

    def route(self, host: str, path: str, answer: Answer) -> None:
        self.routes[(host, path)] = answer

    # -- lifecycle --------------------------------------------------------------------------------
    def __enter__(self) -> "TlsServer":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    def stop(self) -> None:
        self.stopping.set()
        self._listener.close()
        for thread in [self._accepting, *self._threads]:
            thread.join(timeout=10)

    def _accept(self) -> None:
        while not self.stopping.is_set():
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return
            thread = threading.Thread(target=self._serve, args=(conn,), daemon=True)
            self._threads.append(thread)
            thread.start()

    def _serve(self, raw: socket.socket) -> None:
        try:
            if not self._handshake:
                self.stopping.wait(30)  # a TCP peer that never starts TLS
                return
            raw.settimeout(30)
            try:
                conn = self._context.wrap_socket(raw, server_side=True)
            except (OSError, ssl.SSLError):
                return  # the client refused our certificate, as it should
            request = _read_request(conn)
            if request is None:
                return
            self.requests.append(request)
            answer = self.routes.get((request.host, request.path), not_found)
            try:
                answer(conn, request)
            except OSError:
                pass  # the client gave up first
            conn.close()
        finally:
            raw.close()


def _read_request(conn: ssl.SSLSocket) -> Request | None:
    data = b""
    while b"\r\n\r\n" not in data:
        try:
            chunk = conn.recv(4096)
        except OSError:
            return None
        if not chunk:
            return None
        data += chunk
    head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n")
    path = head[0].split(" ")[1]
    names, headers = [], {}
    for line in head[1:]:
        name, _, value = line.partition(":")
        names.append(name.strip().lower())
        headers[name.strip().lower()] = value.strip()
    return Request(headers.get("host", ""), path, tuple(names), headers)


# --- answers ----------------------------------------------------------------------------------


def head(status: int, headers: dict[str, str] | None = None) -> bytes:
    lines = [
        f"HTTP/1.1 {status} X",
        "Connection: close",
        *(f"{k}: {v}" for k, v in (headers or {}).items()),
    ]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


def ok(body: bytes, headers: dict[str, str] | None = None) -> Answer:
    def answer(conn: ssl.SSLSocket, _request: Request) -> None:
        conn.sendall(head(200, {"Content-Length": str(len(body)), **(headers or {})}) + body)

    return answer


def status(code: int, headers: dict[str, str] | None = None, body: bytes = b"{}") -> Answer:
    def answer(conn: ssl.SSLSocket, _request: Request) -> None:
        conn.sendall(head(code, {"Content-Length": str(len(body)), **(headers or {})}) + body)

    return answer


not_found = status(404)


def redirect(location: str) -> Answer:
    def answer(conn: ssl.SSLSocket, _request: Request) -> None:
        conn.sendall(head(302, {"Location": location, "Content-Length": "0"}))

    return answer


def chunked(body: bytes, pieces: int = 3) -> Answer:
    def answer(conn: ssl.SSLSocket, _request: Request) -> None:
        out = head(200, {"Transfer-Encoding": "chunked"})
        step = max(1, len(body) // pieces)
        for start in range(0, len(body), step):
            part = body[start : start + step]
            out += f"{len(part):x}\r\n".encode() + part + b"\r\n"
        conn.sendall(out + b"0\r\n\r\n")

    return answer


def truncated(body: bytes, claimed: int) -> Answer:
    """Content-Length says `claimed`; `body` (shorter) is sent, then the connection closes."""

    def answer(conn: ssl.SSLSocket, _request: Request) -> None:
        conn.sendall(head(200, {"Content-Length": str(claimed)}) + body)

    return answer


def stall(server: TlsServer) -> Answer:
    """Read the request, answer nothing until the server stops."""

    def answer(_conn: ssl.SSLSocket, _request: Request) -> None:
        server.stopping.wait(60)

    return answer


def trickle(server: TlsServer, *, headers_too: bool) -> Answer:
    """One byte every TRICKLE_S, forever (until stopped): the status line and headers too, or a
    complete head and then an endless body under a huge Content-Length."""

    def answer(conn: ssl.SSLSocket, _request: Request) -> None:
        if headers_too:
            data = head(200, {"Content-Length": "1000000"}) + b"x" * 1000000
        else:
            conn.sendall(head(200, {"Content-Length": "1000000"}))
            data = b"x" * 1000000
        for byte in data:
            if server.stopping.wait(TRICKLE_S):
                return
            conn.sendall(bytes([byte]))

    return answer
