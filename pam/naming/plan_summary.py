"""`PlanSummary`: what one `pam plan` run reports (spec 6.3, 13), and its two methods.

Cut out of `pam/naming/planner.py` by M14 Task 5a, a pure move named before Task 5 edited the
planner (it was at 294 executable lines). `planner` imports it back, so every importer that names
`planner.PlanSummary` - `plan_report.py` and `events.py` (a type only) - is unchanged.
"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PlanSummary:
    total: int = 0
    planned: int = 0
    already_conforming: int = 0
    # spec 6.2 rule 7, and it is a DISPLAY ONLY (M10 Task 1, stage-2 review C2). The
    # count of plan items whose proposed name is the name the file already has - spec
    # 7.3 rev 16's no-op items, the members F12's fix puts in the plan so a companion
    # unit is never split. `planned` stays the number of plan ITEMS, because that is
    # what it means and what every other consumer reads; this is subtracted from it at
    # the one place a sentence claims files "would be renamed".
    #
    # NOTHING IS FROZEN BY COUNTING (plan clarification 2). No column is written, no
    # status is set, and apply re-decides every item's no-op-ness at preflight from its
    # CURRENT `new_filename` - which is the whole mechanism by which a typed name turns
    # a no-op back into a real rename. This number describes the plan AS WRITTEN, at the
    # moment it was written, which is exactly what the sentence that uses it claims.
    noop_items: int = 0
    skipped_placeholders: int = 0
    tiers: dict[str, int] = field(default_factory=dict)
    plan_id: int | None = None
    template: str = ""
    log_path: Path | None = None
    errors: list[str] = field(default_factory=list)
    # Finding 10: `errors` holds one entry per dropped UNIT, which is what the log
    # wants; the summary line counts FILES, so a failing live-photo pair does not
    # report "1 files". The two numbers are different questions.
    error_files: int = 0
    examples: list[tuple[str, str]] = field(default_factory=list)  # (old, new), first 3
    left_behind: int = 0  # stem-mates outside sidecar_exts (spec 6.3.3a)
    # spec 6.3 rule 5b rev 14: the events this run's step 0 grouped the archive into.
    # It counts EVENTS, not the files in them, and it is a fact about the whole dated
    # catalog rather than about the plan - a run that plans nothing still groups, so
    # this number is set on both exits from `_plan`.
    events: int = 0
    # C11 (m10.md), and it is a DISPLAY ONLY. The catalog rel_paths of `missing` rows -
    # GHOST ROWS, files pam still records and the disk no longer has - that hold a
    # `{seq}` number one of this plan's proposed names skipped. Nothing is written and no
    # behaviour changes: the numbers were already skipped, correctly and for the reason
    # `collision.taken_names`' docstring gives (a ghost row's name must stay held or
    # `set_file_path` raises and strands a journal row in `intent`, breaking J7).
    #
    # THE MISSING THING WAS A SENTENCE. At HT-M10 a `{seq}` number jumped `_001` -> `_002`
    # for a unit that looked alone on its date, and nothing pam printed could account for
    # it; the tester logged it unexplained. Vision principle 9 forbids the silence, not
    # the behaviour. Empty on every archive with no ghost rows, which is the normal state.
    blocked_by_missing: list[str] = field(default_factory=list)

    @property
    def had_errors(self) -> bool:
        """spec 13 rev 6: a command that recorded per-file errors exits 1, not 0."""
        return bool(self.errors)

    def record_error(self, message: str, files: int) -> None:
        """errors counts UNITS (for the log), error_files counts FILES (for the
        summary line). They are different questions - so they move together.

        The single entry point, so a future error site cannot bump one and forget
        the other: `_print_errors` gates on `errors` and prints `error_files`, and
        an unbumped counter would render "! 0 files could not be given a name".
        ScanSummary legitimately does the simpler thing (`len(self.errors)`),
        because there every entry IS one file - do not copy this pattern across.
        """
        self.errors.append(message)
        self.error_files += files
