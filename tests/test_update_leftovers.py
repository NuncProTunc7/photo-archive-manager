r"""`pam update` reaps the installer downloads earlier updates left in TEMP (M15 Task 12-G, F3).

Every expectation is spelled from the maintainer's rules (2026-11-20, the brief), never from
`pam/update/leftovers.py`: only a folder directly in TEMP named `pam-update-*`, holding exactly one
regular file `pam-setup-<version>.exe` and nothing else; never a link or a junction; never while
setup runs; a removal that fails is skipped. THE DELETION CALLS ARE WATCHED (CLAUDE.md, M697): a
shape that must be left alone is asserted on the spy never being called, not only on the folder
still being there afterwards.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from pam import run_registry
from pam.errors import RunRegistryError
from pam.update import leftovers
from pam.update.leftovers import reap_leftovers

SENTENCE = "pam removed {count} installer download(s) left from earlier updates."  # as ruled


class Spy:
    """`os.remove` / `os.rmdir`, recorded, then done for real (or failing, when told to)."""

    def __init__(self, real, fail_on: set[str] = frozenset()):
        self.real, self.fail_on, self.calls = real, fail_on, []

    def __call__(self, path):
        self.calls.append(Path(path))
        if Path(path).name in self.fail_on or Path(path).parent.name in self.fail_on:
            raise PermissionError(32, "in use", str(path))
        self.real(path)


@pytest.fixture
def temp(tmp_path):
    folder = tmp_path / "temp"
    folder.mkdir()
    return folder


def staged(temp: Path, name: str = "pam-update-abc123", files=("pam-setup-1.1.0.exe",)) -> Path:
    folder = temp / name
    folder.mkdir()
    for file in files:
        (folder / file).write_bytes(b"MZ installer")
    return folder


def reap(temp, *, running=False, fail_on=frozenset()):
    remove_file, remove_folder = Spy(os.remove, fail_on), Spy(os.rmdir)
    count = reap_leftovers(
        temp,
        setup_running=lambda: running,
        remove_file=remove_file,
        remove_folder=remove_folder,
    )
    return count, remove_file.calls, remove_folder.calls


def test_the_sentence_is_the_owners():
    assert leftovers.REAPED == SENTENCE


def test_a_qualifying_folder_goes_its_file_first(temp):
    folder = staged(temp)
    count, files, folders = reap(temp)
    assert count == 1
    assert files == [folder / "pam-setup-1.1.0.exe"] and folders == [folder]
    assert list(temp.iterdir()) == []


def test_every_qualifying_folder_goes_and_a_release_candidate_counts(temp):
    a = staged(temp, "pam-update-a", ("pam-setup-1.1.0.exe",))
    b = staged(temp, "pam-update-b", ("pam-setup-1.2.0rc1.exe",))
    count, files, folders = reap(temp)
    assert count == 2 and sorted(folders) == [a, b] and list(temp.iterdir()) == []


NOT_OURS = {
    # Two second files, one listed before the installer and one after (M4726: a second file that
    # sorts first hid a dropped count check behind the name check).
    "a second file listed first": dict(files=("pam-setup-1.1.0.exe", "notes.txt")),
    "a second file listed last": dict(files=("pam-setup-1.1.0.exe", "readme.txt")),
    "two installers": dict(files=("pam-setup-1.1.0.exe", "pam-setup-1.0.0.exe")),
    "another file name": dict(files=("setup.exe",)),
    "another prefix of the same length": dict(files=("abc-setup-1.1.0.exe",)),
    "another extension": dict(files=("pam-setup-1.1.0.msi",)),
    "not a version": dict(files=("pam-setup-latest.exe",)),
    "not an exe": dict(files=("pam-setup-1.1.0.exe.txt",)),
    "an empty folder": dict(files=()),
    "the wrong prefix": dict(name="pam-updater-abc", files=("pam-setup-1.1.0.exe",)),
    "no prefix at all": dict(name="abc-pam-update-", files=("pam-setup-1.1.0.exe",)),
}


@pytest.mark.parametrize("shape", sorted(NOT_OURS))
def test_a_folder_of_any_other_shape_is_left_alone_and_nothing_is_deleted(temp, shape):
    assert NOT_OURS  # an independently written list, never empty (CLAUDE.md, M743)
    folder = staged(temp, **NOT_OURS[shape])
    before = sorted(p.relative_to(temp) for p in temp.rglob("*"))
    count, files, folders = reap(temp)
    assert count == 0 and files == [] and folders == []
    assert sorted(p.relative_to(temp) for p in temp.rglob("*")) == before
    assert folder.is_dir()


def test_a_subfolder_beside_the_installer_keeps_the_folder(temp):
    folder = staged(temp)
    (folder / "sub").mkdir()
    count, files, folders = reap(temp)
    assert (count, files, folders) == (0, [], [])
    assert (folder / "pam-setup-1.1.0.exe").is_file()


def test_a_subfolder_alone_named_like_the_installer_is_not_a_file(temp):
    folder = staged(temp, files=())
    (folder / "pam-setup-1.1.0.exe").mkdir()
    assert reap(temp) == (0, [], [])


def test_a_file_in_temp_named_like_a_staging_folder_is_not_a_folder(temp):
    (temp / "pam-update-abc").write_bytes(b"MZ")
    assert reap(temp) == (0, [], [])
    assert (temp / "pam-update-abc").is_file()


def test_only_folders_directly_in_temp_are_looked_at(temp):
    nested = staged(temp, "keep-me")
    inner = staged(nested, "pam-update-inner")
    assert reap(temp) == (0, [], [])
    assert (inner / "pam-setup-1.1.0.exe").is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="a junction is Windows'")
def test_a_junction_named_like_a_staging_folder_is_never_followed(temp, tmp_path):
    """The junction's TARGET has exactly the qualifying shape: following it would delete a file
    outside TEMP."""
    target = staged(tmp_path, "elsewhere")
    link = temp / "pam-update-junction"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True
    )
    try:
        assert link.is_dir()  # the junction resolves
        assert reap(temp) == (0, [], [])
        assert (target / "pam-setup-1.1.0.exe").is_file()
    finally:
        os.rmdir(link)  # removes the junction, never its target


def _symlink_or_skip(link: Path, target: Path, folder: bool) -> None:
    try:
        os.symlink(target, link, target_is_directory=folder)
    except OSError as error:  # Windows without Developer Mode cannot make one
        pytest.skip(f"cannot create a symbolic link here ({error})")


def test_a_symbolic_link_folder_is_never_followed(temp, tmp_path):
    target = staged(tmp_path, "elsewhere")
    _symlink_or_skip(temp / "pam-update-link", target, folder=True)
    assert reap(temp) == (0, [], [])
    assert (target / "pam-setup-1.1.0.exe").is_file()


def test_a_symbolic_link_installer_is_never_removed(temp, tmp_path):
    real = tmp_path / "pam-setup-1.1.0.exe"
    real.write_bytes(b"MZ")
    folder = staged(temp, files=())
    _symlink_or_skip(folder / "pam-setup-1.1.0.exe", real, folder=False)
    assert reap(temp) == (0, [], [])
    assert real.is_file()


def test_nothing_is_looked_at_while_setup_runs(temp):
    folder = staged(temp)
    assert reap(temp, running=True) == (0, [], [])
    assert (folder / "pam-setup-1.1.0.exe").is_file()


def test_nothing_is_removed_when_pam_cannot_tell_whether_setup_runs(temp):
    folder = staged(temp)
    removed = []

    def cannot_tell():
        raise RunRegistryError("pam cannot tell whether setup.lock is in use (denied).")

    count = reap_leftovers(
        temp,
        setup_running=cannot_tell,
        remove_file=removed.append,
        remove_folder=removed.append,
    )
    assert count == 0 and removed == []
    assert (folder / "pam-setup-1.1.0.exe").is_file()


def test_a_removal_that_fails_is_skipped_silently_and_the_others_still_go(temp):
    busy = staged(temp, "pam-update-busy")
    free = staged(temp, "pam-update-free")
    count, files, folders = reap(temp, fail_on={"pam-update-busy"})
    assert count == 1
    assert sorted(files) == [busy / "pam-setup-1.1.0.exe", free / "pam-setup-1.1.0.exe"]
    assert folders == [free]
    assert (busy / "pam-setup-1.1.0.exe").is_file() and not free.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="a file in use is Windows' refusal")
def test_an_installer_still_open_is_left_and_not_counted(temp):
    """The real `os.remove` against a file another handle holds (setup running from it)."""
    folder = staged(temp)
    with open(folder / "pam-setup-1.1.0.exe", "rb"):
        assert reap_leftovers(temp, setup_running=lambda: False) == 0
    assert (folder / "pam-setup-1.1.0.exe").is_file()


def test_the_defaults_are_the_temp_folder_and_the_real_setup_lock(
    temp, monkeypatch, isolated_run_registry
):
    """No seam replaced but TEMP itself: a folder in `tempfile.gettempdir()` goes; then, with this
    test's own registry holding `setup.lock`, a second one stays."""
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    staged(temp, "pam-update-first")
    assert reap_leftovers() == 1 and list(temp.iterdir()) == []
    second = staged(temp, "pam-update-second")
    if sys.platform != "win32":
        return  # setup.lock is Windows' (E11)
    lock = run_registry.setup_lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    handle = run_registry._hold(lock, 1.0)
    try:
        assert run_registry.setup_running()
        assert reap_leftovers() == 0
    finally:
        run_registry._close(handle)
    assert (second / "pam-setup-1.1.0.exe").is_file()


class FakeEntry:
    """A `DirEntry` that says what a link says, for a machine that cannot make one (no Developer
    Mode: the two symbolic-link tests above skip there). Each operand of "never a link" alone."""

    def __init__(self, *, symlink=False, reparse=False, folder=False):
        self.symlink, self.reparse, self.folder = symlink, reparse, folder
        self.name, self.path = "pam-setup-1.1.0.exe", "unused"

    def is_symlink(self):
        return self.symlink

    def stat(self, follow_symlinks=True):
        assert follow_symlinks is False  # never followed
        return _Reparse() if self.reparse else _Plain()

    def is_dir(self, follow_symlinks=True):
        assert follow_symlinks is False
        return self.folder

    def is_file(self, follow_symlinks=True):
        assert follow_symlinks is False
        return not self.folder


class _Reparse:
    st_file_attributes = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT, spelled from the Windows SDK


class _Plain:
    st_file_attributes = 0x10 | 0x20  # DIRECTORY | ARCHIVE: attributes, and no reparse point


@pytest.mark.parametrize("folder", [False, True], ids=["file", "folder"])
def test_a_link_by_either_sign_is_not_plain_and_a_plain_entry_is(folder):
    assert leftovers._plain(FakeEntry(folder=folder), folder=folder) is True  # the control
    assert leftovers._plain(FakeEntry(symlink=True, folder=folder), folder=folder) is False
    assert leftovers._plain(FakeEntry(reparse=True, folder=folder), folder=folder) is False
