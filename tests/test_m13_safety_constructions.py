"""M13's close (plan Task 16, Steps 1-2): the full chain twice, and the construction hunt.

WHY THIS FILE EXISTS. A property no single module owns is invisible to a mutation pass scoped
to one task's files (CLAUDE.md, "the mutation gate"): M8's 232 rows could not reach `pam apply`
splitting a unit, M9's 392 could not reach F12, M11's review found two defects BETWEEN commands.
So the vision's safety properties - S1-S4, T3 ("nothing is ever renamed without an explicit
approval action in that session") and the M13 plan's invariants I1-I8 - are taken one at a time
and the violation is BUILT end to end, driving the real commands (`pam.cli.main`) and the real
review server over loopback HTTP, on real scanned archives.

STEP 1 - THE CHAIN, TWICE, on a scratch copy of the spec 9.1 tiny library (real exiftool, real
`phash` and `geocode`): scan -> enrich -> plan -> review (a STRICT SUBSET approved through the
server: `decide` naming one id per unit on the first pass, `Accept all` on the second) -> apply
-> count what changed -> undo -> verify. `pam verify` reports 12 checks clean after EVERY step;
the apply changes exactly the approved units' paths, never splits one, and the undo puts every
path and every byte back.

STEP 2 - SIX QUESTIONS (M8-M12's five, plus F1's):

  Q1  Which filter dimensions are NOT unit-uniform? M13's new one is `look_alikes`: the photo
      of a Live Photo can look like another photo while its video (never fingerprinted) does
      not. No write path selects by it - `decide-filter` has no such field, and a body that
      carries one decides exactly what the same body without it decides. `Accept all`'s scope
      is the whole plan, and it writes whole units only.
  Q2  Which members of a unit does each command NOT see? `pre_pam_names` is per FILE. A unit
      pam renamed, one of whose members the USER then renamed (a case-only rename: the stem is
      kept, so the unit is kept), is read per member - pam's own name is not evidence for the
      member pam renamed, and the user's name IS evidence for the other. The plan still keeps
      the two members in one unit.
  Q3  Does every write path still resolve over the WHOLE plan, whole units only? The live route
      table is compared, route by route, with a list written here by hand: M13 adds NO route,
      and `Accept all` is a branch of `decide-filter`.
  Q4  Which faces does an unnamed-cluster operation see? M11/M12's constructions stay in their
      own modules and run unchanged; M13's new faces command, `pam faces calibrate`, READS and
      never writes - proved by the bytes of the catalog and of every workspace file.
  Q5  What does a write in one command (or the OPERATING SYSTEM) do to an artefact another
      produced, or to a reader still open? (a) `pam status` paused after each of its eight
      reads while `pam apply` commits reports the before- or the after-state, never a mix;
      `pam verify` holds the lock, so `pam apply` is refused under it and renames nothing.
      (b) Every fact pam derives from a NAME or an MTIME, each touched under a fresh scan: the
      `filename` date (I5), the `file_mtime` date, the stat that P2's skip trusts, the apply-time
      `(size, mtime_ns)` check, and the `qt_utc` "video date ambiguous" rule's name comparison.
  Q6  F1's: after R1, which path still turns an UNDECIDED item into a rename? `pam apply`
      without and with `--all` (cancelled), every review write endpoint, a faces write, and
      RECOVERY: an apply crashed at `after_intent` with one approved and one undecided item.

EVERY EXPECTATION IS SPELLED FROM THE SPEC OR BUILT BY HAND, never read back out of the
production call it checks: statuses, units and names are read with the suite's own SQL, the
route table is a literal, the disk is walked and hashed here, and the dangerous call itself -
`perform_verified_rename`, the one verified rename apply, undo and recovery share - is watched
by a spy (M697: an outcome check cannot tell "never renamed it" from "renamed it and put it
back").
"""

import hashlib
import json
import os
import socket
import sqlite3
import threading
import webbrowser
from datetime import datetime
from http.client import HTTPConnection
from pathlib import Path, PurePosixPath

import pytest
import test_net_canary as net_canary
from archives import FAKE_COMMAND, materialise_tiny_library, put
from test_m12_safety_constructions import STATUS_READS, _masked, _status_across

from pam.catalog.db import connect
from pam.cli import main
from pam.execute import unit as unit_mod
from pam.paths import workspace_for
from pam.scan.runner import run_scan
from pam.webui import run_review

# design 7.5's twelve check keys, spelled out: `pam verify` prints one `ok` line per check.
VERIFY_KEYS = ("1", "2a", "2b", "3", "4a", "4b", "4c", "4d", "4e", "5", "6", "7")
VERIFY_CLEAN = "Done. 12 checks, everything pam recorded looks right."


@pytest.fixture(autouse=True)
def _never_a_real_browser(monkeypatch):
    """`run_review` opens a browser unless told otherwise; this makes a forgotten opener a no-op."""
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: True)


@pytest.fixture()
def canary(monkeypatch):
    """`tests/test_net_canary.py::watch`, installed the same way from that module's own parts
    (a fixture cannot be imported by name without shadowing it): every `socket` connect,
    lookup and send this test makes is recorded, and its alarms judge the record."""
    watcher = net_canary.ConnectWatch()
    for name in net_canary.WATCHED_METHODS:
        method = getattr(socket.socket, name)
        monkeypatch.setattr(socket.socket, name, net_canary._wrap(method, watcher))
    for name in net_canary.WATCHED_LOOKUP_FUNCTIONS:
        lookup = getattr(socket, name)
        monkeypatch.setattr(socket, name, net_canary._wrap_lookup(lookup, watcher))
    for name in net_canary.WATCHED_SEND_METHODS:
        if hasattr(socket.socket, name):
            wrapped = net_canary._SEND_WRAPPERS[name](getattr(socket.socket, name), watcher)
            monkeypatch.setattr(socket.socket, name, wrapped)
    return watcher


@pytest.fixture()
def spy(monkeypatch):
    """Every (old_rel, new_rel) pam asked the one verified rename to perform - forward, back,
    and recovery's revert alike (`pam/execute/unit.py` is its only call site)."""
    calls = []
    real = unit_mod.perform_verified_rename

    def watching(*args, **kwargs):
        assert args == (), "perform_verified_rename takes keyword arguments only"
        calls.append((kwargs["old_rel"], kwargs["new_rel"]))
        return real(*args, **kwargs)

    monkeypatch.setattr(unit_mod, "perform_verified_rename", watching)
    return calls


# --------------------------------------------------------------------------- shared drivers


def _snapshot(root):
    """`{rel_path: sha256}` of every file under `root`, walked and hashed HERE."""
    found = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = Path(dirpath) / name
            found[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return found


def _conn(root):
    conn = connect(workspace_for(root).db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _items(root, plan_id):
    """`{old_rel_path: row}` of one plan's items, with the suite's own SQL."""
    conn = _conn(root)
    try:
        rows = conn.execute(
            "SELECT id, old_rel_path, new_filename, unit_key, tier, status FROM plan_items"
            " WHERE plan_id = ?",
            (plan_id,),
        ).fetchall()
    finally:
        conn.close()
    return {row["old_rel_path"]: dict(row) for row in rows}


def _statuses(root, plan_id):
    return {rel: row["status"] for rel, row in _items(root, plan_id).items()}


def _units(items):
    """`{unit_key: [old_rel_path, ...]}`, sorted, from rows the suite read itself."""
    found = {}
    for rel, row in items.items():
        found.setdefault(row["unit_key"], []).append(rel)
    return {key: sorted(members) for key, members in found.items()}


def _open_plan(root):
    conn = _conn(root)
    try:
        rows = conn.execute("SELECT id FROM plans WHERE status = 'open'").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, f"expected one open plan, found {len(rows)}"
    return int(rows[0][0])


def _sessions(root):
    conn = _conn(root)
    try:
        return [
            str(r[0])
            for r in conn.execute(
                "SELECT session_id FROM journal GROUP BY session_id ORDER BY MIN(id)"
            )
        ]
    finally:
        conn.close()


def _pam(capsys, *args, typed=None, monkeypatch=None):
    """`pam ARGS` through `pam.cli.main`. Returns (exit code, stdout, stderr)."""
    if typed is not None:
        monkeypatch.setattr("builtins.input", lambda: typed)
    capsys.readouterr()
    code = main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def _verified_clean(capsys, root, where):
    """`pam verify`: exit 0, one `ok` line per design 7.5 check, the clean closing line."""
    code, out, err = _pam(capsys, "verify", "--archive", str(root))
    assert code == 0, (where, out, err)
    oks = [line.split()[1] for line in out.splitlines() if line.startswith("  ok ")]
    assert oks == list(VERIFY_KEYS), (where, out)
    assert VERIFY_CLEAN in out, (where, out)


def _call(server, method, path, body=None):
    payload = b"" if body is None else json.dumps(body).encode("utf-8")
    conn = HTTPConnection(server.host, server.port, timeout=30)
    try:
        conn.request(
            method,
            f"/t/{server.token}/api{path}",
            body=payload,
            headers={"Content-Type": "application/json", "Content-Length": str(len(payload))},
        )
        response = conn.getresponse()
        raw = response.read()
        return response.status, (json.loads(raw) if raw else None)
    finally:
        conn.close()


def _ok(server, method, path, body=None):
    status, reply = _call(server, method, path, body)
    assert status == 200, (method, path, status, reply)
    return reply


def _drive(plan_id, root, work):
    """Run `work(server)` against the server `pam review` really starts, then stop it."""
    result = {}

    def serve(server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result["value"] = work(server)
        finally:
            server.shutdown()
            thread.join(timeout=30)

    run_review(plan_id, root, serve=serve, opener=lambda url: True)
    return result["value"]


def _expected_after(before, items, approved_units):
    """The disk after an apply that renames exactly `approved_units`, spelled from design 7.1:
    every member moves to `<its folder>/<new_filename>` (a member already carrying it - the
    no-op item - stays put); every other path keeps its name and its bytes."""
    moves = {}
    for rel, row in items.items():
        if row["unit_key"] in approved_units:
            target = str(PurePosixPath(rel).parent / row["new_filename"]).removeprefix("./")
            if target != rel:
                moves[rel] = target
    return {moves.get(rel, rel): sha for rel, sha in before.items()}, moves


def _stem(rel):
    name = PurePosixPath(rel).name
    return name.rsplit(".", 1)[0]


# ============================================================================ STEP 1


def _accepted_units(items):
    """F1 R2's rule, written HERE as a second implementation (never `naming.accept`): a unit
    is accepted iff every member is undecided and none is `low`."""
    return {
        key
        for key, members in _units(items).items()
        if all(items[m]["status"] == "proposed" for m in members)
        and not any(items[m]["tier"] == "low" for m in members)
    }


def test_the_instruments_see_a_rename_a_changed_byte_a_unit_and_the_accept_rule(tmp_path):
    """The hunt's own instruments, controlled (CLAUDE.md: mutate the code that TESTS the code).
    `_snapshot` names the path a byte changed at and sees a rename; `_expected_after` moves a
    whole unit into its folder and leaves a no-op member and everything else alone; `_stem`
    keeps a dotted stem; `_accepted_units` (the second implementation of F1 R2) answers a
    hand-written table - an all-undecided unit, a mixed one, a skipped one, a low one."""
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "x.bin").write_bytes(b"abc")
    (tmp_path / "y.bin").write_bytes(b"abc")
    abc = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    before = _snapshot(tmp_path)
    assert before == {"d/x.bin": abc, "y.bin": abc}
    (tmp_path / "y.bin").write_bytes(b"abd")
    assert {p for p in before if _snapshot(tmp_path)[p] != before[p]} == {"y.bin"}
    os.rename(tmp_path / "d" / "x.bin", tmp_path / "d" / "z.bin")
    assert "d/z.bin" in _snapshot(tmp_path) and "d/x.bin" not in _snapshot(tmp_path)

    items = {
        "d/a.JPG": {
            "unit_key": "u",
            "new_filename": "N.JPG",
            "status": "proposed",
            "tier": "high",
        },
        "d/N.MOV": {
            "unit_key": "u",
            "new_filename": "N.MOV",
            "status": "proposed",
            "tier": "high",
        },
        "b.JPG": {
            "unit_key": "v",
            "new_filename": "M.JPG",
            "status": "proposed",
            "tier": "high",
        },
    }
    shas = {"d/a.JPG": "1", "d/N.MOV": "2", "b.JPG": "3", "c.txt": "4"}
    after, moves = _expected_after(shas, items, {"u"})
    assert moves == {"d/a.JPG": "d/N.JPG"}
    assert after == {"d/N.JPG": "1", "d/N.MOV": "2", "b.JPG": "3", "c.txt": "4"}
    assert _stem("d/2019-07-04_001.acr_.jpg") == "2019-07-04_001.acr_"

    def row(unit, status, tier="high"):
        return {"unit_key": unit, "status": status, "tier": tier}

    table = {
        "a1": row("a", "proposed"),
        "a2": row("a", "proposed", "medium"),
        "m1": row("m", "proposed"),
        "m2": row("m", "approved"),
        "s1": row("s", "skipped"),
        "l1": row("l", "proposed"),
        "l2": row("l", "proposed", "low"),
    }
    assert _accepted_units(table) == {"a"}


def _chain_pass(capsys, monkeypatch, root, *, how):
    """One pass of the chain. Returns what it measured, for the progress notes."""
    measured = {}
    code, out, err = _pam(capsys, "scan", str(root))
    assert code == 0, out + err
    _verified_clean(capsys, root, "after scan")

    # The tiny library's HEIC files are containers with no coded image ON PURPOSE, so `phash`
    # counts them "could not be read" and exits 1 (spec 13: completed with per-file errors).
    code, out, err = _pam(capsys, "enrich", "phash", "--archive", str(root))
    assert code in (0, 1), out + err
    code, out, err = _pam(capsys, "enrich", "geocode", "--archive", str(root))
    assert code == 0, out + err
    _verified_clean(capsys, root, "after enrich")

    code, out, err = _pam(capsys, "plan", "--archive", str(root))
    assert code == 0, out + err
    plan_id = _open_plan(root)
    items = _items(root, plan_id)
    units = _units(items)
    assert {row["status"] for row in items.values()} == {"proposed"}
    _verified_clean(capsys, root, "after plan")

    before = _snapshot(root)
    if how == "decide":
        # Every companion unit, plus the first two singles - ONE id named per unit; the server
        # must widen each id to its unit (F1 R3).
        multi = sorted(key for key, members in units.items() if len(members) > 1)
        singles = sorted(key for key, members in units.items() if len(members) == 1)[:2]
        chosen = set(multi) | set(singles)
        named = [items[units[key][0]]["id"] for key in sorted(chosen)]
        reply = _drive(
            plan_id,
            root,
            lambda server: _ok(
                server,
                "POST",
                f"/plan/{plan_id}/decide",
                {"action": "approve", "item_ids": named},
            ),
        )
        assert reply["decided"] == sum(len(units[key]) for key in chosen)
        assert multi, "the tiny library holds companion units; the pass must widen one"
    else:
        chosen = _accepted_units(items)
        reply = _drive(
            plan_id,
            root,
            lambda server: _ok(
                server,
                "POST",
                f"/plan/{plan_id}/decide-filter",
                {"action": "approve", "accept_all": True},
            ),
        )
        assert reply["decided"] == sum(len(units[key]) for key in chosen)
        assert reply["excluded_low"] == sum(
            len(members)
            for key, members in units.items()
            if any(items[m]["tier"] == "low" for m in members)
        )
    assert 0 < len(chosen) < len(units), "the approved set must be a STRICT subset"
    statuses = _statuses(root, plan_id)
    for key, members in units.items():
        want = "approved" if key in chosen else "proposed"
        assert {statuses[m] for m in members} == {want}, (key, members)
    assert _snapshot(root) == before, "deciding renamed something"
    _verified_clean(capsys, root, "after review")

    code, out, err = _pam(
        capsys,
        "apply",
        str(plan_id),
        "--archive",
        str(root),
        typed="apply",
        monkeypatch=monkeypatch,
    )
    assert code == 0, out + err
    after = _snapshot(root)
    expected, moves = _expected_after(before, items, chosen)
    assert after == expected, "the apply changed something other than the approved units"
    measured["changed"] = len(moves)
    measured["approved_items"] = sum(len(units[key]) for key in chosen)
    measured["units"] = (len(chosen), len(units))
    # No unit split: every member of every unit shares ONE stem on disk.
    for key, members in units.items():
        on_disk = [moves.get(m, m) for m in members]
        assert len({_stem(p) for p in on_disk}) == 1, (key, on_disk)
    _verified_clean(capsys, root, "after apply")

    session = _sessions(root)[-1]
    code, out, err = _pam(capsys, "undo", session, "--archive", str(root))
    assert code == 0, out + err
    assert _snapshot(root) == before, "--exact: the undo did not restore every path and byte"
    _verified_clean(capsys, root, "after undo")
    return measured


def test_step1_the_full_chain_twice_on_a_scratch_tiny_library(
    tmp_path, capsys, monkeypatch, canary
):
    """Spec 9.1's library, copied (never the committed folder). Pass 1 approves every companion
    unit and two singles by naming ONE id each; pass 2, over a fresh plan of the same archive
    after the undo, approves through `Accept all`. Both are strict subsets; both change exactly
    the approved units on disk; both undo to the exact original. And the whole chain runs under
    the privacy canary (vision S4): the only sockets are the review page's own loopback ones -
    which the canary must have SEEN, or it proves nothing."""
    root = materialise_tiny_library(tmp_path / "a")
    original = _snapshot(root)
    first = _chain_pass(capsys, monkeypatch, root, how="decide")
    second = _chain_pass(capsys, monkeypatch, root, how="accept_all")
    assert _snapshot(root) == original
    assert canary.attempts, "the canary saw not even the review server's loopback connection"
    net_canary.assert_only_loopback(canary)
    # Measured at the Task 16 commit: pass 1 approved 9 of 95 units (16 items, 16 paths
    # changed); pass 2 accepted 81 of 95 (87 items, 87 paths; 15 low items held back).
    assert (first["units"], first["approved_items"], first["changed"]) == ((9, 95), 16, 16)
    assert (second["units"], second["approved_items"], second["changed"]) == ((81, 95), 87, 87)


# ============================================================================ Q1
# --- `look_alikes`: a dimension that is NOT unit-uniform, and that no write path selects by --


def _look_alike_archive(tmp_path, capsys):
    """A Live Photo whose PHOTO looks like `base.png` (the same 32x32 pattern, resized and
    re-encoded) and whose VIDEO is never fingerprinted (design 8.1: `phash` does not apply to a
    video), one photo that looks like nothing, and the real `pam enrich phash` and `pam plan`.
    Returns (root, plan_id)."""
    from test_webui_lookalikes import BASE, FAR, _resized_jpeg

    root = tmp_path / "photos"
    root.mkdir()
    for name, payload in (
        ("IMG_7.JPG", _resized_jpeg(BASE, 2)),
        ("IMG_7.MOV", b"the only copy of IMG_7.MOV"),
        ("base.png", BASE),
        ("far.png", FAR),
    ):
        put(root, name, content=payload)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    code, out, err = _pam(capsys, "enrich", "phash", "--archive", str(root))
    assert code == 0, out + err
    code, out, err = _pam(capsys, "plan", "--archive", str(root))
    assert code == 0, out + err
    return root, _open_plan(root)


# The cards' look-alikes, spelled from H3: each item names the OTHER-unit items in its group.
LOOK_ALIKES = {
    "IMG_7.JPG": ["base.png"],
    "IMG_7.MOV": [],
    "base.png": ["IMG_7.JPG"],
    "far.png": [],
}
LIVE_UNIT = ["IMG_7.JPG", "IMG_7.MOV"]


def _cards(server, plan_id, query=""):
    reply = _ok(server, "GET", f"/plan/{plan_id}/items?limit=200{query}")
    return {card["old_rel_path"]: card for card in reply["items"]}


def _reset_all(server, plan_id):
    _ok(server, "POST", f"/plan/{plan_id}/decide-filter", {"action": "reset"})


def test_q1_look_alikes_differ_inside_one_unit_and_no_write_path_selects_by_them(
    tmp_path, capsys
):
    """The precondition, then the property. The Live Photo's two cards carry DIFFERENT
    look-alikes (the dimension is not unit-uniform). `GET items` takes no `look_alikes`
    filter: a request carrying one is REFUSED by name (M14 Task 2 Step 1a, V3's rule on the
    read endpoint; until then it was ignored and listed the unfiltered plan). And every
    `decide-filter` body carrying a `look_alikes` field - beside a search, alone, or beside
    `accept_all` - is REFUSED and writes nothing (M14 Task 0D, M13 V3: a field pam does not
    know is a 400; until then it was ignored and wrote what the body without it wrote), while
    the body without it writes whole units only.

    Mutation: a `look_alikes` operand added to `repo.select_items` or `FILTER_FIELDS` -> the
    body with the field is accepted (200) instead of refused."""
    root, plan_id = _look_alike_archive(tmp_path, capsys)
    items = _items(root, plan_id)
    assert _units(items)[items["IMG_7.JPG"]["unit_key"]] == LIVE_UNIT
    bodies = [
        {"action": "skip", "q": "IMG_7.JPG"},
        {"action": "skip", "q": "base"},
        {"action": "approve", "q": "IMG_7.MOV"},
        {"action": "approve"},
        {"action": "approve", "accept_all": True},
    ]
    shapes = (["base.png"], True, "base.png", 1)

    def work(server):
        cards = _cards(server, plan_id)
        filtered = {
            str(shape): _call(
                server, "GET", f"/plan/{plan_id}/items?limit=200&look_alikes={shape}"
            )
            for shape in shapes
        }
        writes = []
        for body in bodies:
            outcomes = []
            for extra in ({}, *({"look_alikes": shape} for shape in shapes)):
                _reset_all(server, plan_id)
                status, _ = _call(
                    server, "POST", f"/plan/{plan_id}/decide-filter", body | extra
                )
                outcomes.append((status, _statuses(root, plan_id)))
            writes.append(outcomes)
        _reset_all(server, plan_id)
        return cards, filtered, writes

    cards, filtered, writes = _drive(plan_id, root, work)

    assert {rel: card["look_alikes"] for rel, card in cards.items()} == LOOK_ALIKES
    refusal = 'look_alikes: "look_alikes" is not something pam can filter by.'
    for shape, (status, reply) in filtered.items():
        assert (status, reply) == (400, {"error": refusal}), shape
    for body, outcomes in zip(bodies, writes, strict=True):
        baseline = outcomes[0]
        assert baseline[0] == 200, body
        untouched = {rel: "proposed" for rel in baseline[1]}
        for outcome in outcomes[1:]:
            assert outcome == (400, untouched), (body, outcome)
        statuses = baseline[1]
        assert len({statuses[m] for m in LIVE_UNIT}) == 1, (body, statuses)
    # The live unit was decided by a search naming ONE of its members, both ways.
    assert writes[0][0][1] == {
        "IMG_7.JPG": "skipped",
        "IMG_7.MOV": "skipped",
        "base.png": "proposed",
        "far.png": "proposed",
    }
    assert writes[2][0][1]["IMG_7.JPG"] == "approved"


def test_q1_no_write_module_reads_the_look_alike_field():
    """About the CODE: the look-alikes are computed in `webui/lookalikes.py` and read by the
    card builder alone. No module that decides, renames, plans or applies mentions them."""
    import pam.execute as execute_pkg
    import pam.naming as naming_pkg
    from pam.webui import decide_api, write_api, write_state

    sources = [Path(m.__file__) for m in (decide_api, write_api, write_state)]
    for package in (execute_pkg, naming_pkg):
        sources.extend(sorted(Path(package.__file__).parent.glob("*.py")))
    assert len(sources) > 10
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert "look_alike" not in text and "lookalike" not in text, path


# --- `Accept all`: the whole plan, whole units, and a mixed unit left alone whole ----------

ACCEPT_FILES = {
    # a Live Photo of three (`.aae` is a default sidecar) - all undecided, not low
    "IMG_1.HEIC": "2019:07:04 18:23:11",
    "IMG_1.MOV": "2019:07:04 18:23:11",
    "IMG_1.AAE": "2019:07:04 18:23:11",
    # two pairs made MIXED by hand below (no endpoint can build one since A1)
    "IMG_2.HEIC": "2020:08:05 09:15:00",
    "IMG_2.MOV": "2020:08:05 09:15:00",
    "IMG_3.HEIC": "2021:09:06 11:02:03",
    "IMG_3.MOV": "2021:09:06 11:02:03",
    "B.JPG": "2022:10:07 12:00:00",
}
LOW = "D.JPG"  # no date anywhere: `undated_NNN`, tier `low`


def _set(root, plan_id, rel, status):
    conn = _conn(root)
    try:
        conn.execute(
            "UPDATE plan_items SET status = ? WHERE plan_id = ? AND old_rel_path = ?",
            (status, plan_id, rel),
        )
        conn.commit()
    finally:
        conn.close()


def _accept_archive(tmp_path):
    root = tmp_path / "photos"
    root.mkdir()
    for name, date in ACCEPT_FILES.items():
        put(root, name, date)
    (root / LOW).write_bytes(b"the only copy of D.JPG, which has no date")
    (root / f"{LOW}.tags.json").write_text(json.dumps({"PamTestSidecar": LOW}), "utf-8")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    return root


def test_q1_accept_all_writes_whole_units_of_the_whole_plan_and_apply_moves_them_whole(
    tmp_path, capsys, monkeypatch, spy
):
    """`Accept all` with NO view: every undecided unit but Low, whole. IMG_2's video was
    SKIPPED by hand and IMG_3's video APPROVED by hand, so both are mixed: Accept all leaves
    each exactly as it was (M13-D1). Then `pam apply`: exactly the units whose every member is
    approved move, whole - IMG_3 (one member approved, one undecided) is left alone WHOLE
    (I2), and the spy sees no rename for any of its members.

    Mutation: `accept_all` decides per ITEM instead of per unit -> IMG_2.HEIC or IMG_3.HEIC
    is approved beside its partner's other decision, and the mixed rows below fail."""
    root = _accept_archive(tmp_path)
    code, out, err = _pam(capsys, "plan", "--archive", str(root))
    assert code == 0, out + err
    plan_id = _open_plan(root)
    items = _items(root, plan_id)
    assert items[LOW]["tier"] == "low"
    assert {items[n]["tier"] for n in ACCEPT_FILES} <= {"high", "medium"}
    _set(root, plan_id, "IMG_2.MOV", "skipped")
    _set(root, plan_id, "IMG_3.MOV", "approved")
    before = _snapshot(root)

    reply = _drive(
        plan_id,
        root,
        lambda server: _ok(
            server,
            "POST",
            f"/plan/{plan_id}/decide-filter",
            {"action": "approve", "accept_all": True},
        ),
    )
    assert reply["decided"] == 4 and reply["excluded_low"] == 1, reply
    assert _statuses(root, plan_id) == {
        "IMG_1.HEIC": "approved",
        "IMG_1.MOV": "approved",
        "IMG_1.AAE": "approved",
        "IMG_2.HEIC": "proposed",
        "IMG_2.MOV": "skipped",
        "IMG_3.HEIC": "proposed",
        "IMG_3.MOV": "approved",
        "B.JPG": "approved",
        LOW: "proposed",
    }

    code, out, err = _pam(
        capsys,
        "apply",
        str(plan_id),
        "--archive",
        str(root),
        typed="apply",
        monkeypatch=monkeypatch,
    )
    # IMG_3's approved video is REFUSED, not renamed alone (spec 7.3 rev 15, F1 R3): the run
    # completes with one per-file refusal, which spec 13 makes exit 1.
    assert code == 1, out + err
    assert "Done with problems. 4 renamed, 1 failed" in out, out
    moved = {
        "IMG_1.HEIC": "2019-07-04_001.HEIC",
        "IMG_1.MOV": "2019-07-04_001.MOV",
        "IMG_1.AAE": "2019-07-04_001.AAE",
        "B.JPG": "2022-10-07_001.JPG",
    }
    assert sorted(spy) == sorted(moved.items())
    assert _snapshot(root) == {moved.get(rel, rel): sha for rel, sha in before.items()}
    _verified_clean(capsys, root, "after the Accept-all apply")


# ============================================================================ Q2
# --- the members a command does not see: `pre_pam_names` is per FILE ----------------------

MTIME = datetime(2024, 5, 1, 12, 0, 0)  # every file's mtime before the first scan
TOUCH = datetime(2025, 3, 3, 12, 0, 0)  # the operating system's touch, after `pam apply`


def _touch(path, when):
    """An mtime-only change: the bytes are untouched, as a sync client or a restore leaves them."""
    os.utime(path, (when.timestamp(), when.timestamp()))


def _bare(root, rel, *, tags=None, stamp=MTIME, content=None):
    """One archive file with NO EXIF unless `tags` says so (the fake exiftool reads
    `<path>.tags.json`), so its date comes from its name or its mtime - S9's case."""
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if content is not None else f"the only copy of {rel}".encode())
    (root / f"{rel}.tags.json").write_text(
        json.dumps({**(tags or {}), "PamTestSidecar": rel}), encoding="utf-8"
    )
    _touch(path, stamp)


def _captures(root):
    """`{rel_path: (capture_dt, capture_dt_source)}` of every present file, the suite's SQL."""
    conn = _conn(root)
    try:
        rows = conn.execute(
            "SELECT f.rel_path, c.capture_dt, c.capture_dt_source FROM capture c"
            " JOIN files f ON f.id = c.file_id WHERE f.status = 'present'"
        ).fetchall()
    finally:
        conn.close()
    return {r[0]: (r[1], r[2]) for r in rows}


def _approve_all_sql(root, plan_id):
    """The suite's own approval, for setup only (`tests/archives.py`'s SQL, spelled here)."""
    conn = _conn(root)
    try:
        conn.execute(
            "UPDATE plan_items SET status = 'approved' WHERE plan_id = ? AND status = 'proposed'",
            (plan_id,),
        )
        conn.commit()
    finally:
        conn.close()


def test_q2_a_unit_one_member_renamed_by_pam_and_one_by_the_user_is_read_per_file(
    tmp_path, capsys, monkeypatch, spy
):
    """A RAW+display pair with no EXIF, named by pam from its mtime (2024-05-01_001.*). The
    USER then renames the RAW case-only (`.NEF` -> `.nef`: the stem is kept, so the unit is
    kept), and the operating system touches both. A fresh `pam scan` reads each member ALONE
    (`pre_pam_names` is per file):

      - the display image is the file pam renamed: its name is NOT evidence (I5, S9/H8), so its
        pre-pam name `pair.png` carries no date and it is dated by the touch (`file_mtime`);
      - the RAW is a NEW `files` row (a case-only rename is a new path) with no journal
        history, so the user's name - whose date pam wrote - IS evidence: `filename`, 2024-05-01.
        RECORDED, NOT A BREACH: design 5.1's residual (a file the user renames or moves is the
        user's), and `tests/test_pam_written_names.py`'s negative control rules it so.

    And the PLAN keeps the two together (S3): one unit_key, one stem, named from the primary;
    one id named through the server approves both; `pam apply` renames both, and the spy sees
    exactly those two renames.

    Mutation: the planner groups by `files.companion_group_id` read per member instead of the
    scan's stem grouping -> the user-renamed member falls out of the unit."""
    root = tmp_path / "photos"
    root.mkdir()
    _bare(root, "pair.png")
    _bare(root, "pair.NEF")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    code, out, err = _pam(capsys, "plan", "--archive", str(root))
    assert code == 0, out + err
    plan_id = _open_plan(root)
    _approve_all_sql(root, plan_id)
    code, out, err = _pam(
        capsys,
        "apply",
        str(plan_id),
        "--archive",
        str(root),
        typed="apply",
        monkeypatch=monkeypatch,
    )
    assert code == 0, out + err
    assert sorted(spy) == [
        ("pair.NEF", "2024-05-01_001.NEF"),
        ("pair.png", "2024-05-01_001.png"),
    ]
    spy.clear()

    os.rename(root / "2024-05-01_001.NEF", root / "2024-05-01_001.nef")  # the user, case only
    for name in ("2024-05-01_001.png", "2024-05-01_001.nef"):
        _touch(root / name, TOUCH)  # the operating system
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert _captures(root) == {
        "2024-05-01_001.png": ("2025-03-03T12:00:00", "file_mtime"),
        "2024-05-01_001.nef": ("2024-05-01T00:00:00", "filename"),
    }

    code, out, err = _pam(capsys, "plan", "--archive", str(root))
    assert code == 0, out + err
    second = _open_plan(root)
    items = _items(root, second)
    assert sorted(items) == ["2024-05-01_001.nef", "2024-05-01_001.png"]
    assert len({row["unit_key"] for row in items.values()}) == 1, items
    assert {rel: row["new_filename"] for rel, row in items.items()} == {
        "2024-05-01_001.png": "2025-03-03_001.png",
        "2024-05-01_001.nef": "2025-03-03_001.nef",
    }
    assert {row["tier"] for row in items.values()} == {"low"}

    before = _snapshot(root)
    reply = _drive(
        second,
        root,
        lambda server: _ok(
            server,
            "POST",
            f"/plan/{second}/decide",
            {"action": "approve", "item_ids": [items["2024-05-01_001.nef"]["id"]]},
        ),
    )
    assert reply["decided"] == 2
    code, out, err = _pam(
        capsys,
        "apply",
        str(second),
        "--archive",
        str(root),
        typed="apply",
        monkeypatch=monkeypatch,
    )
    assert code == 0, out + err
    moved = {
        "2024-05-01_001.png": "2025-03-03_001.png",
        "2024-05-01_001.nef": "2025-03-03_001.nef",
    }
    assert sorted(spy) == sorted(moved.items())
    assert _snapshot(root) == {moved.get(rel, rel): sha for rel, sha in before.items()}
    _verified_clean(capsys, root, "after the second apply")


# ============================================================================ Q3
# --- every write path, ENUMERATED from the route table the server really serves ----------

# The WHOLE live route table at M13, written by hand. M13 adds NO route: `Accept all` is a
# branch of `decide-filter` (F1 R2 / Task 2), and the look-alikes (H3) ride on `GET items`.
READ_ROUTES = {
    ("GET", "ping"),
    ("GET", "plan/{id}"),
    ("GET", "plan/{id}/groups"),
    # M14 Task 2 (U25): the review list's groups, a read.
    ("GET", "plan/{id}/list"),
    ("GET", "plan/{id}/items"),
    ("GET", "thumb/{file_id}"),
    ("GET", "faces/clusters"),
    ("GET", "faces/cluster/{id}"),
    ("GET", "faces/crop/{face_id}"),
    ("GET", "faces/persons"),
    ("GET", "faces/ignored"),
    ("GET", "faces/person/{id}"),
}
# Routes that decide, rename or restore PLAN ITEMS - each must write whole units only.
PLAN_WRITE_ROUTES = {
    ("POST", "plan/{id}/decide"),
    ("POST", "plan/{id}/decide-filter"),
    # M14 Task 3 (U17): the selection's decision - widens over the whole plan like decide-filter.
    ("POST", "plan/{id}/decide-selection"),
    ("POST", "item/{id}/rename"),
    ("POST", "item/{id}/restore"),
}
# Routes that write faces and people and reach no plan item.
FACES_WRITE_ROUTES = {
    ("POST", "faces/cluster/{id}/label"),
    ("POST", "faces/cluster/{id}/dismiss"),
    ("POST", "faces/cluster/{id}/split"),
    ("POST", "faces/faces/state"),
    ("POST", "faces/faces/assign"),
    ("POST", "faces/person/{id}"),
    ("POST", "faces/person/{id}/merge"),
}
# M14 Task 3 (U17): a POST that writes NOTHING. The selection's preview is a POST only because a
# selection is a body; it is classified here rather than as a write, and its own module spies on
# the write and on `total_changes` to prove it.
READ_ONLY_POST_ROUTES = {("POST", "plan/{id}/selection")}
# M14 Task 2: READ_ROUTES 11 -> 12 for U25's `plan/{id}/list`. M14 Task 3: PLAN_WRITE_ROUTES
# 4 -> 5 for `decide-selection`, and READ_ONLY_POST_ROUTES (1) for `selection`.
assert len(READ_ROUTES) == 12 and len(PLAN_WRITE_ROUTES) == 5 and len(FACES_WRITE_ROUTES) == 7
assert len(READ_ONLY_POST_ROUTES) == 1
# M14 Task 6c, RULING B1: `pam review` now serves `pam start`'s server, bound (U5, U10), so the
# table this test reads through `run_review` carries U13's chooser and U18's job and rename routes
# (Tasks 4-6). Before B1 this test also pinned "`pam review`'s server has no job routes"; that was
# Task 5's as-built narrowing, not an owner ruling. Spelled here from U18, by hand. None of them
# decides a plan item: a read job writes a NEW plan through `plan_locked`, and the two moving
# routes rename through `apply_locked` / `undo_locked`, whose preflight contains whole units
# (`tests/test_webui_rename.py`). `POST bind` is NOT served: `pam review` never changes folder.
PAGE_WORKFLOW_ROUTES = {
    ("GET", "folders"),
    ("GET", "job"),
    ("POST", "job/read"),
    ("POST", "job/cancel"),
    ("GET", "plan/{id}/confirmation"),
    ("POST", "plan/{id}/apply"),
    ("GET", "session/{id}"),
    ("POST", "session/{id}/undo"),
    # M14 Task 7 (W22, X2): History's two reads. Neither decides an item or moves a file.
    ("GET", "sessions"),
    ("GET", "session/{id}/report"),
}
assert len(PAGE_WORKFLOW_ROUTES) == 10


def _q3_archive(tmp_path, capsys):
    """IMG_1's Live Photo of three plus two dated singles; scanned and planned."""
    root = tmp_path / "photos"
    root.mkdir()
    for name in ("IMG_1.HEIC", "IMG_1.MOV", "IMG_1.AAE"):
        put(root, name, "2019:07:04 18:23:11")
    put(root, "B.JPG", "2020:08:05 09:15:00")
    put(root, "C.JPG", "2021:09:06 11:02:03")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    code, out, err = _pam(capsys, "plan", "--archive", str(root))
    assert code == 0, out + err
    return root, _open_plan(root)


THE_UNIT = ["IMG_1.AAE", "IMG_1.HEIC", "IMG_1.MOV"]


def test_q3_the_live_route_table_is_exactly_the_hand_written_one_and_has_no_accept_route(
    tmp_path, capsys
):
    """The table the server really serves equals these three lists, entry by entry; no route
    is named for accepting; and the `accept_all` field means nothing to `decide`, the other
    route a page could aim it at - it is refused there for its missing `item_ids` and writes
    nothing.

    Mutation: add any row to `write_api.WRITE_HANDLERS` (or any handler table) -> this fails
    naming the unclassified route."""
    root, plan_id = _q3_archive(tmp_path, capsys)

    def work(server):
        table = set(server.api_handlers)
        status, reply = _call(
            server, "POST", f"/plan/{plan_id}/decide", {"action": "approve", "accept_all": True}
        )
        return table, status, reply

    table, status, reply = _drive(plan_id, root, work)
    expected = (
        READ_ROUTES
        | PLAN_WRITE_ROUTES
        | FACES_WRITE_ROUTES
        | READ_ONLY_POST_ROUTES
        | PAGE_WORKFLOW_ROUTES  # ruling B1 (M14 Task 6c)
    )
    assert table == expected, sorted(table.symmetric_difference(expected))
    assert ("POST", "bind") not in table
    assert not PAGE_WORKFLOW_ROUTES & (READ_ROUTES | PLAN_WRITE_ROUTES | READ_ONLY_POST_ROUTES)
    assert not [route for route in table if "accept" in route[1]]
    assert status == 400, reply
    assert set(_statuses(root, plan_id).values()) == {"proposed"}


@pytest.mark.parametrize(
    ("route", "member"),
    [
        ("decide", "IMG_1.AAE"),
        ("decide-filter", "IMG_1.MOV"),
        # M14 Task 3: the selection's decision, through one ticked member.
        ("decide-selection", "IMG_1.AAE"),
        ("accept-all", None),
        ("rename", "IMG_1.MOV"),
        ("restore", "IMG_1.AAE"),
    ],
)
def test_q3_every_plan_write_route_writes_the_whole_unit_from_one_member(
    tmp_path, capsys, route, member
):
    """Each plan-write route, driven through ONE member of the three-member unit (or, for
    `Accept all`, through no member at all): afterwards the unit's three items carry ONE
    status and ONE stem. `restore` runs after a `rename` of a DIFFERENT member, so the unit
    has something to be restored from.

    Mutation: any write route acting on the named member alone -> the unit holds two statuses
    or two stems."""
    root, plan_id = _q3_archive(tmp_path, capsys)
    items = _items(root, plan_id)
    assert _units(items)[items["IMG_1.HEIC"]["unit_key"]] == THE_UNIT

    def work(server):
        if route == "decide":
            body = {"action": "skip", "item_ids": [items[member]["id"]]}
            return _ok(server, "POST", f"/plan/{plan_id}/decide", body)
        if route == "decide-filter":
            return _ok(
                server, "POST", f"/plan/{plan_id}/decide-filter", {"action": "skip", "q": "MOV"}
            )
        if route == "decide-selection":
            body = {"selection": {"view": {}, "ids": [items[member]["id"]]}, "action": "skip"}
            return _ok(server, "POST", f"/plan/{plan_id}/decide-selection", body)
        if route == "accept-all":
            body = {"action": "approve", "accept_all": True}
            return _ok(server, "POST", f"/plan/{plan_id}/decide-filter", body)
        if route == "rename":
            body = {"new_filename": "Holiday.MOV"}
            return _ok(server, "POST", f"/item/{items[member]['id']}/rename", body)
        _ok(
            server,
            "POST",
            f"/item/{items['IMG_1.HEIC']['id']}/rename",
            {"new_filename": "X.HEIC"},
        )
        return _ok(server, "POST", f"/item/{items[member]['id']}/restore")

    _drive(plan_id, root, work)
    after = _items(root, plan_id)
    assert len({after[m]["status"] for m in THE_UNIT}) == 1, after
    assert len({_stem(after[m]["new_filename"]) for m in THE_UNIT}) == 1, after
    want = {
        "decide": ("skipped", "2019-07-04_001"),
        "decide-filter": ("skipped", "2019-07-04_001"),
        "decide-selection": ("skipped", "2019-07-04_001"),
        "accept-all": ("approved", "2019-07-04_001"),
        "rename": ("approved", "Holiday"),
        "restore": ("approved", "2019-07-04_001"),
    }[route]
    assert {(after[m]["status"], _stem(after[m]["new_filename"])) for m in THE_UNIT} == {want}


# ============================================================================ Q4
# --- `pam faces calibrate` READS: the catalog's bytes, and every workspace file's -----------
#
# M11's and M12's Q4 constructions (`tests/test_m11_safety_constructions.py`,
# `tests/test_m12_safety_constructions.py`) stay where they are and run unchanged in every
# suite run; M13 adds no faces WRITE, so they are not copied. M13's one new faces command is
# below.


EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _workspace_bytes(root):
    """`{path relative to the workspace: sha256}` of every file in the archive's workspace -
    the catalog, its WAL and shared-memory files, the logs, the reports, `pam.toml`."""
    ws = workspace_for(root)
    base = ws.db_path.parent
    found = {}
    for path in sorted(base.rglob("*")):
        if path.is_file():
            found[path.relative_to(base).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return found


@pytest.fixture()
def _faces_stack(monkeypatch):
    """The `[faces]` gate answered "installed" (the venv has numpy and not onnxruntime; the
    sweep needs only numpy) - `tests/test_faces_calibrate.py::stack_present`'s seam."""
    import numpy

    from pam.enrich import faces_model

    monkeypatch.setattr(faces_model, "import_runtime", lambda: (object(), numpy))


def test_q4_pam_faces_calibrate_reads_and_never_writes_a_byte(
    tmp_path, capsys, _faces_stack, canary
):
    """The toy catalog of `tests/test_faces_calibrate.py` (labelled, unnamed, below-floor and
    reviewed faces), and `pam faces calibrate` run twice - with no options and with every
    option. Every byte of every workspace file that existed is the same afterwards, the
    catalog included; the only files that may appear are SQLite's reader-side `-shm` and an
    EMPTY `-wal`; and no session log is written (a lock-free reader, design 12 / S13).

    Mutation: calibrate opens `connect` and commits anything (or writes `pam.toml`) -> a byte
    or a file changes."""
    from test_faces_calibrate import TOY
    from test_faces_calibrate import archive as faces_archive

    root = faces_archive(tmp_path, TOY)
    before = _workspace_bytes(root)
    assert "catalog.db" in before
    for extra in (
        [],
        ["--floor", "0.5", "--exclude-label", "Person G", "--exclude-path", "Scans/*"],
    ):
        code, out, err = _pam(capsys, "faces", "calibrate", "--archive", str(root), *extra)
        assert code == 0, out + err
        assert "epsilon  groups" in out, out
        after = _workspace_bytes(root)
        # Every file that existed is byte-identical - `catalog.db` first among them.
        assert {name: after.get(name) for name in before} == before, extra
        # SQLite's own WAL bookkeeping, and nothing else, may appear: a reader of a WAL
        # database opens the `-shm` index and an EMPTY `-wal` (measured). Neither holds a row.
        new = set(after) - set(before)
        assert new <= {"catalog.db-wal", "catalog.db-shm"}, new
        assert after.get("catalog.db-wal", EMPTY_SHA256) == EMPTY_SHA256
    net_canary.assert_nothing_recorded(canary)  # and it opens no socket at all (S4)


# ============================================================================ Q5
# --- (a) a lock-free reader across `pam apply`'s commit; `pam verify` holds the lock ------
#
# The instrument is M12's (`tests/test_m12_safety_constructions.py`): `pam status` runs in a
# thread, ONE of its eight reads is wrapped to park the reader right after it returns, the
# writer runs to completion in the main thread, and the reader is released - barriers, never
# sleeps. M12 drove `pam plan` and `pam enrich ocr` through it; M13 drives `pam apply`, which
# changes what `pam status` prints in more than one place.


def _approved_q3_archive(tmp_path, capsys):
    root, plan_id = _q3_archive(tmp_path, capsys)
    _approve_all_sql(root, plan_id)
    return root, plan_id


@pytest.mark.parametrize("pause_after", STATUS_READS)
def test_q5_pam_status_read_across_an_apply_reports_before_or_after(
    tmp_path, capsys, monkeypatch, pause_after
):
    """Each reading equals the full `pam status` text taken BEFORE the apply or the one taken
    AFTER it - states that existed - and nothing in between (S13, I4).

    Mutation: remove `status._read_archive`'s `read_snapshot` -> a pause between the plans
    reads and the sessions read reports an applied plan beside "no renames applied"."""
    root, plan_id = _approved_q3_archive(tmp_path, capsys)
    monkeypatch.setattr("builtins.input", lambda: "apply")
    code, out, err = _pam(capsys, "status", "--archive", str(root))
    assert code == 0, out + err
    before, scanned = _masked(out)

    def writer():
        return main(["apply", str(plan_id), "--archive", str(root)])

    seen, scanned_seen = _masked(_status_across(monkeypatch, capsys, root, pause_after, writer))
    code, out, err = _pam(capsys, "status", "--archive", str(root))
    assert code == 0, out + err
    after, _ = _masked(out)
    assert before != after, "the apply changed nothing pam status shows"
    assert scanned_seen == scanned
    assert seen in (before, after), "\n".join(seen)
    assert len(_sessions(root)) == 1, "the writer really committed while the reader was parked"


LOCKED = "Another pam command is already running against this archive."


def test_q5_pam_verify_holds_the_lock_so_an_apply_under_it_renames_nothing(
    tmp_path, capsys, monkeypatch, spy
):
    """`pam verify` is parked INSIDE its checks (after `run_checks` returns, before it
    reports); `pam apply` started then is refused by the workspace lock, attempts no rename
    (the spy) and moves no byte; verify then finishes clean. Once verify ends, the same apply
    succeeds - so it was the lock that refused it.

    Mutation: drop the lock from `run_verify`'s check half -> the apply renames under the
    checks."""
    from pam import verify as verify_mod

    root, plan_id = _approved_q3_archive(tmp_path, capsys)
    monkeypatch.setattr("builtins.input", lambda: "apply")
    reached, release = threading.Event(), threading.Event()
    result = {}
    real = verify_mod.run_checks

    def parked(*args, **kwargs):
        value = real(*args, **kwargs)
        if threading.current_thread() is reader:
            reached.set()
            assert release.wait(60), "never released"
        return value

    monkeypatch.setattr(verify_mod, "run_checks", parked)

    def read():
        result["code"] = main(["verify", "--archive", str(root)])

    reader = threading.Thread(target=read, daemon=True)
    before = _snapshot(root)
    capsys.readouterr()
    reader.start()
    try:
        assert reached.wait(60), "pam verify never reached its checks"
        code = main(["apply", str(plan_id), "--archive", str(root)])
        refused = capsys.readouterr()
        assert reader.is_alive()
    finally:
        release.set()
        reader.join(60)
    assert code == 1, refused.out + refused.err
    assert LOCKED in refused.err, refused.err
    assert spy == []
    assert _snapshot(root) == before
    assert result["code"] == 0

    code, out, err = _pam(capsys, "apply", str(plan_id), "--archive", str(root))
    assert code == 0, out + err
    assert len(spy) == 5


# --- (b) every fact pam derives from a NAME or an MTIME, touched under a fresh scan -------
#
# Read off the code, not assumed (design 5.1, 5.3, 6.4, 7.1):
#   1. the `filename` date step        - the scanner parses a file's NAME (the pre-pam name
#                                        when pam wrote the current one, H8);
#   2. the `file_mtime` date step      - the scanner's last resort, the file's MTIME;
#   3. the P2 zero-work skip           - `(size, mtime)` unchanged -> not re-read;
#   4. the apply-time snapshot         - `perform_verified_rename` refuses a file whose
#                                        `(size, mtime_ns)` moved since the plan;
#   5. the "video date ambiguous" rule - the planner compares a `qt_utc` date with the NAME.
# (Companion grouping by stem and `already_conforming` read names too, but a touch changes
# no name; Q2 is the construction where a name changes.)


def _follow(root, old, new):
    """Move the fake exiftool's tags file after its photo: real EXIF lives INSIDE the bytes
    and travels with a rename, and the fixture's must too or the touch test measures the
    fixture."""
    os.rename(root / f"{old}.tags.json", root / f"{new}.tags.json")


def test_q5_a_touch_after_apply_re_derives_only_what_an_mtime_may_decide(
    tmp_path, capsys, monkeypatch
):
    """Three files pam names and applies: `a.png` dated by its mtime, `b.JPG` by its camera,
    `c.JPG` by its camera and NEVER touched. The OS then touches `a.png` and `b.JPG`.

      1+2. `a.png`'s new name (2024-05-01_001.png) is pam's, so it is not evidence (I5): the
           re-read dates it by the touch (`file_mtime`, 2025-03-03) - RECORDED (Task 5
           review M2: H8 as adopted);
      -    `b.JPG` keeps its camera date (`exif`): a touch cannot move a fact the bytes hold;
      3.   the scan re-reads EXACTLY the two touched files (P2: `c.JPG` is not re-read).
    """
    root = tmp_path / "photos"
    root.mkdir()
    _bare(root, "a.png")
    _bare(root, "b.JPG", tags={"DateTimeOriginal": "2019:07:04 18:23:11"})
    _bare(root, "c.JPG", tags={"DateTimeOriginal": "2020:08:05 09:15:00"})
    run_scan(root, exiftool_command=FAKE_COMMAND)
    code, out, err = _pam(capsys, "plan", "--archive", str(root))
    assert code == 0, out + err
    plan_id = _open_plan(root)
    _approve_all_sql(root, plan_id)
    code, out, err = _pam(
        capsys,
        "apply",
        str(plan_id),
        "--archive",
        str(root),
        typed="apply",
        monkeypatch=monkeypatch,
    )
    assert code == 0, out + err
    for old, new in (("b.JPG", "2019-07-04_001.JPG"), ("c.JPG", "2020-08-05_001.JPG")):
        _follow(root, old, new)
    assert _captures(root) == {
        "2024-05-01_001.png": ("2024-05-01T12:00:00", "file_mtime"),
        "2019-07-04_001.JPG": ("2019-07-04T18:23:11", "exif"),
        "2020-08-05_001.JPG": ("2020-08-05T09:15:00", "exif"),
    }

    for name in ("2024-05-01_001.png", "2019-07-04_001.JPG"):
        _touch(root / name, TOUCH)
    summary = run_scan(root, exiftool_command=FAKE_COMMAND)
    assert (summary.changed, summary.read) == (2, 2)
    assert _captures(root) == {
        "2024-05-01_001.png": ("2025-03-03T12:00:00", "file_mtime"),
        "2019-07-04_001.JPG": ("2019-07-04T18:23:11", "exif"),
        "2020-08-05_001.JPG": ("2020-08-05T09:15:00", "exif"),
    }


def test_q5_a_touch_between_plan_and_apply_refuses_the_whole_unit_and_moves_nothing(
    tmp_path, capsys, monkeypatch, spy
):
    """Fact 4. A Live Photo pair is planned and approved; the OS touches the VIDEO (no scan in
    between). `pam apply`: the touched member fails `perform_verified_rename`'s
    `(size, mtime_ns)` check, the unit is not left split (spec 7.3 - either both members move
    or neither), every byte and path is where it was, and `pam verify` is clean.

    Mutation: drop the mtime operand of the snapshot check -> the touched video is renamed."""
    root = tmp_path / "photos"
    root.mkdir()
    for name in ("IMG_5.HEIC", "IMG_5.MOV"):
        put(root, name, "2019:07:04 18:23:11")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    code, out, err = _pam(capsys, "plan", "--archive", str(root))
    assert code == 0, out + err
    plan_id = _open_plan(root)
    _approve_all_sql(root, plan_id)
    _touch(root / "IMG_5.MOV", TOUCH)
    before = _snapshot(root)

    code, out, err = _pam(
        capsys,
        "apply",
        str(plan_id),
        "--archive",
        str(root),
        typed="apply",
        monkeypatch=monkeypatch,
    )
    assert code == 1, out + err
    assert _snapshot(root) == before
    # Every forward rename that was attempted has its reverse, and the touched video was
    # attempted (and refused by the check) or never reached - it never stayed renamed.
    for old, new in spy:
        if old.startswith("IMG_5"):
            assert (new, old) in spy or old == "IMG_5.MOV", spy
    _verified_clean(capsys, root, "after the refused apply")


# design 6.4's "video date ambiguous" sentence, as pam prints it.
AMBIGUOUS = (
    "Video time is recorded in UTC and the old filename suggests a different day - "
    "please check this one."
)


def _reasons(root, plan_id):
    conn = _conn(root)
    try:
        rows = conn.execute(
            "SELECT old_rel_path, tier, trust_reasons_json FROM plan_items WHERE plan_id = ?",
            (plan_id,),
        ).fetchall()
    finally:
        conn.close()
    return {r[0]: (r[1], json.loads(r[2])) for r in rows}


def test_q5_the_video_rule_after_apply_and_a_touch_compares_with_the_name_before_pam(
    tmp_path, capsys, monkeypatch
):
    """Fact 5, the brief's named suspicion: `consistency._video_date_ambiguous` reads a NAME.
    A video whose camera named it 2023-08-28 carries a UTC stamp of 2023-08-29 06:59:59
    (`qt_utc`): pam names it 2023-08-29_001.MOV and flags it ("the old filename suggests a
    different day"). After `pam apply`, the OS touches it, `pam scan` re-reads it, and a
    fresh `pam plan` (template `{date}`, so the unit is not already conforming) must STILL
    flag it - comparing the stamp with pam's own name (2023-08-29) would silence the flag.

    VERDICT: it does not compare with pam's own name (H8 holds after a touch; the item stays
    `low` with the sentence). No finding."""
    root = tmp_path / "photos"
    root.mkdir()
    _bare(root, "VID_20230828_235959.MOV", tags={"CreateDate": "2023:08:29 06:59:59"})
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert _captures(root) == {"VID_20230828_235959.MOV": ("2023-08-29T06:59:59", "qt_utc")}
    code, out, err = _pam(capsys, "plan", "--archive", str(root))
    assert code == 0, out + err
    plan_id = _open_plan(root)
    first = _reasons(root, plan_id)["VID_20230828_235959.MOV"]
    assert AMBIGUOUS in first[1] and first[0] == "low"
    _approve_all_sql(root, plan_id)
    code, out, err = _pam(
        capsys,
        "apply",
        str(plan_id),
        "--archive",
        str(root),
        typed="apply",
        monkeypatch=monkeypatch,
    )
    assert code == 0, out + err
    _follow(root, "VID_20230828_235959.MOV", "2023-08-29_001.MOV")
    _touch(root / "2023-08-29_001.MOV", TOUCH)
    summary = run_scan(root, exiftool_command=FAKE_COMMAND)
    assert (summary.changed, summary.read) == (1, 1)
    assert _captures(root) == {"2023-08-29_001.MOV": ("2023-08-29T06:59:59", "qt_utc")}

    code, out, err = _pam(capsys, "plan", "--archive", str(root), "--template", "{date}")
    assert code == 0, out + err
    again = _reasons(root, _open_plan(root))["2023-08-29_001.MOV"]
    assert again[0] == "low", again
    assert AMBIGUOUS in again[1], again


# ============================================================================ Q6
# --- F1's: after R1, which path still turns an UNDECIDED item into a rename? --------------
#
# Enumerated, never sampled: `pam apply` (plain; `--all` cancelled; `--all` confirmed), every
# plan-write endpoint with every action it takes, a faces write, and recovery. From a plan in
# which NOTHING is approved, each path runs, then a plain `pam apply` (typed `apply`) runs, and
# the spy says which renames were attempted. The expectation is spelled from T3 / F1 R1-R4:
# ONLY an approval the user made - `approve` (named, filtered or `Accept all`), a typed name
# (UX 5a's `C` is a decision, spec 8.2), or `--all` confirmed - may lead to a rename.

Q6_FILES = {
    "IMG_1.HEIC": "2019:07:04 18:23:11",
    "IMG_1.MOV": "2019:07:04 18:23:11",
    "B.JPG": "2020:08:05 09:15:00",
}
Q6_RENAMES = {
    "IMG_1.HEIC": "2019-07-04_001.HEIC",
    "IMG_1.MOV": "2019-07-04_001.MOV",
    "B.JPG": "2020-08-05_001.JPG",
}
NOTHING_APPROVED = "Nothing in plan {plan_id} is approved."


def _q6_archive(tmp_path, capsys):
    root = tmp_path / "photos"
    root.mkdir()
    for name, date in Q6_FILES.items():
        put(root, name, date)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    code, out, err = _pam(capsys, "plan", "--archive", str(root))
    assert code == 0, out + err
    plan_id = _open_plan(root)
    assert set(_statuses(root, plan_id).values()) == {"proposed"}
    return root, plan_id


# (label, request builder, the files a following plain apply may rename). `ids` maps
# old_rel_path -> plan item id. Every plan-write route x every action it accepts.
ENDPOINT_CASES = [
    ("decide-approve", lambda p, ids: ("/plan/{p}/decide", {"action": "approve", "item_ids": [ids["IMG_1.MOV"]]}), ["IMG_1.HEIC", "IMG_1.MOV"]),
    ("decide-skip", lambda p, ids: ("/plan/{p}/decide", {"action": "skip", "item_ids": [ids["IMG_1.MOV"]]}), []),
    ("decide-reset", lambda p, ids: ("/plan/{p}/decide", {"action": "reset", "item_ids": [ids["IMG_1.MOV"]]}), []),
    ("filter-approve", lambda p, ids: ("/plan/{p}/decide-filter", {"action": "approve", "q": "B.JPG"}), ["B.JPG"]),
    ("filter-skip", lambda p, ids: ("/plan/{p}/decide-filter", {"action": "skip"}), []),
    ("filter-reset", lambda p, ids: ("/plan/{p}/decide-filter", {"action": "reset"}), []),
    ("accept-all", lambda p, ids: ("/plan/{p}/decide-filter", {"action": "approve", "accept_all": True}), ["IMG_1.HEIC", "IMG_1.MOV", "B.JPG"]),
    # M14 Task 3: the selection's decision, every action it accepts.
    ("selection-approve", lambda p, ids: ("/plan/{p}/decide-selection", {"selection": {"view": {}, "ids": [ids["IMG_1.MOV"]]}, "action": "approve"}), ["IMG_1.HEIC", "IMG_1.MOV"]),
    ("selection-skip", lambda p, ids: ("/plan/{p}/decide-selection", {"selection": {"view": {}, "all": True}, "action": "skip"}), []),
    ("selection-reset", lambda p, ids: ("/plan/{p}/decide-selection", {"selection": {"view": {}, "all": True}, "action": "reset"}), []),
    # M14 Task 3 slice 3b: the list's one-tap shortcut (OD-1), a `decide-filter` branch.
    ("shortcut", lambda p, ids: ("/plan/{p}/decide-filter", {"action": "approve", "shortcut": True}), ["IMG_1.HEIC", "IMG_1.MOV", "B.JPG"]),
    ("rename", lambda p, ids: (f"/item/{ids['B.JPG']}/rename", {"new_filename": "Beach.JPG"}), ["B.JPG"]),
    ("restore", lambda p, ids: (f"/item/{ids['B.JPG']}/restore", None), []),
]  # fmt: skip
assert len(ENDPOINT_CASES) == 13  # 9 at M13; +3 at M14 Task 3 (decide-selection); +1 at 3b
# (the shortcut)


@pytest.mark.parametrize(
    ("label", "request_for", "renamed"), ENDPOINT_CASES, ids=[c[0] for c in ENDPOINT_CASES]
)
def test_q6_each_review_write_then_a_plain_apply_renames_only_what_was_approved(
    tmp_path, capsys, monkeypatch, spy, label, request_for, renamed
):
    """Mutation: `restore` (or `skip`/`reset`) writing `approved` -> its case renames files."""
    root, plan_id = _q6_archive(tmp_path, capsys)
    ids = {rel: row["id"] for rel, row in _items(root, plan_id).items()}
    path, body = request_for(plan_id, ids)
    _drive(plan_id, root, lambda server: _ok(server, "POST", path.format(p=plan_id), body))
    before = _snapshot(root)

    code, out, err = _pam(
        capsys,
        "apply",
        str(plan_id),
        "--archive",
        str(root),
        typed="apply",
        monkeypatch=monkeypatch,
    )
    names = {rel: ("Beach.JPG" if label == "rename" else Q6_RENAMES[rel]) for rel in renamed}
    assert sorted(spy) == sorted(names.items()), (label, spy)
    assert _snapshot(root) == {names.get(rel, rel): sha for rel, sha in before.items()}
    if renamed:
        assert code == 0, out + err
    else:
        assert code == 1, out + err
        assert NOTHING_APPROVED.format(plan_id=plan_id) in err, err


def test_q6_apply_all_cancelled_approves_nothing_and_a_plain_apply_after_it_is_refused(
    tmp_path, capsys, monkeypatch, spy
):
    """`pam apply N --all` answered with anything but `apply` writes NO approval (F1 R4: the
    `--all` approvals are written after the typed confirmation), so the next plain apply is
    refused and nothing is ever renamed. `--all` CONFIRMED is the user's explicit approval and
    renames every non-Low unit - the only CLI path from undecided to renamed."""
    root, plan_id = _q6_archive(tmp_path, capsys)
    before = _snapshot(root)
    code, out, err = _pam(
        capsys,
        "apply",
        str(plan_id),
        "--all",
        "--archive",
        str(root),
        typed="no",
        monkeypatch=monkeypatch,
    )
    assert set(_statuses(root, plan_id).values()) == {"proposed"}, (code, out, err)
    code, out, err = _pam(
        capsys,
        "apply",
        str(plan_id),
        "--archive",
        str(root),
        typed="apply",
        monkeypatch=monkeypatch,
    )
    assert code == 1 and NOTHING_APPROVED.format(plan_id=plan_id) in err, out + err
    assert spy == [] and _snapshot(root) == before

    code, out, err = _pam(
        capsys,
        "apply",
        str(plan_id),
        "--all",
        "--archive",
        str(root),
        typed="apply",
        monkeypatch=monkeypatch,
    )
    assert code == 0, out + err
    assert sorted(spy) == sorted(Q6_RENAMES.items())


def test_q6_a_faces_write_on_a_planned_archive_decides_no_plan_item(
    tmp_path, capsys, monkeypatch, spy
):
    """M12's faces archive (four dated photographs, one cluster) is PLANNED, then its cluster
    is labelled through the faces route. The plan's items are exactly as undecided as before,
    and a plain apply is refused and renames nothing."""
    from test_m12_safety_constructions import _faces_archive
    from test_webui_faces import Served

    root, _blair, _faces = _faces_archive(tmp_path)
    code, out, err = _pam(capsys, "plan", "--archive", str(root))
    assert code == 0, out + err
    plan_id = _open_plan(root)
    statuses = _statuses(root, plan_id)
    assert set(statuses.values()) == {"proposed"} and len(statuses) == 4
    served = Served(root)
    try:
        status, written = served.json("POST", "/faces/cluster/2/label", {"person": "Person E"})
    finally:
        served.close()
    assert status == 200, written
    assert _statuses(root, plan_id) == statuses
    code, out, err = _pam(
        capsys,
        "apply",
        str(plan_id),
        "--archive",
        str(root),
        typed="apply",
        monkeypatch=monkeypatch,
    )
    assert code == 1 and NOTHING_APPROVED.format(plan_id=plan_id) in err, out + err
    assert spy == []


@pytest.mark.parametrize("checkpoint", ["after_intent", "after_rename", "before_done_commit"])
def test_q6_recovery_never_renames_an_undecided_item(
    tmp_path, capsys, monkeypatch, spy, set_fault, checkpoint
):
    """The brief's construction. `B.JPG` approved, the Live Photo UNDECIDED; `pam apply`
    crashes at `checkpoint` (its first hit is `B.JPG`'s, the only approved unit); the next
    archive-scoped command (`pam verify`) runs spec 7.2's recovery. `recover._UNATTEMPTED`
    names `proposed` - and still the undecided files keep their names and bytes, the spy
    never sees a rename of them (forward, back or recovery's), and `pam verify` is clean.

    Mutation: recovery (or apply) acting on `proposed` -> the Live Photo's renames appear."""
    root, plan_id = _q6_archive(tmp_path, capsys)
    _set(root, plan_id, "B.JPG", "approved")
    before = _snapshot(root)
    set_fault.arm(f"crash:{checkpoint}:1")
    code, out, err = _pam(
        capsys,
        "apply",
        str(plan_id),
        "--archive",
        str(root),
        typed="apply",
        monkeypatch=monkeypatch,
    )
    assert set_fault.hits(checkpoint) == 1, (out, err)
    assert f"PAM_FAULT: crashed at {checkpoint} hit 1" in out + err
    set_fault.next_invocation()

    _verified_clean(capsys, root, f"after recovery from {checkpoint}")
    after = _snapshot(root)
    for rel in ("IMG_1.HEIC", "IMG_1.MOV"):
        assert after.get(rel) == before[rel], rel
        assert not [call for call in spy if rel in call], spy
    # B.JPG is either where it was or under its approved name - nowhere else, bytes intact.
    b_now = [rel for rel, sha in after.items() if sha == before["B.JPG"]]
    assert b_now in (["B.JPG"], ["2020-08-05_001.JPG"]), b_now
    assert _statuses(root, plan_id)["IMG_1.HEIC"] == "proposed"
    assert _statuses(root, plan_id)["IMG_1.MOV"] == "proposed"
