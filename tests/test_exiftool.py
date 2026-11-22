import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import build_tiny_library  # noqa: E402

from pam.errors import ExifToolError  # noqa: E402
from pam.scan import exiftool  # noqa: E402
from pam.scan.exiftool import (  # noqa: E402
    BATCH_LIMIT,
    FROZEN_ARGS_IMAGE,
    FROZEN_ARGS_VIDEO,
    ExifTool,
    default_command,
)

FAKE = Path(__file__).parent / "fake_exiftool.py"


def fake_command() -> list[str]:
    return [sys.executable, str(FAKE)]


def key_for(path: Path) -> str:
    return str(path).replace("\\", "/")


def test_frozen_arg_lists_are_exactly_the_read_only_sets():
    # Locking the constants *is* the safety property: no caller can extend them.
    # spec 5.3/5.5 rev 4: -fast2 is unsafe for video (it returns no QuickTime
    # metadata when moov follows mdat) and stays on the image path only.
    assert FROZEN_ARGS_IMAGE == ("-json", "-n", "-fast2", "-charset", "filename=UTF8")
    assert FROZEN_ARGS_VIDEO == ("-json", "-n", "-fast", "-charset", "filename=UTF8")


def test_extract_returns_tags_and_sends_only_frozen_args(tmp_path):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    (tmp_path / "a.jpg.tags.json").write_text(json.dumps({"Make": "Canon"}), encoding="utf-8")
    with ExifTool(command=fake_command()) as et:
        results = et.extract([photo])
    record = results[key_for(photo)]
    assert record["Make"] == "Canon"
    assert record["PamFakeArgs"] == list(FROZEN_ARGS_IMAGE)


def test_unreadable_file_is_absent_not_raised(tmp_path):
    good = tmp_path / "good.jpg"
    bad = tmp_path / "unreadable.jpg"
    good.write_bytes(b"x")
    bad.write_bytes(b"x")
    with ExifTool(command=fake_command()) as et:
        results = et.extract([good, bad])
    assert key_for(good) in results
    assert key_for(bad) not in results


def test_extract_batches_over_the_limit(tmp_path):
    paths = []
    for i in range(BATCH_LIMIT + 5):
        p = tmp_path / f"f{i:03d}.jpg"
        p.write_bytes(b"x")
        paths.append(p)
    with ExifTool(command=fake_command()) as et:
        results = et.extract(paths)
    assert len(results) == BATCH_LIMIT + 5


def test_malformed_output_skips_batch_and_wrapper_stays_usable(tmp_path):
    good = tmp_path / "good.jpg"
    poison = tmp_path / "poison.jpg"
    good.write_bytes(b"x")
    poison.write_bytes(b"x")
    with ExifTool(command=fake_command()) as et:
        results = et.extract([good, poison])
        results_after = et.extract([good])  # protocol must still be in sync
    assert results == {}
    assert key_for(good) in results_after


def test_missing_exiftool_is_a_friendly_error(monkeypatch):
    import pam.scan.exiftool as mod

    monkeypatch.setattr(mod.shutil, "which", lambda name: None)
    with pytest.raises(ExifToolError, match="pam doctor"):
        default_command()


@pytest.mark.skipif(shutil.which("exiftool") is None, reason="real exiftool not on PATH")
def test_real_exiftool_roundtrip(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("hello", encoding="utf-8")
    with ExifTool() as et:
        results = et.extract([f])
    record = results[key_for(f)]
    assert record["FileSize"] == 5  # -n makes FileSize numeric bytes


def test_dict_shaped_json_skips_batch_and_wrapper_stays_usable(tmp_path):
    # M2 review finding A: valid JSON of the wrong shape must behave exactly
    # like malformed JSON - batch unread, no exception, protocol still in sync.
    good = tmp_path / "good.jpg"
    weird = tmp_path / "dictshape.jpg"
    good.write_bytes(b"x")
    weird.write_bytes(b"x")
    with ExifTool(command=fake_command()) as et:
        results = et.extract([good, weird])
        results_after = et.extract([good])
    assert results == {}
    assert key_for(good) in results_after


def test_dead_child_raises_exiftoolerror_not_oserror(tmp_path):
    # M2 review finding B: a dead child must surface as ExifToolError whether
    # the failure is the stdin write (OSError) or EOF on readline.
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    with ExifTool(command=[sys.executable, "-c", "pass"]) as et:
        assert et._proc is not None
        et._proc.wait(timeout=10)
        with pytest.raises(ExifToolError):
            et.extract([photo])


def test_video_batches_send_the_video_args(tmp_path):
    # C13: the media type picks the arg list. The fake echoes the argument lines
    # it saw as PamFakeArgs, so this asserts what actually reached exiftool.
    clip = tmp_path / "a.mp4"
    clip.write_bytes(b"v")
    with ExifTool(command=fake_command()) as et:
        results = et.extract([clip], media_type="video")
    assert results[key_for(clip)]["PamFakeArgs"] == list(FROZEN_ARGS_VIDEO)


def test_image_is_the_default_media_type(tmp_path):
    # Every M2/M3 call site passes no media_type and must keep -fast2.
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    with ExifTool(command=fake_command()) as et:
        results = et.extract([photo])
    assert results[key_for(photo)]["PamFakeArgs"] == list(FROZEN_ARGS_IMAGE)


def test_exit_closes_the_child_pipes(tmp_path):
    # C8 (m3.md item 14): __exit__ killed the child but never closed its pipes,
    # so the unflushed '-stay_open False' retried in the TextIOWrapper finalizer
    # and printed 'Exception ignored ... OSError' to stderr - right after pam's
    # friendly message, undercutting the spec 13 contract at the worst moment.
    with ExifTool(command=[sys.executable, "-c", "pass"]) as et:
        assert et._proc is not None
        proc = et._proc
        proc.wait(timeout=10)
    assert proc.stdin is None or proc.stdin.closed
    assert proc.stdout is None or proc.stdout.closed


# ======================================================================================
# Spec 5.3's THIRD frozen surface: the one-shot BINARY extractor (M10 Task 6)
# ======================================================================================
#
# THE CHAIN'S ORDER IS THE PROPERTY UNDER TEST, and a fixture carrying one representation
# three times cannot test it: every ordering returns the same bytes and the test passes
# for all of them. So `tools/build_tiny_library.raw_with_previews` writes a RAW whose
# `PreviewImage`, `JpgFromRaw` and `ThumbnailImage` are THREE DIFFERENT JPEGs, and the
# assertions below name which one must come back. That matters more here than it did in
# `pam/webui/thumbs.py`: since design 8.1 rev 17 this chain also decides which rendering
# of the photograph every RAW file's perceptual hash is computed over.
#
# THE EXTRACTOR IS THE REAL BINARY. A fake that returned bytes on request would confirm
# only what this module's author already believed about `-b`, which is the same argument
# `tests/test_webui_thumbs.py` makes at length.

needs_exiftool = pytest.mark.skipif(
    shutil.which("exiftool") is None, reason="binary extraction needs the real exiftool"
)

# Spec 8.2 rev 12: "`-b -ThumbnailImage` for the grid; `-b -PreviewImage` falling back to
# `-b -JpgFromRaw` then `-b -ThumbnailImage` for the overlay." Design 8.1 rev 17 gives the
# same three, in the same order, to the image loader's RAW branch. Written out as the
# specs write them, NOT built from the module's own constants.
SPEC_PREVIEW_CHAIN = (
    ("-b", "-PreviewImage"),
    ("-b", "-JpgFromRaw"),
    ("-b", "-ThumbnailImage"),
)
SPEC_THUMBNAIL_ARGS = ("-b", "-ThumbnailImage")

# Which tag each of the three cases must answer with, spelled from that sentence rather
# than read off a run: the chain takes the FIRST tag that is present, in that order.
CHAIN_CASES = (
    (("PreviewImage", "JpgFromRaw", "ThumbnailImage"), "PreviewImage"),
    (("JpgFromRaw", "ThumbnailImage"), "JpgFromRaw"),
    (("ThumbnailImage",), "ThumbnailImage"),
)


def _raw(tmp_path: Path, name: str, tags: tuple[str, ...]) -> Path:
    path = tmp_path / name
    path.write_bytes(build_tiny_library.raw_with_previews(name, tags=tags))
    return path


def test_the_preview_chain_is_exactly_what_the_specs_freeze_in_that_order():
    assert exiftool.PREVIEW_ATTEMPTS == SPEC_PREVIEW_CHAIN


def test_the_thumbnail_attempt_is_one_tuple_and_never_a_chain():
    """Spec 8.2: the grid variant is `-ThumbnailImage` alone and never falls back."""
    assert exiftool.THUMBNAIL_ARGS == SPEC_THUMBNAIL_ARGS


def test_the_fixture_really_does_carry_three_DIFFERENT_payloads():
    """The control for every ordering assertion below.

    If the three embedded images were the same bytes, reordering the chain would be
    invisible and the three cases below would pass for any order at all - which is the
    oracle weakness this project's mutation gate exists to find.
    """
    payloads = build_tiny_library.raw_preview_payloads("probe.ARW")
    assert sorted(payloads) == sorted(build_tiny_library.RAW_PREVIEW_TAGS)
    assert len(set(payloads.values())) == 3


@needs_exiftool
@pytest.mark.parametrize("tags,expected", CHAIN_CASES, ids=[e for _t, e in CHAIN_CASES])
def test_the_chain_returns_the_first_tag_the_file_carries(tmp_path, tags, expected):
    """Three files, three answers, and the answer is asserted on the BYTES.

    "A valid image came back" would be true of every ordering. What is checked is that
    the bytes are the payload written under `expected`'s tag and no other.
    """
    name = f"chain_{expected}.ARW"
    payloads = build_tiny_library.raw_preview_payloads(name)
    got = exiftool.extract_binary(_raw(tmp_path, name, tags), exiftool.PREVIEW_ATTEMPTS)
    assert got == payloads[expected]
    for other, blob in payloads.items():
        if other != expected:
            assert got != blob, f"the chain answered with {other}, not {expected}"


@needs_exiftool
def test_a_raw_carrying_nothing_at_all_is_the_EMPTY_answer_and_not_the_no_answer(tmp_path):
    """`b""` means "pam looked and there is nothing"; `None` means "pam did not find out".

    Spec 8.1 rev 17 makes this the failure case for the image loader and spec 8.2 makes
    it the 404 for a thumbnail, and both of them turn on it being distinguishable from an
    extraction that never completed.
    """
    source = _raw(tmp_path, "bare.ARW", ())
    assert exiftool.extract_binary(source, exiftool.PREVIEW_ATTEMPTS) == b""


@needs_exiftool
def test_the_thumbnail_attempt_does_NOT_reach_a_larger_preview(tmp_path):
    """Spec 8.2's grid rule, on a file where a fallback WOULD have found something.

    The RAW here carries `PreviewImage` and `JpgFromRaw` and no `ThumbnailImage`, so a
    chain that quietly acquired a fallback returns bytes and this returns `b""`. Pinned
    here as well as in `tests/test_webui_thumbs.py` because M10 moved the two chains into
    one module, and a merge is now a one-line edit.
    """
    source = _raw(tmp_path, "large_only.ARW", ("PreviewImage", "JpgFromRaw"))
    assert exiftool.extract_binary(source, (exiftool.THUMBNAIL_ARGS,)) == b""
    assert exiftool.extract_binary(source, exiftool.PREVIEW_ATTEMPTS) != b""


@needs_exiftool
def test_the_extractor_hands_exiftool_the_frozen_arguments_and_nothing_else(
    tmp_path, monkeypatch
):
    """The argv is watched, because "the right bytes came back" cannot see an extra flag.

    Spec 5.3's promise about this surface is about what pam RUNS, not about what it gets.
    """
    seen: list[list[str]] = []
    real = exiftool.subprocess.run

    def watching(argv, **kwargs):
        seen.append(list(argv))
        return real(argv, **kwargs)

    monkeypatch.setattr(exiftool.subprocess, "run", watching)
    source = _raw(tmp_path, "argv.ARW", build_tiny_library.RAW_PREVIEW_TAGS)
    exiftool.extract_binary(source, exiftool.PREVIEW_ATTEMPTS)
    assert len(seen) == 1, "the first tag answered, so no second call should have run"
    assert seen[0][1:] == [*SPEC_PREVIEW_CHAIN[0], str(source)]
