"""`tests/js/list_test.js` executed, and the floor that keeps it honest (M14 Task 2 Steps 3-4).

The review list is two classic scripts nothing else in this suite runs: `pam-row.js` (one row,
pure) and `pam-list.js` (the progress line, the tabs, the groups and their paging, the circle's
decision). The node driver executes them over a hand-written DOM and a stub server; this module
runs it and reads the names it prints - every check prints `ok <name>` and the run ends with
`ran <n>` - so a known set must actually EXECUTE, not merely exit 0.

THE SHAPE IS `tests/test_app_provenance.py`'s, including its two halves. `node` is an external
tool on the exiftool precedent and a machine may not have it, so the node half skips where it is
absent - and the stdlib half below NEVER skips, so a deleted driver or a deleted sentence is red
on either machine (CLAUDE.md, M783/M784).

THE ROW'S COPY IS SPELLED HERE, from U2/W7-W10, UX 5 rev 11 and the controller's Task 2 ruling
for the two codes W10 does not cover - never read back out of `pam-row.js`. The list is asserted
non-empty AT COLLECTION (T2.8): pytest SKIPS an empty parametrize rather than failing it (M743).

THE NEGATIVE CONTROL IS RUN: T2.4's defect - an approved row's circle sending `approve` - is
planted in a COPY of `pam-row.js` through `PAM_ROW_JS`, and the driver must go red on the check
that exists for it. Nothing on disk is touched.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
NODE_DRIVER = TESTS_DIR / "js" / "list_test.js"
STATIC_DIR = TESTS_DIR.parent / "pam" / "webui" / "static"
ROW_JS = STATIC_DIR / "pam-row.js"
LIST_JS = STATIC_DIR / "pam-list.js"
CORE_JS = STATIC_DIR / "pam-core.js"

# THE CHECKS THE DRIVER MUST EXECUTE, spelled from the T2 mutation list and Steps 3-4.
REQUIRED_CHECKS = (
    "a thumbnail that opens the preview is a named control Tab and Enter reach (HT-M14 F4)",
    "a thumbnail that fails to load is replaced by a glyph that still opens the preview",
    # T2.8's cases, each a spelled string
    "every row-copy case reads its spelled string",
    # U15/W10: the needs-a-look note per note_code, only while undecided
    "each note code gives its sentence while undecided and none once decided",
    # T2.6
    "a no-op member of a group carries UX 5 rev 11's sentence verbatim",
    # U2: unchanged copy
    "the look-alike line is UX 5's unchanged sentence",
    # W9: the unit disclosure, from the server's `unit_kind`
    "the unit disclosure reads the server's kind and count, and a lone file says nothing",
    # U2: a video glyph for a video
    "a video row draws the video glyph and never asks for a thumbnail",
    # Task 2 review R2 (M2869-M2873): buildRow DRAWS each line rowModel holds
    "the row draws its was line, label, decision word, look-alike line and note",
    "the circle's action is reset from approved and approve from proposed or skipped",
    # T2.4, through the page's own click
    "an approved row's circle sends reset through the existing decide",
    # T2.5
    "the progress line shows the decision response's counts, not local arithmetic",
    # design 7.3: the partners a widened decision moved are repainted
    "a decision that widened to a companion repaints the partner's row",
    # T2.3's page half
    "Show N more pages through a group and shows every id exactly once",
    # W2 and U2's header words
    "the progress line and the group header read the specs' words",
    # W5
    "the tabs read All N and Needs a look K, K from the server's undecided set",
    "the Needs a look view asks every group for attention items only",
    # Step 5 (U2, U16): the keys, and HT-M7's inert-in-the-field check
    "each of U2's keys maps to its action in the list and in the preview",
    "X, Z and C stay inert inside the name field and every other field",
    "the open shortcut list takes only its own closing keys",
    "down moves the cursor, X approves and advances, X again resets and stays",
    "Z skips, and Z on a skipped row resets",
    # M14 Task 3 (U3): Space selects
    "Space hands the row to the selection, and Enter, C and ? hand the row to their surfaces",
    # Step 6a (U2, U3): Accept all stays, and the list refreshes from the server after it
    "Accept all reloads the list, and every count it shows is the server's",
    # Step 6b part 2: moved from the card page (UX 5's search and sort, its empty-filter and
    # plan-decided states, and UX 5 rev 9 / B1's pass complete)
    "search and sort reach every group's items request, and clear filters drops the search",
    "a view the server leaves empty shows UX 5's empty state, and one with rows hides it",
    "the view's last undecided decision reports the pass complete and leaves the row",
    "undecided rows in pages not loaded yet keep the pass open",
    "the done panel shows only when nothing is undecided, from the server's counts",
    # Step 6b part 2, half 2: U8 W3/W4 with OD-3's sentence, UX 5's resume and estimate,
    # Accept all's count (F1 R2) and UX 5's fourth state, on the list
    "the callout reads W3 with OD-3's sentence, and its button opens the Needs a look tab",
    "the callout reads W4 when nothing undecided needs a look, and is gone when all is decided",
    # Step 6b final: W3's singular (an inflection of the spelled copy, the Task 2 ruling)
    "the callout reads W3's singular for one name and its plural for two",
    "the resume banner reads UX 5 rev 8's line and Jump lands on the first undecided row",
    "Jump with no undecided row loaded narrows the view to the undecided",
    "the estimate stays hidden until ten paced decisions and then reads at your pace",
    "Accept all's count is the plan's undecided high plus medium, from the server",
    "a dead server shows the list's error banner and the page goes inert",
    # Task 2 review R2 (M2871): pam-keys.js's stop guard, executed
    "a key on a stopped page decides nothing, and the same key decides while it runs",
    # M14 Task 3 (Task 2 review R2 F3): the decide path's own stop guard
    "a stopped page's circle decides nothing, because the decide path checks it too",
    # M14 Task 3 slice 3d (OD-1, U3, W3, W12): the one-tap shortcuts, N always the server's
    "a group shows Approve N from the server, pam isn't sure of these when all low, and neither at 0",
    "a group's Approve N posts 3b's body with its key and the view, then reloads the list and its counts",
    "Approve the other N posts the view with no list group, and /list is asked with the view and never the sort",
    "on a stopped page a shortcut writes nothing",
    # Task 3 review R2's closures (F3): the shortcut buttons as drawn, and their refresh
    "a group's drawn Approve N button posts that group's key",
    "a header refresh repaints each group's Approve N and the callout's N from the fresh /list",
    # Task 3 review R2 F4 (the controller's fix): one shortcut write at a time
    "a double click on a shortcut posts once, and the next click after it settles posts again",
)

# EVERY SENTENCE A ROW CAN SHOW, spelled from the specs (T2.8's independent list). W7's three
# decision words, W8's `was`, UX 5 rev 8's edited label, UX 5 rev 11's no-op sentence, W10's
# three notes, and the controller's two reused sentences for `utc_video` and `truncated`.
ROW_SENTENCES = (
    "Approved",
    "Skipped, keeps its name",
    "Not decided",
    "was ",
    "Edited by you",
    "This file already has the right name — it is here because pam renames a group together.",
    "The date came from when the file was last changed, not from the camera. Check it.",
    "The date was read from the date printed on the photo. Check it matches.",
    "No date found anywhere. You can type one in.",
    "Video time is UTC, local date may differ.",
    "Part of this name was shortened to fit.",
    # W9's two kind words
    "Live Photo",
    "RAW + JPEG",
)

# AT COLLECTION: an emptied or cut list fails before any test runs (M743).
assert len(ROW_SENTENCES) >= 13, "the row sentence list is empty or cut"

# Step 5's defect: the field guard gone, so typing a name in the editor decides the row
# (HT-M7's check - `X`/`Z`/`C` stay inert in the name field).
FIELD_GUARD = 'if (tag === "input" || tag === "select" || tag === "textarea") {'
NO_FIELD_GUARD = "if (false) {"

# Step 6a's defect: Accept all writes and never asks the server for the list again.
ACCEPT_RELOAD = "return Promise.all([loadGroups(), loadAttentionTodo()]).then(function () {"
NO_ACCEPT_RELOAD = "return Promise.resolve().then(function () {"

# M14 Task 3 slice 3d's defect: a shortcut posted without the view - the write would take a
# broader set than the N `/list` counted over that view.
SHORTCUT_VIEW = "var params = listViewParams(view);"
NO_SHORTCUT_VIEW = "var params = {};"

# T2.4's defect, as the mutation would apply it.
TOGGLE = 'return item.status === "approved" ? "reset" : "approve";'
NO_TOGGLE = 'return "approve";'


# --- the stdlib half: never skips --------------------------------------------------------


def test_the_required_check_list_is_not_empty():
    # 6b final: + W3's singular; Task 2 review R2: + buildRow's lines, + the keys' stop guard;
    # M14 Task 3: + the decide path's stop guard; slice 3d: + the four shortcut checks
    assert len(REQUIRED_CHECKS) >= 44


def test_the_node_driver_ships_and_is_not_empty():
    assert NODE_DRIVER.is_file()
    assert len(NODE_DRIVER.read_text(encoding="utf-8")) > 8000
    for check in REQUIRED_CHECKS:
        assert f'test("{check}"' in NODE_DRIVER.read_text(encoding="utf-8"), check


@pytest.mark.parametrize("sentence", ROW_SENTENCES)
def test_the_row_scripts_carry_every_row_sentence_verbatim(sentence):
    """The copy, in the files the page loads - `pam-core.js` holds the two the card page
    shares (`Not decided`, the no-op sentence) as its ONE constant each."""
    text = ROW_JS.read_text(encoding="utf-8") + CORE_JS.read_text(encoding="utf-8")
    assert f'"{sentence}' in text, sentence


def test_the_guard_the_negative_control_plants_against_is_in_the_row():
    """If the toggle's line is reworded, the control below stops planting anything."""
    assert ROW_JS.read_text(encoding="utf-8").count(TOGGLE) == 1
    assert LIST_JS.is_file()
    # Step 5: the field guard the second control plants against, exactly once.
    assert LIST_JS.read_text(encoding="utf-8").count(FIELD_GUARD) == 1
    # Step 6a: Accept all's reload, which the third control removes, exactly once.
    assert LIST_JS.read_text(encoding="utf-8").count(ACCEPT_RELOAD) == 1
    # Slice 3d: the shortcut body's view, which the fourth control removes, exactly once.
    assert LIST_JS.read_text(encoding="utf-8").count(SHORTCUT_VIEW) == 1


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


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_an_approved_circle_sends_approve(tmp_path):
    """THE NEGATIVE CONTROL, RUN (T2.4): planted in a copy, red on the check for it."""
    planted = tmp_path / "pam-row.js"
    source = ROW_JS.read_text(encoding="utf-8")
    planted.write_text(source.replace(TOGGLE, NO_TOGGLE), encoding="utf-8")
    run = _run({**os.environ, "PAM_ROW_JS": str(planted)})
    assert run.returncode == 1, run.stdout + run.stderr
    assert "FAIL an approved row's circle sends reset through the existing decide" in run.stdout
    assert "ok a no-op member of a group carries UX 5 rev 11's sentence verbatim" in run.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_the_name_field_lets_x_z_c_through(tmp_path):
    """THE NEGATIVE CONTROL, RUN (Step 5): the field guard deleted in a copy of
    `pam-list.js`, and the inert-in-the-field check must go red while another stays green."""
    planted = tmp_path / "pam-list.js"
    source = LIST_JS.read_text(encoding="utf-8")
    planted.write_text(source.replace(FIELD_GUARD, NO_FIELD_GUARD), encoding="utf-8")
    run = _run({**os.environ, "PAM_LIST_JS": str(planted)})
    assert run.returncode == 1, run.stdout + run.stderr
    assert (
        "FAIL X, Z and C stay inert inside the name field and every other field" in run.stdout
    )
    assert "ok the look-alike line is UX 5's unchanged sentence" in run.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_accept_all_does_not_reload_the_list(tmp_path):
    """THE NEGATIVE CONTROL, RUN (Step 6a): Accept all's reload removed in a copy of
    `pam-list.js`, and the Accept all check must go red while another stays green."""
    planted = tmp_path / "pam-list.js"
    source = LIST_JS.read_text(encoding="utf-8")
    planted.write_text(source.replace(ACCEPT_RELOAD, NO_ACCEPT_RELOAD), encoding="utf-8")
    run = _run({**os.environ, "PAM_LIST_JS": str(planted)})
    assert run.returncode == 1, run.stdout + run.stderr
    assert (
        "FAIL Accept all reloads the list, and every count it shows is the server's"
        in run.stdout
    )
    assert "ok the look-alike line is UX 5's unchanged sentence" in run.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_a_shortcut_drops_the_view(tmp_path):
    """THE NEGATIVE CONTROL, RUN (M14 Task 3 slice 3d): the shortcut body built without the
    view in a copy of `pam-list.js` - the body check must go red, another check stays green."""
    planted = tmp_path / "pam-list.js"
    source = LIST_JS.read_text(encoding="utf-8")
    planted.write_text(source.replace(SHORTCUT_VIEW, NO_SHORTCUT_VIEW), encoding="utf-8")
    run = _run({**os.environ, "PAM_LIST_JS": str(planted)})
    assert run.returncode == 1, run.stdout + run.stderr
    assert (
        "FAIL a group's Approve N posts 3b's body with its key and the view, then reloads the"
        " list and its counts" in run.stdout
    )
    assert "ok on a stopped page a shortcut writes nothing" in run.stdout
