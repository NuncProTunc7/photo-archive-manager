"""What the faces page and `pam status` say, and the server fields they say it from (M12 Task 5).

Condition C8 of `m11.md`, with the copy adopted as P2 and P3 (the M12 plan amendments, applied
to UX 6, design 8.2 and design 12 in the same commit):

- **F5** - `GET /faces/clusters` gains `hidden_below_floor`, the unnamed groups whose
  unreviewed faces ALL sit below `[tiers] faces` floor, so the done state can count them
  instead of saying nothing about them (vision principle 9);
- **O8** - the restore (`faces/faces/state` with `null`) says where the faces went, as
  `person_labels`, so the page can stop promising "its group" to a face whose person is named;
- **the counter** - every write the naming page's progress line follows answers with
  `named` and `remaining`, read after the write, because HT-M11 Leg B watched the counter
  sit still while the page did its own arithmetic;
- **P3** - `pam status` names `pam faces cluster` once faces coverage is non-zero, and not
  otherwise.

EVERY EXPECTED NUMBER IS WORKED BY HAND FROM THE FIXTURE, in the test that asserts it, and
every catalog row is planted with the suite's own SQL (`tests/test_webui_faces.py`'s rule).
None is read back through `GET /faces/clusters` or `faces_api.progress`: the write routes'
counts and the queue's counts come from ONE production function, so comparing them would be
the ENRICHERS lesson - an oracle built from the definition it checks. The page-side half (the
sentences) is executed under node by `tests/js/faces_state_test.js`.
"""

import json
from pathlib import Path

from test_webui_faces import Served, _conn, archive, plant_face, plant_person, sha_of

from pam import status
from pam.cli import main
from pam.paths import workspace_if_exists

# P3's line, spelled from design 12 rev 27 - NOT from `status.FACES_CLUSTER_STEP`.
FACES_OR_LINE = (
    "  or: pam faces cluster   (group the faces pam found, then name them with pam faces label)"
)


def call(root: Path, method: str, path: str, body=None):
    live = Served(root)
    try:
        return live.json(method, path, body)
    finally:
        live.close()


def set_floor(root: Path, floor: float) -> None:
    ws = workspace_if_exists(root)
    assert ws is not None
    ws.config_path.write_text(f"[tiers]\nfaces = {{ floor = {floor} }}\n", encoding="utf-8")


# --- F5: hidden_below_floor ----------------------------------------------------------


def test_a_group_whose_faces_are_all_below_the_floor_is_counted_and_the_floor_is_named(
    tmp_path,
):
    """The plan's own case: cluster 1 holds faces only at 0.3, cluster 2 one at 0.9.

    At `floor = 0.4` (the fixture's `pam.toml`) cluster 1 is hidden and cluster 2 is not:
    the field is 1. At `floor = 0.2` both are shown: 0. The page names the threshold, so
    the response carries it too."""
    root = archive(tmp_path)
    sha = sha_of(root, "IMG_1.JPG")
    plant_face(root, sha, cluster_id=1, confidence=0.3)
    plant_face(root, sha, cluster_id=1, confidence=0.3)
    plant_face(root, sha, cluster_id=2, confidence=0.9)

    status_code, body = call(root, "GET", "/faces/clusters")
    assert status_code == 200
    assert body["hidden_below_floor"] == 1
    assert body["floor"] == 0.4
    assert [row["cluster_id"] for row in body["clusters"]] == [2]

    set_floor(root, 0.2)
    _status, lowered = call(root, "GET", "/faces/clusters")
    assert lowered["hidden_below_floor"] == 0
    assert lowered["floor"] == 0.2


def test_only_unnamed_unreviewed_faces_below_the_floor_make_a_group_hidden(tmp_path):
    """P2's definition, operand by operand, at `floor = 0.4`:

    - cluster 1: one face at 0.9 and one at 0.3 - SHOWN (it has an offerable face): no;
    - cluster 2: its only face at 0.3 is DISMISSED - reviewed away, not hidden: no;
    - cluster 3: a named face at 0.9 and an UNNAMED remainder only at 0.3 - the queue
      offers the unnamed remainder as its own group, and the floor hides it: YES;
    - cluster 4: its only face at 0.3 is NAMED - nothing unnamed to hide: no;
    - cluster 5: one unnamed face at 0.3 - YES.
    Expected 2, worked here and not read from the server."""
    root = archive(tmp_path)
    sha = sha_of(root, "IMG_1.JPG")
    emma = plant_person(root, "Emma")
    plant_face(root, sha, cluster_id=1, confidence=0.9)
    plant_face(root, sha, cluster_id=1, confidence=0.3)
    plant_face(root, sha, cluster_id=2, confidence=0.3, review_state="dismissed")
    plant_face(root, sha, cluster_id=3, confidence=0.9, person_id=emma)
    plant_face(root, sha, cluster_id=3, confidence=0.3)
    plant_face(root, sha, cluster_id=4, confidence=0.3, person_id=emma)
    plant_face(root, sha, cluster_id=5, confidence=0.3)

    _status, body = call(root, "GET", "/faces/clusters")

    assert body["hidden_below_floor"] == 2


# --- O8: the restore says where the faces went ---------------------------------------


def test_a_restore_names_the_person_its_faces_go_back_to(tmp_path):
    """`person_labels` is the distinct labels of the restored faces' people, `null` for a
    face with none: one named face -> ["Emma"]; one unnamed -> [null]; both -> both."""
    root = archive(tmp_path)
    sha = sha_of(root, "IMG_1.JPG")
    emma = plant_person(root, "Emma")
    named = plant_face(root, sha, cluster_id=1, person_id=emma, review_state="not_a_face")
    unnamed = plant_face(root, sha, cluster_id=2, review_state="dismissed")
    also_emma = plant_face(root, sha, cluster_id=1, person_id=emma, review_state="not_a_face")
    loose = plant_face(root, sha, cluster_id=3, review_state="dismissed")

    _s, one_named = call(
        root, "POST", "/faces/faces/state", {"face_ids": [named], "review_state": None}
    )
    _s, one_unnamed = call(
        root, "POST", "/faces/faces/state", {"face_ids": [unnamed], "review_state": None}
    )
    _s, mixed = call(
        root,
        "POST",
        "/faces/faces/state",
        {"face_ids": [also_emma, loose], "review_state": None},
    )

    assert one_named["person_labels"] == ["Emma"]
    assert one_unnamed["person_labels"] == [None]
    assert mixed["person_labels"] == ["Emma", None]

    # DISTINCT (P2's word), and the case P2's PLURAL named sentence is for (Task 5 review,
    # F1 / M2054): two faces of one person answer ONE label, and two faces of no person ONE
    # null - a repeated entry would read on the page as "more than one destination".
    pair = [
        plant_face(root, sha, cluster_id=1, person_id=emma, review_state="not_a_face")
        for _ in range(2)
    ]
    strays = [plant_face(root, sha, cluster_id=4, review_state="dismissed") for _ in range(2)]
    _s, two_named = call(
        root, "POST", "/faces/faces/state", {"face_ids": pair, "review_state": None}
    )
    _s, two_unnamed = call(
        root, "POST", "/faces/faces/state", {"face_ids": strays, "review_state": None}
    )
    assert two_named["person_labels"] == ["Emma"]
    assert two_unnamed["person_labels"] == [None]


# --- the counter: every write the page's progress line follows -----------------------


def test_every_counted_write_answers_with_the_counts_after_it(tmp_path):
    """One archive, the writes in sequence, each expectation worked by hand.

    Start: cluster 1 two unnamed faces; cluster 2 one unnamed face; cluster 3 one Emma face
    and one Em face; cluster 4 one dismissed face. `named` counts named (cluster, person)
    groups and `remaining` unnamed groups: named 2 ((3, Emma), (3, Em)), remaining 2 (1, 2).
    """
    root = archive(tmp_path)
    sha = sha_of(root, "IMG_1.JPG")
    emma = plant_person(root, "Emma")
    em = plant_person(root, "Em")
    plant_face(root, sha, cluster_id=1)
    plant_face(root, sha, cluster_id=1)
    two = plant_face(root, sha, cluster_id=2)
    emmas = plant_face(root, sha, cluster_id=3, person_id=emma)
    plant_face(root, sha, cluster_id=3, person_id=em)
    ignored = plant_face(root, sha, cluster_id=4, review_state="dismissed")

    def counts(body):
        return body["named"], body["remaining"]

    # MERGE Em into Emma: cluster 3 is ONE named group now - a merge moves the counter
    # although no group was named or unnamed. named 1, remaining 2.
    _s, merged = call(root, "POST", f"/faces/person/{em}/merge", {"into": emma})
    assert counts(merged) == (1, 2)

    # LABEL cluster 1 "Kofi": named 2 ((1, Kofi), (3, Emma)), remaining 1 (2).
    _s, labelled = call(root, "POST", "/faces/cluster/1/label", {"person": "Kofi"})
    assert counts(labelled) == (2, 1)

    # UNASSIGN Emma's face in cluster 3: (3, Emma) is gone and (3, None) is new; Em's face,
    # merged into Emma, still names cluster 3. named 2, remaining 2.
    _s, unassigned = call(
        root, "POST", "/faces/faces/assign", {"face_ids": [emmas], "person": None}
    )
    assert counts(unassigned) == (2, 2)

    # MOVE cluster 2's face to Emma: (2, Emma) named, cluster 2 leaves the queue.
    # named 3, remaining 1 (3's unnamed remainder).
    _s, moved = call(root, "POST", "/faces/faces/assign", {"face_ids": [two], "person": "Emma"})
    assert counts(moved) == (3, 1)

    # RESTORE cluster 4's dismissed face: it rejoins as an unnamed group. named 3, remaining 2.
    _s, restored = call(
        root, "POST", "/faces/faces/state", {"face_ids": [ignored], "review_state": None}
    )
    assert counts(restored) == (3, 2)

    # IGNORE cluster 4 again: named 3, remaining 1.
    _s, dismissed = call(root, "POST", "/faces/cluster/4/dismiss", {})
    assert counts(dismissed) == (3, 1)


# --- P3: `pam status` names `pam faces cluster` --------------------------------------


def _archive_status(faces: int, **fields) -> status.ArchiveStatus:
    enrichment = {"caption": 0, "faces": faces, "ocr": 0, "phash": 0, "geocode": 0}
    base = {
        "root": Path("photos"),
        "reachable": True,
        "scanned": True,
        "enrichment": enrichment,
    }
    return status.ArchiveStatus(**{**base, **fields})


def test_status_names_faces_cluster_once_faces_coverage_is_non_zero():
    """P3: one `or:` line, the last in the block, in design 12's words."""
    lines = status.step_lines(status.next_steps(_archive_status(3)))
    assert lines[-1] == FACES_OR_LINE
    assert sum("pam faces cluster" in line for line in lines) == 1
    assert lines[0].startswith("Next: pam plan"), lines


def test_status_says_nothing_about_faces_cluster_where_faces_never_ran():
    """The zero case: no line on an archive where faces never ran (P3)."""
    for state in (
        _archive_status(0),
        _archive_status(0, open_plan_ids=(4,)),
    ):
        lines = status.step_lines(status.next_steps(state))
        assert not any("faces" in line for line in lines), lines


def test_the_faces_line_joins_every_state_of_a_scanned_archive_pam_can_see():
    """An open plan's block keeps its two steps and gains the line; an archive pam cannot
    see gets none - `pam faces cluster` could not run against it (spec 3 rev 8)."""
    planned = status.step_lines(status.next_steps(_archive_status(2, open_plan_ids=(4,))))
    assert planned[0].startswith("Next: pam review 4"), planned
    assert planned[-1] == FACES_OR_LINE
    gone = status.step_lines(status.next_steps(_archive_status(2, reachable=False)))
    assert not any("faces" in line for line in gone), gone


def test_pam_status_prints_the_faces_line_from_the_coverage_it_computes(tmp_path, capsys):
    """End to end: a real scanned archive, one live `faces` marker planted with the suite's
    own SQL, and the screen `pam status` prints."""
    root = archive(tmp_path)
    sha = sha_of(root, "IMG_1.JPG")

    code = main(["status", "--archive", str(root)])
    before = capsys.readouterr().out
    assert code == 0
    assert "pam faces cluster" not in before

    conn = _conn(root)
    try:
        conn.execute(
            "INSERT INTO inferred_facts (sha256, kind, value_json, confidence, model_name,"
            " model_version, generated_at) VALUES (?, 'faces', ?, NULL, 'yunet+sface',"
            " 'test-generation', '2026-09-25T00:00:00')",
            (sha, json.dumps({"count": 1})),
        )
        conn.commit()
    finally:
        conn.close()

    code = main(["status", "--archive", str(root)])
    after = capsys.readouterr().out
    assert code == 0
    assert FACES_OR_LINE in after.splitlines(), after
