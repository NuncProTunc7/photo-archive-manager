"""The OCR recogniser's side of design 8.1 rev 28's pipeline: P-9 to P-11.

PURE FUNCTIONS ON ARRAYS - cropping a box out of the working image, building the recogniser's
input, and CTC-decoding its output into one `OcrLine`. Nothing here builds a session
(`ocr_model.Engine` does, R10), and nothing here filters by score: P-12 says there is NO score
filter of any kind anywhere in `Engine.read` (I16, R11), because the floor is applied at plan time
and must stay reversible in both directions.

`OcrLine` IS `pam.naming.ocr_dates.OcrLine`, imported and never redefined: one type for the
engine's answer and the grammar's input (Task 10's review).

Every constant is a module-level name spelled as the table spells it, and a digest input of
`ocr_model.identity` (Q2). Every third-party import is deferred (`numpy`, Pillow).
"""

import math
from typing import Any

from pam.enrich.ocr_detect import Box, resample, to_tensor
from pam.naming.ocr_dates import OcrLine

# --- P-9: crop -----------------------------------------------------------------------------
ROTATE_RATIO = 1.5

# --- P-10: the recogniser's input, batch 1 -------------------------------------------------
REC_H = 48
REC_MIN_W = 320
REC_CHANNELS = "BGR"  # K5(a): pam's tensor for a pure-red image equals the reference's

# --- P-11: CTC decode ----------------------------------------------------------------------
BLANK = 0


def crop(array: Any, box: Box) -> Any:
    """P-9: `working[Y0:Y1, X0:X1]`, HALF-OPEN over the rounded box - None if a side is 0.

    A crop at least `ROTATE_RATIO` times taller than wide is turned a quarter counter-clockwise
    (`numpy.rot90(k=1)`), because a tall box is most likely vertical text.
    """
    import numpy

    piece = array[box.y0 : box.y1, box.x0 : box.x1]
    if piece.shape[0] == 0 or piece.shape[1] == 0:
        return None
    if piece.shape[0] / piece.shape[1] >= ROTATE_RATIO:
        piece = numpy.rot90(piece, k=1)
    return piece


def rec_tensor(piece: Any, channels: str) -> Any:
    """P-10: `[1, 3, REC_H, W_in]`, the resized crop on the left and `0.0` padding on the right.

    `W_in = max(REC_MIN_W, int(REC_H * ratio))` and the resized width is
    `min(ceil(REC_H * ratio), W_in)`, where `ratio` is the crop's width over its height AFTER
    P-9's rotation. `channels` is the module constant the caller passes (`REC_CHANNELS`).
    """
    import numpy
    from PIL import Image

    h, w = piece.shape[0], piece.shape[1]
    ratio = w / h
    w_in = max(REC_MIN_W, int(REC_H * ratio))
    w_r = min(math.ceil(REC_H * ratio), w_in)
    resized = Image.fromarray(numpy.ascontiguousarray(piece)).resize((w_r, REC_H), resample())
    padded = numpy.zeros((1, 3, REC_H, w_in), dtype=numpy.float32)
    padded[:, :, :, :w_r] = to_tensor(resized, channels)
    return padded


def charset(metadata_text: str) -> tuple[str, ...]:
    """P-11's charset: `<blank>`, then the model's `character` metadata line by line, then " ".

    Its length must equal the recogniser's last output dimension; `ocr_model.Engine` checks that
    at initialisation and refuses a pack where it does not (R10).
    """
    return ("<blank>", *metadata_text.splitlines(), " ")


def ctc_decode(probs: Any, chars: tuple[str, ...]) -> OcrLine | None:
    """P-11 over one `[T, C]` output: greedy CTC, or None when nothing survives.

    Per timestep, `numpy.argmax` (the FIRST index on a tie) and that probability. A position is
    kept when its index differs from the previous timestep's AND is not `BLANK` - so a blank
    between two equal indices keeps both ("aa"). The text is the kept characters joined and
    `.strip()`ped; the score is the float64 MEAN of the kept probabilities, spaces included.
    """
    import numpy

    indices = numpy.argmax(probs, axis=1).tolist()
    kept_chars: list[str] = []
    kept_probs: list[float] = []
    previous = None
    for t, index in enumerate(indices):
        if index != previous and index != BLANK:
            kept_chars.append(chars[index])
            kept_probs.append(float(probs[t, index]))
        previous = index
    if not kept_chars:
        return None
    text = "".join(kept_chars).strip()
    if not text:
        return None
    return OcrLine(text, float(numpy.mean(numpy.asarray(kept_probs, dtype=numpy.float64))))
