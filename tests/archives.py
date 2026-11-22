"""Build, scan, plan and inspect a real temp archive. Shared by the execute tests.

Not `conftest.py`: these are plain helpers rather than fixtures, and conftest already
owns the isolation fixtures and the three autouse tripwires at ~270 lines. Not
`test_apply.py` either, which is where they started - `test_execute_unit.py` imported
six of them from it **by module name**, and M5 Tasks 8 and 9 would have made
`recover.py` and `undo.py` the third and fourth importers of a *test* module. That
couples unrelated files through collection order and makes deleting a test somebody
else imports a surprise.

Everything here builds an archive of REAL files on a real temp filesystem and reads back what
was actually committed. Nothing is mocked: what is under test is a pairing - what the
catalog says AND what the disk holds - and a mock can only confirm what the test
author already believed.
"""

import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

from video_samples import write_samples

from pam import verify
from pam.catalog import repo
from pam.catalog.db import connect, connect_readonly
from pam.naming.planner import run_plan
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan

FAKE_COMMAND = [sys.executable, str(Path(__file__).parent / "fake_exiftool.py")]

# `repo.plan_rows`' required `floor` (M11 Task 12), for the tests that read a plan and are
# not about people: the shipped `[tiers] faces` floor, spelled from spec 11. No fixture
# using it plants a face, so the value selects nothing either way.
FACES_FLOOR = 0.4

# One capture date per file, so every proposed name is predictable and distinct:
# IMG_1.JPG -> 2019-07-04_001.JPG, IMG_2.JPG -> 2020-08-05_001.JPG, and so on.
DATES = {
    "IMG_1.JPG": "2019:07:04 18:23:11",
    "IMG_2.JPG": "2020:08:05 09:15:00",
    "IMG_3.JPG": "2021:09:06 11:02:03",
}


DATE = "2019:07:04 18:23:11"


def put(root, name, date=DATE, content=None):
    """One archive file plus the sidecar the fake exiftool reads its date from.

    The general-purpose builder, unlike `build_archive` below, which only knows the
    three names in `DATES`. `content=None` writes a small unique payload, so every
    file in an archive has a distinct sha256 and "no file lost / no file overwritten"
    can be asserted over a SET of digests rather than a count.
    """
    (root / name).write_bytes(
        content if content is not None else f"the only copy of {name}".encode()
    )
    # `PamTestSidecar` is inert (no parser looks at it) and makes the SIDECAR's bytes
    # unique too. Without it two files sharing a date have byte-identical sidecars,
    # so "the set of sha256 digests on disk is unchanged" silently collapses two
    # files into one entry and a lost sidecar cannot be seen by a set comparison.
    (root / f"{name}.tags.json").write_text(
        json.dumps({"DateTimeOriginal": date, "PamTestSidecar": name}), encoding="utf-8"
    )


def archive_names(root):
    """What is really on disk, minus the fake-exiftool sidecars."""
    return sorted(p.name for p in root.iterdir() if not p.name.endswith(".tags.json"))


def companion_archive(tmp_path, names=("IMG_1.JPG", "IMG_1.MOV")):
    """One stem, several extensions: one companion group, one unit (spec 5.4)."""
    root = tmp_path / "photos"
    root.mkdir()
    for name in names:
        put(root, name)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    summary = run_plan(root)
    assert summary.plan_id is not None
    return root, summary.plan_id


def build_archive(tmp_path, names):
    root = tmp_path / "photos"
    root.mkdir()
    for name in names:
        (root / name).write_bytes(f"the only copy of {name}".encode())
        (root / f"{name}.tags.json").write_text(
            json.dumps({"DateTimeOriginal": DATES[name]}), encoding="utf-8"
        )
    return root


def materialise_tiny_library(root):
    """The spec 9.1 tiny library, laid down at `root` and ready to scan.

    A COPY, never `tests/tiny_library/` itself: from M5 the suite renames files, and a
    test that scanned the committed folder in place would be one `pam apply` away from
    renaming the fixture other tests assert against.

    Two things happen here that `shutil.copytree` alone does not do, and both are why
    this is a function rather than a line every caller writes for itself:

      * SEVEN FILES ARE NOT IN THE REPOSITORY. `git add` refuses the reserved device
        stems (AUX, CON, NUL, PRN, COM1, LPT1, LPT0) on Windows, so the committed folder
        holds 99 files and the library under test holds 106. A caller that only copied
        would be scanning a different library from the one Task 7 pinned, and would find
        out by getting different numbers rather than by being told.
      * MTIMES ARE STAMPED. A file's mtime is not in its bytes and git does not preserve
        it, so a fresh clone dates every file at checkout time and the fourteen dateless
        files fall back to that. `stamp_mtimes` pins them to a fixed epoch.

    THE COPY DELIBERATELY DOES NOT INHERIT MTIMES (`copy_function=shutil.copy`, which
    copies content and permissions but not timestamps - `copytree`'s default `copy2`
    copies timestamps too). Review findings M469/M470/M471, and the reasoning is worth
    keeping because it took three mutations to see:

      * Deleting `stamp_mtimes(root)` below left the whole suite green. So did deleting
        the `stamp_mtimes(dest)` at the end of `_write_all`, which is the stamp actually
        doing the work. The two are REDUNDANT and each masks the other.
      * With `copy2`, deleting BOTH was *still* green - because every file in
        `tests/tiny_library/` in a working tree that has ever run the builder already
        carries `FIXED_MTIME_EPOCH` on disk (measured: one distinct mtime across the
        whole folder), and `copy2` preserved it faithfully. The determinism the pinned
        plan names depend on was coming from the developer's filesystem.
      * That is green here and red on a fresh CI clone, where git dates every file at
        checkout - the exact inversion of a useful signal.

    Not inheriting mtimes makes the stamping load-bearing on every machine: with this
    argument in place, removing both stamps fails `test_plan_proposes_exactly_these_names`
    immediately. The call below is kept as the redundant half deliberately - `_write_all`
    stamps as a side effect of building, and a future `write_device_name_files` that
    stopped doing so should not silently un-pin fourteen filenames.

    `build_tiny_library` lives in `tools/`, which is not a package, so the import is
    done here rather than at module scope - importing it costs the other ten modules
    that import this file a `sys.path` mutation they have no use for.
    """
    tools = str(Path(__file__).resolve().parent.parent / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import build_tiny_library

    shutil.copytree(build_tiny_library.LIBRARY_DIR, root, copy_function=shutil.copy)
    report = build_tiny_library.write_device_name_files(root)
    assert report.skipped == [], (
        f"this filesystem refused a reserved device stem: {report.skipped}. The counts"
        " every caller pins include those files, so they cannot simply be dropped -"
        " decide deliberately what this platform's numbers should be."
    )
    build_tiny_library.stamp_mtimes(root)
    return root


def raw_with_preview_bytes(name: str) -> bytes:
    """A RAW file that really carries the camera's three embedded previews (M10 Task 6).

    A construction that needs a RAW half in a companion unit used to write PNG bytes
    under a `.NEF` name, which Pillow opened happily - it sniffs content, not extension.
    Design 8.1 rev 17 / amendment A4 made the image loader branch on the EXTENSION, so a
    RAW-named file is now handed to exiftool and a mislabelled PNG has no preview in it
    to find. THE FIXTURE WAS THE THING THAT WAS WRONG: pam classifies by extension
    everywhere, and a `.NEF` that is not a NEF is not a case the product owes anything.

    `name` is written into the file, so two files with different names have different
    digests - which `inferred_facts`, being content-addressed, depends on.
    """
    tools = str(Path(__file__).resolve().parent.parent / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import build_tiny_library

    return build_tiny_library.raw_with_previews(name)


def planned(tmp_path, names):
    """An archive of real files, scanned and planned. Returns (root, plan_id)."""
    root = build_archive(tmp_path, names)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    summary = run_plan(root)
    assert summary.plan_id is not None, "the fixture archive must produce a plan"
    return root, summary.plan_id


def catalog(root):
    """A fresh connection to the workspace catalog, for reading what was committed."""
    ws = workspace_if_exists(root)
    assert ws is not None
    conn = connect(ws.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def hashes(root):
    """Every file's content hash, sorted. Invariant across any pam operation:
    pam renames, it never rewrites (spec 7.0 J8, vision principle 1)."""
    return sorted(
        hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()
    )


# --- moved here at M6 Task 1 ---------------------------------------------------------
# `device_mov_bytes` and `digests` were private to `test_faults_matrix.py` until the
# seeded round-trip harness moved out of it into `test_round_trip_property.py`
# (docs/implementation-notes/m5.md section 3.5). Both modules need them, and open
# question 14's ruling is that a shared helper lives here rather than being imported
# from one test module by another. The bodies are unchanged by the move.

_MOV_BYTES: bytes | None = None


def device_mov_bytes() -> bytes:
    """A REAL device-shaped QuickTime file (tests/video_samples.py), read once.

    The standalone-video row is a rev 3 shipping gate, so the bytes being renamed are
    a genuine `ftyp/moov/mdat` file rather than a text placeholder. Its capture facts
    come from the fake exiftool's sidecar, which is deliberate - see
    `test_a_standalone_video_survives_a_crash_under_real_exiftool` for the reason and
    for the one cell that uses the real one.
    """
    global _MOV_BYTES
    if _MOV_BYTES is None:
        with tempfile.TemporaryDirectory() as directory:
            _MOV_BYTES = write_samples(Path(directory))["iphone"].read_bytes()
    return _MOV_BYTES


def digests(root: Path) -> dict[str, str]:
    """sha256 -> filename, for every file in the archive.

    Content-addressed rather than name-addressed because that is exactly the identity
    a rename does not change: it lets the same map answer "is every file still here"
    (the keys), "did anything get overwritten" (the size), "did any byte change" (the
    keys again) AND "where is each original file now" (the values), which is what
    "undo restores exact names" is asserted against.

    Every file the matrix writes has unique content, which is asserted here rather
    than assumed: two files sharing a digest would silently collapse into one entry
    and make every set comparison below weaker than it reads.
    """
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest not in found, (
            f"{path.name} and {found[digest]} have identical bytes, so a digest-set"
            " comparison cannot tell them apart"
        )
        # The rel_path, not the bare name: assertion 5 compares these maps, and a bare
        # name cannot see a file restored into a DIFFERENT subdirectory under the same
        # name. Unreachable from this module's flat archives, but the assertion should
        # be as strong as it reads - `perform_verified_rename`'s `not_same_directory`
        # refusal is the production guarantee, and this is the witness for it.
        found[digest] = path.relative_to(root).as_posix()
    return found


class OpenPlan(NamedTuple):
    id: int
    status: str
    template: str


def open_plan(conn):
    """The newest `open` plan, read with the test suite's OWN SQL.

    This was `repo.open_plan`, deleted at the M5 review gate: it had zero production
    call sites across two milestones, and the M4 gate's ruling ("anything in that list
    still unconsumed when M5 is reviewed gets deleted then") re-armed here. Its
    unverified-ness was visible in the code - it SELECTed `already_conforming` and
    then dropped it on the floor, so `PlanRow.already_conforming` was permanently 0
    and no test could see it, because no test read a field the constructor never set.

    Living in `tests/` is the improvement, not the cost. Every caller is a supersede
    ORACLE - "is there an open plan now?" - and an oracle that calls the production
    query cannot catch that query being wrong. This is the same ruling open question
    13 made about the J6 tripwire: keep an independent second implementation.
    """
    row = conn.execute(
        "SELECT id, status, template FROM plans WHERE status = 'open' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return None if row is None else OpenPlan(row["id"], row["status"], row["template"])


# --- F1 (M13 Task 1): the suite's own approval, with its own SQL -----------------------


def approve_all(conn, plan_id):
    """Approve every UNDECIDED item, Low included - the suite's own second implementation.

    F1 (2026-10-08): `pam apply` acts on `approved` items only. Tests that exist to exercise
    the rename path need a plan that is approved; they must not get it from the production
    approve path (CLAUDE.md: an oracle built from the definition it checks is a survivor).
    `skipped` stays skipped - this mirrors a user who approved everything they did not skip.
    """
    conn.execute(
        "UPDATE plan_items SET status = 'approved' WHERE plan_id = ? AND status = 'proposed'",
        (plan_id,),
    )
    conn.commit()


def approve_ids(conn, item_ids):
    """Approve exactly these plan_items rows, nothing else."""
    conn.executemany(
        "UPDATE plan_items SET status = 'approved' WHERE id = ?", [(i,) for i in item_ids]
    )
    conn.commit()


def approve_archive(root, plan_id):
    """`approve_all` on the archive's own catalog, through a connection opened and closed here.

    For the tests that hold no connection of their own (most of them drive `run_apply` on a
    root and read the result back afterwards). Same SQL as `approve_all`, by calling it.
    """
    conn = catalog(root)
    try:
        approve_all(conn, plan_id)
    finally:
        conn.close()


def approve_and_apply(plan_id, root, **kwargs):
    """`approve_archive`, then the real `run_apply` - for the tests that apply a plan in order
    to exercise RENAMING (M13 Task 1 Step 9). Every undecided item is approved with the suite's
    own SQL first; a `skipped` item stays skipped, so a test about a skip still means it.

    NEVER `accept_all=True` in its place: `--all` holds Low back, so it would change what the
    test covers. Spelled at the call site rather than hidden in a fixture builder, so a reader
    can see which applies were approved and which were not.
    """
    from pam.execute.apply import run_apply  # local: most importers of this module never apply

    approve_archive(root, plan_id)
    return run_apply(plan_id, root, **kwargs)


# --- `pam verify` in the loop (M6 Task 4 Step 2) -------------------------------------
# Spec 7.5's closing sentence: "`pam verify` is wired into CI against the tiny library
# after every apply/undo test scenario." Here rather than in `test_faults_matrix.py`
# because the fault matrix and the round-trip harness both call it, and open question
# 14's ruling is that a helper two test modules share lives in this file rather than
# being imported from one test module by another.


def verify_findings(root):
    """Every check `pam verify` runs, against this archive's workspace. Read-only.

    THE CHECKS, NOT THE COMMAND, and the difference is load-bearing. `run_verify` runs
    spec 7.2's recovery first, through a write connection - which is right for a person
    at a terminal and wrong here: a scenario that deliberately manufactures a state and
    then asks what `pam verify` makes of it would get an answer about the archive AFTER
    a repair pass this helper had just run. Every caller drives recovery itself, where
    and as often as its own scenario needs, and then asks this what is left. A helper
    that healed on its way in would report a clean archive it had just cleaned, hiding
    exactly the divergence Step 2 exists to find.

    Returns only the non-passing results, so a caller's expectation is a short list of
    spec 7.5 check keys rather than thirteen statuses.
    """
    ws = workspace_if_exists(root)
    assert ws is not None, "the scenario archive was never scanned"
    conn = connect_readonly(ws.db_path)
    try:
        ctx = verify.CheckContext(conn=conn, archive_root=root, thumbs_dir=ws.thumbs_dir)
        results = verify.run_checks(ctx)
    finally:
        conn.close()
    assert len(results) == len(verify.CHECKS), "a check went missing from the runner"
    return [r for r in results if r.status != verify.STATUS_PASS]


def assert_verify_agrees(root, *, expect=(), where=""):
    """`pam verify` finds exactly `expect` and nothing else. Empty means a clean bill.

    `expect` is a tuple of spec 7.5 check keys (`"4e"`, `"2b"`, ...). It is EXACT in
    both directions on purpose: a scenario that stops producing the finding it declares
    is as interesting as one that starts producing a finding it does not, and a helper
    that only asserted "at least these" would let the first pass silently.

    Where a scenario and the checker disagree, the checker's report is the truth and the
    scenario says what it expects - spec 7.0 names states that are "reported for human
    attention", and a cell that legitimately ends in one must assert that `pam verify`
    REPORTS it, never that `pam verify` is silent about it.
    """
    findings = verify_findings(root)
    assert sorted(r.key for r in findings) == sorted(expect), (
        f"{where}: pam verify reported "
        f"{[(r.key, r.count, r.details) for r in findings]}, expected {sorted(expect)}"
    )
    return findings


def journal_pairs(conn, state):
    return [(r.old_rel_path, r.new_rel_path) for r in repo.rows_in_state(conn, state)]


def item_statuses(conn, plan_id):
    return {item.old_rel_path: item.status for item in repo.plan_items(conn, plan_id)}
