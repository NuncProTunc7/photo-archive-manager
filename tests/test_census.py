"""The suite census's own tests (M12 Task 1, `m11.md` condition C4).

`tests/census.py` is the one inventory, outside every guarded module, of the controls the suite
must still hold, and `conftest.py` checks it at COLLECTION. These tests drive every check it makes
on HAND-BUILT inputs written here - a census that is only ever run against the real tree cannot
tell "the tree is intact" from "the check can never fail" - plus two subprocess runs: the hook
really stops collection, and every name the census lists is really COLLECTED, which an `ast`
definition is not proof of (the M12 gate's I3).

This module's own names are in `CENSUS["test_census.py"]`, so deleting this file is itself a
collection error.
"""

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from census import (
    CENSUS,
    NOT_REGISTRIES,
    TESTS_DIR,
    active_census,
    census_errors,
    defined_tests,
    registered_names,
    unmapped_registries,
)

REPO_ROOT = TESTS_DIR.parent


def write(path: Path, source: str) -> Path:
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def clean_env() -> dict[str, str]:
    """The environment for a child pytest, with nothing inherited that could narrow it."""
    env = dict(os.environ)
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


# --- the census's contents ---------------------------------------------------------------


def test_the_census_names_m11s_three_unanchored_survivors():
    """`m11.md` 2.4: the three modules the review deleted whole with the suite green
    (M1960-M1962) are C4's first entries. Spelled here as LITERALS, not read off the census."""
    for module in (
        "test_m11_safety_constructions.py",
        "test_faces_export.py",
        "test_line_budget.py",
    ):
        assert module in CENSUS, f"{module} is not in the census"
        assert CENSUS[module], f"the census names no controls for {module}"


def test_the_real_tree_passes_the_census():
    """The positive control. Without it, the hook test below could be passing because the
    census always fails."""
    # The census the hook checks on this tree: the whole one here, the public view in the export.
    census, registries, floors = active_census(TESTS_DIR)
    assert census_errors(TESTS_DIR, census, registries, floors=floors) == []


# --- defined_tests -----------------------------------------------------------------------


def test_defined_tests_ignores_a_nested_def(tmp_path):
    """A `def test_` inside another function is never collected, so it must never satisfy the
    census."""
    module = write(
        tmp_path / "test_nested.py",
        """
        def test_outer():
            def test_inner():
                pass

        def helper():
            def test_hidden():
                pass
        """,
    )
    assert defined_tests(module) == {"test_outer"}


def test_defined_tests_reads_a_test_class_method(tmp_path):
    module = write(
        tmp_path / "test_classy.py",
        """
        class TestThing:
            def test_method(self):
                pass

            def helper(self):
                pass

        class Helper:
            def test_not_collected(self):
                pass
        """,
    )
    assert defined_tests(module) == {"TestThing::test_method"}


# --- registered_names ---------------------------------------------------------------------


def test_registered_names_reads_dict_set_and_frozenset_literals_and_refuses_a_comprehension(
    tmp_path,
):
    module = write(
        tmp_path / "test_forms.py",
        """
        A_CONTROLS = {"test_a": "why", "test_b": "why"}
        B_CONTROLS = {"test_c", "test_d"}
        C_CONTROLS = frozenset({"test_e"})
        D_CONTROLS = {name: "why" for name in ("test_f",)}
        E_CONTROLS = sorted(B_CONTROLS)
        F_CONTROLS = {"test_g": "why", **A_CONTROLS}
        """,
    )
    assert registered_names(module, "A_CONTROLS") == {"test_a", "test_b"}
    assert registered_names(module, "B_CONTROLS") == {"test_c", "test_d"}
    assert registered_names(module, "C_CONTROLS") == {"test_e"}
    assert registered_names(module, "D_CONTROLS") is None, "a comprehension is not a literal"
    assert registered_names(module, "E_CONTROLS") is None, "a call is not a literal"
    assert registered_names(module, "F_CONTROLS") is None, "a ** spread is not a literal"
    assert registered_names(module, "NOT_THERE_CONTROLS") is None


def test_registered_names_reads_every_form_the_suite_uses(tmp_path):
    """Dict, set, tuple and `frozenset({...})` - every form a registry has taken in `tests/`
    (pass 2, A5), plus an annotated assignment."""
    module = write(
        tmp_path / "test_every_form.py",
        """
        DICT_CONTROLS = {"test_dict": ("a reason, "
                                       "in two parts")}
        SET_CONTROLS = {"test_set"}
        TUPLE_CONTROLS = ("test_tuple_1", "test_tuple_2")
        FROZEN_CONTROLS = frozenset({"test_frozen"})
        ANNOTATED_CONTROLS: dict[str, str] = {"test_annotated": "why"}
        """,
    )
    assert registered_names(module, "DICT_CONTROLS") == {"test_dict"}
    assert registered_names(module, "SET_CONTROLS") == {"test_set"}
    assert registered_names(module, "TUPLE_CONTROLS") == {"test_tuple_1", "test_tuple_2"}
    assert registered_names(module, "FROZEN_CONTROLS") == {"test_frozen"}
    assert registered_names(module, "ANNOTATED_CONTROLS") == {"test_annotated"}


# --- census_errors on hand-built inputs -----------------------------------------------------


def test_census_errors_names_a_missing_module_a_missing_control_and_an_empty_entry(tmp_path):
    write(
        tmp_path / "test_present.py",
        """
        def test_kept():
            pass
        """,
    )
    write(tmp_path / "test_emptied.py", "def test_anything():\n    pass\n")
    census = {
        "test_present.py": frozenset({"test_kept", "test_deleted"}),
        "test_vanished.py": frozenset({"test_whatever"}),
        "test_emptied.py": frozenset(),
    }
    errors = census_errors(tmp_path, census, {"test_present.py": ()}, floors=None)
    assert any("test_vanished.py is GONE" in e for e in errors), errors
    assert any(
        "test_present.py no longer defines" in e and "test_deleted" in e for e in errors
    ), errors
    assert any("test_emptied.py: the census names no controls" in e for e in errors), errors
    assert len(errors) == 3, errors

    assert census_errors(tmp_path, {}, {"x": ()}, floors=None) == [
        "the census is empty; it protects nothing"
    ]
    assert "the census maps no registries; REGISTRIES is empty" in census_errors(
        tmp_path, {"test_present.py": frozenset({"test_kept"})}, {}, floors=None
    )


def test_each_floor_fails_below_it_and_passes_at_it(tmp_path):
    """The three floors, driven to FAIL on a hand-built census (M12 Task 1 review, M1993-M1995:
    every other call here passes `floors=None` and the real tree sits exactly AT each floor, so
    each floor check could be deleted with the suite green). One operand at a time, so each
    line is pinned alone, and the boundary, so `<` cannot drift to `<=`."""
    write(tmp_path / "test_one.py", "def test_one():\n    pass\n\nX_CONTROLS = {'test_one'}\n")
    census = {"test_one.py": frozenset({"test_one"})}
    registries = {"test_one.py": ("X_CONTROLS",)}

    def errors(floors):
        return census_errors(tmp_path, census, registries, floors=floors)

    assert errors((1, 1, 1)) == [], "a census AT every floor must pass"
    assert errors((2, 1, 1)) == ["the census lists 1 modules, floor 2"]
    assert errors((1, 2, 1)) == ["the census lists 1 controls, floor 2"]
    assert errors((1, 1, 2)) == ["REGISTRIES maps 1 modules, floor 2"]


def test_a_removed_registration_fails_the_census(tmp_path):
    """The gate's I3: the test survives, its REGISTRATION is gone. Neither the registry's own
    membership test nor a definition check can see this; the census must."""
    write(
        tmp_path / "test_registry.py",
        """
        def test_registered():
            pass

        def test_unregistered():
            pass

        LOAD_BEARING_CONTROLS = {"test_registered": "why"}
        """,
    )
    census = {"test_registry.py": frozenset({"test_registered", "test_unregistered"})}
    registries = {"test_registry.py": ("LOAD_BEARING_CONTROLS",)}
    errors = census_errors(tmp_path, census, registries, floors=None)
    assert errors == ["test_registry.py no longer REGISTERS ['test_unregistered']"]

    write(tmp_path / "test_registry.py", "def test_registered():\n    pass\n")
    errors = census_errors(
        tmp_path,
        {"test_registry.py": frozenset({"test_registered"})},
        registries,
        floors=None,
    )
    assert "test_registry.py: registry LOAD_BEARING_CONTROLS is gone or not a literal" in errors


def test_a_registry_in_a_module_the_census_does_not_list_is_reported(tmp_path):
    """M14 Task 7 review (concern 12, built): a NEW module declaring a registry, absent from the
    census, passed every check. It is reported now; the same module with no registry is not (the
    census is opt-in for a plain module), and once listed it is the census's like any other."""
    write(tmp_path / "test_listed.py", "def test_kept():\n    pass\n")
    write(
        tmp_path / "test_new.py",
        """
        def test_guard():
            pass

        LOAD_BEARING_CONTROLS = {"test_guard": "a new guard"}
        """,
    )
    write(tmp_path / "test_plain.py", "def test_plain():\n    pass\n")
    census = {"test_listed.py": frozenset({"test_kept"})}
    assert census_errors(tmp_path, census, {"test_listed.py": ()}, floors=None) == [
        "test_new.py declares ['LOAD_BEARING_CONTROLS'] but the census does not list it"
    ]
    census["test_new.py"] = frozenset({"test_guard"})
    registries = {"test_listed.py": (), "test_new.py": ("LOAD_BEARING_CONTROLS",)}
    assert census_errors(tmp_path, census, registries, floors=None) == []


def test_an_unmapped_registry_is_reported(tmp_path):
    write(
        tmp_path / "test_unmapped.py",
        """
        def test_one():
            pass

        LOAD_BEARING_CONTROLS = {"test_one": "why"}
        """,
    )
    census = {"test_unmapped.py": frozenset({"test_one"})}
    errors = census_errors(tmp_path, census, {"test_other.py": ("X",)}, floors=None)
    assert errors == [
        "test_unmapped.py: LOAD_BEARING_CONTROLS looks like a registry the census does not map"
    ]

    # A COUNTER listed in NOT_REGISTRIES is not a registry, and is not reported. (The module above
    # goes first: unlisted below, its registry would now be reported in its own right.)
    (tmp_path / "test_unmapped.py").unlink()
    counter = sorted(NOT_REGISTRIES)[0]
    write(
        tmp_path / "test_counter.py",
        f"""
        def test_one():
            pass

        {counter} = 3
        """,
    )
    assert unmapped_registries(tmp_path / "test_counter.py", ()) == []
    assert (
        census_errors(
            tmp_path,
            {"test_counter.py": frozenset({"test_one"})},
            {"test_other.py": ("X",)},
            floors=None,
        )
        == []
    )


# --- the two subprocess runs --------------------------------------------------------------


def test_the_hook_fails_collection_when_a_guarded_module_is_gone(tmp_path):
    """The WIRING, not the function. A copy of the real `conftest.py` and `census.py` in a
    directory holding none of the guarded modules; BOTH optional-stack stubs say READY (M12
    Task 12 made the stacks a table: faces and ocr), so the table's loop has nothing to skip -
    the census must run above it."""
    shutil.copy(TESTS_DIR / "conftest.py", tmp_path / "conftest.py")
    shutil.copy(TESTS_DIR / "census.py", tmp_path / "census.py")
    for stub, variable in (
        ("faces_stack.py", "PAM_FACES_REQUIRED"),
        ("ocr_stack.py", "PAM_OCR_REQUIRED"),
        ("inno_stack.py", "PAM_INNO_REQUIRED"),
    ):
        write(
            tmp_path / stub,
            f"""
            REQUIRED_VAR = "{variable}"
            STACK_READY = True
            STACK_REASON = "stubbed ready by tests/test_census.py"
            """,
        )
    write(tmp_path / "test_trivial.py", "def test_trivial():\n    pass\n")
    env = clean_env()
    env.pop("PAM_FACES_REQUIRED", None)
    env.pop("PAM_OCR_REQUIRED", None)
    env.pop("PAM_INNO_REQUIRED", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 4, output[-2000:]
    assert "THE SUITE CENSUS FAILED" in output, output[-2000:]
    assert "test_faces_export.py is GONE" in output, output[-2000:]


def test_every_census_control_is_collected():
    """The gate's I3: a DEFINITION is not a COLLECTION (`__test__ = False`, a collection
    error, a bad marker would each leave every `def` in place). Collects the whole tree once,
    with nothing inherited that could narrow it, and requires exit 0 before reading an id."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            # `test_ci_lanes.py`'s probe fixture writes and deletes a file under `tests/`; a
            # concurrent run deleting it mid-walk failed this collection (M15 Task 8 review, O3).
            "--ignore-glob=*test_zz_ci_lane_probe_*",
            "tests",
        ],
        cwd=REPO_ROOT,
        env=clean_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert result.returncode == 0, (result.stdout + result.stderr)[-3000:]
    collected: set[tuple[str, str]] = set()
    for line in result.stdout.splitlines():
        if not line.startswith("tests/") or "::" not in line:
            continue
        path, _, name = line.partition("::")
        # keyed as the census keys it: relative to tests/ (`private/test_x.py` in tests/private)
        collected.add((Path(path).relative_to("tests").as_posix(), name.split("[", 1)[0]))
    assert len(collected) > 1000, f"only {len(collected)} node ids parsed; the parse is wrong"
    missing = sorted(
        (module, name)
        for module, names in active_census(TESTS_DIR)[0].items()
        for name in names
        if (module, name) not in collected
    )
    assert missing == [], f"census controls DEFINED but not COLLECTED: {missing}"
