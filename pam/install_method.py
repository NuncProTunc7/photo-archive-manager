"""How this pam was installed (M15 Task 11, E13): the installer's record, or pip.

THE ONE PATH RULE. Spike 10A measured the installed layout (`docs/implementation-notes/
m15-spikes/10a/10a-record.md`, "Tree layout"; the 3.14 addendum keeps it):

    {app}\\pam.cmd
    {app}\\python\\              the embeddable interpreter, so sys.prefix == {app}\\python
    {app}\\exiftool\\            the bundled exiftool
    {app}\\pam-install.json      written by the installer (Task 13)

So `{app}` is `sys.prefix`'s PARENT, and only when `sys.prefix` is a folder named `python`
(compared without case, as Windows does). Any other prefix - a venv, a system Python, a CI
runner's toolcache - is not the installed layout, and no record is looked for: a stray
`pam-install.json` beside a venv must never make pam claim an installer. Task 13's build and
Task 12's `pam update` take `{app}` from `app_dir` and the record's name from `RECORD_NAME`, so
the rule is spelled once.

THE RECORD IS EVIDENCE ONLY WHEN IT IS WELL FORMED: a JSON object, at most `MAX_RECORD_BYTES`,
with `method == "installer"`, a string `version` and a list of string `components` (extra keys
are tolerated, so a newer installer's record still reads). Anything else - no file, a file that
cannot be read, garbage, the wrong shape, an unknown method - reads as `PIP`, the method that
writes no record. Nothing here raises.

Standard library only: `pam doctor` imports this, and the eager import closure stays
`{click, pam}`.
"""

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from pam import __version__

RECORD_NAME = "pam-install.json"
INTERPRETER_DIR = "python"
INSTALLER = "installer"
PIP = "pip"
# The installer's one optional component (spike 10A Q4, adopted 2026-11-17: `[faces]` and `[ocr]`
# install the same files, so they are ONE component). Task 13's `.iss` shows this label.
COMPONENT_LABEL = "Face and text reading"
# A record is about a hundred bytes; a file far larger than that is not one, and is not read whole.
MAX_RECORD_BYTES = 64 * 1024


@dataclass(frozen=True)
class Provenance:
    method: str  # INSTALLER or PIP (`provenance`); INSTALLER, NO_RECORD or MALFORMED (`record_state`)
    app: Path | None  # `{app}`, only when `method` is INSTALLER


def app_dir(prefix: str | None = None) -> Path | None:
    """`{app}` for the installed layout - `prefix`'s parent when `prefix` is `{app}\\python` -
    else None. `prefix` defaults to `sys.prefix`, read at call time."""
    interpreter = Path(sys.prefix if prefix is None else prefix)
    if interpreter.name.casefold() != INTERPRETER_DIR:
        return None
    # THE FOLDER'S NAME ALONE IS NOT THE INSTALLED LAYOUT (M15 Task 11 review, F3): a venv, or a
    # system Python, in a folder named `python` with a stray record beside it claimed "installer".
    # Only the installer's EMBEDDABLE interpreter carries a `python3*._pth` (spike 10A), and a
    # venv always carries `pyvenv.cfg`. Both facts are checked; either one failing is not installed.
    if (interpreter / "pyvenv.cfg").exists() or not any(interpreter.glob("python3*._pth")):
        return None
    return interpreter.parent


def provenance(prefix: str | None = None) -> Provenance:
    """INSTALLER with its `{app}` when `{app}\\pam-install.json` is a well-formed installer record;
    otherwise PIP. Never raises. (`record_state`'s three answers, with "malformed" read as pip -
    right for doctor, whose pip advice is harmless; `pam update` needs the three.)"""
    state = record_state(prefix)
    return state if state.method == INSTALLER else Provenance(PIP, None)


# `pam update`'s three answers (M15 Task 12, E2 rev 3). A MALFORMED record is not pip: something
# wrote a file where the installer writes its record, so pam cannot tell how it was installed, and
# `pam update` guesses no command for it.
NO_RECORD = "no record"
MALFORMED = "malformed"


def record_state(prefix: str | None = None) -> Provenance:
    """INSTALLER with its `{app}`; NO_RECORD when this is not the installed layout or no record
    exists there; MALFORMED for a record that exists and is not a well-formed installer record
    (unreadable, over the cap, not UTF-8 JSON, the wrong shape, another method). Never raises."""
    app = app_dir(prefix)
    if app is None:
        return Provenance(NO_RECORD, None)
    try:
        with (app / RECORD_NAME).open("rb") as record:
            raw = record.read(MAX_RECORD_BYTES + 1)
    except FileNotFoundError:
        return Provenance(NO_RECORD, None)
    except OSError:
        return Provenance(MALFORMED, None)
    try:
        if len(raw) > MAX_RECORD_BYTES:
            return Provenance(MALFORMED, None)
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return Provenance(MALFORMED, None)
    if not _is_installer_record(data):
        return Provenance(MALFORMED, None)
    return Provenance(INSTALLER, app)


def _is_installer_record(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    components = data.get("components")
    return (
        data.get("method") == INSTALLER
        and isinstance(data.get("version"), str)
        and isinstance(components, list)
        and all(isinstance(name, str) for name in components)
    )


# --- the advice an installed pam gives (E13; spike 10A F7) -------------------------------------
#
# EVERY INSTALLER SENTENCE `pam doctor` PRINTS IS SPELLED HERE, ONCE. Each function takes the pip
# sentence its caller already prints and returns it unchanged for anything but a well-formed
# installer record - so a pip install, and a malformed or missing record, keep pip's advice.
REPAIR = "Run pam's installer again to repair this installation."
COMPONENT_MISSING = f'the optional "{COMPONENT_LABEL}" component is not installed'
TICK = f'Run pam\'s installer again and tick "{COMPONENT_LABEL}".'


def repair(installed: Provenance, pip: str) -> str:
    """A missing package or dataset: the installer put it there, so the installer repairs it."""
    return REPAIR if installed.method == INSTALLER else pip


def missing_extra(installed: Provenance, pip_what: str, pip_fix: str) -> tuple[str, str]:
    """(what is missing, its fix) for an optional extra: for an installed pam, the installer's
    ONE optional component (`[faces]` and `[ocr]` alike); else the pip extra's two sentences."""
    if installed.method == INSTALLER:
        return COMPONENT_MISSING, TICK
    return pip_what, pip_fix


def exiftool_fix(installed: Provenance, pip: str) -> str:
    """exiftool not on PATH: an installed pam's exiftool is bundled at `{app}\\exiftool`, so it
    names that folder and the repair, never a download."""
    app = installed.app if installed.method == INSTALLER else None
    if app is None:
        return pip
    return f"pam's own copy belongs in {app / 'exiftool'}. {REPAIR}"


# --- where a pip install gets an optional extra (the maintainer's decision 4, 2026-11-21) ---------
#
# pam is NOT on PyPI, so a bare `photo-archive-manager[faces]` would resolve there to whatever owns
# that name - today nothing, tomorrow anyone. The extra comes from the release's own wheel, for the
# version that is running. `pam/update/release.py` builds its pip route from these same constants.
OWNER = "NuncProTunc7"
REPO = "photo-archive-manager"
DISTRIBUTION = "photo-archive-manager"  # OD-12: the pip distribution; the command stays `pam`
DOWNLOAD_HOST = "github.com"
# PEP 427's file-name escape of DISTRIBUTION (PEP 503's normalisation, `-` to `_`): the release's
# wheel is `photo_archive_manager-<version>-py3-none-any.whl` (measured: `pip wheel` of 1.0.0 and of
# 1.0.0rc1, 2026-11-21). The release workflow spells the same name and refuses a build without it.
WHEEL_PREFIX = re.sub(r"[-_.]+", "_", DISTRIBUTION).lower()


def wheel_url(version: str) -> str:
    """The wheel asset of the release `v<version>`, at the one download URL form pam uses."""
    return (
        f"https://{DOWNLOAD_HOST}/{OWNER}/{REPO}/releases/download/v{version}/"
        f"{WHEEL_PREFIX}-{version}-py3-none-any.whl"
    )


def pip_extra(extra: str, version: str = __version__) -> str:
    """The pip command that adds `extra` to this pam, from this version's release wheel."""
    return f'pip install "{DISTRIBUTION}[{extra}] @ {wheel_url(version)}"'
