"""Spec 8.2's read API, over real loopback HTTP against a real plan (M7 Task 8).

ONE MODULE-SCOPED LIVE SERVER AND ONE ARCHIVE (M7 plan clarification 10). The suite sits
near its 120 s re-flag threshold, and the expensive half of a live server is not the
socket - it is the archive behind it, which is scanned once here and planned twice.

THE REQUESTS ARE REAL HTTP, through stdlib `http.client`. The API is a wire format before
it is a function, and the two things most likely to be wrong about it - what a query
string parses to, and what `json.dumps` does to a payload - are invisible to a test that
calls the handler directly. Spec 10's network guard covers `pam/` only, so a test client
here is not a widening of it.

THE BROWSER IS PATCHED FOR EVERY TEST IN THIS MODULE by an autouse fixture. A suite that
opens real browser tabs is a broken suite, and Task 7's review measured that the failure
is silent on the machine of whoever wrote the test.

WHAT THE TWO PLANS ARE FOR. `main_plan` uses the configured template and is the dated,
tiered, filtered subject of nearly everything below. `orig_plan` re-plans the SAME
catalog with `{orig}_{seq}`, a template that emits NO DATE TOKEN AT ALL - which is spec
8.2's "no date token emitted (undated)" reached through the real planner rather than
asserted. An item whose date token holds `build_stem`'s literal `undated` is NOT
constructible through `pam plan` under the default configuration today (every primary a
companion group can elect is an image or a video, so every one of them carries a
`capture` row, and `build_capture` degrades to `file_mtime` rather than to nothing), so
that case is pinned against `date_token_value` directly, from spec 6.1's wording.
"""

import dataclasses
import json
import logging
import os
import re
import sqlite3
import threading
import webbrowser
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NamedTuple
from urllib.parse import urlencode

import pytest
from archives import FACES_FLOOR, FAKE_COMMAND

from pam.catalog import repo
from pam.catalog.db import connect
from pam.catalog.repo.review import ReviewItem, date_token_value
from pam.config import load_config
from pam.naming.collision import split_rel_path
from pam.naming.planner import run_plan
from pam.naming.sanitize import MAX_FULL_PATH
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan
from pam.webui import api, provenance
from pam.webui.server import ReviewServer

_REAL_WEBBROWSER_OPEN = webbrowser.open


@pytest.fixture(autouse=True)
def no_real_browser(monkeypatch):
    """Every test in this module gets a stand-in for `webbrowser.open`."""
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: True)


def test_the_browser_patch_is_autouse_and_not_opt_in():
    """Takes no fixture argument, so it is only ever patched by the autouse above."""
    assert webbrowser.open is not _REAL_WEBBROWSER_OPEN


# --- the archive, built once ---------------------------------------------------------

# The fixture archive, spelled out as data so every expectation below can be read against
# it. `Orientation` appears on exactly ONE file: spec 8.2 says orientation is applied
# client-side "from the orientation the catalog already recorded", and there is no
# `orientation` column in any migration - the recorded value is the `Orientation` KEY
# inside `capture.exif_json`, which exiftool's unrestricted `-json -n` dump carries.
ARCHIVE = {
    # A live-photo unit: one stem, two extensions, renamed together (spec 5.4).
    "IMG_1.JPG": {"DateTimeOriginal": "2019:07:04 18:23:11", "Orientation": 6},
    "IMG_1.MOV": {"CreateDate": "2019:07:04 18:23:11"},
    # A sidecar unit: the `.xmp` gets no capture row and is never a primary.
    "IMG_2.JPG": {"DateTimeOriginal": "2020:08:05 09:15:00"},
    "IMG_2.xmp": {},
    "IMG_3.JPG": {"DateTimeOriginal": "2019:08:09 11:02:03"},
    # A5 boundary: a plain `qt_utc` video with nothing to disagree with. Medium, unflagged
    # and NOT attention - the case the equivalence claim would be vacuous without.
    "VID_PLAIN.MOV": {"CreateDate": "2021:03:03 10:00:00"},
    # A5 class 1: `qt_utc` whose filename says a different day (spec 6.4 video-date-
    # ambiguous). The rule caps the date token to `low`, which caps the item to `low`.
    "2018-01-01_VID.MOV": {"CreateDate": "2021:03:04 10:00:00"},
}

# A5 class 2: spec 6.2 rule 5's path budget, made to bite. It lives in a directory long
# enough that `stem_budget` leaves exactly TWELVE characters, which fits `2022-02-02`
# whole and cuts `001` down to `0` - so the DATE survives intact and the SEQ is truncated.
# That separation is the point: the item is `medium` and dated, so `attention` can only be
# true through the truncation operand, and the operand is pinned alone.
TRUNCATED_FILE = "T.JPG"
TRUNCATED_TAGS = {"DateTimeOriginal": "2022:02:02 08:00:00"}
TRUNCATED_BUDGET = 12

# What the eight files become under the configured template, as the planner writes them.
# Spelled out here rather than read back from `plan_items`, so a change to the planner is
# a red test in this module too.
MAIN_NAMES = {
    "2018-01-01_VID.MOV": "2021-03-04_001.MOV",
    "IMG_1.JPG": "2019-07-04_001.JPG",
    "IMG_1.MOV": "2019-07-04_001.MOV",
    "IMG_2.JPG": "2020-08-05_001.JPG",
    "IMG_2.xmp": "2020-08-05_001.xmp",
    "IMG_3.JPG": "2019-08-09_001.JPG",
    "VID_PLAIN.MOV": "2021-03-03_001.MOV",
    "T.JPG": "2022-02-02_0.JPG",
}

# spec 6.3.4 tier and spec 6.4 trust score per file, derived from the spec's own tables:
# `exif` dates are high and lose nothing; `qt_utc` caps the date token to medium and costs
# TRUST_QT_UTC (10); the video-date-ambiguous rule caps to low and costs a further
# TRUST_CONSISTENCY (15); truncation caps to medium and costs TRUST_TRUNCATED (10).
MAIN_TIERS = {
    "2018-01-01_VID.MOV": ("low", 75),
    "IMG_1.JPG": ("high", 100),
    "IMG_1.MOV": ("high", 100),
    "IMG_2.JPG": ("high", 100),
    "IMG_2.xmp": ("high", 100),
    "IMG_3.JPG": ("high", 100),
    "VID_PLAIN.MOV": ("medium", 90),
    "T.JPG": ("medium", 90),
}

# The two items spec 8.2's `attention` predicate selects out of the eight: the first by
# the `low` operand alone, the second by the `truncated` operand alone. Nothing else in
# this plan is attention - the boundary case above is what keeps that from being vacuous.
ATTENTION = ("2018-01-01_VID.MOV", "T.JPG")

APPROVED = ("IMG_1.JPG", "IMG_1.MOV")
SKIPPED = ("IMG_3.JPG",)


class Live(NamedTuple):
    server: ReviewServer
    root: Path
    # The catalog's absolute path, carried rather than re-resolved: `workspace_if_exists`
    # reads PAM_HOME, and `conftest.py`'s function-scoped `isolated_pam_home` hands every
    # test a fresh empty one. The server holds absolute paths for the same reason.
    db_path: Path
    main_plan: int
    orig_plan: int
    token: str
    ids: dict[str, int]  # old filename -> plan_items.id in `main_plan`
    corrupt: str  # the old filename whose tokens_json was deliberately damaged
    deep_dir: str


def _put(directory: Path, name: str, tags: dict[str, Any]) -> None:
    """One archive file plus the sidecar `tests/fake_exiftool.py` reads its tags from."""
    (directory / name).write_bytes(f"the only copy of {name}".encode())
    (directory / f"{name}.tags.json").write_text(
        json.dumps({**tags, "PamTestSidecar": name}), encoding="utf-8"
    )


def _deep_directory(root: Path) -> Path:
    """A subdirectory long enough that `stem_budget` leaves `TRUNCATED_BUDGET` characters.

    Computed from spec 6.2 rule 5's own formula rather than hardcoded, because the
    archive lives under a temp path whose length differs on every machine.
    """
    pad = MAX_FULL_PATH - len(str(root)) - len("JPG") - 1 - TRUNCATED_BUDGET
    assert pad > 0, (
        f"the temp path is {len(str(root))} characters, which leaves no room to build a"
        " directory deep enough to exercise the path budget. Point pytest at a shorter"
        " --basetemp rather than weakening the fixture"
    )
    deep = root / ("d" * pad)
    deep.mkdir()
    return deep


def _build_archive(root: Path) -> str:
    root.mkdir(parents=True)
    for name, tags in ARCHIVE.items():
        _put(root, name, tags)
    deep = _deep_directory(root)
    _put(deep, TRUNCATED_FILE, TRUNCATED_TAGS)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    return deep.name


def _ids(conn: sqlite3.Connection, plan_id: int) -> dict[str, int]:
    return {
        item.old_filename: item.id for item in repo.plan_rows(conn, plan_id, floor=FACES_FLOOR)
    }


def _prepare_catalog(
    db_path: Path, main_plan: int, orig_plan: int, corrupt: str
) -> dict[str, int]:
    """Set the two decided statuses, and damage one row's `tokens_json` on purpose.

    THE DAMAGE LIVES IN `orig_plan`, whose every item is undated already, so a row that
    degrades to undated changes none of that plan's counts - which is itself the
    assertion `test_a_row_whose_tokens_json_will_not_parse...` reads. Written with the
    suite's own SQL rather than through a repo function because no repo function writes
    catalog damage, and none should.
    """
    conn = connect(db_path)
    try:
        main_ids = _ids(conn, main_plan)
        orig_ids = _ids(conn, orig_plan)
        for name in APPROVED:
            repo.set_plan_item_status(conn, main_ids[name], "approved")
        for name in SKIPPED:
            repo.set_plan_item_status(conn, main_ids[name], "skipped")
        conn.execute(
            "UPDATE plan_items SET tokens_json = ? WHERE id = ?",
            ("{this is not json", orig_ids[corrupt]),
        )
        conn.commit()
    finally:
        conn.close()
    return main_ids


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """A real scanned archive, two real plans, and a real server serving them.

    PAM_HOME is set and restored INSIDE the setup, never held across the yield:
    `conftest.py`'s `isolated_pam_home` is function-scoped and would fight it.
    """
    home = tmp_path_factory.mktemp("api-home")
    previous = os.environ.get("PAM_HOME")
    os.environ["PAM_HOME"] = str(home)
    try:
        root = tmp_path_factory.mktemp("api-archive") / "photos"
        deep_dir = _build_archive(root)
        # `{orig}_{seq}` first, so the CONFIGURED template's plan is the one left `open`.
        orig = run_plan(root, "{orig}_{seq}")
        main = run_plan(root)
        assert orig.plan_id is not None and main.plan_id is not None
        ws = workspace_if_exists(root)
        assert ws is not None
    finally:
        if previous is None:
            os.environ.pop("PAM_HOME", None)
        else:
            os.environ["PAM_HOME"] = previous

    corrupt = "IMG_3.JPG"
    main_ids = _prepare_catalog(ws.db_path, main.plan_id, orig.plan_id, corrupt)

    logger = logging.getLogger("pam.tests.webui_api")
    logger.propagate = False
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    server = ReviewServer(workspace=ws, plan_id=main.plan_id, logger=logger)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield Live(
        server=server,
        root=root,
        db_path=ws.db_path,
        main_plan=main.plan_id,
        orig_plan=orig.plan_id,
        token=server.token,
        ids=main_ids,
        corrupt=corrupt,
        deep_dir=deep_dir,
    )
    server.shutdown()
    server.server_close()
    thread.join(timeout=20)


class Reply(NamedTuple):
    status: int
    body: Any


def get(live: Live, path: str, **params: Any) -> Reply:
    """One GET to the live server, JSON decoded. Query values are urlencoded properly."""
    query = urlencode([(k, v) for k, v in params.items() if v is not None], doseq=True)
    url = f"/t/{live.token}/api{path}" + (f"?{query}" if query else "")
    conn = HTTPConnection(live.server.host, live.server.port, timeout=20)
    try:
        conn.request("GET", url)
        response = conn.getresponse()
        raw = response.read()
        return Reply(response.status, json.loads(raw) if raw else None)
    finally:
        conn.close()


def items(live: Live, plan_id: int | None = None, **params: Any) -> list[dict[str, Any]]:
    reply = get(live, f"/plan/{live.main_plan if plan_id is None else plan_id}/items", **params)
    assert reply.status == 200, reply.body
    return reply.body["items"]


def names(cards: list[dict[str, Any]]) -> list[str]:
    return [card["old_filename"] for card in cards]


# --- /plan/{id} ----------------------------------------------------------------------


def test_the_plan_summary_is_the_header_and_the_smart_review_banner(live):
    """UX 5's header line and UX 5a's banner, counted from the plan the planner wrote."""
    reply = get(live, f"/plan/{live.main_plan}")
    assert reply.status == 200
    body = reply.body
    assert body["plan_id"] == live.main_plan
    assert body["archive_root"] == str(live.root)
    assert body["template"] == "{date}_{event-or-scene}_{people}_{seq}"
    assert body["status"] == "open"
    assert body["created_at"]
    assert body["total"] == len(MAIN_NAMES) == 8
    assert body["approved"] == len(APPROVED) == 2
    assert body["skipped"] == len(SKIPPED) == 1
    assert body["remaining"] == 5
    assert body["attention"] == len(ATTENTION) == 2
    assert body["tiers"] == {"high": 5, "medium": 2, "low": 1}
    assert body["statuses"] == {"approved": 2, "skipped": 1, "proposed": 5}


def test_the_summary_counts_add_up_to_the_plan(live):
    """`remaining` is everything that is neither approved nor skipped, never a status
    read by name - a status this endpoint had not heard of must not vanish."""
    body = get(live, f"/plan/{live.main_plan}").body
    assert body["approved"] + body["skipped"] + body["remaining"] == body["total"]
    assert sum(body["tiers"].values()) == body["total"]
    assert sum(body["statuses"].values()) == body["total"]


@pytest.mark.parametrize("suffix", ["", "/groups", "/items"])
def test_an_unknown_plan_id_is_a_404_that_says_so(live, suffix):
    """Spec 8.2 / UX 1.4: a refusal that names nothing is a dead end. Distinct from
    amendment A3's SILENT empty 404, which is what a request without the token gets."""
    reply = get(live, f"/plan/99999{suffix}")
    assert reply.status == 404
    assert "99999" in reply.body["error"]


def test_a_plan_id_that_is_not_a_number_is_a_404_and_not_a_crash(live):
    reply = get(live, "/plan/not-a-number")
    assert reply.status == 404
    assert "not-a-number" in reply.body["error"]


def test_a_request_without_the_token_gets_the_silent_empty_404(live):
    """The API's talkative refusals must not have widened amendment A3's gate."""
    conn = HTTPConnection(live.server.host, live.server.port, timeout=20)
    try:
        conn.request("GET", f"/t/not-the-token/api/plan/{live.main_plan}")
        response = conn.getresponse()
        assert (response.status, response.read()) == (404, b"")
    finally:
        conn.close()


# --- /plan/{id}/groups ---------------------------------------------------------------


def event_ids_by_day(db_path: Path) -> dict[str, int]:
    """`YYYY-MM-DD` of an event's start -> its id, read with the SUITE'S OWN SQL.

    An INDEPENDENT read of the events table, following `tests/test_planner.py`'s
    `stored_events` and the `repo.open_plan` ruling behind it: an oracle that calls the
    production query it is checking agrees with any value of that query. `event_id` is a
    database-assigned integer and nothing in the spec fixes its value, so an expectation
    has to look the id up - but it must look it up somewhere that is not `nav_tree`.

    The day is the key because it is unique in every fixture that calls this, which is
    asserted rather than assumed: two events starting on one day would silently collide
    and the caller would compare its expectation against whichever won.
    """
    conn = connect(db_path)
    try:
        rows = list(conn.execute("SELECT id, start_dt FROM events ORDER BY id"))
    finally:
        conn.close()
    by_day = {str(row["start_dt"])[:10]: int(row["id"]) for row in rows}
    assert len(by_day) == len(rows), "two events start on the same day - key by start_dt"
    return by_day


def test_the_nav_tree_is_year_month_event_with_counts_at_every_level(live):
    """Spec 8.2 rev 14: grouped by the DATE TOKEN'S VALUE at all THREE levels, so the nav
    agrees with the cards.

    Every count is an ITEM count, not a unit count: the live-photo pair puts two items in
    2019-07 and the sidecar unit puts two in 2020-08, which is what the user is picking
    between when they click a month.

    2020-08 IS THE LEVEL'S ARITHMETIC WRITTEN DOWN. The month holds two items and its one
    event holds ONE of them, because `IMG_2.xmp` is a sidecar: spec 8.3 rev 14 groups
    every present, dated file and a sidecar has no `capture` row, so it is in no event -
    while its CARD is dated, because a companion takes its date token from its unit's
    primary. A month's count is its events' counts plus its unevented items, and this is
    the fixture where those two numbers differ.

    Every label here is a DATE SPAN, spelled from UX spec 5 rev 8's own examples: every
    event M8 writes is unlabelled, because a label is geocoded and geocoding is M9.
    """
    day = event_ids_by_day(live.db_path)
    reply = get(live, f"/plan/{live.main_plan}/groups")
    assert reply.status == 200
    assert reply.body["groups"] == [
        {
            "key": "2019",
            "label": "2019",
            "count": 3,
            "months": [
                {
                    "key": "2019-07",
                    "label": "July",
                    "count": 2,
                    "events": [
                        {
                            "key": f"2019-07.e{day['2019-07-04']}",
                            "label": "Jul 4",
                            "count": 2,
                        }
                    ],
                },
                {
                    "key": "2019-08",
                    "label": "August",
                    "count": 1,
                    "events": [
                        {
                            "key": f"2019-08.e{day['2019-08-09']}",
                            "label": "Aug 9",
                            "count": 1,
                        }
                    ],
                },
            ],
        },
        {
            "key": "2020",
            "label": "2020",
            "count": 2,
            "months": [
                {
                    "key": "2020-08",
                    "label": "August",
                    "count": 2,
                    "events": [
                        {
                            "key": f"2020-08.e{day['2020-08-05']}",
                            "label": "Aug 5",
                            "count": 1,
                        }
                    ],
                }
            ],
        },
        {
            "key": "2021",
            "label": "2021",
            "count": 2,
            "months": [
                {
                    "key": "2021-03",
                    "label": "March",
                    "count": 2,
                    "events": [
                        {
                            "key": f"2021-03.e{day['2021-03-03']}",
                            "label": "Mar 3",
                            "count": 1,
                        },
                        {
                            "key": f"2021-03.e{day['2021-03-04']}",
                            "label": "Mar 4",
                            "count": 1,
                        },
                    ],
                }
            ],
        },
        {
            "key": "2022",
            "label": "2022",
            "count": 1,
            "months": [
                {
                    "key": "2022-02",
                    "label": "February",
                    "count": 1,
                    "events": [
                        {
                            "key": f"2022-02.e{day['2022-02-02']}",
                            "label": "Feb 2",
                            "count": 1,
                        }
                    ],
                }
            ],
        },
    ]


def test_the_unevented_sidecar_is_reachable_through_its_month_and_through_no_event(live):
    """The other half of 2020-08's arithmetic, read as CARDS rather than as counts.

    A count that does not reconcile is a bug; a count that reconciles while the missing
    item is reachable through nothing is a photograph the user cannot get to. The month
    shows both items and the event shows one, and the one it does not show is the sidecar.
    """
    day = event_ids_by_day(live.db_path)
    month = sorted(names(items(live, group="2020-08", limit=200)))
    event = sorted(names(items(live, group=f"2020-08.e{day['2020-08-05']}", limit=200)))
    assert month == ["IMG_2.JPG", "IMG_2.xmp"]
    assert event == ["IMG_2.JPG"]


def test_an_event_group_selects_through_file_events_and_not_through_the_file_id(live):
    """The event filter's selection is MEMBERSHIP, and this is the fixture that can tell.

    `IMG_1.JPG` and `IMG_1.MOV` are two different `files` rows in ONE event, and
    `IMG_2.JPG` is a third row in a different one. A filter that matched on the file's own
    id, or on anything positional, would return one card here or the wrong card; the
    membership `file_events` recorded returns exactly two.
    """
    day = event_ids_by_day(live.db_path)
    selected = sorted(names(items(live, group=f"2019-07.e{day['2019-07-04']}", limit=200)))
    assert selected == ["IMG_1.JPG", "IMG_1.MOV"]


def test_a_plan_whose_template_emits_no_date_token_is_entirely_undated(live):
    """Spec 8.2's "no date token emitted" reached through the real planner (A5 class 3).

    `{orig}_{seq}` names every file after itself, so not one item carries a date token -
    and the whole plan lands in the one bucket spec 8.2 pins last, with no months under
    it, because a bucket with no date has no months to have.
    """
    body = get(live, f"/plan/{live.orig_plan}/groups").body
    assert body["groups"] == [{"key": "undated", "label": "Undated", "count": 8, "months": []}]


def test_undated_is_pinned_after_the_years_and_carries_no_months():
    """The mixed tree, over `nav_tree` directly.

    It is pinned HERE rather than through the server because no plan `pam plan` can build
    today mixes the two: a plan's template either emits a date token for every item or for
    none, and an item whose date token holds the literal `undated` needs a companion
    primary with no `capture` row, which the default configuration cannot produce.
    """
    tree = repo.nav_tree(
        [
            _hand_item(1, "2020-05-01"),
            _hand_item(2, None),
            _hand_item(3, "2019-12-31"),
            _hand_item(4, "2019-12-01"),
        ]
    )
    assert [(year.key, year.count) for year in tree] == [
        ("2019", 2),
        ("2020", 1),
        ("undated", 1),
    ]
    assert tree[-1].months == ()
    assert [month.key for month in tree[0].months] == ["2019-12"]


def test_the_event_level_is_ordered_BY_ITS_SPAN_WHEN_THE_IDS_DISAGREE():
    """The ordering claim, over a fixture that can actually tell (M993).

    `_events_in` sorts by `(start, end, event_id)`, and the live archive cannot prove the
    first two terms matter: `pam plan` inserts events in chronological order, so their
    ids ascend with their spans and sorting by id alone produces the identical list.
    `test_the_event_level_is_ordered_by_its_own_span_and_not_by_id` in
    `test_webui_events.py` therefore passed under `key=lambda g: g.event_id` with the
    whole suite green - its name was a claim its fixture could not support.

    Here the ids run BACKWARDS against the dates: event 9 is in July, event 1 in
    September. A nav ordered by id would print September first, which is what UX spec 5's
    chronological nav forbids at every level.
    """
    tree = repo.nav_tree(
        [
            _hand_item(1, "2021-09-20", event_id=1),
            _hand_item(2, "2021-07-04", event_id=9),
        ]
    )
    by_month = {month.key: month for year in tree for month in year.months}
    assert [event.event_id for event in by_month["2021-07"].events] == [9]
    assert [event.event_id for event in by_month["2021-09"].events] == [1]

    same_month = repo.nav_tree(
        [
            _hand_item(3, "2021-07-25", event_id=2),
            _hand_item(4, "2021-07-04", event_id=8),
        ]
    )
    events = [month for year in same_month for month in year.months][0].events
    assert [event.event_id for event in events] == [8, 2], (
        "two events in ONE month must be ordered by their spans, and the ids here"
        " disagree with the dates on purpose"
    )


# --- /plan/{id}/items: the card ------------------------------------------------------


def test_every_item_carries_what_the_card_renders(live):
    """One card, field by field, against what the archive and spec 8.2 say it must hold."""
    card = _card(live, "IMG_1.JPG")
    assert card["id"] == live.ids["IMG_1.JPG"]
    assert card["old_rel_path"] == "IMG_1.JPG"
    assert card["rel_dir"] == ""
    assert card["old_filename"] == "IMG_1.JPG"
    assert card["generated_filename"] == MAIN_NAMES["IMG_1.JPG"]
    assert card["new_filename"] == MAIN_NAMES["IMG_1.JPG"]
    assert card["media_type"] == "image"
    assert card["ext"] == "JPG", "spec 5.4 rev 6: the extension keeps its on-disk case"
    assert (card["tier"], card["trust_score"]) == MAIN_TIERS["IMG_1.JPG"]
    assert card["trust_label"] == "High trust", "UX spec 5 rev 8's word for `high`"
    assert card["edited"] is False
    assert card["status"] == "approved"
    assert card["placeholder"] is False
    assert card["has_thumb"] is True
    assert card["attention"] is False
    assert card["date"] == "2019-07-04"
    assert card["orientation"] == 6


def test_every_item_carries_the_name_the_tier_and_the_score_the_planner_gave_it(live):
    """All eight, against the tables at the top of this module.

    Spelled from spec 6.1's template, spec 6.3.4's min-tier rule and spec 6.4's deduction
    table rather than read back out of `plan_items` - a serializer compared against the
    thing it serializes is the vacuity this project has now met three times.
    """
    cards = {card["old_filename"]: card for card in items(live, limit=200)}
    assert {name: card["new_filename"] for name, card in cards.items()} == MAIN_NAMES
    assert {name: card["generated_filename"] for name, card in cards.items()} == MAIN_NAMES
    assert {
        name: (card["tier"], card["trust_score"]) for name, card in cards.items()
    } == MAIN_TIERS


def test_the_trust_label_is_ux_5s_word_for_the_TIER_and_never_the_score(live):
    """UX spec 5 rev 8 / spec 6.4 rev 14, and HT-M7's P2 closed.

    THE THREE WORDS ARE SPELLED OUT HERE, INLINE, FROM UX SPEC 5's OWN SENTENCE -
    `high -> "High trust"`, `medium -> "Good"`, `low -> "Needs review"`. Never read out
    of `api.TRUST_LABELS`: `for tier in TIERS: assert card["trust_label"] ==
    LABELS[tier]` passes for ANY map, which is `repo.ENRICHERS` for the fifth time on
    this project and four times in M8's own mutation table.

    THE TWO MIDDLE ROWS ARE THE DEFECT ITSELF. Under M7's deleted 85/60 score bands a
    `medium`/90 item read "High trust" beside an `Approve High` button that excludes it,
    and a `low`/75 item read "Good" - the single riskiest name in a real-world library
    describing itself as good. So the tier, the score AND the word travel together in
    one comparison: a `trust_label` that went back to reading the score would still
    serve "High trust" for 90 and "Good" for 75, and this list says both are wrong.
    """
    cards = {card["old_filename"]: card for card in items(live, limit=200)}
    assert [
        (name, cards[name]["tier"], cards[name]["trust_score"], cards[name]["trust_label"])
        for name in ("IMG_1.JPG", "VID_PLAIN.MOV", TRUNCATED_FILE, "2018-01-01_VID.MOV")
    ] == [
        ("IMG_1.JPG", "high", 100, "High trust"),
        ("VID_PLAIN.MOV", "medium", 90, "Good"),
        (TRUNCATED_FILE, "medium", 90, "Good"),
        ("2018-01-01_VID.MOV", "low", 75, "Needs review"),
    ]


def test_a_tier_ux_5_does_not_name_is_rendered_as_itself_and_never_as_a_500():
    """`plan_items.tier` carries no CHECK constraint, so a row a later milestone wrote
    can hold a word this spec does not name. A review page that raised over one word
    would hide the whole plan; there is nothing better to say about such a tier than to
    say it, which is `app.js`'s own precedent for the tier BADGE."""
    item = dataclasses.replace(_hand_item(1, "2020-05-01"), tier="provisional")
    assert api._card(item, 1, {})["trust_label"] == "provisional"


def test_edited_is_a_byte_comparison_so_a_case_only_edit_is_an_edit():
    """Spec 8.2 rev 14. `edited` answers "did the USER type this name?", and the rename
    endpoint accepts a stem differing from the planner's only in case (`sanitize` never
    case-folds and spec 5.4 rev 6 keeps the extension byte-for-byte). A case-insensitive
    comparison would call `Img.JPG` over `IMG.JPG` a NON-edit and leave the planner's
    provenance - chips, label, percentage, bar - attached to a name pam did not write.

    Driven against `_card` with hand-built items: the case-only pair and the
    extension-case pair are legal through the API but are not states `pam plan` produces,
    so the live plan cannot reach them.
    """
    cases = [
        ("2019-07-04_001.JPG", "2019-07-04_001.JPG", False),
        ("IMG.JPG", "Img.JPG", True),
        ("IMG.JPG", "IMG.jpg", True),
        ("2019-07-04_001.JPG", "Fireworks.JPG", True),
    ]
    # M743: an expectation list that can be emptied takes its own assertion with it -
    # `[] == []` is green. Pinned by count, unparametrized, in the same test.
    assert len(cases) == 4, "the four cases below are the whole of this expectation"
    seen = [
        (
            generated,
            new,
            api._card(
                dataclasses.replace(
                    _hand_item(1, "2020-05-01"),
                    generated_filename=generated,
                    new_filename=new,
                ),
                1,
                {},
            )["edited"],
        )
        for generated, new, _ in cases
    ]
    assert seen == cases


def test_no_item_of_a_freshly_planned_plan_is_edited(live):
    """The other half of the predicate, over the wire. Every item of a plan nothing has
    renamed proposes the name the planner generated, so `edited` is false for all eight -
    and the card face therefore keeps its label, percentage, bar, badge and chips."""
    assert [card["edited"] for card in items(live, limit=200)] == [False] * 8


def test_a_card_in_a_subdirectory_reports_its_directory(live):
    card = _card(live, TRUNCATED_FILE)
    assert card["rel_dir"] == live.deep_dir
    assert card["old_rel_path"] == f"{live.deep_dir}/{TRUNCATED_FILE}"


def test_a_companion_unit_says_how_many_files_it_renames(live):
    """Amendment A6: a rename edits the whole unit's stem, so the card must say so.

    Counted over the whole plan and never over the page: the partner is frequently on
    another page or excluded by the current filter, and a count that shrank with the
    filter would tell the user a live-photo pair was a lone file.
    """
    assert _card(live, "IMG_1.JPG")["unit_size"] == 2
    assert _card(live, "IMG_1.MOV")["unit_size"] == 2
    assert _card(live, "IMG_1.JPG")["unit_key"] == _card(live, "IMG_1.MOV")["unit_key"]
    assert _card(live, "IMG_3.JPG")["unit_size"] == 1
    # The filter cannot shrink it: this page holds one member of a two-file unit.
    only_video = items(live, tier="high", status="approved", q=".MOV")
    assert names(only_video) == ["IMG_1.MOV"]
    assert only_video[0]["unit_size"] == 2


@pytest.mark.parametrize(
    "old_filename,media_type,has_thumb",
    [
        ("IMG_1.JPG", "image", True),
        ("IMG_1.MOV", "video", False),
        ("IMG_2.xmp", "sidecar", False),
    ],
)
def test_has_thumb_is_false_for_everything_spec_8_2_never_extracts(
    live, old_filename, media_type, has_thumb
):
    """Spec 8.2: no video, no sidecar, no `other`, nothing without a sha256, no
    placeholder. False here is the frontend's licence to draw the glyph without a
    wasted request."""
    card = _card(live, old_filename)
    assert (card["media_type"], card["has_thumb"]) == (media_type, has_thumb)


@pytest.mark.parametrize(
    "media_type,has_sha256,placeholder,expected",
    [
        ("image", True, False, True),
        # Spec 8.2's exclusion list, one operand at a time. Every video, every sidecar and
        # every `other` gets the glyph card; a file with no sha256 has no cache key; and a
        # cloud PLACEHOLDER is excluded because READING ONE HYDRATES IT (vision R2) - the
        # one entry on that list that is a safety rule rather than an optimisation.
        ("video", True, False, False),
        ("sidecar", True, False, False),
        ("other", True, False, False),
        ("image", False, False, False),
        ("image", True, True, False),
        # Two at once, so no operand can be credited with another's kill.
        ("image", False, True, False),
    ],
)
def test_has_thumb_excludes_each_thing_spec_8_2_lists_operand_by_operand(
    media_type, has_sha256, placeholder, expected
):
    """Spec 8.2's `has_thumb`, pinned against inputs `pam plan` cannot currently produce.

    Found by mutation (M645): deleting the `not self.placeholder` operand left the whole
    suite green. `naming/units._units` skips any unit holding a placeholder, so no real
    plan contains one - which makes the operand untestable through the server and is
    precisely why it has to be pinned on the pure property instead. Same for `has_sha256`:
    every fixture file is hashed.

    The `media_type` half IS reachable through the server and is covered there too
    (`test_has_thumb_is_false_for_everything_spec_8_2_never_extracts`); it is repeated
    here so all three operands are read off one list spelled from spec 8.2's own prose.
    """
    item = dataclasses.replace(
        _hand_item(1, "2020-05-01"),
        media_type=media_type,
        has_sha256=has_sha256,
        placeholder=placeholder,
    )
    assert item.thumbnailable is expected


@pytest.mark.parametrize(
    "exif,expected",
    [
        # The tag itself, which is what exiftool's `-json -n` writes for an oriented JPEG.
        ('{"Orientation": 6}', 6),
        ('{"Orientation": 1}', 1),
        # `0` is not "no orientation" - it is a recorded value, and `or`-style defaulting
        # would quietly promote it to 1.
        ('{"Orientation": 0}', 0),
        # THE MUTATION THIS PARAMETRIZE EXISTS FOR (M644). Every one of these contains the
        # string `Orientation` and none of them RECORDS one, so a substring test on the
        # blob answers all three wrongly while an exact key lookup answers them right.
        ('{"CameraOrientation": 6}', None),
        ('{"ScreenOrientation": 3, "Model": "X"}', None),
        ('{"Comment": "Orientation was not recorded"}', None),
        # Present, but not a number pam can hand the frontend as a CSS transform.
        ('{"Orientation": "Rotate 90 CW"}', None),
        ('{"Orientation": true}', None),
        ('{"Orientation": null}', None),
        # Absent, damaged, or no capture row at all.
        ('{"Model": "X"}', None),
        ("{this is not json", None),
        ("[]", None),
        (None, None),
    ],
)
def test_the_orientation_lookup_is_an_exact_key_and_never_a_substring(exif, expected):
    """CLAUDE.md's hard-won rule, as an assertion instead of a docstring (M644).

    "Never grep exiftool JSON for a bare tag name" has produced two false "it works"
    readings on this project. Task 8's code obeys it, but nothing measured that: the
    fixture archive carries `Orientation` on one file and no near-miss tag anywhere, so a
    substring test on the raw blob passed the whole suite.

    Driven against the pure function with hand-built records, because the near-miss cases
    are exactly the ones an archive of real files is not obliged to contain. The expectations are
    spelled from what the field IS - an integer EXIF tag read by name - not from
    `_orientation`.
    """
    from pam.catalog.repo.review import _orientation

    assert _orientation(exif) == expected


def test_orientation_comes_from_the_recorded_exif_key_and_is_none_when_absent(live):
    """Ruling 2 / spec 8.2. There is no `orientation` COLUMN, so it is read as an exact
    KEY out of the parsed `capture.exif_json` - never a substring test on the blob, which
    is the mistake `FileCreateDate` has caused on this project twice."""
    carried = [card for card in items(live, limit=200) if card["orientation"] is not None]
    assert names(carried) == ["IMG_1.JPG"]
    assert carried[0]["orientation"] == 6
    # A file with a capture row but no `Orientation` key, and one with no capture row.
    assert _card(live, "IMG_3.JPG")["orientation"] is None
    assert _card(live, "IMG_2.xmp")["orientation"] is None


def test_no_plan_item_is_a_placeholder_and_the_flag_says_so(live):
    """Spec 8.2 asks for the flag; `naming/units._units` skips any unit holding a
    placeholder, so today it is false for every item of every plan. Served from
    `files.placeholder` all the same, and asserted rather than assumed."""
    assert [card["placeholder"] for card in items(live, limit=200)] == [False] * 8


# --- provenance passes through unrecomputed ------------------------------------------


def test_tokens_and_reasons_arrive_semantically_equal_to_what_the_planner_stored(live):
    """Clarification 6, with the SUITE'S OWN SQL as the oracle.

    Read back through `repo.plan_rows` this would be circular - the same parse the API
    performs, compared against itself. The stored TEXT is fetched here instead, parsed
    here, and compared field for field against what came off the wire.

    Byte-for-byte is deliberately NOT the contract: both documents are embedded inside a
    larger JSON response, so whitespace and escaping may differ. Array order IS part of
    the contract in both - `tokens_json` is in stem order (spec 6.1) and
    `trust_reasons_json` is the order the deductions were charged in (spec 6.4).
    """
    conn = connect(live.db_path)
    try:
        stored = {
            row["old_rel_path"]: (row["tokens_json"], row["trust_reasons_json"])
            for row in conn.execute(
                "SELECT old_rel_path, tokens_json, trust_reasons_json"
                " FROM plan_items WHERE plan_id = ?",
                (live.main_plan,),
            )
        }
    finally:
        conn.close()
    assert len(stored) == 8

    for card in items(live, limit=200):
        tokens_json, reasons_json = stored[card["old_rel_path"]]
        assert card["tokens"] == json.loads(tokens_json), card["old_filename"]
        assert card["trust_reasons"] == json.loads(reasons_json), card["old_filename"]


def test_the_date_token_is_named_the_same_in_both_of_the_planners_fields(live):
    """`repo.review` matches on `resolved_from`; the planner writes `token` beside it.

    `{date}` has no alias - unlike `{event-or-scene}`, whose `token` and `resolved_from`
    genuinely differ - so the two are equal here, and that is recorded as a measured fact
    rather than left as an assumption a reader has to re-derive.
    """
    tokens = _card(live, "IMG_1.JPG")["tokens"]
    date_tokens = [t for t in tokens if t["resolved_from"] == "date"]
    assert len(date_tokens) == 1
    assert date_tokens[0]["token"] == "date"
    assert date_tokens[0]["value"] == "2019-07-04"
    assert date_tokens[0]["source_table"] == "capture"


def test_a_truncated_token_reaches_the_wire_as_the_planner_flagged_it(live):
    """The truncation the whole `T.JPG` fixture exists for, visible in the provenance."""
    tokens = _card(live, TRUNCATED_FILE)["tokens"]
    by_name = {token["resolved_from"]: token for token in tokens}
    assert by_name["date"]["value"] == "2022-02-02"
    assert by_name["date"]["truncated"] is False
    assert by_name["seq"]["truncated"] is True
    assert by_name["seq"]["value"] == "0"


def test_a_row_whose_tokens_json_will_not_parse_is_served_as_undated_not_as_a_500(live):
    """The docstring on `review._parsed` says this cannot happen and what pam does; this
    is the test that goes with the sentence.

    One damaged row must cost its own card its provenance and nothing else - a 500 would
    take the whole review page down over one row of catalog damage, and `pam verify` is
    the command with an opinion about catalog damage.
    """
    reply = get(live, f"/plan/{live.orig_plan}/items", limit=200)
    assert reply.status == 200
    damaged = [c for c in reply.body["items"] if c["old_filename"] == live.corrupt]
    assert len(damaged) == 1
    assert damaged[0]["tokens"] is None
    assert damaged[0]["trust_reasons"] == [], "only tokens_json was damaged"
    assert damaged[0]["date"] is None
    assert damaged[0]["attention"] is True
    assert len(reply.body["items"]) == 8, "the other seven cards are unaffected"


# --- the attention predicate, operand by operand -------------------------------------


def test_attention_is_true_for_exactly_the_two_items_that_earn_it(live):
    """Spec 8.2 rev 12 / UX 5a rev 6, against plans the REAL planner built.

    A5's equivalence claim is that "consistency-flagged" is fully covered by the `low`
    operand for every rule that exists through M10. Both rules are exercised here and
    each is asserted as `flag present <=> attention matches it`.
    """
    assert sorted(names(items(live, attention="1", limit=200))) == sorted(ATTENTION)
    assert sorted(names(items(live, attention="0", limit=200))) == sorted(
        set(MAIN_NAMES) - set(ATTENTION)
    )


def test_the_video_date_ambiguous_rule_caps_to_low_and_that_is_the_first_operand(live):
    """A5 class 1. The spec 6.4 flag is present as a human sentence AND the item is low,
    which is exactly the equivalence A5 replaces "consistency-flagged" with."""
    card = _card(live, "2018-01-01_VID.MOV")
    assert card["tier"] == "low"
    assert card["attention"] is True
    assert any("different day" in reason for reason in card["trust_reasons"])
    # The other two operands are FALSE here, so `low` is carrying it alone.
    assert card["date"] == "2021-03-04"
    assert all(token["truncated"] is False for token in card["tokens"])


def test_truncation_is_attention_on_its_own_without_low_and_without_undated(live):
    """A5 class 2, and the deliberate WIDENING the amendment records.

    Spec 6.4 caps a truncated token to `medium`, not to `low`, so this item is attention
    only because of the third operand. Deleting that operand leaves it unflagged - which
    is the whole reason the amendment says the set changes twice on purpose.
    """
    card = _card(live, TRUNCATED_FILE)
    assert card["tier"] == "medium"
    assert card["date"] == "2022-02-02"
    assert card["attention"] is True
    assert any(token["truncated"] for token in card["tokens"])


def test_undated_is_attention_on_its_own_without_low_and_without_truncation(live):
    """A5 class 3, in `orig_plan`, where a high-tier item still needs a human because the
    proposed name says nothing about when the photo was taken."""
    reply = get(live, f"/plan/{live.orig_plan}/items", q="IMG_1.JPG", limit=200)
    card = reply.body["items"][0]
    assert card["tier"] == "high"
    assert card["date"] is None
    assert all(token["truncated"] is False for token in card["tokens"])
    assert card["attention"] is True


def test_a_plain_qt_utc_video_is_medium_and_is_not_attention(live):
    """A5's BOUNDARY case, and the one that keeps the claim from being vacuous.

    `qt_utc` is the NORMAL source for video on users' archives (almost every real clip), so
    if medium-tier UTC video were attention, Smart Review would hand the user the whole
    video half of their archive. No consistency rule fires here, and no operand does.
    """
    card = _card(live, "VID_PLAIN.MOV")
    assert card["tier"] == "medium"
    assert card["trust_score"] == 90
    assert card["attention"] is False
    assert card["trust_reasons"] == [
        "Video time is recorded in UTC - the local date may differ."
    ]


@pytest.mark.parametrize(
    "tokens,expected",
    [
        ([{"resolved_from": "date", "value": "2019-07-04"}], "2019-07-04"),
        # `build_stem` substitutes this literal when no date was found (spec 6.1).
        ([{"resolved_from": "date", "value": "undated"}], None),
        # spec 6.2 rule 5 can leave the token holding a fragment of a date.
        ([{"resolved_from": "date", "value": "2019-0"}], None),
        ([{"resolved_from": "date", "value": "2019-07"}], None),
        # No date token at all: a template without `{date}`, or the budget cutting it.
        ([{"resolved_from": "seq", "value": "001"}], None),
        ([], None),
        # Catalog damage, which `_parsed` turns into None rather than a raise.
        (None, None),
        ("not a list", None),
        ([{"resolved_from": "date"}], None),
    ],
)
def test_the_date_token_definition_reads_spec_6_1s_shapes(tokens, expected):
    """The one definition every one of the three consumers calls, pinned from the spec."""
    assert date_token_value(tokens) == expected


# --- the filters ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "group,expected",
    [
        ("2019", ["IMG_1.JPG", "IMG_1.MOV", "IMG_3.JPG"]),
        ("2019-07", ["IMG_1.JPG", "IMG_1.MOV"]),
        ("2019-08", ["IMG_3.JPG"]),
        ("2021-03", ["2018-01-01_VID.MOV", "VID_PLAIN.MOV"]),
        ("2022", ["T.JPG"]),
        ("undated", []),
    ],
)
def test_the_group_filter_narrows_to_exactly_that_year_or_month(live, group, expected):
    assert sorted(names(items(live, group=group, limit=200))) == sorted(expected)


@pytest.mark.parametrize(
    "tier,expected",
    [
        ("low", ["2018-01-01_VID.MOV"]),
        ("medium", ["T.JPG", "VID_PLAIN.MOV"]),
        ("high", ["IMG_1.JPG", "IMG_1.MOV", "IMG_2.JPG", "IMG_2.xmp", "IMG_3.JPG"]),
    ],
)
def test_the_tier_filter_narrows_exactly(live, tier, expected):
    assert sorted(names(items(live, tier=tier, limit=200))) == sorted(expected)


@pytest.mark.parametrize(
    "status,expected",
    [
        ("approved", list(APPROVED)),
        ("skipped", list(SKIPPED)),
        (
            "proposed",
            ["2018-01-01_VID.MOV", "IMG_2.JPG", "IMG_2.xmp", "T.JPG", "VID_PLAIN.MOV"],
        ),
        ("applied", []),
    ],
)
def test_the_status_filter_narrows_exactly(live, status, expected):
    assert sorted(names(items(live, status=status, limit=200))) == sorted(expected)


@pytest.mark.parametrize(
    "needle,expected",
    [
        # The OLD name, case-insensitively.
        ("img_2", ["IMG_2.JPG", "IMG_2.xmp"]),
        # The PROPOSED name - nothing on disk is called this.
        ("2021-03", ["2018-01-01_VID.MOV", "VID_PLAIN.MOV"]),
        (".xmp", ["IMG_2.xmp"]),
        ("nothing matches this", []),
    ],
)
def test_the_search_matches_old_and_proposed_names_and_nothing_else_yet(live, needle, expected):
    """Amendment A4: old + proposed name in M7, plus the event label from M9 Task 11.

    THIS FIXTURE IS THE DEGRADED HALF and that is now the point of it: nothing here
    geocodes, so every `events.label` is NULL and the M9 operand contributes nothing.
    Spec 8.2's degradation table is about what the UI does when a thing is ABSENT, and
    an unlabelled archive - the tiny library, any archive before `pam enrich`, every
    archive M8 could build - must go on returning exactly the name matches it always
    did. The NULL is asserted below rather than assumed: if a future fixture change
    seeded a label, these expectations could start passing for a different reason.
    """
    conn = connect(live.db_path)
    try:
        labels = [r["label"] for r in conn.execute("SELECT label FROM events")]
    finally:
        conn.close()
    assert labels, "no events at all would make the M9 operand vacuous rather than NULL"
    assert set(labels) == {None}, f"this fixture must be the UNLABELLED case: {labels}"
    assert sorted(names(items(live, q=needle, limit=200))) == sorted(expected)


def test_the_search_does_not_reach_the_provenance_documents(live):
    """Amendment A4's BOUNDARY, which the parametrize above pins only the inside of.

    Found by mutation (M634): widening `_matches_q` to search `tokens_json` too left the
    whole suite green, because every needle in that parametrize either matches a filename
    or matches nothing at all. A scope restriction is only tested by a needle OUTSIDE it.

    Each needle is proven to be three things, in this order, or the test would pass for
    the wrong reason - which is exactly how a negative test goes vacuous:

    1. genuinely present in the provenance the planner stored (read as the suite's own
       SQL, so the oracle is not the parse the API performs);
    2. absent from every old and proposed filename in the plan;
    3. and therefore, per A4, matched by nothing the search returns.

    M9 TASK 11 WIDENED THE SCOPE RATHER THAN DELETING THIS TEST, exactly as the M7
    docstring said each milestone would: the needle must now also be absent from every
    event label, or "matched by nothing" would be proving that the scope excludes the
    provenance while a label quietly let it back in. Place and people widen it again.
    """
    conn = connect(live.db_path)
    try:
        rows = conn.execute(
            "SELECT old_rel_path, new_filename, tokens_json, trust_reasons_json"
            " FROM plan_items WHERE plan_id = ?",
            (live.main_plan,),
        ).fetchall()
        labels = [
            str(r["label"]) for r in conn.execute("SELECT label FROM events") if r["label"]
        ]
    finally:
        conn.close()
    assert len(rows) == 8
    provenance = " ".join(
        f"{r['tokens_json']} {r['trust_reasons_json']}" for r in rows
    ).casefold()
    all_names = " ".join(f"{r['old_rel_path']} {r['new_filename']}" for r in rows).casefold()
    all_labels = " ".join(labels).casefold()

    # `capture` is the `source_table` on every resolved token; `UTC` is a sentence out of
    # `trust_reasons_json`. One needle per document, so neither is covered by accident.
    for needle in ("capture", "UTC"):
        folded = needle.casefold()
        assert folded in provenance, (
            f"{needle!r} is not in the stored provenance, so this test would pass vacuously"
        )
        assert folded not in all_names, f"{needle!r} is in a filename, so it proves nothing"
        assert folded not in all_labels, (
            f"{needle!r} is in an event label, so it proves nothing"
        )
        assert items(live, q=needle, limit=200) == []


def test_the_filters_compose(live):
    """Each narrows the set the others left, rather than replacing it."""
    assert names(items(live, group="2019", tier="high", status="approved", limit=200)) == [
        "IMG_1.JPG",
        "IMG_1.MOV",
    ]
    assert names(items(live, group="2019", tier="high", status="approved", q=".JPG")) == [
        "IMG_1.JPG"
    ]
    assert items(live, group="2019", tier="low") == []
    assert names(items(live, attention="1", tier="medium")) == ["T.JPG"]


# --- ordering and paging -------------------------------------------------------------


def test_the_default_sort_is_lowest_trust_first(live):
    """UX 5a: "sort within every view puts lowest trust first, so attention lands where
    it pays". Asserted as the score sequence, which is what the rule is about."""
    cards = items(live, limit=200)
    assert [card["trust_score"] for card in cards] == [75, 90, 90, 100, 100, 100, 100, 100]
    assert cards[0]["old_filename"] == "2018-01-01_VID.MOV"
    assert names(items(live, sort="trust_asc", limit=200)) == names(cards)


def test_the_date_sort_is_chronological_with_undated_last(live):
    """The toggle UX 5a offers for chronological review."""
    cards = items(live, sort="date", limit=200)
    assert [card["date"] for card in cards] == [
        "2019-07-04",
        "2019-07-04",
        "2019-08-09",
        "2020-08-05",
        "2020-08-05",
        "2021-03-03",
        "2021-03-04",
        "2022-02-02",
    ]
    undated = items(live, live.orig_plan, sort="date", limit=200)
    assert [card["date"] for card in undated] == [None] * 8


def test_the_order_is_total_so_equal_trust_cannot_reorder_between_pages(live):
    """Spec 8.2: `id` is the tiebreaker "so paging cannot skip or repeat".

    Five of these eight items score 100. Without the tiebreaker their relative order is
    whatever the sort last happened to produce, and `offset=4` would be slicing a
    different list from the one `offset=0` came out of - so a card would be silently
    skipped and another shown twice, in a review the user believes is exhaustive.
    """
    tied = [card for card in items(live, limit=200) if card["trust_score"] == 100]
    assert [card["id"] for card in tied] == sorted(card["id"] for card in tied)


@pytest.mark.parametrize("sort", ["trust_asc", "date"])
def test_the_tiebreaker_orders_a_tie_the_server_did_not_receive_in_id_order(sort):
    """Spec 8.2's `id` tiebreaker, pinned WITHOUT the input already being id-ordered.

    Found by mutation (M632/M633): deleting `item.id` from BOTH branches of `_sort_key`
    left the whole suite green. `plan_rows` reads `ORDER BY i.id` and `list.sort` is
    stable, so every test that reaches the sort through the server hands it a list that
    is already in the order the tiebreaker would produce - and a test that cannot
    distinguish "the key sorted it" from "it arrived that way" is not pinning the key.

    So the pure function is driven directly with the tie SHUFFLED, and the expectation is
    spelled from spec 8.2's sentence - "a total order (id as tiebreaker) so paging cannot
    skip or repeat" - rather than from `_sort_key`. Both branches are covered: under
    `trust_asc` all five items tie on `trust_score`, and under `date` all five tie on the
    same date token, so in each mode `id` is the only thing left to order them by.
    """
    scrambled = [_hand_item(item_id, "2020-05-01") for item_id in (40, 10, 50, 20, 30)]
    ordered = repo.select_items(scrambled, repo.ItemFilter(sort=sort))
    assert [item.id for item in ordered] == [10, 20, 30, 40, 50]

    # The consequence the spec names: page one at a time and the whole list comes back
    # exactly once. Re-sorted per page, as the endpoint does, so an unstable order shows
    # up as a skipped or repeated id rather than only as a wrong sequence.
    paged = [
        item.id
        for offset in range(len(scrambled))
        for item in repo.select_items(scrambled, repo.ItemFilter(sort=sort))[
            offset : offset + 1
        ]
    ]
    assert paged == [10, 20, 30, 40, 50]


@pytest.mark.parametrize("sort", ["trust_asc", "date"])
def test_paging_one_at_a_time_reproduces_the_whole_list_exactly_once(live, sort):
    whole = names(items(live, sort=sort, limit=200))
    assert len(whole) == 8
    paged = [
        name
        for offset in range(0, 10)
        for name in names(items(live, sort=sort, offset=offset, limit=1))
    ]
    assert paged == whole, "a page boundary skipped or repeated a card"


def test_total_is_the_size_of_the_filtered_set_not_of_the_page(live):
    reply = get(live, f"/plan/{live.main_plan}/items", limit=2)
    assert reply.body["total"] == 8
    assert len(reply.body["items"]) == 2
    assert (reply.body["offset"], reply.body["limit"]) == (0, 2)
    filtered = get(live, f"/plan/{live.main_plan}/items", tier="high")
    assert filtered.body["total"] == 5


def test_paging_past_the_end_is_an_empty_page_and_not_a_wrapped_one(live):
    reply = get(live, f"/plan/{live.main_plan}/items", offset=100, limit=10)
    assert (reply.status, reply.body["items"], reply.body["total"]) == (200, [], 8)


def test_the_server_bounds_the_page_size_itself(live):
    """Spec 8.2's server-side maximum. A browser asking for a 20,000-item archive in one
    response is how the page becomes one that never paints."""
    assert api.MAX_LIMIT == 200
    assert get(live, f"/plan/{live.main_plan}/items", limit=api.MAX_LIMIT).status == 200
    over = get(live, f"/plan/{live.main_plan}/items", limit=api.MAX_LIMIT + 1)
    assert over.status == 400
    assert over.body["error"].startswith("limit:")


def test_isdigit_is_not_int_and_the_two_characters_that_prove_it(live):
    """`m9.md` C4: `_bounded_int`'s comment was wrong IN BOTH DIRECTIONS, and each half
    is one character.

    SUPERSCRIPT TWO: `isdigit()` is True and `int()` RAISES. It walked past the validator
    as a `ValueError`, which `server.py` turns into a 500 - spec 8.2 owes a 400 for a
    malformed query value, and a 500 is the one answer that tells the page nothing it can
    act on.

    ARABIC-INDIC DIGIT TWO: `isdigit()` is True and `int()` returns 2, so it is ACCEPTED -
    where the comment claimed unicode oddities were refused. That is asserted here as the
    behaviour it is, not fixed: it is a well-formed number in another script and refusing
    it would be pam deciding which digits a person may type.

    Both characters are spelled by codepoint name so the row cannot be mangled by an
    editor into an ASCII `2` and quietly stop testing anything.
    """
    superscript = "²"
    arabic_indic = "٢"
    assert superscript.isdigit() and arabic_indic.isdigit(), (
        "neither character reaches the branch this test is about any more"
    )
    with pytest.raises(ValueError):
        int(superscript)
    assert int(arabic_indic) == 2

    refused = get(live, f"/plan/{live.main_plan}/items", limit=superscript)
    assert refused.status == 400, refused.body
    assert refused.body["error"].startswith("limit:"), refused.body

    accepted = get(live, f"/plan/{live.main_plan}/items", limit=arabic_indic)
    assert accepted.status == 200, accepted.body
    assert accepted.body["limit"] == 2
    assert len(accepted.body["items"]) == 2, "and it really is used as the page size"


def test_limit_zero_asks_for_the_SIZE_of_a_filtered_set_and_none_of_it(live):
    """`limit=0` is the count request, and it is a CONTRACT rather than an accident.

    Every number `app.js` puts on a control is fetched this way - `Approve High (91)`,
    `Approve High+Medium (103)` and UX 5a's `[Needs attention (K)]` all go through one
    `scopeQuery` that sets `limit=0` on the same filter the button will POST, so the
    count the user reads and the set the server decides cannot disagree.

    Found by mutation (M747, M751-M752). M747 deleted the `limit` operand of the page
    slice outright and NO test asked for zero items, which is how the gap was noticed;
    M752 then expressed the real bug in the shape Python invites - `_bounded_int(...) or
    DEFAULT_LIMIT`, the falsy-default idiom, where a legitimate 0 silently becomes 100.

    The failure it guards is quiet by construction. The page would still show the RIGHT
    NUMBER, because `total` is the size of the filtered set and not of the page; it
    would just pull a hundred full cards - `tokens_json` and `trust_reasons_json` inside
    each - for every count on every view change and every debounced refresh, on the one
    path UX 5 needs to stay at a keystroke per photo. A wrong number would have been
    noticed. This would not.
    """
    whole = get(live, f"/plan/{live.main_plan}/items", limit=0)
    assert whole.status == 200
    assert whole.body["items"] == [], "limit=0 must return NO cards"
    assert whole.body["limit"] == 0, "the echoed limit is the page's evidence it was honoured"
    assert whole.body["total"] == len(MAIN_NAMES) == 8

    # The two shapes the page actually sends: a preset's tier scope, and 5a's attention
    # scope. Both spelled from this module's own data, not read back from the endpoint.
    high = get(live, f"/plan/{live.main_plan}/items", tier="high", limit=0)
    assert (high.body["items"], high.body["total"]) == (
        [],
        sum(1 for tier, _ in MAIN_TIERS.values() if tier == "high"),
    )
    attention = get(live, f"/plan/{live.main_plan}/items", attention="1", limit=0)
    assert (attention.body["items"], attention.body["total"]) == ([], len(ATTENTION))


# --- malformed queries ---------------------------------------------------------------

# Hoisted to a module constant so the control below can be pinned. A parametrize list is
# the expectation AND the control built from it, and pytest SKIPS an empty parametrize
# rather than failing it - emptying this one took the suite from 2265 passed to 2249
# passed + 2 skipped, exit 0, with sixteen refusal tests silently gone (M1074, and M743
# before it). The assert BELOW the list is the answer, on the precedent of
# `test_webui_decide.py::test_the_malformed_event_group_list_is_not_empty` - at collection
# rather than in a test, because a collection-time failure cannot itself be skipped.
MALFORMED_QUERIES = [
    {"tier": "excellent"},
    {"status": "pondered"},
    {"attention": "yes"},
    {"attention": "2"},
    {"group": "20199"},
    {"group": "2019-13"},
    {"group": "2019-7"},
    # A four-character head that is not a year. `_is_year_or_month` asks TWO things about
    # the head - that it is four characters and that it is digits - and until this row the
    # second was unpinned: deleting `head.isdigit()` left the whole suite green (M1069),
    # and the mutant accepts `abcd-07` as a filter, which `_matches_group` then compares
    # against `date[:7]` and matches nothing. That is amendment A2's own defect - a value
    # that validates and selects the empty set - surviving one operand to the left of the
    # form Task 3 fixed. Every other four-character-head row here dies on the LENGTH.
    {"group": "abcd-07"},
    {"group": "last summer"},
    # Spec 8.2 rev 14's event form, and the four ways to get it wrong. The event id
    # must be digits (`eX` is the mutation this list exists to kill); the head must
    # be a year-MONTH, because a nav level is a (month, event) pair and a bare year
    # names no month to file the event under; the id may not be empty; and `undated`
    # carries no months, so it carries no events either.
    {"group": "2019-07.eX"},
    {"group": "2019.e12"},
    {"group": "2019-07.e"},
    {"group": "undated.e1"},
    {"sort": "newest"},
    {"offset": "-1"},
    {"offset": "half"},
    {"limit": "0.5"},
]

# The EXACT length, not merely non-empty: emptying the list and narrowing it to one row
# are two different edits, and only the first is obvious vandalism ("the group rows are
# covered by the decide module, drop them" is a review comment somebody writes in good
# faith - M1035). A new refusal is welcome here; it just has to change this number too.
assert len(MALFORMED_QUERIES) == 17, "a malformed-query row was added or removed"


@pytest.mark.parametrize("params", MALFORMED_QUERIES)
def test_a_malformed_query_value_is_a_400_that_names_the_parameter(live, params):
    """UX 1.4 applies to the API too: the page shows these, and a refusal that does not
    say WHICH control is wrong is a dead end wearing an error message's clothes."""
    reply = get(live, f"/plan/{live.main_plan}/items", **params)
    parameter = next(iter(params))
    assert reply.status == 400, params
    assert reply.body["error"].startswith(f"{parameter}:"), reply.body


def test_a_year_with_a_trailing_separator_is_a_400_and_not_a_filter_that_matches_nothing(
    live,
):
    """Spec 8.2 rev 15 (amendment A2): the accepted forms are EXACTLY `2019`, `2019-07`,
    `2019-07.e12` and `undated`, and `2019-` is none of them.

    It used to validate and then select the empty set - `_is_year_or_month` asked whether
    the tail was empty rather than whether a dash was present, and `_matches_group` then
    compared `2019-` against `date[:7]`, which can never equal it. A filter that looks
    accepted and silently matches nothing is UX spec 1.4's dead end.

    THIS TEST IS THE CONDITION, not a follow-up to it. The M8 review applied the one-line
    fix and all 2204 tests stayed green in both directions (m8.md 2.3): nothing depended
    on the bug and nothing would have noticed the repair, so it ships with a test or it
    regresses in silence.

    Written as its own test rather than one more row of the malformed list above because
    the contrast with the neighbour below is the point - a WELL-FORMED group naming no
    event is a 200 with an empty page, and a MALFORMED one is a 400. The bug put `2019-`
    on the wrong side of that line. The bare year is driven too, so the refusal is pinned
    to the trailing separator and cannot pass by refusing years.
    """
    reply = get(live, f"/plan/{live.main_plan}/items", group="2019-", limit=200)
    assert reply.status == 400, reply.body
    assert reply.body["error"].startswith("group:"), reply.body

    year = get(live, f"/plan/{live.main_plan}/items", group="2019", limit=200)
    assert year.status == 200, year.body
    assert year.body["items"], "the year this is a mangling of must still select items"


def test_a_well_formed_event_group_that_names_no_event_is_an_EMPTY_PAGE_not_a_400(live):
    """The 400 is about the SHAPE of the value, never about whether it selects anything.

    A filter that refused an unknown id would turn an ordinary race - the user clicks an
    event in a nav the page fetched a moment ago, and a re-plan has since replaced the
    grouping - into a red error box where an empty grid is the honest answer. Every other
    filter on this endpoint behaves this way already (`group=undated` on this plan is an
    empty page, and the parametrized filter test above asserts it).
    """
    reply = get(live, f"/plan/{live.main_plan}/items", group="2019-07.e999999", limit=200)
    assert reply.status == 200, reply.body
    assert reply.body["items"] == []
    assert reply.body["total"] == 0


def test_a_parameter_given_twice_is_a_400_rather_than_a_silent_choice(live):
    reply = get(live, f"/plan/{live.main_plan}/items", tier=["high", "low"])
    assert reply.status == 400
    assert reply.body["error"].startswith("tier:")


@pytest.mark.parametrize("name", ["tier", "status", "group", "attention", "sort", "q"])
def test_an_empty_parameter_means_no_filter_rather_than_a_refusal(live, name):
    """The page clears a dropdown by sending it empty; that is not a malformed value."""
    reply = get(live, f"/plan/{live.main_plan}/items", limit=200, **{name: ""})
    assert reply.status == 200
    assert len(reply.body["items"]) == 8


# --- the two implementations this module keeps honest --------------------------------


@pytest.mark.parametrize(
    "rel_path", ["a.jpg", "2019/a.jpg", "a/b/c.jpg", "", "x/", "no-extension"]
)
def test_the_repo_layers_rel_path_split_matches_the_naming_layers(rel_path):
    """`review._split_rel_path` cannot import `naming.collision` (that package imports
    FROM the repo layer, so it would be a cycle). The two are pinned equal instead."""
    from pam.catalog.repo.review import _split_rel_path

    assert _split_rel_path(rel_path) == split_rel_path(rel_path)


def test_the_route_table_holds_the_read_endpoints_spec_8_2_names(live):
    """Spec 8.2's API block, read off the live server. A route added without a test is
    caught by the count; a route deleted is caught by the names.

    Widened by ONE entry in M7 Task 10: spec 8.2's API block lists
    `/thumb/{file_id}?variant=grid|full` beside the three plan reads, and the endpoint's
    own module is `tests/test_webui_thumbs.py`. The list grows entry by entry with a
    reason, exactly as `ALLOWED_NETWORK_IMPORTS` does; it is never relaxed into a count.

    WIDENED BY FOUR IN M11 TASK 10, and the guard FIRED on them rather than being updated
    ahead of them - which is the third time on this project that a new surface has been
    caught by a list rather than by a reviewer. They are amendment A18's read routes, they
    live in `pam/webui/faces_api.py` as a THIRD handler table merged in `ReviewServer`, and
    their own module is `tests/test_webui_faces.py`. Task 11 adds `faces/ignored` here with
    its six write routes; nothing is reserved for it in advance, because an entry with no
    endpoint behind it is exactly the thing this list is a guard against.

    WIDENED BY ONE IN M11 TASK 11 STEP 1: `faces/ignored`, A18's restore surface, served
    from `pam/webui/faces_manage_api.py` (a fourth table, for the file budget alone). Its
    own module is `tests/test_webui_faces_manage.py`.

    WIDENED BY ONE IN M11 TASK 11 STEP 2: `faces/person/{id}`, the maintainer's ruling 4 - one
    named person's faces, which Manage People needs ids from and no A18 route listed. Same
    table, same test module.

    WIDENED BY ONE IN M14 TASK 2: `plan/{id}/list`, U25's list groups, served from
    `pam/webui/nav.py`. Its own module is `tests/test_webui_listing.py`.
    """
    read_routes = sorted(
        template for (method, template) in live.server.api_handlers if method == "GET"
    )
    assert read_routes == [
        "faces/cluster/{id}",
        "faces/clusters",
        "faces/crop/{face_id}",
        "faces/ignored",
        "faces/person/{id}",
        "faces/persons",
        "ping",
        "plan/{id}",
        "plan/{id}/groups",
        "plan/{id}/items",
        "plan/{id}/list",
        "thumb/{file_id}",
    ]


def _hand_item(
    item_id: int,
    date: str | None,
    event_id: int | None = None,
    event_label: str | None = None,
) -> ReviewItem:
    """A `ReviewItem` built by hand, for the pure functions. Only the fields they read."""
    tokens = [] if date is None else [{"resolved_from": "date", "value": date}]
    return ReviewItem(
        id=item_id,
        file_id=item_id,
        old_rel_path=f"{item_id}.jpg",
        rel_dir="",
        old_filename=f"{item_id}.jpg",
        unit_key=f"\x1f{item_id}.jpg",
        generated_filename=f"{item_id}.jpg",
        new_filename=f"{item_id}.jpg",
        tier="high",
        trust_score=100,
        status="proposed",
        tokens=tokens,
        trust_reasons=[],
        filename=f"{item_id}.jpg",
        ext="jpg",
        media_type="image",
        placeholder=False,
        has_sha256=True,
        orientation=None,
        event_id=event_id,
        event_label=event_label,
    )


def _card(live: Live, old_filename: str) -> dict[str, Any]:
    """The one card for `old_filename` in the main plan, found by id rather than by q."""
    wanted = live.ids[old_filename]
    found = [card for card in items(live, limit=200) if card["id"] == wanted]
    assert len(found) == 1, f"{old_filename} is not in the plan exactly once"
    return found[0]


def test_the_catalog_is_never_written_by_a_read_request(live):
    """Spec 8.2: "the UI reads and approves; it never applies". Task 8 ships no writer at
    all, so every row of the plan must be byte-identical after the whole module has run.

    Compared against a snapshot taken through the suite's own SQL, not through the API.
    """
    conn = connect(live.db_path)
    try:
        before = conn.execute(
            "SELECT id, new_filename, status, tokens_json FROM plan_items ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    for suffix in ("", "/groups", "/items"):
        assert get(live, f"/plan/{live.main_plan}{suffix}").status == 200
    conn = connect(live.db_path)
    try:
        after = conn.execute(
            "SELECT id, new_filename, status, tokens_json FROM plan_items ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert [tuple(row) for row in before] == [tuple(row) for row in after]


# --- M10 Task 10: `fact_models` and vision Q3's confidence sentence -------------------
#
# A FOURTH ARCHIVE, and the reason is the same one `live`'s two plans record: this section
# needs a template naming `{place}` and `{scene}`, and `live`'s is the shipped default,
# whose spans, counts, tiers and truncation are the subject of most of this module. A
# module-scoped fixture two sections mutate is the order dependency M628a cost this
# project a security-relevant test to learn about.
#
# THE THREE FILES ARE THE THREE BRANCHES OF vision Q3 rev 4, one each:
#   P1 - no AI token at all          -> no `fact_models` entry, no sentence
#   P2 - an AI token whose model REPORTED a confidence -> an entry, and no sentence,
#        because Q3's FIRST half is satisfied and inventing the second would be untrue
#   P3 - an AI token whose model reported none         -> an entry, and the sentence
# M10 ships no enricher that emits a confidence (`geocode` and `caption` both store NULL,
# which spec 8.1 records for each), so P2's branch is reachable only from a seeded row -
# which is exactly why it is seeded rather than left as an unexercised `if`.
FACT_TEMPLATE = "{date}_{place}_{scene}_{seq}"
FACT_FILES = {
    "P1.JPG": "2021:01:02 10:00:00",
    "P2.JPG": "2021:01:02 11:00:00",
    "P3.JPG": "2021:01:02 12:00:00",
}
FACT_PLACE = "Kyoto"
FACT_SCENE = "Blue Door"
FACT_GEOCODER = ("rg_cities1000", "1.0")
FACT_VLM = ("qwen2.5vl", "7b")
# A confidence a model DID report. Any non-NULL value does the work; 0.87 is written out
# so the row reads as a measurement rather than as a flag.
REPORTED_CONFIDENCE = 0.87


def _seed_fact(
    db_path: Path,
    sha256: str,
    kind: str,
    value: dict[str, str],
    model: tuple[str, str],
    confidence: float | None,
) -> int:
    """One `inferred_facts` row, with the SUITE'S OWN SQL, never `repo.insert_fact`.

    An oracle written through the production writer would agree with it by construction
    and prove nothing about either (M8's M922/M924); `tests/test_place_naming.py`'s
    `add_fact` sets the precedent.
    """
    conn = connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO inferred_facts (sha256, kind, value_json, confidence, model_name,"
            " model_version, generated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sha256, kind, json.dumps(value), confidence, *model, "2026-09-03T09:00:00"),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


class FactLive(NamedTuple):
    server: ReviewServer
    plan_id: int
    token: str
    fact_ids: dict[str, int]  # old filename -> the `inferred_facts` id its token points at
    file_ids: dict[str, int]  # old filename -> `files.id`


@pytest.fixture(scope="module")
def fact_backed(tmp_path_factory):
    """An archive whose proposed names carry `{place}` and `{scene}` tokens.

    The facts are seeded AFTER the scan (nothing knows a digest before the scanner writes
    one) and BEFORE the plan, because these tokens must actually reach the names - which is
    what makes `tokens_json` carry a `source_table` of `inferred_facts` at all.
    """
    home = tmp_path_factory.mktemp("facts-home")
    previous = os.environ.get("PAM_HOME")
    os.environ["PAM_HOME"] = str(home)
    try:
        root = tmp_path_factory.mktemp("facts-archive") / "photos"
        root.mkdir(parents=True)
        for name, taken in FACT_FILES.items():
            _put(root, name, {"DateTimeOriginal": taken})
        run_scan(root, exiftool_command=FAKE_COMMAND)
        ws = workspace_if_exists(root)
        assert ws is not None

        conn = connect(ws.db_path)
        try:
            files = {
                str(r["rel_path"]): (int(r["id"]), str(r["sha256"]))
                for r in conn.execute(
                    "SELECT id, rel_path, sha256 FROM files WHERE sha256 IS NOT NULL"
                )
            }
        finally:
            conn.close()
        fact_ids = {
            "P2.JPG": _seed_fact(
                ws.db_path,
                files["P2.JPG"][1],
                "caption",
                {"label": FACT_SCENE, "sentence": "A blue door."},
                FACT_VLM,
                REPORTED_CONFIDENCE,
            ),
            "P3.JPG": _seed_fact(
                ws.db_path,
                files["P3.JPG"][1],
                "place",
                {"place": FACT_PLACE},
                FACT_GEOCODER,
                None,
            ),
        }
        planned = run_plan(root, FACT_TEMPLATE)
        assert planned.plan_id is not None
    finally:
        if previous is None:
            os.environ.pop("PAM_HOME", None)
        else:
            os.environ["PAM_HOME"] = previous

    logger = logging.getLogger("pam.tests.webui_facts")
    logger.propagate = False
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    server = ReviewServer(workspace=ws, plan_id=planned.plan_id, logger=logger)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield FactLive(
        server=server,
        plan_id=planned.plan_id,
        token=server.token,
        fact_ids=fact_ids,
        file_ids={name: ids[0] for name, ids in files.items()},
    )
    server.shutdown()
    server.server_close()
    thread.join(timeout=20)


def _fact_page(live: FactLive, limit: int = 200, offset: int = 0) -> dict[str, Any]:
    url = f"/t/{live.token}/api/plan/{live.plan_id}/items?limit={limit}&offset={offset}"
    conn = HTTPConnection(live.server.host, live.server.port, timeout=20)
    try:
        conn.request("GET", url)
        response = conn.getresponse()
        raw = response.read()
        assert response.status == 200, raw
        return json.loads(raw)
    finally:
        conn.close()


def test_the_fact_backed_fixture_really_put_both_tokens_into_the_names(fact_backed):
    """Read first, before anything is asserted about the response fields.

    If a fact had failed to reach a name, `tokens_json` would carry no `inferred_facts`
    source at all, `fact_models` would be legitimately empty, and every expectation below
    would be satisfied by a page with no provenance in it whatsoever.
    """
    cards = {card["old_filename"]: card for card in _fact_page(fact_backed)["items"]}
    assert cards["P2.JPG"]["new_filename"] == "2021-01-02_Blue-Door_002.JPG"
    assert cards["P3.JPG"]["new_filename"] == "2021-01-02_Kyoto_003.JPG"
    assert cards["P1.JPG"]["new_filename"] == "2021-01-02_001.JPG"


def test_fact_models_is_keyed_by_the_FACT_id_and_never_by_the_file_id(fact_backed):
    """Spec 8.2 rev 22: "a map from `inferred_facts` **id**", on EACH ITEM.

    THE TWO IDS ARE DIFFERENT NUMBERS IN THIS FIXTURE, asserted rather than hoped: the
    facts are seeded after the scan, so `P3.JPG` is the third file and the second fact.
    A map keyed by the file would be indexable by the same integers on a one-file archive
    and would pass an end-to-end test that never checked - which is why this precondition
    is an assertion in the test that depends on it.

    Keyed by the fact because that is what the PAGE holds: a token carries `source_id`, one
    file can carry several facts from several models, and one fact is shared by every
    duplicate of one photograph (the table is content-addressed).
    """
    cards = {card["old_filename"]: card for card in _fact_page(fact_backed)["items"]}
    ids = fact_backed.fact_ids
    files = fact_backed.file_ids
    assert {ids[name] for name in ids} != {files[name] for name in ids}
    assert cards["P2.JPG"]["fact_models"] == {str(ids["P2.JPG"]): "qwen2.5vl 7b"}
    assert cards["P3.JPG"]["fact_models"] == {str(ids["P3.JPG"]): "rg_cities1000 1.0"}


def test_fact_models_covers_exactly_the_tokens_whose_source_is_inferred_facts(fact_backed):
    """A token from `capture`, `files`, `events` or `planner` has no entry (spec 8.2).

    The oracle is built from the SERVED tokens' own `source_table`, which is the planner's
    document, and never from `provenance.fact_tokens`, which is the function under test.
    Both directions are checked: every AI token is covered, and the page really does hold
    tokens from other tables - a map that simply held every id it saw would satisfy an
    equality no other source of ids ever contested.

    THE TWO SETS ARE NOT COMPARED FOR OVERLAP, and that is not an omission: `source_id` is
    a row id in whichever table `source_table` names, so a `capture` id and an
    `inferred_facts` id are equal whenever two unrelated rows happen to share a number.
    Reading that as a leak would be reading two namespaces as one.
    """
    page = _fact_page(fact_backed)
    covered = {int(key) for card in page["items"] for key in card["fact_models"]}
    ai, other = set(), set()
    for card in page["items"]:
        for token in card["tokens"]:
            (ai if token["source_table"] == "inferred_facts" else other).add(token["source_id"])
    assert ai, "no token in this page came from an enricher, so this proves nothing"
    assert other, "every token here came from an enricher, so the exclusion is untested"
    assert covered == ai


def test_the_confidence_note_names_the_model_and_the_tier_it_produced(fact_backed):
    """Vision Q3 rev 4's second half, spelled from the VISION rather than from the code.

    Q3: an AI-derived token carries "either the model's confidence score or, where the
    model emits none, the tier that confidence-lessness produced, together with the
    sentence saying so". So the sentence must contain BOTH - the model that gave no score,
    and the word for the tier - and both halves are written out here from the spec side
    rather than compared against `provenance.CONFIDENCE_NOTE.format(...)`, which would
    make the expectation the production string and prove nothing (M8's M922/M924).

    The TOKEN's tier, not the item's. Spec 6.3 rule 4 makes an item's tier the minimum
    across all its tokens, so a date from `file_mtime` could drag it to `low` and this
    sentence would then blame the model for a cap the model did not cause. Here they
    coincide - spec 8.1 caps a confidence-less enricher at `medium` and nothing else on
    this card is lower - and the assertion is written against the `place` token's own tier.
    """
    cards = {card["old_filename"]: card for card in _fact_page(fact_backed)["items"]}
    card = cards["P3.JPG"]
    place = [t for t in card["tokens"] if t["resolved_from"] == "place"]
    assert len(place) == 1 and place[0]["tier"] == "medium"
    note = card["confidence_note"]
    assert "rg_cities1000 1.0" in note
    assert "Good" in note
    # And it does not name the model that DID report one, which is on another card.
    assert "qwen2.5vl" not in note


def test_a_model_that_REPORTED_a_confidence_gets_no_sentence(fact_backed):
    """Q3's first half is satisfied by the score, and the second must not be invented.

    Amendment A7 is emphatic that a confidence-less model is met by traceability plus the
    tier and NEVER by a proxy; the mirror of that rule is that a model which did report a
    score must not be described as having reported none. `P2.JPG` still gets its
    `fact_models` entry, because traceability is owed either way - only the sentence is
    absent.
    """
    cards = {card["old_filename"]: card for card in _fact_page(fact_backed)["items"]}
    card = cards["P2.JPG"]
    assert card["confidence_note"] is None
    assert str(fact_backed.fact_ids["P2.JPG"]) in card["fact_models"]


def test_an_item_with_no_ai_token_gets_no_sentence_and_no_entry(fact_backed):
    """`P1.JPG`'s name is a date and a sequence, and neither came from a model.

    "No entry" is now assertable on the CARD (spec 8.2 rev 22): under the response-level
    map this test could only say the item had no AI token, because the map beside `items`
    carried the OTHER cards' models and there was nothing per-card to be empty.
    """
    cards = {card["old_filename"]: card for card in _fact_page(fact_backed)["items"]}
    card = cards["P1.JPG"]
    assert card["confidence_note"] is None
    assert all(token["source_table"] != "inferred_facts" for token in card["tokens"])
    assert card["fact_models"] == {}


def test_the_served_tokens_are_the_planner_s_document_unedited_and_unreordered(fact_backed):
    """Spec 8.2: `fact_models` sits BESIDE the items and annotates no token.

    The oracle is `plan_items.tokens_json` read back with the suite's own SQL and parsed
    here, so a `_card` that had written a `model` key into a token - the obvious shortcut,
    and the one spec 8.2 rev 17 forbids in as many words - would fail this and nothing
    else. Order is meaning in this document, so the comparison is over the LIST.
    """
    page = _fact_page(fact_backed)
    conn = connect(fact_backed.server.workspace.db_path)
    try:
        stored = {
            str(row["old_rel_path"]): json.loads(row["tokens_json"])
            for row in conn.execute(
                "SELECT old_rel_path, tokens_json FROM plan_items WHERE plan_id = ?",
                (fact_backed.plan_id,),
            )
        }
    finally:
        conn.close()
    assert stored
    assert {card["old_rel_path"]: card["tokens"] for card in page["items"]} == stored


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        # The two shapes `review._parsed` can hand this: a damaged `tokens_json` parses to
        # None, and a hand-edited catalog can hold any JSON document at all.
        (None, []),
        ({"token": "date"}, []),
        ("[]", []),
        ([None, 7, "x"], []),
        # `True` is an `int` in Python and is not a row id.
        ([{"source_table": "inferred_facts", "source_id": True, "tier": "medium"}], []),
        ([{"source_table": "inferred_facts", "source_id": None, "tier": "medium"}], []),
        ([{"source_table": "capture", "source_id": 4, "tier": "high"}], []),
        ([{"source_table": "inferred_facts", "source_id": 4}], [(4, "")]),
        ([{"source_table": "inferred_facts", "source_id": 4, "tier": 9}], [(4, "")]),
        ([{"source_table": "inferred_facts", "source_id": 4, "tier": "low"}], [(4, "low")]),
    ],
)
def test_fact_tokens_reads_a_damaged_document_without_raising(tokens, expected):
    """One damaged row must cost one card its model name, never 500 the whole page.

    That is `review._parsed`'s ruling for the same document - `pam verify` is the command
    with an opinion about catalog damage - and it has to hold here too, because this
    module reads the parsed result rather than the text.
    """
    assert provenance.fact_tokens(tokens) == expected


@pytest.mark.parametrize(
    ("tiers", "expected"),
    [
        (["high"], "high"),
        (["high", "medium"], "medium"),
        (["low", "high"], "low"),
        (["medium", "low", "high"], "low"),
        # A tier spec 6.3.4 does not name sorts after all three and renders as itself,
        # on `trust_label`'s precedent: a review page must not 500 over one word.
        (["high", "provisional"], "provisional"),
        ([""], ""),
    ],
)
def test_the_lowest_tier_is_the_last_of_the_specs_three(tiers, expected):
    """`TIERS` is spelled `("high", "medium", "low")` in spec 6.3.4's own order."""
    assert provenance._lowest_tier(tiers) == expected


# --- THE TIER THE SENTENCE NAMES IS THE TOKEN'S, NOT THE ITEM'S -------------------------------

# `{date}` and `{place}` only. The scene token is deliberately absent: this archive exists
# to make the item's tier and the AI token's tier DISAGREE, and a second confidence-less
# enricher on the same card would be a second thing to reason about.
DIVERGENT_TEMPLATE = "{date}_{place}_{seq}"


@pytest.fixture(scope="module")
def divergent_tiers(tmp_path_factory):
    """A card whose ITEM tier is `low` while its only AI token's tier is `medium`.

    BUILT THROUGH THE REAL PIPELINE, not by hand, because this pipeline does produce the
    divergence and a hand-built `ReviewItem` would leave "can `pam plan` actually emit
    this?" unanswered (M8's M993 - a test name claiming "and not X" over a fixture that
    cannot separate the two).

    THE TWO TIERS, EACH FROM ITS OWN SPEC SENTENCE. The file carries NO EXIF date, so spec
    5.3's hierarchy falls through to `file_mtime`, which spec 6.1 scores `low`. The `place`
    fact carries `confidence` NULL, so spec 8.1's rule for a confidence-less enricher caps
    its token at `medium`. Spec 6.3 rule 4 then makes the ITEM's tier the minimum across
    all its tokens - `low`. The sentence vision Q3 rev 4 owes is about what
    confidence-lessness produced, and that is `medium`; the `low` belongs to a date no
    model touched.
    """
    home = tmp_path_factory.mktemp("divergent-home")
    previous = os.environ.get("PAM_HOME")
    os.environ["PAM_HOME"] = str(home)
    try:
        root = tmp_path_factory.mktemp("divergent-archive") / "photos"
        root.mkdir(parents=True)
        _put(root, "D1.JPG", {})
        run_scan(root, exiftool_command=FAKE_COMMAND)
        ws = workspace_if_exists(root)
        assert ws is not None

        conn = connect(ws.db_path)
        try:
            row = conn.execute(
                "SELECT f.id, f.sha256, c.capture_dt_source AS src FROM files f"
                " LEFT JOIN capture c ON c.file_id = f.id WHERE f.rel_path = 'D1.JPG'"
            ).fetchone()
            file_id, sha, date_source = int(row["id"]), str(row["sha256"]), row["src"]
        finally:
            conn.close()
        # The divergence is REAL before anything is asserted about the sentence. Without
        # this the fixture could silently have produced an `exif` date, both tiers would
        # read the same, and every expectation below would be met by either reading.
        assert date_source == "file_mtime"

        fact_id = _seed_fact(
            ws.db_path, sha, "place", {"place": FACT_PLACE}, FACT_GEOCODER, None
        )
        planned = run_plan(root, DIVERGENT_TEMPLATE)
        assert planned.plan_id is not None
    finally:
        if previous is None:
            os.environ.pop("PAM_HOME", None)
        else:
            os.environ["PAM_HOME"] = previous

    logger = logging.getLogger("pam.tests.webui_divergent")
    logger.propagate = False
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    server = ReviewServer(workspace=ws, plan_id=planned.plan_id, logger=logger)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield FactLive(
        server=server,
        plan_id=planned.plan_id,
        token=server.token,
        fact_ids={"D1.JPG": fact_id},
        file_ids={"D1.JPG": file_id},
    )
    server.shutdown()
    server.server_close()
    thread.join(timeout=20)


def test_the_fixture_really_separates_the_item_s_tier_from_the_ai_token_s(divergent_tiers):
    """Read first. If the two tiers coincided here, the test below would prove nothing.

    This is the precondition M8's M993 is about: `confidence_note`'s docstring claims the
    ITEM's tier is "deliberately not used", and until this fixture existed every card the
    suite served had `item.tier == token.tier`, so the claim was an untested sentence and
    substituting one for the other was a silent survivor (M1634).
    """
    card = _fact_page(divergent_tiers)["items"][0]
    place = [t for t in card["tokens"] if t["resolved_from"] == "place"]
    assert len(place) == 1
    assert place[0]["source_table"] == "inferred_facts"
    assert (card["tier"], place[0]["tier"]) == ("low", "medium")


def test_the_confidence_note_names_the_AI_token_s_tier_and_not_the_item_s(divergent_tiers):
    """Vision Q3 rev 4: "the tier that CONFIDENCE-LESSNESS produced".

    Spelled from the two specs by hand rather than from `provenance.CONFIDENCE_NOTE`: the
    word for `medium` is UX spec 5's `Good`, the word for `low` is `Needs review`, and the
    sentence must carry the first and not the second. Reading the item's tier here would
    blame `rg_cities1000` for a cap a `file_mtime` date caused, which is the opposite of
    what Q3 asks the sentence to disclose.
    """
    card = _fact_page(divergent_tiers)["items"][0]
    note = card["confidence_note"]
    assert "rg_cities1000 1.0" in note
    assert "Good" in note
    assert "Needs review" not in note


def test_a_token_pointing_at_a_row_that_is_not_there_costs_one_sentence_not_the_page():
    """`confidence_note`'s `fact is None` guard, which nothing else in the suite reaches.

    `provenance.py`'s own docstring for `fact_tokens` records the ruling this enforces -
    "a damaged row must cost one card its model name rather than 500 the whole review
    page" - and `review._parsed` and `review._orientation` are the two precedents it cites.
    The guard one level up is the same ruling about a different damage: a `source_id` that
    names an `inferred_facts` row the catalog no longer holds. `pam/` never deletes such a
    row, so no command can produce this state and no end-to-end fixture can reach it - a
    hand-edited catalog can, and `pam verify` is the command with an opinion about that.

    Two AI tokens, one resolvable and one dangling, so the assertion is about DEGRADING
    rather than about returning None: the sentence must still name the model that is
    there. A single dangling token would be satisfied by a function that gave up entirely.
    """
    item = dataclasses.replace(
        _hand_item(1, None),
        tier="medium",
        tokens=[
            {"source_table": "inferred_facts", "source_id": 11, "tier": "medium"},
            {"source_table": "inferred_facts", "source_id": 12, "tier": "medium"},
        ],
    )
    facts = {
        11: repo.FactModel(model_name="rg_cities1000", model_version="1.0", confidence=None)
    }
    note = provenance.confidence_note(item, facts)
    assert note is not None and "rg_cities1000 1.0" in note
    # And the map has nothing to say about the missing id either, rather than inventing one.
    assert provenance.fact_models(facts) == {11: "rg_cities1000 1.0"}


def test_each_card_carries_its_own_models_and_a_page_fetched_alone_is_complete(fact_backed):
    """M11 Task 5 / spec 8.2 rev 22 (A17): THE TEST THE OLD SHAPE COULD NOT EXPRESS.

    Through rev 21 `fact_models` was a page-level map beside `items`, so `app.js` merged it
    across pages and reset it only on a filter change - and scrolling past a page boundary
    could silently drop a page-1 card's model name, with no symptom but a missing
    parenthesis, in a file nothing in this project lints, types or executes (`m7.md` F4).
    Dropping the merge left every test green (M1643, and M1436 / M1640-M1643 with it),
    because from this side of the wire there was no per-card fact to assert. **A17 deletes
    the class rather than testing it**, and this is what deletion buys: the model a card
    renders is a field ON THAT CARD, so a page fetched on its own is complete.

    THE THREE PROPERTIES, and the second is the one that used to be unreachable:

    1. Two cards in ONE response, pointing at DIFFERENT fact ids, each rendering its own
       model - so a card cannot be reading a neighbour's entry.
    2. Each card fetched ALONE, one page per card, carries exactly the same map. Under the
       old shape a single-card page carried only its own id and the client had to have kept
       the rest; here there is no "rest" to keep.
    3. No card carries a model belonging to a fact its own tokens do not point at. This is
       `_card`'s `if i in facts` narrowing, and it is a SCOPE-PRIVACY property, not tidiness:
       a card serving the whole page's map would disclose what OTHER photographs were
       enriched with, to a page that has no business rendering it.

    EVERY EXPECTED LABEL IS SPELLED OUT BY HAND. `"<model_name> <model_version>"` is spec
    8.2's shape, written here from the spec side; calling `provenance.model_label` to build
    it would make the expectation the production string and prove only that production
    equals itself (M8's M922/M924, and `repo.ENRICHERS` before it).
    """
    ids = fact_backed.fact_ids
    whole = _fact_page(fact_backed)
    assert len(whole["items"]) > 1, "one page cannot exercise a per-page boundary"
    cards = {card["old_filename"]: card for card in whole["items"]}

    # 1 - two cards, two different fact ids, each with its own model and only its own.
    assert ids["P2.JPG"] != ids["P3.JPG"]
    assert cards["P2.JPG"]["fact_models"] == {str(ids["P2.JPG"]): "qwen2.5vl 7b"}
    assert cards["P3.JPG"]["fact_models"] == {str(ids["P3.JPG"]): "rg_cities1000 1.0"}

    # 2 - one page per card, each fetched on its own, each complete.
    alone = {}
    for offset in range(whole["total"]):
        page = _fact_page(fact_backed, limit=1, offset=offset)
        assert page["total"] == whole["total"]
        assert len(page["items"]) == 1
        card = page["items"][0]
        alone[card["old_filename"]] = card["fact_models"]
    assert alone["P2.JPG"] == {str(ids["P2.JPG"]): "qwen2.5vl 7b"}
    assert alone["P3.JPG"] == {str(ids["P3.JPG"]): "rg_cities1000 1.0"}
    assert alone == {name: card["fact_models"] for name, card in cards.items()}
    # More than one page carries a model, or "complete alone" is a claim about one card.
    assert sum(1 for models in alone.values() if models) > 1

    # 3 - no card holds an entry its own tokens do not point at, on EITHER shape of
    # request. The oracle is the served `tokens_json`, the planner's own document.
    for card in list(whole["items"]) + [
        _fact_page(fact_backed, limit=1, offset=offset)["items"][0]
        for offset in range(whole["total"])
    ]:
        own = {
            str(token["source_id"])
            for token in card["tokens"]
            if token["source_table"] == "inferred_facts"
        }
        assert set(card["fact_models"]) == own, card["old_filename"]

    # And the response no longer carries the merged map at all - the state that had to be
    # reset on a filter change is gone, not merely unused.
    assert "fact_models" not in whole


def test_a_card_whose_token_names_a_row_the_catalog_no_longer_holds_still_renders():
    """M11 Task 5 review, finding F1: `_card`'s `if i in facts`, KeyError half.

    THE GUARD HAS TWO HALVES AND ONLY ONE OF THEM WAS MEASURED. Scope-privacy - a card
    must not carry models belonging to facts its own tokens do not point at - is pinned
    three ways by the test above and by M1714. The other half is not a privacy question at
    all: `facts` is `repo.facts_by_id` over the page's own token ids, so an id a token
    names and the catalog does not hold is simply ABSENT from it, and indexing `facts[i]`
    for that id raises `KeyError` out of `_card` and **500s the whole review page**.

    THAT IS THE RULING `provenance.py` ALREADY WRITES DOWN and nothing asserted of this
    caller: "a damaged row must cost one card its model name rather than 500 the whole
    review page". `test_a_token_pointing_at_a_row_that_is_not_there_costs_one_sentence_not_
    the_page` asserts it of `provenance.fact_models` THE FUNCTION; A17 moved the map onto
    the card, so the layer that can now raise is `_card` THE CALLER, and it had no test.
    Measured: dropping `if i in facts` left 2984 passed + 1 skipped, exit 0 (M1715).

    `pam/` never deletes an `inferred_facts` row, so no command reaches this state and no
    end-to-end fixture can - a hand-edited catalog can, which is the same reason the
    sibling test builds its item by hand. DEGRADING, not giving up: the resolvable token
    keeps its model and the dangling one contributes nothing.
    """
    item = dataclasses.replace(
        _hand_item(1, None),
        tier="medium",
        tokens=[
            {"source_table": "inferred_facts", "source_id": 11, "tier": "medium"},
            {"source_table": "inferred_facts", "source_id": 12, "tier": "medium"},
        ],
    )
    facts = {
        11: repo.FactModel(model_name="rg_cities1000", model_version="1.0", confidence=None)
    }
    # Spelled by hand from spec 8.2's `"<model_name> <model_version>"`, never built by
    # calling `provenance.model_label` - an oracle built from the definition it checks is a
    # survivor waiting to happen.
    assert api._card(item, 1, facts)["fact_models"] == {11: "rg_cities1000 1.0"}


# --- M11 Task 12 Step 9: a `{people}` token gets NO model, ever ------------------------
#
# Spec 8.2 rev 22: "A `{people}` token gets NO entry: its `source_table` is `persons`, a
# person is not a model's output, and a person id colliding with a fact id would name a
# caption model as the source of somebody's child." The collision is BUILT, not hoped for:
# the person id and the fact id are the same integer below, on purpose.

COLLIDING_ID = 11
CAPTION_MODEL = repo.FactModel(model_name="qwen2.5vl", model_version="7b", confidence=None)


def test_a_people_token_whose_id_collides_with_a_fact_on_the_page_gets_no_model():
    """The page's facts hold id 11 (ANOTHER card's caption); this card's only AI-looking id
    is a PERSON 11. `_card` must not hand the person that caption's model."""
    item = dataclasses.replace(
        _hand_item(1, None),
        tokens=[{"source_table": "persons", "source_id": COLLIDING_ID, "tier": "high"}],
    )
    card = api._card(item, 1, {COLLIDING_ID: CAPTION_MODEL})
    assert card["fact_models"] == {}
    assert card["confidence_note"] is None


def test_on_one_card_the_scene_keeps_its_model_and_the_colliding_person_gets_nothing():
    """The same integer on ONE card, as a caption fact and as a person. The map is keyed by
    fact id (spec 8.2), so it holds exactly one entry and it is the SCENE's - which is why
    `app.js` must read it for `inferred_facts` tokens only (the test below pins that)."""
    item = dataclasses.replace(
        _hand_item(1, None),
        tier="medium",
        tokens=[
            {"source_table": "inferred_facts", "source_id": COLLIDING_ID, "tier": "medium"},
            {"source_table": "persons", "source_id": COLLIDING_ID, "tier": "high"},
        ],
    )
    assert provenance.fact_tokens(item.tokens) == [(COLLIDING_ID, "medium")]
    assert api._card(item, 1, {COLLIDING_ID: CAPTION_MODEL})["fact_models"] == {
        COLLIDING_ID: "qwen2.5vl 7b"
    }


def _app_js_code() -> str:
    """`app.js` without its `/* */` comments, so a sentence in a comment cannot satisfy a
    test about code. `app.js` holds no `//` comments and no `/*` inside a string.

    M14 Task 1: the Why? panel's code moved, unchanged, from `app.js` into `pam-trust.js`,
    which is what this reads now."""
    source = (Path(api.__file__).parent / "static" / "pam-trust.js").read_text(encoding="utf-8")
    return re.sub(r"/\*.*?\*/", "", source, flags=re.S)


def test_the_why_panel_names_a_person_and_reads_a_model_for_ai_tokens_only():
    """UX 5's Why? panel, in the one file nothing in this project executes (`m7.md` F4),
    so the contract is pinned on the SOURCE: `SOURCE_WORDS` gains `persons`, and the model
    lookup is guarded by `source_table` - without the guard, the collision above puts the
    caption model beside a person's name on the card that holds both."""
    code = _app_js_code()
    assert 'persons: "a person you named",' in code
    assert re.search(
        r'var model = table === "inferred_facts" \? models\[token\.source_id\] : null;', code
    ), "sourceWords reads a model for a token whose source is not inferred_facts"


# --- M11 Task 12 review, F1: the SERVER's search reads the CONFIGURED floor -----------
#
# M1936, a survivor over 3618 tests: `api.faces_floor` returning 0.0 was green. Every
# people-search test in `test_people_naming.py` calls `repo.plan_rows` with an explicit
# floor, so nothing in the repository read the key through the server - and ruling 2 (a
# search matches exactly the set `{people}` names) is a statement about the SERVER's
# answer. A person whose face is below A11's floor could be found on the review page by a
# name the proposed filename does not carry.
#
# The floor is read at the SERVER, so it is pinned at the server: through the live HTTP
# route, with the two faces planted either side of the shipped 0.4.

BELOW_FLOOR = 0.39
ABOVE_FLOOR = 0.9


def _plant_below_and_above(live: Live, label: str) -> tuple[str, str]:
    """A person with one face under A11's floor and one over it, on two MAIN plan files.

    The suite's own INSERTs, never `repo.insert_face` / `repo.upsert_person`: an oracle
    built from the production writer agrees with it by construction (M8's M922/M924).
    """
    hidden_file, shown_file = "IMG_1.JPG", "IMG_3.JPG"
    conn = sqlite3.connect(live.db_path)
    try:
        conn.row_factory = sqlite3.Row
        person = int(
            conn.execute("INSERT INTO persons (label) VALUES (?)", (label,)).lastrowid or 0
        )
        for name, confidence in ((hidden_file, BELOW_FLOOR), (shown_file, ABOVE_FLOOR)):
            sha = conn.execute(
                "SELECT sha256 FROM files WHERE filename = ?", (name,)
            ).fetchone()["sha256"]
            conn.execute(
                "INSERT INTO face_instances (sha256, bbox_json, embedding, det_confidence,"
                " person_id, model_name, model_version)"
                " VALUES (?, '{}', ?, ?, ?, 'yunet+sface', '1')",
                (sha, b"\x01", confidence, person),
            )
        conn.commit()
    finally:
        conn.close()
    return hidden_file, shown_file


def test_people_search_through_the_server_does_not_find_a_BELOW_FLOOR_face(live):
    """Ruling 2 at the surface that answers the user: `/items?q=` reads `[tiers] faces`."""
    hidden_file, shown_file = _plant_below_and_above(live, "Emma")
    assert names(items(live, q="emma")) == [shown_file]
    assert hidden_file not in names(items(live, q="emma"))


def test_the_review_api_reads_the_faces_floor_out_of_the_archives_config(tmp_path):
    """And it reads the KEY, not the shipped number: `faces_floor` against a config that
    sets a floor no default supplies. A hardcoded 0.4 passes the test above and fails here."""
    (tmp_path / "pam.toml").write_text("[tiers]\nfaces = { floor = 0.77 }\n", encoding="utf-8")
    config = load_config(tmp_path / "pam.toml")
    assert api.faces_floor(SimpleNamespace(config=config)) == 0.77


# THE FLOOR UNDER THIS MODULE'S `fact_models` INVENTORY (M1670, and Tasks 1-4's F5/F1/F3/F1
# before it - this is the fifth consecutive sole-guardian finding on M11). SCOPED
# DELIBERATELY to the provenance family this task owns: registering all of a 2100-line
# module's tests would be an inventory nobody maintains, which is how a floor stops meaning
# anything. Every row is a control measured to be the sole thing standing behind a
# property, so deleting it is a deliberate edit to a registry rather than a silently
# smaller suite. HAND-SPELLED, never derived from `globals()` - an inventory that asks the
# module what it contains passes its own deletion (CLAUDE.md's `repo.ENRICHERS` lesson).
LOAD_BEARING_CONTROLS = {
    "test_each_card_carries_its_own_models_and_a_page_fetched_alone_is_complete": (
        "spec 8.2 rev 22 / A17 itself, and the ONLY thing in the repository that pins "
        "property 2 - a page fetched alone is complete - which is the property the "
        "response-level shape could not express and the amendment was adopted to buy. "
        "Measured: deleting it left 2983 passed + 1 skipped, exit 0 (M1716)"
    ),
    "test_fact_models_is_keyed_by_the_FACT_id_and_never_by_the_file_id": (
        "spec 8.2's 'keyed by fact id, never by file or item id', on a fixture where the "
        "two are deliberately different numbers - the only place that separation is "
        "observable rather than coincidental"
    ),
    "test_fact_models_covers_exactly_the_tokens_whose_source_is_inferred_facts": (
        "the map's DOMAIN over a whole page, read independently of `_card`'s own "
        "comprehension - the second implementation behind the per-card partition"
    ),
    "test_an_item_with_no_ai_token_gets_no_sentence_and_no_entry": (
        "the empty case. A17 is what made `card['fact_models'] == {}` assertable at all, "
        "and it is one of the three kills on the whole-page-map mutation (M1714)"
    ),
    "test_a_token_pointing_at_a_row_that_is_not_there_costs_one_sentence_not_the_page": (
        "`confidence_note`'s `fact is None` guard, which nothing else in the suite "
        "reaches, and `fact_models`' behaviour on a dangling id at the FUNCTION level"
    ),
    "test_a_card_whose_token_names_a_row_the_catalog_no_longer_holds_still_renders": (
        "the same ruling one layer up, at the CALLER A17 moved the map to. Sole guardian "
        "of `_card`'s `if i in facts` KeyError half; its mutation survived a whole green "
        "suite before this test existed (M1715, finding F1)"
    ),
    "test_a_people_token_whose_id_collides_with_a_fact_on_the_page_gets_no_model": (
        "spec 8.2 rev 22's 'A `{people}` token gets NO entry', with the person id and a "
        "page fact id deliberately equal - the privacy half of Task 12 Step 9"
    ),
    "test_on_one_card_the_scene_keeps_its_model_and_the_colliding_person_gets_nothing": (
        "the same collision on ONE card: the map legitimately holds the id, which is the "
        "fact that makes the page-side guard below necessary rather than tidy"
    ),
    "test_the_why_panel_names_a_person_and_reads_a_model_for_ai_tokens_only": (
        "the page-side guard itself, in `app.js`, which nothing executes - the only pin "
        "between a person's chip and a caption model's name"
    ),
    "test_people_search_through_the_server_does_not_find_a_BELOW_FLOOR_face": (
        "ruling 2 (2026-09-17) AT THE SERVER, and the only thing in the repository that "
        "reads the people floor through an HTTP route: every other people-search test "
        "passes `repo.plan_rows` an explicit floor. `api.faces_floor` returning 0.0 was "
        "green over 3618 tests before this test existed (M1936, reviewer's F1)"
    ),
    "test_the_review_api_reads_the_faces_floor_out_of_the_archives_config": (
        "the other half of the same finding: that the floor is the CONFIGURED key and "
        "not the shipped default, which the route-level test above cannot separate"
    ),
    "test_the_served_tokens_are_the_planner_s_document_unedited_and_unreordered": (
        "the reason the test above it may use `card['tokens']` as an oracle at all: "
        "`tokens_json` is served verbatim, so the partition spelled from it is the "
        "planner's document and not a second reading of `_card`'s own comprehension"
    ),
    "test_the_known_items_parameters_are_exactly_design_8_2s": (
        "M14 Task 2 Step 1a: the known set spelled from design 8.2, including U25's "
        "`list_group` - the one thing that pins which parameters the refusal lets through"
    ),
    "test_an_unknown_items_parameter_is_refused_by_name": (
        "M14 Task 2 Step 1a (M13 V3's rule on the read endpoint): deleting the refusal "
        "lets a mistyped filter be silently dropped and the page show a broader set"
    ),
}

assert LOAD_BEARING_CONTROLS, "this module's control list is empty; it protects nothing"


# THE REGISTRY'S OWN FLOOR (M11 Task 12's review, finding F4, measured as M1940).
# Deleting a control's ENTRY from the map above - the test itself untouched - was green:
# the test below asks whether every REGISTERED name is present, which catches a deleted
# TEST and cannot see a deleted REGISTRATION. That is `M1670`'s shape one level up, and
# M1670 is exactly how M10's canary registry stopped covering two tasks' controls. A
# LITERAL count, written here rather than computed, so the map cannot shrink quietly;
# raise it in the same commit that registers a new control, and lower it only with the
# reason in the commit message.
REGISTERED_CONTROLS_FLOOR = 12


def test_the_control_registry_itself_cannot_shrink_quietly():
    """F4: the map's SIZE, which the membership test below cannot see."""
    assert len(LOAD_BEARING_CONTROLS) >= REGISTERED_CONTROLS_FLOOR, (
        "a registration has been removed from LOAD_BEARING_CONTROLS. The entry is what"
        " makes deleting the test a deliberate act; without it the floor is a list of"
        " whatever survived."
    )


def test_no_fact_models_control_in_this_file_can_be_deleted_without_saying_so():
    """The floor under this module's provenance inventory (reviewer's F2, on M1670)."""
    present = {name for name in globals() if name.startswith("test_")}
    missing = sorted(set(LOAD_BEARING_CONTROLS) - present)
    assert missing == [], (
        f"a load-bearing control has been deleted from {Path(__file__).name}:"
        f" {missing}. Each is why another assertion here means anything"
        f" ({[LOAD_BEARING_CONTROLS[m] for m in missing]})."
    )


# The three cross-module anchors that stood here - under `tests/test_webui_faces.py`,
# `test_webui_faces_manage.py` and `test_webui_faces_split.py` (M1836, M1878, M1879) - were
# absorbed into `tests/census.py` at M12 Task 1 (m11.md condition C4): one hand-written
# inventory outside every guarded module, checked at collection by `conftest.py`.


# --- M14 Task 2 Step 1a (M13 V3 carried to the read endpoint) ---------------------------
#
# Design 8.2's `/plan/{id}/items?group=&tier=&status=&attention=&q=&sort=&offset=&limit=`
# plus U25's `list_group`, SPELLED FROM THE SPEC and never read from production's
# `ITEM_QUERY_PARAMETERS` (an oracle built from the definition it checks).
SPEC_ITEM_QUERY_PARAMETERS = (
    "group",
    "tier",
    "status",
    "attention",
    "q",
    "sort",
    "offset",
    "limit",
    "list_group",
)
assert SPEC_ITEM_QUERY_PARAMETERS, "the spelled list of 8.2's items parameters is empty"


def test_the_known_items_parameters_are_exactly_design_8_2s():
    assert set(api.ITEM_QUERY_PARAMETERS) == set(SPEC_ITEM_QUERY_PARAMETERS)


def test_an_unknown_items_parameter_is_refused_by_name(live):
    """A mistyped filter must not silently widen what the page shows: a 400 naming it."""
    path = f"/plan/{live.main_plan}/items"
    for bad in ("colour", "zzz_unknown", "Tier"):
        reply = get(live, path, **{bad: "x"})
        assert reply.status == 400, reply.body
        assert reply.body["error"] == f'{bad}: "{bad}" is not something pam can filter by.'
    # several unknown parameters: the first in sorted order is named
    reply = get(live, path, zz="1", aa="2")
    assert reply.status == 400
    assert reply.body["error"].startswith('aa: "aa"')
    # positive control: every spelled parameter, given legally, is accepted
    legal = {"group": "", "tier": "", "status": "", "attention": "", "q": "", "sort": ""}
    reply = get(live, path, **legal, offset="0", limit="5", list_group="undated")
    assert reply.status == 200, reply.body
