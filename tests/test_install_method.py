"""M15 Task 11 (E13): install provenance, and `pam doctor`'s advice following the install method.

THE PATH RULE, from spike 10A's measured layout (`docs/implementation-notes/m15-spikes/10a/
10a-record.md`, "Tree layout"; unchanged on 3.14 by the addendum): the installer puts the
embeddable interpreter at `{app}\\python\\`, so `sys.prefix` IS `{app}\\python` and the record
`{app}\\pam-install.json` is a child of `sys.prefix`'s PARENT. A prefix whose folder is not named
`python` is not that layout, so no record is looked for at all.

EVERY EXPECTED SENTENCE BELOW IS SPELLED HERE, never read back from `pam.doctor` or
`pam.install_method` - an oracle built from the constant it checks passes for any value of it
(`repo.ENRICHERS`). The pip sentences are the ones design 12 rev 35 (OD-12) and the HT documents
already quote; the installer sentences are E13's ("run the installer and tick ..."), worded for
the one optional component spike 10A's Q4 ruling made of `[faces]` and `[ocr]`.
"""

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

import pam.doctor as doctor_mod
from pam import __version__
from pam.cli import cli
from pam.install_method import INSTALLER, PIP, app_dir, provenance, record_state


# The pip route to an optional extra, spelled from the maintainer's decision 4 (2026-11-21), never read
# from `pam.install_method`: the release's own wheel for the running version, never a bare PyPI name.
def _pip(extra: str) -> str:
    v = __version__
    return (
        f'pip install "photo-archive-manager[{extra}] @ https://github.com/NuncProTunc7/'
        f'photo-archive-manager/releases/download/v{v}/photo_archive_manager-{v}-py3-none-any.whl"'
    )


# --- the sentences, spelled from the spec side ---------------------------------------------

PIP_FACES = f"Run: {_pip('faces')}."
PIP_OCR = f"Run: {_pip('ocr')}."
PIP_EXIFTOOL = (
    "not found on PATH. Download the Windows executable from exiftool.org, "
    "rename it to exiftool.exe, and put its folder on PATH."
)
REPAIR = "Run pam's installer again to repair this installation."
TICK = 'Run pam\'s installer again and tick "Face and text reading".'
COMPONENT = 'the optional "Face and text reading" component is not installed'

GOOD_RECORD = {"method": "installer", "version": "1.0.0", "components": ["base"]}


def _layout(tmp_path: Path, record: object | None = None, *, raw: bytes | None = None) -> Path:
    """An installed layout: `{app}\\python` (the prefix) and, optionally, `{app}\\pam-install.json`.
    Returns the PREFIX - what `sys.prefix` is for an installed pam."""
    app = tmp_path / "app"
    prefix = app / "python"
    prefix.mkdir(parents=True)
    # The embeddable interpreter's path file - the installed layout's own mark (spike 10A, F3).
    (prefix / "python314._pth").write_text("python314.zip\n.\nimport site\n", encoding="utf-8")
    if raw is not None:
        (app / "pam-install.json").write_bytes(raw)
    elif record is not None:
        (app / "pam-install.json").write_text(json.dumps(record), encoding="utf-8")
    return prefix


# --- the path rule ---------------------------------------------------------------------------


def test_app_is_the_parent_of_the_interpreter_folder(tmp_path):
    prefix = _layout(tmp_path)
    assert app_dir(str(prefix)) == tmp_path / "app"
    # Windows folder names are case-insensitive; the installer's casing is not a fact to rely on.
    assert app_dir(str(tmp_path / "app" / "Python")) == tmp_path / "app"


def test_a_prefix_that_is_not_the_installed_layout_has_no_app(tmp_path):
    """A venv (`.venv`), a system Python (`Python314`), the hosted runner's `x64`: no record is
    looked for, so a stray `pam-install.json` beside a venv cannot claim an installer."""
    for name in (".venv", "Python314", "x64", "pythonx"):
        assert app_dir(str(tmp_path / name)) is None, name


def test_a_stray_record_beside_a_non_installed_prefix_is_not_believed(tmp_path):
    venv = tmp_path / ".venv"
    venv.mkdir()
    (tmp_path / "pam-install.json").write_text(json.dumps(GOOD_RECORD), encoding="utf-8")
    assert provenance(str(venv)).method == PIP


def test_the_rule_reads_sys_prefix_when_no_prefix_is_given(tmp_path, monkeypatch):
    prefix = _layout(tmp_path, GOOD_RECORD)
    monkeypatch.setattr(sys, "prefix", str(prefix))
    assert provenance().method == INSTALLER
    assert provenance().app == tmp_path / "app"


# --- reading the record ----------------------------------------------------------------------


def test_a_well_formed_installer_record_reads_as_the_installer(tmp_path):
    found = provenance(str(_layout(tmp_path, GOOD_RECORD)))
    assert found.method == INSTALLER
    assert found.app == tmp_path / "app"


def test_a_newer_installers_extra_key_does_not_demote_it(tmp_path):
    record = dict(GOOD_RECORD, channel="stable")
    assert provenance(str(_layout(tmp_path, record))).method == INSTALLER


def test_no_record_reads_as_pip(tmp_path):
    found = provenance(str(_layout(tmp_path)))
    assert found.method == PIP
    assert found.app is None


# Written by hand, row by row, one operand of the shape each. NOT derived from the good record:
# a table built from the definition it checks degenerates with it.
MALFORMED = [
    ("garbage bytes", b"\x00\xff\xfe garbage"),
    ("not utf-8", '{"method": "installer\u00e9"}'.encode("latin-1")),
    ("empty file", b""),
    ("truncated json", b'{"method": "installer", "version": "1.0.0", '),
    ("a json array", b'["installer", "1.0.0", []]'),
    ("a json string", b'"installer"'),
    ("json null", b"null"),
    ("method missing", b'{"version": "1.0.0", "components": []}'),
    ("an unknown method", b'{"method": "msi", "version": "1.0.0", "components": []}'),
    ("method pip", b'{"method": "pip", "version": "1.0.0", "components": []}'),
    (
        "method in another case",
        b'{"method": "Installer", "version": "1.0.0", "components": []}',
    ),
    ("method not a string", b'{"method": 1, "version": "1.0.0", "components": []}'),
    ("version missing", b'{"method": "installer", "components": []}'),
    ("version a number", b'{"method": "installer", "version": 1, "components": []}'),
    ("components missing", b'{"method": "installer", "version": "1.0.0"}'),
    (
        "components a string",
        b'{"method": "installer", "version": "1.0.0", "components": "base"}',
    ),
    (
        "a component not a string",
        b'{"method": "installer", "version": "1.0.0", "components": [1]}',
    ),
    (
        # EVERY component must be a string: a non-empty list where only SOME are is where `all`
        # and `any` disagree (review M4208 - the row above cannot tell them apart).
        "a non-string component among strings",
        b'{"method": "installer", "version": "1.0.0", "components": ["base", 1]}',
    ),
    # Under the size cap (60 KB), so it is the PARSER's `RecursionError` this row reaches.
    ("nesting deeper than json can parse", b"[" * 30_000 + b"]" * 30_000),
    (
        # A good record followed by whitespace JSON allows: its first 64 KiB + 1 still parse, so
        # only the size check refuses it (a bounded read alone would not).
        "larger than any record",
        b'{"method": "installer", "version": "1.0.0", "components": []}' + b" " * (70 * 1024),
    ),
]
assert len(MALFORMED) >= 20, "the malformed table was cut; it protects nothing"


@pytest.mark.parametrize(("why", "raw"), MALFORMED, ids=[row[0] for row in MALFORMED])
def test_a_malformed_record_reads_as_pip_and_never_raises(tmp_path, why, raw):
    found = provenance(str(_layout(tmp_path, raw=raw)))
    assert found.method == PIP, why
    assert found.app is None, why


def test_the_oversize_row_is_a_good_record_but_for_its_size(tmp_path):
    """The size row is well-formed whole AND in its first 64 KiB + 1 bytes - so it is the size
    CHECK that refuses it, not its shape or a truncated read (a negative test must not pass for
    the wrong reason)."""
    raw = dict(MALFORMED)["larger than any record"]
    assert len(raw) > 64 * 1024 + 1
    good = {"method": "installer", "version": "1.0.0", "components": []}
    assert json.loads(raw) == good
    assert json.loads(raw[: 64 * 1024 + 1]) == good


def test_a_record_of_exactly_the_cap_is_read(tmp_path):
    """The cap is 64 KiB (spelled here, not read from the module), and a record of exactly that
    size is a record: only a LARGER file is refused unread."""
    head = b'{"method": "installer", "version": "1.0.0", "components": []'
    raw = head + b" " * (64 * 1024 - len(head) - 1) + b"}"
    assert len(raw) == 64 * 1024
    assert provenance(str(_layout(tmp_path, raw=raw))).method == INSTALLER


def test_an_oversize_record_is_never_read_whole(tmp_path, monkeypatch):
    """A file far larger than a record is not one, and is NOT READ WHOLE: every read of the record
    asks for at most 64 KiB + 1 bytes (spelled here). The size row's outcome alone cannot see this
    - an unbounded read refuses it too, by its length (review M4197) - so the reads are watched."""
    raw = dict(MALFORMED)["larger than any record"]
    prefix = _layout(tmp_path, raw=raw)
    asked: list[int] = []
    real_open = Path.open

    class Watched:
        def __init__(self, handle):
            self._handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._handle.__exit__(*exc)

        def read(self, size=-1):
            asked.append(size)
            return self._handle.read(size)

    def watched_open(self, *args, **kwargs):
        handle = real_open(self, *args, **kwargs)
        return Watched(handle) if self.name == "pam-install.json" else handle

    monkeypatch.setattr(Path, "open", watched_open)
    assert provenance(str(prefix)).method == PIP
    assert asked, "the record was never read through the watched handle"
    assert all(0 <= size <= 64 * 1024 + 1 for size in asked), asked


def test_an_unreadable_record_reads_as_pip(tmp_path):
    """A record that cannot be opened (here: a folder in its place, which Windows refuses to read
    with an `OSError`) is no evidence, so pip."""
    prefix = _layout(tmp_path)
    (tmp_path / "app" / "pam-install.json").mkdir()
    assert provenance(str(prefix)).method == PIP


# --- the three-way reading pam update needs (M15 Task 12) ---------------------------------------
#
# `provenance()` folds "no record" and "malformed" into PIP, which is right for doctor's advice and
# wrong for `pam update`: a missing record means "not ours, give the pip route"; a record that
# exists but cannot be believed means "reinstall" (the ruling's three-way provenance). The
# outcome words are spelled here by hand, not imported.


def test_record_state_reads_the_installer_with_its_app(tmp_path):
    found = record_state(str(_layout(tmp_path, GOOD_RECORD)))
    assert (found.method, found.app) == ("installer", tmp_path / "app")


def test_record_state_reads_no_record_when_the_layout_has_none(tmp_path):
    found = record_state(str(_layout(tmp_path)))
    assert (found.method, found.app) == ("no record", None)


def test_record_state_reads_no_record_outside_the_installed_layout(tmp_path):
    """A venv with a good record beside it: not the layout, so no record is looked for at all."""
    venv = tmp_path / ".venv"
    venv.mkdir()
    (tmp_path / "pam-install.json").write_text(json.dumps(GOOD_RECORD), encoding="utf-8")
    assert record_state(str(venv)).method == "no record"


@pytest.mark.parametrize(("why", "raw"), MALFORMED, ids=[row[0] for row in MALFORMED])
def test_record_state_reads_every_malformed_record_as_malformed(tmp_path, why, raw):
    found = record_state(str(_layout(tmp_path, raw=raw)))
    assert (found.method, found.app) == ("malformed", None), why


def test_record_state_reads_an_unreadable_record_as_malformed(tmp_path):
    """It exists and cannot be read: evidence of an installer pam cannot trust, not of pip."""
    prefix = _layout(tmp_path)
    (tmp_path / "app" / "pam-install.json").mkdir()
    assert record_state(str(prefix)).method == "malformed"


def test_provenance_still_folds_both_non_installer_states_into_pip(tmp_path):
    """Doctor is unchanged by the three-way split: no record and malformed both read as PIP."""
    assert provenance(str(_layout(tmp_path / "a"))).method == PIP
    assert provenance(str(_layout(tmp_path / "b", raw=b"null"))).method == PIP
    assert provenance(str(_layout(tmp_path / "c", GOOD_RECORD))).method == INSTALLER


# --- pam doctor ------------------------------------------------------------------------------


def _toolchain(monkeypatch, *, exiftool: bool, faces: bool, missing_dep: str | None = None):
    """Script the machine doctor sees: exiftool, the `[faces]` pair, one runtime dependency."""
    if exiftool:
        monkeypatch.setattr(doctor_mod.shutil, "which", lambda name: r"C:\tools\exiftool.exe")
        monkeypatch.setattr(doctor_mod, "_exiftool_version", lambda exe: "13.10")
    else:
        monkeypatch.setattr(doctor_mod.shutil, "which", lambda name: None)
    monkeypatch.setattr(doctor_mod, "_ollama_tags", lambda port, **kw: ["qwen2.5vl:latest"])
    real = doctor_mod.importlib.util.find_spec
    absent = set() if faces else {"onnxruntime"}
    if missing_dep:
        absent.add(missing_dep)

    def find_spec(name, *a, **k):
        if name in absent:
            return None
        return real("json") if name in ("onnxruntime", "numpy") else real(name, *a, **k)

    monkeypatch.setattr(doctor_mod.importlib.util, "find_spec", find_spec)


def _doctor(monkeypatch, prefix: Path):
    monkeypatch.setattr(sys, "prefix", str(prefix))
    return CliRunner().invoke(cli, ["doctor"])


def _line(output: str, label: str) -> str:
    lines = [ln for ln in output.splitlines() if f" {label}:" in ln]
    assert len(lines) == 1, output
    return lines[0]


def test_an_installed_pam_is_told_to_run_its_installer_for_every_repair(tmp_path, monkeypatch):
    """F7 (spike 10A): an installed pam's doctor advised pip and a download. Each line now
    points at the installer, and no line anywhere says `pip install`."""
    _toolchain(monkeypatch, exiftool=False, faces=False, missing_dep="pillow_heif")
    result = _doctor(monkeypatch, _layout(tmp_path, GOOD_RECORD))
    out = result.output
    bundled = tmp_path / "app" / "exiftool"
    assert _line(out, "exiftool").endswith(
        f"not found on PATH. pam's own copy belongs in {bundled}. {REPAIR}"
    ), out
    assert _line(out, "pillow-heif").endswith(
        f"not installed (reads HEIC, which Pillow alone cannot). {REPAIR}"
    ), out
    assert f"{COMPONENT} (onnxruntime missing) on " in _line(out, "Face models"), out
    assert f"is off by default. {TICK} [enrich.faces]" in _line(out, "Face models"), out
    assert _line(out, "OCR").startswith(
        f"  - OCR: {COMPONENT} (onnxruntime missing). {TICK} [enrich.ocr]"
    ), out
    assert "pip install" not in out, out
    assert "exiftool.org" not in out, out
    assert "[faces] extra" not in out and "[ocr] extra" not in out, out


def test_an_installed_pams_missing_geocoder_dataset_is_a_repair(tmp_path, monkeypatch):
    _toolchain(monkeypatch, exiftool=True, faces=True)
    monkeypatch.setattr(doctor_mod, "_geocoder_dataset", lambda spec: tmp_path / "absent.csv")
    out = _doctor(monkeypatch, _layout(tmp_path, GOOD_RECORD)).output
    line = _line(out, "reverse_geocoder")
    assert line.endswith(f"would DOWNLOAD it from the internet. {REPAIR}"), out
    assert "pip install" not in out, out


def test_a_pip_install_keeps_the_pip_advice(tmp_path, monkeypatch):
    """No record (every pip install, and this suite): OD-12's pip sentences, unchanged."""
    _toolchain(monkeypatch, exiftool=False, faces=False, missing_dep="pillow_heif")
    out = _doctor(monkeypatch, _layout(tmp_path)).output
    assert _line(out, "exiftool").endswith(PIP_EXIFTOOL), out
    assert _line(out, "pillow-heif").endswith("Run: pip install pillow-heif"), out
    assert PIP_FACES in _line(out, "Face models"), out
    assert PIP_OCR in _line(out, "OCR"), out
    assert "installer" not in out, out


@pytest.mark.parametrize(
    "why",
    [
        "garbage bytes",
        "truncated json",
        "a json array",
        "an unknown method",
        "components missing",
    ],
)
def test_a_malformed_record_gives_pip_advice_through_the_command(tmp_path, monkeypatch, why):
    _toolchain(monkeypatch, exiftool=False, faces=False)
    out = _doctor(monkeypatch, _layout(tmp_path, raw=dict(MALFORMED)[why])).output
    assert _line(out, "exiftool").endswith(PIP_EXIFTOOL), out
    assert PIP_FACES in out and PIP_OCR in out, out
    assert "installer" not in out, out


def test_an_unreadable_record_gives_pip_advice_through_the_command(tmp_path, monkeypatch):
    _toolchain(monkeypatch, exiftool=False, faces=False)
    prefix = _layout(tmp_path)
    (tmp_path / "app" / "pam-install.json").mkdir()
    result = _doctor(monkeypatch, prefix)
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        result.exception
    )
    assert PIP_FACES in result.output, result.output


@pytest.mark.parametrize("exiftool", [True, False], ids=["healthy", "exiftool-missing"])
def test_the_provenance_never_moves_the_exit_code_or_a_mark(tmp_path, monkeypatch, exiftool):
    """Only the advice follows the method. Each mark (`ok`/`x`/`-`) and the exit code are the
    same with no record, a good record and a garbage one."""
    _toolchain(monkeypatch, exiftool=exiftool, faces=False)
    seen = []
    for case, record, raw in (
        ("none", None, None),
        ("installer", GOOD_RECORD, None),
        ("garbage", None, b"\x00garbage"),
    ):
        result = _doctor(monkeypatch, _layout(tmp_path / case, record, raw=raw))
        assert result.exception is None or isinstance(result.exception, SystemExit), case
        marks = [ln.split(":")[0] for ln in result.output.splitlines() if ln.startswith("  ")]
        seen.append((result.exit_code, marks))
    assert seen[0] == seen[1] == seen[2], seen
    assert seen[0][0] == (0 if exiftool else 1)


# --- F3 (M15 Task 11 review): the folder's NAME is not the installed layout ------------------


def test_a_venv_named_python_with_a_stray_record_is_not_an_installer(tmp_path):
    """A venv at `<dir>/python` (it has a venv marker) beside a well-formed record: not installed,
    even if a `._pth` were copied in."""
    prefix = _layout(tmp_path, GOOD_RECORD)
    (prefix / "pyvenv.cfg").write_text("home = C:\\Python314\n", encoding="utf-8")
    assert app_dir(str(prefix)) is None
    assert provenance(str(prefix)).method == "pip"


def test_a_system_python_in_a_folder_named_python_is_not_an_installer(tmp_path):
    """A full Python install has no `python3*._pth`; only the embeddable package does."""
    app = tmp_path / "app"
    prefix = app / "python"
    prefix.mkdir(parents=True)
    (app / "pam-install.json").write_text(json.dumps(GOOD_RECORD), encoding="utf-8")
    assert app_dir(str(prefix)) is None
    assert provenance(str(prefix)).method == "pip"


def test_the_embeddable_layout_with_its_pth_and_no_venv_marker_is_an_installer(tmp_path):
    prefix = _layout(tmp_path, GOOD_RECORD)
    assert app_dir(str(prefix)) == tmp_path / "app"
    assert provenance(str(prefix)).method == "installer"


# --- decision 4 (2026-11-21): a pip install's extra comes from the release's own wheel ---------


def test_the_extra_route_is_the_running_versions_release_wheel():
    from pam import install_method

    assert install_method.pip_extra("faces") == _pip("faces")
    assert install_method.pip_extra("ocr") == _pip("ocr")
    assert install_method.pip_extra("faces", "1.0.0rc1") == (
        'pip install "photo-archive-manager[faces] @ https://github.com/NuncProTunc7/'
        "photo-archive-manager/releases/download/v1.0.0rc1/"
        'photo_archive_manager-1.0.0rc1-py3-none-any.whl"'
    )


def test_no_pam_sentence_names_an_extra_by_a_bare_pypi_name():
    """pam is not on PyPI: `"photo-archive-manager[faces]"` would install whatever owns that name."""
    import re

    bare = re.compile(r"photo-archive-manager\[\w+\]\"")
    assert bare.search('Run: pip install "photo-archive-manager[faces]".')  # the control
    package = Path(__file__).resolve().parent.parent / "pam"
    sources = sorted(package.rglob("*.py"))
    assert len(sources) > 100  # the walk reached the package
    hits = [
        f"{path.relative_to(package)}:{number}"
        for path in sources
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if bare.search(line)
    ]
    assert hits == []


def test_the_update_route_and_the_extra_route_name_the_same_wheel():
    from pam import install_method
    from pam.update.release import download_url, wheel_name

    assert download_url("v1.2.3", wheel_name("1.2.3")) == install_method.wheel_url("1.2.3")
