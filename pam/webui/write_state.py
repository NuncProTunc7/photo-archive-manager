"""The state spec 8.2's writes are allowed to touch, and THE grouping, spelled once.

Both halves of the write API - `decide_api.py`'s two decision endpoints and
`write_api.py`'s two name endpoints - need the same three things: the action vocabulary,
the check that the plan is still `open`, and the answer to "which items rename together".
They live here so that neither half can grow its own copy of any of them.

THE GROUPING IS WHY THIS MODULE EXISTS AT ALL (M10 Task 2, `m9.md` 5.2). `_by_unit`'s
docstring below records what a second copy costs: the protected write and the unprotected
one disagreed about the members of a unit, and vision S3 is what that cost. The split of
the ~300-line `write_api.py` had to preserve that property BY CONSTRUCTION rather than by
proximity, so the one expression of "the members of this unit" moved into one module both
halves import. A second grouping helper appearing in either half is the defect returning.
"""

import sqlite3

from pam.catalog import repo
from pam.catalog.repo.review import ReviewItem
from pam.errors import OLD_PLAN_REASON
from pam.webui.api import _conflict, _not_found

# Spec 8.2's three actions and what each one writes.
APPROVE = "approve"
SKIP = "skip"
RESET = "reset"
ACTIONS = (APPROVE, SKIP, RESET)
TARGET_STATUS = {APPROVE: "approved", SKIP: "skipped", RESET: "proposed"}

# Spec 8.2: "any id in a batch that is terminal (`applied`, `failed`, `undone`)". Spelled
# from the spec's own three words rather than derived as `ITEM_STATUSES - TARGET_STATUS
# .values()`, because deriving it would make the guard agree with whatever the vocabulary
# happens to be - `tests/test_webui_decide.py` asserts the two partitions cover spec 4's
# enum exactly, which is the independent check a derivation cannot give.
TERMINAL_STATUSES = ("applied", "failed", "undone")

OPEN = "open"


def _require_open_plan(conn: sqlite3.Connection, plan_id: int) -> None:
    """Spec 8.2: the plan must be `open`, re-checked here rather than trusted.

    Read inside the handler, which is inside the server's single DB lock - so no other
    pam process can be mid-apply while this runs (`pam review` holds the workspace lock
    for its whole lifetime, plan clarification 2) and no other request can interleave.
    The lock is what makes it atomic; re-reading is what stops the server trusting a
    page that has been open since before `pam apply` ran.
    """
    status = repo.plan_status(conn, plan_id)
    if status is None:
        raise _not_found(f"pam does not know a plan with id {plan_id}")
    if status != OPEN:
        raise _conflict(
            f"plan {plan_id} is {status}, not open, so its names cannot be changed any"
            " more. Run: pam plan"
        )


def _open_plan_items(conn: sqlite3.Connection, plan_id: int, floor: float) -> list[ReviewItem]:
    """`_require_open_plan`, plus the plan's rows and spec 7.3's OLD-PLAN refusal (F10).

    THE REVIEW UI MUST REFUSE WHAT THE VALIDATOR REFUSES. Migration 0004 had to declare
    `unit_key TEXT NOT NULL DEFAULT ''`, so a plan built before it legitimately carries
    '' on every item. `execute/validator.py` raises `OLD_PLAN_REASON` on any such plan
    and `pam apply` refuses it outright; `pam/webui/` had no equivalent check, so the
    review UI served a plan that could never be applied - and worse, `_by_unit` groups by
    `unit_key`, so '' makes the WHOLE PLAN one unit and one keystroke widens over every
    item in it. Severity is bounded by apply's own refusal: no rename can result, so this
    is a wasted review pass rather than a data risk. It is refused here anyway, because
    the alternative is a user deciding four hundred files and finding out afterwards.

    `OLD_PLAN_REASON` IS IMPORTED, NEVER RESTATED. One sentence for one condition; two
    wordings of the same refusal is how a user learns that the two commands are different
    programs. Its `Run: pam plan` is the route out, which is UX 8's requirement. It lives
    in `pam/errors.py` rather than in `execute/validator.py` where it was written, because
    `pam/webui/` may import nothing under `pam.execute` that
    `tests/guards.py::ALLOWED_WEBUI_EXECUTE_IMPORTS` does not name - spec 8.2's "the UI
    reads and approves; it never applies", made structural - and sharing one string is not
    worth an entry in that allowlist.

    Returned rather than discarded because every caller reads the rows immediately
    afterwards, and a second `plan_rows` would be a second answer to "what is in this
    plan" inside one request.
    """
    _require_open_plan(conn, plan_id)
    items = repo.plan_rows(conn, plan_id, floor=floor)
    if any(not item.unit_key for item in items):
        raise _conflict(OLD_PLAN_REASON.format(plan_id=plan_id))
    return items


def _by_unit(items: list[ReviewItem]) -> dict[str, list[ReviewItem]]:
    """`unit_key` -> the plan items that rename together. THE grouping, spelled once.

    Spec 7.3 rev 15 (amendment A1): a companion unit is decided as a unit, and `pam
    apply` never renames a proper subset of one. `rename_item` has grouped by `unit_key`
    since M7 and `_unit`'s docstring names the hazard; `decide` and `decide-filter` wrote
    one item at a time with no such reasoning anywhere, which is the asymmetry A1 closes.
    Both writes now come through this one function rather than each spelling the grouping
    for itself: one module holding two expressions of "the members of this unit" is how
    the protected write and the unprotected one came to disagree in the first place.

    An index rather than a scan per unit, because `decide-filter` unfiltered names every
    unit in the plan and a per-unit scan would make that quadratic in the plan's size.

    `unit_key` is the grouping spec 6.3 rule 6 froze into the plan, never the live
    `files.companion_group_id`, which `pam scan` recomputes from scratch.
    """
    units: dict[str, list[ReviewItem]] = {}
    for item in items:
        units.setdefault(item.unit_key, []).append(item)
    return units


def _widen_to_units(items: list[ReviewItem], ids: list[int]) -> list[int]:
    """The named ids, plus every other member of every unit they name (spec 8.2 rev 15).

    RESOLVE FIRST, THEN WIDEN. The caller has already decided WHICH items it means - by
    id, or by resolving a filter - and this only ever adds the partners those items
    rename with. Widening before resolution would be a different endpoint: a filter that
    silently selected items it does not match.

    THE CALLER'S IDS COME FIRST, IN THE CALLER'S ORDER. They are deduplicated (so a
    caller naming both members of a unit is not counted twice, and `decided` can never
    exceed the number of rows written), and the companions follow, unit by unit, in the
    order those units were first named. `_refuse_undecidable` builds its refusal by
    walking this list, so the user's own ids are named before the ones pam added.

    AN ID THIS PLAN DOES NOT HOLD IS KEPT, not dropped. It has no `unit_key` to widen by,
    and dropping it here would turn spec 8.2's "an item from another plan is not this
    plan's to decide" refusal into a silent partial success - the one outcome that
    section forbids for every other reason.
    """
    units = _by_unit(items)
    by_id = {item.id: item for item in items}
    named = list(dict.fromkeys(ids))
    seen = set(named)
    widened = list(named)
    keys = dict.fromkeys(by_id[item_id].unit_key for item_id in named if item_id in by_id)
    for key in keys:
        widened += [member.id for member in units[key] if member.id not in seen]
    return widened
