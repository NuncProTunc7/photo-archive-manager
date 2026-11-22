"""Thin click CLI: argument parsing and exit-code mapping only."""

import math
import traceback
from pathlib import Path

import click

from pam import __version__
from pam.cli_faces import faces
from pam.doctor import DEFAULT_CAPTION_MODEL, pull_message, pull_model, run_checks
from pam.enrich.command import run_enrich
from pam.enrich.summary import print_enrich_summary
from pam.errors import EXIT_EXPECTED, EXIT_INTERNAL, EXIT_OK, PamError
from pam.execute.apply import run_apply
from pam.execute.reporter import confirm_apply, print_apply_summary, print_undo_summary
from pam.execute.undo import run_undo
from pam.forget import print_forget, run_forget
from pam.naming.plan_report import print_plan_summary
from pam.naming.planner import run_plan
from pam.report import (
    print_session,
    print_skipped,
    print_subjects,
    run_report_session,
    run_report_skipped,
)
from pam.report_duplicates import print_duplicates, run_report_duplicates
from pam.run_registry import registered
from pam.scan.runner import print_summary, run_scan
from pam.status import collect_status, print_guide, print_status
from pam.verify import print_verify_summary, run_verify
from pam.webui import run_review
from pam.webui.launcher import run_start


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="pam")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """pam - Photo Archive Manager. Safe, local, reversible photo renaming."""
    if ctx.invoked_subcommand is None:
        # UX spec section 3: a status-aware guide, not a flag dump - that is `pam
        # --help`, and M1's placeholder printed it here. The guide computes nothing of
        # its own; `collect_status` is the same engine `pam status` renders in full.
        guide = collect_status(None)
        print_guide(guide)
        if guide.refused:  # M15 Task 8 S2-F1: a folder a newer pam wrote, named above
            raise SystemExit(EXIT_EXPECTED)


@cli.command()
def doctor() -> None:
    """Check that pam's environment is ready."""
    checks = run_checks()
    for check in checks:
        # A failing OPTIONAL check is yellow and marked `-`, never a red `x`: it is
        # reported honestly, but it is not something to fix, and giving it the same mark
        # as a missing exiftool would teach a user to ignore the mark that matters.
        if check.ok:
            mark, color = "ok", "green"
        else:
            mark, color = ("x", "red") if check.required else ("-", "yellow")
        click.secho(f"  {mark} {check.name}: {check.detail}", fg=color)
    if not all(c.ok for c in checks if c.required):
        click.echo("\nNext: fix the items marked x above, then run: pam doctor")
        raise SystemExit(1)
    if not all(c.ok for c in checks):
        click.echo("\nThe items marked - are optional: pam works without them.")
    click.echo("\nEverything looks good. Next: pam scan <your photo folder>")


@cli.group()
def models() -> None:
    """Manage the local Ollama models pam uses to caption photos."""


@models.command("pull")
@click.argument("model", required=False)
def models_pull(model: str | None) -> None:
    """Ask the local Ollama server to download a captioning model."""
    # No `--archive`, because spec 12's table gives this command none, and doctor's
    # reasoning applies unchanged: the port is the default one and the message says so.
    name = model or DEFAULT_CAPTION_MODEL
    click.echo(pull_message(name))
    click.echo(pull_model(name))


# M14 Task 4 Step 1: the `faces` group lives in `pam/cli_faces.py` (a pure move).
cli.add_command(faces)


@cli.command()
@click.option(
    "--archive",
    type=click.Path(path_type=Path),
    default=None,
    help="The photo folder to summarize (without it, every folder pam knows).",
)
def status(archive: Path | None) -> None:
    """Show what pam knows about your photo folders, and what to do next."""
    # Every state this prints - including an archive pam cannot see - is a report, not a
    # failure; spec 13's refusals come out of `resolve_archive` when --archive names a folder
    # that is not there, and out of E1 for a newer catalog. ONE exit branch (M15 Task 8
    # S2-F1): with no --archive, a folder a newer pam wrote is named and reported, then exit 1.
    summary = collect_status(archive)
    print_status(summary)
    if summary.refused:
        raise SystemExit(EXIT_EXPECTED)


@cli.command()
@click.argument("path", type=click.Path(path_type=Path))
def forget(path: Path) -> None:
    """Stop offering a photo folder. Keeps its catalog, history and files."""
    print_forget(run_forget(path))


@cli.group(invoke_without_command=True)
@click.pass_context
def report(ctx: click.Context) -> None:
    """Look up what pam recorded: skipped files, duplicates, or one rename run."""
    if ctx.invoked_subcommand is None:
        # UX 1.4: never a dead end. Bare `pam report` names its three subjects rather
        # than printing the flag list, exactly as bare `pam` prints the guide rather
        # than `--help`. A user who typed `pam report` wants to know what it reports on.
        print_subjects()


# Every subcommand takes --archive with the same help-text shape the other
# archive-scoped commands use, and none of them takes a lock, runs recovery or opens a
# session log - `pam/report.py`'s module docstring carries the reasoning, which is
# `pam/status.py`'s. No exit-code branch either: a report is never a failure. The
# refusals that DO exit 1 come out of `resolve_archive` and out of an unknown session id.
ARCHIVE_HELP = "The photo folder to report on (needed only if pam knows several)."


@report.command("skipped")
@click.option("--archive", type=click.Path(path_type=Path), default=None, help=ARCHIVE_HELP)
def report_skipped(archive: Path | None) -> None:
    """List every file pam did not read, and why."""
    print_skipped(run_report_skipped(archive))


@report.command("duplicates")
@click.option("--archive", type=click.Path(path_type=Path), default=None, help=ARCHIVE_HELP)
def report_duplicates(archive: Path | None) -> None:
    """List identical files, and photos that look alike."""
    print_duplicates(run_report_duplicates(archive))


@report.command("session")
@click.argument("session_id")
@click.option("--archive", type=click.Path(path_type=Path), default=None, help=ARCHIVE_HELP)
def report_session(session_id: str, archive: Path | None) -> None:
    """Show what one rename run did, and whether it still holds."""
    print_session(run_report_session(archive, session_id))


@cli.command()
@click.argument("path", type=click.Path(path_type=Path))
def scan(path: Path) -> None:
    """Read a photo folder and its metadata into the catalog. Renames nothing."""
    click.echo(f"Scanning {path} ...")
    summary = run_scan(path)
    print_summary(summary)
    # spec 13 rev 6: the summary still prints in full; only the exit code changes.
    if summary.had_errors:
        raise SystemExit(EXIT_EXPECTED)


@cli.command()
@click.argument("name", required=False)
@click.option(
    "--archive",
    type=click.Path(path_type=Path),
    default=None,
    help="The photo folder to enrich (needed only if pam knows several).",
)
@click.option(
    "--refresh-older-than-model",
    "refresh_older_than_model",
    default=None,
    metavar="V",
    help="Replace results made by a model older than this version, then re-run.",
)
def enrich(
    name: str | None, archive: Path | None, refresh_older_than_model: str | None
) -> None:
    """Add AI understanding to what pam has scanned. Renames nothing."""
    summary = run_enrich(name, archive, refresh_older_than_model=refresh_older_than_model)
    print_enrich_summary(summary)
    # spec 13 rev 6: the summary still prints in full; only the exit code changes.
    if summary.had_errors:
        raise SystemExit(EXIT_EXPECTED)


@cli.command()
@click.option(
    "--archive",
    type=click.Path(path_type=Path),
    default=None,
    help="The photo folder to plan for (needed only if pam knows several).",
)
@click.option(
    "--template", default=None, help="Use this naming template instead of the configured one."
)
def plan(archive: Path | None, template: str | None) -> None:
    """Propose new names for everything pam has scanned. Renames nothing."""
    summary = run_plan(archive, template_override=template)
    print_plan_summary(summary)
    # spec 13 rev 6: a script wrapping pam plan must be able to tell a complete
    # plan from one missing files. A human loses nothing - the summary is above.
    if summary.had_errors:
        raise SystemExit(EXIT_EXPECTED)


@cli.command()
@click.argument("plan_id", type=int, required=False)
@click.option(
    "--archive",
    type=click.Path(path_type=Path),
    default=None,
    help="The photo folder to review (needed only if pam knows several).",
)
def review(plan_id: int | None, archive: Path | None) -> None:
    """Open a plan in your browser to review names. Renames only what you confirm there."""
    # No exit-code branch, and that is M7 plan clarification 4: Ctrl+C is the DESIGNED
    # way out of a review session, so a served session that ends exits 0. Spec 13's
    # refusals come out of `run_review` as PamError and exit 1; an unexpected exception
    # reaches `main`'s traceback handler and exits 2.
    # Step 8 (U11, ruling B1): Ctrl+C while the page renames is a crash, and exits 1 after
    # saying so - `pam start`'s rule, since both serve the page that renames.
    if run_review(plan_id, archive).stopped_while_renaming:
        raise SystemExit(EXIT_EXPECTED)


@cli.command()
@click.argument("path", type=click.Path(path_type=Path), required=False)
def start(path: Path | None) -> None:
    """Open pam in your browser: choose a folder, read it, review, rename."""
    # U9: with PATH it opens on that folder, without it on Welcome. Ctrl+C is the designed way
    # out and exits 0, as `pam review`'s does; a refused PATH is a PamError and exits 1.
    # Step 8 (U11): Ctrl+C while the page renames is a crash, and exits 1 after saying so.
    if run_start(path).stopped_while_renaming:
        raise SystemExit(EXIT_EXPECTED)


@cli.command()
@click.argument("plan_id", type=int)
@click.option(
    "--archive",
    type=click.Path(path_type=Path),
    default=None,
    help="The photo folder to apply to (needed only if pam knows several).",
)
@click.option(
    "--all",
    "accept_all",
    is_flag=True,
    help="Accept every name you have not decided, except low-confidence ones, then apply.",
)
def apply(plan_id: int, archive: Path | None, accept_all: bool) -> None:
    """Rename the approved files in a plan. The only command that changes anything."""
    # `confirm=` is NOT optional here and must never become so: this is the one caller
    # a human reaches, and UX 4's typed confirmation is the only moment pam asks
    # permission before renaming files it cannot replace. It CONFIRMS; it no longer
    # approves (F1 R1) - only `--all` approves, and only after it.
    summary = run_apply(plan_id, archive, confirm=confirm_apply, accept_all=accept_all)
    if summary.cancelled:
        click.echo("Cancelled. Nothing was renamed.")
        return
    print_apply_summary(summary)
    # spec 13 rev 6: the summary still prints in full; only the exit code changes.
    if summary.had_errors:
        raise SystemExit(EXIT_EXPECTED)


@cli.command()
@click.argument("session_id")
@click.option(
    "--archive",
    type=click.Path(path_type=Path),
    default=None,
    help="The photo folder to undo in (needed only if pam knows several).",
)
def undo(session_id: str, archive: Path | None) -> None:
    """Put an applied session's files back under their original names."""
    summary = run_undo(session_id, archive)
    print_undo_summary(summary)
    # spec 13 rev 6: the summary still prints in full; only the exit code changes.
    if summary.had_errors:
        raise SystemExit(EXIT_EXPECTED)


def _refuse_nan_sample(
    _ctx: click.Context, _param: click.Parameter, value: float | None
) -> float | None:
    # click's FloatRange admits nan, because every comparison with nan is False; H4's
    # `0 < PERCENT <= 100` does not (M13 Task 11 review, F3).
    if value is not None and math.isnan(value):
        raise click.BadParameter("must be a number greater than 0 and at most 100.")
    return value


@cli.command()
@click.option(
    "--archive",
    type=click.Path(path_type=Path),
    default=None,
    help="The photo folder to check (needed only if pam knows several).",
)
@click.option(
    "--deep",
    is_flag=True,
    default=False,
    help="Also re-read a random sample of your files from disk (1% unless --sample)"
    " and compare the bytes.",
)
@click.option(
    "--seed",
    type=int,
    default=None,
    help="Draw --deep's sample reproducibly, so a finding can be re-checked.",
)
@click.option(
    "--sample",
    type=click.FloatRange(0, 100, min_open=True),
    default=None,
    callback=_refuse_nan_sample,
    help="With --deep: the percentage of files to re-read (default 1; 100 re-reads them all).",
)
def verify(archive: Path | None, deep: bool, seed: int | None, sample: float | None) -> None:
    """Check pam's records for an archive. Reads only; renames nothing."""
    summary = run_verify(archive, deep=deep, seed=seed, sample=sample)
    print_verify_summary(summary)
    # spec 7.5's exit codes DELIBERATELY override spec 13's: 0 all-pass, 1 warnings
    # only, 2 errors. pam/verify.py's module docstring carries the reasoning - read it
    # before "fixing" the 2 here, which is not spec 13's traceback exit.
    if summary.exit_code != EXIT_OK:
        raise SystemExit(summary.exit_code)


@cli.command()
@click.option(
    "--check", is_flag=True, help="Only say whether a newer pam is offered. Downloads nothing."
)
@click.option(
    "--channel",
    type=click.Choice(["stable", "rc"]),
    default="stable",
    show_default=True,
    help="Which releases to follow: stable ones, or release candidates too.",
)
def update(check: bool, channel: str) -> None:
    """Install a newer pam from GitHub, after you confirm. The only command that goes online."""
    # E4 (M15 Task 12): THE ONE IMPORT OF `pam.update` IN THE PACKAGE, inside this body and never at
    # module scope, so no archive command loads the release client. `tests/test_update_isolation.py`
    # pins the enclosing function by AST and the registered callback by its code object.
    from pam.update.command import run_update

    code = run_update(check=check, channel=channel)
    if code != EXIT_OK:
        raise SystemExit(code)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Maps errors to exit codes per design spec section 13.

    E11 (M15 Task 13A): it enters `run_registry.registered()` before click parses anything - here
    and not in the click group, because `--version` and `--help` leave click before the group's body
    runs. The console script and the launcher call `pam.entry.main` (Task 13A-F1), which registers
    and checks for setup BEFORE this module is imported; reached that way, this is the ref-counted
    reuse of that one run file and a second look for setup. Reached directly (the in-process suite),
    it is the registration. A refusal is a `PamError`: exit 1.
    """
    try:
        with registered():
            cli.main(args=argv, standalone_mode=False)
    except click.exceptions.Exit as e:
        return e.exit_code  # --version / --help leave through here in non-standalone mode
    except click.ClickException as e:
        e.show()
        return e.exit_code
    except click.exceptions.Abort:
        return EXIT_EXPECTED
    except PamError as e:
        click.secho(f"x {e}", fg="red", err=True)
        return EXIT_EXPECTED
    except SystemExit as e:
        if e.code is None:
            return EXIT_OK
        if isinstance(e.code, int):
            return e.code
        click.echo(str(e.code), err=True)
        return EXIT_EXPECTED
    except Exception:
        traceback.print_exc()
        return EXIT_INTERNAL
    return EXIT_OK
