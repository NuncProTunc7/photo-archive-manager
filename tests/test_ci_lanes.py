"""The ONE CI lane, pinned from inside the suite (M6 Task 9; rewritten at M11 Task 1).

THERE IS ONE LANE NOW - **rewritten at M11 Task 1, executing design 9.4 rev 21 (owner
ruling 2026-09-04, `docs/superpowers/specs/2026-09-04-suite-cost-ruling.md`)**. From M6
Task 9 to M10 this module pinned TWO lanes: a `fast` lane on every push that excluded
`tests/test_faults_matrix.py`, and a `gate` lane on main/master/tags that excluded nothing.
The fast lane is retired: measured across four closes its exclusion bought 10.9 % -> 7.8 %
-> 7.90 %, which by the M10 close was two near-identical runs at DOUBLE the CI cost to save
nineteen seconds, against a **66-test hole in M5's fault-injection matrix on every ordinary
push**. Its 100 s target went with it, missed by 117 %.

**The module's checks did not shrink with the lane, and most of them got STRONGER.** What
was "the fast lane skips the matrix and the gate lane runs it" is now "**no push escapes the
fault matrix**". What was "the gate can fire and the fast lane is unconditional" is now "**the
lane has no `if:` at all**" - which is the property that matters most, because with one lane a
condition on main/master/tags would leave every PULL REQUEST with no CI whatsoever. Exactly
one test was retired as genuinely obsolete
(`test_the_fast_lane_announces_the_exclusion_it_actually_enforces`): it pinned the fast lane's
printed announcement of its own partiality against the exclusion it enforced, and both the
announcement and the exclusion are gone, so there is no property left behind it.

THE LOCAL PRE-COMMIT HOOK IS STILL THE FULL SUITE, and that is NOT a consequence of this
change. `test_the_pre_commit_hook_runs_the_whole_suite_unnarrowed` is the same assertion it
always was - it was never about the fast lane, only ever framed by it - and it matters MORE
now, not less. Deleting it with the lane would have been M743 exactly.

THE REMOTE EXISTS AND THE LANE RUNS - corrected at M8 Task 3. This module used to open
"THERE IS NO GIT REMOTE ON THIS PROJECT", false since the M7 close
(`github.com/NuncProTunc7/photo-archive-manager`). What it buys is catching a broken workflow
**before** a push rather than instead of one - and hosted CI is ~3x slower than this machine,
so a defect found here is found minutes earlier. The M8 gate-lane race is the standing proof
that a green hosted tick and a correct lane are still two different things.

THE PROPERTY, NOT THE STRING. The requirement is that "a test file added under `tests/` is
picked up without editing CI config - select by exclusion, never by an enumerated include
list". A test that grepped the workflow for `--ignore` would restate the fixture it was
written against and prove nothing: an enumerated lane can also contain the substring
`--ignore`, and a lane could name every test file it runs and still pass a grep. So the
lane's pytest arguments are extracted from the workflow and handed to a REAL pytest
collector, against a test file created for the occasion. What is asserted is what pytest
actually collects.

Every check here carries its own negative control -
`test_the_lane_check_can_tell_an_enumerated_lane_from_an_excluding_one` runs the same
machinery against an enumerated include list and requires it to come out FALSE. It is kept
**able to tell both shapes apart even though the shipped workflow now uses neither**: a
control that only works against the document as it stands today is a control that stops
working the day the document changes.

A REAL YAML PARSER, AND A SMALL STRUCTURAL READER BESIDE IT - corrected at the M6 close.
This paragraph used to read "NO YAML PARSER", on the grounds that the project takes no
dependency without a spec amendment. The grounds were right; the conclusion was wrong.
`.github/workflows/ci.yml` shipped at `db4ebe3` **not parsing as YAML** - a plain scalar
carrying ": " - which GitHub rejects as an invalid workflow, so both lanes were dead and
every check in this module was green about a file the real world would refuse to load.
**A hand-rolled parser cannot notice that the document is not the language.**

So spec 9.4 rev 11 requires a real parser and adopts `pyyaml` DEV-ONLY for it - dev-only
still, after M9 Task 6 spent the runtime closure on design 1's four image and geocoding
packages, and no module under `pam/` may import it (asserted at the bottom of this file;
`tests/test_deps.py` owns the runtime list itself). `_lanes()` below stays, because it
expands `$env:` references and flattens `run: |` blocks into individual commands, which is
what every check here needs and which a raw parse does not give - but it is now
**cross-checked against the real parser** rather than trusted, and that cross-check found a
second defect on its first run (`_unquote`). It knows the four shapes this workflow uses,
and `test_the_workflow_parse_found_what_it_expected_to_find` fails loudly if the file grows
a fifth rather than letting the lane silently read as empty.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
FAULT_MATRIX = "tests/test_faults_matrix.py"

# The one lane's job id, spelled here as a literal rather than read back out of the parse it
# is used to check (this module's own rule, and `m10.md`'s: an oracle built from the thing it
# checks is a survivor waiting to happen).
LANE = "ci"

# M11 Task 7 / A2. The workflow gained a SECOND JOB, and it is deliberately not a second
# LANE: `faces` does not run a subset of the suite instead of the whole one (which is what
# the retired `fast` lane did and what every check below is written against) - it runs the
# tests the suite lane CANNOT run, because the suite lane installs `.[dev]` and every
# `faces`-marked test skips there. The distinction is kept in the code rather than in a
# comment: `_lane_names` is every JOB, `_suite_lanes` is the jobs that run the whole
# suite, and the properties about exclusion, the fault matrix and new-file pickup are
# asserted over the second.
FACES_JOB = "faces"
# M12 Task 12 Part B (design 9.4 rev 27-28, P5/Q5): the optional `[ocr]` extra's job, the faces
# job's shape. Like `faces`, a JOB and not a lane: it runs `-m ocr` and nothing else.
OCR_JOB = "ocr"
# M15 Task 14: the installer's behaviour tests (Task 13B's job body), on a pinned Inno Setup.
# `tests/test_public_workflows.py` owns its pins and its order.
INSTALLER_JOB = "installer"
ALL_JOBS = [LANE, FACES_JOB, OCR_JOB, INSTALLER_JOB]

# A3's two pinned files, spelled here as the WORKFLOW's copy of them and compared against
# `pam/enrich/faces_model.py`'s constants below. Two artefacts, one fetch: if the product
# moves to a different pack and this file does not, the faces job would validate the old
# bytes and every parity assertion would be measuring a pack the product does not use.
PINNED_PACK_FILES = (
    "face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx",
)

# M12 TASK 6 (design 9.4 rev 27, P5): the node ids the faces job must see PASS, read by
# `tools/ci_required_passed.py faces.xml FACES_JOB_MUST_PASS` from this file with `ast`.
# SPELLED BY HAND, NEVER COMPUTED FROM THE `faces` MARKER (M743): a list derived from the marker
# agrees with whatever the marker selects today, so a deleted or unmarked test would vanish from
# the requirement together with the test. The script refuses a non-literal or empty value; the
# test below compares this list against `tests/test_faces_model.py::FACES_MARKED_TESTS`, the
# second hand spelling, so removing one name here is red.
FACES_JOB_MUST_PASS = (
    "tests/test_faces_enricher.py::test_a_real_pack_gives_the_enricher_the_identity_the_digest_computes",
    "tests/test_faces_enricher.py::test_a_real_photograph_produces_one_marker_and_one_face_matching_the_fixture",
    "tests/test_faces_enricher.py::test_available_is_true_with_a_real_pack_and_names_the_two_files",
    "tests/test_faces_enricher.py::test_the_confidence_of_every_detection_is_inside_design_6_4s_range",
    "tests/test_faces_enricher.py::test_the_stored_embedding_is_128_float32s_and_is_unit_LENGTH",
    "tests/test_faces_model.py::test_a_real_session_really_runs_on_the_cpu_provider_only",
    "tests/test_faces_model.py::test_the_detector_reproduces_the_recorded_boxes_scores_and_landmarks",
    "tests/test_faces_model.py::test_the_real_pack_is_the_pinned_one",
    "tests/test_faces_model.py::test_the_real_runtime_writes_the_receipt_with_the_real_providers",
    "tests/test_faces_model.py::test_the_recogniser_reproduces_the_recorded_embedding_and_would_see_a_channel_swap",
    "tests/test_net_canary.py::test_loading_a_real_model_pack_opens_no_socket_at_all",
    "tests/test_net_canary.py::test_the_enrich_stage_WITH_FACES_ON_opens_no_socket_at_all",
)
assert FACES_JOB_MUST_PASS, "the faces job's required list is empty; it requires nothing"

# Q1's THREE hashes (design 1 rev 29), spelled here BY HAND: the recorded source wheel and its two
# members. The ocr job must verify each; the two file hashes must equal the product's constants.
OCR_PACK_HASHES = {
    "rapidocr_onnxruntime-1.4.4-py3-none-any.whl": (
        "971d7d5f223a7a808662229df1ef69893809d8457d834e6373d3854bc1782cbf"
    ),
    "ch_PP-OCRv4_det_infer.onnx": "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9",
    "ch_PP-OCRv4_rec_infer.onnx": "48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b",
}
assert len(OCR_PACK_HASHES) == 3

# Rev 4.1: the ocr job's proof that no RapidOCR code is importable where pam runs, verbatim.
OCR_NO_RAPIDOCR_PROBE = (
    'python -c "import importlib.util, sys;'
    " sys.exit(importlib.util.find_spec('rapidocr_onnxruntime') is not None)\""
)

# M12 TASK 12 (Q5): the node ids the ocr job must see PASS, read by
# `tools/ci_required_passed.py ocr.xml OCR_JOB_MUST_PASS`. SPELLED BY HAND (M743), and compared
# below with `tests/test_ocr_model.py::OCR_MARKED_TESTS`, the second hand spelling.
OCR_JOB_MUST_PASS = (
    "tests/test_ocr_model.py::test_every_fixture_matches_the_regression_baseline",
    "tests/test_ocr_model.py::test_every_governed_fixture_meets_truth_json",
    "tests/test_ocr_model.py::test_no_k11_fixture_yields_a_date_truth_does_not_name",
    "tests/test_ocr_model.py::test_the_real_sessions_run_on_the_cpu_provider_only",
    "tests/test_net_canary.py::test_the_ocr_engine_loads_and_reads_without_a_python_socket",
    "tests/test_ocr_enricher.py::test_the_real_engine_reads_a_committed_imprint_and_the_plan_dates_the_scan",  # M12 Task 13
    "tests/test_t1_ocr_chain.py::test_the_m12_chain_on_the_REAL_pack_reads_the_rendered_imprints",  # M12 Task 14
)
assert OCR_JOB_MUST_PASS, "the ocr job's required list is empty; it requires nothing"
REQUIRED_PASSED_SCRIPT = "tools/ci_required_passed.py"

# Spec 9.4, quoted: "CI (tiny library): all S-criteria tests, sanitizer/planner suites,
# canary." The lane names these again by hand, after the full suite has already run them, so
# the lane can SHOW you the checks it exists for. Mutations M507/M508 deleted those steps and
# the whole module stayed green.
SPEC_94_NAMED_CHECKS = (
    "tests/test_net_canary.py",  # the privacy canary
    "tests/test_net.py",  # the network grep-gate
    "tests/test_sanitize.py",  # the sanitizer suite
    "tests/test_planner.py",  # the planner suite
)
TINY_LIBRARY_GATE = "tools/ci_gate_library.py"  # spec 9.4's "CI (tiny library)"

# Design 9.4 rev 21 / M11 Task 1 Step 2: the lane runs on every trigger. Spelled from the
# ruling, not read off the document. NOTE the `True`: YAML 1.1 parses a bare `on:` key as the
# BOOLEAN true, so `document["on"]` raises KeyError and a check written that way would look
# like a bug in the workflow rather than in itself.
REQUIRED_TRIGGERS = ("push", "pull_request")

# Every way pytest can be told to run less than everything. Matched as a token PREFIX,
# not as a substring: `-knot_matrix` and `-mnotslow` are valid pytest and contain neither
# `"-k "` nor `"-m "`, which is what the first version of this check looked for.
NARROWING_FLAGS = ("-k", "-m", "--ignore", "--deselect")


def _top_level_env(lines: list[str]) -> dict[str, str]:
    """The workflow's top-level `env:` mapping.

    Kept after the fast lane's retirement, and not as vestige: the retired lane's exclusion
    lived in `FAST_LANE_SKIPS` here, so this is what
    `test_the_workflow_parse_found_what_it_expected_to_find` uses to assert that no such
    definition has come back.
    """
    found: dict[str, str] = {}
    inside = False
    for line in lines:
        if line.startswith("env:"):
            inside = True
            continue
        if inside:
            if line[:1] not in (" ", "#", ""):  # any other top-level key ends the block
                break
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and ":" in stripped:
                key, _, value = stripped.partition(":")
                found[key.strip()] = value.strip()
    return found


def _unquote(payload: str) -> str:
    """Strip a YAML scalar's surrounding quotes, if it has any.

    ADDED AT THE M6 CLOSE, and found by the new real-parser cross-check at the bottom of
    this module rather than by reading. A `run:` value that has to carry ": " must be
    quoted or the whole workflow is invalid YAML (finding F1) - and this reader was
    keeping the quotes, so it believed the lane ran `'Write-Host "..."'`, quotes and all.
    Harmless while nothing was quoted; a quoted `pytest` step would have made
    `_pytest_invocations` see `'pytest` and silently stop finding the lane's suite run.
    """
    for quote in ("'", '"'):
        if len(payload) >= 2 and payload.startswith(quote) and payload.endswith(quote):
            return payload[1:-1]
    return payload


def _expand(command: str, env: dict[str, str]) -> str:
    for key, value in env.items():
        command = command.replace(f"$env:{key}", value)
    return _unquote(command)


def _lanes() -> dict[str, list[str]]:
    """job id -> every shell command that job runs, with `$env:` references expanded."""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    env = _top_level_env(lines)

    commands: dict[str, list[str]] = {}
    job: str | None = None
    in_jobs = False
    block_indent: int | None = None
    for line in lines:
        if line.startswith("jobs:"):
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if line and not line.startswith(" ") and not line.startswith("#"):
            break  # a later top-level key: out of `jobs:`

        if block_indent is not None:
            # Inside a `run: |` block scalar: continuation lines are indented further.
            indent = len(line) - len(line.lstrip())
            if line.strip() and indent >= block_indent:
                assert job is not None
                commands[job].append(_expand(line.strip(), env))
                continue
            block_indent = None

        stripped = line.strip()
        if (
            len(line) - len(stripped) == 2
            and stripped.endswith(":")
            and not line.startswith("   ")
        ):
            job = stripped[:-1]
            commands[job] = []
            continue
        if job is None:
            continue
        body = stripped[2:].strip() if stripped.startswith("- ") else stripped
        if body.startswith("run:"):
            payload = body[len("run:") :].strip()
            if payload == "|":
                block_indent = len(line) - len(line.lstrip()) + 2
            else:
                commands[job].append(_expand(payload, env))
    return commands


def _lane_names() -> list[str]:
    """Every lane the workflow actually defines, with a floor written independently.

    A loop over a literal `("fast", "gate")` is the `for`-statement form of M743's empty
    `parametrize`: nothing ties it to the document, so narrowing or emptying it disarms
    the check silently - and unlike `parametrize`, an empty `for` does not even skip, it
    passes. The floor is spelled here rather than derived from the parse it guards.

    **It stays a LIST, and the loops over it stay loops, with one lane in it.** Collapsing
    this to `return [LANE]` would delete exactly the guard it exists to be: what it asserts
    is that the document defines the lane set the ruling says it does and *no other* - so a
    second lane reappearing (or this one being renamed) fails here, loudly, once, instead of
    being silently skipped by every caller.
    """
    import yaml

    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    lanes = sorted(document["jobs"])
    assert lanes == sorted(ALL_JOBS), f"unexpected job set {lanes}; every job needs checking"
    return lanes


def _suite_lanes() -> list[str]:
    """The jobs that run the WHOLE suite - today exactly one, and that is the assertion.

    Everything below about exclusions, the fault matrix and picking up a new test file is a
    property of a lane that runs everything, and applying it to the faces job would be
    asserting that a job whose whole purpose is `-m faces` collects the tree. So the split
    is made once, here, and the floor is spelled beside it: a THIRD job appearing is
    `_lane_names`'s red suite, and a job that stops being a suite lane is this one's.
    """
    lanes = [job for job in _lane_names() if job not in (FACES_JOB, OCR_JOB, INSTALLER_JOB)]
    assert lanes == [LANE], f"unexpected suite-lane set {lanes}; every lane needs checking"
    return lanes


def _pytest_invocations(job: str) -> list[list[str]]:
    """The arguments each `pytest ...` command in `job` passes, `pytest` itself dropped."""
    return [
        command.split()[1:] for command in _lanes()[job] if command.split()[:1] == ["pytest"]
    ]


def _lane_args(job: str) -> list[str]:
    """The ONE lane-wide pytest invocation of `job` - the one that is not a named subset.

    The lane's suite run passes no test PATHS; spec 9.4's named-checks step passes several.
    Distinguishing them by "has no path argument" rather than by position means reordering
    the steps cannot silently repoint this at the wrong one. A path is a bare argument -
    `--ignore=tests/x.py` names a file and is not one, which is the whole difference between
    excluding and enumerating.
    """
    whole = [
        args
        for args in _pytest_invocations(job)
        if not any(a.endswith(".py") and not a.startswith("-") for a in args)
    ]
    assert len(whole) == 1, f"{job} runs {len(whole)} whole-suite pytest commands, expected 1"
    return whole[0]


def _job_block(job: str) -> str:
    """The raw text of one job, from its key to the end of the file or the next job."""
    raw = WORKFLOW.read_text(encoding="utf-8")
    assert f"\n  {job}:" in raw, f"no `{job}:` job in the workflow"
    block = raw.split(f"\n  {job}:", 1)[1]
    # With one lane there is nothing after it to cut, but the cut stays: a second job
    # appearing must not be read as part of this one's block. `_lane_names()` is the check
    # that such a job exists at all; this is only about not misattributing its text.
    for other in _lanes():
        if other != job:
            block = block.split(f"\n  {other}:", 1)[0]
    return block


def _trigger(job: str) -> str | None:
    """A job's `if:` expression as written, or None if the job has no condition.

    Deliberately textual, and the docstrings below say so: a GitHub `if:` expression is
    evaluated by GitHub, not by anything reachable from here, so there is no collector to
    hand it to the way `_collect` takes the pytest arguments. What CAN be checked is
    whether a condition is there at all - which is exactly the property mutation M506 broke
    while every other check in this module stayed green, and which, with one lane, is now
    the stronger claim: **there must be none.**
    """
    block = _job_block(job)
    if "\n    if:" not in block:
        return None
    return block.split("\n    if:", 1)[1].split("\n    runs-on:", 1)[0]


def _collect(args: list[str]) -> str:
    """What pytest really collects for these arguments, from the repository root."""
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, f"collection failed for {args}: {completed.stdout[-800:]}"
    return completed.stdout


@pytest.fixture
def probe():
    """A new test file under `tests/`, of the kind a future task will add."""
    name = f"test_zz_ci_lane_probe_{os.getpid()}.py"
    path = Path(__file__).parent / name
    path.write_text("def test_a_new_file_nobody_told_ci_about():\n    assert True\n", "utf-8")
    try:
        yield name
    finally:
        path.unlink(missing_ok=True)


def test_the_workflow_parse_found_what_it_expected_to_find():
    """The reader above is small enough to be wrong quietly. This is the loud part.

    The `env:` half is not vestigial after the fast lane's retirement, it is the retirement's
    own pin: `FAST_LANE_SKIPS` was a top-level `env:` entry, so an empty mapping here is the
    assertion that the exclusion definition has not come back by the door it left through.
    """
    lanes = _lanes()
    assert set(lanes) == set(ALL_JOBS), (
        f"expected jobs {sorted(ALL_JOBS)}, found {sorted(lanes)}"
    )
    assert lanes[LANE], f"{LANE} parsed as running no commands at all"
    assert lanes[FACES_JOB], f"{FACES_JOB} parsed as running no commands at all"
    assert lanes[OCR_JOB], f"{OCR_JOB} parsed as running no commands at all"
    assert any(command.startswith("pytest") for command in lanes[LANE])
    assert _top_level_env(WORKFLOW.read_text(encoding="utf-8").splitlines()) == {}, (
        "the workflow grew a top-level `env:` mapping - if that is a resurrected"
        " FAST_LANE_SKIPS, design 9.4 rev 21 retired it"
    )


def test_a_new_test_file_is_picked_up_by_the_lane_without_touching_ci_config(probe):
    """The requirement, asserted against a real collection rather than a grep."""
    # M820/M821 (M8 Task 3 review): a lane loop over a literal tuple is tied to nothing, so
    # narrowing or emptying it leaves lanes unchecked with the suite green. Iterate the lanes
    # the DOCUMENT has, with an independently written floor beside them.
    lanes = _suite_lanes()
    for job in lanes:
        collected = _collect(_lane_args(job))
        assert probe in collected, f"the {job} lane would not have run a newly added {probe}"


def test_the_lane_check_can_tell_an_enumerated_lane_from_an_excluding_one(probe):
    """The negative control for the test above: the same machinery, an include list.

    Without this, `test_a_new_test_file_is_picked_up_by_the_lane...` could be passing because
    the probe string appears in any collection output whatsoever, or because `_collect`
    returns something that always contains it. An enumerated lane is exactly what the
    requirement forbids, so it is what the check must be able to see.

    **KEPT AT M11 Task 1 EXACTLY AS IT WAS, and deliberately so.** The shipped workflow now
    uses neither shape - it passes `-q` and nothing else, so it neither enumerates nor
    excludes - and the temptation is therefore to call this control moot. It is the opposite:
    a control is for the document somebody writes NEXT. It must stay able to tell an
    enumerated lane from an excluding one whether or not today's file is either.
    """
    enumerated = _collect(["tests/test_sanity.py", "tests/test_paths.py"])
    assert probe not in enumerated, "an enumerated include list appeared to pick up a new file"
    assert "test_sanity.py" in enumerated, "the control collected nothing, so it proves nothing"


def test_the_lane_runs_the_fault_matrix(probe):
    """No push escapes M5's fault-injection matrix - the whole point of retiring the lane.

    This was `test_the_fast_lane_skips_the_fault_matrix_and_the_gate_lane_runs_it`, and the
    property did not go with the lane, it got STRONGER: what used to be true of one lane out
    of two on one set of refs is now true of every trigger. The 66 tests in that file are
    what the fast lane's 7.90 % saving was bought with, and the ruling bought them back.

    Asserted as a fact about COLLECTION, not about the workflow's text: a lane could name
    the matrix in a comment, or exclude it by a `-k` expression that no grep would catch.
    """
    for job in _suite_lanes():
        collected = _collect(_lane_args(job))
        assert FAULT_MATRIX.split("/")[-1] in collected, (
            f"the {job} lane would not run {FAULT_MATRIX}"
        )
        # The probe pins that this collection is a whole-suite one and not a lucky path
        # argument naming the matrix directly.
        assert probe in collected, f"the {job} lane's suite run is not collecting the tree"


def test_the_lane_excludes_nothing():
    """A lane that grew an exclusion would still be green, and would no longer be the gate.

    Distinct from `test_the_lane_runs_the_fault_matrix` above on purpose, and not a
    duplicate of it: that one asks what pytest really collects TODAY, for one named file;
    this one asks whether the lane carries any narrowing flag at all, for any file, including
    ones nobody has written yet.
    """
    args = _lane_args(LANE)
    assert not [a for a in args if a.startswith(NARROWING_FLAGS)], (
        f"the lane narrowed its own suite: {args}"
    )


def test_the_lane_is_unconditional_and_runs_on_pushes_and_pull_requests():
    """M11 Task 1 Step 2, and the most load-bearing check in this module.

    This was `test_the_gate_lane_can_actually_fire_and_the_fast_lane_is_unconditional`, born
    of M6 Task 9's M506: every OTHER check here asserts what a lane RUNS, none asked whether
    it runs at all, so narrowing `if:` to a branch that does not exist disabled the gate with
    all eight tests green.

    **With one lane the claim inverts and hardens: there must be no `if:` whatsoever.** The
    retired `gate` lane's condition named main/master/tags, which was safe only because the
    `fast` lane caught everything else. Reapply that condition now and every PULL REQUEST,
    and every non-default branch, gets no CI at all - the same silent nothing M506 produced,
    reached by a change that would look like tidying.

    The triggers are checked beside it, from literals: an unconditional job in a workflow
    that only fires `on: push` is unconditional about nothing.
    """
    for job in _lane_names():
        condition = _trigger(job)
        assert condition is None, (
            f"the {job} lane grew an `if:` ({condition!r}); with one lane a condition means"
            " pull requests and non-default branches get no CI at all"
        )
    # M11 Task 7: the loop above now covers the faces job too, and that is A2's "gates
    # tags" clause rather than an accident of the loop. A tag push IS a push, so an
    # unconditional job runs on every tag; a condition naming tags would gate tags and
    # nothing else, which is exactly the retired `gate` lane's mistake.

    import yaml

    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # YAML 1.1 reads a bare `on:` key as the BOOLEAN true, not the string "on".
    triggers = document[True]
    # M1682: `REQUIRED_TRIGGERS = ()` left this loop asserting nothing, so the workflow
    # could drop `pull_request:` outright - the very outcome Step 2 deleted the `if:` to
    # prevent - under a test whose name still said it checked it. The floor is the
    # ruling's two triggers, spelled here.
    assert set(REQUIRED_TRIGGERS) == {"push", "pull_request"}, REQUIRED_TRIGGERS
    for required in REQUIRED_TRIGGERS:
        assert required in triggers, f"the workflow does not fire on {required}: {triggers}"


def test_the_lane_names_spec_9_4s_checks_and_runs_the_tiny_library():
    """Spec 9.4 by name, not by "the full suite covers it" (M6 Task 9 review, M507/M508).

    The named steps are redundant with the full suite BY DESIGN - 9.4 requires the lane to
    name them, so that the lane can show the check it exists for. That redundancy is also
    what made them free to delete: with the suite still green either way, removing both
    steps cost nothing that any test could see.

    Each named file is also required to EXIST. A step naming a module that has since been
    renamed passes any grep and fails in CI, which is the failure this repository cannot
    observe for itself.
    """
    # M1681: the loop below is vacuous if SPEC_94_NAMED_CHECKS is emptied or narrowed -
    # emptying it left 2972 tests green while this guard, the one written to prevent
    # M507/M508, asserted nothing at all. Spec 9.4's sentence, spelled out here beside the
    # loop rather than derived from the tuple the loop reads: "CI (tiny library): all
    # S-criteria tests, sanitizer/planner suites, canary."
    assert set(SPEC_94_NAMED_CHECKS) == {
        "tests/test_net_canary.py",
        "tests/test_net.py",
        "tests/test_sanitize.py",
        "tests/test_planner.py",
    }, f"spec 9.4's named checks were edited away from the spec: {SPEC_94_NAMED_CHECKS}"

    named = {argument for args in _pytest_invocations(LANE) for argument in args}
    for check in SPEC_94_NAMED_CHECKS:
        assert check in named, f"the lane no longer names spec 9.4's {check}"
        assert (REPO_ROOT / check).exists(), f"the lane names {check}, which does not exist"

    assert any(TINY_LIBRARY_GATE in command for command in _lanes()[LANE]), (
        f"the lane no longer runs {TINY_LIBRARY_GATE} - spec 9.4's 'CI (tiny library)'"
    )
    assert (REPO_ROOT / TINY_LIBRARY_GATE).exists()


def test_the_lanes_tiny_library_pass_really_passes():
    """`tools/ci_gate_library.py` is a step no lane here can run, so the suite runs it.

    Spec 9.4's "CI (tiny library)". It exits nonzero if `pam verify` returns anything but
    0 over the library - measured at 0 today, all twelve checks passing - and the whole
    pass costs about a second and a half.
    """
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "ci_gate_library.py")],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout[-1500:] + completed.stderr[-500:]
    assert "GATE PASSED" in completed.stdout

    # The throwaway PAM_HOME, checked from outside the process (M6 Task 9 review, M532).
    # Deleting the override left the gate driving the machine's REAL PAM_HOME - the one
    # spec 3 rev 8 already refuses to auto-select from because of stale pointers - and
    # every assertion above stayed green. Both paths are derived from the same scratch
    # workdir, so requiring them to be siblings pins the property without pinning a path.
    library = re.search(r"tiny library at (.+): \d+ files", completed.stdout)
    home = re.search(r"Throwaway PAM_HOME: (.+)", completed.stdout)
    assert library and home, (
        f"the gate no longer reports where it worked: {completed.stdout[:400]}"
    )
    assert Path(home.group(1).strip()).parent == Path(library.group(1).strip()).parent, (
        f"the gate's PAM_HOME ({home.group(1)}) is not beside its scratch library"
        f" ({library.group(1)}) - it is using the machine's own home"
    )


# --- the workflow as a DOCUMENT, not as text (M6 review, finding F1) -----------------
#
# Everything above this line reads the workflow structurally, with the deliberately small
# reader `_lanes()` documented at the top of this module. That reader answered every
# question asked of it while the file it read WOULD NOT LOAD: `.github/workflows/ci.yml`
# shipped at `db4ebe3` with a plain scalar containing ": ", which GitHub rejects as an
# invalid workflow - so both lanes were dead and nothing here noticed. A hand-rolled
# parser cannot notice that the document is not the language.
#
# Spec 9.4 rev 11 therefore requires a real parser, and adopts `pyyaml` DEV-ONLY for it.


def test_the_workflow_is_a_document_github_can_actually_load():
    """The check whose absence let a broken workflow ship. Nothing else asserts this."""
    import yaml

    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(document, dict), "the workflow is not a YAML mapping"
    assert set(document["jobs"]) == set(ALL_JOBS), document["jobs"].keys()


def test_the_lane_asserts_node_is_present_before_running_pytest():
    """C3: `tests/test_static_syntax.py`'s `node --check` half SKIPS where node is
    absent - which the local machine needs, so it can run the suite without node - but a
    lane that silently skips that guard is indistinguishable from a lane that ran it. This
    is the exact shape of failure this project keeps meeting (the gate lane's race, the
    workflow that never parsed): a green tick that means less than it appears to.

    So the lane must fail LOUDLY if node is absent, on the `choco install exiftool` /
    `pam doctor` precedent already in the workflow - a step running `node --version` ahead
    of pytest.

    Asserted PER LANE, BY NAME, on purpose, and that survives the retirement: with two lanes
    a check asking "does *some* lane mention node" survived deleting the step from one of
    them; with one lane the same check would survive the step being attached to a job nobody
    added yet. Real parser, not the hand-rolled reader (spec 9.4), because this is exactly
    the kind of structural question `_lanes()` is not trusted with unescorted.
    """
    import yaml

    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    # M820/M821 (review): the lane list was a literal that nothing tied to the document,
    # so narrowing it - or emptying it outright - left a lane unguarded with all 1870 tests
    # green. A `for` loop is the same vacuity M743 found in `parametrize`. Iterate the lanes
    # the DOCUMENT actually has, with an independently written floor beside them: the idiom
    # `test_the_workflow_parse_found_what_it_expected_to_find` already uses above.
    # `_suite_lanes()`, not `_lane_names()`: M11's faces job runs `pytest -m faces`, which
    # collects none of `tests/test_static_syntax.py` - so requiring `node` there would fail a
    # job that has no use for it. The property is about a lane that runs the file whose guard
    # can silently skip, and that is still exactly one job.
    for job in _suite_lanes():
        steps = document["jobs"][job]["steps"]
        node_indices = [
            i for i, s in enumerate(steps) if s.get("run", "").strip() == "node --version"
        ]
        assert len(node_indices) == 1, (
            f"{job}: expected exactly one 'node --version' step, found {len(node_indices)}"
        )
        pytest_indices = [i for i, s in enumerate(steps) if "pytest" in s.get("run", "")]
        assert pytest_indices, f"{job}: no pytest step found at all"
        assert node_indices[0] < min(pytest_indices), (
            f"{job}: the node presence check must run before the first pytest step"
        )


def test_the_yaml_check_can_see_the_defect_it_exists_for():
    """The negative control, and it is the ACTUAL line that shipped, not a stand-in.

    A guard that has never been shown to fail is a guard nobody has tested (this module's
    own opening argument). So the shipped defect is replayed here verbatim: an unquoted
    `run:` scalar carrying ": " inside it. If a future YAML library started accepting it,
    this test fails and the guard above is revealed as decorative.

    The `fast:` in the fixture below is the SHIPPED TEXT of the M6 defect and is left
    verbatim on purpose - it is a historical artefact being replayed, not a reference to a
    lane this workflow still has.
    """
    import yaml

    broken = 'jobs:\n  fast:\n    steps:\n      - run: Write-Host "did NOT run: $env:X"\n'
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(broken)

    fixed = "jobs:\n  fast:\n    steps:\n      - run: 'Write-Host \"did NOT run: $env:X\"'\n"
    assert yaml.safe_load(fixed)["jobs"]["fast"]["steps"][0]["run"].startswith("Write-Host")


def test_the_hand_rolled_reader_agrees_with_a_real_parser_about_every_command():
    """The small reader stays, and the real parser becomes its oracle.

    `_lanes()` is not deleted: it expands `$env:` references and flattens `run: |` blocks
    into individual commands, which is what every check above needs and which a raw parse
    does not give. What it must never do again is answer questions about a file it has
    misread. So the two are compared - every command the structural reader found must be
    present in the text the real parser extracted for that job, and every `run:` step the
    parser found must have contributed at least one command.

    **Both readings survive M11 Task 1 and stay cross-checked over the surviving lane.** The
    retirement halved the number of jobs, not the number of parsers: F1 was a file that did
    not parse as YAML at all, and one lane is exactly as capable of being that file as two.
    """
    import yaml

    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    env = document.get("env", {})
    for job in _lane_names():
        steps = document["jobs"][job]["steps"]
        parsed_runs = [step["run"] for step in steps if "run" in step]
        expanded = "\n".join(_expand(run, env) for run in parsed_runs)
        found = _lanes()[job]
        assert found, f"the structural reader found no commands at all for {job}"
        for command in found:
            assert command in expanded, (
                f"{job}: the structural reader believes this lane runs {command!r},"
                " which the real parser did not find in any of its `run:` steps"
            )
        assert len(parsed_runs) <= len(found), (
            f"{job}: the real parser found {len(parsed_runs)} `run:` steps but the"
            f" structural reader produced only {len(found)} commands - a step was missed"
        )


def test_no_module_under_pam_imports_the_dev_only_yaml_parser():
    """Spec 9.4 rev 11: `pyyaml` is a DEV dependency and `pam/` may not import it.

    `tests/test_net.py`'s import guard covers network-capable modules and would not see
    this one, so the constraint that justified adopting the dependency is asserted here,
    beside the reason it was adopted.

    This used to say "and the runtime closure stays `{click, pam}`", which M9 Task 6 made
    false - and which was never this test's claim anyway: it walks `pam/` for one import.
    The runtime list is `tests/test_deps.py`'s, in both directions.
    """
    offenders = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "pam").rglob("*.py")
        if re.search(r"^\s*(import yaml|from yaml\b)", path.read_text(encoding="utf-8"), re.M)
    ]
    assert offenders == [], f"pam/ imports the dev-only YAML parser: {offenders}"


def test_the_narrowing_check_can_see_a_command_that_really_is_narrowed():
    """The negative control under `NARROWING_FLAGS` (M1680), and it is behavioural.

    `str.startswith(())` is `False` for every token, so emptying that tuple disarms BOTH
    of its consumers at once - `test_the_lane_excludes_nothing` stops seeing an exclusion
    in the lane, and `test_the_pre_commit_hook_runs_the_whole_suite_unnarrowed` stops
    seeing a narrowed hook - and the whole suite stayed green. A floor spelled as
    `set(NARROWING_FLAGS) == {...}` would have caught the emptying, but not a tuple that
    still had four entries and had stopped matching anything; this drives the predicate
    itself over tokens written from **pytest's own selector syntax**, in both directions.

    The jammed forms (`-knot_matrix`, `-mslow`) are the M525/M526 shape: valid pytest,
    carrying neither `"-k "` nor `"-m "` as a substring.
    """
    narrowed = ["-knot_matrix", "-mslow", "--ignore=tests/test_faults_matrix.py", "--deselect"]
    for token in narrowed:
        assert token.startswith(NARROWING_FLAGS), (
            f"{token!r} narrows a pytest run and the guard cannot see it"
        )

    # The other direction: a check that flagged everything would be no check either.
    for token in ["-q", "--tb=short", "tests/test_net.py", "pytest"]:
        assert not token.startswith(NARROWING_FLAGS), (
            f"{token!r} was flagged and narrows nothing"
        )


# The controls in this file that no future task may delete quietly, each with the reason it
def test_the_faces_job_exists_installs_the_extra_and_runs_the_marked_tests():
    """A2: an optional extra must be exercised somewhere that gates a release.

    Three separate claims, because two of them are satisfiable without the third: the job
    exists; it installs `.[faces]` (a job that installed only `.[dev]` would run and skip
    everything, reporting green over nothing); and it really runs the marked tests.
    """
    commands = _lanes()[FACES_JOB]
    assert any('pip install -e ".[dev,faces]"' == command for command in commands), commands
    pytest_runs = [command for command in commands if command.split()[:1] == ["pytest"]]
    assert pytest_runs, f"the {FACES_JOB} job runs no pytest at all: {commands}"
    assert any("-m faces" in command for command in pytest_runs), pytest_runs


def test_the_faces_job_is_not_allowed_to_fail():
    """`continue-on-error` would make it a report rather than a gate.

    This is the only place in the project where the real ONNX stack is exercised at all -
    the whole of `-m faces` skips everywhere else - so a job permitted to fail would leave
    A5's provider pin, the golden-parity fixture and the pack's hashes measured nowhere.
    Read from the parsed document, not from the text, because `continue-on-error` can be
    set on the job OR on any step and both make the failure advisory.
    """
    import yaml

    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = document["jobs"][FACES_JOB]
    assert job.get("continue-on-error") in (None, False), job.get("continue-on-error")
    for step in job["steps"]:
        assert step.get("continue-on-error") in (None, False), step


def test_the_faces_job_fetches_the_same_pinned_pack_the_product_expects():
    """The pack is PINNED, and pinned in two files that must agree.

    The workflow fetches and hashes; `pam/enrich/faces_model.py` holds the constants the
    product's own `describe` checks against. Comparing the two is the point: a milestone
    that repointed the product at a new pack and left the workflow fetching the old one
    would have the faces job validating bytes nothing uses, with every parity number still
    green because the fixture and the workflow agree with each other.

    The expectation is the PRODUCT's constant, not a third literal here, deliberately: a
    third copy of a hash is a third thing to update and adds no independent reading.
    """
    from pam.enrich import faces_model

    block = _job_block(FACES_JOB)
    for name, sha in (
        (faces_model.DETECTOR_FILE, faces_model.DETECTOR_SHA256),
        (faces_model.RECOGNIZER_FILE, faces_model.RECOGNIZER_SHA256),
    ):
        assert name in block, f"the faces job does not fetch {name}"
        assert sha in block, f"the faces job does not verify {name}'s pinned SHA-256"
    assert sorted(PINNED_PACK_FILES) == sorted(
        (faces_model.DETECTOR_FILE, faces_model.RECOGNIZER_FILE)
    ), "this module's pinned-file list and the product's have drifted apart"
    # It must fetch from the mirrors that serve real ONNX. `raw.githubusercontent` returns
    # a ~130-byte git-LFS pointer for both files: it downloads successfully, has the right
    # name and contains no model, and the hash check below is what would catch it.
    assert "raw.githubusercontent" not in block
    assert "huggingface.co" in block


def test_the_faces_job_never_asks_pam_to_download_anything():
    """A1, asserted against the one file that could quietly contradict it.

    There is no code in pam that can fetch a model, so the pack has to be fetched by the
    job itself - and the risk that creates is the opposite one: a convenience step that
    made pam do it would be invisible to every test about `pam/`, because it would live
    here. What pam is asked to do in this job is write a manifest for files that are
    already on disk.
    """
    commands = _lanes()[FACES_JOB]
    pam_commands = [command for command in commands if command.split()[:1] == ["pam"]]
    assert pam_commands == ["pam faces manifest pack"], pam_commands


def test_the_faces_job_REQUIRES_the_stack_rather_than_skipping_it():
    """The M11 review's R3: "nothing here may skip" was a step NAME and nothing enforced it.

    Measured before this: with a real SHA-verified pack and the faces stack unavailable,
    the job's exact command printed `10 skipped, 3735 deselected` and exited 0. Read from
    the PARSED document (M6's lesson), and the variable is spelled here by hand AND compared
    with the name `tests/conftest.py` reads, so the workflow and the hook cannot drift apart.
    """
    import yaml
    from faces_stack import REQUIRED_VAR

    assert REQUIRED_VAR == "PAM_FACES_REQUIRED"
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = [
        step
        for step in document["jobs"][FACES_JOB]["steps"]
        if "-m faces" in str(step.get("run", ""))
    ]
    assert steps, "the faces job runs no `-m faces` step"
    for step in steps:
        assert (step.get("env") or {}).get("PAM_FACES_REQUIRED") == "1", step


def _faces_collection(required):
    """`pytest -m faces` over one marked module, in a FRESH interpreter, with the pack
    variable REMOVED so the stack is unavailable on every machine - including the faces
    job, where this test runs too if it is ever marked."""
    env = {k: v for k, v in os.environ.items() if k not in ("PAM_FACES_MODEL_DIR",)}
    env.pop("PAM_FACES_REQUIRED", None)
    if required:
        env["PAM_FACES_REQUIRED"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", "faces"]
        + ["tests/test_faces_model.py"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _faces_steps():
    """The faces job's steps, from the REAL parser (design 9.4 rev 11)."""
    import yaml

    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return document["jobs"][FACES_JOB]["steps"]


def test_the_faces_job_installs_exiftool_and_gates_on_the_hand_listed_tests_PASSING():
    """M12 Task 6 Step 3 (design 9.4 rev 27, P5): the faces job, completed.

    Two things the M12 gate found missing (I2, B2), asserted on the PARSED workflow. The job
    installed no exiftool, so the canary's faces stage - which drives a real `pam scan` - could
    not run there; and it had no floor, although `m11.md` C3 specified one. The floor is three
    parts and each is asserted: pytest writes a JUnit report, a LATER step hands that report to
    `tools/ci_required_passed.py`, and the list it names is the hand-written one in this file.
    """
    steps = _faces_steps()
    runs = [str(step.get("run", "")).strip() for step in steps]

    exiftool = [
        i for i, run in enumerate(runs) if run == "choco install exiftool -y --no-progress"
    ]
    assert len(exiftool) == 1, f"the faces job installs exiftool {len(exiftool)} times: {runs}"

    marked = [i for i, run in enumerate(runs) if run.startswith("pytest") and "-m faces" in run]
    assert len(marked) == 1, f"expected one `pytest -m faces` step, found {len(marked)}"
    assert exiftool[0] < marked[0], "exiftool must be installed before the faces tests run"
    assert "--junitxml=faces.xml" in runs[marked[0]].split(), runs[marked[0]]

    floor = [i for i, run in enumerate(runs) if REQUIRED_PASSED_SCRIPT in run]
    assert len(floor) == 1, f"expected one {REQUIRED_PASSED_SCRIPT} step, found {len(floor)}"
    assert floor[0] > marked[0], "the floor must read the report AFTER pytest has written it"
    assert runs[floor[0]].split() == [
        "python",
        REQUIRED_PASSED_SCRIPT,
        "faces.xml",
        "FACES_JOB_MUST_PASS",
    ], runs[floor[0]]
    assert (REPO_ROOT / REQUIRED_PASSED_SCRIPT).is_file()


def test_the_faces_jobs_required_list_is_a_literal_and_names_every_marked_test():
    """The list's own floor: literal, non-empty, and equal to the SECOND hand spelling.

    Read through the script's own reader, so what is checked here is what CI will require - a
    list the script would refuse (computed, empty) fails here first. Compared with
    `tests/test_faces_model.py::FACES_MARKED_TESTS`, which is hand-written independently and is
    itself compared with what `-m faces` collects: removing one name from either list is red.
    Never compared with the marker directly - that would be the list computing itself.
    """
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        import ci_required_passed
    finally:
        sys.path.pop(0)
    from test_faces_model import FACES_MARKED_TESTS

    # Read through the script's OWN default source - the one the CI step uses, since it passes
    # no `source=` - never through `Path(__file__)`. M2082: pointing `LIST_SOURCE` at another
    # module left every test green (each passed its source explicitly) and the job refusing.
    assert ci_required_passed.LIST_SOURCE == Path(__file__).resolve()
    required = ci_required_passed.required_ids(
        ci_required_passed.LIST_SOURCE, "FACES_JOB_MUST_PASS"
    )
    assert len(required) == len(set(required)) == len(FACES_JOB_MUST_PASS), required
    assert set(required) == set(FACES_MARKED_TESTS), (
        f"required but not marked: {sorted(set(required) - set(FACES_MARKED_TESTS))};"
        f" marked but not required: {sorted(set(FACES_MARKED_TESTS) - set(required))}"
    )


# A hand-written JUnit report: one testcase that passed, one skipped, one failed, one errored.
# The shape is pytest's own (`classname` is the dotted module path), written out rather than
# generated so this test does not depend on the pytest that runs it.
_THREE_CASE_REPORT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="4">
<testcase classname="tests.test_x" name="test_passed" time="0.1" />
<testcase classname="tests.test_x" name="test_skipped" time="0.0"><skipped message="no pack" /></testcase>
<testcase classname="tests.test_x" name="test_failed" time="0.1"><failure message="boom" /></testcase>
<testcase classname="tests.test_x" name="test_errored" time="0.1"><error message="setup" /></testcase>
</testsuite></testsuites>
"""


def test_the_required_passed_script_counts_ONLY_a_pass(tmp_path):
    """The script's truth table, on a hand-written report (M12 Task 6).

    A floor that accepted a skip would be `PAM_FACES_REQUIRED`'s hole one layer down; one that
    accepted an absence would let a deleted test leave the job green. Driven both as a function
    and as the command CI runs, because the exit code is the only thing CI reads.
    """
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        import ci_required_passed
    finally:
        sys.path.pop(0)

    report = tmp_path / "report.xml"
    report.write_text(_THREE_CASE_REPORT, encoding="utf-8")

    def verdict(node):
        return ci_required_passed.problems(report, (f"tests/test_x.py::{node}",))

    assert verdict("test_passed") == []
    assert verdict("test_skipped") == ["skipped: tests/test_x.py::test_skipped"]
    assert verdict("test_failed") == ["failed: tests/test_x.py::test_failed"]
    assert verdict("test_errored") == ["failed: tests/test_x.py::test_errored"]
    assert verdict("test_absent") == ["missing: tests/test_x.py::test_absent"]

    # The command itself, over a list source written for the occasion.
    source = tmp_path / "lists.py"
    source.write_text(
        "PASSES = ('tests/test_x.py::test_passed',)\n"
        "SKIPS = ('tests/test_x.py::test_passed', 'tests/test_x.py::test_skipped')\n"
        "EMPTY = ()\n"
        "COMPUTED = tuple(sorted(['tests/test_x.py::test_passed']))\n",
        encoding="utf-8",
    )
    code = (
        "import sys, pathlib;"
        f"sys.path.insert(0, {str(REPO_ROOT / 'tools')!r});"
        "import ci_required_passed as c;"
        f"sys.exit(c.main([{str(report)!r}, sys.argv[1]], source=pathlib.Path({str(source)!r})))"
    )

    def run(name):
        return subprocess.run(
            [sys.executable, "-c", code, name], capture_output=True, text=True, timeout=60
        )

    assert run("PASSES").returncode == 0
    skipped = run("SKIPS")
    assert (
        skipped.returncode == 1 and "skipped: tests/test_x.py::test_skipped" in skipped.stdout
    )
    for refused in ("EMPTY", "COMPUTED", "NOT_THERE"):
        result = run(refused)
        assert result.returncode == 2 and "REFUSED" in result.stdout, (refused, result.stdout)


# The matching rule's report: a required name whose only neighbours are LONGER names that merely
# start with it, and a parametrized test with one case skipped. Hand-written, as above.
_MATCHING_REPORT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="6">
<testcase classname="tests.test_x" name="test_y_again" time="0.1" />
<testcase classname="tests.test_x" name="test_yes" time="0.1" />
<testcase classname="tests.test_x" name="test_p[a]" time="0.1" />
<testcase classname="tests.test_x" name="test_p[b]" time="0.0"><skipped message="no pack" /></testcase>
<testcase classname="tests.test_x" name="test_q[a]" time="0.1" />
<testcase classname="tests.test_x" name="test_q[b]" time="0.1" />
</testsuite></testsuites>
"""


def test_the_required_passed_script_matches_a_base_name_and_EVERY_parametrization(tmp_path):
    """The module docstring's matching rule, both halves (M12 Task 6 review, F1 / M2081).

    "Matched by its base name, and every parametrization must pass": a required `test_y` is NOT
    satisfied by a passing `test_y_again` or `test_yes` - a deleted or renamed required test
    must not hide behind a longer-named sibling - and a required `test_p` whose `[b]` case
    skipped did not pass, although its `[a]` case did.
    """
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        import ci_required_passed
    finally:
        sys.path.pop(0)

    report = tmp_path / "report.xml"
    report.write_text(_MATCHING_REPORT, encoding="utf-8")

    def verdict(node):
        return ci_required_passed.problems(report, (f"tests/test_x.py::{node}",))

    assert verdict("test_y") == ["missing: tests/test_x.py::test_y"]
    assert verdict("test_p") == ["skipped: tests/test_x.py::test_p"]
    assert verdict("test_q") == []


def test_a_REQUIRED_faces_stack_that_is_unavailable_fails_instead_of_skipping():
    result = _faces_collection(required=True)
    assert result.returncode != 0, result.stdout[-2000:]
    assert "PAM_FACES_REQUIRED=1 but the faces stack is unavailable" in (
        result.stdout + result.stderr
    ), result.stdout[-2000:]


def test_the_control_WITHOUT_the_requirement_the_same_collection_skips_and_passes():
    """Without this the test above could pass because collection broke for some other
    reason - a negative test passing for the wrong reason."""
    result = _faces_collection(required=False)
    assert result.returncode == 0, result.stdout[-2000:]
    assert "skipped" in result.stdout, result.stdout[-2000:]


# --- the `ocr` job (M12 Task 12 Part B, Step B10; design 9.4 rev 27-28, P5/Q5) -------------------


def _ocr_steps():
    """The ocr job's steps, from the REAL parser (design 9.4 rev 11)."""
    import yaml

    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return document["jobs"][OCR_JOB]["steps"]


def _ocr_runs():
    return [str(step.get("run", "")).strip() for step in _ocr_steps()]


def test_the_ocr_job_exists_installs_both_extras_and_runs_the_marked_tests():
    """Q5: `.[dev,faces,ocr]` TOGETHER - Q1 says the two extras are one pair of packages, and
    this job is where their coexistence is measured - and `pytest -m ocr`."""
    commands = _lanes()[OCR_JOB]
    assert 'pip install -e ".[dev,faces,ocr]"' in commands, commands
    pytest_runs = [command for command in commands if command.split()[:1] == ["pytest"]]
    assert [run for run in pytest_runs if "-m ocr" in run], pytest_runs


def test_the_ocr_job_is_not_allowed_to_fail():
    """The only place the real OCR stack runs; a job allowed to fail would measure nothing."""
    import yaml

    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = document["jobs"][OCR_JOB]
    assert job.get("continue-on-error") in (None, False), job.get("continue-on-error")
    for step in job["steps"]:
        assert step.get("continue-on-error") in (None, False), step
    assert "if" not in job, "a condition on the ocr job would leave some refs ungated"


def test_the_ocr_pack_step_verifies_q1s_three_hashes_from_the_recorded_source():
    """Q5: the wheel's SHA-256, then EXACTLY its two members extracted with `zipfile`, then both
    files' SHA-256 - three hashes, each a LITERAL equal to Q1's (spelled in `OCR_PACK_HASHES`
    from design 1 rev 29), and the two file hashes equal to the product's own constants, so the
    job and `pam` cannot drift onto two different packs."""
    from pam.enrich import ocr_model

    runs = _ocr_runs()
    fetch = [run for run in runs if "pip download" in run]
    assert len(fetch) == 1, f"expected one pack step, found {len(fetch)}"
    step = fetch[0]
    assert (
        "pip download rapidocr-onnxruntime==1.4.4 --no-deps --only-binary=:all: -d wheel"
        in step
    )
    for name, sha in OCR_PACK_HASHES.items():
        assert name in step, f"the pack step does not name {name}"
        assert sha in step, f"the pack step does not verify {name}'s SHA-256 {sha}"
    assert step.count("Get-FileHash") == 2, "the wheel and the loop over the two files"
    assert "import zipfile" in step
    for member in (ocr_model.DETECTOR_FILE, ocr_model.RECOGNIZER_FILE):
        assert member in step
    assert "'rapidocr_onnxruntime/models/' + n" in step, "the members' path inside the wheel"
    assert OCR_PACK_HASHES[ocr_model.DETECTOR_FILE] == ocr_model.DETECTOR_SHA256
    assert OCR_PACK_HASHES[ocr_model.RECOGNIZER_FILE] == ocr_model.RECOGNIZER_SHA256
    assert OCR_PACK_HASHES["rapidocr_onnxruntime-1.4.4-py3-none-any.whl"] in ocr_model.SOURCE


def test_the_ocr_job_installs_nothing_from_the_wheel_and_proves_it():
    """Rev 4.1: no RapidOCR code where pam runs. No `pip install` names it, and a step asserts
    `find_spec("rapidocr_onnxruntime") is None` in the job's own interpreter, before pytest."""
    runs = _ocr_runs()
    installs = [run for run in runs if "pip install" in run]
    assert installs and not [run for run in installs if "rapidocr" in run], installs
    probe = [i for i, run in enumerate(runs) if run == OCR_NO_RAPIDOCR_PROBE]
    assert len(probe) == 1, f"expected one find_spec probe, found {len(probe)}: {runs}"
    marked = [i for i, run in enumerate(runs) if run.startswith("pytest") and "-m ocr" in run]
    assert probe[0] < marked[0]
    assert not [c for c in _lanes()[OCR_JOB] if c.split()[:1] == ["pam"]], (
        "the ocr job asks pam to do something; there is no pack command and no download"
    )


def test_the_ocr_job_REQUIRES_the_stack_and_points_it_at_the_pack_it_verified():
    """R3's shape for the ocr stack: `PAM_OCR_REQUIRED=1` on the `-m ocr` step, and
    `PAM_OCR_MODEL_DIR` naming the folder the pack step extracted into. The two names are
    spelled here by hand AND compared with the ones `tests/ocr_stack.py` reads."""
    import yaml
    from ocr_stack import MODEL_DIR_VAR, REQUIRED_VAR

    assert REQUIRED_VAR == "PAM_OCR_REQUIRED"
    assert MODEL_DIR_VAR == "PAM_OCR_MODEL_DIR"
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = [
        step
        for step in document["jobs"][OCR_JOB]["steps"]
        if "-m ocr" in str(step.get("run", ""))
    ]
    assert len(steps) == 1, steps
    env = steps[0].get("env") or {}
    assert env.get("PAM_OCR_REQUIRED") == "1", env
    assert env.get("PAM_OCR_MODEL_DIR") == "ocrpack", env
    assert "-Path ocrpack" in "\n".join(_ocr_runs()), "the pack is not extracted to ocrpack/"


def test_the_ocr_job_installs_exiftool_and_gates_on_the_hand_listed_tests_PASSING():
    """The faces job's floor, for the ocr job: exiftool before the tests, the pack before the
    tests, a JUnit report, and a LATER step requiring every name in `OCR_JOB_MUST_PASS`."""
    runs = _ocr_runs()
    exiftool = [
        i for i, run in enumerate(runs) if run == "choco install exiftool -y --no-progress"
    ]
    assert len(exiftool) == 1, f"the ocr job installs exiftool {len(exiftool)} times: {runs}"
    pack = [i for i, run in enumerate(runs) if "pip download" in run]
    marked = [i for i, run in enumerate(runs) if run.startswith("pytest") and "-m ocr" in run]
    assert len(marked) == 1, f"expected one `pytest -m ocr` step, found {len(marked)}"
    assert exiftool[0] < marked[0] and pack[0] < marked[0]
    assert "--junitxml=ocr.xml" in runs[marked[0]].split(), runs[marked[0]]
    floor = [i for i, run in enumerate(runs) if REQUIRED_PASSED_SCRIPT in run]
    assert len(floor) == 1, f"expected one {REQUIRED_PASSED_SCRIPT} step, found {len(floor)}"
    assert floor[0] > marked[0], "the floor must read the report AFTER pytest has written it"
    assert runs[floor[0]].split() == [
        "python",
        REQUIRED_PASSED_SCRIPT,
        "ocr.xml",
        "OCR_JOB_MUST_PASS",
    ], runs[floor[0]]


def test_the_ocr_jobs_required_list_is_a_literal_and_names_every_marked_test():
    """Read through the floor script's own reader, and compared with the SECOND hand spelling,
    `tests/test_ocr_model.py::OCR_MARKED_TESTS` - itself compared with what `-m ocr` collects."""
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    try:
        import ci_required_passed
    finally:
        sys.path.pop(0)
    from test_ocr_model import OCR_MARKED_TESTS

    required = ci_required_passed.required_ids(
        ci_required_passed.LIST_SOURCE, "OCR_JOB_MUST_PASS"
    )
    assert len(required) == len(set(required)) == len(OCR_JOB_MUST_PASS), required
    assert set(required) == set(OCR_MARKED_TESTS), (
        f"required but not marked: {sorted(set(required) - set(OCR_MARKED_TESTS))};"
        f" marked but not required: {sorted(set(OCR_MARKED_TESTS) - set(required))}"
    )


def _ocr_collection(required):
    """`pytest -m ocr` over one marked module, in a FRESH interpreter, with the pack variable
    REMOVED so the ocr stack is unavailable on every machine, the ocr job's included."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("PAM_OCR_MODEL_DIR", "PAM_OCR_REQUIRED", "PAM_FACES_REQUIRED")
    }
    if required:
        env["PAM_OCR_REQUIRED"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", "ocr"]
        + ["tests/test_ocr_model.py"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_a_REQUIRED_ocr_stack_that_is_unavailable_fails_instead_of_skipping():
    result = _ocr_collection(required=True)
    assert result.returncode == 4, result.stdout[-2000:]
    assert "PAM_OCR_REQUIRED=1 but the ocr stack is unavailable" in (
        result.stdout + result.stderr
    ), result.stdout[-2000:]


def test_the_control_WITHOUT_the_ocr_requirement_the_same_collection_skips_and_passes():
    """The negative control: without it the test above could pass on a collection that broke
    for some other reason."""
    result = _ocr_collection(required=False)
    assert result.returncode == 0, result.stdout[-2000:]
    assert "skipped" in result.stdout, result.stdout[-2000:]


# is kept. `tests/test_net_canary.py`'s `LOAD_BEARING_CONTROLS` is the precedent, and M1670
# is why it is copied here rather than admired there: that floor was file-local, so the
# lesson - an instrument's own coverage is owned by nobody - simply moved to the next
# guarded file. M1683 measured it: deleting the pre-commit hook test outright left 2971
# passed, 1 skipped and nothing red. The plan sentence ordering it kept was the only thing
# standing behind it.
LOAD_BEARING_CONTROLS = {
    # M11 Task 7, registered in the commit that adds them (M10's C4).
    "test_the_faces_job_exists_installs_the_extra_and_runs_the_marked_tests": (
        "A2: the optional extra is exercised in a job that gates a release"
    ),
    "test_the_faces_job_is_not_allowed_to_fail": (
        "the only place the real ONNX stack runs; advisory would measure nothing"
    ),
    "test_the_faces_job_fetches_the_same_pinned_pack_the_product_expects": (
        "the workflow's hashes and the product's constants, compared"
    ),
    "test_the_faces_job_never_asks_pam_to_download_anything": (
        "A1 asserted where a convenience step could contradict it invisibly"
    ),
    # The M11 review's R3, registered in the commit that adds them.
    "test_the_faces_job_REQUIRES_the_stack_rather_than_skipping_it": (
        "the job's `nothing may skip` made true: an unavailable stack is not ten green skips"
    ),
    "test_a_REQUIRED_faces_stack_that_is_unavailable_fails_instead_of_skipping": (
        "the hook itself, driven in a fresh interpreter"
    ),
    "test_the_control_WITHOUT_the_requirement_the_same_collection_skips_and_passes": (
        "the negative control; without it the test above could pass on a broken collection"
    ),
    "test_the_lane_check_can_tell_an_enumerated_lane_from_an_excluding_one": (
        "the negative control under the new-file check; without it that check proves nothing"
    ),
    "test_the_yaml_check_can_see_the_defect_it_exists_for": (
        "the shipped M6 defect replayed - proof the YAML guard can fail"
    ),
    "test_the_hand_rolled_reader_agrees_with_a_real_parser_about_every_command": (
        "the small reader's oracle; F1 was a file that did not parse at all"
    ),
    "test_the_narrowing_check_can_see_a_command_that_really_is_narrowed": (
        "the floor under NARROWING_FLAGS (M1680)"
    ),
    "test_the_lane_is_unconditional_and_runs_on_pushes_and_pull_requests": (
        "M506, inverted by the retirement: one lane with an `if:` means PRs get no CI"
    ),
    # M12 Task 6 (P5, m11.md C3's floor), registered in the commit that adds them.
    "test_the_faces_job_installs_exiftool_and_gates_on_the_hand_listed_tests_PASSING": (
        "the faces job's exiftool and its PASSED floor, on the parsed workflow"
    ),
    "test_the_faces_jobs_required_list_is_a_literal_and_names_every_marked_test": (
        "the required list's own floor: literal, non-empty, equal to the second hand spelling"
    ),
    "test_the_required_passed_script_counts_ONLY_a_pass": (
        "the floor script's truth table - a skip, a failure or an absence never satisfies it"
    ),
    "test_the_required_passed_script_matches_a_base_name_and_EVERY_parametrization": (
        "the floor's matching rule - no prefix sibling satisfies a name, every case must pass"
    ),
    # M12 Task 12 Part B (Step B10, Q5), registered in the commit that adds them.
    "test_the_ocr_job_exists_installs_both_extras_and_runs_the_marked_tests": (
        "Q5: [dev,faces,ocr] together, and `-m ocr` really runs"
    ),
    "test_the_ocr_job_is_not_allowed_to_fail": (
        "the only place the real OCR stack runs; advisory or conditional would gate nothing"
    ),
    "test_the_ocr_pack_step_verifies_q1s_three_hashes_from_the_recorded_source": (
        "the wheel's and both members' SHA-256, literals from Q1, equal to the product's pins"
    ),
    "test_the_ocr_job_installs_nothing_from_the_wheel_and_proves_it": (
        "rev 4.1: no RapidOCR code where pam runs, asserted by find_spec in the job"
    ),
    "test_the_ocr_job_REQUIRES_the_stack_and_points_it_at_the_pack_it_verified": (
        "R3 for ocr: PAM_OCR_REQUIRED=1 and PAM_OCR_MODEL_DIR at the verified folder"
    ),
    "test_the_ocr_job_installs_exiftool_and_gates_on_the_hand_listed_tests_PASSING": (
        "the ocr job's PASSED floor, after pytest, over the hand-written list"
    ),
    "test_the_ocr_jobs_required_list_is_a_literal_and_names_every_marked_test": (
        "OCR_JOB_MUST_PASS against OCR_MARKED_TESTS, the second hand spelling"
    ),
    "test_a_REQUIRED_ocr_stack_that_is_unavailable_fails_instead_of_skipping": (
        "the hook's ocr row itself, in a fresh interpreter: exit 4, never a skip"
    ),
    "test_the_control_WITHOUT_the_ocr_requirement_the_same_collection_skips_and_passes": (
        "the negative control under the test above"
    ),
}

assert LOAD_BEARING_CONTROLS, "this module's control list is empty; it protects nothing"


def test_no_control_in_this_file_can_be_deleted_without_saying_so():
    """The floor under this module's own inventory (M1683, on M1670's precedent).

    Removing one of these is a deliberate edit to `LOAD_BEARING_CONTROLS`, with the reason
    it was kept for written beside it - not a quiet deletion during a refactor of the thing
    that framed it. **When this file gains a control, its registration lands in the same
    commit** (CLAUDE.md, M10 close).
    """
    present = {name for name in globals() if name.startswith("test_")}
    missing = sorted(set(LOAD_BEARING_CONTROLS) - present)
    assert missing == [], (
        f"a load-bearing control has been deleted from {Path(__file__).name}: {missing}."
        f" Each is why another assertion here means anything"
        f" ({[LOAD_BEARING_CONTROLS[m] for m in missing]})."
    )
