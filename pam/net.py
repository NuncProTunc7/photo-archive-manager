"""The only LOOPBACK HTTP code in pam (spec 10); `pam/update/fetch.py` is the release client.

WHAT THIS MODULE IS FOR. The product vision's first principle is that nothing leaves
the machine. That is a promise until something makes it mechanical, and three things
together do: this module (the one loopback client, which refuses anything off-box; the release client
`pam/update/fetch.py` is the other, E10, and reaches three fixed hosts), the
grep-gate in `tests/guards.py` (no `requests`/`httpx`/`urllib.request` import anywhere
under `pam/`), and the canary in `tests/test_net_canary.py` (the whole pipeline runs
with `socket.socket.connect` under a wrapper, and the wrapper records nothing).

IT SHIPPED WITHOUT A CONSUMER, DELIBERATELY, AND M10 GAVE IT TWO. It landed in M6
because the grep-gate and the canary need there to be exactly one piece of HTTP code to
point at; adding the client alongside its first caller would mean the privacy guarantee
arrived after the code it is supposed to constrain. M10 Task 7 made `doctor.py` the
first caller (spec 12 rev 17's A9/A10 - the Ollama reachability check and
`pam models pull`) and Task 8 makes `enrich/caption.py` the second. Spec 10's sentence
naming `enrich/caption.py` as this module's ONLY consumer is therefore out of date and
owes an amendment; it is disclosed in `docs/implementation-notes/m10-progress.md` and
is NOT written here, because a module docstring is not where a normative sentence is
changed. What has not moved is the thing the sentence was protecting: this is still the
only LOOPBACK HTTP code in pam (M15 Task 12 added the update client, E10), and every caller reaches
the network through THIS module or not at all.

RESOLVE FIRST, THEN CHECK. Refusing `evil.example` by inspecting the *string* is not a
control at all: the attack is a name that looks local and resolves off-box, and
`localhost.evil.example` is a name somebody else's DNS server answers however it likes.
So the loopback check runs on the ADDRESSES a host resolves to, never on the host text,
and a host that resolves to a mix of loopback and non-loopback addresses is refused
outright rather than filtered down to the loopback one - a resolver answering both is
already telling us something is wrong. The one check on the text comes BEFORE resolving
and only narrows (O-1, amendment S7, design §10): a host that is neither `localhost` nor an IP literal
is refused without being looked up, so no name ever reaches a resolver at all.

AND THEN CONNECT TO THE ADDRESS THAT WAS CHECKED. `HTTPConnection` is handed the
resolved literal, not the hostname, so the name is resolved exactly once per request
and the address checked is the address dialled. Passing the hostname would leave a
window - check `localhost` -> 127.0.0.1, then let `http.client` resolve it again and
get whatever the second answer is. That window is the entire DNS-rebinding attack, and
it costs one line to close.
"""

import http.client
import ipaddress
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from pam.errors import PamNetworkError

# "Give me every address this host resolves to." A named seam rather than a call to
# `socket.getaddrinfo` written inline, because the resolution IS the security boundary:
# a test that cannot control what a name resolves to can only prove that pam refuses IP
# literals, which is the easy half. The tests drive this with a fake and never make a
# real DNS query.
Resolver = Callable[[str, int], list[str]]

DEFAULT_TIMEOUT_S = 10.0


def system_resolver(host: str, port: int) -> list[str]:
    """The real resolver: every address this machine's DNS/hosts stack gives for `host`.

    `getaddrinfo` returns one record per (family, socktype, protocol) combination, so a
    name usually appears several times; the duplicates are harmless because the caller
    only asks whether they are ALL loopback. `IPPROTO_TCP` trims the obvious triplicates
    without changing which addresses come back.

    Element 4 of each record is the sockaddr - `(address, port)` for IPv4 and
    `(address, port, flowinfo, scope_id)` for IPv6 - so element 0 of that is the
    address, in both families.
    """
    records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    return [str(record[4][0]) for record in records]


def resolve_loopback_address(host: str, port: int, resolver: Resolver = system_resolver) -> str:
    """The address `host` resolves to, or `PamNetworkError` if any answer is off-box.

    Every refusal in pam's privacy story funnels through here, so the rules are stated
    once, in one place, and each one is a separate `raise` with its own message - a
    single "refused" for four different reasons is a support problem, and the four are
    genuinely different mistakes.

    A host that resolves to NOTHING is refused rather than treated as harmless. It
    cannot leak anything by itself, but the caller is about to connect to something and
    "no addresses" is not an answer it can act on; failing here names the host, while
    letting it through would surface as an obscure error out of `http.client`.

    ALL addresses must be loopback, not merely the first. A resolver that answers
    `127.0.0.1` and `203.0.113.7` for the same name is doing exactly what a rebinding
    attack does, and picking the loopback one out of the pair would make pam's behaviour
    depend on record ordering - which is under the attacker's control.
    """
    # O-1 (S7): a NAME is refused before anything resolves it. Resolving first would put the
    # user's chosen text in front of a DNS server, and nothing about "resolve first, then
    # check" requires that: `localhost` and IP literals are the only hosts pam ever dials.
    if host.lower() != "localhost":
        try:
            ipaddress.ip_address(host)
        except ValueError as exc:
            raise PamNetworkError(
                f"{host!r} is a name, not localhost or an address; pam will not look it"
                " up (spec 10: nothing leaves the machine)."
            ) from exc
    addresses = resolver(host, port)
    if not addresses:
        raise PamNetworkError(
            f"{host!r} does not resolve to any address; pam will not connect."
        )
    for address in addresses:
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            # An unparseable answer is refused, never ignored - a resolver handing back
            # a NAME instead of an address is the shape that lands here. Note that a
            # scoped IPv6 address ("fe80::1%eth0") does NOT: Python 3.14's `ipaddress`
            # parses those, so they are refused one branch down as non-loopback.
            raise PamNetworkError(
                f"{host!r} resolved to {address!r}, which is not an IP address pam can check."
            ) from exc
        if not parsed.is_loopback:
            raise PamNetworkError(
                f"{host!r} resolved to {address}, which is not on this machine. pam only"
                " ever talks to loopback (spec 10: nothing leaves the machine)."
            )
    return addresses[0]


@dataclass(frozen=True)
class LoopbackResponse:
    """One HTTP response, fully read, with the connection already closed.

    Read to completion and returned as bytes rather than handed back as a live file
    object, so that `request` can own the connection's whole lifetime in one `finally`.
    A streaming interface would let a caller leave a socket open, and this module's
    entire job is being the one place that decides when a socket exists.
    """

    status: int
    reason: str
    headers: dict[str, str]
    body: bytes


class LoopbackHttpClient:
    """An HTTP client that can only ever reach this machine.

    Construction resolves and checks the host, so a misconfigured destination fails
    where it is configured rather than at the first request - and `pam doctor` or a
    future `enrich` preflight can find out whether a destination is acceptable without
    sending anything. Each `request` then resolves and checks AGAIN: the answer can
    change between construction and use, and the check is one lookup.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 80,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        resolver: Resolver = system_resolver,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.resolver = resolver
        # Refuse at construction. The return value is deliberately dropped: what is
        # wanted here is the exception, and re-using an address resolved minutes ago
        # would be the stale-check bug this module exists to avoid.
        resolve_loopback_address(host, port, resolver)

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> LoopbackResponse:
        """Perform one request against the checked loopback address.

        The `Host` header carries the name the caller asked for, because the connection
        is made to the resolved literal and `http.client` would otherwise announce the
        raw IP - which a local server using name-based routing would not recognise.

        Both failure families are turned into `PamNetworkError`: `OSError` for "the
        local server is not running" and `HTTPException` for "it answered something that
        is not HTTP". Neither is a pam bug, so neither should reach the user as a
        traceback; `PamError`'s contract prints the message and exits 1.
        """
        address = resolve_loopback_address(self.host, self.port, self.resolver)
        sent = {"Host": f"{self.host}:{self.port}"}
        sent.update(headers or {})
        connection = http.client.HTTPConnection(address, self.port, timeout=self.timeout)
        try:
            connection.request(method, path, body=body, headers=sent)
            response = connection.getresponse()
            return LoopbackResponse(
                status=response.status,
                reason=response.reason,
                headers=dict(response.getheaders()),
                body=response.read(),
            )
        except (OSError, http.client.HTTPException) as exc:
            raise PamNetworkError(
                f"pam could not reach {self.host}:{self.port} on this machine: {exc}"
            ) from exc
        finally:
            connection.close()
