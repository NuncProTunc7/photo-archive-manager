"""U3 / U17: the review list's SELECTION - its resolver and its two routes (M14 Task 3).

`POST /plan/{id}/selection` previews what a selection means and writes nothing (POST only
because a selection is a body). `POST /plan/{id}/decide-selection` decides it, and it is
THE ONE ROUTE by which `approve` may reach a `low` item in bulk (design 8.2, U3): only when
the request carries `include_low: true` AND `expected_low` equal to the number of low items
the server itself counts in the widened set - so the user approves only a count the page
actually showed them. A mismatch is a 409 carrying the fresh preview, and nothing is written.

THE RESOLUTION IS U17's, IN ITS ORDER. Each group is `select_items(view)` intersected with
U25's list group; `all` is `select_items(view)`; then the `ids` the user ticked, taken as
named whatever the view; minus `except_ids`; and only THEN `write_state._widen_to_units`
over the WHOLE plan. Widening last is what keeps a unit whole: an `except_id` whose partner
is still selected is widened back in, and `overridden` says so. Widening first and then
subtracting would write a proper subset of a unit (vision S3).

`low` COUNTS LOW ITEMS IN THE WIDENED SET, never in what the user named: tier is unit-uniform
in what the planner writes, but the count the user is shown must be the count of what will
be WRITTEN, and the widening is the step that decides that.

VALIDATE EVERYTHING, THEN WRITE - decide_api's two-pass rule, with its own two passes
imported (`_refuse_undecidable`, `_write_decisions`) rather than copied. Every refusal here,
the 409 included, is raised before the first `_write_decisions` call.

WHAT THE BODY MAY CARRY, AND WHAT IS REFUSED (each a 400, nothing written):
- a field outside U17's wire format, at any of its three levels (the body, `selection`,
  `view`) - M13 V3's copy, through `decide_api._refuse_unknown_names`;
- `view` missing or not an object (U17 gives it no `?`: a selection always says which view
  it was made in); its fields go through the ONE filter validator `decide-filter` uses;
- `ids` / `except_ids` not lists of item ids (`True` is not an id), or naming an id this
  plan does not hold;
- `groups` not a list of U25 list-group keys, or naming a group this plan does not have;
- `all` anything but the literal `true`;
- a selection that names nothing (no ids, no groups, no `all`);
- `include_low` / `expected_low` beside `skip` or `reset`, which need neither (U17) - they
  are REFUSED rather than ignored, because a field that cannot mean anything in this
  request is the page asking for something pam does not do (`_accept_all`'s precedent);
- `include_low` not a JSON boolean, `expected_low` not a whole number >= 0 (`1` is not
  `true`, and `true` is not `1`).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pam.catalog import repo
from pam.catalog.repo.review import ReviewItem
from pam.webui.api import (
    ApiError,
    ApiRequest,
    _bad_request,
    _item_filter,
    _plan_id,
    counts,
    faces_floor,
)
from pam.webui.decide_api import (
    LOW_TIER,
    _action,
    _decision_response,
    _filter_request,
    _refuse_undecidable,
    _refuse_unknown_names,
    _write_decisions,
)
from pam.webui.listing import in_list_group, is_list_group_key, unit_keys
from pam.webui.write_state import APPROVE, _open_plan_items, _widen_to_units

if TYPE_CHECKING:  # pragma: no cover - import for typing only, never at runtime
    from pam.webui.server import ReviewServer

CONFLICT_STATUS = 409

# U17's wire format, level by level.
SELECTION_FIELDS = ("view", "ids", "groups", "all", "except_ids")
# U17's view, plus the date tree's `group` (M14 Task 3's controller ruling, pending the design
# model's ratification): a selection made under a month filter covers that month, not the plan.
VIEW_FIELDS = ("tier", "status", "attention", "q", "group")
PREVIEW_BODY_FIELDS = ("selection",)
DECIDE_BODY_FIELDS = ("selection", "action", "include_low", "expected_low")
INCLUDE_LOW = "include_low"
EXPECTED_LOW = "expected_low"

STALE_LOW_COUNT = (
    "The selection changed: it now holds {low} name(s) that need review, and the page"
    " showed a different count. pam changed nothing. Check the count and try again."
)


@dataclass(frozen=True)
class Selection:
    """A validated U17 selection, before it is resolved against the plan."""

    view: repo.ItemFilter
    ids: tuple[int, ...]
    groups: tuple[str, ...]
    all: bool
    except_ids: tuple[int, ...]


@dataclass(frozen=True)
class Resolved:
    """What a selection means over one plan: the ids to write, and what the bar shows."""

    named: list[int]  # resolved and minus `except_ids`, before the widening
    widened: list[int]  # what a decision writes - whole units of the whole plan
    overridden: list[int]  # `except_ids` the widening put back
    low: int
    units: int
    attention: int

    def preview(self) -> dict[str, Any]:
        """U17's `{items, units, low, overridden, attention}`."""
        return {
            "items": len(self.widened),
            "units": self.units,
            "low": self.low,
            "overridden": self.overridden,
            "attention": self.attention,
        }


# --- reading the body -----------------------------------------------------------------


def _object(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _bad_request(name, "must be an object")
    return value


def _id_list(selection: Mapping[str, Any], name: str) -> tuple[int, ...]:
    value = selection.get(name, [])
    if not isinstance(value, list):
        raise _bad_request(name, "must be a list of item ids")
    for entry in value:
        if isinstance(entry, bool) or not isinstance(entry, int):
            raise _bad_request(name, f"{entry!r} is not an item id")
    return tuple(value)


def _group_list(selection: Mapping[str, Any]) -> tuple[str, ...]:
    value = selection.get("groups", [])
    if not isinstance(value, list):
        raise _bad_request("groups", "must be a list of group keys")
    for entry in value:
        if not isinstance(entry, str) or not is_list_group_key(entry):
            raise _bad_request("groups", f"{entry!r} is not a group of the list")
    return tuple(value)


def read_selection(body: Mapping[str, Any]) -> Selection:
    """The `selection` object, validated field by field. Touches no row."""
    if "selection" not in body:
        raise _bad_request("selection", "is missing")
    selection = _object(body["selection"], "selection")
    _refuse_unknown_names(selection, SELECTION_FIELDS)
    if "view" not in selection:
        raise _bad_request("view", "is missing")
    view = _object(selection["view"], "view")
    _refuse_unknown_names(view, VIEW_FIELDS)
    filt = _item_filter(_filter_request(ApiRequest(params={}, query={}, body=view)))
    if "all" in selection and selection["all"] is not True:
        raise _bad_request("all", "must be true")
    parsed = Selection(
        view=filt,
        ids=_id_list(selection, "ids"),
        groups=_group_list(selection),
        all=selection.get("all") is True,
        except_ids=_id_list(selection, "except_ids"),
    )
    if not (parsed.ids or parsed.groups or parsed.all):
        raise _bad_request("selection", "names no item, no group and not all")
    return parsed


# --- the resolver ---------------------------------------------------------------------


def _refuse_strangers(items: list[ReviewItem], selection: Selection) -> dict[str, str]:
    """An id or a group this plan does not hold is a 400. Returns `unit_keys` for reuse."""
    held = {item.id for item in items}
    for name, ids in (("ids", selection.ids), ("except_ids", selection.except_ids)):
        for item_id in ids:
            if item_id not in held:
                raise _bad_request(name, f"{item_id} is not an item of this plan")
    keys = unit_keys(items)
    known = set(keys.values())
    for group in selection.groups:
        if group not in known:
            raise _bad_request("groups", f"{group!r} is not a group of this plan")
    return keys


def resolve(items: list[ReviewItem], selection: Selection) -> Resolved:
    """U17's resolution, in its order. `items` is the WHOLE plan; pure, no connection."""
    keys = _refuse_strangers(items, selection)
    chosen: list[int] = []
    if selection.groups or selection.all:
        in_view = repo.select_items(items, selection.view)
        for group in selection.groups:
            chosen += [item.id for item in in_view if in_list_group(item, group, keys)]
        if selection.all:
            chosen += [item.id for item in in_view]
    chosen += selection.ids
    excepted = set(selection.except_ids)
    named = [item_id for item_id in dict.fromkeys(chosen) if item_id not in excepted]
    widened = _widen_to_units(items, named)
    written = set(widened)
    by_id = {item.id: item for item in items}
    rows = [by_id[item_id] for item_id in widened]
    return Resolved(
        named=named,
        widened=widened,
        overridden=[i for i in dict.fromkeys(selection.except_ids) if i in written],
        low=sum(1 for row in rows if row.tier == LOW_TIER),
        units=len({row.unit_key for row in rows}),
        attention=sum(1 for row in rows if row.attention),
    )


# --- the two routes -------------------------------------------------------------------


def preview_selection(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """`POST /api/plan/{id}/selection` - READ-ONLY: what this selection would decide."""
    plan_id = _plan_id(request)
    _refuse_unknown_names(request.body, PREVIEW_BODY_FIELDS)
    selection = read_selection(request.body)
    with server.connection() as conn:
        items = _open_plan_items(conn, plan_id, faces_floor(server))
    return {"plan_id": plan_id, **resolve(items, selection).preview()}


def _low_acknowledgement(body: Mapping[str, Any], action: str) -> tuple[bool, int | None]:
    """`include_low` and `expected_low`, type-checked. Neither may ride with skip/reset."""
    if action != APPROVE:
        for name in (INCLUDE_LOW, EXPECTED_LOW):
            if name in body:
                raise _bad_request(name, f"only an approve carries it, not {action}")
        return False, None
    include = body.get(INCLUDE_LOW, False)
    if not isinstance(include, bool):
        raise _bad_request(INCLUDE_LOW, "must be true or false")
    expected = body.get(EXPECTED_LOW)
    if expected is not None and (
        isinstance(expected, bool) or not isinstance(expected, int) or expected < 0
    ):
        raise _bad_request(EXPECTED_LOW, "must be a whole number of names")
    return include, expected


def _refuse_unacknowledged_low(
    resolved: Resolved, include: bool, expected: int | None, plan_id: int
) -> None:
    """U3's disclosure contract: approve a low item only at the count the page showed.

    With no low item in the set nothing needs acknowledging, but a page that says it showed
    a NONZERO count is out of date all the same, so a stale `expected_low` refuses there too.
    """
    if resolved.low == 0 and expected in (None, 0):
        return
    if resolved.low > 0 and include is True and expected == resolved.low:
        return
    raise ApiError(
        CONFLICT_STATUS,
        STALE_LOW_COUNT.format(low=resolved.low),
        {"selection": {"plan_id": plan_id, **resolved.preview()}},
    )


def decide_selection(server: "ReviewServer", request: ApiRequest) -> dict[str, Any]:
    """`POST /api/plan/{id}/decide-selection` - approve, skip or reset a selection.

    THE ONE SANCTIONED BYPASS of the Low rule (U3): `decide-filter`, the presets, the
    shortcuts and `--all` never approve a low item; this route does, and only at the count
    the page disclosed. Both refusals - `_refuse_undecidable` and the low count - run
    before the one write.
    """
    plan_id = _plan_id(request)
    _refuse_unknown_names(request.body, DECIDE_BODY_FIELDS)
    action = _action(request)
    selection = read_selection(request.body)
    include, expected = _low_acknowledgement(request.body, action)
    with server.connection() as conn:
        items = _open_plan_items(conn, plan_id, faces_floor(server))
        prior = {item.id: item.status for item in items}
        resolved = resolve(items, selection)
        _refuse_undecidable({item.id: item for item in items}, resolved.widened)
        if action == APPROVE:
            _refuse_unacknowledged_low(resolved, include, expected, plan_id)
        decided = _write_decisions(conn, resolved.widened, action)
        conn.commit()
        fresh = counts(repo.plan_rows(conn, plan_id, floor=faces_floor(server)))
    response = _decision_response(
        plan_id,
        action,
        decided=decided,
        written=resolved.widened,
        named=resolved.named,
        prior=prior,
        excluded_low=0,
        fresh=fresh,
    )
    response["low_included"] = resolved.low if action == APPROVE else 0
    response["overridden"] = resolved.overridden
    return response
