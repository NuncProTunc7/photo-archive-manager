"""Device-shaped MP4/MOV builders for the real-exiftool tests (design spec 5.5).

Three shapes matter:
  iphone   moov first, Apple Keys creationdate with a UTC offset  -> qt_local
  android  moov first, mvhd CreateDate only, plus udta GPS        -> qt_utc
  gopro    moov AFTER mdat, same mvhd CreateDate                  -> the -fast2 defect

Real exiftool parses these the same way it parses camera output, so the C13 fix
can be tested without keeping binary sample files in the repository.
"""

import struct
from pathlib import Path

EPOCH_1904 = 2082844800  # seconds from 1904-01-01 to 1970-01-01
CREATE_UNIX = 1562289791  # 2019-07-05 01:23:11 UTC == 2019-07-04 18:23:11 -0700
_MDAT_BYTES = 64 * 1024  # big enough to be a real box, small enough to be free


def box(kind: bytes, *payload: bytes) -> bytes:
    body = b"".join(payload)
    return struct.pack(">I", 8 + len(body)) + kind + body


def full_box(kind: bytes, version: int, flags: int, *payload: bytes) -> bytes:
    return box(kind, struct.pack(">B", version) + flags.to_bytes(3, "big"), *payload)


def _mvhd(unix_time: int = CREATE_UNIX) -> bytes:
    t = unix_time + EPOCH_1904
    return full_box(
        b"mvhd",
        0,
        0,
        struct.pack(">II", t, t),
        struct.pack(">II", 1000, 5000),
        struct.pack(">i", 0x00010000),
        struct.pack(">h", 0x0100),
        b"\x00" * 10,
        struct.pack(">9i", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000),
        b"\x00" * 24,
        struct.pack(">I", 2),
    )


def _trak(unix_time: int = CREATE_UNIX) -> bytes:
    """Minimal video track, so the file looks like something a muxer wrote."""
    t = unix_time + EPOCH_1904
    tkhd = full_box(
        b"tkhd",
        0,
        7,
        struct.pack(">II", t, t),
        struct.pack(">II", 1, 0),
        struct.pack(">I", 5000),
        b"\x00" * 8,
        struct.pack(">hhhh", 0, 0, 0, 0),
        struct.pack(">9i", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000),
        struct.pack(">II", 1920 << 16, 1080 << 16),
    )
    mdhd = full_box(
        b"mdhd",
        0,
        0,
        struct.pack(">II", t, t),
        struct.pack(">II", 30000, 150000),
        struct.pack(">HH", 0x55C4, 0),
    )
    hdlr = full_box(
        b"hdlr", 0, 0, struct.pack(">I", 0), b"vide", b"\x00" * 12, b"VideoHandler\x00"
    )
    return box(b"trak", tkhd, box(b"mdia", mdhd, hdlr))


def _apple_keys_creationdate(value: str) -> bytes:
    """moov/meta holding com.apple.quicktime.creationdate, as iPhones write it."""
    key = b"com.apple.quicktime.creationdate"
    keys = full_box(
        b"keys",
        0,
        0,
        struct.pack(">I", 1),
        struct.pack(">I", 8 + len(key)) + b"mdta" + key,
    )
    hdlr = full_box(b"hdlr", 0, 0, struct.pack(">I", 0), b"mdta", b"\x00" * 12, b"\x00")
    data = box(
        b"data",
        struct.pack(">I", 1),  # type indicator: 1 = UTF-8
        struct.pack(">I", 0),  # locale
        value.encode("utf-8"),
    )
    item = box(struct.pack(">I", 1), data)  # the item box name IS the 1-based key index
    return box(b"meta", hdlr, keys, box(b"ilst", item))


def _udta_gps(coords: str) -> bytes:
    """Android and most cameras write location as a udta (c)xyz box, ISO 6709."""
    payload = coords.encode("ascii")
    return box(b"udta", box(b"\xa9xyz", struct.pack(">HH", len(payload), 0x15C7), payload))


def _write(
    path: Path,
    *,
    moov_last: bool,
    extras: tuple[bytes, ...] = (),
    unix_time: int = CREATE_UNIX,
) -> Path:
    ftyp = box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2mp41")
    moov = box(b"moov", _mvhd(unix_time), _trak(unix_time), *extras)
    mdat = box(b"mdat", b"\x00" * _MDAT_BYTES)
    path.write_bytes(ftyp + mdat + moov if moov_last else ftyp + moov + mdat)
    return path


# The public names. `write_samples` covers the three shapes spec 5.5 names, and that is
# all the exiftool tests need; `tools/build_tiny_library.py` needs the same writer with
# other clocks and other atoms (a zero-offset CreationDate, the 1904 sentinel), so the
# knobs are exported rather than copied. The defaults above keep `write_samples` emitting
# byte-identical output, which tests/archives.py::device_mov_bytes and the whole fault
# matrix depend on.
write_movie = _write
apple_keys_creationdate = _apple_keys_creationdate
udta_gps = _udta_gps


def write_samples(directory: Path) -> dict[str, Path]:
    """Write the three shapes into directory; keys are 'iphone'/'android'/'gopro'."""
    return {
        "iphone": _write(
            directory / "iphone_like.mov",
            moov_last=False,
            extras=(_apple_keys_creationdate("2019-07-04T18:23:11-0700"),),
        ),
        "android": _write(
            directory / "android_like.mp4",
            moov_last=False,
            extras=(_udta_gps("+37.7749-122.4194/"),),
        ),
        "gopro": _write(directory / "gopro_like.mp4", moov_last=True),
    }
