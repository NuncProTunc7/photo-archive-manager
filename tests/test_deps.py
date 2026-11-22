"""The runtime dependency closure, as a declared allowlist (M9 Task 6, design 1).

WHAT THIS FILE IS FOR. From M1 to M8 the runtime closure was exactly `{click, pam}`, and
that was checked by hand at each milestone gate: import every module under `pam.`, look
at `sys.modules`, count the non-stdlib top-level names. It was never a test. M9 spends
the closure - `Pillow`, `pillow-heif`, `imagehash`, `reverse_geocoder` - and the point of
the task that added them is that the spending is **a decision with a test around it**,
not a side effect of somebody's `import`.

FOUR SETS, EACH SPELLED OUT AS LITERALS, EACH ASSERTED IN BOTH DIRECTIONS.

  1. `RUNTIME_DEPENDENCIES` - what `pyproject.toml` may declare. Written from design 1's
     stack table by hand.
  2. `TRANSITIVE_DEPENDENCIES` - what those five drag in behind them. `reverse_geocoder`
     brings `scipy`; `imagehash` brings `numpy` and `PyWavelets`; `click` has brought
     `colorama` on Windows since M1 and nobody ever wrote it down. Weight that nothing
     names is weight nobody re-examines.
  3. `EAGER_IMPORT_CLOSURE` - what is actually resident in the interpreter after
     importing all of `pam.`. This is the eight-milestone instrument, finally automated.
  4. `OPTIONAL_DEPENDENCIES` - the `[faces]` extra (M11, A2). AN OPTIONAL EXTRA NEVER
     ENTERS `RUNTIME_DEPENDENCIES`, `TRANSITIVE_DEPENDENCIES` OR `EAGER_IMPORT_CLOSURE`,
     and the three assertions that say so are the point of the fourth set rather than a
     side effect of it. `pip install pam` installs none of it; the extra is what a user
     who wants face detection asks for by name. **A faces import appearing in the eager
     closure is a module-scope import somebody added, not a policy change** - the fix is
     to move that import inside a function, never to widen the closure, and
     `tests/test_faces_model.py` names the offending FILE, which this test cannot.

     ITS TRANSITIVE ARRIVALS ARE DECLARED TOO - `protobuf`, `flatbuffers`, `packaging`,
     which `onnxruntime` brings. Same rule as the runtime closure's: weight that nothing
     names is weight nobody re-examines. They are kept in their own set rather than mixed
     into `TRANSITIVE_DEPENDENCIES`, because that set is what `pip install pam` really
     drags in and these three are not in it.

WHY BOTH DIRECTIONS, EVERYWHERE. An `<=` assertion would let a sixth dependency arrive
unannounced, which is the failure this file exists to prevent, and it must be a red suite
rather than a diff nobody reads. A `>=` assertion would let one be silently dropped. The
plan's sentence is that an unplanned dependency must fail the suite as loudly as a
missing one, and only equality does that.

M10 ADDS NO PACKAGE, AND THIS SENTENCE USED TO PREDICT THAT IT WOULD. Through M9 the
paragraph above read "M10 adds an Ollama client and it must be a red suite" - which was a
reasonable guess and is wrong, and leaving a prediction of an event that does not happen
is how a reader concludes the list is behind rather than deliberate. THE HONEST ANSWER,
recorded here because it is the constraint M10 most expects to be tested (M10 plan,
clarification 1):

  * `pam/net.py`'s `LoopbackHttpClient` has been the Ollama client since M6. It is
    stdlib, already guarded, and design 10 has named it `enrich/caption.py`'s ONLY client
    since it was written. M10 gives it its first callers - `pam doctor`'s probe and
    `pam models pull` in Task 7, the caption enricher in Task 8 - and no new code that
    can open a socket.
  * The `ollama` pip package would be BOTH a sixth runtime dependency AND a second HTTP
    client, and design 10 permits exactly one. It is not a convenience with a cost; it is
    two rules at once.
  * Ollama itself is an EXTERNAL SERVICE on the exiftool precedent: user-installed,
    sanctioned by design 1's stack table since rev 2, and checked by `pam doctor` (design
    12 rev 17, A10). An external service is not a dependency of this closure and never
    appears in the three sets below.

So `RUNTIME_DEPENDENCIES` stays at FIVE names through M10, `EAGER_IMPORT_CLOSURE` stays
`{click, pam}`, and `tests/guards.py`'s `ALLOWED_NETWORK_IMPORTS` gained nothing for them (it lists the sanctioned
imports of `net.py`, `webui/server.py` and, from M15 Task 12, `update/fetch.py`).
A milestone that finds itself widening any of the three has found something the plan did
not, and the executor rule says to stop and report rather than to edit the list.

WHY THE EXPECTATIONS ARE LITERALS AND NEVER READ BACK FROM THE THING THEY CHECK. This
project's single most repeated lesson: `repo.ENRICHERS` (four of five enrichers could be
deleted from `pam status` with 1238 tests green), `repo.open_plan`, M8's partition
oracles. A test that asserts "the allowlist matches whatever the environment produced"
passes for every value of the environment. Nothing below reads `pyproject.toml`,
`importlib.metadata` or `sys.modules` to build its expectation - they are all typed out
above the tests, from the spec, and the machinery under test is what gets compared
against them.

WHY DIRECT AND TRANSITIVE ARE SEPARATE SETS RATHER THAN ONE FLAT LIST. Design 1's stack
table enumerates *choices*, and a choice is a direct dependency. A transitive arrival is
a consequence of a choice, and confusing the two would let `scipy` look like something
pam picked. Keeping them apart also keeps the reason legible: `numpy` is not here because
anybody wanted an array library, it is here because `imagehash` and `reverse_geocoder`
both need one.
"""

import re
import subprocess
import sys
import tomllib
from importlib import metadata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"

# --- 1. THE DECLARED ALLOWLIST -------------------------------------------------------
#
# Transcribed from design 1's stack table, by hand, with the reason each was chosen. An
# entry is earned by the milestone that consumes it - the same convention spec 10 uses
# for `ALLOWED_NETWORK_IMPORTS`, where "pre-blessing two modules nobody imports is the
# opposite of what the allowlist was adopted for".
RUNTIME_DEPENDENCIES = {
    # M1. "Boring, ubiquitous."
    "click": "the CLI",
    # M9+ only, and design 1 says so in the cell itself: nothing before M9 decodes an
    # image. M7's review UI extracts the preview the camera embedded (8.2).
    "pillow": "image decode, for the phash enricher (M9 Task 8)",
    # HEIC is the format most phone photographs are in. Pillow alone cannot open one.
    "pillow-heif": "HEIC decode, which Pillow alone cannot do",
    # 8.1: "phash: dHash".
    "imagehash": "dHash for the phash enricher (M9 Task 8)",
    # Design 1's rationale cell is one word: "No network." The dataset ships in the
    # wheel. `tests/test_net_canary.py` proves that behaviourally.
    "reverse-geocoder": "offline place names for the geocode enricher (M9 Task 9)",
}

# What the five above pull in behind them. NOT a wish list - each of these really is
# installed by `pip install pam`, and naming them is how the weight stays visible.
#
# `colorama` WAS the oldest entry here by four milestones - `click` required it on Windows
# from M1 - and it is GONE as of the M11 close, because **click 8.5.0 dropped that
# dependency**. It left the way an entry should: hosted CI went red on 8.5.0 while this
# machine was green on 8.4.2, this test named both halves of the difference, and
# `pyproject.toml` now floors click at 8.5 so the footprint is one set again rather than a
# property of whichever click a machine happens to have. The history is kept here because
# the "closure is `{click, pam}`" sentence was always about what pam IMPORTS, never about
# what pip installs, and that distinction is the reason this file exists.
TRANSITIVE_DEPENDENCIES = {
    "numpy": "imagehash and reverse_geocoder both need arrays",
    "scipy": "reverse_geocoder's cKDTree; imagehash's non-dHash hashes",
    "pywavelets": "imagehash's wavelet hash - pam uses dHash, but the dependency is declared",
}

# --- 2b. THE OPTIONAL EXTRA (M11 Task 7, A2) -----------------------------------------
#
# Transcribed from amendment A2, by hand, like every other set in this file. MEASURED in a
# throwaway venv on Python 3.14.2 / AMD64 / Windows 11, WHEELS ONLY, NO COMPILER:
# onnxruntime 1.29.0, numpy 2.5.2.
OPTIONAL_DEPENDENCIES = {
    "faces": {
        # A1: raw ONNX, in-process, no server, no model download. The wheel is ~14 MB and
        # most of this product has no use for it, which is what makes it an extra rather
        # than a sixth runtime dependency.
        "onnxruntime": "runs YuNet and SFace in-process (M11 Task 7)",
        # NOT the same claim as the `numpy` in TRANSITIVE_DEPENDENCIES, and the distinction
        # is the whole reason the sets are separate: `imagehash` and `reverse_geocoder`
        # drag numpy in for every install, while the faces stack DECLARES it, because a
        # pipeline that decodes twelve tensors by hand cannot be written without it and
        # must not rest on another package's transitive gift.
        "numpy": "the detector's decode arithmetic (M11 Task 7)",
    },
    # M12 Task 12 Part B (design 1 rev 28, Q1): EXACTLY the [faces] pair - there is no OCR
    # library. pam's own PP-OCRv4 pipeline runs over the same two wheels.
    "ocr": {
        "onnxruntime": "runs the PP-OCRv4 detector and recogniser in-process (M12 Task 12)",
        "numpy": "pam's own OCR pipeline, P-1 to P-11 (M12 Task 12)",
    },
}

# What `pip install "photo-archive-manager[faces]"` drags in behind those two. Measured, not guessed.
OPTIONAL_TRANSITIVE_DEPENDENCIES = {
    "protobuf": "onnxruntime reads the .onnx protobuf",
    "flatbuffers": "onnxruntime's serialized format",
    "packaging": "onnxruntime's version handling",
}


def test_pyproject_declares_exactly_the_sanctioned_optional_extras():
    """A2, in both directions, over `[project.optional-dependencies]`.

    BOTH directions, and the second one is the one worth having: an extra that quietly
    grew a third package would satisfy a `<=` check, and the whole argument that the faces
    stack is two wheels and no server rests on the list being what it says it is.
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    extras = set(data["project"]["optional-dependencies"]) - {"dev"}
    assert extras == set(OPTIONAL_DEPENDENCIES), (
        f"pyproject.toml declares extras {sorted(extras)}; A2 sanctions"
        f" {sorted(OPTIONAL_DEPENDENCIES)}. A new extra needs a spec amendment first."
    )
    for extra, expected in OPTIONAL_DEPENDENCIES.items():
        declared = _declared(extra)
        assert declared == set(expected), (
            f"the [{extra}] extra and A2 disagree."
            f" Only in pyproject.toml: {sorted(declared - set(expected))}."
            f" Only in the spec-written allowlist: {sorted(set(expected) - declared)}."
        )


def test_the_ocr_extra_is_exactly_q1s_two_requirements_and_equal_to_faces():
    """Q1 (design 1 rev 28): `[ocr]` = `onnxruntime>=1.29,<2` and `numpy>=2.0,<3`, "the `[faces]`
    extra's two packages at its ranges".

    Two claims, asserted separately. The LITERAL is spelled here from Q1 and never read from
    `[faces]` - a copy of `[faces]` would agree with `[faces]` whatever either said. And the two
    extras are EQUAL, because Q1 says both: an `[ocr]` that drifted from `[faces]` would admit a
    package, or a range, the face stack never measured.
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    assert extras["ocr"] == ["onnxruntime>=1.29,<2", "numpy>=2.0,<3"]
    assert extras["ocr"] == extras["faces"]


def test_the_optional_extras_are_pinned_to_a_tested_range_and_not_a_bare_floor():
    """Spec 9.4 / A2: `[faces]` is pinned to a TESTED RANGE.

    A bare `>=` is a claim about every future release of a model runtime, which is the one
    kind of dependency where a major version really can change what a graph computes. The
    upper bound is the half doing the work here; the floors and their reasons are in
    `pyproject.toml`'s own block.
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    for extra in OPTIONAL_DEPENDENCIES:
        for requirement in data["project"]["optional-dependencies"][extra]:
            assert "<" in requirement, (
                f"[{extra}] declares {requirement!r} with no upper bound; A2 requires a"
                " tested range, and the version that will break this is the next major"
            )


def test_an_optional_extra_never_enters_the_runtime_closure():
    """THE FOURTH SET'S REASON FOR EXISTING, and it is three separate claims.

    `pip install pam` must install none of the extra: not through `[project] dependencies`
    (so no wheel arrives), not through the transitive walk (so no dependency of a runtime
    dependency drags one in under another name), and not through the eager import closure
    (so no module under `pam/` imports one at module scope and breaks `pam --version` on a
    machine that installed pam and not `pam[faces]`).

    The third is the one that would be met by an ACCIDENT rather than by a decision, which
    is why the docstring at the top of this file says what to do about it: move the import
    inside a function. Widening `EAGER_IMPORT_CLOSURE` to make this green would turn an
    optional extra into a mandatory one without anybody writing that down.
    """
    optional = {_normalise(name) for names in OPTIONAL_DEPENDENCIES.values() for name in names}
    runtime = {_normalise(name) for name in RUNTIME_DEPENDENCIES}
    assert optional & runtime == set(), (
        f"declared both as an optional extra and as a runtime dependency: {optional & runtime}"
    )
    assert optional & set(_declared("runtime")) == set()
    assert optional & {_normalise(n) for n in EAGER_IMPORT_CLOSURE} == set()
    # `numpy` IS in TRANSITIVE_DEPENDENCIES - imagehash and reverse_geocoder both need it -
    # so the transitive claim is deliberately about ONNXRUNTIME's own arrivals, which are
    # what an ordinary `pip install pam` must not receive.
    installed_by_pip_install_pam = _metadata_closure(set(RUNTIME_DEPENDENCIES))
    for name in ("onnxruntime", *OPTIONAL_TRANSITIVE_DEPENDENCIES):
        assert _normalise(name) not in installed_by_pip_install_pam, (
            f"{name} arrives with a plain `pip install pam`; the [faces] extra is not"
            " optional any more"
        )


def test_the_optional_extras_transitive_footprint_is_written_down_if_it_is_installed():
    """`protobuf`, `flatbuffers` and `packaging`, named because nothing else names them.

    IT DOES NOT SKIP WHEN THE EXTRA IS ABSENT, deliberately: on the push lane there is no
    onnxruntime to walk, and a skip here would be a third skip count to reconcile against
    A2's two. It is the faces job that has the packages, and it is the faces job this
    assertion is for.
    """
    try:
        metadata.version("onnxruntime")
    except metadata.PackageNotFoundError:
        return
    found = _metadata_closure({"onnxruntime"}) - {"onnxruntime"}
    expected = set(OPTIONAL_TRANSITIVE_DEPENDENCIES) | {"numpy"}
    assert found == expected, (
        "the [faces] extra's transitive footprint changed."
        f" Arrived without being written down: {sorted(found - expected)}."
        f" Written down but no longer required: {sorted(expected - found)}."
    )


# --- 2a. THE THIRD ARTEFACT --------------------------------------------------------
#
# WHY A THIRD LIST EXISTS AT ALL. `RUNTIME_DEPENDENCIES` and `pyproject.toml` are compared
# only with each other, and the M9 Task 6 review measured what that is worth: an identical
# co-edit removing `pillow-heif` from BOTH left 2309 tests green (M1162), and the same edit
# removing `reverse_geocoder` - the dependency this milestone's whole S4 argument is about -
# left 2309 tests green (M1163). That is M8's `M922/M924` lesson exactly: two artefacts that
# pin each other prove they AGREE, never that either is RIGHT.
#
# Three of the five ARE caught, and the review's coverage map says why it is an accident
# rather than a design: `click`, `Pillow` and `imagehash` are each REQUIRED BY something
# else in the set, so dropping one orphans a name in `TRANSITIVE_DEPENDENCIES`. The two
# leaves - `pillow-heif` and `reverse_geocoder` - are required by nothing and were caught by
# nothing. Those are precisely the two M9 spent the closure FOR: HEIC is most of a phone
# library, and the geocoder is Task 9.
#
# So the comparison below is against `pam/doctor.py`'s OWN list, which is a third artefact
# in a third file, and which is itself pinned by `tests/test_doctor.py`'s
# `SPEC_RUNTIME_DEPENDENCIES` - an independent literal transcribed from design 1's stack
# table. Removing a dependency invisibly now takes four coordinated edits across three
# files instead of two in two.
#
# It closes the drift in the other direction too, which nothing covered either: a fifth
# dependency added here and to `pyproject.toml` but NOT to `pam doctor` would ship with
# Task 6 Step 4's promise silently unmet for it - "a missing `pillow-heif` must be a legible
# line, not an ImportError deep in a run".

# `click` is the one deliberate exemption and it is written down rather than filtered out
# silently: `pam doctor` is itself a click command, so a `click` that is missing does not
# reach a check - the interpreter fails before `main()` runs. Every other runtime
# dependency is optional to STARTING pam and mandatory to FINISHING an enrichment, which is
# exactly the population `pam doctor` is for.
DOCTOR_EXEMPT = {"click"}


def test_pam_doctor_checks_for_every_declared_runtime_dependency_but_click():
    """The third artefact, in both directions. Closes the M1162/M1163 survivors.

    Compared against `_declared("runtime")` and NOT against `RUNTIME_DEPENDENCIES`,
    deliberately: comparing it with the literal would add a fourth reading of the same
    sentence without adding an independent one, and the co-mutation this closes moves the
    literal and the document together.
    """
    from pam.doctor import _RUNTIME_DEPENDENCIES

    checked = {
        _normalise(distribution) for distribution, _module, _purpose in _RUNTIME_DEPENDENCIES
    }
    declared = _declared("runtime")
    assert checked | DOCTOR_EXEMPT == declared, (
        "`pam doctor` and `pyproject.toml` disagree about the runtime dependencies."
        f" Declared but not checked by doctor: {sorted(declared - checked - DOCTOR_EXEMPT)}."
        f" Checked by doctor but not declared: {sorted(checked - declared)}."
        " Adding a runtime dependency means: the spec amendment, the pyproject line,"
        " RUNTIME_DEPENDENCIES here, `_RUNTIME_DEPENDENCIES` in pam/doctor.py, and"
        " SPEC_RUNTIME_DEPENDENCIES in tests/test_doctor.py."
    )


def test_the_import_name_doctor_checks_really_resolves_to_the_distribution_it_names():
    """`_RUNTIME_DEPENDENCIES` carries a (distribution, import name) PAIR, and the test
    above only reads the first half. A pair whose second half is wrong - `pillow-heif`
    mapped to `pillow_heic`, say - makes `pam doctor` report a correctly installed machine
    as broken, and the halves are checked in different places on purpose."""
    from pam.doctor import _RUNTIME_DEPENDENCIES

    owners = metadata.packages_distributions()
    wrong = [
        (distribution, module)
        for distribution, module, _purpose in _RUNTIME_DEPENDENCIES
        if _normalise(distribution) not in {_normalise(d) for d in owners.get(module, [])}
    ]
    assert wrong == [], (
        "pam/doctor.py maps a distribution to an import name that does not belong to it:"
        f" {wrong}. `importlib.metadata.packages_distributions()` is the oracle - it is"
        " built from the installed metadata and knows nothing about doctor's tuple."
    )


# --- 3. THE EAGER IMPORT CLOSURE -----------------------------------------------------
#
# The non-stdlib top-level modules resident after importing EVERY module under `pam.`.
#
# STILL `{click, pam}` AFTER M9 TASK 8 SHIPPED THE FIRST ENRICHER, AND THAT IS THE SIGNED
# DECISION THIS COMMENT USED TO DEMAND, not an oversight. `pam/enrich/phash.py` imports
# `PIL.Image`, `pillow_heif` and `imagehash` inside functions rather than at module scope.
# Measured on this machine at Task 8, best of seven: `pam --version` costs 0.181 s and
# importing those three adds 0.066 s - so a module-scope import would have put a ~36 % tax
# on every command in the product, including `pam doctor`, `pam status` and shell
# completion, to serve the one command that decodes anything. `tests/test_phash.py` re-proves the deferral
# for that module by itself, because this test would report the regression as "the package
# got heavier" without naming the file that did it.
#
# STILL `{click, pam}` AFTER TASK 9 SHIPPED THE GEOCODER TOO, and the number is why.
# `pam/enrich/geocode.py` imports `reverse_geocoder` inside functions. Measured at Task 9
# on this machine, best of nine: a bare interpreter is 0.053 s, `import pam.cli` is
# 0.231 s, adding `import pam.enrich.geocode` costs 0.002 s - and adding
# `import reverse_geocoder` costs 0.337 s, a 145 % tax on EVERY pam command, because it
# pulls `scipy` eagerly. Its 7.8 MB dataset is separately lazy (~0.28 s on the first
# query) and is paid once per RUN, by the one command that wanted a place name.
# `tests/test_geocode.py` re-proves the deferral for that module by itself, because this
# test would report the regression as "the package got heavier" without naming the file.
#
# A red suite here means "you spent import time; say so".
EAGER_IMPORT_CLOSURE = {"click", "pam"}


def _normalise(name: str) -> str:
    """PEP 503 name normalisation: `PyWavelets`, `pillow_heif` and `Pillow` are not
    three spellings this file should have to know about individually."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _declared(section: str) -> set[str]:
    """The distribution names `pyproject.toml` declares, normalised, version pins gone.

    Hand-parsed on purpose and only just far enough: a requirement here is a name and an
    optional `>=`. `tomllib` does the part that a hand-rolled reader gets wrong (the
    M6-close lesson - a hand-rolled parser cannot notice the document is not the
    language); splitting `click>=8.1` on its first non-name character is not that part.
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data["project"]
    raw = (
        project["dependencies"]
        if section == "runtime"
        else project["optional-dependencies"][section]
    )
    return {_normalise(re.split(r"[<>=!~\[; ]", entry, maxsplit=1)[0]) for entry in raw}


def _metadata_closure(roots: set[str]) -> set[str]:
    """Every distribution reachable from `roots` through declared requirements.

    `extra == ` requirements are skipped: they are not installed by `pip install pam`,
    and Pillow alone declares twenty-odd of them. Requirements carrying any OTHER marker
    (`platform_system == 'Windows'`, which is how `colorama` arrived until click 8.5.0
    dropped it) ARE counted, by name, and are not walked into - the name is what this file
    is pinning, and on a non-Windows machine such a requirement would not be importable to
    walk.
    """
    seen: set[str] = set()
    queue = [(name, True) for name in roots]
    while queue:
        name, walk_in = queue.pop()
        key = _normalise(name)
        if key in seen:
            continue
        seen.add(key)
        if not walk_in:
            continue
        try:
            requirements = metadata.requires(name) or []
        except metadata.PackageNotFoundError:  # pragma: no cover - see the test below
            continue
        for requirement in requirements:
            head, _, marker = requirement.partition(";")
            if "extra ==" in marker:
                continue
            dependency = re.split(r"[<>=!~\[; ]", head.strip(), maxsplit=1)[0]
            if dependency:
                queue.append((dependency, marker.strip() == ""))
    return seen


def test_pyproject_declares_exactly_the_sanctioned_runtime_dependencies():
    """Design 1's stack table, in both directions, against `[project] dependencies`.

    THE ASSERTION THE WHOLE TASK IS ABOUT. `<=` here would be the hole: a sixth name
    added to `pyproject.toml` would satisfy it silently, and the next milestone's model
    server client would ship without anybody deciding it should.
    """
    declared = _declared("runtime")
    expected = set(RUNTIME_DEPENDENCIES)
    assert declared == expected, (
        "pyproject.toml's runtime dependencies and design 1's stack table disagree."
        f" Only in pyproject.toml: {sorted(declared - expected)}."
        f" Only in the spec-written allowlist: {sorted(expected - declared)}."
        " A new runtime dependency needs a spec amendment first, then an entry in"
        " RUNTIME_DEPENDENCIES with its reason, then the pyproject line."
    )


def test_every_sanctioned_runtime_dependency_is_actually_installed():
    """Declared is not installed. A name in `pyproject.toml` that pip cannot resolve is a
    dependency that fails at the user's first run instead of at ours, and
    `test_pyproject_declares_exactly...` above cannot tell the two apart - it compares one
    document with one literal and never touches the environment."""
    missing = []
    for name in sorted(RUNTIME_DEPENDENCIES):
        try:
            metadata.version(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
    assert missing == [], (
        f"declared as runtime dependencies but not installed: {missing}."
        ' Run: pip install -e ".[dev]"'
    )


def test_the_dev_dependencies_stay_dev_and_do_not_leak_into_the_runtime_list():
    """The two lists are disjoint, and `pyyaml` in particular is dev-only.

    `tests/test_ci_lanes.py` asserts that no module under `pam/` IMPORTS yaml. That is
    the other half; this is the declaration half, and neither implies the other - a
    dependency can be wrongly declared without being imported, and the wheel a user
    installs is built from the declaration.
    """
    runtime, dev = _declared("runtime"), _declared("dev")
    assert runtime & dev == set(), (
        f"declared both at runtime and dev-only: {sorted(runtime & dev)}"
    )
    assert "pyyaml" in dev and "pyyaml" not in runtime


def test_the_transitive_footprint_is_exactly_what_is_written_down():
    """What the five direct dependencies drag in, pinned in both directions.

    WHY THIS EXISTS AT ALL. `reverse_geocoder` is nine hundred lines and a CSV; `scipy`
    is thirty-seven megabytes. Approving the first and receiving the second without
    noticing is how a dependency budget stops meaning anything, and the closure sentence
    this file replaces never covered it: it counted IMPORTS, so `colorama` sat inside
    `{click, pam}` unmentioned from M1 until this file was written - and left again, at the
    M11 close, when click 8.5.0 stopped requiring it.

    WHY IT IS NOT BRITTLE IN THE WAY IT LOOKS. It reads DECLARED requirements, not the
    resolved environment, and it skips `extra ==` requirements - so it does not move when
    a new version is published unless that version changed what it requires, which is
    precisely the event worth a red suite. Extras and dev tooling are invisible to it.
    """
    found = _metadata_closure(set(RUNTIME_DEPENDENCIES)) - set(RUNTIME_DEPENDENCIES)
    expected = set(TRANSITIVE_DEPENDENCIES)
    assert found == expected, (
        "the transitive runtime footprint changed."
        f" Arrived without being written down: {sorted(found - expected)}."
        f" Written down but no longer required: {sorted(expected - found)}."
        " Each entry in TRANSITIVE_DEPENDENCIES carries the reason it is there; add or"
        " remove the reason with the name."
    )


def test_the_transitive_walk_can_see_a_dependency_at_all():
    """The negative control on the walk above, and it is not optional.

    `_metadata_closure` returning its own input for every input would make the assertion
    above pass for the best possible reason and the worst possible one, exactly the shape
    `test_net_canary.py`'s proof-of-sight defends against.

    THE PAIR MOVED AT THE M11 CLOSE, and why is worth a sentence. It used to be
    `click` -> `colorama`, which click 8.5.0 deleted - so the control that proved the walk
    could see went blind in the same release that changed what it was watching. The
    replacement is `imagehash` -> `numpy`: `numpy` is in neither this call's input nor
    pam's five DIRECT dependencies, so only a walk that really reads declared requirements
    finds it, and unlike a Windows-only marker it holds on every platform the project runs
    on. MEASURED, not assumed: with `_metadata_closure` stubbed to return its own input,
    this assertion fails.
    """
    assert "numpy" in _metadata_closure({"imagehash"}) - {"imagehash"}


# --- the eager import closure --------------------------------------------------------

# Run in a FRESH INTERPRETER, never in pytest's. By the time this module executes, this
# process has imported pytest, its plugins, and whatever every other test module pulled
# in; `sys.modules` here answers a question about the test session, not about pam. A
# subprocess is the only honest instrument, and it is the same one the milestone gates
# have used by hand since M1.
_CLOSURE_PROBE = """
import importlib, pkgutil, sys
import pam
for module in pkgutil.walk_packages(pam.__path__, prefix="pam."):
    importlib.import_module(module.name)
tops = {name.partition(".")[0] for name in sys.modules if name}
print(" ".join(sorted(top for top in tops if top not in sys.stdlib_module_names)))
"""


def _eager_closure(probe: str = _CLOSURE_PROBE) -> set[str]:
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=120,
        check=True,
    )
    names = set(result.stdout.split())
    # Editable installs inject a finder module named for the project; `__main__` and the
    # private C shims are not dependencies either. Dropped by SHAPE, not by name, so that
    # a real dependency cannot be dropped along with them.
    return {name for name in names if not name.startswith("_")}


def test_the_eager_import_closure_is_exactly_what_is_declared():
    """Import all of `pam.` in a clean interpreter; the non-stdlib residents must match.

    THIS IS THE M1-M8 MILESTONE-GATE PROCEDURE, RUN BY THE SUITE. It was a manual audit
    for eight milestones and its result was quoted in four sets of implementation notes;
    it had never once failed a build, because nothing ran it except a human at a close.

    It is a DIFFERENT claim from the two above. `pyproject.toml` says what pip installs;
    this says what ends up inside the process. A dependency declared and never imported
    is dead weight in the wheel; a dependency imported at module scope is a cost `pam
    --version` pays on every invocation.
    """
    found = _eager_closure()
    assert found == EAGER_IMPORT_CLOSURE, (
        "the set of non-stdlib modules resident after importing all of `pam.` changed."
        f" Newly imported: {sorted(found - EAGER_IMPORT_CLOSURE)}."
        f" No longer imported: {sorted(EAGER_IMPORT_CLOSURE - found)}."
        " If this is a deliberate new import, edit EAGER_IMPORT_CLOSURE and say in the"
        " commit what `pam --version` now pays for it."
    )


def test_the_closure_probe_would_notice_a_new_import():
    """The negative control, and the reason the assertion above is evidence.

    `_eager_closure` returning `{"click", "pam"}` unconditionally - a stubbed subprocess,
    a swallowed error, an over-eager filter - passes the test above forever. So the same
    machinery is driven against a probe that imports one extra thing, and it must see it.
    A stdlib module would be invisible by design, so the planted import is the real one
    M9 Task 8 will land: `PIL.Image`, whose footprint is exactly `PIL` and nothing else.
    """
    found = _eager_closure(_CLOSURE_PROBE.replace("import pam\n", "import pam, PIL.Image\n"))
    assert found == EAGER_IMPORT_CLOSURE | {"PIL"}


# --- 4. THE ONE WAY THIS DEPENDENCY LIES ---------------------------------------------
#
# `reverse_geocoder.search()` DEFAULTS TO `mode=2`, AND `mode=2` RETURNS SILENTLY WRONG
# PLACE NAMES ON WINDOWS. Reproduced by the M9 Task 6 review, three ways:
#
#   * called at MODULE SCOPE of a module `multiprocessing` re-imports, `mode=2` answered
#     `[('El Tarter', 'AD'), ('El Tarter', 'AD')]` for London and San Francisco - the
#     FIRST ROW of `rg_cities1000.csv`, for every coordinate - with EXIT CODE 0, nothing
#     raised in the parent, and sixteen children that each died in multiprocessing's
#     bootstrapping check.
#   * the same call inside a function under `if __name__ == "__main__"`: correct.
#   * `mode=1`: correct, always.
#
# `pquery` starts `mp.Process` per CPU, writes into a shared-memory index array, joins,
# and raises only if the workers set `ierr` - which a child that dies during BOOTSTRAP
# never reaches. So the zero-initialised index array is returned as if it were an answer,
# and index 0 is a real city. THERE IS NO EXCEPTION AND NO WARNING; the geocoder simply
# reports Andorra.
#
# For pam that is a wrong place name written into the stem of an irreplaceable file, at
# `high` trust, through `{place}`. It is exactly vision principle 9's failure: a claim the
# name makes that the evidence does not support, with nothing to tell the user.
#
# THE GUARD BELOW IS WHY THIS IS NOT ONLY A COMMENT. Task 9 is the first consumer, and a
# warning that lives in prose is a warning the next executor may not read. The AST walk
# refuses any `reverse_geocoder` search/get call under `pam/` that does not pass a literal
# `mode=1`. Since Task 9 the walk has a real file to inspect - `pam/enrich/geocode.py` -
# and the planted-source control below stays anyway, on the `ALLOWED_NETWORK_IMPORTS`
# precedent: a walk that inspects one file and a matcher that returns [] look identical
# from a green run, and the third test proves the guard REACHES that file.

_GEOCODER_CALLS = ("search", "get")


def _geocoder_receivers(tree):
    """The names in one module that could hold the geocoder, RESOLVED rather than listed.

    Returns `(imported, handed_on)`.

    M9 TASK 9 REVIEW, FINDING 1. This used to be the literal set `{"rg", "rgc"}`, and
    M1280 measured what that cost: renaming `pam/enrich/geocode.py`s local alias `rg` to
    `geo`, with `mode=1` left on every call, made both calls INVISIBLE to the walk below -
    which went green - and only the reach test noticed. A guard anchored to a NAME rather
    than to the CALL is this project recurring shape, and the repair is to resolve the
    alias instead of guessing it.

    Two bindings count, and they are returned SEPARATELY because they are not equally
    safe to trust:

      * `imported` - `import reverse_geocoder [as X]` / `from reverse_geocoder import ...`.
        Unambiguous, so it counts for both `search` and `get`.
      * `handed_on` - a name assigned from a CALL: `rg = _import()`, `geo = _geocoder()`,
        `x = importlib.import_module("reverse_geocoder")`. The geocoder is handed out by a
        function in this codebase, so the import name is never the receiver of the call
        that matters. This counts for `search` ONLY. Extending it to `get` matches
        `memberships.get(...)` in `pam/naming/planner.py` - a file that mentions the
        geocoder in prose - and a guard with a false positive in it is a guard somebody
        turns off. The reach test over `GEOCODER_CONSUMERS` is the containment for the
        residual: a consumer that reached the geocoder only through `alias.get(...)` would
        make that test red, because stripping `mode` from it would produce no offender.

    A name bound by a plain `import re` is in NEITHER set, which is what keeps `re.search`
    out of the offender list in a file whose prose happens to mention the geocoder.
    """
    import ast

    imported = set()
    handed_on = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "reverse_geocoder":
                    imported.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "reverse_geocoder":
                imported.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    handed_on.add(target.id)
    return imported, handed_on


def _bad_geocoder_calls(tree, where):
    """Every `*.search(...)` / `*.get(...)` call whose receiver could be the geocoder and
    which does not pass `mode=1` as a literal keyword."""
    import ast

    imported, handed_on = _geocoder_receivers(tree)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _GEOCODER_CALLS:
            continue
        receiver = ast.unparse(node.func.value)
        allowed = imported if node.func.attr == "get" else imported | handed_on
        if "reverse_geocoder" not in receiver and receiver not in allowed:
            continue
        mode = next((kw for kw in node.keywords if kw.arg == "mode"), None)
        if not (
            mode is not None and isinstance(mode.value, ast.Constant) and mode.value.value == 1
        ):
            offenders.append(f"{where}:{node.lineno}")
    return offenders


def test_no_call_into_reverse_geocoder_under_pam_uses_its_default_mode():
    """`mode=1` is mandatory in `pam/`, and it is a correctness rule, not a performance one.

    See the block above: the default `mode=2` can return the first row of the dataset for
    every coordinate, silently, with exit code 0.
    """
    import ast

    offenders = []
    for path in sorted((REPO_ROOT / "pam").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "reverse_geocoder" not in source:
            continue
        offenders += _bad_geocoder_calls(
            ast.parse(source), path.relative_to(REPO_ROOT).as_posix()
        )
    assert offenders == [], (
        "a call into reverse_geocoder under pam/ does not pass mode=1: "
        f"{offenders}. Its default (mode=2) fans out over multiprocessing and, from a"
        " module multiprocessing re-imports, returns the dataset's FIRST ROW for every"
        " coordinate with no exception raised. Pass mode=1 explicitly."
    )


# The files under `pam/` that CALL the geocoder, spelled by hand rather than discovered by
# the matcher the reach test is checking. M9 Task 9 makes this exactly one. It is a FLOOR:
# a milestone that adds a second consumer adds a row here, and the reach test then proves
# the guard sees the new file too. Written from the plan - Task 9 owns `enrich/geocode.py`
# and clarification 3 gives geocode `reverse_geocoder` and nothing else - never from a walk.
GEOCODER_CONSUMERS = ("pam/enrich/geocode.py",)
assert GEOCODER_CONSUMERS, (
    "the geocoder-consumer list is empty, so the reach test below iterates nothing and"
    " passes vacuously; pam has had at least one consumer since M9 Task 9"
)


def test_the_mode_guard_actually_REACHES_EVERY_geocoder_module_in_the_tree():
    """The other half of the control: the planted sources prove the MATCHER works, and
    this proves the WALK gets to the files that matter.

    Each real source is parsed, every `mode` keyword is stripped out of its calls, and the
    result is fed back through the same matcher: at least one offender must appear. A
    guard that skipped a file - a changed glob, a renamed module, an alias the receiver
    test does not recognise - would be green on the walk above and red here.

    M9 TASK 9 REVIEW, FINDING 1: this used to name ONE hard-coded path, so a second
    consumer under `pam/` carrying a third alias would have been invisible to both halves.
    It now runs over `GEOCODER_CONSUMERS`, asserted non-empty at collection so that
    emptying it cannot quietly retire the control (M7 M743).
    """
    import ast

    for relative in GEOCODER_CONSUMERS:
        path = REPO_ROOT / relative
        assert path.is_file(), f"{relative} is named a geocoder consumer and is not there"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert _bad_geocoder_calls(tree, relative) == [], "the shipped file must be clean"
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                node.keywords = [kw for kw in node.keywords if kw.arg != "mode"]
        assert _bad_geocoder_calls(tree, relative), (
            f"stripping mode= from {relative} produced no offender, so the guard cannot"
            " see that file at all and the walk above is vacuous for it"
        )


def test_the_mode_guard_sees_a_call_through_an_alias_it_was_never_told_about():
    """FINDING 1 own control, and the reason the receiver set is RESOLVED and not listed.

    `import reverse_geocoder` bound to a name no list could have anticipated, handed on
    through a function the way `pam/enrich/geocode.py` hands it on, is still a call into
    the geocoder and still has to pass `mode=1`. Before FINDING 1 this planted source was
    green, which is exactly what M1280 measured on the real module.
    """
    import ast

    aliased = (
        "import reverse_geocoder as anything\n"
        "def _hand_it_on():\n"
        "    return anything\n"
        "somewhere = _hand_it_on()\n"
        "somewhere.search(points)\n"
    )
    safe = aliased.replace("somewhere.search(points)", "somewhere.search(points, mode=1)")
    assert _bad_geocoder_calls(ast.parse(aliased), "planted") == ["planted:5"]
    assert _bad_geocoder_calls(ast.parse(safe), "planted") == []


def test_a_plain_stdlib_search_in_a_geocoder_mentioning_file_is_NOT_an_offender():
    """The other side of the resolution, without which FINDING 1 repair would over-match.

    `pam/naming/planner.py` and `pam/catalog/repo/events.py` both mention the geocoder in
    prose. A `re.search(...)` in such a file is not a geocoder call, and a guard that said
    it was would be turned off by the first person who hit the false positive.
    """
    import ast

    source = "import re\n# reverse_geocoder in a comment\nre.search(pattern, subject)\n"
    assert _bad_geocoder_calls(ast.parse(source), "planted") == []


def test_the_mode_guard_is_proven_enforcing_on_a_planted_call():
    """The negative control, and it is what stops the test above from being vacuous.

    `_bad_geocoder_calls` returning [] unconditionally would pass the walk above whatever
    is in the tree. Three planted sources: the dangerous default, the dangerous explicit
    `mode=2`, and the safe form.
    """
    import ast

    dangerous = "import reverse_geocoder\nreverse_geocoder.search([(1.0, 2.0)])\n"
    explicit = "import reverse_geocoder as rg\nrg.search(points, mode=2)\n"
    safe = "import reverse_geocoder as rg\nrg.search(points, mode=1)\n"
    assert _bad_geocoder_calls(ast.parse(dangerous), "planted") == ["planted:2"]
    assert _bad_geocoder_calls(ast.parse(explicit), "planted") == ["planted:2"]
    assert _bad_geocoder_calls(ast.parse(safe), "planted") == []
