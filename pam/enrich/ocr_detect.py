"""The OCR detector's pre- and post-processing: design 8.1 rev 28's pipeline, P-1 to P-8.

PURE FUNCTIONS ON IMAGES AND ARRAYS, and nothing here builds a session. `ocr_model.Engine` is the
only module that does (M12 plan rev 4.1, R10); this one turns a working image into the
detector's input tensor, and the detector's probability map into boxes in reading order.

THE TABLE IS THE DEFINITION. Every constant below is a module-level name spelled exactly as design
8.1's pipeline table spells it, and every one of them is a digest input of `ocr_model.identity`
(Q2): a change to any of them is a change to what pam reads off a photograph, so it must re-read.
The table is pam's OWN definition, modelled on rapidocr 1.4.4's defaults and equal to it in no
step by claim (R2); P-0 lists what is omitted on purpose, so do not "restore" any of it here.

TWO CONVENTIONS THE TABLE STATES AND A READER WILL WANT TO "FIX": a component's extrema are
INCLUSIVE (P-4), and a crop is HALF-OPEN over the rounded box (P-9, `ocr_recognize`). The
conversion between them is intentional. "round" is Python's `round`, ties to even.

EVERY THIRD-PARTY IMPORT IS DEFERRED (`numpy`, Pillow), on `faces_model.py`'s precedent, so the
eager import closure stays `{click, pam}` (`tests/test_deps.py`).
"""

from dataclasses import dataclass
from typing import Any

from pam.errors import PamError

# --- P-1: bound the size -------------------------------------------------------------------
MAX_SIDE = 2000
MIN_SIDE = 30
RESAMPLE = "BILINEAR"

# --- P-2: the detector's input (limit_type "min") ------------------------------------------
DET_LIMIT = 736
DET_MAX_SIDE = 4096
DET_CHANNELS = "BGR"  # K5(a): pam's tensor for a pure-red image equals the reference's

# --- P-3: binarise and dilate --------------------------------------------------------------
THRESH = 0.3
DILATE = "2x2 anchor (1,1)"

# --- P-4: components -----------------------------------------------------------------------
CONNECTIVITY = 8
MAX_CANDIDATES = 1000

# --- P-5 / P-6: box, score, unclip ---------------------------------------------------------
MIN_SIZE = 3
BOX_THRESH = 0.5
UNCLIP = 1.6

# --- P-8: reading order --------------------------------------------------------------------
ORDER_TOL = 10


class OcrInputError(PamError):
    """P-2: the detector's input would exceed `DET_MAX_SIDE`.

    A PER-FILE FAILURE - counted, no marker written, retried next run - and never a silent "no
    text" (R1). A thin strip that cannot be read is a file pam did not read, not a file pam read
    and found empty, and only the second is coverage (design 8.1's I8).
    """


@dataclass(frozen=True)
class Component:
    """P-4: one 8-connected component's INCLUSIVE extrema."""

    x0: int
    y0: int
    x1: int
    y1: int


@dataclass(frozen=True)
class Box:
    """P-7: one box in WORKING-IMAGE pixels, clipped. P-9 crops it HALF-OPEN."""

    x0: int
    y0: int
    x1: int
    y1: int


def resample() -> Any:
    from PIL import Image

    return getattr(Image.Resampling, RESAMPLE)


def to_tensor(image: Any, channels: str) -> Any:
    """An RGB Pillow image -> `[1, 3, H, W]` float32 in `channels` order, `(x/255 - 0.5)/0.5`.

    P-2's and P-10's shared normalisation. `channels` is the MODULE CONSTANT the caller passes
    (`DET_CHANNELS`, `ocr_recognize.REC_CHANNELS`), never a literal at a call site, so the
    solid-colour test in `tests/test_ocr_pipeline.py` kills a swap of either constant alone.
    """
    import numpy

    array = numpy.asarray(image, dtype=numpy.float32)
    if channels == "BGR":
        array = array[:, :, ::-1]
    elif channels != "RGB":
        raise ValueError(f"unknown channel order {channels!r}")
    array = (array / numpy.float32(255.0) - numpy.float32(0.5)) / numpy.float32(0.5)
    return numpy.ascontiguousarray(array.transpose(2, 0, 1)[numpy.newaxis], dtype=numpy.float32)


def working_size(h: int, w: int) -> tuple[int, int] | None:
    """P-1's target `(h, w)`, or None for "no lines". Decided BEFORE any resize."""
    if max(h, w) > MAX_SIDE:
        s = MAX_SIDE / max(h, w)
        target = (int(h * s), int(w * s))
    else:
        target = (h, w)
    if min(target) < MIN_SIDE:
        return None
    return target


def working_image(rgb: Any) -> Any:
    """P-1 over an upright RGB Pillow image: the working image, or None for "no lines"."""
    target = working_size(rgb.height, rgb.width)
    if target is None:
        return None
    if target == (rgb.height, rgb.width):
        return rgb
    return rgb.resize((target[1], target[0]), resample())


def det_size(h: int, w: int) -> tuple[int, int] | None:
    """P-2's arithmetic: the detector input's `(H, W)`, None when a side is not positive.

    TRUNCATE FIRST, then round to a multiple of 32 - the reference's order, and it matters:
    `(31, 60)` gives `(736, 1408)` this way and `(736, 1440)` rounded directly. The
    `DET_MAX_SIDE` refusal is decided HERE, before anything is resized or allocated.
    """
    r = DET_LIMIT / min(h, w) if min(h, w) < DET_LIMIT else 1.0
    h2, w2 = int(h * r), int(w * r)
    big_h = int(round(h2 / 32) * 32)
    big_w = int(round(w2 / 32) * 32)
    if big_h <= 0 or big_w <= 0:
        return None
    if max(big_h, big_w) > DET_MAX_SIDE:
        raise OcrInputError(
            f"the detector's input would be {big_w}x{big_h}, over the {DET_MAX_SIDE}-pixel"
            " limit: an image this thin cannot be read"
        )
    return big_h, big_w


def det_tensor(image: Any, channels: str) -> Any:
    """P-2 over the working image: `[1, 3, H, W]`, or None when `det_size` says no lines."""
    size = det_size(image.height, image.width)
    if size is None:
        return None
    resized = image.resize((size[1], size[0]), resample())
    return to_tensor(resized, channels)


def dilate(bitmap: Any) -> Any:
    """P-3's 2x2 dilation, anchor (1,1): `D[y,x] = B[y,x] | B[y-1,x] | B[y,x-1] | B[y-1,x-1]`.

    An out-of-range neighbour is false, so an isolated pixel grows RIGHT AND DOWN.
    """
    out = bitmap.copy()
    out[1:, :] |= bitmap[:-1, :]
    out[:, 1:] |= bitmap[:, :-1]
    out[1:, 1:] |= bitmap[:-1, :-1]
    return out


def _runs(bitmap: Any) -> tuple[list[tuple[int, int, int]], list[list[int]]]:
    """Every row's maximal runs of true pixels, half-open `[a0, a1)`, in raster order."""
    import numpy

    runs: list[tuple[int, int, int]] = []
    by_row: list[list[int]] = []
    for y in range(bitmap.shape[0]):
        padded = numpy.concatenate(([0], bitmap[y].astype(numpy.int8), [0]))
        edges = numpy.diff(padded)
        ids = []
        for a0, a1 in zip(
            numpy.flatnonzero(edges == 1).tolist(),
            numpy.flatnonzero(edges == -1).tolist(),
            strict=True,
        ):
            ids.append(len(runs))
            runs.append((y, a0, a1))
        by_row.append(ids)
    return runs, by_row


def components(bitmap: Any) -> list[Component]:
    """P-4: 8-connected components by run-length union-find, first `MAX_CANDIDATES` kept.

    A run in row y joins a run in row y-1 iff `a0 <= b1 and b0 <= a1` - overlap, or a diagonal
    touch. Union by LOWER run id, so every root is its component's lowest run id and sorting the
    roots IS P-4's order (raster order of the first pixel). The sweep over two adjacent rows
    advances past whichever run ends first: runs in one row are separated by at least one false
    pixel, so a run that ends earlier can touch nothing further along the other row.
    """
    runs, by_row = _runs(bitmap)
    parent = list(range(len(runs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for y in range(1, len(by_row)):
        above, here = by_row[y - 1], by_row[y]
        i = j = 0
        while i < len(here) and j < len(above):
            _, a0, a1 = runs[here[i]]
            _, b0, b1 = runs[above[j]]
            if a0 <= b1 and b0 <= a1:
                root_here, root_above = find(here[i]), find(above[j])
                if root_here != root_above:
                    low, high = sorted((root_here, root_above))
                    parent[high] = low
            if a1 < b1:
                i += 1
            elif b1 < a1:
                j += 1
            else:
                i += 1
                j += 1
    extrema: dict[int, list[int]] = {}
    for run_id, (y, a0, a1) in enumerate(runs):
        root = find(run_id)
        seen = extrema.get(root)
        if seen is None:
            extrema[root] = [a0, y, a1 - 1, y]
        else:
            seen[0] = min(seen[0], a0)
            seen[1] = min(seen[1], y)
            seen[2] = max(seen[2], a1 - 1)
            seen[3] = max(seen[3], y)
    return [Component(*extrema[root]) for root in sorted(extrema)[:MAX_CANDIDATES]]


def boxes(prob: Any, map_shape: tuple[int, int], work_shape: tuple[int, int]) -> list[Box]:
    """P-3 and P-5 to P-7 over the detector's map `prob` (shape `map_shape`, `(H, W)`).

    Returns boxes in WORKING-IMAGE pixels (`work_shape`, `(H_work, W_work)`), in P-4's order;
    `reading_order` is P-8. The score is the mean of the RAW map over the component's inclusive
    extent, never of the binarised one.
    """
    import numpy

    map_h, map_w = map_shape
    work_h, work_w = work_shape
    kept: list[Box] = []
    for c in components(dilate(prob > THRESH)):
        w, h = c.x1 - c.x0, c.y1 - c.y0  # P-5: contour lengths, so a single pixel is 0 x 0
        if min(w, h) < MIN_SIZE:
            continue
        score = float(numpy.mean(prob[c.y0 : c.y1 + 1, c.x0 : c.x1 + 1], dtype=numpy.float64))
        if score < BOX_THRESH:
            continue
        d = w * h * UNCLIP / (2 * (w + h))  # P-6, float64
        if min(w, h) + 2 * d < MIN_SIZE + 2:
            continue

        def back(value: float, work: int, full: int) -> int:  # P-7
            return min(max(round(value * work / full), 0), work)

        kept.append(
            Box(
                back(c.x0 - d, work_w, map_w),
                back(c.y0 - d, work_h, map_h),
                back(c.x1 + d, work_w, map_w),
                back(c.y1 + d, work_h, map_h),
            )
        )
    return kept


def reading_order(found: list[Box]) -> list[Box]:
    """P-8: stable `sorted` by `(y0, x0)`, then the `ORDER_TOL` swap pass, exactly as written."""
    ordered = sorted(found, key=lambda b: (b.y0, b.x0))
    for i in range(len(ordered) - 1):
        for j in range(i, -1, -1):
            if (
                abs(ordered[j + 1].y0 - ordered[j].y0) < ORDER_TOL
                and ordered[j + 1].x0 < ordered[j].x0
            ):
                ordered[j], ordered[j + 1] = ordered[j + 1], ordered[j]
            else:
                break
    return ordered
