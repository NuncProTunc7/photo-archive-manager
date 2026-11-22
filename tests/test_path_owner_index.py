"""M14 Task 0C (M13 C3, R3; OD-6 (a)): `repo.path_owner` reads through a NOCASE index.

`path_owner` runs `WHERE rel_path = ? COLLATE NOCASE` once per resolved rename, in apply, undo
and recovery alike. The UNIQUE index on `files.rel_path` is BINARY, so that statement was a
`SCAN files` - measured at 0.34 ms a call at 10,000 files and growing with the catalog, the
super-linear term in M13's F-A. Migration 0008 adds `idx_files_rel_path_nocase`; the function
itself is unchanged, so its answer is unchanged by construction. Two things are pinned here:

- the ANSWER: a case-variant owner is still found (written before the migration, and green
  before it - it is what stops "drop `COLLATE NOCASE` from `path_owner`" from passing as a
  speed-up, since the BINARY unique index would then serve it);
- the PLAN: the statement `path_owner` actually sends is answered `USING INDEX
  idx_files_rel_path_nocase` (SQLite prints `USING COVERING INDEX`), read through a trace callback so the test explains production's
  own SQL rather than a copy of it.
"""

import re
import sqlite3
from pathlib import Path

import pytest

import pam.catalog.db
from pam.catalog import repo
from pam.catalog.db import connect, migrate

NOW = "2026-10-30T10:00:00"
INDEX = "idx_files_rel_path_nocase"  # spelled from design 4 rev 34 (OD-6 (a)), not read back


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "catalog.db")
    migrate(c)
    yield c
    c.close()


def _add(conn: sqlite3.Connection, rel_path: str) -> int:
    return repo.upsert_file(
        conn,
        rel_path=rel_path,
        filename=rel_path.rsplit("/", 1)[-1],
        ext=rel_path.rsplit(".", 1)[-1].lower(),
        media_type="image",
        size=100,
        mtime_ns=1_000,
        placeholder=False,
        now=NOW,
    )


def _statements_sent_by_path_owner(conn: sqlite3.Connection, rel_path: str) -> list[str]:
    sent: list[str] = []
    conn.set_trace_callback(sent.append)
    try:
        repo.path_owner(conn, rel_path)
    finally:
        conn.set_trace_callback(None)
    return sent


def test_a_case_variant_owner_is_found(conn):
    """Spec 6.3.2 / J11: the row stored as `A/IMG.JPG` owns `a/img.jpg`."""
    owner = _add(conn, "A/IMG.JPG")
    _add(conn, "B/other.jpg")
    assert repo.path_owner(conn, "a/img.jpg") == owner
    assert repo.path_owner(conn, "A/Img.Jpg") == owner
    assert repo.path_owner(conn, "A/IMG.JPG") == owner


def test_an_unowned_path_has_no_owner(conn):
    """The negative half, so a lookup that answers every path cannot pass the test above."""
    _add(conn, "A/IMG.JPG")
    assert repo.path_owner(conn, "A/IMG2.JPG") is None
    assert repo.path_owner(conn, "a/img.jpeg") is None


def test_a_path_differing_only_at_a_like_wildcard_is_not_owned(conn):
    """Equality under NOCASE, not a pattern match (M14 Task 0C review, M2719 / F-0C-1).

    `rel_path LIKE ?` is also ASCII-case-insensitive, and SQLite's LIKE optimisation SEARCHes
    a NOCASE index - so after 0008 it reads as a free rewrite that the EXPLAIN test above
    accepts. But `_` and `%` are wildcards to LIKE, and both are ordinary filename characters
    (`IMG_0001.JPG`). A wildcard-matched owner is a wrong J11 answer: a false discrepancy in
    apply and undo, a false strand in recovery.
    """
    _add(conn, "A/IMGX0001.JPG")
    _add(conn, "B/a-long-name.jpg")
    assert repo.path_owner(conn, "A/IMG_0001.JPG") is None
    assert repo.path_owner(conn, "b/a%name.jpg") is None


def test_path_owner_reads_through_the_nocase_index(conn):
    """The plan of the statement production sends, not of a copy of it."""
    for n in range(50):
        _add(conn, f"d{n % 5}/IMG_{n:04d}.JPG")
    sent = _statements_sent_by_path_owner(conn, "d1/img_0001.jpg")
    selects = [s for s in sent if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 1, sent
    sql = selects[0]
    params = ("d1/img_0001.jpg",) if "?" in sql else ()
    plan = [row["detail"] for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", params)]
    assert _searches_the_index(plan), plan


def _searches_the_index(plan: list[str]) -> bool:
    """Both operands are needed, and the control below pins each one.

    SQLite says COVERING when the index alone answers (`id` is the rowid it carries). A plan
    can NAME the index and still be a full scan: `LOWER(rel_path) = LOWER(?)` is planned as
    `SCAN files USING COVERING INDEX idx_files_rel_path_nocase` (measured, M2720), which is
    O(n) per rename - the very term 0008 removed.
    """
    names_it = any(re.search(rf"USING (COVERING )?INDEX {INDEX}\b", d) for d in plan)
    scans = any(detail.startswith("SCAN") for detail in plan)
    return names_it and not scans


# Literal plans, spelled from SQLite's own output rather than from the predicate (M2721, F-0C-2).
PLAN_VERDICTS = [
    (["SEARCH files USING COVERING INDEX idx_files_rel_path_nocase (rel_path=?)"], True),
    (["SEARCH files USING INDEX idx_files_rel_path_nocase (rel_path=?)"], True),
    (["SCAN files USING COVERING INDEX idx_files_rel_path_nocase"], False),
    (["SEARCH files USING COVERING INDEX sqlite_autoindex_files_1 (rel_path=?)"], False),
    (["SEARCH files USING COVERING INDEX idx_files_rel_path_nocase_old (rel_path=?)"], False),
    (["SCAN files"], False),
]


def test_the_plan_predicate_rejects_a_scan_that_names_the_index():
    """The control on the EXPLAIN test's own instrument (M14 Task 0C review, M2721)."""
    assert len(PLAN_VERDICTS) >= 6
    for plan, expected in PLAN_VERDICTS:
        assert _searches_the_index(plan) is expected, plan


def test_the_index_is_rel_path_under_nocase(conn):
    """Design 4: one column, `rel_path`, collated NOCASE - not a second BINARY index."""
    names = {r["name"] for r in conn.execute("PRAGMA index_list(files)")}
    assert INDEX in names
    keys = [r for r in conn.execute(f"PRAGMA index_xinfo({INDEX})") if r["key"]]
    assert [(r["name"], r["coll"]) for r in keys] == [("rel_path", "NOCASE")]


def test_an_existing_0007_catalog_migrates_to_0008_and_its_rows_are_found(tmp_path):
    """A catalog made before 0008 gains the index on its next open; its rows are untouched."""
    shipped = sorted((Path(pam.catalog.db.__file__).parent / "migrations").glob("*.sql"))
    assert [p for p in shipped if p.name.startswith("0008_")], "migration 0008 is gone"
    older = tmp_path / "migrations_0007"
    older.mkdir()
    for path in [p for p in shipped if p.name < "0008_"]:
        (older / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    db = tmp_path / "catalog.db"
    conn = connect(db)
    migrate(conn, older)
    assert INDEX not in {r["name"] for r in conn.execute("PRAGMA index_list(files)")}
    owner = _add(conn, "Trip/IMG_0001.JPG")
    conn.commit()
    conn.close()

    conn = connect(db)
    try:
        migrate(conn)
        assert INDEX in {r["name"] for r in conn.execute("PRAGMA index_list(files)")}
        assert repo.path_owner(conn, "trip/img_0001.jpg") == owner
        assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1
    finally:
        conn.close()
