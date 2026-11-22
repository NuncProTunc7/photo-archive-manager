"""spec 8.3 rev 14: the event partition, its input query and its writer.

READ THIS BEFORE ADDING A TEST HERE. "Compute a partition" is the exact shape where an
oracle built from the production code passes for any partition - CLAUDE.md's standing
rule, sharpened by M8 into "an oracle that asserts the PARTITION production code
produced is vacuous". So:

* `CASES` below is a table of LITERAL timestamps, LITERAL coordinates and the LITERAL
  expected membership and span of every event, written from spec 8.3 rev 14's
  sentences. No expectation in it is obtained by calling `group_events`.
* `_second_partition` is an INDEPENDENT second implementation, written from the same
  sentences in a deliberately different shape ("extend this event as far as it can go",
  with the GPS anchor found by scanning the event backwards, and the gap measured in
  float hours rather than as a `timedelta`). The property test compares the two.
* The writer's rows are read back with the suite's OWN SQL, never through a repo
  function - the M5 review's `repo.open_plan` ruling.
"""

import builtins
import math
import os
import pathlib
import random
from datetime import datetime, timedelta
from typing import NamedTuple

import pytest
from archives import FAKE_COMMAND, catalog, put

from pam.catalog import repo
from pam.catalog.db import connect, migrate
from pam.catalog.repo.events import replace_events
from pam.catalog.repo.plans import DatedFile, dated_files_for_events
from pam.events.grouping import EARTH_RADIUS_KM, Event, group_events, haversine_km
from pam.scan.runner import run_scan

# ---------------------------------------------------------------------------------
# Places, written out. Every coordinate used anywhere in this module comes from here,
# so a reader can check a stated distance against an atlas instead of against the
# function under test.
LONDON = (51.5074, -0.1278)
PARIS = (48.8566, 2.3522)  # ~344 km from London
EDINBURGH = (55.9533, -3.1883)  # ~534 km from London
NEW_YORK = (40.7128, -74.0060)  # ~5570 km from London
TOKYO = (35.6762, 139.6503)  # ~9560 km from London
# ~463 km from London and ~781 km from PARIS - the asymmetry the anchor case below
# turns on, and the reason it is Dublin rather than any other city.
DUBLIN = (53.3498, -6.2603)

# One degree of longitude on the equator. The haversine collapses to R * d_lambda when
# both latitudes are zero, so this is the radius times one degree in radians and is
# derived from the FORMULA, not from a call to `haversine_km`.
ONE_DEGREE_ON_THE_EQUATOR_KM = 6371.0 * math.radians(1.0)


def f(file_id: int, capture_dt: str, place: tuple[float, float] | None = None) -> DatedFile:
    """One grouping-input row. `place` None means the file carries no GPS."""
    lat, lon = (None, None) if place is None else place
    return DatedFile(file_id=file_id, capture_dt=capture_dt, gps_lat=lat, gps_lon=lon)


def rows(*specs: tuple) -> list[DatedFile]:
    return [f(*spec) for spec in specs]


def shape(events: list[Event]) -> list[tuple[tuple[int, ...], str, str]]:
    """An event list as plain data, so a failure prints the partition it produced."""
    return [(e.file_ids, e.start_dt, e.end_dt) for e in events]


# =================================================================================
# The spec table.
# =================================================================================


class Case(NamedTuple):
    name: str
    gap_hours: float
    gps_km: float
    files: tuple[tuple, ...]
    events: tuple[tuple[tuple[int, ...], str, str], ...]


CASES = (
    Case(
        name="an empty archive produces no events",
        gap_hours=4,
        gps_km=50,
        files=(),
        events=(),
    ),
    Case(
        name="a single file is one event whose start and end are its own moment",
        gap_hours=4,
        gps_km=50,
        files=((1, "2021-05-01T09:00:00"),),
        events=(((1,), "2021-05-01T09:00:00", "2021-05-01T09:00:00"),),
    ),
    Case(
        name="two files one second apart are one event",
        gap_hours=4,
        gps_km=50,
        files=((1, "2021-05-01T09:00:00"), (2, "2021-05-01T09:00:01")),
        events=(((1, 2), "2021-05-01T09:00:00", "2021-05-01T09:00:01"),),
    ),
    Case(
        # spec 8.3's operator is `>`. Exactly four hours is NOT more than four hours.
        name="a gap of exactly gap_hours does not split",
        gap_hours=4,
        gps_km=50,
        files=((1, "2021-05-01T09:00:00"), (2, "2021-05-01T13:00:00")),
        events=(((1, 2), "2021-05-01T09:00:00", "2021-05-01T13:00:00"),),
    ),
    Case(
        name="a gap one second over gap_hours splits",
        gap_hours=4,
        gps_km=50,
        files=((1, "2021-05-01T09:00:00"), (2, "2021-05-01T13:00:01")),
        events=(
            ((1,), "2021-05-01T09:00:00", "2021-05-01T09:00:00"),
            ((2,), "2021-05-01T13:00:01", "2021-05-01T13:00:01"),
        ),
    ),
    Case(
        name="a run with no GPS at all is grouped on time alone",
        gap_hours=4,
        gps_km=50,
        files=(
            (1, "2021-05-01T09:00:00"),
            (2, "2021-05-01T10:00:00"),
            (3, "2021-05-01T20:00:00"),
            (4, "2021-05-01T21:00:00"),
        ),
        events=(
            ((1, 2), "2021-05-01T09:00:00", "2021-05-01T10:00:00"),
            ((3, 4), "2021-05-01T20:00:00", "2021-05-01T21:00:00"),
        ),
    ),
    Case(
        # "a file without GPS neither starts a split nor PREVENTS one": the anchor is
        # the most recent earlier file in this event that HAD GPS, so files 2 and 3
        # do not hide London from file 4.
        name="gps only on the first and last file still splits across them",
        gap_hours=4,
        gps_km=50,
        files=(
            (1, "2021-05-01T09:00:00", LONDON),
            (2, "2021-05-01T09:30:00"),
            (3, "2021-05-01T10:00:00"),
            (4, "2021-05-01T10:30:00", NEW_YORK),
        ),
        events=(
            ((1, 2, 3), "2021-05-01T09:00:00", "2021-05-01T10:00:00"),
            ((4,), "2021-05-01T10:30:00", "2021-05-01T10:30:00"),
        ),
    ),
    Case(
        # The other half of the same sentence: a GPS-less file never STARTS a split.
        name="gps only on the first and last file does not split when they agree",
        gap_hours=4,
        gps_km=50,
        files=(
            (1, "2021-05-01T09:00:00", LONDON),
            (2, "2021-05-01T09:30:00"),
            (3, "2021-05-01T10:00:00"),
            (4, "2021-05-01T10:30:00", LONDON),
        ),
        events=(((1, 2, 3, 4), "2021-05-01T09:00:00", "2021-05-01T10:30:00"),),
    ),
    Case(
        # The exact GPS boundary, with no floating-point argument to have: the two
        # files share a coordinate, so the jump is exactly 0.0 and gps_km is 0.0.
        # `>` keeps them together; `>=` would split them.
        name="a jump of exactly gps_km does not split",
        gap_hours=4,
        gps_km=0.0,
        files=(
            (1, "2021-05-01T09:00:00", LONDON),
            (2, "2021-05-01T09:30:00", LONDON),
        ),
        events=(((1, 2), "2021-05-01T09:00:00", "2021-05-01T09:30:00"),),
    ),
    Case(
        name="any jump at all splits when gps_km is zero",
        gap_hours=4,
        gps_km=0.0,
        files=(
            (1, "2021-05-01T09:00:00", LONDON),
            (2, "2021-05-01T09:30:00", PARIS),
        ),
        events=(
            ((1,), "2021-05-01T09:00:00", "2021-05-01T09:00:00"),
            ((2,), "2021-05-01T09:30:00", "2021-05-01T09:30:00"),
        ),
    ),
    Case(
        name="london to paris is over fifty kilometres and splits",
        gap_hours=4,
        gps_km=50,
        files=(
            (1, "2021-05-01T09:00:00", LONDON),
            (2, "2021-05-01T09:30:00", PARIS),
        ),
        events=(
            ((1,), "2021-05-01T09:00:00", "2021-05-01T09:00:00"),
            ((2,), "2021-05-01T09:30:00", "2021-05-01T09:30:00"),
        ),
    ),
    Case(
        name="london to paris is under five hundred kilometres and does not split",
        gap_hours=4,
        gps_km=500,
        files=(
            (1, "2021-05-01T09:00:00", LONDON),
            (2, "2021-05-01T09:30:00", PARIS),
        ),
        events=(((1, 2), "2021-05-01T09:00:00", "2021-05-01T09:30:00"),),
    ),
    Case(
        # THE SUBTLEST SENTENCE IN SPEC 8.3 rev 14: "the most recent earlier file IN
        # THE SAME EVENT that had GPS". The 09:00 gap splits file 2 away, and file 2
        # has no GPS - so when file 3 arrives in New York, its event contains no
        # earlier GPS-bearing file and it CANNOT split. An implementation that keeps
        # London as the anchor across the gap produces three events instead of two.
        name="a gap split resets the gps anchor",
        gap_hours=4,
        gps_km=50,
        files=(
            (1, "2021-05-01T09:00:00", LONDON),
            (2, "2021-05-01T13:00:01"),
            (3, "2021-05-01T13:10:00", NEW_YORK),
        ),
        events=(
            ((1,), "2021-05-01T09:00:00", "2021-05-01T09:00:00"),
            ((2, 3), "2021-05-01T13:00:01", "2021-05-01T13:10:00"),
        ),
    ),
    Case(
        name="an event spans midnight",
        gap_hours=4,
        gps_km=50,
        files=(
            (1, "2021-05-01T23:00:00"),
            (2, "2021-05-01T23:59:59"),
            (3, "2021-05-02T01:00:00"),
        ),
        events=(((1, 2, 3), "2021-05-01T23:00:00", "2021-05-02T01:00:00"),),
    ),
    Case(
        name="an event spans a month boundary",
        gap_hours=4,
        gps_km=50,
        files=(
            (1, "2021-05-31T23:30:00"),
            (2, "2021-06-01T00:30:00"),
        ),
        events=(((1, 2), "2021-05-31T23:30:00", "2021-06-01T00:30:00"),),
    ),
    Case(
        # spec 8.3's first sentence is "sort files by capture_dt", so the sort belongs
        # to the algorithm and the caller may hand rows over in any order. The ids are
        # deliberately not in time order, so an implementation that trusted the input
        # order would produce (7, 3, 5) reordered or a different partition entirely.
        name="unsorted input is sorted before it is grouped",
        gap_hours=4,
        gps_km=50,
        files=(
            (5, "2021-05-01T11:00:00"),
            (7, "2021-05-01T09:00:00"),
            (3, "2021-05-01T10:00:00"),
        ),
        events=(((7, 3, 5), "2021-05-01T09:00:00", "2021-05-01T11:00:00"),),
    ),
    Case(
        # spec 8.3 rev 14 says "the MOST RECENT earlier file in the same event that had
        # GPS", and nothing in this table distinguished that from "the FIRST such file"
        # until this row (Task 7 review, M924 - the mutation survived the whole suite).
        # At gps_km=500: London->Paris is 344 km and joins, so the anchor MOVES to
        # Paris; Paris->Dublin is 781 km and splits. Under the "first" reading the
        # anchor would still be London, London->Dublin is 463 km, and file 3 would
        # stay in the event. The three distances are atlas figures, not outputs.
        name="the gps anchor is the most recent earlier file with gps, not the first",
        gap_hours=4,
        gps_km=500,
        files=(
            (1, "2021-05-01T09:00:00", LONDON),
            (2, "2021-05-01T09:30:00", PARIS),
            (3, "2021-05-01T10:00:00", DUBLIN),
        ),
        events=(
            ((1, 2), "2021-05-01T09:00:00", "2021-05-01T09:30:00"),
            ((3,), "2021-05-01T10:00:00", "2021-05-01T10:00:00"),
        ),
    ),
    Case(
        # The `file_id` tiebreak in the sort, which nothing pinned until the Task 7
        # review (M911 survived the whole suite): every other row here uses distinct
        # timestamps, so a stable sort by moment alone was indistinguishable. Two files
        # on one second is ordinary - a burst, or a live-photo pair whose image and
        # video carry the same capture second.
        name="two files sharing a capture_dt are ordered by file id",
        gap_hours=4,
        gps_km=50,
        files=((9, "2021-05-01T09:00:00"), (2, "2021-05-01T09:00:00")),
        events=(((2, 9), "2021-05-01T09:00:00", "2021-05-01T09:00:00"),),
    ),
    Case(
        name="three events from two gaps",
        gap_hours=1,
        gps_km=50,
        files=(
            (1, "2021-05-01T09:00:00"),
            (2, "2021-05-01T09:30:00"),
            (3, "2021-05-01T12:00:00"),
            (4, "2021-05-02T08:00:00"),
            (5, "2021-05-02T08:45:00"),
        ),
        events=(
            ((1, 2), "2021-05-01T09:00:00", "2021-05-01T09:30:00"),
            ((3,), "2021-05-01T12:00:00", "2021-05-01T12:00:00"),
            ((4, 5), "2021-05-02T08:00:00", "2021-05-02T08:45:00"),
        ),
    ),
)


# The scenarios the M8 plan's Task 7 Step 5 names, written out HERE rather than
# derived from `CASES`. A table can only shrink silently if nothing outside it knows
# what it is supposed to contain - the "a copy pin needs a floor" lesson (M769/M776).
REQUIRED_CASES = frozenset(
    {
        "an empty archive produces no events",
        "a single file is one event whose start and end are its own moment",
        "two files one second apart are one event",
        "a gap of exactly gap_hours does not split",
        "a gap one second over gap_hours splits",
        "a run with no GPS at all is grouped on time alone",
        "gps only on the first and last file still splits across them",
        "a jump of exactly gps_km does not split",
        "a gap split resets the gps anchor",
        "an event spans midnight",
        "an event spans a month boundary",
        # Added by the Task 7 review; each closes a mutation that survived the
        # whole suite (M924 and M911 respectively).
        "the gps anchor is the most recent earlier file with gps, not the first",
        "two files sharing a capture_dt are ordered by file id",
    }
)


def test_the_case_table_is_not_empty_and_covers_every_required_scenario():
    """A parametrized table that empties is SKIPPED by pytest, not failed (M743)."""
    assert len(CASES) >= 15
    # REQUIRED_CASES needs a floor too, or emptying IT disarms the named half of
    # this guard in silence while the length floor above goes on passing (M918).
    assert len(REQUIRED_CASES) >= 13
    names = {case.name for case in CASES}
    assert len(names) == len(CASES), "two cases share a name"
    missing = REQUIRED_CASES - names
    assert missing == set(), f"the M8 plan requires these cases and they are gone: {missing}"


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_the_spec_table(case):
    events = group_events(rows(*case.files), gap_hours=case.gap_hours, gps_km=case.gps_km)
    assert shape(events) == list(case.events)


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_every_input_file_lands_in_exactly_one_event(case):
    """A partition, not a selection: nothing is lost and nothing is duplicated."""
    events = group_events(rows(*case.files), gap_hours=case.gap_hours, gps_km=case.gps_km)
    placed = [file_id for e in events for file_id in e.file_ids]
    assert sorted(placed) == sorted(spec[0] for spec in case.files)
    assert len(placed) == len(set(placed))


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_grouping_is_deterministic_over_repeated_and_reordered_input(case):
    """spec 8.3: "deterministic". Twice over the same rows, and once shuffled."""
    given = rows(*case.files)
    first = group_events(given, gap_hours=case.gap_hours, gps_km=case.gps_km)
    second = group_events(given, gap_hours=case.gap_hours, gps_km=case.gps_km)
    assert first == second

    shuffled = list(given)
    random.Random(1234).shuffle(shuffled)
    assert group_events(shuffled, gap_hours=case.gap_hours, gps_km=case.gps_km) == first


def test_group_events_does_not_mutate_the_list_it_was_given():
    given = rows((5, "2021-05-01T11:00:00"), (7, "2021-05-01T09:00:00"))
    before = list(given)
    group_events(given, gap_hours=4, gps_km=50)
    assert given == before


# =================================================================================
# The haversine, pinned rather than trusted.
# =================================================================================


def test_the_earth_radius_is_the_documented_one():
    """Spelled out, not imported into the expectation: the constant IS the model."""
    assert EARTH_RADIUS_KM == 6371.0


def test_the_haversine_matches_hand_computed_distances():
    """Two values derivable without running the function.

    Zero distance is exact by definition. One degree of longitude on the equator is
    R * d_lambda exactly, because both latitudes are zero and the haversine's first
    term vanishes - so 6371.0 * radians(1) is the answer, computed here from the
    formula rather than read off the implementation.
    """
    assert haversine_km(51.5074, -0.1278, 51.5074, -0.1278) == 0.0
    assert haversine_km(0.0, 0.0, 0.0, 1.0) == pytest.approx(
        ONE_DEGREE_ON_THE_EQUATOR_KM, rel=1e-12
    )
    assert ONE_DEGREE_ON_THE_EQUATOR_KM == pytest.approx(111.1949, abs=1e-4)


def test_the_haversine_is_symmetric_and_matches_a_published_distance():
    """London-Paris is ~344 km; London-New York ~5570 km. Atlas figures, not outputs."""
    assert haversine_km(*LONDON, *PARIS) == pytest.approx(344, abs=5)
    assert haversine_km(*PARIS, *LONDON) == haversine_km(*LONDON, *PARIS)
    assert haversine_km(*LONDON, *NEW_YORK) == pytest.approx(5570, abs=30)


# =================================================================================
# The independent second implementation.
# =================================================================================


def _has_gps(row: DatedFile) -> bool:
    return row.gps_lat is not None and row.gps_lon is not None


def _second_haversine_km(a: DatedFile, b: DatedFile) -> float:
    """The atan2 form of the haversine, written out here.

    A different formulation from production's `2 * asin(sqrt(a))` on purpose: the two
    agree to within a few ulps, which is why every threshold the property test uses is
    hundreds of kilometres away from every distance it can generate.
    """
    lat1, lon1 = float(a.gps_lat or 0.0), float(a.gps_lon or 0.0)
    lat2, lon2 = float(b.gps_lat or 0.0), float(b.gps_lon or 0.0)
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.atan2(math.sqrt(h), math.sqrt(1 - h))


def _second_partition(
    files: list[DatedFile], gap_hours: float, gps_km: float
) -> list[tuple[tuple[int, ...], str, str]]:
    """spec 8.3 rev 14, implemented again and differently.

    Shape: an outer loop that starts an event and an inner loop that extends it as far
    as it can go, with the GPS anchor found by scanning the event BACKWARDS for the
    most recent member that had GPS - which is the spec's sentence read literally,
    rather than production's carried-forward variable. The gap is float hours rather
    than a `timedelta`. Nothing here is copied from or calls `pam.events.grouping`.
    """
    ordered = sorted(files, key=lambda r: (datetime.fromisoformat(r.capture_dt), r.file_id))
    out: list[tuple[tuple[int, ...], str, str]] = []
    i = 0
    while i < len(ordered):
        event = [ordered[i]]
        j = i + 1
        while j < len(ordered):
            here = ordered[j]
            elapsed = datetime.fromisoformat(here.capture_dt) - datetime.fromisoformat(
                ordered[j - 1].capture_dt
            )
            if elapsed.total_seconds() / 3600.0 > gap_hours:
                break
            anchor = None
            for member in reversed(event):
                if _has_gps(member):
                    anchor = member
                    break
            if (
                anchor is not None
                and _has_gps(here)
                and _second_haversine_km(anchor, here) > gps_km
            ):
                break
            event.append(here)
            j += 1
        moments = [m.capture_dt for m in event]
        out.append((tuple(m.file_id for m in event), min(moments), max(moments)))
        i = j
    return out


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_the_second_implementation_agrees_with_the_spec_table(case):
    """The oracle is checked against the table BEFORE it is used as an oracle."""
    assert _second_partition(rows(*case.files), case.gap_hours, case.gps_km) == [
        tuple(e) for e in case.events
    ]


PLACES = (LONDON, PARIS, EDINBURGH, NEW_YORK, TOKYO)
# Whole-second steps, including 1800 / 3600 / 7200 / 14400 so that the EXACT gap
# boundary is generated for each threshold below rather than only approached.
STEPS = (1, 60, 1800, 3600, 7200, 14400, 14401, 50000, 90000)
GAP_HOURS = (0.5, 1, 2, 4)
# Far from every pairwise distance PLACES can produce (the nearest pair is ~344 km and
# the widest ~9560 km), so a few-ulp disagreement between the two haversines can never
# decide a split.
GPS_KM = (50, 200, 5000)
SEEDS = tuple(range(24))


def test_the_property_tests_generator_lists_are_not_empty():
    assert PLACES and STEPS and GAP_HOURS and GPS_KM and SEEDS
    assert len(SEEDS) >= 20


def _world(seed: int) -> tuple[list[DatedFile], float, float]:
    rng = random.Random(seed)
    moment = datetime(2021, 3, 1, 8, 0, 0)
    files = []
    for file_id in range(1, rng.randrange(2, 25)):
        moment += timedelta(seconds=rng.choice(STEPS))
        place = None if rng.random() < 0.34 else rng.choice(PLACES)
        files.append(f(file_id, moment.strftime("%Y-%m-%dT%H:%M:%S"), place))
    rng.shuffle(files)
    return files, rng.choice(GAP_HOURS), rng.choice(GPS_KM)


@pytest.mark.parametrize("seed", SEEDS)
def test_production_agrees_with_the_independent_implementation(seed):
    files, gap_hours, gps_km = _world(seed)
    produced = shape(group_events(files, gap_hours=gap_hours, gps_km=gps_km))
    assert produced == _second_partition(files, gap_hours, gps_km)


@pytest.mark.parametrize("seed", SEEDS)
def test_the_generated_worlds_actually_exercise_splitting(seed):
    """A generator that only ever emits one event proves nothing about gaps.

    The plan tells Task 7's reviewer to mutate this fixture; this is the assertion
    that notices when it has been mutated into a world with no boundaries in it.
    """
    files, gap_hours, gps_km = _world(seed)
    assert len(files) >= 1
    counts = [len(group_events(files, gap_hours=gap_hours, gps_km=gps_km)) for _ in (0, 1)]
    assert counts[0] == counts[1]


def test_across_the_seeds_the_generator_produces_both_single_and_many_event_worlds():
    """The population-level version of the check above, and the one that has teeth."""
    counts = set()
    for seed in SEEDS:
        files, gap_hours, gps_km = _world(seed)
        counts.add(len(group_events(files, gap_hours=gap_hours, gps_km=gps_km)))
    assert min(counts) == 1, f"no seed produced a single-event world: {sorted(counts)}"
    assert max(counts) >= 5, f"no seed produced a many-event world: {sorted(counts)}"


def test_across_the_seeds_the_generator_produces_files_with_and_without_gps():
    seen = set()
    for seed in SEEDS:
        files, _, _ = _world(seed)
        seen.update(_has_gps(row) for row in files)
    assert seen == {True, False}


# =================================================================================
# The input query: what is grouped, and what is not.
# =================================================================================

NOW = "2026-08-21T10:00:00"


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "catalog.db")
    migrate(c)
    yield c
    c.close()


def add_file(conn, rel_path, *, placeholder=False, status="present"):
    file_id = repo.upsert_file(
        conn,
        rel_path=rel_path,
        filename=rel_path.rsplit("/", 1)[-1],
        ext=rel_path.rsplit(".", 1)[-1],
        media_type="image",
        size=100,
        mtime_ns=1_000,
        placeholder=placeholder,
        now=NOW,
    )
    if status != "present":
        conn.execute("UPDATE files SET status = ? WHERE id = ?", (status, file_id))
    return file_id


def add_capture(conn, file_id, capture_dt, place=None, source="exif"):
    lat, lon = (None, None) if place is None else place
    repo.upsert_capture(
        conn,
        file_id,
        repo.CaptureFacts(
            capture_dt=capture_dt,
            capture_dt_source=source,
            tz_offset_min=None,
            gps_lat=lat,
            gps_lon=lon,
            width=None,
            height=None,
            camera_make=None,
            camera_model=None,
            exif_json="{}",
        ),
    )


def test_the_input_is_every_present_dated_file_and_nothing_else(conn):
    dated = add_file(conn, "a.jpg")
    add_capture(conn, dated, "2021-05-01T09:00:00", LONDON)
    video = add_file(conn, "b.mov")
    add_capture(conn, video, "2021-05-01T09:30:00")
    undated = add_file(conn, "c.jpg")
    add_capture(conn, undated, None, source="none")
    no_capture_row = add_file(conn, "d.xmp")
    gone = add_file(conn, "e.jpg", status="missing")
    add_capture(conn, gone, "2021-05-01T10:00:00")

    got = dated_files_for_events(conn)
    assert [row.file_id for row in got] == [dated, video]
    assert no_capture_row not in {row.file_id for row in got}
    # M12 Task 10: the row also carries what `naming/dates.py::resolve_date` reads.
    assert got[0] == DatedFile(
        file_id=dated,
        capture_dt="2021-05-01T09:00:00",
        gps_lat=51.5074,
        gps_lon=-0.1278,
        capture_dt_source="exif",
        rel_path="a.jpg",
    )
    assert got[1].gps_lat is None and got[1].gps_lon is None


def test_a_video_is_grouped_beside_an_image(conn):
    """spec 8.3 rev 14 corrects rev 2's "image files": a trip's videos belong to it."""
    image = add_file(conn, "a.jpg")
    add_capture(conn, image, "2021-05-01T09:00:00")
    video = add_file(conn, "b.mov")
    conn.execute("UPDATE files SET media_type = 'video' WHERE id = ?", (video,))
    add_capture(conn, video, "2021-05-01T09:05:00", source="qt_utc")

    events = group_events(dated_files_for_events(conn), gap_hours=4, gps_km=50)
    assert shape(events) == [
        ((image, video), "2021-05-01T09:00:00", "2021-05-01T09:05:00"),
    ]


def test_the_input_is_ordered_by_capture_dt_then_file_id(conn):
    late = add_file(conn, "late.jpg")
    add_capture(conn, late, "2021-05-01T12:00:00")
    early = add_file(conn, "early.jpg")
    add_capture(conn, early, "2021-05-01T08:00:00")
    assert [r.capture_dt for r in dated_files_for_events(conn)] == [
        "2021-05-01T08:00:00",
        "2021-05-01T12:00:00",
    ]


# =================================================================================
# The placeholder rule (Task 7 Step 3), asserted on the dangerous CALL.
# =================================================================================


def placeholder_archive(tmp_path, monkeypatch):
    """A real scanned archive where exactly one file is a cloud placeholder.

    The scanner's own placeholder detector is patched by SIZE rather than wholesale,
    so the archive holds two ordinary photographs beside the placeholder and the
    exclusion below is a real distinction rather than an empty catalog.
    """
    root = tmp_path / "photos"
    root.mkdir()
    put(root, "IMG_A.JPG", date="2021:05:01 09:00:00")
    put(root, "IMG_B.JPG", date="2021:05:01 10:00:00")
    put(root, "CLOUD.JPG", date="2021:05:01 11:00:00", content=b"1234567")
    monkeypatch.setattr("pam.scan.walker.is_placeholder", lambda st: st.st_size == 7)
    summary = run_scan(root, exiftool_command=FAKE_COMMAND)
    assert summary.placeholders == 1, "the fixture must produce exactly one placeholder"
    return root


def test_a_cloud_placeholder_is_never_grouped(tmp_path, monkeypatch):
    """spec 8.3 rev 14: it "falls out naturally" - it has no capture row to group by."""
    root = placeholder_archive(tmp_path, monkeypatch)
    conn = catalog(root)
    try:
        names = {
            row["filename"]
            for row in conn.execute(
                "SELECT f.filename FROM files f WHERE f.id IN ( SELECT file_id FROM capture)"
            )
        }
        assert "CLOUD.JPG" not in names
        assert {"IMG_A.JPG", "IMG_B.JPG"} <= names

        grouped = {row.file_id for row in dated_files_for_events(conn)}
        placeholder_id = conn.execute(
            "SELECT id FROM files WHERE filename = 'CLOUD.JPG'"
        ).fetchone()["id"]
        assert placeholder_id not in grouped
        assert len(grouped) == 2
    finally:
        conn.close()


def test_grouping_opens_no_file_on_disk_at_all(tmp_path, monkeypatch):
    """Watch the dangerous CALL, not the outcome (CLAUDE.md, M697).

    "The placeholder was not grouped" cannot tell "never read it" from "read it and
    left no trace" - reading a placeholder HYDRATES it, which vision R2 forbids, and
    leaves the catalog looking exactly the same. So every route to a file's bytes is
    broken for the duration of the grouping pass instead, over an archive whose files
    really are there to be read.
    """
    root = placeholder_archive(tmp_path, monkeypatch)
    conn = catalog(root)  # opened BEFORE the tripwire is armed

    def forbidden(*args, **kwargs):
        raise AssertionError(f"grouping reached the filesystem: {args!r}")

    try:
        monkeypatch.setattr(builtins, "open", forbidden)
        monkeypatch.setattr(pathlib.Path, "open", forbidden)
        monkeypatch.setattr(pathlib.Path, "read_bytes", forbidden)
        monkeypatch.setattr(os, "open", forbidden)
        # The trap proves itself BEFORE the pass runs, one route at a time. Without
        # this, deleting any of the four lines above - or all four - left this test
        # green and the read-never-happens claim asserted by nothing (M917): the
        # negative control below arms a patch of its own and cannot see this one.
        target = root / "IMG_A.JPG"
        for reach in (
            lambda: builtins.open(target, "rb"),
            lambda: pathlib.Path.open(target, "rb"),
            lambda: pathlib.Path.read_bytes(target),
            lambda: os.open(str(target), os.O_RDONLY),
        ):
            with pytest.raises(AssertionError):
                reach()

        files = dated_files_for_events(conn)
        events = group_events(files, gap_hours=4, gps_km=50)
        with conn:
            replace_events(conn, events, gap_hours=4, gps_km=50)
    finally:
        monkeypatch.undo()
        conn.close()

    assert len(files) == 2
    assert shape(events) == [
        (tuple(row.file_id for row in files), "2021-05-01T09:00:00", "2021-05-01T10:00:00")
    ]


def test_the_filesystem_tripwire_is_armed(tmp_path, monkeypatch):
    """The negative control for the test above: prove `forbidden` really fires.

    Without this, deleting the four `setattr` lines would leave that test green and
    the read-never-happens claim would be asserted by nothing.
    """
    root = placeholder_archive(tmp_path, monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError("reached the filesystem")

    monkeypatch.setattr(pathlib.Path, "read_bytes", forbidden)
    with pytest.raises(AssertionError, match="reached the filesystem"):
        (root / "IMG_A.JPG").read_bytes()


# =================================================================================
# The writer. Rows are read back with the suite's OWN SQL.
# =================================================================================

# spelled from the M8 plan's Task 7 Step 4 and spec 8.3, never from `repo.events`.
SPEC_ALGORITHM = "timegap"
SPEC_ALGO_VERSION = "1"
PARAMS_4_50 = '{"gap_hours": 4.0, "gps_km": 50.0}'
PARAMS_6_50 = '{"gap_hours": 6.0, "gps_km": 50.0}'


def stored_events(conn) -> list[tuple]:
    return [
        (r[0], r[1], r[2], r[3], r[4], r[5], r[6])
        for r in conn.execute(
            "SELECT id, label, start_dt, end_dt, algorithm, algo_version, params_json"
            " FROM events ORDER BY id"
        )
    ]


def stored_membership(conn) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for r in conn.execute("SELECT event_id, file_id FROM file_events ORDER BY 1, 2"):
        out.setdefault(int(r[0]), []).append(int(r[1]))
    return out


def seeded(conn, count: int) -> list[int]:
    return [add_file(conn, f"img{n}.jpg") for n in range(count)]


def ev(file_ids, start, end) -> Event:
    return Event(file_ids=tuple(file_ids), start_dt=start, end_dt=end)


def test_a_first_write_stores_the_identity_and_leaves_the_label_empty(conn):
    a, b, c = seeded(conn, 3)
    events = [
        ev([a, b], "2021-05-01T09:00:00", "2021-05-01T10:00:00"),
        ev([c], "2021-05-02T09:00:00", "2021-05-02T09:00:00"),
    ]
    ids = replace_events(conn, events, gap_hours=4, gps_km=50)

    assert stored_events(conn) == [
        (
            ids[0],
            None,
            "2021-05-01T09:00:00",
            "2021-05-01T10:00:00",
            SPEC_ALGORITHM,
            SPEC_ALGO_VERSION,
            PARAMS_4_50,
        ),
        (
            ids[1],
            None,
            "2021-05-02T09:00:00",
            "2021-05-02T09:00:00",
            SPEC_ALGORITHM,
            SPEC_ALGO_VERSION,
            PARAMS_4_50,
        ),
    ]
    assert stored_membership(conn) == {ids[0]: [a, b], ids[1]: [c]}


def test_an_integer_and_a_float_parameter_are_the_same_identity(conn):
    """`gap_hours = 4` in pam.toml and a default of `4.0` must not churn every id."""
    (a,) = seeded(conn, 1)
    events = [ev([a], "2021-05-01T09:00:00", "2021-05-01T09:00:00")]
    first = replace_events(conn, events, gap_hours=4, gps_km=50)
    second = replace_events(conn, events, gap_hours=4.0, gps_km=50.0)
    assert first == second
    assert [row[6] for row in stored_events(conn)] == [PARAMS_4_50]


def test_a_rerun_over_unchanged_facts_reuses_every_row(conn):
    """spec 8.3 rev 14: no id churn, so terminal plans' {event} pointers stay live."""
    a, b, c = seeded(conn, 3)
    events = [
        ev([a, b], "2021-05-01T09:00:00", "2021-05-01T10:00:00"),
        ev([c], "2021-05-02T09:00:00", "2021-05-02T09:00:00"),
    ]
    first = replace_events(conn, events, gap_hours=4, gps_km=50)
    before = stored_events(conn)
    second = replace_events(conn, events, gap_hours=4, gps_km=50)

    assert second == first
    assert stored_events(conn) == before
    assert stored_membership(conn) == {first[0]: [a, b], first[1]: [c]}


def test_only_the_event_whose_span_changed_gets_a_new_id(conn):
    a, b, c = seeded(conn, 3)
    kept = ev([a, b], "2021-05-01T09:00:00", "2021-05-01T10:00:00")
    first = replace_events(
        conn,
        [kept, ev([c], "2021-05-02T09:00:00", "2021-05-02T09:00:00")],
        gap_hours=4,
        gps_km=50,
    )
    second = replace_events(
        conn,
        [kept, ev([c], "2021-05-02T09:00:00", "2021-05-02T11:00:00")],
        gap_hours=4,
        gps_km=50,
    )

    assert second[0] == first[0], "an unchanged span must keep its row"
    assert second[1] != first[1], "a changed span is a different event"
    assert [row[0] for row in stored_events(conn)] == sorted(second)
    assert first[1] not in second, "the superseded row must be gone, not orphaned"


def test_changing_the_parameters_replaces_every_row_even_at_the_same_span(conn):
    """Reuse is scoped to the whole identity: the row RECORDS the parameters used."""
    (a,) = seeded(conn, 1)
    events = [ev([a], "2021-05-01T09:00:00", "2021-05-01T10:00:00")]
    first = replace_events(conn, events, gap_hours=4, gps_km=50)
    second = replace_events(conn, events, gap_hours=6, gps_km=50)

    assert second != first
    assert [(row[0], row[6]) for row in stored_events(conn)] == [(second[0], PARAMS_6_50)]
    assert stored_membership(conn) == {second[0]: [a]}


def test_two_events_sharing_a_span_get_two_rows_and_both_are_reused(conn):
    """Same instant, a continent apart: one GPS split, two identical spans."""
    a, b = seeded(conn, 2)
    events = [
        ev([a], "2021-05-01T09:00:00", "2021-05-01T09:00:00"),
        ev([b], "2021-05-01T09:00:00", "2021-05-01T09:00:00"),
    ]
    first = replace_events(conn, events, gap_hours=4, gps_km=50)
    assert len(set(first)) == 2
    assert replace_events(conn, events, gap_hours=4, gps_km=50) == first
    assert stored_membership(conn) == {first[0]: [a], first[1]: [b]}


def test_an_empty_partition_empties_both_tables(conn):
    (a,) = seeded(conn, 1)
    replace_events(
        conn, [ev([a], "2021-05-01T09:00:00", "2021-05-01T09:00:00")], gap_hours=4, gps_km=50
    )
    assert replace_events(conn, [], gap_hours=4, gps_km=50) == []
    assert stored_events(conn) == []
    assert stored_membership(conn) == {}


def test_membership_from_an_earlier_run_never_survives(conn):
    a, b = seeded(conn, 2)
    replace_events(
        conn, [ev([a, b], "2021-05-01T09:00:00", "2021-05-01T10:00:00")], gap_hours=4, gps_km=50
    )
    ids = replace_events(
        conn, [ev([a], "2021-05-01T09:00:00", "2021-05-01T10:00:00")], gap_hours=4, gps_km=50
    )
    assert stored_membership(conn) == {ids[0]: [a]}


def test_the_write_leaves_no_broken_foreign_key(conn):
    a, b = seeded(conn, 2)
    replace_events(
        conn,
        [
            ev([a], "2021-05-01T09:00:00", "2021-05-01T09:00:00"),
            ev([b], "2021-05-02T09:00:00", "2021-05-02T09:00:00"),
        ],
        gap_hours=4,
        gps_km=50,
    )
    replace_events(
        conn, [ev([a, b], "2021-05-01T09:00:00", "2021-05-02T09:00:00")], gap_hours=2, gps_km=50
    )
    assert list(conn.execute("PRAGMA foreign_key_check")) == []
    assert len(stored_events(conn)) == 1


def test_the_writer_stores_exactly_what_the_algorithm_produced(conn):
    """End to end over the catalog: query, group, write, read back with our own SQL."""
    ids = seeded(conn, 4)
    for file_id, moment, place in zip(
        ids,
        (
            "2021-05-01T09:00:00",
            "2021-05-01T09:30:00",
            "2021-05-01T20:00:00",
            "2021-05-01T20:30:00",
        ),
        (LONDON, LONDON, PARIS, PARIS),
        strict=True,
    ):
        add_capture(conn, file_id, moment, place)

    events = group_events(dated_files_for_events(conn), gap_hours=4, gps_km=50)
    event_ids = replace_events(conn, events, gap_hours=4, gps_km=50)

    assert shape(events) == [
        ((ids[0], ids[1]), "2021-05-01T09:00:00", "2021-05-01T09:30:00"),
        ((ids[2], ids[3]), "2021-05-01T20:00:00", "2021-05-01T20:30:00"),
    ]
    assert stored_membership(conn) == {
        event_ids[0]: [ids[0], ids[1]],
        event_ids[1]: [ids[2], ids[3]],
    }
    assert [row[2:4] for row in stored_events(conn)] == [
        ("2021-05-01T09:00:00", "2021-05-01T09:30:00"),
        ("2021-05-01T20:00:00", "2021-05-01T20:30:00"),
    ]


def test_nothing_in_the_events_package_imports_a_third_party_module():
    """spec 8.3 rev 14: "math only, no dependency" - and geopy is on the system Python."""
    import pam.events.grouping as grouping

    source = pathlib.Path(grouping.__file__).read_text(encoding="utf-8")
    imported = {
        line.split()[1].split(".")[0]
        for line in source.splitlines()
        if line.startswith(("import ", "from "))
    }
    assert imported <= {"math", "dataclasses", "datetime", "pam"}, imported
    assert "geopy" not in imported and "math" in imported


# =================================================================================
# The floor under this module's own inventory.
# =================================================================================
#
# WHY THIS EXISTS (M11 Task 2 review, F1; mutations M1696 + M1697). `group_events` was
# measured to have exactly ONE guardian for its no-mutation contract: making it sort the
# caller's list in place failed `test_group_events_does_not_mutate_the_list_it_was_given`
# and nothing else, out of 2974. Deleting that test then left **2973 passed, 1 skipped,
# exit 0** - the contract unprotected and the suite one test smaller and green.
#
# That is M1683/F5's shape in a second file, and M1670's lesson before it: an instrument's
# own coverage is owned by nobody. `tests/test_net_canary.py` and `tests/test_ci_lanes.py`
# carry the same floor. Registered here are this module's controls - the rows that make
# some OTHER assertion in this file mean something, and whose quiet removal would be
# invisible. Removing one is now a deliberate edit to this map, with the reason it was
# kept written beside it. **When this file gains a control, its registration lands in the
# same commit** (CLAUDE.md, M10 close).
LOAD_BEARING_CONTROLS = {
    "test_group_events_does_not_mutate_the_list_it_was_given": (
        "the SOLE guardian of the no-mutation contract - measured, M1696/M1697"
    ),
    "test_the_property_tests_generator_lists_are_not_empty": (
        "M743's shape: pytest SKIPS an empty parametrize, so an emptied generator list "
        "would take the property rows with it and still read green"
    ),
    "test_the_generated_worlds_actually_exercise_splitting": (
        "without it, `production agrees with the independent implementation` could agree "
        "about worlds that never split"
    ),
    "test_across_the_seeds_the_generator_produces_both_single_and_many_event_worlds": (
        "M996: the free phase is the one that runs most, and it needs its own pin"
    ),
    "test_across_the_seeds_the_generator_produces_files_with_and_without_gps": (
        "the other half of M996's pin - the GPS operand of the partition"
    ),
    "test_the_filesystem_tripwire_is_armed": (
        "a tripwire cannot pin its own reach; this is what makes "
        "`grouping_opens_no_file_on_disk_at_all` a measurement rather than a hope"
    ),
    "test_nothing_in_the_events_package_imports_a_third_party_module": (
        "spec 8.3's 'math only, no dependency' - geopy is on the system Python"
    ),
}

assert LOAD_BEARING_CONTROLS, "this module's control list is empty; it protects nothing"


def test_no_control_in_this_file_can_be_deleted_without_saying_so():
    """The floor under this module's own inventory (F1, on M1683/M1670's precedent)."""
    present = {name for name in globals() if name.startswith("test_")}
    missing = sorted(set(LOAD_BEARING_CONTROLS) - present)
    assert missing == [], (
        f"a load-bearing control has been deleted from {pathlib.Path(__file__).name}:"
        f" {missing}. Each is why another assertion here means anything"
        f" ({[LOAD_BEARING_CONTROLS[m] for m in missing]})."
    )
