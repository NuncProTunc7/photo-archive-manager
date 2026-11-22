"""OD-8's version grammar and order (M15 Task 9), and the one version pam carries.

THE TABLES ARE HAND-WRITTEN FROM OD-8, never derived from `pam.update.version`: `X.Y.Z` stable,
`X.Y.ZrcN` a rehearsal, a strict subset of PEP 440 ordered by PEP 440's rules - so a release
candidate sorts BELOW its release, and every number compares as a NUMBER (`rc10 > rc2`,
`1.10.0 > 1.9.0`), which a string comparison gets wrong in both places.
"""

import tomllib
from pathlib import Path

import pytest

import pam
from pam.errors import PamError
from pam.update import version as version_mod
from pam.update.version import Version, VersionError, parse

REPO_ROOT = Path(__file__).resolve().parent.parent

# OD-8: the first stable version. Spelled here, not read from `pam.__version__`.
OD_8_FIRST_VERSION = "1.0.0"

# Strictly ascending, as OD-8 and PEP 440 order them. Every adjacent pair is a claim, and the
# pairs are chosen where a wrong reading flips them: rc below release, rc number as a number,
# patch then minor as numbers.
ASCENDING = (
    "0.9.9",
    "1.0.0rc1",
    "1.0.0rc2",
    "1.0.0rc10",
    "1.0.0",
    "1.0.1",
    "1.0.2rc1",
    "1.0.2",
    "1.2.0",
    "1.9.0",
    "1.10.0",
    "2.0.0rc1",
    "2.0.0",
    "10.0.0",
)

# Each one is not OD-8's grammar. Hand-written, one shape per line.
GARBAGE = (
    "",
    "1",
    "1.0",
    "1.0.0.0",
    "v1.0.0",
    "1.0.0-rc1",
    "1.0.0.rc1",
    "1.0.0rc",
    "1.0.0rc0",
    "1.0.0rc01",
    "1.0.0a1",
    "1.0.0b1",
    "1.0.0RC1",
    "1.0.0.post1",
    "1.0.0.dev1",
    "1.0.0+local",
    "01.0.0",
    "1.00.0",
    "1.0.00",
    " 1.0.0",
    "1.0.0 ",
    "1.0.0\n",
    "1.-1.0",
    "1.0.x",
    "１.0.0",  # a full-width digit: `\d` would take it
    "1.0.0rc٢",  # an Arabic-Indic digit
)


def test_the_package_version_is_od_8s():
    assert pam.__version__ == OD_8_FIRST_VERSION


def test_pyproject_and_the_package_agree_on_the_version():
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    assert project["version"] == pam.__version__


def test_the_distribution_is_photo_archive_manager_and_the_command_stays_pam():
    """OD-12: the PyPI name `pam` belongs to an unrelated project."""
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)["project"]
    assert project["name"] == "photo-archive-manager"
    # M15 Task 13A-F1: the command is the light entry, which registers before `pam.cli` is imported
    assert project["scripts"] == {"pam": "pam.entry:main"}


def test_the_package_version_parses_under_its_own_grammar():
    assert str(parse(pam.__version__)) == pam.__version__


def test_the_tables_are_not_empty():
    assert len(ASCENDING) >= 10
    assert len(GARBAGE) >= 20


@pytest.mark.parametrize(("lower", "higher"), list(zip(ASCENDING, ASCENDING[1:], strict=False)))
def test_each_version_sorts_below_the_next(lower, higher):
    assert parse(lower) < parse(higher)
    assert parse(higher) > parse(lower)
    assert not parse(higher) < parse(lower)
    assert parse(lower) != parse(higher)


def test_the_whole_table_sorts_into_its_written_order():
    shuffled = list(reversed(ASCENDING))
    assert [str(v) for v in sorted(parse(text) for text in shuffled)] == list(ASCENDING)


@pytest.mark.parametrize("text", ASCENDING)
def test_a_valid_version_round_trips(text):
    assert str(parse(text)) == text
    assert parse(text) == parse(text)


@pytest.mark.parametrize("text", GARBAGE)
def test_garbage_is_refused(text):
    with pytest.raises(VersionError):
        parse(text)


def test_a_non_string_is_refused():
    for value in (None, 1, 1.0, b"1.0.0", ["1.0.0"]):
        with pytest.raises(VersionError):
            parse(value)


def test_the_refusal_is_a_pam_error_naming_the_text():
    with pytest.raises(PamError, match="v1.0.0"):
        parse("v1.0.0")


def test_rc_is_known_and_a_release_is_not():
    assert parse("1.0.0rc2").rc == 2
    assert parse("1.0.0rc2").is_rc
    assert parse("1.0.0").rc is None
    assert not parse("1.0.0").is_rc


def test_the_fields_are_numbers():
    assert parse("12.34.56rc7") == Version(12, 34, 56, 7)
    assert parse("1.10.0") == Version(1, 10, 0, None)


def test_the_module_imports_only_the_standard_library_and_pam():
    """`pam update` and the release tool import this; `EAGER_IMPORT_CLOSURE` stays {click, pam}."""
    import ast

    tree = ast.parse(Path(version_mod.__file__).read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert roots <= {"__future__", "dataclasses", "functools", "re", "pam"}, roots
