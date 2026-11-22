"""M12's construction hunt (plan Task 14 Step 2). Vision S1-S4, BUILT rather than argued.

WHY THIS FILE EXISTS, in the project's own words. A property no single module owns is
invisible to a mutation pass scoped to one task's files: M8's 232 exemplary rows could not
reach `pam apply` splitting a companion unit, M9's 392 could not reach F12, and M11's review
found two product defects between commands that no task-scoped row could see. So the vision's
safety properties are taken one at a time and the violation is BUILT end to end, across module
boundaries, driving the real commands and the real review server.

M12 asks FIVE questions. The first four are carried forward from M8-M11 by `CLAUDE.md`; the
fifth is `m11.md` 4's (Task 14 Step 2, Q5).

  Q1  Which filter dimensions are NOT unit-uniform?
      M12's answer: EVENT MEMBERSHIP diverges inside a unit more often than before, because
      step 0 groups every file under its OWN effective date (design 6.1 rev 27, I3) while the
      name - and so the nav MONTH - is the primary's. A RAW+display unit whose two readings
      are years apart is two events under ONE month: each event selects one member, and
      `decide-filter` must widen it. `attention`'s new operand is read off the unit's tokens,
      which the planner copies to every member, so it is uniform - proved below, not assumed.
  Q2  Which members of a unit does each command NOT see?
      A scan's `.xmp` sidecar is never read by OCR. The plan names `wants()` as the reason;
      the code stops it EARLIER - design 4 never hashes a sidecar, and design 8.1 rev 16 skips
      a file with no sha256 before `wants()` is asked. Either way it carries no `ocr_text`
      marker and `pam status` counts it as not covered (design 8.1: coverage counts files
      PROCESSED) - and it still takes the unit's one stem and the primary's OCR date. The
      planner, for its part, never sees a NON-primary member's own date (recorded below).
  Q3  Does every write path still resolve through `_widen_to_units` over the WHOLE plan?
      M12 adds no write route. The live route table is enumerated and compared, route by
      route, with a list written here by hand; each plan-write route is then DRIVEN with one
      member of an OCR unit whose members sit in different events.
  Q4  Which faces does each unnamed-cluster operation actually SEE, compared with the faces
      the page SHOWED? M11's constructions, rebuilt against Task 5's changed responses: the
      queue now reports `hidden_below_floor` and `floor`, and every write returns `named` /
      `remaining`. A below-floor detection in the cluster is the new case.
  Q5  What does a write in one command do to an artefact another command already produced,
      or to a reader that is still open?
      SEQUENTIAL: an OCR-dated unit is reviewed and approved, then one of three changes lands
      - a second `pam enrich ocr` under a new identity whose reading finds no date, `[tiers]
      ocr.floor` raised above the reading, `Scans` removed from `[enrich.ocr] folders` - and
      `pam apply` of the still-open plan applies the REVIEWED name, whole, byte-identical,
      `pam verify` clean (P11: not a refusal). Each change is shown to be real: after `pam
      undo`, a fresh plan names the unit from its mtime.
      CONCURRENT: `pam status` (lock-free) is stopped after each of its eight reads in turn
      while `pam enrich ocr` or `pam plan` commits. Every reading is the hand-spelled before-
      or after-state EXCEPT ONE - a FINDING, xfail(strict): a `pam plan` committing between
      the plan counts and the open-plan ids is reported as "Plans: 1 open" beside "Next: pam
      review 2", a state the catalog never held (FIXED in M13 Task 4, S13: one snapshot;
      the xfail is gone and every case is a plain test). `pam review` holds the workspace lock for its
      whole life, so both writers are REFUSED under its read endpoints (measured), which
      report the same state before and after the attempt.
      CLARIFICATION 8: under a second PAM_HOME, a fresh catalog reads the applied OCR- and
      mtime-dated names as `filename` dates - `medium`, out of `attention` - while the
      original workspace still holds `file_mtime` and the reading.

EVERY EXPECTATION HERE IS SPELLED FROM THE SPEC OR BUILT BY HAND, never read back out of the
production call it checks: event ids and statuses are read with the suite's own SQL, nav keys
are assembled from those ids and a month written as a literal, response sentences are written
out from rule 7's register, and the apply/undo oracle hashes the disk itself.
"""

import hashlib
import json
import os
import sqlite3
import threading
import webbrowser
from datetime import datetime
from http.client import HTTPConnection
from pathlib import Path

import pytest
from archives import FAKE_COMMAND, approve_archive
from ocr_stack import fake_pack
from PIL import Image
from test_faces_cluster import GEOMETRY, blob
from test_ocr_naming import add_reading, build
from test_t1_ocr_chain import ImprintFactory
from test_webui_faces import Served

from pam.catalog import repo
from pam.catalog.db import connect
from pam.cli import main
from pam.enrich import ocr_model
from pam.naming.planner import run_plan
from pam.paths import workspace_for, workspace_if_exists
from pam.scan.runner import run_scan
from pam.webui import faces_api, faces_manage_api, faces_split_api, run_review

# ---------------------------------------------------------------------------
# The shared archive: ONE RAW+display unit in `Scans/`, both members mtime-dated, each with its
# own reading. Design 5.4 makes the display image the primary, so the unit is named from ITS
# date; step 0 groups each file under its own effective date (I3), so the RAW's reading puts it
# in an event of its own, five years from its partner's.
# ---------------------------------------------------------------------------

PRIMARY = "Scans/pair.png"
RAW = "Scans/pair.nef"
PRIMARY_DATE = "1990-01-01"
RAW_DATE = "1995-06-06"
# The nav month is the DATE TOKEN's (spec 8.2), and both members carry the primary's.
MONTH = "1990-01"
# The nav label of an unlabelled event is its span READ OFF THE NAMING TOKENS (UX 5 rev 8), so
# the RAW's event - whose own date is 1995-06-06 - is labelled with the primary's day too.
SPAN_LABEL = "Jan 1"
NAMES = {PRIMARY: "1990-01-01_001.png", RAW: "1990-01-01_001.nef"}
# Rule 7's sentences, written out from the register (UX 5 rev 10), never formatted from
# `disclosure`'s templates.
UNIT_NOTE = "pam also {verb} 1 companion file — pam renames a group together or not at all."
OVERRODE = (
    " That file had been decided the other way — pam changed it to keep the group together."
)


@pytest.fixture(autouse=True)
def _never_a_real_browser(monkeypatch):
    """`run_review` opens a browser unless told otherwise. Every call here passes an opener;
    this makes a forgotten one a no-op rather than a real tab."""
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: True)


def _ocr_unit(tmp_path, *, primary_score=0.8):
    """Scan, two readings years apart, `pam plan`. Returns (root, plan_id)."""
    root = build(tmp_path, {PRIMARY: None, RAW: None})
    add_reading(root, PRIMARY, [(PRIMARY_DATE, primary_score)])
    add_reading(root, RAW, [(RAW_DATE, 0.8)])
    summary = run_plan(root)
    assert summary.plan_id is not None
    return root, summary.plan_id


def _conn(root):
    conn = connect(workspace_for(root).db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _item_ids(root, plan_id):
    """`{old_rel_path: plan_items.id}`, with the suite's own SQL."""
    conn = _conn(root)
    try:
        rows = conn.execute(
            "SELECT id, old_rel_path FROM plan_items WHERE plan_id = ?", (plan_id,)
        ).fetchall()
    finally:
        conn.close()
    return {row["old_rel_path"]: int(row["id"]) for row in rows}


def _statuses(root, plan_id):
    conn = _conn(root)
    try:
        rows = conn.execute(
            "SELECT old_rel_path, status FROM plan_items WHERE plan_id = ?", (plan_id,)
        ).fetchall()
    finally:
        conn.close()
    return {row["old_rel_path"]: row["status"] for row in rows}


def _new_names(root, plan_id):
    conn = _conn(root)
    try:
        rows = conn.execute(
            "SELECT old_rel_path, new_filename FROM plan_items WHERE plan_id = ?", (plan_id,)
        ).fetchall()
    finally:
        conn.close()
    return {row["old_rel_path"]: row["new_filename"] for row in rows}


def _set_status(root, plan_id, rel, status):
    """A partly decided unit, built by hand: no endpoint can produce one since A1."""
    conn = _conn(root)
    try:
        conn.execute(
            "UPDATE plan_items SET status = ? WHERE plan_id = ? AND old_rel_path = ?",
            (status, plan_id, rel),
        )
        conn.commit()
    finally:
        conn.close()


def _events_of(root):
    """`{rel_path: (event_id, start_dt)}` through `file_events`, the suite's own SQL."""
    conn = _conn(root)
    try:
        rows = conn.execute(
            "SELECT f.rel_path, e.id, e.start_dt FROM file_events fe"
            " JOIN files f ON f.id = fe.file_id JOIN events e ON e.id = fe.event_id"
        ).fetchall()
    finally:
        conn.close()
    return {row["rel_path"]: (int(row["id"]), row["start_dt"]) for row in rows}


def _nav_keys(root):
    """The nav key each member's card is filed under, ASSEMBLED BY HAND: the hand-written month
    plus the event id the suite's own SQL read. Never `repo.event_key`, whose selection the
    constructions below are testing."""
    events = _events_of(root)
    assert events[PRIMARY][1] == f"{PRIMARY_DATE}T00:00:00", events
    assert events[RAW][1] == f"{RAW_DATE}T00:00:00", events
    assert events[PRIMARY][0] != events[RAW][0], "the two readings did not make two events"
    return {rel: f"{MONTH}.e{events[rel][0]}" for rel in (PRIMARY, RAW)}


def _call(server, method, path, body=None):
    """One request through the token prefix. Returns (status, json body)."""
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
        return response.status, (json.loads(raw) if raw else None)
    finally:
        conn.close()


def _ok(server, method, path, body=None):
    status, reply = _call(server, method, path, body)
    assert status == 200, (method, path, status, reply)
    return reply


def _drive(plan_id, root, work):
    """Run `work(server)` against the server `pam review` really starts, then stop it."""
    result = {}

    def serve(server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result["value"] = work(server)
        finally:
            server.shutdown()
            thread.join(timeout=20)

    run_review(plan_id, root, serve=serve, opener=lambda url: True)
    return result["value"]


def _listed(server, plan_id, query):
    reply = _ok(server, "GET", f"/plan/{plan_id}/items?limit=200&{query}")
    return sorted(card["old_rel_path"] for card in reply["items"])


def _decide_filter(server, plan_id, action, group):
    return _ok(
        server, "POST", f"/plan/{plan_id}/decide-filter", {"action": action, "group": group}
    )


def _counts(*, approved=0, skipped=0, attention=2):
    return {
        "total": 2,
        "approved": approved,
        "skipped": skipped,
        "remaining": 2 - approved - skipped,
        "attention": attention,
    }


# ---------------------------------------------------------------------------
# Q1 - EVENT MEMBERSHIP, A NON-UNIFORM DIMENSION THAT OCR MAKES COMMON
# ---------------------------------------------------------------------------


def test_q1_a_units_two_readings_are_two_events_under_one_month_and_the_nav_says_so(tmp_path):
    """The precondition, read through the API: the nav really does hold TWO event entries for
    ONE unit, both under the primary's month, both labelled with the primary's day - and each
    entry selects exactly one member. (The label is the naming tokens' span, not the RAW's own
    date: that is UX 5 rev 8's rule, not a claim about the RAW.)"""
    root, plan_id = _ocr_unit(tmp_path)
    assert _new_names(root, plan_id) == NAMES
    keys = _nav_keys(root)

    def work(server):
        groups = _ok(server, "GET", f"/plan/{plan_id}/groups")["groups"]
        shown = {rel: _listed(server, plan_id, f"group={keys[rel]}") for rel in keys}
        return groups, shown

    groups, shown = _drive(plan_id, root, work)

    assert [(year["key"], year["count"]) for year in groups] == [("1990", 2)]
    (month,) = groups[0]["months"]
    assert (month["key"], month["count"]) == (MONTH, 2)
    assert sorted((e["key"], e["count"]) for e in month["events"]) == sorted(
        [(keys[PRIMARY], 1), (keys[RAW], 1)]
    )
    # HT-M14 F3 (AM7): two events whose words are the same are told apart, in date order.
    assert [e["label"] for e in month["events"]] == ["Jan 1, event 1", "Jan 1, event 2"]
    assert SPAN_LABEL == "Jan 1"
    assert shown == {PRIMARY: [PRIMARY], RAW: [RAW]}


def test_q1_decide_filter_on_either_event_decides_the_whole_unit_and_says_so(tmp_path):
    """The plan's construction: approve, skip and reset through `decide-filter` on ONE member's
    event, alternating which, asserting BOTH members, the widening disclosure and the counts.

    Mutation the reviewer runs first (the plan's own): `decide_filter` hands `_widen_to_units`
    the filtered subset (`chosen`) instead of the whole plan -> `decided` is 1, the partner is
    left behind, and the first assertion after the approve fails."""
    root, plan_id = _ocr_unit(tmp_path)
    keys = _nav_keys(root)
    ids = _item_ids(root, plan_id)

    def work(server):
        steps = []
        for action, rel in (("approve", RAW), ("skip", PRIMARY), ("reset", RAW)):
            reply = _decide_filter(server, plan_id, action, keys[rel])
            steps.append((reply, _statuses(root, plan_id)))
        return steps

    (approved, after_approve), (skipped, after_skip), (reset, after_reset) = _drive(
        plan_id, root, work
    )

    # approve on the RAW's event: the primary is the companion, and nothing was overridden.
    assert after_approve == {PRIMARY: "approved", RAW: "approved"}
    assert approved["decided"] == 2
    assert approved["companions"] == [ids[PRIMARY]]
    assert approved["overrode"] == []
    assert approved["excluded_low"] == 0
    assert approved["note"] == UNIT_NOTE.format(verb="approved")
    assert approved["counts"] == _counts(approved=2)

    # skip on the primary's event: the RAW had been APPROVED, so the skip overrides it.
    assert after_skip == {PRIMARY: "skipped", RAW: "skipped"}
    assert skipped["decided"] == 2
    assert skipped["companions"] == [ids[RAW]]
    assert skipped["overrode"] == [ids[RAW]]
    assert skipped["note"] == UNIT_NOTE.format(verb="skipped") + OVERRODE
    assert skipped["counts"] == _counts(skipped=2)

    # reset on the RAW's event: both back to undecided.
    assert after_reset == {PRIMARY: "proposed", RAW: "proposed"}
    assert reset["decided"] == 2
    assert reset["companions"] == [ids[PRIMARY]]
    assert reset["overrode"] == [ids[PRIMARY]]
    assert reset["note"] == UNIT_NOTE.format(verb="returned to undecided") + OVERRODE
    assert reset["counts"] == _counts()


def test_q1_a_low_tier_unit_is_held_back_WHOLE_and_skip_and_reset_still_widen(tmp_path):
    """The same construction over a `low`-tier unit (the primary's line scores 0.6, below
    `[tiers] ocr.low` = 0.7; design 6.1 rev 27). A bulk approve on the RAW's event must hold
    back BOTH members (spec 8.2 rev 15: the exclusion is unit-scoped) and report both; skip and
    reset carry no restriction and must still widen."""
    root, plan_id = _ocr_unit(tmp_path, primary_score=0.6)
    keys = _nav_keys(root)
    ids = _item_ids(root, plan_id)

    def work(server):
        steps = []
        for action, rel in (("approve", RAW), ("skip", RAW), ("reset", PRIMARY)):
            reply = _decide_filter(server, plan_id, action, keys[rel])
            steps.append((reply, _statuses(root, plan_id)))
        return steps

    (approved, after_approve), (skipped, after_skip), (reset, after_reset) = _drive(
        plan_id, root, work
    )

    assert after_approve == {PRIMARY: "proposed", RAW: "proposed"}
    assert approved["decided"] == 0
    assert approved["excluded_low"] == 2
    assert approved["companions"] == []
    assert approved["note"] == ""
    assert approved["counts"] == _counts()

    assert after_skip == {PRIMARY: "skipped", RAW: "skipped"}
    assert skipped["decided"] == 2
    assert skipped["companions"] == [ids[PRIMARY]]
    assert skipped["overrode"] == []
    assert skipped["note"] == UNIT_NOTE.format(verb="skipped")
    assert skipped["counts"] == _counts(skipped=2)

    assert after_reset == {PRIMARY: "proposed", RAW: "proposed"}
    assert reset["decided"] == 2
    assert reset["companions"] == [ids[RAW]]
    assert reset["overrode"] == [ids[RAW]]
    assert reset["counts"] == _counts()


def test_q1_a_companion_decided_the_OTHER_way_is_brought_along_and_named(tmp_path):
    """A unit whose RAW was SKIPPED while its primary is undecided (built with the suite's own
    SQL - no endpoint can produce it since A1). Approving the PRIMARY's event reaches the unit
    through its undecided half; the RAW must be approved with it and the disclosure must say a
    decision was reversed (UX 5 rev 10)."""
    root, plan_id = _ocr_unit(tmp_path)
    keys = _nav_keys(root)
    ids = _item_ids(root, plan_id)
    _set_status(root, plan_id, RAW, "skipped")

    def work(server):
        shown = _listed(server, plan_id, f"group={keys[PRIMARY]}")
        return shown, _decide_filter(server, plan_id, "approve", keys[PRIMARY])

    shown, approved = _drive(plan_id, root, work)

    assert shown == [PRIMARY]
    assert _statuses(root, plan_id) == {PRIMARY: "approved", RAW: "approved"}
    assert approved["decided"] == 2
    assert approved["companions"] == [ids[RAW]]
    assert approved["overrode"] == [ids[RAW]]
    assert approved["note"] == UNIT_NOTE.format(verb="approved") + OVERRODE
    assert approved["counts"] == _counts(approved=2)


def _hashes(root):
    """`{rel_path: sha256}` of every file under the archive, hashed HERE - an oracle that shares
    nothing with the catalog, the journal or `archives.digests`."""
    found = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            found[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return found


def test_the_hash_oracle_reads_the_bytes_and_names_the_path_they_changed_at(tmp_path):
    """`_hashes`' own control (M2332): a published SHA-256 vector, and one flipped byte moves
    exactly that path's digest - so the byte half of the path-and-hash oracle is not blind."""
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.bin").write_bytes(b"abc")
    (tmp_path / "y.bin").write_bytes(b"abc")
    abc = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    before = _hashes(tmp_path)
    assert before == {"a/x.bin": abc, "y.bin": abc}
    (tmp_path / "y.bin").write_bytes(b"abd")
    after = _hashes(tmp_path)
    assert {rel for rel in before if before[rel] != after[rel]} == {"y.bin"}


def test_q1_the_unit_decided_through_one_event_applies_whole_and_undoes_exactly(
    tmp_path, monkeypatch, capsys
):
    """The finish: approve through the RAW's event, `pam apply`, `pam undo` - checked on DISK by
    a path-and-hash oracle built here. After the apply both members carry the primary's stem
    and their own bytes; after the undo every path and every byte is the original."""
    root, plan_id = _ocr_unit(tmp_path)
    keys = _nav_keys(root)
    before = _hashes(root)

    _drive(plan_id, root, lambda server: _decide_filter(server, plan_id, "approve", keys[RAW]))
    monkeypatch.setattr("builtins.input", lambda: "apply")
    assert main(["apply", str(plan_id), "--archive", str(root)]) == 0
    capsys.readouterr()

    moved = {
        PRIMARY: "Scans/1990-01-01_001.png",
        RAW: "Scans/1990-01-01_001.nef",
    }
    expected = {moved.get(rel, rel): digest for rel, digest in before.items()}
    assert _hashes(root) == expected, "the unit did not move whole, or a byte changed"

    conn = _conn(root)
    try:
        session = str(
            conn.execute("SELECT session_id FROM journal ORDER BY id DESC LIMIT 1").fetchone()[
                0
            ]
        )
    finally:
        conn.close()
    assert main(["undo", session, "--archive", str(root)]) == 0
    capsys.readouterr()
    assert _hashes(root) == before
    assert main(["verify", "--archive", str(root)]) == 0
    capsys.readouterr()


# --- Q1, the candidate the plan names: `attention` IS unit-uniform -------------------------

# Two units. In `a`, only the PRIMARY has a reading, so the name carries an OCR date and both
# members are in attention. In `b`, the primary carries a CAMERA date (2010-06-15, EXIF) and only
# the mtime-dated RAW has a reading: the name is the camera date, and the RAW's own reading -
# which WOULD put a per-member predicate in attention - must not, because the unit's tokens are
# the primary's. (`b`'s primary is EXIF-dated rather than mtime-dated because a `file_mtime` date
# is itself `low` - measured - and so in attention by the first operand, which would hide the
# fourth.) Each entry: (the EXIF date or None, the reading's date or None).
ATTENTION_UNITS = {
    "Scans/a.png": (None, "1990-01-01"),
    "Scans/a.nef": (None, None),
    "Scans/b.png": ("2010:06:15 09:00:00", None),
    "Scans/b.nef": (None, "1995-06-06"),
}
IN_ATTENTION = ["Scans/a.nef", "Scans/a.png"]


def test_q1_attention_is_uniform_across_a_unit_whichever_member_carries_the_reading(tmp_path):
    """The plan's candidate, PROVED: `attention=1` selects both members of `a` and neither of
    `b`. A per-member operand would select `a.png` alone and `b.nef` alone."""
    root = build(tmp_path, {rel: exif for rel, (exif, _) in ATTENTION_UNITS.items()})
    for rel, (_, date) in ATTENTION_UNITS.items():
        if date is not None:
            add_reading(root, rel, [(date, 0.8)])
    summary = run_plan(root)
    plan_id = summary.plan_id
    assert _new_names(root, plan_id) == {
        "Scans/a.png": "1990-01-01_001.png",
        "Scans/a.nef": "1990-01-01_001.nef",
        "Scans/b.png": "2010-06-15_001.png",
        "Scans/b.nef": "2010-06-15_001.nef",
    }

    shown = _drive(plan_id, root, lambda server: _listed(server, plan_id, "attention=1"))
    assert shown == IN_ATTENTION


# ---------------------------------------------------------------------------
# Q2 - THE MEMBERS A COMMAND DOES NOT SEE
#
# `pam enrich ocr` is driven through the CLI over the real `ocr` enricher and the real `Engine`,
# whose sessions are `ImprintFactory`'s scripted fakes (the repo venv has no `[ocr]` extra).
# ---------------------------------------------------------------------------

SCAN = "Scans/print.png"
SIDECAR = "Scans/print.xmp"
SCAN_DATE = "1998-07-04"
MTIME = datetime(2024, 5, 1, 12, 0, 0)


def _scan_with_sidecar(tmp_path, pack):
    root = tmp_path / "photos"
    (root / "Scans").mkdir(parents=True)
    Image.new("RGB", (64, 32), (250, 250, 250)).save(root / SCAN)
    (root / SIDECAR).write_bytes(b"<x:xmpmeta>the only copy of the sidecar</x:xmpmeta>")
    for rel in (SCAN, SIDECAR):
        (root / f"{rel}.tags.json").write_text(json.dumps({"PamTestSidecar": rel}), "utf-8")
        os.utime(root / rel, (MTIME.timestamp(), MTIME.timestamp()))
    run_scan(root, exiftool_command=FAKE_COMMAND)
    workspace_for(root).config_path.write_text(
        f"[enrich.ocr]\nenabled = true\nfolders = [\"Scans\"]\nmodel_dir = '{pack}'\n",
        encoding="utf-8",
    )
    return root


def _ocr_rows(root):
    """`{rel_path: value}` of every `ocr_text` row, the suite's own SQL."""
    conn = _conn(root)
    try:
        rows = conn.execute(
            "SELECT f.rel_path, i.value_json FROM inferred_facts i"
            " JOIN files f ON f.sha256 = i.sha256 WHERE i.kind = 'ocr_text'"
        ).fetchall()
    finally:
        conn.close()
    return {row["rel_path"]: json.loads(row["value_json"]) for row in rows}


def _ocr_paragraph(out):
    """The `ocr` paragraph's first line and its `skipped:` line (design 12 rev 28, P13)."""
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("ocr: "))
    skipped = next(line for line in lines[start:] if line.startswith("  skipped: "))
    return lines[start], skipped


def _sha_of(root, rel):
    conn = _conn(root)
    try:
        return conn.execute("SELECT sha256 FROM files WHERE rel_path = ?", (rel,)).fetchone()[0]
    finally:
        conn.close()


# The archive holds FOUR files: the scan, its sidecar, and the two `.tags.json` files the fake
# exiftool reads (type `other`). Design 4: "'sidecar'/'other' never hashed", so three of the four
# have no sha256, and design 8.1 rev 16 skips a file with no sha256 BEFORE `wants()` - the runner
# counts it "never read from disk", never "not applicable".
FIRST_RUN = (
    "ocr: 1 of 4 files read.",
    "  skipped: 0 already done, 0 not applicable, 0 identical to another file,"
    " 3 never read from disk.",
)
SECOND_RUN = (
    "ocr: 0 of 4 files read.",
    "  skipped: 1 already done, 0 not applicable, 0 identical to another file,"
    " 3 never read from disk.",
)


def test_q2_the_sidecar_ocr_never_sees_is_uncovered_and_still_takes_the_scans_date(
    tmp_path, monkeypatch, capsys
):
    """THE MEMBER `pam enrich ocr` DOES NOT SEE, and what that does to the name: nothing.

    The plan's candidate says `wants()` is why; the code says the sidecar never GETS that far.
    Design 4: sidecars are never hashed. Design 8.1 rev 16: a file with no sha256 is skipped
    before `wants()` (an `inferred_facts` row is filed by content, so there is nothing to file
    it against). Either way the outcome is design 8.1's: no `ocr_text` row, so no coverage
    marker, and "coverage counts files PROCESSED" - `pam status` counts one file, not two. A
    second run reads nothing, so the uncovered sidecar is not retried forever.

    And the NAME is untouched by it. Spec 5.4/6.1 name the unit from its primary: the sidecar
    takes the scan's OCR date, tier and attention; it sits in no event (no `capture` row, spec
    8.3) while the nav still files it under the scan's month.

    Mutation: delete the runner's `file.sha256 is None` skip (`enrich/base.py`) -> the sidecar
    reaches `wants()`, is counted "not applicable", and the `skipped:` line fails. (Making
    `OcrEnricher.wants` accept sidecars is an INVALID mutant here - the file never reaches it.)"""
    pack = fake_pack(tmp_path / "pack", monkeypatch)
    root = _scan_with_sidecar(tmp_path, pack)
    assert _sha_of(root, SIDECAR) is None and _sha_of(root, SCAN) is not None
    factory = ImprintFactory([(SCAN_DATE, 0.75)])
    monkeypatch.setattr(ocr_model, "_onnx_session", factory)
    monkeypatch.setattr(ocr_model, "_missing_extra", lambda: [])

    capsys.readouterr()
    assert main(["enrich", "ocr", "--archive", str(root)]) == 0
    first = capsys.readouterr().out
    assert _ocr_rows(root) == {SCAN: {"lines": [{"text": SCAN_DATE, "score": 0.75}]}}
    assert factory.seen == 1, "the engine was handed a file other than the scan"
    assert _ocr_paragraph(first) == FIRST_RUN, first

    assert main(["enrich", "ocr", "--archive", str(root)]) == 0
    second = capsys.readouterr().out
    assert _ocr_paragraph(second) == SECOND_RUN, second
    assert factory.seen == 1

    assert main(["status", "--archive", str(root)]) == 0
    status = capsys.readouterr().out
    assert "ocr 1" in status and "ocr 2" not in status, status

    summary = run_plan(root)
    plan_id = summary.plan_id
    assert _new_names(root, plan_id) == {
        SCAN: "1998-07-04_001.png",
        SIDECAR: "1998-07-04_001.xmp",
    }
    assert _events_of(root).keys() == {SCAN}, "the sidecar was grouped into an event"

    def work(server):
        cards = _ok(server, "GET", f"/plan/{plan_id}/items?limit=200")["items"]
        month = _listed(server, plan_id, "group=1998-07")
        return {card["old_rel_path"]: card for card in cards}, month

    cards, month = _drive(plan_id, root, work)
    for rel in (SCAN, SIDECAR):
        assert (cards[rel]["date"], cards[rel]["tier"], cards[rel]["attention"]) == (
            SCAN_DATE,
            "medium",
            True,
        ), rel
    assert month == [SCAN, SIDECAR]


def test_q2_a_non_primary_members_own_camera_date_is_disclosed(tmp_path):
    """The planner's blind member (design 5.4 rev 6 / 6.1): a unit is named from its PRIMARY
    alone. A display image with no camera date and a reading, beside a RAW that DOES carry an
    EXIF date years away, is named by the OCR date - the RAW's camera date names nothing.

    RECORDED, NOT A BREACH: no file is lost, moved apart or overwritten (S1-S3 hold), the item is
    `medium` and in `attention`, and the primary-only rule predates M12. Until M13 the two dates
    met with no flag; this test pinned that so any change would be deliberate. M13 Task 6 (C3,
    design 6.4 S10) made it: the name is unchanged, and EVERY member's item now carries a reason
    naming the RAW and its camera date (`tests/test_unit_date_disagreement.py` owns the rule).
    """
    root = build(tmp_path, {PRIMARY: None, RAW: "2003:03:14 10:00:00"})
    add_reading(root, PRIMARY, [(PRIMARY_DATE, 0.8)])
    summary = run_plan(root)
    assert _new_names(root, summary.plan_id) == NAMES
    conn = _conn(root)
    try:
        sources = dict(
            conn.execute(
                "SELECT f.rel_path, c.capture_dt_source FROM capture c"
                " JOIN files f ON f.id = c.file_id"
            ).fetchall()
        )
        reasons = {
            row[0]: json.loads(row[1])
            for row in conn.execute(
                "SELECT old_rel_path, trust_reasons_json FROM plan_items WHERE plan_id = ?",
                (summary.plan_id,),
            )
        }
    finally:
        conn.close()
    assert sources == {PRIMARY: "file_mtime", RAW: "exif"}
    disclosed = (
        "Another file in this group, Scans/pair.nef, was dated 2003-03-14 by its camera"
        " - more than a year from the date in this name."
    )
    for rel in (PRIMARY, RAW):
        assert [r for r in reasons[rel] if "2003" in r] == [disclosed], reasons[rel]


# ---------------------------------------------------------------------------
# Q3 - THE ENDPOINTS, ENUMERATED (never the function)
# ---------------------------------------------------------------------------

# The WHOLE live route table at M12, written by hand. M11's hunt classified the POST routes;
# M12 adds no route at all, so the GET half is pinned too - a new GET is not a write path, but
# a route table that grew without this list noticing is the drift this question exists for.
READ_ROUTES = {
    ("GET", "ping"),
    ("GET", "plan/{id}"),
    ("GET", "plan/{id}/groups"),
    # M14 Task 2 (U25): the review list's groups, a read.
    ("GET", "plan/{id}/list"),
    ("GET", "plan/{id}/items"),
    ("GET", "thumb/{file_id}"),
    ("GET", "faces/clusters"),
    ("GET", "faces/cluster/{id}"),
    ("GET", "faces/crop/{face_id}"),
    ("GET", "faces/persons"),
    ("GET", "faces/ignored"),
    ("GET", "faces/person/{id}"),
}
# Routes that decide, rename or restore PLAN ITEMS - each must widen over the WHOLE plan.
PLAN_WRITE_ROUTES = {
    ("POST", "plan/{id}/decide"),
    ("POST", "plan/{id}/decide-filter"),
    # M14 Task 3 (U17): the selection's decision - widens over the whole plan like decide-filter.
    ("POST", "plan/{id}/decide-selection"),
    ("POST", "item/{id}/rename"),
    ("POST", "item/{id}/restore"),
}
# Routes that write faces and people and reach no plan item.
FACES_WRITE_ROUTES = {
    ("POST", "faces/cluster/{id}/label"),
    ("POST", "faces/cluster/{id}/dismiss"),
    ("POST", "faces/cluster/{id}/split"),
    ("POST", "faces/faces/state"),
    ("POST", "faces/faces/assign"),
    ("POST", "faces/person/{id}"),
    ("POST", "faces/person/{id}/merge"),
}
# M14 Task 3 (U17): a POST that writes NOTHING. The selection's preview is a POST only because a
# selection is a body; it is classified here rather than as a write, and its own module spies on
# the write and on `total_changes` to prove it.
READ_ONLY_POST_ROUTES = {("POST", "plan/{id}/selection")}
assert READ_ROUTES and PLAN_WRITE_ROUTES and FACES_WRITE_ROUTES and READ_ONLY_POST_ROUTES


def test_q3_the_live_route_table_is_exactly_the_hand_written_one(tmp_path):
    """M12 adds no route: the table the server really serves equals these three lists, entry by
    entry, and every route is classified exactly once.

    Mutation: add any row to `write_api.WRITE_HANDLERS` (or any handler table) -> this fails
    naming the unclassified route."""
    root, plan_id = _ocr_unit(tmp_path)
    served = Served(root, plan_id)
    try:
        table = set(served.server.api_handlers)
    finally:
        served.close()
    expected = READ_ROUTES | PLAN_WRITE_ROUTES | FACES_WRITE_ROUTES | READ_ONLY_POST_ROUTES
    assert table == expected, sorted(table.symmetric_difference(expected))
    assert not (READ_ONLY_POST_ROUTES & (PLAN_WRITE_ROUTES | FACES_WRITE_ROUTES))
    assert not (READ_ROUTES & PLAN_WRITE_ROUTES)
    assert not (READ_ROUTES & FACES_WRITE_ROUTES)
    assert not (PLAN_WRITE_ROUTES & FACES_WRITE_ROUTES)
    assert {method for method, _ in READ_ROUTES} == {"GET"}
    assert {method for method, _ in PLAN_WRITE_ROUTES | FACES_WRITE_ROUTES} == {"POST"}
    assert {method for method, _ in READ_ONLY_POST_ROUTES} == {"POST"}


def test_q3_no_faces_route_reaches_a_plan_item_writer():
    """About the CODE the faces routes reach, not their names: no faces module calls a plan
    item writer or reads the plan."""
    for module in (faces_api, faces_manage_api, faces_split_api):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for writer in ("set_plan_item_status", "set_plan_item_new_filename", "plan_rows"):
            assert writer not in source, f"{module.__name__} reaches {writer}"


def test_q3_decide_naming_ONE_member_writes_the_whole_unit(tmp_path):
    """`decide` with the RAW's id alone. Mutation: `decide` widens over `[]` or over the named
    items only -> the primary stays proposed."""
    root, plan_id = _ocr_unit(tmp_path)
    ids = _item_ids(root, plan_id)

    reply = _drive(
        plan_id,
        root,
        lambda server: _ok(
            server,
            "POST",
            f"/plan/{plan_id}/decide",
            {"action": "skip", "item_ids": [ids[RAW]]},
        ),
    )
    assert reply["decided"] == 2
    assert reply["companions"] == [ids[PRIMARY]]
    assert _statuses(root, plan_id) == {PRIMARY: "skipped", RAW: "skipped"}


def test_q3_rename_and_restore_of_ONE_member_move_the_whole_units_stem(tmp_path):
    """`rename` on the RAW names the unit's stem for BOTH members (and approves both); `restore`
    on the PRIMARY puts the planner's names back on both. Mutation: `write_api._unit` returns
    `[clicked]` instead of its `unit_key` group -> one member keeps the other stem."""
    root, plan_id = _ocr_unit(tmp_path)
    ids = _item_ids(root, plan_id)

    def work(server):
        renamed = _ok(
            server, "POST", f"/item/{ids[RAW]}/rename", {"new_filename": "Grandad.nef"}
        )
        after_rename = (_new_names(root, plan_id), _statuses(root, plan_id))
        restored = _ok(server, "POST", f"/item/{ids[PRIMARY]}/restore")
        return renamed, after_rename, restored

    renamed, (names, statuses), restored = _drive(plan_id, root, work)

    assert names == {PRIMARY: "Grandad.png", RAW: "Grandad.nef"}
    assert statuses == {PRIMARY: "approved", RAW: "approved"}
    assert sorted(m["id"] for m in renamed["members"]) == sorted(ids.values())
    assert _new_names(root, plan_id) == NAMES
    assert _statuses(root, plan_id) == {PRIMARY: "approved", RAW: "approved"}
    assert sorted(m["id"] for m in restored["members"]) == sorted(ids.values())


# ---------------------------------------------------------------------------
# Q4 - THE FACES AN OPERATION SEES vs THE FACES THE PAGE SHOWED, against Task 5's responses
#
# M11's shape (S2-F1): one cluster holding a NAMED face and an unnamed remainder. M12 Task 5
# made the queue report `hidden_below_floor` and `floor`, and every write report `named` /
# `remaining`; and A11's floor (`[tiers] faces` floor = 0.4 by default, design 11) means a
# detection BELOW it is in the cluster and on no page. Each construction asks whether the
# write touched exactly what the page showed, and whether the counts it returned are true.
# ---------------------------------------------------------------------------

FLOOR = 0.4  # design 11's default `[tiers] faces` floor, spelled here


def _faces_archive(tmp_path, *, named_all=False):
    """Four photographs, one face each, all in cluster 2: Blair's (0.9), two unnamed (0.9), and
    one unnamed BELOW the floor (0.3). Returns (root, blair_id, [blair, a, b, low] face ids)."""
    root = tmp_path / "photos"
    root.mkdir()
    for index in range(4):
        (root / f"P{index}.JPG").write_bytes(b"content of P%d" % index)
        (root / f"P{index}.JPG.tags.json").write_text(
            json.dumps({"DateTimeOriginal": "2019:07:04 18:23:11"}), encoding="utf-8"
        )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    ws = workspace_if_exists(root)
    assert ws is not None
    conn = connect(ws.db_path)
    try:
        # The photographs only: the `.tags.json` files the fake exiftool reads are in `files`
        # too, with no sha256 - `zip(..., strict=True)` below is what caught a draft of this
        # query planting faces on the string "None".
        shas = [
            str(r[0])
            for r in conn.execute(
                "SELECT sha256 FROM files WHERE filename LIKE 'P%.JPG' ORDER BY filename"
            )
        ]
        assert len(shas) == 4 and "None" not in shas, shas
        blair = int(conn.execute("INSERT INTO persons (label) VALUES ('Blair')").lastrowid or 0)
        faces = []
        for position, (sha, confidence) in enumerate(
            zip(shas, (0.9, 0.9, 0.9, 0.3), strict=True)
        ):
            person = blair if position == 0 or (named_all and confidence >= FLOOR) else None
            row = conn.execute(
                "INSERT INTO face_instances (sha256, bbox_json, embedding, det_confidence,"
                " cluster_id, person_id, model_name, model_version)"
                " VALUES (?, ?, ?, ?, 2, ?, 'yunet+sface', '1')",
                (sha, json.dumps(GEOMETRY), blob([0.1, 0.2, 0.3, 0.4]), confidence, person),
            )
            faces.append(int(row.lastrowid or 0))
        conn.commit()
    finally:
        conn.close()
    return root, blair, faces


def _face_rows(root):
    """`{face id: (person label or None, review_state)}`, the suite's own SQL."""
    conn = _conn(root)
    try:
        rows = conn.execute(
            "SELECT a.id, p.label, a.review_state FROM face_instances a"
            " LEFT JOIN persons p ON p.id = a.person_id"
        ).fetchall()
    finally:
        conn.close()
    return {int(r[0]): (r[1], r[2]) for r in rows}


def test_q4_label_writes_exactly_the_faces_the_queue_offered_and_its_counts_are_true(tmp_path):
    """The queue shows cluster 2 as TWO faces (the unnamed pair above the floor), samples exactly
    those two, hides nothing yet, and reports one group named (Blair's share) and one remaining.
    Naming it writes those two and NOT Blair's face and NOT the below-floor face - and the counts
    the write returns, and the queue after it, say: two named, none remaining, one group now
    hidden below the floor (the below-floor face's remainder, which no page showed).

    Mutation: `repo.set_person` drops its floor operand -> the 0.3 face is named too, the write's
    count disagrees with the page's and the label is a 409 (and this test fails)."""
    root, _, (blair, a, b, low) = _faces_archive(tmp_path)
    served = Served(root)
    try:
        status, queue = served.json("GET", "/faces/clusters")
        assert status == 200, queue
        status, written = served.json("POST", "/faces/cluster/2/label", {"person": "Dana"})
        assert status == 200, written
        status, after = served.json("GET", "/faces/clusters")
        assert status == 200, after
    finally:
        served.close()

    (group,) = queue["clusters"]
    assert (group["cluster_id"], group["faces"], group["photographs"]) == (2, 2, 2)
    assert {face["face_id"] for face in group["sample"]} == {a, b}
    assert (queue["named"], queue["remaining"], queue["hidden_below_floor"]) == (1, 1, 0)
    assert queue["floor"] == FLOOR

    assert (written["faces"], written["photographs"], written["face_ids"]) == (
        2,
        2,
        sorted([a, b]),
    )
    assert (written["named"], written["remaining"]) == (2, 0)
    assert _face_rows(root) == {
        blair: ("Blair", None),
        a: ("Dana", None),
        b: ("Dana", None),
        low: (None, None),
    }
    assert after["clusters"] == []
    assert (after["named"], after["remaining"], after["hidden_below_floor"]) == (2, 0, 1)


def test_q4_dismiss_ignores_exactly_the_faces_the_queue_offered(tmp_path):
    """`Ignore` on the same cluster: the two shown faces are dismissed; Blair's face and the
    below-floor face are untouched, and the counts it returns are Blair's group named and
    nothing remaining. Mutation: `dismiss` writes by `cluster_id` at floor 0.0 -> the 0.3 face
    is dismissed too."""
    root, _, (blair, a, b, low) = _faces_archive(tmp_path)
    served = Served(root)
    try:
        status, dismissed = served.json("POST", "/faces/cluster/2/dismiss")
        assert status == 200, dismissed
        status, after = served.json("GET", "/faces/clusters")
    finally:
        served.close()

    assert dismissed["faces"] == 2, dismissed
    assert (dismissed["named"], dismissed["remaining"]) == (1, 0)
    assert _face_rows(root) == {
        blair: ("Blair", None),
        a: (None, "dismissed"),
        b: (None, "dismissed"),
        low: (None, None),
    }
    assert (after["named"], after["remaining"], after["hidden_below_floor"]) == (1, 0, 1)


def test_q4_a_cluster_named_above_the_floor_is_refused_BY_NAME_even_with_a_hidden_remainder(
    tmp_path,
):
    """Every face ABOVE the floor is Blair's; one unnamed face sits below it. The queue shows no
    group and says one is hidden below the floor - and a stale page's `label` on cluster 2 is
    M11's 409 that NAMES Blair, writing nothing: the hidden face is not quietly named through a
    group the page never offered."""
    root, _, faces = _faces_archive(tmp_path, named_all=True)
    before = _face_rows(root)
    served = Served(root)
    try:
        status, queue = served.json("GET", "/faces/clusters")
        assert status == 200, queue
        status, refusal = served.json("POST", "/faces/cluster/2/label", {"person": "Dana"})
    finally:
        served.close()

    assert queue["clusters"] == []
    assert (queue["named"], queue["remaining"], queue["hidden_below_floor"]) == (1, 0, 1)
    assert status == 409, (status, refusal)
    assert "Blair" in json.dumps(refusal), refusal
    assert _face_rows(root) == before
    assert before[faces[3]] == (None, None)


# ---------------------------------------------------------------------------
# Q5 - WHAT A WRITE IN ONE COMMAND DOES TO AN ARTEFACT ANOTHER COMMAND ALREADY PRODUCED, OR TO
# A READER THAT IS STILL OPEN (`m11.md` 4; plan Task 14 Step 2 item 5)
#
# The archive is Q2's: one real PNG under `Scans/` with its `.xmp` sidecar (a companion unit
# of two), read by the real `ocr` enricher over `ImprintFactory`'s scripted sessions. Every
# expected name below is spelled here: the reading's date is 1998-07-04 and the mtime is
# 2024-05-01, so the reviewed name is `1998-07-04_001` and the name a catalog whose reading
# no longer yields a date falls back to is `2024-05-01_001`.
# ---------------------------------------------------------------------------

Q5_OCR = "[enrich.ocr]\nenabled = true\nfolders = [{folders}]\nmodel_dir = '{pack}'\n"
REVIEWED = {SCAN: "1998-07-04_001.png", SIDECAR: "1998-07-04_001.xmp"}
FALLBACK = {SCAN: "2024-05-01_001.png", SIDECAR: "2024-05-01_001.xmp"}
NO_DATE_TEXT = "HELLO"  # a line the date grammar cannot read a date out of


def _configure_ocr(root, pack, folders, extra=""):
    workspace_for(root).config_path.write_text(
        Q5_OCR.format(folders=folders, pack=pack) + extra, encoding="utf-8"
    )


def _q5_archive(tmp_path, monkeypatch, *, folders='"Scans"', loose=None, enrich=True):
    """Scan Q2's unit (plus, optionally, one mtime-dated file OUTSIDE `Scans/` at `loose`),
    configure OCR over `folders`, and - unless `enrich` is False - run the real `pam enrich
    ocr` once. Returns (root, pack). The engine reads exactly one image, the scan, as
    `SCAN_DATE` at 0.75."""
    pack = fake_pack(tmp_path / "pack", monkeypatch)
    root = tmp_path / "photos"
    (root / "Scans").mkdir(parents=True)
    Image.new("RGB", (64, 32), (250, 250, 250)).save(root / SCAN)
    (root / SIDECAR).write_bytes(b"<x:xmpmeta>the only copy of the sidecar</x:xmpmeta>")
    if loose is not None:
        (root / loose).parent.mkdir(parents=True, exist_ok=True)
        (root / loose).write_bytes(b"the only copy of the loose photo")
    for rel in (SCAN, SIDECAR, *([loose] if loose else [])):
        os.utime(root / rel, (MTIME.timestamp(), MTIME.timestamp()))
    run_scan(root, exiftool_command=FAKE_COMMAND)
    _configure_ocr(root, pack, folders)
    monkeypatch.setattr(ocr_model, "_missing_extra", lambda: [])
    factory = ImprintFactory([(SCAN_DATE, 0.75)])
    monkeypatch.setattr(ocr_model, "_onnx_session", factory)
    if enrich:
        assert main(["enrich", "ocr", "--archive", str(root)]) == 0
        assert factory.seen == 1
    return root, pack


def _readings_of(root, rel):
    """Every `ocr_text` row for `rel`'s content, oldest first: (model_version, lines)."""
    conn = _conn(root)
    try:
        rows = conn.execute(
            "SELECT i.model_version, i.value_json FROM inferred_facts i"
            " JOIN files f ON f.sha256 = i.sha256"
            " WHERE i.kind = 'ocr_text' AND f.rel_path = ? ORDER BY i.id",
            (rel,),
        ).fetchall()
    finally:
        conn.close()
    return [(row["model_version"], json.loads(row["value_json"])["lines"]) for row in rows]


def _plans(root):
    """`[(id, status)]` of every plan, the suite's own SQL."""
    conn = _conn(root)
    try:
        return [tuple(row) for row in conn.execute("SELECT id, status FROM plans ORDER BY id")]
    finally:
        conn.close()


def _latest_session(root):
    conn = _conn(root)
    try:
        return str(
            conn.execute("SELECT session_id FROM journal ORDER BY id DESC LIMIT 1").fetchone()[
                0
            ]
        )
    finally:
        conn.close()


def _cards(server, plan_id):
    reply = _ok(server, "GET", f"/plan/{plan_id}/items?limit=200")
    return {card["old_rel_path"]: card for card in reply["items"]}


def _new_identity(root, pack, monkeypatch):
    """A second `pam enrich ocr` under a NEW identity (design 8.1 rev 28: the digest moves
    with `OCR_PIPELINE_VERSION`) whose reading finds no date. Not cached, so it re-reads."""
    monkeypatch.setattr(ocr_model, "OCR_PIPELINE_VERSION", "q5-second-identity")
    factory = ImprintFactory([(NO_DATE_TEXT, 0.75)])
    monkeypatch.setattr(ocr_model, "_onnx_session", factory)
    assert main(["enrich", "ocr", "--archive", str(root)]) == 0
    assert factory.seen == 1, "the second identity did not re-read the scan"
    readings = _readings_of(root, SCAN)
    assert [lines for _, lines in readings] == [
        [{"text": SCAN_DATE, "score": 0.75}],
        [{"text": NO_DATE_TEXT, "score": 0.75}],
    ]
    assert readings[0][0] != readings[1][0], "the two readings share one identity"


def _floor_raised(root, pack, monkeypatch):
    """`[tiers] ocr.floor` raised above the reading's 0.75 (`low` raised with it: design 11
    rev 24 orders `0 <= floor <= low <= high <= 1`)."""
    _configure_ocr(
        root, pack, '"Prints", "Scans"', "\n[tiers]\nocr = { floor = 0.8, low = 0.8 }\n"
    )


def _folder_removed(root, pack, monkeypatch):
    """`Scans` removed from `[enrich.ocr] folders`; the other entry stays."""
    _configure_ocr(root, pack, '"Prints"')


Q5_CHANGES = {
    "a_second_enrich_under_a_new_identity_finds_no_date": _new_identity,
    "the_ocr_floor_raised_above_the_reading": _floor_raised,
    "the_scans_folder_removed_from_the_ocr_folders": _folder_removed,
}
assert len(Q5_CHANGES) == 3  # the plan's three, spelled out; an empty parametrize would SKIP


@pytest.mark.parametrize("change", sorted(Q5_CHANGES))
def test_q5_an_open_plan_applies_the_REVIEWED_name_after_a_later_ocr_change(
    tmp_path, monkeypatch, capsys, change
):
    """P11 (design 6.4 rev 27): a superseded source fact is "deliberately NOT a refusal" at
    apply - "the name in an open plan is the name a human reviewed", and a newer OCR run, a
    raised floor or a narrowed folder list is not a withdrawal of consent (J13 stays scoped to
    `{people}`). So each change is made AFTER the review and BEFORE `pam apply`, and:

    - the plan is still `open` (only `pam scan` and `pam plan` supersede) and the review page
      still shows the cards it showed before, field for field;
    - `pam apply` renames the unit WHOLE to the reviewed stem and changes no byte (S1, S3),
      checked on disk by Q1's path-and-hash oracle; `pam verify` passes;
    - and the change is REAL, not inert: after `pam undo` puts the files back, a fresh plan
      names the unit from its modification date instead.

    Mutation: a J13-style recompute of the OCR date at preflight, refusing when it differs ->
    `pam apply` exits 1 here and the oracle sees the old names."""
    root, pack = _q5_archive(tmp_path, monkeypatch, folders='"Prints", "Scans"')
    plan_id = run_plan(root).plan_id
    assert _new_names(root, plan_id) == REVIEWED
    ids = _item_ids(root, plan_id)

    def review(server):
        cards = _cards(server, plan_id)
        for rel in (SCAN, SIDECAR):
            card = cards[rel]
            assert (card["new_filename"], card["date"], card["tier"], card["attention"]) == (
                REVIEWED[rel],
                SCAN_DATE,
                "medium",
                True,
            ), rel
        body = {"action": "approve", "item_ids": [ids[SCAN]]}
        assert _ok(server, "POST", f"/plan/{plan_id}/decide", body)["decided"] == 2
        return cards

    reviewed = _drive(plan_id, root, review)
    assert _statuses(root, plan_id) == {SCAN: "approved", SIDECAR: "approved"}

    Q5_CHANGES[change](root, pack, monkeypatch)
    capsys.readouterr()

    assert _plans(root) == [(plan_id, "open")]
    shown = _drive(plan_id, root, lambda server: _cards(server, plan_id))
    assert shown == {rel: {**card, "status": "approved"} for rel, card in reviewed.items()}

    before = _hashes(root)
    monkeypatch.setattr("builtins.input", lambda: "apply")
    assert main(["apply", str(plan_id), "--archive", str(root)]) == 0
    capsys.readouterr()
    moved = {rel: f"Scans/{name}" for rel, name in REVIEWED.items()}
    assert _hashes(root) == {moved.get(rel, rel): digest for rel, digest in before.items()}
    assert _statuses(root, plan_id) == {SCAN: "applied", SIDECAR: "applied"}
    assert main(["verify", "--archive", str(root)]) == 0
    capsys.readouterr()

    assert main(["undo", _latest_session(root), "--archive", str(root)]) == 0
    capsys.readouterr()
    assert _hashes(root) == before
    assert _new_names(root, run_plan(root).plan_id) == FALLBACK


# --- Q5, concurrent: `pam status` (lock-free by design) read across a writer's commit ------
#
# `pam status` reads one archive through ONE read-only connection, in eight separate SELECTs
# outside any transaction (`status._read_archive`), so each statement sees whatever was
# committed when IT ran. The interleaving is made DETERMINISTIC rather than raced: the reader
# runs in a thread, one of the eight reads is wrapped to stop the READER THREAD (and only it)
# right after that read returns, the writer runs to completion in the main thread, and the
# reader is released. Barriers, never sleeps; a reader that never reaches its pause fails at a
# deadline. Pausing after every read in turn covers every place a commit can fall.
#
# The eight reads, in the order `_read_archive` makes them - an INSTRUMENT, not an oracle: if
# the order or the set changes, the pause either is not reached (a loud failure) or moves.
STATUS_READS = (
    "sessions",
    "media_type_counts",
    "status_counts",
    "placeholder_count",
    "last_scanned_at",
    "enrichment_coverage",
    "plan_status_counts",
    "plans_in_status",
)
assert len(STATUS_READS) == len(set(STATUS_READS)) == 8  # eight DISTINCT pauses (M2331)
BARRIER_S = 60  # a deadline for a barrier that should open at once, never a wait for an effect


def _status_block(root, *, ocr, plans, step_plan):
    """`pam status --archive` for this archive, SPELLED BY HAND from spec 12 / UX 3's layout:
    two files (the scan and its sidecar), the five enrichers' coverage, the plans line, and the
    closing block - `pam plan`/`pam enrich` with no open plan, else review/apply of the open
    one. The `Last scanned` line is a wall-clock time and is masked (see `_masked`)."""
    if step_plan is None:
        steps = [
            "Next: pam plan   (propose new names - renames nothing)",
            "  or: pam enrich   (add AI understanding first)",
        ]
        approved = []
    else:
        steps = [
            f"Next: pam review {step_plan}   (see & approve the plan)",
            f"  or: pam apply {step_plan}   (renames only what you approve)",
        ]
        # M14 Y1: the open plan's approved line - the scan and its sidecar, nothing decided.
        approved = [f"  Plan {step_plan}: 0 of 2 names approved."]
    return [
        str(root),
        "  2 files: 1 image, 1 sidecar",
        "  2 present, 0 missing, 0 cloud-only",
        "  Last scanned <masked>",
        "  Enrichment coverage, of 2 files:",
        f"    caption 0   faces 0   ocr {ocr}   phash 0   geocode 0",
        f"  Plans: {plans}",
        *approved,
        "  No renames have been applied here yet.",
        *steps,
    ]


def _masked(out):
    """The status text as lines, with the one wall-clock line masked. Returns (lines, the
    masked line) so the caller can assert it did not move across the writer."""
    lines = out.splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("  Last scanned "))
    real = lines[index]
    lines[index] = "  Last scanned <masked>"
    return lines, real


def _status_across(monkeypatch, capsys, root, pause_after, writer):
    """Run `pam status` in a thread, stop it right after its `pause_after` read, run `writer`
    to completion here, release it. Returns the status text the reader printed."""
    reached, release = threading.Event(), threading.Event()
    result = {}

    def read():
        result["code"] = main(["status", "--archive", str(root)])

    reader = threading.Thread(target=read, daemon=True)
    original = getattr(repo, pause_after)

    def paused(*args, **kwargs):
        value = original(*args, **kwargs)
        if threading.current_thread() is reader and not reached.is_set():
            reached.set()
            assert release.wait(BARRIER_S), "the writer never released the reader"
        return value

    monkeypatch.setattr(repo, pause_after, paused)
    capsys.readouterr()
    reader.start()
    try:
        assert reached.wait(BARRIER_S), f"pam status never made its {pause_after} read"
        assert writer() == 0
        # The reader is still parked at its pause - so nothing it prints can have been printed
        # while the writer ran, and the line below swallows only the writer's output (M2330).
        assert reader.is_alive() and not release.is_set()
        capsys.readouterr()  # the writer's own output, which is not the reader's
    finally:
        release.set()
        reader.join(BARRIER_S)
    assert not reader.is_alive()
    assert result["code"] == 0
    return capsys.readouterr().out


def _enrich_writer(root):
    return lambda: main(["enrich", "ocr", "--archive", str(root)])


def _plan_writer(root):
    return lambda: main(["plan", "--archive", str(root)])


# THE FINDING (Q5, M12): `plan_status_counts` and `plans_in_status` are two statements, so a
# `pam plan` that commits between them is read as "Plans: 1 open" (before: only plan 1 exists)
# beside "Next: pam review 2" (after: plan 2 is the open one) - ONE plan, open, numbered 2,
# which the catalog never held. No file is touched (S1-S4 hold: `pam status` writes nothing),
# and a second `pam status` is right; but it is a reader reporting a state that never existed.
# FIXED BY M13 Task 4 (C1, S13): `_read_archive` reads inside ONE `read_snapshot`, and the
# strict xfail that carried the finding XPASSED and was removed in that commit - every case
# below is now a plain test.
class NeverExisted(AssertionError):
    """A reader reported a state that is neither the before-state nor the after-state. Its own
    type, so the failure names the finding rather than a barrier or a precondition."""


STATUS_CASES = [
    *[pytest.param("enrich", read, id=f"enrich-after-{read}") for read in STATUS_READS],
    *[pytest.param("plan", read, id=f"plan-after-{read}") for read in STATUS_READS],
]


def test_q5_the_status_instrument_pauses_at_every_read_status_makes_and_marks_one_case():
    """The instrument's own control (M2331). `STATUS_READS` is hand-written; here it is compared
    with the `repo.<name>(...)` calls `status._read_archive` actually makes, in order, read off
    its AST - so a read added, removed, renamed or reordered in production moves no pause
    silently. And NO case carries a mark any more (M13 Task 4): the finding is fixed, so an
    xfail or skip reappearing on any case would hide a regression of S13 with the suite green."""
    import ast
    import inspect

    from pam import status

    tree = ast.parse(inspect.getsource(status._read_archive))
    calls = sorted(
        (node.lineno, node.col_offset, node.func.attr)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "repo"
    )
    assert [name for _, _, name in calls] == list(STATUS_READS)
    assert [case.id for case in STATUS_CASES if case.marks] == []
    assert len(STATUS_CASES) == 2 * len(STATUS_READS) == 16


@pytest.mark.parametrize(("writer", "pause_after"), STATUS_CASES)
def test_q5_pam_status_read_across_a_writers_commit_reports_before_or_after(
    tmp_path, monkeypatch, capsys, writer, pause_after
):
    """Each reading must equal the state BEFORE the writer or the state AFTER it, both spelled
    by hand - nothing in between.

    `pam enrich ocr` changes one thing `pam status` shows (the `ocr` coverage, 0 -> 1), so no
    interleaving can mix two of its fields. `pam plan` supersedes plan 1 and opens plan 2 in
    one commit, and `pam status` shows that in TWO places read by two statements - which was
    M12's finding, held by a strict xfail until M13 Task 4 read them in one snapshot (S13).

    Mutation: remove `_read_archive`'s `read_snapshot` -> `plan-after-plan_status_counts`
    raises NeverExisted."""
    if writer == "enrich":
        root, _ = _q5_archive(tmp_path, monkeypatch, enrich=False)
        before = _status_block(root, ocr=0, plans="none", step_plan=None)
        after = _status_block(root, ocr=1, plans="none", step_plan=None)
        run = _enrich_writer(root)
    else:
        root, _ = _q5_archive(tmp_path, monkeypatch)
        assert run_plan(root).plan_id == 1
        before = _status_block(root, ocr=1, plans="1 open", step_plan=1)
        after = _status_block(root, ocr=1, plans="1 open, 1 superseded", step_plan=2)
        run = _plan_writer(root)

    capsys.readouterr()
    assert main(["status", "--archive", str(root)]) == 0
    baseline, scanned = _masked(capsys.readouterr().out)
    assert baseline == before, "the hand-spelled BEFORE state is not what pam status prints"

    seen, scanned_seen = _masked(_status_across(monkeypatch, capsys, root, pause_after, run))
    assert scanned_seen == scanned
    if writer == "plan":
        assert _plans(root) == [(1, "superseded"), (2, "open")]
    if seen not in (before, after):
        raise NeverExisted("\n".join(seen))

    assert main(["status", "--archive", str(root)]) == 0
    assert _masked(capsys.readouterr().out)[0] == after


# --- Q5, concurrent: `pam review`'s read endpoints, and why no writer commits under them ----
#
# `pam review` is NOT one of the lock-free readers `m11.md` 4 means: it takes the workspace
# lock and holds it for the server's whole lifetime (`webui/__init__.py`, M7), precisely so a
# plan cannot be applied half-way through being decided. So "while `pam enrich ocr` writes"
# and "while `pam plan` supersedes" cannot happen under its readers - MEASURED here rather than
# assumed: with the server up, each writer is refused by the lock, writes nothing, and every
# read endpoint reports the same state after the attempt as before it. Once the server stops,
# the same two commands succeed, which proves it was the lock that refused them.
LOCKED = "Another pam command is already running against this archive."


def test_q5_pam_review_holds_the_lock_so_no_writer_commits_under_its_read_endpoints(
    tmp_path, monkeypatch, capsys
):
    """Mutation: drop `WorkspaceLock` from `run_review` -> `pam plan` exits 0 inside the
    serve callback, plan 1 is superseded under the open page, and the GETs change."""
    root, _ = _q5_archive(tmp_path, monkeypatch)
    plan_id = run_plan(root).plan_id
    readings = _readings_of(root, SCAN)
    # A new identity, so that `pam enrich ocr` WOULD re-read the scan if it were let in.
    monkeypatch.setattr(ocr_model, "OCR_PIPELINE_VERSION", "q5-second-identity")
    factory = ImprintFactory([(NO_DATE_TEXT, 0.75)])
    monkeypatch.setattr(ocr_model, "_onnx_session", factory)

    def reads(server):
        summary = _ok(server, "GET", f"/plan/{plan_id}")
        return summary, _cards(server, plan_id), _ok(server, "GET", f"/plan/{plan_id}/groups")

    def work(server):
        before = reads(server)
        capsys.readouterr()
        codes = (
            main(["enrich", "ocr", "--archive", str(root)]),
            main(["plan", "--archive", str(root)]),
        )
        refused = capsys.readouterr().err
        return before, codes, refused, reads(server)

    before, codes, refused, after = _drive(plan_id, root, work)

    summary, cards, _ = before
    assert (summary["status"], summary["total"], summary["attention"]) == ("open", 2, 2)
    assert {rel: card["new_filename"] for rel, card in cards.items()} == REVIEWED
    assert codes == (1, 1)
    assert refused.count(LOCKED) == 2, refused
    assert factory.seen == 0, "the refused enrich run read a photograph"
    assert after == before
    assert _plans(root) == [(plan_id, "open")]
    assert _readings_of(root, SCAN) == readings

    capsys.readouterr()
    assert main(["enrich", "ocr", "--archive", str(root)]) == 0
    assert main(["plan", "--archive", str(root)]) == 0
    assert factory.seen == 1
    assert _plans(root) == [(plan_id, "superseded"), (plan_id + 1, "open")]


# --- Q5, clarification 8: pam's own output read back as evidence, in a FRESH catalog --------
#
# After `pam apply`, a file dated by OCR (or by `file_mtime`) carries that date in its NAME. A
# catalog rebuilt from scratch - a new PAM_HOME, never `pam forget`, which keeps the workspace -
# reads the name with `parse_filename_date` and records a `filename` date: `medium` evidence
# minted from pam's own proposal. RECORDED, NOT FIXED (plan clarification 8): the ORIGINAL
# workspace is what preserves the evidence, and this construction measures both side by side.
#
# The second home is a directory INSIDE the test's isolated PAM_HOME, so the J6 tripwire's
# `catalogs_under` still reaches its catalog, and PAM_HOME is restored by a raw environment
# write in `finally` (conftest's `isolated_pam_home` reasoning: a monkeypatch can be undone).
LOOSE = "Phone/loose.jpg"
APPLIED = {
    SCAN: "Scans/1998-07-04_001.png",
    SIDECAR: "Scans/1998-07-04_001.xmp",
    LOOSE: "Phone/2024-05-01_001.jpg",
}
# `pam plan --template {date}`: the default template already conforms after the apply (so no
# catalog would list an item to read a tier from), and a template that drops `{seq}` gives
# every file an item whose tier and attention are those of its date token alone.
DATE_ONLY = "{date}"


def _capture_sources(root):
    """`{rel_path: (capture_dt, capture_dt_source)}`, the suite's own SQL."""
    conn = _conn(root)
    try:
        rows = conn.execute(
            "SELECT f.rel_path, c.capture_dt, c.capture_dt_source FROM capture c"
            " JOIN files f ON f.id = c.file_id"
        ).fetchall()
    finally:
        conn.close()
    return {row["rel_path"]: (row["capture_dt"], row["capture_dt_source"]) for row in rows}


def _date_only_plan(root, capsys):
    """`pam plan --template {date}`; returns `{old_rel_path: (new_filename, tier, attention)}`
    with the tier from the suite's own SQL and attention as the review page serves it."""
    capsys.readouterr()
    assert main(["plan", "--archive", str(root), "--template", DATE_ONLY]) == 0
    capsys.readouterr()
    plan_id = max(plan for plan, _ in _plans(root))
    conn = _conn(root)
    try:
        tiers = dict(
            conn.execute(
                "SELECT old_rel_path, tier FROM plan_items WHERE plan_id = ?", (plan_id,)
            ).fetchall()
        )
    finally:
        conn.close()
    cards = _drive(plan_id, root, lambda server: _cards(server, plan_id))
    return {
        rel: (cards[rel]["new_filename"], tiers[rel], cards[rel]["attention"]) for rel in tiers
    }


def test_q5_clarification_8_a_fresh_catalog_reads_pams_own_names_as_filename_dates(
    tmp_path, monkeypatch, capsys, isolated_pam_home
):
    """Original workspace: the scan is OCR-dated (`medium`, in `attention`), the loose photo is
    mtime-dated (`low`, in `attention`); both are applied. A FRESH catalog of the same folder,
    under a second PAM_HOME, records both dates as source `filename`, and plans both at
    `medium` and OUT of `attention` - the evidence that a machine read the date is gone. The
    original workspace, re-planned with the same template, still knows.

    Mutation: `scan/objective.py`'s date hierarchy skips the filename date -> the fresh
    catalog's sources read `file_mtime` and the scan's date becomes 2024-05-01."""
    root, _ = _q5_archive(tmp_path, monkeypatch, loose=LOOSE)
    plan_id = run_plan(root).plan_id
    assert _new_names(root, plan_id) == {
        rel: path.split("/")[1] for rel, path in APPLIED.items()
    }
    approve_archive(root, plan_id)  # F1: apply renames only what was approved
    monkeypatch.setattr("builtins.input", lambda: "apply")
    assert main(["apply", str(plan_id), "--archive", str(root)]) == 0
    assert set(_hashes(root)) == set(APPLIED.values())

    mtime = "2024-05-01T12:00:00"
    original = {
        APPLIED[SCAN]: (mtime, "file_mtime"),
        APPLIED[LOOSE]: (mtime, "file_mtime"),
    }
    assert _capture_sources(root) == original
    assert _date_only_plan(root, capsys) == {
        APPLIED[SCAN]: ("1998-07-04.png", "medium", True),
        APPLIED[SIDECAR]: ("1998-07-04.xmp", "medium", True),
        APPLIED[LOOSE]: ("2024-05-01.jpg", "low", True),
    }

    second = isolated_pam_home / "second-home"
    os.environ["PAM_HOME"] = str(second)
    try:
        assert workspace_if_exists(root) is None, "the second home is not a fresh catalog"
        run_scan(root, exiftool_command=FAKE_COMMAND)
        assert workspace_for(root).db_path.is_relative_to(second)
        assert _capture_sources(root) == {
            APPLIED[SCAN]: ("1998-07-04T00:00:00", "filename"),
            APPLIED[LOOSE]: ("2024-05-01T00:00:00", "filename"),
        }
        assert _date_only_plan(root, capsys) == {
            APPLIED[SCAN]: ("1998-07-04.png", "medium", False),
            APPLIED[SIDECAR]: ("1998-07-04.xmp", "medium", False),
            APPLIED[LOOSE]: ("2024-05-01.jpg", "medium", False),
        }
    finally:
        os.environ["PAM_HOME"] = str(isolated_pam_home)

    assert _capture_sources(root) == original, "the original workspace lost its evidence"
