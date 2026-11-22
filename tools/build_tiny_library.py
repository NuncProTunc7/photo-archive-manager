"""Build `tests/tiny_library/` - the ~100-file benchmark archive of design spec 9.1.

    .venv\\Scripts\\python tools\\build_tiny_library.py

Rebuilds the committed library in place and rewrites `tests/tiny_library_manifest.json`
beside it. `tests/test_tiny_library.py` proves a rebuild is byte-identical to what is
committed, so running this on a clean tree must leave `git status` clean.

WHY THIS EXISTS. Spec 9.1's tiny library is the fixture every later milestone measures
against: "pam read N files and errored on M" and "pam proposed exactly these names" are
only regression signals if the input is frozen. A folder of real photos cannot be
committed (they are the maintainer's, they are large, and they are not reproducible), so the
library is SYNTHESISED from bytes this file writes.

NO PILLOW, NO NEW DEPENDENCY (M6 plan clarification 7). Spec 9.1 says "programmatically
generated images (Pillow)", which predates M2 proving that hand-written EXIF/TIFF/BMFF
bytes are read by real exiftool exactly like camera output. Everything here is `struct`
and `zlib` from the standard library, plus `tests/video_samples.py`'s existing writers
for the QuickTime shapes - the ones M4 Task 2 Step 7 proved against real exiftool. There
is deliberately no second video writer in this file.

THE ONE THING THIS CANNOT PRODUCE, STATED OUT LOUD. Spec 9.1 asks for EXIF-less "scans
with rendered date text (for OCR)". Rendering text into pixels needs a rasteriser, which
needs Pillow. The two `scan_ocr_placeholder_*` files below carry their date as a JPEG
COM segment instead - *machine-readable text in the file, not pixels* - so they exercise
the EXIF-less path and nothing else. THEY ARE NOT AN OCR FIXTURE. M12 owns OCR and owes
the real files. The same warning is in `tests/tiny_library/README.md`, because a
placeholder presented as the real case is precisely the failure vision principle 9
names.

DETERMINISM IS THE WHOLE POINT, AND MTIME IS THE HARD PART. Every byte written here is a
pure function of the table below - no clock, no random, no zlib version dependence (the
one deflate stream is a hand-written stored block). But a file's MTIME is not in its
bytes and git does not preserve it: a fresh clone stamps every file with the checkout
time. Spec 9.1 requires date-less files, whose capture date therefore falls back to
`file_mtime` (`pam/scan/objective.py`), so their proposed names would differ on every
machine. `stamp_mtimes()` below is the answer: the builder AND the test fixture both
force every file to `FIXED_MTIME_EPOCH` before anything scans. Nothing else that feeds a
proposed name reads a clock - `files.first_seen_at`, `files.last_scanned_at` and
`plans.created_at` are wall-clock but none of them reaches the name.

`datetime.fromtimestamp` in `build_capture` is LOCAL, so the date those files land on is
still the reader's timezone - it is 2011-06-15 or 2011-06-16 depending on the offset.
That is why `FIXED_MTIME_EPOCH` sits in a three-day window (2011-06-14..16) that no
other file in the library uses: the seq run those files form is stable everywhere, and
the test computes the one date component it cannot pin. See the test for the rest.
"""

import json
import os
import struct
import sys
import tempfile
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"
LIBRARY_DIR = TESTS_DIR / "tiny_library"
MANIFEST_PATH = TESTS_DIR / "tiny_library_manifest.json"

# `tests/video_samples.py` is the project's ONE device-shaped QuickTime writer (M4 Task 2
# Step 7, proven against real exiftool in tests/test_video_real_exiftool.py). A tool
# importing out of `tests/` is backwards, and it is still the right call: the alternative
# is a second writer that can drift from the first, and the M6 plan says in as many words
# to reuse this one. A benchmark test does the mirror-image insert for `tools/`.
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ht_manifest  # noqa: E402
import video_samples  # noqa: E402

# A fixed instant for every file in the library. 2011-06-15 12:00:00 UTC: mid-day so no
# real-world offset moves it more than one day, and inside a three-day window
# (2011-06-14/15/16) that no dated file below occupies, so the file_mtime bucket can
# never merge with a dated one no matter which timezone reads it.
FIXED_MTIME_EPOCH = int(datetime(2011, 6, 15, 12, 0, 0, tzinfo=UTC).timestamp())

# Every supported extension, from pam/config.py's defaults. Written out rather than
# imported so that ADDING an extension to the config does not silently change what this
# library covers - it should fail the pinned counts and make someone extend the table.
IMAGE_EXTS = (
    "jpg",
    "jpeg",
    "heic",
    "heif",
    "png",
    "tif",
    "tiff",
    "cr2",
    "nef",
    "arw",
    "dng",
)
VIDEO_EXTS = ("mov", "mp4", "avi", "mts")

# The 250-char-path case (spec 9.1). A FIXED 140-character stem, not a stem computed to
# hit exactly 250: the library is copied to a temp folder to be scanned, so the absolute
# path depends on where it sits and a build-time calculation would be wrong at read time.
# 140 keeps the total near the Windows limit from both the repo (~226 chars) and a pytest
# tmp_path (~240) without crossing 260, which is what a fixed length can honestly promise.
LONG_STEM = "long_path_" + ("x" * 130)

MAKE = "PamTest"
MODEL = "TinyLibrary"

_ASCII, _SHORT, _LONG, _RATIONAL = 2, 3, 4, 5

Entry = tuple[int, int, int, bytes]  # tag, TIFF type, count, payload


# --------------------------------------------------------------------------------------
# TIFF / EXIF
# --------------------------------------------------------------------------------------


def _ascii(value: str) -> bytes:
    """NUL-terminated bytes for a TIFF ASCII field.

    `backslashreplace`, not a UTF-8 passthrough: the TIFF ASCII type is 7-bit by
    definition, and this library deliberately has unicode FILENAMES which are written
    into ImageDescription. Escaping keeps the field legal and still unique per file.
    """
    return value.encode("ascii", "backslashreplace") + b"\x00"


def _ascii_entry(tag: int, value: str) -> Entry:
    """One ASCII IFD entry whose count matches the bytes actually written."""
    payload = _ascii(value)
    return (tag, _ASCII, len(payload), payload)


def _rational(numerator: int, denominator: int) -> bytes:
    return struct.pack(">II", numerator, denominator)


def _dms(value: float) -> bytes:
    """Decimal degrees -> the three RATIONALs EXIF stores a coordinate as."""
    degrees = int(value)
    minutes_float = (value - degrees) * 60
    minutes = int(minutes_float)
    hundredths = round((minutes_float - minutes) * 60 * 100)
    return _rational(degrees, 1) + _rational(minutes, 1) + _rational(hundredths, 100)


def _ifd_layout(entries: list[Entry]) -> tuple[int, int]:
    """(directory bytes, value-area bytes) for one IFD, without building it.

    Needed before the directory exists, because IFD0 must carry a POINTER to the Exif
    and GPS sub-IFDs and the pointer is an absolute file offset. Sizes are knowable from
    the payloads alone; the layout below writes them at exactly these offsets.
    """
    directory = 2 + 12 * len(entries) + 4
    values = 0
    for _tag, _typ, _count, payload in entries:
        if len(payload) > 4:
            values += len(payload) + len(payload) % 2  # IFD values are word-aligned
    return directory, values


def _ifd_bytes(entries: list[Entry], start: int, next_ifd: int = 0) -> bytes:
    """One IFD placed at absolute offset `start`, linking to `next_ifd` (0 = none)."""
    ordered = sorted(entries)  # TIFF requires ascending tag order within a directory
    directory, _values_size = _ifd_layout(ordered)
    value_at = start + directory
    out = [struct.pack(">H", len(ordered))]
    values = bytearray()
    for tag, typ, count, payload in ordered:
        if len(payload) <= 4:
            field_bytes = payload.ljust(4, b"\x00")
        else:
            field_bytes = struct.pack(">I", value_at + len(values))
            values += payload
            if len(values) % 2:
                values += b"\x00"
        out.append(struct.pack(">HHI", tag, typ, count) + field_bytes)
    out.append(struct.pack(">I", next_ifd))
    return b"".join(out) + bytes(values)


def exif_tiff(
    *,
    description: str,
    date: str | None = None,
    offset: str | None = None,
    gps: tuple[float, float] | None = None,
    width: int = 64,
    height: int = 48,
) -> bytes:
    """A big-endian TIFF/EXIF block: IFD0 + optional Exif sub-IFD + optional GPS sub-IFD.

    Used two ways, unchanged: as the payload of a JPEG APP1 segment, and as an entire
    `.tif`/`.nef`/`.cr2`/`.arw`/`.dng` file. `description` goes in ImageDescription and
    is the file's own name, which is what makes every generated file's sha256 distinct -
    two files with the same date and no distinguishing bytes would collapse into one
    entry in any digest-set comparison.
    """
    exif_entries: list[Entry] = [
        (0xA002, _LONG, 1, struct.pack(">I", width)),
        (0xA003, _LONG, 1, struct.pack(">I", height)),
    ]
    if date is not None:
        exif_entries.append(_ascii_entry(0x9003, date))
        exif_entries.append(_ascii_entry(0x9004, date))
    if offset is not None:
        exif_entries.append(_ascii_entry(0x9011, offset))
    gps_entries: list[Entry] = []
    if gps is not None:
        latitude, longitude = gps
        gps_entries = [
            _ascii_entry(0x0001, "N" if latitude >= 0 else "S"),
            (0x0002, _RATIONAL, 3, _dms(abs(latitude))),
            _ascii_entry(0x0003, "E" if longitude >= 0 else "W"),
            (0x0004, _RATIONAL, 3, _dms(abs(longitude))),
        ]

    base: list[Entry] = [
        (0x0100, _SHORT, 1, struct.pack(">H", width)),
        (0x0101, _SHORT, 1, struct.pack(">H", height)),
        _ascii_entry(0x010E, description),
        _ascii_entry(0x010F, MAKE),
        _ascii_entry(0x0110, MODEL),
    ]
    placeholder = b"\x00\x00\x00\x00"
    pointers: list[Entry] = []
    if exif_entries:
        pointers.append((0x8769, _LONG, 1, placeholder))
    if gps_entries:
        pointers.append((0x8825, _LONG, 1, placeholder))

    directory0, values0 = _ifd_layout(base + pointers)
    exif_start = 8 + directory0 + values0
    directory1, values1 = _ifd_layout(exif_entries) if exif_entries else (0, 0)
    gps_start = exif_start + directory1 + values1

    resolved: list[Entry] = list(base)
    if exif_entries:
        resolved.append((0x8769, _LONG, 1, struct.pack(">I", exif_start)))
    if gps_entries:
        resolved.append((0x8825, _LONG, 1, struct.pack(">I", gps_start)))

    body = _ifd_bytes(resolved, 8)
    if exif_entries:
        body += _ifd_bytes(exif_entries, exif_start)
    if gps_entries:
        body += _ifd_bytes(gps_entries, gps_start)
    return b"MM\x00\x2a\x00\x00\x00\x08" + body


# --------------------------------------------------------------------------------------
# A RAW that really does carry an embedded preview (M10 Task 6, design 8.1 rev 17 / A4)
# --------------------------------------------------------------------------------------
#
# WHY THIS EXISTS. Every other RAW here is a plain TIFF body with no coded image in it,
# which is faithful for the SCANNER - pam classifies by extension and reads dates from
# the standard Exif IFD - and useless for the LOADER, which has to open a picture.
# HT-M9's F1 measured half of the real `.NEF` files a human test tried failing `phash` with
# `UnidentifiedImageError`; amendment A4's fix is to hand the enricher the preview the
# camera embedded, and nothing in this library embedded one.
#
# THE THREE PAYLOADS ARE DELIBERATELY DIFFERENT BYTES, and that is the whole point of the
# fixture rather than a flourish. Spec 8.2's chain is ORDERED - `-PreviewImage`, then
# `-JpgFromRaw`, then `-ThumbnailImage` - and a file carrying one representation three
# times cannot tell a correct chain from a reversed one: every ordering returns the same
# bytes and the test passes for all of them. `tests/test_exiftool.py` drives all three
# links and asserts on which payload came back.
#
# WHY THE EXTENSION IS `.ARW` AND NOT THE `.NEF` THE PLAN SUGGESTED. Measured against
# real exiftool 13.59, not reasoned: tags 0x0201/0x0202 are named `PreviewImageStart` in
# IFD0 only for `TIFF_TYPE` ARW and SR2 (`Exif.pm`), and `JpgFromRawStart` in a SubIFD.
# In a NEF, CR2 or DNG built this way exiftool reports no `PreviewImage` at all, so a
# `.NEF` fixture could exercise only the chain's second and third links. The four RAW
# extensions are one class to pam (`RAW_EXTS` in `pam/enrich/base.py`), so the branch
# under test is the same one either way.
RAW_PREVIEW_TAGS = ("PreviewImage", "JpgFromRaw", "ThumbnailImage")


def raw_preview_payloads(name: str) -> dict[str, bytes]:
    """The three embedded JPEGs for one RAW fixture: one per tag, all different bytes.

    Each is a structurally complete baseline JPEG that Pillow really decodes, carrying
    its own tag name in its COM segment - so an assertion can name which link of the
    chain answered without decoding anything, and a decode still works for a test that
    wants pixels.
    """
    return {
        tag: jpeg(exif=None, comment=f"pam tiny library: {name} {tag}")
        for tag in RAW_PREVIEW_TAGS
    }


def raw_with_previews(
    name: str,
    *,
    date: str | None = None,
    tags: tuple[str, ...] = RAW_PREVIEW_TAGS,
) -> bytes:
    """A big-endian ARW-shaped TIFF carrying the embedded previews `tags` names.

    `PreviewImage` is 0x0201/0x0202 in IFD0, `JpgFromRaw` the same pair in a SubIFD
    (0x014A) and `ThumbnailImage` the same pair in IFD1 - the three places exiftool reads
    them from, verified against the real binary rather than inferred from `Exif.pm`.
    Dropping a tag from `tags` drops that whole IFD, which is what lets one builder
    produce the "no `PreviewImage`" and "thumbnail only" cases the chain test needs.
    """
    payloads = raw_preview_payloads(name)
    described = f"pam tiny library: {name}"
    base: list[Entry] = [
        (0x0100, _SHORT, 1, struct.pack(">H", 64)),
        (0x0101, _SHORT, 1, struct.pack(">H", 48)),
        _ascii_entry(0x010E, described),
        _ascii_entry(0x010F, MAKE),
        _ascii_entry(0x0110, MODEL),
    ]
    preview = payloads["PreviewImage"] if "PreviewImage" in tags else b""
    # A four-byte stand-in wherever the value is an absolute offset that is not known
    # until the IFDs after this one have been laid out. `_ifd_layout` only needs the
    # SIZES, and every one of these is exactly four bytes whatever it ends up pointing at.
    unresolved = b"\x00\x00\x00\x00"
    deferred = {0x0201, 0x014A, 0x8769}
    ifd0 = list(base)
    if preview:
        ifd0 += [
            (0x0201, _LONG, 1, unresolved),
            (0x0202, _LONG, 1, struct.pack(">I", len(preview))),
        ]
    if "JpgFromRaw" in tags:
        ifd0.append((0x014A, _LONG, 1, unresolved))
    if date is not None:
        ifd0.append((0x8769, _LONG, 1, unresolved))

    directory0, values0 = _ifd_layout(ifd0)
    preview_at = 8 + directory0 + values0
    cursor = preview_at + _padded_len(preview)
    sub_bytes, sub_at, cursor = _image_ifd(payloads["JpgFromRaw"], cursor, "JpgFromRaw" in tags)
    exif_at = cursor
    exif_bytes = b""
    if date is not None:
        exif_entries: list[Entry] = [
            (0xA002, _LONG, 1, struct.pack(">I", 64)),
            (0xA003, _LONG, 1, struct.pack(">I", 48)),
            _ascii_entry(0x9003, date),
            _ascii_entry(0x9004, date),
        ]
        exif_bytes = _ifd_bytes(exif_entries, exif_at)
        cursor = exif_at + len(exif_bytes)
    thumb_bytes, thumb_at, cursor = _image_ifd(
        payloads["ThumbnailImage"], cursor, "ThumbnailImage" in tags, reduced=True
    )

    resolved = [entry for entry in ifd0 if entry[0] not in deferred]
    if preview:
        resolved.append((0x0201, _LONG, 1, struct.pack(">I", preview_at)))
    if sub_at:
        resolved.append((0x014A, _LONG, 1, struct.pack(">I", sub_at)))
    if date is not None:
        resolved.append((0x8769, _LONG, 1, struct.pack(">I", exif_at)))
    body = _ifd_bytes(resolved, 8, thumb_at) + preview + b"\x00" * (len(preview) % 2)
    return b"MM\x00\x2a" + struct.pack(">I", 8) + body + sub_bytes + exif_bytes + thumb_bytes


def _padded_len(blob: bytes) -> int:
    return len(blob) + len(blob) % 2


def _image_ifd(
    blob: bytes, start: int, present: bool, *, reduced: bool = False
) -> tuple[bytes, int, int]:
    """One IFD whose only content is a 0x0201/0x0202 embedded JPEG; ("", 0, start) if not.

    `reduced` writes IFD1's SubfileType/Compression pair, which is what a camera writes
    beside a real EXIF thumbnail.
    """
    if not present:
        return b"", 0, start
    entries: list[Entry] = [
        (0x0201, _LONG, 1, b"\x00\x00\x00\x00"),
        (0x0202, _LONG, 1, struct.pack(">I", len(blob))),
    ]
    if reduced:
        entries += [
            (0x00FE, _LONG, 1, struct.pack(">I", 1)),
            (0x0103, _SHORT, 1, struct.pack(">H", 6)),
        ]
    directory, values = _ifd_layout(entries)
    blob_at = start + directory + values
    resolved = [entry for entry in entries if entry[0] != 0x0201]
    resolved.append((0x0201, _LONG, 1, struct.pack(">I", blob_at)))
    body = _ifd_bytes(resolved, start) + blob + b"\x00" * (len(blob) % 2)
    return body, start, start + len(body)


# --------------------------------------------------------------------------------------
# Container writers, one per shape. All pure: bytes in, bytes out.
# --------------------------------------------------------------------------------------


def _segment(marker: bytes, payload: bytes) -> bytes:
    return marker + struct.pack(">H", len(payload) + 2) + payload


def jpeg(*, exif: bytes | None, comment: str) -> bytes:
    """A structurally complete baseline JPEG: SOI, APP1, COM, DQT, SOF0, SOS, EOI.

    Complete rather than minimal on purpose. `\\xff\\xd8\\xff\\xd9` is enough for exiftool
    to report FileType JPEG, but it also earns a "Missing JPEG SOS" warning - and a
    library whose every file carries a warning cannot be used to notice that ONE file
    started carrying one.
    """
    parts = [b"\xff\xd8"]
    if exif is not None:
        parts.append(_segment(b"\xff\xe1", b"Exif\x00\x00" + exif))
    parts.append(_segment(b"\xff\xfe", comment.encode("utf-8")))
    parts.append(_segment(b"\xff\xdb", b"\x00" + bytes([16] * 64)))
    parts.append(
        _segment(b"\xff\xc0", b"\x08" + struct.pack(">HH", 48, 64) + b"\x01\x01\x11\x00")
    )
    parts.append(_segment(b"\xff\xda", b"\x01\x01\x00\x00\x3f\x00"))
    parts.append(b"\x3f\xd0\x00\x00\xff\xd9")
    return b"".join(parts)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))


# A hand-written deflate stream: a zlib header, STORED blocks, and the payload's adler32.
# Written out rather than produced by zlib.compress() because compressor output is a
# function of the zlib build, and this library's bytes must be a function of this file
# alone. A stored block carries its payload verbatim, so the only version-dependent thing
# left is the CHECKSUM - and adler32/crc32 are specified constants, not encoder choices.
#
# M9 TASK 8 TURNED THE CONSTANT INTO A FUNCTION, AND THE CONSTANT IS THE CONTROL. The
# thirteen bytes that used to be written out here are exactly what this returns for the
# 1x1 payload, which is why every PNG committed before that change still hashes the same;
# `tests/test_tiny_library.py` asserts it rather than leaving it to the rebuild diff. The
# generalisation is what lets the phash fixtures below carry real pixels.
_STORED_IDAT_1X1 = bytes(
    [0x78, 0x01, 0x01, 0x02, 0x00, 0xFD, 0xFF, 0x00, 0x00, 0x00, 0x02, 0x00, 0x01]
)


def stored_zlib(raw: bytes) -> bytes:
    """`raw` as a zlib stream of STORED deflate blocks. No compressor, so no zlib build."""
    out = bytearray([0x78, 0x01])
    position = 0
    while True:
        chunk = raw[position : position + 0xFFFF]
        position += len(chunk)
        final = position >= len(raw)
        out.append(1 if final else 0)
        out += struct.pack("<HH", len(chunk), len(chunk) ^ 0xFFFF)
        out += chunk
        if final:
            break
    out += struct.pack(">I", zlib.adler32(raw))
    return bytes(out)


# The side of the 8-bit greyscale square the three phash fixtures are drawn on. 32 is
# comfortably above dHash's 9x8 working grid, so their hashes are a statement about the
# picture rather than about Pillow's resampling of something smaller than the grid.
PHASH_SIDE = 32
PHASH_PATTERNS = ("base", "near", "far")


def phash_rows(kind: str) -> tuple[bytes, ...]:
    """Greyscale scanlines for one of the three `phash_pattern_*.png` files (design 8.1).

    THE POINT OF THESE THREE IS THE DISTANCES BETWEEN THEM, not any one hash. dHash
    compares each pixel with its right-hand neighbour, so:

      base - a diagonal ramp; every row has real left-to-right structure, which a flat
             or one-pixel image does not (every perceptual hash of those is zero, and a
             fixture that hashes to zero cannot tell a working dHash from a broken one).
      near - the same ramp with one 4x4 block inverted. A NEAR duplicate: the same
             picture, a hash that differs in a couple of bits.
      far  - the ramp photometrically inverted. Every neighbour comparison flips, which
             is the FARTHEST a dHash can get from `base`.

    `tests/test_phash.py` pins the distances, spelled from this docstring and not from a
    call to the enricher, so a change to these pixels fails there and says why.
    """
    if kind not in PHASH_PATTERNS:
        raise ValueError(f"unknown phash pattern {kind!r}")
    ramp = [[(x * 8 + y * 3) % 256 for x in range(PHASH_SIDE)] for y in range(PHASH_SIDE)]
    if kind == "near":
        for y in range(2, 6):
            for x in range(2, 6):
                ramp[y][x] = 255 - ramp[y][x]
    elif kind == "far":
        ramp = [[255 - value for value in row] for row in ramp]
    return tuple(bytes(row) for row in ramp)


def png(
    *, exif: bytes | None, description: str, pixels: tuple[bytes, ...] = (b"\x00",)
) -> bytes:
    """A PNG. `pixels` is one bytes object per 8-bit greyscale scanline.

    The default is the 1x1 black pixel every PNG in this library carried before M9 Task 8,
    kept as the default so those files' bytes did not move. It DOES decode - Pillow opens
    it and reports `L (1, 1)` - but see `phash_rows` for why that is not enough.
    """
    height = len(pixels)
    width = len(pixels[0])
    raw = b"".join(b"\x00" + row for row in pixels)
    chunks = [
        _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)),
        _png_chunk(b"tEXt", b"Description\x00" + description.encode("utf-8")),
    ]
    if exif is not None:
        chunks.append(_png_chunk(b"eXIf", exif))
    chunks.append(_png_chunk(b"IDAT", stored_zlib(raw)))
    chunks.append(_png_chunk(b"IEND", b""))
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks)


def heif(*, brand: bytes, description: str) -> bytes:
    """An ISO-BMFF still: ftyp + a `pict` meta + a free box carrying the name.

    Reuses `video_samples.box`, the same primitive the QuickTime writers are built from -
    a HEIC file and a MOV file are the same container with a different major brand, so
    there is no second box builder here either. No EXIF: getting an Exif item into the
    `meta`/`iloc`/`iinf` machinery is a real HEIF encoder's job, and every HEIC/HEIF file
    below therefore takes its date from its filename or its mtime. Recorded as a known
    limit in tests/tiny_library/README.md rather than papered over.
    """
    ftyp = video_samples.box(b"ftyp", brand + struct.pack(">I", 0) + b"mif1" + brand)
    handler = video_samples.full_box(
        b"hdlr", 0, 0, struct.pack(">I", 0), b"pict", b"\x00" * 12, b"\x00"
    )
    meta = video_samples.full_box(b"meta", 0, 0, handler)
    free = video_samples.box(b"free", description.encode("utf-8"))
    mdat = video_samples.box(b"mdat", b"\x00" * 4096)
    return ftyp + meta + free + mdat


def _riff_chunk(kind: bytes, payload: bytes) -> bytes:
    padded = payload + (b"\x00" if len(payload) % 2 else b"")
    return kind + struct.pack("<I", len(payload)) + padded


def avi(*, description: str) -> bytes:
    """A minimal RIFF/AVI: hdrl+avih, a JUNK chunk holding the name, and an empty movi.

    No date anywhere - AVI's optional IDIT tag is not written, which is the common case
    for the camcorder-era files this extension exists for, and makes this the AVI half of
    spec 9.1's date-less coverage.
    """
    avih = _riff_chunk(
        b"avih", struct.pack("<14I", 33333, 0, 0, 16, 1, 0, 0, 0, 64, 48, 0, 0, 0, 0)
    )
    hdrl = _riff_chunk(b"LIST", b"hdrl" + avih)
    junk = _riff_chunk(b"JUNK", description.encode("utf-8"))
    movi = _riff_chunk(b"LIST", b"movi")
    body = b"AVI " + hdrl + junk + movi
    return b"RIFF" + struct.pack("<I", len(body)) + body


def m2ts(*, description: str) -> bytes:
    """AVCHD transport stream: 188-byte packets, each starting with the 0x47 sync byte.

    Payload is the file's own name then 0xFF padding, so the bytes are unique per file
    and the packet grid is what exiftool needs to recognise the format. No date: AVCHD
    carries its clock in the modification-time stream, which nothing here writes.
    """
    payload = (description.encode("utf-8") + b"\xff" * 184)[:184]
    return (b"\x47\x40\x00\x10" + payload) * 24


def xmp(*, description: str) -> bytes:
    return (
        '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        f"  <!-- pam tiny library sidecar for {description} -->\n"
        "</x:xmpmeta>\n"
        '<?xpacket end="w"?>\n'
    ).encode()


def aae(*, description: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<plist version="1.0">\n'
        "  <dict>\n"
        "    <key>adjustmentBaseVersion</key><integer>0</integer>\n"
        f"    <key>pamTinyLibrarySource</key><string>{description}</string>\n"
        "  </dict>\n"
        "</plist>\n"
    ).encode()


def plain_text(*, description: str, body: str) -> bytes:
    return f"{body}\n\n(pam tiny library: {description})\n".encode()


def corrupt_jpeg(*, description: str) -> bytes:
    """A JPEG that starts as one and then stops making sense mid-APP1.

    exiftool reports `FileType: JPEG` and `Warning: JPEG format error` - and, critically,
    it STILL EMITS A JSON RECORD. See `tests/test_tiny_library.py` for what that means
    for spec 9.1's "a corrupt JPEG" and why the pinned scan error count is what it is.
    """
    return b"\xff\xd8\xff\xe1\x02\x00Exif\x00\x00MM\x00\x2a" + description.encode("utf-8")[:24]


# --------------------------------------------------------------------------------------
# The library itself: one row per file, spec 9.1's list made executable.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    name: str
    payload: bytes
    why: str  # the spec 9.1 clause this file is here to cover


@dataclass
class BuildReport:
    written: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (name, reason)


def _still(
    name: str,
    *,
    date: str | None = None,
    offset: str | None = None,
    gps: tuple[float, float] | None = None,
    comment: str | None = None,
    pixels: tuple[bytes, ...] | None = None,
) -> bytes:
    """The right container for `name`'s extension, carrying `date` if the format takes it.

    `name` is also written INTO the file (ImageDescription / a JPEG COM segment / a PNG
    tEXt chunk), which is what makes every file's sha256 distinct. Two files with the
    same date and no distinguishing bytes would collapse into one entry in any
    digest-set comparison, and a lost file could then hide behind its twin.
    """
    ext = name.rsplit(".", 1)[1].lower()
    described = f"pam tiny library: {name}"
    if comment is not None:
        described = f"{described} | {comment}"
    blob = (
        exif_tiff(description=described, date=date, offset=offset, gps=gps)
        if date is not None or gps is not None
        else None
    )
    if ext in ("jpg", "jpeg"):
        return jpeg(exif=blob, comment=described)
    if ext == "png":
        if pixels is None:
            return png(exif=blob, description=described)
        return png(exif=blob, description=described, pixels=pixels)
    if ext in ("heic", "heif"):
        return heif(brand=ext.encode("ascii"), description=described)
    # tif/tiff and every RAW extension are the TIFF block on its own. A vendor RAW file
    # is a TIFF with private IFDs; pam classifies by EXTENSION (pam/config.py) and reads
    # dates from the standard Exif IFD, so a plain TIFF body exercises both faithfully.
    # It is NOT a vendor-real CR2/NEF/ARW - noted in the library's README.
    return exif_tiff(description=described, date=date, offset=offset, gps=gps)


def _movie(
    name: str,
    *,
    unix_time: int,
    moov_last: bool = False,
    extras: tuple[bytes, ...] = (),
) -> bytes:
    """A device-shaped QuickTime file, written by `tests/video_samples.py`, in memory.

    The writer needs a path, so this round-trips through a scratch file rather than
    refactoring it - which keeps `write_samples()` producing byte-identical output for
    the tests that already depend on it (tests/archives.py::device_mov_bytes, the whole
    fault matrix).

    THE SCRATCH FILE GOES IN A TEMPORARY DIRECTORY, NOT IN `LIBRARY_DIR` (M6 Task 7
    review, M443). It used to live at `LIBRARY_DIR / "_movie_scratch.tmp"`, which meant
    `build(some_other_dest)` wrote into and deleted from the repository's COMMITTED
    fixture folder - so the rebuild test, which builds into `tmp_path`, quietly removed
    any file of that name from `tests/tiny_library/` and passed. A builder must not
    touch a directory it was not asked to write to.
    """
    with tempfile.TemporaryDirectory() as scratch_dir:
        scratch = Path(scratch_dir) / "movie.tmp"
        video_samples.write_movie(
            scratch,
            moov_last=moov_last,
            extras=(*extras, video_samples.box(b"free", name.encode("utf-8"))),
            unix_time=unix_time,
        )
        return scratch.read_bytes()


# Distinct capture instants, so a name diff points at one row of the table rather than at
# "some 2019 photo". Two pairs deliberately SHARE a date (0704 and the ext-case block) so
# the per-date seq run in pam/naming/units.py is exercised rather than assumed.
D_0704A = "2019:07:04 18:23:11"
D_0704B = "2019:07:04 19:00:00"
NO_MOOV_DATE = -video_samples.EPOCH_1904  # mvhd/tkhd/mdhd all read back as the 1904 sentinel
QT_2019 = video_samples.CREATE_UNIX
QT_2021 = int(datetime(2021, 10, 11, 5, 6, 7, tzinfo=UTC).timestamp())
QT_2022 = int(datetime(2022, 11, 12, 6, 7, 8, tzinfo=UTC).timestamp())
QT_2013 = int(datetime(2013, 6, 3, 14, 15, 16, tzinfo=UTC).timestamp())


# Every reserved-stem row's capture date, in one place, because they are now split across
# two functions and the proposed names in tests/test_tiny_library.py are pinned to them.
RESERVED_DATES = {
    "con.jpg": "2021:04:05 08:00:00",
    "prn.JPG": "2021:04:06 08:00:00",
    "aux.jpg": "2021:04:07 08:00:00",
    "nul.jpg": "2021:04:08 08:00:00",
    "com0.JPG": "2021:04:09 08:00:00",
    "com1.jpg": "2021:04:10 08:00:00",
    "lpt1.tif": "2021:04:11 08:00:00",
    "lpt0.tif": "2021:04:12 08:00:00",
}


def device_name_items() -> list[Item]:
    """The reserved-stem rows GIT CANNOT STORE. Written at fixture time, never committed.

    MEASURED ON THIS MACHINE (Windows 11 26100, Git 2.x), which is the only way this was
    ever going to be settled. Python creates every one of these as an ordinary file and
    real exiftool reads them - CLAUDE.md's note is correct and spec 6.2 rule 4 is not
    obsolete. But `git add` refuses seven of the eight:

        error: open("tests/tiny_library/aux.jpg"): No such file or directory

    for AUX, CON, NUL, PRN, COM1, LPT1 and LPT0, while COM0 indexes normally. So the M6
    plan's "build them on this machine and commit the result" is achievable for exactly
    one of them, and committing the rest would additionally break `git add -A` at the
    repo root for everybody, forever.

    The answer is not to drop the coverage. `items()` keeps COM0, and these seven are
    written into the SCRATCH COPY by the test fixture, from this same table, so the
    pinned scan and plan results still cover all eight. What is lost is only the
    "committed bytes" half of the guarantee for these seven - they are rebuilt each run
    rather than restored, which is stated here and in the library's README rather than
    left for someone to discover from a count that does not add up.
    """
    return [
        Item(
            name=name,
            payload=_still(name, date=RESERVED_DATES[name]),
            why="reserved device stem on input; git refuses to index it",
        )
        for name in (
            "con.jpg",
            "prn.JPG",
            "aux.jpg",
            "nul.jpg",
            "com1.jpg",
            "lpt1.tif",
            "lpt0.tif",
        )
    ]


def items() -> list[Item]:
    """Spec 9.1's list, in order, one Item per file. This table IS the library."""
    out: list[Item] = []

    def add(name: str, payload: bytes, why: str) -> None:
        out.append(Item(name=name, payload=payload, why=why))

    # --- EXIF-dated stills, one per TIFF/JPEG-capable extension ------------------------
    add("exif_basic.jpg", _still("exif_basic.jpg", date=D_0704A), "exif date, no gps")
    add(
        "exif_gps.JPG",
        _still("exif_gps.JPG", date=D_0704B, gps=(37.7749, -122.4194)),
        "gps N/W, date shared with exif_basic",
    )
    add(
        "exif_gps_south.jpg",
        _still("exif_gps_south.jpg", date="2019:08:11 07:30:00", gps=(-33.8688, 151.2093)),
        "gps S/E - the sign-flip path",
    )
    add(
        "exif_offset.jpeg",
        _still("exif_offset.jpeg", date="2019:09:12 22:05:33", offset="-05:00"),
        "OffsetTimeOriginal -> tz_offset_min",
    )
    add("exif_plain.tif", _still("exif_plain.tif", date="2020:01:15 10:00:00"), "tif")
    add(
        "exif_upper.TIFF",
        _still("exif_upper.TIFF", date="2020:02:16 11:11:11"),
        "tiff, uppercase ext",
    )
    add("raw_canon.CR2", _still("raw_canon.CR2", date="2020:03:17 12:12:12"), "raw cr2")
    add("raw_nikon.NEF", _still("raw_nikon.NEF", date="2020:04:18 13:13:13"), "raw nef")
    add("raw_sony.ARW", _still("raw_sony.ARW", date="2020:05:19 14:14:14"), "raw arw")
    add("raw_adobe.DNG", _still("raw_adobe.DNG", date="2020:06:20 15:15:15"), "raw dng")
    add("exif_png.png", _still("exif_png.png", date="2020:07:21 16:16:16"), "png eXIf chunk")

    # THE ONE RAW WITH A CODED IMAGE IN IT (M10 Task 6, design 8.1 rev 17 / amendment A4).
    # The four above are TIFF bodies with no pixels, which is what the scanner needs and
    # what left HT-M9's F1 unreproducible in this suite: `pil_image_loader` raised
    # `UnidentifiedImageError` on every one of them, exactly as it did on three of the
    # real `.NEF` files a human test tried. This one carries the camera's three embedded
    # previews, so the loader's RAW branch has something real to open. `2020:10:24` is
    # used by no other file here, so adding it shifted no existing sequence number.
    add(
        "raw_preview.ARW",
        raw_with_previews("raw_preview.ARW", date="2020:10:24 10:24:00"),
        "raw arw carrying all three embedded previews",
    )

    # --- decodable pixels, for the `phash` enricher (design 8.1, M9 Task 8) ------------
    #
    # WHAT THESE ADD THAT THE LIBRARY DID NOT ALREADY HAVE. Everything above is read by
    # exiftool, which never decodes a pixel, so nothing above was ever built to be looked
    # AT. The 1x1 PNGs and the hand-written JPEGs do decode - that much was measured
    # before these were added, and the M9 plan's premise that the library "has never
    # contained a decodable image" is not quite right - but every JPEG here carries the
    # SAME pixel payload, and a 1x1 image hashes to zero. Neither can distinguish one
    # picture from another, which is the only thing a perceptual hash claims to do.
    #
    # They share a date (and therefore an event and a seq run) because near-duplicates in
    # an archive of real files are seconds apart. `2021-11-05` is used by no other file here, so
    # adding them shifted no existing sequence number.
    for pattern, why in (
        ("base", "a decodable picture with real left-to-right structure"),
        ("near", "a NEAR duplicate of base - dHash differs in a couple of bits"),
        ("far", "base inverted - the farthest a dHash can be from base"),
    ):
        name = f"phash_pattern_{pattern}.png"
        add(
            name,
            _still(name, date="2021:11:05 14:20:00", pixels=phash_rows(pattern)),
            why,
        )

    # --- RAW + JPEG pairs (spec 5.4 raw_jpeg companion units) -------------------------
    for stem, date in (
        ("DSC_1001", "2020:08:22 08:00:00"),
        ("IMG_2002", "2020:09:23 09:00:00"),
    ):
        raw_ext = "NEF" if stem.startswith("DSC") else "CR2"
        add(
            f"{stem}.{raw_ext}",
            _still(f"{stem}.{raw_ext}", date=date),
            "raw half of a raw_jpeg pair",
        )
        add(f"{stem}.JPG", _still(f"{stem}.JPG", date=date), "jpeg half of a raw_jpeg pair")

    # --- Live-Photo pairs (spec 5.4 live_photo companion units) -----------------------
    # 3001 is the shape the M4 real-archive run found on 5 real units: the HEIC primary
    # carries no date at all while the MOV beside it does, and the unit is named from the
    # primary. Keeping it means a change to the primary election is visible here.
    add("IMG_3001.HEIC", _still("IMG_3001.HEIC"), "live photo, dateless HEIC primary")
    add(
        "IMG_3001.MOV",
        _movie("IMG_3001.MOV", unix_time=QT_2021),
        "live photo, dated MOV member",
    )
    add(
        "IMG_3002.JPG",
        _still("IMG_3002.JPG", date="2021:01:02 10:20:30"),
        "live photo, dated JPG primary",
    )
    add(
        "IMG_3002.mov",
        _movie("IMG_3002.mov", unix_time=QT_2021),
        "live photo, lowercase mov member",
    )

    # --- Sidecars (scan.sidecar_exts: xmp + aae) --------------------------------------
    add(
        "sidecar_photo.JPG",
        _still("sidecar_photo.JPG", date="2021:02:03 11:00:00"),
        "xmp sidecar's primary",
    )
    add("sidecar_photo.xmp", xmp(description="sidecar_photo.JPG"), "xmp sidecar")
    add(
        "edited_photo.JPG",
        _still("edited_photo.JPG", date="2021:03:04 12:00:00"),
        "aae sidecar's primary",
    )
    add("edited_photo.AAE", aae(description="edited_photo.JPG"), "aae sidecar (C15)")

    # --- Wrong-clock EXIF -------------------------------------------------------------
    add(
        "wrong_clock_1980.jpg",
        _still("wrong_clock_1980.jpg", date="1980:01:01 00:00:00"),
        "camera clock never set",
    )
    add(
        "wrong_clock_future.jpg",
        _still("wrong_clock_future.jpg", date="2099:12:31 23:59:59"),
        "camera clock in the future",
    )

    # --- Reserved Windows device stems (spec 6.2 rule 4) ------------------------------
    # Only COM0 is here. The other seven cannot be committed - see device_name_items().
    add(
        "com0.JPG",
        _still("com0.JPG", date=RESERVED_DATES["com0.JPG"]),
        "reserved device stem on input (spec 6.2 rule 4's COM0)",
    )

    # --- The long-path case -----------------------------------------------------------
    add(
        f"{LONG_STEM}.JPG",
        _still(f"{LONG_STEM}.JPG", date="2021:05:06 13:00:00"),
        "path near the Windows limit",
    )

    # --- Unicode filenames ------------------------------------------------------------
    for index, name in enumerate(
        (
            "café_señor.jpg",
            "日本語の写真.JPG",
            "Ελληνικά.jpg",
            "Иванов_семья.jpg",
            "party_🎉_time.jpg",
        )
    ):
        add(name, _still(name, date=f"2021:06:{7 + index:02d} 09:00:00"), "non-ASCII filename")

    # --- Filename-dated, no EXIF (source 'filename', medium tier) ---------------------
    add("2005-06-07_holiday.jpg", _still("2005-06-07_holiday.jpg"), "separated filename date")
    add("20060708_trip.JPG", _still("20060708_trip.JPG"), "compact filename date")
    add(
        "IMG_20070809_120000.jpg",
        _still("IMG_20070809_120000.jpg"),
        "android-style filename date",
    )
    add("2008.09.10_scan.tif", _still("2008.09.10_scan.tif"), "dot-separated filename date")

    # --- Truly date-less stills (source 'file_mtime', low tier) -----------------------
    for name in (
        "no_date_alpha.jpg",
        "no_date_beta.PNG",
        "no_date_gamma.tif",
        "no_date_delta.HEIC",
        "no_date_epsilon.HEIF",
    ):
        add(name, _still(name), "no date anywhere -> file_mtime")

    # --- EXIF-less "scans" whose date is only in the image (spec 9.1's OCR case) -------
    # PLACEHOLDERS. The date is a JPEG COM segment / TIFF ImageDescription, not pixels.
    # M12 owns OCR and owes the real files. See this module's docstring and the README.
    add(
        "scan_ocr_placeholder_01.jpg",
        _still(
            "scan_ocr_placeholder_01.jpg",
            comment="PLACEHOLDER rendered date: March 1987 - not pixels, see README",
        ),
        "OCR placeholder, M12 owes the real file",
    )
    add(
        "scan_ocr_placeholder_02.tif",
        _still("scan_ocr_placeholder_02.tif"),
        "OCR placeholder, M12 owes the real file",
    )

    # --- The corrupt JPEG -------------------------------------------------------------
    add(
        "corrupt_broken.jpg",
        corrupt_jpeg(description="corrupt_broken.jpg"),
        "spec 9.1's corrupt JPEG",
    )

    # --- Videos (spec 5.5 / spec 9.1 rev 3) -------------------------------------------
    add(
        "video_qt_local.MOV",
        _movie(
            "video_qt_local.MOV",
            unix_time=QT_2019,
            extras=(video_samples.apple_keys_creationdate("2019-07-04T18:23:11-0700"),),
        ),
        "CreationDate with a real offset -> qt_local",
    )
    add(
        "video_qt_utc_gps.mp4",
        _movie(
            "video_qt_utc_gps.mp4",
            unix_time=QT_2022,
            extras=(video_samples.udta_gps("+37.7749-122.4194/"),),
        ),
        "mvhd date + udta GPS -> qt_utc",
    )
    add(
        "video_moov_last.MP4",
        _movie("video_moov_last.MP4", unix_time=QT_2022, moov_last=True),
        "moov after mdat - the C13 -fast2 defect",
    )
    add(
        "video_zero_offset.MOV",
        _movie(
            "video_zero_offset.MOV",
            unix_time=QT_2021,
            extras=(video_samples.apple_keys_creationdate("2021-10-11T05:06:07+0000"),),
        ),
        "CreationDate +00:00 -> qt_utc, never qt_local (C14)",
    )
    add(
        "video_epoch_sentinel.mp4",
        _movie("video_epoch_sentinel.mp4", unix_time=NO_MOOV_DATE),
        "1904 sentinel in every atom -> file_mtime",
    )
    add(
        "2018-05-04_named_clip.mp4",
        _movie("2018-05-04_named_clip.mp4", unix_time=NO_MOOV_DATE),
        "sentinel dates + a filename date -> filename",
    )
    add(
        "video_no_moov.MOV",
        video_samples.box(b"ftyp", b"qt  " + struct.pack(">I", 512) + b"qt  ")
        + video_samples.box(b"mdat", b"\x00" * 8192),
        "no moov at all -> file_mtime",
    )
    add("clip_camcorder.AVI", avi(description="clip_camcorder.AVI"), "avi (spec 9.1 rev 3)")
    add("clip_avchd.MTS", m2ts(description="clip_avchd.MTS"), "mts / AVCHD")

    # --- Stem-mates outside sidecar_exts: the spec 6.3.3a left-behind report -----------
    add(
        "left_behind_pair.JPG",
        _still("left_behind_pair.JPG", date="2021:07:08 14:00:00"),
        "left-behind report: the renamed half",
    )
    add(
        "left_behind_pair.thm",
        plain_text(description="left_behind_pair.thm", body="thumbnail stub"),
        "left-behind report: the stranded half",
    )
    add(
        "takeout_photo.JPG",
        _still("takeout_photo.JPG", date="2021:07:09 15:00:00"),
        "left-behind report: Google Takeout shape",
    )
    add(
        "takeout_photo.json",
        plain_text(description="takeout_photo.json", body='{"title": "takeout_photo.JPG"}'),
        "left-behind report: the Takeout json",
    )

    # --- Non-media files that simply have to be ignored --------------------------------
    add(
        "notes.txt",
        plain_text(
            description="notes.txt", body="A stray text file, as users' archives contain."
        ),
        "media_type 'other'",
    )

    # --- The extension-case matrix ----------------------------------------------------
    # Almost every file of a phone library is UPPERCASE-extension (M4 review), and
    # almost every plan item over one existed only to correct the case. Every supported
    # extension therefore appears in both cases, all on one date, so the block also
    # exercises a 30-deep per-date seq run in one go. The date is in the FILENAME because
    # HEIC/HEIF/AVI/MTS cannot carry EXIF here - one source for all thirty rows.
    for ext in (*IMAGE_EXTS, *VIDEO_EXTS):
        for label, written_ext in (("lower", ext), ("upper", ext.upper())):
            name = f"2022-02-02_extcase_{ext}_{label}.{written_ext}"
            if ext in ("mov", "mp4"):
                payload = _movie(name, unix_time=NO_MOOV_DATE)
            elif ext == "avi":
                payload = avi(description=name)
            elif ext == "mts":
                payload = m2ts(description=name)
            else:
                payload = _still(name)
            add(name, payload, f"extension case matrix: .{written_ext}")

    # --- One subdirectory, so rel_dir and per-directory collision scoping are covered --
    add(
        "2013 Vacation/sub_exif_a.jpg",
        _still("sub_exif_a.jpg", date="2013:06:01 09:00:00"),
        "subdirectory, first of a same-date pair",
    )
    add(
        "2013 Vacation/sub_exif_b.JPG",
        _still("sub_exif_b.JPG", date="2013:06:01 10:00:00"),
        "subdirectory, second of a same-date pair",
    )
    add(
        "2013 Vacation/sub_dateless.jpg", _still("sub_dateless.jpg"), "subdirectory, file_mtime"
    )
    add(
        "2013 Vacation/sub_pair.NEF",
        _still("sub_pair.NEF", date="2013:06:02 11:00:00"),
        "subdirectory raw_jpeg pair",
    )
    add(
        "2013 Vacation/sub_pair.JPG",
        _still("sub_pair.JPG", date="2013:06:02 11:00:00"),
        "subdirectory raw_jpeg pair",
    )
    add(
        "2013 Vacation/sub_video.MOV",
        _movie("sub_video.MOV", unix_time=QT_2013),
        "subdirectory qt_utc video",
    )

    add("README.md", README.encode("utf-8"), "the library's own README (media_type 'other')")
    return out


def stamp_mtimes(root: Path) -> None:
    """Force every file under `root` to FIXED_MTIME_EPOCH. The determinism fix.

    Called by the builder AND by the test fixture, because git restores content and not
    timestamps: a fresh clone would otherwise date every EXIF-less file to the moment of
    checkout. Directories are left alone - nothing reads their mtime.
    """
    for path in sorted(root.rglob("*")):
        if path.is_file():
            os.utime(path, (FIXED_MTIME_EPOCH, FIXED_MTIME_EPOCH))


def _write_all(dest: Path, wanted: list[Item]) -> BuildReport:
    """Write `wanted` into `dest`. A file the filesystem refuses is skipped, not fatal.

    Skipping rather than raising is the M6 plan's instruction, and it is aimed at exactly
    two rows: the reserved device stems and the 144-character filename. Both succeed on
    this machine and both are the kind of thing another filesystem can refuse, and a
    builder that died on them would be unusable precisely where its output matters. The
    caller decides whether a skip is acceptable - `main` prints it, the test fixture
    fails on it.
    """
    report = BuildReport()
    dest.mkdir(parents=True, exist_ok=True)
    for item in wanted:
        path = dest / item.name
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(item.payload)
        except OSError as error:
            report.skipped.append((item.name, str(error)))
            continue
        report.written.append(item.name)
    stamp_mtimes(dest)
    return report


def build(dest: Path) -> BuildReport:
    """Write the COMMITTED half of the library into `dest` - everything git can store."""
    return _write_all(dest, items())


def write_device_name_files(dest: Path) -> BuildReport:
    """Add the seven reserved-stem files git cannot index. See `device_name_items()`.

    Called by the test fixture against its scratch copy, never against the committed
    folder: a `con.jpg` sitting in `tests/tiny_library/` would make `git add -A` fail at
    the repository root for every future commit, whether or not it touched this library.
    """
    return _write_all(dest, device_name_items())


README = """# tests/tiny_library - the spec 9.1 benchmark archive

Built by `tools/build_tiny_library.py`. **Do not edit these files by hand.** Change the
table in the builder and re-run it; `tests/test_tiny_library.py` fails if the committed
bytes and a fresh build disagree.

    .venv\\Scripts\\python tools\\build_tiny_library.py

## What it is for

Spec 9.1's tiny library is the frozen input that makes "pam read N files" and "pam
proposed exactly these names" into regression signals rather than anecdotes. Every later
milestone measures against the counts pinned in `tests/test_tiny_library.py`.

## What is real and what is a stand-in

Everything here is synthesised from bytes, with no Pillow and no new dependency. Four
things are honestly less than the spec's wording, and they are listed here rather than
discovered later:

1. **The OCR "scans" are PLACEHOLDERS.** Spec 9.1 asks for EXIF-less scans with the date
   *rendered into the image*. Rendering text into pixels needs a rasteriser.
   `scan_ocr_placeholder_01.jpg` and `scan_ocr_placeholder_02.tif` carry their date as
   machine-readable text (a JPEG COM segment / TIFF ImageDescription) instead, so they
   exercise the EXIF-less date path and NOTHING about OCR. **M12 owns OCR and owes the
   real files.** Do not treat a passing test here as OCR coverage.
2. **The RAW files are TIFF bodies, not vendor containers.** `.CR2`, `.NEF`, `.ARW` and
   `.DNG` hold a standard TIFF/Exif block. pam classifies by extension and reads the
   standard Exif IFD, so both of those paths are faithful; vendor-private IFDs and
   maker-note parsing are not covered.
3. **The HEIC/HEIF files carry no EXIF, AND NO PIXELS EITHER.** They are real ISO-BMFF
   containers with the right brand, but embedding an Exif item - or an actual coded
   image - needs a HEIF encoder. Those files take their date from their filename or their
   mtime, which is stated in the builder's table. **Pillow with `pillow-heif` registered
   cannot open them**, and no test here may claim HEIC decode coverage: most phone
   photographs are HEIC, and only HT-M9 reaches a real one.
4. **Seven of the eight reserved-device-name files are NOT committed.** `git add` on
   Windows refuses `aux.jpg`, `con.jpg`, `nul.jpg`, `prn.JPG`, `com1.jpg`, `lpt1.tif` and
   `lpt0.tif` with `open(...): No such file or directory`, and committing them would
   break `git add -A` at the repository root for everyone. Only `com0.JPG` is here. The
   other seven are written into the scratch copy by the test fixture, from the same
   table (`build_tiny_library.write_device_name_files`), so the pinned scan and plan
   results still cover all eight - but their bytes are rebuilt each run rather than
   restored from the repository.

## What M9 Task 8 added

Three files - `phash_pattern_base.png`, `phash_pattern_near.png`, `phash_pattern_far.png`
- and one generalisation. Until M9 nothing in this project decoded a pixel: exiftool reads
metadata and M7's review UI extracts a preview the camera embedded. The `phash` enricher
(design 8.1, dHash) decodes, and a perceptual hash only claims one thing - that two
pictures that look alike hash alike - which needs fixtures that differ in their PIXELS.
The library had none: every JPEG here carries the same pixel payload and every PNG was
one black pixel.

The three are 32x32 greyscale, generated by `build_tiny_library.phash_rows()` from a
formula, still with **no Pillow** - `png()` now writes any number of scanlines through
`stored_zlib()`, the generalisation of the hand-written deflate constant it replaces. A
Pillow-encoded fixture would make these bytes a function of the installed Pillow, and the
byte-identical-rebuild test would fail the next time Pillow changed its encoder.

`near` is a near duplicate of `base` and `far` is `base` inverted; `tests/test_phash.py`
pins the Hamming distances between them, so editing these pixels fails there with a
message that says why.

## The mtime rule

mtime is not in a file's bytes and git does not preserve it, so the date-less files here
would otherwise be dated to the moment of checkout. `build_tiny_library.stamp_mtimes()`
forces every file to a fixed instant, and the test fixture calls it before scanning.
**Any harness that scans this library must call it too**, or the proposed names for the
date-less files will not match the pinned set.
"""


def main(argv: list[str]) -> int:
    dest = Path(argv[0]).expanduser().resolve() if argv else LIBRARY_DIR
    report = build(dest)
    # `tools/ht_manifest.py` is the project's ONE hasher (M6 plan Task 7: "reuse it; do
    # not write a third"), tested at Task 2. The manifest lives BESIDE the library rather
    # than inside it: a manifest inside the archive would have to contain its own hash,
    # and it would also be one more file for `pam scan` to count.
    MANIFEST_PATH.write_text(
        json.dumps(ht_manifest.snapshot(dest), indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(report.written)} files to {dest}")
    print(f"Manifest: {MANIFEST_PATH}")
    print(
        f"Not written here (git cannot index them; the test fixture writes them into its"
        f" own copy): {', '.join(item.name for item in device_name_items())}"
    )
    for name, reason in report.skipped:
        print(f"SKIPPED  {name}: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
