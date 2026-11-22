"""M13 Task 3 (`m12.md` C4, amendment S8): `--refresh-older-than-model` with `faces` or `ocr` is
refused BEFORE ANY CONFIGURATION IS READ, so it exits 1 whatever `pam.toml` says.

THE DEFECT, measured at the M12 review: the refusal sat in the runner (`base.run_enricher`), which
is only reached by an enricher that is enabled, scoped and has its pack - so
`pam enrich ocr --refresh-older-than-model X` EXITED 0 when ocr was disabled or `folders = []`.
A flag combination that can never be valid is a usage error, and a script reading the exit code
must not get 0.

EVERY STATE BELOW IS BUILT SO THAT ONLY ONE OPERAND OF THE OLD SHORT-CIRCUIT HOLDS (CLAUDE.md: "a
mutation can delete an operand; it cannot add one that was never written"): the enricher
(a) enabled + configured + available, (b) `enabled = false`, (c) `ocr` `folders = []`, (d) its
pack missing. (a) is the only state today's runner refused; (b)-(d) each stopped the old path at a
different place, before the runner.

THE SENTENCES ARE SPELLED HERE AS LITERALS, copied from `sink.refresh_refusal` /
`sink.digest_refusal` as they read at M12's close - never read from the production symbol.

"NO CONFIGURATION IS READ" IS WATCHED AS A CALL (M697): a spy on the command's `load_config`
asserts it was never called, beside a control proving the same spy DOES record a call when the
flag is absent - a spy that records nothing would otherwise make every "never called" pass.
And an unparseable `pam.toml` is the outcome-side proof: the refusal prints, not a config error.

THE NO-NAME PATH IS PINNED AS IT IS (the plan's scope: a NAMED enricher only). Recorded in
`docs/implementation-notes/m13-progress.md` before this change; pinned exactly here.
"""

import json
from pathlib import Path

import pytest
from archives import FAKE_COMMAND

from pam.cli import main
from pam.enrich import command as enrich_command
from pam.enrich.faces import FacesEnricher
from pam.enrich.ocr import OcrEnricher
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan

PICTURE = Path(__file__).resolve().parent / "tiny_library" / "phash_pattern_base.png"

# `sink.refresh_refusal`'s sentence for `faces` (A9), spelled by hand.
FACES_REFUSAL = (
    "--refresh-older-than-model cannot be used with faces: its detections live in"
    " face_instances, which has no superseded_by, so a refresh would leave two model"
    " generations side by side. Run: pam faces export, then pam faces purge, then re-run"
    " this command."
)
# `sink.digest_refusal`'s sentence for `ocr` (P13 / D7), spelled by hand.
OCR_REFUSAL = (
    "--refresh-older-than-model cannot be used with ocr: its model version is a digest,"
    " which has no order, so no reading is older than another. A new ocr engine is used"
    " automatically - just run pam enrich ocr. Going back to an earlier engine's readings"
    " is not something pam v1.0 can do."
)
REFUSALS = {"faces": FACES_REFUSAL, "ocr": OCR_REFUSAL}

# The states, by hand. `{pack}` is a real, empty folder. Each config is a whole `pam.toml`.
FACES_STATES = {
    "a_configured": "[enrich.faces]\nenabled = true\nmodel_dir = '{pack}'\n",
    "b_disabled": "[enrich.faces]\nenabled = false\nmodel_dir = '{pack}'\n",
    "d_pack_missing": "[enrich.faces]\nenabled = true\n",
}
OCR_STATES = {
    "a_configured": "[enrich.ocr]\nenabled = true\nfolders = [\"Scans\"]\nmodel_dir = '{pack}'\n",
    "b_disabled": "[enrich.ocr]\nenabled = false\nfolders = [\"Scans\"]\nmodel_dir = '{pack}'\n",
    "c_no_folders": "[enrich.ocr]\nenabled = true\nfolders = []\nmodel_dir = '{pack}'\n",
    "d_pack_missing": '[enrich.ocr]\nenabled = true\nfolders = ["Scans"]\n',
}
CASES = [("faces", state, body) for state, body in FACES_STATES.items()] + [
    ("ocr", state, body) for state, body in OCR_STATES.items()
]
UNPARSEABLE = "[enrich.faces\nenabled = = true\n"


def test_the_state_tables_are_whole_at_collection():
    """A table emptied by a mutation must fail, never skip (pytest SKIPS an empty parametrize)."""
    assert sorted(FACES_STATES) == ["a_configured", "b_disabled", "d_pack_missing"]
    assert sorted(OCR_STATES) == [
        "a_configured",
        "b_disabled",
        "c_no_folders",
        "d_pack_missing",
    ]
    assert len(CASES) == 7


def build(tmp_path, config):
    """A scanned archive with one decodable picture under `Scans/`, and `config` as pam.toml."""
    root = tmp_path / "photos"
    (root / "Scans").mkdir(parents=True)
    path = root / "Scans" / "picture.png"
    path.write_bytes(PICTURE.read_bytes())
    sidecar = path.with_name(path.name + ".tags.json")
    sidecar.write_text(json.dumps({"PamTestSidecar": path.name}), encoding="utf-8")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    pack = tmp_path / "pack"
    pack.mkdir()
    if config is not None:
        workspace_if_exists(root).config_path.write_text(
            config.replace("{pack}", str(pack)), encoding="utf-8"
        )
    return root


@pytest.fixture
def config_spy(monkeypatch):
    """Every call the command makes to `load_config`, recorded, then delegated."""
    calls = []
    real = enrich_command.load_config

    def spy(path):
        calls.append(path)
        return real(path)

    monkeypatch.setattr(enrich_command, "load_config", spy)
    return calls


@pytest.fixture
def available(monkeypatch):
    """State (a): both enrichers report themselves ready, so ONLY the refresh operand is left.

    Without this, a venv without the extras fails `available()` and (a) collapses into (d).
    """
    monkeypatch.setattr(FacesEnricher, "available", lambda self: (True, ""))
    monkeypatch.setattr(OcrEnricher, "available", lambda self: (True, ""))


def enrich(capsys, *args):
    capsys.readouterr()  # drop the scan's own output, so `out` is this command's alone
    code = main(["enrich", *args])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize(
    ("name", "state", "config"), CASES, ids=[f"{n}-{s}" for n, s, _ in CASES]
)
def test_the_refresh_flag_is_refused_in_every_state_before_any_config_is_read(
    tmp_path, capsys, config_spy, available, name, state, config
):
    root = build(tmp_path, config)

    code, out, err = enrich(
        capsys, name, "--refresh-older-than-model", "1", "--archive", str(root)
    )

    assert code == 1
    assert f"x {REFUSALS[name]}" in err.splitlines()
    assert config_spy == []
    # Nothing ran and no closing block printed: the refusal came before every enricher.
    assert out == ""


@pytest.mark.parametrize("name", ["faces", "ocr"])
def test_an_unparseable_pam_toml_still_gets_the_refusal_and_not_a_config_error(
    tmp_path, capsys, config_spy, name
):
    root = build(tmp_path, UNPARSEABLE)

    code, out, err = enrich(
        capsys, name, "--refresh-older-than-model", "1", "--archive", str(root)
    )

    assert code == 1
    assert err.splitlines() == [f"x {REFUSALS[name]}"]
    assert config_spy == []
    assert out == ""


def test_the_unparseable_pam_toml_really_is_a_config_error_without_the_flag(
    tmp_path, capsys, config_spy
):
    """The control for the case above: the same file IS refused as configuration when nothing
    refuses first - so the case above proves the refusal won, not that the file was harmless."""
    root = build(tmp_path, UNPARSEABLE)

    code, _out, err = enrich(capsys, "faces", "--archive", str(root))

    assert code == 1
    assert len(config_spy) == 1
    assert "pam.toml" in err
    assert FACES_REFUSAL not in err


def test_the_config_spy_records_a_call_when_the_flag_is_absent(tmp_path, capsys, config_spy):
    """The instrument's own control (mutation 4): a spy that recorded nothing would make every
    `config_spy == []` above pass for the wrong reason."""
    root = build(tmp_path, None)

    code, _out, _err = enrich(capsys, "phash", "--archive", str(root))

    assert code == 0
    assert len(config_spy) == 1


def test_another_enricher_with_the_flag_still_reads_its_config(tmp_path, capsys, config_spy):
    """Keyed on the enricher: `phash` with the flag is a refresh, not a refusal."""
    root = build(tmp_path, None)

    code, _out, err = enrich(
        capsys, "phash", "--refresh-older-than-model", "1", "--archive", str(root)
    )

    assert code == 0
    assert len(config_spy) == 1
    assert "cannot be used with" not in err


# ----------------------------------------------------------------- the no-name path, as it is


def test_no_name_with_default_config_refreshes_the_rest_and_reports_faces_and_ocr_disabled(
    tmp_path, capsys
):
    root = build(tmp_path, None)

    code, out, err = enrich(capsys, "--refresh-older-than-model", "1", "--archive", str(root))

    assert code == 0
    lines = out.splitlines()
    assert "phash: 1 of 2 files read, 1 results stored." in lines
    assert "geocode: 0 of 2 files read, 0 results stored." in lines
    for name in ("caption", "faces", "ocr"):
        assert f"{name}: not run - [enrich.{name}] enabled is false in pam.toml." in lines
    assert "cannot be used with" not in out + err


def test_no_name_with_faces_ready_stops_the_whole_command_at_faces_with_exit_one(
    tmp_path, capsys, available
):
    root = build(tmp_path, FACES_STATES["a_configured"] + OCR_STATES["a_configured"])

    code, out, err = enrich(capsys, "--refresh-older-than-model", "1", "--archive", str(root))

    assert code == 1
    assert f"x {FACES_REFUSAL}" in err.splitlines()
    assert OCR_REFUSAL not in err
    lines = out.splitlines()
    # phash and geocode refreshed first; faces was reached and refused by the runner.
    for name in ("phash", "geocode", "faces"):
        assert f"Enriching with {name} ..." in lines
    assert "Enriching with ocr ..." not in lines
    # The closing block never prints: the refusal is raised mid-command, as it is today.
    assert not any(line.startswith("Enrichment in ") for line in lines)


def test_no_name_with_ocr_ready_stops_at_ocr_with_exit_one(tmp_path, capsys, available):
    root = build(tmp_path, OCR_STATES["a_configured"])

    code, out, err = enrich(capsys, "--refresh-older-than-model", "1", "--archive", str(root))

    assert code == 1
    assert f"x {OCR_REFUSAL}" in err.splitlines()
    lines = out.splitlines()
    assert "Enriching with ocr ..." in lines
    assert not any(line.startswith("Enrichment in ") for line in lines)


def test_no_name_with_ocr_enabled_and_no_folders_exits_zero_and_reports_no_refusal(
    tmp_path, capsys
):
    root = build(tmp_path, OCR_STATES["c_no_folders"])

    code, out, err = enrich(capsys, "--refresh-older-than-model", "1", "--archive", str(root))

    assert code == 0
    assert "ocr: not run - [enrich.ocr] folders is empty in pam.toml." in out.splitlines()
    assert "cannot be used with" not in out + err
