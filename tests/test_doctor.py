import copy
import importlib
import importlib.util
import subprocess
import sys

import pytest
from click.testing import CliRunner

import pam.doctor as doctor_mod
from pam import __version__
from pam.cli import cli
from pam.doctor import Check, model_is_installed, pull_message, run_checks
from pam.errors import PamNetworkError
from pam.install_method import PIP, Provenance


# The pip route to an optional extra, spelled from the maintainer's decision 4 (2026-11-21), never read
# from `pam.install_method`: the release's own wheel for the running version, never a bare PyPI name.
def _pip(extra: str) -> str:
    v = __version__
    return (
        f'pip install "photo-archive-manager[{extra}] @ https://github.com/NuncProTunc7/'
        f'photo-archive-manager/releases/download/v{v}/photo_archive_manager-{v}-py3-none-any.whl"'
    )


# --- M10 Task 7: the Ollama fakes -----------------------------------------------------
#
# EVERY TEST BELOW THAT REACHES `run_checks` SCRIPTS ONE OF THESE. The real Ollama is
# installed on the developer's machine, so the real answer to every Ollama check here is
# "everything is fine" - which is the answer that would hide a defect and then fail in
# CI, where Ollama will never exist. `tests/conftest.py`'s `_no_test_talks_to_the_real_
# ollama` makes forgetting a loud failure rather than a silent pass.
#
# SPELLED FROM SPEC 11 BY HAND, never read back from `pam.doctor`. `DEFAULT_OLLAMA_PORT`
# and `DEFAULT_CAPTION_MODEL` are derived from `Config`'s defaults in production; a test
# that compared against those symbols would pass for any value of them, which is
# `repo.ENRICHERS` exactly.
SPEC_11_OLLAMA_PORT = 11434
SPEC_11_CAPTION_MODEL = "qwen2.5vl"

# M15 Task 11 (E13): the checks driven directly take the install provenance; these are a pip install.
PIP_INSTALL = Provenance(PIP, None)


def _tags(monkeypatch, *names):
    """Script `GET /api/tags` to list exactly `names`, and record the port it was asked."""
    asked = []

    def fake(port, **kwargs):
        asked.append(port)
        return list(names)

    monkeypatch.setattr(doctor_mod, "_ollama_tags", fake)
    return asked


def _ollama_fails(monkeypatch, exc):
    def fake(port, **kwargs):
        raise exc

    monkeypatch.setattr(doctor_mod, "_ollama_tags", fake)


def _faces_extra_present(monkeypatch):
    """Answer `find_spec` for the two optional packages as if they were installed.

    M11 TASK 7 ADDED THIS AND THE REASON IS THE SAME ONE `_tags` EXISTS FOR. `test_all_good`
    below describes a machine on which EVERYTHING is present, and the `[faces]` extra is
    not installed on the machine that runs this suite, nor on the push lane - so without a
    stub the test would be asserting "this developer happens to have onnxruntime", which is
    a fact about the environment and not about `run_checks`. Ollama was stubbed for exactly
    this in M10; the extra is stubbed here for exactly this. The tests that matter for the
    absent case are the ones that stub it ABSENT and check the mark is `-` and the exit
    code is 0.
    """
    real = doctor_mod.importlib.util.find_spec
    monkeypatch.setattr(
        doctor_mod.importlib.util,
        "find_spec",
        lambda name, *a, **k: (
            real("json") if name in ("onnxruntime", "numpy") else real(name, *a, **k)
        ),
    )


def test_all_good(monkeypatch):
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda name: r"C:\tools\exiftool.exe")
    monkeypatch.setattr(doctor_mod, "_exiftool_version", lambda exe: "13.10")
    _tags(monkeypatch, f"{SPEC_11_CAPTION_MODEL}:latest")
    _faces_extra_present(monkeypatch)
    checks = run_checks()
    assert all(c.ok for c in checks)
    names = [c.name for c in checks]
    assert "Python" in names and "exiftool" in names and "App data" in names


def test_missing_exiftool_fails_with_guidance(monkeypatch):
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda name: None)
    _tags(monkeypatch, f"{SPEC_11_CAPTION_MODEL}:latest")
    checks = run_checks()
    exif = next(c for c in checks if c.name == "exiftool")
    assert exif.ok is False
    assert "exiftool.org" in exif.detail


def test_cli_exit_codes(monkeypatch):
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda name: r"C:\tools\exiftool.exe")
    monkeypatch.setattr(doctor_mod, "_exiftool_version", lambda exe: "13.10")
    _tags(monkeypatch, f"{SPEC_11_CAPTION_MODEL}:latest")
    result = CliRunner().invoke(cli, ["doctor"])
    assert result.exit_code == 0
    assert "ok" in result.output

    monkeypatch.setattr(doctor_mod.shutil, "which", lambda name: None)
    result = CliRunner().invoke(cli, ["doctor"])
    assert result.exit_code == 1
    assert "Next:" in result.output


def test_check_dataclass():
    c = Check(name="x", ok=True, detail="fine")
    assert (c.name, c.ok, c.detail) == ("x", True, "fine")


def test_main_entrypoint_real_code_path(monkeypatch):
    # Exercises pam.cli.main() itself - the installed executable's actual path -
    # not just CliRunner's invoke path.
    from pam.cli import main
    from pam.errors import EXIT_EXPECTED, EXIT_OK

    monkeypatch.setattr(doctor_mod.shutil, "which", lambda name: r"C:\tools\exiftool.exe")
    monkeypatch.setattr(doctor_mod, "_exiftool_version", lambda exe: "13.10")
    _tags(monkeypatch, f"{SPEC_11_CAPTION_MODEL}:latest")
    assert main(["doctor"]) == EXIT_OK
    assert main(["--version"]) == EXIT_OK

    monkeypatch.setattr(doctor_mod.shutil, "which", lambda name: None)
    assert main(["doctor"]) == EXIT_EXPECTED


# --- M9 Task 6: the four runtime dependencies -----------------------------------------
#
# The exiftool precedent, applied to the dependencies M9 spends the closure on: a missing
# `pillow-heif` must be one legible line here, not an `ImportError` raised deep inside an
# enrichment run over somebody's irreplaceable archive.
#
# THE EXPECTED NAMES ARE WRITTEN OUT, not read from `doctor._RUNTIME_DEPENDENCIES`. A
# test that iterates the production tuple and asserts each member appears passes for ANY
# value of that tuple - three of the four could be deleted and it would stay green. That
# is `repo.ENRICHERS` exactly (four of five enrichers deletable from `pam status` with
# 1238 tests green), and the fix is the same one: spell the expectation out from the
# spec's stack table and keep an independent second list.
SPEC_RUNTIME_DEPENDENCIES = ["Pillow", "pillow-heif", "imagehash", "reverse_geocoder"]

# Spelled here rather than read from `doctor._GEOCODER_DATASET`, same reason as above:
# `reverse_geocoder`'s own `RG_FILE`. A wrong filename in doctor is a red line on every
# correctly installed machine.
_GEOCODER_CSV = "rg_cities1000.csv"


def _ok(monkeypatch):
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda name: r"C:\tools\exiftool.exe")
    monkeypatch.setattr(doctor_mod, "_exiftool_version", lambda exe: "13.10")
    _tags(monkeypatch, f"{SPEC_11_CAPTION_MODEL}:latest")


def test_doctor_reports_every_runtime_dependency_design_1_names(monkeypatch):
    _ok(monkeypatch)
    names = [c.name for c in run_checks()]
    assert [n for n in names if n in SPEC_RUNTIME_DEPENDENCIES] == SPEC_RUNTIME_DEPENDENCIES


def test_a_missing_dependency_is_a_red_line_with_the_command_that_fixes_it(monkeypatch):
    """UX 2: a refusal that does not say what to do next is a dead end.

    `find_spec` is stubbed for ONE name only, so the test proves the loop reports the
    absent one and keeps reporting the present ones - a blanket failure would also
    satisfy a check that only looked at `ok is False`.
    """
    _ok(monkeypatch)
    real = doctor_mod.importlib.util.find_spec
    monkeypatch.setattr(
        doctor_mod.importlib.util,
        "find_spec",
        lambda name: None if name == "pillow_heif" else real(name),
    )
    checks = {c.name: c for c in run_checks()}
    assert checks["pillow-heif"].ok is False
    assert "pip install pillow-heif" in checks["pillow-heif"].detail
    assert checks["Pillow"].ok is True and checks["imagehash"].ok is True


def test_a_missing_dependency_makes_pam_doctor_exit_1(monkeypatch):
    """The line has to be worth something: an `x` that still exits 0 is decoration."""
    from pam.cli import main
    from pam.errors import EXIT_EXPECTED

    _ok(monkeypatch)
    monkeypatch.setattr(doctor_mod.importlib.util, "find_spec", lambda name: None)
    assert main(["doctor"]) == EXIT_EXPECTED


def test_the_geocoders_missing_dataset_is_a_failure_even_though_the_package_is_there():
    """S4's structural half, at the user's machine rather than at ours.

    `reverse_geocoder` with its CSV deleted still imports, still answers, and reaches
    `download.geonames.org` over plain HTTP to do it. So "installed" is the wrong
    question and `pam doctor` must not ask it: the package present and the dataset absent
    is the one configuration in which pam dials out.

    Driven through `_dependency_checks` with a spec whose search location is an empty
    directory, which is the same shape a `--no-binary` or truncated install produces.
    """
    import importlib.util

    spec = importlib.util.find_spec("reverse_geocoder")
    assert spec is not None, "reverse_geocoder must be installed - it is a runtime dependency"
    assert doctor_mod._geocoder_dataset(spec) is not None


def _live_geocoder_spec():
    """The geocoder module's OWN `__spec__`, the object the test-order bug mutated.

    Importing first is the point (Task 8 review, M2490): `find_spec` hands back
    `sys.modules[name].__spec__` only for a module already imported. In a run where nothing
    has imported the geocoder yet it builds a FRESH spec on every call, so a fake that
    mutated "its" spec in place would leave this one untouched and the live-spec assertion
    would pass with the bug back in.
    """
    module = importlib.import_module("reverse_geocoder")
    live = module.__spec__
    assert live is not None
    assert importlib.util.find_spec("reverse_geocoder") is live  # what the fakes start from
    return live


def test_the_dataset_check_fails_when_the_dataset_is_absent(monkeypatch, tmp_path):
    """The negative control on the check above. Without it, `_geocoder_dataset` could
    return a path that always exists and the green line would mean nothing."""
    import importlib.util

    real = importlib.util.find_spec
    live = _live_geocoder_spec()
    before = live.submodule_search_locations

    def fake(name):
        spec = real(name)
        if name == "reverse_geocoder" and spec is not None:
            # A COPY: the spec is the live module's own `__spec__`, and mutating it in
            # place breaks every later `pam doctor` in the process (test-order bug).
            spec = copy.copy(spec)
            spec.submodule_search_locations = [str(tmp_path)]  # no CSV in here
        return spec

    monkeypatch.setattr(doctor_mod.shutil, "which", lambda name: r"C:\tools\exiftool.exe")
    monkeypatch.setattr(doctor_mod, "_exiftool_version", lambda exe: "13.10")
    _tags(monkeypatch, f"{SPEC_11_CAPTION_MODEL}:latest")
    monkeypatch.setattr(doctor_mod.importlib.util, "find_spec", fake)
    geocoder = next(c for c in run_checks() if c.name == "reverse_geocoder")
    assert geocoder.ok is False
    assert "DOWNLOAD" in geocoder.detail and "rg_cities1000.csv" in geocoder.detail
    assert live.submodule_search_locations == before  # the live spec was not touched


def test_a_locationless_spec_is_a_legible_line_and_not_an_AttributeError(monkeypatch):
    """`_geocoder_dataset` is annotated `-> Path | None` and nothing exercised the `None`.

    Closes the M9 Task 6 review's M1174 survivor: deleting the `dataset is None` operand
    from `if dataset is None or not dataset.is_file():` left all 2309 tests green, because
    no test ever produced a spec without `submodule_search_locations`. Under that mutant
    `pam doctor` raises an unhandled `AttributeError`, which is the one thing `pam doctor`
    exists not to do - it is the command a user runs BECAUSE something is already wrong.

    A spec with no search locations is what a single-module distribution produces, which is
    what `reverse_geocoder` would become if it ever stopped shipping as a package. The
    declared return type says the case is real, so the case gets a test rather than the
    annotation getting a lie.
    """
    real = importlib.util.find_spec
    live = _live_geocoder_spec()
    before = live.submodule_search_locations

    def locationless(name):
        spec = real(name)
        if name == "reverse_geocoder" and spec is not None:
            spec = copy.copy(spec)  # never mutate the live module's own spec
            spec.submodule_search_locations = None
        return spec

    monkeypatch.setattr(doctor_mod.shutil, "which", lambda name: r"C:\tools\exiftool.exe")
    monkeypatch.setattr(doctor_mod, "_exiftool_version", lambda exe: "13.10")
    _tags(monkeypatch, f"{SPEC_11_CAPTION_MODEL}:latest")
    monkeypatch.setattr(doctor_mod.importlib.util, "find_spec", locationless)

    assert doctor_mod._geocoder_dataset(locationless("reverse_geocoder")) is None
    geocoder = next(c for c in run_checks() if c.name == "reverse_geocoder")
    assert geocoder.ok is False
    assert _GEOCODER_CSV in geocoder.detail
    assert live.submodule_search_locations == before  # the live spec was not touched


def test_pam_doctor_does_not_import_the_heavy_dependencies_to_check_for_them():
    """`find_spec`, not `import`. `reverse_geocoder` pulls in scipy - 37 MB - and
    `pam doctor` is what both CI lanes run before anything else.

    Asserted in a FRESH interpreter: this test session has already imported all four
    (`test_deps.py` and the canary both do), so `sys.modules` here would answer a
    question about pytest rather than about `run_checks`.
    """
    probe = (
        "import sys;"
        " import pam.doctor as d;"
        # The subprocess is outside conftest's tripwire and outside monkeypatch, so the
        # probe is disarmed by hand. Without this the test would ask the REAL Ollama on
        # this machine - the one thing no test in this suite may do (clarification 7).
        " d._ollama_tags = lambda port, **kw: [];"
        " d.run_checks();"
        " print(sorted({'PIL','pillow_heif','imagehash','reverse_geocoder','scipy','numpy'}"
        " & set(sys.modules)))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == "[]", f"pam doctor imported the dependencies it was only asked about: {out}"


# --- M10 Task 7: Ollama (design 12 rev 17, amendments A9 + A10) -----------------------
#
# THREE CHECKS, THREE FAULTS, THREE FIXES, and every one of them is driven against a
# fake in BOTH directions. On this machine the real answer to all three is "fine", so a
# test that called `run_checks()` and asserted `ok` would pass here and fail in CI - and
# `pam models pull` against the real server would start a multi-gigabyte download from a
# test run. `tests/conftest.py`'s tripwire makes forgetting loud.


def _ollama(checks):
    return {c.name: c for c in checks if c.name.startswith("Ollama")}


def test_doctor_reports_three_separate_ollama_lines(monkeypatch):
    """Spec 12 rev 17: "It reports three things separately, each with its own Fix: route."

    The names are spelled out and compared as an ordered list, so collapsing two of them
    into one line - the obvious simplification, and the one that loses the distinction
    between "nothing is listening" and "the model is missing" - fails here.
    """
    _ok(monkeypatch)
    assert [c.name for c in run_checks() if c.name.startswith("Ollama")] == [
        "Ollama",
        "Ollama API",
        "Ollama model",
    ]


def test_every_ollama_line_names_the_default_port_it_assumed(monkeypatch):
    """A10's whole reason: doctor is not archive-scoped, so it must say which port.

    Asserted on the HAPPY path as well as the failing one. A user who moved
    `[enrich.caption] ollama_port` needs to read "this answer is about 11434" whichever
    way the answer went - a green line about the wrong server is a worse lie than a red
    one.
    """
    _ok(monkeypatch)
    ollama = _ollama(run_checks())["Ollama"]
    assert ollama.ok is True
    assert str(SPEC_11_OLLAMA_PORT) in ollama.detail
    assert "ollama_port" in ollama.detail

    _ollama_fails(monkeypatch, PamNetworkError("connection refused"))
    down = _ollama(run_checks())["Ollama"]
    assert down.ok is False
    assert f"127.0.0.1:{SPEC_11_OLLAMA_PORT}" in down.detail
    assert "ollama.com" in down.detail


def test_doctor_asks_the_default_port_and_nothing_else(monkeypatch):
    """The port is not merely PRINTED, it is the port doctor actually probes.

    Without this the message could name 11434 while the probe asked something else, and
    every assertion about the wording above would still pass.
    """
    _ok(monkeypatch)
    asked = _tags(monkeypatch, f"{SPEC_11_CAPTION_MODEL}:latest")
    run_checks()
    assert asked == [SPEC_11_OLLAMA_PORT]


def test_an_unreachable_server_is_one_line_and_never_a_traceback(monkeypatch):
    """The M10 plan: "an unreachable server is a one-line `ok: no`, not a stack trace."

    The two downstream lines say they were NOT CHECKED rather than claiming the model is
    absent, because pam does not know: a red "the model is missing" over a server that
    never answered would send the user to `pam models pull`, which cannot work either.
    """
    _ok(monkeypatch)
    _ollama_fails(monkeypatch, PamNetworkError("nothing is listening"))
    checks = _ollama(run_checks())
    assert [c.ok for c in checks.values()] == [False, False, False]
    assert "not checked" in checks["Ollama API"].detail
    assert "not checked" in checks["Ollama model"].detail
    assert "pam models pull" not in checks["Ollama model"].detail


def test_something_that_is_not_ollama_on_the_port_is_its_own_fault(monkeypatch):
    """The middle check, and the reason there are three rather than two.

    `_ollama_tags` raises `ValueError` when something ANSWERED and was not a model
    listing, which is a different fix from "install Ollama": the port is taken by
    something else, or the server is too old for the route. A design that collapsed the
    two would tell a user with a port clash to install software they already have.
    """
    _ok(monkeypatch)
    _ollama_fails(monkeypatch, ValueError("GET /api/tags answered HTTP 500 Server Error"))
    checks = _ollama(run_checks())
    assert checks["Ollama"].ok is True
    assert checks["Ollama API"].ok is False
    assert "not as Ollama" in checks["Ollama API"].detail
    assert "500" in checks["Ollama API"].detail
    assert checks["Ollama model"].ok is False


def test_a_missing_model_is_a_line_that_names_pam_models_pull(monkeypatch):
    """Spec 12's third `Fix:` route, verbatim: "when it is not, Run: pam models pull"."""
    _ok(monkeypatch)
    _tags(monkeypatch, "llama3:latest")
    checks = _ollama(run_checks())
    assert (checks["Ollama"].ok, checks["Ollama API"].ok) == (True, True)
    assert checks["Ollama model"].ok is False
    assert "Run: pam models pull" in checks["Ollama model"].detail
    assert SPEC_11_CAPTION_MODEL in checks["Ollama model"].detail


@pytest.mark.parametrize("installed", ["qwen2.5vl", "qwen2.5vl:latest"])
def test_finding_o2_both_spellings_of_the_default_model_count_as_installed(
    monkeypatch, installed
):
    """FINDING O2, and it is the reason this task carries a measurement.

    Spec 11's default is the BARE name `qwen2.5vl`. The real `GET /api/tags` on the
    the developer's machine reports that same working model as `qwen2.5vl:latest`. A check
    written as `model in tags` reports `ok: no` on a correct install and sends the user
    to download a model they already have.

    BOTH SPELLINGS ARE DRIVEN, and that is the point of the parametrize: the real answer
    on this machine is `:latest`, which is exactly the case a test written against the
    bare name would have got wrong while looking right. The two names are literals rather
    than `SPEC_11_CAPTION_MODEL` plus a suffix, so the parametrize cannot be emptied by a
    change to the constant (M743: pytest SKIPS an empty parametrize).
    """
    _ok(monkeypatch)
    _tags(monkeypatch, installed)
    assert _ollama(run_checks())["Ollama model"].ok is True


def test_a_tagged_model_is_not_satisfied_by_a_different_tag():
    """The negative half of O2's rule, which the `:latest` cases alone cannot prove.

    `qwen2.5vl:7b` and `qwen2.5vl:latest` are different weights. Widening the match to
    "same name before the colon" would make every O2 case above pass and would tell a
    user their pinned model is present when it is not.
    """
    assert model_is_installed("qwen2.5vl", ["qwen2.5vl:latest"]) is True
    assert model_is_installed("qwen2.5vl", ["qwen2.5vl"]) is True
    assert model_is_installed("qwen2.5vl:7b", ["qwen2.5vl:latest"]) is False
    assert model_is_installed("qwen2.5vl:7b", ["qwen2.5vl:7b"]) is True
    assert model_is_installed("qwen2.5vl", ["qwen2.5vl-abliterated:latest"]) is False
    assert model_is_installed("qwen2.5vl", []) is False


def test_a_machine_without_ollama_is_a_correctly_installed_machine(monkeypatch):
    """`pam doctor` still exits 0, and the line is `-` rather than `x`.

    Captioning is opt-in (spec 11: `[enrich.caption] enabled = false`), so an absent
    Ollama is a configuration the user chose - amendment A2's rule about a disabled
    enricher, applied to the environment check for the same service. Making it exit 1
    would report the SHIPPED DEFAULT as a broken install, and would fail both CI lanes at
    their `pam doctor` step on runners that will never have Ollama.

    DISCLOSED: no normative sentence rules on this exit code. See `Check.required`.
    """
    from pam.cli import main
    from pam.errors import EXIT_OK

    _ok(monkeypatch)
    _ollama_fails(monkeypatch, PamNetworkError("nothing is listening"))
    result = CliRunner().invoke(cli, ["doctor"])
    assert result.exit_code == 0
    assert "- Ollama:" in result.output
    assert "optional" in result.output
    assert "x Ollama" not in result.output
    assert main(["doctor"]) == EXIT_OK


def test_a_required_failure_still_exits_1_while_an_optional_one_is_present(monkeypatch):
    """The other direction, and it is the one that keeps `required` honest.

    With BOTH a missing exiftool and a missing Ollama, the command must still exit 1 and
    still print the "fix the items marked x" line - an optional failure must not be able
    to swallow a required one, which is what a naive rewrite of the exit condition would
    do.
    """
    monkeypatch.setattr(doctor_mod.shutil, "which", lambda name: None)
    _ollama_fails(monkeypatch, PamNetworkError("nothing is listening"))
    result = CliRunner().invoke(cli, ["doctor"])
    assert result.exit_code == 1
    assert "x exiftool" in result.output
    assert "- Ollama:" in result.output
    assert "fix the items marked x" in result.output


def _faces_extra_absent(monkeypatch):
    """`find_spec` answers None for onnxruntime and numpy and nothing else.

    Stubbed for TWO names only, never blanket, on the same argument the dependency test
    above uses: a stub that fails everything would satisfy a check that only looked at
    `ok is False`, and would say nothing about the loop reporting the present ones.
    """
    real = doctor_mod.importlib.util.find_spec
    monkeypatch.setattr(
        doctor_mod.importlib.util,
        "find_spec",
        lambda name, *a, **k: None if name in ("onnxruntime", "numpy") else real(name, *a, **k),
    )


def test_a_machine_without_the_faces_extra_is_a_correctly_installed_machine(monkeypatch):
    """A22 + design 12 rev 20's R3: `-`, not `x`, and the exit code does not move.

    Face detection is opt-in (`[enrich.faces] enabled = false`) and the extra is ~14 MB of
    model runtime most of this product has no use for, so its absence is a configuration
    the user chose - exactly Ollama's argument one milestone later. Giving it the same
    mark as a missing exiftool teaches a reader to ignore the mark that matters.

    THIS TEST NEVER SKIPS, and that is the half worth stating: it is about the machine
    that does NOT have the extra, which is every machine on the push lane and most
    machines anywhere.
    """
    from pam.cli import main
    from pam.errors import EXIT_OK

    _ok(monkeypatch)
    _faces_extra_absent(monkeypatch)
    result = CliRunner().invoke(cli, ["doctor"])
    assert result.exit_code == 0
    assert "- Face models:" in result.output
    assert "x Face models" not in result.output
    assert _pip("faces") in result.output
    assert main(["doctor"]) == EXIT_OK


def test_the_faces_line_names_what_it_deliberately_does_not_check(monkeypatch):
    """A22: `pam doctor` HAS NO ARCHIVE, so it cannot read `[enrich.faces] model_dir`.

    Pack validation belongs to `pam enrich faces`, which has one. A doctor that stayed
    silent about the gap would leave a user reading a green line as "the model pack is
    fine", which it does not say and cannot: the line is about the interpreter, not about
    an archive. Both branches carry the sentence, because the state a user is most likely
    to misread is the one where everything else looks right.
    """
    _ok(monkeypatch)
    for absent in (True, False):
        if absent:
            _faces_extra_absent(monkeypatch)
        result = CliRunner().invoke(cli, ["doctor"])
        line = [ln for ln in result.output.splitlines() if "Face models:" in ln]
        assert len(line) == 1, result.output
        assert "[enrich.faces] model_dir" in line[0]
        assert "pam faces manifest" in line[0]
        assert "--archive" not in result.output


def test_the_faces_check_reaches_no_archive_and_imports_neither_package(monkeypatch):
    """The two things that would make this line wrong rather than merely unhelpful.

    (1) It must not IMPORT onnxruntime: `pam doctor` is what CI runs before every lane and
    what a user runs when something is broken, and paying a model-runtime load to answer a
    yes/no question is the cost `_dependency_checks` already refuses for Pillow and scipy.
    Watched on the CALL - `find_spec` returning a spec and `import_module` never being
    reached are two different facts, and only the second is this one.

    (2) It must not resolve an archive. Asserted by driving the check itself rather than
    the command, so a future `pam doctor --archive` could not satisfy it by accident.
    """
    imported = []
    monkeypatch.setattr(
        doctor_mod.importlib,
        "import_module",
        lambda name, *a, **k: imported.append(name),
    )
    checks = doctor_mod._faces_checks(PIP_INSTALL)
    assert [c.name for c in checks] == ["Face models"]
    assert checks[0].required is False
    assert imported == [], f"the faces check imported {imported}"


def test_check_defaults_to_required_so_a_new_check_fails_the_command():
    """The safe way round: a check written without thinking about `required` is required.

    Pinned as a property of the dataclass rather than inferred from the checks that
    exist, because the failure it prevents is a check somebody adds in M11.
    """
    assert Check("x", False, "d").required is True
    assert Check("x", False, "d", required=False).required is False


def test_the_pull_message_is_printed_before_anything_blocks():
    """UX 2 forbids a silent multi-minute wait, and spec 12 says why there is no bar.

    `LoopbackHttpClient` does not stream, so the pull is one request that shows no
    progress. The message therefore has to carry four things, and each is asserted: what
    it is doing, where, that it is minutes and gigabytes, and that Ollama - not pam - is
    the thing downloading.
    """
    message = pull_message("qwen2.5vl", SPEC_11_OLLAMA_PORT)
    assert "qwen2.5vl" in message
    assert f"127.0.0.1:{SPEC_11_OLLAMA_PORT}" in message
    assert "gigabytes" in message and "minutes" in message
    assert "Ollama does the download, not pam" in message
    assert "no progress" in message


# --- M10 Task 7 review, finding F4: the tripwire is asserted ARMED --------------------
#
# `tests/conftest.py::_no_test_talks_to_the_real_ollama` is the control that keeps the
# REAL Ollama - installed on this machine - out of the suite. Removing any ONE of its
# three patch targets left all 2743 tests green (M1537, M1538), because every test that
# cares installs its own fake over the same name and none of them can see the tripwire's
# state. A control nothing asserts is a control that can be half-disarmed by an edit that
# looks like tidying, and the failure it lets through is silent in the worst direction:
# green here, red in CI, or a multi-gigabyte download started by a test run.
#
# CLAUDE.md states the rule this closes: a sanction list must be ASSERTED ARMED at setup,
# never merely installed.
#
# SPELLED OUT BY HAND FROM THE REASON, not read back from `conftest`. The third entry is
# the one that matters and the one an edit would drop: `pam/cli.py` does
# `from pam.doctor import ... pull_model`, so it holds its OWN reference and a patch on
# `pam.doctor.pull_model` alone leaves `pam models pull` calling the real function.
#
# THE ARGUMENTS ARE CHOSEN SO THE UNBLOCKED CALL CANNOT REACH THE REAL SERVER EITHER:
# port 1 has nothing listening, so a disarmed tripwire raises `PamNetworkError` - still a
# failure of `pytest.raises(AssertionError)`, and still not a request to the Ollama this
# machine is running. A call written with no arguments at all would "fail" on a missing
# positional and pass this test for a reason that has nothing to do with the tripwire.
_UNREACHABLE = {"port": 1, "timeout": 0.5}
BLOCKED_OLLAMA_ENTRY_POINTS = [
    ("pam.doctor", "_ollama_tags", (1,), {"timeout": 0.5}, "doctor's GET /api/tags probe"),
    ("pam.doctor", "pull_model", ("qwen2.5vl",), _UNREACHABLE, "the pull, at its definition"),
    ("pam.cli", "pull_model", ("qwen2.5vl",), _UNREACHABLE, "cli holds a second reference"),
    # M10 TASK 8's ROUTE, and it is a CLASS rather than a function: `enrich/caption.py`
    # builds its own `net.LoopbackHttpClient`, so a caption test that forgot to point at
    # the stub would take design 11's default `ollama_port` and reach the server on this
    # machine. The arguments are the pair the tripwire refuses - THE DEFAULT PORT with THE
    # REAL RESOLVER - and note that a DISARMED tripwire would not raise at all here:
    # construction only resolves, so the unblocked call succeeds silently, which is exactly
    # the failure this row exists to make loud.
    (
        "pam.net",
        "LoopbackHttpClient",
        ("127.0.0.1", SPEC_11_OLLAMA_PORT),
        {},
        "the caption enricher builds its own client",
    ),
]

assert BLOCKED_OLLAMA_ENTRY_POINTS, "the blocked-entry-point list is empty; it blocks nothing"


@pytest.mark.parametrize(
    ("module", "attribute", "args", "kwargs", "why"), BLOCKED_OLLAMA_ENTRY_POINTS
)
def test_the_real_ollama_tripwire_is_armed_at_every_entry_point(
    module, attribute, args, kwargs, why
):
    """Every route to the machine's own Ollama refuses, loudly, in an ordinary test.

    Deliberately does NOT request `real_ollama`: the question is what an ordinary test -
    one that forgot to script a fake - meets when it reaches for these names.
    """
    import importlib

    blocked = getattr(importlib.import_module(module), attribute)
    with pytest.raises(AssertionError, match="reached the real Ollama"):
        blocked(*args, **kwargs)


def test_the_tripwire_hands_back_the_real_functions_rather_than_hiding_them(real_ollama):
    """The other direction: `real_ollama` must be the real thing, not a second refusal.

    If it handed back the refusal, Task 7's whole Step 6 would be proving a property of a
    fake - and it would prove it green, because a refusal raises `AssertionError` and the
    tests there expect an exception. So both are checked to be the production functions by
    name, and checked NOT to be the tripwire's closure.
    """
    assert (real_ollama.tags.__name__, real_ollama.pull.__name__) == (
        "_ollama_tags",
        "pull_model",
    )
    assert "refuse" not in (real_ollama.tags.__name__, real_ollama.pull.__name__)


# --- M10 Task 7 review, finding F6: `_ollama_tags`'s own body, against the stub -------
#
# M1556: deleting the `response.status != 200` check from `_ollama_tags` left all 2747
# tests green. Every doctor test above drives a FAKE that raises the exception the case
# wants, so the mapping the whole three-check design rests on - "nothing answered" is a
# `PamNetworkError` and "something answered and was not a model listing" is a `ValueError`
# - was asserted only about the fakes. The function that decides it was exercised on one
# path: a well-formed 200.
#
# So the real function is driven against `tests/ollama_stub.py` here, one row per shape a
# server can actually put on the wire. The expectation is spelled from the FAILURE FAMILY
# the caller must distinguish, not from the code's `except` clause.

_TAGS = "/api/tags"


def test_a_well_formed_listing_is_parsed_into_its_names(real_ollama):
    """The happy path, with two models, so a one-element answer cannot pass by luck."""
    from ollama_stub import OllamaStub, tags_reply

    with OllamaStub({_TAGS: [tags_reply("qwen2.5vl:latest", "llama3:8b")]}) as stub:
        assert real_ollama.tags(stub.port) == ["qwen2.5vl:latest", "llama3:8b"]


@pytest.mark.parametrize(
    ("reply_kwargs", "expected"),
    [
        ({"status": 500, "body": {"models": []}}, "500"),
        ({"status": 404, "body": {"models": []}}, "404"),
        ({"body": b"<html>not ollama</html>"}, "did not answer a model listing"),
        ({"body": {"tags": []}}, "did not answer a model listing"),
        ({"body": {"models": [{"id": "x"}]}}, "did not answer a model listing"),
        ({"body": {"models": "qwen2.5vl"}}, "did not answer a model listing"),
    ],
    ids=[
        "http-500-with-a-body-that-would-otherwise-parse",
        "http-404-with-a-body-that-would-otherwise-parse",
        "answered-html",
        "json-without-a-models-key",
        "models-entries-without-a-name",
        "models-is-a-string",
    ],
)
def test_something_answering_that_is_not_a_model_listing_is_a_ValueError(
    real_ollama, reply_kwargs, expected
):
    """ "Something is listening and it is not Ollama" - the middle `pam doctor` line.

    THE FIRST TWO ROWS ARE THE ONES M1556 SURVIVED. Their bodies parse perfectly, so only
    the status code separates them from success; without the status check pam would report
    a model listing read off an HTTP 500.
    """
    from ollama_stub import OllamaStub, Reply

    with OllamaStub({_TAGS: [Reply(**reply_kwargs)]}) as stub:
        with pytest.raises(ValueError, match=expected) as raised:
            real_ollama.tags(stub.port)
    # The two families must stay distinguishable: a `PamNetworkError` here would send the
    # user to "install Ollama" when Ollama is running and answering.
    assert not isinstance(raised.value, PamNetworkError)


def test_nothing_listening_is_a_PamNetworkError_and_not_a_ValueError(real_ollama):
    """The other family, from the wire rather than from a fake.

    A closed port on 127.0.0.1: the stub is started and stopped so the port is one the OS
    really handed out and really is not listening, rather than a number guessed at.
    """
    from ollama_stub import OllamaStub

    with OllamaStub({_TAGS: []}) as stub:
        closed = stub.port

    with pytest.raises(PamNetworkError, match=f"127.0.0.1:{closed}"):
        real_ollama.tags(closed, timeout=0.5)


# --- M10 Task 7 review, finding F7: doctor must never hang, and that was unpinned ------
#
# M1559: raising `_OLLAMA_PROBE_TIMEOUT_S` from 2 s to 600 s left all 2755 tests green.
# The M10 plan's Step 2 says `pam doctor` "must stay fast and must never hang", and BOTH
# CI lanes run `pam doctor` as a bare gating step - so a probe that waits ten minutes on a
# socket that accepts and never answers hangs the lane, not just the user. The hanging case
# is not a closed port (which is refused instantly); it is exactly the case doctor's middle
# check exists for: something else is listening on 11434.
#
# TWO ASSERTIONS, BECAUSE EITHER ALONE IS VACUOUS. A bound on a constant says nothing if
# the constant is not what reaches the socket; a behavioural test with an explicit timeout
# says nothing about the value doctor actually ships.


def test_the_probe_gives_up_rather_than_hanging_when_a_server_never_answers(real_ollama):
    """The timeout is WIRED to the socket, proven against a server that accepts and waits."""
    import threading
    import time

    from ollama_stub import OllamaStub, Reply

    never = threading.Event()
    with OllamaStub({_TAGS: [Reply(body={"models": []}, block_until=never)]}) as stub:
        started = time.monotonic()
        with pytest.raises(PamNetworkError, match="timed out"):
            real_ollama.tags(stub.port, timeout=0.2)
        elapsed = time.monotonic() - started
        never.set()  # release the handler so the stub can shut down promptly

    assert elapsed < 5.0, (
        "the probe's timeout argument did not reach the socket; it waited"
        f" {elapsed:.1f}s for a 0.2s timeout"
    )


def test_the_shipped_probe_timeout_is_short_enough_that_pam_doctor_cannot_hang(real_ollama):
    """And the value doctor ships is the one the test above proved is honoured.

    The ceiling is written from the requirement ("must stay fast and must never hang",
    with both CI lanes running `pam doctor` as a bare step), not read back from the
    constant - an assertion of the form `X == doctor.X` passes for any value of it.
    """
    import inspect

    assert 0 < doctor_mod._OLLAMA_PROBE_TIMEOUT_S <= 5.0, (
        "pam doctor's Ollama probe may wait"
        f" {doctor_mod._OLLAMA_PROBE_TIMEOUT_S}s on a socket that accepts and never"
        " answers. Doctor is what a user runs to find out why something is wrong, and"
        " what both CI lanes run as a bare gating step."
    )
    # Through the fixture, because the module attribute is the tripwire's refusal in an
    # ordinary test and a `*args, **kwargs` closure has no `timeout` parameter to read.
    default = inspect.signature(real_ollama.tags).parameters["timeout"].default
    assert default == doctor_mod._OLLAMA_PROBE_TIMEOUT_S, (
        "the bound above is on a constant the probe does not use"
    )


# --- M12 Task 12 Part B, Step B11: the optional OCR line (design 12 rev 28, Q4) ----------------

# Q4's scope sentence, spelled HERE from the spec's words ("naming `[enrich.ocr] model_dir` as not
# checked here - pam doctor has no archive"), never read from `pam/doctor.py`.
OCR_SCOPE = "[enrich.ocr] model_dir is not checked here - pam doctor has no archive."


def test_a_machine_without_the_ocr_extra_is_a_correctly_installed_machine(monkeypatch):
    """Q4's absent form, `- OCR: the optional [ocr] extra is not installed (...). Run: pip install
    "photo-archive-manager[ocr]".`, then the scope sentence - `-`, never `x`, and the exit code does not move."""
    _ok(monkeypatch)
    _faces_extra_absent(monkeypatch)  # the same two packages: [ocr] IS the [faces] pair (Q1)
    result = CliRunner().invoke(cli, ["doctor"])
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.output.splitlines() if "OCR:" in ln]
    assert lines == [
        "  - OCR: the optional [ocr] extra is not installed (onnxruntime, numpy missing)."
        f" Run: {_pip('ocr')}. " + OCR_SCOPE
    ], result.output


def test_the_ocr_line_names_both_versions_the_platform_and_python(monkeypatch):
    """Q4's present form, `ok OCR: onnxruntime <v>, numpy <v> on <platform>, Python <v>.`, then
    the scope sentence. Versions and platform are stubbed: the line is about `run_checks`, not
    about whichever interpreter happens to run this suite."""
    _ok(monkeypatch)
    _faces_extra_present(monkeypatch)
    versions = {"onnxruntime": "1.30.0", "numpy": "2.5.3"}
    real_version = doctor_mod.metadata.version
    monkeypatch.setattr(
        doctor_mod.metadata,
        "version",
        lambda name: versions[name] if name in versions else real_version(name),
    )
    monkeypatch.setattr(doctor_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(doctor_mod.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(doctor_mod.platform, "python_version", lambda: "3.12.9")
    result = CliRunner().invoke(cli, ["doctor"])
    lines = [ln for ln in result.output.splitlines() if "OCR:" in ln]
    assert lines == [
        "  ok OCR: onnxruntime 1.30.0, numpy 2.5.3 on windows-AMD64, Python 3.12.9. "
        + OCR_SCOPE
    ], result.output


def test_the_ocr_check_is_optional_and_imports_neither_package(monkeypatch):
    """`required=False` as a FIELD, and watched on the CALL: `find_spec` only, no import."""
    imported = []
    monkeypatch.setattr(
        doctor_mod.importlib, "import_module", lambda name, *a, **k: imported.append(name)
    )
    checks = doctor_mod._ocr_checks(PIP_INSTALL)
    assert [c.name for c in checks] == ["OCR"]
    assert checks[0].required is False
    assert imported == [], f"the OCR check imported {imported}"


# --- the controls this file must not lose ---------------------------------------------
#
# ADDED BY THE M11 TASK 7 REVIEW (finding F2/M1753). This module had no floor at all, and
# three of Task 7's tests were deleted from it with the whole suite green - among them TWO
# of the four rows Task 7's plan writes down by hand as tests that MUST NEVER SKIP, and
# the only test in the repository that pins `Check.required` as a field rather than
# through the exit code. That is `M1670` and M10's condition C4 exactly: registration
# stops where the author happened to be looking.
#
# Hand-spelled, never derived from `globals()`: a list built from the module's own
# contents is satisfied by whatever the module happens to contain. Scoped to the faces
# checks deliberately - a reviewer floors what this task added, and floors the rest of
# `pam doctor`'s coverage the day a task touches it.

LOAD_BEARING_CONTROLS = {
    "test_a_machine_without_the_faces_extra_is_a_correctly_installed_machine": (
        "A22 + design 12 rev 20 R3, and Step 10's never-skip inventory row 2: `-` not"
        " `x`, exit 0, and a machine with no face stack is a correctly installed machine"
    ),
    "test_the_faces_line_names_what_it_deliberately_does_not_check": (
        "A22's whole point: doctor stays archive-independent and SAYS which key it is not"
        " checking, in both branches - pack validation belongs to `pam enrich faces`"
    ),
    "test_the_faces_check_reaches_no_archive_and_imports_neither_package": (
        "the only test that pins `required=False` as a FIELD (measured at M1744a), and"
        " the only one that watches the import: doctor must not load a model runtime"
    ),
    # M12 Task 12 Part B (Step B11, Q4), registered in the commit that adds them.
    "test_a_machine_without_the_ocr_extra_is_a_correctly_installed_machine": (
        "Q4's absent form verbatim, `-` not `x`, exit 0, with the scope sentence"
    ),
    "test_the_ocr_line_names_both_versions_the_platform_and_python": (
        "Q4's present form verbatim, with the scope sentence"
    ),
    "test_the_ocr_check_is_optional_and_imports_neither_package": (
        "required=False as a FIELD, and no model-runtime import to answer a yes/no question"
    ),
}

assert LOAD_BEARING_CONTROLS, "this module's control list is empty; it protects nothing"


def test_no_faces_control_in_this_file_can_be_deleted_without_saying_so():
    """The anti-deletion floor for the faces checks (M11 Task 7 review, F2).

    Removing one of these is a deliberate edit to `LOAD_BEARING_CONTROLS`, with the reason
    it was kept written beside it, in the same commit.
    """
    import ast
    from pathlib import Path

    present = {
        node.name
        for node in ast.parse(Path(__file__).read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef)
    }
    missing = sorted(set(LOAD_BEARING_CONTROLS) - present)
    assert not missing, (
        f"a load-bearing faces control was deleted or renamed: {missing}"
        f" ({[LOAD_BEARING_CONTROLS[m] for m in missing]})."
    )
