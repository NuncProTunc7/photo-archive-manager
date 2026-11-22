"""Turn exiftool output + filesystem facts into capture rows (objective facts)."""

import json
import re
from datetime import datetime

from pam.catalog.repo import CaptureFacts

# EXIF date-time tags in trust order; both map to the 'exif' source (spec 6.1).
_EXIF_DT_TAGS = ("DateTimeOriginal", "CreateDate")
_EXIF_DT = re.compile(r"^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")
_TZ_TAGS = ("OffsetTimeOriginal", "OffsetTime")
_TZ = re.compile(r"^([+-])(\d{2}):?(\d{2})$")

# QuickTime video date tags (spec 5.5). CreationDate (Apple Keys) carries a
# UTC offset -> true local time. The CreateDate trio is UTC per the QuickTime
# spec and carries no offset, so the local capture date is unknowable from it
# alone - source 'qt_utc' is tier-capped downstream, never silently localized.
_QT_LOCAL_TAG = "CreationDate"
_QT_UTC_TAGS = ("CreateDate", "MediaCreateDate", "TrackCreateDate")
_QT_LOCAL = re.compile(
    r"^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?([+-])(\d{2}):?(\d{2})$"
)

# Filename date patterns: separated (2019-07-04 / 2019_07_04 / 2019.07.04),
# then compact (20190704) guarded against sitting inside a longer digit run.
_FILENAME_DATES = (
    re.compile(r"((?:19|20)\d{2})[-_.](0[1-9]|1[0-2])[-_.](0[1-9]|[12]\d|3[01])"),
    re.compile(r"(?<!\d)((?:19|20)\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)"),
)


# What `read_error`/`read_warning` say when exiftool named a problem but gave no text
# for it. The KEY's presence is the fact; its text is only the detail - so a record
# carrying `"Error": null` still has to reach `summary.errors`, or the file exiftool
# complained about would exit 0 in silence (vision principle 9).
UNSTATED = "exiftool did not say what went wrong"


def _note(tags: dict[str, object], key: str) -> str | None:
    """exiftool's own text under `key`, or None if it did not use that key at all.

    `key not in tags` - an EXACT key lookup, never a substring test on the record or
    its JSON. `FileCreateDate`, `FileModifyDate` and `FileAccessDate` are on every
    record exiftool returns, so a reader that matched a key by substring reports a
    problem for every file in every archive. That mistake has been made twice on this
    project already (CLAUDE.md), which is why it is spelled out here and pinned by
    `tests/test_objective.py`.
    """
    if key not in tags:
        return None
    value = tags[key]
    if not isinstance(value, str) or not value.strip():
        return UNSTATED
    return value.strip()


def read_error(tags: dict[str, object]) -> str | None:
    """exiftool's `Error` for this record: a PER-FILE ERROR (spec 13 rev 6).

    Measured with real exiftool 13.59: `Error` appears when exiftool could not identify
    the file's FORMAT at all - an empty file, a file of all zero bytes, random bytes,
    a JPEG magic number followed by garbage. Such a record carries no `FileType` and no
    tags, so the file falls to `file_mtime` at the lowest trust and, until M7, nothing
    said so. The scanner now records it, counts it, and exits 1.

    DEFINED BESIDE `read_warning` ON PURPOSE, for the reason `execute/unit.py` defines
    `APPLY_REASONS` beside `UNDO_REASONS`: the asymmetry between the two keys IS the
    design, and a reader who finds only one of them cannot see it.
    """
    return _note(tags, "Error")


def read_warning(tags: dict[str, object]) -> str | None:
    """exiftool's `Warning` for this record: a PARTIAL READ, and NOT an error.

    exiftool warns when it DID identify the format and could not parse some of the
    contents - a truncated JPEG comes back as `FileType: JPEG, Warning: JPEG format
    error`. It is common and usually benign: about half of the capture rows in a real-world
    library carry one, all of them videos, and every one of those videos got its
    date. Treating a warning as an error would make an ordinary scan exit 1 on half an
    archive and call those files unreadable - a product-visible regression dressed as a
    fix. So this changes NO behaviour; it goes to the session log at debug level and
    nowhere else.

    A COROLLARY WORTH KNOWING BEFORE ANYONE "FIXES" IT: a recognisably-JPEG-but-damaged
    photo warns rather than errors, so `pam scan` still does not report it as skipped.
    That is a deliberate open gap, escalated to the owner, not an oversight here.
    """
    return _note(tags, "Warning")


def _valid_date(year: int, month: int, day: int) -> bool:
    try:
        datetime(year, month, day)
    except ValueError:
        return False
    return True


def _parse_dt(tags: dict[str, object], tag_names: tuple[str, ...]) -> str | None:
    for tag in tag_names:
        value = tags.get(tag)
        if not isinstance(value, str):
            continue
        m = _EXIF_DT.match(value.strip())
        if not m:
            continue
        y, mo, d, h, mi, s = (int(g) for g in m.groups())
        if not _valid_date(y, mo, d) or h > 23 or mi > 59 or s > 59:
            continue
        return f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}"
    return None


def parse_exif_dt(tags: dict[str, object]) -> str | None:
    """'2019:07:04 18:23:11[.frac][offset]' -> ISO local time, else None."""
    return _parse_dt(tags, _EXIF_DT_TAGS)


def parse_tz_offset_min(tags: dict[str, object]) -> int | None:
    for tag in _TZ_TAGS:
        value = tags.get(tag)
        if not isinstance(value, str):
            continue
        m = _TZ.match(value.strip())
        if not m:
            continue
        sign = 1 if m.group(1) == "+" else -1
        return sign * (int(m.group(2)) * 60 + int(m.group(3)))
    return None


def _is_epoch_sentinel(iso: str) -> bool:
    """QuickTime/Unix zero-clock values: present in tags, absent in reality."""
    return iso.startswith("1904-01-01") or iso == "1970-01-01T00:00:00"


def parse_qt_local(tags: dict[str, object]) -> tuple[str, int] | None:
    """Apple CreationDate ('2019:07:04 18:23:11-07:00') -> (ISO local, tz minutes)."""
    value = tags.get(_QT_LOCAL_TAG)
    if not isinstance(value, str):
        return None
    m = _QT_LOCAL.match(value.strip())
    if not m:
        return None
    y, mo, d, h, mi, s = (int(g) for g in m.groups()[:6])
    if not _valid_date(y, mo, d) or h > 23 or mi > 59 or s > 59:
        return None
    iso = f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}"
    if _is_epoch_sentinel(iso):
        return None
    sign = 1 if m.group(7) == "+" else -1
    return iso, sign * (int(m.group(8)) * 60 + int(m.group(9)))


def parse_qt_utc(tags: dict[str, object]) -> str | None:
    """First non-sentinel of the UTC QuickTime trio -> ISO string (UTC), else None."""
    for tag in _QT_UTC_TAGS:
        iso = _parse_dt(tags, (tag,))
        if iso is not None and not _is_epoch_sentinel(iso):
            return iso
    return None


def _video_date(tags: dict[str, object]) -> tuple[str | None, str, int | None]:
    """Video hierarchy (spec 5.5): qt_local > exif DateTimeOriginal > qt_utc."""
    local = parse_qt_local(tags)
    # rev 5: a zero offset is indistinguishable from a writer that had no
    # timezone and emitted UTC - which is what real iPhones do. It must not be
    # presented as local time; it falls through and is relabeled below.
    if local is not None and local[1] != 0:
        return local[0], "qt_local", local[1]
    dto = _parse_dt(tags, ("DateTimeOriginal",))
    if dto is not None and not _is_epoch_sentinel(dto):
        return dto, "exif", parse_tz_offset_min(tags)
    utc = parse_qt_utc(tags)
    if utc is not None:
        return utc, "qt_utc", None
    if local is not None:
        # Zero-offset CreationDate and no UTC trio: it is UTC by its own claim,
        # so keep the timestamp and label it honestly rather than losing the date.
        return local[0], "qt_utc", None
    return None, "none", None


def parse_filename_date(filename: str) -> str | None:
    """First valid calendar date embedded in the filename, date-only ISO form."""
    for pattern in _FILENAME_DATES:
        for m in pattern.finditer(filename):
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if _valid_date(y, mo, d):
                return f"{y:04d}-{mo:02d}-{d:02d}T00:00:00"
    return None


def _number(tags: dict[str, object], key: str) -> float | None:
    value = tags.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _gps(tags: dict[str, object]) -> tuple[float | None, float | None]:
    lat = _number(tags, "GPSLatitude")
    lon = _number(tags, "GPSLongitude")
    if lat is None or lon is None:
        return None, None
    lat_ref = tags.get("GPSLatitudeRef")
    if isinstance(lat_ref, str) and lat_ref.upper().startswith("S") and lat > 0:
        lat = -lat
    lon_ref = tags.get("GPSLongitudeRef")
    if isinstance(lon_ref, str) and lon_ref.upper().startswith("W") and lon > 0:
        lon = -lon
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, None
    return lat, lon


def _text(tags: dict[str, object], key: str) -> str | None:
    value = tags.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _int_tag(tags: dict[str, object], key: str) -> int | None:
    value = tags.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def build_capture(
    tags: dict[str, object] | None,
    filename: str,
    mtime_ns: int,
    media_type: str = "image",
    *,
    evidence_filename: str | None = None,
) -> CaptureFacts:
    """Assemble one capture row.

    tags=None means exiftool could not read the file; the date then degrades
    through filename/mtime sources - always labeled, never guessed (vision
    principle 5). media_type='video' switches to the spec-5.5 hierarchy.

    `evidence_filename` (design 5.1, M13 S9/H8): when pam wrote this file's current name,
    the scanner passes the name the file had BEFORE pam renamed it, and the filename step
    parses that instead - a name pam wrote is not evidence. Nothing else changes.
    """
    safe_tags: dict[str, object] = tags or {}
    tz_offset_min: int | None = None
    if media_type == "video":
        capture_dt, source, tz_offset_min = _video_date(safe_tags)
    else:
        capture_dt = parse_exif_dt(safe_tags)
        source = "exif"
        if capture_dt is not None:
            tz_offset_min = parse_tz_offset_min(safe_tags)
    if capture_dt is None:
        capture_dt = parse_filename_date(
            filename if evidence_filename is None else evidence_filename
        )
        source = "filename"
        tz_offset_min = None
    if capture_dt is None:
        moment = datetime.fromtimestamp(mtime_ns / 1_000_000_000)
        capture_dt = moment.strftime("%Y-%m-%dT%H:%M:%S")
        source = "file_mtime"
    lat, lon = _gps(safe_tags)
    return CaptureFacts(
        capture_dt=capture_dt,
        capture_dt_source=source,
        tz_offset_min=tz_offset_min,
        gps_lat=lat,
        gps_lon=lon,
        width=_int_tag(safe_tags, "ImageWidth"),
        height=_int_tag(safe_tags, "ImageHeight"),
        camera_make=_text(safe_tags, "Make"),
        camera_model=_text(safe_tags, "Model"),
        exif_json=json.dumps(safe_tags, ensure_ascii=False, sort_keys=True),
    )
