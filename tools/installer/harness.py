r"""Drive a TEST BUILD of pam's installer (M15 Task 13B, ruling 6): never the developer's environment.

A `TestInstall` owns one temporary world: an install root (the test build's `PamTestRoot`, in place
of `%LOCALAPPDATA%\pam-install`), an `{app}` folder whose name holds a space and a non-ASCII
letter, its own shortcut folder (the test build never writes the Start menu), and a fresh `AppId`,
so its HKCU uninstall key is its own. `cleanup()` uninstalls whatever is still installed and
reports whether the uninstall key is gone; a key the uninstaller could not remove is deleted only
when its install location is inside this world.

Used by `tests/test_installer_behaviour.py` and by `tools/installer/q8_measure.py`.
"""

import contextlib
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
import winreg
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import _proctree  # noqa: E402

import build  # noqa: E402

UNINSTALL_KEYS = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
QUIET = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
REAL_ROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "pam-install"
# The holder: a child that takes a run file the way every pam does, says its OWN pid (a venv's
# python.exe is a launcher, 10C M5), and holds the file until its stdin closes or it is killed.
HOLDER = (
    "import os, sys\n"
    "from pam import run_registry\n"
    "path = run_registry.register()\n"
    "print(os.getpid(), flush=True)\n"
    "sys.stdin.read()\n"
    "run_registry.release(path)\n"
)


class TestInstall:
    __test__ = False  # not a pytest class

    def __init__(self, work: Path, iscc: Path, app_name: str = "pam app ü") -> None:
        self.work = work.resolve()
        self.root = self.work / "root"
        if REAL_ROOT.resolve() in (self.root, *self.root.parents):
            raise RuntimeError(f"a test build must never use the real install root {REAL_ROOT}")
        self.app = self.work / app_name
        self.iscc = iscc
        self.app_id = "{{" + str(uuid.uuid4()).upper() + "}"
        self.key = UNINSTALL_KEYS + "\\" + self.app_id[1:] + "_is1"

    # --- build and run ---------------------------------------------------------------------------

    def build(
        self, payload: Path, component: Path, version: str = "1.0.0", **values: str
    ) -> Path:
        out = self.work / "out" / version
        merged = build.defines(payload, component, out)
        merged["PamVersion"] = version
        merged.update(values)
        test = {"PamTestRoot": str(self.root), "PamTestAppId": self.app_id}
        done = build.compile_installer(self.iscc, merged, test)
        if done.returncode != 0:
            raise RuntimeError(f"ISCC failed:\n{done.stdout}{done.stderr}")
        return out / f"pam-setup-{version}.exe"

    def start(self, exe: Path, *flags: str, quiet: bool = True) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [str(exe), *(QUIET if quiet else ()), f"/DIR={self.app}", *flags]
        )

    def run(self, exe: Path, *flags: str, quiet: bool = True, timeout: float = 180) -> int:
        return self.wait(self.start(exe, *flags, quiet=quiet), timeout)

    @staticmethod
    def wait(proc: subprocess.Popen[bytes], timeout: float = 180) -> int:
        try:
            return proc.wait(timeout)
        except subprocess.TimeoutExpired:
            _proctree.kill_tree(proc.pid)
            raise

    def uninstaller(self, *flags: str) -> subprocess.Popen[bytes]:
        command = self.value("UninstallString")
        if command is None:
            raise RuntimeError("nothing is installed under this test's AppId")
        return subprocess.Popen([command.strip('"'), *QUIET, *flags])

    def uninstall(self, *flags: str, timeout: float = 120) -> int:
        """The uninstaller's exit code, once its clone (which does the work) has finished.

        The original process can exit before its clone has run usPostUninstall (the PATH entry's
        removal): measured under a loaded full suite, the key and unins000.exe were gone and the
        PATH was not yet restored. A test build logs every uninstall step, so this waits for the
        clone's usDone (3), which comes after usPostUninstall (2) has returned."""
        done = sum(line.startswith("uninstall-step 3 ") for line in self.events())
        code = self.wait(self.uninstaller(*flags), timeout)
        end = time.monotonic() + 60
        while code == 0 and time.monotonic() < end:
            finished = (
                sum(line.startswith("uninstall-step 3 ") for line in self.events()) > done
            )
            if finished and not self.installed() and not (self.app / "unins000.exe").exists():
                break
            time.sleep(0.05)
        return code

    # --- what is on disk and in the registry --------------------------------------------------

    def value(self, name: str) -> str | None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key) as key:
                return str(winreg.QueryValueEx(key, name)[0])
        except OSError:
            return None

    def installed(self) -> bool:
        try:
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key).Close()
            return True
        except OSError:
            return False

    def files(self) -> set[str]:
        if not self.app.exists():
            return set()
        return {p.relative_to(self.app).as_posix() for p in self.app.rglob("*") if p.is_file()}

    def record(self) -> dict[str, object] | None:
        path = self.app / "pam-install.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def gone(self, timeout: float = 30) -> bool:
        """Whether `{app}` disappears: the uninstaller's clone removes it just after it exits."""
        end = time.monotonic() + timeout
        while self.app.exists() and time.monotonic() < end:
            time.sleep(0.05)
        return not self.app.exists()

    def events(self) -> list[str]:
        """The test build's event lines. Setup appends while a test reads: a read that meets the
        append (a sharing violation, PermissionError) is retried for up to a second (Stage 2
        review, T-1: two full-suite runs failed on it)."""
        path = self.root / "events.log"
        for _ in range(100):
            try:
                return path.read_text(encoding="utf-8").splitlines() if path.exists() else []
            except PermissionError:
                time.sleep(0.01)
        return path.read_text(encoding="utf-8").splitlines()

    # --- a pam that is running ------------------------------------------------------------------

    def hold_run_file(self) -> tuple[subprocess.Popen[str], int]:
        """A child holding `root\\run\\<its pid>.lock`, until `release_holder`."""
        env = dict(os.environ, PAM_RUN_REGISTRY=str(self.root))
        child = subprocess.Popen(
            [sys.executable, "-c", HOLDER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, env=env,
        )  # fmt: skip
        assert child.stdout is not None
        return child, int(child.stdout.readline())

    @staticmethod
    def release_holder(child: subprocess.Popen[str]) -> None:
        assert child.stdin is not None
        child.stdin.close()
        child.wait(30)

    # --- teardown --------------------------------------------------------------------------------

    def cleanup(self) -> bool:
        """Uninstall anything still installed; True when this test's uninstall key is gone."""
        if self.installed():
            with contextlib.suppress(OSError, RuntimeError, subprocess.TimeoutExpired):
                self.uninstall("/LOG=" + str(self.work / "cleanup-uninstall.log"))
        if self.installed():
            location = self.value("InstallLocation") or ""
            if location and Path(location).resolve().is_relative_to(self.work):
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, self.key)
        return not self.installed()


def real_environment() -> dict[str, object]:
    """What a test build must never change: the real install root, the real program folder and
    Start menu folder, the HKCU uninstall list, and the user PATH (value and kind, by hash)."""
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    menu = (
        Path(os.environ.get("APPDATA", ""))
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
    )
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEYS) as key:
            names = sorted(winreg.EnumKey(key, i) for i in range(winreg.QueryInfoKey(key)[0]))
    except FileNotFoundError:  # a fresh account (a hosted runner) has no Uninstall key yet
        names = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, kind = winreg.QueryValueEx(key, "Path")
        path = (kind, hashlib.sha256(str(value).encode("utf-16-le")).hexdigest())
    except OSError:
        path = (None, None)
    real = local / "pam-install"
    return {
        # every name under the real install root, not only whether it exists: a pam run without
        # PAM_RUN_REGISTRY creates run\ there (it did once, 2026-11-20, from a bare `pam --help`)
        "pam-install": sorted(p.relative_to(real).as_posix() for p in real.rglob("*"))
        if real.exists()
        else None,
        "Programs/pam": (local / "Programs" / "pam").exists(),
        "Start menu pam": (menu / "pam").exists(),
        "uninstall keys": names,
        "user PATH": path,
    }
