"""Catalog SQL for the files table. All SQL lives under catalog/repo/."""

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class FileRow:
    id: int
    rel_path: str
    filename: str
    ext: str
    media_type: str
    size: int
    mtime_ns: int
    sha256: str | None
    status: str
    placeholder: bool
    companion_group_id: int | None


FILE_COLUMNS = (
    "id, rel_path, filename, ext, media_type, size, mtime_ns, sha256,"
    " status, placeholder, companion_group_id"
)


def _file_row(row: sqlite3.Row) -> FileRow:
    """One `files` row as a `FileRow`. The shared READ, not a shared query.

    Three call sites spelled these eleven fields out separately until M11 Task 10 added a
    fourth; `catalog/repo/faces.py::_face` is the precedent and its reasoning is the same.
    A field added to the table has one place to be wired in, and a field mis-wired in one
    of four copies is exactly the defect nothing notices.
    """
    return FileRow(
        id=row["id"],
        rel_path=row["rel_path"],
        filename=row["filename"],
        ext=row["ext"],
        media_type=row["media_type"],
        size=row["size"],
        mtime_ns=row["mtime_ns"],
        sha256=row["sha256"],
        status=row["status"],
        placeholder=bool(row["placeholder"]),
        companion_group_id=row["companion_group_id"],
    )


def all_files(conn: sqlite3.Connection) -> dict[str, FileRow]:
    """Every files row keyed by rel_path (the scanner's change-detection snapshot)."""
    rows = conn.execute(f"SELECT {FILE_COLUMNS} FROM files")
    return {r["rel_path"]: _file_row(r) for r in rows}


def file_by_id(conn: sqlite3.Connection, file_id: int) -> FileRow | None:
    """One files row by its id, or None. Spec 8.2's `/thumb/{file_id}` is the consumer.

    `all_files` is keyed by rel_path and loads the whole table, which is right for the
    scanner's change detection and wrong for a request that knows exactly which row it
    wants; the review server serves one thumbnail per request and a 20,000-file archive
    would otherwise pay for the whole catalog per card.
    """
    row = conn.execute(f"SELECT {FILE_COLUMNS} FROM files WHERE id = ?", (file_id,)).fetchone()
    return None if row is None else _file_row(row)


def files_by_sha256(conn: sqlite3.Connection, sha256: str) -> list[FileRow]:
    """Every files row holding this content, BEST CANDIDATE FIRST (spec 8.2's `/faces/crop`).

    A LIST AND NOT A ROW, because spec 4 keys `face_instances` by CONTENT: a face knows
    its sha256 and nothing else, and an archive may hold the same photograph at two paths.
    One of those paths can be `missing` since the last scan, or be a cloud placeholder, and
    the other be sitting on disk - so the endpoint needs the candidates, not a guess.

    THE ORDER IS THE PICK, and it is expressed in SQL rather than left to the caller so
    that "which copy did pam read" is one answer everywhere. `present` before `missing`
    (a row the scanner could not find has nothing to open), then NOT-placeholder before
    placeholder (vision R2: reading a placeholder's bytes hydrates it, so a hydrated
    sibling is strictly the better source), then `rel_path` - which is total, so the
    choice is DETERMINISTIC and two requests for one face cannot serve two files.

    IT IS A PREFERENCE AND NOT A GUARANTEE. `status` and `placeholder` are what the last
    scan recorded, and the caller re-checks placeholder state against the live filesystem
    immediately before reading (`webui/crop.py`); this ordering only decides what it looks
    at first. A catalog column is not a promise about the disk right now.
    """
    rows = conn.execute(
        f"SELECT {FILE_COLUMNS} FROM files WHERE sha256 = ?"
        " ORDER BY (status != 'present'), placeholder, rel_path",
        (sha256,),
    ).fetchall()
    return [_file_row(r) for r in rows]


def upsert_file(
    conn: sqlite3.Connection,
    *,
    rel_path: str,
    filename: str,
    ext: str,
    media_type: str,
    size: int,
    mtime_ns: int,
    placeholder: bool,
    now: str,
) -> int:
    """Insert or refresh one on-disk file. Preserves sha256 and first_seen_at."""
    row = conn.execute(
        """INSERT INTO files (rel_path, filename, ext, media_type, size, mtime_ns,
                              status, placeholder, first_seen_at, last_scanned_at)
           VALUES (?, ?, ?, ?, ?, ?, 'present', ?, ?, ?)
           ON CONFLICT (rel_path) DO UPDATE SET
             filename = excluded.filename,
             ext = excluded.ext,
             media_type = excluded.media_type,
             size = excluded.size,
             mtime_ns = excluded.mtime_ns,
             status = 'present',
             placeholder = excluded.placeholder,
             last_scanned_at = excluded.last_scanned_at
           RETURNING id""",
        (rel_path, filename, ext, media_type, size, mtime_ns, int(placeholder), now, now),
    ).fetchone()
    return int(row["id"])


def touch_files(conn: sqlite3.Connection, ids: list[int], now: str) -> None:
    """Unchanged files: refresh last_scanned_at (and un-miss returners)."""
    conn.executemany(
        "UPDATE files SET status = 'present', last_scanned_at = ? WHERE id = ?",
        [(now, file_id) for file_id in ids],
    )


def set_sha256(conn: sqlite3.Connection, file_id: int, sha256: str | None) -> None:
    conn.execute("UPDATE files SET sha256 = ? WHERE id = ?", (sha256, file_id))


def path_owner(conn: sqlite3.Connection, rel_path: str) -> int | None:
    """Which files row owns `rel_path`, compared case-insensitively? (spec 7.0 J11)

    `files.rel_path` is UNIQUE and rows are NEVER deleted, so a path can be owned by a
    row describing a file that is long gone. Writing over that ownership raises
    sqlite3.IntegrityError, which J11 forbids from blocking a journal row's resolution
    - so the writer asks first.

    COLLATE NOCASE rather than an exact match, mirroring spec 6.3.2 and
    `validator._ghost_name_refusals`. An exact duplicate (the only case that would
    actually RAISE) is necessarily found by it; the extra reach catches an ASCII-case
    twin, which raises nothing at all and instead writes the case-duplicate spec 7.5's
    check 6 calls catalog corruption on Windows - where the two names cannot both exist
    on disk. SQLite's NOCASE is ASCII-only, so a unicode-case twin is missed here; that
    one cannot raise either, and `journal_ops` still guards the write itself.
    """
    row = conn.execute(
        "SELECT id FROM files WHERE rel_path = ? COLLATE NOCASE", (rel_path,)
    ).fetchone()
    return None if row is None else int(row["id"])


def set_file_path(conn: sqlite3.Connection, file_id: int, rel_path: str) -> None:
    """Point a files row at the path a completed rename actually put it at (spec 7.1).

    Does NOT commit: spec 7.0 J5 makes this write part of one transaction with the
    journal 'done' row and the plan_item status, and M5's RenameTransaction owns that
    transaction boundary.

    `filename` is derived here rather than passed in, so the two columns cannot
    disagree - they are the same fact stored twice. `ext` is deliberately untouched:
    spec 5.4 rev 6 has a rename preserve the extension exactly as written on disk, so
    the lowercase `ext` used for typing and matching is invariant under a rename. A
    write here that changed it would be a bug, not a refinement.
    """
    conn.execute(
        "UPDATE files SET rel_path = ?, filename = ? WHERE id = ?",
        (rel_path, rel_path.rpartition("/")[2], file_id),
    )


def mark_missing(conn: sqlite3.Connection, ids: list[int]) -> None:
    conn.executemany("UPDATE files SET status = 'missing' WHERE id = ?", [(i,) for i in ids])


@dataclass(frozen=True)
class RehashCandidate:
    """One file spec 7.5 check 8 is allowed to re-read, and the hash to compare against."""

    rel_path: str
    sha256: str


def rehash_candidates(conn: sqlite3.Connection) -> list[RehashCandidate]:
    """Every file `pam verify --deep` may sample, in a fixed order (spec 7.5 check 8).

    THREE FILTERS, AND EACH ONE IS LOAD-BEARING.

    `status = 'present'` because a `missing` file has nothing on disk to re-read; its
    row is kept so it can come back (see `orphan_rows`), and sampling it would report
    "the bytes changed" about bytes nobody looked at. Spec 7.5 check 2 owns that
    relationship.

    `placeholder = 0` because a cloud placeholder's bytes are NOT on this machine
    (spec 5.2). Reading one asks OneDrive to hydrate it, so a read-only check would
    quietly pull gigabytes down a metered connection - and the hash it then computed
    would be of a file pam had just caused to be downloaded. Placeholders are excluded
    from plans for the same reason; a checker has even less business hydrating one.

    `sha256 IS NOT NULL` because a file the scanner could not hash has nothing to
    compare against. Sampling it would compare a real hash to nothing at all.

    ORDERED BY `rel_path` so the sample is REPRODUCIBLE. `random.Random(seed).sample`
    draws from the sequence it is given, so an unordered query would make `--seed`
    a promise SQLite does not keep - two runs could return the same rows in a
    different order and draw a different sample from the same seed. `rel_path` rather
    than `id` because it is the stable key across a re-scan: ids follow insertion
    order, which a re-scan of a moved archive does not preserve.
    """
    rows = conn.execute(
        "SELECT rel_path, sha256 FROM files"
        " WHERE status = 'present' AND placeholder = 0 AND sha256 IS NOT NULL"
        " ORDER BY rel_path"
    )
    return [RehashCandidate(rel_path=str(r["rel_path"]), sha256=str(r["sha256"])) for r in rows]


def status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM files GROUP BY status")
    return {r["status"]: r["n"] for r in rows}


def media_type_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """`pam status`: how many image / video / sidecar / other files pam has (spec 12).

    Every row, not only the `present` ones. `status_counts` beside it is what says how
    many of them are still on disk, and a file that has gone missing is still a file
    pam knows about - dropping it here would make the two lists disagree by a number
    the reader would then have to work out.
    """
    rows = conn.execute("SELECT media_type, COUNT(*) AS n FROM files GROUP BY media_type")
    return {r["media_type"]: int(r["n"]) for r in rows}


def placeholder_count(conn: sqlite3.Connection) -> int:
    """How many files are cloud-only placeholders (spec 5.2), whatever their status.

    A separate number rather than a `files.status` value because that is how the schema
    stores it: `placeholder` is its own column, and a placeholder is `present` - the
    file's entry is really there, its bytes are not. `pam plan` excludes them, so a
    reader comparing "500 files" against a plan of 286 needs this line to explain it.
    """
    row = conn.execute("SELECT COUNT(*) AS n FROM files WHERE placeholder = 1").fetchone()
    return int(row["n"])


def last_scanned_at(conn: sqlite3.Connection) -> str | None:
    """When pam last read this archive: the newest `files.last_scanned_at`, or None.

    MAX over the column rather than a separate "last scan" record, because the scanner
    writes this on every file it touches and there is no other timestamp to read. None
    means the table is empty - a workspace whose first scan wrote nothing.
    """
    row = conn.execute("SELECT MAX(last_scanned_at) AS at FROM files").fetchone()
    return None if row["at"] is None else str(row["at"])
