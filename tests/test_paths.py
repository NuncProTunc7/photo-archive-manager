import os
from pathlib import Path

import pytest

from pam.errors import PamError
from pam.paths import (
    ARCHIVE_POINTER,
    Workspace,
    app_root,
    archive_slug,
    known_archives,
    record_archive_root,
    resolve_archive,
    workspace_for,
    workspace_if_exists,
)


def test_slug_is_stable_and_safe(tmp_path):
    root = tmp_path / "My Photos!"
    root.mkdir()
    slug = archive_slug(root)
    assert slug == archive_slug(root)  # deterministic
    name, digest = slug.rsplit("-", 1)
    assert name == "my-photos"  # sanitized, lowercased
    assert len(digest) == 8
    assert all(c in "0123456789abcdef" for c in digest)


def test_different_roots_different_slugs(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    assert archive_slug(a) != archive_slug(b)


def test_workspace_layout_created(tmp_path, isolated_pam_home):
    root = tmp_path / "photos"
    root.mkdir()
    ws = workspace_for(root)
    assert isinstance(ws, Workspace)
    assert ws.root.is_dir()
    assert str(ws.root).startswith(str(isolated_pam_home))
    for d in (ws.logs_dir, ws.reports_dir, ws.thumbs_dir):
        assert d.is_dir()
    assert ws.db_path == ws.root / "catalog.db"
    assert ws.archive_root == root.resolve()


def test_records_and_resolves_a_single_archive(tmp_path):
    archive = tmp_path / "photos"
    archive.mkdir()
    ws = workspace_for(archive)
    record_archive_root(ws)
    assert resolve_archive(None) == archive.resolve()
    assert [a.path for a in known_archives()] == [archive.resolve()]


def test_explicit_archive_wins_and_needs_no_pointer(tmp_path):
    archive = tmp_path / "photos"
    archive.mkdir()
    assert resolve_archive(archive) == archive.resolve()


def test_no_known_archive_says_how_to_make_one(tmp_path):
    with pytest.raises(PamError, match="pam scan"):
        resolve_archive(None)


def test_two_archives_require_a_choice(tmp_path):
    for name in ("a", "b"):
        archive = tmp_path / name
        archive.mkdir()
        record_archive_root(workspace_for(archive))
    with pytest.raises(PamError, match="--archive"):
        resolve_archive(None)


def test_scan_records_the_pointer(tmp_path):
    # The resolver only works because pam scan leaves this breadcrumb.
    import sys

    from pam.scan.runner import run_scan

    archive = tmp_path / "photos"
    archive.mkdir()
    (archive / "a.jpg").write_bytes(b"x")
    fake = [sys.executable, str(Path(__file__).parent / "fake_exiftool.py")]
    run_scan(archive, exiftool_command=fake)
    assert resolve_archive(None) == archive.resolve()


def test_known_archives_strips_trailing_whitespace_from_pointer(tmp_path):
    # A trailing newline in the pointer file must not corrupt the path.
    archive = tmp_path / "photos"
    archive.mkdir()
    ws = workspace_for(archive)
    (ws.root / ARCHIVE_POINTER).write_text(f"{archive.resolve()}\n\n", encoding="utf-8")
    assert [a.path for a in known_archives()] == [archive.resolve()]


def test_known_archives_ignores_an_empty_pointer_file(tmp_path):
    # An empty (or whitespace-only) pointer is skipped, not turned into Path(".").
    archive = tmp_path / "photos"
    archive.mkdir()
    ws = workspace_for(archive)
    (ws.root / ARCHIVE_POINTER).write_text("   \n", encoding="utf-8")
    assert known_archives() == []


def test_known_archives_are_sorted(tmp_path):
    # Results are sorted by workspace slug, not creation order or glob order.
    base = app_root() / "archives"
    for slug, name in [("zzz-11112222", "z"), ("aaa-11112222", "a"), ("mmm-11112222", "m")]:
        d = base / slug
        d.mkdir(parents=True)
        # The archive folders really exist so these read as REACHABLE (spec 3 rev 8);
        # sort order is by workspace slug either way, but a fixture of imaginary
        # paths would be testing the unreachable branch without saying so.
        (tmp_path / name).mkdir()
        (d / ARCHIVE_POINTER).write_text(str(tmp_path / name), encoding="utf-8")
    assert [a.path for a in known_archives()] == [
        tmp_path / "a",
        tmp_path / "m",
        tmp_path / "z",
    ]


def test_an_unreachable_archive_beside_a_reachable_one_refuses_instead_of_guessing(tmp_path):
    """spec 3 rev 8. This test USED to assert the opposite, and the reversal is the
    point (`test_a_pointer_to_a_deleted_folder_is_not_offered`).

    M4 finding 12 dropped unreachable pointers so this case - scan OldName, rename the
    folder, scan again - kept working without --archive. But the filesystem cannot tell
    a renamed folder from an unplugged drive, so the same filter that removed the
    convenience problem also made pam silently auto-select the survivor when the user
    had two archives and one was disconnected. Under spec 7.2 rev 8 that is not merely
    unhelpful: recovery branches on the disk, so running against the wrong archive
    resolves `intent` rows terminally (J9).

    pam now asks. Both folders are listed, the one it cannot see is marked, and the
    message says what to do.
    """
    old = tmp_path / "OldName"
    old.mkdir()
    record_archive_root(workspace_for(old))
    new = tmp_path / "NewName"
    old.rename(new)
    record_archive_root(workspace_for(new))

    assert [(a.path, a.reachable) for a in known_archives()] == [
        (new.resolve(), True),
        (old.resolve(), False),
    ]
    with pytest.raises(PamError) as excinfo:
        resolve_archive(None)
    message = str(excinfo.value)
    assert "more than one" in message
    assert str(old.resolve()) in message and str(new.resolve()) in message
    assert "cannot see this one" in message
    assert "--archive" in message


def test_a_single_unreachable_archive_names_it_instead_of_denying_it_exists(tmp_path):
    """The lie this ruling exists to stop: pam claiming it has never scanned a folder
    it has scanned. Previously `known_archives()` returned [] here and the user was
    told "pam has not scanned any folder yet" - about the folder they scanned this
    morning, on a drive that is merely unplugged.
    """
    archive = tmp_path / "photos"
    archive.mkdir()
    record_archive_root(workspace_for(archive))
    archive.rmdir()

    assert [(a.path, a.reachable) for a in known_archives()] == [(archive.resolve(), False)]
    with pytest.raises(PamError) as excinfo:
        resolve_archive(None)
    message = str(excinfo.value)
    assert "scanned this folder before" in message
    assert str(archive.resolve()) in message
    assert "has not scanned any folder yet" not in message


def test_an_explicit_archive_pam_cannot_see_is_refused(tmp_path):
    """spec 3 rev 8: an explicit path is used unless it is not an existing directory.

    Refused before any workspace is opened, because spec 7.2's recovery runs at the
    start of every command and branches on the disk - handing it a root pam cannot see
    would record terminal outcomes for an archive whose real state is invisible (J9).
    """
    with pytest.raises(PamError, match="cannot see this folder"):
        resolve_archive(tmp_path / "not-there")

    a_file = tmp_path / "notes.txt"
    a_file.write_text("x", encoding="utf-8")
    with pytest.raises(PamError, match="cannot see this folder"):
        resolve_archive(a_file)


def test_no_archives_at_all_still_says_how_to_make_one(tmp_path):
    """The genuinely empty case keeps its message - the one situation where "pam has
    not scanned any folder yet" is true."""
    assert known_archives() == []
    with pytest.raises(PamError, match="pam scan"):
        resolve_archive(None)


def test_a_pointer_that_names_a_file_is_not_an_archive(tmp_path):
    # is_dir(), not exists(): a pointer resolving to a plain file is as dead as one
    # resolving to nothing, and workspace_if_exists would refuse it anyway.
    archive = tmp_path / "photos"
    archive.mkdir()
    ws = workspace_for(archive)
    (ws.root / ARCHIVE_POINTER).write_text(str(tmp_path / "notes.txt"), encoding="utf-8")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert known_archives() == []


def test_the_pointer_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    # Finding 12: the `if text:` guard catches a zero-byte pointer, but a TRUNCATED
    # one ('C:/Photos/2019' cut to 'C:/Photos') resolves to a different existing
    # directory and passes is_dir() - the dangerous failure, and only an atomic
    # write prevents it. Rewriting over an existing pointer must also converge.
    archive = tmp_path / "photos"
    archive.mkdir()
    ws = workspace_for(archive)
    (ws.root / ARCHIVE_POINTER).write_text("C:/somewhere/else", encoding="utf-8")
    record_archive_root(ws)
    record_archive_root(ws)  # idempotent: os.replace overwrites its own target
    assert (ws.root / ARCHIVE_POINTER).read_text(encoding="utf-8") == str(archive.resolve())
    # The temp file lives in the SAME directory (a cross-volume os.replace is not
    # atomic) and must not survive as a stray file in the workspace.
    assert [p.name for p in ws.root.iterdir() if p.name.startswith(ARCHIVE_POINTER)] == [
        ARCHIVE_POINTER
    ]


def test_a_crash_mid_write_leaves_the_previous_pointer_intact(tmp_path, monkeypatch):
    # Mutation-gate finding, closed here. Replacing the atomic write with a plain
    # write_text left the whole suite green: the test above only proves the final
    # content is right, which a direct write satisfies too. Atomicity is only
    # observable by interrupting the write, so this interrupts it.
    #
    # The dangerous failure is a TRUNCATED pointer, not an empty one: cutting
    # '...\photos\2019' to '...\photos' names a directory that exists, so is_dir()
    # accepts it and pam silently plans against the wrong folder.
    archive = tmp_path / "photos" / "2019"
    archive.mkdir(parents=True)
    ws = workspace_for(archive)
    record_archive_root(ws)
    assert [a.path for a in known_archives()] == [archive.resolve()]

    parent = str(archive.resolve().parent)
    real_write_text = Path.write_text

    def dies_half_way(self, data, *args, **kwargs):
        real_write_text(self, data[: len(parent)], *args, **kwargs)
        raise OSError("There is not enough space on the disk")

    # A nested context, not monkeypatch.undo(): undo() reverses EVERY patch on this
    # monkeypatch instance, and the autouse isolated_pam_home fixture shares it -
    # so undo() would point the rest of this test at the real PAM_HOME.
    with monkeypatch.context() as mp:
        mp.setattr(Path, "write_text", dies_half_way)
        with pytest.raises(OSError):
            record_archive_root(ws)

    assert (tmp_path / "photos").is_dir()  # the truncation really does name a folder
    assert [a.path for a in known_archives()] == [
        archive.resolve()
    ]  # ... and pam never sees it


# --- the test suite's own blast-radius limiter (tests/conftest.py) -------------
#
# Both tests below are ABOUT the fixtures, not about pam. They live here because
# PAM_HOME is what app_root() reads, and from M5 Task 5 the suite renames real
# files: a test that escapes isolation stops being one that reads the developer's
# archive pointers and becomes one that renames files under the developer's home.


def test_monkeypatch_undo_cannot_lift_the_pam_home_isolation(monkeypatch, isolated_pam_home):
    # This happened, and luck caught it (M5 Task 3, closing mutation M40). The
    # autouse fixture used to set PAM_HOME through the SAME per-test monkeypatch
    # instance the test body gets, so one undo() reversed pam's isolation along
    # with the patch the test meant to lift. The fixture now writes os.environ
    # directly with a try/finally restore, which undo() has no record of.
    monkeypatch.setenv("PAM_UNRELATED_TO_ISOLATION", "x")
    monkeypatch.undo()
    assert app_root() == isolated_pam_home


def test_the_isolation_tripwire_fires_when_a_test_escapes(tmp_path, isolated_pam_home):
    # The other half: a belt-and-braces teardown check that catches an escape by
    # any route the fixture itself cannot prevent. It can only fire on a test that
    # escapes - and a committed test that escapes would be a failing test - so
    # drive the fixture's own generator instead of waiting for one to appear.
    import sys

    conftest = sys.modules["conftest"]
    generator = conftest._pam_home_stayed_isolated.__wrapped__(isolated_pam_home)
    next(generator)  # setup
    os.environ["PAM_HOME"] = str(tmp_path / "escaped")
    try:
        with pytest.raises(AssertionError, match="escaped isolation"):
            next(generator)  # teardown, with PAM_HOME pointing somewhere else
    finally:
        os.environ["PAM_HOME"] = str(isolated_pam_home)  # or the REAL tripwire fires


def test_a_failed_pointer_write_is_per_process_and_leaves_nothing_behind(tmp_path, monkeypatch):
    # Two halves of one property, both introduced by the M5 Task 3 review.
    #
    # 1. The temp file carries our pid. Two concurrent scans of one archive used to
    #    share the fixed name 'archive_root.txt.tmp': P1 writes half, P2 writes the
    #    whole thing, P1 replaces, and P2's replace raises FileNotFoundError (or
    #    PermissionError on Windows, with the file still open). A leaked temp from a
    #    crashed process can never be consumed or deleted by another one now.
    # 2. A failed write leaves NO trace - not the pointer, not the temp file. The
    #    success-path sibling above already asserts the workspace holds exactly one
    #    'archive_root.txt*' entry; the failure path owes the same. The stub must
    #    write PART of the data before it raises: a stub that raises without
    #    touching the disk leaves nothing to clean up, so it cannot tell the
    #    cleanup from its absence (mutation M53 survived exactly that way).
    archive = tmp_path / "photos"
    archive.mkdir()
    ws = workspace_for(archive)
    attempted: list[Path] = []
    real_write_text = Path.write_text

    def refuses(self, data, *args, **kwargs):
        attempted.append(Path(self))
        real_write_text(self, data[: len(data) // 2], *args, **kwargs)
        raise OSError("There is not enough space on the disk")

    with monkeypatch.context() as mp:
        mp.setattr(Path, "write_text", refuses)
        with pytest.raises(OSError):
            record_archive_root(ws)

    assert [p.name for p in attempted] == [f"{ARCHIVE_POINTER}.{os.getpid()}.tmp"]
    assert [p.name for p in ws.root.iterdir() if p.name.startswith(ARCHIVE_POINTER)] == []


def test_the_pointer_is_written_under_the_workspace_lock(tmp_path):
    # Behaviour regression from M5 Task 3 Step 8. record_archive_root ran BEFORE
    # WorkspaceLock was taken, so two concurrent scans raced on the pointer write
    # and the loser died with a traceback and exit code 2 - outside the lock, so
    # the friendly "another pam is already running" never got a chance to fire.
    # Holding the lock must now stop the scan before it touches the pointer at all.
    import sys

    from pam.errors import WorkspaceLockedError
    from pam.lock import WorkspaceLock
    from pam.scan.runner import run_scan

    archive = tmp_path / "photos"
    archive.mkdir()
    (archive / "a.jpg").write_bytes(b"x")
    ws = workspace_for(archive)
    fake = [sys.executable, str(Path(__file__).parent / "fake_exiftool.py")]

    with WorkspaceLock(ws.lock_path):  # our own live pid: a second holder is refused
        with pytest.raises(WorkspaceLockedError):
            run_scan(archive, exiftool_command=fake)
        assert not (ws.root / ARCHIVE_POINTER).exists()

    assert known_archives() == []


def test_known_archives_skips_a_non_directory_base(isolated_pam_home):
    # "archives" existing as a plain file (not a directory) must not crash.
    isolated_pam_home.mkdir(parents=True, exist_ok=True)
    base = isolated_pam_home / "archives"
    base.write_text("not a directory", encoding="utf-8")
    assert known_archives() == []


def test_known_archives_returns_empty_when_archives_dir_is_missing():
    assert known_archives() == []


def test_resolve_archive_expands_user(tmp_path, monkeypatch):
    archive = tmp_path / "photos"
    archive.mkdir()
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_archive(Path("~") / "photos") == archive.resolve()


def test_resolve_archive_resolves_relative_paths(tmp_path, monkeypatch):
    archive = tmp_path / "sub" / "photos"
    archive.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    assert resolve_archive(Path("sub") / ".." / "sub" / "photos") == archive.resolve()


def test_pointer_round_trips_a_non_ascii_archive_path(tmp_path):
    # Reviewer-verified working today; nothing previously guarded it. The
    # write uses an explicit encoding="utf-8" already (spot-checked in
    # pam/paths.py), so this pins the behavior rather than fixing anything.
    archive = tmp_path / "Muñoz 写真"
    archive.mkdir()
    ws = workspace_for(archive)
    record_archive_root(ws)
    assert [a.path for a in known_archives()] == [archive.resolve()]


def test_workspace_if_exists_creates_nothing(tmp_path):
    # N4: workspace creation is pam scan's exclusive privilege. Every other command
    # asks whether one exists and must leave the filesystem exactly as it found it.
    archive = tmp_path / "photos"
    archive.mkdir()
    assert workspace_if_exists(archive) is None
    assert not app_root().exists()


def test_workspace_if_exists_needs_the_catalog_not_just_the_directory(tmp_path):
    # An interrupted first scan leaves the directory with no catalog.db in it.
    archive = tmp_path / "photos"
    archive.mkdir()
    created = workspace_for(archive)
    assert created.root.is_dir() and not created.db_path.exists()
    assert workspace_if_exists(archive) is None

    created.db_path.write_bytes(b"")  # any catalog at all is enough to answer yes
    found = workspace_if_exists(archive)
    assert found is not None
    assert found == created  # same root, same archive_root - the resolution matches
