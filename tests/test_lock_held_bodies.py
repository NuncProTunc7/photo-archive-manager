"""M14 Task 5, Step 1: the scan and plan BODIES, callable under a lock the caller proves it holds.

`pam start`'s server takes the workspace lock at bind and keeps it; Task 5's job thread must then
scan and plan WITHOUT taking it again. So the bodies became `scan_locked` / `plan_locked`, whose
first argument is a `HeldWorkspace` - a proof only `WorkspaceLock.__enter__` can make - and
`run_scan` / `run_plan` became thin shells: resolve, lock, open their own log, call the body.

THE GOLDEN TRANSCRIPT (T5.9) IS TYPED BY HAND. It was captured ONCE by running `pam scan` and
then `pam plan` over the tiny library at `2d38060` - the commit before Task 5 touched either
command - in a scratch worktree, and pasted below. Nothing in this file regenerates it, and it is
never read back from the code under test: a transcript produced by the code it checks is equal
to that code by construction. Normalised, and only these: the archive's temp path (`<ROOT>`) and
each `Log:` line's workspace path and timestamp (`<WORKSPACE>`, `<STAMP>`), the three things that
differ on every run. No timing or session id is printed by either command.

The tiny library needs REAL exiftool (`tests/test_tiny_library.py` says why), so those tests
skip without it, as that module's do.
"""

import logging
import os
import re
import shutil

import pytest
from archives import FAKE_COMMAND, approve_ids, catalog, materialise_tiny_library, planned, put
from click.testing import CliRunner

from pam.catalog.db import connect
from pam.cli import cli
from pam.config import load_config
from pam.errors import WorkspaceLockedError
from pam.execute import unit as unit_mod
from pam.execute.apply import ApplySummary, _Clock, apply_locked, run_apply
from pam.execute.undo import UndoSummary, run_undo, undo_locked
from pam.lock import HeldWorkspace, WorkspaceLock
from pam.naming.planner import plan_locked, run_plan
from pam.naming.template import parse_template
from pam.paths import workspace_for, workspace_if_exists
from pam.scan import exiftool, walker
from pam.scan.runner import run_scan, scan_locked
from pam.webui import launcher
from pam.webui.launcher import start_server

needs_exiftool = pytest.mark.skipif(
    shutil.which("exiftool") is None, reason="the tiny library is pinned against REAL exiftool"
)

# The tiny library's own numbers, spelled (tests/test_tiny_library.py: READ = 100, the images and
# videos pam opens). Written here, not imported: a stop test that borrowed the count it checks
# would follow it anywhere.
TINY_LIBRARY_READ = 100
STOP_AFTER = 85  # past the 80 images, so the stop lands inside the videos' batch
LEFT_AFTER_THE_STOP = 15  # 100 - 85, by hand

# `pam scan <ROOT>` then `pam plan --archive <ROOT>`, captured at 2d38060 and pasted (above).
GOLDEN_SCAN = """\
Scanning <ROOT> ...
Reading  100 / 100 files  (100%)
Scan complete.
  106 files found  (80 images, 20 videos, 2 sidecars, 4 other)
  106 new - 0 unchanged - 0 changed - 0 missing
  100 files read this scan - everything else was already known.

pam scan is safe to re-run anytime - only new or changed files are re-read.
Next: pam plan   (propose new names - renames nothing)
  or: pam enrich   (add AI understanding first)
Log: <WORKSPACE>\\logs\\<STAMP>_scan.log
"""
GOLDEN_PLAN = """\
Plan 1 is ready - nothing has been renamed yet.
  102 files would be renamed, out of 102 pam can name.
  Grouped into 47 events by date and place.
  Confidence: 44 high, 43 medium, 15 low.
  ! 2 other files share a name with something being renamed and will NOT be renamed with it\
 - listed in the log.
  For example:
    2005-06-07_holiday.jpg  ->  2005-06-07_001.jpg
    20060708_trip.JPG  ->  2006-07-08_001.JPG
    2008.09.10_scan.tif  ->  2008-09-10_001.tif

Next: pam review 1   (see & approve the plan)
  or: pam apply 1   (renames only what you approve)
Log: <WORKSPACE>\\logs\\<STAMP>_plan.log
"""

_LOG_LINE = re.compile(
    r"^Log: (?P<ws>.+)[\\/]logs[\\/]\d{4}-\d{2}-\d{2}_\d{6}_(?P<kind>scan|plan)(_\d+)?\.log$",
    re.MULTILINE,
)


def _normalise(output: str, root) -> str:
    """Only what varies run to run: the archive path and the `Log:` line's path and stamp."""
    home = os.environ["PAM_HOME"]

    def log_line(match: re.Match[str]) -> str:
        assert match.group("ws").startswith(home), match.group(0)  # a workspace under PAM_HOME
        return f"Log: <WORKSPACE>\\logs\\<STAMP>_{match.group('kind')}.log"

    return _LOG_LINE.sub(log_line, output.replace(str(root), "<ROOT>"))


def _logger() -> logging.Logger:
    logger = logging.getLogger("pam.tests.lock_held_bodies")
    logger.propagate = False
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    return logger


def _library(tmp_path, name="a"):
    return materialise_tiny_library(tmp_path / name).resolve()


def _read_files(root) -> int:
    """Files READ so far, counted from a separate connection: a hash AND a capture row."""
    conn = catalog(root)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM files f JOIN capture c ON c.file_id = f.id"
            " WHERE f.sha256 IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()


# --- T5.9: the golden transcript --------------------------------------------------------------


@needs_exiftool
def test_scan_then_plan_print_the_transcript_recorded_before_the_bodies_moved(tmp_path):
    root = _library(tmp_path)
    scan = CliRunner().invoke(cli, ["scan", str(root)])
    plan = CliRunner().invoke(cli, ["plan", "--archive", str(root)])
    assert (scan.exit_code, plan.exit_code) == (0, 0)
    assert _normalise(scan.output, root) == GOLDEN_SCAN
    assert _normalise(plan.output, root) == GOLDEN_PLAN


# --- T5.8: the proof ----------------------------------------------------------------------------


def test_a_held_workspace_cannot_be_forged(tmp_path):
    """Only `WorkspaceLock.__enter__` makes one; anything else passing any issuer is refused."""
    ws = workspace_for(tmp_path.resolve())
    lock = WorkspaceLock(ws.lock_path, ws)
    for issuer in (None, object(), "enter", lock):
        with pytest.raises(TypeError, match="only by WorkspaceLock.__enter__"):
            HeldWorkspace(issuer, lock, ws)
    # The positive control: the lock's own `__enter__` makes one, current until `__exit__`.
    with WorkspaceLock(ws.lock_path, ws) as held:
        assert isinstance(held, HeldWorkspace)
        assert held.workspace == ws
        assert held.held is True
        assert held.require(ws) is ws
    assert held.held is False


def test_a_lock_built_without_a_workspace_gives_a_proof_for_nothing(tmp_path):
    """Every `WorkspaceLock(path)` written before M14 keeps working; its proof names None."""
    ws = workspace_for(tmp_path.resolve())
    with WorkspaceLock(ws.lock_path) as held:
        assert held.workspace is None
        with pytest.raises(AssertionError):
            held.require(ws)


def test_the_bodies_refuse_a_proof_for_another_folder_and_touch_nothing(tmp_path):
    a, _ = planned(_mkdir(tmp_path / "a"), ["IMG_1.JPG"])
    b, _ = planned(_mkdir(tmp_path / "b"), ["IMG_1.JPG"])
    fresh = _mkdir(tmp_path / "fresh")
    ws_a, ws_b = workspace_if_exists(a), workspace_if_exists(b)
    assert ws_a is not None and ws_b is not None
    config = load_config(ws_b.config_path)
    order = parse_template(config.template)
    with WorkspaceLock(ws_a.lock_path, ws_a) as held:
        with pytest.raises(AssertionError):
            scan_locked(held, fresh.resolve(), config, FAKE_COMMAND, _logger())
        with pytest.raises(AssertionError):
            plan_locked(held, b.resolve(), config, config.template, order, _logger())
    assert not workspace_for(fresh.resolve()).db_path.exists()  # refused before connect
    conn = catalog(b)
    try:
        assert conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0] == 1  # no second plan
    finally:
        conn.close()
    # And a proof whose lock was released is refused even for its own folder.
    with pytest.raises(AssertionError, match="released"):
        scan_locked(held, a.resolve(), config, FAKE_COMMAND, _logger())


def _mkdir(path):
    path.mkdir()
    return path


# --- C5-2: should_stop and progress ------------------------------------------------------------


@needs_exiftool
def test_a_stopped_read_leaves_no_half_recorded_file(tmp_path):
    """T5.3. `should_stop` is asked after each file's commit and before the next hash: at its
    k-th call exactly k files are read, as a SEPARATE connection sees them. Stopped after 85, the
    scan says so and has read 85; the next scan reads exactly the other 15."""
    root = _library(tmp_path)
    ws = workspace_for(root)
    events: list[tuple[str, int]] = []

    def progress(done: int, _total: int) -> None:
        events.append(("progress", done))

    def should_stop() -> bool:
        events.append(("stop", _read_files(root)))
        return sum(kind == "stop" for kind, _ in events) == STOP_AFTER

    with WorkspaceLock(ws.lock_path, ws) as held:
        summary = scan_locked(
            held,
            root,
            load_config(ws.config_path),
            exiftool.default_command(),
            _logger(),
            progress=progress,
            should_stop=should_stop,
        )

    expected: list[tuple[str, int]] = []
    for k in range(1, STOP_AFTER + 1):
        expected += [("progress", k), ("stop", k)]
    assert events == expected
    assert (summary.stopped, summary.read) == (True, STOP_AFTER)
    assert _read_files(root) == STOP_AFTER

    rest = run_scan(root)
    assert (rest.stopped, rest.read, rest.errors) == (False, LEFT_AFTER_THE_STOP, [])
    assert _read_files(root) == TINY_LIBRARY_READ


@needs_exiftool
def test_progress_gets_every_file_and_its_default_is_the_console_printer(tmp_path, capsys):
    root = _library(tmp_path, "a")
    ws = workspace_for(root)
    seen: list[tuple[int, int]] = []
    with WorkspaceLock(ws.lock_path, ws) as held:
        summary = scan_locked(
            held,
            root,
            load_config(ws.config_path),
            exiftool.default_command(),
            _logger(),
            progress=lambda done, total: seen.append((done, total)),
        )
    assert seen == [(k, 100) for k in range(1, 101)]
    assert summary.stopped is False
    assert capsys.readouterr().out == ""  # the console printer was replaced, not added to

    other = _library(tmp_path, "b")
    ws = workspace_for(other)
    with WorkspaceLock(ws.lock_path, ws) as held:
        scan_locked(
            held, other, load_config(ws.config_path), exiftool.default_command(), _logger()
        )
    assert capsys.readouterr().out == "Reading  100 / 100 files  (100%)\n"


@needs_exiftool
def test_a_file_freed_after_the_walk_still_ticks_progress_and_is_a_place_to_stop(
    tmp_path, monkeypatch
):
    """C5-2 holds in the became-cloud-only branch too (M14 Task 5 review R1, M3118/M3119): a file
    OneDrive frees between the walk and its batch is a file done - one progress tick, then the
    stop is asked. Every file here is freed, so no file reaches the main branch's checks."""
    root = _library(tmp_path)
    ws = workspace_for(root)
    monkeypatch.setattr(walker, "placeholder_now", lambda _path: True)
    seen: list[tuple[int, int]] = []
    asked: list[int] = []

    def should_stop() -> bool:
        asked.append(len(seen))
        return len(asked) == 3

    with WorkspaceLock(ws.lock_path, ws) as held:
        summary = scan_locked(
            held,
            root,
            load_config(ws.config_path),
            exiftool.default_command(),
            _logger(),
            progress=lambda done, total: seen.append((done, total)),
            should_stop=should_stop,
        )
    assert seen == [(1, 100), (2, 100), (3, 100)]
    assert asked == [1, 2, 3]
    assert summary.stopped is True


# --- the server keeps the proof beside its lock ------------------------------------------------


def test_a_bound_start_server_holds_the_proof_for_its_folder_and_none_after(
    tmp_path, monkeypatch
):
    a, _ = planned(_mkdir(tmp_path / "a"), ["IMG_1.JPG"])
    b, _ = planned(_mkdir(tmp_path / "b"), ["IMG_1.JPG"])
    server = start_server(_logger())
    try:
        assert server.held is None
        assert launcher.bind(server, a)["state"] == "bound"
        first = server.held
        assert first is not None and first.held
        assert first.workspace == workspace_if_exists(a)

        assert launcher.bind(server, b)["state"] == "bound"  # a change of folder
        assert first.held is False  # the old folder's proof died with its lock
        assert server.held is not None and server.held.workspace == workspace_if_exists(b)

        chosen = _mkdir(tmp_path / "never-scanned")
        assert launcher.bind(server, chosen)["state"] == "chosen"
        assert server.held is None

        assert launcher.bind(server, a)["state"] == "bound"

        def broken(*_args, **_kwargs):
            raise RuntimeError("recovery failed")

        monkeypatch.setattr(launcher, "run_recovery", broken)
        with pytest.raises(RuntimeError):
            launcher.bind(server, b)  # a failed bind
        assert (server.held, server.held_lock) == (None, None)
        # M3111 (R1): and the log the failed bind opened is closed again - the server is back on
        # the logger that writes nothing, and the `pam` logger holds no handler.
        assert (server.log_path, server.logger) == (None, _logger())
        assert logging.getLogger("pam").handlers == []
    finally:
        server.server_close()
    assert server.held is None


# --- Task 5 review R1, finding F1: a stopped read, then the console's plan -----------------------


LIVE_PHOTO_PAIR = ("IMG_1.JPG", "IMG_1.MOV")  # one stem, a display + a live video (spec 5.4)


def _stopped_first_read_of_the_pair(tmp_path, should_stop, logger=None):
    """A Live Photo pair read for the FIRST time by `scan_locked`, stopped by `should_stop`."""
    root = (tmp_path / "photos").resolve()
    root.mkdir(parents=True)
    for name in LIVE_PHOTO_PAIR:
        put(root, name)
    ws = workspace_for(root)
    with WorkspaceLock(ws.lock_path, ws) as held:
        stopped = scan_locked(
            held,
            root,
            load_config(ws.config_path),
            FAKE_COMMAND,
            logger if logger is not None else _logger(),
            progress=lambda done, total: None,
            should_stop=should_stop,
        )
    return root, stopped


def _group_ids(root) -> dict[str, int | None]:
    """Each file's `companion_group_id`, by the SUITE'S OWN SQL."""
    conn = catalog(root)
    try:
        return {
            r["filename"]: r["companion_group_id"]
            for r in conn.execute("SELECT filename, companion_group_id FROM files")
        }
    finally:
        conn.close()


def _units_of(root, plan_id) -> dict[str, str]:
    """filename -> its plan item's `unit_key`, by the SUITE'S OWN SQL."""
    conn = catalog(root)
    try:
        return {
            r["filename"]: r["unit_key"]
            for r in conn.execute(
                "SELECT f.filename, i.unit_key FROM plan_items i JOIN files f ON f.id = i.file_id"
                " WHERE i.plan_id = ?",
                (plan_id,),
            )
        }
    finally:
        conn.close()


def test_a_plan_made_after_a_stopped_read_never_splits_a_companion_unit(tmp_path):
    """R1-F1, built end to end. A Live Photo pair, `IMG_1.JPG` + `IMG_1.MOV`, is read for the
    first time and the read is STOPPED after the photo (U11's Stop). C5-4 as amended (2026-11-13):
    the stopped scan still GROUPS before it returns, so the unread video groups with the photo.
    The user then closes `pam start` and runs `pam plan` in the console: the plan holds both
    members in ONE unit. Before the amendment it put them in two, and `pam apply` renamed them
    apart (`2019-07-04_001.JPG`, `undated_001.MOV`, measured) for good."""
    root, stopped = _stopped_first_read_of_the_pair(tmp_path, lambda: True)
    assert (stopped.stopped, stopped.read) == (True, 1)
    groups = _group_ids(root)
    assert groups["IMG_1.JPG"] is not None and groups["IMG_1.JPG"] == groups["IMG_1.MOV"], (
        groups
    )

    plan = run_plan(root)

    assert plan.plan_id is not None
    units = _units_of(root, plan.plan_id)
    assert sorted(units) == sorted(LIVE_PHOTO_PAIR), units
    assert len(set(units.values())) == 1, units


def test_a_read_stopped_at_its_last_file_still_groups_and_plans_the_pair_as_one(tmp_path):
    """The controller's case (R1-F1 route (b)): Stop pressed while the LAST file is read. The stop
    is asked after every file, the last included, so every file is read and - before C5-4's
    amendment - none was grouped; a refusal keyed on "files not yet read" would not see it."""
    asked: list[int] = []

    def stop_after_the_second_file() -> bool:
        asked.append(1)
        return len(asked) == 2  # the pair's second, and last, file

    root, stopped = _stopped_first_read_of_the_pair(tmp_path, stop_after_the_second_file)
    assert (stopped.stopped, stopped.read, len(asked)) == (True, 2, 2)
    groups = _group_ids(root)
    assert groups["IMG_1.JPG"] is not None and groups["IMG_1.JPG"] == groups["IMG_1.MOV"], (
        groups
    )

    plan = run_plan(root)

    assert plan.plan_id is not None
    units = _units_of(root, plan.plan_id)
    assert sorted(units) == sorted(LIVE_PHOTO_PAIR), units
    assert len(set(units.values())) == 1, units


def test_a_read_stopped_where_a_file_became_cloud_only_still_groups_the_pair(
    tmp_path, monkeypatch
):
    """R1-F1 at C5-2's OTHER stop site (Task 5f Z1 review, M3192): the photo is freed by OneDrive
    after the walk, so its batch records it as the placeholder it now is and asks the stop THERE,
    in the became-cloud-only branch. The stopped scan still groups the pair before it returns -
    the video, never reached, included - exactly as a stop in the per-file loop does."""
    real = walker.placeholder_now
    freed: list[str] = []

    def the_photo_is_freed(path) -> bool:
        if path.name == "IMG_1.JPG":
            freed.append(path.name)
            return True
        return real(path)

    monkeypatch.setattr(walker, "placeholder_now", the_photo_is_freed)
    root, stopped = _stopped_first_read_of_the_pair(tmp_path, lambda: True)
    assert freed == ["IMG_1.JPG"]  # the stop was asked in the became-cloud-only branch...
    assert (stopped.stopped, stopped.read, stopped.placeholders) == (True, 0, 1)  # ...and only
    groups = _group_ids(root)
    assert groups["IMG_1.JPG"] is not None and groups["IMG_1.JPG"] == groups["IMG_1.MOV"], (
        groups
    )


class _Messages(logging.Handler):
    """Every message a logger is given, in order."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def _recording_logger(name: str) -> tuple[logging.Logger, _Messages]:
    logger = logging.getLogger(f"pam.tests.lock_held_bodies.{name}")
    logger.propagate = False
    logger.handlers.clear()
    messages = _Messages()
    logger.addHandler(messages)
    logger.setLevel(logging.INFO)
    return logger, messages


def test_a_stopped_scan_logs_that_it_stopped_last_and_never_that_it_was_done(tmp_path):
    """C5-4 as amended (Task 5f Z1 review, M3195): a stopped scan's log says `scan stopped` as its
    LAST line, after the grouping, and never `scan done` - the log is the record of what a read
    did. The control: the same pair scanned to the end, in another folder, does log `scan done`,
    so the negative half cannot pass because that wording moved."""
    logger, stopped_log = _recording_logger("stopped")
    _root, stopped = _stopped_first_read_of_the_pair(tmp_path / "s", lambda: True, logger)
    assert stopped.stopped is True
    assert stopped_log.lines[-1] == "scan stopped: 1 of 2 read", stopped_log.lines
    assert not [line for line in stopped_log.lines if line.startswith("scan done")]

    logger, full_log = _recording_logger("full")
    _root, full = _stopped_first_read_of_the_pair(tmp_path / "f", lambda: False, logger)
    assert full.stopped is False
    assert full_log.lines[-1].startswith("scan done: "), full_log.lines


# --- M14 Task 6a, Step 1: the apply and undo BODIES, under a lock the caller proves it holds ---
#
# Task 6's page starts a rename from its job thread under the lock the server already holds, so
# `run_apply` / `run_undo` became thin shells around `apply_locked` / `undo_locked` (the old
# `_apply` / `_undo`, renamed and given the proof), exactly as T5.8 did for scan and plan.
#
# THE GOLDEN TRANSCRIPTS ARE TYPED BY HAND, on T5.9's precedent above. They were captured ONCE by
# driving `pam apply` (typed `no`, then typed `apply`) and `pam undo` twice through `CliRunner`
# over the archive `_applyable` builds, in a scratch worktree at `v0.1-m13` (`698d2e1`), and
# pasted. The same capture at `87e7f8e` - the commit before Task 6 touched either command - was
# identical after the normalisation below. Nothing in this file regenerates them. Normalised, and
# only these: the archive's temp path (`<ROOT>`), its workspace's path (`<WORKSPACE>`), each
# log's stamp and same-second suffix (`<STAMP>`), the session id (`<SESSION>`) and the seconds
# (`<S>`) - the things that differ on every run.

GOLDEN_APPLY_HEAD = """\
Plan 1 - ready to apply
  <ROOT>
  3 renames approved   (4 proposed, 0 skipped by you, 0 already conforming)
  1 not approved - they will not be renamed.
  0 files will be deleted, moved, or modified - renames only, in place.
  Every change is journaled. Undo restores exact original names.


Type "apply" to proceed, anything else to cancel: """
GOLDEN_CANCEL = GOLDEN_APPLY_HEAD + "Cancelled. Nothing was renamed.\n"
GOLDEN_APPLY = (
    GOLDEN_APPLY_HEAD
    + """\
Done. 3 renamed, 0 failed, in <S>s.
  3 approved, 1 not approved and left as they are. To name them later: pam plan
  Every rename was journaled. Undo is available anytime - it does not
  expire - as long as the files aren't renamed outside pam.
  Report: <WORKSPACE>\\reports\\session-<SESSION>.md
  Undo:   pam undo <SESSION>
Log: <WORKSPACE>\\logs\\<STAMP>_apply.log
"""
)
GOLDEN_UNDO = """\
Done. 3 put back, 0 failed, in <S>s.
  The plan is now undone.
Log: <WORKSPACE>\\logs\\<STAMP>_undo.log
"""
GOLDEN_UNDO_AGAIN = """\
Done. 0 put back, 0 failed, in <S>s.
  3 file(s) were already back under their original names.
  The plan is now undone.
Log: <WORKSPACE>\\logs\\<STAMP>_undo.log
"""

# The three renames `_applyable`'s approvals ask for, spelled from `archives.put`'s dates (spec 6:
# `{date}_{seq}`, the extension's case kept). IMG_3.JPG is left undecided, so it never moves.
APPROVED_RENAMES = [
    ("IMG_1.JPG", "2019-07-04_001.JPG"),
    ("IMG_1.MOV", "2019-07-04_001.MOV"),
    ("IMG_2.JPG", "2020-08-05_001.JPG"),
]
ORIGINAL_NAMES = ["IMG_1.JPG", "IMG_1.MOV", "IMG_2.JPG", "IMG_3.JPG"]
APPLIED_NAMES = ["2019-07-04_001.JPG", "2019-07-04_001.MOV", "2020-08-05_001.JPG", "IMG_3.JPG"]

_SESSION_ID = re.compile(r"s-\d{8}-\d{6}")
_RUN_LOG = re.compile(r"\d{4}-\d{2}-\d{2}_\d{6}_(?P<kind>apply|undo)(_\d+)?\.log")
_SECONDS = re.compile(r" in \d+\.\d+s\.")


def _applyable(tmp_path):
    """A Live Photo unit and two singles, scanned and planned; the unit and IMG_2 approved by the
    suite's own SQL, IMG_3 left undecided. Returns (root, plan_id)."""
    root = (tmp_path / "photos").resolve()
    root.mkdir(parents=True)
    put(root, "IMG_1.JPG")
    put(root, "IMG_1.MOV")
    put(root, "IMG_2.JPG", date="2020:08:05 09:15:00")
    put(root, "IMG_3.JPG", date="2021:09:06 11:02:03")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    assert plan_id == 1
    conn = catalog(root)
    try:
        rows = conn.execute(
            "SELECT i.id FROM plan_items i JOIN files f ON f.id = i.file_id"
            " WHERE i.plan_id = ? AND f.filename IN ('IMG_1.JPG', 'IMG_1.MOV', 'IMG_2.JPG')",
            (plan_id,),
        )
        approve_ids(conn, [r["id"] for r in rows])
    finally:
        conn.close()
    return root, plan_id


def _normalise_run(output: str, root) -> str:
    """Only what varies run to run (above), and the workspace before the root it does not hold."""
    ws = workspace_if_exists(root)
    assert ws is not None
    text = output.replace(str(ws.logs_dir.parent), "<WORKSPACE>").replace(str(root), "<ROOT>")
    text = _RUN_LOG.sub(lambda m: f"<STAMP>_{m.group('kind')}.log", text)
    return _SECONDS.sub(" in <S>s.", _SESSION_ID.sub("<SESSION>", text))


def _names(root) -> list[str]:
    return sorted(p.name for p in root.iterdir() if not p.name.endswith(".tags.json"))


def _sessions(root) -> list[str]:
    conn = catalog(root)
    try:
        return [
            r[0] for r in conn.execute("SELECT DISTINCT session_id FROM journal ORDER BY 1")
        ]
    finally:
        conn.close()


@pytest.fixture()
def rename_spy(monkeypatch):
    """Every (old_rel, new_rel) handed to the one verified rename (`execute/unit.py` is its only
    call site): the CALL is watched, not only the outcome (M697)."""
    calls = []
    real = unit_mod.perform_verified_rename

    def watching(*args, **kwargs):
        calls.append((kwargs["old_rel"], kwargs["new_rel"]))
        return real(*args, **kwargs)

    monkeypatch.setattr(unit_mod, "perform_verified_rename", watching)
    return calls


def test_cli_apply_and_undo_print_the_transcripts_recorded_at_v0_1_m13(tmp_path):
    root, plan_id = _applyable(tmp_path)
    args = ["apply", str(plan_id), "--archive", str(root)]
    cancel = CliRunner().invoke(cli, args, input="no\n")
    assert (cancel.exit_code, _normalise_run(cancel.output, root)) == (0, GOLDEN_CANCEL)
    assert (_names(root), _sessions(root)) == (ORIGINAL_NAMES, [])

    applied = CliRunner().invoke(cli, args, input="apply\n")
    assert (applied.exit_code, _normalise_run(applied.output, root)) == (0, GOLDEN_APPLY)
    assert _names(root) == APPLIED_NAMES
    [session] = _sessions(root)
    assert f"  Undo:   pam undo {session}\n" in applied.output

    undo = ["undo", session, "--archive", str(root)]
    first = CliRunner().invoke(cli, undo)
    assert (first.exit_code, _normalise_run(first.output, root)) == (0, GOLDEN_UNDO)
    assert _names(root) == ORIGINAL_NAMES
    again = CliRunner().invoke(cli, undo)
    assert (again.exit_code, _normalise_run(again.output, root)) == (0, GOLDEN_UNDO_AGAIN)
    assert _names(root) == ORIGINAL_NAMES


def _apply_under(held, root, plan_id):
    ws = workspace_if_exists(root)
    assert ws is not None
    summary = ApplySummary(archive_root=root, plan_id=plan_id)
    conn = connect(ws.db_path)
    try:
        apply_locked(held, conn, root, plan_id, summary, _logger(), None, _Clock(), False)
    finally:
        conn.close()
    return summary


def _undo_under(held, root, session_id):
    ws = workspace_if_exists(root)
    assert ws is not None
    summary = UndoSummary(undone_session_id=session_id)
    conn = connect(ws.db_path)
    try:
        undo_locked(held, conn, root, session_id, summary, _logger())
    finally:
        conn.close()
    return summary


def test_apply_locked_and_undo_locked_rename_exactly_the_approved_under_the_held_lock(
    tmp_path, rename_spy
):
    """The positive control the refusals below need: under a proof for its own folder each body
    does the whole job - exactly the approved renames out, exactly their reverse back."""
    root, plan_id = _applyable(tmp_path)
    ws = workspace_if_exists(root)
    assert ws is not None
    with WorkspaceLock(ws.lock_path, ws) as held:
        applied = _apply_under(held, root, plan_id)
        assert (applied.renamed, applied.failed, applied.not_approved) == (3, 0, 1)
        assert sorted(rename_spy) == APPROVED_RENAMES
        assert _names(root) == APPLIED_NAMES
        rename_spy.clear()
        undone = _undo_under(held, root, applied.session_id)
    assert (undone.restored, undone.failed, undone.plan_status) == (3, 0, "undone")
    assert sorted(rename_spy) == sorted((new, old) for old, new in APPROVED_RENAMES)
    assert _names(root) == ORIGINAL_NAMES


def test_apply_locked_and_undo_locked_refuse_another_folders_proof_and_rename_nothing(
    tmp_path, rename_spy
):
    root, plan_id = _applyable(tmp_path / "a")
    other, _ = planned(_mkdir(tmp_path / "b"), ["IMG_1.JPG"])
    ws_other = workspace_if_exists(other)
    assert ws_other is not None
    with WorkspaceLock(ws_other.lock_path, ws_other) as held:
        with pytest.raises(AssertionError):
            _apply_under(held, root, plan_id)
    assert (rename_spy, _names(root), _sessions(root)) == ([], ORIGINAL_NAMES, [])

    # A real session to undo, then a proof for the wrong folder: nothing is put back.
    ws = workspace_if_exists(root)
    assert ws is not None
    with WorkspaceLock(ws.lock_path, ws) as own:
        session = _apply_under(own, root, plan_id).session_id
    rename_spy.clear()
    with WorkspaceLock(ws_other.lock_path, ws_other) as held:
        with pytest.raises(AssertionError):
            _undo_under(held, root, session)
    assert (rename_spy, _names(root), _sessions(root)) == ([], APPLIED_NAMES, [session])


def test_apply_locked_and_undo_locked_refuse_a_released_proof_and_rename_nothing(
    tmp_path, rename_spy
):
    root, plan_id = _applyable(tmp_path)
    ws = workspace_if_exists(root)
    assert ws is not None
    with WorkspaceLock(ws.lock_path, ws) as held:
        session = _apply_under(held, root, plan_id).session_id
    rename_spy.clear()
    with pytest.raises(AssertionError, match="released"):
        _undo_under(held, root, session)
    assert (rename_spy, _names(root)) == ([], APPLIED_NAMES)
    run_undo(session, root)
    assert _names(root) == ORIGINAL_NAMES
    before = _sessions(root)
    assert len(before) == 2  # the apply's session and the undo's, by hand
    rename_spy.clear()
    with pytest.raises(AssertionError, match="released"):
        _apply_under(held, root, plan_id)
    assert (rename_spy, _names(root), _sessions(root)) == ([], ORIGINAL_NAMES, before)


def test_run_apply_and_run_undo_still_take_the_lock_themselves(tmp_path, rename_spy):
    """The shells keep locking (T5.8's shape): while another holder has the folder's lock, both
    are refused with the CLI's sentence and nothing moves."""
    root, plan_id = _applyable(tmp_path)
    ws = workspace_if_exists(root)
    assert ws is not None
    with WorkspaceLock(ws.lock_path, ws):
        with pytest.raises(
            WorkspaceLockedError, match="Another pam command is already running"
        ):
            run_apply(plan_id, root)
        with pytest.raises(
            WorkspaceLockedError, match="Another pam command is already running"
        ):
            run_undo("s-20190704-182311", root)
    assert (rename_spy, _names(root), _sessions(root)) == ([], ORIGINAL_NAMES, [])
