"""T1 end to end: scan -> plan -> review -> apply, through the entry points a user types.

WHY THIS EXISTS WHEN TASK 9 ALREADY PROVED THE WRITE PATH. `tests/test_webui_decide.py`
ends with `test_what_the_browser_writes_is_what_pam_apply_renames`, which builds a
`ReviewServer` directly, decides over HTTP and calls `run_apply`. That proves the write
API and M5's apply agree about a plan, which is the deepest question Task 9 could ask -
and it deliberately says nothing about whether the COMMANDS are wired to each other.

This test asks the other question. `pam plan` resolves the archive, opens a session log,
writes a plan and prints an id; `pam review` takes that id, takes the workspace LOCK for
the whole session, runs spec 7.2's recovery on the way in and hands the same plan to the
server; `pam apply` takes the id again, asks UX 4's typed confirmation and renames. Every
one of those handoffs is wiring no endpoint test can see, and the failure mode is a
product whose parts each work and whose chain does not.

WHAT IS DRIVEN, AND THE ONE PLACE THIS FALLS SHORT OF `main()`. `pam plan` and `pam apply`
go through `cli.main` with the arguments the user types, confirmation included. `pam
review` goes through `run_review`, which IS what `cli.main` calls, with its `serve` seam
supplying the browser's HTTP - the real command blocks in a serve loop until Ctrl+C, so no
test can call it and return. `pam scan` goes through `run_scan` with the suite's fake
exiftool for the same shape of reason: the CLI has no seam for it and this archive's files
are not real JPEGs. Both substitutions are named here rather than hidden, and neither is
on the plan -> review -> apply path this test is about.

THE MIXED SET IS THE POINT (M7 plan Task 13 Step 2): a bulk preset, an individual decide,
one edit and one skip, all over HTTP, so that the plan reaching `pam apply` was built four
different ways. A plan every item of which was approved identically would not notice a
`pam apply` that ignored `status` altogether.
"""

import json
import threading
import webbrowser
from http.client import HTTPConnection

import pytest
from archives import FAKE_COMMAND, assert_verify_agrees, catalog, journal_pairs, open_plan, put

from pam.cli import main
from pam.scan.runner import run_scan
from pam.webui import run_review

# The archive, and the name the planner owes each file. WRITTEN OUT BY HAND from spec
# 6.1's template semantics, never read back from the planner: an expectation derived from
# the code under test agrees with a broken planner. `A.JPG` and `A.NEF` share a stem, so
# spec 5.4 makes them one companion unit and one name.
ARCHIVE = {
    "A.JPG": "2019:07:04 18:23:11",
    "A.NEF": "2019:07:04 18:23:11",
    "B.JPG": "2020:08:05 09:15:00",
    "C.JPG": "2021:09:06 11:02:03",
    "D.JPG": "2022:10:07 12:00:00",
    "E.JPG": "2023:11:08 13:30:00",
    "F.JPG": "2024:12:09 14:45:00",
}

PLANNED_NAMES = {
    "A.JPG": "2019-07-04_001.JPG",
    "A.NEF": "2019-07-04_001.NEF",
    "B.JPG": "2020-08-05_001.JPG",
    "C.JPG": "2021-09-06_001.JPG",
    "D.JPG": "2022-10-07_001.JPG",
    "E.JPG": "2023-11-08_001.JPG",
    "F.JPG": "2024-12-09_001.JPG",
}

# What the user types into the editor for the `A` unit, and what each member becomes.
EDITED_SUBMISSION = "Beach-Day.JPG"
EDITED = {"A.JPG": "Beach-Day.JPG", "A.NEF": "Beach-Day.NEF"}


@pytest.fixture(autouse=True)
def no_real_browser(monkeypatch):
    """`run_review` opens a browser. A suite that opens real tabs is a broken suite."""
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)
    return opened


def _request(server, method, path, body=None):
    """One request to the live review server, exactly as the page makes it.

    Through the token prefix, because that is the only way in - a driver that reached
    the handlers by any other route would pass while the shipped page could not load.
    """
    payload = b"" if body is None else json.dumps(body).encode("utf-8")
    conn = HTTPConnection(server.host, server.port, timeout=20)
    try:
        conn.request(
            method,
            f"/t/{server.token}/api{path}",
            body=payload,
            headers={"Content-Type": "application/json", "Content-Length": str(len(payload))},
        )
        response = conn.getresponse()
        raw = response.read()
        assert response.status == 200, (method, path, response.status, raw)
        return json.loads(raw)
    finally:
        conn.close()


def test_t1_scan_plan_review_apply_is_one_chain(tmp_path, monkeypatch, capsys, no_real_browser):
    """UX 3's fifteen-minute path, end to end, with a mixed set decided over HTTP."""
    root = tmp_path / "photos"
    root.mkdir()
    for name, date in ARCHIVE.items():
        put(root, name, date)

    # --- scan ------------------------------------------------------------------------
    scanned = run_scan(root, exiftool_command=FAKE_COMMAND)
    assert not scanned.errors, scanned.errors

    # --- plan, through the CLI -------------------------------------------------------
    capsys.readouterr()
    assert main(["plan", "--archive", str(root)]) == 0
    plan_out = capsys.readouterr().out
    conn = catalog(root)
    try:
        plan_id = open_plan(conn).id
    finally:
        conn.close()
    # The chain's first handoff: the id the user is about to type came off this screen.
    assert f"Next: pam review {plan_id}" in plan_out

    # --- review, through the server `pam review` runs --------------------------------
    seen: dict[str, object] = {}

    def drive(server):
        """Stand in for the serve loop `pam review` blocks in, then be the browser.

        `serve_forever` on its own thread, because that is what `serve_until_interrupted`
        does and what makes the socket answer; the real command ends when Ctrl+C shuts it
        down, and this one ends when the session below is finished.
        """
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            _session(server)
        finally:
            server.shutdown()
            thread.join(timeout=20)

    def _session(server):
        """The browser's whole session: read the plan, then decide it four ways."""
        listing = _request(server, "GET", f"/plan/{plan_id}/items?limit=200")
        ids = {item["old_filename"]: item["id"] for item in listing["items"]}
        # Asserted, not assumed. The planner must have proposed exactly the names spelled
        # at the top of this module, or every disk assertion below tests something else.
        assert {i["old_filename"]: i["new_filename"] for i in listing["items"]} == PLANNED_NAMES

        # 1. ONE SKIP, by id. `C.JPG` keeps the name it has.
        skipped = _request(
            server,
            "POST",
            f"/plan/{plan_id}/decide",
            {"item_ids": [ids["C.JPG"]], "action": "skip"},
        )
        assert skipped["decided"] == 1

        # 2. ONE EDIT, on the RAW+JPEG unit. Amendment A6: the subject is the UNIT, so
        #    one request renames both members and approves both.
        edited = _request(
            server,
            "POST",
            f"/item/{ids['A.JPG']}/rename",
            {"new_filename": EDITED_SUBMISSION},
        )
        assert {m["old_filename"]: m["new_filename"] for m in edited["members"]} == EDITED
        assert {m["status"] for m in edited["members"]} == {"approved"}

        # 3. ONE INDIVIDUAL APPROVE, by id.
        approved = _request(
            server,
            "POST",
            f"/plan/{plan_id}/decide",
            {"item_ids": [ids["F.JPG"]], "action": "approve"},
        )
        assert approved["decided"] == 1

        # 4. THE BULK PRESET, last, exactly as UX 5's `Approve High+Medium` builds it:
        #    an unfiltered approve pinned to `status: proposed`. It must therefore reach
        #    B, D and E and NOTHING ELSE - not the skipped item, not the edited unit, not
        #    the one already approved. That is the whole reason the preset runs last.
        preset = _request(
            server,
            "POST",
            f"/plan/{plan_id}/decide-filter",
            {"action": "approve", "status": "proposed"},
        )
        assert (preset["decided"], preset["excluded_low"]) == (3, 0)
        seen["counts"] = preset["counts"]

    summary = run_review(plan_id, root, serve=drive)
    review_out = capsys.readouterr().out
    assert summary.plan_id == plan_id
    assert (summary.approved, summary.skipped, summary.remaining) == (6, 1, 0)
    # The server's own arithmetic, recomputed from the real rows on the last write, and
    # `attention` is 0 because every name here is dated, high-tier and untruncated (spec
    # 8.2 rev 12's three operands) - asserted so a plan that quietly grew an attention
    # item would not slip through as "the counts still add up".
    assert seen["counts"] == {
        "total": 7,
        "approved": 6,
        "skipped": 1,
        "remaining": 0,
        "attention": 0,
    }
    # The chain's second handoff, and the browser really was opened at the printed URL.
    assert f"Next: pam apply {plan_id}" in review_out
    assert no_real_browser == [summary.url]

    # --- apply, through the CLI, with UX 4's typed confirmation ----------------------
    monkeypatch.setattr("builtins.input", lambda: "apply")
    assert main(["apply", str(plan_id), "--archive", str(root)]) == 0
    apply_out = capsys.readouterr().out
    assert "6 renamed, 0 failed" in apply_out

    # --- the disk, which is the only thing that settles it ---------------------------
    expected = set(EDITED.values())
    expected |= {PLANNED_NAMES[n] for n in ("B.JPG", "D.JPG", "E.JPG", "F.JPG")}
    expected.add("C.JPG")  # skipped: untouched, under the name it arrived with
    on_disk = {p.name for p in root.iterdir() if not p.name.endswith(".tags.json")}
    assert on_disk == expected
    # And the planner's own name for the edited unit is NOWHERE, which is the claim the
    # set comparison above makes only by omission.
    assert not (root / PLANNED_NAMES["A.JPG"]).exists()

    # --- the journal and the report agree with the disk and with each other ----------
    conn = catalog(root)
    try:
        done = dict(journal_pairs(conn, "done"))
    finally:
        conn.close()
    assert done == {
        "A.JPG": "Beach-Day.JPG",
        "A.NEF": "Beach-Day.NEF",
        "B.JPG": PLANNED_NAMES["B.JPG"],
        "D.JPG": PLANNED_NAMES["D.JPG"],
        "E.JPG": PLANNED_NAMES["E.JPG"],
        "F.JPG": PLANNED_NAMES["F.JPG"],
    }

    session_id = _session_id(root)
    report_text = _report_text(root, session_id)
    assert f"- {len(done):,} file(s) renamed" in report_text
    assert "- 0 file(s) not renamed" in report_text
    assert f"- Proposed names from: plan {plan_id:,}" in report_text
    assert f"- Photo folder: {root}" in report_text
    assert f"pam undo {session_id}" in report_text

    assert_verify_agrees(root, where="after the T1 chain")


def _session_id(root):
    """The one session this archive has, read with the SUITE'S OWN SQL.

    Not `repo.sessions`: the report's session id is one of the things being checked, and
    an oracle that asked the production query would agree with a broken one (m5.md's
    `repo.open_plan` lesson).
    """
    conn = catalog(root)
    try:
        rows = conn.execute("SELECT DISTINCT session_id FROM journal").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, rows
    return str(rows[0]["session_id"])


def _report_text(root, session_id):
    from pam.paths import workspace_if_exists

    ws = workspace_if_exists(root)
    assert ws is not None
    return (ws.reports_dir / f"session-{session_id}.md").read_text(encoding="utf-8")
