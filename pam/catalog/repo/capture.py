"""Catalog SQL for the capture table. All SQL lives under catalog/repo/."""

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class CaptureFacts:
    capture_dt: str | None
    capture_dt_source: (
        str  # 'exif' | 'qt_local' | 'qt_utc' | 'filename' | 'file_mtime' | 'none'
    )
    tz_offset_min: int | None
    gps_lat: float | None
    gps_lon: float | None
    width: int | None
    height: int | None
    camera_make: str | None
    camera_model: str | None
    exif_json: str


def upsert_capture(conn: sqlite3.Connection, file_id: int, cap: CaptureFacts) -> None:
    conn.execute(
        """INSERT INTO capture (file_id, capture_dt, capture_dt_source, tz_offset_min,
                                gps_lat, gps_lon, width, height, camera_make,
                                camera_model, exif_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (file_id) DO UPDATE SET
             capture_dt = excluded.capture_dt,
             capture_dt_source = excluded.capture_dt_source,
             tz_offset_min = excluded.tz_offset_min,
             gps_lat = excluded.gps_lat,
             gps_lon = excluded.gps_lon,
             width = excluded.width,
             height = excluded.height,
             camera_make = excluded.camera_make,
             camera_model = excluded.camera_model,
             exif_json = excluded.exif_json""",
        (
            file_id,
            cap.capture_dt,
            cap.capture_dt_source,
            cap.tz_offset_min,
            cap.gps_lat,
            cap.gps_lon,
            cap.width,
            cap.height,
            cap.camera_make,
            cap.camera_model,
            cap.exif_json,
        ),
    )


def gps_by_file_id(conn: sqlite3.Connection) -> dict[int, tuple[float, float]]:
    """file_id -> (lat, lon) for every capture row carrying BOTH coordinates.

    Spec 8.1's `wants()` for the geocode enricher is "has GPS", and spec 8.1 rev 3 says
    it reads that from the `capture` row rather than from pixels - which is the whole
    reason geocode serves videos. `FileRecord` is `repo.FileRow` and carries no GPS, and
    the runner hands `wants()` a `FileRecord` and no connection (`enrich/base.py`), so
    the answer has to be looked up BEFORE the run and handed to the enricher.

    IT LIVES HERE AND NOT IN `enrichment.py` FOR AN ARCHITECTURAL REASON, not a stylistic
    one. This module OWNS the `capture` table (`tests/guards.py::REPO_OWN_TABLES`), so
    this query is not a cross-table reach at all and `ALLOWED_REPO_REACHES` stays at the
    five pairs it has held since M7. The same query written in `enrichment.py` would be a
    sixth.

    A row with one coordinate and not the other is NOT positioned. Both columns are
    nullable and the scanner writes them together, but a half-populated row would
    otherwise reach the geocoder as a coordinate with a silent zero in it, and a name is
    the artefact - `events/grouping.py::_gps` answers the same question the same way.

    Whole-catalog and eager, like `repo.event_membership`: `wants()` is asked for every
    uncached file on every run and must stay cheap, and one round trip per file over an
    archive of tens of thousands is what a per-file query would cost.
    """
    return {
        int(row["file_id"]): (float(row["gps_lat"]), float(row["gps_lon"]))
        for row in conn.execute(
            "SELECT file_id, gps_lat, gps_lon FROM capture"
            " WHERE gps_lat IS NOT NULL AND gps_lon IS NOT NULL"
        )
    }


def date_sources(conn: sqlite3.Connection) -> dict[int, str]:
    """`file_id` -> `capture_dt_source`, for every capture row that has one (M14 Task 2, U15).

    The review row's note code (`webui/listing.note_code`) follows a date token's
    provenance pointer - `(capture, source_id)` - to the source that dated the file, because
    `tokens_json` records the table and the row, not the `capture_dt_source` string. One
    statement over the table, like `gps_by_file_id`: no variable limit to chunk, and this
    module owns `capture`, so it is no cross-table reach.
    """
    return {
        int(row["file_id"]): str(row["capture_dt_source"])
        for row in conn.execute(
            "SELECT file_id, capture_dt_source FROM capture WHERE capture_dt_source IS NOT NULL"
        )
    }


def file_ids_by_date_source(conn: sqlite3.Connection, source: str) -> frozenset[int]:
    """Every file id whose `capture_dt_source` is `source` - `ocr`'s `wants()` input.

    Design 8.1 rev 27 (P7): OCR reads a file only when its date came from `file_mtime`,
    because design 6.1's hierarchy lets an OCR date replace that and nothing else. The
    runner hands `wants()` a `FileRecord` and no connection, so the set is read BEFORE the
    run, as `gps_by_file_id` above is for geocode, and for the same architectural reason it
    lives here: this module owns `capture`, so the query is no cross-table reach.
    """
    return frozenset(
        int(row["file_id"])
        for row in conn.execute(
            "SELECT file_id FROM capture WHERE capture_dt_source = ?", (source,)
        )
    )
