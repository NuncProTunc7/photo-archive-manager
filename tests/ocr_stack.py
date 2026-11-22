"""ONE boolean for every `ocr`-marked test, decided ONCE - and the fake session factory (R10).

THE BOOLEAN IS `tests/faces_stack.py`'s, FOR `faces_stack.py`'s REASON (A2): a lane skips ALL the
marked tests or NONE of them, so each lane's skip count is deterministic. The question - is the
`[ocr]` extra importable AND does `PAM_OCR_MODEL_DIR` hold the pinned pack? - is asked here, at
import, and `tests/conftest.py`'s table of optional stacks applies the one answer to every marked
item. `PAM_OCR_REQUIRED=1` (set by the CI `ocr` job and nowhere else) turns "unavailable" into a
collection error instead of a skip, on the M11 review's R3.

`PAM_OCR_MODEL_DIR` IS A TEST VARIABLE ONLY (Q3), like `PAM_FACES_MODEL_DIR`, and never a product
setting: the product reads `[enrich.ocr] model_dir` from `pam.toml`.

THE FAKE FACTORY drives the REAL `Engine` without onnxruntime (M12 plan rev 4.1, R10). It records
every construction, hands out sessions with scripted outputs and scripted providers, and records
every `run` - including whether two ever overlapped, which is I17's measurement.
"""

import hashlib
import os
import threading
import time
from pathlib import Path

import numpy

MODEL_DIR_VAR = "PAM_OCR_MODEL_DIR"
REQUIRED_VAR = "PAM_OCR_REQUIRED"


def _probe() -> tuple[bool, str]:
    """Is the whole OCR stack here? Extra AND pack, as one answer."""
    import importlib.util

    for module in ("onnxruntime", "numpy"):
        try:
            if importlib.util.find_spec(module) is None:
                return False, f"the optional [ocr] extra is not installed ({module})"
        except (ImportError, ValueError):
            return False, f"the optional [ocr] extra is not importable ({module})"
    raw = os.environ.get(MODEL_DIR_VAR, "").strip()
    if not raw:
        return False, f"{MODEL_DIR_VAR} is not set, so there is no OCR model pack to load"
    from pam.enrich import ocr_model

    ok, sentence = ocr_model.describe(raw)
    if not ok:
        return False, f"{MODEL_DIR_VAR}={raw}: {sentence}"
    return True, sentence


STACK_READY, STACK_REASON = _probe()


def model_dir() -> Path:
    """The pack's directory. Only ever called from an `ocr`-marked test."""
    assert STACK_READY, STACK_REASON
    return Path(os.environ[MODEL_DIR_VAR].strip())


# --- the fake pack and the fake factory (unmarked tests) ------------------------------------------


def fake_pack(directory: Path, monkeypatch) -> Path:
    """Two stand-in files, with the module's pinned hashes patched to THEIR hashes.

    The Engine validates the pack before it calls the factory, exactly as it would for real, so
    a fake factory still has to be handed a pack that passes validation.
    """
    from pam.enrich import ocr_model

    directory.mkdir(parents=True, exist_ok=True)
    for name, attr, body in (
        (ocr_model.DETECTOR_FILE, "DETECTOR_SHA256", b"fake detector"),
        (ocr_model.RECOGNIZER_FILE, "RECOGNIZER_SHA256", b"fake recognizer"),
    ):
        (directory / name).write_bytes(body)
        monkeypatch.setattr(ocr_model, attr, hashlib.sha256(body).hexdigest())
    return directory


# The recogniser's dictionary for the fake: "a", "b", "c" -> ("<blank>", "a", "b", "c", " "),
# FIVE classes, so the fake's output is [1, T, 5].
FAKE_CHARACTERS = "a\nb\nc"
FAKE_CLASSES = 5


class _Named:
    def __init__(self, name=None, shape=None):
        self.name = name
        self.shape = shape


class _Meta:
    def __init__(self, characters):
        self.custom_metadata_map = {"character": characters}


class FakeSession:
    """A session with scripted providers and a scripted `run`, recording every call."""

    def __init__(self, factory, role, providers):
        self.factory = factory
        self.role = role
        self._providers = providers

    def get_providers(self):
        return list(self._providers)

    def get_inputs(self):
        return [_Named(name="x")]

    def get_outputs(self):
        return [_Named(shape=[None, None, self.factory.classes])]

    def get_modelmeta(self):
        return _Meta(self.factory.characters)

    def run(self, output_names, feeds):
        factory = self.factory
        with factory.lock:
            factory.runs.append(self.role)
            factory.active += 1
            factory.max_active = max(factory.max_active, factory.active)
        try:
            time.sleep(factory.run_delay)
            (tensor,) = feeds.values()
            if self.role == "detector":
                return [factory.detector_map(tensor.shape[2], tensor.shape[3])]
            return [factory.recognizer_output[numpy.newaxis]]
        finally:
            with factory.lock:
                factory.active -= 1


class FakeFactory:
    """`session_factory` for the real `Engine`: records `(path, providers)` per construction.

    Its default script yields ONE line, "ab", scored 0.45 - below the plan-time floor of 0.5 on
    purpose (B6, I16): the detector's map holds one solid block, and the recogniser's four
    timesteps are "a" at 0.45, blank, "b" at 0.45, blank.
    """

    def __init__(self, *, detector_providers=None, recognizer_providers=None, fail_times=0):
        self.lock = threading.Lock()
        self.calls: list[tuple[str, list[str]]] = []
        self.runs: list[str] = []
        self.active = 0
        self.max_active = 0
        self.run_delay = 0.0
        self.fail_times = fail_times
        self.characters = FAKE_CHARACTERS
        self.classes = FAKE_CLASSES
        self.providers = {
            "detector": detector_providers or ["CPUExecutionProvider"],
            "recognizer": recognizer_providers or ["CPUExecutionProvider"],
        }
        rest = (1.0 - 0.45) / 4  # the other four classes share the rest, each below 0.45
        a, b = [rest] * FAKE_CLASSES, [rest] * FAKE_CLASSES
        a[1], b[2] = 0.45, 0.45
        blank = [0.0] * FAKE_CLASSES
        blank[0] = 1.0
        self.recognizer_output = numpy.array([a, blank, b, blank], dtype=numpy.float32)

    def detector_map(self, height, width):
        """One solid block of 0.9 in the middle third of the map: one box, well inside."""
        prob = numpy.zeros((1, 1, height, width), dtype=numpy.float32)
        prob[0, 0, height // 3 : 2 * height // 3, width // 3 : 2 * width // 3] = 0.9
        return prob

    def __call__(self, path, *, providers):
        with self.lock:
            self.calls.append((path, list(providers)))
            if self.fail_times > 0:
                self.fail_times -= 1
                raise RuntimeError("the fake factory was told to fail once")
        role = "detector" if Path(path).name.endswith("det_infer.onnx") else "recognizer"
        return FakeSession(self, role, self.providers[role])

    def constructions(self, role):
        suffix = "det_infer.onnx" if role == "detector" else "rec_infer.onnx"
        return [call for call in self.calls if call[0].endswith(suffix)]
