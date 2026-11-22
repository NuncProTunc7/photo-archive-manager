"""Spec 8.2's `POST /faces/cluster/{id}/split` (M11 Task 11 Step 4, amendment A18).

WHAT IS PINNED AND FROM WHERE. Spec 8.2: "Returns the PROPOSED partition and the epsilon
used; a second call confirms. Deterministic, collision-free cluster-id allocation; the whole
reassignment in ONE transaction; enough prior membership persisted to undo it. When the
faces will not separate it SAYS SO rather than inventing a split." Plus the maintainer's rulings
of 2026-09-12 (m11-progress.md, Task 11): a TIGHTER FIXED STEP below the configured epsilon,
and undo held in SERVER MEMORY.

EVERY EXPECTATION IS SPELLED HERE. The step factor (0.75) and the step count (4) are written
as literals below, never read off the production module's constants; every catalog row is
planted and read back with the suite's own SQL.

THE EMBEDDINGS ARE DESIGNED, NOT DISCOVERED. Each face is a 2-D unit vector at an ANGLE, so
the cosine distance between two faces is `1 - cos(angle difference)`, computed by hand:

      10 deg -> 0.0152     38 deg -> 0.2120     45 deg -> 0.2929
      28 deg -> 0.1171     40 deg -> 0.2340     2 deg  -> 0.0006

and every threshold below is placed with a margin of at least 0.009 on either side.
"""

import json
import math
import sqlite3
import struct
from pathlib import Path
from typing import Any

import pytest
from test_webui_faces import MODEL, Served, _conn, archive, geometry, plant_person, sha_of

from pam.paths import workspace_if_exists

# The maintainer's "tighter fixed step" (2026-09-12), as the brief's controller fixed it.
FACTOR = 0.75
STEPS = 4

# A18's two non-NULL review states, spelled from the spec.
DISMISSED = "dismissed"


def at(degrees: float) -> bytes:
    """A 2-D unit embedding at `degrees`, as design 4's raw float32 BLOB."""
    radians = math.radians(degrees)
    return struct.pack("<2f", math.cos(radians), math.sin(radians))


def plant(
    root: Path,
    degrees: float,
    *,
    cluster_id: int | None = 1,
    sha256: str | None = None,
    confidence: float = 0.9,
    person_id: int | None = None,
    review_state: str | None = None,
) -> int:
    """One `face_instances` row WITH AN EMBEDDING, with the suite's own INSERT."""
    conn = _conn(root)
    try:
        cur = conn.execute(
            "INSERT INTO face_instances (sha256, bbox_json, embedding, det_confidence,"
            " cluster_id, person_id, review_state, model_name, model_version)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sha_of(root, "IMG_1.JPG") if sha256 is None else sha256,
                json.dumps(geometry(), sort_keys=True),
                at(degrees),
                confidence,
                cluster_id,
                person_id,
                review_state,
                MODEL[0],
                MODEL[1],
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


def configured(tmp_path, epsilon: float | None) -> Path:
    """`archive()`'s floor of 0.4, plus `[enrich.faces] cluster_epsilon` when given."""
    root = archive(tmp_path)
    ws = workspace_if_exists(root)
    assert ws is not None
    text = ws.config_path.read_text(encoding="utf-8")
    if epsilon is not None:
        text += f"[enrich.faces]\ncluster_epsilon = {epsilon}\n"
    ws.config_path.write_text(text, encoding="utf-8")
    return root


def cluster_ids(root: Path) -> dict[int, int | None]:
    """`face id -> cluster_id`, read with the suite's own SQL."""
    conn = _conn(root)
    try:
        return {
            int(r["id"]): r["cluster_id"]
            for r in conn.execute("SELECT id, cluster_id FROM face_instances").fetchall()
        }
    finally:
        conn.close()


def split(live: Served, cluster: int, body: Any) -> tuple[int, Any]:
    return live.json("POST", f"/faces/cluster/{cluster}/split", body)


def five_faces_that_separate_at_step_one(root: Path) -> list[int]:
    """0, 10, 50, 60 and 105 degrees, configured epsilon 0.3.

    Adjacent distances: 0-10 0.0152, 10-50 0.2340, 50-60 0.0152, 60-105 0.2929. At 0.3 all
    five chain into one cluster. Step 1 is 0.3 x 0.75 = 0.225: 10-50 (0.2340) and 60-105
    (0.2929) both break, so {0,10} and {50,60} are two groups and 105 is noise (its nearest
    face, 60, is 0.2929 away; every other face is further).
    """
    return [plant(root, degrees) for degrees in (0, 10, 50, 60, 105)]


# --- preview ---------------------------------------------------------------------------


def test_preview_separates_at_the_first_step_and_reports_that_epsilon(tmp_path):
    root = configured(tmp_path, 0.3)
    a0, a10, b50, b60, lone = five_faces_that_separate_at_step_one(root)
    before = cluster_ids(root)
    live = Served(root)
    try:
        status, body = split(live, 1, {"action": "preview"})
    finally:
        live.close()

    assert status == 200
    assert body["separated"] is True
    assert body["epsilon"] == pytest.approx(0.3 * FACTOR)
    assert [group["face_ids"] for group in body["groups"]] == [[a0, a10], [b50, b60]]
    assert [group["faces"] for group in body["groups"]] == [2, 2]
    assert [group["photographs"] for group in body["groups"]] == [1, 1]
    assert body["unclustered"]["face_ids"] == [lone]
    assert isinstance(body["token"], str) and body["token"]
    assert cluster_ids(root) == before, "a preview writes nothing"


def test_preview_separates_only_at_a_later_step_and_reports_that_steps_epsilon(tmp_path):
    """0, 10, 48, 58 degrees at configured epsilon 0.4. The gap 10-48 is 38 degrees, 0.2120.

    Step 1 is 0.3 and step 2 is 0.225 - both still bridge 0.2120, so one group. Step 3 is
    0.16875, which does not: two groups, and the epsilon reported is step 3's."""
    root = configured(tmp_path, 0.4)
    faces = [plant(root, degrees) for degrees in (0, 10, 48, 58)]
    live = Served(root)
    try:
        status, body = split(live, 1, {"action": "preview"})
    finally:
        live.close()

    assert status == 200
    assert body["separated"] is True
    assert body["epsilon"] == pytest.approx(0.4 * FACTOR**3)
    assert [group["face_ids"] for group in body["groups"]] == [faces[:2], faces[2:]]
    assert body["unclustered"]["face_ids"] == []


def test_faces_that_never_separate_say_so_and_offer_nothing_to_confirm(tmp_path):
    """0, 1, 2 and 40 degrees at configured epsilon 0.4. 2-40 is 38 degrees, 0.2120.

    Steps 1-2 (0.3, 0.225) keep all four together; steps 3-4 (0.16875, 0.1265625) leave
    {0,1,2} as ONE group and 40 as noise. One group plus noise is NOT a split - so after all
    four steps pam says it will not separate, reports the last epsilon tried, and hands out
    no token a confirm could use."""
    root = configured(tmp_path, 0.4)
    faces = [plant(root, degrees) for degrees in (0, 1, 2, 40)]
    before = cluster_ids(root)
    live = Served(root)
    try:
        status, body = split(live, 1, {"action": "preview"})
        confirm_status, _ = split(live, 1, {"action": "confirm", "token": body["token"]})
    finally:
        live.close()

    assert status == 200
    assert body["separated"] is False
    assert body["epsilon"] == pytest.approx(0.4 * FACTOR**STEPS)
    assert body["token"] is None
    assert body["groups"] == []
    assert "not" in body["message"].lower() and "separate" in body["message"].lower()
    assert confirm_status == 409
    assert cluster_ids(root) == before
    assert all(cluster_ids(root)[face] == 1 for face in faces)


def test_split_of_a_named_cluster_is_refused_naming_the_person(tmp_path):
    """A cluster whose every face is named: nothing unnamed to split, so a 409 naming them."""
    root = configured(tmp_path, 0.3)
    emma = plant_person(root, "Emma")
    faces = [plant(root, degrees, person_id=emma) for degrees in (0, 10, 50, 60, 105)]
    before = cluster_ids(root)
    live = Served(root)
    try:
        status, body = split(live, 1, {"action": "preview"})
    finally:
        live.close()

    assert status == 409
    assert "Emma" in body["error"]
    assert cluster_ids(root) == before
    assert faces


def test_split_of_a_remainder_partitions_only_its_unnamed_faces(tmp_path):
    """S2-F1 (Stage 2): a named face sharing the cluster is neither previewed nor moved."""
    root = configured(tmp_path, 0.3)
    five_faces_that_separate_at_step_one(root)
    emma = plant_person(root, "Emma")
    named = plant(root, 5, person_id=emma)
    live = Served(root)
    try:
        status, preview = split(live, 1, {"action": "preview"})
        _, confirmed = split(live, 1, {"action": "confirm", "token": preview["token"]})
    finally:
        live.close()

    assert status == 200, preview
    assert preview["separated"] is True
    previewed = {f for group in preview["groups"] for f in group["face_ids"]}
    assert named not in previewed | set(preview["unclustered"]["face_ids"])
    assert confirmed["faces"] == 5
    assert cluster_ids(root)[named] == 1, "a named face was re-clustered by a split"


def test_split_with_no_epsilon_configured_is_refused_naming_the_key(tmp_path):
    root = configured(tmp_path, None)
    five_faces_that_separate_at_step_one(root)
    live = Served(root)
    try:
        status, body = split(live, 1, {"action": "preview"})
    finally:
        live.close()

    assert status == 409
    assert "[enrich.faces] cluster_epsilon" in body["error"]


def test_a_cluster_holding_two_embedding_widths_is_refused_with_a_sentence(tmp_path):
    """Stage 1 open item 2, closed: vectors of different widths cannot be compared, and
    `pam faces cluster` refuses the same state by name (A6's second half). Split's preview
    DBSCAN'd them anyway and the page got a 500 with no sentence. It now refuses with the
    same instruction the command gives, hands out no token and writes nothing."""
    root = configured(tmp_path, 0.3)
    five_faces_that_separate_at_step_one(root)
    odd = plant(root, 20)
    conn = _conn(root)
    try:
        conn.execute(
            "UPDATE face_instances SET embedding = ? WHERE id = ?",
            (struct.pack("<3f", 1.0, 0.0, 0.0), odd),
        )
        conn.commit()
    finally:
        conn.close()
    before = cluster_ids(root)
    live = Served(root)
    try:
        status, body = split(live, 1, {"action": "preview"})
    finally:
        live.close()

    assert status == 409, body
    assert "different widths" in body["error"]
    assert "pam faces purge" in body["error"]
    assert cluster_ids(root) == before


def test_split_of_an_empty_cluster_is_a_404(tmp_path):
    root = configured(tmp_path, 0.3)
    plant(root, 0, review_state=DISMISSED)
    live = Served(root)
    try:
        gone, _ = split(live, 1, {"action": "preview"})
        none, _ = split(live, 9, {"action": "preview"})
    finally:
        live.close()

    assert (gone, none) == (404, 404)


def test_split_refuses_a_missing_or_unknown_action_by_naming_the_field(tmp_path):
    root = configured(tmp_path, 0.3)
    five_faces_that_separate_at_step_one(root)
    live = Served(root)
    try:
        for body_in in (None, {}, {"action": "apply"}, {"action": 1}):
            status, body = split(live, 1, body_in)
            assert status == 400, body_in
            assert body["error"].startswith("action:"), body
    finally:
        live.close()


# --- confirm ---------------------------------------------------------------------------


def test_confirm_without_a_preview_changes_nothing(tmp_path):
    """SPLIT MUST NEVER CONFIRM WITHOUT PREVIEWING - with no token, a made-up token, or a
    token for a DIFFERENT cluster's preview."""
    root = configured(tmp_path, 0.3)
    five_faces_that_separate_at_step_one(root)
    for degrees in (0, 10, 50, 60):  # separates at step 1, as cluster 1 does
        plant(root, degrees, cluster_id=2)
    before = cluster_ids(root)
    live = Served(root)
    try:
        _, other = split(live, 2, {"action": "preview"})
        assert other["separated"] is True
        refusals = [
            split(live, 1, {"action": "confirm"}),
            split(live, 1, {"action": "confirm", "token": "0123456789abcdef"}),
            split(live, 1, {"action": "confirm", "token": other["token"]}),
        ]
    finally:
        live.close()

    assert [status for status, _ in refusals] == [409, 409, 409]
    assert cluster_ids(root) == before


def test_confirm_applies_the_previewed_partition_with_fresh_ids_above_every_row(tmp_path):
    """COLLISION-FREE AGAINST A ROW THE PAGE CANNOT SEE. The largest `cluster_id` in the
    table belongs to a BELOW-FLOOR face (12), and a DISMISSED face holds 11. An allocation
    that looked only at faces the page shows - or at the floor-filtered set - would hand out
    11 or 12 and merge strangers into them. Fresh ids are 13 and 14, in ascending order of
    each group's smallest face id; the noise face is unclustered; the old id 1 is gone."""
    root = configured(tmp_path, 0.3)
    a0, a10, b50, b60, lone = five_faces_that_separate_at_step_one(root)
    dismissed = plant(root, 0, cluster_id=11, review_state=DISMISSED)
    below = plant(root, 0, cluster_id=12, confidence=0.1)
    live = Served(root)
    try:
        _, preview = split(live, 1, {"action": "preview"})
        status, body = split(live, 1, {"action": "confirm", "token": preview["token"]})
    finally:
        live.close()

    assert status == 200
    after = cluster_ids(root)
    assert (after[a0], after[a10], after[b50], after[b60]) == (13, 13, 14, 14)
    assert after[lone] is None
    assert (after[dismissed], after[below]) == (11, 12)
    assert 1 not in after.values()
    assert body["cluster_ids"] == [13, 14]
    assert (body["faces"], body["photographs"]) == (5, 1)
    assert isinstance(body["undo"], str) and body["undo"]


def test_fresh_ids_clear_an_offerable_cluster_holding_the_largest_id(tmp_path):
    """The other half of the collision: the largest id is held by an ordinary, offerable,
    unnamed cluster elsewhere (10), and the non-offerable faces hold less (9). An allocation
    above only the RETAINED ids (`retained_cluster_ids`) would hand out 10 and merge this
    split's first group into that cluster."""
    root = configured(tmp_path, 0.3)
    a0, a10, b50, b60, _ = five_faces_that_separate_at_step_one(root)
    other = plant(root, 170, cluster_id=10)
    plant(root, 0, cluster_id=9, confidence=0.1)
    live = Served(root)
    try:
        _, preview = split(live, 1, {"action": "preview"})
        status, body = split(live, 1, {"action": "confirm", "token": preview["token"]})
    finally:
        live.close()

    assert status == 200
    after = cluster_ids(root)
    assert (after[a0], after[b50]) == (11, 12)
    assert after[other] == 10
    assert body["cluster_ids"] == [11, 12]


def test_confirm_after_the_cluster_changed_is_refused_and_writes_nothing(tmp_path):
    root = configured(tmp_path, 0.3)
    faces = five_faces_that_separate_at_step_one(root)
    live = Served(root)
    try:
        _, preview = split(live, 1, {"action": "preview"})
        conn = _conn(root)
        try:
            conn.execute(
                "UPDATE face_instances SET review_state = ? WHERE id = ?", (DISMISSED, faces[0])
            )
            conn.commit()
        finally:
            conn.close()
        before = cluster_ids(root)
        status, body = split(live, 1, {"action": "confirm", "token": preview["token"]})
    finally:
        live.close()

    assert status == 409
    assert "reload" in body["error"].lower()
    assert cluster_ids(root) == before


def test_confirm_leaves_below_floor_and_reviewed_faces_in_the_cluster_untouched(tmp_path):
    """The faces written are exactly the faces the page showed - by confirm AND by undo."""
    root = configured(tmp_path, 0.3)
    five_faces_that_separate_at_step_one(root)
    below = plant(root, 0, confidence=0.1)
    reviewed = plant(root, 0, review_state=DISMISSED)
    live = Served(root)
    try:
        _, preview = split(live, 1, {"action": "preview"})
        _, confirmed = split(live, 1, {"action": "confirm", "token": preview["token"]})
        assert (cluster_ids(root)[below], cluster_ids(root)[reviewed]) == (1, 1)
        assert confirmed["faces"] == 5
        status, _ = split(live, 1, {"action": "undo", "token": confirmed["undo"]})
    finally:
        live.close()

    assert status == 200
    assert (cluster_ids(root)[below], cluster_ids(root)[reviewed]) == (1, 1)


def test_a_write_that_fails_midway_leaves_every_cluster_id_unchanged(tmp_path, monkeypatch):
    """ONE TRANSACTION. The write lands every assignment but the last for real and then
    fails; the catalog must hold every face at its old id, and there must be nothing to undo.

    THE FAILURE IS INJECTED AT THE WRITE THAT WOULD COMPLETE THE REASSIGNMENT, not at the
    first write (Stage 1's M1877, F2). Failing the FIRST `set_cluster` call can only see a
    commit INSIDE `repo.set_cluster`: a handler that wrote one face, committed, and wrote the
    rest was green, because the commit it misplaced came after the call that failed. Here
    every call before the completing one goes through untouched, in whatever order and in as
    many statements as the handler likes, so a commit anywhere before the last face is
    written leaves a face at a new id - and the completing call itself writes all but one
    face before raising, so a commit inside `set_cluster` is still visible too."""
    from pam.catalog import repo
    from pam.catalog.repo import faces as faces_repo

    root = configured(tmp_path, 0.3)
    faces = five_faces_that_separate_at_step_one(root)
    before = cluster_ids(root)
    real = faces_repo.set_cluster
    unwritten = set(faces)
    fired: list[bool] = []

    def fail_at_the_completing_write(
        conn: sqlite3.Connection, assignments: list[tuple[int, int | None]]
    ) -> None:
        mine = [pair for pair in assignments if pair[0] in unwritten]
        if {face_id for face_id, _ in mine} != unwritten:
            real(conn, assignments)
            unwritten.difference_update(face_id for face_id, _ in assignments)
            return
        real(conn, mine[:-1])
        fired.append(True)
        raise sqlite3.OperationalError("disk I/O error, injected")

    live = Served(root)
    try:
        _, preview = split(live, 1, {"action": "preview"})
        monkeypatch.setattr(repo, "set_cluster", fail_at_the_completing_write)
        status, _ = split(live, 1, {"action": "confirm", "token": preview["token"]})
        monkeypatch.undo()
        after = cluster_ids(root)
        undo_status, _ = split(live, 1, {"action": "undo", "token": preview["token"]})
    finally:
        live.close()

    assert fired == [True], "the injected failure never fired, so this proves nothing"
    assert status == 500
    assert after == before
    assert undo_status == 409


# --- undo ------------------------------------------------------------------------------


def test_undo_restores_every_faces_prior_cluster_id(tmp_path):
    root = configured(tmp_path, 0.3)
    five_faces_that_separate_at_step_one(root)
    before = cluster_ids(root)
    live = Served(root)
    try:
        _, preview = split(live, 1, {"action": "preview"})
        _, confirmed = split(live, 1, {"action": "confirm", "token": preview["token"]})
        assert cluster_ids(root) != before
        status, body = split(live, 1, {"action": "undo", "token": confirmed["undo"]})
    finally:
        live.close()

    assert status == 200
    assert cluster_ids(root) == before
    assert (body["faces"], body["photographs"]) == (5, 1)


def test_a_second_undo_is_refused_and_writes_nothing(tmp_path):
    root = configured(tmp_path, 0.3)
    five_faces_that_separate_at_step_one(root)
    live = Served(root)
    try:
        _, preview = split(live, 1, {"action": "preview"})
        _, confirmed = split(live, 1, {"action": "confirm", "token": preview["token"]})
        first, _ = split(live, 1, {"action": "undo", "token": confirmed["undo"]})
        restored = cluster_ids(root)
        second, _ = split(live, 1, {"action": "undo", "token": confirmed["undo"]})
    finally:
        live.close()

    assert (first, second) == (200, 409)
    assert cluster_ids(root) == restored


def test_only_the_most_recent_split_can_be_undone(tmp_path):
    """NOT A STACK (the maintainer's ruling). Splitting cluster 2 after cluster 1 makes cluster
    1's undo token stale: a 409 that writes nothing, and cluster 2's undo still works."""
    root = configured(tmp_path, 0.3)
    five_faces_that_separate_at_step_one(root)
    for degrees in (0, 10, 50, 60):
        plant(root, degrees, cluster_id=2)
    live = Served(root)
    try:
        _, first_preview = split(live, 1, {"action": "preview"})
        _, first = split(live, 1, {"action": "confirm", "token": first_preview["token"]})
        _, second_preview = split(live, 2, {"action": "preview"})
        _, second = split(live, 2, {"action": "confirm", "token": second_preview["token"]})
        after_both = cluster_ids(root)
        stale, _ = split(live, 1, {"action": "undo", "token": first["undo"]})
        unchanged = cluster_ids(root)
        latest, _ = split(live, 2, {"action": "undo", "token": second["undo"]})
    finally:
        live.close()

    assert stale == 409
    assert unchanged == after_both
    assert latest == 200


def test_a_stale_undo_token_cannot_reverse_a_later_split_of_the_same_cluster(tmp_path):
    """F6 (Stage 1, M1881): the undo TOKEN is the only thing tying an undo to the confirm the
    user saw, and every other refusal in this module is reached through another operand.
    Split cluster 1, undo it, split cluster 1 AGAIN: the first split's token names the same
    cluster and the undo slot is full, so only the token can refuse it. Without that check a
    stale page reverses a split the user never undid - Step 8's "the undo window reverses a
    different write", on split."""
    root = configured(tmp_path, 0.3)
    five_faces_that_separate_at_step_one(root)
    live = Served(root)
    try:
        _, first_preview = split(live, 1, {"action": "preview"})
        _, first = split(live, 1, {"action": "confirm", "token": first_preview["token"]})
        undone, _ = split(live, 1, {"action": "undo", "token": first["undo"]})
        _, second_preview = split(live, 1, {"action": "preview"})
        _, second = split(live, 1, {"action": "confirm", "token": second_preview["token"]})
        after_second = cluster_ids(root)
        stale, _ = split(live, 1, {"action": "undo", "token": first["undo"]})
        unchanged = cluster_ids(root)
        latest, _ = split(live, 1, {"action": "undo", "token": second["undo"]})
    finally:
        live.close()

    assert undone == 200
    assert first["undo"] != second["undo"], "the two confirms must issue different tokens"
    assert stale == 409
    assert unchanged == after_second
    assert latest == 200


def test_undo_after_membership_changed_is_refused_and_writes_nothing(tmp_path):
    root = configured(tmp_path, 0.3)
    faces = five_faces_that_separate_at_step_one(root)
    live = Served(root)
    try:
        _, preview = split(live, 1, {"action": "preview"})
        _, confirmed = split(live, 1, {"action": "confirm", "token": preview["token"]})
        conn = _conn(root)
        try:
            conn.execute("UPDATE face_instances SET cluster_id = 99 WHERE id = ?", (faces[0],))
            conn.commit()
        finally:
            conn.close()
        before = cluster_ids(root)
        status, body = split(live, 1, {"action": "undo", "token": confirmed["undo"]})
    finally:
        live.close()

    assert status == 409
    assert cluster_ids(root) == before


def test_undo_after_a_face_row_vanished_is_a_sentence_not_a_crash(tmp_path):
    """F7 (Stage 1, M1889): the membership check's `face is None` operand. A face deleted
    under a running server - a catalog edited, a purge - is a membership change like a
    moved face, and must get the same 409 sentence with nothing written, not an
    `AttributeError` that reaches the page as a 500."""
    root = configured(tmp_path, 0.3)
    faces = five_faces_that_separate_at_step_one(root)
    live = Served(root)
    try:
        _, preview = split(live, 1, {"action": "preview"})
        _, confirmed = split(live, 1, {"action": "confirm", "token": preview["token"]})
        conn = _conn(root)
        try:
            conn.execute("DELETE FROM face_instances WHERE id = ?", (faces[0],))
            conn.commit()
        finally:
            conn.close()
        before = cluster_ids(root)
        status, body = split(live, 1, {"action": "undo", "token": confirmed["undo"]})
    finally:
        live.close()

    assert status == 409, body
    assert "moved since the split" in body["error"]
    assert cluster_ids(root) == before


def test_undo_does_not_survive_the_server(tmp_path):
    """SERVER MEMORY, the maintainer's ruling: a new server holds no undo, and says so."""
    root = configured(tmp_path, 0.3)
    five_faces_that_separate_at_step_one(root)
    live = Served(root)
    try:
        _, preview = split(live, 1, {"action": "preview"})
        _, confirmed = split(live, 1, {"action": "confirm", "token": preview["token"]})
    finally:
        live.close()
    after = cluster_ids(root)
    fresh = Served(root)
    try:
        status, _ = split(fresh, 1, {"action": "undo", "token": confirmed["undo"]})
    finally:
        fresh.close()

    assert status == 409
    assert cluster_ids(root) == after


# -------------------------------------------------------------------------------------
# THE LOAD-BEARING CONTROLS OF THIS MODULE (M10's C4). Hand-spelled, never derived.
# -------------------------------------------------------------------------------------

LOAD_BEARING_CONTROLS = {
    "test_split_of_a_remainder_partitions_only_its_unnamed_faces": (
        "S2-F1: split must act on the remainder the queue offered, never on named faces"
    ),
    "test_a_cluster_holding_two_embedding_widths_is_refused_with_a_sentence": (
        "open item 2: without the width check split's preview is a 500 on a cluster "
        "`pam faces cluster` would have refused by name"
    ),
    "test_undo_after_a_face_row_vanished_is_a_sentence_not_a_crash": (
        "F7: the `face is None` operand of undo's membership check; the moved-face test "
        "cannot reach it"
    ),
    "test_a_stale_undo_token_cannot_reverse_a_later_split_of_the_same_cluster": (
        "F6: the undo token operand; every other undo refusal is reached through "
        "`undo is None` or the cluster operand"
    ),
    "test_confirm_without_a_preview_changes_nothing": (
        "the task's named mutation: split must never confirm without previewing"
    ),
    "test_faces_that_never_separate_say_so_and_offer_nothing_to_confirm": (
        "A18's 'SAYS SO rather than inventing a split' - and one group plus noise is not two"
    ),
    "test_preview_separates_only_at_a_later_step_and_reports_that_steps_epsilon": (
        "the only test that can see the step loop stop early, late, or report the wrong epsilon"
    ),
    "test_confirm_applies_the_previewed_partition_with_fresh_ids_above_every_row": (
        "collision-free allocation against below-floor and dismissed rows"
    ),
    "test_fresh_ids_clear_an_offerable_cluster_holding_the_largest_id": (
        "collision-free allocation against an offerable cluster retained_cluster_ids omits"
    ),
    "test_a_write_that_fails_midway_leaves_every_cluster_id_unchanged": (
        "spec 8.2's ONE transaction"
    ),
    "test_confirm_after_the_cluster_changed_is_refused_and_writes_nothing": (
        "a stale preview must not be applied to a cluster that is no longer the one shown"
    ),
    "test_only_the_most_recent_split_can_be_undone": (
        "the maintainer's ruling: one undo of the latest split, not a stack"
    ),
    "test_undo_after_membership_changed_is_refused_and_writes_nothing": (
        "undo must not overwrite a change made after the split"
    ),
    "test_undo_restores_every_faces_prior_cluster_id": (
        "the split's undo, vision R6. MOVED here at M12 Task 1 from "
        "`tests/test_webui_api.py`'s FACES_SPLIT_ANCHORED_CONTROLS (M1879: this module "
        "deleted whole left 3525 passed, 0 failed)"
    ),
}

assert LOAD_BEARING_CONTROLS, "this module's control list is empty; it protects nothing"


def test_no_faces_split_control_in_this_file_can_be_deleted_without_saying_so():
    """The floor under this module's inventory (M10's C4, amendment R4)."""
    present = {name for name in globals() if name.startswith("test_")}
    missing = sorted(set(LOAD_BEARING_CONTROLS) - present)
    assert missing == [], f"a load-bearing control was deleted: {missing}"
