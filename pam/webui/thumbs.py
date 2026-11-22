"""Spec 8.2's thumbnails: the preview the CAMERA wrote, extracted - never generated.

Rev 2 promised a "256px JPEG (generated on demand)", which needs an image decoder pam
does not have and will not have before M9 (spec 1). Rev 12 / amendment A2 replaces it
with extraction: exiftool pulls out the preview already embedded in the file, pam caches
the bytes under the workspace, and a file that has no embedded preview gets a 404 which
the frontend draws as a glyph card. NOTHING HERE DECODES A PIXEL.

THE EXTRACTOR ITSELF LIVES IN `pam/scan/exiftool.py` SINCE M10 TASK 6, and this module
owns only what spec 8.2 owns: the two VARIANTS, the cache, the empty marker and the key
format. The frozen `-b` argument tuples and the one-shot `subprocess.run` are spec 5.3's
THIRD frozen surface and moved to sit beside the first two (`FROZEN_ARGS_IMAGE` /
`FROZEN_ARGS_VIDEO`), so that `pam/enrich/`'s RAW image loader could share them without
importing `pam/webui/` - a layering inversion in a package whose review UI contains no
code that can rename a file (M10 clarification 4, spec 8.1 rev 17). NOTHING ABOUT THE
THUMBNAILS CHANGED IN THAT MOVE: the same chain, in the same order, behind the same
cache key.

THE GRID IS `-ThumbnailImage` ALONE AND NEVER FALLS BACK TO A LARGER TAG (A2's first
amendment). `-PreviewImage` on a RAW is routinely a full-resolution JPEG, and a contact
sheet of two hundred ~96px cards each pulling one would move hundreds of megabytes to
draw postage stamps. A file whose only embedded preview is a large one renders the glyph
card in the grid and the real image in the overlay, which is the correct trade because
the overlay is one image on demand. The fallback chain belongs to `full` and to nothing
else.

`None` MEANS "THERE IS NO IMAGE TO SERVE", NEVER "NOT CACHED YET". `get_thumb` extracts
on a cache miss and caches what it extracted, in the same call: extract-on-demand is the
whole design, nothing pre-warms this cache and no other code populates it. So a caller
that gets `None` can 404 immediately and a page that gets the 404 can draw its glyph
without ever asking again.

THE REFUSALS ARE RULES, NOT OPTIMISATIONS, AND ONE OF THEM IS A SAFETY RULE. Videos,
sidecars and `other` carry no still preview pam can use; a file with no sha256 has no
cache key. **A cloud PLACEHOLDER is refused because READING ONE HYDRATES IT** - vision
R2 - so the refusal is checked before anything touches the disk, cache included.
"""

from pathlib import Path

from pam.catalog.repo.files import FileRow
from pam.paths import Workspace
from pam.scan import exiftool, walker

# Spec 8.2's two variants: the contact sheet's card, and the full-review overlay.
GRID = "grid"
FULL = "full"
VARIANTS = (GRID, FULL)

# Part of the cache key (spec 8.2 rev 2's versioned-key requirement, which A2 keeps).
# BUMP THIS WHENEVER THE EXTRACTION CHAIN BELOW CHANGES: the old entries then key to a
# name nothing asks for, and the next request re-extracts instead of serving bytes that
# a different chain produced. It is not a schema version and not pam's version.
EXTRACTOR_VERSION = "e1"

# Spec 8.2: the extracted bytes are always JPEG - `ThumbnailImage`, `PreviewImage` and
# `JpgFromRaw` are JPEG by definition - so one media type and one cache suffix serve
# every variant.
JPEG_SUFFIX = ".jpg"

# THE NEGATIVE CACHE'S SUFFIX (HT-M7's P11). An extraction that produced nothing is a
# permanent fact about this file and this variant - `extractable` has already excluded
# every file whose contents pam may change its mind about, and nothing in pam edits an
# image - so re-running exiftool on the next scroll past the same card spends ~0.27s to
# learn what the last call already learned. On a phone library that is the ORDINARY
# case, not the exception: design 8.2 rev 14 / amendment A9 records only a few percent of
# items with an embedded thumbnail, because HEIC carries none at all.
#
# A ZERO-BYTE FILE RATHER THAN A DATABASE ROW, and it lives in the same directory under
# the same key as the bytes it stands in for, because it is the same cache: one place to
# look, one place `EXTRACTOR_VERSION` invalidates, and no schema change (the M8 plan
# forbids a migration and does not owe one for a cache).
#
# THE FILENAME STILL BEGINS WITH THE SHA256, which is amendment A2's normative rule and
# is load-bearing here rather than incidental: `verify/checks.py::_thumb_key` reads the
# leading 64 hex characters and spec 7.5 check 7 reports every filename it cannot read
# that way as belonging to no file pam knows about. A marker keyed any other way would
# make `pam verify` report the whole cache as stale - a worse defect than the one this
# fixes, and one that would arrive as a warning on a healthy archive.
#
# THE SCALE QUESTION IS NOT TAKEN HERE. Eviction, a cache-size bound and what either
# costs on a 100k-file archive are M13's by name (the M8 plan says so); this marker is
# the same size as the entry it replaces would have been minus its bytes.
EMPTY_SUFFIX = ".none"

# The one media type that can carry a still preview (spec 8.2's "every video, every
# sidecar, every `other`" stated from the other side). `walker.media_type_for` writes
# exactly one of image / video / sidecar / other.
IMAGE_MEDIA_TYPE = "image"


def attempts(variant: str) -> tuple[tuple[str, ...], ...]:
    """The exiftool argument tuples to try, in order, for `variant`.

    One attempt for the grid and three for the overlay, taken from the two frozen
    constants in `pam/scan/exiftool.py`. THE GRID'S IS A ONE-TUPLE AND THAT IS THE
    AMENDMENT (A2's first): `-PreviewImage` on a RAW is routinely a full-resolution
    JPEG, and a contact sheet of two hundred ~96px cards each pulling one would move
    hundreds of megabytes to draw postage stamps. The two chains are two constants over
    there rather than one sliced two ways, so merging them has to be written on purpose.
    """
    if variant == GRID:
        return (exiftool.THUMBNAIL_ARGS,)
    return exiftool.PREVIEW_ATTEMPTS


def extractable(file_row: FileRow) -> bool:
    """Could an embedded preview be extracted from this file AT ALL (spec 8.2)?

    The three operands are spec 8.2's own never-extract list, and none of them is an
    optimisation of another. Every video, every sidecar and every `other` is excluded by
    the first (v1.0 decodes no video and a sidecar is not a picture). A file with no
    sha256 is excluded by the second, because the cache key IS the sha256. A cloud
    PLACEHOLDER is excluded by the third, and that one is a SAFETY rule: reading a
    placeholder's bytes hydrates it, which is vision R2's whole point.

    TRUE DOES NOT PROMISE AN IMAGE - whether the camera embedded a preview is knowable
    only by extracting one. This is deliberately the same question, on the same three
    operands, that `repo.review.ReviewItem.thumbnailable` answers for a plan row; the
    two types are different (a `FileRow` carries the sha256 itself, a `ReviewItem`
    carries whether there is one) and the two spellings are checked against each other
    in `tests/test_webui_thumbs.py` rather than one being derived from the other.
    """
    return (
        file_row.media_type == IMAGE_MEDIA_TYPE
        and file_row.sha256 is not None
        and not file_row.placeholder
    )


def cache_path(workspace: Workspace, sha256: str, variant: str) -> Path:
    """Where `variant`'s bytes for this file live under the workspace `thumbs/` dir.

    THE FILENAME BEGINS WITH THE SHA256 AND NOTHING ELSE (amendment A2, normative).
    `verify/checks.py::_thumb_key` reads the LEADING 64 HEX CHARACTERS as the key and
    spec 7.5 check 7 reports every filename it cannot read that way as belonging to no
    file pam knows about - so any other shape would make `pam verify` report the entire
    thumbnail cache as stale. The rest of the key follows: the variant, because the grid
    and the overlay hold different images, and the extractor version, so a change to the
    chain invalidates cleanly instead of serving the old chain's output forever.
    """
    return _keyed(workspace, sha256, variant, JPEG_SUFFIX)


def empty_marker_path(workspace: Workspace, sha256: str, variant: str) -> Path:
    """Where "this file has no `variant` preview" is remembered (see `EMPTY_SUFFIX`).

    The same key as `cache_path` under a different suffix, built by the same helper, so
    the sha256-first rule check 7 depends on cannot hold for one and not the other.
    """
    return _keyed(workspace, sha256, variant, EMPTY_SUFFIX)


def _keyed(workspace: Workspace, sha256: str, variant: str, suffix: str) -> Path:
    return workspace.thumbs_dir / f"{sha256}.{variant}.{EXTRACTOR_VERSION}{suffix}"


def get_thumb(workspace: Workspace, file_row: FileRow, variant: str) -> Path | None:
    """The cached preview for this file, EXTRACTING IT FIRST if it is not cached yet.

    `None` is spec 8.2's 404 and the frontend's signal to draw the glyph card: a refusal
    class, a source that is not on disk, or a file whose camera embedded no preview at
    all. It never means "ask again later" - there is no later, because this call is the
    only thing that ever fills the cache.

    THE ORDER OF THE STEPS IS THE CONTRACT. The refusals come first and return before
    any path is touched, so a catalog placeholder is never read, never stat'd and never
    given a cache entry - POSITIVE OR NEGATIVE. A cache hit returns without going near the
    archive. Only then is the source looked at, and only then is exiftool run - after
    `walker.placeholder_now` has stat'd it, so a file that became cloud-only after the
    last scan gets the same answer as a catalog placeholder, and no cache entry (V1).

    THE NEGATIVE CACHE (HT-M7's P11) REMEMBERS EXACTLY ONE OUTCOME: exiftool ran over a
    real file and produced no bytes. The other two `None`s are deliberately NOT cached
    and the difference is not an optimisation:
    - a REFUSED file never reaches the cache at all, which is what keeps a placeholder
      from being given an entry that says something about bytes pam has not read;
    - a MISSING source is a state that heals. Spec 7.5 check 1 reports it, the file may
      be back before the next request, and a marker written now would go on answering
      for it after it returned. `None` here means "not today", the one reading this
      docstring's opening paragraph says the CACHE never means. An extraction that never
      COMPLETED (`extract` returning `None` - a timeout, an unrunnable binary) is the
      same case and is declined for the same reason.
    """
    if not extractable(file_row):
        return None
    # `extractable` proved this, but mypy reads the fields rather than the function.
    assert file_row.sha256 is not None
    cached = cache_path(workspace, file_row.sha256, variant)
    if cached.is_file():
        return cached
    if empty_marker_path(workspace, file_row.sha256, variant).is_file():
        return None
    source = workspace.archive_root / file_row.rel_path
    # A file the catalog knows and the disk no longer has is an ordinary state between
    # a scan and the next one (spec 7.5 check 1 is what reports it). It is a glyph card
    # here, never an exception out of a request handler.
    if not source.is_file():
        return None
    # Design 8.2 rev 34 (V1): `extractable` read the CATALOG's placeholder flag, which is
    # what the last scan saw. OneDrive can free the file after that, and exiftool reading
    # it would hydrate it - so the live attributes are checked immediately before the read,
    # and a file that has become cloud-only gets the catalog placeholder's answer: no
    # thumbnail, and no cache entry of either kind.
    if walker.placeholder_now(source):
        return None
    data = extract(source, variant)
    if data is None:
        return None
    workspace.thumbs_dir.mkdir(parents=True, exist_ok=True)
    if not data:
        empty_marker_path(workspace, file_row.sha256, variant).write_bytes(b"")
        return None
    cached.write_bytes(data)
    return cached


def extract(source: Path, variant: str) -> bytes | None:
    """`variant`'s embedded preview; `b""` for "it has none"; `None` for "ask again".

    The variant is spec 8.2's and the extraction is spec 5.3's, so this is the whole of
    the join between them: `attempts()` turns a variant into a chain and
    `exiftool.extract_binary` runs it. The three-way answer is that function's and is
    documented there - `None` exists for the negative cache, never for the 404.
    """
    return exiftool.extract_binary(source, attempts(variant))
