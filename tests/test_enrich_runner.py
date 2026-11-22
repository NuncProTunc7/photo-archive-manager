"""Spec 8.1's enricher runner, driven by a fake enricher whose calls are counted.

THE SHAPE OF THE RISK, WRITTEN DOWN SO THE TESTS BELOW CAN BE READ AGAINST IT. A runner
is where an oracle built from the production symbol passes for any behaviour: "the run
enriched the files the run decided to enrich" is true of every implementation, correct
or not. So nothing here asks the runner what it did. Every expectation is a list of
`rel_path` strings SPELLED OUT BY HAND, compared against what the fake enricher
RECORDED being asked - `wants_calls` and `enrich_calls` - and the two are different
lists on purpose, because the cache skip and the `wants()` skip are different mechanisms
and a single "was it processed" counter cannot tell them apart.

AND WHERE A CALL CAN BE WATCHED, IT IS WATCHED RATHER THAN INFERRED FROM THE OUTCOME.
That is CLAUDE.md's M697 lesson: a test that asserts the catalog is unchanged cannot
tell "never wrote" from "wrote and rolled back". The single-writer tests watch the
worker threads and the connection itself; the validation tests read the table back
through a SECOND connection, so a fact that was inserted and never committed cannot
masquerade as a fact that was correctly dropped.

THE CURSOR AND THE CACHE ARE TESTED SEPARATELY, and that is the point of
`SilentEnricher`. An interrupted run whose enricher wrote facts is resumed correctly by
EITHER mechanism, so a single test proves neither. `SilentEnricher` returns no facts at
all, so nothing is ever cached and the cursor is the only thing that can stop a resume
re-walking from the beginning; the cache tests run to completion, where the run is
finished and the cursor is not consulted at all.
"""

import ast
import functools
import json
import logging
import math
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import pytest

from pam.catalog import repo
from pam.catalog.db import connect, migrate
from pam.enrich import COMMIT_BATCH, InferredFact, run_enricher, sink, version_key
from pam.enrich import base as enrich_base
from pam.enrich.base import _enrich_one
from pam.enrich.eta import ALPHA, PRIOR_FILES, Eta
from pam.enrich.validate import FACT_SCHEMAS, fact_error

NOW = "2026-08-28T10:00:00"

# Spec 8.1's Enricher block, TRANSCRIBED FROM THE SPEC - member names AND signatures,
# spelled out here rather than read off the Protocol. The whole point of the guard below
# is to notice the normative interface changing, and an expectation derived from the
# interface agrees with any value of it (M6's `ENRICHERS`, M5's `open_plan`).
#
# THE SIGNATURES ARE HERE BECAUSE THE NAMES ALONE WERE NOT ENOUGH (stage-1 review,
# M1216/M1217). With only the member set pinned, `available(self) -> tuple[bool, str]`
# could be changed to `-> bool` with the whole suite green AND `mypy pam` clean - it has
# no production caller in M9 - and the pair that carries the *reason* string, which is
# the half `pam doctor` and `pam enrich` will render, was guarded by nothing.
SPEC_8_1_SIGNATURES = {
    "name": "str",
    "model_name": "str",
    "model_version": "str",
    "available": "(self) -> tuple[bool, str]",
    "cost_estimate_ms": "(self) -> int",
    "wants": "(self, file: FileRecord) -> bool",
    "enrich": "(self, file: FileRecord, image: PILImageLoader) -> list[InferredFact]",
}
SPEC_8_1_MEMBERS = frozenset(SPEC_8_1_SIGNATURES)

# Spec 6.4 + spec 8.1's enricher notes, read off the two sections and not off the table
# they check. `geocode` is ABSENT deliberately: the M9 plan's clarification 1 records
# that the geocode enricher's kind is `place`, not `geocode`, and Task 9 corrects
# `_FACT_KINDS`. A schema row for `geocode` would let that defect store facts instead of
# dropping them.
# M11 (A7) adds two, and they are read off spec 4 rev 22 and amendment A7, not off the
# table: `faces` is the PER-FILE MARKER (`{"count": N}`, including N = 0) and `face` is
# the DETECTION, routed to `face_instances` by a sink. Both are kinds, both are
# validated, and only one of them ever reaches `inferred_facts`.
# M12 Task 13 (design 8.1 rev 27, P7): `ocr_date` is NOT a stored kind - "`ocr_date` is not a
# stored kind (rev 27)" - so it leaves this set, and `ocr_text` is the one OCR kind.
SPEC_FACT_KINDS = frozenset({"caption", "ocr_text", "phash", "place", "faces", "face"})

# --- the list-driven checks, hoisted so their LENGTH can be pinned ---------------------
#
# M743's lesson, reproduced against this module by the stage-1 review: pytest SKIPS an
# empty parametrize rather than failing it (M1214 - five cases vanished, suite green,
# one extra skip nobody asserts about), and a NARROWED one leaves no trace at all
# (M1215 - `math.nan`, `True` and `"0.5"` silently stopped being tested and there was
# not even a skip). A non-empty assert catches only the first; the length is therefore
# pinned EXACTLY, and `test_every_list_driven_check_in_this_module_still_has_its_cases`
# adds a floor written from the spec side rather than a bare count.

# The cache key's three dimensions (spec 8.1: `(name, model_name, model_version)`, and
# the kind belongs to the name).
CACHE_KEY_FIELDS = [
    ("kind", "caption"),
    ("model_name", "some-other-model"),
    ("version", "0.9"),
]

# Spec 6.4: "confidence is NULL or in [0,1]". `True` is here because a bool passes
# `0 <= x <= 1` and would be stored as a certainty of 1.0; `"0.5"` because a string is
# what a misparsed model response actually looks like.
BAD_CONFIDENCES = [-0.000001, 1.000001, math.nan, True, "0.5"]
GOOD_CONFIDENCES = [None, 0.0, 1.0, 0.5]

# Spec 6.4's other clauses: the declared schema, `place` non-empty after sanitization,
# P7's `ocr_text` lines (each EXACTLY `text` str and `score` a number, not a bool, in
# [0, 1]), and a kind with no schema at all. `ocr_date` left with rev 27: it is not a kind.
MALFORMED_VALUES = [
    ("phash", {}),
    ("phash", {"hash": "x", "extra": "y"}),
    ("phash", {"hash": 7}),
    ("phash", {"hash": True}),
    ("caption", {"label": "a"}),
    ("ocr_text", {"lines": [{"text": "'98 7 4", "score": 1.5}]}),
    ("ocr_text", {"lines": [{"text": "'98 7 4", "score": True}]}),
    ("ocr_text", {"lines": [{"score": 0.93}]}),
    ("ocr_text", {"lines": [{"text": "'98 7 4", "score": 0.93, "box": [0, 0, 1, 1]}]}),
    ("ocr_text", {"lines": [{"text": 1998, "score": 0.93}]}),
    ("ocr_text", {"lines": ["'98 7 4"]}),
    ("ocr_text", {"lines": [{"text": "'98 7 4", "score": math.nan}]}),
    ("ocr_text", {"lines": "'98 7 4"}),
    # M12 Task 13 review (M2290, M2291): an entry that is not an object at all and is not
    # iterable, and a score that is a number-shaped STRING. Each was otherwise accepted or
    # crashed validation instead of being reported.
    ("ocr_text", {"lines": [7]}),
    ("ocr_text", {"lines": [{"text": "'98 7 4", "score": "0.5"}]}),
    ("place", {"place": "..."}),
    ("place", {"place": "   "}),
    ("nosuchkind", {"hash": "x"}),
    # M11 (A7). The marker's `count` is an int and nothing else: a string would be
    # stored and then compared against numbers, and `True` is an `int` in Python, so a
    # type confusion would record "one face" for a boolean nobody meant as a count.
    ("faces", {"count": "3"}),
    ("faces", {"count": True}),
    # A13's geometry is a DOCUMENT, and a `face` carrying a string where the box should
    # be would reach `bbox_json` as `"..."` and make every crop of it unrenderable.
    ("face", {"geometry": "0,0,10,10", "embedding": bytes(4)}),
    # AND THE ONE THAT IS ABOUT THE DESTINATION, not about the value:
    # `face_instances.det_confidence` is NOT NULL (spec 4), so a detection with no
    # confidence has nowhere to go. This case is parametrized with `confidence=None`,
    # which every other row here also is - the difference is that for `face` it is the
    # thing being tested.
    ("face", {"geometry": {"v": 1}, "embedding": bytes(4)}),
]
# `(kind, value, confidence)`. The third element exists because A7's `face` kind is the
# first whose well-formedness depends on the confidence being present: spelling every row
# with a hard-coded `None` would have made the passing case for `face` unwritable.
WELL_FORMED_VALUES = [
    ("phash", {"hash": "ff00"}, None),
    ("caption", {"label": "beach", "sentence": "A beach at sunset."}, None),
    ("ocr_text", {"lines": [{"text": "'98 7 4", "score": 0.93}]}, None),
    # I8: a photograph OCR looked at and found no text in is COVERED - an empty list.
    ("ocr_text", {"lines": []}, None),
    ("ocr_text", {"lines": [{"text": "ROLL 12", "score": 0}, {"text": "x", "score": 1}]}, None),
    ("place", {"place": "Paris"}, None),
    # I8, WRITTEN OUT AS ITS OWN ROW: a photograph the detector looked at and found
    # nobody in is COVERED. If `{"count": 0}` were not well-formed, every faceless
    # photograph in the archive would be decoded again on every run, for ever.
    ("faces", {"count": 0}, None),
    ("faces", {"count": 3}, None),
    (
        "face",
        {"geometry": {"v": 1, "bbox": [0.1, 0.1, 0.2, 0.2]}, "embedding": bytes(4)},
        0.91,
    ),
]


# --- the fakes -----------------------------------------------------------------------


class FakeEnricher:
    """A spec 8.1 enricher that records every call made to it and decodes nothing.

    `enrich_calls` and `wants_calls` are the oracles for every behavioural test in this
    module. They are appended to under a lock because the runner calls `enrich` from a
    `ThreadPoolExecutor` and `wants` from the writer thread, and a test whose evidence
    is itself racy would report the race as a runner bug.
    """

    def __init__(
        self,
        *,
        name="phash",
        model_name="fake-model",
        model_version="1.0",
        kind="phash",
        confidence=None,
        cost_ms=100,
        facts_per_file=1,
        wants_pred=None,
        boom=None,
    ):
        self.name = name
        self.model_name = model_name
        self.model_version = model_version
        self.kind = kind
        self.confidence = confidence
        self.cost_ms = cost_ms
        self.facts_per_file = facts_per_file
        self._wants_pred = wants_pred
        self._boom = boom
        self._lock = threading.Lock()
        self.wants_calls: list[str] = []
        self.enrich_calls: list[str] = []
        self.threads: set[int] = set()
        self.loaders: list[object] = []

    def available(self):
        return True, "the fake is always available"

    def cost_estimate_ms(self):
        return self.cost_ms

    def wants(self, file):
        self.wants_calls.append(file.rel_path)
        return True if self._wants_pred is None else self._wants_pred(file)

    def enrich(self, file, image):
        with self._lock:
            self.enrich_calls.append(file.rel_path)
            self.threads.add(threading.get_ident())
            self.loaders.append(image)
            seen = len(self.enrich_calls)
        if self._boom is not None:
            self._boom(file, seen)
        return [
            InferredFact(
                kind=self.kind,
                value={"hash": f"{file.rel_path}-{n}"},
                confidence=self.confidence,
            )
            for n in range(self.facts_per_file)
        ]


class SilentEnricher(FakeEnricher):
    """Records its calls and writes NOTHING, so only the cursor can prevent a re-walk."""

    def __init__(self, **kw):
        super().__init__(facts_per_file=0, **kw)


def refusing_loader(file):
    """The `PILImageLoader` for a task that decodes no pixels.

    It raises rather than returning a stub, so a runner that decided to open the image
    itself - Task 8's job, not this one's - fails loudly here instead of silently
    hydrating a cloud placeholder.
    """
    raise AssertionError(f"nothing in Task 7 may decode {file.rel_path}")


class Captured(logging.Handler):
    """A logger that keeps its records, so "logged and dropped" can be read as text."""

    def __init__(self):
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


@pytest.fixture()
def log():
    handler = Captured()
    logger = logging.getLogger(f"pam-test-enrich-{id(handler)}")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.captured = handler.messages
    return logger


# --- catalog helpers -----------------------------------------------------------------


@pytest.fixture()
def db(tmp_path):
    conn = connect(tmp_path / "catalog.db")
    migrate(conn)
    yield conn
    conn.close()


def add_files(conn, names, *, shas=None, media="image", status="present"):
    """Files in the catalog, in the order given, each with a distinct sha256 by default.

    Returns name -> id. Ids come out of `upsert_file` in insertion order, so a caller
    that wants to reason about id ORDER writes the names in that order.
    """
    ids = {}
    for name in names:
        ids[name] = repo.upsert_file(
            conn,
            rel_path=name,
            filename=name.rsplit("/", 1)[-1],
            ext=name.rsplit(".", 1)[-1],
            media_type=media,
            size=10,
            mtime_ns=1,
            placeholder=False,
            now=NOW,
        )
        sha = f"sha-{name}" if shas is None else shas.get(name)
        if sha is not None:
            repo.set_sha256(conn, ids[name], sha)
    if status != "present":
        repo.mark_missing(conn, list(ids.values()))
    conn.commit()
    return ids


def stored_facts(db_path):
    """Every `inferred_facts` row, read back with the SUITE's own SQL.

    Deliberately not `repo.live_facts`: an oracle that calls the query it is checking
    agrees with any value of it, which is the M5 `open_plan` ruling. A SECOND connection
    so that a row inserted and never committed cannot be mistaken for a row correctly
    dropped.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id, sha256, kind, value_json, confidence, model_name,"
                " model_version, superseded_by FROM inferred_facts ORDER BY id"
            )
        ]
    finally:
        conn.close()


def stored_runs(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id, enricher, model_name, model_version, finished_at,"
                " cursor_file_id FROM enrichment_runs ORDER BY id"
            )
        ]
    finally:
        conn.close()


def run(conn, enricher, *, workers=2, **kw):
    return run_enricher(conn, enricher, refusing_loader, workers=workers, **kw)


# --- Step 1: the Protocol ------------------------------------------------------------


def protocol_signatures():
    """The `Enricher` Protocol's members AND their signatures, by AST.

    An AST walk for `tests/guards.py`'s reason: it reads what is written in the file,
    survives a `typing` implementation detail changing under it, and cannot be satisfied
    by the very object it is checking. `ast.unparse` renders each member the way spec
    8.1 writes it, so the comparison below is against a transcription of the spec.
    """
    source = (Path(__file__).parents[1] / "pam" / "enrich" / "base.py").read_text(
        encoding="utf-8"
    )
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef) and node.name == "Enricher":
            found = {
                n.target.id: ast.unparse(n.annotation)
                for n in node.body
                if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
            }
            found.update(
                {
                    n.name: f"({ast.unparse(n.args)}) -> {ast.unparse(n.returns)}"
                    for n in node.body
                    if isinstance(n, ast.FunctionDef) and n.returns is not None
                }
            )
            return found
    raise AssertionError("pam/enrich/base.py has no Enricher class")


def protocol_members():
    return frozenset(protocol_signatures())


def test_the_protocol_is_exactly_spec_8_1s_seven_members():
    assert protocol_members() == SPEC_8_1_MEMBERS


def test_every_protocol_member_is_spelled_the_way_spec_8_1_spells_it():
    """The member NAMES were pinned and the SIGNATURES were not (stage-1 review).

    A return type nothing calls is a return type nothing checks: `available()` has no
    production caller in M9, so `-> tuple[bool, str]` could become `-> bool` with the
    suite green and mypy clean. Spec 8.1's block is normative in full, not just in its
    seven names.
    """
    assert protocol_signatures() == SPEC_8_1_SIGNATURES


def test_the_fake_enricher_implements_every_protocol_member():
    """If the fake could drift from the Protocol, every test below would test a fiction."""
    fake = FakeEnricher()
    missing = sorted(m for m in SPEC_8_1_MEMBERS if not hasattr(fake, m))
    assert missing == []


def test_the_runner_hands_the_image_loader_to_enrich():
    """Spec 8.1's `enrich(file, image)`. The loader is the runner's to supply."""
    conn = connect(":memory:")
    migrate(conn)
    add_files(conn, ["a.jpg"])
    fake = FakeEnricher()
    run(conn, fake)
    assert fake.loaders == [refusing_loader]
    conn.close()


# --- Step 2: resume, and what the cursor means ---------------------------------------


def test_a_run_walks_every_present_file_in_id_order(db):
    fake = FakeEnricher()
    add_files(db, ["c.jpg", "a.jpg", "b.jpg"])
    summary = run(db, fake)
    # Insertion order, NOT alphabetical: the cursor is an id, so the walk is by id.
    assert fake.enrich_calls == ["c.jpg", "a.jpg", "b.jpg"]
    assert (summary.walked, summary.enriched) == (3, 3)


def test_a_missing_file_is_never_walked(db):
    fake = FakeEnricher()
    add_files(db, ["here.jpg"])
    add_files(db, ["gone.jpg"], status="missing")
    run(db, fake)
    assert fake.enrich_calls == ["here.jpg"]


def test_the_cursor_is_the_last_file_walked_and_the_run_is_closed(db, tmp_path):
    ids = add_files(db, ["a.jpg", "b.jpg", "c.jpg"])
    summary = run(db, FakeEnricher())
    assert summary.cursor_file_id == ids["c.jpg"]
    (row,) = stored_runs(tmp_path / "catalog.db")
    assert row["cursor_file_id"] == ids["c.jpg"]
    assert row["finished_at"] is not None


def test_the_cursor_advances_over_files_that_were_only_SKIPPED(db, tmp_path):
    """Walked, not enriched. A cursor that only moved over enriched files would make
    every resume re-walk every cached and unwanted file for the life of the archive."""
    ids = add_files(db, ["a.jpg", "b.jpg"])
    fake = FakeEnricher(wants_pred=lambda f: False)
    summary = run(db, fake)
    assert fake.enrich_calls == []
    assert summary.skipped_wants == 2
    assert summary.cursor_file_id == ids["b.jpg"]


def test_commits_are_batched_every_fifty_walked_files(db):
    """Spec 8.1: "single-writer DB commits batched every 50 files"."""
    assert COMMIT_BATCH == 50
    names = [f"f{i:03d}.jpg" for i in range(120)]
    add_files(db, names)
    ticks = []
    run(db, FakeEnricher(), progress=ticks.append)
    assert [t.walked for t in ticks] == [50, 100, 120]


def test_an_exact_multiple_of_the_batch_does_not_commit_an_empty_tail(db):
    add_files(db, [f"f{i:03d}.jpg" for i in range(100)])
    ticks = []
    run(db, FakeEnricher(), progress=ticks.append)
    assert [t.walked for t in ticks] == [50, 100]


def interrupt_after(n):
    """A `boom` that stops the run once the enricher has seen `n` files.

    `KeyboardInterrupt` rather than a plain exception, and that is the whole point:
    `_enrich_one` catches `Exception` so one bad file cannot end a run, so a test that
    raised `ValueError` would measure the per-file error path and call it an
    interruption. Only a `BaseException` reaches the writer and stops the walk.
    """

    def boom(file, seen):
        if seen >= n:
            raise KeyboardInterrupt(f"stopped at {file.rel_path}")

    return boom


def test_an_interrupted_run_resumes_from_the_cursor_and_repeats_no_committed_work(
    tmp_path,
):
    """P3, and the cursor ALONE: `SilentEnricher` writes no facts, so nothing is cached
    and the cursor is the only mechanism that can stop a resume walking from file 1."""
    db_path = tmp_path / "catalog.db"
    names = [f"f{i:03d}.jpg" for i in range(120)]
    first = connect(db_path)
    migrate(first)
    ids = add_files(first, names)
    with pytest.raises(KeyboardInterrupt):
        run(first, SilentEnricher(boom=interrupt_after(60)), workers=1)
    first.close()  # the process died: the uncommitted batch rolls back

    (row,) = stored_runs(db_path)
    assert row["finished_at"] is None, "an interrupted run must stay resumable"
    assert row["cursor_file_id"] == ids["f049.jpg"], (
        "the last COMMITTED batch, not the last walked"
    )

    second = connect(db_path)
    resumed = SilentEnricher()
    run(second, resumed, workers=1)
    assert resumed.enrich_calls == names[50:], "exactly the files the first run did not commit"
    assert resumed.wants_calls == names[50:], "and it did not even ask about the first fifty"
    assert stored_runs(db_path)[0]["finished_at"] is not None
    second.close()


def test_an_interrupted_run_leaves_the_committed_batches_on_disk(tmp_path):
    """The other half of the same event, and it watches the WRITE rather than the walk:
    the facts for the first fifty files are readable from a second connection after the
    first one died mid-batch."""
    db_path = tmp_path / "catalog.db"
    names = [f"f{i:03d}.jpg" for i in range(120)]
    first = connect(db_path)
    migrate(first)
    add_files(first, names)
    with pytest.raises(KeyboardInterrupt):
        run(first, FakeEnricher(boom=interrupt_after(60)), workers=1)
    first.close()
    assert [f["sha256"] for f in stored_facts(db_path)] == [f"sha-{n}" for n in names[:50]]


def test_a_finished_run_is_never_resumed(db, tmp_path):
    """`finished_at IS NULL` is what makes a run resumable. Without it the second
    invocation would start at the first run's cursor and walk nothing forever."""
    add_files(db, ["a.jpg", "b.jpg"])
    run(db, FakeEnricher())
    second = FakeEnricher(model_version="2.0")
    run(db, second)
    assert second.enrich_calls == ["a.jpg", "b.jpg"]
    assert [r["finished_at"] is not None for r in stored_runs(tmp_path / "catalog.db")] == [
        True,
        True,
    ]


def test_a_file_added_after_the_cursor_is_picked_up_by_the_resume(tmp_path):
    db_path = tmp_path / "catalog.db"
    first = connect(db_path)
    migrate(first)
    names = [f"f{i:03d}.jpg" for i in range(60)]
    add_files(first, names)
    with pytest.raises(KeyboardInterrupt):
        run(first, SilentEnricher(boom=interrupt_after(55)), workers=1)
    add_files(first, ["late.jpg"])
    first.close()
    second = connect(db_path)
    resumed = SilentEnricher()
    run(second, resumed, workers=1)
    assert resumed.enrich_calls == [*names[50:], "late.jpg"]
    second.close()


# --- Step 3: the cache skip ----------------------------------------------------------


def test_a_completed_run_caches_its_files_and_the_second_run_asks_about_none_of_them(db):
    """The cache ALONE: the first run finished, so its cursor is never consulted."""
    add_files(db, ["a.jpg", "b.jpg"])
    run(db, FakeEnricher())
    again = FakeEnricher()
    summary = run(db, again)
    assert again.enrich_calls == []
    assert again.wants_calls == [], "a cached file is not offered to the enricher at all"
    assert summary.skipped_cached == 2


def seed_fact(conn, sha, *, kind="phash", model_name="fake-model", version="1.0"):
    fid = repo.insert_fact(
        conn,
        sha256=sha,
        kind=kind,
        value_json='{"hash": "seeded"}',
        confidence=None,
        model_name=model_name,
        model_version=version,
        generated_at=NOW,
    )
    conn.commit()
    return fid


def test_a_SUPERSEDED_fact_does_not_skip_its_file(db):
    """`superseded_by IS NULL`, load-bearing reading one: a superseded fact that still
    skipped its file would make the model-upgrade path unable to ever re-run."""
    add_files(db, ["a.jpg"])
    old = seed_fact(db, "sha-a.jpg")
    new = seed_fact(db, "sha-a.jpg", version="9.9")
    repo.supersede_facts(db, [old], new)
    db.commit()
    fake = FakeEnricher()
    run(db, fake)
    assert fake.enrich_calls == ["a.jpg"]


def test_a_LIVE_fact_at_this_identity_skips_its_file(db):
    add_files(db, ["a.jpg"])
    seed_fact(db, "sha-a.jpg")
    fake = FakeEnricher()
    summary = run(db, fake)
    assert fake.enrich_calls == []
    assert summary.skipped_cached == 1


@pytest.mark.parametrize(("field", "value"), CACHE_KEY_FIELDS)
def test_a_fact_from_a_different_identity_does_not_skip_its_file(db, field, value):
    """The cache key is `(name, model_name, model_version)` and the kind belongs to the
    name. A fact differing in any one of the three is not this enricher's fact."""
    add_files(db, ["a.jpg"])
    seed_fact(db, "sha-a.jpg", **{field: value})
    fake = FakeEnricher()
    run(db, fake)
    assert fake.enrich_calls == ["a.jpg"]


def test_two_files_with_identical_bytes_are_enriched_once(db, tmp_path):
    """Spec 4: `inferred_facts` is CONTENT-addressed, so one fact covers both files.

    The dedupe is at DISPATCH, not after the write, and `workers=2` is what makes that
    observable: both files land in the same batch and are handed to the pool together,
    so a check made after the write would have DECODED the second one already. It is
    not a write race - `write_facts` runs on the calling thread - which is why the
    assertion below is on `enrich_calls`, the work, rather than on the catalog.
    """
    add_files(db, ["one.jpg", "two.jpg"], shas={"one.jpg": "same", "two.jpg": "same"})
    fake = FakeEnricher()
    summary = run(db, fake, workers=2)
    assert fake.enrich_calls == ["one.jpg"], "the lower id, once"
    assert summary.skipped_duplicate == 1
    assert [f["sha256"] for f in stored_facts(tmp_path / "catalog.db")] == ["same"]


def test_a_file_with_no_sha256_is_skipped_and_says_why(db, log):
    add_files(db, ["nohash.jpg"], shas={})
    fake = FakeEnricher()
    summary = run(db, fake, logger=log)
    assert fake.enrich_calls == []
    assert summary.skipped_no_sha == 1
    assert any("nohash.jpg" in m and "content-addressed" in m for m in log.captured)


def test_wants_is_not_asked_about_a_cached_file(db):
    """Check order, and it is observable: a cached file never reaches the enricher."""
    add_files(db, ["cached.jpg", "fresh.jpg"])
    seed_fact(db, "sha-cached.jpg")
    fake = FakeEnricher()
    run(db, fake)
    assert fake.wants_calls == ["fresh.jpg"]


def test_a_file_wants_rejects_is_never_enriched(db):
    add_files(db, ["yes.jpg", "no.jpg"])
    fake = FakeEnricher(wants_pred=lambda f: f.rel_path == "yes.jpg")
    summary = run(db, fake)
    assert fake.wants_calls == ["yes.jpg", "no.jpg"]
    assert fake.enrich_calls == ["yes.jpg"]
    assert summary.skipped_wants == 1


# --- Step 4: concurrency and the single writer ---------------------------------------


def test_the_worker_function_takes_no_connection_and_closes_over_nothing():
    """The single-writer rule, made structural and then read back off the code object.

    `co_freevars` empty means `_enrich_one` is not a closure, so there is no enclosing
    frame it could reach a connection through; the parameter list is spelled out here so
    that adding one named `conn` is a red test rather than a review's diligence.
    """
    assert _enrich_one.__code__.co_freevars == ()
    names = _enrich_one.__code__.co_varnames[: _enrich_one.__code__.co_argcount]
    assert names == ("enricher", "loader", "clock", "file")


def test_the_callable_actually_handed_to_the_pool_carries_no_frame(db, monkeypatch):
    """The other half of the structural rule, and it was missing (stage-1 review, M1195).

    The test above inspects `_enrich_one`. It says nothing about what is SUBMITTED, and
    that is the object a worker runs: replacing `functools.partial(_enrich_one, ...)`
    with a nested `def` closing over the runner's frame - where `conn` lives - left all
    2393 tests green. So this watches the submission itself, the way the `check_same_
    thread` test watches the dangerous call rather than the outcome.
    """
    submitted = []

    class Recording(ThreadPoolExecutor):
        def map(self, fn, *iterables, **kwargs):
            submitted.append(fn)
            return super().map(fn, *iterables, **kwargs)

    monkeypatch.setattr(enrich_base, "ThreadPoolExecutor", Recording)
    add_files(db, ["a.jpg"])
    fake = FakeEnricher()
    run(db, fake)

    assert submitted, "the runner must hand its work to the pool"
    for work in submitted:
        assert isinstance(work, functools.partial), (
            "a lambda or nested def would close over the runner's frame, and `conn` is"
            f" in that frame; got {type(work).__name__}"
        )
        assert work.func is _enrich_one
        assert work.args == (fake, refusing_loader, time.monotonic)
        assert not work.keywords
        assert work.func.__closure__ is None


def test_the_workers_never_run_on_the_writers_thread(db):
    add_files(db, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])
    fake = FakeEnricher()
    run(db, fake, workers=2)
    assert threading.get_ident() not in fake.threads
    assert fake.threads, "the fake must have recorded the threads it ran on"


def test_the_fact_writer_runs_only_on_the_calling_thread(db):
    """The half of the single-writer rule that the M11 Task 8 Step 1 move CHANGED SHAPE.

    `sink.write_facts` used to be a closure inside `run_enricher`, so "runs ONLY on the
    calling thread" was structural in the same way `_enrich_one` closing over nothing is:
    a worker had no name to call and no `conn` to hand it. Module-level it has both, and
    the property now rests on the CALL SITE - `flush`, which runs on this thread. The
    two tests above did not move with it: they inspect `_enrich_one` and the partial
    submitted to the pool, and both still assert exactly what they asserted before, which
    is why neither could notice the writer being dispatched instead of called.

    So this watches the thread the writer ACTUALLY RUNS ON, not the rows it leaves
    behind - M697's rule. `workers=2` and four files, and the workers' own threads are
    asserted to be different, so "it ran on the calling thread" is not passing merely
    because nothing ran concurrently at all.
    """
    add_files(db, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])
    real = enrich_base.write_facts
    writer_threads = []

    def watched(*args, **kwargs):
        writer_threads.append(threading.get_ident())
        return real(*args, **kwargs)

    with mock.patch.object(enrich_base, "write_facts", watched):
        fake = FakeEnricher()
        summary = run(db, fake, workers=2)

    assert summary.facts_written == 4
    assert len(writer_threads) == 4, "the writer must be called once per enriched file"
    assert set(writer_threads) == {threading.get_ident()}
    assert fake.threads and threading.get_ident() not in fake.threads, (
        "the enrichers must have run somewhere else, or the assertion above is vacuous"
    )


def test_the_runner_calls_the_sink_module_and_holds_no_copy_of_the_writer(db):
    """`base.py` must reach `sink.write_facts` through the module, one writer only.

    The test above monkeypatches `enrich_base.write_facts` and would pass vacuously
    against a runner that had kept its own private copy of the write step - the patch
    would simply never be reached, `writer_threads` would be empty, and only the length
    assertion stands between that and a green run. This pins the identity directly, and
    it is also the thing Step 3's sink table needs to stay true of: one writer, in one
    module, that Task 8 can add a second DESTINATION to without adding a second WRITER.
    """
    assert enrich_base.write_facts is sink.write_facts
    source = Path(enrich_base.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert "write_facts" not in defined, (
        "the write step lives in `sink.py` (I16, M11 Task 8 Step 1); a definition here"
        " would be a second writer with the first one's name"
    )


def test_a_worker_that_touches_the_connection_is_refused_by_sqlite(db, log):
    """The second layer, and it watches the DANGEROUS CALL rather than the outcome.

    `sqlite3.connect` defaults to `check_same_thread=True` and `catalog/db.py` does not
    turn it off, so a worker that found the connection anyway raises rather than
    corrupting anything. Asserting only that the catalog looks right could not tell
    "never touched it" from "touched it and got away with it".
    """
    add_files(db, ["a.jpg"])

    def touch(file, seen):
        db.execute("SELECT 1")

    summary = run(db, FakeEnricher(boom=touch), workers=1, logger=log)
    assert summary.failed == 1
    assert summary.facts_written == 0
    assert any("ProgrammingError" in m for m in log.captured)


def test_the_connection_factory_still_refuses_cross_thread_use(tmp_path):
    """Pinned at the factory, not only here: `check_same_thread=False` anywhere in
    `catalog/db.py` would silently disarm the layer above."""
    conn = connect(tmp_path / "c.db")
    migrate(conn)
    caught = []

    def use():
        try:
            conn.execute("SELECT 1")
        except BaseException as exc:
            caught.append(type(exc).__name__)

    thread = threading.Thread(target=use)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive(), "the probe thread never finished"
    assert caught == ["ProgrammingError"]
    conn.close()


def test_one_file_failing_does_not_end_the_run(db, log):
    add_files(db, ["ok1.jpg", "bad.jpg", "ok2.jpg"])

    def boom(file, seen):
        if file.rel_path == "bad.jpg":
            raise OSError("cannot read")

    summary = run(db, FakeEnricher(boom=boom), workers=1, logger=log)
    assert summary.failed == 1
    assert summary.facts_written == 2
    assert any("bad.jpg" in m and "OSError" in m for m in log.captured)


# --- Step 5: the ETA -----------------------------------------------------------------


def test_the_eta_constants_are_spec_8_1s_numbers():
    """ "after 20 processed files it switches to an ... (alpha = 0.1)" - spec 8.1."""
    assert (PRIOR_FILES, ALPHA) == (20, 0.1)


def ewma_from_the_spec(observations, alpha=0.1):
    """An INDEPENDENT second implementation, written from spec 8.1's sentence.

    Not imported from `pam.enrich.eta`: two artefacts that pin each other prove they
    agree and never that either is right (M8's M922/M924), so this one is joined below
    by literals worked out by hand.
    """
    average = observations[0]
    for value in observations[1:]:
        average = alpha * value + (1 - alpha) * average
    return average


def test_the_eta_uses_the_prior_until_the_twentieth_file():
    eta = Eta(prior_ms=100)
    for _ in range(19):
        eta.observe(500.0)
    assert eta.per_file_ms() == 100.0
    # 100 ms x 3 files / min(1, 3) = 300 ms, by hand.
    assert eta.remaining_ms(3, workers=1) == 300.0


def test_the_eta_switches_at_the_twentieth_file_and_not_the_twenty_first():
    eta = Eta(prior_ms=100)
    for _ in range(20):
        eta.observe(500.0)
    assert eta.per_file_ms() == 500.0, "twenty observations of 500 average to exactly 500"
    # 500 ms x 2 files / min(1, 2) = 1000 ms, by hand.
    assert eta.remaining_ms(2, workers=1) == 1000.0


def test_the_average_is_the_spec_recurrence_and_the_number_it_produces():
    eta = Eta(prior_ms=7)
    observations = [1000.0, *([0.0] * 19)]
    for value in observations:
        eta.observe(value)
    # 1000 * 0.9**19, worked out by hand: the first observation SEEDS the average and
    # each of the nineteen zeros multiplies it by (1 - alpha).
    assert eta.per_file_ms() == pytest.approx(135.08517176729928)
    assert eta.per_file_ms() == pytest.approx(ewma_from_the_spec(observations))


def test_the_average_is_seeded_by_the_first_observation_and_not_by_the_prior():
    """Spec 8.1 says the EWMA is "of observed per-file times". Seeding it with
    `cost_estimate_ms` would leave the prior in the answer after the switch."""
    eta = Eta(prior_ms=1_000_000)
    for _ in range(PRIOR_FILES):
        eta.observe(10.0)
    assert eta.per_file_ms() == 10.0


def test_remaining_time_never_goes_negative():
    eta = Eta(prior_ms=100)
    assert eta.remaining_ms(-5, workers=4) == 0


def test_the_remaining_time_is_divided_by_the_workers_that_can_be_busy():
    """Spec 8.1 rev 16: `per_file_ms x files_remaining / min(workers, files_remaining)`.

    Every number below is worked out by hand from that sentence, not read back from
    `remaining_ms`. The third row is the one the formula's `min` exists for: four
    workers and two files left is two workers' worth of parallelism, not four.
    """
    eta = Eta(prior_ms=250)
    assert eta.remaining_ms(40, workers=1) == 10_000.0  # 250 * 40 / 1
    assert eta.remaining_ms(40, workers=4) == 2_500.0  # 250 * 40 / 4
    assert eta.remaining_ms(2, workers=4) == 250.0  # 250 * 2 / min(4, 2) = 250 * 2 / 2
    assert eta.remaining_ms(1, workers=4) == 250.0  # one file cannot be shared


def test_the_last_tick_of_every_run_would_divide_by_zero():
    """`min(workers, files_remaining)` is 0 when nothing is left, and every run ends
    there: `test_the_progress_eta_falls_as_the_run_proceeds` asserts the final tick is
    0 ms. The zero case is answered before the division, never by catching after it."""
    eta = Eta(prior_ms=250)
    assert eta.remaining_ms(0, workers=4) == 0.0
    assert eta.remaining_ms(0, workers=0) == 0.0


def test_the_1093x_case_the_m9_review_measured(db):
    """m9.md C3: 400 files, 360 cached, `cost_estimate_ms` 250, `workers = 4`.

    BY HAND from spec 8.1 rev 16, at the first tick, before any eligible file is
    walked: 40 files the runner does not already know it will skip, priced at the
    prior of 250 ms, over `min(4, 40)` = 4 workers -> **2500 ms**.

    Under the rev-15 reading the same tick displayed `250 x (400 - 50)` = 87.5 s for a
    run that took 0.08 s. The point of the assertion is the NUMBER; the old formula is
    named only so a reader can see which of the two this pins.
    """
    names = [f"f{i:03d}.jpg" for i in range(400)]
    add_files(db, names)

    first = FakeEnricher(cost_ms=250, wants_pred=lambda f: int(f.rel_path[1:4]) < 360)
    run(db, first, workers=4)

    ticks = []
    summary = run(db, FakeEnricher(cost_ms=250), workers=4, progress=ticks.append)
    assert summary.skipped_cached == 360, "the cache is known in full before the walk"
    assert ticks[0].walked == 50, "the batch boundary is 50 files WALKED"
    assert ticks[0].eta_ms == 2500.0


def test_the_eta_does_not_count_a_file_that_has_no_sha256(db):
    """Spec 8.1 rev 16's FIRST clause, which had no test at all (M10 Tasks 3+4, M1458).

    Rev 16 excludes two populations from `files_remaining`, and they are two separate
    clauses: a file with NO `sha256`, and a file ALREADY COVERED. The covered half is
    pinned by the 1093x case above; the sha256 half was pinned by nothing. Dropping it
    from `eligible_for_enrichment` - leaving `return file.sha256 not in covered`, which is
    True for a file whose sha256 is None - left ALL 2663 TESTS GREEN, so the ETA would
    again have counted files the runner already knows it will skip. That is the exact
    defect this whole condition exists to remove, surviving in the half nobody built a
    case for.

    THE UNHASHED FILES ARE WALKED FIRST, deliberately. The first progress tick lands at 50
    files WALKED, so with the 300 unhashed files at the head of the id order that tick
    fires having consumed none of the eligible work at all - which puts the two readings
    maximally far apart and makes the arithmetic checkable in the head.

    THE NUMBER IS COMPUTED BY HAND FROM THE SPEC SENTENCE, never read back from
    `remaining_ms` or from `eligible_for_enrichment`: 100 eligible files, none cached,
    `cost_estimate_ms` 250, `workers = 4` -> `250 x 100 / min(4, 100)` = 6250 ms. If the
    300 unhashed files were counted, `files_remaining` would be `400 - 50` = 350 and the
    same tick would read `250 x 350 / 4` = 21 875 ms - a 3.5x overstatement of the same
    run, which is the 1093x defect in miniature.
    """
    unhashed = [f"u{i:03d}.jpg" for i in range(300)]
    hashed = [f"h{i:03d}.jpg" for i in range(100)]
    add_files(db, unhashed + hashed, shas={name: f"sha-{name}" for name in hashed})

    ticks = []
    summary = run(db, FakeEnricher(cost_ms=250), workers=4, progress=ticks.append)

    # The runner really did meet all four hundred files and really did skip the three
    # hundred - so the tick below is about a population the runner knows it will skip.
    assert summary.skipped_no_sha == 300
    assert ticks[0].walked == 50, "the batch boundary is 50 files WALKED"
    assert ticks[0].eta_ms == 6250.0


def test_the_progress_eta_falls_as_the_run_proceeds(db):
    """A real clock is the wrong tool here: `FakeEnricher.enrich` does no measurable
    work, so `time.monotonic()` deltas depend on the runner's timer resolution rather
    than on anything this test controls. On this machine that delta is reliably
    nonzero; on a hosted CI runner it was reliably 0.0 for enough calls that the EWMA
    collapsed to 0 and the strict-decrease assertion below failed (M9 post-tag CI, both
    lanes, identical failure). A monotonically increasing fake clock makes every
    `_enrich_one` call see a fixed, positive elapsed time regardless of the host."""
    add_files(db, [f"f{i:03d}.jpg" for i in range(120)])
    ticks = []
    lock = threading.Lock()
    tick_count = [0.0]

    def fake_clock() -> float:
        with lock:
            tick_count[0] += 0.01
            return tick_count[0]

    run(db, FakeEnricher(cost_ms=10), progress=ticks.append, clock=fake_clock)
    assert [t.total for t in ticks] == [120, 120, 120]
    assert ticks[0].eta_ms > ticks[1].eta_ms > ticks[2].eta_ms == 0


# --- Step 6: spec 6.4 write-time structural validation --------------------------------


def test_the_schema_table_holds_exactly_the_kinds_the_spec_names():
    assert set(FACT_SCHEMAS) == set(SPEC_FACT_KINDS)


def test_there_is_no_geocode_schema():
    """M9 plan clarification 1: the geocode enricher's kind is `place`, not `geocode`.

    Task 9 corrected `_FACT_KINDS`, so nothing emits the kind `geocode` any more - and
    that is exactly why this assertion stays. `place` and `geocode` now have to agree in
    two places (this table and `_FACT_KINDS`); a `geocode` schema appearing here would
    make a REGRESSION of that correction storable instead of droppable, which is the
    wrong direction for a defect that has already been fixed once.
    """
    assert "geocode" not in FACT_SCHEMAS


def test_the_geocode_enricher_emits_the_kind_its_schema_is_declared_for():
    """The two halves of the Task 9 correction, pinned against each other AND the spec.

    `repo.fact_kinds("geocode")` is what `pam status` counts coverage over and
    `FACT_SCHEMAS` is what the runner validates against; a fact of a kind missing from
    either is dropped, silently for coverage and loudly for validation. Both are
    compared with the literal `"place"`, spelled from spec 6.1's token and spec 6.4's
    rule, rather than with each other.
    """
    assert repo.fact_kinds("geocode") == ("place",)
    assert "place" in FACT_SCHEMAS


@pytest.mark.parametrize("value", BAD_CONFIDENCES)
def test_a_confidence_outside_zero_to_one_is_not_well_formed(value):
    assert fact_error("phash", {"hash": "x"}, value) is not None


@pytest.mark.parametrize("value", GOOD_CONFIDENCES)
def test_a_confidence_inside_zero_to_one_is_well_formed(value):
    assert fact_error("phash", {"hash": "x"}, value) is None


@pytest.mark.parametrize(("kind", "value"), MALFORMED_VALUES)
def test_a_malformed_value_is_not_well_formed(kind, value):
    assert fact_error(kind, value, None) is not None


@pytest.mark.parametrize(("kind", "value", "confidence"), WELL_FORMED_VALUES)
def test_a_well_formed_value_passes(kind, value, confidence):
    assert fact_error(kind, value, confidence) is None


def test_an_unknown_kind_is_reported_as_such_even_when_its_confidence_is_also_wrong():
    """`fact_error`'s docstring calls its check ORDER load-bearing. This is that sentence.

    M11 Task 8 gave the schema lookup a second job - `requires_confidence`, which decides
    whether a NULL confidence is legal - and wrote that the lookup therefore has to come
    FIRST, adding: "A kind with no schema is reported as such even when its confidence is
    also wrong - the unknown kind is the more useful sentence."

    NOTHING TESTED IT. Task 8 stage 1's M1775 moved a `required=False` confidence check
    ahead of the lookup and all 3129 tests passed, because no test in the repository drove
    `fact_error` with an unknown kind at all. That is CLAUDE.md's untested-docstring
    pattern - write the test or delete the claim - so here is the test.

    BOTH HALVES. An unknown kind with a LEGAL confidence must also say "no declared
    schema", or this would pass under an implementation that reported the kind only by
    accident of the confidence being fine.
    """
    for confidence in (None, 0.5, 7.0, "not a number"):
        message = fact_error("no-such-kind", {"anything": 1}, confidence)
        assert message is not None
        assert "no-such-kind" in message and "no declared schema" in message, message


def test_every_list_driven_check_in_this_module_still_has_its_cases():
    """The suite's convention, and this module owed it (stage-1 review, M1214/M1215).

    EXACT lengths, not merely non-empty: an emptied parametrize is skipped rather than
    failed, and a narrowed one is invisible even as a skip. The three assertions after
    the counts are the FLOOR - written from spec 6.4 and spec 8.1 rather than from
    `FACT_SCHEMAS` - so that a list rewritten to five different cases could not keep the
    count while losing what the cases were cases OF.
    """
    assert len(CACHE_KEY_FIELDS) == 3
    assert len(BAD_CONFIDENCES) == 5
    assert len(GOOD_CONFIDENCES) == 4
    assert len(MALFORMED_VALUES) == 22
    assert len(WELL_FORMED_VALUES) == 9
    assert [field for field, _ in CACHE_KEY_FIELDS] == ["kind", "model_name", "version"]
    assert {kind for kind, _, _ in WELL_FORMED_VALUES} == SPEC_FACT_KINDS, (
        "every kind spec 6.4 declares needs a case that PASSES, or a schema could be"
        " tightened into rejecting everything and only the negative cases would notice"
    )
    assert any(value is True for value in BAD_CONFIDENCES), (
        "the bool-as-confidence trap `_confidence_error` exists to catch"
    )
    assert any(isinstance(v, float) and math.isnan(v) for v in BAD_CONFIDENCES)


def test_a_malformed_fact_is_logged_and_dropped_and_never_reaches_the_catalog(
    db, log, tmp_path
):
    """Spec 6.4: "Malformed output is logged and dropped - garbage never enters the
    catalog." Read back through a SECOND connection, so a fact that was inserted and
    then rolled back cannot pass for a fact that was correctly dropped."""

    class Malformed(FakeEnricher):
        def enrich(self, file, image):
            super().enrich(file, image)
            return [InferredFact(kind="phash", value={"wrong_key": "x"})]

    add_files(db, ["a.jpg"])
    summary = run(db, Malformed(), logger=log)
    assert (summary.facts_written, summary.facts_dropped) == (0, 1)
    assert stored_facts(tmp_path / "catalog.db") == []
    assert any("a.jpg" in m and "missing ['hash']" in m for m in log.captured)


def test_a_fact_of_a_kind_the_enricher_does_not_own_is_dropped(db, log, tmp_path):
    """`_FACT_KINDS` is what `pam status` counts coverage over, so a stored fact of a
    foreign kind would be a row that counts towards nothing."""
    add_files(db, ["a.jpg"])
    summary = run(db, FakeEnricher(name="phash", kind="caption"), logger=log)
    assert (summary.facts_written, summary.facts_dropped) == (0, 1)
    assert stored_facts(tmp_path / "catalog.db") == []
    assert any("phash emits phash" in m for m in log.captured)


def test_a_valid_fact_is_written_with_the_runners_provenance(db, tmp_path):
    """The enricher supplies the inference; the RUNNER stamps sha256 and the model."""
    add_files(db, ["a.jpg"])
    run(db, FakeEnricher(confidence=0.25))
    (row,) = stored_facts(tmp_path / "catalog.db")
    assert (row["sha256"], row["kind"], row["model_name"], row["model_version"]) == (
        "sha-a.jpg",
        "phash",
        "fake-model",
        "1.0",
    )
    assert row["confidence"] == 0.25
    assert row["value_json"] == '{"hash": "a.jpg-0"}'
    assert row["superseded_by"] is None


def test_a_fact_whose_unique_tuple_is_already_taken_is_not_rewritten(db, log, tmp_path):
    """The third drop path, and no test read its counter (stage-1 review, M1213).

    `superseded_by IS NULL` keeps a superseded fact from skipping its file, so the file
    IS re-enriched - and the superseded row still occupies
    `UNIQUE (sha256, kind, model_name, model_version)`. `fact_id_at` is what turns that
    into a counted, logged refusal instead of an `IntegrityError` that would end the
    run, and `facts_conflicted` could be frozen at 0 with the whole suite green.
    """
    add_files(db, ["a.jpg"])
    old = seed_fact(db, "sha-a.jpg")
    new = seed_fact(db, "sha-a.jpg", version="9.9")
    repo.supersede_facts(db, [old], new)
    db.commit()
    fake = FakeEnricher()
    summary = run(db, fake, logger=log)
    assert fake.enrich_calls == ["a.jpg"], "the fixture must actually reach the write"
    assert (summary.facts_conflicted, summary.facts_written) == (1, 0)
    assert [r["value_json"] for r in stored_facts(tmp_path / "catalog.db")] == [
        '{"hash": "seeded"}',
        '{"hash": "seeded"}',
    ], "the occupied row keeps its own value - the refusal is not a silent overwrite"
    assert any("already carries" in m and "not rewritten" in m for m in log.captured)


# --- Step 7: re-enrichment ------------------------------------------------------------


def test_version_key_orders_versions_as_numbers_not_as_text():
    """SQLite would order '10.0' before '2.0', which is why this is done in Python."""
    assert version_key("2.0") < version_key("10.0")
    assert version_key("1.9") < version_key("1.10") < version_key("2.0")
    assert version_key("2.0") < version_key("2.0a")
    assert version_key("2.0") == version_key("2.0")


def test_version_key_uses_isdecimal_so_a_superscript_cannot_raise():
    """CLAUDE.md's trap: '²'.isdigit() is True and int() RAISES on it."""
    assert version_key("²") == ((1, 0, "²"),)


def test_a_model_upgrade_supersedes_the_old_fact_and_writes_the_new_one(db, tmp_path):
    add_files(db, ["a.jpg"])
    run(db, FakeEnricher(model_version="1.0"))
    summary = run(db, FakeEnricher(model_version="2.0"), refresh_older_than_model="2.0")
    old, new = stored_facts(tmp_path / "catalog.db")
    assert old["model_version"] == "1.0"
    assert new["model_version"] == "2.0"
    assert old["superseded_by"] == new["id"]
    assert new["superseded_by"] is None
    assert summary.superseded == 1


def test_the_supersede_leaves_exactly_one_live_fact_and_the_file_counted_once(db, tmp_path):
    """What this CAN see, said honestly (stage-1 review, M1188).

    It used to be named for "not two hundred percent" and to assert only the coverage
    number - which is `COUNT(DISTINCT f.id)` and can never exceed the file count, so
    the assertion held with or without `superseded_by IS NULL`. The reachable property
    is the one below: two rows exist, exactly ONE of them is live, and the file is
    counted once. The clause's over-count is measured in `tests/test_repo.py`, on a
    state the runner cannot produce.
    """
    add_files(db, ["a.jpg"])
    run(db, FakeEnricher(model_version="1.0"))
    run(db, FakeEnricher(model_version="2.0"), refresh_older_than_model="2.0")
    rows = stored_facts(tmp_path / "catalog.db")
    assert [r["model_version"] for r in rows] == ["1.0", "2.0"]
    assert [r["superseded_by"] is None for r in rows] == [False, True]
    assert repo.enrichment_coverage(db)["phash"] == 1


def test_without_the_flag_a_model_upgrade_leaves_both_facts_live(db, tmp_path):
    """What the flag actually buys, stated as a test rather than as a docstring: the
    cache key already contains the version, so the FILES re-run either way. Only the
    supersede is the flag's."""
    add_files(db, ["a.jpg"])
    run(db, FakeEnricher(model_version="1.0"))
    fake = FakeEnricher(model_version="2.0")
    run(db, fake)
    assert fake.enrich_calls == ["a.jpg"], "the upgrade re-runs with or without the flag"
    assert [f["superseded_by"] for f in stored_facts(tmp_path / "catalog.db")] == [None, None]


def test_a_refresh_newer_than_the_installed_model_is_refused_before_anything_is_written(
    db, tmp_path
):
    """The `UNIQUE (sha256, kind, model_name, model_version)` case, refused rather than
    papered over: the run would rewrite facts at the version the refresh calls stale,
    and there is no `ON CONFLICT REPLACE` on this project."""
    add_files(db, ["a.jpg"])
    with pytest.raises(ValueError, match="newer than"):
        run(db, FakeEnricher(model_version="1.0"), refresh_older_than_model="2.0")
    assert stored_runs(tmp_path / "catalog.db") == []
    assert stored_facts(tmp_path / "catalog.db") == []


def test_a_refresh_at_the_installed_version_selects_only_strictly_older_facts(db, tmp_path):
    add_files(db, ["a.jpg"])
    run(db, FakeEnricher(model_version="2.0"))
    again = FakeEnricher(model_version="2.0")
    summary = run(db, again, refresh_older_than_model="2.0")
    assert again.enrich_calls == [], "its own fact is not older than itself"
    assert summary.superseded == 0
    assert [f["superseded_by"] for f in stored_facts(tmp_path / "catalog.db")] == [None]


def test_a_refresh_ignores_another_models_facts(db, tmp_path):
    """ "Older than" across two different model NAMES is not a comparison that means
    anything, so the refresh is scoped to the enricher's own model."""
    add_files(db, ["a.jpg"])
    seed_fact(db, "sha-a.jpg", model_name="someone-elses", version="0.1")
    run(db, FakeEnricher(model_version="2.0"), refresh_older_than_model="2.0")
    rows = stored_facts(tmp_path / "catalog.db")
    assert [r["model_name"] for r in rows] == ["someone-elses", "fake-model"]
    assert [r["superseded_by"] for r in rows] == [None, None]


def test_an_already_superseded_fact_is_not_superseded_a_second_time(db, tmp_path):
    """`superseded_by IS NULL`, load-bearing reading two: repointing a live edge is how
    spec 7.5 check 5's cycle becomes reachable."""
    add_files(db, ["a.jpg"])
    old = seed_fact(db, "sha-a.jpg", version="0.5")
    mid = seed_fact(db, "sha-a.jpg", version="1.0")
    repo.supersede_facts(db, [old], mid)
    db.commit()
    run(db, FakeEnricher(model_version="2.0"), refresh_older_than_model="2.0")
    rows = {r["model_version"]: r for r in stored_facts(tmp_path / "catalog.db")}
    assert rows["0.5"]["superseded_by"] == mid, "unchanged: it was already superseded"
    assert rows["1.0"]["superseded_by"] == rows["2.0"]["id"]


def test_supersede_never_points_a_fact_at_itself(db):
    """A one-node cycle, which spec 7.5 check 5 reports as an error."""
    add_files(db, ["a.jpg"])
    only = seed_fact(db, "sha-a.jpg")
    repo.supersede_facts(db, [only], only)
    db.commit()
    assert repo.fact_supersede_edges(db) == []


def test_the_supersede_chain_has_no_cycle_and_no_dangling_target(db, tmp_path):
    """Spec 7.5 check 5's two questions, asked with the SUITE's own walk rather than by
    calling the checker that would answer them."""
    add_files(db, ["a.jpg", "b.jpg"])
    for version in ("1.0", "2.0", "3.0"):
        run(db, FakeEnricher(model_version=version), refresh_older_than_model=version)
    rows = stored_facts(tmp_path / "catalog.db")
    ids = {r["id"] for r in rows}
    edges = {r["id"]: r["superseded_by"] for r in rows if r["superseded_by"] is not None}
    assert edges, "the fixture must actually produce supersede edges"
    assert set(edges.values()) <= ids
    for start in edges:
        seen, node = {start}, start
        while node in edges:
            node = edges[node]
            assert node not in seen, f"cycle from {start}"
            seen.add(node)


# --- `fact_kinds`, the one map -------------------------------------------------------


def test_fact_kinds_answers_for_every_enricher_that_writes_facts():
    """Spec 8.1's five names, spelled out rather than read off the map."""
    assert {n: repo.fact_kinds(n) for n in ("caption", "faces", "ocr", "phash", "geocode")} == {
        "caption": ("caption",),
        # M11 (A7, spec 4 rev 22). The MARKER and only the marker: the detections are the
        # `face` kind and go to `face_instances` through a sink, so a `"face"` appearing
        # in this tuple would make `pam status` count coverage over a table
        # `_fact_coverage` does not read.
        "faces": ("faces",),
        # M12 Task 13 (design 8.1 rev 27, P7): ONE stored kind. `ocr_date` is not a kind.
        "ocr": ("ocr_text",),
        "phash": ("phash",),
        # CORRECTED IN TASK 9, and this line was the tripwire that made the correction
        # visible: Task 7 pinned the known-wrong `("geocode",)` here on purpose so that
        # M9's fix could not land without a red test. The kind is `place` - spec 6.1's
        # token is `{place}` and spec 6.4's validation rule is written about "`place`
        # tokens" - and `SPEC_FACT_KINDS` above has said so since Task 7.
        "geocode": ("place",),
    }


def test_fact_kinds_gives_faces_the_marker_and_not_the_detection():
    """A7, and the negative half is the point (I8).

    Through M10 this name RAISED, because spec 4 gave the enricher `face_instances` and
    no fact kind at all. Rev 22 gives it the per-file marker - which is what makes a
    photograph with no face in it COVERED rather than re-decoded on every run for ever -
    and gives the DETECTIONS a separate kind that this map must not name.

    Both halves are spelled from spec 4 rev 22, not from `_FACT_KINDS` or from
    `sink.SINK_FACT_KINDS`, either of which would agree with any value of itself.
    """
    assert repo.fact_kinds("faces") == ("faces",)
    assert "face" not in repo.fact_kinds("faces")


def test_fact_kinds_refuses_a_name_that_is_not_an_enricher():
    with pytest.raises(KeyError, match="no inferred_facts kinds"):
        repo.fact_kinds("not-an-enricher")


def test_fact_id_at_sees_a_superseded_row_because_the_unique_constraint_does(db):
    """The one query here that is deliberately NOT filtered by `superseded_by`: a
    superseded row still occupies its UNIQUE tuple and an insert over it still raises."""
    add_files(db, ["a.jpg"])
    old = seed_fact(db, "sha-a.jpg")
    new = seed_fact(db, "sha-a.jpg", version="2.0")
    repo.supersede_facts(db, [old], new)
    db.commit()
    assert repo.fact_id_at(db, "sha-a.jpg", "phash", "fake-model", "1.0") == old
    with pytest.raises(sqlite3.IntegrityError):
        seed_fact(db, "sha-a.jpg")


# --- A7's partition and A8's marker, asserted HERE as well ----------------------------
#
# WHY A SECOND MODULE ASSERTS THIS AT ALL (Task 8 stage 2, closing `M1781`). Stage 1's
# sole-guardian probe deleted `tests/test_faces_sink.py` outright, re-applied the routing
# mutation that writes a `face` fact to `inferred_facts` as well as to `face_instances`,
# and got 3115 passed / 0 failed: with that one module gone, NOTHING in the suite could
# see A7's partition break. A module's own `LOAD_BEARING_CONTROLS` floor is deleted with
# the module, so the answer is not a registration - it is a control that lives somewhere
# else, and this module is where the runner's contract is already tested.
#
# WRITTEN AS AN INDEPENDENT SECOND IMPLEMENTATION. Nothing is imported from
# `tests/test_faces_sink.py` - not its fake, not its geometry, not its readers. An import
# would make deleting that module a COLLECTION ERROR, which passes the probe for the wrong
# reason and breaks the moment somebody splits the file. The enricher below, the geometry
# document and the expected rows are all spelled from amendment A7 (one `faces` marker per
# file carrying a count, including zero, and one `face` fact per detection), amendment A13
# (the versioned geometry document) and design 4's `face_instances` columns.


# A13's document, spelled out here rather than borrowed. The coordinates are normalised,
# which is what makes a box measured against one decoding select the same pixels in
# another; the values themselves are arbitrary because this module decodes nothing.
A13_GEOMETRY = {
    "v": 1,
    "bbox": [0.25, 0.10, 0.20, 0.30],
    "landmarks": [[0.30, 0.20], [0.40, 0.20], [0.35, 0.25], [0.31, 0.32], [0.39, 0.32]],
    "source_w": 4032,
    "source_h": 3024,
    "orientation": 6,
}


class PeopleFinder:
    """An A7-shaped enricher: one `faces` marker per file, one `face` per detection.

    Deliberately NOT a `FakeEnricher` subclass. `FakeEnricher` emits one KIND; A7's whole
    point is that one enricher emits two kinds with two DESTINATIONS, and a fake that
    reached that shape by parameterising the existing one would be describing the design
    in terms of the design. `detections` is `rel_path -> [confidence, ...]`, so the caller
    spells the expected rows out from the same literals it spells the input from.
    """

    name = "faces"
    model_name = "yunet+sface"
    model_version = "second-implementation-1"

    def __init__(self, detections):
        self._detections = detections

    def available(self):
        return True, "the fake pack is always here"

    def cost_estimate_ms(self):
        return 700

    def wants(self, file):
        return file.rel_path in self._detections

    def enrich(self, file, image):
        scores = self._detections[file.rel_path]
        facts = [InferredFact(kind="faces", value={"count": len(scores)})]
        facts.extend(
            InferredFact(
                kind="face",
                value={"geometry": A13_GEOMETRY, "embedding": bytes([n % 256]) * 512},
                confidence=score,
            )
            for n, score in enumerate(scores)
        )
        return facts


def stored_face_rows(db_path):
    """`face_instances`, read with this module's OWN SQL on a SECOND connection.

    Design 4's columns, spelled out. Not `repo.faces_for_clustering`, which filters, and
    not `repo.export_faces`, which shapes: an oracle that calls a production query agrees
    with any value of that query, and a filtering one would hide exactly the rows A11 says
    must be stored.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT sha256, bbox_json, embedding, det_confidence, model_name,"
                " model_version, cluster_id, person_id, review_state"
                " FROM face_instances ORDER BY id"
            )
        ]
    finally:
        conn.close()


def test_a_face_fact_leaves_inferred_facts_holding_EXACTLY_the_marker(db, tmp_path):
    """A7's partition, in a second module (`M1781`).

    EXACTLY, never "contains". A containment assertion survives the routing mutation this
    design exists to prevent - the extra `inferred_facts` row is simply not looked at -
    which is M8's partition lesson and is what `M1762`/`M1763` measured: three of that
    mutant's four kills came from the exact equality and vanished when it was weakened.
    """
    add_files(db, ["a.jpg", "b.jpg"])
    run(db, PeopleFinder({"a.jpg": [0.91, 0.62], "b.jpg": []}))

    facts = stored_facts(tmp_path / "catalog.db")
    assert [(f["kind"], json.loads(f["value_json"])) for f in facts] == [
        ("faces", {"count": 2}),
        ("faces", {"count": 0}),
    ], "a `face` fact reached `inferred_facts`; A7 routes it to `face_instances` alone"

    rows = stored_face_rows(tmp_path / "catalog.db")
    assert [(r["sha256"], r["det_confidence"]) for r in rows] == [
        ("sha-a.jpg", 0.91),
        ("sha-a.jpg", 0.62),
    ]
    assert [json.loads(r["bbox_json"]) for r in rows] == [A13_GEOMETRY, A13_GEOMETRY]
    assert [bytes(r["embedding"]) for r in rows] == [bytes([0]) * 512, bytes([1]) * 512]
    assert {r["model_name"] for r in rows} == {"yunet+sface"}
    assert {r["model_version"] for r in rows} == {"second-implementation-1"}
    # I2 and A11: an enricher names nobody and reviews nothing.
    assert [(r["cluster_id"], r["person_id"], r["review_state"]) for r in rows] == [
        (None, None, None),
        (None, None, None),
    ]


def test_every_marker_counts_the_rows_the_table_really_holds(db, tmp_path):
    """A8/I9, read from the TABLE rather than from the sink's call count (`M1764`).

    STAGE 1 DISCLOSED THIS RESIDUAL AND IT IS CLOSED HERE. A8 says the marker's count is
    "computed from the rows actually accepted", but the runner tallies sink CALLS, so a
    sink that returned without inserting would leave a marker claiming detections the
    catalog does not hold - and stage 1 measured it: under `M1764` (A11's floor applied at
    WRITE time) the marker said 2 over 1 stored row and every existing assertion passed.
    `FactSink` is typed `-> None` by the plan's own Step 3 block, so a reviewer may not
    make the count come from the sink; an oracle that compares the marker against the rows
    needs no signature at all.

    THE SUB-FLOOR DETECTION IS WHY THE DATA LOOKS LIKE THIS. 0.20 is below `[tiers] faces`
    floor's default of 0.4, so a write-time floor would decline exactly that row while the
    marker still counted it. A11 says every structurally valid detection is stored and the
    floor is applied when detections are READ.
    """
    add_files(db, ["c.jpg", "d.jpg", "e.jpg"])
    run(db, PeopleFinder({"c.jpg": [0.95, 0.20], "d.jpg": [], "e.jpg": [0.41]}))

    counts = {
        f["sha256"]: json.loads(f["value_json"])["count"]
        for f in stored_facts(tmp_path / "catalog.db")
        if f["kind"] == "faces"
    }
    rows: dict[str, int] = {}
    for row in stored_face_rows(tmp_path / "catalog.db"):
        rows[row["sha256"]] = rows.get(row["sha256"], 0) + 1

    assert counts == {"sha-c.jpg": 2, "sha-d.jpg": 0, "sha-e.jpg": 1}
    assert {sha: rows.get(sha, 0) for sha in counts} == counts, (
        "a `faces` marker claims a number of detections that `face_instances` does not"
        f" hold: markers {counts}, rows {rows}. A8's count is about STORED rows."
    )


# ---------------------------------------------------------------------------
# THE DELETION FLOOR (M10's C4, and `M1670`'s lesson about the instruments).
#
# HAND-SPELLED, NEVER DERIVED FROM `globals()`: an inventory that asks the module what it
# contains agrees with any value of itself, so deleting a test would delete its name with
# it and the floor would pass.
# ---------------------------------------------------------------------------

LOAD_BEARING_CONTROLS = {
    "test_a_face_fact_leaves_inferred_facts_holding_EXACTLY_the_marker": (
        "M1781: the SECOND module asserting A7's partition. With this gone, deleting "
        "tests/test_faces_sink.py leaves nothing in the suite able to see a `face` "
        "fact routed into `inferred_facts`"
    ),
    "test_every_marker_counts_the_rows_the_table_really_holds": (
        "M1764: the only assertion anywhere that compares a `faces` marker's count "
        "against the rows `face_instances` really holds, rather than against the "
        "number of times the sink was called"
    ),
    "test_the_fact_writer_runs_only_on_the_calling_thread": (
        "spec 8.1's single writer, watched as the dangerous CALL (M697) and not as "
        "the rows left behind; it is what Task 8 Step 1's move had to re-prove"
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
