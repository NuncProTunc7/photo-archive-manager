"""Spec 8.2 rev 14's EVENT nav level, over real loopback HTTP against a real plan.

WHY A SECOND WEBUI ARCHIVE EXISTS AT ALL. `tests/test_webui_api.py`'s archive is a
day-per-event archive: every one of its events starts and ends inside one day, so it can
say nothing about the two cases this level is actually hard for - an event that spans a
MONTH boundary, and an event whose span is more than one day. Task 7 built `an event
spans a month boundary` as a grouping fixture for exactly this, and this module is the
nav's half of it, built from real files through a real `pam scan` and a real `pam plan`.

THE ARITHMETIC THIS MODULE EXISTS TO PIN, in one sentence: a nav level in a Year/Month/
Event hierarchy is a (month, event) PAIR, so an event that spans two months appears
under both, and its count in each is its members in THAT month - never its whole
membership. `SPAN` below is five files across midnight on 31 May, three of them in May
and two in June, and 3 + 2 = 5 is the whole of the point.

WHAT IS SPELLED FROM THE SPEC AND WHAT IS LOOKED UP. Every month, label, span and count
below is written out from UX spec 5 rev 8 and from the fixture's own dates - never read
back from `repo.nav_tree`, which is the thing under test. The one value that cannot be
written out is the event's ID, which SQLite assigns; it is looked up through
`event_ids_by_day`, the suite's own SQL over the events table, on the same reasoning
`tests/test_planner.py::stored_events` records.
"""

import json
import logging
import os
import threading
import webbrowser
from http.client import HTTPConnection
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlencode

import pytest
from archives import FACES_FLOOR, FAKE_COMMAND
from test_webui_api import event_ids_by_day

from pam.catalog import repo
from pam.catalog.db import connect
from pam.catalog.repo.review import EventGroup
from pam.errors import PamError
from pam.naming.planner import run_plan
from pam.naming.sanitize import sanitize_token
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan
from pam.webui import run_review
from pam.webui.nav import MONTH_ABBREVIATIONS, MONTH_NAMES, SPAN_DASH, event_label
from pam.webui.server import ReviewServer

# --- the archive, spelled out as data -------------------------------------------------
#
# The default `[events]` parameters apply (gap_hours = 4.0, gps_km = 50.0) and every gap
# below is well inside four hours, so the three chains are three events under the shipped
# defaults rather than under a configuration this module invented. No file carries GPS, so
# spec 8.3's distance split cannot fire and the partition is the gap rule alone.

# ONE event, FIVE files, TWO months: three date tokens fall on 2021-05-31 and two on
# 2021-06-01. This is the whole subject of the module.
SPAN = {
    "MAY_1.JPG": "2021:05:31 18:00:00",
    "MAY_2.JPG": "2021:05:31 21:00:00",
    "MAY_3.JPG": "2021:05:31 23:30:00",
    "JUN_1.JPG": "2021:06:01 01:00:00",
    "JUN_2.JPG": "2021:06:01 03:00:00",
}
SPAN_START_DAY = "2021-05-31"
SPAN_IN_MAY = ("MAY_1.JPG", "MAY_2.JPG", "MAY_3.JPG")
SPAN_IN_JUNE = ("JUN_1.JPG", "JUN_2.JPG")

# ONE event, TWO files, TWO days, ONE month: the multi-day span, which is the branch that
# renders a dash at all. It also crosses midnight, so it is the month-boundary case with
# the boundary removed - the two differ in exactly one respect and that is the point.
NIGHT = {"NIGHT_1.JPG": "2021:07:12 23:00:00", "NIGHT_2.JPG": "2021:07:13 01:00:00"}
NIGHT_START_DAY = "2021-07-12"

# ONE event, ONE file, ONE day: the single-day span, which renders NO dash.
SOLO = {"SOLO.JPG": "2021:07:20 10:00:00"}
SOLO_START_DAY = "2021-07-20"

# ONE event of one file, plus a SIDECAR that is in no event. Spec 8.3 rev 14 groups every
# present, dated file, and a sidecar has no `capture` row - so `SIDE.xmp` is grouped by
# nothing while its CARD is dated 2021-07-25, because a companion takes its date token
# from its unit's primary. It is the item that makes July's month count larger than the
# sum of July's events.
SIDECAR = {"SIDE.JPG": "2021:07:25 10:00:00"}
SIDECAR_START_DAY = "2021-07-25"
UNEVENTED = "SIDE.xmp"

# What the whole archive is, in item counts, written out per month from the four groups
# above. `SIDE.xmp` is an item of the plan and a member of no event, which is why JULY
# does not equal the sum of its events.
MAY_ITEMS = len(SPAN_IN_MAY)
JUNE_ITEMS = len(SPAN_IN_JUNE)
JULY_ITEMS = len(NIGHT) + len(SOLO) + len(SIDECAR) + 1  # + the sidecar
TOTAL_ITEMS = MAY_ITEMS + JUNE_ITEMS + JULY_ITEMS


def _put(directory: Path, name: str, tags: dict[str, Any]) -> None:
    """One archive file plus the sidecar `tests/fake_exiftool.py` reads its tags from."""
    (directory / name).write_bytes(f"the only copy of {name}".encode())
    (directory / f"{name}.tags.json").write_text(
        json.dumps({**tags, "PamTestSidecar": name}), encoding="utf-8"
    )


class Live(NamedTuple):
    server: ReviewServer
    root: Path
    db_path: Path
    plan_id: int
    token: str


@pytest.fixture(autouse=True)
def no_real_browser(monkeypatch):
    """A suite that opens real browser tabs is a broken suite (M7 Task 7's finding)."""
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: True)


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """A real scanned archive, one real open plan, and a real server serving it.

    PAM_HOME is set and restored INSIDE the setup, never held across the yield:
    `conftest.py`'s `isolated_pam_home` is function-scoped and would fight it.
    """
    home = tmp_path_factory.mktemp("events-home")
    previous = os.environ.get("PAM_HOME")
    os.environ["PAM_HOME"] = str(home)
    try:
        root = tmp_path_factory.mktemp("events-archive") / "photos"
        root.mkdir(parents=True)
        for group in (SPAN, NIGHT, SOLO, SIDECAR):
            for name, taken in group.items():
                _put(root, name, {"DateTimeOriginal": taken})
        _put(root, UNEVENTED, {})
        run_scan(root, exiftool_command=FAKE_COMMAND)
        planned = run_plan(root)
        assert planned.plan_id is not None
        ws = workspace_if_exists(root)
        assert ws is not None
    finally:
        if previous is None:
            os.environ.pop("PAM_HOME", None)
        else:
            os.environ["PAM_HOME"] = previous

    logger = logging.getLogger("pam.tests.webui_events")
    logger.propagate = False
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    server = ReviewServer(workspace=ws, plan_id=planned.plan_id, logger=logger)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Live(
        server=server,
        root=root,
        db_path=ws.db_path,
        plan_id=planned.plan_id,
        token=server.token,
    )
    server.shutdown()
    server.server_close()
    thread.join(timeout=20)


def get(live: Live, path: str, **params: Any) -> tuple[int, Any]:
    """One GET to the live server, JSON decoded."""
    query = urlencode([(k, v) for k, v in params.items() if v is not None], doseq=True)
    url = f"/t/{live.token}/api{path}" + (f"?{query}" if query else "")
    conn = HTTPConnection(live.server.host, live.server.port, timeout=20)
    try:
        conn.request("GET", url)
        response = conn.getresponse()
        raw = response.read()
        return response.status, (json.loads(raw) if raw else None)
    finally:
        conn.close()


def old_names(live: Live, group: str) -> list[str]:
    status, body = get(live, f"/plan/{live.plan_id}/items", group=group, limit=200)
    assert status == 200, body
    return sorted(card["old_filename"] for card in body["items"])


def groups(live: Live) -> list[dict[str, Any]]:
    status, body = get(live, f"/plan/{live.plan_id}/groups")
    assert status == 200, body
    return list(body["groups"])


def only_year(live: Live) -> dict[str, Any]:
    """The fixture's single year, unwrapped - every date in it is in 2021."""
    tree = groups(live)
    assert [year["key"] for year in tree] == ["2021"], tree
    return tree[0]


def month(live: Live, key: str) -> dict[str, Any]:
    found = [entry for entry in only_year(live)["months"] if entry["key"] == key]
    assert len(found) == 1, f"{key} is not in the nav exactly once"
    return found[0]


# --- the tree, written out from the fixture's own dates -------------------------------


def test_the_nav_is_three_levels_and_the_month_spanning_event_is_in_both_months(live):
    """Spec 8.2 rev 14 and UX spec 5 rev 8, end to end, as ONE expectation.

    Read the three months against `SPAN` above: the same event id appears under 2021-05
    and under 2021-06 with counts 3 and 2, and 3 + 2 is its whole membership. An
    implementation that filed it under one month only, or that printed 5 under each, fails
    here on the number rather than on a missing key.

    July is the reconciliation case in the same expectation: three events summing to four
    items inside a month of five, because `SIDE.xmp` is in no event.
    """
    day = event_ids_by_day(live.db_path)
    assert only_year(live) == {
        "key": "2021",
        "label": "2021",
        "count": TOTAL_ITEMS,
        "months": [
            {
                "key": "2021-05",
                "label": "May",
                "count": MAY_ITEMS,
                "events": [
                    {
                        "key": f"2021-05.e{day[SPAN_START_DAY]}",
                        "label": "May 31",
                        "count": MAY_ITEMS,
                    }
                ],
            },
            {
                "key": "2021-06",
                "label": "June",
                "count": JUNE_ITEMS,
                "events": [
                    {
                        "key": f"2021-06.e{day[SPAN_START_DAY]}",
                        "label": "Jun 1",
                        "count": JUNE_ITEMS,
                    }
                ],
            },
            {
                "key": "2021-07",
                "label": "July",
                "count": JULY_ITEMS,
                "events": [
                    {
                        "key": f"2021-07.e{day[NIGHT_START_DAY]}",
                        "label": f"Jul 12{SPAN_DASH}13",
                        "count": len(NIGHT),
                    },
                    {
                        "key": f"2021-07.e{day[SOLO_START_DAY]}",
                        "label": "Jul 20",
                        "count": len(SOLO),
                    },
                    {
                        "key": f"2021-07.e{day[SIDECAR_START_DAY]}",
                        "label": "Jul 25",
                        "count": len(SIDECAR),
                    },
                ],
            },
        ],
    }


def test_the_spanning_events_two_entries_hold_two_DIFFERENT_sets_of_photographs(live):
    """The count is a promise about which CARDS the entry opens, so read the cards.

    A count is satisfiable by arithmetic that selects the wrong items - the two entries
    could each report 3 and 2 while both opened the whole event. These are the two halves
    of `SPAN`, they are disjoint, and together they are all of it.
    """
    day = event_ids_by_day(live.db_path)
    in_may = old_names(live, f"2021-05.e{day[SPAN_START_DAY]}")
    in_june = old_names(live, f"2021-06.e{day[SPAN_START_DAY]}")
    assert in_may == sorted(SPAN_IN_MAY)
    assert in_june == sorted(SPAN_IN_JUNE)
    assert set(in_may).isdisjoint(in_june)
    assert sorted(in_may + in_june) == sorted(SPAN)


def test_every_count_in_the_tree_reconciles_downward(live):
    """The three levels are not three independent tallies.

    A year's count IS the sum of its months'. A month's count is the sum of its events'
    counts in that month PLUS the items of that month that belong to no event - it is not
    an identity, and asserting one would have failed on July's sidecar. The residual is
    checked as a number in its own right rather than absorbed, so an event level that
    quietly dropped an entry would show up as a residual that grew.
    """
    year = only_year(live)
    assert year["count"] == sum(entry["count"] for entry in year["months"])
    residuals = {}
    for entry in year["months"]:
        evented = sum(event["count"] for event in entry["events"])
        assert evented <= entry["count"], entry
        residuals[entry["key"]] = entry["count"] - evented
    assert residuals == {"2021-05": 0, "2021-06": 0, "2021-07": 1}


def test_the_months_own_count_is_every_card_its_events_open_plus_the_unevented_one(live):
    """July's residual, as cards. The one item no event entry can reach is the sidecar,
    and it is reached through the MONTH, which is what makes the residual acceptable
    rather than a photograph the user cannot get to."""
    july = month(live, "2021-07")
    from_events = set()
    for event in july["events"]:
        from_events.update(old_names(live, event["key"]))
    assert set(old_names(live, "2021-07")) - from_events == {UNEVENTED}


def test_the_event_level_is_ordered_by_its_own_span_and_not_by_id(live):
    """UX spec 5's nav is chronological at every level. Read as the labels a human sees,
    because the ordering is a property of the printed list rather than of a sort key."""
    assert [event["label"] for event in month(live, "2021-07")["events"]] == [
        f"Jul 12{SPAN_DASH}13",
        "Jul 20",
        "Jul 25",
    ]


# --- the date span, spelled from UX spec 5 rev 8 --------------------------------------

# UX spec 5 rev 8's two worked examples, verbatim: "the level shows `Jul 4 (81)` /
# `Jul 12-14 (44)` until a label exists and the label afterwards". The counts are the
# spec's; only the label is this function's business. Written out here rather than
# derived, and the dash is the spec's EN DASH.
UX_5_SPANS = (
    ("2019-07-04", "2019-07-04", "Jul 4"),
    ("2019-07-12", "2019-07-14", "Jul 12–14"),
)


def test_the_ux_5_span_examples_are_not_empty_and_use_the_spec_dash():
    """This module's convention (and the suite's): a list-driven check asserts its list
    non-empty at collection, because pytest SKIPS an empty parametrize rather than
    failing it. The dash is asserted separately so that a list rewritten with a hyphen
    could not quietly redefine what the examples are examples OF."""
    assert len(UX_5_SPANS) == 2
    assert SPAN_DASH == "–", "UX spec 5 rev 8 writes the span with an EN DASH"
    assert any(SPAN_DASH in label for _, _, label in UX_5_SPANS)


@pytest.mark.parametrize(("start", "end", "expected"), UX_5_SPANS)
def test_an_unlabelled_event_renders_as_ux_5s_own_date_span(start, end, expected):
    """`Jul 12-14`, the spec's three-day example, which the live archive cannot build
    cheaply: chaining three days under the shipped four-hour gap takes seven files."""
    event = EventGroup(
        key=f"{start[:7]}.e1", event_id=1, label=None, start=start, end=end, count=1
    )
    assert event_label(event) == expected


@pytest.mark.parametrize("label", ["Lake Tahoe", "Emma's birthday"])
def test_a_LABELLED_event_renders_its_label_instead_of_its_span(label):
    """The M9 branch. Every event M8 writes is unlabelled, so this is the case a human
    will not see this milestone - it is here because "and the label once one exists" is a
    sentence in UX spec 5 rev 8, and a branch with no test is a branch M9 inherits
    untested."""
    event = EventGroup(
        key="2019-07.e1",
        event_id=1,
        label=label,
        start="2019-07-12",
        end="2019-07-14",
        count=4,
    )
    assert event_label(event) == label


def test_an_EMPTY_label_falls_back_to_the_span_rather_than_rendering_as_nothing():
    """ "No label" and "the empty label" must not be tellable apart by accident -
    `EventMembership`'s docstring settles that for naming, and a nav entry printed as
    ` (44)` is the same mistake wearing a different hat."""
    event = EventGroup(
        key="2019-07.e1", event_id=1, label="", start="2019-07-04", end="2019-07-04", count=1
    )
    assert event_label(event) == "Jul 4"


# The twelve month words UX spec 5's nav prints, and the twelve short forms rev 8's event
# level prints. Typed out from ENGLISH rather than derived from `MONTH_NAMES`, which is
# the whole point: an oracle built from the definition it checks agrees with any value of
# that definition (the ENRICHERS lesson, and M989's dash one section up).
ENGLISH_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def test_the_month_words_are_pams_own_and_not_the_c_libraries():
    """`MONTH_ABBREVIATIONS` is derived from `MONTH_NAMES`, and both are pam's, because
    `calendar.month_abbr` is locale-dependent: the same plan must not read `Jul` on one
    machine and `juil.` on another. Spelled out here from English rather than from the
    production tuple, which is the ENRICHERS lesson.

    BOTH tuples are read, and M991 is why. Only three of the twelve FULL names appear in
    any live-archive expectation (`May`, `June`, `July` - the fixture's own months), and
    the abbreviations are the first three letters, so replacing `September` with the
    FRENCH `Septembre` left the whole suite green while the nav printed a locale-
    dependent word - the exact failure `MONTH_NAMES`' docstring says the tuple exists to
    prevent. Nine of the twelve nav labels a user can see were pinned by nothing.
    """
    assert len(ENGLISH_MONTHS) == 12
    assert list(MONTH_NAMES) == list(ENGLISH_MONTHS)
    assert list(MONTH_ABBREVIATIONS) == [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]


# --- Step 3's premise: the live join is safe only because of this ---------------------


def test_a_superseded_plan_is_refused_which_is_what_makes_the_live_join_safe(
    tmp_path, monkeypatch
):
    """SPEC 8.2 REV 14'S NO-MIGRATION ARGUMENT, PINNED RATHER THAN ASSUMED.

    The item's event is read LIVE through `file_events` at request time, with no
    plan-time copy of it anywhere - unlike `unit_key`, which exists precisely because
    `pam scan` recomputes companion groups between plan and apply. The argument for the
    difference has two halves and this test is the second: nothing recomputes events
    except `pam plan`, which supersedes the plan it replaces (spec 6.3 rule 5); and `pam
    review` serves `open` plans only, so the one plan whose items could be navigated
    against a grouping that is no longer theirs is a plan pam refuses to serve.

    Both halves are exercised. The re-plan runs under a DIFFERENT `gap_hours`, so the
    second grouping is genuinely a different partition of the same files and not a reuse
    of the first - asserted as a count, from `pam plan`'s own summary, so that a
    configuration that silently failed to load could not make this test vacuous.
    """
    monkeypatch.setenv("PAM_HOME", str(tmp_path / "home"))
    root = tmp_path / "photos"
    root.mkdir(parents=True)
    for name, taken in SPAN.items():
        _put(root, name, {"DateTimeOriginal": taken})
    run_scan(root, exiftool_command=FAKE_COMMAND)

    first = run_plan(root)
    assert first.plan_id is not None
    assert first.events == 1, "the five files chain into one event under the defaults"

    ws = workspace_if_exists(root)
    assert ws is not None
    ws.config_path.write_text("[events]\ngap_hours = 1\n", encoding="utf-8")
    second = run_plan(root)
    assert second.plan_id is not None and second.plan_id != first.plan_id
    assert second.events > first.events, (
        "a shorter gap must split the chain, or this test proves nothing about a plan"
        " outliving the grouping it was navigated against"
    )

    with pytest.raises(PamError) as refused:
        run_review(first.plan_id, root, serve=lambda server: None)
    assert f"Plan {first.plan_id} is superseded" in str(refused.value)

    conn = connect(ws.db_path)
    try:
        statuses = dict(conn.execute("SELECT id, status FROM plans").fetchall())
    finally:
        conn.close()
    assert statuses[first.plan_id] == "superseded"
    assert statuses[second.plan_id] == "open"


# =====================================================================================
# M9 TASK 11 - THE LABELLED HALF: the nav renders a label, and search finds one
# =====================================================================================
#
# Everything above this line is the UNLABELLED archive, and it stays that way on
# purpose: spec 8.2's degradation table is about what the UI does when a thing is
# ABSENT, and the tiny library, every archive before `pam enrich` and every archive M8
# could build have no labels at all. A second archive is built here rather than seeding
# labels into `live`, whose spans are the subject of two dozen assertions above - a
# module-scoped fixture that two sections mutate is the order dependency M628a cost this
# project a security-relevant test to learn about.
#
# THE TEMPLATE IS `{date}_{seq}` AND THAT IS THE WHOLE DESIGN OF THIS FIXTURE. The
# SHIPPED default is `{date}_{event-or-scene}_{people}_{seq}`, which would put every
# label into every proposed filename - and `q` has matched proposed filenames since M7,
# so a search for `Tahoe` would pass here with no event operand at all. That is the
# vacuous oracle this project has met with a query, a constant and a partition. Dropping
# `{event-or-scene}` from the template is what makes a match PROOF that the label
# operand fired.

LABELLED_FILES = {
    # One event, two photographs, and a sidecar sharing SP_2's stem. `SP_2.xmp` has no
    # `capture` row, so spec 8.3 never groups it and its `event_id` is NULL while its
    # unit partner's is not. EVENT MEMBERSHIP IS NOT UNIT-UNIFORM, and this pair is how
    # that gets tested.
    "SP_1.JPG": "2021:03:10 10:00:00",
    "SP_2.JPG": "2021:03:10 12:00:00",
    # `LT.JPG`, not `TAHOE.JPG`: no word of any seeded label may appear in any filename
    # in this archive, or a search for it would be matched by the name clause M7 shipped
    # and would prove nothing about M9's. `test_no_label_word_reaches_a_filename` below
    # is the assertion, and it is why the abbreviation is here.
    "LT.JPG": "2021:05:20 09:00:00",
    # The two degraded rows. `PLAIN` keeps the NULL its `events` row is born with;
    # `BLANK` is seeded with the EMPTY STRING, which spec 8.3 and `EventMembership`
    # both require to be indistinguishable from NULL.
    "PLAIN.JPG": "2021:07:14 09:00:00",
    "BLANK.JPG": "2021:09:03 09:00:00",
}
LABELLED_SIDECAR = "SP_2.xmp"

# start day -> the label seeded on that event. Written out here and read nowhere else.
# The accent is the subject of one test below and is not decoration.
SAO_PAULO = "São Paulo"
LAKE_TAHOE = "Lake Tahoe"
SEEDED_LABELS = {"2021-03-10": SAO_PAULO, "2021-05-20": LAKE_TAHOE, "2021-09-03": ""}
UNSEEDED_DAY = "2021-07-14"

# Which old filenames belong to which event, worked out from the dates above and spec
# 8.3's four-hour gap by hand: every gap here is weeks, so the four chains are four
# events and the only within-event gap is SP_1 -> SP_2's two hours.
SAO_PAULO_PHOTOS = ["SP_1.JPG", "SP_2.JPG"]
SAO_PAULO_UNIT = ["SP_2.JPG", LABELLED_SIDECAR]
LABELLED_ITEMS = len(LABELLED_FILES) + 1  # + the sidecar


def _seed_label(db_path: Path, event_id: int, label: str) -> None:
    """One `events` row's label, written with the SUITE'S OWN SQL.

    `label_source = 'user'` is load-bearing rather than tidy, and `set_label` in
    `tests/test_event_naming.py` carries the same line for the same reason: `pam plan`
    geocodes every event's centroid and `repo.events._set_label` overwrites a label
    whose source is NULL or 'geocode'. These files carry no GPS, so a re-plan would
    geocode them to nothing and clear a source-less seed - correct for a stale geocode,
    wrong for a label a person chose. Seeding 'user' is what makes the seed survive the
    second `run_plan` this fixture needs.
    """
    conn = connect(db_path)
    try:
        conn.execute(
            "UPDATE events SET label = ?, label_source = 'user' WHERE id = ?",
            (label, event_id),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(scope="module")
def labelled(tmp_path_factory):
    """An archive of real files whose events carry labels: two seeded, one NULL, one empty.

    TWO PLANS, and the second is the one served. An `events` row only exists after a
    plan's step 0 has run, so the labels cannot be seeded before the first plan; the
    second run REUSES those rows (spec 8.3 rev 14 - identity and `(start_dt, end_dt)`
    unchanged) and supersedes the first, which is the plan `pam review` then refuses.
    That the reuse actually happened is asserted, not assumed: a run that inserted four
    fresh rows would drop every seeded label and leave this whole section testing the
    unlabelled case a second time.
    """
    home = tmp_path_factory.mktemp("labelled-home")
    previous = os.environ.get("PAM_HOME")
    os.environ["PAM_HOME"] = str(home)
    try:
        root = tmp_path_factory.mktemp("labelled-archive") / "photos"
        root.mkdir(parents=True)
        for name, taken in LABELLED_FILES.items():
            _put(root, name, {"DateTimeOriginal": taken})
        _put(root, LABELLED_SIDECAR, {})
        run_scan(root, exiftool_command=FAKE_COMMAND)
        first = run_plan(root, "{date}_{seq}")
        assert first.plan_id is not None
        ws = workspace_if_exists(root)
        assert ws is not None

        by_day = event_ids_by_day(ws.db_path)
        assert sorted(by_day) == sorted([*SEEDED_LABELS, UNSEEDED_DAY]), (
            f"the fixture must group into exactly four events: {sorted(by_day)}"
        )
        for day, label in SEEDED_LABELS.items():
            _seed_label(ws.db_path, by_day[day], label)

        planned = run_plan(root, "{date}_{seq}")
        assert planned.plan_id is not None and planned.plan_id != first.plan_id
        assert event_ids_by_day(ws.db_path) == by_day, (
            "the re-plan replaced the events rows instead of reusing them, so every"
            " seeded label was dropped and this fixture tests nothing it claims to"
        )
    finally:
        if previous is None:
            os.environ.pop("PAM_HOME", None)
        else:
            os.environ["PAM_HOME"] = previous

    logger = logging.getLogger("pam.tests.webui_labelled")
    logger.propagate = False
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    server = ReviewServer(workspace=ws, plan_id=planned.plan_id, logger=logger)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Live(
        server=server,
        root=root,
        db_path=ws.db_path,
        plan_id=planned.plan_id,
        token=server.token,
    )
    server.shutdown()
    server.server_close()
    thread.join(timeout=20)


def found(live: Live, needle: str) -> list[str]:
    """The old filenames one `?q=` returns, sorted. The whole plan fits in one page."""
    status, body = get(live, f"/plan/{live.plan_id}/items", q=needle, limit=200)
    assert status == 200, body
    return sorted(card["old_filename"] for card in body["items"])


def test_the_labelled_fixture_really_carries_the_three_label_states(labelled):
    """Read with the suite's own SQL before anything is asserted ABOUT the labels.

    Three states, not two: a label, no label, and the empty label. If the seeding
    silently failed, every expectation below would still be satisfiable by the date-span
    branch - the branch M8 already shipped - and this section would pass while proving
    nothing about M9 at all.
    """
    conn = connect(labelled.db_path)
    try:
        rows = {
            str(r["start_dt"])[:10]: r["label"]
            for r in conn.execute("SELECT start_dt, label FROM events")
        }
    finally:
        conn.close()
    assert rows == {
        "2021-03-10": SAO_PAULO,
        "2021-05-20": LAKE_TAHOE,
        UNSEEDED_DAY: None,
        "2021-09-03": "",
    }


def test_no_label_word_reaches_a_filename(labelled):
    """The fixture's own premise, asserted rather than trusted.

    `q` has matched the OLD and the PROPOSED filename since M7. If a label word reached
    either, every search assertion below would pass with the M9 operand deleted - and
    deleting it is the first mutation anyone will try. Both name clauses are checked,
    not just the proposed one: `TAHOE.JPG` was this fixture's first draft and it made
    four of the parametrized rows below pass through the M7 clause without the label
    ever being consulted.

    The proposed half is also what says the template really dropped `{event-or-scene}`,
    in terms of the data rather than in terms of the config.
    """
    status, body = get(labelled, f"/plan/{labelled.plan_id}/items", limit=200)
    assert status == 200, body
    names = [card["new_filename"] for card in body["items"]]
    names += [card["old_filename"] for card in body["items"]]
    assert len(names) == 2 * LABELLED_ITEMS
    for label in (SAO_PAULO, LAKE_TAHOE):
        for word in label.split():
            assert not any(word.casefold() in name.casefold() for name in names), (
                f"{word!r} is in a filename, so a search for it proves nothing"
            )


# --- Step 1: the nav renders the label, and degrades to the span where there is none ---


def test_the_nav_renders_the_label_where_there_is_one_and_the_span_where_there_is_not(
    labelled,
):
    """SPEC 8.2 / UX SPEC 5 REV 8, END TO END AND AS ONE EXPECTATION.

    M8 shipped the span half against four NULL labels; this is the first time a stored
    label travels `events.label` -> `plan_rows` -> `nav_tree` -> `event_label` -> JSON.
    All four states are in one assertion on purpose: the two labelled months and the two
    degraded ones are the same code path answering a different question, and a renderer
    that lost the fallback would pass a labelled-only test.

    March also re-pins the downward reconciliation with a label in the way: the month
    counts THREE items and its one event counts TWO, because `SP_2.xmp` is in the unit
    and in no event.
    """
    day = event_ids_by_day(labelled.db_path)
    assert only_year(labelled) == {
        "key": "2021",
        "label": "2021",
        "count": LABELLED_ITEMS,
        "months": [
            {
                "key": "2021-03",
                "label": "March",
                "count": len(SAO_PAULO_PHOTOS) + 1,
                "events": [
                    {
                        "key": f"2021-03.e{day['2021-03-10']}",
                        "label": SAO_PAULO,
                        "count": len(SAO_PAULO_PHOTOS),
                    }
                ],
            },
            {
                "key": "2021-05",
                "label": "May",
                "count": 1,
                "events": [
                    {
                        "key": f"2021-05.e{day['2021-05-20']}",
                        "label": LAKE_TAHOE,
                        "count": 1,
                    }
                ],
            },
            {
                "key": "2021-07",
                "label": "July",
                "count": 1,
                # NULL: spec 8.2's degradation table, still working with labels in the
                # same tree. `Jul 14` is UX spec 5 rev 8's single-day span form.
                "events": [
                    {
                        "key": f"2021-07.e{day[UNSEEDED_DAY]}",
                        "label": "Jul 14",
                        "count": 1,
                    }
                ],
            },
            {
                "key": "2021-09",
                "label": "September",
                "count": 1,
                # The EMPTY label falls to the span too, or the nav would read ` (1)`.
                "events": [
                    {
                        "key": f"2021-09.e{day['2021-09-03']}",
                        "label": "Sep 3",
                        "count": 1,
                    }
                ],
            },
        ],
    }


# --- Step 2: `q` joins event labels ---------------------------------------------------


@pytest.mark.parametrize(
    ("needle", "expected"),
    [
        # The label, whole and in part, in three casings. No word of it is in any
        # filename in this archive - `test_no_label_word_reaches_a_filename` is what
        # proves that, and without it these rows would pass through M7's name clause.
        (LAKE_TAHOE, ["LT.JPG"]),
        ("Tahoe", ["LT.JPG"]),
        ("TAHOE", ["LT.JPG"]),
        ("lake", ["LT.JPG"]),
        # THE MEMBERS THAT HAVE THE EVENT, AND NOT THEIR UNIT PARTNER. `SP_2.xmp` shares
        # a `unit_key` with `SP_2.JPG` and has no `capture` row, so it is in no event -
        # the same non-uniformity `group=YYYY-MM.eN` has, and the reason spec 8.2 rev 15
        # widens a bulk decision to whole units after resolving the filter.
        ("Paulo", SAO_PAULO_PHOTOS),
        (SAO_PAULO, SAO_PAULO_PHOTOS),
        # The names still match, unchanged since M7.
        ("sp_", ["SP_1.JPG", "SP_2.JPG", LABELLED_SIDECAR]),
        # In no name and in no label.
        ("qqq", []),
    ],
)
def test_the_search_matches_an_event_label(labelled, needle, expected):
    assert found(labelled, needle) == sorted(expected)


def test_an_absent_label_matches_exactly_what_its_NAMES_match_and_nothing_more(labelled):
    """THE DEGRADATION, STATED AS A PROPERTY RATHER THAN AS TWO EXAMPLES.

    `PLAIN.JPG` carries a NULL label and `BLANK.JPG` an empty one, and neither may be
    reachable by a needle its own names do not contain - or the M9 operand would be a
    filter that WIDENS as the data thins, and "no label" would match every search
    instead of none. The oracle is a second implementation of the two name clauses,
    written from spec 8.2's own sentence ("q searches old + proposed name") rather than
    from `_matches_q`, which is the function under test.

    Each item is required to be matched by at least one needle before its misses are
    counted, because a list of misses would prove only that misses miss - the shape
    M743's empty parametrize taught this project to distrust.
    """
    status, body = get(labelled, f"/plan/{labelled.plan_id}/items", limit=200)
    assert status == 200, body
    cards = {card["old_filename"]: card for card in body["items"]}
    needles = [
        LAKE_TAHOE,
        "Tahoe",
        "lake",
        SAO_PAULO,
        "Paulo",
        "plain",
        "BLANK",
        "2021-07",
        "qqq",
    ]
    assert len(needles) >= 5

    for name in ("PLAIN.JPG", "BLANK.JPG"):
        card = cards[name]
        hits = {
            needle
            for needle in needles
            if needle.casefold() in card["old_filename"].casefold()
            or needle.casefold() in card["new_filename"].casefold()
        }
        assert hits, f"no needle matches {name}'s names, so this proves only that misses miss"
        assert {needle for needle in needles if name in found(labelled, needle)} == hits


def test_the_accented_label_is_matched_BY_ITS_OWN_SPELLING_and_is_not_transliterated(
    labelled,
):
    """THE FOLDING DECISION, AND BOTH HALVES OF WHY IT IS SAFE.

    Spec 8.2 says `q` searches names case-insensitively and says nothing about
    transliteration; spec 6.2's NFKD + fold table (spec 5.3) is normative for FILENAMES,
    and applying a filename rule to a search box would be inventing a rule no spec
    carries. So `q` casefolds and does not fold: the accented spelling finds the label
    and the ASCII one does not.

    That costs the user nothing on an archive of real files, and the second half is what says so:
    the shipped default template carries `{event-or-scene}`, so the label reaches the
    proposed NAME - folded, by rule 3 - and the ASCII spelling finds it there. Both
    spellings reach the event, by two honest routes rather than one lossy one. The fold
    is asserted through `sanitize_token` so the claim is measured here and not hearsay;
    the route itself is `tests/test_event_naming.py`'s subject.
    """
    from pam.naming.sanitize import sanitize_token

    assert found(labelled, "São") == SAO_PAULO_PHOTOS
    assert found(labelled, "são paulo") == SAO_PAULO_PHOTOS
    assert found(labelled, "Sao") == [], (
        "the search folded an accent, which is spec 6.2's filename rule applied where"
        " no spec puts it"
    )
    assert sanitize_token(SAO_PAULO) == "Sao-Paulo", (
        "the ASCII route depends on the fold table turning this label into that stem"
    )


def test_a_bulk_decision_found_BY_LABEL_still_reaches_the_whole_companion_unit(labelled):
    """AMENDMENT A1 / VISION S3, ASKED OF THE FILTER DIMENSION M9 JUST ADDED.

    Spec 8.2 rev 15 names two filter dimensions that are not uniform across a companion
    unit - free-text `q`, and event membership - and `decide-filter` contains both the
    same way: it resolves the filter through `select_items`, exactly as the read
    endpoint does, and THEN widens the chosen ids to whole units. An event label is a
    new way to be non-uniform, not a new way to escape the widening, and this asserts
    that composition over the fixture's own unit rather than assuming it.

    The two steps are checked separately and in order. The first must EXCLUDE the
    sidecar - if it did not, the second would be trivially satisfied and the test would
    pass with the widening deleted.
    """
    from pam.catalog.repo.review import ItemFilter, plan_rows, select_items
    from pam.webui.write_state import _widen_to_units

    conn = connect(labelled.db_path)
    try:
        items = plan_rows(conn, labelled.plan_id, floor=FACES_FLOOR)
    finally:
        conn.close()
    by_id = {item.id: item for item in items}

    chosen = select_items(items, ItemFilter(q=SAO_PAULO))
    assert sorted(item.old_filename for item in chosen) == SAO_PAULO_PHOTOS, (
        "the filter must leave the sidecar out, or the widening below proves nothing"
    )

    widened = _widen_to_units(items, [item.id for item in chosen])
    assert sorted(by_id[item_id].old_filename for item_id in widened) == sorted(
        SAO_PAULO_PHOTOS + [LABELLED_SIDECAR]
    )
    assert len(widened) == len(set(widened)), "a unit member was named twice"


# --- M10 Task 10: `q` joins the PLACE facts (spec 8.2 rev 16 / UX 5 rev 10) -----------
#
# A THIRD ARCHIVE, for the reason the second one exists. `labelled`'s four events are the
# subject of two dozen assertions above and its files carry no GPS at all; seeding facts
# into it would make one module-scoped fixture answer two sections' questions, which is
# the order dependency M628a cost this project a security-relevant test to learn about.
#
# THE TEMPLATE IS `{date}_{seq}` FOR THE SAME REASON IT IS THERE. The shipped default
# carries `{event-or-scene}` and would put nothing of a `place` fact into a name today,
# but `{place}` is a token the user MAY template, and an archive whose names carried the
# place would let every row below pass through the name clause M7 shipped. Dropping every
# enricher token from the template is what makes a match PROOF that the place operand
# fired.
#
# NO FILE HERE CARRIES GPS, so every event's label is NULL and the M9 operand contributes
# nothing either. Three of the four operands are shut off by construction and
# `test_no_place_word_reaches_a_name_or_a_label` is the assertion that says so.

PLACED_FILES = {
    # Two photographs sharing one place NAME and not one fact row: `inferred_facts` is
    # content-addressed and these two files have different bytes, so `Kyoto` is written
    # twice. That is the ordinary state of an archive of real files and it is what a search must
    # cope with.
    "KY_1.JPG": "2022:03:10 10:00:00",
    "KY_2.JPG": "2022:03:10 12:00:00",
    # The accented row, and the subject of the folding assertion below. HT-M9 recorded
    # that no label on a real-world library is accented (H6), so this is the only
    # place the decision is exercised at all.
    "OS.JPG": "2022:05:20 09:00:00",
    # The two degraded rows: one file no enricher ever covered, and one whose only place
    # fact has been SUPERSEDED. Neither may be reachable by a place needle.
    "NONE.JPG": "2022:07:14 09:00:00",
    "DEAD.JPG": "2022:09:03 09:00:00",
}
PLACED_SIDECAR = "KY_2.xmp"

# The place names, written out here and read nowhere else. No word of any of them appears
# in any filename above, which is what `test_no_place_word_reaches_a_name_or_a_label`
# measures rather than assumes.
KYOTO = "Kyoto"
OSAKA = "Ōsaka"
NARA = "Nara"
# The caption label seeded beside them. Clarification 6: neither spec 8.2 nor UX spec 5
# promises caption or scene search at any milestone, so this must be UNREACHABLE - the
# negative half of `review.SEARCHABLE_FACT_KINDS` being wired to one kind.
CAPTION_LABEL = "Blue Door"

KYOTO_PHOTOS = ["KY_1.JPG", "KY_2.JPG"]


def _seed_fact(
    db_path: Path,
    sha256: str,
    kind: str,
    value: dict[str, str],
    *,
    model: str = "rg_cities1000",
    version: str = "1.0",
    dead: bool = False,
) -> int:
    """One `inferred_facts` row, written with the SUITE'S OWN SQL, not `repo.insert_fact`.

    An oracle written through the production writer would agree with it by construction
    and prove nothing about either (M8's M922/M924), and `tests/test_place_naming.py`'s
    `add_fact` sets this exact precedent. `superseded_by` points at the row itself when
    `dead`: the column only has to be non-NULL for the live filter, and a self-reference
    satisfies the foreign key without inventing a second row nothing else looks at.
    """
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO inferred_facts (sha256, kind, value_json, confidence, model_name,"
            " model_version, generated_at) VALUES (?, ?, ?, NULL, ?, ?, ?)",
            (sha256, kind, json.dumps(value), model, version, "2026-09-03T09:00:00"),
        )
        fact_id = int(cur.lastrowid or 0)
        if dead:
            conn.execute(
                "UPDATE inferred_facts SET superseded_by = ? WHERE id = ?", (fact_id, fact_id)
            )
        conn.commit()
        return fact_id
    finally:
        conn.close()


def _sha256_by_name(db_path: Path) -> dict[str, str]:
    """`rel_path -> sha256` over the files that HAVE one.

    `tests/fake_exiftool.py` reads its tags out of a `<name>.tags.json` beside each file,
    and the scanner walks those too - they are ordinary files in this archive. They carry
    no digest pam would enrich against, so they are filtered here rather than being
    allowed to make the distinctness assertion below fail for the wrong reason.
    """
    conn = connect(db_path)
    try:
        return {
            str(row["rel_path"]): str(row["sha256"])
            for row in conn.execute(
                "SELECT rel_path, sha256 FROM files WHERE sha256 IS NOT NULL"
            )
        }
    finally:
        conn.close()


@pytest.fixture(scope="module")
def placed(tmp_path_factory):
    """An archive of real files whose files carry `place` facts: two live, one accented, one dead.

    The facts are seeded AFTER the scan, because they are keyed by `sha256` and nothing
    knows a digest before the scanner writes one. They are seeded BEFORE the plan only so
    the archive is in one state for the whole module; the plan's template names no place
    token, so nothing about the plan depends on them - which is the point.
    """
    home = tmp_path_factory.mktemp("placed-home")
    previous = os.environ.get("PAM_HOME")
    os.environ["PAM_HOME"] = str(home)
    try:
        root = tmp_path_factory.mktemp("placed-archive") / "photos"
        root.mkdir(parents=True)
        for name, taken in PLACED_FILES.items():
            _put(root, name, {"DateTimeOriginal": taken})
        _put(root, PLACED_SIDECAR, {})
        run_scan(root, exiftool_command=FAKE_COMMAND)
        ws = workspace_if_exists(root)
        assert ws is not None

        sha = _sha256_by_name(ws.db_path)
        assert len(set(sha.values())) == len(sha), (
            "two files share a digest, so one fact would cover both and every per-file"
            " assertion below would be measuring the wrong thing"
        )
        for name in KYOTO_PHOTOS:
            _seed_fact(ws.db_path, sha[name], "place", {"place": KYOTO})
        _seed_fact(ws.db_path, sha["OS.JPG"], "place", {"place": OSAKA})
        _seed_fact(ws.db_path, sha["DEAD.JPG"], "place", {"place": NARA}, dead=True)
        _seed_fact(
            ws.db_path,
            sha["KY_1.JPG"],
            "caption",
            {"label": CAPTION_LABEL, "sentence": "A blue door."},
            model="qwen2.5vl",
        )

        planned = run_plan(root, "{date}_{seq}")
        assert planned.plan_id is not None
    finally:
        if previous is None:
            os.environ.pop("PAM_HOME", None)
        else:
            os.environ["PAM_HOME"] = previous

    logger = logging.getLogger("pam.tests.webui_placed")
    logger.propagate = False
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    server = ReviewServer(workspace=ws, plan_id=planned.plan_id, logger=logger)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Live(
        server=server,
        root=root,
        db_path=ws.db_path,
        plan_id=planned.plan_id,
        token=server.token,
    )
    server.shutdown()
    server.server_close()
    thread.join(timeout=20)


def test_no_place_word_reaches_a_name_or_a_label(placed):
    """The precondition every row below rests on, measured rather than assumed.

    If a place name reached a proposed filename, an old filename or an event label, the
    three operands M7 and M9 shipped would satisfy the whole section and the fourth could
    be deleted with every test green - the vacuous oracle this project has now met with a
    query, a constant and a partition.
    """
    status, body = get(placed, f"/plan/{placed.plan_id}/items", limit=200)
    assert status == 200, body
    assert len(body["items"]) == len(PLACED_FILES) + 1
    for card in body["items"]:
        for text in (card["old_filename"], card["new_filename"]):
            for place in (KYOTO, OSAKA, NARA, CAPTION_LABEL):
                assert place.casefold() not in text.casefold(), (text, place)
    conn = connect(placed.db_path)
    try:
        labels = [row["label"] for row in conn.execute("SELECT label FROM events")]
    finally:
        conn.close()
    assert labels and all(label is None for label in labels), labels


@pytest.mark.parametrize(
    ("needle", "expected"),
    [
        # The place, whole and in part, in three casings. No word of it is in any filename
        # and no event here is labelled at all.
        (KYOTO, KYOTO_PHOTOS),
        ("kyo", KYOTO_PHOTOS),
        ("KYOTO", KYOTO_PHOTOS),
        (OSAKA, ["OS.JPG"]),
        # THE MEMBER THAT HAS THE FACT, AND NOT ITS UNIT PARTNER. `KY_2.xmp` shares a
        # `unit_key` with `KY_2.JPG`, has its own digest and carries no fact - so `place`
        # is a FOURTH filter dimension that is not uniform across a companion unit, which
        # is why spec 8.2 rev 15's widening is re-driven through it in
        # `tests/test_m9_safety_constructions.py`.
        ("KY_2", ["KY_2.JPG", PLACED_SIDECAR]),
        # A SUPERSEDED fact is not something this archive still says about the file.
        (NARA, []),
        # Clarification 6: captions and scenes are searchable at NO milestone, and a
        # caption fact sits in the very table the operand reads.
        (CAPTION_LABEL, []),
        ("Blue", []),
        # The names still match, unchanged since M7.
        ("ky_", [*KYOTO_PHOTOS, PLACED_SIDECAR]),
        ("qqq", []),
    ],
)
def test_the_search_matches_a_live_place_fact_and_only_a_place_fact(placed, needle, expected):
    assert found(placed, needle) == sorted(expected)


def test_the_accented_place_is_matched_BY_ITS_OWN_SPELLING_and_is_not_transliterated(placed):
    """The same folding decision the event label makes, asserted on the fact operand.

    Spec 8.2 says `q` searches case-insensitively and says nothing about transliteration;
    spec 6.2's NFKD + fold table (spec 5.3) is normative for FILENAMES, and applying a
    filename rule to a search box would be inventing a rule no spec carries. So `q`
    casefolds and does not fold, in both operands, and the two agree because they are one
    predicate rather than because somebody kept them in step.

    The user is not cut off from the accented place: the ASCII spelling finds it through
    the proposed NAME wherever `{place}` is templated, folded by rule 3 - which is exactly
    the second half of the event label's argument, and `tests/test_place_naming.py` owns
    that route. This archive templates no place, which is what isolates the operand.
    """
    assert found(placed, OSAKA) == ["OS.JPG"]
    assert found(placed, OSAKA.casefold()) == ["OS.JPG"]
    assert sanitize_token(OSAKA) == "Osaka"
    assert found(placed, "Osaka") == []


def test_a_file_with_no_place_fact_matches_exactly_what_its_NAMES_match_and_nothing_more(
    placed,
):
    """THE DEGRADATION, STATED AS A PROPERTY, exactly as the label operand states it.

    `NONE.JPG` carries no fact and `DEAD.JPG` only a superseded one, and neither may be
    reachable by a needle its own names do not contain - or the M10 operand would be a
    filter that WIDENS as the data thins, and "no place" would match every search instead
    of none. The oracle is a second implementation of the two name clauses, written from
    spec 8.2's own sentence rather than from `_matches_q`, which is under test.

    Each item is required to be matched by at least one needle before its misses are
    counted, because a list of misses would prove only that misses miss - the shape
    M743's empty parametrize taught this project to distrust.
    """
    status, body = get(placed, f"/plan/{placed.plan_id}/items", limit=200)
    assert status == 200, body
    cards = {card["old_filename"]: card for card in body["items"]}
    needles = [KYOTO, "kyo", OSAKA, NARA, CAPTION_LABEL, "none", "DEAD", "2022-0", "qqq"]
    assert len(needles) >= 5

    for name in ("NONE.JPG", "DEAD.JPG"):
        card = cards[name]
        hits = {
            needle
            for needle in needles
            if needle.casefold() in card["old_filename"].casefold()
            or needle.casefold() in card["new_filename"].casefold()
        }
        assert hits, f"no needle matches {name}'s names, so this proves only that misses miss"
        assert {needle for needle in needles if name in found(placed, needle)} == hits


def test_the_searchable_fact_kinds_are_place_and_place_alone(placed):
    """Clarification 6 and spec 8.2's degradation table, spelled from the SPEC.

    > "Search | name, people, event, place | ... place **M10** (rev 16) - people **M11**"

    and, above it, the sentence that bounds the list: neither spec 8.2 nor UX spec 5
    promises caption or scene search at any milestone. So the map is exactly one entry at
    M10, and the two halves of that entry are spec 6.1's `{place}` token and spec 6.4's
    write-time rule, "written about `place` tokens" - the KIND and the FIELD.

    Asserted as an exact equality rather than a membership test: `{"place": "place",
    "caption": "label"}` satisfies "place is in it" and ships a feature the deletion gate
    exists to remove, and the map growing silently is how the placeholder above it comes
    to promise something no sentence asked for.
    """
    assert repo.SEARCHABLE_FACT_KINDS == {"place": "place"}


# --- TWO LIVE FACTS ON ONE FILE: THE SECOND STATEMENT'S WHOLE JUSTIFICATION -------------------

# One companion unit, so `unit_size` has something to be wrong about. `.NEF` + `.JPG` is
# spec 5.4's `raw_jpeg` pairing.
MULTI_FILES = {"M1.NEF": "2022:03:04 09:00:00", "M1.JPG": "2022:03:04 09:00:00"}
MULTI_PLACES = ("Lisbon", "Porto")
# `UNIQUE (sha256, kind, model_name, model_version)` - two live rows of one kind on one
# digest need two model identities, and a version bump is the one spec 8.1 already names.
MULTI_VERSIONS = ("1.0", "2.0")


@pytest.fixture(scope="module")
def multi_fact(tmp_path_factory):
    """A companion unit one of whose members carries TWO live `place` facts.

    WHY THIS ARCHIVE EXISTS. `review._searchable_facts` is a SECOND STATEMENT rather than a
    fifth LEFT JOIN in `plan_rows`, and its docstring gives the reason: `inferred_facts` is
    content-addressed, so a join to it would MULTIPLY `plan_items` rows, and three numbers
    downstream assume one row per item - `unit_sizes` counts them, `api.counts` counts them,
    and the page's `total` is `len(chosen)`. Until this fixture no file in the suite carried
    more than one fact of a searchable kind, so the multiplication had no input and the
    argument for the shape was a paragraph rather than a test.

    Two live rows of one kind on one digest is a state the SCHEMA PERMITS AND WRITES. The
    constraint is `UNIQUE (sha256, kind, model_name, model_version)` (migration 0001), so
    two rows of one kind coexist as soon as the MODEL IDENTITY differs - which is why the
    two facts below are seeded at versions `1.0` and `2.0`. Nothing retires the first:
    `repo.supersede_facts` is driven with the ids the runner chose, and spec 8.1's
    `--refresh-older-than-model` is the only command that chooses any, so a plain second
    enricher at a new version leaves both rows live. `pam verify` is the command with an
    opinion about whether it should; the review page's obligation is to not corrupt its own
    arithmetic over it.
    """
    home = tmp_path_factory.mktemp("multi-home")
    previous = os.environ.get("PAM_HOME")
    os.environ["PAM_HOME"] = str(home)
    try:
        root = tmp_path_factory.mktemp("multi-archive") / "photos"
        root.mkdir(parents=True)
        for name, taken in MULTI_FILES.items():
            _put(root, name, {"DateTimeOriginal": taken})
        run_scan(root, exiftool_command=FAKE_COMMAND)
        ws = workspace_if_exists(root)
        assert ws is not None

        sha = _sha256_by_name(ws.db_path)
        for version, place in zip(MULTI_VERSIONS, MULTI_PLACES, strict=True):
            _seed_fact(ws.db_path, sha["M1.NEF"], "place", {"place": place}, version=version)
        # The state this fixture is FOR, measured before anything rests on it: two rows,
        # both live, one digest, one kind. A `_seed_fact` that had superseded the first
        # would leave one live fact and every assertion below would pass vacuously.
        conn = connect(ws.db_path)
        try:
            live = conn.execute(
                "SELECT COUNT(*) AS n FROM inferred_facts WHERE sha256 = ? AND kind = 'place'"
                " AND superseded_by IS NULL",
                (sha["M1.NEF"],),
            ).fetchone()["n"]
        finally:
            conn.close()
        assert live == 2

        planned = run_plan(root, "{date}_{seq}")
        assert planned.plan_id is not None
    finally:
        if previous is None:
            os.environ.pop("PAM_HOME", None)
        else:
            os.environ["PAM_HOME"] = previous

    logger = logging.getLogger("pam.tests.webui_multi")
    logger.propagate = False
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    server = ReviewServer(workspace=ws, plan_id=planned.plan_id, logger=logger)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Live(
        server=server,
        root=root,
        db_path=ws.db_path,
        plan_id=planned.plan_id,
        token=server.token,
    )
    server.shutdown()
    server.server_close()
    thread.join(timeout=20)


def test_the_two_files_really_are_one_companion_unit(multi_fact):
    """The other half of the precondition. Without it `unit_size` is trivially 1."""
    status, body = get(multi_fact, f"/plan/{multi_fact.plan_id}/items", limit=200)
    assert status == 200, body
    assert {card["old_filename"] for card in body["items"]} == set(MULTI_FILES)
    assert len({card["unit_key"] for card in body["items"]}) == 1


def test_two_live_facts_on_one_file_do_not_multiply_the_plan_s_rows(multi_fact):
    """The numbers `_searchable_facts` exists to protect, spelled from the archive.

    The expectations are written from what was PUT ON DISK - two files, one unit, neither
    decided - and not from what the endpoint happened to return, so a join that served the
    `.NEF` twice would show up as `total` 3 against a two-file archive rather than as two
    readings agreeing with each other.
    """
    status, page = get(multi_fact, f"/plan/{multi_fact.plan_id}/items", limit=200)
    assert status == 200, page
    assert page["total"] == len(MULTI_FILES)
    assert len(page["items"]) == len(MULTI_FILES)
    assert len({card["id"] for card in page["items"]}) == len(MULTI_FILES)
    # `unit_size` is the third number, and it is per card rather than per plan.
    assert [card["unit_size"] for card in page["items"]] == [len(MULTI_FILES)] * len(
        MULTI_FILES
    )

    status, summary = get(multi_fact, f"/plan/{multi_fact.plan_id}")
    assert status == 200, summary
    assert summary["total"] == len(MULTI_FILES)
    assert summary["remaining"] == len(MULTI_FILES)
    assert summary["approved"] == 0 and summary["skipped"] == 0


@pytest.mark.parametrize("place", MULTI_PLACES)
def test_every_live_fact_a_file_carries_is_searchable_not_just_one_of_them(multi_fact, place):
    """`_searchable_facts` keeps ALL the live values, and this is what that sentence means.

    `places_by_file_id` breaks a tie between two live facts by taking the highest id
    because spec 6.2 rule 6 requires one name from one set of inputs. A SEARCH has no such
    obligation, and a needle matching a fact this archive holds about the file must find
    it - an operand that matched fewer values would HIDE a card, which is the failure UX
    spec 1.4 names. Both values are asserted, so a tie-break borrowed from the naming path
    would fail on whichever value it discarded.
    """
    status, body = get(multi_fact, f"/plan/{multi_fact.plan_id}/items", q=place, limit=200)
    assert status == 200, body
    assert [card["old_filename"] for card in body["items"]] == ["M1.NEF"]
