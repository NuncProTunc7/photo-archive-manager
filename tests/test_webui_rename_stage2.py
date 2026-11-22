"""M14 Task 6's STAGE 2: the page's rename and undo, driven through the REAL server in a SUBPROCESS.

Task 6 is M14's shipping gate: since it, the review page renames the user's photographs. Stage 1
proved it in-process with a spy on the one verified rename. This module is the plan's second,
independent pair of eyes (`docs/superpowers/plans/2026-10-29-m14-ui-redesign.md`, Task 6,
"Stage 2"): it starts `pam start <archive>` (and `pam review`) as a SEPARATE PROCESS, reads the
address from its stdout, drives it over loopback HTTP ONLY with the headers the page sends, and
measures the files on disk with `tools/ht_manifest.py` - the maintainer's own human-test instrument -
and with this module's own SQL.

HOW IT IS BUILT, and why:

  - EVERY COUNT AND EVERY PATH IS SPELLED BY HAND from the fixture below: the six names the page
    renames, the names pam proposes for them (spec 6.1: `{date}_{seq}` with no event, scene or
    person facts; a file with no metadata date is named by the mtime the fixture gives it), the
    counts the confirmation answers.
  - THE SERVER READS THROUGH A STAND-IN EXIFTOOL ON ITS PATH. `scan/exiftool.py` finds exiftool
    with `shutil.which`; a directory holding `exiftool.cmd` (which runs `tests/fake_exiftool.py`)
    is put first on the subprocess's PATH, so the server's own read job scans the fixture's
    `.tags.json` dates exactly as `run_scan(..., exiftool_command=FAKE_COMMAND)` does in-process.
    Nothing in `pam/` is told about it.
  - THE BROWSER IS NOT OPENED: the subprocess replaces `webbrowser.open` before `pam.cli.main`.
    Ctrl+Break is mapped to `KeyboardInterrupt` (a console Ctrl+C) so a server can be stopped the
    way a user stops it and print its closing block; a CRASH is `TerminateProcess` over the whole
    process tree (`tools/_proctree.py`).
  - NO WALL CLOCK DECIDES ANYTHING. A job is awaited by polling `GET /api/job` against a deadline
    that FAILS LOUDLY naming the last snapshot; a line on stdout/stderr is awaited the same way.
    The `/list` latency figures are RECORDED, never asserted (a test that measures a real clock is
    a latent hosted failure).
  - "WATCH THE DANGEROUS CALL" (M697) across a process boundary: a spy cannot reach the
    subprocess, so every refusal asserts that the JOURNAL gained no row (every rename journals its
    intent before it moves a file, spec 7.0 J1) AND that the tree is unchanged.

Run as a script for the record: `python tests/test_webui_rename_stage2.py` runs this module with
`-s`, and every helper prints the evidence it checked (`changed`'s output, `verify` lines, the job
snapshots).

STEP (i) needed `GET /api/sessions`, which is Task 7's route (X2: "Task 6 Stage 2 (i), and Task 7
Step 4"), so the controller's ruling S2-1 deferred it; Task 7's review added it here
(`test_stage2_i_an_older_session_is_undone_through_its_history_row`).
"""

import json
import os
import re
import signal
import sqlite3
import statistics
import subprocess
import sys
import sysconfig
import threading
import time
from collections.abc import Callable
from datetime import datetime
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

import pytest
from archives import put

from pam.paths import workspace_if_exists

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPO_ROOT / "tools"
FAKE_EXIFTOOL = Path(__file__).resolve().parent / "fake_exiftool.py"
sys.path.insert(0, str(TOOLS))

import _proctree  # noqa: E402

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="the process tree and Ctrl+Break are Windows-only here"
)

PRINT = os.environ.get("PAM_STAGE2_PRINT") == "1"


def _note(text: str) -> None:
    """Evidence for the record: printed when run as a script, silent under the suite."""
    if PRINT:
        print(text, flush=True)


# --- (a) the fixture, as data: N = 12 files ----------------------------------------------------

LIVE_1 = ("IMG_1.HEIC", "IMG_1.MOV")
LIVE_2 = ("IMG_2.HEIC", "IMG_2.MOV")
LIVE_3 = ("IMG_3.HEIC", "IMG_3.MOV")
RAW_PAIR = ("DSC_1.JPG", "DSC_1.NEF")
SINGLES = ("A.JPG", "B.JPG", "C.JPG")
LOW_SINGLE = "D.JPG"  # no metadata date: dated by its mtime (spec 5.3), so tier `low`
# The low single's mtime, set by the fixture: noon local time, 2015-06-07 (pam names it by it).
LOW_MTIME = (2015, 6, 7, 12, 0, 0)

DATES = {
    "IMG_1.HEIC": "2016:01:02 10:00:00",
    "IMG_1.MOV": "2016:01:02 10:00:00",
    "IMG_2.HEIC": "2017:02:03 11:00:00",
    "IMG_2.MOV": "2017:02:03 11:00:00",
    "IMG_3.HEIC": "2018:03:04 12:00:00",
    "IMG_3.MOV": "2018:03:04 12:00:00",
    "DSC_1.JPG": "2019:04:05 13:00:00",
    "DSC_1.NEF": "2019:04:05 13:00:00",
    "A.JPG": "2020:05:06 14:00:00",
    "B.JPG": "2021:06:07 15:00:00",
    "C.JPG": "2022:07:08 16:00:00",
}
TWELVE = (*LIVE_1, *LIVE_2, *LIVE_3, *RAW_PAIR, *SINGLES, LOW_SINGLE)
# Twelve photographs, each with the stand-in exiftool's `.tags.json` beside it: 24 files on disk.
FILES_ON_DISK = 24

# (b): two units and one single through `decide` (5 files), then the low single through a
# selection that acknowledges it (6). Spelled from spec 6.1, every extension keeping its case.
SIX_RENAMES = {
    "A.JPG": "2020-05-06_001.JPG",
    "D.JPG": "2015-06-07_001.JPG",
    "DSC_1.JPG": "2019-04-05_001.JPG",
    "DSC_1.NEF": "2019-04-05_001.NEF",
    "IMG_1.HEIC": "2016-01-02_001.HEIC",
    "IMG_1.MOV": "2016-01-02_001.MOV",
}

LOCKED = "Another pam command is already running against this archive."
CHANGED = "Something changed since you looked. Check the names again."
VERIFY_CLEAN = "Done. 12 checks, everything pam recorded looks right."


def _build(root: Path) -> Path:
    root.mkdir()
    for name, date in DATES.items():
        put(root, name, date)
    (root / LOW_SINGLE).write_bytes(b"the only copy of D.JPG, which has no date")
    (root / f"{LOW_SINGLE}.tags.json").write_text(
        json.dumps({"PamTestSidecar": LOW_SINGLE}), encoding="utf-8"
    )
    stamp = datetime(*LOW_MTIME).timestamp()
    os.utime(root / LOW_SINGLE, (stamp, stamp))
    return root


def _build_many(root: Path, count: int) -> Path:
    """`count` dated singles, one day apart: every name pam proposes is `<date>_001.JPG`."""
    root.mkdir()
    for n in range(count):
        year, day = 2001 + n // 300, n % 300
        month, mday = 1 + day // 28, 1 + day % 28
        put(root, f"P_{n:04d}.JPG", f"{year}:{month:02d}:{mday:02d} 09:00:00")
    return root


# --- the instruments: ht_manifest (a subprocess) and this module's own SQL ---------------------


def _manifest(*args: str) -> subprocess.CompletedProcess:
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [sys.executable, str(TOOLS / "ht_manifest.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    _note(f"$ ht_manifest {' '.join(args[:1])} ... -> exit {done.returncode}\n{done.stdout}")
    return done


def _snapshot(root: Path, manifest: Path, expect: int) -> None:
    done = _manifest("snapshot", str(root), str(manifest), "--expect", str(expect))
    assert done.returncode == 0, done.stdout


def _verify_exact(root: Path, manifest: Path, files: int) -> None:
    done = _manifest("verify", str(root), str(manifest), "--exact")
    assert done.returncode == 0, done.stdout
    assert f"PASS  {files} files, exact paths and exact content." in done.stdout, done.stdout


def _changed_lists(root: Path, manifest: Path, renames: dict[str, str], files: int) -> None:
    """`changed --expect K` PASSES for the spelled K, and the tool's own RENAMED list - printed
    when it is asked for one fewer - names exactly the spelled `old -> new` pairs."""
    k = len(renames)
    done = _manifest("changed", str(manifest), str(root), "--expect", str(k))
    assert done.returncode == 0, done.stdout
    assert f"PASS  {k} files renamed, {files - k} unchanged, every hash present" in done.stdout
    listed = _manifest("changed", str(manifest), str(root), "--expect", str(k - 1))
    assert listed.returncode == 1, listed.stdout
    pairs = sorted(f"      {old} -> {new}" for old, new in renames.items())
    assert f"  RENAMED (old -> new): {k}\n" + "\n".join(pairs) in listed.stdout, listed.stdout
    same = _manifest("verify", str(root), str(manifest), "--bytes")
    assert same.returncode == 0, same.stdout
    assert f"PASS  {files} files, every hash still present, none added." in same.stdout


def _db(root: Path) -> sqlite3.Connection:
    workspace = workspace_if_exists(root)
    assert workspace is not None
    conn = sqlite3.connect(f"file:{workspace.db_path}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _query(root: Path, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    conn = _db(root)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def _items(root: Path, plan_id: int) -> dict[str, sqlite3.Row]:
    rows = _query(
        root,
        "SELECT id, old_rel_path, unit_key, tier, status FROM plan_items WHERE plan_id = ?",
        (plan_id,),
    )
    return {row["old_rel_path"]: row for row in rows}


def _journal_rows(root: Path) -> int:
    return int(_query(root, "SELECT COUNT(*) FROM journal")[0][0])


def _sessions(root: Path) -> list[str]:
    rows = _query(root, "SELECT session_id FROM journal GROUP BY session_id ORDER BY MIN(id)")
    return [row[0] for row in rows]


def _plan_status(root: Path, plan_id: int) -> str:
    return str(_query(root, "SELECT status FROM plans WHERE id = ?", (plan_id,))[0][0])


# --- the drivers -------------------------------------------------------------------------------

# The subprocess: the browser is not opened, and Ctrl+Break is a console Ctrl+C.
#
# It runs on the BASE interpreter with this venv's site-packages added, not through
# `.venv\Scripts\python.exe`: that file is a redirector which starts the base interpreter as a
# CHILD and leaves its parent holding a handle to that child for the parent's whole life
# (measured: the grandchild stays openable after a clean exit, until the parent exits). The
# server would then be a grandchild of this test, and a killed server's lock would read as held
# to the next `pam start` for as long as pytest runs (finding S2-F3 records the production side).
PYTHON = getattr(sys, "_base_executable", sys.executable)
SITE = sysconfig.get_paths()["purelib"]
SERVE = (
    f"import site; site.addsitedir({SITE!r})\n"
    "import signal, sys, webbrowser\n"
    "webbrowser.open = lambda *a, **k: True\n"
    "signal.signal(signal.SIGBREAK, signal.default_int_handler)\n"
    "from pam.cli import main\n"
    "sys.exit(main())\n"
)
URL = re.compile(r"^Review at (http://127\.0\.0\.1:(\d+)/t/([0-9A-Za-z_-]+)/)$")


def _env(tmp_path: Path, fault: str | None = None) -> dict[str, str]:
    """This test's environment (the conftest's isolated `PAM_HOME`), with the stand-in exiftool
    first on PATH, unbuffered output, and `PAM_FAULT` exactly as asked."""
    bindir = tmp_path / "bin"
    if not bindir.is_dir():
        bindir.mkdir()
        (bindir / "exiftool.cmd").write_text(
            f'@"{PYTHON}" "{FAKE_EXIFTOOL}" %*\r\n', encoding="ascii"
        )
    env = dict(os.environ)
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("PAM_FAULT", None)
    if fault is not None:
        env["PAM_FAULT"] = fault
    return env


def _pam(env: dict[str, str], *args: str, stdin: str = "") -> subprocess.CompletedProcess:
    """A second console: `pam ARGS` in a fresh interpreter, waited for."""
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [PYTHON, "-c", SERVE, *args],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO_ROOT),
        env=env,
        timeout=300,
    )
    _note(f"$ pam {' '.join(args[:2])} -> exit {done.returncode}\n{done.stdout}{done.stderr}")
    return done


def _await(what: str, probe: Callable[[], Any], timeout: float = 120.0) -> Any:
    """`probe()` until it answers something truthy; at the deadline, FAIL naming `what`."""
    deadline = time.monotonic() + timeout
    while True:
        answer = probe()
        if answer:
            return answer
        if time.monotonic() > deadline:
            pytest.fail(f"{what}: not reached within {timeout} s")
        time.sleep(0.02)


class Server:
    """`pam ARGS` serving the page, in its own process group; read and driven over HTTP."""

    def __init__(self, env: dict[str, str], *args: str) -> None:
        self.args = args
        self.proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [PYTHON, "-c", SERVE, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            cwd=str(REPO_ROOT),
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        self.out: list[str] = []
        self.err: list[str] = []
        self.children: list[int] = []
        self._readers = [
            threading.Thread(target=self._read, args=(self.proc.stdout, self.out), daemon=True),
            threading.Thread(target=self._read, args=(self.proc.stderr, self.err), daemon=True),
        ]
        for reader in self._readers:
            reader.start()
        # A constructor that fails after `Popen` has no caller's `finally` to stop the server:
        # measured, the first draft of this module left one running, holding its folder's lock.
        try:
            found = _await(f"pam {args[0]}'s address", self._address, timeout=60.0)
            self.url, port, self.token = found.group(1), int(found.group(2)), found.group(3)
            self.port = port
            self.own = f"127.0.0.1:{port}"
            self.binding: str | None = None  # a GET with no binding header is served (R1-F1)
            self.binding = str(self.get("api/ping")[1]["binding"])
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _read(stream, lines: list[str]) -> None:
        for line in stream:
            lines.append(line.rstrip("\n"))

    def _address(self):
        if self.proc.poll() is not None and not any(URL.match(x) for x in self.out):
            pytest.fail(f"pam {self.args[0]} exited: {self.out} {self.err}")
        return next((m for m in map(URL.match, list(self.out)) if m), None)

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        """One request to `/t/<token>/<path>` with the headers the page sends (6b's report)."""
        sent = {"Origin": f"http://{self.own}", "X-Pam-Binding": self.binding}
        payload = None
        if method == "POST":
            sent["Content-Type"] = "application/json"
            payload = json.dumps(body if body is not None else {}).encode("utf-8")
        sent.update(headers or {})
        sent = {k: v for k, v in sent.items() if v is not None}
        conn = HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            conn.request(method, f"/t/{self.token}/{path}", body=payload, headers=sent)
            response = conn.getresponse()
            raw = response.read()
            kind = response.getheader("Content-Type") or ""
        finally:
            conn.close()
        if kind.startswith("application/json") and raw:
            return response.status, json.loads(raw.decode("utf-8"))
        return response.status, raw

    def get(self, path: str) -> tuple[int, Any]:
        return self.request("GET", path)

    def post(self, path: str, body: Any = None, **headers: str) -> tuple[int, Any]:
        return self.request("POST", path, body, headers)

    def job(self) -> dict[str, Any]:
        status, snapshot = self.get("api/job")
        assert status == 200, snapshot
        return snapshot

    def await_job(self, number: int, timeout: float = 180.0) -> dict[str, Any]:
        """The job's snapshot once it is no longer running; FAILS LOUDLY at the deadline."""
        last: dict[str, Any] = {}

        def ended() -> dict[str, Any] | None:
            last.clear()
            last.update(self.job())
            return dict(last) if last["state"] != "running" else None

        try:
            snapshot = _await(f"job {number} to end", ended, timeout)
        except pytest.fail.Exception:
            pytest.fail(f"job {number} still running at the deadline; last snapshot {last}")
        _note(f"job {number} ended: {snapshot}")
        return snapshot

    def _children(self) -> None:
        if self.proc.poll() is None:
            self.children = [p for p in _proctree.tree(self.proc.pid) if p != self.proc.pid]

    def gone(self) -> bool:
        """The server and every child it had when it was stopped are no longer RUNNING: absent
        from a Toolhelp snapshot, which lists live processes only. (`_proctree.gone` asks whether a
        PID can be opened, and an exited process stays openable while any handle to it is open -
        `Popen`'s own, for the root - so it cannot answer this.)"""
        live = {entry.pid for entry in _proctree.snapshot()}
        return self.proc.poll() is not None and not live & {self.proc.pid, *self.children}

    def interrupt(self) -> int:
        """Ctrl+Break, which the subprocess maps to Ctrl+C: the user stopping pam. Its exit code."""
        self._children()
        os.kill(self.proc.pid, signal.CTRL_BREAK_EVENT)
        code = self.proc.wait(timeout=60)
        self._let_go()
        for reader in self._readers:
            reader.join(timeout=30)
        _note(f"pam {self.args[0]} stopped (exit {code}):\n" + "\n".join(self.out + self.err))
        return code

    def crash(self) -> None:
        """`TerminateProcess` over the whole tree: no `finally` runs."""
        self._children()
        _proctree.kill_tree(self.proc.pid)
        self.proc.wait(timeout=60)
        self._let_go()
        for reader in self._readers:
            reader.join(timeout=30)

    def _let_go(self) -> None:
        """Close OUR handle to the exited server, as the console that started it does.

        `pam/lock.py::_win_pid_alive` calls a PID alive while it can be OPENED, and an exited
        process stays openable while any handle to it is open - `Popen` keeps one until it is
        collected. Without this the next `pam start` would find the dead server's lock "held"
        because of the test's own handle (finding S2-F3 records the production side)."""
        handle = getattr(self.proc, "_handle", None)
        if handle is not None and self.proc.returncode is not None:
            handle.Close()

    def close(self) -> None:
        """For `finally`: whatever state it is in, the tree is gone after this."""
        self._children()
        if self.proc.poll() is None:
            _proctree.kill_tree(self.proc.pid)
            self.proc.wait(timeout=60)
        self._let_go()


def _start(env: dict[str, str], *args: str) -> Server:
    return Server(env, *args)


def _read(server: Server) -> int:
    """`Start reading` (POST job/read), awaited; the plan it published."""
    status, body = server.post("api/job/read")
    assert status == 200, body
    server.binding = str(body["binding"])  # reading a chosen folder binds it: a new number
    finished = server.await_job(body["job"])
    assert finished["state"] == "done", finished
    plan_id = server.get("api/ping")[1]["plan_id"]
    assert isinstance(plan_id, int), plan_id
    return plan_id


def _decide(server: Server, plan_id: int, item_id: int) -> dict[str, Any]:
    status, body = server.post(
        f"api/plan/{plan_id}/decide", {"item_ids": [item_id], "action": "approve"}
    )
    assert status == 200, body
    return body


def _approve_selection(
    server: Server, plan_id: int, selection: dict[str, Any], low: int
) -> dict[str, Any]:
    body: dict[str, Any] = {"selection": selection, "action": "approve"}
    if low:
        body.update(include_low=True, expected_low=low)
    status, answer = server.post(f"api/plan/{plan_id}/decide-selection", body)
    assert status == 200, answer
    return answer


def _confirm(server: Server, plan_id: int) -> dict[str, Any]:
    status, facts = server.get(f"api/plan/{plan_id}/confirmation")
    assert status == 200, facts
    _note(
        f"confirmation for plan {plan_id}: { {k: v for k, v in facts.items() if k != 'nonce'} }"
    )
    return facts


def _apply(server: Server, plan_id: int, nonce: str) -> int:
    status, body = server.post(
        f"api/plan/{plan_id}/apply", {"nonce": nonce, "folder_confirmed": True}
    )
    assert status == 200, body
    return int(body["job"])


def _undo(server: Server, session: str) -> dict[str, Any]:
    status, facts = server.get(f"api/session/{session}")
    assert status == 200, facts
    assert facts["undoable"] is True, facts
    status, body = server.post(f"api/session/{session}/undo", {"nonce": facts["nonce"]})
    assert status == 200, body
    finished = server.await_job(body["job"])
    assert finished["state"] == "done", finished
    return finished


# --- (a)-(f): the page renames exactly what it approved, and nothing a stray request asks ------


def test_stage2_a_to_f_the_page_renames_what_it_approved_and_puts_it_back(tmp_path):
    root = _build(tmp_path / "photos")
    manifest = tmp_path / "before.json"
    _snapshot(root, manifest, FILES_ON_DISK)
    env = _env(tmp_path)
    server = _start(env, "start", str(root))
    try:
        # --- (b) read, then the fixture's shape with this module's own SQL -------------------
        plan_id = _read(server)
        items = _items(root, plan_id)
        assert sorted(items) == sorted(TWELVE)
        units: dict[str, set[str]] = {}
        for name, row in items.items():
            units.setdefault(row["unit_key"], set()).add(name)
        assert sorted(sorted(u) for u in units.values()) == sorted(
            [sorted(LIVE_1), sorted(LIVE_2), sorted(LIVE_3), sorted(RAW_PAIR)]
            + [[s] for s in SINGLES]
            + [[LOW_SINGLE]]
        )  # 8 units: 3 Live Photos, 1 RAW+JPEG, 4 singles
        assert {n for n, r in items.items() if r["tier"] == "low"} == {LOW_SINGLE}
        assert {r["status"] for r in items.values()} == {"proposed"}

        # Two units and a single through `decide`, each NAMING ONE id: the server widens.
        _decide(server, plan_id, items["IMG_1.MOV"]["id"])
        _decide(server, plan_id, items["DSC_1.JPG"]["id"])
        _decide(server, plan_id, items["A.JPG"]["id"])
        approved = {n for n, r in _items(root, plan_id).items() if r["status"] == "approved"}
        assert approved == {"IMG_1.HEIC", "IMG_1.MOV", "DSC_1.JPG", "DSC_1.NEF", "A.JPG"}
        # The low single, through a selection that acknowledges exactly one low name.
        answer = _approve_selection(
            server, plan_id, {"view": {}, "ids": [items[LOW_SINGLE]["id"]]}, low=1
        )
        assert answer["low_included"] == 1, answer
        approved = {n for n, r in _items(root, plan_id).items() if r["status"] == "approved"}
        assert approved == set(SIX_RENAMES)
        _verify_exact(root, manifest, FILES_ON_DISK)  # deciding renamed nothing

        facts = _confirm(server, plan_id)
        assert facts["archive_root"] == str(root.resolve())
        assert facts["renames"] == 6
        assert facts["not_approved"] == 6
        assert facts["skipped_by_you"] == 0
        assert facts["already_conforming"] == 0
        assert facts["low_confidence"] == 1
        assert facts["sync_provider"] is None
        nonce = facts["nonce"]

        # (d, apply route) a foreign Origin carrying the VALID nonce: the empty 404, nothing
        # journalled, and the nonce is not spent - the real POST below still uses it.
        journal_before = _journal_rows(root)
        status, raw = server.post(
            f"api/plan/{plan_id}/apply",
            {"nonce": nonce, "folder_confirmed": True},
            Origin="http://example.invalid",
        )
        assert (status, raw) == (404, b"")
        # (d) a POST without the nonce: refused with U14's sentence.
        status, body = server.post(f"api/plan/{plan_id}/apply", {"folder_confirmed": True})
        assert (status, body) == (409, {"error": CHANGED}), body
        assert _journal_rows(root) == journal_before == 0
        _verify_exact(root, manifest, FILES_ON_DISK)

        job = _apply(server, plan_id, nonce)
        finished = server.await_job(job)
        assert finished["kind"] == "rename" and finished["state"] == "done", finished
        assert finished["lines"] == [], finished
        session = finished["session"]
        assert isinstance(session, str), finished
        assert finished["result"]["renamed"] == 6, finished
        assert finished["result"]["failed"] == 0, finished
        _changed_lists(root, manifest, SIX_RENAMES, FILES_ON_DISK)
        assert _sessions(root) == [session]
        journal_after = _journal_rows(root)
        assert _plan_status(root, plan_id) == "partially_applied"

        # --- (c) the same POST again: refused, nothing journalled, `changed` still 6 ---------
        status, body = server.post(
            f"api/plan/{plan_id}/apply", {"nonce": nonce, "folder_confirmed": True}
        )
        assert (status, body) == (409, {"error": CHANGED}), body
        assert server.job()["state"] == "done"  # no new job started
        assert _journal_rows(root) == journal_after
        _changed_lists(root, manifest, SIX_RENAMES, FILES_ON_DISK)

        # --- (d, undo route) a valid undo nonce under a foreign Origin, then none at all ------
        status, about = server.get(f"api/session/{session}")
        assert status == 200, about
        assert (about["done"], about["undone"], about["undoable"]) == (6, 0, True), about
        undo_nonce = about["nonce"]
        status, raw = server.post(
            f"api/session/{session}/undo",
            {"nonce": undo_nonce},
            Origin="http://example.invalid",
        )
        assert (status, raw) == (404, b"")
        status, body = server.post(f"api/session/{session}/undo", {})
        assert (status, body) == (409, {"error": CHANGED}), body
        assert _journal_rows(root) == journal_after
        _changed_lists(root, manifest, SIX_RENAMES, FILES_ON_DISK)

        # --- (e) a second console's `pam apply --all` while the server holds the lock ---------
        refused = _pam(
            env, "apply", str(plan_id), "--all", "--archive", str(root), stdin="apply\n"
        )
        assert refused.returncode == 1, refused.stdout + refused.stderr
        assert LOCKED in refused.stderr, refused.stderr
        assert "renamed" not in refused.stdout, refused.stdout
        assert _journal_rows(root) == journal_after
        _changed_lists(root, manifest, SIX_RENAMES, FILES_ON_DISK)

        # --- (f) undo from `session/{id}` with the nonce (d) did not spend ------------------
        status, body = server.post(f"api/session/{session}/undo", {"nonce": undo_nonce})
        assert status == 200, body
        undone = server.await_job(body["job"])
        assert undone["kind"] == "undo" and undone["state"] == "done", undone
        assert undone["result"]["restored"] == 6, undone
        assert undone["result"]["failed"] == 0, undone
        _verify_exact(root, manifest, FILES_ON_DISK)
        assert _plan_status(root, plan_id) == "undone"
    finally:
        server.close()
    _await("the server's process tree to be gone", server.gone, timeout=30.0)


# --- (g) a rename job that dies in the middle of a companion unit -------------------------------

# Every item approved (the low one acknowledged), applied in the planner's order: the four
# singles first, then the RAW+JPEG unit, whose first member is renamed when `mid_companion_unit`
# fires on its first hit. Spelled from the fixture and spec 6.1.
G_KEPT = {
    "A.JPG": "2020-05-06_001.JPG",
    "B.JPG": "2021-06-07_001.JPG",
    "C.JPG": "2022-07-08_001.JPG",
    "D.JPG": "2015-06-07_001.JPG",
}
G_RECOVERY = (
    "pam finished tidying up after an interrupted run: 1 file(s) put back,"
    " 1 rename(s) not started."
)


def test_stage2_g_a_page_rename_that_dies_mid_unit_is_recovered_by_the_next_request(tmp_path):
    root = _build(tmp_path / "photos")
    manifest = tmp_path / "before.json"
    _snapshot(root, manifest, FILES_ON_DISK)
    server = _start(_env(tmp_path, fault="crash:mid_companion_unit:1"), "start", str(root))
    try:
        plan_id = _read(server)
        answer = _approve_selection(server, plan_id, {"view": {}, "all": True}, low=1)
        assert answer["low_included"] == 1, answer
        facts = _confirm(server, plan_id)
        assert (facts["renames"], facts["not_approved"], facts["low_confidence"]) == (12, 0, 1)
        died = server.await_job(_apply(server, plan_id, facts["nonce"]))
        # The job died (the fault's SystemExit in the job thread), after 4 of 12.
        assert (died["kind"], died["state"], died["done"], died["total"]) == (
            "rename",
            "failed",
            4,
            12,
        ), died
        # The next request joined it and recovered (`settle` runs before any route): U12's line.
        healed = server.job()
        assert healed["recovery"] == G_RECOVERY, healed
        _note(f"(g) the request after the death: {healed}")
        assert _plan_status(root, plan_id) == "partially_applied"  # J10: terminal, not applying
        (session,) = _sessions(root)
        states = {
            r["old_rel_path"]: r["state"]
            for r in _query(root, "SELECT old_rel_path, state FROM journal")
        }
        assert states == {
            "A.JPG": "done",
            "B.JPG": "done",
            "C.JPG": "done",
            "D.JPG": "done",
            "DSC_1.JPG": "reverted",  # renamed, then put back: the unit is never split
            "DSC_1.NEF": "failed",  # never started
        }
        _changed_lists(root, manifest, G_KEPT, FILES_ON_DISK)
        assert server.interrupt() == 0
    finally:
        server.close()

    env = _env(tmp_path)
    verified = _pam(env, "verify", "--archive", str(root))
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert VERIFY_CLEAN in verified.stdout, verified.stdout

    # Undo the four from the page, through a fresh `pam start`.
    server = _start(env, "start", str(root))
    try:
        undone = _undo(server, session)
        assert undone["result"]["restored"] == 4, undone
        assert undone["result"]["failed"] == 0, undone
        _verify_exact(root, manifest, FILES_ON_DISK)
        assert server.interrupt() == 0
    finally:
        server.close()
    _await("the server's process tree to be gone", server.gone, timeout=30.0)


# --- (h) the server process killed in the middle of a 400-file page rename ----------------------

MANY = 400
MANY_ON_DISK = 800  # each with its stand-in exiftool sidecar
RECOVERY_LINE = re.compile(r"^pam finished tidying up after an interrupted run: .+\.$")


def _many_ready(tmp_path: Path) -> tuple[Path, Path]:
    root = _build_many(tmp_path / "photos", MANY)
    manifest = tmp_path / "before.json"
    _snapshot(root, manifest, MANY_ON_DISK)
    return root, manifest


def _approve_everything(server: Server, plan_id: int) -> str:
    """Every name approved through one selection (none is low); the confirmation's nonce."""
    answer = _approve_selection(server, plan_id, {"view": {}, "all": True}, low=0)
    assert answer["low_included"] == 0, answer
    facts = _confirm(server, plan_id)
    assert (facts["renames"], facts["not_approved"], facts["low_confidence"]) == (MANY, 0, 0)
    return str(facts["nonce"])


def _mid_rename(server: Server) -> Callable[[], dict[str, Any] | None]:
    def probe() -> dict[str, Any] | None:
        job = server.job()
        assert job["state"] == "running", f"the rename ended before it could be caught: {job}"
        return job if 0 < job["done"] < job["total"] else None

    return probe


def test_stage2_h_the_server_killed_mid_rename_is_put_right_by_the_next_start(tmp_path):
    root, manifest = _many_ready(tmp_path)
    env = _env(tmp_path)
    server = _start(env, "start", str(root))
    try:
        plan_id = _read(server)
        nonce = _approve_everything(server, plan_id)
        _apply(server, plan_id, nonce)
        caught = _await("0 < done < total", _mid_rename(server), timeout=120.0)
        server.crash()  # TerminateProcess over the tree: no `finally`, no closing block
        _note(f"(h) killed at {caught}")
        assert caught["kind"] == "rename" and caught["total"] == MANY, caught
    finally:
        server.close()
    _await("the killed server's process tree to be gone", server.gone, timeout=30.0)
    assert not any(RECOVERY_LINE.match(line) for line in server.err), server.err
    assert _plan_status(root, plan_id) == "applying"  # left mid-run: recovery's to finish
    # The killed server's lock is left behind, naming its PID. Measured on this machine: that
    # PID stays OPENABLE after the process has exited (no live process has it; a handle is held
    # somewhere). Before the controller's S2-F3 fix `pam/lock.py::_win_pid_alive` called an
    # openable PID alive and the next `pam start` refused "Another pam is working on this
    # folder", so this test deleted the lock by hand. It no longer does: the restart below must
    # find the holder dead (its exit code is no longer STILL_ACTIVE) and take the lock itself.
    lock = workspace_if_exists(root).lock_path
    assert lock.read_text(encoding="ascii").strip() == str(server.proc.pid)
    assert server.proc.pid not in {entry.pid for entry in _proctree.snapshot()}

    server = _start(env, "start", str(root))
    try:
        # Recovery ran at the bind, before the address was printed: its line is on STDERR.
        shown = [line for line in server.err if RECOVERY_LINE.match(line)]
        assert len(shown) == 1, server.err
        _note(f"(h) restart stdout {server.out}\n(h) restart stderr {server.err}")
        ping, job = server.get("api/ping")[1], server.job()
        _note(f"(h) restart ping {ping}\n(h) restart job {job}")
        assert job["state"] == "idle", job
        status = _plan_status(root, plan_id)
        assert status == "partially_applied", status  # J10: the next pam finished it
        assert ping["plan_id"] is None, ping  # no open plan: the page is Welcome on the folder
        (session,) = _sessions(root)
        assert server.interrupt() == 0
    finally:
        server.close()

    verified = _pam(env, "verify", "--archive", str(root))
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert VERIFY_CLEAN in verified.stdout, verified.stdout

    server = _start(env, "start", str(root))
    try:
        undone = _undo(server, session)
        assert undone["result"]["failed"] == 0, undone
        assert 0 < undone["result"]["restored"] < MANY, undone
        _verify_exact(root, manifest, MANY_ON_DISK)
        assert server.interrupt() == 0
    finally:
        server.close()
    _await("the server's process tree to be gone", server.gone, timeout=30.0)


# --- (i) (rev 1.2, X2): an OLDER session undone from History, after a newer one was put back -----

# Plan A renames the RAW+JPEG unit (two files), so the undo THROUGH HISTORY must put back a whole
# unit, every member (Q3); plan B, read after it, renames three singles. Spelled from the fixture
# and spec 6.1, every extension keeping its case.
A_RENAMES = {"DSC_1.JPG": "2019-04-05_001.JPG", "DSC_1.NEF": "2019-04-05_001.NEF"}
B_RENAMES = {
    "A.JPG": "2020-05-06_001.JPG",
    "B.JPG": "2021-06-07_001.JPG",
    "C.JPG": "2022-07-08_001.JPG",
}


def _id_clock(session: str) -> str:
    """This module's own reading of a session id's clock: `s-20261114-143205` -> its ISO form."""
    found = re.fullmatch(r"s-(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)(\d\d)(?:-\d+)?", session)
    assert found is not None, session
    y, mo, d, h, mi, s = found.groups()
    return f"{y}-{mo}-{d}T{h}:{mi}:{s}"


def _history(server: Server) -> list[dict[str, Any]]:
    status, body = server.get("api/sessions")
    assert status == 200, body
    return body["sessions"]


def _status_line(env: dict[str, str], root: Path) -> list[str]:
    """`pam status` (lock-free, so it runs while the server holds the lock): its session line."""
    done = _pam(env, "status", "--archive", str(root))
    assert done.returncode == 0, done.stdout + done.stderr
    return [
        line for line in done.stdout.splitlines() if line.startswith("  Most recent session")
    ]


def test_stage2_i_an_older_session_is_undone_through_its_history_row(tmp_path):
    """The plan's step (i), five sub-steps: plan A (2 files) then plan B (3 other files); B undone
    from `session/{id}` as Done does; History's row for A still says it can be undone, and A is
    undone THROUGH THAT ROW'S session id; `--exact` PASS; and `pam status` names B's undo session,
    not A - F-A6's shape, the reason the page needs its own route.

    ONE ORDERING NOTE, recorded rather than improvised: the plan lists `pam status` after A's undo,
    when the most recent session is A's UNDO, not B's. F-A6's shape is the state between the two
    undos, so the status line is read THERE (B's undo session named, A's id nowhere, nothing left
    to undo) and again at the end (A's undo session named)."""
    root = _build(tmp_path / "photos")
    manifest = tmp_path / "before.json"
    _snapshot(root, manifest, FILES_ON_DISK)
    env = _env(tmp_path)
    server = _start(env, "start", str(root))
    try:
        # --- 1. plan A: the RAW+JPEG unit, approved by naming ONE member (the server widens) ---
        plan_a = _read(server)
        items = _items(root, plan_a)
        _decide(server, plan_a, items["DSC_1.NEF"]["id"])
        approved = {n for n, r in _items(root, plan_a).items() if r["status"] == "approved"}
        assert approved == {"DSC_1.JPG", "DSC_1.NEF"}
        facts = _confirm(server, plan_a)
        assert facts["renames"] == 2
        finished = server.await_job(_apply(server, plan_a, facts["nonce"]))
        assert finished["kind"] == "rename" and finished["state"] == "done", finished
        assert finished["result"]["renamed"] == 2, finished
        assert finished["result"]["failed"] == 0, finished
        session_a = finished["session"]
        _changed_lists(root, manifest, A_RENAMES, FILES_ON_DISK)

        # --- 1. plan B, read after A: three singles, each approved by naming its id ----------
        plan_b = _read(server)
        assert plan_b != plan_a
        items = _items(root, plan_b)
        _decide(server, plan_b, items["A.JPG"]["id"])
        _decide(server, plan_b, items["B.JPG"]["id"])
        _decide(server, plan_b, items["C.JPG"]["id"])
        approved = {n for n, r in _items(root, plan_b).items() if r["status"] == "approved"}
        assert approved == {"A.JPG", "B.JPG", "C.JPG"}
        facts = _confirm(server, plan_b)
        assert facts["renames"] == 3
        finished = server.await_job(_apply(server, plan_b, facts["nonce"]))
        assert finished["kind"] == "rename" and finished["state"] == "done", finished
        assert finished["result"]["renamed"] == 3, finished
        assert finished["result"]["failed"] == 0, finished
        session_b = finished["session"]
        assert session_b != session_a
        _changed_lists(root, manifest, {**A_RENAMES, **B_RENAMES}, FILES_ON_DISK)

        # --- 2. undo B from `session/{id}`, as Done does --------------------------------------
        undone = _undo(server, session_b)
        assert undone["result"]["restored"] == 3, undone
        assert undone["result"]["failed"] == 0, undone
        # Only A's unit remains renamed.
        _changed_lists(root, manifest, A_RENAMES, FILES_ON_DISK)
        sessions = _sessions(root)
        assert sessions[:2] == [session_a, session_b] and len(sessions) == 3, sessions
        undo_b = sessions[2]

        # --- 5, read HERE: F-A6's shape - status names B's undo, and A nowhere ----------------
        assert _status_line(env, root) == [
            f"  Most recent session {undo_b}: 3 put back, an undo cannot itself be undone"
            " - there is nothing here left to undo."
        ]

        # --- 3. History: A's row can still be undone; B's is undone; the undo is not a row ----
        rows = _history(server)
        assert [
            (r["session_id"], r["renamed"], r["undone"], r["undoable"], r["report"])
            for r in rows
        ] == [(session_b, 3, True, False, True), (session_a, 2, False, True, True)]
        assert [r["time"] for r in rows] == [_id_clock(session_b), _id_clock(session_a)]
        history_id = rows[1]["session_id"]

        # ... and A is undone THROUGH THAT ROW's id, by Done's routes (nonce, then POST).
        undone = _undo(server, history_id)
        assert undone["result"]["restored"] == 2, undone
        assert undone["result"]["failed"] == 0, undone

        # --- 4. every file back where it was, byte for byte ----------------------------------
        _verify_exact(root, manifest, FILES_ON_DISK)
        rows = _history(server)
        assert [(r["session_id"], r["undone"], r["undoable"]) for r in rows] == [
            (session_b, True, False),
            (session_a, True, False),
        ]
        sessions = _sessions(root)
        assert sessions[:3] == [session_a, session_b, undo_b] and len(sessions) == 4, sessions
        undo_a = sessions[3]

        # --- 5, at the end: the most recent session is now A's undo ---------------------------
        assert _status_line(env, root) == [
            f"  Most recent session {undo_a}: 2 put back, an undo cannot itself be undone"
            " - there is nothing here left to undo."
        ]
    finally:
        server.close()
    _await("the server's process tree to be gone", server.gone, timeout=30.0)


# --- beyond (a)-(h): the other trigger, a reload mid-rename, and /list's cost -----------------


def test_stage2_pam_review_closing_block_after_a_page_rename_of_one_file(tmp_path):
    """B1: `pam review` serves the same rename routes. One page apply of ONE file, then the user
    stops pam: the closing block must tell the truth about the rename that happened."""
    root = _build(tmp_path / "photos")
    manifest = tmp_path / "before.json"
    _snapshot(root, manifest, FILES_ON_DISK)
    env = _env(tmp_path)
    assert _pam(env, "scan", str(root)).returncode == 0
    assert _pam(env, "plan", "--archive", str(root)).returncode == 0
    (plan_row,) = _query(root, "SELECT id FROM plans WHERE status = 'open'")
    plan_id = int(plan_row[0])
    server = _start(env, "review", str(plan_id), "--archive", str(root))
    try:
        _decide(server, plan_id, _items(root, plan_id)["B.JPG"]["id"])
        facts = _confirm(server, plan_id)
        assert (facts["renames"], facts["not_approved"]) == (1, 11), facts
        finished = server.await_job(_apply(server, plan_id, facts["nonce"]))
        assert finished["state"] == "done" and finished["result"]["renamed"] == 1, finished
        session = finished["session"]
        _changed_lists(root, manifest, {"B.JPG": "2021-06-07_001.JPG"}, FILES_ON_DISK)
        assert server.interrupt() == 0
    finally:
        server.close()
    closing = server.out[server.out.index("Press Ctrl+C here when you are done.") + 1 :]
    _note(f"B1 closing block: {closing}")
    assert closing == [
        f"Plan {plan_id} is already applied in part.",
        f"Next: pam report session {session}   (see what was renamed)",
        f"  or: pam undo {session}   (put those files back under their old names)",
    ], closing


def test_stage2_a_reload_mid_rename_finds_it_running_and_list_latency_is_recorded(tmp_path):
    """The server half of S1P-F2: during a 400-file page rename, a reload (`GET /`, then the
    boot's `GET api/job`) is served and the job still says `running` with its progress. `/list`
    is timed before any confirmation, during the rename and after it - RECORDED, not asserted."""
    root, _manifest_path = _many_ready(tmp_path)
    server = _start(_env(tmp_path), "start", str(root))
    timings: dict[str, list[float]] = {"before": [], "during": [], "after": []}

    def timed_list(phase: str) -> None:
        began = time.perf_counter()
        status, body = server.get(f"api/plan/{plan_id}/list")
        timings[phase].append(time.perf_counter() - began)
        assert status == 200, body

    try:
        plan_id = _read(server)
        for _ in range(3):
            timed_list("before")
        nonce = _approve_everything(server, plan_id)
        job = _apply(server, plan_id, nonce)
        caught = _await("0 < done < total", _mid_rename(server), timeout=120.0)
        status, page = server.get("")
        assert status == 200 and b"<html" in page.lower(), status
        reloaded = server.job()
        _note(f"reload: caught {caught}; after GET / the job says {reloaded}")
        assert reloaded["kind"] == "rename" and reloaded["total"] == MANY, reloaded
        assert reloaded["state"] in ("running", "done"), reloaded
        assert reloaded["done"] >= caught["done"], reloaded
        while len(timings["during"]) < 3 and server.job()["state"] == "running":
            timed_list("during")
        finished = server.await_job(job)
        assert finished["state"] == "done" and finished["result"]["renamed"] == MANY, finished
        for _ in range(3):
            timed_list("after")
        assert server.interrupt() == 0
    finally:
        server.close()
    medians = {
        k: round(statistics.median(v) * 1000, 1) if v else None for k, v in timings.items()
    }
    _note(f"/list ms: {timings} medians {medians}")


# --- the findings, each a strict xfail until the controller fixes production ------------------


def test_s2_f1_a_died_rename_still_names_the_session_that_renamed_files(tmp_path):
    root = _build(tmp_path / "photos")
    server = _start(_env(tmp_path, fault="crash:mid_companion_unit:1"), "start", str(root))
    try:
        plan_id = _read(server)
        _approve_selection(server, plan_id, {"view": {}, "all": True}, low=1)
        facts = _confirm(server, plan_id)
        died = server.await_job(_apply(server, plan_id, facts["nonce"]))
        assert died["state"] == "failed", died
        (session,) = _sessions(root)  # it renamed the four singles (step (g))
        assert server.job()["session"] == session
    finally:
        server.close()


def test_s2_f2_recovery_at_a_start_reaches_the_page(tmp_path):
    root = _build(tmp_path / "photos")
    env = _env(tmp_path)
    assert _pam(env, "scan", str(root)).returncode == 0
    assert _pam(env, "plan", "--archive", str(root)).returncode == 0
    (plan_row,) = _query(root, "SELECT id FROM plans WHERE status = 'open'")
    crashed = _pam(
        {**env, "PAM_FAULT": "crash:mid_companion_unit:1"},
        "apply",
        str(plan_row[0]),
        "--all",
        "--archive",
        str(root),
        stdin="apply\n",
    )
    assert "PAM_FAULT: crashed at mid_companion_unit" in crashed.stdout + crashed.stderr
    server = _start(env, "start", str(root))
    try:
        (line,) = [x for x in server.err if RECOVERY_LINE.match(x)]
        boot = (server.get("api/ping")[1].get("recovery"), server.job()["recovery"])
        assert line in boot, (line, boot)
    finally:
        server.close()


def test_s2_f3_an_exited_process_is_not_a_live_lock_holder():
    from pam.lock import _pid_alive

    proc = subprocess.Popen([PYTHON, "-c", "pass"])  # noqa: S603 - fixed argv, no shell
    proc.wait(timeout=60)
    try:
        assert proc.returncode == 0
        assert proc.pid not in {entry.pid for entry in _proctree.snapshot()}  # not running
        assert not _pid_alive(proc.pid)  # yet `Popen`'s open handle keeps it openable
    finally:
        proc._handle.Close()  # type: ignore[attr-defined]


if __name__ == "__main__":
    os.environ["PAM_STAGE2_PRINT"] = "1"
    sys.exit(pytest.main([__file__, "-q", "-s", "-p", "no:cacheprovider"]))
