"""Catalog SQL for the plan_items table. All SQL lives under catalog/repo/."""

import json
import sqlite3
from dataclasses import dataclass

# The spec 4 enum, complete. A closed vocabulary for the same reason journal.py has
# one: the DDL carries no CHECK constraint, so a typo'd status would be written
# happily and then match nothing - `pam verify` check 4 (item statuses versus journal
# outcomes) would report a plan whose items simply vanished from every query that
# names a status.
ITEM_STATUSES = frozenset({"proposed", "approved", "skipped", "applied", "failed", "undone"})

# How many ids one `IN (?, ...)` binds (design 2 rev 32, L1), far under SQLite's default
# bound-variable limit of 32,766 and under the 999 of builds older than 3.32.
_ID_CHUNK = 500


@dataclass(frozen=True)
class PlanItemDraft:
    file_id: int
    old_rel_path: str
    unit_key: str  # spec 6.3 rule 6: the companion unit AS OF PLAN TIME
    generated_filename: str
    tokens_json: str
    tier: str
    trust_score: int
    trust_reasons_json: str


@dataclass(frozen=True)
class PlanItemRow:
    id: int
    file_id: int
    old_rel_path: str
    unit_key: str
    generated_filename: str
    new_filename: str
    tokens_json: str
    tier: str
    trust_score: int
    trust_reasons_json: str
    status: str


def insert_plan_items(
    conn: sqlite3.Connection, plan_id: int, items: list[PlanItemDraft]
) -> None:
    """new_filename starts equal to generated_filename; M7's edits land there.

    unit_key is written from the draft, never derived here: spec 6.3 rule 6 freezes
    the grouping into the plan so spec 7.3's apply never consults
    files.companion_group_id, which pam scan recomputes from scratch every scan.

    The empty-unit_key guard is deliberate and does NOT duplicate M5 Task 5's
    read-side refusal. Migration 0004 had to declare `unit_key TEXT NOT NULL
    DEFAULT ''` (SQLite requires a default on ADD COLUMN NOT NULL), so a pre-0004
    plan legitimately carries ''. Task 5 refuses those as "built by an older version
    of pam". A future write path that stored '' would be indistinguishable from one
    of those, so Task 5 would refuse every plan this version writes and misdiagnose
    the reason. Two different bugs, two different messages: this is the only writer,
    so it is where "this version wrote ''" gets caught, loudly.
    """
    if any(not item.unit_key for item in items):
        raise ValueError("plan_items.unit_key is required (spec 6.3 rule 6)")
    conn.executemany(
        """INSERT INTO plan_items (plan_id, file_id, old_rel_path, unit_key,
                                   generated_filename, new_filename, tokens_json,
                                   tier, trust_score, trust_reasons_json, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed')""",
        [
            (
                plan_id,
                item.file_id,
                item.old_rel_path,
                item.unit_key,
                item.generated_filename,
                item.generated_filename,
                item.tokens_json,
                item.tier,
                item.trust_score,
                item.trust_reasons_json,
            )
            for item in items
        ],
    )


def set_plan_item_status(conn: sqlite3.Connection, plan_item_id: int, status: str) -> None:
    """Move one item to `status`. Does NOT commit - the caller owns the transaction.

    spec 7.0 J5: the item's status, its journal row and its files row are written in
    ONE transaction by M5's RenameTransaction, so that a crash between items leaves
    the catalog consistent with disk for every item already resolved.

    Unlike the journal's state machine (spec 7.0 J2) there is no legal-predecessor
    check here, and deliberately so: plan_items has no invariant of that shape. An
    item legitimately goes proposed -> approved -> applied, and also applied -> undone
    (spec 7.4) long afterwards, and M7 will add user edits that move it back. The
    journal is the append-only record; this column is a summary of where the item
    currently stands.
    """
    if status not in ITEM_STATUSES:
        raise ValueError(
            f"unknown plan_item status {status!r} (spec 4: {sorted(ITEM_STATUSES)})"
        )
    conn.execute("UPDATE plan_items SET status = ? WHERE id = ?", (status, plan_item_id))


def set_plan_item_new_filename(
    conn: sqlite3.Connection, plan_item_id: int, new_filename: str
) -> None:
    """Spec 8.2's manual rename: the name this item will be applied under.

    Does NOT commit - the caller owns the transaction, exactly as
    `set_plan_item_status` does. M7's rename endpoint writes every member of a
    companion unit through this in ONE transaction (spec 7.3, amendment A6), and a
    handler that raises half way through must leave every row byte-identical.

    `generated_filename` is never touched, by anybody: it is what the PLANNER
    proposed, and spec 8.2's `restore` puts it back into this column with one click.
    Two columns because they answer two questions - what pam suggested, and what the
    user settled on - and a single column would make restore unimplementable.

    The empty guard follows `insert_plan_items`'s: an empty `new_filename` would make
    spec 7.1 build a target path that is the directory itself, and the writer is the
    only place that can tell an impossible value from a legitimately odd one.
    """
    if not new_filename:
        raise ValueError("plan_items.new_filename is required (spec 8.2)")
    conn.execute(
        "UPDATE plan_items SET new_filename = ? WHERE id = ?", (new_filename, plan_item_id)
    )


_COLUMNS = (
    "id, file_id, old_rel_path, unit_key, generated_filename, new_filename,"
    " tokens_json, tier, trust_score, trust_reasons_json, status"
)


def plan_items_by_id(
    conn: sqlite3.Connection, plan_item_ids: list[int]
) -> dict[int, PlanItemRow]:
    """plan_item_id -> row, for the journal's consumers (spec 7.2 and spec 7.4).

    A journal row names a `plan_item_id` and two paths and nothing else, but resolving
    it needs the `file_id` (to point the `files` row at what the disk now holds), the
    `unit_key` (spec 7.3's grouping, frozen at plan time) and the `status` (spec 7.4's
    undo must not replay an item that is already `undone`). All three live on this row,
    so this is one query rather than three near-identical ones.

    Looked up by id rather than by rel_path deliberately: the paths are exactly what is
    in dispute after a crash, and a lookup keyed on one of them would answer
    differently depending on which side of the rename the file happens to be sitting.

    Ids missing from the result do not exist. `journal.plan_item_id` carries a foreign
    key and nothing deletes a `plan_items` row, so that is catalog corruption rather
    than an ordinary outcome - the caller reports it, and is written to survive it.
    """
    found: dict[int, PlanItemRow] = {}
    for chunk in _chunks(plan_item_ids):
        placeholders = ",".join("?" * len(chunk))
        for r in conn.execute(
            f"SELECT {_COLUMNS} FROM plan_items WHERE id IN ({placeholders})", chunk
        ):
            found[int(r["id"])] = _row(r)
    return found


def plan_ids_for_items(conn: sqlite3.Connection, plan_item_ids: list[int]) -> list[int]:
    """Which plans these items belong to, ascending (spec 7.4's status write).

    `plan_id` is not on `PlanItemRow` - it is the parent key, not a property of the
    item - so it is asked for separately rather than widening a dataclass four
    milestones of code construct. A session's rows all belong to one plan today; the
    return is a list so a caller that finds more than one can say so instead of
    silently writing a status to whichever it happened to see first.
    """
    plan_ids: set[int] = set()
    for chunk in _chunks(plan_item_ids):
        placeholders = ",".join("?" * len(chunk))
        for r in conn.execute(
            f"SELECT DISTINCT plan_id FROM plan_items WHERE id IN ({placeholders})", chunk
        ):
            plan_ids.add(int(r["plan_id"]))
    return sorted(plan_ids)


def _chunks(ids: list[int]) -> list[list[int]]:
    """The distinct ids in slices of `_ID_CHUNK` (design 2 rev 32, L1).

    A session's journal rows are what these two reads are handed, and an apply of a whole
    archive is one session, so the list grows with the archive and a single `IN (?, ...)`
    crosses SQLite's bound-variable limit. CHUNKING IS SOUND HERE AND ONLY HERE because both
    reads are a POSITIVE membership: the union of the chunks' answers is the answer over the
    whole list. (A `NOT IN` is not - see `events._delete_all_but`.) An empty list yields no
    chunk, so no `IN ()` is ever built.
    """
    unique = sorted(set(ids))
    return [unique[i : i + _ID_CHUNK] for i in range(0, len(unique), _ID_CHUNK)]


def _row(r: sqlite3.Row) -> PlanItemRow:
    return PlanItemRow(
        id=r["id"],
        file_id=r["file_id"],
        old_rel_path=r["old_rel_path"],
        unit_key=r["unit_key"],
        generated_filename=r["generated_filename"],
        new_filename=r["new_filename"],
        tokens_json=r["tokens_json"],
        tier=r["tier"],
        trust_score=r["trust_score"],
        trust_reasons_json=r["trust_reasons_json"],
        status=r["status"],
    )


def plan_item_status_counts(conn: sqlite3.Connection, plan_id: int) -> dict[str, int]:
    """How many of one plan's items sit in each status - `pam review`'s closing summary.

    Grouped in SQL rather than counted from `plan_items` in Python, for the reason
    `plans.plan_status_counts` records: the caller would then have to know the status
    vocabulary, and a value this function had not heard of would go unreported - which
    is precisely what a summary exists to surface. The caller sums what it is given.
    """
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM plan_items WHERE plan_id = ? GROUP BY status",
        (plan_id,),
    )
    return {str(r["status"]): int(r["n"]) for r in rows}


def plan_items(conn: sqlite3.Connection, plan_id: int) -> list[PlanItemRow]:
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM plan_items WHERE plan_id = ? ORDER BY id",
        (plan_id,),
    )
    return [_row(r) for r in rows]


# A14's provenance for a `{people}` token: spec 6.1 records `(source_table='persons',
# source_id=<lowest person id named>)`, so `'persons'` in a `tokens_json` entry's
# `source_table` is the machine-readable mark that a filename was built from a person.
# SPELLED FROM SPEC 6.1, never read out of `naming/tokens.py` - an oracle taken from the
# code it checks agrees with any value of it.
PEOPLE_SOURCE_TABLE = "persons"


def plan_items_with_people_tokens(conn: sqlite3.Connection) -> dict[int, int]:
    """`{plan id: item count}` over items whose name was built from a person (A19).

    WHY THIS LIVES HERE AND NOT IN `faces.py`, WHERE THE M11 PLAN'S TASK 8 STEP 5 NAMES
    IT. The query reads `plan_items.tokens_json` and nothing else - no face, no person -
    because A19 is explicit that the inventory must be made "independently of label
    text". SQL over one table belongs with that table, and filing it under `faces.py`
    would have been a sixth entry in `guards.ALLOWED_REPO_REACHES`, which the same step
    forbids. The public name is unchanged: `repo.plan_items_with_people_tokens(conn)`.

    WHY THE MATCH IS IN PYTHON AND NOT IN SQL. `tokens_json` is a JSON array and the
    provenance is a field inside it, so the only pure-SQL spellings are a `LIKE` over the
    serialized text - which turns on `json.dumps`'s spacing and would silently stop
    matching if that ever changed - or SQLite's json1 extension, which is a build-time
    option this project has never depended on. Parsing is the reading that cannot be
    wrong about its own format.

    A19 IS WHY THIS COUNTS RATHER THAN NAMES. Before deleting `persons`, purge reports
    the affected plan and item counts; it does not, and must not, try to say WHICH name
    is in which filename. A14 records only the LOWEST person id for a multi-person token,
    sanitization changes the text, and a user-edited filename keeps the name with no
    attribution at all - so a per-person answer would be confidently wrong.

    A row whose `tokens_json` does not parse is skipped rather than raised on: this is
    the disclosure step of a destructive command, and a half-written plan from an
    interrupted milestone must not be able to stop a user purging their own faces.
    """
    affected: dict[int, int] = {}
    for row in conn.execute("SELECT id, plan_id, tokens_json FROM plan_items"):
        try:
            tokens = json.loads(str(row["tokens_json"]))
        except (TypeError, ValueError):
            continue
        if not isinstance(tokens, list):
            continue
        if any(
            isinstance(t, dict) and t.get("source_table") == PEOPLE_SOURCE_TABLE for t in tokens
        ):
            plan_id = int(row["plan_id"])
            affected[plan_id] = affected.get(plan_id, 0) + 1
    return affected
