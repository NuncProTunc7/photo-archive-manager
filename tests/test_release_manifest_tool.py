"""`tools/release_manifest.py` (M15 Task 9, E5): `pam-release.json` from a built installer.

EVERY EXPECTATION IS A LITERAL: the installer's bytes, their SHA-256 and size (computed once, by
hand, with `sha256sum` and `wc -c`), the eight shipped migration names, and the exact text written.
Nothing here re-derives an expectation with the code under test.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import release_manifest  # noqa: E402

INSTALLER_BYTES = b"not a real installer, M15 Task 9\n"
INSTALLER_SHA256 = "fd05c0d6f7dfdfe544597ef1f6039412fd5b6b5b8b4f5cec882e25aec525f17d"
INSTALLER_SIZE = 33

SHIPPED = (
    "0001_init.sql",
    "0002_scanner.sql",
    "0003_video_reread.sql",
    "0004_plan_unit_key.sql",
    "0005_plan_already_conforming.sql",
    "0006_event_label_source.sql",
    "0007_face_review_state.sql",
    "0008_files_rel_path_nocase.sql",
)

EXPECTED_STABLE = """{
  "version": "1.0.0",
  "channel": "stable",
  "installer": "pam-setup-1.0.0.exe",
  "sha256": "fd05c0d6f7dfdfe544597ef1f6039412fd5b6b5b8b4f5cec882e25aec525f17d",
  "size": 33,
  "schema": "0008_files_rel_path_nocase.sql",
  "migrations": [
    "0001_init.sql",
    "0002_scanner.sql",
    "0003_video_reread.sql",
    "0004_plan_unit_key.sql",
    "0005_plan_already_conforming.sql",
    "0006_event_label_source.sql",
    "0007_face_review_state.sql",
    "0008_files_rel_path_nocase.sql"
  ]
}
"""


def _payload(tmp_path: Path, names=SHIPPED) -> Path:
    payload = tmp_path / "payload"
    folder = payload / "pam" / "catalog" / "migrations"
    folder.mkdir(parents=True)
    for name in names:
        (folder / name).write_bytes(b"-- a migration\n")
    return payload


def _installer(tmp_path: Path, name="pam-setup-1.0.0.exe") -> Path:
    path = tmp_path / "out" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(INSTALLER_BYTES)
    return path


def _run(*args) -> int:
    return release_manifest.main([str(arg) for arg in args])


def test_it_writes_the_manifest_beside_the_installer(tmp_path):
    installer = _installer(tmp_path)
    code = _run(
        installer, "--payload", _payload(tmp_path), "--channel", "stable", "--version", "1.0.0"
    )
    assert code == 0
    assert (tmp_path / "out" / "pam-release.json").read_text(
        encoding="utf-8"
    ) == EXPECTED_STABLE


def test_the_default_version_is_the_packages(tmp_path):
    installer = _installer(tmp_path)
    assert _run(installer, "--payload", _payload(tmp_path), "--channel", "stable") == 0
    assert '"version": "1.0.0"' in (tmp_path / "out" / "pam-release.json").read_text("utf-8")


def test_a_release_candidate(tmp_path):
    installer = _installer(tmp_path, "pam-setup-1.0.0rc1.exe")
    out = tmp_path / "rc.json"
    code = _run(
        installer,
        "--payload",
        _payload(tmp_path),
        "--channel",
        "rc",
        "--version",
        "1.0.0rc1",
        "--out",
        out,
    )
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert '"version": "1.0.0rc1"' in text
    assert '"channel": "rc"' in text
    assert '"installer": "pam-setup-1.0.0rc1.exe"' in text


@pytest.mark.parametrize(
    ("names", "words"),
    [
        (SHIPPED[:-1], "0008_files_rel_path_nocase.sql"),
        (SHIPPED + ("0009_future.sql",), "0009_future.sql"),
        ((), "0001_init.sql"),
    ],
    ids=["payload-misses-one", "payload-has-an-extra", "payload-has-none"],
)
def test_a_payload_whose_inventory_differs_is_refused(tmp_path, capsys, names, words):
    installer = _installer(tmp_path)
    code = _run(installer, "--payload", _payload(tmp_path, names), "--channel", "stable")
    assert code == 2
    assert words in capsys.readouterr().err
    assert not (tmp_path / "out" / "pam-release.json").exists()


def test_a_payload_without_a_migrations_folder_is_refused(tmp_path, capsys):
    installer = _installer(tmp_path)
    (tmp_path / "empty").mkdir()
    assert _run(installer, "--payload", tmp_path / "empty", "--channel", "stable") == 2
    assert "migrations" in capsys.readouterr().err
    assert not (tmp_path / "out" / "pam-release.json").exists()


def test_an_installer_named_for_another_version_is_refused(tmp_path, capsys):
    installer = _installer(tmp_path, "pam-setup-1.0.1.exe")
    assert _run(installer, "--payload", _payload(tmp_path), "--channel", "stable") == 2
    assert "pam-setup-1.0.0.exe" in capsys.readouterr().err
    assert not (tmp_path / "out" / "pam-release.json").exists()


def test_a_channel_that_disagrees_with_the_version_is_refused(tmp_path, capsys):
    installer = _installer(tmp_path)
    assert _run(installer, "--payload", _payload(tmp_path), "--channel", "rc") == 2
    assert "channel rc" in capsys.readouterr().err


def test_a_missing_installer_is_refused(tmp_path, capsys):
    missing = tmp_path / "out" / "pam-setup-1.0.0.exe"
    assert _run(missing, "--payload", _payload(tmp_path), "--channel", "stable") == 2
    assert "pam-setup-1.0.0.exe" in capsys.readouterr().err


def test_an_existing_manifest_is_never_overwritten(tmp_path, capsys):
    installer = _installer(tmp_path)
    existing = tmp_path / "out" / "pam-release.json"
    existing.write_text("keep me\n", encoding="utf-8")
    assert _run(installer, "--payload", _payload(tmp_path), "--channel", "stable") == 2
    assert existing.read_text(encoding="utf-8") == "keep me\n"
    assert "already exists" in capsys.readouterr().err


def test_the_source_inventory_is_this_pams(tmp_path):
    """The default source is the repository's own migrations folder: eight files today."""
    assert release_manifest.source_inventory() == SHIPPED
