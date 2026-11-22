"""E6 (M15 Task 7, design 7.2, `m14.md` C9): recovery writes the report of an interrupted run.

HT-M14 F6: a renaming run that crashed was finished or put back by spec 7.2's recovery, and
then had no report at all - History said `No report`, and `pam report session` said the
report was missing. This module is what recovery does about it, AFTER its three phases have
all run (phase 2 can put back a member phase 1 just adopted, so a report written between them
would describe a state recovery was about to change).

DISCOVERY IS ON EVERY RUN, NOT ONLY FOR THIS RUN'S WORK. A session needs a report when it is
a RENAME session, none of its journal rows is still `intent`, and its report is not final
(`reporter.report_is_final`: missing, or begun by this pam and cut short). So a crash after
recovery resolved a session but before the report was written is caught by the next command,
and so is a session an older pam left without one. Three cases get NO report, by design:
  - an UNDO session: pam never writes a report for putting files back (HT-M6 H3), so a missing
    one says nothing about an interruption - and an interrupted undo is finished by running
    `pam undo` again (design 7.4);
  - a session with NO journal rows (a crash before the first intent, or a run that skipped
    every unit): there is nothing to recover and no session to discover (the maintainer's C6 ruling);
  - a report that is final: complete, unreadable, or legacy (written by a pam older than this
    one, with no format field). A final report is never touched.

"INTERRUPTED" ONLY FOR WHAT THIS PASS RECOVERED (Task 7 Stage 2, F1 and F2). Discovery cannot
tell a report the user deleted from one never written, and a report written late describes the
catalog as it stands now. So the `(interrupted)` title and the "stopped part-way" line are kept
for a session THIS pass resolved (phase 1), healed (phase 2) or finalized (phase 3, through its
plan); every other session gets the ordinary title and the neutral `FOUND_LINE`. The body is the
same either way.

THE CONTENT COMES FROM THE JOURNAL'S FINAL ROWS, never from what this run happened to do,
because the run that resolved the session may have been an earlier one. A failure to write is
logged and swallowed: recovery's journal work has already committed, and a report must never
take the command down.
"""

import logging
import sqlite3
from collections.abc import Set
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pam.catalog import repo
from pam.execute import reporter
from pam.paths import workspace_if_exists

# How much of a report `report_is_final` reads: the header sits in the first lines, and the end
# line is the last. Reading only the two ends keeps the check flat for a report of 100k failures.
_HEAD_BYTES = 64 * 1024
_TAIL_BYTES = 256

# The two sentences under the header (E6, ruling (b) and F1), spelled once.
STOPPED_LINE = (
    "- This run stopped part-way. pam recovered this session at {at} and wrote this report."
)
FOUND_LINE = (
    "- pam found no complete report for this session and wrote this one at {at}, from the"
    " catalog as it stood then."
)


@dataclass(frozen=True)
class InterruptedRun:
    """One interrupted renaming run, as its journal rows ended up. Every path is a rel_path.

    `unresolved` holds files under their NEW names in a group that is still split (spec 7.0
    J6): recovery puts them back as soon as it can, and until then they are neither renamed
    nor put back. Recovery's own word for them is "stranded"; the report says `unresolved`.
    `not_renamed` includes the plan's `failed` items with no journal row (S2-F1) - why each
    was refused is not recorded anywhere, so the report claims no reason - and its `approved`
    items the crash never reached (the controller's ruling (a)): the plan is terminal after
    recovery, so this plan will never rename them, and the same rule and wording apply.
    """

    plan_id: int | None
    started_at: str
    recovered_at: str
    renamed: tuple[tuple[str, str], ...]
    put_back: tuple[str, ...]
    not_renamed: tuple[str, ...]
    unresolved: tuple[str, ...]
    # F1: True only when THIS recovery pass resolved, healed or finalized the session.
    recovered: bool

    def title(self, session: str) -> str:
        """The report's first line: `(interrupted)` only for a session this pass recovered."""
        return f"# Renaming run {session}" + (" (interrupted)" if self.recovered else "")

    def body_lines(self, session: str) -> list[str]:
        """Everything below the report's format field, down to (not including) the end line."""
        lines = [f"- Started: {reporter._local(self.started_at)}"]
        if self.plan_id is not None:
            lines.append(f"- Proposed names from: plan {self.plan_id:,}")
        line = STOPPED_LINE if self.recovered else FOUND_LINE
        lines += [
            line.format(at=reporter._local(self.recovered_at)),
            "",
            "## What happened",
            "",
            f"- {len(self.renamed):,} file(s) renamed",
            f"- {len(self.put_back):,} file(s) put back under their original names",
            f"- {len(self.not_renamed):,} file(s) not renamed",
            f"- {len(self.unresolved):,} file(s) unresolved",
            "",
            "No file was deleted, moved out of the folder, or changed inside. pam renames"
            " in place and nothing else.",
            "",
        ]
        lines += _section("Files renamed", [f"`{old}` -> `{new}`" for old, new in self.renamed])
        lines += _section(
            "Files put back under their original names", [f"`{p}`" for p in self.put_back]
        )
        lines += _section("Files pam did not rename", [f"`{p}`" for p in self.not_renamed])
        lines += _section(
            "Files unresolved",
            [f"`{p}`" for p in self.unresolved],
            "These are under their new names while the group they belong to is not."
            " Nothing was lost. pam puts them back the next time it runs and can.",
        )
        if self.renamed:
            lines += [
                "## Undo",
                "",
                "This puts every file renamed above back under the name it had before.",
                "",
                f"    pam undo {session}",
                "",
            ]
        return lines


def _section(heading: str, entries: list[str], note: str | None = None) -> list[str]:
    """A `## ` section listing `entries`, or nothing at all when there are none."""
    if not entries:
        return []
    return [
        f"## {heading}",
        "",
        *([note, ""] if note else []),
        *(f"- {e}" for e in entries),
        "",
    ]


def interrupted_run(
    conn: sqlite3.Connection,
    session_id: str,
    now: str,
    recovered_sessions: Set[str],
    finalized_plans: Set[int],
) -> InterruptedRun:
    """The session as its journal rows ended up - spelled from the rows, item by item.

    `recovered` is whether this pass touched it: phases 1-2 name it, phase 3 names its plan."""
    rows = repo.session_rows(conn, session_id)
    items = repo.plan_items_by_id(conn, [row.plan_item_id for row in rows])
    split = {unit for sid, unit in repo.split_units(conn) if sid == session_id}
    renamed, put_back, not_renamed, unresolved = [], [], [], []
    for row in rows:
        item = items.get(row.plan_item_id)
        if row.state == "done" and item is not None and item.status == "undone":
            # Spec 7.0 J2: a rename row stays `done` for ever and `pam undo` records the put-back
            # on the ITEM - a session an older pam recovered without a report, then undone (Q10).
            put_back.append(row.old_rel_path)
        elif row.state == "done" and (item is None or item.unit_key in split):
            unresolved.append(row.new_rel_path)
        elif row.state == "done":
            renamed.append((row.old_rel_path, row.new_rel_path))
        elif row.state == "reverted":
            put_back.append(row.old_rel_path)
        elif row.state == "failed":
            not_renamed.append(row.old_rel_path)
        else:
            unresolved.append(row.old_rel_path)
    plan_ids = repo.plan_ids_for_items(conn, list(items))
    plan_id = plan_ids[0] if len(plan_ids) == 1 else None
    if plan_id is not None:
        not_renamed += repo.unjournaled_unrenamed_items(conn, plan_id)
    return InterruptedRun(
        plan_id=plan_id,
        started_at=min((row.intent_at for row in rows), default=""),
        recovered_at=now,
        renamed=tuple(renamed),
        put_back=tuple(put_back),
        not_renamed=tuple(not_renamed),
        unresolved=tuple(unresolved),
        recovered=session_id in recovered_sessions or plan_id in finalized_plans,
    )


def report_is_final(path: Path) -> bool:
    """E6: True when the report at `path` must never be written again.

    Final means COMPLETE (it has the format field and ends with `reporter.END_OF_REPORT`), LEGACY (no
    format field: an older pam wrote it, and legacy is final) or UNREADABLE (pam cannot tell,
    so it leaves the file alone). Only a missing file, or one with the field and without the
    end line - a write a crash cut short - is not final.
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(_HEAD_BYTES)
            size = handle.seek(0, 2)
            handle.seek(max(0, size - _TAIL_BYTES))
            tail = handle.read()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if not head.strip(b"\x00 \t\r\n"):
        return False  # empty or NUL-filled: what a power cut leaves, and no pam ever wrote that
    if not _has_format_field(head.decode("utf-8", "replace")):
        return True
    return tail.decode("utf-8", "replace").splitlines()[-1:] == [reporter.END_OF_REPORT]


def _has_format_field(head: str) -> bool:
    """Whether the report's HEADER - the lines before its first `## ` section - names a format."""
    for line in head.splitlines():
        if line.startswith("## "):
            return False
        if line.startswith(reporter.REPORT_FORMAT_FIELD):
            return True
    return False


def report_interrupted_runs(
    conn: sqlite3.Connection,
    archive_root: Path,
    logger: logging.Logger,
    recovered_sessions: Set[str],
    finalized_plans: Set[int],
) -> list[str]:
    """Write the report of every rename session that needs one; return their session ids.

    Called by `recover()` after all three phases, with the sessions its phases 1 and 2 resolved or
    healed and the plans its phase 3 finalized (F1: only those reports say "interrupted").
    Raises for nothing it writes: an OSError is logged and that session is left for the next
    run to try again.
    """
    ws = workspace_if_exists(archive_root)
    if ws is None:
        return []
    written = []
    now = datetime.now().isoformat(timespec="seconds")
    for counts in repo.sessions(conn):
        if counts.ops != (repo.OP_RENAME,) or counts.intent:
            continue
        if report_is_final(reporter.session_report_path(ws.reports_dir, counts.session_id)):
            continue
        run = interrupted_run(conn, counts.session_id, now, recovered_sessions, finalized_plans)
        try:
            path = reporter.write_recovery_report(ws, counts.session_id, run)
        except OSError as exc:
            logger.error(
                "recovery could not write the report of session %s: %s", counts.session_id, exc
            )
            continue
        written.append(counts.session_id)
        logger.warning("recovery wrote the report of session %s: %s", counts.session_id, path)
    return written
