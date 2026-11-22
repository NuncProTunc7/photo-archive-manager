"""The folder chooser and the network refusal (M14 Task 4 Step 5, U13; `webui/folders.py`).

WHAT IS PINNED HERE, AND HOW:

- A listing is FOLDERS ONLY, sorted, hidden and system entries left out - each property against
  an expectation spelled by hand, never read off the production call.
- NOTHING IS OPENED (vision R2: never hydrate). Watched at the CALL: a spy on `builtins.open`,
  `io.open` and `os.open` around a listing of a folder full of files. The outcome cannot show it -
  an opened file looks exactly like an unopened one afterwards.
- THE NETWORK REFUSAL is U13's sentence, on BOTH surfaces that take a path: the chooser and bind
  (which must not trust the page). Each operand separately (T4.4): the `\\\\` prefix, and a drive
  `GetDriveTypeW` calls `DRIVE_REMOTE`. The surface tests are Windows-only (a UNC path or a drive
  letter is not even an absolute path elsewhere) and PAIRED with controls that never skip and
  drive the same predicate with a fake drive table and a fake mount table.
- No test touches the network, even under a mutation: every surface test replaces
  `resolve_archive` with a spy that RAISES, so a dropped refusal fails on the spy instead of
  asking the operating system about a share.
"""

import builtins
import io
import os
import sys
from pathlib import Path

import pytest
from test_webui_launcher import _scanned, bind, fetch, started  # noqa: F401 - fixture

from pam.errors import PamError
from pam.paths import app_root
from pam.webui import folders, launcher
from pam.webui.api import ApiRequest

# U13, word for word - spelled here, not imported.
REFUSAL = "pam works on folders on this computer. Copy the photos to this computer first."
WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32", reason="a Windows path and drive table"
)


def _get(server, query: str = "") -> tuple[int, dict]:
    import json

    reply = fetch(server, f"/t/{server.token}/api/folders{query}")
    return reply.status, json.loads(reply.body)


def _q(path) -> str:
    from urllib.parse import quote

    return "?path=" + quote(str(path), safe="")


def _tree(base: Path, folders_: list[str], files: list[str]) -> Path:
    base.mkdir()
    for name in folders_:
        (base / name).mkdir()
    for name in files:
        (base / name).write_bytes(b"not a photo, and never to be opened")
    return base


# --- the listing ------------------------------------------------------------------------


def test_the_chooser_lists_folders_only(tmp_path, started):  # noqa: F811
    """T4.5. Files - photos included - never appear; only the folders, each with its full path."""
    base = _tree(tmp_path / "pick", ["Trips", "Scans"], ["IMG_1.JPG", "notes.txt", "Trips.jpg"])
    status, body = _get(started, _q(base))
    assert status == 200, body
    root = base.resolve()
    assert body == {
        "path": str(root),
        "parent": str(root.parent),
        "folders": [
            {"name": "Scans", "path": str(root / "Scans")},
            {"name": "Trips", "path": str(root / "Trips")},
        ],
        "drives": None,
        "known": None,
    }


def test_the_folders_are_sorted_ignoring_case(tmp_path, started):  # noqa: F811
    base = _tree(tmp_path / "pick", ["beta", "Gamma", "alpha2", "Alpha"], [])
    status, body = _get(started, _q(base))
    assert status == 200
    assert [f["name"] for f in body["folders"]] == ["Alpha", "alpha2", "beta", "Gamma"]


def test_a_dot_folder_is_left_out(tmp_path, started):  # noqa: F811
    """Hidden on every platform: the control that never skips."""
    base = _tree(tmp_path / "pick", [".git", ".thumbnails", "Photos"], [".hidden.jpg"])
    status, body = _get(started, _q(base))
    assert (status, [f["name"] for f in body["folders"]]) == (200, ["Photos"])


@WINDOWS_ONLY
def test_hidden_and_system_folders_are_left_out_on_windows(tmp_path, started):  # noqa: F811
    import ctypes

    base = _tree(tmp_path / "pick", ["Hidden", "System", "Both", "Photos"], [])
    set_attributes = ctypes.WinDLL("kernel32", use_last_error=True).SetFileAttributesW
    hidden, system = 0x2, 0x4
    for name, attributes in (("Hidden", hidden), ("System", system), ("Both", hidden | system)):
        assert set_attributes(str(base / name), attributes), name
    status, body = _get(started, _q(base))
    assert (status, [f["name"] for f in body["folders"]]) == (200, ["Photos"])


def test_listing_a_folder_opens_no_file(tmp_path, monkeypatch):
    """T4.6 (vision R2). Watched at the call: a listing opens nothing at all on Windows; on
    POSIX the one file it may open is the mount table, never anything in the folder."""
    base = _tree(tmp_path / "pick", ["Trips", "Scans"], [f"IMG_{i}.JPG" for i in range(20)])
    opened: list[str] = []

    def spy(real):
        def watching(file, *args, **kwargs):
            opened.append(str(file))
            return real(file, *args, **kwargs)

        return watching

    monkeypatch.setattr(builtins, "open", spy(builtins.open))
    monkeypatch.setattr(io, "open", spy(io.open))
    monkeypatch.setattr(os, "open", spy(os.open))
    # The spy's own reach, proven first (Task 4 review R1, M3012): a spy that records nothing
    # makes "zero opens" true of any listing. One open through each entry point is seen.
    probe = base / "IMG_0.JPG"
    builtins.open(probe, "rb").close()
    io.open(probe, "rb").close()  # noqa: UP020 - the io.open spy's reach, not builtins'
    os.close(os.open(probe, os.O_RDONLY))
    assert opened == [str(probe)] * 3, opened
    opened.clear()
    answer = folders.api_folders(None, ApiRequest(params={}, query={"path": [str(base)]}))
    monkeypatch.undo()
    assert [f["name"] for f in answer["folders"]] == ["Scans", "Trips"]
    allowed = [] if sys.platform == "win32" else [folders.PROC_MOUNTS]
    assert [name for name in opened if name not in allowed] == []


# --- the top level: known archives and drives ---------------------------------------------


def test_the_top_level_lists_the_known_archives_and_the_drives(tmp_path, started):  # noqa: F811
    root, _ = _scanned(tmp_path / "a")
    gone = app_root() / "archives" / "gone-00000000"
    gone.mkdir(parents=True)
    (gone / "archive_root.txt").write_text(str(tmp_path / "unplugged"), encoding="utf-8")
    status, body = _get(started)
    assert status == 200, body
    assert (body["path"], body["parent"], body["folders"]) == (None, None, [])
    assert sorted(body["known"], key=lambda k: k["path"]) == sorted(
        [
            {"path": str(root.resolve()), "reachable": True},
            {"path": str(tmp_path / "unplugged"), "reachable": False},
        ],
        key=lambda k: k["path"],
    )
    assert isinstance(body["drives"], list)


def test_the_drives_come_from_listdrives_where_python_has_it(monkeypatch):
    monkeypatch.setattr(os, "listdrives", lambda: ["D:\\", "C:\\"], raising=False)
    monkeypatch.setattr(folders, "_drive_type", lambda root: 3)  # DRIVE_FIXED
    monkeypatch.setattr(folders, "PROC_MOUNTS", "/no/such/mount/table")
    assert folders.drives() == ["C:\\", "D:\\"]


def test_the_drives_fallback_probes_every_letter(monkeypatch):
    """No `os.listdrives` (Python before 3.12, or not Windows): `A:\\`..`Z:\\` probed in turn."""
    monkeypatch.delattr(os, "listdrives", raising=False)
    monkeypatch.setattr(folders, "_drive_type", lambda root: 3)
    monkeypatch.setattr(folders, "PROC_MOUNTS", "/no/such/mount/table")
    probed: list[str] = []

    def isdir(path):
        probed.append(path)
        return path in ("C:\\", "Q:\\")

    monkeypatch.setattr(os.path, "isdir", isdir)
    assert folders.drives() == ["C:\\", "Q:\\"]
    assert probed == [f"{letter}:\\" for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"]


@WINDOWS_ONLY
def test_a_network_drive_is_left_out_of_the_drives_and_never_probed(monkeypatch):
    monkeypatch.delattr(os, "listdrives", raising=False)
    monkeypatch.setattr(folders, "_drive_type", lambda root: 4 if root == "Z:\\" else 3)
    probed: list[str] = []
    monkeypatch.setattr(os.path, "isdir", lambda path: probed.append(path) or True)
    assert "Z:\\" not in folders.drives()
    assert "Z:\\" not in probed


def test_a_network_drive_listdrives_reports_is_left_out(monkeypatch):
    """The `os.listdrives` branch - the one Python 3.12+ on Windows takes - leaves a network
    drive out too (Task 4 review R1, M3002: only the fallback had this case). Never skips: the
    Windows branch of the predicate is driven with `sys.platform` set and a fake drive table."""
    monkeypatch.setattr(os, "listdrives", lambda: ["Z:\\", "C:\\", "E:\\"], raising=False)
    monkeypatch.setattr(folders.sys, "platform", "win32")
    types = {"C:\\": 3, "E:\\": 2, "Z:\\": 4}  # fixed, removable, DRIVE_REMOTE
    monkeypatch.setattr(folders, "_drive_type", lambda root: types[root])
    assert folders.drives() == ["C:\\", "E:\\"]


# --- refusals -----------------------------------------------------------------------------


def test_the_chooser_refuses_an_unknown_parameter_and_a_bad_path(tmp_path, started):  # noqa: F811
    status, body = _get(started, _q(tmp_path) + "&depth=2")
    assert (status, body) == (
        400,
        {"error": 'depth: "depth" is not something pam can filter by.'},
    )
    status, body = _get(started, "?show=files")
    assert (status, body) == (
        400,
        {"error": 'show: "show" is not something pam can filter by.'},
    )
    status, body = _get(started, _q(tmp_path) + "&path=" + "x")
    assert status == 400 and body["error"].startswith("path: given more than once")
    for bad in ("", "relative/folder"):
        status, body = _get(started, "?path=" + bad)
        assert (status, body) == (400, {"error": "path: give the full path of a folder."}), bad


def test_a_folder_pam_cannot_see_is_refused_with_resolve_archives_sentence(tmp_path, started):  # noqa: F811
    missing = tmp_path / "unplugged"
    status, body = _get(started, _q(missing))
    assert status == 409
    assert body["error"].startswith(
        "pam cannot see this folder, so it will not touch anything:"
    )


def test_the_chooser_answers_while_bound(tmp_path, started):  # noqa: F811
    root, _ = _scanned(tmp_path / "a")
    assert bind(started, root)[0] == 200
    status, body = _get(started, _q(tmp_path))
    assert status == 200 and started.bound
    assert "a" in [f["name"] for f in body["folders"]]


def _never_resolve(monkeypatch) -> list:
    """`resolve_archive` replaced, on both surfaces, by a spy that RAISES: a refused path must
    be refused before anything asks the filesystem - and so the network - about it."""
    calls: list = []

    def refusing(path):
        calls.append(path)
        raise PamError("resolve_archive must not be reached for a network path")

    monkeypatch.setattr(folders, "resolve_archive", refusing)
    monkeypatch.setattr(launcher, "resolve_archive", refusing)
    return calls


@WINDOWS_ONLY
def test_a_unc_path_is_refused_by_the_chooser_and_by_bind(monkeypatch, started):  # noqa: F811
    """T4.4, the `\\\\` operand, on both surfaces."""
    calls = _never_resolve(monkeypatch)
    for unc in (r"\\server\share\photos", r"\\?\C:\photos", "//server/share/photos"):
        status, body = _get(started, _q(unc))
        assert (status, body) == (409, {"error": REFUSAL}), unc
        assert bind(started, unc) == (409, {"error": REFUSAL}), unc
        assert not started.bound
    assert calls == []


@WINDOWS_ONLY
def test_a_network_drive_is_refused_by_the_chooser_and_by_bind(monkeypatch, started):  # noqa: F811
    """T4.4, the `DRIVE_REMOTE` operand: `GetDriveTypeW` itself replaced (the real ctypes call
    path in `_drive_type`, with a fake kernel32), on both surfaces."""
    asked: list[str] = []

    def get_drive_type(root):
        asked.append(root)
        return 4 if root.upper() == "Z:\\" else 3

    class Kernel32:
        GetDriveTypeW = get_drive_type

    monkeypatch.setattr(folders, "_kernel32", lambda: Kernel32)
    calls = _never_resolve(monkeypatch)
    status, body = _get(started, _q(r"Z:\photos"))
    assert (status, body) == (409, {"error": REFUSAL})
    assert bind(started, r"Z:\photos") == (409, {"error": REFUSAL})
    assert not started.bound
    assert calls == [] and "Z:\\" in asked


@WINDOWS_ONLY
def test_a_local_drive_is_not_a_network_drive_on_this_machine(tmp_path):
    """The real `GetDriveTypeW`, unpatched: the drive the suite runs on is not refused."""
    assert folders.network_refusal(tmp_path) is None


def test_the_network_refusal_reads_both_operands_on_any_platform(monkeypatch):
    """The control that NEVER skips (T4.4's pair): the same predicate, a fake drive table."""
    for platform in ("win32", "linux"):
        assert folders._is_network(r"\\server\share\photos", platform), platform
    types = {"Z:\\": 4, "C:\\": 3, "E:\\": 2}  # DRIVE_REMOTE, DRIVE_FIXED, DRIVE_REMOVABLE
    monkeypatch.setattr(folders, "_drive_type", lambda root: types[root])
    assert folders._is_network(r"Z:\photos", "win32") is True
    assert folders._is_network(r"C:\photos", "win32") is False
    assert folders._is_network(r"E:\photos", "win32") is False  # removable drives are allowed


def test_a_posix_network_mount_is_refused_when_the_mount_table_says_so(tmp_path, monkeypatch):
    table = tmp_path / "mounts"
    table.write_text(
        "/dev/sda1 / ext4 rw 0 0\n"
        "server:/export /mnt/nas nfs rw 0 0\n"
        "/dev/sdb1 /mnt/nas/local ext4 rw 0 0\n"
        "//server/share /mnt/my\\040share cifs rw 0 0\n"
        "server:/x /mnt/old smbfs rw 0 0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(folders, "PROC_MOUNTS", str(table))
    assert folders._is_network("/mnt/nas/photos", "linux") is True
    assert folders._is_network("/mnt/nas", "linux") is True
    assert folders._is_network("/mnt/my share/photos", "linux") is True
    assert folders._is_network("/mnt/old/photos", "linux") is True
    assert folders._is_network("/mnt/nas/local/photos", "linux") is False  # the longest mount
    assert folders._is_network("/mnt/nasty/photos", "linux") is False  # not under /mnt/nas
    assert folders._is_network("/home/user/photos", "linux") is False
    monkeypatch.setattr(folders, "PROC_MOUNTS", str(tmp_path / "unreadable"))
    assert folders._is_network("/mnt/nas/photos", "linux") is False  # the stated limitation
