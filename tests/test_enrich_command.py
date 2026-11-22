"""`pam enrich` - design 12's command, M9 Task 12.

Everything here drives the REAL command over an archive of REAL files on a real temp filesystem,
scanned by the real scanner through the fake exiftool, and enriched by the two enrichers
pam actually ships. `tests/test_enrich_runner.py` and `tests/test_enrich_gate.py` own the
runner's mechanics with stub enrichers; this file owns the things only the command has:
which enrichers it selects, the workspace lock, the session log, spec 13's exit code,
`[enrich] workers`, and the sentence a user gets for a name pam does not ship.

WHY THE UNSHIPPED-NAME TESTS ARE THE MOST IMPORTANT ONES HERE. Design 8.1 names five
enrichers and M9 ships two. UX 1.4 forbids a dead end, and the M9 plan forbids
`pam enrich caption` "even disabled" - so the failure mode this file exists to catch is
a helpful-looking message that offers a user three commands which cannot run for another
three milestones. The assertion is therefore about what the message does NOT say as much
as about what it does, and it is spelled from the two names that ship rather than by
subtracting one list from another.
"""

import ast
import hashlib
import json
import re
import shutil
import sqlite3
from pathlib import Path

import pytest
from archives import FAKE_COMMAND, catalog, put
from ollama_stub import generate_reply, scripted

from pam.catalog import repo
from pam.cli import cli, main
from pam.enrich import command as enrich_command
from pam.enrich import summary as enrich_summary
from pam.enrich.base import EnrichSummary
from pam.enrich.command import BUILDERS, run_enrich
from pam.enrich.summary import EnricherOutcome, EnrichSummaryReport
from pam.errors import PamError
from pam.lock import WorkspaceLock
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan

LIBRARY = Path(__file__).resolve().parent / "tiny_library"
# A picture Pillow can really decode, borrowed from the spec 9.1 library rather than
# generated here: `put`'s ordinary payload is a sentence with a `.JPG` on the end, which
# `phash` reads, fails to decode, and counts `failed` - correct behaviour and useless as
# the happy path.
REAL_PICTURE = LIBRARY / "phash_pattern_base.png"

# Design 8.1's `geocode` example is "has GPS", and GPS reaches the catalog through the
# capture row the scanner builds from exiftool's tags. Tokyo, so the place is legible in
# a failure message.
TOKYO_LAT, TOKYO_LON = 35.6895, 139.6917

# THE ENRICHERS PAM SHIPS AT M11, written out from design 14's milestone table ("M9 -
# Enricher runner + phash + geocode"; "M10 - Caption enricher"; "M11 - Faces") and from
# each milestone's own Task 8, NOT read back from `command.BUILDERS`. An expectation fetched from `BUILDERS`
# would agree with any value of it - this project's most repeated defect (M6's
# `ENRICHERS`, M5's `open_plan`, M8's partition oracles) - and the whole point of these
# tests is that the shipped set is exactly these three and not design 8.1's five.
# THE ORDER IS `BUILDERS`' ORDER AND IT IS ASSERTED AS A LIST BELOW. HT-M10's F1 was
# that map's insertion order being silently load-bearing for correctness.
SHIPPED = ("phash", "geocode", "caption", "faces", "ocr")

# The design 8.1 names that are NOT shipped yet. EMPTY SINCE M12 TASK 13, which shipped the
# last of design 8.1's five (`ocr`). Kept, empty and asserted empty below, so that the
# partition with SHIPPED stays checked; no test is parametrized over it any more - an empty
# parametrize is SKIPPED rather than failed (M743), so the tests that used to be now drive a
# name that is no design 8.1 enricher at all.
NOT_YET: tuple[str, ...] = ()

# A name that is NOT a design 8.1 enricher, for the refusal tests. A typo a user could make.
UNKNOWN = "capshun"

# THE SPLIT IS ASSERTED AT COLLECTION, and it is asserted by LENGTH as well as by
# membership (M9 Task 12 review, M1396/M1397). `NOT_YET` narrowed to `("caption",)` was
# green at 2602 - `faces` and `ocr` silently stopped being checked in all four tests that
# read this tuple - and `NOT_YET = ()` only made pytest SKIP the parametrized one, which
# is M7's M743 exactly. A collection-time assert catches the emptied form; only an exact
# length catches the narrowed one, so both are here.
#
# The union is design 8.1's five names spelled out, NOT `repo.ENRICHERS`: the whole point
# of this file is that the shipped set is a proper subset of that tuple. M10 Task 8 moved
# `caption` from one half to the other and BOTH lengths changed together - it was never
# deleted from here, which is exactly what this pair of asserts exists to force.
# M11 Task 8 moved `faces` across, and both lengths moved with it - 3/2 became 4/1. M12 Task
# 13 moved `ocr`, the last: 4/1 became 5/0.
assert len(SHIPPED) == 5 and len(NOT_YET) == 0, (
    "an enricher name was dropped rather than moved between SHIPPED and NOT_YET."
    f" SHIPPED={SHIPPED}, NOT_YET={NOT_YET}"
)
assert set(SHIPPED) | set(NOT_YET) == {"caption", "faces", "ocr", "phash", "geocode"}, (
    "SHIPPED and NOT_YET no longer partition design 8.1's five enrichers:"
    f" {sorted(set(SHIPPED) | set(NOT_YET))}"
)


def archive(tmp_path, name="photos", gps=True):
    """A scanned archive with one decodable picture and one file carrying GPS."""
    root = tmp_path / name
    root.mkdir()
    (root / "picture.png").write_bytes(REAL_PICTURE.read_bytes())
    _tags(root / "picture.png", {})
    (root / "located.png").write_bytes(REAL_PICTURE.read_bytes()[:-1] + b"\x00")
    _tags(
        root / "located.png",
        {"GPSLatitude": TOKYO_LAT, "GPSLongitude": TOKYO_LON} if gps else {},
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    return root


def _tags(path: Path, extra):
    tags = {"DateTimeOriginal": "2019:07:04 18:23:11", "PamTestSidecar": path.name}
    tags.update(extra)
    path.with_name(path.name + ".tags.json").write_text(json.dumps(tags), encoding="utf-8")


def facts(root):
    """Every live inferred fact in one archive's catalog, as (kind, model_name) pairs."""
    conn = catalog(root)
    try:
        rows = conn.execute(
            "SELECT kind, model_name FROM inferred_facts WHERE superseded_by IS NULL"
        ).fetchall()
    finally:
        conn.close()
    return sorted((row["kind"], row["model_name"]) for row in rows)


def outcome(report: EnrichSummaryReport, name: str) -> EnricherOutcome:
    return next(o for o in report.outcomes if o.name == name)


# --- which enrichers run ---------------------------------------------------------------


def test_pam_enrich_with_no_name_runs_every_enricher_pam_ships(tmp_path):
    """Design 12: `pam enrich [NAME]`, and NAME is optional. No name means all of them.

    Asserted against the SHIPPED literal above rather than against `BUILDERS`, and by
    listing the outcomes rather than counting them: a report with two `phash` outcomes
    and no `geocode` has the same length as the right answer.

    `caption` IS IN THE LIST AND HAS NO RESULT, which is amendment A2 seen from the
    outside: `[enrich.caption] enabled` ships `false`, so the captioner is reported and
    not run. "Reported" is the half that matters - an enricher missing from the report
    entirely would be indistinguishable from one nobody built.
    """
    root = archive(tmp_path)

    report = run_enrich(None, root)

    assert [o.name for o in report.outcomes] == list(SHIPPED)
    assert outcome(report, "phash").result is not None
    assert outcome(report, "geocode").result is not None
    assert outcome(report, "caption").result is None
    assert outcome(report, "caption").disabled == "[enrich.caption] enabled"
    # `faces` ships disabled for the same reason and reaches the report the same way
    # (M11 Task 8 Step 9). Two enrichers in the third state is what makes it a STATE
    # rather than a special case somebody wrote for the captioner.
    assert outcome(report, "faces").result is None
    assert outcome(report, "faces").disabled == "[enrich.faces] enabled"
    # And `ocr` (M12 Task 13), which ships `enabled = false` too (design 8.1 rev 17).
    assert outcome(report, "ocr").result is None
    assert outcome(report, "ocr").disabled == "[enrich.ocr] enabled"


def test_pam_enrich_writes_the_facts_of_both_enrichers(tmp_path):
    """The end-to-end claim: two enrichers, two kinds of fact, in one catalog.

    The kinds are `phash` and `place` - design 6.1's token is `{place}` and design 6.4's
    validation rule names `place`, so the enricher's NAME and its fact's KIND differ for
    `geocode` and this is the pairing a wiring mistake would swap.
    """
    root = archive(tmp_path)

    run_enrich(None, root)

    # Two pictures, so two `phash` facts; one of them carries GPS, so one `place`.
    assert facts(root) == [
        ("phash", "dhash"),
        ("phash", "dhash"),
        ("place", "rg_cities1000"),
    ]


def test_pam_enrich_with_a_name_runs_only_that_enricher(tmp_path):
    root = archive(tmp_path)

    report = run_enrich("geocode", root)

    assert [o.name for o in report.outcomes] == ["geocode"]
    assert facts(root) == [("place", "rg_cities1000")]


# --- the names pam does NOT ship -------------------------------------------------------


def test_pam_enrich_refuses_a_name_pam_does_not_ship_and_names_all_five(tmp_path):
    """UX 1.4 and the M9 plan: a name pam does not ship is refused, and the refusal offers
    exactly what runs. Until M12 Task 13 this was parametrized over `NOT_YET` (design 8.1's
    unshipped names); that tuple is now empty, so it drives a name that is no enricher at all.

    THE REFUSAL IS REFUSED BEFORE ANY STATE IS TOUCHED, which is why this needs no
    archive at all: an unknown name is a fact about the command line, not about a
    catalog, so it cannot depend on resolving one. And the sentence names the two
    enrichers that RUN today - never the five design 8.1 lists - because a message that
    offered `caption` would be the dead end dressed as guidance, three milestones early.
    """
    with pytest.raises(PamError) as refusal:
        run_enrich(UNKNOWN, tmp_path / "nowhere")

    message = str(refusal.value)
    for shipped in SHIPPED:
        assert shipped in message, f"the refusal does not name {shipped}, which works today"


def test_the_refusal_offers_nothing_a_later_milestone_owns(tmp_path):
    """The other half, and the one a helpful edit would break.

    Driven with a name that is not any design 8.1 enricher, so that the three names
    below cannot appear merely because the user typed one of them - the refusal echoes
    the name it was given, and echoing is not offering. What is asserted is that the
    OFFER is exactly the shipped pair.
    """
    with pytest.raises(PamError) as refusal:
        run_enrich(UNKNOWN, tmp_path / "nowhere")

    message = str(refusal.value)
    for later in NOT_YET:
        assert later not in message, (
            f"the refusal offers {later!r}, which no milestone before M10 can run - UX"
            " 1.4's dead end, and the M9 plan forbids it by name"
        )
    # With every design 8.1 enricher shipped (M12 Task 13), the OFFER is exactly SHIPPED, in
    # `BUILDERS`' order - a widened or reordered offer fails here, not only a later name.
    assert f"Today pam enrich understands: {', '.join(SHIPPED)}." in message, message


def test_no_enricher_a_later_milestone_owns_is_registered(tmp_path):
    """The registry itself, not merely the message it produces.

    A row that exists and refuses is exactly the "even disabled" shape the plan forbids,
    and it would not be visible through the refusal test above - `_selected` would find
    the key and hand it to `_run_one`. Compared as a SET OF KEYS against the literal, in
    both directions, so an unshipped addition fails as loudly as a deletion.
    """
    assert set(BUILDERS) == set(SHIPPED)
    for later in NOT_YET:
        assert later not in BUILDERS


def test_the_refusal_ends_with_a_command_that_really_exists(tmp_path):
    """The M4 standing rule at the one NEW message that carries a `Run:` line.

    `pam verify`'s fix lines have been checked against click's own command table since M6
    (`test_verify.py::_assert_fix_is_a_real_command`) because a line naming a command that
    does not exist is a dead end dressed as guidance. This message had no such check, and
    `Run: pam enrichers (with no name) ...` was green across the whole suite (M9 Task 12
    review, M1394) - the sentence whose entire job is to end with something that WORKS.

    The command word is read out of the sentence the user is actually shown, so a typo, a
    rename, or a milestone promise fails here.
    """
    with pytest.raises(PamError) as refusal:
        run_enrich("capshun", tmp_path / "nowhere")

    message = str(refusal.value)
    named = re.search(r"Run: pam ([a-z][a-z-]*)", message)
    assert named, f"the refusal names no command to run at all: {message!r}"
    assert named.group(1) in cli.commands, (
        f"the refusal tells the user to run `pam {named.group(1)}`, which is not a pam"
        f" command. Commands that exist: {sorted(cli.commands)}"
    )


def test_pam_enrich_help_names_no_enricher_at_all(tmp_path, capsys):
    """`pam enrich --help`. NAME is a free argument, so click lists no choices - and it
    must stay that way: a `click.Choice` would print the shipped pair on every help
    screen, which is fine, but a `click.Choice` over design 8.1's five would print three
    commands that cannot run. This pins the surface, not the intention."""
    assert main(["enrich", "--help"]) == 0
    text = capsys.readouterr().out
    for later in NOT_YET:
        assert later not in text


# --- spec 13's exit codes --------------------------------------------------------------


def test_pam_enrich_exits_zero_when_nothing_failed(tmp_path, capsys):
    root = archive(tmp_path)

    assert main(["enrich", "--archive", str(root)]) == 0
    assert "could not be read" not in capsys.readouterr().out


def test_a_file_pam_cannot_decode_exits_one_and_still_prints_the_whole_summary(
    tmp_path, capsys
):
    """Spec 13 rev 6: a command that RECORDED per-file errors exits 1, not 0 - and rev 12,
    which requires the summary line to name where the errors are listed.

    `pam report skipped` is the SCANNER's register and holds none of these (the file was
    read perfectly well; Pillow could not make a picture out of it), so the log is the
    honest pointer and the assertion is that the line says so.
    """
    root = archive(tmp_path)
    put(root, "not_really_a_picture.png")
    run_scan(root, exiftool_command=FAKE_COMMAND)

    code = main(["enrich", "phash", "--archive", str(root)])

    text = capsys.readouterr().out
    assert code == 1
    assert "1 files could not be read" in text
    assert "listed in the log" in text
    # The run still completed and the other two pictures were hashed. "Record, count,
    # continue, summarize" is about the WORK; only the exit code changed. Asserted from
    # the catalog rather than from the summary's own arithmetic - a summary that
    # miscounted would satisfy a phrase match against itself.
    assert facts(root) == [("phash", "dhash"), ("phash", "dhash")]


def test_a_refresh_newer_than_the_installed_model_is_a_message_and_not_a_traceback(
    tmp_path, capsys
):
    """`run_enricher` raises `ValueError` for this; `errors.py` was not in Task 7's file
    list, so the translation into spec 13's exit 1 is this command's job.

    Exit 1 with the reason on stderr, never exit 2 with a traceback: a version string on
    a command line is an ordinary mistake, and `cli.main`'s `except Exception` is for the
    ones nobody predicted.
    """
    root = archive(tmp_path)

    code = main(
        ["enrich", "phash", "--refresh-older-than-model", "9.0", "--archive", str(root)]
    )

    captured = capsys.readouterr()
    assert code == 1
    assert "Traceback" not in captured.err
    assert "newer than" in captured.err


def test_an_enricher_that_cannot_run_is_a_sentence_and_a_reason_to_exit_one(
    tmp_path, monkeypatch, capsys
):
    """`available()`'s contract is "a missing package is a sentence, never a crash" - and
    the sentence still means the user did not get what they asked for, so it exits 1.

    Faked at the enricher rather than by uninstalling Pillow, and asserted on BOTH halves:
    the reason reaches the screen, and the next step named is a command that exists.
    """

    class Unavailable:
        name = "phash"
        model_name = "dhash"
        model_version = "1.0"

        def available(self):
            return False, "Pillow is not installed"

        def cost_estimate_ms(self):
            return 1

        def wants(self, file):
            return False

        def enrich(self, file, image):
            return []

    root = archive(tmp_path)
    monkeypatch.setitem(
        BUILDERS, "phash", lambda conn, r, cfg, log: (Unavailable(), lambda f: None)
    )

    code = main(["enrich", "phash", "--archive", str(root)])

    text = capsys.readouterr().out
    assert code == 1
    assert "phash did not run: Pillow is not installed" in text
    assert "pam doctor" in text
    assert facts(root) == []


# --- the archive-scoped machinery every command owes -----------------------------------


def test_pam_enrich_refuses_a_folder_pam_has_never_scanned(tmp_path, capsys):
    root = tmp_path / "unscanned"
    root.mkdir()

    code = main(["enrich", "--archive", str(root)])

    assert code == 1
    assert "has not scanned" in capsys.readouterr().err


def test_pam_enrich_will_not_run_while_another_pam_command_holds_the_lock(tmp_path):
    """The single-process lock is what stops two `pam enrich` processes resuming ONE
    `enrichment_runs` row: `open_run` hands the newest unfinished run to whoever asks, so
    two processes would share a cursor and each would walk past the other's work."""
    root = archive(tmp_path)
    ws = workspace_if_exists(root)

    with WorkspaceLock(ws.lock_path):
        with pytest.raises(PamError):
            run_enrich("geocode", root)


def test_pam_enrich_reads_the_worker_count_from_pam_toml(tmp_path, monkeypatch):
    """Design 11's `[enrich] workers`, validated since M9 Task 7 and read by NOTHING
    until this command - the obligation Task 7's stage 1 flagged.

    The runner is intercepted rather than observed indirectly, because the number of
    threads a pool used is not visible in any output: a hard-coded `workers=4` would
    satisfy every other test in this file, which is exactly how an unread config key
    stays unread.
    """
    root = archive(tmp_path)
    workspace_if_exists(root).config_path.write_text(
        "[enrich]\nworkers = 7\n", encoding="utf-8"
    )
    seen = []
    real = enrich_command.run_enricher

    def spy(conn, enricher, loader, *, workers, **kw):
        seen.append(workers)
        return real(conn, enricher, loader, workers=workers, **kw)

    monkeypatch.setattr(enrich_command, "run_enricher", spy)

    run_enrich(None, root)

    assert seen == [7, 7], "the configured worker count did not reach the runner"


# --- design 8.1's resume, and the run row nothing else reads ---------------------------


def _unfinished_runs(root):
    conn = catalog(root)
    try:
        return conn.execute(
            "SELECT enricher, cursor_file_id FROM enrichment_runs WHERE finished_at IS NULL"
        ).fetchall()
    finally:
        conn.close()


def test_a_second_run_resumes_the_unfinished_one_rather_than_opening_another(tmp_path):
    """Design 8.1's resume, driven through the command, and the evidence behind Step 4's
    decision that `pam verify` gets no check for an unfinished `enrichment_runs` row.

    An interrupted run leaves exactly ONE open row per enricher identity - `open_run`
    returns it and `run_enricher` adopts it instead of calling `start_run` - so the state
    is bounded at one row and is cleared by the next `pam enrich` of that enricher. There
    is no unbounded accumulation for a checker to find, and nothing about the row is
    wrong: it is the resume mechanism, working.
    """
    root = archive(tmp_path)
    conn = catalog(root)
    try:
        run_id = repo.start_run(conn, "geocode", "rg_cities1000", "1.0", "2026-08-28T00:00:00")
        repo.set_run_cursor(conn, run_id, 1)
        conn.commit()
    finally:
        conn.close()
    assert len(_unfinished_runs(root)) == 1

    run_enrich("geocode", root)

    assert _unfinished_runs(root) == [], (
        "the killed run was not adopted and closed - a second open row for one identity"
        " would be the unbounded state Step 4 decided pam verify does not need to watch"
    )


def test_an_unfinished_enrichment_run_is_not_a_pam_verify_finding(tmp_path):
    """STEP 4'S DECISION, WRITTEN AS A TEST RATHER THAN AS A SENTENCE.

    `pam verify` gets NO new check for `enrichment_runs`, and this is the state that
    argued for one: a run killed and never resumed, which no reader outside
    `catalog/repo/enrichment.py` can see today. The decision is that this is not a
    finding, for the reason the M9 plan gives for refusing a split-unit check - a check
    can only be right about a state that is WRONG, and an open run is not wrong. It is
    indistinguishable from a run the user is about to resume, so a checker would have to
    report every interrupted run for ever with no way to tell the two apart, and rev 11's
    `Fix:` rule would then be satisfied by `pam enrich` on an archive where nothing is
    broken. `pam verify` must stay silent here, and this pins that it does.
    """
    root = archive(tmp_path)
    conn = catalog(root)
    try:
        repo.start_run(conn, "phash", "dhash", "1.0", "2026-08-28T00:00:00")
        conn.commit()
    finally:
        conn.close()

    from pam.verify import run_verify

    summary = run_verify(root)

    assert summary.exit_code == 0, [
        (c.key, c.name, c.details) for c in summary.results if c.status != "pass"
    ]


# --- what pam verify DOES cover, now that there are rows to cover ----------------------


def test_verify_reports_an_enrichment_fact_whose_file_is_gone_as_inert(tmp_path):
    """Spec 7.5 check 2, second half, reaching a REAL fact for the first time.

    Until M9 nothing wrote `inferred_facts`, so check 2b stood guard over an empty table.
    A `phash` fact is keyed by content hash precisely so it survives the file leaving and
    coming back, and the spec's word for that is *inert, not an error* - `warn`, exit 1,
    and a report rather than damage.
    """
    root = archive(tmp_path)
    run_enrich("phash", root)
    # THE FILE IS EDITED, NOT DELETED, and that is the realistic route to this state as
    # well as the only legal one: `files` rows are never deleted (a `capture` row's
    # foreign key refuses it, and spec 7.0 J2's reasoning is the same), and a file that
    # merely goes missing keeps its row and its sha256 - "a missing file's cache is not
    # stale, it is waiting", says `inert_fact_rows` itself. Editing the bytes moves the
    # file's sha256, and the fact keyed to the OLD content is then a cache for content
    # pam can no longer see. Which is precisely what check 2b is for.
    (root / "picture.png").write_bytes((LIBRARY / "phash_pattern_far.png").read_bytes())
    run_scan(root, exiftool_command=FAKE_COMMAND)

    from pam.verify import run_verify

    summary = run_verify(root)
    inert = next(c for c in summary.results if c.key == "2b")

    assert inert.status == "warn" and inert.count == 1
    # AND IT IS THE RIGHT ROW, not merely one row (M9 Task 12 review, M1399). This archive
    # holds two enriched pictures and only one of them was edited, so `count == 1` is
    # satisfied by the INVERTED predicate too - `WHERE EXISTS` reports the file pam can
    # still see, and this test could not tell the two apart. The two hashes are computed
    # from the bytes this fixture wrote, never read back from the catalog the check reads.
    gone = hashlib.sha256(REAL_PICTURE.read_bytes()).hexdigest()
    still_here = hashlib.sha256(REAL_PICTURE.read_bytes()[:-1] + b"\x00").hexdigest()
    assert len(inert.details) == 1, inert.details
    assert gone[:12] in inert.details[0], (
        f"check 2b did not name the content that went away ({gone[:12]}): {inert.details}"
    )
    assert still_here[:12] not in inert.details[0], (
        "check 2b named content the catalog still holds - the predicate is inverted"
    )
    assert summary.exit_code == 1, "an inert fact is a report, never an error (spec 7.5)"


def test_verify_walks_a_supersede_chain_a_real_refresh_wrote(tmp_path, monkeypatch):
    """Spec 7.5 check 5 over rows `--refresh-older-than-model` really produced.

    Check 5's own docstring says the table "is empty on any archive pam has built so
    far"; this is the first task that can make it not be. The refresh is driven the way a
    user drives it - a newer model version installed, then the flag naming the old one -
    so the chain under test is the one the product creates rather than one this test
    inserted by hand.
    """
    root = archive(tmp_path)
    run_enrich("phash", root)
    monkeypatch.setattr(enrich_command.PhashEnricher, "model_version", "2.0")

    report = run_enrich("phash", root, refresh_older_than_model="2.0")

    assert outcome(report, "phash").result.superseded == 2
    conn = catalog(root)
    try:
        chained = conn.execute(
            "SELECT COUNT(*) AS n FROM inferred_facts WHERE superseded_by IS NOT NULL"
        ).fetchone()["n"]
    finally:
        conn.close()
    assert chained == 2

    from pam.verify import run_verify

    summary = run_verify(root)
    chains = next(c for c in summary.results if c.key == "5")
    assert chains.status == "pass" and chains.count == 0


# --- the closing block -----------------------------------------------------------------


def test_pam_enrich_ends_with_the_next_step_and_the_log_path(tmp_path, capsys):
    """UX 2: every command but `pam verify` ends with a `Next:` block, and every session
    ends with its log path on stderr.

    The reason is the sentence `pam/report.py` and `pam scan` already print for `pam
    plan` - UX 3 rev 9's rule that two surfaces naming one command must not describe it
    differently - and it is spelled here rather than fetched from `status.next_steps`.
    """
    root = archive(tmp_path)

    main(["enrich", "--archive", str(root)])

    captured = capsys.readouterr()
    assert "Next: pam plan   (propose new names - renames nothing)" in captured.out
    assert captured.err.strip().startswith("Log: ")


def test_the_session_log_records_what_the_run_did(tmp_path):
    """Spec 13: per-file errors and what the command did go to the log, not only to the
    screen. The summary points the user at the log for its failures, so the log has to
    have them - a pointer at an empty file is the worst of both."""
    root = archive(tmp_path)
    put(root, "not_really_a_picture.png")
    run_scan(root, exiftool_command=FAKE_COMMAND)

    report = run_enrich("phash", root)

    text = report.log_path.read_text(encoding="utf-8")
    assert "enrich start" in text
    assert "not_really_a_picture.png failed" in text


# --- the summary's own arithmetic ------------------------------------------------------


def test_had_errors_counts_a_dropped_fact_and_not_a_conflicting_one():
    """The exit-code predicate, driven directly over hand-built summaries.

    `facts_dropped` means pam refused its OWN enricher's output - the file was read and
    its result thrown away for failing design 6.4's validation - and reporting exit 0
    over that is the subset-as-whole failure vision principle 9 names. `facts_conflicted`
    means the identity already held that fact, which is what a re-run over unchanged
    bytes is SUPPOSED to look like. The two are one field apart and pull opposite ways,
    so they are asserted apart rather than through a run that could produce either.
    """

    def report(**kw):
        base = dict(
            run_id=1,
            total=1,
            walked=1,
            enriched=1,
            skipped_cached=0,
            skipped_wants=0,
            skipped_duplicate=0,
            skipped_no_sha=0,
            failed=0,
            facts_written=1,
            facts_dropped=0,
            facts_conflicted=0,
            superseded=0,
            cursor_file_id=1,
        )
        base.update(kw)
        return EnrichSummaryReport(
            archive_root=Path("x"),
            outcomes=[EnricherOutcome(name="phash", result=EnrichSummary(**base))],
        )

    assert report().had_errors is False
    assert report(failed=1).had_errors is True
    assert report(facts_dropped=1).had_errors is True
    assert report(facts_conflicted=1).had_errors is False
    assert (
        EnrichSummaryReport(
            archive_root=Path("x"),
            outcomes=[EnricherOutcome(name="phash", refusal="Pillow is not installed")],
        ).had_errors
        is True
    )


def test_the_geocode_loader_refuses_to_be_asked_for_a_picture():
    """`geocode` reads the catalog's GPS columns and never opens a file, so the loader it
    is handed is one that says so. A `pil_image_loader` here would import Pillow for a run
    that decodes nothing, and would silently start working if `wants()` ever changed."""
    with pytest.raises(AssertionError):
        enrich_command._no_image(None)


def test_nothing_in_the_command_reaches_the_database_with_its_own_sql():
    """The no-SQL-outside-`catalog/repo` rule, at the one new module. `guards.py` sweeps
    the package; this is the local statement, because a command that resolves an archive
    and opens a connection is the module most tempted to run one query of its own."""
    source = Path(enrich_command.__file__).read_text(encoding="utf-8")
    for statement in ("SELECT ", "INSERT ", "UPDATE ", "DELETE "):
        assert statement not in source, f"{statement.strip()} appears in enrich/command.py"
    assert isinstance(sqlite3.Connection, type)  # the import is a type, never a cursor


def test_the_tiny_library_picture_this_file_leans_on_is_really_decodable():
    """The control. Every happy path above depends on `phash` succeeding, and it would
    fail identically if this fixture were unreadable - `failed` counts, `facts_written`
    stays 0, and half this file would be asserting about the wrong thing."""
    from PIL import Image

    assert REAL_PICTURE.exists()
    with Image.open(REAL_PICTURE) as image:
        image.load()
        assert image.size[0] > 0


def test_the_fixture_archive_really_carries_gps(tmp_path):
    """The other control, for the same reason: `geocode.wants()` is "has GPS", so a
    sidecar whose coordinates never reached the capture row would make every geocode
    assertion above pass by wanting nothing."""
    root = archive(tmp_path)
    conn = catalog(root)
    try:
        assert len(repo.gps_by_file_id(conn)) == 1
    finally:
        conn.close()


def test_pam_enrich_refuses_an_archive_it_cannot_see(tmp_path, capsys):
    """Spec 7.0 J9: recovery must never run against an archive pam cannot see, and this
    command runs recovery. An absent tree reads as every file having gone missing, so the
    refusal comes out of `resolve_archive` before anything opens a catalog - which is why
    this command adds no `is_dir()` check of its own. The property is pinned from where a
    user meets it rather than at the line that would implement it."""
    root = archive(tmp_path)
    shutil.rmtree(root)

    code = main(["enrich", "--archive", str(root)])

    assert code == 1
    assert "pam cannot see this folder, so it will not touch anything" in (
        capsys.readouterr().err
    )


def test_the_front_door_names_every_enricher_pam_ships():
    """`m9.md` C4: `pam.enrich`'s own docstring says every enricher imports its interface
    from there, and until M10 Task 4 the package named the interface and not one of the
    enrichers that satisfy it.

    The expectation is `SHIPPED` - design 14's milestone table, spelled at the top of this
    module - and NOT `dir(enrich)`, so a re-export deleted here fails rather than being
    agreed with. `CaptionEnricher` arrives with M10 Task 8 and moves `caption` from
    `NOT_YET` into `SHIPPED`, which is what will bring it under this assertion.
    """
    import pam.enrich as enrich_pkg

    exported = {}
    for attr in dir(enrich_pkg):
        candidate = getattr(enrich_pkg, attr)
        name = getattr(candidate, "name", None)
        if isinstance(candidate, type) and isinstance(name, str):
            exported[name] = attr
    assert set(exported) == set(SHIPPED), (
        f"the enrichers reachable from `pam.enrich` are {sorted(exported)}, and the"
        f" enrichers pam ships are {sorted(SHIPPED)}"
    )
    for name in NOT_YET:
        assert name not in exported, f"{name} is re-exported before it is shipped"


# --- M10 Task 11: the captioner, seen from the command ---------------------------------
#
# WHAT THIS SECTION OWNS AND WHAT IT DOES NOT. `tests/test_caption.py` owns the enricher:
# the prompt, the four layers of a reply, spec 6.4's validation, `available()`. What only
# the COMMAND has is the paragraph a user reads - amendment A2's disabled sentence, the
# summary's skip counters, and the dropped-results line spec 13 rev 12 governs - with the
# exit code that goes with each. Every one of them drives `cli.main` with the words a user
# types, against the stub Ollama, because a summary line is a thing you can only get wrong
# on screen.
#
# THE TWO DISABLED SENTENCES ARE SPELLED OUT HERE, BY HAND, from amendment A2 and UX 2's
# rule that a refusal which does not say what to do next is a dead end. Not read back from
# `command.DISABLEABLE`, which would agree with any value of it, and not assembled out of
# `Config`'s attribute names.
CAPTION_OFF_LINE = "caption: not run - [enrich.caption] enabled is false in pam.toml."
CAPTION_ON_LINE = "  Set [enrich.caption] enabled = true to turn it on."


def caption_archive(tmp_path, toml: str, *, decoys=False) -> Path:
    """A scanned archive of decodable pictures, with `pam.toml` written before enrich.

    `decoys` adds the files that make the OTHER skip counters non-zero: a byte-for-byte
    copy of the first picture (`identical to another file`) and TWO videos (`not
    applicable`, because `wants()` is an allowlist of images and vision R2 is why). The
    names are ordered so the model's scripted replies land on known files with one worker.

    TWO VIDEOS AND ONE DUPLICATE, WHICH IS NOT A ROUNDING CHOICE (M10 Task 11 review,
    M1655). With one of each, `not applicable` and `identical to another file` both read
    `1` - and swapping the two operands in `_outcome_lines`' format string was a SURVIVOR
    across the whole suite. A summary compared whole still cannot see a right number in
    the wrong clause while two clauses carry the same number, so the four counters are
    given four values no two of which are equal: 0 / 2 / 1 / 5.
    """
    root = tmp_path / "photos"
    root.mkdir()
    for index, name in enumerate(("a_one.png", "b_two.png")):
        # The last byte is the IEND chunk's CRC, which Pillow does not verify: every copy
        # decodes and every copy has its own sha256, so two pictures are two
        # content-addressed facts rather than one shared one.
        (root / name).write_bytes(REAL_PICTURE.read_bytes()[:-1] + bytes([index]))
        _tags(root / name, {})
    if decoys:
        (root / "c_dup.png").write_bytes((root / "a_one.png").read_bytes())
        _tags(root / "c_dup.png", {})
        put(root, "d_clip.mp4")
        put(root, "e_clip.mov")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    ws = workspace_if_exists(root)
    assert ws is not None
    ws.config_path.write_text(toml, encoding="utf-8")
    return root


@pytest.mark.parametrize("argv", [["enrich"], ["enrich", "caption"]])
def test_a_disabled_captioner_names_the_key_AND_the_file_and_exits_zero(tmp_path, capsys, argv):
    """AMENDMENT A2 AND UX 2, ON BOTH ROUTES A USER CAN TAKE TO IT.

    `pam enrich` runs every enricher and `pam enrich caption` names one; the captioner
    ships off, so both must produce the same paragraph and both must exit 0. Parametrized
    because the two go through different branches of `_selected` - the named route was the
    only one under test after Task 8, and a disabled row dropped from the all-enrichers
    report would have been invisible there.

    WHAT THE PARAGRAPH OWES, from UX 2: not merely that the captioner did not run, but the
    two facts that let a user change the answer - THE KEY and THE FILE. A sentence naming
    `[enrich.caption] enabled` with no `pam.toml` in it sends a user hunting for where
    settings live, which is UX 1.4's dead end in a politer form.

    Driven with NO `pam.toml` at all, because that is what a default install is.
    """
    root = caption_archive(tmp_path, "")

    code = main([*argv, "--archive", str(root)])

    text = capsys.readouterr().out
    assert code == 0, "a configuration the user chose is not a per-file error (A2)"
    assert CAPTION_OFF_LINE in text
    assert CAPTION_ON_LINE in text
    # The file is named, and the key is spelled the way it is typed - `key = value`, which
    # is what the user will paste.
    assert "pam.toml" in text
    assert "[enrich.caption] enabled = true" in text
    # A2's other half: no `!`, and no diagnostic route for a setting nothing is wrong with.
    assert "pam doctor" not in text
    assert "!" not in text


def test_a_caption_run_reports_read_stored_the_four_skips_and_the_DROPPED_results(
    tmp_path, capsys
):
    """STEP 3, and the line this milestone exists to get right.

    One archive, three files, one caption run, and the counters exercised at values a
    hard-coded zero could not produce:

    * two pictures are READ and the model answers both;
    * the first answer is good and is STORED;
    * the second is well-formed JSON that is not a caption - a five-word `label` - so
      spec 6.4's write-time validation DROPS it (amendment A3: dropped, never truncated);
    * a text file is not an image, so `wants()` refuses it and it is `not applicable`.

    THE DROPPED LINE IS WHAT SPEC 13 REV 12 GOVERNS: "every summary line that mentions
    per-file errors must name where they are listed". `pam report skipped` is the
    SCANNER's register and holds none of these, so the log is the only honest pointer -
    and the run exits 1, because reporting exit 0 over a result pam threw away is the
    subset-as-whole failure vision principle 9 names. A malformed model answer is exactly
    the cause `facts_dropped` was built for, and this is the first test to read the
    sentence it produces.

    The lines are compared WHOLE rather than probed for substrings: a summary is read, and
    the failure mode is a right number in the wrong clause.
    """
    with scripted(
        generate_reply("Birthday Cake", "A child blowing out candles."),
        generate_reply("one two three four five", "Five words is not a label."),
    ) as stub:
        root = caption_archive(
            tmp_path,
            f"[enrich.caption]\nenabled = true\nollama_port = {stub.port}\n",
            decoys=True,
        )
        code = main(["enrich", "caption", "--archive", str(root)])

    text = capsys.readouterr().out
    lines = text.splitlines()
    assert code == 1, "a dropped result is a reason to exit 1 (spec 13 rev 12)"
    assert "caption: 2 of 10 files read, 1 results stored." in lines
    assert (
        "  skipped: 0 already done, 2 not applicable, 1 identical to another file,"
        " 5 never read from disk." in lines
    )
    assert (
        "  ! 1 results were rejected as malformed and not stored - listed in the log." in lines
    )
    # And the place that sentence points at really holds the reason, per file, by name. A
    # pointer at a file the reason is not in is the worst of both (spec 13), and it is the
    # half a screen-only assertion cannot see.
    ws = workspace_if_exists(root)
    assert ws is not None
    log = sorted(ws.logs_dir.glob("*_enrich.log"))[-1].read_text(encoding="utf-8")
    assert "dropped a fact for b_two.png" in log
    assert "5 words, over the 3 allowed" in log


def test_a_second_caption_run_reports_the_cached_skip_and_exits_zero(tmp_path, capsys):
    """The counter the test above could not reach, and the control for its zero.

    `skipped: 0 already done` is what a first run says; only a SECOND run over unchanged
    bytes can show that clause carrying a number, and a first-run-only test would pass for
    an implementation that printed a literal 0 there. This is also spec 8.1's zero-work
    property seen through the summary, and it is asserted AT THE STUB - the model is asked
    nothing the second time - rather than inferred from the count the summary prints.
    """
    with scripted(generate_reply("Birthday Cake", "A child blowing out candles.")) as stub:
        root = caption_archive(
            tmp_path, f"[enrich.caption]\nenabled = true\nollama_port = {stub.port}\n"
        )
        assert main(["enrich", "caption", "--archive", str(root)]) == 0
        asked_first = stub.paths().count("/api/generate")
        capsys.readouterr()

        code = main(["enrich", "caption", "--archive", str(root)])
        asked_again = stub.paths().count("/api/generate") - asked_first

    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert (asked_first, asked_again) == (2, 0), (
        "the second run asked the model again; a caption is cached by content identity"
    )
    assert "caption: 0 of 4 files read, 0 results stored." in lines
    assert (
        "  skipped: 2 already done, 0 not applicable, 0 identical to another file,"
        " 2 never read from disk." in lines
    )


def test_the_two_CONDITIONAL_summary_lines_no_run_in_this_suite_prints(capsys):
    """FINDING F2 (M10 Task 11 review, M1666/M1667). The two lines nothing rendered.

    `_outcome_lines` prints seven kinds of line and this suite drove five of them. Both
    `  N older results replaced by this run.` and `  N results were already stored and
    kept as they are.` could be DELETED OUTRIGHT with 2,932 tests green - they are
    reached only when a `--refresh-older-than-model` run supersedes, or when an identity
    already holds the fact, and no test in the milestone printed either.

    The COUNTERS behind them are pinned (`test_enrich_runner.py`'s supersede rows, and
    `test_had_errors_counts_a_dropped_fact_and_not_a_conflicting_one` above), which is
    exactly what made this invisible: a number can be right in the catalog and never
    reach the screen. A summary line is a thing you can only get wrong on screen.

    Driven over a hand-built summary rather than through a run, for the reason the
    `had_errors` test above gives: `superseded` and `facts_conflicted` pull opposite ways
    and a run that produced one would not produce the other. The paragraph is compared
    WHOLE and spelled out by hand from design 13 - not probed for substrings, and not
    read back from `_outcome_lines`.
    """
    summary = EnrichSummary(
        run_id=1,
        total=9,
        walked=9,
        enriched=4,
        skipped_cached=1,
        skipped_wants=2,
        skipped_duplicate=3,
        skipped_no_sha=5,
        failed=6,
        facts_written=4,
        facts_dropped=7,
        facts_conflicted=8,
        superseded=9,
        cursor_file_id=1,
    )
    report = EnrichSummaryReport(
        archive_root=Path("x"),
        outcomes=[EnricherOutcome(name="caption", result=summary)],
    )
    report.elapsed_s = 1.25

    lines = enrich_summary.enrich_lines(report)

    # Every count is a different number, so a line rendering the wrong field is a wrong
    # NUMBER and not merely a wrong word (M1655's lesson, applied at construction).
    assert lines[2:9] == [
        "caption: 4 of 9 files read, 4 results stored.",
        "  skipped: 1 already done, 2 not applicable, 3 identical to another file,"
        " 5 never read from disk.",
        "  9 older results replaced by this run.",
        "  8 results were already stored and kept as they are.",
        "  ! 6 files could not be read - skipped and listed in the log.",
        "  ! 7 results were rejected as malformed and not stored - listed in the log.",
        "",
    ], lines
    # Design 13: the two `!` lines are the ones that mention per-file errors, and both
    # name where they are listed. The two lines above them carry no `!` - a replaced
    # result and a kept one are what a re-run is SUPPOSED to look like.
    assert not any(line.startswith("  !") for line in lines[4:6])


# UX 3 REV 9'S SENTENCE FOR `pam plan`, QUOTED FROM THE SPEC: "pam scan prints the reason
# pam report's renderer already uses for that exact command (`propose new names - renames
# nothing`) rather than inventing a third wording: two surfaces naming the same command
# must not describe it differently."
UX3_PLAN_WHY = "propose new names - renames nothing"
# Every module that sends a user to `pam plan` today. Written out so that a fifth surface
# has to be added here deliberately, and so the search below cannot degenerate to zero
# sites and still pass.
# M14 Task 5a (C5-1): `pam scan`'s closing block moved from `scan/runner.py` to `scan/console.py`.
PLAN_STEP_MODULES = ("enrich/summary.py", "report.py", "scan/console.py", "status.py")


def test_every_surface_that_sends_a_user_to_pam_plan_says_the_same_sentence():
    """STEP 5, and UX 3 rev 9's rule stated as a test rather than as a convention.

    `pam enrich`'s closing block is load-bearing rather than tidy from M10 onwards:
    captions change proposed names, and a user who enriches and does not re-plan sees
    nothing change. The M8 review measured THREE treatments of `Next:` across four
    modules, so the failure mode here is not a missing line - four tests already pin that
    per surface - but a FOURTH WORDING appearing in one of them.

    THE SENTENCE IS THE ORACLE AND IT IS QUOTED FROM UX 3 ABOVE. Rendering the four
    surfaces and comparing them to EACH OTHER would prove only that they agree, which is
    M8's M922/M924; each is compared to the spec's words. The reasons are read out of the
    source because two of the four are inline literals rather than importable constants -
    a structural read, with `PLAN_STEP_MODULES` as its floor so a regex that stopped
    matching fails instead of passing over nothing.
    """
    package = Path(enrich_command.__file__).resolve().parent.parent
    found: dict[str, set[str]] = {}
    for relative in PLAN_STEP_MODULES:
        source = (package / relative).read_text(encoding="utf-8")
        reasons = set(re.findall(r'NextStep\("pam plan", "([^"]*)"\)', source))
        reasons |= set(re.findall(r'"Next: pam plan   \(([^)]*)\)"', source))
        assert reasons, f"{relative} no longer names `pam plan` in any shape this reads"
        found[relative] = reasons

    assert set(found) == set(PLAN_STEP_MODULES)
    for relative, reasons in found.items():
        assert reasons == {UX3_PLAN_WHY}, (
            f"{relative} describes `pam plan` as {sorted(reasons)}; UX 3 rev 9 says two"
            f" surfaces naming one command must not describe it differently, and the"
            f" sentence is {UX3_PLAN_WHY!r}"
        )


# C8's CUT, AS A STRUCTURE (M13 Task 3 review, M2418/M2419). A pure move is invisible to every
# behaviour test: a re-export through `command.py`, or a stale duplicate left behind in it, prints
# the same bytes today and drifts tomorrow. The plan's words are "one definition, no re-export".
# SPELLED FROM THE PLAN'S C8 TABLE plus the maintainer's 2026-10-09 widening (the three constants, and
# `_progress` renamed `progress`) - never read from `summary.py`'s own namespace.
C8_MOVED = frozenset(
    {
        "EnricherOutcome",
        "EnrichSummaryReport",
        "progress",
        "_eta",
        "enrich_lines",
        "_outcome_lines",
        "_stored",
        "print_enrich_summary",
        "NOT_RUN_REFUSALS",
        "PLAN_TIME_RESULTS",
        "RESULT_NOUNS",
    }
)
# The maintainer's ruling: `command.py` imports exactly these from `summary.py`, one direction only.
C8_COMMAND_IMPORTS = frozenset({"EnricherOutcome", "EnrichSummaryReport", "progress"})


def _top_level_definitions(tree):
    names = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _imported_from(tree, module):
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == module
        for alias in node.names
    }


def test_c8s_moved_names_have_one_definition_and_no_re_export():
    repo_root = Path(enrich_command.__file__).resolve().parents[2]
    enrich_dir = repo_root / "pam" / "enrich"
    summary_path = enrich_dir / "summary.py"
    # The positive half: every moved name IS defined in summary.py.
    summary_tree = ast.parse(summary_path.read_text(encoding="utf-8"))
    assert _top_level_definitions(summary_tree) >= C8_MOVED
    # ONE DEFINITION: no other enrich module, nor `cli.py`, defines a moved name again. Scoped to
    # where the names lived and are consumed: `progress` is a common name elsewhere in pam
    # (`webui/faces_api.py`), and `scan/runner.py` has its own `_progress`.
    neighbours = [p for p in sorted(enrich_dir.glob("*.py")) if p != summary_path]
    neighbours.append(repo_root / "pam" / "cli.py")
    assert enrich_dir / "command.py" in neighbours and len(neighbours) > 10
    for path in neighbours:
        defined = _top_level_definitions(ast.parse(path.read_text(encoding="utf-8")))
        duplicated = defined & (C8_MOVED | {"_progress"})
        assert not duplicated, f"{path.name} defines {sorted(duplicated)} again"
    # NO RE-EXPORT: nothing in pam/ or tools/ imports a moved name through command.py.
    sources = sorted((repo_root / "pam").rglob("*.py")) + sorted(
        (repo_root / "tools").glob("*.py")
    )
    assert summary_path in sources and len(sources) > 50, "the walk stopped reaching pam/"
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        re_exported = _imported_from(tree, "pam.enrich.command") & C8_MOVED
        assert not re_exported, f"{path.name} imports {sorted(re_exported)} from command.py"
    # The maintainer's ruling: command.py imports exactly three names from summary.py.
    command_tree = ast.parse((enrich_dir / "command.py").read_text(encoding="utf-8"))
    assert _imported_from(command_tree, "pam.enrich.summary") == C8_COMMAND_IMPORTS
