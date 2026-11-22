"""`pam faces export` (M11 Task 13, Steps 1 and 2; amendment A20).

WHAT THIS MODULE IS ABOUT. Not "did a file appear" - a file appearing is the easy half.
It is about the document being READABLE BY SOMEBODY WHO DOES NOT HAVE PAM: the sidecar's
bytes equal the stored blobs in the declared order, the declared shape describes those
bytes, a Unicode name survives as itself, and a failed write leaves nothing at all behind
rather than a plausible-looking half.

EVERY CATALOG ROW IS PLANTED WITH THE SUITE'S OWN SQL (`test_faces_cluster.plant`) and
every expectation about the file is read back with the suite's own `struct` unpacker,
never with `cluster.decode_embedding` - the writer and the reader of these bytes must not
be one implementation, which is M8's M922/M924.
"""

import json
import os
import struct
from pathlib import Path

import pytest
from archives import FAKE_COMMAND
from click.testing import CliRunner
from test_faces_cluster import archive, blob, plant, plant_person

from pam.catalog.db import connect
from pam.cli import cli, main
from pam.errors import EXIT_EXPECTED, PamError
from pam.faces import export as export_mod
from pam.faces.export import run_export
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan

# Four float32s each, so a row is 16 bytes and an offset is a number somebody can check
# by eye. Distinct per face, so "the rows came back in the wrong order" cannot pass.
VECTORS = {
    1: (1.0, 0.0, 0.0, 0.0),
    2: (0.0, 1.0, 0.0, 0.0),
    3: (0.0, 0.0, 1.0, 0.0),
}


def read_export(root):
    """The written document and sidecar, found by GLOB rather than by a computed name.

    The stamp is a clock reading and a test that recomputed it would be asserting against
    its own arithmetic; the glob asserts there is EXACTLY ONE of each, which is the
    stronger statement and the one a leftover temp file fails.
    """
    ws = workspace_if_exists(root)
    assert ws is not None
    docs = sorted(ws.reports_dir.glob("faces-export-*.json"))
    bins = sorted(ws.reports_dir.glob("faces-export-*.embeddings.bin"))
    assert len(docs) == 1 and len(bins) == 1, (docs, bins)
    return json.loads(docs[0].read_text(encoding="utf-8")), bins[0].read_bytes()


def reports_listing(root):
    """Every file under the workspace's reports/ directory, by name."""
    ws = workspace_if_exists(root)
    assert ws is not None
    return sorted(p.name for p in ws.reports_dir.iterdir())


# ---------------------------------------------------------------------------
# 1. THE DOCUMENT - A20's fields, and whether they describe the bytes beside them.
# ---------------------------------------------------------------------------


def test_the_export_names_its_format_its_versions_and_its_lack_of_an_import(tmp_path):
    """A20's header, which is what makes the file readable in five years' time.

    `format` and `format_version` are what a reader dispatches on; `schema_version` and
    `app_version` are what a reader BLAMES when a field is missing. The `import` field is
    the sentence A20 requires in the command's output, put in the FILE as well - the
    output scrolls away and the file is what a user finds later, filed as their backup.
    """
    root = archive(tmp_path)
    plant(root, VECTORS[1])

    run_export(root)

    document, _ = read_export(root)
    assert document["format"] == "pam-faces-export"
    assert document["format_version"] == 1
    assert document["schema_version"] >= 7
    assert document["app_version"]
    assert "no import" in document["import"]
    assert "not a backup" in document["import"]


def test_every_face_field_A20_names_is_in_the_document(tmp_path):
    """The per-face row, spelled from A20's sentence rather than from `ExportFace`.

    A20: "per-face rel_path, sha256, geometry, confidence, cluster, person label,
    review_state". A list read off the production dataclass would agree with any value of
    it - this project's most repeated defect (M6's `ENRICHERS`, M8's `M922`).
    """
    root = archive(tmp_path)
    person = plant_person(root, "Zoe")
    plant(root, VECTORS[1], cluster_id=7, person_id=person, review_state="confirmed")

    run_export(root)

    document, _ = read_export(root)
    (face,) = document["faces"]
    for field in (
        "id",
        "sha256",
        "rel_paths",
        "geometry",
        "confidence",
        "cluster_id",
        "person_label",
        "review_state",
        "model_name",
        "model_version",
        "embedding_row",
    ):
        assert field in face, field
    assert face["cluster_id"] == 7
    assert face["person_label"] == "Zoe"
    assert face["review_state"] == "confirmed"
    assert face["geometry"]["bbox"] == [0.1, 0.2, 0.3, 0.4]


def test_the_sidecar_bytes_are_the_stored_blobs_in_the_declared_row_order(tmp_path):
    """THE TEST THIS COMMAND EXISTS FOR: the file is the data, not a description of it.

    Unpacked with the suite's own `struct`, in the order the document's `embedding_row`
    declares, and compared against the vectors planted. A sidecar written in `id` order
    while the document lists faces in another would pass every other test in this module
    and hand the user somebody else's face vector under their name.
    """
    root = archive(tmp_path)
    for index, vector in VECTORS.items():
        plant(root, vector, sha=f"sha-{index}")

    run_export(root)

    document, raw = read_export(root)
    width = document["embeddings"]["shape"][1]
    assert document["embeddings"]["shape"] == [3, 4] and width == 4
    assert len(raw) == 3 * width * 4
    for face in document["faces"]:
        row = face["embedding_row"]
        chunk = raw[row * width * 4 : (row + 1) * width * 4]
        expected = VECTORS[int(face["sha256"].split("-")[1])]
        assert struct.unpack(f"<{width}f", chunk) == expected


def test_the_declared_dtype_and_byte_order_are_what_the_catalog_actually_stores(tmp_path):
    """A20 wants dtype, shape and byte order DECLARED, and a wrong declaration is worse
    than none: a reader who trusts `float64` reads half as many faces, silently.

    Design 4's BLOB is raw float32 written by `numpy.astype(float32).tobytes()` in native
    order, and `cluster.FLOAT32_BYTES`' comment records that every platform pam supports
    is little-endian. The assertion below is that the file can be READ under the
    declaration, which is the only version of this claim that can fail.
    """
    root = archive(tmp_path)
    plant(root, VECTORS[1])

    run_export(root)

    document, raw = read_export(root)
    assert document["embeddings"]["dtype"] == "float32"
    assert document["embeddings"]["byte_order"] == "little"
    assert document["embeddings"]["file"].endswith(".embeddings.bin")
    assert struct.unpack("<4f", raw) == VECTORS[1]


def test_the_sidecar_is_named_by_FILENAME_and_sits_beside_the_document(tmp_path):
    """A relative name, not an absolute path: a user who moves the pair to a USB stick
    still has a document that points at the file next to it."""
    root = archive(tmp_path)
    plant(root, VECTORS[1])

    report = run_export(root)

    document, _ = read_export(root)
    assert document["embeddings"]["file"] == report.embeddings_path.name
    assert "/" not in document["embeddings"]["file"]
    assert "\\" not in document["embeddings"]["file"]
    assert report.json_path.parent == report.embeddings_path.parent


def test_the_models_block_counts_faces_per_generation(tmp_path):
    """A20's "model identity". A catalog holding two generations is a state A6 refuses to
    CLUSTER and this command must still export - it is what pam holds about the user, and
    a user in that state is exactly the one who needs their data out."""
    root = archive(tmp_path)
    plant(root, VECTORS[1], sha="sha-1", model=("yunet+sface", "gen-1"))
    plant(root, VECTORS[2], sha="sha-2", model=("yunet+sface", "gen-1"))
    plant(root, VECTORS[3], sha="sha-3", model=("other-stack", "gen-9"))

    run_export(root)

    document, _ = read_export(root)
    assert document["models"] == [
        {"model_name": "other-stack", "model_version": "gen-9", "faces": 1},
        {"model_name": "yunet+sface", "model_version": "gen-1", "faces": 2},
    ]


# ---------------------------------------------------------------------------
# 2. rel_paths - EVERY copy, resolved from the sha256 (A20 + spec 4).
# ---------------------------------------------------------------------------


def test_rel_paths_holds_every_file_row_with_that_content_sorted(tmp_path):
    """Spec 4 keys a face by CONTENT, so a face knows a sha256 and not a path.

    An archive holding one photograph twice is ordinary (a copy in an `Exported/` folder),
    and an export that picked ONE path would be answering a data-access request with pam's
    opinion of which copy mattered. `repo.files_by_sha256`'s ordering is a PICK for a
    command that must open a file; this is not that command.
    """
    root = tmp_path / "photos"
    root.mkdir()
    for name in ("IMG_1.JPG", "COPY.JPG"):
        (root / name).write_bytes(b"identical bytes")
        (root / f"{name}.tags.json").write_text(
            json.dumps({"DateTimeOriginal": "2019:07:04 18:23:11"}), encoding="utf-8"
        )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    ws = workspace_if_exists(root)
    assert ws is not None
    conn = connect(ws.db_path)
    try:
        # BY rel_path, not `LIMIT 1`: the `.tags.json` sidecars the fake exiftool reads are
        # ordinary `files` rows too, and the first one the table happens to return is one
        # of them - which is how this test first passed nothing to `plant`.
        sha = str(
            conn.execute(
                "SELECT sha256 FROM files WHERE rel_path = ?", ("IMG_1.JPG",)
            ).fetchone()[0]
        )
    finally:
        conn.close()
    plant(root, VECTORS[1], sha=sha)

    run_export(root)

    document, _ = read_export(root)
    (face,) = document["faces"]
    assert face["rel_paths"] == ["COPY.JPG", "IMG_1.JPG"]


def test_a_face_whose_content_is_in_no_files_row_exports_an_empty_rel_paths(tmp_path):
    """Not a crash and not a dropped row. A face survives its file being deleted from the
    archive (spec 4's whole point), and an export that refused such a row would withhold
    data from the user because pam could no longer say where it came from."""
    root = archive(tmp_path)
    plant(root, VECTORS[1], sha="sha-of-nothing-on-disk")

    run_export(root)

    document, _ = read_export(root)
    assert document["faces"][0]["rel_paths"] == []


# ---------------------------------------------------------------------------
# 3. UNICODE - the labels are people's names.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", ["Zoë", "Ægir", "田中さん", "Renée O'Brien"])
def test_a_unicode_label_round_trips_through_the_document_exactly(tmp_path, label):
    """`ensure_ascii=False`, and the assertion is on the BYTES as well as on the parse.

    `json.dumps` with `ensure_ascii=True` produces a document that parses back to the same
    string and reads, to a human opening it, as `Zo\\u00eb`. A20's export is meant to be
    read; a name nobody recognises in the file is a failure the parse cannot see.
    """
    root = archive(tmp_path)
    person = plant_person(root, label)
    plant(root, VECTORS[1], person_id=person)

    run_export(root)

    ws = workspace_if_exists(root)
    assert ws is not None
    (path,) = sorted(ws.reports_dir.glob("faces-export-*.json"))
    text = path.read_text(encoding="utf-8")
    assert label in text
    assert json.loads(text)["faces"][0]["person_label"] == label


# ---------------------------------------------------------------------------
# 4. THE WRITE - never overwrite, atomic, nothing left behind.
# ---------------------------------------------------------------------------


def test_an_existing_destination_refuses_and_writes_nothing(tmp_path, monkeypatch):
    """A20's temp-and-replace is about crashes; this is about the user's own data.

    An export is the copy of their data they asked pam to make. A second run one second
    later replacing it silently is the failure mode a stamped filename exists to avoid,
    and pam refuses rather than inventing a `-2` suffix - two files nobody can tell apart
    is a worse answer than a sentence naming the one already there.
    """
    root = archive(tmp_path)
    plant(root, VECTORS[1])
    monkeypatch.setattr(export_mod, "_stamp", lambda now=None: "20260917-101112")
    run_export(root)
    before = reports_listing(root)
    original = (workspace_if_exists(root).reports_dir / f"{before[0]}").read_bytes()

    with pytest.raises(PamError) as caught:
        run_export(root)

    assert "already exists" in str(caught.value)
    assert reports_listing(root) == before
    assert (workspace_if_exists(root).reports_dir / f"{before[0]}").read_bytes() == original


def test_a_failure_between_the_sidecar_and_the_document_leaves_NO_file_behind(
    tmp_path, monkeypatch
):
    """A20: "partial sidecars deleted on failure", and the sidecar has ALREADY MOVED.

    The JSON is replaced last, so the window this test opens is the real one: the sidecar
    is in place under its final name and the document never arrives. What is left is a
    block of float32 with nothing saying what is in it - an orphan, and A20's sentence is
    about exactly this. The failure is injected at `os.replace`'s SECOND call so that no
    part of the production write path is stubbed out.
    """
    root = archive(tmp_path)
    plant(root, VECTORS[1])
    calls = {"n": 0}
    real = os.replace

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("the disk went away between the sidecar and the document")
        return real(src, dst)

    monkeypatch.setattr(export_mod.os, "replace", flaky)

    with pytest.raises(OSError):
        run_export(root)

    assert calls["n"] == 2
    assert reports_listing(root) == []


def test_a_failure_writing_the_document_leaves_no_temp_file(tmp_path, monkeypatch):
    """The earlier window: the sidecar's temp file exists and the JSON never gets written.

    A `.tmp` left in `reports/` is a file a user finds, cannot read and does not know
    whether they may delete - and `pam verify` check 7 walks that directory.
    """
    root = archive(tmp_path)
    plant(root, VECTORS[1])

    def boom(*args, **kwargs):
        raise OSError("no space left on device")

    monkeypatch.setattr(export_mod.Path, "write_text", boom)

    with pytest.raises(OSError):
        run_export(root)

    assert reports_listing(root) == []


def test_the_document_is_written_LAST_so_a_present_json_means_a_complete_export(tmp_path):
    """The ordering, watched at `os.replace` rather than inferred from the outcome.

    M697's shape: asserting only that both files exist afterwards cannot tell "sidecar
    first" from "document first" - both end with two files. The sequence of destinations
    is the property, so the sequence is what is recorded.
    """
    root = archive(tmp_path)
    plant(root, VECTORS[1])
    moved: list[str] = []
    real = os.replace

    def watched(src, dst):
        moved.append(Path(dst).name)
        return real(src, dst)

    import unittest.mock as mock

    with mock.patch.object(export_mod.os, "replace", watched):
        run_export(root)

    assert len(moved) == 2
    assert moved[0].endswith(".embeddings.bin")
    assert moved[1].endswith(".json")


def test_the_destination_being_under_the_archive_root_RAISES_and_writes_nothing(tmp_path):
    """A20: "a path under the archive root is a bug; ASSERT IT" - as a raised error.

    NOT an `assert` statement, which `python -O` drops. What this guards is pam writing a
    file it generated into the user's irreplaceable photo folder, which is the one thing
    vision's premise says pam never does; a check that evaporates under an optimisation
    flag is not a check of that.
    """
    root = archive(tmp_path)
    inside = root / "reports" / "faces-export-1.json"

    with pytest.raises(PamError) as caught:
        export_mod._refuse_archive_path(inside, root)

    assert str(root) in str(caught.value)
    assert "never writes into your photo folder" in str(caught.value)
    assert not inside.exists()


def test_run_export_ITSELF_refuses_when_the_workspace_sits_inside_the_archive(tmp_path):
    """THE WIRING, not the function (reviewer's F3, measured as M1953).

    Deleting the `_refuse_archive_path(path, root)` call out of `_export` left **3699 tests
    green**: the test above drives the FUNCTION, and nothing drove the COMMAND. CLAUDE.md's
    standing lesson - an import is not a wiring - on the one rule vision's premise rests on,
    that pam never writes into the photo folder.

    The state is built rather than mocked: `PAM_HOME` is put INSIDE the archive before the
    scan, so the workspace pam resolves - and therefore its `reports/` - really is under the
    archive root, which is a thing a user can do to themselves with one environment variable.
    A raw `os.environ` write with try/finally, never `monkeypatch`, for the reason
    `conftest.py::isolated_pam_home` gives.
    """
    root = tmp_path / "photos"
    root.mkdir()
    (root / "IMG_1.JPG").write_bytes(b"one")
    (root / "IMG_1.JPG.tags.json").write_text(
        json.dumps({"DateTimeOriginal": "2019:07:04 18:23:11"}), encoding="utf-8"
    )
    previous = os.environ.get("PAM_HOME")
    os.environ["PAM_HOME"] = str(root / "pam-home")
    try:
        run_scan(root, exiftool_command=FAKE_COMMAND)
        ws = workspace_if_exists(root)
        assert ws is not None
        assert ws.reports_dir.is_relative_to(root), "the fixture must put it inside"
        before = sorted(q.name for q in ws.reports_dir.glob("*"))

        with pytest.raises(PamError) as caught:
            run_export(root)

        assert str(root) in str(caught.value)
        assert "never writes into your photo folder" in str(caught.value)
        assert sorted(q.name for q in ws.reports_dir.glob("*")) == before
        assert list(ws.reports_dir.glob("faces-export-*")) == []
    finally:
        if previous is None:
            os.environ.pop("PAM_HOME", None)
        else:
            os.environ["PAM_HOME"] = previous


def test_a_destination_outside_the_archive_root_is_allowed(tmp_path):
    """The other direction, so the check above cannot be "always refuse"."""
    root = archive(tmp_path)
    ws = workspace_if_exists(root)
    assert ws is not None
    export_mod._refuse_archive_path(ws.reports_dir / "faces-export-1.json", root)


def test_the_shipped_destination_is_the_workspace_and_never_the_archive(tmp_path):
    """The property the check above defends, measured end to end on a real run.

    A file count over the whole archive tree, before and after - `tools/ht_manifest.py`'s
    idea in-process. Neither command may add, change or remove one byte of a photograph.
    """
    root = archive(tmp_path)
    plant(root, VECTORS[1])
    before = {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}

    report = run_export(root)

    assert {p: p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()} == before
    assert not report.json_path.is_relative_to(root)
    assert not report.embeddings_path.is_relative_to(root)


def test_a_long_windows_path_under_reports_is_written_and_read_back(tmp_path):
    """A20's "long Windows paths", reached the way a real one is reached.

    THE ARCHIVE'S NAME IS WHAT LENGTHENS THE DESTINATION: `paths.archive_slug` builds the
    workspace directory out of it, so a photo folder with a long descriptive name puts a
    long directory under `PAM_HOME/archives/`, and `faces-export-YYYYMMDD-HHMMSS.
    embeddings.bin` adds 43 characters on top of `reports/`. That is the shape of the
    problem on this platform, and it is not something a test can reach by nesting tmp
    directories - `mkdir` itself gives up at MAX_PATH before pam is ever involved.

    Read back through the ordinary API and through `open`, because a path that only
    `pathlib` can see is a file the user's own tools cannot open.

    THE LENGTH IS BOUNDED BY MAX_PATH AND THAT IS NOT THIS COMMAND'S DEFECT. A longer
    archive name than this one fails at `paths.workspace_for`'s `mkdir` during `pam scan`,
    before export exists - Windows long paths are off on this machine and pam opts into
    none. So the floor here is 200 characters rather than 260: enough that `reports/` plus
    a 43-character export filename is a real part of the budget, and not an assertion
    about a platform limit `pam scan` meets first.
    """
    root = tmp_path / "family-photographs-scanned-tagged-and-labelled-2019-2024"
    root.mkdir()
    (root / "IMG_1.JPG").write_bytes(b"the only copy of IMG_1.JPG")
    (root / "IMG_1.JPG.tags.json").write_text(
        json.dumps({"DateTimeOriginal": "2019:07:04 18:23:11"}), encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plant(root, VECTORS[1])

    report = run_export(root)

    assert len(str(report.embeddings_path)) > 200, report.embeddings_path
    with open(report.json_path, encoding="utf-8") as handle:
        assert json.load(handle)["faces"]
    assert report.embeddings_path.read_bytes() == blob(VECTORS[1])


# ---------------------------------------------------------------------------
# 5. THE EDGES - an empty catalog, and embeddings of two widths.
# ---------------------------------------------------------------------------


def test_an_archive_with_no_faces_exports_an_EMPTY_document_rather_than_refusing(tmp_path):
    """The honest answer to "what do you hold about me" is sometimes "nothing".

    A refusal would make the empty case the one where a user cannot get an answer in
    writing, and "pam has no faces for this archive" is a fact worth being able to file.
    `shape` is `[0, 0]` and the sidecar is a real, empty file - a reader's `fromfile`
    returns an empty array rather than raising on a missing path.
    """
    root = archive(tmp_path)

    report = run_export(root)

    document, raw = read_export(root)
    assert report.faces == 0 and report.persons == 0 and report.photographs == 0
    assert document["faces"] == []
    assert document["models"] == []
    assert document["embeddings"]["shape"] == [0, 0]
    assert raw == b""
    assert report.embeddings_path.exists()


def test_embeddings_of_two_widths_declare_a_null_shape_and_per_face_offsets(tmp_path):
    """`width_refusal` refuses to CLUSTER this catalog; export must still produce it.

    A rectangle is a claim about the bytes, and these bytes are not one. So `shape` is
    `null`, each face carries its own offset and length, and the document says why -
    which is the version of this file a reader can actually parse.
    """
    root = archive(tmp_path)
    plant(root, VECTORS[1], sha="sha-1")
    plant(root, struct.pack("<8f", *range(8)), sha="sha-2")

    run_export(root)

    document, raw = read_export(root)
    assert document["embeddings"]["shape"] is None
    assert "more than one width" in document["embeddings"]["note"]
    first, second = document["faces"]
    assert (first["embedding_offset"], first["embedding_length"]) == (0, 16)
    assert (second["embedding_offset"], second["embedding_length"]) == (16, 32)
    assert len(raw) == 48
    assert struct.unpack("<8f", raw[16:]) == tuple(float(n) for n in range(8))


# ---------------------------------------------------------------------------
# 6. THE COMMAND - what a user sees, through click.
# ---------------------------------------------------------------------------


def test_the_summary_prints_both_paths_the_counts_and_the_no_import_sentence(tmp_path):
    """A20 requires the command to print path and counts and to SAY it has no import."""
    root = archive(tmp_path)
    person = plant_person(root, "Zoe")
    plant(root, VECTORS[1], sha="sha-1", person_id=person)
    plant(root, VECTORS[2], sha="sha-1")
    plant(root, VECTORS[3], sha="sha-2")

    result = CliRunner().invoke(cli, ["faces", "export", "--archive", str(root)])

    assert result.exit_code == 0, result.output
    document, _ = read_export(root)
    assert str(Path(document["embeddings"]["file"]).name) in result.output
    assert ".json" in result.output
    assert "3 faces over 2 photographs" in result.output
    assert "1 people you have named" in result.output
    assert "not a backup" in result.output
    assert "No photograph was read, written or renamed." in result.output


def test_the_command_refuses_an_archive_pam_has_never_scanned(tmp_path, capsys):
    """Spec 3: a workspace is never created as a side effect of a read-only command.

    Through `pam.cli.main`, not `CliRunner`, for `test_faces_cluster`'s reason: the
    exit-code mapping lives in `main` and `CliRunner().invoke` catches the `PamError`
    before it ever gets there.
    """
    unscanned = tmp_path / "elsewhere"
    unscanned.mkdir()

    assert main(["faces", "export", "--archive", str(unscanned)]) == EXIT_EXPECTED

    assert "pam scan" in capsys.readouterr().err
