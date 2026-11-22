"""The faces model pack: the seam, the manifest, the provider pin, and golden parity.

FOUR KINDS OF TEST LIVE HERE AND ONLY ONE OF THEM CAN SKIP.

  1. **The absence tests.** `describe("")`, `describe` over a folder with no manifest,
     `pam faces manifest` over a folder with no models. These run everywhere, including on
     a machine that has never heard of onnxruntime, because they are about the sentences a
     user meets when the stack is *not* there and that is the population that meets them.
  2. **The fake-runtime tests.** A pack whose two "ONNX" files are a few bytes of test
     data, a manifest `write_manifest` really wrote for them, and a stand-in
     `onnxruntime` module in `sys.modules`. This is how `load`'s **`providers=`** argument
     (A5) and `describe`'s **never loads a session** promise are asserted WITHOUT the
     extra: the thing under test is what pam passes to the constructor, and a fake
     constructor records it exactly as a real one would. **These never skip either**, which
     matters more than it sounds - the provider pin is a vision S4 property, and a control
     that only runs in one CI job is a control most pushes do not have.
  3. **The AST guards.** No module under `pam/` imports onnxruntime or numpy at module
     scope; no `InferenceSession(...)` anywhere under `pam/` omits `providers=`. AST and
     not grep, because a grep-based guard reports a hit on a comment and a miss on a line
     it has never seen. **Never skip.**
  4. **Golden parity, marked `faces`** - the one kind that needs the real extra and the
     real ~37 MB pack. `tests/faces_stack.py` owns that decision for the whole suite.

WHAT GOLDEN PARITY HERE DOES AND DOES NOT PROVE, said plainly because the plan requires it
to be said. It pins this pipeline against **DRIFT**: a swapped channel order, a changed
stride, a different NMS threshold, a different score fusion, a pack whose bytes moved. It
is **not** a re-derivation of the spike's cv2 comparison - `opencv-python` is not a pam
dependency and never will be; it was the spike's oracle in a throwaway virtualenv, where
it measured detector agreement at 0.000 px / 0.00000 score and recogniser agreement at
cosine 1.00000012 against OpenCV's own wrappers. That comparison is done, recorded in
`docs/implementation-notes/m11-model-spike.md`, and its OUTPUT is what is frozen here.

AND THE ASSERTION IS ON VALUES, WHICH IS THE WHOLE REASON THE FIXTURE EXISTS (A4). The
detector consumes **BGR** and the recogniser consumes **RGB**. Feeding SFace BGR - the
obvious mistake, since the detector took BGR - produces **cosine 0.938 against the correct
embedding, not garbage**. A test that checks "a 128-vector came back" passes for it. A
test that checks the vector's *shape* passes for it. A test that checks it is normalised
passes for it. Only the numbers see it, so the numbers are what is committed.

THE FIXTURE'S IMAGE IS SYNTHETIC AND THAT IS DELIBERATE. It is a drawn oval with two eye
blobs, generated once and committed - not a photograph of a person. pam's test suite has
no business carrying a real face, and this one is detected at score 0.91 on stride 32,
which exercises the decode path end to end. Regenerating it is `_write_fixture` below.
"""

import ast
import json
import logging
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest
from faces_stack import STACK_READY, model_dir

from pam import __version__
from pam.enrich import faces_model
from pam.enrich.faces_model import FacesPackError


# The pip route to an optional extra, spelled from the maintainer's decision 4 (2026-11-21), never read
# from `pam.install_method`: the release's own wheel for the running version, never a bare PyPI name.
def _pip(extra: str) -> str:
    v = __version__
    return (
        f'pip install "photo-archive-manager[{extra}] @ https://github.com/NuncProTunc7/'
        f'photo-archive-manager/releases/download/v{v}/photo_archive_manager-{v}-py3-none-any.whl"'
    )


REPO_ROOT = Path(__file__).resolve().parents[1]
PAM_ROOT = REPO_ROOT / "pam"
FIXTURE_IMAGE = Path(__file__).parent / "fixtures" / "faces_parity.png"
FIXTURE_VALUES = Path(__file__).parent / "fixtures" / "faces_parity.json"

# The detection score the fixture was recorded at. Not a product constant - design 11's
# `[tiers] faces` floor is the product's, applied at READ time (A11) - this is only the
# threshold the reference values were frozen at, and it belongs to the fixture.
FIXTURE_SCORE_THRESHOLD = 0.5


# --- 1. the pack, without any runtime at all -----------------------------------------


def _fake_pack(tmp_path: Path) -> Path:
    """A structurally valid pack whose model files are a few bytes of test data.

    `write_manifest` hashes what is really there, so the manifest is honest about these
    files and `describe` accepts them. Nothing here can be *run* - which is the point:
    every property in this section is about what pam reads, checks and passes on, and
    none of it is about arithmetic.
    """
    directory = tmp_path / "pack"
    directory.mkdir(parents=True)
    (directory / faces_model.DETECTOR_FILE).write_bytes(b"not really a detector")
    (directory / faces_model.RECOGNIZER_FILE).write_bytes(b"not really a recognizer")
    faces_model.write_manifest(directory)
    return directory


class _FakeSession:
    """Records exactly what `InferenceSession` was constructed with."""

    def __init__(self, path, providers=None, **kwargs):
        self.path = path
        self.providers = providers
        self.kwargs = kwargs

    def get_providers(self):
        """What a real session answers: the providers it was built with (N9's receipt)."""
        return list(self.providers or [])


def _fake_runtime(monkeypatch) -> list[_FakeSession]:
    """Put stand-in `onnxruntime` and `numpy` modules in `sys.modules`; collect sessions."""
    built: list[_FakeSession] = []

    def make(path, providers=None, **kwargs):
        session = _FakeSession(path, providers, **kwargs)
        built.append(session)
        return session

    onnxruntime = types.ModuleType("onnxruntime")
    onnxruntime.InferenceSession = make  # type: ignore[attr-defined]
    onnxruntime.get_available_providers = lambda: [  # type: ignore[attr-defined]
        "AzureExecutionProvider",
        "CPUExecutionProvider",
    ]
    monkeypatch.setitem(sys.modules, "onnxruntime", onnxruntime)
    if "numpy" not in sys.modules:
        monkeypatch.setitem(sys.modules, "numpy", types.ModuleType("numpy"))
    return built


def test_describe_says_what_is_unset_rather_than_raising_when_model_dir_is_empty():
    """The commonest state of every machine, and it must be a sentence (A12).

    `[enrich.faces] model_dir` defaults to `""` because pam cannot fetch a model and will
    not invent a path. The sentence has to name the key - a user who is told "no model
    pack" and not *which setting* has nowhere to go - and it has to say that pam does not
    download one, because the alternative reading is "wait for it".
    """
    ok, sentence = faces_model.describe("")
    assert ok is False
    assert "[enrich.faces] model_dir" in sentence
    assert "never downloads" in sentence
    assert "pam faces manifest" in sentence


def test_describe_names_the_missing_manifest_rather_than_the_missing_files(tmp_path):
    """A folder with no `pack.toml` gets one instruction, not two."""
    ok, sentence = faces_model.describe(tmp_path)
    assert ok is False
    assert faces_model.MANIFEST_NAME in sentence
    assert "pam faces manifest" in sentence


def test_describe_accepts_a_pack_whose_files_hash_to_what_the_manifest_says(tmp_path):
    ok, sentence = faces_model.describe(_fake_pack(tmp_path))
    assert ok is True, sentence
    assert faces_model.DETECTOR_FILE in sentence
    assert faces_model.RECOGNIZER_FILE in sentence


def test_describe_refuses_a_pack_whose_bytes_changed_under_its_manifest(tmp_path):
    """A3: `available()` verifies the HASHES. A manifest checking only names is worth
    nothing - the file it describes could be any bytes at all, and the shape this most
    often takes is real: the `raw.githubusercontent` download path for these two models
    returns a ~130-byte git-LFS POINTER, which is a perfectly present file with the right
    name and no model in it. Swapping the bytes and leaving the name is therefore not a
    contrived mutation, it is the failure mode."""
    directory = _fake_pack(tmp_path)
    (directory / faces_model.RECOGNIZER_FILE).write_bytes(b"version https://git-lfs...")
    ok, sentence = faces_model.describe(directory)
    assert ok is False
    assert faces_model.RECOGNIZER_FILE in sentence
    assert "hash" in sentence


def test_describe_refuses_a_manifest_naming_a_file_that_is_not_there(tmp_path):
    directory = _fake_pack(tmp_path)
    (directory / faces_model.DETECTOR_FILE).unlink()
    ok, sentence = faces_model.describe(directory)
    assert ok is False
    assert faces_model.DETECTOR_FILE in sentence


def test_describe_never_loads_a_session(tmp_path, monkeypatch):
    """The reason `describe` is not `load`, asserted on the CALL and not on the outcome.

    `pam doctor`, the refusal paths and (from Task 10) the review UI all need "is this
    pack usable?" without paying 38 MB of model load, and - the half that is a safety
    property rather than a performance one - without constructing anything that selects an
    execution provider. Watching the constructor is the only way to tell "never built a
    session" from "built one and threw it away": both leave the same return value.
    """
    built = _fake_runtime(monkeypatch)
    ok, _ = faces_model.describe(_fake_pack(tmp_path))
    assert ok is True
    assert built == [], "describe() constructed an InferenceSession"


def test_load_builds_both_sessions_and_pins_the_cpu_execution_provider(tmp_path, monkeypatch):
    """A5, behaviourally, and WITHOUT the optional extra so it runs on every push.

    A stock onnxruntime wheel offers `['AzureExecutionProvider', 'CPUExecutionProvider']`
    and Azure EP executes the model against a REMOTE endpoint;
    `InferenceSession(path)` with no `providers=` accepts whatever is offered. So the
    property is about the ARGUMENT pam passes, and a fake constructor records that exactly
    as a real one does. Both sites are checked, not one: a list written twice is a list
    that disagrees with itself once.
    """
    built = _fake_runtime(monkeypatch)
    sessions = faces_model.load(_fake_pack(tmp_path))
    assert len(built) == 2, f"expected two sessions, got {len(built)}"
    for session in built:
        assert session.providers == ["CPUExecutionProvider"], (
            f"a session was built with providers={session.providers!r}; vision S4 admits"
            " no execution provider that can leave the machine"
        )
    assert sessions.detector is built[0]
    assert sessions.recognizer is built[1]


def test_load_refuses_a_bad_pack_before_it_reaches_onnxruntime(tmp_path, monkeypatch):
    """Order matters: a pack whose bytes do not match its manifest must be refused
    BEFORE anything is handed to a model runtime, not after."""
    built = _fake_runtime(monkeypatch)
    directory = _fake_pack(tmp_path)
    (directory / faces_model.DETECTOR_FILE).write_bytes(b"something else entirely")
    with pytest.raises(FacesPackError):
        faces_model.load(directory)
    assert built == [], "load() reached onnxruntime with a pack it should have refused"


def test_the_absent_extra_is_a_named_sentence_and_not_an_import_error(tmp_path, monkeypatch):
    """`pip install onnxruntime` is the sentence; `ModuleNotFoundError: onnxruntime` is
    not. `enrich/phash.py`'s precedent, for the same reason: a user who has never heard
    the name cannot act on a traceback that only contains it."""
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    with pytest.raises(faces_model.FacesUnavailableError) as raised:
        faces_model.load(_fake_pack(tmp_path))
    assert "onnxruntime" in str(raised.value)
    assert _pip("faces") in str(raised.value)


# --- 1b. what the faces commands SAY (M12 Task 4: C7, N9, P3) ------------------------

# Design 12 rev 27 (P3), TRANSCRIBED: the missing-weight refusal "ends with the source URL and
# '(pam never downloads a model)'". The URL is spelled here, never read from `SOURCE`.
P3_SOURCE_TAIL = (
    "; get it from https://github.com/opencv/opencv_zoo (pam never downloads a model)."
)


class _Lines(logging.Handler):
    """Every message a logger emitted, formatted, in order."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[tuple[int, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append((record.levelno, record.getMessage()))


def _captured(name: str) -> tuple[logging.Logger, _Lines]:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = _Lines()
    logger.handlers = [handler]
    return logger, handler


@pytest.mark.parametrize(
    ("role", "file_name"),
    [
        ("detector", "face_detection_yunet_2023mar.onnx"),
        ("recognizer", "face_recognition_sface_2021dec.onnx"),
    ],
)
def test_the_missing_weight_file_sentence_names_where_the_file_comes_from(
    tmp_path, role, file_name
):
    """P3: the branch a user is MOST likely to hit - a manifest naming a file that is not in
    the folder - names the source, as `write_manifest`'s branch always did (M11 Task 8 stage
    2's disclosed asymmetry: pam knew the source and did not say so here). The WHOLE
    sentence, both roles, spelled from design 12 rev 27 and not from `SOURCE`."""
    directory = _fake_pack(tmp_path)
    (directory / file_name).unlink()
    ok, sentence = faces_model.describe(directory)
    assert ok is False
    assert sentence == (
        f"pack.toml names a {role} {file_name} that is not in {directory}{P3_SOURCE_TAIL}"
    ), sentence


def test_load_writes_N9s_receipt_from_what_each_SESSION_reports(tmp_path, monkeypatch):
    """N9 (design 12 rev 26): the model identity, EACH session's `get_providers()` and what
    the runtime offers, at INFO, in the log the command writes.

    THE FAKE RUNTIME HERE DID NOT HONOUR THE PIN FOR THE RECOGNIZER - it reports Azure as
    well - which is the one situation the receipt exists to reveal. A receipt that printed
    `PROVIDERS` (the list pam PASSED) would read CPU for both and hide it; one that logged the
    detector's answer twice would hide it too. The line is compared WHOLE, spelled by hand;
    only the digest is taken from `identity`, whose own tests pin it.
    """
    built = _fake_runtime(monkeypatch)
    real_make = sys.modules["onnxruntime"].InferenceSession

    def make(path, providers=None, **kwargs):
        session = real_make(path, providers=providers, **kwargs)
        if str(path).endswith("face_recognition_sface_2021dec.onnx"):
            session.get_providers = lambda: ["AzureExecutionProvider", "CPUExecutionProvider"]
        return session

    monkeypatch.setattr(sys.modules["onnxruntime"], "InferenceSession", make)
    pack = _fake_pack(tmp_path)
    logger, handler = _captured("pam-test-receipt")
    sessions = faces_model.load(pack, logger)

    version = faces_model.identity(pack)[1]
    assert len(built) == 2
    assert handler.lines == [
        (
            logging.INFO,
            f"faces model yunet+sface {version};"
            " detector providers ['CPUExecutionProvider'];"
            " recognizer providers ['AzureExecutionProvider', 'CPUExecutionProvider'];"
            " runtime offers ['AzureExecutionProvider', 'CPUExecutionProvider']",
        )
    ], handler.lines
    assert sessions.model_version == version


@pytest.mark.faces
def test_the_real_runtime_writes_the_receipt_with_the_real_providers(parity):
    """N9 against the REAL onnxruntime: what each real session reports and what the real
    runtime offers. A stock wheel offers Azure (A5); the pin must hold and the receipt must
    say both - that is the line HT-M12 reads."""
    import onnxruntime

    logger, handler = _captured("pam-test-real-receipt")
    faces_model.load(model_dir(), logger)
    version = faces_model.identity(model_dir())[1]
    offered = list(onnxruntime.get_available_providers())
    assert handler.lines == [
        (
            logging.INFO,
            f"faces model yunet+sface {version};"
            " detector providers ['CPUExecutionProvider'];"
            " recognizer providers ['CPUExecutionProvider'];"
            f" runtime offers {offered}",
        )
    ], handler.lines


# --- 2. the identity digest (A4) -----------------------------------------------------


def test_the_identity_digest_moves_when_any_answer_changing_constant_moves(tmp_path):
    """A4, field by field, and this is what protects the cache from a wrong hit.

    Model identity is a digest over every constant that can change an answer, never a
    directory basename - two folders called `models` can hold different bytes. The fields
    are enumerated here as LITERALS with the value each should be perturbed to, spelled
    from the amendment rather than read back off `Manifest`'s field list: a loop over
    `dataclasses.fields(Manifest)` would pass for any digest, including one that hashed
    every field and one that hashed the whole file blindly.
    """
    directory = _fake_pack(tmp_path)
    _name, baseline = faces_model.identity(directory)

    perturbations = {
        "detector_sha256": '"deadbeef"',
        "detector_input": "[320, 320]",
        "detector_channels": '"RGB"',
        "recognizer_sha256": '"feedface"',
        "recognizer_input": "[96, 96]",
        # THE ONE THIS WHOLE MILESTONE IS ABOUT. A pack that swapped the recogniser's
        # channel order would produce embeddings 0.938 away from the stored ones -
        # near enough that nothing looks broken and every cluster is slightly wrong.
        "recognizer_channels": '"BGR"',
        "embedding_dim": "512",
        "embedding_dtype": '"float16"',
        "embedding_l2_normalized": "true",
    }
    assert len(perturbations) == 9, "a digest input was added or lost without a decision"

    original = (directory / faces_model.MANIFEST_NAME).read_text(encoding="utf-8")
    for key, replacement in perturbations.items():
        text = "\n".join(
            f"{key} = {replacement}" if line.startswith(f"{key} = ") else line
            for line in original.splitlines()
        )
        (directory / faces_model.MANIFEST_NAME).write_text(text + "\n", encoding="utf-8")
        _name, moved = faces_model.identity(directory)
        assert moved != baseline, (
            f"changing {key} did not change the model identity; a cache keyed on it would"
            " serve one pack's answers as another's"
        )
    (directory / faces_model.MANIFEST_NAME).write_text(original, encoding="utf-8")


def test_the_identity_digest_does_not_move_for_the_same_pack_in_a_different_folder(tmp_path):
    """The other direction, and the reason the digest is not the directory name: the same
    two files copied elsewhere are the same model and must hit the same cache."""
    first = _fake_pack(tmp_path / "a")
    second = _fake_pack(tmp_path / "b")
    assert faces_model.identity(first) == faces_model.identity(second)
    assert faces_model.identity(first)[0] == faces_model.MODEL_NAME


# A4's digest inputs THAT ARE PAM'S OWN CONSTANTS AND NOT THE MANIFEST'S, spelled from the
# amendment - "the score fusion, box/keypoint decode and NMS constants ... and pam's own
# face-pipeline version constant" - with a value each must be perturbed to. Never read off
# `identity()`'s payload, which is the subject.
#
# THIS LIST EXISTS BECAUSE FIVE OF THESE SIX DELETED FROM THE DIGEST WITH 3041 TESTS AND
# THE WHOLE FACES JOB GREEN (M11 Task 7 review, finding F1/M1746-M1747). The test above
# enumerates the NINE MANIFEST fields and is right to; the six below live in
# `faces_model.py`, and only `PIPELINE_VERSION` had a test. I6 - one archive, one
# face-model generation - rests entirely on this digest, and pam changing its NMS
# threshold or its score fusion WITHOUT the pack moving is exactly the case a hash over
# the weights cannot see, which is what A4 exists for.
PIPELINE_DIGEST_INPUTS = {
    "STRIDES": (4, 8, 16),
    "SCORE_FUSION": "cls*obj",
    "BOX_DECODE": "cx=b0 cy=b1 w=b2 h=b3",
    "KEYPOINT_DECODE": "kp_i=(k[2i],k[2i+1])",
    "NMS_IOU": 0.5,
    "PIPELINE_VERSION": "99",
}

assert len(PIPELINE_DIGEST_INPUTS) == 6, (
    "A4 names six digest inputs that are pam's own constants rather than the pack's;"
    " adding or losing one is a decision about model identity, not an edit"
)


def test_the_identity_digest_moves_when_a_constant_of_PAMS_OWN_moves(tmp_path, monkeypatch):
    """A4/I7's other half: the digest inputs that are NOT in `pack.toml`.

    The manifest half is pinned by the test above, field by field, from A3's keys. These
    six are pam's own decode constants, and every one of them changes every box, every
    landmark and every embedding pam has ever stored while the two ONNX files - and so
    every hash in the manifest - stay exactly as they were.

    Perturbed one at a time through `monkeypatch.setattr` on the module, because that is
    where they live; the expected values are literals here so that the enumeration is a
    reading of the amendment and not a loop over whatever `identity()` happens to hash.
    """
    directory = _fake_pack(tmp_path)
    _name, baseline = faces_model.identity(directory)
    for attribute, replacement in PIPELINE_DIGEST_INPUTS.items():
        assert hasattr(faces_model, attribute), attribute
        with monkeypatch.context() as patched:
            patched.setattr(faces_model, attribute, replacement)
            _name, moved = faces_model.identity(directory)
        assert moved != baseline, (
            f"changing {attribute} did not change the model identity. A4 lists it as a"
            " digest input because it changes an answer without the pack changing at all;"
            " I6's one-archive-one-generation refusal cannot see a generation the digest"
            " does not distinguish"
        )


def test_the_identity_digest_covers_pams_own_pipeline_version(tmp_path, monkeypatch):
    """A4's last input, and the one a hash over the weights cannot see: pam changing a
    crop rule or a normalisation step without the pack moving at all."""
    directory = _fake_pack(tmp_path)
    before = faces_model.identity(directory)
    monkeypatch.setattr(faces_model, "PIPELINE_VERSION", "99")
    assert faces_model.identity(directory) != before


# --- 3. the AST guards (never skip) ---------------------------------------------------


def _pam_modules() -> list[Path]:
    modules = sorted(PAM_ROOT.rglob("*.py"))
    assert len(modules) > 60, f"the module walk found only {len(modules)} files under pam/"
    return modules


# THE MATCHER IS A HELPER SO THAT THE NEGATIVE CONTROL CAN DRIVE THE REAL ONE (M11 Task 7
# review, finding F3). Written inline inside the test, this walk could be neutered - `for
# node in []` - and the whole suite stayed green, because `pam/` legitimately holds no
# offender and the negative control planted a tree and then RE-IMPLEMENTED the matcher on
# it. Two copies of a walk prove they agree with each other and nothing else (CLAUDE.md's
# M769/M776), and CLAUDE.md's other sentence is the rest of it: a guard that walks the
# wrong AST node passes everything. One function, two callers: the guard over `pam/`, and
# the control over a tree whose answer is known.
def _module_scope_faces_imports(tree: ast.Module) -> list[str]:
    """`onnxruntime`/`numpy` imported at MODULE SCOPE in `tree`.

    An import inside a function body is invisible here BY DESIGN - being invisible is the
    property A2 asks for, so the control below pins both answers, not just the positive.
    """
    found: list[str] = []
    for node in tree.body:  # MODULE SCOPE ONLY - a deferred import is inside a function
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names = [(node.module or "").split(".")[0]]
        found.extend(name for name in names if name in ("onnxruntime", "numpy"))
    return found


def test_no_module_under_pam_imports_onnxruntime_or_numpy_at_module_scope():
    """A2's deferral, as an AST fact rather than as a promise in a docstring.

    `EAGER_IMPORT_CLOSURE` in `tests/test_deps.py` would also catch this, and would report
    it as "the package got heavier" without naming the file that did it - `phash.py`'s
    lesson, applied before the mistake instead of after. It is also the check that keeps
    the optional extra optional: a module-scope `import numpy` in `pam/` makes
    `pam --version` fail outright on a machine that installed `pam` and not `pam[faces]`.

    AST and not grep: a grep hits the word in this very docstring.
    """
    offenders = []
    for path in _pam_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name in _module_scope_faces_imports(tree):
            offenders.append(f"{path.relative_to(REPO_ROOT)}: {name}")
    assert offenders == [], (
        f"the [faces] extra is imported at module scope: {offenders}. Every import of it"
        " is deferred (pyproject.toml's [faces] block says why); a module-scope one breaks"
        " every pam command on a machine that did not install the extra."
    )


def test_the_module_scope_walk_can_see_an_import_it_is_supposed_to_catch():
    """The negative control, and IT DRIVES `_module_scope_faces_imports` ITSELF.

    A walk that parses nothing, or that only ever inspects function bodies, passes the
    test above for every possible tree: `pam/` holds no offender, so an empty result is
    what a working guard and a dead one both return. Written as a re-implementation of the
    matcher over a planted tree - which is how it started - this control was green with the
    real walk neutered to `for node in []` (M11 Task 7 review, F3/M1755). It has to call
    the function the guard calls, and it has to pin BOTH answers: the module-scope import
    is seen, and the one inside a function body is not.
    """
    planted = ast.parse("import numpy\n\n\ndef f():\n    import onnxruntime\n")
    assert _module_scope_faces_imports(planted) == ["numpy"], (
        "the module-scope walk no longer sees a module-scope `import numpy`, or has begun"
        " seeing the deferred `import onnxruntime` beside it that it must ignore"
    )
    assert _module_scope_faces_imports(ast.parse("def f():\n    import numpy\n")) == []
    assert _module_scope_faces_imports(ast.parse("from numpy import array\n")) == ["numpy"]


# The same helper treatment, for the same reason and with more at stake: this matcher is
# one of A5's three controls and one of only TWO that run on every push (M1748a measured
# the third - the real-session `get_providers()` check - as unable to express an unpinned
# session at all on onnxruntime 1.29.0). Neutered inline, it was green over the whole
# suite (F3/M1756).
def _unpinned_session_calls(tree: ast.Module) -> list[int]:
    """Line numbers of `InferenceSession(...)` calls in `tree` with no `providers=`.

    Matched on the CALL, never on a name: `InferenceSession` reached through any alias
    still spells `InferenceSession` at the call site, and a guard anchored to the module
    alias is M1280's shape.
    """
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "InferenceSession":
            continue
        if not any(keyword.arg == "providers" for keyword in node.keywords):
            lines.append(node.lineno)
    return lines


def test_every_onnx_session_pins_the_cpu_execution_provider():
    """A5 as an AST fact over the WHOLE package, and it never skips.

    The behavioural test above proves the two sites in `faces_model.py` pass
    `providers=`. This proves there is no third site anywhere - including one a later task
    adds in `pam/webui/` or `pam/faces/` - because the milestone that opens a session
    somewhere new is exactly the milestone that will not think about Azure EP.

    Matched on the CALL, never on a name: `InferenceSession` reached through any alias
    still spells `InferenceSession` at the call site, and a guard anchored to the module
    alias is M1280's shape.
    """
    offenders = []
    for path in _pam_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for lineno in _unpinned_session_calls(tree):
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    assert offenders == [], (
        f"an InferenceSession is constructed without an explicit providers=: {offenders}."
        " A stock onnxruntime wheel offers AzureExecutionProvider, which executes the"
        " model against a remote endpoint. Vision S4 admits no exception."
    )


def test_the_provider_walk_would_notice_a_session_built_without_one():
    """The negative control for the guard above, and IT DRIVES `_unpinned_session_calls`.

    A walk that matches nothing and a package that is clean look identical from a green
    run - `test_net.py`'s precedent. This control used to plant the tree and then
    re-implement the matcher over it, so breaking the real matcher's name comparison left
    the whole suite green (M11 Task 7 review, F3/M1756). It now calls the same function
    the guard calls, and pins both answers: the bare call is caught, the pinned one is not.

    THE PLAIN-NAME FORM IS HERE TOO. `from onnxruntime import InferenceSession` reaches
    the call site as `ast.Name`, not `ast.Attribute`, and a matcher that handled only the
    attribute form would still satisfy an alias-only control.
    """
    planted = ast.parse(
        "import onnxruntime as ort\n"
        "def a(p):\n    return ort.InferenceSession(p)\n"
        "def b(p):\n    return ort.InferenceSession(p, providers=['CPUExecutionProvider'])\n"
    )
    assert _unpinned_session_calls(planted) == [3], _unpinned_session_calls(planted)
    bare = ast.parse(
        "from onnxruntime import InferenceSession\n"
        "def a(p):\n    return InferenceSession(p)\n"
        "def b(p):\n    return InferenceSession(p, providers=['CPUExecutionProvider'])\n"
    )
    assert _unpinned_session_calls(bare) == [3], _unpinned_session_calls(bare)


def test_the_faces_extra_never_reaches_the_eager_import_closure():
    """`pam.enrich.faces_model` imported ON ITS OWN, in a FRESH INTERPRETER, costs neither.

    `tests/test_deps.py` asserts the whole-package closure; this names the module. The
    fresh interpreter is not decoration - by the time this file runs, the session may have
    imported numpy through Pillow or imagehash, and `sys.modules` would answer a question
    about the test run rather than about pam (`test_phash.py`'s HEIC-opener lesson).
    """
    probe = (
        "import sys, pam.enrich.faces_model\n"
        "print(int('onnxruntime' in sys.modules), int('numpy' in sys.modules))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=120,
        check=True,
    )
    assert result.stdout.split() == ["0", "0"], result.stdout


# --- 3b. the skip count is deterministic (A2) -----------------------------------------

# Every `faces`-marked test in the repository, spelled out. A2 requires each lane's skip
# count to be DETERMINISTIC - all of them or none, never some - and a count is only
# reconcilable against a number somebody wrote down. Read the names before adding
# another: a test belongs here only if it genuinely cannot run without the ~37 MB pack.
FACES_MARKED_TESTS = {
    "tests/test_faces_model.py::test_the_detector_reproduces_the_recorded_boxes_scores_and_landmarks",
    "tests/test_faces_model.py::test_the_recogniser_reproduces_the_recorded_embedding_and_would_see_a_channel_swap",
    "tests/test_faces_model.py::test_a_real_session_really_runs_on_the_cpu_provider_only",
    "tests/test_faces_model.py::test_the_real_pack_is_the_pinned_one",
    # M12 Task 4 (N9): the receipt, read off the REAL runtime's sessions.
    "tests/test_faces_model.py::test_the_real_runtime_writes_the_receipt_with_the_real_providers",
    "tests/test_net_canary.py::test_loading_a_real_model_pack_opens_no_socket_at_all",
    # M12 Task 6 (C9): the canary's faces stage - `pam enrich faces` + `pam faces cluster`.
    "tests/test_net_canary.py::test_the_enrich_stage_WITH_FACES_ON_opens_no_socket_at_all",
    # M11 Task 8 Step 11. `FacesEnricher.enrich()` imports numpy and runs both sessions,
    # so nothing that calls it can run without the pack. Everything else about that
    # enricher - both refusals in `wants()`, `available()`'s sentence, the identity
    # fallback, A6, A9, A11's floor and the `BUILDERS` wiring - is UNMARKED and runs on
    # every push, which is where a mistake actually gets caught.
    "tests/test_faces_enricher.py::test_a_real_photograph_produces_one_marker_and_one_face_matching_the_fixture",
    "tests/test_faces_enricher.py::test_the_stored_embedding_is_128_float32s_and_is_unit_LENGTH",
    "tests/test_faces_enricher.py::test_the_confidence_of_every_detection_is_inside_design_6_4s_range",
    "tests/test_faces_enricher.py::test_a_real_pack_gives_the_enricher_the_identity_the_digest_computes",
    "tests/test_faces_enricher.py::test_available_is_true_with_a_real_pack_and_names_the_two_files",
}

assert FACES_MARKED_TESTS, "the marked-test inventory is empty; the skip count is unpinned"


def test_the_faces_marked_tests_are_exactly_the_ones_written_down():
    """A2's determinism, half one: WHICH tests can skip is a decision, not an accident.

    Collected from the tree with `-m faces` and compared against a hand-written set, in
    BOTH directions. A marker added to a test that does not need the pack silently removes
    it from every push; a marker forgotten on one that does makes the faces job fail on a
    machine where it should have skipped. Neither is visible from a green run.

    NEVER SKIPS - it asks pytest what it would collect, which needs no model pack.
    """
    result = subprocess.run(
        # `test_ci_lanes.py`'s probe fixture writes and deletes a file under `tests/`; a
        # concurrent run deleting it mid-walk failed this collection (M15, found by
        # sharded mutation runs). Only that one name is ignored.
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-m",
            "faces",
            "--ignore-glob=*test_zz_ci_lane_probe_*",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout[-2000:]
    collected = {
        line.strip().replace("\\", "/").split("[")[0]
        for line in result.stdout.splitlines()
        if "::" in line
    }
    assert collected == FACES_MARKED_TESTS, (
        f"marked but not written down: {sorted(collected - FACES_MARKED_TESTS)};"
        f" written down but not marked: {sorted(FACES_MARKED_TESTS - collected)}"
    )


def test_with_no_pack_every_marked_test_skips_and_none_of_them_runs(tmp_path):
    """A2's determinism, half two: the count is ALL or NONE, measured rather than argued.

    Driven with `PAM_FACES_MODEL_DIR` pointing at a folder that is not there, so the answer
    is the same on the push lane (no extra, no pack) and in the faces job (extra and pack
    present) - which is the only way one test can speak for both lanes. What it asserts is
    that the marked set skipped ENTIRELY: not one of them ran, and the number that skipped
    is the number that exist.

    The failure this closes is a per-test `skipif`: five tests each asking a slightly
    different question produce a count between 0 and 5 that reconciles against neither
    lane, and the lane that reports `3 skipped` looks exactly like the lane that reports
    `5 skipped` to anybody reading a summary line.
    """
    env = {**os.environ, "PAM_FACES_MODEL_DIR": str(tmp_path / "not-a-pack")}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-m", "faces", "-p", "no:cacheprovider"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=900,
        env=env,
    )
    tail = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    assert f"{len(FACES_MARKED_TESTS)} skipped" in tail, tail
    assert " passed" not in tail, tail
    assert " failed" not in tail, tail


def test_a_test_whose_NAME_mentions_faces_is_not_skipped_by_the_marker_hook():
    """THE NEGATIVE CONTROL ON THE SKIP HOOK, and it is here because the hook got it wrong.

    Written first as `if "faces" in item.keywords`, the hook in `tests/conftest.py` skipped
    `test_pam_enrich_refuses_an_enricher_no_milestone_has_built_yet[faces]` - because
    `item.keywords` holds a node's NAME and its PARAMETRIZE IDS as well as its markers, and
    that test is parametrized over the enrichers pam does not ship, one of which is spelled
    `faces`. The suite was green with a refusal test silently disarmed.

    THE SUBJECT MOVED IN M11 TASK 8, AND THAT IS THE POINT WORTH READING. This control
    used to point at `test_pam_enrich_refuses_an_enricher_no_milestone_has_built_yet
    [faces]`; Task 8 SHIPPED `faces`, so that parametrize id no longer exists and the
    control would have gone on passing over a node whose id says `ocr` - proving nothing
    about a hook that collides on the word `faces` (M7's M743: a control whose subject has
    left is a control that protects nothing). It now points at an unmarked, parametrized
    test whose id IS `faces`, and that test's docstring says so from its side.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-rs",
            "tests/test_faces_enricher.py::test_every_disableable_enricher_names_the_key_that_turns_it_on",
            "-p",
            "no:cacheprovider",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout[-2000:]
    assert "skipped" not in result.stdout, result.stdout[-2000:]
    assert " passed" in result.stdout, result.stdout[-2000:]


# --- 4. `pam faces manifest` (A22) ----------------------------------------------------


def test_write_manifest_records_the_hashes_of_the_files_that_are_really_there(tmp_path):
    """A22: pack metadata gets ONE authoritative producer, so a user assembling a pack is
    not inventing `detector_input` or a channel order - the two things they cannot check
    and that fail silently when wrong."""
    directory = _fake_pack(tmp_path)
    parsed = faces_model.manifest(directory)
    assert parsed.detector == faces_model.DETECTOR_FILE
    assert parsed.detector_input == (640, 640)
    assert parsed.detector_channels == "BGR"
    assert parsed.recognizer_channels == "RGB"
    assert parsed.embedding_dim == 128
    assert parsed.embedding_l2_normalized is False
    assert "MIT" in parsed.license and "Apache-2.0" in parsed.license
    # RECORDED, NOT ASSUMED: the hash is of the bytes on disk, which is why the manifest is
    # worth checking later. Writing the pinned hash for a file that does not have it would
    # make the manifest a wish and `describe` a formality.
    assert parsed.detector_sha256 != faces_model.DETECTOR_SHA256


def test_write_manifest_refuses_a_folder_that_does_not_hold_the_pack(tmp_path):
    with pytest.raises(FacesPackError) as raised:
        faces_model.write_manifest(tmp_path)
    assert faces_model.DETECTOR_FILE in str(raised.value)
    assert "never downloads" in str(raised.value)


def test_write_manifest_ignores_a_file_it_does_not_recognise_and_says_so(tmp_path):
    """It refuses rather than guesses: writing a manifest for an unknown ONNX file would
    be pam inventing the metadata this command exists to stop the user inventing."""
    (tmp_path / "some_other_model.onnx").write_bytes(b"x")
    with pytest.raises(FacesPackError) as raised:
        faces_model.write_manifest(tmp_path)
    assert "some_other_model.onnx" in str(raised.value)


def test_the_faces_manifest_command_writes_a_pack_and_reports_it(tmp_path):
    from click.testing import CliRunner

    from pam.cli import cli

    directory = tmp_path / "pack"
    directory.mkdir(parents=True)
    (directory / faces_model.DETECTOR_FILE).write_bytes(b"a")
    (directory / faces_model.RECOGNIZER_FILE).write_bytes(b"b")
    result = CliRunner().invoke(cli, ["faces", "manifest", str(directory)])
    assert result.exit_code == 0, result.output
    assert (directory / faces_model.MANIFEST_NAME).is_file()
    assert "[enrich.faces] model_dir" in result.output


def test_the_faces_manifest_command_exits_one_on_a_folder_with_no_models(tmp_path):
    from pam.cli import main

    assert main(["faces", "manifest", str(tmp_path)]) == 1


# --- 5. golden parity, the real stack (marked) ---------------------------------------


def _nchw(image, channels: str):
    """An `H x W x 3` RGB uint8 array as the NCHW float32 batch a session wants."""
    import numpy as np

    array = np.asarray(image, dtype=np.uint8)[:, :, :3]
    if channels == "BGR":
        array = array[:, :, ::-1]
    elif channels != "RGB":  # pragma: no cover - the manifest is validated before this
        raise AssertionError(f"unknown channel order {channels!r}")
    return np.transpose(array.astype(np.float32), (2, 0, 1))[None].copy()


def _detect(session, image, threshold: float = FIXTURE_SCORE_THRESHOLD):
    """YuNet's twelve raw tensors, decoded by the spike's formulae and NMS'd.

    The graph provides NONE of this: no score, no box, no keypoint, no suppression. These
    twenty lines ARE the detector, which is why every constant in them is one of A4's
    digest inputs and why a fixture that only checked "some faces came back" would be
    checking nothing at all.
    """
    import numpy as np

    width, height = faces_model.DETECTOR_INPUT
    outputs = session.run(
        None, {faces_model.DETECTOR_INPUT_NAME: _nchw(image, faces_model.DETECTOR_CHANNELS)}
    )
    raw = dict(zip([o.name for o in session.get_outputs()], outputs, strict=True))

    found = []
    for stride in faces_model.STRIDES:
        cols = width // stride
        cls = np.asarray(raw[f"cls_{stride}"]).reshape(-1)
        obj = np.asarray(raw[f"obj_{stride}"]).reshape(-1)
        box = np.asarray(raw[f"bbox_{stride}"]).reshape(-1, 4)
        kps = np.asarray(raw[f"kps_{stride}"]).reshape(-1, 10)
        # score = sqrt(cls * obj)
        score = np.sqrt(np.clip(cls, 0.0, 1.0) * np.clip(obj, 0.0, 1.0))
        for index in np.flatnonzero(score >= threshold):
            row, col = divmod(int(index), cols)
            cx = (col + float(box[index][0])) * stride
            cy = (row + float(box[index][1])) * stride
            w = float(np.exp(box[index][2])) * stride
            h = float(np.exp(box[index][3])) * stride
            landmarks = [
                [
                    (col + float(kps[index][2 * i])) * stride,
                    (row + float(kps[index][2 * i + 1])) * stride,
                ]
                for i in range(5)
            ]
            found.append(
                {
                    "score": float(score[index]),
                    "box": [cx - w / 2.0, cy - h / 2.0, w, h],
                    "landmarks": landmarks,
                }
            )
    assert height == width, "the fixture assumes YuNet's square input"
    return _nms(found, faces_model.NMS_IOU)


def _nms(detections, iou_threshold: float):
    """Greedy NMS at the spike's IoU. Written out rather than imported: `torchvision` is
    not a dependency and never will be, and twelve lines is cheaper than a wheel."""
    kept: list[dict] = []
    for candidate in sorted(detections, key=lambda d: -d["score"]):
        if all(_iou(candidate["box"], other["box"]) <= iou_threshold for other in kept):
            kept.append(candidate)
    return kept


def _iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    left, top = max(ax, bx), max(ay, by)
    right, bottom = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    overlap = max(0.0, right - left) * max(0.0, bottom - top)
    union = aw * ah + bw * bh - overlap
    return overlap / union if union > 0 else 0.0


def _embed(session, image, box):
    """SFace over a box, L2-normalised, through `RECOGNIZER_CHANNELS`.

    THE CROP IS A PLAIN RESIZE OF THE BOX and not Task 8's five-point similarity
    alignment, deliberately: this fixture pins the SESSION and the CHANNEL ORDER, which is
    what Task 7 owes, and a crop rule that does not exist yet cannot be frozen. Task 8's
    alignment lands with its own numbers; this one stays as it is, because a fixture
    rewritten whenever the code moves is a fixture that pins nothing.
    """
    import numpy as np
    from PIL import Image

    x, y, w, h = (int(round(v)) for v in box)
    crop = (
        image.convert("RGB")
        .crop((x, y, x + w, y + h))
        .resize(faces_model.RECOGNIZER_INPUT, Image.BILINEAR)
    )
    outputs = session.run(
        [faces_model.RECOGNIZER_OUTPUT_NAME],
        {faces_model.RECOGNIZER_INPUT_NAME: _nchw(crop, faces_model.RECOGNIZER_CHANNELS)},
    )
    vector = np.asarray(outputs[0]).reshape(-1).astype(np.float64)
    return vector, float(np.linalg.norm(vector))


def _write_fixture() -> None:  # pragma: no cover - the regeneration path, run by hand
    """Regenerate `faces_parity.json` from the committed image and the current pack.

    Run deliberately, never automatically, and read the docstring at the top of this file
    before you do: a fixture regenerated to make a red test green records the bug.
    """
    from PIL import Image

    sessions = faces_model.load(model_dir())
    image = Image.open(FIXTURE_IMAGE)
    detections = _detect(sessions.detector, image)
    vector, norm = _embed(sessions.recognizer, image, detections[0]["box"])
    FIXTURE_VALUES.write_text(
        json.dumps(
            {
                "score_threshold": FIXTURE_SCORE_THRESHOLD,
                "detections": detections,
                "embedding_raw_l2_norm": norm,
                "embedding": (vector / norm).tolist(),
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def parity():
    if not STACK_READY:  # pragma: no cover - the marker skips these before the fixture
        pytest.skip("faces stack unavailable")
    from PIL import Image

    sessions = faces_model.load(model_dir())
    return sessions, Image.open(FIXTURE_IMAGE), json.loads(FIXTURE_VALUES.read_text("utf-8"))


@pytest.mark.faces
def test_the_detector_reproduces_the_recorded_boxes_scores_and_landmarks(parity):
    """Golden parity, detector half: VALUES, to a tolerance a real drift cannot hide in.

    Every number here is a product of the decode formulae in `_detect` - the score fusion,
    the two exponentials, the anchor arithmetic, the strides and the NMS threshold - and
    of the detector's channel order. Change any one of them and these move.
    """
    sessions, image, expected = parity
    found = _detect(sessions.detector, image, expected["score_threshold"])
    assert len(found) == len(expected["detections"]), (
        f"the detector found {len(found)} faces, the fixture records"
        f" {len(expected['detections'])}"
    )
    assert found, "the fixture records no detection at all, so it asserts nothing"
    for got, want in zip(found, expected["detections"], strict=True):
        assert got["score"] == pytest.approx(want["score"], abs=1e-4)
        for axis, (a, b) in enumerate(zip(got["box"], want["box"], strict=True)):
            assert a == pytest.approx(b, abs=1e-2), f"box component {axis}"
        for point, (a, b) in enumerate(zip(got["landmarks"], want["landmarks"], strict=True)):
            assert a[0] == pytest.approx(b[0], abs=1e-2), f"landmark {point} x"
            assert a[1] == pytest.approx(b[1], abs=1e-2), f"landmark {point} y"


@pytest.mark.faces
def test_the_recogniser_reproduces_the_recorded_embedding_and_would_see_a_channel_swap(parity):
    """Golden parity, recogniser half, AND the trap it exists for (A4).

    The first assertion is the pin: 128 recorded floats, elementwise. The second is the
    one that makes this test worth its 38 MB - **the same crop fed BGR instead of RGB
    scores cosine ~0.938 against the right answer**, not garbage. That is close enough to
    cluster almost right: a few identities merge, a few split, nobody notices, and the
    wrong name reaches a filename. So the swap is measured HERE, in the same test, and the
    assertion is that the pipeline's own answer is far closer to the fixture than the
    swapped one is - which fails the instant `RECOGNIZER_CHANNELS` moves, and cannot be
    satisfied by any shape-only or norm-only check.
    """
    import numpy as np

    sessions, image, expected = parity
    box = expected["detections"][0]["box"]
    vector, norm = _embed(sessions.recognizer, image, box)
    unit = vector / norm

    assert len(unit) == faces_model.EMBEDDING_DIM
    assert np.allclose(unit, np.asarray(expected["embedding"]), atol=2e-3), (
        "the embedding drifted from the recorded one"
    )
    # A3: the raw output is NOT normalised - measured L2 ~13.4. pam normalises, and a pack
    # whose manifest claimed otherwise would have pam skip a step it must not skip.
    assert faces_model.EMBEDDING_L2_NORMALIZED is False
    assert norm == pytest.approx(expected["embedding_raw_l2_norm"], rel=1e-2)
    assert norm > 2.0, "the raw embedding looks normalised; A3 measured ~13.4"

    from PIL import Image as PILImage

    x, y, w, h = (int(round(v)) for v in box)
    crop = (
        image.convert("RGB")
        .crop((x, y, x + w, y + h))
        .resize(faces_model.RECOGNIZER_INPUT, PILImage.BILINEAR)
    )
    swapped = np.asarray(
        sessions.recognizer.run(
            [faces_model.RECOGNIZER_OUTPUT_NAME],
            {faces_model.RECOGNIZER_INPUT_NAME: _nchw(crop, "BGR")},
        )[0]
    ).reshape(-1)
    swapped = swapped / np.linalg.norm(swapped)
    correct_cosine = float(np.dot(unit, np.asarray(expected["embedding"])))
    swapped_cosine = float(np.dot(swapped, np.asarray(expected["embedding"])))
    assert correct_cosine > 0.999, correct_cosine
    assert swapped_cosine < 0.99, (
        f"feeding BGR scored {swapped_cosine:.4f} against the fixture, which is not"
        " distinguishable from the right answer - the trap A4 records is not being"
        " measured by this fixture any more"
    )


@pytest.mark.faces
def test_a_real_session_really_runs_on_the_cpu_provider_only(parity):
    """A5 against the real runtime, beside the fake-runtime test that never skips.

    `get_providers()` is the constructed session's answer, not the argument pam passed, so
    this is the half the fake cannot give: it proves the runtime HONOURED the list.
    """
    sessions, _image, _expected = parity
    for session in (sessions.detector, sessions.recognizer):
        assert session.get_providers() == ["CPUExecutionProvider"], session.get_providers()


@pytest.mark.faces
def test_the_real_pack_is_the_pinned_one(parity):
    """The fixture's numbers belong to two specific files. If the pack is a different one,
    every assertion above is measuring something else and should say so here first."""
    sessions, _image, _expected = parity
    assert sessions.manifest.detector_sha256 == faces_model.DETECTOR_SHA256
    assert sessions.manifest.recognizer_sha256 == faces_model.RECOGNIZER_SHA256


# --- the controls this file must not lose ---------------------------------------------
#
# M1670: a control nobody registered is a control the next task deletes with the suite
# green. Hand-spelled, never derived from `globals()` - a list built from the module's own
# contents is satisfied by whatever the module happens to contain, which is the vacuous
# oracle this project keeps finding.

LOAD_BEARING_CONTROLS = {
    "test_load_builds_both_sessions_and_pins_the_cpu_execution_provider": (
        "A5's behavioural half, and it NEVER SKIPS: without it the provider pin is"
        " measured only in a CI job most pushes do not run"
    ),
    "test_every_onnx_session_pins_the_cpu_execution_provider": (
        "A5 over the whole package - the third session site a later task adds"
    ),
    "test_the_provider_walk_would_notice_a_session_built_without_one": (
        "the negative control on that walk; a matcher that returns [] looks identical"
    ),
    "test_describe_never_loads_a_session": (
        "watches the CALL, not the outcome: 'never built one' and 'built one and threw it"
        " away' return the same value"
    ),
    "test_describe_refuses_a_pack_whose_bytes_changed_under_its_manifest": (
        "A3: a manifest that checks only names accepts a git-LFS pointer"
    ),
    "test_the_identity_digest_moves_when_any_answer_changing_constant_moves": (
        "A4 field by field, including recognizer_channels - the 0.938 trap"
    ),
    "test_the_identity_digest_moves_when_a_constant_of_PAMS_OWN_moves": (
        "A4's other half (F1/M1747): five of the six decode constants deleted from the"
        " digest with 3041 tests and the whole faces job green"
    ),
    "test_describe_says_what_is_unset_rather_than_raising_when_model_dir_is_empty": (
        "Step 10's never-skip inventory, row 1: the commonest state of every machine,"
        " and the sentence has to name the key and say pam does not download (F2/M1753)"
    ),
    "test_the_faces_extra_never_reaches_the_eager_import_closure": (
        "I11 named at the FILE, in a fresh interpreter; the whole-package closure test"
        " reports 'the package got heavier' without saying which module did it (F2)"
    ),
    "test_no_module_under_pam_imports_onnxruntime_or_numpy_at_module_scope": (
        "keeps the optional extra optional; names the file, unlike the closure test"
    ),
    "test_the_module_scope_walk_can_see_an_import_it_is_supposed_to_catch": (
        "the negative control on that walk"
    ),
    "test_the_recogniser_reproduces_the_recorded_embedding_and_would_see_a_channel_swap": (
        "the golden-parity half that asserts VALUES; a shape check passes for the swap"
    ),
    "test_the_detector_reproduces_the_recorded_boxes_scores_and_landmarks": (
        "pins the decode formulae, the strides and the NMS threshold"
    ),
    "test_the_faces_marked_tests_are_exactly_the_ones_written_down": (
        "A2: WHICH tests may skip is a decision; both directions against a literal"
    ),
    "test_with_no_pack_every_marked_test_skips_and_none_of_them_runs": (
        "A2: the count is all-or-none, measured the same way in both lanes"
    ),
    "test_a_test_whose_NAME_mentions_faces_is_not_skipped_by_the_marker_hook": (
        "the hook really did disarm a refusal test through item.keywords; this is the"
        " control that noticed"
    ),
    "test_the_missing_weight_file_sentence_names_where_the_file_comes_from": (
        "P3 (M12 Task 4): the refusal a user most often meets names the source, whole"
    ),
    "test_load_writes_N9s_receipt_from_what_each_SESSION_reports": (
        "N9 (M12 Task 4): the receipt reads each session's get_providers(), never the list"
        " pam passed - a fake runtime that did not honour the pin is the case it exists for"
    ),
    "test_the_real_runtime_writes_the_receipt_with_the_real_providers": (
        "N9 (M12 Task 4) against the real runtime; the faces job's half"
    ),
}

assert LOAD_BEARING_CONTROLS, "this module's control list is empty; it protects nothing"


def test_no_control_in_this_file_can_be_deleted_without_saying_so():
    """The anti-deletion floor. Removing one of these is a deliberate edit to
    `LOAD_BEARING_CONTROLS`, with the reason, in the same commit."""
    present = {
        node.name
        for node in ast.parse(Path(__file__).read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef)
    }
    missing = sorted(set(LOAD_BEARING_CONTROLS) - present)
    assert not missing, (
        f"a load-bearing control was deleted or renamed: {missing}"
        f" ({[LOAD_BEARING_CONTROLS[m] for m in missing]})."
    )
