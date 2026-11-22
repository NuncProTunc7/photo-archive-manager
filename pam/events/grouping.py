"""spec 8.3 rev 14: the event partition, as a pure function.

Nothing here opens a file, reads a photograph or touches the catalog. `group_events`
takes the rows `repo.dated_files_for_events` selected and returns the partition; the
caller stores it. That is what makes the placeholder rule in spec 8.3 rev 14 hold by
construction rather than by care: a cloud placeholder has no `capture` row, so it is
not in the input, and there is no code path here that could read one even if it were
(vision R2 forbids hydrating a placeholder).

The algorithm, from spec 8.3 rev 14, sentence by sentence:

- Files are walked in `capture_dt` order.
- A new event starts when the gap to the previous file is GREATER THAN `gap_hours` or
  the GPS jump is GREATER THAN `gps_km`. `>`, not `>=`: a gap of exactly `gap_hours`
  does not split, and neither does a jump of exactly `gps_km`.
- The GPS jump is measured between a file with GPS and the most recent earlier file
  IN THE SAME EVENT that had GPS. A file without GPS neither starts a split nor
  prevents one - absence of evidence is not evidence.
- `start_dt`/`end_dt` are the min and max `capture_dt` of the event's members.

The words "in the same event" are the subtle ones and are why `_anchor` is cleared
whenever an event ends. When a GAP splits, the new event's first GPS-bearing file has
no earlier GPS-bearing file within its own event, so it cannot itself trigger a GPS
split - however far it is from the last photograph of the previous event. Comparing it
to that one would split the new event's very first file away from itself.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from pam.catalog.repo import DatedFile

# The mean Earth radius the haversine uses, in kilometres. spec 8.3 rev 14 names the
# formula and the units and leaves the radius to the implementation; it is a named
# constant rather than a literal in the expression so that a test can pin the value
# instead of agreeing with whatever the expression happens to compute.
EARTH_RADIUS_KM = 6371.0

# Below this length, the mean of the unit vectors has no meaningful direction and
# `gps_centroid` reports no centroid. A named constant rather than a literal for the same
# reason as the radius above: a test can pin the value instead of agreeing with whatever
# the expression happens to compute. 1e-9 is far below any resultant a real event
# produces - even two exactly antipodal points are the only way to reach it - and far
# above the floating-point noise of summing a few thousand unit vectors.
_DEGENERATE_CENTROID = 1e-9


@dataclass(frozen=True)
class Event:
    """One event: its members, and the span they cover.

    `file_ids` is a tuple in `capture_dt` order, so two runs over the same facts
    compare equal element by element - spec 8.3's "deterministic" is a property of
    this value, and a list would still compare equal but could be mutated by a caller
    into something the algorithm never produced.
    """

    file_ids: tuple[int, ...]
    start_dt: str
    end_dt: str
    # spec 8.3: "Label = geocoded place of the event's GPS centroid, else empty." The
    # centroid is a property of the partition and is computed here, where the members
    # are; the geocoding is `enrich/geocode.py`'s and the label is the catalog's. `None`
    # when no member carries GPS - which is the "else empty" half of that sentence.
    #
    # It carries a DEFAULT so that a caller constructing an Event by hand (the suite's
    # grouping oracle does) is not forced to state a centroid it is not testing. The
    # production path always sets it: `_event` below computes it from the members.
    centroid: tuple[float, float] | None = None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres. `math` only - spec 8.3 rev 14, no dependency.

    Deliberately NOT geopy. The system Python on the developer's machine carries it, so an
    import here would work on this machine and fail on a clean install - and a great
    circle is fifteen lines of `math`.

    (M9 Task 6 spends the runtime closure, which was exactly `{click, pam}` when this was
    written. That does not re-open this: design 1's stack table sanctions four named
    packages for four named jobs, and "the trigonometry function I was about to write"
    is not one of them. A dependency this project takes is one the spec chose in advance;
    `tests/test_deps.py` is where the list lives.)
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _gps(file: DatedFile) -> tuple[float, float] | None:
    """The file's coordinates, or None if it carries no GPS.

    Both columns are nullable and are written together by the scanner, but "has GPS"
    is asked in three places below and a half-populated row must not be treated as
    positioned, so the question is answered once, here.
    """
    if file.gps_lat is None or file.gps_lon is None:
        return None
    return (file.gps_lat, file.gps_lon)


def group_events(files: list[DatedFile], *, gap_hours: float, gps_km: float) -> list[Event]:
    """Partition `files` into events (spec 8.3 rev 14).

    `files` is every present, dated file - image and video alike. The caller need not
    pre-sort: spec 8.3's first sentence is "sort files by `capture_dt`", so the sort is
    part of the algorithm rather than a precondition on the caller. `file_id` breaks a
    tie, so the answer does not depend on the order the rows arrived in.

    IF YOU CHANGE WHAT THIS FUNCTION PARTITIONS, BUMP `repo.events.ALGO_VERSION`.
    The constants naming this algorithm live in the WRITER, not here - row identity is a
    catalog concern, and importing them back would close a cycle (`grouping.py` already
    imports `DatedFile` from the repo package, on `naming/tokens.py`'s `PlanFile`
    precedent). That split is deliberate and it has one cost: the obligation is not
    visible from the file you would actually be editing. Hence this sentence. A changed
    partition under an unchanged `(algorithm, algo_version, params_json)` identity makes
    spec 8.3's reuse rule silently keep rows computed by the OLD algorithm, because reuse
    is keyed on `(start_dt, end_dt)` within an identity and a stale span can coincide.
    """
    gap = timedelta(hours=gap_hours)

    parsed = [(datetime.fromisoformat(f.capture_dt), f) for f in files]
    parsed.sort(key=lambda pair: (pair[0], pair[1].file_id))

    events: list[Event] = []
    members: list[tuple[datetime, DatedFile]] = []
    previous: datetime | None = None
    anchor: tuple[float, float] | None = None  # most recent GPS IN THIS EVENT

    for moment, file in parsed:
        here = _gps(file)
        if previous is not None:
            gap_split = moment - previous > gap
            gps_split = (
                anchor is not None
                and here is not None
                and haversine_km(anchor[0], anchor[1], here[0], here[1]) > gps_km
            )
            if gap_split or gps_split:
                events.append(_event(members))
                members = []
                # The new event has no earlier GPS-bearing member of its own yet.
                anchor = None
        members.append((moment, file))
        if here is not None:
            anchor = here
        previous = moment

    if members:
        events.append(_event(members))
    return events


def gps_centroid(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """The mean position of `points` on the sphere, or None if there are none.

    NOT THE ARITHMETIC MEAN OF THE LATITUDES AND LONGITUDES, and the difference is a real
    wrong answer rather than a rounding one. Longitude wraps at +/-180: an event spanning
    the antimeridian (Fiji, Kiribati, the Aleutians, a flight over the Pacific) holds
    values near +179 and near -179, whose arithmetic mean is 0 - the Gulf of Guinea, a
    third of the way round the planet, and a place name that would be written into a
    filename. The mean of the unit vectors has no such seam anywhere on the sphere and
    is the standard reading of "centroid" for coordinates.

    THE ANTIPODAL CASE RETURNS None RATHER THAN A GUESS. Points spread evenly enough over
    the sphere sum to (almost) the zero vector, whose direction is undefined; there is no
    centroid to report and any answer picked here would be arbitrary. An event with no
    centroid gets no label, which spec 6.1 already handles - "an event with an empty label
    emits no token at all" - and a missing token is always safer than a wrong one
    (vision principle 5: never guess).
    """
    if not points:
        return None
    x = y = z = 0.0
    for lat, lon in points:
        phi, lam = math.radians(lat), math.radians(lon)
        x += math.cos(phi) * math.cos(lam)
        y += math.cos(phi) * math.sin(lam)
        z += math.sin(phi)
    count = len(points)
    x, y, z = x / count, y / count, z / count
    if math.hypot(x, y, z) < _DEGENERATE_CENTROID:
        return None
    return (math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x)))


def _event(members: list[tuple[datetime, DatedFile]]) -> Event:
    """One event from its members: spec 8.3's min and max, taken over the members.

    Written as `min`/`max` over the member list rather than as "the first and last of
    a sorted list", because the spec sentence is about the members and reading it off
    the sort order would make `end_dt` true only for as long as the sort above stays
    exactly as it is.
    """
    start = min(members, key=lambda pair: pair[0])[1]
    end = max(members, key=lambda pair: pair[0])[1]
    positions = [gps for _, file in members if (gps := _gps(file)) is not None]
    return Event(
        file_ids=tuple(file.file_id for _, file in members),
        start_dt=start.capture_dt,
        end_dt=end.capture_dt,
        centroid=gps_centroid(positions),
    )
