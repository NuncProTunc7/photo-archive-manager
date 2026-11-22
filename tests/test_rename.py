"""The single verified rename (spec 7.1 rev 7, spec 7.0 J8) and the guard on it.

Every test here runs against a real temp filesystem. Mocking `os.rename` would
defeat the entire purpose of the module: what is under test is what actually happens
to a file on this operating system, and a mock can only confirm what the test author
already believed.
"""

import hashlib
import logging
import os
import sys
from pathlib import Path

import pytest
from guards import ALLOWED_MOVE_SITES, move_call_sites

from pam.execute.rename import (
    CASE_ONLY_REASON,
    CODE_NOT_SAME_DIRECTORY,
    CODE_TARGET_OCCUPIED,
    REFUSAL_CODES,
    perform_verified_rename,
)

LOG = logging.getLogger("pam.tests.rename")


def snapshot(path: Path) -> tuple[int, int]:
    """The (size, mtime_ns) pair a plan is built from, read the way the scanner reads it."""
    stat = os.stat(path)
    return stat.st_size, stat.st_mtime_ns


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rename(root: Path, old_rel: str, new_rel: str, *, expect: tuple[int, int] | None = None):
    size, mtime_ns = snapshot(root / old_rel) if expect is None else expect
    return perform_verified_rename(
        archive_root=root,
        old_rel=old_rel,
        new_rel=new_rel,
        expect_size=size,
        expect_mtime_ns=mtime_ns,
        logger=LOG,
    )


@pytest.fixture()
def archive(tmp_path):
    root = tmp_path / "archive"
    (root / "2019").mkdir(parents=True)
    (root / "IMG_1234.JPG").write_bytes(b"the only copy of this photo")
    (root / "2019" / "IMG_9.HEIC").write_bytes(b"nested")
    return root


# --- the happy path ---------------------------------------------------------------


def test_a_verified_rename_moves_the_file_and_reports_ok(archive):
    before = sha256(archive / "IMG_1234.JPG")
    outcome = rename(archive, "IMG_1234.JPG", "2019-07-04_001.JPG")
    assert (outcome.ok, outcome.reason) == (True, None)
    assert not (archive / "IMG_1234.JPG").exists()
    # spec 7.0 J8's other half: the bytes are the same bytes. pam renames, never
    # rewrites - the file's content is the thing that is irreplaceable.
    assert sha256(archive / "2019-07-04_001.JPG") == before


def test_a_rename_inside_a_subdirectory_uses_posix_relative_paths(archive):
    outcome = rename(archive, "2019/IMG_9.HEIC", "2019/2019-07-04_002.HEIC")
    assert outcome.ok
    assert (archive / "2019" / "2019-07-04_002.HEIC").read_bytes() == b"nested"


def test_sha256_is_invariant_across_every_successful_rename(archive):
    """Hash invariance, asserted over a chain of renames rather than a single one."""
    before = sha256(archive / "IMG_1234.JPG")
    chain = ["IMG_1234.JPG", "a.JPG", "b.JPG", "c.JPG"]
    for old_rel, new_rel in zip(chain, chain[1:], strict=False):
        assert rename(archive, old_rel, new_rel).ok
        assert sha256(archive / new_rel) == before


# --- refusals: each one is a file that survives -----------------------------------


def test_an_occupied_target_is_refused_and_the_occupant_is_untouched(archive):
    (archive / "taken.JPG").write_bytes(b"a different photo entirely")
    outcome = rename(archive, "IMG_1234.JPG", "taken.JPG")
    assert outcome.ok is False
    # The REASON is asserted, not just the refusal: with the explicit absence check
    # deleted, Windows' os.rename still refuses (FileExistsError) and a test that only
    # checked `ok is False` would pass while the safety check was gone. On POSIX the
    # bytes assertion catches it instead. Together they pin it on both.
    assert outcome.reason is not None and "already there" in outcome.reason
    assert (archive / "taken.JPG").read_bytes() == b"a different photo entirely"
    assert (archive / "IMG_1234.JPG").read_bytes() == b"the only copy of this photo"


def test_a_target_differing_only_in_case_is_occupied_too(archive):
    """spec 6.3.2 / 5.4 rev 6: absence is judged case-INSENSITIVELY, by pam.

    The check must not be delegated to the filesystem. This assertion holds on NTFS
    (where `Path.exists()` would agree) and on a case-sensitive Linux CI runner
    (where it would not, and the file would be silently clobbered on POSIX).
    """
    (archive / "PHOTO.JPG").write_bytes(b"someone else")
    outcome = rename(archive, "IMG_1234.JPG", "photo.jpg")
    assert outcome.ok is False
    assert outcome.reason is not None and "already there" in outcome.reason
    assert (archive / "PHOTO.JPG").read_bytes() == b"someone else"
    assert sorted(p.name for p in archive.iterdir()) == ["2019", "IMG_1234.JPG", "PHOTO.JPG"]


def test_a_case_only_rename_is_refused_with_its_own_reason(archive):
    outcome = rename(archive, "IMG_1234.JPG", "img_1234.jpg")
    assert (outcome.ok, outcome.reason) == (False, CASE_ONLY_REASON)
    # The on-disk name keeps its case exactly: on NTFS a case-only rename would
    # "succeed" as a no-op and be reported as a rename that happened.
    assert [p.name for p in archive.iterdir() if p.is_file()] == ["IMG_1234.JPG"]


def test_renaming_a_file_to_its_own_name_is_refused(archive):
    """Not a case of its own: the target-absence check finds the source there.

    The reason is pinned because the generic "already there, and pam never
    overwrites" would be untrue here - the file in the way is the user's own.
    """
    outcome = rename(archive, "IMG_1234.JPG", "IMG_1234.JPG")
    assert outcome.ok is False
    assert outcome.code == CODE_TARGET_OCCUPIED
    assert outcome.reason is not None and "already has the name" in outcome.reason
    assert (archive / "IMG_1234.JPG").read_bytes() == b"the only copy of this photo"


# --- refusal 0: a rename never moves a file (UX 4) ---------------------------------


def test_a_target_in_another_directory_is_refused(archive):
    """UX 4 promises "0 files will be deleted, moved, or modified - renames only, in
    place." Nothing else in the decomposition enforces it, and two of this function's
    three callers build their paths from journal rows rather than a validated plan."""
    outcome = rename(archive, "2019/IMG_9.HEIC", "IMG_9.HEIC")
    assert (outcome.ok, outcome.code) == (False, CODE_NOT_SAME_DIRECTORY)
    assert (archive / "2019" / "IMG_9.HEIC").read_bytes() == b"nested"
    assert not (archive / "IMG_9.HEIC").exists()


def test_a_target_escaping_the_archive_is_refused(archive):
    """`..` would take the file out of the tree pam is responsible for entirely, and
    undo's journal row would then point outside the root."""
    outcome = rename(archive, "2019/IMG_9.HEIC", "../OUTSIDE.HEIC")
    assert (outcome.ok, outcome.code) == (False, CODE_NOT_SAME_DIRECTORY)
    assert (archive / "2019" / "IMG_9.HEIC").read_bytes() == b"nested"
    assert not (archive.parent / "OUTSIDE.HEIC").exists()


@pytest.mark.parametrize("bad", ["", ".", "..", "2019/"])
def test_a_target_with_no_usable_name_is_refused(archive, bad):
    outcome = rename(archive, "2019/IMG_9.HEIC", bad)
    assert (outcome.ok, outcome.code) == (False, CODE_NOT_SAME_DIRECTORY)
    assert (archive / "2019" / "IMG_9.HEIC").exists()


def test_a_rename_inside_a_subdirectory_still_works(archive):
    """The guard must not break legitimate nested archives - only cross-directory."""
    before = sha256(archive / "2019" / "IMG_9.HEIC")
    outcome = rename(archive, "2019/IMG_9.HEIC", "2019/2019-07-04_001.HEIC")
    assert (outcome.ok, outcome.reason) == (True, None)
    assert sha256(archive / "2019" / "2019-07-04_001.HEIC") == before


@pytest.mark.parametrize("drift", ["size", "mtime"])
def test_a_file_that_changed_since_the_scan_is_left_alone(archive, drift):
    size, mtime_ns = snapshot(archive / "IMG_1234.JPG")
    expect = (size + 1, mtime_ns) if drift == "size" else (size, mtime_ns + 1)
    outcome = rename(archive, "IMG_1234.JPG", "2019-07-04_001.JPG", expect=expect)
    assert outcome.ok is False
    assert outcome.reason is not None and "changed on disk" in outcome.reason
    assert (archive / "IMG_1234.JPG").read_bytes() == b"the only copy of this photo"
    assert not (archive / "2019-07-04_001.JPG").exists()


def test_verification_reads_the_disk_not_the_expectation(archive):
    """The disk is the authority: a file edited after the scan is refused."""
    expect = snapshot(archive / "IMG_1234.JPG")
    (archive / "IMG_1234.JPG").write_bytes(b"edited after the scan, in place")
    outcome = rename(archive, "IMG_1234.JPG", "2019-07-04_001.JPG", expect=expect)
    assert outcome.ok is False
    assert (archive / "IMG_1234.JPG").read_bytes() == b"edited after the scan, in place"


def test_a_vanished_source_is_an_outcome_not_an_exception(archive):
    outcome = rename(archive, "gone.JPG", "2019-07-04_001.JPG", expect=(1, 1))
    assert outcome.ok is False
    assert outcome.reason is not None and "could not read" in outcome.reason


def test_an_unreachable_target_directory_is_an_outcome_not_an_exception(archive):
    """Since the same-directory refusal landed, this is caught before the filesystem
    is touched at all - a stronger answer than the OSError it used to produce, and
    reached without asking the disk about a folder pam would never write to."""
    outcome = rename(archive, "IMG_1234.JPG", "no/such/folder/x.JPG")
    assert (outcome.ok, outcome.code) == (False, CODE_NOT_SAME_DIRECTORY)
    assert outcome.reason is not None and "renames files where they are" in outcome.reason
    assert (archive / "IMG_1234.JPG").exists()


def test_every_refusal_carries_a_documented_code(archive):
    """`code` is what Tasks 8 and 9 branch on; a refusal path without one would push
    them back to substring-matching English prose."""
    seen = {
        rename(archive, "2019/IMG_9.HEIC", "IMG_9.HEIC").code,
        rename(archive, "IMG_1234.JPG", "img_1234.jpg").code,
        rename(archive, "gone.JPG", "x.JPG", expect=(1, 1)).code,
        rename(archive, "IMG_1234.JPG", "a.JPG", expect=(1, 1)).code,
        rename(archive, "IMG_1234.JPG", "IMG_1234.JPG").code,
        rename(archive, "IMG_1234.JPG", "L" * 300 + ".JPG").code,
    }
    assert None not in seen
    assert seen <= REFUSAL_CODES
    assert seen == REFUSAL_CODES, (
        f"a documented code is unreached by any test: {REFUSAL_CODES - seen}"
    )


def test_an_oserror_from_the_rename_itself_is_an_outcome_not_an_exception(archive):
    """A name too long for the filesystem: the source directory is readable, the
    absence check passes, and `os.rename` itself is what fails. Portable - NTFS and
    ext4 both cap one path component at 255 characters."""
    outcome = rename(archive, "IMG_1234.JPG", "L" * 300 + ".JPG")
    assert outcome.ok is False
    assert outcome.reason is not None and "could not rename" in outcome.reason
    # The OS's own words go in `detail`, not glued onto `reason`: str(OSError) repeats
    # the absolute paths the report already prints beside it.
    assert outcome.detail and "\\" not in outcome.detail
    assert (archive / "IMG_1234.JPG").read_bytes() == b"the only copy of this photo"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows rename semantics")
def test_windows_os_rename_refuses_an_existing_target(tmp_path):
    """Not a test of pam: a test of the platform fact pam's second line of defence
    rests on (CLAUDE.md, spec 7.0 J8). If a future Windows build ever made os.rename
    overwrite like POSIX does, the explicit absence check above would become the only
    protection and this failure is how we would find out.
    """
    src = tmp_path / "a.jpg"
    dst = tmp_path / "b.jpg"
    src.write_bytes(b"source")
    dst.write_bytes(b"target")
    with pytest.raises(OSError):
        os.rename(src, dst)
    assert dst.read_bytes() == b"target"


# --- the fault checkpoint ---------------------------------------------------------


def test_after_rename_fires_immediately_after_the_rename_happened(archive, set_fault):
    """spec 9.3: the checkpoint is AFTER the filesystem moved, and that is the whole
    state it exists to create - a rename done with no journal resolution behind it,
    which spec 7.2 adopts. Moved one line earlier it would crash before the rename and
    the entire recovery matrix would be testing the wrong window.
    """
    set_fault.arm("crash:after_rename:1")
    with pytest.raises(SystemExit):
        rename(archive, "IMG_1234.JPG", "2019-07-04_001.JPG")
    assert set_fault.hits("after_rename") == 1
    assert not (archive / "IMG_1234.JPG").exists()
    assert (archive / "2019-07-04_001.JPG").read_bytes() == b"the only copy of this photo"


def test_a_refused_rename_never_reaches_the_checkpoint(archive, set_fault):
    set_fault.arm("crash:after_rename:1")
    (archive / "taken.JPG").write_bytes(b"occupied")
    assert rename(archive, "IMG_1234.JPG", "taken.JPG").ok is False
    assert set_fault.hits("after_rename") == 0


# --- the single-implementation guard (spec 7.1 rev 7) -----------------------------


def test_exactly_one_filesystem_move_implementation_exists_in_the_package():
    """spec 7.1 rev 7's decomposition, made mechanical.

    The reason apply, undo and recovery share `perform_verified_rename` is that three
    copies would drift by M8. An intention someone re-checks at review is not that
    guarantee; this is. It also kills the `os.rename` -> `os.replace` mutation, which
    no behavioural test can catch: the explicit absence check refuses an occupied
    target before either call is reached, so the two behave identically in every
    scenario a test can construct - and differ only in the race pam cannot lose.
    """
    import pam

    pam_dir = Path(pam.__file__).parent
    sites = [
        (path.relative_to(pam_dir).as_posix(), function, call)
        for path in sorted(pam_dir.rglob("*.py"))
        for function, call in move_call_sites(path.read_text(encoding="utf-8"))
    ]
    assert sorted(sites) == sorted(ALLOWED_MOVE_SITES)


def test_no_module_under_execute_moves_a_file_except_rename_py():
    """The narrower rule, stated separately so it cannot be lost in a merge.

    Tasks 6-9 add apply.py, unit.py, recover.py and undo.py to this package. Each of
    them has an obvious reason to reach for os.rename, and each of them must not.
    """
    import pam

    execute_dir = Path(pam.__file__).parent / "execute"
    offenders = [
        (path.name, sites)
        for path in sorted(execute_dir.rglob("*.py"))
        if path.name != "rename.py"
        and (sites := move_call_sites(path.read_text(encoding="utf-8")))
    ]
    assert offenders == []


@pytest.mark.parametrize(
    "planted",
    [
        "import os\ndef f():\n    os.rename(a, b)\n",
        "import os as o\ndef f():\n    o.rename(a, b)\n",
        "from os import rename\ndef f():\n    rename(a, b)\n",
        "from os import rename as mv\ndef f():\n    mv(a, b)\n",
        "import os\ndef f():\n    os.replace(a, b)\n",
        "from os import replace as swap\ndef f():\n    swap(a, b)\n",
        "def f():\n    new_path.rename(old_path)\n",
        "def f():\n    new_path.replace(old_path)\n",
        "import shutil\ndef f():\n    shutil.move(a, b)\n",
    ],
)
def test_the_move_guard_catches_every_route_to_moving_a_file(planted):
    """The guard proven ENFORCING, one row per route in - the aliased import and the
    pathlib methods being the ones a grep for `os.rename(` would never see."""
    assert move_call_sites(planted) != []


def test_the_move_guard_does_not_fire_on_prose_or_on_string_replace():
    """The other direction. A guard that fires on ordinary code gets worked around,
    and `str.replace` is ordinary code - `pam/paths.py` alone has two."""
    benign = (
        '"""os.rename lives in execute/rename.py; os.replace is never used here."""\n'
        "# os.rename(a, b)\n"
        "def f(text):\n"
        "    return text.replace('\\\\', '/').replace('x', 'y')\n"
    )
    assert move_call_sites(benign) == []


def test_the_move_guard_notices_a_second_call_in_the_sanctioned_function():
    """A duplicate must not be absorbed: the allowlist is compared as a sorted list
    precisely so that a second os.rename beside the blessed one still fails."""
    planted = (
        "import os\ndef perform_verified_rename():\n    os.rename(a, b)\n    os.rename(c, d)\n"
    )
    assert move_call_sites(planted) == [
        ("perform_verified_rename", "os.rename"),
        ("perform_verified_rename", "os.rename"),
    ]
