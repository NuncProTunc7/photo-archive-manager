"""Design 8.1's `faces` enricher: YuNet detection and SFace embedding, per photograph.

WHAT THIS MODULE OWNS AND WHAT IT DOES NOT. `faces_model.py` owns the pack - the
manifest, the hashes, the two sessions, the provider pin and A4's identity digest - and
this module owns the ARITHMETIC THE GRAPH DOES NOT DO: the score fusion, the anchor
decode, non-maximum suppression, the crop, and the L2 normalisation the raw `fc1` vector
does not come with. Every constant either path uses lives in `faces_model.py` and is
CONSUMED here rather than re-typed, because a number spelled twice is a number that
disagrees with itself once and A4's digest would then be a digest of one of the two.

WHAT IT EMITS (A7). One `faces` MARKER per file, `{"count": N}` including N = 0, and one
`face` fact per detection carrying A13's geometry document and the embedding's raw
float32 bytes. The marker is what makes a landscape COVERED (I8); without it every
faceless photograph is decoded again on every run for ever. `sink.py` routes the `face`
facts to `face_instances` and overwrites the marker's count with what it actually
accepted, so nothing this module claims can outrun what was stored (A8).

NO FLOOR IS APPLIED HERE, AND THAT IS AMENDMENT A11. Every structurally valid detection
is stored with its confidence; `[tiers] faces` floor is applied when detections are READ
(`repo.faces_for_clustering`, `repo.cluster_faces`), so raising and lowering it are both
reversible and neither costs a re-enrichment. `CANDIDATE_SCORE` below is NOT that floor -
see its comment.

EVERY THIRD-PARTY IMPORT IS DEFERRED, on `phash.py`'s measured precedent: `pam --version`
must not pay for onnxruntime, numpy or Pillow, and `tests/test_deps.py`'s eager closure is
`{click, pam}` because of it. `[faces]` is an OPTIONAL extra (A2), so on most machines
these imports would not resolve at all and a module-scope one would break `pam` entirely.

THE DECODED RASTER IS THE SHARED DECODER'S (I4, A18). Detection, the crop this module
embeds, and the crop the review UI renders in Task 10 all resolve through
`base.pil_image_loader`. Measured at the spike: on a real HEIC, `-ThumbnailImage`,
`-PreviewImage` and `-JpgFromRaw` all return ZERO BYTES while Pillow decodes the full
4032x3024 raster, so an extraction-based crop path would render nothing for 92 % of the
a real-world library while detection saw everything.
"""

import importlib
import logging
import threading
from typing import Any

from pam.enrich import faces_model
from pam.enrich.base import FileRecord, InferredFact, PILImageLoader
from pam.enrich.upright import GEOMETRY_VERSION as GEOMETRY_VERSION
from pam.enrich.upright import upright

NAME = "faces"

# A4's identity when the pack is not there. `available()` is what stops it ever reaching
# the catalog: the runner reads `model_version` only after `available()` has returned
# True, and by then `__init__`'s `identity()` call has succeeded. It is spelled as an
# obviously-not-a-digest string so that a row carrying it would be recognisable as a bug
# rather than mistaken for a generation.
UNKNOWN_VERSION = "no-pack"

# Per-image cost for the ETA's prior (design 8.1). A decode, a 640x640 detector pass and
# one SFace pass per face is high hundreds of milliseconds on this machine; the runner
# replaces this with a measured moving average after twenty files, so it only has to be
# the right order of magnitude.
COST_ESTIMATE_MS = 700

# A13's geometry document version, re-exported from `upright.py` since M11 Task 10. The
# document is `{"v": 1, "bbox": [...], "landmarks": [...], "source_w": ..., "source_h":
# ..., "orientation": ...}` and every coordinate in it is NORMALISED to the source raster
# - so a box measured against one decoding selects the same pixels in another, which is
# what the crop endpoint needs (A18) and what a bare pixel box could not give it.
#
# IT MOVED BECAUSE THE FRAME MOVED. `upright.py` now owns the transpose this module used
# to perform inline, so that `webui/crop.py` cuts the SAME raster detection measured (I4);
# the version of the document those two agree about belongs beside that function, not
# beside the enricher that happens to be its first writer.
#
# (The name is IMPORTED above rather than respelled here - one value, one definition.)

# THE DECODE'S NOISE FLOOR, AND IT IS NOT A11's FLOOR. YuNet scores 8,400 anchors per
# image and the overwhelming majority of them are noise; something has to decide what
# counts as a DETECTION at all, or every photograph would store thousands of rows and A11
# would be reversible over garbage.
#
# MEASURED, on `tests/fixtures/faces_parity.png` (one drawn face) with the pack this
# milestone pins: threshold 0.02 -> 5 detections after NMS, 0.05 -> 2, 0.10 -> 1. The real
# face scores 0.912; the best noise anchor scores 0.052.
#
# 0.10 sits an order of magnitude below the real face, above the noise, and WELL BELOW
# `[tiers] faces` floor's default of 0.4 - which is the property that matters, because a
# candidate threshold at or above the floor would make lowering the floor recover nothing
# and quietly undo A11.
#
# IT IS AN ANSWER-CHANGING CONSTANT: moving it changes which rows exist. It is not in
# `faces_model.identity()`'s payload (that digest is Task 7's and is frozen), and it does
# not need to be - `faces_model.PIPELINE_VERSION` is the digest input that exists for
# exactly this, "pam changes an answer WITHOUT the pack changing". CHANGE ONE AND CHANGE
# THE OTHER.
CANDIDATE_SCORE = 0.10

# The five points YuNet returns per face, in its own order (right eye, left eye, nose,
# right mouth corner, left mouth corner). Named so that the geometry document's
# `landmarks` list has a declared length rather than whatever the tensor reshape produced.
LANDMARK_COUNT = 5


class FacesEnricher:
    """Design 8.1's `faces`. Satisfies `enrich.Enricher`; the runner owns everything else.

    `model_dir` is `[enrich.faces] model_dir` (design 11), handed in by
    `command.BUILDERS` exactly as the captioner's port and model are - an enricher the
    runner had to configure specially is what the Protocol's docstring forbids.

    THE IDENTITY IS RESOLVED AT CONSTRUCTION AND THE SESSIONS ARE NOT. `identity()` parses
    a small TOML; `load()` maps ~37 MB of weights and builds two onnxruntime sessions, and
    doing that in `__init__` would make `pam enrich` pay for it before `available()` had
    even been asked, and would put a provider decision on a path that has no business
    making one (`faces_model.describe`'s own docstring). So construction is cheap and
    total - it CANNOT raise for a missing or malformed pack - and the sessions are built
    on the first `enrich()` call, under a lock because `[enrich.faces] workers` may be
    more than one.
    """

    name = NAME

    def __init__(self, model_dir: str, logger: logging.Logger | None = None) -> None:
        self._model_dir = model_dir
        self._log = logger or logging.getLogger("pam")
        self._sessions: faces_model.Sessions | None = None
        self._lock = threading.Lock()
        # N9 (M12 Task 4): `model_name model_version` of the pack THIS RUN LOADED, set in
        # `_loaded` and read by `enrich/command.py` for its closing block. Not on design
        # 8.1's Protocol: the command holds this concrete enricher and asks it directly.
        # None until a session is built - a run that enriched nothing loaded nothing.
        self.loaded_model: str | None = None
        try:
            self.model_name, self.model_version = faces_model.identity(model_dir)
        except faces_model.FacesPackError:
            # `available()` is the gate, and it asks the same question with the hashes
            # checked as well. This branch exists so that BUILDING the enricher on a
            # machine with no pack is not an error - which is every machine until the
            # user puts two files somewhere and says where.
            self.model_name, self.model_version = faces_model.MODEL_NAME, UNKNOWN_VERSION

    def available(self) -> tuple[bool, str]:
        """Design 8.1's dependency check: the extra AND the pack, as one sentence.

        Both halves, in the order a user hits them. Without `[faces]` there is no
        onnxruntime to load a pack with, so the sentence that names the extra comes first;
        with the extra and no pack, the sentence names `model_dir`. Neither loads a
        session - `describe` verifies the two SHA-256 hashes and stops there.
        """
        try:
            faces_model.import_runtime()
        except faces_model.FacesUnavailableError as error:
            return False, str(error)
        return faces_model.describe(self._model_dir)

    def cost_estimate_ms(self) -> int:
        return COST_ESTIMATE_MS

    def wants(self, file: FileRecord) -> bool:
        """Images that are really on this disk. `phash`'s two refusals, for its reasons.

        A CLOUD PLACEHOLDER IS REFUSED BECAUSE READING ONE HYDRATES IT, which vision R2
        forbids absolutely - the file is a pointer, and opening it makes OneDrive download
        gigabytes the user did not ask for. This is a decision about the ACT of reading,
        not about the answer, so `tests/test_faces_enricher.py` watches the OPEN rather
        than the facts: a refusal that read the bytes and then returned an empty list
        would look identical from the outside. M8's M697 is the precedent and it is not a
        hypothetical - a mis-ordered placeholder refusal there read a cloud placeholder's
        bytes and returned the same 404 the correct order returns, with 1718 tests green.

        VIDEOS ARE REFUSED because design 8.1 rev 3 says every pixel-reading enricher
        does: no video decoding exists in v1.0. It is written as an ALLOWLIST of `image`
        rather than a denylist of `video` so that a media type nobody has thought of yet -
        and `sidecar` and `other`, which pam does not decode at all - default to not being
        fed to a decoder.
        """
        if file.placeholder:
            return False
        return file.media_type == "image"

    def enrich(self, file: FileRecord, image: PILImageLoader) -> list[InferredFact]:
        """A marker, plus one `face` fact per detection. `image` is the runner's loader.

        THE ORIENTATION-CORRECTED RASTER IS THE ONE EVERYTHING SEES (I4, A13). Pillow does
        not apply the EXIF orientation on `open()`, so a phone photograph decodes sideways;
        detection on a sideways raster and a crop rendered from an upright one would
        disagree about where a face is. `upright.upright` applies the transpose and records
        the ORIGINAL tag value in the geometry document so a later reader knows which
        raster the normalised coordinates belong to - and it is a SHARED function rather
        than four lines here because `webui/crop.py` calls the same one (M11 Task 10). Two
        spellings of "turn it the right way up" is one of them being wrong.

        NO FLOOR (A11). Every detection above `CANDIDATE_SCORE` is returned with its score
        as the fact's confidence, and `[tiers] faces` floor is applied when they are read.
        """
        numpy = _numpy()
        sessions = self._loaded()
        with image(file) as decoded:
            frame = upright(decoded)
            orientation = frame.orientation
            source_w, source_h = frame.width, frame.height
            rgb = frame.raster.convert("RGB")
            detections = _detect(numpy, sessions, rgb)
            facts: list[InferredFact] = [
                InferredFact(kind="faces", value={"count": len(detections)})
            ]
            for score, box, landmarks in detections:
                blob = _embed(numpy, sessions, rgb, box)
                if blob is None:
                    self._log.warning(
                        "enrich faces: %s - a detection embedded to a zero vector and was"
                        " dropped; it carries no usable identity",
                        file.rel_path,
                    )
                    continue
                facts.append(
                    InferredFact(
                        kind="face",
                        value={
                            "geometry": {
                                "v": GEOMETRY_VERSION,
                                "bbox": list(box),
                                "landmarks": [list(point) for point in landmarks],
                                "source_w": source_w,
                                "source_h": source_h,
                                "orientation": orientation,
                            },
                            "embedding": blob,
                        },
                        confidence=score,
                    )
                )
        # A8 counts what the SINK accepted, not what this returns, so the marker's count
        # is overwritten downstream. It is filled in truthfully here anyway: a marker that
        # arrived wrong and was corrected is a marker nobody can reason about, and one of
        # the mutations this task owes is to check that the correction is what fires.
        facts[0] = InferredFact(kind="faces", value={"count": len(facts) - 1})
        return facts

    def _loaded(self) -> faces_model.Sessions:
        """Both sessions, built once. The lock is `[enrich.faces] workers` > 1."""
        with self._lock:
            if self._sessions is None:
                self._sessions = faces_model.load(self._model_dir, self._log)
                self.loaded_model = f"{faces_model.MODEL_NAME} {self._sessions.model_version}"
            return self._sessions


def _numpy() -> Any:
    """numpy, deferred. `faces_model.import_runtime` has already named the extra."""
    return faces_model.import_runtime()[1]


def _nchw(numpy: Any, image: Any, channels: str) -> Any:
    """One RGB PIL image as the NCHW float32 batch a session takes, in `channels` order.

    THE DETECTOR TAKES BGR AND THE RECOGNISER TAKES RGB - one pipeline, two orders, and
    the manifest declares both because getting it wrong is not visible. Feeding SFace BGR
    scores cosine ~0.938 against the correct embedding, which is close enough to cluster
    almost right and to pass any test that only asks whether a 128-vector came back.
    """
    array = numpy.asarray(image, dtype=numpy.uint8)[:, :, :3]
    if channels == "BGR":
        array = array[:, :, ::-1]
    return numpy.transpose(array.astype(numpy.float32), (2, 0, 1))[None].copy()


def _detect(
    numpy: Any, sessions: faces_model.Sessions, rgb: Any
) -> list[tuple[float, list[float], list[list[float]]]]:
    """YuNet's twelve raw tensors, decoded, suppressed, and NORMALISED to the raster.

    THE GRAPH PROVIDES NONE OF THIS. No score, no box, no keypoint, no suppression: the
    formulae below ARE the detector, which is why every one of them is a `faces_model`
    constant and one of A4's digest inputs, and why a test that asked only whether some
    faces came back would be checking nothing.

    THE INPUT SIZE IS A PROPERTY OF THE FILE, NOT A KNOB (design 8.1 rev 22). The shipped
    graph declares `[1, 3, 640, 640]` with zero dynamic dimensions; OpenCV's wrapper can
    call `setInputSize` because it reshapes the graph and raw onnxruntime cannot. So the
    raster is RESIZED to that square, and because a full-frame resize is linear in each
    axis independently, a coordinate normalised in the 640 frame is the SAME NUMBER as the
    coordinate normalised in the source frame - which is why the mapping back to A13's
    document is a division and not a transform. The price is aspect distortion (a 4:3
    photograph is squashed to 1:1 before detection); letterboxing is the alternative
    design 8.1 also permits, it is not measured, and adopting it would move
    `faces_model.PIPELINE_VERSION`.
    """
    width, height = faces_model.DETECTOR_INPUT
    resized = rgb.resize((width, height), _resample())
    outputs = sessions.detector.run(
        None,
        {faces_model.DETECTOR_INPUT_NAME: _nchw(numpy, resized, faces_model.DETECTOR_CHANNELS)},
    )
    raw = dict(zip([o.name for o in sessions.detector.get_outputs()], outputs, strict=True))

    found: list[tuple[float, list[float], list[list[float]]]] = []
    for stride in faces_model.STRIDES:
        cols = width // stride
        cls = numpy.asarray(raw[f"cls_{stride}"]).reshape(-1)
        obj = numpy.asarray(raw[f"obj_{stride}"]).reshape(-1)
        box = numpy.asarray(raw[f"bbox_{stride}"]).reshape(-1, 4)
        kps = numpy.asarray(raw[f"kps_{stride}"]).reshape(-1, 10)
        # `faces_model.SCORE_FUSION`, spelled: sqrt(cls * obj).
        score = numpy.sqrt(numpy.clip(cls, 0.0, 1.0) * numpy.clip(obj, 0.0, 1.0))
        for index in numpy.flatnonzero(score >= CANDIDATE_SCORE):
            row, col = divmod(int(index), cols)
            # `faces_model.BOX_DECODE`, spelled.
            cx = (col + float(box[index][0])) * stride
            cy = (row + float(box[index][1])) * stride
            w = float(numpy.exp(box[index][2])) * stride
            h = float(numpy.exp(box[index][3])) * stride
            # `faces_model.KEYPOINT_DECODE`, spelled.
            landmarks = [
                [
                    (col + float(kps[index][2 * i])) * stride / width,
                    (row + float(kps[index][2 * i + 1])) * stride / height,
                ]
                for i in range(LANDMARK_COUNT)
            ]
            found.append(
                (
                    float(score[index]),
                    [
                        (cx - w / 2.0) / width,
                        (cy - h / 2.0) / height,
                        w / width,
                        h / height,
                    ],
                    landmarks,
                )
            )
    return _nms(found, faces_model.NMS_IOU)


def _nms(
    detections: list[tuple[float, list[float], list[list[float]]]], iou_threshold: float
) -> list[tuple[float, list[float], list[list[float]]]]:
    """Greedy non-maximum suppression at the pack's IoU, highest score first.

    Written out rather than imported: `torchvision` is not a dependency and never will be,
    and a dozen lines is cheaper than a wheel. Ties in score keep the order the strides
    produced, which is deterministic because `STRIDES` and the anchor walk both are.
    """
    kept: list[tuple[float, list[float], list[list[float]]]] = []
    for candidate in sorted(detections, key=lambda d: -d[0]):
        if all(_iou(candidate[1], other[1]) <= iou_threshold for other in kept):
            kept.append(candidate)
    return kept


def _iou(a: list[float], b: list[float]) -> float:
    """Intersection over union of two `[x, y, w, h]` boxes in the same coordinate frame."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    left, top = max(ax, bx), max(ay, by)
    right, bottom = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    overlap = max(0.0, right - left) * max(0.0, bottom - top)
    union = aw * ah + bw * bh - overlap
    return overlap / union if union > 0 else 0.0


def _embed(
    numpy: Any, sessions: faces_model.Sessions, rgb: Any, box: list[float]
) -> bytes | None:
    """SFace over one normalised box, L2-NORMALISED, as raw float32 bytes.

    THE CROP COMES OFF THE FULL-RESOLUTION RASTER, not off the 640 square the detector
    saw. The box is normalised, so denormalising it against `rgb.size` selects the same
    region with every pixel the file actually has - and it is the same arithmetic the
    crop endpoint will run in Task 10, which is what I4 means by "the same pixels".

    IT IS A PLAIN RESIZE OF THE BOX, NOT A FIVE-POINT SIMILARITY ALIGNMENT, and that is
    disclosed rather than quiet. SFace's canonical usage aligns the crop against a
    reference template of five points; no such template is a measured constant of this
    pack - `faces_model.py` records the detector's decode and the two channel orders and
    nothing about alignment - and inventing the coordinates would be pam making up the
    numbers the manifest exists to stop it making up. Adopting alignment later is a
    `PIPELINE_VERSION` move, which is exactly the mechanism that exists for it.

    THE STORED VECTOR IS NORMALISED AND THE RAW ONE IS NOT. Measured at the spike: `fc1`
    comes back with an L2 norm of ~13.4, and `faces_model.EMBEDDING_L2_NORMALIZED` is
    False because that describes the MODEL. pam normalises before storing so that every
    consumer's cosine distance is a dot product and no consumer can forget the step - the
    failure mode of normalising at read time is one caller that does not.

    Returns None for a vector of zero length, which is the only input a normalisation
    cannot be performed on. That is not a "cannot happen": it is what the caller drops and
    logs, because a face with no direction carries no identity to cluster on.
    """
    width, height = rgb.size
    x, y, w, h = box
    left = max(0, int(round(x * width)))
    top = max(0, int(round(y * height)))
    right = min(width, int(round((x + w) * width)))
    bottom = min(height, int(round((y + h) * height)))
    if right <= left or bottom <= top:
        return None
    crop = rgb.crop((left, top, right, bottom)).resize(
        faces_model.RECOGNIZER_INPUT, _resample()
    )
    outputs = sessions.recognizer.run(
        [faces_model.RECOGNIZER_OUTPUT_NAME],
        {
            faces_model.RECOGNIZER_INPUT_NAME: _nchw(
                numpy, crop, faces_model.RECOGNIZER_CHANNELS
            )
        },
    )
    vector = numpy.asarray(outputs[0]).reshape(-1).astype(numpy.float32)
    norm = float(numpy.linalg.norm(vector))
    if norm == 0.0:
        return None
    return bytes((vector / norm).astype(numpy.float32).tobytes())


def _resample() -> Any:
    """Pillow's bilinear filter, deferred. One spelling, two call sites."""
    return importlib.import_module("PIL.Image").Resampling.BILINEAR
