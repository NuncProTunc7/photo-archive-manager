"""Catalog SQL for the companion_groups table. All SQL lives under catalog/repo/.

Cross-table reach (deliberate, one of two in this package - see repo/__init__.py):
group membership is stored as files.companion_group_id, so this module reads and
writes the files table as well as its own (companion_membership reads
files.rel_path; create/clear write files.companion_group_id).
"""

import sqlite3


def clear_companion_groups(conn: sqlite3.Connection) -> None:
    """Groups are recomputed from scratch every scan (deterministic)."""
    conn.execute("UPDATE files SET companion_group_id = NULL")
    conn.execute("DELETE FROM companion_groups")


def create_companion_group(
    conn: sqlite3.Connection, primary_file_id: int, kind: str, member_ids: list[int]
) -> int:
    row = conn.execute(
        "INSERT INTO companion_groups (primary_file_id, kind) VALUES (?, ?) RETURNING id",
        (primary_file_id, kind),
    ).fetchone()
    group_id = int(row["id"])
    conn.executemany(
        "UPDATE files SET companion_group_id = ? WHERE id = ?",
        [(group_id, member_id) for member_id in member_ids],
    )
    return group_id


def companion_membership(conn: sqlite3.Connection) -> set[tuple[str, frozenset[str]]]:
    """The stored grouping as {(kind, member rel_paths)} - spec 5.1 rev 6.

    Membership, never group ids. clear_companion_groups deletes and recreates every
    group on every scan, so ids carry no information about whether the grouping
    actually changed; the members do. pam scan compares this against the grouping
    it just computed and supersedes open plans when the two differ, because editing
    scan.sidecar_exts or scan.ignore_stem_suffixes in pam.toml re-groups files with
    no file changing on disk.
    """
    rows = conn.execute(
        "SELECT g.id, g.kind, f.rel_path FROM companion_groups g"
        " JOIN files f ON f.companion_group_id = g.id"
    )
    by_group: dict[int, tuple[str, set[str]]] = {}
    for r in rows:
        kind, members = by_group.setdefault(int(r["id"]), (str(r["kind"]), set()))
        members.add(str(r["rel_path"]))
    return {(kind, frozenset(members)) for kind, members in by_group.values()}


def companion_primaries(conn: sqlite3.Connection) -> dict[int, int]:
    """group id -> the member whose facts drive the group's name (spec 5.4)."""
    rows = conn.execute("SELECT id, primary_file_id FROM companion_groups")
    return {int(r["id"]): int(r["primary_file_id"]) for r in rows}


def companion_kinds(conn: sqlite3.Connection) -> dict[int, str]:
    """`file_id` -> its companion group's kind, for every grouped file (M14 W9).

    The review row's unit disclosure (`Live Photo · 2 files`, `RAW + JPEG · 2 files`) names
    the KIND the scanner gave the group (`scan/companions.py`: `raw_jpeg`, `live_photo`,
    `sidecar`), so the page never guesses it from file extensions. One statement over this
    module's own table and the `files.companion_group_id` pointer it already writes -
    reach 2 above, no new pair. Read for an OPEN plan this is the grouping the plan was
    made from: a scan that changes membership supersedes every open plan (spec 5.1 rev 6).
    """
    rows = conn.execute(
        "SELECT f.id, g.kind FROM files f JOIN companion_groups g ON f.companion_group_id = g.id"
    )
    return {int(r["id"]): str(r["kind"]) for r in rows}
