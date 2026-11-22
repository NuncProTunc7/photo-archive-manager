"""HT-M14 F3 (AM7): two events whose words are the same are told apart.

On a real-world library the date tree showed `Aug 30 (36)` and `Aug 30 (4)` - two unlabelled events
on one day - and the list two headings both reading `August 27, 2023`. Nothing told them apart.
AM7 numbers a repeated label in date order: `Aug 30, event 1`, `Aug 30, event 2`.

The table is spelled by hand; the endpoints are read over a real scanned archive and a real
server, with the expected words written out, never derived from `nav.told_apart`.
"""

import json
import logging
import threading
from http.client import HTTPConnection

import pytest
from archives import FAKE_COMMAND

from pam.naming.planner import run_plan
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan
from pam.webui.nav import told_apart
from pam.webui.server import ReviewServer

TABLE = [
    ([], []),
    (["Aug 30"], ["Aug 30"]),
    (["Aug 30", "Aug 31"], ["Aug 30", "Aug 31"]),
    (["Aug 30", "Aug 30"], ["Aug 30, event 1", "Aug 30, event 2"]),
    (
        ["Aug 29", "Aug 30", "Place A", "Aug 30", "Aug 30"],
        ["Aug 29", "Aug 30, event 1", "Place A", "Aug 30, event 2", "Aug 30, event 3"],
    ),
    (["Place A", "Place A"], ["Place A, event 1", "Place A, event 2"]),
]
assert TABLE, "the told-apart table is empty"


@pytest.mark.parametrize(("labels", "expected"), TABLE)
def test_a_repeated_label_is_numbered_in_order_and_a_single_one_is_untouched(labels, expected):
    assert told_apart(labels) == expected


@pytest.fixture
def served(tmp_path):
    """Two photos on one day, ten hours apart (two events at the 4 h default gap), one the
    next day, all unlabelled - the HT-M14 shape."""
    root = tmp_path / "photos"
    root.mkdir()
    for name, when in {
        "a.jpg": "2023:08:30 08:00:00",
        "b.jpg": "2023:08:30 18:00:00",
        "c.jpg": "2023:08:31 09:00:00",
    }.items():
        (root / name).write_bytes(f"the only copy of {name}".encode())
        (root / f"{name}.tags.json").write_text(
            json.dumps({"DateTimeOriginal": when, "PamTestSidecar": name}), encoding="utf-8"
        )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan = run_plan(root)
    ws = workspace_if_exists(root)
    server = ReviewServer(workspace=ws, plan_id=plan.plan_id, logger=logging.getLogger("t"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, plan.plan_id
    server.shutdown()
    server.server_close()
    thread.join(timeout=20)


def _get(server, path):
    conn = HTTPConnection(server.host, server.port, timeout=20)
    try:
        conn.request("GET", f"/t/{server.token}/api{path}")
        response = conn.getresponse()
        assert response.status == 200
        return json.loads(response.read())
    finally:
        conn.close()


def test_the_date_tree_numbers_two_events_on_one_day(served):
    server, plan_id = served
    (year,) = _get(server, f"/plan/{plan_id}/groups")["groups"]
    (month,) = year["months"]
    assert [(e["label"], e["count"]) for e in month["events"]] == [
        ("Aug 30, event 1", 1),
        ("Aug 30, event 2", 1),
        ("Aug 31", 1),
    ]


def test_the_list_numbers_two_headings_with_the_same_words(served):
    server, plan_id = served
    groups = _get(server, f"/plan/{plan_id}/list")["groups"]
    assert [g["title"] for g in groups] == [
        "August 30, 2023, event 1",
        "August 30, 2023, event 2",
        "August 31, 2023",
    ]
    # The date line under each title stays the plain date.
    assert [g["date_line"] for g in groups] == [
        "August 30, 2023",
        "August 30, 2023",
        "August 31, 2023",
    ]
