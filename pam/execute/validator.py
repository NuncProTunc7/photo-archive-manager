"""Plan preflight: everything apply refuses BEFORE it writes anything (spec 7.1).

Two kinds of refusal live here, and the difference is deliberate.

A WHOLE-PLAN refusal raises `PamError`. The plan cannot be applied at all - it was
superseded, it was already applied, or it predates the schema apply needs - so there
is nothing to record against individual items and nothing to journal. `pam apply`
prints one line and exits 1 (spec 13). Nothing in the catalog changes, and in
particular **no journal row is written**: spec 7.0 J1 makes a journal row a promise
that pam is about to touch a file, and a refused plan touches none.

A PER-ITEM refusal marks that item `failed` and lets the rest of the plan run. One
stale item must not block an otherwise-good plan (vision principle 9: record, count,
continue). The item's journal history stays empty for the same reason as above.

This module reads. It never writes - `apply.py` owns the transaction that records
what preflight found, so that `--all`'s approvals (if any) and the refusals land together,
after the confirmation (F1 R1, R4).
"""

import dataclasses
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pam.catalog import repo
from pam.config import Config, load_config
from pam.errors import OLD_PLAN_REASON, PamError
from pam.naming import accept
from pam.paths import workspace_if_exists

# spec 4's plans.status enum, minus 'open'. Every other value means "not applicable
# now", and each says WHY in the user's language plus the command that moves them
# forward - UX spec 2: a refusal that does not say what to do next is a dead end.
_STATUS_REFUSALS = {
    "superseded": "Plan {plan_id} was superseded by a newer plan. Run: pam plan",
    "applying": (
        "Plan {plan_id} was interrupted while pam was applying it, so it cannot be"
        " applied again. Run: pam plan"
    ),
    "applied": "Plan {plan_id} has already been applied. Run: pam plan",
    "partially_applied": ("Plan {plan_id} has already been applied in part. Run: pam plan"),
    "undone": "Plan {plan_id} was undone. Run: pam plan",
}

# THE SENTENCE MOVED TO `pam/errors.py` (M10 Task 4, finding F10) and is re-exported
# here so that this module's readers still find it where they expect it. The review UI
# now refuses the same plan with the same words, and `pam/webui/` may not import anything
# under `pam.execute` - `tests/guards.py::ALLOWED_WEBUI_EXECUTE_IMPORTS` is spec 8.2's
# "the UI reads and approves; it never applies" made structural, and one shared string is
# not worth an entry in it. The reasoning about migration 0004 is at the definition,
# and it is imported at the top of this module.

# The ghost-row refusal (layer 2). `files.rel_path` is UNIQUE and rows are NEVER
# deleted, so a photo deleted outside pam leaves a `missing` row owning its name
# forever. If a plan hands that name to a DIFFERENT file, the rename SUCCEEDS on disk
# and `repo.set_file_path` then raises sqlite3.IntegrityError - leaving a journal row
# in `intent` beside a completed rename. spec 7.2's recovery adopts that row and hits
# the same constraint, so it can never be resolved: spec 7.0 J7 ("after recovery no
# row remains in intent") broken by a pass that runs at the start of every invocation.
#
# Layer 1 (the planner's `taken` set now covering every catalog rel_path, not only the
# `present` ones) means no plan this version builds can reach here. This layer keeps
# the invariant for plans built BEFORE that fix, which are still sitting in real
# workspaces, and it is the last check before the rename that would brick them.
GHOST_NAME_REASON = (
    "{old_rel_path} was left alone: pam's records still show a file named"
    " {new_rel_path}, so giving that name to another file would corrupt them."
    " Run: pam plan to rebuild the plan."
)

# spec 7.3: a companion group's members are renamed together or not at all. A member
# refused above therefore fails its whole unit - and because nothing has been renamed
# yet, that costs nothing and needs no revert (which is Task 7's job, for the harder
# case where a member has already moved).
#
# It carries its own `Run:` route (UX 2), and until rev 15 it did not need one: the only
# thing that could block a unit was a ghost name, whose causing member is refused in the
# same run with GHOST_NAME_REASON and the route on it. That is no longer true. A unit is
# now also blocked by a member apply will not act on at all, and such a member is not a
# candidate, so it earns no sentence of its own - leaving this one printed alone with
# nowhere to go. Its route is the same as the ghost's because the cause is the same: the
# plan is stale, and re-deciding it cannot help.
COMPANION_REASON = (
    "{old_rel_path} was left alone: another file it is grouped with could not be"
    " renamed, and pam renames a group together or not at all."
    " Run: pam plan to rebuild the plan."
)

# spec 7.3 rev 15's governing sentence - "a companion unit is DECIDED as a unit. Every
# member of a `unit_key` group carries the same decision, and `pam apply` never renames
# a proper subset of a unit" - for the case where nothing failed at all: the user
# approved one member and skipped another.
#
# COMPANION_REASON cannot serve this case. It says "could not be renamed", and for a
# skip that is simply untrue - nothing failed, pam was told not to. A refusal that
# misdescribes its own cause sends the user looking for a fault that is not there.
#
# It is printed alone - a skipped member is not itself refused, so no sibling sentence
# carries the route - and its route differs from COMPANION_REASON's because its cause
# does: nothing is stale, the user simply decided the unit two ways. `pam plan` and not
# "re-approve it": by the time this is printed, `_record_preflight` has already marked
# the refused members `failed` and the plan is on its way to `partially_applied`, so the
# plan the user just ran cannot be decided again.
SKIPPED_COMPANION_REASON = (
    "{old_rel_path} was left alone: another file it is grouped with was skipped, and"
    " pam renames a group together or not at all. Run: pam plan, then approve or skip"
    " the whole group together."
)

# spec 7.0 J12 / M10 condition C1: a no-op item whose file is not at `old_rel_path`.
#
# `is_noop` answers from the CATALOG'S BELIEF about where the file is, and until this
# check nothing verified that belief: `actionable()` filters a no-op item out before spec
# 7.1 step 1's existence check and before `execute_unit`'s, so an external rename between
# plan and apply - the case spec 7.4 explicitly contemplates - made pam record `applied`
# for a path that is not on disk, with spec 7.5's twelve checks reporting a clean 12/12.
#
# THE ROUTE IS `pam scan`, NOT `pam plan`. Nothing about the plan is stale; the CATALOG is.
# Re-planning against a catalog that still believes the old path proposes the same item
# again, so `pam plan` would send the user round a loop that cannot terminate.
NO_OP_MOVED_REASON = (
    "{old_rel_path} was left alone: it is not where pam last saw it, so pam cannot tell"
    " whether it still needs the name this plan gives it. Run: pam scan"
)

# spec 7.0 J13 (rev 26; the M11 review's R2): a unit whose `{people}` value is no longer
# the one `pam plan` would produce. Measured before J13: a person HIDDEN in Manage People -
# whose own panel says "not used in filenames" - or removed by `pam faces purge`, after the
# plan was made, reached disk through `pam apply` of that still-open plan.
#
# THE SENTENCE SAYS "CAN NO LONGER CONFIRM", NOT "HAS CHANGED", because it also covers a
# plan written before pam froze the named people into `tokens_json`: for that plan pam does
# not know whether anything changed, and a refusal must not claim more than pam knows.
# The route is `pam plan`, which proposes the name the catalog supports now.
STALE_PEOPLE_REASON = (
    "{old_rel_path} was left alone: pam can no longer confirm the people named in its new"
    " name - a person may have been hidden, renamed, removed or merged since this plan was"
    " made - so pam will not write that name. Run: pam plan"
)

# F1 (HT-M12, ruled 2026-10-08) - the refusals and the companion sentence its rules need.
# Spelled from the maintainer's ruling, R4 (`docs/superpowers/specs/2026-10-08-ht-m12-f1-approval-
# amendment.md`) and H1. Each names the command that moves the user forward (UX 2).
NOTHING_APPROVED = (
    "Nothing in plan {plan_id} is approved. Run pam review {plan_id} to approve names,"
    " or pam apply {plan_id} --all to accept everything except low-confidence names."
)
NOTHING_ACCEPTABLE = (  # --all, and the undecided names are all low-confidence or held by a skip
    "Nothing in plan {plan_id} can be accepted with --all: every name you have not decided"
    " is low-confidence ({low} of them) or belongs with a file you skipped."
    " Run pam review {plan_id} to approve them one at a time."
)
NOTHING_LEFT = (  # --all, and nothing in the plan is undecided at all
    "Nothing in plan {plan_id} is left to accept: you have skipped every name in it."
    " Run pam review {plan_id} to change that, or pam plan for a new plan."
)
# spec 7.3 rev 15 for a unit one of whose members is still UNDECIDED (F1 R1, R3): apply no
# longer acts on `proposed`, so an approved member beside an undecided one would otherwise be
# renamed alone. The page cannot produce that state (it widens every decision to the unit);
# a hand-edited catalog or a future write path could, and this is the containment.
UNDECIDED_COMPANION_REASON = (
    "{old_rel_path} was left alone: another file it is grouped with was not approved, and"
    " pam renames a group together or not at all. Run: pam plan, then approve or skip"
    " the whole group together."
)

# spec 7.1, F1 R1: the ONE status `pam apply` acts on. `proposed` used to be here too,
# because in M5 the typed confirmation WAS the approval (plan clarification 2) and apply
# flipped every `proposed` item itself - which is how the owner approved 5 names and
# watched 399 change (HT-M12 F1). The typed `apply` now confirms and no longer approves;
# `--all` writes its approvals explicitly (`apply._record_preflight`).
ACTIONABLE_STATUSES = ("approved",)


@dataclass(frozen=True)
class PlanPreflight:
    """What apply learned about a plan before touching anything.

    `items` is every row of the plan, in id order. `refusals` maps plan_item_id ->
    the sentence a human reads, and covers only items apply would otherwise act on.
    """

    plan_id: int
    items: tuple[repo.PlanItemRow, ...]
    refusals: dict[int, str]
    # `--all`'s approvals (F1 R4): the ids `accept.accept_all` chose, already `approved` in
    # `items` (in memory only) and written by apply AFTER the confirmation. Empty unless
    # `--all`.
    accepted: frozenset[int] = frozenset()
    # How many Low-tier ITEMS `--all` left undecided (F1 R2), for the confirmation's line.
    excluded_low: int = 0

    def actionable(self) -> list[repo.PlanItemRow]:
        """The items apply will try to rename: approved, not refused, not a no-op.

        `proposed` is NOT included (F1 R1): an undecided name is left as it is. Until M13
        it was, because in M5 the typed confirmation was the approval and apply flipped
        every `proposed` item itself - the defect HT-M12 found. `--all`'s choices reach
        here already `approved`, because `validate_plan` replaced them in `items`.

        NO-OP ITEMS ARE EXCLUDED (spec 7.0 J12): apply attempts no `os.rename` for them,
        so they must not reach `_units_of`, and they must not be counted as renames the
        user is about to authorise at UX 4's confirmation.
        """
        return [
            item
            for item in self.items
            if item.status in ACTIONABLE_STATUSES
            and item.id not in self.refusals
            and not is_noop(item)
        ]

    def noops(self) -> list[repo.PlanItemRow]:
        """The items apply will RESOLVE without touching the disk (spec 7.0 J12).

        The same three filters as `actionable`, with the last one the other way round,
        so the two partition the candidates and no item can fall into both or neither.
        A refused item is NOT here: spec 7.3's containment is about the unit, and an
        item the preflight refused is reported with the refusal's own sentence exactly
        as its siblings are.
        """
        return [
            item
            for item in self.items
            if item.status in ACTIONABLE_STATUSES
            and item.id not in self.refusals
            and is_noop(item)
        ]


def new_rel_path(item: repo.PlanItemRow) -> str:
    """The path this item proposes: the new filename in the old file's own directory.

    THE definition, shared by the validator and by apply, deliberately: two
    derivations of "where this item wants the file to be" could disagree, and the one
    that decided a name was free would not be the one that used it. A rename never
    changes the directory (UX spec 4, and `perform_verified_rename` refuses it), so
    the directory comes from `old_rel_path` and is never taken from anywhere else.
    """
    rel_dir, _, _ = item.old_rel_path.rpartition("/")
    return f"{rel_dir}/{item.new_filename}" if rel_dir else item.new_filename


def is_noop(item: repo.PlanItemRow) -> bool:
    """spec 7.0 J12: does this item propose the name its file already has?

    COMPUTED FROM THE ITEM'S CURRENT `new_filename`, NEVER FROZEN AT PLAN TIME, and that
    is the whole of why this is a function and not a `plan_items` column. spec 8.2's
    `rename` endpoint edits the whole unit's stem AFTER the plan was written, which turns
    a no-op item into an ordinary rename - and that is precisely the mechanism by which
    spec 7.3 rev 16's membership rule keeps a companion unit together. A stored `is_noop`
    flag, or a status the planner wrote, would make the review UI unable to turn it back
    and would reintroduce F12 in a new form.

    THE COMPARISON IS AGAINST `old_rel_path`, WHICH IS WHERE APPLY *BELIEVES* THE FILE IS,
    AND THIS FUNCTION DOES NOT CHECK THAT BELIEF - `_moved_no_op_refusals` DOES, in the
    preflight, before `blocked_units` is computed (M10 condition C1). That separation is
    deliberate: this predicate answers a question about two NAMES and is called from three
    places, one of which is spec 7.4's finalization, where the file has legitimately moved.

    Case-insensitive, matching spec 6.3.3, spec 6.3.2 and `perform_verified_rename`: a
    case-only rename is an item spec 7.1 is guaranteed to fail, and an identical-name
    rename fails harder, because `os.rename` refusing an existing target is the safety
    asset spec 7.0 J8 rests on.

    ONE DEFINITION, THREE CONSUMERS - the preflight here, apply's resolution, and spec
    7.4's plan finalization. Three copies of "did this item move a file" is how the
    counts, the statuses and the plan's terminal status come to disagree.
    """
    return new_rel_path(item).lower() == item.old_rel_path.lower()


def validate_plan(
    conn: sqlite3.Connection, plan_id: int, *, root: Path, accept_all: bool = False
) -> PlanPreflight:
    """Refuse what cannot be applied; report what must be skipped. Writes nothing.

    Raises PamError for a whole-plan refusal (spec 13: one clear line, exit 1) - including
    a plan with nothing approved in it (F1 R4).

    `accept_all` is `pam apply --all` (F1 R4, M13-D1): `accept.accept_all` chooses the
    undecided, non-Low, whole units, and they are treated as `approved` IN MEMORY. The
    preflight still writes nothing; apply writes them after the confirmation.

    `root` IS REQUIRED AND HAS NO DEFAULT, deliberately. It is the archive root spec 3
    resolved, and `_moved_no_op_refusals` asks the disk about paths relative to it. A
    defaulted `root` is one a future caller forgets to pass while the check goes on
    answering confidently about the wrong directory - and the answer it would give is
    "the file is not there", i.e. it would refuse an entire archive for free.
    """
    status = repo.plan_status(conn, plan_id)
    if status is None:
        raise PamError(f"pam has no plan {plan_id}. Run: pam plan")
    if status != "open":
        template = _STATUS_REFUSALS.get(
            status, "Plan {plan_id} is not open, so it cannot be applied. Run: pam plan"
        )
        raise PamError(template.format(plan_id=plan_id))

    items = tuple(repo.plan_items(conn, plan_id))
    if any(not item.unit_key for item in items):
        raise PamError(OLD_PLAN_REASON.format(plan_id=plan_id))

    acceptance = accept.accept_all(items) if accept_all else accept.Acceptance(frozenset(), 0)
    # IN MEMORY ONLY: this module writes nothing, and `--all`'s approvals must not exist
    # until the user has typed `apply` (apply writes them, in the refusals' transaction).
    items = tuple(
        dataclasses.replace(item, status="approved") if item.id in acceptance.ids else item
        for item in items
    )
    # F1 R4. BEFORE `_item_refusals`, so a plan nobody approved is refused whole, with one
    # line and nothing written - never item by item.
    if not any(item.status == "approved" for item in items):
        raise PamError(_nothing_to_apply(plan_id, items, acceptance, accept_all=accept_all))

    return PlanPreflight(
        plan_id=plan_id,
        items=items,
        refusals=_item_refusals(conn, root, items),
        accepted=acceptance.ids,
        excluded_low=acceptance.excluded_low,
    )


def _nothing_to_apply(
    plan_id: int,
    items: tuple[repo.PlanItemRow, ...],
    acceptance: accept.Acceptance,
    *,
    accept_all: bool,
) -> str:
    """Which of F1 R4's three sentences: plain apply, `--all` with nothing left undecided,
    or `--all` whose undecided names are all low-confidence or held by a skip."""
    if not accept_all:
        return NOTHING_APPROVED.format(plan_id=plan_id)
    if not any(item.status == accept.UNDECIDED for item in items):
        return NOTHING_LEFT.format(plan_id=plan_id)
    return NOTHING_ACCEPTABLE.format(plan_id=plan_id, low=acceptance.excluded_low)


def _item_refusals(
    conn: sqlite3.Connection, root: Path, items: tuple[repo.PlanItemRow, ...]
) -> dict[int, str]:
    """Per-item refusals, then their companions (spec 7.3 rev 15).

    THE TWO COMPANION SETS ARE COMPUTED OVER DIFFERENT DOMAINS, AND THAT IS THE POINT.

    `blocked_units` is computed over the CANDIDATES, because a ghost-name refusal is
    something only a candidate can earn - the check asks what an item is about to be
    renamed to, and an item nothing is renaming proposes nothing.

    `decided_out_units` is computed over ALL of the plan's items, because a member the
    run will not touch is exactly the one the narrow domain could not see. Until rev 15
    both sets were the candidates, so a `skipped` member had already left the set the
    rule "renamed together or not at all" was computed over: approve a `.HEIC`, skip its
    `.MOV`, apply, and one unit became two stems on disk with `pam verify` reporting a
    clean 12/12. The next `pam scan` then set `companion_group_id` to NULL for both
    (spec 5.4 groups by stem) and the next plan proposed `_002` for the orphan -
    separated, differently numbered, and unrecoverable by any pam command (vision S3).

    Refusing here costs nothing: no file has moved, no journal row exists, and there is
    nothing to revert. That is why the containment lives in the preflight rather than in
    apply's loop.

    EVERY non-actionable status blocks its unit, not only `skipped`, because spec 7.1
    rev 15 renames a unit whose members are ALL actionable and says nothing about how a
    member stopped being one. `skipped` is the ordinary case (M7's review UI, spec 8.2),
    and `proposed` - undecided - has its own sentence since F1 R1 (M13), ranked below the
    skip. Neither is a failure, so neither reads as COMPANION_REASON.
    `failed` is reachable in one narrow window - a power cut between
    `_record_preflight`'s commit and the `applying` write leaves an open plan carrying
    items this preflight refused last time - and COMPANION_REASON says exactly the right
    thing about it, route included (a stale plan is rebuilt). `applied` and
    `undone` are written only while a plan is `applying` (spec 7.1, 7.4), so they cannot
    reach an `open` plan at all; they are grouped with `failed` rather than ignored,
    because the safe reading of an unexpected status is "do not rename this unit".
    """
    candidates = [item for item in items if item.status in ACTIONABLE_STATUSES]
    refusals = _ghost_name_refusals(conn, candidates)
    # MERGED BEFORE `blocked_units` IS COMPUTED, AND THAT ORDERING IS THE WHOLE SAFETY
    # ARGUMENT. `blocked_units` is read once, from `refusals` as it stands at that moment;
    # a moved no-op member added AFTER it would be refused alone, its companion would be
    # renamed, and `pam apply` would leave the unit split across two stems - which spec
    # 7.3's closing sentence and vision S3 forbid absolutely. Merging here buys spec 7.3's
    # containment with no new code and no fourth companion sentence.
    refusals.update(_moved_no_op_refusals(root, items))
    # J13, merged here for J12's reason: before `blocked_units` is read, so a refused member
    # takes its whole unit with it. Every member of a unit carries the same `tokens_json`
    # (spec 6.3: tokens are computed once per unit), so each is normally refused on its own
    # account too - this ordering is what makes that a convenience rather than the guarantee.
    refusals.update(_stale_people_refusals(conn, root, candidates))
    blocked_units = {item.unit_key for item in candidates if item.id in refusals}
    blocked_units |= {
        item.unit_key
        for item in items
        if item.status not in ACTIONABLE_STATUSES and item.status not in ("skipped", "proposed")
    }
    decided_out_units = {item.unit_key for item in items if item.status == "skipped"}
    # F1 R1/R3: an undecided member is not renamed, so its approved siblings must not be.
    undecided_units = {item.unit_key for item in items if item.status == "proposed"}
    for item in candidates:
        if item.id in refusals:
            continue
        # Precedence blocked > skipped > undecided. `blocked_units` wins where a unit is in
        # both: a ghost name or a member that already failed is not fixed by deciding the
        # unit again - the plan is stale and must be rebuilt - and the sibling refusal
        # printed beside this one says so. A skip beats an undecided member because it is
        # the user's own decision, and the sentence about it is the truer one.
        if item.unit_key in blocked_units:
            refusals[item.id] = COMPANION_REASON.format(old_rel_path=item.old_rel_path)
        elif item.unit_key in decided_out_units:
            refusals[item.id] = SKIPPED_COMPANION_REASON.format(old_rel_path=item.old_rel_path)
        elif item.unit_key in undecided_units:
            refusals[item.id] = UNDECIDED_COMPANION_REASON.format(
                old_rel_path=item.old_rel_path
            )
    return refusals


def _moved_no_op_refusals(root: Path, items: tuple[repo.PlanItemRow, ...]) -> dict[int, str]:
    """spec 7.1 step 1's existence check, for the items `actionable()` filters out.

    ONE `stat` PER NO-OP ITEM. Every other candidate meets step 1 inside
    `perform_verified_rename`, immediately before the move; checking them again here
    would be a second answer taken at a different moment, and the one that mattered would
    not be this one.
    """
    return {
        item.id: NO_OP_MOVED_REASON.format(old_rel_path=item.old_rel_path)
        for item in items
        if item.status in ACTIONABLE_STATUSES
        and is_noop(item)
        and not (root / item.old_rel_path).exists()
    }


def _stale_people_refusals(
    conn: sqlite3.Connection, root: Path, candidates: list[repo.PlanItemRow]
) -> dict[int, str]:
    """spec 7.0 J13: refuse a `{people}` value the catalog would no longer produce.

    RECOMPUTED EXACTLY AS `pam plan` WOULD NOW: `repo.people_by_sha256` at `[tiers]
    faces.floor`, capped at `[naming] max_people`, for the unit's PRIMARY (spec 6.1 rev 26:
    a unit's people are its primary's) - whose rel_path is the second half of `unit_key`,
    which the plan froze, so no current grouping is consulted. Compared against the
    `named` list the planner froze on the token. Any difference refuses; so does a token
    with no `named` list at all, which is a plan made before J13 existed.

    EXEMPT: an item that will not write (a no-op, J12), and an item whose name a person
    EDITED (`new_filename != generated_filename`) - text a person typed is user truth
    (spec 8.2's manual rename), and pam does not second-guess it.

    The config is read only when some candidate carries a `{people}` token, so an archive
    that has never met a face pays nothing and meets no new failure here.
    """
    carrying = [
        (item, entry)
        for item in candidates
        if not is_noop(item)
        and item.new_filename == item.generated_filename
        and (entry := _people_entry(item.tokens_json)) is not None
    ]
    if not carrying:
        return {}
    ws = workspace_if_exists(root)
    config = load_config(ws.config_path) if ws is not None else Config()
    people = repo.people_by_sha256(conn, config.tiers["faces"].floor)
    catalog = repo.all_files(conn)
    refusals: dict[int, str] = {}
    for item, entry in carrying:
        primary = catalog.get(item.unit_key.partition("\x1f")[2])
        sha = primary.sha256 if primary is not None else None
        now = [(p.id, p.filename_value) for p in people.get(sha or "", ())[: config.max_people]]
        frozen = entry.get("named")
        if sha is None or frozen is None or now != [(i, v) for i, v in frozen]:
            refusals[item.id] = STALE_PEOPLE_REASON.format(old_rel_path=item.old_rel_path)
    return refusals


def _people_entry(tokens_json: str) -> dict[str, Any] | None:
    """The `{people}` token's entry in a plan item's `tokens_json`, or None if it has none.

    Matched on `resolved_from`, the token that SUPPLIED the value, never on the slot.

    A `tokens_json` that does not parse answers `{}` - an entry with no `named` list - so
    the item is REFUSED rather than waved through: pam cannot tell whether a name it cannot
    read carries a person, and J13's whole premise is not writing a name it cannot confirm.
    Every plan pam writes parses, so this costs nothing but a guess never made.
    """
    try:
        entries = json.loads(tokens_json)
    except (TypeError, ValueError):
        return {}
    if not isinstance(entries, list):
        return {}
    for entry in entries:
        if isinstance(entry, dict) and entry.get("resolved_from") == "people":
            return entry
    return None


def _ghost_name_refusals(
    conn: sqlite3.Connection, items: list[repo.PlanItemRow]
) -> dict[int, str]:
    """Items proposing a name some OTHER `files` row already owns. See GHOST_NAME_REASON.

    The comparison is case-INSENSITIVE, matching spec 6.3.2 and `perform_verified_rename`.
    The UNIQUE index on `files.rel_path` is BINARY, so a ghost differing only in case
    raises nothing at all: it writes a second row whose path differs from the first
    only by case, which spec 7.5's check 6 classifies as catalog corruption on Windows,
    on a filesystem where the two names cannot both exist. Case-sensitive here would
    trade a loud IntegrityError for a silent corruption.
    """
    # LOAD-BEARING: `repo.all_files` must keep returning rows of EVERY status,
    # `missing` included. A ghost is by definition a `missing` row, so narrowing that
    # query to live files would silently disable this whole check - and the defect it
    # guards against is unresolvable once it fires (see GHOST_NAME_REASON above).
    # `tests/test_validator.py::test_all_files_still_returns_missing_rows` pins it.
    owners: dict[str, list[int]] = {}
    for row in repo.all_files(conn).values():
        owners.setdefault(row.rel_path.lower(), []).append(row.id)
    refusals: dict[int, str] = {}
    for item in items:
        proposed = new_rel_path(item)
        # Its OWN row owning the name is the ordinary case, not a ghost: every item's
        # file is in `files`, and a case-only difference against itself is the
        # planner's already-conforming path, refused again in rename.py.
        if any(file_id != item.file_id for file_id in owners.get(proposed.lower(), [])):
            refusals[item.id] = GHOST_NAME_REASON.format(
                old_rel_path=item.old_rel_path, new_rel_path=proposed
            )
    return refusals
