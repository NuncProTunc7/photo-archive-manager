"""A process's whole TREE on Windows: find it, measure it, kill it (M13 Task 13, Task 16 reuses it).

WHY A TREE AND NOT A PID. `.venv\\Scripts\\pam.exe` is a LAUNCHER: it starts `python.exe`, which
does the work and starts `exiftool.exe` (whose standalone build may start a Perl of its own).
Killing the launcher's PID alone can leave the `python.exe` doing the work running, and the
launcher's own working set is not the scan's memory. So every measurement and every kill here is
over the tree rooted at one PID.

Stdlib `ctypes` only - no psutil, no new dependency (the `pam/lock.py` precedent). Windows only;
every entry point raises `OSError` elsewhere.

PID REUSE. Toolhelp reports each process's PARENT PID as it was at creation, and Windows reuses
PIDs, so a process whose recorded parent died long ago can name a PID that now belongs to an
unrelated process. `descendants` therefore admits a child only if it was created no earlier than
the parent it claims - killing an unrelated process is the one mistake this module must not make.
"""

import ctypes
import functools
import sys
from collections.abc import Callable, Iterable
from ctypes import wintypes
from dataclasses import dataclass

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_PATH = 260
STILL_ACTIVE = 259


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * MAX_PATH),
    ]


class _MemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


@functools.cache
def _kernel32() -> "ctypes.WinDLL":
    if sys.platform != "win32":
        raise OSError("tools/_proctree.py is Windows-only")
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # restype/argtypes spelled out: the default `int` restype truncates a 64-bit HANDLE.
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
    k32.K32GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_MemoryCounters),
        wintypes.DWORD,
    ]
    return k32


@dataclass(frozen=True)
class Entry:
    pid: int
    ppid: int
    exe: str


def snapshot() -> list[Entry]:
    """Every process on the machine, with its recorded parent, from one Toolhelp snapshot."""
    k32 = _kernel32()
    handle = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if handle == INVALID_HANDLE_VALUE or not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
        out: list[Entry] = []
        ok = k32.Process32FirstW(handle, ctypes.byref(entry))
        while ok:
            out.append(Entry(entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile))
            ok = k32.Process32NextW(handle, ctypes.byref(entry))
        return out
    finally:
        k32.CloseHandle(handle)


def _with_process(pid: int, access: int, use: Callable[[int], int | None]) -> int | None:
    k32 = _kernel32()
    handle = k32.OpenProcess(access, False, pid)
    if not handle:
        return None
    try:
        return use(handle)
    finally:
        k32.CloseHandle(handle)


def creation_time(pid: int) -> int | None:
    """A LIVE process's creation FILETIME; None if it has exited or cannot be opened.

    "Exited" is checked, not assumed: a process that has terminated stays in the Toolhelp
    snapshot for as long as anything holds a handle to it (its own `subprocess.Popen` does),
    and a tree that listed it would report a dead launcher as still running.
    """
    k32 = _kernel32()

    def read(handle: int) -> int | None:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != STILL_ACTIVE:
            return None
        times = [ctypes.c_ulonglong() for _ in range(4)]
        if not k32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        return times[0].value

    return _with_process(pid, PROCESS_QUERY_LIMITED_INFORMATION, read)


def descendants(
    entries: Iterable[Entry], root: int, created: Callable[[int], int | None]
) -> list[int]:
    """`root` and every process below it, breadth-first, root first. Pure but for `created`.

    A child is admitted only if `created(child) >= created(parent)` - see the module docstring
    on PID reuse. A process whose creation time cannot be read is not admitted: if it cannot be
    opened it can be neither measured nor killed, so listing it would be a false promise.
    """
    children: dict[int, list[int]] = {}
    for entry in entries:
        if entry.pid != entry.ppid:  # the System Idle Process is its own parent
            children.setdefault(entry.ppid, []).append(entry.pid)
    root_created = created(root)
    if root_created is None:
        return []
    order = [root]
    born = {root: root_created}
    queue = [root]
    while queue:
        parent = queue.pop(0)
        for child in children.get(parent, []):
            if child in born:
                continue
            child_created = created(child)
            if child_created is None or child_created < born[parent]:
                continue
            born[child] = child_created
            order.append(child)
            queue.append(child)
    return order


def tree(root: int) -> list[int]:
    """The live tree rooted at `root`, root first. Empty if `root` is gone."""
    return descendants(snapshot(), root, creation_time)


def _memory(pid: int) -> _MemoryCounters | None:
    k32 = _kernel32()
    counters = _MemoryCounters()
    counters.cb = ctypes.sizeof(_MemoryCounters)

    def read(handle: int) -> int | None:
        return (
            1
            if k32.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
            else None
        )

    if _with_process(pid, PROCESS_QUERY_LIMITED_INFORMATION, read) is None:
        return None
    return counters


def working_set(pid: int) -> int:
    """The process's current working set in bytes; 0 if it cannot be read."""
    counters = _memory(pid)
    return 0 if counters is None else int(counters.WorkingSetSize)


def peak_working_set(pid: int) -> int:
    """The OS's own record of the process's PEAK working set; 0 if it cannot be read."""
    counters = _memory(pid)
    return 0 if counters is None else int(counters.PeakWorkingSetSize)


def tree_working_set(root: int) -> int:
    """The summed CURRENT working set of every process in the tree rooted at `root`."""
    return sum(working_set(pid) for pid in tree(root))


def kill_tree(root: int, wait_s: float = 30.0) -> list[int]:
    """Terminate every process in the tree rooted at `root`, CHILDREN FIRST. Returns the PIDs hit.

    Children first so that a parent cannot notice a dead child and start another in the gap. A
    PID that has already exited is skipped silently; the list names the ones TerminateProcess
    accepted.

    `TerminateProcess` is ASYNCHRONOUS, so this then WAITS (up to `wait_s` each) for every
    process it hit to have exited. Measured on this task's first resume run: a `pam scan`
    started the instant the kill returned found the killed scan's lock PID still openable and
    refused with "Another pam command is already running" - the kill had been issued, the
    process had not finished dying. A caller that starts the next command after this returns
    meets a tree that is really gone.
    """
    k32 = _kernel32()
    killed: list[int] = []
    handles: list[int] = []
    try:
        for pid in reversed(tree(root)):
            handle = k32.OpenProcess(PROCESS_TERMINATE | SYNCHRONIZE, False, pid)
            if not handle:
                continue
            handles.append(handle)
            if k32.TerminateProcess(handle, 1):
                killed.append(pid)
        for handle in handles:
            k32.WaitForSingleObject(handle, int(wait_s * 1000))
    finally:
        for handle in handles:
            k32.CloseHandle(handle)
    return killed


def gone(pid: int) -> bool:
    """True once no process with this PID can be opened at all - the test `pam/lock.py` uses
    to decide a lock is stale, which a terminated process still holding handles does not pass."""
    return _with_process(pid, PROCESS_QUERY_LIMITED_INFORMATION, lambda _handle: 1) is None
