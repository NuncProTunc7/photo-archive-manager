r"""The installer tests' own instruments (M15 Task 13B, Stage 1 review): the guard that the maintainer's
real environment is untouched, and the refusal to build a test world inside the real install root.

A guard that is never itself tested passes everything (CLAUDE.md, "mutate the code that TESTS the
code"). Each expectation here is an independent second reading, never `harness`'s own.
"""

import hashlib
import sys
import winreg
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "installer"))
import harness  # noqa: E402
import test_installer_behaviour  # noqa: E402


def _fake_profile(tmp_path, monkeypatch):
    local, roaming = tmp_path / "local", tmp_path / "roaming"
    local.mkdir()
    roaming.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    return local, roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def test_real_environment_sees_every_name_under_the_install_root_and_the_program_folders(
    tmp_path, monkeypatch
):
    local, menu = _fake_profile(tmp_path, monkeypatch)
    before = harness.real_environment()
    assert before["pam-install"] is None
    assert before["Programs/pam"] is False and before["Start menu pam"] is False
    (local / "pam-install" / "run").mkdir(parents=True)
    (local / "pam-install" / "run" / "4242.lock").write_text("")
    (local / "Programs" / "pam").mkdir(parents=True)
    (menu / "pam").mkdir(parents=True)
    after = harness.real_environment()
    assert after["pam-install"] == ["run", "run/4242.lock"]  # nested, not only the top level
    assert after["Programs/pam"] is True and after["Start menu pam"] is True
    assert after != before


def test_real_environment_reads_the_uninstall_list_and_the_PATH_value_and_kind():
    """A second reading of the same two registry values, written here, not taken from harness."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, harness.UNINSTALL_KEYS) as key:
            count = winreg.QueryInfoKey(key)[0]
            names = sorted(winreg.EnumKey(key, i) for i in range(count))
    except FileNotFoundError:
        names = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, kind = winreg.QueryValueEx(key, "Path")
        path = (kind, hashlib.sha256(str(value).encode("utf-16-le")).hexdigest())
    except OSError:
        path = (None, None)
    seen = harness.real_environment()
    assert seen["uninstall keys"] == names
    assert seen["user PATH"] == path


def test_the_behaviour_modules_fixture_fails_when_the_real_environment_changed(monkeypatch):
    states = iter([{"pam-install": None}, {"pam-install": ["run"]}])
    monkeypatch.setattr(harness, "real_environment", lambda: next(states))
    fixture = test_installer_behaviour.real_environment_untouched._get_wrapped_function()
    steps = fixture()
    next(steps)
    with pytest.raises(AssertionError):
        next(steps)


def test_the_behaviour_modules_fixture_passes_when_nothing_changed(monkeypatch):
    """The negative control: the same fixture over an unchanged environment finishes cleanly."""
    monkeypatch.setattr(harness, "real_environment", lambda: {"pam-install": None})
    fixture = test_installer_behaviour.real_environment_untouched._get_wrapped_function()
    steps = fixture()
    next(steps)
    with pytest.raises(StopIteration):
        next(steps)


def test_a_test_world_inside_the_real_install_root_is_refused(tmp_path, monkeypatch):
    real = tmp_path / "pam-install"
    monkeypatch.setattr(harness, "REAL_ROOT", real)
    with pytest.raises(RuntimeError, match="never use the real install root"):
        harness.TestInstall(real, Path("ISCC.exe"), "x")  # its root inside the real one
    with pytest.raises(RuntimeError, match="never use the real install root"):
        harness.TestInstall(real / "deeper", Path("ISCC.exe"), "x")
    harness.TestInstall(tmp_path / "elsewhere", Path("ISCC.exe"), "x")  # the control


def test_real_environment_reads_a_missing_uninstall_key_as_no_programs(monkeypatch):
    """A fresh Windows account (the hosted runner, 2026-11-21) has no Uninstall key at all: nothing
    is installed, so the reading is an empty list, never a crash."""
    missing = harness.UNINSTALL_KEYS + r"\pam-test-no-such-key-0f3a"
    with pytest.raises(FileNotFoundError):
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, missing).Close()
    monkeypatch.setattr(harness, "UNINSTALL_KEYS", missing)
    assert harness.real_environment()["uninstall keys"] == []
