"""M9 Task 10: spec 6.1's `{place}` token, and spec 6.2 rule 7's second half.

`{place}` IS THE FIRST DROPPABLE TOKEN IN THIS PROJECT'S HISTORY. Through M8
`resolve_tokens` supplied `date`, `seq`, `orig` and (from Task 9) `event` - and rule 5's
trim order is `{scene}`, `{place}`, `{people}`, none of which a planner could emit. So
the trim order could not fire for any user's archive, `StemResult.dropped` was deleted as
structurally unreachable, and the obligation moved to spec 6.2 rule 7, which M9 owes.

The trim order itself is exercised in `tests/test_template.py`, against the assembler and
against the SPEC's spelling of the order. This file covers the half the assembler cannot
see: where the place VALUE comes from, what tier it carries, and that the drop reaches a
user through `trust_reasons_json` with no penalty and no cap attached to it.
"""

import json
import sqlite3

import pytest
from archives import FAKE_COMMAND

from pam.catalog import repo
from pam.catalog.db import connect
from pam.config import TierBounds
from pam.enrich.geocode import GeocodeEnricher
from pam.naming.planner import run_plan
from pam.naming.template import DROPPED_NOTE, TOKEN_LABELS
from pam.naming.tokens import PLACE_TIER, resolve_tokens
from pam.paths import workspace_for
from pam.scan.runner import run_scan

# --------------------------------------------------------------------------- fixtures

FAKE_SHA = "0" * 64
GENERATED_AT = "2026-08-28T09:00:00"


def build(tmp_path, files):
    """An archive of `{filename: exif date or None}`, ready to scan."""
    root = tmp_path / "photos"
    root.mkdir()
    for name, date in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(f"the only copy of {name}".encode())
        tags = {} if date is None else {"DateTimeOriginal": date}
        (root / f"{name}.tags.json").write_text(json.dumps(tags), encoding="utf-8")
    return root


def scanned(tmp_path, files):
    root = build(tmp_path, files)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    return root


def _conn(root):
    conn = connect(workspace_for(root).db_path)
    conn.row_factory = sqlite3.Row
    return conn


def sha_of(root, rel_path):
    conn = _conn(root)
    try:
        row = conn.execute(
            "SELECT sha256 FROM files WHERE rel_path = ?", (rel_path,)
        ).fetchone()
        return str(row["sha256"])
    finally:
        conn.close()


def file_id_of(root, rel_path):
    conn = _conn(root)
    try:
        row = conn.execute("SELECT id FROM files WHERE rel_path = ?", (rel_path,)).fetchone()
        return int(row["id"])
    finally:
        conn.close()


def add_fact(root, sha256, *, kind="place", value_json=None, version="1.0", dead=False):
    """Seed one `inferred_facts` row with the SUITE'S OWN SQL, not `repo.insert_fact`.

    `pam enrich` is M9 Task 12, so nothing in production writes a `place` fact yet; a
    test may write catalog state, the same way M5's tests seeded journal rows and M8's
    seeded an event label. Writing it here rather than through the production insert
    also keeps this file's oracle independent of the writer it is checking.

    `superseded_by` points at the row itself when `dead` - the column only has to be
    non-NULL for `places_by_file_id`'s filter, and self-reference keeps the FK satisfied
    without inventing a second row nothing else looks at.
    """
    body = json.dumps({"place": "Somewhere"}) if value_json is None else value_json
    conn = _conn(root)
    try:
        cur = conn.execute(
            "INSERT INTO inferred_facts"
            " (sha256, kind, value_json, confidence, model_name, model_version,"
            "  generated_at) VALUES (?, ?, ?, NULL, 'rg_cities1000', ?, ?)",
            (sha256, kind, body, version, GENERATED_AT),
        )
        fact_id = int(cur.lastrowid)
        if dead:
            conn.execute(
                "UPDATE inferred_facts SET superseded_by = ? WHERE id = ?",
                (fact_id, fact_id),
            )
        conn.commit()
        return fact_id
    finally:
        conn.close()


def plan_file(**kw):
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


def stored(root, plan_id):
    """{old rel_path: row} straight out of `plan_items` - the suite's own SQL.

    What `pam apply` will rename to, and what `pam review` will show, is the stored row
    and nothing else.
    """
    conn = _conn(root)
    try:
        return {
            str(r["old_rel_path"]): r
            for r in conn.execute(
                "SELECT old_rel_path, generated_filename, tokens_json, tier,"
                " trust_score, trust_reasons_json FROM plan_items WHERE plan_id = ?",
                (plan_id,),
            )
        }
    finally:
        conn.close()


# ------------------------------------------------------- Step 1: {place} resolves


def test_the_place_token_is_the_geocoded_place_name():
    # spec 6.1: "{place}: geocode enricher token (city or named place)". Value = the
    # name; provenance = (inferred_facts, <fact id>), which is rev 6's "must reference a
    # row that exists" - the id came from the row the name came from.
    facts = resolve_tokens(
        plan_file(),
        seq=1,
        event=None,
        place=repo.PlaceFact(fact_id=77, place="San Francisco"),
        scene=None,
        caption_tier="medium",
        people=(),
        max_people=3,
        date=None,
    )
    assert facts["place"].value == "San Francisco"
    assert facts["place"].source_table == "inferred_facts"
    assert facts["place"].source_id == 77


def test_the_place_tier_is_medium_because_the_geocoder_reports_no_confidence():
    """Derived from the SPEC, not by analogy with `{event}`'s geocoded half.

    Two spec sentences, and the test pins both premises rather than only the conclusion:

    - spec 6.4 allows a NULL confidence and `enrich/geocode.py` stores one, because a
      nearest-city lookup in an offline table is a measurement with no probability
      attached;
    - spec 8.1's only rule for a confidence-less enricher is "tier by config default
      (`medium` cap for confidence-less enrichers)", which
      `config.TierBounds.max_tier_when_no_confidence` spells `medium`.
    """
    assert PLACE_TIER == "medium"
    assert TierBounds(floor=0.0, low=0.5, high=0.85).max_tier_when_no_confidence == "medium"
    (fact,) = GeocodeEnricher({1: (37.77, -122.42)}).enrich(_RECORD, image=_unused_image_loader)
    assert fact.kind == "place" and fact.confidence is None
    facts = resolve_tokens(
        plan_file(),
        seq=1,
        event=None,
        place=repo.PlaceFact(fact_id=1, place="Sydney"),
        scene=None,
        caption_tier="medium",
        people=(),
        max_people=3,
        date=None,
    )
    assert facts["place"].tier == "medium"


def _unused_image_loader():  # pragma: no cover - geocode never opens an image
    raise AssertionError("geocode must not decode pixels (spec 8.1 rev 3)")


_RECORD = repo.FileRow(
    id=1,
    rel_path="a.jpg",
    filename="a.jpg",
    ext="jpg",
    media_type="image",
    size=1,
    mtime_ns=1,
    sha256=FAKE_SHA,
    status="present",
    placeholder=False,
    companion_group_id=None,
)


def test_a_file_with_no_place_fact_emits_no_place_token():
    # Absent from the dict rather than present-and-empty: build_stem omits an absent
    # token and its separator (spec 6.1's graceful degradation), and a present-and-empty
    # fact would invite a provenance pointer for a token no filename ever carried.
    facts = resolve_tokens(
        plan_file(),
        seq=1,
        event=None,
        place=None,
        scene=None,
        caption_tier="medium",
        people=(),
        max_people=3,
        date=None,
    )
    assert "place" not in facts
    assert set(facts) == {"date", "seq", "orig"}


def test_an_empty_place_name_emits_no_place_token():
    # spec 6.4's write-time rule ("`place` tokens must be non-empty after sanitization")
    # already refuses this at the boundary, and `places_by_file_id` refuses it again;
    # this is the third reading, at the token itself.
    facts = resolve_tokens(
        plan_file(),
        seq=1,
        event=None,
        place=repo.PlaceFact(fact_id=1, place=""),
        scene=None,
        caption_tier="medium",
        people=(),
        max_people=3,
        date=None,
    )
    assert "place" not in facts


# ------------------------------------------- Step 1: the catalog reader behind the token


def test_places_by_file_id_returns_the_live_place_name_for_each_file(tmp_path):
    root = scanned(tmp_path, {"a.jpg": "2019:07:04 18:23:11"})
    fact_id = add_fact(root, sha_of(root, "a.jpg"), value_json='{"place": "Sydney"}')
    conn = _conn(root)
    try:
        places = repo.places_by_file_id(conn)
    finally:
        conn.close()
    assert places == {file_id_of(root, "a.jpg"): repo.PlaceFact(fact_id, "Sydney")}


def test_two_files_with_the_same_bytes_share_one_place_fact(tmp_path):
    # spec 4 keys `inferred_facts` by sha256, not by file id. Two identical photos are
    # two files and one fact, and both must be named.
    root = build(tmp_path, {"a.jpg": "2019:07:04 18:23:11"})
    (root / "b.jpg").write_bytes((root / "a.jpg").read_bytes())
    (root / "b.jpg.tags.json").write_bytes((root / "a.jpg.tags.json").read_bytes())
    run_scan(root, exiftool_command=FAKE_COMMAND)
    add_fact(root, sha_of(root, "a.jpg"), value_json='{"place": "Sydney"}')
    conn = _conn(root)
    try:
        places = repo.places_by_file_id(conn)
    finally:
        conn.close()
    assert {p.place for p in places.values()} == {"Sydney"}
    assert set(places) == {file_id_of(root, "a.jpg"), file_id_of(root, "b.jpg")}


def test_a_superseded_place_fact_never_names_a_file(tmp_path):
    # spec 8.1's re-enrichment supersedes the old fact and writes a new one. A filename
    # built from a retired fact is a filename built from a model the user replaced.
    root = scanned(tmp_path, {"a.jpg": "2019:07:04 18:23:11"})
    add_fact(root, sha_of(root, "a.jpg"), value_json='{"place": "Sydney"}', dead=True)
    conn = _conn(root)
    try:
        assert repo.places_by_file_id(conn) == {}
    finally:
        conn.close()


def test_a_fact_of_another_kind_is_not_a_place(tmp_path):
    root = scanned(tmp_path, {"a.jpg": "2019:07:04 18:23:11"})
    add_fact(root, sha_of(root, "a.jpg"), kind="phash", value_json='{"place": "Sydney"}')
    conn = _conn(root)
    try:
        assert repo.places_by_file_id(conn) == {}
    finally:
        conn.close()


def test_the_most_recent_of_two_live_place_facts_wins(tmp_path):
    """`inferred_facts` is UNIQUE on (sha256, kind, model_name, model_version), so two
    LIVE `place` rows for one content are reachable the moment a second dataset version
    runs without superseding the first. Spec 6.2 rule 6 requires the same inputs to
    produce the same name, and "whichever row SQLite returned first" is not a rule."""
    root = scanned(tmp_path, {"a.jpg": "2019:07:04 18:23:11"})
    sha = sha_of(root, "a.jpg")
    add_fact(root, sha, value_json='{"place": "Sydney"}', version="1.0")
    newer = add_fact(root, sha, value_json='{"place": "Melbourne"}', version="2.0")
    conn = _conn(root)
    try:
        assert repo.places_by_file_id(conn) == {
            file_id_of(root, "a.jpg"): repo.PlaceFact(newer, "Melbourne")
        }
    finally:
        conn.close()


# One row per way a hand-edited catalog can be wrong about spec 6.4's
# `{"place": <non-empty str>}`: an empty name, a non-string name, a missing key, a
# document that is not an object, and text that is not JSON at all.
#
# NAMED AT MODULE SCOPE AND PINNED BY AN EXACT LENGTH ASSERTED AT COLLECTION TIME. The
# M9 Task 10 review measured both halves of this on the decorator that used to sit
# inline: emptying the list (M1342) made pytest SKIP the test rather than fail it - M7's
# M743 exactly - and narrowing it to one entry (M1343) was completely silent, with the
# suite total the only thing that moved. A collection-time assert catches the first; only
# an EXACT length catches the second.
MALFORMED_BODIES = ['{"place": ""}', '{"place": 7}', '{"city": "Sydney"}', "[]", "not json"]
assert len(MALFORMED_BODIES) == 5, "the malformed-fact table lost or gained a shape"


@pytest.mark.parametrize("body", MALFORMED_BODIES)
def test_a_malformed_place_fact_names_nothing_rather_than_crashing_the_plan(tmp_path, body):
    # The runner validates every fact before insert (spec 6.4), so this cannot come from
    # pam - it can come from a hand-edited catalog. A name built from a half-parsed fact
    # is worse than a name with no place in it, and spec 6.1's graceful degradation is
    # the designed answer to an absent token.
    root = scanned(tmp_path, {"a.jpg": "2019:07:04 18:23:11"})
    add_fact(root, sha_of(root, "a.jpg"), value_json=body)
    conn = _conn(root)
    try:
        assert repo.places_by_file_id(conn) == {}
    finally:
        conn.close()


# --------------------------------------------- Steps 2-4: the token, and rule 7's half

TEMPLATE = "{date}_{place}_{seq}"


def test_a_place_fact_reaches_a_filename_end_to_end(tmp_path):
    root = scanned(tmp_path, {"a.jpg": "2019:07:04 18:23:11"})
    fact_id = add_fact(root, sha_of(root, "a.jpg"), value_json='{"place": "San Francisco"}')
    summary = run_plan(root, template_override=TEMPLATE)
    row = stored(root, summary.plan_id)["a.jpg"]
    # spec 6.2 rule 1: a whitespace run becomes '-'.
    assert row["generated_filename"] == "2019-07-04_San-Francisco_001.jpg"
    tokens = {t["token"]: t for t in json.loads(row["tokens_json"])}
    assert tokens["place"] == {
        "token": "place",
        "resolved_from": "place",
        "value": "San-Francisco",
        "source_table": "inferred_facts",
        "source_id": fact_id,
        "tier": "medium",
        "truncated": False,
    }
    # spec 6.3.4: the item's tier is the min across its tokens, and `medium` is now it.
    assert row["tier"] == "medium"
    # No deduction: spec 6.4's table has no row for a place token, and the enricher's
    # NULL confidence is not "confidence < high bound".
    assert row["trust_score"] == 100
    assert json.loads(row["trust_reasons_json"]) == []


def test_an_archive_with_no_place_facts_plans_exactly_as_before(tmp_path):
    root = scanned(tmp_path, {"a.jpg": "2019:07:04 18:23:11"})
    summary = run_plan(root, template_override=TEMPLATE)
    row = stored(root, summary.plan_id)["a.jpg"]
    assert row["generated_filename"] == "2019-07-04_001.jpg"
    assert row["tier"] == "high" and row["trust_score"] == 100
    assert json.loads(row["trust_reasons_json"]) == []


# A place name no path budget can hold. Nothing sanitizes it away (spec 6.2 rule 2 keeps
# ASCII letters and rule 3 collapses only repeated `-`/`_`), and 300 > the 240-character
# full-path budget on any machine, so the trim order MUST drop it.
OVERLONG_PLACE = "P" * 300


def test_a_dropped_place_is_disclosed_in_trust_reasons_and_charged_nothing(tmp_path):
    """Spec 6.2 rule 7's second half, end to end and for real.

    The stored `trust_reasons_json` is what `catalog/repo/review.py` parses into
    `ReviewItem.trust_reasons`, what `webui/api.py` passes out as `trust_reasons`, and
    what `app.js` renders in the Why? panel - which reads whatever list it is given and
    needs no change to show this sentence.

    And it is a DISCLOSURE, NOT A DEDUCTION (rule 7's closing paragraph, M8's A5): the
    score is still 100 and the tier is still `high`. Truncation is charged -10 because
    the name makes a mangled claim; a dropped token makes no claim at all.
    """
    root = scanned(tmp_path, {"a.jpg": "2019:07:04 18:23:11"})
    add_fact(root, sha_of(root, "a.jpg"), value_json=json.dumps({"place": OVERLONG_PLACE}))
    summary = run_plan(root, template_override=TEMPLATE)
    row = stored(root, summary.plan_id)["a.jpg"]
    assert row["generated_filename"] == "2019-07-04_001.jpg"
    assert json.loads(row["trust_reasons_json"]) == [
        DROPPED_NOTE.format(label=TOKEN_LABELS["place"])
    ]
    assert row["trust_score"] == 100
    assert row["tier"] == "high"
    # The token is absent from provenance too: spec 6.1 records only tokens that
    # reached the filename, so the drop is reported as a sentence and nowhere else.
    assert [t["token"] for t in json.loads(row["tokens_json"])] == ["date", "seq"]


def test_a_drop_sentence_joins_the_deduction_sentences_rather_than_replacing_them(
    tmp_path,
):
    # A file with no camera date: the scanner falls back to `file_mtime`, spec 6.4
    # charges -25 and writes its own sentence. The drop's sentence is appended to that
    # list, so the Why? panel shows both and neither channel can swallow the other.
    root = scanned(tmp_path, {"a.jpg": None})
    add_fact(root, sha_of(root, "a.jpg"), value_json=json.dumps({"place": OVERLONG_PLACE}))
    summary = run_plan(root, template_override=TEMPLATE)
    row = stored(root, summary.plan_id)["a.jpg"]
    reasons = json.loads(row["trust_reasons_json"])
    assert reasons[-1] == DROPPED_NOTE.format(label=TOKEN_LABELS["place"])
    assert len(reasons) == 2 and "modification time" in reasons[0]
    assert row["trust_score"] == 75  # -25 for the mtime date, and nothing for the drop
