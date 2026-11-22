"""M10 Task 9: spec 6.1's `{scene}` token, the trim order's FIRST entry, and spec 6.4's
caption-vs-geocode rule.

`{scene}` IS THE FIRST ENTRY IN SPEC 6.2 RULE 5's TRIM ORDER, and until this milestone
nothing could put a value in it. M9 Task 10 made the SECOND entry (`{place}`) reachable and
said so; the first stayed theoretical for one more milestone. So this file is
`tests/test_place_naming.py`'s sibling by design, and where it repeats that file's shape it
repeats it deliberately - the two tokens are different questions with the same wiring, and
the M8 lesson (M922/M924) is that two artefacts which pin each other prove they agree and
never that either is right.

WHAT IS DIFFERENT FROM `{place}`, AND IT IS ONLY EVER THESE THREE THINGS:

1. **The tier is a CONFIG READ.** `PLACE_TIER` is a literal because spec 11's `[tiers]`
   table has no `geocode` row to read; `[tiers] caption` DOES exist, so `{scene}`'s tier is
   read from it. The test that matters is not "it is medium" - it is that changing the
   config key changes the tier, which is the difference between derived and copied.
2. **`{event-or-scene}` finally has two live candidates.** The alias has existed since M3
   and has never had to choose. Both branches AND the tie are exercised here.
3. **Spec 6.4's caption-vs-geocode rule is NON-LOCAL** (rev 18) - the only rule in that
   table that reaches outside the file it judges. The non-locality itself is tested, not
   only the rule: the same caption on the same file, flagged and unflagged as the rest of
   the archive changes.

THE ORACLES ARE INDEPENDENT. Every fact is seeded with the SUITE'S OWN SQL, every expected
name and tier is a literal spelled from the spec, and the trim order is written out from
spec 6.2 rule 5's own sentence rather than read back from `template._TRIM_ORDER`.
"""

import json
import sqlite3

import pytest
from archives import FACES_FLOOR, FAKE_COMMAND
from ollama_stub import OllamaStub, generate_reply, tags_reply
from PIL import Image

from pam.catalog import repo
from pam.catalog.db import connect
from pam.config import Config, TierBounds
from pam.enrich.base import FileRecord, pil_image_loader
from pam.enrich.caption import CaptionEnricher
from pam.naming.consistency import CAPTION_PLACE_REASON, TRUST_CONSISTENCY
from pam.naming.planner import run_plan
from pam.naming.template import _TRIM_ORDER, DROPPED_NOTE, TOKEN_LABELS, build_stem
from pam.naming.tokens import CAPTION_TIER_KEY, resolve_tokens
from pam.paths import workspace_for
from pam.scan.runner import run_scan

# --------------------------------------------------------------------------- fixtures

GENERATED_AT = "2026-09-02T09:00:00"
CAPTION_MODEL = "qwen2.5vl"
GEOCODE_MODEL = "rg_cities1000"


def build(tmp_path, files):
    """An archive of `{filename: exif date or None}`, ready to scan.

    Real PNG bytes, one colour per file, so no two files share a sha256 -
    `inferred_facts` is CONTENT-addressed and byte-identical files would share one fact,
    which would make every per-file assertion below weaker than it looks.
    """
    root = tmp_path / "photos"
    root.mkdir(exist_ok=True)
    for index, (name, date) in enumerate(files.items()):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        colour = index + 1
        Image.new(
            "RGB", (24, 18), (colour % 251, (colour * 7) % 251, (colour * 13) % 251)
        ).save(root / name, format="PNG")
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


def add_fact(root, sha256, *, kind, value_json, model, version="1.0", dead=False):
    """Seed one `inferred_facts` row with the SUITE'S OWN SQL, not `repo.insert_fact`.

    `test_place_naming.py`'s helper argues this at length and the argument is unchanged:
    writing the row here rather than through the production insert keeps this file's oracle
    independent of the writer it is checking. `superseded_by` points at the row itself when
    `dead` - the column only has to be non-NULL for the live filter, and self-reference
    keeps the FK satisfied without inventing a second row nothing looks at.
    """
    conn = _conn(root)
    try:
        cur = conn.execute(
            "INSERT INTO inferred_facts"
            " (sha256, kind, value_json, confidence, model_name, model_version,"
            "  generated_at) VALUES (?, ?, ?, NULL, ?, ?, ?)",
            (sha256, kind, value_json, model, version, GENERATED_AT),
        )
        fact_id = int(cur.lastrowid)
        if dead:
            conn.execute(
                "UPDATE inferred_facts SET superseded_by = ? WHERE id = ?", (fact_id, fact_id)
            )
        conn.commit()
        return fact_id
    finally:
        conn.close()


def add_caption(root, rel_path, label, *, sentence="A photograph.", **kw):
    """One live `caption` fact. Spec 8.1: "output parsed to `{label, sentence}`"."""
    return add_fact(
        root,
        sha_of(root, rel_path),
        kind="caption",
        value_json=json.dumps({"label": label, "sentence": sentence}),
        model=CAPTION_MODEL,
        **kw,
    )


def add_place(root, rel_path, place, **kw):
    """One live `place` fact. Spec 6.4: `{"place": <non-empty str>}`."""
    return add_fact(
        root,
        sha_of(root, rel_path),
        kind="place",
        value_json=json.dumps({"place": place}),
        model=GEOCODE_MODEL,
        **kw,
    )


def write_config(root, text):
    ws = workspace_for(root)
    ws.config_path.write_text(text, encoding="utf-8")


def plan_file(**kw):
    base = dict(
        id=1,
        rel_path="a.png",
        filename="a.png",
        ext="png",
        ext_as_written="png",
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
    """{old rel_path: row} straight out of `plan_items` - the suite's own SQL."""
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


def tokens_of(row):
    return {t["token"]: t for t in json.loads(row["tokens_json"])}


# ------------------------------------------------- Step 1: {scene} resolves to the LABEL


def test_the_scene_token_is_the_caption_label_and_never_the_sentence():
    """Spec 6.1: "the enricher emits both a `label` <=3 words and a `sentence`; naming
    uses `label`". Provenance is (inferred_facts, <fact id>), which is rev 6's "must
    reference a row that exists" - the id came from the row the label came from."""
    facts = resolve_tokens(
        plan_file(),
        seq=1,
        event=None,
        place=None,
        scene=repo.CaptionFact(fact_id=88, label="Sunset Beach"),
        caption_tier="medium",
        people=(),
        max_people=3,
        date=None,
    )
    assert facts["scene"].value == "Sunset Beach"
    assert facts["scene"].source_table == "inferred_facts"
    assert facts["scene"].source_id == 88


def test_a_file_with_no_caption_fact_emits_no_scene_token():
    # Absent from the dict rather than present-and-empty, for the two reasons {event} and
    # {place} already give: the stem is the same either way but the PROVENANCE is not, and
    # one shape for "this enricher has nothing to say" rather than two.
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
    assert "scene" not in facts
    assert set(facts) == {"date", "seq", "orig"}


def test_an_empty_caption_label_emits_no_scene_token():
    # Spec 6.4 / 8.1 rev 17 already refuse an empty label at write time, and
    # `captions_by_file_id` refuses it again; this is the third reading, at the token.
    facts = resolve_tokens(
        plan_file(),
        seq=1,
        event=None,
        place=None,
        scene=repo.CaptionFact(fact_id=1, label=""),
        caption_tier="medium",
        people=(),
        max_people=3,
        date=None,
    )
    assert "scene" not in facts


# ------------------------------------------ Step 1: the catalog reader behind the token


def test_captions_by_file_id_returns_the_live_label_for_each_file(tmp_path):
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    fact_id = add_caption(root, "a.png", "Harbour Bridge", sentence="A bridge over water.")
    conn = _conn(root)
    try:
        captions = repo.captions_by_file_id(conn)
    finally:
        conn.close()
    # The LABEL and nothing else: the sentence is spec 6.1's prose half and a filename
    # built from it would be a filename built from a sentence.
    assert captions == {file_id_of(root, "a.png"): repo.CaptionFact(fact_id, "Harbour Bridge")}


def test_two_files_with_the_same_bytes_share_one_caption_fact(tmp_path):
    # spec 4 keys `inferred_facts` by sha256. Two identical photos are two files and one
    # fact, and both must be named.
    root = build(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    (root / "b.png").write_bytes((root / "a.png").read_bytes())
    (root / "b.png.tags.json").write_bytes((root / "a.png.tags.json").read_bytes())
    run_scan(root, exiftool_command=FAKE_COMMAND)
    add_caption(root, "a.png", "Red Door")
    conn = _conn(root)
    try:
        captions = repo.captions_by_file_id(conn)
    finally:
        conn.close()
    assert {c.label for c in captions.values()} == {"Red Door"}
    assert set(captions) == {file_id_of(root, "a.png"), file_id_of(root, "b.png")}


def test_a_superseded_caption_never_names_a_file(tmp_path):
    # spec 8.1's re-enrichment supersedes the old fact and writes a new one. A filename
    # built from a retired fact is a filename built from a model the user replaced.
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    add_caption(root, "a.png", "Red Door", dead=True)
    conn = _conn(root)
    try:
        assert repo.captions_by_file_id(conn) == {}
    finally:
        conn.close()


def test_a_fact_of_another_kind_is_not_a_caption(tmp_path):
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    add_fact(
        root,
        sha_of(root, "a.png"),
        kind="ocr_text",
        value_json=json.dumps({"label": "Red Door", "sentence": "x"}),
        model=CAPTION_MODEL,
    )
    conn = _conn(root)
    try:
        assert repo.captions_by_file_id(conn) == {}
    finally:
        conn.close()


def test_the_most_recent_of_two_live_caption_facts_wins(tmp_path):
    """`inferred_facts` is UNIQUE on (sha256, kind, model_name, model_version), so two LIVE
    `caption` rows for one content are reachable the moment a second model version runs
    without superseding the first. Spec 6.2 rule 6 requires the same inputs to produce the
    same name, and "whichever row SQLite returned first" is not a rule."""
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    add_caption(root, "a.png", "Old Guess", version="1.0")
    newer = add_caption(root, "a.png", "New Guess", version="2.0")
    conn = _conn(root)
    try:
        assert repo.captions_by_file_id(conn) == {
            file_id_of(root, "a.png"): repo.CaptionFact(newer, "New Guess")
        }
    finally:
        conn.close()


# One row per way a hand-edited catalog can be wrong about spec 8.1's `{label, sentence}`.
# NAMED AT MODULE SCOPE AND PINNED BY AN EXACT LENGTH ASSERTED AT COLLECTION TIME: emptying
# an inline list makes pytest SKIP rather than fail (M7's M743), and narrowing it to one
# entry is completely silent. Only an exact length catches the second.
MALFORMED_CAPTIONS = [
    '{"label": "", "sentence": "x"}',
    '{"label": 7, "sentence": "x"}',
    '{"sentence": "x"}',
    '{"caption": "Red Door"}',
    "[]",
    "not json",
]
assert len(MALFORMED_CAPTIONS) == 6, "the malformed-caption table lost or gained a shape"


@pytest.mark.parametrize("body", MALFORMED_CAPTIONS)
def test_a_malformed_caption_names_nothing_rather_than_crashing_the_plan(tmp_path, body):
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    add_fact(root, sha_of(root, "a.png"), kind="caption", value_json=body, model=CAPTION_MODEL)
    conn = _conn(root)
    try:
        assert repo.captions_by_file_id(conn) == {}
    finally:
        conn.close()


def test_live_place_names_is_the_catalogs_whole_place_vocabulary(tmp_path):
    """Spec 6.4 rev 17: "the distinct set of `place` values across live `inferred_facts`
    rows". DISTINCT, live-only, and read off the fact rows rather than off a join to
    `files` - see `repo.live_place_names`' docstring for why the literal reading matters."""
    root = scanned(
        tmp_path,
        {
            "a.png": "2019:07:04 18:23:11",
            "b.png": "2019:07:04 18:24:11",
            "c.png": "2019:07:04 18:25:11",
        },
    )
    add_place(root, "a.png", "Paris")
    add_place(root, "b.png", "Paris")  # the same name twice -> one entry
    add_place(root, "c.png", "Kyoto", dead=True)  # superseded -> no entry
    conn = _conn(root)
    try:
        assert repo.live_place_names(conn) == frozenset({"Paris"})
    finally:
        conn.close()


# ------------------------------------------------------ Step 2: the tier is a CONFIG read

SCENE_TEMPLATE = "{date}_{scene}_{seq}"


def test_the_caption_enricher_reports_no_confidence_at_all(tmp_path):
    """The FIRST premise of the tier derivation, pinned rather than assumed.

    Spec 8.1's caption note: "confidence proxy: none from Ollama -> store NULL". Driven
    against the stub, never against the real Ollama (M10 plan, clarification 7).
    """
    root = tmp_path / "archive"
    root.mkdir()
    Image.new("RGB", (24, 18), (10, 20, 30)).save(root / "photo.png", format="PNG")
    with OllamaStub(
        {
            "/api/generate": [generate_reply("Red Door", "A red door.")],
            "/api/tags": [tags_reply("qwen2.5vl:latest")],
        }
    ) as stub:
        enricher = CaptionEnricher(CAPTION_MODEL, port=stub.port, timeout_s=10.0)
        (fact,) = enricher.enrich(
            FileRecord(
                id=1,
                rel_path="photo.png",
                filename="photo.png",
                ext="png",
                media_type="image",
                size=1024,
                mtime_ns=1,
                sha256="0" * 64,
                status="present",
                placeholder=False,
                companion_group_id=None,
            ),
            pil_image_loader(root),
        )
    assert fact.kind == "caption"
    assert fact.confidence is None


def test_the_config_default_for_a_confidence_less_caption_is_medium():
    """The SECOND premise. Spec 8.1's only rule for a confidence-less enricher is "tier by
    config default (`medium` cap for confidence-less enrichers)", and spec 11 spells that
    default for this enricher and no other:
    `caption = { floor = 0.0, low = 0.5, high = 0.85, max_tier_when_no_confidence = "medium" }`.
    """
    assert CAPTION_TIER_KEY == "caption"
    assert TierBounds(0.0, 0.5, 0.85, "medium").max_tier_when_no_confidence == "medium"
    # The shipped default, indexed the way the planner indexes it.
    assert Config().tiers[CAPTION_TIER_KEY].max_tier_when_no_confidence == "medium"


def test_the_scene_tier_is_medium_end_to_end_and_caps_the_item(tmp_path):
    # The conclusion of the two premises above, measured through the real planner. Spec
    # 6.3 rule 4: the item's tier is the MINIMUM across its tokens, so a scene token drags
    # an otherwise-high item to `medium` - clarification 10, and it is designed behaviour.
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    fact_id = add_caption(root, "a.png", "Red Door")
    summary = run_plan(root, template_override=SCENE_TEMPLATE)
    row = stored(root, summary.plan_id)["a.png"]
    assert row["generated_filename"] == "2019-07-04_Red-Door_001.png"
    assert tokens_of(row)["scene"] == {
        "token": "scene",
        "resolved_from": "scene",
        "value": "Red-Door",
        "source_table": "inferred_facts",
        "source_id": fact_id,
        "tier": "medium",
        "truncated": False,
    }
    assert row["tier"] == "medium"
    # No deduction: spec 6.4's table charges nothing for a caption that contradicts
    # nothing, and a NULL confidence is not "confidence < high bound".
    assert row["trust_score"] == 100
    assert json.loads(row["trust_reasons_json"]) == []


def test_the_scene_tier_FOLLOWS_the_config_key_rather_than_a_literal(tmp_path):
    """THE TEST THAT SEPARATES DERIVED FROM COPIED, and the reason Step 2 exists at all.

    `PLACE_TIER` is a literal because spec 11 has no `geocode` row to read. `[tiers]
    caption` DOES exist, so the value pam uses must come from it - and the only way to
    show that is to change the key and watch the tier move. A hard-coded "medium" passes
    every assertion in the test above and fails this one.
    """
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    add_caption(root, "a.png", "Red Door")
    write_config(
        root,
        "[tiers]\n"
        "caption = { floor = 0.0, low = 0.5, high = 0.85,"
        ' max_tier_when_no_confidence = "low" }\n',
    )
    summary = run_plan(root, template_override=SCENE_TEMPLATE)
    row = stored(root, summary.plan_id)["a.png"]
    assert tokens_of(row)["scene"]["tier"] == "low"
    assert row["tier"] == "low"
    # And the NAME does not move with the tier: spec 6.1's "missing/below-floor token ->
    # omitted" is a different test, and a `low`-tier token still emits.
    assert row["generated_filename"] == "2019-07-04_Red-Door_001.png"


# ------------------------------------------- Step 3: {event-or-scene}, with two candidates

ALIAS_TEMPLATE = "{date}_{event-or-scene}_{seq}"


def event_ids(root):
    conn = _conn(root)
    try:
        return [int(r["id"]) for r in conn.execute("SELECT id FROM events ORDER BY id")]
    finally:
        conn.close()


def set_label(root, event_id, label):
    """Seed a label on one `events` row, with `label_source='user'`.

    `tests/test_event_naming.py::set_label` argues both halves: a test may write catalog
    state, and 'user' is what stops `pam plan`'s own geocoding pass from clearing a label
    on the next run over a fixture that carries no GPS.
    """
    conn = _conn(root)
    try:
        conn.execute(
            "UPDATE events SET label = ?, label_source = 'user' WHERE id = ?", (label, event_id)
        )
        conn.commit()
    finally:
        conn.close()


def test_the_alias_resolves_to_the_scene_when_the_event_has_no_label(tmp_path):
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    add_caption(root, "a.png", "Red Door")
    summary = run_plan(root, template_override=ALIAS_TEMPLATE)
    row = stored(root, summary.plan_id)["a.png"]
    assert row["generated_filename"] == "2019-07-04_Red-Door_001.png"
    # The emitted token records WHICH candidate supplied the value in `resolved_from` and
    # the template position it filled in `token`.
    scene = tokens_of(row)["event-or-scene"]
    assert scene["resolved_from"] == "scene"
    assert scene["value"] == "Red-Door"
    assert scene["source_table"] == "inferred_facts"


def test_the_alias_resolves_to_the_event_when_the_event_has_a_label(tmp_path):
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    run_plan(root, template_override=ALIAS_TEMPLATE)  # step 0 creates the events row
    (event_id,) = event_ids(root)
    set_label(root, event_id, "Lake Tahoe")
    summary = run_plan(root, template_override=ALIAS_TEMPLATE)
    row = stored(root, summary.plan_id)["a.png"]
    assert row["generated_filename"] == "2019-07-04_Lake-Tahoe_001.png"
    event = tokens_of(row)["event-or-scene"]
    assert event["resolved_from"] == "event"
    assert event["source_table"] == "events"
    assert event["source_id"] == event_id


def test_the_alias_prefers_the_EVENT_when_both_candidates_are_live(tmp_path):
    """THE TIE, and the first time in this project's life it can be built.

    Spec 6.1: "`{event-or-scene}` means event label if the file's event has one, else scene
    token", and `template._ALIASED` is ("event", "scene") IN THAT ORDER. Through M9 the
    alias could only ever have one candidate, so the order was untested by anything that
    could tell the two apart.
    """
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    add_caption(root, "a.png", "Red Door")
    run_plan(root, template_override=ALIAS_TEMPLATE)
    (event_id,) = event_ids(root)
    set_label(root, event_id, "Lake Tahoe")
    summary = run_plan(root, template_override=ALIAS_TEMPLATE)
    row = stored(root, summary.plan_id)["a.png"]
    assert row["generated_filename"] == "2019-07-04_Lake-Tahoe_001.png"
    assert tokens_of(row)["event-or-scene"]["resolved_from"] == "event"
    # And the scene contributed NOTHING - not the name, not a provenance row, not the
    # tier. The item is `high` because the user-sourced event label is `high` (spec 6.1
    # rev 15) and the caption is not in the name to cap it.
    assert set(tokens_of(row)) == {"date", "event-or-scene", "seq"}
    assert row["tier"] == "high"


def test_the_alias_emits_nothing_when_neither_candidate_exists(tmp_path):
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    summary = run_plan(root, template_override=ALIAS_TEMPLATE)
    row = stored(root, summary.plan_id)["a.png"]
    assert row["generated_filename"] == "2019-07-04_001.png"
    assert set(tokens_of(row)) == {"date", "seq"}


# -------------------------------------------------- Step 4: the trim order's FIRST entry

# Spec 6.2 rule 5, spelled out from the sentence rather than read back from
# `template._TRIM_ORDER`: "Trim, in order: {scene}, {place}, {people} until it fits."
SPEC_TRIM_ORDER = ("scene", "place", "people")

TRIM_ARGS = dict(archive_root_abs="C:\\a", rel_dir="", exts=("png",))


def stem_of(order, values, budget_padding=0):
    """`build_stem` with a budget squeezed by making the archive root long.

    The budget is `MAX_FULL_PATH - len(root) - len(rel_dir) - len(ext) - 1`, so padding the
    root is the one knob that shortens it without touching the values.
    """
    root = "C:\\" + "d" * budget_padding
    return build_stem(order, values, archive_root_abs=root, rel_dir="", exts=("png",))


def test_the_trim_order_starts_with_scene():
    # Production's tuple against the SPEC's sentence, written out above by hand. The
    # private name is imported deliberately: the point of this line is that the shipped
    # constant says what rule 5 says, and there is no public reading of it.
    assert _TRIM_ORDER == SPEC_TRIM_ORDER


def test_scene_is_dropped_BEFORE_place():
    """Rule 5's first step, with a real value in it for the first time.

    A budget that fits the date, the place and the number but not the scene as well: the
    scene goes and the place stays. Reverse the trim order and this test fails, because
    the place would go first and the scene would then still not fit.
    """
    values = {"date": "2019-07-04", "scene": "S" * 40, "place": "Paris", "seq": "001"}
    order = ("date", "scene", "place", "seq")
    result = stem_of(order, values, budget_padding=185)
    assert result.stem == "2019-07-04_Paris_001"
    assert [t.name for t in result.emitted] == ["date", "place", "seq"]
    assert result.notes == (DROPPED_NOTE.format(label=TOKEN_LABELS["scene"]),)


def test_place_is_dropped_second_when_dropping_scene_was_not_enough():
    values = {"date": "2019-07-04", "scene": "S" * 40, "place": "P" * 40, "seq": "001"}
    order = ("date", "scene", "place", "seq")
    result = stem_of(order, values, budget_padding=205)
    assert result.stem == "2019-07-04_001"
    assert set(result.notes) == {
        DROPPED_NOTE.format(label=TOKEN_LABELS["scene"]),
        DROPPED_NOTE.format(label=TOKEN_LABELS["place"]),
    }


def test_an_absent_trim_candidate_does_not_consume_a_drop_step():
    """Rule 5's clause that a plausible loop gets wrong.

    With no scene in the name, the FIRST trim step must still be the one that removes the
    place - the loop must not advance past `scene` on faith and leave the name too long. A
    loop that consumed a step for an absent candidate would emit the over-long name here.
    """
    values = {"date": "2019-07-04", "place": "P" * 40, "seq": "001"}
    result = stem_of(("date", "place", "seq"), values, budget_padding=185)
    assert result.stem == "2019-07-04_001"
    assert result.notes == (DROPPED_NOTE.format(label=TOKEN_LABELS["place"]),)


# A scene label no path budget can hold. Nothing sanitizes it away (rule 2 keeps ASCII
# letters, rule 3 collapses only repeated separators) and 300 > the 240-character budget on
# any machine, so the trim order MUST drop it.
OVERLONG_SCENE = "S" * 300


def test_a_dropped_scene_is_disclosed_in_trust_reasons_and_charged_nothing(tmp_path):
    """Spec 6.2 rule 7, for the trim order's first entry, end to end.

    A DISCLOSURE, NOT A DEDUCTION (rule 7's closing paragraph, M8's A5): the score is still
    100 and the tier is still `high`, because a dropped token makes no claim at all. And
    the tier is `high` for a second reason worth seeing - the scene's `medium` cap goes
    with it, since `token_tiers` reads the tokens that reached the name.

    It also does NOT re-create `StemResult.dropped`: what crosses the seam is the finished
    SENTENCE, and the token is absent from provenance entirely.
    """
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    add_caption(root, "a.png", OVERLONG_SCENE)
    summary = run_plan(root, template_override=SCENE_TEMPLATE)
    row = stored(root, summary.plan_id)["a.png"]
    assert row["generated_filename"] == "2019-07-04_001.png"
    assert json.loads(row["trust_reasons_json"]) == [
        DROPPED_NOTE.format(label=TOKEN_LABELS["scene"])
    ]
    assert row["trust_score"] == 100
    assert row["tier"] == "high"
    assert [t["token"] for t in json.loads(row["tokens_json"])] == ["date", "seq"]
    assert not hasattr(summary, "dropped")


# ------------------------------- Step 5: spec 6.4's caption-vs-geocode rule (A8, B3)

RULE_TEMPLATE = "{date}_{scene}_{seq}"


def flagged(row):
    """Did the caption rule fire on this stored item? Read off the STORED sentences."""
    return [r for r in json.loads(row["trust_reasons_json"]) if "AI scene guess" in r]


def test_a_caption_naming_another_files_place_caps_the_scene_token_to_low(tmp_path):
    """Spec 6.4 rev 17's rule, with both operands present for the first time in the
    product's life. The effect is exactly what the table's row says: "Scene token tier
    capped at `low` -> review required"."""
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11", "b.png": "2019:07:04 18:24:11"})
    add_place(root, "a.png", "Tokyo")
    add_caption(root, "a.png", "Paris rooftops")
    add_place(root, "b.png", "Paris")
    summary = run_plan(root, template_override=RULE_TEMPLATE)
    row = stored(root, summary.plan_id)["a.png"]
    assert tokens_of(row)["scene"]["tier"] == "low"
    assert row["tier"] == "low"
    assert flagged(row) == [CAPTION_PLACE_REASON.format(found="Paris", own="Tokyo")]
    assert row["trust_score"] == 100 - TRUST_CONSISTENCY
    # RULE 5 OF B3: it caps a TIER and can never change a NAME. Spec 6.2 rule 6's "same
    # inputs, same name" is untouched, which is what bounds the non-locality below.
    assert row["generated_filename"] == "2019-07-04_Paris-rooftops_001.png"


def test_THE_NON_LOCALITY_the_same_caption_on_the_same_file_flips_as_the_archive_changes(
    tmp_path,
):
    """B3, AND NOTHING ELSE IN THIS FILE TESTS IT.

    Spec 6.4 rev 18: "whether one photograph's caption is flagged depends on what other
    photographs are in the archive". The vocabulary is the catalog's own live `place`
    values, because pam ships no gazetteer and `reverse_geocoder` answers *coordinates ->
    place*, the wrong direction.

    So: one file, one caption, one geocode, and NOTHING about it changes between the two
    plans below. What changes is that a second, unrelated photograph acquires a `place`
    fact - and the first file's caption becomes contradictory where it was not. An
    implementation whose vocabulary is the file's OWN place (the plausible local reading)
    passes every other test in this section and fails this one.
    """
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11", "b.png": "2019:07:04 18:24:11"})
    add_place(root, "a.png", "Tokyo")
    add_caption(root, "a.png", "Paris rooftops")

    before = stored(root, run_plan(root, template_override=RULE_TEMPLATE).plan_id)["a.png"]
    assert flagged(before) == []
    assert before["tier"] == "medium"  # the scene's own cap, and nothing else

    # The ONLY change: an unrelated photograph is geocoded to Paris.
    add_place(root, "b.png", "Paris")

    after = stored(root, run_plan(root, template_override=RULE_TEMPLATE).plan_id)["a.png"]
    assert flagged(after) == [CAPTION_PLACE_REASON.format(found="Paris", own="Tokyo")]
    assert after["tier"] == "low"
    # And the NAME is byte-identical across the flip, which is what makes the non-locality
    # acceptable rather than a violation of spec 6.2 rule 6.
    assert after["generated_filename"] == before["generated_filename"]


def test_a_caption_naming_a_place_the_catalog_has_never_heard_of_is_not_flagged(tmp_path):
    # Spec 6.4's vocabulary is the archive's own catalog and nothing else. "Place B" is
    # a place name; it is not a place name THIS archive holds, so nothing contradicts.
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    add_place(root, "a.png", "Tokyo")
    add_caption(root, "a.png", "Place B harbour")
    row = stored(root, run_plan(root, template_override=RULE_TEMPLATE).plan_id)["a.png"]
    assert flagged(row) == []


def test_a_caption_naming_its_OWN_place_agrees_and_is_not_flagged(tmp_path):
    # Spec 6.4 rev 17: "a caption naming its own file's place AGREES and is not flagged".
    # Case-insensitively, which is the same clause the match itself is written under.
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    add_place(root, "a.png", "Tokyo")
    add_caption(root, "a.png", "tokyo street")
    row = stored(root, run_plan(root, template_override=RULE_TEMPLATE).plan_id)["a.png"]
    assert flagged(row) == []
    assert row["tier"] == "medium"


def test_a_caption_on_a_file_with_NO_place_fact_contradicts_nothing(tmp_path):
    """Spec 6.4 rev 17: "A caption naming a place for a file with NO `place` fact
    contradicts nothing and is not flagged". Both operands, not one."""
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11", "b.png": "2019:07:04 18:24:11"})
    add_caption(root, "a.png", "Paris rooftops")  # a.png has no place fact
    add_place(root, "b.png", "Paris")
    row = stored(root, run_plan(root, template_override=RULE_TEMPLATE).plan_id)["a.png"]
    assert flagged(row) == []


# The four shapes spec 6.4 rev 18 names by hand, each with the caption that must fire and a
# NEAR MISS that must not. The near miss is the half that matters: "whole token is where a
# plausible implementation goes wrong quietly", and a substring test passes every `fires`
# row below and fails every `misses` one.
#
# Written out here rather than derived, and asserted non-empty at collection time (M7's
# M743: pytest SKIPS an empty parametrize rather than failing it).
WHOLE_TOKEN_CASES = [
    # (the catalog's other place, a caption that FIRES, a caption that does NOT)
    ("York", "York minster", "Yorkshire pudding"),
    ("New York", "New York skyline", "New buildings York"),
    ("St. Louis", "St. Louis arch", "Louis quatorze"),
    ("Lake-Tahoe", "Lake Tahoe pines", "Tahoe pines"),
]
assert len(WHOLE_TOKEN_CASES) == 4, "the whole-token table lost or gained a shape"


@pytest.mark.parametrize("place,fires,misses", WHOLE_TOKEN_CASES)
def test_the_match_is_whole_token_after_sanitization(tmp_path, place, fires, misses):
    """Spec 6.4 rev 17: "matches - case-insensitively, as a whole token, after spec 6.2's
    sanitization". `St. Louis` and `Lake-Tahoe` are the punctuation half: the sanitizer
    writes them `St.-Louis` and `Lake-Tahoe`, so the comparison must be made on tokens the
    sanitizer produced and not on the raw catalog text."""
    root = scanned(
        tmp_path,
        {
            "a.png": "2019:07:04 18:23:11",
            "b.png": "2019:07:04 18:24:11",
            "c.png": "2019:07:04 18:25:11",
        },
    )
    add_place(root, "a.png", "Tokyo")
    add_place(root, "b.png", "Tokyo")
    add_place(root, "c.png", place)  # the vocabulary entry under test
    add_caption(root, "a.png", fires)
    add_caption(root, "b.png", misses)
    rows = stored(root, run_plan(root, template_override=RULE_TEMPLATE).plan_id)
    assert flagged(rows["a.png"]) == [CAPTION_PLACE_REASON.format(found=place, own="Tokyo")]
    assert flagged(rows["b.png"]) == []


# Spec 6.4 rev 17's match is "case-insensitively, as a whole token, after spec 6.2's
# sanitization" - THREE properties, and the whole-token table above separates only the last
# two. Both directions, because a fold applied to one side only is a plausible half-fix and
# passes whichever direction happened to be written.
#
# Written out here rather than derived, and asserted non-empty at collection time (M7's
# M743: pytest SKIPS an empty parametrize rather than failing it).
CASE_INSENSITIVE_CASES = [
    # (the catalog's other place, a caption naming it in a DIFFERENT case)
    ("Kyoto", "KYOTO street"),
    ("Kyoto", "kyoto street"),
    ("kyoto", "Kyoto street"),
    ("LAKE-TAHOE", "lake tahoe pines"),
]
assert len(CASE_INSENSITIVE_CASES) == 4, "the case-insensitivity table lost or gained a shape"


@pytest.mark.parametrize("place,caption", CASE_INSENSITIVE_CASES)
def test_the_match_is_case_insensitive_in_both_directions(tmp_path, place, caption):
    """Spec 6.4 rev 17: the caption "matches - CASE-INSENSITIVELY, as a whole token, after
    spec 6.2's sanitization - a place in that set other than its own".

    The expected sentence is SPELLED OUT BY HAND from spec 6.4 rather than formatted from
    `CAPTION_PLACE_REASON`, on Task 8 stage 1's F2: an oracle built from the definition it
    checks is a survivor waiting to happen. The catalog's own spelling of the place is what
    the sentence must print, not the caption's."""
    root = scanned(
        tmp_path,
        {
            "a.png": "2019:07:04 18:23:11",
            "c.png": "2019:07:04 18:25:11",
        },
    )
    add_place(root, "a.png", "Tokyo")
    add_place(root, "c.png", place)  # the vocabulary entry, in ITS case
    add_caption(root, "a.png", caption)  # the caption, in the OTHER case
    row = stored(root, run_plan(root, template_override=RULE_TEMPLATE).plan_id)["a.png"]
    assert flagged(row) == [
        f"The AI scene guess mentions {place}, but this photo's location reads Tokyo - "
        "please check this one."
    ]
    assert row["tier"] == "low"


def test_the_flag_is_reported_deterministically_when_two_places_could_match(tmp_path):
    # Spec 6.2 rule 6's determinism, applied to the SENTENCE rather than to the name: a
    # set's iteration order is not a rule, and this string is stored and read back by the
    # review UI. `sorted()` picks 'Kyoto' over 'Paris' every time.
    root = scanned(
        tmp_path,
        {
            "a.png": "2019:07:04 18:23:11",
            "b.png": "2019:07:04 18:24:11",
            "c.png": "2019:07:04 18:25:11",
        },
    )
    add_place(root, "a.png", "Tokyo")
    add_place(root, "b.png", "Paris")
    add_place(root, "c.png", "Kyoto")
    add_caption(root, "a.png", "Paris Kyoto")
    row = stored(root, run_plan(root, template_override=RULE_TEMPLATE).plan_id)["a.png"]
    assert flagged(row) == [CAPTION_PLACE_REASON.format(found="Kyoto", own="Tokyo")]


def test_the_trust_reason_names_BOTH_the_captions_place_and_the_files_own(tmp_path):
    """Spec 6.4: the flag is about a DISAGREEMENT, so its reason must name both halves -
    the place the caption mentions and the place this photo's own geocode reads. A sentence
    naming only one of them cannot be acted on by the human it is written for.

    SPELLED OUT BY HAND, not formatted from `CAPTION_PLACE_REASON`. Dropping the `{own}`
    half of that constant is invisible to any oracle built from it - `str.format` ignores
    the now-extra keyword and every such test formats the same shortened string and agrees
    with it (Task 8 stage 1's F2, and this file's M1616). Both place names appear as
    separate literals below so that losing either one is a failure here."""
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11", "b.png": "2019:07:04 18:24:11"})
    add_place(root, "a.png", "Tokyo")
    add_place(root, "b.png", "Paris")
    add_caption(root, "a.png", "Paris rooftops")
    reasons = flagged(
        stored(root, run_plan(root, template_override=RULE_TEMPLATE).plan_id)["a.png"]
    )
    assert reasons == [
        "The AI scene guess mentions Paris, but this photo's location reads Tokyo - "
        "please check this one."
    ]
    # The two halves again, as independent claims: a future edit that keeps the sentence
    # well-formed while dropping one of the names fails on the assertion that names it.
    assert "Paris" in reasons[0]  # the caption's place
    assert "Tokyo" in reasons[0]  # the file's own place


def test_a_superseded_place_is_not_part_of_the_vocabulary(tmp_path):
    # The vocabulary is spelled "across LIVE `inferred_facts` rows". A place the user has
    # re-geocoded away is not a place this archive holds.
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11", "b.png": "2019:07:04 18:24:11"})
    add_place(root, "a.png", "Tokyo")
    add_caption(root, "a.png", "Paris rooftops")
    add_place(root, "b.png", "Paris", dead=True)
    row = stored(root, run_plan(root, template_override=RULE_TEMPLATE).plan_id)["a.png"]
    assert flagged(row) == []


def test_a_dropped_scene_token_is_charged_nothing_even_when_the_rule_fires(tmp_path):
    # `consistency.score` and `token_tiers` both read the tokens that reached the NAME, so
    # a flag on a token the budget removed charges nothing and caps nothing. The item is
    # `high`, not `low`, and the only sentence is rule 7's disclosure.
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11", "b.png": "2019:07:04 18:24:11"})
    add_place(root, "a.png", "Tokyo")
    add_caption(root, "a.png", "Paris " + OVERLONG_SCENE)
    add_place(root, "b.png", "Paris")
    row = stored(root, run_plan(root, template_override=RULE_TEMPLATE).plan_id)["a.png"]
    assert row["generated_filename"] == "2019-07-04_001.png"
    assert row["tier"] == "high"
    assert row["trust_score"] == 100
    assert json.loads(row["trust_reasons_json"]) == [
        DROPPED_NOTE.format(label=TOKEN_LABELS["scene"])
    ]


# --------------------------- Step 6: the attention-equivalence proof, per FLAG CLASS


def review_items(root, plan_id):
    conn = _conn(root)
    try:
        return {
            item.old_rel_path: item for item in repo.plan_rows(conn, plan_id, floor=FACES_FLOOR)
        }
    finally:
        conn.close()


def test_the_caption_flag_class_lands_inside_attention_against_the_REAL_planner(tmp_path):
    """Spec 8.2's `attention` note, discharged for the flag class M10 adds.

    The note requires the equivalence between spec 6.4's flags and "tier `low`" to be
    "proven against the real planner per flag class rather than asserted", and this is the
    first new class since it was written (carried from M8's close through `m9.md` 5.4).

    `attention` is *tier `low` OR undated OR truncated*. This file is DATED and its tokens
    are COMPLETE, so the other two operands are false and the first one is doing all the
    work - which is what makes this a proof about the flag class rather than about a file
    that happened to be in `attention` anyway. The negative control beside it carries the
    identical caption with the vocabulary entry removed.
    """
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11", "b.png": "2019:07:04 18:24:11"})
    add_place(root, "a.png", "Tokyo")
    add_caption(root, "a.png", "Paris rooftops")
    add_place(root, "b.png", "Paris")

    item = review_items(root, run_plan(root, template_override=RULE_TEMPLATE).plan_id)["a.png"]
    assert item.tier == "low"
    assert item.date == "2019-07-04"  # not undated
    assert item.truncated is False  # nothing shortened
    assert item.attention is True

    # The control: the same caption on the same file, with the vocabulary entry gone.
    conn = _conn(root)
    try:
        conn.execute(
            "DELETE FROM inferred_facts WHERE kind = 'place' AND value_json LIKE ?",
            ("%Paris%",),
        )
        conn.commit()
    finally:
        conn.close()
    control = review_items(root, run_plan(root, template_override=RULE_TEMPLATE).plan_id)[
        "a.png"
    ]
    assert control.tier == "medium"
    assert control.attention is False


# ------------------------- Step 7: `tier` and `attention` are UNIFORM across a unit


def test_a_companion_units_members_share_one_tier_one_attention_and_one_stem(tmp_path):
    """Spec 8.2 rev 15's uniformity, ASSERTED rather than assumed, for the first fact kind
    that is not present on every member of a unit.

    `wants()` is per FILE and `inferred_facts` is keyed by sha256, so a unit's `.AAE`
    sidecar can never carry a caption of its own - two members of one unit never share a
    digest. The proposal is still ONE stem, built from the PRIMARY, and `tier`,
    `trust_score`, `tokens_json` and therefore `attention` are computed once per unit and
    copied to every member.

    THIS IS LOAD-BEARING FOR M8's A1: the preset bulk-approval buttons filter on `tier` and
    on `attention`, and they are safe precisely because those two are unit-uniform (m9.md
    5.4: "Do not re-open"). A caption that reached only the primary's item would make them
    non-uniform and would put a fourth split route into the product.
    """
    root = build(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    (root / "a.aae").write_text("<plist/>", encoding="utf-8")
    (root / "a.aae.tags.json").write_text("{}", encoding="utf-8")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    add_place(root, "a.png", "Tokyo")
    add_caption(root, "a.png", "Red Door")

    plan_id = run_plan(root, template_override=SCENE_TEMPLATE).plan_id
    rows = stored(root, plan_id)
    assert set(rows) == {"a.png", "a.aae"}
    # One stem across the unit (vision S3), built from the primary's caption.
    assert {r["generated_filename"] for r in rows.values()} == {
        "2019-07-04_Red-Door_001.png",
        "2019-07-04_Red-Door_001.aae",
    }
    # ...and one tier, one score, one provenance document, for the member that has the
    # caption fact and for the member that structurally cannot.
    assert len({r["tier"] for r in rows.values()}) == 1
    assert len({r["trust_score"] for r in rows.values()}) == 1
    assert len({r["tokens_json"] for r in rows.values()}) == 1
    assert rows["a.png"]["tier"] == "medium"

    items = review_items(root, plan_id)
    assert {i.attention for i in items.values()} == {False}
    # The sidecar really does carry no caption fact of its own - so the uniformity above
    # comes from the planner copying the unit's facts, not from both files having one.
    conn = _conn(root)
    try:
        captions = repo.captions_by_file_id(conn)
    finally:
        conn.close()
    assert set(captions) == {file_id_of(root, "a.png")}


def test_a_flagged_units_members_are_ALL_in_attention(tmp_path):
    # The same uniformity where it costs something: the caption rule caps the PRIMARY's
    # scene token to `low`, and every member of the unit lands in `attention` with it.
    root = build(tmp_path, {"a.png": "2019:07:04 18:23:11", "b.png": "2019:07:04 18:24:11"})
    (root / "a.aae").write_text("<plist/>", encoding="utf-8")
    (root / "a.aae.tags.json").write_text("{}", encoding="utf-8")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    add_place(root, "a.png", "Tokyo")
    add_caption(root, "a.png", "Paris rooftops")
    add_place(root, "b.png", "Paris")

    plan_id = run_plan(root, template_override=RULE_TEMPLATE).plan_id
    items = review_items(root, plan_id)
    unit = {p: i for p, i in items.items() if p in ("a.png", "a.aae")}
    assert len(unit) == 2
    assert {i.tier for i in unit.values()} == {"low"}
    assert {i.attention for i in unit.values()} == {True}
    assert len({i.unit_key for i in unit.values()}) == 1
