"""M14 Task 3 slice 3b: the list's one-tap shortcuts (OD-1) and the view-scoped `/list`.

EVERY EXPECTATION IS SPELLED FROM THE RULINGS, never read back out of production:
  - OD-1 (ADOPTED 2026-10-29): the shortcuts hold back the WHOLE needs-a-look set (Low,
    undated, truncated, OCR-dated), so "the other N" and the needs-a-look count add up;
  - the controller's 3b rulings: `/list` takes the view (`tier`, `status`, `attention`, `q`)
    and refuses anything else in Task 0D's copy; the shortcut writes through `decide-filter`
    as `{"action": "approve", "shortcut": true, "list_group"?, ...view}`; it works on WHOLE
    UNITS of the whole plan; its N is what its write takes;
  - M13 (`2026-10-08-ht-m12-f1-approval-amendment.md`): `Accept all` and `--all` hold back
    Low and nothing else - unchanged by OD-1.

The fixture is IMG_1's HEIC + AAE unit (2019), IMG_2 and IMG_3 an hour apart (2020), IMG_4
(2021). Every item starts `high`. The needs-a-look states a real scan of this fixture never
produces - an OCR-dated `medium` name, a unit whose members disagree about attention - are
BUILT BY HAND in `plan_items` (CLAUDE.md M993: build the case the pipeline never makes).
Every refusal watches the write itself: a spy on `decide_api._write_decisions`.
"""

import json
import logging
import sqlite3
from typing import Any

import pytest
from archives import FAKE_COMMAND, put

from pam.naming.planner import run_plan
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan
from pam.webui import api, decide_api, nav
from pam.webui.api import ApiError, ApiRequest
from pam.webui.server import ReviewServer

FILES = ["IMG_1.HEIC", "IMG_1.AAE", "IMG_2.JPG", "IMG_3.JPG", "IMG_4.JPG"]


@pytest.fixture
def served(tmp_path):
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
    logger = logging.getLogger("pam.tests.webui_shortcut")
    logger.propagate = False
    server = ReviewServer(workspace=ws, plan_id=summary.plan_id, logger=logger)
    _sql(ws.db_path, "UPDATE plan_items SET tier = 'high'")
    yield server, summary.plan_id, ws.db_path
    server.server_close()


def _sql(db_path, query: str, args: tuple[Any, ...] = ()) -> None:
    db = sqlite3.connect(db_path)
    try:
        db.execute(query, args)
        db.commit()
    finally:
        db.close()


def _statuses(db_path) -> dict[str, str]:
    db = sqlite3.connect(db_path)
    try:
        return {
            str(p): str(s) for p, s in db.execute("SELECT old_rel_path, status FROM plan_items")
        }
    finally:
        db.close()


def _approved(db_path) -> set[str]:
    return {name for name, status in _statuses(db_path).items() if status == "approved"}


def _ocr_dated_medium(db_path, rel_path: str) -> None:
    """By hand: the date token now comes from OCR (`inferred_facts`) and the tier is `medium`
    - design 8.2 rev 27 (P12): in the needs-a-look set, and NOT low."""
    db = sqlite3.connect(db_path)
    try:
        (raw,) = db.execute(
            "SELECT tokens_json FROM plan_items WHERE old_rel_path = ?", (rel_path,)
        ).fetchone()
        tokens = json.loads(raw)
        dates = [t for t in tokens if isinstance(t, dict) and t.get("resolved_from") == "date"]
        assert dates, f"precondition: {rel_path} has a date token"
        for token in dates:
            token["source_table"] = "inferred_facts"
        db.execute(
            "UPDATE plan_items SET tokens_json = ?, tier = 'medium' WHERE old_rel_path = ?",
            (json.dumps(tokens), rel_path),
        )
        db.commit()
    finally:
        db.close()


def _list(server, plan_id, **query: str) -> dict[str, Any]:
    request = ApiRequest(params={"id": str(plan_id)}, query={k: [v] for k, v in query.items()})
    return nav.plan_list(server, request)


def _group_with(reply: dict[str, Any], server, plan_id, rel_path: str) -> dict[str, Any]:
    """The `/list` group holding `rel_path`, found through `/items?list_group=` (the page's
    own route), never through `listing`'s key rule."""
    for group in reply["groups"]:
        request = ApiRequest(
            params={"id": str(plan_id)}, query={"list_group": [group["key"]], "limit": ["200"]}
        )
        names = {card["old_rel_path"] for card in api.plan_items(server, request)["items"]}
        if rel_path in names:
            return group
    raise AssertionError(f"no /list group holds {rel_path}")


def _post(server, plan_id, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = ApiRequest(params={"id": str(plan_id)}, query={}, body=body)
    try:
        return 200, decide_api.decide_filter(server, request)
    except ApiError as refusal:
        return refusal.status, {"error": refusal.message}


@pytest.fixture
def writes(monkeypatch):
    calls: list[tuple[list[int], str]] = []
    real = decide_api._write_decisions

    def spy(conn, ids, action):
        calls.append((list(ids), action))
        return real(conn, ids, action)

    monkeypatch.setattr(decide_api, "_write_decisions", spy)
    return calls


SHORTCUT = {"action": "approve", "shortcut": True}


# --- OD-1: the callout's two numbers add up --------------------------------------------


def test_the_other_N_and_the_needs_a_look_count_add_up(served, writes):
    """T3 mutation 9 (the shortcut calls M13's `accept_all` with no `also_hold`): IMG_2 is
    an OCR-dated `medium` name - in the needs-a-look set, NOT low - and IMG_4 is low. Spelled
    by hand: 5 undecided, 2 need a look (IMG_2, IMG_4), so "the other N" is 3 (IMG_1's two
    files and IMG_3). Under the mutation IMG_2 joins the shortcut and 4 + 2 != 5."""
    server, plan_id, db = served
    _ocr_dated_medium(db, "IMG_2.JPG")
    _sql(db, "UPDATE plan_items SET tier = 'low' WHERE old_rel_path = 'IMG_4.JPG'")
    reply = _list(server, plan_id)
    assert (reply["undecided"], reply["attention_undecided"], reply["shortcut_count"]) == (
        5,
        2,
        3,
    )
    assert reply["shortcut_count"] + reply["attention_undecided"] == reply["undecided"]
    pair = _group_with(reply, server, plan_id, "IMG_2.JPG")
    assert (pair["undecided"], pair["attention_undecided"], pair["shortcut_count"]) == (2, 1, 1)
    for group in reply["groups"]:
        assert group["shortcut_count"] + group["attention_undecided"] == group["undecided"]

    status, result = _post(server, plan_id, SHORTCUT)
    assert status == 200, result
    assert _approved(db) == {"IMG_1.HEIC", "IMG_1.AAE", "IMG_3.JPG"}
    assert result["decided"] == 3 == reply["shortcut_count"]
    # OD-1's counting choice: the low unit counts in `excluded_low`, the OCR one in `held`.
    assert (result["excluded_low"], result["held"]) == (1, 1)
    assert writes and all(action == "approve" for _, action in writes)


def test_a_groups_shortcut_writes_exactly_the_N_its_group_showed(served, writes):
    """A group's `Approve N`: the write takes that group's units and no other's."""
    server, plan_id, db = served
    reply = _list(server, plan_id)
    pair = _group_with(reply, server, plan_id, "IMG_3.JPG")
    assert pair["shortcut_count"] == 2, "precondition: IMG_2 and IMG_3 share a group"
    status, result = _post(server, plan_id, {**SHORTCUT, "list_group": pair["key"]})
    assert status == 200, result
    assert _approved(db) == {"IMG_2.JPG", "IMG_3.JPG"}
    assert result["decided"] == pair["shortcut_count"]


def test_an_all_low_group_has_no_shortcut_and_says_so(served):
    """UX 5 (U3): a group made only of low-trust names shows `pam isn't sure of these` -
    `all_low: true`, and its count is 0."""
    server, plan_id, db = served
    _sql(db, "UPDATE plan_items SET tier = 'low' WHERE old_rel_path = 'IMG_4.JPG'")
    group = _group_with(_list(server, plan_id), server, plan_id, "IMG_4.JPG")
    assert (group["all_low"], group["shortcut_count"]) == (True, 0)


# --- whole units of the whole plan (vision S3) -----------------------------------------


def test_a_search_matching_one_member_of_a_unit_approves_both_members_or_neither(served):
    """`q=aae` selects only IMG_1.AAE; its unit is IMG_1.HEIC + IMG_1.AAE. The shortcut's
    candidates are the WHOLE unit from the whole plan, so it approves both - and when the
    HEIC alone needs a look (built by hand: production makes attention unit-uniform) it
    approves neither. Never one: `accept_all` over the view's rows alone would."""
    server, plan_id, db = served
    reply = _list(server, plan_id, q="aae")
    assert reply["shortcut_count"] == 2
    status, result = _post(server, plan_id, {**SHORTCUT, "q": "aae"})
    assert status == 200, result
    assert _approved(db) == {"IMG_1.HEIC", "IMG_1.AAE"}
    assert result["decided"] == 2

    _sql(db, "UPDATE plan_items SET status = 'proposed'")
    _ocr_dated_medium(db, "IMG_1.HEIC")
    assert _list(server, plan_id, q="aae")["shortcut_count"] == 0
    status, result = _post(server, plan_id, {**SHORTCUT, "q": "aae"})
    assert status == 200, result
    assert _approved(db) == set()
    assert (result["decided"], result["held"]) == (0, 2)


# --- `/list` is view-scoped (R1 F7 + F9) -----------------------------------------------


def test_the_lists_counts_are_the_views_rows(served):
    """`status=proposed` drops a decided row from every count; `q` drops other groups."""
    server, plan_id, db = served
    _sql(db, "UPDATE plan_items SET status = 'approved' WHERE old_rel_path = 'IMG_3.JPG'")
    whole = _group_with(_list(server, plan_id), server, plan_id, "IMG_2.JPG")
    assert (whole["total"], whole["undecided"], whole["shortcut_count"]) == (2, 1, 1)
    view = _list(server, plan_id, status="proposed")
    pair = _group_with(view, server, plan_id, "IMG_2.JPG")
    assert (pair["total"], pair["undecided"], pair["shortcut_count"]) == (1, 1, 1)
    assert sum(g["total"] for g in view["groups"]) == 4
    only = _list(server, plan_id, q="img_4")
    assert [g["total"] for g in only["groups"]] == [1]
    assert (only["undecided"], only["shortcut_count"]) == (1, 1)


@pytest.mark.parametrize("name", ["sort", "list_group", "offset", "limit", "colour"])
def test_the_list_refuses_a_parameter_that_is_not_part_of_a_view(served, name):
    """Task 0D's copy, spelled; `/list`'s view is `tier`, `status`, `attention`, `q` and -
    the controller's M14 Task 3 ruling (slice 3d) - the date tree's `group`."""
    server, plan_id, _db = served
    with pytest.raises(ApiError) as refused:
        _list(server, plan_id, **{name: "x"})
    assert refused.value.status == 400
    assert refused.value.message == f'{name}: "{name}" is not something pam can filter by.'


def test_the_list_accepts_every_view_parameter(served):
    """The negative control over the same five names, spelled independently."""
    server, plan_id, _db = served
    reply = _list(server, plan_id, tier="high", status="proposed", attention="0", q="img")
    assert reply["undecided"] == 5
    reply = _list(
        server, plan_id, tier="high", status="proposed", attention="0", q="img", group="2020"
    )
    assert reply["undecided"] == 2


def test_an_empty_list_group_on_a_shortcut_is_no_filter(served):
    server, plan_id, db = served
    status, result = _post(server, plan_id, {**SHORTCUT, "list_group": ""})
    assert status == 200, result
    assert _approved(db) == set(FILES)


# --- the shortcut's body, refused whole ------------------------------------------------

REFUSED_BODIES = [
    ("shortcut false", {"action": "approve", "shortcut": False}, "shortcut"),
    ("shortcut 1", {"action": "approve", "shortcut": 1}, "shortcut"),
    ("with accept_all", {**SHORTCUT, "accept_all": True}, "accept_all"),
    ("skip", {"action": "skip", "shortcut": True}, "action"),
    ("reset", {"action": "reset", "shortcut": True}, "action"),
    ("malformed nav group", {**SHORTCUT, "group": "2020-13"}, "group"),
    ("sort", {**SHORTCUT, "sort": "date"}, "sort"),
    ("tier low", {**SHORTCUT, "tier": "low"}, "tier"),
    ("bad key", {**SHORTCUT, "list_group": "2020-13-01"}, "list_group"),
    ("key not text", {**SHORTCUT, "list_group": 5}, "list_group"),
    ("list_group alone", {"action": "approve", "list_group": "undated"}, "list_group"),
    ("unknown field", {**SHORTCUT, "colour": "x"}, "colour"),
]
assert len(REFUSED_BODIES) >= 10, "the refusal table would parametrize over nothing"


@pytest.mark.parametrize(
    ("body", "field"),
    [(body, field) for _, body, field in REFUSED_BODIES],
    ids=[name for name, *_ in REFUSED_BODIES],
)
def test_a_malformed_shortcut_is_a_400_naming_its_field_and_writes_nothing(
    served, writes, body, field
):
    server, plan_id, db = served
    before = _statuses(db)
    status, result = _post(server, plan_id, body)
    assert status == 400, result
    assert result["error"].startswith(f"{field}:"), result
    assert writes == []
    assert _statuses(db) == before


# --- M13's `Accept all` is unchanged by OD-1 (T3 mutation 10, the page's half) ----------


def test_accept_all_still_approves_a_needs_a_look_name_that_is_not_low(served):
    """M13's ruling: `Accept all` holds back Low and NOTHING else, so an OCR-dated `medium`
    name - in the needs-a-look set - is approved by it, unlike by a shortcut."""
    server, plan_id, db = served
    _ocr_dated_medium(db, "IMG_2.JPG")
    _sql(db, "UPDATE plan_items SET tier = 'low' WHERE old_rel_path = 'IMG_4.JPG'")
    status, result = _post(server, plan_id, {"action": "approve", "accept_all": True})
    assert status == 200, result
    assert _approved(db) == {"IMG_1.HEIC", "IMG_1.AAE", "IMG_2.JPG", "IMG_3.JPG"}
    assert result["excluded_low"] == 1


# --- the date tree's `group` (M14 Task 3 slice 3d, the controller's ruling) ---------------


def test_the_lists_counts_under_a_month_filter_are_that_months_units(served):
    """Under `2020-08` the list shows IMG_2 and IMG_3 (one list group, an hour apart) and no
    other group; every count is theirs. Spelled from the fixture."""
    server, plan_id, _db = served
    reply = _list(server, plan_id, group="2020-08")
    assert [g["total"] for g in reply["groups"]] == [2]
    assert (reply["undecided"], reply["attention_undecided"], reply["shortcut_count"]) == (
        2,
        0,
        2,
    )
    assert (reply["groups"][0]["undecided"], reply["groups"][0]["shortcut_count"]) == (2, 2)


def test_a_shortcut_under_a_month_filter_approves_exactly_that_months_units(served, writes):
    """The top `Approve the other N` under `2020-08`: N is 2, and the write is IMG_2 and IMG_3."""
    server, plan_id, db = served
    assert _list(server, plan_id, group="2020-08")["shortcut_count"] == 2
    status, result = _post(server, plan_id, {**SHORTCUT, "group": "2020-08"})
    assert status == 200, result
    assert result["decided"] == 2
    assert _approved(db) == {"IMG_2.JPG", "IMG_3.JPG"}


# --- a group the view shows only PART of (Task 3 review R1, finding F3) -----------------------


def test_a_groups_N_under_a_search_that_shows_part_of_it_is_the_views_and_so_is_its_write(
    served, writes
):
    """IMG_2 and IMG_3 are one list group (an hour apart). `q=img_2` shows IMG_2 alone, so the
    group's `Approve N` is 1 - NOT the group's 2 - and that shortcut, sent with the view and the
    group's key as the page sends it, approves IMG_2 and nothing else. Spelled from the fixture.
    (M2918: a group's N counted over the whole list group left every other test green.)"""
    server, plan_id, db = served
    whole = _group_with(_list(server, plan_id), server, plan_id, "IMG_2.JPG")
    assert whole["shortcut_count"] == 2, "precondition: IMG_2 and IMG_3 share a list group"
    reply = _list(server, plan_id, q="img_2")
    assert len(reply["groups"]) == 1
    group = reply["groups"][0]
    assert group["key"] == whole["key"]
    assert (group["total"], group["undecided"], group["shortcut_count"]) == (1, 1, 1)
    status, result = _post(
        server, plan_id, {**SHORTCUT, "q": "img_2", "list_group": group["key"]}
    )
    assert status == 200, result
    assert result["decided"] == 1 == group["shortcut_count"]
    assert _approved(db) == {"IMG_2.JPG"}
