"""`tools/_proctree.py`: a process TREE found, measured and killed (M13 Task 13 Step 2).

The live test's oracle is independent of the module: it holds its own SYNCHRONIZE handles to
both processes, opened BEFORE the kill (so a reused PID cannot answer for a dead one), and asks
the kernel whether each has exited. Its `finally` kills both through those handles and through
`taskkill /T`, so a failing assertion cannot leave a sleeping process behind.
"""

import ctypes
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import _proctree  # noqa: E402

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows process APIs")

SYNCHRONIZE = 0x00100000
PROCESS_TERMINATE = 0x0001
WAIT_OBJECT_0 = 0

# The parent prints its child's PID, then sleeps: the shape `pam.exe` -> `python.exe` has.
PARENT = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "print(child.pid, flush=True)\n"
    "time.sleep(120)\n"
)


def _k32():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    return k32


@windows_only
def test_the_tree_is_found_measured_and_killed_children_included():
    k32 = _k32()
    parent = subprocess.Popen([sys.executable, "-c", PARENT], stdout=subprocess.PIPE, text=True)
    handles = []
    try:
        assert parent.stdout is not None
        child_pid = int(parent.stdout.readline())
        for pid in (parent.pid, child_pid):
            handle = k32.OpenProcess(SYNCHRONIZE | PROCESS_TERMINATE, False, pid)
            assert handle, f"could not open {pid}"
            handles.append(handle)

        found = _proctree.tree(parent.pid)
        assert found[0] == parent.pid
        assert child_pid in found
        assert _proctree.tree_working_set(parent.pid) > _proctree.working_set(parent.pid) > 0

        killed = _proctree.kill_tree(parent.pid)
        assert set(killed) >= {parent.pid, child_pid}
        assert killed.index(child_pid) < killed.index(parent.pid)  # children first
        for handle in handles:
            assert k32.WaitForSingleObject(handle, 10_000) == WAIT_OBJECT_0
        assert _proctree.tree(parent.pid) == []
    finally:
        for handle in handles:
            k32.TerminateProcess(handle, 1)
            k32.CloseHandle(handle)
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(parent.pid)], capture_output=True, check=False
        )
        parent.wait(timeout=30)


def _entries(*rows):
    return [_proctree.Entry(pid, ppid, "x.exe") for pid, ppid in rows]


def test_descendants_walks_every_level_root_first():
    entries = _entries((1, 0), (2, 1), (3, 2), (4, 3), (5, 1), (9, 8))
    born = {1: 10, 2: 11, 3: 12, 4: 13, 5: 14, 9: 1}
    assert _proctree.descendants(entries, 1, born.get) == [1, 2, 5, 3, 4]


def test_a_reused_pid_does_not_adopt_an_older_process():
    """Process 7 names 2 as its parent, but was created BEFORE the current process 2: its real
    parent died and the PID was reused. Killing 7 would kill an unrelated process."""
    entries = _entries((1, 0), (2, 1), (7, 2))
    born = {1: 10, 2: 20, 7: 15}
    assert _proctree.descendants(entries, 1, born.get) == [1, 2]


def test_an_unopenable_process_is_neither_listed_nor_followed():
    entries = _entries((1, 0), (2, 1), (3, 2))
    born = {1: 10, 3: 12}  # 2 cannot be opened
    assert _proctree.descendants(entries, 1, born.get) == [1]
    assert _proctree.descendants(entries, 4, born.get) == []  # a root that is gone


@windows_only
def test_kill_tree_waits_for_every_process_it_hit(monkeypatch):
    """`kill_tree` WAITS for each process it terminated (TerminateProcess is asynchronous, and
    Task 13's first resume run met a killed scan's lock PID still openable). The live test above
    waits on its own handles, so it cannot see this wait; the call itself is watched here
    (M2580, review): one `WaitForSingleObject` per handle it terminated, with `wait_s`."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    k32 = _proctree._kernel32()
    real = k32.WaitForSingleObject
    waits: list[int] = []

    def spy(handle, ms):
        waits.append(ms)
        return real(handle, ms)

    try:
        monkeypatch.setattr(k32, "WaitForSingleObject", spy)
        killed = _proctree.kill_tree(proc.pid, wait_s=7.0)
        monkeypatch.undo()
        # A venv's python.exe is itself a launcher, so the tree may hold one process or two
        # depending on how far it has started; every handle it opened is waited on, with wait_s.
        assert proc.pid in killed
        assert len(waits) >= len(killed) and set(waits) == {7000}
    finally:
        monkeypatch.undo()
        proc.kill()
        proc.wait(timeout=30)
