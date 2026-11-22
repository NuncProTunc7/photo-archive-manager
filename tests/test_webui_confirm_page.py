"""`tests/js/confirm_test.js` executed, and the floor that keeps it honest (M14 Task 6c).

The page's second trigger of the one rename (U5): the footer's `Rename N files` (W16), *Ready to
rename* (W19, U6), the rename's progress, *Done* (W20), `Undo this rename` and its end (W21), and a
rename that did not run (U14, U12) - with U22's conditions on every sentence. It lives in
`pam-confirm.js` and `pam-done.js`; the footer's N is `/list`'s `renames` (ruling B2). The node
driver loads the page's fifteen scripts into one context, in the page's order, so `app.js` boots a
bound page as it does in a browser; this module runs it and reads the names it prints - every check
prints `ok <name>` and the run ends with `ran <n>` - so a known set must actually EXECUTE.

THE SHAPE IS `tests/test_webui_welcome_page.py`'s. `node` is an external tool on the exiftool
precedent, so the node half skips where it is absent - and the stdlib half NEVER skips, so a
deleted driver or a deleted sentence is red on either machine (CLAUDE.md, M783/M784).

THE COPY IS SPELLED HERE from U6, U22 and W16-W21, the controller's rulings B3 and B5, and the
implementer's two words pending the design model - never read back out of the scripts. The lists
are asserted non-empty AT COLLECTION (M743: pytest SKIPS an empty parametrize).

THE NEGATIVE CONTROLS ARE RUN: each plants one defect in a COPY of a script through
`PAM_CONFIRM_JS` / `PAM_DONE_JS` / `PAM_LIST_JS` / `PAM_KEYS_JS` / `PAM_APP_JS`, and the driver must go red on the
check that exists for it while another stays green. A compound guard is planted OPERAND BY OPERAND.
Nothing on disk is touched.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
NODE_DRIVER = TESTS_DIR / "js" / "confirm_test.js"
STATIC_DIR = TESTS_DIR.parent / "pam" / "webui" / "static"
CONFIRM_JS = STATIC_DIR / "pam-confirm.js"
DONE_JS = STATIC_DIR / "pam-done.js"
# M15 Task 5b: a rename that did not run or stopped part-way, cut out of `pam-done.js`.
STOPPED_JS = STATIC_DIR / "pam-stopped.js"
LIST_JS = STATIC_DIR / "pam-list.js"
KEYS_JS = STATIC_DIR / "pam-keys.js"
APP_JS = STATIC_DIR / "app.js"
INDEX_HTML = STATIC_DIR / "index.html"

# THE CHECKS THE DRIVER MUST EXECUTE, spelled from the Task 6c brief's Tests section.
FOOTER_CHECK = (
    "the footer's button is Rename N files from /list's renames, with the step pills shown"
)
FOOTER_NONE_CHECK = (
    "the footer with nothing to rename says Approve names to rename them and is off"
)
READY_CHECK = (
    "Ready to rename shows the server's folder in full, its counts and U6's copy, unticked"
)
TICK_CHECK = "the tick box gates Rename N files and the sentence under it"
UNTICKED_CHECK = "the box is unticked on arrival, even after going back with it ticked"
POST_ONCE_CHECK = (
    "Rename N files posts once on a double press, the nonce as received and folder_confirmed "
    "true, as JSON"
)
UNTICKED_PRESS_CHECK = "an unticked press posts nothing"
SYNC_CHECK = "the sync line is drawn only when the server names a provider"
LOW_CHECK = "the low line is drawn only when the batch holds a low name"
REFUSED_RENAME_CHECK = (
    "a refused rename shows the server's sentence verbatim and offers the way back"
)
REFUSED_CONFIRMATION_CHECK = (
    "a refused confirmation shows the server's sentence on the list and opens no screen"
)
PROGRESS_CHECK = (
    "the progress line counts with thousands and keeps the window sentence, the way back hidden"
)
DONE_CHECK = (
    "Done with no failure: the headline, the time with no problems, the left line and Undo"
)
DONE_FAILED_CHECK = (
    "Done with one failure: the headline says so, no time line, the job's lines under it"
)
LEFT_CHECK = "the left line at each boundary: not approved 0, skipped 0, both 0"
UNDO_ONCE_CHECK = "Undo this rename asks for the session's nonce and posts it as received, once"
UNDO_PROGRESS_CHECK = "the undo's progress line is Putting names back"
UNDO_ALL_CHECK = (
    "an undo that put every name back says All N names put back and the folder sentence"
)
UNDO_PART_CHECK = (
    "an undo that put back only some says how many could not be, and no folder sentence"
)
UNDO_ALREADY_CHECK = "an undo where some names were already back makes no folder claim"
CANCELLED_CHECK = "a cancelled rename shows the job's sentence verbatim and no headline"
DIED_CHECK = "a rename that died shows C6.2's heading and recovery's own line"
# M15 Task 5b (the maintainer's C6 ruling, 2026-11-16).
DIED_UNDO_JOB_CHECK = (
    "an undo that died keeps U12's heading over recovery's line, and no headline"
)
ALREADY_BACK_CHECK = "C6.1: names already back are said after the headline, and could not be only when one failed"
CLOUD_TIME_CHECK = "C6.8(a): a rename that skipped cloud-only files says its time with no tail"
KEYS_CHECK = (
    "the list's keys are off while the screen is open and back on after Keep checking names"
)
PILLS_CHECK = "the step pills mark Review done and Rename current, and go back with the list"
BACK_CHECK = "Keep checking names goes back to the list and asks for its numbers again"
# The Stage 1 page review's (M3335, M3340, M3347, M3352).
NEWEST_CHECK = "Confirm shows and sends the newest confirmation, never an earlier one"
SPENT_CHECK = "Rename N files stays off after a refused rename, however the box is ticked"
NO_UNDO_CHECK = "Done offers no Undo when nothing was renamed"
REFUSED_UNDO_CHECK = "a refused undo shows the server's sentence verbatim"
# S2-F1 (the controller's fix after Task 6 Stage 2): a died rename's session and its Undo.
DIED_UNDO_CHECK = (
    "a rename that died after renaming offers Undo when its session can still be undone"
)
DIED_NO_UNDO_CHECK = (
    "a died job offers no Undo when its session cannot be undone, names none, or was an undo"
)
# S1P-F2 (the controller's fix after the Stage 1 page review): a reload finds the rename again.
RELOAD_RUNNING_CHECK = "a reload during a rename brings back its progress, not the list"
RELOAD_DONE_CHECK = "a reload after a rename finished shows Done with its Undo"
RELOAD_UNDO_CHECK = "a reload during an undo brings back Putting names back"
RELOAD_NONE_CHECK = "a reload with no rename or undo leaves the list as it was"
# M14 Y3 (the HT-readiness amendment): the folder sentence only when the folder is as it was.
FOLDER_ONLY_CHECK = "an undo of the folder's only rename says the folder is exactly as it was"
FOLDER_NEWER_CHECK = (
    "an undo of the newer of two renames says its names came back and nothing about the folder"
)
ONE_NAME_CHECK = "one name put back is singular, with every name back and with some not"
# Task 9Y review (M3469): the undone session is the older of two and only the NEWER stands.
FOLDER_OLDER_CHECK = "an undo of the older of two renames, the newer still standing, says nothing about the folder"
# M14 Y2: Done offers `Start reading`, through the Reading step's one read flow.
READ_AFTER_RENAME_CHECK = (
    "after a rename Done offers Start reading, which opens the Reading step on the server's"
    " folder"
)
READ_AFTER_UNDO_CHECK = "after an undo Done offers Start reading too"
READ_ONCE_CHECK = (
    "Done's Start reading reads through the Reading step, once, and Review names opens the"
    " plan the read made"
)
READ_IN_FLIGHT_CHECK = "Done's Start reading does nothing while an undo is being asked for"
READ_NOT_RUN_CHECK = "a rename that did not run offers no Start reading"
READ_STOPPED_CHECK = "Done offers no Start reading once pam has stopped"

REQUIRED_CHECKS = (
    "one file gets its new name, in the singular (HT-M14 F5)",
    FOOTER_CHECK,
    FOOTER_NONE_CHECK,
    READY_CHECK,
    TICK_CHECK,
    UNTICKED_CHECK,
    POST_ONCE_CHECK,
    UNTICKED_PRESS_CHECK,
    SYNC_CHECK,
    LOW_CHECK,
    REFUSED_RENAME_CHECK,
    REFUSED_CONFIRMATION_CHECK,
    PROGRESS_CHECK,
    DONE_CHECK,
    DONE_FAILED_CHECK,
    LEFT_CHECK,
    UNDO_ONCE_CHECK,
    UNDO_PROGRESS_CHECK,
    UNDO_ALL_CHECK,
    UNDO_PART_CHECK,
    UNDO_ALREADY_CHECK,
    CANCELLED_CHECK,
    DIED_CHECK,
    KEYS_CHECK,
    PILLS_CHECK,
    BACK_CHECK,
    NEWEST_CHECK,
    SPENT_CHECK,
    NO_UNDO_CHECK,
    REFUSED_UNDO_CHECK,
    RELOAD_RUNNING_CHECK,
    RELOAD_DONE_CHECK,
    RELOAD_UNDO_CHECK,
    RELOAD_NONE_CHECK,
    DIED_UNDO_CHECK,
    DIED_NO_UNDO_CHECK,
    FOLDER_ONLY_CHECK,
    FOLDER_NEWER_CHECK,
    ONE_NAME_CHECK,
    READ_AFTER_RENAME_CHECK,
    READ_AFTER_UNDO_CHECK,
    READ_ONCE_CHECK,
    READ_IN_FLIGHT_CHECK,
    READ_NOT_RUN_CHECK,
    READ_STOPPED_CHECK,
    FOLDER_OLDER_CHECK,
    DIED_UNDO_JOB_CHECK,
    ALREADY_BACK_CHECK,
    CLOUD_TIME_CHECK,
)

# W16, W19 (U6), U11, U22 and the implementer's way back, each a whole literal in `pam-confirm.js`.
CONFIRM_SENTENCES = (
    "Approve names to rename them",
    "Ready to rename",
    "In this folder",
    "This is the right folder",
    "get their new names",
    "Not approved, left as they are",
    "Skipped by you",
    "Nothing is deleted, moved or edited. Only names change.",
    "Every change is written down before it happens.",
    "Undo puts every name back exactly. It never expires.",
    "As long as the files aren't renamed outside pam.",
    "Keep this window open or not — pam finishes either way.",
    "Keep checking names",
)
# Task 6b's refusal for an unticked box, carried in single quotes because it holds double ones.
TICK_FIRST_LITERAL = "'Tick \"This is the right folder\" first.'"
# W20, W21 and U12, each a whole literal in `pam-done.js`.
DONE_SENTENCES = (
    "Changed your mind?",
    "Undo this rename",
    "Your folder is exactly as it was before.",
    # M14 Y3 and Y2 (the amendment's words).
    " name put back",
    "Start reading",
    # C6.1 and C6.8(a) (the maintainer's ruling, 2026-11-16).
    " were already back under their old names.",
    " was already back under its old name.",
    " seconds.",
)
# U12 and C6.2 (the maintainer's ruling, 2026-11-16), each a whole literal in `pam-stopped.js`.
STOPPED_SENTENCES = (
    "pam finished putting things right after it stopped.",
    "pam stopped part-way. It has put things right.",
    "Some files were renamed before it stopped. You can put them back.",
)
# U6's order on the screen, by the ids `index.html` gives each part: the folder, the box, the
# counts, U22's sync line, the low line, the reassurances, the button.
U6_ORDER = (
    'id="confirm-folder"',
    'id="confirm-tick"',
    'id="confirm-renames"',
    'id="confirm-not-approved"',
    'id="confirm-skipped"',
    'id="confirm-sync"',
    'id="confirm-low"',
    'id="confirm-promises"',
    'id="confirm-rename"',
)

# AT COLLECTION: an emptied or cut list fails before any test runs (M743).
assert len(REQUIRED_CHECKS) >= 48, "the required check list is empty or cut"
assert len(set(REQUIRED_CHECKS)) == len(REQUIRED_CHECKS), "a check is named twice"
assert len(CONFIRM_SENTENCES) >= 13, "the Confirm sentence list is empty or cut"
assert len(DONE_SENTENCES) >= 8, "the Done sentence list is empty or cut"
assert len(STOPPED_SENTENCES) >= 3, "the stopped sentence list is empty or cut"
assert len(U6_ORDER) == 9, "U6's order went quiet"

# THE NEGATIVE CONTROLS: (script, variable, the line, its defect, the check that must go red, a
# check that must stay green). Written here, independently of the checks' own expectations.
PLANTS = (
    # Stage 1 mutation 14: "with no problems" printed when `failed > 0`.
    (
        DONE_JS,
        "PAM_DONE_JS",
        "if (result.failed !== 0) {",
        "if (false) {",
        DONE_FAILED_CHECK,
        DONE_CHECK,
    ),
    # U22's left line, operand by operand.
    (
        DONE_JS,
        "PAM_DONE_JS",
        "if (notApproved === 0 && skipped === 0) {",
        "if (skipped === 0) {",
        LEFT_CHECK,
        DONE_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "if (notApproved === 0 && skipped === 0) {",
        "if (notApproved === 0) {",
        LEFT_CHECK,
        DONE_CHECK,
    ),
    # W21's folder sentence, operand by operand.
    (
        DONE_JS,
        "PAM_DONE_JS",
        "if (result.failed === 0 && result.already_undone === 0) {",
        "if (result.already_undone === 0) {",
        UNDO_PART_CHECK,
        UNDO_ALL_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "if (result.failed === 0 && result.already_undone === 0) {",
        "if (result.failed === 0) {",
        UNDO_ALREADY_CHECK,
        UNDO_ALL_CHECK,
    ),
    # Undo's one post, and its nonce as received.
    (
        DONE_JS,
        "PAM_DONE_JS",
        "if (DONE.undoing || PAGE.stopped || !DONE.session) {",
        "if (PAGE.stopped || !DONE.session) {",
        UNDO_ONCE_CHECK,
        UNDO_ALL_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "postJson({ nonce: facts.nonce })",
        "postJson({ nonce: facts.nonce.slice(0, -1) })",
        UNDO_ONCE_CHECK,
        DONE_CHECK,
    ),
    # W20: the failure lines under the headline; the undo box; U14's line; U12's.
    (
        DONE_JS,
        "PAM_DONE_JS",
        "drawLine(f.left, leftLine(result.not_approved, skipped));\n  drawJobLines(f, job.lines);",
        "drawLine(f.left, leftLine(result.not_approved, skipped));\n  drawJobLines(f, []);",
        DONE_FAILED_CHECK,
        DONE_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "f.undoBox.hidden = !(result.renamed > 0 && job.session);",
        "f.undoBox.hidden = true;",
        DONE_CHECK,
        LEFT_CHECK,
    ),
    (
        STOPPED_JS,
        "PAM_STOPPED_JS",
        "drawJobLines(f, healed ? [] : job.lines);",
        "drawJobLines(f, []);",
        CANCELLED_CHECK,
        DIED_CHECK,
    ),
    (
        STOPPED_JS,
        "PAM_STOPPED_JS",
        'var healed = job.state === "failed" && Boolean(job.recovery);',
        "var healed = false;",
        DIED_CHECK,
        CANCELLED_CHECK,
    ),
    # The press's guard, operand by operand (the mock DOM, like a script, can click a disabled
    # button - so the guard, not the attribute, is what is held).
    (
        CONFIRM_JS,
        "PAM_CONFIRM_JS",
        "if (CONFIRM.posting || PAGE.stopped || !c.tick.checked || CONFIRM.facts === null) {",
        "if (PAGE.stopped || !c.tick.checked || CONFIRM.facts === null) {",
        POST_ONCE_CHECK,
        UNTICKED_PRESS_CHECK,
    ),
    (
        CONFIRM_JS,
        "PAM_CONFIRM_JS",
        "if (CONFIRM.posting || PAGE.stopped || !c.tick.checked || CONFIRM.facts === null) {",
        "if (CONFIRM.posting || PAGE.stopped || CONFIRM.facts === null) {",
        UNTICKED_PRESS_CHECK,
        POST_ONCE_CHECK,
    ),
    # The nonce as received, the box gating the button, and unticked on arrival.
    (
        CONFIRM_JS,
        "PAM_CONFIRM_JS",
        "var body = { nonce: CONFIRM.facts.nonce, folder_confirmed: true };",
        "var body = { nonce: CONFIRM.facts.nonce.trim(), folder_confirmed: true };",
        POST_ONCE_CHECK,
        UNTICKED_PRESS_CHECK,
    ),
    (
        CONFIRM_JS,
        "PAM_CONFIRM_JS",
        "c.rename.disabled = !c.tick.checked || CONFIRM.posting || PAGE.stopped;",
        "c.rename.disabled = CONFIRM.posting || PAGE.stopped;",
        TICK_CHECK,
        POST_ONCE_CHECK,
    ),
    (
        CONFIRM_JS,
        "PAM_CONFIRM_JS",
        "  c.tick.checked = false;\n",
        "",
        UNTICKED_CHECK,
        TICK_CHECK,
    ),
    # U22's sync line and U6's low line, each only when it applies.
    (
        CONFIRM_JS,
        "PAM_CONFIRM_JS",
        "return provider ? ",
        "return true ? ",
        SYNC_CHECK,
        LOW_CHECK,
    ),
    (
        CONFIRM_JS,
        "PAM_CONFIRM_JS",
        "if (low <= 0) {",
        "if (low < 0) {",
        LOW_CHECK,
        SYNC_CHECK,
    ),
    # A refusal verbatim: the rename's, and the confirmation's on the list.
    (
        CONFIRM_JS,
        "PAM_CONFIRM_JS",
        "      c.back.hidden = false;\n      if (failure instanceof ApiRefusal) {\n"
        "        showConfirmError(failure.message);",
        "      c.back.hidden = false;\n      if (failure instanceof ApiRefusal) {\n"
        '        showConfirmError("pam could not rename.");',
        REFUSED_RENAME_CHECK,
        REFUSED_CONFIRMATION_CHECK,
    ),
    (
        CONFIRM_JS,
        "PAM_CONFIRM_JS",
        "showListNote(failure.message);",
        'showListNote("pam could not rename.");',
        REFUSED_CONFIRMATION_CHECK,
        REFUSED_RENAME_CHECK,
    ),
    # The progress line: one formatter, ruling B3's lead for undo, the way back hidden.
    (
        CONFIRM_JS,
        "PAM_CONFIRM_JS",
        "thousands(job.done) + CONFIRM_COPY.of + thousands(job.total)",
        "job.done + CONFIRM_COPY.of + job.total",
        PROGRESS_CHECK,
        DONE_CHECK,
    ),
    (
        CONFIRM_JS,
        "PAM_CONFIRM_JS",
        'job.kind === "undo" ? ',
        "false ? ",
        UNDO_PROGRESS_CHECK,
        PROGRESS_CHECK,
    ),
    (
        CONFIRM_JS,
        "PAM_CONFIRM_JS",
        'c.back.hidden = name === "running";',
        "c.back.hidden = false;",
        PROGRESS_CHECK,
        DONE_CHECK,
    ),
    # W16's off footer, ruling B6's pills, and the way back asking again.
    (
        CONFIRM_JS,
        "PAM_CONFIRM_JS",
        "c.footerRename.disabled = n === 0 || PAGE.stopped;",
        "c.footerRename.disabled = PAGE.stopped;",
        FOOTER_NONE_CHECK,
        FOOTER_CHECK,
    ),
    (
        CONFIRM_JS,
        "PAM_CONFIRM_JS",
        'c.stepReview.setAttribute("data-step", on ? "done" : "current");',
        'c.stepReview.setAttribute("data-step", "current");',
        PILLS_CHECK,
        READY_CHECK,
    ),
    (
        CONFIRM_JS,
        "PAM_CONFIRM_JS",
        "  return refreshHeaders();\n",
        "  return null;\n",
        BACK_CHECK,
        PILLS_CHECK,
    ),
    # Ruling B2: the footer's N is `/list`'s `renames`, not another of its numbers.
    (
        LIST_JS,
        "PAM_LIST_JS",
        'callHook("renames", payload.renames);',
        'callHook("renames", payload.undecided);',
        FOOTER_CHECK,
        READY_CHECK,
    ),
    # The list's keys go quiet under Confirm.
    (
        KEYS_JS,
        "PAM_KEYS_JS",
        "if (PAGE.stopped || PAGE.confirming) {",
        "if (PAGE.stopped) {",
        KEYS_CHECK,
        READY_CHECK,
    ),
    # The Stage 1 page review's (M3335, M3340, M3347, M3352, M3357).
    (
        CONFIRM_JS,
        "PAM_CONFIRM_JS",
        "      CONFIRM.facts = facts;\n",
        "      CONFIRM.facts = CONFIRM.first = CONFIRM.first || facts;\n",
        NEWEST_CHECK,
        READY_CHECK,
    ),
    (
        CONFIRM_JS,
        "PAM_CONFIRM_JS",
        "c.rename.disabled = !c.tick.checked || CONFIRM.posting || PAGE.stopped;",
        "c.rename.disabled = !c.tick.checked || PAGE.stopped;",
        SPENT_CHECK,
        TICK_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "f.undoBox.hidden = !(result.renamed > 0 && job.session);",
        "f.undoBox.hidden = !job.session;",
        NO_UNDO_CHECK,
        DONE_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "drawJobLines(f, [failure.message]);",
        'drawJobLines(f, ["pam could not undo."]);',
        REFUSED_UNDO_CHECK,
        UNDO_ONCE_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "    confirmElements().back.hidden = true;\n",
        "",
        DONE_CHECK,
        DONE_FAILED_CHECK,
    ),
    # S1P-F2: the reload's job filter, operand by operand - a read's job opens Confirm, and an
    # undo's (or a rename's) never does.
    (
        CONFIRM_JS,
        "PAM_CONFIRM_JS",
        'if (CONFIRM.posting || (job.kind !== "rename" && job.kind !== "undo")) {',
        "if (CONFIRM.posting || job.kind === null) {",
        RELOAD_NONE_CHECK,
        RELOAD_RUNNING_CHECK,
    ),
    (
        CONFIRM_JS,
        "PAM_CONFIRM_JS",
        'if (CONFIRM.posting || (job.kind !== "rename" && job.kind !== "undo")) {',
        'if (CONFIRM.posting || job.kind !== "rename") {',
        RELOAD_UNDO_CHECK,
        RELOAD_RUNNING_CHECK,
    ),
    (
        CONFIRM_JS,
        "PAM_CONFIRM_JS",
        'if (CONFIRM.posting || (job.kind !== "rename" && job.kind !== "undo")) {',
        'if (CONFIRM.posting || job.kind !== "undo") {',
        RELOAD_RUNNING_CHECK,
        RELOAD_UNDO_CHECK,
    ),
    # S2-F1: a died rename's Undo - the server's word, each operand of the guard, the call.
    (
        STOPPED_JS,
        "PAM_STOPPED_JS",
        "f.undoBox.hidden = !facts.undoable;",
        "f.undoBox.hidden = false;",
        DIED_NO_UNDO_CHECK,
        DIED_UNDO_CHECK,
    ),
    (
        STOPPED_JS,
        "PAM_STOPPED_JS",
        'if (job.kind !== "rename" || !job.session) {',
        "if (!job.session) {",
        DIED_NO_UNDO_CHECK,
        DIED_UNDO_CHECK,
    ),
    (
        STOPPED_JS,
        "PAM_STOPPED_JS",
        'if (job.kind !== "rename" || !job.session) {',
        'if (job.kind !== "rename") {',
        DIED_NO_UNDO_CHECK,
        DIED_UNDO_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "return offerUndoAfterDeath(f, job);",
        "return null;",
        DIED_UNDO_CHECK,
        DIED_CHECK,
    ),
    # M14 Y3: the folder sentence - another session still undoable, a partial undo, the
    # sentence itself, a refused listing, and one name.
    (
        DONE_JS,
        "PAM_DONE_JS",
        "if (rows[i].undoable) {",
        "if (false) {",
        FOLDER_NEWER_CHECK,
        FOLDER_ONLY_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "if (!text.whole) {",
        "if (false) {",
        UNDO_PART_CHECK,
        UNDO_ALL_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "      drawLine(f.time, DONE_COPY.exactly);\n",
        "",
        FOLDER_ONLY_CHECK,
        FOLDER_NEWER_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "    function () {\n      return null;\n    }",
        "    function () {\n      drawLine(f.time, DONE_COPY.exactly);\n      return null;\n    }",
        FOLDER_NEWER_CHECK,
        FOLDER_ONLY_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        '(one ? "" : DONE_COPY.all)',
        "DONE_COPY.all",
        ONE_NAME_CHECK,
        UNDO_ALL_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "restored === 1 ? DONE_COPY.putBackOne : DONE_COPY.putBack",
        "DONE_COPY.putBack",
        ONE_NAME_CHECK,
        UNDO_ALL_CHECK,
    ),
    # M14 Y2: offered after a rename and after an undo, never on a stopped page or after a
    # rename that did not run; the press's guard operand by operand; one read flow.
    (
        DONE_JS,
        "PAM_DONE_JS",
        "    offerReadAgain(f);\n",
        '    if (job.kind !== "undo") {\n      offerReadAgain(f);\n    }\n',
        READ_AFTER_UNDO_CHECK,
        READ_AFTER_RENAME_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "f.read.hidden = PAGE.stopped;",
        "f.read.hidden = false;",
        READ_STOPPED_CHECK,
        READ_AFTER_RENAME_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "if (PAGE.stopped || DONE.pressing || f.read.hidden) {",
        "if (DONE.pressing || f.read.hidden) {",
        READ_STOPPED_CHECK,
        READ_AFTER_RENAME_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "if (PAGE.stopped || DONE.pressing || f.read.hidden) {",
        "if (PAGE.stopped || f.read.hidden) {",
        READ_IN_FLIGHT_CHECK,
        READ_AFTER_RENAME_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "if (PAGE.stopped || DONE.pressing || f.read.hidden) {",
        "if (PAGE.stopped || DONE.pressing) {",
        READ_NOT_RUN_CHECK,
        READ_AFTER_RENAME_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "  f.read.hidden = true;\n",
        "",
        READ_NOT_RUN_CHECK,
        READ_AFTER_RENAME_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        'var open = ping.status === "open" ? ping.plan_id : null;',
        "var open = ping.plan_id;",
        READ_AFTER_RENAME_CHECK,
        READ_NOT_RUN_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "      showConfirm(false);\n",
        "",
        READ_AFTER_RENAME_CHECK,
        READ_NOT_RUN_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "      showConfirm(false);\n",
        '      showConfirm(false);\n      postJob("api/job/read");\n',
        READ_ONCE_CHECK,
        READ_STOPPED_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "        DONE.reload();\n",
        "",
        READ_ONCE_CHECK,
        READ_AFTER_RENAME_CHECK,
    ),
    (
        APP_JS,
        "PAM_APP_JS",
        "var hand = open || openList;",
        "var hand = openList;",
        READ_ONCE_CHECK,
        READ_AFTER_RENAME_CHECK,
    ),
    # Task 9Y review (M3469): the rule reads every row but the newest.
    (
        DONE_JS,
        "PAM_DONE_JS",
        "      for (var i = 0; i < rows.length; i += 1) {",
        "      for (var i = 1; i < rows.length; i += 1) {",
        FOLDER_OLDER_CHECK,
        FOLDER_ONLY_CHECK,
    ),
    # M15 Task 5b - C6.1: the "already back" sentence, its singular, its guard, and `could not
    # be` only when a name failed.
    (
        DONE_JS,
        "PAM_DONE_JS",
        "  if (back > 0) {",
        "  if (false) {",
        ALREADY_BACK_CHECK,
        UNDO_PART_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "(back === 1 ? DONE_COPY.alreadyBackOne : DONE_COPY.alreadyBack)",
        "DONE_COPY.alreadyBack",
        ALREADY_BACK_CHECK,
        UNDO_PART_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        'head += result.failed > 0 ? ", " + thousands(result.failed) + DONE_COPY.couldNotBe : ".";',
        'head += ", " + thousands(result.failed) + DONE_COPY.couldNotBe;',
        ALREADY_BACK_CHECK,
        UNDO_PART_CHECK,
    ),
    # C6.8(a): the tail follows `cloud_only`.
    (
        DONE_JS,
        "PAM_DONE_JS",
        "var tail = result.cloud_only > 0 ? DONE_COPY.secondsOnly : DONE_COPY.noProblems;",
        "var tail = DONE_COPY.noProblems;",
        CLOUD_TIME_CHECK,
        DONE_CHECK,
    ),
    # C6.2: the heading for a died RENAME only, U12's kept for a died undo, and the second line
    # exactly when the server says the session can be undone.
    (
        STOPPED_JS,
        "PAM_STOPPED_JS",
        'var died = healed && job.kind === "rename";',
        "var died = healed;",
        DIED_UNDO_JOB_CHECK,
        DIED_CHECK,
    ),
    (
        STOPPED_JS,
        "PAM_STOPPED_JS",
        "drawLine(f.headline, died ? STOPPED_COPY.partWay : null);",
        "drawLine(f.headline, null);",
        DIED_CHECK,
        DIED_UNDO_JOB_CHECK,
    ),
    (
        STOPPED_JS,
        "PAM_STOPPED_JS",
        "drawLine(f.recoveryTitle, died ? null : STOPPED_COPY.recovered);",
        "drawLine(f.recoveryTitle, STOPPED_COPY.recovered);",
        DIED_CHECK,
        DIED_UNDO_JOB_CHECK,
    ),
    (
        STOPPED_JS,
        "PAM_STOPPED_JS",
        "drawLine(f.time, facts.undoable ? STOPPED_COPY.renamedBefore : null);",
        "drawLine(f.time, STOPPED_COPY.renamedBefore);",
        DIED_NO_UNDO_CHECK,
        DIED_UNDO_CHECK,
    ),
)
assert len(PLANTS) >= 66, "the negative controls went quiet"


# --- the stdlib half: never skips --------------------------------------------------------


def test_the_node_driver_ships_and_names_every_required_check():
    assert NODE_DRIVER.is_file()
    text = NODE_DRIVER.read_text(encoding="utf-8")
    assert len(text) > 8000
    for check in REQUIRED_CHECKS:
        assert f'test("{check}"' in text, check


@pytest.mark.parametrize("sentence", CONFIRM_SENTENCES)
def test_confirm_carries_w16_w19_u11_and_u22s_copy_verbatim(sentence):
    assert f'"{sentence}"' in CONFIRM_JS.read_text(encoding="utf-8"), sentence


def test_confirm_carries_the_tick_first_refusal_verbatim():
    assert TICK_FIRST_LITERAL in CONFIRM_JS.read_text(encoding="utf-8")


@pytest.mark.parametrize("sentence", DONE_SENTENCES)
def test_done_carries_w20_w21_and_u12s_copy_verbatim(sentence):
    assert f'"{sentence}"' in DONE_JS.read_text(encoding="utf-8"), sentence


@pytest.mark.parametrize("sentence", STOPPED_SENTENCES)
def test_a_stopped_rename_carries_u12_and_c6_2s_copy_verbatim(sentence):
    """M15 Task 5b: U12's heading and C6.2's two sentences, in `pam-stopped.js`."""
    assert f'"{sentence}"' in STOPPED_JS.read_text(encoding="utf-8"), sentence


def test_the_confirm_screen_is_in_u6s_order():
    html = INDEX_HTML.read_text(encoding="utf-8")
    at = [html.index(each) for each in U6_ORDER]
    assert at == sorted(at), [U6_ORDER[i] for i in range(len(at))]


def test_the_spelled_lists_hold_at_their_floors():
    """Task 9Y review (M3488, M3445's shape): each list's collection-time floor sits beside the
    list in one hunk, so a plant cut together with its floor was green - pytest runs the rows
    that are left. The floors are spelled again HERE, in another hunk."""
    assert len(PLANTS) >= 66
    assert len(REQUIRED_CHECKS) >= 48
    assert len(DONE_SENTENCES) >= 8
    assert len(STOPPED_SENTENCES) >= 3
    assert len(CONFIRM_SENTENCES) >= 13


def test_every_check_a_negative_control_names_is_required():
    for _, _, _, _, red, green in PLANTS:
        assert red in REQUIRED_CHECKS, red
        assert green in REQUIRED_CHECKS, green
        assert red != green


def test_the_lines_the_negative_controls_plant_against_are_there_once():
    for source, _, old, _, _, _ in PLANTS:
        assert source.read_text(encoding="utf-8").count(old) == 1, (source.name, old)


# --- the node half: skips where node is absent, which is why the half above exists ---------


def _run(env=None):
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, no caller-supplied args
        [shutil.which("node"), str(NODE_DRIVER)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env=env,
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_executes_every_check_it_claims():
    run = _run()
    assert run.returncode == 0, run.stdout + run.stderr
    ran = {line[3:] for line in run.stdout.splitlines() if line.startswith("ok ")}
    assert ran == set(REQUIRED_CHECKS), (
        sorted(set(REQUIRED_CHECKS) - ran),
        sorted(ran - set(REQUIRED_CHECKS)),
    )
    assert f"ran {len(REQUIRED_CHECKS)}" in run.stdout.splitlines()


def _planted(tmp_path, source, old, new, variable):
    planted = tmp_path / source.name
    planted.write_text(source.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
    return _run({**os.environ, variable: str(planted)})


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
@pytest.mark.parametrize(
    ("source", "variable", "old", "new", "red", "green"),
    PLANTS,
    ids=[f"{n:02d}-{plant[0].name}" for n, plant in enumerate(PLANTS)],
)
def test_the_node_driver_fails_when_a_defect_is_planted(
    tmp_path, source, variable, old, new, red, green
):
    """THE NEGATIVE CONTROL, RUN: one defect planted in a copy - its check goes red, another
    stays green, so the run is not simply broken."""
    run = _planted(tmp_path, source, old, new, variable)
    assert run.returncode == 1, run.stdout + run.stderr
    assert f"FAIL {red}" in run.stdout, run.stdout
    assert f"ok {green}" in run.stdout, run.stdout


# --- ruling B5's overlay sentence and ruling B6's wide-only pills (the Stage 1 page review) ---
STYLE_CSS = STATIC_DIR / "style.css"
# B5 (the controller's ruling, pending the design model): the `?` overlay's sentence, which
# replaced "Nothing on this page renames a file." once the page renames (B1).
B5_OVERLAY = (
    "<p>Nothing is renamed until you confirm. Typing in the name box turns these keys off.</p>"
)


def test_the_shortcuts_overlay_says_b5s_sentence():
    html = INDEX_HTML.read_text(encoding="utf-8")
    overlay = html[html.index('id="shortcuts"') :]
    assert B5_OVERLAY in overlay
    assert "Nothing on this page renames" not in html


def test_the_step_pills_show_at_wide_widths_only():
    """W23 / ruling B6: `pam-confirm.js` un-hides `#steps`, and only the stylesheet keeps the
    pills off a narrow window - hidden by default, shown inside the 1100 px query."""
    css = STYLE_CSS.read_text(encoding="utf-8")
    base = css.index(".review-page .steps {\n")
    assert css[base : css.index("}", base)].count("display: none;") == 1
    wide = css.index("@media (min-width: 1100px) {\n  .review-page .steps {\n")
    assert "display: flex;" in css[wide : css.index("}", wide)]
