"""RenameTransaction - the journal state machine (spec 7.0 J1, J2, J5).

Two things make these tests different from ordinary catalog tests, and both are
deliberate.

A SECOND CONNECTION reads every durability assertion. Asking the writing connection
what it wrote proves nothing about what was committed: an uncommitted INSERT is
perfectly visible to the connection that issued it, so `conn.execute(...)` would
report a durable intent row for a transaction that had not committed at all - the
exact bug spec 7.0 J1 exists to forbid.

REAL FILES on a real temp filesystem, because the states under test are pairs: what
the journal says AND what is on disk. `dbfail:before_done_commit` is only interesting
because the rename physically happened while the row still says `intent`.
"""

import logging
import sqlite3
from dataclasses import replace

import pytest

from pam.catalog import repo
from pam.catalog.db import connect, migrate
from pam.catalog.repo.journal import JournalInvariantError
from pam.execute.journal_ops import RenameItem, RenameTransaction
from pam.execute.rename import perform_verified_rename

LOG = logging.getLogger("pam.tests.journal_ops")
NOW = "2026-07-31T10:30:55"
SESSION = "s-20260731-103055"


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "workspace" / "catalog.db"


@pytest.fixture()
def conn(db_path):
    db_path.parent.mkdir(parents=True)
    c = connect(db_path)
    migrate(c)
    yield c
    c.close()


@pytest.fixture()
def archive(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    return root


def other_connection(db_path):
    """A reader that has never seen this process's uncommitted work."""
    c = connect(db_path)
    c.row_factory = sqlite3.Row
    return c


def build_unit(conn, archive, names, *, unit_key="unit-1"):
    """Real files on disk, real catalog rows, real plan items -> RenameItems.

    Journal rows carry a plan_item_id under a live foreign key and the resolution
    writes touch `plan_items` and `files`, so nothing here can be faked without the
    test ceasing to exercise the thing it claims to.
    """
    plan_id = repo.create_plan(
        conn,
        now=NOW,
        template="{date}_{seq}",
        config_json="{}",
        app_version="0.1.0",
        schema_version=4,
    )
    items = []
    for old_rel, new_rel in names:
        (archive / old_rel).write_bytes(f"bytes of {old_rel}".encode())
        stat = (archive / old_rel).stat()
        file_id = repo.upsert_file(
            conn,
            rel_path=old_rel,
            filename=old_rel.rsplit("/", 1)[-1],
            ext=old_rel.rsplit(".", 1)[-1].lower(),
            media_type="image",
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            placeholder=False,
            now=NOW,
        )
        items.append((file_id, old_rel, new_rel, stat))
    repo.insert_plan_items(
        conn,
        plan_id,
        [
            repo.PlanItemDraft(
                file_id=file_id,
                old_rel_path=old_rel,
                unit_key=unit_key,
                generated_filename=new_rel.rsplit("/", 1)[-1],
                tokens_json="[]",
                tier="high",
                trust_score=100,
                trust_reasons_json="[]",
            )
            for file_id, old_rel, new_rel, _ in items
        ],
    )
    rows = repo.plan_items(conn, plan_id)
    for row in rows:
        # The typed confirmation is the approval in M5 (plan clarification 2); an
        # item is 'approved' by the time apply journals anything about it.
        repo.set_plan_item_status(conn, row.id, "approved")
    conn.commit()
    return [
        RenameItem(
            plan_item_id=row.id,
            file_id=file_id,
            old_rel_path=old_rel,
            new_rel_path=new_rel,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        )
        for row, (file_id, old_rel, new_rel, stat) in zip(rows, items, strict=True)
    ]


def transaction(conn, items, *, op="rename"):
    return RenameTransaction(conn, session_id=SESSION, op=op, items=items, logger=LOG)


def journal_states(conn):
    """Every journal row, whatever state it is in, as (old_rel_path, state)."""
    return [
        (row.old_rel_path, row.state)
        for state in ("intent", "done", "failed", "reverted")
        for row in repo.rows_in_state(conn, state)
    ]


def do_rename(archive, item):
    return perform_verified_rename(
        archive_root=archive,
        old_rel=item.old_rel_path,
        new_rel=item.new_rel_path,
        expect_size=item.size,
        expect_mtime_ns=item.mtime_ns,
        logger=LOG,
    )


# --- J1: write-ahead --------------------------------------------------------------


def test_begin_commits_the_intent_before_any_rename_could_happen(conn, db_path, archive):
    """spec 7.0 J1. The row is durable - visible to a connection that cannot see this
    one's open transaction - while the file is still sitting at its old name."""
    items = build_unit(conn, archive, [("IMG_1.JPG", "2019-07-04_001.JPG")])
    txn = transaction(conn, items)
    txn.begin()

    reader = other_connection(db_path)
    try:
        rows = repo.rows_in_state(reader, "intent")
        assert [(r.old_rel_path, r.new_rel_path, r.op) for r in rows] == [
            ("IMG_1.JPG", "2019-07-04_001.JPG", "rename")
        ]
        assert rows[0].size == items[0].size and rows[0].mtime_ns == items[0].mtime_ns
    finally:
        reader.close()
    assert (archive / "IMG_1.JPG").exists()
    assert not (archive / "2019-07-04_001.JPG").exists()


def test_a_whole_unit_of_intents_is_committed_together(conn, db_path, archive):
    """spec 7.3: a companion group's intents are all committed, THEN the renames run
    member by member. One member journaled and the other not would let recovery
    resolve half a group."""
    items = build_unit(
        conn, archive, [("IMG_1.JPG", "a.JPG"), ("IMG_1.MOV", "a.MOV")], unit_key="live-1"
    )
    transaction(conn, items).begin()

    reader = other_connection(db_path)
    try:
        assert sorted(r.old_rel_path for r in repo.rows_in_state(reader, "intent")) == [
            "IMG_1.JPG",
            "IMG_1.MOV",
        ]
    finally:
        reader.close()


def test_a_crash_before_the_intent_commit_leaves_no_trace_at_all(
    conn, db_path, archive, set_fault
):
    """spec 9.3 rev 7's new checkpoint. Nothing on disk, nothing in the journal - the
    strictly different assertion from `after_intent`, where the row must survive."""
    items = build_unit(conn, archive, [("IMG_1.JPG", "a.JPG"), ("IMG_1.MOV", "a.MOV")])
    set_fault.arm("crash:before_intent_commit:1")
    with pytest.raises(SystemExit):
        transaction(conn, items).begin()
    assert set_fault.hits("before_intent_commit") == 1

    reader = other_connection(db_path)
    try:
        assert repo.rows_in_state(reader, "intent") == []
    finally:
        reader.close()
    # And the write is gone from the writing connection too, not merely invisible
    # elsewhere: a rolled-back INSERT cannot be swept into a later commit.
    set_fault.next_invocation()
    assert repo.rows_in_state(conn, "intent") == []
    assert (archive / "IMG_1.JPG").exists()


def test_a_dbfail_before_the_intent_commit_cannot_be_swept_in_by_a_later_commit(
    conn, db_path, archive, set_fault
):
    """The in-process half of the same window. `dbfail:` is caught rather than fatal,
    so the connection lives on and the next item commits on it - and would carry the
    abandoned INSERTs with it if they had not been rolled back."""
    first = build_unit(conn, archive, [("IMG_1.JPG", "a.JPG")], unit_key="u1")
    second = build_unit(conn, archive, [("IMG_2.JPG", "b.JPG")], unit_key="u2")
    set_fault.arm("dbfail:before_intent_commit:1")
    with pytest.raises(sqlite3.OperationalError):
        transaction(conn, first).begin()

    set_fault.next_invocation()
    transaction(conn, second).begin()

    reader = other_connection(db_path)
    try:
        assert [r.old_rel_path for r in repo.rows_in_state(reader, "intent")] == ["IMG_2.JPG"]
    finally:
        reader.close()


def test_a_crash_after_the_intent_leaves_it_durable_for_recovery(
    conn, db_path, archive, set_fault
):
    """spec 7.0 J1 from the other side: past this point the intent MUST survive, or
    spec 7.2 has nothing to find and a rename could happen unrecorded."""
    items = build_unit(conn, archive, [("IMG_1.JPG", "a.JPG")])
    set_fault.arm("crash:after_intent:1")
    with pytest.raises(SystemExit):
        transaction(conn, items).begin()
    assert set_fault.hits("after_intent") == 1

    reader = other_connection(db_path)
    try:
        assert [r.state for r in repo.rows_in_state(reader, "intent")] == ["intent"]
    finally:
        reader.close()


# --- J5: one transaction per item -------------------------------------------------


def test_commit_writes_the_journal_the_item_and_the_files_row(conn, db_path, archive):
    items = build_unit(conn, archive, [("IMG_1.JPG", "2019-07-04_001.JPG")])
    txn = transaction(conn, items)
    txn.begin()
    assert do_rename(archive, items[0]).ok
    txn.commit(items[0])

    reader = other_connection(db_path)
    try:
        assert [r.state for r in repo.rows_in_state(reader, "done")] == ["done"]
        assert repo.rows_in_state(reader, "intent") == []
        assert set(repo.all_files(reader)) == {"2019-07-04_001.JPG"}
        assert repo.all_files(reader)["2019-07-04_001.JPG"].filename == "2019-07-04_001.JPG"
        assert [i.status for i in repo.plan_items(reader, 1)] == ["applied"]
    finally:
        reader.close()


def test_a_dbfail_before_the_done_commit_leaves_intent_plus_a_finished_rename(
    conn, db_path, archive, set_fault
):
    """spec 7.0 J5, and precisely the state spec 7.2 adopts (new exists, old absent).

    All three writes are absent together. Split into separate commits for "speed",
    the journal or the item or the files row would already be visible here, and a
    crash in that window is the one spec 7.2 cannot repair - it only ever looks at
    rows still in `intent`.
    """
    items = build_unit(conn, archive, [("IMG_1.JPG", "2019-07-04_001.JPG")])
    txn = transaction(conn, items)
    txn.begin()
    assert do_rename(archive, items[0]).ok
    set_fault.arm("dbfail:before_done_commit:1")
    with pytest.raises(sqlite3.OperationalError):
        txn.commit(items[0])
    assert set_fault.hits("before_done_commit") == 1

    assert (archive / "2019-07-04_001.JPG").exists()
    assert not (archive / "IMG_1.JPG").exists()
    reader = other_connection(db_path)
    try:
        assert [r.state for r in repo.rows_in_state(reader, "intent")] == ["intent"]
        assert repo.rows_in_state(reader, "done") == []
        assert set(repo.all_files(reader)) == {"IMG_1.JPG"}
        assert [i.status for i in repo.plan_items(reader, 1)] == ["approved"]
    finally:
        reader.close()


def test_a_crash_between_two_items_leaves_the_first_one_consistent_with_disk(
    conn, db_path, archive, set_fault
):
    """spec 7.0 J5's actual promise: never batch resolutions across items."""
    items = build_unit(conn, archive, [("IMG_1.JPG", "a.JPG"), ("IMG_2.JPG", "b.JPG")])
    txn = transaction(conn, items)
    txn.begin()
    assert do_rename(archive, items[0]).ok
    txn.commit(items[0])
    set_fault.arm("crash:after_rename:1")
    with pytest.raises(SystemExit):
        do_rename(archive, items[1])

    reader = other_connection(db_path)
    try:
        files = repo.all_files(reader)
        assert "a.JPG" in files and (archive / "a.JPG").exists()
        assert [(r.old_rel_path, r.state) for r in repo.rows_in_state(reader, "done")] == [
            ("IMG_1.JPG", "done")
        ]
        assert [r.old_rel_path for r in repo.rows_in_state(reader, "intent")] == ["IMG_2.JPG"]
    finally:
        reader.close()


# --- fail and revert --------------------------------------------------------------


def test_fail_resolves_the_row_and_leaves_the_files_row_alone(conn, db_path, archive):
    """The file never moved, so the catalog already describes what is on disk."""
    items = build_unit(conn, archive, [("IMG_1.JPG", "a.JPG")])
    txn = transaction(conn, items)
    txn.begin()
    txn.fail(items[0], "a file named a.JPG is already there, and pam never overwrites.")

    reader = other_connection(db_path)
    try:
        assert [r.state for r in repo.rows_in_state(reader, "failed")] == ["failed"]
        assert set(repo.all_files(reader)) == {"IMG_1.JPG"}
        assert [i.status for i in repo.plan_items(reader, 1)] == ["failed"]
    finally:
        reader.close()


def test_revert_returns_the_catalog_to_the_old_path_and_fails_the_item(conn, db_path, archive):
    """spec 7.3 / J6: the member was renamed, the unit failed, the file is back."""
    items = build_unit(conn, archive, [("IMG_1.JPG", "a.JPG")])
    txn = transaction(conn, items)
    txn.begin()
    assert do_rename(archive, items[0]).ok
    txn.commit(items[0])

    back = perform_verified_rename(
        archive_root=archive,
        old_rel="a.JPG",
        new_rel="IMG_1.JPG",
        expect_size=items[0].size,
        expect_mtime_ns=items[0].mtime_ns,
        logger=LOG,
    )
    assert back.ok
    txn.revert(items[0])

    reader = other_connection(db_path)
    try:
        assert [r.state for r in repo.rows_in_state(reader, "reverted")] == ["reverted"]
        assert set(repo.all_files(reader)) == {"IMG_1.JPG"}
        assert [i.status for i in repo.plan_items(reader, 1)] == ["failed"]
    finally:
        reader.close()


def test_an_undo_transaction_marks_its_item_undone(conn, db_path, archive):
    """spec 7.4: undo is journaled with op `undo_rename`, and the plan item it
    replayed backwards is `undone`, not `applied`."""
    items = build_unit(conn, archive, [("a.JPG", "IMG_1.JPG")])
    txn = transaction(conn, items, op="undo_rename")
    txn.begin()
    assert do_rename(archive, items[0]).ok
    txn.commit(items[0])

    reader = other_connection(db_path)
    try:
        assert [(r.op, r.state) for r in repo.rows_in_state(reader, "done")] == [
            ("undo_rename", "done")
        ]
        assert [i.status for i in repo.plan_items(reader, 1)] == ["undone"]
    finally:
        reader.close()


@pytest.mark.parametrize("resolution", ["fail", "revert"])
def test_an_undo_that_does_not_move_the_file_leaves_the_item_applied(
    conn, db_path, archive, resolution
):
    """The mirror of the two tests above, for op `undo_rename`.

    An undo member that fails, or that is put back because a companion of its unit
    failed, ends with the file at its APPLIED name - exactly where it was before the
    undo started. `failed` would claim the original apply failed: it contradicts the
    file on disk, it would surface as a discrepancy in spec 7.5's check 4, and nothing
    went wrong except an undo that declined to guess (vision A8).
    """
    items = build_unit(conn, archive, [("2019-07-04_001.JPG", "IMG_1.JPG")])
    txn = transaction(conn, items, op="undo_rename")
    txn.begin()
    if resolution == "fail":
        txn.fail(items[0], "the original name is taken")
    else:
        assert do_rename(archive, items[0]).ok
        txn.commit(items[0])
        back = perform_verified_rename(
            archive_root=archive,
            old_rel="IMG_1.JPG",
            new_rel="2019-07-04_001.JPG",
            expect_size=items[0].size,
            expect_mtime_ns=items[0].mtime_ns,
            logger=LOG,
        )
        assert back.ok
        txn.revert(items[0])

    reader = other_connection(db_path)
    try:
        assert [i.status for i in repo.plan_items(reader, 1)] == ["applied"]
        assert set(repo.all_files(reader)) == {"2019-07-04_001.JPG"}
        assert (archive / "2019-07-04_001.JPG").exists()
    finally:
        reader.close()


# --- J2: the state machine cannot be walked backwards -----------------------------


def test_resolving_twice_raises_rather_than_silently_succeeding(conn, archive):
    """spec 7.0 J2. `resolve` is deliberately not idempotent, and RenameTransaction
    must not paper over that: a second resolution means a double replay."""
    items = build_unit(conn, archive, [("IMG_1.JPG", "a.JPG")])
    txn = transaction(conn, items)
    txn.begin()
    assert do_rename(archive, items[0]).ok
    txn.commit(items[0])
    with pytest.raises(JournalInvariantError):
        txn.commit(items[0])


def test_a_committed_item_cannot_then_be_failed(conn, archive):
    items = build_unit(conn, archive, [("IMG_1.JPG", "a.JPG")])
    txn = transaction(conn, items)
    txn.begin()
    assert do_rename(archive, items[0]).ok
    txn.commit(items[0])
    with pytest.raises(JournalInvariantError):
        txn.fail(items[0], "too late")


def test_an_unrenamed_item_cannot_be_reverted(conn, archive):
    """`reverted` may only follow `done`: reverting an intent would claim a rename
    back that never happened."""
    items = build_unit(conn, archive, [("IMG_1.JPG", "a.JPG")])
    txn = transaction(conn, items)
    txn.begin()
    with pytest.raises(JournalInvariantError):
        txn.revert(items[0])


def test_an_illegal_transition_leaves_the_row_and_the_catalog_untouched(conn, db_path, archive):
    items = build_unit(conn, archive, [("IMG_1.JPG", "a.JPG")])
    txn = transaction(conn, items)
    txn.begin()
    with pytest.raises(JournalInvariantError):
        txn.revert(items[0])

    reader = other_connection(db_path)
    try:
        assert [r.state for r in repo.rows_in_state(reader, "intent")] == ["intent"]
        assert set(repo.all_files(reader)) == {"IMG_1.JPG"}
        assert [i.status for i in repo.plan_items(reader, 1)] == ["approved"]
    finally:
        reader.close()


# --- refusing to resolve what was never journaled ---------------------------------


def test_resolving_before_begin_raises_loudly(conn, archive):
    """spec 7.0 J1: a resolution with no intent row behind it means a rename was
    performed with no write-ahead record. pam contradicting itself, never a user
    error - so it raises the type that is outside PamError and ValueError alike."""
    items = build_unit(conn, archive, [("IMG_1.JPG", "a.JPG")])
    txn = transaction(conn, items)
    with pytest.raises(JournalInvariantError, match="begin"):
        txn.commit(items[0])


def test_begin_refuses_to_run_twice(conn, archive, db_path):
    """`journal` has no UNIQUE on plan_item_id, so a second begin() would write a
    second set of intent rows and orphan the first. Those orphans are unreachable by
    this transaction and survive forever: spec 7.2's recovery would adopt each one by
    branching on disk, producing several `done` rows for one plan_item, and the J6
    unit-consistency pass could then revert a member whose rename was correct."""
    items = build_unit(conn, archive, [("IMG_1.JPG", "a.JPG")])
    txn = transaction(conn, items)
    txn.begin()
    with pytest.raises(JournalInvariantError, match="already run"):
        txn.begin()

    reader = other_connection(db_path)
    try:
        assert len(repo.rows_in_state(reader, "intent")) == len(items)
    finally:
        reader.close()


def test_resolving_with_an_item_from_another_transaction_is_refused(conn, archive):
    """_journal_ids is keyed by plan_item_id alone, so an item carrying a matching id
    but a different new_rel_path would resolve the right row while writing a false
    files.rel_path. RenameItem is frozen, so membership compares by value."""
    items = build_unit(conn, archive, [("IMG_1.JPG", "a.JPG")])
    txn = transaction(conn, items)
    txn.begin()
    impostor = replace(items[0], new_rel_path="somewhere_else.JPG")
    with pytest.raises(JournalInvariantError, match="not a member"):
        txn.commit(impostor)


def test_an_unknown_op_is_refused_at_construction(conn, archive):
    items = build_unit(conn, archive, [("IMG_1.JPG", "a.JPG")])
    with pytest.raises(JournalInvariantError):
        transaction(conn, items, op="move")


def test_an_empty_transaction_is_refused(conn):
    with pytest.raises(JournalInvariantError):
        transaction(conn, [])


# --- the journal is append-only ---------------------------------------------------


def test_no_path_through_the_transaction_ever_deletes_a_row(conn, db_path, archive):
    """spec 7.0 J2: rows are never deleted. Every terminal state adds information;
    spec 7.4's promise that undo works indefinitely rests on the whole history
    surviving."""
    items = build_unit(conn, archive, [("IMG_1.JPG", "a.JPG"), ("IMG_2.JPG", "b.JPG")])
    txn = transaction(conn, items)
    txn.begin()
    assert do_rename(archive, items[0]).ok
    txn.commit(items[0])
    txn.fail(items[1], "target occupied")

    reader = other_connection(db_path)
    try:
        assert sorted(journal_states(reader)) == [
            ("IMG_1.JPG", "done"),
            ("IMG_2.JPG", "failed"),
        ]
    finally:
        reader.close()


# --- adopt(): binding rows a previous invocation wrote (spec 7.2) -----------------


def test_adopt_binds_existing_rows_so_recovery_can_resolve_them(conn, db_path, archive):
    """spec 7.2's alternate constructor. Recovery resolves rows written by a process
    that is gone, so it must NOT call `begin()` - that would insert a second set of
    intent rows and orphan the first, rows nothing can ever resolve."""
    items = build_unit(conn, archive, [("IMG_1.JPG", "2019-07-04_001.JPG")])
    transaction(conn, items).begin()
    assert do_rename(archive, items[0]).ok

    rows = repo.rows_in_state(conn, "intent")
    adopted = RenameTransaction.adopt(conn, rows, logger=LOG)
    assert adopted.items == tuple(items)
    adopted.commit(adopted.items[0])

    reader = other_connection(db_path)
    try:
        assert journal_states(reader) == [("IMG_1.JPG", "done")]
        assert repo.all_files(reader)["2019-07-04_001.JPG"].id == items[0].file_id
    finally:
        reader.close()


def test_an_adopted_transaction_refuses_to_begin(conn, archive):
    """A second set of intent rows for one plan_item would be unresolvable forever,
    and spec 7.2 would later adopt them as extra `done` rows for one file."""
    items = build_unit(conn, archive, [("IMG_1.JPG", "a.JPG")])
    transaction(conn, items).begin()

    adopted = RenameTransaction.adopt(conn, repo.rows_in_state(conn, "intent"), logger=LOG)

    with pytest.raises(JournalInvariantError):
        adopted.begin()


def test_adopt_refuses_rows_from_two_sessions(conn, archive):
    """A unit is `(session_id, unit_key)`: an apply and its later undo legitimately
    share a unit_key, and resolving one session's rows under the other's rules would
    write the wrong plan_item status - `_STATUS_AFTER_NO_CHANGE` alone differs."""
    items = build_unit(conn, archive, [("IMG_1.JPG", "a.JPG"), ("IMG_2.JPG", "b.JPG")])
    transaction(conn, items[:1]).begin()
    RenameTransaction(
        conn, session_id="s-20260731-999999", op="rename", items=items[1:], logger=LOG
    ).begin()

    with pytest.raises(JournalInvariantError):
        RenameTransaction.adopt(conn, repo.rows_in_state(conn, "intent"), logger=LOG)


def test_adopt_refuses_an_empty_row_list(conn):
    with pytest.raises(JournalInvariantError):
        RenameTransaction.adopt(conn, [], logger=LOG)


def test_adopt_carries_the_journals_own_size_and_mtime_not_a_fresh_read(conn, archive):
    """Recovery may run days later. The snapshot the rename was verified against is on
    the journal row, so a file edited since the crash is left alone rather than moved
    on top of whatever is now at the other name."""
    items = build_unit(conn, archive, [("IMG_1.JPG", "a.JPG")])
    transaction(conn, items).begin()
    (archive / "IMG_1.JPG").write_bytes(b"edited by something else, much longer now")

    adopted = RenameTransaction.adopt(conn, repo.rows_in_state(conn, "intent"), logger=LOG)

    assert adopted.items[0].size == items[0].size
    assert adopted.items[0].mtime_ns == items[0].mtime_ns


# --- J11: a resolution is never blocked by the catalog ----------------------------


def test_a_commit_resolves_even_when_another_row_owns_the_new_path(conn, db_path, archive):
    """spec 7.0 J11. `files.rel_path` is UNIQUE and rows are never deleted, so a path
    can be owned by a row describing a file that is gone. The journal is the record of
    what pam DID; `files` is a cache the next scan rebuilds. So the row reaches `done`,
    the `files` write is skipped, and the discrepancy is reported - never raised, on a
    path that runs at the start of every invocation."""
    items = build_unit(conn, archive, [("IMG_1.JPG", "2019-07-04_001.JPG")])
    ghost = repo.upsert_file(
        conn,
        rel_path="2019-07-04_001.JPG",
        filename="2019-07-04_001.JPG",
        ext="jpg",
        media_type="image",
        size=1,
        mtime_ns=1,
        placeholder=False,
        now=NOW,
    )
    conn.commit()
    txn = transaction(conn, items)
    txn.begin()
    assert do_rename(archive, items[0]).ok

    discrepancy = txn.commit(items[0])

    assert discrepancy is not None and "2019-07-04_001.JPG" in discrepancy
    reader = other_connection(db_path)
    try:
        assert journal_states(reader) == [("IMG_1.JPG", "done")]
        # The ghost still owns the path and the moved file's row is stale - both true,
        # both fixed by the next scan, and neither able to block the resolution.
        assert repo.all_files(reader)["2019-07-04_001.JPG"].id == ghost
        assert repo.all_files(reader)["IMG_1.JPG"].id == items[0].file_id
    finally:
        reader.close()


def test_a_revert_resolves_even_when_another_row_owns_the_old_path(conn, db_path, archive):
    """The other half of J11, and the one that was reproduced as a live defect: the
    rename succeeded, `set_file_path` raised IntegrityError, the whole transaction
    rolled back, and the file was left moved with its resolution undone."""
    items = build_unit(conn, archive, [("IMG_1.JPG", "2019-07-04_001.JPG")])
    txn = transaction(conn, items)
    txn.begin()
    assert do_rename(archive, items[0]).ok
    txn.commit(items[0])
    # A scan runs - exactly what pam's own stranded message tells the user to do - and
    # records something at the old name.
    repo.upsert_file(
        conn,
        rel_path="IMG_1.JPG",
        filename="IMG_1.JPG",
        ext="jpg",
        media_type="image",
        size=1,
        mtime_ns=1,
        placeholder=False,
        now=NOW,
    )
    conn.commit()
    assert perform_verified_rename(
        archive_root=archive,
        old_rel="2019-07-04_001.JPG",
        new_rel="IMG_1.JPG",
        expect_size=items[0].size,
        expect_mtime_ns=items[0].mtime_ns,
        logger=LOG,
    ).ok

    discrepancy = txn.revert(items[0])

    assert discrepancy is not None and "IMG_1.JPG" in discrepancy
    reader = other_connection(db_path)
    try:
        # The file went back and the row says so: `reverted`, not a rolled-back `done`.
        assert journal_states(reader) == [("IMG_1.JPG", "reverted")]
    finally:
        reader.close()


def test_an_ordinary_commit_reports_no_discrepancy(conn, archive):
    """The negative half: the guard must be inert on the path apply takes every time,
    or every successful rename would report a catalog problem it does not have."""
    items = build_unit(conn, archive, [("IMG_1.JPG", "a.JPG")])
    txn = transaction(conn, items)
    txn.begin()
    assert do_rename(archive, items[0]).ok

    assert txn.commit(items[0]) is None


def test_the_integrity_guard_resolves_the_row_when_the_owner_check_is_blind(
    conn, db_path, archive, monkeypatch
):
    """J11's SECOND layer, proven by disabling the first.

    The `path_owner` precondition makes the IntegrityError guard unreachable, which is
    what a second layer is for and also why no ordinary test can pin it: deleting the
    guard leaves the whole suite green. So the check is blinded here and the exception
    path is driven directly. What it must produce is what J11 requires - the row
    reaches its terminal state, the `files` write is abandoned rather than the
    resolution, and nothing is raised out of a code path that runs at the start of
    every invocation.

    Blinding one layer to test the one behind it is the only way to test defence in
    depth. The mutation that proves it is "delete the except sqlite3.IntegrityError";
    without this test it survives.
    """
    items = build_unit(conn, archive, [("IMG_1.JPG", "2019-07-04_001.JPG")])
    repo.upsert_file(
        conn,
        rel_path="2019-07-04_001.JPG",
        filename="2019-07-04_001.JPG",
        ext="jpg",
        media_type="image",
        size=1,
        mtime_ns=1,
        placeholder=False,
        now=NOW,
    )
    conn.commit()
    txn = transaction(conn, items)
    txn.begin()
    assert do_rename(archive, items[0]).ok
    monkeypatch.setattr(repo, "path_owner", lambda _conn, _rel_path: None)

    discrepancy = txn.commit(items[0])

    assert discrepancy is not None and "UNIQUE" in discrepancy
    reader = other_connection(db_path)
    try:
        assert journal_states(reader) == [("IMG_1.JPG", "done")]
    finally:
        reader.close()
