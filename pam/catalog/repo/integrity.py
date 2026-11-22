"""Catalog SQL for `pam verify` (spec 7.5). All SQL lives under catalog/repo/.

THE ONE MODULE IN THIS PACKAGE THAT IS NOT KEYED TO A TABLE, and deliberately so.
Every other module here owns one table and reaches across only where the schema makes
one table the home of another's pointer (see repo/__init__.py). Spec 7.5's checker
asks a different kind of question: every one of its checks is ABOUT the relationship
between two tables - a `capture` row without its `files` row, a `plan_items` status
that disagrees with a `journal` outcome, a `companion_groups` row whose primary is not
one of its members. Filing eleven such queries under whichever table each happens to
name first would put each one where it does not belong and would turn a documented
list of three cross-table reaches into a list of fourteen. They are gathered here
instead, under the one consumer they all serve.

Nothing here writes. `pam verify` opens the catalog read-only (plan clarification 4:
"a checker must not be able to write"), so a write added to this module would fail at
runtime rather than silently corrupt what it was asked to inspect.

PREDICATES LIVE IN `pam/verify.py`, NOT HERE, wherever there is a choice. These
functions return rows; the checker decides which rows are a finding. That is not
tidiness: the reviewer's mutation pass disables and inverts each check's predicate one
at a time, and a predicate buried in a WHERE clause is a predicate that mutation pass
cannot reach. The two exceptions are noted where they occur.
"""

import sqlite3
from dataclasses import dataclass

# One query per table that carries a `files` pointer. A table added to this map is a
# table spec 7.5 check 2 starts covering; a table left out is one it silently does not,
# which is why the map is written out rather than derived from the schema.
#
# The key each query returns is the ORPHAN ROW's own key: `capture` and `file_events`
# are both keyed by `file_id`, `plan_items` by its own `id`.
_ORPHAN_QUERIES = {
    "capture": (
        "SELECT c.file_id AS key FROM capture c"
        " LEFT JOIN files f ON f.id = c.file_id"
        " WHERE f.id IS NULL ORDER BY c.file_id"
    ),
    "file_events": (
        "SELECT e.file_id AS key FROM file_events e"
        " LEFT JOIN files f ON f.id = e.file_id"
        " WHERE f.id IS NULL ORDER BY e.file_id"
    ),
    "plan_items": (
        "SELECT p.id AS key FROM plan_items p"
        " LEFT JOIN files f ON f.id = p.file_id"
        " WHERE f.id IS NULL ORDER BY p.id"
    ),
}

# The two content-addressed tables (spec 4: keyed by sha256, not file_id, so that
# duplicates share enrichment and the cache survives a rename). Both are empty until
# M9-M11; the queries exist now because spec 7.5 check 2 names them now.
_INERT_FACT_QUERIES = {
    "inferred_facts": (
        "SELECT DISTINCT i.sha256 AS sha256 FROM inferred_facts i"
        " WHERE NOT EXISTS (SELECT 1 FROM files f WHERE f.sha256 = i.sha256)"
        " ORDER BY i.sha256"
    ),
    "face_instances": (
        "SELECT DISTINCT a.sha256 AS sha256 FROM face_instances a"
        " WHERE NOT EXISTS (SELECT 1 FROM files f WHERE f.sha256 = a.sha256)"
        " ORDER BY a.sha256"
    ),
}


def pragma_problems(conn: sqlite3.Connection) -> list[str]:
    """Spec 7.5 check 1: everything `integrity_check` and `foreign_key_check` report.

    Both, never one: `integrity_check` reads the b-tree structure and says nothing
    about referential integrity, and `foreign_key_check` reads the references and says
    nothing about the pages they live on. A catalog can fail either while passing the
    other.

    `integrity_check` returns the single row 'ok' on a healthy database, which is the
    one row that is NOT a problem - every other row it returns is one.
    """
    problems = []
    for row in conn.execute("PRAGMA integrity_check"):
        finding = str(row[0])
        if finding != "ok":
            problems.append(f"integrity_check: {finding}")
    for row in conn.execute("PRAGMA foreign_key_check"):
        # (table, rowid, parent table, fk index) - sqlite's own column order.
        problems.append(
            f"foreign_key_check: {row[0]} rowid {row[1]} points at a missing {row[2]} row"
        )
    return problems


def orphan_rows(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """Spec 7.5 check 2: (table, key) for every row whose `files` row is gone.

    "Without a live `files` row" is read as "without a `files` row at all", not as
    "without a `present` one". Nothing in pam deletes a `files` row - a file that
    disappears from disk is marked `status='missing'` and keeps its row and its facts,
    so it can come back - which means a `capture` row for a missing file is ordinary,
    healthy bookkeeping and reporting it would fire this check on every archive the
    user has tidied up. What cannot happen through pam is the row vanishing entirely.
    """
    found: list[tuple[str, int]] = []
    for table, query in _ORPHAN_QUERIES.items():
        found.extend((table, int(row["key"])) for row in conn.execute(query))
    return found


def inert_fact_rows(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Spec 7.5 check 2's second half: (table, sha256) matching no file pam knows of.

    Reported as inert rather than as an error, because spec 7.5 says so explicitly:
    these are content-addressed, so an entry whose file is not in the catalog today is
    a cache for a file that may return tomorrow (an external drive, a folder moved back)
    and it will be reused as it stands when that happens.

    Compared against every `sha256` in `files`, including the rows marked `missing`, for
    the reason `orphan_rows` gives: a missing file's cache is not stale, it is waiting.
    """
    found: list[tuple[str, str]] = []
    for table, query in _INERT_FACT_QUERIES.items():
        found.extend((table, str(row["sha256"])) for row in conn.execute(query))
    return found


@dataclass(frozen=True)
class CompanionGroupHealth:
    """One companion group as spec 7.5 check 3 needs to see it.

    `member_count` and `primary_is_member` are counted in SQL rather than derived from
    a membership list because a group with ZERO members is exactly one of the two
    states the check exists to find, and a query that joined members to groups would
    drop that group from the result entirely.
    """

    group_id: int
    kind: str
    primary_file_id: int
    member_count: int
    primary_is_member: bool


def companion_group_health(conn: sqlite3.Connection) -> list[CompanionGroupHealth]:
    """Every companion group with its member count and whether its primary is one.

    Membership lives on `files.companion_group_id` (spec 4), so this reads both tables;
    that is the reach `repo/companions.py` already documents, pointed at a different
    question.
    """
    # One pass over `files` (a GROUP BY), then a LEFT JOIN: a plain JOIN would drop a group
    # with ZERO members. The primary is a member exactly when ITS row names this group (a
    # primary-key lookup). Neither `files.companion_group_id` nor the journal is indexed, which
    # is why the old correlated form cost a table scan per group.
    rows = conn.execute(
        "SELECT g.id, g.kind, g.primary_file_id,"
        " COALESCE(m.members, 0) AS members,"
        " (fp.id IS NOT NULL) AS primary_members"
        " FROM companion_groups g"
        " LEFT JOIN (SELECT companion_group_id AS gid, COUNT(*) AS members FROM files"
        "            WHERE companion_group_id IS NOT NULL GROUP BY companion_group_id) m"
        "   ON m.gid = g.id"
        " LEFT JOIN files fp ON fp.id = g.primary_file_id AND fp.companion_group_id = g.id"
        " ORDER BY g.id"
    )
    return [
        CompanionGroupHealth(
            group_id=int(r["id"]),
            kind=str(r["kind"]),
            primary_file_id=int(r["primary_file_id"]),
            member_count=int(r["members"]),
            primary_is_member=bool(r["primary_members"]),
        )
        for r in rows
    ]


@dataclass(frozen=True)
class ItemOutcome:
    """One plan item beside the NEWEST terminal journal row that resolved it.

    Newest, because an item legitimately accumulates rows over its life: a `rename` row
    from the apply and an `undo_rename` row from the undo years later (spec 7.0 J2 -
    rows are never deleted, so the apply's row stays `done` forever). Only the last
    resolution describes where the file is now, and that is what the item's status
    claims to summarize.
    """

    plan_item_id: int
    plan_id: int
    old_rel_path: str
    item_status: str
    op: str
    state: str


def item_outcomes(conn: sqlite3.Connection) -> list[ItemOutcome]:
    """Spec 7.5 check 4: every resolved plan item beside its newest journal outcome.

    Rows still in `intent` are excluded on BOTH sides of the comparison: an unresolved
    row has no outcome to agree or disagree with, and an item whose newest row is
    `intent` is still correctly described by the resolution before it (spec 7.4 rev 9
    point 2 - a crashed undo leaves the item `applied` until its row resolves). Check
    4's first clause owns the `intent` rows themselves.
    """
    # One grouped pass over the journal for each item's newest terminal row, then joins. (A derived
    # table, not a `WITH`: tests/test_repo.py's table extractor would read a CTE's name as a table.)
    rows = conn.execute(
        "SELECT p.id, p.plan_id, p.old_rel_path, p.status, j.op, j.state"
        " FROM (SELECT plan_item_id, MAX(id) AS jid FROM journal"
        "       WHERE state <> 'intent' GROUP BY plan_item_id) newest"
        " JOIN journal j ON j.id = newest.jid"
        " JOIN plan_items p ON p.id = newest.plan_item_id"
        " ORDER BY p.id"
    )
    return [
        ItemOutcome(
            plan_item_id=int(r["id"]),
            plan_id=int(r["plan_id"]),
            old_rel_path=str(r["old_rel_path"]),
            item_status=str(r["status"]),
            op=str(r["op"]),
            state=str(r["state"]),
        )
        for r in rows
    ]


@dataclass(frozen=True)
class PlanOutcome:
    plan_id: int
    status: str
    applied: int
    undone: int


def plan_outcomes(conn: sqlite3.Connection, statuses: tuple[str, ...]) -> list[PlanOutcome]:
    """Plans in the given statuses, each with how many of its items are applied/undone.

    `statuses` comes from the caller rather than being written into the SQL so that the
    set spec 7.5 check 4's rev-10 clause names ('applied', 'partially_applied') stays
    visible in `verify.py` beside the predicate that reads these counts.

    THE `applied` COUNT IS OF ITEMS THAT WERE ACTUALLY RENAMED, WHICH IS WHY IT JOINS THE
    JOURNAL (spec 7.0 J12). A no-op item is resolved `applied` at preflight with no
    journal row and no `os.rename`, so it is `applied` forever - and check 4d's caller
    skips any plan with `applied > 0`, which would silently disarm the stale-status check
    for every plan holding one. J12's own sentence is the fix: "the plan is `undone` when
    nothing of it is still applied ON DISK, and a file that never moved was never applied
    to anything". A file that moved is journaled first (J1), so "applied and journaled" IS
    "still applied on disk", and it is the reading this check - which exists to hold
    `plan_items` against the journal - should have been making all along.
    """
    if not statuses:
        return []
    placeholders = ",".join("?" * len(statuses))
    rows = conn.execute(
        "SELECT pl.id, pl.status,"
        " SUM(CASE WHEN pi.status = 'applied' AND jx.plan_item_id IS NOT NULL"
        " THEN 1 ELSE 0 END) AS applied,"
        " SUM(CASE WHEN pi.status = 'undone' THEN 1 ELSE 0 END) AS undone"
        " FROM plans pl LEFT JOIN plan_items pi ON pi.plan_id = pl.id"
        " LEFT JOIN (SELECT DISTINCT plan_item_id FROM journal) jx ON jx.plan_item_id = pi.id"
        f" WHERE pl.status IN ({placeholders})"
        " GROUP BY pl.id, pl.status ORDER BY pl.id",
        statuses,
    )
    return [
        PlanOutcome(
            plan_id=int(r["id"]),
            status=str(r["status"]),
            applied=int(r["applied"] or 0),
            undone=int(r["undone"] or 0),
        )
        for r in rows
    ]


def rename_session_for_plan(conn: sqlite3.Connection, plan_id: int) -> str | None:
    """The newest session that RENAMED files for this plan, or None if none did.

    This is the session id spec 7.5 check 4's rev-10 clause must print: the fix for a
    plan left `applied` while every file is back is `pam undo <that apply's session>`,
    which finds every item already `undone`, renames nothing and writes the status.
    Never an `undo_rename` session - spec 7.4 rev 9 point 1 refuses to undo an undo.
    """
    row = conn.execute(
        "SELECT j.session_id FROM journal j JOIN plan_items p ON p.id = j.plan_item_id"
        " WHERE p.plan_id = ? AND j.op = 'rename'"
        " ORDER BY j.id DESC LIMIT 1",
        (plan_id,),
    ).fetchone()
    return None if row is None else str(row["session_id"])


def rename_sessions_for_plan(conn: sqlite3.Connection, plan_id: int) -> list[str]:
    """Every session that RENAMED files for this plan, oldest first (M15 Task 5b, C6.4).

    A plan the review page renamed in two batches has two rename sessions; `pam review`'s
    closing block names each one's report. Never an `undo_rename` session.
    """
    rows = conn.execute(
        "SELECT j.session_id FROM journal j JOIN plan_items p ON p.id = j.plan_item_id"
        " WHERE p.plan_id = ? AND j.op = 'rename'"
        " GROUP BY j.session_id ORDER BY MIN(j.id)",
        (plan_id,),
    ).fetchall()
    return [str(row["session_id"]) for row in rows]


def fact_ids(conn: sqlite3.Connection) -> set[int]:
    """Every `inferred_facts` id, so check 5 can tell a dangling target from a real one."""
    return {int(r["id"]) for r in conn.execute("SELECT id FROM inferred_facts")}


def fact_supersede_edges(conn: sqlite3.Connection) -> list[tuple[int, int]]:
    """(id, superseded_by) for every fact that names a successor (spec 7.5 check 5).

    `inferred_facts.superseded_by` is the only supersede pointer in spec 4's schema.
    Written by re-enrichment (spec 8.1), which arrives in M9 - so until then this
    returns nothing on any archive pam itself built, and the check is standing guard
    rather than reporting.
    """
    rows = conn.execute(
        "SELECT id, superseded_by FROM inferred_facts"
        " WHERE superseded_by IS NOT NULL ORDER BY id"
    )
    return [(int(r["id"]), int(r["superseded_by"])) for r in rows]


def duplicate_paths_nocase(conn: sqlite3.Connection) -> list[tuple[str, list[str]]]:
    """Spec 7.5 check 6: (lowered path, the rows sharing it) for every case-only clash.

    `files.rel_path` is UNIQUE, but SQLite's UNIQUE index is BINARY, so 'a/IMG.JPG' and
    'a/img.jpg' are two perfectly legal rows describing one file that Windows cannot
    hold twice. `journal_ops.py`'s J11 layer 2 exists because of exactly this, and this
    check is what reports the ones that got in anyway.

    THIS CHECK'S PREDICATE IS IN THE SQL, unlike most here, because "duplicate" is a
    GROUP BY ... HAVING and there is no honest way to hand a caller the grouping without
    performing it. LOWER() is ASCII-only, the same limitation `files.path_owner`
    documents for COLLATE NOCASE: a unicode-case twin is missed by both.
    """
    rows = conn.execute(
        "SELECT rel_path FROM files WHERE LOWER(rel_path) IN ("
        "  SELECT LOWER(rel_path) FROM files GROUP BY LOWER(rel_path) HAVING COUNT(*) > 1"
        ") ORDER BY LOWER(rel_path), rel_path"
    )
    grouped: dict[str, list[str]] = {}
    for row in rows:
        rel_path = str(row["rel_path"])
        grouped.setdefault(rel_path.lower(), []).append(rel_path)
    return list(grouped.items())


def known_sha256(conn: sqlite3.Connection) -> set[str]:
    """Every content hash in the catalog (spec 7.5 check 7's reference set).

    Includes files marked `missing`, for the reason `inert_fact_rows` gives: a cache
    entry for a file that is temporarily away is not an unknown key.
    """
    return {
        str(r["sha256"])
        for r in conn.execute("SELECT DISTINCT sha256 FROM files WHERE sha256 IS NOT NULL")
    }
