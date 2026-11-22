"""`tests/PUBLIC-COVERAGE.md` and the public census (M15 Task 14).

The public repository carries `tests/` minus `tests/private/`. Two things must stay true of it:

- every product safety control (guard, canary, fault-matrix cell, construction) still has a public
  test, and `PUBLIC-COVERAGE.md` names each one by node id. This module COLLECTS those node ids with
  a real pytest run, so a name that no longer collects fails here, not in a reader's head;
- the census the public tree runs (`census.public_census()`) is non-empty, names only modules the
  export carries, and names no module that lives in `tests/private/`.

The document's floors (its required headings and a minimum count) are spelled here from the plan,
never read back from the document, so deleting a section from the document cannot pass.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parent
sys.path.insert(0, str(REPO / "tools"))

import census  # noqa: E402
import export_public  # noqa: E402

COVERAGE = TESTS / "PUBLIC-COVERAGE.md"
NODE = re.compile(r"^- `(tests/[\w/]+\.py)::(\w+)`$", re.MULTILINE)
# The plan's four kinds of product safety control, plus the installer's source guards the
# rulings put in the public suite. Spelled by hand.
REQUIRED_HEADINGS = (
    "## Guards",
    "## Privacy canary",
    "## Fault-injection matrix",
    "## Safety constructions",
    "## Installer source guards",
)
REQUIRED_MODULES = (
    "tests/test_net_canary.py",
    "tests/test_faults_matrix.py",
    "tests/test_m9_safety_constructions.py",
    "tests/test_m11_safety_constructions.py",
    "tests/test_m12_safety_constructions.py",
    "tests/test_m13_safety_constructions.py",
    "tests/test_m14_safety_constructions.py",
    "tests/test_installer_source.py",
    "tests/test_rename.py",
    "tests/test_net.py",
    "tests/test_repo.py",
    "tests/test_update_isolation.py",
    "tests/test_webui_server.py",
)
NODE_FLOOR = 180


def named_nodes() -> list[tuple[str, str]]:
    return NODE.findall(COVERAGE.read_text(encoding="utf-8"))


def test_the_coverage_document_keeps_its_headings_and_its_floor():
    text = COVERAGE.read_text(encoding="utf-8")
    missing = [heading for heading in REQUIRED_HEADINGS if f"\n{heading}\n" not in text]
    assert missing == []
    nodes = named_nodes()
    assert len(nodes) >= NODE_FLOOR, f"only {len(nodes)} node ids, floor {NODE_FLOOR}"
    modules = {module for module, _ in nodes}
    assert sorted(set(REQUIRED_MODULES) - modules) == []


def test_every_node_id_the_coverage_document_names_is_collected():
    nodes = named_nodes()
    modules = sorted({module for module, _ in nodes})
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         *modules],
        cwd=REPO, capture_output=True, text=True, timeout=300,
    )  # fmt: skip
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
    collected = {line.split("[", 1)[0] for line in done.stdout.splitlines() if "::" in line}
    absent = sorted(f"{m}::{t}" for m, t in nodes if f"{m}::{t}" not in collected)
    assert absent == []


def test_every_module_the_coverage_document_names_is_public():
    for module, _ in named_nodes():
        assert export_public.selected(module), module
        assert not module.startswith("tests/private/"), module


# --- the public census ---------------------------------------------------------------------------


def test_the_public_census_is_non_empty_and_meets_its_own_floors():
    public = census.public_census()
    assert len(public) >= census.PUBLIC_CENSUS_MODULES_FLOOR > 0
    assert sum(len(c) for c in public.values()) >= census.PUBLIC_CENSUS_CONTROLS_FLOOR > 0
    assert len(census.public_registries()) >= census.PUBLIC_REGISTRIES_FLOOR > 0


def test_the_public_census_names_only_modules_the_export_carries_and_none_from_private():
    leaked = []
    for module in census.public_census():
        if (
            not export_public.selected(f"tests/{module}")
            or (TESTS / "private" / module).exists()
        ):
            leaked.append(module)
        elif not (TESTS / module).is_file():
            leaked.append(module)
    assert leaked == []


def test_every_module_under_tests_private_is_keyed_as_private_in_the_census():
    private_dir = TESTS / "private"
    if not private_dir.is_dir():
        pytest.skip("the public repository has no tests/private")
    unkeyed = [
        p.name
        for p in sorted(private_dir.glob("test_*.py"))
        if p.name in census.CENSUS or p.name in census.public_census()
    ]
    assert unkeyed == []


def test_the_public_census_is_exactly_the_census_minus_its_private_keys():
    # An independent second reading of the split: the prefix, spelled here.
    expected = {k: v for k, v in census.CENSUS.items() if not k.startswith("private/")}
    assert census.public_census() == expected
    assert all(not k.startswith("private/") for k in census.public_registries())


def test_the_public_census_passes_the_census_check_on_this_tree():
    errors = census.census_errors(
        TESTS,
        census.public_census(),
        census.public_registries(),
        floors=(
            census.PUBLIC_CENSUS_MODULES_FLOOR,
            census.PUBLIC_CENSUS_CONTROLS_FLOOR,
            census.PUBLIC_REGISTRIES_FLOOR,
        ),
        include_private=False,
    )
    assert errors == []


def test_a_private_module_left_in_the_public_census_fails_the_public_check(tmp_path):
    """The planted case: a module that lives only in tests/private, keyed as if it were public."""
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "test_doc.py").write_text(
        "def test_reads_a_private_doc():\n    pass\n"
    )
    (tmp_path / "test_pub.py").write_text("def test_public():\n    pass\n")
    planted = {"test_pub.py": frozenset({"test_public"}),
               "test_doc.py": frozenset({"test_reads_a_private_doc"})}  # fmt: skip
    errors = census.census_errors(tmp_path, planted, {"x": ()}, floors=None)
    assert errors == ["test_doc.py is GONE, and it held ['test_reads_a_private_doc']"]


def test_the_census_the_hook_checks_is_the_public_view_exactly_where_tests_private_is_absent(
    tmp_path,
):
    """`conftest.py` asks `census.active_census` which census to check. Without tests/private
    (the export) it must be the public view and its floors; with it (here), the whole census."""
    public, public_registries, public_floors = census.active_census(tmp_path)
    assert public == census.public_census()
    assert public_registries == census.public_registries()
    assert public_floors == (
        census.PUBLIC_CENSUS_MODULES_FLOOR,
        census.PUBLIC_CENSUS_CONTROLS_FLOOR,
        census.PUBLIC_REGISTRIES_FLOOR,
    )
    (tmp_path / "private").mkdir()
    whole, registries, floors = census.active_census(tmp_path)
    assert (whole, registries) == (census.CENSUS, census.REGISTRIES)
    assert floors == (
        census.CENSUS_MODULES_FLOOR,
        census.CENSUS_CONTROLS_FLOOR,
        census.REGISTRIES_FLOOR,
    )
