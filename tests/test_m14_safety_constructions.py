"""M14's close (plan Task 8): the chain twice - through the PAGE and through the CONSOLE - and the hunt.

WHY THIS FILE EXISTS. A property no single module owns is invisible to a mutation pass scoped to
one task's files (CLAUDE.md, "the mutation gate"): M8's 232 rows could not reach `pam apply`
splitting a unit, M9's 392 could not reach F12, M11's review found two defects BETWEEN commands.
M14 gave a user's photographs a SECOND way to be renamed - the review page reads, renames and
undoes, through one door (U19: `webui/rename_jobs.py` -> `apply_locked` / `undo_locked`). So the
vision's safety properties - S1-S4 and T3 ("nothing is ever renamed without an explicit approval
action in that session") - are taken one at a time and the violation is BUILT end to end, driving
the real commands (`pam.cli.main`), the real `pam start` server over loopback HTTP (in-process, so
the one verified rename can be watched), and, for the chain, the real `pam start` in a SUBPROCESS
measured with `tools/ht_manifest.py` (Task 6 Stage 2's harness, imported, never copied).

STEP 1 - THE CHAIN, TWICE, on two scratch copies of the spec 9.1 tiny library, real exiftool.
  The PAGE: `pam start <folder>` in its own process -> `Start reading` (the read job) -> a
  selection naming ONE id per chosen unit, low units among them (`include_low`, `expected_low`)
  -> its preview -> Confirm -> the rename job -> `ht_manifest changed` names exactly the spelled
  renames -> the server stopped, `pam verify` clean -> a second `pam start` undoes the session
  from its `session/{id}` row -> `ht_manifest verify --exact`, and `pam verify` clean again.
  The CONSOLE: `pam scan`, `pam plan`, the same items approved BY ID through `decide`, `pam apply`
  (watched by the spy), `pam undo`. The two plans (every path's proposed name and tier) and the
  two renamed-path sets are IDENTICAL: one implementation behind two triggers.

STEP 2 (8a's half) - FIVE QUESTIONS:

  Q1  Which filter dimensions are NOT unit-uniform? M14's new ones are the list's own: a LIST
      GROUP (U25), `except_ids`, a Shift+click RANGE, and a `Show N more` BOUNDARY. The fixture's
      Live Photo carries an `.AAE` sidecar with NO capture row (so event membership is not
      unit-uniform at the member level - M8's measurement), and its three members are listed in
      a group whose first page holds ONE of them. Each selection is built to name a proper subset
      of the unit; each writes the whole unit, and the page's apply then renames it whole.
  Q2  Which members of a unit does each command NOT see? The page shows a member and not its
      partners (they are on a page not loaded). The selection PREVIEW, the CONFIRM screen's
      counts, and the rename job all count and act on the partners the user never saw.
  Q3  Does every write path still resolve through `_widen_to_units` over the whole plan? The
      route table of `pam start`'s server is ENUMERATED AT RUNTIME and compared, in both
      directions, with a hand-written table: every read (Task 7's `GET sessions` and
      `GET session/{id}/report` among them) is driven and must change no catalog row and rename
      nothing; every plan write - `decide`, `decide-filter`, its `accept_all` and `shortcut`
      branches, `decide-selection`, `rename`, `restore` - is driven through ONE member and writes
      the whole unit; the two moving routes act on whole units through the preflight.
  Q4  Which faces does an unnamed-cluster operation see? The faces WRITE code is unchanged by
      M14 (since `v0.1-m13` only `crop.py`'s live-placeholder read, V1, and where `face_crop` reads
      the archive root, R1-F1, changed), and the restyle (U20) is the page's. M11's construction is RE-RUN here through `pam start`'s bound server (the
      server the redesigned page talks to): `label` and `dismiss` write exactly the unnamed
      faces the queue offered, and a cluster whose every face is named is refused by name.
  Q9  Does every path that calls `perform_verified_rename` pass a run's `DirectoryNames`? Its call
      sites are ENUMERATED by AST over `pam/` (one, `execute/unit.py`) together with every call
      on the way to it, against a hand-written list, both directions; every such call passes
      `names=names`. Then each PATH - `pam apply`, `pam undo`, the revert inside each, recovery's
      revert, and the page's rename and undo jobs and its recovery - is driven over ONE flat
      folder of FLAT_PHOTOS photographs, counting the occupancy check's `os.scandir` calls: one
      per directory per run.

WHAT EACH FOUND, at 8a's commit: NO breach of S1-S4 or T3, and no H-F finding.
  Step 1: the page and the console proposed the same name and tier for all 106 paths in the same
      95 units; the review chose 10 units (17 items, 3 of them low) by naming one id each; both
      renamed the same 17 paths, both undid to the exact original, `pam verify` clean throughout.
  Q1: the list group, `except_ids`, the range and the page boundary each named a proper subset of
      the Live Photo and each wrote the whole unit. (The list group cannot exercise the widening:
      U25 makes the group the UNIT's, so the group already holds every member - the first layer,
      measured; the other three reach `_widen_to_units` and fail without it.)
  Q2: the preview (3 items, 1 unit), the group's `total` (3) and Confirm's `renames` (3) all count
      the two members the page never loaded; the job renamed exactly those three.
  Q3: the bound `pam start` table is exactly the 36 routes below; 18 reads and the preview changed
      no catalog row; all seven plan-write forms wrote whole units; the page's apply refused a
      hand-built mixed unit whole (its approved member `failed`, nothing of it renamed).
  Q4: label and dismiss wrote exactly the two unnamed faces the queue offered; the all-named
      cluster was a 409 naming the person.
  Q9: seven calls on the way to the verified rename, one module calling it, every call passing
      `names=names`; all eight paths listed the flat folder once per run.

STEP 2 (8b's half) - Q5, Q6, Q7 AND Q8:

  Q5  What does a write in one command do to an artefact another produced, or a reader still
      open? Five artefacts: the CONFIRM screen open while another tab approves MORE (and then
      skips what it confirmed); a selection PREVIEW held across a read job (sent to the old plan
      and, with the old ids, to the new one); the LOOK-ALIKE memory across a new plan, after the
      OS deleted one of a look-alike pair; a page left OPEN across a console `pam scan` after the
      server stopped (its old token, its stale nonce, its superseded plan); and THE OS: an
      mtime-only touch between Confirm and Rename.
  Q6  After F1, which path still turns an undecided item into a rename? The page's apply (a
      plan with nothing approved has no Confirm), the selection (its preview; `select all` with
      a low name and no `include_low`), the shortcuts (`Accept all`, a `q=` shortcut), and
      recovery after a page rename died mid-unit.
  Q7  Which request reaches a rename without a person at the page? TEN requests, built against
      the bound server in the dangerous state (names approved, a Confirm open, its nonce
      current): no token, no nonce, a replayed nonce, a superseded plan's nonce, a GET, a form
      POST, a foreign Origin, a rebound Host, two tabs racing one nonce, the CLI concurrently -
      and the CONTROL, the person's own click, which renames the four.
  Q8  What does pam decide from the CATALOG'S copy of a fact the OS can change without changing
      a file's name, size or mtime? The REAL `FILE_ATTRIBUTE_OFFLINE` (Task 0B's construction)
      set between the read and the page's use, measured by Task 0B's read spy on four readers:
      the list's thumbnail, the folder chooser, the read job (and its progress), and Confirm's
      counts with the rename and the undo after them. Windows only (the attribute).

WHAT 8b's Q5-Q7 FOUND: NO breach of S1-S4 or T3, and no H-F finding.
  Q5: the Confirm screen's nonce was cancelled with U14's sentence both when another tab added a
      name and when it removed one; the held selection was refused by the superseded plan (409)
      and by the new plan (400, an id not of this plan); P2's look-alikes were computed afresh
      (the deleted copy named nowhere); the old page's token was the empty 404 and its nonce
      U14's 409 on the new server. THE OS CASE, from the spec: Confirm does NOT refuse an
      mtime-only touch (its digest is the `(item id, new name)` pairs, U14) - the rename's own
      disk check refuses the touched file (design 7.1 step 1) and puts its unit back whole
      (7.3), so the run renamed exactly A.JPG of the five approved.
  Q6: no path renamed an undecided item; recovery only put back.
  Q7: all ten renamed nothing beyond the one click the replay and the race began with.
  Q8: nothing read: the thumbnail is a 404 (A.JPG's, the control, was extracted); the chooser
      opened no file; the read job read the new C.JPG (the control) and not B.JPG, flagged it a
      placeholder and gave it no item in the new plan. Confirm counted B.JPG and the job renamed
      and undid it reading nothing (an `os.stat`, design 7.1 step 1) - pinned as measured: the
      placeholder flag licenses READS (design 5.2 rev 34), and whether a rename of a now
      cloud-only file should be refused is left to the design model (8b's report, Q8-D1).

THE TASK 8 REVIEW ADDED (test-only):
  Q7 on the OTHER moving route: the same ten requests against `session/{id}/undo` with S's undo
      nonce current, and their control (the person's `Undo` puts four back). The fourth case
      is the superseded plan's twin: a nonce issued for ANOTHER session, sent while that
      session's own nonce is current - the only case that reaches `Nonces.take`'s comparison.
  Q5, the review's own construction: History's undo of a session S1 whose file a LATER page
      rename (S2) moved again. S1's undo is handed `Holiday.JPG`, refused on its disk check
      (design 7.4), moves nothing, leaves the item `applied` and S1 undoable; S2 then S1 put
      the photograph back with its own bytes. FOUND: no breach.
  Q8: a control for the `reads` fixture's two `open` hooks, which this module re-installs.

STEP 3 - the canary (`tests/test_net_canary.py`) drives the page's chain - read, rename, undo -
inside the socket watch, asserting the work and every record loopback (LOAD_BEARING_CONTROLS).
STEP 4 - the hunt's own floors: `HUNT` lists every test by question with a spelled floor per
question, checked at collection and again by `test_the_hunt_holds_every_question_at_its_floor`;
Q7's ten requests have their own spelled floor; the census lists this module (the outside floor).

EVERY EXPECTATION IS SPELLED FROM THE SPEC OR THE FIXTURE, never read back out of the production
call it checks: statuses, units and names are read with the suite's own SQL, the route table and
the call-site list are literals, the disk is walked and hashed here (or by `ht_manifest`), and the
dangerous call itself - `perform_verified_rename` - is watched by a spy (M697).
"""

import ast
import datetime
import json
import os
import sqlite3
import sys
import threading
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path

import pytest
import test_m13_safety_constructions as m13
import test_webui_rename_stage2 as stage2
from archives import FAKE_COMMAND, materialise_tiny_library, put
from test_faces_cluster import GEOMETRY, blob
from test_webui_jobs import await_state
from test_webui_rename import _quiet, call

from pam.catalog.db import connect
from pam.cli import main
from pam.execute import unit as unit_mod
from pam.naming.planner import run_plan
from pam.paths import workspace_if_exists
from pam.scan import exiftool as exiftool_mod
from pam.scan.runner import run_scan
from pam.webui import launcher

REPO = Path(__file__).resolve().parent.parent
VERIFY_CLEAN = "Done. 12 checks, everything pam recorded looks right."


@pytest.fixture()
def spy(monkeypatch):
    """Every (old_rel, new_rel) handed to the one verified rename - forward, back, recovery's
    revert, in-process and in a page job's thread alike (`execute/unit.py` is its only caller)."""
    calls: list[tuple[str, str]] = []
    real = unit_mod.perform_verified_rename

    def watching(*args, **kwargs):
        assert args == (), "perform_verified_rename takes keyword arguments only"
        calls.append((kwargs["old_rel"], kwargs["new_rel"]))
        return real(*args, **kwargs)

    monkeypatch.setattr(unit_mod, "perform_verified_rename", watching)
    return calls


@contextmanager
def served(root: Path) -> Iterator[launcher.StartServer]:
    """`pam start`'s server, serving, BOUND to `root` exactly as `POST bind` binds it."""
    server = launcher.start_server(_quiet())
    # The read job's exiftool (`StartServer.exiftool_command`, the launcher's own test seam):
    # the stand-in that reads the fixtures' `.tags.json` dates, as `run_scan` does here.
    server.exiftool_command = list(FAKE_COMMAND)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        launcher.bind(server, root)
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=30)


def _ok(server, path, method="GET", body=None):
    reply = call(server, path, method, body)
    assert reply.status == 200, (method, path, reply.status, reply.body[:300])
    return reply.json()


def _ro(root: Path) -> sqlite3.Connection:
    ws = workspace_if_exists(root)
    assert ws is not None
    conn = sqlite3.connect(f"file:{ws.db_path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _items(root: Path, plan_id: int) -> dict[str, dict]:
    """`{old_rel_path: row}` of one plan, with this module's own SQL."""
    conn = _ro(root)
    try:
        rows = conn.execute(
            "SELECT id, old_rel_path, new_filename, unit_key, tier, status FROM plan_items"
            " WHERE plan_id = ?",
            (plan_id,),
        ).fetchall()
    finally:
        conn.close()
    return {row["old_rel_path"]: dict(row) for row in rows}


def _statuses(root: Path, plan_id: int) -> dict[str, str]:
    return {rel: row["status"] for rel, row in _items(root, plan_id).items()}


def _set(root: Path, plan_id: int, rel: str, status: str) -> None:
    """Setup only: a status written by hand (a state no endpoint can build since A1)."""
    conn = connect(workspace_if_exists(root).db_path)  # type: ignore[union-attr]
    try:
        conn.execute(
            "UPDATE plan_items SET status = ? WHERE plan_id = ? AND old_rel_path = ?",
            (status, plan_id, rel),
        )
        conn.commit()
    finally:
        conn.close()


def _catalog_dump(root: Path) -> list[str]:
    """Every row of every table, as SQL text, read here: a read that wrote would show."""
    conn = _ro(root)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def _page_apply(server, plan_id: int) -> dict:
    """Confirm, then the rename job, awaited: the finished job's snapshot."""
    facts = _ok(server, f"plan/{plan_id}/confirmation")
    body = {"nonce": facts["nonce"], "folder_confirmed": True}
    _ok(server, f"plan/{plan_id}/apply", "POST", body)
    return await_state(server, "done", "failed", "cancelled")


def _page_undo(server, session: str) -> dict:
    facts = _ok(server, f"session/{session}")
    _ok(server, f"session/{session}/undo", "POST", {"nonce": facts["nonce"]})
    return await_state(server, "done", "failed", "cancelled")


# ============================================================================ STEP 1

# Spec 9.1's library as `materialise_tiny_library` lays it down: 99 committed files plus the
# seven reserved device stems git cannot hold (`tests/archives.py`).
TINY_FILES = 106


def _real_env() -> dict[str, str]:
    """The subprocess's environment: this test's isolated `PAM_HOME`, the REAL exiftool on PATH
    (the tiny library's dates are real EXIF), unbuffered UTF-8, and no `PAM_FAULT`."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("PAM_FAULT", None)
    return env


def _units(items: dict[str, dict]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for rel, row in items.items():
        found.setdefault(row["unit_key"], []).append(rel)
    return {key: sorted(members) for key, members in found.items()}


def _chosen_units(items: dict[str, dict]) -> set[str]:
    """The review's choice, written HERE: every companion unit, the first two singles that are
    not low, and the first two low units - so the selection carries low names (U3)."""
    units = _units(items)
    low = sorted(k for k, m in units.items() if items[m[0]]["tier"] == "low")
    multi = sorted(k for k, m in units.items() if len(m) > 1)
    singles = sorted(k for k, m in units.items() if len(m) == 1 and k not in low)
    return set(multi) | set(singles[:2]) | set(low[:2])


def _moves(items: dict[str, dict], chosen: set[str]) -> dict[str, str]:
    """Design 7.1, spelled: every member of a chosen unit moves to `<folder>/<new_filename>`
    unless it already carries it (the no-op item)."""
    moves = {}
    for rel, row in items.items():
        if row["unit_key"] in chosen:
            folder = rel.rpartition("/")[0]
            target = f"{folder}/{row['new_filename']}" if folder else row["new_filename"]
            if target != rel:
                moves[rel] = target
    return moves


@pytest.mark.skipif(sys.platform != "win32", reason="Task 6 Stage 2's harness is Windows-only")
def test_step1_the_chain_through_the_page_and_through_the_console_is_one_implementation(
    tmp_path, capsys, monkeypatch, spy
):
    """THE PAGE (a real `pam start` subprocess, real exiftool, `ht_manifest` as the instrument),
    then THE CONSOLE (`pam.cli.main` on a second copy, the spy as the instrument); the two plans
    and the two renamed-path sets are identical.

    Mutation: the read job plans with anything but `plan_locked`'s own inputs (another template,
    another order) -> the plans differ; the page's apply renames a member the selection did not
    widen to -> `changed` names a path the spelled set does not."""
    # --- THE PAGE -------------------------------------------------------------------------
    page_root = materialise_tiny_library(tmp_path / "page")
    manifest = tmp_path / "page-before.json"
    stage2._snapshot(page_root, manifest, TINY_FILES)
    page_hashes = m13._snapshot(page_root)
    env = _real_env()
    server = stage2.Server(env, "start", str(page_root))
    try:
        plan_id = stage2._read(server)
        page_items = _items(page_root, plan_id)
        assert {row["status"] for row in page_items.values()} == {"proposed"}
        units = _units(page_items)
        chosen = _chosen_units(page_items)
        named = [page_items[units[key][0]]["id"] for key in sorted(chosen)]
        widened = {rel for key in chosen for rel in units[key]}
        low = sum(1 for rel in widened if page_items[rel]["tier"] == "low")
        assert low > 0, "the selection must carry low names"
        selection = {"view": {}, "ids": named}
        status, preview = server.post(f"api/plan/{plan_id}/selection", {"selection": selection})
        assert status == 200, preview
        assert (preview["items"], preview["units"], preview["low"]) == (
            len(widened),
            len(chosen),
            low,
        )
        answer = stage2._approve_selection(server, plan_id, selection, low)
        assert answer["low_included"] == low, answer
        approved = {
            r for r, row in _items(page_root, plan_id).items() if row["status"] == "approved"
        }
        assert approved == widened
        stage2._verify_exact(page_root, manifest, TINY_FILES)  # deciding renamed nothing

        moves = _moves(page_items, chosen)
        facts = stage2._confirm(server, plan_id)
        assert facts["renames"] == len(moves), facts
        assert facts["not_approved"] == len(page_items) - len(widened), facts
        finished = server.await_job(stage2._apply(server, plan_id, facts["nonce"]))
        assert (finished["kind"], finished["state"]) == ("rename", "done"), finished
        assert (finished["result"]["renamed"], finished["result"]["failed"]) == (len(moves), 0)
        session = finished["session"]
        # `ht_manifest changed` PASSES for exactly K (its RENAMED list stops at ten names, so the
        # pairs themselves are checked by this module's own hash walk, path by path).
        changed = stage2._manifest(
            "changed", str(manifest), str(page_root), "--expect", str(len(moves))
        )
        assert changed.returncode == 0, changed.stdout
        assert m13._snapshot(page_root) == {
            moves.get(r, r): sha for r, sha in page_hashes.items()
        }
        assert server.interrupt() == 0
    finally:
        server.close()
    verified = stage2._pam(env, "verify", "--archive", str(page_root))
    assert verified.returncode == 0 and VERIFY_CLEAN in verified.stdout, verified.stdout
    again = stage2.Server(env, "start", str(page_root))
    try:
        undone = stage2._undo(again, session)
        assert (undone["result"]["restored"], undone["result"]["failed"]) == (len(moves), 0)
        assert again.interrupt() == 0
    finally:
        again.close()
    stage2._verify_exact(page_root, manifest, TINY_FILES)
    verified = stage2._pam(env, "verify", "--archive", str(page_root))
    assert verified.returncode == 0 and VERIFY_CLEAN in verified.stdout, verified.stdout

    # --- THE CONSOLE ----------------------------------------------------------------------
    cli_root = materialise_tiny_library(tmp_path / "cli")
    original = m13._snapshot(cli_root)
    code, out, err = m13._pam(capsys, "scan", str(cli_root))
    assert code == 0, out + err
    code, out, err = m13._pam(capsys, "plan", "--archive", str(cli_root))
    assert code == 0, out + err
    cli_plan = m13._open_plan(cli_root)
    cli_items = _items(cli_root, cli_plan)

    # ONE IMPLEMENTATION: the same paths, the same proposed names, the same tiers, the same units.
    def plan_of(items):
        return {rel: (row["new_filename"], row["tier"]) for rel, row in items.items()}

    assert plan_of(cli_items) == plan_of(page_items)
    assert sorted(_units(cli_items).values()) == sorted(units.values())

    ids = [cli_items[rel]["id"] for rel in sorted(widened)]
    reply = m13._drive(
        cli_plan,
        cli_root,
        lambda s: m13._ok(
            s, "POST", f"/plan/{cli_plan}/decide", {"action": "approve", "item_ids": ids}
        ),
    )
    assert reply["decided"] == len(widened)
    code, out, err = m13._pam(
        capsys,
        "apply",
        str(cli_plan),
        "--archive",
        str(cli_root),
        typed="apply",
        monkeypatch=monkeypatch,
    )
    assert code == 0, out + err
    assert dict(spy) == moves, "the console renamed a different set from the page"
    assert m13._snapshot(cli_root) == {moves.get(r, r): sha for r, sha in original.items()}
    spy.clear()
    code, out, err = m13._pam(
        capsys, "undo", m13._sessions(cli_root)[-1], "--archive", str(cli_root)
    )
    assert code == 0, out + err
    assert sorted(spy) == sorted((new, old) for old, new in moves.items())
    assert m13._snapshot(cli_root) == original
    # Measured at 8a's commit: see `MEASURED_CHAIN`.
    assert (len(units), len(chosen), len(widened), low, len(moves)) == MEASURED_CHAIN


# (units in the plan, units chosen, items approved, low items among them, files renamed) -
# measured at 8a's commit on the tiny library and pinned, as M13 pinned its chain.
MEASURED_CHAIN = (95, 10, 17, 3, 17)


# ============================================================================ Q1 and Q2

# The fixture, by hand: a Live Photo of three (`.AAE` is a default sidecar and gets NO capture
# row), two dated singles, and an undated single (`low`, named by the mtime the fixture gives it).
Q_DATES = {
    "A.JPG": "2018:01:01 10:00:00",
    "IMG_1.HEIC": "2019:07:04 18:23:11",
    "IMG_1.MOV": "2019:07:04 18:23:11",
    "IMG_1.AAE": "2019:07:04 18:23:11",
    "B.JPG": "2020:08:05 09:15:00",
}
LOW_SINGLE = "D.JPG"
LIVE = ["IMG_1.AAE", "IMG_1.HEIC", "IMG_1.MOV"]
# Spelled from spec 6.1 (`{date}_{seq}`, extension case kept) and the fixture's dates.
Q_NEW = {
    "A.JPG": "2018-01-01_001.JPG",
    "B.JPG": "2020-08-05_001.JPG",
    "D.JPG": "2015-06-07_001.JPG",
    "IMG_1.AAE": "2019-07-04_001.AAE",
    "IMG_1.HEIC": "2019-07-04_001.HEIC",
    "IMG_1.MOV": "2019-07-04_001.MOV",
}
# The list's groups (U25), spelled: one event per date here, in date order, the unit's ONE key.
Q_GROUPS = {
    "e1": ["D.JPG"],
    "e2": ["A.JPG"],
    "e3": LIVE,
    "e4": ["B.JPG"],
}
LIVE_GROUP = "e3"
# `items?list_group=e3`'s total order, spelled (the page's `Show N more` asks for offsets in it).
LIVE_ORDER = ["IMG_1.AAE", "IMG_1.HEIC", "IMG_1.MOV"]
# The whole list's order (`items`, no view): the low single first, then by path.
LIST_ORDER = ["D.JPG", "A.JPG", "B.JPG", "IMG_1.AAE", "IMG_1.HEIC", "IMG_1.MOV"]


def _q_archive(tmp_path: Path) -> tuple[Path, int]:
    root = tmp_path / "photos"
    root.mkdir()
    for name, date in Q_DATES.items():
        put(root, name, date)
    (root / LOW_SINGLE).write_bytes(b"the only copy of D.JPG, which has no date")
    (root / f"{LOW_SINGLE}.tags.json").write_text(
        json.dumps({"PamTestSidecar": LOW_SINGLE}), encoding="utf-8"
    )
    stamp = datetime.datetime(2015, 6, 7, 12, 0, 0).timestamp()
    os.utime(root / LOW_SINGLE, (stamp, stamp))
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    assert plan_id is not None
    items = _items(root, plan_id)
    assert {rel: row["new_filename"] for rel, row in items.items()} == Q_NEW
    assert _units(items)[items["IMG_1.HEIC"]["unit_key"]] == LIVE
    assert {rel for rel, row in items.items() if row["tier"] == "low"} == {LOW_SINGLE}
    return root, plan_id


def _capture_rows(root: Path) -> set[str]:
    conn = _ro(root)
    try:
        rows = conn.execute(
            "SELECT f.filename FROM capture c JOIN files f ON f.id = c.file_id"
        ).fetchall()
    finally:
        conn.close()
    return {row[0] for row in rows}


def _ids(root, plan_id, *names):
    items = _items(root, plan_id)
    return [items[name]["id"] for name in names]


def _page(server, plan_id, query):
    reply = _ok(server, f"plan/{plan_id}/items?{query}")
    return [card["old_rel_path"] for card in reply["items"]], reply["total"]


def _preview(server, plan_id, selection):
    return _ok(server, f"plan/{plan_id}/selection", "POST", {"selection": selection})


def _decide_selection(server, plan_id, selection, action="approve"):
    return _ok(
        server,
        f"plan/{plan_id}/decide-selection",
        "POST",
        {"selection": selection, "action": action},
    )


def test_q1_a_list_group_holds_every_member_of_a_unit_whose_sidecar_has_no_capture(
    tmp_path, spy
):
    """The precondition, then the property. The `.AAE` has NO capture row (so a per-member event
    rule would put it in no event, M8's measurement), yet the list puts all three members in ONE
    group, every unit in exactly one group, and the group's `total` counts the sidecar. Selecting
    that group by its key decides the whole unit and nothing else; nothing is renamed.

    Mutation: `listing.unit_keys` keyed per MEMBER -> the `.AAE` falls into `undated` and the
    group holds two of three."""
    root, plan_id = _q_archive(tmp_path)
    assert "IMG_1.AAE" not in _capture_rows(root) and "IMG_1.HEIC" in _capture_rows(root)
    with served(root) as server:
        listed = _ok(server, f"plan/{plan_id}/list")
        groups = {g["key"]: g["total"] for g in listed["groups"]}
        members = {
            key: sorted(_page(server, plan_id, f"list_group={key}&limit=200")[0])
            for key in groups
        }
        preview = _preview(server, plan_id, {"view": {}, "groups": [LIVE_GROUP]})
        written = _decide_selection(server, plan_id, {"view": {}, "groups": [LIVE_GROUP]})
    assert members == Q_GROUPS
    assert groups == {key: len(names) for key, names in Q_GROUPS.items()}
    assert (preview["items"], preview["units"], preview["low"]) == (3, 1, 0)
    assert written["decided"] == 3, written
    assert _statuses(root, plan_id) == {
        name: ("approved" if name in LIVE else "proposed") for name in Q_NEW
    }
    assert spy == []


def test_q1_except_ids_naming_one_member_is_widened_back_and_named_overridden(tmp_path, spy):
    """`all` minus ONE member of the Live Photo: U17 widens LAST, so the member comes back with
    its partners and `overridden` names it. `all` minus EVERY member leaves the unit out whole.

    Mutation: widening BEFORE subtracting `except_ids` -> the MOV is left undecided while the
    HEIC and AAE are skipped (vision S3)."""
    root, plan_id = _q_archive(tmp_path)
    (mov,) = _ids(root, plan_id, "IMG_1.MOV")
    every = _ids(root, plan_id, *LIVE)
    one = {"view": {}, "all": True, "except_ids": [mov]}
    whole = {"view": {}, "all": True, "except_ids": every}
    with served(root) as server:
        first = _preview(server, plan_id, one)
        _decide_selection(server, plan_id, one, "skip")
        after_one = _statuses(root, plan_id)
        _ok(server, f"plan/{plan_id}/decide-filter", "POST", {"action": "reset"})
        second = _preview(server, plan_id, whole)
        _decide_selection(server, plan_id, whole, "skip")
        after_whole = _statuses(root, plan_id)
    assert (first["items"], first["units"], first["overridden"]) == (6, 4, [mov])
    assert after_one == {name: "skipped" for name in Q_NEW}
    assert (second["items"], second["units"], second["overridden"]) == (3, 3, [])
    assert after_whole == {name: ("proposed" if name in LIVE else "skipped") for name in Q_NEW}
    assert spy == []


def test_q1_a_shift_click_range_ending_inside_a_unit_writes_and_renames_the_unit_whole(
    tmp_path, spy
):
    """A Shift+click range is the ids between two rows of the list as the page shows it: from
    `A.JPG` to the FIRST member of the Live Photo, in `LIST_ORDER` - three ids, one of them a
    third of a unit. The decision covers the whole unit, and the page's own apply then renames
    exactly the two singles and the three members (the spy).

    Mutation: `decide-selection` writing the named ids without `_widen_to_units` -> the HEIC and
    MOV stay undecided, and the apply refuses the mixed unit or renames one member."""
    root, plan_id = _q_archive(tmp_path)
    with served(root) as server:
        shown, total = _page(server, plan_id, "limit=200")
        assert (shown, total) == (LIST_ORDER, 6)
        span = LIST_ORDER[LIST_ORDER.index("A.JPG") : LIST_ORDER.index("IMG_1.AAE") + 1]
        assert span == ["A.JPG", "B.JPG", "IMG_1.AAE"]
        selection = {"view": {}, "ids": _ids(root, plan_id, *span)}
        preview = _preview(server, plan_id, selection)
        _decide_selection(server, plan_id, selection)
        finished = _page_apply(server, plan_id)
    assert (preview["items"], preview["units"]) == (5, 3)
    assert finished["state"] == "done" and finished["result"]["renamed"] == 5, finished
    renamed = ["A.JPG", "B.JPG", *LIVE]
    assert sorted(spy) == sorted((name, Q_NEW[name]) for name in renamed)
    assert sorted(p.name for p in root.iterdir() if not p.name.endswith(".json")) == sorted(
        [Q_NEW[n] for n in renamed] + [LOW_SINGLE]
    )


def test_q1_a_show_more_boundary_inside_a_unit_selects_the_whole_unit(tmp_path, spy):
    """The group's first page is ONE row (`limit=1`): the `.AAE`; `Show N more` would load the
    other two from `offset=1`. "Select all visible" before that click names only the `.AAE` -
    and still decides the three. Paging neither skips nor repeats a member.

    Mutation: `items?list_group=` ordering by something not total -> a member appears on both
    pages or neither."""
    root, plan_id = _q_archive(tmp_path)
    with served(root) as server:
        first, total = _page(server, plan_id, f"list_group={LIVE_GROUP}&limit=1")
        rest, _ = _page(server, plan_id, f"list_group={LIVE_GROUP}&offset=1&limit=200")
        assert (first, rest, total) == (LIVE_ORDER[:1], LIVE_ORDER[1:], 3)
        visible = {"view": {}, "ids": _ids(root, plan_id, *first)}
        preview = _preview(server, plan_id, visible)
        _decide_selection(server, plan_id, visible)
    assert (preview["items"], preview["units"]) == (3, 1)
    assert {n: s for n, s in _statuses(root, plan_id).items() if s == "approved"} == {
        n: "approved" for n in LIVE
    }
    assert spy == []


def test_q2_the_members_the_page_never_showed_are_counted_confirmed_and_renamed(tmp_path, spy):
    """The page has loaded ONE row of the Live Photo's group (its `.AAE`) and the user approves
    it. What the user never saw - the HEIC and the MOV on the unloaded page - is in the preview's
    count, in the group's `total`, in the Confirm screen's `renames`, and in the rename job: the
    spy sees exactly the three renames, and `not_approved` counts the three singles.

    Mutation: the Confirm screen counting the user's NAMED ids instead of the plan's approved
    items -> `renames` 1 while the job renames 3."""
    root, plan_id = _q_archive(tmp_path)
    with served(root) as server:
        first, _ = _page(server, plan_id, f"list_group={LIVE_GROUP}&limit=1")
        assert first == ["IMG_1.AAE"]
        group = {g["key"]: g for g in _ok(server, f"plan/{plan_id}/list")["groups"]}[LIVE_GROUP]
        selection = {"view": {}, "ids": _ids(root, plan_id, *first)}
        preview = _preview(server, plan_id, selection)
        _decide_selection(server, plan_id, selection)
        facts = _ok(server, f"plan/{plan_id}/confirmation")
        finished = _page_apply(server, plan_id)
    assert (group["total"], group["undecided"]) == (3, 3)
    assert (preview["items"], preview["units"]) == (3, 1)
    assert (facts["renames"], facts["not_approved"], facts["low_confidence"]) == (3, 3, 0)
    assert finished["result"]["renamed"] == 3, finished
    assert sorted(spy) == sorted((name, Q_NEW[name]) for name in LIVE)


# ============================================================================ Q3

# The WHOLE live route table of `pam start`'s server, bound, at M14 Task 8 - written by hand.
# Reads: they decide nothing, rename nothing, write no catalog row.
READS = {
    ("GET", "ping"),
    ("GET", "plan/{id}"),
    ("GET", "plan/{id}/groups"),
    ("GET", "plan/{id}/list"),
    ("GET", "plan/{id}/items"),
    ("GET", "thumb/{file_id}"),
    ("GET", "faces/clusters"),
    ("GET", "faces/cluster/{id}"),
    ("GET", "faces/crop/{face_id}"),
    ("GET", "faces/persons"),
    ("GET", "faces/ignored"),
    ("GET", "faces/person/{id}"),
    ("GET", "folders"),
    ("GET", "job"),
    ("GET", "plan/{id}/confirmation"),
    ("GET", "session/{id}"),
    # M14 Task 7 (W22, X2): History's two reads.
    ("GET", "sessions"),
    ("GET", "session/{id}/report"),
}
# The selection's preview: a POST because a selection is a body; it writes nothing.
READ_ONLY_POSTS = {("POST", "plan/{id}/selection")}
# Writes to PLAN ITEMS: each must write whole units of the whole plan.
PLAN_WRITES = {
    ("POST", "plan/{id}/decide"),
    ("POST", "plan/{id}/decide-filter"),  # incl. its `accept_all` and `shortcut` branches
    ("POST", "plan/{id}/decide-selection"),
    ("POST", "item/{id}/rename"),
    ("POST", "item/{id}/restore"),
}
# Writes to faces and people: no plan item.
FACES_WRITES = {
    ("POST", "faces/cluster/{id}/label"),
    ("POST", "faces/cluster/{id}/dismiss"),
    ("POST", "faces/cluster/{id}/split"),
    ("POST", "faces/faces/state"),
    ("POST", "faces/faces/assign"),
    ("POST", "faces/person/{id}"),
    ("POST", "faces/person/{id}/merge"),
}
# The two routes that MOVE files (U18, U19): whole units through the preflight.
MOVES = {("POST", "plan/{id}/apply"), ("POST", "session/{id}/undo")}
# The page's jobs and its folder: `bind` changes folder; `job/read` writes a NEW plan through
# `plan_locked`; `job/cancel` stops a read. None decides an item of an existing plan.
JOB_AND_FOLDER = {("POST", "bind"), ("POST", "job/read"), ("POST", "job/cancel")}
ROUTE_TABLE = READS | READ_ONLY_POSTS | PLAN_WRITES | FACES_WRITES | MOVES | JOB_AND_FOLDER
# Spelled floors, checked at collection: a list cut together with its test is caught here.
assert (len(READS), len(PLAN_WRITES), len(FACES_WRITES), len(MOVES), len(JOB_AND_FOLDER)) == (
    18,
    5,
    7,
    2,
    3,
)
assert len(ROUTE_TABLE) == 36


def test_q3_the_live_route_table_of_pam_start_is_exactly_the_hand_written_one(tmp_path):
    """Both directions: every route the bound server serves is in the hand-written table, and
    every route in the table is served. The classes do not overlap.

    Mutation: a new row in any handler table, or a route renamed -> the symmetric difference
    names it."""
    root, _ = _q_archive(tmp_path)
    with served(root) as server:
        table = set(server.api_handlers)
    assert table == ROUTE_TABLE, sorted(table.symmetric_difference(ROUTE_TABLE))
    classes = [READS, READ_ONLY_POSTS, PLAN_WRITES, FACES_WRITES, MOVES, JOB_AND_FOLDER]
    assert sum(len(c) for c in classes) == len(ROUTE_TABLE)


def _fill(template: str, ids: dict[str, object]) -> str:
    path = template
    for name, value in ids.items():
        path = path.replace("{" + name + "}", str(value))
    return path


def test_q3_every_read_route_and_the_preview_change_no_catalog_row_and_rename_nothing(
    tmp_path, spy
):
    """Every GET in the table, and the selection preview, driven on an archive with an applied
    session (so Task 7's `sessions` and `session/{id}/report` have something to read): the
    catalog's every row is the same afterwards, the disk is the same, and the spy saw nothing.
    A read that is refused (an unknown face) is still a read.

    Mutation: `GET session/{id}` (or History's report) recording that it was viewed in a table
    -> the dump differs."""
    root, plan_id = _q_archive(tmp_path)
    with served(root) as server:
        _decide_selection(server, plan_id, {"view": {}, "ids": _ids(root, plan_id, "A.JPG")})
        session = _page_apply(server, plan_id)["session"]
        spy.clear()
        conn = _ro(root)
        try:
            file_id = conn.execute("SELECT MIN(id) FROM files").fetchone()[0]
        finally:
            conn.close()
        ids = {"id": plan_id, "file_id": file_id, "face_id": 1}
        before_rows, before_disk = _catalog_dump(root), m13._snapshot(root)
        statuses = {}
        for method, template in sorted(READS):
            fill = dict(ids)
            if template.startswith(("session/", "faces/")):
                fill["id"] = session if template.startswith("session/") else 1
            statuses[template] = call(server, _fill(template, fill), method).status
        selection = {"view": {}, "all": True}
        statuses["selection"] = call(
            server, f"plan/{plan_id}/selection", "POST", {"selection": selection}
        ).status
        after_rows, after_disk = _catalog_dump(root), m13._snapshot(root)
    assert len(statuses) == len(READS) + 1
    assert all(code < 500 for code in statuses.values()), statuses
    # The reads that MUST answer on this archive did (an unknown face is a 404, a refusal).
    for template in (
        "sessions",
        "session/{id}/report",
        "session/{id}",
        "plan/{id}/list",
        "job",
    ):
        assert statuses[template] == 200, (template, statuses)
    assert after_rows == before_rows
    assert after_disk == before_disk
    assert spy == []


# Each plan-write route driven through ONE member (or none, for `accept_all`), and the
# (status, stem) every member of the Live Photo carries afterwards, spelled.
PLAN_WRITE_CASES = [
    ("decide", ("skipped", "2019-07-04_001")),
    ("decide-filter", ("skipped", "2019-07-04_001")),
    ("accept-all", ("approved", "2019-07-04_001")),
    ("shortcut", ("approved", "2019-07-04_001")),
    ("decide-selection", ("skipped", "2019-07-04_001")),
    ("rename", ("approved", "Holiday")),
    ("restore", ("approved", "2019-07-04_001")),
]
assert len(PLAN_WRITE_CASES) == 7


def _stem(name: str) -> str:
    return name.rsplit(".", 1)[0]


@pytest.mark.parametrize(
    ("route", "want"), PLAN_WRITE_CASES, ids=[c[0] for c in PLAN_WRITE_CASES]
)
def test_q3_every_plan_write_route_of_pam_start_writes_the_whole_unit_from_one_member(
    tmp_path, spy, route, want
):
    """Through `pam start`'s server (the page's), with its binding and Origin: afterwards the
    Live Photo's three items carry ONE status and ONE stem, and nothing was renamed.

    Mutation: any of these routes acting on the named member alone -> two statuses or stems."""
    root, plan_id = _q_archive(tmp_path)
    items = _items(root, plan_id)
    mov, aae = items["IMG_1.MOV"]["id"], items["IMG_1.AAE"]["id"]
    with served(root) as server:
        base = f"plan/{plan_id}"
        if route == "decide":
            _ok(server, f"{base}/decide", "POST", {"action": "skip", "item_ids": [aae]})
        elif route == "decide-filter":
            _ok(server, f"{base}/decide-filter", "POST", {"action": "skip", "q": "MOV"})
        elif route == "accept-all":
            _ok(
                server,
                f"{base}/decide-filter",
                "POST",
                {"action": "approve", "accept_all": True},
            )
        elif route == "shortcut":
            body = {"action": "approve", "shortcut": True, "q": "MOV"}
            _ok(server, f"{base}/decide-filter", "POST", body)
        elif route == "decide-selection":
            _decide_selection(server, plan_id, {"view": {}, "ids": [aae]}, "skip")
        elif route == "rename":
            _ok(server, f"item/{mov}/rename", "POST", {"new_filename": "Holiday.MOV"})
        else:
            heic = items["IMG_1.HEIC"]["id"]
            _ok(server, f"item/{heic}/rename", "POST", {"new_filename": "X.HEIC"})
            _ok(server, f"item/{aae}/restore", "POST", {})
    after = _items(root, plan_id)
    assert {(after[m]["status"], _stem(after[m]["new_filename"])) for m in LIVE} == {want}
    assert spy == []


def test_q3_the_two_moving_routes_act_on_whole_units_only(tmp_path, spy):
    """`plan/{id}/apply` on a plan holding a MIXED unit (the MOV approved by hand, its partners
    undecided - a state no endpoint can build since A1) and an approved single: the job renames
    the single and leaves every member of the mixed unit alone (the spy). Then a whole unit
    applied, and `session/{id}/undo`: the spy sees all three members put back, newest first.

    Mutation: the preflight's unit containment deleted -> the MOV is renamed alone."""
    root, plan_id = _q_archive(tmp_path)
    _set(root, plan_id, "IMG_1.MOV", "approved")
    _set(root, plan_id, "A.JPG", "approved")
    with served(root) as server:
        first = _page_apply(server, plan_id)
        assert spy == [("A.JPG", Q_NEW["A.JPG"])], spy
        assert (first["result"]["renamed"], first["result"]["failed"]) == (1, 1), first
        assert {n: s for n, s in _statuses(root, plan_id).items() if n in LIVE} == {
            "IMG_1.AAE": "proposed",
            "IMG_1.HEIC": "proposed",
            "IMG_1.MOV": "failed",  # refused whole (spec 7.3 rev 15, F1 R3), never renamed
        }
    # A fresh plan, the unit approved whole through one member, applied, then undone.
    code = main(["plan", "--archive", str(root)])
    assert code == 0
    second = m13._open_plan(root)
    spy.clear()
    with served(root) as server:
        (heic,) = _ids(root, second, "IMG_1.HEIC")
        _ok(server, f"plan/{second}/decide", "POST", {"action": "approve", "item_ids": [heic]})
        applied = _page_apply(server, second)
        forward = sorted(spy)
        spy.clear()
        _page_undo(server, applied["session"])
    assert forward == sorted((n, Q_NEW[n]) for n in LIVE)
    assert sorted(spy) == sorted((Q_NEW[n], n) for n in LIVE)


# ============================================================================ Q4

# Stand-ins (CLAUDE.md's privacy rule): the named neighbour and the name the queue gives.
NAMED, GIVEN = "Person A", "Person B"


def _faces_archive(tmp_path: Path) -> tuple[Path, int, list[int]]:
    """M11's construction (`test_m11_safety_constructions._cluster_with_a_named_neighbour`),
    with stand-in names: one cluster of three faces, the first already named."""
    root = tmp_path / "photos"
    root.mkdir(parents=True)
    for index in range(3):
        put(root, f"P{index}.JPG", "2019:07:04 18:23:11")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    conn = connect(workspace_if_exists(root).db_path)  # type: ignore[union-attr]
    try:
        shas = [
            str(r[0])
            for r in conn.execute(
                "SELECT sha256 FROM files WHERE filename LIKE 'P%.JPG' ORDER BY filename"
            )
        ]
        named = int(
            conn.execute("INSERT INTO persons (label) VALUES (?)", (NAMED,)).lastrowid or 0
        )
        faces = []
        for position, sha in enumerate(shas):
            row = conn.execute(
                "INSERT INTO face_instances (sha256, bbox_json, embedding, det_confidence,"
                " cluster_id, person_id, model_name, model_version)"
                " VALUES (?, ?, ?, 0.9, 2, ?, 'yunet+sface', '1')",
                (
                    sha,
                    json.dumps(GEOMETRY),
                    blob([0.1, 0.2, 0.3, 0.4]),
                    named if position == 0 else None,
                ),
            )
            faces.append(int(row.lastrowid or 0))
        conn.commit()
    finally:
        conn.close()
    return root, named, faces


def _faces(root: Path) -> dict[int, tuple[str | None, str | None]]:
    conn = _ro(root)
    try:
        rows = conn.execute(
            "SELECT a.id, p.label, a.review_state FROM face_instances a"
            " LEFT JOIN persons p ON p.id = a.person_id"
        ).fetchall()
    finally:
        conn.close()
    return {row[0]: (row[1], row[2]) for row in rows}


def test_q4_label_and_dismiss_through_pam_start_write_exactly_the_faces_the_queue_offered(
    tmp_path,
):
    """`GET faces/clusters` offers the cluster as TWO faces (the unnamed ones) and samples only
    those; `label` names exactly those two and leaves the named neighbour alone. On a second copy
    `dismiss` ignores exactly those two. Both through the bound `pam start` server.

    Mutation: an unnamed-cluster operation selecting by `cluster_id` alone -> the named face is
    re-named or dismissed (S2-F1)."""
    root, _named, faces = _faces_archive(tmp_path / "label")
    with served(root) as server:
        (group,) = _ok(server, "faces/clusters")["clusters"]
        written = _ok(server, "faces/cluster/2/label", "POST", {"person": GIVEN})
    assert group["faces"] == 2 and {f["face_id"] for f in group["sample"]} == set(faces[1:])
    assert written["faces"] == 2
    assert _faces(root) == {
        faces[0]: (NAMED, None),
        faces[1]: (GIVEN, None),
        faces[2]: (GIVEN, None),
    }

    root, _named, faces = _faces_archive(tmp_path / "dismiss")
    with served(root) as server:
        dismissed = _ok(server, "faces/cluster/2/dismiss", "POST", {})
    assert dismissed["faces"] == 2
    assert _faces(root) == {
        faces[0]: (NAMED, None),
        faces[1]: (None, "dismissed"),
        faces[2]: (None, "dismissed"),
    }


def test_q4_a_cluster_whose_every_face_is_named_is_refused_by_name_through_pam_start(tmp_path):
    """Every face of the cluster named: `label` is a 409 that NAMES the person - never a silent
    re-naming, never a bare 404 - and no face changes.

    Mutation: the empty-unnamed-set guard deleted -> a 200 that re-names the named faces."""
    root, named, faces = _faces_archive(tmp_path)
    conn = connect(workspace_if_exists(root).db_path)  # type: ignore[union-attr]
    try:
        conn.execute("UPDATE face_instances SET person_id = ? WHERE cluster_id = 2", (named,))
        conn.commit()
    finally:
        conn.close()
    with served(root) as server:
        reply = call(server, "faces/cluster/2/label", "POST", {"person": GIVEN})
    assert reply.status == 409, reply
    assert NAMED in reply.body.decode("utf-8"), reply.body
    assert _faces(root) == {face: (NAMED, None) for face in faces}


# ============================================================================ Q9

# Every call on the way to the one verified rename, written by hand from `pam/execute/`:
# (module, the function the call is in, the function called). Each must pass `names=names`.
CALLS_TO_THE_RENAME = {
    ("pam.execute.unit", "execute_rename_unit", "perform_verified_rename"),
    ("pam.execute.unit", "execute_rename_unit", "revert_unit"),
    ("pam.execute.unit", "revert_unit", "perform_verified_rename"),
    ("pam.execute.unit", "execute_unit", "execute_rename_unit"),
    ("pam.execute.apply", "apply_locked", "execute_unit"),
    ("pam.execute.undo", "_undo_unit", "execute_rename_unit"),
    ("pam.execute.recover", "_heal_split_units", "revert_unit"),
}
CALLEES = {callee for _, _, callee in CALLS_TO_THE_RENAME}
assert len(CALLS_TO_THE_RENAME) == 7 and len(CALLEES) == 4


def _calls_into(callees: set[str]) -> dict[tuple[str, str, str], ast.Call]:
    """Every call in `pam/` whose callee's NAME is in `callees` (a bare name or an attribute),
    keyed by (module, enclosing function, callee). Read from the source, never imported."""
    found: dict[tuple[str, str, str], ast.Call] = {}
    package = REPO / "pam"
    for path in sorted(package.rglob("*.py")):
        module = ".".join(path.relative_to(REPO).with_suffix("").parts)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(func):
                if not isinstance(node, ast.Call):
                    continue
                target = node.func
                name = (
                    target.id if isinstance(target, ast.Name) else getattr(target, "attr", None)
                )
                if name in callees:
                    found[(module, func.name, name)] = node
    return found


def test_q9_every_call_on_the_way_to_the_verified_rename_is_listed_and_passes_the_runs_names():
    """The call graph to `perform_verified_rename`, enumerated by AST over `pam/` and compared
    with the hand-written list in BOTH directions; `perform_verified_rename` itself is called in
    exactly one module, `execute/unit.py`; and every listed call passes `names=names` - the run's
    one `DirectoryNames` - never `None` and never a new one.

    Mutation: `names=names` dropped from `apply_locked`'s `execute_unit` call -> this names it
    (and the flat-folder counts below list the folder once per rename)."""
    found = _calls_into(CALLEES)
    assert set(found) == CALLS_TO_THE_RENAME, sorted(
        set(found).symmetric_difference(CALLS_TO_THE_RENAME)
    )
    assert {m for m, _, c in found if c == "perform_verified_rename"} == {"pam.execute.unit"}
    for key, node in found.items():
        passed = {kw.arg: kw.value for kw in node.keywords}
        assert "names" in passed, key
        value = passed["names"]
        assert isinstance(value, ast.Name) and value.id == "names", (key, ast.dump(value))


def test_q9_the_enumerator_sees_a_call_in_each_spelling(tmp_path, monkeypatch):
    """The walker's own control: a bare-name call and an attribute call in a nested function are
    both found, and a mention that is not a call is not."""
    tree = ast.parse(
        "def outer():\n"
        "    def inner():\n"
        "        unit.revert_unit(names=names)\n"
        "    execute_unit(names=None)\n"
        "    x = revert_unit\n"
    )
    package = tmp_path / "pam"
    package.mkdir()
    (package / "probe.py").write_text(ast.unparse(tree), encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    found = _calls_into({"revert_unit", "execute_unit"})
    assert set(found) == {
        ("pam.probe", "inner", "revert_unit"),
        ("pam.probe", "outer", "revert_unit"),
        ("pam.probe", "outer", "execute_unit"),
    }


@pytest.fixture()
def listings(monkeypatch):
    """Every `os.scandir` made FROM `pam/execute/rename.py` (the occupancy check), by folder, from
    any thread. A scan's walk is not the occupancy check and is not counted."""
    seen: list[Path] = []
    real = os.scandir

    def counting(path="."):
        if sys._getframe(1).f_globals.get("__name__") == "pam.execute.rename":
            seen.append(Path(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", counting)
    return seen


FLAT_PHOTOS = 40  # photographs in ONE folder, each with its stand-in exiftool sidecar
UNIT = ("IMG_X.JPG", "IMG_X.MOV", "IMG_X.XMP")  # one companion unit beside them
UNIT_NEW_JPG = "2021-09-06_001.JPG"  # spec 6.1 from the unit's date


def _flat(tmp_path: Path) -> tuple[Path, int]:
    root = tmp_path / "flat"
    root.mkdir()
    for i in range(FLAT_PHOTOS):
        put(root, f"IMG_{i:04d}.JPG")
    for name in UNIT:
        put(root, name, "2021:09:06 11:02:03")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    assert plan_id is not None
    conn = connect(workspace_if_exists(root).db_path)  # type: ignore[union-attr]
    try:  # setup only: the suite's own approval
        conn.execute("UPDATE plan_items SET status = 'approved' WHERE plan_id = ?", (plan_id,))
        conn.commit()
    finally:
        conn.close()
    return root, plan_id


def _touch(path: Path) -> None:
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def _cli_apply(capsys, monkeypatch, root, plan_id):
    return m13._pam(
        capsys,
        "apply",
        str(plan_id),
        "--archive",
        str(root),
        typed="apply",
        monkeypatch=monkeypatch,
    )


# The paths that reach the verified rename, by hand: (the path, the runs it makes). Each run
# lists the one flat folder ONCE.
PATHS = [
    ("pam apply", 1),
    ("pam undo", 1),
    ("pam apply, a unit put back", 1),
    ("pam undo, a unit renamed forward again", 1),
    ("recovery's revert (pam verify after a crash)", 1),
    ("the page's rename job", 1),
    ("the page's undo job", 1),
    ("the page's rename job, then its recovery", 2),
]
assert len(PATHS) == 8


@pytest.mark.parametrize(("path", "runs"), PATHS, ids=[p for p, _ in PATHS])
def test_q9_each_path_to_the_verified_rename_lists_a_flat_folder_once_per_run(
    tmp_path, capsys, monkeypatch, listings, spy, set_fault, path, runs
):
    """A flat folder of FLAT_PHOTOS photographs and a three-member unit. Each path is driven end
    to end; the occupancy check's listings are counted from where the path starts. It did the
    work (the spy saw the renames) and listed the folder exactly `runs` times.

    Mutation: any of these paths handing `names=None` (or a fresh `DirectoryNames` per unit) ->
    the folder is listed once per rename (or per unit): 43, not 1."""
    root, plan_id = _flat(tmp_path)
    expected_work = FLAT_PHOTOS + len(UNIT)
    if path == "pam apply":
        listings.clear()
        code, out, err = _cli_apply(capsys, monkeypatch, root, plan_id)
        assert code == 0, out + err
        assert len(spy) == expected_work
    elif path == "pam undo":
        code, out, err = _cli_apply(capsys, monkeypatch, root, plan_id)
        assert code == 0, out + err
        spy.clear()
        listings.clear()
        code, out, err = m13._pam(
            capsys, "undo", m13._sessions(root)[-1], "--archive", str(root)
        )
        assert code == 0, out + err
        assert len(spy) == expected_work
    elif path == "pam apply, a unit put back":
        _touch(root / "IMG_X.XMP")
        listings.clear()
        code, out, err = _cli_apply(capsys, monkeypatch, root, plan_id)
        assert code == 1, out + err
        backs = [(old, new) for old, new in spy if old.startswith("2021-09-06_001")]
        assert len(backs) == 2, spy  # two members renamed, then put back
    elif path == "pam undo, a unit renamed forward again":
        code, out, err = _cli_apply(capsys, monkeypatch, root, plan_id)
        assert code == 0, out + err
        _touch(root / UNIT_NEW_JPG)
        spy.clear()
        listings.clear()
        code, out, err = m13._pam(
            capsys, "undo", m13._sessions(root)[-1], "--archive", str(root)
        )
        assert code == 1, out + err
        forward_again = [(old, new) for old, new in spy if new.startswith("2021-09-06_001")]
        assert len(forward_again) == 2, spy
    elif path.startswith("recovery"):
        set_fault.arm("crash:mid_companion_unit:2")
        _cli_apply(capsys, monkeypatch, root, plan_id)
        assert set_fault.hits("mid_companion_unit") == 2
        set_fault.next_invocation()
        spy.clear()
        listings.clear()
        code, out, err = m13._pam(capsys, "verify", "--archive", str(root))
        assert "put back" in out + err, out + err
        assert len(spy) == 2, spy  # the unit's two renamed members, reverted
    else:
        if path == "the page's rename job, then its recovery":
            set_fault.arm("crash:mid_companion_unit:2")
        with served(root) as server:
            listings.clear()
            finished = _page_apply(server, plan_id)
            if path == "the page's rename job, then its recovery":
                assert finished["state"] == "failed", finished
                _ok(server, "job")  # a request heals the dead job first (U11)
                assert len([1 for old, _ in spy if old.startswith("2021-09-06_001")]) == 2, spy
            else:
                assert finished["result"]["renamed"] == expected_work, finished
            if path == "the page's undo job":
                spy.clear()
                listings.clear()
                undone = _page_undo(server, finished["session"])
                assert undone["result"]["restored"] == expected_work, undone
                assert len(spy) == expected_work
    assert listings == [root] * runs, (path, len(listings))


# ============================================================================ 8b: shared

# U14's sentence, word for word (design 8.2): a cancelled page apply or undo says it.
CHANGED = "Something changed since you looked. Check the names again."
# What the Q fixture holds on disk before anything is renamed (its `.tags.json` sidecars aside).
ORIGINAL = sorted(Q_NEW)
LOCKED = "Another pam command is already running against this archive."


def _on_disk(root: Path) -> list[str]:
    return sorted(p.name for p in root.iterdir() if not p.name.endswith(".json"))


def _approve(server, root: Path, plan_id: int, *names: str, decided: int | None = None) -> None:
    """Approve `names` by id through `decide`; `decided` is the WIDENED count (units whole)."""
    ids = _ids(root, plan_id, *names)
    reply = _ok(
        server, f"plan/{plan_id}/decide", "POST", {"action": "approve", "item_ids": ids}
    )
    assert reply["decided"] == (len(names) if decided is None else decided), reply


def _read_job(server) -> int:
    """The page's `Start reading`: the read job, awaited; the plan it published."""
    _ok(server, "job/read", "POST", {})
    finished = await_state(server, "done", "failed", "cancelled")
    assert (finished["kind"], finished["state"]) == ("read", "done"), finished
    return int(server.plan_id)


def _plan_status(root: Path, plan_id: int) -> str:
    conn = _ro(root)
    try:
        return str(
            conn.execute("SELECT status FROM plans WHERE id = ?", (plan_id,)).fetchone()[0]
        )
    finally:
        conn.close()


def _apply_body(nonce: str) -> dict:
    return {"nonce": nonce, "folder_confirmed": True}


def _pairs(*names: str) -> list[tuple[str, str]]:
    return sorted((name, Q_NEW[name]) for name in names)


# ============================================================================ Q5


def test_q5_a_confirm_screen_open_across_a_decision_in_another_tab_renames_nothing(
    tmp_path, spy
):
    """Tab 1 opens Confirm over ONE approved name (A.JPG). Tab 2 then approves B.JPG - a rename
    the Confirm screen never showed - and, on a second pass, skips the one tab 1 confirmed. Tab 1's
    `Rename` with its nonce is cancelled both times with U14's sentence: the job recomputes the
    digest of what it is about to rename (design 8.2 U14) and it is not the batch the person saw.
    Nothing is renamed (the spy), and the decisions stand as tab 2 left them.

    Mutation: `page_confirm` comparing the root only -> the first apply renames A AND B."""
    root, plan_id = _q_archive(tmp_path)
    with served(root) as server:
        _approve(server, root, plan_id, "A.JPG")
        seen = _ok(server, f"plan/{plan_id}/confirmation")  # tab 1
        assert seen["renames"] == 1, seen
        _approve(server, root, plan_id, "B.JPG")  # tab 2: MORE than tab 1 saw
        _ok(server, f"plan/{plan_id}/apply", "POST", _apply_body(seen["nonce"]))
        more = await_state(server, "done", "failed", "cancelled")
        again = _ok(server, f"plan/{plan_id}/confirmation")  # tab 1, re-opened: two names
        assert again["renames"] == 2, again
        (b_id,) = _ids(root, plan_id, "B.JPG")
        _ok(server, f"plan/{plan_id}/decide", "POST", {"action": "skip", "item_ids": [b_id]})
        _ok(server, f"plan/{plan_id}/apply", "POST", _apply_body(again["nonce"]))
        fewer = await_state(server, "done", "failed", "cancelled")
    for finished in (more, fewer):
        assert (finished["state"], finished["lines"]) == ("cancelled", [CHANGED]), finished
    assert spy == []
    assert _on_disk(root) == ORIGINAL
    assert _plan_status(root, plan_id) == "open"
    assert _statuses(root, plan_id) == {
        name: {"A.JPG": "approved", "B.JPG": "skipped"}.get(name, "proposed") for name in Q_NEW
    }


def test_q5_a_selection_preview_held_across_a_read_job_writes_nothing_anywhere(tmp_path, spy):
    """The page previews `select all` on plan P (6 items, 4 units). A read job then publishes P2
    and supersedes P. The held selection, sent to P, is refused; sent to P2 with P's item ids it
    is refused too. Neither plan gains a decision and nothing is renamed.

    Mutation: `decide-selection` not checking the plan is open -> P's items are approved after
    the read job replaced it."""
    root, plan_id = _q_archive(tmp_path)
    with served(root) as server:
        held = {"view": {}, "ids": _ids(root, plan_id, "A.JPG", "IMG_1.HEIC")}
        preview = _preview(server, plan_id, held)
        new = _read_job(server)
        to_old = call(
            server,
            f"plan/{plan_id}/decide-selection",
            "POST",
            {"selection": held, "action": "approve"},
        )
        to_new = call(
            server,
            f"plan/{new}/decide-selection",
            "POST",
            {"selection": held, "action": "approve"},
        )
    assert (preview["items"], preview["units"]) == (4, 2)
    assert new != plan_id
    assert (_plan_status(root, plan_id), _plan_status(root, new)) == ("superseded", "open")
    assert (to_old.status, to_old.json()) == (
        409,
        {
            "error": f"plan {plan_id} is superseded, not open, so its names cannot be changed"
            " any more. Run: pam plan"
        },
    )
    assert (to_new.status, to_new.json()["error"].endswith("is not an item of this plan")) == (
        400,
        True,
    )
    assert set(_statuses(root, plan_id).values()) == {"proposed"}
    assert set(_statuses(root, new).values()) == {"proposed"}
    assert spy == []
    assert _on_disk(root) == ORIGINAL


def test_q5_the_look_alike_memory_does_not_outlive_its_plan(tmp_path, spy):
    """Two photographs that look alike (`base.png` and the same picture at twice the size), and a
    third that looks like neither, fingerprinted by the REAL phash enricher. The page's list for
    plan P names each as the other's look-alike - and the server REMEMBERS it (computed once per
    server and plan). The user then deletes the larger copy in Explorer (the OS writes) and the
    page reads again: P2 is a new plan, and its look-alikes are computed over what is on disk
    NOW - `base.png` names nothing, and the deleted path is named nowhere.

    Mutation: `lookalikes.forget` not called when the read job publishes a plan, AND the memory
    keyed by server alone -> P2 is answered with P's groups, naming a file that is gone."""
    import test_webui_lookalikes as alike

    from pam.enrich.command import run_enrich

    root = tmp_path / "photos"
    root.mkdir()
    for name, payload in (
        ("base.png", alike.BASE),
        ("base_large.JPG", alike._resized_jpeg(alike.BASE, 2)),
        ("far.png", alike.FAR),
    ):
        put(root, name, content=payload)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    run_enrich("phash", root)
    plan_id = run_plan(root).plan_id
    assert plan_id is not None
    with served(root) as server:
        first = _ok(server, f"plan/{plan_id}/items?limit=200")["items"]
        (root / "base_large.JPG").unlink()
        new = _read_job(server)
        second = _ok(server, f"plan/{new}/items?limit=200")["items"]
    assert {c["old_rel_path"]: c["look_alikes"] for c in first} == {
        "base.png": ["base_large.JPG"],
        "base_large.JPG": ["base.png"],
        "far.png": [],
    }
    assert {c["old_rel_path"]: c["look_alikes"] for c in second} == {
        "base.png": [],
        "far.png": [],
    }
    assert spy == []


def _raw(server, method: str, path: str, body: bytes | None, headers: dict[str, str]):
    """One HTTP request exactly as given: the path is NOT prefixed with the token."""
    conn = HTTPConnection(server.host, server.port, timeout=60)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def _page_headers(server) -> dict[str, str]:
    """What the page's `fetch` sends on a POST (`test_webui_rename.call`'s, spelled here)."""
    return {
        "Content-Type": "application/json",
        "X-Pam-Binding": str(server.binding),
        "Origin": f"http://127.0.0.1:{server.port}",
    }


def test_q5_a_page_left_open_across_a_console_scan_after_the_server_stopped_renames_nothing(
    tmp_path, capsys, monkeypatch, spy
):
    """The page confirms one approved name, and the server is stopped (Ctrl+C) with the tab still
    open: it holds the old token, the plan id and a nonce. The user adds a photograph and runs
    `pam scan` in the console, which supersedes the open plan (design 6.3, a scan that changes
    the catalog). Then `pam start` again. The old page's requests - with its old token, and with
    the new token, its stale nonce and plan - rename nothing: the old token is the gate's empty
    404, the new server issued no such nonce (409, U14's sentence), and the superseded plan
    has no confirmation.

    Mutation: `Nonces` kept beyond one server (a module-level store) -> the stale nonce starts a
    job on the superseded plan."""
    root, plan_id = _q_archive(tmp_path)
    with served(root) as first:
        _approve(first, root, plan_id, "A.JPG")
        stale = _ok(first, f"plan/{plan_id}/confirmation")
        old_token = first.token
    put(root, "C.JPG", "2021:01:01 00:00:00")
    monkeypatch.setattr(exiftool_mod, "default_command", lambda: list(FAKE_COMMAND))
    code, out, err = m13._pam(capsys, "scan", str(root))
    assert code == 0, out + err
    assert _plan_status(root, plan_id) == "superseded"
    with served(root) as second:
        assert second.token != old_token
        body = json.dumps(_apply_body(stale["nonce"])).encode()
        old_page = _raw(
            second,
            "POST",
            f"/t/{old_token}/api/plan/{plan_id}/apply",
            body,
            _page_headers(second),
        )
        replayed = call(second, f"plan/{plan_id}/apply", "POST", _apply_body(stale["nonce"]))
        reconfirm = call(second, f"plan/{plan_id}/confirmation")
        job = _ok(second, "job")
    assert old_page == (404, b"")
    assert (replayed.status, replayed.json()) == (409, {"error": CHANGED})
    assert reconfirm.status == 409, reconfirm
    assert job["state"] == "idle", job
    assert spy == []
    assert _on_disk(root) == sorted([*ORIGINAL, "C.JPG"])


def test_q5_the_os_touches_a_file_between_confirm_and_rename(tmp_path, spy):
    """THE OS AS A WRITER. The person approves A.JPG, B.JPG and the Live Photo and sees Confirm
    say 5. Then a sync client touches B.JPG's and IMG_1.MOV's mtime (no byte, no name changes).

    WHICH, FROM THE SPEC: Confirm does NOT refuse - its nonce is bound to the digest of the
    `(item id, new name)` pairs pam will rename (U14), which an mtime does not change. The
    rename then verifies EACH file against the disk, not the catalog (design 7.1 step 1:
    "old path exists; (size, mtime_ns) match"), so B.JPG is left alone, and the Live Photo's
    unit is refused or put back WHOLE (7.3). The run renames exactly A.JPG: never a file the
    person did not approve, and never a file that changed since it was read.

    Mutation: the `(size, mtime_ns)` comparison in `perform_verified_rename` deleted -> B.JPG and
    the unit are renamed over a file pam has not read since it changed."""
    root, plan_id = _q_archive(tmp_path)
    with served(root) as server:
        _approve(server, root, plan_id, "A.JPG", "B.JPG", "IMG_1.HEIC", decided=5)
        facts = _ok(server, f"plan/{plan_id}/confirmation")
        assert facts["renames"] == 5, facts
        _touch(root / "B.JPG")
        _touch(root / "IMG_1.MOV")
        _ok(server, f"plan/{plan_id}/apply", "POST", _apply_body(facts["nonce"]))
        finished = await_state(server, "done", "failed", "cancelled")
    assert finished["state"] == "done", finished
    assert (finished["result"]["renamed"], finished["result"]["failed"]) == (1, 4), finished
    # The CALLS, spelled: B.JPG and IMG_1.MOV are handed to the verified rename, which refuses
    # each on its disk check; the unit's two members renamed before the MOV are put back.
    assert spy == [
        *_pairs("A.JPG", "B.JPG", "IMG_1.AAE", "IMG_1.HEIC", "IMG_1.MOV"),
        (Q_NEW["IMG_1.HEIC"], "IMG_1.HEIC"),
        (Q_NEW["IMG_1.AAE"], "IMG_1.AAE"),
    ], spy
    assert _statuses(root, plan_id) == {
        "A.JPG": "applied",
        "B.JPG": "failed",
        "D.JPG": "proposed",
        **{name: "failed" for name in LIVE},
    }
    assert _on_disk(root) == sorted([Q_NEW["A.JPG"], *(n for n in ORIGINAL if n != "A.JPG")])


def test_q5_a_history_undo_of_a_session_a_later_page_rename_moved_again_moves_nothing(
    tmp_path, capsys, spy
):
    """THE TASK 8 REVIEW'S OWN CONSTRUCTION (Q5: the later write is a second page rename; the
    artefact is the EARLIER session's journal, which History still offers to undo). Session S1
    renames A.JPG to `Holiday.JPG` (an edited name). The page reads again; P2 proposes a name
    for `Holiday.JPG` and session S2 renames it again. History (Task 7, X2) lists both, each
    `undoable`. Undo S1 FIRST, from History: S1's journal names `Holiday.JPG`, which is gone, so
    design 7.4's verification refuses it - nothing moves, the item stays `applied` (7.4 rev 9
    item 2) and S1 stays undoable. Then S2 is undone and S1 again: the photograph is back at
    A.JPG with its own bytes, and `pam verify` is clean.

    Mutation: the undo's verification trusting the journal (no `old path exists` check) ->
    S1's undo moves whatever is at, or reaches for, a path the file left."""
    root, plan_id = _q_archive(tmp_path)
    original = m13._snapshot(root)
    (a_id,) = _ids(root, plan_id, "A.JPG")
    with served(root) as server:
        _ok(server, f"item/{a_id}/rename", "POST", {"new_filename": "Holiday.JPG"})
        s1 = _page_apply(server, plan_id)
        assert (s1["state"], s1["result"]["renamed"]) == ("done", 1), s1
    # HT-M14 F1 (AM6): a typed name is KEPT while the template that proposed it is in force, so
    # the second rename this construction needs is reached the way a user reaches it now: a
    # template change in `pam.toml`, `pam start` again (the server reads its config when it
    # binds), and a read. History's journal is the same artefact across the restart.
    config = workspace_if_exists(root).config_path
    before = config.read_text(encoding="utf-8") if config.exists() else ""
    assert "[naming]" not in before, before
    config.write_text(before + '\n[naming]\ntemplate = "{seq}_{date}"\n', encoding="utf-8")
    with served(root) as server:
        new = _read_job(server)
        p2 = _items(root, new)
        assert "Holiday.JPG" in p2, (new, sorted(p2))
        again = p2["Holiday.JPG"]["new_filename"]
        assert again != "Holiday.JPG"
        _approve(server, root, new, "Holiday.JPG")
        s2 = _page_apply(server, new)
        assert (s2["state"], s2["result"]["renamed"]) == ("done", 1), s2
        history = {row["session_id"]: row for row in _ok(server, "sessions")["sessions"]}
        assert {k: (r["renamed"], r["undoable"]) for k, r in history.items()} == {
            s1["session"]: (1, True),
            s2["session"]: (1, True),
        }
        spy.clear()
        first = _page_undo(server, s1["session"])  # History's `Undo this rename`, on S1
        refused = list(spy)
        on_disk_after_refusal = _on_disk(root)
        status_after_refusal = _statuses(root, plan_id)["A.JPG"]
        still = {r["session_id"]: r["undoable"] for r in _ok(server, "sessions")["sessions"]}
        spy.clear()
        _page_undo(server, s2["session"])
        _page_undo(server, s1["session"])
        back = list(spy)
    assert (first["state"], first["result"]["restored"], first["result"]["failed"]) == (
        "done",
        0,
        1,
    ), first
    assert refused == [("Holiday.JPG", "A.JPG")]  # handed over, and refused on its disk check
    assert on_disk_after_refusal == sorted([again, *(n for n in ORIGINAL if n != "A.JPG")])
    assert status_after_refusal == "applied"
    assert still == {s1["session"]: True, s2["session"]: True}
    assert back == [(again, "Holiday.JPG"), ("Holiday.JPG", "A.JPG")]
    assert m13._snapshot(root) == original
    assert _statuses(root, plan_id)["A.JPG"] == "undone"
    code, out, err = m13._pam(capsys, "verify", "--archive", str(root))
    assert code == 0 and VERIFY_CLEAN in out, out + err


# ============================================================================ Q6


def test_q6_the_pages_apply_renames_only_what_was_approved_and_refuses_a_plan_with_none(
    tmp_path, spy
):
    """F1 (vision T3) through the page's moving route. A plan with NOTHING approved has no
    Confirm (the CLI's F1 refusal, a 409, and no nonce), so the route has nothing to take: a
    made-up nonce is U14's 409. Then ONE name is approved and the page's apply renames exactly
    it; the other five items are still undecided afterwards and their files untouched.

    Mutation: `validator.ACTIONABLE_STATUSES` regaining `proposed` -> the second run renames all
    six (F1's own defect, through the page)."""
    root, plan_id = _q_archive(tmp_path)
    with served(root) as server:
        none = call(server, f"plan/{plan_id}/confirmation")
        guessed = call(server, f"plan/{plan_id}/apply", "POST", _apply_body("0" * 32))
        assert _ok(server, "job")["state"] == "idle"
        assert spy == []
        _approve(server, root, plan_id, "B.JPG")
        finished = _page_apply(server, plan_id)
    assert (none.status, none.json()) == (
        409,
        {
            "error": f"Nothing in plan {plan_id} is approved. Run pam review {plan_id} to approve"
            f" names, or pam apply {plan_id} --all to accept everything except low-confidence"
            " names."
        },
    )
    assert (guessed.status, guessed.json()) == (409, {"error": CHANGED})
    assert finished["result"]["renamed"] == 1, finished
    assert spy == _pairs("B.JPG")
    assert _statuses(root, plan_id) == {
        name: ("applied" if name == "B.JPG" else "proposed") for name in Q_NEW
    }


def test_q6_the_selection_turns_no_undecided_item_into_a_rename(tmp_path, spy):
    """The selection's moves. Its PREVIEW of `select all` decides nothing. `select all`
    approved WITHOUT `include_low`, over a plan that holds a low name, is refused WHOLE (U3: a
    low name is approved only when the person says so, against the count the page showed) and
    decides nothing - so there is still no Confirm. `select all` minus the low single
    (`except_ids`) approves the other five, and the page's apply renames exactly those; the low
    single stays undecided and on disk under its own name.

    Mutation: `decide-selection`'s low check deleted -> the second request approves D.JPG and
    the apply renames it."""
    root, plan_id = _q_archive(tmp_path)
    everything = {"view": {}, "all": True}
    with served(root) as server:
        preview = _preview(server, plan_id, everything)
        undecided = _statuses(root, plan_id)
        refused = call(
            server,
            f"plan/{plan_id}/decide-selection",
            "POST",
            {"selection": everything, "action": "approve"},
        )
        after_refusal = _statuses(root, plan_id)
        no_confirm = call(server, f"plan/{plan_id}/confirmation").status
        but_low = {"view": {}, "all": True, "except_ids": _ids(root, plan_id, LOW_SINGLE)}
        written = _decide_selection(server, plan_id, but_low)
        finished = _page_apply(server, plan_id)
    assert (preview["items"], preview["units"], preview["low"]) == (6, 4, 1)
    assert set(undecided.values()) == set(after_refusal.values()) == {"proposed"}
    assert refused.status == 409 and "need review" in refused.json()["error"], refused
    assert no_confirm == 409
    assert written["decided"] == 5, written
    assert finished["result"]["renamed"] == 5, finished
    assert sorted(spy) == _pairs("A.JPG", "B.JPG", *LIVE)
    assert _statuses(root, plan_id)[LOW_SINGLE] == "proposed"
    assert LOW_SINGLE in _on_disk(root)


@pytest.mark.parametrize("shortcut", ["accept_all", "q=MOV"])
def test_q6_the_shortcuts_turn_no_undecided_item_into_a_rename(tmp_path, spy, shortcut):
    """The one-tap shortcuts (U3, OD-1). `Accept all` approves everything except Low and leaves
    the low single undecided; a shortcut over `q=MOV` approves the Live Photo (whole) and
    nothing else. The page's apply renames exactly what the shortcut approved.

    Mutation: `accept_all`'s low exclusion deleted -> D.JPG is renamed without a person ever
    choosing a low name."""
    root, plan_id = _q_archive(tmp_path)
    if shortcut == "accept_all":
        body = {"action": "approve", "accept_all": True}
        renamed = ("A.JPG", "B.JPG", *LIVE)
    else:
        body = {"action": "approve", "shortcut": True, "q": "MOV"}
        renamed = tuple(LIVE)
    with served(root) as server:
        _ok(server, f"plan/{plan_id}/decide-filter", "POST", body)
        finished = _page_apply(server, plan_id)
    assert finished["result"]["renamed"] == len(renamed), finished
    assert sorted(spy) == _pairs(*renamed)
    assert _statuses(root, plan_id) == {
        name: ("applied" if name in renamed else "proposed") for name in Q_NEW
    }


def test_q6_recovery_after_a_page_rename_dies_renames_no_undecided_item(
    tmp_path, spy, set_fault
):
    """The page's rename job dies after the Live Photo's second member (the fault), with A.JPG
    and the unit approved and B.JPG and the low single undecided. The next request heals it
    (U11, design 7.2): recovery only PUTS BACK - every call after the crash is a reverse
    rename of a member the job had renamed - and the two undecided files are never handed to
    the verified rename, before or after. Their items are still undecided.

    Mutation: recovery rolling a unit FORWARD (finishing the rename) -> a third forward call."""
    root, plan_id = _q_archive(tmp_path)
    set_fault.arm("crash:mid_companion_unit:2")
    with served(root) as server:
        _approve(server, root, plan_id, "A.JPG", "IMG_1.MOV", decided=4)
        finished = _page_apply(server, plan_id)
        crashed = list(spy)
        _ok(server, "job")  # a request heals the dead job first (U11)
    assert finished["state"] == "failed", finished
    healed = spy[len(crashed) :]
    forward = set(_pairs("A.JPG", *LIVE))
    assert set(crashed) <= forward, crashed
    assert healed and all((new, old) in forward for old, new in healed), healed
    assert not {old for old, _ in spy} & {"B.JPG", LOW_SINGLE}
    assert (_statuses(root, plan_id)["B.JPG"], _statuses(root, plan_id)[LOW_SINGLE]) == (
        "proposed",
        "proposed",
    )
    assert {"B.JPG", LOW_SINGLE} <= set(_on_disk(root))


# ============================================================================ Q7
#
# Which request reaches a rename without a person at the page? The setup of every case is the
# DANGEROUS state: four names approved (A.JPG and the Live Photo, whole) and a Confirm screen
# OPEN, its nonce current and unused - so the request a person's `Rename` click sends WOULD
# rename four files (`test_q7_the_control_...` proves it). Each case changes ONE thing about
# that request, and must rename nothing. In the two cases in which a person DID click once
# (the replay, and the race), the one click renames the four once, and the second request
# renames nothing more.

Q7_APPROVED = ("A.JPG", *LIVE)


class Q7:
    """One case's world: the bound server, the archive, the plan and the open Confirm's nonce."""

    def __init__(self, server, root, plan_id, nonce, capsys, monkeypatch):
        self.server, self.root, self.plan_id, self.nonce = server, root, plan_id, nonce
        self.capsys, self.monkeypatch = capsys, monkeypatch

    @property
    def apply_path(self) -> str:
        return f"/t/{self.server.token}/api/plan/{self.plan_id}/apply"

    def body(self) -> bytes:
        return json.dumps(_apply_body(self.nonce)).encode()

    def post(self, path=None, body=None, **headers) -> tuple[int, bytes]:
        """The request the page sends on `Rename`, with ONE thing changed by the caller."""
        sent = _page_headers(self.server)
        sent.update({name.replace("_", "-"): value for name, value in headers.items()})
        return _raw(
            self.server,
            "POST",
            path or self.apply_path,
            self.body() if body is None else body,
            sent,
        )

    def job_ended(self) -> dict:
        return await_state(self.server, "done", "failed", "cancelled")


def _q7_no_token(w: Q7) -> list:
    """A stray POST from another tab, which cannot know the token: the full body, the page's
    own headers, and no token in the path (then a wrong one of the right length)."""
    assert w.post(f"/api/plan/{w.plan_id}/apply") == (404, b"")
    wrong = "x" * len(w.server.token)
    assert w.post(f"/t/{wrong}/api/plan/{w.plan_id}/apply") == (404, b"")
    return []


def _q7_no_nonce(w: Q7) -> list:
    """The token, the page's headers, the tick - and no nonce."""
    status, body = w.post(body=json.dumps({"folder_confirmed": True}).encode())
    assert (status, json.loads(body)) == (409, {"error": CHANGED})
    return []


def _q7_replayed_nonce(w: Q7) -> list:
    """The person's one click renames the four; the same request sent again renames nothing."""
    assert w.post()[0] == 200
    assert w.job_ended()["result"]["renamed"] == 4
    status, body = w.post()
    assert (status, json.loads(body)) == (409, {"error": CHANGED})
    return _pairs(*Q7_APPROVED)


def _q7_superseded_plan_nonce(w: Q7) -> list:
    """A read job publishes P2 (superseding P) and the person approves the same names in it. P's
    nonce, sent to P, starts a job that the preflight refuses (P is not open); sent to P2, it
    is not P2's nonce."""
    new = _read_job(w.server)
    _approve(w.server, w.root, new, "A.JPG", "IMG_1.HEIC", decided=4)
    assert w.post()[0] == 200
    ended = w.job_ended()
    # Pinned by the Task 8 review (8b left "failed or cancelled" open): the preflight refuses a
    # plan that is not open BEFORE the nonce's digest is compared, with the CLI's sentence.
    assert (ended["state"], ended["lines"]) == (
        "failed",
        [f"Plan {w.plan_id} was superseded by a newer plan. Run: pam plan"],
    ), ended
    status, body = w.post(f"/t/{w.server.token}/api/plan/{new}/apply")
    assert (status, json.loads(body)) == (409, {"error": CHANGED})
    assert _plan_status(w.root, new) == "open"
    assert set(_statuses(w.root, new).values()) == {"approved", "proposed"}
    return []


def _q7_a_get(w: Q7) -> list:
    """The moving route asked for by GET, the nonce in the query: no GET route exists."""
    query = urllib.parse.urlencode({"nonce": w.nonce, "folder_confirmed": "true"})
    assert _raw(w.server, "GET", f"{w.apply_path}?{query}", None, {}) == (404, b"")
    return []


def _q7_form_post(w: Q7) -> list:
    """An HTML form's POST (a cross-site form needs no script): the right fields, form-encoded,
    and as `text/plain` (the other type a form may send)."""
    form = urllib.parse.urlencode({"nonce": w.nonce, "folder_confirmed": "true"}).encode()
    assert w.post(body=form, Content_Type="application/x-www-form-urlencoded") == (404, b"")
    assert w.post(Content_Type="text/plain") == (404, b"")
    return []


def _q7_foreign_origin(w: Q7) -> list:
    """Everything right, from a page on another origin (and from `localhost`, which is not this
    server's own origin either)."""
    assert w.post(Origin="http://attacker.example") == (404, b"")
    assert w.post(Origin=f"http://localhost:{w.server.port}") == (404, b"")
    return []


def _q7_rebound_host(w: Q7) -> list:
    """DNS rebinding: a name the attacker controls now resolves to 127.0.0.1, so the browser
    sends that name as the Host (with and without an Origin to match)."""
    rebound = f"attacker.example:{w.server.port}"
    assert w.post(Host=rebound, Origin=f"http://{rebound}") == (404, b"")
    assert w.post(Host=rebound) == (404, b"")
    return []


def _q7_two_tabs_racing(w: Q7) -> list:
    """Two tabs send the SAME nonce at the same moment: one starts the job, the other is U14's
    409; the four are renamed once."""
    gate = threading.Barrier(2)
    answers: list[int] = []

    def tab():
        gate.wait()
        answers.append(w.post()[0])

    tabs = [threading.Thread(target=tab) for _ in range(2)]
    for each in tabs:
        each.start()
    for each in tabs:
        each.join(timeout=60)
    assert sorted(answers) == [200, 409], answers
    assert w.job_ended()["result"]["renamed"] == 4
    return _pairs(*Q7_APPROVED)


def _q7_the_cli_concurrently(w: Q7) -> list:
    """`pam apply`, and `pam apply --all`, from the console while the page holds the archive:
    the workspace lock refuses each before anything is read."""
    for extra in ([], ["--all"]):
        code, out, err = m13._pam(
            w.capsys,
            "apply",
            str(w.plan_id),
            *extra,
            "--archive",
            str(w.root),
            typed="apply",
            monkeypatch=w.monkeypatch,
        )
        assert code != 0 and LOCKED in out + err, (extra, code, out, err)
    return []


# Q7's requests, BY HAND, from the plan's list (Task 8, Q7), in its order.
Q7_REQUESTS = (
    "a stray POST from another tab (no token)",
    "a token-bearing POST without a nonce",
    "a replayed nonce",
    "a nonce from a superseded plan",
    "a GET",
    "a form POST",
    "a foreign Origin",
    "a rebound Host",
    "two tabs racing one nonce",
    "the CLI concurrently",
)
# The builders: the enumeration the hand-written list is held against, in both directions.
Q7_BUILDERS = {
    "a stray POST from another tab (no token)": _q7_no_token,
    "a token-bearing POST without a nonce": _q7_no_nonce,
    "a replayed nonce": _q7_replayed_nonce,
    "a nonce from a superseded plan": _q7_superseded_plan_nonce,
    "a GET": _q7_a_get,
    "a form POST": _q7_form_post,
    "a foreign Origin": _q7_foreign_origin,
    "a rebound Host": _q7_rebound_host,
    "two tabs racing one nonce": _q7_two_tabs_racing,
    "the CLI concurrently": _q7_the_cli_concurrently,
}
# The floor, SPELLED (Step 4): ten requests, each built. Cutting the list fails collection.
assert len(Q7_REQUESTS) == 10, "Q7 builds TEN requests (plan Task 8); the list was cut"
assert len(set(Q7_REQUESTS)) == len(Q7_REQUESTS)
assert set(Q7_REQUESTS) == set(Q7_BUILDERS), sorted(set(Q7_REQUESTS) ^ set(Q7_BUILDERS))


@contextmanager
def _q7_world(tmp_path, capsys, monkeypatch) -> Iterator[Q7]:
    root, plan_id = _q_archive(tmp_path)
    with served(root) as server:
        _approve(server, root, plan_id, "A.JPG", "IMG_1.HEIC", decided=4)
        nonce = _ok(server, f"plan/{plan_id}/confirmation")["nonce"]
        yield Q7(server, root, plan_id, nonce, capsys, monkeypatch)


def test_q7_the_control_the_persons_own_click_renames_the_four(
    tmp_path, capsys, monkeypatch, spy
):
    """THE POSITIVE CONTROL for every Q7 case: the setup is live. The request the page sends on
    `Rename` - the one each case changes one thing of - renames exactly the four."""
    with _q7_world(tmp_path, capsys, monkeypatch) as w:
        assert w.post()[0] == 200
        assert w.job_ended()["result"]["renamed"] == 4
    assert sorted(spy) == _pairs(*Q7_APPROVED)


@pytest.mark.parametrize("request_", Q7_REQUESTS)
def test_q7_no_request_without_a_person_at_the_page_reaches_a_rename(
    tmp_path, capsys, monkeypatch, spy, request_
):
    """Each case built against the real, bound `pam start` server with the dangerous state set
    up. The spy is the measure (M697): it holds exactly what one person's click renamed, which
    for eight of the ten is nothing. The disk and every approved item's status agree.

    Mutation: any one of U14's conditions deleted (`refused_before_parse`'s Content-Type, Origin
    or Host operand; `Nonces.take`'s `used`; the nonce's plan key) -> its case renames four."""
    with _q7_world(tmp_path, capsys, monkeypatch) as w:
        clicked = Q7_BUILDERS[request_](w)
    assert sorted(spy) == clicked
    renamed = {old for old, _ in clicked}
    assert _on_disk(w.root) == sorted(Q_NEW[n] if n in renamed else n for n in ORIGINAL)
    if request_ != "a nonce from a superseded plan":
        expect = "applied" if renamed else "approved"
        assert {n: s for n, s in _statuses(w.root, w.plan_id).items() if n in Q7_APPROVED} == {
            n: expect for n in Q7_APPROVED
        }


# Q7's OTHER moving route (the Task 8 review): `session/{id}/undo`. The plan says "each is built",
# and the two routes share `refused_before_parse` and `Nonces` - but sharing is a claim about the
# code, and a route left out of `MOVING_ROUTES`, or an undo that skips `_take`, renamed four
# files back with every rename case above green. The dangerous state: the page renamed A.JPG
# and the Live Photo (session S), and S's session screen is OPEN, its undo nonce current and
# unused - so the page's `Undo` request WOULD put four files back (the control). Each case
# changes one thing about that request and must put back nothing (the spy, M697). The
# superseded-plan case's twin here is a nonce ISSUED for another session, sent to that
# session's route while that session's own nonce is current (a wrong string against a live
# issue: the only case in the hunt that reaches `Nonces.take`'s comparison).

RESTORED = sorted((Q_NEW[name], name) for name in Q7_APPROVED)


class Q7Undo(Q7):
    """Q7's world for the undo route: `session` is S, `nonce` S's undo nonce."""

    def __init__(self, server, root, plan_id, session, nonce, capsys, monkeypatch):
        super().__init__(server, root, plan_id, nonce, capsys, monkeypatch)
        self.session = session

    @property
    def apply_path(self) -> str:  # the route each case changes one thing of
        return f"/t/{self.server.token}/api/session/{self.session}/undo"

    def body(self) -> bytes:
        return json.dumps({"nonce": self.nonce}).encode()


def _u_no_token(w: Q7Undo) -> list:
    assert w.post(f"/api/session/{w.session}/undo") == (404, b"")
    wrong = "x" * len(w.server.token)
    assert w.post(f"/t/{wrong}/api/session/{w.session}/undo") == (404, b"")
    return []


def _u_no_nonce(w: Q7Undo) -> list:
    status, body = w.post(body=b"{}")
    assert (status, json.loads(body)) == (409, {"error": CHANGED})
    return []


def _u_replayed_nonce(w: Q7Undo) -> list:
    assert w.post()[0] == 200
    assert w.job_ended()["result"]["restored"] == 4
    status, body = w.post()
    assert (status, json.loads(body)) == (409, {"error": CHANGED})
    return RESTORED


def _u_another_sessions_nonce(w: Q7Undo) -> list:
    """A read job publishes P2; B.JPG is approved and renamed in it (session S2), and S2's
    session screen is opened too, so BOTH nonces are current. S's nonce sent to S2's route,
    and S2's sent to S's, are each U14's 409: a nonce names one session."""
    new = _read_job(w.server)
    _approve(w.server, w.root, new, "B.JPG")
    s2 = _page_apply(w.server, new)
    assert s2["result"]["renamed"] == 1, s2
    s2_nonce = _ok(w.server, f"session/{s2['session']}")["nonce"]
    status, body = w.post(f"/t/{w.server.token}/api/session/{s2['session']}/undo")
    assert (status, json.loads(body)) == (409, {"error": CHANGED})
    status, body = w.post(body=json.dumps({"nonce": s2_nonce}).encode())
    assert (status, json.loads(body)) == (409, {"error": CHANGED})
    return [("B.JPG", Q_NEW["B.JPG"])]


def _u_a_get(w: Q7Undo) -> list:
    query = urllib.parse.urlencode({"nonce": w.nonce})
    assert _raw(w.server, "GET", f"{w.apply_path}?{query}", None, {}) == (404, b"")
    return []


def _u_form_post(w: Q7Undo) -> list:
    form = urllib.parse.urlencode({"nonce": w.nonce}).encode()
    assert w.post(body=form, Content_Type="application/x-www-form-urlencoded") == (404, b"")
    assert w.post(Content_Type="text/plain") == (404, b"")
    return []


def _u_foreign_origin(w: Q7Undo) -> list:
    assert w.post(Origin="http://attacker.example") == (404, b"")
    assert w.post(Origin=f"http://localhost:{w.server.port}") == (404, b"")
    return []


def _u_rebound_host(w: Q7Undo) -> list:
    rebound = f"attacker.example:{w.server.port}"
    assert w.post(Host=rebound, Origin=f"http://{rebound}") == (404, b"")
    assert w.post(Host=rebound) == (404, b"")
    return []


def _u_two_tabs_racing(w: Q7Undo) -> list:
    gate = threading.Barrier(2)
    answers: list[int] = []

    def tab():
        gate.wait()
        answers.append(w.post()[0])

    tabs = [threading.Thread(target=tab) for _ in range(2)]
    for each in tabs:
        each.start()
    for each in tabs:
        each.join(timeout=60)
    assert sorted(answers) == [200, 409], answers
    assert w.job_ended()["result"]["restored"] == 4
    return RESTORED


def _u_the_cli_concurrently(w: Q7Undo) -> list:
    code, out, err = m13._pam(w.capsys, "undo", w.session, "--archive", str(w.root))
    assert code != 0 and LOCKED in out + err, (code, out, err)
    return []


# The undo route's ten, BY HAND, in the plan's order (the fourth is the superseded plan's twin).
Q7_UNDO_REQUESTS = (
    "a stray POST from another tab (no token)",
    "a token-bearing POST without a nonce",
    "a replayed nonce",
    "a nonce issued for another session",
    "a GET",
    "a form POST",
    "a foreign Origin",
    "a rebound Host",
    "two tabs racing one nonce",
    "the CLI concurrently",
)
Q7_UNDO_BUILDERS = {
    "a stray POST from another tab (no token)": _u_no_token,
    "a token-bearing POST without a nonce": _u_no_nonce,
    "a replayed nonce": _u_replayed_nonce,
    "a nonce issued for another session": _u_another_sessions_nonce,
    "a GET": _u_a_get,
    "a form POST": _u_form_post,
    "a foreign Origin": _u_foreign_origin,
    "a rebound Host": _u_rebound_host,
    "two tabs racing one nonce": _u_two_tabs_racing,
    "the CLI concurrently": _u_the_cli_concurrently,
}
assert len(Q7_UNDO_REQUESTS) == 10, "Q7 builds TEN requests per moving route; the list was cut"
assert len(set(Q7_UNDO_REQUESTS)) == len(Q7_UNDO_REQUESTS)
assert set(Q7_UNDO_REQUESTS) == set(Q7_UNDO_BUILDERS), sorted(
    set(Q7_UNDO_REQUESTS) ^ set(Q7_UNDO_BUILDERS)
)


@contextmanager
def _q7_undo_world(tmp_path, capsys, monkeypatch, spy) -> Iterator[Q7Undo]:
    root, plan_id = _q_archive(tmp_path)
    with served(root) as server:
        _approve(server, root, plan_id, "A.JPG", "IMG_1.HEIC", decided=4)
        applied = _page_apply(server, plan_id)
        assert applied["result"]["renamed"] == 4, applied
        session = applied["session"]
        nonce = _ok(server, f"session/{session}")["nonce"]
        spy.clear()
        yield Q7Undo(server, root, plan_id, session, nonce, capsys, monkeypatch)


def test_q7_the_control_the_persons_own_undo_click_restores_the_four(
    tmp_path, capsys, monkeypatch, spy
):
    """THE POSITIVE CONTROL for every undo case: the page's own `Undo` request puts the four
    back."""
    with _q7_undo_world(tmp_path, capsys, monkeypatch, spy) as w:
        assert w.post()[0] == 200
        assert w.job_ended()["result"]["restored"] == 4
    assert sorted(spy) == RESTORED
    assert _on_disk(w.root) == ORIGINAL


@pytest.mark.parametrize("request_", Q7_UNDO_REQUESTS)
def test_q7_no_request_without_a_person_at_the_page_reaches_an_undo(
    tmp_path, capsys, monkeypatch, spy, request_
):
    """Each case against the real, bound server, S applied and its undo nonce current. The spy
    holds exactly what a person's one click did (for eight of the ten: nothing put back; the
    other-session case also holds the person's own rename of B.JPG in P2), the disk agrees, and
    S's items are `applied` unless that one click undid them.

    Mutation: `UNDO_ROUTE` out of `MOVING_ROUTES`, or `api_undo` not taking the nonce, or
    `Nonces.take`'s comparison deleted -> a case here puts four files back."""
    with _q7_undo_world(tmp_path, capsys, monkeypatch, spy) as w:
        done = Q7_UNDO_BUILDERS[request_](w)
    assert sorted(spy) == sorted(done)
    disk = {Q_NEW[n] if n in Q7_APPROVED else n for n in ORIGINAL}
    for old, new in done:
        disk.remove(old)
        disk.add(new)
    assert _on_disk(w.root) == sorted(disk)
    expect = "undone" if done == RESTORED else "applied"
    assert {n: s for n, s in _statuses(w.root, w.plan_id).items() if n in Q7_APPROVED} == {
        n: expect for n in Q7_APPROVED
    }


# ============================================================================ Q8
#
# What does pam decide from the CATALOG'S copy of a fact the OS can change without changing a
# file's name, size or mtime? The fact is `files.placeholder`; the change is Task 0B's: the
# REAL `FILE_ATTRIBUTE_OFFLINE` set with `SetFileAttributesW` (what OneDrive's "Free up space"
# leaves), between the read that recorded "local" and the page's use of it. The instrument is
# Task 0B's read spy (`tests/test_live_placeholder.py`'s `Spy` and hooks: `builtins.open` and
# `io.open` in read modes, `extract_binary`, `ExifTool.extract`), whose own all-hooks control is
# `test_the_spy_sees_a_local_file_on_every_hook`; each case below adds its own control where its
# reader has one.

windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="FILE_ATTRIBUTE_OFFLINE is a Windows file attribute"
)


@pytest.fixture()
def reads(monkeypatch):
    """Every path handed to a reader of archive content, by hook (Task 0B's instrument)."""
    import builtins
    import io

    import test_live_placeholder as live

    from pam.scan.exiftool import ExifTool

    watch = live.Spy()
    real_builtin_open, real_io_open = builtins.open, io.open
    real_extract_binary, real_extract = exiftool_mod.extract_binary, ExifTool.extract

    def builtin_open(file, *args, **kwargs):
        if live._reads(args, kwargs):
            watch.record("builtins.open", file)
        return real_builtin_open(file, *args, **kwargs)

    def io_open(file, *args, **kwargs):
        if live._reads(args, kwargs):
            watch.record("io.open", file)
        return real_io_open(file, *args, **kwargs)

    def extract_binary(source, attempts):
        watch.record("extract_binary", source)
        return real_extract_binary(source, attempts)

    def extract(self, paths, media_type="image"):
        for path in paths:
            watch.record("ExifTool.extract", path)
        return real_extract(self, paths, media_type=media_type)

    monkeypatch.setattr(builtins, "open", builtin_open)
    monkeypatch.setattr(io, "open", io_open)
    monkeypatch.setattr(exiftool_mod, "extract_binary", extract_binary)
    monkeypatch.setattr(ExifTool, "extract", extract)
    return watch


def _from_now(reads) -> None:
    """The moment the OS changed the file: what the SETUP read (the first scan) is forgotten."""
    reads.seen = {hook: [] for hook in reads.seen}


@pytest.fixture()
def offline():
    """`offline(path)` sets the REAL FILE_ATTRIBUTE_OFFLINE; each is restored at teardown."""
    import test_live_placeholder as live

    with live._offline_attributes() as set_offline:
        yield set_offline


def _file_ids(root: Path) -> dict[str, int]:
    conn = _ro(root)
    try:
        return {r[0]: r[1] for r in conn.execute("SELECT rel_path, id FROM files")}
    finally:
        conn.close()


def _placeholder_flags(root: Path) -> dict[str, int]:
    conn = _ro(root)
    try:
        return {r[0]: r[1] for r in conn.execute("SELECT rel_path, placeholder FROM files")}
    finally:
        conn.close()


def test_q8_this_modules_read_spy_sees_a_file_opened_for_reading_by_either_open(
    tmp_path, reads
):
    """THE CONTROL for the `reads` fixture's two `open` hooks (the Task 8 review). The fixture
    RE-INSTALLS Task 0B's hooks here, so Task 0B's own all-hooks control does not reach these
    two: blinded, they left Q8's "the rename reads nothing" green over any content read. Its
    other two hooks have their controls in the thumbnail (`extract_binary`) and read-job
    (`ExifTool.extract`) cases. A write is not a read."""
    import builtins
    import io

    target = tmp_path / "X.JPG"
    target.write_bytes(b"local bytes")
    _from_now(reads)
    reads.assert_never_read(target)  # the write above was not recorded
    with builtins.open(target, "rb") as handle:
        assert handle.read() == b"local bytes"
    reads.assert_read(["builtins.open"], target)
    _from_now(reads)
    with io.open(target, "rb") as handle:  # noqa: UP020 - the io hook itself, on purpose
        assert handle.read() == b"local bytes"
    reads.assert_read(["io.open"], target)


@windows_only
def test_q8_the_lists_thumbnail_never_reads_a_file_that_became_cloud_only(
    tmp_path, reads, offline
):
    """The page's list asks for each row's thumbnail. B.JPG was local at the read (its catalog
    row says so) and is cloud-only now: its thumbnail is a 404 and B.JPG is handed to no reader.
    THE CONTROL: A.JPG's thumbnail, asked in the same breath, IS extracted (the spy saw it).

    Mutation: `get_thumb`'s `placeholder_now` check deleted -> `extract_binary` reads B.JPG."""
    root, _plan_id = _q_archive(tmp_path)
    ids = _file_ids(root)
    assert _placeholder_flags(root)["B.JPG"] == 0  # the catalog's copy: local
    offline(root / "B.JPG")
    _from_now(reads)
    with served(root) as server:
        cloud = call(server, f"thumb/{ids['B.JPG']}?variant=grid")
        local = call(server, f"thumb/{ids['A.JPG']}?variant=grid")
    assert cloud.status == 404, cloud
    assert local.status in (200, 404), local
    reads.assert_read(["extract_binary"], root / "A.JPG")
    reads.assert_never_read(root / "B.JPG")


@windows_only
def test_q8_the_folder_chooser_reads_no_file_in_the_folders_it_lists(tmp_path, reads, offline):
    """The folder chooser (U13, A10) lists the archive's folder, which holds a sub-folder whose
    photograph became cloud-only after the read. The answer names the sub-folder, and NO file
    under the archive - the cloud-only one or any other - is handed to a reader.

    Mutation: the listing counting photos per folder by opening them -> the spy sees a file."""
    root, _plan_id = _q_archive(tmp_path)
    inner = root / "Sub"
    inner.mkdir()
    put(inner, "E.JPG", "2022:02:02 02:02:02")
    offline(inner / "E.JPG")
    _from_now(reads)
    with served(root) as server:
        listed = _ok(server, "folders?" + urllib.parse.urlencode({"path": str(root)}))
    assert [f["name"] for f in listed["folders"]] == ["Sub"], listed
    for path in [*root.iterdir(), *inner.iterdir()]:
        if path.is_file():
            reads.assert_never_read(path)


@windows_only
def test_q8_the_read_jobs_progress_does_not_read_a_file_that_became_cloud_only(
    tmp_path, reads, offline
):
    """The page reads again. Between the first read and this one, B.JPG became cloud-only (its
    name, size and mtime unchanged) and the user added C.JPG. The read job reads C.JPG - THE
    CONTROL, the spy saw it through exiftool - and hands B.JPG to no reader. Its catalog row is
    now a placeholder, and the plan the job publishes has no item for it (placeholders stay out
    of plans, design 5.2 rev 34), so nothing downstream renames it on the strength of the copy.

    Mutation: the scanner's change detection ignoring the placeholder flag -> B.JPG keeps its
    stale `placeholder = 0` and its item."""
    root, plan_id = _q_archive(tmp_path)
    put(root, "C.JPG", "2021:01:01 00:00:00")
    offline(root / "B.JPG")
    _from_now(reads)
    with served(root) as server:
        new = _read_job(server)
        job = _ok(server, "job")
    reads.assert_read(["ExifTool.extract"], root / "C.JPG")
    reads.assert_never_read(root / "B.JPG")
    assert _placeholder_flags(root)["B.JPG"] == 1
    assert "B.JPG" in _items(root, plan_id) and "B.JPG" not in _items(root, new)
    assert "C.JPG" in _items(root, new)
    assert (job["state"], job["done"]) == ("done", job["total"]) and job["total"] > 0, job


@windows_only
def test_q8_confirm_counts_and_the_rename_read_nothing_of_a_file_that_became_cloud_only(
    tmp_path, spy, reads, offline
):
    """The person approved A.JPG and B.JPG while both were local; B.JPG then became cloud-only.
    Confirm counts two renames (it reads nothing of the files), the job renames ONE and skips
    B.JPG's unit (AM2, M15 Task 2: `placeholder_now`, a stat, immediately before the unit's first
    rename), and B.JPG's CONTENT is handed to no reader - the probe and the rename's
    verification (design 7.1 step 1) read a directory entry and hydrate nothing - and the undo
    puts A.JPG back, again reading nothing.

    This case pinned the rename of B.JPG as measured until Q8-D1 was ruled (`m14.md` 2.3, AM2):
    a rename is a change the sync client must replay, so `pam apply` and the page's rename now
    skip a unit any member of which is cloud-only NOW. `tests/test_apply_cloud_only_now.py`
    owns AM2 through both routes; this case keeps Q8's read oracle over it.

    Mutation: the rename's verification hashing the file (a content read) -> the spy sees A.JPG
    through `io.open`; AM2's probe opening the file -> the spy sees B.JPG."""
    root, plan_id = _q_archive(tmp_path)
    with served(root) as server:
        _approve(server, root, plan_id, "A.JPG", "B.JPG")
        offline(root / "B.JPG")
        _from_now(reads)
        facts = _ok(server, f"plan/{plan_id}/confirmation")
        applied = _page_apply(server, plan_id)
        forward = sorted(spy)
        undone = _page_undo(server, applied["session"])
    assert facts["renames"] == 2, facts
    assert (applied["result"]["renamed"], applied["result"]["cloud_only"]) == (1, 1), applied
    assert forward == _pairs("A.JPG")
    assert undone["result"]["restored"] == 1, undone
    assert _on_disk(root) == ORIGINAL
    reads.assert_never_read(root / "B.JPG")
    reads.assert_never_read(root / Q_NEW["B.JPG"])


# ============================================================================ STEP 4: the floors

# The hunt's tests, BY HAND, question by question, with each question's floor SPELLED. A test
# renamed, deleted or moved out of its question fails here at COLLECTION; the census entry for
# this module (`tests/census.py`) is the second floor, outside the module (a list cut together
# with its own count is green inside the module that holds both - M2352).
HUNT = {
    "Step 1": (
        "test_step1_the_chain_through_the_page_and_through_the_console_is_one_implementation",
    ),
    "Q1": (
        "test_q1_a_list_group_holds_every_member_of_a_unit_whose_sidecar_has_no_capture",
        "test_q1_except_ids_naming_one_member_is_widened_back_and_named_overridden",
        "test_q1_a_shift_click_range_ending_inside_a_unit_writes_and_renames_the_unit_whole",
        "test_q1_a_show_more_boundary_inside_a_unit_selects_the_whole_unit",
    ),
    "Q2": ("test_q2_the_members_the_page_never_showed_are_counted_confirmed_and_renamed",),
    "Q3": (
        "test_q3_the_live_route_table_of_pam_start_is_exactly_the_hand_written_one",
        "test_q3_every_read_route_and_the_preview_change_no_catalog_row_and_rename_nothing",
        "test_q3_every_plan_write_route_of_pam_start_writes_the_whole_unit_from_one_member",
        "test_q3_the_two_moving_routes_act_on_whole_units_only",
    ),
    "Q4": (
        "test_q4_label_and_dismiss_through_pam_start_write_exactly_the_faces_the_queue_offered",
        "test_q4_a_cluster_whose_every_face_is_named_is_refused_by_name_through_pam_start",
    ),
    "Q5": (
        "test_q5_a_confirm_screen_open_across_a_decision_in_another_tab_renames_nothing",
        "test_q5_a_selection_preview_held_across_a_read_job_writes_nothing_anywhere",
        "test_q5_the_look_alike_memory_does_not_outlive_its_plan",
        "test_q5_a_page_left_open_across_a_console_scan_after_the_server_stopped_renames_nothing",
        "test_q5_the_os_touches_a_file_between_confirm_and_rename",
        "test_q5_a_history_undo_of_a_session_a_later_page_rename_moved_again_moves_nothing",
    ),
    "Q6": (
        "test_q6_the_pages_apply_renames_only_what_was_approved_and_refuses_a_plan_with_none",
        "test_q6_the_selection_turns_no_undecided_item_into_a_rename",
        "test_q6_the_shortcuts_turn_no_undecided_item_into_a_rename",
        "test_q6_recovery_after_a_page_rename_dies_renames_no_undecided_item",
    ),
    "Q7": (
        "test_q7_the_control_the_persons_own_click_renames_the_four",
        "test_q7_no_request_without_a_person_at_the_page_reaches_a_rename",
        "test_q7_the_control_the_persons_own_undo_click_restores_the_four",
        "test_q7_no_request_without_a_person_at_the_page_reaches_an_undo",
    ),
    "Q8": (
        "test_q8_this_modules_read_spy_sees_a_file_opened_for_reading_by_either_open",
        "test_q8_the_lists_thumbnail_never_reads_a_file_that_became_cloud_only",
        "test_q8_the_folder_chooser_reads_no_file_in_the_folders_it_lists",
        "test_q8_the_read_jobs_progress_does_not_read_a_file_that_became_cloud_only",
        "test_q8_confirm_counts_and_the_rename_read_nothing_of_a_file_that_became_cloud_only",
    ),
    "Q9": (
        "test_q9_every_call_on_the_way_to_the_verified_rename_is_listed_and_passes_the_runs_names",
        "test_q9_the_enumerator_sees_a_call_in_each_spelling",
        "test_q9_each_path_to_the_verified_rename_lists_a_flat_folder_once_per_run",
    ),
}
# The floors, spelled from what each question builds (plan Task 8), never from `len(HUNT[q])`.
HUNT_FLOORS = {
    "Step 1": 1,
    "Q1": 4,
    "Q2": 1,
    "Q3": 4,
    "Q4": 2,
    "Q5": 6,
    "Q6": 4,
    "Q7": 4,
    "Q8": 5,
    "Q9": 3,
}
FLOORS_TEST = "test_the_hunt_holds_every_question_at_its_floor"
assert set(HUNT) == set(HUNT_FLOORS) == {"Step 1", *(f"Q{n}" for n in range(1, 10))}
for _question, _floor in HUNT_FLOORS.items():
    assert len(HUNT[_question]) >= _floor, (_question, len(HUNT[_question]), _floor)
_DEFINED = {name for name in list(globals()) if name.startswith("test_")}
_LISTED = {name for names in HUNT.values() for name in names}
assert _LISTED | {FLOORS_TEST} == _DEFINED | {FLOORS_TEST}, sorted(_LISTED ^ _DEFINED)


def test_the_hunt_holds_every_question_at_its_floor():
    """The collection-time floors above, again at run time and in both directions: every test in
    this module belongs to exactly one question, every question meets its spelled floor, and
    Q7's enumeration holds its ten requests (`Q7_REQUESTS`, floor spelled beside it)."""
    module = sys.modules[__name__]
    defined = {name for name in vars(module) if name.startswith("test_")} - {FLOORS_TEST}
    listed = [name for names in HUNT.values() for name in names]
    assert len(listed) == len(set(listed)), "a test listed under two questions"
    assert set(listed) == defined, sorted(set(listed) ^ defined)
    assert {q: len(HUNT[q]) >= HUNT_FLOORS[q] for q in HUNT} == {q: True for q in HUNT}
    assert len(Q7_REQUESTS) >= 10 and set(Q7_REQUESTS) == set(Q7_BUILDERS)
    assert len(Q7_UNDO_REQUESTS) >= 10 and set(Q7_UNDO_REQUESTS) == set(Q7_UNDO_BUILDERS)
    # The other parametrize lists, floored again HERE, outside the hunk that holds each list and
    # its own count (the Task 8 review: a list cut together with its adjacent `assert len(...)`
    # - or emptied, which pytest SKIPS - was green; M2352's shape). Spelled from the plan: Q9's
    # eight paths, Q3's seven plan-write forms.
    assert len(PATHS) >= 8, "Q9 drives EIGHT paths (plan Task 8); the list was cut"
    assert len(PLAN_WRITE_CASES) >= 7, "Q3 drives SEVEN plan-write forms; the list was cut"
