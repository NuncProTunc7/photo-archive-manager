"""Catalog SQL for `pam report` (spec 12, M6 Task 6). All SQL lives under catalog/repo/.

NOT KEYED TO A TABLE, and filed under its one consumer for the same reason
`integrity.py` is: `report_facts` reads `files` LEFT JOIN `capture`, so filing it under
either name would put it where it does not belong and would turn this package's four
documented cross-table reaches into five. Every query here exists to answer one
command's questions and nothing else's - `phash_by_sha256` (M13 Task 9) reads
`inferred_facts` against `files` for `pam report duplicates`' look-alikes, and
`labelled_face_embeddings` (M13 Task 14, H7) reads `face_instances`, `persons` and `files`
for `pam faces calibrate`, a lock-free reader with `pam report`'s shape.

NO QUERY HERE DECIDES ANYTHING. `report_facts` returns the FACTS the catalog holds
about each file - has pam a fingerprint for it, has pam any metadata - and
`pam/report.py` turns those into the six reasons a file went unread. The classification
is first-match-wins and therefore order-dependent, which is exactly the kind of rule
that must be readable in one place rather than spread across a WHERE clause and a
Python `if`. A predicate expressed half in SQL and half in Python drifts silently.
"""

import json
import sqlite3
from dataclasses import dataclass

# The key exiftool uses when it could not identify a file's format at all - an empty
# file, a file of all zero bytes, random bytes (measured with exiftool 13.59). NOT
# `Warning`, which means the format WAS identified and some of the contents could not be
# parsed: in a real-world library about half of the capture rows carry one, every one a
# video that got its date, so a group that matched it would list half an archive as
# unreadable.
#
# SPELLED OUT HERE rather than imported from `pam/scan/objective.py`, which reads the
# same key off the live record during a scan. Not an oversight and not duplication for
# its own sake: this package is the bottom layer and `objective.py` imports FROM it, so
# the import would be a cycle - and an independent second implementation of a constant a
# test asserts against is what this project's mutation gate asks for (`repo.ENRICHERS`,
# M6). The two are pinned together by `tests/test_report.py`, which drives a real scan
# through the scanner's reader and then reads the result back through this one.
_ERROR_KEY = "Error"


@dataclass(frozen=True)
class ReportFact:
    """What the catalog knows about one file, in the terms `pam report skipped` asks in.

    `has_hash` and `has_metadata` are BOOLEANS rather than the values themselves,
    because the report never shows a user a checksum or a blob of exiftool JSON (UX 8:
    no jargon leaks). The question is only ever "does pam have this or not".

    `has_metadata` is false for a file with no `capture` row AND for one whose row holds
    an empty `exif_json` object - the scanner writes that row when exiftool returned
    nothing usable, so the row's existence alone is not evidence pam read anything.

    `has_read_error` is the third such boolean and the one that is NOT implied by the
    other two: a record carrying an `Error` key is still a record, so `has_metadata` is
    true for it and `has_hash` is true as well (a file's bytes are readable even when
    its format is not). Before M7 those two facts together said "pam read this file",
    and a file exiftool could make nothing of appeared in no group of this report at
    all. The message itself is deliberately not carried - it is exiftool's wording, the
    session log has it, and the group's explanation is what the user needs.

    NO DEFAULT on any field, `has_read_error` included, for `SessionCounts.ops`' reason:
    a caller that could leave it out would be constructing a fact whose relationship to
    the record is unstated, and `report_facts` silently ceasing to set it would be a
    mutation nothing catches.
    """

    rel_path: str
    media_type: str
    status: str
    placeholder: bool
    has_hash: bool
    has_metadata: bool
    has_read_error: bool


def report_facts(conn: sqlite3.Connection) -> list[ReportFact]:
    """Every file pam knows about, with the three "did pam read it" facts. By path.

    EVERY file, not only the unread ones. The classifier needs to count what it did not
    list - "3 of 400" is the number the user is reading for - and a query that
    pre-filtered here would put half of the first-match-wins rule into SQL.

    LEFT JOIN, so a file with no `capture` row appears with `has_metadata` false rather
    than vanishing. An inner join would drop precisely the files this report exists to
    name, which is the failure mode vision principle 9 calls out: reporting a subset as
    if it were the whole.
    """
    rows = conn.execute(
        "SELECT f.rel_path, f.media_type, f.status, f.placeholder, f.sha256,"
        " c.exif_json"
        " FROM files f LEFT JOIN capture c ON c.file_id = f.id"
        " ORDER BY f.rel_path"
    )
    return [
        ReportFact(
            rel_path=str(r["rel_path"]),
            media_type=str(r["media_type"]),
            status=str(r["status"]),
            placeholder=bool(r["placeholder"]),
            has_hash=r["sha256"] is not None,
            has_metadata=r["exif_json"] is not None and str(r["exif_json"]) != "{}",
            has_read_error=_has_read_error(r["exif_json"]),
        )
        for r in rows
    ]


def _has_read_error(exif_json: object) -> bool:
    """Did exiftool report an `Error` for this file when the scanner read it?

    `json.loads` IN PYTHON, not `json_extract` in SQL, and the choice is deliberate.
    SQLite's JSON1 would be a real parser too, but every other field of `ReportFact` is
    derived here from a raw column, and this module's own docstring is emphatic that a
    predicate expressed half in SQL and half in Python drifts silently. Putting one of
    the six facts in the SELECT and the other five here is exactly that split. The blob
    is already being fetched for `has_metadata`, so nothing is saved by moving the
    parse into the query - and `json_extract` raises on a malformed blob, which would
    make one bad row take the whole report down instead of one file.

    AN EXACT KEY LOOKUP, never a substring test on the blob. `FileCreateDate`,
    `FileModifyDate` and `FileAccessDate` are in every record exiftool returns, so
    `"Error" in exif_json` on the raw text would be a different question with a
    plausible-looking answer - the mistake CLAUDE.md records this project making twice.

    A blob that will not parse is NOT reported as an error: pam wrote every one of them
    with `json.dumps`, so an unparseable one means something is wrong with the catalog
    rather than with the photo, and telling the user to replace the file would send them
    after the wrong thing. `pam verify` is the command that has an opinion about catalog
    damage.
    """
    if not isinstance(exif_json, str):
        return False
    try:
        record = json.loads(exif_json)
    except json.JSONDecodeError:
        return False
    return isinstance(record, dict) and _ERROR_KEY in record


@dataclass(frozen=True)
class DuplicateGroup:
    """Files whose bytes are identical (spec 8.1: exact dupes derived from `sha256`).

    The digest is carried so a test and a log line can name the group, and is NEVER
    rendered to the user - UX 8 keeps the word out of user-facing copy, and a user
    deciding which of two identical photos to keep gains nothing from 64 hex digits.
    """

    digest: str
    rel_paths: tuple[str, ...]


def duplicate_groups(conn: sqlite3.Connection) -> list[DuplicateGroup]:
    """Every group of 2+ present files sharing one digest, by path, groups by first path.

    `status = 'present'` ON BOTH SIDES of the subquery, and the group size is counted
    AFTER that filter. A `missing` row describes a file that was not on disk at the last
    scan; pairing it with a file that IS there and calling them duplicates would invite
    the user to delete one of two photos when only one of them exists. Filtering only
    the outer query would have reported such a pair as a one-member "duplicate group",
    which is worse than not reporting it.

    Files with no digest cannot be compared at all and are excluded - a sidecar is never
    hashed, and a file the scanner could not read has nothing to compare. Those files
    are `pam report skipped`'s subject, not this one, and NULL never equals NULL in SQL
    anyway, so the guard is documentation as much as it is a filter.
    """
    rows = conn.execute(
        "SELECT sha256, rel_path FROM files"
        " WHERE status = 'present' AND sha256 IS NOT NULL AND sha256 IN ("
        "   SELECT sha256 FROM files WHERE status = 'present' AND sha256 IS NOT NULL"
        "   GROUP BY sha256 HAVING COUNT(*) > 1)"
        " ORDER BY sha256, rel_path"
    )
    grouped: dict[str, list[str]] = {}
    for row in rows:
        grouped.setdefault(str(row["sha256"]), []).append(str(row["rel_path"]))
    groups = [
        DuplicateGroup(digest=digest, rel_paths=tuple(paths))
        for digest, paths in grouped.items()
    ]
    # By first path rather than by digest: a digest order is an arbitrary order the user
    # cannot predict, and two runs over the same archive should list the same groups in
    # the same places. Paths are what the reader is scanning for.
    groups.sort(key=lambda g: g.rel_paths[0])
    return groups


def phash_by_sha256(conn: sqlite3.Connection) -> dict[str, str]:
    """sha256 -> its live `phash` hex digest, for contents some present, real file holds.

    ONE HASH PER CONTENT, the newest live fact's (`ORDER BY id`, the last write wins): design
    4 keys facts by `sha256`, so two files with identical bytes share one fingerprint and are
    one content to compare - which is what `pam report duplicates`' look-alikes group by.
    `superseded_by IS NULL` for `_fact_coverage`'s reason (`enrichment.py`). Only contents
    held by a `present`, non-placeholder file: a missing file is not there to look like
    anything, and a placeholder is never read (vision R2). Videos never have one - the
    enricher's `wants()` refuses them.

    A `value_json` that does not parse or has no string `hash` is skipped rather than raised:
    pam wrote every one with `json.dumps`, so a bad one is catalog damage, and that is
    `pam verify`'s subject, not a reason to deny the user the rest of the report.
    """
    rows = conn.execute(
        "SELECT sha256, value_json FROM inferred_facts"
        " WHERE superseded_by IS NULL AND kind = 'phash' AND sha256 IN ("
        "   SELECT sha256 FROM files"
        "   WHERE status = 'present' AND placeholder = 0 AND sha256 IS NOT NULL)"
        " ORDER BY id"
    )
    hashes: dict[str, str] = {}
    for row in rows:
        try:
            value = json.loads(str(row["value_json"]))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("hash"), str):
            hashes[str(row["sha256"])] = value["hash"]
    return hashes


@dataclass(frozen=True)
class CalibrationFace:
    """One detection as `pam faces calibrate` reads it (M13 Task 14, H7).

    `person_label` is None for a face nobody has named: the sweep clusters EVERY face, named
    or not, because a merge that exists only through an unnamed face is still a merge (the
    sweep's `indirect` column). `rel_paths` is every file holding the face's content, present
    and missing alike, for `--exclude-path` - the same set `pam faces export` publishes.
    """

    id: int
    embedding: bytes
    det_confidence: float
    person_label: str | None
    review_state: str | None
    model_name: str
    model_version: str
    rel_paths: tuple[str, ...]


def labelled_face_embeddings(conn: sqlite3.Connection) -> list[CalibrationFace]:
    """Every face with its embedding, its person's label and its `review_state`, by id.

    FILED HERE AND NOT IN `faces.py`, which is at its ruled line ceiling: like every query in
    this module it answers one command's question, and it reads `face_instances`, `persons`
    and `files` together - the relationship, not a table. NO FLOOR AND NO `review_state`
    FILTER: the command excludes, and says by which reason, so a filter here would hide a
    face from the printed exclusion counts. Two statements, neither binding a list (L1).
    """
    paths: dict[str, list[str]] = {}
    for row in conn.execute(
        "SELECT sha256, rel_path FROM files WHERE sha256 IS NOT NULL ORDER BY rel_path"
    ):
        paths.setdefault(str(row["sha256"]), []).append(str(row["rel_path"]))
    rows = conn.execute(
        "SELECT a.id AS id, a.sha256 AS sha256, a.embedding AS embedding,"
        " a.det_confidence AS det_confidence, a.review_state AS review_state,"
        " a.model_name AS model_name, a.model_version AS model_version,"
        " p.label AS person_label"
        " FROM face_instances a LEFT JOIN persons p ON p.id = a.person_id"
        " ORDER BY a.id"
    )
    return [
        CalibrationFace(
            id=int(r["id"]),
            embedding=bytes(r["embedding"]),
            det_confidence=float(r["det_confidence"]),
            person_label=None if r["person_label"] is None else str(r["person_label"]),
            review_state=None if r["review_state"] is None else str(r["review_state"]),
            model_name=str(r["model_name"]),
            model_version=str(r["model_version"]),
            rel_paths=tuple(paths.get(str(r["sha256"]), ())),
        )
        for r in rows
    ]
