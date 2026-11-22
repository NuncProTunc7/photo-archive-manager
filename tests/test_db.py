import sqlite3
from pathlib import Path

import pytest

import pam.catalog.db
from pam.catalog.db import connect, migrate
from pam.errors import MigrationError

EXPECTED_TABLES = {
    "schema_migrations",
    "files",
    "companion_groups",
    "capture",
    "inferred_facts",
    "user_facts",
    "persons",
    "face_instances",
    "events",
    "file_events",
    "plans",
    "plan_items",
    "journal",
    "enrichment_runs",
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {r["name"] for r in rows}


def test_migrate_creates_all_tables(tmp_path):
    conn = connect(tmp_path / "catalog.db")
    migrate(conn)
    assert EXPECTED_TABLES <= _tables(conn)


def test_pragmas(tmp_path):
    conn = connect(tmp_path / "catalog.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migrate_is_idempotent(tmp_path):
    conn = connect(tmp_path / "catalog.db")
    migrate(conn)
    migrate(conn)  # must not raise or duplicate
    count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    assert count == 8  # one row per shipped migration file (0008: M14 Task 0C, OD-6 (a))


def test_changed_applied_migration_refuses(tmp_path):
    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir()
    first = mig_dir / "0001_a.sql"
    first.write_text("CREATE TABLE t1 (id INTEGER PRIMARY KEY);", encoding="utf-8")
    conn = connect(tmp_path / "catalog.db")
    migrate(conn, migrations_dir=mig_dir)
    first.write_text("CREATE TABLE t1 (id INTEGER PRIMARY KEY, extra TEXT);", encoding="utf-8")
    with pytest.raises(MigrationError, match="0001_a.sql"):
        migrate(conn, migrations_dir=mig_dir)


def test_new_migration_applies_on_top(tmp_path):
    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir()
    (mig_dir / "0001_a.sql").write_text(
        "CREATE TABLE t1 (id INTEGER PRIMARY KEY);", encoding="utf-8"
    )
    conn = connect(tmp_path / "catalog.db")
    migrate(conn, migrations_dir=mig_dir)
    (mig_dir / "0002_b.sql").write_text(
        "CREATE TABLE t2 (id INTEGER PRIMARY KEY);", encoding="utf-8"
    )
    migrate(conn, migrations_dir=mig_dir)
    assert {"t1", "t2"} <= _tables(conn)


def test_foreign_keys_enforced(tmp_path):
    conn = connect(tmp_path / "catalog.db")
    migrate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO file_events (file_id, event_id) VALUES (999, 999)")


def test_recorded_checksum_matches_file(tmp_path):
    import hashlib

    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir()
    first = mig_dir / "0001_a.sql"
    first.write_text("CREATE TABLE t1 (id INTEGER PRIMARY KEY);", encoding="utf-8")
    conn = connect(tmp_path / "catalog.db")
    migrate(conn, migrations_dir=mig_dir)
    row = conn.execute(
        "SELECT sha256 FROM schema_migrations WHERE filename = '0001_a.sql'"
    ).fetchone()
    expected = hashlib.sha256(first.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
    assert row["sha256"] == expected


def test_failed_migration_rolls_back_completely(tmp_path):
    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir()
    (mig_dir / "0001_bad.sql").write_text(
        "CREATE TABLE good_one (id INTEGER PRIMARY KEY);\n"
        "CREATE TABLE broken (id INTEGER PRIMARY KEY;\n",  # deliberate syntax error
        encoding="utf-8",
    )
    conn = connect(tmp_path / "catalog.db")
    with pytest.raises(MigrationError, match="rolled back"):
        migrate(conn, migrations_dir=mig_dir)
    assert "good_one" not in _tables(conn)  # partial DDL must not survive
    count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    assert count == 0


def test_migration_0003_clears_video_hashes(tmp_path):
    # C13: changing the video arg list invalidates video capture rows, so the
    # rev-3 one-time backfill is re-armed by NULLing their sha256 (spec 5.5).
    import shutil
    from pathlib import Path

    import pam.catalog.db as db_module

    shipped = Path(db_module.__file__).parent / "migrations"
    mig_up_to_0002 = tmp_path / "mig"
    mig_up_to_0002.mkdir()
    for name in ("0001_init.sql", "0002_scanner.sql"):
        shutil.copy(shipped / name, mig_up_to_0002 / name)

    db = tmp_path / "catalog.db"
    conn = connect(db)
    try:
        migrate(conn, migrations_dir=mig_up_to_0002)
        conn.execute(
            "INSERT INTO files (rel_path, filename, ext, media_type, size, mtime_ns,"
            " sha256, first_seen_at, last_scanned_at)"
            " VALUES ('a.mp4','a.mp4','mp4','video',1,1,'deadbeef','t','t')"
        )
        conn.execute(
            "INSERT INTO files (rel_path, filename, ext, media_type, size, mtime_ns,"
            " sha256, first_seen_at, last_scanned_at)"
            " VALUES ('a.jpg','a.jpg','jpg','image',1,1,'cafe','t','t')"
        )
        conn.commit()
        migrate(conn)
        rows = {
            r["rel_path"]: r["sha256"]
            for r in conn.execute("SELECT rel_path, sha256 FROM files")
        }
        assert rows == {"a.mp4": None, "a.jpg": "cafe"}
    finally:
        conn.close()


def test_second_migration_adds_placeholder_column(tmp_path):
    conn = connect(tmp_path / "catalog.db")
    migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(files)")}
    assert "placeholder" in cols
    count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    assert count == 8


def test_sixth_migration_adds_the_events_label_source_column(tmp_path):
    # spec 6.1 rev 15 / M9 Task 9: `{event}`'s tier is derived from the provenance of the
    # LABEL, and through M8 there was no column to derive it from.
    conn = connect(tmp_path / "catalog.db")
    migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    assert "label_source" in cols


def test_the_sixth_migration_backfills_a_pre_0006_label_as_a_users_own(tmp_path):
    """An EXISTING catalog at 0005, migrated forward - not a fresh one.

    The backfill is load-bearing and is not cosmetic. Before 0006 the only writer of a
    non-NULL `events.label` was a person (`_insert_event` has written NULL since M8 Task
    7 and geocoding did not exist), so 'user' is the true provenance of every pre-0006
    label. Left NULL, a user's own label would be rated by `tokens.UNKNOWN_LABEL_SOURCE_
    TIER` - `low` - instead of the `high` spec 6.1 gives it, and a name they chose would
    be pushed out of bulk approval.

    The catalog is built by running the FIRST FIVE shipped migration files into a
    directory of their own, so the "before" state is the real 0005 schema and not a
    hand-written approximation of it.
    """
    shipped = sorted((Path(pam.catalog.db.__file__).parent / "migrations").glob("*.sql"))
    names = [p.name for p in shipped]
    assert [n for n in names if n.startswith("0006_")], (
        "this test pins the 0005 -> 0006 step and that migration is gone"
    )
    # SLICED BY NAME, NOT BY `[:-1]` (M11 Task 8). Reading "everything but the last file"
    # meant this test silently changed which step it pinned the day an 0007 landed - and
    # the assertion above, written as `names[-1].startswith("0006_")`, went red for a
    # reason that had nothing to do with the backfill it protects.
    older = tmp_path / "migrations_0005"
    older.mkdir()
    for path in [p for p in shipped if p.name < "0006_"]:
        (older / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    db = tmp_path / "catalog.db"
    conn = connect(db)
    migrate(conn, older)
    assert "label_source" not in {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    conn.execute(
        "INSERT INTO events (id, label, start_dt, end_dt, algorithm, algo_version,"
        " params_json) VALUES (1, 'Cornwall', 'a', 'b', 'timegap', '1', '{}'),"
        " (2, NULL, 'c', 'd', 'timegap', '1', '{}')"
    )
    conn.commit()
    conn.close()

    conn = connect(db)
    migrate(conn)
    rows = {
        int(r["id"]): (r["label"], r["label_source"])
        for r in conn.execute("SELECT id, label, label_source FROM events")
    }
    conn.close()
    assert rows == {1: ("Cornwall", "user"), 2: (None, None)}


def test_seventh_migration_adds_the_face_review_state_column(tmp_path):
    """M11 amendment A13, migration 0007 - THE ONLY COLUMN M11 ADDS (invariant I5).

    Spelled from spec 4 rev 22 rather than from `repo.faces`: the column is NULLABLE and
    carries no CHECK, because this catalog's idiom for a closed vocabulary is a declared
    tuple in one module (`repo.faces.REVIEW_STATES`, following `journal.OPS`). Asserting
    the vocabulary here from that tuple would be an oracle taken from its own subject.

    The `face_instances` shape is asserted whole, not just for the new name, so that a
    migration that added a SECOND column - the thing I5 forbids - is a red test rather
    than an invisible one.
    """
    conn = connect(tmp_path / "catalog.db")
    migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(face_instances)")}
    assert cols == {
        "id",
        "sha256",
        "bbox_json",
        "embedding",
        "cluster_id",
        "person_id",
        "review_state",
        "det_confidence",
        "model_name",
        "model_version",
    }
    nullable = {
        r["name"]: bool(r["notnull"]) for r in conn.execute("PRAGMA table_info(face_instances)")
    }
    assert nullable["review_state"] is False


def test_the_seventh_migration_leaves_every_existing_face_unreviewed(tmp_path):
    """A13: "No backfill: NULL is correct for every existing row."

    An EXISTING catalog at 0006, migrated forward - not a fresh one - because the claim
    is about rows that were already there. A backfill that set anything non-NULL would
    exclude a pre-M11 detection from clustering and from `{people}` for ever, silently.
    """
    shipped = sorted((Path(pam.catalog.db.__file__).parent / "migrations").glob("*.sql"))
    older = tmp_path / "migrations_0006"
    older.mkdir()
    for path in [p for p in shipped if p.name < "0007_"]:
        (older / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    db = tmp_path / "catalog.db"
    conn = connect(db)
    migrate(conn, older)
    assert "review_state" not in {
        r["name"] for r in conn.execute("PRAGMA table_info(face_instances)")
    }
    conn.execute(
        "INSERT INTO face_instances (sha256, bbox_json, embedding, det_confidence,"
        " model_name, model_version) VALUES ('a', '{}', X'00', 0.9, 'm', '1')"
    )
    conn.commit()
    conn.close()

    conn = connect(db)
    migrate(conn)
    states = [
        r["review_state"] for r in conn.execute("SELECT review_state FROM face_instances")
    ]
    conn.close()
    assert states == [None]


def test_fourth_migration_adds_unit_key_column(tmp_path):
    # spec 4 rev 6: plan_items freezes the companion unit it was planned against.
    conn = connect(tmp_path / "catalog.db")
    migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(plan_items)")}
    assert "unit_key" in cols
    indexes = {r["name"] for r in conn.execute("PRAGMA index_list(plan_items)")}
    assert "idx_plan_items_unit" in indexes
