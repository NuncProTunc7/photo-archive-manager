r"""Remove the installer downloads earlier `pam update` runs left in TEMP (M15 Task 12-G; Task 12
Stage 2's F3, adopted by the owner 2026-11-20).

`pam update` stages each installer in its own `tempfile.mkdtemp(prefix="pam-update-")` folder and
cannot delete it: setup runs from that file after pam has exited. So the NEXT `pam update` removes
what is left, and only what it can be sure is its own:

  - a folder directly in `tempfile.gettempdir()` whose name starts with `pam-update-`;
  - holding EXACTLY one entry, a regular file named `pam-setup-<version>.exe` (a version pam can
    read), and nothing else - no second file, no subfolder;
  - never a link or a junction, neither the folder nor the file (neither is followed);
  - never while the installer or uninstaller holds `setup.lock`, and never when pam cannot tell
    whether one does (`RunRegistryError`: an unknown state is never "not running");
  - a removal that fails (the file is in use) is skipped, silently.

Cut out of `pam/update/command.py` (Task 12-G, named): that module was at 251 executable lines, and
this is a step of its own with its own seams. Every effect is a parameter, so a test watches the
deletion call itself.
"""

import contextlib
import os
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path

from pam import run_registry
from pam.errors import RunRegistryError
from pam.update.version import VersionError
from pam.update.version import parse as parse_version

STAGING_PREFIX = "pam-update-"  # `command.make_staging`'s prefix
INSTALLER_PREFIX, INSTALLER_SUFFIX = "pam-setup-", ".exe"
REAPED = "pam removed {count} installer download(s) left from earlier updates."


def _plain(entry: os.DirEntry[str], *, folder: bool) -> bool:
    """A real folder (or a regular file), never a link or a junction: nothing is followed."""
    if entry.is_symlink():
        return False
    attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:  # a junction, or any other reparse point
        return False
    return (
        entry.is_dir(follow_symlinks=False) if folder else entry.is_file(follow_symlinks=False)
    )


def leftover_installer(folder: os.DirEntry[str]) -> Path | None:
    """The one installer an earlier update staged in `folder`, or None for any other shape."""
    if not folder.name.startswith(STAGING_PREFIX) or not _plain(folder, folder=True):
        return None
    with os.scandir(folder.path) as listing:
        entries = list(listing)
    if len(entries) != 1 or not _plain(entries[0], folder=False):
        return None
    name = entries[0].name
    if not (name.startswith(INSTALLER_PREFIX) and name.endswith(INSTALLER_SUFFIX)):
        return None
    try:
        parse_version(name[len(INSTALLER_PREFIX) : -len(INSTALLER_SUFFIX)])
    except VersionError:
        return None
    return Path(entries[0].path)


def reap_leftovers(
    temp: Path | None = None,
    setup_running: Callable[[], bool] = run_registry.setup_running,
    remove_file: Callable[[Path], None] = os.remove,
    remove_folder: Callable[[Path], None] = os.rmdir,
) -> int:
    """How many leftover installers it removed (each folder is removed after its file)."""
    try:
        if setup_running():
            return 0
    except RunRegistryError:
        return 0
    try:
        with os.scandir(temp or tempfile.gettempdir()) as listing:
            candidates = list(listing)
    except OSError:
        return 0
    removed = 0
    for folder in candidates:
        try:
            installer = leftover_installer(folder)
            if installer is None:
                continue
            remove_file(installer)
        except OSError:
            continue
        removed += 1
        with contextlib.suppress(OSError):
            remove_folder(Path(folder.path))
    return removed
