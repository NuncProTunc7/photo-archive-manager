"""spec 6.3 rule 5b / spec 8.3: `pam plan`'s STEP 0, the event partition.

M10 review condition C2. This is `naming/planner.py`'s `_group_events` moved out
VERBATIM ahead of M11's `{people}` token, which touches `_plan_unit` and `_plan` - the
two largest functions in a file that was seven executable lines from the ~300 budget.
The function was renamed on the move: it calls `pam/events/grouping.py::group_events`,
and `group_events` calling `group_events` across two modules both called `events` is a
reading hazard, not a naming style. `PlanSummary` is in `plan_summary.py` (M14 Task 5a's cut).
"""

import logging
from dataclasses import replace
from sqlite3 import Connection
from typing import TYPE_CHECKING

from pam.catalog import repo
from pam.config import Config
from pam.enrich.geocode import event_labels
from pam.events.grouping import group_events
from pam.naming.dates import resolve_date

if TYPE_CHECKING:  # pragma: no cover - import-cycle avoidance, not behaviour
    # `planner.py` imports this module at runtime, so it must not import `planner` back.
    # `PlanSummary` is needed only as a type annotation, which is what TYPE_CHECKING is
    # for: mypy sees the real type, the interpreter sees none.
    from pam.naming.planner import PlanSummary


def group_events_step(
    conn: Connection,
    config: Config,
    summary: "PlanSummary",
    logger: logging.Logger,
    *,
    readings: dict[int, repo.OcrReading],
) -> None:
    """spec 6.3 rule 5b rev 14 / spec 8.3 rev 14: the planner's STEP 0.

    Grouping runs FIRST - before a single name is generated - and inside this
    command's own transaction. Three things about that are load-bearing:

    - *First*, because spec 6.1's `{event}` and spec 6.3 rule 2's `{seq}` scope are
      read off the partition (M8 Task 9). A name generated before the partition exists
      would be a name generated from the PREVIOUS run's events, and the plan would
      freeze parameters it was not built under.
    - *Here and not in `pam scan`*: grouping is a full-catalog pass and scan's 0.03 s
      zero-work re-scan is a measured, defended property (vision P2). No scan-time
      consumer reads events, so no scan should pay for them.
    - *Unconditionally*, for the same reason rule 5's supersede is unconditional and
      sits above the early return: spec 8.3 rev 14 groups "all present, dated files",
      which is a statement about the catalog and not about the plan. An archive whose
      files all already conform plans nothing and is still an archive with events in
      it, and `pam review`'s nav (M8 Task 10) reads the catalog, not the drafts. The
      caller - `naming/planner.py::_plan`, still the only one - therefore places this
      call above every early exit; do not move it into a branch, and note that the
      constraint now lives in a different file from the code it constrains.

    THE LABELS ARE GEOCODED HERE, IN THE SAME STEP (M9 Task 9, spec 8.3: "Label =
    geocoded place of the event's GPS centroid, else empty"). They belong to the `events`
    row and `repo.replace_events` is the only writer of one, so the centroid computed by
    `group_events` is turned into a name before that single write rather than by a second
    pass that would have to find the rows again.

    `pam plan` DOES NOT REQUIRE THE GEOCODER. `event_labels` returns `None` - not an
    empty list - when `reverse_geocoder` or its offline dataset is missing, and
    `replace_events` reads that as "leave existing labels alone". A machine without it
    still plans, still groups, and simply emits no `{event}` token for events nothing has
    named yet, which is spec 6.1's graceful degradation. `pam doctor` is where the
    missing package is a legible line.

    No commit here. Whichever exit `_plan` takes commits this write together with the
    supersede and (on the write path) the plan itself - so a failure between grouping
    and the plan write leaves NO events rows behind, which is the property spec 6.3
    rule 5b's "in this command's transaction" actually buys.

    EVERY FILE IS GROUPED UNDER ITS EFFECTIVE DATE (M12, design 6.1 rev 27, I3). `readings`
    is the planner's one read of `repo.ocr_readings_by_file_id`, and `resolve_date` - the
    same function the name uses - replaces each file's `capture_dt` before grouping, so a
    scan's event, its nav month and its name agree. `group_events` sorts its own input, so
    nothing is re-sorted here, and `ALGO_VERSION` does not move: P9 changes the algorithm's
    INPUTS, not the algorithm, and spec 8.3's reuse-by-span rule already handles moved dates.
    """
    bounds = config.tiers["ocr"]
    dated: list[repo.DatedFile] = []
    for file in repo.dated_files_for_events(conn):
        resolved = resolve_date(
            capture_dt=file.capture_dt,
            capture_dt_source=file.capture_dt_source,
            rel_path=file.rel_path,
            reading=readings.get(file.file_id),
            bounds=bounds,
            folders=config.ocr_folders,
        )
        # `resolve_date` hands back the date it was given unless it upgrades it, and every
        # row here has one (the query's `capture_dt IS NOT NULL`).
        assert resolved.capture_dt is not None
        dated.append(replace(file, capture_dt=resolved.capture_dt))
    events = group_events(
        dated,
        gap_hours=config.event_gap_hours,
        gps_km=config.event_gps_km,
    )
    labels = event_labels([event.centroid for event in events], logger=logger)
    repo.replace_events(
        conn,
        events,
        gap_hours=config.event_gap_hours,
        gps_km=config.event_gps_km,
        labels=labels,
    )
    summary.events = len(events)
    logger.info(
        "plan step 0: %d events, %d labelled (gap_hours=%s gps_km=%s)",
        summary.events,
        sum(1 for label in labels or () if label),
        config.event_gap_hours,
        config.event_gps_km,
    )
