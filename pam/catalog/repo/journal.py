"""Catalog SQL for the journal table. All SQL lives under catalog/repo/.

The journal is the write-ahead log that makes every rename recoverable (spec 7).
The table has existed since 0001_init.sql; M5 is its first consumer, so no schema
change was needed for it.

This module is pure SQL with no test affordances: no maybe_fault() call and no import
of pam.faults may appear anywhere under catalog/repo/. Fault injection lives in the
RenameTransaction wrapper that calls these functions (spec 9.3 rev 7). The rule is
enforced by an AST walk in tests/test_repo.py, which reads calls and imports rather
than text - so this sentence can name the thing it forbids.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime


class JournalInvariantError(Exception):
    """spec 7.0 J2 violated: pam's own state machine is wrong. Never a user error.

    Deliberately OUTSIDE the PamError hierarchy. pam/cli.py maps a PamError to exit 1
    with a tidy message and no traceback, which is right for a condition the user
    caused and can fix. This is not one: external interference cannot change the
    catalog, so an illegal journal transition means pam contradicted itself. It falls
    through to the `except Exception` handler instead - spec 13's exit 2 with a
    traceback, which is the honest report.

    A dedicated type rather than ValueError because ValueError is also what
    `datetime.fromisoformat` and `int()` raise, and this module does both. An
    `except ValueError` written for timestamp or int parsing in a later task would
    otherwise swallow a J2 violation, the one error here that must never be swallowed.
    """


# spec 7.0 J2 - append-and-resolve. Rows are NEVER deleted and never revert to an
# earlier state. This map IS the legal transition set, and `resolve` turns it into a
# WHERE clause so an illegal transition affects zero rows in the database rather than
# being refused by a Python check that a later edit could drop.
_LEGAL_PREDECESSOR = {
    "done": "intent",
    "failed": "intent",
    "reverted": "done",
}

# Every state a journal row can be in: the three terminal states above plus the one
# every row starts in. `resolve` has a closed vocabulary and `rows_in_state` must have
# one too - a typo'd state there returns [] ("nothing to recover"), which spec 7.5's
# check 4 would read as a clean archive.
_STATES = frozenset({"intent", "done", "failed", "reverted"})

# spec 7.0 J2's three transition TARGETS - every state a row can be moved to, which is
# every state except the one it starts in. "Resolved" and "terminal" are one word in this
# project's vocabulary: J7's convergence is "after recovery no row remains in `intent`".
# Public because `pam report session` probes the disk for exactly these rows and must not
# spell the set a second time.
RESOLVED_STATES = ("done", "failed", "reverted")

# The same contract `journal_ops` asserts about its two status maps: a fourth state added
# to the vocabulary above, or a fourth legal transition, fails at IMPORT and names this
# file - rather than silently leaving rows in the new state unprobed by the report, which
# would go on printing a reassuring count line about a subset.
assert set(RESOLVED_STATES) == set(_LEGAL_PREDECESSOR) == _STATES - {"intent"}

# The two operations a journal row can record. Free text here would let a typo in
# spec 7.4's undo path write rows `pam undo` then misclassifies, and the DDL carries
# no CHECK constraint, so this is the only guard. Named singly as well as listed since
# M6 Task 5, because `SessionCounts.ops` made the two values something CALLERS branch
# on - and a caller comparing against its own string literal is how the two spellings
# of "undo_rename" drift apart.
OP_RENAME = "rename"
OP_UNDO_RENAME = "undo_rename"
OPS = (OP_RENAME, OP_UNDO_RENAME)

_COLUMNS = (
    "id, session_id, plan_item_id, op, old_rel_path, new_rel_path,"
    " size, mtime_ns, state, intent_at, resolved_at"
)

# The same columns qualified for unit_rows' join, spelled out rather than derived from
# _COLUMNS: a generated prefix reads as cleverness in a module whose whole job is to be
# checkable by eye.
_J_COLUMNS = (
    "j.id, j.session_id, j.plan_item_id, j.op, j.old_rel_path, j.new_rel_path,"
    " j.size, j.mtime_ns, j.state, j.intent_at, j.resolved_at"
)


@dataclass(frozen=True)
class JournalEntry:
    """One intended rename, as known BEFORE it is attempted (spec 7.1 step 2).

    size and mtime_ns are the snapshot the rename was verified against; spec 7.4's
    undo re-verifies the file against them before touching it, which is why they are
    journaled rather than re-read from `files` at undo time.
    """

    plan_item_id: int
    old_rel_path: str
    new_rel_path: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class JournalRow:
    id: int
    session_id: str
    plan_item_id: int
    op: str  # 'rename' | 'undo_rename'
    old_rel_path: str
    new_rel_path: str
    size: int
    mtime_ns: int
    state: str  # 'intent' | 'done' | 'failed' | 'reverted'
    intent_at: str
    resolved_at: str | None


def _row(r: sqlite3.Row) -> JournalRow:
    return JournalRow(
        id=r["id"],
        session_id=r["session_id"],
        plan_item_id=r["plan_item_id"],
        op=r["op"],
        old_rel_path=r["old_rel_path"],
        new_rel_path=r["new_rel_path"],
        size=r["size"],
        mtime_ns=r["mtime_ns"],
        state=r["state"],
        intent_at=r["intent_at"],
        resolved_at=r["resolved_at"],
    )


def insert_intents(
    conn: sqlite3.Connection,
    session_id: str,
    op: str,
    entries: list[JournalEntry],
    *,
    now: str,
) -> list[int]:
    """Write one 'intent' row per entry and return their ids, in the caller's order.

    spec 7.0 J1 - write-ahead: this is the write that must be COMMITTED before any
    os.rename is attempted. The commit is deliberately NOT done here. A companion
    unit's intents are all written and then committed together (spec 7.3), so the
    transaction boundary belongs to the caller (M5 Task 5's RenameTransaction), which
    is also where the `before_intent_commit` checkpoint sits.

    Inserted one row at a time rather than with executemany because executemany
    cannot return the generated ids, and the caller needs them to resolve each row
    individually. M5 is the correctness milestone: an item-by-item loop that yields
    the ids is worth more than a batch that does not.

    Raises JournalInvariantError on an unknown `op`, the same way `resolve` refuses an
    unknown state: the DDL has no CHECK on the column, so a typo in spec 7.4's undo
    path would otherwise write rows `pam undo` silently misclassifies.
    """
    if op not in OPS:
        raise JournalInvariantError(f"unknown journal op {op!r} (legal ops: {list(OPS)})")
    ids = []
    for entry in entries:
        row = conn.execute(
            """INSERT INTO journal (session_id, plan_item_id, op, old_rel_path,
                                    new_rel_path, size, mtime_ns, state, intent_at,
                                    resolved_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'intent', ?, NULL)
               RETURNING id""",
            (
                session_id,
                entry.plan_item_id,
                op,
                entry.old_rel_path,
                entry.new_rel_path,
                entry.size,
                entry.mtime_ns,
                now,
            ),
        ).fetchone()
        ids.append(int(row["id"]))
    return ids


def resolve(conn: sqlite3.Connection, journal_id: int, state: str, *, now: str) -> None:
    """Move one row to a terminal state, enforcing spec 7.0 J2 in SQL.

    The UPDATE carries `AND state = <legal predecessor>`, so an illegal transition
    matches zero rows and is refused by the database itself. Legal transitions are
    exactly intent -> done, intent -> failed, done -> reverted; nothing else, ever,
    and no row is ever deleted. Spec 7.4's promise that undo works indefinitely rests
    on this, because a row that could go backwards could be replayed twice.

    Does NOT commit, for the reason written on insert_intents: spec 7.0 J5 is "one
    transaction per item", and M5 Task 5's RenameTransaction.commit() must write the
    journal 'done', the plan_item 'applied' and the files row as ONE transaction. A
    commit here would split that triple, and a crash in the window would leave a
    'done' journal row whose files.rel_path still names the old path - which spec 7.2
    can never correct, because it branches on disk and finds nothing in 'intent'.

    DELIBERATELY NOT IDEMPOTENT: re-resolving an already-terminal row raises, because
    the `WHERE state = <predecessor>` clause matches zero rows. A silently idempotent
    resolve would hide a double replay. Spec 7.0 J7's convergence is therefore
    satisfied by re-querying `rows_in_state('intent')` on each recovery pass - never
    by re-issuing `resolve`.

    Raises JournalInvariantError on an illegal transition or a missing row. Both mean
    pam's own state machine is wrong (external interference cannot change the
    catalog), so this is an internal error and should surface as a traceback, not a
    tidy message. See that class for why it is not a ValueError and not a PamError.
    """
    predecessor = _LEGAL_PREDECESSOR.get(state)
    if predecessor is None:
        raise JournalInvariantError(
            f"illegal journal target state {state!r} (spec 7.0 J2 allows only"
            f" {sorted(_LEGAL_PREDECESSOR)})"
        )
    cur = conn.execute(
        "UPDATE journal SET state = ?, resolved_at = ? WHERE id = ? AND state = ?",
        (state, now, journal_id, predecessor),
    )
    if cur.rowcount == 1:
        return
    current = conn.execute("SELECT state FROM journal WHERE id = ?", (journal_id,)).fetchone()
    if current is None:
        raise JournalInvariantError(f"journal row {journal_id} does not exist")
    raise JournalInvariantError(
        f"illegal journal transition {current['state']!r} -> {state!r} for row"
        f" {journal_id} (spec 7.0 J2: {state!r} may only follow {predecessor!r})"
    )


def rows_in_state(conn: sqlite3.Connection, state: str) -> list[JournalRow]:
    """Every row in one state, oldest first - the crash-recovery pass's only query.

    Recovery reads state='intent' (spec 7.2) and then branches on the DISK, never on
    the catalog: spec 7.0 J3 says an 'intent' row admits all four disk states.

    The state vocabulary is closed, like `resolve`'s. An unknown state would otherwise
    return [] - indistinguishable from "nothing to recover", which is exactly the
    answer spec 7.5's check 4 reads as a clean archive.
    """
    if state not in _STATES:
        raise JournalInvariantError(
            f"unknown journal state {state!r} (legal states: {sorted(_STATES)})"
        )
    rows = conn.execute(f"SELECT {_COLUMNS} FROM journal WHERE state = ? ORDER BY id", (state,))
    return [_row(r) for r in rows]


def session_rows(conn: sqlite3.Connection, session_id: str) -> list[JournalRow]:
    """One session's rows in the order they were journaled.

    Ascending, always. spec 7.4's undo replays a session in REVERSE, and reversing in
    Python at the call site keeps that visible where it matters instead of hiding it
    in an ORDER BY that a later reader would have to go find.
    """
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM journal WHERE session_id = ? ORDER BY id", (session_id,)
    )
    return [_row(r) for r in rows]


def latest_resolved_rows(conn: sqlite3.Connection, session_id: str) -> dict[int, JournalRow]:
    """plan_item_id -> the LAST resolved row about that file, from ANY session.

    Serves `pam report session`'s "where are the files now" pass, and the reason it
    reaches past the session it is asked about is the whole design of the thing. spec 7.0
    J2 keeps a `rename` row `done` for ever - a row never reverts to an earlier state and
    is never deleted - so an apply's rows still say "the new name" long after `pam undo`
    legitimately put every file back. Reading each row's own state alone would therefore
    report every file of an undone session as interfered with: every renamed file of a
    human test's run, on a perfectly healthy archive. The journal is an append-only log, so the answer
    is to read it as one: the LAST thing pam recorded about a file is where pam put it.

    Keyed by `plan_item_id`, which is what an apply's `rename` row and the later undo's
    `undo_rename` row share - the undo builds its rows from the apply's, carrying the
    plan item across and reversing the direction (see `execute/undo.py`).

    Rows in `intent` are excluded: J3 says an `intent` row tells you pam MEANT to rename
    and nothing whatever about the disk, so it can govern no expectation. Its caller
    counts those separately rather than dropping them.

    ORDER BY id ascending and the dict comprehension keeps the last write per key, which
    is the "latest" this function promises. The subselect avoids a parameter list per
    plan item - a session holds hundreds of them - and names only this module's own table.
    """
    placeholders = ", ".join("?" * len(RESOLVED_STATES))
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM journal"
        " WHERE plan_item_id IN (SELECT plan_item_id FROM journal WHERE session_id = ?)"
        f"   AND state IN ({placeholders})"
        " ORDER BY id",
        (session_id, *RESOLVED_STATES),
    )
    return {row.plan_item_id: row for row in (_row(r) for r in rows)}


def unit_rows(conn: sqlite3.Connection, session_id: str, unit_key: str) -> list[JournalRow]:
    """One companion unit's rows within one session (spec 7.3, rev 6).

    Joined through plan_items.unit_key - the grouping FROZEN into the plan at plan
    time - and never through files.companion_group_id, which pam scan clears and
    recreates on every scan. Deriving the unit from the live grouping is how a group
    ends up split across two stems (vision S3); it is the bug this milestone exists
    to prevent.
    """
    rows = conn.execute(
        f"SELECT {_J_COLUMNS}"
        " FROM journal j JOIN plan_items p ON p.id = j.plan_item_id"
        " WHERE j.session_id = ? AND p.unit_key = ?"
        " ORDER BY j.id",
        (session_id, unit_key),
    )
    return [_row(r) for r in rows]


def split_units(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """(session_id, unit_key) for every unit spec 7.0 J6 forbids, oldest session first.

    A unit is SPLIT when its rows hold a `done` member beside a `failed` or `reverted`
    one: half the companion group is under the new stem and half is not (vision S3).
    spec 7.2's phase 2 repairs exactly this set, and it is the only query that finds
    them - phase 1 cannot, because a split unit can hold ZERO rows in `intent` (a crash
    inside spec 7.3's revert loop leaves precisely that).

    `intent` is deliberately not part of the test: J6 calls `done` + `intent` a
    legitimate transient, and phase 1 has already emptied that state by the time phase
    2 runs.

    The unit is the pair `(session_id, unit_key)`, never `unit_key` alone: an apply and
    its later undo legitimately touch the same unit_key in two sessions, and merging
    them would fabricate a split out of two healthy runs. Joined through
    `plan_items.unit_key` for the reason `unit_rows` gives - never
    `files.companion_group_id`, which pam scan recreates on every scan.
    """
    rows = conn.execute(
        "SELECT j.session_id, p.unit_key"
        " FROM journal j JOIN plan_items p ON p.id = j.plan_item_id"
        " GROUP BY j.session_id, p.unit_key"
        " HAVING SUM(CASE WHEN j.state = 'done' THEN 1 ELSE 0 END) > 0"
        "    AND SUM(CASE WHEN j.state IN ('failed', 'reverted') THEN 1 ELSE 0 END) > 0"
        " ORDER BY j.session_id, p.unit_key"
    )
    return [(r["session_id"], r["unit_key"]) for r in rows]


def unjournaled_unrenamed_items(conn: sqlite3.Connection, plan_id: int) -> list[str]:
    """The `old_rel_path` of every `failed` or `approved` item of `plan_id` with NO journal row.

    E6 (M15 Task 7, the controller's S2-F1 ruling): an item apply refused before writing an
    intent - AM2's cloud-only skip is one - is `failed` with no journal row, and there is no
    column saying why (schema 0008). The recovery report names these as not renamed and claims
    no reason. "No row in ANY session" rather than "none in this one", so an item that failed
    with a row in an earlier session of the same plan is not re-listed as unexplained here.

    Ruling (a) adds the `approved` items an interrupted run never reached: phase 3 leaves them
    `approved` and the plan terminal (`partially_applied`), so this plan never renames them.
    `proposed` and `skipped` items were never going to be renamed and are not returned.
    """
    rows = conn.execute(
        "SELECT p.old_rel_path FROM plan_items p"
        " WHERE p.plan_id = ? AND p.status IN ('failed', 'approved')"
        "   AND NOT EXISTS (SELECT 1 FROM journal j WHERE j.plan_item_id = p.id)"
        " ORDER BY p.id",
        (plan_id,),
    )
    return [r["old_rel_path"] for r in rows]


@dataclass(frozen=True)
class SessionCounts:
    """One session as `pam undo`'s "which session did you mean?" chooser sees it.

    NAMED FIELDS RATHER THAN A TUPLE, deliberately. This function used to return
    `(session_id, done, failed)`, and two positional numbers whose meaning the caller
    had to infer is what produced M5 open question 7: a session with nothing but
    unresolved `intent` rows and a session whose every row was reverted BOTH read
    `(id, 0, 0)`, and an applied session and one that has since been fully undone both
    read `(id, N, 0)`. Neither collision is fixable in the message - a message that
    guesses from two numbers is guessing.

    `done`/`failed`/`reverted`/`intent` are this session's own journal rows, counted by
    state; together they total every row of the session, so a caller can never be left
    wondering where the rest went. `undone` is the one number that does NOT come from
    the journal: spec 7.0 J2 means a `rename` row stays `done` forever, so "has this
    session been put back?" is only answerable from the `plan_items` rows those journal
    rows point at, which spec 7.4's undo moves to `undone`.

    `ops` IS THE M6 TASK 5 WIDENING, and it is the same defect one level up. The five
    numbers separate the STATES a session's rows are in; they say nothing about which
    OPERATION wrote them, so an apply of two files and the undo that put those same two
    files back are byte-identical here - both `(done=2, failed=0, reverted=0, intent=0,
    undone=2)`, reproduced against a real catalog before this field was added. Every
    consumer therefore called the undo session "2 renamed", which is false: those files
    were put back. It is a tuple of the DISTINCT ops present, not a single string,
    because a session holding two ops is catalog corruption rather than something this
    function may quietly collapse - and because `undo.undo_locked` already spells the
    predicate that way (`{row.op for row in rows} == {OP_UNDO}`), so the two cannot
    drift into disagreeing about what an undo session is.
    """

    session_id: str
    done: int
    failed: int
    reverted: int
    intent: int
    undone: int
    # No default, deliberately: a caller that could leave this out would be constructing
    # a session whose operation is unstated, which is the exact silence this field was
    # added to end.
    ops: tuple[str, ...]


def sessions(conn: sqlite3.Connection) -> list[SessionCounts]:
    """Every session with its state counts, oldest first.

    Feeds `pam undo`'s "which session did you mean?" message when the id is unknown.
    Session ids sort chronologically by construction, so ORDER BY session_id is also
    time order.

    LEFT JOIN, never an inner one: `journal.plan_item_id` is a foreign key and nothing
    deletes a `plan_items` row, so a missing item means catalog corruption - and an
    inner join would answer that by dropping the session out of the listing entirely,
    which is the one outcome a chooser must never have. A LEFT JOIN keeps the session
    visible with its journal counts intact and only `undone` under-reported.

    `GROUP_CONCAT(DISTINCT j.op)` carries the operation (M6 Task 5). SQLite promises
    nothing about the order it concatenates in, so the tuple is sorted here rather than
    taken as returned - two catalogs holding the same session must not compare unequal
    because one row was written first.
    """
    rows = conn.execute(
        "SELECT j.session_id,"
        " SUM(CASE WHEN j.state = 'done' THEN 1 ELSE 0 END) AS done_count,"
        " SUM(CASE WHEN j.state = 'failed' THEN 1 ELSE 0 END) AS failed_count,"
        " SUM(CASE WHEN j.state = 'reverted' THEN 1 ELSE 0 END) AS reverted_count,"
        " SUM(CASE WHEN j.state = 'intent' THEN 1 ELSE 0 END) AS intent_count,"
        " SUM(CASE WHEN p.status = 'undone' THEN 1 ELSE 0 END) AS undone_count,"
        " GROUP_CONCAT(DISTINCT j.op) AS ops"
        " FROM journal j LEFT JOIN plan_items p ON p.id = j.plan_item_id"
        " GROUP BY j.session_id ORDER BY j.session_id"
    )
    return [
        SessionCounts(
            session_id=r["session_id"],
            done=int(r["done_count"]),
            failed=int(r["failed_count"]),
            reverted=int(r["reverted_count"]),
            intent=int(r["intent_count"]),
            undone=int(r["undone_count"]),
            ops=tuple(sorted(str(r["ops"]).split(","))),
        )
        for r in rows
    ]


def next_session_id(conn: sqlite3.Connection, now: str) -> str:
    """An unused session id for `now`, formatted 's-YYYYMMDD-HHMMSS'.

    Seconds, not minutes (owner review of the M5 plan): '-2'/'-3' suffixes are hard
    to read in a log, and seconds remove nearly every occasion for one. The
    uniqueness check is kept anyway - two scripted invocations inside one second are
    still possible, and a suffix on a genuine collision beats a silent id reuse, which
    would merge two runs into one undoable session.

    `now` is the same ISO-8601 string every other repo function takes, so one caller
    passes one timestamp to next_session_id and insert_intents alike.
    """
    base = datetime.fromisoformat(now).strftime("s-%Y%m%d-%H%M%S")
    taken = {r["session_id"] for r in conn.execute("SELECT DISTINCT session_id FROM journal")}
    if base not in taken:
        return base
    suffix = 2
    while f"{base}-{suffix}" in taken:
        suffix += 1
    return f"{base}-{suffix}"


@dataclass(frozen=True)
class PrePamName:
    """Design 5.1/6.1, M13 (S9, H8): one file pam renamed, and the name it had before."""

    pam_path: str  # the rel_path pam last wrote for this file
    pre_pam_path: str  # the rel_path the file had before pam first renamed it
    size: int  # the journalled size at pam's last rename
    # HT-M14 F1 (AM6): the plan whose rename put the file at `pam_path`, when the user TYPED that
    # name (`new_filename` is not the `generated_filename`); None when pam generated it. The
    # planner keeps a typed name while that plan's template is still the one in force.
    typed_in_plan: int | None = None


def pre_pam_names(conn: sqlite3.Connection) -> dict[int, PrePamName]:
    """S9 / H8: file_id -> where pam put it and what it was called before pam.

    A file is in the map iff its latest `done` op='rename' row is not reversed by a later
    `done` op='undo_rename' row for the same plan_item. Chains (pam renamed it in plan A,
    then again in plan B) resolve `pre_pam_path` to the FIRST pre-pam name. The CALLER checks
    that the file is still at `pam_path` with `size` - a path is not an identity; that check
    is `name_evidence` below, so the scanner and the planner cannot spell it twice.

    ONLY `done` ROWS. An `intent` row says nothing about the disk (J3, and recovery resolves
    every one before a scan or a plan reads this), a `failed` rename never moved the file, and
    a `reverted` one was put back; none of them is a name pam wrote. An undo's rows carry the
    apply row's `plan_item_id` with the direction reversed (`execute/undo.py`), so a `done`
    `undo_rename` cancels the rename of the same plan item - and, for a chain, only that one:
    undoing plan B leaves the file at plan A's name, which is still pam's.

    The third function behind reach 3 (`journal` JOIN `plan_items`): the journal row carries
    only the `plan_item_id`, and the file it moved is `plan_items.file_id`. One SELECT, walked
    in journal `id` order in Python, because a chain is a sequence and SQL would hide it.
    """
    rows = conn.execute(
        "SELECT j.op, j.plan_item_id, j.old_rel_path, j.new_rel_path, j.size, p.file_id,"
        " p.plan_id, p.new_filename, p.generated_filename"
        " FROM journal j JOIN plan_items p ON p.id = j.plan_item_id"
        " WHERE j.state = 'done'"
        " ORDER BY j.id"
    )
    # file_id -> the renames pam has made of it and not undone, oldest first.
    chains: dict[int, list[sqlite3.Row]] = {}
    for r in rows:
        chain = chains.setdefault(r["file_id"], [])
        if r["op"] == OP_RENAME:
            chain.append(r)
        else:
            chain[:] = [done for done in chain if done["plan_item_id"] != r["plan_item_id"]]
    return {
        file_id: PrePamName(
            pam_path=chain[-1]["new_rel_path"],
            pre_pam_path=chain[0]["old_rel_path"],
            size=chain[-1]["size"],
            typed_in_plan=_typed_in_plan(chain[-1]),
        )
        for file_id, chain in chains.items()
        if chain
    }


def _typed_in_plan(row: sqlite3.Row) -> int | None:
    """The plan's id when this rename's name was typed by the user, else None (AM6)."""
    if row["new_filename"].casefold() == row["generated_filename"].casefold():
        return None
    return int(row["plan_id"])


def name_evidence(entry: PrePamName | None, rel_path: str, size: int) -> str | None:
    """The filename whose date is evidence for the file at `rel_path`, when pam wrote its name.

    `entry` is `pre_pam_names()[file_id]` for the files row now at `rel_path`. It applies only
    while that file is still at pam's path (case-insensitively, as every name comparison in
    pam is - design 6.3.2) AND still has the journalled size; otherwise the current name is
    the user's and this returns None, which means "parse the current name". When it applies,
    the answer is the BASENAME of the name the file had before pam renamed it (H8): a name pam
    wrote is never evidence, and a date the user's own original name carried is not lost.
    """
    if entry is None:
        return None
    if entry.pam_path.casefold() != rel_path.casefold() or entry.size != size:
        return None
    return entry.pre_pam_path.rpartition("/")[2]
