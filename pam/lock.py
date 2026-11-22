"""Single-process-per-archive lock, self-healing on stale lockfiles."""

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from pam.errors import WorkspaceLockedError

if TYPE_CHECKING:  # pragma: no cover - the type only; `paths` is not needed at run time
    from pam.paths import Workspace

# Module-private: only `WorkspaceLock.__enter__` passes it, so only a taken lock makes a proof.
_ISSUED_BY_ENTER = object()


def _win_pid_alive(pid: int) -> bool:
    """Windows process-existence check via OpenProcess, then GetExitCodeProcess.

    We must NOT use os.kill(pid, 0) here: on Windows signal 0 is CTRL_C_EVENT,
    so os.kill would fire a Ctrl+C into the console instead of probing the pid.

    OPENABLE IS NOT ALIVE (M14 Task 6 Stage 2, S2-F3). A process that has EXITED stays openable
    for as long as any handle to it is open anywhere, so a killed `pam start` kept refusing the
    next one for 20-30 s and more. An opened process is alive only while its exit code is
    STILL_ACTIVE. A process that exited with the code 259 itself reads as alive, and so does a
    query that fails: both err towards refusing, which a stale-lock sentence can recover from.
    """
    import ctypes

    process_query_limited_information = 0x1000
    error_access_denied = 5
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    kernel32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel32.OpenProcess(process_query_limited_information, 0, pid)
    if handle:
        try:
            code = ctypes.c_uint32()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    # No handle: either the process is gone (ERROR_INVALID_PARAMETER) or it exists
    # but we cannot open it (ERROR_ACCESS_DENIED -> still alive, owned by another user).
    return ctypes.get_last_error() == error_access_denied


def _pid_alive(pid: int) -> bool:
    """Whether a process with this pid currently exists (platform-correct)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _win_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True  # exists, owned by another user
    except OSError:
        return False  # no such process
    return True


class HeldWorkspace:
    """Proof that this process holds `workspace`'s lock (M14 Task 5, Step 1).

    Made ONLY by `WorkspaceLock.__enter__`, which passes the module-private sentinel; any other
    construction raises. A body that runs under a lock it did not take itself (`scan_locked`,
    `plan_locked`, for the review server's job thread) takes one of these as its first argument
    and calls `require`, so it can neither run unlocked nor under another folder's lock.

    `workspace` is None when the lock was built without one (`WorkspaceLock(path)`, every call
    site written before M14): such a proof is valid for nothing that calls `require`.
    """

    def __init__(self, issuer: object, lock: "WorkspaceLock", workspace: "Workspace | None"):
        if issuer is not _ISSUED_BY_ENTER:
            raise TypeError("A HeldWorkspace is made only by WorkspaceLock.__enter__.")
        self._lock = lock
        self.workspace = workspace

    @property
    def held(self) -> bool:
        """Whether the lock this proof came from is still held (False after `__exit__`)."""
        return self._lock._fd is not None

    def require(self, workspace: "Workspace | None") -> "Workspace":
        """`workspace`, refused (AssertionError) unless this proof is current and names it.

        An explicit raise, not an `assert` statement: `python -O` must not remove it.
        """
        if workspace is None or self.workspace != workspace:
            raise AssertionError(
                f"the lock held is for {self.workspace!r}, not for {workspace!r}"
            )
        if not self.held:
            raise AssertionError(f"the lock for {workspace!r} has been released")
        return workspace


class WorkspaceLock:
    """Exclusive per-archive lock via O_EXCL lockfile containing our pid.

    Stale locks (dead pid or unreadable content) are removed and re-acquired.

    TODO(v1.x): Windows PID reuse can make a recycled PID look alive, producing a
    false "another pam is running" until the lockfile is deleted by hand. Robust
    fix: store (pid, process creation time) or use OS-level file locking.
    Accepted for v1 because pam is local and single-user, and the error message
    names the exact file to delete.
    """

    def __init__(self, lock_path: Path, workspace: "Workspace | None" = None):
        self._path = lock_path
        self._fd: int | None = None
        # M14 Task 5: optional, so every pre-M14 `WorkspaceLock(path)` is unchanged; it is what
        # the `HeldWorkspace` returned by `__enter__` names.
        self._workspace = workspace

    def _locked_error(self) -> WorkspaceLockedError:
        return WorkspaceLockedError(
            "Another pam command is already running against this archive. "
            "Wait for it to finish, then try again. If you are certain nothing "
            f"is running, delete: {self._path}"
        )

    def __enter__(self) -> HeldWorkspace:
        try:
            self._fd = self._create()
        except FileExistsError:
            if not self._holder_alive():
                self._path.unlink(missing_ok=True)
                try:
                    self._fd = self._create()
                except FileExistsError:
                    raise self._locked_error() from None
            else:
                raise self._locked_error() from None
        os.write(self._fd, str(os.getpid()).encode("ascii"))
        os.fsync(self._fd)
        # M14 Task 5: the proof, not the lock. Every `with WorkspaceLock(...)` written before
        # M14 ignores the value; whoever must `__exit__` later keeps the lock object itself.
        return HeldWorkspace(_ISSUED_BY_ENTER, self, self._workspace)

    def __exit__(self, *exc_info) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self._path.unlink(missing_ok=True)

    def _create(self) -> int:
        return os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)

    def _holder_alive(self) -> bool:
        try:
            pid = int(self._path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return False
        return _pid_alive(pid)
