"""M14 Task 3 Step 4 (U3): the ONE bypass of the Low rule, made findable.

U3: "the server keeps the property that no shortcut, preset, filter or `--all` approves a low
item: one Low rule (`naming/accept.py`), now with ONE sanctioned bypass, and the bypass is a
named route a guard can find." This is that guard.

WHAT "LOW-INCLUDING" MEANS HERE, so an AST walk can decide it:
  A call `_write_decisions(conn, ids, action)` is LOW-GUARDED when, inside the SAME function
  (the innermost `def` around it), a call to the Low rule comes EARLIER in source order: one
  of `accept_all(...)` or `exclude_low_units(...)` (`pam/naming/accept.py`, by name or as an
  attribute), or `listing.shortcut(...)` - which is itself `accept_all` (checked below, so the
  name cannot be borrowed by a function that is not). Every other call site may pass a
  low-including id set, with any action, approve included (the action is not read: a
  variable `action` can be `approve`, so every site is treated as one that can approve).
  "Earlier in the same function" stands in for "dominates": `decide_filter` runs
  `exclude_low_units` under `if action == APPROVE`, which is exactly the path that approves.

THE SANCTIONED UNGUARDED SITES, each with its reason, spelled as literals:
  - `selection.decide_selection` - U3's ONE bulk bypass: it approves low names only at the
    count the page showed (`include_low` + `expected_low`, design 8.2 rev N / U17);
  - `decide_api.decide` - NOT bulk: it writes the ids the request names one by one (UX 5a
    step 2's per-item `X`), and spec 8.2 rev 12 gives it no tier restriction on purpose. It
    stays sanctioned only while it reads its ids from `_item_ids` and never from a filter.

THE WALKER READS `Call` NODES, NEVER `Import` NODES (CLAUDE.md: an import is not a wiring;
T3 mutation 11). The set of call sites it finds is compared against an INDEPENDENTLY spelled
list, so a walker that finds nothing fails here rather than passing everything.
"""

import ast
from pathlib import Path

import pam.webui

WEBUI = Path(pam.webui.__file__).parent
WRITER = "_write_decisions"
LOW_RULE = ("accept_all", "exclude_low_units")
SHORTCUT = ("listing", "shortcut")

# The one bulk bypass and the one per-id route, with why (see the module docstring).
SANCTIONED_UNGUARDED = {
    "selection.decide_selection": "U3: the one bulk bypass, at the count the page showed",
    "decide_api.decide": "names its ids one by one; spec 8.2 rev 12 gives it no tier rule",
}
assert SANCTIONED_UNGUARDED, "the sanctioned list is empty - the guard would allow nothing"
assert "selection.decide_selection" in SANCTIONED_UNGUARDED

# Every function that calls the writer today, spelled by hand from the code's routes - NOT
# from the walker. A new caller must be added here, and so is found by a reviewer.
EXPECTED_CALLERS = {
    "decide_api.decide",
    "decide_api.decide_filter",
    "decide_api._accept_all",
    "decide_api._shortcut",
    "selection.decide_selection",
}
assert EXPECTED_CALLERS


def _name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_low_rule(call: ast.Call) -> bool:
    if _name(call.func) in LOW_RULE:
        return True
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and (func.value.id, func.attr) == SHORTCUT
    )


def _functions(tree: ast.AST):
    """Every `def`, with the calls that belong to IT (not to a nested `def`)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            calls: list[ast.Call] = []
            stack: list[ast.AST] = list(node.body)
            while stack:
                child = stack.pop()
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
                    continue
                if isinstance(child, ast.Call):
                    calls.append(child)
                stack.extend(ast.iter_child_nodes(child))
            yield node, calls


def write_sites(directory: Path = WEBUI) -> dict[str, bool]:
    """`module.function` -> is its every `_write_decisions` call low-guarded?"""
    sites: dict[str, bool] = {}
    for path in sorted(directory.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func, calls in _functions(tree):
            writes = [c for c in calls if _name(c.func) == WRITER]
            if not writes:
                continue
            rules = [(c.lineno, c.col_offset) for c in calls if _is_low_rule(c)]
            guarded = all(
                any(rule < (w.lineno, w.col_offset) for rule in rules) for w in writes
            )
            sites[f"{path.stem}.{func.name}"] = guarded
    return sites


def stray_references(directory: Path = WEBUI) -> list[str]:
    """A load of the writer that is not a call's callee (`w = _write_decisions`) would hide a
    call site from `write_sites`; there must be none."""
    found = []
    for path in sorted(directory.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        callees = {id(c.func) for c in ast.walk(tree) if isinstance(c, ast.Call)}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Name | ast.Attribute)
                and _name(node) == WRITER
                and isinstance(node.ctx, ast.Load)
                and id(node) not in callees
            ):
                found.append(f"{path.stem}:{node.lineno}")
    return found


def test_the_walker_finds_exactly_the_writers_callers_spelled_by_hand():
    """T3 mutation 11's kill: a walker on `Import` nodes finds no call site at all."""
    assert set(write_sites()) == EXPECTED_CALLERS


def test_the_only_unguarded_writers_are_the_sanctioned_two():
    unguarded = {site for site, guarded in write_sites().items() if not guarded}
    assert unguarded == set(SANCTIONED_UNGUARDED), (
        "a function passes a possibly low-including id set to _write_decisions without the "
        f"Low rule: {sorted(unguarded - set(SANCTIONED_UNGUARDED))}"
    )


def test_nothing_hides_the_writer_behind_another_name():
    assert stray_references() == []


def test_the_shortcut_the_guard_trusts_is_the_low_rule():
    """`listing.shortcut` counts as the Low rule only because it calls `accept_all` - with
    `also_hold`, OD-1's hold - and returns that result."""
    tree = ast.parse((WEBUI / "listing.py").read_text(encoding="utf-8"))
    func = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "shortcut"
    )
    returns = [n for n in ast.walk(func) if isinstance(n, ast.Return)]
    assert len(returns) == 1 and isinstance(returns[0].value, ast.Call)
    call = returns[0].value
    assert _name(call.func) == "accept_all"
    assert [k.arg for k in call.keywords] == ["also_hold"]


def test_decide_stays_a_per_id_route():
    """`decide`'s sanction holds only while its ids are the request's own `item_ids`."""
    tree = ast.parse((WEBUI / "decide_api.py").read_text(encoding="utf-8"))
    func = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "decide"
    )
    names = {_name(c.func) for c in ast.walk(func) if isinstance(c, ast.Call)}
    assert "_item_ids" in names
    assert not names & {"select_items", "_item_filter", "resolve", "in_list_group"}


def test_the_guard_finds_a_second_unguarded_caller(tmp_path):
    """The instrument's own control: a scratch module with an unguarded approve IS found, and
    a guarded one is not flagged (independent of the real package)."""
    (tmp_path / "scratch.py").write_text(
        "def sneak(conn, ids):\n"
        "    return _write_decisions(conn, ids, APPROVE)\n"
        "def fine(conn, items, ids):\n"
        "    kept, _ = exclude_low_units(items, ids)\n"
        "    return _write_decisions(conn, kept, APPROVE)\n"
        "def late(conn, items, ids):\n"
        "    n = _write_decisions(conn, ids, APPROVE)\n"
        "    accept_all(items)\n"
        "    return n\n",
        encoding="utf-8",
    )
    assert write_sites(tmp_path) == {
        "scratch.sneak": False,
        "scratch.fine": True,
        "scratch.late": False,
    }
    (tmp_path / "alias.py").write_text("w = _write_decisions\n", encoding="utf-8")
    assert stray_references(tmp_path) == ["alias:1"]


# --- the row writer UNDER `_write_decisions` (Task 3 review R1, finding F2) -----------------
#
# The guard above watches `_write_decisions`, but that is not the only call that can make an
# item `approved`: `repo.set_plan_item_status` is the row writer it wraps, and a route that
# called it directly would approve a low item with no Low rule and no count shown - and every
# check above would pass, because it never names `_write_decisions`. So the functions that call
# the row writer in `pam/webui/` are spelled here by hand, each with its reason.
ROW_WRITER = "set_plan_item_status"
ROW_WRITER_CALLERS = {
    "decide_api._write_decisions": "the one decision write every route above goes through",
    "write_api.rename_item": "per unit, the one the user typed a name for (spec 8.2 rev 12)",
}
assert ROW_WRITER_CALLERS


def row_writer_callers(directory: Path = WEBUI) -> set[str]:
    """`module.function` for every function whose own body calls the row writer."""
    found: set[str] = set()
    for path in sorted(directory.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func, calls in _functions(tree):
            if any(_name(c.func) == ROW_WRITER for c in calls):
                found.add(f"{path.stem}.{func.name}")
    return found


def test_only_the_spelled_functions_write_an_items_status_directly():
    assert row_writer_callers() == set(ROW_WRITER_CALLERS)


def test_the_row_writer_walk_finds_a_direct_caller(tmp_path):
    """The control: a scratch module approving through the row writer is found, though
    `write_sites` - which watches `_write_decisions` only - does not see it at all."""
    (tmp_path / "scratch.py").write_text(
        "def sneak(conn, ids):\n"
        "    for i in ids:\n"
        "        repo.set_plan_item_status(conn, i, 'approved')\n",
        encoding="utf-8",
    )
    assert row_writer_callers(tmp_path) == {"scratch.sneak"}
    assert write_sites(tmp_path) == {}
