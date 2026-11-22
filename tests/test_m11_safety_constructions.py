"""M11's construction hunt (plan Task 14 Step 2). Vision S1-S4, BUILT rather than argued.

WHY THIS FILE EXISTS, in the project's own words. A property no single module owns is
invisible to a mutation pass scoped to one task's files: M8's 232 exemplary rows could not
reach `pam apply` splitting a companion unit, and M9's 392 could not reach F12. The answer
is not more mutations - it is to take the vision's safety properties one at a time and try
to BUILD the violation end to end, across module boundaries, driving the real commands.

M11 asks FOUR questions, not three. The first three are M8's, M9's and M10's, carried
forward by `CLAUDE.md`; the fourth is this milestone's own, and Task 11's S2-F1 is why.

  Q1  Which filter dimensions are NOT unit-uniform?
      (Answers so far: free-text search, event membership, the event label, `place`,
      `q=acr_` - and M11's `{people}`, which Task 12 built and `test_people_naming.py`
      pins with the `decide-filter` widening proof. No new dimension was found here.)
  Q2  Which members of a unit does each command NOT see?
      (M11's candidate is real: `wants()` refuses videos, so a Live Photo unit's `.MOV`
      carries no face, no marker and no people. Q2 asks what that does to the NAME.)
  Q3  Does every write path still resolve through `_widen_to_units` over the WHOLE plan?
      (M11 adds ELEVEN write endpoints. The instruction is to ENUMERATE THE ENDPOINTS,
      never to check the function - so the tests below walk the live route table.)
  Q4  Which faces does each unnamed-cluster operation actually SEE, compared with the
      faces the page SHOWED? (S2-F1: the queue offered "1 face" while its sample drew four
      of a named person's, and naming the group moved all seven. The owner CONFIRMED the
      fix on 2026-09-17: an unnamed group's operations act on its unnamed faces, and a
      wholly named cluster is a 409 naming the people.)

EVERY EXPECTATION HERE IS SPELLED FROM THE SPEC OR BUILT BY HAND, never read back out of
the production call it checks - an oracle built from the definition it checks is a
survivor waiting to happen.
"""

import json
import sqlite3
from pathlib import Path

from archives import FAKE_COMMAND
from test_faces_cluster import GEOMETRY, blob
from test_webui_faces import Served

from pam.catalog.db import connect
from pam.naming.planner import run_plan
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan
from pam.webui import faces_api, faces_manage_api, faces_split_api, write_api

# ---------------------------------------------------------------------------
# Q3 - THE ENDPOINTS, ENUMERATED (never the function)
#
# `CLAUDE.md`: "does every write path still resolve through `_widen_to_units` over the
# whole plan? - enumerate the endpoints, never check the function." M10 made that
# containment a single choke point, which is also a single point of failure, so M11's new
# routes are classified here ONE BY ONE, by hand. A route that appears in the server and
# in neither list fails this test, which is the point: the next milestone's new endpoint
# cannot land unclassified.
# ---------------------------------------------------------------------------

# Routes that decide, rename or restore PLAN ITEMS. Every one must reach the plan through
# a unit-widening read - `_widen_to_units` for the filter endpoints, `_unit` for the
# per-item ones, both of which read `repo.plan_rows` over the WHOLE plan.
PLAN_WRITE_ROUTES = {
    ("POST", "plan/{id}/decide"),
    ("POST", "plan/{id}/decide-filter"),
    # M14 Task 3 (U17): the selection's decision - widens over the whole plan like decide-filter.
    ("POST", "plan/{id}/decide-selection"),
    ("POST", "item/{id}/rename"),
    ("POST", "item/{id}/restore"),
}

# Routes that write FACES AND PEOPLE and touch no plan item at all. They change
# `face_instances` and `persons`; the names they affect are the ones the NEXT `pam plan`
# proposes, and a plan already written is not edited by any of them.
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


def test_every_POST_route_the_server_serves_is_classified_by_hand(tmp_path):
    """Q3, first half: the live route table and these two lists agree, entry by entry."""
    root = _archive_of(tmp_path, 1)
    served = Served(root)
    try:
        posts = {route for route in served.server.api_handlers if route[0] == "POST"}
    finally:
        served.close()
    classified = PLAN_WRITE_ROUTES | FACES_WRITE_ROUTES | READ_ONLY_POST_ROUTES
    assert posts == classified, sorted(posts.symmetric_difference(classified))
    assert not PLAN_WRITE_ROUTES & FACES_WRITE_ROUTES
    assert not READ_ONLY_POST_ROUTES & (PLAN_WRITE_ROUTES | FACES_WRITE_ROUTES)


def test_no_faces_route_reaches_a_plan_item_writer(tmp_path):
    """Q3, second half - about the CODE the routes reach, not about their names.

    Every faces module is checked for the writes a plan item can be changed by (spec 8.2's
    only item writes) and for `plan_rows` itself. A faces route that started editing plan
    items would be a new write path into the plan, and `_widen_to_units` is precisely the
    containment it would be bypassing.
    """
    for module in (faces_api, faces_manage_api, faces_split_api):
        source = Path(module.__file__).read_text(encoding="utf-8")
        for writer in ("set_item_status", "set_item_name", "plan_rows"):
            assert writer not in source, f"{module.__name__} reaches {writer}"
    plan_source = Path(write_api.__file__).read_text(encoding="utf-8")
    assert "_widen_to_units" in plan_source or "_unit(" in plan_source


# ---------------------------------------------------------------------------
# Q2 - A LIVE PHOTO UNIT WHOSE VIDEO HAS NO FACE AND NO PEOPLE
#
# `enrich/base.py::wants()` refuses videos, so the `.MOV` half of a Live Photo carries no
# face row, no `faces` marker and no person. The unit still has to take ONE stem.
# ---------------------------------------------------------------------------


def _dated(root, name):
    (root / name).write_bytes(b"content of " + name.encode())
    (root / f"{name}.tags.json").write_text(
        json.dumps({"DateTimeOriginal": "2019:07:04 18:23:11"}), encoding="utf-8"
    )


def _live_photo_unit(tmp_path):
    root = tmp_path / "photos"
    root.mkdir()
    for name in ("LIVE_1.JPG", "LIVE_1.MOV"):
        _dated(root, name)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    return root


def _archive_of(tmp_path, count):
    root = tmp_path / "photos"
    root.mkdir()
    for index in range(count):
        _dated(root, f"P{index}.JPG")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    return root


def _readonly(root):
    ws = workspace_if_exists(root)
    assert ws is not None
    return sqlite3.connect(f"file:{ws.db_path}?mode=ro", uri=True)


def _plant_person_on(root, filename, label):
    ws = workspace_if_exists(root)
    assert ws is not None
    conn = connect(ws.db_path)
    try:
        sha = str(
            conn.execute("SELECT sha256 FROM files WHERE filename = ?", (filename,)).fetchone()[
                0
            ]
        )
        person = int(
            conn.execute("INSERT INTO persons (label) VALUES (?)", (label,)).lastrowid or 0
        )
        conn.execute(
            "INSERT INTO face_instances (sha256, bbox_json, embedding, det_confidence,"
            " person_id, model_name, model_version)"
            " VALUES (?, ?, ?, 0.9, ?, 'yunet+sface', '1')",
            (sha, json.dumps(GEOMETRY), blob([0.1, 0.2, 0.3, 0.4]), person),
        )
        conn.commit()
    finally:
        conn.close()
    return person


def test_the_video_of_a_live_photo_takes_the_persons_name_the_photograph_earned(tmp_path):
    """Q2: the member no enricher can SEE is named with the member that was seen.

    Spec 6.1 names a unit from its PRIMARY, so both members must take the same stem - and
    if they ever did not, `pam apply` would rename a companion unit to two different names,
    which is vision S3's breach and the one M8 shipped.
    """
    root = _live_photo_unit(tmp_path)
    _plant_person_on(root, "LIVE_1.JPG", "Emma")

    summary = run_plan(root)

    conn = _readonly(root)
    try:
        rows = dict(
            conn.execute(
                "SELECT old_rel_path, generated_filename FROM plan_items WHERE plan_id = ?",
                (summary.plan_id,),
            ).fetchall()
        )
        units = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT unit_key FROM plan_items WHERE plan_id = ?",
                (summary.plan_id,),
            )
        }
    finally:
        conn.close()

    assert set(rows) == {"LIVE_1.JPG", "LIVE_1.MOV"}, rows
    stems = {Path(name).stem for name in rows.values()}
    assert len(stems) == 1, f"the unit took two stems: {rows}"
    assert "Emma" in stems.pop(), rows
    assert len(units) == 1, "the two members are not one unit"


def test_the_face_rows_cover_the_photograph_and_not_its_video(tmp_path):
    """Q2's other half, stated as a fact rather than feared as one: the video really is a
    member no face enricher sees. Without it, the test above could pass because the
    enricher happened to look at both, and the interesting case would be untested."""
    root = _live_photo_unit(tmp_path)
    _plant_person_on(root, "LIVE_1.JPG", "Emma")

    conn = _readonly(root)
    try:
        faces = dict(
            conn.execute(
                "SELECT f.filename, COUNT(a.id) FROM files f"
                " LEFT JOIN face_instances a ON a.sha256 = f.sha256"
                " WHERE f.filename IN ('LIVE_1.JPG', 'LIVE_1.MOV') GROUP BY f.filename"
            ).fetchall()
        )
    finally:
        conn.close()
    assert faces == {"LIVE_1.JPG": 1, "LIVE_1.MOV": 0}, faces


# ---------------------------------------------------------------------------
# Q4 - THE FACES AN OPERATION SEES vs THE FACES THE PAGE SHOWED (S2-F1's lesson)
#
# Task 11 Stage 2, on real photographs: taking the name off ONE of a person's seven faces
# left the rest carrying `cluster_id` 2, so the queue offered that cluster as "1 face
# across 1 photograph" while its sample drew FOUR of that person's - and Save name moved all
# seven. The counts agreed with each other and with nothing else. The owner CONFIRMED the
# fix (2026-09-17): an unnamed group's operations act on its UNNAMED faces only.
#
# These constructions are the general form of that defect: for every unnamed-cluster
# operation, the set it WRITES must equal the set the page was SHOWN.
# ---------------------------------------------------------------------------


def _cluster_with_a_named_neighbour(tmp_path):
    """One cluster, three faces: two unnamed and one already named - S2-F1's shape."""
    root = _archive_of(tmp_path, 3)
    ws = workspace_if_exists(root)
    assert ws is not None
    conn = connect(ws.db_path)
    try:
        shas = [
            str(r[0])
            for r in conn.execute(
                "SELECT sha256 FROM files WHERE filename LIKE 'P%.JPG' ORDER BY filename"
            )
        ]
        named = int(conn.execute("INSERT INTO persons (label) VALUES ('Blair')").lastrowid or 0)
        faces = []
        for position, sha in enumerate(shas):
            person = named if position == 0 else None
            row = conn.execute(
                "INSERT INTO face_instances (sha256, bbox_json, embedding,"
                " det_confidence, cluster_id, person_id, model_name, model_version)"
                " VALUES (?, ?, ?, 0.9, 2, ?, 'yunet+sface', '1')",
                (sha, json.dumps(GEOMETRY), blob([0.1, 0.2, 0.3, 0.4]), person),
            )
            faces.append(int(row.lastrowid or 0))
        conn.commit()
    finally:
        conn.close()
    return root, named, faces  # faces[0] is Blair's; faces[1:] are unnamed


def test_the_queue_offers_exactly_the_faces_the_label_will_write(tmp_path):
    """Q4: the page's OWN numbers and sample, against what `label` then touches.

    The two are read independently: the queue's counts and sample come from
    `/faces/clusters`, the write's effect is read back with this file's own SQL, and
    Blair's face is named by hand here so its exclusion is a fact of the fixture.
    """
    root, named, faces = _cluster_with_a_named_neighbour(tmp_path)
    served = Served(root)
    try:
        status, listing = served.json("GET", "/faces/clusters")
        assert status == 200, listing
        (group,) = listing["clusters"]
        offered = {face["face_id"] for face in group["sample"]}
        status, written = served.json("POST", "/faces/cluster/2/label", {"person": "Dana"})
        assert status == 200, written
    finally:
        served.close()

    assert group["faces"] == 2, group
    assert offered == set(faces[1:]), "the sample drew a face the group does not own"
    assert written["faces"] == 2, written

    conn = _readonly(root)
    try:
        by_person = dict(
            conn.execute(
                "SELECT a.id, p.label FROM face_instances a"
                " LEFT JOIN persons p ON p.id = a.person_id"
            ).fetchall()
        )
    finally:
        conn.close()
    assert by_person[faces[0]] == "Blair", "a named face was re-named by an unnamed group"
    assert by_person[faces[1]] == "Dana" and by_person[faces[2]] == "Dana", by_person


def test_dismiss_ignores_only_the_faces_the_queue_showed(tmp_path):
    """Q4 for the second operation that acts on an unnamed cluster: `dismiss` must ignore
    only the unnamed faces, and the named one must come back untouched."""
    root, named, faces = _cluster_with_a_named_neighbour(tmp_path)
    served = Served(root)
    try:
        status, dismissed = served.json("POST", "/faces/cluster/2/dismiss")
        assert status == 200, dismissed
    finally:
        served.close()

    assert dismissed["faces"] == 2, dismissed
    conn = _readonly(root)
    try:
        states = dict(conn.execute("SELECT id, review_state FROM face_instances"))
        person_of = dict(conn.execute("SELECT id, person_id FROM face_instances"))
    finally:
        conn.close()
    assert states[faces[0]] is None, "a named face was dismissed by an unnamed group"
    assert person_of[faces[0]] == named
    assert states[faces[1]] == "dismissed" and states[faces[2]] == "dismissed", states


def test_a_cluster_whose_every_face_is_named_is_refused_BY_NAME(tmp_path):
    """Q4's closing case, and the maintainer's CONFIRMED reading (2026-09-17): a group with no
    unnamed face left is a 409 that NAMES the people - never a silent re-naming, and never
    a bare 404 that leaves the user guessing what happened to their group."""
    root, named, faces = _cluster_with_a_named_neighbour(tmp_path)
    ws = workspace_if_exists(root)
    assert ws is not None
    conn = connect(ws.db_path)
    try:
        conn.execute("UPDATE face_instances SET person_id = ? WHERE cluster_id = 2", (named,))
        conn.commit()
    finally:
        conn.close()

    served = Served(root)
    try:
        status, refusal = served.json("POST", "/faces/cluster/2/label", {"person": "Dana"})
    finally:
        served.close()

    assert status == 409, (status, refusal)
    assert "Blair" in json.dumps(refusal), refusal
