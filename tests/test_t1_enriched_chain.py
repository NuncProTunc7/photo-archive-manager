"""M9's chain: scan -> ENRICH -> plan -> review -> apply -> undo, with `{place}` on disk.

WHY THIS EXISTS BESIDE `tests/test_t1_integration.py`. That module drives the M7 chain -
scan, plan, review, apply - and it is still the authority on those four handoffs. It has
no `pam enrich` in it and it cannot have one: its archive carries no GPS, its payloads are
sentences rather than pictures, and its `PLANNED_NAMES` are spelled against the DEFAULT
template. **Nothing before M9 ran a chain with enrichment in it** (M9 plan, Task 13
Step 1), and two consequences of that were measured rather than assumed:

1. **`{place}` had never reached DISK.** `tests/test_place_naming.py` proves the token
   resolves and that `plan_items.generated_filename` carries it; no test applied such a
   plan. A rename is only real on disk, and everything between the plan row and the
   filesystem - the collision search, `unit_key`, the journal, `perform_verified_rename`,
   `set_file_path` - had never seen a name a geocoder supplied.
2. **The default template does not ask for `{place}` at all.** `config.py` ships
   `{date}_{event-or-scene}_{people}_{seq}`, so an archive can be enriched, planned and
   applied with every place fact in the catalog and not one of them in a filename. This
   module writes the override into `pam.toml` exactly as `docs/testing/ht-m9.md` Leg A
   asks the owner to, which makes the two artefacts the same experiment at two scales.

THE ENRICHERS ARE THE REAL ONES. `pam enrich` runs `phash` and `geocode` as shipped -
Pillow really decodes the pictures, `reverse_geocoder` really answers from its offline
dataset - because the question here is whether the COMMANDS are wired to each other, and
a stub enricher would answer a different one. `tests/test_enrich_runner.py` owns the
runner's mechanics with stubs; this file owns the chain.

THE SUBSTITUTIONS ARE THE SAME TWO `test_t1_integration.py` NAMES, and for the same
reasons: `pam scan` goes through `run_scan` with the suite's fake exiftool (the CLI has no
seam for it, and these files carry no real EXIF), and `pam review` goes through
`run_review` with its `serve` seam (the real command blocks until Ctrl+C). `pam enrich`,
`pam plan`, `pam apply` and `pam undo` all go through `cli.main` with the arguments a user
types, typed confirmation included.

UNDO IS PART OF THE CHAIN HERE AND WAS NOT PART OF M7'S. A place name reaching disk is
only safe if it can be taken off again, and `--exact` is the guarantee `pam undo` makes.
The digest map below is the same instrument `tools/ht_manifest.py --exact` is: content
identifies the file, so one map answers "is every file still here", "did any byte change"
and "is each one back under its original name".
"""

import hashlib
import json
import threading
import webbrowser
from http.client import HTTPConnection
from pathlib import Path

import pytest
from archives import (
    FAKE_COMMAND,
    approve_archive,
    assert_verify_agrees,
    catalog,
    digests,
    open_plan,
    raw_with_preview_bytes,
)
from ollama_stub import generate_reply, scripted

from pam.catalog import repo
from pam.catalog.repo.files import FileRow
from pam.cli import main
from pam.enrich.base import RAW_EXTS, pil_image_loader
from pam.paths import workspace_for
from pam.scan.runner import run_scan
from pam.webui import run_review

LIBRARY = Path(__file__).resolve().parent / "tiny_library"
# A picture Pillow can really decode, borrowed from the spec 9.1 library rather than
# generated here - the same source `tests/test_enrich_command.py` uses, and for the same
# reason: an undecodable payload makes `phash` count `failed` and `pam enrich` exit 1,
# which is correct behaviour and useless as the happy path.
PICTURE = (LIBRARY / "phash_pattern_base.png").read_bytes()

# THE TEMPLATE OVERRIDE, and it is the whole reason this module can see anything. Written
# out here, not built from `TOKEN_NAMES`: a template assembled from the token list would
# contain `{place}` for any value of that list.
TEMPLATE = "{date}_{place}_{seq}"

# Three coordinates and the names `rg_cities1000` gives them. SPELLED OUT, because an
# expectation read back from `reverse_geocoder` would agree with a geocoder returning
# `El Tarter, Andorra` for every point on earth - which is exactly what `mode=2` does on
# this platform (m9-progress.md, "the five things Tasks 9-13 must not re-derive").
TOKYO = (35.6895, 139.6917)
SYDNEY = (-33.8688, 151.2093)
TOKYO_NAME = "Tokyo"
SYDNEY_NAME = "Sydney"

# The archive: one companion unit whose two members share a stem and a coordinate, one
# lone file somewhere else on earth, and one file with NO GPS AT ALL. The third is not
# padding - spec 6.1's graceful degradation says an absent token emits nothing rather
# than a placeholder, and a chain that only ever planned located files could not tell
# "the token was dropped" from "the token was never asked for".
ARCHIVE = {
    "A.PNG": ("2019:07:04 18:23:11", TOKYO),
    "A.NEF": ("2019:07:04 18:23:11", TOKYO),
    "B.PNG": ("2020:08:05 09:15:00", SYDNEY),
    "C.PNG": ("2021:09:06 11:02:03", None),
}

# What the planner owes each file under TEMPLATE, written by hand from spec 6.1's
# semantics. `A.PNG` and `A.NEF` share a stem, so spec 5.4 makes them one companion unit
# and one name; spec 5.4's primary rule is display > RAW, so the unit is named from
# `A.PNG` - and both members carry the same coordinate anyway, which is asserted below
# rather than left to the election.
PLANNED = {
    "A.PNG": f"2019-07-04_{TOKYO_NAME}_001.PNG",
    "A.NEF": f"2019-07-04_{TOKYO_NAME}_001.NEF",
    "B.PNG": f"2020-08-05_{SYDNEY_NAME}_001.PNG",
    # No GPS, so no `place` fact, so no token: the stem is date and seq alone.
    "C.PNG": "2021-09-06_001.PNG",
}


def _put(root: Path, name: str, date: str, gps, salt: int) -> None:
    """One real picture plus the sidecar the fake exiftool reads its tags from.

    The last byte is varied per file rather than appending to the stream: PNG's trailing
    bytes are the IEND chunk's CRC, which Pillow does not verify, so every file decodes
    and every file has its own sha256. Distinct digests matter twice here - `digests()`
    refuses a collision outright, and `inferred_facts` is CONTENT-addressed, so two
    byte-identical files would share one fact and make the per-file assertions weaker
    than they read.

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
    tags: dict[str, object] = {"DateTimeOriginal": date, "PamTestSidecar": name}
    if gps is not None:
        tags["GPSLatitude"], tags["GPSLongitude"] = gps
    (root / f"{name}.tags.json").write_text(json.dumps(tags), encoding="utf-8")


def _archive_names(root: Path) -> set[str]:
    return {p.name for p in root.iterdir() if not p.name.endswith(".tags.json")}


@pytest.fixture(autouse=True)
def no_real_browser(monkeypatch):
    """`run_review` opens a browser. A suite that opens real tabs is a broken suite."""
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: opened.append(url) or True)
    return opened


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


def _live_places(root: Path) -> dict[str, str]:
    """filename -> live place name, read with the SUITE'S OWN SQL.

    Not `repo.places_by_file_id`: that query is one of the things this chain is checking,
    and an oracle built from the production symbol agrees with a broken one (m5.md's
    `repo.open_plan` lesson, M8's M922/M924).
    """
    conn = catalog(root)
    try:
        rows = conn.execute(
            "SELECT f.rel_path AS rel_path, i.value_json AS value_json FROM files f"
            " JOIN inferred_facts i ON i.sha256 = f.sha256"
            " WHERE i.superseded_by IS NULL AND i.kind = 'place'"
        ).fetchall()
    finally:
        conn.close()
    return {row["rel_path"]: json.loads(row["value_json"])["place"] for row in rows}


def _session_id(root: Path) -> str:
    conn = catalog(root)
    try:
        rows = conn.execute("SELECT DISTINCT session_id FROM journal").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, rows
    return str(rows[0]["session_id"])


def test_the_m9_chain_puts_a_geocoded_place_name_on_disk_and_takes_it_off_again(
    tmp_path, monkeypatch, capsys, no_real_browser
):
    """scan -> enrich -> plan -> review -> apply -> undo, through the entry points typed."""
    root = tmp_path / "photos"
    root.mkdir()
    for salt, (name, (date, gps)) in enumerate(ARCHIVE.items()):
        _put(root, name, date, gps, salt)
    before = digests(root)

    # --- scan --------------------------------------------------------------------------
    scanned = run_scan(root, exiftool_command=FAKE_COMMAND)
    assert not scanned.errors, scanned.errors

    # --- the template override, which is HT-M9 Leg A's `pam.toml` edit -----------------
    # Written after the scan because the scan is what creates the workspace. Asserted to
    # have taken effect further down, on the plan row, rather than trusted: a config file
    # in the wrong directory is silently the default template, and the default template
    # is exactly the state that would make this whole test vacuous.
    workspace_for(root).config_path.write_text(
        f'[naming]\ntemplate = "{TEMPLATE}"\n', encoding="utf-8"
    )

    # --- enrich, through the CLI, with the enrichers pam really ships ------------------
    capsys.readouterr()
    assert main(["enrich", "--archive", str(root)]) == 0
    enrich_out = capsys.readouterr().out
    assert "geocode" in enrich_out and "phash" in enrich_out
    # The chain's new handoff: `pam enrich` sends the user to `pam plan`.
    assert "Next: pam plan" in enrich_out

    # The geocoder answered from its offline dataset, per file, with the right names -
    # and the file with no GPS got no fact rather than an empty one.
    assert _live_places(root) == {
        "A.PNG": TOKYO_NAME,
        "A.NEF": TOKYO_NAME,
        "B.PNG": SYDNEY_NAME,
    }

    # --- plan, through the CLI ---------------------------------------------------------
    assert main(["plan", "--archive", str(root)]) == 0
    plan_out = capsys.readouterr().out
    conn = catalog(root)
    try:
        plan = open_plan(conn)
    finally:
        conn.close()
    # The override really reached the planner. `plans.template` is frozen at plan time,
    # so this is the plan's own record of which template built its names.
    assert plan.template == TEMPLATE
    assert f"Next: pam review {plan.id}" in plan_out

    # --- review, through the server `pam review` runs ----------------------------------
    def drive(server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            listing = _request(server, "GET", f"/plan/{plan.id}/items?limit=200")
            # The proposed names, asserted against the hand-written table. This is where
            # `{place}` becomes visible: it is in three of the four names and absent from
            # the fourth, and the browser is reading exactly what the planner wrote.
            assert {
                item["old_filename"]: item["new_filename"] for item in listing["items"]
            } == PLANNED
            approved = _request(
                server,
                "POST",
                f"/plan/{plan.id}/decide-filter",
                {"action": "approve", "status": "proposed"},
            )
            assert (approved["decided"], approved["excluded_low"]) == (4, 0)
        finally:
            server.shutdown()
            thread.join(timeout=20)

    summary = run_review(plan.id, root, serve=drive)
    review_out = capsys.readouterr().out
    assert (summary.approved, summary.skipped, summary.remaining) == (4, 0, 0)
    assert f"Next: pam apply {plan.id}" in review_out
    assert no_real_browser == [summary.url]

    # --- apply, through the CLI, with UX 4's typed confirmation ------------------------
    monkeypatch.setattr("builtins.input", lambda: "apply")
    assert main(["apply", str(plan.id), "--archive", str(root)]) == 0
    apply_out = capsys.readouterr().out
    assert "4 renamed, 0 failed" in apply_out

    # --- THE DISK. This is the assertion the milestone did not have. -------------------
    assert _archive_names(root) == set(PLANNED.values())
    # Named individually as well as as a set, because the set comparison would pass if
    # two files swapped names - and the companion unit is the pair that could.
    for original, renamed in PLANNED.items():
        assert (root / renamed).is_file(), f"{original} did not become {renamed}"
    # A geocoded name really is on disk, spelled out so the claim does not rest on the
    # table above being read carefully.
    assert (root / f"2019-07-04_{TOKYO_NAME}_001.PNG").is_file()
    assert (root / f"2019-07-04_{TOKYO_NAME}_001.NEF").is_file()
    # Vision S3: the companion unit took ONE stem across both members.
    assert len({Path(PLANNED["A.PNG"]).stem, Path(PLANNED["A.NEF"]).stem}) == 1
    # Not one byte moved. `--bytes`, in the shape `tools/ht_manifest.py` uses.
    assert set(digests(root)) == set(before)

    assert_verify_agrees(root, where="after the enriched T1 chain")

    # --- undo, through the CLI --------------------------------------------------------
    session_id = _session_id(root)
    assert main(["undo", session_id, "--archive", str(root)]) == 0
    undo_out = capsys.readouterr().out
    assert "4 put back, 0 failed" in undo_out

    # `--exact`: every file back under its original name, byte for byte. The digest map
    # compares content AND name in one comparison, which a name-only check cannot.
    assert digests(root) == before
    assert_verify_agrees(root, where="after undoing the enriched T1 chain")


def test_the_place_facts_outlive_the_undo_and_the_next_plan_proposes_the_same_names(
    tmp_path, monkeypatch, capsys, no_real_browser
):
    """A second pass over the same archive must land on the SAME names, not on `_002`.

    Spec 6.2 rule 6: the same inputs produce the same name. After an undo the archive is
    byte-identical and the place facts are still live - `inferred_facts` is keyed by
    sha256 and nothing about an undo touches it - so the re-plan is the cheapest test
    that enrichment is not a one-shot. It is also the state the owner will be in halfway
    through HT-M9 Leg A, where a second `pam plan` after a round trip is the natural
    thing to type.

    `pam enrich` is deliberately NOT re-run here: the claim is that the facts SURVIVE,
    and re-running the enricher would make the assertion true either way.
    """
    root = tmp_path / "photos"
    root.mkdir()
    for salt, (name, (date, gps)) in enumerate(ARCHIVE.items()):
        _put(root, name, date, gps, salt)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    workspace_for(root).config_path.write_text(
        f'[naming]\ntemplate = "{TEMPLATE}"\n', encoding="utf-8"
    )
    assert main(["enrich", "--archive", str(root)]) == 0
    assert main(["plan", "--archive", str(root)]) == 0
    conn = catalog(root)
    try:
        first = open_plan(conn).id
    finally:
        conn.close()
    approve_archive(root, first)  # F1: apply renames only what was approved
    monkeypatch.setattr("builtins.input", lambda: "apply")
    assert main(["apply", str(first), "--archive", str(root)]) == 0
    assert main(["undo", _session_id(root), "--archive", str(root)]) == 0
    capsys.readouterr()

    # A re-scan first, because that is what a user does and because spec 6.3 rule 5
    # supersedes the open plan on it.
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert main(["plan", "--archive", str(root)]) == 0
    capsys.readouterr()

    conn = catalog(root)
    try:
        second = open_plan(conn).id
        rows = conn.execute(
            "SELECT f.rel_path AS old, p.generated_filename AS new FROM plan_items p"
            " JOIN files f ON f.id = p.file_id WHERE p.plan_id = ?",
            (second,),
        ).fetchall()
    finally:
        conn.close()
    assert second != first
    assert {row["old"]: row["new"] for row in rows} == PLANNED


def test_the_geocoder_used_by_this_chain_is_the_offline_one(tmp_path):
    """Vision S4, at the only place in this module that could break it silently.

    `pam enrich` above is allowed to succeed only because `reverse_geocoder`'s dataset is
    on disk; with it missing the package DOWNLOADS it over plain HTTP, and the enricher
    is required to refuse instead. That refusal is `tests/test_geocode.py`'s subject. What
    is asserted here is the other half - that the run this module drives really did go
    through the offline path - so a future change that made the chain pass by reaching
    the network would be red HERE, in the file whose whole claim is "these commands are
    wired together".
    """
    from pam.enrich.geocode import GeocodeEnricher

    ok, reason = GeocodeEnricher({}).available()
    assert ok, reason
    assert "no network" in reason


def _planned_names(root: Path):
    """The open plan's template, and old filename -> proposed filename."""
    conn = catalog(root)
    try:
        plan = open_plan(conn)
        rows = conn.execute(
            "SELECT f.rel_path AS old, p.generated_filename AS new FROM plan_items p"
            " JOIN files f ON f.id = p.file_id WHERE p.plan_id = ?",
            (plan.id,),
        ).fetchall()
    finally:
        conn.close()
    return plan.template, {row["old"]: row["new"] for row in rows}


def test_a_geocoded_name_reaches_the_DEFAULT_template_without_pam_enrich_at_all(
    tmp_path, capsys
):
    """THE CORRECTION TO WHAT M9 EXPECTED, and it changes what HT-M9 Leg A can conclude.

    `docs/implementation-notes/m9-progress.md` records that `{place}` is not in the
    shipped template and warns that Leg A would therefore "run `pam enrich`, see place
    facts land in the catalog, and observe no change in a single proposed name". The
    first half is true. The second is NOT, and this test is the measurement: the default
    template contains `{event-or-scene}`, spec 8.3 makes an event's label "the geocoded
    place of the event's GPS centroid", and `naming/planner.py` geocodes those labels
    ITSELF, from the `capture` GPS the scanner recorded - `event_labels` is called from
    `_group`, in `pam plan`'s own transaction.

    So `pam plan` alone puts `Tokyo` in a filename. **No `pam enrich` is run here at
    all**, which is the whole point: an owner who runs enrich and then plan under the
    default template sees a place name appear and cannot tell which command produced it.
    That is a stronger reason for Leg A's template override than the one the handoff
    gives, and it is why the override is written into the procedure as a `{place}`-only
    template rather than as a template carrying both tokens.
    """
    root = tmp_path / "photos"
    root.mkdir()
    for salt, (name, (date, gps)) in enumerate(ARCHIVE.items()):
        _put(root, name, date, gps, salt)
    run_scan(root, exiftool_command=FAKE_COMMAND)

    assert main(["plan", "--archive", str(root)]) == 0
    capsys.readouterr()

    template, names = _planned_names(root)
    # The shipped default, spelled out rather than imported: the point is what it IS.
    assert template == "{date}_{event-or-scene}_{people}_{seq}"
    assert names["A.PNG"] == f"2019-07-04_{TOKYO_NAME}_001.PNG"
    assert names["B.PNG"] == f"2020-08-05_{SYDNEY_NAME}_001.PNG"
    # The undated-by-GPS file is in no located event, so it carries no name at all.
    assert names["C.PNG"] == "2021-09-06_001.PNG"
    # And nothing enriched anything: the catalog holds no place fact whatever.
    assert _live_places(root) == {}


def test_only_the_place_token_carries_the_ENRICHERS_fact(tmp_path, capsys):
    """The real negative control for `{place}`, isolated from the event-label route.

    Same archive, same `{date}_{place}_{seq}` override, and the ONE difference is whether
    `pam enrich` ran. Without it the token is absent from every name and the stem is date
    and seq alone; with it the geocoder's fact is in three names out of four. That pair is
    what makes HT-M9 Leg A a test of the enricher rather than of the planner - under the
    default template the two are indistinguishable (see the test above).
    """
    root = tmp_path / "photos"
    root.mkdir()
    for salt, (name, (date, gps)) in enumerate(ARCHIVE.items()):
        _put(root, name, date, gps, salt)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    workspace_for(root).config_path.write_text(
        f'[naming]\ntemplate = "{TEMPLATE}"\n', encoding="utf-8"
    )

    assert main(["plan", "--archive", str(root)]) == 0
    capsys.readouterr()
    template, without = _planned_names(root)
    assert template == TEMPLATE
    assert without == {
        "A.PNG": "2019-07-04_001.PNG",
        "A.NEF": "2019-07-04_001.NEF",
        "B.PNG": "2020-08-05_001.PNG",
        "C.PNG": "2021-09-06_001.PNG",
    }

    assert main(["enrich", "--archive", str(root)]) == 0
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert main(["plan", "--archive", str(root)]) == 0
    capsys.readouterr()
    _, with_facts = _planned_names(root)
    assert with_facts == PLANNED
    assert set(_live_places(root).values()) == {TOKYO_NAME, SYDNEY_NAME}


def test_the_pictures_this_module_leans_on_really_decode_and_really_differ(tmp_path):
    """Two assumptions the chain would fail confusingly on, asserted directly.

    If the payload stopped decoding, `pam enrich` would exit 1 and every test above would
    fail somewhere far from the cause; if the salted copies collided on sha256 they would
    share one content-addressed fact and the per-file place assertions would weaken
    without failing.

    IT GOES THROUGH `pil_image_loader`, NOT `Image.open`. Since design 8.1 rev 17 those
    are two different questions for a RAW file - the loader hands the enricher the
    camera's embedded preview and never lets Pillow near the container - and this control
    exists to answer the one `pam enrich` actually asks.
    """
    root = tmp_path / "photos"
    root.mkdir()
    load = pil_image_loader(root)
    seen = set()
    for salt, (name, (date, gps)) in enumerate(ARCHIVE.items()):
        _put(root, name, date, gps, salt)
        with load(_record(name)) as image:
            image.load()
        seen.add(hashlib.sha256((root / name).read_bytes()).hexdigest())
    assert len(seen) == len(ARCHIVE)


def _record(name: str) -> FileRow:
    """The one catalog row `pil_image_loader` reads: the relative path and the extension."""
    return FileRow(
        id=1,
        rel_path=name,
        filename=name,
        ext=name.rsplit(".", 1)[-1].lower(),
        media_type="image",
        size=1,
        mtime_ns=1,
        sha256=None,
        status="complete",
        companion_group_id=None,
        placeholder=False,
    )


def test_the_archive_this_module_builds_has_the_three_gps_cases_it_claims(tmp_path):
    """One coordinate shared by a companion unit, one alone, one absent.

    Spelled as an assertion because every expectation in `PLANNED` depends on it, and a
    fixture that quietly stopped writing GPS would turn the whole module into the
    negative control above - passing, and testing nothing.
    """
    root = tmp_path / "photos"
    root.mkdir()
    for salt, (name, (date, gps)) in enumerate(ARCHIVE.items()):
        _put(root, name, date, gps, salt)
    run_scan(root, exiftool_command=FAKE_COMMAND)

    conn = catalog(root)
    try:
        coordinates = repo.gps_by_file_id(conn)
        rel_of = {row.id: row.rel_path for row in repo.all_files(conn).values()}
    finally:
        conn.close()
    located = {rel_of[file_id]: point for file_id, point in coordinates.items()}
    assert set(located) == {"A.PNG", "A.NEF", "B.PNG"}
    assert located["A.PNG"] == located["A.NEF"] == TOKYO
    assert located["B.PNG"] == SYDNEY


# --- M10 Task 11 Step 6: the same chain, with a MODEL in it ----------------------------
#
# WHY THE MODULE GREW RATHER THAN FORKING. This file's subject is "an inferred fact
# reaches DISK and comes off again", and M9 could only ask that of the geocoder. A caption
# is the same claim about a fact whose source is non-deterministic, arrives over a socket,
# and is the first one a user could disagree with - so it belongs beside the place chain
# and not in a second module that would re-derive the archive, the digest map and the
# review driver.
#
# THE MODEL IS THE STUB, ALWAYS (M10 plan, clarification 7). The real Ollama is installed
# on the developer's machine and is the one thing no test here may talk to; HT-M10 Leg A is
# where a real model is exercised, by a human who reads the captions.
CAPTION_TEMPLATE = "{date}_{scene}_{seq}"

# One caption per file, all four different, SCRIPTED IN WALK ORDER. `[enrich.caption]
# workers` defaults to 1, so the captioner asks the model once per file in file-id order,
# which is scan order - and the walk order is asserted below rather than assumed, so a
# platform that scanned differently fails loudly here instead of quietly pairing the wrong
# caption with the wrong photograph.
CAPTION_WALK_ORDER = ("A.NEF", "A.PNG", "B.PNG", "C.PNG")
CAPTIONS = {
    "A.NEF": "Old Camera",
    "A.PNG": "Birthday Cake",
    "B.PNG": "Harbour Bridge",
    "C.PNG": "Quiet Street",
}

# What the planner owes each file under CAPTION_TEMPLATE, written by hand from spec 6.1's
# semantics and spec 5.4's primary rule.
#
# THE COMPANION UNIT IS THE ASSERTION THAT MATTERS. `A.NEF` and `A.PNG` are one unit, spec
# 5.4 elects the display file as its primary, and spec 6.1 says a unit's scene is its
# PRIMARY's caption - so `Birthday-Cake` names both members and `Old-Camera` names NOTHING,
# even though it is a live fact in the catalog against a file that is being renamed. A
# planner that read the scene per member would put two different stems on one unit, which
# is vision S3, and it would be invisible to a fixture whose members shared a caption.
CAPTIONED = {
    "A.PNG": "2019-07-04_Birthday-Cake_001.PNG",
    "A.NEF": "2019-07-04_Birthday-Cake_001.NEF",
    "B.PNG": "2020-08-05_Harbour-Bridge_001.PNG",
    "C.PNG": "2021-09-06_Quiet-Street_001.PNG",
}


def _live_captions(root: Path) -> dict[str, str]:
    """filename -> live caption label, with the SUITE'S OWN SQL.

    Not `repo.captions_by_file_id`: that query is one of the things this chain checks, and
    an oracle built from the production symbol agrees with a broken one.
    """
    conn = catalog(root)
    try:
        rows = conn.execute(
            "SELECT f.rel_path AS rel_path, i.value_json AS value_json FROM files f"
            " JOIN inferred_facts i ON i.sha256 = f.sha256"
            " WHERE i.superseded_by IS NULL AND i.kind = 'caption'"
        ).fetchall()
    finally:
        conn.close()
    return {row["rel_path"]: json.loads(row["value_json"])["label"] for row in rows}


def test_the_m10_chain_puts_a_MODELS_words_on_disk_and_takes_them_off_again(
    tmp_path, monkeypatch, capsys, no_real_browser
):
    """scan -> enrich (caption) -> plan -> review -> apply -> undo, through the entry
    points a user types, with a model in the loop.

    What this asserts that no other test in the milestone can:

    * the model's word reaches a REAL FILENAME ON DISK - everything between the caption
      fact and the filesystem (the token, the collision search, `unit_key`, the journal,
      `perform_verified_rename`) had never seen a name a model supplied;
    * the companion unit takes ONE stem, and it is the PRIMARY's caption (vision S3);
    * `pam plan` is what makes a caption visible - I4, spec 6.3 rule 5: a plan is
      immutable with respect to later enrichment, so the user who enriches and does not
      re-plan sees nothing, which is why `pam enrich`'s `Next:` line is load-bearing;
    * the tier collapses to `medium` (clarification 10, vision 6a.3 working as designed);
    * and `pam undo` puts every byte and every name back, which is what makes an AI
      guess safe to accept in the first place.
    """
    root = tmp_path / "photos"
    root.mkdir()
    for salt, (name, (date, gps)) in enumerate(ARCHIVE.items()):
        _put(root, name, date, gps, salt)
    before = digests(root)

    # --- scan --------------------------------------------------------------------------
    scanned = run_scan(root, exiftool_command=FAKE_COMMAND)
    assert not scanned.errors, scanned.errors
    # The walk order the scripted replies below depend on, asserted rather than assumed.
    conn = catalog(root)
    try:
        walked = [
            row["rel_path"]
            for row in conn.execute(
                "SELECT rel_path FROM files WHERE sha256 IS NOT NULL ORDER BY id"
            ).fetchall()
        ]
    finally:
        conn.close()
    assert tuple(walked) == CAPTION_WALK_ORDER, walked

    with scripted(*(generate_reply(CAPTIONS[n], f"A photograph of {n}.") for n in walked)) as (
        stub
    ):
        # --- the two `pam.toml` edits, which are HT-M10 Leg A's and Leg B's ------------
        # The template is written for the same reason the place chain writes one: the
        # default is `{event-or-scene}`, where a scene and an event label are
        # indistinguishable on the screen, and this module's job is to say which command
        # produced the word on disk.
        workspace_for(root).config_path.write_text(
            f'[naming]\ntemplate = "{CAPTION_TEMPLATE}"\n\n'
            f"[enrich.caption]\nenabled = true\nollama_port = {stub.port}\n",
            encoding="utf-8",
        )

        # --- enrich, through the CLI, with the captioner switched on -------------------
        capsys.readouterr()
        assert main(["enrich", "--archive", str(root)]) == 0
        enrich_out = capsys.readouterr().out
        assert "Next: pam plan" in enrich_out
        # The model was asked once per file and no more: four photographs, four calls.
        assert stub.paths().count("/api/generate") == len(CAPTIONS)

    # Every file has its own caption, and they are the model's words rather than a
    # default the enricher could have invented.
    assert _live_captions(root) == CAPTIONS

    # --- plan, through the CLI ---------------------------------------------------------
    assert main(["plan", "--archive", str(root)]) == 0
    capsys.readouterr()
    conn = catalog(root)
    try:
        plan = open_plan(conn)
        rows = conn.execute(
            "SELECT f.rel_path AS old, p.generated_filename AS new, p.tier AS tier"
            " FROM plan_items p JOIN files f ON f.id = p.file_id WHERE p.plan_id = ?",
            (plan.id,),
        ).fetchall()
    finally:
        conn.close()
    assert plan.template == CAPTION_TEMPLATE
    assert {row["old"]: row["new"] for row in rows} == CAPTIONED
    # Clarification 10: a confidence-less fact caps its token at `medium` and spec 6.3
    # rule 4 makes the item's tier the minimum across its tokens. Every item carries a
    # scene here, so every item is `medium` - `Approve High` collapsing is this, and it is
    # designed behaviour rather than a defect.
    assert {row["tier"] for row in rows} == {"medium"}

    # --- review, through the server `pam review` runs -----------------------------------
    def drive(server):
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            listing = _request(server, "GET", f"/plan/{plan.id}/items?limit=200")
            assert {
                item["old_filename"]: item["new_filename"] for item in listing["items"]
            } == CAPTIONED
            approved = _request(
                server,
                "POST",
                f"/plan/{plan.id}/decide-filter",
                {"action": "approve", "status": "proposed"},
            )
            assert (approved["decided"], approved["excluded_low"]) == (4, 0)
        finally:
            server.shutdown()
            thread.join(timeout=20)

    summary = run_review(plan.id, root, serve=drive)
    capsys.readouterr()
    assert (summary.approved, summary.skipped, summary.remaining) == (4, 0, 0)

    # --- apply, through the CLI, with UX 4's typed confirmation ------------------------
    monkeypatch.setattr("builtins.input", lambda: "apply")
    assert main(["apply", str(plan.id), "--archive", str(root)]) == 0
    assert "4 renamed, 0 failed" in capsys.readouterr().out

    # --- THE DISK ----------------------------------------------------------------------
    assert _archive_names(root) == set(CAPTIONED.values())
    for original, renamed in CAPTIONED.items():
        assert (root / renamed).is_file(), f"{original} did not become {renamed}"
    # Vision S3: one stem across both members of the companion unit...
    assert len({Path(CAPTIONED["A.PNG"]).stem, Path(CAPTIONED["A.NEF"]).stem}) == 1
    # ... and it is the PRIMARY's caption. The RAW member's own caption is a live fact in
    # the catalog and names nothing on disk, which is spec 6.1's "the unit's scene is its
    # primary's" - and the assertion that would fail if the planner read it per member.
    assert CAPTIONS["A.NEF"] == "Old Camera"
    assert not any("Old-Camera" in name for name in _archive_names(root))
    # Not one byte moved. `--bytes`, in the shape `tools/ht_manifest.py` uses.
    assert set(digests(root)) == set(before)

    assert_verify_agrees(root, where="after the captioned T1 chain")

    # --- undo, through the CLI ---------------------------------------------------------
    assert main(["undo", _session_id(root), "--archive", str(root)]) == 0
    assert "4 put back, 0 failed" in capsys.readouterr().out
    # `--exact`: every file back under its original name, byte for byte.
    assert digests(root) == before
    assert_verify_agrees(root, where="after undoing the captioned T1 chain")
    # The captions outlive the undo: `inferred_facts` is content-addressed and nothing
    # about putting a name back touches it. A user who undoes has not lost the model run.
    assert _live_captions(root) == CAPTIONS


def test_a_plan_made_BEFORE_the_captions_does_not_acquire_them(tmp_path, capsys):
    """INVARIANT I4, and the reason `pam enrich`'s `Next:` line is load-bearing.

    Spec 6.3 rule 5: a plan is immutable with respect to later enrichment. So the owner
    who plans, then enriches, then opens the review UI sees the names the plan was built
    with and NOT the captions - and the only way the model's words reach a name is a new
    `pam plan`. This is the state HT-M10 Leg B walks into, and a chain that always
    enriched first could not tell the two orders apart.
    """
    root = tmp_path / "photos"
    root.mkdir()
    for salt, (name, (date, gps)) in enumerate(ARCHIVE.items()):
        _put(root, name, date, gps, salt)
    run_scan(root, exiftool_command=FAKE_COMMAND)

    with scripted(
        *(generate_reply(CAPTIONS[n], f"A photograph of {n}.") for n in CAPTION_WALK_ORDER)
    ) as stub:
        workspace_for(root).config_path.write_text(
            f'[naming]\ntemplate = "{CAPTION_TEMPLATE}"\n\n'
            f"[enrich.caption]\nenabled = true\nollama_port = {stub.port}\n",
            encoding="utf-8",
        )
        # PLAN FIRST, with no caption in the catalog at all.
        assert main(["plan", "--archive", str(root)]) == 0
        capsys.readouterr()
        _, before_enrich = _planned_names(root)
        assert main(["enrich", "caption", "--archive", str(root)]) == 0
        capsys.readouterr()

    # No scene token anywhere: the template asked for one and no fact existed.
    assert before_enrich == {
        "A.PNG": "2019-07-04_001.PNG",
        "A.NEF": "2019-07-04_001.NEF",
        "B.PNG": "2020-08-05_001.PNG",
        "C.PNG": "2021-09-06_001.PNG",
    }
    # The facts are now in the catalog, and the OPEN PLAN still holds the old names.
    assert _live_captions(root) == CAPTIONS
    _, unchanged = _planned_names(root)
    assert unchanged == before_enrich, (
        "an existing plan changed its proposed names when enrichment landed; spec 6.3"
        " rule 5 says a plan is immutable with respect to later facts"
    )

    # A new `pam plan` is the route, and it is the one `pam enrich`'s closing block names.
    assert main(["plan", "--archive", str(root)]) == 0
    capsys.readouterr()
    _, after_replan = _planned_names(root)
    assert after_replan == CAPTIONED
