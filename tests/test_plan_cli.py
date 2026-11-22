import json
import sys
from pathlib import Path

from click.testing import CliRunner

from pam.cli import cli
from pam.naming.plan_report import print_plan_summary
from pam.naming.planner import PlanSummary
from pam.scan.runner import run_scan

FAKE_COMMAND = [sys.executable, str(Path(__file__).parent / "fake_exiftool.py")]


def scanned(tmp_path):
    # Uppercase extension on purpose (spec 5.4 rev 6): almost every file in a
    # a real-world library are uppercase, and this file had none - the blind spot
    # that let N1 ship all the way to a plan full of case-only "renames".
    root = tmp_path / "photos"
    root.mkdir()
    (root / "IMG_1.JPG").write_bytes(b"a")
    (root / "IMG_1.JPG.tags.json").write_text(
        json.dumps({"DateTimeOriginal": "2019:07:04 18:23:11"}), encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    return root


def test_cli_plan_happy_path(tmp_path):
    root = scanned(tmp_path)
    result = CliRunner().invoke(cli, ["plan", "--archive", str(root)])
    assert result.exit_code == 0, result.output
    assert "1 file" in result.output
    assert "2019-07-04_001.JPG" in result.output  # the extension keeps its case
    assert "Next:" in result.output


def test_cli_plan_reports_nothing_to_rename(tmp_path):
    root = scanned(tmp_path)
    CliRunner().invoke(cli, ["plan", "--archive", str(root)])
    (root / "IMG_1.JPG").rename(root / "2019-07-04_001.JPG")
    (root / "IMG_1.JPG.tags.json").rename(root / "2019-07-04_001.JPG.tags.json")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    result = CliRunner().invoke(cli, ["plan", "--archive", str(root)])
    assert result.exit_code == 0, result.output
    assert "already" in result.output.lower()


def test_cli_plan_bad_template_exits_one(tmp_path):
    from pam.cli import main

    root = scanned(tmp_path)
    assert main(["plan", "--archive", str(root), "--template", "{banana}"]) == 1


def test_cli_plan_without_a_scan_exits_one(tmp_path):
    from pam.cli import main

    assert main(["plan"]) == 1


def test_cli_plan_on_an_unscanned_folder_exits_one_and_creates_nothing(tmp_path, capsys):
    # N4 at the CLI boundary: spec 13 maps PamError to exit 1, and PAM_HOME must
    # be left untouched - an orphan workspace makes a typo'd --archive look like a
    # genuine all-conforming archive on the next command.
    import os

    from pam.cli import main

    home = Path(os.environ["PAM_HOME"])
    root = tmp_path / "never-scanned"
    root.mkdir()
    assert main(["plan", "--archive", str(root)]) == 1
    assert "has not scanned" in capsys.readouterr().err
    assert not home.exists() or list(home.rglob("*")) == []


def test_cli_plan_with_an_empty_template_exits_one(tmp_path):
    # N5: --template "" is a template with no tokens, which is an error, not a
    # request to fall back to the configured template.
    from pam.cli import main

    root = scanned(tmp_path)
    assert main(["plan", "--archive", str(root), "--template", ""]) == 1


def test_cli_plan_bare_resolves_via_recorded_pointer(tmp_path):
    # Clarification 9's headline behavior: after a scan, `pam plan` with no
    # --archive finds the archive on its own via the pointer scan left behind.
    scanned(tmp_path)
    result = CliRunner().invoke(cli, ["plan"])
    assert result.exit_code == 0, result.output
    assert "2019-07-04_001.JPG" in result.output


def test_print_plan_summary_pins_the_plan_exists_output(capsys):
    # M13-M18/M23: mutation testing found nine user-facing lines on this path
    # deletable with a green suite. Pin content, not just non-emptiness - the
    # existing "1 file" in result.output check is loose enough to satisfy the
    # known singular/plural bug too.
    summary = PlanSummary(
        total=10,
        planned=7,
        already_conforming=2,
        skipped_placeholders=1,
        tiers={"high": 5, "medium": 2},
        plan_id=42,
        template="{date}_{seq}",
        log_path=Path("C:/fake/log.txt"),
        errors=[
            "a.jpg: no free name after 500 tries",
            "b.jpg: no free name after 500 tries",
        ],
        error_files=3,  # one of the two dropped units was a live-photo pair
        examples=[("IMG_1.jpg", "2019-07-04_001.jpg")],
        left_behind=3,
        events=4,
    )
    print_plan_summary(summary)
    captured = capsys.readouterr()

    assert "Plan 42 is ready - nothing has been renamed yet." in captured.out
    # Finding 10: PlanSummary.total counts the files pam CAN name, not everything
    # the scan walked, so the bare "out of 10." had no honest reading.
    assert "  7 files would be renamed, out of 10 pam can name." in captured.out
    assert "2 already match the template." in captured.out
    assert "1 cloud-only files were skipped." in captured.out
    assert "  Grouped into 4 events by date and place." in captured.out
    assert "Confidence: 5 high, 2 medium." in captured.out
    # Finding 10: both warning lines carry the '!' symbol - UX spec section 2 says
    # colour is never the only signal, and NO_COLOR/non-TTY output has only this.
    assert (
        "  ! 3 other files share a name with something being renamed and will NOT "
        "be renamed with it - listed in the log." in captured.out
    )
    # ... and the failure line counts FILES, not the units the errors list holds.
    assert "  ! 3 files could not be given a name - see the log." in captured.out
    assert "IMG_1.jpg  ->  2019-07-04_001.jpg" in captured.out

    # The log path is a stderr-only line - a plain "in result.output" check
    # (mixed stdout+stderr) can't tell it apart from being on stdout.
    assert "Log:" not in captured.out
    assert "Log: C:\\fake\\log.txt" in captured.err


def test_print_plan_summary_nothing_to_rename_reports_placeholders(capsys):
    # Clarification 7: placeholders are excluded and reported with a reason,
    # on the early-return "nothing to rename" branch too.
    summary = PlanSummary(total=3, already_conforming=1, skipped_placeholders=2)
    print_plan_summary(summary)
    out = capsys.readouterr().out

    assert "Nothing to rename." in out
    assert "1 files already match the naming template." in out
    assert "2 cloud-only files were skipped" in out
    assert "pam never downloads them to read a date" in out
    # M9 Task 5 / B2: the whole line, not the command half. This assertion passed both
    # before and after the format converged, so on its own it says nothing about which
    # of the three treatments this branch prints - and the nothing-to-rename branch is
    # the one whose `Next:` was PROSE rather than a command plus a reason.
    assert "Next: pam scan <folder>   (if you have added photos since the last scan)" in out


def test_a_nothing_to_rename_run_still_reports_its_errors_and_its_log(capsys):
    # Finding 8 / spec 13 rev 6. Both lines used to live under
    # `if plan_id is not None`, so a run that dropped forty units rendered as
    # "Nothing to rename." with no error line and no log path - failure
    # summarized as success (vision principle 9), and UX section 2's "every
    # session ends with the log path on stderr" is unconditional.
    summary = PlanSummary(
        total=40,
        errors=["a.jpg: no free name after 999 tries"] * 40,
        error_files=40,
        log_path=Path("C:/fake/log.txt"),
    )
    print_plan_summary(summary)
    captured = capsys.readouterr()

    assert "Nothing to rename." in captured.out
    assert "  ! 40 files could not be given a name - see the log." in captured.out
    assert "Log: C:\\fake\\log.txt" in captured.err
    assert "Log:" not in captured.out  # stderr only, exactly as on the plan path


def test_an_ordinary_nothing_to_rename_run_still_prints_the_log_path(capsys):
    summary = PlanSummary(total=3, already_conforming=3, log_path=Path("C:/fake/log.txt"))
    print_plan_summary(summary)
    captured = capsys.readouterr()

    assert "Nothing to rename." in captured.out
    assert "could not be given a name" not in captured.out  # no errors, no line
    assert "Log: C:\\fake\\log.txt" in captured.err


def test_one_planned_file_is_singular(capsys):
    # Finding 10: "1 files would be renamed" was the shipped copy.
    print_plan_summary(PlanSummary(total=1, planned=1, plan_id=1))
    assert "  1 file would be renamed, out of 1 pam can name." in capsys.readouterr().out


def test_two_planned_files_are_plural(capsys):
    print_plan_summary(PlanSummary(total=9, planned=2, plan_id=1))
    assert "  2 files would be renamed, out of 9 pam can name." in capsys.readouterr().out


def test_cli_plan_with_a_dropped_unit_exits_one_and_still_prints_everything(
    tmp_path, monkeypatch
):
    # Finding 9 / spec 13 rev 6: a command that recorded per-file errors exits 1.
    # The whole summary still prints - only the exit code changes, so a script can
    # tell a complete plan from one missing files and a human loses nothing.
    from pam.cli import main

    root = scanned(tmp_path)
    monkeypatch.setattr("pam.naming.collision.assign_unique_stem", lambda *a, **k: None)
    result = CliRunner().invoke(cli, ["plan", "--archive", str(root)])
    assert result.exit_code == 1, result.output
    assert "Nothing to rename." in result.output
    assert "could not be given a name" in result.output
    assert main(["plan", "--archive", str(root)]) == 1


def test_a_clean_plan_run_still_exits_zero(tmp_path):
    root = scanned(tmp_path)
    assert CliRunner().invoke(cli, ["plan", "--archive", str(root)]).exit_code == 0


def test_the_plan_summary_names_the_events_it_grouped(capsys):
    # M8 Task 8 Step 5 / spec 6.3 rule 5b: `pam plan` is the command that groups, so
    # it is the command that reports it - not `pam status`, whose enrichment list is
    # spec 8.1's five enrichers and does not include events. UX section 2: numbers are
    # humanized. The count is EVENTS, not the files in them.
    print_plan_summary(PlanSummary(total=9, planned=9, plan_id=1, events=1234))
    assert "  Grouped into 1,234 events by date and place." in capsys.readouterr().out


def test_one_event_is_singular(capsys):
    print_plan_summary(PlanSummary(total=9, planned=9, plan_id=1, events=1))
    assert "  Grouped into 1 event by date and place." in capsys.readouterr().out


def test_an_archive_with_no_dated_files_says_nothing_about_events(capsys):
    # Zero prints nothing, matching every other count line on this summary. An
    # archive of undated scans has no events, and "Grouped into 0 events" is noise
    # about work that produced nothing.
    print_plan_summary(PlanSummary(total=9, planned=9, plan_id=1, events=0))
    assert "Grouped into" not in capsys.readouterr().out


def test_the_nothing_to_rename_summary_names_the_events_too(capsys):
    # Grouping is unconditional (spec 6.3 rule 5b), so a run that plans nothing still
    # regrouped the archive. Reporting it only when there are names to show would be
    # silent about the only work that run did.
    summary = PlanSummary(total=3, already_conforming=3, events=2)
    print_plan_summary(summary)
    out = capsys.readouterr().out
    assert "Nothing to rename." in out
    assert "  Grouped into 2 events by date and place." in out


def test_the_reported_event_count_reaches_the_console(tmp_path):
    # End to end, through the CLI: the number on screen comes from the run and not
    # from a constant. This fixture holds one dated photograph, which spec 8.3's
    # algorithm makes exactly one event - so the line is also the singular one, on a
    # real invocation rather than a hand-built summary.
    scanned(tmp_path)
    result = CliRunner().invoke(cli, ["plan"])
    assert result.exit_code == 0, result.output
    assert "Grouped into 1 event by date and place." in result.output


def test_a_number_held_by_a_ghost_row_is_EXPLAINED_and_not_merely_skipped(capsys):
    # C11 (m10.md). The behaviour was already correct and stays untouched; what was
    # missing was a sentence. At HT-M10 a `{seq}` number jumped `_001` -> `_002` for a
    # unit that looked alone on its date, and nothing pam printed could account for it -
    # the tester read `taken_names`' then-false docstring, correctly concluded the code
    # could not explain it, and logged it unexplained. Vision principle 9 forbids the
    # silence.
    summary = PlanSummary(
        total=9,
        planned=1,
        plan_id=7,
        blocked_by_missing=["2019/2019-07-04_001.jpg", "2019/2019-07-04_002.jpg"],
    )
    print_plan_summary(summary)
    out = capsys.readouterr().out
    # The '!' is UX spec 2's symbol: colour is never the only signal.
    assert (
        "  ! 2 numbers were skipped because pam's records still name files that are "
        "not on disk:" in out
    )
    # BOTH names, not a sample: the whole purpose of the line is to say WHICH number.
    assert "      2019/2019-07-04_001.jpg" in out
    assert "      2019/2019-07-04_002.jpg" in out
    assert "Those names are held so a rename cannot overwrite them." in out
    assert "no pam command clears these records." in out


def test_the_ghost_row_sentence_is_SINGULAR_when_one_number_was_skipped(capsys):
    # The one-file case is the case HT-M10 actually met, so it is the wording a real
    # user reads first. Both nouns move together - "1 numbers were ... files that are"
    # is the mutation a plural-only pin survives.
    summary = PlanSummary(
        total=9, planned=1, plan_id=7, blocked_by_missing=["2019-07-04_001.jpg"]
    )
    print_plan_summary(summary)
    out = capsys.readouterr().out
    assert (
        "  ! 1 number was skipped because pam's records still name a file that is "
        "not on disk:" in out
    )


def test_the_ghost_row_sentence_offers_NO_Fix_AND_NO_Run_because_none_exists(capsys):
    """Spec 7.5 rev 11: a finding no pam command can clear names no `Fix:` line.

    Nothing in v1.0 clears a ghost row. `m10.md`'s C11 guessed `pam forget`; it is NOT
    the route - spec 3 makes it a command that drops an archive POINTER, and it clears no
    `files` row at all. A `Fix:` naming it would send the user to a command that cannot
    do the job, which is worse than saying plainly that there is nothing to run.

    Both words are checked, and scoped to the block this line owns rather than to the
    whole summary: `pam plan`'s closing `Next:` steps legitimately name commands, and a
    whole-output `"Run:" not in out` would go red for the wrong reason the day one is
    rendered that way.
    """
    summary = PlanSummary(
        total=9, planned=1, plan_id=7, blocked_by_missing=["2019-07-04_001.jpg"]
    )
    print_plan_summary(summary)
    out = capsys.readouterr().out
    start = out.index("number was skipped")
    block = out[start : out.index("no pam command clears these records.")]
    assert "Fix:" not in block, block
    assert "Run:" not in block, block
    assert "pam forget" not in out


def test_no_ghost_rows_prints_no_ghost_row_LINE_AT_ALL(capsys):
    # The negative control the three above need: an unconditional line would satisfy
    # every one of them and would also appear on the overwhelming majority of archives,
    # which have no ghost rows at all.
    print_plan_summary(PlanSummary(total=9, planned=9, plan_id=1))
    out = capsys.readouterr().out
    assert "skipped because" not in out
    assert "not on disk" not in out


# ---------------------------------------------------------------------------
# THE DELETION FLOOR (reviewer's F2, on M1670 and on M1735).
#
# Measured by this review: deleting Task 6's four C11 copy tests left 2994 passed + 1 skipped, exit 0. One of them is the sole guardian of the emptiness rule (M1723) and one is the only pin in the repository on a RULING rather than on a behaviour.
#
# HAND-SPELLED, NEVER DERIVED FROM `globals()`. An inventory that asks the module what it
# contains passes the very mutation it exists to catch (`CLAUDE.md`'s `repo.ENRICHERS`
# lesson): delete the test and the name goes with it, and the two agree about less.
# ---------------------------------------------------------------------------

LOAD_BEARING_CONTROLS = {
    "test_a_number_held_by_a_ghost_row_is_EXPLAINED_and_not_merely_skipped": (
        "C11's copy, in full and in the plural. UX spec 2's `!` symbol (colour is "
        "never the only signal) and the rule that BOTH names are listed rather "
        "than sampled - the point of the line is to say WHICH number was skipped"
    ),
    "test_the_ghost_row_sentence_is_SINGULAR_when_one_number_was_skipped": (
        "the one-file case, which is the case HT-M10 actually met. Both nouns move "
        "together, so `1 numbers were ... files that are` is the mutation a "
        "plural-only pin survives"
    ),
    "test_the_ghost_row_sentence_offers_NO_Fix_AND_NO_Run_because_none_exists": (
        "spec 7.5 rev 11 - a finding no pam command can clear names no `Fix:` "
        "line - and the refusal of `m10.md` C11's own guess that `pam forget` is "
        "the route (spec 3: it drops an archive POINTER and clears no `files` "
        "row). NOTHING ELSE in the repository records that ruling"
    ),
    "test_no_ghost_rows_prints_no_ghost_row_LINE_AT_ALL": (
        "the negative control the three above need, and the SOLE guardian of the "
        "only-when-non-empty rule: measured, an unconditional print satisfies all "
        "three and fails only this one (M1723)"
    ),
}

assert LOAD_BEARING_CONTROLS, "this module's control list is empty; it protects nothing"


def test_no_ghost_row_control_in_this_file_can_be_deleted_without_saying_so():
    """The floor under this module's C11 inventory (reviewer's F2)."""
    present = {name for name in globals() if name.startswith("test_")}
    missing = sorted(set(LOAD_BEARING_CONTROLS) - present)
    assert missing == [], (
        f"a load-bearing control has been deleted from {Path(__file__).name}:"
        f" {missing}. Each is why another assertion here means anything"
        f" ({[LOAD_BEARING_CONTROLS[m] for m in missing]})."
    )
