"""Workspace resolution: where pam keeps per-archive state, never inside the archive."""

import hashlib
import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from pam.errors import PamError


def app_root() -> Path:
    """PAM_HOME override -> %LOCALAPPDATA%/pam -> ~/.pam (design spec section 3)."""
    override = os.environ.get("PAM_HOME")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "pam"
    return Path.home() / ".pam"


def archive_key(root: Path) -> str:
    """The one spelling of an archive path that pam compares by (spec 3).

    Extracted from `archive_slug` at M6 Task 5, unchanged, because `pam forget` has to
    decide whether the folder the user typed is the folder a pointer names - and that
    is the same question the slug answers by hashing. Two implementations of "are these
    the same archive" would let `pam forget D:\\photos` refuse a pointer that
    `pam scan D:\\Photos` had written.

    NFC-normalized so unicode-equivalent paths (mainly a macOS concern) compare equal,
    lowercased because Windows paths are case-insensitive, and separator- and
    trailing-slash-normalized. `resolve()` is non-strict, so an UNREACHABLE path still
    gets a key - which is required here: the archives `pam forget` exists for are
    exactly the ones that are not there any more.
    """
    return unicodedata.normalize(
        "NFC", str(root.resolve()).replace("\\", "/").rstrip("/").lower()
    )


def archive_slug(root: Path) -> str:
    digest = hashlib.sha256(archive_key(root).encode("utf-8")).hexdigest()[:8]
    name = "".join(c if c.isalnum() else "-" for c in root.name.lower())
    name = "-".join(part for part in name.split("-") if part) or "archive"
    return f"{name}-{digest}"


@dataclass(frozen=True)
class Workspace:
    root: Path  # the workspace directory (app data)
    archive_root: Path  # the user's photo folder (never written to)

    @property
    def db_path(self) -> Path:
        return self.root / "catalog.db"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def thumbs_dir(self) -> Path:
        return self.root / "thumbs"

    @property
    def config_path(self) -> Path:
        return self.root / "pam.toml"

    @property
    def lock_path(self) -> Path:
        return self.root / "pam.lock"


def workspace_for(archive_root: Path) -> Workspace:
    """Resolve the workspace, CREATING it. Only pam scan may do this."""
    ws_root = app_root() / "archives" / archive_slug(archive_root)
    ws = Workspace(root=ws_root, archive_root=archive_root.resolve())
    for d in (ws.root, ws.logs_dir, ws.reports_dir, ws.thumbs_dir):
        d.mkdir(parents=True, exist_ok=True)
    return ws


def workspace_if_exists(archive_root: Path) -> Workspace | None:
    """Resolve the workspace WITHOUT creating anything; None if it is not there.

    Every command except pam scan must use this. Creating a workspace as a side
    effect of a read-only command makes a typo'd --archive indistinguishable from a
    genuine all-conforming archive: pam would report "Nothing to rename." and exit
    0 for a folder it has never looked at (vision principle 9 - never summarize
    failure as success). The catalog must exist too, not just the directory: an
    interrupted first scan can leave the directory behind with no catalog in it.
    """
    ws_root = app_root() / "archives" / archive_slug(archive_root)
    ws = Workspace(root=ws_root, archive_root=archive_root.resolve())
    if not ws.root.is_dir() or not ws.db_path.exists():
        return None
    return ws


ARCHIVE_POINTER = "archive_root.txt"


def record_archive_root(ws: Workspace) -> None:
    """Leave a breadcrumb so later commands can find this archive without a path.

    The workspace directory name is a one-way hash of the archive path (spec 3),
    so the pointer is the only way back. Written on every scan; workspaces made
    before this existed gain one on their next scan.

    Finding 12: written atomically - full content to a temp file in the SAME
    directory, then os.replace. The `if text:` guard in known_archives() catches a
    zero-byte pointer, but a TRUNCATED one ('C:/Photos/2019' cut to 'C:/Photos')
    resolves to a different existing directory and passes every check below it.
    That is the dangerous failure, and an atomic write prevents a TORN write from
    ever being visible: os.replace guarantees atomic visibility, not durability -
    there is no fsync here, so a power loss can still lose the whole write. That is
    fine. Losing the pointer costs one `pam scan` to rewrite it, and the zeros a
    lost write typically leaves are filtered by the `if not text: continue` guard in
    known_archives(). Half a path passing is_dir() is the failure worth engineering
    against, and only atomicity prevents that one - which matters MORE under spec 3
    rev 8, not less: a truncated path that names nothing is now reported as an
    unreachable archive rather than silently dropped, so the user would be asked to
    choose between a real folder and a fragment of one.
    os.replace is correct HERE and nowhere near the archive: this is a small
    pam-owned metadata file whose previous content is meant to be replaced. The
    rename path uses os.rename precisely because it refuses an existing target.

    The temp name carries our pid so two processes can never write, replace or
    consume each other's temp file. Callers must hold the WorkspaceLock as well -
    that is what turns "two concurrent scans" into one friendly "another pam is
    running" instead of a FileNotFoundError from a replace that lost its source.
    Belt and braces on purpose: the lock fixes the race, the unique name means a
    temp file leaked by a crashed process can never be picked up by another.
    """
    target = ws.root / ARCHIVE_POINTER
    tmp = ws.root / f"{ARCHIVE_POINTER}.{os.getpid()}.tmp"
    try:
        tmp.write_text(str(ws.archive_root), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        # A failed write leaves no trace: the half-written temp file is ours alone,
        # and the previous pointer is still the one on disk.
        tmp.unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class KnownArchive:
    """An archive pam has scanned, and whether pam can see it right now (spec 3 rev 8).

    The two are separate facts on purpose. Reachability is a property of this instant -
    a drive gets unplugged and plugged back in - while "pam has scanned this" is a
    permanent fact about the workspace, and collapsing them is what made pam claim it
    had never seen a folder it had scanned.
    """

    path: Path
    reachable: bool


def known_archives() -> list[KnownArchive]:
    """Every archive pam has scanned, reachable or not (spec 3 rev 8).

    An unplugged drive, a disconnected network share and a deleted folder are
    INDISTINGUISHABLE to the filesystem, so pam does not guess between them: it reports
    what it knows and what it can currently see, and lets the user say which they meant.

    This rev partially reverses M4 finding 12, which dropped unreachable pointers so
    that `scan OldName` -> rename the folder -> `scan again` left a bare `pam plan`
    working. That bought convenience by DISCARDING the information that distinguishes
    "I have one archive" from "I have two and one is unplugged" - and spec 7.2's
    recovery branches on the disk, so running against an archive pam cannot see would
    resolve every `intent` row terminally `failed` for renames that may well have
    completed on that disk (J9). The release valve for a genuinely dead pointer is
    `pam forget`, which M6 must therefore ship.

    A pointer that names something which EXISTS but is not a directory is excluded
    entirely rather than reported unreachable: that is a corrupt or tampered pointer,
    and unlike an unplugged drive it can never become an archive.
    """
    base = app_root() / "archives"
    if not base.is_dir():
        return []
    found: list[KnownArchive] = []
    for pointer in sorted(base.glob(f"*/{ARCHIVE_POINTER}")):
        text = pointer.read_text(encoding="utf-8").strip()
        if not text:
            continue
        path = Path(text)
        if path.is_dir():
            found.append(KnownArchive(path, reachable=True))
        elif not path.exists():
            found.append(KnownArchive(path, reachable=False))
    return found


def pointer_for(root: Path) -> Path | None:
    """The PAM_HOME pointer file naming `root`, or None if pam does not know it.

    Found by walking the same pointers `known_archives` reads and comparing
    `archive_key`s, NOT by computing `archive_slug(root)` and looking in that one
    directory. The two agree today, and the walk is the safer of the two: `pam forget`
    lists the known archives when it refuses, so a pointer this function cannot find
    but that listing can show is a dead end - the user would be told to pick from a
    list containing the answer pam had just rejected. A pointer whose text is empty is
    skipped, exactly as `known_archives` skips it.
    """
    base = app_root() / "archives"
    if not base.is_dir():
        return None
    wanted = archive_key(root)
    for pointer in sorted(base.glob(f"*/{ARCHIVE_POINTER}")):
        text = pointer.read_text(encoding="utf-8").strip()
        if text and archive_key(Path(text)) == wanted:
            return pointer
    return None


def forget_archive(root: Path) -> Path | None:
    """Delete the pointer naming `root`; return its workspace directory, or None.

    THE ONLY THING THIS DELETES IS THE POINTER (spec 3, M6 plan clarification 5). The
    workspace directory it returns is left exactly as it was - catalog, journal, logs
    and reports - and `pam scan <root>` writes the pointer back, so a `forget` is
    reversible in one command and loses nothing. Deleting the catalog would be the one
    action in pam that destroys journal history, and spec 7.0 J2 says journal rows are
    never deleted; doing it by removing the file they live in is the same thing by
    another route.

    `unlink`, not a move: pam has exactly two sanctioned filesystem-move call sites
    (`tests/guards.py::ALLOWED_MOVE_SITES`) and this is not a third.
    """
    pointer = pointer_for(root)
    if pointer is None:
        return None
    pointer.unlink()
    return pointer.parent


# Spelled once: `resolve_archive` says it, and so does the folder chooser (`webui/folders.py`)
# when a folder it was asked to list cannot be read.
CANNOT_SEE_FOLDER = (
    "pam cannot see this folder, so it will not touch anything:\n  {root}\n"
    "If the photos are on a drive or a network folder, connect it and run "
    "the command again."
)


def resolve_archive(explicit: Path | None) -> Path:
    """spec 3 rev 8: the given folder, else the single reachable known archive, else ask.

    Never auto-selects while any known archive is unreachable. The unreachable one may
    be the one the user meant, and applying or undoing against the wrong archive is the
    least acceptable error pam can make - so the tie is broken by asking, never by
    picking the survivor (vision: loud failure over silent wrong).
    """
    if explicit is not None:
        root = explicit.expanduser().resolve()
        if not root.is_dir():
            # Refused BEFORE any workspace is opened. spec 7.2's recovery runs at the
            # start of every command and branches on the disk; handing it a root pam
            # cannot see would record terminal outcomes for an invisible archive (J9).
            raise PamError(CANNOT_SEE_FOLDER.format(root=root))
        return root
    known = known_archives()
    if not known:
        raise PamError(
            "pam has not scanned any folder yet, so there is nothing to plan. "
            "Run: pam scan <your photo folder>"
        )
    if len(known) == 1:
        only = known[0]
        if only.reachable:
            return only.path
        raise PamError(
            "pam has scanned this folder before, but cannot see it right now:\n"
            f"  {only.path}\n"
            "If the photos are on a drive or a network folder, connect it and run the "
            "command again. If you moved them, run: pam scan <the new folder>"
        )
    listed = "\n".join(
        f"  {a.path}"
        + ("" if a.reachable else "            (pam cannot see this one right now)")
        for a in known
    )
    raise PamError(
        "pam knows about more than one photo folder, so it needs to be told which "
        f"one to use:\n{listed}\nAdd --archive <folder> to your command."
    )
