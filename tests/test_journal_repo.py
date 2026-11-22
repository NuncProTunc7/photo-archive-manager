"""Journal repo layer - the write-ahead log behind spec section 7's safety core.

Every test runs against a real migrated catalog (foreign keys ON, as db.connect
sets them), because the invariants under test are enforced in SQL and a mock would
prove nothing about them.
"""

import pytest

from pam.catalog import repo
from pam.catalog.db import connect, migrate

NOW = "2026-07-31T10:30:55"
LATER = "2026-07-31T10:31:00"


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "catalog.db")
    migrate(c)
    yield c
    c.close()


def make_items(conn, specs):
    """Create one plan with a plan_item per (rel_path, unit_key); return item ids.

    Journal rows carry a plan_item_id under a real foreign key, and unit_rows joins
    through plan_items.unit_key, so the journal cannot be exercised honestly without
    genuine plan rows behind it.
    """
    plan_id = repo.create_plan(
        conn,
        now=NOW,
        template="{date}_{seq}",
        config_json="{}",
        app_version="0.1.0",
        schema_version=4,
    )
    drafts = []
    for rel_path, unit_key in specs:
        file_id = repo.upsert_file(
            conn,
            rel_path=rel_path,
            filename=rel_path.rsplit("/", 1)[-1],
            ext=rel_path.rsplit(".", 1)[-1],
            media_type="image",
            size=100,
            mtime_ns=1_000,
            placeholder=False,
            now=NOW,
        )
        drafts.append(
            repo.PlanItemDraft(
                file_id=file_id,
                old_rel_path=rel_path,
                unit_key=unit_key,
                generated_filename="2019-07-04_001." + rel_path.rsplit(".", 1)[-1],
                tokens_json="[]",
                tier="high",
                trust_score=100,
                trust_reasons_json="[]",
            )
        )
    repo.insert_plan_items(conn, plan_id, drafts)
    conn.commit()
    return [item.id for item in repo.plan_items(conn, plan_id)]


def entry(plan_item_id, old="a.jpg", new="2019-07-04_001.jpg", size=100, mtime_ns=1_000):
    return repo.JournalEntry(
        plan_item_id=plan_item_id,
        old_rel_path=old,
        new_rel_path=new,
        size=size,
        mtime_ns=mtime_ns,
    )


# --- insert_intents (spec 7.0 J1) -------------------------------------------------


def test_insert_intents_writes_intent_rows_and_returns_ids_in_order(conn):
    a, b = make_items(conn, [("a.jpg", "u1"), ("b.jpg", "u2")])

    ids = repo.insert_intents(
        conn,
        "s-20260731-103055",
        "rename",
        [entry(a, old="a.jpg"), entry(b, old="b.jpg")],
        now=NOW,
    )
    conn.commit()

    assert len(ids) == 2
    rows = repo.session_rows(conn, "s-20260731-103055")
    assert [r.id for r in rows] == ids
    assert [r.plan_item_id for r in rows] == [a, b]
    assert [r.old_rel_path for r in rows] == ["a.jpg", "b.jpg"]


def test_insert_intents_starts_every_row_in_intent(conn):
    """spec 7.0 J1 - write-ahead. The committed row says 'pam is ABOUT to rename';
    if it were written already resolved, a crash before the rename would leave a
    row recovery reads as finished work and never re-examines.
    """
    (a,) = make_items(conn, [("a.jpg", "u1")])

    repo.insert_intents(conn, "s1", "rename", [entry(a)], now=NOW)
    conn.commit()

    (row,) = repo.session_rows(conn, "s1")
    assert row.state == "intent"
    assert row.resolved_at is None
    assert row.intent_at == NOW
    assert row.op == "rename"


def test_insert_intents_records_the_verification_snapshot(conn):
    """size and mtime_ns are journaled, not re-read at undo time (spec 7.4)."""
    (a,) = make_items(conn, [("a.jpg", "u1")])

    repo.insert_intents(
        conn, "s1", "rename", [entry(a, size=4_242, mtime_ns=987_654_321)], now=NOW
    )
    conn.commit()

    (row,) = repo.session_rows(conn, "s1")
    assert (row.size, row.mtime_ns) == (4_242, 987_654_321)


def test_insert_intents_does_not_commit(conn):
    """The caller owns the transaction boundary: a companion unit's intents are all
    committed together (spec 7.3), and the before_intent_commit checkpoint has to
    have a window to crash in.
    """
    (a,) = make_items(conn, [("a.jpg", "u1")])

    repo.insert_intents(conn, "s1", "rename", [entry(a)], now=NOW)
    conn.rollback()

    assert repo.session_rows(conn, "s1") == []


def test_insert_intents_carries_the_op_it_is_given(conn):
    (a,) = make_items(conn, [("a.jpg", "u1")])

    repo.insert_intents(conn, "s2", "undo_rename", [entry(a)], now=NOW)
    conn.commit()

    (row,) = repo.session_rows(conn, "s2")
    assert row.op == "undo_rename"


@pytest.mark.parametrize("op", ["undo-rename", "undo", "RENAME", "", "revert"])
def test_insert_intents_refuses_an_op_outside_the_closed_vocabulary(conn, op):
    """`op` was free text: the DDL carries no CHECK, and `state` had a closed
    vocabulary while its neighbour on the same row did not.

    A typo in Task 9's undo path would write rows that `pam undo` then misclassifies -
    silently, because nothing reads `op` back except undo itself. Refused at the
    write, the same way an illegal state is.
    """
    (a,) = make_items(conn, [("a.jpg", "u1")])

    with pytest.raises(repo.JournalInvariantError):
        repo.insert_intents(conn, "s1", op, [entry(a)], now=NOW)
    conn.commit()

    assert repo.session_rows(conn, "s1") == []


def test_the_op_vocabulary_is_exactly_the_two_the_journal_row_documents(conn):
    assert repo.OPS == ("rename", "undo_rename")


# --- resolve (spec 7.0 J2) --------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "final"),
    [
        (("done",), "done"),
        (("failed",), "failed"),
        (("done", "reverted"), "reverted"),
    ],
)
def test_resolve_walks_the_legal_transitions(conn, path, final):
    """spec 7.0 J2 names exactly three: intent->done, intent->failed, done->reverted."""
    (a,) = make_items(conn, [("a.jpg", "u1")])
    (jid,) = repo.insert_intents(conn, "s1", "rename", [entry(a)], now=NOW)
    conn.commit()

    for state in path:
        repo.resolve(conn, jid, state, now=LATER)
    conn.commit()

    (row,) = repo.session_rows(conn, "s1")
    assert row.state == final
    assert row.resolved_at == LATER


@pytest.mark.parametrize(
    ("reach", "illegal"),
    [
        (("done",), "intent"),  # never backwards
        (("failed",), "done"),  # failed is terminal
        (("failed",), "reverted"),  # and cannot be reverted either
        (("done", "reverted"), "done"),  # reverted is terminal
        (("done", "reverted"), "failed"),
        ((), "reverted"),  # intent -> reverted skips the rename
        ((), "intent"),  # intent -> intent is not a transition
    ],
)
def test_resolve_refuses_every_illegal_transition_and_changes_nothing(conn, reach, illegal):
    """The refusal is a WHERE clause, so the database rejects it: an illegal
    transition matches zero rows. spec 7.4's indefinite-undo promise rests on a row
    never going backwards, because a row that could would be replayed twice.
    """
    (a,) = make_items(conn, [("a.jpg", "u1")])
    (jid,) = repo.insert_intents(conn, "s1", "rename", [entry(a)], now=NOW)
    conn.commit()
    for state in reach:
        repo.resolve(conn, jid, state, now=LATER)
    conn.commit()
    before = repo.session_rows(conn, "s1")

    with pytest.raises(repo.JournalInvariantError):
        repo.resolve(conn, jid, illegal, now="2026-07-31T12:00:00")

    assert repo.session_rows(conn, "s1") == before


def test_resolve_names_the_states_when_it_refuses(conn):
    (a,) = make_items(conn, [("a.jpg", "u1")])
    (jid,) = repo.insert_intents(conn, "s1", "rename", [entry(a)], now=NOW)
    repo.resolve(conn, jid, "failed", now=LATER)
    conn.commit()

    with pytest.raises(repo.JournalInvariantError) as excinfo:
        repo.resolve(conn, jid, "done", now=LATER)
    message = str(excinfo.value)
    assert "failed" in message and "done" in message


def test_resolve_refuses_a_state_that_is_not_a_journal_state(conn):
    (a,) = make_items(conn, [("a.jpg", "u1")])
    (jid,) = repo.insert_intents(conn, "s1", "rename", [entry(a)], now=NOW)
    conn.commit()

    with pytest.raises(repo.JournalInvariantError):
        repo.resolve(conn, jid, "applied", now=LATER)  # a plan_items status, not a journal one
    assert repo.session_rows(conn, "s1")[0].state == "intent"


def test_resolve_refuses_a_missing_row(conn):
    with pytest.raises(repo.JournalInvariantError) as excinfo:
        repo.resolve(conn, 9_999, "done", now=LATER)
    assert "9999" in str(excinfo.value)


def test_a_j2_violation_is_not_a_value_error_and_not_a_pam_error(conn):
    """The TYPE is load-bearing, in both directions.

    Not ValueError: `next_session_id` calls `datetime.fromisoformat`, which raises
    ValueError on a bad timestamp - same module, opposite meaning. Any `except
    ValueError` written for timestamp or int parsing in Tasks 5-10 would silently
    swallow a J2 violation, the one error in this codebase that must never be
    swallowed.

    Not PamError either: cli.py maps PamError to exit 1 with a tidy message and no
    traceback, which is right for a user error. An illegal journal transition is not
    one - external interference cannot change the catalog - so it must fall through
    to spec 13's exit 2 with a traceback.
    """
    from pam.errors import PamError

    (a,) = make_items(conn, [("a.jpg", "u1")])
    (jid,) = repo.insert_intents(conn, "s1", "rename", [entry(a)], now=NOW)
    repo.resolve(conn, jid, "done", now=LATER)
    conn.commit()

    with pytest.raises(repo.JournalInvariantError) as excinfo:
        repo.resolve(conn, jid, "done", now=LATER)

    assert not isinstance(excinfo.value, ValueError)
    assert not isinstance(excinfo.value, PamError)
    assert not issubclass(repo.JournalInvariantError, (ValueError, PamError))


def test_resolve_does_not_commit(conn):
    """spec 7.0 J5 - one transaction per ITEM, and `resolve` is not the whole item.

    Task 5's RenameTransaction.commit() writes the journal 'done', the plan_item
    'applied' and the files row as ONE transaction. A commit inside resolve splits
    that triple, and a crash in the window leaves a 'done' journal row whose
    files.rel_path still names the old path - a state spec 7.2 can never correct,
    because recovery branches on disk and finds nothing left in 'intent'.

    Invisible to every other resolve test: they all commit immediately afterwards, so
    an internal commit changes nothing they can observe. The rollback is the only way
    to see it.
    """
    (a,) = make_items(conn, [("a.jpg", "u1")])
    (jid,) = repo.insert_intents(conn, "s1", "rename", [entry(a)], now=NOW)
    conn.commit()

    repo.resolve(conn, jid, "done", now=LATER)
    conn.rollback()

    (row,) = repo.session_rows(conn, "s1")
    assert row.state == "intent"
    assert row.resolved_at is None


def test_resolve_is_deliberately_not_idempotent(conn):
    """Re-resolving a terminal row raises rather than passing quietly (spec 7.0 J7).

    A silently idempotent resolve would hide a double replay, so recovery cannot get
    J7's convergence by re-issuing resolve; it gets it by re-querying
    rows_in_state('intent') on each pass, which returns fewer rows every time and
    eventually none. Pinned here because Task 8 depends on it and would otherwise
    discover it by watching pass 2 raise.
    """
    (a,) = make_items(conn, [("a.jpg", "u1")])
    (jid,) = repo.insert_intents(conn, "s1", "rename", [entry(a)], now=NOW)
    repo.resolve(conn, jid, "done", now=LATER)
    conn.commit()

    with pytest.raises(repo.JournalInvariantError):
        repo.resolve(conn, jid, "done", now=LATER)

    # The convergent route, run twice: idempotent, and empty after the first pass.
    assert [r.id for r in repo.rows_in_state(conn, "intent")] == []
    assert [r.id for r in repo.rows_in_state(conn, "intent")] == []


def test_resolve_touches_only_its_own_row(conn):
    a, b = make_items(conn, [("a.jpg", "u1"), ("b.jpg", "u1")])
    first, second = repo.insert_intents(
        conn, "s1", "rename", [entry(a, old="a.jpg"), entry(b, old="b.jpg")], now=NOW
    )
    conn.commit()

    repo.resolve(conn, first, "done", now=LATER)
    conn.commit()

    states = {r.id: r.state for r in repo.session_rows(conn, "s1")}
    assert states == {first: "done", second: "intent"}


# --- rows_in_state (spec 7.0 J3, the recovery query) ------------------------------


def test_rows_in_state_returns_only_that_state_oldest_first(conn):
    a, b, c = make_items(conn, [("a.jpg", "u1"), ("b.jpg", "u2"), ("c.jpg", "u3")])
    ids = repo.insert_intents(
        conn,
        "s1",
        "rename",
        [entry(a, old="a.jpg"), entry(b, old="b.jpg"), entry(c, old="c.jpg")],
        now=NOW,
    )
    repo.resolve(conn, ids[1], "done", now=LATER)
    conn.commit()

    assert [r.id for r in repo.rows_in_state(conn, "intent")] == [ids[0], ids[2]]
    assert [r.id for r in repo.rows_in_state(conn, "done")] == [ids[1]]
    assert repo.rows_in_state(conn, "reverted") == []


def test_rows_in_state_spans_sessions(conn):
    """Recovery runs at the start of every invocation and must find an interrupted
    row from ANY earlier session, not just the current one (spec 7.2).
    """
    a, b = make_items(conn, [("a.jpg", "u1"), ("b.jpg", "u2")])
    repo.insert_intents(conn, "s-old", "rename", [entry(a, old="a.jpg")], now=NOW)
    repo.insert_intents(conn, "s-new", "rename", [entry(b, old="b.jpg")], now=LATER)
    conn.commit()

    assert {r.session_id for r in repo.rows_in_state(conn, "intent")} == {"s-old", "s-new"}


@pytest.mark.parametrize("state", ["intnet", "INTENT", "applied", "", "pending"])
def test_rows_in_state_refuses_a_state_outside_the_closed_vocabulary(conn, state):
    """`resolve` had a closed vocabulary; its read counterpart did not.

    `rows_in_state(conn, "intnet")` returned [] - which is not "unknown state", it is
    "nothing to recover". That is the exact answer spec 7.5's check 4 reads as a clean
    archive, so a typo there reports health while a half-finished rename sits on disk.
    """
    (a,) = make_items(conn, [("a.jpg", "u1")])
    repo.insert_intents(conn, "s1", "rename", [entry(a)], now=NOW)
    conn.commit()

    with pytest.raises(repo.JournalInvariantError):
        repo.rows_in_state(conn, state)


def test_every_legal_journal_state_is_queryable(conn):
    """The other half of the closed vocabulary: the guard must not refuse a real
    state. All four appear in spec 7.0 J2's transition set plus the state every row
    starts in, and recovery, undo and the 7.5 check between them read all four.
    """
    for state in ("intent", "done", "failed", "reverted"):
        assert repo.rows_in_state(conn, state) == []


# --- session_rows (spec 7.4) ------------------------------------------------------


def test_session_rows_are_ascending_and_scoped_to_one_session(conn):
    a, b, c = make_items(conn, [("a.jpg", "u1"), ("b.jpg", "u2"), ("c.jpg", "u3")])
    mine = repo.insert_intents(
        conn, "s1", "rename", [entry(a, old="a.jpg"), entry(b, old="b.jpg")], now=NOW
    )
    repo.insert_intents(conn, "s2", "rename", [entry(c, old="c.jpg")], now=LATER)
    conn.commit()

    rows = repo.session_rows(conn, "s1")
    assert [r.id for r in rows] == sorted(mine)
    assert {r.session_id for r in rows} == {"s1"}


def test_session_rows_is_empty_for_an_unknown_session(conn):
    assert repo.session_rows(conn, "s-nope") == []


# --- unit_rows (spec 7.3 rev 6) ---------------------------------------------------


def test_unit_rows_groups_by_the_plans_frozen_unit_key(conn):
    """spec 7.3 rev 6: the unit is plan_items.unit_key, frozen at plan time. Never
    files.companion_group_id, which pam scan clears and recreates every scan.
    """
    heic, mov, other = make_items(
        conn, [("IMG_1.HEIC", "u-live"), ("IMG_1.MOV", "u-live"), ("z.jpg", "u-solo")]
    )
    ids = repo.insert_intents(
        conn,
        "s1",
        "rename",
        [
            entry(heic, old="IMG_1.HEIC"),
            entry(mov, old="IMG_1.MOV"),
            entry(other, old="z.jpg"),
        ],
        now=NOW,
    )
    conn.commit()

    unit = repo.unit_rows(conn, "s1", "u-live")
    assert [r.id for r in unit] == [ids[0], ids[1]]
    assert [r.old_rel_path for r in unit] == ["IMG_1.HEIC", "IMG_1.MOV"]
    assert [r.id for r in repo.unit_rows(conn, "s1", "u-solo")] == [ids[2]]


def test_unit_rows_does_not_cross_sessions(conn):
    """Two runs over the same unit are separate units of work: a revert in run 2
    must not touch run 1's rows.
    """
    a, b = make_items(conn, [("a.jpg", "u1"), ("b.jpg", "u1")])
    first = repo.insert_intents(conn, "s1", "rename", [entry(a, old="a.jpg")], now=NOW)
    repo.insert_intents(conn, "s2", "rename", [entry(b, old="b.jpg")], now=LATER)
    conn.commit()

    assert [r.id for r in repo.unit_rows(conn, "s1", "u1")] == first


def test_unit_rows_is_empty_for_an_unknown_unit(conn):
    (a,) = make_items(conn, [("a.jpg", "u1")])
    repo.insert_intents(conn, "s1", "rename", [entry(a)], now=NOW)
    conn.commit()

    assert repo.unit_rows(conn, "s1", "u-nope") == []


def test_unit_rows_ignores_the_live_companion_grouping(conn):
    """The bug this milestone exists to prevent, driven directly: two files that
    share a LIVE companion group but were planned into different units must not be
    returned together, and re-grouping them on disk must not change the answer.
    """
    heic, mov = make_items(conn, [("IMG_1.HEIC", "u-a"), ("IMG_1.MOV", "u-b")])
    ids = repo.insert_intents(
        conn,
        "s1",
        "rename",
        [entry(heic, old="IMG_1.HEIC"), entry(mov, old="IMG_1.MOV")],
        now=NOW,
    )
    files = repo.all_files(conn)
    members = [files["IMG_1.HEIC"].id, files["IMG_1.MOV"].id]
    repo.create_companion_group(conn, members[0], "live_photo", members)
    conn.commit()

    assert [r.id for r in repo.unit_rows(conn, "s1", "u-a")] == [ids[0]]
    assert [r.id for r in repo.unit_rows(conn, "s1", "u-b")] == [ids[1]]


# --- sessions ---------------------------------------------------------------------


def test_sessions_counts_every_journal_state_per_session(conn):
    a, b, c = make_items(conn, [("a.jpg", "u1"), ("b.jpg", "u2"), ("c.jpg", "u3")])
    one = repo.insert_intents(
        conn, "s-20260731-100000", "rename", [entry(a), entry(b)], now=NOW
    )
    two = repo.insert_intents(conn, "s-20260731-110000", "rename", [entry(c)], now=LATER)
    repo.resolve(conn, one[0], "done", now=LATER)
    repo.resolve(conn, one[1], "failed", now=LATER)
    repo.resolve(conn, two[0], "done", now=LATER)
    conn.commit()

    assert repo.sessions(conn) == [
        repo.SessionCounts(
            "s-20260731-100000",
            done=1,
            failed=1,
            reverted=0,
            intent=0,
            undone=0,
            ops=("rename",),
        ),
        repo.SessionCounts(
            "s-20260731-110000",
            done=1,
            failed=0,
            reverted=0,
            intent=0,
            undone=0,
            ops=("rename",),
        ),
    ]


def test_sessions_separates_an_unresolved_session_from_a_reverted_one(conn):
    """M5 open question 7, half one. Under the old `(id, done, failed)` shape BOTH of
    these read `(id, 0, 0)`: a run interrupted before spec 7.2's recovery could decide
    anything, and a companion unit spec 7.3 rolled back whole. They need opposite
    things from the user - the first needs any pam command run, the second needs
    nothing at all - and a chooser that renders them identically is a UX 1.4 dead end.
    """
    a, b = make_items(conn, [("a.jpg", "u1"), ("b.jpg", "u2")])
    unresolved = repo.insert_intents(conn, "s-crashed", "rename", [entry(a)], now=NOW)
    rolled_back = repo.insert_intents(conn, "s-reverted", "rename", [entry(b)], now=NOW)
    repo.resolve(conn, rolled_back[0], "done", now=LATER)
    repo.resolve(conn, rolled_back[0], "reverted", now=LATER)
    conn.commit()
    assert unresolved, "the fixture must actually leave a row in intent"

    assert repo.sessions(conn) == [
        repo.SessionCounts(
            "s-crashed", done=0, failed=0, reverted=0, intent=1, undone=0, ops=("rename",)
        ),
        repo.SessionCounts(
            "s-reverted", done=0, failed=0, reverted=1, intent=0, undone=0, ops=("rename",)
        ),
    ]


def test_sessions_reports_a_session_that_has_been_put_back_as_undone(conn):
    """M5 open question 7, half two, and the reason this query joins `plan_items` at
    all. spec 7.0 J2 keeps a `rename` row `done` FOREVER - undo appends new rows rather
    than rewriting old ones - so the journal alone cannot tell an applied session from
    one whose files are all back. spec 7.4 records that on the plan item, and this is
    where the two halves are put together.

    Both sessions below are `done=1`, and that is the point: the difference is entirely
    in the column that was not there before.
    """
    a, b = make_items(conn, [("a.jpg", "u1"), ("b.jpg", "u2")])
    still_applied = repo.insert_intents(conn, "s-applied", "rename", [entry(a)], now=NOW)
    put_back = repo.insert_intents(conn, "s-undone", "rename", [entry(b)], now=NOW)
    repo.resolve(conn, still_applied[0], "done", now=LATER)
    repo.resolve(conn, put_back[0], "done", now=LATER)
    repo.set_plan_item_status(conn, a, "applied")
    repo.set_plan_item_status(conn, b, "applied")
    repo.set_plan_item_status(conn, b, "undone")
    conn.commit()

    assert repo.sessions(conn) == [
        repo.SessionCounts(
            "s-applied", done=1, failed=0, reverted=0, intent=0, undone=0, ops=("rename",)
        ),
        repo.SessionCounts(
            "s-undone", done=1, failed=0, reverted=0, intent=0, undone=1, ops=("rename",)
        ),
    ]


def test_sessions_tells_an_apply_from_the_undo_that_reversed_it(conn):
    """M6 Task 5's ruling, and the state that forced it.

    Reproduced against an archive of real files before `ops` existed: apply two files, undo them,
    and BOTH sessions read `SessionCounts(done=2, failed=0, reverted=0, intent=0,
    undone=2)` - byte-identical - so `pam undo`'s chooser called the undo session "2
    renamed, already undone". The verb is false (those files were put back) and so is
    the offer (`run_undo` refuses an undo session outright). The five state counts here
    are deliberately identical for the two sessions: `ops` is the ONLY thing that
    separates them, which is what makes it load-bearing rather than decorative.
    """
    a, b = make_items(conn, [("a.jpg", "u1"), ("b.jpg", "u2")])
    applied = repo.insert_intents(conn, "s-apply", "rename", [entry(a)], now=NOW)
    put_back = repo.insert_intents(conn, "s-undo", "undo_rename", [entry(b)], now=NOW)
    repo.resolve(conn, applied[0], "done", now=LATER)
    repo.resolve(conn, put_back[0], "done", now=LATER)
    repo.set_plan_item_status(conn, a, "applied")
    repo.set_plan_item_status(conn, a, "undone")
    repo.set_plan_item_status(conn, b, "applied")
    repo.set_plan_item_status(conn, b, "undone")
    conn.commit()

    counts = {s.session_id: s for s in repo.sessions(conn)}
    assert counts["s-apply"].ops == ("rename",)
    assert counts["s-undo"].ops == ("undo_rename",)
    stripped = {
        session_id: (s.done, s.failed, s.reverted, s.intent, s.undone)
        for session_id, s in counts.items()
    }
    assert stripped["s-apply"] == stripped["s-undo"], (
        "the two sessions must stay indistinguishable WITHOUT `ops`, or this test has"
        " stopped pinning the thing it was written for"
    )


def test_sessions_reports_both_ops_of_a_session_that_holds_both(conn):
    """A session holding two ops is corruption pam cannot produce - and is reported.

    `next_session_id` gives every command a fresh id, so `rename` and `undo_rename` can
    never share one through pam. A hand-edited or partially restored catalog can, and
    the honest answer is both values rather than whichever row the query met first: a
    single-string field would have had to pick, and picking is what turns corruption
    into a confident wrong answer.
    """
    a, b = make_items(conn, [("a.jpg", "u1"), ("b.jpg", "u2")])
    repo.insert_intents(conn, "s-mixed", "rename", [entry(a)], now=NOW)
    repo.insert_intents(conn, "s-mixed", "undo_rename", [entry(b)], now=NOW)
    conn.commit()

    assert repo.sessions(conn)[0].ops == ("rename", "undo_rename")


def test_the_ops_of_a_mixed_session_do_not_depend_on_which_row_was_written_first(conn):
    """Review M378. The `sorted()` on `ops` is load-bearing, and the test above cannot
    see it: it writes `rename` first, and SQLite concatenates in scan order, which for
    that fixture is already alphabetical (`r` < `u`).

    Written the other way round, `GROUP_CONCAT(DISTINCT j.op)` returns
    `'undo_rename,rename'` (measured), so without the sort two catalogs holding the
    same session compare unequal purely because their rows were inserted in a different
    order - and `SessionCounts` is compared by whole-dataclass equality throughout this
    file. Same shape as M6 Task 4's M347: an order the fixture happens to satisfy is
    not an order the code guarantees.
    """
    a, b = make_items(conn, [("a.jpg", "u1"), ("b.jpg", "u2")])
    repo.insert_intents(conn, "s-mixed", "undo_rename", [entry(a)], now=NOW)
    repo.insert_intents(conn, "s-mixed", "rename", [entry(b)], now=NOW)
    conn.commit()

    assert repo.sessions(conn)[0].ops == ("rename", "undo_rename")


def test_session_counts_refuses_to_be_built_without_an_op():
    """Review M375. `ops` has NO default, and that is the guard, not a style choice.

    A default of `()` is silently wrong rather than absent: `describe_session` falls
    through to its apply branch and renders an op-less session as "N renamed" - the
    exact false verb the field was added to end - while `undoable_session` sees
    `ops != ("rename",)` and stops offering `pam undo` for a real apply. Both failures
    are quiet, so the constructor is where it has to be caught. Nothing else in the
    suite notices the default, because every `SessionCounts` in it comes from
    `repo.sessions`, which always supplies the field.
    """
    with pytest.raises(TypeError):
        repo.SessionCounts(  # type: ignore[call-arg]
            session_id="s-1", done=1, failed=0, reverted=0, intent=0, undone=0
        )


def test_a_session_whose_plan_item_went_missing_is_still_listed(conn):
    """The LEFT JOIN, and the only state on earth that tells it from an inner one.

    `journal.plan_item_id` is `NOT NULL REFERENCES plan_items(id)` and `db.connect` sets
    `PRAGMA foreign_keys=ON`, so pam itself cannot produce a dangling reference - which
    is exactly why turning this join into an INNER one passed all 996 tests (M6 Task 2
    review, mutation M265). A catalog restored from a partial backup or edited by hand
    can, though, and a corruptible catalog is the premise of the whole `pam verify`
    milestone; the constraint promises pam will not create this state, not that it
    cannot exist.

    An inner join answers that state by DROPPING THE SESSION OUT OF THE LISTING: the
    user is told pam knows no such session, about a session whose files it renamed, and
    `pam undo` becomes unreachable for the one session that most needs it (UX 1.4). The
    LEFT JOIN keeps the session visible with its journal counts intact and only `undone`
    under-reported - the docstring's promise, now asserted rather than stated.

    The FK is dropped and restored around the corruption because there is no other way
    to manufacture it; `PRAGMA foreign_keys` is a no-op inside a transaction, hence the
    commits either side.
    """
    (a,) = make_items(conn, [("a.jpg", "u1")])
    ids = repo.insert_intents(conn, "s-orphaned", "rename", [entry(a)], now=NOW)
    repo.resolve(conn, ids[0], "done", now=LATER)
    conn.commit()

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("DELETE FROM plan_items WHERE id = ?", (a,))
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")
    assert conn.execute("SELECT COUNT(*) FROM plan_items").fetchone()[0] == 0, (
        "the fixture must actually leave the journal row pointing at nothing"
    )

    assert repo.sessions(conn) == [
        repo.SessionCounts(
            "s-orphaned", done=1, failed=0, reverted=0, intent=0, undone=0, ops=("rename",)
        )
    ]


def test_sessions_is_empty_on_a_fresh_catalog(conn):
    assert repo.sessions(conn) == []


# --- next_session_id --------------------------------------------------------------


def test_next_session_id_uses_the_seconds_format(conn):
    assert repo.next_session_id(conn, "2026-07-31T10:30:55") == "s-20260731-103055"


def test_next_session_id_suffixes_a_genuine_collision(conn):
    """Seconds remove nearly every collision, but two scripted invocations inside one
    second are still possible - and reusing an id would merge two runs into one
    undoable session, which is worse than an ugly suffix.
    """
    a, b = make_items(conn, [("a.jpg", "u1"), ("b.jpg", "u2")])
    first = repo.next_session_id(conn, "2026-07-31T10:30:55")
    assert first == "s-20260731-103055"
    repo.insert_intents(conn, first, "rename", [entry(a)], now=NOW)
    conn.commit()

    second = repo.next_session_id(conn, "2026-07-31T10:30:55")
    assert second == "s-20260731-103055-2"
    repo.insert_intents(conn, second, "rename", [entry(b)], now=NOW)
    conn.commit()

    assert repo.next_session_id(conn, "2026-07-31T10:30:55") == "s-20260731-103055-3"


def test_next_session_id_is_unaffected_by_a_different_second(conn):
    (a,) = make_items(conn, [("a.jpg", "u1")])
    taken = repo.next_session_id(conn, "2026-07-31T10:30:55")
    repo.insert_intents(conn, taken, "rename", [entry(a)], now=NOW)
    conn.commit()

    assert repo.next_session_id(conn, "2026-07-31T10:30:56") == "s-20260731-103056"


def test_next_session_id_ignores_the_date_separator_style(conn):
    """Same instant, either ISO spelling: the id is derived from the parsed moment,
    not from string surgery on the timestamp.
    """
    assert repo.next_session_id(conn, "2026-07-31 10:30:55") == "s-20260731-103055"


# --- J2, structurally -------------------------------------------------------------


def _j2_sources():
    """Every file that could delete a journal row: the repo layer and the migrations.

    The migrations are in scope because J2's most plausible future violation is not a
    stray DELETE in Python - it is a migration doing a table rebuild (create-new,
    copy, drop-old), which the repo-only glob could not see at all.
    """
    from pathlib import Path

    import pam.catalog
    import pam.catalog.repo as repo_pkg

    repo_dir = Path(repo_pkg.__file__).parent
    migrations_dir = Path(pam.catalog.__file__).parent / "migrations"
    return sorted(repo_dir.glob("*.py")) + sorted(migrations_dir.glob("*.sql"))


def _normalized(path):
    """Upper-cased with every run of whitespace collapsed to one space.

    Matching raw text made the guard whitespace-fragile: `DELETE  FROM journal` (two
    spaces) and a multi-line `\"\"\"DELETE\\n FROM journal ...\"\"\"` both slipped
    through - and multi-line is the prevailing style in this very module, which is how
    insert_intents and unit_rows are written.
    """
    return " ".join(path.read_text(encoding="utf-8").upper().split())


def test_the_j2_source_set_covers_both_the_repo_layer_and_the_migrations():
    """A glob that quietly matches nothing is a guard that quietly passes."""
    names = {p.name for p in _j2_sources()}

    assert "journal.py" in names
    assert "0001_init.sql" in names
    assert sum(1 for p in _j2_sources() if p.suffix == ".sql") >= 4


def test_no_journal_row_is_ever_deleted_by_the_repo_layer():
    """spec 7.0 J2: rows are never deleted. Grepped rather than driven, because the
    property is 'no code path anywhere does this' - a behavioural test can only
    cover the paths someone thought of.
    """
    for path in _j2_sources():
        text = _normalized(path)
        assert "DELETE FROM JOURNAL" not in text, f"{path.name} deletes journal rows"
        assert "DROP TABLE JOURNAL" not in text, f"{path.name} drops the journal table"


@pytest.mark.parametrize(
    "planted",
    [
        'conn.execute("DELETE FROM journal WHERE id = ?", (1,))',  # the plain form
        'conn.execute("DELETE  FROM journal WHERE id = ?", (1,))',  # two spaces
        'conn.execute("""DELETE\n               FROM journal\n            """)',  # multi-line
    ],
)
def test_the_j2_guard_catches_every_spelling_of_a_delete(tmp_path, planted):
    """The guard proven ENFORCING, not merely present.

    Two of these three passed the pre-fix guard. Driven against a temp file rather
    than by editing the real module, so the proof is a committed test instead of a
    note in a review that a delete was planted once and reverted.
    """
    source = tmp_path / "planted.py"
    source.write_text(f"def f(conn):\n    {planted}\n", encoding="utf-8")

    assert "DELETE FROM JOURNAL" in _normalized(source)


def test_the_j2_guard_does_not_fire_on_an_unrelated_delete(tmp_path):
    """The other direction: deleting from a different table is legitimate, and a guard
    that fired on it would be routed around rather than kept.
    """
    source = tmp_path / "planted.py"
    source.write_text(
        'def f(conn):\n    conn.execute("DELETE FROM plan_items")\n', encoding="utf-8"
    )

    assert "DELETE FROM JOURNAL" not in _normalized(source)
