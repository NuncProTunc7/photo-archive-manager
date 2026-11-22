"""The confirmation, the closing summary and the session report (UX 4, spec 13).

COPY IS NORMATIVE HERE in a way it is not anywhere else in M5. UX 4 calls `pam apply`
"the one dangerous moment", and every line of its block was chosen: the archive path is
second because the counts are meaningless if the folder is wrong (UX rev 4, finding F4),
the deletion promise is stated in the negative because that is the fear people actually
have, and the undo promise is INDEFINITE because the journal has no expiry. So these
tests assert whole lines, in order, not substrings - a test that matches loosely would
pass while the one screen the user reads before renaming their archive said something
else.

The one thing they cannot assert: the confirmation reads stdin, and pytest has no
console. `builtins.input` is patched here, which proves the branching but not the
console behaviour. HT-M5 on a real PowerShell is what proves that (CLAUDE.md's HT-01
gotcha: console-only bugs pass green under pytest).
"""

import ast
import inspect
import json
import logging

import pytest
from archives import (
    FAKE_COMMAND,
    approve_and_apply,
    approve_archive,
    catalog,
    item_statuses,
    open_plan,
    planned,
)

from pam.catalog import repo
from pam.cli import cli, main
from pam.errors import EXIT_EXPECTED, EXIT_INTERNAL
from pam.execute import apply as apply_mod
from pam.execute import reporter
from pam.execute.apply import ApplySummary
from pam.execute.undo import UndoSummary
from pam.naming.planner import run_plan
from pam.paths import workspace_if_exists
from pam.scan.runner import run_scan

LOGGER = logging.getLogger("tests.reporter")


def always(answer):
    """A confirmation stub. The real one reads stdin; these drive the branch."""
    return lambda _confirmation: answer


def low_tier_archive(tmp_path):
    """A file with no date anywhere: named `undated_001`, tier `low` (spec 6.3.4)."""
    root = tmp_path / "photos"
    root.mkdir(parents=True)
    (root / "IMG_9.JPG").write_bytes(b"a photo with no date at all")
    (root / "IMG_9.JPG.tags.json").write_text(json.dumps({}), encoding="utf-8")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    summary = run_plan(root)
    assert summary.plan_id is not None
    return root, summary.plan_id


def confirmation_for(root, plan_id):
    """The block `pam apply` would print, captured without applying anything."""
    seen = []

    def capture(confirmation):
        seen.append(confirmation)
        return False

    approve_and_apply(plan_id, root, confirm=capture)
    assert len(seen) == 1
    return seen[0]


# --- UX 4's confirmation block, line by line --------------------------------------


def test_the_confirmation_block_is_exactly_ux_fours_copy(tmp_path):
    """Every line, in order, for the ordinary case: no low-tier items, no sync client."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])

    lines = reporter.confirmation_lines(confirmation_for(root, plan_id))

    assert lines == [
        f"Plan {plan_id} - ready to apply",
        f"  {root}",
        "  2 renames approved   (2 proposed, 0 skipped by you, 0 already conforming)",
        "  0 files will be deleted, moved, or modified - renames only, in place.",
        "  Every change is journaled. Undo restores exact original names.",
        "",
    ]


def test_the_archive_path_is_the_second_line_and_is_absolute(tmp_path):
    """UX rev 4 / finding F4, and the reason it is normative: it is the fact the user
    must check FIRST, because every count below it is meaningless if the folder is
    wrong. Printed exactly as `resolve_archive` returned it - never shortened, never
    relativized, because `Photos` could be any of five folders."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])

    lines = reporter.confirmation_lines(confirmation_for(root, plan_id))

    assert lines[1] == f"  {root}"
    assert root.is_absolute()
    assert str(root) in lines[1]


def test_the_low_confidence_line_appears_only_when_the_plan_holds_low_tier_items(
    tmp_path,
):
    """The tier gate's stand-in until M7's review UI exists. A plan pam is sure about
    must not carry a warning the user learns to ignore."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    assert not any(
        line.startswith("  ! ")
        for line in reporter.confirmation_lines(confirmation_for(root, plan_id))
    )

    low_root, low_plan = low_tier_archive(tmp_path / "low")

    lines = reporter.confirmation_lines(confirmation_for(low_root, low_plan))

    assert "  ! 1 of these are low confidence - pam is unsure about the name." in lines


def test_the_sync_warning_names_the_provider_when_one_is_detected(tmp_path):
    """A human test's library was inside OneDrive, so this line is live, not
    hypothetical: a sync client can lock a file mid-rename or re-create one at a path
    pam just vacated."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    confirmation = confirmation_for(root, plan_id)

    with_sync = reporter.confirmation_lines(
        reporter.Confirmation(**{**vars(confirmation), "sync_provider": "OneDrive"})
    )

    assert "  ! OneDrive detected: consider pausing sync while applying." in with_sync
    assert not any("detected" in line for line in reporter.confirmation_lines(confirmation))


def test_both_warnings_appear_together_after_a_blank_line(tmp_path):
    """The warning block is separated from the counts, and neither warning suppresses
    the other - a plan can be both low-confidence and inside a sync folder, which is
    exactly a real-world library."""
    root, plan_id = low_tier_archive(tmp_path)
    confirmation = confirmation_for(root, plan_id)

    lines = reporter.confirmation_lines(
        reporter.Confirmation(**{**vars(confirmation), "sync_provider": "OneDrive"})
    )

    assert lines[-3] == ""
    assert lines[-2] == "  ! 1 of these are low confidence - pam is unsure about the name."
    assert lines[-1] == "  ! OneDrive detected: consider pausing sync while applying."


# --- the typed confirmation itself -------------------------------------------------


def test_typing_apply_proceeds(tmp_path, monkeypatch, capsys):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    confirmation = confirmation_for(root, plan_id)
    monkeypatch.setattr("builtins.input", lambda: "apply")

    assert reporter.confirm_apply(confirmation) is True

    printed = capsys.readouterr().out
    assert printed.endswith('Type "apply" to proceed, anything else to cancel: ')


@pytest.mark.parametrize("typed", ["APPLY", "Apply", "yes", "y", "", " apply", "apply "])
def test_anything_other_than_exactly_apply_cancels(tmp_path, monkeypatch, typed):
    """Case-sensitive and exact, deliberately. The ritual IS the point: a confirmation
    that accepts `y` is one a user completes without reading, and this is the only
    moment pam asks permission before renaming an irreplaceable archive."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    confirmation = confirmation_for(root, plan_id)
    monkeypatch.setattr("builtins.input", lambda: typed)

    assert reporter.confirm_apply(confirmation) is False


def test_a_closed_stdin_cancels_rather_than_proceeding(tmp_path, monkeypatch):
    """`pam apply </dev/null` must not rename an archive. The safe default on EOF is
    the only defensible one, and a script that meant to apply can say so."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    confirmation = confirmation_for(root, plan_id)

    def eof():
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)

    assert reporter.confirm_apply(confirmation) is False


def test_cancelling_writes_nothing_at_all(tmp_path):
    """The whole promise of the confirmation. Not one journal row, not one status
    change, not one file moved - and exit 0, because cancelling is not an error."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])

    summary = approve_and_apply(plan_id, root, confirm=always(False))

    assert summary.cancelled
    assert not summary.had_errors
    assert (summary.renamed, summary.failed) == (0, 0)
    conn = catalog(root)
    try:
        assert repo.session_rows(conn, summary.session_id) == []
        # F1 (M13 Task 1 Step 9): the items are `approved` because the TEST approved them
        # (`approve_and_apply`, the suite's own SQL) - the cancel changed none of them. The
        # cancelled `--all` case, where nobody approved anything, is
        # `test_apply_approval.py::test_cancelling_all_writes_no_approval`.
        assert set(item_statuses(conn, plan_id).values()) == {"approved"}
        assert repo.plan_status(conn, plan_id) == "open"
    finally:
        conn.close()
    # Not even a report file: "nothing was written" has to mean nothing, and a report
    # for a session that never happened would be a record of work pam did not do.
    assert summary.report_path is None
    ws = workspace_if_exists(root)
    assert list(ws.reports_dir.glob("*.md")) == []
    assert sorted(p.name for p in root.iterdir() if "tags" not in p.name) == [
        "IMG_1.JPG",
        "IMG_2.JPG",
    ]


def test_confirming_applies_the_plan(tmp_path):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])

    summary = approve_and_apply(plan_id, root, confirm=always(True))

    assert not summary.cancelled
    assert summary.renamed == 1
    assert (root / "2019-07-04_001.JPG").exists()


# --- the closing summary (UX 4) ---------------------------------------------------


def test_a_clean_run_closes_with_ux_fours_copy(tmp_path):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    summary = approve_and_apply(plan_id, root)

    lines = reporter.apply_summary_lines(summary)

    assert lines[0] == f"Done. 2 renamed, 0 failed, in {summary.elapsed_s:.1f}s."
    assert lines[1:3] == [
        "  Every rename was journaled. Undo is available anytime - it does not",
        "  expire - as long as the files aren't renamed outside pam.",
    ]
    assert lines[3] == f"  Report: {summary.report_path}"
    assert lines[4] == f"  Undo:   pam undo {summary.session_id}"
    assert len(lines) == 5


def test_failures_change_the_headline_and_are_never_buried(tmp_path):
    """UX 4: `Done with problems.` - the count of failures is in the first line, not
    in a detail block the user can skip."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    (root / "2020-08-05_001.JPG").write_bytes(b"in the way")
    summary = approve_and_apply(plan_id, root)

    lines = reporter.apply_summary_lines(summary)

    assert lines[0] == (
        f"Done with problems. 1 renamed, 1 failed (details in report),"
        f" in {summary.elapsed_s:.1f}s."
    )


# The FOUR console headlines that render `elapsed_s`, one parameter each: apply and undo,
# each with and without failures. ONE PER CALL SITE ON PURPOSE (M258's lesson): the fix
# for HT-M6's H6 was applied at four places, and a single test driving one of them would
# let the other three drift back to `:.0f` in silence. A lambda per site rather than a
# summary object per site, so a site that stopped being called at all fails here too.
ELAPSED_HEADLINES = [
    pytest.param(
        lambda elapsed: reporter.apply_summary_lines(
            ApplySummary(renamed=2, elapsed_s=elapsed)
        )[0],
        id="apply-clean",
    ),
    pytest.param(
        lambda elapsed: reporter.apply_summary_lines(
            ApplySummary(
                renamed=1,
                failed=1,
                failures=[("IMG_2.JPG", "something was in the way")],
                elapsed_s=elapsed,
            )
        )[0],
        id="apply-with-problems",
    ),
    pytest.param(
        lambda elapsed: reporter.undo_summary_lines(UndoSummary(restored=2, elapsed_s=elapsed))[
            0
        ],
        id="undo-clean",
    ),
    pytest.param(
        lambda elapsed: reporter.undo_summary_lines(
            UndoSummary(
                restored=1,
                failed=1,
                failures=[("IMG_2.JPG", "something was in the way")],
                elapsed_s=elapsed,
            )
        )[0],
        id="undo-with-problems",
    ),
]


@pytest.mark.parametrize("headline_at", ELAPSED_HEADLINES)
def test_a_sub_second_run_prints_a_truthful_duration_at_every_console_site(headline_at):
    """HT-M6's H6. `in 0s` is what the console said for any run under half a second -
    the ORDINARY case on a small archive, and visually identical to the `took 0.0s`
    defect M6 had just fixed. The value was always right; only the rendering lied.

    0.4 rather than 0.04, so a truthful render and a rounded one differ in the DIGIT and
    not merely in a trailing zero: `:.0f` gives `in 0s`, `:.1f` gives `in 0.4s`.
    """
    headline = headline_at(0.4)

    assert "in 0.4s" in headline, headline
    assert "in 0s" not in headline, headline


@pytest.mark.parametrize("headline_at", ELAPSED_HEADLINES)
def test_a_whole_second_still_carries_its_decimal_at_every_console_site(headline_at):
    """The other direction, so `:.0f` cannot survive anywhere by rounding to the same
    text: at exactly 24 seconds - UX 4's own example - the two formats read `in 24s` and
    `in 24.0s`. The deviation from that example copy is deliberate and recorded in
    `reporter.py`'s docstring: one session's one number is printed one way."""
    headline = headline_at(24.0)

    assert "in 24.0s" in headline, headline


def _elapsed_format_specs():
    """Every format spec `reporter.py` applies to an `elapsed_s` value, read with `ast`.

    A REAL PARSER RATHER THAN A TEXT SCAN, and that is not pedantry here: the module
    docstring above those sites now discusses `:.0f` in prose, so a grep would report a
    rendering that does not exist. CLAUDE.md's hand-rolled-parser lesson in miniature -
    where a test reads a *format*, the format's own parser is the oracle.
    """
    specs = []
    for node in ast.walk(ast.parse(inspect.getsource(reporter))):
        if not isinstance(node, ast.FormattedValue):
            continue
        if "elapsed_s" not in ast.unparse(node.value):
            continue
        spec = node.format_spec
        specs.append(
            ""
            if spec is None
            else "".join(part.value for part in spec.values if isinstance(part, ast.Constant))
        )
    return specs


def test_every_elapsed_rendering_in_the_module_is_one_decimal():
    """The M7 Task 2 review's finding (mutation M539), and the structural half of H6.

    `ELAPSED_HEADLINES` above drives the four console sites one at a time, which is
    M258's lesson - but NOTHING tied that list to the sites themselves. Point one of the
    four lambdas at the wrong summary builder (they are four near-identical copies, so
    this is the ordinary copy-paste slip) and that site is silently uncovered; revert it
    to `:.0f` and the entire suite stays green. Measured: 1256 passed with an undo
    headline printing `in 0s` again.

    So the requirement is asserted against the module rather than against the tests:
    EVERY place `reporter.py` renders an elapsed value renders it the same way, whether
    or not a test happens to drive it, and whether it goes to the console or into the
    written report. Spelled from the requirement - one decimal, project-wide - and not
    from the production format string, which is the whole point of the rule the
    `repo.ENRICHERS` survivor left behind.

    The floor keeps it from passing on an empty walk: UX 4's closing line for apply and
    for undo, each with and without failures, is four, and the report's `took` line is
    the fifth.
    """
    specs = _elapsed_format_specs()

    assert len(specs) >= 4, f"the walk found too few elapsed renderings to be believed: {specs}"
    assert set(specs) == {".1f"}, specs


def test_a_stranded_file_is_named_exactly_and_not_folded_into_a_count(
    tmp_path, allow_split_unit
):
    """The one outcome in the summary that needs a human rather than a re-run. A count
    would tell the user something is wrong without telling them which of their photos
    it is, and the file is not lost - it is under its new name while its group is not."""
    _root, _plan_id, summary = _stranded_apply(tmp_path)

    lines = reporter.apply_summary_lines(summary)

    assert summary.stranded == ["2019-07-04_001.JPG"]
    assert "  ! Needs your attention - these files are under their new names" in lines
    assert "      2019-07-04_001.JPG" in lines


def _stranded_apply(tmp_path):
    """Apply a companion unit whose revert is blocked by something outside pam.

    Does the apply itself, so the logging filter is REMOVED again in a `finally`. An
    earlier version left it attached to the shared 'pam' logger for the rest of the
    session: it fires on any message starting with `renamed: IMG_1.JPG`, which most
    tests in the suite produce, and it would then write a file into a previous test's
    temp directory. Nothing went red - which is the whole point. It is the same shape
    as the sanction leak conftest's two tripwires exist to catch, and a test helper is
    just as capable of it as a fixture.
    """
    root = tmp_path / "photos"
    root.mkdir()
    for name in ("IMG_1.JPG", "IMG_1.MOV"):
        (root / name).write_bytes(f"the only copy of {name}".encode())
        (root / f"{name}.tags.json").write_text(
            json.dumps({"DateTimeOriginal": "2019:07:04 18:23:11"}), encoding="utf-8"
        )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_id = run_plan(root).plan_id
    (root / "2019-07-04_001.MOV").write_bytes(b"in the way")

    class TakeTheOldNameBack(logging.Filter):
        def filter(self, record):
            if record.getMessage().startswith("renamed: IMG_1.JPG"):
                (root / "IMG_1.JPG").write_bytes(b"something else entirely")
            return True

    interference = TakeTheOldNameBack()
    logging.getLogger("pam").addFilter(interference)
    try:
        summary = approve_and_apply(plan_id, root)
    finally:
        logging.getLogger("pam").removeFilter(interference)
    return root, plan_id, summary


# --- the session report (clarification 13) ----------------------------------------


def test_the_session_report_names_every_failure_and_the_undo_command(tmp_path):
    """The M5 minimum, still asserted whole after M6 Task 6's readability pass.

    "Extend, do not replace" (m5.md 2.7): the counts, the failing path, the reason it
    failed and the undo command are what the report is FOR, and the pass that made the
    file readable was not licensed to drop any of them. The wording of the count lines
    changed deliberately - `1 renamed` reads as a fragment beside a heading - and that
    change is written down here rather than absorbed by loosening the assertion.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    (root / "2020-08-05_001.JPG").write_bytes(b"in the way")

    summary = approve_and_apply(plan_id, root)

    assert summary.report_path is not None
    assert summary.report_path.name == f"session-{summary.session_id}.md"
    report = summary.report_path.read_text(encoding="utf-8")
    assert "- 1 file(s) renamed" in report
    assert "- 1 file(s) not renamed" in report
    assert "IMG_2.JPG" in report
    assert "never overwrites" in report
    assert f"    pam undo {summary.session_id}" in report


def test_the_session_report_says_which_folder_when_and_from_which_plan(tmp_path):
    """M6 Task 6's addition. None of the three is recoverable from the file afterwards:
    the workspace directory holding it is named by a one-way hash of the archive path
    (spec 3), so a report forwarded on its own could not say which folder it was about.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])

    summary = approve_and_apply(plan_id, root)

    assert summary.report_path is not None
    report = summary.report_path.read_text(encoding="utf-8")
    assert f"- Photo folder: {root}" in report
    assert f"- Proposed names from: plan {plan_id:,}" in report
    assert "- Started: " in report
    assert "- Started: unknown" not in report


def test_the_session_report_uses_no_internal_vocabulary(tmp_path):
    """UX 8: the words below never appear in user-facing copy, and this file is the copy
    a user is most likely to forward to somebody else."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])

    summary = approve_and_apply(plan_id, root)

    assert summary.report_path is not None
    report = summary.report_path.read_text(encoding="utf-8").lower()
    for jargon in ("sha256", "journal", "enricher", "plan_item", "unit_key", "sqlite"):
        assert jargon not in report, f"{jargon!r} leaked into the session report"


@pytest.fixture
def visible_seconds(monkeypatch):
    """Push the clock's reading past the rendering's rounding, without faking it.

    THE ROUNDING IS WHY THIS FIXTURE EXISTS (M6 Task 6 review, mutation M408). The two
    tests below both compare the report against `summary.elapsed_s`, and a one-file
    apply on a temp filesystem finishes in well under 0.05 s - so `f"{elapsed:.1f}"` is
    `"0.0"`, which is *also* what an unassigned `elapsed_s` renders as. Moving the
    assignment back to where the `took 0.0s` bug lived left both tests green: they
    passed for exactly the wrong reason, in M242's and M371's family.

    `elapsed()` still returns the real measurement; it is only offset far enough that
    the default and a genuine reading cannot render alike. Offsetting rather than
    replacing keeps `elapsed_s > 0.0` meaning "time really passed".
    """
    real = apply_mod._Clock.elapsed
    monkeypatch.setattr(apply_mod._Clock, "elapsed", lambda self: real(self) + 5.0)


def test_the_session_report_carries_a_real_duration_not_zero(tmp_path, visible_seconds):
    """The `took 0.0s` bug, pinned. `write_session_report` is called BEFORE the line
    that used to assign `elapsed_s`, so every report pam had ever written said the run
    took no time at all - while the console, which reads the summary after that line,
    said something true. One session, one number."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])

    summary = approve_and_apply(plan_id, root)

    assert summary.elapsed_s > 0.0, "the run took measurable time"
    assert summary.report_path is not None
    report = summary.report_path.read_text(encoding="utf-8")
    assert f"- took {summary.elapsed_s:.1f}s" in report
    assert "- took 0.0s" not in report, "the number was read before it was taken"


def test_the_report_and_the_console_print_the_same_duration(tmp_path, capsys, visible_seconds):
    """The report is the artifact a user forwards when something went wrong. It must not
    disagree with what they read on the screen - that is the whole reason the number is
    taken once rather than at each place it is printed.

    SINCE M7 TASK 2 THEY ALSO AGREE ABOUT ITS PRECISION (HT-M6 finding H6). The console
    rendered `:.0f` and the report `:.1f`, so one number really was printed two ways, and
    a sub-second run said `in 0s` on screen while the report said `took 0.4s`.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    summary = approve_and_apply(plan_id, root)
    capsys.readouterr()

    reporter.print_apply_summary(summary)

    printed = capsys.readouterr().out
    assert summary.report_path is not None
    report = summary.report_path.read_text(encoding="utf-8")
    assert f"in {summary.elapsed_s:.1f}s" in printed
    assert f"- took {summary.elapsed_s:.1f}s" in report
    # Both halves are non-default, so agreeing cannot mean "both are still zero".
    assert "in 0.0s" not in printed and "- took 0.0s" not in report


def test_the_session_report_carries_what_recovery_flagged_for_a_human(
    tmp_path, allow_split_unit
):
    """Clarification 13: the report must name files recovery could not decide about.

    Recovery runs at the start of `pam apply`, so its findings belong to THAT session's
    report - otherwise the only record is a log line the user has no reason to open.

    The reachable route is a stranded member, and it is the route that matters: spec
    7.2 phase 2 reports a split unit on **every** invocation until a human resolves it,
    so it is still there when the next `pam apply` runs. A one-shot flag (the "both
    names exist" case) is consumed by whichever command recovers first, which is
    normally `pam scan` or `pam plan` - neither of which writes a report. That
    asymmetry is real and is reported, not designed around.

    The J6 tripwire is sanctioned for the same reason as its four other holders and no
    new one: something outside pam took the old name back mid-run, pam cannot honestly
    put the file back, and it says so every time until a person acts.
    """
    root, _first_plan, stranded = _stranded_apply(tmp_path)
    assert stranded.stranded == ["2019-07-04_001.JPG"]

    run_scan(root, exiftool_command=FAKE_COMMAND)
    second_plan = run_plan(root).plan_id
    summary = approve_and_apply(second_plan, root)

    assert summary.recovery_notes
    report = summary.report_path.read_text(encoding="utf-8")
    assert "needing your attention" in report.lower()
    assert "2019-07-04_001.JPG" in report


# --- spec 13: the log path survives a run that never reached its summary -----------


def test_an_interrupted_apply_still_prints_its_session_and_log(tmp_path, set_fault, capsys):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    set_fault.arm("crash:after_intent:1")
    capsys.readouterr()

    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root)
    set_fault.next_invocation()

    printed = capsys.readouterr().err
    assert "Session: s-" in printed
    assert "Log: " in printed


def test_an_interrupted_undo_still_prints_its_session_and_log(tmp_path, set_fault, capsys):
    """The same promise on the other path. Both callers share ONE implementation now -
    two copies of a spec 13 contract is the drift spec 7.1 rev 7 exists to prevent."""
    from pam.execute.undo import run_undo

    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    applied = approve_and_apply(plan_id, root)
    set_fault.arm("crash:after_intent:1")
    capsys.readouterr()

    with pytest.raises(SystemExit):
        run_undo(applied.session_id, root)
    set_fault.next_invocation()

    printed = capsys.readouterr().err
    assert "Session: s-" in printed
    assert "Log: " in printed


# --- pam plan's Next: line (M4 review) ---------------------------------------------


def test_pam_plan_points_at_the_command_that_actually_exists(tmp_path, capsys):
    """Standing rule: the `Next:` line names the next command that EXISTS, never a
    milestone promise. It used to point at `pam review, coming in M7`, which is a dead
    end dressed as guidance - and M7 shipped `pam review`, so it is now a command.

    Both lines are asserted, and in the order UX 3 draws them: review is the `Next:`
    and apply is the `or:`. Asserting only membership would let the two swap silently,
    and the chain's whole claim is which step comes first.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    capsys.readouterr()

    assert main(["plan", "--archive", str(root)]) == 0

    printed = capsys.readouterr().out
    new_plan = open_plan(catalog(root)).id
    # M9 Task 5 / B2: flipped from `Next: <cmd> - <why>.` to the one format. This test
    # pinned `plan_report.py`'s own punctuation as the expectation - the third milestone
    # running in which a `Next:` defect was pinned rather than caught (m7.md F3, M8 C2,
    # here). The reasons are unchanged; only the rendering moves, and the strings below
    # are written out from UX §3 rather than fetched from `status.next_steps`.
    assert f"Next: pam review {new_plan}   (see & approve the plan)" in printed
    assert f"  or: pam apply {new_plan}   (renames only what you approve)" in printed
    assert printed.index(f"Next: pam review {new_plan}") < printed.index(
        f"  or: pam apply {new_plan}"
    )
    assert "coming in M7" not in printed


# --- the CLI (spec 12: `pam apply PLAN_ID`) ----------------------------------------


def test_the_apply_cli_asks_before_it_renames_anything(tmp_path, monkeypatch, capsys):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    monkeypatch.setattr("builtins.input", lambda: "no thanks")
    capsys.readouterr()

    approve_archive(root, plan_id)  # F1: apply renames only what was approved
    assert main(["apply", str(plan_id), "--archive", str(root)]) == 0

    assert (root / "IMG_1.JPG").exists()
    assert "Cancelled. Nothing was renamed." in capsys.readouterr().out


def test_the_apply_cli_renames_when_the_word_is_typed(tmp_path, monkeypatch, capsys):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    monkeypatch.setattr("builtins.input", lambda: "apply")
    capsys.readouterr()

    approve_archive(root, plan_id)  # F1: apply renames only what was approved
    assert main(["apply", str(plan_id), "--archive", str(root)]) == 0

    assert (root / "2019-07-04_001.JPG").exists()
    assert "Done. 1 renamed, 0 failed" in capsys.readouterr().out


def test_the_apply_cli_exits_one_when_a_file_failed(tmp_path, monkeypatch, capsys):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    (root / "2020-08-05_001.JPG").write_bytes(b"in the way")
    monkeypatch.setattr("builtins.input", lambda: "apply")

    approve_archive(root, plan_id)  # F1: apply renames only what was approved
    assert main(["apply", str(plan_id), "--archive", str(root)]) == 1
    # The exit code alone cannot tell a FILE failing from the whole plan being refused -
    # since F1 the nothing-approved refusal exits 1 too, and this test stayed green with
    # every plain apply refused (M2374). So: apply RAN, and its headline names the failure.
    assert (
        "Done with problems. 1 renamed, 1 failed (details in report)" in capsys.readouterr().out
    )


def test_the_apply_cli_always_passes_a_confirmation(tmp_path):
    """`run_apply(..., confirm=None)` applies without asking, which is what the ~40
    tests that drive it directly need. The CLI is the one caller a human reaches, so
    the CLI is where the confirmation must be non-optional - asserted structurally,
    because a refactor that dropped the argument would silently remove the only moment
    pam asks permission."""
    import ast
    from pathlib import Path

    import pam.cli

    tree = ast.parse(Path(pam.cli.__file__).read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "run_apply"
    ]
    assert calls, "pam/cli.py does not call run_apply at all"
    for call in calls:
        assert any(kw.arg == "confirm" for kw in call.keywords), (
            "pam apply must always pass a confirmation"
        )


# --- progress (UX 4: per-1000 lines during the run) -------------------------------


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (0, 1, None),
        (0, 999, None),
        (0, 1000, "  1,000 renamed so far ..."),
        (999, 1000, "  1,000 renamed so far ..."),
        (1000, 1001, None),
        (1999, 2000, "  2,000 renamed so far ..."),
        (0, 2500, "  2,000 renamed so far ..."),
    ],
)
def test_progress_speaks_once_per_thousand(before, after, expected):
    """Driven directly: an archive big enough to cross the boundary would make the
    suite minutes slower to assert one string. A human test's library is a few hundred files,
    so this line has never fired in a test that renames anything."""
    assert reporter.progress_line(before, after) == expected


def test_recovery_notes_name_every_kind_of_thing_recovery_can_leave_behind():
    """spec 7.2 leaves three different kinds of unfinished business, and the report
    must carry all three - a rename it could not decide about, a member it could not
    put back, and a catalog row it could not update (J11).

    Driven directly because the integration route cannot separate them: by the time a
    second `pam apply` runs, a scan has usually created a `files` row at the stranded
    member's old name, so phase 2 takes the CATALOG branch and the disk branch never
    fires. Dropping the stranded half left all 881 tests green.
    """
    from pam.execute.apply import _recovery_notes
    from pam.execute.recover import RecoverySummary

    notes = _recovery_notes(
        RecoverySummary(
            needs_attention=["IMG_1.JPG"],
            stranded=["2019-07-04_001.MOV"],
            discrepancies=["pam's records still need updating for X. Run: pam scan"],
            adopted=["ignored.JPG"],
            failed=["also-ignored.JPG"],
            reverted=["ignored-too.JPG"],
        )
    )

    assert len(notes) == 3
    assert any("IMG_1.JPG" in note and "could not tell" in note for note in notes)
    assert any("2019-07-04_001.MOV" in note and "Nothing was lost" in note for note in notes)
    assert any("records still need updating" in note for note in notes)
    # Rows recovery resolved cleanly are the protocol working; they belong in the log,
    # not in a report the user opens when something has gone wrong.
    assert not any("ignored" in note for note in notes)


# Commands that open NO session log, and therefore owe no `Log:` line. A LITERAL, so
# that a command added in M7 is neither in it nor driven below and the sweep FAILS -
# which is the whole point. Each entry is justified, and the justification is the test's
# real content:
#
#   doctor  - checks the environment, not an archive. There is no workspace to log into
#             and it runs before pam has one.
#   status  - reads N archives at once through `connect_readonly` and writes nothing;
#             `pam/status.py`'s module docstring argues at length why it must not take a
#             lock or open a log, and opening N logs is the same objection.
#   report  - `pam/report.py` makes the same choice for the same reasons, and cites it.
#   models  - M10 Task 7. `pam models pull` asks the local Ollama for a model; it takes
#             no `--archive`, opens no catalog and touches no photograph, so there is no
#             workspace to log into. It is `doctor`'s argument exactly, and design 12
#             gives the two commands the same shape for the same reason: both answer a
#             question about this machine's environment, before an archive exists.
#   faces   - M11 Task 7. `pam faces manifest` hashes ONNX files in a folder and writes
#             `pack.toml` beside them. A model pack belongs to no catalog - amendment A22
#             is explicit that it is archive-independent, which is the same sentence that
#             keeps `pam doctor` from validating one - so there is again no workspace to
#             log into. THE LATER MEMBERS OF THIS GROUP ARE NOT COVERED BY THAT REASON:
#             `pam faces cluster`, `export` and `purge` are archive-scoped and DO owe a
#             log, and the day one of them lands this entry stops being about the group
#             and has to become about the one subcommand that is environment-only.
#   update  - M15 Task 12 (E2). `pam update` replaces pam itself: it takes no `--archive`,
#             opens no catalog and touches no photograph (E4 forbids it to import anything that
#             does), so there is no workspace to log into - `doctor`'s and `models`' argument
#             again. Each of its outcomes is one sentence on the console, and the installer it
#             starts keeps its own log.
#
# `start` WAS HERE FROM M14 Task 4c TO Task 5c, AS A DEBT. U23 gives a page session ONE log for
# its whole life, and ruling C5-5 says where: it opens at bind in the bound folder's logs dir. So
# `pam start PATH` is now DRIVEN below. (An unbound `pam start` that never binds a folder has no
# workspace and so no log, and prints no `Log:` line - C5-5's one exception, pinned in
# `tests/test_webui_jobs.py`.)
# The bare `pam` guide is not a command at all and is covered by the same argument as
# `status`, whose engine it renders.
NO_SESSION_LOG = frozenset({"doctor", "status", "report", "models", "faces", "update"})


def test_every_command_that_opens_a_log_ends_with_its_path_on_stderr(
    tmp_path, monkeypatch, capsys
):
    """spec 13, and it is UNCONDITIONAL: "every session (any command) opens a dated log"
    and UX 2 promises the path on stderr, last, every time. Deleting the line from
    apply's closing summary left the whole suite green - the promise was pinned for the
    INTERRUPTED path and nowhere for the ordinary one.

    DRIVEN FROM click's OWN COMMAND LIST rather than from six hand-written calls (M6
    Task 6). The hand-written version could only fail for a command somebody had
    remembered to add to it, so a seventh command would have skipped the promise
    silently; this one fails for any command that is neither driven here nor named in
    `NO_SESSION_LOG` above. That is what caught `pam forget`, which opened a session log
    and printed its path only when it failed.

    Every driven command is run for real, in dependency order, against one archive.
    `forget` runs last because it removes the pointer the others resolve through, and
    `review` runs before `apply` because it reviews `open` plans only.
    """
    root = tmp_path / "photos"
    root.mkdir()
    (root / "IMG_1.JPG").write_bytes(b"the only copy of IMG_1.JPG")
    (root / "IMG_1.JPG.tags.json").write_text(
        json.dumps({"DateTimeOriginal": "2019:07:04 18:23:11"}), encoding="utf-8"
    )
    monkeypatch.setattr("builtins.input", lambda: "apply")
    capsys.readouterr()

    assert main(["scan", str(root)]) == 0
    driven = {"scan": capsys.readouterr().err}

    # M9 Task 12. Driven in spec 10's own pipeline position, between scan and plan, and
    # driven as `enrich geocode` rather than as bare `enrich` for one reason: the only
    # image in this archive is 26 bytes of prose with a `.JPG` on it, so `phash` reads
    # it, fails to decode it and exits 1 by spec 13 rev 6 - correctly, and unhelpfully
    # for a test whose subject is the `Log:` line. `geocode` opens no file at all, wants
    # nothing here, and still opens the session log every command owes.
    assert main(["enrich", "geocode", "--archive", str(root)]) == 0
    driven["enrich"] = capsys.readouterr().err

    assert main(["plan", "--archive", str(root)]) == 0
    driven["plan"] = capsys.readouterr().err

    plan_id = _only_plan_id(root)

    # `pam review` opens a session log like every other archive-scoped command, and
    # Ctrl+C is the NORMAL way it ends - so the `Log:` line has to be on the ordinary
    # path, not only on the interrupted one. Driven with the serve loop returning
    # immediately (the real one blocks until Ctrl+C) and with the browser patched, for
    # the reason `tests/test_webui_server.py` records: a suite that opens real browser
    # tabs is a broken suite. It runs BEFORE apply, because it reviews `open` plans.
    monkeypatch.setattr("pam.webui.serve_until_interrupted", lambda server: None)
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **kw: True)
    assert main(["review", str(plan_id), "--archive", str(root)]) == 0
    driven["review"] = capsys.readouterr().err

    # M14 Task 5c (U23, C5-5): `pam start PATH` binds the folder, and the bind opens the page
    # session's ONE log. Served with the loop returning at once, as `review` is above.
    monkeypatch.setattr("pam.webui.launcher.serve_until_interrupted", lambda server: None)
    assert main(["start", str(root)]) == 0
    driven["start"] = capsys.readouterr().err

    approve_archive(root, plan_id)  # F1: apply renames only what was approved
    assert main(["apply", str(plan_id), "--archive", str(root)]) == 0
    driven["apply"] = capsys.readouterr().err

    assert main(["undo", _only_session(root), "--archive", str(root)]) == 0
    driven["undo"] = capsys.readouterr().err

    assert main(["verify", "--archive", str(root)]) == 0
    driven["verify"] = capsys.readouterr().err

    assert main(["forget", str(root)]) == 0
    driven["forget"] = capsys.readouterr().err

    registered = set(cli.commands)
    unclassified = registered - NO_SESSION_LOG - set(driven)
    assert not unclassified, (
        f"{sorted(unclassified)} is neither driven here nor listed in NO_SESSION_LOG."
        " Every command either ends with its log path on stderr (spec 13) or has a"
        " written reason for opening no log at all."
    )
    assert NO_SESSION_LOG <= registered, "NO_SESSION_LOG names a command that is gone"
    # The two classifications must be DISJOINT, not merely exhaustive (M6 Task 6 review,
    # mutation M412). A command that is both driven and excused passes the exhaustiveness
    # check above today, and the day somebody drops its driven call - because it got slow,
    # or because a fixture changed - the excuse absorbs it silently and this sweep goes on
    # reporting nothing. Same family as M5's rule that a sanction list is asserted empty
    # at setup: one leak disarms the guard for everything after it.
    assert not (NO_SESSION_LOG & set(driven)), (
        f"{sorted(NO_SESSION_LOG & set(driven))} is BOTH driven here and excused in"
        " NO_SESSION_LOG. A command opens a log or it does not - never both."
    )

    for name, stderr in driven.items():
        assert "Log: " in stderr, f"pam {name} printed no log path"
        last = [line for line in stderr.splitlines() if line.strip()][-1]
        assert last.startswith("Log: "), f"pam {name}'s log path is not the last line"


@pytest.mark.parametrize(
    ("command", "target"),
    [
        ("scan", "pam.scan.walker.walk"),
        ("plan", "pam.catalog.repo.files_for_planning"),
    ],
)
def test_scan_and_plan_still_print_their_log_path_when_they_crash(
    tmp_path, monkeypatch, capsys, command, target
):
    """M5 open question 6, the half that was still open. `pam/cli.py` catches an
    unexpected exception and prints a TRACEBACK WITH NO LOG PATH, so before this the
    only record of an internal crash mid-scan was a log file the user had no way to
    find - and the traceback is precisely the output that makes a user want it.

    Patched DEEP rather than at the command boundary: `walker.walk` and
    `repo.files_for_planning` are both inside the workspace lock and inside the session
    log's window, which is where a real internal failure happens. Patching `run_scan`
    itself would raise before the log existed and the test would pass with no log path
    to print.
    """
    root, _plan_id = planned(tmp_path, ["IMG_1.JPG"])

    def boom(*_args, **_kwargs):
        raise RuntimeError("something internal went wrong")

    monkeypatch.setattr(target, boom)
    capsys.readouterr()

    argv = ["scan", str(root)] if command == "scan" else ["plan", "--archive", str(root)]
    assert main(argv) == EXIT_INTERNAL

    printed = capsys.readouterr()
    assert "Traceback" in printed.err, "spec 13: an unexpected exception still shows one"
    assert "Log: " in printed.err


@pytest.mark.parametrize(
    ("command", "target"),
    [
        ("scan", "pam.scan.walker.walk"),
        ("plan", "pam.catalog.repo.files_for_planning"),
    ],
)
def test_scan_and_plan_print_their_log_path_when_the_process_is_told_to_stop(
    tmp_path, monkeypatch, capsys, command, target
):
    """`except BaseException`, not `except Exception` - and this is the test that says
    so. A `crash:` fault raises SystemExit and a real Ctrl+C raises KeyboardInterrupt;
    neither is an `Exception`, and both are the moment the user most needs the log path
    and least expects to go hunting for it. M5's finding F2 was this exact narrowing,
    found by mutation rather than by reading.

    PARAMETRIZED OVER BOTH COMMANDS, and it has to be (M6 Task 2 review, mutation M258).
    This test used to drive `pam scan` alone while its name claimed both, so narrowing
    `run_plan` back to `except Exception` - F2's exact shape, on the command this task
    added it to - left all 995 tests green. Together with the crashing test above, the
    two now cover the same 2x2 grid: {scan, plan} x {Exception, BaseException}, at the
    same two deep patch points.

    The exit code is EXIT_EXPECTED rather than EXIT_INTERNAL because click converts a
    KeyboardInterrupt into its own `Abort` on the way out, which `pam/cli.py` maps to 1.
    That conversion is exactly why the log path has to be printed BELOW it, inside
    `run_scan`/`run_plan`: by the time the CLI sees this it is an `Abort` carrying no
    log path and no way to find one.
    """
    root, _plan_id = planned(tmp_path, ["IMG_1.JPG"])

    def interrupted(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(target, interrupted)
    capsys.readouterr()

    argv = ["scan", str(root)] if command == "scan" else ["plan", "--archive", str(root)]
    assert main(argv) == EXIT_EXPECTED

    assert "Log: " in capsys.readouterr().err


def _only_plan_id(root):
    conn = catalog(root)
    try:
        plan = open_plan(conn)
    finally:
        conn.close()
    assert plan is not None
    return plan.id


def _only_session(root):
    conn = catalog(root)
    try:
        sessions = repo.sessions(conn)
    finally:
        conn.close()
    assert len(sessions) == 1
    return sessions[0].session_id


def test_already_conforming_reaches_the_confirmation_from_the_plan(tmp_path):
    """UX 4's fourth count, carried from plan time to apply time (migration 0005).

    It ships as a NON-ZERO number here on purpose. Every other assertion of this line
    uses an archive where nothing conforms yet, so `0 already conforming` would print
    correctly even if the value were hardcoded, never written, or never read back -
    the shape of "passes for the wrong reason" this project keeps paying for.

    The count cannot be recomputed at apply time: an already-conforming file gets no
    `plan_items` row, and "present files with no plan item" would also catch
    placeholders and files that errored. So the planner freezes it or it is lost.
    """
    root = tmp_path / "photos"
    root.mkdir()
    # One file already sitting at the name the template would generate for it, and
    # one that still needs renaming - so the plan is created AND has something to
    # count. Distinct dates keep both at seq 001.
    for name, date in (
        ("2019-07-04_001.JPG", "2019:07:04 18:23:11"),
        ("IMG_2.JPG", "2020:08:05 09:15:00"),
    ):
        (root / name).write_bytes(f"the only copy of {name}".encode())
        (root / f"{name}.tags.json").write_text(
            json.dumps({"DateTimeOriginal": date}), encoding="utf-8"
        )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan_summary = run_plan(root)
    assert plan_summary.plan_id is not None
    assert plan_summary.already_conforming == 1, "the fixture must actually produce one"

    conn = catalog(root)
    try:
        # Frozen in the plan row, not merely in the plan run's in-memory summary.
        assert repo.plan_already_conforming(conn, plan_summary.plan_id) == 1
    finally:
        conn.close()

    seen = []
    approve_and_apply(plan_summary.plan_id, root, confirm=lambda c: seen.append(c) or False)

    assert seen[0].already_conforming == 1
    assert reporter.confirmation_lines(seen[0])[2] == (
        "  1 renames approved   (1 proposed, 0 skipped by you, 1 already conforming)"
    )
