"""The review card's look-alikes (design 8.2, UX 5; M13 Task 10, amendment H3).

Each item in `/api/plan/{id}/items` carries `look_alikes` - the `rel_path`s of up to
`SHOWN` OTHER plan items in its look-alike group - and `look_alikes_more`, how many more
there are. The card shows one line naming them. NO FILTER, NO LINK, NO ACTION (H3, vision
A10): this is detection and reporting, and nothing here can decide or rename anything.

THE GROUPS ARE `pam report duplicates`' GROUPS (H2, `pam/report_duplicates.py`). One live
fingerprint per CONTENT (`repo.phash_by_sha256`), searched by `near_dupe_groups` at the
archive's `[enrich.phash] near_dupe_distance`, over every content in the archive - so the
card and the report agree about which photos look alike. A group is then expanded to the
plan's ITEMS whose file is present, not a cloud placeholder, and holds one of its
contents (the report's "present, non-placeholder files"; a placeholder is never read,
vision R2).

AN ITEM'S OWN COMPANION UNIT IS NEVER ITS LOOK-ALIKE. A RAW and its JPEG are one shot,
renamed together (design 7.3); telling the user one looks like the other is noise. The
unit is the PLAN's (`unit_key`), never `files.companion_group_id`, which `pam scan`
recomputes: design 7.3 rev 16 makes a plan's unit the thing every layer reasons about.
A unit's members can still name the SAME other photos - each member is listed against
everything outside its unit, and nothing inside it.

COMPUTED ONCE PER SERVER AND PLAN. `pam review` holds the workspace lock for the server's
whole lifetime (`webui/__init__.py`; `test_the_server_holds_the_lock_for_the_whole_session`),
so no `pam scan` or `pam enrich` can change a fingerprint or a file while it runs, and the
answer cannot go stale. Nothing the review page writes touches a fingerprint, a file's
status or a plan's units - a rename edits the PROPOSED name, and look-alikes are listed by
the path each item has on disk (`old_rel_path`), which nothing renames while the lock is
held. The memory is read and written only inside `server.connection()`,
whose lock serializes every request, so two threads cannot compute it at once.
"""

import sqlite3
from typing import TYPE_CHECKING, Any
from weakref import WeakKeyDictionary

from pam.catalog import repo
from pam.catalog.repo.review import ReviewItem
from pam.enrich.phash import near_dupe_groups

if TYPE_CHECKING:  # pragma: no cover - import for typing only, never at runtime
    from pam.webui.server import ReviewServer

# H3: "up to five" paths on the card; the rest are counted, never listed.
SHOWN = 5

# server -> plan id -> item id -> every other item's path in its group, sorted. Weak, so a
# stopped server (and every test's server) takes its memory with it.
_MEMORY: "WeakKeyDictionary[ReviewServer, dict[int, dict[int, list[str]]]]" = (
    WeakKeyDictionary()
)


def forget(server: "ReviewServer") -> None:
    """Drop everything remembered for `server`. Called under `db_lock`, at two moments.

    It has changed folder (M14 Task 4 R1-F2): plan ids start at 1 in every catalog, so a memory
    kept across a change of folder would answer the new folder's plan 1 with the old folder's
    look-alikes. And a read has written a new plan (M14 Task 5d, U11: caches that outlive a plan
    are keyed by plan id): the old plan's memory must not be served after it, and the new plan's
    is computed on its first request.
    """
    _MEMORY.pop(server, None)


def for_plan(
    server: "ReviewServer", conn: sqlite3.Connection, plan_id: int, items: list[ReviewItem]
) -> dict[int, list[str]]:
    """This plan's look-alikes, computed on the first request and remembered after it.

    Call ONLY inside `server.connection()`: its lock is what makes the memory safe.
    """
    plans = _MEMORY.setdefault(server, {})
    if plan_id not in plans:
        distance = server.config.phash_near_dupe_distance
        plans[plan_id] = look_alikes(conn, items, distance)
    return plans[plan_id]


def look_alikes(
    conn: sqlite3.Connection, items: list[ReviewItem], distance: int
) -> dict[int, list[str]]:
    """item id -> the sorted paths of every OTHER-unit plan item in its look-alike group.

    An item with none is absent from the map. Module docstring: which items, which groups.
    """
    hashes = repo.phash_by_sha256(conn)
    files = {f.id: f for f in repo.all_files(conn).values()}
    held: dict[str, list[ReviewItem]] = {}
    for item in items:
        file = files.get(item.file_id)
        if file is None or file.status != "present" or file.placeholder:
            continue
        if file.sha256 is not None and file.sha256 in hashes:
            held.setdefault(file.sha256, []).append(item)
    contents = sorted(hashes)  # an int key per distinct content, in a stable order
    groups = near_dupe_groups(
        {index: hashes[sha] for index, sha in enumerate(contents)}, max_distance=distance
    )
    found: dict[int, list[str]] = {}
    for members in groups:
        group = [item for index in members for item in held.get(contents[index], [])]
        for item in group:
            others = sorted(o.old_rel_path for o in group if o.unit_key != item.unit_key)
            if others:
                found[item.id] = others
    return found


def card_fields(found: dict[int, list[str]], item_id: int) -> dict[str, Any]:
    """The card's two fields (H3): at most `SHOWN` paths, and how many more there are."""
    others = found.get(item_id, [])
    return {"look_alikes": others[:SHOWN], "look_alikes_more": max(0, len(others) - SHOWN)}
