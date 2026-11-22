"""M9's shipping gate, stage 2: the runner DRIVEN, killed, and resumed across a real
process boundary.

WHY THIS MODULE EXISTS BESIDE `test_enrich_runner.py`, WHICH ALREADY HAS INTERRUPTION
TESTS. Stage 1's interruption evidence comes from a `KeyboardInterrupt` raised inside a
fake enricher, in-process, at `workers=1`. `KeyboardInterrupt` is an ordinary exception:
it UNWINDS THE STACK. Every `finally` runs, `ThreadPoolExecutor.__exit__` runs, the
connection is closed politely, and the interpreter is still alive afterwards. That is
`pam/faults.py`'s `crash:` mode and its docstring already says what it is not:

    "A genuine process-level crash needs the command run in a SUBPROCESS."

Vision **P3** - *"enrichment of an interrupted run resumes without repeating completed
work"* - is a claim about a process that DIED, so it is measured here against a child
interpreter killed with `Popen.kill()` (`TerminateProcess` on Windows). The control that
makes that claim checkable is `test_a_hard_kill_is_a_real_process_death_and_not_an_unwind`:
the child registers a `finally` that writes a flag file, and after the kill **the flag is
not there**. Without that control this module would be indistinguishable from stage 1's,
and every number below would be a number about an unwind.

WHAT IS MEASURED, AND HOW THE EXPECTATION IS BUILT. The child appends every `enrich()`
call to a log file, flushed and `fsync`ed per line, so the record survives the kill; the
parent reads that log and compares it against a list of `rel_path` strings SPELLED OUT BY
HAND from the commit-batch arithmetic. Nothing here asks the runner what it did, and the
assertion is on the work **not repeated**, never on the final state being correct - a
runner that re-enriched the whole archive on every resume would also end correct, which is
exactly the oracle spec 8.1's cursor exists to make impossible.

THE BLOCK POINT IS A BARRIER, NEVER A SLEEP. The child stops dead at a chosen `enrich()`
call, writes a flag and spins; the parent waits for that flag with a deadline and fails
loudly if it never appears. CLAUDE.md's rule about hosted CI being ~3x slower applies
directly: a `time.sleep` long enough here would be a race lost on a slower runner.
"""

import collections
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from pam.catalog import repo
from pam.catalog.db import connect, migrate
from pam.enrich import COMMIT_BATCH, InferredFact, run_enricher

sys.path.insert(0, str(Path(__file__).resolve().parent))

from archives import FAKE_COMMAND, materialise_tiny_library  # noqa: E402

from pam.scan.runner import run_scan  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = "2026-08-28T10:00:00"

# How long the parent will wait for the child to reach its block point. Generous on
# purpose: it is a DEADLINE, not a timing assumption, and the only thing it has to beat
# is a cold interpreter start on a runner three times slower than this machine.
BLOCK_DEADLINE_S = 120.0

# The barrier deadline in the concurrency tests. Smaller than the one above because
# nothing has to start here - the threads already exist - and because a barrier that
# never trips is a DEADLOCK: this number is how long the suite spends discovering that
# the pool is not parallel, and it is paid on failure only.
BARRIER_DEADLINE_S = 30.0

# The corpus every kill-point case below is spelled against: 120 files, so the walk
# crosses the fiftieth and hundredth file and ends on a partial batch of twenty.
CORPUS = 120
NAMES = [f"f{i:03d}.jpg" for i in range(CORPUS)]


# --- the child ------------------------------------------------------------------------

# A whole pam process. It opens the catalog, runs the runner with a fake enricher that
# records each call to disk, and blocks forever at the call the parent names so the
# parent can kill it at a chosen point in the batch. `writes` picks the stage-1
# distinction: a fact-writing enricher makes the CACHE able to prevent a re-walk, a
# silent one leaves the CURSOR as the only mechanism that can.
_CHILD = r"""
import os, sys, threading, time
from pathlib import Path

from pam.catalog.db import connect
from pam.enrich.base import InferredFact, run_enricher

db_path, calls_path, flag_path, done_path, stop_at, workers, writes = sys.argv[1:8]
stop_at, workers, writes = int(stop_at), int(workers), writes == "1"
calls, flag, done = Path(calls_path), Path(flag_path), Path(done_path)
lock = threading.Lock()
seen = 0


class Child:
    name = "phash"
    model_name = "fake-model"
    model_version = "1.0"

    def available(self):
        return True, "the child fake is always available"

    def cost_estimate_ms(self):
        return 1

    def wants(self, file):
        return True

    def enrich(self, file, image):
        global seen
        with lock:
            with open(calls, "a", encoding="utf-8") as fh:
                fh.write(file.rel_path + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            seen += 1
            mine = seen
        if stop_at < 0 and mine >= -stop_at:
            # Stage 1's interruption, reproduced here on purpose so the two can be
            # compared side by side: this one UNWINDS and the `finally` below runs.
            flag.write_text(file.rel_path, encoding="utf-8")
            raise KeyboardInterrupt("stopped at " + file.rel_path)
        if stop_at > 0 and mine >= stop_at:
            flag.write_text(file.rel_path, encoding="utf-8")
            while True:
                time.sleep(0.02)
        return [InferredFact(kind="phash", value={"hash": file.rel_path})] if writes else []


conn = connect(Path(db_path))
try:
    summary = run_enricher(conn, Child(), lambda f: None, workers=workers)
    done.write_text("completed %d" % summary.walked, encoding="utf-8")
finally:
    # The witness that separates a hard kill from an unwind. TerminateProcess never
    # gets here; SystemExit, KeyboardInterrupt and a clean return all do.
    Path(str(done) + ".finally").write_text("the finally ran", encoding="utf-8")
"""


class _Child:
    """One child run: what it was asked, what it recorded, and how it ended."""

    def __init__(self, calls, returncode, finally_ran, done, wal_at_kill):
        self.calls = calls
        self.returncode = returncode
        self.finally_ran = finally_ran
        self.done = done
        self.wal_at_kill = wal_at_kill


def _run_child(db_path, work_dir, tag, *, stop_at=0, workers=1, writes=True):
    """Run the runner in a fresh interpreter; kill it hard if `stop_at` is set.

    `stop_at=0` means "let it finish", which is how a resume is driven through the same
    process boundary as the run it is resuming.
    """
    calls = work_dir / f"calls-{tag}.log"
    flag = work_dir / f"flag-{tag}"
    done = work_dir / f"done-{tag}"
    proc = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-c",
            _CHILD,
            str(db_path),
            str(calls),
            str(flag),
            str(done),
            str(stop_at),
            str(workers),
            "1" if writes else "0",
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    wal_at_kill = None
    if stop_at > 0:
        deadline = time.monotonic() + BLOCK_DEADLINE_S
        while time.monotonic() < deadline and not flag.exists():
            if proc.poll() is not None:
                break
            time.sleep(0.01)
        assert flag.exists(), (
            f"the child never reached enrich() call {stop_at} within {BLOCK_DEADLINE_S}s"
            f" (returncode {proc.poll()}); nothing below would be measuring a kill"
        )
        wal_at_kill = Path(str(db_path) + "-wal").exists()
        proc.kill()
    out, err = proc.communicate(timeout=BLOCK_DEADLINE_S)
    if stop_at >= 0:
        assert "Traceback" not in err, f"the child raised:\n{err[-2000:]}"
    return _Child(
        calls=calls.read_text(encoding="utf-8").splitlines() if calls.exists() else [],
        returncode=proc.returncode,
        finally_ran=Path(str(done) + ".finally").exists(),
        done=done.read_text(encoding="utf-8") if done.exists() else None,
        wal_at_kill=wal_at_kill,
    )


# --- reading what was committed, with the suite's own SQL -----------------------------


def _seed(work_dir, names=NAMES):
    """A catalog holding `names` as present files, each with its own sha256.

    No archive on disk: the runner never opens a file, the enricher is a fake and the
    loader returns None, so what is under test here is the walk, the batching and the
    cursor - and a corpus of 120 real files would only make the kill slower to reach.
    """
    db_path = work_dir / "catalog.db"
    conn = connect(db_path)
    migrate(conn)
    ids = {}
    for name in names:
        ids[name] = repo.upsert_file(
            conn,
            rel_path=name,
            filename=name,
            ext="jpg",
            media_type="image",
            size=10,
            mtime_ns=1,
            placeholder=False,
            now=NOW,
        )
        repo.set_sha256(conn, ids[name], f"sha-{name}")
    conn.commit()
    conn.close()
    return db_path, ids


def _committed(db_path):
    """Run rows, fact shas and `PRAGMA integrity_check`, on a fresh connection.

    A fresh connection because the process that wrote them is dead and its write-ahead
    log has not been checkpointed: this is the read that proves SQLite recovered the WAL
    rather than the read that proves a tidy shutdown happened.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        runs = [
            dict(r)
            for r in conn.execute(
                "SELECT id, cursor_file_id, finished_at FROM enrichment_runs ORDER BY id"
            )
        ]
        facts = [
            r["sha256"] for r in conn.execute("SELECT sha256 FROM inferred_facts ORDER BY id")
        ]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        return runs, facts, integrity
    finally:
        conn.close()


# --- the controls: a process death, and a corruption check that can fail --------------


def test_the_integrity_check_in_this_harness_can_actually_report_corruption(tmp_path):
    """The positive control for every `integrity == "ok"` below, and it was earned (M1238).

    Replacing `PRAGMA integrity_check`'s result with the literal `"ok"` in `_committed`
    left the whole suite green at **2427 passed**. So "a hard kill does not corrupt the
    catalog" - one of the questions this gate exists to answer - was being asserted by an
    oracle that had never once been shown capable of saying no. That is CLAUDE.md's M697
    in a different costume: an outcome-shaped assertion that cannot tell "nothing broke"
    from "nothing was looked at".

    Six hundred bytes are zeroed inside the b-tree leaf holding a real row, so the check
    has something genuine to find. The assertion is only that it stops saying `ok` - not
    what it says instead, because that wording is SQLite's and belongs to SQLite.
    """
    db_path, _ = _seed(tmp_path)
    raw = bytearray(db_path.read_bytes())
    offset = raw.find(b"f100.jpg")
    assert offset > 0, "the fixture's rows are not in the main database file to corrupt"
    raw[offset : offset + 600] = b"\x00" * 600
    db_path.write_bytes(bytes(raw))
    try:
        _, _, integrity = _committed(db_path)
    except sqlite3.DatabaseError:
        return  # SQLite refused the file outright, which is also "not ok"
    assert integrity != "ok", (
        "600 zeroed bytes in a b-tree leaf went unnoticed, so `integrity_check` in this"
        " harness reports `ok` for a database it has not really examined"
    )


# --- the control: this is a process death, not an unwind ------------------------------


def test_a_hard_kill_is_a_real_process_death_and_not_an_unwind(tmp_path):
    """Every P3 number in this module is worthless without this one, IN THREE ARMS.

    The child's `finally` writes a flag. `SystemExit` runs it (`faults.py` says so in as
    many words), `KeyboardInterrupt` runs it, a clean return runs it. `TerminateProcess`
    does not. So the absence of that flag is what says the evidence below is about a
    process that died rather than one that tidied up on the way out.

    THE NEGATIVE ARM ALONE IS NOT A CONTROL, AND THAT WAS MEASURED, NOT REASONED
    (stage-2 review, M1234). Deleting the child's whole `finally` block left the entire
    suite green: `finally_ran` was then False for a kill, for an unwind and for a clean
    exit alike, so this module's one claim to be different from stage 1's was resting on
    an assertion that could not fail. The fix is the other two arms - a child allowed to
    FINISH must write the flag, and a child stopped by stage 1's own `KeyboardInterrupt`
    must ALSO write it. Together they say the witness can fire, and that it fires for
    exactly the interruption this module is not about.
    """
    db_path, _ = _seed(tmp_path)

    killed = _run_child(db_path, tmp_path, "control-kill", stop_at=60, writes=False)
    assert killed.finally_ran is False, (
        "the child's finally block ran, so this was an unwind and not a kill - which is"
        " exactly the interruption stage 1 already measured"
    )
    assert killed.done is None, "the child cannot have completed; it was blocked"
    assert killed.returncode != 0
    assert len(killed.calls) == 60, "the record the child fsynced survived its own death"

    # Positive arm: the witness fires when nothing kills the process.
    finished = _run_child(db_path, tmp_path, "control-done", writes=False)
    assert finished.finally_ran is True, "the witness cannot fire at all"
    assert finished.done == "completed 70", "and the run it witnessed really completed"

    # The discriminating arm: stage 1's interruption, in a real process. It unwinds, so
    # the flag IS written - which is precisely why an in-process KeyboardInterrupt could
    # never have measured what the rows in `docs/mutations/m9.md` above measure.
    unwound = _run_child(db_path, tmp_path, "control-unwind", stop_at=-10, writes=False)
    assert unwound.finally_ran is True, (
        "a KeyboardInterrupt must run the finally; if it does not, the negative arm"
        " above is not distinguishing a kill from an unwind"
    )
    assert unwound.done is None, "and it still did not complete"


def test_the_write_ahead_log_is_live_at_the_moment_of_the_kill(tmp_path):
    """The kill lands with an un-checkpointed WAL, which is the interesting case.

    `catalog/db.py` sets `journal_mode=WAL`, so a killed writer leaves `catalog.db-wal`
    on disk holding frames no reader has merged. If this were false, every recovery
    assertion in this module would be measuring a plain closed database and the question
    the gate was asked - "does a partially written WAL after a hard kill resume?" -
    would be untested while reading green.
    """
    db_path, _ = _seed(tmp_path)
    child = _run_child(db_path, tmp_path, "wal", stop_at=60, writes=True)
    assert child.wal_at_kill is True, "no -wal file existed when the child was killed"
    _, facts, integrity = _committed(db_path)
    assert integrity == "ok"
    assert len(facts) == COMMIT_BATCH, "the recovered WAL yields exactly the committed batch"


# --- P3, measured -----------------------------------------------------------------

# Kill point -> (the cursor's file, the files the resume must call enrich() on). Spelled
# out from spec 8.1's "commits batched every 50 files": a batch commits after the 50th,
# 100th ... file is WALKED, and every file walked after the last commit is lost with the
# process. The names are 0-indexed, so the fiftieth file walked is `f049.jpg`.
#
# Written as literals rather than computed from COMMIT_BATCH: a case list derived from
# the constant it is about agrees with any value of that constant, which is this
# project's oldest measured mistake (M5's `open_plan`, M6's `ENRICHERS`).
KILL_POINTS = {
    # Killed inside the FIRST batch: nothing has ever committed, so the cursor is still
    # NULL and the resume starts at the beginning. One file of work is repeated.
    1: (None, NAMES[0:]),
    # Killed on the LAST file of the first batch - one enrich() short of the commit.
    # This is the worst case the design allows and it is worth naming: a full batch of
    # fifty enrichments is discarded, because the commit that would have kept them is
    # the very next statement.
    50: (None, NAMES[0:]),
    # Killed on the FIRST file of the second batch: one batch is committed and the
    # resume repeats only the file that was in flight.
    51: ("f049.jpg", NAMES[50:]),
    # Killed inside the trailing partial batch, after two commits.
    110: ("f099.jpg", NAMES[100:]),
}


def test_the_kill_points_still_cover_every_region_of_the_walk():
    """A collection-time pin on the table above, and it was earned (M1235).

    Deleting three of the four cases - including the worst case and the tail case - left
    the suite green at 2423 passed with no failure and no skip: a parametrize simply got
    smaller and nothing said so. That is CLAUDE.md's M743 arriving in this module, and
    the answer is the same: the list needs a floor written from the SPEC side rather than
    from the parametrize that consumes it.

    Spec 8.1 says commits are "batched every 50 files". Four regions follow from that
    sentence and each must have a case: before any commit at all, on the last file of a
    batch, on the first file of the next, and inside the trailing partial batch. The last
    assertion restates the same sentence a second way - nothing is committed until the
    fiftieth file has been walked - so a table edited to agree with a broken runner has
    to be edited in two places that do not look alike.
    """
    assert len(KILL_POINTS) == 4
    assert min(KILL_POINTS) == 1, "one kill before anything has ever committed"
    assert {50, 51} <= set(KILL_POINTS), "one on each side of a commit boundary"
    assert max(KILL_POINTS) > 100, "one inside the trailing partial batch"
    for stop_at, (cursor_name, resume) in KILL_POINTS.items():
        assert resume, f"the case at {stop_at} expects an empty resume"
        assert (cursor_name is None) is (stop_at <= 50), (
            f"the case at {stop_at} disagrees with spec 8.1's fifty-file batch about"
            " whether anything can have committed yet"
        )


@pytest.mark.parametrize("stop_at", sorted(KILL_POINTS))
def test_p3_a_resume_after_a_hard_kill_repeats_only_the_uncommitted_batch(tmp_path, stop_at):
    """Vision P3, across a real process boundary, with the CURSOR as the only mechanism.

    `writes=False`, so the child stores no facts and nothing is ever cached: a resume
    that walked from file one would be indistinguishable from a correct one if the cache
    were also in play. The expectation is the hand-written list above, and the assertion
    that carries the claim is the SECOND one - the files the resume did *not* call
    `enrich()` on.
    """
    expected_cursor_name, expected_resume = KILL_POINTS[stop_at]
    db_path, ids = _seed(tmp_path)

    first = _run_child(db_path, tmp_path, f"kill{stop_at}", stop_at=stop_at, writes=False)
    assert first.finally_ran is False
    assert first.calls == NAMES[:stop_at], "the child enriched in id order up to the kill"

    runs, _, integrity = _committed(db_path)
    assert integrity == "ok", "a hard kill must not corrupt the catalog"
    assert len(runs) == 1
    expected_cursor = None if expected_cursor_name is None else ids[expected_cursor_name]
    assert runs[0]["cursor_file_id"] == expected_cursor
    assert runs[0]["finished_at"] is None, "a killed run must stay resumable"

    second = _run_child(db_path, tmp_path, f"resume{stop_at}", writes=False)
    assert second.done == f"completed {len(expected_resume)}"
    assert second.calls == expected_resume, "the resume walked exactly the uncommitted tail"

    never_repeated = [n for n in first.calls if n not in expected_resume]
    repeated = [n for n in first.calls if n in expected_resume]
    assert never_repeated == NAMES[: len(first.calls) - len(repeated)]
    assert len(repeated) <= COMMIT_BATCH, (
        "a resume may repeat at most the one batch the kill destroyed;"
        f" it repeated {len(repeated)}"
    )

    runs, _, _ = _committed(db_path)
    assert len(runs) == 1, "the resume continued the killed run rather than opening a new one"
    assert runs[0]["finished_at"] is not None


def test_p3_repeated_hard_kills_never_accumulate_run_rows(tmp_path):
    """Three deaths and one recovery leave ONE run row, not four.

    `open_run` takes the newest UNFINISHED run for the identity, so each kill is resumed
    rather than replaced. If it did not, `enrichment_runs` would grow a row per crash and
    every resume would start from the newest - which, for a run that had just started,
    is a cursor of NULL and a full re-walk on every recovery.
    """
    db_path, ids = _seed(tmp_path)
    # Each kill is counted in the CHILD's own enrich() calls, and every child starts
    # counting at one over the files its resume was given - 120, then 70, then 20. So
    # the third kill lands inside a partial batch that never commits, which is why the
    # cursor below is the hundredth file and not the hundred-and-fifteenth.
    for tag, (stop_at, expect_cursor) in enumerate(
        [(60, "f049.jpg"), (60, "f099.jpg"), (15, "f099.jpg")]
    ):
        _run_child(db_path, tmp_path, f"repeat{tag}", stop_at=stop_at, writes=False)
        runs, _, integrity = _committed(db_path)
        assert integrity == "ok"
        assert len(runs) == 1, f"kill {tag} opened a second run row"
        assert runs[0]["finished_at"] is None
        assert runs[0]["cursor_file_id"] == ids[expect_cursor]
    final = _run_child(db_path, tmp_path, "repeat-done", writes=False)
    assert final.done == "completed 20", "the last resume walked only the trailing batch"
    runs, _, _ = _committed(db_path)
    assert len(runs) == 1
    assert runs[0]["cursor_file_id"] == ids["f119.jpg"]


def test_a_resume_with_nothing_left_to_walk_still_closes_the_killed_run(tmp_path):
    """The empty walk, reached the way a user reaches it: a kill, then a deletion.

    A run is killed with its cursor at file fifty, and before the resume the rest of the
    archive goes away - the folder was moved, the drive was unplugged, `pam scan` marked
    them `missing`. `files_to_enrich` filters on `status = 'present'`, so the resume's
    file list is EMPTY.

    That is the one shape which can reach `files[-1]` on an empty list, and it is the
    off-by-one worth pinning at a gate: the run must close rather than crash, and must
    not be offered for resume forever afterwards.
    """
    db_path, ids = _seed(tmp_path, NAMES[:100])
    first = _run_child(db_path, tmp_path, "tail", stop_at=60, writes=False)
    assert first.finally_ran is False
    runs, _, _ = _committed(db_path)
    assert runs[0]["cursor_file_id"] == ids["f049.jpg"], "the fiftieth file committed"
    assert runs[0]["finished_at"] is None, "but the run never closed"

    conn = connect(db_path)
    repo.mark_missing(conn, [ids[n] for n in NAMES[50:100]])
    conn.commit()
    conn.close()

    second = _run_child(db_path, tmp_path, "tail-resume", writes=False)
    assert second.calls == [], "there is nothing left to enrich"
    assert second.done == "completed 0"
    runs, _, integrity = _committed(db_path)
    assert integrity == "ok"
    assert len(runs) == 1
    assert runs[0]["finished_at"] is not None, "an emptied run must still be closed"
    assert runs[0]["cursor_file_id"] == ids["f049.jpg"], "and must not move its cursor"


def test_a_hard_kill_keeps_the_facts_of_every_committed_batch_and_no_others(tmp_path):
    """The write side of the same event, watched rather than inferred.

    The child writes a fact per file. After the kill the catalog must hold exactly the
    facts of the batches that committed - not the ten the child had already handed to
    `write_facts` inside the batch it never committed. That is the transaction boundary
    doing its job, and it is invisible to any assertion about the resume alone.
    """
    db_path, _ = _seed(tmp_path)
    child = _run_child(db_path, tmp_path, "facts", stop_at=110, writes=True)
    assert len(child.calls) == 110
    _, facts, integrity = _committed(db_path)
    assert integrity == "ok"
    assert facts == [f"sha-{n}" for n in NAMES[:100]], (
        "the ten files enriched inside the uncommitted batch left no trace"
    )


# --- the tiny library, driven end to end ----------------------------------------------

# Spec 9.1's committed library, counted from the FIXTURE and not from the runner. 106
# files: 80 images, 20 videos, 4 `other` and 2 sidecars (M9 Task 8 added three PNGs with
# real pixels in them, which is why 76 became 79; M10 Task 6 added `raw_preview.ARW`,
# the one RAW here with a coded image inside it, which is why 79 became 80). The last six are never hashed by
# `pam scan` (`walker.READ_MEDIA_TYPES`), which is why they reach the runner with no
# sha256 at all - and `inferred_facts` is content-addressed, so there is nothing to file
# a fact against. All 100 hashed files are distinct: the library contains no exact
# duplicate, which is why `skipped_duplicate` is 0 here and has its own tests elsewhere.
TINY_TOTAL = 106
TINY_IMAGES = 80
TINY_VIDEOS = 20
TINY_UNHASHED = 6


class CountingEnricher:
    """A phash-shaped fake: spec 8.1 rev 3 says a pixel enricher refuses videos."""

    name = "phash"
    model_name = "fake-model"
    model_version = "1.0"

    def __init__(self, cost_ms=250):
        self.cost_ms = cost_ms
        self._lock = threading.Lock()
        self.wants_calls: list[str] = []
        self.enrich_calls: list[str] = []
        self.threads: set[int] = set()

    def available(self):
        return True, "the fake is always available"

    def cost_estimate_ms(self):
        return self.cost_ms

    def wants(self, file):
        self.wants_calls.append(file.rel_path)
        return file.media_type == "image"

    def enrich(self, file, image):
        with self._lock:
            self.enrich_calls.append(file.rel_path)
            self.threads.add(threading.get_ident())
        return [InferredFact(kind="phash", value={"hash": file.rel_path})]


@pytest.fixture()
def tiny(tmp_path):
    """The spec 9.1 library, scanned, with a connection to its catalog.

    Scanned with `FAKE_COMMAND` rather than real exiftool DELIBERATELY, and it is not a
    weakening: media type comes from the extension and sha256 from hashing the bytes, so
    the composition this module counts is identical either way (measured). What real
    exiftool would add is capture DATES, which no enricher in this task reads. The
    alternative is `test_tiny_library.py`'s module-scoped exiftool skipif, and a gate
    whose headline test can vanish on a machine without a third-party binary is a gate
    that disarms silently.
    """
    root = materialise_tiny_library(tmp_path / "a")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    from pam.paths import workspace_if_exists

    ws = workspace_if_exists(root)
    assert ws is not None
    conn = connect(ws.db_path)
    yield root, conn
    conn.close()


def test_the_tiny_librarys_composition_is_what_this_module_counts_against(tiny):
    """The fixture's own numbers, read from `files`, before the runner is asked anything.

    An expectation the runner supplied would agree with any runner. These four numbers
    come from the scan of the committed library and are the source for every count in the
    test below it.
    """
    _, conn = tiny
    rows = conn.execute("SELECT media_type, sha256, status FROM files").fetchall()
    by_type = collections.Counter(r["media_type"] for r in rows)
    assert len(rows) == TINY_TOTAL
    assert {r["status"] for r in rows} == {"present"}
    assert by_type["image"] == TINY_IMAGES
    assert by_type["video"] == TINY_VIDEOS
    assert by_type["other"] + by_type["sidecar"] == TINY_UNHASHED
    assert sum(1 for r in rows if r["sha256"] is None) == TINY_UNHASHED
    hashed = [r["sha256"] for r in rows if r["sha256"] is not None]
    assert len(set(hashed)) == len(hashed), "the library holds no exact duplicate"


def test_the_runner_over_the_tiny_library_calls_enrich_on_its_images_and_nothing_else(tiny):
    """The whole runner over spec 9.1's library, with every count spelled from the fixture.

    `wants()` is asked about 100 files and not 106: a file with no sha256 is refused
    before the enricher is consulted, which is the ordering the runner's own walk
    encodes. `enrich()` is called on 80 - every image, no video, no sidecar - and the
    catalog ends with exactly 80 facts.
    """
    root, conn = tiny
    enricher = CountingEnricher()
    summary = run_enricher(conn, enricher, lambda f: None, workers=4)

    on_disk = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    images = {c for c in enricher.enrich_calls}
    assert images <= on_disk, "every enriched path is a real file in the library"

    assert summary.walked == TINY_TOTAL
    assert summary.skipped_no_sha == TINY_UNHASHED
    assert summary.skipped_wants == TINY_VIDEOS
    assert summary.skipped_duplicate == 0
    assert summary.skipped_cached == 0
    assert summary.failed == 0
    assert summary.facts_dropped == 0
    assert summary.enriched == TINY_IMAGES
    assert summary.facts_written == TINY_IMAGES
    assert len(enricher.enrich_calls) == TINY_IMAGES
    assert len(enricher.wants_calls) == TINY_TOTAL - TINY_UNHASHED

    media = dict(conn.execute("SELECT rel_path, media_type FROM files").fetchall())
    assert {media[c] for c in enricher.enrich_calls} == {"image"}

    again = CountingEnricher()
    second = run_enricher(conn, again, lambda f: None, workers=4)
    assert again.enrich_calls == [], "a completed run leaves every enriched file cached"
    assert second.skipped_cached == TINY_IMAGES
    # AND THE TWENTY VIDEOS ARE ASKED AGAIN, WHICH IS WORTH KNOWING BEFORE M10 BUILDS ON
    # THIS. The cache is keyed on FACTS, and a `wants()` refusal writes none - so a file
    # the enricher declined is re-walked and re-asked on every run for the life of the
    # archive. Cheap for a `wants()` that reads `media_type`; not cheap for one that has
    # to look something up, which is a shape spec 8.1 already contemplates ("ocr: scans
    # only", "geocode: has GPS" - both read the `capture` row).
    assert len(again.wants_calls) == TINY_VIDEOS
    assert second.skipped_wants == TINY_VIDEOS


def test_the_tiny_library_survives_a_hard_kill_and_resumes_where_it_stopped(tmp_path):
    """P3 over the real library rather than over synthetic rows.

    106 files means one commit at the fiftieth WALKED file, one at the hundredth, and a
    tail of five. The child is killed inside the second batch, so the resume must walk
    exactly the files with an id above fifty.

    THE EXPECTATION IS READ FROM `files`, NOT FROM THE FIRST CHILD. Those are two
    different lists and the difference is the whole point: the batch boundary counts
    files WALKED, while the child's log counts files ENRICHED, and six of the library's
    files carry no sha256 and are skipped before the enricher is consulted. An
    expectation built from "what the first process managed to enrich" would silently
    absorb an off-by-six.
    """
    root = materialise_tiny_library(tmp_path / "a")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    from pam.paths import workspace_if_exists

    ws = workspace_if_exists(root)
    assert ws is not None
    conn = connect(ws.db_path)
    after_cursor = [
        r["rel_path"]
        for r in conn.execute(
            "SELECT rel_path FROM files WHERE id > 50 AND sha256 IS NOT NULL ORDER BY id"
        )
    ]
    committed = {
        r["rel_path"]
        for r in conn.execute("SELECT rel_path FROM files WHERE id <= 50 ORDER BY id")
    }
    conn.close()
    assert len(committed) == 50 and after_cursor, "the fixture straddles the batch boundary"

    first = _run_child(ws.db_path, tmp_path, "tiny-kill", stop_at=55, writes=False)
    assert first.finally_ran is False
    runs, _, integrity = _committed(ws.db_path)
    assert integrity == "ok", "a hard kill over the real library must not corrupt it"
    assert runs[0]["finished_at"] is None
    assert runs[0]["cursor_file_id"] == 50, "the fiftieth file of the library committed"

    second = _run_child(ws.db_path, tmp_path, "tiny-resume", writes=False)
    assert second.done == "completed 56", "106 files less the fifty already committed"
    assert second.calls == after_cursor, "the resume walked exactly the uncommitted tail"
    assert committed.isdisjoint(second.calls), (
        "the resume re-enriched a file the first process had already committed"
    )


# --- concurrency above the two workers every other test uses ---------------------------

# `config.py` allows 1..32. Nothing in the suite ran above 2 before this module.
WORKER_COUNTS = (1, 2, 4, 8, 16, 32)


def test_the_worker_counts_here_span_the_whole_range_the_config_allows():
    """A negative control for the parametrize below, written from `config.py`'s bounds.

    CLAUDE.md's M743: a list parametrized over the very expectation it protects
    disappears with it, and pytest SKIPS an empty parametrize rather than failing it. So
    the list is pinned here, at collection, against the validator's own range.
    """
    from pam import config

    assert WORKER_COUNTS[0] == 1 and WORKER_COUNTS[-1] == 32
    assert len(WORKER_COUNTS) == 6
    assert config.Config().workers in WORKER_COUNTS, "the shipped default is exercised"

    # Driven through `load_config`, the path a user's `pam.toml` takes, so the bounds
    # come from the validator rather than from a literal transcribed beside it.
    tmp = Path(os.environ.get("TEMP", ".")) / f"pam-workers-{os.getpid()}.toml"
    try:
        for count in (*WORKER_COUNTS, 0, 33):
            tmp.write_text(f"[enrich]\nworkers = {count}\n", encoding="utf-8")
            if count in WORKER_COUNTS:
                assert config.load_config(tmp).workers == count
            else:
                with pytest.raises(config.ConfigError):
                    config.load_config(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def _facts_at(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [
            (r["sha256"], r["kind"], r["value_json"], r["confidence"], r["superseded_by"])
            for r in conn.execute("SELECT * FROM inferred_facts ORDER BY id")
        ]
    finally:
        conn.close()


@pytest.mark.parametrize("workers", WORKER_COUNTS)
def test_every_worker_count_writes_the_same_facts_in_the_same_order(tmp_path, workers):
    """Determinism above concurrency, which matters more here than speed does.

    `pool.map` yields results in submission order and `write_facts` runs on the calling
    thread, so the row order in `inferred_facts` is the file id order whatever the worker
    count. The expectation is spelled from the corpus, not from a `workers=1` run held in
    a fixture: two runs that are each other's oracle prove they agree, never that either
    is right (M8's M922/M924).
    """
    names = [f"f{i:03d}.jpg" for i in range(60)]
    db_path, _ = _seed(tmp_path, names)
    conn = connect(db_path)
    enricher = CountingEnricher()
    summary = run_enricher(conn, enricher, lambda f: None, workers=workers)
    conn.close()

    assert summary.enriched == len(names)
    assert summary.facts_written == len(names)
    assert summary.failed == 0
    assert sorted(enricher.enrich_calls) == names, "every file, exactly once, at any width"
    assert _facts_at(db_path) == [
        (f"sha-{n}", "phash", json.dumps({"hash": n}, sort_keys=True), None, None)
        for n in names
    ], "the committed rows are file-id ordered however many threads produced them"


@pytest.mark.parametrize("workers", [w for w in WORKER_COUNTS if w > 1])
def test_the_pool_really_runs_that_many_enrichers_at_once(tmp_path, workers):
    """The pool is parallel, watched at the call rather than timed.

    A `threading.Barrier` of `workers` parties inside `enrich()`: it can only be crossed
    if that many calls are in flight simultaneously, so a runner that serialised its
    workers - or one whose `max_workers` ignored the argument - deadlocks and the barrier
    raises at its timeout. No sleep decides anything; the timeout is a deadline, which is
    `tests/test_webui_server.py::await_log`'s pattern and CLAUDE.md's rule for a machine
    three times slower than this one.

    EXACTLY `workers` FILES, WHICH IS NOT A ROUND NUMBER CHOSEN FOR TIDINESS. The pool is
    handed one commit batch at a time, so a corpus that is not a whole number of barrier
    parties leaves a remainder no barrier of this width can ever cross - the run would
    deadlock on a correct runner, and the test would report the pool as broken because
    the arithmetic was.
    """
    names = [f"f{i:03d}.jpg" for i in range(workers)]
    assert len(names) <= COMMIT_BATCH, "the corpus must fit in one dispatch"
    db_path, _ = _seed(tmp_path, names)
    conn = connect(db_path)
    barrier = threading.Barrier(workers, timeout=BARRIER_DEADLINE_S)
    reached = []
    lock = threading.Lock()

    class Concurrent(CountingEnricher):
        def enrich(self, file, image):
            barrier.wait()
            with lock:
                reached.append(threading.get_ident())
            return [InferredFact(kind="phash", value={"hash": file.rel_path})]

    summary = run_enricher(conn, Concurrent(), lambda f: None, workers=workers)
    conn.close()
    assert summary.enriched == len(names)
    assert len(set(reached)) == workers, (
        f"{len(set(reached))} distinct threads ran the enricher, not {workers}"
    )


def test_a_worker_never_writes_and_the_writer_is_the_calling_thread(tmp_path):
    """At 32 workers, still exactly one writer, and it is the thread that called in.

    Stage 1 pinned this at 2. The interesting failure would be a pool wide enough that a
    result arrives while another is still being written, and the answer is the same: the
    write happens where `pool.map` is consumed.
    """
    names = [f"f{i:03d}.jpg" for i in range(64)]
    db_path, _ = _seed(tmp_path, names)
    conn = connect(db_path)
    writers = set()

    class Watching(CountingEnricher):
        def wants(self, file):
            writers.add(threading.get_ident())
            return True

    enricher = Watching()
    run_enricher(conn, enricher, lambda f: None, workers=32)
    conn.close()
    assert writers == {threading.get_ident()}, "the walk ran on the caller's thread"
    assert threading.get_ident() not in enricher.threads, "and no enrich() did"
    assert len(enricher.threads) > 1, "while the enrichers really were spread over the pool"


# --- the duplicate skip, and what it costs when the first copy fails -------------------


class Doomed(CountingEnricher):
    """Fails on one named path and succeeds on every other - the asymmetric failure.

    Two files can hold identical bytes and still not be equally readable: a path over the
    Windows limit, a member of a companion unit locked by a sync client, a copy on a
    drive that has gone away. The sha256 the fact is filed against is the same for both.
    """

    def __init__(self, doomed):
        super().__init__()
        self.doomed = doomed

    def enrich(self, file, image):
        with self._lock:
            self.enrich_calls.append(file.rel_path)
        if file.rel_path in self.doomed:
            raise OSError(f"cannot open {file.rel_path}")
        return [InferredFact(kind="phash", value={"hash": file.sha256})]


def _duplicate_pair(work_dir, pad=0):
    """Two files with ONE sha256 between them, optionally split across a commit batch."""
    db_path = work_dir / "catalog.db"
    conn = connect(db_path)
    migrate(conn)

    def add(name, sha):
        file_id = repo.upsert_file(
            conn,
            rel_path=name,
            filename=name,
            ext="jpg",
            media_type="image",
            size=10,
            mtime_ns=1,
            placeholder=False,
            now=NOW,
        )
        repo.set_sha256(conn, file_id, sha)

    add("A_unreadable.jpg", "sha-shared")
    for i in range(pad):
        add(f"pad{i:03d}.jpg", f"sha-pad{i:03d}")
    add("B_readable.jpg", "sha-shared")
    conn.commit()
    return db_path, conn


def test_a_duplicate_in_the_SAME_batch_as_a_failure_is_still_not_retried(tmp_path):
    """THE RESIDUAL DEFECT, pinned deliberately. Read the whole docstring before changing.

    `in_flight` records a sha at DISPATCH. The stage-2 gate found that it was never
    cleared, so a file whose `enrich()` failed left its bytes blocked for the whole run
    AND for every run after it - no fact is written, so nothing caches the content, and
    the next run repeats the identical sequence forever. The controller closed the
    cross-batch half with one `in_flight.discard` in `flush`'s failure branch (see
    `test_a_duplicate_in_a_LATER_batch_than_a_failure_IS_retried` below).

    THIS HALF IS NOT CLOSED, and the reason is structural rather than an oversight: both
    files are walked and dispatched BEFORE `flush` runs, so at the moment `B_readable`
    is refused the failure has not happened yet. Clearing a sha the runner has not yet
    tried is not available to `flush`; closing it would mean re-dispatching inside the
    batch, which is a different design and not one Task 7 was asked for.

    The cost is bounded and worth stating plainly: two files sharing a sha256, walked
    within the same 50-file batch, where the FIRST is unreadable and the SECOND is not.
    Those bytes go un-enriched until a `--refresh-older-than-model` or a re-scan moves
    one of them into a different batch. `pam status` shows the coverage shortfall and
    nothing says why - which is the half worth carrying to the M9 close.
    """
    db_path, conn = _duplicate_pair(tmp_path, pad=0)
    for _ in range(3):
        enricher = Doomed({"A_unreadable.jpg"})
        summary = run_enricher(conn, enricher, lambda f: None, workers=1)
        assert enricher.enrich_calls.count("A_unreadable.jpg") == 1
        assert "B_readable.jpg" not in enricher.enrich_calls
        assert summary.failed == 1
        assert summary.skipped_duplicate == 1
    conn.close()
    assert [f[0] for f in _facts_at(db_path) if f[0] == "sha-shared"] == [], (
        "the enrichment of these bytes is unreachable, and no counter in the summary"
        " says so: `failed` blames one file and `skipped_duplicate` exonerates the other"
    )


def test_a_duplicate_in_a_LATER_batch_than_a_failure_IS_retried(tmp_path):
    """The controller's fix, measured rather than assumed (M1242).

    `pad=60` puts the twin in the second commit batch, so by the time it is walked the
    failure is already known and `flush` has released the sha. Before the fix this test
    was the `[60]` case of a test asserting the OPPOSITE, written that way on purpose:
    CLAUDE.md's M8 lesson is that a fix nothing notices is a regression waiting to
    happen, so the repair was made to break something. It did - `[60]` failed on the
    first run with the fix in place, and `[0]` did not.

    Asserted on the CALL and on the stored row, not on a counter: `skipped_duplicate`
    would read 0 whether the twin was enriched or never walked at all.
    """
    db_path, conn = _duplicate_pair(tmp_path, pad=60)

    first = Doomed({"A_unreadable.jpg"})
    summary = run_enricher(conn, first, lambda f: None, workers=1)
    assert first.enrich_calls.count("A_unreadable.jpg") == 1
    assert "B_readable.jpg" in first.enrich_calls, "the released sha is dispatchable"
    assert summary.failed == 1
    assert summary.skipped_duplicate == 0, "nothing was standing in the twin's way"

    # The content is now covered, so the SECOND run asks about neither of them - which
    # is the point: one readable twin rescues bytes the unreadable one cannot reach.
    second = Doomed({"A_unreadable.jpg"})
    again = run_enricher(conn, second, lambda f: None, workers=1)
    assert second.enrich_calls == []
    assert again.failed == 0, "the unreadable file is never opened again"
    conn.close()
    assert [f[0] for f in _facts_at(db_path) if f[0] == "sha-shared"] == ["sha-shared"]


def test_a_duplicate_of_a_SUCCEEDING_file_is_covered_by_it_which_is_the_design(tmp_path):
    """The control the test above needs: when the first copy works, one fact serves both.

    Without this, "the duplicate was skipped" would read as a bug in every case rather
    than in the failing one, and the dedupe at dispatch - which saves a whole model call
    per exact duplicate - would look like the defect instead of the feature.
    """
    db_path, conn = _duplicate_pair(tmp_path)
    enricher = Doomed(set())
    summary = run_enricher(conn, enricher, lambda f: None, workers=1)
    conn.close()
    assert enricher.enrich_calls == ["A_unreadable.jpg"]
    assert summary.skipped_duplicate == 1
    assert summary.failed == 0
    assert [f[0] for f in _facts_at(db_path)] == ["sha-shared"], "one fact, both files"


# --- what the progress display would actually tell a user ------------------------------


def test_the_eta_does_not_count_files_it_already_knows_it_will_skip(tmp_path):
    """Spec 8.1 rev 16, end to end through the runner rather than through `Eta` alone.

    Rev 15 left the formula to inference and the runner inferred
    `per_file_ms() * (len(files) - walked)` - the files left to WALK, times the cost of
    a file that is actually ENRICHED, undivided by `workers`. On a re-run almost every
    remaining file is already cached and costs nothing, so that priced fifty files at a
    full model call when ten of them were work. Rev 16 says `files_remaining` counts
    only the files the runner does not already know it will skip.

    BY HAND, at the first tick: ninety of a hundred files cached, so ten files remain
    eligible, none of them yet walked (the cache covers `f000`-`f089` and the first
    batch stops at `f049`); the prior is 250 ms and `workers` is 4, so
    `250 x 10 / min(4, 10)` = **625 ms**. The rev-15 reading printed `250 x 50` =
    12500 ms at the same tick.
    """
    names = [f"f{i:03d}.jpg" for i in range(100)]
    db_path, _ = _seed(tmp_path, names)
    conn = connect(db_path)

    class Partial(CountingEnricher):
        def wants(self, file):
            return int(file.rel_path[1:4]) < 90

    run_enricher(conn, Partial(), lambda f: None, workers=4)

    ticks = []
    second = CountingEnricher(cost_ms=250)
    summary = run_enricher(conn, second, lambda f: None, workers=4, progress=ticks.append)
    conn.close()

    assert summary.skipped_cached == 90
    assert summary.enriched == 10, "ten files of real work remain"
    assert ticks[0].walked == 50
    assert ticks[0].eta_ms == 625.0, (
        "the first tick prices the ten files that are actually work, over four workers"
        " - not the fifty unwalked files, forty of which are in the cache set the"
        " runner computed before it started"
    )
    assert ticks[0].enriched == 0, "and none of the first fifty cost anything at all"
    assert ticks[-1].eta_ms == 0.0, "nothing eligible left, and no division by zero"
