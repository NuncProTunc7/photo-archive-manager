r"""E11: the process-exclusion protocol, pam's side - every pam says it is running, and none runs
while pam is being installed or updated.

THE DESIGN IS SPIKE 10C's, AS ADMITTED (`docs/implementation-notes/m15-spikes/10c/10c-record.md`
section 1 and its amendments A1-A4; adopted by the owner 2026-11-17). The mutex sketch of the M15
plan's rev 2 is superseded: a mutex cannot say whether ANOTHER process holds it, and `Local\` is
per session.

THE REGISTRY ROOT is the compatibility ledger's install root, `%LOCALAPPDATA%\pam-install`
(`~/.pam` without LOCALAPPDATA), INDEPENDENT OF `PAM_HOME`: the installer knows no workspace.
`PAM_RUN_REGISTRY` overrides it, for tests only (`tests/conftest.py`), as `PAM_SCHEMA_LEDGER`
overrides the ledger. Under it:

    run\<pid>.lock  one per running pam, opened FIRST THING by `pam.entry.main` (before `pam.cli` is
                    imported, Task 13A-F1) and held until the command ends: GENERIC_READ|GENERIC_WRITE|DELETE, share mode 0 and
                    FILE_FLAG_DELETE_ON_CLOSE (A1). NEVER FILE_SHARE_DELETE: a holder that grants
                    it is invisible, because on this Windows `os.remove` deletes the name while it
                    is held (10C M1). The kernel closes the handle when the process dies however it
                    dies, so the name goes with it (M2: within 5 ms of a TerminateProcess).
    setup.lock      held the same way by the installer and the uninstaller for their whole run
                    (Task 13B). pam never creates it.

THE PROBE (A2) is an open, never `os.remove`: CreateFileW(DELETE, share R|W|D, OPEN_EXISTING,
FILE_FLAG_DELETE_ON_CLOSE). Opened -> nobody holds it, it is stale, and closing our handle deletes
it; SHARING (32) -> held; NOT FOUND (2, 3) -> absent; DENIED (5) -> a concurrent prober's delete is
pending OR an ACL: retried for 2 s, then REFUSED, never read as absent; anything else -> refused.

THE ORDER IS LOAD-BEARING (Dekker): pam takes its run file BEFORE it probes `setup.lock`, and setup
takes `setup.lock` before it enumerates the run files, so whichever starts second sees the other.

FAIL CLOSED (owner, 2026-11-17, 10C question 2): a pam that cannot create its run file refuses,
with a sentence naming the folder, exit 1. Running unregistered would let an installer replace pam
under it. Ordinary pams never refuse because of EACH OTHER (they run side by side as before).

OFF WINDOWS THE PROTOCOL DOES NOTHING: there is no installer there (the installer is Windows-only),
so there is nothing to exclude. Registration is a no-op, `setup_running` and `await_setup` answer
False and `others_running` answers [] - the reason is `NOT_PROTECTED`. Every Windows call sits
behind `sys.platform == "win32"` (CLAUDE.md: no POSIX idioms on the Windows path, and vice versa).

Standard library and ctypes only, ctypes loaded on the Windows branch: `pam.entry` imports this
before `pam.cli` exists in the process, so it must never import click or `pam.cli` (Task 13A-F1),
and the eager import closure stays `{click, pam}`.
"""

import os
import re
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pam.errors import RunRegistryError

# The registry root. Tests set it (tests/conftest.py, `isolated_run_registry`); nothing else should.
REGISTRY_ENV = "PAM_RUN_REGISTRY"
RUN_DIR = "run"
SETUP_LOCK = "setup.lock"

# 10C's measured numbers, kept by the owner (question 4): a transient DENIED or SHARING (a prober's
# handle, a reused pid's delete-pending file) is retried this long before pam refuses.
RETRY_S = 2.0
# A4: how long `pam update` waits, still registered, for setup to hold `setup.lock`.
AWAIT_SETUP_S = 10.0

# `[0-9]`, not `\d` (which matches every Unicode digit), and `\Z`, not `$`. Any other name in the
# run folder is not a run file: it is ignored and never deleted.
_RUN_NAME = re.compile(r"^([0-9]+)\.lock\Z")

SETUP_RUNNING = "pam is being installed or updated. Try again when it finishes."
CANNOT_REGISTER = (
    "pam cannot record that it is running in {path} ({error}), so it cannot tell whether it is"
    " being installed or updated. Make that folder writable, then try again."
)
CANNOT_TELL = "pam cannot tell whether {what} ({error}). Make {path} readable and writable, then try again."
NOT_PROTECTED = (
    "pam's installer is Windows-only, so off Windows nothing is excluded: pam registers nothing"
    " and never reports another pam as running."
)

# Windows constants (winnt.h, winerror.h).
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_DELETE = 0x00010000
_SHARE_READ_WRITE_DELETE = 0x1 | 0x2 | 0x4
_OPEN_EXISTING = 3
_OPEN_ALWAYS = 4
_DELETE_ON_CLOSE = 0x04000000
_HOLD_ACCESS = _GENERIC_READ | _GENERIC_WRITE | _DELETE
_HOLD_SHARE = 0  # A1: exclusive. Never add FILE_SHARE_DELETE (0x4) here.
_ERROR_FILE_NOT_FOUND = 2
_ERROR_PATH_NOT_FOUND = 3
_ERROR_ACCESS_DENIED = 5
_ERROR_SHARING_VIOLATION = 32
_RETRY_SLEEP_S = 0.002

HELD = "held"
STALE = "stale"
ABSENT = "absent"


def registry_root() -> Path:
    r"""`%LOCALAPPDATA%\pam-install`; without LOCALAPPDATA, `~/.pam`. Never under `PAM_HOME`."""
    override = os.environ.get(REGISTRY_ENV)
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "pam-install"
    return Path.home() / ".pam"


def run_file(root: Path | None = None, pid: int | None = None) -> Path:
    return (root or registry_root()) / RUN_DIR / f"{os.getpid() if pid is None else pid}.lock"


def setup_lock_path(root: Path | None = None) -> Path:
    return (root or registry_root()) / SETUP_LOCK


# --- the Windows calls --------------------------------------------------------------------------

_kernel32: Any = None


def _k32() -> Any:
    """kernel32 with CreateFileW/CloseHandle typed, built once per process (cost per start)."""
    global _kernel32
    if _kernel32 is None:
        import ctypes  # the `lock.py` precedent: stdlib, loaded only on the Windows branch

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
        _kernel32 = kernel32
    return _kernel32


def _create_file(
    path: Path, access: int, share: int, disposition: int
) -> tuple[int | None, int]:
    """(handle, 0) or (None, winerror). Always with FILE_FLAG_DELETE_ON_CLOSE."""
    import ctypes

    kernel32 = _k32()
    handle = kernel32.CreateFileW(
        str(path), access, share, None, disposition, _DELETE_ON_CLOSE, None
    )
    if handle is None or handle == ctypes.c_void_p(-1).value:
        return None, ctypes.get_last_error()
    return handle, 0


def _close(handle: int) -> None:
    _k32().CloseHandle(handle)


def _describe(winerror: int) -> str:
    import ctypes

    return str(ctypes.WinError(winerror))


# --- the probe (A2) -----------------------------------------------------------------------------


def probe(path: Path, deadline_s: float = RETRY_S) -> str:
    """HELD, STALE (and now deleted) or ABSENT. Raises `RunRegistryError` when it cannot tell.

    Windows only: off Windows the protocol does nothing and nothing calls this."""
    end = time.monotonic() + deadline_s
    while True:
        handle, error = _create_file(path, _DELETE, _SHARE_READ_WRITE_DELETE, _OPEN_EXISTING)
        if handle is not None:
            _close(handle)  # delete-on-close: a stale file is removed on sight
            return STALE
        if error == _ERROR_SHARING_VIOLATION:
            return HELD
        if error in (_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND):
            return ABSENT
        if error != _ERROR_ACCESS_DENIED or time.monotonic() > end:
            raise RunRegistryError(
                CANNOT_TELL.format(
                    what=f"{path.name} is in use", error=_describe(error), path=path.parent
                )
            )
        time.sleep(_RETRY_SLEEP_S)


def setup_running(root: Path | None = None, deadline_s: float = RETRY_S) -> bool:
    """Whether the installer or uninstaller holds `setup.lock`. A stale one is removed."""
    if sys.platform != "win32":
        return False
    return probe(setup_lock_path(root), deadline_s) == HELD


def others_running(
    exclude: Iterable[int] = (), root: Path | None = None, deadline_s: float = RETRY_S
) -> list[int]:
    """THE INTERFACE `pam update` (Task 12) CALLS: the pids of every OTHER live pam, sorted.

    This process is always excluded, and so is every pid in `exclude`. A stale run file (a power
    loss) is removed on sight; a name that is not `<digits>.lock` is ignored and kept. A folder
    that cannot be listed, or a run file that cannot be probed, raises `RunRegistryError`: an
    unknown state is refused, never read as "none running". Off Windows: [] (`NOT_PROTECTED`)."""
    if sys.platform != "win32":
        return []
    excluded = {os.getpid(), *exclude}
    folder = (root or registry_root()) / RUN_DIR
    try:
        names = os.listdir(folder)
    except FileNotFoundError:
        return []
    except OSError as error:
        raise RunRegistryError(
            CANNOT_TELL.format(what="another pam is running", error=error, path=folder)
        ) from None
    held = []
    for name in names:
        match = _RUN_NAME.match(name)
        if match is None:
            continue
        pid = int(match.group(1))
        if pid not in excluded and probe(folder / name, deadline_s) == HELD:
            held.append(pid)
    return sorted(held)


def another_pam_running(exclude: Iterable[int] = (), root: Path | None = None) -> bool:
    """`others_running` as a yes/no, for `pam update`'s "another pam is running" refusal."""
    return bool(others_running(exclude, root))


# --- registration -------------------------------------------------------------------------------

# One run file per (process, root), whatever the number of registrations: the in-process test
# suite and a nested `main` must not refuse themselves (share mode 0 refuses a second open even
# from the same process). The last release closes the handle.
_held_lock = threading.Lock()
_held: dict[str, list[int]] = {}  # normcased run-file path -> [handle, registrations]


def register(root: Path | None = None, deadline_s: float = RETRY_S) -> Path | None:
    r"""Take `run\<pid>.lock` for this process; the path, or None off Windows.

    Raises `RunRegistryError` (fail closed) when it cannot be created within `deadline_s`."""
    if sys.platform != "win32":
        return None
    path = run_file(root)
    key = os.path.normcase(str(path))
    with _held_lock:
        entry = _held.get(key)
        if entry is not None:
            entry[1] += 1
            return path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise RunRegistryError(
                CANNOT_REGISTER.format(path=path.parent, error=error)
            ) from None
        _held[key] = [_hold(path, deadline_s), 1]
    return path


def _hold(path: Path, deadline_s: float) -> int:
    end = time.monotonic() + deadline_s
    while True:
        handle, error = _create_file(path, _HOLD_ACCESS, _HOLD_SHARE, _OPEN_ALWAYS)
        if handle is not None:
            return handle
        transient = error in (_ERROR_SHARING_VIOLATION, _ERROR_ACCESS_DENIED)
        if not transient or time.monotonic() > end:
            raise RunRegistryError(
                CANNOT_REGISTER.format(path=path.parent, error=_describe(error))
            )
        time.sleep(_RETRY_SLEEP_S)


def release(path: Path | None) -> None:
    """Undo one `register`; the last one closes the handle, and the file goes with it."""
    if path is None:
        return
    key = os.path.normcase(str(path))
    with _held_lock:
        entry = _held.get(key)
        if entry is None:
            return
        entry[1] -= 1
        if entry[1] == 0:
            del _held[key]
            _close(entry[0])


@contextmanager
def registered(root: Path | None = None) -> Iterator[None]:
    """`pam.cli.main`'s first act (`pam.entry.main` does the same before importing `pam.cli`):
    REGISTER, THEN check for setup (the order is the protocol).

    Raises `RunRegistryError` with `SETUP_RUNNING` while `setup.lock` is held, after releasing."""
    path = register(root)
    try:
        if setup_running(root):
            raise RunRegistryError(SETUP_RUNNING)
        yield
    finally:
        release(path)


# --- A4, for the updater ------------------------------------------------------------------------


def await_setup(
    child_exited: Callable[[], bool] | None = None,
    root: Path | None = None,
    deadline_s: float = AWAIT_SETUP_S,
    poll_s: float = 0.01,
) -> bool:
    """A4: `pam update`, STILL REGISTERED, waits until setup holds `setup.lock`; True once it does.

    False when `child_exited()` says the setup it launched has ended, or at `deadline_s` - then the
    updater exits anyway (setup refuses if anything else is running; that is the safe outcome).
    A probe that cannot tell is not a sighting, and the wait goes on. Off Windows: False at once.
    Without it two updaters started together could each launch a setup (10C, A4)."""
    if sys.platform != "win32":
        return False
    end = time.monotonic() + deadline_s
    while True:
        try:
            if setup_running(root, deadline_s=0.0):
                return True
        except RunRegistryError:
            pass
        if (child_exited is not None and child_exited()) or time.monotonic() > end:
            return False
        time.sleep(poll_s)
