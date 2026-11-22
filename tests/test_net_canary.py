"""The privacy canary (spec 10, success criterion S4): pam runs and opens no socket.

WHAT THIS FILE IS. The product vision's first principle is that nothing leaves the
machine. `pam/net.py` constrains the one client pam will ever have, and the grep-gate
in `tests/test_net.py` constrains what may be imported - but both are statements about
SOURCE. This file is the statement about BEHAVIOUR: the whole mutating pipeline, over a
real-world library, with `socket.socket.connect` under a wrapper that records every call, and
every address in that recording must be on this machine.

WHY THAT IS DIFFERENT FROM THE GREP-GATE. A guard over imports cannot see a socket
opened by something pam calls rather than something pam imports - exiftool, a telemetry
hook somebody adds to a library, an SDK that phones home on first use. The pipeline
either dials out or it does not, and only running it can say which.

THE DEVIATION EXPIRED AT M10 TASK 7, AND THIS IS ITS ONE RELAXATION.
  From M6 to M9 this file asserted zero connection attempts of ANY kind, loopback
  included, because nothing pam shipped had any business opening a socket at all. M10 is
  the milestone in which pam acquires a loopback consumer - `pam doctor`'s Ollama probe
  and `pam models pull` in Task 7, `enrich/caption.py` in Task 8 - so the assertion is
  now spec 10's own: EVERY RECORDED ADDRESS MUST BE LOOPBACK.

  IT IS RELAXED TO THAT AND NEVER TO NOTHING. A non-loopback address is still a failure,
  and it is the only failure this file was ever really about; vision S4 stays literally
  true rather than nearly true. The trigger below was written in M9 naming exactly this
  edit, and the history is kept because the reasoning is what stops the next relaxation
  being an easier one.

  THE LOOPBACK TEST IS WRITTEN HERE, FROM `ipaddress`, AND NOT BORROWED FROM
  `pam/net.py`. Calling `net.resolve_loopback_address` would make this assertion depend
  on the correctness of the very code it exists to watch - the M9 note below already
  says so - and this file's whole value is being an independent second opinion about
  what really happened on the wire.

  WHAT THE DEVIATION USED TO SAY, kept because the reasoning is still the reasoning:
  Spec 10's wording is "asserting zero NON-LOOPBACK connection attempts". This file
  asserted zero connection attempts of ANY kind, loopback included.
  * WHY: at M6 nothing in pam has any business opening a socket at all. `net.py` has no
    consumer until M10, and a loopback connection appearing in this pipeline today would
    be something nobody intended - which is precisely what a canary is for. Narrowing to
    "non-loopback" now would buy nothing and would make the assertion depend on
    `net.py`'s own check being correct, which is a different test's job.
  * THIS NOTE USED TO SAY "EXPIRY: M9", AND THE TRIGGER WAS MIS-KEYED TO A MILESTONE
    NUMBER RATHER THAN TO A CAPABILITY. The reason given was always the right one -
    spec 10 names `enrich/caption.py` as `LoopbackHttpClient`'s only consumer, and once
    a local model server is in the pipeline a loopback connection is EXPECTED. But
    **M9 builds no caption enricher and no Ollama client**; its plan defers both to M10
    by name. Relaxing the assertion at M9 would buy nothing and would disarm the canary
    over a milestone that adds four dependencies, which is the worst possible moment.
    So the deviation stands, and the trigger now reads: **relax this when the first
    loopback consumer lands** - `enrich/caption.py`, M10 - and relax it to spec 10's
    wording (every recorded address must be loopback), never to nothing.
  * SPEC 10'S `enrich (stub models)` STAGE IS NOW IN THE SEQUENCE (M9 Task 12), AND IT
    IS NOT STUBBED. Spec 10 says "stub models" because it was written when the enrichers
    were expected to be model servers; M9's two are not. `phash` is Pillow, pillow-heif
    and imagehash decoding a real picture, and running the REAL enricher is strictly
    stronger than running a stub: a stub proves that pam's own loop dials nothing, which
    is the half the AST import guard already covers, while the whole reason this file
    exists is that "a guard over imports cannot see a socket opened by something pam
    CALLS rather than something pam imports". Three of M9's four new dependencies open
    that hole and only this stage closes it. The fourth - `reverse_geocoder`, the one
    that really does hold a `download.geonames.org` URL - is covered by the two dedicated
    tests at the bottom of this file, and is left out of the pipeline stage because
    loading its dataset costs more than it adds beside them. Measured: the stage adds
    ~0.5 s to this test.
    Adding a stage does not relax the alarm; see the trigger above.
  * THE STRICTER FORM WAS A DELIBERATE DEPARTURE, recorded here so the next reader did
    not have to guess, and the useful edit was always to change the assertion rather
    than to delete the note. M10 Task 7 made exactly that edit and no other.

WHAT M9 TASK 6 ADDED, AND WHY IT IS IN THIS FILE. M9 spends the runtime dependency
closure, and one of the four - `reverse_geocoder` - was chosen by design 1 for exactly
one reason: "No network." That is a claim about a third-party package, and the AST import
guard in `tests/test_net.py` cannot reach it: that guard walks `pam/`, and nothing under
`pam/` imports `reverse_geocoder` at all yet. `reverse_geocoder/__init__.py` DOES import
`urllib.request` and DOES hold a `download.geonames.org` URL, so the sentence needed
proving rather than trusting. The two tests at the bottom of this file are the proof.

WHAT THE WATCH SEES, AND NOTHING MORE (design 10 rev 28, P6 and Q6). It RECORDS AND
FORWARDS - it never refuses - and it keeps three records: CONNECTION ATTEMPTS
(`socket.socket.connect`, `connect_ex`), NAME LOOKUPS (`socket.getaddrinfo`,
`gethostbyname`, `gethostbyname_ex`, patched on the `socket` module) and DATAGRAM SENDS
(`socket.socket.sendto`, and `sendmsg` where the platform has it - Windows does not). Every
empty list in this file therefore means: no PYTHON-LEVEL connection, lookup or datagram send
on the paths the test exercised. It cannot see native code (an ONNX execution provider
dialling from C++) or a child process; the control for the native layer is the PROVIDER
ASSERTION, and the process-level capture is an HT step, never CI. A name bound by `from
socket import ...` would escape the module patch, so `pam/` may not bind one that way
(`test_no_pam_module_binds_a_socket_resolver_by_name`).

AND THE CANARY CARRIES ITS OWN PROOF OF SIGHT. A canary that cannot see is worse than
no canary at all, because it is believed: `record` doing nothing would leave
`attempts == []` for the best possible reason and the worst possible one, and the two
are indistinguishable from the assertion alone. So
`test_the_canary_records_a_real_outbound_connection` plants a real connection and
asserts the wrapper catches it. That test is the reason the empty list means something,
and deleting it silently disarms every other assertion in this file.
"""

import ipaddress
import pathlib
import shutil
import socket
import sys

import pytest
from archives import FAKE_COMMAND, approve_archive, materialise_tiny_library
from ollama_stub import OllamaStub, generate_reply, tags_reply
from PIL import Image

from pam.enrich.caption import CaptionEnricher
from pam.enrich.command import run_enrich
from pam.errors import PamNetworkError
from pam.execute.apply import run_apply
from pam.execute.undo import run_undo
from pam.naming.planner import run_plan
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan
from pam.verify import run_verify

# NARROWED FROM A `pytestmark` TO ONE DECORATOR (M9 Task 6). It used to be module-scoped,
# which skipped the whole file where exiftool is absent - INCLUDING the three tests that
# prove the canary can see and its alarm can fire, none of which touch exiftool. A guard
# that disarms silently on the machine without the tool is the failure mode, not the
# missing tool (CLAUDE.md, the `node --check` precedent). Only the pipeline test drives a
# real scan, so only the pipeline test needs the binary.
needs_exiftool = pytest.mark.skipif(
    shutil.which("exiftool") is None,
    reason="the canary runs the real pipeline over the tiny library, which needs exiftool",
)

# Both entry points to "open a TCP connection". `connect_ex` is the same syscall with
# the error returned instead of raised, and wrapping only `connect` - which is all spec
# 10 asks for - would leave a one-word bypass. Neither is more suspicious than the
# other, so both are recorded and the assertion does not care which one fired.
WATCHED_METHODS = ("connect", "connect_ex")

# M12 TASK 6 STEP 4 (rev 4), SPELLED FROM DESIGN 10 Q6 AND NOT FROM THE PLATFORM. The name
# lookups are patched ON THE `socket` MODULE, which is where `pam/net.py`'s `socket.getaddrinfo`
# and the stdlib's own `create_connection` both look them up. The datagram sends are patched on
# `socket.socket`, and the fixture installs only the names this platform HAS: `sendmsg` does not
# exist on Windows (`hasattr` is False on this machine's 3.14), and patching it anyway errored
# every watch-using test in this module - the measurement that blocked Step 4 before rev 4.
WATCHED_LOOKUP_FUNCTIONS = ("getaddrinfo", "gethostbyname", "gethostbyname_ex")
WATCHED_SEND_METHODS = ("sendto", "sendmsg")

# The sequence spec 10 names, pinned so that shortening it is a DELIBERATE edit.
# Review finding M450/M451: the canary's pipeline could be trimmed - drop `undo`, drop
# `verify` - and the whole suite stayed green, because every remaining assertion is
# about the stages that are still there and nothing said which stages there should be.
# A canary whose coverage can shrink silently is a canary that will; and `ruff --fix`
# is happy to delete the now-unused import for you, which is how it would shrink.
# `enrich` JOINED THIS TUPLE AT M9 TASK 12, when `pam enrich` came into existence - not
# at Task 6, where a stage stubbed to match a document would have been a canary watching
# nothing. It sits between `scan` and `plan`, which is spec 10's own order.
PIPELINE_STAGES = ("scan", "enrich", "plan", "apply", "undo", "verify")

# DESIGN 10 S4'S SEQUENCE, SPELLED FROM THE SPEC SENTENCE AND NOT FROM THE TUPLE ABOVE.
# `PIPELINE_STAGES` was its own only oracle until M9 Task 12's review: the pipeline body
# asserts `stages == list(PIPELINE_STAGES)`, so deleting a stage from the body AND from
# the tuple made the two agree about a shorter pipeline and left the whole suite green
# (M1381 - the `enrich` stage, and with it Pillow, pillow-heif and imagehash, gone from
# spec 10's watch at 2602 passed). That is this project's copy-pin lesson exactly: two
# artefacts that are each other's oracle agree perfectly about less and less, and a copy
# pin needs a floor written from the SPEC side.
#
# The sentence, verbatim from design 10: "a test wraps `socket.socket.connect` and runs
# scan -> enrich (stub models) -> plan -> apply -> undo -> verify". Six stages, in that
# order. A milestone that legitimately changes the sequence amends the spec and then
# edits BOTH tuples; nothing else can shorten the canary.
SPEC_10_SEQUENCE = ("scan", "enrich", "plan", "apply", "undo", "verify")


def is_loopback(address):
    """Is one recorded `connect` argument an address on this machine?

    SPELLED FROM SPEC 10 AND NOT FROM `pam/net.py`. The whole value of this file is
    being a second opinion, so it re-decides "loopback" from `ipaddress` rather than
    asking the module under watch.

    FAIL CLOSED ON ANYTHING IT DOES NOT UNDERSTAND. `socket.connect` accepts more than
    an `(address, port)` tuple - a string path for AF_UNIX, a four-tuple for IPv6, a
    bytes object - and every one of those is something nobody in pam meant to do. An
    unrecognised argument is therefore NOT loopback, so it trips the alarm rather than
    slipping through the classifier; a permissive default here would be the way the
    relaxation quietly becomes a relaxation to nothing.
    """
    if not isinstance(address, tuple) or not address:
        return False
    try:
        return ipaddress.ip_address(address[0]).is_loopback
    except (ValueError, TypeError):
        return False


def is_loopback_name(host):
    """Is one recorded LOOKUP argument a name that can only mean this machine? (Q6)

    `localhost`, compared under ASCII case-folding and EXACTLY - not `localhost.`, not
    `localhost.evil.example`, whose resolution is somebody else's DNS server's to answer - or a
    string that parses as a loopback IP literal. Anything else, `None` and `bytes` included, is
    NOT loopback, for `is_loopback`'s fail-closed reason.

    A SEPARATE CLASSIFIER, and `is_loopback` is deliberately UNCHANGED. A hostname inside a
    `connect` tuple is resolved natively, below the lookup wrapper, so `("localhost", 80)` in
    `attempts` must still trip the alarm; only a lookup - which this watch sees as a lookup -
    may be judged by name.
    """
    if not isinstance(host, str):
        return False
    if host.isascii() and host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def assert_only_loopback(watch):
    """The alarm over ALL THREE records: every entry must pass its own classifier (R6).

    `attempts` and `sends` by `is_loopback` (IP literals only), `lookups` by
    `is_loopback_name`. The consumer that legitimately talks to a loopback model server - the
    captioner - calls this, rather than filtering `attempts` by hand and staying blind to the
    other two lists.
    """
    judged = (
        ("connection attempts", watch.attempts, is_loopback),
        ("name lookups", watch.lookups, is_loopback_name),
        ("datagram sends", watch.sends, is_loopback),
    )
    for kind, recorded, classifier in judged:
        escaped = [entry for entry in recorded if not classifier(entry)]
        assert escaped == [], (
            f"pam made {kind} to somewhere off this machine. Vision S4 is absolute."
            f" Off-box {kind}: {escaped}. All {kind}: {recorded}"
        )


def assert_nothing_recorded(watch):
    """The alarm for a path that has no business touching the network AT ALL: all three
    records empty. Every test that asserts `watch.attempts == []` calls it as well (Q6)."""
    assert (watch.attempts, watch.lookups, watch.sends) == ([], [], []), (
        "pam recorded network activity on a path that should have none."
        f" Connection attempts: {watch.attempts}. Name lookups: {watch.lookups}."
        f" Datagram sends: {watch.sends}"
    )


def assert_every_connection_was_loopback(watch, stages):
    """The canary's ALARM, as a function, so that it can be proven able to go off.

    Review finding M449: the assertion below used to be written inline in the pipeline
    test, where weakening it to `assert watch.attempts is not None` left the entire
    suite green. The two positive controls prove the RECORDER can see; nothing proved
    the thing that consumes the recording could fail. A sensor wired to a disconnected
    alarm is the same defect one layer up, and it is the shape this whole file exists to
    refuse.

    So the alarm lives here, is called by the pipeline test, and is driven directly by
    `test_the_alarm_fires_when_it_should` with a watch that HAS recorded something and
    with a pipeline that stopped early. Both must raise.
    """
    assert stages == list(PIPELINE_STAGES), (
        "the canary did not run the whole spec 10 sequence: it covered"
        f" {stages} and must cover {list(PIPELINE_STAGES)}. Shrinking what the canary"
        " watches is a deliberate edit to PIPELINE_STAGES, never a silent one."
    )
    # SPEC 10'S OWN WORDING, and the one relaxation this file has ever had (M10 Task 7):
    # every recorded address must be loopback. NOT "the list may be non-empty" - the
    # addresses are filtered and what is left over is what fails. A rewrite that dropped
    # the filter and asserted something about the list's length or its type would be the
    # relaxation-to-nothing this file's docstring forbids, and
    # `test_the_alarm_fires_when_it_should` plants a non-loopback address to prove it has
    # not happened.
    escaped = [address for address in watch.attempts if not is_loopback(address)]
    assert escaped == [], (
        "pam opened a socket to somewhere off this machine during scan -> enrich ->"
        " plan -> apply -> undo -> verify. Vision S4 is absolute and spec 10 permits"
        " loopback only - and the enrich stage means this covers Pillow, pillow-heif"
        " and imagehash as well as pam's own code."
        f" Addresses dialled off-box: {escaped}. All addresses: {watch.attempts}"
    )
    # AND THE OTHER TWO RECORDS (M12 Task 6 Step 4, rev 4): a lookup of a non-loopback name
    # or a datagram to somewhere else is as much a leak as a connection.
    assert_only_loopback(watch)


class ConnectWatch:
    """What `socket` was asked to do on the network, while installed - THREE records (Q6).

    - `attempts`: every address `socket.socket.connect`/`connect_ex` was given. Its meaning
      is exactly what it was before M12, so every `watch.attempts == []` in this file still
      says what it always said.
    - `lookups`: the host argument of every `socket.getaddrinfo`, `gethostbyname` and
      `gethostbyname_ex` call.
    - `sends`: the destination of every `socket.socket.sendto` (and `sendmsg`, where the
      platform has it; `None` when a `sendmsg` names no address).

    Python-level calls only, on the paths a test exercises - never native code, never a child
    process (P6). A class rather than bare lists because `record*` are the single points a
    mutation has to defeat, and the proof-of-sight tests below point at exactly those points.
    Appending to a list is the whole implementation, so a reader can satisfy themselves that
    a recorded call cannot be lost.
    """

    def __init__(self):
        self.attempts = []
        self.lookups = []
        self.sends = []

    def record(self, address):
        self.attempts.append(address)

    def record_lookup(self, host):
        self.lookups.append(host)

    def record_send(self, address):
        self.sends.append(address)


def _wrap(original, watch):
    """The recording wrapper for `connect`/`connect_ex`. Records FIRST, then forwards.

    The lookup and send wrappers below follow the same two rules (record first, never
    refuse); each has its own function because "the first argument is the address" is
    wrong for all of them.

    THE ORIGINAL IS STILL CALLED. A wrapper that recorded and then refused would make
    the canary a mock: the pipeline would take its no-network path and this file would
    prove only that pam does not dial out when it cannot. Recording and forwarding means
    every assertion here is about what the real code really did.

    Order matters within the wrapper too. Forwarding first would lose the address of any
    connection that raised, and a refused connection to somewhere off-box is exactly as
    much of a leak (a DNS query, a SYN packet, a name in somebody's log) as a successful
    one.
    """

    def wrapper(sock, address, *args, **kwargs):
        watch.record(address)
        return original(sock, address, *args, **kwargs)

    return wrapper


def _wrap_lookup(original, watch):
    """The name-lookup wrapper: records the HOST, then forwards, whatever the call's shape.

    The host is the first positional argument, or `host=` for `getaddrinfo(host=..., ...)`;
    `None` is recorded when neither is present (and fails the alarm closed). The original's
    return value comes back and its exception propagates, both unchanged - a wrapper that
    swallowed a `gaierror` would change what the code under watch does.
    """

    def wrapper(*args, **kwargs):
        watch.record_lookup(args[0] if args else kwargs.get("host"))
        return original(*args, **kwargs)

    return wrapper


def _wrap_sendto(original, watch):
    """`sendto(data, address)` or `sendto(data, flags, address)`: the destination is the LAST
    positional argument, never the first (which is the payload)."""

    def wrapper(sock, *args, **kwargs):
        watch.record_send(args[-1] if args else kwargs.get("address"))
        return original(sock, *args, **kwargs)

    return wrapper


def _wrap_sendmsg(original, watch):
    """`sendmsg(buffers[, ancdata[, flags[, address]]])`: the destination is the FOURTH
    positional argument or `address=`, recorded as `None` when the call names none."""

    def wrapper(sock, *args, **kwargs):
        watch.record_send(args[3] if len(args) > 3 else kwargs.get("address"))
        return original(sock, *args, **kwargs)

    return wrapper


_SEND_WRAPPERS = {"sendto": _wrap_sendto, "sendmsg": _wrap_sendmsg}


@pytest.fixture()
def watch(monkeypatch):
    """A `ConnectWatch` installed over `socket` for the duration of one test.

    Installed through `monkeypatch` so that a failing assertion cannot leave the whole
    interpreter with a wrapped socket class - the rest of the suite would then be
    running against a patched `socket`, and the failure would be reported somewhere
    unrelated. `socket.socket` inherits its methods from the C base, so `monkeypatch`
    restores them by setting the inherited implementations back as class attributes;
    they are the same function objects, so behaviour is unchanged either way, and the
    teardown check below asserts every wrapper itself is gone.

    The send methods are installed ONLY where this platform has them (Q6): `sendmsg` is
    absent on Windows, and `test_the_send_wrappers_installed_are_exactly_what_this_platform_has`
    pins the filter.
    """
    watcher = ConnectWatch()
    for name in WATCHED_METHODS:
        monkeypatch.setattr(socket.socket, name, _wrap(getattr(socket.socket, name), watcher))
    for name in WATCHED_LOOKUP_FUNCTIONS:
        monkeypatch.setattr(socket, name, _wrap_lookup(getattr(socket, name), watcher))
    for name in WATCHED_SEND_METHODS:
        if hasattr(socket.socket, name):
            wrapped = _SEND_WRAPPERS[name](getattr(socket.socket, name), watcher)
            monkeypatch.setattr(socket.socket, name, wrapped)
    yield watcher


@pytest.fixture()
def library(tmp_path):
    """The spec 9.1 tiny library, in a scratch archive. See `archives.py` for what that
    involves; the short version is 106 files, seven of which git will not store."""
    return materialise_tiny_library(tmp_path / "a")


@needs_exiftool
def test_the_whole_pipeline_opens_no_socket_at_all(library, watch):
    """S4: scan -> plan -> apply -> undo -> verify, and not one connection attempt.

    THE PIPELINE IS ASSERTED TO HAVE DONE THE WORK, not merely to have returned. A
    canary over a run that failed on its first command would report zero connections
    truthfully and prove nothing at all, and the failure mode is realistic: `run_apply`
    with a plan id that no longer exists raises, and a `pytest.raises`-shaped mistake
    would swallow it.

    The counts are asserted as RELATIONSHIPS (every planned item was renamed, every
    rename was put back) rather than as the pinned numbers from spec 9.1. Those numbers
    are `tests/test_tiny_library.py`'s to own - restating them here would mean a
    deliberate change to the library breaks a privacy test, which would teach whoever
    hits it that this file is noise.
    """
    stages = []

    scan = run_scan(library)
    assert scan.errors == [] and scan.read > 0
    stages.append("scan")

    # `phash` only, and REAL. Spec 10's sequence names an enrich stage; M9's two shipped
    # enrichers are the pixel one and the offline-table one, and the pixel one is what
    # puts three brand-new third-party packages inside this watch for the first time.
    # `facts_written > 0` is the stage's proof of work, for the reason the docstring
    # gives: a canary over a run that did nothing reports zero connections truthfully and
    # proves nothing. `failed` is deliberately NOT asserted at zero - the tiny library's
    # HEIC files are containers with no coded image on purpose, and pinning their count
    # here would make a deliberate change to the library break a privacy test.
    enriched = run_enrich("phash", library)
    assert enriched.outcomes[0].result.facts_written > 0
    # AND THE STAGE'S SUBJECT, not only its work (M9 Task 12 review, M1383 - a survivor).
    # Swapping this call to `geocode` left the whole suite green: the tiny library's real
    # JPEGs carry GPS, so `facts_written > 0` is satisfied by the enricher that reads
    # CATALOG COLUMNS and opens no file at all. The reason this stage exists is that
    # `phash` is what puts Pillow, pillow-heif and imagehash inside the watch - an import
    # guard cannot see a socket opened by something pam CALLS - so the enricher that ran
    # is pinned by name, and a milestone that changes it does so on purpose.
    assert [o.name for o in enriched.outcomes] == ["phash"]
    stages.append("enrich")

    plan = run_plan(library)
    assert plan.plan_id is not None and plan.planned > 0
    stages.append("plan")

    approve_archive(library, plan.plan_id)  # F1: apply renames only what was approved
    applied = run_apply(plan.plan_id, library, confirm=lambda confirmation: True)
    assert (applied.renamed, applied.failed, applied.cancelled) == (plan.planned, 0, False)
    stages.append("apply")

    undone = run_undo(applied.session_id, library)
    assert (undone.restored, undone.failed) == (applied.renamed, 0)
    stages.append("undo")

    verified = run_verify(library)
    assert verified.exit_code == 0, [check.name for check in verified.results if check.status]
    stages.append("verify")

    assert_every_connection_was_loopback(watch, stages)


# M14 Task 8 Step 3: the page's chain. The review page reads, renames and undoes in pam's own
# process (U19), so its whole chain runs inside the watch: the server, the jobs it starts and the
# page's own requests, which are loopback HTTP. Four photographs, a Live Photo among them, with the
# stand-in exiftool's dates.
PAGE_CHAIN_FILES = {
    "A.JPG": "2018:01:01 10:00:00",
    "B.JPG": "2020:08:05 09:15:00",
    "IMG_1.HEIC": "2019:07:04 18:23:11",
    "IMG_1.MOV": "2019:07:04 18:23:11",
}
PAGE_CHAIN_STAGES = ("read", "rename", "undo")


def test_the_page_chain_read_rename_undo_opens_only_loopback_sockets(tmp_path, watch):
    """S4 over the page's chain (M14 Task 8 Step 3): `pam start`'s server bound to a folder pam
    has never read -> `Start reading` (the read job: scan and plan) -> `Accept all` -> Confirm ->
    the rename job -> the undo job, every step a real request to the real server over loopback.

    THE RUN IS ASSERTED TO HAVE DONE THE WORK (design 10 R4's floor): the read published a plan of
    four items, the rename job renamed four files, the undo put four back, and the folder holds
    its original names again - a chain that failed at its first request would record only
    loopback addresses truthfully and prove nothing. And the watch is asserted to have SEEN the
    page's own requests, so its silence about anything else means something. Then every record -
    connections, lookups, datagrams - is loopback (`assert_only_loopback`).
    """
    import threading

    from archives import put
    from test_webui_jobs import await_state
    from test_webui_rename import _quiet, call

    from pam.webui import launcher

    root = tmp_path / "photos"
    root.mkdir()
    for name, date in PAGE_CHAIN_FILES.items():
        put(root, name, date)
    original = sorted(PAGE_CHAIN_FILES)

    def on_disk():
        return sorted(p.name for p in root.iterdir() if not p.name.endswith(".json"))

    def ok(server, path, method="GET", body=None):
        reply = call(server, path, method, body)
        assert reply.status == 200, (method, path, reply.status, reply.body[:300])
        return reply.json()

    def finished(server, kind):
        snapshot = await_state(server, "done", "failed", "cancelled")
        assert (snapshot["kind"], snapshot["state"]) == (kind, "done"), snapshot
        return snapshot

    stages = []
    server = launcher.start_server(_quiet())
    server.exiftool_command = list(FAKE_COMMAND)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        launcher.bind(server, root)
        ok(server, "job/read", "POST", {})
        finished(server, "read")
        plan_id = server.plan_id
        assert ok(server, f"plan/{plan_id}/items?limit=200")["total"] == len(PAGE_CHAIN_FILES)
        stages.append("read")

        ok(
            server,
            f"plan/{plan_id}/decide-filter",
            "POST",
            {"action": "approve", "accept_all": True},
        )
        nonce = ok(server, f"plan/{plan_id}/confirmation")["nonce"]
        ok(server, f"plan/{plan_id}/apply", "POST", {"nonce": nonce, "folder_confirmed": True})
        renamed = finished(server, "rename")
        assert renamed["result"]["renamed"] == len(PAGE_CHAIN_FILES), renamed
        assert not set(on_disk()) & set(original), on_disk()
        stages.append("rename")

        session = renamed["session"]
        undo_nonce = ok(server, f"session/{session}")["nonce"]
        ok(server, f"session/{session}/undo", "POST", {"nonce": undo_nonce})
        undone = finished(server, "undo")
        assert undone["result"]["restored"] == len(PAGE_CHAIN_FILES), undone
        assert on_disk() == original
        stages.append("undo")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=30)

    assert tuple(stages) == PAGE_CHAIN_STAGES, stages
    # The watch SAW the page's requests (each is a loopback connection): its control here.
    assert watch.attempts, "the page's chain made no connection the watch could see"
    assert_only_loopback(watch)


# M15 Task 12 (E4): THE RELEASE CLIENT'S CANARY. `pam update` is pam's first non-loopback network
# code, and E4 says no command that touches an archive loads it. Measured in a FRESH interpreter
# (`update_canary_child.py`): this process imported `pam.update` long ago, so `sys.modules` here
# can say nothing. The commands are spelled from design 12's table, by hand.
ARCHIVE_COMMANDS_RUN = (
    "scan",
    "status",
    "enrich",
    "plan",
    "report skipped",
    "report duplicates",
    "apply",
    "report session",
    "undo",
    "verify",
    "pam",
    "forget",
)
assert ARCHIVE_COMMANDS_RUN, "the release-client canary must drive at least one command"


def _canary_child(tmp_path, mode):
    import json
    import os
    import subprocess

    env = {
        **os.environ,
        "PAM_HOME": str(tmp_path / "home"),
        "PAM_SCHEMA_LEDGER": str(tmp_path / "schema-ledger"),
        "PAM_RUN_REGISTRY": str(tmp_path / "run-registry"),
    }
    child = pathlib.Path(__file__).with_name("update_canary_child.py")
    result = subprocess.run(
        [sys.executable, str(child), mode, str(tmp_path / "archive")],
        input="apply\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_no_archive_command_opens_a_socket_or_loads_the_release_client(tmp_path):
    """E4 and S4 at once: every archive command, through `pam.cli.main`, in a fresh process -
    no connection, lookup or datagram, and `pam.update` never loaded, not even by `import pam.cli`.
    The chain did the work: every command exited 0 and the apply renamed both photographs."""
    seen = _canary_child(tmp_path, "archive")
    assert seen["after_import"] == [] and seen["after"] == []
    assert (seen["attempts"], seen["lookups"], seen["sends"]) == ([], [], [])
    renamed = seen["codes"].pop("renamed")
    assert renamed == 2
    assert seen["codes"] == dict.fromkeys(ARCHIVE_COMMANDS_RUN, 0)


def test_the_release_client_canary_sees_the_lazy_import_and_a_real_connection(tmp_path):
    """The canary's two controls, in the same fresh-process harness: `pam update --check` (the
    pip route in a venv - no request) DOES load `pam.update`, and one real loopback connection
    IS recorded, as are one name lookup and one loopback datagram. Without these the empty lists
    above could be a detector that sees nothing."""
    seen = _canary_child(tmp_path, "control")
    assert seen["after_import"] == []
    assert "pam.update.command" in seen["after"] and "pam.update.fetch" in seen["after"]
    assert seen["codes"] == {"update --check": 0}
    assert len(seen["attempts"]) == 1 and "127.0.0.1" in seen["attempts"][0]
    # Exactly the child's own probes: `pam update --check` itself looked up and sent nothing.
    assert seen["lookups"] == ["'localhost'"]
    assert len(seen["sends"]) == 1 and "127.0.0.1" in seen["sends"][0]


def test_the_canary_records_a_real_outbound_connection(watch):
    """THE PROOF OF SIGHT, and the reason the empty list above is evidence.

    Without this, `ConnectWatch.record` could be emptied to `pass` and the canary would
    stay green - green for the reason that a defect makes it green, which is the worst
    shape a safety test can take. This is the M6 plan's named mutation for Task 8,
    written as a test rather than left as a review activity, so it defends the canary on
    every run instead of once.

    A LISTENER ON 127.0.0.1, connected to for real. Loopback, so this test is honest
    about pam's own promise - nothing leaves the machine here either - and a real
    listener rather than a closed port so the connection SUCCEEDS: a refused connection
    would exercise the wrapper's error path instead of its ordinary one, and the
    ordinary one is what the canary above depends on. Port 0 lets the OS choose, so the
    test cannot collide with whatever else is running on this machine.
    """
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        # Read the address BEFORE the listener is closed: `getsockname` on a closed
        # socket raises WinError 10038, which cost the first draft of this test a run.
        address = listener.getsockname()
        client = socket.socket()
        try:
            client.connect(address)
        finally:
            client.close()
    finally:
        listener.close()

    assert watch.attempts == [address], (
        "the canary did not see a connection that really happened; every other"
        " assertion in this file is worthless until it does"
    )
    assert_only_loopback(watch)


def test_the_canary_also_sees_connect_ex(watch):
    """`connect_ex` is the same syscall with the error returned rather than raised.

    Wrapping only `connect` - which is all spec 10's wording asks for - would leave a
    one-word bypass, and a canary with a documented hole in it is a canary somebody will
    walk through by accident.
    """
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        address = listener.getsockname()
        client = socket.socket()
        try:
            assert client.connect_ex(address) == 0
        finally:
            client.close()
    finally:
        listener.close()

    assert watch.attempts == [address]
    assert_only_loopback(watch)


# --- M12 Task 6 Step 4 (rev 4): proof of sight for the lookup and send wrappers ----------
#
# One test per new wrapper, on `test_the_canary_also_sees_connect_ex`'s pattern, and LOOPBACK
# TARGETS ONLY: a test must never itself emit a query or a datagram off this machine. Each also
# proves FORWARDING - the original's answer really comes back - because a wrapper that
# recorded and returned nothing would make the canary a mock (`_wrap`'s docstring).


def test_the_canary_sees_getaddrinfo_and_forwards_its_answer(watch):
    """`socket.getaddrinfo`, positionally and as `host=` - both shapes are recorded."""
    records = socket.getaddrinfo("localhost", 0)
    by_keyword = socket.getaddrinfo(host="localhost", port=0)

    assert watch.lookups == ["localhost", "localhost"]
    answered = {record[4][0] for record in records + by_keyword}
    assert answered & {"127.0.0.1", "::1"}, records
    assert (watch.attempts, watch.sends) == ([], [])
    assert_only_loopback(watch)


def test_the_canary_sees_gethostbyname(watch):
    assert socket.gethostbyname("localhost") == "127.0.0.1"
    assert watch.lookups == ["localhost"]
    assert_only_loopback(watch)


def test_the_canary_sees_gethostbyname_ex(watch):
    assert "127.0.0.1" in socket.gethostbyname_ex("localhost")[2]
    assert watch.lookups == ["localhost"]
    assert_only_loopback(watch)


def test_the_canary_sees_sendto_and_the_datagram_arrives(watch):
    """`sendto`, in both its shapes, to a UDP receiver this test bound on 127.0.0.1.

    The recorded destination is the LAST positional argument; the first is the payload, and
    recording it would put `b"pam-canary"` where an address belongs. The receiver READING
    the payload is the forwarding proof.
    """
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(5.0)
        address = receiver.getsockname()
        sender.sendto(b"pam-canary", address)
        sender.sendto(b"pam-canary-flags", 0, address)
        got = {receiver.recvfrom(64)[0], receiver.recvfrom(64)[0]}
    finally:
        sender.close()
        receiver.close()

    assert got == {b"pam-canary", b"pam-canary-flags"}
    assert watch.sends == [address, address]
    assert_only_loopback(watch)


@pytest.mark.skipif(
    not hasattr(socket.socket, "sendmsg"),
    reason="socket.socket.sendmsg does not exist on this platform (Windows), so there is"
    " nothing to wrap - design 10 Q6",
)
def test_the_canary_sees_sendmsg_where_the_platform_has_it(watch):
    """`sendmsg`'s destination is its fourth positional argument. POSIX only."""
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(5.0)
        address = receiver.getsockname()
        sender.sendmsg([b"pam-canary"], [], 0, address)
        got = receiver.recvfrom(64)[0]
    finally:
        sender.close()
        receiver.close()

    assert got == b"pam-canary"
    assert watch.sends == [address]
    assert_only_loopback(watch)


def test_the_wrappers_forward_the_originals_exception_unchanged(watch):
    """A wrapper that swallowed an error would change what the code under watch does.

    Two real failures, both local: a service name no machine knows (a `gaierror` from the
    lookup wrapper - `localhost` never reaches a DNS server), and a datagram from a socket
    that is already closed (the send wrapper). Each is recorded FIRST and then arrives.
    """
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("localhost", "no-such-service-pam")
    closed = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    closed.close()
    with pytest.raises(OSError):
        closed.sendto(b"pam-canary", ("127.0.0.1", 9))

    assert watch.lookups == ["localhost"]
    assert watch.sends == [("127.0.0.1", 9)]
    assert_only_loopback(watch)


def test_the_send_wrappers_installed_are_exactly_what_this_platform_has(watch):
    """The `hasattr` filter, pinned (Q6) - and it can never empty the set.

    What is INSTALLED is read off `socket.socket` itself, over a wider hand-written list than
    `WATCHED_SEND_METHODS`, so the expectation is not the fixture's own loop read back.
    """
    installed = {
        name
        for name in ("send", "sendall", "sendto", "sendmsg", "sendfile")
        if getattr(socket.socket.__dict__.get(name), "__name__", "") == "wrapper"
    }
    assert installed == {n for n in WATCHED_SEND_METHODS if hasattr(socket.socket, n)}
    assert "sendto" in installed, "the datagram watch installed no sendto wrapper"
    if sys.platform == "win32":
        assert not hasattr(socket.socket, "sendmsg"), (
            "Windows now has socket.sendmsg - Q6's sentence about the platform is stale"
        )
        assert "sendmsg" not in installed
    assert_nothing_recorded(watch)


def test_the_lookup_classifier_answers_a_hand_written_table():
    """`is_loopback_name`, both halves, spelled from Q6 and never from `pam/net.py`."""
    for host in ("localhost", "LOCALHOST", "LocalHost", "127.0.0.1", "127.8.9.10", "::1"):
        assert is_loopback_name(host) is True, host
    for host in (
        None,
        b"localhost",
        "",
        "localhost.",
        "localhost.evil.example",
        "evil.localhost",
        " localhost",
        "localhost:80",
        "example.invalid",
        "203.0.113.7",
        "192.168.1.50",
        "0.0.0.0",
        "::",
        "fe80::1",
        "127.1",
        "locałhost",
        "ｌｏｃａｌｈｏｓｔ",
        11434,
        ("localhost", 80),
    ):
        assert is_loopback_name(host) is False, host


# The names whose binding by `from socket import ...` would escape the module patch (Q6). Spelled
# from the plan's Step 4 item 7, by hand.
_BANNED_SOCKET_NAMES = frozenset(
    {
        "getaddrinfo",
        "gethostbyname",
        "gethostbyname_ex",
        "create_connection",
        "connect",
        "sendto",
        "sendmsg",
    }
)


def _socket_bindings(source):
    """Every banned `from socket import X [as Y]` (and `import *`) in one module's source."""
    import ast

    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module == "socket" and node.level == 0:
            found += [a.name for a in node.names if a.name in _BANNED_SOCKET_NAMES | {"*"}]
    return found


def test_no_pam_module_binds_a_socket_resolver_by_name():
    """THE BLIND SPOT, CLOSED (Q6): a name bound by `from socket import ...` escapes the patch.

    `monkeypatch.setattr(socket, "getaddrinfo", ...)` replaces the MODULE ATTRIBUTE; a module
    that did `from socket import getaddrinfo` holds the original function object and would
    resolve names this watch never sees. So no module under `pam/` may bind any of the names
    above that way, whatever the `as` alias, and never with `import *`. `import socket` - what
    `pam/net.py` does - is fine.

    WHAT THIS DOES NOT COVER: a DEPENDENCY's own aliases, and native resolution. Those are
    below the Python watch altogether (P6).
    """
    # The detector can see, before it is trusted with the tree.
    assert _socket_bindings("from socket import getaddrinfo") == ["getaddrinfo"]
    assert _socket_bindings("from socket import getaddrinfo as g") == ["getaddrinfo"]
    assert _socket_bindings("from socket import *") == ["*"]
    assert _socket_bindings("import socket\nfrom socket import AF_INET") == []

    root = pathlib.Path(__file__).resolve().parent.parent / "pam"
    modules = sorted(root.rglob("*.py"))
    assert len(modules) > 50, f"the walk found only {len(modules)} modules under {root}"
    offenders = {
        str(path.relative_to(root)): names
        for path in modules
        if (names := _socket_bindings(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}, (
        f"a pam module binds a socket name directly, escaping the privacy canary: {offenders}"
    )


def test_every_watch_consumer_calls_an_alarm():
    """THE WIRING GUARD (rev 4.1, R6): every test that takes `watch` calls one of the alarms.

    An AST walk of this module. The consumers it finds must equal a HAND-WRITTEN tuple, so a
    new consumer that forgets its alarm fails here, and so does a deleted one. It enumerates
    CONSUMERS, not empty-list assertions: the captioning stage asserted a NON-empty list and
    filtered it by hand, and a guard over `== []` would never have reached it.
    """
    import ast

    alarms = {
        "assert_nothing_recorded",
        "assert_only_loopback",
        "assert_every_connection_was_loopback",
    }
    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    consumers, silent = set(), set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        if "watch" not in [arg.arg for arg in node.args.args]:
            continue
        consumers.add(node.name)
        called = {
            call.func.id
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }
        if not called & alarms:
            silent.add(node.name)

    assert consumers, "the walk found no test taking the watch fixture"
    assert silent == set(), f"watch consumers that call no alarm: {sorted(silent)}"
    assert consumers == set(WATCH_CONSUMERS), (
        f"new, unlisted: {sorted(consumers - set(WATCH_CONSUMERS))};"
        f" listed, gone: {sorted(set(WATCH_CONSUMERS) - consumers)}"
    )


# Every test in this module that takes the `watch` fixture, BY HAND (R6).
WATCH_CONSUMERS = (
    "test_the_whole_pipeline_opens_no_socket_at_all",
    "test_the_canary_records_a_real_outbound_connection",
    "test_the_canary_also_sees_connect_ex",
    "test_the_canary_sees_getaddrinfo_and_forwards_its_answer",
    "test_the_canary_sees_gethostbyname",
    "test_the_canary_sees_gethostbyname_ex",
    "test_the_canary_sees_sendto_and_the_datagram_arrives",
    "test_the_canary_sees_sendmsg_where_the_platform_has_it",
    "test_the_wrappers_forward_the_originals_exception_unchanged",
    "test_the_send_wrappers_installed_are_exactly_what_this_platform_has",
    "test_reverse_geocoder_imports_and_answers_a_query_without_opening_a_socket",
    "test_the_faces_loader_refuses_a_missing_pack_without_opening_a_socket",
    "test_the_faces_loader_refuses_a_corrupt_pack_without_opening_a_socket",
    "test_loading_a_real_model_pack_opens_no_socket_at_all",
    "test_the_enrich_stage_WITH_FACES_ON_opens_no_socket_at_all",
    "test_the_doctor_probe_refuses_an_off_box_host_without_dialling_it",
    "test_pam_models_pull_refuses_an_off_box_host_without_dialling_it",
    "test_a_host_answering_both_loopback_and_off_box_is_refused_before_any_connection",
    "test_the_probe_really_does_connect_when_the_address_is_loopback",
    "test_the_captioner_refuses_an_off_box_host_without_dialling_it",
    "test_a_captioner_host_answering_LOOPBACK_AND_OFF_BOX_is_refused_before_any_connection",
    "test_the_captioner_really_does_connect_when_the_address_is_loopback",
    "test_the_enrich_stage_WITH_CAPTIONING_ON_opens_only_loopback_sockets",
    # M12 Task 12 Part B (B12): the OCR engine, cold, under the watch.
    "test_the_ocr_engine_loads_and_reads_without_a_python_socket",
    # M14 Task 8 Step 3: the page's chain (read -> rename -> undo) under the watch.
    "test_the_page_chain_read_rename_undo_opens_only_loopback_sockets",
)
assert WATCH_CONSUMERS, "the watch-consumer list is empty; it protects nothing"


def test_the_alarm_fires_when_it_should():
    """THE PROOF THE ALARM IS CONNECTED, which is a different claim from proof of sight.

    `test_the_canary_records_a_real_outbound_connection` proves `ConnectWatch` SEES.
    This proves the assertion that reads it can FAIL - review finding M449, where the
    inline assertion could be weakened to `assert watch.attempts is not None` with the
    whole suite still green, because nothing in the run plants a socket inside the
    pipeline to tell the strict form from the vacuous one.

    Driven directly rather than through the pipeline, deliberately: the pipeline is a
    ~15 s real-exiftool run and cannot be made to dial out without wiring a leak into
    production code. The alarm is a pure function of (what was recorded, what ran), so
    it can be handed both failure shapes for free.

    Also covers M450/M451: a canary that quietly stopped running `undo` or `verify` must
    fail, and the only way to shorten it is to edit `PIPELINE_STAGES` on purpose.
    """
    leaked = ConnectWatch()
    leaked.record(("203.0.113.7", 443))
    with pytest.raises(AssertionError, match="pam opened a socket"):
        assert_every_connection_was_loopback(leaked, list(PIPELINE_STAGES))

    with pytest.raises(AssertionError, match="did not run the whole spec 10 sequence"):
        assert_every_connection_was_loopback(ConnectWatch(), ["scan", "plan", "apply"])

    # M10 TASK 7: THE RELAXATION, PROVEN NOT TO BE A RELAXATION TO NOTHING. A loopback
    # connection is now allowed - that is the whole edit - and one non-loopback address
    # ANYWHERE in the list still fails, even mixed in with loopback ones. Without this
    # pair, rewriting the alarm to `assert True` or to `assert watch.attempts is not
    # None` would pass every assertion in this file.
    loopback_only = ConnectWatch()
    loopback_only.record(("127.0.0.1", 11434))
    loopback_only.record(("::1", 11434, 0, 0))
    assert_every_connection_was_loopback(loopback_only, list(PIPELINE_STAGES))

    mixed = ConnectWatch()
    mixed.record(("127.0.0.1", 11434))
    mixed.record(("203.0.113.7", 443))
    with pytest.raises(AssertionError, match="203.0.113.7"):
        assert_every_connection_was_loopback(mixed, list(PIPELINE_STAGES))

    # M10 TASK 7 REVIEW, FINDING F2 (M1530). The plants above are a public/TEST-NET
    # address and shapes the classifier cannot parse, and those were ALL of them: widening
    # `is_loopback` to accept the whole of `192.168.0.0/16` left the entire suite green,
    # so the canary would have watched pam dial another machine in the same house and said
    # nothing. Vision S4 is "nothing leaves the machine", and a LAN address is somebody
    # else's machine. Written out here as literals rather than shared with
    # `tests/test_net.py`'s list ON PURPOSE: this file is the second opinion, and a shared
    # constant would let one edit move both readings together (M922/M924).
    for off_box in (
        "192.168.1.50",
        "10.0.0.5",
        "172.16.0.1",
        "100.64.0.1",
        "169.254.1.1",
        "0.0.0.0",
        "fd00::1",
        "fe80::1",
    ):
        lan = ConnectWatch()
        lan.record((off_box, 11434))
        with pytest.raises(AssertionError, match="pam opened a socket"):
            assert_every_connection_was_loopback(lan, list(PIPELINE_STAGES))

    # And anything the classifier does not understand fails closed: an AF_UNIX path, a
    # bare hostname, an empty tuple. Nothing in pam means to do any of these, and a
    # permissive default is how "every address is loopback" quietly becomes "every
    # address pam happened to format the way I expected".
    for odd in ("/tmp/sock", ("localhost", 80), (), b"\x00", None):
        strange = ConnectWatch()
        strange.record(odd)
        with pytest.raises(AssertionError, match="pam opened a socket"):
            assert_every_connection_was_loopback(strange, list(PIPELINE_STAGES))

    # M12 TASK 6 STEP 4 (rev 4): THE OTHER TWO RECORDS, planted directly into a fresh
    # watch's list and never performed - a test must not itself emit a query or a datagram
    # off this machine. Each must trip the pipeline's alarm AND the shared one the captioning
    # stage calls.
    off_box_lookup = ConnectWatch()
    off_box_lookup.record_lookup("example.invalid")
    off_box_send = ConnectWatch()
    off_box_send.record_send(("203.0.113.7", 9))
    for planted, kind in ((off_box_lookup, "name lookups"), (off_box_send, "datagram sends")):
        with pytest.raises(AssertionError, match=f"off this machine.*Off-box {kind}"):
            assert_every_connection_was_loopback(planted, list(PIPELINE_STAGES))
        with pytest.raises(AssertionError, match=f"Off-box {kind}"):
            assert_only_loopback(planted)
    # And the empty-list alarm fires on each record ALONE, so none of its three operands is
    # decorative.
    for planted in (leaked, off_box_lookup, off_box_send):
        with pytest.raises(AssertionError, match="should have none"):
            assert_nothing_recorded(planted)
    loopback_everywhere = ConnectWatch()
    loopback_everywhere.record(("127.0.0.1", 11434))
    loopback_everywhere.record_lookup("localhost")
    loopback_everywhere.record_send(("127.0.0.1", 9))
    assert_only_loopback(loopback_everywhere)

    # And the honest case passes, so the cases above are not passing because the function
    # raises unconditionally.
    assert_every_connection_was_loopback(ConnectWatch(), list(PIPELINE_STAGES))
    assert_nothing_recorded(ConnectWatch())


def test_the_canary_covers_every_stage_spec_10_names():
    """The floor under `PIPELINE_STAGES`, written from design 10 rather than from it.

    M1381: `PIPELINE_STAGES` and the pipeline body are each other's oracle, so a stage
    deleted from both is invisible - the `stages == list(PIPELINE_STAGES)` assertion
    passes over a canary that has quietly stopped watching an entire command. Compared as
    an ORDERED tuple in both directions: order is spec 10's own (`enrich` sits between
    `scan` and `plan`, which is where a real user meets it), and an equality catches an
    unshipped stage added here as loudly as a shipped one dropped.
    """
    assert PIPELINE_STAGES == SPEC_10_SEQUENCE, (
        "the canary no longer covers design 10 S4's sequence. Shrinking it is a spec"
        " amendment, not an edit to this file: it covers"
        f" {list(PIPELINE_STAGES)} and spec 10 names {list(SPEC_10_SEQUENCE)}"
    )


def test_the_watch_is_uninstalled_when_a_test_finishes():
    """The tripwire on the tripwire: no test above may leak its wrapper into the suite.

    A `socket.socket.connect` left wrapped would record every connection every later
    test makes into a `ConnectWatch` nobody reads, and - worse - would survive a failing
    assertion in the canary, so the first symptom would be an unrelated test behaving
    oddly. This runs without the `watch` fixture, so if `monkeypatch` teardown ever
    stops restoring the class this test is the one that says so.
    """
    for name in WATCHED_METHODS:
        method = socket.socket.__dict__.get(name, getattr(socket.socket, name))
        assert getattr(method, "__name__", "") != "wrapper", (
            f"socket.socket.{name} is still wrapped by this module's canary"
        )
    # M12 Task 6 Step 4 (rev 4): every new wrapper as well.
    for name in WATCHED_LOOKUP_FUNCTIONS:
        assert getattr(getattr(socket, name), "__name__", "") != "wrapper", (
            f"socket.{name} is still wrapped by this module's canary"
        )
    for name in WATCHED_SEND_METHODS:
        method = socket.socket.__dict__.get(name, getattr(socket.socket, name, None))
        assert getattr(method, "__name__", "") != "wrapper", (
            f"socket.socket.{name} is still wrapped by this module's canary"
        )


# --- M9 Task 6: the offline geocoder, proven offline ---------------------------------
#
# Design 1 sanctioned `reverse_geocoder` for one reason - "No network" - and the M9 plan
# says to be suspicious of it and prove it rather than trust it. Reading the package is
# what raised the suspicion: `reverse_geocoder/__init__.py` imports `urllib.request` at
# module scope and holds `GN_URL = 'http://download.geonames.org/export/dump/'`. Both
# tests below are needed, because they answer different questions.

# 51.5215 N, 0.1730 W - Bayswater, London. A real land coordinate, because a query that
# resolves to nothing could take a shorter path than one that resolves to a city.
_LONDON = (51.5214588, -0.1729636)


def test_reverse_geocoder_imports_and_answers_a_query_without_opening_a_socket(watch):
    """S4, behaviourally, for the one dependency design 1 chose FOR being offline.

    The import and the first query are both inside the watch, and they are different
    events: the dataset is not touched at import time - it is read lazily, on the first
    `search` - so a canary that only imported would have watched the cheap half.

    `mode=1` IS NOT AN OPTIMISATION AND IS NOT COSMETIC. The default (`mode=2`) fans the
    query out over `multiprocessing`, and a child process gets a FRESH interpreter with
    an unwrapped `socket` - so a connection opened in a child would be invisible to this
    watch, and the assertion would be green for the wrong reason. Single-process keeps
    every syscall inside the recorder. (It is also the mode M9 Task 9 must use for an
    unrelated and more alarming reason: driven from a module `multiprocessing` re-imports
    on Windows, `mode=2` returns SILENTLY WRONG place names rather than raising. That
    cross-reference used to point at a "sibling note" in `tests/test_deps.py` which was
    never written - the M9 Task 6 review reproduced the defect and wrote it, under
    section 4 of that file, WITH AN AST GUARD BESIDE IT so `pam/` cannot reach the
    dangerous form by accident.)
    """
    import reverse_geocoder

    results = reverse_geocoder.search([_LONDON], mode=1)

    # The query is asserted to have really answered. A `search` that returned an empty
    # list would open no socket either, and would prove nothing about the code path a
    # user's photograph takes.
    assert results and results[0]["cc"] == "GB" and results[0]["name"]
    assert watch.attempts == [], (
        "reverse_geocoder opened a socket while answering a query. Design 1 chose it for"
        " being offline and vision S4 is absolute: nothing leaves the machine."
        f" Addresses dialled: {watch.attempts}"
    )
    assert_nothing_recorded(watch)


def test_the_geocoder_dataset_ships_inside_the_package_so_the_download_branch_is_dead():
    """The structural half, and it is what makes the behavioural half hold TOMORROW.

    `RGeocoder.extract()` downloads `GN_URL` **only when the local dataset file is
    absent** - `if os.path.exists(local_filename): ... else: urlretrieve(...)`. So the
    test above passes for a reason that lives on disk, and a wheel or a mirror that
    shipped without the CSV would turn pam's first geocode into an HTTP download to
    geonames.org, in plaintext, writing a zip into the user's current directory. The run
    above would still be green; it would just be green on a machine that already has the
    file.

    This is the "the suite tests the SOURCE TREE; users install the ARTEFACT" lesson
    pointed at a dependency instead of at our own package data: the guarantee is a file
    being present, so the file being present is the thing to assert. `pam doctor` carries
    the same check for the user's machine, which is where it actually matters.
    """
    import reverse_geocoder

    dataset = pathlib.Path(reverse_geocoder.__file__).parent / reverse_geocoder.RG_FILE
    assert dataset.is_file(), (
        f"reverse_geocoder's offline dataset is missing ({dataset}). Its first query"
        f" would DOWNLOAD it from {reverse_geocoder.GN_URL}, which breaks vision S4."
    )
    # Named for what it rules out: a zero-byte or placeholder file exists just as hard as
    # a real one, and the download branch would not fire for it either - the query would
    # simply fail or answer wrongly. The real dataset is ~7.8 MB.
    assert dataset.stat().st_size > 1_000_000, f"{dataset} is too small to be the dataset"


# --- the narrowing, pinned ------------------------------------------------------------

# Exactly which tests in this module may be skipped when exiftool is absent, written out
# by hand from the reason rather than read back from the module's own marks. Only the
# pipeline test drives a real scan; nothing else here touches the binary.
#
# WHY THIS IS PINNED AT ALL (M9 Task 6 review, M1176). Task 6 narrowed a module-level
# `pytestmark` to this one decorator, and the measurement says the narrowing is worth
# real coverage: with exiftool removed from PATH the shipped tree runs 6 and skips 1,
# while the module-scoped form skips ALL SEVEN - the three proof-of-sight tests and both
# `reverse_geocoder` S4 tests included. But putting it BACK is invisible on any machine
# that has exiftool, which is this one and both CI lanes: the full suite stayed at
# 2309 passed with the narrowing reverted. A fix no test can see is a regression waiting
# to happen, so the fix gets the test.
EXIFTOOL_GATED_TESTS = {"test_the_whole_pipeline_opens_no_socket_at_all"}

# M12 Task 6 Step 4 (rev 4.1, R7): the ONE test gated on the PLATFORM rather than on exiftool -
# `sendmsg` does not exist on Windows (design 10 Q6), so its proof of sight skips there. Written
# by hand, like the set above, and the guard below extends to it rather than being bypassed.
PLATFORM_GATED_TESTS = {"test_the_canary_sees_sendmsg_where_the_platform_has_it"}


# M10 TASK 7 REVIEW, FINDING F3 (M1534). This file's own docstring says that deleting
# `test_the_canary_records_a_real_outbound_connection` "silently disarms every other
# assertion in this file" - and that sentence was an untested test: the deletion left the
# whole suite green. It is the third time this file has been found able to shrink without
# saying so (M449, the alarm inlined and weakenable; M450/M451, `PIPELINE_STAGES`), and
# the answer both previous times was a hand-written floor, so it is the answer here.
#
# WRITTEN OUT BY HAND, WITH THE REASON PER ENTRY, and never read back from the module -
# a set built from `vars(module)` would contain whatever survived and would assert that
# the file matches itself. Deleting a control is then a deliberate edit to this list,
# which is what the two earlier repairs bought and this one extends to the controls.
def test_the_faces_loader_refuses_a_missing_pack_without_opening_a_socket(watch, tmp_path):
    """M11 A1: THERE IS NO CODE IN PAM THAT COULD FETCH A MODEL, measured rather than said.

    This is the strong form of the claim, and it is why the milestone deleted a dependency
    instead of configuring one. Rev 1 of the M11 plan proposed disabling `insightface`'s
    download through constructor flags THAT DO NOT EXIST, so its argument that the download
    path was unreachable was false and vision S4 rested on it. The replacement is a loader
    built from local paths with nothing in it that speaks a protocol - and the difference
    between "the download is disabled" and "there is no download" is exactly what a socket
    watch can see and a code review cannot.

    THE STATE UNDER TEST IS THE COMMONEST ONE: no pack. That is the moment a downloader
    would fire, if there were one. `describe` must answer with a sentence and no
    connection; the empty list is the assertion, and the fixture's own proof-of-sight tests
    are what make an empty list evidence rather than an absence of instrumentation.
    """
    from pam.enrich import faces_model

    for where in ("", str(tmp_path / "not-a-pack"), str(tmp_path)):
        ok, sentence = faces_model.describe(where)
        assert ok is False, where
        assert sentence
    assert watch.attempts == [], watch.attempts
    assert_nothing_recorded(watch)


def test_the_faces_loader_refuses_a_corrupt_pack_without_opening_a_socket(watch, tmp_path):
    """The same claim over the OTHER refusal, and it is a different code path.

    A pack whose bytes no longer hash to its manifest is the state in which a product that
    could fetch would fetch - "your model is damaged, re-downloading". pam's answer is a
    sentence, and the watch is what proves there is no second half to it.
    """
    from pam.enrich import faces_model

    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / faces_model.DETECTOR_FILE).write_bytes(b"detector")
    (pack / faces_model.RECOGNIZER_FILE).write_bytes(b"recognizer")
    faces_model.write_manifest(pack)
    (pack / faces_model.DETECTOR_FILE).write_bytes(b"something else")
    ok, sentence = faces_model.describe(pack)
    assert ok is False and "hash" in sentence
    assert watch.attempts == [], watch.attempts
    assert_nothing_recorded(watch)


@pytest.mark.faces
def test_loading_a_real_model_pack_opens_no_socket_at_all(watch):
    """The same claim with the REAL 37 MB pack and the real runtime behind it (A5).

    The two tests above prove pam's own code dials nothing. This one is about the
    DEPENDENCY, which is the layer spec 10's AST guard cannot reach at all: a stock
    `onnxruntime` wheel offers `AzureExecutionProvider`, that provider executes the model
    against a REMOTE endpoint, and `InferenceSession(path)` with no `providers=` takes
    whatever is offered. `pam/enrich/faces_model.py` passes the list explicitly; this is
    the measurement that the resulting session really keeps to the machine.

    Marked, because it needs the extra and the pack. `tests/test_faces_model.py` carries a
    fake-runtime twin that never skips and asserts the ARGUMENT, so the property is
    measured on every push and the honouring of it is measured here.

    BOTH GRAPHS ARE EXECUTED, AND THAT HALF WAS ADDED BY TASK 8's STAGE 2 (its finding
    F1). Until then every S4 measurement over the real face stack was about session
    CONSTRUCTION: this test loaded two sessions and never ran either, and no other test in
    the repository put a `.run()` under the watch at all. But an execution provider is a
    thing that RUNS a graph - `AzureExecutionProvider` would dial when the graph is
    executed, not when it is built - so the layer this test exists for was the one layer it
    did not reach. Construction and inference are two different moments and only one of
    them was watched.

    The tensors are zeros of the pack's own declared shapes because nothing here is about
    the ANSWER; `tests/test_faces_enricher.py` owns what the graphs compute. What matters
    is that the real weights, the real provider and the real runtime all execute with the
    recorder installed.
    """
    import numpy
    from faces_stack import model_dir

    from pam.enrich import faces_model

    sessions = faces_model.load(model_dir())
    assert sessions.detector.get_providers() == ["CPUExecutionProvider"]
    assert sessions.recognizer.get_providers() == ["CPUExecutionProvider"]

    detector_w, detector_h = faces_model.DETECTOR_INPUT
    detections = sessions.detector.run(
        None,
        {
            faces_model.DETECTOR_INPUT_NAME: numpy.zeros(
                (1, 3, detector_h, detector_w), dtype=numpy.float32
            )
        },
    )
    recognizer_w, recognizer_h = faces_model.RECOGNIZER_INPUT
    embedding = sessions.recognizer.run(
        [faces_model.RECOGNIZER_OUTPUT_NAME],
        {
            faces_model.RECOGNIZER_INPUT_NAME: numpy.zeros(
                (1, 3, recognizer_h, recognizer_w), dtype=numpy.float32
            )
        },
    )
    # Proof of WORK, for the reason this file's pipeline test gives: a canary over a run
    # that did nothing reports zero connections truthfully and proves nothing at all.
    assert detections and numpy.asarray(detections[0]).size > 0
    assert numpy.asarray(embedding[0]).reshape(-1).shape == (128,)

    assert watch.attempts == [], watch.attempts
    assert_nothing_recorded(watch)


# The face fixture `tests/test_faces_enricher.py` already runs real inference on, reused so the
# faces stage adds no photograph to the repository (M12 Task 6 Step 1).
_FACES_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "faces_parity.png"


@pytest.mark.faces
def test_the_enrich_stage_WITH_FACES_ON_opens_no_socket_at_all(tmp_path, watch):
    """C9 (M12 Task 6): THE CANARY'S `enrich` STAGE, WALKED WITH THE FACES PIPELINE.

    Until this row, S4 for `pam enrich faces` rested on one hand measurement and on
    `test_loading_a_real_model_pack_opens_no_socket_at_all`, which runs the two sessions but
    NOT THE COMMAND - M1670's situation before M1670. So the real commands run here, through
    the CLI runner and the real config, over a copy of the tiny library plus the committed
    face fixture: `pam scan` (real exiftool - the faces job installs it for this row), then
    `pam enrich faces` on the real pack, then `pam faces cluster`.

    PROOF OF WORK, for this file's standing reason - a watch over a run that did nothing
    reports zero connections truthfully and proves nothing: at least one `face_instances`
    row, read with the suite's own SQL, and the cluster command's summary line.

    WHAT THE EMPTY LIST PROVES, AND NOTHING MORE (design 10 rev 27, P6). It shows no
    Python-level call through what `ConnectWatch` wraps happened on the paths this run
    exercised. It CANNOT see a native library or a child process: an ONNX execution provider
    dialling from C++ would not appear here. The control for that native layer is the
    PROVIDER ASSERTION (`faces_model.load` refusing any session whose `get_providers()` is not
    CPU-only, and `test_a_real_session_really_runs_on_the_cpu_provider_only`); the process-level
    capture is an HT step and never CI.

    `cluster_epsilon` is set in this test's own `pam.toml` to an arbitrary value: the test is
    about sockets, not grouping, and design 11 leaves the key unset on purpose.
    """
    import sqlite3

    from click.testing import CliRunner
    from faces_stack import model_dir

    from pam.cli import cli

    root = materialise_tiny_library(tmp_path / "a")
    shutil.copyfile(_FACES_FIXTURE, root / "faces_parity.png")

    scanned = CliRunner().invoke(cli, ["scan", str(root)])
    assert scanned.exit_code == 0, scanned.output
    ws = workspace_if_exists(root)
    assert ws is not None
    ws.config_path.write_text(
        "[enrich.faces]\n"
        "enabled = true\n"
        f"model_dir = '{model_dir()}'\n"
        "cluster_epsilon = 0.35\n",
        encoding="utf-8",
    )

    enriched = CliRunner().invoke(cli, ["enrich", "faces", "--archive", str(root)])
    # Exit 1 is spec 13's per-file-error code, and the tiny library carries files no decoder
    # can read ON PURPOSE (its HEIC containers hold no coded image) - so 0 or 1, never a
    # refusal or a crash. Pinning the failure count here would make a deliberate change to
    # the library break a privacy test (the pipeline test's own reasoning, above).
    assert enriched.exit_code in (0, 1), enriched.output
    assert "faces:" in enriched.output, enriched.output
    clustered = CliRunner().invoke(cli, ["faces", "cluster", "--archive", str(root)])
    assert clustered.exit_code == 0, clustered.output

    conn = sqlite3.connect(ws.db_path)
    try:
        (faces_found,) = conn.execute("SELECT COUNT(*) FROM face_instances").fetchone()
    finally:
        conn.close()
    assert faces_found >= 1, (
        "pam enrich faces stored no face, so this canary watched a run that did nothing and"
        f" its empty socket list proves nothing. Output: {enriched.output}"
    )
    assert "faces considered" in clustered.output, clustered.output

    assert watch.attempts == [], (
        "the faces pipeline made a Python-level network call. Vision S4 is absolute."
        f" Recorded: {watch.attempts}"
    )
    assert_nothing_recorded(watch)


# M12 Task 12 Part B, Step B12 (P6): the OCR engine under the watch. A committed fixture, so the
# stage adds no image to the repository.
_OCR_FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "ocr" / "date_iso.png"


@pytest.mark.ocr
def test_the_ocr_engine_loads_and_reads_without_a_python_socket(watch):
    """A COLD `Engine` - no session built - then one `read` of `date_iso.png`, all under the
    rev 4 watch: connection attempts, name lookups and datagram sends.

    THE BOUNDARY, STATED SO NOBODY OVERCLAIMS IT (design 10 rev 28, P6/Q6): this sees PYTHON-level
    calls only. A native library that dialled from C, or a child process, would be invisible
    here; the provider assertion inside `Engine` (each session must report exactly
    `['CPUExecutionProvider']`) is the control for the native runtime, and HT-M12's
    process-level capture is the measurement of it. Both sessions are BUILT and both graphs RUN
    inside the watch, because the lazy engine initialises on this first `read` - construction
    and inference are the two moments an execution provider could act, and both are watched.

    PROOF OF WORK: the date line is read. A canary over a run that did nothing reports zero
    connections truthfully and proves nothing at all.
    """
    from ocr_stack import model_dir
    from PIL import Image

    from pam.enrich import ocr_model

    engine = ocr_model.Engine(model_dir())
    assert engine.receipt() is None, "the engine was not cold"
    with Image.open(_OCR_FIXTURE) as image:
        rgb = image.convert("RGB")
    lines = engine.read(rgb)
    assert [line.text for line in lines] == ["1998-07-04"], lines
    assert engine.receipt() is not None

    assert watch.attempts == [], watch.attempts
    assert_nothing_recorded(watch)


LOAD_BEARING_CONTROLS = {
    # Proof of SIGHT: the recorder really catches a connection that really happened.
    "test_the_canary_records_a_real_outbound_connection": "connect, via a real listener",
    "test_the_canary_also_sees_connect_ex": "the one-word bypass, closed",
    # Proof the ALARM can FIRE, which is the different claim (M449).
    "test_the_alarm_fires_when_it_should": "the assertion that reads the recording",
    # Proof the canary still covers what spec 10 names, and that the watch is removed.
    "test_the_canary_covers_every_stage_spec_10_names": "the floor under PIPELINE_STAGES",
    "test_the_watch_is_uninstalled_when_a_test_finishes": "no wrapper leaks into the suite",
    # The behavioural statement itself, and the two S4 proofs about the geocoder.
    "test_the_whole_pipeline_opens_no_socket_at_all": "spec 10 S4, over the real pipeline",
    "test_reverse_geocoder_imports_and_answers_a_query_without_opening_a_socket": "S4, deps",
    "test_the_geocoder_dataset_ships_inside_the_package_so_the_download_branch_is_dead": (
        "why the test above is green tomorrow as well as today"
    ),
    # M10 Task 7 Step 6: the first loopback consumers, refused off-box and connecting on it.
    "test_the_doctor_probe_refuses_an_off_box_host_without_dialling_it": "doctor, refusal",
    "test_pam_models_pull_refuses_an_off_box_host_without_dialling_it": "pull, refusal",
    "test_a_host_answering_both_loopback_and_off_box_is_refused_before_any_connection": (
        "the rebinding shape, through the consumer rather than through net.py"
    ),
    "test_the_probe_really_does_connect_when_the_address_is_loopback": (
        "the positive control under three assertions of an EMPTY list"
    ),
    # M10 CLOSE (the architecture review, M1670): the CAPTIONER's four rows, which Task 8
    # stage 2 and Task 12 Step 2 added and NEITHER registered here. Deleting all four left
    # 2955 passed + 1 skipped and this very control green - the list stopped at Task 7, so
    # the module spec 10 was actually RELAXED FOR was the one module whose proofs could be
    # removed silently. That is this file's own lesson arriving a fourth time (M449;
    # M450/M451; F3 above), and the answer is the same hand-written floor.
    "test_the_captioner_refuses_an_off_box_host_without_dialling_it": (
        "the captioner, refused off-box - the refusal S4 rests on"
    ),
    "test_a_captioner_host_answering_LOOPBACK_AND_OFF_BOX_is_refused_before_any_connection": (
        "the rebinding shape, through the captioner rather than through net.py"
    ),
    "test_the_captioner_really_does_connect_when_the_address_is_loopback": (
        "the positive control under the captioner's own empty-list assertions"
    ),
    "test_the_enrich_stage_WITH_CAPTIONING_ON_opens_only_loopback_sockets": (
        "the enrich stage walked with the ONE enricher that opens a socket; without it"
        " the Task 7 relaxation is never exercised by the consumer it was made for"
    ),
    # M11 TASK 7. Registered IN THE COMMIT THAT ADDS THEM, which is M10's condition C4 and
    # the direct consequence of M1670 above: the captioner's four rows were added by two
    # tasks and registered by neither, and were deletable with 2955 tests green.
    "test_the_faces_loader_refuses_a_missing_pack_without_opening_a_socket": (
        "A1's strong form - not 'the download is disabled' but 'there is no download',"
        " measured in the state where a downloader would fire"
    ),
    "test_the_faces_loader_refuses_a_corrupt_pack_without_opening_a_socket": (
        "the other refusal path, which is where a product that could re-fetch would"
    ),
    "test_loading_a_real_model_pack_opens_no_socket_at_all": (
        "A5 against the real runtime: the layer spec 10's AST guard cannot reach, because"
        " the capability arrives inside a dependency rather than as an import"
    ),
    # M12 TASK 6 (C9), registered in the commit that adds it (rev 20 R4).
    "test_the_enrich_stage_WITH_FACES_ON_opens_no_socket_at_all": (
        "the faces COMMANDS under the watch - enrich and cluster on the real pack; before it"
        " S4 for faces was one hand measurement plus a sessions-only test"
    ),
    # M12 TASK 6 STEP 4 (rev 4, design 10 Q6), registered in the commit that adds them.
    "test_the_canary_sees_getaddrinfo_and_forwards_its_answer": (
        "proof of sight for the lookup wrapper, both call shapes, and that it forwards"
    ),
    "test_the_canary_sees_gethostbyname": "proof of sight, gethostbyname",
    "test_the_canary_sees_gethostbyname_ex": "proof of sight, gethostbyname_ex",
    "test_the_canary_sees_sendto_and_the_datagram_arrives": (
        "proof of sight for sendto - the LAST argument recorded - and that the datagram arrives"
    ),
    "test_the_canary_sees_sendmsg_where_the_platform_has_it": (
        "proof of sight for sendmsg; skips on Windows, pinned by PLATFORM_GATED_TESTS"
    ),
    "test_the_wrappers_forward_the_originals_exception_unchanged": (
        "record-and-forward includes the original's error; a swallowing wrapper is a mock"
    ),
    "test_the_send_wrappers_installed_are_exactly_what_this_platform_has": (
        "the hasattr filter, and that it can never empty the datagram watch"
    ),
    "test_the_lookup_classifier_answers_a_hand_written_table": (
        "is_loopback_name, both halves - localhost exactly, never localhost.evil.example"
    ),
    "test_no_pam_module_binds_a_socket_resolver_by_name": (
        "the blind spot Q6 names: a from-socket binding escapes the module patch"
    ),
    "test_every_watch_consumer_calls_an_alarm": (
        "R6: every consumer of the watch reads all three records, by a hand-written tuple"
    ),
    # M12 TASK 12 PART B (B12, P6), registered in the commit that adds it.
    "test_the_ocr_engine_loads_and_reads_without_a_python_socket": (
        "the OCR engine built AND run under the watch - Python-level calls only; the provider"
        " assertion is the native layer's control"
    ),
    # M14 TASK 8 STEP 3 (M1670's rule: registered in the commit that adds it).
    "test_the_page_chain_read_rename_undo_opens_only_loopback_sockets": (
        "S4 over the page's chain - the server, its read/rename/undo jobs and the page's own"
        " loopback requests - asserting the work was done as well as every address loopback"
    ),
    # M15 TASK 12 (E4; M1670's rule: registered in the commit that adds it).
    "test_no_archive_command_opens_a_socket_or_loads_the_release_client": (
        "E4 + S4: every headless archive command through `pam.cli.main` in a fresh process -"
        " nothing recorded and `pam.update` never loaded; the release client's second guardian"
    ),
    "test_the_release_client_canary_sees_the_lazy_import_and_a_real_connection": (
        "the fresh-process canary's controls: it sees `pam update`'s lazy import and a real"
        " loopback connection, so its empty readings mean something"
    ),
}

assert LOAD_BEARING_CONTROLS, "the canary's control list is empty; it protects nothing"


def test_no_control_in_this_file_can_be_deleted_without_saying_so():
    """The floor under the file's own inventory (F3).

    A canary whose coverage can shrink silently is a canary that will - written about
    `PIPELINE_STAGES` at M9 and true again here one layer up. `ruff --fix` will happily
    remove an import left unused by a deleted test, so the shrinking is not even a
    deliberate act somebody has to commit to.
    """
    import sys as _sys

    present = {name for name in vars(_sys.modules[__name__]) if name.startswith("test_")}
    missing = sorted(set(LOAD_BEARING_CONTROLS) - present)
    assert missing == [], (
        "a load-bearing control has been deleted from the privacy canary:"
        f" {missing}. Each of these is the reason another assertion in this file means"
        " anything; removing one is a deliberate edit to LOAD_BEARING_CONTROLS, with the"
        f" reason it was kept for written beside it ({[LOAD_BEARING_CONTROLS[m] for m in missing]})."
    )


def test_only_the_pipeline_test_is_gated_on_the_presence_of_exiftool():
    """The gate's reach, in both directions, on a machine where exiftool is present.

    Reads this module's own objects rather than running them, because the interesting
    machine - the one WITHOUT the binary - is not the machine the suite runs on. A
    `skipif` whose condition is False still carries its mark, so the mark is what gets
    inspected and the condition is irrelevant here.
    """
    import sys as _sys

    module = _sys.modules[__name__]
    assert not hasattr(module, "pytestmark"), (
        "tests/test_net_canary.py has a MODULE-LEVEL mark again. If it is the exiftool"
        " skipif, it silently disarms the canary's proof of sight and both S4 geocoder"
        " tests on every machine without the binary - which is the failure mode, not the"
        " missing tool. Gate the one test that needs a real scan."
    )
    gated = {
        name
        for name, obj in vars(module).items()
        if name.startswith("test_")
        and any(mark.name == "skipif" for mark in getattr(obj, "pytestmark", []))
    }
    allowed = EXIFTOOL_GATED_TESTS | PLATFORM_GATED_TESTS
    assert gated == allowed, (
        "the set of skippable tests in the canary changed."
        f" Newly skippable: {sorted(gated - allowed)}."
        f" No longer skippable: {sorted(allowed - gated)}."
    )
    assert EXIFTOOL_GATED_TESTS.isdisjoint(PLATFORM_GATED_TESTS)
    assert len(PLATFORM_GATED_TESTS) == 1, PLATFORM_GATED_TESTS


# --- M10 Task 7 Step 6: the first loopback consumers, and their refusal ---------------
#
# `pam doctor`'s Ollama probe and `pam models pull` are the first code in pam that opens
# a socket on purpose. Both go through `pam/net.py`, which RESOLVES FIRST AND THEN
# CONNECTS TO THE ADDRESS IT CHECKED - the whole DNS-rebinding defence - and M10 is the
# first milestone with a caller who could lose it.
#
# THESE TESTS WATCH THE CALL, NOT THE OUTCOME. M8's M697 is why: a refusal that did the
# dangerous thing first and then returned the same error as a correct refusal is
# indistinguishable from the correct one if the assertion only reads the outcome. So the
# refusal is asserted AND `watch.attempts` is asserted empty - the connection must never
# have been attempted, not merely have failed.

_OFF_BOX = "203.0.113.7"  # TEST-NET-3, RFC 5737. Never routable, never this machine.


def off_box_resolver(host, port):
    """A resolver that answers a name with an address somewhere else on the internet.

    A fake rather than a real lookup, for `pam/net.py`'s own stated reason: the attack is
    a name that LOOKS local and resolves off-box, and a test that cannot control the
    answer can only prove that pam refuses IP literals, which is the easy half.
    """
    return [_OFF_BOX]


def test_the_doctor_probe_refuses_an_off_box_host_without_dialling_it(watch, real_ollama):
    """Step 6, for `pam doctor`'s `GET /api/tags`."""
    with pytest.raises(PamNetworkError, match=_OFF_BOX):
        real_ollama.tags(11434, host="localhost", resolver=off_box_resolver)
    assert watch.attempts == [], (
        "pam doctor's Ollama probe connected to a host it was about to refuse."
        f" Addresses dialled: {watch.attempts}"
    )
    assert_nothing_recorded(watch)


def test_pam_models_pull_refuses_an_off_box_host_without_dialling_it(watch, real_ollama):
    """Step 6, for `pam models pull` - amendment A9, in behaviour rather than in prose.

    "pam itself opens no non-loopback socket, in any command, ever" (spec 12 rev 17). The
    command that LOOKS like egress - it is the one that fetches gigabytes over the
    internet - is the one that must be shown not to be: Ollama does the downloading, and
    pam's own socket goes to 127.0.0.1 or nowhere.
    """
    with pytest.raises(PamNetworkError, match=_OFF_BOX):
        real_ollama.pull("qwen2.5vl", host="localhost", resolver=off_box_resolver, timeout=5.0)
    assert watch.attempts == [], (
        "pam models pull connected to a host it was about to refuse."
        f" Addresses dialled: {watch.attempts}"
    )
    assert_nothing_recorded(watch)


def test_a_host_answering_both_loopback_and_off_box_is_refused_before_any_connection(
    watch, real_ollama
):
    """The rebinding shape itself: a resolver answering 127.0.0.1 AND somewhere else.

    Picking the loopback answer out of the pair would make pam's behaviour depend on
    record ordering, which is under the attacker's control. Driven through the real
    consumer rather than through `net.py` directly, because the question this task adds
    is whether the CONSUMER kept the guarantee, not whether `net.py` still has it.
    """

    def both(host, port):
        return ["127.0.0.1", _OFF_BOX]

    with pytest.raises(PamNetworkError, match=_OFF_BOX):
        real_ollama.tags(11434, host="localhost", resolver=both)
    assert watch.attempts == []
    assert_nothing_recorded(watch)


def test_the_probe_really_does_connect_when_the_address_is_loopback(watch, real_ollama):
    """THE POSITIVE CONTROL, and without it every assertion above is worthless.

    All three tests above assert an EMPTY list, which an implementation that never
    connects to anything at all would satisfy perfectly. So the same production function,
    pointed at `tests/ollama_stub.py` on 127.0.0.1, must really open a socket, really get
    an answer, and the address it dialled must be loopback - which is exactly the
    assertion spec 10 now asks the canary for.
    """
    with OllamaStub({"/api/tags": [tags_reply("qwen2.5vl:latest")]}) as stub:
        assert real_ollama.tags(stub.port) == ["qwen2.5vl:latest"]
        assert stub.paths() == ["/api/tags"]

    assert watch.attempts, "the probe answered without opening a socket at all"
    assert all(is_loopback(address) for address in watch.attempts), watch.attempts
    assert [address[1] for address in watch.attempts] == [stub.port]
    # M12 Task 6 Step 4 (rev 4): the LOOKUP wrapper sees a real consumer's resolution -
    # `pam/net.py`'s own resolve-then-connect - and nothing here sends a datagram.
    assert watch.lookups, "a real loopback request resolved nothing the watch could see"
    assert all(is_loopback_name(host) for host in watch.lookups), watch.lookups
    assert watch.sends == [], watch.sends
    assert_only_loopback(watch)


# --- STAGE 2: THE CAPTIONER'S OWN S4 PROOF (M10 Task 8 Step 9) ------------------------
#
# THE THREE TESTS ABOVE COVER `pam doctor` AND `pam models pull`. THEY DO NOT COVER THE
# CAPTIONER, which is the module design 10 was actually relaxed for, and until this block
# existed the non-loopback refusal was asserted NOWHERE for it - the implementer disclosed
# that and Task 8's stage 1 confirmed it, leaving it here by design.
#
# WHY A FAKE RESOLVER AND NOT A STUB BOUND OFF-BOX. Step 9 says "a stub bound to a
# non-loopback address", and no process can bind one - 203.0.113.7 is not an address this
# machine holds. The mechanism that matters is the one `pam/net.py`'s docstring names: the
# attack is A NAME THAT LOOKS LOCAL AND RESOLVES OFF-BOX, so the resolver is the seam, and
# refusing an IP literal is only the easy half. `off_box_resolver` above is Task 7's, reused
# deliberately so both consumers are held to one standard.
#
# THE REFUSAL DOES NOT RAISE HERE, and that is `CaptionEnricher.__init__`'s design: a
# `PamNetworkError` out of a builder would be a traceback where design 8.1 promises a
# sentence, so it is caught and carried as `available()`'s reason. That makes the assertion
# a two-parter - the sentence names the address, AND no socket was opened - and it is why
# `watch.attempts` is the load-bearing half rather than the exception.


def _captioner(port, **kwargs):
    """A `CaptionEnricher` with the timeout wound down, since none of these connect far."""
    return CaptionEnricher("qwen2.5vl", port=port, timeout_s=5.0, **kwargs)


def test_the_captioner_refuses_an_off_box_host_without_dialling_it(watch, real_ollama):
    """S4 for the enricher itself: the refusal, and no connection attempt.

    Asserted on the CALL and not on the outcome (M8's M697): a captioner that resolved the
    name, opened the socket, sent a photograph and THEN noticed the address was off-box
    would return this same sentence, and an outcome-only assertion could not tell the two
    apart. `watch.attempts == []` is the half that can.
    """
    ready, reason = _captioner(11434, host="localhost", resolver=off_box_resolver).available()

    assert ready is False
    assert _OFF_BOX in reason, reason
    assert watch.attempts == [], (
        "the captioner connected to a host it was about to refuse - a photograph may have"
        f" left this machine. Addresses dialled: {watch.attempts}"
    )
    assert_nothing_recorded(watch)


def test_a_captioner_host_answering_LOOPBACK_AND_OFF_BOX_is_refused_before_any_connection(
    watch, real_ollama
):
    """The rebinding shape, from the consumer that carries the image bytes.

    `net.py` refuses a host that resolves to a mix rather than filtering down to the
    loopback answer, because picking one would make pam's behaviour depend on record
    ordering - which is under the attacker's control. Task 7 pinned this at `net.py`'s own
    level and for `pam doctor`; THE CAPTIONER IS THE FIRST CONSUMER FOR WHICH LOSING IT
    WOULD SEND A PHOTOGRAPH, so it gets its own row.
    """

    def both(host, port):
        return ["127.0.0.1", _OFF_BOX]

    ready, reason = _captioner(11434, host="localhost", resolver=both).available()

    assert ready is False
    assert _OFF_BOX in reason, reason
    assert watch.attempts == []
    assert_nothing_recorded(watch)


def test_the_captioner_really_does_connect_when_the_address_is_loopback(watch, real_ollama):
    """THE POSITIVE CONTROL, and without it the two tests above are worthless.

    Both assert an EMPTY list, which a captioner that never connects to anything at all -
    or one whose client was quietly replaced by the suite's own tripwire - would satisfy
    perfectly. So the same production class, pointed at `tests/ollama_stub.py` on
    127.0.0.1, must really open a socket, really get its answer, and every address it
    dialled must be loopback: which is exactly the assertion spec 10 relaxed the canary to.
    """
    with OllamaStub({"/api/tags": [tags_reply("qwen2.5vl:latest")]}) as stub:
        ready, reason = _captioner(stub.port).available()

        assert ready is True, reason
        assert stub.paths() == ["/api/tags"]

    assert watch.attempts, "the captioner answered without opening a socket at all"
    assert all(is_loopback(address) for address in watch.attempts), watch.attempts
    assert [address[1] for address in watch.attempts] == [stub.port]
    # M12 Task 6 Step 4 (rev 4): the LOOKUP wrapper sees a real consumer's resolution -
    # `pam/net.py`'s own resolve-then-connect - and nothing here sends a datagram.
    assert watch.lookups, "a real loopback request resolved nothing the watch could see"
    assert all(is_loopback_name(host) for host in watch.lookups), watch.lookups
    assert watch.sends == [], watch.sends
    assert_only_loopback(watch)


def test_the_enrich_stage_WITH_CAPTIONING_ON_opens_only_loopback_sockets(
    tmp_path, watch, real_ollama
):
    """THE CANARY'S `enrich` STAGE, WALKED WITH THE ONE ENRICHER THAT OPENS A SOCKET.

    THIS CLOSES A REAL GAP AND IT IS WORTH STATING PLAINLY. `PIPELINE_STAGES` names an
    `enrich` stage, and `test_the_whole_pipeline_opens_no_socket_at_all` drives it as
    `run_enrich("phash", library)` - ONE ENRICHER, BY NAME, with
    `assert [o.name for o in enriched.outcomes] == ["phash"]` next to it. `caption` ships
    `enabled = false`, so no test in this file had ever walked the captioner: the canary was
    RELAXED IN TASK 7 FOR A CONSUMER IT NEVER EXERCISED, and a canary that cannot reach the
    thing it was relaxed for is a canary that would have kept reading green if the
    captioner dialled the internet on every photograph.

    Driven through `run_enrich` - the real command's own entry point, config and all - so
    the address dialled is the one a user's `pam enrich` would dial, and asserted to have
    DONE THE WORK (`facts_written > 0`), because a stage that captioned nothing would report
    zero connections truthfully and prove nothing.

    IT SCANS WITH THE FAKE EXIFTOOL AND SO CARRIES NO `needs_exiftool` GATE, deliberately.
    What is under watch here is the captioner's socket, not the scanner's subprocess - the
    pipeline test above already covers real exiftool - and a conditional skip would make
    THIS row, the only one that watches pam talking to a model, vanish on exactly the
    machine that has no exiftool. `EXIFTOOL_GATED_TESTS` stays a one-name set, and the
    guard that pins it is what caught the first draft of this test trying to join it.
    """
    root = tmp_path / "photos"
    root.mkdir(parents=True)
    Image.new("RGB", (40, 30), (17, 99, 200)).save(root / "photo.png", format="PNG")
    with OllamaStub(
        {
            "/api/tags": [tags_reply("qwen2.5vl:latest")],
            "/api/generate": [generate_reply("Blue Rectangle", "A small blue rectangle.")],
        }
    ) as stub:
        run_scan(root, exiftool_command=FAKE_COMMAND)
        ws = workspace_if_exists(root)
        assert ws is not None
        ws.config_path.write_text(
            f"[enrich.caption]\nenabled = true\nollama_port = {stub.port}\n",
            encoding="utf-8",
        )

        enriched = run_enrich("caption", root)

        assert [o.name for o in enriched.outcomes] == ["caption"]
        assert enriched.outcomes[0].result is not None, enriched.outcomes[0].refusal
        assert enriched.outcomes[0].result.facts_written > 0, (
            "the captioner stored no caption, so this canary watched a run that did"
            " nothing and its empty socket list proves nothing"
        )
        assert "/api/generate" in stub.paths()

    assert watch.attempts, "captioning wrote facts without opening a socket at all"
    # ALL THREE RECORDS, through the shared alarm (rev 4.1, R6). The hand filter this replaced
    # read `attempts` only, so a captioner that looked up an off-box name or sent it a
    # datagram would have passed.
    assert_only_loopback(watch)
