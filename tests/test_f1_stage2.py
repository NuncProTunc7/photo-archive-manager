"""F1 (HT-M12, ruled 2026-10-08) - M13 Task 1's STAGE 2: the shipping gate, driven end to end.

Task 1 changed WHICH FILES `pam apply` renames. Stage 1 proved it in-process, with a spy on the
one verified rename. This module is the second, independent pair of eyes the plan asks for
(`docs/superpowers/plans/2026-10-08-m13-hardening.md`, Task 1, "Stage 2"): it drives the REAL
`pam apply`, `pam plan` and `pam undo` as SUBPROCESSES with their stdin, decides through the REAL
review server over loopback HTTP, and counts what changed on disk with its OWN oracle.

The ruling it checks (`docs/superpowers/specs/2026-10-08-ht-m12-f1-approval-amendment.md`):

  - R1 `pam apply` renames only `approved` items; the typed `apply` confirms, never approves;
  - R3 a companion unit is decided as a unit - the server widens ONE named id to its unit;
  - R4 a plan with nothing approved is refused (exit 1, the sentence on stderr); `--all`
    accepts every undecided name except low-confidence ones, and says how many it left;
  - R5 the confirmation counts approved items only.

HOW IT IS BUILT, and why:

  - THE ORACLE IS WRITTEN HERE. `_snapshot` walks the archive and hashes every byte with
    sha256 itself; it never calls `tests/archives.py::digests`, `hashes`, or any production
    function. A rename is "a path that held these bytes before and does not now".
  - EVERY EXPECTATION IS SPELLED BY HAND FROM THE FIXTURE AND THE RULING: the file names, the
    names pam must propose (spec 6.1: `{date}_{seq}` when no event, scene or person exists),
    the counts, the sentences. Nothing is derived from a production selection or constant.
  - THE FIXTURE'S SHAPE IS ASSERTED with the test's own SQL before anything relies on it.
  - NO WALL CLOCK. Every subprocess is waited for; the server's write is read back from the
    catalog only after its HTTP reply has arrived (the handler commits before it responds).

The fixture is EIGHT files in FIVE units. The plan's sentence says "one Live-Photo pair"; eight
files over five units with three singles and a RAW+JPEG pair leaves THREE for the Live Photo, so
it is the photo, its video and its Apple edit sidecar (`.aae` is a default `scan.sidecar_exts`
entry) - the same unit `tests/test_apply_approval.py` builds.
"""

import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import threading
import webbrowser
from http.client import HTTPConnection
from pathlib import Path

import pytest
from archives import FAKE_COMMAND, put

from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan
from pam.webui.server import ReviewServer

REPO_ROOT = Path(__file__).resolve().parent.parent

# --- the fixture, as data -----------------------------------------------------------------

RAW_PAIR = ["RAW_1.JPG", "RAW_1.NEF"]  # one stem, two extensions: one unit (spec 5.4)
LIVE_UNIT = ["IMG_1.HEIC", "IMG_1.MOV", "IMG_1.AAE"]  # photo + video + edit sidecar
SINGLE_B = "B.JPG"
SINGLE_C = "C.JPG"
LOW_SINGLE = "D.JPG"  # no date anywhere: `undated_NNN`, tier `low` (spec 6.3.4)

DATES = {
    "RAW_1.JPG": "2019:07:04 18:23:11",
    "RAW_1.NEF": "2019:07:04 18:23:11",
    "IMG_1.HEIC": "2020:08:05 09:15:00",
    "IMG_1.MOV": "2020:08:05 09:15:00",
    "IMG_1.AAE": "2020:08:05 09:15:00",
    "B.JPG": "2021:09:06 11:02:03",
    "C.JPG": "2022:10:07 12:00:00",
}
ALL_EIGHT = [*RAW_PAIR, *LIVE_UNIT, SINGLE_B, SINGLE_C, LOW_SINGLE]

# What the planner proposes, spelled from spec 6.1 (no event, scene or person facts exist, so
# the stem is the capture date and a per-date sequence; every extension keeps its case).
FIRST_RENAMES = {
    "RAW_1.JPG": "2019-07-04_001.JPG",
    "RAW_1.NEF": "2019-07-04_001.NEF",
}
ALL_RENAMES = {
    "IMG_1.HEIC": "2020-08-05_001.HEIC",
    "IMG_1.MOV": "2020-08-05_001.MOV",
    "IMG_1.AAE": "2020-08-05_001.AAE",
    "B.JPG": "2021-09-06_001.JPG",
    "C.JPG": "2022-10-07_001.JPG",
}


def _undated(root: Path, name: str) -> None:
    """A file with no date anywhere. The sidecar carries only `PamTestSidecar`, so its bytes
    are unique and the oracle can tell it apart from every other file."""
    (root / name).write_bytes(f"the only copy of {name}, which has no date".encode())
    (root / f"{name}.tags.json").write_text(
        json.dumps({"PamTestSidecar": name}), encoding="utf-8"
    )


def _build(tmp_path: Path) -> Path:
    root = tmp_path / "photos"
    root.mkdir()
    for name, date in DATES.items():
        put(root, name, date)
    _undated(root, LOW_SINGLE)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    return root


# --- the oracle: walk + sha256, written here ----------------------------------------------


def _snapshot(root: Path) -> dict[str, str]:
    """Every file under `root` -> the sha256 of its bytes. Read with `open`, nothing else."""
    found: dict[str, str] = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = Path(dirpath) / name
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for block in iter(lambda: handle.read(1 << 16), b""):
                    digest.update(block)
            found[path.relative_to(root).as_posix()] = digest.hexdigest()
    return found


def _media(snapshot: dict[str, str]) -> dict[str, str]:
    """The archive's own files, without the fake exiftool's `.tags.json` sidecars."""
    return {path: sha for path, sha in snapshot.items() if not path.endswith(".tags.json")}


def _sidecars(snapshot: dict[str, str]) -> dict[str, str]:
    return {path: sha for path, sha in snapshot.items() if path.endswith(".tags.json")}


def _moves(before: dict[str, str], after: dict[str, str]) -> dict[str, str]:
    """old path -> new path for every file whose bytes now live at a different path.

    Asserts first that no bytes were lost, gained or duplicated: the multiset of digests is
    the same on both sides (every fixture file's bytes are unique by construction)."""
    assert sorted(before.values()) == sorted(after.values()), "bytes were lost or changed"
    assert len(set(before.values())) == len(before), "the fixture's bytes are not unique"
    where_now = {sha: path for path, sha in after.items()}
    return {path: where_now[sha] for path, sha in before.items() if where_now[sha] != path}


def test_the_oracle_sees_a_rename_and_a_changed_byte(tmp_path):
    """The oracle cannot be decoration: a rename is reported as one move, and a changed byte
    (or a lost file) fails it. Mutating the test's own instrument, not only production."""
    (tmp_path / "a.bin").write_bytes(b"one")
    (tmp_path / "b.bin").write_bytes(b"two")
    before = _snapshot(tmp_path)
    assert before == {
        "a.bin": hashlib.sha256(b"one").hexdigest(),
        "b.bin": hashlib.sha256(b"two").hexdigest(),
    }
    os.rename(tmp_path / "a.bin", tmp_path / "c.bin")
    assert _moves(before, _snapshot(tmp_path)) == {"a.bin": "c.bin"}
    (tmp_path / "b.bin").write_bytes(b"tw0")
    with pytest.raises(AssertionError, match="bytes were lost or changed"):
        _moves(before, _snapshot(tmp_path))
    (tmp_path / "b.bin").unlink()
    with pytest.raises(AssertionError, match="bytes were lost or changed"):
        _moves(before, _snapshot(tmp_path))


# --- the drivers --------------------------------------------------------------------------


def _pam(*args: str, stdin: str = "") -> subprocess.CompletedProcess:
    """`pam ARGS` in a FRESH interpreter, through the console entry point's own `main`.

    The environment is this test's (`PAM_HOME` is the conftest's isolated home). Waited for,
    never timed: the result is read after the process has exited."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PAM_FAULT", None)
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, "-c", "import sys; from pam.cli import main; sys.exit(main())", *args],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO_ROOT),
        env=env,
        timeout=300,
    )


def _db(root: Path) -> sqlite3.Connection:
    workspace = workspace_if_exists(root)
    assert workspace is not None
    conn = sqlite3.connect(workspace.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _items(root: Path, plan_id: int) -> dict[str, sqlite3.Row]:
    conn = _db(root)
    try:
        rows = conn.execute(
            "SELECT id, old_rel_path, unit_key, tier, status FROM plan_items WHERE plan_id = ?",
            (plan_id,),
        ).fetchall()
    finally:
        conn.close()
    return {row["old_rel_path"]: row for row in rows}


def _open_plan_id(root: Path) -> int:
    conn = _db(root)
    try:
        rows = conn.execute("SELECT id FROM plans WHERE status = 'open'").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, f"expected exactly one open plan, found {len(rows)}"
    return int(rows[0][0])


def _plan_status(root: Path, plan_id: int) -> str:
    conn = _db(root)
    try:
        return str(
            conn.execute("SELECT status FROM plans WHERE id = ?", (plan_id,)).fetchone()[0]
        )
    finally:
        conn.close()


def _journal_sessions(root: Path) -> set[str]:
    conn = _db(root)
    try:
        return {r[0] for r in conn.execute("SELECT DISTINCT session_id FROM journal")}
    finally:
        conn.close()


def _session_from(stdout: str) -> str:
    found = re.findall(r"^  Undo:   pam undo (\S+)$", stdout, flags=re.MULTILINE)
    assert len(found) == 1, stdout
    return found[0]


def _approve_through_the_server(root: Path, plan_id: int, item_id: int) -> tuple[int, dict]:
    """`pam review`'s server on its own thread; ONE `POST /decide` naming ONE id; stopped.

    An isolated logger, never the `pam` one (finding M628a). The server is shut down and its
    thread joined before this returns, so nothing it does can overlap the subprocess."""
    workspace = workspace_if_exists(root)
    assert workspace is not None
    logger = logging.getLogger("pam.tests.f1_stage2")
    logger.propagate = False
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    server = ReviewServer(workspace=workspace, plan_id=plan_id, logger=logger)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        payload = json.dumps({"item_ids": [item_id], "action": "approve"}).encode("utf-8")
        conn = HTTPConnection(server.host, server.port, timeout=30)
        try:
            conn.request(
                "POST",
                f"/t/{server.token}/api/plan/{plan_id}/decide",
                body=payload,
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": str(len(payload)),
                },
            )
            response = conn.getresponse()
            status, body = response.status, json.loads(response.read().decode("utf-8"))
        finally:
            conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=30)
    assert not thread.is_alive(), "the review server did not stop"
    return status, body


@pytest.fixture(autouse=True)
def no_real_browser(monkeypatch):
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: True)


# --- the gate ------------------------------------------------------------------------------


def test_f1_end_to_end_apply_renames_only_what_was_approved_then_all_then_undo(tmp_path):
    root = _build(tmp_path)
    original = _snapshot(root)
    assert sorted(_media(original)) == sorted(ALL_EIGHT)
    assert len(_sidecars(original)) == 8

    # --- step 0: `pam plan` N, and the fixture's shape, with the test's own SQL ------------
    planned = _pam("plan", "--archive", str(root))
    assert planned.returncode == 0, planned.stdout + planned.stderr
    plan_n = _open_plan_id(root)
    items = _items(root, plan_n)
    assert sorted(items) == sorted(ALL_EIGHT)  # 8 items
    units = {}
    for name, row in items.items():
        units.setdefault(row["unit_key"], set()).add(name)
    assert sorted(sorted(u) for u in units.values()) == sorted(
        [sorted(RAW_PAIR), sorted(LIVE_UNIT), [SINGLE_B], [SINGLE_C], [LOW_SINGLE]]
    )  # 5 units, exactly these
    low_units = {row["unit_key"] for row in items.values() if row["tier"] == "low"}
    assert low_units == {items[LOW_SINGLE]["unit_key"]}  # exactly one Low unit: D.JPG
    assert {row["status"] for row in items.values()} == {"proposed"}

    # --- step 1: approve the RAW+JPEG pair through the server, naming ONE id ---------------
    status, body = _approve_through_the_server(root, plan_n, items["RAW_1.JPG"]["id"])
    assert status == 200, body
    after_decide = _items(root, plan_n)
    assert {n: r["status"] for n, r in after_decide.items()} == {
        "RAW_1.JPG": "approved",
        "RAW_1.NEF": "approved",  # the server WIDENED the one id to its unit (R3)
        "IMG_1.HEIC": "proposed",
        "IMG_1.MOV": "proposed",
        "IMG_1.AAE": "proposed",
        "B.JPG": "proposed",
        "C.JPG": "proposed",
        "D.JPG": "proposed",
    }
    assert _snapshot(root) == original  # deciding renames nothing

    # --- step 2: `pam apply N`, `apply` typed: exactly the pair changes (R1, R5) -----------
    applied = _pam("apply", str(plan_n), "--archive", str(root), stdin="apply\n")
    assert applied.returncode == 0, applied.stdout + applied.stderr
    out = applied.stdout
    assert "  2 renames approved" in out, out
    assert "  6 not approved - they will not be renamed." in out, out
    assert "low-confidence names were not accepted" not in out, out
    assert "Done. 2 renamed, 0 failed," in out, out
    assert (
        "  2 approved, 6 not approved and left as they are. To name them later: pam plan" in out
    ), out
    session_1 = _session_from(out)
    after_first = _snapshot(root)
    assert _moves(_media(original), _media(after_first)) == FIRST_RENAMES  # 2 changed
    kept = {p for p in _media(original) if p in _media(after_first)}
    assert kept == set(ALL_EIGHT) - set(RAW_PAIR)  # 6 kept, by name
    assert len(kept) == 6
    assert _sidecars(after_first) == _sidecars(original)
    # M13-D2: a deliberately partial apply ends `partially_applied` (six were not approved).
    assert _plan_status(root, plan_n) == "partially_applied"

    # --- step 3: a fresh `pam plan` M, applied UNTOUCHED: refused, nothing moves (R4) -------
    replanned = _pam("plan", "--archive", str(root))
    assert replanned.returncode == 0, replanned.stdout + replanned.stderr
    plan_m = _open_plan_id(root)
    assert plan_m != plan_n
    items_m = _items(root, plan_m)
    # The pair already carries pam's names, so the new plan holds the other six.
    assert sorted(items_m) == sorted([*LIVE_UNIT, SINGLE_B, SINGLE_C, LOW_SINGLE])
    assert {r["status"] for r in items_m.values()} == {"proposed"}
    assert {n for n, r in items_m.items() if r["tier"] == "low"} == {LOW_SINGLE}

    refused = _pam("apply", str(plan_m), "--archive", str(root), stdin="apply\n")
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert (
        f"Nothing in plan {plan_m} is approved. Run pam review {plan_m} to approve names,"
        f" or pam apply {plan_m} --all to accept everything except low-confidence names."
    ) in refused.stderr, refused.stderr
    assert "renamed" not in refused.stdout, refused.stdout
    assert _snapshot(root) == after_first  # 0 changed
    assert {r["status"] for r in _items(root, plan_m).values()} == {"proposed"}
    assert _journal_sessions(root) == {session_1}

    # --- step 4: `pam apply M --all`: every non-Low undecided unit, the Low count named -----
    accepted = _pam("apply", str(plan_m), "--all", "--archive", str(root), stdin="apply\n")
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    out = accepted.stdout
    assert (
        f"  ! 1 low-confidence names were not accepted - approve them one at a time"
        f" in pam review {plan_m}."
    ) in out, out
    assert "  5 renames approved" in out, out
    assert "  1 not approved - they will not be renamed." in out, out
    assert "Done. 5 renamed, 0 failed," in out, out
    session_2 = _session_from(out)
    assert session_2 != session_1
    after_all = _snapshot(root)
    assert _moves(_media(after_first), _media(after_all)) == ALL_RENAMES  # 5 changed
    assert LOW_SINGLE in _media(after_all)  # the Low file kept its name
    assert _media(after_all)[LOW_SINGLE] == original[LOW_SINGLE]
    assert _sidecars(after_all) == _sidecars(original)
    statuses_m = {n: r["status"] for n, r in _items(root, plan_m).items()}
    assert statuses_m == {
        "IMG_1.HEIC": "applied",
        "IMG_1.MOV": "applied",
        "IMG_1.AAE": "applied",
        "B.JPG": "applied",
        "C.JPG": "applied",
        "D.JPG": "proposed",  # `--all` never accepts Low (R2/R4)
    }
    assert _journal_sessions(root) == {session_1, session_2}
    assert _plan_status(root, plan_m) == "partially_applied"  # M13-D2: D.JPG was left

    # --- step 5: undo both sessions, newest first; an `--exact` oracle ---------------------
    for session in (session_2, session_1):
        undone = _pam("undo", session, "--archive", str(root))
        assert undone.returncode == 0, undone.stdout + undone.stderr
    assert _snapshot(root) == original  # exact paths AND exact bytes, every file
