"""Environment checks behind `pam doctor`, and the model fetch its Ollama line points at.

`pam models pull` lives here rather than in a module of its own because it is the `Fix:`
route the Ollama checks below print, and because design 12 gives it the same shape they
have: no archive, no catalog, no session log - a question about this machine's
environment, asked before anything depends on the answer.
"""

import importlib.util
import json
import platform
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from importlib import metadata
from importlib.machinery import ModuleSpec
from pathlib import Path

from pam import install_method
from pam.config import Config
from pam.errors import PamNetworkError
from pam.install_method import Provenance, exiftool_fix, missing_extra, provenance, repair
from pam.net import LoopbackHttpClient, Resolver, system_resolver
from pam.paths import app_root

# M9's four runtime dependencies (design 1's stack table), as
# (distribution, import name, what needs it). The closure was `click` alone for eight
# milestones; M9 spends it for the first pixels pam decodes and the first place name it
# produces, and `tests/test_deps.py` is the gate on that list.
#
# WHY THEY ARE CHECKED HERE AT ALL. They are declared in `pyproject.toml`, so a clean
# `pip install photo-archive-manager` (OD-12) has them - but an editable checkout, a partially upgraded venv or a
# wheel installed with `--no-deps` does not, and the symptom is an `ImportError` raised
# in the middle of a long enrichment run over somebody's irreplaceable archive. That is
# the exiftool precedent exactly: one legible line, before anything depends on it.
_RUNTIME_DEPENDENCIES = (
    ("Pillow", "PIL", "decodes images"),
    ("pillow-heif", "pillow_heif", "reads HEIC, which Pillow alone cannot"),
    ("imagehash", "imagehash", "perceptual hashing"),
    ("reverse_geocoder", "reverse_geocoder", "offline place names"),
)

# `reverse_geocoder`'s dataset filename. IT IS CHECKED SEPARATELY FROM THE PACKAGE, and
# the reason is vision S4. `RGeocoder.extract()` reads this file if it is there and
# DOWNLOADS IT FROM `download.geonames.org` OVER PLAIN HTTP if it is not - so "the
# geocoder is offline", the single sentence design 1 chose the library for, is true
# exactly while the file exists. An installed package with the CSV missing is the one
# configuration in which pam reaches the network, and it must be a line here rather than
# a surprise on somebody's first geocode. `tests/test_net_canary.py` holds the other
# half: the behavioural proof that a real query opens no socket.
_GEOCODER_DATASET = "rg_cities1000.csv"

# E13 (M15 Task 11): every repair and extra sentence below follows how pam was installed - the
# pip sentences stay here, beside their lines (the HT documents quote them), and the installer's
# are spelled once in `pam/install_method.py`. Only the advice moves: no mark, no exit code.

# --- M10 Task 7: Ollama, the external service design 1 sanctioned in rev 2 ------------
#
# OLLAMA IS NOT A PYTHON DEPENDENCY AND MUST NEVER BECOME ONE. It is an external service
# on the exiftool precedent - user-installed, checked here, talked to over HTTP on
# loopback through `pam/net.py`, which spec 10 names as the only client pam will ever
# have. The `ollama` pip package would be both a sixth runtime dependency and a second
# HTTP client, and spec 10 permits exactly one.
#
# WHY DOCTOR CHECKS THE DEFAULT PORT AND SAYS SO (spec 12 rev 17, amendment A10).
# `pam doctor` is deliberately not archive-scoped: it takes no `--archive` and reads no
# `pam.toml`, because its whole value is answering BEFORE an archive exists (HT-01). But
# `ollama_port` is per-archive configuration. So the port is the default one and the
# printed line names it, so a user who moved it reads a false negative as the
# configuration question it is rather than as a broken install.
_OLLAMA_HOST = "127.0.0.1"

# The default port and the default caption model, read from `Config`'s own defaults so
# that doctor and spec 11 cannot drift apart. `tests/test_doctor.py` spells both out from
# the spec by hand - the independent second implementation this project's oracle rule
# asks for.
DEFAULT_OLLAMA_PORT = Config().ollama_port
DEFAULT_CAPTION_MODEL = Config().caption_model

# SHORT, BECAUSE DOCTOR MUST NEVER HANG. Everything else in this file answers in
# milliseconds and `pam doctor` is what CI runs before every lane; a probe of a port
# nothing is listening on has to fail fast and print one line, not stall the command a
# user ran to find out why something is wrong. Two seconds is far more than a server on
# this machine needs to accept a connection and answer a listing.
_OLLAMA_PROBE_TIMEOUT_S = 2.0

# A MODEL PULL IS GIGABYTES AND MINUTES, and `LoopbackHttpClient` does not stream
# (spec 10: "this module's entire job is being the one place that decides when a socket
# exists"), so the whole download happens inside one request that shows no progress. An
# hour is sized for a first pull of a multi-gigabyte vision model over a domestic
# connection; the alternative - a timeout that fires mid-download - would leave the user
# with a failure message about a transfer that was working.
_PULL_TIMEOUT_S = 3600.0


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    # M10 Task 7. `False` means "report this honestly, but do not fail `pam doctor` over
    # it": the thing checked is optional, and a machine without it is correctly
    # installed. Ollama is the only such check today, and the reason is spec 11's
    # `[enrich.caption] enabled = false` - captioning is opt-in, so an absent Ollama is a
    # configuration the user chose, exactly as amendment A2 rules that a disabled
    # enricher is not an error. It defaults to `True`, so every check written before this
    # field is unchanged and a check added without thinking about it fails the command,
    # which is the safe way round.
    #
    # DISCLOSED, BECAUSE NO NORMATIVE SENTENCE RULES ON IT. Spec 12 names doctor's
    # subjects as "exiftool, optional extras, Ollama reachability, disk space" and the
    # M10 plan says an unreachable server is "a one-line `ok: no`", which is what this
    # prints. Neither says whether the command should then EXIT 1. Making it do so would
    # report the shipped default configuration as a broken install, and would turn both
    # CI lanes red at their `pam doctor` step on runners that will never have Ollama.
    required: bool = True


def _dependency_checks(installed: Provenance) -> list[Check]:
    """One `Check` per runtime dependency, WITHOUT IMPORTING ANY OF THEM.

    `find_spec` answers "is it installed" from the finders alone; importing Pillow, or
    worse `reverse_geocoder` (which pulls in scipy), would make `pam doctor` pay a real
    import cost to answer a yes/no question, and `pam doctor` is what CI runs before
    every lane.
    """
    checks: list[Check] = []
    for distribution, module, purpose in _RUNTIME_DEPENDENCIES:
        try:
            spec = importlib.util.find_spec(module)
        except (ImportError, ValueError):  # a broken or half-removed installation
            spec = None
        if spec is None:
            checks.append(
                Check(
                    distribution,
                    False,
                    f"not installed ({purpose}). "
                    + repair(installed, f"Run: pip install {distribution}"),
                )
            )
            continue
        try:
            version = metadata.version(distribution)
        except metadata.PackageNotFoundError:
            version = "unknown version"
        detail = f"{version} ({purpose})"
        if module == "reverse_geocoder":
            dataset = _geocoder_dataset(spec)
            if dataset is None or not dataset.is_file():
                checks.append(
                    Check(
                        distribution,
                        False,
                        f"{version} installed but its offline dataset {_GEOCODER_DATASET} is"
                        " missing, so a place lookup would DOWNLOAD it from the internet. "
                        + repair(
                            installed,
                            f"Reinstall it: pip install --force-reinstall {distribution}",
                        ),
                    )
                )
                continue
            detail = f"{version} ({purpose}, dataset present - no network)"
        checks.append(Check(distribution, True, detail))
    return checks


def _geocoder_dataset(spec: ModuleSpec) -> Path | None:
    """Where `reverse_geocoder` keeps its CSV, from the spec rather than from an import.

    The package resolves it as `os.path.dirname(__file__)` joined with the filename, so
    the package's own search location is the same answer without executing its module.
    """
    locations = list(spec.submodule_search_locations or [])
    return Path(locations[0]) / _GEOCODER_DATASET if locations else None


def _ollama_tags(
    port: int,
    *,
    host: str = _OLLAMA_HOST,
    timeout: float = _OLLAMA_PROBE_TIMEOUT_S,
    resolver: Resolver = system_resolver,
) -> list[str]:
    """Every model tag `GET /api/tags` lists, or an exception saying which half failed.

    THE TWO FAILURE FAMILIES ARE DIFFERENT QUESTIONS AND ARE RAISED SEPARATELY.
    `PamNetworkError` means nothing answered on that port - install Ollama, or start it.
    `ValueError` means something answered and it was not a model listing pam can read: a
    different process on 11434, or an Ollama too old to have this route. Collapsing the
    two into one line would send half the users who hit it to the wrong fix.

    `host` and `resolver` are test seams, and they are the same pair `pam/net.py` already
    exposes for the same reason: the resolution IS the security boundary, so a test that
    cannot control what a name resolves to can only prove that pam refuses IP literals,
    which is the easy half.
    """
    client = LoopbackHttpClient(host, port, timeout=timeout, resolver=resolver)
    response = client.request("GET", "/api/tags")
    if response.status != 200:
        raise ValueError(f"GET /api/tags answered HTTP {response.status} {response.reason}")
    try:
        models = json.loads(response.body)["models"]
        return [str(model["name"]) for model in models]
    except (json.JSONDecodeError, TypeError, KeyError, UnicodeDecodeError) as exc:
        raise ValueError(f"GET /api/tags did not answer a model listing: {exc}") from exc


def model_is_installed(model: str, tags: list[str]) -> bool:
    """Is `model` in what `/api/tags` listed - counting Ollama's implicit `:latest` tag?

    FINDING O2, MEASURED ON THE DEVELOPER'S MACHINE 2026-09-01 AND NOT A HYPOTHETICAL. Spec
    11's default is `qwen2.5vl`; `GET /api/tags` reports that very model, installed and
    working, as `qwen2.5vl:latest`. A check asking `model in tags` therefore reports
    `ok: no` on a CORRECT install and sends the user to `pam models pull` for a model
    they already have. `/api/generate` resolves the bare name fine - it is only the tags
    listing that spells the implicit tag out.

    A model the user named WITH a tag is compared as written: `qwen2.5vl:7b` must not be
    satisfied by `qwen2.5vl:latest`, which is a different set of weights.
    """
    wanted = {model} if ":" in model else {model, f"{model}:latest"}
    return any(tag in wanted for tag in tags)


def _unchecked(name: str, where: str) -> Check:
    """A line pam could not answer, said as that rather than guessed either way.

    Reporting `ok` would claim a model is present on a server that never answered, and a
    bare failure would give the user a second thing to fix when there is only one. So it
    is a failure that names the line above it as the thing to fix first.
    """
    return Check(
        name,
        False,
        f"not checked: nothing answered on {where} - fix the line above first",
        required=False,
    )


def _ollama_checks() -> list[Check]:
    """Three lines, reported separately, each with its own `Fix:` route (spec 12, A10).

    NONE OF THEM IS `required`; `Check.required` carries the reasoning and the
    disclosure. The three are genuinely three different faults with three different
    fixes: nothing is listening; something is listening and is not Ollama; Ollama is
    there and the default caption model is not.
    """
    port = DEFAULT_OLLAMA_PORT
    where = f"{_OLLAMA_HOST}:{port}"
    # The port is in EVERY line, not only the failing one (A10): the whole reason doctor
    # names it is that a user who moved `ollama_port` must be able to tell a real absence
    # from doctor asking the wrong port, and that reading is just as necessary when the
    # answer was yes for a server that is not the one their archive uses.
    moved = (
        f"pam doctor always checks the default port {port}; if you set"
        " [enrich.caption] ollama_port, check that port instead"
    )
    try:
        tags = _ollama_tags(port)
    except PamNetworkError as exc:
        return [
            Check(
                "Ollama",
                False,
                f"nothing answered on {where} ({moved}). Needed only for AI captions,"
                " which are off by default. Install it from https://ollama.com and start"
                f" it. [{exc}]",
                required=False,
            ),
            _unchecked("Ollama API", where),
            _unchecked("Ollama model", where),
        ]
    except ValueError as exc:
        return [
            Check("Ollama", True, f"something is listening on {where}", required=False),
            Check(
                "Ollama API",
                False,
                f"{where} answered, but not as Ollama: {exc}. Check that what is"
                f" listening on port {port} is Ollama, and that it is up to date.",
                required=False,
            ),
            _unchecked("Ollama model", where),
        ]
    model = DEFAULT_CAPTION_MODEL
    installed = model_is_installed(model, tags)
    return [
        Check("Ollama", True, f"answering on {where} ({moved})", required=False),
        Check("Ollama API", True, f"GET /api/tags lists {len(tags)} model(s)", required=False),
        Check(
            "Ollama model",
            installed,
            f"{model} is installed"
            if installed
            else f"{model} is not installed on the Ollama at {where}."
            f" Run: pam models pull {model}",
            required=False,
        ),
    ]


# A2. The `[faces]` extra, spelled here as (distribution, import name) for the same reason
# `_RUNTIME_DEPENDENCIES` is: `pip install numpy` is the sentence, `import numpy` is not.
# It is NOT in `_RUNTIME_DEPENDENCIES` and must never be - an optional extra is not part of
# the runtime closure, and `tests/test_deps.py` asserts that in both directions.
_FACES_EXTRA = (
    ("onnxruntime", "onnxruntime"),
    ("numpy", "numpy"),
)

# A22, and it is the whole shape of this check. `pam doctor` HAS NO ARCHIVE - spec 12 gives
# it none, and M10 Task 7 wrote down why: a command that resolves an archive to answer a
# toolchain question fails on a machine with no archive, which is every machine before
# `pam scan`. `[enrich.faces] model_dir` lives in an archive's config, so doctor cannot read
# it and does not pretend to. Pack validation belongs to `pam enrich faces`, which has one.
_FACES_UNCHECKED = (
    "[enrich.faces] model_dir is not checked here - pam doctor has no archive."
    " Run pam enrich faces to validate a model pack, or pam faces manifest <folder>"
    " to write one."
)


def _faces_checks(installed: Provenance) -> list[Check]:
    """One archive-independent line for the optional face stack (A22).

    `required=False`, so it prints `-` and not `x` and does not reach the exit code: a
    machine without the `[faces]` extra is a correctly installed machine, exactly as
    design 12 rev 20's R3 ruled for Ollama. Face detection is opt-in
    (`[enrich.faces] enabled = false`), and reporting an opt-in absence as a broken install
    teaches a user to ignore the mark that matters.

    The check uses `find_spec` and NEVER IMPORTS EITHER PACKAGE. Importing onnxruntime to
    answer a yes/no question would cost `pam doctor` a real model-runtime load, and
    `pam doctor` is what CI runs before every lane.
    """
    missing, versions = _extra_state(_FACES_EXTRA)
    where = _where()
    if missing:
        what, fix = missing_extra(
            installed,
            "the optional [faces] extra is not installed",
            f"Run: {install_method.pip_extra('faces')}.",
        )
        detail = (
            f"{what} ({', '.join(missing)} missing) on {where}."
            f" Needed only for face detection, which is off by default. {fix} "
        )
        return [Check("Face models", False, detail + _FACES_UNCHECKED, required=False)]
    return [
        Check(
            "Face models",
            True,
            f"{', '.join(versions)} on {where}. " + _FACES_UNCHECKED,
            required=False,
        )
    ]


def _extra_state(extra: tuple[tuple[str, str], ...]) -> tuple[list[str], list[str]]:
    """(missing distributions, "name version" of the present ones) - `find_spec` only, never an
    import. Shared by the faces and OCR lines (M12 Task 12), which ask the same question."""
    missing: list[str] = []
    versions: list[str] = []
    for distribution, module in extra:
        try:
            spec = importlib.util.find_spec(module)
        except (ImportError, ValueError):  # a broken or half-removed installation
            spec = None
        if spec is None:
            missing.append(distribution)
            continue
        try:
            versions.append(f"{distribution} {metadata.version(distribution)}")
        except metadata.PackageNotFoundError:
            versions.append(f"{distribution} (unknown version)")
    return missing, versions


def _where() -> str:
    # The platform, because it is the other archive-free thing worth saying: onnxruntime
    # ships per-platform wheels, and "there is no wheel for this interpreter" is the way
    # the extra most commonly fails to install.
    return (
        f"{platform.system().lower()}-{platform.machine()}, Python {platform.python_version()}"
    )


# Design 12 rev 28 (Q4): the optional `[ocr]` extra - EXACTLY the `[faces]` pair (Q1), spelled
# again here for the same reason `_FACES_EXTRA` is spelled: the distribution is the sentence.
_OCR_EXTRA = (
    ("onnxruntime", "onnxruntime"),
    ("numpy", "numpy"),
)

# A22's scope sentence for OCR (Q4): `[enrich.ocr] model_dir` lives in an archive's config, and
# `pam doctor` has no archive. Pack validation belongs to `pam enrich ocr`.
_OCR_UNCHECKED = "[enrich.ocr] model_dir is not checked here - pam doctor has no archive."


def _ocr_checks(installed: Provenance) -> list[Check]:
    """Q4's optional OCR line: two states, A22's scope sentence after each, never `required`.

    `find_spec` only, NEVER an import - `_faces_checks`' reason: a yes/no question must not
    cost `pam doctor` a model-runtime load.
    """
    missing, versions = _extra_state(_OCR_EXTRA)
    if missing:
        what, fix = missing_extra(
            installed,
            "the optional [ocr] extra is not installed",
            f"Run: {install_method.pip_extra('ocr')}.",
        )
        detail = f"{what} ({', '.join(missing)} missing). {fix} "
        return [Check("OCR", False, detail + _OCR_UNCHECKED, required=False)]
    detail = f"{', '.join(versions)} on {_where()}. "
    return [Check("OCR", True, detail + _OCR_UNCHECKED, required=False)]


def pull_message(model: str, port: int = DEFAULT_OLLAMA_PORT) -> str:
    """What `pam models pull` prints BEFORE it blocks (spec 12 rev 17, UX 2).

    A pull is gigabytes and minutes and `LoopbackHttpClient` does not stream, so there is
    no progress bar and there cannot be one without a second HTTP client. UX 2 forbids a
    silent multi-minute wait, so the command says what it is doing, where, how long it
    may take, and who is actually doing the downloading - Ollama, which is the division
    of labour design 1's stack table chose Ollama for.
    """
    return (
        f"Asking the Ollama on {_OLLAMA_HOST}:{port} to fetch {model} ...\n"
        "  Ollama does the download, not pam. A first pull of a vision model is"
        " several gigabytes\n"
        "  and can take many minutes, and pam shows no progress while it waits."
        " Ollama's own\n"
        "  logs have the detail. Nothing leaves this machine from pam itself"
        " (spec 10)."
    )


def pull_model(
    model: str,
    *,
    port: int = DEFAULT_OLLAMA_PORT,
    host: str = _OLLAMA_HOST,
    timeout: float = _PULL_TIMEOUT_S,
    resolver: Resolver = system_resolver,
) -> str:
    """`POST /api/pull` to the local Ollama, and one line back (spec 12 rev 17, A9).

    THIS OPENS NO NON-LOOPBACK SOCKET, and that is the whole point of routing it through
    `LoopbackHttpClient` rather than shelling out to an `ollama` executable: the client
    resolves the host, refuses anything that is not on this machine, and connects to the
    address it checked. Ollama then performs the download. Design 12 rev 17 deleted the
    sentence that called this "the only network egress", because it is not one - pam
    itself opens no non-loopback socket in any command, ever.

    Every failure is a `PamNetworkError`, which spec 13 prints as one line and exits 1.
    """
    client = LoopbackHttpClient(host, port, timeout=timeout, resolver=resolver)
    body = json.dumps({"model": model, "stream": False}).encode("utf-8")
    response = client.request(
        "POST", "/api/pull", body=body, headers={"Content-Type": "application/json"}
    )
    detail = _pull_error(response.body)
    if response.status != 200:
        raise PamNetworkError(
            f"the Ollama on {host}:{port} refused to pull {model!r}:"
            f" HTTP {response.status} {response.reason}{detail}"
        )
    if detail:
        raise PamNetworkError(f"the Ollama on {host}:{port} could not pull {model!r}:{detail}")
    return f"ok {model} is installed. Next: pam doctor"


def _pull_error(body: bytes) -> str:
    """Ollama's own explanation, when it sent one.

    A pull of a model that does not exist answers with an `error` field, and it is the
    only text that says WHICH pull failed and why ("pull model manifest: file does not
    exist"). It arrives on both the HTTP-error and the HTTP-200 paths depending on where
    the pull failed, so it is read once, before the status is looked at, and an
    unparseable body simply contributes nothing rather than raising over a body that was
    never the point.
    """
    try:
        error = json.loads(body).get("error")
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
        return ""
    return f" {error}" if error else ""


def _exiftool_version(exe: str) -> str:
    out = subprocess.run([exe, "-ver"], capture_output=True, text=True, timeout=10, check=True)
    return out.stdout.strip()


def run_checks() -> list[Check]:
    checks: list[Check] = []
    # E13: read once, before any line; every repair sentence below follows it.
    installed = provenance()

    ok = sys.version_info >= (3, 12)
    checks.append(Check("Python", ok, f"{sys.version.split()[0]}"))

    exe = shutil.which("exiftool")
    if exe is None:
        pip = (
            "Download the Windows executable from exiftool.org, "
            "rename it to exiftool.exe, and put its folder on PATH."
        )
        detail = "not found on PATH. " + exiftool_fix(installed, pip)
        checks.append(Check("exiftool", False, detail))
    else:
        try:
            version = _exiftool_version(exe)
            checks.append(Check("exiftool", True, f"{version} at {exe}"))
        except (subprocess.SubprocessError, OSError) as e:
            checks.append(Check("exiftool", False, f"found at {exe} but not runnable: {e}"))

    # After exiftool, before the writability probe: these are toolchain questions and
    # they belong beside the other one, not after a filesystem check.
    checks.extend(_dependency_checks(installed))

    # Ollama last of the toolchain questions, because it is the only optional one: the
    # lines above are things pam needs, and a reader who sees a red line wants the ones
    # that stop the product working nearest the top of the block.
    checks.extend(_ollama_checks())

    # And the face stack after it, for the same reason in the same order: optional things
    # last, so a reader meeting a red line meets the ones that stop the product working
    # first. Neither of these two blocks can fail `pam doctor`.
    checks.extend(_faces_checks(installed))
    # Q4: the second optional model stack, after the first
    checks.extend(_ocr_checks(installed))

    root = app_root()
    probe = (
        root / f".write-probe-{uuid.uuid4().hex}"
    )  # unique name: a crashed run leaves no collision
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        checks.append(Check("App data", True, str(root)))
    except OSError as e:
        checks.append(Check("App data", False, f"{root} is not writable: {e}"))
    finally:
        probe.unlink(missing_ok=True)

    return checks
