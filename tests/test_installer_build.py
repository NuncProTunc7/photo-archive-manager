r"""`tools/installer/build.py`, the parts that run without ISCC or the payload (M15 Task 13B).

Every expectation is spelled from the brief and spike 10A, never read back from the module.
"""

import hashlib
import sys
import zipfile
from pathlib import Path

import pytest

from pam import __version__
from pam.catalog import ledger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "installer"))
import build  # noqa: E402

LOCK_TEXT = build.LOCK.read_text(encoding="utf-8")
# What ISCC prints before it reads a script (measured on 6.7.4, Task 13B-F).
ENGINE_LINE = "Inno Setup 6 Command-Line Compiler\r\nCompiler engine version: Inno Setup {}\r\n"


def test_the_target_is_read_from_the_lock_header():
    assert build.target(LOCK_TEXT) == ("3.14.10", "314")
    with pytest.raises(build.BuildError):
        build.target("# nothing here\nclick==8.5.1\n")


def test_the_lock_splits_into_its_two_sections_unchanged():
    base, component = build.split_lock(LOCK_TEXT)
    assert base + component == LOCK_TEXT
    names = lambda text: sorted(  # noqa: E731
        line.split("==")[0]
        for line in text.splitlines()
        if "==" in line and not line.startswith("#")
    )
    assert names(component) == ["flatbuffers", "onnxruntime", "packaging", "protobuf"]
    assert "onnxruntime" not in names(base) and "click" in names(base)
    with pytest.raises(build.BuildError):
        build.split_lock("click==8.5.1\n")


def test_the_embeddable_is_pinned_by_hash():
    assert build.EMBEDDABLE_SHA256 == {
        "3.14.10": "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159"
    }


def test_a_wrong_or_unpinned_embeddable_is_refused(tmp_path):
    fake = tmp_path / "python-3.14.10-embed-amd64.zip"
    with zipfile.ZipFile(fake, "w") as archive:
        archive.writestr("python314._pth", "python314.zip\n.\n")
    with pytest.raises(build.BuildError, match="does not match the pinned SHA-256"):
        build.stage_interpreter(fake, "3.14.10", "314", tmp_path / "python")
    with pytest.raises(build.BuildError, match="no pinned SHA-256"):
        build.stage_interpreter(fake, "3.14.9", "314", tmp_path / "python")
    assert not (tmp_path / "python").exists()


def test_the_launcher_runs_pam_entry_never_pam_cli():
    text = build.LAUNCHER
    assert "from pam.entry import main; sys.exit(main())" in text
    assert "pam.cli" not in text and "import cli" not in text
    assert '"%PAM_APP%python\\python.exe" -I -B -c ' in text
    assert 'set "PATH=%PAM_APP%exiftool;%PATH%"' in text
    assert text.endswith("exit /b %ERRORLEVEL%\r\n") and "\n" not in text.replace("\r\n", "")
    assert text.encode("ascii")


def test_the_pth_has_no_import_site():
    assert [e.format(tag="314") for e in build.PTH_ENTRIES] == [
        "python314.zip",
        ".",
        "Lib\\site-packages",
    ]


def test_scrub_removes_every_onnx_and_bin(tmp_path):
    site = tmp_path / "site"
    (site / "bin").mkdir(parents=True)
    (site / "bin" / "f2py.exe").write_bytes(b"x")
    (site / "onnxruntime" / "datasets").mkdir(parents=True)
    for name in ("mul_1.onnx", "sigmoid.ONNX", "keep.py"):
        (site / "onnxruntime" / "datasets" / name).write_bytes(b"x")
    build.scrub(site)
    assert sorted(p.relative_to(site).as_posix() for p in site.rglob("*")) == [
        "onnxruntime", "onnxruntime/datasets", "onnxruntime/datasets/keep.py",
    ]  # fmt: skip


def test_the_production_defines_come_from_pam_and_carry_no_test_key(tmp_path):
    values = build.defines(tmp_path / "p", tmp_path / "c", tmp_path / "o")
    assert set(values) == {
        "PamVersion", "HighestMigration", "ComponentLabel", "NeedsNewer", "PayloadDir", "ComponentDir", "OutputDir",
        "LongestPayloadPath",
    }  # fmt: skip
    assert values["LongestPayloadPath"] == "0"  # neither folder exists
    assert values["PamVersion"] == __version__
    assert values["ComponentLabel"] == "Face and text reading"
    assert values["NeedsNewer"] == ledger.NEEDS_NEWER
    assert ledger.MIGRATION_NAME.match(values["HighestMigration"])
    assert values["HighestMigration"] >= "0008_"


def test_a_test_define_must_be_a_PamTest_one(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(build.subprocess, "run", lambda args, **k: calls.append(args))
    with pytest.raises(build.BuildError):
        build.compile_installer(
            tmp_path / "ISCC.exe", {"PamVersion": "1"}, {"InstallRoot": "x"}
        )
    assert calls == []
    build.compile_installer(tmp_path / "ISCC.exe", {"PamVersion": "1"}, {"PamTestRoot": "r"})
    assert calls[0][1:-1] == ["/Q", "/DPamVersion=1", "/DPamTestRoot=r"]
    assert calls[0][-1] == str(build.SCRIPT)


def test_the_wheel_is_built_from_the_committed_tree_only_of_what_it_needs():
    assert build.WHEEL_SOURCES == ("pam", "pyproject.toml")


# --- Stage 1 review closures: the parts of the build no test ran ----------------------------------


def _fake_stage_inputs(tmp_path, monkeypatch):
    r"""`stage()` with its four external effects replaced: each pip install drops a toy `.onnx` and a
    `bin\` into its target, the way onnxruntime's wheel and a console script do."""
    installs = []

    def interpreter(embed_zip, version, tag, python):
        python.mkdir(parents=True)
        (python / "python.exe").write_bytes(b"MZ")

    def pip(requirements, wheels, dest, version, tag, hashes=True):
        installs.append(dest)
        (dest / "pkg" / "datasets").mkdir(parents=True, exist_ok=True)
        (dest / "pkg" / "datasets" / "toy.onnx").write_bytes(b"x")
        (dest / "bin").mkdir(exist_ok=True)
        (dest / "bin" / "tool.exe").write_bytes(b"x")

    monkeypatch.setattr(build, "stage_interpreter", interpreter)
    monkeypatch.setattr(build, "pip_install", pip)
    monkeypatch.setattr(build, "compile_bytecode", lambda python_exe, folder: None)
    exiftool = tmp_path / "exiftool-source"
    (exiftool / "exiftool_files").mkdir(parents=True)
    (exiftool / "exiftool.exe").write_bytes(b"MZ")
    (tmp_path / "pam.whl").write_bytes(b"x")
    args = build.argparse.Namespace(
        lock=str(build.LOCK), embed_zip="unused.zip", wheels=str(tmp_path),
        exiftool_dir=str(exiftool), pam_wheel=str(tmp_path / "pam.whl"), wheel_python=None,
    )  # fmt: skip
    return args, installs


def test_stage_scrubs_the_component_as_well_as_the_base(tmp_path, monkeypatch):
    """Ruling 9: the models are never bundled. onnxruntime, whose wheel carries the toy graphs, is
    in the COMPONENT folder, so a scrub of the base alone would ship them."""
    args, installs = _fake_stage_inputs(tmp_path, monkeypatch)
    payload, component = build.stage(args, tmp_path / "work")
    assert component in installs and payload / "python" / "Lib" / "site-packages" in installs
    for folder in (payload, component):
        assert not [p for p in folder.rglob("*") if p.suffix.lower() == ".onnx"], folder
        assert not [p for p in folder.rglob("bin") if p.is_dir()], folder
    assert (payload / "pam.cmd").read_bytes() == build.LAUNCHER.encode("ascii")


def test_stage_ships_the_licence_and_the_third_party_notices_in_the_payload(
    tmp_path, monkeypatch
):
    """Task 14's ruling: THIRD-PARTY-NOTICES.txt ships in the installer payload as well as in the
    export, beside pam's own LICENSE, byte for byte from the repository root (pam.iss copies the
    whole payload folder to {app})."""
    args, _ = _fake_stage_inputs(tmp_path, monkeypatch)
    payload, _ = build.stage(args, tmp_path / "work")
    for name in ("LICENSE", "THIRD-PARTY-NOTICES.txt"):
        assert (payload / name).read_bytes() == (build.REPO / name).read_bytes(), name
    notices = (payload / "THIRD-PARTY-NOTICES.txt").read_text(encoding="utf-8")
    for component in ("Python 3.14.10", "onnxruntime", "exiftool"):
        assert component in notices, component


# Task 15W: the licence texts no shipped wheel carries, each spelled here from the upstream file it
# was taken from (downloaded once with the maintainer's approval, 2026-11-21): name -> (first non-blank
# line, SHA-256 of the text with LF line endings - a Windows checkout may write CRLF).
LICENCE_TEXTS = {
    # x265-4.3/COPYING (github.com/Multicorewareinc/x265, tag 4.3)
    "GPL-2.0.txt": (
        "GNU GENERAL PUBLIC LICENSE",
        "d8afb1bcc7a2cfc603683b168d6987ef0a48e59e0da3693bf55c5d33b67e2b49",
    ),
    # libde265-1.1.3/libde265/COPYING (github.com/strukturag/libde265, tag v1.1.3)
    "LGPL-3.0.txt": (
        "GNU LESSER GENERAL PUBLIC LICENSE",
        "da7eabb7bafdf7d3ae5e9f223aa5bdc1eece45ac569dc21b3b037520b4464768",
    ),
    # LICENSE of github.com/thampiman/reverse-geocoder at a81b4095 (the repository has no tags)
    "LGPL-2.1.txt": (
        "GNU LESSER GENERAL PUBLIC LICENSE",
        "1d429c98b7beaf7d70d5444b119458eabf211487b29c5f70c66d0948c1de3491",
    ),
    # LICENSE of github.com/google/flatbuffers at tag v25.12.19
    "Apache-2.0.txt": (
        "Apache License",
        "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
    ),
}
assert len(LICENCE_TEXTS) == 4


def _lf_sha256(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def test_the_committed_licence_texts_are_the_upstream_files_unchanged():
    for name, (first_line, digest) in LICENCE_TEXTS.items():
        data = (build.LICENSES / name).read_bytes()
        assert _lf_sha256(data) == digest, name
        assert next(ln for ln in data.decode("utf-8").splitlines() if ln.strip()).strip() == (
            first_line
        ), name
    assert sorted(p.name for p in build.LICENSES.iterdir()) == sorted(LICENCE_TEXTS)


def test_stage_ships_every_licence_text_into_the_payload(tmp_path, monkeypatch):
    """Task 15W: THIRD-PARTY-NOTICES.txt names `licenses\\<text>` for each; the build puts them
    there, byte for byte."""
    args, _ = _fake_stage_inputs(tmp_path, monkeypatch)
    payload, _ = build.stage(args, tmp_path / "work")
    shipped = payload / "licenses"
    assert sorted(p.name for p in shipped.iterdir()) == sorted(LICENCE_TEXTS)
    for name, (_, digest) in LICENCE_TEXTS.items():
        assert _lf_sha256((shipped / name).read_bytes()) == digest, name
    notices = (payload / "THIRD-PARTY-NOTICES.txt").read_text(encoding="utf-8")
    for name in LICENCE_TEXTS:
        assert f"licenses\\{name}" in notices, name


def test_a_production_compile_passes_no_PamTest_define(tmp_path, monkeypatch):
    """Ruling 5's source-level guard, on the call the release makes: `main()` and a bare
    `compile_installer` put no `/DPamTest...` on ISCC's command line."""
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        out = ENGINE_LINE.format("6.7.4") if args[1:] == ["-"] else ""
        return build.subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(build.subprocess, "run", run)
    build.compile_installer(tmp_path / "ISCC.exe", {"PamVersion": "1"})
    assert calls[-1][1:-1] == ["/Q", "/DPamVersion=1"]
    payload, component, out = tmp_path / "p", tmp_path / "c", tmp_path / "out"
    monkeypatch.setattr(build, "stage", lambda args, work: (payload, component))
    out.mkdir()
    (out / f"pam-setup-{__version__}.exe").write_bytes(b"MZ")
    code = build.main(
        ["--embed-zip", "e.zip", "--wheels", "w", "--exiftool-dir", "x", "--out", str(out),
         "--pam-wheel", "pam.whl", "--iscc", str(tmp_path / "ISCC.exe")]
    )  # fmt: skip
    assert code == 0
    iscc_calls = [c for c in calls if c[0] == str(tmp_path / "ISCC.exe")]
    assert len(iscc_calls) == 3  # the direct compile, main's version check, main's compile
    assert iscc_calls[1][1:] == ["-"]
    defines = [a for a in iscc_calls[-1] if a.startswith("/D")]
    assert sorted(a.split("=", 1)[0] for a in defines) == [
        "/DComponentDir", "/DComponentLabel", "/DHighestMigration", "/DLongestPayloadPath",
        "/DNeedsNewer", "/DOutputDir", "/DPamVersion", "/DPayloadDir",
    ]  # fmt: skip


def test_scrub_refuses_when_a_model_survives_the_removal(tmp_path, monkeypatch):
    """Q3's proof is the backstop, not the loop: if a removal silently leaves a `.onnx` behind (a
    read-only file, a locked file), the build stops instead of shipping it."""
    site = tmp_path / "site"
    (site / "pkg").mkdir(parents=True)
    (site / "pkg" / "toy.onnx").write_bytes(b"x")
    monkeypatch.setattr(build.Path, "unlink", lambda self, missing_ok=False: None)
    with pytest.raises(build.BuildError, match="still holds"):
        build.scrub(site)


def test_the_ci_installer_job_requires_inno_and_runs_the_behaviour_module():
    """Ruling 7 / the M11 review's R3: the job sets PAM_INNO_REQUIRED=1 before it runs the `inno`
    marker over the behaviour module, so a runner without ISCC fails rather than skipping green."""
    job = (build.REPO / "tools" / "installer" / "ci_installer_job.ps1").read_text("utf-8")
    lines = [line.strip() for line in job.splitlines()]
    assert '$env:PAM_INNO_REQUIRED = "1"' in lines
    first_pytest = next(i for i, line in enumerate(lines) if "-m pytest" in line)
    assert lines.index('$env:PAM_INNO_REQUIRED = "1"') < first_pytest
    assert "-m inno" in lines[first_pytest]
    assert lines[first_pytest + 1] == "tests/test_installer_behaviour.py"


# --- Task 13B-F: the path budget the installer enforces, and the Inno version the build demands ----


def test_the_longest_path_counts_the_component_below_site_packages_and_a_folders_temp_file(
    tmp_path,
):
    r"""Spelled by hand: a payload file `a\bb.txt` (8), its folder `a` (1 + 18: Inno's
    `\is-XXXXXXXXXX.tmp`, measured), and a component file `x\y.py` installed as
    `python\Lib\site-packages\x\y.py` (31), its folder 26 + 18 = 44."""
    payload, component = tmp_path / "p", tmp_path / "c"
    (payload / "a").mkdir(parents=True)
    (payload / "a" / "bb.txt").write_text("x")
    assert build.longest_path(payload, component) == 19
    (component / "x").mkdir(parents=True)
    (component / "x" / "y.py").write_text("x")
    assert build.longest_path(payload, component) == 44
    (payload / "a" / ("n" * 60 + ".txt")).write_text("x")
    assert (
        build.longest_path(payload, component) == 66
    )  # a file longer than any folder's budget
    assert build.defines(payload, component, tmp_path / "o")["LongestPayloadPath"] == "66"


def _fake_iscc(tmp_path, reports: str | None):
    fake = tmp_path / "fake iscc" / "ISCC.cmd"
    fake.parent.mkdir()
    line = "" if reports is None else f"echo Compiler engine version: Inno Setup {reports}"
    fake.write_text(f"@echo off\r\necho Inno Setup 6 Command-Line Compiler\r\n{line}\r\n")
    return fake


def test_the_build_is_pinned_to_inno_setup_6_7_4():
    assert build.INNO_VERSION == "6.7.4"  # the maintainer's ruling, 2026-11-20


@pytest.mark.skipif(sys.platform != "win32", reason="a .cmd stands in for ISCC")
@pytest.mark.parametrize("reports", ["6.7.3", "7.1.1", "6.7.4.1", None])
def test_the_build_refuses_an_iscc_that_does_not_report_6_7_4(tmp_path, monkeypatch, reports):
    """Before anything is staged or compiled: the fake ISCC reports another version, or none."""
    staged = []
    monkeypatch.setattr(build, "stage", lambda args, work: staged.append(args))
    fake = _fake_iscc(tmp_path, reports)
    assert build.iscc_version(fake) == reports
    with pytest.raises(build.BuildError, match="Inno Setup 6.7.4 only"):
        build.check_iscc(fake)
    code = build.main(
        ["--embed-zip", "e.zip", "--wheels", "w", "--exiftool-dir", "x",
         "--out", str(tmp_path / "out"), "--pam-wheel", "pam.whl", "--iscc", str(fake)]
    )  # fmt: skip
    assert code == 1 and staged == []


@pytest.mark.skipif(sys.platform != "win32", reason="a .cmd stands in for ISCC")
def test_the_build_accepts_an_iscc_that_reports_6_7_4(tmp_path):
    fake = _fake_iscc(tmp_path, "6.7.4")
    assert build.iscc_version(fake) == "6.7.4"
    build.check_iscc(fake)  # no refusal


def test_an_iscc_that_cannot_run_is_refused(tmp_path):
    with pytest.raises(build.BuildError, match="cannot be run"):
        build.check_iscc(tmp_path / "no such" / "ISCC.exe")
