"""Filesystem traversal, media typing, change detection, sync/placeholder detection."""

import os
import stat as stat_module
from dataclasses import dataclass
from pathlib import Path

from pam.catalog.repo import FileRow
from pam.config import Config

# Windows cloud-placeholder attribute bits (not all exposed by the stat module).
_FILE_ATTRIBUTE_OFFLINE = 0x1000
_FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
_PLACEHOLDER_MASK = (
    _FILE_ATTRIBUTE_OFFLINE
    | _FILE_ATTRIBUTE_RECALL_ON_OPEN
    | _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
)


@dataclass(frozen=True)
class DiskFile:
    rel_path: str  # '/'-separated, relative to the archive root
    filename: str
    ext: str  # lowercase, no dot
    media_type: str  # 'image' | 'video' | 'sidecar' | 'other'
    size: int
    mtime_ns: int
    placeholder: bool


def ext_of(path: Path) -> str:
    """A file's extension as the walk records it (`DiskFile.ext`): lowercase, no dot.

    `pam plan`'s Z2 check (M14 Task 5h) classifies a catalog path through this and
    `media_type_for` under the CURRENT config, exactly as the next walk would.
    """
    return path.suffix.lower().lstrip(".")


def media_type_for(ext: str, config: Config) -> str:
    if ext in config.image_exts:
        return "image"
    if ext in config.video_exts:
        return "video"
    if ext in config.sidecar_exts:
        return "sidecar"
    return "other"


def is_placeholder(st: os.stat_result) -> bool:
    """Cloud-only placeholder: reading its bytes would trigger a download (spec 5.2)."""
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & _PLACEHOLDER_MASK)


def placeholder_now(path: Path) -> bool:
    """Is `path` a cloud placeholder RIGHT NOW? Design 5.2 rev 34 (V1): the live check.

    THE ONE CHOKE POINT every read of archive content calls IMMEDIATELY BEFORE the read -
    the scanner's batches, `pam verify --deep`'s check 8, the enrichers' loader (both of
    its branches), the thumbnail extractor and the face crop. `files.placeholder` is what
    the LAST SCAN saw, and OneDrive can free a file after that (M13 review R1, built with
    the real OFFLINE attribute). `os.stat` reads a directory entry and hydrates nothing.

    AN `OSError` ANSWERS `False`, deliberately: the read that follows then fails exactly
    as it does today (a vanished file is each reader's own, already-handled case), so this
    function adds a refusal and never a new failure mode.
    """
    try:
        st = os.stat(path)
    except OSError:
        return False
    return is_placeholder(st)


def _is_reparse_dir(entry: os.DirEntry[str]) -> bool:
    """Junction / mount-point directory - never descend (cycle safety)."""
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return True  # unreadable: safest to not descend
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & stat_module.FILE_ATTRIBUTE_REPARSE_POINT)


def walk(archive_root: Path, config: Config) -> tuple[list[DiskFile], list[str]]:
    """Collect every regular file under archive_root, sorted by rel_path.

    Never follows symlinks or directory junctions. Per-file problems are
    recorded in the returned error list, never raised (design spec section 13).
    """
    files: list[DiskFile] = []
    errors: list[str] = []
    root = archive_root.resolve()
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as e:
            errors.append(f"{current}: {e}")
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if not _is_reparse_dir(entry):
                        stack.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError as e:
                errors.append(f"{entry.path}: {e}")
                continue
            path = Path(entry.path)
            ext = ext_of(path)
            files.append(
                DiskFile(
                    rel_path=path.relative_to(root).as_posix(),
                    filename=path.name,
                    ext=ext,
                    media_type=media_type_for(ext, config),
                    size=st.st_size,
                    mtime_ns=st.st_mtime_ns,
                    placeholder=is_placeholder(st),
                )
            )
    files.sort(key=lambda f: f.rel_path)
    return files, errors


@dataclass(frozen=True)
class ScanDiff:
    new: list[DiskFile]
    unchanged: list[tuple[DiskFile, FileRow]]
    changed: list[tuple[DiskFile, FileRow]]
    missing: list[FileRow]


# Media types the scanner opens and reads (spec 5.1, rev 3). diff_catalog's
# 'complete' predicate and runner's to_read filter must agree: if they drift,
# files are either never read or re-read on every scan, both silently.
READ_MEDIA_TYPES = ("image", "video")


def diff_catalog(disk: list[DiskFile], catalog: dict[str, FileRow]) -> ScanDiff:
    """Classify per design spec 5.1. 'unchanged' is the P2 skip-everything gate."""
    new: list[DiskFile] = []
    unchanged: list[tuple[DiskFile, FileRow]] = []
    changed: list[tuple[DiskFile, FileRow]] = []
    seen: set[str] = set()
    for f in disk:
        seen.add(f.rel_path)
        row = catalog.get(f.rel_path)
        if row is None:
            new.append(f)
            continue
        same_stat = row.size == f.size and row.mtime_ns == f.mtime_ns
        same_shape = row.media_type == f.media_type and row.placeholder == f.placeholder
        # rev 3: videos are read too. A pre-rev-3 video row has sha256 NULL,
        # so it classifies as changed exactly once - the one-time backfill.
        complete = (
            f.media_type not in READ_MEDIA_TYPES or f.placeholder or row.sha256 is not None
        )
        if same_stat and same_shape and complete:
            unchanged.append((f, row))
        else:
            changed.append((f, row))
    missing = [
        row for rel, row in catalog.items() if rel not in seen and row.status != "missing"
    ]
    return ScanDiff(new=new, unchanged=unchanged, changed=changed, missing=missing)


_MARKER_DIRS = {
    ".dropbox": "Dropbox",
    ".dropbox.cache": "Dropbox",
    ".tmp.drivedownload": "Google Drive",
    ".tmp.driveupload": "Google Drive",
}
_ONEDRIVE_ENV_VARS = ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")


def detect_sync_root(archive_root: Path) -> str | None:
    """Best-effort sync-provider detection for the loud warning (vision R2)."""
    root = archive_root.resolve()
    for var in _ONEDRIVE_ENV_VARS:
        value = os.environ.get(var)
        if not value:
            continue
        try:
            sync_home = Path(value).resolve()
        except OSError:
            continue
        if root == sync_home or sync_home in root.parents:
            return "OneDrive"
    for candidate in (root, *root.parents):
        for marker, provider in _MARKER_DIRS.items():
            if (candidate / marker).exists():
                return provider
    return None
