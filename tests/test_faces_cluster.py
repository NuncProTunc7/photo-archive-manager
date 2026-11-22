"""`pam faces cluster` (M11 Task 9, Steps 4, 5 and 6). The command, not the algorithm.

`tests/test_cluster.py` owns the arithmetic. This module owns everything that happens
around it: which faces the command may look at, the three refusals A6 and A12 require,
A21's preamble arriving BEFORE the work, the single transaction, and idempotence over a
real catalog.

EVERY CATALOG ROW HERE IS PLANTED WITH THE SUITE'S OWN SQL and read back the same way,
never through the production query that put it there. The embeddings are the `signs`
vectors from `tests/test_cluster.py` - exact unit vectors whose pairwise distances are
`0.125 * k` by hand - so every expectation about WHICH faces group together is a number
somebody can check rather than whatever the model happened to produce.
"""

import json
import sqlite3
import struct
from pathlib import Path

import pytest
from archives import FAKE_COMMAND
from click.testing import CliRunner
from test_cluster import signs

from pam import faces as faces_cmd
from pam.catalog import repo
from pam.catalog.db import connect
from pam.cli import cli, main
from pam.enrich import cluster
from pam.errors import EXIT_EXPECTED, PamError
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan

GEOMETRY = {"v": 1, "bbox": [0.1, 0.2, 0.3, 0.4], "source_w": 100, "source_h": 100}

# A21's preamble is three lines and they are asserted by content elsewhere; here the
# first word is enough to answer "did it print before the work".
PREAMBLE_FIRST_WORD = "Clustering"


def blob(vector) -> bytes:
    """A `signs` vector as design 4's BLOB, with the SUITE's own packer.

    `struct`, not `numpy.tobytes()`, so that `cluster.decode_embedding` and the writer of
    these bytes are not the same implementation.
    """
    return struct.pack(f"<{len(vector)}f", *vector)


def archive(tmp_path, *, epsilon="0.2"):
    """A scanned archive with a `pam.toml` carrying (or not carrying) an epsilon.

    Scanned for real, because `run_cluster` resolves an archive, takes the workspace lock,
    migrates and runs recovery before it does anything of its own - a hand-built catalog
    would skip four things a user cannot skip.
    """
    root = tmp_path / "photos"
    root.mkdir()
    (root / "IMG_1.JPG").write_bytes(b"the only copy of IMG_1.JPG")
    (root / "IMG_1.JPG.tags.json").write_text(
        json.dumps({"DateTimeOriginal": "2019:07:04 18:23:11"}), encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    ws = workspace_if_exists(root)
    assert ws is not None
    lines = ["[enrich.faces]", 'model_dir = ""']
    if epsilon is not None:
        lines.append(f"cluster_epsilon = {epsilon}")
    ws.config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


def plant(
    root,
    vector,
    *,
    sha=None,
    confidence=0.9,
    cluster_id=None,
    person_id=None,
    review_state=None,
    model=("yunet+sface", "abc123"),
):
    """One `face_instances` row, with the SUITE's own INSERT. Returns its id."""
    ws = workspace_if_exists(root)
    conn = connect(ws.db_path)
    try:
        cur = conn.execute(
            "INSERT INTO face_instances (sha256, bbox_json, embedding, det_confidence,"
            " cluster_id, person_id, review_state, model_name, model_version)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sha or "sha-of-IMG_1",
                json.dumps(GEOMETRY, sort_keys=True),
                blob(vector) if isinstance(vector, tuple) else vector,
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
    finally:
        conn.close()


def plant_person(root, label):
    ws = workspace_if_exists(root)
    conn = connect(ws.db_path)
    try:
        cur = conn.execute("INSERT INTO persons (label) VALUES (?)", (label,))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def read_clusters(root):
    """`{face id: cluster id}` straight out of the table, with the suite's own SELECT."""
    ws = workspace_if_exists(root)
    conn = connect(ws.db_path)
    try:
        conn.row_factory = sqlite3.Row
        return {
            int(r["id"]): (None if r["cluster_id"] is None else int(r["cluster_id"]))
            for r in conn.execute("SELECT id, cluster_id FROM face_instances")
        }
    finally:
        conn.close()


def partition_of(root):
    """The catalog's clustering as a SET OF FROZENSETS of face ids, unclustered dropped.

    STEP 6'S ASSERTION SHAPE, and it is the whole point of the idempotence rows below.
    "Ids may be renumbered; the partition may not." An `==` on `read_clusters` passes for
    a renumbering and fails for nothing - it cannot tell a stable algorithm from one that
    reshuffled every group, because both change the numbers.
    """
    groups: dict[int, set[int]] = {}
    for face_id, number in read_clusters(root).items():
        if number is not None:
            groups.setdefault(number, set()).add(face_id)
    return {frozenset(members) for members in groups.values()}


def run(root, *, announce=None):
    """`run_cluster` with a capturing announcer. Returns `(report, printed lines)`."""
    printed: list[str] = []
    report = faces_cmd.run_cluster(
        root, announce=printed.append if announce is None else announce
    )
    return report, printed


# ---------------------------------------------------------------------------
# 1. A12 - THE EPSILON NOBODY HAS MEASURED. The task's headline.
# ---------------------------------------------------------------------------


def test_with_no_cluster_epsilon_the_command_refuses_and_NAMES_THE_KEY(tmp_path):
    """A12: "there is NO DEFAULT, and `pam faces cluster` REFUSES TO RUN".

    THE KEY IS THE ASSERTION. A refusal that says "clustering is not configured" sends a
    user to the documentation; one that spells `[enrich.faces] cluster_epsilon` sends them
    to the line they have to edit (UX 1.4, never a dead end). Driven with a `pam.toml`
    that has the table and omits the key, which is exactly what ships.
    """
    root = archive(tmp_path, epsilon=None)
    plant(root, signs())
    with pytest.raises(PamError) as caught:
        run(root)
    message = str(caught.value)
    assert "[enrich.faces] cluster_epsilon" in message
    assert "pam.toml" in message


def test_the_refusal_suggests_no_value_of_its_own(tmp_path):
    """It must not propose a number, and that is a deliberate absence rather than a gap.

    A11's ruling: "the value is not normative until the evidence exists". A refusal
    reading "try 0.4" is the guess the refusal exists to prevent, arriving in the voice of
    the product. The numbers checked for are the ones this project has actually written
    down somewhere - rev 2's inherited 0.35 and the spike's three measured distances -
    because those are the ones a well-meaning edit would reach for.
    """
    root = archive(tmp_path, epsilon=None)
    with pytest.raises(PamError) as caught:
        run(root)
    message = str(caught.value)
    for guess in ("0.35", "0.4", "0.3", "0.4678", "0.5"):
        assert guess not in message, guess


def test_nothing_is_written_when_the_epsilon_is_missing(tmp_path):
    """The refusal is BEFORE the catalog, so a pre-existing clustering survives it.

    Watches the dangerous outcome rather than the message: a command that refused after
    renumbering would print the same sentence. `M697`'s shape - "a test that asserts the
    OUTCOME is unchanged cannot tell 'never did it' from 'did it and left no trace'" - is
    answered here by the state being NON-TRIVIAL: face 1 carries cluster 7 before the run
    and must still carry cluster 7 after it, which a clear-then-refuse would destroy.
    """
    root = archive(tmp_path, epsilon=None)
    face = plant(root, signs(), cluster_id=7)
    with pytest.raises(PamError):
        run(root)
    assert read_clusters(root) == {face: 7}


def test_a_configured_epsilon_is_the_one_that_decides_the_groups(tmp_path):
    """The key is READ, not merely validated - two runs at two epsilons, two answers.

    Faces at exactly 0.25 (two flips). At epsilon 0.25 they are one group; at 0.125 they
    are two singletons. Without this row the command passes for an implementation that
    hard-codes an epsilon of its own and range-checks the configured one.
    """
    root = archive(tmp_path, epsilon="0.25")
    a = plant(root, signs())
    b = plant(root, signs(0, 1))
    report, _ = run(root)
    assert report.epsilon == 0.25
    assert partition_of(root) == {frozenset({a, b})}

    ws = workspace_if_exists(root)
    ws.config_path.write_text(
        '[enrich.faces]\nmodel_dir = ""\ncluster_epsilon = 0.125\n', encoding="utf-8"
    )
    report, _ = run(root)
    assert report.epsilon == 0.125
    assert partition_of(root) == set()


# ---------------------------------------------------------------------------
# 2. A6 - one archive, one generation; and one embedding width.
# ---------------------------------------------------------------------------


def test_two_model_generations_are_refused_with_both_named_and_a_route_out(tmp_path):
    """A6: "`pam faces cluster` refuses the same state" as the enricher.

    Both generations and both counts are in the sentence, because the number is what a
    user needs to decide whether the purge is worth it. The route is export -> purge ->
    enrich, in that order: export FIRST is what keeps the names already given (I3).
    """
    root = archive(tmp_path)
    plant(root, signs(), model=("yunet+sface", "old"))
    plant(root, signs(0), model=("yunet+sface", "new"))
    with pytest.raises(PamError) as caught:
        run(root)
    message = str(caught.value)
    assert "old" in message and "new" in message
    assert message.index("pam faces export") < message.index("pam faces purge")
    assert message.index("pam faces purge") < message.index("pam enrich faces")


def test_one_generation_over_many_faces_is_not_a_refusal(tmp_path):
    """The negative control: the refusal counts GENERATIONS, not rows.

    Five faces sharing one `(model_name, model_version)`. Without this row the refusal
    above passes for an implementation that refuses any archive with more than one face.
    """
    root = archive(tmp_path)
    for index in range(5):
        plant(root, signs(index), sha=f"sha-{index}")
    report, _ = run(root)
    assert report.considered == 5


def test_embeddings_of_two_widths_are_refused_even_under_one_generation(tmp_path):
    """A6's SECOND half, which the generation check cannot reach.

    "ADDITIONALLY refuses embeddings of differing width." Two rows carrying the same
    `(model_name, model_version)` - a hand-edited catalog, an import - and 16-value and
    4-value vectors. The generation check passes them; these cannot be compared at all.
    Both widths are named, because "some of your embeddings are the wrong size" tells a
    user nothing they can act on.
    """
    root = archive(tmp_path)
    plant(root, signs())
    plant(root, struct.pack("<4f", 1.0, 0.0, 0.0, 0.0), sha="sha-2")
    with pytest.raises(PamError) as caught:
        run(root)
    message = str(caught.value)
    assert "16 values" in message and "4 values" in message


def test_a_second_width_that_is_BELOW_THE_FLOOR_is_not_a_refusal(tmp_path):
    """The width check reads the faces the pass will CLUSTER, not the whole table.

    A11 applies the floor at read time, so a 512-value row left over below the floor is
    not in the comparison and must not stop a run. This is the row that distinguishes
    "checked `faces_for_clustering`'s result" from "checked `SELECT * FROM
    face_instances`", and the two are indistinguishable on every other input.
    """
    root = archive(tmp_path)
    a = plant(root, signs())
    b = plant(root, signs(0), sha="sha-2")
    plant(root, struct.pack("<4f", 1.0, 0.0, 0.0, 0.0), sha="sha-3", confidence=0.1)
    report, _ = run(root)
    assert report.considered == 2
    assert partition_of(root) == {frozenset({a, b})}


# ---------------------------------------------------------------------------
# 3. WHICH FACES THE PASS MAY SEE - the three filters, one at a time.
# ---------------------------------------------------------------------------


def test_a_named_face_is_left_alone_and_the_summary_says_how_many(tmp_path):
    """I3: a pass must never re-decide a name a human gave, and must say it did not.

    The named face keeps the cluster id it had - not `None`, and not a new number - and
    the closing block reports the count. Being excluded SILENTLY is what makes a user
    think their names were lost, which is why the sentence is asserted as well as the row.
    """
    root = archive(tmp_path)
    person = plant_person(root, "Emma")
    named = plant(root, signs(), cluster_id=4, person_id=person)
    a = plant(root, signs(8), sha="sha-2")
    b = plant(root, signs(8, 9), sha="sha-3")
    report, _ = run(root)

    assert read_clusters(root)[named] == 4
    assert report.already_named == 1
    assert partition_of(root) >= {frozenset({a, b})}
    text = "\n".join(faces_cmd.cluster_lines(report))
    assert "1 faces you have already named were left alone." in text


def test_a_reviewed_away_face_is_neither_clustered_nor_counted(tmp_path):
    """A13: "a face in either non-NULL state is excluded from clustering".

    BOTH states, parametrised over `repo.REVIEW_STATES`' own vocabulary spelled out here
    from spec 4 rev 22 rather than read off the constant - a test that iterated
    `repo.REVIEW_STATES` would agree with any value of it.
    """
    for state in ("not_a_face", "dismissed"):
        here = tmp_path / state
        here.mkdir()
        root = archive(here)
        hidden = plant(root, signs(), review_state=state)
        a = plant(root, signs(0), sha="sha-2")
        b = plant(root, signs(0, 1), sha="sha-3")
        report, _ = run(root)
        assert report.considered == 2, state
        assert read_clusters(root)[hidden] is None, state
        assert partition_of(root) == {frozenset({a, b})}, state


def test_a_detection_below_the_floor_is_not_considered(tmp_path):
    """A11's read-time floor, at the default 0.4, in both directions.

    Two faces that would group, one of them at 0.39. The pair does not form. Raising the
    other one's confidence above the floor - without touching a stored row - forms it,
    which is the reversibility A11 exists to provide.
    """
    root = archive(tmp_path)
    a = plant(root, signs(), confidence=0.9)
    b = plant(root, signs(0), sha="sha-2", confidence=0.39)
    report, _ = run(root)
    assert report.considered == 1
    assert partition_of(root) == set()

    ws = workspace_if_exists(root)
    # THE ROW DESIGN 11 PRINTS, and it only became writable at Task 9.5. Until then
    # `config.py` built a `TierBounds(**table)` - a replacement, not a merge - so
    # `low`/`high` were required of every row and this line had to restate two fields
    # A11 had removed from the `faces` row. Task 9.5 made the override a merge; the
    # config-level assertions live in `tests/test_config.py`, and what this line adds
    # is that the row 11 prints reaches `pam faces cluster` and changes what it reads.
    ws.config_path.write_text(
        '[enrich.faces]\nmodel_dir = ""\ncluster_epsilon = 0.2\n\n'
        "[tiers]\nfaces = { floor = 0.3 }\n",
        encoding="utf-8",
    )
    report, _ = run(root)
    assert report.considered == 2
    assert partition_of(root) == {frozenset({a, b})}


def test_a_faces_floor_ABOVE_THE_REMOVED_LOW_reaches_the_command(tmp_path):
    """DESIGN 11 REV 24 THROUGH THE COMMAND, and the case that amendment exists to permit.

    `pam/enrich/cluster.py`'s scale-ceiling refusal tells the user to raise
    `[tiers] faces.floor`, and on a detector whose candidate threshold is 0.10 the natural
    place to go is 0.8-0.9. Until Task 9.6 every value above the shipped `low` of 0.6 - a
    key A11 REMOVED from this row - was a ConfigError quoting a rule about keys the row
    does not have, so pam shipped advice that failed above 0.6.

    The config-level assertions live in `tests/test_config.py`. THIS ROW EXISTS BECAUSE
    THEY ALL DID (M1831): with that module deleted whole, restoring the over-broad
    ordering was green over 3165 tests - the fix had no anchor outside the module it was
    written in, and neither did the value the product's own refusal asks the user for.
    """
    root = archive(tmp_path)
    high = plant(root, signs(), confidence=0.95)
    plant(root, signs(0), sha="sha-2", confidence=0.7)
    ws = workspace_if_exists(root)
    ws.config_path.write_text(
        '[enrich.faces]\nmodel_dir = ""\ncluster_epsilon = 0.2\n\n'
        "[tiers]\nfaces = { floor = 0.9 }\n",
        encoding="utf-8",
    )

    report, _ = run(root)

    # pam ACCEPTED the row (a ConfigError here is the defect), and the command READ it:
    # 0.95 is above the floor and 0.7 is not, so the pair that would have formed does not.
    assert report.considered == 1
    assert partition_of(root) == set()
    assert read_clusters(root)[high] is None


# ---------------------------------------------------------------------------
# 4. THE SUMMARY - clarification 1's singletons, and the counts.
# ---------------------------------------------------------------------------


def test_the_summary_reports_faces_considered_groups_formed_and_singletons(tmp_path):
    """Design clarification 1: `pam faces cluster` SAYS a face seen once is not offered.

    Three faces: two within epsilon and one far away. The numbers are 3 / 1 / 1 and every
    one of them is in the text. The singleton sentence carries its REASON, because
    "1 singleton" leaves a user hunting for it on the labeling page.
    """
    root = archive(tmp_path)
    plant(root, signs())
    plant(root, signs(0), sha="sha-2")
    plant(root, signs(0, 1, 2, 3, 4, 5, 6, 7), sha="sha-3")
    report, _ = run(root)
    assert (report.considered, report.clusters, report.singletons) == (3, 1, 1)
    text = "\n".join(faces_cmd.cluster_lines(report))
    assert "3 faces considered" in text
    assert "1 groups formed" in text
    assert "1 of them appear only once and were left ungrouped" in text
    assert "pam does not offer a face it has seen once for naming." in text


def test_the_summary_omits_the_singleton_and_named_lines_when_there_are_none(tmp_path):
    """A count of zero is not news, and a line reading "0 faces" is noise.

    The negative control under the row above: without it "the singleton sentence is
    present" passes for an implementation that always prints it.
    """
    root = archive(tmp_path)
    plant(root, signs())
    plant(root, signs(0), sha="sha-2")
    report, _ = run(root)
    text = "\n".join(faces_cmd.cluster_lines(report))
    assert "appear only once" not in text
    assert "already named" not in text


def test_the_closing_block_points_at_the_labeling_page(tmp_path):
    """UX 3's chain: what a user does after clustering is name the groups."""
    root = archive(tmp_path)
    plant(root, signs())
    report, _ = run(root)
    text = "\n".join(faces_cmd.cluster_lines(report))
    assert "Next: pam faces label" in text


# ---------------------------------------------------------------------------
# 5. A21 - the scale boundary, published BEFORE the work and refused above it.
# ---------------------------------------------------------------------------


def test_the_preamble_is_announced_BEFORE_the_clustering_runs(tmp_path):
    """A21: "before starting publishes the face count, the estimated time and memory".

    ORDER IS THE PROPERTY, not presence, and presence is what a summary-only test would
    check. Measured by an announcer that records how many faces had been clustered at the
    moment each line arrived: `cluster.dbscan` is wrapped so the first call flips a flag,
    and every announced line must arrive with that flag still false.
    """
    root = archive(tmp_path)
    plant(root, signs())
    plant(root, signs(0), sha="sha-2")
    started = {"clustering": False}
    real = cluster.dbscan

    def watched(*args, **kwargs):
        started["clustering"] = True
        return real(*args, **kwargs)

    seen: list[bool] = []
    faces_cmd.cluster.dbscan = watched  # type: ignore[assignment]
    try:
        faces_cmd.run_cluster(
            root, announce=lambda line: seen.append(started["clustering"]) or None
        )
    finally:
        faces_cmd.cluster.dbscan = real  # type: ignore[assignment]
    assert seen, "A21's preamble printed nothing at all"
    assert not any(seen), "a preamble line arrived after the clustering had started"


def test_the_preamble_carries_the_face_count_the_estimate_and_the_ceiling(tmp_path):
    """The three facts, in the text the user really sees, from the real command."""
    root = archive(tmp_path)
    plant(root, signs())
    plant(root, signs(0), sha="sha-2")
    _, printed = run(root)
    text = "\n".join(printed)
    assert text.startswith(PREAMBLE_FIRST_WORD)
    assert "2 faces" in text
    assert "memory" in text
    assert f"{cluster.TESTED_CEILING:,}" in text


def test_above_the_tested_ceiling_the_command_refuses_and_writes_nothing(tmp_path, monkeypatch):
    """A21's refusal, driven through the real command with the ceiling moved down to 2.

    Moving the constant rather than planting 100,001 rows: the ceiling is a measured
    number and the thing under test is the WIRING - that `run_cluster` consults it at all,
    before it clusters. `estimate` reads `TESTED_CEILING` at call time, so the monkeypatch
    reaches both the refusal and the sentence.

    And it must write NOTHING: the pre-existing cluster ids survive, so a refusal that
    fired after the renumbering would be caught by the state rather than by the message.
    """
    monkeypatch.setattr(cluster, "TESTED_CEILING", 2)
    root = archive(tmp_path)
    ids = [plant(root, signs(index), sha=f"sha-{index}", cluster_id=9) for index in range(3)]
    with pytest.raises(PamError) as caught:
        run(root)
    assert "3" in str(caught.value) and "2" in str(caught.value)
    assert read_clusters(root) == dict.fromkeys(ids, 9)


def test_exactly_at_the_ceiling_the_command_runs(tmp_path, monkeypatch):
    """The other side of A21's boundary: the measured number is INCLUSIVE.

    Without it the refusal above passes for a `>=`, which would refuse an archive pam has
    actually run.
    """
    monkeypatch.setattr(cluster, "TESTED_CEILING", 2)
    root = archive(tmp_path)
    a = plant(root, signs())
    b = plant(root, signs(0), sha="sha-2")
    report, _ = run(root)
    assert partition_of(root) == {frozenset({a, b})}
    assert report.considered == 2


# ---------------------------------------------------------------------------
# 6. IDEMPOTENCE (Step 6) and collision-free allocation.
# ---------------------------------------------------------------------------


def test_two_runs_over_an_unchanged_catalog_give_the_SAME_PARTITION(tmp_path):
    """Step 6, asserted as a SET OF FROZENSETS.

    "Ids may be renumbered; the partition may not." The comparison below is between two
    `partition_of` results - sets of frozensets of face ids - so a run that reshuffled
    every group fails it and a run that merely renumbered them does not. An `==` on the
    raw `{face_id: cluster_id}` map would be the weaker assertion in both directions: it
    fails for a harmless renumbering and it cannot distinguish "same groups" from "same
    numbers over different groups".
    """
    root = archive(tmp_path)
    for index in range(4):
        plant(root, signs(), sha=f"a-{index}")
    for index in range(3):
        plant(root, signs(0, 1, 2, 3, 4, 5, 6, 7), sha=f"b-{index}")
    plant(root, signs(0, 1, 2, 3), sha="lonely")

    run(root)
    first = partition_of(root)
    run(root)
    second = partition_of(root)
    assert first == second
    assert len(first) == 2
    assert {len(group) for group in first} == {3, 4}


def test_a_third_and_a_tenth_run_still_give_that_partition(tmp_path):
    """Idempotence at N > 2, because "stable once" and "stable" are different claims.

    A pass that offset its numbering by the retained maximum on every run would drift if
    the retained set were computed wrongly; ten runs is where that shows.
    """
    root = archive(tmp_path)
    for index in range(3):
        plant(root, signs(), sha=f"a-{index}")
    run(root)
    expected = partition_of(root)
    for _ in range(9):
        run(root)
        assert partition_of(root) == expected


def test_re_running_never_puts_STRANGERS_into_a_cluster_a_user_has_NAMED(tmp_path):
    """The collision A12's renumbering creates, and the reason `retained_cluster_ids` exists.

    THE SEQUENCE IS THE ORDINARY ONE, not a contrived state. Run the pass; a user names
    the first cluster; run the pass again. The named faces leave the pass (I3), so the
    numbering starts over - and without an offset it hands cluster 1 to a completely
    different set of faces while the named person is still sitting in cluster 1. That is
    two people under one name, which is vision R6's failure arriving with no user action
    at all.

    Asserted with the suite's own SELECT: the named person's cluster must hold exactly the
    faces that were named, and no others. (It was read through `repo.cluster_faces`, a
    production oracle; since S2-F1 that read returns only UNNAMED faces by design, so it
    would report this cluster as empty whether or not strangers had been added to it.)
    """
    root = archive(tmp_path)
    named_a = plant(root, signs(), sha="named-1")
    named_b = plant(root, signs(0), sha="named-2")
    stranger_a = plant(root, signs(0, 1, 2, 3, 4, 5, 6, 7), sha="stranger-1")
    stranger_b = plant(root, signs(1, 2, 3, 4, 5, 6, 7), sha="stranger-2")

    run(root)
    clusters = read_clusters(root)
    named_cluster = clusters[named_a]
    assert named_cluster is not None and clusters[named_b] == named_cluster

    person = plant_person(root, "Emma")
    ws = workspace_if_exists(root)
    conn = connect(ws.db_path)
    try:
        conn.execute(
            "UPDATE face_instances SET person_id = ? WHERE cluster_id = ?",
            (person, named_cluster),
        )
        conn.commit()
    finally:
        conn.close()

    run(root)

    members = {
        face for face, cluster in read_clusters(root).items() if cluster == named_cluster
    }
    assert members == {named_a, named_b}, (
        "a later clustering pass reused the cluster id of a person the user had named"
    )
    assert read_clusters(root)[stranger_a] not in (None, named_cluster)
    assert read_clusters(root)[stranger_b] not in (None, named_cluster)


def test_a_face_that_stops_grouping_is_UNCLUSTERED_rather_than_left_stale(tmp_path):
    """The pass writes `None` as well as numbers, or a stale group outlives its evidence.

    Two faces group; one is then reviewed away; the survivor must end up with no cluster
    at all rather than keeping the number it had. A `set_cluster` call that skipped the
    noise rows would leave a one-face cluster on the labeling page for ever.
    """
    root = archive(tmp_path)
    a = plant(root, signs())
    b = plant(root, signs(0), sha="sha-2")
    run(root)
    assert partition_of(root) == {frozenset({a, b})}

    ws = workspace_if_exists(root)
    conn = connect(ws.db_path)
    try:
        conn.execute("UPDATE face_instances SET review_state = 'dismissed' WHERE id = ?", (b,))
        conn.commit()
    finally:
        conn.close()

    run(root)
    assert read_clusters(root)[a] is None


# ---------------------------------------------------------------------------
# 7. ONE TRANSACTION (Step 4).
# ---------------------------------------------------------------------------


def test_an_interruption_during_the_write_leaves_every_cluster_id_untouched(tmp_path):
    """ "writes `cluster_id` IN ONE TRANSACTION so an interruption cannot leave a partially
    renumbered catalog".

    Driven by making the write itself raise AFTER it has issued its statements: the
    `executemany` has run on the connection and the commit has not. A catalog renumbered
    halfway is one in which some faces carry this pass's numbers and some carry the last
    pass's, and nothing downstream could tell which - so the assertion is that the state
    is EXACTLY what it was, over a non-trivial starting state (every face at cluster 9).

    WATCHES THE DANGEROUS CALL, not only the outcome: `set_cluster` is spied on, so a
    production change that split the write into two commits fails on the first one's
    landing rather than escaping because the second never ran.
    """
    root = archive(tmp_path)
    ids = [
        plant(root, signs(index % 2), sha=f"sha-{index}", cluster_id=9) for index in range(4)
    ]
    real = repo.set_cluster
    calls: list[int] = []

    def exploding(conn, assignments):
        calls.append(len(assignments))
        real(conn, assignments)
        raise RuntimeError("power cut")

    faces_cmd.repo.set_cluster = exploding  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError, match="power cut"):
            run(root)
    finally:
        faces_cmd.repo.set_cluster = real  # type: ignore[assignment]
    assert calls == [4], f"the write was not one call over every face: {calls}"
    assert read_clusters(root) == dict.fromkeys(ids, 9)


# ---------------------------------------------------------------------------
# 8. THE COMMAND AS A USER MEETS IT.
# ---------------------------------------------------------------------------


def test_pam_faces_cluster_exits_0_and_prints_the_preamble_then_the_summary(tmp_path):
    """End to end through click, which is where the exit-code contract lives (spec 13)."""
    root = archive(tmp_path)
    plant(root, signs())
    plant(root, signs(0), sha="sha-2")
    result = CliRunner().invoke(cli, ["faces", "cluster", "--archive", str(root)])
    assert result.exit_code == 0, result.output
    assert result.output.index(PREAMBLE_FIRST_WORD) < result.output.index("faces considered")
    assert "1 groups formed" in result.output


def test_pam_faces_cluster_exits_1_with_no_epsilon(tmp_path, capsys):
    """A12's refusal is an EXPECTED failure - exit 1, no traceback (spec 13).

    Through `pam.cli.main`, not `CliRunner`, because the exit-code mapping lives there:
    `CliRunner().invoke(cli, ...)` catches the `PamError` before `main` ever sees it and
    would pass for a command whose refusal crashed the process with exit 2.
    """
    root = archive(tmp_path, epsilon=None)
    plant(root, signs())
    assert main(["faces", "cluster", "--archive", str(root)]) == EXIT_EXPECTED
    captured = capsys.readouterr()
    assert "[enrich.faces] cluster_epsilon" in captured.err
    assert "Traceback" not in captured.err


def test_pam_faces_cluster_refuses_an_archive_pam_has_not_scanned(tmp_path, capsys):
    """Spec 3: an unknown folder is a refusal naming `pam scan`, not an empty report."""
    unknown = tmp_path / "elsewhere"
    unknown.mkdir()
    assert main(["faces", "cluster", "--archive", str(unknown)]) == EXIT_EXPECTED
    assert "pam scan" in capsys.readouterr().err


def test_an_archive_with_no_faces_at_all_reports_zero_rather_than_failing(tmp_path):
    """The state a user is in before `pam enrich faces` has ever run.

    `dbscan` over an empty mapping, `estimate` over 0 faces, and a summary that says so.
    UX 1.4: never a dead end, so the `Next:` line still points somewhere.
    """
    root = archive(tmp_path)
    report, printed = run(root)
    assert (report.considered, report.clusters, report.singletons) == (0, 0, 0)
    assert "0 faces" in "\n".join(printed)
    assert "Next: pam faces label" in "\n".join(faces_cmd.cluster_lines(report))


def test_clustering_writes_a_session_log_and_names_it_on_stderr(tmp_path):
    """Spec 13: every archive-scoped command leaves a log and says where it is."""
    root = archive(tmp_path)
    plant(root, signs())
    report, _ = run(root)
    assert report.log_path is not None and report.log_path.exists()
    assert "faces cluster start" in report.log_path.read_text(encoding="utf-8")


def test_clustering_renames_nothing_on_disk(tmp_path):
    """Design 8.1 and the milestone's third governing sentence: nothing here renames.

    The archive's file names before and after, compared as a set. A clustering pass that
    could touch a photograph would be a clustering pass that could lose one.
    """
    root = archive(tmp_path)
    plant(root, signs())
    plant(root, signs(0), sha="sha-2")
    before = sorted(path.name for path in root.iterdir())
    run(root)
    assert sorted(path.name for path in root.iterdir()) == before


# ---------------------------------------------------------------------------
# 9. `repo.retained_cluster_ids` - the complement, asserted as a PARTITION.
# ---------------------------------------------------------------------------


def test_the_two_reads_partition_the_table(tmp_path):
    """`faces_for_clustering` and `retained_cluster_ids` write the same predicate twice.

    SQLite has no way to say "the complement of that query", so the two WHERE clauses are
    written out separately and could drift. This asserts the partition from the OUTSIDE,
    over a catalog holding a row of every shape: eligible, named, reviewed away, and below
    the floor. Every id is in exactly one side, and the retained side's cluster ids are
    exactly the ones the retained rows carry.
    """
    root = archive(tmp_path)
    eligible = plant(root, signs(), sha="a", cluster_id=3)
    named = plant(root, signs(), sha="b", cluster_id=5, person_id=plant_person(root, "Emma"))
    hidden = plant(root, signs(), sha="c", cluster_id=6, review_state="dismissed")
    low = plant(root, signs(), sha="d", cluster_id=7, confidence=0.1)
    unclustered_low = plant(root, signs(), sha="e", confidence=0.1)

    ws = workspace_if_exists(root)
    conn = connect(ws.db_path)
    try:
        considered = {face.id for face in repo.faces_for_clustering(conn, 0.4)}
        retained = repo.retained_cluster_ids(conn, 0.4)
        every = {int(r[0]) for r in conn.execute("SELECT id FROM face_instances")}
    finally:
        conn.close()

    assert considered == {eligible}
    assert every - considered == {named, hidden, low, unclustered_low}
    # The retained SIDE's cluster ids, and not the eligible row's 3: an implementation
    # that returned every cluster id in the table would include it.
    assert retained == [5, 6, 7]


def test_retained_cluster_ids_is_empty_when_every_face_is_eligible(tmp_path):
    """The negative control: an archive nobody has named has no retained ids.

    Without it the row above passes for an implementation that returns every distinct
    cluster id there is, which would make the offset grow on every run for ever.
    """
    root = archive(tmp_path)
    plant(root, signs(), sha="a", cluster_id=3)
    plant(root, signs(), sha="b", cluster_id=4)
    ws = workspace_if_exists(root)
    conn = connect(ws.db_path)
    try:
        assert repo.retained_cluster_ids(conn, 0.4) == []
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# THE DELETION FLOOR (M10's C4, and `M1670`'s lesson about the instruments).
#
# HAND-SPELLED, NEVER DERIVED FROM `globals()`.
# ---------------------------------------------------------------------------

# The cross-module anchor that stood here, under `tests/test_config.py`, M1831, was absorbed into
# `tests/census.py` at M12 Task 1 (m11.md condition C4): one hand-written inventory outside
# every guarded module, checked at collection by `conftest.py`.

LOAD_BEARING_CONTROLS = {
    "test_a_faces_floor_ABOVE_THE_REMOVED_LOW_reaches_the_command": (
        "design 11 rev 24 at the COMMAND level, and the only assertion outside "
        "tests/test_config.py that a faces floor above the removed `low` of 0.6 loads "
        "at all (M1831: that module deleted, the defect was green over 3165 tests)"
    ),
    "test_with_no_cluster_epsilon_the_command_refuses_and_NAMES_THE_KEY": (
        "A12's headline. The only assertion in the repository that the key is NAMED, "
        "which is what makes the refusal actionable rather than a dead end"
    ),
    "test_the_refusal_suggests_no_value_of_its_own": (
        "A11's ruling that the value is not normative until the evidence exists. "
        "Nothing else stops a well-meaning edit putting 0.35 back into a sentence"
    ),
    "test_nothing_is_written_when_the_epsilon_is_missing": (
        "M697's shape: the refusal must fire BEFORE the catalog is touched, watched "
        "over a non-trivial prior state rather than over an empty table"
    ),
    "test_a_second_width_that_is_BELOW_THE_FLOOR_is_not_a_refusal": (
        "the only row distinguishing 'checked the faces the pass will cluster' from "
        "'checked every row in face_instances'; A11's floor makes them different"
    ),
    "test_the_preamble_is_announced_BEFORE_the_clustering_runs": (
        "A21's ORDER, which is the whole of the requirement. Presence is what a "
        "summary-only test checks and presence is satisfied by printing it afterwards"
    ),
    "test_re_running_never_puts_STRANGERS_into_a_cluster_a_user_has_NAMED": (
        "vision R6 through the ordinary sequence cluster -> name -> cluster. The only "
        "thing standing behind repo.retained_cluster_ids existing at all"
    ),
    "test_an_interruption_during_the_write_leaves_every_cluster_id_untouched": (
        "Step 4's single transaction, watched as the CALL and not only as the rows"
    ),
    "test_two_runs_over_an_unchanged_catalog_give_the_SAME_PARTITION": (
        "Step 6's idempotence, as a set of frozensets. An id-equality assertion passes "
        "for a renumbering and fails for nothing"
    ),
    "test_the_two_reads_partition_the_table": (
        "the duplicated predicate between faces_for_clustering and "
        "retained_cluster_ids. Nothing else would see the two drift apart"
    ),
    "test_a_face_that_stops_grouping_is_UNCLUSTERED_rather_than_left_stale": (
        "the pass writes None as well as numbers; without it a stale one-face cluster "
        "outlives the evidence for it on the labeling page"
    ),
    "test_two_model_generations_are_refused_with_both_named_and_a_route_out": (
        "A6's first refusal. MOVED here at M12 Task 1 from `tests/test_cluster.py`'s "
        "COMMAND_LEVEL_CONTROLS, the cross-module anchor under this file (M1796: this "
        "module deleted whole was green over 3167 tests)"
    ),
    "test_embeddings_of_two_widths_are_refused_even_under_one_generation": (
        "A6's second refusal. MOVED here at M12 Task 1 from `tests/test_cluster.py`'s "
        "COMMAND_LEVEL_CONTROLS (M1796)"
    ),
    "test_above_the_tested_ceiling_the_command_refuses_and_writes_nothing": (
        "A21's ceiling at the command. MOVED here at M12 Task 1 from "
        "`tests/test_cluster.py`'s COMMAND_LEVEL_CONTROLS (M1796)"
    ),
}

assert LOAD_BEARING_CONTROLS, "this module's control list is empty; it protects nothing"


def test_no_load_bearing_control_in_this_file_can_be_deleted_without_saying_so():
    """The floor under this module's own controls."""
    present = {name for name in globals() if name.startswith("test_")}
    missing = sorted(set(LOAD_BEARING_CONTROLS) - present)
    assert missing == [], (
        f"a load-bearing control has been deleted from {Path(__file__).name}:"
        f" {missing}. Each is why another assertion here means anything"
        f" ({[LOAD_BEARING_CONTROLS[m] for m in missing]})."
    )
