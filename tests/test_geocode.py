"""M9 Task 9: design 8.1's `geocode`, design 8.3's event labels, and migration 0006.

THE THING THIS FILE IS REALLY ABOUT IS `mode=1`. `reverse_geocoder.search()` defaults to
`mode=2`, which fans out over `multiprocessing`; driven from a library module on Windows
the children die in bootstrap and `search()` returns THE DATASET'S FIRST ROW for every
coordinate, with exit code 0 and nothing raised - London and San Francisco both come back
as *El Tarter, Andorra*. `tests/test_deps.py` guards the source with an AST walk and a
planted-source control; this file holds the BEHAVIOURAL half, because a guard over source
text cannot tell that the answers are right.

BUT READ THE NEXT SENTENCE BEFORE YOU DELETE THAT AST GUARD. This docstring used to end
the paragraph above by saying that four cities resolving correctly is "what makes a green
AST guard mean something". The Task 9 review MEASURED that claim and it is FALSE (M1279).
With `mode=1` removed from every call site - so the singleton is built at the library
default `mode=2` - London, San Francisco, Sydney and Tokyo all still resolved CORRECTLY,
every end-to-end name matched, and the only observable cost was +24 s of `multiprocessing`
fan-out. **The `El Tarter` failure does not reproduce under pytest**, whose own main module
imports cleanly on spawn; it was reproduced twice, independently, driven from a library
module the way `pam plan` drives it.

So the behavioural tests below are NOT the thing standing behind `mode=1`. **The AST guard
in `tests/test_deps.py` is the entire protection**, and a reader who deletes it because
"the behaviour is covered anyway" ships a geocoder that writes the dataset's first row into
filenames. This paragraph exists because the previous one would have talked that reader
into it - the seventh-and-counting instance on this project of a docstring asserting
something no test checks, and one of the few where the sentence was not merely untested but
wrong.

WHAT IS SPELLED FROM THE SPEC AND NOT FROM THE CODE. Every tier below is written from
design 6.1 rev 15 ("`high` for a user-set label, the geocoder's tier otherwise") and
design 8.1's confidence-less rule, never read off `EVENT_LABEL_SOURCE_TIER`; the fact
kind is the literal `"place"`, from design 6.1's token and design 6.4's validation rule,
never `repo.fact_kinds("geocode")`; and the centroid cases are worked out by hand.

WHAT NO TEST HERE CAN PROVE. That the shipped `rg_cities1000.csv` names the maintainer's own
places correctly - an archive of real files's photographs are not in this suite.
HT-M9 is the only thing that reaches that.
"""

import builtins
import json
import logging
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from archives import FAKE_COMMAND

from pam.catalog import repo
from pam.catalog.db import connect, migrate
from pam.enrich import run_enricher
from pam.enrich.base import FileRecord, pil_image_loader
from pam.enrich.geocode import (
    COST_ESTIMATE_MS,
    MODEL_NAME,
    MODEL_VERSION,
    NAME,
    GeocodeEnricher,
    GeocodeUnavailableError,
    event_labels,
    place_names,
)
from pam.enrich.validate import fact_error
from pam.events.grouping import Event, gps_centroid, group_events
from pam.naming.planner import run_plan
from pam.naming.tokens import (
    EVENT_LABEL_SOURCE_TIER,
    UNKNOWN_LABEL_SOURCE_TIER,
    resolve_tokens,
)
from pam.paths import workspace_for
from pam.scan.runner import run_scan

NOW = "2026-08-28T10:00:00"

# Coordinates and the names design 1's offline dataset gives them. Four continents, and
# the pair at the top is the `mode=2` trap: under the default mode BOTH of these came
# back as `El Tarter`, measured twice on this machine.
LONDON = (51.5074, -0.1278)
SAN_FRANCISCO = (37.7749, -122.4194)
SYDNEY = (-33.8688, 151.2093)
TOKYO = (35.6762, 139.6503)
EXPECTED_PLACES = {
    LONDON: "London",
    SAN_FRANCISCO: "San Francisco",
    SYDNEY: "Sydney",
    TOKYO: "Tokyo",
}

# The dataset's first row, which is what `mode=2` returns for everything. Named here so
# the negative assertion below says what it is refusing rather than "not this string".
MODE_2_GARBAGE = "El Tarter"


def record(
    file_id: int,
    rel_path: str = "a.jpg",
    *,
    media_type: str = "image",
    placeholder: bool = False,
) -> FileRecord:
    """A `FileRecord` for `wants()`. This enricher reads only its `id`."""
    return FileRecord(
        id=file_id,
        rel_path=rel_path,
        filename=rel_path.rsplit("/", 1)[-1],
        ext=rel_path.rsplit(".", 1)[-1].lower(),
        media_type=media_type,
        size=1024,
        mtime_ns=1,
        sha256=f"sha-{rel_path}",
        status="present",
        placeholder=placeholder,
        companion_group_id=None,
    )


def exploding_loader(file):
    """The `PILImageLoader` for an enricher that must never decode anything.

    It raises rather than returning a stub. `geocode` serves cloud placeholders on
    purpose - it reads the `capture` row the scanner already wrote - and that is only
    safe while nothing here opens the file, because opening one hydrates it (vision R2).
    A refusal that read the bytes and discarded them would be invisible to any assertion
    about the ANSWER, which is M8's M697 exactly, so the loader is the watch.
    """
    raise AssertionError(f"geocode decodes no pixels; something opened {file.rel_path}")


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "catalog.db")
    migrate(conn)
    yield conn
    conn.close()


def _conn(root):
    conn = connect(workspace_for(root).db_path)
    conn.row_factory = sqlite3.Row
    return conn


def event_rows(root):
    """[(id, label, label_source)] in id order - the suite's own SQL, never `repo`."""
    conn = _conn(root)
    try:
        return [
            (int(r["id"]), r["label"], r["label_source"])
            for r in conn.execute("SELECT id, label, label_source FROM events ORDER BY id")
        ]
    finally:
        conn.close()


def stored_names(root, plan_id):
    conn = _conn(root)
    try:
        return {
            str(r["old_rel_path"]): str(r["generated_filename"])
            for r in conn.execute(
                "SELECT old_rel_path, generated_filename FROM plan_items WHERE plan_id = ?",
                (plan_id,),
            )
        }
    finally:
        conn.close()


def scanned(tmp_path, files):
    """An archive of `{name: tags dict}`, scanned with the fake exiftool."""
    root = tmp_path / "photos"
    root.mkdir()
    for name, tags in files.items():
        (root / name).write_bytes(f"the only copy of {name}".encode())
        (root / f"{name}.tags.json").write_text(json.dumps(tags), encoding="utf-8")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    return root


def tags(date: str, coordinate: tuple[float, float] | None = None) -> dict:
    out: dict[str, object] = {"DateTimeOriginal": date}
    if coordinate is not None:
        lat, lon = coordinate
        out["GPSLatitude"] = lat
        out["GPSLongitude"] = lon
    return out


# --- 1. mode=1, and the answers being RIGHT -------------------------------------------


def test_four_coordinates_on_four_continents_resolve_to_four_named_cities():
    """The behavioural half of `tests/test_deps.py`'s AST guard on `mode`.

    The AST guard reads source text and cannot know what the calls return. Under the
    default `mode=2` every one of these comes back as the dataset's first row with no
    exception raised, so "four different correct names" is the assertion that catches
    the failure the guard exists to prevent.
    """
    coordinates = [LONDON, SAN_FRANCISCO, SYDNEY, TOKYO]
    names = place_names(coordinates)
    assert names == [EXPECTED_PLACES[c] for c in coordinates]
    assert len(set(names)) == 4, "mode=2 returns the same first row for every coordinate"
    assert MODE_2_GARBAGE not in names


def test_one_coordinate_at_a_time_gives_the_same_answer_as_the_batch():
    """`enrich()` queries one point and the event labeller queries many; one code path.

    A batch that silently reordered or a singleton that took a different branch would
    both be a place name attached to the wrong photograph, which is the failure mode
    this product cannot have.
    """
    batch = place_names([SYDNEY, LONDON])
    assert [place_names([SYDNEY])[0], place_names([LONDON])[0]] == batch


def test_no_coordinates_asks_the_geocoder_nothing():
    assert place_names([]) == []


# --- 2. the interface design 8.1 declares ---------------------------------------------


def test_the_enricher_carries_the_identity_the_runners_cache_is_keyed_on():
    enricher = GeocodeEnricher({})
    assert (enricher.name, enricher.model_name, enricher.model_version) == (
        NAME,
        MODEL_NAME,
        MODEL_VERSION,
    )
    assert enricher.name == "geocode", "spec 8.1 names the five enrichers"
    assert enricher.cost_estimate_ms() == COST_ESTIMATE_MS


def test_available_is_true_here_and_says_what_it_checked():
    ok, reason = GeocodeEnricher({}).available()
    assert ok is True
    assert "no network" in reason


def test_available_names_the_DISTRIBUTION_when_the_package_is_missing(monkeypatch):
    """A user who has never heard of `reverse_geocoder` still needs to know what to
    install, so the message is the distribution name and not an ImportError's text."""
    import pam.enrich.geocode as geocode_mod

    monkeypatch.setattr(geocode_mod, "_warmed", False)
    monkeypatch.setattr(
        geocode_mod.importlib,
        "import_module",
        lambda name: (_ for _ in ()).throw(ImportError("no module named that")),
    )
    ok, reason = GeocodeEnricher({}).available()
    assert ok is False
    assert "reverse_geocoder is not installed" in reason
    assert "pam doctor" in reason


def test_available_is_FALSE_when_the_offline_dataset_is_gone(monkeypatch):
    """Vision S4, and it is the one configuration in which pam would reach the network.

    With the CSV missing, `reverse_geocoder` DOWNLOADS it from `download.geonames.org`
    over plain HTTP. `pam doctor` already prints this as a red line (M9 Task 6); the
    enricher has to refuse as well, because a run that started anyway would make the
    request before anybody read the doctor's output.
    """
    import pam.enrich.geocode as geocode_mod

    monkeypatch.setattr(geocode_mod, "_warmed", False)
    monkeypatch.setattr(geocode_mod, "_dataset_present", lambda: False)
    ok, reason = GeocodeEnricher({}).available()
    assert ok is False
    assert "dataset is missing" in reason and "network" in reason


def test_a_missing_dataset_raises_a_NAMED_error_rather_than_reaching_the_network(
    monkeypatch,
):
    import pam.enrich.geocode as geocode_mod

    monkeypatch.setattr(geocode_mod, "_warmed", False)
    monkeypatch.setattr(geocode_mod, "_dataset_present", lambda: False)
    with pytest.raises(GeocodeUnavailableError, match="dataset is missing"):
        place_names([LONDON])


def test_the_index_BUILD_happens_under_the_load_lock(monkeypatch):
    """M9 TASK 9 REVIEW, FINDING 3. The lock was asserted in a docstring and by nothing else.

    `_geocoder()` builds a 7.8 MB index the first time it is called, and the runner calls
    `enrich()` from a `ThreadPoolExecutor` - four threads racing into that build would each
    pay for it. M1282 deleted `with _LOAD_LOCK:` and all 2520 tests stayed green, because
    every thread still gets a CORRECT answer and no assertion about the answer can see the
    difference. So this watches the ACT: the module hands out a stub whose `search` records
    whether the lock is held at the moment the build is happening.
    """
    import pam.enrich.geocode as geocode_mod

    held: list[bool] = []

    class OneCallModule:
        def search(self, points, *, mode=None, verbose=None):
            held.append(geocode_mod._LOAD_LOCK.locked())
            return [{"name": "nowhere"}]

    monkeypatch.setattr(geocode_mod, "_warmed", False)
    monkeypatch.setattr(geocode_mod, "_dataset_present", lambda: True)
    monkeypatch.setattr(geocode_mod, "_import", lambda: OneCallModule())
    geocode_mod._geocoder()
    assert held == [True], (
        "the geocoder index was built outside `_LOAD_LOCK`, so four runner threads can"
        f" each build their own (held={held})"
    )


def test_a_FRESH_process_prints_nothing_on_stdout_when_it_builds_the_index():
    """M9 TASK 9 REVIEW, FINDING 2, and it can only be asked in a fresh process.

    `reverse_geocoder` prints "Loading formatted geocoded file..." to STDOUT while it
    builds its index, and `RGeocoder` is `@singleton`-decorated: the FIRST construction in
    a process fixes `verbose` for every later call. Inside the suite that first
    construction belongs to whichever test ran first, so
    `::test_pam_plan_prints_no_geocoder_chatter_on_stdout` is looking at a process where
    the line has already been printed and discarded. M1281 deleted `verbose=False` from the
    warm-up - the ONE call whose value is load-bearing - and all 2520 tests stayed green,
    while a real `pam plan` would print a library progress line into the user summary.

    A subprocess is the only place the question is answerable. The stderr control is not
    decoration: without it, an empty stdout would also be what a failed import produces.
    """
    probe = (
        "import sys\n"
        "from pam.enrich.geocode import place_names\n"
        "names = place_names([(51.5074, -0.1278)])\n"
        "print('RESOLVED', names, file=sys.stderr)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
        timeout=300,
        check=True,
    )
    assert "RESOLVED ['London']" in result.stderr, (
        "the probe did not build the index and query it, so an empty stdout says nothing"
        f" (stderr: {result.stderr.strip()})"
    )
    assert result.stdout == "", (
        "the geocoder printed to stdout in a fresh process, which is where `pam plan`"
        f" writes the user summary: {result.stdout.strip()!r}"
    )


# --- 3. wants(): "has GPS", and nothing else ------------------------------------------


# Design 4 media_type vocabulary, written out here rather than imported from anything
# production, so that the table below is compared against the SPEC and not against
# whatever the code happens to enumerate.
SPEC_MEDIA_TYPES = ("image", "video", "sidecar", "other")

# The answer for each, BY HAND. Every row is True and that is the point - design 8.1 rev 3
# gives geocode videos by name - but an all-True table is also the weakest possible shape,
# because narrowing it loses rows without losing a single assertion.
WANTS_BY_MEDIA_TYPE = [("image", True), ("video", True), ("sidecar", True), ("other", True)]

# M9 TASK 9 REVIEW, FINDING 4. M1301 emptied this table and the suite stayed green with a
# SECOND skip (pytest reports an empty parametrize as a skip, never a failure - M7 M743);
# M1302 narrowed it to `[("image", True)]` and the suite stayed green with NO skip at all,
# silently losing the `video` row that is this whole enricher reason to exist. A
# collection-time assert catches the emptied form; only comparing the table with an
# independently written vocabulary catches the narrowed one. Both are needed, and this is
# M9 Task 8 review FINDING 3 carried across to the file it was not applied to.
assert tuple(kind for kind, _ in WANTS_BY_MEDIA_TYPE) == SPEC_MEDIA_TYPES, (
    "the wants() table no longer covers design 4 media_type vocabulary exactly"
)


@pytest.mark.parametrize(("media_type", "wanted"), WANTS_BY_MEDIA_TYPE)
def test_wants_asks_about_GPS_and_never_about_the_media_type(media_type, wanted):
    """Spec 8.1 rev 3: "**geocode** serves videos: it reads GPS from the `capture` row,
    not pixels". No media-type test exists, and a `video` row here is the whole reason
    this enricher was separated from `phash` rather than merged with it."""
    enricher = GeocodeEnricher({7: LONDON})
    assert enricher.wants(record(7, media_type=media_type)) is wanted


def test_wants_refuses_a_file_with_no_GPS():
    assert GeocodeEnricher({7: LONDON}).wants(record(8)) is False


def test_wants_serves_a_cloud_placeholder_because_nothing_here_opens_it():
    """The opposite of `phash`'s rule, for the opposite reason. A placeholder's
    coordinates were read at scan time and are in the catalog; naming it costs no
    hydration because this enricher never touches the file (vision R2)."""
    assert GeocodeEnricher({7: LONDON}).wants(record(7, placeholder=True)) is True


def test_wants_is_answered_from_the_mapping_and_not_from_a_query(db):
    """`enrich/base.py`: an enricher gets no connection, and `wants()` is asked for every
    uncached file on every run, forever. The coordinates arrive at construction."""
    enricher = GeocodeEnricher({})
    assert enricher.wants(record(1)) is False


# --- 4. the GPS query, in the module that owns the table -------------------------------


def test_gps_by_file_id_returns_both_coordinates_or_nothing(db):
    """A half-populated `capture` row is NOT positioned. Both columns are nullable and
    the scanner writes them together, but a row with one would otherwise reach the
    geocoder as a coordinate with a silent zero in it - and the artefact is a filename."""
    for index, (lat, lon) in enumerate(
        [(51.5, -0.1), (None, -0.1), (51.5, None), (None, None)], start=1
    ):
        repo.upsert_file(
            db,
            rel_path=f"f{index}.jpg",
            filename=f"f{index}.jpg",
            ext="jpg",
            media_type="image",
            size=1,
            mtime_ns=1,
            placeholder=False,
            now=NOW,
        )
        repo.upsert_capture(
            db,
            index,
            repo.CaptureFacts(
                capture_dt="2019-07-04T00:00:00",
                capture_dt_source="exif",
                tz_offset_min=None,
                gps_lat=lat,
                gps_lon=lon,
                width=None,
                height=None,
                camera_make=None,
                camera_model=None,
                exif_json="{}",
            ),
        )
    assert repo.gps_by_file_id(db) == {1: (51.5, -0.1)}


def test_a_file_with_no_capture_row_at_all_is_absent_from_the_mapping(db):
    repo.upsert_file(
        db,
        rel_path="sidecar.xmp",
        filename="sidecar.xmp",
        ext="xmp",
        media_type="sidecar",
        size=1,
        mtime_ns=1,
        placeholder=False,
        now=NOW,
    )
    assert repo.gps_by_file_id(db) == {}


# --- 5. enrich(): one `place` fact spec 6.4 accepts ------------------------------------


def test_enrich_returns_one_well_formed_place_fact():
    """The KIND is `place`, spelled from spec 6.1's token and spec 6.4's rule.

    `fact_error` is asserted directly rather than inferred from a green run: the runner
    logs a malformed fact and carries on, so a wrong shape would show up as an archive
    with no place facts and a summary field nobody read.
    """
    facts = GeocodeEnricher({3: SYDNEY}).enrich(record(3), exploding_loader)
    assert len(facts) == 1
    fact = facts[0]
    assert fact.kind == "place"
    assert fact.value == {"place": "Sydney"}
    assert fact.confidence is None, "a table lookup is a measurement; spec 6.4 allows NULL"
    assert fact_error("place", fact.value, fact.confidence) is None


def test_enrich_never_opens_the_file_and_the_loader_proves_it():
    """The loader raises, so a green assertion here is a statement about the ACT."""
    assert GeocodeEnricher({3: SYDNEY}).enrich(record(3, placeholder=True), exploding_loader)


def test_enrich_of_a_file_with_no_coordinate_emits_NO_fact_rather_than_an_empty_one():
    """`wants()` should have refused it; if something calls `enrich` anyway, an empty
    `place` would be dropped as malformed and counted against the run. An absence is
    not malformed output."""
    assert GeocodeEnricher({}).enrich(record(3), exploding_loader) == []


def test_a_place_fact_reaches_the_catalog_through_the_RUNNER(db, tmp_path):
    """The end-to-end half of the `_FACT_KINDS` correction, and it is the reason that
    correction was a Task 9 obligation rather than a tidy-up.

    Before it, `_FACT_KINDS["geocode"]` said `("geocode",)` while the enricher emits
    `place`, and `enrich/base.py` drops a fact of a kind its enricher does not own - so
    every geocode fact would have been dropped, logged, and counted as malformed. The
    row read back here with the suite's own SQL is what says it is not.
    """
    file_id = repo.upsert_file(
        db,
        rel_path="a.jpg",
        filename="a.jpg",
        ext="jpg",
        media_type="image",
        size=1,
        mtime_ns=1,
        placeholder=False,
        now=NOW,
    )
    repo.set_sha256(db, file_id, "sha-a")
    summary = run_enricher(db, GeocodeEnricher({file_id: TOKYO}), exploding_loader, workers=1)
    assert (summary.facts_written, summary.facts_dropped) == (1, 0)
    conn = sqlite3.connect(tmp_path / "catalog.db")
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute("SELECT kind, value_json FROM inferred_facts")]
    finally:
        conn.close()
    assert rows == [{"kind": "place", "value_json": '{"place": "Tokyo"}'}]


def test_a_cloud_placeholder_geocode_WANTS_is_never_OPENED_by_the_runner_either(
    tmp_path, monkeypatch
):
    """M9 TASK 9 REVIEW, FINDING 5. Vision R2, asserted against the CALL, through the RUNNER.

    `geocode` is the FIRST enricher whose `wants()` says True to a cloud placeholder, and
    claim 2 removed phash placeholder check on a reasoning argument - "nothing in the
    module opens a file". `exploding_loader` is a real watch on that argument (M1304
    measured it firing in four tests), but it watches the only route the enricher has and
    not the one the RUNNER has: the production path hands the enricher a real
    `pil_image_loader(root)`, and nothing had ever driven a placeholder that far.

    So this drives the real runner with the real loader over an archive holding a
    placeholder geocode accepts, and records every `builtins.open` - the call
    `PIL.Image.open` makes - asserting:

      * no path under the archive root is opened while the run is happening;
      * a fact IS written for the placeholder, without which "opened nothing" would be a
        statement about an enricher that did nothing;
      * the watch records a path when one IS opened, the control M8 M697 exists for.
    """
    root = tmp_path / "archive"
    root.mkdir()
    for name in ("on_disk.jpg", "in_the_cloud.jpg"):
        (root / name).write_bytes(b"the only copy")
    conn = connect(tmp_path / "catalog.db")
    migrate(conn)
    ids = {}
    for name, placeholder in (("on_disk.jpg", False), ("in_the_cloud.jpg", True)):
        ids[name] = repo.upsert_file(
            conn,
            rel_path=name,
            filename=name,
            ext="jpg",
            media_type="image",
            size=1,
            mtime_ns=1,
            placeholder=placeholder,
            now=NOW,
        )
        repo.set_sha256(conn, ids[name], f"sha-{name}")
    enricher = GeocodeEnricher({ids["on_disk.jpg"]: LONDON, ids["in_the_cloud.jpg"]: SYDNEY})

    opened: list[str] = []
    real_open = builtins.open

    def watching_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", watching_open)
    try:
        summary = run_enricher(conn, enricher, pil_image_loader(root), workers=1)
        during_the_run = list(opened)
        with open(root / "on_disk.jpg", "rb"):  # the control, under the same wrapper
            pass
    finally:
        monkeypatch.setattr(builtins, "open", real_open)

    assert str(root / "on_disk.jpg") in opened, (
        "the watch recorded nothing for a file that WAS opened - it cannot see, so its"
        " silence about the placeholder means nothing"
    )
    inside = [path for path in during_the_run if str(root) in path]
    assert inside == [], (
        f"pam opened a file inside the archive while geocoding: {inside}. Reading a cloud"
        " placeholder hydrates it (vision R2), and geocode reads the capture row instead"
    )
    assert (summary.enriched, summary.facts_written) == (2, 2), (
        "the placeholder was not enriched, so opening nothing says nothing about R2"
    )
    conn.close()


def test_the_enricher_covers_what_pam_status_counts(db):
    """`pam status`' coverage is counted over `_FACT_KINDS`, so a kind the map does not
    name counts towards nothing. Both sides are compared with the literal `"place"`."""
    assert repo.fact_kinds("geocode") == ("place",)


# --- 6. the centroid (spec 8.3) --------------------------------------------------------


def test_a_single_point_is_its_own_centroid():
    lat, lon = gps_centroid([SYDNEY])
    assert (round(lat, 6), round(lon, 6)) == SYDNEY


def test_no_points_have_no_centroid():
    assert gps_centroid([]) is None


def test_the_centroid_of_two_points_lies_between_them():
    lat, lon = gps_centroid([(0.0, 0.0), (0.0, 10.0)])
    assert (round(lat, 6), round(lon, 6)) == (0.0, 5.0)


def test_the_centroid_does_NOT_cross_the_planet_at_the_ANTIMERIDIAN():
    """The bug an arithmetic mean of longitudes has, written out as a case.

    +179.9 and -179.9 are 0.2 degrees apart across the date line. Their arithmetic mean
    is 0 - the Gulf of Guinea, a third of the way round the world - and the geocoder
    would name it. The mean of the unit vectors puts it back on the date line where it
    belongs. An archive of real files reaches this in Fiji, Kiribati and the Aleutians.
    """
    lat, lon = gps_centroid([(1.0, 179.9), (1.0, -179.9)])
    assert abs(abs(lon) - 180.0) < 1e-6, f"{lon} is not on the date line"
    # Slightly POLEWARD of both inputs, and correctly so: the mean of two unit vectors
    # is the chord's midpoint, which sits inside the sphere, and normalising it back out
    # moves it away from the equator. 1.5e-6 degrees is ~17 cm. Asserted with a
    # tolerance rather than rounded to nothing, so the property stays visible.
    assert 1.0 <= lat < 1.0001


def test_two_antipodal_points_have_NO_centroid_rather_than_a_guess():
    """Their unit vectors sum to zero and the direction is undefined. Vision principle
    5: never guess. No centroid means no label, and spec 6.1 already says an event with
    an empty label emits no token at all."""
    assert gps_centroid([(0.0, 0.0), (0.0, 180.0)]) is None


def test_group_events_puts_the_centroid_of_its_GPS_BEARING_members_on_the_event():
    """A member with no GPS neither contributes to the centroid nor blocks it - the same
    reading of "absence of evidence is not evidence" that spec 8.3 gives the GPS jump."""
    from pam.catalog.repo import DatedFile

    files = [
        DatedFile(file_id=1, capture_dt="2019-07-04T08:00:00", gps_lat=0.0, gps_lon=0.0),
        DatedFile(file_id=2, capture_dt="2019-07-04T09:00:00", gps_lat=None, gps_lon=None),
        DatedFile(file_id=3, capture_dt="2019-07-04T10:00:00", gps_lat=0.0, gps_lon=10.0),
    ]
    (event,) = group_events(files, gap_hours=4.0, gps_km=5000.0)
    assert event.file_ids == (1, 2, 3)
    lat, lon = event.centroid
    assert (round(lat, 6), round(lon, 6)) == (0.0, 5.0)


def test_an_event_with_no_GPS_at_all_has_no_centroid():
    from pam.catalog.repo import DatedFile

    (event,) = group_events(
        [DatedFile(file_id=1, capture_dt="2019-07-04T08:00:00", gps_lat=None, gps_lon=None)],
        gap_hours=4.0,
        gps_km=50.0,
    )
    assert event.centroid is None


# --- 7. event_labels: three-valued, and the third value is the point -------------------


def test_event_labels_names_the_centroids_it_is_given():
    assert event_labels([SAN_FRANCISCO, None, SYDNEY]) == ["San Francisco", None, "Sydney"]


def test_event_labels_of_nothing_positioned_is_a_list_of_Nones_and_asks_nothing():
    assert event_labels([None, None]) == [None, None]


def test_event_labels_returns_NONE_WHOLESALE_when_the_geocoder_is_unavailable(monkeypatch):
    """The distinction `replace_events` reads: `None` means "this run learned nothing",
    a list means "these are the labels". Conflating them would have a machine without
    `reverse_geocoder` ERASE the labels a machine with it wrote."""
    import pam.enrich.geocode as geocode_mod

    monkeypatch.setattr(geocode_mod, "_warmed", False)
    monkeypatch.setattr(geocode_mod, "_dataset_present", lambda: False)
    log = logging.getLogger("pam-test-geocode")
    assert event_labels([SYDNEY], logger=log) is None


# --- 8. the catalog side: labels, and the id that must not churn ------------------------


def stored_events(conn):
    """[(id, label, label_source)] with the SUITE's own SQL - never `repo.event_membership`,
    which is the query these tests are checking."""
    return [
        (int(r["id"]), r["label"], r["label_source"])
        for r in conn.execute("SELECT id, label, label_source FROM events ORDER BY id")
    ]


def one_event(file_ids=(1,), start="2019-07-04T08:00:00", end="2019-07-04T09:00:00"):
    return Event(file_ids=tuple(file_ids), start_dt=start, end_dt=end)


def seed_file(conn, file_id):
    repo.upsert_file(
        conn,
        rel_path=f"f{file_id}.jpg",
        filename=f"f{file_id}.jpg",
        ext="jpg",
        media_type="image",
        size=1,
        mtime_ns=1,
        placeholder=False,
        now=NOW,
    )


def test_a_new_events_row_records_the_label_and_its_source_together(db):
    seed_file(db, 1)
    repo.replace_events(db, [one_event()], gap_hours=4.0, gps_km=50.0, labels=["Sydney"])
    assert stored_events(db) == [(1, "Sydney", "geocode")]


def test_a_new_events_row_with_no_label_records_no_source_either(db):
    seed_file(db, 1)
    repo.replace_events(db, [one_event()], gap_hours=4.0, gps_km=50.0, labels=[None])
    assert stored_events(db) == [(1, None, None)]


def test_a_REUSED_row_that_GAINS_a_label_KEEPS_ITS_ID(db):
    """Spec 8.3's reuse rule, and Task 9 Step 6. Every `{event}` provenance pointer in a
    terminal plan is `(source_table='events', source_id=<id>)` (spec 6.1 rev 14); if
    labelling re-inserted the row instead of updating it, the first plan run after the
    geocoder was installed would retire every one of those pointers at once."""
    seed_file(db, 1)
    (first,) = repo.replace_events(db, [one_event()], gap_hours=4.0, gps_km=50.0)
    assert stored_events(db) == [(first, None, None)]
    (second,) = repo.replace_events(
        db, [one_event()], gap_hours=4.0, gps_km=50.0, labels=["Sydney"]
    )
    assert second == first, "the row was re-inserted instead of updated"
    assert stored_events(db) == [(first, "Sydney", "geocode")]


def test_a_reused_row_whose_event_LOST_its_GPS_has_its_stale_label_CLEARED(db):
    seed_file(db, 1)
    (first,) = repo.replace_events(
        db, [one_event()], gap_hours=4.0, gps_km=50.0, labels=["Sydney"]
    )
    repo.replace_events(db, [one_event()], gap_hours=4.0, gps_km=50.0, labels=[None])
    assert stored_events(db) == [(first, None, None)]


def test_a_USER_label_survives_a_re_plan_that_geocodes_something_else(db):
    """A person's own label outranks a geocode - spec 6.1 rev 15 gives it `high` and the
    geocode `medium` - and a re-plan is not a thing that should undo it."""
    seed_file(db, 1)
    (first,) = repo.replace_events(db, [one_event()], gap_hours=4.0, gps_km=50.0)
    db.execute(
        "UPDATE events SET label = 'Grandad''s farm', label_source = 'user' WHERE id = ?",
        (first,),
    )
    repo.replace_events(db, [one_event()], gap_hours=4.0, gps_km=50.0, labels=["Sydney"])
    assert stored_events(db) == [(first, "Grandad's farm", "user")]


def test_labels_of_NONE_leaves_every_existing_label_exactly_where_it_was(db):
    """The machine without `reverse_geocoder` case. `pam plan` still groups and still
    plans; it simply says nothing about labels."""
    seed_file(db, 1)
    (first,) = repo.replace_events(
        db, [one_event()], gap_hours=4.0, gps_km=50.0, labels=["Sydney"]
    )
    repo.replace_events(db, [one_event()], gap_hours=4.0, gps_km=50.0, labels=None)
    assert stored_events(db) == [(first, "Sydney", "geocode")]


def test_a_labels_list_of_the_wrong_length_is_refused_rather_than_zipped(db):
    """`zip` would silently drop the tail, which is a label attached to the wrong event."""
    seed_file(db, 1)
    with pytest.raises(ValueError, match="2 labels for 1 events"):
        repo.replace_events(db, [one_event()], gap_hours=4.0, gps_km=50.0, labels=["a", "b"])


def test_event_membership_carries_the_label_source_the_tier_is_derived_from(db):
    seed_file(db, 1)
    repo.replace_events(db, [one_event()], gap_hours=4.0, gps_km=50.0, labels=["Sydney"])
    membership = repo.event_membership(db)
    assert membership[1].label == "Sydney"
    assert membership[1].label_source == "geocode"


def test_the_catalogs_vocabulary_and_the_naming_layers_tier_map_cover_the_same_sources(db):
    """Two artefacts, both pinned to the SPEC's two words rather than to each other.

    `repo.LABEL_SOURCES` is what may be stored; `EVENT_LABEL_SOURCE_TIER` is spec 6.1
    rev 15's derivation. A source the catalog can hold and the naming layer cannot rate
    would fall to `UNKNOWN_LABEL_SOURCE_TIER` silently, which is the shape of an
    unnoticed downgrade.
    """
    assert set(repo.LABEL_SOURCES) == {"geocode", "user"}
    assert set(EVENT_LABEL_SOURCE_TIER) == {"geocode", "user"}


# --- 9. spec 6.1 rev 15: {event}'s tier derives from label_source -----------------------

# Spelled from the spec, not from the map: rev 15 says "`high` for a user-set label, the
# geocoder's tier otherwise", and spec 8.1's rule for a confidence-less enricher is the
# config default, `medium`. `geocode` stores NULL confidence.
SPEC_EVENT_TIERS = [("user", "high"), ("geocode", "medium")]


def plan_file(**kw):
    """A `PlanFile` for `resolve_tokens`. Only the event argument matters to these tests."""
    base = dict(
        id=1,
        rel_path="a.jpg",
        filename="a.jpg",
        ext="jpg",
        ext_as_written="jpg",
        media_type="image",
        placeholder=False,
        companion_group_id=None,
        capture_dt="2019-07-04T18:23:11",
        capture_dt_source="exif",
        has_capture=True,
    )
    base.update(kw)
    return repo.PlanFile(**base)


@pytest.mark.parametrize(("source", "tier"), SPEC_EVENT_TIERS)
def test_the_event_tokens_tier_is_derived_from_the_labels_source(source, tier):
    facts = resolve_tokens(
        plan_file(),
        seq=1,
        event=repo.EventMembership(event_id=5, label="Sydney", label_source=source),
        place=None,
        scene=None,
        caption_tier="medium",
        people=(),
        max_people=3,
        date=None,
    )
    assert facts["event"].tier == tier


def test_the_two_sources_really_do_rate_DIFFERENTLY():
    """The negative control on the table above. If both rows said `high` - which is what
    M8's literal did - every parametrised case would still pass and A8's obligation would
    be undischarged with a green suite."""
    assert len({tier for _, tier in SPEC_EVENT_TIERS}) == 2
    assert len(SPEC_EVENT_TIERS) == 2


def test_a_label_whose_source_the_catalog_cannot_name_is_rated_LOW():
    """Unreachable through pam - both writers set the column with the label, and
    migration 0006 backfilled the rest - so this is the corrupted-catalog case. `low`
    means "needs review" and keeps the name out of M7's tier-gated bulk approval."""
    facts = resolve_tokens(
        plan_file(),
        seq=1,
        event=repo.EventMembership(event_id=5, label="Sydney", label_source="martian"),
        place=None,
        scene=None,
        caption_tier="medium",
        people=(),
        max_people=3,
        date=None,
    )
    assert facts["event"].tier == UNKNOWN_LABEL_SOURCE_TIER == "low"


# --- 10. end to end: a real coordinate becomes a real filename --------------------------


def test_pam_plan_geocodes_an_events_centroid_into_the_proposed_name(tmp_path):
    """Spec 8.3's whole sentence, driven through the real command.

    Two photographs four minutes apart in San Francisco are one event; its centroid is
    geocoded and the label reaches the filename through the default template's
    `{event-or-scene}`. Nothing here reads the label back out of the catalog and then
    agrees with it - `San-Francisco` is written out, sanitized the way spec 6.2 says a
    space is.
    """
    root = scanned(
        tmp_path,
        {
            "IMG_1.JPG": tags("2019:07:04 18:23:11", SAN_FRANCISCO),
            "IMG_2.JPG": tags("2019:07:04 18:27:11", SAN_FRANCISCO),
        },
    )
    summary = run_plan(root)
    assert event_rows(root) == [(1, "San Francisco", "geocode")]
    assert stored_names(root, summary.plan_id) == {
        "IMG_1.JPG": "2019-07-04_San-Francisco_001.JPG",
        "IMG_2.JPG": "2019-07-04_San-Francisco_002.JPG",
    }


def test_two_events_on_two_continents_get_two_different_labels(tmp_path):
    """The `mode=2` failure, expressed as a plan: under it both events would be labelled
    `El Tarter` and two unrelated trips would be named the same thing."""
    root = scanned(
        tmp_path,
        {
            "IMG_1.JPG": tags("2019:07:04 18:23:11", SAN_FRANCISCO),
            "IMG_2.JPG": tags("2019:08:11 07:30:00", SYDNEY),
        },
    )
    summary = run_plan(root)
    assert [label for _, label, _ in event_rows(root)] == ["San Francisco", "Sydney"]
    assert set(stored_names(root, summary.plan_id).values()) == {
        "2019-07-04_San-Francisco_001.JPG",
        "2019-08-11_Sydney_001.JPG",
    }


def test_an_event_with_no_GPS_is_planned_with_no_event_token_at_all(tmp_path):
    """Spec 6.1: "an event with an empty label emits no token at all" - the graceful
    degradation, still the normal case for an archive whose camera had no GPS."""
    root = scanned(tmp_path, {"IMG_1.JPG": tags("2019:07:04 18:23:11")})
    summary = run_plan(root)
    assert event_rows(root) == [(1, None, None)]
    assert stored_names(root, summary.plan_id) == {"IMG_1.JPG": "2019-07-04_001.JPG"}


def test_a_re_plan_over_unchanged_facts_neither_churns_the_id_nor_the_label(tmp_path):
    """Step 6 again, this time through the command rather than through the repo."""
    root = scanned(tmp_path, {"IMG_1.JPG": tags("2019:07:04 18:23:11", SYDNEY)})
    run_plan(root)
    before = event_rows(root)
    run_plan(root)
    assert event_rows(root) == before == [(1, "Sydney", "geocode")]


def test_pam_plan_prints_no_geocoder_chatter_on_stdout(tmp_path, capsys):
    """`reverse_geocoder` prints "Loading formatted geocoded file..." to STDOUT when it
    builds its index, and `pam plan`'s stdout is the user's summary. `verbose=False` is
    what keeps a library's progress message out of the product's output; because
    `search` is `@singleton`-decorated, the FIRST call in the process fixes it for every
    later one, which is why `_geocoder()` makes that first call itself."""
    root = scanned(tmp_path, {"IMG_1.JPG": tags("2019:07:04 18:23:11", TOKYO)})
    capsys.readouterr()
    run_plan(root)
    assert "Loading formatted geocoded file" not in capsys.readouterr().out


# --- 11. the deferred import, proved in a fresh interpreter -----------------------------


def test_importing_this_module_does_not_import_reverse_geocoder_or_scipy():
    """The deferral, for THIS file by name.

    `tests/test_deps.py::EAGER_IMPORT_CLOSURE` is the gate for the package as a whole
    and would also catch this, but it reports "the package got heavier" and leaves the
    reader to find which of seventy modules did it. Measured at Task 9: `pam --version`
    costs ~0.18 s and `import reverse_geocoder` adds 0.32 s of that again, because it
    pulls `scipy` eagerly. Nothing but `pam enrich` and `pam plan`'s step 0 needs a
    place name, and neither needs one at import time.
    """
    probe = (
        "import sys, pam.enrich.geocode;"
        "print(' '.join(n for n in ('reverse_geocoder','scipy','numpy') if n in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
        timeout=120,
        check=True,
    )
    assert result.stdout.split() == [], (
        "pam/enrich/geocode.py imports the geocoder at module scope; every pam command"
        f" now pays for it. Resident: {result.stdout.strip()}"
    )
