"""The faces model pack: a manifest, two ONNX sessions, and NO WAY TO FETCH ANYTHING (A1).

REV 1 OF THIS PLAN CHOSE `insightface` AND WAS WRONG TWICE - a download-disabling API that
does not exist, and research-only weights. The answer to both was to delete the dependency.
This module holds two `onnxruntime.InferenceSession` objects built from local paths. THERE IS
NO CODE HERE THAT COULD REACH A NETWORK, which is stronger than "the download is disabled".

AND THE PROVIDER LIST IS EXPLICIT, WHICH IS NOT COSMETIC (A5). A stock onnxruntime wheel
offers ['AzureExecutionProvider', 'CPUExecutionProvider'], and Azure EP executes models
against a REMOTE endpoint. `InferenceSession(path)` with no `providers=` takes whatever is
offered. On a product whose vision S4 is zero egress, provider selection is a safety decision.

EVERY THIRD-PARTY IMPORT IS DEFERRED, on `enrich/phash.py`'s measured precedent.
"""

import hashlib
import importlib
import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pam.errors import PamError
from pam.install_method import pip_extra

# --- the pack's shape -----------------------------------------------------------------

MANIFEST_NAME = "pack.toml"
MANIFEST_SCHEMA_VERSION = 1

# A5, and the reason it is a module constant rather than an inline literal: every session
# this package builds must pass the SAME list, and a list written twice is a list that
# disagrees with itself once. `tests/test_faces_model.py` walks the AST of `pam/` and
# refuses an `InferenceSession(...)` without an explicit `providers=` keyword - that guard
# needs no model pack and therefore never skips.
PROVIDERS = ("CPUExecutionProvider",)

# A4's `model_name`. It names the STACK, not the file: two packs holding different bytes are
# distinguished by `model_version`, which is a digest, never by this string.
MODEL_NAME = "yunet+sface"

# pam's own face-pipeline version, and it is one of A4's digest inputs. It moves when pam
# changes an answer WITHOUT the pack changing - a different crop rule, a different
# normalisation, a different NMS implementation - which is the case a hash over the weights
# cannot see. It is not a release number; do not bump it for a comment.
PIPELINE_VERSION = "1"

# --- the measured constants (the spike, sections 2 and 3) -----------------------------
#
# Every one of these was READ OFF THE GRAPH or measured against OpenCV's own wrappers on a
# real three-face photograph (detector 0.000 px / 0.00000 score, recogniser cosine
# 1.00000012). None of it is inferred from documentation, and none of it is a tunable.

DETECTOR_FILE = "face_detection_yunet_2023mar.onnx"
DETECTOR_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
# FIXED BY THE GRAPH, NOT A KNOB (A3). YuNet declares `[1, 3, 640, 640]` with ZERO dynamic
# dimensions. OpenCV's `FaceDetectorYN.setInputSize()` works because cv2 RESHAPES the graph;
# raw onnxruntime cannot, and this milestone does no graph surgery. A pack whose manifest
# says anything else describes a different file.
DETECTOR_INPUT = (640, 640)
DETECTOR_INPUT_NAME = "input"
DETECTOR_CHANNELS = "BGR"

RECOGNIZER_FILE = "face_recognition_sface_2021dec.onnx"
RECOGNIZER_SHA256 = "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"
RECOGNIZER_INPUT = (112, 112)
RECOGNIZER_INPUT_NAME = "data"
RECOGNIZER_OUTPUT_NAME = "fc1"
# NOT A TYPO, AND IT IS THE MILESTONE'S NASTIEST TRAP (A4). The detector consumes BGR and
# the recogniser consumes RGB - one pipeline, two channel orders. Feeding SFace BGR (the
# obvious thing, since the detector took BGR) yields COSINE 0.938 AGAINST THE CORRECT
# EMBEDDING, NOT GARBAGE: close enough to cluster almost right, and every test that only
# asks "did a 128-vector come back" passes. `tests/test_faces_model.py`'s golden-parity
# fixture asserts VALUES for exactly this reason.
RECOGNIZER_CHANNELS = "RGB"

EMBEDDING_DIM = 128
EMBEDDING_DTYPE = "float32"
# Measured: the raw `fc1` vector has L2 norm ~13.377, not 1.0. pam normalises before any
# cosine distance. Design 4's `-- float32[512]` comment is wrong for this stack (A3).
EMBEDDING_L2_NORMALIZED = False

LICENSE = "YuNet: MIT; SFace: Apache-2.0"
SOURCE = "https://github.com/opencv/opencv_zoo"

# The decode the graph does not do. YuNet emits twelve raw tensors across three strides and
# none of the geometry; these four lines ARE the detector, and a change to any of them
# changes every box pam has ever stored, which is why they are digest inputs (A4).
STRIDES = (8, 16, 32)
SCORE_FUSION = "sqrt(cls*obj)"
BOX_DECODE = "cx=(col+b0)*s cy=(row+b1)*s w=exp(b2)*s h=exp(b3)*s"
KEYPOINT_DECODE = "kp_i=((col+k[2i])*s,(row+k[2i+1])*s)"
NMS_IOU = 0.3

# (distribution, import name), in `enrich/phash.py`'s shape and for its reason: the
# distribution name is what a user types to install it, so it is what a refusal must say.
_REQUIREMENTS = (
    ("onnxruntime", "onnxruntime"),
    ("numpy", "numpy"),
)

# What `write_manifest` knows about a file it is handed. A user assembling a pack should not
# have to invent `detector_input` or a channel order (A22); they downloaded two files.
_KNOWN_FILES: dict[str, dict[str, Any]] = {
    DETECTOR_FILE: {
        "role": "detector",
        "sha256": DETECTOR_SHA256,
        "input": list(DETECTOR_INPUT),
        "channels": DETECTOR_CHANNELS,
    },
    RECOGNIZER_FILE: {
        "role": "recognizer",
        "sha256": RECOGNIZER_SHA256,
        "input": list(RECOGNIZER_INPUT),
        "channels": RECOGNIZER_CHANNELS,
    },
}


class FacesPackError(PamError):
    """The pack is missing, malformed, or does not hash to what its manifest claims."""


class FacesUnavailableError(PamError):
    """The `[faces]` extra is not installed. Carries the distribution name, not an ImportError."""


@dataclass(frozen=True)
class Manifest:
    """A3's `pack.toml`, parsed. Every field is an answer-changing constant except the last two."""

    schema_version: int
    detector: str
    detector_sha256: str
    detector_input: tuple[int, int]
    detector_channels: str
    recognizer: str
    recognizer_sha256: str
    recognizer_input: tuple[int, int]
    recognizer_channels: str
    embedding_dim: int
    embedding_dtype: str
    embedding_l2_normalized: bool
    license: str
    source: str


@dataclass(frozen=True)
class Sessions:
    """The two loaded sessions, with the manifest that describes them and A4's version.

    `model_version` rides along (M12 Task 4, N9) so the identity `load` wrote into the
    receipt is the one `enrich/faces.py` hands the closing block - one computation, not two.
    """

    detector: Any
    recognizer: Any
    manifest: Manifest
    model_version: str


_REQUIRED_KEYS = (
    "schema_version",
    "detector",
    "detector_sha256",
    "detector_input",
    "detector_channels",
    "recognizer",
    "recognizer_sha256",
    "recognizer_input",
    "recognizer_channels",
    "embedding_dim",
    "embedding_dtype",
    "embedding_l2_normalized",
    "license",
    "source",
)


def _pack_dir(model_dir: str | Path) -> Path:
    text = str(model_dir).strip()
    if not text:
        raise FacesPackError(
            "[enrich.faces] model_dir is not set, so pam does not know where the face"
            " model pack is. pam never downloads a model: put the YuNet and SFace ONNX"
            f" files in a folder with a {MANIFEST_NAME} beside them (pam faces manifest"
            " <folder> writes one) and set model_dir to that folder."
        )
    return Path(text).expanduser()


def manifest(model_dir: str | Path) -> Manifest:
    """Parse `<model_dir>/pack.toml`. Structure only - the hashes are `describe`'s job.

    Raises `FacesPackError` naming the file and the key, never a `KeyError` or a
    `tomllib.TOMLDecodeError`: a hand-assembled pack is a thing users get wrong, and the
    only useful answer names what is wrong with it.
    """
    path = _pack_dir(model_dir) / MANIFEST_NAME
    if not path.is_file():
        raise FacesPackError(
            f"no {MANIFEST_NAME} in {path.parent}. Run: pam faces manifest {path.parent}"
        )
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as error:
        raise FacesPackError(f"{path} is not readable TOML: {error}") from error
    missing = [key for key in _REQUIRED_KEYS if key not in data]
    if missing:
        raise FacesPackError(f"{path} is missing: {', '.join(missing)}")
    if data["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise FacesPackError(
            f"{path} declares schema_version {data['schema_version']!r};"
            f" this pam understands {MANIFEST_SCHEMA_VERSION}"
        )
    try:
        return Manifest(
            schema_version=int(data["schema_version"]),
            detector=str(data["detector"]),
            detector_sha256=str(data["detector_sha256"]),
            detector_input=(int(data["detector_input"][0]), int(data["detector_input"][1])),
            detector_channels=str(data["detector_channels"]),
            recognizer=str(data["recognizer"]),
            recognizer_sha256=str(data["recognizer_sha256"]),
            recognizer_input=(
                int(data["recognizer_input"][0]),
                int(data["recognizer_input"][1]),
            ),
            recognizer_channels=str(data["recognizer_channels"]),
            embedding_dim=int(data["embedding_dim"]),
            embedding_dtype=str(data["embedding_dtype"]),
            embedding_l2_normalized=bool(data["embedding_l2_normalized"]),
            license=str(data["license"]),
            source=str(data["source"]),
        )
    except (TypeError, ValueError, IndexError, KeyError) as error:
        raise FacesPackError(f"{path} has a malformed value: {error}") from error


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def describe(model_dir: str | Path) -> tuple[bool, str]:
    """Is this pack usable? A sentence either way. **NEVER LOADS A SESSION.**

    THAT IS THE WHOLE REASON IT IS NOT `load`. `pam doctor`, the refusal paths and the
    review UI all need to answer "is this pack usable?" without paying 38 MB of model load
    and without constructing anything that could select an execution provider. A `describe`
    that quietly loaded would make every one of those callers pay for an answer they did
    not ask for, and would put a provider decision on a path that has no business making
    one.

    AND IT VERIFIES BOTH HASHES (A3). A manifest that checks only names is worth nothing:
    the file it describes could be any bytes at all, including a git-LFS pointer, which is
    exactly what the `raw.githubusercontent` download path returns.
    """
    try:
        parsed = manifest(model_dir)
    except FacesPackError as error:
        return False, str(error)
    directory = _pack_dir(model_dir)
    for role, name, expected in (
        ("detector", parsed.detector, parsed.detector_sha256),
        ("recognizer", parsed.recognizer, parsed.recognizer_sha256),
    ):
        path = directory / name
        if not path.is_file():
            # P3 (M12 Task 4): the branch a user is most likely to hit names where the file
            # comes from, as `write_manifest`'s already does - pam knows and must say so.
            return False, (
                f"{MANIFEST_NAME} names a {role} {name} that is not in {directory};"
                f" get it from {SOURCE} (pam never downloads a model)."
            )
        found = _sha256(path)
        if found != expected:
            return (
                False,
                f"{name} does not hash to what {MANIFEST_NAME} says it should"
                f" (expected {expected}, found {found}); the file is not the one this"
                " pack describes",
            )
    return True, (
        f"{MODEL_NAME} pack in {directory}: {parsed.detector} +"
        f" {parsed.recognizer}, {parsed.embedding_dim}-d embeddings, {parsed.license}"
    )


def import_runtime() -> tuple[Any, Any]:
    """Import `onnxruntime` and `numpy`, DEFERRED, and name the distribution on failure.

    PUBLIC BECAUSE `enrich/faces.py` NEEDS BOTH HALVES OF IT (M11 Task 8 Step 7), and this
    is the only additive change that task makes to Task 7's module. `FacesEnricher.
    available()` has to answer "is the extra installed?" WITHOUT loading a session -
    `describe` deliberately does not - and the enricher needs numpy for the decode
    arithmetic. A second `find_spec` walk in `faces.py` would be a second implementation of
    the one question this function exists to answer, and the two would disagree the day
    `_REQUIREMENTS` gains a third entry.
    """
    modules = {}
    for distribution, module_name in _REQUIREMENTS:
        try:
            modules[module_name] = importlib.import_module(module_name)
        except ImportError as error:
            raise FacesUnavailableError(
                f"{distribution} is not installed ({error}). Face detection is an optional"
                f" extra. Run: {pip_extra('faces')}"
            ) from error
    return modules["onnxruntime"], modules["numpy"]


def load(model_dir: str | Path, logger: logging.Logger | None = None) -> Sessions:
    """Build both sessions from local paths. The only place in pam a model is loaded.

    `describe` runs first, so a pack whose bytes do not match its manifest is refused
    before anything is handed to onnxruntime.

    N9'S RECEIPT (design 12 rev 26, built in M12 Task 4). Once both sessions exist, one INFO
    line names the model identity, what EACH session reports from `get_providers()` - the
    runtime's answer, not the list pam passed - and what the runtime offers. A stock wheel
    offers `AzureExecutionProvider` (A5); `PROVIDERS` is the promise and this line is the
    receipt a person holding a real pack can read in the session log.
    """
    ok, sentence = describe(model_dir)
    if not ok:
        raise FacesPackError(sentence)
    onnxruntime, _numpy = import_runtime()
    parsed = manifest(model_dir)
    directory = _pack_dir(model_dir)
    # A5. `providers=` is passed EXPLICITLY at both sites, and it is the same list at both.
    # Dropping it here would not fail, would not warn, and would let a stock wheel offer
    # AzureExecutionProvider - a provider that executes the model against a remote endpoint.
    detector = onnxruntime.InferenceSession(
        str(directory / parsed.detector), providers=list(PROVIDERS)
    )
    recognizer = onnxruntime.InferenceSession(
        str(directory / parsed.recognizer), providers=list(PROVIDERS)
    )
    model_name, model_version = identity(model_dir)
    (logger or logging.getLogger("pam")).info(
        "faces model %s %s; detector providers %s; recognizer providers %s; runtime offers %s",
        model_name,
        model_version,
        list(detector.get_providers()),
        list(recognizer.get_providers()),
        list(onnxruntime.get_available_providers()),
    )
    return Sessions(
        detector=detector, recognizer=recognizer, manifest=parsed, model_version=model_version
    )


def identity(model_dir: str | Path) -> tuple[str, str]:
    """A4: `(model_name, model_version)`, where the version is a digest over every constant
    that can change an answer - NEVER a directory basename.

    Two directories with the same name can hold different bytes, and a cache keyed on the
    name would serve one pack's embeddings as the other's. So the digest covers both file
    hashes, the detector's fixed input size, BOTH CHANNEL ORDERS, the score fusion, the box
    and keypoint decode formulae, the NMS threshold, the embedding's width, dtype and
    normalisation state, and pam's own pipeline version.

    THE CHANNEL ORDERS ARE IN HERE FOR THE SAME REASON THEY ARE IN THE MANIFEST. A pack
    that swapped them would produce embeddings 0.938 away from the ones already stored -
    close enough that nothing would look broken and every cluster would be slightly wrong.
    """
    parsed = manifest(model_dir)
    payload = "\n".join(
        (
            f"pipeline={PIPELINE_VERSION}",
            f"detector_sha256={parsed.detector_sha256}",
            f"detector_input={parsed.detector_input[0]}x{parsed.detector_input[1]}",
            f"detector_channels={parsed.detector_channels}",
            f"recognizer_sha256={parsed.recognizer_sha256}",
            f"recognizer_input={parsed.recognizer_input[0]}x{parsed.recognizer_input[1]}",
            f"recognizer_channels={parsed.recognizer_channels}",
            f"strides={','.join(str(s) for s in STRIDES)}",
            f"score={SCORE_FUSION}",
            f"box={BOX_DECODE}",
            f"keypoints={KEYPOINT_DECODE}",
            f"nms_iou={NMS_IOU}",
            f"embedding_dim={parsed.embedding_dim}",
            f"embedding_dtype={parsed.embedding_dtype}",
            f"embedding_l2_normalized={parsed.embedding_l2_normalized}",
        )
    )
    return MODEL_NAME, hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def write_manifest(directory: str | Path) -> Path:
    """A22: hash the ONNX files in `directory` and write `pack.toml` beside them.

    A user assembling a pack by hand should not be inventing `detector_input` or a channel
    order - those are properties of the files they downloaded, and getting either wrong is
    silent. This command is pack metadata's one authoritative producer.

    IT REFUSES A FILE IT DOES NOT RECOGNISE rather than guessing. pam knows two ONNX files;
    writing a manifest for a third would be pam inventing the metadata it exists to stop the
    user inventing. **It does not check the hashes against the pinned ones** - it RECORDS
    what is there, and `describe` is what later refuses a file that has changed underneath
    the manifest. Recording a hash the file does not have would make the manifest a wish.
    """
    path = _pack_dir(directory)
    if not path.is_dir():
        raise FacesPackError(f"{path} is not a folder")
    found: dict[str, dict[str, Any]] = {}
    unknown: list[str] = []
    for entry in sorted(path.glob("*.onnx")):
        known = _KNOWN_FILES.get(entry.name)
        if known is None:
            unknown.append(entry.name)
            continue
        found[known["role"]] = {**known, "name": entry.name, "sha256": _sha256(entry)}
    missing = [
        name
        for name, spec in ((DETECTOR_FILE, "detector"), (RECOGNIZER_FILE, "recognizer"))
        if spec not in found
    ]
    if missing:
        raise FacesPackError(
            f"{path} does not hold the face model pack. Missing: {', '.join(missing)}."
            f" pam never downloads a model; get both files from {SOURCE} (or its"
            " HuggingFace mirrors) and put them in this folder."
            + (f" Not recognised, and ignored: {', '.join(unknown)}." if unknown else "")
        )
    detector, recognizer = found["detector"], found["recognizer"]
    text = "\n".join(
        (
            "# Written by `pam faces manifest`. The hashes are of the files as they were",
            "# when this was written; pam refuses the pack if they stop matching.",
            f"schema_version = {MANIFEST_SCHEMA_VERSION}",
            f'detector = "{detector["name"]}"',
            f'detector_sha256 = "{detector["sha256"]}"',
            f"detector_input = [{detector['input'][0]}, {detector['input'][1]}]",
            f'detector_channels = "{detector["channels"]}"',
            f'recognizer = "{recognizer["name"]}"',
            f'recognizer_sha256 = "{recognizer["sha256"]}"',
            f"recognizer_input = [{recognizer['input'][0]}, {recognizer['input'][1]}]",
            f'recognizer_channels = "{recognizer["channels"]}"',
            f"embedding_dim = {EMBEDDING_DIM}",
            f'embedding_dtype = "{EMBEDDING_DTYPE}"',
            f"embedding_l2_normalized = {str(EMBEDDING_L2_NORMALIZED).lower()}",
            f'license = "{LICENSE}"',
            f'source = "{SOURCE}"',
            "",
        )
    )
    target = path / MANIFEST_NAME
    target.write_text(text, encoding="utf-8")
    return target
