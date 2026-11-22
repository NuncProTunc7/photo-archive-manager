"""M14 Task 3 slice 3a: the selection's resolver and its two routes (U3, U17, U18).

EVERY EXPECTATION IS SPELLED FROM U17's TEXT, never read back out of `webui/selection.py`:
the known field sets below are literals copied from U17's wire format, the resolution order
is U17's sentence, and every row assertion reads `plan_items` with the suite's OWN SQL.

"WATCH THE DANGEROUS CALL ITSELF" (CLAUDE.md, M697). A refusal that returns the right status
after writing and rolling back looks exactly like one that never wrote, so every refusal here
is checked three ways: the status, a spy on `_write_decisions` (the one write), and the
request connection's `total_changes`, which counts a change even when it is rolled back.

TIER IS UNIT-UNIFORM IN WHAT THE PLANNER WRITES, so the cases that need a low item - and the
one that needs a unit whose members' tiers DIFFER (T3 mutation 3) - set `plan_items.tier` by
hand. Production never makes the mixed unit; the count's rule must hold over it anyway
(CLAUDE.md, M993: build by hand the case the real pipeline never produces).
"""

import contextlib
import json
import logging
import sqlite3
import threading
from http.client import HTTPConnection
from typing import Any

import pytest
from archives import FAKE_COMMAND, put

from pam.catalog import repo
from pam.naming.planner import run_plan
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan
from pam.webui import api, selection
from pam.webui.api import ApiError, ApiRequest
from pam.webui.server import ReviewServer

# --- U17's wire format, spelled from the amendment's text (never imported) ----------------

# `POST /plan/{id}/selection {selection}` and `.../decide-selection {selection, action,
# include_low?, expected_low?}`; `selection = {view: {tier?, status?, attention?, q?}, ids?,
# groups?, all?, except_ids?}`.
U17_PREVIEW_BODY = ("selection",)
U17_DECIDE_BODY = ("selection", "action", "include_low", "expected_low")
U17_SELECTION = ("view", "ids", "groups", "all", "except_ids")
# `group` is NOT in the amendment's text: it is the controller's M14 Task 3 ruling (slice 3d) -
# "the filter the user was looking at" includes the date tree's month, year, event or undated
# entry - pending the design model's ratification at the M14 close. Spelled from that ruling.
U17_VIEW = ("tier", "status", "attention", "q", "group")
U17_PREVIEW_FIELDS = {"plan_id", "items", "units", "low", "overridden", "attention"}

# Names a page could plausibly send that U17 does not carry, each at the level it would be
# mistyped at. `sort`/`offset` are `items`', not a view's; `item_ids` is `decide`'s;
# `accept_all` is `decide-filter`'s branch.
UNKNOWN_FIELDS = [
    ("selection", "body", "item_ids"),
    ("decide-selection", "body", "item_ids"),
    ("decide-selection", "body", "accept_all"),
    ("selection", "body", "action"),
    ("selection", "selection", "tier"),
    ("decide-selection", "selection", "id"),
    ("selection", "view", "offset"),
    ("decide-selection", "view", "sort"),
    ("decide-selection", "view", "list_group"),
]
assert UNKNOWN_FIELDS and U17_SELECTION and U17_VIEW and U17_DECIDE_BODY


# --- the archive ------------------------------------------------------------------------


@pytest.fixture
def served(tmp_path):
    """IMG_1's HEIC + AAE unit (2019), IMG_2 and IMG_3 an hour apart (2020), IMG_4 (2021).

    A server that is not listening: its handlers are called directly with the server's own
    connection factory. Every item starts `high`; a test lowers a tier by hand.
    """
    root = tmp_path / "photos"
    root.mkdir()
    put(root, "IMG_1.HEIC", date="2019:07:04 18:23:11")
    put(root, "IMG_1.AAE", date="2019:07:04 18:23:11")
    put(root, "IMG_2.JPG", date="2020:08:05 09:15:00")
    put(root, "IMG_3.JPG", date="2020:08:05 10:15:00")
    put(root, "IMG_4.JPG", date="2021:09:06 11:02:03")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    summary = run_plan(root)
    assert summary.plan_id is not None
    ws = workspace_if_exists(root)
    assert ws is not None
    logger = logging.getLogger("pam.tests.webui_selection")
    logger.propagate = False
    server = ReviewServer(workspace=ws, plan_id=summary.plan_id, logger=logger)
    db = sqlite3.connect(ws.db_path)
    db.execute("UPDATE plan_items SET tier = 'high'")
    db.commit()
    db.close()
    yield server, summary.plan_id, ws.db_path
    server.server_close()


def _ids(db_path) -> dict[str, int]:
    """`old_rel_path` -> plan item id, by the suite's own SQL."""
    db = sqlite3.connect(db_path)
    try:
        return {
            str(p): int(i) for i, p in db.execute("SELECT id, old_rel_path FROM plan_items")
        }
    finally:
        db.close()


def _statuses(db_path) -> dict[str, str]:
    db = sqlite3.connect(db_path)
    try:
        rows = db.execute("SELECT old_rel_path, status FROM plan_items")
        return {str(p): str(s) for p, s in rows}
    finally:
        db.close()


def _set_tier(db_path, rel_path: str, tier: str) -> None:
    db = sqlite3.connect(db_path)
    try:
        db.execute("UPDATE plan_items SET tier = ? WHERE old_rel_path = ?", (tier, rel_path))
        db.commit()
    finally:
        db.close()


def _group_of(server, plan_id, rel_path: str) -> str:
    """The U25 list-group key of one file's unit: its event, spelled `e<id>`."""
    with server.connection() as conn:
        rows = repo.plan_rows(conn, plan_id, floor=api.faces_floor(server))
    row = next(r for r in rows if r.old_rel_path == rel_path)
    assert row.event_id is not None, f"precondition: {rel_path} is in an event"
    return f"e{row.event_id}"


@pytest.fixture
def watch(served, monkeypatch):
    """Spies on the one write and on every request connection's `total_changes`."""
    server, _plan_id, _db = served
    calls: list[tuple[list[int], str]] = []
    changes: list[int] = []
    real_write = selection._write_decisions
    real_connection = server.connection

    def spy(conn, ids, action):
        calls.append((list(ids), action))
        return real_write(conn, ids, action)

    @contextlib.contextmanager
    def watched():
        with real_connection() as conn:
            try:
                yield conn
            finally:
                changes.append(conn.total_changes)

    monkeypatch.setattr(selection, "_write_decisions", spy)
    monkeypatch.setattr(server, "connection", watched)
    return calls, changes


def post(handler, server, plan_id, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Call a route; a refusal comes back as `(status, {error, **extra})`, never raised."""
    request = ApiRequest(params={"id": str(plan_id)}, query={}, body=body)
    try:
        return 200, handler(server, request)
    except ApiError as refusal:
        return refusal.status, {"error": refusal.message, **refusal.extra}


def preview(server, plan_id, sel):
    return post(selection.preview_selection, server, plan_id, {"selection": sel})


def decide(server, plan_id, sel, action, **extra):
    body = {"selection": sel, "action": action, **extra}
    return post(selection.decide_selection, server, plan_id, body)


def assert_nothing_written(watch, db_path, before):
    calls, changes = watch
    assert calls == [], "the write was called"
    assert all(n == 0 for n in changes), f"a connection changed rows: {changes}"
    assert _statuses(db_path) == before


# --- the preview -------------------------------------------------------------------------


def test_the_preview_is_read_only_and_spells_u17s_fields(served, watch):
    """`POST /plan/{id}/selection` is a READ: no write call, no changed row, U17's fields."""
    server, plan_id, db = served
    before = _statuses(db)
    status, reply = preview(server, plan_id, {"view": {}, "all": True})
    assert status == 200, reply
    assert set(reply) == U17_PREVIEW_FIELDS
    assert (reply["items"], reply["units"], reply["low"], reply["overridden"]) == (5, 4, 0, [])
    _calls, changes = watch
    assert changes, "the preview opened no connection - the spy saw nothing"
    assert_nothing_written(watch, db, before)


def test_a_groups_box_selects_the_whole_group_including_rows_the_page_never_loaded(served):
    """A group key, not enumerated ids: both of the 2020 group's rows, though none was named."""
    server, plan_id, db = served
    group = _group_of(server, plan_id, "IMG_2.JPG")
    assert _group_of(server, plan_id, "IMG_3.JPG") == group, "precondition: one group"
    status, reply = decide(server, plan_id, {"view": {}, "groups": [group]}, "skip")
    assert status == 200, reply
    after = _statuses(db)
    assert (after["IMG_2.JPG"], after["IMG_3.JPG"]) == ("skipped", "skipped")
    assert {after["IMG_1.HEIC"], after["IMG_1.AAE"], after["IMG_4.JPG"]} == {"proposed"}


def test_a_groups_box_in_the_needs_a_look_view_selects_only_that_views_rows(served):
    """T3 mutation 5: `select_items(view) ∩ group`. IMG_3 is low (so in `attention`), IMG_2
    is not; the `Needs a look` tab's box on their group takes IMG_3 alone."""
    server, plan_id, db = served
    _set_tier(db, "IMG_3.JPG", "low")
    group = _group_of(server, plan_id, "IMG_2.JPG")
    sel = {"view": {"attention": True}, "groups": [group]}
    status, reply = preview(server, plan_id, sel)
    assert status == 200, reply
    assert (reply["items"], reply["low"], reply["attention"]) == (1, 1, 1)
    status, reply = decide(server, plan_id, sel, "skip")
    assert status == 200, reply
    after = _statuses(db)
    assert (after["IMG_2.JPG"], after["IMG_3.JPG"]) == ("proposed", "skipped")


def test_all_is_the_view_and_ids_are_taken_as_named_whatever_the_view(served):
    """`all` = `select_items(view)`; a ticked id outside the view is still meant."""
    server, plan_id, db = served
    _set_tier(db, "IMG_4.JPG", "low")
    ids = _ids(db)
    sel = {"view": {"tier": "high"}, "all": True}
    status, reply = preview(server, plan_id, sel)
    assert (status, reply["items"], reply["low"]) == (200, 4, 0)
    status, reply = preview(server, plan_id, {**sel, "ids": [ids["IMG_4.JPG"]]})
    assert (status, reply["items"], reply["low"]) == (200, 5, 1)


def test_an_except_id_that_would_split_a_unit_is_widened_back_and_reported(served):
    """T3 mutation 4: resolve, subtract `except_ids`, THEN widen. Unticking the AAE inside
    its ticked group leaves the HEIC selected, so the unit stays whole and `overridden` names
    the AAE; unticking a lone row removes it and overrides nothing."""
    server, plan_id, db = served
    ids = _ids(db)
    unit_group = _group_of(server, plan_id, "IMG_1.HEIC")
    sel = {"view": {}, "groups": [unit_group], "except_ids": [ids["IMG_1.AAE"]]}
    status, reply = preview(server, plan_id, sel)
    assert status == 200, reply
    assert (reply["items"], reply["units"], reply["overridden"]) == (2, 1, [ids["IMG_1.AAE"]])
    status, reply = decide(server, plan_id, sel, "skip")
    assert status == 200, reply
    assert reply["overridden"] == [ids["IMG_1.AAE"]]
    after = _statuses(db)
    assert (after["IMG_1.HEIC"], after["IMG_1.AAE"]) == ("skipped", "skipped")

    lone = _group_of(server, plan_id, "IMG_2.JPG")
    sel = {"view": {}, "groups": [lone], "except_ids": [ids["IMG_2.JPG"]]}
    status, reply = preview(server, plan_id, sel)
    assert (status, reply["items"], reply["overridden"]) == (200, 1, [])


def test_low_counts_the_widened_set_not_the_rows_the_user_named(served, watch):
    """T3 mutation 3. A unit whose members' tiers DIFFER (hand-built: the HEIC `high`, its
    AAE `low`); the user names only the HEIC. The widening adds the AAE, so the count is 1,
    an unacknowledged approve is refused, and an acknowledged one approves both."""
    server, plan_id, db = served
    _set_tier(db, "IMG_1.AAE", "low")
    ids = _ids(db)
    sel = {"view": {}, "ids": [ids["IMG_1.HEIC"]]}
    status, reply = preview(server, plan_id, sel)
    assert (status, reply["items"], reply["low"]) == (200, 2, 1)

    before = _statuses(db)
    status, reply = decide(server, plan_id, sel, "approve")
    assert status == 409, reply
    assert reply["selection"]["low"] == 1
    assert_nothing_written(watch, db, before)

    status, reply = decide(server, plan_id, sel, "approve", include_low=True, expected_low=1)
    assert status == 200, reply
    assert reply["low_included"] == 1
    after = _statuses(db)
    assert (after["IMG_1.HEIC"], after["IMG_1.AAE"]) == ("approved", "approved")


# --- decide-selection: the low acknowledgement --------------------------------------------


def _two_low(served):
    server, plan_id, db = served
    _set_tier(db, "IMG_3.JPG", "low")
    _set_tier(db, "IMG_4.JPG", "low")
    return server, plan_id, db, {"view": {}, "all": True}


# Task 3 review R1 F5 (M2921): an EMPTY parametrize is skipped, not failed, so every table
# that carries a T3 kill is a constant with its size asserted at collection.
STALE_COUNTS = [0, 1, 3]
INCLUDE_LOW_CASES = [
    ({"expected_low": 2}, 409),  # absent
    ({"include_low": False, "expected_low": 2}, 409),
    ({"include_low": 1, "expected_low": 2}, 400),  # 1 is not true
    ({"include_low": "true", "expected_low": 2}, 400),
    ({"include_low": None, "expected_low": 2}, 400),
]
EXPECTED_LOW_BAD = [True, "2", 2.0, -1, None]
NOT_APPROVE = ["skip", "reset"]
LOW_FIELDS = ["include_low", "expected_low"]
assert (len(STALE_COUNTS), len(INCLUDE_LOW_CASES), len(EXPECTED_LOW_BAD)) == (3, 5, 5)
assert (len(NOT_APPROVE), len(LOW_FIELDS)) == (2, 2)


@pytest.mark.parametrize("stale", STALE_COUNTS)
def test_a_stale_low_count_is_refused_and_writes_nothing(served, watch, stale):
    """T3 mutations 1, 2 (`include_low` checked, `expected_low` not) and 8 (write before the
    refusal). Two low names; the page says it showed `stale`. 409, the fresh preview rides
    along, and the write is never called."""
    server, plan_id, db, sel = _two_low(served)
    before = _statuses(db)
    status, reply = decide(
        server, plan_id, sel, "approve", include_low=True, expected_low=stale
    )
    assert status == 409, reply
    assert reply["selection"]["low"] == 2
    assert set(reply["selection"]) == U17_PREVIEW_FIELDS
    assert "error" in reply
    assert_nothing_written(watch, db, before)


@pytest.mark.parametrize(("extra", "code"), INCLUDE_LOW_CASES)
def test_include_low_must_be_the_literal_true(served, watch, extra, code):
    """T3 mutation 2's other operand (`expected_low` checked, `include_low` not)."""
    server, plan_id, db, sel = _two_low(served)
    before = _statuses(db)
    status, reply = decide(server, plan_id, sel, "approve", **extra)
    assert status == code, reply
    assert_nothing_written(watch, db, before)


@pytest.mark.parametrize("bad", EXPECTED_LOW_BAD)
def test_expected_low_must_be_a_literal_whole_number(served, watch, bad):
    """`True` is not `1`: with ONE low name, `expected_low: true` is still refused."""
    server, plan_id, db = served
    _set_tier(db, "IMG_4.JPG", "low")
    before = _statuses(db)
    sel = {"view": {}, "all": True}
    status, reply = decide(server, plan_id, sel, "approve", include_low=True, expected_low=bad)
    assert status in (400, 409), reply
    if bad is not None:
        assert status == 400 and reply["error"].startswith("expected_low:")
    assert_nothing_written(watch, db, before)


def test_the_acknowledged_count_approves_every_low_name_and_says_how_many(served):
    server, plan_id, db, sel = _two_low(served)
    status, reply = decide(server, plan_id, sel, "approve", include_low=True, expected_low=2)
    assert status == 200, reply
    assert (reply["decided"], reply["low_included"], reply["overridden"]) == (5, 2, [])
    assert set(_statuses(db).values()) == {"approved"}
    assert reply["counts"]["approved"] == 5


@pytest.mark.parametrize("action", NOT_APPROVE)
def test_skip_needs_no_low_acknowledgement(served, action):
    """T3 mutation 7: U17, "`skip` and `reset` need no `include_low`"."""
    server, plan_id, db, sel = _two_low(served)
    status, reply = decide(server, plan_id, sel, action)
    assert status == 200, reply
    target = {"skip": "skipped", "reset": "proposed"}[action]
    assert set(_statuses(db).values()) == {target}
    assert reply["low_included"] == 0


@pytest.mark.parametrize("action", NOT_APPROVE)
@pytest.mark.parametrize("field", LOW_FIELDS)
def test_skip_and_reset_refuse_a_low_acknowledgement_they_cannot_use(
    served, watch, action, field
):
    """The slice's ruling: REFUSED, not ignored - a field that cannot mean anything here is
    the page asking for something pam does not do."""
    server, plan_id, db, sel = _two_low(served)
    before = _statuses(db)
    value: object = True if field == "include_low" else 2
    status, reply = decide(server, plan_id, sel, action, **{field: value})
    assert status == 400 and reply["error"].startswith(f"{field}:"), reply
    assert_nothing_written(watch, db, before)


def test_a_selection_with_no_low_name_needs_no_acknowledgement_but_refuses_a_stale_one(
    served, watch
):
    server, plan_id, db = served
    sel = {"view": {}, "all": True}
    before = _statuses(db)
    status, reply = decide(server, plan_id, sel, "approve", include_low=True, expected_low=1)
    assert status == 409 and reply["selection"]["low"] == 0, reply
    assert_nothing_written(watch, db, before)
    status, reply = decide(server, plan_id, sel, "approve")
    assert status == 200 and reply["low_included"] == 0, reply
    assert set(_statuses(db).values()) == {"approved"}


# --- validation: U17's wire format, and nothing else ---------------------------------------


@pytest.mark.parametrize(("route", "level", "name"), UNKNOWN_FIELDS)
def test_a_field_outside_u17s_wire_format_is_refused_and_writes_nothing(
    served, watch, route, level, name
):
    """Rev 1.1 (M13 V3) on both routes: a 400 in Task 0D's copy, at every level of the body."""
    server, plan_id, db = served
    before = _statuses(db)
    sel: dict[str, Any] = {"view": {}, "all": True}
    body: dict[str, Any] = {"selection": sel}
    if route == "decide-selection":
        body["action"] = "skip"
    target = {"body": body, "selection": sel, "view": sel["view"]}[level]
    target[name] = "x"
    handler = {
        "selection": selection.preview_selection,
        "decide-selection": selection.decide_selection,
    }[route]
    status, reply = post(handler, server, plan_id, body)
    assert status == 400, reply
    assert reply["error"] == f'{name}: "{name}" is not something pam can filter by.'
    assert_nothing_written(watch, db, before)


def test_every_field_u17_names_is_accepted_at_its_level(served):
    """The negative control: the known sets, spelled from U17, are each accepted - so the
    refusal above is about the NAME, not about any extra field."""
    server, plan_id, db = served
    ids = _ids(db)
    group = _group_of(server, plan_id, "IMG_2.JPG")
    view = {
        "tier": "high",
        "status": "proposed",
        "attention": False,
        "q": "IMG",
        "group": "2020",
    }
    assert tuple(view) == U17_VIEW
    sel = {
        "view": view,
        "ids": [ids["IMG_4.JPG"]],
        "groups": [group],
        "all": True,
        "except_ids": [ids["IMG_2.JPG"]],
    }
    assert tuple(sel) == U17_SELECTION
    status, reply = preview(server, plan_id, sel)
    assert status == 200, reply
    body = {"selection": sel, "action": "approve", "include_low": False, "expected_low": 0}
    assert tuple(body) == U17_DECIDE_BODY
    assert tuple({"selection": sel}) == U17_PREVIEW_BODY
    status, reply = post(selection.decide_selection, server, plan_id, body)
    assert status == 200, reply


BAD_SELECTIONS = [
    ("missing selection", None, "selection:"),
    ("selection not an object", [1], "selection:"),
    ("missing view", {"all": True}, "view:"),
    ("view not an object", {"view": "high", "all": True}, "view:"),
    ("view tier", {"view": {"tier": "nonsense"}, "all": True}, "tier:"),
    ("view attention", {"view": {"attention": "yes"}, "all": True}, "attention:"),
    ("ids not a list", {"view": {}, "ids": 3}, "ids:"),
    ("ids true", {"view": {}, "ids": [True]}, "ids:"),
    ("ids text", {"view": {}, "ids": ["1"]}, "ids:"),
    ("ids stranger", {"view": {}, "ids": [999999]}, "ids:"),
    ("except stranger", {"view": {}, "all": True, "except_ids": [999999]}, "except_ids:"),
    ("groups not a list", {"view": {}, "groups": "undated"}, "groups:"),
    ("groups malformed", {"view": {}, "groups": ["2019-07"]}, "groups:"),
    ("groups not a key", {"view": {}, "groups": [7]}, "groups:"),
    ("groups stranger", {"view": {}, "groups": ["e999999"]}, "groups:"),
    ("all false", {"view": {}, "all": False, "ids": []}, "all:"),
    ("all one", {"view": {}, "all": 1}, "all:"),
    ("names nothing", {"view": {}}, "selection:"),
    ("names nothing, empty lists", {"view": {}, "ids": [], "groups": []}, "selection:"),
]
assert BAD_SELECTIONS


@pytest.mark.parametrize(
    ("what", "sel", "prefix"), BAD_SELECTIONS, ids=[b[0] for b in BAD_SELECTIONS]
)
@pytest.mark.parametrize("route", ["selection", "decide-selection"])
def test_a_malformed_selection_is_a_400_naming_its_field_and_writes_nothing(
    served, watch, route, what, sel, prefix
):
    server, plan_id, db = served
    before = _statuses(db)
    body: dict[str, Any] = {} if sel is None else {"selection": sel}
    if route == "decide-selection":
        body["action"] = "approve"
        body["include_low"] = True
        body["expected_low"] = 0
    handler = (
        selection.preview_selection if route == "selection" else selection.decide_selection
    )
    status, reply = post(handler, server, plan_id, body)
    assert status == 400, (what, reply)
    assert reply["error"].startswith(prefix), (what, reply)
    assert_nothing_written(watch, db, before)


# --- the wire ------------------------------------------------------------------------------


def _http(server, path, body):
    payload = json.dumps(body).encode("utf-8")
    conn = HTTPConnection(server.host, server.port, timeout=30)
    try:
        conn.request(
            "POST",
            f"/t/{server.token}/api{path}",
            body=payload,
            headers={"Content-Type": "application/json", "Content-Length": str(len(payload))},
        )
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


def test_both_routes_are_served_under_the_token_and_a_409_carries_the_fresh_preview(served):
    """U18: both routes under the token prefix; U17: the stale-count 409's body is
    `{error, selection}` over the real wire, not only in the handler."""
    server, plan_id, db, sel = _two_low(served)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, reply = _http(server, f"/plan/{plan_id}/selection", {"selection": sel})
        assert status == 200 and reply["low"] == 2, reply
        body = {"selection": sel, "action": "approve", "include_low": True, "expected_low": 1}
        status, reply = _http(server, f"/plan/{plan_id}/decide-selection", body)
        assert status == 409, reply
        assert set(reply) == {"error", "selection"}
        assert reply["selection"]["low"] == 2
        body["expected_low"] = 2
        status, reply = _http(server, f"/plan/{plan_id}/decide-selection", body)
        assert status == 200 and reply["low_included"] == 2, reply
    finally:
        server.shutdown()
        thread.join(timeout=30)
    assert set(_statuses(db).values()) == {"approved"}


# --- the date tree's `group` (M14 Task 3 slice 3d, the controller's ruling) ---------------


def test_select_all_under_a_month_filter_covers_exactly_that_months_units(served, watch):
    """Under the date tree's `2020-08`, the list shows IMG_2 and IMG_3 and nothing else, so
    `Select all` - previewed and approved - is exactly those two. Spelled from the fixture:
    IMG_1's unit is 2019-07 and IMG_4 is 2021-09."""
    server, plan_id, db = served
    sel = {"view": {"group": "2020-08"}, "all": True}
    status, reply = preview(server, plan_id, sel)
    assert status == 200, reply
    assert (reply["items"], reply["units"], reply["low"]) == (2, 2, 0)
    status, reply = decide(server, plan_id, sel, "approve", expected_low=0)
    assert status == 200, reply
    approved = {p for p, s in _statuses(db).items() if s == "approved"}
    assert approved == {"IMG_2.JPG", "IMG_3.JPG"}


def test_a_view_group_is_validated_like_the_items_reader(served, watch):
    """A malformed date-tree group is a 400 naming `group`, as `GET items` answers it."""
    server, plan_id, db = served
    before = _statuses(db)
    status, reply = decide(
        server, plan_id, {"view": {"group": "2020-13"}, "all": True}, "approve"
    )
    assert status == 400, reply
    assert reply["error"].startswith("group:"), reply
    # Validated, not refused as an unknown name (M2903 removed `group` from the view's names
    # and this test still passed, because V3's refusal starts `group:` too - F4).
    assert "is not something pam can filter by" not in reply["error"], reply
    assert_nothing_written(watch, db, before)
