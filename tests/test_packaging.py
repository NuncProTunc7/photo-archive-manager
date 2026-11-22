"""What the BUILT DISTRIBUTION contains - the one thing the rest of this suite cannot see.

WHY THIS FILE EXISTS (M7 close, finding F1). `pam/webui/server.py` reads spec 8.2's three
static files out of the PACKAGE with `importlib.resources`, and `catalog/db.py` reads the
migrations the same way. Under pytest that resolves to the source tree, where both sets of
files are simply on disk - so every other test in this project passes whether or not the
build system has been told to ship them. Deleting `"webui/static/*"` from
`[tool.setuptools.package-data]` leaves **1856 tests green** and produces a wheel with no
review UI in it at all (mutation M781). The migrations sit on the same line and have been
exposed the same way since M1.

WHY THIS DOES NOT BUILD A WHEEL. Running the build here would be the most direct check and
is deliberately not done: `pip wheel` uses build isolation and reaches for the network for
its build backend, which would put a network fetch inside a suite whose privacy canary
exists to prove pam makes none, and would make a green suite depend on a package index
being up. The guard instead reads `pyproject.toml` with a REAL PARSER (`tomllib`, stdlib -
no new dependency) and asks the question the build asks: for every non-Python file that
actually sits inside `pam/`, is there a pattern that would carry it?

WHY THE EXPECTATION COMES FROM THE SOURCE TREE AND NOT FROM THE PATTERN LIST. An oracle
built from the definition it checks is a survivor waiting to happen - this project has
learned that about a query (`repo.open_plan`) and about a constant (`repo.ENRICHERS`).
Asserting that the patterns match the patterns would pass for any value of the patterns.
So the file set is WALKED, asserted non-empty first (M528's lesson - an empty input
disarms the check silently), and each member must be claimed by some pattern. Add a static
file, a migration, or any other data file to `pam/` without a `package-data` entry and this
fails.
"""

import fnmatch
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "pam"

# Named from the SPECS, not read back from `pyproject.toml` or from the directory - these
# are the two data sets pam cannot run without. Spec 8.2's page is the whole M7
# deliverable; spec 4's migrations are what creates a catalog at all.
#
# M11 TASK 10 STEP 2 ADDS UX SPEC 6'S LABELING PAGE. `pyproject.toml`'s `webui/static/*`
# glob already carries any new file in that directory - **and that is exactly why the two
# new files need naming here**. A glob is not a guarantee that the file is present: F1/M781
# was a wheel built with the glob DELETED and 1856 tests green, and the symmetrical defect
# is a page whose script was never written at all. Naming them makes both red.
REQUIRED_DATA_FILES = (
    "webui/static/index.html",
    # M14 Task 1: the review page's five classic scripts (design spec 2's tree).
    "webui/static/pam-core.js",
    "webui/static/pam-trust.js",
    "webui/static/pam-keys.js",
    "webui/static/pam-row.js",
    "webui/static/pam-selection.js",
    "webui/static/pam-list.js",
    "webui/static/pam-bar.js",
    "webui/static/pam-detail.js",
    "webui/static/pam-chooser.js",
    "webui/static/pam-reading.js",
    "webui/static/pam-welcome.js",
    "webui/static/pam-confirm.js",
    "webui/static/pam-done.js",
    "webui/static/pam-stopped.js",
    "webui/static/pam-history.js",
    "webui/static/app.js",
    "webui/static/style.css",
    "webui/static/faces.html",
    "webui/static/faces.js",
    "catalog/migrations/0001_init.sql",
)

# The floor is COUNTED FROM THE SPECS, not from the tuple above: spec 8.2's review page is
# three files, UX spec 6's labeling page adds two, and spec 4 needs at least one migration.
# Deriving it (`>= len(REQUIRED_DATA_FILES)`) is what mutation M793 exposed - emptying the
# tuple made the floor `0 >= 0` while pytest SKIPPED the parametrized half rather than
# failing it (M743, third occurrence), so both halves of this guard vanished together and
# the suite read 1860 passed / 2 skipped, green.
#
# M14 Task 1: design spec 2's `static/` tree names nine files (two pages, five review scripts,
# the faces script, the stylesheet) and spec 4 at least one migration - ten. M14 Task 2 adds
# `pam-row.js` and `pam-list.js`, and Step 6a `pam-detail.js` - thirteen. Step 6b retired the
# card page, `pam-review.js` (design spec 2's tree no longer names it) - twelve. M14 Task 3 adds
# `pam-selection.js` and `pam-bar.js` (U3, U17) - fourteen. M14 Task 4 adds `pam-welcome.js`
# (W18, U13) - fifteen. M14 Task 5a cuts the folder chooser (U13) into `pam-chooser.js` - sixteen.
# M14 Task 5e writes Welcome's Reading step (U11) in `pam-reading.js` - seventeen. M14 Task 6c
# adds the Confirm screen and Done (U6, W19-W21), `pam-confirm.js` and `pam-done.js` - nineteen.
# M14 Task 7 adds History (W22, X2), `pam-history.js` - twenty. M15 Task 5b cuts a rename that did
# not run or stopped part-way (U12, C6.2) out of `pam-done.js` into `pam-stopped.js` - twenty-one.
MINIMUM_DATA_FILES = 21


def _package_data_patterns() -> list[str]:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)
    package_data = config["tool"]["setuptools"]["package-data"]
    return list(package_data["pam"])


def _shipped_data_files() -> list[str]:
    """Every non-Python file under `pam/`, as a POSIX path relative to the package."""
    found = []
    for path in sorted(PACKAGE_ROOT.rglob("*")):
        if not path.is_file() or path.suffix == ".py":
            continue
        if "__pycache__" in path.parts:
            continue
        found.append(path.relative_to(PACKAGE_ROOT).as_posix())
    return found


def _covered(relative_path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(relative_path, pattern) for pattern in patterns)


def test_the_required_file_list_is_not_empty():
    """M743's shape, unparametrized so it cannot be deleted by what it protects.

    `test_the_files_pam_cannot_run_without_are_present_and_packaged` is parametrized over
    `REQUIRED_DATA_FILES`, and pytest SKIPS an empty parametrize instead of failing it. This
    test is the one that cannot disappear with the list.
    """
    assert REQUIRED_DATA_FILES, (
        "REQUIRED_DATA_FILES is empty, so the parametrized guard below is SKIPPED rather "
        "than failed and nothing here names a file pam cannot run without"
    )
    # M14 Task 4 review R2 (M3061): the floor counted from the specs binds THIS tuple too.
    # Asserted only on the discovered files (twenty and more, with the migrations), it let an
    # entry - `pam-welcome.js` - leave the tuple with the suite green.
    assert len(REQUIRED_DATA_FILES) >= MINIMUM_DATA_FILES, REQUIRED_DATA_FILES


def test_there_are_data_files_to_protect():
    # M528's lesson, applied to this file's own input: if the walk ever finds nothing,
    # every assertion below passes vacuously and the guard is disarmed without a failure.
    found = _shipped_data_files()
    assert len(found) >= MINIMUM_DATA_FILES, found


@pytest.mark.parametrize("relative_path", REQUIRED_DATA_FILES)
def test_the_files_pam_cannot_run_without_are_present_and_packaged(relative_path):
    # Two claims, deliberately in one test: the file exists in the source tree AND a
    # `package-data` pattern would carry it into a distribution. Either alone is the
    # half-truth that let F1 stand.
    assert (PACKAGE_ROOT / relative_path).is_file(), f"{relative_path} is missing from pam/"
    patterns = _package_data_patterns()
    assert _covered(relative_path, patterns), (
        f"{relative_path} is in pam/ but no [tool.setuptools.package-data] pattern in "
        f"pyproject.toml matches it, so a built wheel would not contain it. Patterns: "
        f"{patterns}"
    )


def test_every_non_python_file_in_the_package_would_be_shipped():
    """The open-ended half: a data file added later must not be silently left behind."""
    patterns = _package_data_patterns()
    orphans = [name for name in _shipped_data_files() if not _covered(name, patterns)]
    assert orphans == [], (
        "these files live inside pam/ but no [tool.setuptools.package-data] pattern "
        f"carries them into a distribution: {orphans}"
    )


# --- M15 Task 13A-F1: the console script is the light entry ------------------------------------
# E11's registration happens in `pam.entry.main`, BEFORE `pam.cli` is imported. A console script
# pointing back at `pam.cli:main` still registers (there, after every import) and passes every
# in-process test - it only reopens the import window - so the wheel's entry point is pinned here,
# from the brief, with a real parser; and the INSTALLED script is read too, because the artefact,
# not the source tree, is what a user runs.

LIGHT_ENTRY = "pam.entry:main"


def test_the_console_script_is_the_light_entry_not_pam_cli():
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)
    assert config["project"]["scripts"] == {"pam": LIGHT_ENTRY}


def test_the_installed_console_script_is_the_light_entry_too():
    """An editable install keeps the entry point it was installed with: after changing
    `[project.scripts]`, re-run `pip install -e ".[dev]" --no-deps`.

    Read from THIS distribution's metadata by name (OD-12), not from every console script on the
    path: a stale ignored `pam.egg-info` from before the rename sits in the repository root of a
    developer machine and is on `sys.path` whenever the working directory is."""
    from importlib.metadata import distribution

    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        name = tomllib.load(handle)["project"]["name"]
    installed = [
        ep.value
        for ep in distribution(name).entry_points
        if ep.group == "console_scripts" and ep.name == "pam"
    ]
    assert installed == [LIGHT_ENTRY]
