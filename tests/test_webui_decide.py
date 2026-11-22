"""Spec 8.2's write API, over real loopback HTTP against a real plan (M7 Task 9).

THESE FOUR ENDPOINTS ARE THE ONLY WEB-REACHABLE WRITES IN THE PRODUCT, and what they
write is what `pam apply` renames irreplaceable files to. So the module ends with an
END-TO-END test that decides and edits over HTTP, stops the server, runs the REAL
`run_apply`, and checks the disk - the only test that can prove the UI's writes and M5's
apply agree about what a plan is. Everything above it is the shape of one refusal or one
transition, pinned from spec 8.2 / UX 5's own wording rather than from the handlers.

EVERY EXPECTATION IS SPELLED FROM THE SPEC, NEVER READ BACK OUT OF THE CODE UNDER TEST.
This project has been bitten four times by an oracle built from the definition it checks
(`repo.open_plan`, `repo.ENRICHERS`, Task 7's `LOOPBACK_HOST`, Task 8's `_orientation`),
so the proposed names below are written out by hand from spec 6.1's template semantics,
the status vocabulary is written out from spec 4, and the row assertions read `plan_items`
with the suite's OWN SQL rather than through the repo functions the handlers call.

THE DRIVER SEES EVERY STATUS. `post` returns `Reply(status, body)` and never asserts, so
each refusal test names the status it expects. A driver that raised on non-2xx, or that
reported 200 for everything, would make every refusal below vacuous - that is the
reviewer's mutation 6, and it must turn this whole module red.

WRITE ISOLATION IS A FIXTURE, NOT AN ORDERING. The archive and the server are
module-scoped (plan clarification 10 - the suite sits near its 120 s re-flag and the
expensive half of a live server is the archive behind it), but these tests WRITE, so
`baseline` resets every item's status and `new_filename` before each one. A shared mutable
plan across write tests is finding M628a's exact shape.
"""

import dataclasses
import json
import logging
import os
import sqlite3
import threading
import webbrowser
from http.client import HTTPConnection
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import quote

import pytest
from archives import FACES_FLOOR, FAKE_COMMAND, assert_verify_agrees

from pam.catalog import repo
from pam.catalog.db import connect
from pam.execute.apply import run_apply
from pam.execute.validator import OLD_PLAN_REASON
from pam.naming.accept import accept_all
from pam.naming.planner import run_plan
from pam.naming.sanitize import MAX_FULL_PATH, full_path_length, reserved_head
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan
from pam.webui import decide_api, disclosure, write_api, write_state
from pam.webui.api import ApiError
from pam.webui.server import ReviewServer

_REAL_WEBBROWSER_OPEN = webbrowser.open


@pytest.fixture(autouse=True)
def no_real_browser(monkeypatch):
    """Every test in this module gets a stand-in for `webbrowser.open`.

    Task 7 measured that a missing patch fails SILENTLY on the machine of whoever wrote
    the test - a real browser tab opens and nothing in the run says so.
    """
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: True)


def test_the_browser_patch_is_autouse_and_not_opt_in():
    """Takes no fixture argument, so it is only ever patched by the autouse above."""
    assert webbrowser.open is not _REAL_WEBBROWSER_OPEN


# --- the archive, built once ---------------------------------------------------------

# Spelled out as data so every expectation below can be read against it.
ARCHIVE = {
    # A RAW+JPEG unit: ONE stem, TWO different extensions, renamed together (spec 5.4).
    # This is amendment A6's subject - the members' extensions differ BY CONSTRUCTION,
    # which is why the rename endpoint validates against the clicked member only.
    "RAW_1.JPG": {"DateTimeOriginal": "2019:07:04 18:23:11"},
    "RAW_1.NEF": {"DateTimeOriginal": "2019:07:04 18:23:11"},
    # A live-photo unit: the other two-extension shape, and a video beside an image.
    "LIVE_1.JPG": {"DateTimeOriginal": "2020:08:05 09:15:00"},
    "LIVE_1.MOV": {"CreateDate": "2020:08:05 09:15:00"},
    # A lone high-tier item, and the plan-side collision target.
    "SOLO.JPG": {"DateTimeOriginal": "2021:09:06 11:02:03"},
    # spec 6.4's video-date-ambiguous rule: a `qt_utc` video whose filename says a
    # different day. The rule caps the date token to `low`, which caps the item to `low`
    # - this is the file the Low-tier contract is tested against, in both directions.
    "2018-01-01_VID.MOV": {"CreateDate": "2021:03:04 10:00:00"},
    # The boundary: a plain `qt_utc` video with nothing to disagree with. `medium`, so a
    # broad `approve` must take THIS and leave the `low` one - a bulk-approval test where
    # everything is low or everything is high proves nothing about the exclusion.
    "VID_PLAIN.MOV": {"CreateDate": "2021:03:03 10:00:00"},
    # A photo and its Apple edit sidecar - ONE companion unit (spec 5.4, `.aae` is a
    # default `scan.sidecar_exts` entry), and the ROUTE THE M8 REVIEW FOUND. A sidecar has
    # no `capture` row, so it is in no event, so `event_key` is None for it while the photo
    # it belongs to sits under `2023-03.eN`: an event click used to select the photo and
    # leave the sidecar behind. On a date of its own, so it forms its own event and the
    # `2019-07` event test above keeps meaning what it says.
    "SIDE_1.JPG": {"DateTimeOriginal": "2023:03:03 14:00:00"},
    "SIDE_1.AAE": {},
    # ALREADY CONFORMING, so it is on disk and in `files` but NOT in the plan (spec 6.3
    # rule 3). It is the disk half of the collision namespace, isolated: no plan item
    # proposes this name, so a rename refused against it can only have come from disk.
    "2015-05-05_001.JPG": {"DateTimeOriginal": "2015:05:05 12:00:00"},
}

# spec 6.2 rule 5's path budget, made to leave exactly three characters for the stem.
# Three is the number that matters: `Console` cut to three characters is `Con`, a Windows
# device name - M3 review condition C1, and the interaction a one-pass sanitize/truncate
# composition gets wrong. The directory depth is computed from rule 5's own formula
# because the archive lives under a temp path whose length differs on every machine.
DEEP_FILE = "T.JPG"
DEEP_TAGS = {"DateTimeOriginal": "2022:02:02 08:00:00"}
DEEP_BUDGET = 3

# What the planner writes under the configured template `{date}_{event-or-scene}_
# {people}_{seq}`, spelled out from spec 6.1: no event, scene or people facts exist for
# these files, so the stem is `{date}_{seq}` - the capture date as `YYYY-MM-DD` and a
# three-digit sequence scoped per date, with every extension keeping its on-disk case
# (spec 5.4 rev 6). `T.JPG` is the exception: its budget leaves three characters, so the
# date is cut to `202` and `{seq}` never fits at all.
PLANNED = {
    "RAW_1.JPG": "2019-07-04_001.JPG",
    "RAW_1.NEF": "2019-07-04_001.NEF",
    "LIVE_1.JPG": "2020-08-05_001.JPG",
    "LIVE_1.MOV": "2020-08-05_001.MOV",
    "SOLO.JPG": "2021-09-06_001.JPG",
    "2018-01-01_VID.MOV": "2021-03-04_001.MOV",
    "VID_PLAIN.MOV": "2021-03-03_001.MOV",
    "SIDE_1.JPG": "2023-03-03_001.JPG",
    "SIDE_1.AAE": "2023-03-03_001.AAE",
    DEEP_FILE: "202.JPG",
}

# spec 6.3.4's tiers for the three files the Low-tier contract turns on.
LOW = "2018-01-01_VID.MOV"
MEDIUM = "VID_PLAIN.MOV"
HIGH = "SOLO.JPG"

# spec 4's complete `plan_items.status` enum, written out here rather than imported, so
# that `test_the_two_status_partitions_cover_the_whole_enum` is an independent check.
SPEC_ITEM_STATUSES = frozenset(
    {"proposed", "approved", "skipped", "applied", "failed", "undone"}
)
DECIDABLE_STATUSES = frozenset({"proposed", "approved", "skipped"})


class Live(NamedTuple):
    server: ReviewServer
    root: Path
    db_path: Path
    plan_id: int  # the `open` plan every write test drives
    stale_plan: int  # a `superseded` plan, for the not-open refusals
    token: str
    ids: dict[str, int]  # old filename -> plan_items.id in `plan_id`
    tiers: dict[int, str]  # plan_items.id -> the tier the PLANNER wrote
    deep_dir: str


def _put(directory: Path, name: str, tags: dict[str, Any]) -> None:
    """One archive file plus the sidecar `tests/fake_exiftool.py` reads its tags from."""
    (directory / name).write_bytes(f"the only copy of {name}".encode())
    (directory / f"{name}.tags.json").write_text(
        json.dumps({**tags, "PamTestSidecar": name}), encoding="utf-8"
    )


def _deep_directory(root: Path) -> Path:
    """A directory deep enough that `stem_budget` leaves `DEEP_BUDGET` characters."""
    pad = MAX_FULL_PATH - len(str(root)) - len("JPG") - 1 - DEEP_BUDGET
    assert pad > 0, (
        f"the temp path is {len(str(root))} characters, which leaves no room to build a"
        " directory deep enough to exercise the path budget. Point pytest at a shorter"
        " --basetemp rather than weakening the fixture"
    )
    deep = root / ("d" * pad)
    deep.mkdir()
    return deep


def _serve(workspace, plan_id: int, name: str) -> tuple[ReviewServer, threading.Thread]:
    """A live server on its own thread, with an ISOLATED logger.

    Never the `pam` logger (finding M628a): `open_session_log` clears that logger's
    handlers, so a fixture borrowing it has its log file silently killed by the next
    command-driving test - an order dependency that made a security-relevant test pass by
    position. `propagate = False` keeps this one out of pytest's capture too.
    """
    logger = logging.getLogger(name)
    logger.propagate = False
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    server = ReviewServer(workspace=workspace, plan_id=plan_id, logger=logger)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    """A real scanned archive, two real plans, and a real server serving the open one.

    PAM_HOME is set and restored INSIDE the setup, never held across the yield:
    `conftest.py`'s `isolated_pam_home` is function-scoped and would fight it. Everything
    the tests need afterwards is an absolute path.

    Two plans because spec 8.2's refusals include "the plan must be `open`": the first
    run is superseded by the second (spec 6.3 rule 5), which gives a real terminal plan to
    aim the refusal tests at rather than a hand-written status.
    """
    home = tmp_path_factory.mktemp("decide-home")
    previous = os.environ.get("PAM_HOME")
    os.environ["PAM_HOME"] = str(home)
    try:
        root = tmp_path_factory.mktemp("decide-archive") / "photos"
        root.mkdir(parents=True)
        for name, tags in ARCHIVE.items():
            _put(root, name, tags)
        deep = _deep_directory(root)
        _put(deep, DEEP_FILE, DEEP_TAGS)
        run_scan(root, exiftool_command=FAKE_COMMAND)
        stale = run_plan(root)
        current = run_plan(root)
        assert stale.plan_id is not None and current.plan_id is not None
        workspace = workspace_if_exists(root)
        assert workspace is not None
    finally:
        if previous is None:
            os.environ.pop("PAM_HOME", None)
        else:
            os.environ["PAM_HOME"] = previous

    conn = connect(workspace.db_path)
    try:
        planned = repo.plan_rows(conn, current.plan_id, floor=FACES_FLOOR)
        ids = {item.old_filename: item.id for item in planned}
        tiers = {item.id: item.tier for item in planned}
    finally:
        conn.close()

    server, thread = _serve(workspace, current.plan_id, "pam.tests.webui_decide")
    yield Live(
        server=server,
        root=root,
        db_path=workspace.db_path,
        plan_id=current.plan_id,
        stale_plan=stale.plan_id,
        token=server.token,
        ids=ids,
        tiers=tiers,
        deep_dir=deep.name,
    )
    server.shutdown()
    server.server_close()
    thread.join(timeout=20)


@pytest.fixture()
def baseline(live):
    """Put the plan back to what the planner wrote, before every test in this module.

    Written with the suite's own SQL rather than through the repo functions the handlers
    call: this is the fixture that must not agree with a broken writer. Reset on ENTRY,
    so a test that fails half way through leaves nothing for the next one to inherit.

    `tier` IS RESTORED HERE TOO, and it is the one column no endpoint can write. The
    Low-tier contract's interaction with A1's unit widening is only testable against a
    unit whose members DISAGREE about their tier, and the planner cannot produce one -
    `set_tier` forces it. A forced tier that outlived its test would silently re-tier a
    module-scoped plan for every test after it, which is finding M628a's shape again.
    """
    conn = connect(live.db_path)
    try:
        conn.execute(
            "UPDATE plan_items SET status = 'proposed',"
            " new_filename = generated_filename WHERE plan_id = ?",
            (live.plan_id,),
        )
        conn.executemany(
            "UPDATE plan_items SET tier = ? WHERE id = ?",
            [(tier, item_id) for item_id, tier in live.tiers.items()],
        )
        conn.commit()
        # ASSERTED, not merely written. A reset that silently rewrites is a reset that
        # cannot be caught failing: delete the statement above and every tier is already
        # what it should be for every test that did not force one, so the leak only
        # shows up in whichever test happens to run after the one that did (M628a's
        # shape, and M1048 measured exactly this leak). Reading the column back turns a
        # broken reset into a red fixture instead of a red test somewhere else.
        restored = dict(
            conn.execute(
                "SELECT id, tier FROM plan_items WHERE plan_id = ?", (live.plan_id,)
            ).fetchall()
        )
        assert restored == live.tiers, "the tier reset did not put the planner's tiers back"
    finally:
        conn.close()
    return live


# --- the drivers ---------------------------------------------------------------------


class Reply(NamedTuple):
    status: int
    body: Any


def post(live: Live, path: str, body: Any = None) -> Reply:
    """One POST to the live server. RETURNS the status; never asserts on it.

    A driver that raised on a refusal, or that reported 200 whatever came back, would
    make every refusal test in this module vacuous while leaving them all green.
    """
    payload = b"" if body is None else json.dumps(body).encode("utf-8")
    conn = HTTPConnection(live.server.host, live.server.port, timeout=20)
    try:
        conn.request(
            "POST",
            f"/t/{live.token}/api{path}",
            body=payload,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
            },
        )
        response = conn.getresponse()
        raw = response.read()
        return Reply(response.status, json.loads(raw) if raw else None)
    finally:
        conn.close()


def post_raw(live: Live, path: str, payload: bytes) -> Reply:
    """The same, with the body bytes given exactly - for the malformed-body tests."""
    conn = HTTPConnection(live.server.host, live.server.port, timeout=20)
    try:
        conn.request(
            "POST",
            f"/t/{live.token}/api{path}",
            body=payload,
            headers={"Content-Length": str(len(payload))},
        )
        response = conn.getresponse()
        raw = response.read()
        return Reply(response.status, json.loads(raw) if raw else None)
    finally:
        conn.close()


def get(live: Live, path: str) -> Reply:
    conn = HTTPConnection(live.server.host, live.server.port, timeout=20)
    try:
        conn.request("GET", f"/t/{live.token}/api{path}")
        response = conn.getresponse()
        raw = response.read()
        return Reply(response.status, json.loads(raw) if raw else None)
    finally:
        conn.close()


def rows(live: Live, plan_id: int | None = None) -> dict[str, tuple[str, str]]:
    """old filename -> (status, new_filename), read with the SUITE'S OWN SQL.

    An independent second implementation of the read the handlers write through, which
    is the shape M5 settled on for a supersede oracle: a test that asked `repo` what it
    had just told `repo` to write would agree with a broken writer.
    """
    conn = connect(live.db_path)
    try:
        conn.row_factory = sqlite3.Row
        found = conn.execute(
            "SELECT old_rel_path, status, new_filename FROM plan_items WHERE plan_id = ?",
            (live.plan_id if plan_id is None else plan_id,),
        )
        return {
            str(r["old_rel_path"]).rpartition("/")[2]: (
                str(r["status"]),
                str(r["new_filename"]),
            )
            for r in found
        }
    finally:
        conn.close()


def decide(live: Live, names: tuple[str, ...], action: str) -> Reply:
    ids = [live.ids[name] for name in names]
    return post(live, f"/plan/{live.plan_id}/decide", {"item_ids": ids, "action": action})


def set_status(live: Live, name: str, status: str) -> None:
    """Force one item into a status the API cannot reach, with the suite's own SQL."""
    conn = connect(live.db_path)
    try:
        conn.execute("UPDATE plan_items SET status = ? WHERE id = ?", (status, live.ids[name]))
        conn.commit()
    finally:
        conn.close()


def set_tier(live: Live, name: str, tier: str) -> None:
    """Force one item's tier, with the suite's own SQL - no endpoint writes this column.

    The planner computes a tier ONCE PER UNIT and copies it to every member, which is the
    uniformity `test_tier_trust_score_and_attention_are_uniform_across_every_companion_
    unit` pins. That uniformity is why the preset buttons could never split a unit - and
    it is also why the ONLY way to test what the Low-tier contract does to a widened unit
    is to break it by hand. `baseline` puts it back.
    """
    conn = connect(live.db_path)
    try:
        conn.execute("UPDATE plan_items SET tier = ? WHERE id = ?", (tier, live.ids[name]))
        conn.commit()
    finally:
        conn.close()


def set_new_filename(live: Live, name: str, new_filename: str) -> None:
    """Force one item's proposed name, with the suite's own SQL - the sibling of
    `set_status`, for the refusal setups that must not go through a writer the spy in
    `test_a_refusal_never_reaches_the_writer_at_all` is watching."""
    conn = connect(live.db_path)
    try:
        conn.execute(
            "UPDATE plan_items SET new_filename = ? WHERE id = ?",
            (new_filename, live.ids[name]),
        )
        conn.commit()
    finally:
        conn.close()


# --- the vocabulary the transitions are written against ------------------------------


def test_the_two_status_partitions_cover_the_whole_enum():
    """Spec 4's six statuses split into "decidable" and "terminal" with nothing left.

    Both halves are spelled from the spec above, so a seventh status added to the enum
    without a decision about which half it belongs in is a red test rather than a value
    `_refuse_undecidable` silently treats as decidable.
    """
    # READ FROM `write_state`, WHICH DEFINES THEM, NEVER FROM `write_api`, WHICH ONLY
    # IMPORTS THEM (M10 Task 2 review, R1; mutation M1450). After the split these three
    # lines still resolved through `write_api`'s re-export, so a mutant that broke
    # `write_state`'s definition and gave `write_api` a correct private copy left this
    # test GREEN over a broken partition - an oracle reaching its subject through a
    # module that merely re-exports it is the same defect as one built from the
    # definition it checks.
    assert repo.ITEM_STATUSES == SPEC_ITEM_STATUSES
    assert frozenset(write_state.TERMINAL_STATUSES) | DECIDABLE_STATUSES == SPEC_ITEM_STATUSES
    assert frozenset(write_state.TERMINAL_STATUSES) & DECIDABLE_STATUSES == frozenset()
    assert frozenset(write_state.TARGET_STATUS.values()) == DECIDABLE_STATUSES


def test_the_planner_wrote_the_names_this_module_expects(baseline):
    """The fixture, asserted once so every expectation below rests on something real."""
    assert {name: value[1] for name, value in rows(baseline).items()} == PLANNED
    assert set(baseline.ids) == set(PLANNED)


# --- decide: the transition matrix ---------------------------------------------------


@pytest.mark.parametrize("start", sorted(DECIDABLE_STATUSES))
@pytest.mark.parametrize("action", ["approve", "skip"])
def test_approve_and_skip_are_legal_from_every_decidable_status(baseline, start, action):
    """Spec 8.2: approve/skip from `proposed`, `approved` or `skipped` - idempotent and
    switchable, so all six cells succeed and land on the action's own target."""
    set_status(baseline, HIGH, start)
    reply = decide(baseline, (HIGH,), action)
    assert reply.status == 200, reply.body
    assert reply.body["decided"] == 1
    assert rows(baseline)[HIGH][0] == {"approve": "approved", "skip": "skipped"}[action]


@pytest.mark.parametrize("start", ["approved", "skipped"])
def test_reset_returns_a_decided_item_to_proposed(baseline, start):
    """Spec 8.2: `reset` -> `proposed` from `approved` or `skipped`."""
    set_status(baseline, HIGH, start)
    reply = decide(baseline, (HIGH,), "reset")
    assert reply.status == 200, reply.body
    assert rows(baseline)[HIGH][0] == "proposed"


def test_reset_on_an_undecided_item_succeeds_and_changes_nothing(baseline):
    """Spec 8.2 enumerates the two things that refuse a batch - a terminal status and
    another plan's item - and this is neither. Refusing it would make a bulk `reset` over
    an ordinary view fail because part of the view had never been decided."""
    reply = decide(baseline, (HIGH,), "reset")
    assert reply.status == 200, reply.body
    assert rows(baseline)[HIGH][0] == "proposed"


@pytest.mark.parametrize("terminal", sorted(SPEC_ITEM_STATUSES - DECIDABLE_STATUSES))
@pytest.mark.parametrize("action", ["approve", "skip", "reset"])
def test_no_action_may_touch_a_terminal_item(baseline, terminal, action):
    """Spec 8.2: `applied`, `failed` and `undone` refuse the request, naming the id and
    its status. Nine cells, because a terminal status is terminal for every action."""
    set_status(baseline, HIGH, terminal)
    reply = decide(baseline, (HIGH,), action)
    assert reply.status == 409
    assert str(baseline.ids[HIGH]) in reply.body["error"]
    assert terminal in reply.body["error"]
    assert rows(baseline)[HIGH][0] == terminal


def test_one_terminal_id_refuses_the_whole_batch_and_writes_nothing(baseline):
    """Spec 8.2's batch atomicity. The half a lazy test skips is the second assertion:
    the OTHER items must be exactly as they were, not merely 'the request failed'."""
    set_status(baseline, LOW, "applied")
    before = rows(baseline)
    reply = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide",
        {
            "item_ids": [baseline.ids[HIGH], baseline.ids[LOW], baseline.ids[MEDIUM]],
            "action": "approve",
        },
    )
    assert reply.status == 409
    assert rows(baseline) == before


def test_an_item_of_another_plan_refuses_the_whole_batch(baseline):
    """Spec 8.2: an id belonging to another plan refuses the request. The superseded
    plan's ids are real ids of real items - just not this plan's to decide."""
    conn = connect(baseline.db_path)
    try:
        stranger = repo.plan_rows(conn, baseline.stale_plan, floor=FACES_FLOOR)[0].id
    finally:
        conn.close()
    before = rows(baseline)
    reply = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide",
        {"item_ids": [baseline.ids[HIGH], stranger], "action": "approve"},
    )
    assert reply.status == 409
    assert str(stranger) in reply.body["error"]
    assert rows(baseline) == before


def test_an_id_that_exists_nowhere_refuses_the_whole_batch(baseline):
    before = rows(baseline)
    reply = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide",
        {"item_ids": [baseline.ids[HIGH], 987654], "action": "approve"},
    )
    assert reply.status == 409
    assert "987654" in reply.body["error"]
    assert rows(baseline) == before


def test_decide_refuses_a_plan_that_is_not_open(baseline):
    """Spec 8.2: the plan must be `open`, re-checked at the moment of the write."""
    conn = connect(baseline.db_path)
    try:
        stranger = repo.plan_rows(conn, baseline.stale_plan, floor=FACES_FLOOR)[0].id
    finally:
        conn.close()
    reply = post(
        baseline,
        f"/plan/{baseline.stale_plan}/decide",
        {"item_ids": [stranger], "action": "approve"},
    )
    assert reply.status == 409
    assert "superseded" in reply.body["error"]


def test_decide_on_an_unknown_plan_is_a_404_that_says_so(baseline):
    reply = post(baseline, "/plan/99999/decide", {"item_ids": [], "action": "approve"})
    assert reply.status == 404
    assert "99999" in reply.body["error"]


def test_every_write_response_carries_fresh_counts(baseline):
    """Spec 8.2: the header must update without a second request - so the counts in the
    response are asserted equal to what `/plan/{id}` says a moment later."""
    reply = decide(baseline, (HIGH, MEDIUM), "approve")
    assert reply.status == 200, reply.body
    assert reply.body["counts"]["approved"] == 2
    assert reply.body["counts"]["remaining"] == len(PLANNED) - 2
    summary = get(baseline, f"/plan/{baseline.plan_id}").body
    for key, value in reply.body["counts"].items():
        assert summary[key] == value, key


def test_ux_5a_step_3_is_reachable_only_through_the_UNDECIDED_attention_count(baseline):
    """UX 5a rev 6 steps 1-3, against the two numbers the banner can be built from.

    THE SPEC GAP THE TASKS 11+12 REVIEW RULED ON, pinned server-side so the ruling
    cannot quietly rot. UX 5a promises the banner reaches step 3 - `Nothing needs
    attention.` - after the user "clears the 20". But spec 8.2 rev 12 makes `attention`
    a property of the NAME (tier `low` OR undated OR any token truncated), and a
    DECISION changes none of those three operands. So:

      - `/plan/{id}`'s `attention` is INVARIANT across every decision. A banner counting
        it can never reach step 3: the user works through every flagged name and is
        still told to review them.
      - `?attention=1&status=proposed` is the work still outstanding, and it reaches 0.

    Both halves are asserted here, and the second is asserted to be non-zero at the
    baseline first, so the flow cannot pass by having nothing to clear. Which of the two
    the page uses where is `app.js`'s and HT-M7's; that the server can answer both, and
    that only one of them ever reaches zero, is this module's.
    """
    whole_set = get(baseline, f"/plan/{baseline.plan_id}/items?attention=1").body["total"]
    landing = get(baseline, f"/plan/{baseline.plan_id}/items?attention=1&status=proposed").body
    assert whole_set > 0, "no attention items, so the flow below would be vacuous"
    # UX 5a step 1: nothing is decided yet, so the work equals the whole set and the
    # banner reads exactly as the spec draws it.
    assert landing["total"] == whole_set

    # Step 2: the user clears them, one at a time, with `X`.
    reply = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide",
        {"item_ids": [card["id"] for card in landing["items"]], "action": "approve"},
    )
    assert reply.status == 200, reply.body

    # Step 3, reachable on one number and not on the other.
    after = get(baseline, f"/plan/{baseline.plan_id}/items?attention=1&status=proposed")
    assert after.body["total"] == 0
    summary = get(baseline, f"/plan/{baseline.plan_id}").body
    assert summary["attention"] == whole_set, (
        "deciding an item changed the summary's `attention`, so spec 8.2's predicate is"
        " no longer a property of the name alone - and the review's E-A ruling, which"
        " rests on that invariance, needs revisiting"
    )
    assert reply.body["counts"]["attention"] == whole_set


# --- decide: the Low-tier contract, from all four directions -------------------------


def test_decide_by_id_MAY_approve_a_low_tier_item(baseline):
    """Spec 8.2 puts the refusal on `decide-filter` AND ONLY THERE. UX 5a step 2 is the
    user clearing the attention items one at a time with `X`, and most of those are
    `low` - a tier check added here would look like extra care and break Smart Review."""
    assert _tier_of(baseline, LOW) == "low"
    reply = decide(baseline, (LOW,), "approve")
    assert reply.status == 200, reply.body
    assert reply.body["decided"] == 1
    assert reply.body["excluded_low"] == 0
    assert rows(baseline)[LOW][0] == "approved"


def test_an_explicit_low_filter_with_approve_is_refused_outright(baseline):
    """Spec 8.2 / vision 6a.3: refused, and nothing is written. Enforced server-side
    because a page that can POST can POST `{tier: 'low', action: 'approve'}`."""
    before = rows(baseline)
    reply = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide-filter",
        {"tier": "low", "action": "approve"},
    )
    assert reply.status == 400
    assert "bulk" in reply.body["error"]
    assert rows(baseline) == before


def test_a_broad_approve_silently_excludes_low_and_reports_how_many(baseline):
    """Spec 8.2: a broader filter excludes the low-tier items and the response reports
    `{decided, excluded_low}` so the UI can say so."""
    reply = post(baseline, f"/plan/{baseline.plan_id}/decide-filter", {"action": "approve"})
    assert reply.status == 200, reply.body
    assert reply.body["excluded_low"] == 1
    assert reply.body["decided"] == len(PLANNED) - 1
    after = rows(baseline)
    assert after[LOW][0] == "proposed"
    assert after[MEDIUM][0] == "approved"
    assert after[HIGH][0] == "approved"


def test_an_explicit_low_filter_with_skip_is_allowed(baseline):
    """Spec 8.2: `skip` and `reset` carry no tier restriction - skipping a low-confidence
    name in bulk is the safe direction, and Smart Review depends on it."""
    reply = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide-filter",
        {"tier": "low", "action": "skip"},
    )
    assert reply.status == 200, reply.body
    assert reply.body["decided"] == 1
    assert rows(baseline)[LOW][0] == "skipped"


def test_an_explicit_low_filter_with_reset_is_allowed(baseline):
    set_status(baseline, LOW, "approved")
    reply = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide-filter",
        {"tier": "low", "action": "reset"},
    )
    assert reply.status == 200, reply.body
    assert rows(baseline)[LOW][0] == "proposed"


def test_decide_filter_selects_exactly_what_the_items_endpoint_shows(baseline):
    """One definition of which items a filter selects (plan section C): the bulk decision
    must act on the same set the user was looking at, not on a second interpretation."""
    shown = get(baseline, f"/plan/{baseline.plan_id}/items?tier=medium").body["items"]
    reply = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide-filter",
        {"tier": "medium", "action": "skip"},
    )
    assert reply.status == 200, reply.body
    assert reply.body["decided"] == len(shown)
    skipped = {name for name, (status, _) in rows(baseline).items() if status == "skipped"}
    assert skipped == {card["old_filename"] for card in shown}


# --- A4/P9's un-decide, and P10's preset counts, from the server side ----------------
#
# The PAGE's half of both - which control sends which action, and which handler re-reads
# which number - runs in a file this suite cannot execute. What is checkable here is the
# ground each one stands on: that the `Undecided` set really is re-enterable, that the
# number a preset button prints moves under both of the writes that used to leave it
# alone, and that the one write path exempted from refreshing it genuinely moves nothing.

# UX spec 5's counts line (`923 items . 812 approved . 15 skipped . 96 remaining`) plus
# 5a's attention figure. Written out from the two spec sentences, never read back from
# `api._counts`, because an oracle built from the definition it checks is a survivor
# waiting to happen.
SPEC_8_2_COUNT_KEYS = frozenset({"total", "approved", "skipped", "remaining", "attention"})

# A year the archive really has items in, and not all of them: the nav's `group` filter
# is what P10's first half moved without telling the buttons.
NAV_GROUP = "2019"


def _view_total(live: Live, query: str) -> int:
    """The SIZE of a filtered set and none of it - `limit=0`, exactly the request the
    page's `countInView` makes to put a number on a preset button."""
    reply = get(live, f"/plan/{live.plan_id}/items?{query}&limit=0")
    assert reply.status == 200, reply.body
    return int(reply.body["total"])


def _undecided_names(live: Live) -> set[str]:
    reply = get(live, f"/plan/{live.plan_id}/items?status=proposed&limit={len(PLANNED)}")
    assert reply.status == 200, reply.body
    return {card["old_filename"] for card in reply.body["items"]}


def test_the_undecided_set_is_re_populatable_and_reset_is_what_re_populates_it(baseline):
    """A4 / UX 5 rev 8's whole reason for existing, and HT-M7's P9.

    The Status filter offers `Undecided` as a set, and a set that cannot be re-entered is
    UX 1.4's dead end with a dropdown in front of it. It bit for real: the owner
    bulk-approved before a test that needed undecided items, and recovery took calling
    this endpoint directly - "a path no user has".

    THE DEAD END IS REPRODUCED FIRST. Everything is decided and `status=proposed` is
    asserted EMPTY, so the second half cannot pass by having had somewhere to go all
    along. Which control on the page sends the action is `app.js`'s and HT-M8's.
    """
    assert _undecided_names(baseline) == set(PLANNED)
    assert decide(baseline, tuple(PLANNED), "approve").status == 200
    assert _undecided_names(baseline) == set(), "the plan was not fully decided"
    assert _view_total(baseline, "status=proposed") == 0

    assert decide(baseline, (HIGH,), "reset").status == 200
    assert _undecided_names(baseline) == {HIGH}
    assert _view_total(baseline, "status=proposed") == 1


def test_a_bulk_approve_empties_the_preset_count_its_own_response_cannot_report(baseline):
    """P10, half two, and the reason `runPreset` must RE-READ rather than trust its write.

    `[Approve High (N)]` prints `tier=high&status=proposed`, and a bulk approve over that
    same filter takes it to zero. The write's response carries the HEADER's five counts
    and no tier breakdown, so nothing in it can move the number on the button - a page
    that applied the response and stopped would leave the button offering exactly what it
    had just approved. On a human test's library that button read hundreds when the answer was 0.

    The key set is asserted with `==` so that a future field appearing there - one that
    COULD carry the button's number - fails this test rather than silently making its
    premise stale.
    """
    before = _view_total(baseline, "tier=high&status=proposed")
    assert before > 0, "no undecided high-tier items, so the check below would be vacuous"

    reply = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide-filter",
        {"tier": "high", "action": "approve"},
    )
    assert reply.status == 200, reply.body
    assert reply.body["decided"] == before
    assert _view_total(baseline, "tier=high&status=proposed") == 0
    assert set(reply.body["counts"]) == SPEC_8_2_COUNT_KEYS


def test_the_preset_count_is_scoped_to_the_nav_group_so_a_nav_click_moves_it(baseline):
    """P10, half one, and the reason a date-nav click must go through `applyFilters`.

    `presetFilter` puts `state.filters.group` into the scope it counts AND into the scope
    it posts, so the number on the button is a different number inside a year than over
    the whole plan. `navEntry` used to set that filter and reload the grid without
    re-reading either count, which is a button offering the whole plan's total over a
    view holding a fraction of it.

    Both totals are asserted non-zero and unequal, so the check cannot pass on an empty
    group or on a plan whose year happens to hold everything.
    """
    whole = _view_total(baseline, "tier=high&status=proposed")
    inside = _view_total(baseline, f"group={NAV_GROUP}&tier=high&status=proposed")
    assert inside > 0, f"no undecided high-tier items in {NAV_GROUP}, so this proves nothing"
    assert inside < whole, (
        f"{NAV_GROUP} holds every undecided high-tier item ({inside} of {whole}), so the"
        " nav filter cannot be shown to move the count at all"
    )


def test_a_restore_moves_no_number_the_page_would_have_refreshed(baseline):
    """WHY `restoreUnit` IS THE ONE WRITE PATH IN `app.js` THAT REFRESHES NO COUNT.

    Spec 8.2's restore writes `new_filename` alone. `status`, `tier`, and `attention`'s
    three operands - the tier, the date and the planner's `truncated` flags (spec 8.2
    rev 12) - are all left where they were, and none of them is rewritten by an edit or a
    restore either (P7).

    Asserted as three numbers, and the rename before it is the non-vacuity control: a
    rename APPROVES its whole unit, so it must be seen MOVING them before the restore is
    credited with leaving them alone.

    THIS IS NOT AN EXEMPTION FROM REFRESHING THE COUNTS, and the review that took it for
    one is why the sentence is here: what a preset button counts is not `tier`+`status`,
    it is whatever `presetFilter()` builds, and that carries the SEARCH box too. The
    test immediately below is the counter-example, and `restoreUnit` refreshes.
    """

    def counted() -> tuple[int, int, int]:
        return (
            _view_total(baseline, "tier=high&status=proposed"),
            _view_total(baseline, "tier=medium&status=proposed"),
            _view_total(baseline, "attention=1&status=proposed"),
        )

    before = counted()
    assert rename(baseline, "RAW_1.JPG", "Sunset-Ridge.JPG").status == 200
    after_rename = counted()
    assert after_rename != before, (
        "the rename moved none of the three counts, so the restore below cannot be"
        " credited with moving none of them either"
    )

    assert post(baseline, f"/item/{baseline.ids['RAW_1.JPG']}/restore").status == 200
    assert counted() == after_rename


# The needle is planted BY the rename and exists nowhere in the archive otherwise: no
# `old_filename` in `PLANNED` contains it, and no generated name can, since every
# generated name is a date and a sequence. So a count scoped to it is a count of exactly
# the items carrying a USER-TYPED name - which is the set a restore empties.
SEARCH_NEEDLE = "Sunset"


def test_a_restore_moves_the_preset_count_when_a_SEARCH_is_in_the_view(baseline):
    """THE HOLE IN "restore is exempt by contract", found by reading `presetFilter`.

    `[Approve High (N)]` does not print `tier=high&status=proposed`. It prints whatever
    `presetFilter()` assembles, which carries `state.filters.group`, `state.filters.q`
    and the status filter as well - and spec 8.2's `q` matches the old name **or the
    proposed one**. `new_filename` is the single field a restore writes, so with a search
    in the view, putting pam's name back moves the number on the button: this is P10's
    defect, reached through the one path the invariant had excused.

    The state is not contrived - it is the one A4 just made reachable. Edit a name (which
    approves the unit), press X to take that decision back, then press the restore arrow.

    Both totals are asserted, and the middle one is the non-vacuity control: without it a
    broken `q` that matched nothing would satisfy the final assertion trivially.
    """
    scoped = f"q={SEARCH_NEEDLE}&tier=high&status=proposed"
    assert _view_total(baseline, scoped) == 0, "the needle is already in the archive"

    assert rename(baseline, HIGH, f"{SEARCH_NEEDLE}-Ridge.JPG").status == 200
    assert decide(baseline, (HIGH,), "reset").status == 200
    assert _view_total(baseline, scoped) == 1, (
        "the searched-for name is not in the counted set, so the restore below cannot be"
        " shown to take it out"
    )

    assert post(baseline, f"/item/{baseline.ids[HIGH]}/restore").status == 200
    assert _view_total(baseline, scoped) == 0, (
        "a restore left the preset count unmoved, so `restoreUnit` would have no reason"
        " to refresh it - re-check `presetFilter`'s operands before deleting that call"
    )


@pytest.mark.parametrize("wanted", [True, False])
def test_a_json_boolean_filter_selects_what_the_query_string_spelling_selects(baseline, wanted):
    """JSON has a boolean and a query string does not, so the body's `true`/`false` has to
    be translated into spec 8.2's `attention=1` / `attention=0`.

    That translation is the one place `decide-filter`'s body and `/items`' query string
    are NOT the same text, so it is the one place they can stop agreeing - and until this
    test nothing in the suite posted `attention` in a body at all, so inverting the
    translation left the whole suite green (M684). Both values are driven, because
    `attention` is tri-state and `false` is a real request for the complement.

    The oracle is the `/items` endpoint's own answer for the same filter, compared as a
    SET OF FILENAMES rather than as a count - two filters can select the same NUMBER of
    items and not the same items.
    """
    shown = get(baseline, f"/plan/{baseline.plan_id}/items?attention={int(wanted)}").body[
        "items"
    ]
    assert shown, "the fixture must hold items on BOTH sides of `attention`"
    reply = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide-filter",
        {"attention": wanted, "action": "skip"},
    )
    assert reply.status == 200, reply.body
    skipped = {name for name, (status, _) in rows(baseline).items() if status == "skipped"}
    assert skipped == {card["old_filename"] for card in shown}


def test_a_filter_value_in_the_body_is_validated_exactly_as_one_in_the_query(baseline):
    """Plan section C: a `?tier=nonsense` 400 that a body-borne `{"tier": "nonsense"}`
    does not get is a real bug, so both spellings go through the one validator."""
    from_query = get(baseline, f"/plan/{baseline.plan_id}/items?tier=nonsense")
    from_body = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide-filter",
        {"tier": "nonsense", "action": "skip"},
    )
    assert from_query.status == 400
    assert from_body.status == 400
    assert from_body.body["error"] == from_query.body["error"]


# Spec 8.2 rev 14's event group form, and the four ways to get it wrong - written out
# here rather than imported from the read module's list, because the whole claim under
# test is that TWO endpoints answer the same way, and a shared constant would prove only
# that one list was used twice. Every one of these is a 400 on both.
MALFORMED_EVENT_GROUPS = ("2019-07.eX", "2019.e12", "2019-07.e", "undated.e1")


def test_the_malformed_event_group_list_is_not_empty():
    """A control parametrized over the expectation it protects vanishes with it, and
    pytest SKIPS an empty parametrize rather than failing it (M743)."""
    assert len(MALFORMED_EVENT_GROUPS) == 4


@pytest.mark.parametrize("group", MALFORMED_EVENT_GROUPS)
def test_a_malformed_event_group_is_the_SAME_400_on_the_read_and_the_write_endpoint(
    baseline, group
):
    """Spec 8.2 rev 13 amendment D, applied to rev 14's new FORM of an old field.

    `group` gaining the `YYYY-MM.eN` shape widens nothing, because `decide-filter`
    resolves its filter through the same `_item_filter` the read endpoint does - which is
    also why "bulk-approve this event" arrives for free. The spec's own sentence for why
    that sharing matters is the assertion here: "a `tier=nonsense` that 400s on the read
    endpoint and not on the write one is a bug". The SENTENCE is compared, not only the
    status, so a write endpoint that refused for a different reason would still fail.
    """
    from_query = get(baseline, f"/plan/{baseline.plan_id}/items?group={quote(group)}")
    from_body = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide-filter",
        {"group": group, "action": "skip"},
    )
    assert from_query.status == 400, from_query.body
    assert from_body.status == 400, from_body.body
    assert from_body.body["error"] == from_query.body["error"]
    assert from_body.body["error"].startswith("group:")


def test_a_year_with_a_trailing_separator_is_the_SAME_400_on_BOTH_endpoints(baseline):
    """Spec 8.2 rev 15 (amendment A2), landing on rev 13 amendment D's shared validator.

    `2019-` was the one form that passed `_is_year_or_month` and then selected the empty
    set. On the READ endpoint that is an empty grid; on THIS one it is a bulk decision
    that reports nothing decided and leaves the archive alone - which is why the M8 review
    rated it low, and also why the executor deferred it: the fix narrows a bulk-WRITE
    input contract. The narrowing is safe because `app.js` builds every group key from
    `group_key`/`event_key` and no client sends this form.

    The SENTENCE is compared and not merely the status, exactly as the malformed-event
    case above does it: a write endpoint refusing for its own reason would be a second
    validator wearing the first one's status code. And the catalog is read back either
    side, because a 400 that had already written half a filter's worth of rows would
    satisfy every assertion about the response.
    """
    before = rows(baseline)
    from_query = get(baseline, f"/plan/{baseline.plan_id}/items?group={quote('2019-')}")
    from_body = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide-filter",
        {"group": "2019-", "action": "skip"},
    )
    assert from_query.status == 400, from_query.body
    assert from_body.status == 400, from_body.body
    assert from_body.body["error"] == from_query.body["error"]
    assert from_body.body["error"].startswith("group:")
    assert rows(baseline) == before


def test_bulk_deciding_one_event_is_what_the_shared_validator_buys(baseline):
    """The positive half, and the reason the negative half above matters.

    `RAW_1.JPG` and `RAW_1.NEF` are one companion unit in one event; nothing else in this
    archive shares it. A `decide-filter` scoped to that event's `YYYY-MM.eN` group decides
    exactly those two items and leaves every other row alone - read back with the suite's
    own SQL, so the assertion is about the CATALOG and not about what the endpoint said.
    """
    conn = connect(baseline.db_path)
    try:
        events = {
            str(row["start_dt"])[:10]: int(row["id"])
            for row in conn.execute("SELECT id, start_dt FROM events")
        }
    finally:
        conn.close()
    group = f"2019-07.e{events['2019-07-04']}"
    shown = get(baseline, f"/plan/{baseline.plan_id}/items?group={quote(group)}&limit=200")
    assert shown.status == 200, shown.body
    assert sorted(card["old_filename"] for card in shown.body["items"]) == [
        "RAW_1.JPG",
        "RAW_1.NEF",
    ]

    reply = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide-filter",
        {"group": group, "action": "approve"},
    )
    assert reply.status == 200, reply.body
    approved = {name for name, (status, _) in rows(baseline).items() if status == "approved"}
    assert approved == {"RAW_1.JPG", "RAW_1.NEF"}


def test_decide_filter_refuses_a_terminal_item_its_filter_selected(baseline):
    """Spec 8.2 writes the terminal refusal for `decide-filter` too - "then the same
    transition rules" - and three of the four endpoints had a test for it.

    The state has to be FORCED with the suite's own SQL, exactly as every other terminal
    test in this module forces it: no pam command leaves an `applied`, `failed` or
    `undone` item inside an `open` plan, so the branch is defensive. Defensive is not the
    same as dead - it is the last thing between a bulk `skip` over an ordinary view and a
    finished run's bookkeeping being rewritten - and without this test the whole call can
    be deleted from `decide_filter` with the suite green (M680).

    Unfiltered `skip` on purpose: that is UX 5a's real bulk action, and it selects the
    whole plan, so the terminal row arrives inside a batch of legitimate ones rather than
    as the only thing selected.
    """
    set_status(baseline, HIGH, "applied")
    before = rows(baseline)
    reply = post(baseline, f"/plan/{baseline.plan_id}/decide-filter", {"action": "skip"})
    assert reply.status == 409
    assert str(baseline.ids[HIGH]) in reply.body["error"]
    assert "applied" in reply.body["error"]
    assert rows(baseline) == before


def test_decide_filter_refuses_a_plan_that_is_not_open(baseline):
    reply = post(baseline, f"/plan/{baseline.stale_plan}/decide-filter", {"action": "skip"})
    assert reply.status == 409
    assert "superseded" in reply.body["error"]


# --- A1: a companion unit is decided as a unit ---------------------------------------
#
# Spec 7.3 rev 15's governing sentence, at the source. `pam apply` renaming a proper
# subset of a unit is a vision S3 breach whose damage is PERMANENT: the next `pam scan`
# sets `companion_group_id` to NULL for both files (spec 5.4 groups by stem) and the next
# plan proposes `_002` for the orphan, so the pair ends up separated, differently numbered
# and unrecoverable by any pam command. Task 1 contained that at apply; these are the two
# layers that stop the state being CREATED, and they matter because two of the three
# routes to it never render a card for the user to notice anything on.
#
# The units are written out from `ARCHIVE` above, never read back from `unit_key`: an
# oracle that asked the plan which items share a unit and then asserted those items share
# a unit would agree with any grouping at all.
LIVE_UNIT = ("LIVE_1.JPG", "LIVE_1.MOV")  # route 2's subject: `q=mov` matches one of these
RAW_UNIT = ("RAW_1.JPG", "RAW_1.NEF")
SIDECAR_UNIT = ("SIDE_1.JPG", "SIDE_1.AAE")  # route 3's: the `.AAE` sits in no event
COMPANION_UNITS = (LIVE_UNIT, RAW_UNIT, SIDECAR_UNIT)

# Asserted at COLLECTION, not inside a test: pytest SKIPS an empty parametrize rather
# than failing it, so the eighteen-cell matrix below would vanish silently and the run
# would read GREENER than before (M743, and M1050 measured exactly that here). The
# decorator itself is pinned in `test_the_matrix_really_runs_over_every_companion_unit`,
# because emptying the list and swapping the decorator are two different edits and this
# assert only catches the first (M1034/M1051).
assert COMPANION_UNITS, "the companion-unit matrix would be parametrized over nothing"

TARGET_OF = {"approve": "approved", "skip": "skipped", "reset": "proposed"}


def _event_group(live: Live, day: str) -> str:
    """The `YYYY-MM.eN` nav key of the event starting on `day`, read with the suite's SQL."""
    conn = connect(live.db_path)
    try:
        starts = {
            str(row["start_dt"])[:10]: int(row["id"])
            for row in conn.execute("SELECT id, start_dt FROM events")
        }
    finally:
        conn.close()
    assert day in starts, f"no event starts on {day}; the archive has {sorted(starts)}"
    return f"{day[:7]}.e{starts[day]}"


def _cards(live: Live, query: str = "") -> list[dict[str, Any]]:
    reply = get(live, f"/plan/{live.plan_id}/items?limit=200{query}")
    assert reply.status == 200, reply.body
    return list(reply.body["items"])


def test_the_fixture_really_holds_the_three_companion_units_this_section_needs(baseline):
    """Non-vacuity for everything below, and it is not a formality.

    Every test in this section is of the form "deciding one member moved the other". If
    the archive's pairs were not actually grouped - a `.AAE` typed `other` rather than a
    sidecar, say, or a live-photo pair split by a stem difference - every one of them
    would pass by deciding a lone item and finding nothing else to check.
    """
    keys = {card["old_filename"]: card["unit_key"] for card in _cards(baseline)}
    assert set(keys) == set(PLANNED)
    for unit in COMPANION_UNITS:
        assert keys[unit[0]] == keys[unit[1]], f"{unit} is not one companion unit"
    assert len({keys[unit[0]] for unit in COMPANION_UNITS}) == len(COMPANION_UNITS)
    lone = [name for name in PLANNED if all(name not in unit for unit in COMPANION_UNITS)]
    assert len({keys[name] for name in lone}) == len(lone), "a 'lone' file has a partner"


def test_the_matrix_really_runs_over_every_companion_unit():
    """The eighteen cells cover all three units, all three actions, either member.

    The collection-time assert beside COMPANION_UNITS catches the list being emptied.
    It does NOT catch the decorator being pointed somewhere else - narrowing it to
    `[LIVE_UNIT]` deletes twelve cells and leaves a green suite (M1051), and "the raw
    and sidecar pairs behave like the live one, drop them" is an edit somebody would
    make in good faith. Each axis is spelled from spec 8.2 here rather than read back
    from the decorator, so a decorator that agrees with itself is not enough.
    """
    matrix = test_deciding_one_member_of_a_companion_unit_decides_every_member
    axes = {
        mark.args[0]: mark.args[1] for mark in matrix.pytestmark if mark.name == "parametrize"
    }
    assert axes["unit"] == COMPANION_UNITS
    assert axes["action"] == ["approve", "reset", "skip"]  # spec 8.2's three, sorted
    assert axes["named"] == [0, 1]  # either member may be the one a key was pressed on


@pytest.mark.parametrize("action", sorted(TARGET_OF))
@pytest.mark.parametrize("named", [0, 1], ids=["first-member", "second-member"])
@pytest.mark.parametrize("unit", COMPANION_UNITS, ids=["live", "raw", "sidecar"])
def test_deciding_one_member_of_a_companion_unit_decides_every_member(
    baseline, unit, named, action
):
    """Route 1 (spec 8.2 rev 15): the per-card `X`/`Z` that used to split a unit.

    Eighteen cells, because the defect is symmetric in all three ways it can be reached:
    either member may be the one the user pressed a key on, all three actions write a
    decision, and the three unit SHAPES are three different reasons a unit exists
    (RAW+JPEG, Live Photo, sidecar - vision S3 names all three in one breath).

    The starting state is chosen so no cell can pass by doing nothing: `reset`'s target
    is `proposed`, so its unit starts `approved`.
    """
    start = "approved" if action == "reset" else "proposed"
    for name in unit:
        set_status(baseline, name, start)

    reply = decide(baseline, (unit[named],), action)
    assert reply.status == 200, reply.body
    assert reply.body["decided"] == 2

    after = rows(baseline)
    assert [after[name][0] for name in unit] == [TARGET_OF[action]] * 2
    assert {after[name][0] for name in PLANNED if name not in unit} == {"proposed"}


@pytest.mark.parametrize(
    ("action", "verb"),
    [("approve", "approved"), ("skip", "skipped"), ("reset", "returned to undecided")],
)
def test_a_decision_that_moved_more_than_one_file_names_them_and_says_so(
    baseline, action, verb
):
    """Spec 6.2 rule 7 / 8.2 rev 15: pam says what it changed, and the SERVER says it.

    The sentence is written out here from rule 7's own register rather than imported from
    `disclosure`, because a test that formatted the production template would pass for any
    wording at all - including no wording. The ids are asserted too: `app.js` repaints the
    cards it decided from its own request, and after A1 that request moves rows it never
    named, so a `companions` list that came back empty would leave a partner card showing
    a decision it no longer has.
    """
    reply = decide(baseline, ("LIVE_1.MOV",), action)
    assert reply.status == 200, reply.body
    assert reply.body["companions"] == [baseline.ids["LIVE_1.JPG"]]
    assert reply.body["note"] == (
        f"pam also {verb} 1 companion file — pam renames a group together or not at all."
    )


def test_a_bulk_approve_that_reverses_a_deliberate_skip_says_that_it_did(baseline):
    """FINDING F9, and UX 5 rev 10's second clause. NOTHING ABOUT THE BEHAVIOUR CHANGES.

    `decide-filter` with `status=proposed` and `approve` is the most ordinary bulk action
    there is - "approve everything I have not decided yet". A unit whose `.MOV` the user
    deliberately SKIPPED reaches that filter through its still-undecided `.JPG`, and A1's
    widening then approves the skipped member too. That is CORRECT and must not be
    "fixed": leaving one member skipped is the split spec 7.3 forbids, and pam renames a
    group together or not at all. What was missing is that nobody was told - design 6.2
    rule 7's disclosure counted the files and never said one of them had been decided the
    other way, and "a bulk action that silently undoes an explicit choice is the surprise
    section 1.2 exists to prevent".

    The status is forced with the suite's own SQL because no endpoint can produce a partly
    decided unit any more: `decide` on the `.MOV` alone would skip the `.JPG` with it,
    which is A1 doing its job and is the reason this state has to be built by hand.

    The sentence is written out from UX 5 rev 10's register rather than imported from
    `disclosure`, on this module's standing rule: a test that formatted the production
    template would pass for any wording, including none.
    """
    set_status(baseline, "LIVE_1.MOV", "skipped")
    shown = {card["old_filename"] for card in _cards(baseline, "&status=proposed")}
    assert "LIVE_1.JPG" in shown
    assert "LIVE_1.MOV" not in shown, (
        "`status=proposed` now shows the skipped member, so this filter no longer reaches"
        " a unit through its undecided half and the test is not testing F9"
    )

    reply = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide-filter",
        {"status": "proposed", "action": "approve"},
    )
    assert reply.status == 200, reply.body
    assert rows(baseline)["LIVE_1.MOV"][0] == "approved", (
        "the skip is overridden and that is spec 7.3 rev 15 working - a member left"
        " skipped inside an approved unit is the split vision S3 forbids"
    )
    assert reply.body["companions"] == [baseline.ids["LIVE_1.MOV"]]
    assert reply.body["overrode"] == [baseline.ids["LIVE_1.MOV"]]
    assert reply.body["note"] == (
        "pam also approved 1 companion file — pam renames a group together or not at"
        " all. That file had been decided the other way — pam changed it to keep the"
        " group together."
    )


def test_a_companion_that_had_not_been_decided_is_counted_and_not_reported_as_overridden(
    baseline,
):
    """The control on the sentence above, and the reason `overrode` is not `companions`.

    An undecided companion is swept along by exactly the same widening, and rule 7's
    count already covers it. `proposed` is not a decision, so there is nothing to say was
    reversed - a page that claimed an override on every widened keystroke would stop
    meaning anything on the one that mattered.

    The second half is the other non-override: a companion already decided THE SAME WAY.
    """
    reply = decide(baseline, ("LIVE_1.MOV",), "approve")
    assert reply.status == 200, reply.body
    assert reply.body["companions"] == [baseline.ids["LIVE_1.JPG"]]
    assert reply.body["overrode"] == []
    assert "decided the other way" not in reply.body["note"]

    set_status(baseline, "SIDE_1.AAE", "approved")
    same = decide(baseline, ("SIDE_1.JPG",), "approve")
    assert same.status == 200, same.body
    assert same.body["companions"] == [baseline.ids["SIDE_1.AAE"]]
    assert same.body["overrode"] == [], "it moved no further than the rest of its unit"


def test_an_item_the_user_NAMED_is_not_reported_as_an_override(baseline):
    """`overrode` is scoped to the COMPANIONS, and that narrow reading had no test.

    THE MUTATION THIS PINS (M10 Tasks 3+4 review, M1467) SURVIVED THE WHOLE SUITE.
    Widening `overrode` from `companions` to every item whose decision changed - the
    reading UX 5 rev 10's body can be stretched to permit - left all 2663 tests green,
    so the implementer's deliberate choice was held up by nothing but its own docstring.
    This project's standing lesson, in its purest form: a judgement call no test can see
    is a judgement call the next person will reverse without noticing.

    THE CHOICE, SPELLED FROM THE AMENDMENT'S OWN TITLE: "a bulk decision that WIDENS to a
    companion unit says what it overrode." The sentence exists because a keystroke aimed
    at one card moved a file the user was not looking at. An item the user NAMED is an
    item they aimed at - reporting it back to them as an override would be pam explaining
    the user's own decision to them, and it would fire on the ordinary case of changing
    one's mind about a single card, which is most of what a review pass IS.
    """
    # A lone file, skipped, then approved BY NAME. Nothing is widened; the user aimed at
    # it; the decision really did reverse.
    set_status(baseline, "SOLO.JPG", "skipped")
    reply = decide(baseline, ("SOLO.JPG",), "approve")
    assert reply.status == 200, reply.body
    assert reply.body["companions"] == [], "a lone file widens to nobody"
    assert reply.body["overrode"] == [], "the user named it, so it is not an override"
    assert "decided the other way" not in reply.body["note"]

    # And the mixed case, which is the one the narrow reading actually protects: the user
    # names one member of a unit that was skipped, and the WIDENING catches its sibling.
    # Only the sibling is the override - the named member is not, even though both
    # reversed.
    set_status(baseline, "LIVE_1.MOV", "skipped")
    set_status(baseline, "LIVE_1.JPG", "skipped")
    mixed = decide(baseline, ("LIVE_1.MOV",), "approve")
    assert mixed.status == 200, mixed.body
    assert mixed.body["companions"] == [baseline.ids["LIVE_1.JPG"]]
    assert mixed.body["overrode"] == [baseline.ids["LIVE_1.JPG"]]


def test_the_review_ui_refuses_the_pre_0004_plan_that_apply_refuses(baseline):
    """FINDING F10, and the severity is bounded by the refusal it mirrors.

    Migration 0004 had to declare `unit_key TEXT NOT NULL DEFAULT ''`, so a plan built
    before it legitimately carries '' on every item. `execute/validator.py` refuses such a
    plan outright, so no rename can result - but `pam/webui/` had no equivalent check, and
    `_by_unit` groups by `unit_key`, so '' makes THE WHOLE PLAN ONE UNIT and one keystroke
    widens over every item in it. The user reviews four hundred files and finds out at
    `pam apply`.

    ONE SENTENCE, NOT TWO. `OLD_PLAN_REASON` is imported here deliberately - the property
    under test is that the browser and the CLI say the SAME thing, which is precisely what
    comparing against the shared constant proves. The copy itself is pinned where it is
    defined; what is pinned here is that this endpoint reaches it.

    Every write endpoint, because the check belongs to the shared gate and not to one
    handler: a decision, a bulk decision and a rename all have to meet it.
    """
    conn = connect(baseline.db_path)
    try:
        before = dict(
            conn.execute(
                "SELECT id, unit_key FROM plan_items WHERE plan_id = ?", (baseline.plan_id,)
            ).fetchall()
        )
        assert all(before.values()), "the fixture's plan is already a pre-0004 plan"
        conn.execute(
            "UPDATE plan_items SET unit_key = '' WHERE plan_id = ?", (baseline.plan_id,)
        )
        conn.commit()

        expected = OLD_PLAN_REASON.format(plan_id=baseline.plan_id)
        replies = {
            "decide": decide(baseline, ("SOLO.JPG",), "approve"),
            "decide-filter": post(
                baseline,
                f"/plan/{baseline.plan_id}/decide-filter",
                {"tier": "high", "action": "approve"},
            ),
            "rename": rename(baseline, "SOLO.JPG", "Renamed-By-Hand.JPG"),
        }
        for endpoint, reply in replies.items():
            assert reply.status == 409, (endpoint, reply.status, reply.body)
            assert reply.body["error"] == expected, (endpoint, reply.body)
        assert all(status == "proposed" for status, _ in rows(baseline).values()), (
            "a refused write moved a row"
        )
    finally:
        conn.executemany(
            "UPDATE plan_items SET unit_key = ? WHERE id = ?",
            [(key, item_id) for item_id, key in before.items()],
        )
        conn.commit()
        conn.close()


def test_a_decision_naming_every_member_itself_reports_no_companions_and_no_sentence(
    baseline,
):
    """The ordinary case, and the control on the sentence above.

    A selection that already covered its units added nothing, so rule 7 has nothing to
    disclose - a page that announced a companion decision on every keystroke would be
    noise, and worse, would stop meaning anything on the keystroke that mattered. Two ids
    naming one item are also deduplicated, so `decided` counts ROWS WRITTEN and can never
    exceed them.
    """
    reply = decide(baseline, LIVE_UNIT, "approve")
    assert reply.status == 200, reply.body
    assert reply.body["companions"] == []
    assert reply.body["note"] == ""
    assert reply.body["decided"] == 2

    twice = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide",
        {"item_ids": [baseline.ids["LIVE_1.JPG"]] * 2, "action": "skip"},
    )
    assert twice.status == 200, twice.body
    assert twice.body["decided"] == 2


def test_a_lone_file_is_decided_alone_and_says_nothing(baseline):
    """The containment is not over-broad: a unit of one pulls in nobody."""
    reply = decide(baseline, (HIGH,), "approve")
    assert reply.status == 200, reply.body
    assert reply.body["decided"] == 1
    assert reply.body["companions"] == []
    assert reply.body["note"] == ""
    moved = {name for name, (status, _) in rows(baseline).items() if status != "proposed"}
    assert moved == {HIGH}


def test_a_search_matching_only_the_video_of_a_live_photo_unit_decides_the_photo_too(
    baseline,
):
    """ROUTE 2, and one search plus one bulk click is the whole cost to the user.

    `_matches_q` searches `old_filename` and `new_filename` INCLUDING their extensions, so
    `mov` - the most natural thing a person types to find their videos - selects the `.MOV`
    of every Live Photo pair and none of the photos beside them. A phone library
    is mostly live_photo groups.

    `skip` on purpose: it carries no tier restriction, so this test is about the widening
    alone and not about the Low contract, which has its own tests below. The read endpoint
    is asserted to select ONE of the two first - that assertion IS the route; if a later
    `q` learns to match a companion's partner, this test must say so rather than quietly
    become a second copy of the test above it.
    """
    matched = {card["old_filename"] for card in _cards(baseline, "&q=mov")}
    assert "LIVE_1.MOV" in matched, "the search matches no live-photo video"
    assert "LIVE_1.JPG" not in matched, (
        "`q=mov` now matches the photo as well as its video, so route 2 no longer splits"
        " a unit and this test is not testing what it says"
    )

    reply = post(
        baseline, f"/plan/{baseline.plan_id}/decide-filter", {"q": "mov", "action": "skip"}
    )
    assert reply.status == 200, reply.body
    assert reply.body["companions"] == [baseline.ids["LIVE_1.JPG"]]
    assert reply.body["decided"] == len(matched) + 1
    skipped = {name for name, (status, _) in rows(baseline).items() if status == "skipped"}
    assert skipped == matched | {"LIVE_1.JPG"}


def test_an_event_group_that_leaves_a_sidecar_behind_decides_the_sidecar_too(baseline):
    """ROUTE 3 - THE ONE M8 INTRODUCED - and one click on an event in the nav reaches it.

    `event_key` runs through the item's `event_id`, and spec 8.3 groups PRESENT DATED
    FILES: a sidecar has no `capture` row, so it is in no event, so its `event_key` is
    None while the photo it renames with sits under `2023-03.eN`. The Event level is M8's
    whole on-screen payoff, and it selected the photo and left the `.AAE` behind.

    The read endpoint's one-of-two selection is asserted first and is the route itself.
    """
    group = _event_group(baseline, "2023-03-03")
    shown = [card["old_filename"] for card in _cards(baseline, f"&group={quote(group)}")]
    assert shown == ["SIDE_1.JPG"], (
        f"{group} selects {shown}, so the sidecar is no longer left behind by an event"
        " and this test is not testing the route it names"
    )

    reply = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide-filter",
        {"group": group, "action": "approve"},
    )
    assert reply.status == 200, reply.body
    assert reply.body["companions"] == [baseline.ids["SIDE_1.AAE"]]
    after = rows(baseline)
    assert [after[name][0] for name in SIDECAR_UNIT] == ["approved", "approved"]
    assert {after[name][0] for name in PLANNED if name not in SIDECAR_UNIT} == {"proposed"}


# Route 4's person: a name nothing in the archive's filenames contains, so a match can only
# have come through the people operand.
ROUTE_4_PERSON = "Zephyrine"


def test_a_people_search_matching_only_the_photo_of_a_live_photo_unit_decides_the_video(
    baseline,
):
    """ROUTE 4 - M11 Task 12's NEW NON-UNIFORM FILTER DIMENSION (plan Step 8, I12).

    A Live Photo unit is a photo and a video. `wants()` refuses videos, so the video has no
    faces and no people, so `q=<person>` selects ONE member and not the other. The
    containment is `_widen_to_units` over the whole plan, which already existed; this test is
    the proof it is REACHED for the new dimension. `decided == 2` EXACTLY, and both rows are
    read back: `>= 1` would pass on the split this test exists to forbid.

    The person is planted with the suite's own SQL on the photo's sha256 and removed again,
    so the module-scoped archive leaves this test as it entered it. The fixture's photo is a
    `.JPG` where a real-world library has `.HEIC`; the unit SHAPE (image + `.MOV`) is what
    makes the dimension non-uniform, and the format plays no part in the search.
    """
    conn = connect(baseline.db_path)
    try:
        sha = conn.execute("SELECT sha256 FROM files WHERE rel_path = 'LIVE_1.JPG'").fetchone()[
            0
        ]
        person = conn.execute(
            "INSERT INTO persons (label) VALUES (?)", (ROUTE_4_PERSON,)
        ).lastrowid
        conn.execute(
            "INSERT INTO face_instances (sha256, bbox_json, embedding, det_confidence,"
            " person_id, model_name, model_version) VALUES (?, '{}', x'01', 0.9, ?, 'm', '1')",
            (sha, person),
        )
        conn.commit()
    finally:
        conn.close()
    try:
        matched = {c["old_filename"] for c in _cards(baseline, f"&q={ROUTE_4_PERSON.lower()}")}
        assert matched == {"LIVE_1.JPG"}, (
            f"the people search selects {matched}, not the photo alone, so it no longer"
            " splits a unit and this test is not testing the route it names"
        )

        reply = post(
            baseline,
            f"/plan/{baseline.plan_id}/decide-filter",
            {"q": ROUTE_4_PERSON.lower(), "action": "skip"},
        )
        assert reply.status == 200, reply.body
        assert reply.body["decided"] == 2
        assert reply.body["companions"] == [baseline.ids["LIVE_1.MOV"]]
        after = rows(baseline)
        assert [after[name][0] for name in LIVE_UNIT] == ["skipped", "skipped"]
        assert {after[name][0] for name in PLANNED if name not in LIVE_UNIT} == {"proposed"}
    finally:
        conn = connect(baseline.db_path)
        try:
            conn.execute("DELETE FROM face_instances WHERE person_id = ?", (person,))
            conn.execute("DELETE FROM persons WHERE id = ?", (person,))
            conn.commit()
        finally:
            conn.close()


def test_a_terminal_companion_pulled_in_by_the_widening_refuses_the_whole_batch(baseline):
    """What A1 does when it cannot give the unit ONE decision (spec 8.2's batch atomicity).

    A terminal member cannot be written, so writing the rest of its unit would create the
    very split this amendment exists to prevent - the request refuses whole, exactly as it
    does for a terminal id the user named, and the refusal names the id and its status even
    though the user never mentioned it. Nothing is written, asserted over every row.
    """
    set_status(baseline, "LIVE_1.MOV", "applied")
    before = rows(baseline)
    reply = decide(baseline, ("LIVE_1.JPG",), "approve")
    assert reply.status == 409, reply.body
    assert str(baseline.ids["LIVE_1.MOV"]) in reply.body["error"]
    assert "applied" in reply.body["error"]
    assert rows(baseline) == before


def test_a_terminal_companion_refuses_a_bulk_decision_that_never_selected_it(baseline):
    """The same rule on the endpoint that resolves its own ids, where nobody typed one."""
    set_status(baseline, "LIVE_1.MOV", "applied")
    before = rows(baseline)
    reply = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide-filter",
        {"q": "LIVE_1.JPG", "action": "skip"},
    )
    assert reply.status == 409, reply.body
    assert str(baseline.ids["LIVE_1.MOV"]) in reply.body["error"]
    assert rows(baseline) == before


# --- Accept all (M13 Task 2, F1 R2, M13-D1) ------------------------------------------------

# What `Accept all` must approve over the state `test_accept_all_...` builds, SPELLED BY HAND
# from the fixture and the ruling - never from `naming.accept`: the three undecided non-Low
# units, whole. RAW_1 (2019-07) and LIVE_1 (2020-08) are two members each, VID_PLAIN
# (2021-03) is one, so the three sit under three different date groups.
ACCEPTED_BY_ACCEPT_ALL = ("RAW_1.JPG", "RAW_1.NEF", "LIVE_1.JPG", "LIVE_1.MOV", "VID_PLAIN.MOV")


class _Row(NamedTuple):
    id: int
    unit_key: str
    tier: str
    status: str


def _decidable_rows(live: Live) -> list[_Row]:
    """The plan's rows as `naming.accept` takes them, read with the suite's own SQL."""
    conn = connect(live.db_path)
    try:
        found = conn.execute(
            "SELECT id, unit_key, tier, status FROM plan_items WHERE plan_id = ? ORDER BY id",
            (live.plan_id,),
        ).fetchall()
    finally:
        conn.close()
    return [_Row(int(a), str(b), str(c), str(d)) for a, b, c, d in found]


def test_accept_all_approves_every_undecided_unit_but_low_and_never_a_skip(baseline):
    """F1 R2 + M13-D1, through the real server, and the CLI's selection on the same rows.

    The plan holds: one Low unit (`LOW`, one item), one skipped unit (`SOLO.JPG`), one
    MIXED unit (`SIDE_1.JPG` skipped, its `.AAE` still undecided - only the suite's own SQL
    can build it, since every endpoint decides whole units), one unit already approved
    (`T.JPG`) and the three undecided units above. The mixed unit is the case
    `select_items` + `_widen_to_units` gets wrong: the widening would sweep the skipped
    `.JPG` back to approved.
    """
    set_status(baseline, "SOLO.JPG", "skipped")
    set_status(baseline, "SIDE_1.JPG", "skipped")
    set_status(baseline, DEEP_FILE, "approved")
    snapshot = _decidable_rows(baseline)
    expected_ids = {baseline.ids[name] for name in ACCEPTED_BY_ACCEPT_ALL}
    assert len(expected_ids) == 5

    reply = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide-filter",
        {"action": "approve", "accept_all": True},
    )
    assert reply.status == 200, reply.body
    assert reply.body["decided"] == 5
    assert reply.body["excluded_low"] == 1
    assert rows(baseline) == {
        "RAW_1.JPG": ("approved", "2019-07-04_001.JPG"),
        "RAW_1.NEF": ("approved", "2019-07-04_001.NEF"),
        "LIVE_1.JPG": ("approved", "2020-08-05_001.JPG"),
        "LIVE_1.MOV": ("approved", "2020-08-05_001.MOV"),
        "SOLO.JPG": ("skipped", "2021-09-06_001.JPG"),
        "2018-01-01_VID.MOV": ("proposed", "2021-03-04_001.MOV"),
        "VID_PLAIN.MOV": ("approved", "2021-03-03_001.MOV"),
        "SIDE_1.JPG": ("skipped", "2023-03-03_001.JPG"),
        "SIDE_1.AAE": ("proposed", "2023-03-03_001.AAE"),
        DEEP_FILE: ("approved", "202.JPG"),
    }

    # What `pam apply --all` selects, over the rows as they were BEFORE the POST - compared
    # to the same hand-spelled literal, never to the server's answer.
    acceptance = accept_all(snapshot)
    assert acceptance.ids == expected_ids
    assert acceptance.excluded_low == 1


def test_accept_all_refuses_a_filter_field_by_name_and_writes_nothing(baseline):
    """Accept all's scope is the whole plan; a view sent along is refused, not ignored."""
    before = rows(baseline)
    reply = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide-filter",
        {"action": "approve", "accept_all": True, "group": "2019-07"},
    )
    assert reply.status == 400, reply.body
    assert reply.body["error"].startswith("group: ")
    assert rows(baseline) == before


def test_accept_all_must_be_exactly_true_and_writes_nothing_otherwise(baseline):
    """M13 Task 2 review (M2395): `accept_all: false` is not a request to accept all. A body
    whose flag is anything but the JSON `true` is a 400 naming the flag, and writes nothing."""
    before = rows(baseline)
    for flag in (False, "true", 1):
        reply = post(
            baseline,
            f"/plan/{baseline.plan_id}/decide-filter",
            {"action": "approve", "accept_all": flag},
        )
        assert reply.status == 400, (flag, reply.body)
        assert reply.body["error"].startswith("accept_all: "), reply.body
    assert rows(baseline) == before


def test_accept_all_only_approves_and_writes_nothing_for_another_action(baseline):
    """M13 Task 2 review (M2396): Accept all with `skip` or `reset` is refused by name - the
    branch writes APPROVE whatever the action said, so the guard is all that stands between a
    `skip` and an approval of the whole plan."""
    before = rows(baseline)
    for action in ("skip", "reset"):
        reply = post(
            baseline,
            f"/plan/{baseline.plan_id}/decide-filter",
            {"action": action, "accept_all": True},
        )
        assert reply.status == 400, (action, reply.body)
        assert reply.body["error"].startswith("action: "), reply.body
    assert rows(baseline) == before


# M13 V3. Every field design 8.2 lets `decide-filter`'s body carry, SPELLED FROM THE SPEC'S
# line `POST /plan/{id}/decide-filter {group?, tier?, status?, attention?, q?, sort?, action}
# | {action: 'approve', accept_all: true}` - never read from production's `FILTER_FIELDS`
# (an oracle built from the definition it checks; the M6 `ENRICHERS` lesson).
# M14 Task 3 (the controller's ruling, OD-1): the shortcuts write through `decide-filter` with
# `{"action": "approve", "shortcut": true, "list_group"?: <U25 key>, ...view}` - two names more.
SPEC_BODY_FIELDS = (
    "group",
    "tier",
    "status",
    "attention",
    "q",
    "sort",
    "action",
    "accept_all",
    "shortcut",
    "list_group",
)
SPEC_NOT_FILTERS = {"action", "accept_all", "shortcut", "list_group"}
assert SPEC_BODY_FIELDS, "the spelled list of 8.2's body fields is empty"


def test_the_known_body_fields_are_exactly_design_8_2s():
    assert set(decide_api.BODY_FIELDS) == set(SPEC_BODY_FIELDS)
    assert set(decide_api.FILTER_FIELDS) == set(SPEC_BODY_FIELDS) - SPEC_NOT_FILTERS


@pytest.mark.parametrize("branch", ["filter", "accept_all"])
def test_an_unknown_body_field_is_refused_by_name_and_never_reaches_the_writer(
    baseline, monkeypatch, branch
):
    """M13 V3 (16A's L1): a mistyped filter must not widen a bulk decision. Watches the
    writer itself, with a positive control showing the spy sees a legal request's write."""
    calls: list[list[int]] = []
    real = decide_api._write_decisions

    def spy(conn, ids, action):
        calls.append(list(ids))
        return real(conn, ids, action)

    monkeypatch.setattr(decide_api, "_write_decisions", spy)
    path = f"/plan/{baseline.plan_id}/decide-filter"
    extra = {"accept_all": True} if branch == "accept_all" else {}
    before = rows(baseline)
    for bad in ("colour", "zzz_unknown"):
        reply = post(baseline, path, {"action": "approve", **extra, bad: "x"})
        assert reply.status == 400, reply.body
        assert reply.body["error"] == f'{bad}: "{bad}" is not something pam can filter by.'
    # several unknown fields: the first in sorted order is named
    reply = post(baseline, path, {"action": "approve", **extra, "zz": 1, "aa": 2})
    assert reply.body["error"].startswith("aa: "), reply.body
    assert calls == [] and rows(baseline) == before
    # positive control: the spy does see a legal request's write
    ok = post(baseline, path, {"action": "approve", **extra})
    assert ok.status == 200, ok.body
    assert calls, "the spy never saw a legal write"


def test_tier_trust_score_and_attention_are_uniform_across_every_companion_unit(baseline):
    """WHY THE PRESET BUTTONS COULD NEVER SPLIT A UNIT, pinned at last (Task 2 Step 5).

    `naming/planner.py` computes a tier, a trust score and a tokens document ONCE PER UNIT
    from the primary's facts and copies them to every member; `attention` is derived from
    those. So the two filter dimensions the presets use are unit-uniform by construction,
    and `Approve High` was never one of the three routes to a split unit. The M8 review
    called that "a real and load-bearing containment" and measured that NOTHING asserted
    it - which means the day a planner change makes a companion's tier its own, the
    most-used bulk path in the product silently becomes route 4.

    It is also what makes `decide-filter`'s Low-tier exclusion and A1's widening agree
    rather than contradict each other, so the two tests below rest on this one.

    The count of multi-member units is asserted from `COMPANION_UNITS`, so a plan that
    stopped grouping anything fails here rather than passing over nothing to check.
    """
    units: dict[str, list[dict[str, Any]]] = {}
    for card in _cards(baseline):
        units.setdefault(card["unit_key"], []).append(card)
    multi = [members for members in units.values() if len(members) > 1]
    assert len(multi) == len(COMPANION_UNITS)
    for members in multi:
        names = sorted(member["old_filename"] for member in members)
        for field in ("tier", "trust_score", "attention"):
            assert len({member[field] for member in members}) == 1, (
                f"{names} disagree about {field}, so a filter on it selects part of a"
                " companion unit - the preset buttons are now route 4 to a split unit"
            )


def test_a_broad_approve_leaves_a_WHOLE_unit_alone_when_ANY_member_is_low_tier(baseline):
    """WHERE THE LOW-TIER CONTRACT AND A1 MEET, built by hand because nothing produces it.

    Vision 6a.3 says no bulk `approve` may touch a `low` name; spec 7.3 rev 15 says a unit
    is decided as a unit. Dropping the low ITEM out of a widened unit would honour the
    first by breaking the second, so `decide-filter` drops the whole UNIT - and reports
    every item it dropped, because `excluded_low` is what the page prints.

    The planner cannot make a unit whose members disagree about their tier (the test above
    is why), so the case is FORCED with the suite's own SQL. That is M8's M993 lesson
    applied: where a name says the guard holds "when ANY member is low", build the case
    where any-member and every-member disagree, by hand if the pipeline never does.

    Without the unit scoping this passes at three-quarters: `LIVE_1.MOV` is excluded and
    `LIVE_1.JPG` is approved, one unit decided two ways, and `excluded_low` reads 1.
    """
    set_tier(baseline, "LIVE_1.MOV", "low")
    reply = post(baseline, f"/plan/{baseline.plan_id}/decide-filter", {"action": "approve"})
    assert reply.status == 200, reply.body

    after = rows(baseline)
    assert [after[name][0] for name in LIVE_UNIT] == ["proposed", "proposed"]
    assert after[LOW][0] == "proposed"
    # The two live-photo members plus the archive's genuinely `low` lone video.
    assert reply.body["excluded_low"] == 3
    assert reply.body["decided"] == len(PLANNED) - 3
    assert after[HIGH][0] == "approved"


def test_a_low_member_the_WIDENING_pulled_in_excludes_its_whole_unit(baseline):
    """The mirror of the test above, and the case the ordering argument is really about.

    Above, the filter selects the `low` member itself. Here the filter selects only the
    `high` one - `q=LIVE_1.JPG` matches one filename and no other - and the `low` member
    arrives ONLY because A1 widened the selection to the unit. So the exclusion is being
    asked about an item the filter never chose, which is the one shape where excluding
    before widening and excluding after it give different answers.

    Both members must be left alone and BOTH must be counted. `excluded_low` is what the
    page prints: exclude before widening and the user whose search matched one member is
    told 1 file was held back when 2 were, which is the number they reconcile against
    their own eyes. M1061 is that mutation, and this is the only test that dies on it.
    """
    set_tier(baseline, "LIVE_1.MOV", "low")
    assert [card["old_filename"] for card in _cards(baseline, "&q=LIVE_1.JPG")] == [
        "LIVE_1.JPG"
    ], "the filter no longer selects one member of the unit, so this test proves nothing"

    reply = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide-filter",
        {"q": "LIVE_1.JPG", "action": "approve"},
    )

    assert reply.status == 200, reply.body
    after = rows(baseline)
    assert [after[name][0] for name in LIVE_UNIT] == ["proposed", "proposed"]
    assert reply.body["decided"] == 0
    assert reply.body["excluded_low"] == 2


def test_an_explicit_low_filter_still_refuses_outright_when_its_unit_is_mixed(baseline):
    """The first half of the contract is untouched by A1: the refusal is before resolution.

    A `tier: 'low'` approve never gets as far as selecting anything, so there is nothing to
    widen and nothing is written - including the `high`-tier partner the widening would
    otherwise have reached.

    The name says "when its unit is mixed", so the mixing is ASSERTED rather than assumed.
    Make `set_tier` a no-op (M1049) and this test still passed: the unit was uniform, the
    refusal fired for the ordinary reason, and the half of the sentence that names this
    case was doing nothing. A test whose fixture cannot express its own name is M993's
    finding, and it is closed here by reading the tiers back.
    """
    set_tier(baseline, "LIVE_1.MOV", "low")
    tiers = {card["old_filename"]: card["tier"] for card in _cards(baseline)}
    assert {tiers[name] for name in LIVE_UNIT} == {"low", "high"}, (
        "the unit is not mixed, so this test is not testing what it is named for"
    )
    before = rows(baseline)
    reply = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide-filter",
        {"tier": "low", "action": "approve"},
    )
    assert reply.status == 400, reply.body
    assert rows(baseline) == before


# --- the body, as a wire format ------------------------------------------------------


@pytest.mark.parametrize("action", ["", "Approve", "delete", None, 3])
def test_an_action_that_is_not_one_of_the_three_is_a_400_naming_it(baseline, action):
    reply = post(
        baseline,
        f"/plan/{baseline.plan_id}/decide",
        {"item_ids": [], "action": action},
    )
    assert reply.status == 400
    assert reply.body["error"].startswith("action:")


@pytest.mark.parametrize("ids", [None, 5, "1,2", {"1": 1}, [1, "2"], [True], [1.5]])
def test_item_ids_that_are_not_a_list_of_ids_are_a_400_naming_it(baseline, ids):
    reply = post(
        baseline, f"/plan/{baseline.plan_id}/decide", {"item_ids": ids, "action": "skip"}
    )
    assert reply.status == 400
    assert reply.body["error"].startswith("item_ids:")


@pytest.mark.parametrize("payload", [b"{not json", b"[1, 2]", b"null", b'"text"', b"7"])
def test_a_body_that_is_not_a_json_object_is_a_400_naming_the_body(baseline, payload):
    """`[1, 2]` and `null` are both valid JSON and neither can carry `action`, so the
    page is told which rather than watching every field read as missing."""
    reply = post_raw(baseline, f"/plan/{baseline.plan_id}/decide", payload)
    assert reply.status == 400
    assert reply.body["error"].startswith("body:")


def test_a_malformed_body_on_an_unrouted_path_is_still_the_silent_empty_404(baseline):
    """Amendment A3: the empty 404 exists so a probing page learns nothing. A 400 saying
    "that is not JSON" would tell it pam is listening - so the body is parsed only after
    a route has matched."""
    conn = HTTPConnection(baseline.server.host, baseline.server.port, timeout=20)
    try:
        conn.request(
            "POST",
            f"/t/{baseline.token}/api/plan/1/no-such-endpoint",
            body=b"{not json",
            headers={"Content-Length": "9"},
        )
        response = conn.getresponse()
        assert (response.status, response.read()) == (404, b"")
    finally:
        conn.close()


def test_a_post_without_the_token_gets_the_silent_empty_404(baseline):
    """The write API's talkative refusals must not have widened amendment A3's gate."""
    conn = HTTPConnection(baseline.server.host, baseline.server.port, timeout=20)
    payload = json.dumps({"item_ids": [], "action": "approve"}).encode()
    try:
        conn.request(
            "POST",
            f"/t/not-the-token/api/plan/{baseline.plan_id}/decide",
            body=payload,
            headers={"Content-Length": str(len(payload))},
        )
        response = conn.getresponse()
        assert (response.status, response.read()) == (404, b"")
    finally:
        conn.close()


def test_a_second_request_on_the_same_connection_is_read_correctly(baseline):
    """The body must ALWAYS be read, routed or not: HTTP/1.1 keeps the connection open
    and an undrained body is read as the next request line."""
    conn = HTTPConnection(baseline.server.host, baseline.server.port, timeout=20)
    payload = json.dumps({"item_ids": [], "action": "approve"}).encode()
    try:
        conn.request(
            "POST",
            f"/t/{baseline.token}/api/nowhere",
            body=payload,
            headers={"Content-Length": str(len(payload))},
        )
        assert conn.getresponse().read() == b""
        conn.request("GET", f"/t/{baseline.token}/api/plan/{baseline.plan_id}")
        second = conn.getresponse()
        assert second.status == 200
        assert json.loads(second.read())["plan_id"] == baseline.plan_id
    finally:
        conn.close()


# --- rename: the companion unit is the subject ---------------------------------------


def _tier_of(live: Live, name: str) -> str:
    conn = connect(live.db_path)
    try:
        return next(
            item.tier
            for item in repo.plan_rows(conn, live.plan_id, floor=FACES_FLOOR)
            if item.old_filename == name
        )
    finally:
        conn.close()


def rename(live: Live, name: str, new_filename: str) -> Reply:
    return post(live, f"/item/{live.ids[name]}/rename", {"new_filename": new_filename})


@pytest.mark.parametrize(
    ("clicked", "partner", "submitted", "partner_name"),
    [
        # Amendment A6: validate against the CLICKED member's extension only. A unit's
        # members have different extensions by construction, so both directions of a
        # RAW+JPEG pair and of a live-photo pair must work.
        ("RAW_1.JPG", "RAW_1.NEF", "Sunset-Ridge.JPG", "Sunset-Ridge.NEF"),
        ("RAW_1.NEF", "RAW_1.JPG", "Sunset-Ridge.NEF", "Sunset-Ridge.JPG"),
        ("LIVE_1.JPG", "LIVE_1.MOV", "Beach-Day.JPG", "Beach-Day.MOV"),
        ("LIVE_1.MOV", "LIVE_1.JPG", "Beach-Day.MOV", "Beach-Day.JPG"),
    ],
)
def test_a_rename_applies_the_stem_to_every_member_keeping_each_extension(
    baseline, clicked, partner, submitted, partner_name
):
    """Spec 8.2 rev 12: the stem goes to EVERY member, each keeping ITS OWN proposed
    extension byte-for-byte, case included (spec 5.4 rev 6)."""
    reply = rename(baseline, clicked, submitted)
    assert reply.status == 200, reply.body
    after = rows(baseline)
    assert after[clicked][1] == submitted
    assert after[partner][1] == partner_name
    assert {member["new_filename"] for member in reply.body["members"]} == {
        submitted,
        partner_name,
    }


def test_a_rename_approves_every_member_of_the_unit(baseline):
    """Spec 8.2: a successful rename writes BOTH axes - UX 5a's `C` is a decision, and
    edit-then-approve-again would double-key every edit in a 900-item review."""
    reply = rename(baseline, "RAW_1.JPG", "Sunset-Ridge.JPG")
    assert reply.status == 200, reply.body
    after = rows(baseline)
    assert after["RAW_1.JPG"][0] == "approved"
    assert after["RAW_1.NEF"][0] == "approved"


def test_a_rename_leaves_every_other_unit_alone(baseline):
    before = rows(baseline)
    assert rename(baseline, "RAW_1.JPG", "Sunset-Ridge.JPG").status == 200
    after = rows(baseline)
    for name in before:
        if not name.startswith("RAW_1."):
            assert after[name] == before[name], name


@pytest.mark.parametrize(
    "submitted",
    ["Sunset-Ridge.jpg", "Sunset-Ridge.JPEG", "Sunset-Ridge", "Sunset-Ridge.", ".JPG"],
)
def test_a_submitted_name_that_does_not_keep_the_clicked_extension_is_a_400(
    baseline, submitted
):
    """Byte-for-byte, case included: pam is not the tool that decides a `.JPG` should
    have been a `.jpg`. A bare stem and a bare extension are refused for the same reason
    - the endpoint edits stems and the extension is how it knows where the stem ends.

    THE REASON IS ASSERTED, NOT ONLY THE STATUS. All five rows are spec 8.2 step 2, and
    step 4's "empty after sanitization" is a different rule about a different input - but
    both answer 400 with a sentence beginning `new_filename:`, so a status-and-prefix
    assertion cannot tell them apart. `.JPG` is the row that turns on it: with the
    bare-extension half of the guard removed it sails into step 4 and is refused for the
    wrong reason, with the whole suite green (M667).
    """
    before = rows(baseline)
    reply = rename(baseline, "RAW_1.JPG", submitted)
    assert reply.status == 400
    assert reply.body["error"].startswith("new_filename:")
    assert "extension" in reply.body["error"], reply.body
    assert ".JPG" in reply.body["error"], reply.body
    assert rows(baseline) == before


def test_a_case_only_stem_change_onto_the_units_own_disk_name_succeeds(baseline):
    """Spec 8.2: every member of the target unit comes out of BOTH namespaces first, or a
    legitimate re-edit collides with itself - and a case-only change is legal.

    `raw_1` against the on-disk `RAW_1.JPG`/`RAW_1.NEF` is the sharpest form of it: the
    proposed names are case-insensitively identical to names really on disk, and those
    names are the unit's own."""
    reply = rename(baseline, "RAW_1.JPG", "raw_1.JPG")
    assert reply.status == 200, reply.body
    after = rows(baseline)
    assert (after["RAW_1.JPG"][1], after["RAW_1.NEF"][1]) == ("raw_1.JPG", "raw_1.NEF")


def test_editing_a_unit_twice_to_the_same_stem_succeeds(baseline):
    """The unit's own CURRENT proposed names come out of the namespace too, so a re-edit
    is not a collision with the edit before it."""
    assert rename(baseline, "RAW_1.JPG", "Sunset-Ridge.JPG").status == 200
    reply = rename(baseline, "RAW_1.JPG", "Sunset-Ridge.JPG")
    assert reply.status == 200, reply.body
    assert rows(baseline)["RAW_1.JPG"][1] == "Sunset-Ridge.JPG"


def test_a_stem_taken_by_another_item_in_this_plan_is_refused_and_writes_nothing(
    baseline,
):
    """Spec 6.3 rule 2's namespace, plan half: another item's proposed name in the same
    directory. The rows are asserted, not just the status code."""
    before = rows(baseline)
    reply = rename(baseline, "RAW_1.JPG", f"{PLANNED[HIGH][:-4]}.JPG")
    assert reply.status == 409
    assert PLANNED[HIGH] in reply.body["error"]
    assert rows(baseline) == before


def test_a_stem_taken_by_a_file_on_disk_is_refused_and_writes_nothing(baseline):
    """Spec 6.3 rule 2's namespace, disk half. `2015-05-05_001.JPG` is already conforming,
    so it is on disk and in `files` but in NO plan item - a refusal against it can only
    have come from the disk half of the namespace."""
    assert "2015-05-05_001.JPG" not in rows(baseline)
    before = rows(baseline)
    reply = rename(baseline, "RAW_1.JPG", "2015-05-05_001.JPG")
    assert reply.status == 409
    assert "2015-05-05_001.JPG" in reply.body["error"]
    assert rows(baseline) == before


def test_a_collision_on_ONE_member_refuses_the_whole_unit(baseline):
    """The disk name `2015-05-05_001.JPG` clashes with the unit's `.JPG` member and not
    with its `.NEF` - and spec 7.3's atomicity means the unit renames together or not at
    all, so the `.NEF` must not be written either."""
    before = rows(baseline)
    assert rename(baseline, "RAW_1.NEF", "2015-05-05_001.NEF").status == 409
    assert rows(baseline) == before


def test_a_stem_that_sanitizes_to_nothing_is_a_400_in_plain_language(baseline):
    """Spec 8.2: empty after sanitization -> a refusal with a plain-language reason."""
    before = rows(baseline)
    reply = rename(baseline, "RAW_1.JPG", "???.JPG")
    assert reply.status == 400
    assert "Windows" in reply.body["error"]
    assert rows(baseline) == before


def test_a_stem_is_re_sanitized_and_the_response_returns_what_was_stored(baseline):
    """Spec 8.2: silent normalization is fine, silent DIFFERENCE is not - so the page is
    told the name that is now in the catalog. Spec 6.2 rules 1-3 on one input: a
    whitespace run becomes `-`, `#` is outside the allowlist and goes, a repeated
    separator collapses, and the edges are stripped."""
    reply = rename(baseline, "RAW_1.JPG", "  Lake #2__Tahoe--.JPG")
    assert reply.status == 200, reply.body
    stored = rows(baseline)["RAW_1.JPG"][1]
    assert stored == "Lake-2_Tahoe.JPG"
    assert [
        m["new_filename"] for m in reply.body["members"] if m["id"] == baseline.ids["RAW_1.JPG"]
    ] == [stored]


def test_a_reserved_device_stem_comes_back_suffixed(baseline):
    """Spec 6.2 rule 4: `con` is CON to Win32 whatever this build of Windows allows."""
    reply = rename(baseline, "RAW_1.JPG", "con.JPG")
    assert reply.status == 200, reply.body
    assert rows(baseline)["RAW_1.JPG"][1] == "con-x.JPG"


def test_a_stem_that_truncates_INTO_a_reserved_name_still_leaves_a_legal_name(baseline):
    """M3 review condition C1: `Console` cut to three characters is `Con`, so spec 6.2
    rule 4 must be re-applied AFTER rule 5's cut - and there is no room for `-x`, so the
    head is shortened instead (spec 6.1 rev 4).

    The assertion is spec 6.1's POSTCONDITION, spelled out here rather than a single
    expected string: non-empty, edge-clean, never a reserved head, inside the budget.
    """
    reply = rename(baseline, DEEP_FILE, "Console.JPG")
    assert reply.status == 200, reply.body
    stored = rows(baseline)[DEEP_FILE][1]
    stem, _, ext = stored.rpartition(".")
    assert stem
    assert ext == "JPG"
    assert stem.strip(".-_") == stem
    assert reserved_head(stem) is None
    assert full_path_length(str(baseline.root), baseline.deep_dir, stored) <= MAX_FULL_PATH


def test_rename_refuses_a_terminal_member_and_writes_nothing(baseline):
    """Spec 8.2: no member terminal, else the whole unit is refused."""
    set_status(baseline, "RAW_1.NEF", "applied")
    before = rows(baseline)
    reply = rename(baseline, "RAW_1.JPG", "Sunset-Ridge.JPG")
    assert reply.status == 409
    assert str(baseline.ids["RAW_1.NEF"]) in reply.body["error"]
    assert rows(baseline) == before


def test_rename_refuses_a_plan_that_is_not_open(baseline):
    conn = connect(baseline.db_path)
    try:
        stranger = repo.plan_rows(conn, baseline.stale_plan, floor=FACES_FLOOR)[0]
    finally:
        conn.close()
    reply = post(
        baseline, f"/item/{stranger.id}/rename", {"new_filename": stranger.new_filename}
    )
    assert reply.status == 409
    assert "superseded" in reply.body["error"]


def test_rename_on_an_unknown_item_is_a_404_that_says_so(baseline):
    reply = post(baseline, "/item/987654/rename", {"new_filename": "x.JPG"})
    assert reply.status == 404
    assert "987654" in reply.body["error"]


@pytest.mark.parametrize("submitted", [None, "", "   ", 7, ["a.JPG"]])
def test_a_missing_or_unusable_new_filename_is_a_400_naming_it(baseline, submitted):
    reply = post(
        baseline,
        f"/item/{baseline.ids['RAW_1.JPG']}/rename",
        {"new_filename": submitted},
    )
    assert reply.status == 400
    assert reply.body["error"].startswith("new_filename:")


# --- restore, and the two axes -------------------------------------------------------


def test_reset_leaves_an_edited_name_alone(baseline):
    """Spec 8.2 rev 12: decision state and name state are SEPARATE AXES. `reset` changes
    status only - an edited name survives it, and `restore` is the filename operation."""
    assert rename(baseline, "RAW_1.JPG", "Sunset-Ridge.JPG").status == 200
    assert decide(baseline, ("RAW_1.JPG", "RAW_1.NEF"), "reset").status == 200
    after = rows(baseline)
    assert after["RAW_1.JPG"] == ("proposed", "Sunset-Ridge.JPG")
    assert after["RAW_1.NEF"] == ("proposed", "Sunset-Ridge.NEF")


def test_restore_puts_the_planners_name_back_for_every_member(baseline):
    """Amendment A6: `new_filename := generated_filename`, for every member of the unit."""
    assert rename(baseline, "RAW_1.JPG", "Sunset-Ridge.JPG").status == 200
    reply = post(baseline, f"/item/{baseline.ids['RAW_1.JPG']}/restore")
    assert reply.status == 200, reply.body
    after = rows(baseline)
    assert after["RAW_1.JPG"][1] == PLANNED["RAW_1.JPG"]
    assert after["RAW_1.NEF"][1] == PLANNED["RAW_1.NEF"]


def test_the_unit_response_carries_the_edited_flag_for_every_member(baseline):
    """Spec 8.2 rev 14, on the ONE path that changes the predicate.

    The page repaints a unit straight out of this response and does not re-fetch the
    card, so a response that carried the two new filenames and not the new `edited`
    would leave the `↺` control, the trust display and the token chips reading a stale
    predicate until the next full reload - and rename/restore are the only two writes
    that can change it, so this is exactly where a stale flag would be wrong.

    Both members, not just the clicked one: a rename edits the UNIT's stem (amendment
    A6), so the companion's card face must stop showing the planner's provenance too.
    """
    renamed = rename(baseline, "RAW_1.JPG", "Sunset-Ridge.JPG")
    assert renamed.status == 200, renamed.body
    assert {member["old_filename"]: member["edited"] for member in renamed.body["members"]} == {
        "RAW_1.JPG": True,
        "RAW_1.NEF": True,
    }
    restored = post(baseline, f"/item/{baseline.ids['RAW_1.JPG']}/restore")
    assert restored.status == 200, restored.body
    assert {
        member["old_filename"]: member["edited"] for member in restored.body["members"]
    } == {
        "RAW_1.JPG": False,
        "RAW_1.NEF": False,
    }


def test_restore_leaves_the_status_untouched(baseline):
    """Amendment A6: statuses untouched. A rename approves, so restoring after one must
    leave `approved` standing - restoring is not an undecision."""
    assert rename(baseline, "RAW_1.JPG", "Sunset-Ridge.JPG").status == 200
    assert post(baseline, f"/item/{baseline.ids['RAW_1.JPG']}/restore").status == 200
    after = rows(baseline)
    assert after["RAW_1.JPG"][0] == "approved"
    assert after["RAW_1.NEF"][0] == "approved"


def test_restore_leaves_a_skipped_unit_skipped(baseline):
    set_status(baseline, "RAW_1.JPG", "skipped")
    set_status(baseline, "RAW_1.NEF", "skipped")
    assert post(baseline, f"/item/{baseline.ids['RAW_1.JPG']}/restore").status == 200
    after = rows(baseline)
    assert after["RAW_1.JPG"][0] == "skipped"
    assert after["RAW_1.NEF"][0] == "skipped"


def test_restore_refuses_a_terminal_member_and_writes_nothing(baseline):
    set_status(baseline, "RAW_1.NEF", "undone")
    before = rows(baseline)
    reply = post(baseline, f"/item/{baseline.ids['RAW_1.JPG']}/restore")
    assert reply.status == 409
    assert rows(baseline) == before


def test_restore_refuses_a_plan_that_is_not_open(baseline):
    conn = connect(baseline.db_path)
    try:
        stranger = repo.plan_rows(conn, baseline.stale_plan, floor=FACES_FLOOR)[0].id
    finally:
        conn.close()
    reply = post(baseline, f"/item/{stranger}/restore")
    assert reply.status == 409
    assert "superseded" in reply.body["error"]


def test_restore_on_an_unknown_item_is_a_404_that_says_so(baseline):
    reply = post(baseline, "/item/987654/restore")
    assert reply.status == 404
    assert "987654" in reply.body["error"]


def test_restore_refuses_a_collision_it_would_otherwise_reintroduce(baseline):
    """M8 Task 2, C1 - design spec rev 13 / §8.2. INVERTS the old characterization test.

    Spec 6.3 rule 2 makes a plan's proposed names unique per directory, case-insensitively.
    The planner establishes that and `rename` preserves it. Before this task, `restore` was
    the one writer that could break it: `generated_filename` sits in NEITHER half of
    `_refuse_collisions`' namespace once another unit has taken it (the plan half reads the
    other items' `new_filename`, moved away by step 1 below; the disk half holds only names
    really on disk, which a generated name by definition is not).

    The old docstring justified the missing check with "putting it back can only return the
    plan to a state it was already in" - false, and this project's seventh measured "this
    cannot happen" docstring (the first that was wrong, not merely untested). Reproduced
    2026-08-21 and recorded in `docs/implementation-notes/m8-progress.md`'s Task 2 section
    before this fix landed. `restore` now runs the identical check `rename` does, over the
    identical namespace, and refuses with spec 8.2's own restore-specific copy.
    """
    # 1. Move unit A off its generated name, freeing that name inside the plan.
    assert rename(baseline, "RAW_1.JPG", "Sunset-Ridge.JPG").status == 200
    # 2. Unit B may now take it - and this success IS the mechanism, so it is asserted
    #    rather than merely performed. Refusing here would close the hole at step 2.
    taken = PLANNED["RAW_1.JPG"]
    assert rename(baseline, "LIVE_1.JPG", taken).status == 200
    before = rows(baseline)
    # 3. Restore A. `new_filename := generated_filename` would collide with unit B's -
    #    refused before any row is written.
    reply = post(baseline, f"/item/{baseline.ids['RAW_1.JPG']}/restore")
    assert reply.status == 409
    assert "Restore or rename that one first" in reply.body["error"]
    assert rows(baseline) == before, "a refusal must leave every row byte-identical"


# Spec 8.2 and M8 Task 2 Step 3: the clash refusal's closing remedy is PART OF THE
# CONDITION, not a nicety, and the two endpoints owe DIFFERENT ones - a user who pressed
# restore typed no name and cannot act on "Try a different name". Spelled out here from
# the spec side rather than read off `write_api`'s `clash_reason` arguments, so the
# expectation cannot follow a mutation of the thing it checks.
RENAME_REMEDY = "Try a different name."
RESTORE_REMEDY = "Restore or rename that one first."


def test_each_clash_refusal_carries_its_own_remedy_and_not_the_others(baseline):
    """M809 is why this exists. `_refuse_collisions` took `clash_reason` from its caller
    in M8 Task 2, and until this test only ONE of the two arguments was pinned: crossing
    them the other way - `rename` telling a user who typed a name to "Restore or rename
    that one first" - left the whole suite green.

    Both halves are asserted in both directions, presence AND absence, because "contains
    the right sentence" alone still passes for an error that contains both.
    """
    taken = rename(baseline, "RAW_1.JPG", f"{PLANNED[HIGH][:-4]}.JPG")
    assert taken.status == 409, taken.body
    assert RENAME_REMEDY in taken.body["error"], taken.body
    assert RESTORE_REMEDY not in taken.body["error"], taken.body

    # The Task 2 reproduction, driven over HTTP: A off its generated name, B onto it,
    # restore A. The refusal above wrote nothing, so the fixture is still the baseline.
    assert rename(baseline, "RAW_1.JPG", "Sunset-Ridge.JPG").status == 200
    assert rename(baseline, "LIVE_1.JPG", PLANNED["RAW_1.JPG"]).status == 200
    put_back = post(baseline, f"/item/{baseline.ids['RAW_1.JPG']}/restore")
    assert put_back.status == 409, put_back.body
    assert RESTORE_REMEDY in put_back.body["error"], put_back.body
    assert RENAME_REMEDY not in put_back.body["error"], put_back.body


# M14 Task 5g (Z3, design 6.3 rule 2): the namespace holds STEMS as well as names. A stem whose
# spec 5.4 grouping key another file in the folder holds would be bound to that file by the next
# scan (finding 5f-F1, vision S3), so `rename` and `restore` refuse it and name the holder. The
# sentence is spelled here by hand, with each endpoint's remedy from the constants above.
def _stem_refusal(names: str, holder: str, remedy: str) -> str:
    return (
        f"pam changed nothing. {names} would share a name with {holder} in that folder,"
        f" and pam would then treat them as one photo. {remedy}"
    )


def _spy_name_writes(monkeypatch) -> list[tuple]:
    """Watch the dangerous CALL (CLAUDE.md, M697): every write of a proposed name."""
    calls: list[tuple] = []
    real = repo.set_plan_item_new_filename

    def spy(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(repo, "set_plan_item_new_filename", spy)
    return calls


def test_a_stem_whose_key_a_file_on_disk_holds_is_refused_by_name_and_never_written(
    baseline, monkeypatch
):
    """`2015-05-05_001.MOV` is a free NAME beside `2015-05-05_001.JPG` (already conforming, on
    disk, in no plan item) and NOT a free stem: the next scan would make the two one Live Photo.
    Before Z3 this rename was accepted."""
    before = rows(baseline)
    writes = _spy_name_writes(monkeypatch)
    reply = rename(baseline, MEDIUM, "2015-05-05_001.MOV")
    assert reply.status == 409, reply.body
    assert reply.body["error"] == _stem_refusal(
        "2015-05-05_001.MOV", "2015-05-05_001.JPG", RENAME_REMEDY
    )
    assert writes == [], "a refused rename wrote a name"
    assert rows(baseline) == before


def test_restore_refuses_a_stem_whose_key_another_item_now_holds(baseline, monkeypatch):
    """The restore half. Unit A (the RAW+JPEG pair) moves off its generated stem; a lone video
    then takes that STEM under its own extension - a free name and, with A gone, a free stem, so
    it is accepted. Restoring A would put `2019-07-04_001.JPG/.NEF` beside the video's
    `2019-07-04_001.MOV`: refused, naming the video, with restore's own remedy."""
    assert rename(baseline, "RAW_1.JPG", "Sunset-Ridge.JPG").status == 200
    took = rename(baseline, MEDIUM, f"{PLANNED['RAW_1.JPG'][:-4]}.MOV")
    assert took.status == 200, took.body
    before = rows(baseline)
    writes = _spy_name_writes(monkeypatch)
    reply = post(baseline, f"/item/{baseline.ids['RAW_1.JPG']}/restore")
    assert reply.status == 409, reply.body
    assert reply.body["error"] == _stem_refusal(
        "2019-07-04_001.JPG, 2019-07-04_001.NEF", "2019-07-04_001.MOV", RESTORE_REMEDY
    )
    assert writes == [], "a refused restore wrote a name"
    assert rows(baseline) == before


def test_both_endpoints_read_the_archives_ignore_stem_suffixes(baseline, monkeypatch):
    """`scan.ignore_stem_suffixes` is part of the key (5.4), so the UI must read the bound
    archive's value, as the planner does. Under `["_edit"]`, `2015-05-05_001_edit.MOV` shares
    the key of `2015-05-05_001.JPG`; under the default it does not. Each endpoint is driven both
    ways, so dropping either endpoint's argument fails here."""
    edited = dataclasses.replace(baseline.server.config, ignore_stem_suffixes=("_edit",))

    monkeypatch.setattr(baseline.server, "_config", edited)
    refused = rename(baseline, MEDIUM, "2015-05-05_001_edit.MOV")
    assert refused.status == 409, refused.body
    assert refused.body["error"] == _stem_refusal(
        "2015-05-05_001_edit.MOV", "2015-05-05_001.JPG", RENAME_REMEDY
    )
    monkeypatch.undo()
    assert rename(baseline, MEDIUM, "2015-05-05_001_edit.MOV").status == 200

    # restore: the RAW pair off its stem, the video onto `<that stem>_edit`, then put it back.
    assert rename(baseline, "RAW_1.JPG", "Sunset-Ridge.JPG").status == 200
    assert rename(baseline, MEDIUM, "2019-07-04_001_edit.MOV").status == 200
    monkeypatch.setattr(baseline.server, "_config", edited)
    put_back = post(baseline, f"/item/{baseline.ids['RAW_1.JPG']}/restore")
    assert put_back.status == 409, put_back.body
    assert put_back.body["error"] == _stem_refusal(
        "2019-07-04_001.JPG, 2019-07-04_001.NEF", "2019-07-04_001_edit.MOV", RESTORE_REMEDY
    )
    monkeypatch.undo()
    assert post(baseline, f"/item/{baseline.ids['RAW_1.JPG']}/restore").status == 200


def test_a_units_proposed_name_does_not_free_a_key_its_files_do_not_hold(baseline, monkeypatch):
    """The Task 5g review's Q2, under Z3.1. Only the unit's own files ON DISK free their key, so
    the UI's "own" is the members' current names, never the proposal: under the default config
    `2015-05-05_001_edit.MOV` is accepted (its key is its own); under `["_edit"]` the same name
    shares the key of `2015-05-05_001.JPG` on disk, and asking for it again is refused - although
    it is now the item's own proposal. VID_PLAIN.MOV holds no such key, so after an apply the
    next scan would bind the two."""
    assert rename(baseline, MEDIUM, "2015-05-05_001_edit.MOV").status == 200
    edited = dataclasses.replace(baseline.server.config, ignore_stem_suffixes=("_edit",))
    monkeypatch.setattr(baseline.server, "_config", edited)
    before = rows(baseline)
    writes = _spy_name_writes(monkeypatch)
    again = rename(baseline, MEDIUM, "2015-05-05_001_edit.MOV")
    assert again.status == 409, again.body
    assert again.body["error"] == _stem_refusal(
        "2015-05-05_001_edit.MOV", "2015-05-05_001.JPG", RENAME_REMEDY
    )
    assert writes == [], "a refused rename wrote a name"
    assert rows(baseline) == before


def test_a_stem_held_in_another_case_is_refused(baseline, monkeypatch):
    """The Task 5g review (M3213): 5.4 compares stems CASE-INSENSITIVELY, so `solo.MOV` shares the
    key of `SOLO.JPG` on disk although the two names differ. Every other stem in these tests is
    digits, which have no case - so dropping the fold was invisible to all of them."""
    before = rows(baseline)
    writes = _spy_name_writes(monkeypatch)
    reply = rename(baseline, MEDIUM, "solo.MOV")
    assert reply.status == 409, reply.body
    assert reply.body["error"] == _stem_refusal("solo.MOV", "SOLO.JPG", RENAME_REMEDY)
    assert writes == [], "a refused rename wrote a name"
    assert rows(baseline) == before


def test_a_stem_held_only_in_another_folder_is_free(baseline):
    """The Task 5g review (M3214, M3225): 5.4 groups within ONE folder, so the key carries the
    folder. The deep folder's `T.JPG` (on disk) and that item's proposed name (reserved for the
    plan) hold their stems THERE; at the archive root both stems are free. The decide archive's
    other files all sit in the root, which is the reservation's default folder - so a key
    without its folder, or a reservation filed under the root, was invisible to every other
    test. The deep item's proposed name is read with the suite's own SQL."""
    deep_stem = rows(baseline)[DEEP_FILE][1].rsplit(".", 1)[0]
    assert deep_stem.lower() != "t", "the construction needs two different stems"
    assert rename(baseline, MEDIUM, "T.MOV").status == 200
    took = rename(baseline, MEDIUM, f"{deep_stem}.MOV")
    assert took.status == 200, took.body


def test_a_missing_files_row_holds_its_stem_key_in_the_review_ui(baseline, monkeypatch):
    """The Task 5g review (M3221), ruling concern 3 for the UI as for the planner: a catalog row
    whose file has gone holds its stem key, as it holds its name - the file can come back, and
    the next scan would bind it. A real scan marks a file deleted outside pam `missing`; here
    the suite's own SQL sets that status on `SOLO.JPG`'s row for the length of the test."""
    conn = connect(baseline.db_path)
    try:
        conn.execute("UPDATE files SET status = 'missing' WHERE rel_path = 'SOLO.JPG'")
        conn.commit()
    finally:
        conn.close()
    try:
        writes = _spy_name_writes(monkeypatch)
        reply = rename(baseline, MEDIUM, "SOLO.MOV")
        assert reply.status == 409, reply.body
        assert reply.body["error"] == _stem_refusal("SOLO.MOV", "SOLO.JPG", RENAME_REMEDY)
        assert writes == [], "a refused rename wrote a name"
    finally:
        conn = connect(baseline.db_path)
        try:
            conn.execute("UPDATE files SET status = 'present' WHERE rel_path = 'SOLO.JPG'")
            conn.commit()
        finally:
            conn.close()


def test_the_path_budget_is_taken_against_the_units_longest_extension(baseline):
    """Spec 6.2 rule 5: every member gets the SAME stem, so the longest of them has to fit.

    Not reachable through the endpoint on this fixture, and that is the point: every unit
    in `ARCHIVE` has three-character extensions on both members (`.JPG`+`.NEF`,
    `.JPG`+`.MOV`), so "the longest member's extension" and "the clicked member's
    extension" are the same number and the whole rule is invisible. It is not invisible on
    an archive of real files - an iPhone live photo is `.HEIC` + `.MOV`, four against three - so the
    pair is built here by hand instead, at the deep directory whose budget is already down
    to a handful of characters.

    The assertion is spec 6.2 rule 5's own formula (`full_path_length <= MAX_FULL_PATH`)
    applied to BOTH members, never a comparison against a stem this module predicted.
    """
    conn = connect(baseline.db_path)
    try:
        items = repo.plan_rows(conn, baseline.plan_id, floor=FACES_FLOOR)
    finally:
        conn.close()
    pair = [item for item in items if item.old_filename.startswith("RAW_1.")]
    assert len(pair) == 2
    members = [
        dataclasses.replace(pair[0], rel_dir=baseline.deep_dir, new_filename="a.JPG"),
        dataclasses.replace(pair[1], rel_dir=baseline.deep_dir, new_filename="a.JPEG"),
    ]
    exts = [repo.ext_of(member.new_filename) for member in members]
    assert len(exts[0]) != len(exts[1]), (
        "the two extensions must differ in LENGTH or this test cannot see the rule"
    )
    stem = disclosure.edited_name(
        baseline.server, "Something-Long.JPG", members[0], members
    ).stem
    assert stem
    for ext in exts:
        assert (
            full_path_length(str(baseline.root), baseline.deep_dir, f"{stem}.{ext}")
            <= MAX_FULL_PATH
        ), ext


def test_two_members_of_one_unit_may_never_be_proposed_the_same_name(baseline):
    """Spec 5.4 safety property 1, asserted LOCALLY - the invariant `pam apply` relies on.

    A companion unit may not hold two members with the same extension, and
    `catalog/repo/companions.py` enforces that three modules away, so no plan the real
    planner can build reaches this guard and no test that drives the endpoint can either.
    `collision.assign_unique_stem` carries the identical guard for the identical reason
    ("an invariant apply will rely on must not be provable only somewhere else"), and it
    is pinned there; this is the write API's copy of the same last line.

    Driven straight at `_refuse_collisions` with the state `companions.py` forbids, built
    by hand: one unit, two members, one extension between them, differing only in case -
    which spec 6.3 rule 2 counts as the same name on Windows. The second half is not
    decoration: without a call that must NOT refuse, a guard that refused everything would
    pass this test.
    """
    conn = connect(baseline.db_path)
    try:
        items = repo.plan_rows(conn, baseline.plan_id, floor=FACES_FLOOR)
        members = [item for item in items if item.old_filename.startswith("RAW_1.")]
        assert len(members) == 2, "the RAW+JPEG unit is what this test needs"
        with pytest.raises(ApiError) as refusal:
            write_api._refuse_collisions(
                conn,
                items,
                members,
                {members[0].id: "Sunset-Ridge.JPG", members[1].id: "sunset-ridge.JPG"},
                clash_reason="unreachable - this call hits the duplicate-within-unit"
                " branch, never the clash branch this text belongs to",
            )
        assert refusal.value.status == 409
        write_api._refuse_collisions(
            conn,
            items,
            members,
            {members[0].id: "Sunset-Ridge.JPG", members[1].id: "Sunset-Ridge.NEF"},
            clash_reason="unreachable - this call refuses nothing",
        )
    finally:
        conn.close()


# --- every refusal is write-FREE, which is not the same claim as "nothing survived" ---


def _refused_decide_terminal_id(live: Live) -> Reply:
    set_status(live, LOW, "applied")
    return post(
        live,
        f"/plan/{live.plan_id}/decide",
        {"item_ids": [live.ids[HIGH], live.ids[LOW]], "action": "approve"},
    )


def _stranger_id(live: Live) -> int:
    conn = connect(live.db_path)
    try:
        return repo.plan_rows(conn, live.stale_plan, floor=FACES_FLOOR)[0].id
    finally:
        conn.close()


def _refused_decide_another_plans_id(live: Live) -> Reply:
    return post(
        live,
        f"/plan/{live.plan_id}/decide",
        {"item_ids": [live.ids[HIGH], _stranger_id(live)], "action": "approve"},
    )


def _refused_decide_on_a_closed_plan(live: Live) -> Reply:
    return post(
        live,
        f"/plan/{live.stale_plan}/decide",
        {"item_ids": [_stranger_id(live)], "action": "approve"},
    )


def _refused_filter_terminal_item(live: Live) -> Reply:
    set_status(live, HIGH, "applied")
    return post(live, f"/plan/{live.plan_id}/decide-filter", {"action": "skip"})


def _refused_filter_low_approve(live: Live) -> Reply:
    return post(
        live, f"/plan/{live.plan_id}/decide-filter", {"tier": "low", "action": "approve"}
    )


def _refused_filter_unknown_field(live: Live) -> Reply:
    return post(
        live, f"/plan/{live.plan_id}/decide-filter", {"action": "approve", "colour": "red"}
    )


def _refused_accept_all_unknown_field(live: Live) -> Reply:
    return post(
        live,
        f"/plan/{live.plan_id}/decide-filter",
        {"action": "approve", "accept_all": True, "colour": "red"},
    )


def _refused_rename_onto_a_plan_name(live: Live) -> Reply:
    return rename(live, "RAW_1.JPG", f"{PLANNED[HIGH][:-4]}.JPG")


def _refused_rename_onto_a_disk_name(live: Live) -> Reply:
    return rename(live, "RAW_1.JPG", "2015-05-05_001.JPG")


def _refused_rename_wrong_extension(live: Live) -> Reply:
    return rename(live, "RAW_1.JPG", "Sunset-Ridge.jpg")


def _refused_rename_empty_after_sanitize(live: Live) -> Reply:
    return rename(live, "RAW_1.JPG", "???.JPG")


def _refused_rename_terminal_member(live: Live) -> Reply:
    set_status(live, "RAW_1.NEF", "applied")
    return rename(live, "RAW_1.JPG", "Sunset-Ridge.JPG")


def _refused_restore_terminal_member(live: Live) -> Reply:
    set_status(live, "RAW_1.NEF", "undone")
    return post(live, f"/item/{live.ids['RAW_1.JPG']}/restore")


def _refused_restore_onto_a_plan_name(live: Live) -> Reply:
    """Restore's OWN collision check (spec 8.2 rev 13, M8 Task 2) - the fourth endpoint.

    Set up with the suite's own SQL rather than the two HTTP renames the reproduction
    uses, because the spy is already installed when this runs and a successful rename
    calls both writers: the setup would fail the assertion instead of the refusal.

    THE UNIT MUST BE MOVED OFF ITS GENERATED NAME FIRST, and that is not decoration:
    parking another unit on `2019-07-04_001.JPG` alone leaves `RAW_1.JPG` still sitting
    on it, and the namespace's unit-exclusion discards the restoring unit's own current
    `new_filename` - so the clash cancels itself and restore answers 200. That is the
    state the API cannot reach anyway (a `rename` onto a name another item proposes is
    refused), so these three writes reproduce the M8 Task 2 reproduction exactly: unit A
    off its generated name, unit B onto it, then restore A.

    M806 is why this row exists: without it, moving restore's check after its write loop
    left all 1866 tests green.
    """
    set_new_filename(live, "RAW_1.JPG", "Sunset-Ridge.JPG")
    set_new_filename(live, "RAW_1.NEF", "Sunset-Ridge.NEF")
    set_new_filename(live, "LIVE_1.JPG", PLANNED["RAW_1.JPG"])
    return post(live, f"/item/{live.ids['RAW_1.JPG']}/restore")


# One row per refusal spec 8.2 names, across all four endpoints and both failure shapes -
# `decide`'s all-or-nothing batch and the all-or-nothing unit `rename` and (since M8 Task 2)
# `restore` share.
REFUSALS = {
    "decide-terminal-id": (_refused_decide_terminal_id, 409),
    "decide-another-plans-id": (_refused_decide_another_plans_id, 409),
    "decide-plan-not-open": (_refused_decide_on_a_closed_plan, 409),
    "decide-filter-low-approve": (_refused_filter_low_approve, 400),
    "decide-filter-terminal-item": (_refused_filter_terminal_item, 409),
    "decide-filter-unknown-field": (_refused_filter_unknown_field, 400),
    "decide-filter-accept-all-unknown-field": (_refused_accept_all_unknown_field, 400),
    "rename-name-taken-in-plan": (_refused_rename_onto_a_plan_name, 409),
    "rename-name-taken-on-disk": (_refused_rename_onto_a_disk_name, 409),
    "rename-wrong-extension": (_refused_rename_wrong_extension, 400),
    "rename-empty-after-sanitize": (_refused_rename_empty_after_sanitize, 400),
    "rename-terminal-member": (_refused_rename_terminal_member, 409),
    "restore-terminal-member": (_refused_restore_terminal_member, 409),
    "restore-name-taken-in-plan": (_refused_restore_onto_a_plan_name, 409),
}

# The two functions in the whole product that write a `plan_items` row a browser can
# reach. Named here from spec 8.2's two columns - the decision and the name - rather than
# read off `write_api`, so a third writer added without a decision about this test is an
# unspied writer and this pin says nothing about it. That is the trade, and it is the
# reason the names are spelled out.
PLAN_ITEM_WRITERS = ("set_plan_item_status", "set_plan_item_new_filename")


# Spec 8.2's four WRITE endpoints, written out here from the spec and not derived from
# `REFUSALS`, so the floor below cannot follow the map it protects.
WRITE_ENDPOINTS = ("decide", "decide-filter", "rename", "restore")


def _refusal_cases_for(endpoint: str) -> list[str]:
    """The `REFUSALS` keys belonging to one endpoint. `decide-filter` is its own endpoint
    in spec 8.2, so a `decide-filter-*` key must not be counted as a `decide` one."""
    prefix = f"{endpoint}-"
    return [
        case
        for case in REFUSALS
        if case.startswith(prefix)
        and (endpoint != "decide" or not case.startswith("decide-filter-"))
    ]


def test_every_write_endpoint_has_at_least_one_refusal_under_the_writer_spy():
    """UNPARAMETRIZED ON PURPOSE - it is the floor under the parametrized test below.

    pytest SKIPS an empty parametrize instead of failing it, so emptying `REFUSALS` made
    all twelve writer-spy contracts vanish with the suite reading green (M811, and the
    fourth time this project has been bitten by that shape - M743, M793 before it). A
    control parametrized over the very expectation it protects disappears together with
    it; this one is not, and its list is spelled from spec 8.2 rather than from the map.
    """
    assert REFUSALS, "the writer-spy has nothing to run: spec 8.2's refusals are unpinned"
    missing = [name for name in WRITE_ENDPOINTS if not _refusal_cases_for(name)]
    assert missing == [], f"write endpoints with no refusal under the writer spy: {missing}"


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_a_refusal_never_reaches_the_writer_at_all(baseline, monkeypatch, case):
    """Spec 8.2's wording is "NOTHING IS WRITTEN" and "no member row is WRITTEN until
    every check above has passed" - a claim about what the handler DOES, not about what
    survives it.

    Every other refusal test in this module asserts the second thing (`rows() == before`),
    and the second thing is delivered by `ReviewServer.connection`, which never commits
    and closes the handle in a `finally` - so sqlite3 discards the UPDATEs of a handler
    that wrote first and refused afterwards, and the rows come out byte-identical either
    way. Measured, not argued: moving `_write_decisions` ahead of `_refuse_undecidable`,
    and moving `rename`'s member-write loop ahead of `_refuse_collisions`, each left the
    whole suite green (M655, M656).

    So this test watches the WRITER instead of the rows. `write_api`'s own docstring is
    the standard it is held to: "Leaning on 'we did not commit' for either would be
    leaning on sqlite3's rollback to cover a bug in the order of two loops." A refused
    request must not call either writer once.
    """
    calls: list[tuple[str, tuple[Any, ...]]] = []

    def spy(name, real):
        def wrapped(*args, **kwargs):
            calls.append((name, args[1:]))
            return real(*args, **kwargs)

        return wrapped

    for name in PLAN_ITEM_WRITERS:
        monkeypatch.setattr(repo, name, spy(name, getattr(repo, name)))

    make_request, expected = REFUSALS[case]
    reply = make_request(baseline)
    assert reply.status == expected, reply.body
    assert calls == [], f"{case} refused, but only after writing: {calls}"


# --- END TO END, with the real consumer ----------------------------------------------


def test_what_the_browser_writes_is_what_pam_apply_renames(tmp_path):
    """THE test this task exists for: the UI's writes and M5's apply agree about a plan.

    Built on its own archive, with its own server, because it RENAMES REAL FILES - the
    module-scoped archive above is shared and must survive every test that reads it.

    Decisions are made only over HTTP; the assertions are made only against the disk and
    against `pam verify`. Nothing in between is inspected, which is the point: if the
    write API and apply disagreed about which items are approved, or about which files a
    unit holds, no amount of agreement inside the server would hide it here.
    """
    root = tmp_path / "photos"
    root.mkdir()
    for name, tags in {
        "A.JPG": {"DateTimeOriginal": "2019:07:04 18:23:11"},
        "A.NEF": {"DateTimeOriginal": "2019:07:04 18:23:11"},
        "B.JPG": {"DateTimeOriginal": "2020:08:05 09:15:00"},
        "C.JPG": {"DateTimeOriginal": "2021:09:06 11:02:03"},
    }.items():
        _put(root, name, tags)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan = run_plan(root)
    assert plan.plan_id is not None
    workspace = workspace_if_exists(root)
    assert workspace is not None

    server, thread = _serve(workspace, plan.plan_id, "pam.tests.webui_decide.e2e")
    try:
        conn = connect(workspace.db_path)
        try:
            planned = repo.plan_rows(conn, plan.plan_id, floor=FACES_FLOOR)
            ids = {i.old_filename: i.id for i in planned}
        finally:
            conn.close()
        session = Live(
            server=server,
            root=root,
            db_path=workspace.db_path,
            plan_id=plan.plan_id,
            stale_plan=plan.plan_id,
            token=server.token,
            ids=ids,
            tiers={i.id: i.tier for i in planned},
            deep_dir="",
        )
        assert decide(session, ("B.JPG",), "approve").status == 200
        assert decide(session, ("C.JPG",), "skip").status == 200
        edited = rename(session, "A.JPG", "Beach-Day.JPG")
        assert edited.status == 200, edited.body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=20)

    summary = run_apply(plan.plan_id, root)
    assert (summary.renamed, summary.failed) == (3, 0), summary.failures

    on_disk = sorted(p.name for p in root.iterdir() if not p.name.endswith(".tags.json"))
    # The edited unit landed TOGETHER under the edited stem, each member keeping its own
    # extension (spec 7.3 / amendment A6); the approved lone file took the planner's
    # name; the skipped file was not touched at all.
    assert on_disk == sorted(["Beach-Day.JPG", "Beach-Day.NEF", "2020-08-05_001.JPG", "C.JPG"])
    assert_verify_agrees(root, where="after applying what the review UI wrote")


# --- spec 6.2 rule 7: pam says what it changed (M8 Task 6 Step 2) ---------------------
#
# THE SENTENCES ARE TRANSCRIBED FROM AMENDMENT A5, not imported from `disclosure`. The
# separator is an ASCII hyphen where A5 writes an em dash; `disclosure.py`'s constant
# block carries that argument. `test_webui_disclosure.py` owns the copy and the
# precedence in isolation; these are the same rule reached through a real POST, over a
# real-world library, with the real naming engine underneath - which is the only place the
# CHOICE of sentence is driven by a `StemResult` rather than by a keyword argument.

RULE_7_RESERVED = 'pam adjusted that to "{stem}" — Windows reserves that name.'
RULE_7_ILLEGAL = (
    'pam adjusted that to "{stem}" — names use letters, numbers, dots, dashes and underscores.'
)
RULE_7_SHORTENED = 'pam shortened that to "{stem}" to fit the Windows path limit.'


def _stored_stem(live: Live, name: str) -> str:
    """What the catalog now holds for `name`, minus its extension - the suite's own SQL."""
    return rows(live)[name][1].rpartition(".")[0]


def test_a_reserved_stem_the_user_typed_is_disclosed(baseline):
    """HT-M7's P8, the headline case: `CON` becomes `CON-x` and pam now says so."""
    reply = rename(baseline, "RAW_1.JPG", "con.JPG")
    assert reply.status == 200, reply.body
    stem = _stored_stem(baseline, "RAW_1.JPG")
    assert stem == "con-x"
    assert reply.body["note"] == RULE_7_RESERVED.format(stem=stem)


def test_a_character_the_allowlist_removed_is_disclosed(baseline):
    """The other half of P8: a `:` or a `#` vanished and nothing anywhere said so."""
    reply = rename(baseline, "RAW_1.JPG", "Lake #2.JPG")
    assert reply.status == 200, reply.body
    stem = _stored_stem(baseline, "RAW_1.JPG")
    assert stem == "Lake-2"
    assert reply.body["note"] == RULE_7_ILLEGAL.format(stem=stem)


def test_a_stem_the_path_budget_shortened_is_disclosed(baseline):
    """Rule 5's cut. `T.JPG` sits deep enough that three characters are all it has."""
    reply = rename(baseline, DEEP_FILE, "Fireworks.JPG")
    assert reply.status == 200, reply.body
    stem = _stored_stem(baseline, DEEP_FILE)
    assert len(stem) == DEEP_BUDGET
    assert reply.body["note"] == RULE_7_SHORTENED.format(stem=stem)


def test_a_name_that_is_both_reserved_and_shortened_reports_the_reserved_rule(baseline):
    """The precedence, at the one input where two rules fire for ONE cause.

    `Console` cut to three characters is `Con`, so rule 4 re-fires and - with no room for
    `-x` at this budget - `_finalize` SHORTENS the head instead, setting rule 4's flag and
    rule 5's on the same token. The name was shortened BECAUSE Windows reserves it, so
    "Windows reserves that name" is the true sentence and the path limit is not.
    """
    reply = rename(baseline, DEEP_FILE, "Console.JPG")
    assert reply.status == 200, reply.body
    stem = _stored_stem(baseline, DEEP_FILE)
    assert reserved_head(stem) is None
    assert reply.body["note"] == RULE_7_RESERVED.format(stem=stem)


def test_a_name_pam_stored_exactly_as_typed_says_nothing(baseline):
    """Rule 7 speaks about a DIFFERENCE. Saying "pam adjusted that to X" when pam
    adjusted nothing would be worse than silence: it teaches the user to ignore it."""
    reply = rename(baseline, "RAW_1.JPG", "Lake-2_Tahoe.JPG")
    assert reply.status == 200, reply.body
    assert rows(baseline)["RAW_1.JPG"][1] == "Lake-2_Tahoe.JPG"
    assert reply.body["note"] == ""


def test_the_rename_response_echoes_the_name_that_was_SUBMITTED(baseline):
    """A5: the response reports the submitted stem beside the stored one.

    The page renders the pairing, and both halves come from one answer - the request was
    in flight and the field is editable, so a page that read the stored name from the
    response and the typed name from the input could show two different edits.
    """
    submitted = "  Lake #2__Tahoe--.JPG"
    reply = rename(baseline, "RAW_1.JPG", submitted)
    assert reply.status == 200, reply.body
    assert reply.body["submitted_filename"] == submitted
    assert reply.body["submitted_filename"] != rows(baseline)["RAW_1.JPG"][1]


def test_restore_discloses_nothing_because_the_user_typed_nothing(baseline):
    """Rule 7 is about a name the INPUT asked for. `restore` has no such name: the user
    pressed a control, and the name being installed is the planner's own."""
    assert rename(baseline, "RAW_1.JPG", "con.JPG").status == 200
    reply = post(baseline, f"/item/{baseline.ids['RAW_1.JPG']}/restore")
    assert reply.status == 200, reply.body
    assert "note" not in reply.body
    assert "submitted_filename" not in reply.body
