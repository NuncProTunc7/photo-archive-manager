"""The OCR model pack and the one `Engine` that reads it: design 1 rev 29's pack, 8.1 rev 28's Q2.

THERE IS NO OCR LIBRARY (M12 plan rev 4). The models are a pack of two PP-OCRv4 ONNX files the
USER places in `[enrich.ocr] model_dir`, identified by SHA-256 and held in pam's code, whose
recorded source is the immutable PyPI wheel `rapidocr_onnxruntime-1.4.4`. No RapidOCR code is
installed or imported, there is no manifest, and THERE IS NO CODE HERE THAT COULD REACH A NETWORK:
pam never downloads a model.

THIS IS THE ONLY MODULE THAT BUILDS AN OCR SESSION, and it builds both lazily (R10). `Engine(...)`
is cheap and stores its arguments; its first `read`, under the engine's one lock, validates the
pack, builds each session with `providers=list(PROVIDERS)` and ASSERTS each session's
`get_providers()` (A5: a stock onnxruntime offers `AzureExecutionProvider`, which executes a model
against a remote endpoint), and checks the charset against the recogniser's output. Any failure
raises and leaves the engine uninitialised, so the next `read` retries - a per-file failure (I8).
A run that reads nothing builds no session.

`PROVIDERS` IS IMPORTED FROM `faces_model`, never copied: a list written twice is a list that
disagrees with itself once (A5). Every `numpy`/`onnxruntime` import is deferred.
"""

import hashlib
import importlib.util
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pam.enrich import ocr_detect, ocr_recognize
from pam.enrich.faces_model import PROVIDERS
from pam.errors import PamError
from pam.install_method import pip_extra
from pam.naming.ocr_dates import OcrLine

DETECTOR_FILE = "ch_PP-OCRv4_det_infer.onnx"
DETECTOR_SHA256 = "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9"
RECOGNIZER_FILE = "ch_PP-OCRv4_rec_infer.onnx"
RECOGNIZER_SHA256 = "48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b"
SOURCE = (
    "the two members rapidocr_onnxruntime/models/<name> of the PyPI wheel"
    " rapidocr_onnxruntime-1.4.4-py3-none-any.whl (SHA-256"
    " 971d7d5f223a7a808662229df1ef69893809d8457d834e6373d3854bc1782cbf)"
)
LICENSE = "Apache-2.0 (PaddleOCR's README and LICENSE, and the wheel's METADATA)"
MODEL_NAME = "ppocr-v4"
# pam's own pipeline version (Q2). Any change to any row of design 8.1's pipeline table bumps it;
# it is a digest input, so a bump re-reads every file. Not a release number.
OCR_PIPELINE_VERSION = "1"

# The recogniser's dictionary lives in its own metadata under this key (design 1 rev 29).
CHARSET_KEY = "character"

# Q4's second sentence, verbatim: where the files come from, and that pam never fetches them.
PUT_THE_PACK = (
    "  Put ch_PP-OCRv4_det_infer.onnx and ch_PP-OCRv4_rec_infer.onnx from the"
    " rapidocr_onnxruntime 1.4.4 wheel on PyPI in one folder and set model_dir to it"
    " (pam never downloads a model)."
)

_REQUIREMENTS = ("onnxruntime", "numpy")


class OcrModelError(PamError):
    """The pack is unusable, or a session is not the session pam asked for. Per file (I8)."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pack_problem(model_dir: str | Path) -> str | None:
    """Q4's reason the pack is unusable, as the text after `ocr: not run - `, or None.

    Two lines: the reason (unset, a missing file, a hash mismatch) and Q4's `PUT_THE_PACK`.
    Reads the pinned hashes at CALL time, so a test can pin a pack of its own.
    """
    text = str(model_dir).strip()
    if not text:
        return f"[enrich.ocr] model_dir is not set.\n{PUT_THE_PACK}"
    directory = Path(text).expanduser()
    for name, want in ((DETECTOR_FILE, DETECTOR_SHA256), (RECOGNIZER_FILE, RECOGNIZER_SHA256)):
        path = directory / name
        if not path.is_file():
            return f"[enrich.ocr] model_dir names a folder without {name}.\n{PUT_THE_PACK}"
        got = _sha256(path)
        if got != want:
            return (
                f"[enrich.ocr] model_dir holds a {name} whose SHA-256 is {got}, not {want}."
                f"\n{PUT_THE_PACK}"
            )
    return None


def _missing_extra() -> list[str]:
    missing = []
    for module in _REQUIREMENTS:
        try:
            found = importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(module)
    return missing


def describe(model_dir: str | Path) -> tuple[bool, str]:
    """Is OCR usable here? The extra (asked with `find_spec`, NEVER imported) and the pack.

    Never builds a session. A sentence either way; the pack's sentences are Q4's, verbatim.
    """
    missing = _missing_extra()
    if missing:
        return False, (
            f"the optional [ocr] extra is not installed ({', '.join(missing)} missing)."
            f" Run: {pip_extra('ocr')}."
        )
    problem = pack_problem(model_dir)
    if problem is not None:
        return False, problem
    return True, f"{MODEL_NAME} pack in {Path(str(model_dir).strip()).expanduser()}"


def identity_rows() -> tuple[str, ...]:
    """Q2's digest inputs, one `key=value` line each: both pack hashes, `PROVIDERS`, every
    constant in design 8.1's pipeline table (the channel orders included) and
    `OCR_PIPELINE_VERSION`. Read through their modules at CALL time, never copied.
    """
    d, r = ocr_detect, ocr_recognize
    return (
        f"OCR_PIPELINE_VERSION={OCR_PIPELINE_VERSION}",
        f"DETECTOR_SHA256={DETECTOR_SHA256}",
        f"RECOGNIZER_SHA256={RECOGNIZER_SHA256}",
        f"PROVIDERS={','.join(PROVIDERS)}",
        f"MAX_SIDE={d.MAX_SIDE}",
        f"MIN_SIDE={d.MIN_SIDE}",
        f"RESAMPLE={d.RESAMPLE}",
        f"DET_LIMIT={d.DET_LIMIT}",
        f"DET_MAX_SIDE={d.DET_MAX_SIDE}",
        f"DET_CHANNELS={d.DET_CHANNELS}",
        f"THRESH={d.THRESH!r}",
        f"DILATE={d.DILATE}",
        f"CONNECTIVITY={d.CONNECTIVITY}",
        f"MAX_CANDIDATES={d.MAX_CANDIDATES}",
        f"MIN_SIZE={d.MIN_SIZE}",
        f"BOX_THRESH={d.BOX_THRESH!r}",
        f"UNCLIP={d.UNCLIP!r}",
        f"ORDER_TOL={d.ORDER_TOL}",
        f"ROTATE_RATIO={r.ROTATE_RATIO!r}",
        f"REC_H={r.REC_H}",
        f"REC_MIN_W={r.REC_MIN_W}",
        f"REC_CHANNELS={r.REC_CHANNELS}",
        f"BLANK={r.BLANK}",
    )


def identity(model_dir: str | Path) -> tuple[str, str]:
    """`("ppocr-v4", <digest>)` - the same for every folder, deliberately.

    `model_dir` is accepted for the enricher's interface and does not move the digest: a pack is
    admitted only if it hashes to the pinned files, so two admitted packs are the same bytes, and
    the pinned hashes ARE the files' hashes. Never a directory name (A4's lesson).
    """
    payload = "\n".join(identity_rows())
    return MODEL_NAME, hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _onnx_session(path: str, *, providers: list[str]) -> Any:
    """The default session factory: onnxruntime's own constructor, `providers=` EXPLICIT."""
    try:
        import onnxruntime
    except ImportError as error:
        raise OcrModelError(
            f"onnxruntime is not installed ({error}). Run: {pip_extra('ocr')}"
        ) from error
    return onnxruntime.InferenceSession(path, providers=providers)


@dataclass(frozen=True)
class _Ready:
    detector: Any
    recognizer: Any
    detector_input: str
    recognizer_input: str
    chars: tuple[str, ...]


class Engine:
    """One OCR engine per run, called one image at a time (I17).

    ONE `threading.Lock`, taken once per `read` with `with` and never re-taken by a helper,
    around initialise-if-needed and inference - so the sessions are built once however many
    workers race for the first read, `run` is never called concurrently, and a raising read
    releases the lock. Decoding, `upright` and `convert("RGB")` happen OUTSIDE it, in the
    enricher (R9), so workers still overlap their decoding. A hung native call inside `run` is
    NOT cancellable: nothing here can interrupt onnxruntime once it has the thread.
    """

    def __init__(
        self, model_dir: str | Path, *, session_factory: Callable[..., Any] | None = None
    ) -> None:
        self._model_dir = model_dir
        self._factory = session_factory or _onnx_session
        self._lock = threading.Lock()
        self._ready: _Ready | None = None
        self._receipt: str | None = None

    def read(self, rgb: Any) -> tuple[OcrLine, ...]:
        """P-1 to P-12 over an UPRIGHT RGB Pillow image. No score filter anywhere (I16, R11)."""
        with self._lock:
            if self._ready is None:
                self._ready = self._initialise()
            return _pipeline(self._ready, rgb)

    def receipt(self) -> str | None:
        """None before initialisation; afterwards the identity and each session's providers."""
        return self._receipt

    def _initialise(self) -> _Ready:
        problem = pack_problem(self._model_dir)
        if problem is not None:
            raise OcrModelError(problem)
        directory = Path(str(self._model_dir).strip()).expanduser()
        detector = self._session(directory / DETECTOR_FILE, "detector")
        recognizer = self._session(directory / RECOGNIZER_FILE, "recognizer")
        chars = ocr_recognize.charset(
            recognizer.get_modelmeta().custom_metadata_map[CHARSET_KEY]
        )
        width = recognizer.get_outputs()[0].shape[-1]
        if len(chars) != width:
            raise OcrModelError(
                f"{RECOGNIZER_FILE}'s dictionary gives {len(chars)} classes and its output has"
                f" {width}: the model and its dictionary do not belong together"
            )
        ready = _Ready(
            detector=detector,
            recognizer=recognizer,
            detector_input=detector.get_inputs()[0].name,
            recognizer_input=recognizer.get_inputs()[0].name,
            chars=chars,
        )
        name, version = identity(self._model_dir)
        # Published LAST: everything above can still raise, and B4's receipt is None until
        # initialisation has succeeded.
        self._receipt = (
            f"ocr model {name} {version}; detector providers {list(detector.get_providers())};"
            f" recognizer providers {list(recognizer.get_providers())}"
        )
        return ready

    def _session(self, path: Path, role: str) -> Any:
        session = self._factory(str(path), providers=list(PROVIDERS))
        # A5, asserted on the RUNTIME's answer rather than on the list pam passed.
        reported = list(session.get_providers())
        if reported != ["CPUExecutionProvider"]:
            raise OcrModelError(
                f"the OCR {role} session reports providers {reported}; pam runs a model on"
                " ['CPUExecutionProvider'] and nothing else"
            )
        return session


def _pipeline(ready: _Ready, rgb: Any) -> tuple[OcrLine, ...]:
    import numpy

    working = ocr_detect.working_image(rgb)
    if working is None:
        return ()
    tensor = ocr_detect.det_tensor(working, ocr_detect.DET_CHANNELS)
    if tensor is None:
        return ()
    map_shape = (int(tensor.shape[2]), int(tensor.shape[3]))
    output = ready.detector.run(None, {ready.detector_input: tensor})[0]
    prob = numpy.asarray(output)[0, 0]
    array = numpy.asarray(working)
    lines = []
    for box in ocr_detect.reading_order(
        ocr_detect.boxes(prob, map_shape, (working.height, working.width))
    ):
        piece = ocr_recognize.crop(array, box)
        if piece is None:
            continue
        feed = {
            ready.recognizer_input: ocr_recognize.rec_tensor(piece, ocr_recognize.REC_CHANNELS)
        }
        probs = numpy.asarray(ready.recognizer.run(None, feed)[0])[0]
        line = ocr_recognize.ctc_decode(probs, ready.chars)
        if line is not None:
            lines.append(line)
    return tuple(lines)
