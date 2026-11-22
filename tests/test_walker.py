import os
from types import SimpleNamespace

import pytest

from pam.catalog.repo import FileRow
from pam.config import Config
from pam.scan.walker import (
    DiskFile,
    detect_sync_root,
    diff_catalog,
    is_placeholder,
    media_type_for,
    walk,
)

CFG = Config()


def _fake_stat(attrs):
    return SimpleNamespace(st_file_attributes=attrs)


def test_media_type_for():
    assert media_type_for("jpg", CFG) == "image"
    assert media_type_for("cr2", CFG) == "image"
    assert media_type_for("mp4", CFG) == "video"
    assert media_type_for("xmp", CFG) == "sidecar"
    assert media_type_for("txt", CFG) == "other"
    assert media_type_for("", CFG) == "other"


def test_is_placeholder_attribute_bits():
    assert is_placeholder(_fake_stat(0x400000)) is True  # RECALL_ON_DATA_ACCESS
    assert is_placeholder(_fake_stat(0x1000)) is True  # OFFLINE
    assert is_placeholder(_fake_stat(0x20)) is False  # plain ARCHIVE attribute
    assert is_placeholder(SimpleNamespace()) is False  # POSIX: no attribute field


def test_walk_collects_files_sorted_with_posix_rel_paths(tmp_path):
    root = tmp_path / "photos"
    (root / "b").mkdir(parents=True)
    (root / "a").mkdir()
    (root / "b" / "two.JPG").write_bytes(b"2")
    (root / "a" / "one.jpg").write_bytes(b"1")
    (root / "note.txt").write_bytes(b"t")
    files, errors = walk(root, CFG)
    assert errors == []
    assert [f.rel_path for f in files] == ["a/one.jpg", "b/two.JPG", "note.txt"]
    two = files[1]
    assert two == DiskFile(
        rel_path="b/two.JPG",
        filename="two.JPG",
        ext="jpg",
        media_type="image",
        size=1,
        mtime_ns=two.mtime_ns,
        placeholder=False,
    )
    assert two.mtime_ns > 0


def test_walk_does_not_follow_directory_symlinks(tmp_path):
    root = tmp_path / "photos"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "trap.jpg").write_bytes(b"x")
    try:
        os.symlink(outside, root / "link", target_is_directory=True)
    except OSError:
        pytest.skip("no symlink privilege on this machine")
    files, errors = walk(root, CFG)
    assert files == []
    assert errors == []


def test_walk_missing_root_reports_error_not_raise(tmp_path):
    files, errors = walk(tmp_path / "nope", CFG)
    assert files == []
    assert len(errors) == 1


def disk_file(rel_path="a.jpg", **overrides):
    kwargs = dict(
        rel_path=rel_path,
        filename=rel_path.rsplit("/", 1)[-1],
        ext=rel_path.rsplit(".", 1)[-1].lower(),
        media_type="image",
        size=100,
        mtime_ns=1_000,
        placeholder=False,
    )
    kwargs.update(overrides)
    return DiskFile(**kwargs)


def file_row(rel_path="a.jpg", **overrides):
    kwargs = dict(
        id=1,
        rel_path=rel_path,
        filename=rel_path.rsplit("/", 1)[-1],
        ext=rel_path.rsplit(".", 1)[-1].lower(),
        media_type="image",
        size=100,
        mtime_ns=1_000,
        sha256="abc",
        status="present",
        placeholder=False,
        companion_group_id=None,
    )
    kwargs.update(overrides)
    return FileRow(**kwargs)


def test_diff_new_file():
    diff = diff_catalog([disk_file()], {})
    assert [f.rel_path for f in diff.new] == ["a.jpg"]
    assert diff.unchanged == [] and diff.changed == [] and diff.missing == []


def test_diff_unchanged_when_stat_and_hash_complete():
    f, row = disk_file(), file_row()
    diff = diff_catalog([f], {"a.jpg": row})
    assert diff.unchanged == [(f, row)]
    assert diff.new == [] and diff.changed == [] and diff.missing == []


def test_diff_changed_on_size_or_mtime():
    assert diff_catalog([disk_file(size=999)], {"a.jpg": file_row()}).changed
    assert diff_catalog([disk_file(mtime_ns=2)], {"a.jpg": file_row()}).changed


def test_diff_image_with_null_sha_is_retried_as_changed():
    diff = diff_catalog([disk_file()], {"a.jpg": file_row(sha256=None)})
    assert len(diff.changed) == 1


def test_diff_unread_media_with_null_sha_is_unchanged():
    # Sidecars and 'other' files are never read, so a NULL sha256 is complete.
    f = disk_file(rel_path="a.xmp", media_type="sidecar")
    row = file_row(rel_path="a.xmp", media_type="sidecar", sha256=None)
    diff = diff_catalog([f], {"a.xmp": row})
    assert diff.unchanged == [(f, row)]


def test_diff_pre_rev3_video_with_null_sha_is_changed():
    # rev 3: videos are read now, so a pre-rev-3 video row (sha256 NULL) is
    # incomplete and re-reads exactly once - the one-time backfill.
    f = disk_file(rel_path="clip.mp4", media_type="video")
    row = file_row(rel_path="clip.mp4", media_type="video", sha256=None)
    diff = diff_catalog([f], {"clip.mp4": row})
    assert diff.changed == [(f, row)]


def test_diff_placeholder_video_without_sha_is_unchanged():
    # Placeholder videos must never be read, even after rev 3 - a NULL
    # sha256 on a placeholder is still complete, not a backfill trigger.
    f = disk_file(rel_path="clip.mp4", media_type="video", placeholder=True)
    row = file_row(rel_path="clip.mp4", media_type="video", placeholder=True, sha256=None)
    diff = diff_catalog([f], {"clip.mp4": row})
    assert diff.unchanged == [(f, row)]


def test_diff_media_type_change_is_changed():
    f = disk_file(media_type="other")
    diff = diff_catalog([f], {"a.jpg": file_row(media_type="image")})
    assert len(diff.changed) == 1


def test_diff_materialized_placeholder_is_changed():
    f = disk_file(placeholder=False)
    row = file_row(placeholder=True, sha256=None)
    diff = diff_catalog([f], {"a.jpg": row})
    assert len(diff.changed) == 1


def test_diff_placeholder_image_without_sha_is_unchanged():
    f = disk_file(placeholder=True)
    row = file_row(placeholder=True, sha256=None)
    diff = diff_catalog([f], {"a.jpg": row})
    assert diff.unchanged == [(f, row)]


def test_diff_missing_and_already_missing():
    gone = file_row(rel_path="gone.jpg")
    long_gone = file_row(rel_path="long-gone.jpg", status="missing")
    diff = diff_catalog([], {"gone.jpg": gone, "long-gone.jpg": long_gone})
    assert diff.missing == [gone]  # already-missing rows are not re-marked


def test_detect_sync_root_onedrive_env(tmp_path, monkeypatch):
    onedrive = tmp_path / "OneDrive"
    archive = onedrive / "Pictures"
    archive.mkdir(parents=True)
    monkeypatch.setenv("OneDrive", str(onedrive))
    assert detect_sync_root(archive) == "OneDrive"
    assert detect_sync_root(onedrive) == "OneDrive"


def test_detect_sync_root_outside_onedrive(tmp_path, monkeypatch):
    monkeypatch.setenv("OneDrive", str(tmp_path / "OneDrive"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert detect_sync_root(elsewhere) is None


def test_detect_sync_root_dropbox_marker(tmp_path, monkeypatch):
    monkeypatch.delenv("OneDrive", raising=False)
    root = tmp_path / "Dropbox" / "Photos"
    root.mkdir(parents=True)
    (tmp_path / "Dropbox" / ".dropbox").mkdir()
    assert detect_sync_root(root) == "Dropbox"


def test_detect_sync_root_none(tmp_path, monkeypatch):
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        monkeypatch.delenv(var, raising=False)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert detect_sync_root(plain) is None


def test_read_media_types_is_the_shared_constant():
    # C9: the walker's 'complete' predicate and the runner's to_read filter must
    # agree. Disagreeing silently either never reads a type or re-reads it every
    # scan, so the tuple lives in one place.
    from pam.scan import runner, walker

    assert walker.READ_MEDIA_TYPES == ("image", "video")
    assert runner.walker.READ_MEDIA_TYPES is walker.READ_MEDIA_TYPES


def test_aae_is_typed_as_a_sidecar():
    from pam.config import Config
    from pam.scan.walker import media_type_for

    cfg = Config()
    assert media_type_for("aae", cfg) == "sidecar"
    assert media_type_for("xmp", cfg) == "sidecar"
    assert media_type_for("txt", cfg) == "other"
