r"""The installer, compiled and RUN (M15 Task 13B, rulings 3-8): a TEST build, never the developer's pam.

Every test compiles a test build of `tools/installer/pam.iss` over a small stub payload with its
own `AppId`, a temporary install root (in place of `%LOCALAPPDATA%\pam-install`) and a temporary
`{app}` whose name holds a space and a non-ASCII letter, runs it silently, and uninstalls at
teardown, asserting its uninstall key is gone. The module asserts, before and after, that the real
install root, program folder, Start menu folder, HKCU uninstall list and user PATH are unchanged
(`harness.real_environment`). One test, the PATH test, writes the real user PATH, and restores it
byte for byte whatever happens.

Marked `inno`: they run wherever ISCC is (`tests/inno_stack.py`), and `PAM_INNO_REQUIRED=1` turns
its absence into a collection error. `tests/test_installer_source.py` holds the guards that never
skip.

Exit codes measured on Inno Setup 6.7.4: 1 InitializeSetup refused, 2 cancelled before install,
5 aborted during install (rollback), 7 PrepareToInstall refused.
"""

import os
import subprocess
import sys
import time
import winreg
from pathlib import Path

import pytest
from inno_stack import iscc_path

from pam import install_method, run_registry
from pam.install_method import COMPONENT_LABEL
from pam.update.command import INSTALLER_FLAGS, UPDATER_PID_FLAG

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "installer"))
import harness  # noqa: E402

import build  # noqa: E402

pytestmark = pytest.mark.inno

REFUSED_AT_START = 1
CANCELLED = 2
ABORTED = 5
REFUSED_BEFORE_COPY = 7
OWN_FILES = {"pam-install.json", "unins000.exe", "unins000.dat"}


@pytest.fixture(scope="module", autouse=True)
def real_environment_untouched():
    before = harness.real_environment()
    yield
    assert harness.real_environment() == before


@pytest.fixture
def inst(tmp_path):
    install = harness.TestInstall(tmp_path / "w", iscc_path())
    yield install
    assert install.cleanup(), f"the test's uninstall key {install.key} is still there"


def payload(
    where: Path, version: str, packages: tuple[str, ...] = ("alpha", "beta"), bulk: int = 0
) -> tuple[Path, Path]:
    base, comp = where / version / "base", where / version / "comp"
    site = base / "python" / "Lib" / "site-packages"
    for name in ("pam", *packages):
        (site / name).mkdir(parents=True)
        (site / name / "__init__.py").write_text(f"VERSION = {version!r}\n")
    for i in range(bulk):  # a copy long enough for a click to land in the middle of it
        (site / "alpha" / f"bulk{i}.bin").write_bytes(os.urandom(1 << 20))
    (base / "python" / "python314._pth").write_text("python314.zip\n.\nLib\\site-packages\n")
    (base / "exiftool").mkdir()
    (base / "exiftool" / "exiftool.exe").write_bytes(b"MZ stub " + version.encode())
    (base / "pam.cmd").write_text(f"rem pam {version}\n")
    (base / "LICENSE").write_text("MIT\n")
    (comp / "onnxruntime").mkdir(parents=True)
    (comp / "onnxruntime" / "__init__.py").write_text(f"VERSION = {version!r}\n")
    return base, comp


def installer(inst, version: str = "1.0.0", **kwargs) -> Path:
    packages = kwargs.pop("packages", ("alpha", "beta"))
    bulk = kwargs.pop("bulk", 0)
    return inst.build(
        *payload(inst.work / "payload", version, packages, bulk), version, **kwargs
    )


def expected_files(
    version_packages: tuple[str, ...] = ("alpha", "beta"), reading: bool = False
) -> set[str]:
    site = "python/Lib/site-packages"
    files = {f"{site}/{name}/__init__.py" for name in ("pam", *version_packages)}
    files |= {
        "python/python314._pth",
        "exiftool/exiftool.exe",
        "pam.cmd",
        "LICENSE",
    } | OWN_FILES
    if reading:
        files.add(f"{site}/onnxruntime/__init__.py")
    return files


def await_event(inst, prefix: str, timeout: float = 60) -> str:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for line in inst.events():
            if line.startswith(prefix):
                return line
        time.sleep(0.05)
    raise AssertionError(f"no {prefix!r} event; events: {inst.events()}")


def log_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace") if path.exists() else ""


# --- a clean install, and an uninstall that removes exactly what it installed ------------------


def test_a_silent_install_writes_the_record_the_cache_and_shortcuts_inside_its_own_root(inst):
    exe = installer(inst)
    assert inst.run(exe, *INSTALLER_FLAGS) == 0
    assert inst.record() == {"method": "installer", "version": "1.0.0", "components": ["base"]}
    assert inst.files() == expected_files()
    reader = install_method.record_state(str(inst.app / "python"))  # pam's own reader agrees
    assert reader == install_method.Provenance(install_method.INSTALLER, inst.app)
    cache = inst.root / "installers"
    assert sorted(os.listdir(cache)) == ["installers.json", "pam-setup-1.0.0.exe"]
    listed = __import__("json").loads((cache / "installers.json").read_text())
    assert listed["current"] == "pam-setup-1.0.0.exe"
    assert listed["installers"] == [
        {"file": "pam-setup-1.0.0.exe", "sha256": build.sha256(cache / "pam-setup-1.0.0.exe")}
    ]
    assert build.sha256(cache / "pam-setup-1.0.0.exe") == build.sha256(exe)
    assert sorted(p.name for p in (inst.root / "menu").iterdir()) == [
        "Check for pam updates.lnk", "Repair pam.lnk", "pam.lnk",
    ]  # fmt: skip
    assert inst.value("DisplayVersion") == "1.0.0"
    assert Path(inst.value("InstallLocation") or "").resolve() == inst.app


def test_uninstall_removes_what_it_installed_and_keeps_every_file_of_the_users(inst):
    assert inst.run(installer(inst)) == 0
    (inst.app / "my notes.txt").write_text("the user's")
    (inst.root / "schema-ledger").write_text("0001_init.sql\n")
    assert inst.uninstall() == 0
    assert not inst.installed()
    assert sorted(p.relative_to(inst.app).as_posix() for p in inst.app.rglob("*")) == [
        "my notes.txt"
    ]
    assert (inst.root / "schema-ledger").read_text() == "0001_init.sql\n"
    assert (
        inst.root / "installers" / "pam-setup-1.0.0.exe"
    ).is_file()  # the cache outlives pam
    assert not (inst.root / "menu").exists() or not any((inst.root / "menu").iterdir())


def test_an_uninstall_with_nothing_of_the_users_leaves_no_folder(inst):
    assert inst.run(installer(inst)) == 0
    assert inst.uninstall() == 0
    assert inst.gone()


# --- E11: never while pam runs, and pam never while setup runs ----------------------------------


def test_setup_refuses_while_a_pam_runs_after_the_grace(inst):
    exe = installer(inst)
    child, pid = inst.hold_run_file()
    try:
        started = time.monotonic()
        assert inst.run(exe, "/LOG=" + str(inst.work / "setup.log")) == REFUSED_AT_START
        assert time.monotonic() - started >= 2.0  # A3's grace was waited out, not skipped
    finally:
        inst.release_holder(child)
    assert not inst.installed()
    assert "pam refused: pam is running. Close it (and stop pam start)" in log_text(
        inst.work / "setup.log"
    )
    assert any(line.startswith("initialize-setup 0") for line in inst.events())


def test_uninstall_refuses_while_a_pam_runs_and_removes_nothing(inst):
    assert inst.run(installer(inst)) == 0
    before = inst.files()
    child, _ = inst.hold_run_file()
    try:
        assert inst.uninstall() != 0
    finally:
        inst.release_holder(child)
    assert inst.installed()
    assert inst.files() == before
    assert any(line.startswith("initialize-uninstall 0") for line in inst.events())


def test_a_pam_that_leaves_within_the_grace_does_not_block_setup(inst):
    exe = installer(inst)
    child, _ = inst.hold_run_file()
    proc = inst.start(exe)
    time.sleep(0.5)
    inst.release_holder(child)
    assert inst.wait(proc) == 0
    assert inst.record() is not None


def test_the_updater_named_by_UPDATERPID_is_waited_for_past_the_grace(inst):
    exe = installer(inst)
    child, pid = inst.hold_run_file()
    proc = inst.start(exe, UPDATER_PID_FLAG.format(pid=pid))
    try:
        time.sleep(4.0)  # twice A3's grace: any other pam would have been refused by now
        assert proc.poll() is None
    finally:
        inst.release_holder(child)
        code = inst.wait(proc)
    assert code == 0


UPDATER = (
    "import os, sys, time\n"
    "from pam import run_registry\n"
    "from pam.update import command\n"
    "path = run_registry.register()\n"
    "command.INSTALLER_FLAGS = (*command.INSTALLER_FLAGS, *sys.argv[2:])  # silent, for the test\n"
    "command.launch_installer(sys.argv[1])\n"
    "time.sleep(4.0)\n"
    "run_registry.release(path)\n"
)


def test_pam_updates_own_argv_makes_setup_wait_for_that_pam_and_no_other(inst):
    """Both sides at once: `pam update`'s real launch (its flags and its UPDATERPID) against the
    real .iss. The updater stays registered twice A3's grace and the install still happens; a
    launch without the pid would have been refused at 2 s."""
    exe = installer(inst)
    env = dict(os.environ, PAM_RUN_REGISTRY=str(inst.root))
    updater = subprocess.run(
        [sys.executable, "-c", UPDATER, str(exe), *harness.QUIET, f"/DIR={inst.app}",
         "/LOG=" + str(inst.work / "setup.log")],
        env=env, timeout=120,
    )  # fmt: skip
    assert updater.returncode == 0
    end = time.monotonic() + 120
    while inst.record() is None and time.monotonic() < end:
        time.sleep(0.2)
    assert inst.record() is not None, log_text(inst.work / "setup.log")[-2000:]
    assert any(line.startswith("initialize-setup 1") for line in inst.events())


def test_a_stale_run_file_is_removed_and_does_not_block(inst):
    exe = installer(inst)
    stale = inst.root / "run" / "999999.lock"
    stale.parent.mkdir(parents=True)
    stale.write_text("")
    assert inst.run(exe) == 0
    assert not stale.exists()


def _pam_in(root: Path, home: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PAM_RUN_REGISTRY=str(root), PAM_HOME=str(home))
    code = "import sys; from pam.entry import main; sys.exit(main(['status']))"
    return subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60
    )


def test_setup_holds_setup_lock_from_its_start_and_a_pam_started_then_refuses(inst):
    exe = installer(inst)
    gate = inst.work / "gate"
    gate.write_text("")
    proc = inst.start(exe, f"/PAMGATE={gate}")
    try:
        await_event(inst, "gate-wait")
        assert run_registry.setup_running(inst.root)
        done = _pam_in(inst.root, inst.work / "home")
        assert done.returncode == 1, done.stderr
        assert run_registry.SETUP_RUNNING in done.stderr
        assert not (inst.work / "home").exists()  # refused before pam.cli ran at all
    finally:
        gate.unlink()
        code = inst.wait(proc)  # even when an assertion failed: never outlive the cleanup
    assert code == 0
    assert not run_registry.setup_running(inst.root)


def test_the_uninstaller_holds_setup_lock_in_the_process_that_deletes(inst):
    assert inst.run(installer(inst)) == 0
    gate = inst.work / "gate"
    gate.write_text("")
    proc = inst.uninstaller(f"/PAMGATE={gate}")
    try:
        waiting = await_event(inst, "gate-wait")
        assert run_registry.setup_running(inst.root)
        checked = await_event(inst, "initialize-uninstall 1")
        assert (
            checked.split()[-1] == waiting.split()[-1]
        )  # one process: the check covers the delete
        assert inst.installed()
    finally:
        gate.unlink()
        code = inst.wait(proc)
    assert code == 0
    assert inst.gone()


def test_a_second_installer_is_refused_not_queued(inst):
    exe = installer(inst)
    gate = inst.work / "gate"
    gate.write_text("")
    first = inst.start(exe, f"/PAMGATE={gate}")
    try:
        await_event(inst, "gate-wait")
        started = time.monotonic()
        assert inst.run(exe, "/LOG=" + str(inst.work / "second.log")) == REFUSED_AT_START
        assert time.monotonic() - started < 20
        assert "Another pam installer is running" in log_text(inst.work / "second.log")
    finally:
        gate.unlink()
        code = inst.wait(first)
    assert code == 0


# --- E12: never a payload older than the newest catalog --------------------------------------


@pytest.mark.parametrize(
    ("ledger_lines", "refused"),
    [
        (["0001_init.sql", "9999_from_a_newer_pam.sql"], True),
        (["9999_from_a_newer_pam.sql\r"], True),  # a CRLF line still counts
        ([], False),  # an empty ledger
        (["0001_init.sql"], False),  # older than the payload
        (["9999_NOT_A_NAME.sql", "9999_x.sql.bak", "9999_x.sql ", "garbage"], False),  # no name
    ],
    ids=["higher", "higher-crlf", "empty", "lower", "garbled"],
)
def test_the_ledger_decides_whether_this_payload_may_replace_pam(inst, ledger_lines, refused):
    exe = installer(inst)
    inst.root.mkdir(parents=True)
    (inst.root / "schema-ledger").write_bytes("".join(f"{x}\n" for x in ledger_lines).encode())
    code = inst.run(exe, "/LOG=" + str(inst.work / "setup.log"))
    assert code == (REFUSED_BEFORE_COPY if refused else 0)
    assert inst.installed() is not refused
    if refused:
        # Task 12-G (F4): the maintainer's sentence, spelled here; no migration number in it.
        sentence = (
            "pam refused: pam's installer will not install pam 1.0.0: a newer pam has already"
            " updated a catalog on this PC, and pam 1.0.0 could not open it. Nothing was changed."
            " If you no longer use any catalog a newer pam opened, delete"
            f" {inst.root / 'schema-ledger'} and try again."
        )
        assert sentence in log_text(inst.work / "setup.log")
        assert (
            "9999"
            not in log_text(inst.work / "setup.log").split("pam refused: ")[1].splitlines()[0]
        )


def test_the_ledger_at_the_payloads_own_level_is_no_constraint(inst):
    exe = installer(inst)
    inst.root.mkdir(parents=True)
    (inst.root / "schema-ledger").write_text(build.highest_migration() + "\n")
    assert inst.run(exe) == 0


def test_a_missing_ledger_is_no_constraint_and_an_unreadable_one_refuses(inst):
    exe = installer(inst)
    (inst.root / "schema-ledger").mkdir(parents=True)  # a folder: present, and not readable
    assert inst.run(exe, "/LOG=" + str(inst.work / "setup.log")) == REFUSED_BEFORE_COPY
    assert "cannot read" in log_text(inst.work / "setup.log")
    (inst.root / "schema-ledger").rmdir()
    assert inst.run(exe) == 0


def test_the_ledger_also_guards_an_upgrade_and_leaves_the_previous_pam_whole(inst):
    assert inst.run(installer(inst, "1.0.0")) == 0
    exe = installer(inst, "1.0.1")
    before = {p: (inst.app / p).read_bytes() for p in inst.files() if p != "unins000.dat"}
    (inst.root / "schema-ledger").write_text("9999_from_a_newer_pam.sql\n")
    assert inst.run(exe) == REFUSED_BEFORE_COPY
    assert {p: (inst.app / p).read_bytes() for p in before} == before
    assert inst.value("DisplayVersion") == "1.0.0"


def test_the_installer_reads_no_PAM_variable(inst):
    """Ruling 5: PAM_SCHEMA_LEDGER and PAM_RUN_REGISTRY point at a ledger that would refuse and a
    pam that is running; the installer, which knows only its own root, installs anyway."""
    exe = installer(inst)
    elsewhere = inst.work / "elsewhere"
    elsewhere.mkdir(parents=True)
    (elsewhere / "schema-ledger").write_text("9999_from_a_newer_pam.sql\n")
    env = dict(
        os.environ,
        PAM_RUN_REGISTRY=str(elsewhere),
        PAM_SCHEMA_LEDGER=str(elsewhere / "schema-ledger"),
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", harness.HOLDER],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert holder.stdout is not None
        holder.stdout.readline()
        assert list((elsewhere / "run").iterdir())
        proc = subprocess.Popen([str(exe), *harness.QUIET, f"/DIR={inst.app}"], env=env)
        assert inst.wait(proc) == 0
    finally:
        inst.release_holder(holder)
    assert inst.record() is not None


# --- Task 13B-F: an {app} too long for pam's files is refused before anything is copied ----------

MAX_PATH = 260  # spelled from Windows' limit, not read from pam.iss


def _app_of_length(inst, length: int) -> Path:
    """A folder in this test's world whose full path is exactly `length` characters."""
    base = str(inst.work) + "\\long "
    assert len(base) < length - 10
    return Path(base + "x" * (length - len(base)))


def _longest(inst) -> int:
    return build.longest_path(*payload(inst.work / "measured", "1.0.0"))


def test_a_folder_too_long_for_pams_files_is_refused_on_the_folder_page(inst):
    """The wizard's folder page (reached by a silent fresh install too): refused at a full path of
    260, nothing installed; one character shorter installs, with the component."""
    exe = installer(inst)
    longest = _longest(inst)
    inst.app = _app_of_length(inst, MAX_PATH - 1 - longest)  # {app} + the longest reaches 260
    log = inst.work / "setup.log"
    assert (
        inst.run(exe, "/LOG=" + str(log)) == REFUSED_AT_START
    )  # 6.7.4: a silent refusal on a page
    assert not inst.installed() and not inst.app.exists()
    assert "pam refused: pam's installer cannot install into " + str(inst.app) in log_text(log)
    assert "Choose a shorter folder." in log_text(log)
    assert not any(line.startswith("prepare-to-install") for line in inst.events())
    inst.app = _app_of_length(inst, MAX_PATH - 2 - longest)  # 259: the longest still fits
    fit = inst.work / "fit.log"
    assert inst.run(exe, "/COMPONENTS=reading", "/LOG=" + str(fit)) == 0, log_text(fit)[-3000:]
    assert inst.files() == expected_files(reading=True)


def test_an_upgrade_into_a_folder_too_long_is_refused_before_the_copy(inst):
    """An upgrade never shows the folder page; PrepareToInstall refuses it (exit 7) and the
    installed pam is left whole."""
    assert inst.run(installer(inst, "1.0.0")) == 0
    exe = installer(inst, "1.0.1")
    before = {p: (inst.app / p).read_bytes() for p in inst.files() if p != "unins000.dat"}
    short = inst.app
    inst.app = _app_of_length(inst, MAX_PATH - 1 - _longest(inst))
    log = inst.work / "setup.log"
    try:
        assert inst.run(exe, "/LOG=" + str(log)) == REFUSED_BEFORE_COPY
        assert not inst.app.exists()
    finally:
        inst.app = short
    assert "pam refused: pam's installer cannot install into " in log_text(log)
    assert {p: (inst.app / p).read_bytes() for p in before} == before
    assert inst.value("DisplayVersion") == "1.0.0"


# --- components, upgrades and the cache ---------------------------------------------------------


def test_the_component_is_installed_kept_on_upgrade_and_removed_when_deselected(inst):
    assert inst.run(installer(inst, "1.0.0"), "/COMPONENTS=reading") == 0
    assert inst.record()["components"] == ["base", "reading"]
    assert inst.files() == expected_files(reading=True)
    assert inst.run(installer(inst, "1.0.1")) == 0  # an upgrade that says nothing about it
    assert inst.record() == {
        "method": "installer",
        "version": "1.0.1",
        "components": ["base", "reading"],
    }
    assert inst.files() == expected_files(reading=True)
    assert (
        inst.app / "python/Lib/site-packages/onnxruntime/__init__.py"
    ).read_text() == "VERSION = '1.0.1'\n"
    assert inst.run(installer(inst, "1.0.2"), "/TYPE=base") == 0
    assert inst.record()["components"] == ["base"]
    assert inst.files() == expected_files()  # deselected: none of its files is left


def test_the_component_selector_shows_the_label_pam_reads(inst):
    exe = installer(inst)
    assert inst.run(exe, "/LOG=" + str(inst.work / "setup.log")) == 0
    assert inst.value("Inno Setup: Selected Components") == "base"
    assert COMPONENT_LABEL == "Face and text reading"  # spelled from the brief, not from pam


def test_an_upgrade_leaves_no_file_of_a_package_the_new_payload_dropped(inst):
    assert inst.run(installer(inst, "1.0.0", packages=("alpha", "beta"))) == 0
    assert inst.run(installer(inst, "1.0.1", packages=("alpha", "gamma"))) == 0
    assert inst.files() == expected_files(("alpha", "gamma"))
    assert (inst.app / "pam.cmd").read_text() == "rem pam 1.0.1\n"


def test_the_cache_keeps_the_current_installer_and_the_previous_one_only(inst):
    for version in ("1.0.0", "1.0.1", "1.0.2"):
        assert inst.run(installer(inst, version)) == 0
    cache = inst.root / "installers"
    assert sorted(os.listdir(cache)) == [
        "installers.json",
        "pam-setup-1.0.1.exe",
        "pam-setup-1.0.2.exe",
    ]
    listed = __import__("json").loads((cache / "installers.json").read_text())
    assert listed["current"] == "pam-setup-1.0.2.exe"
    assert {e["file"]: e["sha256"] for e in listed["installers"]} == {
        name: build.sha256(cache / name)
        for name in ("pam-setup-1.0.2.exe", "pam-setup-1.0.1.exe")
    }


def test_Repair_keeps_the_previous_versions_cached_installer(inst):
    """F-P2: running the cached current installer ("Repair pam") is a re-run of the same version,
    and it must keep the way back: the cache still holds 1.0.0 and 1.0.1, listed with their
    SHA-256s, after one Repair and after a second."""
    assert inst.run(installer(inst, "1.0.0")) == 0
    assert inst.run(installer(inst, "1.0.1")) == 0
    cache = inst.root / "installers"
    both = ["installers.json", "pam-setup-1.0.0.exe", "pam-setup-1.0.1.exe"]
    assert sorted(os.listdir(cache)) == both
    for _ in range(2):
        assert inst.run(cache / "pam-setup-1.0.1.exe") == 0
        assert sorted(os.listdir(cache)) == both
        listed = __import__("json").loads((cache / "installers.json").read_text())
        assert listed["current"] == "pam-setup-1.0.1.exe"
        assert {e["file"]: e["sha256"] for e in listed["installers"]} == {
            name: build.sha256(cache / name)
            for name in ("pam-setup-1.0.1.exe", "pam-setup-1.0.0.exe")
        }
    assert inst.value("DisplayVersion") == "1.0.1"


# --- failure, rollback and Repair (Q8's cases, at stub scale) -----------------------------------


def _held(path: Path):
    import ctypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = ctypes.c_void_p
    handle = k32.CreateFileW(
        str(path), 0x80000000, 0, None, 3, 0, None
    )  # GENERIC_READ, share 0
    assert handle not in (None, ctypes.c_void_p(-1).value)
    return k32, handle


def test_an_in_use_file_aborts_the_upgrade_and_Repair_restores_the_previous_pam(inst):
    """Ruling 4: never a forced replacement. Inno 6.7.4's rollback does NOT restore the files it
    had already replaced (measured), so the previous pam is recovered by Repair pam, the cached
    installer of the version still recorded."""
    assert inst.run(installer(inst, "1.0.0")) == 0
    exe = installer(inst, "1.0.1")
    k32, handle = _held(inst.app / "python/Lib/site-packages/beta/__init__.py")
    try:
        assert inst.run(exe, *INSTALLER_FLAGS) == ABORTED
    finally:
        k32.CloseHandle(__import__("ctypes").c_void_p(handle))
    assert inst.value("DisplayVersion") == "1.0.0"
    assert inst.record()["version"] == "1.0.0"
    assert sorted(os.listdir(inst.root / "installers")) == [
        "installers.json",
        "pam-setup-1.0.0.exe",
    ]
    assert inst.run(inst.root / "installers" / "pam-setup-1.0.0.exe") == 0
    assert inst.files() == expected_files()
    assert (inst.app / "pam.cmd").read_text() == "rem pam 1.0.0\n"


def test_a_copy_that_fails_rolls_back_and_Repair_restores(inst):
    assert inst.run(installer(inst, "1.0.0")) == 0
    exe = installer(inst, "1.0.1")
    assert inst.run(exe, "/PAMFAULT=copy") == ABORTED
    assert any(line.startswith("fault-copy") for line in inst.events())
    assert inst.value("DisplayVersion") == "1.0.0"
    blocker = inst.app / "fault-probe.txt"
    (blocker / "blocker").unlink()
    blocker.rmdir()
    assert inst.run(inst.root / "installers" / "pam-setup-1.0.0.exe") == 0
    assert inst.files() == expected_files()


def test_Repair_puts_back_a_quarantined_file(inst):
    assert inst.run(installer(inst)) == 0
    victim = inst.app / "python/Lib/site-packages/alpha/__init__.py"
    victim.unlink()
    assert inst.run(inst.root / "installers" / "pam-setup-1.0.0.exe") == 0
    assert victim.read_text() == "VERSION = '1.0.0'\n"


@pytest.mark.parametrize("page", [7, 9, 10], ids=["components", "tasks", "ready"])
def test_cancelling_the_wizard_before_install_leaves_the_previous_pam_untouched(inst, page):
    assert inst.run(installer(inst, "1.0.0")) == 0
    exe = installer(inst, "1.0.1")
    before = {p: (inst.app / p).read_bytes() for p in inst.files()}
    assert inst.run(exe, f"/PAMCANCELAT={page}", quiet=False, timeout=90) == CANCELLED
    assert f"cancel {page}" in " ".join(inst.events())
    assert {p: (inst.app / p).read_bytes() for p in inst.files()} == before


def test_cancelling_during_the_copy_aborts_and_Repair_restores(inst):
    assert inst.run(installer(inst, "1.0.0")) == 0
    exe = installer(inst, "1.0.1", bulk=40)
    assert inst.run(exe, "/PAMFAULT=cancel", quiet=False, timeout=90) == ABORTED
    assert "cancel 12" in " ".join(inst.events())
    assert inst.value("DisplayVersion") == "1.0.0"
    assert inst.run(inst.root / "installers" / "pam-setup-1.0.0.exe") == 0
    assert inst.files() == expected_files()


# --- the one test that writes the real user PATH ------------------------------------------------


def _user_path() -> tuple[object, int] | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return winreg.QueryValueEx(key, "Path")
    except OSError:
        return None


def _restore_user_path(saved: tuple[object, int] | None) -> None:
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
    ) as key:
        if saved is None:
            try:
                winreg.DeleteValue(key, "Path")
            except OSError:
                pass
        else:
            winreg.SetValueEx(key, "Path", 0, saved[1], saved[0])


def test_the_PATH_task_appends_app_last_and_uninstall_removes_only_that_entry(inst):
    saved = _user_path()
    old = "" if saved is None else str(saved[0])

    def assert_restored() -> None:
        if saved is not None and saved[1] == winreg.REG_EXPAND_SZ and not old.endswith(";"):
            assert _user_path() == saved  # byte for byte: the entry was still last
        else:  # a REG_SZ PATH comes back REG_EXPAND_SZ, and a trailing ';' is not restored
            assert str(_user_path()[0]).rstrip(";") == old.rstrip(";")

    try:
        exe = installer(inst)
        assert inst.run(exe, "/TASKS=path") == 0
        value, kind = _user_path()
        assert value == (old + ("" if not old or old.endswith(";") else ";") + str(inst.app))
        assert kind == winreg.REG_EXPAND_SZ
        assert inst.uninstall() == 0
        assert_restored()
        # Stage 1 review (M4628): running the installer again with the task adds no second
        # entry, and uninstall still takes the one entry back.
        _restore_user_path(saved)
        assert inst.run(exe, "/TASKS=path") == 0
        assert inst.run(exe, "/TASKS=path") == 0
        parts = [p.upper() for p in str(_user_path()[0]).split(";")]
        assert parts.count(str(inst.app).upper()) == 1
        assert inst.uninstall() == 0
        assert_restored()
        # Stage 1 review (M4610): the user edits the PATH after the install, so the entry is no
        # longer last. Uninstall removes that ONE entry and keeps the edits, including one that
        # merely begins with {app} (a folder inside it is the user's, not the installer's).
        _restore_user_path(saved)
        assert inst.run(exe, "/TASKS=path") == 0
        value, kind = _user_path()
        edits = [str(inst.app) + r"\tools", r"C:\pam-review-user-edit"]
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, "Path", 0, kind, f"{value};{';'.join(edits)}")
        # Stage 2 review (M4811): running the installer again with the task (Repair pam, an
        # upgrade) leaves a PATH the user edited exactly as it is: pam's entry is neither taken
        # out nor moved to the end.
        edited = _user_path()
        assert inst.run(exe, "/TASKS=path") == 0
        assert _user_path() == edited
        assert inst.uninstall() == 0
        kept = old if not old or old.endswith(";") else old + ";"
        assert _user_path() == (kept + ";".join(edits), winreg.REG_EXPAND_SZ)
    finally:
        _restore_user_path(saved)
    assert _user_path() == saved


RECORDED = "Inno Setup CodeFile: PathAdded"  # where RegisterPreviousData keeps it (6.7.4)


def _set_user_path(value: str, kind: int) -> None:
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, "Path", 0, kind, value)


def test_deselecting_the_PATH_task_on_a_rerun_takes_the_entry_back(inst):
    """F-P1 (M4629): a re-run with the task deselected removes the entry the previous run added,
    records nothing, and a later run with the task puts it back once and uninstall still takes it
    away. Inno 6.7.4 re-creates the uninstall key before ssPostInstall, so the previous value has
    to be read before the install step."""
    saved = _user_path()
    old = "" if saved is None else str(saved[0])
    try:
        exe = installer(inst)
        assert inst.run(exe, "/TASKS=path") == 0
        assert inst.value(RECORDED) == str(inst.app)
        assert inst.run(exe, "/MERGETASKS=!path") == 0
        assert str(_user_path()[0]).rstrip(";") == old.rstrip(";")
        assert inst.value(RECORDED) in (None, "")
        assert inst.run(exe, "/MERGETASKS=path") == 0
        parts = [p.upper() for p in str(_user_path()[0]).split(";")]
        assert parts.count(str(inst.app).upper()) == 1
        assert inst.uninstall() == 0  # the entry is not forgotten
        assert str(_user_path()[0]).rstrip(";") == old.rstrip(";")
    finally:
        _restore_user_path(saved)
    assert _user_path() == saved


def test_an_app_folder_already_on_the_users_PATH_is_the_users_and_stays(inst):
    """F-P3: an `{app}` the user put on their PATH before pam's installer ran is never recorded as
    the installer's, so no uninstall and no deselecting re-run takes it away, with or without the
    task. The other direction: an entry the installer added IS recorded (and removed, above)."""
    saved = _user_path()
    old = "" if saved is None else str(saved[0])
    kind = winreg.REG_EXPAND_SZ if saved is None else saved[1]
    mine = (old + ";" if old and not old.endswith(";") else old) + str(inst.app)
    try:
        _set_user_path(mine, kind)
        exe = installer(inst)
        assert inst.run(exe, "/TASKS=path") == 0
        assert inst.value(RECORDED) in (None, "")
        assert _user_path() == (mine, kind)  # not appended a second time
        assert inst.run(exe, "/MERGETASKS=!path") == 0
        assert _user_path() == (mine, kind)
        assert inst.uninstall() == 0
        assert _user_path() == (mine, kind)
        assert inst.run(exe) == 0  # without the task at all
        assert inst.uninstall() == 0
        assert _user_path() == (mine, kind)
        _restore_user_path(saved)
        assert inst.run(exe, "/TASKS=path") == 0  # the other direction: the installer's own
        assert inst.value(RECORDED) == str(inst.app)
    finally:
        _restore_user_path(saved)
    assert _user_path() == saved


EVERYONE = "*S-1-1-0"  # a well-known SID: no user name is written anywhere


def test_a_run_file_setup_cannot_probe_refuses_and_never_reads_as_absent(inst):
    """10C A2 / M4: a run file whose DELETE is denied on the file AND on its folder cannot be
    probed. Setup must refuse ("cannot tell") after the 2 s retry, never treat it as absent."""
    exe = installer(inst)
    run = inst.root / "run"
    run.mkdir(parents=True)
    held = run / "4242.lock"
    held.write_text("")
    deny = [
        ["icacls", str(held), "/deny", f"{EVERYONE}:(DE)"],
        ["icacls", str(run), "/deny", f"{EVERYONE}:(DC)"],
    ]
    try:
        for command in deny:
            subprocess.run(command, check=True, capture_output=True)
        assert inst.run(exe, "/LOG=" + str(inst.work / "setup.log")) == REFUSED_AT_START
    finally:
        for path in (run, held):
            subprocess.run(
                ["icacls", str(path), "/remove:d", EVERYONE], check=False, capture_output=True
            )
    assert not inst.installed()
    assert "pam refused: pam's installer cannot tell whether pam is running" in log_text(
        inst.work / "setup.log"
    )


def test_the_iscc_these_tests_compile_with_reports_the_pinned_version():
    """Task 13B-F: the version check `build.py` makes, against the real ISCC: 6.7.4."""
    assert build.iscc_version(iscc_path()) == "6.7.4"
    build.check_iscc(iscc_path())
