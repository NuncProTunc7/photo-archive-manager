r"""E11, pam's side (M15 Task 13A): every pam registers that it runs, and refuses while setup runs.

SPELLED FROM SPIKE 10C's RECORD (`docs/implementation-notes/m15-spikes/10c/10c-record.md`), not
from `pam/run_registry.py`: the sentence, the paths, the share mode, the probe's table and the
order are written here by hand. The Windows calls this file makes to HOLD and to PROBE files are
its OWN ctypes, an independent second implementation (CLAUDE.md: an oracle built from the
definition it checks is a survivor waiting to happen). Real processes are used where 10C used
them (`run_registry_child.py` runs the real `pam.cli.main`); orders are forced with file gates,
never sleeps, and every wait is a barrier with a deadline (the hosted-CI timing traps).
"""

import json
import os
import pathlib
import signal
import subprocess
import sys
import sysconfig
import threading
import time
from pathlib import Path

import pytest

from pam import cli, entry, run_registry
from pam.errors import RunRegistryError

WINDOWS = sys.platform == "win32"
windows_only = pytest.mark.skipif(
    not WINDOWS, reason="E11 is Windows-only; off Windows it is a no-op"
)

HERE = Path(__file__).resolve().parent
CHILD = HERE / "run_registry_child.py"
DEADLINE_S = 60

# Spelled from 10C 1's state machine and the brief, never read from the module.
SETUP_SENTENCE = "pam is being installed or updated. Try again when it finishes."


# --- an independent Windows layer -----------------------------------------------------------

GENERIC_RW = 0x80000000 | 0x40000000
DELETE = 0x00010000
SHARE_RWD = 0x1 | 0x2 | 0x4
OPEN_EXISTING = 3
OPEN_ALWAYS = 4
DELETE_ON_CLOSE = 0x04000000
SHARING = 32


def _kernel32():
    import ctypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.restype = ctypes.c_void_p
    k.CreateFileW.argtypes = (
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    )
    k.CloseHandle.argtypes = (ctypes.c_void_p,)
    return k


def _open(path, access, share, disposition, flags):
    import ctypes

    k = _kernel32()
    handle = k.CreateFileW(str(path), access, share, None, disposition, flags, None)
    if handle is None or handle == ctypes.c_void_p(-1).value:
        return None, ctypes.get_last_error()
    return handle, 0


class Holder:
    """The installer's hold on `setup.lock`, as 10C 1 describes it (share 0, delete-on-close)."""

    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle, error = _open(path, GENERIC_RW | DELETE, 0, OPEN_ALWAYS, DELETE_ON_CLOSE)
        assert self.handle is not None, f"could not hold {path.name}: winerror {error}"

    def close(self):
        if self.handle is not None:
            _kernel32().CloseHandle(self.handle)
            self.handle = None


def delete_open_error(path):
    """The winerror of a DELETE open with share R|W|D, or 0 (then closed, without delete-on-close
    so a probe here never removes what it looks at)."""
    handle, error = _open(path, DELETE, SHARE_RWD, OPEN_EXISTING, 0x80)
    if handle is not None:
        _kernel32().CloseHandle(handle)
    return error


def run_names(root):
    folder = root / "run"
    return sorted(os.listdir(folder)) if folder.exists() else []


def await_true(condition, what, deadline_s=DEADLINE_S):
    end = time.monotonic() + deadline_s
    while not condition():
        assert time.monotonic() < end, f"never: {what}"
        time.sleep(0.005)


def read_pid(path):
    """The pid a child wrote. Its `os.replace` can still hold the name for an instant after it
    appears - an open then fails with PermissionError on Windows (measured: 1 run in 13) - so a
    failed read is retried to a deadline, never slept past."""
    pid = []

    def read():
        try:
            pid.append(int(path.read_text(encoding="ascii")))
        except (FileNotFoundError, PermissionError, ValueError):
            return False
        return True

    await_true(read, f"{path.name} readable")
    return pid[0]


@pytest.fixture
def fake_command(monkeypatch):
    """Replaces the click group's `main` - the command body `pam.cli.main` runs once registered -
    with a recorder that runs `inside()` there."""
    calls = []

    def install(inside=lambda: None):
        def fake(args=None, standalone_mode=True):
            calls.append(args)
            inside()

        monkeypatch.setattr(cli.cli, "main", fake)
        return calls

    return install


# --- the path rule ---------------------------------------------------------------------------


def test_the_root_is_LOCALAPPDATA_pam_install_whatever_PAM_HOME_says(monkeypatch, tmp_path):
    monkeypatch.delenv(run_registry.REGISTRY_ENV)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("PAM_HOME", str(tmp_path / "elsewhere"))
    root = tmp_path / "local" / "pam-install"
    assert run_registry.registry_root() == root
    assert run_registry.run_file() == root / "run" / f"{os.getpid()}.lock"
    assert run_registry.setup_lock_path() == root / "setup.lock"
    monkeypatch.setenv("PAM_HOME", str(tmp_path / "a-third-place"))
    assert run_registry.registry_root() == root


def test_without_LOCALAPPDATA_the_root_is_dot_pam_in_the_home_folder(monkeypatch):
    monkeypatch.delenv(run_registry.REGISTRY_ENV)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert run_registry.registry_root() == Path.home() / ".pam"


def test_the_override_names_the_root_and_an_empty_one_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv(run_registry.REGISTRY_ENV, str(tmp_path / "r"))
    assert run_registry.registry_root() == tmp_path / "r"
    monkeypatch.setenv(run_registry.REGISTRY_ENV, "")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert run_registry.registry_root() == tmp_path / "local" / "pam-install"


# --- registration, in this process -----------------------------------------------------------


@windows_only
def test_main_holds_its_run_file_for_the_whole_command_and_it_goes_after(
    isolated_run_registry, fake_command
):
    mine = isolated_run_registry / "run" / f"{os.getpid()}.lock"
    seen = []
    calls = fake_command(lambda: seen.append((mine.exists(), delete_open_error(mine))))
    assert cli.main(["--version"]) == 0
    assert calls == [["--version"]]
    assert seen == [(True, SHARING)]
    assert run_names(isolated_run_registry) == []


@windows_only
def test_the_run_file_is_held_exclusively_never_sharing_delete(isolated_run_registry):
    """A1: a holder that grants FILE_SHARE_DELETE is invisible - `os.remove` deletes the name
    while it is held (10C M1). The held file must refuse a delete by name and by handle."""
    path = run_registry.register()
    try:
        assert path == isolated_run_registry / "run" / f"{os.getpid()}.lock"
        with pytest.raises(PermissionError):
            os.remove(path)
        assert path.exists()
        assert delete_open_error(path) == SHARING
    finally:
        run_registry.release(path)
    assert not path.exists()


@windows_only
def test_main_registers_BEFORE_it_checks_for_setup(
    isolated_run_registry, fake_command, monkeypatch
):
    """The race (10C 1, Dekker): pam takes its run file first, then probes setup.lock."""
    mine = isolated_run_registry / "run" / f"{os.getpid()}.lock"
    original = run_registry.setup_running
    at_the_check = []

    def spy(*args, **kwargs):
        at_the_check.append(delete_open_error(mine))
        return original(*args, **kwargs)

    monkeypatch.setattr(run_registry, "setup_running", spy)
    fake_command()
    assert cli.main(["--version"]) == 0
    assert at_the_check == [SHARING]


@windows_only
def test_a_held_setup_lock_refuses_with_the_sentence_exit_1_runs_nothing_and_releases(
    isolated_run_registry, fake_command, capsys
):
    calls = fake_command()
    setup = Holder(isolated_run_registry / "setup.lock")
    try:
        assert cli.main(["scan", "anything"]) == 1
    finally:
        setup.close()
    assert calls == []
    assert SETUP_SENTENCE in capsys.readouterr().err
    assert run_names(isolated_run_registry) == []


@windows_only
def test_a_stale_setup_lock_is_removed_and_the_command_runs(
    isolated_run_registry, fake_command
):
    isolated_run_registry.mkdir(parents=True)
    (isolated_run_registry / "setup.lock").write_text("left by a power loss\n")
    calls = fake_command()
    assert cli.main(["--version"]) == 0
    assert calls == [["--version"]]
    assert not (isolated_run_registry / "setup.lock").exists()


@windows_only
def test_a_run_file_that_cannot_be_created_refuses_exit_1_and_runs_nothing(
    isolated_run_registry, fake_command, capsys
):
    """Fail closed (owner, 2026-11-17): never run unregistered."""
    isolated_run_registry.mkdir(parents=True)
    (isolated_run_registry / "run").write_text("a file where the folder belongs")
    calls = fake_command()
    assert cli.main(["--version"]) == 1
    assert calls == []
    err = capsys.readouterr().err
    assert "pam cannot record that it is running in" in err
    assert str(isolated_run_registry / "run") in err


@windows_only
def test_registrations_in_one_process_share_one_file_nested_and_threaded(isolated_run_registry):
    """The in-process suite and a nested `main` must not refuse themselves: share mode 0 refuses a
    second open of the same file even from the same process."""
    mine = isolated_run_registry / "run" / f"{os.getpid()}.lock"
    errors = []

    def one():
        try:
            with run_registry.registered():
                time.sleep(0.01)
        except Exception as error:  # noqa: BLE001 - recorded, asserted below
            errors.append(error)

    with run_registry.registered():
        with run_registry.registered():
            assert delete_open_error(mine) == SHARING
        assert delete_open_error(mine) == SHARING
        threads = [threading.Thread(target=one) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(DEADLINE_S)
        assert delete_open_error(mine) == SHARING
    assert errors == []
    assert not mine.exists()


# --- real processes --------------------------------------------------------------------------


class Pam:
    """A real `pam` (the real `main`) in a child process; see `run_registry_child.py`."""

    def __init__(self, tmp_path, name, gate=None):
        self.ready = tmp_path / f"{name}.ready"
        self.hold = tmp_path / f"{name}.hold"
        self.hold.touch()
        args = [sys.executable, str(CHILD), str(self.ready), str(self.hold)]
        if gate is not None:
            gate.mkdir(exist_ok=True)
            args.append(str(gate))
        self.process = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8"
        )

    def wait_running(self):
        await_true(
            lambda: self.ready.exists() or self.process.poll() is not None, "the child pam ran"
        )
        assert self.ready.exists(), self.finish()
        return read_pid(self.ready)

    def finish(self):
        self.hold.unlink(missing_ok=True)
        out, err = self.process.communicate(timeout=DEADLINE_S)
        assert "Traceback" not in err, err
        return self.process.returncode, err


@windows_only
def test_a_real_pams_registration_is_seen_by_another_process(isolated_run_registry, tmp_path):
    pam = Pam(tmp_path, "a")
    pid = pam.wait_running()  # the child's own pid, never `Popen.pid` (10C M5)
    assert run_names(isolated_run_registry) == [f"{pid}.lock"]
    assert run_registry.others_running() == [pid]
    assert run_registry.another_pam_running()
    assert run_registry.others_running(exclude=[pid]) == []
    with run_registry.registered():  # this process too: never reported as "another"
        assert run_registry.others_running() == [pid]
    assert pam.finish() == (0, "")
    assert run_names(isolated_run_registry) == []
    assert run_registry.others_running() == []


@windows_only
def test_ordinary_pams_run_side_by_side_against_a_stale_setup_lock(
    isolated_run_registry, tmp_path
):
    """10C: concurrent probes of one stale marker never read each other as a live setup."""
    isolated_run_registry.mkdir(parents=True)
    (isolated_run_registry / "setup.lock").write_text("stale\n")
    pams = [Pam(tmp_path, f"p{n}") for n in range(6)]
    pids = sorted(pam.wait_running() for pam in pams)
    assert run_registry.others_running() == pids
    assert not (isolated_run_registry / "setup.lock").exists()
    assert [pam.finish()[0] for pam in pams] == [0] * 6
    assert run_names(isolated_run_registry) == []


@windows_only
def test_a_killed_pam_leaves_nothing_behind(isolated_run_registry, tmp_path):
    pam = Pam(tmp_path, "k")
    pid = pam.wait_running()
    os.kill(pid, signal.SIGTERM)  # TerminateProcess on Windows (never signal 0: CLAUDE.md)
    pam.process.wait(DEADLINE_S)
    await_true(lambda: run_names(isolated_run_registry) == [], "the killed pam's file went")
    assert run_registry.others_running() == []
    pam.hold.unlink()


@windows_only
def test_the_race_a_registered_pam_is_seen_by_setup_then_refuses(
    isolated_run_registry, tmp_path
):
    """10C's first forced order: pam has registered and not yet checked; setup takes setup.lock
    and must SEE pam; pam then checks, sees setup and refuses; its file goes. Never both."""
    gate = tmp_path / "gate"
    pam = Pam(tmp_path, "r", gate=gate)
    await_true(lambda: (gate / "reached").exists(), "pam reached its setup check")
    pid = read_pid(gate / "reached")
    setup = Holder(isolated_run_registry / "setup.lock")
    try:
        assert run_registry.others_running() == [pid]  # setup must wait, not proceed
        (gate / "go").touch()
        code, err = pam.finish()
        assert code == 1 and SETUP_SENTENCE in err
        assert not pam.ready.exists()
        assert run_registry.others_running() == []  # setup proceeds now
    finally:
        setup.close()


@windows_only
def test_the_race_setup_first_then_a_pam_refuses(isolated_run_registry, tmp_path):
    setup = Holder(isolated_run_registry / "setup.lock")
    try:
        pam = Pam(tmp_path, "s")
        code, err = pam.finish()
    finally:
        setup.close()
    assert code == 1 and SETUP_SENTENCE in err
    assert not pam.ready.exists()
    assert run_names(isolated_run_registry) == []


def _pip_console_script():
    scripts = Path(sysconfig.get_path("scripts"))
    for candidate in (scripts / "pam.exe", Path(sys.executable).with_name("pam.exe")):
        if candidate.exists():
            return candidate
    raise AssertionError(f"no pip-installed pam.exe beside {sys.executable}")


@windows_only
def test_a_pip_installed_pam_takes_part_too(isolated_run_registry):
    """Owner, 2026-11-17 (10C question 3): every pam registers, pip installs included. The pip
    console script refuses while setup runs, and leaves nothing behind when it runs."""
    pam = [str(_pip_console_script()), "--version"]
    setup = Holder(isolated_run_registry / "setup.lock")
    try:
        refused = subprocess.run(pam, capture_output=True, text=True, timeout=DEADLINE_S)
    finally:
        setup.close()
    assert refused.returncode == 1 and SETUP_SENTENCE in refused.stderr
    ran = subprocess.run(pam, capture_output=True, text=True, timeout=DEADLINE_S)
    assert ran.returncode == 0 and "pam" in ran.stdout
    assert run_names(isolated_run_registry) == []


# --- the probe and "another pam is running" ---------------------------------------------------


@windows_only
def test_another_pam_is_probed_by_an_open_never_by_os_remove(
    isolated_run_registry, tmp_path, monkeypatch
):
    """A2. Watch the dangerous CALL, not only the outcome."""
    folder = isolated_run_registry / "run"
    folder.mkdir(parents=True)
    (folder / "999999.lock").write_text("stale")
    live = Holder(folder / "424242.lock")

    def forbidden(*args, **kwargs):
        raise AssertionError("the probe deleted by name")

    try:
        monkeypatch.setattr(os, "remove", forbidden)
        monkeypatch.setattr(os, "unlink", forbidden)
        monkeypatch.setattr(pathlib.Path, "unlink", forbidden)
        assert run_registry.others_running() == [424242]
        monkeypatch.undo()
        assert sorted(os.listdir(folder)) == ["424242.lock"]
    finally:
        live.close()


@windows_only
def test_stale_run_files_are_removed_on_sight_and_foreign_names_are_kept(isolated_run_registry):
    folder = isolated_run_registry / "run"
    folder.mkdir(parents=True)
    for name in ("4.lock", "999999.lock", "123456.lock"):
        (folder / name).write_text("x")
    for name in ("notes.txt", "12.lock.bak", "١٢.lock"):  # the last: Arabic-Indic digits
        (folder / name).write_text("not a run file")
    assert run_registry.others_running() == []
    assert sorted(os.listdir(folder)) == sorted(["notes.txt", "12.lock.bak", "١٢.lock"])


def test_no_registry_folder_means_no_other_pam(isolated_run_registry):
    assert not isolated_run_registry.exists()
    assert run_registry.others_running() == []
    assert not run_registry.another_pam_running()


def _icacls(path, *args):
    who = os.environ["USERNAME"]  # resolved at run time; never written to any file
    result = subprocess.run(
        ["icacls", str(path), *[a.replace("WHO", who) for a in args]],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, "icacls failed"


@pytest.fixture
def denied(isolated_run_registry):
    (isolated_run_registry / "run").mkdir(parents=True)
    undo = []
    yield isolated_run_registry, undo
    for path in reversed(undo):
        _icacls(path, "/remove:d", "WHO")


@windows_only
def test_a_run_file_that_cannot_be_probed_counts_as_running_never_as_none(denied):
    """Access denied is refused, never read as "no other pam" (10C M4: deleting is allowed by
    DELETE on the file OR FILE_DELETE_CHILD on its folder, so the construction denies both)."""
    root, undo = denied
    stuck = root / "run" / "4242.lock"
    stuck.write_text("x")
    _icacls(stuck, "/deny", "WHO:(DE)")
    _icacls(root / "run", "/deny", "WHO:(DC)")
    undo += [stuck, root / "run"]
    with pytest.raises(RunRegistryError, match="pam cannot tell whether"):
        run_registry.others_running(deadline_s=0.2)
    with pytest.raises(RunRegistryError):
        run_registry.another_pam_running()


@windows_only
def test_a_registry_that_cannot_be_listed_refuses(denied):
    root, undo = denied
    _icacls(root / "run", "/deny", "WHO:(RD)")
    undo.append(root / "run")
    with pytest.raises(RunRegistryError, match="another pam is running"):
        run_registry.others_running()


@windows_only
def test_a_real_pam_that_cannot_create_its_run_file_refuses(denied, tmp_path):
    root, undo = denied
    _icacls(root / "run", "/deny", "WHO:(WD)")
    undo.append(root / "run")
    pam = Pam(tmp_path, "d")
    code, err = pam.finish()
    assert code == 1 and "pam cannot record that it is running in" in err
    assert not pam.ready.exists()


@windows_only
def test_a_setup_lock_that_cannot_be_probed_refuses_pam(denied, fake_command, capsys):
    root, undo = denied
    stuck = root / "setup.lock"
    stuck.write_text("x")
    _icacls(stuck, "/deny", "WHO:(DE)")
    _icacls(root, "/deny", "WHO:(DC)")
    undo += [stuck, root]
    calls = fake_command()
    assert cli.main(["--version"]) == 1
    assert calls == []
    assert "pam cannot tell whether" in capsys.readouterr().err
    assert run_names(root) == []


# --- A4: the updater waits, registered, until setup holds setup.lock ---------------------------


@windows_only
def test_await_setup_returns_true_once_setup_holds_its_lock(isolated_run_registry):
    started = threading.Event()
    holders = []

    def installer():
        started.wait(DEADLINE_S)
        holders.append(Holder(isolated_run_registry / "setup.lock"))

    thread = threading.Thread(target=installer)
    thread.start()
    try:
        assert not run_registry.setup_running()
        started.set()
        assert run_registry.await_setup(deadline_s=DEADLINE_S) is True
    finally:
        thread.join(DEADLINE_S)
        for holder in holders:
            holder.close()


@windows_only
def test_await_setup_gives_up_when_the_launched_setup_has_exited(isolated_run_registry):
    asked = []
    assert (
        run_registry.await_setup(lambda: asked.append(1) or True, deadline_s=DEADLINE_S)
        is False
    )
    assert asked == [1]


@windows_only
def test_await_setup_gives_up_at_its_deadline(isolated_run_registry):
    assert run_registry.await_setup(lambda: False, deadline_s=0.05) is False


# --- off Windows --------------------------------------------------------------------------------


def test_off_windows_nothing_registers_and_no_other_pam_is_reported(
    isolated_run_registry, monkeypatch
):
    isolated_run_registry.mkdir(parents=True)
    (isolated_run_registry / "run").mkdir()
    (isolated_run_registry / "run" / "4242.lock").write_text("x")
    with monkeypatch.context() as patch:
        patch.setattr(sys, "platform", "linux")
        assert run_registry.register() is None
        with run_registry.registered():
            pass
        answers = (
            run_registry.others_running(),
            run_registry.another_pam_running(),
            run_registry.setup_running(),
            run_registry.await_setup(),
        )
    assert answers == ([], False, False, False)
    assert sorted(os.listdir(isolated_run_registry / "run")) == ["4242.lock"]
    assert "Windows-only" in run_registry.NOT_PROTECTED


# --- the isolation of the REAL registry --------------------------------------------------------


def test_the_guard_watches_the_spec_s_default_run_folders(real_run_registries, tmp_path):
    expected = [Path.home() / ".pam" / "run"]
    if os.environ.get("LOCALAPPDATA"):
        expected.append(Path(os.environ["LOCALAPPDATA"]) / "pam-install" / "run")
    assert sorted(real_run_registries) == sorted(expected)
    basetemp = tmp_path.parent.parent
    assert not any(basetemp in path.parents for path in real_run_registries)


@pytest.fixture(scope="module")
def registry_seen_by_a_module_fixture():
    return run_registry.registry_root()


def test_a_module_scoped_fixture_is_isolated_too(registry_seen_by_a_module_fixture, tmp_path):
    basetemp = tmp_path.parent.parent
    assert basetemp in registry_seen_by_a_module_fixture.parents


@windows_only
def test_the_guard_sees_a_created_folder_and_a_registration_and_nothing_else(
    tmp_path, monkeypatch
):
    import conftest

    created, used, untouched = (
        tmp_path / "c" / "run",
        tmp_path / "u" / "run",
        tmp_path / "x" / "run",
    )
    used.mkdir(parents=True)
    untouched.mkdir(parents=True)
    paths = (created, used, untouched)
    time.sleep(
        0.05
    )  # a folder's mtime moves on the next change, not inside the tick it was made in
    before = conftest.run_registry_states(paths)
    assert conftest.run_registry_escapes(before, conftest.run_registry_states(paths)) == []
    for root in (created.parent, used.parent):
        monkeypatch.setenv(run_registry.REGISTRY_ENV, str(root))
        with run_registry.registered():
            pass
    monkeypatch.undo()
    after = conftest.run_registry_states(paths)
    assert conftest.run_registry_escapes(before, after) == [str(created), str(used)]


_ESCAPES = """
import os, subprocess, sys, time
from pam import cli, run_registry

def test_0_control():
    assert cli.main(["--version"]) == 0

def test_a_monkeypatch_escape(monkeypatch):
    monkeypatch.delenv(run_registry.REGISTRY_ENV)
    assert cli.main(["--version"]) == 0

def test_b_a_raw_env_write(tmp_path):
    os.environ[run_registry.REGISTRY_ENV] = str(tmp_path / "not-the-isolated-one")

def test_c_subprocess_escape():
    time.sleep(0.05)
    env = {k: v for k, v in os.environ.items() if k != run_registry.REGISTRY_ENV}
    code = "import sys; from pam.cli import main; sys.exit(main(['--version']))"
    subprocess.run([sys.executable, "-c", code], env=env, check=True, capture_output=True)
"""


def errored_tests(output: str) -> set[str]:
    """The names of the child run's ERRORed tests, from its short summary. pytest 9.1 writes
    `ERROR <path>/test_escapes.py::name`; pytest 9.2 (the hosted runner, 2026-11-21) writes
    `ERROR ::name` for this child, so both forms are read."""
    return {
        line.rsplit("::", 1)[-1].split(" ")[0]
        for line in output.splitlines()
        if line.startswith("ERROR ")
        and ("test_escapes.py::" in line or line.startswith("ERROR ::"))
    }


def test_the_child_summary_parser_reads_both_pytest_forms():
    output = "\n".join(
        [
            "ERROR ../run/test_escapes.py::test_a_one - AssertionError: x",
            "ERROR ::test_b_two - AssertionError: y",
            "ERROR elsewhere.py::test_c_not_ours - AssertionError: z",
            "FAILED ::test_d_failed_not_errored",
        ]
    )
    assert errored_tests(output) == {"test_a_one", "test_b_two"}


def _run_child_pytest(tmp_path, source):
    """A child pytest loading THIS conftest with LOCALAPPDATA and the home folder pointed at
    scratch folders, so its 'real' run registry is a scratch one. Returns (output, ERRORed
    test names, the scratch 'real' run folder)."""
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "test_escapes.py").write_text(source, encoding="utf-8")
    local, home = tmp_path / "local", tmp_path / "home"
    local.mkdir()
    home.mkdir()
    env = dict(os.environ)
    for name in (
        "PYTEST_ADDOPTS",
        "PAM_FACES_REQUIRED",
        "PAM_OCR_REQUIRED",
        "PAM_INNO_REQUIRED",
    ):
        env.pop(name, None)
    env.update(LOCALAPPDATA=str(local), USERPROFILE=str(home), HOME=str(home))
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-rE",
            "-p",
            "no:cacheprovider",
            "-p",
            "conftest",
        ]
        + [str(tmp_path / "run" / "test_escapes.py")],
        cwd=HERE,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    output = (result.stdout + result.stderr).replace("\\", "/")
    assert result.returncode == 1, output[-3000:]
    errored = errored_tests(output)
    return output, errored, local / "pam-install" / "run"


@windows_only
def test_an_escape_by_monkeypatch_or_subprocess_fails_the_run(tmp_path):
    """The Task 9 review's F1 lesson, for the run registry: the env check alone cannot see the
    monkeypatch or the subprocess, and the file watch alone cannot see a raw env write."""
    output, errored, real = _run_child_pytest(tmp_path, _ESCAPES)
    assert errored == {
        "test_a_monkeypatch_escape",
        "test_b_a_raw_env_write",
        "test_c_subprocess_escape",
    }, output[-3000:]
    assert "this test registered a pam in the developer's REAL run registry" in output
    assert "the run registry escaped isolation" in output
    assert real.is_dir() and os.listdir(real) == []


_MODULE_ESCAPE = """
import os, subprocess, sys
import pytest
from pam import cli, run_registry

@pytest.fixture(scope="module")
def escaped_before_any_test():
    env = {k: v for k, v in os.environ.items() if k != run_registry.REGISTRY_ENV}
    code = "import sys; from pam.cli import main; sys.exit(main(['--version']))"
    subprocess.run([sys.executable, "-c", code], env=env, check=True, capture_output=True)

def test_uses_the_module_fixture(escaped_before_any_test):
    pass

def test_a_later_clean_test():
    assert cli.main(["--version"]) == 0
"""


@windows_only
def test_an_escape_in_a_module_scoped_fixture_fails_the_session(tmp_path):
    """A module-scoped fixture runs BEFORE the per-test guard's 'before' reading, so only the
    SESSION guard can see it - its sentence, at the session's last teardown."""
    output, errored, real = _run_child_pytest(tmp_path, _MODULE_ESCAPE)
    assert "the suite registered a pam in the developer's REAL run registry" in output
    assert "this test registered a pam" not in output
    assert errored == {"test_a_later_clean_test"}, output[-3000:]
    assert real.is_dir()


# --- the review's closures (M15 Task 13A review, rows M4229/M4241-M4243/M4250/M4253/M4254) ---------


@windows_only
def test_an_error_that_is_not_access_denied_refuses_at_once_never_after_a_retry(
    isolated_run_registry, monkeypatch
):
    """10C 1's probe table: DENIED is retried for 2 s; ANYTHING ELSE refuses - at once. The same
    for the hold: only SHARING and DENIED are transient. A name Windows rejects (`<`, winerror 123)
    is such an error, asked of the probe and of the hold with a 30 s deadline."""
    bad = isolated_run_registry / "run" / "a<b.lock"
    bad.parent.mkdir(parents=True)
    started = time.monotonic()
    with pytest.raises(RunRegistryError, match="pam cannot tell whether"):
        run_registry.probe(bad, deadline_s=30)
    monkeypatch.setattr(run_registry, "run_file", lambda root=None, pid=None: bad)
    with pytest.raises(RunRegistryError, match="pam cannot record that it is running in"):
        run_registry.register(deadline_s=30)
    assert time.monotonic() - started < 10, "an unknown error was retried, not refused"


def _register_while(release_after, isolated_run_registry):
    """`register()` in a thread while this test holds handles on this process's own run file;
    `release_after()` closes them after a moment. The registration and the time it took."""
    result = []

    def take():
        try:
            result.append(run_registry.register(deadline_s=DEADLINE_S))
        except Exception as error:  # noqa: BLE001 - asserted below
            result.append(error)

    thread = threading.Thread(target=take)
    thread.start()
    time.sleep(0.2)
    release_after()
    thread.join(DEADLINE_S)
    return result


@windows_only
def test_a_probers_handle_on_this_pids_file_is_waited_out_not_refused(isolated_run_registry):
    """10C 1: a transient SHARING at the hold - a setup or updater probing a stale file of a reused
    pid holds it for an instant - is retried, never refused."""
    mine = isolated_run_registry / "run" / f"{os.getpid()}.lock"
    mine.parent.mkdir(parents=True)
    mine.write_text("left by an earlier process with this pid")
    prober, error = _open(mine, DELETE, SHARE_RWD, OPEN_EXISTING, DELETE_ON_CLOSE)
    assert prober is not None, error
    result = _register_while(lambda: _kernel32().CloseHandle(prober), isolated_run_registry)
    try:
        assert result == [mine]
        assert delete_open_error(mine) == SHARING
    finally:
        run_registry.release(mine)
    assert not mine.exists()


@windows_only
def test_a_delete_pending_file_of_this_pid_is_waited_out_not_refused(isolated_run_registry):
    """10C 1: a transient DENIED at the hold - the delete-pending stale file of a reused pid,
    being removed by another prober - is retried, never refused."""
    mine = isolated_run_registry / "run" / f"{os.getpid()}.lock"
    mine.parent.mkdir(parents=True)
    mine.write_text("left by an earlier process with this pid")
    reader, error = _open(mine, 0x80000000, SHARE_RWD, OPEN_EXISTING, 0)
    assert reader is not None, error
    prober, error = _open(mine, DELETE, SHARE_RWD, OPEN_EXISTING, DELETE_ON_CLOSE)
    assert prober is not None, error
    _kernel32().CloseHandle(prober)  # the delete is now pending behind `reader`
    assert delete_open_error(mine) == 5, "the construction did not leave a delete pending"
    result = _register_while(lambda: _kernel32().CloseHandle(reader), isolated_run_registry)
    try:
        assert result == [mine]
    finally:
        run_registry.release(mine)


@windows_only
def test_await_setup_never_takes_a_probe_that_cannot_tell_for_a_sighting(denied):
    """A4: the updater waits for setup to HOLD `setup.lock`. A probe that cannot tell (an ACL) is
    not that sighting: the wait ends at its deadline, False."""
    root, undo = denied
    stuck = root / "setup.lock"
    stuck.write_text("x")
    _icacls(stuck, "/deny", "WHO:(DE)")
    _icacls(root, "/deny", "WHO:(DC)")
    undo += [stuck, root]
    assert run_registry.await_setup(lambda: False, deadline_s=0.3) is False


def test_off_windows_no_windows_call_is_made_and_nothing_waits(
    isolated_run_registry, monkeypatch
):
    """Off Windows the protocol does nothing (design 3, rev 35): watch the Windows CALL itself, not
    only the answers, and `await_setup` answers at once rather than at its 10 s deadline."""

    def forbidden(*args, **kwargs):
        raise AssertionError("a Windows call off Windows")

    with monkeypatch.context() as patch:
        patch.setattr(sys, "platform", "linux")
        patch.setattr(run_registry, "_create_file", forbidden)
        patch.setattr(run_registry, "probe", forbidden)
        started = time.monotonic()
        answers = (
            run_registry.register(),
            run_registry.setup_running(),
            run_registry.others_running(),
            run_registry.await_setup(),
        )
        with run_registry.registered():
            pass
        elapsed = time.monotonic() - started
    assert answers == (None, False, [], False)
    assert elapsed < 2, f"off Windows something waited {elapsed:.1f} s"


# --- M15 Task 13A-F1: the console entry registers BEFORE `pam.cli` is imported -----------------
#
# The 13A review measured the import window (`task-13a-mutations.md`): with the registration in
# `pam.cli.main`, a median of 522 ms from spawn to a visible run file, during which setup saw no
# pam (10 of 10). `pam.entry.main` registers and checks first and imports `pam.cli` after. Every
# assertion about WHAT IS IMPORTED runs in a FRESH INTERPRETER: this process imported `pam.cli`
# at the top of the file, so `sys.modules` here says nothing about pam's.

# The child: the real `pam.entry.main`, with `register` and `setup_running` spied on so it records,
# at each, whether its run file exists and whether `pam.cli` or click has been imported yet.
_ENTRY_PROBE = """
import json, sys
from pam import run_registry

seen = []


def look(when):
    seen.append(
        {
            "when": when,
            "run_file": run_registry.run_file().exists(),
            "pam.cli": "pam.cli" in sys.modules,
            "click": "click" in sys.modules,
        }
    )


register, setup_running = run_registry.register, run_registry.setup_running


def spy_register(*args, **kwargs):
    path = register(*args, **kwargs)
    look("registered")
    return path


def spy_setup_running(*args, **kwargs):
    look("setup check")
    return setup_running(*args, **kwargs)


run_registry.register = spy_register
run_registry.setup_running = spy_setup_running
from pam import entry

code = entry.main(sys.argv[1:])
look("after")
print("SEEN " + json.dumps(seen))
sys.exit(code)
"""


def _run_entry(*args):
    """(exit code, stderr, the probe's list of moments) for `pam ARGS` through `pam.entry`."""
    result = subprocess.run(
        [sys.executable, "-c", _ENTRY_PROBE, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=DEADLINE_S,
    )
    lines = [line for line in result.stdout.splitlines() if line.startswith("SEEN ")]
    assert len(lines) == 1, (result.stdout, result.stderr)
    return result.returncode, result.stderr, json.loads(lines[0].removeprefix("SEEN "))


def test_the_entry_module_imports_neither_click_nor_pam_cli():
    """Brief item 6: at module scope the entry imports the standard library, `pam.run_registry`
    and `pam.errors` and nothing else of pam's - spelled here, never read from the module."""
    probe = (
        "import sys, pam.entry\n"
        "print(sorted(name for name in sys.modules"
        " if name.partition('.')[0] not in sys.stdlib_module_names"
        " and not name.startswith('_')))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=DEADLINE_S,
        check=True,
    )
    assert result.stdout.strip() == str(["pam", "pam.entry", "pam.errors", "pam.run_registry"])


def test_the_entry_registers_and_checks_for_setup_before_pam_cli_or_click_is_imported(
    isolated_run_registry,
):
    """At the moment the run file exists, and again at the setup check, neither `pam.cli` nor click
    is in the fresh interpreter's `sys.modules`; once both have passed the command runs."""
    code, err, seen = _run_entry("--version")
    assert code == 0, err
    whens = [moment["when"] for moment in seen]
    first_register = seen[whens.index("registered")]
    first_check = seen[whens.index("setup check")]
    assert whens.index("registered") < whens.index("setup check")  # register, THEN check
    for moment in (first_register, first_check):
        assert moment == {
            "when": moment["when"],
            "run_file": WINDOWS,  # off Windows registration is a no-op (design 3)
            "pam.cli": False,
            "click": False,
        }
    assert seen[-1]["pam.cli"] is True  # and then the command ran, through `pam.cli`
    assert run_names(isolated_run_registry) == []


@windows_only
def test_the_entry_refuses_while_setup_runs_without_ever_importing_pam_cli(
    isolated_run_registry,
):
    """Setup held: the sentence, exit 1, no traceback - and `pam.cli` was never imported, so a
    setup replacing pam's modules cannot turn the refusal into a `ModuleNotFoundError`."""
    setup = Holder(isolated_run_registry / "setup.lock")
    try:
        code, err, seen = _run_entry("scan", "anything")
    finally:
        setup.close()
    assert code == 1
    assert SETUP_SENTENCE in err and "Traceback" not in err
    assert [moment["when"] for moment in seen] == ["registered", "setup check", "after"]
    assert not any(moment["pam.cli"] or moment["click"] for moment in seen)
    assert run_names(isolated_run_registry) == []


@windows_only
def test_the_entry_runs_the_command_under_one_run_file_and_it_goes_after(
    isolated_run_registry, fake_command
):
    """One registration per process: `pam.cli.main`'s own `registered()` reuses the entry's file
    (share mode 0 would refuse a second open even from this process)."""
    mine = isolated_run_registry / "run" / f"{os.getpid()}.lock"
    seen = []
    calls = fake_command(
        lambda: seen.append((run_names(isolated_run_registry), delete_open_error(mine)))
    )
    assert entry.main(["--version"]) == 0
    assert calls == [["--version"]]
    assert seen == [([mine.name], SHARING)]
    assert run_names(isolated_run_registry) == []


@windows_only
def test_the_entry_refuses_when_its_run_file_cannot_be_created_and_runs_nothing(
    isolated_run_registry, fake_command, capsys
):
    isolated_run_registry.mkdir(parents=True)
    (isolated_run_registry / "run").write_text("a file where the folder belongs")
    calls = fake_command()
    assert entry.main(["--version"]) == 1
    assert calls == []
    err = capsys.readouterr().err
    assert err.startswith("x pam cannot record that it is running in")
    assert str(isolated_run_registry / "run") in err


@windows_only
def test_the_entry_refuses_a_setup_lock_it_cannot_probe_and_releases(
    denied, fake_command, capsys
):
    root, undo = denied
    stuck = root / "setup.lock"
    stuck.write_text("x")
    _icacls(stuck, "/deny", "WHO:(DE)")
    _icacls(root, "/deny", "WHO:(DC)")
    undo += [stuck, root]
    calls = fake_command()
    assert entry.main(["--version"]) == 1
    assert calls == []
    assert capsys.readouterr().err.startswith("x pam cannot tell whether")
    assert run_names(root) == []


# --- M15 Task 13A-F1 review closures (M4404, M4405, M4409) ----------------------------------------
# The entry's own refusals are guarded by tests that run IN-PROCESS only (the fixtures above): there
# `pam.cli.main`'s retained `registered()` refuses with the same sentence and the same exit code,
# so a fail-open entry (register error swallowed, "cannot tell" read as "no setup") still passed
# every one of them - while reopening, for a pam that cannot register, the very import window
# F1 closed. These run the real entry in a FRESH interpreter and assert `pam.cli` and click were
# never imported on the way to the refusal.


@windows_only
def test_the_entry_refuses_an_unregistrable_pam_without_ever_importing_pam_cli(
    isolated_run_registry,
):
    isolated_run_registry.mkdir(parents=True)
    (isolated_run_registry / "run").write_text("a file where the folder belongs")
    code, err, seen = _run_entry("--version")
    assert code == 1
    assert (
        err.startswith("x pam cannot record that it is running in") and "Traceback" not in err
    )
    assert [moment["when"] for moment in seen] == ["after"]  # never registered, never checked
    assert not any(moment["pam.cli"] or moment["click"] for moment in seen)


@windows_only
def test_the_entry_refuses_a_setup_lock_it_cannot_probe_without_ever_importing_pam_cli(denied):
    root, undo = denied
    stuck = root / "setup.lock"
    stuck.write_text("x")
    _icacls(stuck, "/deny", "WHO:(DE)")
    _icacls(root, "/deny", "WHO:(DC)")
    undo += [stuck, root]
    code, err, seen = _run_entry("--version")
    assert code == 1
    assert err.startswith("x pam cannot tell whether") and "Traceback" not in err
    assert [moment["when"] for moment in seen] == ["registered", "setup check", "after"]
    assert not any(moment["pam.cli"] or moment["click"] for moment in seen)
    assert run_names(root) == []


def test_the_entry_returns_the_exit_code_of_the_command_it_ran(
    isolated_run_registry, monkeypatch
):
    import click

    def leave(args=None, standalone_mode=True):
        raise click.exceptions.Exit(3)

    monkeypatch.setattr(cli.cli, "main", leave)
    assert entry.main(["whatever"]) == 3


# --- O-1 (Task 13B Stage 2): a damaged payload refuses with a sentence, never a traceback ------------


def _a_missing_pam_cli(monkeypatch):
    """`import pam.cli` fails as it does when a payload file is deleted or quarantined."""
    import pam

    monkeypatch.delattr(
        pam, "cli"
    )  # else `from pam import cli` finds the attribute and never imports
    monkeypatch.setitem(sys.modules, "pam.cli", None)  # a None entry makes the import raise


def test_a_damaged_installed_pam_names_repair_and_releases_its_run_file(
    isolated_run_registry, monkeypatch, capsys
):
    from pam import install_method

    _a_missing_pam_cli(monkeypatch)
    monkeypatch.setattr(
        install_method,
        "provenance",
        lambda prefix=None: install_method.Provenance(install_method.INSTALLER, Path("app")),
    )
    assert entry.main(["--version"]) == 1
    assert capsys.readouterr().err == (
        "x pam cannot start: one of its files is missing or blocked (pam.cli)."
        ' Open the Start menu and run "Repair pam".\n'
    )
    assert run_names(isolated_run_registry) == []


def test_a_damaged_pip_pam_is_told_to_reinstall(isolated_run_registry, monkeypatch, capsys):
    from pam import install_method

    _a_missing_pam_cli(monkeypatch)
    monkeypatch.setattr(
        install_method, "provenance", lambda prefix=None: install_method.Provenance("pip", None)
    )
    assert entry.main(["--version"]) == 1
    assert capsys.readouterr().err == (
        "x pam cannot start: one of its files is missing or blocked (pam.cli)."
        " Reinstall pam, then try again.\n"
    )
