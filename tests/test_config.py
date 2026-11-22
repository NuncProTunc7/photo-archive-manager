from dataclasses import MISSING, fields
from pathlib import Path

import pytest

from pam.config import (
    TIER_KEYS,
    TIER_NAMES,
    TIER_VOCABULARY,
    Config,
    TierBounds,
    load_config,
)
from pam.errors import ConfigError


def test_defaults_when_file_missing(tmp_path):
    cfg = load_config(tmp_path / "pam.toml")
    assert cfg.template == "{date}_{event-or-scene}_{people}_{seq}"
    assert cfg.max_people == 3
    assert "jpg" in cfg.image_exts and "mov" in cfg.video_exts
    assert cfg.event_gap_hours == 4.0
    assert cfg.workers == 4
    assert cfg.caption_enabled is False
    assert cfg.tiers["caption"] == TierBounds(
        floor=0.0, low=0.5, high=0.85, max_tier_when_no_confidence="medium"
    )


def test_overrides(tmp_path):
    path = tmp_path / "pam.toml"
    path.write_text(
        '[naming]\ntemplate = "{date}_{seq}"\nmax_people = 2\n'
        "[enrich]\nworkers = 8\n"
        "[enrich.caption]\nenabled = true\n",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.template == "{date}_{seq}"
    assert cfg.max_people == 2
    assert cfg.workers == 8
    assert cfg.caption_enabled is True
    assert cfg.faces_enabled is False  # untouched default


def test_unknown_key_rejected(tmp_path):
    path = tmp_path / "pam.toml"
    path.write_text('[naming]\ntemplte = "oops"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="templte"):
        load_config(path)


def test_bad_type_rejected(tmp_path):
    path = tmp_path / "pam.toml"
    path.write_text('[enrich]\nworkers = "many"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="workers"):
        load_config(path)


def test_config_is_frozen(tmp_path):
    cfg = load_config(tmp_path / "pam.toml")
    with pytest.raises(AttributeError):
        cfg.workers = 99  # type: ignore[misc]


def test_out_of_range_workers_rejected(tmp_path):
    path = tmp_path / "pam.toml"
    path.write_text("[enrich]\nworkers = -50\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="workers"):
        load_config(path)


def test_out_of_range_port_rejected(tmp_path):
    path = tmp_path / "pam.toml"
    path.write_text("[enrich.caption]\nollama_port = 0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="ollama_port"):
        load_config(path)


def test_inconsistent_tier_bounds_rejected(tmp_path):
    path = tmp_path / "pam.toml"
    path.write_text(
        "[tiers]\ncaption = { floor = 0.9, low = 0.5, high = 0.2 }\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="floor <= low <= high"):
        load_config(path)


def test_non_numeric_tier_bound_rejected(tmp_path):
    path = tmp_path / "pam.toml"
    path.write_text(
        '[tiers]\ncaption = { floor = "x", low = 0.5, high = 0.9 }\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="floor must be a number"):
        load_config(path)


def test_boolean_tier_bound_rejected(tmp_path):
    path = tmp_path / "pam.toml"
    path.write_text(
        "[tiers]\ncaption = { floor = true, low = 0.5, high = 0.9 }\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="floor must be a number"):
        load_config(path)


def test_sidecar_exts_default_and_override(tmp_path):
    assert load_config(tmp_path / "missing.toml").sidecar_exts == ("xmp", "aae")
    path = tmp_path / "pam.toml"
    path.write_text('[scan]\nsidecar_exts = ["XMP", ".THM"]\n', encoding="utf-8")
    # Same normalization as image_exts/video_exts: lowercased, leading dot stripped.
    assert load_config(path).sidecar_exts == ("xmp", "thm")


# --- spec 5.4: a sidecar is never a primary, so a photo is never a sidecar ---------
# M5 open question 1. Each class list is driven separately, because the guard is a
# three-operand union and a whole-guard test credits it with kills its operands do
# not have (the mutation gate's operand-by-operand rule).


@pytest.mark.parametrize(
    ("ext", "class_list"),
    [("jpg", "DISPLAY_EXTS"), ("nef", "RAW_EXTS"), ("mov", "LIVE_VIDEO_EXTS")],
)
def test_a_sidecar_ext_that_is_really_a_photo_or_video_is_refused(tmp_path, ext, class_list):
    path = tmp_path / "pam.toml"
    path.write_text(f'[scan]\nsidecar_exts = ["xmp", "{ext}"]\n', encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    message = str(excinfo.value)
    # M6 review, mutation M242: `match=ext` alone was NOT enough, and the `mov` row
    # proved it. The word "Re-MOV-e" in the fix sentence contains 'mov', so a message
    # that had stopped naming the offending extension ANYWHERE still passed this test
    # - a negative test passing for the wrong reason. The message names the extension
    # twice, so both occurrences are pinned by phrase rather than by substring.
    assert f"declares {ext} as a sidecar" in message, class_list
    # UX spec 1.4: the message says what to do, not only what is wrong.
    assert f"Remove {ext} from sidecar_exts" in message


def test_the_normalized_form_is_what_is_checked(tmp_path):
    """`.JPG` reaches the check as `jpg`, or the refusal is trivial to sidestep."""
    path = tmp_path / "pam.toml"
    path.write_text('[scan]\nsidecar_exts = [".JPG"]\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="declares jpg as a sidecar"):
        load_config(path)


def test_the_default_sidecar_exts_load_cleanly(tmp_path):
    """A refusal that also refuses the default ships a broken product."""
    assert load_config(tmp_path / "missing.toml").sidecar_exts == ("xmp", "aae")
    path = tmp_path / "pam.toml"
    path.write_text('[scan]\nsidecar_exts = ["xmp", "aae"]\n', encoding="utf-8")
    assert load_config(path).sidecar_exts == ("xmp", "aae")


def test_an_unknown_extension_is_still_accepted_as_a_sidecar(tmp_path):
    """The rule is "not a photo and not a video", NOT an allowlist. `.lrv` is a
    real GoPro companion pam has no class for, and declaring it must keep working."""
    path = tmp_path / "pam.toml"
    path.write_text('[scan]\nsidecar_exts = ["lrv"]\n', encoding="utf-8")
    assert load_config(path).sidecar_exts == ("lrv",)


def test_invalid_max_tier_string_rejected(tmp_path):
    path = tmp_path / "pam.toml"
    path.write_text(
        "[tiers]\n"
        "caption = { floor = 0.0, low = 0.5, high = 0.9, "
        'max_tier_when_no_confidence = "extreme" }\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="max_tier_when_no_confidence"):
        load_config(path)


def test_the_events_defaults_are_spec_11_s(tmp_path):
    # spec 11: [events] gap_hours = 4, gps_km = 50. Written out here rather than
    # read off Config(), so a changed default is a red test and not a silent
    # re-baselining of the partition every archive is grouped by.
    cfg = load_config(tmp_path / "pam.toml")
    assert (cfg.event_gap_hours, cfg.event_gps_km) == (4.0, 50.0)


def test_the_events_section_overrides_both_keys(tmp_path):
    path = tmp_path / "pam.toml"
    path.write_text("[events]\ngap_hours = 6\ngps_km = 12.5\n", encoding="utf-8")
    cfg = load_config(path)
    assert (cfg.event_gap_hours, cfg.event_gps_km) == (6, 12.5)


def test_the_events_keys_are_spelled_spec_11_s_way_not_the_attribute_s(tmp_path):
    # The Config ATTRIBUTES are event_gap_hours/event_gps_km; the pam.toml KEYS are
    # [events] gap_hours/gps_km. A user who writes the attribute name gets the same
    # typo error every other unknown key gets, not a silently ignored setting.
    path = tmp_path / "pam.toml"
    path.write_text("[events]\nevent_gap_hours = 6\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"Unknown setting \[events\] event_gap_hours"):
        load_config(path)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("[events]\ngap_hours = 0\n", "gap_hours must be positive"),
        ("[events]\ngap_hours = -1.5\n", "gap_hours must be positive"),
        ("[events]\ngps_km = 0\n", "gps_km must be positive"),
        ("[events]\ngps_km = -0.5\n", "gps_km must be positive"),
    ],
)
def test_a_non_positive_event_parameter_is_refused(tmp_path, body, message):
    # Zero is refused as well as negative, and deliberately: spec 8.3 splits on
    # "> gap_hours", so gap_hours = 0 makes every consecutive pair of photographs a
    # separate event - a partition with no meaning, arrived at by arithmetic rather
    # than by a user's intent.
    path = tmp_path / "pam.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_config(path)


@pytest.mark.parametrize("key", ["gap_hours", "gps_km"])
def test_a_non_numeric_event_parameter_is_refused_in_the_usual_shape(tmp_path, key):
    path = tmp_path / "pam.toml"
    path.write_text(f'[events]\n{key} = "four"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match=rf"\[events\] {key} must be a number"):
        load_config(path)


@pytest.mark.parametrize("key", ["gap_hours", "gps_km"])
def test_a_boolean_event_parameter_is_not_a_number(tmp_path, key):
    # bool is a subclass of int in Python, so `gap_hours = true` would otherwise
    # load as 1 - a valid, positive, entirely wrong parameter.
    path = tmp_path / "pam.toml"
    path.write_text(f"[events]\n{key} = true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=rf"\[events\] {key} must be a number"):
        load_config(path)


# --- M10 amendments A1 and B2: `[enrich.caption] timeout_s` and `workers` --------------


def test_the_caption_defaults_are_design_11s_own_numbers(tmp_path):
    """Design 11 rev 17/18, spelled by hand from the spec's sample and NOT from `Config`.

    120 s because "a vision model on CPU is seconds to a minute per image" and `net.py`'s
    10 s default would fail every call on the hardware this product is for. 1 worker
    because concurrent caption requests are not assumed to improve throughput and can
    substantially increase memory pressure - and NOT because of any claim about how Ollama
    schedules them, which is the sentence rev 18 deliberately removed (B2). The GLOBAL
    `[enrich] workers` stays 4, and the two being DIFFERENT is the whole point of the key.
    """
    cfg = load_config(tmp_path / "pam.toml")

    assert cfg.caption_timeout_s == 120
    assert cfg.caption_workers == 1
    assert cfg.workers == 4


def test_the_caption_keys_are_read_from_their_own_table(tmp_path):
    """All three numbers set to different values, so a key read from the wrong table -
    `[enrich] workers` for the captioner, or the reverse - cannot pass."""
    path = tmp_path / "pam.toml"
    path.write_text(
        "[enrich]\nworkers = 8\n\n[enrich.caption]\ntimeout_s = 600\nworkers = 16\n",
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert (cfg.caption_timeout_s, cfg.caption_workers, cfg.workers) == (600, 16, 8)


@pytest.mark.parametrize(
    ("toml", "message"),
    [
        ("[enrich.caption]\ntimeout_s = 0\n", "timeout_s"),
        ("[enrich.caption]\ntimeout_s = 3601\n", "timeout_s"),
        ("[enrich.caption]\nworkers = 0\n", "workers"),
        ("[enrich.caption]\nworkers = 33\n", "workers"),
    ],
)
def test_the_caption_ranges_are_enforced_at_both_ends(tmp_path, toml, message):
    """A1's ranges: 1 <= timeout_s <= 3600 and 1 <= workers <= 32.

    Both ends of both, because a check written `if value < 1` passes every upper-bound
    case and a range tested from one side is not a range. B2 forbids CLAMPING the worker
    range down: a user who has measured their own setup may raise it to 32.
    """
    path = tmp_path / "pam.toml"
    path.write_text(toml, encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        load_config(path)


@pytest.mark.parametrize("value", [1, 2, 16, 32])
def test_every_caption_worker_count_in_the_range_is_accepted(tmp_path, value):
    """B2: "the code must work at any value in the range"."""
    path = tmp_path / "pam.toml"
    path.write_text(f"[enrich.caption]\nworkers = {value}\n", encoding="utf-8")

    assert load_config(path).caption_workers == value


# --- M11 Task 9.5: `[tiers]` overrides are MERGES, not replacements -------------------
#
# Design 11 rev 22 (A11) PRINTS `faces = { floor = 0.4 }` and pam refused it, because
# `TierBounds(**row)` replaced the whole row and made every required field mandatory.
# A SPEC THAT PRINTS A CONFIGURATION THE PRODUCT REJECTS IS THE FAILURE MODE HERE, and
# the only guard against it returning is asserting the printed rows themselves.
#
# Every row below is TRANSCRIBED BY HAND from design 11's `[tiers]` block, never copied
# out of `_default_tiers()`: an oracle built from the definition it checks agrees with
# any value of that definition.

SPEC_11_TIERS_BLOCK = (
    "[tiers]\n"
    'caption = { floor = 0.0, low = 0.5, high = 0.85, max_tier_when_no_confidence = "medium" }\n'
    "faces   = { floor = 0.4 }\n"
    "ocr     = { floor = 0.5, low = 0.7, high = 0.9 }\n"
)

# 11 names `geocode` as the row that must NOT exist ("takes NO override [...] would be
# read by nothing"); `bogus` is the ordinary typo. Asserted non-empty at collection,
# because pytest SKIPS an empty parametrize rather than failing it (M743).
UNKNOWN_TIER_NAMES = ["geocode", "bogus"]
assert "geocode" in UNKNOWN_TIER_NAMES, "11's own named non-row must stay in this list"


def test_the_tiers_block_design_11_prints_is_a_configuration_pam_ACCEPTS(tmp_path):
    """The whole `[tiers]` block, verbatim from the spec's sample.

    This is the test the defect could not survive: `faces = { floor = 0.4 }` was a
    `ConfigError` reading `TierBounds.__init__() missing 2 required positional
    arguments`, so the product refused its own documented configuration.
    """
    path = tmp_path / "pam.toml"
    path.write_text(SPEC_11_TIERS_BLOCK, encoding="utf-8")

    tiers = load_config(path).tiers

    # The numbers are 11's, written out here rather than compared against Config().
    assert tiers["caption"] == TierBounds(0.0, 0.5, 0.85, "medium")
    assert tiers["ocr"] == TierBounds(0.5, 0.7, 0.9, "medium")
    assert tiers["faces"].floor == 0.4


def test_the_faces_row_design_11_prints_names_ONLY_a_floor(tmp_path):
    """A11: `low`/`high` are GONE from the `faces` row - two knobs that turned nothing.

    So the row names one key of four and the other three keep pam's defaults. The two
    unread fields are asserted at their default values rather than at nothing, because
    "the merge kept them" and "the merge invented them" are different outcomes.
    """
    path = tmp_path / "pam.toml"
    path.write_text("[tiers]\nfaces = { floor = 0.45 }\n", encoding="utf-8")

    faces = load_config(path).tiers["faces"]

    assert faces.floor == 0.45
    assert (faces.low, faces.high) == (0.6, 0.9)  # untouched shipped defaults
    assert faces.max_tier_when_no_confidence == "medium"


def test_a_partial_caption_override_keeps_every_key_it_does_not_name(tmp_path):
    """THIS IS NOT A `faces` BUG - it was every partial override in pam.

    `caption` is the row that uses all four fields, so a merge that silently reset the
    unnamed ones would change what the captioner's tokens are rated at.
    """
    path = tmp_path / "pam.toml"
    path.write_text("[tiers]\ncaption = { low = 0.6 }\n", encoding="utf-8")

    caption = load_config(path).tiers["caption"]

    assert caption.low == 0.6
    assert (caption.floor, caption.high) == (0.0, 0.85)
    assert caption.max_tier_when_no_confidence == "medium"


def test_a_partial_override_leaves_THE_OTHER_ROWS_at_their_defaults(tmp_path):
    """One row named; the other two are not silently dropped from the mapping."""
    path = tmp_path / "pam.toml"
    path.write_text("[tiers]\nfaces = { floor = 0.5 }\n", encoding="utf-8")

    tiers = load_config(path).tiers

    assert sorted(tiers) == ["caption", "faces", "ocr"]
    assert tiers["caption"] == TierBounds(0.0, 0.5, 0.85, "medium")
    assert tiers["ocr"] == TierBounds(0.5, 0.7, 0.9, "medium")


def test_a_full_tier_row_is_still_accepted(tmp_path):
    """The merge must not make a complete row illegal: it is still the way to set
    every key at once, and it is how 11 prints the `caption` and `ocr` rows.

    Rewritten at Task 9.6, NOT deleted: the row this used to write named all four keys
    on `ocr`, and 11 rev 24 settles that row's vocabulary as {floor, low, high}. The
    assertion is about a COMPLETE row being accepted, which is still true and still
    worth pinning - it is "complete" that changed meaning, from "all of `TierBounds`"
    to "all of this row's vocabulary". The four-key row lives on `caption`, in
    `test_every_key_of_the_widest_row_is_still_accepted_on_that_row`.
    """
    path = tmp_path / "pam.toml"
    path.write_text(
        "[tiers]\nocr = { floor = 0.2, low = 0.4, high = 0.6 }\n",
        encoding="utf-8",
    )

    assert load_config(path).tiers["ocr"] == TierBounds(0.2, 0.4, 0.6, "medium")


def test_a_misspelled_field_in_a_tier_row_is_STILL_refused(tmp_path):
    """A merge must not become a shrug. `flor` is not `floor`, and a setting that is
    silently ignored is worse than one that is refused - the user believes it took."""
    path = tmp_path / "pam.toml"
    path.write_text("[tiers]\ncaption = { flor = 0.1 }\n", encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    message = str(excinfo.value)
    assert "flor is not a setting in the caption row" in message
    assert "remove flor from the caption row in pam.toml" in message
    # A leaked constructor signature is not a sentence (Step 4).
    assert "__init__" not in message and "keyword argument" not in message


@pytest.mark.parametrize("name", UNKNOWN_TIER_NAMES)
def test_an_unknown_tier_name_is_refused_rather_than_silently_stored(tmp_path, name):
    """Step 3, decided from 11: the block prints `caption`, `faces` and `ocr`, and says
    of the fourth candidate that "geocode has NO row here and takes NO override [...]
    would be read by nothing - the absence is the design, and it is written down here
    so nobody adds a knob that turns nothing."

    THE ROW IS COMPLETE ON PURPOSE. A partial row was refused before this task by
    accident, through the missing-argument error every row got; a complete one was
    accepted and stored under a key nothing would ever read.
    """
    path = tmp_path / "pam.toml"
    path.write_text(
        f"[tiers]\n{name} = {{ floor = 0.1, low = 0.5, high = 0.9 }}\n", encoding="utf-8"
    )

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    message = str(excinfo.value)
    assert f"[tiers] {name} is not an enricher" in message
    # What to write instead - compared EXACTLY, never as a substring. Until the combined
    # 9.5+9.6 review this was `"caption, faces, ocr" in message`, which a WIDENED list
    # still contains (M1808, M1815: `caption, faces, ocr, people` passed it). The three
    # names are design 11's, written out here and not read off `TIER_NAMES`.
    assert "The rows pam reads are: " in message
    listed = message.split("The rows pam reads are: ", 1)[1].split(". ", 1)[0]
    assert listed.split(", ") == ["caption", "faces", "ocr"]
    assert f"remove {name} from [tiers] in pam.toml" in message


def test_the_row_vocabulary_is_the_one_the_dataclass_really_takes():
    """`TIER_KEYS` is transcribed from 11 and `TierBounds` is written in code; a merge
    built on `replace()` is only as good as the two agreeing. They are two independent
    spellings and this is the only place they meet.

    Task 9.6 adds the third spelling: every per-row vocabulary must be a subset of what
    the dataclass can be `replace()`d with, and the widest row must exhaust it - a key in
    no row's vocabulary is a field nothing can ever set.
    """
    assert set(TIER_KEYS) == {f.name for f in fields(TierBounds)}
    assert set(TIER_NAMES) == set(Config().tiers)
    assert set(TIER_VOCABULARY) == set(TIER_NAMES)
    assert set().union(*(set(keys) for keys in TIER_VOCABULARY.values())) == set(TIER_KEYS)


# --- M11 Task 9.6: the range check is scoped to the keys a row's vocabulary CONTAINS ---
#
# 11 rev 24: "THE PRINTED ROW IS THE ROW'S VOCABULARY, and the range check applies ONLY
# to the keys a row has. [...] For `faces` that is `0 <= floor <= 1` and nothing more."
#
# The defect: `_check_ranges` ordered every row across floor/low/high, so
# `faces = { floor = 0.7 }` was a ConfigError against a `low` of 0.6 that A11 had REMOVED
# from that row - immediately after `pam faces cluster`'s scale-ceiling refusal told the
# user to raise that very key, on a detector whose candidate threshold is 0.10.
#
# Each row below is transcribed BY HAND from 11's `[tiers]` block, not read off
# `_default_tiers()` or off `TIER_VOCABULARY`: an oracle built from the definition it
# checks agrees with any value of that definition.
SPEC_11_ROW_VOCABULARY = {
    "caption": {"floor", "low", "high", "max_tier_when_no_confidence"},
    "faces": {"floor"},
    "ocr": {"floor", "low", "high"},
}
assert SPEC_11_ROW_VOCABULARY, "the hand-spelled vocabulary is empty; it protects nothing"


@pytest.mark.parametrize(
    ("name", "keys"),
    sorted((n, tuple(sorted(k))) for n, k in SPEC_11_ROW_VOCABULARY.items()),
)
def test_each_printed_row_takes_exactly_the_keys_the_spec_prints_for_it(name, keys):
    """The three rows 11 prints, against an expectation written out from the spec."""
    assert set(TIER_VOCABULARY[name]) == set(keys)


# THE ROW NAMES 11 prints, spelled once more as a literal. `SPEC_11_ROW_VOCABULARY` above is
# also written from the spec, but it only PARAMETRIZES a lookup INTO production, so a row
# production ADDS is never compared against it. Measured (M1815): `TIER_NAMES` read off
# `_default_tiers()` plus a fourth row `people` added identically to `_default_tiers()` and
# `TIER_VOCABULARY` failed ONE test, on an incidental `sorted(...)` in a test about
# something else. Every production symbol agreed with every other, and all were wrong.
SPEC_11_ROW_NAMES = frozenset({"caption", "faces", "ocr"})
assert SPEC_11_ROW_NAMES, "the hand-spelled row names are empty; they protect nothing"


def test_the_tiers_rows_pam_reads_are_EXACTLY_the_three_design_11_prints(tmp_path):
    """F2 of the combined 9.5+9.6 review: nothing asserted that the rows pam reads ARE
    11's three. Compared in BOTH directions, against every production place a row name
    lives, and never against a production symbol as the expectation - `geocode` is the
    row 11 says must not exist, and a fourth row is a knob that turns nothing."""
    # Two test-side spellings of the same sentence of 11 must agree before either is used.
    assert set(SPEC_11_ROW_VOCABULARY) == SPEC_11_ROW_NAMES
    path = tmp_path / "pam.toml"
    path.write_text("[tiers]\nfaces = { floor = 0.5 }\n", encoding="utf-8")
    for label, actual in (
        ("TIER_NAMES", set(TIER_NAMES)),
        ("TIER_VOCABULARY", set(TIER_VOCABULARY)),
        ("Config().tiers", set(Config().tiers)),
        ("a loaded pam.toml's tiers", set(load_config(path).tiers)),
    ):
        extra = sorted(actual - SPEC_11_ROW_NAMES)
        missing = sorted(SPEC_11_ROW_NAMES - actual)
        assert extra == [], f"{label} holds a [tiers] row design 11 does not print: {extra}"
        assert missing == [], f"{label} lacks a [tiers] row design 11 prints: {missing}"


@pytest.mark.parametrize("floor", [0.0, 0.4, 0.7, 0.9, 1.0])
def test_a_faces_floor_ABOVE_the_removed_low_is_accepted(tmp_path, floor):
    """THE CASE THIS AMENDMENT EXISTS TO PERMIT.

    0.7 and 0.9 are both above the shipped `low` of 0.6 that A11 removed from this row,
    and 0.9 is where a user sent here by `pam faces cluster`'s refusal would naturally
    go on a detector whose candidate threshold is 0.10. Before Task 9.6 every value
    above 0.6 was a `ConfigError` quoting a rule about keys this row does not have.
    """
    path = tmp_path / "pam.toml"
    path.write_text(f"[tiers]\nfaces = {{ floor = {floor} }}\n", encoding="utf-8")

    assert load_config(path).tiers["faces"].floor == floor


@pytest.mark.parametrize("floor", [-0.1, 1.1])
def test_a_faces_floor_outside_0_to_1_is_STILL_refused(tmp_path, floor):
    """Scoping the check is not switching it off: `floor` is a confidence and the unit
    interval is the whole of what `faces` is ordered against."""
    path = tmp_path / "pam.toml"
    path.write_text(f"[tiers]\nfaces = {{ floor = {floor} }}\n", encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    message = str(excinfo.value)
    assert message == "[tiers] faces: bounds must satisfy 0 <= floor <= 1"
    # The rule quoted must not mention keys this row does not have (the defect's shape).
    assert "low" not in message and "high" not in message


def test_a_caption_floor_of_0_9_is_STILL_REFUSED(tmp_path):
    """THE NEGATIVE CONTROL PROVING THE SCOPING DID NOT SIMPLY SWITCH THE CHECK OFF.

    0.9 is the value `faces` now accepts. `caption` must still refuse it, because that
    row's vocabulary CONTAINS `low`, whose shipped default is 0.5 - so the merged row
    would read floor 0.9 > low 0.5, which is not an ordering of thresholds at all.
    """
    path = tmp_path / "pam.toml"
    path.write_text("[tiers]\ncaption = { floor = 0.9 }\n", encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    assert str(excinfo.value) == (
        "[tiers] caption: bounds must satisfy 0 <= floor <= low <= high <= 1"
    )


def test_an_ocr_row_is_still_ordered_across_all_three_of_its_thresholds(tmp_path):
    """`ocr`'s vocabulary contains floor, low and high, so the full ordering applies -
    the scoping is per row, and `ocr` is the row that proves it is not "one key each"."""
    path = tmp_path / "pam.toml"
    path.write_text("[tiers]\nocr = { floor = 0.8 }\n", encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    assert "0 <= floor <= low <= high <= 1" in str(excinfo.value)


def test_a_caption_row_is_still_ordered_across_all_four_of_its_keys(tmp_path):
    """The row that uses everything: three ordered thresholds AND the enum."""
    path = tmp_path / "pam.toml"
    path.write_text(
        "[tiers]\ncaption = { floor = 0.1, low = 0.4, high = 0.9, "
        'max_tier_when_no_confidence = "sideways" }\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    assert "max_tier_when_no_confidence must be one of: high, medium, low" in str(excinfo.value)


def test_a_key_outside_a_rows_vocabulary_is_refused_BY_NAME_with_the_reason(tmp_path):
    """A11's removal, said out loud. "Unknown key" would be true and useless: the user
    read `low` in a `caption` row, or in this file's own history, and needs to be told
    that THIS row does not have one and why."""
    path = tmp_path / "pam.toml"
    path.write_text("[tiers]\nfaces = { low = 0.5 }\n", encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    message = str(excinfo.value)
    assert "[tiers] faces: low is not a setting in the faces row" in message
    assert "`faces` takes `floor` alone" in message
    assert "A11" in message and "removed `low` and `high` from this row" in message
    assert "remove low from the faces row in pam.toml" in message


def test_the_ocr_row_refuses_max_tier_when_no_confidence_with_the_M12_reason(tmp_path):
    """11 rev 24 settles `ocr` as the MINIMAL reading, and the reason is a fact about
    what is not built: `max_tier_when_no_confidence` exists for an enricher whose facts
    can carry NULL confidence - caption's do (8.1) - and whether OCR's will is NOT KNOWN,
    because OCR is M12's. If M12 measures that it emits confidence-less facts, it adds
    the key to this row BY AMENDMENT, with the evidence attached."""
    path = tmp_path / "pam.toml"
    path.write_text(
        '[tiers]\nocr = { max_tier_when_no_confidence = "medium" }\n', encoding="utf-8"
    )

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    message = str(excinfo.value)
    assert "[tiers] ocr: max_tier_when_no_confidence is not a setting in the ocr row" in message
    assert "`ocr` takes any of: floor, low, high" in message
    assert "OCR is M12's and is not built" in message


def test_every_key_of_the_widest_row_is_still_accepted_on_that_row(tmp_path):
    """The scoping must not have narrowed `caption`, the row that reads all four."""
    path = tmp_path / "pam.toml"
    path.write_text(
        "[tiers]\ncaption = { floor = 0.1, low = 0.4, high = 0.6, "
        'max_tier_when_no_confidence = "low" }\n',
        encoding="utf-8",
    )

    assert load_config(path).tiers["caption"] == TierBounds(0.1, 0.4, 0.6, "low")


def test_TierBounds_low_and_high_have_NO_dataclass_defaults():
    """Step 2's warning, as an assertion.

    Giving the two fields defaults would make the merge redundant AND make
    `caption = { floor = 0.1 }` load as something the user never wrote, in a row where
    all four fields are read. The merge is what keeps `faces`' unread pair at their
    defaults; the dataclass must keep demanding them of anyone constructing one.
    """
    required = {f.name for f in fields(TierBounds) if f.default is MISSING}
    assert {"floor", "low", "high"} <= required
    with pytest.raises(TypeError):
        TierBounds(0.4)  # type: ignore[call-arg]


LOAD_BEARING_CONTROLS = {
    "test_the_tiers_block_design_11_prints_is_a_configuration_pam_ACCEPTS": (
        "one of fourteen killers of the merge-to-replacement defect, not its only one "
        "(M1811: deleted with that defect restored, thirteen still fail). Its unique "
        "content is that design 11's EXACT sample loads - the `ocr` 0.5/0.7/0.9 row and "
        "the four-key `caption` row together, which no other test writes"
    ),
    "test_the_faces_row_design_11_prints_names_ONLY_a_floor": (
        "A11's row, and the only place the unread `low`/`high` are pinned at their "
        "defaults rather than at whatever a merge happened to produce"
    ),
    "test_a_partial_caption_override_keeps_every_key_it_does_not_name": (
        "the general case. Without it the fix reads as a `faces` special case and the "
        "next partial override to be refused would look like new behaviour"
    ),
    "test_a_misspelled_field_in_a_tier_row_is_STILL_refused": (
        "the half a merge can quietly lose: `replace()` refuses unknown keywords, and "
        "nothing else here would notice that refusal turning into a shrug"
    ),
    "test_an_unknown_tier_name_is_refused_rather_than_silently_stored": (
        "11's `geocode` sentence, enforced. The complete row is the point - a partial "
        "one was refused by accident before this task"
    ),
    "test_TierBounds_low_and_high_have_NO_dataclass_defaults": (
        "Step 2's warning as an assertion. Defaults on the two fields would make every "
        "partial `caption` row load as something the user never wrote"
    ),
    "test_the_row_vocabulary_is_the_one_the_dataclass_really_takes": (
        "the spec-transcribed vocabulary and the dataclass are two independent "
        "spellings, and this is the only place they are compared"
    ),
    # --- Task 9.6: the range check is scoped per row ---
    "test_each_printed_row_takes_exactly_the_keys_the_spec_prints_for_it": (
        "the only place `TIER_VOCABULARY` is checked against a key set spelled from "
        "11 rather than read off itself. Without it the map agrees with any value of "
        "itself"
    ),
    "test_the_tiers_rows_pam_reads_are_EXACTLY_the_three_design_11_prints": (
        "the only assertion that the SET of rows pam reads equals 11's three, spelled "
        "from the spec. Without it a row added identically to `_default_tiers()` and "
        "`TIER_VOCABULARY` is caught by one incidental, unregistered `sorted(...)` (M1815)"
    ),
    "test_a_faces_floor_ABOVE_the_removed_low_is_accepted": (
        "the case rev 24 exists to permit. Deleting it lets the over-broad ordering "
        "come back and re-block the value `pam faces cluster`'s own refusal asks for"
    ),
    "test_a_caption_floor_of_0_9_is_STILL_REFUSED": (
        "THE NEGATIVE CONTROL for the PER-ROW scoping, and measured as such: switching "
        "the range check off entirely is caught nine ways (M1826), so this is not the "
        "sole guardian of the check. What it alone pins is the over-correction (M1827, "
        "`thresholds = ['floor']` for every row): 0.9, the value `faces` now accepts, "
        "is still REFUSED on `caption`, whose vocabulary has a `low` of 0.5"
    ),
    "test_a_faces_floor_outside_0_to_1_is_STILL_refused": (
        "the other half of the same control: `faces` keeps a range, it is just its "
        "own. A scoping that dropped the row's only threshold reads green without it"
    ),
    "test_a_key_outside_a_rows_vocabulary_is_refused_BY_NAME_with_the_reason": (
        "the per-row refusal sentence. Task 9.5's message named all four keys for "
        "every row, which told a `faces` user that the row takes `low`"
    ),
    "test_the_ocr_row_refuses_max_tier_when_no_confidence_with_the_M12_reason": (
        "the minimal reading of `ocr`, and the only record in the suite of WHY it is "
        "minimal - a fact about what M12 has not measured yet"
    ),
    "test_an_ocr_row_is_still_ordered_across_all_three_of_its_thresholds": (
        "proves the scoping is per row and not `faces`-shaped: two rows keep the full "
        "ordering and one does not"
    ),
}

assert LOAD_BEARING_CONTROLS, "this module's control list is empty; it protects nothing"


def test_no_load_bearing_control_in_this_file_can_be_deleted_without_saying_so():
    present = {name for name in globals() if name.startswith("test_")}
    missing = sorted(set(LOAD_BEARING_CONTROLS) - present)
    assert missing == [], (
        f"a load-bearing control has been deleted from {Path(__file__).name}:"
        f" {missing}. Each is why another assertion here means anything"
        f" ({[LOAD_BEARING_CONTROLS[m] for m in missing]})."
    )
