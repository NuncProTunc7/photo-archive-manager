"""The `pam faces` command group: argument parsing and exit-code mapping only.

M14 Task 4 Step 1 moved these registrations out of `cli.py`, unchanged, to keep `cli.py` under
the ~300-line budget before `pam start` lands there. A PURE MOVE: every command's name, options,
help text and exit codes are what they were, and `tests/test_cli_help_golden.py` holds
`pam --help` and every subcommand's `--help` byte-identical to the transcript recorded before
the move. `cli.py` registers this group with `cli.add_command(faces)`.
"""

from pathlib import Path

import click

from pam.errors import EXIT_EXPECTED
from pam.faces import print_cluster_summary, print_manifest, run_cluster, run_manifest
from pam.faces.calibrate import print_calibration, run_calibrate
from pam.faces.export import print_export_summary, run_export
from pam.faces.purge import confirm_purge, print_purge_summary, run_purge
from pam.webui import run_faces_label

# `pam report`'s --archive help, word for word (`cli.ARCHIVE_HELP`). Spelled again rather than
# imported because `cli.py` imports this module; the golden help test pins the two equal.
ARCHIVE_HELP = "The photo folder to report on (needed only if pam knows several)."


@click.group()
def faces() -> None:
    """Work with the local face model pack and the people pam has found."""


@faces.command("manifest")
@click.argument("directory", type=click.Path(path_type=Path))
def faces_manifest(directory: Path) -> None:
    """Write pack.toml for a folder holding the face model files."""
    # No `--archive`: a model pack is a folder of files and belongs to no catalog, which
    # is the same reason `pam doctor` does not validate one (A22). pam never downloads a
    # model - this command describes files the user already has.
    print_manifest(run_manifest(directory))


@faces.command("cluster")
@click.option(
    "--archive",
    type=click.Path(path_type=Path),
    default=None,
    help="The photo folder to cluster faces in (spec 3 refuses to guess).",
)
def faces_cluster(archive: Path | None) -> None:
    """Group the faces pam has found into one group per person. Renames nothing."""
    # `click.echo` is A21's announcer: the face count, the estimate and the tested ceiling
    # are printed BEFORE the arithmetic starts, so a user can stop and raise the floor
    # instead of finding out afterwards how long it was going to take.
    print_cluster_summary(run_cluster(archive, announce=click.echo))


@faces.command("label")
@click.option(
    "--archive",
    type=click.Path(path_type=Path),
    default=None,
    help="The photo folder whose people to name (spec 3 refuses to guess).",
)
def faces_label(archive: Path | None) -> None:
    """Name the people pam found, in your browser. Renames nothing."""
    # NO PLAN ARGUMENT AND NO OPEN PLAN NEEDED (spec 8.2 rev 22 / A18). `pam review` takes
    # an optional PLAN_ID because it reviews one; this command is about the whole archive's
    # people and would be refusing to start for a reason that has nothing to do with its
    # job. Spec 13's refusals come out of `run_faces_label` as PamError and exit 1.
    run_faces_label(archive)


@faces.command("export")
@click.option(
    "--archive",
    type=click.Path(path_type=Path),
    default=None,
    help="The photo folder whose faces to export (spec 3 refuses to guess).",
)
def faces_export(archive: Path | None) -> None:
    """Write pam's faces and names to a file you keep. Touches no photograph."""
    # A20's export writes into the WORKSPACE's reports/ directory, never into the archive
    # - `export._refuse_archive_path` raises rather than asserting that, because `-O`
    # drops assertions and what it guards is pam writing into a photo folder.
    print_export_summary(run_export(archive))


@faces.command("purge")
@click.option(
    "--archive",
    type=click.Path(path_type=Path),
    default=None,
    help="The photo folder whose faces to delete (spec 3 refuses to guess).",
)
def faces_purge(archive: Path | None) -> None:
    """Delete every face and name pam holds. Deletes no photograph."""
    # `confirm=` is NOT optional here and must never become so, on `pam apply`'s
    # precedent: this is the one caller a human reaches, and A19's typed confirmation is
    # the only moment pam asks before deleting work a user cannot get back by re-running
    # anything - the names are theirs, and pam has no import.
    report = run_purge(archive, confirm=confirm_purge)
    if report.cancelled:
        click.echo("Cancelled. Nothing was deleted.")
        return
    print_purge_summary(report)
    # Spec 13: a command that recorded an error exits 1 with its summary printed in full.
    # A failed compaction is exactly that - the deletion is committed and the report says
    # so, and an exit 0 would tell a script everything went to plan when the space the
    # user asked pam to reclaim is still held.
    if not report.compacted:
        raise SystemExit(EXIT_EXPECTED)


# M13 Task 14 (H7): a lock-free reader with `pam report`'s shape - no lock, no recovery, no
# log - so it takes the same --archive help. It prints the line to add; it writes nothing.
@faces.command("calibrate")
@click.option("--archive", type=click.Path(path_type=Path), default=None, help=ARCHIVE_HELP)
@click.option("--floor", type=float, default=None, help="Leave out faces below this.")
@click.option("--exclude-label", multiple=True, metavar="NAME", help="Leave out a person.")
@click.option("--exclude-path", multiple=True, metavar="PATH", help="Leave out files (glob).")
def faces_calibrate(archive: Path | None, floor: float | None, **excluded: tuple) -> None:
    """Show which cluster_epsilon keeps the people you named apart. Changes nothing."""
    print_calibration(run_calibrate(archive, floor, **excluded))
