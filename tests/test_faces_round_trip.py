"""M11 Task 13 Step 5 - THE ROUND TRIP, which is the only test of deletions 3 and 4.

    enrich -> cluster -> label -> export -> purge -> `pam enrich faces` AGAIN finds
    the SAME faces.

Nothing shorter reaches the two deletions whose absence is silent. A19's five deletions
divide into three that announce themselves - `face_instances`, `persons` and the export -
and two that do not:

  * DELETION 3, the `faces` MARKER FACTS. Leave them and spec 8.1's cache skip reports
    every file already done, so the next `pam enrich faces` walks the archive, detects
    nothing, writes nothing and SAYS IT SUCCEEDED.
  * DELETION 4, the `faces` `enrichment_runs` ROWS. Leave them and `open_run` hands the
    next run the purged run's cursor, so it starts PAST every file that run walked and
    reports a clean pass over the remainder.

Both symptoms are "the faces do not come back", and both are invisible to any test that
stops at the purge and counts rows.

WHY THE FIRST RUN IS INTERRUPTED, which is the part that is easy to get wrong. A run that
REACHED THE END wrote its `finished_at`, and `open_run` refuses to resume a finished run -
so over a completed first run, "the second run started from no cursor" is true whether
deletion 4 happened or not, and the assertion is vacuous. The cursor only means something
when an UNFINISHED run holds one. So leg 1 is a `pam enrich faces` that a user stopped:
spec 8.1 commits every `COMMIT_BATCH` files walked, the interruption lands on the file
after that commit, and what is left on disk is a run row with `finished_at IS NULL` and a
cursor pointing at the last file of the committed batch. `_CURSOR_IS_REAL` is the floor
that keeps it that way - a corpus that stopped reaching the commit would leave this whole
module passing for the wrong reason.

WHAT IS ASSERTED, AND WITH WHAT. Every catalog read here is the SUITE'S OWN SQL. An
emptiness proved with `repo.export_faces(conn) == []` proves that the reader and the
writer agree about emptiness and nothing else - M5's `open_plan`, M6's `ENRICHERS`, M8's
M922, three times on this project.

AND THE ASSERTION WATCHES THE DANGEROUS CALL, NOT ONLY THE OUTCOME (CLAUDE.md, M697).
The damage a skipped deletion 3 or 4 does is that pam DOES NOT READ THE FILES - so the
instrument is a record of the reads themselves, taken inside the `with image(file)` where
production hands over a decoded raster of a real JPEG on disk. `_Reads` is that record.
The row counts are the outcome and they are asserted too; neither alone is the test,
because a run that read nothing and a run that read everything and stored nothing produce
the same empty table.

THE DETECTOR IS FAKE AND NOTHING ELSE IS. This machine has neither the `[faces]` extra nor
a model pack, and a test marked to skip without them would be a twelfth skip that proves
nothing on the machine that matters. So `command.BUILDERS["faces"]` is swapped for a
builder returning a fake detector and THE REAL `pil_image_loader` - the same seam
`tests/test_faces_enricher.py` uses - and the marker facts, the `face_instances` rows, the
`enrichment_runs` rows, the clustering, the labeling, the export and all five deletions are
written by production code, through the real commands.
"""

import json
import sqlite3
import struct
from pathlib import Path

import pytest
from archives import FAKE_COMMAND
from click.testing import CliRunner
from PIL import Image
from test_webui_faces import Served

from pam.catalog.db import connect
from pam.cli import cli
from pam.enrich.base import InferredFact, pil_image_loader
from pam.enrich.command import BUILDERS
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan

# Spec 8.1 commits "every 50 files walked". WRITTEN OUT, not imported from
# `pam.enrich.COMMIT_BATCH`: a corpus sized from the constant it is exercising fits any
# value of that constant, which is exactly the derived-oracle defect this project has
# measured three times. `_CURSOR_IS_REAL` below is what notices if the two ever disagree.
COMMIT_BATCH_PER_SPEC = 50

# One more file than a batch, so the walk crosses a commit exactly once and the last file
# is the one the interruption lands on.
CORPUS = COMMIT_BATCH_PER_SPEC + 1

# The file with two faces. Every other file has one, so "the same faces come back" is a
# claim about a multiset that is not uniform - a per-file count that was uniform could be
# reproduced by a detector that had forgotten which file it was looking at.
TWO_FACE_INDEX = 3

MODEL = ("yunet+sface", "round-trip-1")
CONFIDENCE = 0.9
EMBEDDING = struct.pack("<4f", 1.0, 0.0, 0.0, 0.0)


# --- the fake detector, and the real read ----------------------------------------------


class _Reads:
    """Every file production actually decoded, in order, with the size it decoded to.

    The size is carried because it is evidence the decode HAPPENED: a loader that handed
    back a lazy handle nobody opened would still let a rel_path be appended.
    """

    def __init__(self) -> None:
        self.entries: list[tuple[str, tuple[int, int]]] = []

    @property
    def paths(self) -> list[str]:
        return [name for name, _size in self.entries]


class FakeFaces:
    """`faces`' name and identity, deterministic detections, no pack and no onnxruntime.

    `wants` is spelled out rather than borrowed from `FacesEnricher` so that this stub
    cannot drift into testing whatever that method happens to say; the two refusals are
    design 8.1's, by hand.
    """

    name = "faces"
    model_name, model_version = MODEL

    def __init__(self, reads: _Reads, stop_at: str | None = None) -> None:
        self.reads = reads
        self.stop_at = stop_at

    def available(self) -> tuple[bool, str]:
        return True, "a fake detector (tests/test_faces_round_trip.py)"

    def cost_estimate_ms(self) -> int:
        return 1

    def wants(self, file) -> bool:
        return not file.placeholder and file.media_type == "image"

    def enrich(self, file, image) -> list[InferredFact]:
        if file.rel_path == self.stop_at:
            # BEFORE the loader, so the interrupted file is one production never read -
            # which is what leg 6 asserts about the file past the cursor.
            raise KeyboardInterrupt("the user stopped pam enrich faces")
        with image(file) as decoded:
            self.reads.entries.append((file.rel_path, decoded.size))
        boxes = _boxes(file.rel_path)
        facts: list[InferredFact] = [InferredFact(kind="faces", value={"count": len(boxes)})]
        for box in boxes:
            facts.append(
                InferredFact(
                    kind="face",
                    value={
                        "geometry": {
                            "v": 1,
                            "bbox": box,
                            "source_w": 32,
                            "source_h": 32,
                            "orientation": 1,
                        },
                        "embedding": EMBEDDING,
                    },
                    confidence=CONFIDENCE,
                )
            )
        return facts


def _index(rel_path: str) -> int:
    return int(Path(rel_path).stem[1:])


def _boxes(rel_path: str) -> list[list[float]]:
    """This file's detections, from its name alone. The same file always yields the same
    geometry, which is what makes "the same faces came back" checkable by value."""
    index = _index(rel_path)
    first = [round(0.001 * index, 4), 0.2, 0.3, 0.4]
    if index != TWO_FACE_INDEX:
        return [first]
    return [first, [0.5, 0.5, 0.6, 0.7]]


def _builder(reads: _Reads, stop_at: str | None):
    """A `command.BUILDERS` entry: the fake detector, and THE REAL image loader.

    The loader is not faked. What deletions 3 and 4 break is pam opening the user's files
    at all, so the read has to be a real decode of a real file off disk or the instrument
    is measuring itself.
    """

    def build(conn, root, config, log):
        return FakeFaces(reads, stop_at), pil_image_loader(root)

    return build


# --- the archive -----------------------------------------------------------------------


def _archive(tmp_path: Path) -> Path:
    """`CORPUS` real, distinct, decodable JPEGs, scanned for real.

    DISTINCT BYTES PER FILE, because spec 4 keys a fact by sha256 and the runner refuses a
    duplicate at dispatch: identical images would be walked and skipped, not enriched, and
    the corpus would quietly shrink to one file.
    """
    root = tmp_path / "photos"
    root.mkdir()
    for index in range(CORPUS):
        image = Image.new("RGB", (32, 32), (index * 5 % 256, 40, 200))
        image.save(root / f"f{index:03d}.JPG", "JPEG", quality=95)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    ws = workspace_if_exists(root)
    assert ws is not None
    ws.config_path.write_text(
        "\n".join(
            [
                "[enrich.faces]",
                "enabled = true",
                'model_dir = ""',
                "cluster_epsilon = 0.2",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return root


# --- the suite's own SQL ----------------------------------------------------------------


def _raw(root: Path) -> sqlite3.Connection:
    ws = workspace_if_exists(root)
    assert ws is not None
    conn = connect(ws.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _query(root: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = _raw(root)
    try:
        return list(conn.execute(sql, params))
    finally:
        conn.close()


def _one(root: Path, sql: str, params: tuple = ()) -> int:
    return int(_query(root, sql, params)[0][0])


def _faces(root: Path) -> list[tuple]:
    """Every face, by VALUE, sorted - the comparable the round trip turns on."""
    return sorted(
        (str(r["sha256"]), str(r["bbox_json"]), float(r["det_confidence"]))
        for r in _query(root, "SELECT sha256, bbox_json, det_confidence FROM face_instances")
    )


def _file_ids(root: Path) -> list[tuple[int, str]]:
    return [
        (int(r["id"]), str(r["rel_path"]))
        for r in _query(root, "SELECT id, rel_path FROM files ORDER BY id")
    ]


def _runs(root: Path) -> list[sqlite3.Row]:
    return _query(
        root,
        "SELECT id, cursor_file_id, finished_at FROM enrichment_runs"
        " WHERE enricher = 'faces' ORDER BY id",
    )


def _markers(root: Path) -> int:
    return _one(root, "SELECT COUNT(*) FROM inferred_facts WHERE kind = 'faces'")


# --- the round trip ---------------------------------------------------------------------


def test_the_round_trip_enrich_cluster_label_export_purge_and_enrich_again(
    tmp_path, monkeypatch
):
    """A19's deletions 3 and 4, measured by the only thing that can measure them.

    Six legs, each driven through the real command. The assertions that carry the step are
    leg 6's: the same faces, by value, come back; production READ the files to produce
    them; and it read the file BEFORE the purged run's cursor, which it could only reach
    by starting from no cursor at all.
    """
    root = _archive(tmp_path)
    ids = _file_ids(root)
    assert len(ids) == CORPUS, "the corpus is the scanned files, and nothing else is here"
    cursor_file_id, cursor_name = ids[COMMIT_BATCH_PER_SPEC - 1]
    stop_at = ids[-1][1]

    # --- leg 1: `pam enrich faces`, stopped by the user one file after a commit --------
    first_reads = _Reads()
    with monkeypatch.context() as patch:
        patch.setitem(BUILDERS, "faces", _builder(first_reads, stop_at))
        interrupted = CliRunner().invoke(cli, ["enrich", "faces", "--archive", str(root)])

    assert interrupted.exit_code != 0, interrupted.output
    assert first_reads.paths == [name for _id, name in ids[:-1]], (
        "the stopped run read every file up to the one it was stopped on, and that one"
        " not at all"
    )
    assert all(size == (32, 32) for _name, size in first_reads.entries)

    enriched = [name for _id, name in ids[:COMMIT_BATCH_PER_SPEC]]
    before = _faces(root)
    assert len(before) == COMMIT_BATCH_PER_SPEC + 1, (
        "one face per committed file, and two for the file that has two - the batch after"
        " the commit was lost with the process, which is what spec 8.1 says happens"
    )
    assert _markers(root) == COMMIT_BATCH_PER_SPEC

    runs = _runs(root)
    assert len(runs) == 1
    assert runs[0]["finished_at"] is None, "a stopped run is resumable and says so"
    # _CURSOR_IS_REAL. Deletion 4's symptom needs an unfinished run holding a cursor that
    # points PAST the beginning and SHORT of the end. If this ever fails, leg 6's cursor
    # assertion has stopped discriminating and this module is passing for the wrong reason.
    assert runs[0]["cursor_file_id"] == cursor_file_id
    assert cursor_file_id > ids[0][0] and cursor_file_id < ids[-1][0]

    # --- leg 2: `pam faces cluster` ---------------------------------------------------
    clustered = CliRunner().invoke(cli, ["faces", "cluster", "--archive", str(root)])
    assert clustered.exit_code == 0, clustered.output
    cluster_ids = {
        r["cluster_id"] for r in _query(root, "SELECT cluster_id FROM face_instances")
    }
    assert None not in cluster_ids and len(cluster_ids) == 1, (
        "every embedding here is the same vector, so one cluster is the whole archive and"
        " one label names all of it"
    )
    cluster_id = int(next(iter(cluster_ids)))

    # --- leg 3: label, through the route the browser takes ----------------------------
    # The real `ReviewServer` and a real HTTP POST to spec 8.2's `cluster/{id}/label`,
    # rather than `repo.upsert_person`: the repo writer is reachable from a test and from
    # nothing a user does, and `faces_api.label` is where the person row, the per-face
    # `person_id` and the count check actually live.
    live = Served(root)
    try:
        status, body = live.json(
            "POST", f"/faces/cluster/{cluster_id}/label", {"person": "Emma"}
        )
    finally:
        live.close()
    assert status == 200, body
    assert body["faces"] == len(before)
    persons = _query(root, "SELECT id, label FROM persons")
    assert [str(r["label"]) for r in persons] == ["Emma"]
    assert _one(root, "SELECT COUNT(*) FROM face_instances WHERE person_id IS NOT NULL") == len(
        before
    )

    # --- leg 4: `pam faces export` ----------------------------------------------------
    exported = CliRunner().invoke(cli, ["faces", "export", "--archive", str(root)])
    assert exported.exit_code == 0, exported.output
    ws = workspace_if_exists(root)
    assert ws is not None
    documents = sorted(ws.reports_dir.glob("faces-export-*.json"))
    assert len(documents) == 1, documents
    document = json.loads(documents[0].read_text(encoding="utf-8"))
    assert len(document["faces"]) == len(before)
    assert {row["person_label"] for row in document["faces"]} == {"Emma"}
    assert (
        sorted(
            (row["sha256"], json.dumps(row["geometry"], sort_keys=True), row["confidence"])
            for row in document["faces"]
        )
        == before
    ), "the export holds the faces that are in the catalog, by value"
    assert {name for row in document["faces"] for name in row["rel_paths"]} == set(enriched)

    # --- leg 5: `pam faces purge` -----------------------------------------------------
    monkeypatch.setattr("builtins.input", lambda: "purge")
    purged = CliRunner().invoke(cli, ["faces", "purge", "--archive", str(root)])
    assert purged.exit_code == 0, purged.output

    assert _one(root, "SELECT COUNT(*) FROM face_instances") == 0
    assert _one(root, "SELECT COUNT(*) FROM persons") == 0
    assert _markers(root) == 0, "deletion 3"
    assert _runs(root) == [], "deletion 4"
    # Deletion 5 has nothing to do and A19 says so: pam caches no face crop, and a purge
    # that deleted a directory of them would be deleting somebody else's files.
    assert list(ws.thumbs_dir.glob("**/*.face.*")) == []
    # The export is the copy the user was told to take BEFORE purging, and a purge that
    # removed it would take the only thing standing between them and the deletion.
    assert documents[0].exists()

    # --- leg 6: `pam enrich faces` again, and the SAME faces come back -----------------
    second_reads = _Reads()
    with monkeypatch.context() as patch:
        patch.setitem(BUILDERS, "faces", _builder(second_reads, stop_at=None))
        again = CliRunner().invoke(cli, ["enrich", "faces", "--archive", str(root)])
    assert again.exit_code == 0, again.output

    # THE READS, which is the dangerous call. A skipped deletion 3 leaves the marker facts
    # and every one of these files is cache-skipped instead of opened; a skipped deletion 4
    # leaves the cursor and only the last file is walked at all. Either way this list is
    # the wrong length and the failure names which.
    assert second_reads.paths == [name for _id, name in ids], (
        "the second run read every file in the archive: it neither cache-skipped the"
        " purged ones nor resumed past them"
    )
    assert second_reads.paths[0] == ids[0][1], (
        "it started at the FIRST file, which is before the purged run's cursor"
        f" ({cursor_name}) - a run resuming that cursor could never have opened it"
    )

    after = _faces(root)
    assert [row for row in after if row[0] in {r[0] for r in before}] == before, (
        "same faces: same sha256s, same geometry, same confidence"
    )
    assert len(after) == CORPUS + 1, (
        "and the file the stopped run never reached now has its face too - the second run"
        " walked the whole archive, not the remainder of the first one"
    )
    assert _markers(root) == CORPUS

    runs_now = _runs(root)
    assert len(runs_now) == 1, "one fresh run row, not the purged one resumed"
    assert runs_now[0]["finished_at"] is not None, "and this one reached the end"
    # NOT `id != runs[0]["id"]`: SQLite hands a deleted rowid back to the next INSERT, so
    # the fresh row is `id = 1` again and an identity comparison would be asserting a
    # property of rowid allocation. The cursor is the honest witness - it sits at the LAST
    # file of the archive, which is where a walk of the whole thing ends.
    assert runs_now[0]["cursor_file_id"] == ids[-1][0]


@pytest.mark.parametrize(
    "index,expected",
    [(0, 1), (TWO_FACE_INDEX, 2), (CORPUS - 1, 1)],
)
def test_the_fake_detector_is_not_uniform_across_the_corpus(index, expected):
    """A floor on the instrument. If `_boxes` ever returned one face for every file, the
    round trip's "same faces came back" would be a claim about a constant, and a detector
    that ignored its argument entirely would satisfy it."""
    assert len(_boxes(f"f{index:03d}.JPG")) == expected
