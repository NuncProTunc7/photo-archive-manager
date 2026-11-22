"""Spec 8.2's `/faces/*` WRITE routes other than split, and `/faces/ignored` (M11 Task 11
Step 1, amendment A18).

EVERY EXPECTATION HERE IS SPELLED FROM THE SPEC, and every catalog row is planted and read
back with the suite's own SQL (`tests/test_webui_faces.py`'s rule), so nothing is checked
through the production query that wrote it. `review_state`'s vocabulary in particular is
written out as literals below and never read off `repo.REVIEW_STATES`: a test built from
that tuple would pass for any value of it (the ENRICHERS lesson, M6).
"""

import sqlite3
from pathlib import Path
from typing import Any

from test_webui_faces import Served, _conn, archive, plant_face, plant_person, sha_of

# A18's two non-NULL review states, spelled from the spec. NOT `repo.REVIEW_STATES`.
NOT_A_FACE = "not_a_face"
DISMISSED = "dismissed"


def rows(root: Path, sql: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    conn = _conn(root)
    try:
        return list(conn.execute(sql, args).fetchall())
    finally:
        conn.close()


def face_states(root: Path) -> dict[int, tuple[Any, Any]]:
    """`face id -> (review_state, person_id)`, read with the suite's own SQL."""
    return {
        int(r["id"]): (r["review_state"], r["person_id"])
        for r in rows(root, "SELECT id, review_state, person_id FROM face_instances")
    }


def person_rows(root: Path) -> dict[int, tuple[Any, Any, Any]]:
    """`person id -> (label, filename_label, hidden)`, with the suite's own SQL."""
    return {
        int(r["id"]): (r["label"], r["filename_label"], r["hidden"])
        for r in rows(root, "SELECT id, label, filename_label, hidden FROM persons")
    }


def two_photographs(tmp_path) -> Path:
    """An archive of two photographs with DIFFERENT contents, so a count of photographs
    can tell 'distinct content' from 'number of faces'."""
    root = archive(tmp_path, names=("a.jpg", "b.jpg"))
    conn = _conn(root)
    try:
        conn.execute("UPDATE files SET sha256 = 'sha-b' WHERE filename = 'b.jpg'")
        conn.commit()
    finally:
        conn.close()
    return root


def call(root: Path, method: str, path: str, body: Any = None):
    live = Served(root)
    try:
        return live.json(method, path, body)
    finally:
        live.close()


# --- POST faces/faces/state ----------------------------------------------------------


def test_state_distinguishes_not_a_face_from_dismissed(tmp_path):
    """The vocabulary is two words, and a test that could not tell them apart would leave
    both untested: each face must end in the state it was sent, not merely 'non-NULL'."""
    root = two_photographs(tmp_path)
    first = plant_face(root, sha_of(root, "a.jpg"), cluster_id=1)
    second = plant_face(root, sha_of(root, "a.jpg"), cluster_id=1)
    third = plant_face(root, "sha-b", cluster_id=1)

    status_a, body_a = call(
        root, "POST", "/faces/faces/state", {"face_ids": [first], "review_state": NOT_A_FACE}
    )
    status_b, body_b = call(
        root,
        "POST",
        "/faces/faces/state",
        {"face_ids": [second, third], "review_state": DISMISSED},
    )

    assert (status_a, status_b) == (200, 200)
    states = face_states(root)
    assert states[first][0] == NOT_A_FACE
    assert states[second][0] == DISMISSED
    assert states[third][0] == DISMISSED
    assert (body_a["faces"], body_a["photographs"]) == (1, 1)
    assert (body_b["faces"], body_b["photographs"]) == (2, 2)


def test_state_null_clears_a_review_state(tmp_path):
    """I3: every correction is reversible. `null` CLEARS; it is not a third state."""
    root = archive(tmp_path)
    sha = sha_of(root, "IMG_1.JPG")
    rejected = plant_face(root, sha, review_state=NOT_A_FACE)
    hidden = plant_face(root, sha, review_state=DISMISSED)

    status, body = call(
        root,
        "POST",
        "/faces/faces/state",
        {"face_ids": [rejected, hidden], "review_state": None},
    )

    assert status == 200 and body["faces"] == 2
    states = face_states(root)
    assert states[rejected][0] is None
    assert states[hidden][0] is None


def test_state_reports_the_people_whose_faces_it_changed(tmp_path):
    """6.2 rule 7: a named face marked away stops contributing to that person's name."""
    root = archive(tmp_path)
    sha = sha_of(root, "IMG_1.JPG")
    emma = plant_person(root, "Emma")
    face = plant_face(root, sha, person_id=emma)
    plant_face(root, sha)

    status, body = call(
        root, "POST", "/faces/faces/state", {"face_ids": [face], "review_state": DISMISSED}
    )

    assert status == 200
    assert body["people"] == 1


def test_state_refuses_an_out_of_vocabulary_state_by_naming_the_field(tmp_path):
    root = archive(tmp_path)
    face = plant_face(root, sha_of(root, "IMG_1.JPG"))

    for bad in ("none", "hidden", "", 1, True):
        status, body = call(
            root, "POST", "/faces/faces/state", {"face_ids": [face], "review_state": bad}
        )
        assert status == 400, bad
        assert body["error"].startswith("review_state:"), body
    status, body = call(root, "POST", "/faces/faces/state", {"face_ids": [face]})
    assert status == 400 and body["error"].startswith("review_state:")
    assert face_states(root)[face][0] is None


def test_state_refuses_empty_or_ill_typed_face_ids_by_naming_the_field(tmp_path):
    root = archive(tmp_path)
    face = plant_face(root, sha_of(root, "IMG_1.JPG"))

    for bad in ([], None, "1", [str(face)], [True], [1.0], {"id": face}):
        status, body = call(
            root, "POST", "/faces/faces/state", {"face_ids": bad, "review_state": DISMISSED}
        )
        assert status == 400, bad
        assert body["error"].startswith("face_ids:"), body
    assert face_states(root)[face][0] is None


def test_state_with_one_unknown_face_id_changes_nothing_at_all(tmp_path):
    """The house `decide` precedent: one bad id refuses the WHOLE batch, one transaction."""
    root = archive(tmp_path)
    face = plant_face(root, sha_of(root, "IMG_1.JPG"))

    status, body = call(
        root,
        "POST",
        "/faces/faces/state",
        {"face_ids": [face, face + 999], "review_state": DISMISSED},
    )

    assert status == 409
    assert str(face + 999) in body["error"]
    assert face_states(root)[face][0] is None


# --- POST faces/faces/assign ---------------------------------------------------------


def test_assign_moves_faces_to_a_person_matched_case_insensitively(tmp_path):
    root = two_photographs(tmp_path)
    emma = plant_person(root, "Emma")
    first = plant_face(root, sha_of(root, "a.jpg"))
    second = plant_face(root, "sha-b")
    untouched = plant_face(root, "sha-b")

    status, body = call(
        root, "POST", "/faces/faces/assign", {"face_ids": [first, second], "person": " emma "}
    )

    assert status == 200
    assert (body["person_id"], body["label"]) == (emma, "Emma")
    assert (body["faces"], body["photographs"]) == (2, 2)
    states = face_states(root)
    assert states[first][1] == emma and states[second][1] == emma
    assert states[untouched][1] is None
    assert len(person_rows(root)) == 1, "`emma` must not become a second person"


def test_assign_to_a_new_name_creates_the_person(tmp_path):
    root = archive(tmp_path)
    face = plant_face(root, sha_of(root, "IMG_1.JPG"))

    status, body = call(
        root, "POST", "/faces/faces/assign", {"face_ids": [face], "person": "Kofi"}
    )

    assert status == 200
    people = person_rows(root)
    assert [label for label, _, _ in people.values()] == ["Kofi"]
    assert face_states(root)[face][1] == body["person_id"]


def test_assign_with_a_null_person_clears_rather_than_sets(tmp_path):
    """I3: `person: null` UNASSIGNS. A null that created a person, or was ignored, would
    make a mistaken assignment permanent."""
    root = archive(tmp_path)
    sha = sha_of(root, "IMG_1.JPG")
    emma = plant_person(root, "Emma")
    kofi = plant_person(root, "Kofi")
    first = plant_face(root, sha, person_id=emma)
    second = plant_face(root, sha, person_id=kofi)

    status, body = call(
        root, "POST", "/faces/faces/assign", {"face_ids": [first, second], "person": None}
    )

    assert status == 200
    assert body["person_id"] is None
    assert body["faces"] == 2
    assert body["people"] == 2, "both Emma and Kofi lost a face"
    states = face_states(root)
    assert states[first][1] is None and states[second][1] is None
    assert len(person_rows(root)) == 2, "unassigning deletes no person"


def test_label_then_unassigning_its_face_ids_restores_every_face_and_no_other(tmp_path):
    """Task 11 Step 5's label undo: `label` returns the ids it wrote, and posting them to
    `assign` with `person: null` puts every one back to unnamed - touching no other face,
    neither one in another cluster nor one already named."""
    root = two_photographs(tmp_path)
    kofi = plant_person(root, "Kofi")
    labelled = [plant_face(root, sha_of(root, "a.jpg"), cluster_id=1)]
    labelled.append(plant_face(root, "sha-b", cluster_id=1))
    elsewhere = plant_face(root, "sha-b", cluster_id=2)
    named = plant_face(root, "sha-b", cluster_id=3, person_id=kofi)
    before = face_states(root)

    status, body = call(root, "POST", "/faces/cluster/1/label", {"person": "Emma"})
    assert status == 200
    assert sorted(body["face_ids"]) == sorted(labelled)
    assert all(face_states(root)[f][1] == body["person_id"] for f in labelled)

    status, _ = call(
        root, "POST", "/faces/faces/assign", {"face_ids": body["face_ids"], "person": None}
    )

    assert status == 200
    assert face_states(root) == before
    assert face_states(root)[elsewhere][1] is None and face_states(root)[named][1] == kofi


def test_assign_refuses_a_missing_or_blank_person_by_naming_the_field(tmp_path):
    root = archive(tmp_path)
    face = plant_face(root, sha_of(root, "IMG_1.JPG"))

    for body_in in ({"face_ids": [face]}, {"face_ids": [face], "person": "  "}):
        status, body = call(root, "POST", "/faces/faces/assign", body_in)
        assert status == 400, body_in
        assert body["error"].startswith("person:"), body
    status, body = call(root, "POST", "/faces/faces/assign", {"face_ids": [], "person": "Emma"})
    assert status == 400 and body["error"].startswith("face_ids:")
    assert person_rows(root) == {}


def test_assign_with_one_unknown_face_id_creates_and_moves_nothing(tmp_path):
    root = archive(tmp_path)
    face = plant_face(root, sha_of(root, "IMG_1.JPG"))

    status, _ = call(
        root, "POST", "/faces/faces/assign", {"face_ids": [face, face + 999], "person": "Emma"}
    )

    assert status == 409
    assert face_states(root)[face][1] is None
    assert person_rows(root) == {}, "a refused batch must not leave a new person behind"


# --- POST faces/person/{id} ----------------------------------------------------------


def test_person_rename_changes_the_label(tmp_path):
    root = archive(tmp_path)
    emma = plant_person(root, "Emma")
    plant_face(root, sha_of(root, "IMG_1.JPG"), person_id=emma)

    status, body = call(root, "POST", f"/faces/person/{emma}", {"label": "Emma Mensah"})

    assert status == 200
    assert person_rows(root)[emma] == ("Emma Mensah", None, 0)
    assert body["person"]["label"] == "Emma Mensah"
    assert (body["people"], body["faces"], body["photographs"]) == (1, 1, 1)


def test_person_rename_onto_another_persons_label_is_refused_not_merged(tmp_path):
    """`persons.label` is UNIQUE and matching is case-insensitive, so renaming Kofi to
    `emma` would make two people answer to one name. pam refuses and says to MERGE."""
    root = archive(tmp_path)
    emma = plant_person(root, "Emma")
    kofi = plant_person(root, "Kofi")
    face = plant_face(root, sha_of(root, "IMG_1.JPG"), person_id=kofi)

    for spelling in ("emma", "Emma"):
        status, body = call(root, "POST", f"/faces/person/{kofi}", {"label": spelling})
        assert status == 409, spelling
        assert "merge" in body["error"].lower()

    assert person_rows(root) == {emma: ("Emma", None, 0), kofi: ("Kofi", None, 0)}
    assert face_states(root)[face][1] == kofi


def test_person_rename_to_its_own_label_in_another_case_is_allowed(tmp_path):
    root = archive(tmp_path)
    emma = plant_person(root, "emma")

    status, _ = call(root, "POST", f"/faces/person/{emma}", {"label": "Emma"})

    assert status == 200
    assert person_rows(root)[emma][0] == "Emma"


def test_person_filename_label_is_set_and_cleared_without_touching_hidden(tmp_path):
    root = archive(tmp_path)
    emma = plant_person(root, "Emma")

    status, body = call(root, "POST", f"/faces/person/{emma}", {"filename_label": "EmmaM"})
    assert status == 200
    assert person_rows(root)[emma] == ("Emma", "EmmaM", 0)
    assert body["person"]["filename_value"] == "EmmaM"

    status, _ = call(root, "POST", f"/faces/person/{emma}", {"filename_label": None})
    assert status == 200
    assert person_rows(root)[emma] == ("Emma", None, 0)


def test_person_hidden_is_toggled_without_touching_the_filename_label(tmp_path):
    root = archive(tmp_path)
    emma = plant_person(root, "Emma")
    call(root, "POST", f"/faces/person/{emma}", {"filename_label": "EmmaM"})

    status, _ = call(root, "POST", f"/faces/person/{emma}", {"hidden": True})
    assert status == 200
    assert person_rows(root)[emma] == ("Emma", "EmmaM", 1)

    status, _ = call(root, "POST", f"/faces/person/{emma}", {"hidden": False})
    assert status == 200
    assert person_rows(root)[emma] == ("Emma", "EmmaM", 0)


def test_person_update_with_no_field_is_a_400_naming_the_body(tmp_path):
    root = archive(tmp_path)
    emma = plant_person(root, "Emma")

    status, body = call(root, "POST", f"/faces/person/{emma}", {})

    assert status == 400
    assert body["error"].startswith("body:")


def test_person_update_refuses_ill_typed_fields_by_name(tmp_path):
    root = archive(tmp_path)
    emma = plant_person(root, "Emma")

    for field, bad in (("label", "  "), ("label", None), ("hidden", 1), ("filename_label", 3)):
        status, body = call(root, "POST", f"/faces/person/{emma}", {field: bad})
        assert status == 400, (field, bad)
        assert body["error"].startswith(f"{field}:"), body
    assert person_rows(root)[emma] == ("Emma", None, 0)


def test_person_update_of_an_unknown_person_is_a_404_with_a_sentence(tmp_path):
    root = archive(tmp_path)

    status, body = call(root, "POST", "/faces/person/77", {"hidden": True})

    assert status == 404
    assert "77" in body["error"]


# --- POST faces/person/{id}/merge ----------------------------------------------------


def test_merge_moves_faces_and_keeps_the_surviving_persons_label(tmp_path):
    """MERGE MOVES FACES; IT NEVER RENAMES. Both people are named, so a merge implemented
    as 'rename the source onto the target' would be visible here: the surviving label is
    `into`'s, and the source's row is gone."""
    root = two_photographs(tmp_path)
    emma = plant_person(root, "Emma")
    em = plant_person(root, "Em")
    moved = [plant_face(root, sha_of(root, "a.jpg"), person_id=em) for _ in range(2)]
    moved.append(plant_face(root, "sha-b", person_id=em))
    kept = plant_face(root, "sha-b", person_id=emma)

    status, body = call(root, "POST", f"/faces/person/{em}/merge", {"into": emma})

    assert status == 200
    assert person_rows(root) == {emma: ("Emma", None, 0)}
    states = face_states(root)
    assert all(states[f][1] == emma for f in [*moved, kept])
    assert (body["faces"], body["photographs"], body["people"]) == (3, 2, 2)
    assert (body["merged"]["id"], body["merged"]["label"]) == (em, "Em")
    assert (body["into"]["id"], body["into"]["label"]) == (emma, "Emma")
    assert "Em" in body["message"] and "Emma" in body["message"]


def test_merge_with_oneself_is_refused(tmp_path):
    root = archive(tmp_path)
    emma = plant_person(root, "Emma")

    status, body = call(root, "POST", f"/faces/person/{emma}/merge", {"into": emma})

    assert status == 400
    assert body["error"].startswith("into:")
    assert emma in person_rows(root)


def test_merge_with_an_unknown_person_on_either_side_is_a_404(tmp_path):
    root = archive(tmp_path)
    emma = plant_person(root, "Emma")

    status_src, _ = call(root, "POST", "/faces/person/77/merge", {"into": emma})
    status_dst, body = call(root, "POST", f"/faces/person/{emma}/merge", {"into": 77})

    assert (status_src, status_dst) == (404, 404)
    assert "77" in body["error"]
    assert emma in person_rows(root)


def test_merge_refuses_an_ill_typed_into_by_naming_the_field(tmp_path):
    root = archive(tmp_path)
    emma = plant_person(root, "Emma")

    for bad in (None, "2", True):
        status, body = call(root, "POST", f"/faces/person/{emma}/merge", {"into": bad})
        assert status == 400, bad
        assert body["error"].startswith("into:")


def test_merge_refuses_true_as_into_when_person_one_is_somebody_else(tmp_path):
    """F5 (Stage 1, M1880): `True` is an `int` in Python, so without the `bool` operand
    `{"into": true}` reaches the handler as person id 1. The ill-typed test above cannot see
    that - its only person IS id 1, so the self-merge refusal answers the same 400. Here
    person 1 is Kofi and the merge is Emma's: the mutant merges Emma into Kofi, a
    destructive write with no undo, from a malformed body."""
    root = archive(tmp_path)
    kofi = plant_person(root, "Kofi")
    emma = plant_person(root, "Emma")
    assert kofi == 1 and emma != 1, "the fixture needs person 1 to be somebody else"
    face = plant_face(root, sha_of(root, "IMG_1.JPG"), person_id=emma)

    status, body = call(root, "POST", f"/faces/person/{emma}/merge", {"into": True})

    assert status == 400, body
    assert body["error"].startswith("into:")
    assert set(person_rows(root)) == {kofi, emma}
    assert face_states(root)[face] == (None, emma)


# --- POST faces/cluster/{id}/dismiss -------------------------------------------------


def test_dismiss_marks_every_face_in_the_cluster_dismissed_and_says_how_many(tmp_path):
    """UX 6's `Ignore`. The stored word is `dismissed` - NOT `not_a_face`."""
    root = two_photographs(tmp_path)
    in_cluster = [plant_face(root, sha_of(root, "a.jpg"), cluster_id=1) for _ in range(2)]
    in_cluster.append(plant_face(root, "sha-b", cluster_id=1))
    elsewhere = plant_face(root, "sha-b", cluster_id=2)

    status, body = call(root, "POST", "/faces/cluster/1/dismiss")

    assert status == 200
    assert (body["faces"], body["photographs"]) == (3, 2)
    states = face_states(root)
    assert all(states[f][0] == DISMISSED for f in in_cluster)
    assert states[elsewhere][0] is None


def test_dismiss_of_a_cluster_with_nothing_left_to_dismiss_is_a_404(tmp_path):
    root = archive(tmp_path)
    plant_face(root, sha_of(root, "IMG_1.JPG"), cluster_id=1, review_state=DISMISSED)

    status_gone, _ = call(root, "POST", "/faces/cluster/1/dismiss")
    status_none, _ = call(root, "POST", "/faces/cluster/9/dismiss")

    assert (status_gone, status_none) == (404, 404)


def test_dismiss_touches_exactly_the_faces_the_labeling_page_showed(tmp_path):
    """A11: the page shows a cluster's faces AT OR ABOVE the floor (0.4 in this archive).
    A below-floor face in the same cluster was never offered, so `Ignore` does not write
    it - and the count reported is the count written."""
    root = archive(tmp_path)
    sha = sha_of(root, "IMG_1.JPG")
    shown = plant_face(root, sha, cluster_id=1, confidence=0.9)
    below = plant_face(root, sha, cluster_id=1, confidence=0.1)
    already = plant_face(root, sha, cluster_id=1, review_state=NOT_A_FACE)

    status, body = call(root, "POST", "/faces/cluster/1/dismiss")

    assert status == 200 and body["faces"] == 1
    states = face_states(root)
    assert states[shown][0] == DISMISSED
    assert states[below][0] is None
    assert states[already][0] == NOT_A_FACE, "dismiss must not overwrite not_a_face"


def test_dismiss_refuses_a_named_cluster_and_writes_nothing(tmp_path):
    """Spec 8.2: dismiss is `Ignore` for an UNNAMED cluster - "a different operation from
    hiding a named person". A cluster whose every face is named has nothing unnamed to
    ignore; the repair for a named person is Manage People, so it is a 409 naming the
    person, and not one face changes state. (A MIXED cluster's unnamed remainder IS
    ignorable - see S2-F1 below.)"""
    root = archive(tmp_path)
    sha = sha_of(root, "IMG_1.JPG")
    emma = plant_person(root, "Emma")
    named = [plant_face(root, sha, cluster_id=1, person_id=emma) for _ in range(2)]

    status, body = call(root, "POST", "/faces/cluster/1/dismiss")

    assert status == 409
    assert "Emma" in body["error"]
    states = face_states(root)
    assert [states[f] for f in named] == [(None, emma), (None, emma)]


# --- S2-F1 (Task 11 Stage 2): a cluster holding named AND unnamed faces --------------
#
# Built on real photographs: take the name off ONE of a person's faces and it keeps its
# `cluster_id`, so the queue offers the UNNAMED REMAINDER of that cluster as its own group
# ("1 face across 1 photograph"). Every unnamed-cluster operation must act on exactly that
# remainder. Before the fix `label` wrote the WHOLE cluster: Blair 6 -> 0, silently, and the
# label's undo could not put the faces back.


def mixed_cluster(tmp_path) -> tuple[Path, int, list[int], int]:
    """Cluster 1: two faces named Blair and one unnamed face, in two photographs."""
    root = two_photographs(tmp_path)
    blair = plant_person(root, "Blair")
    named = [
        plant_face(root, sha_of(root, "a.jpg"), cluster_id=1, person_id=blair),
        plant_face(root, "sha-b", cluster_id=1, person_id=blair, confidence=0.95),
    ]
    remainder = plant_face(root, "sha-b", cluster_id=1, confidence=0.8)
    return root, blair, named, remainder


def test_the_queue_offers_a_remainder_with_only_its_own_faces_in_the_sample(tmp_path):
    root, _, named, remainder = mixed_cluster(tmp_path)

    status, body = call(root, "GET", "/faces/clusters")

    assert status == 200
    offered = [c for c in body["clusters"] if c["cluster_id"] == 1]
    assert len(offered) == 1
    assert (offered[0]["faces"], offered[0]["photographs"]) == (1, 1)
    assert [face["face_id"] for face in offered[0]["sample"]] == [remainder], (
        "the queue asked who this is over faces that already have a name"
    )


def test_every_face_of_a_remainder_lists_only_its_unnamed_faces(tmp_path):
    root, _, named, remainder = mixed_cluster(tmp_path)

    status, body = call(root, "GET", "/faces/cluster/1")

    assert status == 200
    assert [face["face_id"] for face in body["faces"]] == [remainder]


def test_label_on_a_remainder_names_only_it_and_never_moves_a_named_face(tmp_path):
    """S2-F1 itself. The named faces keep their person; the response and `face_ids` (the
    label undo's input) name the remainder alone, so the undo cannot touch Blair either."""
    root, blair, named, remainder = mixed_cluster(tmp_path)

    status, body = call(root, "POST", "/faces/cluster/1/label", {"person": "Dana"})

    assert status == 200, body
    assert (body["faces"], body["photographs"], body["face_ids"]) == (1, 1, [remainder])
    states = face_states(root)
    assert [states[f][1] for f in named] == [blair, blair], "a named person lost faces"
    assert states[remainder][1] == body["person_id"] != blair


def test_label_of_a_fully_named_cluster_is_refused_naming_the_person(tmp_path):
    """A stale page (the group was named in another tab) must not re-name it wholesale."""
    root = archive(tmp_path)
    sha = sha_of(root, "IMG_1.JPG")
    emma = plant_person(root, "Emma")
    named = [plant_face(root, sha, cluster_id=1, person_id=emma) for _ in range(2)]

    status, body = call(root, "POST", "/faces/cluster/1/label", {"person": "Dana"})

    assert status == 409, body
    assert "Emma" in body["error"]
    states = face_states(root)
    assert [states[f][1] for f in named] == [emma, emma]
    assert set(person_rows(root)) == {emma}


def test_dismiss_on_a_remainder_ignores_only_it(tmp_path):
    root, blair, named, remainder = mixed_cluster(tmp_path)

    status, body = call(root, "POST", "/faces/cluster/1/dismiss")

    assert status == 200, body
    assert body["faces"] == 1
    states = face_states(root)
    assert [states[f] for f in named] == [(None, blair), (None, blair)]
    assert states[remainder] == (DISMISSED, None)


def test_persons_counts_only_faces_that_are_not_reviewed_away(tmp_path):
    """S2-F2 (Stage 2): a person whose face was marked not-a-face read "1 face" while
    `person/{id}` listed none, and merge's confirmation is built from these counts."""
    root = two_photographs(tmp_path)
    dana = plant_person(root, "Dana")
    plant_face(root, sha_of(root, "a.jpg"), person_id=dana)
    plant_face(root, "sha-b", person_id=dana, review_state=NOT_A_FACE)
    plant_face(root, "sha-b", person_id=dana, review_state=DISMISSED)

    status, body = call(root, "GET", "/faces/persons")

    assert status == 200
    row = [p for p in body["persons"] if p["id"] == dana][0]
    assert (row["faces"], row["photographs"]) == (1, 1)


def test_label_names_exactly_the_faces_shown_and_leaves_a_below_floor_face_unnamed(tmp_path):
    """TASK 10's KNOWN DEFECT, CLOSED IN TASK 11 (owner, 2026-09-16) - this test used to
    assert the bug. `label` counted the cluster at A11's floor and wrote with no floor, so a
    cluster holding one below-floor detection - the state a RAISED floor leaves behind,
    because A11 applies the floor at read time - 409'd for ever and could not be named.

    The property, and it is vision R6's: pam names exactly the faces the page showed. The
    other reading (write every unreviewed face and count them all) would put a person on a
    detection the user was never shown, so the below-floor face stays unnamed, and it is not
    in `face_ids`, so the undo cannot touch it either."""
    root = archive(tmp_path)
    sha = sha_of(root, "IMG_1.JPG")
    shown = plant_face(root, sha, cluster_id=1, confidence=0.9)
    below = plant_face(root, sha, cluster_id=1, confidence=0.1)

    status, body = call(root, "POST", "/faces/cluster/1/label", {"person": "Emma"})

    assert status == 200, body
    assert (body["faces"], body["photographs"], body["face_ids"]) == (1, 1, [shown])
    states = face_states(root)
    assert states[shown][1] == body["person_id"]
    assert states[below] == (None, None), "a face the page never showed acquired a person"


# --- GET faces/person/{id} (owner ruling 4) ------------------------------------------


def test_person_lists_exactly_that_persons_unreviewed_faces_below_floor_included(tmp_path):
    """Ruling 4: Manage People's expansion. Every face of THIS person with no review state,
    NO floor filter (a below-floor face is still assigned and may need correcting), each
    flagged against the archive's floor (0.4 here). A face of ANOTHER person and a
    reviewed-away face of THIS person are both absent - the second lives on Ignored faces.
    Ordered confidence descending, then id."""
    root = two_photographs(tmp_path)
    emma = plant_person(root, "Emma")
    kofi = plant_person(root, "Kofi")
    low = plant_face(root, sha_of(root, "a.jpg"), person_id=emma, confidence=0.1)
    tie_first = plant_face(root, "sha-b", person_id=emma, confidence=0.7)
    tie_second = plant_face(root, sha_of(root, "a.jpg"), person_id=emma, confidence=0.7)
    high = plant_face(root, "sha-b", person_id=emma, confidence=0.95)
    plant_face(root, "sha-b", person_id=kofi, confidence=0.99)
    plant_face(root, "sha-b", person_id=emma, confidence=0.99, review_state=DISMISSED)
    plant_face(root, "sha-b", person_id=emma, confidence=0.98, review_state=NOT_A_FACE)
    plant_face(root, "sha-b", confidence=0.97)

    status, body = call(root, "GET", f"/faces/person/{emma}")

    assert status == 200
    assert [face["face_id"] for face in body["faces"]] == [high, tie_first, tie_second, low]
    flags = {face["face_id"]: face["below_floor"] for face in body["faces"]}
    assert flags == {high: False, tie_first: False, tie_second: False, low: True}
    by_id = {face["face_id"]: face for face in body["faces"]}
    assert by_id[low]["filename"] == "a.jpg" and by_id[low]["file_id"] is not None
    assert by_id[high]["confidence"] == 0.95
    assert all("embedding" not in face for face in body["faces"])
    assert body["person"] == {
        "id": emma,
        "label": "Emma",
        "filename_label": None,
        "filename_value": "Emma",
        "hidden": False,
        # S2-F2 (Task 11 Stage 2): the count is of the faces LISTED above. It read 6 - the
        # two reviewed-away faces included - so the header disagreed with its own list.
        "faces": 4,
        "photographs": 2,
    }


def test_person_serves_a_hidden_person_because_manage_people_unhides_them(tmp_path):
    root = archive(tmp_path)
    conn = _conn(root)
    try:
        cur = conn.execute("INSERT INTO persons (label, hidden) VALUES ('Sam', 1)")
        conn.commit()
        sam = int(cur.lastrowid or 0)
    finally:
        conn.close()
    face = plant_face(root, sha_of(root, "IMG_1.JPG"), person_id=sam)

    status, body = call(root, "GET", f"/faces/person/{sam}")

    assert status == 200
    assert body["person"]["hidden"] is True
    assert [row["face_id"] for row in body["faces"]] == [face]


def test_person_with_no_faces_is_served_empty_not_missing(tmp_path):
    """A person whose every face was moved away still exists, and is exactly the row a
    user needs to open in order to merge or rename them."""
    root = archive(tmp_path)
    emma = plant_person(root, "Emma")

    status, body = call(root, "GET", f"/faces/person/{emma}")

    assert status == 200
    assert body["faces"] == [] and body["person"]["faces"] == 0


def test_person_read_of_an_unknown_or_non_numeric_id_is_a_404_with_a_sentence(tmp_path):
    root = archive(tmp_path)

    status_unknown, body_unknown = call(root, "GET", "/faces/person/77")
    status_word, body_word = call(root, "GET", "/faces/person/emma")

    assert (status_unknown, status_word) == (404, 404)
    assert "77" in body_unknown["error"]
    assert "emma" in body_word["error"]


# --- GET faces/ignored ---------------------------------------------------------------


def test_ignored_lists_both_states_with_counts_and_no_floor(tmp_path):
    """The page that makes dismissing reversible (vision R6). NO FLOOR: a below-floor face
    that was dismissed must still be restorable from here."""
    root = archive(tmp_path)
    sha = sha_of(root, "IMG_1.JPG")
    rejected = plant_face(root, sha, review_state=NOT_A_FACE)
    hidden_low = plant_face(root, sha, review_state=DISMISSED, confidence=0.1)
    hidden = plant_face(root, sha, review_state=DISMISSED)
    plant_face(root, sha)

    status, body = call(root, "GET", "/faces/ignored")

    assert status == 200
    assert body["counts"] == {NOT_A_FACE: 1, DISMISSED: 2}
    by_id = {face["face_id"]: face for face in body["faces"]}
    assert set(by_id) == {rejected, hidden_low, hidden}
    assert by_id[rejected]["review_state"] == NOT_A_FACE
    assert by_id[hidden]["review_state"] == DISMISSED
    assert by_id[hidden]["filename"] == "IMG_1.JPG"
    assert by_id[hidden]["file_id"] is not None
    assert all("embedding" not in face for face in body["faces"])


def test_ignored_with_nothing_ignored_is_empty_not_missing(tmp_path):
    root = archive(tmp_path)
    plant_face(root, sha_of(root, "IMG_1.JPG"))

    status, body = call(root, "GET", "/faces/ignored")

    assert status == 200
    assert body == {"faces": [], "counts": {NOT_A_FACE: 0, DISMISSED: 0}}


def test_a_face_restored_from_ignored_leaves_the_ignored_list(tmp_path):
    root = archive(tmp_path)
    face = plant_face(root, sha_of(root, "IMG_1.JPG"), review_state=DISMISSED)

    call(root, "POST", "/faces/faces/state", {"face_ids": [face], "review_state": None})
    status, body = call(root, "GET", "/faces/ignored")

    assert status == 200 and body["faces"] == []


# -------------------------------------------------------------------------------------
# THE LOAD-BEARING CONTROLS OF THIS MODULE (M10's C4). Hand-spelled, never derived.
# -------------------------------------------------------------------------------------

LOAD_BEARING_CONTROLS = {
    "test_label_on_a_remainder_names_only_it_and_never_moves_a_named_face": (
        "S2-F1: without it labeling an unnamed remainder silently moves a named person's "
        "faces, and the label undo cannot put them back"
    ),
    "test_label_of_a_fully_named_cluster_is_refused_naming_the_person": (
        "S2-F1's other half: a stale page must not re-name a named cluster wholesale"
    ),
    "test_the_queue_offers_a_remainder_with_only_its_own_faces_in_the_sample": (
        "S2-F1: the sample asked who this is over faces that already had a name"
    ),
    "test_dismiss_on_a_remainder_ignores_only_it": (
        "S2-F1: Ignore on a remainder must not touch the named faces beside it"
    ),
    "test_persons_counts_only_faces_that_are_not_reviewed_away": (
        "S2-F2: counts that include reviewed-away faces overstate a person and a merge"
    ),
    "test_merge_refuses_true_as_into_when_person_one_is_somebody_else": (
        "F5: the bool operand of merge's type guard; the ill-typed test passes for the "
        "wrong reason because its only person is id 1"
    ),
    "test_state_distinguishes_not_a_face_from_dismissed": (
        "the only test that can see the two review states swapped - every other "
        "assertion reads 'non-NULL', which both words satisfy"
    ),
    "test_assign_with_a_null_person_clears_rather_than_sets": (
        "I3 for assignment: a null that was ignored or created a person would make a "
        "mistaken name permanent"
    ),
    "test_merge_moves_faces_and_keeps_the_surviving_persons_label": (
        "spec 8.2's 'MOVES FACES; it does not rename' - both people are named, so the "
        "rename-merge wrong implementation is visible only here"
    ),
    "test_person_hidden_is_toggled_without_touching_the_filename_label": (
        "Step 8's named mutation 'person/{id} with hidden ALSO clears filename_label': the "
        "repo test cannot see the handler and the driver pins only the page's body, so this "
        "is the one test that sees a hide wipe an override (Stage 1's M1874, F1)"
    ),
    "test_person_rename_onto_another_persons_label_is_refused_not_merged": (
        "the case-insensitive collision: SQLite's UNIQUE is case-SENSITIVE, so `emma` "
        "onto `Emma` would otherwise land silently as a second person"
    ),
    "test_dismiss_refuses_a_named_cluster_and_writes_nothing": (
        "spec 8.2's UNNAMED cluster: without it `Ignore` silently strips a person's name"
    ),
    "test_label_names_exactly_the_faces_shown_and_leaves_a_below_floor_face_unnamed": (
        "Task 10's defect, closed: without it a raised floor makes every such cluster "
        "unnameable, or names a face the user never saw (vision R6)"
    ),
    "test_state_null_clears_a_review_state": (
        "A18's review-state vocabulary, the clearing half. MOVED here at M12 Task 1 from "
        "`tests/test_webui_api.py`'s FACES_MANAGE_ANCHORED_CONTROLS (M1878: this module "
        "deleted whole left 3507 passed, 0 failed)"
    ),
    "test_label_then_unassigning_its_face_ids_restores_every_face_and_no_other": (
        "the vision R6 / I3 reversal surface Task 11 exists to build. MOVED here at M12 "
        "Task 1 from FACES_MANAGE_ANCHORED_CONTROLS (M1878)"
    ),
    "test_person_lists_exactly_that_persons_unreviewed_faces_below_floor_included": (
        "ruling 4's person read, below-floor faces included. MOVED here at M12 Task 1 "
        "from FACES_MANAGE_ANCHORED_CONTROLS (M1878)"
    ),
    "test_ignored_lists_both_states_with_counts_and_no_floor": (
        "the `faces/ignored` read, both states and no floor. MOVED here at M12 Task 1 "
        "from FACES_MANAGE_ANCHORED_CONTROLS (M1878)"
    ),
}

assert LOAD_BEARING_CONTROLS, "this module's control list is empty; it protects nothing"


def test_no_faces_manage_control_in_this_file_can_be_deleted_without_saying_so():
    """The floor under this module's inventory (M10's C4, amendment R4)."""
    present = {name for name in globals() if name.startswith("test_")}
    missing = sorted(set(LOAD_BEARING_CONTROLS) - present)
    assert missing == [], f"a load-bearing control was deleted: {missing}"
