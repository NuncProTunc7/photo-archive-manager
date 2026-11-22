"""`catalog/repo/faces.py`: the SQL over `face_instances` and `persons` (M11 Task 8).

EVERY EXPECTATION HERE IS A LITERAL. The rows are inserted by hand with the suite's own
SQL and read back through the repo functions, or written through the repo functions and
read back with the suite's own SQL - never both ends through the same production query,
which is the shape M5's `repo.open_plan` and M6's `repo.ENRICHERS` were.

THE FLOOR IS THE SUBJECT OF HALF THIS MODULE (A11). Amendment A11 moved the detection
threshold from WRITE time to READ time precisely so that raising and lowering it are both
reversible, and the only way to measure that is to read the same stored rows at two
different floors and see the answer move in both directions. A test that only raised the
floor would pass against an implementation that dropped sub-floor detections on insert -
which is exactly the defect A11 exists to undo.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from pam.catalog import repo
from pam.catalog.db import connect, migrate

NOW = "2026-09-10T10:00:00"

# A13's vocabulary, transcribed from spec 4 rev 22's column comment - NOT read out of
# `repo.REVIEW_STATES`, which is the constant this pins.
SPEC_REVIEW_STATES = ("not_a_face", "dismissed")

GEOMETRY = {"v": 1, "bbox": [0.1, 0.2, 0.3, 0.4], "source_w": 100, "source_h": 100}


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "catalog.db")
    migrate(conn)
    yield conn
    conn.close()


def plant_face(
    conn,
    sha,
    *,
    confidence=0.9,
    cluster_id=None,
    person_id=None,
    review_state=None,
    model=("yunet+sface", "1"),
    embedding=b"\x01\x02",
    geometry=None,
):
    """One `face_instances` row, with the SUITE's own INSERT.

    Planted rather than produced by `insert_face`, so a test of `insert_face` and a test
    of a read are never each other's oracle.
    """
    cur = conn.execute(
        "INSERT INTO face_instances (sha256, bbox_json, embedding, det_confidence,"
        " cluster_id, person_id, review_state, model_name, model_version)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sha,
            json.dumps(GEOMETRY if geometry is None else geometry, sort_keys=True),
            embedding,
            confidence,
            cluster_id,
            person_id,
            review_state,
            model[0],
            model[1],
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def plant_person(conn, label, *, filename_label=None, hidden=0):
    cur = conn.execute(
        "INSERT INTO persons (label, filename_label, hidden) VALUES (?, ?, ?)",
        (label, filename_label, hidden),
    )
    conn.commit()
    return int(cur.lastrowid)


def make_plan(conn):
    """A plan row, so the items below have somewhere to hang."""
    return repo.create_plan(
        conn,
        now=NOW,
        template="{date}",
        config_json="{}",
        app_version="test",
        schema_version=7,
    )


def plant_item(conn, plan_id, file_id, tokens_json):
    """One `plan_items` row, with the SUITE's own INSERT and spec 4's real columns."""
    conn.execute(
        "INSERT INTO plan_items (plan_id, file_id, old_rel_path, generated_filename,"
        " new_filename, tokens_json, tier, trust_score, trust_reasons_json, status,"
        " unit_key)"
        " VALUES (?, ?, 'o.jpg', 'g.jpg', 'n.jpg', ?, 'high', 90, '[]', 'proposed', 'u')",
        (plan_id, file_id, tokens_json),
    )


def plant_file(conn, name="o.jpg"):
    file_id = repo.upsert_file(
        conn,
        rel_path=name,
        filename=name,
        ext="jpg",
        media_type="image",
        size=10,
        mtime_ns=1,
        placeholder=False,
        now=NOW,
    )
    conn.commit()
    return file_id


def raw_faces(conn):
    """`{id: (cluster_id, person_id, review_state)}` with the suite's own SQL."""
    return {
        int(r["id"]): (r["cluster_id"], r["person_id"], r["review_state"])
        for r in conn.execute(
            "SELECT id, cluster_id, person_id, review_state FROM face_instances"
        )
    }


# --- the vocabulary -------------------------------------------------------------------


def test_the_review_state_vocabulary_is_A13s_and_NULL_is_not_in_it():
    """Spelled from spec 4 rev 22, not read off the tuple it checks.

    NULL is the fourth state and is deliberately absent: it is the ABSENCE of a review,
    and a tuple that named it would invite `review_state = 'none'` reaching the column,
    where it would exclude a face from clustering and from `{people}` for ever.
    """
    assert repo.REVIEW_STATES == SPEC_REVIEW_STATES
    assert None not in repo.REVIEW_STATES
    assert "none" not in repo.REVIEW_STATES


def test_a_review_state_outside_the_vocabulary_is_refused(db):
    face = plant_face(db, "sha-a")
    with pytest.raises(ValueError, match="not a review state"):
        repo.set_review_state(db, [face], "hidden")
    assert raw_faces(db)[face] == (None, None, None)


# --- insert_face ----------------------------------------------------------------------


def test_insert_face_stores_the_row_and_leaves_all_three_user_columns_NULL(db):
    """`cluster_id`, `person_id` and `review_state` are not an enricher's to set.

    A detection is not clustered until a pass runs, is not a person until a human says
    so (I2), and has not been reviewed until a human reviews it.
    """
    face_id = repo.insert_face(
        db,
        sha256="sha-a",
        bbox_json=json.dumps(GEOMETRY, sort_keys=True),
        embedding=b"\x07\x08",
        det_confidence=0.42,
        model_name="yunet+sface",
        model_version="gen-1",
    )
    db.commit()
    row = db.execute("SELECT * FROM face_instances WHERE id = ?", (face_id,)).fetchone()
    assert (
        row["sha256"],
        row["bbox_json"],
        bytes(row["embedding"]),
        row["det_confidence"],
        row["model_name"],
        row["model_version"],
        row["cluster_id"],
        row["person_id"],
        row["review_state"],
    ) == (
        "sha-a",
        json.dumps(GEOMETRY, sort_keys=True),
        b"\x07\x08",
        0.42,
        "yunet+sface",
        "gen-1",
        None,
        None,
        None,
    )


def test_insert_face_stores_a_detection_below_every_plausible_floor(db):
    """A11 at the WRITE end: `insert_face` takes no threshold and must never grow one.

    0.05 is below `[tiers] faces.floor`'s shipped 0.4 by a wide margin. If storage ever
    filtered, lowering the threshold could not recover the row and the only remedy would
    be re-enriching the archive - the "documented control that only worked once" A11 was
    written to remove.
    """
    repo.insert_face(
        db,
        sha256="sha-a",
        bbox_json="{}",
        embedding=b"\x00",
        det_confidence=0.05,
        model_name="m",
        model_version="1",
    )
    db.commit()
    assert [
        r["det_confidence"] for r in db.execute("SELECT det_confidence FROM face_instances")
    ] == [0.05]


# --- A11's read-time floor, in BOTH directions ----------------------------------------


def test_the_floor_hides_and_then_recovers_the_same_stored_row(db):
    """A11's whole point, measured in both directions over ONE set of stored rows.

    Nothing is re-enriched between the two reads: the sub-floor detection is on disk the
    whole time. Raising the floor hides it and lowering the floor brings it back, which
    is what "raising and lowering the threshold are both reversible" means.
    """
    high = plant_face(db, "sha-a", confidence=0.95)
    low = plant_face(db, "sha-b", confidence=0.30)

    assert [f.id for f in repo.faces_for_clustering(db, 0.4)] == [high]
    assert [f.id for f in repo.faces_for_clustering(db, 0.1)] == [high, low]
    assert [f.id for f in repo.faces_for_clustering(db, 0.99)] == []


def test_faces_for_clustering_skips_a_named_face_and_a_reviewed_one(db):
    """Three filters, three different promises (I2, I3, A13).

    Each is measured on its own row, so a mutation that deleted one of the three cannot
    be masked by another still excluding the same face.
    """
    person = plant_person(db, "Emma")
    free = plant_face(db, "sha-a")
    plant_face(db, "sha-b", person_id=person)
    plant_face(db, "sha-c", review_state="not_a_face")
    plant_face(db, "sha-d", review_state="dismissed")

    assert [f.id for f in repo.faces_for_clustering(db, 0.4)] == [free]


def test_a_face_row_carries_its_parsed_geometry_and_its_raw_embedding(db):
    face = plant_face(db, "sha-a", embedding=b"\x10\x20\x30", confidence=0.77)
    (row,) = repo.faces_for_clustering(db, 0.4)
    assert row.id == face
    assert row.sha256 == "sha-a"
    assert row.geometry == GEOMETRY
    assert row.embedding == b"\x10\x20\x30"
    assert row.det_confidence == 0.77


def test_a_face_whose_geometry_is_not_a_document_reads_as_an_empty_one(db):
    """Skipped rather than raised on: a hand-edited catalog must not brick the page."""
    plant_face(db, "sha-a", geometry=None)
    db.execute("UPDATE face_instances SET bbox_json = 'not json'")
    db.commit()
    assert repo.faces_for_clustering(db, 0.4)[0].geometry == {}


# --- clusters -------------------------------------------------------------------------


def test_clusters_summary_counts_faces_and_contents_separately(db):
    """`contents` is DISTINCT sha256, and the two numbers differ on an archive of real files.

    Two detections of one person in ONE photograph are two faces and one content. A
    summary that reported the same number for both would tell a user a cluster spans two
    photographs when it spans one.
    """
    plant_face(db, "sha-a", cluster_id=1)
    plant_face(db, "sha-a", cluster_id=1)
    plant_face(db, "sha-b", cluster_id=1)
    plant_face(db, "sha-c", cluster_id=2)
    plant_face(db, "sha-d")  # unclustered: belongs to no row here

    assert [(c.cluster_id, c.faces, c.contents) for c in repo.clusters_summary(db, 0.4)] == [
        (1, 3, 2),
        (2, 1, 1),
    ]


def test_clusters_summary_names_the_person_when_the_cluster_has_one(db):
    person = plant_person(db, "Emma")
    plant_face(db, "sha-a", cluster_id=1, person_id=person)
    (row,) = repo.clusters_summary(db, 0.4)
    assert (row.person_id, row.person_label) == (person, "Emma")


def test_cluster_faces_excludes_reviewed_faces_and_sub_floor_ones(db):
    keep = plant_face(db, "sha-a", cluster_id=1, confidence=0.9)
    plant_face(db, "sha-b", cluster_id=1, confidence=0.1)
    plant_face(db, "sha-c", cluster_id=1, review_state="dismissed")
    assert [f.id for f in repo.cluster_faces(db, 1, 0.4)] == [keep]


def test_set_cluster_moves_only_the_faces_named(db):
    a = plant_face(db, "sha-a")
    b = plant_face(db, "sha-b")
    repo.set_cluster(db, [(a, 7)])
    db.commit()
    assert raw_faces(db) == {a: (7, None, None), b: (None, None, None)}


def test_set_cluster_can_unassign(db):
    a = plant_face(db, "sha-a", cluster_id=7)
    repo.set_cluster(db, [(a, None)])
    db.commit()
    assert raw_faces(db)[a] == (None, None, None)


# --- naming a cluster, and correcting one face ----------------------------------------


def test_set_person_names_every_face_in_the_cluster_but_not_a_reviewed_one(db):
    """Spec 8.2's `/faces/cluster/{id}/label`, and A13's exclusion inside it.

    A dismissed face is excluded from the cluster everywhere it is displayed, so naming
    it here would put a person on a face the user has already rejected - and `{people}`
    reads `review_state IS NULL`, so the name would be stored and never used.
    """
    person = plant_person(db, "Emma")
    a = plant_face(db, "sha-a", cluster_id=1)
    b = plant_face(db, "sha-b", cluster_id=1)
    rejected = plant_face(db, "sha-c", cluster_id=1, review_state="not_a_face")
    other = plant_face(db, "sha-d", cluster_id=2)
    # S2-F1 (Task 11 Stage 2): a face ALREADY NAMED in the same cluster is not re-named.
    kept = plant_person(db, "Blair")
    named = plant_face(db, "sha-e", cluster_id=1, person_id=kept)

    assert repo.set_person(db, 1, person, 0.4) == 2
    db.commit()
    assert raw_faces(db) == {
        a: (1, person, None),
        b: (1, person, None),
        rejected: (1, None, "not_a_face"),
        other: (2, None, None),
        named: (1, kept, None),
    }
    assert repo.cluster_faces(db, 1, 0.4) == [], (
        "every face is named now, and a named face is not an unnamed cluster's face"
    )


def test_set_person_names_only_faces_at_the_floor_and_the_floor_is_inclusive(db):
    """A11's floor inside the labeling write (Task 10's defect, closed in Task 11).

    `cluster_faces` shows a cluster AT the floor, so the write names exactly that set: a
    below-floor detection in the same cluster - what a raised floor leaves behind - keeps
    no person, and a face sitting exactly ON the floor is named, because `cluster_faces`
    shows it (`>=`). Lowering the floor later brings the unnamed face back as unnamed.
    """
    person = plant_person(db, "Emma")
    above = plant_face(db, "sha-a", cluster_id=1, confidence=0.9)
    on = plant_face(db, "sha-b", cluster_id=1, confidence=0.4)
    below = plant_face(db, "sha-c", cluster_id=1, confidence=0.39)

    assert repo.set_person(db, 1, person, 0.4) == 2
    db.commit()
    faces = raw_faces(db)
    assert (faces[above], faces[on], faces[below]) == (
        (1, person, None),
        (1, person, None),
        (1, None, None),
    )


def test_assign_faces_moves_named_faces_and_can_unassign_them(db):
    """I3: every correction is reversible, including this one."""
    emma = plant_person(db, "Emma")
    jo = plant_person(db, "Jo")
    a = plant_face(db, "sha-a", person_id=emma)
    b = plant_face(db, "sha-b", person_id=emma)

    assert repo.assign_faces(db, [a], jo) == 1
    db.commit()
    assert raw_faces(db)[a] == (None, jo, None)

    assert repo.assign_faces(db, [a, b], None) == 2
    db.commit()
    assert {i: v[1] for i, v in raw_faces(db).items()} == {a: None, b: None}


def test_assign_faces_and_set_review_state_do_nothing_on_an_empty_list(db):
    a = plant_face(db, "sha-a")
    assert repo.assign_faces(db, [], 1) == 0
    assert repo.set_review_state(db, [], "dismissed") == 0
    assert raw_faces(db)[a] == (None, None, None)


def test_set_review_state_marks_and_then_clears(db):
    """I3 again: dismissing must not be a one-way door (vision R6)."""
    a = plant_face(db, "sha-a")
    assert repo.set_review_state(db, [a], "dismissed") == 1
    db.commit()
    assert raw_faces(db)[a] == (None, None, "dismissed")
    assert repo.set_review_state(db, [a], None) == 1
    db.commit()
    assert raw_faces(db)[a] == (None, None, None)


def test_ignored_faces_is_every_reviewed_face_at_any_confidence(db):
    """NO FLOOR here, deliberately: this is the page that exists to undo a dismissal.

    A face hidden by a threshold change would be unrestorable from the one surface built
    to restore it, which is the one-way door vision R6 forbids.
    """
    dismissed = plant_face(db, "sha-a", review_state="dismissed", confidence=0.01)
    not_a_face = plant_face(db, "sha-b", review_state="not_a_face", confidence=0.99)
    plant_face(db, "sha-c")
    assert sorted(f.id for f in repo.ignored_faces(db)) == sorted([dismissed, not_a_face])


# --- persons --------------------------------------------------------------------------


def test_persons_all_counts_faces_and_contents_and_keeps_an_emptied_person(db):
    """A LEFT JOIN, so a person whose faces were all reassigned still has a row.

    That row is the one a user needs in order to merge or delete them; an INNER JOIN
    would make an emptied person vanish from the only page that can clean them up.
    """
    emma = plant_person(db, "Emma", filename_label="Em")
    empty = plant_person(db, "Alice", hidden=1)
    plant_face(db, "sha-a", person_id=emma)
    plant_face(db, "sha-a", person_id=emma)
    plant_face(db, "sha-b", person_id=emma)

    rows = {p.label: p for p in repo.persons_all(db)}
    assert (rows["Emma"].id, rows["Emma"].faces, rows["Emma"].contents) == (emma, 3, 2)
    assert (rows["Alice"].id, rows["Alice"].faces, rows["Alice"].hidden) == (empty, 0, True)


def test_the_filename_value_prefers_the_override_and_falls_back_to_the_label(db):
    """A14, as one property: `filename_label` when set and non-empty, else `label`.

    The blank-string case is not decoration. A user who clears the override in the UI
    leaves `''` behind, and `'' or label` is the difference between a filename reading
    `Jonathan` and a filename with an empty token in it.
    """
    plant_person(db, "Jonathan", filename_label="John")
    plant_person(db, "Emma", filename_label="")
    plant_person(db, "Ada", filename_label=None)
    plant_person(db, "Bo", filename_label="   ")
    values = {p.label: p.filename_value for p in repo.persons_all(db)}
    assert values == {"Jonathan": "John", "Emma": "Emma", "Ada": "Ada", "Bo": "Bo"}


def test_upsert_person_matches_case_insensitively_and_keeps_the_first_spelling(db):
    """Spec 8.2: "so `Emma` and `emma` do not become two people".

    `persons.label` is UNIQUE but SQLite's default collation is case-SENSITIVE, so the
    constraint alone would happily hold both rows.
    """
    first = repo.upsert_person(db, "Emma")
    db.commit()
    assert repo.upsert_person(db, "emma") == first
    assert repo.upsert_person(db, "EMMA") == first
    db.commit()
    assert [r["label"] for r in db.execute("SELECT label FROM persons")] == ["Emma"]


def test_upsert_person_creates_a_row_when_the_label_is_new(db):
    a = repo.upsert_person(db, "Emma")
    b = repo.upsert_person(db, "Jo")
    db.commit()
    assert a != b
    assert sorted(r["label"] for r in db.execute("SELECT label FROM persons")) == [
        "Emma",
        "Jo",
    ]


def test_update_person_changes_only_what_it_is_given(db):
    person = plant_person(db, "Emma", filename_label="Em", hidden=0)
    repo.update_person(db, person, hidden=True)
    db.commit()
    row = db.execute("SELECT * FROM persons WHERE id = ?", (person,)).fetchone()
    assert (row["label"], row["filename_label"], row["hidden"]) == ("Emma", "Em", 1)


def test_update_person_can_clear_the_filename_override(db):
    """The reason the sentinel exists: `None` is a legal new value, not "leave alone"."""
    person = plant_person(db, "Emma", filename_label="Em")
    repo.update_person(db, person, filename_label=None)
    db.commit()
    row = db.execute("SELECT * FROM persons WHERE id = ?", (person,)).fetchone()
    assert (row["label"], row["filename_label"]) == ("Emma", None)


def test_update_person_with_nothing_to_change_writes_nothing(db):
    person = plant_person(db, "Emma", filename_label="Em")
    repo.update_person(db, person)
    db.commit()
    row = db.execute("SELECT * FROM persons WHERE id = ?", (person,)).fetchone()
    assert (row["label"], row["filename_label"], row["hidden"]) == ("Emma", "Em", 0)


def test_merge_person_moves_the_faces_and_deletes_the_emptied_row(db):
    """Spec 8.2: it MOVES FACES; it does not rename."""
    emma = plant_person(db, "Emma")
    em = plant_person(db, "Em")
    a = plant_face(db, "sha-a", person_id=em)
    b = plant_face(db, "sha-b", person_id=emma)

    assert repo.merge_person(db, em, emma) == 1
    db.commit()
    assert {i: v[1] for i, v in raw_faces(db).items()} == {a: emma, b: emma}
    assert [r["label"] for r in db.execute("SELECT label FROM persons")] == ["Emma"]


def test_merging_a_person_with_themselves_is_refused(db):
    """The docstring said this cannot happen; here is the test that says so.

    It would delete the row every face had just been "moved" to, taking the person and
    their label with it - a data loss with no error message.
    """
    emma = plant_person(db, "Emma")
    face = plant_face(db, "sha-a", person_id=emma)
    with pytest.raises(ValueError, match="with themselves"):
        repo.merge_person(db, emma, emma)
    db.commit()
    assert raw_faces(db)[face][1] == emma
    assert [r["label"] for r in db.execute("SELECT label FROM persons")] == ["Emma"]


# --- generations, export, purge -------------------------------------------------------


def test_model_generations_reports_each_identity_with_its_face_count(db):
    """A6's input: one archive, one face-model generation - so two rows is the refusal."""
    plant_face(db, "sha-a", model=("yunet+sface", "1"))
    plant_face(db, "sha-b", model=("yunet+sface", "1"))
    plant_face(db, "sha-c", model=("yunet+sface", "2"))
    assert [(g.model_name, g.model_version, g.faces) for g in repo.model_generations(db)] == [
        ("yunet+sface", "1", 2),
        ("yunet+sface", "2", 1),
    ]


def test_model_generations_is_empty_on_an_archive_with_no_faces(db):
    assert repo.model_generations(db) == []


def test_export_faces_holds_every_row_including_the_rejected_and_the_sub_floor(db):
    """A20 is a DATA-ACCESS export: it is what pam holds, not what pam believes.

    A threshold or a rejection is pam's reading of the data rather than a reason to
    withhold a row, so `review_state` is exported as a FIELD instead of as a filter.
    """
    emma = plant_person(db, "Emma")
    named = plant_face(db, "sha-a", person_id=emma, confidence=0.9)
    faint = plant_face(db, "sha-b", confidence=0.01)
    rejected = plant_face(db, "sha-c", review_state="not_a_face")

    rows = repo.export_faces(db)
    assert [r.id for r in rows] == [named, faint, rejected]
    assert [r.person_label for r in rows] == ["Emma", None, None]
    assert [r.review_state for r in rows] == [None, None, "not_a_face"]
    assert rows[0].geometry == GEOMETRY


def test_purge_faces_empties_both_tables_and_reports_the_two_counts(db):
    emma = plant_person(db, "Emma")
    plant_person(db, "Jo")
    plant_face(db, "sha-a", person_id=emma)
    plant_face(db, "sha-b")

    assert repo.purge_faces(db) == (2, 2)
    db.commit()
    assert db.execute("SELECT COUNT(*) AS n FROM face_instances").fetchone()["n"] == 0
    assert db.execute("SELECT COUNT(*) AS n FROM persons").fetchone()["n"] == 0


def test_purge_faces_deletes_the_faces_before_the_persons_they_point_at(db):
    """A foreign key, not a preference. `catalog/db.py` sets `PRAGMA foreign_keys=ON`.

    Emptying `persons` while a face still references one is refused by SQLite, so the
    order inside `purge_faces` is load-bearing. This asserts the constraint is really on,
    so that the ordering is protected by the database and not only by a comment.
    """
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    emma = plant_person(db, "Emma")
    plant_face(db, "sha-a", person_id=emma)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM persons")
    db.rollback()
    assert repo.purge_faces(db) == (1, 1)


# --- A19's inventory, which lives in `plan_items.py` -----------------------------------


def test_plan_items_with_people_tokens_finds_items_by_provenance_not_by_text(db):
    """A19: the inventory is made "independently of label text".

    The three planted items are the three readings that matter: one whose token names
    `persons` as its source table, one whose token is a place with the SAME VALUE (so a
    text search for "Emma" would find it), and one with no tokens at all. Only the first
    is affected by a purge.
    """
    conn = db
    plan_a = make_plan(conn)
    plan_b = make_plan(conn)
    people = json.dumps(
        [{"token": "{people}", "value": "Emma", "source_table": "persons", "source_id": 4}]
    )
    place = json.dumps(
        [
            {
                "token": "{place}",
                "value": "Emma",
                "source_table": "inferred_facts",
                "source_id": 9,
            }
        ]
    )
    file_id = plant_file(conn)
    for plan_id, tokens in (
        (plan_a, people),
        (plan_a, people),
        (plan_a, place),
        (plan_b, people),
    ):
        plant_item(conn, plan_id, file_id, tokens)
    plant_item(conn, plan_b, file_id, "[]")
    conn.commit()

    assert repo.plan_items_with_people_tokens(conn) == {plan_a: 2, plan_b: 1}


def test_plan_items_with_people_tokens_is_empty_when_no_name_came_from_a_person(db):
    plan_id = make_plan(db)
    plant_item(db, plan_id, plant_file(db), "not json")
    db.commit()
    assert repo.plan_items_with_people_tokens(db) == {}


def test_the_people_provenance_marker_is_the_spec_s_source_table(db):
    """Spec 6.1 as amended by A14: `source_table='persons'`. A literal, from the spec."""
    assert repo.PEOPLE_SOURCE_TABLE == "persons"


# --- the anti-deletion floor ----------------------------------------------------------

LOAD_BEARING_CONTROLS = {
    "test_the_floor_hides_and_then_recovers_the_same_stored_row": (
        "A11 in BOTH directions over one set of stored rows. A raise-only test passes "
        "against an implementation that filters at WRITE time, which is the defect A11 "
        "exists to undo"
    ),
    "test_insert_face_stores_a_detection_below_every_plausible_floor": (
        "the write end of A11: `insert_face` takes no threshold and must never grow one"
    ),
    "test_set_person_names_every_face_in_the_cluster_but_not_a_reviewed_one": (
        "A13's exclusion inside the labeling action - the one place a rejected face "
        "could still acquire a person"
    ),
    "test_set_person_names_only_faces_at_the_floor_and_the_floor_is_inclusive": (
        "A11 inside the labeling write: without the floor a raised threshold leaves a "
        "cluster unnameable, and a strict `>` drops the face the page shows on the floor"
    ),
    "test_upsert_person_matches_case_insensitively_and_keeps_the_first_spelling": (
        "spec 8.2's `Emma`/`emma`. UNIQUE alone does not give it: SQLite's default "
        "collation is case-sensitive"
    ),
    "test_merging_a_person_with_themselves_is_refused": (
        "the docstring's 'this cannot happen', written as a test. Without it the merge "
        "deletes the person every face was just moved to"
    ),
    "test_plan_items_with_people_tokens_finds_items_by_provenance_not_by_text": (
        "A19's 'independently of label text'. The place-token row with the SAME VALUE "
        "is what makes it a test of provenance rather than of search"
    ),
}

assert LOAD_BEARING_CONTROLS, "this module's control list is empty; it protects nothing"


def test_no_load_bearing_control_in_this_file_can_be_deleted_without_saying_so():
    """The floor under this module's inventory (M10 condition C4)."""
    present = {name for name in globals() if name.startswith("test_")}
    missing = sorted(set(LOAD_BEARING_CONTROLS) - present)
    assert missing == [], (
        f"a load-bearing control has been deleted from {Path(__file__).name}:"
        f" {missing}. Each is why another assertion here means anything"
        f" ({[LOAD_BEARING_CONTROLS[m] for m in missing]})."
    )
