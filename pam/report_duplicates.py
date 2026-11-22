"""`pam report duplicates` - which of my files are the same photo twice (design 8.1, 12).

Two answers, each said separately because they are different claims. EXACT duplicates are
files whose bytes are identical (`files.sha256`; videos included). LOOK-ALIKES (amendment
H2, M13 Task 9) are pictures whose perceptual fingerprints - the dHash `pam enrich phash`
stores - are within `[enrich.phash] near_dupe_distance` bits of each other, transitively:
the same shot at two sizes, or edited and unedited. Look-alikes are DERIVED here, at report
time, and never stored (design 8.1), so changing the distance re-groups on the next report.

LOOK-ALIKES ARE GROUPED BY CONTENT, NOT BY FILE. Fingerprints are keyed by sha256 (design 4),
so two byte-identical files are one content with one fingerprint; grouping them as files
would list every exact duplicate a second time as a "look-alike" of itself. A group needs two
DIFFERENT contents, and is then expanded to every present, non-placeholder file holding them.

A GROUP THAT IS ONE COMPANION UNIT IS NOT REPORTED. A RAW and its JPEG are one shot by
construction, pam already keeps them together, and telling the user they look alike is noise
dressed as a finding. A group with any file outside that one unit is reported whole.

WHY THIS IS ITS OWN MODULE (M13 Task 9, Step 0). It moved out of `pam/report.py`, whole,
before the look-alikes were added, because that file was at 281 of design 1's ~300 executable
lines. Nothing is re-exported from `report.py`; `tests/test_report_duplicates.py` checks there
is one definition. It shares `report.py`'s reasoning about recovery, locks and session logs -
none of the three - and takes that module's archive resolution, path cap and "1 file" helper
from there rather than a second copy of each.
"""

from dataclasses import dataclass
from pathlib import Path

import click

from pam.catalog import repo
from pam.catalog.db import open_catalog_readonly, read_snapshot
from pam.config import load_config
from pam.enrich.phash import near_dupe_groups
from pam.report import PATH_CAP, _files, _resolve


@dataclass(frozen=True)
class DuplicateReport:
    """Exact groups, look-alike groups, and how much of the archive could be compared.

    `fingerprinted` of `images` is the coverage sentence's two numbers: present,
    non-placeholder image FILES, and how many of them have a fingerprint. A report that
    compared 3 of 400 images and said only "no look-alikes" would be vision principle 9's
    subset-as-whole failure.
    """

    archive_root: Path
    groups: tuple[repo.DuplicateGroup, ...] = ()
    look_alikes: tuple[tuple[str, ...], ...] = ()
    fingerprinted: int = 0
    images: int = 0

    @property
    def duplicate_files(self) -> int:
        """Every file in every exact group, INCLUDING the first of each.

        Not "how many could be deleted" (which would be the count minus one per group),
        because pam deletes nothing and must not print a number that reads as an
        invitation. This is how many files are involved.
        """
        return sum(len(group.rel_paths) for group in self.groups)

    @property
    def look_alike_files(self) -> int:
        return sum(len(paths) for paths in self.look_alikes)


def run_report_duplicates(archive: Path | None) -> DuplicateReport:
    """Read one archive's catalog for exact duplicates and look-alikes. Writes nothing."""
    root, ws = _resolve(archive)
    distance = load_config(ws.config_path).phash_near_dupe_distance
    conn = open_catalog_readonly(ws.db_path)
    try:
        # S13: three reads, ONE snapshot - an enrich committing between them would pair
        # fingerprints with a file list they were never true of.
        with read_snapshot(conn):
            groups = repo.duplicate_groups(conn)
            hashes = repo.phash_by_sha256(conn)
            files = repo.all_files(conn).values()
    finally:
        conn.close()
    present = [f for f in files if f.status == "present" and not f.placeholder]
    images = [f for f in present if f.media_type == "image"]
    return DuplicateReport(
        archive_root=root,
        groups=tuple(groups),
        look_alikes=_look_alikes(hashes, present, distance),
        fingerprinted=sum(1 for f in images if f.sha256 in hashes),
        images=len(images),
    )


def _look_alikes(
    hashes: dict[str, str], present: list[repo.FileRow], distance: int
) -> tuple[tuple[str, ...], ...]:
    """The look-alike groups as sorted paths, ordered by first path. Module docstring: why."""
    contents = sorted(hashes)  # an int key per distinct content, in a stable order
    groups = near_dupe_groups(
        {index: hashes[sha] for index, sha in enumerate(contents)}, max_distance=distance
    )
    found = []
    for members in groups:
        shas = {contents[index] for index in members}
        rows = sorted((f for f in present if f.sha256 in shas), key=lambda f: f.rel_path)
        units = {f.companion_group_id for f in rows}
        if len(units) == 1 and None not in units:
            continue  # one companion unit: one shot, already kept together
        found.append(tuple(f.rel_path for f in rows))
    return tuple(sorted(found))


def duplicate_lines(report: DuplicateReport) -> list[str]:
    """Exact groups, then look-alikes, then what was compared and what pam will not do."""
    groups, files = len(report.groups), _files(report.duplicate_files)
    lines = [
        f"Duplicates in {report.archive_root}",
        "",
        f"Exact duplicates - byte-for-byte identical: {_groups(groups)}, {files}",
    ]
    if not report.groups:
        lines.append("  pam found no identical files here.")
    for index, group in enumerate(report.groups, start=1):
        lines.append(f"Group {index:,} - {_files(len(group.rel_paths))}, identical bytes")
        lines.extend(f"    {path}" for path in group.rel_paths[:PATH_CAP])
        if len(group.rel_paths) > PATH_CAP:
            lines.append(f"    ... and {len(group.rel_paths) - PATH_CAP:,} more")
    lines.append("")
    lines.append(
        "Look-alikes - photos that look the same (the same shot at two sizes, or edited"
        f" and unedited): {_groups(len(report.look_alikes))},"
        f" {_files(report.look_alike_files)}"
    )
    if not report.look_alikes:
        lines.append("  pam found no look-alikes among the photos it compared.")
    for index, paths in enumerate(report.look_alikes, start=1):
        more = f", ... and {len(paths) - PATH_CAP:,} more" if len(paths) > PATH_CAP else ""
        lines.append(f"  {index:,}. {', '.join(paths[:PATH_CAP])}{more}")
    rest = " Run pam enrich phash for the rest." if report.fingerprinted < report.images else ""
    lines.append(
        "pam compares the picture fingerprints `pam enrich phash` makes:"
        f" {report.fingerprinted:,} of {report.images:,} images have one.{rest}"
    )
    lines.append("Videos are not compared. pam deletes nothing - this is a list for you.")
    lines.append("")
    lines.append("Next: pam status   (what else pam knows about this folder)")
    return lines


def _groups(count: int) -> str:
    return f"{count:,} group" if count == 1 else f"{count:,} groups"


def print_duplicates(report: DuplicateReport) -> None:
    for line in duplicate_lines(report):
        click.echo(line)
