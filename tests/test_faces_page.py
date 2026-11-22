"""THE LABELING PAGE'S STATE TRANSITIONS, EXECUTED (M11 Task 10, Step 8 - Blocker 8.4).

WHAT THIS CLOSES. `tests/test_static_syntax.py` runs `node --check` over every shipped
script, which is the M783 defect - one unclosed brace left 1862 tests green over a page a
browser refuses to parse. But **syntax validity cannot prove that a transition works**,
and that sentence is the plan's Blocker 8.4 in full. `pam/webui/static/faces.js` is the
first script in this project whose consequential behaviour is written as PURE FUNCTIONS
over a plain state object, with the transport injected, precisely so that a real `node`
can execute it. `tests/js/faces_state_test.js` is that execution; this module drives it.

THE INSTRUMENT IS THE RECORDING TRANSPORT, AND THAT IS CLAUDE.md's M697. A test that
asserts the OUTCOME cannot tell "never did the dangerous thing" from "did it and left no
trace". The dangerous thing on this page is **writing a name nobody typed** (vision R6),
and an empty name field is perfectly consistent with a POST that already happened. So the
driver watches the CALL: loading a group must have issued exactly two GETs and no write at
all, and a blank name must not reach the socket. Neither claim is observable from state.

TWO HALVES, AND ONLY ONE OF THEM CAN SKIP - `test_static_syntax.py`'s split, for its
reason. A guard whose whole value depends on an external tool disarms silently on the
machine that has not got it. So the Node run skips where `node` is absent, and the checks
that pin the SEAM between the driver and the script - that the driver exists, that it is
required to exercise a named set of transitions, and that `faces.js` still exports every
function it reaches for - use nothing but the standard library and **always run**. A
`module.exports` block deleted from `faces.js` would make the Node half fail on a machine
with node and vanish on one without; the stdlib half fails on both.

WHY THE DRIVER IS WATCHED FAILING. "The Node tests pass" is a claim about an instrument
nobody has seen fire. `test_the_node_driver_fails_when_the_page_proposes_a_name` plants
vision R6's exact defect in a COPY of `faces.js` in a temporary directory, points the
driver at it through `PAM_FACES_JS`, and requires the run to fail on the named test.
Nothing on disk is touched.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
NODE_DRIVER = TESTS_DIR / "js" / "faces_state_test.js"
FACES_JS = TESTS_DIR.parent / "pam" / "webui" / "static" / "faces.js"

# THE TRANSITIONS THE DRIVER MUST ACTUALLY EXECUTE, spelled from UX spec 6, design spec
# 8.2 and vision R6 rather than read back out of the driver - an oracle built from the
# definition it checks is a survivor waiting to happen. The driver prints `ok <name>` per
# test, so this is a check that a known set RAN, not that a process exited 0: a file whose
# tests had all been deleted would exit 0 too.
REQUIRED_TRANSITIONS = (
    # M14 review R1 - under `pam start` a write with no binding is refused (R1-F1)
    "every request names the binding the page was loaded on, writes included",
    "the page pings once, at load, and never adopts a later binding",
    "a ping that fails sends no header rather than a made-up one",
    "the page's own transport is the browser's fetch through the binding",
    # vision R6 - the two rules the whole page exists to keep
    "loading a group issues exactly two GETs and no write at all",
    "the name field is empty even though pam knows people to autocomplete",
    "a blank name never reaches the socket",
    # spec 6.2 rule 7 - pam says what it changed, in faces AND photographs
    "the label note names BOTH counts, which is what rule 7 asks for",
    "the scope sentence discloses the same two numbers BEFORE the click",
    # rule 7 is what pam CHANGED, not what the page set out to change (reviewer's F4)
    "the note carries the numbers the SERVER wrote, not the queue's advertisement",
    # the one write
    "saving posts exactly one label, carrying the name the user typed",
    "a saved group leaves the queue and both archive counts move",
    "a refusal changes nothing but the sentence - the group stays on offer",
    "a transport failure makes the page inert and is not a refusal",
    # UX spec 6's queue, and Step 6's Skip-versus-Ignore requirement
    "the queue keeps the server's order and the page never re-sorts it",
    "skip issues no request, moves on, and is reversible in the same visit",
    "skipping every group is NOT the done state",
    "an archive nobody has clustered is the empty state, not the done state",
    # the empty branch's `remaining` operand, which the line above cannot reach (reviewer's F5)
    "an archive with unnamed faces it cannot offer is NOT the fresh-archive state",
    "an archive whose every group is named IS the done state",
    # UX spec 6's progress line
    "the progress line names the two counts UX 6 draws",
    "the estimate is seeded, then MEASURED once a group has been handled",
    "the estimate claims no precision it has not got",
    # spec 8.2's crops
    "expanding swaps the server's sample for every face in the group",
    "a crop path is RELATIVE and carries the face id",
    # the independent second path: the pure functions, driven without the controller
    "the pure transitions compose to exactly the state the controller reaches",
    # Task 11 Step 3 - Ignore, and the Ignored faces view that makes it reversible (I3)
    "ignore posts dismiss exactly once and the group leaves the queue with the server's counts",
    "the ignored list renders both kinds with their counts",
    "an empty ignored list says so rather than drawing nothing",
    "restore posts a cleared state for exactly that face and the list follows the server",
    "leaving the Ignored faces view re-reads the queue, because a restored face rejoins it",
    # Task 11 Step 4 - split: a preview, never a confirm on the click; confirm; undo; cancel
    "not the same person issues a preview and never a confirm",
    "a preview that will not separate offers nothing to confirm and issues nothing further",
    "confirm posts the preview's own token to the same group",
    "undo after a split posts the confirm's undo token to the same group",
    "cancelling a preview issues no request",
    # Task 11 Step 5 - one undo of exactly the latest label, never a stack
    "undo after a label posts exactly that label's faces with no person",
    "after a second label only the latest label is undoable",
    "any other action withdraws the undo offer",
    "a response without face ids offers no undo rather than guessing",
    # every Task 11 write refused changes nothing but the sentence
    "a refusal to ignore, split or undo changes nothing but the sentence",
    # the independent second path for Task 11's transitions
    "the correction transitions compose without the controller",
    # Task 11 Step 2 - Manage people: reading is GETs only; the fields start empty (R6)
    "manage people list and expand issue only GETs",
    "the rename, filename and move fields start empty for every person",
    # spec 8.2 `person/{id}`: each write carries exactly its own field
    "rename posts exactly the typed label and nothing else",
    "setting the filename name posts the typed string and clearing it posts null, never an empty string",
    "hide posts hidden true and nothing else, and unhide posts false",
    # spec 8.2 `faces/assign` and `faces/state`, for the SELECTED faces only
    "unassign posts a null person for exactly the selected faces",
    "move posts the typed name for the selected faces",
    "not a face posts not_a_face, never dismissed",
    "a manage action with no faces selected issues no request",
    # Step 6 - merge is destructive and says so, before anything is sent
    "merge names both people and their counts and issues no request before confirm",
    "cancelling a merge issues no request",
    "confirming a merge posts the surviving id to the merged person's path",
    # a refusal changes nothing but the sentence
    "a refused rename shows the server's sentence and changes nothing else",
    "leaving Manage people re-reads the queue and writes nothing",
    # the independent second path for Step 2's transitions
    "the manage people transitions compose without the controller",
    # M12 Task 5 (C8, P2) - F5: the done state counts the groups the floor hides
    "the done state counts the groups the floor is hiding, in P2's words",
    "an archive whose every group is below the floor is done with the sentence, not empty",
    "the hidden-groups sentence is only ever the done state's",
    "the done sentence is read from the server's field on load",
    # O4: a hidden person's panel - the filename line ABSENT, not merely a sentence present
    "a hidden person's panel renders NO filename line and says so in P2's words",
    "an unhidden person's panel is unchanged: counts, then its filename line",
    # O8: a restore says where the faces went - named (both numbers), unnamed, mixed
    "a restored face going back to a named person says so, singular and plural",
    "a restored face with no person is offered again with its group",
    "a restore spanning more than one destination names no one",
    "restore's note is chosen from the server's person_labels",
    # O5: accessible names
    "a face checkbox is named by its position and photograph, never its id",
    # the counter, one transition per success path P2 lists, from the SERVER's response
    "the counter after a label is the server's",
    "the counter after unassigning faces is the server's",
    "the counter after moving faces is the server's",
    "the counter after a merge is the server's - a merge changes two people's counts",
    "the counter after an ignore is the server's",
    "the counter after a restore is the server's",
    "a write that reports no counts leaves the counter where it was - no arithmetic",
    # Task 5 review, F2 (M2065/M2066): each of withProgress's two guards, singly
    "a response carrying only one of the two counts leaves both where they were",
    "a manage-people write moves the counter from its response through the controller",
)

# The seam the driver reaches through. Named here so that deleting one from `faces.js`'s
# `module.exports` is red on EVERY machine, not only on one that has node.
REQUIRED_EXPORTS = (
    "bindingRequest",
    "pageRequest",
    "createFacesApp",
    "initialState",
    "withClusters",
    "withPersons",
    "withExpanded",
    "visibleClusters",
    "currentCluster",
    "skipCurrent",
    "restoreSkipped",
    "afterLabel",
    # Task 11 Steps 3-5
    "afterIgnore",
    "withSplitPreview",
    "cancelSplit",
    "afterSplitConfirm",
    "afterSplitUndo",
    "afterLabelUndo",
    "showIgnored",
    "withIgnored",
    "afterRestore",
    "closeIgnored",
    # Task 11 Step 2
    "showPeople",
    "closePeople",
    "withManagedPerson",
    "toggleFaceSelection",
    "afterPersonUpdate",
    "afterFacesCorrected",
    "proposeMerge",
    "cancelMerge",
    "afterMerge",
    "mergeSentence",
    "peopleView",
    "ignoreSentence",
    "splitPreviewSentence",
    "splitSentence",
    "afterRefusal",
    "afterServerGone",
    "labelSentence",
    "scopeSentence",
    "progressLine",
    "estimateSeconds",
    "estimateText",
    "positionLine",
    "view",
    "cropPath",
    "cropAlt",
    "FacesRefusal",
    "COPY",
    # M12 Task 5 (P2)
    "restoreSentence",
    "hiddenBelowFloorSentence",
    "faceCheckboxName",
    # M13 Task 2 (C5): the registry moved to EQUALITY, which surfaced the one constant the
    # driver reads (the estimate's seed) and no list named
    "SECONDS_PER_CLUSTER",
)

# Counted from the lists above's own reasons rather than as `len(...)`: M793's lesson,
# where deriving a floor from the tuple it guards made emptying the tuple green.
# Task 10: 22 transitions, 20 exports. Task 11 Steps 3-5 add sixteen transitions (five
# for Ignore and its view, five for split, four for the label undo, one refusal sweep,
# one composition) and thirteen exports (ten transitions, three sentences).
# Task 11 Step 2 adds fifteen transitions (two reads, three person writes, four face
# corrections, three merge, one refusal, one leave, one composition) and eleven exports
# (ten transitions and the merge sentence).
# M12 Task 5 (P2) adds nineteen transitions (four F5, two O4, four O8, one O5, eight for
# the counter) and three exports (the restore and hidden-groups sentences, the O5 name).
# Its review adds one (F2: a half response moves neither count).
MINIMUM_TRANSITIONS = 73
# M13 Task 2 (C5) adds one export: SECONDS_PER_CLUSTER, which the driver already read.
MINIMUM_EXPORTS = 48

_OK_LINE = re.compile(r"^ok (.+)$", re.MULTILINE)
_RAN_LINE = re.compile(r"^ran (\d+)$", re.MULTILINE)


def _run_driver(faces_js: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run the Node driver, optionally against another copy of `faces.js`."""
    environment = dict(os.environ)
    if faces_js is not None:
        environment["PAM_FACES_JS"] = str(faces_js)
    else:
        environment.pop("PAM_FACES_JS", None)
    return subprocess.run(  # noqa: S603 - fixed argv, no shell, no caller-supplied args
        [shutil.which("node"), str(NODE_DRIVER)],
        capture_output=True,
        text=True,
        timeout=120,
        env=environment,
    )


# --- the stdlib half: never skips ----------------------------------------------------


def test_the_required_transition_list_is_not_empty():
    """M743's shape. The parametrized check below is built from this tuple, and pytest
    SKIPS an empty parametrize rather than failing it - so this is the one assertion that
    cannot disappear along with what it protects."""
    assert len(REQUIRED_TRANSITIONS) >= MINIMUM_TRANSITIONS, REQUIRED_TRANSITIONS
    assert len(REQUIRED_EXPORTS) >= MINIMUM_EXPORTS, REQUIRED_EXPORTS


def test_the_node_driver_ships_and_is_not_empty():
    """Without this, every check below that needs node would simply skip on a machine
    where the driver had been deleted, and the deletion would read as a green run."""
    assert NODE_DRIVER.is_file(), f"{NODE_DRIVER} is missing"
    assert len(NODE_DRIVER.read_text(encoding="utf-8")) > 2000, "the driver has been gutted"


@pytest.mark.parametrize("transition", REQUIRED_TRANSITIONS)
def test_the_driver_names_every_transition_this_task_owes(transition):
    """The spec-side floor on WHAT is executed, checked without running anything.

    A driver that still parses and still exits 0 while its assertions have been deleted is
    the failure mode this pins: the names are spelled from UX spec 6 / design spec 8.2 /
    vision R6 here, and the driver must carry each one.
    """
    assert transition in NODE_DRIVER.read_text(encoding="utf-8"), transition


@pytest.mark.parametrize("export", REQUIRED_EXPORTS)
def test_faces_js_still_exports_the_seam_the_driver_reaches_through(export):
    """`module.exports` is what makes the script executable outside a browser. Deleting an
    entry from it would make the Node half fail where node exists and vanish where it does
    not; this half fails on every machine."""
    source = FACES_JS.read_text(encoding="utf-8")
    assert f"{export}: {export}" in source or f"{export}: " in source, (
        f"faces.js no longer exports {export}"
    )


def _faces_export_names(source: str) -> set[str]:
    block = re.search(r"module\.exports = \{(.*?)\n  \};", source, re.DOTALL)
    assert block, "faces.js has no multi-line module.exports block"
    pairs = re.findall(r"^\s*(\w+): (\w+),", block.group(1), re.MULTILINE)
    assert [key for key, value in pairs if key != value] == [], pairs
    return {key for key, _ in pairs}


def assert_faces_exports_are_exactly(found: set[str], expected: tuple[str, ...]) -> None:
    """EQUALITY (M13 Task 2, C5): nothing missing AND nothing extra."""
    missing = sorted(set(expected) - found)
    extra = sorted(found - set(expected))
    assert missing == [] and extra == [], f"missing {missing}, never registered {extra}"


def test_faces_js_exports_exactly_the_registered_seam():
    """The membership check above cannot see an EXTRA export - a function the suite
    believes is reached and is not. Equality can, and with the reachability check below it
    means every export is both registered and executed."""
    found = _faces_export_names(FACES_JS.read_text(encoding="utf-8"))
    assert_faces_exports_are_exactly(found, REQUIRED_EXPORTS)


def test_the_faces_export_equality_refuses_an_extra_name_and_a_missing_one():
    """The equality pinned on hand-built sets, so a check weakened back to membership is
    red."""
    assert_faces_exports_are_exactly({"a", "b"}, ("a", "b"))
    with pytest.raises(AssertionError):
        assert_faces_exports_are_exactly({"a", "b", "c"}, ("a", "b"))
    with pytest.raises(AssertionError):
        assert_faces_exports_are_exactly({"a"}, ("a", "b"))


def test_the_browser_never_reaches_the_commonjs_seam():
    """The export block is guarded, so a browser - where `module` is undefined - skips it.
    An unguarded `module.exports` is a ReferenceError that blanks the page, which is M783
    with a different cause and the same result."""
    source = FACES_JS.read_text(encoding="utf-8")
    assert 'typeof module !== "undefined" && module.exports' in source
    assert 'typeof document !== "undefined"' in source, (
        "faces.js runs its page bootstrap unconditionally, so a plain node run would throw"
    )


# --- the node half: skips where node is absent, and that is why the half above exists -


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_executes_every_transition_it_claims():
    """The actual execution. `node`, no network, no dependency - the exiftool precedent."""
    result = _run_driver()
    assert result.returncode == 0, (result.stdout + result.stderr).strip()[:2000]
    ran = _OK_LINE.findall(result.stdout)
    assert ran, "the driver printed no results at all"
    counted = _RAN_LINE.search(result.stdout)
    assert counted is not None, "the driver did not report how many tests it ran"
    assert int(counted.group(1)) == len(ran), "the driver's own count disagrees with its output"
    assert len(ran) >= MINIMUM_TRANSITIONS, f"only {len(ran)} transitions executed"
    missing = [name for name in REQUIRED_TRANSITIONS if name not in ran]
    assert missing == [], f"these transitions did not run: {missing}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_the_page_proposes_a_name(tmp_path):
    """THE DRIVER, WATCHED FAILING, ON VISION R6's EXACT DEFECT.

    `view()` returns `nameValue: ""` unconditionally; this plants the one-word change that
    makes pam propose the first person it knows - which is what "a name pam proposed is a
    name a tired user accepts" is about. The copy lives in `tmp_path`; nothing on disk is
    touched, and the driver is pointed at it through the override its header documents.
    """
    source = FACES_JS.read_text(encoding="utf-8")
    planted = source.replace(
        '    nameValue: "",\n',
        '    nameValue: state.persons.length ? state.persons[0].label : "",\n',
        1,
    )
    assert planted != source, "the empty name value is not spelled as this test expects"
    copy = tmp_path / "faces.js"
    copy.write_text(planted, encoding="utf-8")
    result = _run_driver(copy)
    assert result.returncode != 0, "pam proposed a name and the driver called it green"
    assert "pam proposed a name for an unnamed group" in result.stderr
    assert "ok the name field is empty" not in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_skip_starts_writing_to_the_server(tmp_path):
    """The second planted defect, on the distinction Step 6 exists to make legible.

    Skip is SESSION state and is deliberately not persisted; Ignore is the persisted one.
    A Skip that quietly posted would be indistinguishable from the outside - the group
    would leave the queue either way - so the control is on the CALL again.
    """
    source = FACES_JS.read_text(encoding="utf-8")
    planted = source.replace(
        "    skip: function () {\n      openedAt = now();\n",
        "    skip: function () {\n      openedAt = now();\n"
        '      request("POST", "api/faces/cluster/0/dismiss", {});\n',
        1,
    )
    assert planted != source, "skip is not spelled as this test expects"
    copy = tmp_path / "faces.js"
    copy.write_text(planted, encoding="utf-8")
    result = _run_driver(copy)
    assert result.returncode != 0, "Skip wrote to the server and the driver called it green"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_not_the_same_person_confirms_without_a_preview(tmp_path):
    """Task 11 Step 8's named mutation, "split confirms without previewing", planted.

    The click on `Not the same person` must ask for a PROPOSAL. A page that confirmed
    straight away would re-shape a group nobody had looked at, and from the outside the
    queue would move either way - so, as for Skip, the control is on the CALL (M697).
    """
    source = FACES_JS.read_text(encoding="utf-8")
    planted = source.replace(
        '{ action: "preview" }',
        '{ action: "confirm", token: "planted" }',
        1,
    )
    assert planted != source, "the preview body is not spelled as this test expects"
    copy = tmp_path / "faces.js"
    copy.write_text(planted, encoding="utf-8")
    result = _run_driver(copy)
    assert result.returncode != 0, "a split was confirmed without a preview and it read green"
    assert "ok not the same person issues a preview and never a confirm" not in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_not_a_face_is_posted_as_dismissed(tmp_path):
    """Task 11 Step 8's named mutation: `state` writes 'dismissed' where 'not_a_face' was
    asked. The two words are the whole vocabulary, and both are 'non-NULL'; if nothing
    distinguishes them, the vocabulary is untested - so the driver is watched failing."""
    source = FACES_JS.read_text(encoding="utf-8")
    planted = source.replace(
        "{ face_ids: ids, review_state: NOT_A_FACE }",
        "{ face_ids: ids, review_state: DISMISSED }",
        1,
    )
    assert planted != source, "the not-a-face body is not spelled as this test expects"
    copy = tmp_path / "faces.js"
    copy.write_text(planted, encoding="utf-8")
    result = _run_driver(copy)
    assert result.returncode != 0, "Not a face was sent as Ignore and it read green"
    assert "ok not a face posts not_a_face, never dismissed" not in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_fails_when_merge_posts_to_the_surviving_persons_path(tmp_path):
    """Task 11 Step 8's named mutation "merge deletes the wrong row", at the page: the path
    names the person who STOPS existing and the body names the one who survives. Swapped,
    the server would delete Emma and keep Em."""
    source = FACES_JS.read_text(encoding="utf-8")
    planted = source.replace(
        'personPath(pending.source.id) + "/merge",\n        { into: pending.into.id },',
        'personPath(pending.into.id) + "/merge",\n        { into: pending.source.id },',
        1,
    )
    assert planted != source, "the merge request is not spelled as this test expects"
    copy = tmp_path / "faces.js"
    copy.write_text(planted, encoding="utf-8")
    result = _run_driver(copy)
    assert result.returncode != 0, "a merge deleting the surviving person read green"
    assert "ok confirming a merge posts the surviving id" not in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_the_node_driver_reports_a_script_it_cannot_even_load(tmp_path):
    """M783's own shape, inside this instrument: an unparseable script must FAIL here, not
    produce an empty result set that reads as nothing to run."""
    copy = tmp_path / "faces.js"
    copy.write_text(FACES_JS.read_text(encoding="utf-8") + "\n{\n", encoding="utf-8")
    result = _run_driver(copy)
    assert result.returncode != 0
    assert _OK_LINE.findall(result.stdout) == []


def test_the_driver_and_the_exports_agree_about_what_is_reachable():
    """Both directions, so the seam cannot shrink quietly on either side.

    Every export named above must actually be reached by the driver. An export nothing
    executes is a function this project believes is tested and is not - the same shape as
    a CSS rule for a class nothing renders.
    """
    driver = NODE_DRIVER.read_text(encoding="utf-8")
    unreached = [name for name in REQUIRED_EXPORTS if f"faces.{name}" not in driver]
    assert unreached == [], f"exported but never executed by the driver: {unreached}"


def test_the_page_and_the_driver_are_packaged_on_the_right_sides_of_the_wheel():
    """`faces.js` SHIPS; the driver does not. A test file inside the wheel would be dead
    weight in the deliverable, and a `faces.js` outside it is M781 - a wheel with no
    review UI in it at all, over 1856 green tests."""
    manifest = json.dumps(
        {"static": sorted(p.name for p in FACES_JS.parent.iterdir() if p.is_file())}
    )
    assert "faces.js" in manifest
    assert NODE_DRIVER.parent.name == "js"
    assert NODE_DRIVER.parent.parent.name == "tests"


# M1670's lesson (M10's C4): an instrument's own coverage is owned by nobody unless
# somebody writes it down. Spelled by hand, never derived from `globals()`, which would
# agree with any deletion. `tests/test_webui_static.py` anchors this module's existence
# from outside it, because no control here can notice its own file being deleted.
LOAD_BEARING_CONTROLS = {
    "test_the_required_transition_list_is_not_empty": (
        "M743: the parametrized checks below are SKIPPED, not failed, over an empty tuple"
    ),
    "test_the_node_driver_ships_and_is_not_empty": (
        "a deleted driver would make every node-dependent check skip, reading as green"
    ),
    "test_the_driver_names_every_transition_this_task_owes": (
        "the spec-side floor on WHAT runs, and it never skips"
    ),
    "test_faces_js_still_exports_the_seam_the_driver_reaches_through": (
        "the seam, pinned on machines without node too"
    ),
    "test_the_browser_never_reaches_the_commonjs_seam": (
        "an unguarded module.exports is a ReferenceError that blanks the page"
    ),
    "test_the_node_driver_executes_every_transition_it_claims": (
        "the execution itself - Blocker 8.4, the whole reason this module exists"
    ),
    "test_the_node_driver_fails_when_the_page_proposes_a_name": (
        "the driver watched failing on vision R6's exact defect"
    ),
    "test_the_node_driver_fails_when_skip_starts_writing_to_the_server": (
        "the driver watched failing on Skip-versus-Ignore, measured at the CALL (M697)"
    ),
    "test_the_node_driver_fails_when_not_the_same_person_confirms_without_a_preview": (
        "the driver watched failing on Task 11's named split mutation, at the CALL (M697)"
    ),
    "test_the_node_driver_fails_when_not_a_face_is_posted_as_dismissed": (
        "the driver watched failing on the review-state vocabulary swap (Step 8)"
    ),
    "test_the_node_driver_fails_when_merge_posts_to_the_surviving_persons_path": (
        "the driver watched failing on 'merge deletes the wrong row' (Step 8), at the CALL"
    ),
    "test_the_node_driver_reports_a_script_it_cannot_even_load": (
        "M783 inside the instrument: an unloadable script must fail, not run nothing"
    ),
    "test_the_driver_and_the_exports_agree_about_what_is_reachable": (
        "an export nothing executes is a function believed tested and not"
    ),
    "test_faces_js_exports_exactly_the_registered_seam": (
        "M13 C5: the registry is EQUALITY - membership cannot see an extra, unreached export"
    ),
    "test_the_faces_export_equality_refuses_an_extra_name_and_a_missing_one": (
        "the equality's own control: weakened back to membership, it goes red"
    ),
}

assert LOAD_BEARING_CONTROLS, "this module's control list is empty; it protects nothing"


def test_no_control_in_this_file_can_be_deleted_without_saying_so():
    """Removing one of these is a deliberate edit to `LOAD_BEARING_CONTROLS`, with the
    reason it was kept written beside it."""
    present = {name for name in globals() if name.startswith("test_")}
    missing = sorted(set(LOAD_BEARING_CONTROLS) - present)
    assert missing == [], (
        f"a load-bearing control has been deleted from {Path(__file__).name}: {missing}."
        f" Each is why another assertion here means anything"
        f" ({[LOAD_BEARING_CONTROLS[m] for m in missing]})."
    )


def test_the_faces_page_sends_its_requests_through_its_own_binding_transport():
    """M14 review R1 / M3500: the Node driver executes `pageRequest`; this pins that the page
    itself uses it - one `request:` in `initFacesPage`, and it is `pageRequest()`."""
    source = FACES_JS.read_text(encoding="utf-8")
    init = source[source.index("function initFacesPage() {") :]
    init = init[: init.index("\n}\n")]
    assert init.count("request:") == 1, init
    assert "request: pageRequest()," in init, init
