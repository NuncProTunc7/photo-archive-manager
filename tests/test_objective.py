import json
from datetime import datetime

import pytest

from pam.scan.objective import (
    UNSTATED,
    build_capture,
    parse_exif_dt,
    parse_filename_date,
    parse_tz_offset_min,
    read_error,
    read_warning,
)

MTIME_NS = 1_562_262_191_000_000_000  # some real instant in 2019


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"DateTimeOriginal": "2019:07:04 18:23:11"}, "2019-07-04T18:23:11"),
        ({"CreateDate": "2019:07:04 18:23:11"}, "2019-07-04T18:23:11"),
        (
            {"DateTimeOriginal": "2019:07:04 18:23:11.123+02:00"},
            "2019-07-04T18:23:11",
        ),
        ({"DateTimeOriginal": "0000:00:00 00:00:00"}, None),
        ({"DateTimeOriginal": "2019:13:04 18:23:11"}, None),
        ({"DateTimeOriginal": "not a date"}, None),
        ({"DateTimeOriginal": 20190704}, None),
        ({}, None),
    ],
)
def test_parse_exif_dt(tags, expected):
    assert parse_exif_dt(tags) == expected


def test_datetimeoriginal_outranks_createdate():
    tags = {
        "CreateDate": "2020:01:01 00:00:00",
        "DateTimeOriginal": "2019:07:04 18:23:11",
    }
    assert parse_exif_dt(tags) == "2019-07-04T18:23:11"


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"OffsetTimeOriginal": "+02:00"}, 120),
        ({"OffsetTime": "-0530"}, -330),
        ({"OffsetTimeOriginal": "garbage"}, None),
        ({}, None),
    ],
)
def test_parse_tz_offset_min(tags, expected):
    assert parse_tz_offset_min(tags) == expected


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("2019-07-04_beach.jpg", "2019-07-04T00:00:00"),
        ("scan_1988.06.12.tif", "1988-06-12T00:00:00"),
        ("IMG_20190704_182311.jpg", "2019-07-04T00:00:00"),
        ("IMG-20190704-WA0001.jpg", "2019-07-04T00:00:00"),
        ("IMG_4321.jpg", None),
        ("20191399.jpg", None),
        ("photo201907041823.jpg", None),
        ("2019-02-30_oops.jpg", None),
        ("nothing-here.jpg", None),
    ],
)
def test_parse_filename_date(filename, expected):
    assert parse_filename_date(filename) == expected


def test_build_capture_exif_wins():
    tags = {
        "DateTimeOriginal": "2019:07:04 18:23:11",
        "OffsetTimeOriginal": "+02:00",
        "ImageWidth": 4000,
        "ImageHeight": 3000,
        "Make": "Canon",
        "Model": "EOS R6",
    }
    cap = build_capture(tags, "2020-01-01_wrong.jpg", MTIME_NS)
    assert cap.capture_dt == "2019-07-04T18:23:11"
    assert cap.capture_dt_source == "exif"
    assert cap.tz_offset_min == 120
    assert cap.width == 4000 and cap.height == 3000
    assert cap.camera_make == "Canon" and cap.camera_model == "EOS R6"


def test_build_capture_filename_fallback():
    cap = build_capture({}, "1988-06-12_grandma.jpg", MTIME_NS)
    assert cap.capture_dt == "1988-06-12T00:00:00"
    assert cap.capture_dt_source == "filename"
    assert cap.tz_offset_min is None


def test_build_capture_mtime_fallback():
    cap = build_capture({}, "IMG_4321.jpg", MTIME_NS)
    expected = datetime.fromtimestamp(MTIME_NS / 1_000_000_000)
    assert cap.capture_dt == expected.strftime("%Y-%m-%dT%H:%M:%S")
    assert cap.capture_dt_source == "file_mtime"


def test_build_capture_unreadable_file_degrades_gracefully():
    cap = build_capture(None, "IMG_4321.jpg", MTIME_NS)
    assert cap.capture_dt_source == "file_mtime"
    assert cap.exif_json == "{}"


def test_gps_signed_values_pass_through():
    cap = build_capture({"GPSLatitude": 39.09, "GPSLongitude": -120.03}, "x.jpg", MTIME_NS)
    assert cap.gps_lat == pytest.approx(39.09)
    assert cap.gps_lon == pytest.approx(-120.03)


def test_gps_refs_apply_hemisphere_signs():
    cap = build_capture(
        {
            "GPSLatitude": 33.86,
            "GPSLatitudeRef": "S",
            "GPSLongitude": 151.21,
            "GPSLongitudeRef": "E",
        },
        "x.jpg",
        MTIME_NS,
    )
    assert cap.gps_lat == pytest.approx(-33.86)
    assert cap.gps_lon == pytest.approx(151.21)


def test_gps_requires_both_coordinates_and_sane_ranges():
    assert build_capture({"GPSLatitude": 39.0}, "x.jpg", MTIME_NS).gps_lat is None
    cap = build_capture({"GPSLatitude": 999.0, "GPSLongitude": 10.0}, "x.jpg", MTIME_NS)
    assert cap.gps_lat is None and cap.gps_lon is None


def test_exif_json_is_the_verbatim_record():
    tags = {"Make": "Canon", "Weird": ["nested", 1]}
    cap = build_capture(tags, "x.jpg", MTIME_NS)
    assert json.loads(cap.exif_json) == tags


def test_non_string_and_empty_camera_fields_are_none():
    cap = build_capture({"Make": "   ", "Model": 7}, "x.jpg", MTIME_NS)
    assert cap.camera_make is None and cap.camera_model is None


@pytest.mark.parametrize(
    ("tags", "expected_dt", "expected_source", "expected_tz"),
    [
        (
            {"CreationDate": "2019:07:04 18:23:11-07:00"},
            "2019-07-04T18:23:11",
            "qt_local",
            -420,
        ),
        (
            {"CreationDate": "2019:07:04 18:23:11+05:30"},
            "2019-07-04T18:23:11",
            "qt_local",
            330,
        ),
        ({"CreateDate": "2019:07:05 01:23:11"}, "2019-07-05T01:23:11", "qt_utc", None),
        (
            {"MediaCreateDate": "2019:07:05 01:23:11"},
            "2019-07-05T01:23:11",
            "qt_utc",
            None,
        ),
        (
            {"TrackCreateDate": "2019:07:05 01:23:11"},
            "2019-07-05T01:23:11",
            "qt_utc",
            None,
        ),
        (
            {
                "CreationDate": "2019:07:04 18:23:11-07:00",
                "CreateDate": "2019:07:05 01:23:11",
            },
            "2019-07-04T18:23:11",
            "qt_local",
            -420,
        ),
        (
            {
                "DateTimeOriginal": "2019:07:04 18:23:11",
                "CreateDate": "2019:07:05 01:23:11",
            },
            "2019-07-04T18:23:11",
            "exif",
            None,
        ),
        (
            {
                "CreationDate": "2019:07:04 18:23:11Z",
                "CreateDate": "2019:07:05 01:23:11",
            },
            "2019-07-05T01:23:11",
            "qt_utc",
            None,
        ),
        (
            {"CreationDate": "2019:07:04 18:23:11.500-07:00"},
            "2019-07-04T18:23:11",
            "qt_local",
            -420,
        ),
        (
            {"CreationDate": "2019:07:04 18:23:11-0700"},
            "2019-07-04T18:23:11",
            "qt_local",
            -420,
        ),
    ],
)
def test_video_date_hierarchy(tags, expected_dt, expected_source, expected_tz):
    cap = build_capture(tags, "clip.mp4", MTIME_NS, "video")
    assert cap.capture_dt == expected_dt
    assert cap.capture_dt_source == expected_source
    assert cap.tz_offset_min == expected_tz


@pytest.mark.parametrize(
    "tags",
    [
        {"CreateDate": "1904:01:01 00:00:00"},
        {"CreateDate": "1904:01:01 04:30:00"},
        {"CreateDate": "1970:01:01 00:00:00"},
        {"CreateDate": "0000:00:00 00:00:00"},
        {"CreationDate": "1904:01:01 00:00:00+00:00"},
    ],
)
def test_video_epoch_sentinels_treated_as_absent(tags):
    cap = build_capture(tags, "clip.mp4", MTIME_NS, "video")
    assert cap.capture_dt_source == "file_mtime"


def test_video_sentinel_falls_through_to_next_utc_tag():
    tags = {
        "CreateDate": "1904:01:01 00:00:00",
        "MediaCreateDate": "2019:07:05 01:23:11",
    }
    cap = build_capture(tags, "clip.mp4", MTIME_NS, "video")
    assert cap.capture_dt == "2019-07-05T01:23:11"
    assert cap.capture_dt_source == "qt_utc"


def test_video_exif_sentinel_falls_through_to_utc_trio():
    # A 1904 DateTimeOriginal must not be emitted as a trustworthy 'exif' date.
    tags = {
        "DateTimeOriginal": "1904:01:01 00:00:00",
        "TrackCreateDate": "2019:07:05 01:23:11",
    }
    cap = build_capture(tags, "clip.mp4", MTIME_NS, "video")
    assert cap.capture_dt == "2019-07-05T01:23:11"
    assert cap.capture_dt_source == "qt_utc"


def test_video_1970_with_real_time_of_day_is_a_valid_date():
    cap = build_capture({"CreateDate": "1970:01:01 08:15:00"}, "clip.mp4", MTIME_NS, "video")
    assert cap.capture_dt == "1970-01-01T08:15:00"
    assert cap.capture_dt_source == "qt_utc"


def test_video_filename_date_beats_mtime():
    cap = build_capture({}, "VID_20190704_182311.mp4", MTIME_NS, "video")
    assert cap.capture_dt == "2019-07-04T00:00:00"
    assert cap.capture_dt_source == "filename"


def test_image_createdate_still_maps_to_exif_source():
    # Regression: the image path is frozen - CreateDate on an image is EXIF
    # local time, never qt_utc (media_type defaults to "image").
    cap = build_capture({"CreateDate": "2019:07:04 18:23:11"}, "x.jpg", MTIME_NS)
    assert cap.capture_dt == "2019-07-04T18:23:11"
    assert cap.capture_dt_source == "exif"


@pytest.mark.parametrize("offset", ["+00:00", "-00:00"])
def test_zero_offset_creationdate_is_utc_not_local(offset):
    # spec 5.5 rev 5: real iPhones write CreationDate with +00:00 and the same
    # wall clock as the UTC trio. Calling that 'qt_local' would present UTC as
    # local time at exif-level trust - vision principle 5's exact failure.
    tags = {
        "CreationDate": f"2023:08:28 10:45:51{offset}",
        "CreateDate": "2023:08:28 10:45:51",
        "MediaCreateDate": "2023:08:28 10:45:51",
    }
    cap = build_capture(tags, "IMG_5808.MOV", MTIME_NS, "video")
    assert cap.capture_dt == "2023-08-28T10:45:51"
    assert cap.capture_dt_source == "qt_utc"
    assert cap.tz_offset_min is None


def test_zero_offset_creationdate_alone_keeps_its_timestamp():
    # No UTC trio to fall back on: keep the date, relabel it. Never discard a
    # real timestamp to punish its offset.
    cap = build_capture(
        {"CreationDate": "2023:08:28 10:45:51+00:00"}, "clip.mov", MTIME_NS, "video"
    )
    assert cap.capture_dt == "2023-08-28T10:45:51"
    assert cap.capture_dt_source == "qt_utc"
    assert cap.tz_offset_min is None


def test_a_half_hour_offset_is_still_a_real_locale():
    # Only *zero* is untrustworthy. +00:30 is nobody's default.
    cap = build_capture(
        {"CreationDate": "2019:07:04 18:23:11+00:30"}, "clip.mov", MTIME_NS, "video"
    )
    assert cap.capture_dt_source == "qt_local" and cap.tz_offset_min == 30


# --- exiftool's own report on a record it returned (M7 Task 5; spec 13 rev 6) ----------
#
# THE TWO KEYS MEAN DIFFERENT THINGS AND ARE TREATED DIFFERENTLY, and the pair of
# functions is defined side by side for the reason `UnitReasons` is (m5.md): the
# asymmetry is the design, so it has to be readable in one place.
#
# Measured with real exiftool 13.59 and the scanner's own frozen image args:
#
#   file shape                 FileType   Error                            Warning
#   empty file                 -          File is empty                    -
#   random bytes               -          File format error                -
#   all zero bytes             -          File header is all binary zeros  -
#   truncated JPEG header      JPEG       -                                JPEG format error
#   first half of a real JPEG  JPEG       -                                JPEG format error
#   text file named .jpg       TXT        -                                -
#
# So `Error` means exiftool could not identify the file's FORMAT at all, and `Warning`
# means it identified the format and could not parse the CONTENTS. About half of the
# capture rows of a real-world library carry a `Warning` - every one of them a video
# that got its date - so widening either reader to match the other key would make an
# ordinary scan exit 1 on half an archive and call those files unreadable.


@pytest.mark.parametrize(
    ("record", "error", "warning"),
    [
        # The four real `Error` shapes. None of them carries a FileType, which is the
        # measured signature of "exiftool could not tell what this file is".
        ({"SourceFile": "a.jpg", "Error": "File is empty"}, "File is empty", None),
        ({"Error": "File format error"}, "File format error", None),
        (
            {"Error": "File header is all binary zeros"},
            "File header is all binary zeros",
            None,
        ),
        # The real `Warning` shape: the format WAS identified.
        (
            {"FileType": "JPEG", "Warning": "JPEG format error"},
            None,
            "JPEG format error",
        ),
        # The one real warning in a real-world library, on files that all got dates.
        (
            {"Warning": "[minor] The ExtractEmbedded option may find more tags"},
            None,
            "[minor] The ExtractEmbedded option may find more tags",
        ),
        # Both at once: neither reader shadows the other.
        (
            {"Error": "File is empty", "Warning": "JPEG format error"},
            "File is empty",
            "JPEG format error",
        ),
        # An ordinary record, and the empty one.
        ({"DateTimeOriginal": "2019:07:04 18:23:11"}, None, None),
        ({}, None, None),
    ],
)
def test_read_error_and_read_warning_read_their_own_key(record, error, warning):
    assert read_error(record) == error
    assert read_warning(record) == warning


@pytest.mark.parametrize(
    "record",
    [
        # THE CENTRAL GOTCHA, as a test. `FileCreateDate`, `FileModifyDate` and
        # `FileAccessDate` are present on EVERY record exiftool returns, so a reader
        # that matched a key by substring would report an error for every file in
        # every archive. These are the keys a substring test would trip on.
        {"FileModifyDate": "2019:07:04 18:23:11", "FileAccessDate": "2019:07:04 18:23:11"},
        {"ErrorLog": "no such thing"},
        {"WarningCount": 3},
        {"ExifToolVersion": 13.59},
        # JSON keys are case-sensitive and exiftool's are capitalised. A lower-case
        # `error` is a TAG some camera wrote, not exiftool speaking.
        {"error": "not exiftool's", "warning": "nor this"},
    ],
)
def test_neither_reader_matches_a_key_that_merely_contains_the_word(record):
    assert read_error(record) is None
    assert read_warning(record) is None


@pytest.mark.parametrize("value", [None, 12, "", "   "])
def test_a_key_present_with_no_usable_text_still_reports_the_failure(value):
    """The KEY's presence is the fact; its text is only the detail.

    Returning None for a malformed value would drop the file out of `summary.errors`
    entirely and let `pam scan` exit 0 on a file exiftool complained about - trading a
    poor message for a silent one, which is vision principle 9's failure.
    """
    assert read_error({"Error": value}) == UNSTATED
    assert read_warning({"Warning": value}) == UNSTATED
