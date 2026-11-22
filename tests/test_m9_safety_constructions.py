"""M9's cross-module safety hunt: vision S1-S4 driven through the real commands.

WHY A WHOLE MODULE FOR THIS, restated from `CLAUDE.md`'s promoted lesson rather than
from the plan. **A property no single module owns is invisible to a mutation pass scoped
to one task's files**, and that is the gate's one structural blind spot. M8 closed with
232 exemplary mutation rows and not one of them could reach the fact that `pam apply`
split a companion unit, because the defect lived in the RELATIONSHIP between three
individually-correct modules. M9 has logged ~390 rows across twelve tasks and inherits
the same blind spot, so Task 13 Step 2 takes the vision's safety criteria one at a time
and tries to BUILD the violation end to end.

The question that found M8's breach was *which filter dimensions are not uniform across a
companion unit?* M9 adds new answers to it, and each one gets a construction here:

- **`q` now matches an event's LABEL** (Task 11) - a third non-uniform dimension, and the
  first one whose values a geocoder writes. `test_a_geocoded_event_label_...` builds the
  unit where exactly one member carries the label and drives the real endpoint.
- **the event nav's `group=YYYY-MM.eN`** (M8's own route) is re-driven here with a label
  `pam plan` really geocoded, because every label the rest of the suite sees is seeded
  `label_source='user'` and a seeded label cannot exercise `pam plan`'s own writer.
- **`inferred_facts` is content-addressed by `sha256`; units are keyed by `unit_key`.**
  Two members of one unit never share a digest, so a placeholder that still carries one
  is the state the runner's `sha256 is None` skip does NOT cover - which is where R2 gets
  its third construction.
- **`pam enrich` is a new archive-scoped command** sitting between `pam plan` and
  `pam apply` for the first time in the product's life.

EVERY CONSTRUCTION IN THIS FILE NOW HOLDS. Corrected at the M10 close, and the stale
sentence is worth naming rather than deleting silently. Through M9 this paragraph read
"FOUR CONSTRUCTIONS HOLD AND ONE BREACHES", the breach being `test_known_defect_f12_...`
at the end of the file - a test that ASSERTED the bug, so that "the suite is green" did
not imply vision S3 held. M10 Task 1 closed F12 and rewrote that test as the property
(`test_a_partially_conforming_unit_survives_a_review_ui_rename_whole`, under "THE BREACH,
CLOSED" below); the body was updated and THIS PARAGRAPH WAS NOT, leaving the file's own
header claiming an open S3 breach that no longer exists and naming a test that no longer
exists either. A resuming session reads the header before it reads the record. That is
this project's "a docstring that says X is an untested test" lesson arriving from the
direction that is easy to miss: not a claim nothing checks, but a claim that has since
become FALSE - and a stale header is worse than a stale comment, because it is the first
thing read and it is about the safety property the file exists for.

M10 then ADDED constructions here rather than only fixing one (Task 12 Step 2: eight, and
the ones about the captioner, the path budget and the crash between members of a partially
conforming unit are the ones with no equivalent earlier in the project).

THE ORACLES ARE INDEPENDENT. Event keys are assembled from the suite's own SQL and the
month spelled by hand, never read back from `repo.event_key`; the place names are the
literals `rg_cities1000` owes those coordinates, never whatever the geocoder returned.
"""

import builtins
import ipaddress
import json
import socket
import threading
import webbrowser
from http.client import HTTPConnection
from pathlib import Path

import pytest
from archives import (
    FACES_FLOOR,
    FAKE_COMMAND,
    approve_archive,
    catalog,
    digests,
    open_plan,
    raw_with_preview_bytes,
)
from ollama_stub import OllamaStub, generate_reply, tags_reply

from pam.catalog import repo
from pam.cli import main
from pam.enrich.base import RAW_EXTS
from pam.paths import workspace_for
from pam.scan.runner import run_scan
from pam.webui import run_review

LIBRARY = Path(__file__).resolve().parent / "tiny_library"
# The same real picture `tests/test_t1_enriched_chain.py` borrows, and for the same
# reason: Pillow must really decode it or `phash` counts a failure and `pam enrich`
# exits 1, which is correct behaviour and useless as a happy path.
PICTURE = (LIBRARY / "phash_pattern_base.png").read_bytes()

TOKYO = (35.6895, 139.6917)
# Spelled out, never read back from the geocoder: an expectation taken from
# `reverse_geocoder` would agree with a geocoder that answered `El Tarter, Andorra` for
# every point on earth, which is exactly what `mode=2` does on this platform.
TOKYO_NAME = "Tokyo"

# A template with NO place and NO event token in it. This is load-bearing rather than
# minimal: the default template carries `{event-or-scene}`, so the label would be inside
# every member's proposed filename and `q=tokyo` would match the whole unit through the
# `new_filename` operand. Only a template that keeps the label OUT of the name can show
# that the LABEL operand alone selects a proper subset of a unit.
DATE_ONLY_TEMPLATE = "{date}_{seq}"


def _put(root: Path, name: str, salt: int, *, date: str | None, gps=None) -> None:
    """One real picture plus the sidecar `tests/fake_exiftool.py` reads its tags from.

    The last byte varies per file because PNG's trailing bytes are the IEND chunk's CRC,
    which Pillow does not verify - so every file still decodes and every file has its own
    sha256. Distinct digests matter here because `inferred_facts` is CONTENT-addressed:
    two byte-identical files would share one fact and make the per-file reads weaker than
    they look.

    A RAW-EXTENSION FILE GETS REAL RAW BYTES (M10 Task 6). This used to write PNG bytes
    under a `.NEF` name, which Pillow opened happily - it sniffs content, not extension -
    and design 8.1 rev 17 / amendment A4 then made the image loader branch on the
    EXTENSION, so a mislabelled PNG has no embedded preview for exiftool to find and the
    file counts as `failed`. The fixture was the thing that was wrong: pam classifies by
    extension everywhere.
    """
    if name.rsplit(".", 1)[-1].lower() in RAW_EXTS:
        (root / name).write_bytes(raw_with_preview_bytes(name))
    else:
        (root / name).write_bytes(PICTURE[:-1] + bytes([salt]))
    tags: dict[str, object] = {"PamTestSidecar": name}
    if date is not None:
        tags["DateTimeOriginal"] = date
    if gps is not None:
        tags["GPSLatitude"], tags["GPSLongitude"] = gps
    (root / f"{name}.tags.json").write_text(json.dumps(tags), encoding="utf-8")


def _write_config(root: Path, body: str) -> None:
    """`pam.toml` in the workspace, created before whichever command must read it.

    Grouping is decided at SCAN time and naming at PLAN time, so a config that must
    change the grouping has to exist before `run_scan`, and the workspace directory may
    not exist yet at that point.
    """
    config_path = workspace_for(root).config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(body, encoding="utf-8")


def _archive_names(root: Path) -> set[str]:
    return {p.name for p in root.iterdir() if not p.name.endswith(".tags.json")}


def _stems(names) -> set[str]:
    return {Path(name).stem for name in names}


@pytest.fixture(autouse=True)
def no_real_browser(monkeypatch):
    """`run_review` opens a browser. A suite that opens real tabs is a broken suite."""
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: True)


def _request(server, method, path, body=None):
    """One request to the live review server, through the token prefix - the only way in."""
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


def _drive(plan_id: int, root: Path, work):
    """Run `work(server)` against the server `pam review` really starts, then stop it."""
    result: dict[str, object] = {}

    def serve(server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result["value"] = work(server)
        finally:
            server.shutdown()
            thread.join(timeout=20)

    run_review(plan_id, root, serve=serve)
    return result["value"]


def _statuses(root: Path) -> dict[str, str]:
    """old_rel_path -> status, read with the SUITE'S OWN SQL.

    Not `repo.plan_rows`: the widening under test runs through that query's own
    `ReviewItem`s, and an oracle built from the production symbol agrees with a broken
    one (m5.md's `repo.open_plan` lesson, M8's M922/M924).
    """
    conn = catalog(root)
    try:
        rows = conn.execute("SELECT old_rel_path, status FROM plan_items").fetchall()
    finally:
        conn.close()
    return {row["old_rel_path"]: row["status"] for row in rows}


def _event_ids(root: Path) -> list[int]:
    conn = catalog(root)
    try:
        return [int(row["id"]) for row in conn.execute("SELECT id FROM events ORDER BY id")]
    finally:
        conn.close()


def _event_labels(root: Path) -> list[tuple[str | None, str | None]]:
    conn = catalog(root)
    try:
        rows = conn.execute("SELECT label, label_source FROM events ORDER BY id").fetchall()
    finally:
        conn.close()
    return [(row["label"], row["label_source"]) for row in rows]


def _sidecar_unit(root: Path) -> None:
    """The archive every non-uniformity construction below is built on.

    ONE COMPANION UNIT WHOSE TWO MEMBERS ARE NOT ALIKE. `A.PNG` is a dated, located
    photograph: it gets a `capture` row, so spec 8.3 puts it in an event and `pam plan`
    geocodes that event's centroid into a label. `A.AAE` is an Apple edit sidecar - one of
    the two default `scan.sidecar_exts`, and a handful of the files in a phone library
    were `.AAE` - which carries no `capture` row at all, so it is in NO event, its
    `event_label` is NULL and its `event_key` is None.

    Spec 5.4 nonetheless groups them, because they share a stem. That is the whole point:
    one unit, one `unit_key`, and two members that every event-shaped filter separates.
    """
    _put(root, "A.PNG", 0, date="2019:07:04 18:23:11", gps=TOKYO)
    _put(root, "A.AAE", 1, date=None)


def _scan_and_plan(root: Path, template: str = DATE_ONLY_TEMPLATE):
    run_scan(root, exiftool_command=FAKE_COMMAND)
    _write_config(root, f'[naming]\ntemplate = "{template}"\n')
    assert main(["plan", "--archive", str(root)]) == 0
    conn = catalog(root)
    try:
        return open_plan(conn)
    finally:
        conn.close()


# --- S3, construction 1: the label operand -------------------------------------------


def test_a_geocoded_event_label_selects_one_member_of_a_unit_and_decide_filter_widens_it(
    tmp_path, capsys
):
    """`q=tokyo` matches ONLY the photograph, and the bulk write decides the sidecar too.

    THE CONSTRUCTION, and why it is M9's rather than a re-run of M8's. Task 11 gave `q` a
    third operand - the event's label - and an event's label is `None` for any member with
    no `capture` row. So `q` can now select a proper subset of a companion unit through a
    dimension no filename carries, which is a route that did not exist before this
    milestone. `DATE_ONLY_TEMPLATE` keeps the label out of both proposed names on purpose:
    under the SHIPPED template the label is inside `new_filename` and the second operand
    would mask the third, so this test would pass without the widening ever running.

    THE LISTING AND THE WRITE ARE ASSERTED SEPARATELY, and the gap between them is the
    finding this pins. `GET /items?q=` returns ONE card - that is correct, and A1 does not
    change what a filter matches - while `POST /decide-filter` writes TWO rows. A test
    that only read the statuses could not tell "the filter matched both" from "the filter
    matched one and pam widened it", and only the second is the containment.
    """
    root = tmp_path / "photos"
    root.mkdir()
    _sidecar_unit(root)
    plan = _scan_and_plan(root)
    capsys.readouterr()

    # The label really was geocoded by `pam plan`, not seeded by a fixture. This is also
    # the only place in the suite where `label_source` is asserted to be the geocoder's.
    assert _event_labels(root) == [(TOKYO_NAME, "geocode")]

    def work(server):
        listing = _request(server, "GET", f"/plan/{plan.id}/items?q={TOKYO_NAME}&limit=200")
        decided = _request(
            server,
            "POST",
            f"/plan/{plan.id}/decide-filter",
            {"action": "approve", "q": TOKYO_NAME},
        )
        return listing, decided

    listing, decided = _drive(plan.id, root, work)

    # The proposed names carry no place and no event, so the ONLY operand that can have
    # matched is the label. Asserted rather than assumed - if a future template default
    # leaked into this archive the test would be measuring the wrong operand.
    names = {item["old_filename"]: item["new_filename"] for item in listing["items"]}
    assert names == {"A.PNG": "2019-07-04_001.PNG"}
    assert TOKYO_NAME.lower() not in "2019-07-04_001.PNG".lower()

    # The write is unit-scoped: one match, two rows, and the response says so.
    assert decided["decided"] == 2
    assert len(decided["companions"]) == 1
    assert _statuses(root) == {"A.PNG": "approved", "A.AAE": "approved"}


# --- S3, construction 1a: the PLACE operand (M10 Task 10) -----------------------------


def _relabel_event(root: Path, label: str) -> None:
    """Rename the archive's one event with the SUITE'S OWN SQL, as a person would have.

    `label_source = 'user'` is what makes the rename survive - `repo.events._set_label`
    overwrites a label whose source is NULL or 'geocode' and leaves a person's alone, and
    `tests/test_webui_events.py::_seed_label` carries the same line for the same reason.

    WHY THE CONSTRUCTION BELOW NEEDS THIS AT ALL. `pam plan` geocodes the event's centroid
    and `pam enrich` geocodes the file's own coordinate, so both answer `Tokyo` for one
    photograph in Tokyo - and `q=Tokyo` would then match through the EVENT LABEL operand
    M9 already shipped, masking the operand under test exactly as the shipped template
    masks the label operand. A user-named event is the ordinary state in which the two
    differ, and it is what lets the place operand be measured on its own.
    """
    conn = catalog(root)
    try:
        conn.execute("UPDATE events SET label = ?, label_source = 'user'", (label,))
        conn.commit()
    finally:
        conn.close()


def _place_facts(root: Path) -> list[tuple[str, str]]:
    """`(rel_path, place)` over every LIVE place fact, with the suite's own SQL."""
    conn = catalog(root)
    try:
        rows = conn.execute(
            "SELECT f.rel_path AS rel_path, i.value_json AS value_json FROM files f"
            " JOIN inferred_facts i ON i.sha256 = f.sha256"
            " WHERE i.kind = 'place' AND i.superseded_by IS NULL ORDER BY f.rel_path"
        ).fetchall()
    finally:
        conn.close()
    return [(r["rel_path"], json.loads(r["value_json"])["place"]) for r in rows]


def test_a_place_fact_selects_one_member_of_a_unit_and_decide_filter_widens_it(
    tmp_path, capsys
):
    """`q=Tokyo` matches ONLY the photograph through its PLACE, and the write widens it.

    M10 TASK 10 GIVES `q` A FOURTH OPERAND, so M8's question - *which filter dimensions
    are not uniform across a companion unit?* - is asked again and answered again. A
    `place` fact is written per FILE by `pam enrich`, keyed by the file's own sha256, and
    a companion unit's two members never share a digest; the sidecar has no `capture` row,
    so it has no coordinate and gets no fact at all. One search therefore selects a proper
    subset of a unit through a dimension no filename carries - the same shape as M9's
    event label, through a table M9's operand never touched.

    THE OTHER THREE OPERANDS ARE SHUT OFF, DELIBERATELY AND SEPARATELY, or this test would
    pass with the fourth deleted. `DATE_ONLY_TEMPLATE` keeps the place out of both proposed
    names; `_relabel_event` moves the event's label off the geocoder's answer; and neither
    old filename contains the needle. Each is asserted below rather than assumed.

    THE LISTING AND THE WRITE ARE ASSERTED SEPARATELY, and the gap between them is the
    containment. `GET /items?q=` returns ONE card - correct, and A1 does not change what a
    filter matches - while `POST /decide-filter` writes TWO rows. A test that read only the
    statuses could not tell "the filter matched both" from "the filter matched one and pam
    widened it", and only the second is vision S3 holding.
    """
    root = tmp_path / "photos"
    root.mkdir()
    # THE SAME UNIT M9's CONSTRUCTION USES, and the date on the photograph is load-bearing
    # rather than incidental: without it the item's date falls to `file_mtime`, spec 6.4
    # caps the item to `low`, and spec 8.2's Low-tier contract makes `decide-filter`
    # exclude it - so the write below would decide NOTHING and the widening this test
    # exists to measure would never run.
    _sidecar_unit(root)
    plan = _scan_and_plan(root)
    assert main(["enrich", "--archive", str(root)]) == 0
    _relabel_event(root, "Weekend")
    capsys.readouterr()

    # The fact really came from `pam enrich`'s geocoder, and only the photograph has one.
    assert _place_facts(root) == [("A.PNG", TOKYO_NAME)]

    def work(server):
        listing = _request(server, "GET", f"/plan/{plan.id}/items?q={TOKYO_NAME}&limit=200")
        decided = _request(
            server,
            "POST",
            f"/plan/{plan.id}/decide-filter",
            {"action": "approve", "q": TOKYO_NAME},
        )
        return listing, decided

    listing, decided = _drive(plan.id, root, work)

    matched = {item["old_filename"]: item for item in listing["items"]}
    assert list(matched) == ["A.PNG"]
    # The three older operands could not have matched: not the two names, and not the
    # label, which is now a word the user chose.
    card = matched["A.PNG"]
    for name in (card["old_filename"], card["new_filename"]):
        assert TOKYO_NAME.lower() not in name.lower()
    assert _event_labels(root) == [("Weekend", "user")]

    # The write is unit-scoped: one match, two rows, and the response says so.
    assert decided["decided"] == 2
    assert len(decided["companions"]) == 1
    assert _statuses(root) == {"A.PNG": "approved", "A.AAE": "approved"}


# --- S3, construction 2: the event nav ------------------------------------------------


def test_an_event_nav_group_selects_one_member_of_a_unit_and_decide_filter_widens_it(
    tmp_path, capsys
):
    """`group=2019-07.eN` matches ONLY the photograph, and the bulk write widens it.

    M8's own route, re-driven here over a label `pam plan` geocoded rather than one a
    fixture seeded, and over a plan whose names carry no event token. The event key is
    assembled from the suite's own `SELECT id FROM events` and the month spelled by hand:
    reading it back from `repo.event_key` would build the expectation out of the function
    whose selection is under test.
    """
    root = tmp_path / "photos"
    root.mkdir()
    _sidecar_unit(root)
    plan = _scan_and_plan(root)
    capsys.readouterr()

    event_ids = _event_ids(root)
    assert len(event_ids) == 1, event_ids
    group = f"2019-07.e{event_ids[0]}"

    def work(server):
        listing = _request(server, "GET", f"/plan/{plan.id}/items?group={group}&limit=200")
        decided = _request(
            server,
            "POST",
            f"/plan/{plan.id}/decide-filter",
            {"action": "approve", "group": group},
        )
        return listing, decided

    listing, decided = _drive(plan.id, root, work)

    assert [item["old_filename"] for item in listing["items"]] == ["A.PNG"]
    assert decided["decided"] == 2
    assert _statuses(root) == {"A.PNG": "approved", "A.AAE": "approved"}


# --- R2: the third hydration route ----------------------------------------------------


def test_pam_enrich_never_opens_a_cloud_placeholder_that_still_carries_a_sha256(tmp_path):
    """The runner's `sha256 is None` skip does not cover this state. `wants()` must.

    WHY THIS STATE IS REACHABLE AND WHY IT IS THE INTERESTING ONE. `pam enrich` skips a
    file with no `sha256` before it ever asks an enricher, so the ordinary cloud
    placeholder - never hashed, because hashing one would hydrate it - is turned away by
    the runner and the enrichers' own refusals are never consulted. A file that was LOCAL
    when it was scanned and has since been dehydrated by OneDrive keeps the digest that
    scan recorded, so it reaches `wants()` with a `sha256` in hand. That is the only path
    on which vision R2 depends on the enricher rather than on the runner, and it is the
    one no test drove before this one.

    THE ASSERTION WATCHES THE OPEN, NOT THE OUTCOME (M8's M697, promoted into CLAUDE.md).
    A refusal that read the bytes and then discarded them would leave exactly the fact
    table this test would otherwise accept, and the archive would have been hydrated. So
    `builtins.open`, `Path.open` and `Path.read_bytes` are all watched for the
    placeholder's own path - three names because a loader could reach the file by any of
    them, and the pool's worker threads share this process.

    THE TWO ENRICHERS ANSWER DIFFERENTLY AND BOTH ARE RIGHT. `phash` refuses outright:
    a dHash needs pixels. `geocode` accepts, because a coordinate the scanner already
    recorded needs no byte of the file - so a placeholder DOES get a `place` fact, which
    is the correct behaviour and the reason the `opened` assertion has to be the one that
    carries the safety claim.
    """
    root = tmp_path / "photos"
    root.mkdir()
    _put(root, "P.PNG", 0, date="2019:07:04 18:23:11", gps=TOKYO)
    _put(root, "Q.PNG", 1, date="2019:07:04 18:24:11", gps=TOKYO)
    run_scan(root, exiftool_command=FAKE_COMMAND)

    conn = catalog(root)
    try:
        conn.execute("UPDATE files SET placeholder = 1 WHERE rel_path = 'P.PNG'")
        conn.commit()
        state = conn.execute(
            "SELECT placeholder, sha256 FROM files WHERE rel_path = 'P.PNG'"
        ).fetchone()
    finally:
        conn.close()
    # The precondition, asserted: a placeholder that DOES have a digest. Without this the
    # test would be re-proving the runner's early skip and claiming R2 for it.
    assert state["placeholder"] == 1 and state["sha256"]

    target = str((root / "P.PNG").resolve()).casefold()
    opened: list[tuple[str, str]] = []
    real_open, real_path_open, real_read_bytes = builtins.open, Path.open, Path.read_bytes

    def _is_target(path) -> bool:
        try:
            return str(Path(path).resolve()).casefold() == target
        except OSError:  # pragma: no cover - a name no filesystem can resolve
            return False

    def spy_open(file, *args, **kwargs):
        if _is_target(file):
            opened.append(("open", str(file)))
        return real_open(file, *args, **kwargs)

    def spy_path_open(self, *args, **kwargs):
        if _is_target(self):
            opened.append(("Path.open", str(self)))
        return real_path_open(self, *args, **kwargs)

    def spy_read_bytes(self):
        if _is_target(self):
            opened.append(("Path.read_bytes", str(self)))
        return real_read_bytes(self)

    builtins.open, Path.open, Path.read_bytes = spy_open, spy_path_open, spy_read_bytes
    try:
        assert main(["enrich", "--archive", str(root)]) == 0
    finally:
        builtins.open, Path.open, Path.read_bytes = (
            real_open,
            real_path_open,
            real_read_bytes,
        )

    assert opened == [], f"vision R2: pam enrich hydrated a placeholder - {opened}"

    # The spy is not watching an empty archive: the LOCAL file really was decoded, so a
    # spy that had been installed wrong would have been caught by its own silence here.
    conn = catalog(root)
    try:
        facts = {
            (row["rel_path"], row["kind"])
            for row in conn.execute(
                "SELECT f.rel_path AS rel_path, i.kind AS kind FROM files f"
                " JOIN inferred_facts i ON i.sha256 = f.sha256"
            )
        }
    finally:
        conn.close()
    assert ("Q.PNG", "phash") in facts
    assert ("P.PNG", "phash") not in facts
    # And the placeholder was named without being read, which is the point of geocode
    # having no placeholder test of its own.
    assert ("P.PNG", "place") in facts


# --- S3 / S1: a new command in the middle of the chain --------------------------------


def test_pam_enrich_between_plan_and_apply_leaves_the_plan_and_its_unit_intact(
    tmp_path, monkeypatch, capsys
):
    """`pam enrich` does NOT supersede an open plan, and does not need to. Recorded either way.

    THE QUESTION. `pam scan` supersedes every open plan, because grouping depends on the
    files and on `pam.toml`, and an open plan built over a different grouping would let
    `pam apply` split a companion unit (spec 6.3 rule 5, vision S3). `pam enrich` is M9's
    new archive-scoped command and it writes to the same catalog under the same lock, so
    the question has to be asked again rather than assumed to have the same answer.

    THE ANSWER IS NO, AND THE REASON IS THAT IT WRITES NOTHING THE PLAN DEPENDS ON. A plan
    item is frozen at `(unit_key, generated_filename, new_filename, tokens_json)`;
    enrichment writes `inferred_facts` rows and touches neither membership nor grouping
    nor any name. So the unit stays whole, the approved names stay exactly the ones the
    user approved, and `pam apply` renames the same two files it would have renamed a
    minute earlier.

    WHAT THE USER SEES IS A CONSEQUENCE, NOT A DEFECT: the applied names carry no place,
    because the facts arrived after the names were built. That is spec 6.2 rule 6 doing
    its job - the same inputs produce the same name - and `pam enrich`'s own closing line
    already sends the user to `pam plan`, which is the command that would pick the new
    facts up. Asserted here so the behaviour is a decision on the record rather than an
    accident nobody measured.
    """
    root = tmp_path / "photos"
    root.mkdir()
    _put(root, "A.PNG", 0, date="2019:07:04 18:23:11", gps=TOKYO)
    _put(root, "A.NEF", 1, date="2019:07:04 18:23:11", gps=TOKYO)
    before = digests(root)
    # `{place}` IS in this template, so a plan built after enrichment would look different
    # from this one. That is what makes the "unchanged" assertions below mean something.
    plan = _scan_and_plan(root, template="{date}_{place}_{seq}")
    planned = {"A.PNG": "2019-07-04_001.PNG", "A.NEF": "2019-07-04_001.NEF"}

    conn = catalog(root)
    try:
        assert {
            r.old_filename: r.new_filename
            for r in repo.plan_rows(conn, plan.id, floor=FACES_FLOOR)
        } == planned
    finally:
        conn.close()

    def work(server):
        return _request(server, "POST", f"/plan/{plan.id}/decide-filter", {"action": "approve"})

    assert _drive(plan.id, root, work)["decided"] == 2
    capsys.readouterr()

    assert main(["enrich", "--archive", str(root)]) == 0
    assert "Next: pam plan" in capsys.readouterr().out

    conn = catalog(root)
    try:
        # The plan is still open and still approved: enrichment superseded nothing.
        assert open_plan(conn).id == plan.id
        rows = repo.plan_rows(conn, plan.id, floor=FACES_FLOOR)
    finally:
        conn.close()
    assert {r.old_filename: r.new_filename for r in rows} == planned
    assert {r.status for r in rows} == {"approved"}
    # The facts really did land - so the "unchanged" assertions above are about a catalog
    # that moved, not about a command that did nothing.
    conn = catalog(root)
    try:
        places = conn.execute(
            "SELECT COUNT(*) AS n FROM inferred_facts WHERE kind = 'place'"
        ).fetchone()["n"]
    finally:
        conn.close()
    assert places >= 1

    monkeypatch.setattr("builtins.input", lambda: "apply")
    assert main(["apply", str(plan.id), "--archive", str(root)]) == 0
    assert "2 renamed, 0 failed" in capsys.readouterr().out

    # Vision S3: one stem across the unit. Vision S1: not one byte moved.
    assert _archive_names(root) == set(planned.values())
    assert len(_stems(_archive_names(root))) == 1
    assert set(digests(root)) == set(before)


# --- I4, M10: the same question, asked of an input that is NOT REPRODUCIBLE -------------
#
# EXTENDING THE TEST ABOVE, NOT DUPLICATING IT. That one asks whether `pam enrich` can move
# an open plan and answers no, with `{place}` in the template so "unchanged" means
# something. M10 adds the half that could not exist before it: a caption is a MODEL'S
# GUESS, so the same command over the same photograph may answer differently on a second
# run, and `pam plan` is the only command allowed to notice.
#
# THE TRUST PROPERTY, stated so it is not lost in the mechanics: A PLAN IS A REVIEWED
# ARTEFACT AND REVIEWED PROPOSALS MUST NEVER MUTATE UNDERNEATH THE USER. Invariant I4.

_CAPTION_TEMPLATE = "{date}_{scene}_{seq}"


def _caption_config(root: Path, port: int) -> None:
    """`pam.toml` carrying BOTH sections the two tests below need at once.

    `_scan_and_plan` writes only `[naming]`, and `pam enrich caption` refuses to run
    without `[enrich.caption] enabled` (amendment A2). Written after the plan exists, so
    the plan really was built by a run that had no captions to offer.
    """
    _write_config(
        root,
        f'[naming]\ntemplate = "{_CAPTION_TEMPLATE}"\n\n'
        f"[enrich.caption]\nenabled = true\nollama_port = {port}\n",
    )


def _caption_stub(*labels: str) -> OllamaStub:
    """A stub answering `/api/generate` with one scripted caption per call."""
    return OllamaStub(
        {
            "/api/generate": [
                generate_reply(label, f"A photograph of {label}.") for label in labels
            ],
            "/api/tags": [tags_reply("qwen2.5vl:latest")],
        }
    )


def _caption_labels(root: Path) -> list[tuple[str, str]]:
    """(model_version, label) for every LIVE caption fact - the suite's own SQL."""
    conn = catalog(root)
    try:
        rows = conn.execute(
            "SELECT model_version, value_json FROM inferred_facts"
            " WHERE kind = 'caption' AND superseded_by IS NULL ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [(str(r["model_version"]), json.loads(r["value_json"])["label"]) for r in rows]


def test_pam_enrich_caption_between_plan_and_apply_renames_to_exactly_what_was_reviewed(
    tmp_path, monkeypatch, capsys
):
    """I4 for a NON-DETERMINISTIC fact, and the template is what makes it a test.

    `{scene}` is in the template and the plan is built before any caption exists, so the
    approved names carry no scene at all. `pam enrich caption` then writes one - and the
    plan must not move, because the user approved those names and nothing since has asked
    them anything.

    THE CLOSING ASSERTION IS WHAT MAKES "UNCHANGED" MEAN SOMETHING: after the apply, a
    re-scan and a fresh `pam plan` propose a DIFFERENT name from the same catalog. So the
    plan held still because plans hold still, not because there was nothing to move it.
    """
    root = tmp_path / "photos"
    root.mkdir()
    _put(root, "A.PNG", 0, date="2019:07:04 18:23:11")
    _put(root, "A.AAE", 1, date=None)  # the companion sidecar: one unit, two members
    before = digests(root)
    plan = _scan_and_plan(root, template=_CAPTION_TEMPLATE)
    planned = {"A.PNG": "2019-07-04_001.PNG", "A.AAE": "2019-07-04_001.AAE"}

    conn = catalog(root)
    try:
        assert {
            r.old_filename: r.new_filename
            for r in repo.plan_rows(conn, plan.id, floor=FACES_FLOOR)
        } == planned
    finally:
        conn.close()

    def work(server):
        return _request(server, "POST", f"/plan/{plan.id}/decide-filter", {"action": "approve"})

    assert _drive(plan.id, root, work)["decided"] == 2
    capsys.readouterr()

    with _caption_stub("Red Door") as stub:
        _caption_config(root, stub.port)
        assert main(["enrich", "caption", "--archive", str(root)]) == 0
    capsys.readouterr()

    # The fact really landed, so the assertions below are about a catalog that moved.
    assert _caption_labels(root) == [("1.0", "Red Door")]

    conn = catalog(root)
    try:
        assert open_plan(conn).id == plan.id  # enrichment superseded nothing
        rows = repo.plan_rows(conn, plan.id, floor=FACES_FLOOR)
    finally:
        conn.close()
    assert {r.old_filename: r.new_filename for r in rows} == planned
    assert {r.status for r in rows} == {"approved"}

    monkeypatch.setattr("builtins.input", lambda: "apply")
    assert main(["apply", str(plan.id), "--archive", str(root)]) == 0
    assert "2 renamed, 0 failed" in capsys.readouterr().out
    # Vision S3: one stem across the unit. Vision S1: not one byte moved.
    assert _archive_names(root) == set(planned.values())
    assert len(_stems(_archive_names(root))) == 1
    assert set(digests(root)) == set(before)

    # ...and the control. A NEW plan is where the caption reaches a name (spec 6.3 rule 5).
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert main(["plan", "--archive", str(root)]) == 0
    conn = catalog(root)
    try:
        fresh = open_plan(conn)
        assert fresh.id != plan.id
        assert {
            r.old_filename: r.new_filename
            for r in repo.plan_rows(conn, fresh.id, floor=FACES_FLOOR)
        } == {
            "2019-07-04_001.PNG": "2019-07-04_Red-Door_001.PNG",
            "2019-07-04_001.AAE": "2019-07-04_Red-Door_001.AAE",
        }
    finally:
        conn.close()


def test_refreshing_the_caption_model_after_a_plan_exists_does_not_move_that_plan(
    tmp_path, capsys
):
    """SPEC 6.4's SUPERSEDE ROW FIRES FOR THE FIRST TIME, and it can change a name now.

    > "Any token's source fact is `superseded` -> token recomputed from current facts;
    > plan marked stale if names change."

    That row has never fired in this project's life, because superseding a `phash` or a
    `place` could not change a name a plan already held - a re-geocode of the same
    coordinates returns the same city. A CAPTION CAN: the same model over the same
    photograph is free to answer "Blue Door" the second time, and
    `--refresh-older-than-model` is the command that asks it to.

    The seeded fact is written at model version 0.9 with the SUITE'S OWN SQL, because
    that is the only way to have a fact the installed enricher's 1.0 is newer than -
    `run_enricher` refuses a threshold newer than the installed version, and rightly.

    WHAT IS PROVEN: the open plan does not move, not by a byte; and a NEW `pam plan` is
    what picks the new fact up, superseding the old plan as spec 6.3 rule 5 requires.
    """
    root = tmp_path / "photos"
    root.mkdir()
    _put(root, "A.PNG", 0, date="2019:07:04 18:23:11")
    run_scan(root, exiftool_command=FAKE_COMMAND)

    conn = catalog(root)
    try:
        sha = str(
            conn.execute("SELECT sha256 FROM files WHERE rel_path = 'A.PNG'").fetchone()[
                "sha256"
            ]
        )
        conn.execute(
            "INSERT INTO inferred_facts (sha256, kind, value_json, confidence, model_name,"
            " model_version, generated_at) VALUES (?, 'caption', ?, NULL, 'qwen2.5vl',"
            " '0.9', '2026-09-02T09:00:00')",
            (sha, json.dumps({"label": "Old Guess", "sentence": "The first answer."})),
        )
        conn.commit()
    finally:
        conn.close()

    _write_config(root, f'[naming]\ntemplate = "{_CAPTION_TEMPLATE}"\n')
    assert main(["plan", "--archive", str(root)]) == 0
    conn = catalog(root)
    try:
        plan = open_plan(conn)
        planned = {
            r.old_filename: r.new_filename
            for r in repo.plan_rows(conn, plan.id, floor=FACES_FLOOR)
        }
    finally:
        conn.close()
    assert planned == {"A.PNG": "2019-07-04_Old-Guess_001.PNG"}
    capsys.readouterr()

    with _caption_stub("Blue Door") as stub:
        _caption_config(root, stub.port)
        assert (
            main(
                [
                    "enrich",
                    "caption",
                    "--refresh-older-than-model",
                    "1.0",
                    "--archive",
                    str(root),
                ]
            )
            == 0
        )
    capsys.readouterr()

    # The supersede really happened and the model really answered differently: exactly one
    # LIVE caption, at the installed version, carrying the new label.
    assert _caption_labels(root) == [("1.0", "Blue Door")]

    conn = catalog(root)
    try:
        # THE PROPERTY. The plan the user is looking at is unchanged and still open - the
        # fact under one of its tokens was retired and its name did not move a byte.
        assert open_plan(conn).id == plan.id
        assert {
            r.old_filename: r.new_filename
            for r in repo.plan_rows(conn, plan.id, floor=FACES_FLOOR)
        } == planned
    finally:
        conn.close()

    # A NEW plan is the command that picks it up, and it supersedes the old one.
    assert main(["plan", "--archive", str(root)]) == 0
    conn = catalog(root)
    try:
        fresh = open_plan(conn)
        assert fresh.id != plan.id
        assert {
            r.old_filename: r.new_filename
            for r in repo.plan_rows(conn, fresh.id, floor=FACES_FLOOR)
        } == {"A.PNG": "2019-07-04_Blue-Door_001.PNG"}
        superseded = conn.execute(
            "SELECT status FROM plans WHERE id = ?", (plan.id,)
        ).fetchone()
    finally:
        conn.close()
    assert superseded["status"] == "superseded"


def _caption_fact_ids(root: Path) -> list[tuple[int, str, str | None]]:
    """`(id, label, superseded_by-as-text)` for every caption fact - the suite's own SQL."""
    conn = catalog(root)
    try:
        rows = conn.execute(
            "SELECT id, value_json, superseded_by FROM inferred_facts"
            " WHERE kind = 'caption' ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [
        (int(r["id"]), json.loads(r["value_json"])["label"], r["superseded_by"]) for r in rows
    ]


def test_a_superseded_caption_still_explains_the_plan_it_produced(tmp_path, capsys):
    """B4 / spec 8.2 rev 22: `fact_models` resolves a fact the archive has RETIRED.

    THE HALF THE TEST ABOVE DOES NOT COVER. That one proves the reviewed plan's NAMES do
    not move when `--refresh-older-than-model` supersedes the fact under one of their
    tokens. This proves the plan still EXPLAINS those names afterwards - which is a
    different claim about the same event, and the one vision Q3 rev 4 makes ("every
    AI-derived token in a proposed name is traceable to a cached model output - the
    model's name and version are shown at review").

    WHY IT COULD HAVE GONE THE OTHER WAY. `fact_models` is DERIVED at read time rather
    than frozen into `tokens_json`, so the obvious implementation filters to live rows the
    way every other `inferred_facts` reader in this package does - `places_by_file_id`,
    `captions_by_file_id`, `_fact_coverage` and `live_place_names` all carry
    `superseded_by IS NULL` and are all right to. `facts_by_id` must NOT, because
    provenance points at the row that PRODUCED THE NAME (spec 6.1 rev 14's write-time
    postcondition), not at whatever is current. A live filter here would make a reviewed
    plan's Why? panel go quiet at exactly the moment the archive learned something new.

    WHAT MAKES DERIVING IT SAFE AT ALL is `inferred_facts` being append-and-supersede: the
    only write to an existing row anywhere in `pam/` sets `superseded_by`, so the model
    identity this panel reads cannot have changed under the plan. That is B4's argument and
    it is asserted here rather than only stated - the superseded row's model name and
    version are compared against what the plan was built with.
    """
    root = tmp_path / "photos"
    root.mkdir()
    _put(root, "A.PNG", 0, date="2019:07:04 18:23:11")
    run_scan(root, exiftool_command=FAKE_COMMAND)

    conn = catalog(root)
    try:
        sha = str(
            conn.execute("SELECT sha256 FROM files WHERE rel_path = 'A.PNG'").fetchone()[
                "sha256"
            ]
        )
        conn.execute(
            "INSERT INTO inferred_facts (sha256, kind, value_json, confidence, model_name,"
            " model_version, generated_at) VALUES (?, 'caption', ?, NULL, 'qwen2.5vl',"
            " '0.9', '2026-09-02T09:00:00')",
            (sha, json.dumps({"label": "Old Guess", "sentence": "The first answer."})),
        )
        conn.commit()
    finally:
        conn.close()

    _write_config(root, f'[naming]\ntemplate = "{_CAPTION_TEMPLATE}"\n')
    assert main(["plan", "--archive", str(root)]) == 0
    conn = catalog(root)
    try:
        plan = open_plan(conn)
        planned = {
            r.old_filename: r.new_filename
            for r in repo.plan_rows(conn, plan.id, floor=FACES_FLOOR)
        }
    finally:
        conn.close()
    assert planned == {"A.PNG": "2019-07-04_Old-Guess_001.PNG"}
    capsys.readouterr()

    # The panel before the refresh, so the comparison below is against a measured state
    # rather than against an expectation invented after the fact.
    before = _drive(plan.id, root, lambda s: _request(s, "GET", f"/plan/{plan.id}/items"))
    old_fact_id, old_label, superseded_by = _caption_fact_ids(root)[0]
    assert (old_label, superseded_by) == ("Old Guess", None)
    # Spec 8.2 rev 22 (A17): the map is a field on the CARD, not beside `items`.
    assert [c["fact_models"] for c in before["items"]] == [{str(old_fact_id): "qwen2.5vl 0.9"}]

    with _caption_stub("Blue Door") as stub:
        _caption_config(root, stub.port)
        assert (
            main(
                [
                    "enrich",
                    "caption",
                    "--refresh-older-than-model",
                    "1.0",
                    "--archive",
                    str(root),
                ]
            )
            == 0
        )
    capsys.readouterr()

    # The supersede really happened: the row the plan points at is retired, and a NEWER
    # row exists. Without this the assertions below would be re-reading a live fact.
    facts = _caption_fact_ids(root)
    assert len(facts) == 2
    assert facts[0][0] == old_fact_id and facts[0][2] is not None
    assert facts[1][1] == "Blue Door" and facts[1][2] is None

    after = _drive(plan.id, root, lambda s: _request(s, "GET", f"/plan/{plan.id}/items"))

    # THE PROPERTY. The plan still names the model that wrote the name it is showing, and
    # names the SUPERSEDED one - not the replacement, which produced no name here.
    card = after["items"][0]
    assert card["fact_models"] == {str(old_fact_id): "qwen2.5vl 0.9"}
    assert card["new_filename"] == "2019-07-04_Old-Guess_001.PNG"
    scene = [t for t in card["tokens"] if t["resolved_from"] == "scene"]
    assert len(scene) == 1
    assert scene[0]["source_table"] == "inferred_facts"
    assert scene[0]["source_id"] == old_fact_id
    # B4's guarantee, measured: nothing about the retired row's identity moved.
    assert [c["fact_models"] for c in after["items"]] == [
        c["fact_models"] for c in before["items"]
    ]
    # And Q3's second half still reads, because the model still emits no confidence.
    assert "qwen2.5vl 0.9" in card["confidence_note"]


# --- THE BREACH, CLOSED -----------------------------------------------------------------------


def test_a_partially_conforming_unit_survives_a_review_ui_rename_whole(
    tmp_path, monkeypatch, capsys
):
    """VISION S3 OVER A PARTIALLY CONFORMING UNIT. Closed by M10 Task 1; was F12.

    THIS TEST ASSERTED THE BUG UNTIL M10 TASK 1 AND NOW ASSERTS THE PROPERTY, which is the
    whole reason it was written that way round: it went RED the moment the defect was
    fixed, and whoever fixed it had to come here and write the guarantee instead.

    THE GUARANTEE (design spec 7.3 rev 16, its closing sentence): a companion group is
    never left split across two stems - by apply, by recovery, or by a name the user
    typed. The mechanism is the membership rule the same revision introduces: **a plan's
    unit is the set of files `pam scan` grouped, as frozen at plan time, not the subset
    that plan happens to hold items for**, so a member the planner would have dropped as
    already conforming gets an item anyway whenever any member of its unit is not. That
    item proposes the name its file already has, which spec 7.0 J12 resolves at preflight
    with no journal row and no `os.rename`.

    WHAT WENT WRONG BEFORE. `pam scan` grouped the two files (`scan.ignore_stem_suffixes`
    lets one unit hold members whose on-disk stems differ), `naming/planner.py` decided
    `already_conforming` PER MEMBER and `continue`d the conforming one, and
    `webui/write_api.py::_unit` then built "the unit" out of `repo.plan_rows` - so the
    member with no plan item was invisible to it, to `validator._item_refusals` after it,
    and to every one of A1's three containment layers, all of which reason over the plan's
    ITEMS. `pam verify` reported a clean 12/12 (check 4e reads `companion_group_id`, which
    survives until the next scan), the next `pam scan` then set that column to NULL for
    both members because spec 5.4 groups by stem, and the next `pam plan` proposed `_002`
    for the orphan: separated, differently numbered, and unrecoverable by any pam command.

    APPLY ALONE WAS ALWAYS SAFE, and that is why this survived to be tagged in `v0.1-m9`:
    the plan's own proposal always moved the unit together. Only a name the USER typed (or
    a `restore` after one) could move the plan's members to a stem the absent member would
    never be given.

    THE ROUTE THAT NEEDS NO HAND-RENAME - apply a plan over a lone RAW, let the editor
    export `<pam stem>.acr_.png` beside it, re-scan, re-plan - is the steady state of an
    archive that has been through pam once, and it is driven in
    `tests/test_f12_unit_membership.py`, together with the three planner cases and every
    consumer of `plan_items.status` a no-op item reaches. This test keeps the end-to-end
    shape M9 measured the breach in.

    Recorded as M1406 in `docs/mutations/m9.md`; the fix is M10 Task 1, decided in
    `docs/implementation-notes/m9.md` section 4.4 (candidate (i)). A refusal was
    considered and rejected because it would be PERMANENT: the state is stable across
    every re-plan, so the user's only route out would be to rename outside pam.
    """
    root = tmp_path / "photos"
    root.mkdir()
    # A unit that is partially conforming: the `.NEF` already carries the stem the
    # planner will choose, the `.acr_.png` does not, and the ignored suffix is what lets
    # them be one unit while their stems differ.
    _put(root, "2019-07-04_001.NEF", 0, date="2019:07:04 18:23:11")
    _put(root, "2019-07-04_001.acr_.png", 1, date="2019:07:04 18:23:11")
    _write_config(
        root,
        f'[naming]\ntemplate = "{DATE_ONLY_TEMPLATE}"\n\n'
        '[scan]\nignore_stem_suffixes = [".acr_"]\n',
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)

    # Precondition 1: `pam scan` really did group them. Without this the rest of the test
    # would be describing two unrelated files, which no spec forbids splitting.
    conn = catalog(root)
    try:
        groups = {
            row["rel_path"]: row["companion_group_id"]
            for row in conn.execute(
                "SELECT rel_path, companion_group_id FROM files"
                " WHERE rel_path NOT LIKE '%.tags.json'"
            )
        }
    finally:
        conn.close()
    assert groups["2019-07-04_001.NEF"] is not None
    assert groups["2019-07-04_001.NEF"] == groups["2019-07-04_001.acr_.png"]

    assert main(["plan", "--archive", str(root)]) == 0
    capsys.readouterr()
    conn = catalog(root)
    try:
        plan = open_plan(conn)
        rows = repo.plan_rows(conn, plan.id, floor=FACES_FLOOR)
    finally:
        conn.close()

    # Precondition 2: THE PLAN HOLDS THE WHOLE UNIT, spelled out from spec 7.3 rev 16
    # rather than counted. Until M10 Task 1 this list was `["2019-07-04_001.acr_.png"]` -
    # a PROPER SUBSET of the unit, which is the state A1's three layers were never built
    # to see and was the whole defect in one line. The `.NEF`'s item is a no-op item: it
    # proposes the name that file already has.
    assert [row.old_filename for row in rows] == [
        "2019-07-04_001.NEF",
        "2019-07-04_001.acr_.png",
    ]
    assert [row.new_filename for row in rows] == [
        "2019-07-04_001.NEF",
        "2019-07-04_001.png",
    ]

    png = next(row for row in rows if row.old_filename.endswith(".acr_.png"))

    def work(server):
        _request(
            server,
            "POST",
            f"/item/{png.id}/rename",
            {"new_filename": "Holiday.png"},
        )
        return _request(server, "POST", f"/plan/{plan.id}/decide-filter", {"action": "approve"})

    # The rename widened to the whole unit, so the approval decides TWO items, not one.
    assert _drive(plan.id, root, work)["decided"] == 2

    monkeypatch.setattr("builtins.input", lambda: "apply")
    assert main(["apply", str(plan.id), "--archive", str(root)]) == 0
    assert "2 renamed, 0 failed" in capsys.readouterr().out

    # THE PROPERTY, on disk. One stem, which is what spec 7.3's closing sentence promises.
    assert _archive_names(root) == {"Holiday.NEF", "Holiday.png"}
    assert len(_stems(_archive_names(root))) == 1

    assert main(["verify", "--archive", str(root)]) == 0
    capsys.readouterr()

    # And the group SURVIVES the next scan, which is where the damage used to become
    # permanent: spec 5.4 groups by stem, and the two stems now agree.
    run_scan(root, exiftool_command=FAKE_COMMAND)
    conn = catalog(root)
    try:
        after = {
            row["rel_path"]: row["companion_group_id"]
            for row in conn.execute(
                "SELECT rel_path, companion_group_id FROM files"
                " WHERE rel_path NOT LIKE '%.tags.json'"
            )
        }
    finally:
        conn.close()
    assert after["Holiday.NEF"] is not None
    assert after["Holiday.NEF"] == after["Holiday.png"]

    # And no orphan is offered a `_002`. The next plan holds BOTH members and proposes
    # ONE stem for them - which is the sentence that used to read
    # `{"Holiday.png": "2019-07-04_002.png"}`, a lone file renumbered away from the
    # partner it had been separated from.
    #
    # HT-M14 F1 (AM6): under the SAME template the typed `Holiday` stem is kept - the unit is
    # whole and nothing is planned, which is the property too (no lone member, no `_002`).
    assert main(["plan", "--archive", str(root)]) == 0
    assert "Nothing to rename." in capsys.readouterr().out
    # So the re-proposal this construction exists for is reached the way a user reaches it
    # now: a template change. This archive has no faces, so `{people}` drops and the new
    # template spells the same names as the old.
    config = workspace_for(root).config_path
    before = config.read_text(encoding="utf-8")
    assert before.count('template = "{date}_{seq}"') == 1, before
    config.write_text(
        before.replace('template = "{date}_{seq}"', 'template = "{date}_{people}_{seq}"'),
        encoding="utf-8",
    )
    assert main(["plan", "--archive", str(root)]) == 0
    conn = catalog(root)
    try:
        reunion = open_plan(conn)
        proposals = {
            r.old_filename: r.new_filename
            for r in repo.plan_rows(conn, reunion.id, floor=FACES_FLOOR)
        }
    finally:
        conn.close()
    assert proposals == {
        "Holiday.NEF": "2019-07-04_001.NEF",
        "Holiday.png": "2019-07-04_001.png",
    }


# =====================================================================================
# M10 TASK 12 STEP 2 - THE CONSTRUCTION HUNT, THIRD MILESTONE RUNNING
# =====================================================================================
#
# WHY THESE EXIST AND WHY THE NUMBER OF MUTATION ROWS IS NOT AN ARGUMENT AGAINST THEM.
# M8 closed with 232 exemplary mutation rows and could not reach the fact that `pam apply`
# split a companion unit. M9 closed with 392 and could not reach F12. M10 has now run ~250
# more, and the two breaches had the same shape: THE DEFECT LIVED IN THE RELATIONSHIP
# BETWEEN MODULES EACH OF WHICH WAS INDIVIDUALLY RIGHT, which a mutation pass scoped to one
# task's files is structurally blind to. So the vision's safety criteria get built end to
# end, across module boundaries, through the commands a user types.
#
# THE THREE QUESTIONS THIS ROUND ASKED, and where each one landed:
#
#   Q1  Which filter dimensions are NOT uniform across a companion unit? (M8's question.)
#       M8 answered free-text search and event membership; M9 added the event LABEL; M10
#       Task 10 added the `place` fact. THE FIFTH IS BELOW and it is M10's own: with
#       `scan.ignore_stem_suffixes` set, a unit's members' OLD FILENAMES DIFFER BY THE
#       IGNORED SUFFIX, so `q=acr_` selects exactly one of them - and the member it leaves
#       behind is the NO-OP ITEM, a row that did not exist in this product before Task 1.
#
#   Q2  Which members of a unit does each command NOT see? (M9's question, the one that
#       actually found F12, because `already_conforming` is not a filter at all.) M10's
#       answer is `wants()`: it is an allowlist over image extensions, applied PER FILE,
#       so a unit's video or sidecar carries no caption. The construction below is the
#       geometry T1's chain cannot build - two captioned members whose ON-DISK STEMS
#       DIFFER - because that is where a planner reading the scene per member would put
#       two stems on one unit and F12's fix is what puts both members in the plan at all.
#
#   Q3  Which per-file facts does the planner read from the PRIMARY only, and could any of
#       them make `tier` or `attention` non-uniform across a unit? (M10's own question:
#       this is the first milestone whose facts come from a non-deterministic source, and
#       `tier`/`attention` uniformity is what makes A1's preset buttons safe.) The answer
#       is that they are uniform BY CONSTRUCTION - `_plan_unit` computes one
#       `(tokens_json, tier, trust_score, trust_reasons_json)` tuple per unit and copies
#       it to every member's draft, and nothing after plan time writes those four columns.
#       Two constructions hold it down: the path budget, which is the one arithmetic in
#       the product that could plausibly differ between members of one unit (their
#       EXTENSIONS have different lengths), and an archive-wide sweep.
#
# And two the questions did not ask for, both about M10's socket:
#
#   S4  the canary's own enrich stage runs `phash`, so the ONE enricher that opens a
#       socket has never been inside the watch that was relaxed because of it;
#   S1  `pam enrich caption` reads a RAW file through exiftool's binary extraction, which
#       is the first time an enricher shells out to a tool over the archive's own bytes.

IGNORED_SUFFIX = ".acr_"

# Spec 6.2 rule 5's constant, transcribed rather than imported. The whole value of the
# budget construction below is that it decides for itself how deep a directory has to be
# to force a truncation; asking `naming/sanitize.py` for the number would make the test
# agree with whatever that module happens to say.
SPEC_MAX_FULL_PATH = 240


def _editor_config(root: Path, template: str, port: int | None = None) -> None:
    """`[naming]` + `[scan] ignore_stem_suffixes` + optionally `[enrich.caption]`.

    Written before the scan, because spec 5.4's grouping is decided at scan time and the
    ignored suffix is what lets one unit hold members whose on-disk stems differ.
    """
    body = f'[naming]\ntemplate = "{template}"\n\n[scan]\nignore_stem_suffixes = ["{IGNORED_SUFFIX}"]\n'
    if port is not None:
        body += f"\n[enrich.caption]\nenabled = true\nollama_port = {port}\n"
    _write_config(root, body)


def _grouped_by_scan(root: Path) -> dict[int, set[str]]:
    """companion_group_id -> the rel_paths `pam scan` put in it. The suite's own SQL.

    THE INDEPENDENT GROUPING. `unit_key` is what the PLANNER froze; this is what the
    SCANNER grouped, and spec 7.3 rev 16's membership rule is precisely that the two must
    describe the same set. An oracle read out of `plan_items.unit_key` would agree with
    the planner by construction and could not see a planner that dropped a member.
    """
    conn = catalog(root)
    try:
        rows = conn.execute(
            "SELECT companion_group_id AS g, rel_path FROM files"
            " WHERE companion_group_id IS NOT NULL AND rel_path NOT LIKE '%.tags.json'"
        ).fetchall()
    finally:
        conn.close()
    groups: dict[int, set[str]] = {}
    for row in rows:
        groups.setdefault(int(row["g"]), set()).add(str(row["rel_path"]))
    return groups


# --- Q1, the FIFTH non-uniform filter dimension: the ignored editor suffix -------------


def test_q_matching_the_ignored_editor_suffix_selects_one_member_and_widens_to_the_unit(
    tmp_path, monkeypatch, capsys
):
    """VISION S3 THROUGH `q`, over the one geometry in which OLD FILENAMES DIFFER.

    THE QUESTION THIS ANSWERS. M8 asked which of `decide-filter`'s filter dimensions are
    not uniform across a companion unit, because those are the ones a single click can use
    to decide a proper subset of a unit. Its own answers were free-text search matching
    EXTENSIONS (`q=mov` selects the video of every Live Photo) and event membership; M9
    added the event label; M10 Task 10 added the `place` fact. THIS IS THE FIFTH, and it
    is the first that is about the members' own STEMS rather than about anything appended
    to them: `scan.ignore_stem_suffixes` exists precisely so that `DSC.NEF` and
    `DSC.acr_.png` are ONE unit, which means `q=acr_` matches one member and not the other.

    IT IS ALSO THE FIRST ONE WHOSE LEFT-BEHIND MEMBER IS A NO-OP ITEM. Before M10 Task 1
    the conforming `.NEF` had no plan item at all - that is F12 - so this filter would have
    selected the only row there was and the widening would have had nothing to add. The
    row it now widens onto is a row spec 7.0 J12 resolves with no journal entry and no
    `os.rename`, so this construction is only expressible in a product that has both
    halves of Task 1 in it.

    THE ORACLE IS INDEPENDENT. `IGNORED_SUFFIX` is the literal this test writes into
    `pam.toml`; the names are spelled by hand from spec 6.2's template semantics; and the
    grouping is read back through `_grouped_by_scan`, which is the SCANNER's grouping and
    not the `unit_key` the planner froze.
    """
    root = tmp_path / "photos"
    root.mkdir()
    _put(root, "2019-07-04_001.NEF", 0, date="2019:07:04 18:23:11")
    _put(root, f"2019-07-04_001{IGNORED_SUFFIX}.png", 1, date="2019:07:04 18:23:11")
    _editor_config(root, DATE_ONLY_TEMPLATE)
    run_scan(root, exiftool_command=FAKE_COMMAND)

    # Precondition: one unit, two members, two DIFFERENT on-disk stems. Without this the
    # rest of the test is about two unrelated files, which no spec forbids splitting.
    groups = _grouped_by_scan(root)
    assert len(groups) == 1, groups
    assert next(iter(groups.values())) == {
        "2019-07-04_001.NEF",
        f"2019-07-04_001{IGNORED_SUFFIX}.png",
    }

    assert main(["plan", "--archive", str(root)]) == 0
    capsys.readouterr()
    conn = catalog(root)
    try:
        plan = open_plan(conn)
        rows = repo.plan_rows(conn, plan.id, floor=FACES_FLOOR)
    finally:
        conn.close()
    proposals = {row.old_filename: row.new_filename for row in rows}
    assert proposals == {
        "2019-07-04_001.NEF": "2019-07-04_001.NEF",  # the no-op item (spec 7.0 J12)
        f"2019-07-04_001{IGNORED_SUFFIX}.png": "2019-07-04_001.png",
    }
    nef = next(row for row in rows if row.old_filename.endswith(".NEF"))

    def work(server):
        # THE FILTER ALONE, asked of the read endpoint the page uses. One item, which is
        # the whole point: this dimension really does select a proper subset of the unit.
        page = _request(server, "GET", f"/plan/{plan.id}/items?q=acr_")
        selected = [item["old_filename"] for item in page["items"]]
        # ...and the SAME filter through the write endpoint.
        decided = _request(
            server,
            "POST",
            f"/plan/{plan.id}/decide-filter",
            {"action": "approve", "q": "acr_"},
        )
        return selected, decided

    selected, decided = _drive(plan.id, root, work)
    assert selected == [f"2019-07-04_001{IGNORED_SUFFIX}.png"]
    # A1's widening put the no-op item in the batch the user never named.
    assert decided["decided"] == 2
    assert decided["companions"] == [nef.id]

    assert _statuses(root) == {
        "2019-07-04_001.NEF": "approved",
        f"2019-07-04_001{IGNORED_SUFFIX}.png": "approved",
    }

    monkeypatch.setattr("builtins.input", lambda: "apply")
    assert main(["apply", str(plan.id), "--archive", str(root)]) == 0
    assert "1 renamed, 0 failed" in capsys.readouterr().out

    # THE PROPERTY, on disk: one stem, and the no-op member was not moved.
    assert _archive_names(root) == {"2019-07-04_001.NEF", "2019-07-04_001.png"}
    assert len(_stems(_archive_names(root))) == 1
    assert main(["verify", "--archive", str(root)]) == 0
    capsys.readouterr()


# --- Q2, which members a command does not see: two captions over two different stems ---


def _labels_by_filename(root: Path) -> dict[str, str]:
    """filename -> live caption label, with the SUITE'S OWN SQL over the join.

    `inferred_facts` is CONTENT-addressed, so the join back to a filename runs through
    `files.sha256`. Not `repo.captions_by_file_id`: an oracle built from the production
    query agrees with a broken one (m5.md's `repo.open_plan` lesson).
    """
    conn = catalog(root)
    try:
        rows = conn.execute(
            "SELECT f.rel_path AS rel_path, i.value_json AS value_json"
            " FROM files f JOIN inferred_facts i ON i.sha256 = f.sha256"
            " WHERE i.kind = 'caption' AND i.superseded_by IS NULL"
        ).fetchall()
    finally:
        conn.close()
    return {Path(row["rel_path"]).name: json.loads(row["value_json"])["label"] for row in rows}


def _plan_item_trust(root: Path) -> set[tuple[str, str, int, str]]:
    """The four columns the planner writes ONCE PER UNIT, read as raw text per item.

    `(unit_key, tier, trust_score, tokens_json)`. The set collapses to one entry per unit
    exactly when the uniformity spec 8.2 rev 15 relies on holds. Read with the suite's own
    SQL and as STORED TEXT, never through `ReviewItem`, whose `tokens` property parses the
    JSON and would make two differently-spelled documents compare equal.
    """
    conn = catalog(root)
    try:
        rows = conn.execute(
            "SELECT unit_key, tier, trust_score, tokens_json FROM plan_items"
        ).fetchall()
    finally:
        conn.close()
    return {
        (str(r["unit_key"]), str(r["tier"]), int(r["trust_score"]), str(r["tokens_json"]))
        for r in rows
    }


def test_two_captioned_members_whose_stems_differ_still_take_the_primarys_scene(
    tmp_path, monkeypatch, capsys
):
    """VISION S3 WHERE M9's F12 GEOMETRY MEETS M10's NON-DETERMINISTIC FACT.

    THE QUESTION. M9's question is *which members of a unit does each command not see?*,
    and M10's answer to it is `wants()`: an allowlist over image extensions applied PER
    FILE, so a unit's video or sidecar gets no caption of its own. Spec 6.1 closes that by
    naming the whole unit from its PRIMARY's facts. `tests/test_t1_enriched_chain.py`
    already drives the two-different-captions case - but over a unit whose members SHARE a
    stem, where a planner reading the scene per member would produce two names that differ
    only in the scene token.

    WHAT THIS BUILDS THAT THAT ONE CANNOT. With `scan.ignore_stem_suffixes` set, the two
    members' on-disk stems ALREADY differ, which is the geometry F12 lived in and the one
    in which "the plan holds a proper subset of the unit" was the steady state of an
    archive that had been through pam once. Put a model's guess on both members of THAT
    unit and the two failure modes compose: the scene could be read per member, and the
    conforming member could be dropped from the plan. Neither happens; both members are in
    the plan and both carry the PRIMARY's caption.

    THE PRIMARY IS THE `.png`, spelled from spec 5.4's rule (display beats RAW) and not
    from whatever the election returned - so `Old-Camera` is a live fact against a file
    that is being renamed and names NOTHING, which is the assertion a fixture whose
    members shared a caption could not make.

    THE WALK ORDER IS ASSERTED, NEVER ASSUMED. `[enrich.caption] workers` defaults to 1,
    so the stub is asked once per file in file-id order; the mapping is read back out of
    the catalog and compared against the pairing this test intends, so a platform that
    scanned differently fails loudly here instead of quietly pairing the wrong caption
    with the wrong photograph.
    """
    root = tmp_path / "photos"
    root.mkdir()
    _put(root, "DSC_9005.NEF", 0, date="2019:07:04 18:23:11")
    _put(root, f"DSC_9005{IGNORED_SUFFIX}.png", 1, date="2019:07:04 18:23:11")
    before = digests(root)

    with _caption_stub("Old Camera", "Birthday Cake") as stub:
        _editor_config(root, _CAPTION_TEMPLATE, stub.port)
        run_scan(root, exiftool_command=FAKE_COMMAND)
        assert main(["enrich", "caption", "--archive", str(root)]) == 0
    capsys.readouterr()

    # The two members really are one unit, and they really carry DIFFERENT captions.
    groups = _grouped_by_scan(root)
    assert len(groups) == 1 and len(next(iter(groups.values()))) == 2, groups
    assert _labels_by_filename(root) == {
        "DSC_9005.NEF": "Old Camera",
        f"DSC_9005{IGNORED_SUFFIX}.png": "Birthday Cake",
    }

    assert main(["plan", "--archive", str(root)]) == 0
    capsys.readouterr()
    conn = catalog(root)
    try:
        plan = open_plan(conn)
        rows = repo.plan_rows(conn, plan.id, floor=FACES_FLOOR)
    finally:
        conn.close()

    # ONE stem for the unit, and it is the PRIMARY's caption. `Old-Camera` names nothing.
    assert {row.old_filename: row.new_filename for row in rows} == {
        "DSC_9005.NEF": "2019-07-04_Birthday-Cake_001.NEF",
        f"DSC_9005{IGNORED_SUFFIX}.png": "2019-07-04_Birthday-Cake_001.png",
    }
    # Q3, on the same plan: one member's caption reached the name and the other's did not,
    # and the two items still agree about everything the preset buttons filter on.
    assert len(_plan_item_trust(root)) == 1

    approve_archive(root, plan.id)  # F1: apply renames only what was approved
    monkeypatch.setattr("builtins.input", lambda: "apply")
    assert main(["apply", str(plan.id), "--archive", str(root)]) == 0
    assert "2 renamed, 0 failed" in capsys.readouterr().out
    assert _archive_names(root) == {
        "2019-07-04_Birthday-Cake_001.NEF",
        "2019-07-04_Birthday-Cake_001.png",
    }
    assert len(_stems(_archive_names(root))) == 1
    # Vision S1: the model's word reached a NAME and not one byte of any photograph.
    assert set(digests(root)) == set(before)
    assert main(["verify", "--archive", str(root)]) == 0
    capsys.readouterr()


# --- S4 and S1: the milestone's socket, and its first shell-out over archive bytes -----


def _loopback(address: object) -> bool:
    """Is one recorded `connect` argument an address on this machine?

    RE-DECIDED FROM `ipaddress`, never borrowed from `pam/net.py` and never imported from
    `tests/test_net_canary.py`. The whole value of a canary is being a second opinion, and
    a third one built from either of those would agree with what it is watching. Anything
    this cannot recognise - an AF_UNIX path, a tuple of the wrong shape - is NOT loopback,
    so it trips the alarm rather than being waved through.
    """
    if not isinstance(address, tuple) or not address:
        return False
    host = address[0]
    if not isinstance(host, str):
        return False
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


def test_the_captioner_is_inside_a_connect_watch_and_every_address_it_dials_is_loopback(
    tmp_path, monkeypatch, capsys
):
    """VISION S4 OVER THE ENRICHER THAT ACTUALLY OPENS THE SOCKET.

    THE GAP THIS CLOSES, found by asking the hunt's questions of the canary itself.
    `tests/test_net_canary.py` is spec 10's instrument, and M10 Task 7 relaxed it from
    *zero connection attempts of any kind* to *every recorded address must be loopback* -
    a relaxation whose entire justification is the captioner. But that file's `enrich`
    stage runs `phash`, pinned by name and for a good reason of its own (it is what puts
    Pillow, pillow-heif and imagehash inside the watch), so THE ONE ENRICHER THAT DIALS
    ANYTHING HAS NEVER BEEN INSIDE THE WATCH THAT WAS WIDENED FOR IT. The widening is
    proven there only by a synthetic `record(("127.0.0.1", 11434))`.

    This is a test gap and not a defect: `pam/net.py` refuses a non-loopback address at
    construction, `tests/test_net.py` proves that, and `tests/test_caption.py` pins the
    connection the client builds. What was missing is the measurement at the level spec 10
    asks for - a wrap around `socket.socket.connect` while a real `pam enrich caption`
    runs - and in particular that the list comes back NON-EMPTY, because a watch over a
    run that dialled nothing would report "all loopback" truthfully and prove nothing.

    THE STUB IS THE MODEL, ALWAYS. The real Ollama is installed on this machine and is the
    one thing no test here may talk to (M10 clarification 7).
    """
    root = tmp_path / "photos"
    root.mkdir()
    _put(root, "A.PNG", 0, date="2019:07:04 18:23:11")
    _put(root, "A.NEF", 1, date="2019:07:04 18:23:11")

    attempts: list[object] = []
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def watched_connect(self, address):
        attempts.append(address)
        return real_connect(self, address)

    def watched_connect_ex(self, address):
        attempts.append(address)
        return real_connect_ex(self, address)

    with _caption_stub("Red Door", "Blue Door") as stub:
        _editor_config(root, _CAPTION_TEMPLATE, stub.port)
        run_scan(root, exiftool_command=FAKE_COMMAND)
        monkeypatch.setattr(socket.socket, "connect", watched_connect)
        monkeypatch.setattr(socket.socket, "connect_ex", watched_connect_ex)
        assert main(["enrich", "caption", "--archive", str(root)]) == 0
        monkeypatch.undo()
    capsys.readouterr()

    # PROOF OF WORK, both halves. The command really captioned both files...
    assert set(_labels_by_filename(root)) == {"A.PNG", "A.NEF"}
    # ...and it really dialled, so "every address was loopback" is a statement about
    # something that happened rather than about an empty list.
    assert attempts, "the captioner opened no socket at all - the watch proves nothing"
    escaped = [address for address in attempts if not _loopback(address)]
    assert not escaped, (
        "pam dialled an address that is not on this machine, which vision S4 forbids"
        f" absolutely: {escaped}"
    )


def test_pam_enrich_caption_over_a_raw_file_changes_not_one_byte_of_the_archive(
    tmp_path, capsys
):
    """VISION S1 OVER THE FIRST ENRICHER THAT SHELLS OUT AT THE ARCHIVE'S OWN BYTES.

    S1 is *zero bytes of file content or embedded metadata modified*. Every command that
    has ever been measured against it renames files; `pam enrich` renames nothing, which
    is exactly why nobody has measured it - and M10 Task 6 gave it a new way to be wrong.
    A `.NEF` cannot be decoded by Pillow (HT-M9's F1), so the loader extracts the CAMERA'S
    EMBEDDED PREVIEW by running exiftool over the file. That is the first time an enricher
    hands a photograph to an external process, and exiftool is a tool whose ordinary use
    is WRITING metadata: an argument list that lost its read-only shape would rewrite the
    file in place and leave a `_original` beside it, and nothing downstream would notice,
    because the caption it produced would be perfectly correct.

    So the assertion is the whole directory, not a checksum of the file the test cares
    about: same set of paths, same bytes at each. A file exiftool wrote and renamed away
    would satisfy a same-content check on the survivors.
    """
    root = tmp_path / "photos"
    root.mkdir()
    _put(root, "RAW_ONLY.NEF", 0, date="2019:07:04 18:23:11")
    _put(root, "PICTURE.PNG", 1, date="2019:07:04 18:23:11")
    before = digests(root)
    listing = sorted(p.name for p in root.iterdir())

    with _caption_stub("Red Door", "Blue Door") as stub:
        _editor_config(root, _CAPTION_TEMPLATE, stub.port)
        run_scan(root, exiftool_command=FAKE_COMMAND)
        assert main(["enrich", "caption", "--archive", str(root)]) == 0
    capsys.readouterr()

    # Proof of work: the RAW really went through the preview branch and got a caption.
    assert "RAW_ONLY.NEF" in _labels_by_filename(root)
    assert sorted(p.name for p in root.iterdir()) == listing
    assert digests(root) == before


# --- Q3: the one arithmetic that could differ between members of one unit --------------


def _budget_archive(tmp_path: Path, budget_for_longest_ext: int) -> Path:
    """An archive root chosen so spec 6.2 rule 5's budget is a NUMBER THIS TEST PICKED.

    Rule 5, transcribed: `budget = MAX_FULL_PATH - len(root) - len(rel_dir) - longest_ext
    - 1`. The padding goes in the ROOT and the files sit directly in it, so `rel_dir` is
    the empty string and the arithmetic has no term this test has to guess at - which
    matters, because the construction below turns on a difference of ONE character.
    """
    pad = SPEC_MAX_FULL_PATH - len("jpeg") - 1 - budget_for_longest_ext - len(str(tmp_path)) - 1
    assert 10 < pad < 200, ("the temp path left no room to pad the archive root", pad)
    root = tmp_path / ("p" * pad)
    root.mkdir(parents=True)
    assert SPEC_MAX_FULL_PATH - len(str(root)) - len("jpeg") - 1 == budget_for_longest_ext
    return root


def _one_unit_named_under_a_caption(root: Path, caption: str, capsys) -> list:
    """`A.jpeg` + `A.nef` scanned, captioned and planned. Returns the plan's rows."""
    _put(root, "A.jpeg", 0, date="2019:07:04 18:23:11")
    _put(root, "A.nef", 1, date="2019:07:04 18:23:11")
    with _caption_stub(caption, caption) as stub:
        _editor_config(root, _CAPTION_TEMPLATE, stub.port)
        run_scan(root, exiftool_command=FAKE_COMMAND)
        assert main(["enrich", "caption", "--archive", str(root)]) == 0
        assert main(["plan", "--archive", str(root)]) == 0
    capsys.readouterr()
    conn = catalog(root)
    try:
        rows = repo.plan_rows(conn, open_plan(conn).id, floor=FACES_FLOOR)
    finally:
        conn.close()
    assert {row.old_filename for row in rows} == {"A.jpeg", "A.nef"}
    return rows


def test_the_path_budget_is_the_units_and_not_each_members_own(tmp_path, capsys):
    """Q3, AT THE ONE NUMBER THE MEMBERS OF A UNIT ARE GENUINELY UNLIKE EACH OTHER ABOUT.

    THE QUESTION. `tier` and `attention` are uniform across a companion unit, and spec 8.2
    rev 15 leans on it: it is the reason M8's preset bulk-approval buttons cannot split a
    unit. M10 is the first milestone whose facts come from a non-deterministic source, so
    the question is whether any per-file fact can make two members disagree. Everything
    spec 6.1 names is read off the PRIMARY, so the answer is no by construction - except
    for one number, which is computed from the MEMBERS: the path budget. Members have
    different EXTENSIONS, extensions have different LENGTHS, and rule 5's budget is a
    function of the longest extension's length.

    WHY THIS ONLY BECAME REACHABLE IN M10. Until captions arrived nothing in a proposed
    name was long enough to press against a 240-character budget on an ordinary path: a
    date is ten characters and a place name is a city. A model's label is the first token
    that will not always fit, and rule 5 answers an overflowing `{scene}` by DROPPING it
    (`_TRIM_ORDER` puts scene first) rather than by truncating it - so the outcome under
    test is a drop, and a drop decided per member is two different stems on one unit.

    THE KNIFE EDGE IS THE ASSERTION. The archive root is padded so the budget for `jpeg`
    is exactly one character less than the budget for `nef`, and the caption is chosen so
    the finished stem is exactly `budget(jpeg) + 1` characters long. So:

      * under the unit's budget - `max` over the members' extensions, which is what
        `stem_budget` does - the scene does not fit and BOTH members lose it;
      * under a per-member budget the `.nef` would keep the scene and the `.jpeg` would
        not, which is `2019-07-04_Aaaaaa-Bbbbbb-Cccccc_001.nef` beside
        `2019-07-04_001.jpeg`: ONE UNIT, TWO STEMS, vision S3.

    AND THE CONTROL, in the same test, because a boundary asserted from one side is a
    boundary that could be anywhere. One character shorter and the scene DOES survive, so
    the drop above is the budget acting and not the caption failing to arrive.

    The arithmetic is spelled from spec 6.2 rule 5 (`SPEC_MAX_FULL_PATH` is a transcribed
    literal, not an import), and the expected stem lengths are computed from the caption
    this test wrote rather than measured off the name the planner returned.
    """
    # `{date}_{scene}_{seq}` = 10 + 1 + len(scene) + 1 + 3. Spelled from the template.
    over = "Aaaaaa Bbbbbb Cccccc"  # sanitizes to 20 characters, three words (spec 6.1)
    under = "Aaaaaa Bbbbbb Ccccc"  # ...and this one to 19
    assert len(over) == 20 and len(under) == 19
    budget = 15 + len(over) - 1  # one character less than the stem the caption needs

    root = _budget_archive(tmp_path / "over", budget)
    rows = _one_unit_named_under_a_caption(root, over, capsys)
    # The scene did not fit, so rule 5 dropped it - FOR BOTH MEMBERS, `nef` included, even
    # though `nef` alone had exactly the one character it needed.
    assert {row.old_filename: row.new_filename for row in rows} == {
        "A.jpeg": "2019-07-04_001.jpeg",
        "A.nef": "2019-07-04_001.nef",
    }
    assert len({Path(row.new_filename).stem for row in rows}) == 1
    assert len({(row.tier, row.trust_score, row.attention) for row in rows}) == 1
    assert len(_plan_item_trust(root)) == 1

    # THE CONTROL. One character shorter and the same budget keeps the scene, so the drop
    # above is the budget and not a caption that never arrived.
    control = _budget_archive(tmp_path / "under", budget)
    rows = _one_unit_named_under_a_caption(control, under, capsys)
    assert {row.old_filename: row.new_filename for row in rows} == {
        "A.jpeg": "2019-07-04_Aaaaaa-Bbbbbb-Ccccc_001.jpeg",
        "A.nef": "2019-07-04_Aaaaaa-Bbbbbb-Ccccc_001.nef",
    }
    assert len({(row.tier, row.trust_score, row.attention) for row in rows}) == 1


def test_every_unit_in_a_captioned_archive_agrees_about_tier_trust_and_attention(
    tmp_path, capsys
):
    """Q3 SWEPT ACROSS A WHOLE ARCHIVE, and the partition checked against the SCANNER'S.

    The construction above takes the one arithmetic that could differ between members; this
    one refuses to reason about mechanisms at all and asserts the property over every unit
    an archive holds, after a real caption run, with the members deliberately unlike one
    another in every way M10 made possible: a member with a caption beside one that can
    never have one (`wants()` is an allowlist, so a `.AAE` sidecar is never asked), members
    whose extensions differ in length, members whose STEMS differ (the ignored editor
    suffix), and a member with no `capture` row, which spec 8.3 leaves out of every event.

    THE PARTITION IS CHECKED AGAINST AN INDEPENDENT ONE. `_grouped_by_scan` is what
    `pam scan` grouped; `unit_key` is what `pam plan` froze. Spec 7.3 rev 16's membership
    rule is exactly that the two describe the same sets - *a plan's unit is the set of
    files `pam scan` grouped, not the subset the plan happens to hold items for* - so
    comparing the planner's partition against its own `unit_key` would be the vacuous
    oracle CLAUDE.md warns about, and comparing it against the scanner's is the check.

    NON-VACUITY IS ASSERTED, not assumed: the archive must really contain several units, at
    least one of them multi-member, at least one member without a caption, and at least one
    unit whose members' extensions have different lengths. A fixture that quietly stopped
    producing those would make every assertion below true and empty.
    """
    root = tmp_path / "photos"
    root.mkdir()
    # PAIR: extension lengths differ (4 vs 3), both captionable, both located
    _put(root, "PAIR.jpeg", 0, date="2019:07:04 18:23:11", gps=TOKYO)
    _put(root, "PAIR.nef", 1, date="2019:07:04 18:23:11", gps=TOKYO)
    # SIDE: an .AAE sidecar - no capture row, no event, and `wants()` never asks it
    _put(root, "SIDE.PNG", 2, date="2020:08:05 09:00:00")
    _put(root, "SIDE.AAE", 3, date=None)
    # EDIT: the ignored editor suffix - one unit whose members' on-disk STEMS differ
    _put(root, "EDIT.NEF", 4, date="2021:09:06 11:30:00")
    _put(root, "EDIT" + IGNORED_SUFFIX + ".png", 5, date="2021:09:06 11:30:00")
    _put(root, "LONE.PNG", 6, date="2022:10:07 14:00:00")  # a unit of one
    _put(root, "NODATE.PNG", 7, date=None)  # undated: attention by its second operand

    with _caption_stub(*["Scene " + str(n) for n in range(1, 9)]) as stub:
        _editor_config(root, _CAPTION_TEMPLATE, stub.port)
        run_scan(root, exiftool_command=FAKE_COMMAND)
        assert main(["enrich", "caption", "--archive", str(root)]) == 0
        assert main(["plan", "--archive", str(root)]) == 0
    capsys.readouterr()

    scanned = _grouped_by_scan(root)
    captioned = set(_labels_by_filename(root))
    conn = catalog(root)
    try:
        rows = repo.plan_rows(conn, open_plan(conn).id, floor=FACES_FLOOR)
    finally:
        conn.close()

    # --- non-vacuity, before anything is concluded from the sweep ----------------------
    assert len(scanned) == 3, scanned
    assert all(len(members) == 2 for members in scanned.values()), scanned
    assert "SIDE.AAE" not in captioned and "PAIR.jpeg" in captioned, sorted(captioned)
    assert {"PAIR.jpeg", "PAIR.nef"} in list(scanned.values()), scanned

    # --- the two partitions describe the same sets (spec 7.3 rev 16) -------------------
    by_unit = {}
    for row in rows:
        by_unit.setdefault(row.unit_key, set()).add(row.old_filename)
    multi = {frozenset(names) for names in by_unit.values() if len(names) > 1}
    assert multi == {frozenset(Path(m).name for m in ms) for ms in scanned.values()}
    assert len(by_unit) == 5, by_unit  # three pairs plus the two lone files

    # --- the property ------------------------------------------------------------------
    for unit_key, names in by_unit.items():
        members = [row for row in rows if row.unit_key == unit_key]
        assert len({(row.tier, row.trust_score, row.attention) for row in members}) == 1, (
            unit_key,
            names,
            [(r.old_filename, r.tier, r.trust_score, r.attention) for r in members],
        )
    assert len(_plan_item_trust(root)) == len(by_unit)


# --- S1/S2/S3: a crash inside the unit F12 lived in -----------------------------------


def _partially_conforming_unit(root: Path) -> None:
    """The archive F12 lived in: one unit, one member already named, stems that differ."""
    _put(root, "2019-07-04_001.NEF", 0, date="2019:07:04 18:23:11")
    _put(root, f"2019-07-04_001{IGNORED_SUFFIX}.png", 1, date="2019:07:04 18:23:11")
    _editor_config(root, DATE_ONLY_TEMPLATE)


def test_a_crash_between_the_members_of_a_partially_conforming_unit_is_healed_whole(
    tmp_path, monkeypatch, capsys, set_fault
):
    """S1, S2 AND S3 OVER THE ONE UNIT SHAPE M10 INVENTED, INTERRUPTED MID-RENAME.

    THE QUESTION, and it is question 2 asked of `pam apply` rather than of a filter. Task 1
    put a NO-OP ITEM in the plan - a row proposing the name its file already has, which
    spec 7.0 J12 resolves at preflight with no journal entry and no `os.rename`. A name the
    user then types turns that row back into a REAL rename, because J12 is computed from
    the item's CURRENT `new_filename` and never frozen at plan time (invariant I2). So the
    unit that reaches spec 7.1's loop has a member whose rename exists only because of an
    edit made after the plan was written, and spec 7.2's recovery has to put that member
    back on a crash exactly as it would any other.

    A crash between the members is the state spec 7.0 J6 calls transient: one member
    resolved `done`, the next sitting in `intent`, one unit across two stems on disk. It is
    legal only until recovery resolves it, and the whole of F12 was that the layers meant
    to see a unit were reasoning over the plan's items. Recovery reasons over the JOURNAL,
    which is a third partition again - so this asks whether the journal's idea of the unit
    survives a member that was a no-op when the plan was written.

    THE ORACLE IS THE ARCHIVE. Names and digests are compared against a snapshot taken
    before anything ran, so "healed" means every byte back at every original path (S1 and
    S2 together), and the grouping is re-read from `files.companion_group_id` afterwards so
    the unit is proven whole rather than assumed (S3).

    THE CRASH IS IN PROCESS, which is `tests/test_recover.py`'s layer and not
    `tests/test_faults_matrix.py`'s: `crash:` raises SystemExit, which unwinds rather than
    kills, so `next_invocation()` is what stands in for a fresh interpreter. The matrix is
    the module that crosses a real process boundary, and it does so for the shapes spec 9.2
    enumerates; this is about a shape it does not have.
    """
    root = tmp_path / "photos"
    root.mkdir()
    _partially_conforming_unit(root)
    before = digests(root)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert main(["plan", "--archive", str(root)]) == 0
    capsys.readouterr()

    conn = catalog(root)
    try:
        plan = open_plan(conn)
        rows = repo.plan_rows(conn, plan.id, floor=FACES_FLOOR)
    finally:
        conn.close()
    # The plan really does hold a no-op item beside a real rename.
    assert {row.old_filename: row.new_filename for row in rows} == {
        "2019-07-04_001.NEF": "2019-07-04_001.NEF",
        f"2019-07-04_001{IGNORED_SUFFIX}.png": "2019-07-04_001.png",
    }
    png = next(row for row in rows if row.old_filename.endswith(".png"))

    def work(server):
        _request(server, "POST", f"/item/{png.id}/rename", {"new_filename": "Holiday.png"})
        return _request(server, "POST", f"/plan/{plan.id}/decide-filter", {"action": "approve"})

    assert _drive(plan.id, root, work)["decided"] == 2
    # The typed name turned the no-op item into a real rename - invariant I2, and the
    # reason this unit has a "between the members" at all.
    conn = catalog(root)
    try:
        assert {
            r.old_filename: r.new_filename
            for r in repo.plan_rows(conn, plan.id, floor=FACES_FLOOR)
        } == {
            "2019-07-04_001.NEF": "Holiday.NEF",
            f"2019-07-04_001{IGNORED_SUFFIX}.png": "Holiday.png",
        }
    finally:
        conn.close()

    set_fault.arm("crash:mid_companion_unit:1")
    monkeypatch.setattr("builtins.input", lambda: "apply")
    # `crash:` raises SystemExit inside `pam apply`; click's own handling turns that into
    # this call's return value rather than letting it escape, so the crash is proven by
    # the exit code AND by the checkpoint's own hit count - never by the return alone,
    # which a command that merely failed would also produce.
    assert main(["apply", str(plan.id), "--archive", str(root)]) != 0
    assert set_fault.hits("mid_companion_unit") == 1
    set_fault.next_invocation()
    capsys.readouterr()

    # The transient state spec 7.0 J6 permits: the unit really is split on disk right now.
    assert len(_stems(_archive_names(root))) == 2, _archive_names(root)

    # The next archive-scoped command runs spec 7.2's recovery before its own work.
    assert main(["verify", "--archive", str(root)]) == 0
    capsys.readouterr()

    # HEALED WHOLE: every original path, every original byte, and the group intact.
    assert digests(root) == before
    run_scan(root, exiftool_command=FAKE_COMMAND)
    groups = _grouped_by_scan(root)
    assert len(groups) == 1 and next(iter(groups.values())) == {
        "2019-07-04_001.NEF",
        f"2019-07-04_001{IGNORED_SUFFIX}.png",
    }


# --- S2: a new fact arriving between the apply and the undo ---------------------------


def _seed_caption(root: Path, filename: str, label: str, version: str) -> None:
    """One caption fact, written with the SUITE'S OWN SQL at a chosen model version.

    `run_enricher` refuses a `--refresh-older-than-model` newer than the installed
    enricher's version, and rightly - re-running would rewrite the facts the refresh
    itself calls stale. So a fact the shipped 1.0 is newer than has to be seeded, exactly
    as `test_refreshing_the_caption_model_after_a_plan_exists_does_not_move_that_plan`
    seeds its 0.9. `inferred_facts` is content-addressed, so the join is on `sha256`.
    """
    conn = catalog(root)
    try:
        sha = str(
            conn.execute("SELECT sha256 FROM files WHERE rel_path = ?", (filename,)).fetchone()[
                "sha256"
            ]
        )
        conn.execute(
            "INSERT INTO inferred_facts (sha256, kind, value_json, confidence, model_name,"
            " model_version, generated_at) VALUES (?, 'caption', ?, NULL, 'qwen2.5vl',"
            " ?, '2026-09-02T09:00:00')",
            (sha, json.dumps({"label": label, "sentence": "The first answer."}), version),
        )
        conn.commit()
    finally:
        conn.close()


def test_a_caption_written_between_apply_and_undo_does_not_change_what_undo_puts_back(
    tmp_path, monkeypatch, capsys
):
    """S2 WITH A NON-DETERMINISTIC FACT LANDING IN THE MIDDLE OF THE ROUND TRIP.

    `pam enrich` is the newest archive-scoped command and M10 gives it a source that can
    answer differently every time it is asked. The existing constructions ask what it does
    to an OPEN plan (invariant I4: nothing). This asks the other half, which only became
    askable once the enricher had a model behind it: what does it do to a plan that has
    already been APPLIED and is about to be undone?

    S2 is *100% of applied renames restored to their exact original names by undo*, and
    undo reverses the JOURNAL - not the plan, and not the catalog's facts. So the property
    is that a caption written after the apply is invisible to the round trip, and the only
    way to see that it really is invisible is to make it a caption that WOULD have changed
    the names: the same enricher, the same photographs, a different answer.

    THE CONTROL IS WHAT MAKES "UNCHANGED" MEAN SOMETHING, as it does in the I4 pair above:
    a fresh `pam plan` after the undo proposes the NEW name from the same catalog, so the
    undo put the old names back because undo reverses a journal, not because nothing in
    the archive had moved.

    The comparison is a whole-directory digest map, so "exact original names" is asserted
    as paths AND bytes rather than as a count of restored rows.
    """
    root = tmp_path / "photos"
    root.mkdir()
    _put(root, "A.PNG", 0, date="2019:07:04 18:23:11")
    _put(root, "A.AAE", 1, date=None)  # the companion sidecar: one unit, two members
    before = digests(root)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    _seed_caption(root, "A.PNG", "Old Guess", "0.9")

    _write_config(root, '[naming]\ntemplate = "' + _CAPTION_TEMPLATE + '"\n')
    assert main(["plan", "--archive", str(root)]) == 0
    conn = catalog(root)
    try:
        plan = open_plan(conn)
    finally:
        conn.close()
    approve_archive(root, plan.id)  # F1: apply renames only what was approved
    monkeypatch.setattr("builtins.input", lambda: "apply")
    assert main(["apply", str(plan.id), "--archive", str(root)]) == 0
    assert "2 renamed, 0 failed" in capsys.readouterr().out
    assert _archive_names(root) == {
        "2019-07-04_Old-Guess_001.PNG",
        "2019-07-04_Old-Guess_001.AAE",
    }

    # The model is asked again, AFTER the rename, and answers differently. The files' bytes
    # did not change, so `inferred_facts` would call them already done - the refresh is the
    # only command that asks a second time, and spec 6.4's supersede row is what it fires.
    with _caption_stub("Blue Door") as stub:
        _caption_config(root, stub.port)
        run_scan(root, exiftool_command=FAKE_COMMAND)
        assert (
            main(
                [
                    "enrich",
                    "caption",
                    "--archive",
                    str(root),
                    "--refresh-older-than-model",
                    "1.0",
                ]
            )
            == 0
        )
    capsys.readouterr()
    assert _caption_labels(root) == [("1.0", "Blue Door")]

    conn = catalog(root)
    try:
        session = str(
            conn.execute("SELECT session_id FROM journal ORDER BY id DESC LIMIT 1").fetchone()[
                "session_id"
            ]
        )
    finally:
        conn.close()
    assert main(["undo", session, "--archive", str(root)]) == 0
    assert "2 put back, 0 failed" in capsys.readouterr().out

    # S2: the exact original paths and the exact original bytes, with a live fact in the
    # catalog saying the names should have been something else.
    assert digests(root) == before
    assert main(["verify", "--archive", str(root)]) == 0
    capsys.readouterr()

    # THE CONTROL: a NEW plan is where the new caption reaches a name (spec 6.3 rule 5).
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert main(["plan", "--archive", str(root)]) == 0
    conn = catalog(root)
    try:
        fresh = open_plan(conn)
        proposals = {
            r.old_filename: r.new_filename
            for r in repo.plan_rows(conn, fresh.id, floor=FACES_FLOOR)
        }
    finally:
        conn.close()
    capsys.readouterr()
    assert proposals == {
        "A.PNG": "2019-07-04_Blue-Door_001.PNG",
        "A.AAE": "2019-07-04_Blue-Door_001.AAE",
    }


# =====================================================================================
# M11 TASK 4 - THE FLOOR UNDER THIS MODULE'S OWN INVENTORY (reviewer's finding, M1713)
# =====================================================================================
#
# WHY THIS EXISTS, and it is CLAUDE.md's M1670 arriving at a fifth file. That lesson is
# that the construction hunt's blind spot - *a property no single module owns is invisible
# to a mutation pass scoped to one task's files* - is true of the INSTRUMENTS as well as
# of the product. Four modules already carry a registry of this shape
# (`tests/test_net_canary.py`, M1670's original; `tests/test_ci_lanes.py`, F5;
# `tests/test_events_grouping.py`, F1; `tests/test_f12_unit_membership.py`, F3) and this
# file - the largest and most safety-critical of the set, holding every M9 and M10
# construction - carried none.
#
# MEASURED, NOT ASSUMED (M1713): deleting
# `test_q_matching_the_ignored_editor_suffix_selects_one_member_and_widens_to_the_unit`
# outright - 93 lines, M10's OWN answer to the hunt's first question, and the only
# end-to-end proof that vision S3 survives the fifth non-uniform filter dimension - left
# the suite GREEN at 2982 passed + 1 skipped, exit 0.
#
# EVERY CONSTRUCTION IN THIS FILE IS REGISTERED, and that is not laziness about picking
# the load-bearing ones. It is what a construction IS: each one was built precisely
# because it is the only place in the repository where some safety property is observed
# across the module boundaries that own it between them. A construction that were merely
# a duplicate of a unit test would not belong in this file at all. So the criterion for
# entry here is membership, and the reason column says which property each one holds.
#
# THE LIST IS SPELLED OUT BY HAND, not derived from `globals()`. An inventory built by
# asking the module what it contains agrees with whatever the module happens to contain
# and would have passed under M1713 (CLAUDE.md's ENRICHERS lesson, and M8's sharpening of
# it: an oracle that asserts the partition production produced is vacuous).
LOAD_BEARING_CONTROLS = {
    # --- M9's nine ---------------------------------------------------------------------
    "test_a_geocoded_event_label_selects_one_member_of_a_unit_and_decide_filter_widens_it": (
        "vision S3 through the THIRD non-uniform filter dimension - the event label - and "
        "the only one whose values `pam plan`'s own geocoder writes rather than a fixture"
    ),
    "test_a_place_fact_selects_one_member_of_a_unit_and_decide_filter_widens_it": (
        "vision S3 through the FOURTH non-uniform filter dimension, M10 Task 10's `place`"
    ),
    "test_an_event_nav_group_selects_one_member_of_a_unit_and_decide_filter_widens_it": (
        "M8's own route (`group=YYYY-MM.eN`) re-driven over a label pam really geocoded; "
        "every other event-nav test seeds `label_source='user'` and cannot reach the writer"
    ),
    "test_pam_enrich_never_opens_a_cloud_placeholder_that_still_carries_a_sha256": (
        "vision R2 over the one placeholder state the runner's `sha256 is None` skip does "
        "NOT cover - and M697 is why the dangerous CALL is watched and not the outcome"
    ),
    "test_pam_enrich_between_plan_and_apply_leaves_the_plan_and_its_unit_intact": (
        "invariant I4 for the first archive-scoped command ever to sit between plan and "
        "apply; nothing else in the suite drives that ordering end to end"
    ),
    "test_pam_enrich_caption_between_plan_and_apply_renames_to_exactly_what_was_reviewed": (
        "I4 again with a NON-DETERMINISTIC fact in the middle: what reached disk is what "
        "the human read, not what the model last said"
    ),
    "test_refreshing_the_caption_model_after_a_plan_exists_does_not_move_that_plan": (
        "the refresh path's half of I4 - the only command that asks the model twice"
    ),
    "test_a_superseded_caption_still_explains_the_plan_it_produced": (
        "spec 6.4's supersede chain read from the PLAN's side: the provenance of a name "
        "already on disk survives the fact that produced it being superseded"
    ),
    "test_a_partially_conforming_unit_survives_a_review_ui_rename_whole": (
        "F12 ITSELF, and the reason `v0.1-m10` was not tagged with an open S3 breach. "
        "This test asserted the BUG through M9 and asserts the PROPERTY since M10 Task 1"
    ),
    # --- M10's eight (the `M10 TASK 12 STEP 2` block) ------------------------------------
    "test_q_matching_the_ignored_editor_suffix_selects_one_member_and_widens_to_the_unit": (
        "vision S3 through the FIFTH non-uniform filter dimension and M10's own answer to "
        "the hunt's Q1 - the first that is about the members' own STEMS, and the first "
        "whose left-behind member is a NO-OP ITEM. Measured: M1713 deleted it and the "
        "suite stayed green at 2982"
    ),
    "test_two_captioned_members_whose_stems_differ_still_take_the_primarys_scene": (
        "the hunt's Q2 where M9's F12 geometry meets M10's non-deterministic fact: the "
        "ONLY fixture with two DIFFERENT captions over two DIFFERENT on-disk stems, which "
        "is the geometry `tests/test_t1_enriched_chain.py` cannot build. Measured: M1709"
    ),
    "test_the_captioner_is_inside_a_connect_watch_and_every_address_it_dials_is_loopback": (
        "vision S4 over the one enricher that actually opens a socket. "
        "`tests/test_net_canary.py`'s enrich stage runs `phash`, so the widening M10 Task "
        "7 made FOR the captioner is proven there only by a synthetic `record(...)`. Its "
        "non-emptiness assert is the sole guardian of the watch's non-vacuity (M1712b)"
    ),
    "test_pam_enrich_caption_over_a_raw_file_changes_not_one_byte_of_the_archive": (
        "vision S1 over the first enricher that hands a photograph to an external process "
        "- exiftool, whose ordinary use is WRITING metadata. `pam enrich` renames nothing, "
        "which is exactly why nothing else has ever measured it against S1"
    ),
    "test_the_path_budget_is_the_units_and_not_each_members_own": (
        "the hunt's Q3 at the ONE arithmetic members of a unit are genuinely unlike each "
        "other about, on a one-character knife edge, with its own control. Measured: it "
        "dies under M1710 (`max` to `min`) AND under M1709, for different reasons"
    ),
    "test_every_unit_in_a_captioned_archive_agrees_about_tier_trust_and_attention": (
        "Q3 swept over a whole archive, with the planner's partition checked against the "
        "SCANNER's rather than against its own `unit_key` - spec 7.3 rev 16's membership "
        "rule, and the vacuous-oracle trap avoided by construction"
    ),
    "test_a_crash_between_the_members_of_a_partially_conforming_unit_is_healed_whole": (
        "S1+S2+S3 over the unit shape M10 invented, interrupted mid-rename. Recovery "
        "reasons over the JOURNAL - a third partition again - and this asks whether the "
        "journal's idea of the unit survives a member that was a no-op at plan time"
    ),
    "test_a_caption_written_between_apply_and_undo_does_not_change_what_undo_puts_back": (
        "vision S2 with a fact that WOULD have changed the names landing mid-round-trip, "
        "plus the control that makes 'unchanged' mean something"
    ),
}

assert LOAD_BEARING_CONTROLS, "this module's control list is empty; it protects nothing"


def test_no_construction_in_this_file_can_be_deleted_without_saying_so():
    """The floor under this module's own inventory (reviewer's F1, on M1670's precedent).

    Removing a construction is a deliberate edit to `LOAD_BEARING_CONTROLS`, with the
    reason it was kept written beside it - never a silently smaller suite.
    """
    present = {name for name in globals() if name.startswith("test_")}
    missing = sorted(set(LOAD_BEARING_CONTROLS) - present)
    assert missing == [], (
        f"a safety construction has been deleted from {Path(__file__).name}: {missing}."
        " Each is the only place in the repository where the property beside it is"
        " observed across the module boundaries that own it between them"
        f" ({[LOAD_BEARING_CONTROLS[m] for m in missing]})."
    )
