"""`tests/js/welcome_test.js` executed, and the floor that keeps it honest (M14 Task 4 Step 7, 5e).

Welcome (U8's W18) is the page `pam start` opens when its server holds no folder, or a folder with
no open plan: U13's chooser, the bind (U10), recovery's line under U12's heading, and - since M14
Task 5e - the Reading step (U11: Start reading, the poll, the progress line, Stop, the job's lines;
`pam-reading.js`) and U12's banner. It lives in `pam-welcome.js` and `pam-reading.js`, and `app.js`
decides on it from the ping. The node driver loads the page's twelve scripts into one context, in
the page's order, so `app.js` boots as it does in a browser; this module runs it and reads the names
it prints - every check prints `ok <name>` and the run ends with `ran <n>` - so a known set must
actually EXECUTE.

THE SHAPE IS `tests/test_webui_list_page.py`'s. `node` is an external tool on the exiftool
precedent, so the node half skips where it is absent - and the stdlib half NEVER skips, so a
deleted driver or a deleted sentence is red on either machine (CLAUDE.md, M783/M784).

THE COPY IS SPELLED HERE from W18, U10, U11, U12 and the controller's ETA rule - never read back
out of the scripts. The lists are asserted non-empty AT COLLECTION (M743: pytest SKIPS an empty
parametrize).

THE NEGATIVE CONTROLS ARE RUN: each plants one defect in a COPY of a script through
`PAM_CORE_JS` / `PAM_READING_JS` / `PAM_WELCOME_JS` / `PAM_APP_JS`, and the driver must go red on
the check that exists for it while another stays green. Nothing on disk is touched.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
NODE_DRIVER = TESTS_DIR / "js" / "welcome_test.js"
STATIC_DIR = TESTS_DIR.parent / "pam" / "webui" / "static"
WELCOME_JS = STATIC_DIR / "pam-welcome.js"
READING_JS = STATIC_DIR / "pam-reading.js"
CORE_JS = STATIC_DIR / "pam-core.js"
APP_JS = STATIC_DIR / "app.js"

# THE CHECKS THE DRIVER MUST EXECUTE, spelled from Step 7 and the Task 4d dispatch's list.
# M14 Task 6 Stage 2, S2-F2: the ping's recovery line, shown on Welcome at boot.
STARTUP_RECOVERY_CHECK = (
    "a start that ran recovery shows U12's heading and recovery's own line on the ping's folder"
)
STARTUP_RECOVERY_PLAN_CHECK = "a start that ran recovery with an open plan waits on Welcome, then Review names opens the list"

REQUIRED_CHECKS = (
    "Welcome hides the step pills a drawn list had shown (HT-M14 F7)",
    # unbound shows Welcome (W18's copy); since Task 5e the job is asked once, at load
    "an unbound server's page shows Welcome with W18's copy, never asks for a plan, and asks for "
    "the job once",
    # bound shows the list, exactly as before Task 4
    "a bound server's page opens the list and never Welcome",
    # U13: the known archives and the drives
    "the known archives and the drives are the server's, and an unreachable archive is not offered",
    "a known archive binds its own path",
    # U13: the browser - drives, then folders, clicking into a folder
    "a drive lists its folders, a folder its own, Up goes back, and Choose this folder binds the listing",
    # U10: a bind success
    "a bind to a folder with a plan opens the list",
    # U12: recovery's own line after a bind
    "a bind that ran recovery shows U12's heading and recovery's own line, then Review names opens the list",
    # U10: chosen, not bound (Task 5e: Start reading is ACTIVE)
    "a never-read folder is chosen: the server's path, Start reading offered, Review names off",
    "a read folder with no open plan offers Start reading and opens no list",
    # each refusal verbatim (U13, U10, Task 4b's two)
    "each bind refusal shows the server's sentence verbatim, and no folder",
    "a refused bind after a choice takes the shown folder away",
    "a listing refusal shows the server's sentence verbatim",
    # U13: the paste box
    "the paste box posts the path as typed, once per choice",
    # Step 7: Change
    "Change goes back to the chooser and asks for the folders again",
    # OD-4
    "OD-4's sentence is drawn nowhere, through every state",
    # Task 4 review R1-F1: every request names the binding the page was given
    "every request names the binding the page last learned: the ping's, then the bind's",
    # Task 4 review R2 (appended; since Task 5a the controls name their checks by string): the
    # busy guard, a stale refusal, a refused plan after a bind, and `withBinding`'s copy
    # (M3029-M3051)
    "a double submit of the paste box posts one bind",
    "a refusal leaves the screen when the next listing or choice succeeds",
    "a bind whose plan is then refused shows the server's sentence on the list",
    "the binding header is added to a copy: the caller's own headers are kept and left untouched",
    # M14 Task 5a (R2-F1, APPENDED): a listing that answers after a newer one is dropped
    "a folder listing that answers after a newer one is dropped: the folder asked for last stays",
    # M14 Task 5e (APPENDED): the Reading step - U10, U11, U12, U21 and the controller's ETA rule
    "Start reading posts once on a double click, adopts the answer's binding and folder, and polls "
    "about once a second",
    "the progress line at a running snapshot, with and without an ETA: 748 of 1,204 · about a "
    "minute left, 19 of 100",
    "every ETA boundary: 59, 60, 89, 90, 3569, 3570, 5399 and 5400 seconds",
    "Stop posts cancel once, and the cancelled read shows the job's line and offers Start reading "
    "again",
    "a read that ends with an open plan enables Review names, which opens the list",
    "a read that ends with no plan keeps Start reading and shows the job's lines, U21's verbatim",
    "a failed read with recovery shows U12's heading and recovery's own line, and offers Start "
    "reading again",
    "a failed read with no recovery shows the job's lines and offers Start reading again",
    "a failed poll shows U12's banner, all three sentences, and stops polling",
    "Start reading is not offered while a plan is open: after a bind that ran recovery, and after "
    "a read whose ping names a plan",
    "a bound folder with no open plan shows the ping's folder and offers Start reading",
    # M14 Task 6 Stage 2, S2-F2 (the controller's fix): recovery's line at `pam start PATH`
    STARTUP_RECOVERY_CHECK,
    STARTUP_RECOVERY_PLAN_CHECK,
    "a reload during a running read resumes the progress view instead of Start reading",
    # M14 Task 5 review R2-F1/F5 (the controller's page fix): split by what the server holds
    "a refused Start reading on a bound folder shows the server's sentence and offers the button "
    "again",
    # M14 Task 5 review R2 (APPENDED): the gaps its mutation pass found (M3122-M3153)
    "an estimate of 0 seconds still reads less than a minute left",
    "a read after a read starts clean: the last one's lines and U12's heading go, and each press "
    "posts once",
    "a poll the server refuses shows its sentence and asks no more",
    "once pam has stopped the page asks nothing more: a poll already waiting, and Start reading",
    "a reload after a read has ended offers Start reading and draws none of its lines",
    "Change after a finished read takes the read's lines away with the folder",
    # M14 Task 5 review R2-F1/F5/F6 (APPENDED): the controller's page fix
    "a refused Start reading on a chosen folder shows the server's sentence and the chooser, no "
    "folder",
    "Change is not offered while Start reading posts, and comes back when the server keeps the "
    "folder",
    "a reload's late answer about a running read starts no second poll once Start reading was "
    "pressed",
    # M14 Task 5: the controller's page-fix mutation pass (M3185, M3187, M3189; APPENDED)
    "a Start reading that meets a dead server asks it nothing more",
    "a read refused because another folder was chosen draws that folder and its binding",
    "a read refused while a plan has opened offers Review names, not Start reading",
)

# W18's copy, verbatim (U8's table), plus U10's and U12's words Welcome draws.
WELCOME_SENTENCES = (
    "Give your photos names that make sense.",
    "Folder",
    "Reading your photos",
    "Review the names",
    "Your photos stay on this computer. Nothing is uploaded.",
    "Only the file names change. Photos are never edited, moved or deleted.",
    "Every rename can be undone, any time.",
    "Review names",
    # U10
    "Start reading",
    # U12
    "pam finished putting things right after it stopped.",
    # M14 Task 4 Step 7
    "Change",
    # M14 Task 5e (U11's cancellation; the controller's word)
    "Stop",
)
# W18's lead, which `pam-welcome.js` spells over two string literals (its line budget).
W18_LEAD = (
    "pam suggests a name for every photo from its date, place and event. "
    "You approve the names. Nothing changes until you say so."
)

# OD-4 (ADOPTED 2026-10-29): W18's last mockup line, dropped in M14.
OD4_SENTENCE = "You can start reviewing while pam keeps reading."
# Task 4d's placeholder for the inactive Reading step, deleted by Task 5e (reading is active).
NOT_READY_SENTENCE = "Reading from this page is not ready yet."

# The ETA phrase's fixed words - the controller's wording (Task 5e dispatch), pending the design
# model - which `pam-reading.js` carries; the counted forms are built around N.
ETA_PHRASES = ("less than a minute left", "about a minute left", "about an hour left")
# U12, verbatim (UX 5's fourth state, rev 16), which `pam-core.js` carries for the banner.
U12_SENTENCES = (
    "pam has stopped.",
    "Nothing is lost: every rename is written down before it happens.",
    "Start pam again and it will put things right before it does anything else.",
)

# AT COLLECTION: an emptied or cut list fails before any test runs (M743).
assert len(REQUIRED_CHECKS) >= 48, "the required check list is empty or cut"
assert len(WELCOME_SENTENCES) >= 12, "the Welcome sentence list is empty or cut"
assert len(ETA_PHRASES) == 3, "the ETA phrase list is empty or cut"
assert len(U12_SENTENCES) == 3, "the U12 sentence list is empty or cut"

# THE CHECKS THE NEGATIVE CONTROLS NAME, each by its literal string (M14 Task 5a, R2-F3). They
# used to be picked out of `REQUIRED_CHECKS` by position, so a check inserted anywhere above
# them shifted every control onto another check - and an `ok` leg that landed on another
# passing check lost its meaning silently. `test_every_check_a_negative_control_names_is
# _required` keeps each one a member of `REQUIRED_CHECKS`.
UNBOUND_CHECK = (
    "an unbound server's page shows Welcome with W18's copy, never asks for a plan, and asks for "
    "the job once"
)
BOUND_CHECK = "a bound server's page opens the list and never Welcome"
PLAN_OPENS_CHECK = "a bind to a folder with a plan opens the list"
RECOVERY_CHECK = (
    "a bind that ran recovery shows U12's heading and recovery's own line, then Review names "
    "opens the list"
)
CHOSEN_CHECK = (
    "a never-read folder is chosen: the server's path, Start reading offered, Review names off"
)
REFUSALS_CHECK = "each bind refusal shows the server's sentence verbatim, and no folder"
REFUSED_AFTER_CHOICE_CHECK = "a refused bind after a choice takes the shown folder away"
LISTING_REFUSAL_CHECK = "a listing refusal shows the server's sentence verbatim"
# M14 Task 5e's.
START_CHECK = (
    "Start reading posts once on a double click, adopts the answer's binding and folder, and polls "
    "about once a second"
)
PROGRESS_CHECK = (
    "the progress line at a running snapshot, with and without an ETA: 748 of 1,204 · about a "
    "minute left, 19 of 100"
)
ETA_CHECK = "every ETA boundary: 59, 60, 89, 90, 3569, 3570, 5399 and 5400 seconds"
STOP_CHECK = (
    "Stop posts cancel once, and the cancelled read shows the job's line and offers Start reading "
    "again"
)
NO_PLAN_LINES_CHECK = "a read that ends with no plan keeps Start reading and shows the job's lines, U21's verbatim"
RECOVERY_READ_CHECK = (
    "a failed read with recovery shows U12's heading and recovery's own line, and offers Start "
    "reading again"
)
BANNER_CHECK = "a failed poll shows U12's banner, all three sentences, and stops polling"
PLAN_OPEN_CHECK = (
    "Start reading is not offered while a plan is open: after a bind that ran recovery, and after "
    "a read whose ping names a plan"
)
BOUND_NO_PLAN_CHECK = (
    "a bound folder with no open plan shows the ping's folder and offers Start reading"
)
RESUME_CHECK = (
    "a reload during a running read resumes the progress view instead of Start reading"
)
# M14 Task 5 (M3190): the R2-F6 check, which a harness that cannot hold an answer passes vacuously.
LATE_RESUME_CHECK = (
    "a reload's late answer about a running read starts no second poll once Start reading was "
    "pressed"
)
CONTROL_CHECKS = (
    UNBOUND_CHECK,
    BOUND_CHECK,
    PLAN_OPENS_CHECK,
    RECOVERY_CHECK,
    CHOSEN_CHECK,
    REFUSALS_CHECK,
    REFUSED_AFTER_CHOICE_CHECK,
    LISTING_REFUSAL_CHECK,
    START_CHECK,
    PROGRESS_CHECK,
    ETA_CHECK,
    STOP_CHECK,
    NO_PLAN_LINES_CHECK,
    RECOVERY_READ_CHECK,
    BANNER_CHECK,
    PLAN_OPEN_CHECK,
    BOUND_NO_PLAN_CHECK,
    RESUME_CHECK,
    LATE_RESUME_CHECK,
    STARTUP_RECOVERY_CHECK,
    STARTUP_RECOVERY_PLAN_CHECK,
)
assert len(CONTROL_CHECKS) >= 21, "the negative controls' check list is empty or cut"

# The lines the negative controls plant against, each with its defect.
UNBOUND_DECISION = "if (ping.plan_id === null) {"
NO_UNBOUND_DECISION = "if (false) {"
RECOVERY_WAITS = "return body.recovery ? null : openTheList();"
RECOVERY_SKIPPED = "return openTheList();"
REFUSAL_VERBATIM = "showError(failure.message);"
REFUSAL_REWORDED = 'showError("pam could not use that folder.");'
# S2-F2's plants: `pam-welcome.js` hides the ping's recovery line; `app.js` opens the list over it.
PING_RECOVERY_SHOWN = "w.recovery.hidden = !ping.recovery;"
PING_RECOVERY_HIDDEN = "w.recovery.hidden = true;"
RECOVERY_BOOT_DECISION = "if (ping.folder && ping.recovery) {"
NO_RECOVERY_BOOT_DECISION = "if (false) {"
# M14 Task 5e's plants. `pam-reading.js`: the plan-open guard, the job's lines, the double-click
# guard; `pam-welcome.js`: the resume at load; `pam-core.js`: U12's middle sentence.
PLAN_OPEN_GUARD = "if (WELCOME.planId !== null) {"
PLAN_OPEN_IGNORED = "if (false) {"
JOB_LINES = "drawLines(w, healed ? [] : job.lines);"
JOB_LINES_REWORDED = 'drawLines(w, healed ? [] : ["Stopped."]);'
DOUBLE_CLICK_GUARD = "if (READING.active || PAGE.stopped) {"
DOUBLE_CLICK_UNGUARDED = "if (PAGE.stopped) {"
RESUMES = "return Promise.all([showTop(), resumeReading()]);"
NO_RESUME = "return showTop();"
U12_LOST = 'lost: "Nothing is lost: every rename is written down before it happens.",'
U12_LOST_REWORDED = 'lost: "Nothing is lost.",'
# M14 Task 5 review R2 (M3180): the poll scheduled twice per answer - two chains asking at once.
# The harness's `tick` holds the page to ONE waiting poll; this plant is that assertion's control.
ONE_POLL = "    READING.schedule(poll);\n"
# M14 Task 5 (R2-F6, M3190): `resumeReading` yields to a press that already owns the poll.
RESUME_YIELDS = "    if (READING.active) {\n"
RESUME_NEVER_YIELDS = "    if (false) {\n"
TWO_POLLS = "    READING.schedule(poll);\n    READING.schedule(poll);\n"
# Each ETA boundary moved by one comparison - written here, independently of the boundary table
# the node check spells, and asserted non-empty at collection (M743).
ETA_PLANTS = (
    ("if (seconds < 60) {", "if (seconds <= 60) {"),
    ("if (seconds < 90) {", "if (seconds <= 90) {"),
    ("if (seconds < 3570) {", "if (seconds <= 3570) {"),
    ("if (seconds < 5400) {", "if (seconds <= 5400) {"),
)
assert len(ETA_PLANTS) == 4, "the ETA plants went quiet"


# --- the stdlib half: never skips --------------------------------------------------------


def test_the_node_driver_ships_and_names_every_required_check():
    assert NODE_DRIVER.is_file()
    text = NODE_DRIVER.read_text(encoding="utf-8")
    assert len(text) > 8000
    for check in REQUIRED_CHECKS:
        assert f'test("{check}"' in text, check


@pytest.mark.parametrize("sentence", WELCOME_SENTENCES)
def test_welcome_carries_w18s_copy_verbatim(sentence):
    assert f'"{sentence}"' in WELCOME_JS.read_text(encoding="utf-8"), sentence


def test_welcome_carries_w18s_lead_verbatim():
    text = WELCOME_JS.read_text(encoding="utf-8")
    start = text.index('"pam suggests')
    end = text.index("stepFolder", start)
    literal = "".join(part for part in text[start:end].split('"')[1::2])
    assert literal == W18_LEAD


def test_od4s_sentence_is_in_no_static_file():
    """OD-4: the sentence is not drawn - nor carried by any file the page is built from. Task 5e:
    nor is Task 4d's placeholder for the inactive Reading step."""
    shipped = sorted(STATIC_DIR.iterdir())
    assert len(shipped) >= 19, "the static directory went quiet"
    for path in shipped:
        text = path.read_text(encoding="utf-8")
        assert OD4_SENTENCE not in text, path.name
        assert NOT_READY_SENTENCE not in text, path.name


@pytest.mark.parametrize("phrase", ETA_PHRASES)
def test_the_reading_step_carries_the_eta_phrases_verbatim(phrase):
    assert f'"{phrase}"' in READING_JS.read_text(encoding="utf-8"), phrase


@pytest.mark.parametrize("sentence", U12_SENTENCES)
def test_the_core_carries_u12s_banner_verbatim(sentence):
    assert f'"{sentence}"' in CORE_JS.read_text(encoding="utf-8"), sentence


def test_every_check_a_negative_control_names_is_required():
    """R2-F3: a control naming a check the driver no longer runs would assert `FAIL` or `ok` of a
    name that is never printed - so each named check must be one the driver is held to run."""
    for check in CONTROL_CHECKS:
        assert check in REQUIRED_CHECKS, check


def test_the_lines_the_negative_controls_plant_against_are_there_once():
    assert APP_JS.read_text(encoding="utf-8").count(UNBOUND_DECISION) == 1
    assert APP_JS.read_text(encoding="utf-8").count(RECOVERY_BOOT_DECISION) == 1
    welcome = WELCOME_JS.read_text(encoding="utf-8")
    for line in (RECOVERY_WAITS, REFUSAL_VERBATIM, RESUMES, PING_RECOVERY_SHOWN):
        assert welcome.count(line) == 1, line
    reading = READING_JS.read_text(encoding="utf-8")
    for line in (
        PLAN_OPEN_GUARD,
        JOB_LINES,
        DOUBLE_CLICK_GUARD,
        ONE_POLL,
        RESUME_YIELDS,
        *(old for old, _ in ETA_PLANTS),
    ):
        assert reading.count(line) == 1, line
    assert CORE_JS.read_text(encoding="utf-8").count(U12_LOST) == 1


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
def test_the_node_driver_fails_when_an_unbound_page_is_not_sent_to_welcome(tmp_path):
    """THE NEGATIVE CONTROL, RUN: `app.js`'s decision removed - the unbound check goes red,
    the bound one stays green."""
    run = _planted(tmp_path, APP_JS, UNBOUND_DECISION, NO_UNBOUND_DECISION, "PAM_APP_JS")
    assert run.returncode == 1, run.stdout + run.stderr
    assert f"FAIL {UNBOUND_CHECK}" in run.stdout
    assert f"ok {BOUND_CHECK}" in run.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_the_list_hides_recoverys_line(tmp_path):
    """THE NEGATIVE CONTROL, RUN (U12): a bind that ran recovery opens the list at once."""
    run = _planted(tmp_path, WELCOME_JS, RECOVERY_WAITS, RECOVERY_SKIPPED, "PAM_WELCOME_JS")
    assert run.returncode == 1, run.stdout + run.stderr
    assert f"FAIL {RECOVERY_CHECK}" in run.stdout
    assert f"ok {PLAN_OPENS_CHECK}" in run.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_the_pings_recovery_line_is_hidden(tmp_path):
    """THE NEGATIVE CONTROL, RUN (S2-F2): Welcome at boot hides recovery's line from the ping."""
    run = _planted(
        tmp_path, WELCOME_JS, PING_RECOVERY_SHOWN, PING_RECOVERY_HIDDEN, "PAM_WELCOME_JS"
    )
    assert run.returncode == 1, run.stdout + run.stderr
    assert f"FAIL {STARTUP_RECOVERY_CHECK}" in run.stdout
    assert f"ok {BOUND_NO_PLAN_CHECK}" in run.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_the_list_opens_over_a_starts_recovery(tmp_path):
    """THE NEGATIVE CONTROL, RUN (S2-F2): a bound plan's boot opens the list over recovery's line."""
    run = _planted(
        tmp_path, APP_JS, RECOVERY_BOOT_DECISION, NO_RECOVERY_BOOT_DECISION, "PAM_APP_JS"
    )
    assert run.returncode == 1, run.stdout + run.stderr
    assert f"FAIL {STARTUP_RECOVERY_PLAN_CHECK}" in run.stdout
    assert f"ok {STARTUP_RECOVERY_CHECK}" in run.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_a_refusal_is_reworded(tmp_path):
    """THE NEGATIVE CONTROL, RUN: the page builds its own sentence in place of the server's."""
    run = _planted(tmp_path, WELCOME_JS, REFUSAL_VERBATIM, REFUSAL_REWORDED, "PAM_WELCOME_JS")
    assert run.returncode == 1, run.stdout + run.stderr
    for check in (REFUSALS_CHECK, REFUSED_AFTER_CHOICE_CHECK, LISTING_REFUSAL_CHECK):
        assert f"FAIL {check}" in run.stdout, check
    assert f"ok {CHOSEN_CHECK}" in run.stdout


# --- M14 Task 5e's negative controls, run ------------------------------------------------


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
@pytest.mark.parametrize(("old", "new"), ETA_PLANTS)
def test_the_node_driver_fails_when_an_eta_boundary_moves(tmp_path, old, new):
    """THE NEGATIVE CONTROL, RUN: each boundary of the controller's ETA rule moved by one
    comparison - the boundary check goes red, the progress line's stays green."""
    run = _planted(tmp_path, READING_JS, old, new, "PAM_READING_JS")
    assert run.returncode == 1, run.stdout + run.stderr
    assert f"FAIL {ETA_CHECK}" in run.stdout
    assert f"ok {PROGRESS_CHECK}" in run.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_start_reading_is_offered_over_an_open_plan(tmp_path):
    """THE NEGATIVE CONTROL, RUN: the plan-open guard ignored - a read would supersede the plan."""
    run = _planted(tmp_path, READING_JS, PLAN_OPEN_GUARD, PLAN_OPEN_IGNORED, "PAM_READING_JS")
    assert run.returncode == 1, run.stdout + run.stderr
    assert f"FAIL {PLAN_OPEN_CHECK}" in run.stdout
    assert f"ok {BOUND_NO_PLAN_CHECK}" in run.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_the_jobs_stopped_line_is_reworded(tmp_path):
    """THE NEGATIVE CONTROL, RUN: the page says its own line in place of the job's."""
    run = _planted(tmp_path, READING_JS, JOB_LINES, JOB_LINES_REWORDED, "PAM_READING_JS")
    assert run.returncode == 1, run.stdout + run.stderr
    assert f"FAIL {STOP_CHECK}" in run.stdout
    assert f"FAIL {NO_PLAN_LINES_CHECK}" in run.stdout
    assert f"ok {RECOVERY_READ_CHECK}" in run.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_the_stopped_banner_is_reworded(tmp_path):
    """THE NEGATIVE CONTROL, RUN: U12's middle sentence cut - the banner check goes red."""
    run = _planted(tmp_path, CORE_JS, U12_LOST, U12_LOST_REWORDED, "PAM_CORE_JS")
    assert run.returncode == 1, run.stdout + run.stderr
    assert f"FAIL {BANNER_CHECK}" in run.stdout
    assert f"ok {STOP_CHECK}" in run.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_a_double_click_posts_twice(tmp_path):
    """THE NEGATIVE CONTROL, RUN (Task 3's F4): the in-flight guard dropped."""
    run = _planted(
        tmp_path, READING_JS, DOUBLE_CLICK_GUARD, DOUBLE_CLICK_UNGUARDED, "PAM_READING_JS"
    )
    assert run.returncode == 1, run.stdout + run.stderr
    assert f"FAIL {START_CHECK}" in run.stdout
    assert f"ok {PROGRESS_CHECK}" in run.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_a_reload_does_not_resume_a_running_read(tmp_path):
    """THE NEGATIVE CONTROL, RUN: Welcome never asks for the job at load."""
    run = _planted(tmp_path, WELCOME_JS, RESUMES, NO_RESUME, "PAM_WELCOME_JS")
    assert run.returncode == 1, run.stdout + run.stderr
    assert f"FAIL {RESUME_CHECK}" in run.stdout
    assert f"ok {BOUND_NO_PLAN_CHECK}" in run.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_the_poll_is_scheduled_twice(tmp_path):
    """THE NEGATIVE CONTROL, RUN (Task 5 review R2, M3180): every running answer schedules two
    polls - the harness's one-waiting-poll assertion goes red; the pure ETA check stays green."""
    run = _planted(tmp_path, READING_JS, ONE_POLL, TWO_POLLS, "PAM_READING_JS")
    assert run.returncode == 1, run.stdout + run.stderr
    assert f"FAIL {PROGRESS_CHECK}" in run.stdout
    assert f"ok {ETA_CHECK}" in run.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_a_late_reload_answer_starts_a_second_poll(tmp_path):
    """THE NEGATIVE CONTROL, RUN (M14 Task 5, M3190): `resumeReading` no longer yields to a press
    that owns the poll - the R2-F6 check goes red, so its harness really holds the late answer;
    the pure ETA check stays green."""
    run = _planted(tmp_path, READING_JS, RESUME_YIELDS, RESUME_NEVER_YIELDS, "PAM_READING_JS")
    assert run.returncode == 1, run.stdout + run.stderr
    assert f"FAIL {LATE_RESUME_CHECK}" in run.stdout
    assert f"ok {ETA_CHECK}" in run.stdout
