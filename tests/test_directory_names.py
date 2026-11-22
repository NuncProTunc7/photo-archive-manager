"""M14 Task 0E (X4): renaming one flat folder in linear time, and the property that makes it safe.

`rename.DirectoryNames` answers spec 6.3.2's question - "is this name taken, ignoring case?" -
from ONE listing of the folder per run, kept current with pam's own renames, with a "taken"
answer re-listed before refusing and a "free" answer confirmed by `os.lstat` immediately
before `os.rename` (the X4 amendment, section 3).

THE ORACLE IS INDEPENDENT. Every answer is compared with `fresh_listing_says_taken` below,
written here with `os.listdir` and its own lowering - never `rename._name_in_use`, which is
the definition being replaced (CLAUDE.md: an oracle built from the definition it checks is a
survivor waiting to happen). Every case runs on a real temp folder.
"""

import errno
import logging
import os
import random
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from archives import (
    FAKE_COMMAND,
    approve_and_apply,
    archive_names,
    assert_verify_agrees,
    catalog,
    hashes,
    put,
)

from pam.catalog import repo
from pam.execute.recover import recover
from pam.execute.rename import (
    CODE_CASE_ONLY,
    CODE_RENAME_FAILED,
    CODE_SOURCE_UNREADABLE,
    CODE_TARGET_OCCUPIED,
    DirectoryNames,
    perform_verified_rename,
)
from pam.execute.undo import run_undo
from pam.naming.planner import run_plan
from pam.scan.runner import run_scan

LOG = logging.getLogger("pam.tests.directory_names")


def fresh_listing_says_taken(directory: Path, name: str) -> bool:
    """The independent oracle: list the folder NOW and compare lowered names."""
    wanted = name.lower()
    return any(entry.lower() == wanted for entry in os.listdir(directory))


# --- the generated cases (Step 1) ---------------------------------------------------------

# The pool every case draws from. Mixed case, `_` and `%` (the two characters a LIKE pattern
# would treat as wildcards), and a stem that differs from another only in case.
POOL = (
    "IMG_0001.JPG",
    "img_0002.jpg",
    "Img_0003.Jpg",
    "IMG%0004.JPG",
    "100%_done.png",
    "a_b.JPG",
    "a%b.JPG",
    "DSC_3579.NEF",
    "dsc_3580.nef",
    "2019-07-04_001.JPG",
    "2019-07-04_002.jpg",
    "Holiday.HEIC",
)
TARGETS = (
    "2019-07-04_001.JPG",
    "2019-07-04_002.JPG",
    "2019-07-04_003.jpg",
    "2020-08-05_001.HEIC",
    "x_1%.JPG",
    "tmp_swap.JPG",
)


@dataclass
class Case:
    """One folder's starting contents and the renames run against it, in order."""

    kind: str
    contents: list[str]
    steps: list[tuple[str, str]] = field(default_factory=list)


def _unique_ci(names: list[str]) -> list[str]:
    """Drop later names equal to an earlier one ignoring case: a folder NTFS can hold."""
    seen: set[str] = set()
    out = []
    for name in names:
        if name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def _variant(rng: random.Random, name: str) -> str:
    """A case variant of `name` that is not `name` itself (a case-only pair)."""
    flipped = name.swapcase()
    return flipped if flipped != name else name.upper() + "X"


def _generate(seed: int = 7) -> list[Case]:
    rng = random.Random(seed)
    cases: list[Case] = []
    for _ in range(12):
        # CHAINS: a -> t1, t1 -> t2, ... - pam's own new name must be seen as taken, and the
        # name it left as free.
        contents = _unique_ci(rng.sample(POOL, 6))
        start = contents[0]
        chain = [start] + rng.sample(
            [t for t in TARGETS if t.lower() not in {c.lower() for c in contents}], 3
        )
        steps = list(zip(chain, chain[1:], strict=False))
        # ...and a later rename back onto the name the chain freed.
        steps.append((contents[1], start))
        cases.append(Case("chain", contents, steps))
    for _ in range(12):
        # SWAPS: a -> tmp, b -> a, tmp -> b.
        contents = _unique_ci(rng.sample(POOL, 5))
        a, b = contents[0], contents[1]
        tmp = "tmp_swap.JPG"
        cases.append(Case("swap", contents, [(a, tmp), (b, a), (tmp, b)]))
    for _ in range(12):
        # REVERSE-ORDER UNDO: forward renames, then each put back newest first.
        contents = _unique_ci(rng.sample(POOL, 6))
        free = [t for t in TARGETS if t.lower() not in {c.lower() for c in contents}]
        forward = list(zip(contents[:3], free[:3], strict=True))
        backward = [(new, old) for old, new in reversed(forward)]
        cases.append(Case("undo", contents, forward + backward))
    for _ in range(12):
        # CASE-ONLY PAIRS and refusals: renames onto a CASE VARIANT of a name already there
        # (must be refused as taken), onto an exact taken name, case-only renames of a file
        # itself (refused by check 1), and a free one between them.
        contents = _unique_ci(rng.sample(POOL, 6))
        free = [t for t in TARGETS if t.lower() not in {c.lower() for c in contents}]
        steps = [
            (contents[0], _variant(rng, contents[1])),
            (contents[2], contents[3]),
            (contents[4], _variant(rng, contents[4])),
            (contents[0], free[0]),
            (contents[5], _variant(rng, free[0])),
            (contents[2], _variant(rng, contents[0])),
        ]
        cases.append(Case("case_pairs", contents, steps))
    return cases


CASES = _generate()
# At COLLECTION: an empty generator would make the parametrized property vanish as a skip.
assert CASES, "the X4 equivalence property generated no cases"
assert {c.kind for c in CASES} == {"chain", "swap", "undo", "case_pairs"}, CASES
assert any("%" in n for c in CASES for n in c.contents), "no case holds a `%` name"
assert any("_" in n for c in CASES for n in c.contents), "no case holds a `_` name"


def _lay_down(directory: Path, contents: list[str]) -> None:
    directory.mkdir(parents=True)
    for name in contents:
        (directory / name).write_bytes(f"the only copy of {name}".encode())


def _rename(root: Path, old: str, new: str, **kwargs):
    stat = os.stat(root / old) if (root / old).exists() else None
    size, mtime_ns = (stat.st_size, stat.st_mtime_ns) if stat else (1, 1)
    return perform_verified_rename(
        archive_root=root,
        old_rel=old,
        new_rel=new,
        expect_size=size,
        expect_mtime_ns=mtime_ns,
        logger=LOG,
        **kwargs,
    )


def _predicted(directory: Path, old: str, new: str) -> tuple[bool, str | None]:
    """What a rename must do, from the oracle alone (checks 1 and 4; the sources exist)."""
    if not (directory / old).exists():
        return (False, "source_unreadable")
    if old.lower() == new.lower() and old != new:
        return (False, CODE_CASE_ONLY)
    if fresh_listing_says_taken(directory, new):
        return (False, CODE_TARGET_OCCUPIED)
    return (True, None)


def _probes(directory: Path) -> set[str]:
    """Every name worth asking about: the pool, the targets, their case variants, the disk."""
    names = set(POOL) | set(TARGETS) | set(os.listdir(directory))
    return names | {n.swapcase() for n in names}


@pytest.mark.parametrize("case", CASES, ids=[f"{i}-{c.kind}" for i, c in enumerate(CASES)])
def test_memory_agrees_with_a_fresh_listing_at_every_step(tmp_path, case):
    """Step 1: at every step, the run's memory answers exactly what a fresh listing answers.

    The same renames run twice on two identical real folders: once as today (`names=None`,
    one listing per rename) and once with one `DirectoryNames` for the whole sequence. Both
    must reach the oracle's predicted outcome at every step, and after every step memory's
    OWN answer (`known`, which never touches the disk) must equal the oracle for every probe
    - including every case variant. `known` is read rather than `taken`, because `taken`
    re-lists on a "taken" answer and would repair a stale memory before it could be seen.
    """
    today = tmp_path / "today"
    memory = tmp_path / "memory"
    _lay_down(today, case.contents)
    _lay_down(memory, case.contents)
    names = DirectoryNames()
    listed = False
    for step, (old, new) in enumerate(case.steps):
        expected = _predicted(today, old, new)
        assert _predicted(memory, old, new) == expected, "the two folders drifted"
        without = _rename(today, old, new)
        with_names = _rename(memory, old, new, names=names)
        assert (without.ok, without.code) == expected, (step, old, new)
        assert (with_names.ok, with_names.code) == expected, (step, old, new)
        assert with_names.reason == without.reason, (step, old, new)
        # A step that reached check 4 listed the folder; from then on it stays remembered
        # (nothing in these sequences fails an os.rename or meets an lstat hit).
        listed = listed or expected[1] not in (CODE_CASE_ONLY, "source_unreadable")
        for probe in sorted(_probes(memory)):
            answer = names.known(memory, probe)
            if not listed:
                assert answer is None, (step, probe)
                continue
            assert answer is not None, f"step {step}: memory forgot the folder"
            assert answer == fresh_listing_says_taken(memory, probe), (step, old, new, probe)
    assert sorted(os.listdir(today)) == sorted(os.listdir(memory))


def test_the_generated_cases_reach_every_answer(tmp_path):
    """The property is only as good as its cases: they must reach a rename that happens, a
    refusal for an occupied name (exact AND case variant) and a case-only refusal."""
    seen = set()
    variant_refusals = 0
    for index, case in enumerate(CASES):
        folder = tmp_path / str(index)
        _lay_down(folder, case.contents)
        for old, new in case.steps:
            on_disk = set(os.listdir(folder))
            outcome = _rename(folder, old, new)
            seen.add(outcome.code)
            if outcome.code == CODE_TARGET_OCCUPIED and new not in on_disk:
                variant_refusals += 1
    assert {None, CODE_TARGET_OCCUPIED, CODE_CASE_ONLY} <= seen, seen
    assert variant_refusals > 0, "no case refused a CASE VARIANT of a name already there"


# --- one listing per folder per run (Step 3) ---------------------------------------------


@pytest.fixture()
def listings(monkeypatch):
    """Every `os.scandir` made FROM `pam/execute/rename.py`, by folder. Other modules' listings
    (a scan, a validator) are not the occupancy check and are not counted."""
    seen: list[Path] = []
    real = os.scandir

    def spy(path="."):
        if sys._getframe(1).f_globals.get("__name__") == "pam.execute.rename":
            seen.append(Path(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", spy)
    return seen


FLAT = 50  # >= 50 photos in ONE folder (the brief); each also has its fake-exiftool sidecar
UNIT = ("IMG_X.JPG", "IMG_X.MOV", "IMG_X.XMP")  # renamed in this order (test_recover)


def _flat_archive(tmp_path, *, unit=False):
    root = tmp_path / "photos"
    root.mkdir()
    for i in range(FLAT):
        put(root, f"IMG_{i:04d}.JPG")
    if unit:
        for name in UNIT:
            put(root, name, date="2021:09:06 11:02:03")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    assert plan_id is not None
    return root, plan_id


def _touch(path: Path) -> None:
    """Change the mtime only (same bytes): the file now 'changed on disk' for pam."""
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def test_without_a_runs_memory_every_rename_lists_the_folder(tmp_path, listings):
    """The spy's own control: `names=None` is today's behaviour, one listing PER RENAME, and
    the spy sees every one of them. Without this, a blind spy would pass every count below."""
    folder = tmp_path / "flat"
    _lay_down(folder, [f"IMG_{i:04d}.JPG" for i in range(FLAT)])
    for i in range(FLAT):
        assert _rename(folder, f"IMG_{i:04d}.JPG", f"2019-07-04_{i + 1:03d}.JPG").ok
    assert listings == [folder] * FLAT


def test_apply_lists_a_flat_folder_once_per_run(tmp_path, listings):
    root, plan_id = _flat_archive(tmp_path)
    listings.clear()
    summary = approve_and_apply(plan_id, root)
    assert (summary.renamed, summary.failed) == (FLAT, 0)
    assert listings == [root]


def test_undo_lists_a_flat_folder_once_per_run(tmp_path, listings):
    root, plan_id = _flat_archive(tmp_path)
    applied = approve_and_apply(plan_id, root)
    listings.clear()
    summary = run_undo(applied.session_id, root)
    assert (summary.restored, summary.failed) == (FLAT, 0)
    assert listings == [root]


def test_apply_puts_a_failed_unit_back_without_listing_again(tmp_path, listings):
    """`revert_unit` inside apply: the unit's third member changed on disk, so its first two
    are renamed and put back - through the run's memory, so still ONE listing."""
    root, plan_id = _flat_archive(tmp_path, unit=True)
    _touch(root / "IMG_X.XMP")
    listings.clear()
    summary = approve_and_apply(plan_id, root)
    assert (summary.renamed, summary.failed) == (FLAT, 3)
    log = summary.log_path.read_text(encoding="utf-8")
    assert "-> IMG_X.MOV" in log and "-> IMG_X.JPG" in log, "nothing was put back"
    assert set(UNIT) <= set(archive_names(root))
    assert listings == [root]


def test_undo_puts_a_failed_unit_back_without_listing_again(tmp_path, listings):
    """`revert_unit` inside undo: undo restores the unit newest first (.XMP, .MOV, .JPG); the
    .JPG changed on disk, so the other two are renamed forward again - still ONE listing."""
    root, plan_id = _flat_archive(tmp_path, unit=True)
    applied = approve_and_apply(plan_id, root)
    assert applied.failed == 0
    conn = catalog(root)
    try:
        jpg = next(
            row.new_rel_path
            for row in repo.session_rows(conn, applied.session_id)
            if row.old_rel_path == "IMG_X.JPG"
        )
    finally:
        conn.close()
    _touch(root / jpg)
    listings.clear()
    summary = run_undo(applied.session_id, root)
    assert (summary.restored, summary.failed) == (FLAT, 3)
    log = summary.log_path.read_text(encoding="utf-8")
    assert "renamed: IMG_X.MOV ->" in log and "renamed: IMG_X.XMP ->" in log
    assert listings == [root]


def test_recovery_puts_a_split_unit_back_listing_the_folder_once(tmp_path, listings, set_fault):
    """Recovery's revert: a crash between the unit's 2nd and 3rd members leaves two `done`;
    phase 2 puts both back through ITS OWN memory - one listing, not one per member."""
    root, plan_id = _flat_archive(tmp_path, unit=True)
    set_fault.arm("crash:mid_companion_unit:2")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()
    listings.clear()
    conn = catalog(root)
    try:
        summary = recover(conn, root, LOG)
    finally:
        conn.close()
    assert summary.reverted == ["IMG_X.MOV", "IMG_X.JPG"]
    assert listings == [root]


# --- interference, watched at the dangerous call (Step 4) --------------------------------


@pytest.fixture()
def renames(monkeypatch):
    """Every `os.rename` actually called, recorded BEFORE it runs, then performed for real."""
    calls: list[tuple[str, str]] = []
    real = os.rename

    def spy(src, dst, *args, **kwargs):
        calls.append((Path(src).name, Path(dst).name))
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "rename", spy)
    return calls


def _primed(tmp_path) -> tuple[Path, DirectoryNames]:
    """A folder whose names are in memory: one rename through `names` listed it."""
    folder = tmp_path / "flat"
    _lay_down(folder, ["A.JPG", "B.JPG", "D.JPG", "E.JPG", "F.JPG"])
    names = DirectoryNames()
    assert _rename(folder, "A.JPG", "A2.JPG", names=names).ok
    assert names.known(folder, "C.JPG") is False
    return folder, names


def test_a_file_created_at_the_exact_target_after_the_listing_is_refused_before_rename(
    tmp_path, renames
):
    folder, names = _primed(tmp_path)
    (folder / "C.JPG").write_bytes(b"put there by another program")
    renames.clear()
    outcome = _rename(folder, "D.JPG", "C.JPG", names=names)
    assert (outcome.ok, outcome.code) == (False, CODE_TARGET_OCCUPIED)
    assert outcome.reason == "a file named C.JPG is already there, and pam never overwrites."
    assert renames == [], "os.rename was CALLED for an occupied target"
    assert (folder / "C.JPG").read_bytes() == b"put there by another program"
    assert (folder / "D.JPG").read_bytes() == b"the only copy of D.JPG"
    assert names.known(folder, "C.JPG") is None, "the folder was not forgotten"


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS: lstat matches case-insensitively")
def test_a_case_variant_created_after_the_listing_is_refused_on_windows(tmp_path, renames):
    folder, names = _primed(tmp_path)
    (folder / "c.jpg").write_bytes(b"put there by another program")
    renames.clear()
    outcome = _rename(folder, "D.JPG", "C.JPG", names=names)
    assert (outcome.ok, outcome.code) == (False, CODE_TARGET_OCCUPIED)
    assert renames == [], "os.rename was CALLED for a case variant of an occupied name"
    assert sorted(os.listdir(folder)) == ["A2.JPG", "B.JPG", "D.JPG", "E.JPG", "F.JPG", "c.jpg"]


def _tmp_is_case_sensitive() -> bool:
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "probe.txt").write_text("x", encoding="utf-8")
        return not (Path(d) / "PROBE.TXT").exists()


@pytest.mark.skipif(not _tmp_is_case_sensitive(), reason="needs a case-sensitive filesystem")
def test_a_case_variant_created_mid_run_is_not_refused_on_a_case_sensitive_filesystem(
    tmp_path, renames
):
    """X4's ACCEPTED RESIDUAL (XQ2), pinned so it is visible rather than assumed. On a
    case-sensitive filesystem the two names are two files: nothing is overwritten."""
    folder, names = _primed(tmp_path)
    (folder / "c.jpg").write_bytes(b"put there by another program")
    outcome = _rename(folder, "D.JPG", "C.JPG", names=names)
    assert outcome.ok
    assert (folder / "c.jpg").read_bytes() == b"put there by another program"
    assert (folder / "C.JPG").read_bytes() == b"the only copy of D.JPG"


# Hand-written, like `test_net_canary.py`'s set of the same name: the tests here that a
# PLATFORM may skip. The guard below compares it with the skipif marks actually present.
PLATFORM_GATED_TESTS = {
    "test_a_case_variant_created_after_the_listing_is_refused_on_windows",
    "test_a_case_variant_created_mid_run_is_not_refused_on_a_case_sensitive_filesystem",
}


def test_the_platform_gated_tests_are_exactly_the_hand_written_set():
    module = sys.modules[__name__]
    assert not hasattr(module, "pytestmark"), "a module-level mark would gate every test here"
    gated = {
        name
        for name, obj in vars(module).items()
        if name.startswith("test_")
        and any(mark.name == "skipif" for mark in getattr(obj, "pytestmark", []))
    }
    assert gated == PLATFORM_GATED_TESTS
    assert len(PLATFORM_GATED_TESTS) == 2, PLATFORM_GATED_TESTS


def test_a_target_lstat_cannot_answer_is_refused_and_never_renamed(
    tmp_path, renames, monkeypatch
):
    folder, names = _primed(tmp_path)
    real = os.lstat

    def lstat(path, *args, **kwargs):
        if Path(path).name == "C.JPG":
            raise PermissionError(errno.EACCES, "denied")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", lstat)
    renames.clear()
    outcome = _rename(folder, "D.JPG", "C.JPG", names=names)
    assert (outcome.ok, outcome.code) == (False, CODE_SOURCE_UNREADABLE)
    assert (
        outcome.reason == "pam could not check whether C.JPG is free, so D.JPG was left alone."
    )
    assert renames == []
    assert names.known(folder, "D.JPG") is None


def test_the_case_sensitive_residual_reaches_os_rename_and_overwrites_nothing(
    tmp_path, renames, monkeypatch
):
    """XQ2's residual on every platform: `lstat` made case-sensitive (as it is off NTFS)
    misses `c.jpg`, so memory's "free" stands and `os.rename` IS called - the accepted
    behaviour - and the file another process put there is untouched whatever the rename
    does. The skipping test above pins the same row where the filesystem really is."""
    folder, names = _primed(tmp_path)
    (folder / "c.jpg").write_bytes(b"put there by another program")
    real = os.lstat

    def case_sensitive_lstat(path, *args, **kwargs):
        if Path(path).name == "C.JPG":
            raise FileNotFoundError(errno.ENOENT, "no such file (case-sensitive)")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", case_sensitive_lstat)
    renames.clear()
    _rename(folder, "D.JPG", "C.JPG", names=names)
    assert renames == [("D.JPG", "C.JPG")], "the residual did not reach os.rename"
    assert (folder / "c.jpg").read_bytes() == b"put there by another program"


# --- stale "taken" (Step 5) and a failed rename -----------------------------------------


def test_a_name_deleted_after_the_listing_is_used_after_exactly_one_fresh_listing(
    tmp_path, listings
):
    folder, names = _primed(tmp_path)
    (folder / "E.JPG").unlink()  # another program removed it; memory still says taken
    assert names.known(folder, "E.JPG") is True
    listings.clear()
    outcome = _rename(folder, "F.JPG", "E.JPG", names=names)
    assert outcome.ok
    assert listings == [folder]
    assert (folder / "E.JPG").read_bytes() == b"the only copy of F.JPG"
    assert names.known(folder, "E.JPG") is True
    assert names.known(folder, "F.JPG") is False


def test_a_name_still_taken_is_refused_after_one_fresh_listing_with_todays_sentence(
    tmp_path, listings, renames
):
    folder, names = _primed(tmp_path)
    listings.clear()
    renames.clear()
    outcome = _rename(folder, "D.JPG", "e.jpg", names=names)
    assert (outcome.ok, outcome.code) == (False, CODE_TARGET_OCCUPIED)
    assert outcome.reason == "a file named e.jpg is already there, and pam never overwrites."
    assert listings == [folder]
    assert renames == []


def test_a_failed_os_rename_changes_no_memory_first_and_forgets_the_folder(
    tmp_path, listings, monkeypatch
):
    """Memory is updated only AFTER a rename that happened: at the moment `os.rename` is
    called the old name is still remembered and the new one is not. When it fails, the
    folder is forgotten, and the next rename lists it fresh."""
    folder, names = _primed(tmp_path)
    at_call: list[tuple[bool | None, bool | None]] = []

    def failing(src, dst, *args, **kwargs):
        at_call.append((names.known(folder, "D.JPG"), names.known(folder, "C.JPG")))
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(os, "rename", failing)
    outcome = _rename(folder, "D.JPG", "C.JPG", names=names)
    monkeypatch.setattr(os, "rename", _REAL_RENAME)
    assert (outcome.ok, outcome.code) == (False, CODE_RENAME_FAILED)
    assert at_call == [(True, False)]
    assert names.known(folder, "D.JPG") is None
    assert sorted(os.listdir(folder)) == ["A2.JPG", "B.JPG", "D.JPG", "E.JPG", "F.JPG"]
    listings.clear()
    assert _rename(folder, "D.JPG", "C.JPG", names=names).ok
    assert listings == [folder]


_REAL_RENAME = os.rename


# --- a crash leaves nothing stale (Step 7) -----------------------------------------------


def test_a_crash_after_a_rename_leaves_recovery_its_own_fresh_memory(
    tmp_path, listings, set_fault
):
    """`crash:after_rename:2` lands between the unit's 2nd `os.rename` and the memory update,
    so the dead run's memory never learned of it. Recovery adopts that rename, fails the 3rd
    member and puts the unit back from ITS OWN listing: every original name, the same bytes,
    no `done` or `intent` row left, and `pam verify` clean."""
    root = tmp_path / "photos"
    root.mkdir()
    for name in UNIT:
        put(root, name)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    assert plan_id is not None
    before = hashes(root)
    set_fault.arm("crash:after_rename:2")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()
    assert archive_names(root) == ["2019-07-04_001.JPG", "2019-07-04_001.MOV", "IMG_X.XMP"]

    listings.clear()
    conn = catalog(root)
    try:
        summary = recover(conn, root, LOG)
        assert repo.rows_in_state(conn, "done") == []
        assert repo.rows_in_state(conn, "intent") == []
    finally:
        conn.close()
    assert summary.adopted == ["2019-07-04_001.MOV"]
    assert summary.reverted == ["IMG_X.MOV", "IMG_X.JPG"]
    assert archive_names(root) == sorted(UNIT)
    assert hashes(root) == before
    assert listings == [root], "recovery did not build its memory from a fresh listing"
    assert_verify_agrees(root, where="after recovery from crash:after_rename:2")
