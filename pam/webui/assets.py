"""The review server's static files: their names, and the allowlist they are served from.

Cut out of `pam/webui/server.py` by M14 Task 5d (ruling C5-6), a PURE MOVE named before Task 5d
edited the server (it was at 299 executable lines). `server` imports every name back, so every
importer - `server.STATIC_FILES`, `server.INDEX`, every `*_SCRIPT` - is unchanged.

`STATIC_FILES` is the allowlist that makes path traversal unreachable: the server serves a name
only if it is a key here, and reads it from the package by that name, never by joining a path.
"""

INDEX = "index.html"
STYLESHEET = "style.css"
SCRIPT = "app.js"
# M14 Task 1: the review page's script is FIVE classic scripts, loaded by `index.html` in
# this order - `app.js` (above) is the last of them and is now the boot only. Design spec
# 2's tree names each.
CORE_SCRIPT = "pam-core.js"
TRUST_SCRIPT = "pam-trust.js"
KEYS_SCRIPT = "pam-keys.js"
# M14 Task 2: the list (U2) - one row's render, and the list itself - loaded after
# `pam-keys.js` and before `app.js`. M14 Task 2 Step 6b retired the card page,
# `pam-review.js`, with the grid it drew.
ROW_SCRIPT = "pam-row.js"
LIST_SCRIPT = "pam-list.js"
# M14 Task 3 (U3, U17): the selection as data, before the list; its bar, after it.
SELECTION_SCRIPT = "pam-selection.js"
BAR_SCRIPT = "pam-bar.js"
# M14 Task 2 Step 6a: the preview overlay and the name editor, on the list's rows.
DETAIL_SCRIPT = "pam-detail.js"
# M14 Task 4 Step 7 (W18, U13): Welcome, the page `pam start` opens with no folder.
WELCOME_SCRIPT = "pam-welcome.js"
# M14 Task 5a (R2-F10): Welcome's folder chooser (U13), cut out of `pam-welcome.js`; loaded before it.
CHOOSER_SCRIPT = "pam-chooser.js"
# M14 Task 5e: Welcome's Reading step (U11) - Start reading, the poll, Stop; after the chooser.
READING_SCRIPT = "pam-reading.js"
# M14 Task 6c (U6, W19-W21): the Confirm screen and the rename's progress, then Done and undo;
# after Welcome, before the boot that wires them.
CONFIRM_SCRIPT = "pam-confirm.js"
DONE_SCRIPT = "pam-done.js"
# M14 Task 7 (W22, X2): History, reached from Done; after Done, whose undo it reuses.
HISTORY_SCRIPT = "pam-history.js"
# M15 Task 5b: a rename that did not run or stopped part-way (U12, U14, S2-F1, C6.2), cut out of
# `pam-done.js` before its C6.2 edit; after Done, whose drawing helpers it uses.
STOPPED_SCRIPT = "pam-stopped.js"
# M11 Task 10 Step 1: a SECOND PAGE, not a mode of the review page. It shares
# `style.css` - UX spec 7 is one visual system for M7 and M11 alike - and shares nothing
# else, because naming people has no plan, no tier, no trust score and no rename in it.
FACES_INDEX = "faces.html"
FACES_SCRIPT = "faces.js"

# The complete set of names the server will serve out of `pam/webui/static/`, with the
# Content-Type each is sent as. A LITERAL rather than a directory listing, because this
# is the allowlist that makes path traversal unreachable; `test_every_shipped_static
# _file_is_routable_and_every_routable_name_ships` pins it against what is on disk in
# both directions, so a file added without an entry (unreachable) and an entry added
# without a file (a 500 waiting to happen) are both red.
# charset on every entry: the pages' copy carries UX spec 5 and 6's own punctuation (an em
# dash in the faces page's "pam faces label has stopped - restart it from the terminal", a
# `·` between the header's counts and in the reading step's progress line), and a stylesheet or a script served without a charset is decoded by
# whatever the browser guesses.
STATIC_FILES = {
    INDEX: "text/html; charset=utf-8",
    STYLESHEET: "text/css; charset=utf-8",
    CORE_SCRIPT: "text/javascript; charset=utf-8",
    TRUST_SCRIPT: "text/javascript; charset=utf-8",
    KEYS_SCRIPT: "text/javascript; charset=utf-8",
    ROW_SCRIPT: "text/javascript; charset=utf-8",
    LIST_SCRIPT: "text/javascript; charset=utf-8",
    SELECTION_SCRIPT: "text/javascript; charset=utf-8",
    BAR_SCRIPT: "text/javascript; charset=utf-8",
    DETAIL_SCRIPT: "text/javascript; charset=utf-8",
    CHOOSER_SCRIPT: "text/javascript; charset=utf-8",
    READING_SCRIPT: "text/javascript; charset=utf-8",
    WELCOME_SCRIPT: "text/javascript; charset=utf-8",
    CONFIRM_SCRIPT: "text/javascript; charset=utf-8",
    DONE_SCRIPT: "text/javascript; charset=utf-8",
    STOPPED_SCRIPT: "text/javascript; charset=utf-8",
    HISTORY_SCRIPT: "text/javascript; charset=utf-8",
    SCRIPT: "text/javascript; charset=utf-8",
    FACES_INDEX: "text/html; charset=utf-8",
    FACES_SCRIPT: "text/javascript; charset=utf-8",
}
