"""Catalog SQL for the plans table. All SQL lives under catalog/repo/."""

import sqlite3
from dataclasses import dataclass


def ext_of(filename: str) -> str:
    """The final extension exactly as written on disk ('IMG.JPG' -> 'JPG').

    spec 5.4 rev 6: a member's new filename keeps its extension's on-disk case.
    `files.ext` is lowercased for typing and matching and must not be the source of
    a proposed filename. The mirror of `naming.tokens.stem_of`, and by construction
    `ext_of(filename).lower() == files.ext` for every file the walker records.
    """
    head, dot, tail = filename.rpartition(".")
    return tail if dot and head else ""


@dataclass(frozen=True)
class PlanFile:
    id: int
    rel_path: str
    filename: str
    ext: str  # lowercased: for typing and matching, never for a proposed name
    ext_as_written: str  # the on-disk case: what a proposed filename must use
    media_type: str
    placeholder: bool
    companion_group_id: int | None
    capture_dt: str | None
    capture_dt_source: str | None
    has_capture: bool  # spec 6.1 rev 6: provenance may only name a row that exists


def files_for_planning(
    conn: sqlite3.Connection, media_types: tuple[str, ...]
) -> list[PlanFile]:
    """Present files the planner may rename, with their capture facts (spec 6.3.1).

    media_types comes from the caller (scan.walker.READ_MEDIA_TYPES) rather than
    being hardcoded here, so the set lives in exactly one place. Sidecars and
    'other' files are included only when they belong to a companion group: they
    rename with their primary as one journaled unit (spec 5.4).
    """
    placeholders = ",".join("?" * len(media_types))
    rows = conn.execute(
        "SELECT f.id, f.rel_path, f.filename, f.ext, f.media_type, f.placeholder,"
        " f.companion_group_id, c.capture_dt, c.capture_dt_source,"
        " c.file_id IS NOT NULL AS has_capture"
        " FROM files f LEFT JOIN capture c ON c.file_id = f.id"
        " WHERE f.status = 'present'"
        f"   AND (f.media_type IN ({placeholders}) OR f.companion_group_id IS NOT NULL)"
        " ORDER BY f.rel_path",
        media_types,
    )
    return [
        PlanFile(
            id=r["id"],
            rel_path=r["rel_path"],
            filename=r["filename"],
            ext=r["ext"],
            ext_as_written=ext_of(r["filename"]),
            media_type=r["media_type"],
            placeholder=bool(r["placeholder"]),
            companion_group_id=r["companion_group_id"],
            capture_dt=r["capture_dt"],
            capture_dt_source=r["capture_dt_source"],
            has_capture=bool(r["has_capture"]),
        )
        for r in rows
    ]


@dataclass(frozen=True)
class DatedFile:
    """One row of spec 8.3's grouping input: a present file that has a capture date."""

    file_id: int
    capture_dt: str
    gps_lat: float | None
    gps_lon: float | None
    # M12 Task 10 (design 6.1 rev 27, I3): what `naming/dates.py::resolve_date` needs to
    # decide this file's EFFECTIVE date before step 0 groups it. DEFAULTED, because the
    # grouping algorithm reads neither and its own tests build rows without them; the one
    # production producer, `dated_files_for_events` below, always fills both.
    capture_dt_source: str | None = None
    rel_path: str = ""


def dated_files_for_events(conn: sqlite3.Connection) -> list[DatedFile]:
    """Every present, DATED file, image and video alike (spec 8.3 rev 14).

    Filed here, in the plans module, for the reason reach 1 is filed here: grouping is
    `pam plan`'s step 0 (spec 6.3 rule 5b) and this query exists to serve it, exactly
    as `files_for_planning` exists to serve the naming pass. It needs `capture` for the
    timestamp and the coordinates and `files` for the `present` filter, and both are
    already blessed here as reach 1 - so events cost the package no new reach.

    There is deliberately NO media_type filter. spec 8.3 rev 14 corrects rev 2's "image
    files" to "image and video alike", and says sidecars and cloud placeholders "fall
    out naturally: neither has a `capture` row". The INNER JOIN is that sentence: a
    placeholder is never read, so it never gets a capture row, so it is never grouped -
    a property of the shape of the query rather than of a filter someone must maintain.
    """
    rows = conn.execute(
        "SELECT f.id, f.rel_path, c.capture_dt, c.capture_dt_source, c.gps_lat, c.gps_lon"
        " FROM files f JOIN capture c ON c.file_id = f.id"
        " WHERE f.status = 'present' AND c.capture_dt IS NOT NULL"
        " ORDER BY c.capture_dt, f.id"
    )
    return [
        DatedFile(
            file_id=int(r["id"]),
            capture_dt=str(r["capture_dt"]),
            gps_lat=r["gps_lat"],
            gps_lon=r["gps_lon"],
            capture_dt_source=r["capture_dt_source"],
            rel_path=str(r["rel_path"]),
        )
        for r in rows
    ]


def supersede_open_plans(conn: sqlite3.Connection) -> int:
    """spec 6.3.5: open plans flip to superseded; applied plans are never touched."""
    cur = conn.execute("UPDATE plans SET status = 'superseded' WHERE status = 'open'")
    return int(cur.rowcount)


def create_plan(
    conn: sqlite3.Connection,
    *,
    now: str,
    template: str,
    config_json: str,
    app_version: str,
    schema_version: int,
    already_conforming: int = 0,
) -> int:
    """Create the plan row. `already_conforming` is frozen here or lost.

    UX spec 4's counts line needs it and the planner is the only thing that ever
    knows it: those files get no `plan_items` row, so by apply time the number is
    underivable (migration 0005 says why at length).
    """
    row = conn.execute(
        """INSERT INTO plans (created_at, status, template, config_json, app_version,
                              schema_version, already_conforming)
           VALUES (?, 'open', ?, ?, ?, ?, ?)
           RETURNING id""",
        (now, template, config_json, app_version, schema_version, already_conforming),
    ).fetchone()
    return int(row["id"])


def plan_already_conforming(conn: sqlite3.Connection, plan_id: int) -> int:
    """UX spec 4's "already conforming" count, as frozen when this plan was built.

    Zero for a plan written before migration 0005, which is what its confirmation
    already implied - not a signal that the archive had none.
    """
    row = conn.execute(
        "SELECT already_conforming FROM plans WHERE id = ?", (plan_id,)
    ).fetchone()
    return 0 if row is None else int(row["already_conforming"])


def plan_status(conn: sqlite3.Connection, plan_id: int) -> str | None:
    row = conn.execute("SELECT status FROM plans WHERE id = ?", (plan_id,)).fetchone()
    return None if row is None else str(row["status"])


@dataclass(frozen=True)
class PlanHeader:
    """The plan itself, as spec 8.2's `/plan/{id}` header shows it (UX spec 5)."""

    id: int
    created_at: str
    template: str
    status: str


def plan_header(conn: sqlite3.Connection, plan_id: int) -> PlanHeader | None:
    """One plan's own row, or None when no plan has that id (spec 8.2's 404).

    HERE rather than in `review.py` even though `pam review` is its only caller: it reads
    `plans` and nothing else, and this package's organising rule is one module per table.
    `review.py` is exempt from the reach arithmetic because every query IN it spans
    tables; a single-table read filed there would be borrowing that exemption for nothing.
    """
    row = conn.execute(
        "SELECT id, created_at, template, status FROM plans WHERE id = ?", (plan_id,)
    ).fetchone()
    if row is None:
        return None
    return PlanHeader(
        id=int(row["id"]),
        created_at=str(row["created_at"]),
        template=str(row["template"]),
        status=str(row["status"]),
    )


def plans_in_status(conn: sqlite3.Connection, status: str) -> list[int]:
    """Plan ids in one status, oldest first (spec 7.2 phase 3's only input).

    Recovery finalizes every plan left `applying` and changes no other plan, ever
    (spec 7.0 J10), so it asks for one status by name rather than reading them all and
    filtering in Python - a filter is a place a later edit can widen by accident.
    """
    rows = conn.execute("SELECT id FROM plans WHERE status = ? ORDER BY id", (status,))
    return [int(r["id"]) for r in rows]


def plan_status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """`pam status`: how many plans sit in each status (spec 12 "plans").

    Grouped in SQL rather than counted from `plans_in_status` per status, because the
    caller would then have to know the status vocabulary - and a value this function
    had not heard of would go unreported, which is precisely the state a summary exists
    to surface.
    """
    rows = conn.execute("SELECT status, COUNT(*) AS n FROM plans GROUP BY status")
    return {str(r["status"]): int(r["n"]) for r in rows}


def set_plan_status(conn: sqlite3.Connection, plan_id: int, status: str) -> None:
    conn.execute("UPDATE plans SET status = ? WHERE id = ?", (status, plan_id))
