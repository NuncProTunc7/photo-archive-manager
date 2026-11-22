"""Catalog SQL for the events and file_events tables. All SQL lives under catalog/repo/.

This module OWNS both tables (`tests/guards.py::REPO_OWN_TABLES`), which is a
declaration of ownership and not a cross-table reach: `file_events` is the membership
side of `events` and holds nothing else, exactly as `companion_groups` and
`files.companion_group_id` are two halves of one link. No query here names `files`,
`capture` or any other table, so `ALLOWED_REPO_REACHES` stays at five pairs.

`Event` is imported for TYPING ONLY. `pam.events.grouping` imports `DatedFile` from
this package, so a runtime import here would close a cycle - and the direction that
matters is the one that already exists elsewhere in the project
(`pam.naming.tokens` imports `repo.PlanFile`): the repo layer owns the row shapes and
the domain reads them.

`event_membership` is the ONE read function here, added by M8 Task 9 - the milestone
that made `{event}` and `{seq}`'s scope real (spec 6.1 rev 14). Through Task 8 this
module was write-only on purpose, and the M1 deletion gate is why: nothing in
production read these tables, so nothing here was allowed to offer a read. The review
nav (Task 10) reads them through `review.py`, which is a consumer-keyed module.

The suite still reads these tables back with its OWN SQL (`tests/test_planner.py`'s
`stored_events`, `tests/test_events_grouping.py`), and must keep doing so. That is the
shape the M5 review settled for exactly this situation (`repo.open_plan`): an oracle
that calls the production query it is checking agrees with any value of it.
"""

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pam.events.grouping import Event

# spec 8.3 rev 14 / M8 plan Task 7 Step 4. The identity a run replaces is
# (algorithm, algo_version, params_json), and the first two are constants of this
# implementation: `timegap` is gap-plus-GPS-jump, and the version moves when the
# partition the same parameters produce would change.
ALGORITHM = "timegap"
ALGO_VERSION = "1"

# The vocabulary `events.label_source` may hold (migration 0006). 'geocode' is written by
# `pam plan`'s step 0 from spec 8.3's centroid; 'user' is a person's own label, which no
# command writes yet and which the migration backfilled onto every pre-0006 label because
# a person was the only thing that could have written one. NULL is "no label", and after
# 0006 `label_source IS NULL` and `label IS NULL` mean the same thing on every row.
#
# A declared tuple rather than a CHECK constraint: no migration in this catalog uses one,
# and this project's idiom for a vocabulary the whole product must agree about is a table
# in one module (`plan_items.ITEM_STATUSES`, `journal.OPS`, `enrichment.ENRICHERS`).
# `naming/tokens.py::EVENT_LABEL_SOURCE_TIER` is spec 6.1 rev 15's derivation from it and
# is the one consumer that matters.
GEOCODE_LABEL_SOURCE = "geocode"
USER_LABEL_SOURCE = "user"
LABEL_SOURCES = (GEOCODE_LABEL_SOURCE, USER_LABEL_SOURCE)


def _params_json(gap_hours: float, gps_km: float) -> str:
    """The `params_json` identity for one set of parameters.

    The identity is compared as a STRING, so two spellings of the same parameters read
    as two identities and every re-plan would churn every event id. `float()` is what
    stops that happening for real: `pam.toml` may hold `gap_hours = 4` and a default
    may be `4.0`, and `json.dumps` writes those as `4` and `4.0`.
    """
    return json.dumps({"gap_hours": float(gap_hours), "gps_km": float(gps_km)}, sort_keys=True)


@dataclass(frozen=True)
class EventMembership:
    """The event one file belongs to, and the label naming reads off it (spec 6.1 rev 14).

    `label` is `None` for every event M8 produces - `_insert_event` leaves it NULL
    because labels come from geocoding and geocoding is M9 (spec 8.3). It is `str |
    None` rather than `str` so that "no label" and "the empty label" cannot be told
    apart by accident downstream: both emit no `{event}` token at all.
    """

    event_id: int
    label: str | None
    # spec 6.1 rev 15 / migration 0006: WHICH FACT PRODUCED THE LABEL, so that
    # `{event}`'s tier can be derived from it instead of asserted. `None` where there is
    # no label. This field is the whole reason 0006 exists - M8 hard-coded `high` because
    # a person was the only writer, and M9's geocoder is the second.
    #
    # NO DEFAULT, deliberately, on `resolve_tokens`' own precedent about its `event`
    # argument: a default would let a caller that simply forgot the provenance produce a
    # label rated by `tokens.UNKNOWN_LABEL_SOURCE_TIER` and never notice, and the wiring
    # rather than the logic is what goes wrong on this project. With no default,
    # forgetting is a TypeError at the call site.
    label_source: str | None


def event_membership(conn: sqlite3.Connection) -> dict[int, EventMembership]:
    """file_id -> the event it belongs to, for every grouped file (spec 6.1 rev 14).

    The two consumers are spec 6.1's `{event}` token and its `{seq}` scope, both in
    `pam plan` and both needing the answer for every unit at once - a per-file query
    would be one round trip per file over an archive of tens of thousands. `file_id` is
    `file_events`' PRIMARY KEY, so at most one row per file and the dict cannot lose one.

    The INNER JOIN is defence in depth, and the stage-1 review of M8 Task 9 measured
    exactly how much (F2/M966). Spec 6.1 rev 6 makes `(source_table='events',
    source_id=<event id>)` a WRITE-TIME postcondition: the row the pointer names must
    exist when the plan is written. What actually makes that hold is that **a label
    lives in the `events` row** - an id with no row has no label, so `{event}` emits no
    token and no pointer is written. Beneath that, `file_events.event_id` carries
    `REFERENCES events(id)` and `db.connect` sets `PRAGMA foreign_keys=ON`, so an orphan
    row is refused by the database. The join is the third layer, and it earns its place
    for the same reason spec 7.5 keeps an orphan check: a catalog can be corrupted from
    outside pam. It is NOT the layer holding rev 6 up - do not delete either of the
    other two on the strength of it.

    No ORDER BY: the result is a dict keyed by `file_id`, so row order cannot reach a
    caller. `_seq_numbers` orders by `(capture_dt, old filename)` from the units it was
    given, never by whatever SQLite returned here.
    """
    rows = conn.execute(
        "SELECT fe.file_id, fe.event_id, e.label, e.label_source"
        " FROM file_events fe JOIN events e ON e.id = fe.event_id"
    )
    return {
        int(r["file_id"]): EventMembership(
            event_id=int(r["event_id"]),
            label=None if r["label"] is None else str(r["label"]),
            label_source=(None if r["label_source"] is None else str(r["label_source"])),
        )
        for r in rows
    }


def replace_events(
    conn: sqlite3.Connection,
    events: list["Event"],
    *,
    gap_hours: float,
    gps_km: float,
    labels: Sequence[str | None] | None = None,
) -> list[int]:
    """Store `events` as the archive's grouping, returning one id per event, in order.

    spec 8.3 rev 14: "a replacing run REUSES the existing row where (start_dt, end_dt)
    is unchanged, so a re-plan over unchanged facts produces no id churn and the
    `{event}` provenance pointers of terminal plans stay live in the common case". A
    row is reusable only when its whole identity matches as well - the row RECORDS the
    parameters it was computed under, so reusing one across a `gap_hours` edit would
    leave it claiming parameters that did not produce it.

    Every `file_events` row goes first, and that is not a shortcut. `file_events.file_id`
    is a PRIMARY KEY, so a file belongs to at most one event and only one grouping can
    be live at a time; grouping is a full-catalog pass over every dated file, so no
    membership from an earlier run survives it. Any `events` row this run does not reuse
    then goes too, whatever identity it carries: a row nothing points at is an event
    with no members, which is not a thing spec 8.3 describes.

    Deletion order is load-bearing - `PRAGMA foreign_keys=ON` (catalog/db.py) would
    refuse to delete an `events` row a `file_events` row still references.

    `labels` IS THREE-VALUED AND THE THIRD VALUE IS THE POINT (M9 Task 9, spec 8.3's
    "Label = geocoded place of the event's GPS centroid, else empty").

    - `None` for the whole argument: this run learned nothing about labels, because the
      geocoder is not installed or its offline dataset is gone. Existing labels are left
      exactly as they are and new rows get NULL. That is what makes `pam plan` keep
      working on a machine without `reverse_geocoder` instead of silently erasing the
      labels a machine with it wrote.
    - A sequence, one entry per event in the same order: these ARE the labels. A `None`
      entry means "this event has no place" and CLEARS a stale one, which is the whole
      difference from the case above - an event whose GPS-bearing members went away must
      not keep naming files after a place it no longer covers.
    - A 'user' label is never overwritten by either. A person's own label outranks a
      geocode (spec 6.1 rev 15 gives it `high` and the geocode `medium`), and a re-plan
      is not a thing that should undo it.

    THE UPDATE IS AN UPDATE AND NOT A DELETE-AND-INSERT, and that is spec 8.3's reuse
    rule doing its job: a row that merely GAINS a label keeps its id, so every
    `{event}` provenance pointer written into a terminal plan
    (`(source_table='events', source_id=<id>)`, spec 6.1 rev 14) stays live. Re-inserting
    would retire every one of them at the first plan after the geocoder was installed.
    """
    params = _params_json(gap_hours, gps_km)

    reusable: dict[tuple[str, str], list[int]] = {}
    for row in conn.execute(
        "SELECT id, start_dt, end_dt FROM events"
        " WHERE algorithm = ? AND algo_version = ? AND params_json = ?"
        " ORDER BY id",
        (ALGORITHM, ALGO_VERSION, params),
    ):
        # A list, not a single id: two events CAN share a span. Two photographs taken
        # at the same instant a continent apart are one GPS split, and both events
        # then have start_dt == end_dt == that instant.
        reusable.setdefault((str(row["start_dt"]), str(row["end_dt"])), []).append(
            int(row["id"])
        )

    conn.execute("DELETE FROM file_events")

    if labels is not None and len(labels) != len(events):
        raise ValueError(f"{len(labels)} labels for {len(events)} events")

    event_ids: list[int] = []
    for index, event in enumerate(events):
        label = None if labels is None else labels[index]
        candidates = reusable.get((event.start_dt, event.end_dt))
        if candidates:
            event_id = candidates.pop(0)
            if labels is not None:
                _set_label(conn, event_id, label)
            event_ids.append(event_id)
        else:
            event_ids.append(_insert_event(conn, event, params, label))

    _delete_all_but(conn, event_ids)

    conn.executemany(
        "INSERT INTO file_events (file_id, event_id) VALUES (?, ?)",
        [
            (file_id, event_id)
            for event, event_id in zip(events, event_ids, strict=True)
            for file_id in event.file_ids
        ],
    )
    return event_ids


def _insert_event(
    conn: sqlite3.Connection, event: "Event", params: str, label: str | None
) -> int:
    """One new events row, with the label this run geocoded for it (spec 8.3).

    `label` is None whenever the geocoder produced nothing for this event - it has no
    GPS-bearing member, the dataset has no name for its centroid, or the geocoder is not
    installed at all - and `label_source` moves with it, never apart from it: the two
    columns are written in the same statement so that "labelled by nothing" is not a
    state this table can reach.
    """
    row = conn.execute(
        "INSERT INTO events (label, label_source, start_dt, end_dt, algorithm,"
        " algo_version, params_json) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
        (
            label,
            GEOCODE_LABEL_SOURCE if label is not None else None,
            event.start_dt,
            event.end_dt,
            ALGORITHM,
            ALGO_VERSION,
            params,
        ),
    ).fetchone()
    return int(row["id"])


def _set_label(conn: sqlite3.Connection, event_id: int, label: str | None) -> None:
    """Put this run's geocoded label on a REUSED row, keeping its id (spec 8.3).

    The `label_source` clause is what protects a person's own label: a row a user named
    is not re-named by a re-plan, and a row a previous geocode named is. A row with no
    label at all has `label_source IS NULL` (migration 0006 made that true of every
    pre-0006 row too) and is free to take one.
    """
    conn.execute(
        "UPDATE events SET label = ?, label_source = ? WHERE id = ?"
        " AND (label_source IS NULL OR label_source = ?)",
        (
            label,
            GEOCODE_LABEL_SOURCE if label is not None else None,
            event_id,
            GEOCODE_LABEL_SOURCE,
        ),
    )


def _delete_all_but(conn: sqlite3.Connection, keep: list[int]) -> None:
    """Drop every events row this run did not reuse or insert.

    THE SET DIFFERENCE IS TAKEN IN PYTHON, AND EACH UNKEPT ROW IS DELETED BY ITS OWN ID
    (design 2 rev 32, L1). The keep list has one id per event, so it grows with the archive,
    and a single `NOT IN (?, ...)` crossed SQLite's bound-variable limit at 100,000 files
    (M13 Task 13). Chunking that `NOT IN` would be WRONG, not merely slow: each chunk's
    statement would delete every other chunk's kept ids. Reading the table's ids and
    deleting `stored - keep` one `?` at a time binds one variable per statement, whatever
    the archive's size, and deletes exactly the rows a single `NOT IN` would have.
    It runs in the caller's transaction and never commits.
    """
    if not keep:
        conn.execute("DELETE FROM events")
        return
    kept = set(keep)
    unkept = sorted(
        event_id
        for event_id in (int(r[0]) for r in conn.execute("SELECT id FROM events"))
        if event_id not in kept
    )
    conn.executemany("DELETE FROM events WHERE id = ?", [(event_id,) for event_id in unkept])
