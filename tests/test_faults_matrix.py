"""The fault-injection matrix (spec 9.2 priority 2) - M5's shipping gate.

Spec 9.2 requires **every fault-injection point (spec 9.3) x {apply, undo} x {single
file, companion unit, standalone video}**, asserting: no file lost, no file
overwritten, hashes unchanged, recovery converges (spec 7.0 J7), and undo restores
exact names. Spec 9.3 rev 8 names SEVEN checkpoints, so the nominal matrix is
7 x 2 x 3 = **42 cells**, plus the `dbfail:` mode on the two commit checkpoints
(2 x 2 x 3 = 12 more).

WHY THIS MODULE EXISTS BESIDE `test_recover.py`. That module drives ~15 crash
scenarios **in process**, and they stay: they are the fine-grained assertions about
what each branch of spec 7.2 does. This one is the layer that proves the same
properties across a genuine **process boundary**. `crash:` raises SystemExit, which
UNWINDS rather than kills (see `faults.maybe_fault`): in process every `finally` runs,
every context manager exits, the workspace lock releases, `faults._counts` survives and
`PAM_FAULT` stays armed. Spec 9.3's "recovery runs on the *next* invocation" means a
fresh interpreter, and only a subprocess is one.

THE APPLICABILITY TABLE IS THE POINT (`REACH` below). Without it the 42 cells are
mostly vacuous: a checkpoint that cannot fire in a shape runs a fully successful
command and passes all five assertions trivially. So every cell is classified, and the
classification decides the assertion:

  * a cell the table says CAN fire runs the command in a child process and proves the
    crash happened three independent ways - the child reports SystemExit, the exact
    production message `PAM_FAULT: crashed at <name> hit <n>`, and `faults._counts`
    for that checkpoint. A checkpoint expected to fire and not firing is a FAILURE.
  * a cell the table says CANNOT fire is a DECLARED skip - but declared *positively*:
    the same scenario runs in process with the same spec armed, and the cell asserts
    the checkpoint recorded **zero** hits and the command ran to completion. A
    `pytest.skip` would record the claim; this proves it, which is what stops the
    table from drifting away from the code it describes.

`REACH` is keyed by `faults.RENAME_CHECKPOINTS` and pinned to it by a test, so a checkpoint
added in a later milestone cannot be silently left out of the matrix. The MIGRATION
checkpoints (E12, M15 Task 9) are not rows: no rename reaches them, and
`tests/test_schema_ledger.py` drives each one against `db.migrate`.

Everything here builds an archive of REAL files on a real temp filesystem and reads back what
was actually committed. Nothing is mocked.
"""

import json
import logging
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from archives import (
    FAKE_COMMAND,
    approve_archive,
    archive_names,
    assert_verify_agrees,
    catalog,
    hashes,
    item_statuses,
    journal_pairs,
    put,
)
from archives import device_mov_bytes as _device_mov_bytes
from archives import digests as _digests
from video_samples import write_samples

from pam import faults
from pam.catalog import repo
from pam.execute.apply import run_apply
from pam.execute.recover import recover
from pam.execute.undo import run_undo
from pam.naming.planner import run_plan
from pam.scan.runner import run_scan

REPO_ROOT = Path(__file__).resolve().parent.parent
LOGGER = logging.getLogger("tests.faults_matrix")

OPERATIONS = ("apply", "undo")
SHAPES = ("single", "unit", "video")

# One capture date for everything, so every proposed stem is `2019-07-04_001` and a
# scenario's expected names can be written down rather than derived.
STEM = "2019-07-04_001"
SINGLE_MEMBERS = ("IMG_1.JPG",)
# Three members, not two: `mid_companion_unit:1` needs a "between members" to sit in,
# and `mid_unit_revert` needs a revert loop with more than one iteration for a crash
# INSIDE the repair to leave something behind.
UNIT_MEMBERS = ("IMG_1.JPG", "IMG_1.MOV", "IMG_1.XMP")
VIDEO_MEMBERS = ("CLIP_9.MOV",)
MEMBERS = {"single": SINGLE_MEMBERS, "unit": UNIT_MEMBERS, "video": VIDEO_MEMBERS}

BLOCKER_BYTES = b"in the way - placed by the fault matrix"


# --- the applicability table (spec 9.2 x spec 9.3, reconciled with the code) --------

UNIT_ONLY = (
    "it lives inside the companion sequencing/revert loop in execute/unit.py, and a"
    " single-file unit has no second member to sit between and nothing to put back"
)


@dataclass(frozen=True)
class Reach:
    """Where one checkpoint can actually fire, and why not where it cannot.

    A data structure rather than prose because the parametrization is GENERATED from
    it: the table and the cells that run cannot drift apart, and a checkpoint added to
    `faults.CHECKPOINTS` without a row here fails `test_the_table_covers_every_checkpoint`
    rather than quietly shrinking the matrix.
    """

    shapes: tuple[str, ...]
    # `mid_unit_revert` only runs when a unit is being PUT BACK, so its scenario has to
    # force a member to fail. Without this the cell arms a checkpoint the run never
    # reaches and passes on a fully successful apply.
    forces_failure: bool = False
    shape_limit: str = ""

    def cannot_fire(self, operation: str, shape: str) -> str:
        """The declared reason this cell cannot fire, or '' when it can."""
        if shape not in self.shapes:
            return f"{shape}: {self.shape_limit}"
        return ""


# Every checkpoint reaches BOTH operations, so `Reach` has no operation restriction to
# express. It had one until the M5 review gate: `before_plan_finalize` was declared
# apply-only because `undo._finalize_plan` carried no `maybe_fault` - an accident of the
# code, not a fact about spec 9.3, which describes the checkpoint in op-neutral terms.
# The review added the missing call and three declared skips became live cells. The
# unused `operation_limit` field and its branch in `cannot_fire` went with it rather
# than surviving as a branch with no reachable input: an unreachable branch is a
# mutation nothing can kill, which is the shape this whole module exists to refuse.
REACH = {
    "before_intent_commit": Reach(SHAPES),
    "after_intent": Reach(SHAPES),
    "after_rename": Reach(SHAPES),
    "before_done_commit": Reach(SHAPES),
    "mid_companion_unit": Reach(("unit",), shape_limit=UNIT_ONLY),
    "mid_unit_revert": Reach(("unit",), forces_failure=True, shape_limit=UNIT_ONLY),
    "before_plan_finalize": Reach(SHAPES),
}

# Spec 9.3 rev 7: `dbfail:` raises sqlite3.OperationalError instead of SystemExit and
# is caught IN PROCESS, so the run must not be left half-committed. It is armed at the
# two commit checkpoints - the only two places a storage failure has a distinct
# signature from a filesystem one.
DBFAIL_CHECKPOINTS = ("before_intent_commit", "before_done_commit")


def _cells():
    """Every (checkpoint, operation, shape) the spec names, with its classification."""
    return [
        (checkpoint, operation, shape, REACH[checkpoint].cannot_fire(operation, shape))
        for checkpoint in faults.RENAME_CHECKPOINTS
        for operation in OPERATIONS
        for shape in SHAPES
    ]


def _cell_id(cell) -> str:
    checkpoint, operation, shape, cannot_fire = cell
    return f"{checkpoint}-{operation}-{shape}{'-cannot-fire' if cannot_fire else ''}"


CELLS = _cells()
CELL_IDS = [_cell_id(cell) for cell in CELLS]


# --- building the three archive shapes ---------------------------------------------


def _archive(tmp_path, shape):
    """An archive of real files of the given shape, scanned and planned. Returns (root, plan_id)."""
    root = tmp_path / "photos"
    root.mkdir()
    for name in MEMBERS[shape]:
        put(root, name, content=_device_mov_bytes() if name.endswith(".MOV") else None)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    summary = run_plan(root)
    assert summary.plan_id is not None, "the matrix archive must produce a plan"
    # F1 (M13): `pam apply` renames only approved items, so the matrix approves every item
    # here, in the PARENT, with the suite's own SQL - before any child process applies.
    approve_archive(root, summary.plan_id)
    return root, summary.plan_id


def _blocker_name(operation: str, shape: str) -> str:
    """The name to occupy so the LAST member of the unit cannot be renamed.

    The last one, so at least one member has already moved when the failure lands -
    which is the only shape that reaches spec 7.3's revert loop at all. Going forward
    that is the last member's TARGET; going backward it is its ORIGINAL name, because
    undo replays a unit in reverse (the last member applied is the first put back).
    """
    members = MEMBERS[shape]
    if operation == "apply":
        return f"{STEM}{Path(members[-1]).suffix}"
    return members[0]


def _block(root: Path, operation: str, shape: str) -> Path:
    blocker = root / _blocker_name(operation, shape)
    blocker.write_bytes(BLOCKER_BYTES)
    return blocker


# --- what "no file lost, no file overwritten, hashes unchanged" means ---------------


def _assert_bytes_survived(root: Path, before: dict[str, str]) -> dict[str, str]:
    """Spec 9.2's first three assertions, kept apart because they fail differently."""
    after = _digests(root)
    # 1. No file lost: every digest that was on disk is still on disk.
    assert set(before) <= set(after), (
        f"content disappeared: {[before[d] for d in set(before) - set(after)]}"
    )
    # 2. No file overwritten: the count is stable, so nothing arrived on top of
    #    anything. (A rename that clobbered a file would lose one entry AND keep the
    #    count of the survivor, which assertion 1 alone would not report as a loss if
    #    the two shared content - hence _digests' uniqueness assertion.)
    assert len(after) == len(before), (
        f"the file count changed: {sorted(before.values())} -> {sorted(after.values())}"
    )
    # 3. Hashes unchanged: every surviving file is byte-identical. pam renames; it
    #    never rewrites a byte (spec 7.0 J8, vision principle 1).
    assert hashes(root) == sorted(before), "a file's bytes changed"
    return after


# --- recovery, convergence and the on-disk unit check -------------------------------


def _snapshot(root, plan_id):
    """Everything a recovery pass could change: disk, journal, items, plan, files."""
    conn = catalog(root)
    try:
        return (
            archive_names(root),
            {
                state: journal_pairs(conn, state)
                for state in ("intent", "done", "failed", "reverted")
            },
            item_statuses(conn, plan_id),
            repo.plan_status(conn, plan_id),
            {row.rel_path: row.status for row in repo.all_files(conn).values()},
        )
    finally:
        conn.close()


def _recover_now(root):
    """One recovery pass against a real workspace, as every command runs it."""
    conn = catalog(root)
    try:
        return recover(conn, root, LOGGER)
    finally:
        conn.close()


def _assert_recovery_converges(root, plan_id):
    """Spec 7.0 J7: no row left in `intent`, and ten passes leave what one pass left."""
    _recover_now(root)
    after_first = _snapshot(root, plan_id)
    for _ in range(9):
        _recover_now(root)
    assert _snapshot(root, plan_id) == after_first, "recovery is not idempotent"
    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "intent") == [], "J7: an `intent` row survived"
        assert repo.split_units(conn) == [], "spec 7.0 J6: a unit was left split"
        assert repo.plan_status(conn, plan_id) != "applying", "J10: a plan stayed `applying`"
    finally:
        conn.close()


def _assert_the_group_is_not_split(shape, original, current):
    """Vision S3 read off the DISK, not the catalog.

    The J6 tripwire in conftest asserts unit atomicity from journal rows; this asserts
    it from the filenames a human would see in their own folder. Two witnesses, because
    a bug that wrote consistent journal rows for an inconsistent archive would satisfy
    the first one entirely.

    `original` is the digest -> name map from BEFORE anything was renamed or blocked,
    so the members are identified by content: a blocker sharing an extension with a
    member (which `_blocker_name` deliberately produces) cannot be mistaken for one,
    and neither can the fake exiftool's `.tags.json` files, which pam never renames.
    """
    if shape != "unit":
        return
    stems = {
        Path(current[digest]).stem
        for digest, name in original.items()
        if not name.endswith(".tags.json")
    }
    assert len(stems) == 1, f"the companion group is split across {sorted(stems)}"


# --- the child process (spec 9.3: recovery runs on the NEXT invocation) -------------

MARKER = "PAM_FAULT_MATRIX "

_CHILD = r"""
import json, sys
from pathlib import Path

from pam import faults

command, root, arg = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
outcome, detail = "completed", ""
try:
    if command == "apply":
        from pam.execute.apply import run_apply

        run_apply(int(arg), root, confirm=None)
    elif command == "undo":
        from pam.execute.undo import run_undo

        run_undo(arg, root)
    elif command == "recover":
        import logging

        from pam.catalog.db import connect, migrate
        from pam.execute.recover import recover
        from pam.paths import workspace_if_exists

        ws = workspace_if_exists(root)
        conn = connect(ws.db_path)
        try:
            migrate(conn)
            recover(conn, root, logging.getLogger("pam.matrix.child"))
        finally:
            conn.close()
    else:
        outcome = "unknown-command"
except SystemExit as exc:
    outcome, detail = "SystemExit", str(exc)
except BaseException as exc:
    outcome, detail = type(exc).__name__, str(exc)
print(
    "PAM_FAULT_MATRIX "
    + json.dumps({"outcome": outcome, "detail": detail, "hits": faults._counts})
)
"""


def _child(set_fault, command: str, root: Path, arg, spec: str) -> dict:
    """Run ONE pam command in a fresh interpreter with `spec` armed. Never arms this one.

    Returns the child's own report: what the command ended with and which checkpoints
    `faults._counts` recorded. That report is the only thing that can prove a crash
    happened where the cell says it did - spec 13 already gives exit 1 to "recorded
    per-file errors and completed", so an exit code cannot tell an injected crash from
    an ordinary partial failure.
    """
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, command, str(root), str(arg)],
        env=set_fault.fault_env(spec),
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    reports = [line for line in proc.stdout.splitlines() if line.startswith(MARKER)]
    assert len(reports) == 1, (
        f"the child did not report exactly once\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    report: dict = json.loads(reports[0][len(MARKER) :])
    return report


def _assert_crashed_at(report: dict, checkpoint: str, hit: int = 1) -> None:
    """Three independent witnesses that the checkpoint fired, all from the child.

    The message is `faults.maybe_fault`'s OWN wording, so a cell cannot pass unless
    production code raised at that checkpoint; and `_counts` is only bumped for the
    armed checkpoint, so the hit map is exactly one entry when a crash landed.
    """
    assert report["outcome"] == "SystemExit", report
    assert report["detail"] == f"PAM_FAULT: crashed at {checkpoint} hit {hit}", report
    assert report["hits"] == {checkpoint: hit}, report


# --- the matrix ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("checkpoint", "operation", "shape", "cannot_fire"), CELLS, ids=CELL_IDS
)
def test_the_fault_matrix(tmp_path, set_fault, checkpoint, operation, shape, cannot_fire):
    """Spec 9.2 priority 2, one cell. See the module docstring for the two modes."""
    reach = REACH[checkpoint]
    spec = f"crash:{checkpoint}:1"
    root, plan_id = _archive(tmp_path, shape)
    original = _digests(root)

    session_id = None
    if operation == "undo":
        applied = run_apply(plan_id, root)
        assert (applied.renamed, applied.failed) == (len(MEMBERS[shape]), 0), applied.failures
        session_id = applied.session_id

    blocker = _block(root, operation, shape) if reach.forces_failure else None
    before = _digests(root)

    if cannot_fire:
        summary = _run_in_process(set_fault, spec, operation, root, plan_id, session_id)
        assert set_fault.hits(checkpoint) == 0, (
            f"{checkpoint} fired in a cell the table says it cannot reach - {cannot_fire}"
        )
        _assert_the_command_ran(summary, operation, shape)
        set_fault.next_invocation()
    else:
        report = _child(
            set_fault,
            operation,
            root,
            plan_id if operation == "apply" else session_id,
            spec,
        )
        _assert_crashed_at(report, checkpoint)

    _assert_recovery_converges(root, plan_id)
    current = _assert_bytes_survived(root, before)
    _assert_the_group_is_not_split(shape, original, current)

    if operation == "undo":
        # Spec 9.2's fifth assertion. The blocker (when there was one) is removed
        # first, because it is the manufactured interference - what is under test is
        # that once the way is clear, one more `pam undo` puts every file back under
        # its exact original name, byte for byte.
        if blocker is not None:
            blocker.unlink()
        assert session_id is not None
        run_undo(session_id, root)
        assert _digests(root) == original, "undo did not restore the exact names"

    assert_verify_agrees(root, where=_cell_id((checkpoint, operation, shape, cannot_fire)))


def _run_in_process(set_fault, spec, operation, root, plan_id, session_id):
    """A declared-skip cell: the same scenario, armed, expected to run to completion.

    In process deliberately - the crash cannot happen, so there is nothing a fresh
    interpreter would add, and running it here is what lets `set_fault.hits()` prove
    the checkpoint was never reached.

    Returns the command's summary so the caller can prove it actually ran; see
    `_assert_the_command_ran` for why that is not optional.
    """
    set_fault.arm(spec)
    if operation == "apply":
        return run_apply(plan_id, root)
    return run_undo(session_id, root)


def _assert_the_command_ran(summary, operation: str, shape: str) -> None:
    """A declared skip must prove the command RAN, not only that the checkpoint did not.

    `hits(checkpoint) == 0` is trivially true of a command that never executed, and so
    is every other assertion in the cell: an archive nothing touched keeps its bytes,
    converges under recovery and has an unsplit group. So the one thing separating
    "this checkpoint cannot fire in this shape" from "nothing happened here" has to be
    asserted directly - and it was not. Replacing the whole in-process run with a bare
    `set_fault.arm(spec)` left all ELEVEN declared skips green, which is precisely the
    vacuity the module docstring says the positive declaration removes.

    Every member ATTEMPTED, rather than every member moved: `mid_unit_revert`'s skip
    cells set `forces_failure`, so their single member is expected to fail against the
    blocker. `moved + failed` is the unit's size either way.
    """
    moved = summary.renamed if operation == "apply" else summary.restored
    assert moved + summary.failed == len(MEMBERS[shape]), (
        f"the {operation} did not run over the whole unit: {moved} moved,"
        f" {summary.failed} failed, {len(MEMBERS[shape])} members expected"
    )


@pytest.mark.parametrize("checkpoint", DBFAIL_CHECKPOINTS)
@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("shape", SHAPES)
def test_the_dbfail_matrix(tmp_path, set_fault, checkpoint, operation, shape):
    """Spec 9.3 rev 7's second mode: a storage failure, caught IN PROCESS.

    No subprocess, and that is the point of the mode: `sqlite3.OperationalError`
    propagates through a live interpreter with the connection still open, so what it
    tests is that nothing is left half-committed for the next transaction to sweep in
    (`journal_ops.py`'s module docstring). The same five assertions still apply.
    """
    root, plan_id = _archive(tmp_path, shape)
    original = _digests(root)

    session_id = None
    if operation == "undo":
        applied = run_apply(plan_id, root)
        assert (applied.renamed, applied.failed) == (len(MEMBERS[shape]), 0), applied.failures
        session_id = applied.session_id

    before = _digests(root)
    with pytest.raises(sqlite3.OperationalError) as raised:
        _run_in_process(
            set_fault, f"dbfail:{checkpoint}:1", operation, root, plan_id, session_id
        )
    assert str(raised.value) == f"PAM_FAULT: simulated DB failure at {checkpoint} hit 1"
    assert set_fault.hits(checkpoint) == 1
    set_fault.next_invocation()

    _assert_recovery_converges(root, plan_id)
    current = _assert_bytes_survived(root, before)
    _assert_the_group_is_not_split(shape, original, current)

    if operation == "undo":
        assert session_id is not None
        run_undo(session_id, root)
        assert _digests(root) == original, "undo did not restore the exact names"

    assert_verify_agrees(root, where=f"dbfail:{checkpoint}/{operation}/{shape}")


# --- the table itself, pinned so it cannot drift from the code ----------------------


def test_the_table_covers_every_checkpoint():
    """`REACH` is keyed by `faults.CHECKPOINTS` and must stay that way.

    A checkpoint added in a later milestone and not added here would shrink the matrix
    silently - the parametrization is generated from `faults.CHECKPOINTS`, so the new
    cells would exist and immediately KeyError; keyed the other way round, a checkpoint
    DELETED from the tuple would leave a stale row nothing notices. Both directions are
    asserted.
    """
    assert set(REACH) == set(faults.RENAME_CHECKPOINTS)
    assert set(DBFAIL_CHECKPOINTS) <= set(faults.RENAME_CHECKPOINTS)
    # The split is a partition of the closed vocabulary: a rename checkpoint moved into the
    # migration tuple would leave the matrix with the suite green.
    assert set(faults.RENAME_CHECKPOINTS) | set(faults.MIGRATION_CHECKPOINTS) == set(
        faults.CHECKPOINTS
    )
    assert not set(faults.RENAME_CHECKPOINTS) & set(faults.MIGRATION_CHECKPOINTS)


def test_the_matrix_is_the_size_the_spec_asks_for():
    """7 checkpoints x 2 operations x 3 shapes = 42 cells, 34 of which can fire.

    Written down so that narrowing `REACH` - the one edit that could turn a live cell
    into a declared skip - fails here instead of quietly reducing what the milestone's
    shipping gate covers.

    It was 31/11 when Task 11 shipped. The M5 review gate gave `undo._finalize_plan`
    the `before_plan_finalize` checkpoint apply already had, which turned that
    checkpoint's three undo cells from declared skips into live ones. The remaining
    eight skips are all shape restrictions: `mid_companion_unit` and `mid_unit_revert`
    are unit-only, x 2 operations x 2 non-unit shapes.
    """
    cells = _cells()
    assert len(cells) == 42
    can_fire = [cell for cell in cells if not cell[3]]
    assert len(can_fire) == 34
    assert len(cells) - len(can_fire) == 8
    # And every declared skip carries a reason a human can read.
    for _checkpoint, _operation, _shape, cannot_fire in cells:
        if cannot_fire:
            assert len(cannot_fire) > 20, cannot_fire


def test_the_disk_side_split_check_notices_a_group_under_two_stems():
    """`_assert_the_group_is_not_split` is a SECOND witness to vision S3, and it is
    never the one that fires.

    The catalog-side witness (spec 7.0 J6, via `repo.split_units` and conftest's
    suite-wide tripwire) reaches every state this module can crash into first, so
    deleting the disk-side check leaves the whole matrix green - it was written for
    the state pam cannot produce and something outside pam can (spec 7.0's closing
    paragraph): a consistent journal above a split archive. A helper cannot pin
    itself, so that state is fabricated here rather than crashed into.
    """
    original = {"a": "IMG_1.JPG", "b": "IMG_1.MOV", "c": "IMG_1.JPG.tags.json"}
    whole = {
        "a": f"{STEM}.JPG",
        "b": f"{STEM}.MOV",
        # pam never renames the fake exiftool's sidecars, and their stem
        # (`IMG_1.JPG.tags`) matches no member's - so counting them would make the
        # check fire on every healthy archive.
        "c": "IMG_1.JPG.tags.json",
    }
    _assert_the_group_is_not_split("unit", original, whole)
    _assert_the_group_is_not_split("unit", original, original)

    with pytest.raises(AssertionError, match="split across"):
        _assert_the_group_is_not_split("unit", original, {**whole, "b": "IMG_1.MOV"})


# The two helper pins that used to sit here - `device_mov_bytes` and `digests` - moved
# to `tests/test_round_trip_property.py` at M6 Task 9. The helpers themselves moved to
# `tests/archives.py` at M6 Task 1, and the fast CI lane runs everything EXCEPT this
# module, so pins left here would cover helpers that lane exercises but never checks.


def test_every_checkpoint_the_table_says_fires_is_reached_by_some_cell():
    """The table's positive half, from the other direction: no checkpoint is
    all-skips. A `Reach` narrowed to an empty shape tuple would satisfy every other
    assertion here while removing that checkpoint from the gate entirely."""
    reached = {cell[0] for cell in _cells() if not cell[3]}
    assert reached == set(faults.RENAME_CHECKPOINTS)


# --- the states spec revs 8/9 created, each owed its own scenario -------------------


def test_a_plan_left_applying_is_finalized_the_same_way_by_apply_and_by_recovery(
    tmp_path, set_fault
):
    """Spec 7.2 phase 3 / spec 7.0 J10, driven end to end rather than at the derivation.

    Two IDENTICAL archives. One is applied to completion, so `apply._finalize_plan`
    writes the terminal status; the other crashes at `before_plan_finalize` in a child
    process, leaving the plan `applying`, and recovery's phase 3 writes it. They must
    agree - that is the whole reason `derive_plan_status` lives in `recover.py` and
    `apply.py` imports it. `test_recover.py` pins the derivation directly; this pins
    that both call sites actually use it.

    Both outcomes are exercised: a clean plan (`applied`) and one whose only item is
    refused by an occupied target (`partially_applied`). One value alone would pass
    against a call site that returned a constant.
    """
    for blocked, expected in ((False, "applied"), (True, "partially_applied")):
        by_apply = _finalized_by_apply(tmp_path / f"by-apply-{blocked}", blocked)
        by_recovery = _finalized_by_recovery(
            tmp_path / f"by-recovery-{blocked}", blocked, set_fault
        )
        assert by_apply == by_recovery == expected


def _plan_status(root, plan_id):
    conn = catalog(root)
    try:
        return repo.plan_status(conn, plan_id)
    finally:
        conn.close()


def _finalized_by_apply(tmp_path, blocked):
    tmp_path.mkdir()
    root, plan_id = _archive(tmp_path, "single")
    if blocked:
        _block(root, "apply", "single")
    run_apply(plan_id, root)
    assert_verify_agrees(root, where=f"finalized by apply, blocked={blocked}")
    return _plan_status(root, plan_id)


def _finalized_by_recovery(tmp_path, blocked, set_fault):
    tmp_path.mkdir()
    root, plan_id = _archive(tmp_path, "single")
    if blocked:
        _block(root, "apply", "single")
    report = _child(set_fault, "apply", root, plan_id, "crash:before_plan_finalize:1")
    _assert_crashed_at(report, "before_plan_finalize")
    # The state rev 8 created and J10 forbids leaving behind - manufactured here so the
    # comparison below is between two REAL finalizations, not two calls to one function.
    assert _plan_status(root, plan_id) == "applying"
    _recover_now(root)
    # `partially_applied` with zero applied and zero undone items is a HEALTHY outcome,
    # not check 4d's stale status - the plan really is partly applied and a `pam undo`
    # of it would put nothing back. This is the false alarm check 4d's extra `undone`
    # clause exists to stop, driven here through a real crash rather than argued.
    assert_verify_agrees(root, where=f"finalized by recovery, blocked={blocked}")
    return _plan_status(root, plan_id)


def test_a_crash_before_undos_finalize_leaves_a_plan_a_second_undo_finishes(
    tmp_path, set_fault
):
    """Spec 7.4 rev 10's fourth point, the undo twin of the test above - and NOT its
    mirror image, which is the reason it is written down.

    Apply's `before_plan_finalize` leaves the plan `applying` and recovery's phase 3
    finishes it. Undo's leaves the plan `applied` while every item reads `undone` and
    every file is back at its original name, and recovery does **nothing at all**: J10
    confines spec 7.2 phase 3 to `applying`, which undo never sets. That is deliberate -
    letting recovery rewrite a plan in any other status is a far larger hole than the
    one it would close - so the route out is a second `pam undo` of the same session,
    which finds every item already `undone`, renames nothing, and writes the status.

    Both halves are asserted. Without the "recovery is inert" half, a future change
    that quietly widened phase 3 past `applying` would pass here.
    """
    root, plan_id = _archive(tmp_path, "unit")
    original = _digests(root)
    applied = run_apply(plan_id, root)
    assert (applied.renamed, applied.failed) == (len(UNIT_MEMBERS), 0), applied.failures

    report = _child(set_fault, "undo", root, applied.session_id, "crash:before_plan_finalize:1")
    _assert_crashed_at(report, "before_plan_finalize")

    conn = catalog(root)
    try:
        assert _digests(root) == original, "every file is back at its original name"
        assert set(item_statuses(conn, plan_id).values()) == {"undone"}
        assert repo.plan_status(conn, plan_id) == "applied", (
            "the plan status is the one thing the crash left behind"
        )
    finally:
        conn.close()

    # Recovery is inert here, by design (J10). Ten passes, so this also says the
    # inertness is not an accident of running it once.
    for _ in range(10):
        summary = _recover_now(root)
    assert not summary.did_anything
    assert _plan_status(root, plan_id) == "applied"

    # SPEC 7.5 CHECK 4d IS THE OTHER HALF OF "RECOVERY IS INERT HERE". Recovery
    # deliberately will not touch this plan, so the route out has to be something the
    # user is TOLD about - and `pam verify` is what tells them. A clean bill at this
    # point would mean pam had parked a stale status where nothing would ever mention
    # it. The finding names the second `pam undo` as its fix.
    (stale,) = assert_verify_agrees(root, expect=("4d",), where="crashed before undo finalize")
    assert stale.fix == f"pam undo {applied.session_id}"

    # The route out: one more `pam undo` of the SAME session.
    second = run_undo(applied.session_id, root)
    assert (second.restored, second.failed) == (0, 0), second.failures
    assert len(second.already_undone) == len(UNIT_MEMBERS)
    assert _plan_status(root, plan_id) == "undone"
    assert _digests(root) == original, "the second undo renamed nothing"

    # And the finding is GONE, which is what makes the fix line above true rather than
    # merely present. A checker whose remedy leaves the identical report is the check 7
    # dead end (M6 Task 3), and this is the assertion that would have caught it.
    assert_verify_agrees(root, where="after the second undo finished the plan")


def test_a_stranded_member_is_reported_never_healed_and_ten_runs_equal_one(
    tmp_path, set_fault, allow_split_unit
):
    """Spec 7.0 J4 across a process boundary, and J7 where it is hardest.

    The crash lands inside spec 7.3's revert loop, so a member is sitting under its
    new name with its group under the old one; then something outside pam takes the
    old name back, so the repair CANNOT succeed. pam's answer is to say exactly which
    file it left where (spec 7.3: never guess), leave the row `done` because that is
    the truth, and be completely inert on every pass after the first.

    The sanction on the J6 tripwire is what this test is: pam cannot produce a split
    unit, the world can, and a test proving pam reports rather than repairs it has to
    be able to reach that state.
    """
    root, plan_id = _archive(tmp_path, "unit")
    _block(root, "apply", "unit")
    before = _digests(root)
    report = _child(set_fault, "apply", root, plan_id, "crash:mid_unit_revert:1")
    _assert_crashed_at(report, "mid_unit_revert")

    # The interference: the old name is taken again before recovery can put it back.
    (root / "IMG_1.JPG").write_bytes(b"something else entirely")

    first = _recover_now(root)
    after_first = _snapshot(root, plan_id)
    for _ in range(9):
        last = _recover_now(root)

    assert first.stranded == [f"{STEM}.JPG"]
    assert last.stranded == first.stranded
    assert last.reverted == []
    assert _snapshot(root, plan_id) == after_first, "the repair was retried, or drifted"
    # Nothing pam wrote was lost; the interloping file is the only new content.
    after = _digests(root)
    assert set(before) <= set(after)
    conn = catalog(root)
    try:
        # Left `done`, because it is: pam DID perform that rename (spec 7.0 J4).
        assert journal_pairs(conn, "done") == [("IMG_1.JPG", f"{STEM}.JPG")]
        assert repo.rows_in_state(conn, "intent") == []
    finally:
        conn.close()

    # SPEC 7.5's ONE EXPLICIT "REPORTED FOR HUMAN ATTENTION" STATE (check 4e / J6), and
    # the only scenario in this module that ends in one. `pam verify` must REPORT the
    # split unit, not be silent about it: this whole test is about pam declining to
    # guess, and a checker that said the archive was fine would make that silence.
    (split,) = assert_verify_agrees(root, expect=("4e",), where="a stranded member")
    assert f"{STEM}.JPG" not in split.details[0], "the detail names the unit, not a member"
    assert "could not put them back safely" in split.details[0]


def test_recovery_against_an_unreachable_archive_changes_nothing(tmp_path, set_fault):
    """Spec 7.0 J9, reached by a real crash in a real child process.

    `intent -> failed` is terminal (J2), so a decision made about a disk pam cannot
    see can never be taken back - and every `intent` row would be resolved `failed`
    for renames that may well have completed on that drive. Recovery must therefore
    do NOTHING AT ALL: no row resolved, no plan finalized, and it must say so rather
    than pass silently. Then the archive comes back and the same rows recover
    normally, which is what proves the first half was a refusal and not a no-op.
    """
    root, plan_id = _archive(tmp_path, "unit")
    report = _child(set_fault, "apply", root, plan_id, "crash:after_intent:1")
    _assert_crashed_at(report, "after_intent")
    before = _snapshot(root, plan_id)

    conn = catalog(root)
    try:
        summary = recover(conn, root.parent / "not-a-folder", LOGGER)
    finally:
        conn.close()

    assert summary.archive_unreachable
    assert summary.did_anything, "J9 must be reported, never passed over in silence"
    assert (summary.failed, summary.adopted, summary.reverted, summary.finalized) == (
        [],
        [],
        [],
        [],
    )
    assert _snapshot(root, plan_id) == before

    # J9's REFUSAL, SEEN FROM THE CHECKER'S SIDE - and the one place in this module
    # where `pam verify` reports something the scenario had not written down.
    #
    # Neither of these checks has a fire path through the `pam verify` COMMAND:
    # `paths.resolve_archive` refuses an unreachable archive at the front door, so the
    # command never reaches recovery, let alone the checks (corrected at the M6 Task 3
    # spec review). Driven at the function level, against a workspace whose archive is
    # gone, BOTH fire - and both are true. Recovery declined to resolve the three
    # `intent` rows (J7, check 4a) *and* declined to finalize the plan it was applying
    # (J10, check 4b), because J9 makes it decline everything: a decision about a disk
    # pam cannot see is irreversible (J2) and must not be taken.
    #
    # 4b was NOT in the first version of this expectation. The checker reported it, the
    # scenario had only predicted 4a, and per the Step 2 rule the checker's report is
    # the truth - so the cell says so. It is the correct pairing: leaving a plan
    # `applying` is precisely what a refused recovery does.
    assert_verify_agrees(
        root, expect=("4a", "4b"), where="recovery refused an unreachable archive"
    )

    _assert_recovery_converges(root, plan_id)
    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "intent") == []
    finally:
        conn.close()
    # The archive came back and the same rows recovered normally, so the refusal above
    # was a refusal and not damage.
    assert_verify_agrees(root, where="the archive came back")


def test_a_crash_inside_recovery_itself_is_recovered_by_the_next_invocation(
    tmp_path, set_fault
):
    """Spec 9.3 rev 8's last sentence: `mid_unit_revert` also arms a crash INSIDE
    spec 7.2, because phase 2 shares `unit.revert_unit` with apply and undo.

    Three invocations, each its own interpreter for the first two:
      1. apply crashes at the top of spec 7.3's revert loop -> two `done` members, one
         `failed`, and ZERO `intent` rows, so only phase 2 can heal it.
      2. recovery itself crashes at the SECOND revert -> the newest member is back and
         resolved `reverted`, the other is still `done`. A half-finished repair, which
         is the one state that would be unreachable if the checkpoint were not shared.
      3. the next pass finishes it, and ten more change nothing (J7).
    """
    root, plan_id = _archive(tmp_path, "unit")
    original = _digests(root)
    _block(root, "apply", "unit")
    before = _digests(root)

    crashed_apply = _child(set_fault, "apply", root, plan_id, "crash:mid_unit_revert:1")
    _assert_crashed_at(crashed_apply, "mid_unit_revert")
    conn = catalog(root)
    try:
        assert journal_pairs(conn, "done") == [
            ("IMG_1.JPG", f"{STEM}.JPG"),
            ("IMG_1.MOV", f"{STEM}.MOV"),
        ]
        assert repo.rows_in_state(conn, "intent") == []
    finally:
        conn.close()

    crashed_recovery = _child(set_fault, "recover", root, "-", "crash:mid_unit_revert:2")
    _assert_crashed_at(crashed_recovery, "mid_unit_revert", hit=2)
    conn = catalog(root)
    try:
        assert journal_pairs(conn, "reverted") == [("IMG_1.MOV", f"{STEM}.MOV")]
        assert journal_pairs(conn, "done") == [("IMG_1.JPG", f"{STEM}.JPG")]
    finally:
        conn.close()

    _assert_recovery_converges(root, plan_id)

    current = _assert_bytes_survived(root, before)
    _assert_the_group_is_not_split("unit", original, current)
    assert archive_names(root) == [f"{STEM}.XMP", "IMG_1.JPG", "IMG_1.MOV", "IMG_1.XMP"]
    conn = catalog(root)
    try:
        assert repo.rows_in_state(conn, "done") == []
        assert repo.split_units(conn) == []
    finally:
        conn.close()
    assert_verify_agrees(root, where="a crash inside recovery, finished by the next pass")


# --- the two real-archive shapes a real-world library is made of -----------------


def test_a_live_photo_pair_survives_a_crash_between_its_members(tmp_path, set_fault):
    """Live-photo groups, most of a phone library: the shape most likely to break, and the one
    the owner has most of. `.HEIC` + `.MOV`, primary the `.HEIC` (spec 5.4 rev 6).

    The crash lands between the two members, so one is renamed and resolved while the
    other's intent row is still open - and recovery must put the first one BACK rather
    than complete the second, because apply already gave up on this unit.
    """
    root = tmp_path / "photos"
    root.mkdir()
    put(root, "IMG_7.HEIC")
    put(root, "IMG_7.MOV", content=_device_mov_bytes())
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    assert plan_id is not None
    approve_archive(root, plan_id)  # F1: approved in the PARENT, before any child applies
    original = _digests(root)

    report = _child(set_fault, "apply", root, plan_id, "crash:mid_companion_unit:1")
    _assert_crashed_at(report, "mid_companion_unit")
    assert archive_names(root) == [f"{STEM}.HEIC", "IMG_7.MOV"]

    _assert_recovery_converges(root, plan_id)

    assert _digests(root) == original, "the pair did not come back whole"
    assert archive_names(root) == ["IMG_7.HEIC", "IMG_7.MOV"]
    assert_verify_agrees(root, where="a live_photo pair crashed between its members")


def test_an_aae_and_mov_unit_survives_a_crash_between_its_members(tmp_path, set_fault):
    """Five such units exist in a real-world library, and M5 Task 1 changed how they
    are named: a sidecar is never the primary (spec 5.4 rev 6), so the `.MOV`'s own
    date names the unit instead of the pair becoming `undated_001.*`.

    Asserted here as well as in the naming tests, because the matrix is where the
    unit is journalled, crashed and put back - and a unit whose primary election
    regressed would rename to a different stem and still pass every assertion about
    bytes.
    """
    root = tmp_path / "photos"
    root.mkdir()
    put(root, "IMG_8.MOV", content=_device_mov_bytes())
    put(root, "IMG_8.AAE")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    assert plan_id is not None
    approve_archive(root, plan_id)  # F1: approved in the PARENT, before any child applies
    original = _digests(root)

    report = _child(set_fault, "apply", root, plan_id, "crash:mid_companion_unit:1")
    _assert_crashed_at(report, "mid_companion_unit")
    # The .MOV's date named the unit; the sidecar followed it. `undated_001.*` here
    # would be M4 finding 6 back again.
    assert archive_names(root) == [f"{STEM}.AAE", "IMG_8.MOV"]

    _assert_recovery_converges(root, plan_id)

    assert _digests(root) == original
    assert archive_names(root) == ["IMG_8.AAE", "IMG_8.MOV"]
    assert_verify_agrees(root, where="an .AAE + .MOV unit crashed between its members")


@pytest.mark.skipif(shutil.which("exiftool") is None, reason="needs the real exiftool on PATH")
def test_a_standalone_video_survives_a_crash_under_real_exiftool(tmp_path, set_fault):
    """The rev 3 shipping gate's extraction half, once.

    The parametrized video row above uses the fake exiftool DELIBERATELY: spec 9.2's
    "standalone video" is about the RENAMING path, and a shipping gate that skips
    itself on a machine without exiftool is not a gate. The bytes it renames are still
    a real device-shaped QuickTime file, so nothing about the file being a video is
    faked - only the tag extraction is.

    This cell closes the remaining gap. Real exiftool reads the `moov`-last GoPro
    shape, which yields `qt_utc` and therefore a MEDIUM-tier plan item (spec 6.4) -
    a different path through the planner from every other cell in this module - and
    that item is then crashed, recovered and undone.
    """
    root = tmp_path / "photos"
    root.mkdir()
    clip = write_samples(root)["gopro"]
    for stray in root.iterdir():
        if stray != clip:
            stray.unlink()
    run_scan(root)
    plan_id = run_plan(root).plan_id
    assert plan_id is not None
    approve_archive(root, plan_id)  # F1: approved in the PARENT, before any child applies
    original = _digests(root)
    conn = catalog(root)
    try:
        (item,) = repo.plan_items(conn, plan_id)
        assert item.tier == "medium", "the qt_utc cap (spec 6.4) is what makes this row differ"
    finally:
        conn.close()

    report = _child(set_fault, "apply", root, plan_id, "crash:after_rename:1")
    _assert_crashed_at(report, "after_rename")

    _assert_recovery_converges(root, plan_id)
    current = _assert_bytes_survived(root, original)
    conn = catalog(root)
    try:
        # The rename happened before the crash, so recovery adopts it (spec 7.2).
        (session_id,) = {row.session_id for row in repo.rows_in_state(conn, "done")}
    finally:
        conn.close()
    assert current != original, "nothing was renamed, so the cell proves nothing"

    run_undo(session_id, root)
    assert _digests(root) == original, "undo did not restore the exact name"
    assert_verify_agrees(root, where="a standalone video under real exiftool")
