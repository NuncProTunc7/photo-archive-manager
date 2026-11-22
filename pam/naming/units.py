"""Rename units: a companion group, or a lone file (design spec 5.4/6.3).

Split out of `planner.py` at M6 Task 1, along the cut the M5 review recorded
(`docs/implementation-notes/m5.md` section 2.5). The planner was the one module over
the rev-10 ~300-executable-line budget and had been on the agenda since the M4 gate.

This is the grouping half: what a rename unit IS, how the catalog's rows become
units, and how sequence numbers are allocated across them. Pure - it reads rows and
returns values, touching no filesystem, no connection and no log.
"""

from dataclasses import dataclass

from pam.catalog import repo
from pam.naming.tokens import date_value


@dataclass(frozen=True)
class Unit:
    """One rename unit: a companion group, or a lone file (spec 5.4/6.3)."""

    primary: repo.PlanFile
    members: tuple[repo.PlanFile, ...]  # includes the primary


def _units(files: list[repo.PlanFile], primaries: dict[int, int]) -> tuple[list[Unit], int]:
    """Group companion members; drop units containing a placeholder."""
    groups: dict[int, list[repo.PlanFile]] = {}
    units: list[Unit] = []
    skipped = 0
    for f in files:
        if f.companion_group_id is None:
            if f.placeholder:
                skipped += 1
                continue
            units.append(Unit(primary=f, members=(f,)))
        else:
            groups.setdefault(f.companion_group_id, []).append(f)
    for group_id, members in groups.items():
        members.sort(key=lambda m: m.rel_path)
        if any(m.placeholder for m in members):
            # Half-renaming a companion unit would break the group invariant, so
            # one placeholder member skips the whole unit.
            skipped += len(members)
            continue
        primary_id = primaries.get(group_id)
        primary = next((m for m in members if m.id == primary_id), members[0])
        units.append(Unit(primary=primary, members=tuple(members)))
    units.sort(key=lambda u: u.primary.rel_path)
    return units, skipped


def _seq_numbers(
    units: list[Unit],
    events: dict[int, repo.EventMembership],
    pre_pam: dict[int, str] | None = None,
) -> dict[int, int]:
    """spec 6.1 rev 14: 3-digit, ordered by (capture_dt, old filename), scoped per
    (event_id, date).

    M8 Task 9 is the milestone this docstring used to name and wait for. The scope is
    now what spec 6.1 says it is, and rev 14 makes it TOTAL: "a file that belongs to no
    event scopes by date alone". `None` is that scope's event half - it is not a
    sentinel event and it is not an event every ungrouped file shares. Two ungrouped
    files on one date land in `(None, date)` together, which IS scoping by date alone;
    an ungrouped file and a grouped one on the same date do not, because the grouped
    one's key carries a real id.

    `events` maps file id -> membership and is looked up by the unit's PRIMARY. A unit
    renames as one stem from the primary's facts (spec 5.4/6.3), so its {seq} must come
    from the primary's scope; a companion sidecar has no `capture` row, is therefore in
    spec 8.3's grouping input for no event at all, and must not be allowed to pull its
    unit into a different scope than the photograph it belongs to.

    Ordering within a scope is unchanged: `(capture_dt, old filename)`. Only the
    partition moved.

    HT-M14 F1 (AM6): "old filename" is the name BEFORE pam. `pre_pam` maps a primary's id to
    the rel_path it had before pam renamed it, for files still at pam's name. Ordering by the
    CURRENT rel_path made a re-plan after an apply swap numbers pam had just given (`b_001`,
    `a_002` re-sorted as `a_002`, `b_001`), because pam's names sort by their tokens, not by
    the originals. The current rel_path stays the last key, so the order is still total.

    THE NUMBERS THIS RETURNS ARE NOT FINAL. They are the number spec 6.3 rule 2 rev 6
    calls "the unit's own current number" - the one appended on a first collision, and
    only incremented on a further one. `collision.assign_unique_stem` owns that loop,
    and with M8's empty labels it is what stands between two events on one day and two
    plan items proposing one filename.
    """
    by_scope: dict[tuple[int | None, str], list[Unit]] = {}
    for unit in units:
        member = events.get(unit.primary.id)
        scope = (
            member.event_id if member is not None else None,
            date_value(unit.primary.capture_dt),
        )
        by_scope.setdefault(scope, []).append(unit)
    out: dict[int, int] = {}
    for group in by_scope.values():
        ordered = sorted(
            group,
            key=lambda u: (
                u.primary.capture_dt or "",
                (pre_pam or {}).get(u.primary.id, u.primary.rel_path),
                u.primary.rel_path,
            ),
        )
        for number, unit in enumerate(ordered, start=1):
            out[unit.primary.id] = number
    return out


def held_names(
    files: list[repo.PlanFile], written: dict[int, repo.PrePamName]
) -> dict[int, repo.PrePamName]:
    """HT-M14 F1 (AM6): the files still at the name pam last gave them, by id.

    `written` is `repo.pre_pam_names`. A file is held while it is at pam's path (case-
    insensitively, design 6.3.2). SIZE IS DELIBERATELY NOT PART OF IT, unlike
    `repo.name_evidence`: an edit to the photo in place keeps the catalog row and changes its
    size, and the NAME is still the one pam gave or the user typed - renaming a photo because it
    was edited is the churn F1 is about. (Evidence is a different question: whether a name's
    date describes these bytes.) A rename outside pam gives the file a NEW catalog row, which
    `written` does not hold, so the path test is defence rather than a reachable branch.
    """
    return {
        f.id: entry
        for f in files
        if (entry := written.get(f.id)) is not None
        and entry.pam_path.casefold() == f.rel_path.casefold()
    }
