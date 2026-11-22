"""F1 R2/R4 and M13-D1: what "accept all" selects - ONE definition, two callers.

`pam apply N --all` (execute/apply) and the review page's `Accept all` (webui/decide_api,
Task 2) must select the same set, or the CLI and the page disagree about the one decision
that renames files. Pure: it takes rows, returns ids; no SQL, no I/O.

THE RULE, spelled from the maintainer's ruling and spec 8.2's Low contract:
  - only UNDECIDED items are candidates (status 'proposed'); a skip stays a skip (M13-D1,
    S11, A4: nothing un-decides by accident);
  - a unit is accepted WHOLE or not at all (F1 R3, spec 7.3 rev 15);
  - a unit holding any 'low'-tier item is never accepted in bulk; its members are counted
    in `excluded_low` (items, not units - that is what the page and the CLI print);
  - a unit with any member not 'proposed' is left exactly as it is (it is decided, or it is
    mixed, and mixing is resolved by the user, never by a bulk button).

M14 OD-1 (ADOPTED 2026-10-29): `also_hold` is the review list's one-tap shortcuts' second
hold - `also_hold=lambda i: i.attention` holds the whole needs-a-look set, so the callout's
"Approve the other N" is literally the other N. A unit is held when ANY member satisfies it,
whole, like Low. ITS DEFAULT (None) HOLDS NOTHING MORE, so `pam apply --all` and M13's
`Accept all`, which pass no `also_hold`, select exactly what they selected before M14.
Counting: a held unit that is ALSO low counts in `excluded_low` only (Low is checked first);
any other held unit counts its members in the separate `held` - `excluded_low` keeps meaning
"low-confidence names", the number the CLI and the page already print under that name.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

LOW_TIER = "low"
UNDECIDED = "proposed"


class Decidable(Protocol):
    @property
    def id(self) -> int: ...
    @property
    def unit_key(self) -> str: ...
    @property
    def tier(self) -> str: ...
    @property
    def status(self) -> str: ...


@dataclass(frozen=True)
class Acceptance:
    ids: frozenset[int]
    excluded_low: int
    held: int = 0  # items in units `also_hold` held back that are not low (M14 OD-1)


def accept_all[D: Decidable](
    items: Iterable[D], also_hold: Callable[[D], bool] | None = None
) -> Acceptance:
    units: dict[str, list[D]] = {}
    for item in items:
        units.setdefault(item.unit_key, []).append(item)
    ids: set[int] = set()
    excluded_low = 0
    held = 0
    for members in units.values():
        if any(member.status != UNDECIDED for member in members):
            continue
        if any(member.tier == LOW_TIER for member in members):
            excluded_low += len(members)
            continue
        if also_hold is not None and any(also_hold(member) for member in members):
            held += len(members)
            continue
        ids.update(member.id for member in members)
    return Acceptance(frozenset(ids), excluded_low, held)


def exclude_low_units(items: Iterable[Decidable], ids: Iterable[int]) -> tuple[list[int], int]:
    """decide-filter's existing unit-scoped Low exclusion, moved here unchanged (Task 2 wires it).

    Returns (kept ids in the given order, count of ITEMS dropped)."""
    by_id = {item.id: item for item in items}
    low_units = {item.unit_key for item in by_id.values() if item.tier == LOW_TIER}
    wanted = list(ids)
    kept = [i for i in wanted if by_id[i].unit_key not in low_units]
    return kept, len(wanted) - len(kept)
