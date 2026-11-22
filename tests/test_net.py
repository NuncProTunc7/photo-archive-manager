"""`pam/net.py`: the one client, and the resolution check that is the whole point.

NO TEST HERE MAKES A DNS QUERY, and that is a property of the file rather than an
accident of what it happens to assert. Every case drives `resolve_loopback_address`
through a fake resolver, because the interesting refusals are precisely the ones the
machine running the suite cannot be asked to reproduce: a hostname that resolves off-box
needs somebody else's DNS to answer, and a test that depended on that would pass or fail
according to what network it ran on. The fake is not a weakening of the test - it is the
only way to state the case at all.

`test_the_client_never_reaches_the_network_to_refuse_a_host` asserts that absence
directly: it installs a resolver that fails the test if it is called with anything the
case did not expect.

TWO TESTS DO OPEN A SOCKET, BOTH TO 127.0.0.1, AND THEY ARE THE M455 CLOSURE. Until this
review the whole file stopped at a refusal, so `request()`'s body - the connect, the
method, the path, the `Host` header, the response assembly, the error wrapping - shipped
with no test executing any of it, and `HTTPConnection(address)` could become
`HTTPConnection(self.host)` unnoticed. That is the DNS-rebinding window `net.py` exists
to close. Closing a hole that big with a loopback listener is the cheap, honest option;
`.invalid` as the host name is what makes it an assertion about WHICH of the two was
dialled rather than a smoke test. No name is resolved by the system in either.
"""

import ipaddress
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from guards import (
    ALLOWED_NETWORK_IMPORTS,
    BANNED_NETWORK_MODULES,
    NETWORK_CAPABLE_MODULES,
    network_import_sites,
)

from pam.errors import PamError, PamNetworkError
from pam.net import LoopbackHttpClient, resolve_loopback_address, system_resolver


def fixed(answers):
    """A resolver that always gives `answers`, whatever it is asked."""

    def resolver(host, port):
        return list(answers)

    return resolver


def table(answers):
    """A resolver driven by a host -> addresses table, as a real one would behave."""

    def resolver(host, port):
        return list(answers[host])

    return resolver


# --- what is allowed --------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "addresses"),
    [
        ("localhost", ["127.0.0.1"]),
        ("localhost", ["::1"]),
        # A real `localhost` on a dual-stack machine answers with both, and both are
        # loopback, so the pair must be accepted - otherwise pam would refuse the one
        # destination it exists to allow on most laptops.
        ("localhost", ["::1", "127.0.0.1"]),
        ("127.0.0.1", ["127.0.0.1"]),
        ("::1", ["::1"]),
        # The whole 127.0.0.0/8 block is loopback, not just .0.1 - a local model server
        # bound to 127.0.0.2 is still on this machine.
        ("127.0.0.2", ["127.0.0.2"]),
    ],
)
def test_a_host_that_resolves_only_to_loopback_is_allowed(host, addresses):
    assert resolve_loopback_address(host, 8080, fixed(addresses)) == addresses[0]


def test_the_client_can_be_constructed_for_a_loopback_host():
    """Construction is the first refusal point, so it has to be provable it can succeed."""
    client = LoopbackHttpClient("localhost", 8080, resolver=fixed(["127.0.0.1"]))
    assert (client.host, client.port) == ("localhost", 8080)


# --- what is refused --------------------------------------------------------------


def test_a_public_ip_literal_is_refused():
    """The easy half: an address that is plainly not on this machine."""
    with pytest.raises(PamNetworkError, match="not on this machine"):
        resolve_loopback_address("203.0.113.7", 80, fixed(["203.0.113.7"]))


def test_a_public_hostname_is_refused_before_it_is_resolved():
    """O-1 (S7): a NAME is refused without the resolver being asked at all."""
    calls = []

    def recording(host, port):
        calls.append(host)
        return ["93.184.216.34"]

    with pytest.raises(PamNetworkError, match="is a name, not localhost or an address"):
        resolve_loopback_address("api.example.com", 443, recording)
    assert calls == []


def test_a_name_that_looks_local_is_refused_before_it_is_resolved():
    """THE CASE THIS MODULE EXISTS FOR (spec 10), now stopped one step earlier.

    `localhost.evil.example` contains the string `localhost` and is a name somebody
    else's DNS server answers however it likes. It used to be refused after resolving, on
    the address; O-1 refuses it before the resolver (and so any DNS server) hears of it.
    """
    calls = []

    def recording(host, port):
        calls.append(host)
        return ["203.0.113.7"]

    with pytest.raises(PamNetworkError, match="is a name, not localhost or an address"):
        resolve_loopback_address("localhost.evil.example", 8080, recording)
    assert calls == []


def test_a_localhost_that_RESOLVES_off_box_is_still_refused_on_the_address():
    """The address check survives O-1: `localhost` is allowed to be ASKED, not believed."""
    with pytest.raises(PamNetworkError, match="203.0.113.7"):
        resolve_loopback_address("localhost", 8080, table({"localhost": ["203.0.113.7"]}))


O1_ASKED = [
    ("localhost", "127.0.0.1"),
    ("LOCALHOST", "127.0.0.1"),
    ("127.0.0.1", "127.0.0.1"),
    ("::1", "::1"),
]
O1_REFUSED_UNASKED = [
    "example.com",
    "localhost.evil.example",
    "evil.localhost",
    "localhost.",
    " localhost",
    "model.invalid",
    "",
    # M2481 (F2): names spelled ONLY in address characters - a "looks like an address" shortcut
    # in place of `ipaddress.ip_address` would send these to a DNS server.
    "cafe.de",
    "1.2.3.4.5",
]
assert O1_ASKED and O1_REFUSED_UNASKED, "O-1's controls are empty; pytest would skip them"


@pytest.mark.parametrize(("host", "answer"), O1_ASKED)
def test_O1_localhost_in_any_case_and_loopback_literals_reach_the_resolver(host, answer):
    calls = []

    def recording(h, port):
        calls.append(h)
        return [answer]

    assert resolve_loopback_address(host, 8080, recording) == answer
    assert calls == [host]


def test_O1_an_off_box_literal_is_asked_and_then_refused_by_the_loopback_rule():
    calls = []

    def recording(h, port):
        calls.append(h)
        return ["192.0.2.1"]

    with pytest.raises(PamNetworkError, match="not on this machine"):
        resolve_loopback_address("192.0.2.1", 8080, recording)
    assert calls == ["192.0.2.1"]


@pytest.mark.parametrize("host", O1_REFUSED_UNASKED)
def test_O1_every_other_name_is_refused_and_the_resolver_is_never_called(host):
    calls = []

    def recording(h, port):
        calls.append(h)
        return ["127.0.0.1"]

    with pytest.raises(PamNetworkError, match="is a name, not localhost or an address"):
        resolve_loopback_address(host, 8080, recording)
    assert calls == []


def test_a_host_that_resolves_to_both_loopback_and_public_is_refused():
    """Not filtered down to the loopback answer - refused entirely.

    Taking the loopback record out of a mixed answer would make pam's destination
    depend on record ORDER, and record order is the attacker's to choose. A resolver
    answering both is already reporting that something is wrong.
    """
    with pytest.raises(PamNetworkError, match="203.0.113.7"):
        resolve_loopback_address("localhost", 8080, fixed(["127.0.0.1", "203.0.113.7"]))


def test_the_mixed_answer_is_refused_in_either_order():
    """The order-independence claim, stated rather than assumed.

    Written as its own test because the loop above could pass by luck if it stopped at
    the first address and the public one happened to come first.
    """
    with pytest.raises(PamNetworkError, match="203.0.113.7"):
        resolve_loopback_address("localhost", 8080, fixed(["203.0.113.7", "127.0.0.1"]))


def test_a_host_that_resolves_to_nothing_is_refused():
    with pytest.raises(PamNetworkError, match="does not resolve"):
        resolve_loopback_address("localhost", 8080, fixed([]))


def test_an_unparseable_answer_is_refused_rather_than_ignored():
    """A resolver answer pam cannot check is a refusal, not a pass.

    The realistic shape is a resolver that hands back a NAME where an address was
    promised - which is what a rewritten `system_resolver` reading the wrong sockaddr
    element would do. The safe reading of "I cannot tell what this is" is no.

    THE FIRST DRAFT OF THIS TEST USED `fe80::1%eth0` AND WAS WRONG. Python 3.14's
    `ipaddress` parses scoped IPv6 addresses (they were a `ValueError` in older
    versions), so that case never reached this branch - it was refused one line further
    down, as a non-loopback address, for a different reason. The premise is asserted
    below so the same mistake cannot be made silently again.
    """
    with pytest.raises(PamNetworkError, match="not an IP address pam can check"):
        resolve_loopback_address("localhost", 8080, fixed(["localhost"]))
    with pytest.raises(ValueError):
        ipaddress.ip_address("localhost")


# M10 TASK 7 REVIEW, FINDING F1 (M1531). "Off box" was pinned by ONE FAMILY of address -
# the public internet (`203.0.113.7`, `93.184.216.34`) - and by nothing else. Widening
# `resolve_loopback_address` to accept the whole of `192.168.0.0/16` left all 2733 tests
# green: pam would then talk to any machine on the user's LAN, which is precisely what
# vision S4 ("nothing leaves the machine") forbids, and the most plausible convenience
# edit anyone will ever propose here is "let me point it at the Ollama on my home server".
#
# SPELLED FROM SPEC 10 BY HAND, AS A LIST OF LITERALS, and deliberately NOT derived from
# `ipaddress` predicates: an expectation written as `not is_private` or `not is_loopback`
# would be the production check's own definition copied into its oracle, which is this
# project's most repeated defect. Spec 10's boundary is "loopback", so everything that is
# not loopback is refused - one row per family somebody might mistake for local.
OFF_BOX_ADDRESSES = [
    ("192.168.1.50", "an RFC1918 LAN address - another machine in the same house"),
    ("10.0.0.5", "the other RFC1918 block, and what a corporate VPN hands out"),
    ("172.16.0.1", "the third RFC1918 block, the one people forget exists"),
    ("100.64.0.1", "CGNAT (RFC6598): not `is_private`, and still not this machine"),
    ("169.254.1.1", "IPv4 link-local: reachable on the wire, not on this machine"),
    ("0.0.0.0", "the unspecified address - a listener binds it, a client must not dial it"),
    ("fd00::1", "an IPv6 unique-local address, the v6 spelling of the LAN case"),
    ("fe80::1", "IPv6 link-local, unscoped"),
]

# M743: pytest SKIPS an empty parametrize rather than failing it, so a list that lost its
# entries would take its whole negative control with it, silently.
assert OFF_BOX_ADDRESSES, "the off-box family list is empty; its controls have vanished"


@pytest.mark.parametrize(("address", "why"), OFF_BOX_ADDRESSES)
def test_every_off_box_family_is_refused_and_not_only_the_public_internet(address, why):
    """Spec 10 permits LOOPBACK, not "not obviously the internet"."""
    with pytest.raises(PamNetworkError, match="not on this machine"):
        resolve_loopback_address("localhost", 8080, fixed([address]))
    # And the client refuses at construction too, which is the seam a caller meets first.
    with pytest.raises(PamNetworkError, match="not on this machine"):
        LoopbackHttpClient("localhost", 8080, resolver=fixed([address]))


def test_the_off_box_list_does_not_accidentally_contain_a_loopback_address():
    """The control on the control: a row that IS loopback would pass for the wrong reason.

    Without this, adding `127.0.0.2` to the list above would turn a negative control into
    a test asserting that pam refuses the one destination it exists to allow - and it
    would fail loudly rather than silently, but only once somebody ran it. Stated as its
    own assertion so the list's own integrity is checked rather than assumed.
    """
    loopback = [a for a, _ in OFF_BOX_ADDRESSES if ipaddress.ip_address(a).is_loopback]
    assert loopback == [], (
        f"these are on this machine and must not be negative cases: {loopback}"
    )


def test_a_scoped_link_local_address_is_refused_as_off_box():
    """The case the test above used to claim, kept because it is a real answer shape.

    `fe80::1%eth0` parses on this interpreter, so it is refused by the loopback check
    rather than the parse check - refused either way, which is the point, but by a
    different line and with a different message.
    """
    assert not ipaddress.ip_address("fe80::1%eth0").is_loopback
    with pytest.raises(PamNetworkError, match="not on this machine"):
        resolve_loopback_address("localhost", 8080, fixed(["fe80::1%eth0"]))


def test_the_client_refuses_at_construction_not_at_first_request():
    """A misconfigured destination fails where it is configured.

    Deliberate: a preflight (`pam doctor`, or M10's enrich setup) can then find out
    whether a destination is acceptable without sending anything at all.
    """
    with pytest.raises(PamNetworkError):
        LoopbackHttpClient("localhost", 443, resolver=fixed(["93.184.216.34"]))


def test_the_refusal_is_a_pam_error_so_the_cli_prints_it_and_exits_one():
    """spec 13's exit-code contract. Pointing pam at another box is a user mistake,
    not an internal fault, so it must not surface as a traceback and exit 2."""
    assert issubclass(PamNetworkError, PamError)


def test_the_client_never_reaches_the_network_to_refuse_a_host():
    """The absence, asserted: a refused destination costs one resolution and no socket.

    If `LoopbackHttpClient` ever resolved through `system_resolver` regardless of the
    injected one - the obvious way for the seam to rot - this fails, because the real
    resolver would be asked for a name that does not exist.
    """
    calls = []

    def recording(host, port):
        calls.append((host, port))
        return ["198.51.100.9"]

    with pytest.raises(PamNetworkError):
        LoopbackHttpClient("localhost", 11434, resolver=recording)
    assert calls == [("localhost", 11434)]


def test_request_re_resolves_and_refuses_before_it_opens_anything():
    """The second check, which is the one a rebinding attack has to beat.

    The resolver here answers loopback once - satisfying construction - and public
    afterwards, which is exactly a DNS record whose TTL expired between configuring a
    destination and using it. `request` must resolve again and refuse; a client that
    trusted the address it checked at construction would connect.

    No socket is opened on either path, which is the assertion's other half: the refusal
    happens before `HTTPConnection` is built, so a failure of this test shows up as a
    connection error rather than as a passing test that leaked.

    THE WIRE PATH BELOW THIS REFUSAL is exercised by
    `test_the_wire_path_dials_the_ADDRESS_and_not_the_HOSTNAME` (review finding M455).
    It was not, when this test was written, and this test cannot reach it: it asserts
    that the connection is never built.
    """
    answers = [["127.0.0.1"], ["203.0.113.7"]]

    def draining(host, port):
        return answers.pop(0)

    client = LoopbackHttpClient("localhost", 11434, resolver=draining)
    with pytest.raises(PamNetworkError, match="203.0.113.7"):
        client.request("POST", "/caption", body=b"{}")
    assert answers == [], "request did not resolve again; it reused the construction check"


class _Echo(BaseHTTPRequestHandler):
    """A one-shot local server that reports back what it was actually sent."""

    protocol_version = "HTTP/1.0"

    def do_POST(self):  # noqa: N802 - the name is BaseHTTPRequestHandler's contract
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        payload = json.dumps(
            {
                "method": self.command,
                "path": self.path,
                "host": self.headers.get("Host"),
                "body": body.decode(),
            }
        ).encode()
        self.send_response(200, "Very Well")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Pam-Test", "yes")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        """Silence. The default writes every request to stderr."""


def test_the_wire_path_dials_the_ADDRESS_and_not_the_HOSTNAME(monkeypatch):
    """REVIEW FINDING M455: `request()`'s body shipped with no test executing it at all.

    Every other test in this file stops at a refusal, so `HTTPConnection(address, ...)`
    could be rewritten to `HTTPConnection(self.host, ...)` - re-opening the DNS-rebinding
    window that `net.py`'s own docstring calls "the entire attack, and it costs one line
    to close" - and the suite stayed green. The method, the path, the `Host` header, the
    response assembly and the `OSError` wrapping were all unexecuted with it.

    THE TRICK THAT MAKES THIS A REAL TEST RATHER THAN A SMOKE TEST. O-1 (S7) now refuses
    any name but `localhost`, so the old `model.invalid` trick is gone. The client is given
    the host `localhost` and a resolver that answers `127.0.0.1`, and `socket.getaddrinfo`
    is patched to FAIL for the host text "localhost" while delegating everything else:
      * dialling the CHECKED ADDRESS works - `http.client` resolves the literal 127.0.0.1 -
        and
      * dialling the HOSTNAME has to resolve "localhost" and cannot work under the patch.
    The assertion is therefore about which of the two was used, not merely that a
    request succeeded.

    LOOPBACK ONLY, and no DNS query: the listener binds 127.0.0.1 on an OS-chosen port
    and the only name involved is resolved by the injected fake.
    """
    real_getaddrinfo = socket.getaddrinfo

    def no_name_lookup(host, *args, **kwargs):
        if host == "localhost":
            raise OSError("the hostname was dialled instead of the checked address")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", no_name_lookup)
    # The patch IS the discrimination, so prove it is armed (M2478, F1): a disarmed patch let
    # a client dialling the HOSTNAME pass, since `localhost` really does reach 127.0.0.1.
    with pytest.raises(OSError, match="the hostname was dialled"):
        socket.create_connection(("localhost", 9))
    server = HTTPServer(("127.0.0.1", 0), _Echo)
    server.timeout = 20
    port = server.server_address[1]
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    try:
        client = LoopbackHttpClient("localhost", port, resolver=fixed(["127.0.0.1"]))
        response = client.request(
            "POST", "/caption", body=b'{"image": 1}', headers={"X-Trace": "m455"}
        )
    finally:
        thread.join(timeout=20)
        server.server_close()

    assert (response.status, response.reason) == (200, "Very Well")
    assert response.headers["X-Pam-Test"] == "yes"
    seen = json.loads(response.body)
    # The method and path arrive as given - not hard-coded, not dropped.
    assert (seen["method"], seen["path"]) == ("POST", "/caption")
    # The `Host` header carries the NAME even though the connection went to the address,
    # which is the half of the design a name-based local server depends on.
    assert seen["host"] == f"localhost:{port}"
    assert seen["body"] == '{"image": 1}'


def test_a_loopback_destination_with_nothing_listening_is_a_pam_error():
    """The `OSError` arm of `request()`'s wrapping, which M455 also left unexecuted.

    "The local model server is not running" is a user-visible condition, not a pam bug,
    so it must arrive as a `PamError` the CLI prints and exits 1 on - never as a raw
    `ConnectionRefusedError` traceback (spec 13).

    The port is taken by binding and immediately closing a listener, so it is a port
    nothing is on rather than a number picked out of the air.
    """
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    client = LoopbackHttpClient("127.0.0.1", port, resolver=fixed(["127.0.0.1"]))
    with pytest.raises(PamNetworkError, match="could not reach 127.0.0.1"):
        client.request("GET", "/")


def test_the_real_resolver_returns_loopback_for_the_loopback_literal():
    """`system_resolver` itself, on the one input that needs no DNS server.

    An IP literal is resolved by the stack without a query, so this exercises the real
    function - the sockaddr unpacking, which is the part a fake cannot check - without
    the suite depending on a network. A name would need one; that is why no name
    appears here.
    """
    assert system_resolver("127.0.0.1", 80) == ["127.0.0.1"]


# --- the grep-gate, as an AST walk (spec 10) ---------------------------------------


def pam_sources():
    """Every module in the production package, as (relative posix path, source)."""
    import pam

    pam_dir = Path(pam.__file__).parent
    return [
        (path.relative_to(pam_dir).as_posix(), path.read_text(encoding="utf-8"))
        for path in sorted(pam_dir.rglob("*.py"))
    ]


def test_the_walk_really_covers_the_whole_package():
    """`pam_sources()`'s REACH, against a second implementation of the same walk.

    REVIEW FINDING M460. `pam_sources()` was narrowed to `pam_dir.glob("net*.py")` - one
    module instead of sixty - and the whole suite stayed green. The planted-file test
    below claims in its docstring to cover exactly this; it does not. Appending an
    offender to whatever the walk returned and asserting the offender list holds only
    that offender is true for ANY walk over a clean subset, and the allowlist test
    passes too, because both of its entries live in `net.py`. A guard that has quietly
    stopped looking at most of the package is the failure this task exists to prevent,
    and it is invisible from inside the guard.

    So the reach is checked against an INDEPENDENT enumeration - `os.walk` rather than
    `Path.rglob` - in the same spirit as `tests/archives.py::open_plan` being the
    suite's own second implementation of a repo query. A mutation to one is not a
    mutation to the other. The floor is a second, cruder line of defence for the case
    where both are broken the same way.
    """
    import os

    import pam

    pam_dir = Path(pam.__file__).parent
    walked = {
        (Path(dirpath) / filename).relative_to(pam_dir).as_posix()
        for dirpath, _, filenames in os.walk(pam_dir)
        for filename in filenames
        if filename.endswith(".py")
    }
    assert {name for name, _ in pam_sources()} == walked
    assert len(walked) > 30, f"the package cannot have shrunk to {len(walked)} modules"


def test_no_module_in_the_package_imports_a_third_party_http_client():
    """spec 10's grep-gate: no `requests`, `httpx` or `urllib.request` anywhere.

    Reported with the offending module's name rather than as a bare count, because the
    failure a year from now will be somebody adding a convenience import to a module
    with no obvious connection to networking, and "guards failed" would send them
    reading the guard instead of their own diff.
    """
    offenders = [
        (name, sites)
        for name, source in pam_sources()
        if (sites := network_import_sites(source))
    ]
    assert offenders == []


def test_only_net_py_can_reach_the_network_at_all():
    """The stronger claim spec 10 actually makes: ONE module contains the HTTP code.

    Banning `requests` while `http.client` is imported in six places would satisfy the
    letter of the plan's grep-gate and none of its purpose - stdlib reaches the network
    exactly as well as a third-party library does. So this walks the same AST for the
    wider set of network-capable stdlib modules and compares against an allowlist, in
    the same shape as `ALLOWED_MOVE_SITES`.

    Compared as a sorted LIST rather than a set, for the reason the move guard is: a
    second `import socket` in `net.py` must fail rather than be swallowed as a
    duplicate.
    """
    sites = [
        (name, imported)
        for name, source in pam_sources()
        for imported in network_import_sites(source, NETWORK_CAPABLE_MODULES)
    ]
    assert sorted(sites) == sorted(ALLOWED_NETWORK_IMPORTS)


# M15 Task 12: the allowlist spelled from the SPEC (design 10 and amendment E3), not from the
# production-side constant - an oracle built from the list it checks passes for any list. Seven
# entries: the loopback client (2), the loopback review server (2), the release client (3).
SPEC_NETWORK_IMPORTS = [
    ("net.py", "http.client"),
    ("net.py", "socket"),
    ("webui/server.py", "http.server"),
    ("webui/server.py", "webbrowser"),
    ("update/fetch.py", "http.client"),
    ("update/fetch.py", "socket"),
    ("update/fetch.py", "ssl"),
]


def test_the_network_allowlist_is_the_one_the_spec_names():
    """`ALLOWED_NETWORK_IMPORTS` equals design 10's list, entry for entry: a fourth module, or a
    fourth import in `update/fetch.py`, needs a spec amendment before it needs a guard edit."""
    assert sorted(ALLOWED_NETWORK_IMPORTS) == sorted(SPEC_NETWORK_IMPORTS)


@pytest.mark.parametrize(
    "planted",
    [
        "import requests\n",
        "import requests as r\n",
        "import requests.adapters\n",
        "from requests import get\n",
        "from requests import get as fetch\n",
        "from requests.sessions import Session\n",
        "import httpx\n",
        "import httpx as h\n",
        "from httpx import Client\n",
        "import urllib.request\n",
        # The two a text search for `urllib.request` would miss or misjudge - the
        # aliased import reads nothing like a network call at the use site, and the
        # `from urllib import request` form does not contain the string at all.
        "import urllib.request as u\n",
        "from urllib import request\n",
        "from urllib import request as u\n",
        "from urllib.request import urlopen\n",
        # And the two ways around a guard that only reads import statements.
        'import importlib\nx = importlib.import_module("requests")\n',
        'x = __import__("httpx")\n',
    ],
)
def test_the_network_guard_catches_every_route_to_a_third_party_client(planted):
    """The guard proven ENFORCING, one row per route in.

    A guard that has never been seen to fail is not known to work: `network_import_sites`
    returning `[]` unconditionally would pass both tests above, because the package it
    walks is clean. These are the rows that would go red.
    """
    assert network_import_sites(planted) != []


def test_the_network_guard_is_proven_enforcing_on_a_planted_file_in_the_package():
    """The end-to-end version: the real walk over the real package, plus one bad file.

    The parametrised cases above prove the FUNCTION sees each route. This proves the
    TEST would fail - that `pam_sources()` really reads what is on disk.

    IT DOES NOT PROVE THE WALK'S REACH, though an earlier version of this docstring
    claimed it did (review finding M460). Appending an offender to whatever the walk
    returned and asserting the result holds only that offender is true for any walk over
    a clean subset - `pam_sources()` was narrowed to one module and this test stayed
    green. `test_the_walk_really_covers_the_whole_package` is the test that owns reach.

    The planted source is analysed in place rather than written into `pam/`: writing a
    file into the production package during a test run is exactly the sort of thing that
    survives a crash and gets committed.
    """
    sources = [*pam_sources(), ("evil/leak.py", "import requests\n")]
    offenders = [
        (name, sites) for name, source in sources if (sites := network_import_sites(source))
    ]
    assert offenders == [("evil/leak.py", ["requests"])]


def test_the_network_guard_does_not_fire_on_prose_or_on_pams_own_modules():
    """The other direction, and the reason this is an AST walk and not a grep.

    Every line of `benign` below contains a banned module name as TEXT. A grep-gate
    written literally would fail on all four - including on its own source file, since
    `guards.py`'s docstring names `urllib.request` out loud - and a guard that fires on
    prose gets deleted rather than obeyed.
    """
    benign = (
        '"""No module here imports requests, httpx or urllib.request."""\n'
        "# import requests\n"
        "from urllib import parse\n"
        "from pam.net import LoopbackHttpClient\n"
        'MESSAGE = "install requests? never."\n'
    )
    assert network_import_sites(benign) == []
    assert network_import_sites(benign, NETWORK_CAPABLE_MODULES) == []
    # And on the guard's own source, which names every banned module in its prose.
    guard_source = Path(__file__).with_name("guards.py").read_text(encoding="utf-8")
    assert network_import_sites(guard_source) == []


def test_a_relative_import_of_a_name_shadowing_a_stdlib_module_is_not_an_offence():
    """`from . import http` is pam's own code, whatever it is called.

    Guarded explicitly because `node.module` for a relative import is the name inside
    the package, so without the `node.level` check a future `pam/http/` package would
    make the whole suite red for a reason that has nothing to do with networking.
    """
    assert network_import_sites("from . import http\n", NETWORK_CAPABLE_MODULES) == []
    assert network_import_sites("from .http import client\n", NETWORK_CAPABLE_MODULES) == []


def test_the_banned_list_is_exactly_what_the_spec_names():
    """spec 10 names three. Pinned so that trimming the list is a deliberate edit."""
    assert BANNED_NETWORK_MODULES == ("requests", "httpx", "urllib.request")


# Spelled out from spec 10 rev 12, which enumerates the network-capable stdlib modules
# by name, and NOT copied from `guards.py` - an oracle built from the definition it
# checks passes for any value of that definition (the `repo.ENRICHERS` lesson).
SPEC_10_NETWORK_CAPABLE = (
    "socket",
    "socketserver",
    "ssl",
    "http",
    "urllib.request",
    "ftplib",
    "smtplib",
    "poplib",
    "imaplib",
    "telnetlib",
    "xmlrpc",
    "asyncio",
    "webbrowser",
)


def test_the_capability_list_is_exactly_what_the_spec_names_plus_the_clients():
    """REVIEW FINDING M613a. `NETWORK_CAPABLE_MODULES` was compared against nothing.

    `BANNED_NETWORK_MODULES` has an equality pin spelled from the spec and a planted row
    per name. The wider capability tuple - the one spec 10 rev 11 adopted the allowlist
    FOR - had neither, and deleting ten of its sixteen entries (`socketserver`, `ssl`,
    `ftplib`, `smtplib`, `poplib`, `imaplib`, `telnetlib`, `xmlrpc`, `asyncio`,
    `aiohttp`) left all 1408 tests green: the four sites that exist today are covered by
    the six survivors, so the allowlist equality still balanced.

    The ten deleted names are exactly the set the guard exists to catch a FUTURE import
    of. `M7 Task 7` is the case in point - `http.server` and `webbrowser` were both in
    the un-imported half until this milestone.
    """
    assert sorted(NETWORK_CAPABLE_MODULES) == sorted(
        (*SPEC_10_NETWORK_CAPABLE, "requests", "httpx", "aiohttp")
    )
    # Every banned name is also capability-banned, or the wider walk would answer a
    # narrower question than the narrow one.
    assert set(BANNED_NETWORK_MODULES) <= set(NETWORK_CAPABLE_MODULES)


@pytest.mark.parametrize(
    "planted",
    [
        "import socketserver\n",
        "from socketserver import ThreadingMixIn\n",
        "import ssl\n",
        "import ftplib\n",
        "import smtplib\n",
        "import poplib\n",
        "import imaplib\n",
        "import telnetlib\n",
        "import xmlrpc.client\n",
        "import asyncio\n",
        "import asyncio as aio\n",
        "import aiohttp\n",
        'x = __import__("asyncio")\n',
    ],
)
def test_the_capability_guard_catches_the_modules_nothing_imports_yet(planted):
    """The second half of M613a's closure, in the shape `BANNED_NETWORK_MODULES` has.

    The equality above stops the tuple being trimmed; these rows prove the guard would
    actually fire on each trimmed name. A name in a list that no test exercises is a
    name whose removal is free.
    """
    assert network_import_sites(planted, NETWORK_CAPABLE_MODULES) != []
