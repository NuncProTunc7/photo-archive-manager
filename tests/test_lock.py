import os

import pytest

from pam.errors import WorkspaceLockedError
from pam.lock import WorkspaceLock, _pid_alive


def test_acquire_and_release(tmp_path):
    lock_path = tmp_path / "pam.lock"
    with WorkspaceLock(lock_path):
        assert lock_path.exists()
        assert lock_path.read_text() == str(os.getpid())
    assert not lock_path.exists()


def test_second_acquire_fails_while_held(tmp_path):
    lock_path = tmp_path / "pam.lock"
    with WorkspaceLock(lock_path):
        with pytest.raises(WorkspaceLockedError, match="Another pam command"):
            WorkspaceLock(lock_path).__enter__()


def test_stale_lock_is_broken(tmp_path):
    lock_path = tmp_path / "pam.lock"
    lock_path.write_text("999999999")  # not a live pid
    with WorkspaceLock(lock_path):
        assert lock_path.read_text() == str(os.getpid())


def test_garbage_lockfile_is_treated_as_stale(tmp_path):
    lock_path = tmp_path / "pam.lock"
    lock_path.write_text("not-a-pid")
    with WorkspaceLock(lock_path):
        assert lock_path.exists()


def test_pid_alive_reports_own_process():
    assert _pid_alive(os.getpid()) is True


def test_pid_alive_nonexistent_pid_is_dead_and_does_not_signal():
    # Regression (HT-01): on Windows, signal 0 IS CTRL_C_EVENT, so the old
    # os.kill(pid, 0) liveness probe fired a Ctrl+C into the console
    # (KeyboardInterrupt) instead of quietly reporting the process dead. A
    # non-existent pid must return False without raising or signalling anything.
    assert _pid_alive(999999999) is False


def test_pid_alive_rejects_nonpositive_pids():
    assert _pid_alive(0) is False
    assert _pid_alive(-1) is False


class _FakeKernel32:
    """A stand-in for `ctypes.WinDLL("kernel32")`: an opened handle, then a chosen exit query."""

    def __init__(self, query_ok, exit_code):
        self.closed = []

        def open_process(_access, _inherit, _pid):
            return 4242

        def get_exit_code_process(_handle, code_ref):
            code_ref._obj.value = exit_code
            return 1 if query_ok else 0

        def close_handle(handle):
            self.closed.append(handle)
            return 1

        self.OpenProcess = open_process
        self.GetExitCodeProcess = get_exit_code_process
        self.CloseHandle = close_handle


@pytest.mark.skipif(os.name != "nt", reason="the Windows liveness branch")
@pytest.mark.parametrize(
    ("query_ok", "exit_code", "alive"),
    [
        # M14 Task 6 S2-F3: an openable process that has EXITED is not a lock holder ...
        (True, 0, False),
        (True, 1, False),
        # ... one whose exit code is still STILL_ACTIVE (259) is ...
        (True, 259, True),
        # ... and a query that FAILS errs towards refusing (M3376's closure: the docstring's
        # "so does a query that fails" was otherwise untested).
        (False, 0, True),
    ],
)
def test_an_opened_pid_is_alive_only_while_its_exit_code_says_so(
    monkeypatch, query_ok, exit_code, alive
):
    import ctypes

    from pam import lock

    fake = _FakeKernel32(query_ok, exit_code)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_k: fake)
    assert lock._win_pid_alive(1234) is alive
    assert fake.closed == [4242], "the process handle was not closed"
