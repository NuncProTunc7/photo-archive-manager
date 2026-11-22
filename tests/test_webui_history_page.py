"""`tests/js/history_test.js` executed, and the floor that keeps it honest (M14 Task 7).

History (UX 5 W22, X2): a row per rename session, reached from Done (T7-R1), with its time label
from an injected "now" (T7-R5), `N renamed` / `N renamed · undone`, `Report`, and `Undo this
rename` exactly when the server says the session can still be undone - Done's own undo, not a
second one. And ruling B4: Done's `See what happened`, under a failure headline only. It lives in
`pam-history.js`, with the seams in `pam-done.js` (`undoSession`, `reportPath`) and
`pam-confirm.js` (`showPart`'s fourth part). The node driver loads the page's fifteen scripts into
one context, in the page's order; this module runs it and reads the names it prints - every check
prints `ok <name>` and the run ends with `ran <n>` - so a known set must actually EXECUTE.

THE SHAPE IS `tests/test_webui_confirm_page.py`'s. `node` is an external tool on the exiftool
precedent, so the node half skips where it is absent - and the stdlib half NEVER skips, so a
deleted driver or a deleted sentence is red on either machine (CLAUDE.md, M783/M784).

THE COPY IS SPELLED HERE from W22, W20, U22 / B4 and the implementer's words pending the design
model - never read back out of the scripts. The lists are asserted non-empty AT COLLECTION (M743:
pytest SKIPS an empty parametrize).

THE NEGATIVE CONTROLS ARE RUN: each plants one defect in a COPY of a script through
`PAM_HISTORY_JS` / `PAM_DONE_JS` / `PAM_CONFIRM_JS`, and the driver must go red on the check that
exists for it while another stays green. Nothing on disk is touched.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
NODE_DRIVER = TESTS_DIR / "js" / "history_test.js"
STATIC_DIR = TESTS_DIR.parent / "pam" / "webui" / "static"
HISTORY_JS = STATIC_DIR / "pam-history.js"
DONE_JS = STATIC_DIR / "pam-done.js"
CONFIRM_JS = STATIC_DIR / "pam-confirm.js"
WELCOME_JS = STATIC_DIR / "pam-welcome.js"
INDEX_HTML = STATIC_DIR / "index.html"

# THE CHECKS THE DRIVER MUST EXECUTE, spelled from the Task 7 brief's Tests section.
OPEN_CHECK = "Done after a rename carries History, and History opens from it"
UNDONE_DONE_CHECK = "Done after an undo carries History too"
BACK_CHECK = "Back returns to the Done History came from, as it was"
ROWS_CHECK = "the rows are the server's, in its order, with W22's labels from an injected now"
LABEL_CHECK = "the time label is Today, Yesterday or the date, on both sides of midnight"
COUNT_CHECK = "a row reads N renamed, or N renamed · undone"
UNDOABLE_CHECK = (
    "Undo this rename is drawn exactly when the server says the session can be undone"
)
REPORT_CHECK = "Report links to the session's report in a new tab, only when the report exists"
EMPTY_CHECK = "an archive with no sessions says so in one sentence"
REFUSED_LIST_CHECK = "a refused listing shows the server's sentence verbatim"
UNDO_CHECK = (
    "a row's Undo this rename posts once, the nonce as received, then Done after the undo"
)
REFUSED_UNDO_CHECK = (
    "a refused undo from History shows the server's sentence where Done's would"
)
B4_CHECK = "B4: See what happened is drawn under a failure headline only, linking to the report"
# Task 7 review (M3401, R7-F4): History's guard on a stopped page.
STOPPED_CHECK = "a row's Undo does nothing once pam has stopped"
# M15 Task 2 (AM2): History's count of files a rename skipped because they became cloud-only.
CLOUD_ONLY_CHECK = (
    "AM2: a row says how many files became cloud-only, only when its report counted any"
)
# M15 Task 3 (AM3, E7): History from the list header and from Welcome, and Back to where it was opened.
AM3_LIST_CHECK = "AM3: the list header's History is drawn exactly when the folder has a session"
AM3_WELCOME_CHECK = (
    "AM3: Welcome's History is drawn exactly when the bound folder has a session"
)
E7_LIST_CHECK = "E7: History from the list lists the rows, and Back returns to the list"
E7_WELCOME_CHECK = "E7: History from Welcome lists the rows, and Back returns to Welcome"
# Task 3 review (M3610, M3615): choosing another folder hides Welcome's link; an undo from it has no list to go back to.
AM3_CHANGE_CHECK = "AM3: choosing another folder takes Welcome's History away"
E7_UNDO_CHECK = "E7: an undo from Welcome's History reaches Done with no Keep checking names"
# R7-F3 (the controller's fix after the Task 7 review): one undo in flight across Done and History.
IN_FLIGHT_CHECK = "an undo already in flight from Done is not sent again from History's row"

REQUIRED_CHECKS = (
    "a session with no report says No report, with the server's reason, and links nothing",
    OPEN_CHECK,
    UNDONE_DONE_CHECK,
    BACK_CHECK,
    ROWS_CHECK,
    LABEL_CHECK,
    COUNT_CHECK,
    UNDOABLE_CHECK,
    REPORT_CHECK,
    EMPTY_CHECK,
    REFUSED_LIST_CHECK,
    UNDO_CHECK,
    REFUSED_UNDO_CHECK,
    B4_CHECK,
    STOPPED_CHECK,
    IN_FLIGHT_CHECK,
    CLOUD_ONLY_CHECK,
    AM3_LIST_CHECK,
    AM3_WELCOME_CHECK,
    E7_LIST_CHECK,
    E7_WELCOME_CHECK,
    AM3_CHANGE_CHECK,
    E7_UNDO_CHECK,
)

# W22 and W20's words, verbatim; then the implementer's (Task 7), pending the design model.
HISTORY_SENTENCES = (
    "History",
    " renamed",
    " · undone",
    "Report",
    "Undo this rename",
    "Back",
    "pam has not renamed anything in this folder yet.",
    "Today, ",
    "Yesterday, ",
    # AM2 (M15 Task 2), verbatim after the count.
    " files became cloud-only after they were approved and were not renamed."
    " Make them available offline and read the folder again.",
    # C6.5 (owner, 2026-11-16): the singular, and the sentence for a unit of several members.
    "1 file became cloud-only after it was approved and was not renamed."
    " Make it available offline and read the folder again.",
    " A photo and the files that go with it are skipped together.",
)
# U22 / ruling B4, in `pam-done.js`.
DONE_SENTENCES = ("See what happened",)
# The parts `index.html` must carry for History and B4.
HISTORY_IDS = (
    'id="finish-history"',
    'id="finish-report-box"',
    'id="finish-report"',
    'id="history"',
    'id="history-title"',
    'id="history-empty"',
    'id="history-rows"',
    'id="history-error"',
    'id="history-back"',
    # M15 Task 3 (AM3): the list header's and Welcome's controls.
    'id="list-history"',
    'id="welcome-history"',
)

# AT COLLECTION: an emptied or cut list fails before any test runs (M743).
assert len(REQUIRED_CHECKS) >= 23, "the required check list is empty or cut"
assert len(set(REQUIRED_CHECKS)) == len(REQUIRED_CHECKS), "a check is named twice"
assert len(HISTORY_SENTENCES) >= 12, "the History sentence list is empty or cut"
assert len(DONE_SENTENCES) >= 1, "the B4 sentence went quiet"
assert len(HISTORY_IDS) == 11, "the History parts went quiet"

# THE NEGATIVE CONTROLS: (script, variable, the line, its defect, the check that must go red, a
# check that must stay green). Written here, independently of the checks' own expectations.
PLANTS = (
    # Task 3 review M3610: an undo from Welcome's History offers Keep checking names.
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        '  if (HISTORY.origin === "welcome") {\n    confirmElements().back.hidden = true;\n  }',
        "",
        E7_UNDO_CHECK,
        E7_WELCOME_CHECK,
    ),
    # Task 3 review M3615: Welcome's link survives a change of folder.
    (
        WELCOME_JS,
        "PAM_WELCOME_JS",
        "    drawHistoryLinks(false);\n",
        "",
        AM3_CHANGE_CHECK,
        AM3_WELCOME_CHECK,
    ),
    # AM3: the list header's control is drawn whether or not a session exists.
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        "  h.listLink.hidden = !any;",
        "  h.listLink.hidden = false;",
        AM3_LIST_CHECK,
        AM3_WELCOME_CHECK,
    ),
    # AM3: ... and never drawn, whatever the server holds.
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        "  h.listLink.hidden = !any;",
        "  h.listLink.hidden = true;",
        AM3_LIST_CHECK,
        AM3_WELCOME_CHECK,
    ),
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        "  h.welcomeLink.hidden = !any;",
        "  h.welcomeLink.hidden = false;",
        AM3_WELCOME_CHECK,
        AM3_LIST_CHECK,
    ),
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        "  h.welcomeLink.hidden = !any;",
        "  h.welcomeLink.hidden = true;",
        AM3_WELCOME_CHECK,
        AM3_LIST_CHECK,
    ),
    # E7: Back always returns to Done, whatever opened History.
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        '  if (HISTORY.origin === "list") {\n    /* History',
        "  if (false) {\n    /* History",
        E7_LIST_CHECK,
        E7_WELCOME_CHECK,
    ),
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        '  if (HISTORY.origin === "welcome") {\n    showPart("ready");',
        '  if (false) {\n    showPart("ready");',
        E7_WELCOME_CHECK,
        E7_LIST_CHECK,
    ),
    # E7: the origin is never recorded, so every Back is Done's.
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        "  HISTORY.origin = origin;",
        '  HISTORY.origin = "done";',
        E7_LIST_CHECK,
        AM3_LIST_CHECK,
    ),
    # AM2: History never says the cloud-only count.
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        "  if (cloudOnly) {",
        "  if (false) {",
        CLOUD_ONLY_CHECK,
        COUNT_CHECK,
    ),
    # C6.5: the singular, and TOGETHER only when files > units.
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        "row.cloud_only === 1 ? HISTORY_COPY.cloudOnlyOne : ",
        "false ? HISTORY_COPY.cloudOnlyOne : ",
        CLOUD_ONLY_CHECK,
        COUNT_CHECK,
    ),
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        "return row.cloud_only > row.cloud_only_units ? ",
        "return true ? ",
        CLOUD_ONLY_CHECK,
        COUNT_CHECK,
    ),
    # T7 (7): the row draws Undo whatever `undoable` says.
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        "if (row.undoable) {",
        "if (true) {",
        UNDOABLE_CHECK,
        ROWS_CHECK,
    ),
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        "if (row.undoable) {",
        "if (false) {",
        UNDOABLE_CHECK,
        COUNT_CHECK,
    ),
    # W22's `· undone`, from the server's flag only.
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        '(row.undone ? HISTORY_COPY.undoneTail : "")',
        '(row.undoable ? "" : HISTORY_COPY.undoneTail)',
        COUNT_CHECK,
        ROWS_CHECK,
    ),
    # The labels: today, yesterday, the 12 o'clock hour.
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        "if (sameDay(year, month, day, now)) {",
        "if (false) {",
        LABEL_CHECK,
        COUNT_CHECK,
    ),
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        "now.getDate() - 1",
        "now.getDate() - 2",
        LABEL_CHECK,
        COUNT_CHECK,
    ),
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        "var h = hour % 12 === 0 ? 12 : hour % 12;",
        "var h = hour % 12;",
        LABEL_CHECK,
        COUNT_CHECK,
    ),
    # Report only when the report exists.
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        "if (row.report) {",
        "if (true) {",
        REPORT_CHECK,
        ROWS_CHECK,
    ),
    # The empty sentence.
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        "h.empty.hidden = rows.length !== 0;",
        "h.empty.hidden = true;",
        EMPTY_CHECK,
        ROWS_CHECK,
    ),
    # A refused listing's sentence.
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        "showHistoryError(h, failure.message);",
        'showHistoryError(h, "");',
        REFUSED_LIST_CHECK,
        ROWS_CHECK,
    ),
    # History is reached from Done.
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        '  h.link.addEventListener("click", openHistory);\n',
        "",
        OPEN_CHECK,
        LABEL_CHECK,
    ),
    # Back is Done, and Keep checking names keeps the state Done gave it.
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        'function backToDone() {\n  showPart("finish");',
        'function backToDone() {\n  showPart("ready");',
        BACK_CHECK,
        OPEN_CHECK,
    ),
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        '  showPart("history");\n  /* History\'s own `Back` is the way out;',
        "  /* History's own `Back` is the way out;",
        OPEN_CHECK,
        LABEL_CHECK,
    ),
    # The row's undo: once (R7-F3: Done's in-flight flag, planted below) and never on a stopped
    # page - Task 7 review (M3401, R7-F4). History's own `undoing` flag was removed with R7-F3's
    # fix: Done's flag made it an equivalent mutant.
    (
        HISTORY_JS,
        "PAM_HISTORY_JS",
        "  if (PAGE.stopped) {\n    return Promise.resolve(null);\n  }\n  button.disabled = true;",
        "  button.disabled = true;",
        STOPPED_CHECK,
        ROWS_CHECK,
    ),
    # ... for THAT row's session, through Done's own undo.
    (
        DONE_JS,
        "PAM_DONE_JS",
        "  DONE.session = session;\n  DONE.undoing = false;\n",
        "  DONE.undoing = false;\n",
        UNDO_CHECK,
        ROWS_CHECK,
    ),
    # ... whose refusal does not sit under the old Done's headline.
    (
        DONE_JS,
        "PAM_DONE_JS",
        '  resetFinish(f);\n  showPart("finish");\n',
        '  showPart("finish");\n',
        REFUSED_UNDO_CHECK,
        UNDO_CHECK,
    ),
    # The fourth part goes away with every other part.
    (
        CONFIRM_JS,
        "PAM_CONFIRM_JS",
        '  c.history.hidden = name !== "history";\n',
        "",
        OPEN_CHECK,
        LABEL_CHECK,
    ),
    # B4: only under a failure headline, and for that session.
    (
        DONE_JS,
        "PAM_DONE_JS",
        "drawReport(f, result.failed > 0 ? job.session : null);",
        "drawReport(f, job.session);",
        B4_CHECK,
        OPEN_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "drawReport(f, result.failed > 0 ? job.session : null);",
        "drawReport(f, null);",
        B4_CHECK,
        OPEN_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        '  return "api/session/" + encodeURIComponent(session) + "/report";',
        '  return "api/session/" + encodeURIComponent(session);',
        B4_CHECK,
        OPEN_CHECK,
    ),
    # R7-F3: `undoSession` waits out an undo in flight, and the flag is cleared when it settles.
    (
        DONE_JS,
        "PAM_DONE_JS",
        "  if (DONE.pressing) {\n    return Promise.resolve(null);\n  }\n",
        "",
        IN_FLIGHT_CHECK,
        ROWS_CHECK,
    ),
    (
        DONE_JS,
        "PAM_DONE_JS",
        "    DONE.pressing = false;\n    return value;\n",
        "    return value;\n",
        IN_FLIGHT_CHECK,
        UNDO_CHECK,
    ),
)
assert len(PLANTS) >= 33, "the negative controls went quiet"


# --- the stdlib half: never skips --------------------------------------------------------


def test_the_node_driver_ships_and_names_every_required_check():
    assert NODE_DRIVER.is_file()
    text = NODE_DRIVER.read_text(encoding="utf-8")
    assert len(text) > 8000
    for check in REQUIRED_CHECKS:
        assert f'test("{check}"' in text, check


@pytest.mark.parametrize("sentence", HISTORY_SENTENCES)
def test_history_carries_w22s_copy_verbatim(sentence):
    assert f'"{sentence}"' in HISTORY_JS.read_text(encoding="utf-8"), sentence


@pytest.mark.parametrize("sentence", DONE_SENTENCES)
def test_done_carries_b4s_link_verbatim(sentence):
    assert f'"{sentence}"' in DONE_JS.read_text(encoding="utf-8"), sentence


@pytest.mark.parametrize("part", HISTORY_IDS)
def test_the_page_carries_historys_parts(part):
    assert INDEX_HTML.read_text(encoding="utf-8").count(part) == 1, part


def test_history_is_reached_from_done_the_list_and_welcome():
    """T7-R1 widened by AM3 / E7 (M15 Task 3): Done's `History` button, inside `#finish`, plus
    one on the list header (`#header-row`) and one on Welcome (`#welcome`)."""
    html = INDEX_HTML.read_text(encoding="utf-8")
    finish = html.index('<div id="finish"')
    history = html.index('<div id="history"')
    assert finish < html.index('id="finish-history"') < history
    assert (
        HISTORY_JS.read_text(encoding="utf-8").count('addEventListener("click", openHistory)')
        == 1
    )
    text = HISTORY_JS.read_text(encoding="utf-8")
    assert text.count('addEventListener("click", openHistoryFromList)') == 1
    assert text.count('addEventListener("click", openHistoryFromWelcome)') == 1
    header = html.index('<header id="header"')
    assert header < html.index('id="list-history"') < html.index('<section id="welcome"')
    assert html.index('<section id="welcome"') < html.index('id="welcome-history"')
    assert html.index('id="welcome-history"') < html.index('<section id="confirm"')


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
