"""A7's sink: the `face` kind's destination, and the marker that must not lie.

WHAT THIS MODULE IS ABOUT, AND WHY IT IS A MODULE OF ITS OWN. Through M10 every fact any
enricher produced went to one table, so "was it written" and "was it written to the right
place" were the same question. A7 splits them: `faces` emits a per-file MARKER that goes
to `inferred_facts` like everything else, and DETECTIONS that go to `face_instances`
through a sink. Two destinations means a routing table, and a routing table is exactly the
shape this project keeps getting wrong - `repo.ENRICHERS` in M6 (four of five enrichers
deletable from `pam status`, 1238 tests green), `repo.open_plan` in M5.

SO NOTHING HERE ASKS THE PRODUCTION CODE WHAT IT ROUTES. The routing table is compared
against a literal spelled from amendment A7 and spec 4 rev 22, the expected
`face_instances` rows are spelled out by hand - geometry, embedding bytes, confidence,
model name and version - and every read-back goes through a SECOND connection using the
suite's own SQL, so a row inserted and never committed cannot masquerade as a row
correctly dropped (M697).

AND THE ONE ASSERTION THAT HAD TO BE EXACT RATHER THAN CONTAINING. `inferred_facts` holds
EXACTLY the marker after a faces run - not "contains a marker". A containment check
survives the mutation that routes `face` to `inferred_facts` as well, which is M8's
partition lesson and is the single most likely way for this design to be undone.
"""

import ast
import inspect
import json
import sqlite3
import threading
from pathlib import Path
from unittest import mock

import pytest

from pam.catalog import repo
from pam.catalog.db import connect, migrate
from pam.enrich import InferredFact, run_enricher, sink

NOW = "2026-09-10T10:00:00"

# A newline, named, so the planted source below stays readable inside a test file.
NL = chr(10)

# A7 and spec 4 rev 22, TRANSCRIBED. `faces` is the enricher whose detections are sinked;
# `face` is the kind that is sinked. Spelled here so that the production table can be
# compared with something that is not itself.
A7_SINK_FACT_KINDS = {"faces": ("face",)}
A7_SINK_KINDS = ["face"]

# Spec 8.1's five enrichers with the kinds each may emit, marker included, written from
# spec 4 rev 22's `kind` comment and A7 - never from `_FACT_KINDS` or `SINK_FACT_KINDS`.
SPEC_ACCEPTED_KINDS = {
    "caption": ("caption",),
    "faces": ("faces", "face"),
    "ocr": ("ocr_text",),  # design 8.1 rev 27 (P7): one stored kind
    "phash": ("phash",),
    "geocode": ("place",),
}

GEOMETRY = {
    "v": 1,
    "bbox": [0.1, 0.2, 0.3, 0.4],
    "landmarks": [[0.1, 0.2], [0.2, 0.2], [0.15, 0.3], [0.12, 0.35], [0.18, 0.35]],
    "source_w": 4032,
    "source_h": 3024,
    "orientation": 1,
}


def embedding(seed: int) -> bytes:
    """Four bytes that differ per detection, so two rows cannot be confused."""
    return bytes([seed, seed + 1, seed + 2, seed + 3])


class FakeFaces:
    """A `faces` enricher that decodes nothing and emits exactly what a test tells it to.

    `plan` maps `rel_path` to a list of `(geometry, embedding, confidence)` triples, or to
    the string `"claims-99"`, which emits a marker whose count is a lie. The marker is
    always emitted, because A7 says the enricher emits one for every file it processed -
    including a file it found nobody in.
    """

    name = "faces"
    model_name = "yunet+sface"
    model_version = "test-generation-1"

    def __init__(self, plan, *, claimed=None):
        self.plan = plan
        self.claimed = claimed
        self.enrich_calls: list[str] = []
        self.threads: set[int] = set()
        self._lock = threading.Lock()

    def available(self):
        return True, "the fake pack is always available"

    def cost_estimate_ms(self):
        return 10

    def wants(self, file):
        return True

    def enrich(self, file, image):
        with self._lock:
            self.enrich_calls.append(file.rel_path)
            self.threads.add(threading.get_ident())
        detections = self.plan.get(file.rel_path, [])
        count = len(detections) if self.claimed is None else self.claimed
        facts = [InferredFact(kind="faces", value={"count": count})]
        for geometry, blob, confidence in detections:
            facts.append(
                InferredFact(
                    kind="face",
                    value={"geometry": geometry, "embedding": blob},
                    confidence=confidence,
                )
            )
        return facts


def no_loader(file):
    """Nothing in this module decodes a pixel; a loader that ran would be a bug."""
    raise AssertionError(f"nothing here may decode {file.rel_path}")


@pytest.fixture()
def archive(tmp_path):
    """A migrated catalog and its path, so every oracle can open a SECOND connection."""
    path = tmp_path / "catalog.db"
    conn = connect(path)
    migrate(conn)
    yield conn, path
    conn.close()


def add_files(conn, names):
    ids = {}
    for name in names:
        ids[name] = repo.upsert_file(
            conn,
            rel_path=name,
            filename=name,
            ext=name.rsplit(".", 1)[-1],
            media_type="image",
            size=10,
            mtime_ns=1,
            placeholder=False,
            now=NOW,
        )
        repo.set_sha256(conn, ids[name], f"sha-{name}")
    conn.commit()
    return ids


def _second(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def stored_facts(path):
    """Every `inferred_facts` row, through a second connection and the suite's own SQL."""
    conn = _second(path)
    try:
        return [
            (r["sha256"], r["kind"], r["value_json"], r["confidence"])
            for r in conn.execute(
                "SELECT sha256, kind, value_json, confidence FROM inferred_facts ORDER BY id"
            )
        ]
    finally:
        conn.close()


def stored_faces(path):
    """Every `face_instances` row, through a second connection and the suite's own SQL."""
    conn = _second(path)
    try:
        return [
            (
                r["sha256"],
                r["bbox_json"],
                bytes(r["embedding"]),
                r["det_confidence"],
                r["model_name"],
                r["model_version"],
                r["cluster_id"],
                r["person_id"],
                r["review_state"],
            )
            for r in conn.execute(
                "SELECT sha256, bbox_json, embedding, det_confidence, model_name,"
                " model_version, cluster_id, person_id, review_state"
                " FROM face_instances ORDER BY id"
            )
        ]
    finally:
        conn.close()


def run(conn, enricher):
    return run_enricher(conn, enricher, no_loader, workers=1)


# --- Step 3: the routing table -------------------------------------------------------


def test_the_sink_table_is_exactly_A7s():
    """The table, against a literal spelled from A7 - never against `_FACT_KINDS`.

    EXACT EQUALITY IN BOTH DIRECTIONS. A subset check would pass a table that had grown a
    second route nobody designed, and a superset check would pass an empty one.
    """
    assert sink.SINK_FACT_KINDS == A7_SINK_FACT_KINDS
    assert sorted(sink.SINKS) == A7_SINK_KINDS


def test_the_marker_is_NOT_sink_routed_and_the_detection_is_not_an_inferred_fact_kind():
    """The two negative halves, which are what make the split a split (I8).

    If `faces` were sinked, coverage and the cache would read a table `_fact_coverage`
    does not touch and every faceless photograph would be decoded for ever. If `face`
    were an `inferred_facts` kind, `pam status` would count detections as coverage and
    the embedding would have to go through `fact_value_json`.
    """
    assert "faces" not in sink.SINKS
    assert "face" not in repo.fact_kinds("faces")
    assert repo.fact_kinds("faces") == ("faces",)


def test_accepted_kinds_is_the_marker_plus_the_sink_kind_and_nobody_else_changed():
    """A7's `repo.fact_kinds(name) + SINK_FACT_KINDS.get(name, ())`, per enricher.

    Every one of spec 8.1's five names, so that a change to the addition shows up as a
    change for the four enrichers that have no sink as well as for the one that does.
    """
    assert {n: sink.accepted_kinds(n) for n in SPEC_ACCEPTED_KINDS} == SPEC_ACCEPTED_KINDS


def test_the_sink_map_is_keyed_by_KIND_and_the_accepted_map_by_ENRICHER_NAME():
    """A KIND, NEVER AN ENRICHER (A7, design 8.1).

    The two maps are spelled identically - `{"faces": ("face",)}` and `{"face": ...}` -
    only because spec 4 gives this enricher a kind of its own name. This pins that
    `SINKS` is looked up by the FACT's kind: `SINKS["face"]` exists and `SINKS["faces"]`
    does not, which is the opposite of `SINK_FACT_KINDS`.
    """
    assert set(sink.SINK_FACT_KINDS) == {"faces"}
    assert set(sink.SINKS) == {"face"}
    assert set(sink.SINK_FACT_KINDS) & set(sink.SINKS) == set()


# --- Step 3/4: what a run actually writes ---------------------------------------------


def test_three_detections_write_one_marker_and_three_rows_spelled_out_by_hand(archive):
    """The end-to-end shape, with the expectation written out rather than read back.

    `inferred_facts` holds EXACTLY the marker - not "contains" it. A containment check
    survives the mutation that routes `face` to `inferred_facts` too, and that mutation
    is the one this whole design exists to prevent (M8's partition lesson).
    """
    conn, path = archive
    add_files(conn, ["a.jpg"])
    faces = FakeFaces(
        {
            "a.jpg": [
                (GEOMETRY, embedding(1), 0.99),
                (GEOMETRY, embedding(10), 0.80),
                (GEOMETRY, embedding(20), 0.55),
            ]
        }
    )
    summary = run(conn, faces)

    assert stored_facts(path) == [
        ("sha-a.jpg", "faces", json.dumps({"count": 3}, sort_keys=True), None)
    ]
    expected_geometry = json.dumps(GEOMETRY, sort_keys=True)
    assert stored_faces(path) == [
        (
            "sha-a.jpg",
            expected_geometry,
            embedding(1),
            0.99,
            "yunet+sface",
            "test-generation-1",
            None,
            None,
            None,
        ),
        (
            "sha-a.jpg",
            expected_geometry,
            embedding(10),
            0.80,
            "yunet+sface",
            "test-generation-1",
            None,
            None,
            None,
        ),
        (
            "sha-a.jpg",
            expected_geometry,
            embedding(20),
            0.55,
            "yunet+sface",
            "test-generation-1",
            None,
            None,
            None,
        ),
    ]
    # Four facts written: the marker and the three detections. A sink row is still a
    # fact and `pam enrich`'s summary must not under-report the work it did.
    assert summary.facts_written == 4
    assert summary.facts_dropped == 0


def test_a_photograph_with_no_face_is_covered_and_is_skipped_on_the_second_run(archive):
    """I8, both halves, and the second half is the one that matters.

    A marker of `{"count": 0}` is what makes a landscape COVERED. Without it the cache
    cannot tell a photograph the detector looked at from one nobody has processed, and
    every faceless photograph in the archive is decoded again on every run, for ever.
    """
    conn, path = archive
    add_files(conn, ["landscape.jpg"])
    faces = FakeFaces({})
    run(conn, faces)

    assert stored_facts(path) == [
        ("sha-landscape.jpg", "faces", json.dumps({"count": 0}, sort_keys=True), None)
    ]
    assert stored_faces(path) == []
    assert faces.enrich_calls == ["landscape.jpg"]

    again = FakeFaces({})
    summary = run(conn, again)
    assert again.enrich_calls == []
    assert summary.skipped_cached == 1


def test_a_malformed_detection_is_dropped_and_the_marker_counts_what_was_accepted(archive):
    """A8: "the marker's count is computed from the rows actually accepted".

    Two good detections and one whose geometry is a string. The marker must say 2. A
    marker of 3 would tell `pam status` that three faces were stored when two were, and
    the third would be unrecoverable without re-enriching the file.
    """
    conn, path = archive
    add_files(conn, ["b.jpg"])
    faces = FakeFaces(
        {
            "b.jpg": [
                (GEOMETRY, embedding(1), 0.9),
                ("0,0,10,10", embedding(2), 0.9),
                (GEOMETRY, embedding(3), 0.8),
            ]
        }
    )
    summary = run(conn, faces)

    assert stored_facts(path) == [
        ("sha-b.jpg", "faces", json.dumps({"count": 2}, sort_keys=True), None)
    ]
    assert [row[2] for row in stored_faces(path)] == [embedding(1), embedding(3)]
    assert summary.facts_dropped == 1


def test_the_runner_counts_what_it_stored_PER_KIND_and_the_kinds_sum_to_the_total(archive):
    """P3 (M12 Task 4, Step 1): `facts_written`, split by the FACT'S KIND.

    Worked by hand. `a.jpg` has two good detections and one whose geometry is a string, so
    the sink accepts two `face` rows, drops one, and the marker is written: 1 `faces`.
    `b.jpg` has nobody in it: its marker only, 1 `faces`. So `{"faces": 2, "face": 2}`,
    `facts_written` 4 (= 2 + 2), and the dropped detection is in NEITHER - a dropped fact
    was not stored. Keyed by enricher instead, this would read `{"faces": 4}` and
    `pam enrich faces` would report the markers as faces found, which is HT-M11's D5.
    """
    conn, _path = archive
    add_files(conn, ["a.jpg", "b.jpg"])
    faces = FakeFaces(
        {
            "a.jpg": [
                (GEOMETRY, embedding(1), 0.9),
                ("0,0,10,10", embedding(2), 0.9),
                (GEOMETRY, embedding(3), 0.8),
            ],
        }
    )
    summary = run(conn, faces)

    assert dict(summary.facts_written_by_kind) == {"faces": 2, "face": 2}
    assert summary.facts_written == 4
    assert summary.facts_written == sum(summary.facts_written_by_kind.values())
    assert summary.facts_dropped == 1


def test_the_marker_count_is_the_runners_and_not_the_enrichers_claim(archive):
    """The sharper half of A8, and the one a well-behaved enricher cannot exercise.

    A real enricher emits a truthful count, so no live fixture separates "the runner
    computed it" from "the runner copied it". This one claims 99 with two valid
    detections. If the count were the enricher's, coverage would be a number the
    enricher could set at will - and a detector that miscounted would be believed.
    """
    conn, path = archive
    add_files(conn, ["c.jpg"])
    faces = FakeFaces(
        {"c.jpg": [(GEOMETRY, embedding(1), 0.9), (GEOMETRY, embedding(2), 0.7)]},
        claimed=99,
    )
    run(conn, faces)

    assert stored_facts(path) == [
        ("sha-c.jpg", "faces", json.dumps({"count": 2}, sort_keys=True), None)
    ]


def test_a_detection_with_no_confidence_is_dropped_rather_than_ending_the_run(archive):
    """`face_instances.det_confidence` is NOT NULL (spec 4), so NULL has nowhere to go.

    Dropped with a sentence, which is spec 6.4's answer for output that will not fit the
    shape it declares. The alternative is an `IntegrityError` out of the sink, which ends
    a run over twenty thousand photographs because of one bad detection.
    """
    conn, path = archive
    add_files(conn, ["d.jpg"])
    faces = FakeFaces({"d.jpg": [(GEOMETRY, embedding(1), None)]})
    summary = run(conn, faces)

    assert stored_faces(path) == []
    assert stored_facts(path) == [
        ("sha-d.jpg", "faces", json.dumps({"count": 0}, sort_keys=True), None)
    ]
    assert summary.facts_dropped == 1


def test_a_face_fact_from_an_enricher_that_may_not_emit_one_never_reaches_the_table(
    archive,
):
    """The accepted-kinds gate still applies to a sink kind (A7).

    `phash` has no sink, so `accepted_kinds("phash")` is `("phash",)` and a `face` fact
    from it is dropped at the boundary. Without this, the sink would be a route into
    `face_instances` that any enricher could take, and the model identity on those rows
    would be a phash model's.
    """
    conn, path = archive
    add_files(conn, ["e.jpg"])

    class Impostor(FakeFaces):
        name = "phash"

    summary = run(conn, Impostor({"e.jpg": [(GEOMETRY, embedding(1), 0.9)]}))
    assert stored_faces(path) == []
    # The marker went too: `faces` is not one of phash's kinds either.
    assert stored_facts(path) == []
    assert summary.facts_dropped == 2


# --- Step 4: atomicity ---------------------------------------------------------------


def test_nothing_from_a_half_written_photograph_is_visible_when_the_sink_fails(archive):
    """A8: a run interrupted between the marker and its faces leaves the file UNPROCESSED.

    The failure is injected at the DANGEROUS CALL - the sink itself, on the second
    detection - rather than simulated by leaving rows out, and the oracle is a SECOND
    connection, so "never committed" is distinguished from "committed and then tidied".
    Nothing is visible: not the first face, and not the marker.
    """
    conn, path = archive
    add_files(conn, ["f.jpg"])
    calls = []

    def exploding(connection, enricher, file, fact):
        calls.append(fact)
        if len(calls) == 2:
            raise RuntimeError("the disk went away mid-photograph")
        sink._write_face_instance(connection, enricher, file, fact)

    faces = FakeFaces({"f.jpg": [(GEOMETRY, embedding(1), 0.9), (GEOMETRY, embedding(2), 0.9)]})
    with mock.patch.dict(sink.SINKS, {"face": exploding}):
        with pytest.raises(RuntimeError):
            run(conn, faces)

    assert len(calls) == 2
    assert stored_faces(path) == []
    assert stored_facts(path) == []


# THE MATCHER IS A HELPER SO THAT THE NEGATIVE CONTROL CAN DRIVE THE REAL ONE. M11 Task 7's
# F3 was this exact defect, twice: a guard walked the AST inline and its control planted a
# tree and then RE-IMPLEMENTED the walk, so a matcher that stopped matching was green in both
# places. Task 8 stage 1 measured it a third time here - M1772 changed `"commit"` to
# `"kommit"` and all 3129 tests passed. One function, two callers: the guard and the control.


def _commit_calls(tree: ast.AST) -> list[ast.Call]:
    """Every `<something>.commit(...)` CALL in a parsed module. The guard's own matcher."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "commit"
    ]


def test_the_write_step_never_commits(archive):
    """The M5 defect, which cost that milestone a whole task: a stray `conn.commit()`.

    Per-file atomicity holds because `run_enricher` commits once per 50-file batch, so a
    photograph's marker and its faces are inside one transaction by construction. A
    commit inside the write step would split exactly what A8 joins, and it would do it
    with every test above still passing - the rows all arrive, just not together. So this
    reads the CODE: the behaviour is unobservable from the outside, which is why the
    structure is what is pinned.

    AN AST WALK, NOT A SUBSTRING SEARCH. The docstrings in that module discuss committing
    at length, and a `"commit()" not in source` would be answering a question about prose
    (`tests/test_ci_lanes.py`'s lesson: parse the language, do not pattern-match it).
    What is asserted is that no `.commit(...)` CALL appears anywhere in the module.
    """
    committers = _commit_calls(ast.parse(inspect.getsource(sink)))
    assert committers == [], (
        "`enrich/sink.py` calls .commit(): that splits the transaction A8 requires a"
        " photograph's marker and its faces to share"
    )


def test_the_commit_walk_would_actually_see_a_commit():
    """THE NEGATIVE CONTROL, and without it the guard above proves nothing (M1772).

    It drives `_commit_calls` - the function the guard calls - over a planted tree that
    DOES commit, rather than re-implementing the match. A matcher narrowed to a name
    nothing is spelled with returns `[]` for every input, and `[] == []` is green for
    ever; this is the assertion that goes red when it stops looking.
    """
    planted = ast.parse(
        "def f(conn):" + NL + "    conn.commit()" + NL + "    other.commit()" + NL
    )
    assert len(_commit_calls(planted)) == 2, _commit_calls(planted)
    assert _commit_calls(ast.parse("def f(conn):" + NL + "    conn.execute('x')" + NL)) == []


# --- the embedding's route ------------------------------------------------------------


def test_the_embedding_never_passes_through_fact_value_json(archive):
    """Watch the dangerous CALL, not the outcome (M697).

    `fact_value_json` is `json.dumps`, which cannot serialize bytes - so a `face` fact
    routed to `inferred_facts` would raise rather than store. That means the OUTCOME
    (no `face` row in `inferred_facts`) is also produced by a run that tried and failed,
    and by a future serializer that learned to encode bytes and stored the embedding as
    a base64 string nobody reads. This asserts what the serializer was ASKED.
    """
    conn, _path = archive
    add_files(conn, ["g.jpg"])
    seen = []
    real = sink.fact_value_json

    def spy(value):
        seen.append(dict(value))
        return real(value)

    with mock.patch.object(sink, "fact_value_json", spy):
        run(conn, FakeFaces({"g.jpg": [(GEOMETRY, embedding(1), 0.9)]}))

    assert seen == [{"count": 1}]


# --- the anti-deletion floor ----------------------------------------------------------
#
# HAND-SPELLED, NEVER DERIVED FROM `globals()`. An inventory that asks the module what it
# contains passes the very mutation it exists to catch (CLAUDE.md's `repo.ENRICHERS`
# lesson): delete the test and the name goes with it, and the two agree about less.

LOAD_BEARING_CONTROLS = {
    "test_the_sink_table_is_exactly_A7s": (
        "the routing table against a literal spelled from A7. Without it the table can "
        "be emptied or widened and every behavioural test below still passes for the "
        "wrong reason"
    ),
    "test_three_detections_write_one_marker_and_three_rows_spelled_out_by_hand": (
        "the EXACT `inferred_facts` assertion. A containment check survives routing "
        "`face` to `inferred_facts`, which is the mutation this design exists to stop"
    ),
    "test_a_photograph_with_no_face_is_covered_and_is_skipped_on_the_second_run": (
        "I8. Delete it and dropping the zero marker is green, and every faceless "
        "photograph is decoded again on every run for ever"
    ),
    "test_the_runner_counts_what_it_stored_PER_KIND_and_the_kinds_sum_to_the_total": (
        "P3 (M12 Task 4): the per-kind split the faces closing line reads; keyed by "
        "enricher it counts markers as faces found (HT-M11 D5)"
    ),
    "test_the_marker_count_is_the_runners_and_not_the_enrichers_claim": (
        "A8's sharper half. No well-behaved enricher can exercise it, so if this row "
        "goes the marker becomes a number the enricher sets at will"
    ),
    "test_nothing_from_a_half_written_photograph_is_visible_when_the_sink_fails": (
        "A8's atomicity, injected at the sink and read through a SECOND connection"
    ),
    "test_the_write_step_never_commits": (
        "M5's stray-commit defect, made structural: a commit here splits the transaction "
        "A8 requires a photograph's marker and its faces to share, and every behavioural "
        "test still passes because the rows all arrive - just not together"
    ),
    "test_the_commit_walk_would_actually_see_a_commit": (
        "the negative control on that walk (M1772): a matcher that matches nothing "
        "returns [] for every input and the guard above is green for ever"
    ),
    "test_the_embedding_never_passes_through_fact_value_json": (
        "M697's shape: the outcome cannot tell 'never asked' from 'asked and raised'"
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
