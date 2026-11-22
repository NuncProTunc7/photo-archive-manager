import shutil

import pytest
from video_samples import write_samples

from pam.scan.exiftool import ExifTool
from pam.scan.objective import build_capture

pytestmark = pytest.mark.skipif(
    shutil.which("exiftool") is None, reason="needs the real exiftool on PATH"
)


def facts(path):
    with ExifTool() as et:
        tags = et.extract([path], media_type="video")
    key = str(path).replace("\\", "/")
    return build_capture(tags.get(key), path.name, path.stat().st_mtime_ns, "video")


def test_real_exiftool_reads_every_device_shape(tmp_path):
    # The spec 5.5 verification item, as a test rather than a human ritual. The
    # gopro row is C13: under -fast2 it silently produced today's mtime instead
    # of the 2019 date that is actually in the file.
    paths = write_samples(tmp_path)
    got = {
        name: (cap.capture_dt, cap.capture_dt_source, cap.tz_offset_min)
        for name, cap in ((n, facts(p)) for n, p in paths.items())
    }
    # The iphone sample deliberately carries a real -0700 offset, so it stays
    # qt_local under C14. Real iPhones in a real-world library write +00:00 and
    # therefore land on qt_utc - that case is covered in tests/test_objective.py.
    assert got == {
        "iphone": ("2019-07-04T18:23:11", "qt_local", -420),
        "android": ("2019-07-05T01:23:11", "qt_utc", None),
        "gopro": ("2019-07-05T01:23:11", "qt_utc", None),
    }


def test_fast2_would_still_lose_the_moov_last_date(tmp_path):
    # Guards the C13 *decision*: if anyone "simplifies" FROZEN_ARGS_VIDEO back to
    # -fast2, this test is the explanation of why not. Passing media_type='image'
    # is how we send the -fast2 list at a video on purpose.
    gopro = write_samples(tmp_path)["gopro"]
    with ExifTool() as et:
        under_fast2 = et.extract([gopro], media_type="image")
    record = under_fast2[str(gopro).replace("\\", "/")]
    assert "CreateDate" not in record  # check the KEY, never grep the JSON text
    assert record["FileType"] == "MP4"  # exiftool succeeded; it just read nothing


def test_video_gps_arrives_as_the_signed_composite(tmp_path):
    # spec 5.5: videos emit signed composite GPSLatitude/GPSLongitude with no Ref
    # tags, and the existing image-era parser handles that form.
    cap = facts(write_samples(tmp_path)["android"])
    assert (cap.gps_lat, cap.gps_lon) == (37.7749, -122.4194)
