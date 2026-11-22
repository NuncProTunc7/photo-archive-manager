"""Fail unless every node id in a hand-written list PASSED in a JUnit report (M12 Task 6, P5).

    python tools/ci_required_passed.py <report.xml> <list-name>

WHY THIS EXISTS. Design 9.4 rev 27 (P5): a release job for an optional extra "asserts a floor
counted from the node ids that PASSED in a JUnit report, so a skip or a failure cannot satisfy
it". `m11.md` C3 specified that floor for the `faces` job and it was never built: the job's
`PAM_FACES_REQUIRED=1` turns an unavailable stack into a collection error, but nothing checked
that the tests the job exists for actually RAN. A test deleted, renamed or deselected leaves
`pytest -m faces` green over less and less. This script is that floor, and `ocr` (M12 Task 12)
reuses it.

THE LIST IS READ FROM `tests/test_ci_lanes.py` AND MUST BE A LITERAL. `<list-name>` names a
module-level assignment there, beside the test that reads the workflow. It is read with `ast`
and `ast.literal_eval`, never imported and never computed: a list derived from the `faces`
marker would agree with whatever the marker happens to select today, which is M743's shape (a
control built from the expectation it protects disappears together with it). A value that is
not a literal, or is empty, is refused.

WHAT COUNTS AS PASSED. A `<testcase>` with no `<failure>`, `<error>` or `<skipped>` child. A
required id matching no testcase is MISSING, and missing is a failure exactly as a skip is. A
parametrized test (`name[param]`) is matched by its base name, and every parametrization must
pass.

STDLIB ONLY - `xml.etree` for the report, `ast` for the list - because it runs in CI after
pytest, and a dependency here would be a dependency of the gate.
"""

import ast
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIST_SOURCE = REPO_ROOT / "tests" / "test_ci_lanes.py"

PASSED = "passed"
SKIPPED = "skipped"
FAILED = "failed"
MISSING = "missing"


class ListError(ValueError):
    """The named list is absent, computed, empty or not a list of node-id strings."""


def required_ids(source: Path, name: str) -> tuple[str, ...]:
    """The literal tuple/list/set of node ids assigned to `name` at module level in `source`."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if not any(isinstance(t, ast.Name) and t.id == name for t in targets):
            continue
        value = getattr(node, "value", None)
        try:
            ids = ast.literal_eval(value) if value is not None else None
        except ValueError as exc:
            raise ListError(f"{name} in {source.name} is not a literal: {exc}") from None
        if not isinstance(ids, (tuple, list, set, frozenset)):
            raise ListError(f"{name} in {source.name} is not a tuple, list or set literal")
        if not ids:
            raise ListError(f"{name} in {source.name} is empty; it would require nothing")
        if not all(isinstance(i, str) and "::" in i for i in ids):
            raise ListError(f"{name} in {source.name} holds something that is not a node id")
        return tuple(sorted(ids))
    raise ListError(f"no module-level {name} in {source.name}")


def _split(node_id: str) -> tuple[str, str]:
    """`tests/test_x.py::TestC::test_y` -> (`tests.test_x.TestC`, `test_y`): JUnit's reading."""
    path, *rest = node_id.split("::")
    module = path.removesuffix(".py").replace("\\", "/").replace("/", ".")
    return ".".join([module, *rest[:-1]]), rest[-1]


def outcomes(report: Path) -> dict[tuple[str, str], str]:
    """(classname, name) -> passed / skipped / failed, for every `<testcase>` in the report."""
    found: dict[tuple[str, str], str] = {}
    for case in ET.parse(report).getroot().iter("testcase"):
        tags = {child.tag for child in case}
        if tags & {"failure", "error"}:
            outcome = FAILED
        elif "skipped" in tags:
            outcome = SKIPPED
        else:
            outcome = PASSED
        found[(case.get("classname", ""), case.get("name", ""))] = outcome
    return found


def problems(report: Path, required: tuple[str, ...]) -> list[str]:
    """One line per required node id that did not PASS, naming what happened to it instead."""
    seen = outcomes(report)
    lines = []
    for node_id in required:
        classname, base = _split(node_id)
        matched = [
            outcome
            for (cls, name), outcome in seen.items()
            if cls == classname and (name == base or name.startswith(base + "["))
        ]
        if not matched:
            lines.append(f"{MISSING}: {node_id}")
        for outcome in matched:
            if outcome != PASSED:
                lines.append(f"{outcome}: {node_id}")
    return lines


def main(argv: list[str], source: Path = LIST_SOURCE) -> int:
    """`source` is where the list lives; only the tests pass anything but the default."""
    if len(argv) != 2:
        print("usage: python tools/ci_required_passed.py <report.xml> <list-name>")
        return 2
    report, name = Path(argv[0]), argv[1]
    try:
        required = required_ids(source, name)
    except ListError as exc:
        print(f"REFUSED: {exc}")
        return 2
    found = problems(report, required)
    if found:
        print(f"{len(found)} of the {len(required)} tests {name} requires did not PASS:")
        for line in found:
            print(f"  {line}")
        return 1
    print(f"all {len(required)} tests {name} requires PASSED in {report.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
