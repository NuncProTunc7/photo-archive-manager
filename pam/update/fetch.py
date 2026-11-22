"""`pam update`'s release client: the ONE module allowed a non-loopback host (E3, E10, OD-2).

ADR 0003's addendum (E10): pam has one LOOPBACK client, `pam/net.py`, and one RELEASE client, this
module, each guarded, and no third. Only `pam update` reaches it (`pam.cli`'s `update` body is the
one importer of `pam.update`; `tests/test_update_isolation.py` guards that and the canary proves no
archive command loads it).

THE RULES, each one a separate refusal (spike 10B, adopted 2026-11-19):
  - HTTPS on port 443 to EXACTLY `api.github.com`, `github.com`, `release-assets.githubusercontent.com`,
    the host compared after IDNA and case-folding, BEFORE ANYTHING RESOLVES IT - the initial URL
    and every redirect alike. An `http://` redirect is refused, never downgraded.
  - Redirects: a request may follow at most ONE, and only to the host its caller names
    (`redirect_to`): none for discovery, `github.com` -> `release-assets` for a download. That hop
    must answer 200.
  - Certificate verification is `ssl.create_default_context()`'s, always on, never retried
    without it. Tests hand in a CA file; production hands in nothing (no certifi, 10B F5).
  - The body is streamed under a byte cap (refused above it, before reading when Content-Length
    says so), a short body is refused as truncated, and chunked encoding is read.
  - Every socket operation has a timeout, AND the whole chain - name lookup, connect, TLS,
    request, headers, body, the redirect's second request - shares ONE overall deadline. Each
    receive is armed with what is left of it, so a server that trickles a byte just inside every
    socket timeout still meets the deadline. `getaddrinfo` cannot be bounded in its thread, so the
    lookup runs in a daemon thread joined against the deadline (the M12 `_bounded` precedent).
  - It sends `User-Agent: pam/<version>` and `Accept`, and nothing else beyond `Host`: no
    cookie, no token, no `Accept-Encoding`.
  - It CONNECTS TO THE ADDRESS IT RESOLVED (net.py's rule): the name is looked up once, by the
    resolver, and the TLS handshake names the host so the certificate is checked against it.

Tests inject the `resolver` (name -> loopback addresses) and the CA file; production passes neither.
"""

import http.client
import io
import ipaddress
import socket
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from pam import __version__
from pam.errors import PamError

ALLOWED_HOSTS = ("api.github.com", "github.com", "release-assets.githubusercontent.com")
HTTPS_PORT = 443
USER_AGENT = f"pam/{__version__}"
METADATA_DEADLINE_S = 30.0
SOCKET_TIMEOUT_S = 10.0
# A download's deadline is the metadata deadline plus its size at this floor of a rate.
MIN_RATE_BYTES_PER_S = 64 * 1024
CHUNK_BYTES = 64 * 1024
_REDIRECTS = frozenset({301, 302, 303, 307, 308})

# name, port -> [(address, port), ...]: getaddrinfo's sockaddrs, address first.
Resolver = Callable[[str, int], list[tuple[str, int]]]
Clock = Callable[[], float]


class FetchError(PamError):
    """A request `pam update` made did not complete. The sentence names why."""


class DeadlineError(FetchError):
    """The overall deadline ran out."""


class CertificateError(FetchError):
    """The server's certificate did not verify."""


class StatusError(FetchError):
    def __init__(self, host: str, status: int) -> None:
        super().__init__(f"{host} answered HTTP {status}.")
        self.status = status


class RateLimitedError(FetchError):
    def __init__(self, reset: int | None) -> None:
        super().__init__("GitHub is limiting how often this PC may ask about releases.")
        self.reset = reset


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]  # names lower-cased
    body: bytes  # empty when a sink took the bytes
    size: int


def download_deadline_s(size: int) -> float:
    return METADATA_DEADLINE_S + size / MIN_RATE_BYTES_PER_S


def system_resolver(host: str, port: int) -> list[tuple[str, int]]:
    records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    return [(str(record[4][0]), int(record[4][1])) for record in records]


def checked_url(url: str) -> tuple[str, str]:
    """`(host, path)` when `url` is HTTPS on 443 to an allowed host; else `FetchError`.
    Decided from the TEXT alone, so a refused host never reaches a resolver."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise FetchError(f"pam refused to fetch {url}: it is not HTTPS.")
    try:
        port = parts.port
        host = (parts.hostname or "").encode("idna").decode("ascii").lower()
    except (ValueError, UnicodeError):
        raise FetchError(
            f"pam refused to fetch {url}: its host or port is not valid."
        ) from None
    if (
        parts.username is not None
        or port not in (None, HTTPS_PORT)
        or host not in ALLOWED_HOSTS
    ):
        raise FetchError(f"pam refused to fetch {url}: pam update talks only to GitHub.")
    path = parts.path or "/"
    return host, f"{path}?{parts.query}" if parts.query else path


class _Budget:
    """The overall deadline, shared by every step of one chain."""

    def __init__(self, deadline: float, clock: Clock, per_op_s: float) -> None:
        self.deadline, self.clock, self.per_op_s = deadline, clock, per_op_s

    def remaining(self) -> float:
        left = self.deadline - self.clock()
        if left <= 0:
            raise DeadlineError(_DEADLINE)
        return left

    def arm(self) -> float:
        """The timeout for the next socket operation: the per-socket one or what is left."""
        return min(self.per_op_s, self.remaining())

    def timed_out(self, armed: float) -> FetchError:
        if armed < self.per_op_s:
            return DeadlineError(_DEADLINE)
        return FetchError(f"GitHub stopped answering for {self.per_op_s:.0f} seconds.")


_DEADLINE = "pam stopped waiting for GitHub: the request took longer than pam allows."


class _DeadlineSocket:
    """What `http.client` uses of a socket - `sendall`, `makefile`, `close` - under the budget.

    `http.client` closes the CONNECTION as soon as a `Connection: close` response's head is read
    and keeps reading the body through the file `makefile` gave it, so - as `socket.makefile`'s own
    reference count does - the TLS socket closes only once the connection AND every file are closed.
    """

    def __init__(self, sock: ssl.SSLSocket, budget: _Budget) -> None:
        self._sock, self._budget = sock, budget
        self._files = 0
        self._closed = False

    def sendall(self, data: bytes) -> None:
        armed = self._budget.arm()
        self._sock.settimeout(armed)
        try:
            self._sock.sendall(data)
        except TimeoutError:
            raise self._budget.timed_out(armed) from None

    def recv_into(self, buffer: memoryview) -> int:
        """Every receive armed with what is left of the deadline (a trickle cannot outlast it)."""
        armed = self._budget.arm()
        self._sock.settimeout(armed)
        try:
            return self._sock.recv_into(buffer)
        except TimeoutError:
            raise self._budget.timed_out(armed) from None

    def makefile(self, *_args: object, **_kwargs: object) -> io.BufferedReader:
        self._files += 1
        return io.BufferedReader(_Reader(self))

    def release(self) -> None:
        self._files -= 1
        self._close_when_unused()

    def close(self) -> None:
        self._closed = True
        self._close_when_unused()

    def _close_when_unused(self) -> None:
        if self._closed and self._files <= 0:
            self._sock.close()


class _Reader(io.RawIOBase):
    def __init__(self, owner: _DeadlineSocket) -> None:
        self._owner = owner

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: memoryview) -> int:  # type: ignore[override]
        return self._owner.recv_into(buffer)

    def close(self) -> None:
        if not self.closed:
            self._owner.release()
        super().close()


class Transport:
    """GET over verified HTTPS to E3's hosts. `get` is the only entry."""

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        cafile: str | None = None,
        clock: Clock = time.monotonic,
        socket_timeout_s: float = SOCKET_TIMEOUT_S,
    ) -> None:
        self._resolver = resolver or system_resolver
        self._context = ssl.create_default_context(cafile=cafile)
        self._clock = clock
        self._per_op_s = socket_timeout_s

    def get(
        self,
        url: str,
        *,
        accept: str,
        max_bytes: int,
        deadline_s: float,
        redirect_to: str | None = None,
        sink: Callable[[bytes], None] | None = None,
    ) -> Response:
        """The 200 response for `url`, after at most one redirect to `redirect_to`."""
        budget = _Budget(self._clock() + deadline_s, self._clock, self._per_op_s)
        host, path = checked_url(url)
        for hop in range(2):
            location = self._request(host, path, accept, max_bytes, budget, sink)
            if isinstance(location, Response):
                return location
            if hop or redirect_to is None:
                raise FetchError(f"{host} redirected pam elsewhere, which pam does not follow.")
            host, path = checked_url(location)
            if host != redirect_to:
                raise FetchError(
                    f"pam refused a redirect to {host}: only {redirect_to} is expected."
                )
        raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover

    def _request(
        self,
        host: str,
        path: str,
        accept: str,
        max_bytes: int,
        budget: _Budget,
        sink: Callable[[bytes], None] | None,
    ) -> Response | str:
        """One request: a `Response` for 200, the `Location` text for a redirect."""
        sock = self._connect(host, self._resolve(host, budget), budget)
        connection = http.client.HTTPSConnection(host, HTTPS_PORT, context=self._context)
        connection.sock = sock
        response: http.client.HTTPResponse | None = None
        try:
            connection.putrequest("GET", path, skip_accept_encoding=True)
            connection.putheader("User-Agent", USER_AGENT)
            connection.putheader("Accept", accept)
            connection.endheaders()
            response = connection.getresponse()
            headers = {name.lower(): value for name, value in response.getheaders()}
            if response.status in _REDIRECTS:
                return headers.get("location", "")
            if response.status in (403, 429) and headers.get("x-ratelimit-remaining") == "0":
                reset = headers.get("x-ratelimit-reset", "")
                raise RateLimitedError(
                    int(reset) if reset.isascii() and reset.isdigit() else None
                )
            if response.status != 200:
                raise StatusError(host, response.status)
            return _read(response, headers, host, max_bytes, sink)
        except (http.client.HTTPException, OSError) as error:
            raise FetchError(f"pam lost the connection to {host} ({error}).") from None
        finally:
            if response is not None:
                response.close()
            connection.close()

    def _resolve(self, host: str, budget: _Budget) -> list[tuple[str, int]]:
        """The resolver's answer, in a daemon thread joined against the deadline."""
        outcome: dict[str, object] = {}

        def work() -> None:
            try:
                outcome["addresses"] = self._resolver(host, HTTPS_PORT)
            except Exception as error:  # reported on the caller's thread
                outcome["error"] = error

        thread = threading.Thread(target=work, name="pam-update-lookup", daemon=True)
        thread.start()
        thread.join(budget.remaining())
        if thread.is_alive():
            raise DeadlineError(_DEADLINE)
        addresses = outcome.get("addresses")
        if not isinstance(addresses, list) or not addresses:
            raise FetchError(
                f"pam could not find {host} ({outcome.get('error', 'no address')})."
                " Check the internet connection, then try again."
            )
        return addresses

    def _connect(
        self, host: str, addresses: list[tuple[str, int]], budget: _Budget
    ) -> _DeadlineSocket:
        """TCP to each resolved address in turn, then TLS naming `host`, verified."""
        failure: OSError | None = None
        for address, port in addresses:
            try:
                version = ipaddress.ip_address(address).version
            except ValueError:
                raise FetchError(f"{host} resolved to {address!r}, not an address.") from None
            # `wrap_socket` detaches the plain socket, so `live` names whichever one is open.
            live = [socket.socket(socket.AF_INET6 if version == 6 else socket.AF_INET)]
            armed = 0.0
            try:
                armed = budget.arm()
                live[0].settimeout(armed)
                live[0].connect((address, port))
                tls = self._context.wrap_socket(
                    live[0], server_hostname=host, do_handshake_on_connect=False
                )
                live[0] = tls
                armed = budget.arm()
                tls.settimeout(armed)
                tls.do_handshake()
                return _DeadlineSocket(tls, budget)
            except ssl.SSLCertVerificationError as error:
                live[0].close()
                raise CertificateError(
                    f"pam could not verify {host}'s certificate ({error.verify_message}), so it"
                    " stopped. Windows may be missing a root certificate; pam never continues"
                    " without one."
                ) from None
            except TimeoutError:
                live[0].close()
                raise budget.timed_out(armed) from None
            except OSError as error:
                live[0].close()
                failure = error
            except BaseException:
                live[0].close()
                raise
        raise FetchError(
            f"pam could not connect to {host} ({failure}). Check the internet connection,"
            " then try again."
        )


def _read(
    response: http.client.HTTPResponse,
    headers: dict[str, str],
    host: str,
    max_bytes: int,
    sink: Callable[[bytes], None] | None,
) -> Response:
    """The body, streamed under `max_bytes`, refused when short of its Content-Length."""
    length: int | None = None
    if not response.chunked and "content-length" in headers:
        text = headers["content-length"]
        if not (text.isascii() and text.isdigit()):
            raise FetchError(f"{host} sent a Content-Length pam cannot read.")
        length = int(text)
        if length > max_bytes:
            raise FetchError(
                f"{host} offered {length} bytes, more than the {max_bytes} expected."
            )
    parts: list[bytes] = []
    size = 0
    while chunk := response.read(CHUNK_BYTES):
        size += len(chunk)
        if size > max_bytes:
            raise FetchError(f"{host} sent more than the {max_bytes} bytes expected.")
        if sink is None:
            parts.append(chunk)
        else:
            sink(chunk)
    if length is not None and size != length:
        raise FetchError(f"{host} sent {size} of {length} bytes: the answer was cut short.")
    return Response(response.status, headers, b"".join(parts), size)
