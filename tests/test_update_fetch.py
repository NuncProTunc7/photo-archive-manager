"""`pam/update/fetch.py` over a REAL transport: TLS on 127.0.0.1, reached through the injected
resolver (M15 Task 12, Step 2; E3 as spike 10B measured it).

Every refusal here is decided by the production code against a real socket: a real handshake with
verification on, real HTTP framing, real timeouts. Nothing reaches GitHub - `tls_server.TlsServer`
answers for E3's three names, and the resolver it hands the client maps each name to itself.

THE TIMING TESTS ARE BOUNDED, NEVER RACED (CLAUDE.md's hosted-CI gotchas). Each deadline test sets a
deadline of `DEADLINE_S` and a socket timeout of `SOCKET_S`, far above it, so ONLY the overall
deadline can end the request in time; the call runs on a daemon thread joined at `GUARD_S`, so a
regression that removes the deadline FAILS (the guard, or the socket timeout's other sentence)
instead of hanging the suite. No assertion is tighter than the guard. The one property that needs a
precise time - the deadline spans the redirect - uses an injected clock instead of a stopwatch.
"""

import socket
import ssl
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime

import pytest
import tls_server
from tls_server import CA_FILE, UNTRUSTED, WRONG_HOST, WRONG_HOST_CA_FILE, TlsServer

from pam.update import fetch
from pam.update.fetch import (
    CertificateError,
    DeadlineError,
    FetchError,
    RateLimitedError,
    StatusError,
    Transport,
    checked_url,
)

DEADLINE_S = 1.5
SOCKET_S = 30.0
GUARD_S = 20.0
API = "https://api.github.com/repos/o/r/releases/latest"
DOWNLOAD = "https://github.com/o/r/releases/download/v1.0.0/pam-setup-1.0.0.exe"
ASSET_PATH = "/github-production-release-asset/1/abc?sig=x"
ASSET = f"https://release-assets.githubusercontent.com{ASSET_PATH}"
JSON = "application/vnd.github+json"


@pytest.fixture
def server():
    with TlsServer() as running:
        yield running


def transport(server, **kwargs):
    kwargs.setdefault("socket_timeout_s", SOCKET_S)
    return Transport(resolver=server.resolver, cafile=CA_FILE, **kwargs)


def get(client, url=API, **kwargs):
    kwargs.setdefault("accept", JSON)
    kwargs.setdefault("max_bytes", 1 << 20)
    kwargs.setdefault("deadline_s", 30.0)
    return client.get(url, **kwargs)


def bounded(call):
    """`call()` on a daemon thread: (its outcome, the seconds it took), failing at GUARD_S."""
    outcome = {}

    def target():
        try:
            outcome["value"] = call()
        except BaseException as error:  # handed back to the test's thread
            outcome["error"] = error

    started = time.monotonic()
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(GUARD_S)
    if thread.is_alive():
        pytest.fail(f"the request was still running after {GUARD_S} s: no deadline ended it")
    return outcome, time.monotonic() - started


# --- the fixtures themselves ------------------------------------------------------------------


def test_the_fixture_certificates_last_at_least_ten_years():
    """The brief's floor: ten years from the day they were made (2026-11-19)."""
    floor = datetime(2036, 11, 19, tzinfo=UTC).timestamp()
    for name in ("ca.pem", "server.pem", "untrusted-server.pem"):
        decoded = ssl._ssl._test_decode_cert(str(tls_server.TLS_DIR / name))
        assert ssl.cert_time_to_seconds(decoded["notAfter"]) >= floor, name
    for key in ("server.TEST-ONLY.key", "untrusted-server.TEST-ONLY.key"):
        assert (tls_server.TLS_DIR / key).read_text().startswith("TEST-ONLY PRIVATE KEY.")


# --- the happy path, and what is sent ---------------------------------------------------------


def test_a_verified_request_reads_the_body_and_sends_only_host_user_agent_and_accept(server):
    server.route("api.github.com", "/repos/o/r/releases/latest", tls_server.ok(b'{"a": 1}'))
    response = get(transport(server))
    assert (response.status, response.body, response.size) == (200, b'{"a": 1}', 8)
    (request,) = server.requests
    assert request.header_names == ("host", "user-agent", "accept")
    assert request.headers == {
        "host": "api.github.com",
        "user-agent": f"pam/{fetch.__version__}",
        "accept": JSON,
    }
    assert server.lookups == ["api.github.com"]


def test_production_verifies_with_the_systems_trust_store_and_the_systems_resolver():
    """Production passes neither seam: verification on, hostnames checked, getaddrinfo."""
    client = Transport()
    assert client._context.verify_mode == ssl.CERT_REQUIRED
    assert client._context.check_hostname is True
    assert client._resolver is fetch.system_resolver


def test_an_untrusted_certificate_is_refused_and_never_retried():
    with TlsServer(UNTRUSTED) as untrusted:
        untrusted.route("api.github.com", "/repos/o/r/releases/latest", tls_server.ok(b"{}"))
        with pytest.raises(
            CertificateError, match="could not verify api.github.com's certificate"
        ):
            get(transport(untrusted))
        assert untrusted.requests == []  # no HTTP was spoken over the unverified connection
        assert untrusted.lookups == ["api.github.com"]  # and nothing was tried a second time


def test_a_certificate_issued_for_another_host_name_is_refused_and_never_retried():
    """Hostname verification, behaviourally: the chain verifies (its CA is the trust store) and the
    name does not, so only `check_hostname` refuses it - with the certificate sentence, before any
    HTTP, and with no second attempt (M4317's sibling for the name rather than the chain)."""
    with TlsServer(WRONG_HOST) as wrong:
        wrong.route("api.github.com", "/repos/o/r/releases/latest", tls_server.ok(b"{}"))
        client = Transport(
            resolver=wrong.resolver, cafile=WRONG_HOST_CA_FILE, socket_timeout_s=SOCKET_S
        )
        with pytest.raises(
            CertificateError, match="could not verify api.github.com's certificate"
        ) as refused:
            get(client)
        assert "Hostname mismatch" in str(refused.value) or "doesn't match" in str(
            refused.value
        )
        assert wrong.requests == []
        assert wrong.lookups == ["api.github.com"]


# --- hosts and redirects (10B section 2.1) ----------------------------------------------------

# Spelled from E3 as 10B measured it, never read from `fetch.ALLOWED_HOSTS`.
ADMITTED = [
    ("https://api.github.com/x", ("api.github.com", "/x")),
    ("https://github.com/x?y=1", ("github.com", "/x?y=1")),
    (
        "https://release-assets.githubusercontent.com/x",
        ("release-assets.githubusercontent.com", "/x"),
    ),
    ("https://GitHub.COM/x", ("github.com", "/x")),
    ("https://github.com:443/x", ("github.com", "/x")),
]
REFUSED = [
    "http://github.com/x",  # not HTTPS
    "https://objects.githubusercontent.com/x",  # E3 rev 1's host, never observed (10B F1)
    "https://evil.example/x",
    "https://github.com.evil.example/x",
    "https://github.com./x",  # not the exact name
    "https://github.com:8443/x",
    "https://user@github.com/x",
    "/relative/x",
    "",
]


@pytest.mark.parametrize(("url", "expected"), ADMITTED)
def test_the_allowlist_admits_exactly_e3s_hosts_on_443(url, expected):
    assert checked_url(url) == expected


@pytest.mark.parametrize("url", REFUSED)
def test_the_allowlist_refuses_from_the_text_alone(url):
    with pytest.raises(FetchError):
        checked_url(url)


def test_a_refused_first_url_is_never_looked_up(server):
    with pytest.raises(FetchError, match="talks only to GitHub"):
        get(transport(server), "https://evil.example/x")
    assert server.lookups == []


def test_a_download_follows_exactly_one_redirect_to_the_asset_host(server):
    path = "/o/r/releases/download/v1.0.0/pam-setup-1.0.0.exe"
    server.route("github.com", path, tls_server.redirect(ASSET))
    server.route("release-assets.githubusercontent.com", ASSET_PATH, tls_server.ok(b"MZ-bytes"))
    response = get(
        transport(server), DOWNLOAD, redirect_to="release-assets.githubusercontent.com"
    )
    assert response.body == b"MZ-bytes"
    assert [(r.host, r.path) for r in server.requests] == [
        ("github.com", path),
        ("release-assets.githubusercontent.com", ASSET_PATH),
    ]


def test_a_redirect_to_http_is_refused_and_not_followed(server):
    path = "/o/r/releases/download/v1.0.0/pam-setup-1.0.0.exe"
    insecure = "http://release-assets.githubusercontent.com/x"
    server.route("github.com", path, tls_server.redirect(insecure))
    with pytest.raises(FetchError, match="not HTTPS"):
        get(transport(server), DOWNLOAD, redirect_to="release-assets.githubusercontent.com")
    assert server.lookups == ["github.com"]
    assert len(server.requests) == 1


def test_a_redirect_off_the_allowlist_is_refused_before_getaddrinfo_is_called(
    server, monkeypatch
):
    """The name is refused from the redirect's TEXT: neither the injected resolver nor the
    system's `getaddrinfo` is ever asked about it (the spy on the call itself)."""
    asked = []
    real = socket.getaddrinfo

    def spy(host, *args, **kwargs):
        asked.append(host)
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", spy)
    path = "/o/r/releases/download/v1.0.0/pam-setup-1.0.0.exe"
    server.route("github.com", path, tls_server.redirect("https://evil.example/x"))
    with pytest.raises(FetchError, match="talks only to GitHub"):
        get(transport(server), DOWNLOAD, redirect_to="release-assets.githubusercontent.com")
    assert "evil.example" not in server.lookups
    assert "evil.example" not in asked
    assert server.lookups == ["github.com"]


def test_a_redirect_to_another_allowed_host_than_the_one_expected_is_refused(server):
    path = "/o/r/releases/download/v1.0.0/pam-setup-1.0.0.exe"
    server.route("github.com", path, tls_server.redirect("https://api.github.com/x"))
    with pytest.raises(
        FetchError, match="only release-assets.githubusercontent.com is expected"
    ):
        get(transport(server), DOWNLOAD, redirect_to="release-assets.githubusercontent.com")
    assert server.lookups == ["github.com"]


def test_discovery_follows_no_redirect(server):
    server.route("api.github.com", "/repos/o/r/releases/latest", tls_server.redirect(API))
    with pytest.raises(FetchError, match="redirected pam elsewhere"):
        get(transport(server))
    assert len(server.requests) == 1


def test_a_second_redirect_is_refused(server):
    path = "/o/r/releases/download/v1.0.0/pam-setup-1.0.0.exe"
    server.route("github.com", path, tls_server.redirect(ASSET))
    server.route("release-assets.githubusercontent.com", ASSET_PATH, tls_server.redirect(ASSET))
    with pytest.raises(FetchError, match="redirected pam elsewhere"):
        get(transport(server), DOWNLOAD, redirect_to="release-assets.githubusercontent.com")
    assert len(server.requests) == 2


# --- the body --------------------------------------------------------------------------------


def test_an_oversize_body_is_refused_before_it_is_read(server):
    server.route("api.github.com", "/repos/o/r/releases/latest", tls_server.ok(b"x" * 101))
    with pytest.raises(FetchError, match="offered 101 bytes, more than the 100 expected"):
        get(transport(server), max_bytes=100)


def test_an_oversize_body_with_no_length_is_refused_as_it_streams(server):
    server.route("api.github.com", "/repos/o/r/releases/latest", tls_server.chunked(b"x" * 101))
    seen = []
    with pytest.raises(FetchError, match="more than the 100 bytes expected"):
        get(transport(server), max_bytes=100, sink=seen.append)
    assert sum(map(len, seen)) <= 100  # nothing past the cap reached the sink


def test_a_body_of_exactly_the_cap_is_read(server):
    server.route("api.github.com", "/repos/o/r/releases/latest", tls_server.ok(b"x" * 100))
    assert get(transport(server), max_bytes=100).size == 100


def test_a_truncated_body_is_refused(server):
    server.route(
        "api.github.com", "/repos/o/r/releases/latest", tls_server.truncated(b"x" * 10, 50)
    )
    with pytest.raises(FetchError):
        get(transport(server))


def test_a_chunked_body_is_read_whole(server):
    body = b"0123456789" * 20
    server.route("api.github.com", "/repos/o/r/releases/latest", tls_server.chunked(body, 7))
    assert get(transport(server)).body == body


def test_a_sink_receives_the_body_in_order(server):
    body = bytes(range(256)) * 600  # > one CHUNK_BYTES read
    server.route("api.github.com", "/repos/o/r/releases/latest", tls_server.ok(body))
    seen = []
    response = get(transport(server), sink=seen.append)
    assert (b"".join(seen), response.body, response.size) == (body, b"", len(body))


# --- statuses ------------------------------------------------------------------------------


@pytest.mark.parametrize("code", [403, 429])
def test_a_rate_limited_answer_is_its_own_outcome_with_the_reset(server, code):
    headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1700000000"}
    server.route(
        "api.github.com", "/repos/o/r/releases/latest", tls_server.status(code, headers)
    )
    with pytest.raises(RateLimitedError) as caught:
        get(transport(server))
    assert caught.value.reset == 1700000000


@pytest.mark.parametrize("code", [403, 404, 500, 503])
def test_any_other_status_is_refused_with_it(server, code):
    server.route("api.github.com", "/repos/o/r/releases/latest", tls_server.status(code))
    with pytest.raises(StatusError) as caught:
        get(transport(server))
    assert caught.value.status == code


# --- the overall deadline --------------------------------------------------------------------


def _expect_deadline(outcome, elapsed):
    assert isinstance(outcome.get("error"), DeadlineError), outcome
    assert elapsed < GUARD_S


def test_a_stalled_read_meets_the_overall_deadline(server):
    server.route("api.github.com", "/repos/o/r/releases/latest", tls_server.stall(server))
    client = transport(server)
    _expect_deadline(*bounded(lambda: get(client, deadline_s=DEADLINE_S)))


@pytest.mark.parametrize("headers_too", [True, False], ids=["head", "body"])
def test_slow_progress_under_every_socket_timeout_still_meets_the_deadline(server, headers_too):
    """One byte every 0.9 s never trips a 30 s socket timeout; only the deadline ends it."""
    answer = tls_server.trickle(server, headers_too=headers_too)
    server.route("api.github.com", "/repos/o/r/releases/latest", answer)
    client = transport(server)
    _expect_deadline(*bounded(lambda: get(client, deadline_s=DEADLINE_S)))


def test_a_stalled_tls_handshake_meets_the_deadline():
    with TlsServer(handshake=False) as silent:
        client = transport(silent)
        _expect_deadline(*bounded(lambda: get(client, deadline_s=DEADLINE_S)))


def test_a_lookup_that_never_returns_meets_the_deadline():
    release = threading.Event()
    asked = []

    def blocking(host, port):
        asked.append(host)
        release.wait(60)
        return [("127.0.0.1", 9)]

    client = Transport(resolver=blocking, cafile=CA_FILE, socket_timeout_s=SOCKET_S)
    try:
        _expect_deadline(*bounded(lambda: get(client, deadline_s=DEADLINE_S)))
        assert asked == ["api.github.com"]
    finally:
        release.set()


def test_the_deadline_is_one_budget_across_the_redirect(server):
    """Time spent on the first request is time the second does not get. An injected clock jumps
    past the deadline while github.com answers; the redirect's request is then never made."""
    offset = [0.0]

    def clock():
        return time.monotonic() + offset[0]

    path = "/o/r/releases/download/v1.0.0/pam-setup-1.0.0.exe"
    redirecting = tls_server.redirect(ASSET)

    def slow_redirect(conn, request):
        offset[0] += 1000.0  # the first hop "took" 1000 s of a 60 s deadline
        redirecting(conn, request)

    server.route("github.com", path, slow_redirect)
    server.route("release-assets.githubusercontent.com", ASSET_PATH, tls_server.ok(b"MZ"))
    client = transport(server, clock=clock)
    with pytest.raises(DeadlineError):
        get(
            client,
            DOWNLOAD,
            deadline_s=60.0,
            redirect_to="release-assets.githubusercontent.com",
        )
    assert [r.host for r in server.requests] == ["github.com"]


def test_a_refused_connection_is_a_sentence_not_a_traceback():
    with socket.create_server(("127.0.0.1", 0)) as listener:
        port = listener.getsockname()[1]
    client = Transport(resolver=lambda h, p: [("127.0.0.1", port)], cafile=CA_FILE)
    with pytest.raises(FetchError, match="could not connect to api.github.com"):
        get(client, deadline_s=10.0)


def test_a_failed_lookup_is_a_sentence_naming_the_host():
    def failing(host, port):
        raise socket.gaierror(11001, "getaddrinfo failed")

    client = Transport(resolver=failing, cafile=CA_FILE)
    with pytest.raises(FetchError, match="could not find api.github.com"):
        get(client)


# --- Stage 1 review closures (M15 Task 12): each a survivor of the review's mutation pass --------


@pytest.mark.parametrize("code", [200, 404, 500])
def test_remaining_zero_on_any_other_status_is_not_the_rate_limit(server, code):
    """10B: ONLY a 403 or 429 carrying `x-ratelimit-remaining: 0` is the rate limit. GitHub sends
    the header on every API answer, so the last request an hour allows is a 200 with remaining 0,
    and it must be read (M4317)."""
    headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1700000000"}
    if code == 200:
        server.route(
            "api.github.com", "/repos/o/r/releases/latest", tls_server.ok(b"{}", headers)
        )
        assert get(transport(server)).body == b"{}"
        return
    server.route(
        "api.github.com", "/repos/o/r/releases/latest", tls_server.status(code, headers)
    )
    with pytest.raises(StatusError) as caught:
        get(transport(server))
    assert caught.value.status == code


def test_a_content_length_that_is_not_a_number_is_refused_in_a_sentence(server):
    """`http.client` silently ignores a Content-Length it cannot parse; pam refuses it in words,
    never with a traceback (M4321)."""
    answer = tls_server.ok(b"hello", {"Content-Length": "5x"})
    server.route("api.github.com", "/repos/o/r/releases/latest", answer)
    with pytest.raises(FetchError, match="sent a Content-Length pam cannot read"):
        get(transport(server))


def test_the_tcp_connect_is_armed_with_what_is_left_of_the_deadline(server, monkeypatch):
    """A connect that never completes (a black-holed address) is bounded too: the plain socket's
    timeout is set BEFORE `connect`, to no more than what is left of the overall deadline. Loopback
    cannot black-hole a SYN, so the test watches the call itself (M4324)."""
    armed = []

    class Watched(socket.socket):
        def connect(self, address):
            armed.append(self.gettimeout())
            return super().connect(address)

    server.route("api.github.com", "/repos/o/r/releases/latest", tls_server.ok(b"{}"))
    client = transport(server)
    monkeypatch.setattr(socket, "socket", Watched)
    get(client, deadline_s=5.0)
    assert len(armed) == 1 and armed[0] is not None and 0 < armed[0] <= 5.0, armed


def test_a_lookup_that_never_returns_does_not_keep_pam_running():
    """The lookup thread is a DAEMON: after the deadline is reported, a `getaddrinfo` that never
    returns must not hold the process open. Only a process's EXIT can show it, so a fresh
    interpreter (M4322)."""
    script = (
        "import threading\n"
        "from pam.update.fetch import DeadlineError, Transport\n"
        "hang = threading.Event()\n"
        f"client = Transport(resolver=lambda h, p: hang.wait() and [], cafile={str(CA_FILE)!r})\n"
        "try:\n"
        "    client.get('https://api.github.com/x', accept='a', max_bytes=1, deadline_s=0.5)\n"
        "except DeadlineError:\n"
        "    print('deadline')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
    )
    assert (result.returncode, result.stdout.strip()) == (0, "deadline"), result.stderr[-2000:]
