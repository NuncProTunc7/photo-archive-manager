"""E4 (M15 Task 12): no command that touches an archive imports `pam.update`.

Three guardians, each of which the others cannot stand in for:

1. an AST walk of every module outside `pam/update/`: the only import of the package is the one
   inside `pam/cli.py`'s `update` function (`tests/guards.py::ALLOWED_UPDATE_IMPORTS`);
2. the WIRING: click's `update` command runs that very function (an import is not a wiring);
3. a fresh interpreter: `import pam.cli` loads no `pam.update` module at all.

The fourth, behavioural, guardian is in `tests/test_net_canary.py`: every headless archive command
run through `pam.cli.main` in a fresh process, with a socket watch.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from tests.guards import ALLOWED_UPDATE_IMPORTS, update_import_sites


def _sources():
    import pam

    root = Path(pam.__file__).parent
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith("update/"):
            continue
        # The importing module's package (for `__init__.py`, the package itself).
        package = ".".join(["pam", *path.relative_to(root).parent.parts])
        yield rel, package, path.read_text(encoding="utf-8")


# Spelled from E4, by hand - not read from ALLOWED_UPDATE_IMPORTS.
SPEC_UPDATE_IMPORTERS = [("cli.py", "update")]


def test_the_allowlist_is_the_one_e4_names():
    assert ALLOWED_UPDATE_IMPORTS == SPEC_UPDATE_IMPORTERS


def test_only_the_update_command_imports_the_release_client():
    """Every import of `pam.update` outside the package, keyed by (module, enclosing function)."""
    found = [
        (rel, where)
        for rel, package, source in _sources()
        for _dotted, where in update_import_sites(source, package)
    ]
    assert found == ALLOWED_UPDATE_IMPORTS


def test_the_walk_reaches_cli_py_and_the_whole_package():
    """The walk's REACH: it reads `cli.py` (where the one import is) and dozens of modules."""
    names = [rel for rel, _package, _source in _sources()]
    assert "cli.py" in names and "doctor.py" in names and "webui/server.py" in names
    assert len(names) > 60
    assert not any(name.startswith("update/") for name in names)


# An INDEPENDENT list of planted routes in, each paired with the site the guard must report. Not
# parametrized over ALLOWED_UPDATE_IMPORTS (a control built from the expectation vanishes with it).
PLANTED = [
    ("import pam.update.command\n", "pam", [("pam.update.command", "<module>")]),
    ("import pam.update\n", "pam", [("pam.update", "<module>")]),
    (
        "def scan():\n    from pam.update.fetch import Transport\n",
        "pam",
        [("pam.update.fetch", "scan")],
    ),
    ("from pam import update\n", "pam", [("pam.update", "<module>")]),
    ("def apply():\n    from .update import command\n", "pam", [("pam.update", "apply")]),
    ("from ..update import fetch\n", "pam.webui", [("pam.update", "<module>")]),
    ("from .. import update\n", "pam.webui", [("pam.update", "<module>")]),
    ('x = __import__("pam.update.command")\n', "pam", [("pam.update.command", "<module>")]),
    (
        'import importlib\nx = importlib.import_module("pam.update.fetch")\n',
        "pam",
        [("pam.update.fetch", "<module>")],
    ),
    (
        "class C:\n    def m(self):\n        import pam.update.release\n",
        "pam",
        [("pam.update.release", "C.m")],
    ),
]
assert PLANTED, "the planted controls must not be empty: pytest SKIPS an empty parametrize"


@pytest.mark.parametrize(("source", "package", "expected"), PLANTED)
def test_the_guard_sees_every_route_in(source, package, expected):
    assert update_import_sites(source, package) == expected


@pytest.mark.parametrize(
    "source",
    [
        "import pam.updater\n",
        "from pam import update_check\n",
        "import pam.webui.update\n",
        'x = "pam.update"\n',
    ],
)
def test_the_guard_does_not_report_a_name_that_only_looks_alike(source):
    assert update_import_sites(source, "pam") == []


def test_the_update_command_is_the_function_that_imports_the_client():
    """AN IMPORT IS NOT A WIRING: click's `update` command must run `pam.cli.update` - the function
    the allowlist names - and that function must reach `pam.update.command`."""
    from pam import cli

    callback = cli.cli.commands["update"].callback
    code = getattr(callback, "__wrapped__", callback).__code__
    assert callback.__module__ == "pam.cli"
    assert callback.__qualname__ == "update"
    assert "run_update" in code.co_names or "run_update" in code.co_varnames


def test_importing_the_cli_loads_no_release_client():
    """A FRESH interpreter: this process imported `pam.update` long ago."""
    probe = (
        "import sys, pam.cli\n"
        "print(sorted(m for m in sys.modules if m == 'pam.update' or m.startswith('pam.update.')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "[]"


def test_the_fresh_interpreter_probe_sees_a_loaded_client():
    """The probe's control: the same reading after importing the client DOES list it."""
    probe = (
        "import sys, pam.cli, pam.update.command\n"
        "print(sorted(m for m in sys.modules if m == 'pam.update' or m.startswith('pam.update.')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert "'pam.update.command'" in result.stdout and "'pam.update.fetch'" in result.stdout
