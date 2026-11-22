"""L1 (design rev 32, M13 Task 13a): no catalog statement binds an archive-sized variable list.

M13 Task 13 measured `pam plan` exiting 2 at 100,000 files with `sqlite3.OperationalError: too
many SQL variables`: `events.py::_delete_all_but` bound one `?` per kept event into one `NOT IN`.
Every `catalog/repo/` statement whose placeholder list grows with the archive is driven here PAST
THE RUNNING BUILD'S OWN LIMIT, read from the connection, not assumed - so the tests stay honest on
a build compiled with a different `SQLITE_MAX_VARIABLE_NUMBER`.

EVERY ROW IS PLANTED AND READ BACK WITH THE SUITE'S OWN SQL; only the function under test is
production. Each expectation is spelled from the spec sentence it checks (which rows a delete
must remove, which a write must touch), never from another production query.

THE CROSS-CHUNK CASE IS THE POINT OF THE EVENTS TEST. A delete-all-but split into chunks of
`NOT IN` deletes, in each chunk, every other chunk's kept ids. So the test keeps MORE ids than
the limit and asserts that every one of them survives with its id unchanged, and that exactly
the unkept rows went.
"""

import sqlite3
from datetime import datetime, timedelta

import pytest

from pam.catalog import repo
from pam.catalog.db import connect, migrate
from pam.events.grouping import Event

NOW = "2026-10-22T10:00:00"


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "catalog.db")
    migrate(conn)
    yield conn
    conn.close()


def over_limit(conn: sqlite3.Connection) -> int:
    """One more than this build's bound-variable limit, measured on the connection itself."""
    limit = conn.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)
    assert limit >= 999, limit  # SQLite's historical minimum default; anything less is odd
    return limit + 1


def past_the_limit(conn: sqlite3.Connection, ids) -> None:
    """Each GROWS test's OWN list is past the limit, read from the build here and not through
    `over_limit`: the control guards that helper, not a test's local count, and lowering one
    test's count with its fix reverted was green (M2586, review)."""
    limit = conn.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)
    assert len(set(ids)) > limit, (len(set(ids)), limit)


def test_the_build_refuses_a_statement_past_its_own_variable_limit(db):
    """The control: without it, every test below could pass on a build with no limit at all."""
    n = over_limit(db)
    with pytest.raises(sqlite3.OperationalError, match="too many SQL variables"):
        db.execute(f"SELECT 1 WHERE 1 IN ({','.join('?' * n)})", tuple(range(n)))


# --- events.py: replace_events -> _delete_all_but -------------------------------------------


def _spans(n: int) -> list[Event]:
    """`n` events with distinct one-second spans and no members (file_events needs no files)."""
    base = datetime(2001, 1, 1)
    events = []
    for i in range(n):
        dt = (base + timedelta(seconds=i)).isoformat()
        events.append(Event(file_ids=(), start_dt=dt, end_dt=dt))
    return events


def test_replace_events_keeps_more_events_than_the_limit_and_deletes_exactly_the_rest(db):
    n = over_limit(db) + 5
    events = _spans(n + 3)
    first = repo.replace_events(db, events, gap_hours=4, gps_km=50)
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == n + 3

    # The second run keeps the first n spans: spec 8.3 reuses every one of them by id, and the
    # three spans this run did not produce are the only rows that may go.
    past_the_limit(db, first[:n])
    second = repo.replace_events(db, events[:n], gap_hours=4, gps_km=50)
    db.commit()
    assert second == first[:n]
    stored = {int(r[0]) for r in db.execute("SELECT id FROM events")}
    assert stored == set(first[:n])
    assert stored.isdisjoint(first[n:])


def test_replace_events_with_nothing_kept_still_empties_the_table(db):
    repo.replace_events(db, _spans(4), gap_hours=4, gps_km=50)
    repo.replace_events(db, [], gap_hours=4, gps_km=50)
    assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


# --- faces.py: assign_faces / set_review_state ---------------------------------------------


def _plant_faces(conn: sqlite3.Connection, n: int) -> list[int]:
    conn.executemany(
        "INSERT INTO face_instances (sha256, bbox_json, embedding, det_confidence,"
        " model_name, model_version) VALUES (?, '{}', x'01', 0.9, 'm', '1')",
        [(f"{i:064x}",) for i in range(n)],
    )
    conn.commit()
    return [int(r[0]) for r in conn.execute("SELECT id FROM face_instances ORDER BY id")]


def _plant_person(conn: sqlite3.Connection, label: str) -> int:
    cur = conn.execute("INSERT INTO persons (label) VALUES (?)", (label,))
    conn.commit()
    return int(cur.lastrowid)


def test_assign_faces_moves_more_faces_than_the_limit_and_no_other(db):
    n = over_limit(db) + 5
    ids = _plant_faces(db, n + 2)
    person = _plant_person(db, "Person A")
    chosen = ids[:n]
    past_the_limit(db, chosen)
    # A repeated id is one face: the single-statement `IN` counted it once, and so must this.
    touched = repo.assign_faces(db, [*chosen, chosen[0], chosen[-1]], person)
    db.commit()
    assert touched == n
    assigned = {
        int(r[0])
        for r in db.execute("SELECT id FROM face_instances WHERE person_id = ?", (person,))
    }
    assert assigned == set(chosen)

    assert repo.assign_faces(db, chosen, None) == n
    db.commit()
    assert (
        db.execute(
            "SELECT COUNT(*) FROM face_instances WHERE person_id IS NOT NULL"
        ).fetchone()[0]
        == 0
    )


def test_set_review_state_marks_more_faces_than_the_limit_and_no_other(db):
    n = over_limit(db) + 5
    ids = _plant_faces(db, n + 2)
    chosen = ids[2:]
    past_the_limit(db, chosen)
    touched = repo.set_review_state(db, [*chosen, chosen[3]], "dismissed")
    db.commit()
    assert touched == n
    marked = {
        int(r[0])
        for r in db.execute("SELECT id FROM face_instances WHERE review_state = 'dismissed'")
    }
    assert marked == set(chosen)

    assert repo.set_review_state(db, chosen, None) == n
    db.commit()
    assert (
        db.execute(
            "SELECT COUNT(*) FROM face_instances WHERE review_state IS NOT NULL"
        ).fetchone()[0]
        == 0
    )


# --- plan_items.py: plan_items_by_id / plan_ids_for_items ----------------------------------


def _plant_plan_items(conn: sqlite3.Connection, n: int) -> tuple[list[int], list[int]]:
    """`n` items split across two plans, the second plan's items at the END of the id range."""
    conn.executemany(
        "INSERT INTO files (rel_path, filename, ext, media_type, size, mtime_ns, status,"
        " first_seen_at, last_scanned_at, placeholder)"
        " VALUES (?, ?, 'jpg', 'image', 1, 1, 'present', ?, ?, 0)",
        [(f"f{i}.jpg", f"f{i}.jpg", NOW, NOW) for i in range(n)],
    )
    plans = []
    for _ in range(2):
        cur = conn.execute(
            "INSERT INTO plans (created_at, status, template, config_json, app_version,"
            " schema_version, already_conforming) VALUES (?, 'open', 't', '{}', '0', 7, 0)",
            (NOW,),
        )
        plans.append(int(cur.lastrowid))
    file_ids = [int(r[0]) for r in conn.execute("SELECT id FROM files ORDER BY id")]
    conn.executemany(
        "INSERT INTO plan_items (plan_id, file_id, old_rel_path, generated_filename,"
        " new_filename, tokens_json, tier, trust_score, trust_reasons_json, status, unit_key)"
        " VALUES (?, ?, ?, 'n.jpg', 'n.jpg', '{}', 'high', 100, '[]', 'proposed', ?)",
        [
            (plans[0] if i < n - 1 else plans[1], fid, f"f{i}.jpg", f"u{i}")
            for i, fid in enumerate(file_ids)
        ],
    )
    conn.commit()
    item_ids = [int(r[0]) for r in conn.execute("SELECT id FROM plan_items ORDER BY id")]
    return item_ids, plans


def test_plan_items_by_id_reads_more_items_than_the_limit(db):
    n = over_limit(db) + 5
    item_ids, _ = _plant_plan_items(db, n)
    past_the_limit(db, item_ids)
    rows = repo.plan_items_by_id(db, [*item_ids, item_ids[0]])
    assert set(rows) == set(item_ids)
    assert all(rows[i].id == i for i in item_ids)


def test_plan_ids_for_items_sees_a_plan_whose_only_item_is_past_the_limit(db):
    n = over_limit(db) + 5
    item_ids, plans = _plant_plan_items(db, n)
    past_the_limit(db, item_ids[:-1])
    assert repo.plan_ids_for_items(db, item_ids) == sorted(plans)
    assert repo.plan_ids_for_items(db, item_ids[:-1]) == [plans[0]]


def test_plan_ids_for_items_are_ascending_where_a_set_would_not_be(db):
    """The docstring's "ascending" (the old `ORDER BY plan_id`) is now `sorted()` over a set,
    and plans 1 and 2 iterate ascending as a set anyway. Plans 1 and 8 do not: CPython's set
    of {1, 8} iterates 8 first, so this case tells `sorted()` from `list()` (M2571, review)."""
    plans = []
    for _ in range(8):
        cur = db.execute(
            "INSERT INTO plans (created_at, status, template, config_json, app_version,"
            " schema_version, already_conforming) VALUES (?, 'open', 't', '{}', '0', 7, 0)",
            (NOW,),
        )
        plans.append(int(cur.lastrowid))
    first, last = plans[0], plans[-1]
    # The fixture's power, checked without production: a set of these two ids is NOT ascending.
    assert list({first, last}) != sorted({first, last}), (first, last)
    db.executemany(
        "INSERT INTO files (rel_path, filename, ext, media_type, size, mtime_ns, status,"
        " first_seen_at, last_scanned_at, placeholder)"
        " VALUES (?, ?, 'jpg', 'image', 1, 1, 'present', ?, ?, 0)",
        [(f"f{i}.jpg", f"f{i}.jpg", NOW, NOW) for i in range(2)],
    )
    file_ids = [int(r[0]) for r in db.execute("SELECT id FROM files ORDER BY id")]
    db.executemany(
        "INSERT INTO plan_items (plan_id, file_id, old_rel_path, generated_filename,"
        " new_filename, tokens_json, tier, trust_score, trust_reasons_json, status, unit_key)"
        " VALUES (?, ?, ?, 'n.jpg', 'n.jpg', '{}', 'high', 100, '[]', 'proposed', ?)",
        [(last, file_ids[0], "f0.jpg", "u0"), (first, file_ids[1], "f1.jpg", "u1")],
    )
    db.commit()
    item_ids = [int(r[0]) for r in db.execute("SELECT id FROM plan_items ORDER BY id")]
    assert repo.plan_ids_for_items(db, item_ids) == [first, last]
