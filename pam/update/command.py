"""`pam update [--check] [--channel stable|rc]` (M15 Task 12; E2, OD-2, OD-11, OD-12).

THE ONLY PAM COMMAND THAT REACHES A NON-LOOPBACK HOST, and only through `pam/update/fetch.py`.
`pam.cli` imports this module inside the `update` command's body and nowhere else, so no archive
command ever loads it (E4; `tests/test_update_isolation.py`).

THE ORDER IS THE CONTRACT (each step's refusal happens before the next step's effect):
  1. how pam was installed: not Windows, or no installer record -> OD-12's pip route, printed,
     exit 0; a MALFORMED record -> E2's sentence, exit 1, no guessed command. No network.
  2. another pam running (E11's registry) -> refused, exit 1. A registry pam cannot read refuses
     too (`RunRegistryError` propagates - an unknown state is never "none running"). Then the
     installer downloads earlier updates left in TEMP are removed (`pam/update/leftovers.py`,
     Task 12-G), with one line naming how many when there were any.
  3. discover the channel's release (one rate-charged request), bind its assets, fetch and
     validate its manifest (E5, Task 9).
  4. not newer than this pam -> "pam is up to date (X).", exit 0.
  5. its newest migration below the compatibility ledger's (E12) -> refused, exit 1.
  6. name both versions and every migration it ships that this pam does not. `--check` ends here.
  7. the typed word `update`; anything else -> "Nothing was changed.", exit 0.
  8. download into `tempfile.mkdtemp(prefix="pam-update-")` (never a path from remote text), check
     the size, then the SHA-256; ON ANY FAILURE OR CANCELLATION THE DIRECTORY IS DELETED.
  9. start the installer detached, wait (still registered) until setup holds its lock (E11's
     A4), and exit.
Every effect is a parameter, so tests can watch the dangerous call itself (CLAUDE.md, M697).
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

import click

from pam import __version__, install_method, run_registry
from pam.catalog import ledger
from pam.catalog.db import shipped_migrations
from pam.errors import EXIT_OK, PamError
from pam.update import manifest as manifest_module
from pam.update.fetch import (
    METADATA_DEADLINE_S,
    FetchError,
    RateLimitedError,
    StatusError,
    Transport,
    download_deadline_s,
)
from pam.update.leftovers import REAPED, reap_leftovers
from pam.update.manifest import Manifest, ManifestError
from pam.update.release import (
    ASSET_HOST,
    BYTES_ACCEPT,
    DISCOVERY_CAPS,
    DISCOVERY_URLS,
    JSON_ACCEPT,
    MANIFEST_CAP,
    PIP_ROUTE,
    PUBLIC_RELEASES_URL,
    STABLE,
    Release,
    ReleaseError,
    offer,
)
from pam.update.version import Version
from pam.update.version import parse as parse_version

# --- the sentences (E2 rev 2: every outcome named) ----------------------------------------------
PIP_LINES = (
    "This pam was not installed by pam's installer, so pam update does not replace it."
    " Update it with pip:",
    f"  {PIP_ROUTE}",
    "Put the version you want in place of X.Y.Z, both times. The releases are listed at"
    f" {PUBLIC_RELEASES_URL}",
)
MALFORMED = f"pam cannot tell how it was installed. Reinstall it from {PUBLIC_RELEASES_URL}."
ANOTHER_PAM = (
    "pam cannot update now: another pam is running. Close it (and stop pam start), then run"
    " pam update again."
)
UP_TO_DATE = "pam is up to date ({installed})."
DOWNGRADE = "pam update will not install pam {offered}: " + ledger.NEEDS_NEWER
NOTHING_CHANGED = "Nothing was changed."
CONFIRM_WORD = "update"
CONFIRM_PROMPT = "Type update to download and install it, or anything else to cancel: "
# Inno Setup's switches, spelled ONCE for Task 13B to pin: Restart Manager stays off both ways,
# so setup refuses while pam runs and never closes pam to replace it (plan Task 13, E11).
INSTALLER_FLAGS = ("/NOCLOSEAPPLICATIONS", "/NORESTARTAPPLICATIONS")
# 10C's state machine: setup waits up to 30 s for THIS pid's run file (it is still registered
# while it hands over, A4) and gives any other held pid A3's 2 s grace. The pid is the
# interpreter's own (10C M5: a venv's python.exe is a launcher; os.getpid() is never the launcher).
UPDATER_PID_FLAG = "/UPDATERPID={pid}"
# DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, spelled as numbers: `subprocess` names them only on
# Windows, and this module is imported (never run to this point) elsewhere too.
DETACHED_FLAGS = 0x00000008 | 0x00000200


class UpdateError(PamError):
    """A refusal of `pam update`. Exit 1; the sentence says what happened and what to do."""


class Launched(Protocol):
    def poll(self) -> int | None: ...


def confirm_update() -> bool:
    """The typed confirmation: True only for exactly `update`. A closed stdin cancels."""
    click.echo(CONFIRM_PROMPT, nl=False)
    try:
        typed = input()
    except EOFError:
        click.echo()
        return False
    return typed == CONFIRM_WORD


def launch_installer(path: Path) -> Launched:
    """The installer, detached, with Restart Manager off and this pam named as the updater.
    Never through a shell."""
    return subprocess.Popen(
        [str(path), *INSTALLER_FLAGS, UPDATER_PID_FLAG.format(pid=os.getpid())],
        creationflags=DETACHED_FLAGS,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def make_staging() -> Path:
    return Path(tempfile.mkdtemp(prefix="pam-update-"))


def run_update(
    *,
    check: bool,
    channel: str = STABLE,
    transport: Transport | None = None,
    confirm: Callable[[], bool] = confirm_update,
    launch: Callable[[Path], Launched] = launch_installer,
    staging: Callable[[], Path] = make_staging,
    await_setup: Callable[..., bool] = run_registry.await_setup,
    another_pam_running: Callable[[], bool] = run_registry.another_pam_running,
    reap: Callable[[], int] = reap_leftovers,
    install_state: Callable[[], install_method.Provenance] = install_method.record_state,
    ledger_level: Callable[[], str | None] = ledger.read_level,
    platform: str | None = None,
    installed: str = __version__,
    shipped: Sequence[str] | None = None,
    echo: Callable[[str], None] = click.echo,
) -> int:
    """Steps 1-9 above. The exit code for an outcome; a refusal raises `UpdateError`."""
    state = install_state()
    if (platform or sys.platform) == "win32" and state.method == install_method.MALFORMED:
        raise UpdateError(MALFORMED)
    if (platform or sys.platform) != "win32" or state.method != install_method.INSTALLER:
        for line in PIP_LINES:
            echo(line)
        return EXIT_OK
    if another_pam_running():
        raise UpdateError(f"{ANOTHER_PAM} {NOTHING_CHANGED}")
    removed = reap()
    if removed:
        echo(REAPED.format(count=removed))
    transport = transport or Transport()
    release, manifest = _discover(transport, channel)
    current = parse_version(installed)
    if not manifest.version > current:
        echo(UP_TO_DATE.format(installed=current))
        return EXIT_OK
    level = ledger_level()
    if level is not None and manifest.schema < level:
        raise UpdateError(DOWNGRADE.format(offered=manifest.version, path=ledger.ledger_path()))
    known = set(shipped_migrations() if shipped is None else shipped)
    for line in offer_lines(
        current, manifest, [m for m in manifest.migrations if m not in known]
    ):
        echo(line)
    if check:
        echo(f"Next: pam update{'' if channel == STABLE else ' --channel ' + channel}")
        return EXIT_OK
    if not confirm():
        echo(NOTHING_CHANGED)
        return EXIT_OK
    # LOOKED AT AGAIN after the typed confirmation (M15 Task 12 Stage 2, F2): the prompt is a wait
    # of human length, and a pam started during it would make setup refuse after a whole download.
    if another_pam_running():
        raise UpdateError(f"{ANOTHER_PAM} {NOTHING_CHANGED}")
    installer = _download(transport, release, manifest, staging(), echo)
    try:
        child = launch(installer)
    except OSError as error:
        shutil.rmtree(installer.parent, ignore_errors=True)
        raise UpdateError(
            f"pam could not start the installer ({error}), so it deleted the download."
            f" {NOTHING_CHANGED}"
        ) from None
    echo("The installer is starting. pam closes now; the installer finishes the update.")
    await_setup(child_exited=lambda: child.poll() is not None)
    return EXIT_OK


def offer_lines(installed: Version, manifest: Manifest, new: Sequence[str]) -> list[str]:
    """Step 6: both versions, and every catalog update the offered release makes."""
    lines = [
        f"Installed: pam {installed}",
        f"Offered:   pam {manifest.version} ({manifest.channel})",
    ]
    if not new:
        lines.append("It makes no change to your catalogs.")
        return lines
    lines.append(f"It updates each catalog it opens ({', '.join(new)}).")
    lines.append(f"After that, pam {installed} cannot open those catalogs.")
    return lines


def _discover(transport: Transport, channel: str) -> tuple[Release, Manifest]:
    """Step 3: the offered release and its validated manifest (two requests, one rate-charged)."""
    try:
        found = transport.get(
            DISCOVERY_URLS[channel],
            accept=JSON_ACCEPT,
            max_bytes=DISCOVERY_CAPS[channel],
            deadline_s=METADATA_DEADLINE_S,
        )
        release = offer(found.body, channel)
        text = transport.get(
            release.manifest_url,
            accept=BYTES_ACCEPT,
            max_bytes=MANIFEST_CAP,
            deadline_s=METADATA_DEADLINE_S,
            redirect_to=ASSET_HOST,
        )
    except RateLimitedError as error:
        raise UpdateError(_rate_limited(error.reset)) from None
    except StatusError as error:
        raise UpdateError(_status(error)) from None
    except FetchError as error:
        raise UpdateError(f"{error} {NOTHING_CHANGED}") from None
    except ReleaseError as error:
        raise UpdateError(f"{error} {NOTHING_CHANGED}") from None
    try:
        manifest = manifest_module.parse(text.body, tag=release.tag)
    except ManifestError as error:
        raise UpdateError(f"{error} {NOTHING_CHANGED}") from None
    if manifest.size != release.installer_size:
        raise UpdateError(
            f"The release {release.tag} names an installer of {manifest.size} bytes in its"
            f" manifest and {release.installer_size} on GitHub, so pam will not install it."
            f" {NOTHING_CHANGED}"
        )
    return release, manifest


def _download(
    transport: Transport,
    release: Release,
    manifest: Manifest,
    folder: Path,
    echo: Callable[[str], None],
) -> Path:
    """Step 8: the installer in `folder`, size and SHA-256 checked; `folder` deleted on failure."""
    target = folder / manifest.installer
    digest = hashlib.sha256()
    try:
        echo(f"Downloading {manifest.installer} ({manifest.size:,} bytes) ...")
        with target.open("xb") as out:

            def keep(chunk: bytes) -> None:
                digest.update(chunk)
                out.write(chunk)

            got = transport.get(
                release.installer_url,
                accept=BYTES_ACCEPT,
                max_bytes=manifest.size,
                deadline_s=download_deadline_s(manifest.size),
                redirect_to=ASSET_HOST,
                sink=keep,
            )
        if got.size != manifest.size:
            raise UpdateError(
                f"The download is {got.size} bytes, not the {manifest.size} the release manifest"
                " names, so pam deleted it."
            )
        if digest.hexdigest() != manifest.sha256:
            raise UpdateError(
                "The download does not match the release manifest's SHA-256 (the release may"
                " have changed while pam read it), so pam deleted it."
            )
    except BaseException as error:
        shutil.rmtree(folder, ignore_errors=True)
        if isinstance(error, UpdateError):
            raise UpdateError(f"{error} {NOTHING_CHANGED}") from None
        if isinstance(error, FetchError):
            raise UpdateError(
                f"{error} pam deleted the partial download. {NOTHING_CHANGED}"
            ) from None
        raise
    echo("Checked: the size and SHA-256 match the release manifest.")
    return target


def _rate_limited(reset: int | None) -> str:
    when = (
        "in an hour"
        if reset is None
        else "after " + time.strftime("%H:%M", time.localtime(reset))
    )
    return (
        "GitHub allows 60 release checks an hour from this PC, and they are used up."
        f" Try again {when}. {NOTHING_CHANGED}"
    )


def _status(error: StatusError) -> str:
    if error.status == 404:
        return f"GitHub has no pam release to offer. {NOTHING_CHANGED}"
    return f"GitHub answered HTTP {error.status}; try again later. {NOTHING_CHANGED}"
