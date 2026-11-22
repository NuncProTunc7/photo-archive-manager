"""E12: the compatibility ledger - the newest catalog update any pam on this PC has reached.

WHY IT EXISTS. An installer that replaces pam with an OLDER pam would leave every catalog the newer
one migrated unopenable (E1 refuses it, correctly). The installer (M15 Task 13) and `pam update`
(Task 12) read this file and refuse a payload whose newest migration sorts below it. It lives
outside every `PAM_HOME`, because the installer knows no workspace.

THE FORM (rev 3, second gate blocker 2): an APPEND-ONLY text file, one migration filename per
line. Its value is the MAX over the lines that match the migration-name grammar, so a truncated or
garbled line is ignored, and nothing is ever rewritten:
  - every append is ONE `write` of under 64 bytes on an append-mode handle, so two pams appending
    at once cannot lose a value (there is no read-modify-write), and no `os.replace` is needed -
    `ALLOWED_MOVE_SITES` does not change;
  - ON WINDOWS THE HANDLE IS OPENED WITH `FILE_APPEND_DATA` ALONE. The C runtime's `O_APPEND` is
    a seek-to-end before each write, not an atomic append: measured on this machine, three
    processes appending 2000 lines each through `os.open(..., O_APPEND)` lost 761 of 6000. With
    `FILE_APPEND_DATA` and no `FILE_WRITE_DATA`, the system appends every write, and the same run
    kept all 6000.

THE TWO WRITERS (both in `pam/catalog/db.py`):
  - RESERVATION, before a migration: `migrate` appends the newest filename it is about to apply
    and fsyncs BEFORE its first `executescript`. A crash in between leaves the ledger HIGHER than
    the catalog - a conservative false positive, repaired by the sentence `NEEDS_NEWER` gives.
    A reservation that cannot be written refuses the migration (`reserve` raises).
  - REGISTRATION, on every compatible open: `check_compatible` passes the catalog's newest
    applied filename, appended only if it is higher than the ledger's value, so a catalog copied
    or restored from elsewhere is registered the first time this pam opens it. A failed
    registration is logged and is not fatal - the catalog itself is fine.

READERS: a missing ledger is no constraint; an UNREADABLE one is refused (`read_level` raises).
"""

import logging
import os
import re
import sys
from pathlib import Path

from pam.errors import LedgerError

# The one grammar of a migration filename. `pam/catalog/db.py` inlines a name it matched into SQL,
# and this module and the release manifest read names written by other programs. `\Z`, never `$`:
# `$` also matches before a trailing newline, and a ledger line or a manifest entry can carry one.
MIGRATION_NAME = re.compile(r"^[0-9]{4}_[a-z0-9_]+\.sql\Z")

# The file the installer reads. Tests set it (tests/conftest.py, `isolated_schema_ledger`), as
# they set PAM_HOME; nothing else should.
LEDGER_ENV = "PAM_SCHEMA_LEDGER"

# One append is one `write` of fewer bytes than this, newline included.
MAX_LINE_BYTES = 64

UNREADABLE = (
    "pam cannot read {path} ({error}). That file records the newest catalog on this PC, so pam"
    " cannot tell whether this version can open your catalogs. Make it readable, then try again."
)
RESERVATION_FAILED = (
    "pam could not record the catalog update it is about to make in {path} ({error}), so it has"
    " not updated the catalog. Make that file writable, then try again."
)
# The E12 refusal's reason, shared by `pam update` and the installer (Tasks 12-13); each puts its
# own leading clause before it ("pam update will not install pam {offered}: "). The maintainer's wording,
# adopted 2026-11-20 (Task 12-G, F4): a migration number is never named as a pam version, and the
# refusal says that nothing was changed.
NEEDS_NEWER = (
    "a newer pam has already updated a catalog on this PC, and pam {offered} could not open it."
    " Nothing was changed. If you no longer use any catalog a newer pam opened, delete {path} and"
    " try again."
)

_log = logging.getLogger("pam.ledger")


def ledger_path() -> Path:
    """`%LOCALAPPDATA%\\pam-install\\schema-ledger`; without LOCALAPPDATA (a pip install off
    Windows, where the ledger is advisory) under `app_root()`'s platform default, `~/.pam`.

    INDEPENDENT OF `PAM_HOME`: the installer reads one fixed file, so a custom `PAM_HOME` must not
    move what pam writes away from it."""
    override = os.environ.get(LEDGER_ENV)
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "pam-install" / "schema-ledger"
    return Path.home() / ".pam" / "schema-ledger"


def read_level(path: Path | None = None) -> str | None:
    """The newest migration filename the ledger names; None when it names none or is missing.

    Raises `LedgerError` when the file exists and cannot be read: an unknown constraint is
    refused, never read as no constraint."""
    path = path or ledger_path()
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise LedgerError(UNREADABLE.format(path=path, error=error)) from None
    valid = []
    for raw in data.split(b"\n"):
        try:
            line = raw.removesuffix(b"\r").decode("ascii")
        except UnicodeDecodeError:
            continue
        if MIGRATION_NAME.match(line):
            valid.append(line)
    return max(valid, default=None)


def reserve(name: str, path: Path | None = None) -> None:
    """Append `name` and fsync it, BEFORE a migration that brings a catalog to `name`.

    Raises `LedgerError` naming the file when it cannot: the caller must not migrate."""
    path = path or ledger_path()
    try:
        _append(path, name)
    except OSError as error:
        raise LedgerError(RESERVATION_FAILED.format(path=path, error=error)) from None


def register(name: str, path: Path | None = None) -> None:
    """Append `name` if it is higher than the ledger's value. Never raises: logged instead."""
    path = path or ledger_path()
    try:
        level = read_level(path)
        if level is None or name > level:
            _append(path, name)
    except (OSError, LedgerError) as error:
        _log.warning("could not register catalog update %s in %s: %s", name, path, error)


def _append(path: Path, name: str) -> None:
    """ONE write of `name` and a newline on an append-only handle, then fsync."""
    line = f"{name}\n".encode("ascii")
    if not MIGRATION_NAME.match(name) or len(line) >= MAX_LINE_BYTES:
        raise ValueError(f"not a ledger line: {name!r}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = _open_append(path)
    try:
        written = os.write(fd, line)
        if written != len(line):
            raise OSError(f"short write to the ledger: {written} of {len(line)} bytes")
        os.fsync(fd)
    finally:
        os.close(fd)


def _open_append(path: Path) -> int:
    if sys.platform == "win32":
        return _win_open_append(path)
    return os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)


def _win_open_append(path: Path) -> int:
    """A file descriptor on a handle with `FILE_APPEND_DATA` and not `FILE_WRITE_DATA`."""
    import ctypes
    import msvcrt

    file_append_data = 0x0004
    synchronize = 0x00100000
    share_all = 0x1 | 0x2 | 0x4  # read, write, delete: never block another pam or a reader
    open_always = 4
    normal = 0x80
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    )
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel32.CreateFileW(
        str(path), file_append_data | synchronize, share_all, None, open_always, normal, None
    )
    if handle is None or handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(handle, os.O_APPEND)
    except BaseException:
        kernel32.CloseHandle(handle)
        raise
