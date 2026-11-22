"""Spec 8.2's `/faces/crop/{face_id}`: THE FACE, cut from the shared decoder's raster.

NEVER THE EXTRACTED EMBEDDED PREVIEW, AND THIS WAS MEASURED (A18). On a real HEIC,
`-ThumbnailImage`, `-PreviewImage` and `-JpgFromRaw` all return ZERO BYTES while Pillow
decodes the same file at 4032x3024 - and most of a phone library is HEIC. Detection
reads a DECODED image, so a crop drawn from `webui/thumbs.py`'s extraction chain would
have produced face rows for most of an archive and a blank card for every one of them.
The two paths are not variants of each other and this module deliberately shares no code
with that one.

THE FRAME IS CHECKED, NOT ASSUMED (invariant I4). The geometry stored against a face was
measured on the orientation-corrected raster `enrich/upright.py::upright` produces, and
this module cuts the raster THAT SAME FUNCTION produces from the same loader. It then
compares the recorded `source_w`, `source_h` and `orientation` against what it actually
decoded and REFUSES ON A MISMATCH rather than serving a face from the wrong place. That
refusal is the whole reason a shared function was not judged sufficient on its own: one
function makes the two paths agree today, and the check is what notices if a file, a
Pillow version or a loader branch ever makes them disagree tomorrow - and what a wrong
answer looks like here is a stranger's face under somebody's name (vision R6).

VISION R2 IS ENFORCED AGAINST THE LIVE FILESYSTEM, NOT AGAINST THE CATALOG.
`thumbs.extractable` trusts `files.placeholder` as the scanner recorded it, which is right
for a thumbnail and not enough here: scan-time metadata is not a guarantee, and OneDrive
can dehydrate a file between the scan and this request. `os.stat` reads a directory entry
WITHOUT hydrating anything, so `walker.placeholder_now(path)` (design 5.2 rev 34, V1: the
one choke point every content reader shares) immediately before the open is the check - and
a placeholder gets an explicit unavailable answer rather than being read, because reading
one hydrates it.

NOTHING HERE CACHES. Spec 8.2 permits a crop cache only if it is inventoried and
purgeable by spec 10's `pam faces purge` (A19), and neither exists; so every response
carries `Cache-Control: no-store` and the bytes are cut on demand. That is the honest
trade: a cache pam cannot enumerate is a derivative pam cannot promise to delete, and A19
is the amendment that says purge covers pam-owned derivatives INCLUDING cached crops.
"""

import importlib
import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pam.catalog.repo.files import FileRow
from pam.enrich.base import pil_image_loader
from pam.enrich.upright import GEOMETRY_VERSION, upright
from pam.scan.walker import placeholder_now

# Spec 8.2's crops are JPEG, like its thumbnails, and for the same reason: an `<img>` tag
# is what renders them and JPEG is what every browser draws without asking.
JPEG_TYPE = "image/jpeg"
JPEG_QUALITY = 88

# The longest edge a crop is served at. A face box on a 4032x3024 photograph is routinely
# 600-1200 px and UX 7 draws these at contact-sheet density, so serving the full cut would
# move megabytes to paint a card. It changes the RESOLUTION of the answer and never WHICH
# PIXELS are in it - the cut happens first, against the recorded geometry, and only the
# result is scaled - so it cannot move a crop off the face.
MAX_EDGE = 320

# A cut that would be empty after clamping. A zero-width box is a geometry document that
# says nothing about where a face is, and an empty JPEG is not an answer.
MIN_EDGE = 1


class CropUnavailable(Exception):
    """No crop can be served for this face, with the sentence saying why.

    AN EXPLICIT STATE, which is spec 8.2's word: "returns an explicit unavailable state
    rather than hydrating". The alternative a crop endpoint reaches for by itself is to
    open the file and find out, and on a cloud placeholder that alternative IS the damage
    (vision R2). Every raise below happens before anything reads bytes.
    """


@dataclass(frozen=True)
class Crop:
    """The rendered face: the bytes, and the frame they were cut from.

    The frame is carried out for the tests rather than for the browser - `width`/`height`
    are the crop's own size and `source` is the raster it came out of, which is what lets
    a test assert that the answer is smaller than the photograph instead of taking the
    endpoint's word for it (the plan's own named mutation: "returns the whole raster
    uncropped").
    """

    body: bytes
    width: int
    height: int
    source: tuple[int, int]


def source_file(candidates: list[FileRow], archive_root: Path) -> FileRow:
    """The first of `candidates` that is actually there, or `CropUnavailable`.

    `candidates` is `repo.files_by_sha256`'s list, already ordered best-first: a face knows
    its CONTENT, and one photograph can sit at two paths of which one is gone and one is
    not. The catalog's opinion decides the ORDER; this decides the answer, by asking the
    disk - `is_file()` on each, in order, and the first that exists wins.

    A PLACEHOLDER IS NOT SKIPPED HERE. It is refused in `render`, immediately before the
    open, against a live `os.stat` - because that is the only check that is still true at
    the moment it matters, and skipping placeholders here would make this function's
    answer disagree with that one.
    """
    for row in candidates:
        if (archive_root / row.rel_path).is_file():
            return row
    if not candidates:
        raise CropUnavailable("pam has no file recorded for the photograph this face is in")
    raise CropUnavailable(
        "the photograph this face is in is not where pam last saw it. Run: pam scan"
    )


def render(archive_root: Path, file_row: FileRow, geometry: dict[str, Any]) -> Crop:
    """Cut `geometry`'s box out of `file_row`'s upright raster. Raises `CropUnavailable`.

    THE ORDER OF THE STEPS IS THE CONTRACT, exactly as it is in `thumbs.get_thumb`, and
    for a sharper reason: the placeholder check comes before the open, so a placeholder is
    never read and never hydrated (vision R2). M8's M697 is the precedent for writing the
    order down - a mis-ordered refusal there read a placeholder's bytes and then returned
    the same 404 the correct order returns, with 1718 tests green, because a test that
    watches the OUTCOME cannot tell "never did the dangerous thing" from "did it and left
    no trace".
    """
    document = _validated(geometry)
    source = archive_root / file_row.rel_path
    try:
        os.stat(source)
    except OSError:
        raise CropUnavailable(
            "the photograph this face is in is not where pam last saw it. Run: pam scan"
        ) from None
    # Design 5.2 rev 34 (V1): the one live check, shared with every other content reader.
    if placeholder_now(source):
        raise CropUnavailable(
            "the photograph this face is in is stored online only, and pam will not"
            " download it to draw a face. Make it available offline, then reload."
        )
    with _decoded(archive_root, file_row) as opened:
        frame = upright(opened)
        _check_frame(document, frame.orientation, frame.width, frame.height)
        box = _pixels(document["bbox"], frame.width, frame.height)
        cut = frame.raster.crop(box).convert("RGB")
    return _encoded(cut, (frame.width, frame.height))


def _decoded(archive_root: Path, file_row: FileRow) -> Any:
    """The shared loader's decoded image, with a decode failure as an unavailable state.

    THE LOADER IS `enrich.base.pil_image_loader` AND NOTHING ELSE (A18, and HT-M10's F1).
    A bare `Image.open` here would skip two things the loader does: the RAW branch, which
    extracts the camera's embedded preview because Pillow decodes no RAW, and
    `pillow_heif.register_heif_opener()`, whose absence made almost every real image
    silently unreadable by the milestone's headline enricher. Detection went through this
    loader; a crop that went round it would be looking at a different photograph or at
    none.

    A file that will not decode is UNAVAILABLE, not a 500. It is an ordinary state - a
    truncated download, a RAW with no embedded preview, a format this install cannot read
    - and the page's answer to it is the same card it draws for a missing file.
    """
    try:
        return pil_image_loader(archive_root)(file_row)
    except Exception as error:
        raise CropUnavailable(
            f"pam could not read the photograph this face is in ({error})"
        ) from None


def _validated(geometry: dict[str, Any]) -> dict[str, Any]:
    """A13's geometry document, or `CropUnavailable` naming what is wrong with it.

    `repo.faces.py::_geometry` returns `{}` for a row whose `bbox_json` will not parse,
    on the stated principle that a crop endpoint refusing ONE face is better than a
    labeling page that will not open at all. This is where that `{}` becomes a sentence.
    """
    if geometry.get("v") != GEOMETRY_VERSION:
        raise CropUnavailable(
            "pam recorded this face in a geometry format it no longer reads."
            " Run: pam enrich faces"
        )
    bbox = geometry.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise CropUnavailable("pam's record of where this face is has no usable box in it")
    for key in ("source_w", "source_h", "orientation"):
        if not isinstance(geometry.get(key), int):
            raise CropUnavailable(
                f"pam's record of where this face is does not say what {key} was"
            )
    return geometry


def _check_frame(document: dict[str, Any], orientation: int, width: int, height: int) -> None:
    """INVARIANT I4, asserted against the pixels rather than trusted (A13, A18).

    The normalised box means nothing on its own: it is a fraction of a RASTER, and the
    document names which one. If the raster this request decoded is a different size or
    was turned a different way from the one detection measured, the fractions point
    somewhere else on the photograph - and the endpoint would serve a confident crop of the
    wrong part of the image, which on this page becomes a stranger's face under a person's
    name. There is no repair available here, so it refuses.

    IT IS ALSO THE ONLY THING THAT WOULD NOTICE THE HAZARD THIS MODULE WAS BUILT AROUND.
    A crop path that forgot the transpose decodes a 4032x3024 raster where the document
    says 3024x4032, and this check is what turns that from wrong pixels into a refusal.
    """
    recorded = (document["source_w"], document["source_h"], document["orientation"])
    if recorded != (width, height, orientation):
        raise CropUnavailable(
            "this photograph no longer decodes the way it did when pam found the face"
            f" in it (pam recorded {recorded[0]}x{recorded[1]} orientation {recorded[2]},"
            f" it now reads {width}x{height} orientation {orientation})."
            " Run: pam enrich faces"
        )


def _pixels(bbox: list[Any], width: int, height: int) -> tuple[int, int, int, int]:
    """A13's normalised `[x, y, w, h]` as a Pillow `(left, upper, right, lower)` box.

    CLAMPED TO THE RASTER, because a detector's box legitimately runs off the edge: YuNet
    decodes an anchor into a width and a height and nothing stops a face at the frame's
    margin from producing coordinates outside it. Pillow would happily crop past the edge
    and pad with black; clamping keeps the answer to pixels that exist.
    """
    try:
        x, y, w, h = (float(v) for v in bbox)
    except (TypeError, ValueError):
        raise CropUnavailable(
            "pam's record of where this face is has no usable box in it"
        ) from None
    left = _clamp(x * width, 0, width - MIN_EDGE)
    upper = _clamp(y * height, 0, height - MIN_EDGE)
    right = _clamp(left + w * width, left + MIN_EDGE, width)
    lower = _clamp(upper + h * height, upper + MIN_EDGE, height)
    return left, upper, right, lower


def _clamp(value: float, low: int, high: int) -> int:
    return max(low, min(high, int(round(value))))


def _encoded(cut: Any, source: tuple[int, int]) -> Crop:
    """The cut raster as JPEG bytes, scaled down to `MAX_EDGE` if it is larger.

    `thumbnail` rather than `resize`: it preserves the aspect ratio and, crucially, does
    nothing at all to an image already within the bound, so a small face is served at the
    resolution it was detected at instead of being upscaled into mush.
    """
    image_module = importlib.import_module("PIL.Image")
    if max(cut.size) > MAX_EDGE:
        cut.thumbnail((MAX_EDGE, MAX_EDGE), image_module.Resampling.LANCZOS)
    buffer = io.BytesIO()
    cut.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    width, height = cut.size
    return Crop(body=buffer.getvalue(), width=int(width), height=int(height), source=source)
