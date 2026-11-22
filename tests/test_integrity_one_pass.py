"""M14 Task 0A (M13 C2, R2): `pam verify`'s three correlated statements, in one pass each.

At 100,000 files after one apply `pam verify` took 13-15 minutes: `item_outcomes` (a correlated
`MAX` per item), `plan_outcomes` (a correlated `EXISTS` per item) and `companion_group_health`
(two correlated `COUNT(*)` per group). No migration, no behaviour change - so the oracle is the
PRE-REWRITE SQL, kept below as an independent second implementation, and the three functions are
held to it case by case. Two implementations that agree prove nothing about which is right, so
the two-resolution item, the after-apply plan and the zero-member group are ALSO pinned by a
literal table spelled from design 7.5, not from either implementation.
"""

import ast
import sqlite3
import time
from pathlib import Path

import pytest

from pam.catalog.db import connect, migrate
from pam.catalog.repo.integrity import companion_group_health, item_outcomes, plan_outcomes

NOW = "2026-10-30T09:00:00"

# The pre-rewrite SQL, kept as an independent second implementation (m13.md C2); never import
# it from production. Verbatim from `v0.1-m13:pam/catalog/repo/integrity.py`.
_REFERENCE_ITEM_OUTCOMES = (
    "SELECT p.id, p.plan_id, p.old_rel_path, p.status, j.op, j.state"
    " FROM plan_items p JOIN journal j ON j.plan_item_id = p.id"
    " WHERE j.state <> 'intent'"
    "   AND j.id = (SELECT MAX(j2.id) FROM journal j2"
    "               WHERE j2.plan_item_id = p.id AND j2.state <> 'intent')"
    " ORDER BY p.id"
)
_REFERENCE_PLAN_OUTCOMES = (
    "SELECT pl.id, pl.status,"
    " SUM(CASE WHEN pi.status = 'applied' AND EXISTS("
    "   SELECT 1 FROM journal j WHERE j.plan_item_id = pi.id"
    " ) THEN 1 ELSE 0 END) AS applied,"
    " SUM(CASE WHEN pi.status = 'undone' THEN 1 ELSE 0 END) AS undone"
    " FROM plans pl LEFT JOIN plan_items pi ON pi.plan_id = pl.id"
    " WHERE pl.status IN ({placeholders})"
    " GROUP BY pl.id, pl.status ORDER BY pl.id"
)
_REFERENCE_COMPANION_HEALTH = (
    "SELECT g.id, g.kind, g.primary_file_id,"
    " (SELECT COUNT(*) FROM files f WHERE f.companion_group_id = g.id) AS members,"
    " (SELECT COUNT(*) FROM files f WHERE f.companion_group_id = g.id"
    "    AND f.id = g.primary_file_id) AS primary_members"
    " FROM companion_groups g ORDER BY g.id"
)

STALE_AND_APPLIED = ("applied", "partially_applied")
# Independently written; asserted non-empty at collection (a parametrize over an empty list is
# SKIPPED by pytest, not failed).
STATUS_SETS = [
    STALE_AND_APPLIED,
    ("applied",),
    ("undone",),
    ("applying",),
    ("applied", "partially_applied", "undone", "applying"),
    ("no_such_status",),
]
assert STATUS_SETS


def _ref_items(conn):
    return [tuple(r) for r in conn.execute(_REFERENCE_ITEM_OUTCOMES)]


def _prod_items(conn):
    return [
        (o.plan_item_id, o.plan_id, o.old_rel_path, o.item_status, o.op, o.state)
        for o in item_outcomes(conn)
    ]


def _ref_plans(conn, statuses):
    sql = _REFERENCE_PLAN_OUTCOMES.format(placeholders=",".join("?" * len(statuses)))
    return [(r[0], r[1], int(r[2] or 0), int(r[3] or 0)) for r in conn.execute(sql, statuses)]


def _prod_plans(conn, statuses):
    return [(p.plan_id, p.status, p.applied, p.undone) for p in plan_outcomes(conn, statuses)]


def _ref_groups(conn):
    return [
        (r["id"], r["kind"], r["primary_file_id"], r["members"], bool(r["primary_members"]))
        for r in conn.execute(_REFERENCE_COMPANION_HEALTH)
    ]


def _prod_groups(conn):
    return [
        (g.group_id, g.kind, g.primary_file_id, g.member_count, g.primary_is_member)
        for g in companion_group_health(conn)
    ]


def _file(conn, file_id, name, group=None):
    conn.execute(
        "INSERT INTO files (id, rel_path, filename, ext, media_type, size, mtime_ns,"
        " companion_group_id, first_seen_at, last_scanned_at)"
        " VALUES (?, ?, ?, 'jpg', 'image', 1, 1, ?, ?, ?)",
        (file_id, f"d/{name}", name, group, NOW, NOW),
    )


def _plan(conn, plan_id, status):
    conn.execute(
        "INSERT INTO plans (id, created_at, status, template, config_json, app_version,"
        " schema_version) VALUES (?, ?, ?, 't', '{}', '0', 7)",
        (plan_id, NOW, status),
    )


def _item(conn, item_id, plan_id, name, status):
    conn.execute(
        "INSERT INTO plan_items (id, plan_id, file_id, old_rel_path, generated_filename,"
        " new_filename, tokens_json, tier, trust_score, trust_reasons_json, status)"
        " VALUES (?, ?, ?, ?, 'g', 'n', '[]', 'high', 90, '[]', ?)",
        (item_id, plan_id, item_id, name, status),
    )


def _row(conn, item_id, op, state):
    conn.execute(
        "INSERT INTO journal (session_id, plan_item_id, op, old_rel_path, new_rel_path, size,"
        " mtime_ns, state, intent_at) VALUES ('s-1', ?, ?, 'o', 'n', 1, 1, ?, ?)",
        (item_id, op, state, NOW),
    )


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "catalog.db")
    migrate(c)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def _journal_fixture(conn):
    """Plans 1-7, one case each; journal rows interleaved across items on purpose."""
    # plan 1: after an APPLY. A1, A2 renamed; A3 a no-op (applied, never journaled); A4 failed.
    _plan(conn, 1, "applied")
    # plan 2: after an UNDO, status still the stale 'applied' (check 4d's subject).
    _plan(conn, 2, "applied")
    # plan 3: after an undo, finalized 'undone' (4d's status filter excludes it).
    _plan(conn, 3, "undone")
    # plan 4: crash residue - intent rows.
    _plan(conn, 4, "applying")
    _plan(conn, 5, "proposed")
    # plan 6: design 7.5 check 4's own example - a plan recovery finalized out of `applying`
    # without renaming anything reads `partially_applied` with NO journal row at all. It is in
    # the queried statuses, so the statement must still report it (M2674: an inner join on the
    # journal dropped it with the suite green).
    _plan(conn, 6, "partially_applied")
    # plan 7: a plan with ZERO items in a queried status (M2675: an inner join on plan_items
    # dropped it with the suite green).
    _plan(conn, 7, "partially_applied")
    items = {
        1: ("A1", 1, "applied"),
        2: ("A2", 1, "applied"),
        3: ("A3", 1, "applied"),
        4: ("A4", 1, "failed"),
        5: ("B1", 2, "undone"),
        6: ("B2", 2, "undone"),
        7: ("U1", 3, "undone"),
        8: ("C1", 4, "applied"),
        9: ("C2", 4, "proposed"),
        10: ("Z1", 5, "proposed"),
        11: ("R1", 6, "approved"),
    }
    for item_id, (name, plan_id, status) in items.items():
        _file(conn, item_id, name)
        _item(conn, item_id, plan_id, name, status)
    # Interleaved: apply rows first, then plan 4's, then the undos - so the newest row of an
    # item is not simply the row nearest the start or end of the table.
    _row(conn, 1, "rename", "done")  # A1
    _row(conn, 5, "rename", "done")  # B1
    _row(conn, 2, "rename", "done")  # A2
    _row(conn, 6, "rename", "done")  # B2
    _row(conn, 7, "rename", "done")  # U1
    _row(conn, 4, "rename", "failed")  # A4
    _row(conn, 8, "rename", "done")  # C1 first resolution
    _row(conn, 9, "rename", "intent")  # C2 only an intent row
    _row(conn, 8, "undo_rename", "intent")  # C1 second row unresolved: the first stands
    _row(conn, 5, "undo_rename", "done")  # B1 undone
    _row(conn, 6, "undo_rename", "done")  # B2 undone
    _row(conn, 7, "undo_rename", "done")  # U1 undone
    conn.commit()


def test_the_item_outcomes_agree_with_the_reference_on_the_journal_fixture(conn):
    _journal_fixture(conn)
    assert _prod_items(conn) == _ref_items(conn)
    assert _prod_items(conn), "the fixture must produce rows or the comparison is vacuous"


def test_a_two_resolution_item_reports_its_newest_resolution_literal_table(conn):
    """Design 7.5 check 4: only the NEWEST terminal row describes where the file is now."""
    _journal_fixture(conn)
    # (plan_item_id, plan_id, old_rel_path, item status, op, state), spelled by hand:
    # A1/A2 one apply row each; A3 no row at all (a no-op); A4 its failed row; B1/B2/U1 an
    # apply `done` THEN an `undo_rename` `done` - the undo wins; C1 `rename done` then an
    # unresolved `undo_rename intent` - the intent is excluded, the rename stands; C2 only an
    # intent row - absent; Z1 nothing - absent.
    assert _prod_items(conn) == [
        (1, 1, "A1", "applied", "rename", "done"),
        (2, 1, "A2", "applied", "rename", "done"),
        (4, 1, "A4", "failed", "rename", "failed"),
        (5, 2, "B1", "undone", "undo_rename", "done"),
        (6, 2, "B2", "undone", "undo_rename", "done"),
        (7, 3, "U1", "undone", "undo_rename", "done"),
        (8, 4, "C1", "applied", "rename", "done"),
    ]


def test_an_intent_row_is_excluded_on_both_sides(conn):
    _journal_fixture(conn)
    ids = {row[0] for row in _prod_items(conn)}
    assert 9 not in ids, "an item whose only row is `intent` has no outcome"
    c1 = [row for row in _prod_items(conn) if row[0] == 8]
    assert c1 == [(8, 4, "C1", "applied", "rename", "done")]


@pytest.mark.parametrize("statuses", STATUS_SETS)
def test_the_plan_outcomes_agree_with_the_reference_for_every_status_set(conn, statuses):
    _journal_fixture(conn)
    assert _prod_plans(conn, statuses) == _ref_plans(conn, statuses)


def test_the_after_apply_plan_counts_only_journaled_applied_items_literal_table(conn):
    """Design 7.0 J12 / 7.5 check 4d: `applied` means applied AND journaled (renamed on disk)."""
    _journal_fixture(conn)
    # Plan 1: A1, A2 applied+journaled = 2; A3 applied but never journaled = not counted;
    # A4 failed. Plan 2 (after an undo, stale 'applied'): 0 applied, 2 undone. Plan 6
    # (finalized out of `applying`, nothing journaled) and plan 7 (no items) are still REPORTED,
    # each with 0 applied and 0 undone - the "zero applied, zero undone" state 7.5 says is not
    # stale - rather than silently missing from the result.
    assert _prod_plans(conn, STALE_AND_APPLIED) == [
        (1, "applied", 2, 0),
        (2, "applied", 0, 2),
        (6, "partially_applied", 0, 0),
        (7, "partially_applied", 0, 0),
    ]


def test_the_after_undo_plan_is_filtered_out_by_its_status(conn):
    _journal_fixture(conn)
    assert _prod_plans(conn, ("undone",)) == [(3, "undone", 0, 1)]
    assert 3 not in {p[0] for p in _prod_plans(conn, STALE_AND_APPLIED)}


def test_an_empty_status_set_returns_nothing(conn):
    _journal_fixture(conn)
    assert plan_outcomes(conn, ()) == []


def _group_fixture(conn):
    # file ids 1..8; the group rows need no FK to files (primary_file_id is a bare INTEGER).
    for i in range(1, 9):
        _file(conn, i, f"F{i}")
    conn.executemany(
        "INSERT INTO companion_groups (id, primary_file_id, kind) VALUES (?, ?, ?)",
        [
            (1, 1, "live_photo"),  # healthy: primary 1 is a member
            (2, 5, "raw_jpeg"),  # primary 5 is NOT a member (it belongs to nothing)
            (3, 6, "live_photo"),  # ZERO members
            (4, 7, "raw_jpeg"),  # primary 7 belongs to group 1, not to group 4
            (5, 8, "live_photo"),  # one member, and it is the primary
        ],
    )
    for fid, gid in [(1, 1), (2, 1), (3, 4), (4, 2), (7, 1), (8, 5)]:
        conn.execute("UPDATE files SET companion_group_id = ? WHERE id = ?", (gid, fid))
    conn.commit()


def test_the_companion_health_agrees_with_the_reference_on_the_group_fixture(conn):
    _group_fixture(conn)
    assert _prod_groups(conn) == _ref_groups(conn)


def test_a_group_with_zero_members_is_reported_literal_table(conn):
    """Design 7.5 check 3: a ZERO-member group is one of the two states the check exists for."""
    _group_fixture(conn)
    # (group id, kind, primary file, members, primary_is_member), spelled by hand from the rows
    # `_group_fixture` writes: group 1 owns files 1, 2, 7 (primary 1 included); group 2 owns
    # file 4 only (primary 5 belongs to nothing); group 3 owns nothing; group 4 owns file 3
    # (primary 7 belongs to group 1); group 5 owns file 8, its primary.
    assert _prod_groups(conn) == [
        (1, "live_photo", 1, 3, True),
        (2, "raw_jpeg", 5, 1, False),
        (3, "live_photo", 6, 0, False),
        (4, "raw_jpeg", 7, 1, False),
        (5, "live_photo", 8, 1, True),
    ]


def test_a_primary_that_is_not_a_member_is_reported_not_a_member(conn):
    _group_fixture(conn)
    by_id = {g[0]: g for g in _prod_groups(conn)}
    assert by_id[2][4] is False and by_id[2][3] == 1
    assert by_id[4][4] is False, "the primary exists but belongs to ANOTHER group"


def test_an_empty_catalog_gives_empty_results(conn):
    assert _prod_items(conn) == [] and _prod_plans(conn, STALE_AND_APPLIED) == []
    assert _prod_groups(conn) == []


# ---------------------------------------------------------------- scale (L1-style guard)

SCALE_ITEMS = 20_000
# 10,000 groups over all 20,000 files, not the brief's 1,000 over 10,000: the correlated group
# statement costs groups x files, and at 1,000 groups it measured 0.74 s - UNDER the bound, so
# the guard's group row could not see it come back (M2670, review). At 10,000 groups it costs
# ~7 s; the one-pass form ~0.02 s.
SCALE_GROUPS = 10_000
SCALE_BOUND_S = 2.0  # a guard, not a benchmark


def _scale_catalog(conn):
    _plan(conn, 1, "applied")
    conn.executemany(
        "INSERT INTO companion_groups (id, primary_file_id, kind) VALUES (?, ?, 'live_photo')",
        [(g, g) for g in range(1, SCALE_GROUPS + 1)],
    )
    conn.executemany(
        "INSERT INTO files (id, rel_path, filename, ext, media_type, size, mtime_ns,"
        " companion_group_id, first_seen_at, last_scanned_at)"
        " VALUES (?, ?, ?, 'jpg', 'image', 1, 1, ?, ?, ?)",
        [
            (
                i,
                f"d/{i}.jpg",
                f"{i}.jpg",
                (i % SCALE_GROUPS) + 1,
                NOW,
                NOW,
            )
            for i in range(1, SCALE_ITEMS + 1)
        ],
    )
    conn.executemany(
        "INSERT INTO plan_items (id, plan_id, file_id, old_rel_path, generated_filename,"
        " new_filename, tokens_json, tier, trust_score, trust_reasons_json, status)"
        " VALUES (?, 1, ?, ?, 'g', 'n', '[]', 'high', 90, '[]', 'applied')",
        [(i, i, f"{i}.jpg") for i in range(1, SCALE_ITEMS + 1)],
    )
    conn.executemany(
        "INSERT INTO journal (session_id, plan_item_id, op, old_rel_path, new_rel_path, size,"
        " mtime_ns, state, intent_at) VALUES ('s-1', ?, ?, 'o', 'n', 1, 1, 'done', ?)",
        [(i, op, NOW) for op in ("rename", "undo_rename") for i in range(1, SCALE_ITEMS + 1)],
    )
    conn.commit()


@pytest.mark.parametrize(
    "call",
    [
        lambda c: item_outcomes(c),
        lambda c: plan_outcomes(c, STALE_AND_APPLIED),
        lambda c: companion_group_health(c),
    ],
    ids=["item_outcomes", "plan_outcomes", "companion_group_health"],
)
def test_each_statement_finishes_inside_two_seconds_at_twenty_thousand_items(conn, call):
    _scale_catalog(conn)
    started = time.perf_counter()
    rows = call(conn)
    elapsed = time.perf_counter() - started
    assert rows, "an empty result would make the bound vacuous"
    assert elapsed < SCALE_BOUND_S, f"{elapsed:.2f}s - the correlated form is back"


def test_the_scale_results_are_the_hand_computed_shape(conn):
    _scale_catalog(conn)
    # Every item has a rename and an undo_rename `done`: the newest is the undo, for all of them.
    items = _prod_items(conn)
    assert len(items) == SCALE_ITEMS
    assert {(row[4], row[5]) for row in items} == {("undo_rename", "done")}
    # All 20,000 items are `applied` and journaled; none is `undone`.
    assert _prod_plans(conn, STALE_AND_APPLIED) == [(1, "applied", SCALE_ITEMS, 0)]
    # Group g owns the two files i <= 20,000 with i % 10,000 == g - 1; its primary, file g,
    # belongs to group (g % 10,000) + 1, never to g itself - so every primary is a non-member.
    assert _prod_groups(conn) == [(g, "live_photo", g, 2, False) for g in range(1, 10_001)]


# ---------------------------------------------------------------- the instrument's own guard

_UNDER_TEST = {"companion_group_health", "item_outcomes", "plan_outcomes"}


def test_this_module_imports_nothing_from_the_integrity_module_but_the_three_functions():
    """An oracle that borrows the production definition agrees with it by construction."""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "pam.catalog.repo.integrity":
                imported |= {a.name for a in node.names}
            elif module in ("pam.catalog.repo", "pam.catalog"):
                assert not {a.name for a in node.names} & {"integrity", "repo"}
        elif isinstance(node, ast.Import):
            assert not any("integrity" in a.name for a in node.names)
    assert imported == _UNDER_TEST


# Spelled by hand: each oracle and the ONE reference statement it must read.
_ORACLE_WIRING = {
    "_ref_items": "_REFERENCE_ITEM_OUTCOMES",
    "_ref_plans": "_REFERENCE_PLAN_OUTCOMES",
    "_ref_groups": "_REFERENCE_COMPANION_HEALTH",
}
assert _ORACLE_WIRING


def test_each_oracle_reads_its_reference_statement_and_never_calls_production():
    """An import is not a wiring (M2669): `return _prod_items(conn)` needs no import at all."""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    forbidden = _UNDER_TEST | {"_prod_items", "_prod_plans", "_prod_groups"}
    seen = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _ORACLE_WIRING:
            seen.add(node.name)
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            names |= {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
            assert _ORACLE_WIRING[node.name] in names, f"{node.name} no longer reads its SQL"
            assert not names & forbidden, f"{node.name} calls production: {names & forbidden}"
    assert seen == set(_ORACLE_WIRING)
