"""The folder chooser (U13): `GET /api/folders`, and the ONE network refusal.

A browser cannot hand a page a filesystem path, so Welcome's folder browser is this read-only
endpoint on `pam start`'s server (`launcher.StartServer` registers it, bound or unbound).

ONE PARAMETER, `path`. Without it the answer is the chooser's top level: the known archives
(`paths.known_archives()`, pam's own pointers) and the drives. With it, the folders directly
inside that folder. Any other parameter is refused by name, in `items`' copy (Task 0D):

    GET /api/folders            -> {"path": null, "parent": null, "folders": [],
                                    "drives": ["C:\\\\", ...],
                                    "known": [{"path": ..., "reachable": bool}, ...]}
    GET /api/folders?path=P     -> {"path": "<P resolved>", "parent": "<its parent>" | null,
                                    "folders": [{"name": ..., "path": ...}, ...],
                                    "drives": null, "known": null}

NOTHING IN THE USER'S FOLDERS IS OPENED (vision R2). A listing is one `os.scandir` of the folder
asked for: directories only (never files, never counts), sorted, hidden and system entries left
out. `DirEntry.is_dir` and, on Windows, `DirEntry.stat` answer from the directory listing itself;
no file is opened, so no cloud-only file is hydrated. The known archives are read only at the top
level - their pointers are pam's own files under PAM_HOME, never the archive's.

THE NETWORK REFUSAL (`network_refusal`) is used by this endpoint AND by `launcher.bind`, which
must not trust the page. U13: a path beginning `\\\\` (UNC) and a drive Windows reports as a
network drive (`GetDriveTypeW` == `DRIVE_REMOTE`) are refused, because the operating system
reaching an SMB share IS network activity in pam's process (vision S4). It is checked on the path
AS GIVEN, before anything touches the filesystem - `resolve()` and `is_dir()` on a share would
already be the network call - and again on the resolved path (a junction can lead to a share).
On POSIX a path under a mount whose type is `nfs`, `cifs` or `smbfs` in `/proc/mounts` is refused
when that file is readable; when it is not (macOS, BSD) the path is ALLOWED - a stated
limitation, not a guarantee. The drive list leaves out the drives this refusal would refuse, so
listing the drives never probes a share either.
"""

import ntpath
import os
import re
import stat
import string
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pam.errors import PamError
from pam.paths import CANNOT_SEE_FOLDER, known_archives, resolve_archive
from pam.webui.api import (
    BAD_REQUEST_STATUS,
    CONFLICT_STATUS,
    ApiError,
    ApiRequest,
    _bad_request,
    _single,
)

if TYPE_CHECKING:  # pragma: no cover - import for typing only, never at runtime
    from pam.webui.server import ReviewServer

# U13, word for word.
NETWORK_REFUSAL = (
    "pam works on folders on this computer. Copy the photos to this computer first."
)
# The chooser's and bind's 400 for a missing or relative path (4b's sentence, spelled once here).
PATH_REQUIRED = "path: give the full path of a folder."

# The endpoint's one query parameter. Anything else is refused by name.
FOLDER_PARAMETERS = ("path",)

# winbase.h: GetDriveTypeW's answer for a network drive.
DRIVE_REMOTE = 4
# U13 / the plan (Task 4 Step 5): the POSIX mount types refused.
NETWORK_FS_TYPES = frozenset({"nfs", "cifs", "smbfs"})
PROC_MOUNTS = "/proc/mounts"

# winnt.h: what makes an entry hidden from the chooser on Windows.
_HIDDEN_OR_SYSTEM = stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM


def api_folders(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """`GET /api/folders[?path=P]`: the chooser's top level, or one folder's folders."""
    unknown = sorted(name for name in request.query if name not in FOLDER_PARAMETERS)
    if unknown:
        raise _bad_request(unknown[0], f'"{unknown[0]}" is not something pam can filter by.')
    text = _single(request, "path")
    if text is None:
        return {
            "path": None,
            "parent": None,
            "folders": [],
            "drives": drives(),
            "known": [
                {"path": str(a.path), "reachable": a.reachable} for a in known_archives()
            ],
        }
    path = Path(text.strip()).expanduser() if text.strip() else None
    if path is None or not path.is_absolute():
        raise ApiError(BAD_REQUEST_STATUS, PATH_REQUIRED)
    root = checked_folder(path)
    parent = root.parent if root.parent != root else None
    return {
        "path": str(root),
        "parent": None if parent is None else str(parent),
        "folders": [{"name": name, "path": str(root / name)} for name in list_folders(root)],
        "drives": None,
        "known": None,
    }


def checked_folder(path: Path) -> Path:
    """`path` resolved, or a 409: a network location (U13) or a folder pam cannot see."""
    refusal = network_refusal(path)
    if refusal is not None:
        raise ApiError(CONFLICT_STATUS, refusal)
    try:
        root = resolve_archive(path)
    except PamError as cannot_see:
        raise ApiError(CONFLICT_STATUS, str(cannot_see)) from None
    refusal = network_refusal(root)
    if refusal is not None:
        raise ApiError(CONFLICT_STATUS, refusal)
    return root


def list_folders(root: Path) -> list[str]:
    """The names of the folders directly inside `root`: directories only, sorted, hidden and
    system entries left out. One `os.scandir`; nothing is opened."""
    names = []
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if entry.is_dir() and not _hidden(entry):
                    names.append(entry.name)
    except OSError:
        raise ApiError(CONFLICT_STATUS, _cannot_see(root)) from None
    return sorted(names, key=lambda name: (name.casefold(), name))


def _hidden(entry: os.DirEntry[str]) -> bool:
    if entry.name.startswith("."):
        return True
    if sys.platform == "win32":
        # On Windows the attributes come with the listing: no file is opened to read them.
        attributes = entry.stat(follow_symlinks=False).st_file_attributes
        return bool(attributes & _HIDDEN_OR_SYSTEM)
    return False


def _cannot_see(root: Path) -> str:
    """`resolve_archive`'s own sentence for a folder pam cannot see (UX 8: one per condition)."""
    return CANNOT_SEE_FOLDER.format(root=root)


def drives() -> list[str]:
    """The drives to browse from: `os.listdrives()` where Python has it, else `A:\\`..`Z:\\`
    probed with `os.path.isdir`. Network drives are left out BEFORE any probe."""
    listdrives = getattr(os, "listdrives", None)
    if listdrives is not None:
        return sorted(d for d in listdrives() if network_refusal(Path(d)) is None)
    found = []
    for letter in string.ascii_uppercase:
        drive = f"{letter}:\\"
        if network_refusal(Path(drive)) is None and os.path.isdir(drive):
            found.append(drive)
    return found


def network_refusal(path: Path) -> str | None:
    """U13's sentence when `path` is a network location, else None. THE one refusal: the
    chooser and `launcher.bind` both call it. Reads nothing in `path` itself."""
    return NETWORK_REFUSAL if _is_network(str(path), sys.platform) else None


def _is_network(text: str, platform: str) -> bool:
    if text.startswith("\\\\"):
        return True
    if platform == "win32":
        drive = ntpath.splitdrive(text)[0]
        # A letter drive only: asking about a UNC root would itself reach the network.
        return len(drive) == 2 and drive[1] == ":" and _drive_type(drive + "\\") == DRIVE_REMOTE
    return _mount_type(text) in NETWORK_FS_TYPES


def _kernel32() -> Any:
    import ctypes  # the `lock.py` precedent: stdlib, loaded only on the Windows branch

    return ctypes.WinDLL("kernel32", use_last_error=True)


def _drive_type(root: str) -> int:
    """`GetDriveTypeW(root)`: answered from the local drive table, not by the drive's server."""
    import ctypes

    kernel32 = _kernel32()
    kernel32.GetDriveTypeW.argtypes = (ctypes.c_wchar_p,)
    kernel32.GetDriveTypeW.restype = ctypes.c_uint
    return int(kernel32.GetDriveTypeW(root))


_OCTAL = re.compile(r"\\([0-7]{3})")


def _mount_type(text: str) -> str | None:
    """The fs type of the longest `/proc/mounts` mount point holding `text`, or None when the
    table cannot be read (then the path is allowed - the stated limitation)."""
    try:
        with open(PROC_MOUNTS, encoding="utf-8", errors="replace") as table:
            lines = table.read().splitlines()
    except OSError:
        return None
    best: tuple[int, str] | None = None
    for line in lines:
        fields = line.split()
        if len(fields) < 3:
            continue
        point = _OCTAL.sub(lambda m: chr(int(m.group(1), 8)), fields[1])
        prefix = point.rstrip("/") + "/"
        if (text == point or text.startswith(prefix)) and (
            best is None or len(point) > best[0]
        ):
            best = (len(point), fields[2])
    return None if best is None else best[1]
