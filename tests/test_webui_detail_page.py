"""`tests/js/detail_test.js` executed, and the floor that keeps it honest (M14 Task 2 Step 6a).

`pam-detail.js` holds the review list's preview overlay (U16) and its one name editor (A6),
moved off the card grid and onto the list's rows. The node driver executes it over a
hand-written DOM and a stub server, wired as `app.js` wires it, and drives it through the
list's own key handler; this module runs it and reads the names it prints - every check prints
`ok <name>` and the run ends with `ran <n>` - so a known set must actually EXECUTE.

THE SHAPE IS `tests/test_webui_list_page.py`'s, both halves: the node half skips where `node`
is absent, and the stdlib half below NEVER skips (CLAUDE.md, M783/M784).

THE NEGATIVE CONTROL IS RUN: `C`'s hook is cut in a COPY of `pam-detail.js` through
`PAM_DETAIL_JS`, and the driver must go red on the check for it while another stays green.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
NODE_DRIVER = TESTS_DIR / "js" / "detail_test.js"
STATIC_DIR = TESTS_DIR.parent / "pam" / "webui" / "static"
DETAIL_JS = STATIC_DIR / "pam-detail.js"
APP_JS = STATIC_DIR / "app.js"

# THE CHECKS THE DRIVER MUST EXECUTE, spelled from the controller's Step 6a rulings, U16 and A6.
REQUIRED_CHECKS = (
    # U2/U16: Enter opens the row's preview, and the arrows move it without closing
    "Enter on a row opens that row's preview",
    # U16: opened by the thumbnail or Enter
    "the row's thumbnail opens the preview on that row",
    # U16: the decision keys and buttons unchanged - through the existing decide
    "the preview's Approve decides that row through decide and moves to the next undecided",
    # U2: C edits the name
    "C opens the editor for that row",
    # HT-M7: X, Z and C are inert while the name field has focus
    "X, Z and C typed into the name field do not act",
    # A6 / design 8.2: the existing rename endpoint, the unit renamed whole
    "a rename sends the existing endpoint's body for that row's id and repaints its unit",
    # spec 6.2 rule 7: the server's sentence at the field
    "a rename the server adjusted keeps the editor open with its sentence",
    # Step 6b (the controller's ruling, UX 5 rev 8, A6): the card's `↺` moved onto the ROW -
    # on an edited row only, through the existing restore endpoint and body
    "the row's restore shows only on an edited row and sends the existing body",
)

# AT COLLECTION: an emptied or cut list fails before any test runs (M743).
assert len(REQUIRED_CHECKS) >= 8, "the required check list is empty or cut"

# The defect the run control plants: `C` hands the row to nothing.
EDIT_HOOK = "LIST.hooks.edit = openEditor;"
NO_EDIT_HOOK = "LIST.hooks.edit = function () {};"
# Step 6b's: the row's `↺` hands the row to nothing.
RESTORE_HOOK = "LIST.hooks.restore = restoreRow;"
NO_RESTORE_HOOK = "LIST.hooks.restore = function () {};"


# --- the stdlib half: never skips --------------------------------------------------------


def test_the_node_driver_ships_and_names_every_required_check():
    assert NODE_DRIVER.is_file()
    text = NODE_DRIVER.read_text(encoding="utf-8")
    assert len(text) > 8000
    for check in REQUIRED_CHECKS:
        assert f'test("{check}"' in text, check


def test_app_js_wires_the_detail_and_the_line_the_control_plants_against_exists():
    """The driver calls `wireDetail()` itself, so it cannot see `app.js` forget to - this
    can. And if the hook line is reworded, the control below stops planting anything."""
    assert "\n  wireDetail();\n" in APP_JS.read_text(encoding="utf-8")
    assert DETAIL_JS.read_text(encoding="utf-8").count(EDIT_HOOK) == 1
    assert DETAIL_JS.read_text(encoding="utf-8").count(RESTORE_HOOK) == 1


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
def test_the_node_driver_fails_when_c_opens_nothing(tmp_path):
    """THE NEGATIVE CONTROL, RUN: `C`'s hook cut in a copy, red on the check for it."""
    planted = tmp_path / "pam-detail.js"
    source = DETAIL_JS.read_text(encoding="utf-8")
    planted.write_text(source.replace(EDIT_HOOK, NO_EDIT_HOOK), encoding="utf-8")
    run = _run({**os.environ, "PAM_DETAIL_JS": str(planted)})
    assert run.returncode == 1, run.stdout + run.stderr
    assert "FAIL C opens the editor for that row" in run.stdout
    assert "ok Enter on a row opens that row's preview" in run.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_the_rows_restore_puts_nothing_back(tmp_path):
    """THE NEGATIVE CONTROL, RUN (Step 6b): the row's `↺` hook cut in a copy, red on the
    restore check while the preview's stays green."""
    planted = tmp_path / "pam-detail.js"
    source = DETAIL_JS.read_text(encoding="utf-8")
    planted.write_text(source.replace(RESTORE_HOOK, NO_RESTORE_HOOK), encoding="utf-8")
    run = _run({**os.environ, "PAM_DETAIL_JS": str(planted)})
    assert run.returncode == 1, run.stdout + run.stderr
    assert "FAIL the row's restore shows only on an edited row and sends the existing body" in (
        run.stdout
    )
    assert "ok Enter on a row opens that row's preview" in run.stdout
