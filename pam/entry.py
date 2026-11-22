r"""The console entry point: E11's registration BEFORE `pam.cli` is imported (M15 Task 13A-F1).

`[project.scripts] pam` and the installed launcher call `main` here, never `pam.cli.main` directly.
Importing `pam.cli` imports click and the whole command tree - measured at the 13A review, a median
of 522 ms from spawn to a visible run file. For all of that time a setup that took `setup.lock` and
enumerated the run files saw no pam (10 of 10), and when it replaced a module the pam had not yet
imported, the pam died with a `ModuleNotFoundError` traceback in place of the refusal sentence.

So this module imports only the standard library, `pam.run_registry` and `pam.errors` (which import
nothing outside the standard library) at module scope. It REGISTERS, then CHECKS for setup - the
order is the protocol (Dekker, design 3) - and only once both have passed does it import `pam.cli`.
The residual window is interpreter start plus this module's own imports; it cannot reach zero, so
the installer still treats a file in use as a refusal or a rollback, never a forced replacement.

`pam.cli.main` still enters `run_registry.registered()`: reached through here that is the
ref-counted reuse of THIS registration (one run file per process) and a second look for setup;
reached directly (the in-process suite) it is the registration. A refusal here prints the line
`pam.cli.main` prints for a `PamError` (`x <sentence>` on stderr), without click, and exits 1.
"""

import sys
from pathlib import Path

from pam import run_registry
from pam.errors import EXIT_EXPECTED, RunRegistryError


def _refuse(message: object) -> int:
    print(f"x {message}", file=sys.stderr)
    return EXIT_EXPECTED


# A file of pam's own payload that is missing or quarantined (M15 Task 13B Stage 2, O-1) fails the
# import of `pam.cli`; a traceback tells the user nothing they can act on. The installer repairs
# what it put there; any other install is reinstalled. Spelled here, read by the owner at the close.
DAMAGED = "pam cannot start: one of its files is missing or blocked ({name})."
DAMAGED_INSTALLER = 'Open the Start menu and run "Repair pam".'
DAMAGED_PIP = "Reinstall pam, then try again."


def _damaged(error: ImportError) -> str:
    from pam import install_method  # standard library only; read only on this path

    installed = install_method.provenance().method == install_method.INSTALLER
    advice = DAMAGED_INSTALLER if installed else DAMAGED_PIP
    return f"{DAMAGED.format(name=error.name or error)} {advice}"


def main(argv: list[str] | None = None) -> int:
    """Register, check for setup, then - and only then - import and run `pam.cli.main`."""
    try:
        path: Path | None = run_registry.register()
    except RunRegistryError as error:
        return _refuse(error)  # fail closed: never run unregistered
    try:
        try:
            setup = run_registry.setup_running()
        except RunRegistryError as error:
            return _refuse(error)  # cannot tell is refused, never read as "no setup"
        if setup:
            return _refuse(run_registry.SETUP_RUNNING)
        try:
            from pam import cli  # deliberately after registration: the import window
        except ImportError as error:
            return _refuse(_damaged(error))
        return cli.main(argv)
    finally:
        run_registry.release(path)
