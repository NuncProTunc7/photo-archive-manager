"""`pam faces purge` (M11 Task 13, Steps 1, 3 and 4; amendment A19).

THE FIVE DELETIONS ARE READ BACK WITH THE SUITE'S OWN SQL, never through the repo
function that performed them. A deletion asserted with `repo.export_faces(conn) == []`
proves the reader and the writer agree about emptiness and nothing else - which is this
project's most repeated defect (M5's `open_plan`, M6's `ENRICHERS`, M8's M922).

AND THE ORACLE FOR COMPACTION IS `PRAGMA freelist_count` AND THE WAL, NEVER FILE SIZE.
A19 says so in as many words: file size is nondeterministic and a test over it can pass
without proving the promised result.
"""

import json
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner
from test_faces_cluster import GEOMETRY, archive, blob, plant, plant_person

from pam.catalog.db import (
    CHECKPOINT_BLOCKED,
    compact,
    connect,
    connect_readonly,
    freelist_count,
)
from pam.cli import cli, main
from pam.errors import EXIT_EXPECTED
from pam.faces.purge import (
    DISCLOSURE,
    PurgeInventory,
    confirmation_lines,
    purge_lines,
    run_purge,
)
from pam.paths import workspace_if_exists

VECTOR = (1.0, 0.0, 0.0, 0.0)


def raw(root):
    """A connection for the SUITE's own SQL. `sqlite3.Row` so columns have names."""
    ws = workspace_if_exists(root)
    assert ws is not None
    conn = connect(ws.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def count(root, sql, params=()):
    conn = raw(root)
    try:
        return int(conn.execute(sql, params).fetchone()[0])
    finally:
        conn.close()


def plant_fact(root, sha, kind="faces", superseded_by=None, version="abc123"):
    """One `inferred_facts` row, with the suite's own INSERT (spec 4's columns)."""
    conn = raw(root)
    try:
        cur = conn.execute(
            "INSERT INTO inferred_facts (sha256, kind, value_json, confidence,"
            " model_name, model_version, generated_at, superseded_by)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sha,
                kind,
                json.dumps({"count": 1}),
                None,
                "yunet+sface",
                version,
                "2026-09-17T10:00:00",
                superseded_by,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def plant_run(root, enricher="faces", cursor=42, finished=None):
    conn = raw(root)
    try:
        cur = conn.execute(
            "INSERT INTO enrichment_runs (enricher, model_name, model_version,"
            " started_at, finished_at, cursor_file_id) VALUES (?, ?, ?, ?, ?, ?)",
            (enricher, "yunet+sface", "abc123", "2026-09-17T10:00:00", finished, cursor),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def stocked(tmp_path):
    """An archive holding one of each of the five things A19 names, plus decoys.

    THE DECOYS ARE THE POINT of half the assertions below: a `phash` fact and a `phash`
    run must survive, or "delete the faces marker" would pass equally well as "TRUNCATE
    inferred_facts", and a user would lose every perceptual hash in the archive to a
    command about faces.
    """
    root = archive(tmp_path)
    person = plant_person(root, "Zoë")
    plant(root, VECTOR, sha="sha-1", person_id=person)
    plant(root, VECTOR, sha="sha-2")
    plant_fact(root, "sha-1", kind="faces")
    plant_fact(root, "sha-2", kind="phash")
    plant_run(root, "faces")
    plant_run(root, "phash")
    return root


def yes(_inventory, _root):
    return True


def no(_inventory, _root):
    return False


# ---------------------------------------------------------------------------
# 1. THE FIVE DELETIONS - each asserted by SQL the suite wrote itself.
# ---------------------------------------------------------------------------


def test_every_face_instances_row_is_gone(tmp_path):
    root = stocked(tmp_path)
    assert count(root, "SELECT COUNT(*) FROM face_instances") == 2

    run_purge(root, confirm=yes)

    assert count(root, "SELECT COUNT(*) FROM face_instances") == 0


def test_every_persons_row_is_gone(tmp_path):
    root = stocked(tmp_path)
    assert count(root, "SELECT COUNT(*) FROM persons") == 1

    run_purge(root, confirm=yes)

    assert count(root, "SELECT COUNT(*) FROM persons") == 0


def test_every_faces_marker_fact_is_gone_and_no_other_kind_is(tmp_path):
    """A19's deletion 3, with its decoy. LEAVE IT and spec 8.1's cache skip reports every
    file already done, so the next `pam enrich faces` detects nothing and says it
    succeeded - the symptom nobody would diagnose. DELETE TOO MUCH and a command about
    faces silently throws away every perceptual hash in the archive."""
    root = stocked(tmp_path)

    run_purge(root, confirm=yes)

    assert count(root, "SELECT COUNT(*) FROM inferred_facts WHERE kind = 'faces'") == 0
    assert count(root, "SELECT COUNT(*) FROM inferred_facts WHERE kind = 'phash'") == 1


def test_a_SUPERSEDED_faces_marker_is_deleted_too(tmp_path):
    """ "Every `faces` marker fact", and a superseded one is still one of pam's records.

    It is also load-bearing for the symptom: `_fact_coverage` reads only live rows, so a
    purge that left superseded markers behind would look clean to every pam query and
    still leave rows about this user's faces in the table.
    """
    root = stocked(tmp_path)
    live = plant_fact(root, "sha-1", kind="faces", version="gen-2")
    plant_fact(root, "sha-1", kind="faces", version="gen-1", superseded_by=live)
    assert count(root, "SELECT COUNT(*) FROM inferred_facts WHERE kind = 'faces'") == 3

    run_purge(root, confirm=yes)

    assert count(root, "SELECT COUNT(*) FROM inferred_facts WHERE kind = 'faces'") == 0


def test_every_faces_enrichment_run_is_gone_and_no_other_enrichers_is(tmp_path):
    """A19's deletion 4. LEAVE IT and `open_run` resumes at `cursor_file_id`, so the next
    run starts PAST every file the purged one walked and reports a clean pass."""
    root = stocked(tmp_path)

    run_purge(root, confirm=yes)

    assert count(root, "SELECT COUNT(*) FROM enrichment_runs WHERE enricher = 'faces'") == 0
    assert count(root, "SELECT COUNT(*) FROM enrichment_runs WHERE enricher = 'phash'") == 1


def test_a_FINISHED_faces_run_is_deleted_too_not_only_the_resumable_one(tmp_path):
    """A finished run carries no cursor and so has no resume symptom - and it is still a
    record that pam ran a face model over this archive on this date, which is exactly what
    A19's "pam's own records" covers. A `WHERE finished_at IS NULL` would pass every other
    row in this module."""
    root = stocked(tmp_path)
    plant_run(root, "faces", cursor=None, finished="2026-09-17T11:00:00")

    run_purge(root, confirm=yes)

    assert count(root, "SELECT COUNT(*) FROM enrichment_runs WHERE enricher = 'faces'") == 0


def test_the_counts_in_the_report_are_the_rows_that_were_really_there(tmp_path):
    root = stocked(tmp_path)

    report = run_purge(root, confirm=yes)

    assert (report.faces, report.persons, report.facts, report.runs) == (2, 1, 1, 1)
    assert report.deleted is True and report.cancelled is False


def test_deletion_5_is_that_there_are_no_crops_and_the_count_is_zero(tmp_path):
    """A19's fifth deletion, answered rather than performed. `test_a_crop_request_writes_
    nothing_anywhere_under_the_workspace` below is the measurement behind this number."""
    root = stocked(tmp_path)

    report = run_purge(root, confirm=yes)

    assert report.crops == 0


# ---------------------------------------------------------------------------
# 2. ONE TRANSACTION.
# ---------------------------------------------------------------------------


def test_a_failure_part_way_through_the_deletions_leaves_EVERY_row_in_place(
    tmp_path, monkeypatch
):
    """A19: "in one transaction". Four deletions committed and the fifth lost is a catalog
    with no faces in it and a marker fact saying every file has already been looked at -
    which is the state that sentence exists to forbid.

    The failure is injected at the LAST repo call, so everything before it has already run
    against the connection and only the absent commit saves the catalog.
    """
    from pam.catalog import repo

    root = stocked(tmp_path)

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repo, "delete_enrichment_runs", boom)

    with pytest.raises(sqlite3.OperationalError):
        run_purge(root, confirm=yes)

    assert count(root, "SELECT COUNT(*) FROM face_instances") == 2
    assert count(root, "SELECT COUNT(*) FROM persons") == 1
    assert count(root, "SELECT COUNT(*) FROM inferred_facts WHERE kind = 'faces'") == 1


# ---------------------------------------------------------------------------
# 3. THE CONFIRMATION.
# ---------------------------------------------------------------------------


def test_a_declined_confirmation_writes_NOTHING(tmp_path):
    root = stocked(tmp_path)

    report = run_purge(root, confirm=no)

    assert report.cancelled is True and report.deleted is False
    assert count(root, "SELECT COUNT(*) FROM face_instances") == 2
    assert count(root, "SELECT COUNT(*) FROM persons") == 1
    assert count(root, "SELECT COUNT(*) FROM inferred_facts WHERE kind = 'faces'") == 1
    assert count(root, "SELECT COUNT(*) FROM enrichment_runs WHERE enricher = 'faces'") == 1


def test_a_declined_confirmation_does_not_compact_either(tmp_path):
    """Compaction is what the second run is FOR. A cancel that ran a VACUUM anyway would
    rewrite a database the user just said to leave alone."""
    root = stocked(tmp_path)
    ran: list[str] = []

    run_purge(root, confirm=no, compaction=lambda conn: ran.append("compacted"))

    assert ran == []


def test_the_confirmation_box_names_faces_people_photographs_and_the_export(tmp_path):
    """Step 3's four requirements, and each is a separate failure if it goes missing.

    THE PHOTOGRAPH COUNT IS NOT THE FACE COUNT and a user reading one number could
    reasonably fear the other. The `pam faces export` pointer is BEFORE the prompt: after
    it is where a sentence about keeping your data goes when nobody will read it.
    """
    inventory = PurgeInventory(
        faces=402, persons=7, photographs=180, plans=0, plan_items=0, facts=180, runs=2
    )

    text = "\n".join(confirmation_lines(inventory, Path("C:/photos")))

    assert "402 faces" in text
    assert "180 photographs" in text
    assert "7 people" in text
    assert "180 note(s)" in text and "2 record(s)" in text
    assert "NO PHOTOGRAPH IS TOUCHED" in text
    assert "pam faces export" in text
    assert text.index("pam faces export") > text.index("402 faces")


def test_the_typed_word_is_exactly_purge_and_nothing_else_will_do(tmp_path, monkeypatch):
    """`execute/reporter.py`'s ritual, one command over. A confirmation that accepts `y`
    is one a user completes without reading, and this one deletes work they cannot get
    back by re-running anything - pam has no import."""
    from pam.faces import purge as purge_mod

    root = stocked(tmp_path)
    for typed in ("y", "yes", "PURGE", " purge", "purge ", ""):
        monkeypatch.setattr("builtins.input", lambda typed=typed: typed)
        assert purge_mod.confirm_purge(purge_mod.read_inventory(raw(root)), root) is False
    monkeypatch.setattr("builtins.input", lambda: "purge")
    assert purge_mod.confirm_purge(purge_mod.read_inventory(raw(root)), root) is True


def test_a_closed_stdin_CANCELS(tmp_path, monkeypatch):
    """`pam faces purge < /dev/null` must not delete a year of somebody's labeling because
    a script pointed the wrong way. `confirm_apply`'s rule, and its reason."""
    from pam.faces import purge as purge_mod

    root = stocked(tmp_path)

    def eof():
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    assert purge_mod.confirm_purge(purge_mod.read_inventory(raw(root)), root) is False


def test_a_second_purge_over_an_empty_face_catalog_asks_NOTHING(tmp_path):
    """A19's SAFE RETRY, and the property that makes it safe.

    A failed compaction leaves the user holding a committed deletion and a command to run
    again. If that second run demanded they type `purge` to delete nothing, the retry
    would be a ritual about a deletion that has already happened - and a user who typed
    the word would reasonably believe something was still being destroyed.
    """
    root = stocked(tmp_path)
    run_purge(root, confirm=yes)
    asked: list[str] = []

    def record(_inventory, _root):
        asked.append("asked")
        return True

    report = run_purge(root, confirm=record)

    assert asked == []
    assert report.deleted is False and report.cancelled is False
    assert report.compacted is True


# ---------------------------------------------------------------------------
# 3a. AN ARCHIVE THE DETECTOR FOUND NO FACE IN (reviewer finding F1).
#
# `pam enrich faces` over an archive of landscapes writes a `faces` marker fact per file
# and one `enrichment_runs` row and detects nothing. The catalog then holds A19's
# deletions 3 and 4 with no face and no person anywhere in it - and that is not an exotic
# setup, it is what a purge is FOR when the user is trying to make pam look again.
# ---------------------------------------------------------------------------


def looked_but_found_nothing(tmp_path):
    """Two `faces` markers and a `faces` run; no face, no person. Plus the decoys."""
    root = archive(tmp_path)
    plant_fact(root, "sha-1", kind="faces")
    plant_fact(root, "sha-2", kind="faces")
    plant_fact(root, "sha-2", kind="phash")
    plant_run(root, "faces")
    plant_run(root, "phash")
    return root


def test_a_purge_with_no_face_and_no_person_still_performs_deletions_3_and_4(tmp_path):
    """THE FINDING. Gating the transaction on faces and people alone made this purge
    delete NOTHING - and deletion 3's own symptom is what it left behind."""
    root = looked_but_found_nothing(tmp_path)
    assert count(root, "SELECT COUNT(*) FROM face_instances") == 0
    assert count(root, "SELECT COUNT(*) FROM persons") == 0

    report = run_purge(root, confirm=yes)

    assert report.deleted is True
    assert (report.facts, report.runs) == (2, 1)
    assert count(root, "SELECT COUNT(*) FROM inferred_facts WHERE kind = 'faces'") == 0
    assert count(root, "SELECT COUNT(*) FROM enrichment_runs WHERE enricher = 'faces'") == 0
    # The decoys, so the fix cannot be "delete more".
    assert count(root, "SELECT COUNT(*) FROM inferred_facts WHERE kind = 'phash'") == 1
    assert count(root, "SELECT COUNT(*) FROM enrichment_runs WHERE enricher = 'phash'") == 1


def test_that_purge_ASKS_first_and_a_declined_one_leaves_the_records(tmp_path):
    """It is a deletion, so it goes through A19's ritual like any other. Before the fix no
    confirmation was reached at all, which is the same bug read from the other end."""
    root = looked_but_found_nothing(tmp_path)

    report = run_purge(root, confirm=no)

    assert report.cancelled is True and report.deleted is False
    assert count(root, "SELECT COUNT(*) FROM inferred_facts WHERE kind = 'faces'") == 2
    assert count(root, "SELECT COUNT(*) FROM enrichment_runs WHERE enricher = 'faces'") == 1


def test_the_box_names_the_notes_and_the_run_records_when_they_are_all_there_is(
    tmp_path, monkeypatch
):
    """A user typing `purge` under two zeroes must be able to see what they are deleting.

    Driven through the CLI so the box that is PRINTED is the one asserted, not the one
    `confirmation_lines` returns to a caller nobody uses.
    """
    root = looked_but_found_nothing(tmp_path)

    monkeypatch.setattr("builtins.input", lambda: "purge")
    result = CliRunner().invoke(cli, ["faces", "purge", "--archive", str(root)])

    assert result.exit_code == 0, result.output
    assert "0 faces found over 0 photographs" in result.output
    assert "2 note(s) recording which files pam has already looked at" in result.output
    assert "1 record(s) of a face run" in result.output
    assert "2 enrichment fact(s), 1 run record(s)" in result.output


def test_a_catalog_with_a_SUPERSEDED_marker_and_nothing_else_is_not_empty(tmp_path):
    """`_fact_coverage` counts LIVE rows only, so a catalog whose markers are all
    superseded looks covered-by-nothing to every pam query and still holds the rows. The
    inventory's predicate is the DELETION's predicate, which is what makes them agree."""
    root = archive(tmp_path)
    live = plant_fact(root, "sha-1", kind="faces", version="gen-2")
    plant_fact(root, "sha-1", kind="faces", version="gen-1", superseded_by=live)

    report = run_purge(root, confirm=yes)

    assert report.deleted is True and report.facts == 2
    assert count(root, "SELECT COUNT(*) FROM inferred_facts WHERE kind = 'faces'") == 0


def test_a_catalog_holding_ONLY_a_finished_run_is_not_empty_either(tmp_path):
    """`open_run` hands back the newest RESUMABLE run and would answer "nothing here"."""
    root = archive(tmp_path)
    plant_run(root, "faces", cursor=None, finished="2026-09-17T11:00:00")

    report = run_purge(root, confirm=yes)

    assert report.deleted is True and report.runs == 1
    assert count(root, "SELECT COUNT(*) FROM enrichment_runs WHERE enricher = 'faces'") == 0


def test_a_TRULY_empty_catalog_still_asks_nothing_and_deletes_nothing(tmp_path):
    """The other direction, and A19's retry depends on it: with all four counts at zero
    the command must not start asking a user to confirm a deletion of nothing."""
    root = archive(tmp_path)
    plant_fact(root, "sha-2", kind="phash")
    plant_run(root, "phash")
    asked: list[str] = []

    report = run_purge(root, confirm=lambda _i, _r: asked.append("asked") or True)

    assert asked == []
    assert report.deleted is False and report.cancelled is False
    assert report.compacted is True
    assert "pam holds no faces, no people and no record" in "\n".join(purge_lines(report))


# ---------------------------------------------------------------------------
# 4. COMPACTION - `freelist_count` and the WAL, never file size (A19).
# ---------------------------------------------------------------------------


def bulk(root, faces=400):
    """Enough rows that deleting them leaves free pages a VACUUM has to reclaim.

    Each embedding is 2 KB, so 400 faces is ~800 KB against SQLite's 4 KB page - about
    200 pages of freelist if nothing compacts. A handful of rows would leave a freelist of
    zero either way and the test would pass without the VACUUM.
    """
    for index in range(faces):
        plant(root, blob((float(index),) * 512), sha=f"sha-{index}")


def test_freelist_count_is_zero_after_a_successful_compaction(tmp_path):
    """A19's oracle. NOT file size, which is nondeterministic and can pass while the
    pages are still held."""
    root = archive(tmp_path)
    bulk(root)

    report = run_purge(root, confirm=yes)

    assert report.compacted is True
    assert report.freelist_after == 0
    conn = raw(root)
    try:
        assert freelist_count(conn) == 0
    finally:
        conn.close()


def test_freelist_count_is_ABOVE_ZERO_when_the_vacuum_is_skipped(tmp_path):
    """THE CONTROL, and without it the test above proves nothing.

    A freelist of 0 is worth exactly as much as the chance that it would have been 0
    anyway. This is the plan's own named mutation - "skip the `VACUUM`" - run as a test,
    so the oracle is measured to be able to tell the two apart before it is trusted.
    """
    root = archive(tmp_path)
    bulk(root)

    report = run_purge(root, confirm=yes, compaction=lambda conn: None)

    assert report.compacted is True  # the seam did not raise, so nothing failed
    assert report.freelist_after > 0, "the freelist oracle cannot see a skipped VACUUM"


def test_the_wal_is_checkpointed_to_nothing(tmp_path):
    """The other half of A19's oracle. `connect` runs in WAL mode, so the rows deleted a
    moment ago sit in `catalog.db-wal` until a checkpoint moves them - a VACUUM alone
    rewrites the main file and leaves them there."""
    root = archive(tmp_path)
    bulk(root)
    ws = workspace_if_exists(root)
    assert ws is not None
    wal = ws.db_path.with_name(ws.db_path.name + "-wal")

    run_purge(root, confirm=yes)

    assert not wal.exists() or wal.stat().st_size == 0


# ---------------------------------------------------------------------------
# 5. A FAILED COMPACTION AFTER A COMMITTED DELETE - A19's headline case.
# ---------------------------------------------------------------------------


def failing(_conn):
    raise sqlite3.OperationalError("disk I/O error")


def test_a_failed_compaction_reports_the_deletion_as_COMMITTED(tmp_path):
    """A19: "a failed compaction after a committed deletion must not read as nothing
    happened". The rows really are gone, and the report has to say so."""
    root = stocked(tmp_path)

    report = run_purge(root, confirm=yes, compaction=failing)

    assert report.deleted is True and report.faces == 2
    assert report.compacted is False
    assert "disk I/O error" in report.compaction_error
    assert count(root, "SELECT COUNT(*) FROM face_instances") == 0


def test_a_failed_compaction_exits_1_with_the_summary_printed_in_full(tmp_path, monkeypatch):
    """Spec 13: a command that recorded an error exits 1 and still prints its summary.

    Exit 0 here would tell a script everything went to plan while the space the user asked
    pam to reclaim is still held; a suppressed summary would hide the fact that the
    deletion succeeded.
    """
    # M14 Task 4 Step 1: the `faces` group's commands live in `pam/cli_faces.py`.
    import pam.cli_faces as cli_mod
    from pam.faces import purge as purge_mod

    root = stocked(tmp_path)
    # THE SEAM, AND ONLY THE SEAM. The real `run_purge` runs - real inventory, real
    # confirmation over real stdin, real deletion, real report, real exit-code mapping -
    # with `compaction` the one argument this test chooses. Patching `purge_mod.compact`
    # would not work and should not: it is a DEFAULT ARGUMENT, bound at definition, which
    # is what makes the shipped behaviour independent of import order.
    real = purge_mod.run_purge
    monkeypatch.setattr(
        cli_mod,
        "run_purge",
        lambda archive, *, confirm: real(archive, confirm=confirm, compaction=failing),
    )

    monkeypatch.setattr("builtins.input", lambda: "purge")
    result = CliRunner().invoke(cli, ["faces", "purge", "--archive", str(root)])

    assert result.exit_code == 1, result.output
    assert "Deleted (committed): 2 faces" in result.output
    assert "Compaction: FAILED" in result.output
    assert "disk I/O error" in result.output
    assert "IS committed" in result.output
    assert "pam faces purge again is the safe retry" in result.output


def test_the_retry_after_a_failed_compaction_compacts_and_deletes_nothing(tmp_path):
    """The retry, end to end: the second run finds nothing, asks nothing, and compacts."""
    root = archive(tmp_path)
    bulk(root)
    first = run_purge(root, confirm=yes, compaction=failing)
    assert first.deleted is True and first.compacted is False and first.freelist_after > 0

    second = run_purge(root, confirm=yes)

    assert second.deleted is False
    assert second.compacted is True
    assert second.freelist_after == 0


# ---------------------------------------------------------------------------
# 6. THE DISCLOSURE, and the LEAK IT MEASURES (A19, Step 4).
# ---------------------------------------------------------------------------

# A19's list, SPELLED FROM THE SPEC's own sentence rather than read off `DISCLOSURE`:
# "previously written exports, plans and reports and session logs already containing
# names, browser caches, and filesystem backups" plus "it states that pam has no import
# path". A list built from the production constant would agree with any value of it.
SPEC_DISCLOSURE_TOPICS = (
    "pam faces export",
    "no import path",
    "plans and reports",
    "session logs",
    "browser",
    "backups",
)


def test_the_disclosure_names_every_item_A19_lists(tmp_path, monkeypatch):
    root = stocked(tmp_path)

    monkeypatch.setattr("builtins.input", lambda: "purge")
    result = CliRunner().invoke(cli, ["faces", "purge", "--archive", str(root)])

    assert result.exit_code == 0, result.output
    for topic in SPEC_DISCLOSURE_TOPICS:
        assert topic in result.output, topic
    assert "cannot promise the bytes are physically gone" in result.output


def test_the_disclosure_list_is_not_empty_and_covers_the_spec_topics():
    """THE FLOOR (M769/M776). A list that can be emptied is a disclosure that disappears
    with the sentence it was supposed to make, and every assertion above it goes green."""
    assert len(DISCLOSURE) >= 5
    joined = "\n".join(DISCLOSURE)
    for topic in SPEC_DISCLOSURE_TOPICS:
        assert topic in joined, topic


def test_the_leak_is_MEASURED_and_the_sentence_states_the_measured_count(tmp_path, monkeypatch):
    """STEP 4: "measure the leak before writing the sentence".

    A plan item whose name was built from a person KEEPS that name after the purge - pam
    deletes its own records and does not rewrite plans. The count printed comes from the
    inventory read before the deletion; this test counts the same rows with its own SQL
    AFTER it, and asserts the two are the same number. A sentence about a leak nobody
    measured is the shape of reassurance this product does not ship.
    """
    root = stocked(tmp_path)
    _plant_people_plan(root, items=3)

    monkeypatch.setattr("builtins.input", lambda: "purge")
    result = CliRunner().invoke(cli, ["faces", "purge", "--archive", str(root)])

    assert result.exit_code == 0, result.output
    assert _people_items_still_there(root) == 3
    assert "3 plan item(s) in 1 plan(s)" in result.output
    assert "still carry the name" in result.output


def test_with_no_people_plan_the_leak_line_is_absent_rather_than_zero(tmp_path, monkeypatch):
    """The other direction, so the sentence above cannot be unconditional prose. A user
    with no such plan must not be told about a leak that is not there."""
    root = stocked(tmp_path)

    monkeypatch.setattr("builtins.input", lambda: "purge")
    result = CliRunner().invoke(cli, ["faces", "purge", "--archive", str(root)])

    assert "plan item(s) in" not in result.output
    assert "pam faces export" in result.output


def _plant_people_plan(root, *, items):
    """A plan whose items carry a `persons`-sourced token, with the suite's own INSERTs.

    THE TOKEN DOCUMENT IS SPELLED FROM A14 rather than produced by the planner: A19's
    inventory is explicitly independent of label TEXT and keys on `source_table`, so the
    fixture has to be able to plant a token whose text says nothing and whose provenance
    says everything.
    """
    conn = raw(root)
    try:
        plan = conn.execute(
            "INSERT INTO plans (created_at, status, template, config_json, app_version,"
            " schema_version) VALUES (?, 'open', '{date}_{people}', '{}', '0.1.0', 7)",
            ("2026-09-17T10:00:00",),
        ).lastrowid
        file_ids = [
            int(r["id"]) for r in conn.execute("SELECT id FROM files ORDER BY id").fetchall()
        ]
        tokens = json.dumps(
            [{"token": "people", "value": "zoe", "source_table": "persons", "source_id": 1}]
        )
        for index in range(items):
            conn.execute(
                "INSERT INTO plan_items (plan_id, file_id, old_rel_path,"
                " generated_filename, new_filename, tokens_json, tier, trust_score,"
                " trust_reasons_json) VALUES (?, ?, ?, ?, ?, ?, 'high', 90, '[]')",
                (
                    plan,
                    file_ids[index % len(file_ids)],
                    f"old_{index}.JPG",
                    f"2019-07-04_zoe_{index:03d}.JPG",
                    f"2019-07-04_zoe_{index:03d}.JPG",
                    tokens,
                ),
            )
        conn.commit()
        return int(plan)
    finally:
        conn.close()


def _people_items_still_there(root):
    """The same count as `repo.plan_items_with_people_tokens`, computed independently.

    The suite's own SQL and the suite's own JSON parse, so this is a SECOND
    implementation - not the production query asked twice.
    """
    conn = raw(root)
    try:
        rows = conn.execute("SELECT tokens_json FROM plan_items").fetchall()
    finally:
        conn.close()
    total = 0
    for row in rows:
        tokens = json.loads(str(row["tokens_json"]))
        if any(t.get("source_table") == "persons" for t in tokens):
            total += 1
    return total


# ---------------------------------------------------------------------------
# 7. NEITHER COMMAND TOUCHES A PHOTOGRAPH (Step 1), and pam CACHES NO CROP (A19's 5).
# ---------------------------------------------------------------------------


def test_purge_leaves_the_archive_tree_byte_for_byte_identical(tmp_path):
    """`tools/ht_manifest.py --exact` in-process: paths AND bytes, before and after."""
    root = stocked(tmp_path)
    before = {
        p.relative_to(root): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }

    run_purge(root, confirm=yes)

    after = {
        p.relative_to(root): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }
    assert after == before


def test_a_crop_request_writes_nothing_anywhere_under_the_workspace(tmp_path):
    """A19's DELETION 5, measured rather than asserted: pam keeps no crop to delete.

    THE REAL ROUTE, not a unit call - `webui/crop.py`'s claim is that "nothing here
    caches", and a cache would be written by the endpoint rather than by the cutter. So
    this drives `GET /faces/crop/{id}` over a live server and then walks the ENTIRE
    workspace, every file at every depth, comparing paths and bytes. `thumbs/` exists and
    is where a crop cache would plausibly land; a new file anywhere under `ws.root` fails.

    IF THIS EVER FAILS, the purge report's `_CROPS_LINE` is a lie and A19's fifth deletion
    needs building rather than declaring.
    """
    from test_webui_faces import Served, plant_face, sha_of
    from test_webui_faces import archive as jpeg_archive

    root = jpeg_archive(tmp_path)
    face_id = plant_face(root, sha_of(root, "IMG_1.JPG"))
    ws = workspace_if_exists(root)
    assert ws is not None

    def snapshot():
        return {
            p.relative_to(ws.root): p.stat().st_size
            for p in sorted(ws.root.rglob("*"))
            if p.is_file() and p.suffix not in (".log", ".db-wal", ".db-shm", ".lock")
        }

    before = snapshot()
    live = Served(root)
    try:
        status, headers, body = live.request("GET", f"/faces/crop/{face_id}")
        second, _headers, _body = live.request("GET", f"/faces/crop/{face_id}")
    finally:
        live.close()

    assert status == 200 and second == 200, body
    assert headers["Cache-Control"] == "no-store"
    assert snapshot() == before, "something wrote a crop to disk - A19's deletion 5 is real"


# ---------------------------------------------------------------------------
# 8. THE COMMAND.
# ---------------------------------------------------------------------------


def test_the_cli_cancels_on_anything_but_the_word_and_says_so(tmp_path, monkeypatch):
    root = stocked(tmp_path)

    monkeypatch.setattr("builtins.input", lambda: "y")
    result = CliRunner().invoke(cli, ["faces", "purge", "--archive", str(root)])

    assert result.exit_code == 0
    assert "Cancelled. Nothing was deleted." in result.output
    assert count(root, "SELECT COUNT(*) FROM face_instances") == 2


def test_the_summary_says_no_photograph_was_touched(tmp_path, monkeypatch):
    root = stocked(tmp_path)

    monkeypatch.setattr("builtins.input", lambda: "purge")
    result = CliRunner().invoke(cli, ["faces", "purge", "--archive", str(root)])

    assert "No photograph was read, written, renamed or deleted." in result.output
    assert "pam never stores one" in result.output


def test_the_command_refuses_an_archive_pam_has_never_scanned(tmp_path, capsys):
    """Through `main`, where spec 13's exit-code mapping lives."""
    unscanned = tmp_path / "elsewhere"
    unscanned.mkdir()

    assert main(["faces", "purge", "--archive", str(unscanned)]) == EXIT_EXPECTED

    assert "pam scan" in capsys.readouterr().err


def test_the_geometry_fixture_is_the_one_the_cluster_tests_use(tmp_path):
    """A canary for this module's imports rather than for the product: `plant` writes
    `GEOMETRY`, and a fixture change over there that silently emptied it would make half
    the counts above measure an archive with no faces in it."""
    assert GEOMETRY["bbox"] == [0.1, 0.2, 0.3, 0.4]


# ---------------------------------------------------------------------------
# THE WRITE-AHEAD LOG, ON THE PATH A19 EXISTS FOR (reviewer's F2, on M1951/M1952).
#
# Both checkpoint mutants SURVIVED a full suite: `wal_checkpoint(TRUNCATE)` weakened to
# `PASSIVE` (M1951) and the checkpoint deleted outright (M1952) were each green over 3699
# tests. Measured afterwards, on a catalog holding one distinctive label and 200 faces,
# with the `VACUUM` failing - which is exactly the state A19 makes the command REPORT:
#
#   TRUNCATE  ->  catalog.db-wal is 0 bytes, and the name is not in it
#   PASSIVE   ->  catalog.db-wal is 168,952 bytes, AND THE NAME IS IN IT
#   no checkpoint -> the same 168,952 bytes, and the name is in it
#
# So the mode is load-bearing precisely when compaction fails, and nothing measured it.
# On the SUCCESS path the three are indistinguishable (SQLite checkpoints on close), which
# is why every existing test passed under both mutants.
#
# THE ASSERTION IS ABOUT THE BYTES ON DISK, not about the pragma's text: a test that
# grepped `db.py` for the word TRUNCATE would pass over a checkpoint that never ran.
# There is no secure-erase promise here (A19 says so in as many words) - the main database
# still holds freed pages - but the write-ahead log is a separate file that purge can, and
# does, leave empty.
# ---------------------------------------------------------------------------

WAL_LABEL = "ZZWALPROBEPERSONZZ"


def wal_bytes(root):
    ws = workspace_if_exists(root)
    assert ws is not None
    wal = ws.db_path.with_name(ws.db_path.name + "-wal")
    return wal.read_bytes() if wal.exists() else b""


def test_a_purge_whose_compaction_FAILS_leaves_no_purged_name_in_the_write_ahead_log(
    tmp_path,
):
    """F2. The deletion is committed and the compaction failed - the state A19 reports on
    its own line - and the purged name must not still be sitting in `catalog.db-wal`.

    THE LOG IS READ WHILE PAM STILL HOLDS THE DATABASE, which is the whole difficulty and
    is why the first attempt at this test was vacuous: SQLite checkpoints on close, so a
    log read after `run_purge` returns is empty whatever the checkpoint did, and both
    mutants stayed green through it (M1951-R, M1952-R). Measured at this moment instead,
    with the connection open: TRUNCATE leaves 0 bytes, PASSIVE and no-checkpoint leave
    168,952 bytes with the name in them.

    The failure injected is real - `VACUUM` inside an open transaction is refused by
    SQLite itself - so `compact`'s own order runs: the checkpoint first, the vacuum after.
    """
    root = archive(tmp_path)
    person = plant_person(root, WAL_LABEL)
    for i in range(200):
        plant(root, VECTOR, sha=f"wal-{i}", person_id=person)
    seen = {}

    def failing_compaction(conn):
        conn.execute("BEGIN")
        try:
            compact(conn)
        finally:
            seen["wal"] = wal_bytes(root)

    report = run_purge(root, confirm=yes, compaction=failing_compaction)

    assert report.deleted is True, "the deletion must be committed before this is asked"
    assert report.compacted is False, "this test is about the FAILED compaction path"
    assert WAL_LABEL.encode() not in seen["wal"], (
        "a purged person's name is still in the write-ahead log after the purge"
    )
    assert len(seen["wal"]) == 0


def test_the_purged_name_really_is_in_the_log_until_the_checkpoint_clears_it(tmp_path):
    """The control, and without it the test above could pass over an archive that never
    wrote a log at all - a negative test passing for the wrong reason.

    Same moment, same open connection, but `compact` is not called: the log is read
    exactly as the purge left it, and it holds the name and 168 KB of pages.
    """
    root = archive(tmp_path)
    person = plant_person(root, WAL_LABEL)
    for i in range(200):
        plant(root, VECTOR, sha=f"wal-{i}", person_id=person)
    seen = {}

    def no_compaction_at_all(conn):
        seen["wal"] = wal_bytes(root)
        raise sqlite3.OperationalError("disk I/O error")

    run_purge(root, confirm=yes, compaction=no_compaction_at_all)

    assert len(seen["wal"]) > 0, "the purge left no log for the checkpoint to clear"
    assert WAL_LABEL.encode() in seen["wal"], (
        "the purged name never reached the log, so the test above proves nothing"
    )


# ---------------------------------------------------------------------------
# A READER STILL OPEN ACROSS THE PURGE (the M11 review's R1; design 10 rev 26, N7b).
#
# The two tests above drive a purge ALONE, and so did every other test in the repository.
# But `pam status`, `pam report` and `pam verify` open a read-only catalog handle WITHOUT
# the workspace lock, by design, and a handle holding a snapshot BLOCKS the checkpoint -
# which does not raise. SQLite answers `(1, 3, 0)` and carries on, `VACUUM` succeeds, and
# before N7b pam reported "compacted" over a log that still held the purged name: built by
# the review through `run_purge` itself, 181,312 bytes of WAL with WAL_LABEL in them.
#
# The REAL `compact` runs in all three tests below. The snapshot is taken the way a
# reader takes one - a transaction that has read a row - on the handle `pam status` uses.
# ---------------------------------------------------------------------------


def open_reader(root):
    ws = workspace_if_exists(root)
    assert ws is not None
    reader = connect_readonly(ws.db_path)
    reader.execute("BEGIN")
    reader.execute("SELECT count(*) FROM face_instances").fetchone()
    return reader


def test_a_purge_beside_an_open_reader_reports_that_compaction_FAILED(tmp_path):
    root = archive(tmp_path)
    person = plant_person(root, WAL_LABEL)
    for i in range(200):
        plant(root, VECTOR, sha=f"reader-{i}", person_id=person)
    reader = open_reader(root)
    try:
        report = run_purge(root, confirm=yes)
    finally:
        reader.close()

    assert report.deleted is True, "the deletion is committed whatever the reader does"
    assert report.compacted is False, (
        "a checkpoint blocked by an open reader was reported as a successful compaction"
    )
    assert report.compaction_error == CHECKPOINT_BLOCKED
    text = "\n".join(purge_lines(report))
    assert "Compaction: FAILED - another program is reading pam's catalog" in text
    assert "Close it, then run pam faces purge again" in text


def test_a_reader_that_opened_AFTER_the_deletion_still_fails_the_compaction(tmp_path):
    """The case that makes `busy` load-bearing ON ITS OWN. A snapshot taken after the
    deletion commits lets every frame checkpoint - SQLite answers `(1, n, n)`, log equal to
    checkpointed - but blocks the TRUNCATE, so `catalog.db-wal` keeps its bytes and the name
    in them. A check on `log != checkpointed` would call this a success."""
    root = archive(tmp_path)
    person = plant_person(root, WAL_LABEL)
    for i in range(200):
        plant(root, VECTOR, sha=f"late-{i}", person_id=person)
    seen = {}

    def a_reader_arrives_then_compact(conn):
        reader = open_reader(root)
        try:
            compact(conn)
        finally:
            seen["wal"] = wal_bytes(root)
            reader.close()

    report = run_purge(root, confirm=yes, compaction=a_reader_arrives_then_compact)

    assert report.compacted is False, "a blocked truncation was reported as a compaction"
    assert report.compaction_error == CHECKPOINT_BLOCKED
    assert WAL_LABEL.encode() in seen["wal"], "the construction no longer blocks anything"


def test_once_the_reader_has_gone_the_retry_compacts_and_the_log_holds_no_name(tmp_path):
    """A19's safe retry, driven: the second purge finds nothing, asks nothing, compacts."""
    root = archive(tmp_path)
    person = plant_person(root, WAL_LABEL)
    for i in range(200):
        plant(root, VECTOR, sha=f"retry-{i}", person_id=person)
    reader = open_reader(root)
    try:
        first = run_purge(root, confirm=yes)
    finally:
        reader.close()
    assert first.compacted is False
    seen = {}

    def real_compaction_then_look(conn):
        compact(conn)
        seen["wal"] = wal_bytes(root)

    def never_asked(_inventory, _root):
        raise AssertionError("a retry over an emptied catalog must ask nothing")

    retry = run_purge(root, confirm=never_asked, compaction=real_compaction_then_look)

    assert retry.compacted is True
    assert WAL_LABEL.encode() not in seen["wal"]


def test_the_control_WITHOUT_a_snapshot_the_same_purge_compacts(tmp_path):
    """The negative control: the same archive and the same handle, opened but holding no
    snapshot. Without it the test above could pass for a reason other than the reader -
    a compaction that failed for everybody would satisfy `compacted is False` too."""
    root = archive(tmp_path)
    person = plant_person(root, WAL_LABEL)
    for i in range(200):
        plant(root, VECTOR, sha=f"idle-{i}", person_id=person)
    ws = workspace_if_exists(root)
    assert ws is not None
    idle = connect_readonly(ws.db_path)
    try:
        report = run_purge(root, confirm=yes)
    finally:
        idle.close()
    assert report.compacted is True, report.compaction_error


# The cross-module anchor that stood here, under `tests/test_faces_round_trip.py`, M1959, was absorbed into
# `tests/census.py` at M12 Task 1 (m11.md condition C4): one hand-written inventory outside
# every guarded module, checked at collection by `conftest.py`.
