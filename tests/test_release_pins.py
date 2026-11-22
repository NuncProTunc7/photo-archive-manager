"""`tools/release_pins.py` - the release workflow's refusals before it downloads anything (Task 14)."""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import release_pins  # noqa: E402
from release_pins import Refused  # noqa: E402

GOOD = {
    "EXIFTOOL_SHA256": "a" * 64,
    "INNO_SHA256": "a549929319f9120716677c685535ca4671e4b937b5fdc87e2585f32d8eb30e8c",
    "EXIFTOOL_URL": "https://example.org/exiftool.zip",
    "INNO_URL": "https://example.org/innosetup.exe",
    "LIBHEIF_SRC_SHA256": "b" * 64,
    "LIBHEIF_SRC_URL": "https://example.org/libheif.tar.gz",
    "LIBDE265_SRC_SHA256": "c" * 64,
    "LIBDE265_SRC_URL": "https://example.org/libde265.tar.gz",
    "X265_SRC_SHA256": "d" * 64,
    "X265_SRC_URL": "https://example.org/x265.tar.gz",
    "PILLOW_HEIF_SRC_SHA256": "e" * 64,
    "PILLOW_HEIF_SRC_URL": "https://example.org/pillow_heif.tar.gz",
}
# Every pin the check must refuse when absent, spelled from the workflow's measured pins (Task 14,
# Task 15W), never read back from `release_pins`.
EVERY_PIN = sorted(GOOD)
assert len(EVERY_PIN) == 12


def test_complete_pins_pass():
    release_pins.check_pins(GOOD)


def test_a_placeholder_sha256_is_refused_by_name():
    env = {
        **GOOD,
        "EXIFTOOL_SHA256": "PLACEHOLDER-measure-the-official-zip-before-the-first-release",
    }
    with pytest.raises(Refused, match="EXIFTOOL_SHA256 is still a placeholder"):
        release_pins.check_pins(env)


@pytest.mark.parametrize("value", ["", "A" * 64, "a" * 63, "a" * 65, "g" * 64])
def test_a_sha256_that_is_not_64_lowercase_hex_is_refused(value):
    with pytest.raises(Refused, match="INNO_SHA256 is not a SHA-256"):
        release_pins.check_pins({**GOOD, "INNO_SHA256": value})


@pytest.mark.parametrize("name", ["EXIFTOOL_URL", "INNO_URL"])
def test_a_url_that_is_not_https_is_refused(name):
    with pytest.raises(Refused, match=f"{name} must be an https:// URL"):
        release_pins.check_pins({**GOOD, name: "http://example.org/x"})


@pytest.mark.parametrize("name", EVERY_PIN)
def test_a_missing_pin_is_refused(name):
    """Task 15W: a source archive whose pin is dropped from the workflow is refused like the rest."""
    env = dict(GOOD)
    del env[name]
    with pytest.raises(Refused, match=name):
        release_pins.check_pins(env)


@pytest.mark.parametrize(
    "tag, version, channel",
    [
        ("v1.0.0", "1.0.0", "stable"),
        ("v1.0.0rc1", "1.0.0rc1", "rc"),
        ("v1.2.3rc12", "1.2.3rc12", "rc"),
    ],
)
def test_the_channel_follows_the_version_grammar(tag, version, channel):
    assert release_pins.channel_for(tag, version) == channel


def test_a_tag_that_is_not_this_version_is_refused():
    with pytest.raises(Refused, match="the tag v1.0.1 is not v1.0.0"):
        release_pins.channel_for("v1.0.1", "1.0.0")


def test_the_cli_accepts_the_committed_workflows_own_pins(monkeypatch, capsys):
    """The pins exactly as `release.yml` sets them pass the check the build step runs first."""
    import yaml

    overlay = REPO / "public/.github/workflows/release.yml"  # the public checkout: at its place
    path = overlay if overlay.is_file() else REPO / ".github/workflows/release.yml"
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    for name, value in workflow["env"].items():
        monkeypatch.setenv(name, str(value))
    assert release_pins.main(["check"]) == 0, capsys.readouterr().err


LOCK = """# header
click==8.5.1 \\
    --hash=sha256:aaa
reverse-geocoder==1.5.1 \\
    --hash=sha256:sdist \\
    --hash=sha256:wheel
    # via photo-archive-manager
scipy==1.18.1 \\
    --hash=sha256:bbb
"""


def test_split_lock_takes_out_the_sdist_only_package_and_keeps_the_rest():
    kept, line = release_pins.split_lock(LOCK)
    assert "reverse-geocoder" not in kept
    assert "click==8.5.1" in kept and "scipy==1.18.1" in kept and "--hash=sha256:bbb" in kept
    assert line == "reverse-geocoder==1.5.1 --hash=sha256:sdist --hash=sha256:wheel"


def test_split_lock_refuses_a_lock_without_the_package():
    with pytest.raises(Refused, match="no reverse-geocoder requirement"):
        release_pins.split_lock("click==8.5.1 \\\n    --hash=sha256:aaa\n")


def test_split_lock_on_the_shipped_lock_keeps_every_other_package(tmp_path):
    lock = REPO / "tools/installer/requirements-314.lock"
    out = tmp_path / "wheels.txt"
    assert release_pins.main(["split-lock", str(lock), str(out)]) == 0
    kept = out.read_text(encoding="utf-8")
    original = lock.read_text(encoding="utf-8")
    names = {
        ln.split("==")[0] for ln in original.splitlines() if "==" in ln and ln[0].isalpha()
    }
    assert {"click", "pillow", "numpy", "onnxruntime"} <= names
    for name in names - {"reverse-geocoder"}:
        assert f"\n{name}==" in "\n" + kept, name
    assert not [ln for ln in kept.splitlines() if ln.startswith("reverse-geocoder==")]
