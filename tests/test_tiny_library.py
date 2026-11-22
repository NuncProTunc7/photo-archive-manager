"""The spec 9.1 tiny library, pinned: it rebuilds byte-identically, and pam reads it
and names it exactly this way (M6 Task 7).

WHAT IS BEING PROTECTED. `tests/tiny_library/` is the frozen input every later milestone
measures against. Two numbers come out of it and both are asserted below:

  * how many files `pam scan` OPENED, and how many it could not read;
  * the complete set of names `pam plan` proposed.

A diff in the second is a naming regression - spec 9.2 priority 1 - and the unicode,
reserved-device-name and long-path rows are the ones that exist to make it visible.

REAL EXIFTOOL, NOT `tests/fake_exiftool.py`, AND THAT IS THE POINT. Everywhere else in
the suite the fake is right: those tests are about pam's logic and a real subprocess
would only add time and a dependency on the machine. Here the fixture IS the metadata.
`tests/fake_exiftool.py` reads a `.tags.json` sidecar written by the test that calls it,
so a library scanned through it would prove that pam can read a file the test itself
wrote the answers for - and every one of spec 9.1's interesting rows (the moov-last
video, the zero-offset `CreationDate`, the PNG `eXIf` chunk, the AVI with no date at all)
is a claim about what REAL exiftool returns. Measured cost: 0.78s and 0.74s for the two
tests that scan (`--durations`), against a whole-suite wall time of ~92s - the price of
the only thing this file can honestly assert, and the 95 extra files in `tests/` cost
nothing at collection (1178 tests collected in 0.23s). The tests skip when exiftool is
absent, exactly as `tests/test_video_real_exiftool.py` does.

MTIME, AND WHY ONE COMPONENT OF THE EXPECTED NAMES IS COMPUTED. A file's mtime is not in
its bytes and git does not preserve it: a fresh clone stamps every file with the moment
of checkout. Spec 9.1 requires date-less files, and `pam/scan/objective.py` falls back to
`datetime.fromtimestamp(mtime)` for those - so without intervention fourteen of the
ninety-seven names below would be the clone date, and this file would fail on every
machine except the one that wrote it. `build_tiny_library.stamp_mtimes()` is the fix and
the fixture calls it before scanning.

That pins the INSTANT but not the DATE, because `fromtimestamp` is local: 2011-06-15
12:00 UTC reads as the 15th almost everywhere and as the 16th east of UTC+12. So
`MTIME_DATE` below is computed from the same fixed constant rather than written out.
This is the one place in this file where an expectation is derived rather than pinned,
and the derivation is deliberately shallow - `strftime` on a constant, no pam code
involved - so a change to how pam formats a date token still shows up as a diff. The
constant sits in a three-day window (2011-06-14/15/16) that no dated file in the library
occupies, so whichever of the two dates a reader gets, that bucket never merges with a
real one and the seq run below is stable everywhere.

Nothing else that reaches a proposed name reads a clock: `files.first_seen_at`,
`files.last_scanned_at` and `plans.created_at` are all wall-clock and none of them is a
token input (`pam/naming/tokens.py`).
"""

import shutil
import struct
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import build_tiny_library  # noqa: E402
import ht_manifest  # noqa: E402
from archives import catalog, materialise_tiny_library  # noqa: E402

from pam import config  # noqa: E402
from pam.catalog import repo  # noqa: E402
from pam.naming.planner import run_plan  # noqa: E402
from pam.scan.runner import run_scan  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("exiftool") is None, reason="the tiny library is pinned against REAL exiftool"
)

MTIME_DATE = datetime.fromtimestamp(build_tiny_library.FIXED_MTIME_EPOCH).strftime("%Y-%m-%d")

LIBRARY = Path(__file__).resolve().parent / "tiny_library"

# What `pam scan` finds. Every one of these is a claim some later milestone can break.
# 99 files come out of the repository and 7 more are written by the fixture (the reserved
# device stems git will not index), so the archive under test holds 106.
COMMITTED_FILES = 99
TOTAL_FILES = 106
IMAGES, VIDEOS, SIDECARS, OTHER = 80, 20, 2, 4
READ = 100  # images + videos: what pam actually opens (walker.READ_MEDIA_TYPES)
SCAN_ERRORS = 0

# What `pam plan` makes of it.
PLANNED = 102  # images + videos + sidecars; the four `other` files are not renamed
LEFT_BEHIND = 2  # left_behind_pair.thm and takeout_photo.json (spec 6.3.3a)
# M9 TASK 9 MOVED FOUR ITEMS FROM `high` TO `medium`, and the shift is the whole point of
# the migration that caused it. `pam plan` now geocodes each event's GPS centroid (spec
# 8.3) and writes the label with `label_source='geocode'`; spec 6.1 rev 15 makes
# `{event}`'s tier the tier of the fact that produced the LABEL, and a confidence-less
# enricher is capped at `medium` (spec 8.1). Six names below gained a `{event}` token and
# four of them were `high` on every other token, so the item tier is now the event's.
# `low` is unchanged at 15: a dropped tier floor cannot be reached by adding a token.
# M10 TASK 6 ADDED ONE `high` ITEM: `raw_preview.ARW` carries a real EXIF date and no
# token that caps a tier, exactly like the four RAW files it sits beside.
TIERS = {"high": 44, "medium": 43, "low": 15}

# The pinned set. Old relative path -> proposed filename. Sorted by old path, which is
# also the order `pam/naming/units.py` assigns seq numbers in, so a reader can follow a
# seq run down the page.
#
# SIX ROWS CARRY A PLACE NAME SINCE M9 TASK 9, and they are the end-to-end proof that
# spec 8.3's event labels reach a filename: `San-Francisco` and `Sydney` come out of
# `reverse_geocoder`'s offline dataset, through the GPS the scanner read out of the
# committed fixtures, through the event partition's centroid, into `events.label` and out
# through the `{event-or-scene}` half of the default template. Two of the six are VIDEOS,
# which is spec 8.1 rev 3's reason for giving geocode the `capture` row rather than
# pixels. Nothing here is skipped when `reverse_geocoder` is absent: it is a declared
# runtime dependency, `pam doctor` prints a red line for it, and a skip would be a
# disarmed guard over the only test in the suite that watches a place name become a name.
EXPECTED_NAMES = {
    "2005-06-07_holiday.jpg": "2005-06-07_001.jpg",
    "20060708_trip.JPG": "2006-07-08_001.JPG",
    "2008.09.10_scan.tif": "2008-09-10_001.tif",
    "2013 Vacation/sub_dateless.jpg": f"{MTIME_DATE}_001.jpg",
    "2013 Vacation/sub_exif_a.jpg": "2013-06-01_001.jpg",
    "2013 Vacation/sub_exif_b.JPG": "2013-06-01_002.JPG",
    "2013 Vacation/sub_pair.JPG": "2013-06-02_001.JPG",
    "2013 Vacation/sub_pair.NEF": "2013-06-02_001.NEF",
    "2013 Vacation/sub_video.MOV": "2013-06-03_001.MOV",
    "2018-05-04_named_clip.mp4": "2018-05-04_001.mp4",
    "2022-02-02_extcase_arw_lower.arw": "2022-02-02_001.arw",
    "2022-02-02_extcase_arw_upper.ARW": "2022-02-02_002.ARW",
    "2022-02-02_extcase_avi_lower.avi": "2022-02-02_003.avi",
    "2022-02-02_extcase_avi_upper.AVI": "2022-02-02_004.AVI",
    "2022-02-02_extcase_cr2_lower.cr2": "2022-02-02_005.cr2",
    "2022-02-02_extcase_cr2_upper.CR2": "2022-02-02_006.CR2",
    "2022-02-02_extcase_dng_lower.dng": "2022-02-02_007.dng",
    "2022-02-02_extcase_dng_upper.DNG": "2022-02-02_008.DNG",
    "2022-02-02_extcase_heic_lower.heic": "2022-02-02_009.heic",
    "2022-02-02_extcase_heic_upper.HEIC": "2022-02-02_010.HEIC",
    "2022-02-02_extcase_heif_lower.heif": "2022-02-02_011.heif",
    "2022-02-02_extcase_heif_upper.HEIF": "2022-02-02_012.HEIF",
    "2022-02-02_extcase_jpeg_lower.jpeg": "2022-02-02_013.jpeg",
    "2022-02-02_extcase_jpeg_upper.JPEG": "2022-02-02_014.JPEG",
    "2022-02-02_extcase_jpg_lower.jpg": "2022-02-02_015.jpg",
    "2022-02-02_extcase_jpg_upper.JPG": "2022-02-02_016.JPG",
    "2022-02-02_extcase_mov_lower.mov": "2022-02-02_017.mov",
    "2022-02-02_extcase_mov_upper.MOV": "2022-02-02_018.MOV",
    "2022-02-02_extcase_mp4_lower.mp4": "2022-02-02_019.mp4",
    "2022-02-02_extcase_mp4_upper.MP4": "2022-02-02_020.MP4",
    "2022-02-02_extcase_mts_lower.mts": "2022-02-02_021.mts",
    "2022-02-02_extcase_mts_upper.MTS": "2022-02-02_022.MTS",
    "2022-02-02_extcase_nef_lower.nef": "2022-02-02_023.nef",
    "2022-02-02_extcase_nef_upper.NEF": "2022-02-02_024.NEF",
    "2022-02-02_extcase_png_lower.png": "2022-02-02_025.png",
    "2022-02-02_extcase_png_upper.PNG": "2022-02-02_026.PNG",
    "2022-02-02_extcase_tif_lower.tif": "2022-02-02_027.tif",
    "2022-02-02_extcase_tif_upper.TIF": "2022-02-02_028.TIF",
    "2022-02-02_extcase_tiff_lower.tiff": "2022-02-02_029.tiff",
    "2022-02-02_extcase_tiff_upper.TIFF": "2022-02-02_030.TIFF",
    # A companion unit renames as one: both members take the primary's stem (spec 5.4).
    "DSC_1001.JPG": "2020-08-22_001.JPG",
    "DSC_1001.NEF": "2020-08-22_001.NEF",
    "IMG_2002.CR2": "2020-09-23_001.CR2",
    "IMG_2002.JPG": "2020-09-23_001.JPG",
    "IMG_20070809_120000.jpg": "2007-08-09_001.jpg",
    # The M4 real-archive shape: the HEIC primary carries no date, the MOV beside it
    # does, and the unit is named from the primary - so a live photo with a real
    # QuickTime date lands in the file_mtime bucket. Pinned because it is a REAL
    # behaviour worth noticing a change in, not because it is desirable.
    "IMG_3001.HEIC": f"{MTIME_DATE}_002.HEIC",
    "IMG_3001.MOV": f"{MTIME_DATE}_002.MOV",
    "IMG_3002.JPG": "2021-01-02_001.JPG",
    "IMG_3002.mov": "2021-01-02_001.mov",
    "aux.jpg": "2021-04-07_001.jpg",
    "café_señor.jpg": "2021-06-07_001.jpg",
    "clip_avchd.MTS": f"{MTIME_DATE}_003.MTS",
    "clip_camcorder.AVI": f"{MTIME_DATE}_004.AVI",
    "com0.JPG": "2021-04-09_001.JPG",
    "com1.jpg": "2021-04-10_001.jpg",
    "con.jpg": "2021-04-05_001.jpg",
    "corrupt_broken.jpg": f"{MTIME_DATE}_005.jpg",
    "edited_photo.AAE": "2021-03-04_001.AAE",
    "edited_photo.JPG": "2021-03-04_001.JPG",
    # 2019-07-04 is shared by three units, so this is the seq run: ordered by
    # (capture_dt, rel_path), which puts 18:23:11 first and 19:00:00 last.
    "exif_basic.jpg": "2019-07-04_San-Francisco_001.jpg",
    "video_qt_local.MOV": "2019-07-04_San-Francisco_002.MOV",
    "exif_gps.JPG": "2019-07-04_San-Francisco_003.JPG",
    "exif_gps_south.jpg": "2019-08-11_Sydney_001.jpg",
    "exif_offset.jpeg": "2019-09-12_001.jpeg",
    "exif_plain.tif": "2020-01-15_001.tif",
    "exif_png.png": "2020-07-21_001.png",
    "exif_upper.TIFF": "2020-02-16_001.TIFF",
    "left_behind_pair.JPG": "2021-07-08_001.JPG",
    f"{build_tiny_library.LONG_STEM}.JPG": "2021-05-06_001.JPG",
    "lpt1.tif": "2021-04-11_001.tif",
    "lpt0.tif": "2021-04-12_001.tif",
    "no_date_alpha.jpg": f"{MTIME_DATE}_006.jpg",
    "no_date_beta.PNG": f"{MTIME_DATE}_007.PNG",
    "no_date_delta.HEIC": f"{MTIME_DATE}_008.HEIC",
    "no_date_epsilon.HEIF": f"{MTIME_DATE}_009.HEIF",
    "no_date_gamma.tif": f"{MTIME_DATE}_010.tif",
    "nul.jpg": "2021-04-08_001.jpg",
    "party_🎉_time.jpg": "2021-06-11_001.jpg",
    # The three decodable-pixel files M9 Task 8 added for `phash`. They share one date that
    # no other file here uses, so they are their own seq run and shifted nobody else's.
    "phash_pattern_base.png": "2021-11-05_001.png",
    "phash_pattern_far.png": "2021-11-05_002.png",
    "phash_pattern_near.png": "2021-11-05_003.png",
    "prn.JPG": "2021-04-06_001.JPG",
    "raw_adobe.DNG": "2020-06-20_001.DNG",
    "raw_canon.CR2": "2020-03-17_001.CR2",
    "raw_nikon.NEF": "2020-04-18_001.NEF",
    # M10 TASK 6'S ADDITION, and the only RAW here with a coded image inside it. Its date
    # is used by no other file, so it is its own seq run and shifted nobody else's.
    "raw_preview.ARW": "2020-10-24_001.ARW",
    "raw_sony.ARW": "2020-05-19_001.ARW",
    "scan_ocr_placeholder_01.jpg": f"{MTIME_DATE}_011.jpg",
    "scan_ocr_placeholder_02.tif": f"{MTIME_DATE}_012.tif",
    "sidecar_photo.JPG": "2021-02-03_001.JPG",
    "sidecar_photo.xmp": "2021-02-03_001.xmp",
    "takeout_photo.JPG": "2021-07-09_001.JPG",
    "video_epoch_sentinel.mp4": f"{MTIME_DATE}_013.mp4",
    "video_moov_last.MP4": "2022-11-12_San-Francisco_001.MP4",
    "video_no_moov.MOV": f"{MTIME_DATE}_014.MOV",
    "video_qt_utc_gps.mp4": "2022-11-12_San-Francisco_002.mp4",
    "video_zero_offset.MOV": "2021-10-11_001.MOV",
    "wrong_clock_1980.jpg": "1980-01-01_001.jpg",
    "wrong_clock_future.jpg": "2099-12-31_001.jpg",
    "Ελληνικά.jpg": "2021-06-09_001.jpg",
    "Иванов_семья.jpg": "2021-06-10_001.jpg",
    "日本語の写真.JPG": "2021-06-08_001.JPG",
}


@pytest.fixture()
def library(tmp_path):
    """The committed library, copied to a scratch archive with its mtimes stamped.

    The work is in `archives.materialise_tiny_library`, which is where the reasoning
    about the copy, the seven uncommittable device-stem files and the mtime stamping
    now lives. It moved there at M6 Task 8, when the privacy canary became a second
    consumer: `tests/test_net_canary.py` importing this *test* module by name to borrow
    a fixture is exactly the coupling `tests/guards.py` documents as the reason
    `archives.py` exists at all.
    """
    # Short, because one library filename is 144 characters long.
    return materialise_tiny_library(tmp_path / "a")


# --- the library's size, where it is written in PROSE ---------------------------------
#
# M9 TASK 8'S REVIEW, FINDING 2. Task 8 grew the library from 102 to 105 files and updated
# the counts in seven places. There were EIGHT: `tests/archives.py`'s own docstring - the
# one `tools/ci_gate_library.py` points at as authoritative - still said 95 and 102, and
# nothing went red, because a number in a docstring is executed by nothing. Two sentences
# about the same fixture disagreeing is how M769/M776's lesson arrives here: a copy needs a
# FLOOR written from the side that knows the answer.
#
# Each entry is (path, sentence). The sentence is formatted with this module's own
# constants - which `test_scan_reads_every_file_it_should_and_records_no_error` pins
# against a real scan - and whitespace is normalised so a reflowed comment does not fail.
LIBRARY_COUNT_PROSE = (
    ("tests/archives.py", "holds {committed} files and the library under test holds {total}"),
    ("tests/test_net_canary.py", "the short version is {total} files"),
    ("tests/test_webui_thumbs.py", "sweeping all {total} files"),
    (
        "tools/ci_gate_library.py",
        "holds {committed} files and the library under test holds {total}",
    ),
    ("tools/ci_gate_library.py", "exiting 0 over {committed} files instead of {total}"),
    ("tests/test_enrich_gate.py", "{total} files means one commit at the fiftieth"),
)
# Exact, not `> 0`: an emptied tuple is caught by either, but a NARROWED one - five of the
# six, the shape that actually happens when somebody deletes the entry their edit broke -
# is caught only by the length.
assert len(LIBRARY_COUNT_PROSE) == 6, "the prose sites were narrowed, not the library"


def test_every_prose_statement_of_the_librarys_SIZE_says_the_current_size():
    """Six sentences in five files quote how big the tiny library is. None is executed."""
    repository = Path(__file__).resolve().parent.parent
    for relative, sentence in LIBRARY_COUNT_PROSE:
        expected = sentence.format(committed=COMMITTED_FILES, total=TOTAL_FILES)
        text = " ".join((repository / relative).read_text(encoding="utf-8").split())
        text = text.replace("# ", "")
        assert expected in " ".join(text.split()), (
            f"{relative} no longer says {expected!r}; a milestone that grows the library"
            " has to move this sentence too, and nothing else in the suite reads it"
        )


def test_the_generalised_deflate_writer_reproduces_the_constant_it_replaced():
    """M9 Task 8 turned a hand-written deflate constant into `stored_zlib()`. This is the
    control for that change: the thirteen bytes the builder used to write for a 1x1 PNG
    are exactly what the function returns for the same payload, which is why no PNG
    committed before M9 moved. Without it the claim rests on the rebuild diff alone, and
    a rebuild diff cannot say WHY two files agree.

    The payload is spelled from the PNG spec - one filter byte plus one greyscale pixel -
    and not read out of the builder, and the expected bytes are written out here rather
    than compared with `build_tiny_library`'s own constant.
    """
    assert build_tiny_library.stored_zlib(b"\x00\x00").hex() == "7801010200fdff000000020001"
    assert build_tiny_library.stored_zlib(b"\x00\x00") == build_tiny_library._STORED_IDAT_1X1


def test_the_phash_fixtures_carry_three_different_pictures():
    """What M9 Task 8 added, asserted at the level this file owns: bytes on disk.

    The perceptual DISTANCES between them are `tests/test_phash.py`'s, which is where the
    decoder lives. Here it is only that the three files exist, are 32x32 greyscale PNGs by
    their own IHDR, and hold three different pixel payloads - because a builder bug that
    wrote the same rows three times would leave every phash assertion downstream true and
    meaningless.
    """
    payloads = {}
    for pattern in build_tiny_library.PHASH_PATTERNS:
        path = LIBRARY / f"phash_pattern_{pattern}.png"
        blob = path.read_bytes()
        assert blob[:8] == b"\x89PNG\r\n\x1a\n", path
        # IHDR payload starts at byte 16: width, height, bit depth, colour type.
        assert blob[16:26] == struct.pack(">IIBB", 32, 32, 8, 0), f"{path} is not 32x32 L8"
        payloads[pattern] = blob
    assert len(set(payloads.values())) == 3
    assert len(payloads) == 3, "the builder declares exactly three phash patterns"


def test_a_rebuild_of_the_library_is_byte_identical_to_what_is_committed(tmp_path):
    """The determinism claim, which every other assertion in this file rests on.

    Compared through `tools/ht_manifest.py` - the project's one hasher, tested at M6
    Task 2 - against the manifest committed beside the library. Sizes as well as digests,
    so a failure says which file and by how much rather than only "a hash moved".

    A failure here means either the builder stopped being a pure function of its table
    (a clock, a `zlib.compress`, a set iteration order) or somebody edited a file in
    `tests/tiny_library/` by hand. Both are the same fix: change the table, re-run the
    builder, commit what it wrote.
    """
    fresh = tmp_path / "fresh"
    report = build_tiny_library.build(fresh)
    assert report.skipped == [], "this machine refused a file the committed library has"
    committed = ht_manifest.snapshot(build_tiny_library.LIBRARY_DIR)
    assert ht_manifest.snapshot(fresh) == committed
    assert len(committed) == COMMITTED_FILES


def test_the_committed_library_still_matches_its_committed_manifest():
    """The manifest is the record, so it has to be checked against the files it describes.

    Separate from the rebuild test on purpose. That one proves the BUILDER is
    deterministic and would still pass if the builder, the library and the manifest had
    all drifted together. This one proves the bytes on disk are the bytes the manifest
    claims - which is what catches a checkout that mangled line endings in the `.xmp`,
    `.aae`, `.json`, `.txt` or `.md` members (`.gitattributes` marks the whole folder
    `-text` to prevent exactly that, and this is the assertion that would notice if the
    rule were dropped).
    """
    import json

    recorded = json.loads(build_tiny_library.MANIFEST_PATH.read_text(encoding="utf-8"))
    assert ht_manifest.snapshot(build_tiny_library.LIBRARY_DIR) == recorded


def test_the_library_covers_every_extension_pam_supports():
    """Spec 9.1: "every supported ext". Driven from the config defaults, not a list here.

    So that adding an extension to `pam/config.py` fails HERE, with a name, instead of
    silently leaving the new format untested. The builder holds its own copy of the two
    tuples for the same reason - see the comment on `build_tiny_library.IMAGE_EXTS`.
    """
    present = {name.rsplit(".", 1)[1].lower() for name in EXPECTED_NAMES}
    supported = set(config.DEFAULT_IMAGE_EXTS) | set(config.DEFAULT_VIDEO_EXTS)
    supported |= set(config.DEFAULT_SIDECAR_EXTS)
    assert supported - present == set()


def test_the_library_still_covers_every_spec_9_1_category(library):
    """Spec 9.1's list is normative and exhaustive, so it is ASSERTED, not constructed.

    THIS TEST EXISTS BECAUSE OF A SURVIVING MUTANT (M6 Task 7, M439). Dropping `gps=`
    from both GPS photos and re-running the builder - the sanctioned way to change this
    library - left all 1177 tests green. Nothing moved: the counts are unchanged because
    the files still exist, the bytes and the manifest regenerate together by
    construction, and no proposed name depends on a coordinate. A whole category of the
    spec's list had vanished and the suite could not tell.

    A COUNT IS NOT COVERAGE. What follows asserts the FACTS the library exists to supply,
    read back out of the catalog after a real scan rather than read off the filenames -
    so a file that is still present but no longer carries what it was built to carry
    fails here, by name. The extension list is asserted next door; this is everything
    else spec 9.1 names.
    """
    run_scan(library)
    conn = catalog(library)
    try:
        rows = conn.execute(
            "SELECT f.rel_path, f.media_type, c.capture_dt, c.capture_dt_source, c.gps_lat"
            " FROM files f LEFT JOIN capture c ON c.file_id = f.id"
        ).fetchall()
        kinds = {row[0] for row in conn.execute("SELECT kind FROM companion_groups")}
    finally:
        conn.close()
    paths = {row[0] for row in rows}
    sources = {row[3] for row in rows}
    media = {row[0]: row[1] for row in rows}

    # Every date source spec 5.5/6.1 can label a file with is exercised by some file.
    assert {"exif", "filename", "file_mtime", "qt_local", "qt_utc"} - sources == set()
    # GPS, both hemispheres, and files carrying none. `exif_gps_south.jpg` is the only
    # negative latitude in the library, so M439 cannot pass this line.
    lats = [row[4] for row in rows if row[1] in ("image", "video")]
    assert [lat for lat in lats if lat is not None and lat > 0], "no northern GPS file"
    assert [lat for lat in lats if lat is not None and lat < 0], "no southern GPS file"
    assert [lat for lat in lats if lat is None], "no file without GPS"
    # Wrong-clock EXIF, in both directions.
    dts = [row[2] for row in rows if row[2] is not None]
    assert [dt for dt in dts if dt < "1990"], "no implausibly early clock"
    assert [dt for dt in dts if dt > "2030"], "no implausibly late clock"
    # Companion pairing, both kinds; sidecars; the reserved stems; the long path.
    assert {"raw_jpeg", "live_photo"} - kinds == set()
    assert [path for path, kind in media.items() if kind == "sidecar"], "no sidecar"
    assert set(build_tiny_library.RESERVED_DATES) - paths == set()
    assert [path for path in paths if len(path) >= 120], "no long path"
    assert len([path for path in paths if not path.isascii()]) >= 5
    assert "corrupt_broken.jpg" in paths


def test_scan_reads_every_file_it_should_and_records_no_error(library):
    """The pinned scan counts - the numbers later milestones measure against.

    `read` is the headline: 95 files of the 101, being every image and every video. The four
    `other` files (README.md, notes.txt, left_behind_pair.thm, takeout_photo.json) and
    the two sidecars are never opened, which is `walker.READ_MEDIA_TYPES` working.

    THE CORRUPT JPEG PRODUCES NO ERROR, AND THAT IS A FINDING, NOT AN OVERSIGHT. The M6
    plan's Task 7 asks this test to prove `pam scan` "errors on exactly the corrupt
    JPEG". It cannot, because pam has no way to see one: `pam/scan/runner.py` records an
    error only when exiftool returns NO RECORD for a path, and exiftool returns a record
    for every file it is handed - `corrupt_broken.jpg` comes back as
    `FileType: JPEG, Warning: JPEG format error`, and an empty file comes back as
    `Error: File is empty`. Neither is visible to pam, which never looks at the `Warning`
    or `Error` keys. So the corrupt file is hashed, dated from its mtime and planned like
    any other. That is defensible - pam does not decode images and does not claim to -
    but it is NOT what the plan says, so the number is pinned as it really is and the
    disagreement is recorded here rather than engineered away.

    THAT LATER MILESTONE IS M7, THE DECISION WAS MADE ON PURPOSE, AND THE NUMBER IS
    STILL 0. M7 Task 5 taught `pam/scan/runner.py` to read the `Error` key - and this
    assertion did not go red, which the plan expected it to. It could not: measured with
    real exiftool 13.59, `corrupt_broken.jpg` comes back as `FileType: JPEG, Warning:
    JPEG format error`, and the ruling is that a `Warning` changes NO behaviour. exiftool
    errors when it cannot identify a file's FORMAT (an empty file, all zero bytes, random
    bytes) and warns when it identified the format and could not parse the CONTENTS -
    which is what a half-written JPEG is. The library holds no file of the first kind, so
    0 is the truth both before and after.

    The `Warning` half is not a detail waiting to be tidied up. About half of the capture
    rows in a real-world library carry one, every one a video that got its date, so
    a scanner that treated warnings as errors would exit 1 on half a healthy archive and
    call those files unreadable. Widening the reader to close the gap this docstring
    describes is therefore a design change for the owner, not a fix - it is escalated,
    and until it is ruled on, `pam scan` reports a file it could not identify at all and
    does NOT report a recognisably-JPEG-but-damaged photo. If THAT changes, this
    assertion finally goes red and names this file.
    """
    summary = run_scan(library)
    assert (summary.total, summary.read) == (TOTAL_FILES, READ)
    assert (summary.images, summary.videos, summary.sidecars, summary.other) == (
        IMAGES,
        VIDEOS,
        SIDECARS,
        OTHER,
    )
    assert summary.errors == []
    assert len(summary.errors) == SCAN_ERRORS


def test_plan_proposes_exactly_these_names(library):
    """Spec 9.2 priority 1: a diff in this dict is a naming regression.

    The rows that earn their keep are the awkward ones - `con.jpg` and its six device-name
    siblings, five non-ASCII filenames, a 144-character stem, thirty files differing only
    in the case of their extension, and the fourteen-deep `file_mtime` seq run. Asserting
    the WHOLE dict rather than sampling it is deliberate: a regression that renames one
    unrelated file is exactly as interesting as one that renames a hard case, and a
    sampled assertion cannot see it.

    The tier breakdown is pinned alongside, because a name can stay right while its trust
    tier moves - and M7's bulk approval is gated on the tier, so a silent slide from
    `medium` to `high` would change what a user can approve without looking.
    """
    run_scan(library)
    summary = run_plan(library)
    assert summary.plan_id is not None
    conn = catalog(library)
    try:
        proposed = {
            item.old_rel_path: item.generated_filename
            for item in repo.plan_items(conn, summary.plan_id)
        }
    finally:
        conn.close()
    assert proposed == EXPECTED_NAMES
    assert summary.planned == PLANNED
    assert summary.already_conforming == 0
    assert summary.errors == []
    assert summary.left_behind == LEFT_BEHIND
    assert summary.tiers == TIERS
