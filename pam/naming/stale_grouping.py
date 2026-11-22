"""`pam plan` refuses over an unfinished grouping (M14 Task 5h, Z2; vision S3).

Record: `docs/superpowers/specs/2026-11-13-m14-r1-f1-amendment.md`, section Z2. A Ctrl+C or a
crash during a console `pam scan`, between its reads and its grouping commit (review R1's F1,
route (c), reachable since M2), leaves a catalog whose companion units are not grouped. A plan
built over it makes each member its own unit, and `pam apply` then renames a Live Photo's photo
and video apart for good. Editing `scan.sidecar_exts` or `scan.ignore_stem_suffixes` in `pam.toml`
and planning before re-scanning planned over the old grouping the same way.

So `plan_locked` calls `refuse_a_stale_grouping` AFTER spec 7.2's recovery (which may correct a
`rel_path`) and before `_plan` writes anything. It is its own module because `planner.py` was at
285 of its ~300 executable lines (the brief's named cut).

WHICH ROWS, CASE BY CASE, against `scan/runner.py`'s `disk` list (the walk) that the last
complete scan grouped:
- a walked file has a `present` row: new and changed files are upserted `present`, unchanged
  ones are touched `present`, so a file that came back is `present` too;
- a row the walk did not see is marked `missing` in the same scan, so `missing` rows are out;
- a cloud placeholder is walked and stays `present` (`placeholder = 1`), so it is in;
- the media type is the walk's own classifier (`walker.ext_of` + `walker.media_type_for`) under
  the CURRENT config, never the stored `files.media_type`: a `pam.toml` edit that adds a sidecar
  extension leaves those rows `other` until a re-scan, and reading the stored type would miss
  exactly the edit this check exists to catch (the record's binding bullet).
"""

from pathlib import Path
from sqlite3 import Connection

from pam.catalog import repo
from pam.config import Config
from pam.errors import PamError
from pam.scan import companions, walker

# Z2's sentence, verbatim from the record and design 12. `{root}` is the archive root.
STALE_GROUPING = (
    "pam's companion groups are out of date - the last scan of {root} did not finish, or pam.toml"
    " changed since. Run: pam scan {root}"
)


def refuse_a_stale_grouping(conn: Connection, root: Path, config: Config) -> None:
    """Raise `PamError(STALE_GROUPING)` while the stored grouping differs from 5.4's.

    5.4's grouping is computed by `companions.decide_groups` - the ONE definition `pam scan`'s
    grouping block stores - over the `present` rows of a grouped media type (module docstring),
    with the catalog's every row as `known`, exactly as the scan passes its `all_files`. The
    comparison is membership and kind, never group ids (5.1 rev 6). Reads only; writes nothing.
    """
    rows = repo.all_files(conn)
    paths = [
        rel
        for rel, row in rows.items()
        if row.status == "present"
        and walker.media_type_for(walker.ext_of(Path(rel)), config)
        in companions.GROUPED_MEDIA_TYPES
    ]
    groups = companions.decide_groups(
        paths, rows, config.sidecar_exts, config.ignore_stem_suffixes
    )
    computed = {(g.kind, frozenset(g.member_rel_paths)) for g in groups}
    if computed != repo.companion_membership(conn):
        raise PamError(STALE_GROUPING.format(root=root))
