"""What the faces commands SAY (M12 Task 4: C7, C10, N9, P3, P4), read as whole lines.

THREE THINGS, all of them copy a human test reads, and every expectation is
spelled from design 12 rev 27 / 11 rev 27 and a run's numbers - never from the
production table (`RESULT_NOUNS`) or string it checks:

  * `pam enrich faces`' closing line counts FACES FOUND, not stored facts. a human test read
    "N results stored" where N was the face rows plus one per-file marker per file read
    (P3). Every other enricher keeps "N results stored".
  * the closing block names the model the run LOADED (N9), read off the concrete
    `FacesEnricher` the command built - design 8.1's Protocol gains nothing.
  * `pam faces cluster`'s refusal still suggests no value: it contains no digit at all (P4,
    M11 Task 9's ruling - a number inside a refusal is the guess the refusal prevents).
"""

import logging
from pathlib import Path

from pam.catalog import repo
from pam.catalog.db import connect, migrate
from pam.config import Config
from pam.enrich import InferredFact, faces_model
from pam.enrich import command as enrich_command
from pam.enrich import summary as enrich_summary
from pam.enrich.base import EnrichSummary
from pam.enrich.faces import FacesEnricher
from pam.enrich.summary import EnricherOutcome, EnrichSummaryReport
from pam.faces import epsilon_refusal

NOW = "2026-09-24T10:00:00"

# The geometry document `tests/test_faces_sink.py` stores, so the face below is a valid one.
GEOMETRY = {
    "v": 1,
    "bbox": [0.1, 0.2, 0.3, 0.4],
    "landmarks": [[0.1, 0.2], [0.2, 0.2], [0.15, 0.3], [0.12, 0.35], [0.18, 0.35]],
    "source_w": 4032,
    "source_h": 3024,
    "orientation": 1,
}


def summary(**overrides) -> EnrichSummary:
    fields = dict(
        run_id=1,
        total=300,
        walked=300,
        enriched=140,
        skipped_cached=0,
        skipped_wants=144,
        skipped_duplicate=2,
        skipped_no_sha=14,
        failed=0,
        facts_written=880,
        facts_dropped=0,
        facts_conflicted=0,
        superseded=0,
        cursor_file_id=300,
        facts_written_by_kind={"faces": 140, "face": 740},
    )
    fields.update(overrides)
    return EnrichSummary(**fields)


def paragraph(outcome: EnricherOutcome) -> list[str]:
    report = EnrichSummaryReport(archive_root=Path("x"), outcomes=[outcome])
    lines = enrich_summary.enrich_lines(report)
    return lines[2 : lines.index("", 2)]


def test_the_faces_line_counts_faces_found_and_the_model_line_follows_the_skips():
    """A human test's shape, re-rendered: 140 of 300 read, 740 faces - never 880."""
    assert paragraph(
        EnricherOutcome(name="faces", result=summary(), model="yunet+sface e293d8f46f48")
    ) == [
        "faces: 140 of 300 files read, 740 faces found.",
        "  skipped: 0 already done, 144 not applicable, 2 identical to another file,"
        " 14 never read from disk.",
        "  model: yunet+sface e293d8f46f48",
    ]


def test_one_face_is_singular_and_no_faces_is_plural():
    one = summary(enriched=1, facts_written=2, facts_written_by_kind={"faces": 1, "face": 1})
    none = summary(enriched=1, facts_written=1, facts_written_by_kind={"faces": 1})
    assert paragraph(EnricherOutcome(name="faces", result=one))[0] == (
        "faces: 1 of 300 files read, 1 face found."
    )
    assert paragraph(EnricherOutcome(name="faces", result=none))[0] == (
        "faces: 1 of 300 files read, 0 faces found."
    )


def test_every_other_enricher_keeps_results_stored_and_prints_no_model_line():
    """P3's other half: an enricher with no marker is unchanged, word for word."""
    caption = summary(
        total=9,
        walked=9,
        enriched=4,
        skipped_wants=2,
        skipped_duplicate=3,
        skipped_no_sha=0,
        facts_written=4,
        facts_written_by_kind={"caption": 4},
    )
    assert paragraph(EnricherOutcome(name="caption", result=caption)) == [
        "caption: 4 of 9 files read, 4 results stored.",
        "  skipped: 0 already done, 2 not applicable, 3 identical to another file,"
        " 0 never read from disk.",
    ]


class LoadsOnly(FacesEnricher):
    """The REAL `_loaded()` and `loaded_model`, over a stand-in `faces_model.load`: this
    enricher's `enrich` builds the sessions exactly as the real one does, then emits one
    marker and one valid face without running a model."""

    def available(self):
        return True, "a stand-in pack"

    def enrich(self, file, image):
        self._loaded()
        return [
            InferredFact(kind="faces", value={"count": 1}),
            InferredFact(
                kind="face",
                value={"geometry": GEOMETRY, "embedding": b"\x01\x02\x03\x04"},
                confidence=0.9,
            ),
        ]


def test_the_command_prints_the_model_its_faces_enricher_LOADED(tmp_path, monkeypatch):
    """N9's closing line, through `_run_one`: the command reads what the concrete enricher
    recorded when its sessions were built, and the faces line counts the ONE face - with
    markers counted as results it would read 2."""
    loaded = faces_model.Sessions(
        detector=object(), recognizer=object(), manifest=None, model_version="abc123def456"
    )
    monkeypatch.setattr(faces_model, "load", lambda model_dir, logger=None: loaded)
    monkeypatch.setitem(
        enrich_command.BUILDERS,
        "faces",
        lambda conn, root, config, log: (LoadsOnly(config.faces_model_dir, log), None),
    )
    conn = connect(tmp_path / "catalog.db")
    migrate(conn)
    try:
        file_id = repo.upsert_file(
            conn,
            rel_path="a.jpg",
            filename="a.jpg",
            ext="jpg",
            media_type="image",
            size=10,
            mtime_ns=1,
            placeholder=False,
            now=NOW,
        )
        repo.set_sha256(conn, file_id, "sha-a")
        conn.commit()
        outcome = enrich_command._run_one(
            conn,
            "faces",
            tmp_path,
            Config(faces_enabled=True),
            None,
            logging.getLogger("pam-test-copy"),
        )
    finally:
        conn.close()

    assert outcome.model == "yunet+sface abc123def456"
    lines = paragraph(outcome)
    assert lines[0] == "faces: 1 of 1 files read, 1 face found."
    assert lines[2] == "  model: yunet+sface abc123def456"


def test_a_faces_enricher_that_loaded_nothing_reports_no_model(tmp_path):
    """Nothing loaded, nothing claimed: a run over an all-cached archive built no session.

    OVER A PACK WHOSE IDENTITY RESOLVES (F3, M12 Task 4 review, M2039): with `""` for the
    folder, `identity` raised and a constructor that claimed the pack's identity up front
    never reached its claim, so the suite was green over it. The pack here is the
    structurally valid stand-in `test_faces_model.py` builds (honest manifest, a few bytes
    per file); construction reads its identity and must still claim nothing."""
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / faces_model.DETECTOR_FILE).write_bytes(b"not really a detector")
    (pack / faces_model.RECOGNIZER_FILE).write_bytes(b"not really a recognizer")
    faces_model.write_manifest(pack)
    enricher = FacesEnricher(str(pack))
    assert enricher.model_version == faces_model.identity(pack)[1]
    assert enricher.loaded_model is None


def test_the_epsilon_refusal_still_suggests_no_value():
    """P4 / D4: `cluster_epsilon` stays unset and the refusal's wording does not change -
    it names the key and holds NO DIGIT, so no number reaches the user as a suggestion."""
    sentence = epsilon_refusal(Config())
    assert sentence is not None
    assert "[enrich.faces] cluster_epsilon" in sentence
    assert not any(ch.isdigit() for ch in sentence), sentence
    # M13 Task 14 (H7): the refusal now names the command that measures a value - and still
    # suggests none itself. Spelled from the plan, verbatim.
    assert sentence.endswith(
        " then run this command again."
        " Run pam faces calibrate to see which value works for your photos."
    ), sentence
