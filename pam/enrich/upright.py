"""THE ONE ORIENTATION-CORRECTED RASTER (invariant I4, amendments A13 and A18).

Detection and crop rendering must see the SAME PIXELS, and the thing that decides
whether they do is four lines long: transpose by the EXIF orientation, remember the tag
that was there, and record the size of what came out. `pil_image_loader` deliberately
does NOT do this - it returns the raster Pillow decoded, un-rotated, because a loader
that silently rotated would make `source_w`/`source_h` a lie about the file - so every
consumer that wants A13's frame has to do it, and two consumers doing it separately is
how they stop agreeing.

THE DEFECT THIS MODULE EXISTS TO PREVENT WAS MEASURED AS A HAZARD, NOT AS A BUG. Until
M11 Task 10, `ImageOps.exif_transpose` was applied inside `enrich/faces.py::enrich` and
nowhere else. The obvious crop endpoint - reuse the shared loader, cut with the stored
box - would then cut a SIDEWAYS raster with UPRIGHT coordinates: wrong pixels on every
phone portrait, correct on every landscape, and nothing in the repository could tell the
two apart because Task 8's crop inspection went through `faces.py`'s own path. A whole
archive of correct-looking landscapes is exactly the shape of defect that ships.

WHY THE ORIGINAL TAG IS CARRIED OUT SEPARATELY. `exif_transpose` normalises the tag away
in what it returns, which is right - the pixels are upright now - but A13's document
records the value the FILE declared, so a later reader can tell which decoding the
normalised coordinates belong to. It is read off the image that was handed in, before
the transpose, and it is the third operand of `webui/crop.py`'s frame check.
"""

import importlib
from dataclasses import dataclass
from typing import Any

# A13's geometry document version. The document is
# `{"v": 1, "bbox": [...], "landmarks": [...], "source_w": .., "source_h": .., "orientation": ..}`
# and the version is what lets a future shape be told apart from this one rather than
# mis-read as it. It lives HERE rather than in `faces.py` because both the writer (the
# enricher) and the reader (the crop endpoint) need it and neither owns the other;
# `faces.py` re-exports it, so its own tests and callers are unchanged.
GEOMETRY_VERSION = 1

# EXIF's own "no rotation" value. A file with no Orientation tag and a file that says
# "upright" record the same thing here, which is true.
UPRIGHT_TAG = 1

# The EXIF tag number for Orientation. Spelled once.
ORIENTATION_TAG = 274

# The range EXIF defines for it. Anything else is a file saying something EXIF has no
# meaning for, and `UPRIGHT_TAG` is the honest answer: the raster is what it is.
_ORIENTATION_RANGE = (1, 8)


@dataclass(frozen=True)
class Upright:
    """One decoded image, turned the right way up, with A13's frame recorded.

    `width`/`height` are the size of `raster` - the UPRIGHT size - which is what
    `source_w`/`source_h` mean in A13's document and what the normalised coordinates are
    normalised against. `orientation` is the tag the ORIGINAL carried, not the tag the
    upright raster would carry (which is always 1).
    """

    raster: Any
    orientation: int
    width: int
    height: int


def upright(decoded: Any) -> Upright:
    """`decoded` turned the right way up, with the tag it declared and the size it became.

    The import is deferred for `pil_image_loader`'s reason and no other: `pam --version`
    must not pay for Pillow, and `tests/test_deps.py::EAGER_IMPORT_CLOSURE` is
    `{click, pam}` because nothing under `pam.` imports it at module scope.

    `exif_transpose` returns `None` for an image it decided needed no work in some Pillow
    versions and the original in others; `or decoded` collapses the two, which is the
    spelling `enrich/faces.py` carried before this module took it over.
    """
    image_ops = importlib.import_module("PIL.ImageOps")
    raster = image_ops.exif_transpose(decoded) or decoded
    width, height = raster.size
    return Upright(
        raster=raster,
        orientation=orientation_tag(decoded),
        width=int(width),
        height=int(height),
    )


def orientation_tag(decoded: Any) -> int:
    """The file's EXIF orientation tag, or `UPRIGHT_TAG` when it declares none.

    Anything unreadable is `UPRIGHT_TAG` for the same reason a missing tag is: this
    function answers "which way up did the file say it was", and a file that said
    nothing said upright. It never raises - a geometry document is written for every
    detection, and an exception here would lose a whole file's faces over a metadata
    quirk.
    """
    try:
        exif = decoded.getexif()
    except Exception:  # pragma: no cover - Pillow raises nothing here for a loaded image
        return UPRIGHT_TAG
    value = exif.get(ORIENTATION_TAG) if exif else None
    low, high = _ORIENTATION_RANGE
    return int(value) if isinstance(value, int) and low <= value <= high else UPRIGHT_TAG
