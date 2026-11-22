"""Spec 8.2's two decision endpoints: `decide` and `decide-filter`.

One half of the write API; `write_api.py` holds the other and the route table, and
`write_state.py` holds the vocabulary and the grouping both halves share. Everything
`write_api.py`'s module docstring says about these being the only web-reachable writes in
the product, and about decision state and name state being separate axes, governs here
too - this module is the decision axis.

THE LOW-TIER CONTRACT IS SERVER-SIDE, AND IT LIVES ON EXACTLY ONE ENDPOINT.
`decide-filter` never approves a `low`-tier item: an explicit `tier: 'low'` is refused
outright and a broader filter silently excludes them and says how many. `decide`, which
names its ids, carries NO tier restriction at all - UX 5a step 2 is the user clearing the
attention items one at a time with `X`, and most of those items are `low`. The contract
is about bulk, not about the tier, and adding a tier check to `decide` would break Smart
Review while looking like extra care. Enforced here rather than in the page because the
server's entire security model is that the browser is untrusted (spec 8.2's token gate):
a page that can POST can POST `{tier: 'low', action: 'approve'}`.

VALIDATE EVERYTHING, THEN WRITE. Both endpoints refuse in a first pass that touches no row
and write in a second: a decision is a batch that must be all-or-nothing across ids, and
leaning on "we did not commit" would be leaning on sqlite3's rollback to cover a bug in
the order of two loops.
"""

import sqlite3
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from pam.catalog import repo
from pam.catalog.repo.review import ReviewItem
from pam.naming.accept import accept_all, exclude_low_units
from pam.webui import listing

# The request primitives and the two shared definitions this module must not own a second
# copy of: `_item_filter` is how a filter field is validated (spec 8.2's five filters,
# one validator for the query string and the body alike) and `counts` is the header
# arithmetic every write response carries back. Private names, imported inside the one
# package that owns them.
from pam.webui.api import (
    ApiRequest,
    _bad_request,
    _conflict,
    _item_filter,
    _list_group,
    _plan_id,
    counts,
    faces_floor,
)
from pam.webui.disclosure import decided_unit
from pam.webui.write_state import (
    ACTIONS,
    APPROVE,
    TARGET_STATUS,
    TERMINAL_STATUSES,
    _open_plan_items,
    _widen_to_units,
)

if TYPE_CHECKING:  # pragma: no cover - import for typing only, never at runtime
    from pam.webui.server import ReviewServer


LOW_TIER = "low"

# What `_filter_request` carries from a JSON body into `_item_filter`. Spec 8.2's
# `decide-filter` body names four of these; `status` and `sort` come along because the
# validator is shared and one definition of "a filter field" is the whole point (a
# `?tier=nonsense` 400 that a body-borne `{"tier": "nonsense"}` does not get is a bug).
# `sort` cannot change WHICH items a bulk decision selects, only their order.
FILTER_FIELDS = ("group", "tier", "status", "attention", "q", "sort")

# M13 V3: every field `decide-filter`'s body may carry - the filter fields plus the two that
# are not filters. A field outside this set is refused by name (`_refuse_unknown_fields`):
# `_filter_request` reads only the names it knows, so a mistyped or unsupported filter would
# otherwise be silently dropped and the bulk decision would act on a BROADER set than the
# page asked for.
BODY_FIELDS = (*FILTER_FIELDS, "action", "accept_all", "shortcut", "list_group")

# UX 8's plain language. The refusal must say what pam will not do AND what the user can
# do instead - a refusal with no way forward is UX 1.4's dead end.
LOW_BULK_REFUSAL = (
    "pam does not approve low-confidence names in bulk. These are the names most likely"
    " to be wrong, so pam asks you to look at each one. Approve them one at a time, or"
    " skip them."
)


# --- reading the body ----------------------------------------------------------------


def _action(request: ApiRequest) -> str:
    value = request.body.get("action")
    if not isinstance(value, str) or value not in ACTIONS:
        raise _bad_request("action", f"must be one of {', '.join(ACTIONS)}")
    return value


def _item_ids(request: ApiRequest) -> list[int]:
    """Spec 8.2's `item_ids`, checked element by element before anything is looked up.

    `bool` is an `int` in Python and `True` is not an item id, so it is refused by name -
    the same trap `review._orientation` documents on the way in.
    """
    value = request.body.get("item_ids")
    if not isinstance(value, list):
        raise _bad_request("item_ids", "must be a list of item ids")
    ids: list[int] = []
    for entry in value:
        if isinstance(entry, bool) or not isinstance(entry, int):
            raise _bad_request("item_ids", f"{entry!r} is not an item id")
        ids.append(entry)
    return ids


def _refuse_unknown_fields(request: ApiRequest) -> None:
    """V3: a body field `decide-filter` does not know is a 400 naming it; nothing is written.

    Runs before the branch split, so the filter branch and `Accept all` share it. When more
    than one field is unknown the first in SORTED order is named, so the answer does not
    depend on the order the page happened to serialise its body in.
    """
    _refuse_unknown_names(request.body, BODY_FIELDS)


def _refuse_unknown_names(body: Mapping[str, Any], known: tuple[str, ...]) -> None:
    """V3's refusal over any JSON object and its known field set - ONE copy of the sentence.

    `decide-filter`'s body is one caller; M14's `selection` routes are the others (their
    body, the `selection` object and its `view`), each with its own literal field set.
    """
    unknown = sorted(name for name in body if name not in known)
    if unknown:
        raise _bad_request(unknown[0], f'"{unknown[0]}" is not something pam can filter by.')


def _filter_request(request: ApiRequest) -> ApiRequest:
    """The body's filter fields, shaped like a query string so `_item_filter` reads them.

    ONE definition of how a filter field is validated, and one definition of which items
    a filter selects: `decide-filter` resolves its filter through `_item_filter` and
    `repo.select_items`, exactly the pair `/plan/{id}/items` uses, so the bulk decision
    can never act on a different set from the one the user was looking at.

    JSON has a boolean and a query string does not, so `attention: true` is translated
    into the `1` that `_attention` reads. That is a transport difference, not a second
    rule: the VALIDATION still happens in one place.
    """
    query: dict[str, list[str]] = {}
    for name in FILTER_FIELDS:
        value = request.body.get(name)
        if value is None:
            continue
        if isinstance(value, bool):
            query[name] = ["1" if value else "0"]
        elif isinstance(value, str):
            query[name] = [value]
        elif isinstance(value, int):
            query[name] = [str(value)]
        else:
            raise _bad_request(name, "must be text, a whole number or true/false")
    return ApiRequest(params=request.params, query=query, body=request.body)


# --- the batch, refused in one pass and written in another ---------------------------


def _refuse_undecidable(items_by_id: dict[int, ReviewItem], ids: list[int]) -> None:
    """Spec 8.2's whole-batch refusal, in a pass that writes nothing.

    Two reasons collapse into one 409 because the answer is the same: pam is not going to
    touch these rows and it is not going to touch the rest of the batch either. An id
    this plan does not hold is either an item of another plan or an item that does not
    exist, and neither is this plan's to decide - `items_by_id` was read for THIS plan,
    so cross-plan needs no second query and cannot be answered inconsistently with it.
    """
    refused = [
        f"{item_id} ({items_by_id[item_id].status})"
        if item_id in items_by_id
        else f"{item_id} (not in this plan)"
        for item_id in ids
        if item_id not in items_by_id or items_by_id[item_id].status in TERMINAL_STATUSES
    ]
    if refused:
        raise _conflict(
            "pam changed nothing. These items cannot be decided: "
            + ", ".join(refused)
            + ". An item that has already been applied, failed or been put back belongs"
            " to a finished run, and an item from another plan is not this plan's to"
            " decide."
        )


def _write_decisions(conn: sqlite3.Connection, ids: list[int], action: str) -> int:
    """The second pass. Every id here has already been proved decidable.

    Separate from `_refuse_undecidable` on purpose and not as a style: folding the write
    into the validation loop would write the ids before the bad one and refuse after,
    which is exactly the non-atomic batch spec 8.2 forbids.
    """
    for item_id in ids:
        repo.set_plan_item_status(conn, item_id, TARGET_STATUS[action])
    return len(ids)


# UX 5 rev 10: "decided the other way" is a DECISION reversed, so `proposed` is not in
# it. Spelled from spec 4's two decided statuses rather than derived from
# `TARGET_STATUS.values()`, which also holds `proposed`.
DECIDED_STATUSES = ("approved", "skipped")


def _decision_response(
    plan_id: int,
    action: str,
    *,
    decided: int,
    written: list[int],
    named: list[int],
    prior: dict[int, str],
    excluded_low: int,
    fresh: dict[str, int],
) -> dict[str, Any]:
    """What both decision endpoints answer with, assembled once (spec 8.2 rev 15).

    `decided` counts every item that moved, companions included - that is spec 8.2's own
    sentence, "the response reports how many items moved". On its own it cannot tell the
    page that a decision touched more than the user asked for: `decide` could compare it
    against the ids it sent, but `decide-filter` never knew that number, and a page that
    did the arithmetic would be holding a second copy of a rule the server owns.

    So `companions` names the items the WIDENING added, and `note` is spec 6.2 rule 7's
    sentence about them - chosen server-side, exactly as the name editor's is, because
    `app.js` is a file nothing in this project lints, types or executes (m7.md F4) and a
    contract expressed there cannot be tested or mutated. The page renders the string and
    holds no copy rule. Empty string when nothing was added, which is the ordinary case
    and the same "no sentence" convention `disclosure.edited_name` already uses.

    `overrode` (UX 5 rev 10, finding F9) is the subset of `companions` whose decision this
    write REVERSED - a member the user had skipped, swept into an approve aimed at its
    undecided partner. It is a list for `companions`' reason and because the page may one
    day want to point at those cards; `note` carries the sentence either way. It is scoped
    to the companions because an id the caller named is an id the user aimed at.

    `companions` is a LIST rather than a count because the page needs the ids anyway: it
    repaints the cards it decided from its own request, and after A1 that request moved
    rows it never named. A count alone would leave those cards showing a stale decision
    until the next reload.
    """
    already = set(named)
    companions = [item_id for item_id in written if item_id not in already]
    target = TARGET_STATUS[action]
    overrode = [
        item_id
        for item_id in companions
        if prior.get(item_id) in DECIDED_STATUSES and prior.get(item_id) != target
    ]
    return {
        "plan_id": plan_id,
        "action": action,
        "decided": decided,
        "excluded_low": excluded_low,
        "companions": companions,
        "overrode": overrode,
        "note": decided_unit(action, len(companions), len(overrode)),
        "counts": fresh,
    }


# --- decide ---------------------------------------------------------------------------


def decide(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """`POST /api/plan/{id}/decide` - approve, skip or reset a named batch of items.

    NO TIER RESTRICTION, DELIBERATELY. Spec 8.2 puts the low-tier refusal on
    `decide-filter` and only there; this endpoint names its ids, which means a human
    looked at each one, and UX 5a's whole flow is the user clearing the attention list -
    mostly `low` items - one keystroke at a time.

    `reset` on an item that is already `proposed` is a no-op that succeeds. Spec 8.2
    enumerates the two things that refuse a batch (a terminal status, another plan's
    item) and this is neither; refusing it would make a bulk `reset` over a normal view
    fail because some of the view had never been decided.

    REV 15: THE DECISION IS WRITTEN TO THE WHOLE COMPANION UNIT (amendment A1). Two
    keystrokes on adjacent cards used to approve a `.HEIC` and skip its `.MOV`, and spec
    7.1's loop then renamed half the unit: one unit, two stems on disk, `pam verify`
    reporting a clean 12/12, and the next `pam scan` destroying the companion group for
    good because spec 5.4 groups by stem. Vision S3 forbids that absolutely. `_unit`
    already reasoned this way for a NAME; the same reasoning was missing for a DECISION.

    THE REFUSAL IS CHECKED OVER THE WIDENED SET, not over the ids the caller named, so a
    companion pulled in by the widening gets exactly the batch refusal a named one gets.
    That is deliberate and it is the only answer consistent with the rest of the section:
    a terminal partner cannot be written, so writing the rest of its unit would create
    the very split this amendment exists to prevent - and spec 8.2 already says a batch
    refuses as a whole rather than doing part of what it was asked.
    """
    plan_id = _plan_id(request)
    action = _action(request)
    ids = _item_ids(request)
    with server.connection() as conn:
        items = _open_plan_items(conn, plan_id, faces_floor(server))
        prior = {item.id: item.status for item in items}
        widened = _widen_to_units(items, ids)
        _refuse_undecidable({item.id: item for item in items}, widened)
        decided = _write_decisions(conn, widened, action)
        conn.commit()
        fresh = counts(repo.plan_rows(conn, plan_id, floor=faces_floor(server)))
    return _decision_response(
        plan_id,
        action,
        decided=decided,
        written=widened,
        named=ids,
        prior=prior,
        excluded_low=0,
        fresh=fresh,
    )


def decide_filter(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """`POST /api/plan/{id}/decide-filter` - the same decisions, resolved from a filter.

    The Low-tier contract (spec 8.2 rev 12 / vision 6a.3), both halves: an explicit
    `tier: 'low'` with `approve` is refused OUTRIGHT, before anything is resolved, and
    any broader filter silently drops the low-tier items and reports how many it dropped
    so the page can say so. `skip` and `reset` carry no restriction - skipping a
    low-confidence name in bulk is exactly the safe direction.

    There is deliberately no server-side "approve all". A FILTER IS NOT REQUIRED - an
    unfiltered `approve` does select the whole plan - but it is not an approve-all either,
    because the exclusion above still takes every `low`-tier item out of it. That is
    exactly how UX 5's `Approve High+Medium` preset has to be built: `tier` is
    single-valued, so no one filter can name two tiers, and "everything the contract
    allows" IS the preset. `skip` and `reset`, carrying no restriction, do select the
    whole plan when unfiltered. `Accept all` (F1 R2, M13) is not an approve-all either: it
    is its own branch, `_accept_all` below, and it holds Low back exactly as this does.

    REV 15: UNIT-SCOPED LIKE `decide`, AND HERE IT IS LOAD-BEARING RATHER THAN TIDY
    (amendment A1). Two of this endpoint's own filter dimensions select a proper subset
    of a companion unit, and both are one click for the user: `q` matches filenames
    INCLUDING extensions, so `q=mov` selects only the video of every Live Photo unit (a phone
    library holds hundreds of them), and `group=YYYY-MM.eN` runs through `event_key`,
    which is None for any member with no `capture` row - so an event selects a photo and
    leaves its `.AAE` sidecar behind. `tier` and `attention` are unit-uniform by
    construction, which is why the preset buttons could never split a unit and why this
    went unnoticed; `status` is not, so a `status=proposed` view widens too.

    THE LOW-TIER EXCLUSION IS UNIT-SCOPED, and that is what keeps the two contracts from
    ever contradicting each other. The Low contract says no `approve` in bulk may touch a
    `low` item; A1 says a unit is decided as a unit. Dropping low ITEMS out of a widened
    unit would satisfy the first by breaking the second. Dropping the whole UNIT satisfies
    both, and under the uniformity the planner actually produces it selects exactly the
    set the pre-A1 code selected - so this is a safety net for a violation of uniformity,
    not a change of behaviour.

    IT RUNS AFTER THE WIDENING, AND THE ORDER SHOWS UP IN THE NUMBER, NOT IN THE WRITE.
    Excluding first leaves the written set identical - a unit-scoped exclusion asks the
    same question of the same units on either side of the widening - so the safety comes
    from the scoping, not from the order. What changes is `excluded_low`: exclude first
    and the count is of the items the FILTER named, so a user whose search matched one
    member of a low unit is told 1 file was held back when 2 were. Excluding last counts
    what was actually dropped. Measured: with the order reversed the whole suite passed
    except the one test built for it (M1061).

    `excluded_low` counts ITEMS, as it always has, because that is what the page prints.
    """
    plan_id = _plan_id(request)
    action = _action(request)
    _refuse_unknown_fields(request)
    if SHORTCUT in request.body:
        return _shortcut(server, request, plan_id, action)
    if LIST_GROUP in request.body:
        raise _bad_request(LIST_GROUP, "only a shortcut (shortcut: true) takes a list group")
    if ACCEPT_ALL in request.body:
        return _accept_all(server, request, plan_id, action)
    filt = _item_filter(_filter_request(request))
    if action == APPROVE and filt.tier == LOW_TIER:
        raise _bad_request("tier", LOW_BULK_REFUSAL)
    with server.connection() as conn:
        items = _open_plan_items(conn, plan_id, faces_floor(server))
        prior = {item.id: item.status for item in items}
        chosen = repo.select_items(items, filt)
        named = [item.id for item in chosen]
        widened = _widen_to_units(items, named)
        excluded_low = 0
        if action == APPROVE:
            widened, excluded_low = exclude_low_units(items, widened)
        _refuse_undecidable({item.id: item for item in items}, widened)
        decided = _write_decisions(conn, widened, action)
        conn.commit()
        fresh = counts(repo.plan_rows(conn, plan_id, floor=faces_floor(server)))
    return _decision_response(
        plan_id,
        action,
        decided=decided,
        written=widened,
        named=named,
        prior=prior,
        excluded_low=excluded_low,
        fresh=fresh,
    )


# --- Accept all (F1 R2, M13-D1) --------------------------------------------------------

# The body field that selects the branch below. `{"action": "approve", "accept_all": true}`
# and nothing else is the whole request.
ACCEPT_ALL = "accept_all"


def _accept_all(
    server: "ReviewServer", request: ApiRequest, plan_id: int, action: str
) -> dict[str, Any]:
    """The review page's `Accept all`: every UNDECIDED unit in the plan except Low.

    ITS SCOPE IS THE WHOLE PLAN ON PURPOSE (F1 R2: "every item in the plan"), so a filter
    field beside it is refused by name rather than silently ignored - a page that sent its
    view along would be asking for something this branch does not do.

    IT SELECTS WITH `naming.accept.accept_all`, the one definition `pam apply --all` uses,
    and NOT with `select_items` + `_widen_to_units`. The widening adds every member of a
    named unit whatever its status, so on a MIXED unit (one member undecided, one skipped)
    it would re-approve the skip - M13-D1 forbids that and `accept_all` leaves such a unit
    alone. The ids it returns are already whole units (it decides per unit), so the write
    covers whole units of the whole plan and never a proper subset of one (vision S3).
    """
    if request.body.get(ACCEPT_ALL) is not True:
        raise _bad_request(ACCEPT_ALL, "must be true")
    if action != APPROVE:
        raise _bad_request("action", "Accept all only approves")
    for name in FILTER_FIELDS:
        if name in request.body:
            raise _bad_request(name, "Accept all decides the whole plan and takes no filter")
    with server.connection() as conn:
        items = _open_plan_items(conn, plan_id, faces_floor(server))
        prior = {item.id: item.status for item in items}
        acceptance = accept_all(items)
        chosen = [item.id for item in items if item.id in acceptance.ids]
        _refuse_undecidable({item.id: item for item in items}, chosen)
        decided = _write_decisions(conn, chosen, APPROVE)
        conn.commit()
        fresh = counts(repo.plan_rows(conn, plan_id, floor=faces_floor(server)))
    return _decision_response(
        plan_id,
        APPROVE,
        decided=decided,
        written=chosen,
        named=chosen,
        prior=prior,
        excluded_low=acceptance.excluded_low,
        fresh=fresh,
    )


# --- the list's one-tap shortcuts (M14 Task 3, OD-1) -----------------------------------

# `{"action": "approve", "shortcut": true, "list_group"?: <U25 key>, ...the view}`: a group's
# `Approve N` (with its key) or the top callout's `Approve the other N` (without).
SHORTCUT = "shortcut"
LIST_GROUP = "list_group"
# The view a shortcut takes is `/list`'s (`nav.LIST_QUERY_PARAMETERS`), which carries the date
# tree's `group` (M14 Task 3's controller ruling) but not the sort: the count `/list` showed
# could not have been over a sort, so a shortcut carrying one is refused rather than ignored.
NOT_A_VIEW_FIELD = ("sort",)


def _shortcut(
    server: "ReviewServer", request: ApiRequest, plan_id: int, action: str
) -> dict[str, Any]:
    """A one-tap shortcut: every UNDECIDED unit of the view (∩ the list group) that is not
    in the needs-a-look set, taken or held WHOLE (vision S3).

    IT WRITES WITH `listing.shortcut`, the call `/list` counts `shortcut_count` with, so the
    N on the button is what this writes. That call reads candidates from the WHOLE plan -
    every member of each unit the view touches - and decides through `naming.accept
    .accept_all` with `also_hold=attention`: the one Low rule, plus OD-1's hold. A `q` that
    matches one member of a unit therefore approves both members or neither, never one.
    """
    if request.body.get(SHORTCUT) is not True:
        raise _bad_request(SHORTCUT, "must be true")
    if ACCEPT_ALL in request.body:
        raise _bad_request(ACCEPT_ALL, "a shortcut is not Accept all; send one or the other")
    if action != APPROVE:
        raise _bad_request("action", "a shortcut only approves")
    for name in NOT_A_VIEW_FIELD:
        if name in request.body:
            raise _bad_request(
                name, "a shortcut takes the list's view: tier, status, attention, q, group"
            )
    filt = _item_filter(_filter_request(request))
    if filt.tier == LOW_TIER:
        raise _bad_request("tier", LOW_BULK_REFUSAL)
    key = _shortcut_list_group(request)
    with server.connection() as conn:
        items = _open_plan_items(conn, plan_id, faces_floor(server))
        prior = {item.id: item.status for item in items}
        chosen = repo.select_items(items, filt)
        if key is not None:
            keys = listing.unit_keys(items)
            chosen = [item for item in chosen if listing.in_list_group(item, key, keys)]
        acceptance = listing.shortcut(items, chosen)
        ids = [item.id for item in items if item.id in acceptance.ids]
        _refuse_undecidable({item.id: item for item in items}, ids)
        decided = _write_decisions(conn, ids, APPROVE)
        conn.commit()
        fresh = counts(repo.plan_rows(conn, plan_id, floor=faces_floor(server)))
    return _decision_response(
        plan_id,
        APPROVE,
        decided=decided,
        written=ids,
        named=ids,
        prior=prior,
        excluded_low=acceptance.excluded_low,
        fresh=fresh,
    ) | {"held": acceptance.held}


def _shortcut_list_group(request: ApiRequest) -> str | None:
    """U25's key, validated by `/items`' own reader; an empty string is no filter there too."""
    value = request.body.get(LIST_GROUP)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _bad_request(LIST_GROUP, "must be a list group's key")
    return _list_group(ApiRequest(params=request.params, query={LIST_GROUP: [value]}, body={}))
