"""Spec 8.2's write API: the four endpoints a review session decides and edits through.

THESE ARE THE ONLY WEB-REACHABLE WRITES IN THE PRODUCT, and what they write is what
`pam apply` renames irreplaceable files to. Nothing here touches a photograph - the whole
write API writes two columns of `plan_items` and nothing else - but a wrong value in
`new_filename` is a wrong name on an irreplaceable file one command later, so every
refusal is a safety property rather than an input-validation nicety.

THE SECTION IS THREE MODULES SINCE M10 TASK 2 (`m9.md` 5.2, finding F11), cut at the
author's own boundary and not anywhere new. This module owns the NAME endpoints - `rename`
and `restore` - and the route table naming all four. `decide_api.py` owns the DECISION
endpoints. `write_state.py` owns what both need: the action vocabulary, the `open`-plan
check, and the one expression of "the members of this unit". The grouping is the reason
the cut is drawn there; see `write_state._by_unit`.

DECISION STATE AND NAME STATE ARE SEPARATE AXES (spec 8.2 rev 12, amendment A6), and
this is the single most misreadable thing in the section. `decide`'s `reset` changes
STATUS ONLY - an edited name survives it. `restore` changes the NAME ONLY - an approval
survives it. Only `rename` writes both, and it writes `approved` deliberately, because
UX 5a's `C` is a decision and edit-then-approve-again would double-key every edit in a
900-item review. An implementation that made `reset` mean "undo everything the user did"
would be a different product.

VALIDATE EVERYTHING, THEN WRITE. Both endpoints here refuse in a first pass that touches
no row and write in a second: `rename` is one unit whose members must not be half-written,
which is a different failure mode from `decide`'s batch atomicity and is mutated
separately. Leaning on "we did not commit" for either would be leaning on sqlite3's
rollback to cover a bug in the order of two loops.
"""

import sqlite3
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pam.catalog import repo
from pam.catalog.repo.review import ReviewItem
from pam.naming import collision

# The request primitives this module must not own a second copy of - `counts` is the
# header arithmetic every write response carries back. Private names, imported inside the
# one package that owns them.
from pam.webui.api import (
    ApiRequest,
    _bad_request,
    _conflict,
    _not_found,
    counts,
    faces_floor,
)
from pam.webui.decide_api import decide, decide_filter
from pam.webui.disclosure import edited_name, not_renamed
from pam.webui.selection import decide_selection, preview_selection
from pam.webui.write_state import (
    APPROVE,
    TARGET_STATUS,
    TERMINAL_STATUSES,
    _by_unit,
    _open_plan_items,
)

if TYPE_CHECKING:  # pragma: no cover - import for typing only, never at runtime
    from pam.webui.server import ReviewServer


# --- reading the body ----------------------------------------------------------------


def _item_id(request: ApiRequest) -> int:
    """The `{id}` out of the path. Not an integer -> 404, because no such item exists."""
    raw = request.params["id"]
    if not raw.isdigit():
        raise _not_found(f"pam does not know an item with id {raw!r}")
    return int(raw)


def _new_filename(request: ApiRequest) -> str:
    value = request.body.get("new_filename")
    if not isinstance(value, str) or not value.strip():
        raise _bad_request("new_filename", "must be the name you want, extension included")
    return value


# --- rename and restore ---------------------------------------------------------------


def _unit(
    conn: sqlite3.Connection, item_id: int, floor: float
) -> tuple[ReviewItem, list[ReviewItem], int, list[ReviewItem]]:
    """The clicked item, its whole `unit_key` group, its plan id, and the plan.

    Amendment A6: the subject of an edit is the COMPANION UNIT, never one file. A
    per-item edit would hand `pam apply` a unit whose members rename to different stems,
    and spec 7.3 would revert the unit at best and split a companion group at worst
    (vision S3). `unit_key` is the plan-time grouping spec 6.3 rule 6 froze, never
    `files.companion_group_id`, which `pam scan` recomputes from scratch.

    The plan is whichever plan holds the item, not the one the server was started with:
    the read endpoints already answer for any plan id in the path, and `_open_plan_items`
    is what actually bounds what may be written - the plan must be `open` and must not be
    a pre-0004 plan `pam apply` would refuse (F10).
    """
    plan_ids = repo.plan_ids_for_items(conn, [item_id])
    if not plan_ids:
        raise _not_found(f"pam does not know an item with id {item_id}")
    plan_id = plan_ids[0]
    items = _open_plan_items(conn, plan_id, floor)
    clicked = next((item for item in items if item.id == item_id), None)
    if clicked is None:  # pragma: no cover - the id came out of this plan a line ago
        raise _not_found(f"pam does not know an item with id {item_id}")
    members = _by_unit(items)[clicked.unit_key]
    terminal = [f"{m.id} ({m.status})" for m in members if m.status in TERMINAL_STATUSES]
    if terminal:
        raise _conflict(
            "pam changed nothing. This file renames together with "
            + ", ".join(terminal)
            + ", which belongs to a finished run."
        )
    return clicked, members, plan_id, items


def _refuse_collisions(
    conn: sqlite3.Connection,
    items: list[ReviewItem],
    members: list[ReviewItem],
    proposed: dict[int, str],
    *,
    clash_reason: str,
    remedy: str = "",
    ignore_stem_suffixes: tuple[str, ...] = (),
) -> None:
    """Spec 6.3 rule 2's namespace AND NO NEW ONE (spec 8.2), case-insensitively.

    Z3 (design 6.3 rule 2, adopted by the owner 2026-11-13): the namespace holds STEMS as well as
    names. A stem whose spec 5.4 grouping key a file outside the unit holds in that folder - on
    disk, or another item's name in this plan - is refused, because the next scan would bind the
    two into one unit (finding 5f-F1, vision S3). Through `collision.StemKeys`, the planner's
    own definition, built from the same rows and reserved by the same `reserve` call: never a
    second copy. `ignore_stem_suffixes` is the bound archive's `scan.ignore_stem_suffixes`, which
    both endpoints pass; the refusal names the file that holds the stem.

    `remedy` is the caller's closing sentence, said after either clash.

    `clash_reason` is the caller's own closing sentence for the clash refusal below -
    "rule 2's namespace and no new one" (spec 8.2) is about the CHECK, not the COPY.
    `rename` and `restore` share the identical namespace computation but owe different
    remedies: a `rename` user typed a name and can try another; a `restore` user pressed
    the restore control and typed nothing, so the copy has to name the OTHER unit
    instead ("Restore or rename that one first"). No default - each caller states its
    own remedy rather than one silently inheriting the other's.

    THE DISK HALF IS EVERY CATALOG rel_path, not only the `present` ones, and
    `naming/planner.py` lines 229-243 record why in full: `files.rel_path` is UNIQUE and
    rows are never deleted, so a `missing` row holds its name for ever and proposing it
    plans a rename that SUCCEEDS on disk and then fails in `set_file_path`, leaving a
    journal row in `intent` that spec 7.2's recovery can never resolve - breaking J7 on a
    pass that runs at the start of every invocation. `collision.taken_names` is the
    planner's own builder, reused rather than re-derived.

    A NOTE ON WHAT THAT COSTS, because spec 8.2's parenthetical reads narrower. It spells
    the namespace as "the on-disk names there the plan is not renaming", which is spec
    6.3 rule 2's literal wording; the SHIPPED rule-2 implementation is deliberately more
    conservative, and `taken_names`' docstring records the reason - the literal reading
    emits plans containing swaps and cycles, which spec 7.1's absence check turns into
    order-dependent failures. Spec 8.2's operative instruction is "rule 2's namespace and
    NO NEW ONE", and building the literal reading would require writing a new namespace
    builder, which is precisely what that sentence forbids. The user-visible consequence,
    which is correct: a unit cannot be renamed onto a name another item is renaming AWAY
    from, and gets an explicable refusal instead of a rename that would have failed.

    THE UNIT'S OWN NAMES COME OUT OF BOTH HALVES FIRST, so that a legitimate re-edit
    does not collide with itself - including a CASE-ONLY stem change, which is legal
    and must succeed. WHICH discard does that work was measured, not assumed (M8 Task 2,
    rows M802-M804): the `old_filename` discard earns the save on its own, and deleting
    it alone fails exactly the test the whole guard fails. The `new_filename` discard is
    an EQUIVALENT mutant - `reserve` only ever adds names for OTHER units, so this unit's
    `new_filename` is never in the set to begin with. It is kept as defence in depth
    against a future caller that reserves more widely; it is not what makes re-edit work.
    """
    if len({name.lower() for name in proposed.values()}) != len(proposed):
        raise _conflict(
            "pam changed nothing. Two files that rename together would end up with the"
            " same name under that stem, so pam cannot use it."
        )
    unit_key = members[0].unit_key
    catalog_paths = [row.rel_path for row in repo.all_files(conn).values()]
    dirs = collision.taken_names(catalog_paths)
    keys = collision.StemKeys(catalog_paths, ignore_stem_suffixes)
    for item in items:
        if item.unit_key == unit_key:
            continue
        collision.reserve(
            dirs.setdefault(item.rel_dir, set()),
            [item.new_filename],
            keys=keys,
            rel_dir=item.rel_dir,
        )
    for member in members:
        taken = dirs.get(member.rel_dir)
        if taken is None:
            continue
        # Lower the candidate, never the set: `taken_names` and `reserve` both store
        # lowercased names, which is what makes the comparison case-insensitive on
        # Windows (`collision.assign_unique_stem`'s docstring says so).
        taken.discard(member.old_filename.lower())
        taken.discard(member.new_filename.lower())
    clashes = sorted(
        proposed[member.id]
        for member in members
        if proposed[member.id].lower() in dirs.get(member.rel_dir, set())
    )
    if clashes:
        raise _conflict(
            "pam changed nothing. "
            + ", ".join(clashes)
            + " is already taken in that folder - "
            + " ".join(part for part in (clash_reason, remedy) if part)
        )
    # The unit's own CURRENT names - on disk, as the planner's `own_names` - and so its own
    # current key (Z3.1). Not `new_filename`: `reserve` above adds only OTHER units' names,
    # so this unit's proposal is never a holder (Task 5g review Q2, M3230 equivalent), and
    # freeing its key would reach wider than the planner's rule.
    own = {m.old_filename.lower() for m in members}
    holders = sorted(
        {held for m in members for held in keys.holders(m.rel_dir, proposed[m.id], own)}
    )
    if holders:
        raise _conflict(
            "pam changed nothing. "
            + ", ".join(sorted(proposed.values()))
            + " would share a name with "
            + ", ".join(holders)
            + " in that folder, and pam would then treat them as one photo. "
            + remedy
        )


def rename_item(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """`POST /api/item/{id}/rename` - edit the stem of the item's whole companion unit.

    NO MEMBER ROW IS WRITTEN UNTIL EVERY CHECK HAS PASSED (spec 8.2). Every refusal above
    happens before the loop below, so a refused rename leaves every row byte-identical -
    a different failure mode from `decide`'s batch atomicity, and one the reviewer
    mutates separately.

    On success both axes move: every member's name, and every member's status to
    `approved` (spec 8.2 - UX 5a's `C` is a decision).

    THE RESPONSE CARRIES THE SUBMITTED NAME AND RULE 7'S SENTENCE (spec 6.2 rule 7,
    amendment A5), which `restore` does not and must not: a user who pressed `restore`
    typed nothing, so there is no name of theirs for pam to have changed. The sentence is
    chosen in `webui/disclosure.py`, where the rule that fired is known; the page renders
    it beside the submitted stem this response echoes back, so both halves of "you asked
    for this, pam stored that" come from one answer rather than half of it from a field
    the user may still be typing into.
    """
    item_id = _item_id(request)
    submitted = _new_filename(request)
    with server.connection() as conn:
        clicked, members, plan_id, items = _unit(conn, item_id, faces_floor(server))
        edited = edited_name(server, submitted, clicked, members)
        proposed = {m.id: f"{edited.stem}.{repo.ext_of(m.new_filename)}" for m in members}
        _refuse_collisions(
            conn,
            items,
            members,
            proposed,
            clash_reason="either by a file on disk or by another name in this plan.",
            remedy="Try a different name.",
            ignore_stem_suffixes=server.config.ignore_stem_suffixes,
        )
        for member in members:
            repo.set_plan_item_new_filename(conn, member.id, proposed[member.id])
            repo.set_plan_item_status(conn, member.id, TARGET_STATUS[APPROVE])
        conn.commit()
        fresh = repo.plan_rows(conn, plan_id, floor=faces_floor(server))
    response = _unit_response(clicked, plan_id, fresh)
    response["submitted_filename"] = submitted
    # TWO SENTENCES CAN BE TRUE OF ONE SUBMISSION (spec 6.2 rule 7, amendments A5 + B1),
    # and they are about different things: what pam stored instead of what was typed, and
    # which member that stored name will not actually move. `disclosure` picks ONE of its
    # three because they are three readings of one event; these two are two events, so
    # both are said. Joined into `note` rather than given a field of its own, because the
    # page already renders `note` at the field, which is where rule 7 puts it.
    response["note"] = " ".join(
        note
        for note in (
            edited.note,
            not_renamed([(m.old_filename, proposed[m.id]) for m in members]),
        )
        if note
    )
    return response


def restore_item(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """`POST /api/item/{id}/restore` - put the planner's own name back, for every member.

    STATUSES UNTOUCHED (amendment A6). Restoring is the filename operation and says
    nothing about whether the user has decided; an approved item that is restored stays
    approved.

    RESTORE GETS SPEC 6.3 RULE 2'S COLLISION CHECK, AS OF M8 TASK 2 (design spec rev 13,
    §8.2). Rev 12 granted restore none, on the reasoning that putting a name back "can
    only return the plan to a state it was already in" - that reasoning is false (M7
    close escalation E1; reproduced and recorded in `docs/implementation-notes/
    m8-progress.md`'s Task 2 section, this project's seventh measured "this cannot
    happen" docstring and the first where the sentence was not merely untested but
    wrong): rename unit A off its generated name, rename unit B ONTO that generated name
    (legal - `_refuse_collisions` reads the other items' `new_filename`, and a generated
    name is by definition not yet on disk, so A's sits in neither half of the
    namespace), then restore A unchecked. Two items of one plan would propose one
    filename in one directory.

    Same builder `rename` uses, same namespace, no new one - only the refusal copy
    differs, because a user who pressed restore typed no name and cannot "try a
    different" one; the copy names the OTHER unit instead (spec 8.2 rev 13's own
    wording).

    The proposed map is the name restore is INSTALLING (`generated_filename`), not the
    one it is replacing (`new_filename`) - the check is over what `restore` is about to
    write, exactly as `rename`'s is over the stem it is about to write.
    """
    item_id = _item_id(request)
    with server.connection() as conn:
        clicked, members, plan_id, items = _unit(conn, item_id, faces_floor(server))
        proposed = {m.id: m.generated_filename for m in members}
        _refuse_collisions(
            conn,
            items,
            members,
            proposed,
            clash_reason="another item in this plan is proposing it.",
            remedy="Restore or rename that one first.",
            ignore_stem_suffixes=server.config.ignore_stem_suffixes,
        )
        for member in members:
            repo.set_plan_item_new_filename(conn, member.id, member.generated_filename)
        conn.commit()
        fresh = repo.plan_rows(conn, plan_id, floor=faces_floor(server))
    return _unit_response(clicked, plan_id, fresh)


def _unit_response(
    clicked: ReviewItem, plan_id: int, fresh: list[ReviewItem]
) -> dict[str, Any]:
    """What the page needs to repaint: the unit as it now stands, plus fresh counts.

    Read back from the catalog rather than reported from what the handler intended to
    write - the response is the page's evidence that the write landed, and a response
    assembled from the request would say the same thing whether it had or not. It is also
    how spec 8.2's "if sanitization alters the stem, the response returns it" is honoured
    without a second rule: the page is told the name that is now stored.

    `edited` RIDES ALONG WITH THE TWO FILENAMES, from the same `ReviewItem` property
    `api._card` reads. A rename and a restore are the only two things that can change it,
    and the page repaints the unit from this response without re-fetching the card - so a
    response that carried the new names and not the new `edited` would leave `↺` and the
    trust display reading a stale predicate until the next reload, which is the precise
    disagreement spec 8.2 rev 14 makes `edited` a served field to prevent.
    """
    return {
        "item_id": clicked.id,
        "plan_id": plan_id,
        "unit_key": clicked.unit_key,
        "members": [
            {
                "id": item.id,
                "old_filename": item.old_filename,
                "new_filename": item.new_filename,
                "generated_filename": item.generated_filename,
                "edited": item.edited,
                # UX 5 rev 11: "if the user edits the unit's stem, the sentence goes
                # away". The page repaints the unit from this response without
                # re-fetching the card, so the predicate has to ride along with the
                # names it is computed from - exactly as `edited` does.
                "noop": item.noop,
                "status": item.status,
            }
            for item in fresh
            if item.unit_key == clicked.unit_key
        ],
        "counts": counts(fresh),
    }


# The four POST rows. `ReviewServer` merges this table with `api.HANDLERS`; see that
# table's comment for why they are two tables and not one. Rows, never router branches -
# `_match_route` reads `{id}` and `decide-filter` is an ordinary literal segment.
WRITE_HANDLERS: dict[tuple[str, str], Callable[["ReviewServer", ApiRequest], Any]] = {
    ("POST", "plan/{id}/decide"): decide,
    ("POST", "plan/{id}/decide-filter"): decide_filter,
    # M14 Task 3 (U17/U18): the selection's preview (READ-ONLY, a POST because a selection
    # is a body) and its decision - the one route that may approve a low item in bulk.
    ("POST", "plan/{id}/selection"): preview_selection,
    ("POST", "plan/{id}/decide-selection"): decide_selection,
    ("POST", "item/{id}/rename"): rename_item,
    ("POST", "item/{id}/restore"): restore_item,
}
