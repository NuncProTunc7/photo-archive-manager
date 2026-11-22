"""M11 Task 12: spec 6.1's `{people}` token, A14's tier and provenance, A15's cap disclosure,
the trim order's THIRD entry, and spec 6.4's "people present (user-confirmed) +5".

`{people}` IS THE LAST OF SPEC 6.2 RULE 5's THREE TRIM ENTRIES TO BECOME REACHABLE. `{place}`
made the second reachable in M9 and `{scene}` the first in M10; this file is
`tests/test_scene_naming.py`'s sibling by design and repeats its shape deliberately.

WHAT IS DIFFERENT, AND WHY EACH HAS ITS OWN TEST:

1. **The read is composed, not joined.** `repo.people_by_sha256` reads `face_instances` and
   `persons` alone (owner ruling 2026-09-17: no sixth cross-table reach), and the planner
   maps the unit PRIMARY's `files.sha256` onto it. So "two files sharing one sha256 are both
   named" is a property of the COMPOSITION, tested end to end.
2. **The tier is a LITERAL `high`** (A14): a person put the name there.
3. **Five filters stand between a face and a filename** - a person id, `hidden = 0`,
   `review_state IS NULL`, the A11 floor, and one entry per person per photograph - and I2
   and vision R6 rest on every one. Each has its own test, and each negative case is built
   beside a positive one so it cannot pass because the read returns nothing at all.
4. **The sort is by the value that REACHES the filename**, and no live fixture can separate
   that from sorting by `label` - so the separating row (`label="Jonathan"`,
   `filename_label="Abby"`) is built by hand (M8's M993).

THE ORACLES ARE INDEPENDENT. Faces and persons are planted with the SUITE'S OWN SQL, every
expected name, sentence and number is a literal spelled from the spec, and the trim order is
written out from rule 5's sentence rather than read from `template._TRIM_ORDER`.
"""

import json
import sqlite3

import pytest
from archives import FAKE_COMMAND
from PIL import Image

from pam.catalog import repo
from pam.catalog.db import connect, migrate
from pam.naming.planner import run_plan
from pam.naming.template import build_stem
from pam.naming.tokens import TokenFact, resolve_tokens
from pam.paths import workspace_for
from pam.scan.runner import run_scan

FLOOR = 0.4
GEOMETRY = json.dumps({"v": 1, "bbox": [0.1, 0.2, 0.3, 0.4], "source_w": 100, "source_h": 100})

# --------------------------------------------------------------------------- fixtures


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "catalog.db")
    migrate(conn)
    yield conn
    conn.close()


def plant_person(conn, label, *, filename_label=None, hidden=0):
    """One `persons` row with the SUITE's own INSERT, never `repo.upsert_person`."""
    cur = conn.execute(
        "INSERT INTO persons (label, filename_label, hidden) VALUES (?, ?, ?)",
        (label, filename_label, hidden),
    )
    conn.commit()
    return int(cur.lastrowid)


def plant_face(conn, sha, *, person_id=None, cluster_id=None, review_state=None, conf=0.9):
    """One `face_instances` row with the SUITE's own INSERT, never `repo.insert_face`."""
    cur = conn.execute(
        "INSERT INTO face_instances (sha256, bbox_json, embedding, det_confidence,"
        " cluster_id, person_id, review_state, model_name, model_version)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 'yunet+sface', '1')",
        (sha, GEOMETRY, b"\x01", conf, cluster_id, person_id, review_state),
    )
    conn.commit()
    return int(cur.lastrowid)


def labels(found):
    """`{sha256: (filename_value, ...)}` - the read's answer in the words a name uses."""
    return {sha: tuple(p.filename_value for p in people) for sha, people in found.items()}


def build(tmp_path, files):
    """`{filename: exif date or None}` as real, distinct PNGs (one sha256 each)."""
    root = tmp_path / "photos"
    root.mkdir(exist_ok=True)
    for index, (name, date) in enumerate(files.items()):
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


def name_face(root, rel_path, label, **kw):
    """A labeled face on one archive file: a person row and a face pointing at it."""
    conn = _conn(root)
    try:
        person = plant_person(conn, label, **kw)
        plant_face(conn, sha_of(root, rel_path), person_id=person)
        return person
    finally:
        conn.close()


def write_config(root, text):
    workspace_for(root).config_path.write_text(text, encoding="utf-8")


def stored(root, plan_id):
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


def reasons_of(row):
    return json.loads(row["trust_reasons_json"])


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


def person(pid, label, filename_label=None):
    return repo.PersonLabel(id=pid, label=label, filename_label=filename_label)


# ------------------------------------------------- Step 1: the read, and its five filters


def test_a_labeled_face_at_or_above_the_floor_names_its_photograph(db):
    emma = plant_person(db, "Emma")
    plant_face(db, "s1", person_id=emma, conf=FLOOR)  # exactly AT the floor counts (>=)
    assert repo.people_by_sha256(db, FLOOR) == {"s1": (person(emma, "Emma"),)}


def test_a_HIDDEN_person_contributes_nothing(db):
    """Spec 4: "hidden persons never appear in {people}". The privacy promise runs through
    this filter, so the visible person beside the hidden one proves the read is live."""
    shown = plant_person(db, "Emma")
    hidden = plant_person(db, "Secret", hidden=1)
    plant_face(db, "s1", person_id=shown)
    plant_face(db, "s1", person_id=hidden)
    plant_face(db, "s2", person_id=hidden)
    assert labels(repo.people_by_sha256(db, FLOOR)) == {"s1": ("Emma",)}


# A13's two review states, transcribed from spec 4 - never read from `repo.REVIEW_STATES`.
# Asserted at collection: pytest SKIPS an empty parametrize rather than failing it (M743).
SPEC_REVIEW_STATES = ["not_a_face", "dismissed"]
assert len(SPEC_REVIEW_STATES) == 2, "the review-state table lost or gained a state"


@pytest.mark.parametrize("state", SPEC_REVIEW_STATES)
def test_a_reviewed_away_face_contributes_nothing(db, state):
    emma = plant_person(db, "Emma")
    plant_face(db, "s1", person_id=emma, review_state=state)
    plant_face(db, "s2", person_id=emma)
    assert labels(repo.people_by_sha256(db, FLOOR)) == {"s2": ("Emma",)}


def test_a_BELOW_FLOOR_face_contributes_nothing_and_the_floor_is_the_parameter(db):
    """A11: the floor is applied at READ time. Read the same rows at two floors and watch
    the answer move - an implementation with a floor of its own passes only one half."""
    emma = plant_person(db, "Emma")
    plant_face(db, "s1", person_id=emma, conf=0.39)
    plant_face(db, "s2", person_id=emma, conf=0.9)
    assert labels(repo.people_by_sha256(db, FLOOR)) == {"s2": ("Emma",)}
    assert labels(repo.people_by_sha256(db, 0.3)) == {"s1": ("Emma",), "s2": ("Emma",)}


def test_an_UNNAMED_clustered_face_never_names_a_photograph(db):
    """I2 / vision R6: "No unconfirmed cluster ever names a file". A face that a clustering
    pass grouped but no human named has a `cluster_id` and no `person_id` - and a person
    whose OTHER face shares that cluster must not lend it their name."""
    emma = plant_person(db, "Emma")
    plant_face(db, "s1", person_id=emma, cluster_id=4)
    plant_face(db, "s2", cluster_id=4)  # same cluster, never named
    plant_face(db, "s3", cluster_id=9)  # a cluster with no person at all
    assert labels(repo.people_by_sha256(db, FLOOR)) == {"s1": ("Emma",)}


def test_a_person_with_two_faces_in_one_photograph_is_named_once(db):
    emma = plant_person(db, "Emma")
    plant_face(db, "s1", person_id=emma)
    plant_face(db, "s1", person_id=emma)
    assert labels(repo.people_by_sha256(db, FLOOR)) == {"s1": ("Emma",)}


def test_people_are_ordered_by_the_value_that_REACHES_the_filename(db):
    """A14: "Sorting is alphabetical by the value that reaches the filename, not by `label`".

    BUILT BY HAND, because no live fixture separates the two readings (M8's M993): Jonathan's
    filename override is `Abby`, which sorts BEFORE Beth while `Jonathan` sorts after her.
    `adam` is lower-case so that a sort without case folding (upper-case letters first)
    would put him after Beth - the sort key is `(casefold, value, id)`.
    """
    beth = plant_person(db, "Beth")
    jon = plant_person(db, "Jonathan", filename_label="Abby")
    adam = plant_person(db, "adam")
    for pid in (beth, jon, adam):
        plant_face(db, "s1", person_id=pid)
    assert labels(repo.people_by_sha256(db, FLOOR)) == {"s1": ("Abby", "adam", "Beth")}


def test_two_people_with_the_same_filename_value_are_ordered_by_id(db):
    # The last operand of the key: a total order, so spec 6.2 rule 6 holds on a tie.
    second = plant_person(db, "Sam B", filename_label="Sam")
    first = plant_person(db, "Sam A", filename_label="Sam")
    for pid in (first, second):
        plant_face(db, "s1", person_id=pid)
    assert [p.id for p in repo.people_by_sha256(db, FLOOR)["s1"]] == sorted([first, second])


def test_an_all_whitespace_filename_label_falls_back_to_the_label():
    # A14: "filename_label when set AND NON-EMPTY, else label" - the rule PersonRow has.
    assert person(1, "Emma", "   ").filename_value == "Emma"
    assert person(1, "Emma", "").filename_value == "Emma"
    assert person(1, "Emma", None).filename_value == "Emma"
    assert person(1, "Emma", "Em").filename_value == "Em"


def test_person_row_and_person_label_share_one_spelling_of_the_rule():
    """M8's M922/M924: two spellings of one rule is one of them being wrong. Pinned by
    behaviour on both types over the same inputs, since both must stay public."""
    for override in (None, "", "  ", "Em"):
        row = repo.PersonRow(
            id=1, label="Emma", filename_label=override, hidden=False, faces=1, contents=1
        )
        assert row.filename_value == person(1, "Emma", override).filename_value


# ----------------------------------------------------- Steps 2-3: the token, from A14


def resolve(people, max_people=3):
    return resolve_tokens(
        plan_file(),
        seq=1,
        event=None,
        place=None,
        scene=None,
        people=people,
        caption_tier="medium",
        max_people=max_people,
        date=None,
    )


def test_the_people_token_is_spelled_from_A14():
    """Value joined by `-`, tier the LITERAL `high`, `source_table='persons'`, and
    `source_id` the LOWEST id named - built so the lowest id is NOT first alphabetically,
    or `people[0].id` would pass too."""
    facts = resolve((person(7, "Abby"), person(3, "Zed")))
    assert facts["people"] == repo_token("Abby-Zed", 3, ((7, "Abby"), (3, "Zed")))


def repo_token(value, source_id, named):
    """`named` is J13's frozen list (design 7.0 rev 26): every person NAMED, in token order,
    as (id, the value that reaches the filename) - spelled here by hand, never read back."""
    return TokenFact(
        value=value, source_table="persons", source_id=source_id, tier="high", named=named
    )


def test_the_value_is_the_filename_label_when_one_is_set():
    facts = resolve((person(4, "Jonathan", "Abby"),))
    assert facts["people"].value == "Abby"


def test_max_people_caps_the_token_and_the_source_id_is_the_lowest_id_NAMED():
    # Capped to one: Abby (id 7) is named, Zed (id 3) is not - so 3 must not be recorded.
    facts = resolve((person(7, "Abby"), person(3, "Zed")), max_people=1)
    assert facts["people"] == repo_token("Abby", 7, ((7, "Abby"),))


def test_no_people_is_an_ABSENT_token_not_an_empty_one():
    assert "people" not in resolve(())


def test_max_people_zero_is_an_ABSENT_token_not_an_empty_one():
    # `people[:0]` is empty: the guard is on the CAPPED list, never on `people`.
    assert "people" not in resolve((person(1, "Emma"),), max_people=0)


def test_resolve_tokens_has_no_default_for_people_or_max_people():
    kw = dict(seq=1, event=None, place=None, scene=None, caption_tier="medium", date=None)
    with pytest.raises(TypeError):
        resolve_tokens(plan_file(), max_people=3, **kw)
    with pytest.raises(TypeError):
        resolve_tokens(plan_file(), people=(), **kw)


# ------------------------------------------------------------ end to end, through the planner

PEOPLE_TEMPLATE = "{date}_{people}_{seq}"
PLAIN_TEMPLATE = "{date}_{seq}"


def test_pam_plan_puts_a_labeled_persons_name_into_the_filename(tmp_path):
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    emma = name_face(root, "a.png", "Emma")
    row = stored(root, run_plan(root, template_override=PEOPLE_TEMPLATE).plan_id)["a.png"]
    assert row["generated_filename"] == "2019-07-04_Emma_001.png"
    assert tokens_of(row)["people"] == {
        "token": "people",
        "resolved_from": "people",
        "value": "Emma",
        "source_table": "persons",
        "source_id": emma,
        "tier": "high",
        "truncated": False,
        "named": [[emma, "Emma"]],
    }
    assert row["tier"] == "high"
    assert row["trust_score"] == 100  # +5 is clamped away on an item with no deduction
    assert reasons_of(row) == ["Names people you labeled: Emma."]


def test_a_template_without_people_is_unaffected_by_a_labeled_face(tmp_path):
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    name_face(root, "a.png", "Emma")
    row = stored(root, run_plan(root, template_override=PLAIN_TEMPLATE).plan_id)["a.png"]
    assert row["generated_filename"] == "2019-07-04_001.png"
    assert "people" not in tokens_of(row)
    assert reasons_of(row) == []
    assert row["trust_score"] == 100


def test_the_default_template_names_people_too(tmp_path):
    # `[naming] template`'s shipped default is `{date}_{event-or-scene}_{people}_{seq}`.
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    name_face(root, "a.png", "Emma")
    row = stored(root, run_plan(root).plan_id)["a.png"]
    assert row["generated_filename"] == "2019-07-04_Emma_001.png"


def test_two_files_sharing_one_sha256_are_both_named(tmp_path):
    """The COMPOSITION: `face_instances` is content-addressed, and the planner maps each
    unit primary's `files.sha256` onto the read. Two identical photographs, one face row."""
    root = build(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    (root / "b.png").write_bytes((root / "a.png").read_bytes())
    (root / "b.png.tags.json").write_bytes((root / "a.png.tags.json").read_bytes())
    run_scan(root, exiftool_command=FAKE_COMMAND)
    name_face(root, "a.png", "Emma")
    rows = stored(root, run_plan(root, template_override=PEOPLE_TEMPLATE).plan_id)
    assert {r["generated_filename"] for r in rows.values()} == {
        "2019-07-04_Emma_001.png",
        "2019-07-04_Emma_002.png",
    }


def test_a_hidden_person_never_reaches_a_filename_end_to_end(tmp_path):
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    name_face(root, "a.png", "Secret", hidden=1)
    row = stored(root, run_plan(root, template_override=PEOPLE_TEMPLATE).plan_id)["a.png"]
    assert row["generated_filename"] == "2019-07-04_001.png"


def test_the_names_sentence_lists_every_person_the_token_names_in_token_order(tmp_path):
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    conn = _conn(root)
    try:
        sha = sha_of(root, "a.png")
        for label, override in (("Jonathan", "Abby"), ("Beth", None)):
            plant_face(conn, sha, person_id=plant_person(conn, label, filename_label=override))
    finally:
        conn.close()
    row = stored(root, run_plan(root, template_override=PEOPLE_TEMPLATE).plan_id)["a.png"]
    assert row["generated_filename"] == "2019-07-04_Abby-Beth_001.png"
    assert reasons_of(row) == ["Names people you labeled: Abby, Beth."]


# ------------------------------------------- Step 4: A15's cap, disclosed and NOT truncated


def plant_people(root, rel_path, names):
    conn = _conn(root)
    try:
        sha = sha_of(root, rel_path)
        for label in names:
            plant_face(conn, sha, person_id=plant_person(conn, label))
    finally:
        conn.close()


def test_max_people_is_read_from_config_and_the_cap_is_disclosed(tmp_path):
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    plant_people(root, "a.png", ["Beth", "Abby"])
    write_config(root, "[naming]\nmax_people = 1\n")
    row = stored(root, run_plan(root, template_override=PEOPLE_TEMPLATE).plan_id)["a.png"]
    assert row["generated_filename"] == "2019-07-04_Abby_001.png"
    assert reasons_of(row) == [
        "Names people you labeled: Abby.",
        "One more person is in this photo than the name has room for (max_people = 1).",
    ]
    # A disclosure, not a deduction and not a truncation (A15).
    assert row["tier"] == "high"
    assert row["trust_score"] == 100
    assert tokens_of(row)["people"]["truncated"] is False


def test_the_spec_example_two_more_people_at_the_default_cap(tmp_path):
    # Spec 6.2 rule 7's own example sentence, at the shipped default of three.
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    plant_people(root, "a.png", ["Ann", "Bob", "Cat", "Dan", "Eve"])
    row = stored(root, run_plan(root, template_override=PEOPLE_TEMPLATE).plan_id)["a.png"]
    assert row["generated_filename"] == "2019-07-04_Ann-Bob-Cat_001.png"
    assert reasons_of(row)[-1] == (
        "Two more people are in this photo than the name has room for (max_people = 3)."
    )


def test_no_cap_sentence_when_everyone_fits(tmp_path):
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    plant_people(root, "a.png", ["Ann", "Bob", "Cat"])
    row = stored(root, run_plan(root, template_override=PEOPLE_TEMPLATE).plan_id)["a.png"]
    assert reasons_of(row) == ["Names people you labeled: Ann, Bob, Cat."]


def test_max_people_zero_writes_no_token_and_no_sentence(tmp_path):
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    plant_people(root, "a.png", ["Ann"])
    write_config(root, "[naming]\nmax_people = 0\n")
    row = stored(root, run_plan(root, template_override=PEOPLE_TEMPLATE).plan_id)["a.png"]
    assert row["generated_filename"] == "2019-07-04_001.png"
    assert reasons_of(row) == []


def test_no_cap_sentence_when_the_template_does_not_emit_people(tmp_path):
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    plant_people(root, "a.png", ["Ann", "Bob", "Cat", "Dan"])
    write_config(root, "[naming]\nmax_people = 1\n")
    row = stored(root, run_plan(root, template_override=PLAIN_TEMPLATE).plan_id)["a.png"]
    assert reasons_of(row) == []


# Number words, spelled from English rather than from production, one to ten; digits above.
SPELLED = [
    (1, "One more person is"),
    (2, "Two more people are"),
    (3, "Three more people are"),
    (4, "Four more people are"),
    (5, "Five more people are"),
    (6, "Six more people are"),
    (7, "Seven more people are"),
    (8, "Eight more people are"),
    (9, "Nine more people are"),
    (10, "Ten more people are"),
    (11, "11 more people are"),
]
assert len(SPELLED) == 11, "the number-word table lost or gained a row"


@pytest.mark.parametrize("extra,opening", SPELLED)
def test_the_cap_sentence_spells_small_numbers(extra, opening):
    from pam.naming.consistency import people_reasons

    named = tuple(person(i, f"P{i:02d}") for i in range(1, extra + 2))
    reasons = people_reasons(named, max_people=1, emitted=True)
    assert reasons[-1] == (
        f"{opening} in this photo than the name has room for (max_people = 1)."
    )


def test_the_cap_leaves_attention_unchanged(tmp_path):
    """A15: "§8.2's `attention` predicate does not move in M11". The same photograph,
    capped and uncapped, reads the same `attention`, tier and score through `plan_rows`."""
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    plant_people(root, "a.png", ["Ann", "Bob", "Cat", "Dan"])

    def item():
        plan_id = run_plan(root, template_override=PEOPLE_TEMPLATE).plan_id
        conn = _conn(root)
        try:
            (only,) = repo.plan_rows(conn, plan_id, floor=FLOOR)
            return only
        finally:
            conn.close()

    write_config(root, "[naming]\nmax_people = 1\n")
    capped = item()
    write_config(root, "[naming]\nmax_people = 10\n")
    uncapped = item()
    assert capped.generated_filename != uncapped.generated_filename  # the cap really bit
    assert capped.truncated is False
    assert capped.attention is False
    assert (capped.attention, capped.tier, capped.trust_score) == (
        uncapped.attention,
        uncapped.tier,
        uncapped.trust_score,
    )


# ------------------------------------------------ Step 5: the trim order's THIRD entry

# Spec 6.2 rule 7's drop sentence for each of rule 5's three trim entries, written out.
SCENE_DROP = "pam left the scene out of this name to fit the Windows path limit."
PLACE_DROP = "pam left the place name out of this name to fit the Windows path limit."
PEOPLE_DROP = "pam left the people out of this name to fit the Windows path limit."


def stem(order, values, padding):
    """`build_stem` under a budget of `240 - 3 - padding - len('.png')` characters."""
    root = "C:\\" + "d" * padding
    return build_stem(order, values, archive_root_abs=root, rel_dir="", exts=("png",))


def test_people_is_dropped_THIRD_after_scene_and_place():
    values = {
        "date": "2019-07-04",
        "scene": "S" * 40,
        "place": "P" * 40,
        "people": "Q" * 40,
        "seq": "001",
    }
    result = stem(("date", "scene", "place", "people", "seq"), values, padding=205)
    assert result.stem == "2019-07-04_001"
    # Rule 5's order, and rule 7's sentence for each: notes come out in the order dropped.
    assert result.notes == (SCENE_DROP, PLACE_DROP, PEOPLE_DROP)


def test_trimming_STOPS_as_soon_as_the_name_fits():
    # Dropping the scene alone is enough, so the place and the people both stay.
    values = {"date": "2019-07-04", "scene": "S" * 40, "place": "Paris", "people": "Emma"}
    values["seq"] = "001"
    result = stem(("date", "scene", "place", "people", "seq"), values, padding=185)
    assert result.stem == "2019-07-04_Paris_Emma_001"
    assert result.notes == (SCENE_DROP,)


def test_people_is_DROPPED_rather_than_truncated():
    # With no scene and no place, the first trim step removes the people WHOLE. A
    # truncating implementation would leave `2019-07-04_QQQQ...` holding a fragment.
    values = {"date": "2019-07-04", "people": "Q" * 40, "seq": "001"}
    result = stem(("date", "people", "seq"), values, padding=205)
    assert result.stem == "2019-07-04_001"
    assert "Q" not in result.stem
    assert [t.name for t in result.emitted] == ["date", "seq"]
    assert result.notes == (PEOPLE_DROP,)


def test_a_dropped_people_token_is_disclosed_and_earns_nothing_end_to_end(tmp_path):
    root = scanned(tmp_path, {"a.png": "2019:07:04 18:23:11"})
    plant_people(root, "a.png", ["Q" * 300])
    row = stored(root, run_plan(root, template_override=PEOPLE_TEMPLATE).plan_id)["a.png"]
    assert row["generated_filename"] == "2019-07-04_001.png"
    # The drop note ONLY: no "Names people" sentence for a token that is not in the name.
    assert reasons_of(row) == [PEOPLE_DROP]
    assert "people" not in tokens_of(row)


# ------------------------------------------------------ Step 6: the +5, on emitted only


def test_people_present_adds_five_and_is_visible_on_an_item_with_a_deduction(tmp_path):
    """Spec 6.4: "people present (user-confirmed) +5". A PNG with no camera date takes its
    date from `file_mtime` (-25), so the +5 is not clamped away: 75 without, 80 with."""
    root = scanned(tmp_path, {"a.png": None})
    plant_people(root, "a.png", ["Emma"])
    with_people = stored(root, run_plan(root, template_override=PEOPLE_TEMPLATE).plan_id)
    without = stored(root, run_plan(root, template_override=PLAIN_TEMPLATE).plan_id)
    assert without["a.png"]["trust_score"] == 75
    assert with_people["a.png"]["trust_score"] == 80


def test_people_NOT_in_the_name_earn_no_five(tmp_path):
    # N2: a claim the name does not make is charged nothing and credited nothing.
    root = scanned(tmp_path, {"a.png": None})
    plant_people(root, "a.png", ["Q" * 300])  # dropped by the path budget
    row = stored(root, run_plan(root, template_override=PEOPLE_TEMPLATE).plan_id)["a.png"]
    assert row["trust_score"] == 75


# ------------------------------------------------ Step 7: A16, the unreachable rule


def test_A16_no_stale_people_rule_exists():
    # Spec 6.4 rev 22 (A16): unreachable in v1.0, implement NOTHING. The rules are named.
    from pam.naming import consistency

    assert [rule.__name__ for rule in consistency.RULES] == [
        "_video_date_ambiguous",
        "_truncated_token",
        "_caption_contradicts_geocode",
    ]


# ------------------------------------ Step 8: people SEARCH - exactly the set {people} reads
#
# Owner ruling 2026-09-17: search matches the same people `{people}` would name - the same
# read, the same floor - so a hidden person, a reviewed-away face, a below-floor face and an
# unnamed cluster are unsearchable. PLAIN_TEMPLATE keeps every name out of the proposed
# filename, so a match here can only have come through the people operand.


def search(root, q, floor=FLOOR):
    """`{old rel_path}` the review API's `q` selects, through the real `plan_rows`."""
    plan_id = run_plan(root, template_override=PLAIN_TEMPLATE).plan_id
    conn = _conn(root)
    try:
        items = repo.plan_rows(conn, plan_id, floor=floor)
    finally:
        conn.close()
    return {item.old_rel_path for item in repo.select_items(items, repo.ItemFilter(q=q))}


TWO_FILES = {"a.png": "2019:07:04 18:23:11", "b.png": "2019:07:05 18:23:11"}


def test_q_finds_a_photograph_by_a_persons_label_case_insensitively(tmp_path):
    root = scanned(tmp_path, TWO_FILES)
    name_face(root, "a.png", "Emma")
    assert search(root, "emma") == {"a.png"}
    assert search(root, "EMM") == {"a.png"}


def test_q_finds_a_photograph_by_a_persons_FILENAME_LABEL_too(tmp_path):
    # BOTH values are searchable (owner ruling): the one the user typed as the name, and the
    # one the filename carries.
    root = scanned(tmp_path, TWO_FILES)
    name_face(root, "a.png", "Jonathan", filename_label="Jon-Smith")
    assert search(root, "jonathan") == {"a.png"}
    assert search(root, "smith") == {"a.png"}


def test_q_does_not_find_a_HIDDEN_person(tmp_path):
    root = scanned(tmp_path, TWO_FILES)
    name_face(root, "a.png", "Secret", hidden=1)
    name_face(root, "b.png", "Emma")
    assert search(root, "secret") == set()
    assert search(root, "emma") == {"b.png"}


@pytest.mark.parametrize("state", SPEC_REVIEW_STATES)
def test_q_does_not_find_a_reviewed_away_face(tmp_path, state):
    root = scanned(tmp_path, TWO_FILES)
    conn = _conn(root)
    try:
        emma = plant_person(conn, "Emma")
        plant_face(conn, sha_of(root, "a.png"), person_id=emma, review_state=state)
        plant_face(conn, sha_of(root, "b.png"), person_id=emma)
    finally:
        conn.close()
    assert search(root, "emma") == {"b.png"}


def test_q_does_not_find_a_below_floor_face_and_reads_the_floor_it_is_given(tmp_path):
    root = scanned(tmp_path, TWO_FILES)
    conn = _conn(root)
    try:
        emma = plant_person(conn, "Emma")
        plant_face(conn, sha_of(root, "a.png"), person_id=emma, conf=0.39)
        plant_face(conn, sha_of(root, "b.png"), person_id=emma, conf=0.9)
    finally:
        conn.close()
    assert search(root, "emma") == {"b.png"}
    assert search(root, "emma", floor=0.3) == {"a.png", "b.png"}


def test_q_does_not_find_an_UNNAMED_cluster(tmp_path):
    root = scanned(tmp_path, TWO_FILES)
    conn = _conn(root)
    try:
        emma = plant_person(conn, "Emma")
        plant_face(conn, sha_of(root, "a.png"), person_id=emma, cluster_id=3)
        plant_face(conn, sha_of(root, "b.png"), cluster_id=3)
    finally:
        conn.close()
    assert search(root, "emma") == {"a.png"}


def test_plan_rows_has_no_default_floor(tmp_path):
    # The floor is A11's and lives in config; a default here would be a second place it
    # lives, and the one read when a caller forgot (faces.py's module docstring).
    root = scanned(tmp_path, TWO_FILES)
    plan_id = run_plan(root, template_override=PLAIN_TEMPLATE).plan_id
    conn = _conn(root)
    try:
        with pytest.raises(TypeError):
            repo.plan_rows(conn, plan_id)
    finally:
        conn.close()
