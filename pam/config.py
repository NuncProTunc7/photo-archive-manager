"""pam.toml loading: defaults, typo/type validation, and range checks."""

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pam.errors import ConfigError
from pam.scan.companions import DISPLAY_EXTS, LIVE_VIDEO_EXTS, RAW_EXTS

DEFAULT_IMAGE_EXTS = (
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
DEFAULT_VIDEO_EXTS = ("mov", "mp4", "avi", "mts")
DEFAULT_SIDECAR_EXTS = ("xmp", "aae")


@dataclass(frozen=True)
class TierBounds:
    floor: float
    low: float
    high: float
    max_tier_when_no_confidence: str = "medium"


def _default_tiers() -> Mapping[str, TierBounds]:
    return MappingProxyType(
        {
            "caption": TierBounds(0.0, 0.5, 0.85, "medium"),
            # A11: `[tiers] faces` KEEPS `floor` AND LOSES `low`/`high`, and the row is
            # still a `TierBounds` because the dataclass is shared with `caption` and
            # `ocr`, which do use all three. The plan's own fallback (Task 7 Step 5):
            # keep the dataclass, STOP READING THE TWO FIELDS. No CONSUMER
            # under `pam/` reads `tiers["faces"].low` or `.high` - and since 11 rev 24
            # (Task 9.6) `_check_ranges` does not either, because the row's VOCABULARY is
            # `floor` alone and the range check is scoped to it - and none may: a
            # detection has a
            # confidence and a floor, and no band above it - design 6.4 gives every face
            # token the same tier, so a low/high split would describe a distinction the
            # product does not make. `floor` is applied when detections are READ, never
            # when they are written, so raising and lowering it are both reversible.
            "faces": TierBounds(0.4, 0.6, 0.9),
            "ocr": TierBounds(0.5, 0.7, 0.9),
        }
    )


# Design 11's `[tiers]` block, TRANSCRIBED FROM THE SPEC and deliberately not read off
# `_default_tiers()` or off `dataclasses.fields(TierBounds)`. A list built from the thing
# it validates agrees with any value of that thing (M6's `ENRICHERS`, M5's `open_plan`),
# so it would accept a name the spec does not print the moment somebody adds one.
#
# 11 prints three rows - `caption`, `faces`, `ocr` - and says of the fourth candidate:
# "geocode has NO row here and takes NO override [...] would be read by nothing - the
# absence is the design, and it is written down here so nobody adds a knob that turns
# nothing." An unlisted name is therefore a setting pam will never read, which is the
# same shape as the typo'd key every other section of this file refuses.
TIER_NAMES = ("caption", "faces", "ocr")
# THE PRINTED ROW IS THE ROW'S VOCABULARY (11 rev 24), transcribed from the same block by
# hand and, like `TIER_NAMES` above, deliberately not read off `_default_tiers()` or off
# `dataclasses.fields(TierBounds)`. The three rows do NOT share one key set: A11 removed
# `low`/`high` from `faces`, and 11 rev 24 settles `ocr` as the minimal reading. Every key
# is optional on every row - an override is a MERGE - so this says which keys a row MAY
# name, not which it must.
#
# Task 9.5 checked one flat key set against every row, which told a `faces` user that the
# row takes `low`; rev 24 is what replaced that.
TIER_VOCABULARY: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "caption": ("floor", "low", "high", "max_tier_when_no_confidence"),
        "faces": ("floor",),
        "ocr": ("floor", "low", "high"),
    }
)
# Why a row's vocabulary EXCLUDES what it excludes, in 11 rev 24's own terms, so the
# refusal is a sentence and not "unknown key". A row whose vocabulary is complete needs
# no such sentence, which is why `caption` has no entry.
TIER_VOCABULARY_WHY: Mapping[str, str] = MappingProxyType(
    {
        "faces": (
            " Design 11 rev 22 (A11) removed `low` and `high` from this row: they would "
            "rate a DETECTOR's score, and design 6.1 gives every face token the tier of "
            "the person who typed the name, so the two knobs turned nothing."
        ),
        "ocr": (
            " Design 11 rev 24 settles this row as the minimal reading: "
            "`max_tier_when_no_confidence` exists for an enricher whose facts can carry "
            "NULL confidence - caption's do - and whether OCR's will is NOT KNOWN, "
            "because OCR is M12's and is not built."
        ),
    }
)
# The widest row's key set, spelled out once more so `_check_ranges` has a strict fallback
# for a row no vocabulary covers, and so the hand-transcribed vocabulary above and the
# `TierBounds` dataclass have one place where they are compared.
TIER_KEYS = ("floor", "low", "high", "max_tier_when_no_confidence")


def _tier_override(name: str, row: Mapping[str, Any]) -> TierBounds:
    """Design 11's `[tiers]` row, MERGED ONTO THE DEFAULT rather than replacing it.

    Until M11 Task 9.5 this was `TierBounds(**row)`, a replacement, so every row had to
    restate every required field and pam refused the exact row 11 PRINTS:
    `faces = { floor = 0.4 }`. That is not what any configuration system means by an
    override, and it was general - `caption = { floor = 0.1 }` failed identically.

    `replace()` keeps the keys the user did not write at their shipped defaults, which is
    A11's intent for `faces`' `low`/`high`, and it is why `TierBounds.low`/`.high` must
    NEVER be given dataclass defaults: that would make a `caption` row missing them load
    silently as something the user never wrote.
    """
    if name not in TIER_NAMES:
        legal = ", ".join(TIER_NAMES)
        raise ConfigError(
            f"[tiers] {name} is not an enricher pam applies tier bounds to. The rows "
            f"pam reads are: {legal}. Every other enricher's tier is settled by design "
            "rather than by configuration, so an entry here would be a knob that turns "
            f"nothing. Correct the spelling, or remove {name} from [tiers] in pam.toml."
        )
    vocabulary = TIER_VOCABULARY[name]
    unknown = sorted(set(row) - set(vocabulary))
    if unknown:
        named = ", ".join(unknown)
        legal = (
            f"`{vocabulary[0]}` alone"
            if len(vocabulary) == 1
            else "any of: " + ", ".join(vocabulary)
        )
        raise ConfigError(
            f"[tiers] {name}: {named} is not a setting in the {name} row. `{name}` takes "
            f"{legal}, and the keys you leave out keep pam's defaults."
            f"{TIER_VOCABULARY_WHY.get(name, '')} Correct the spelling, or remove "
            f"{named} from the {name} row in pam.toml."
        )
    return replace(_default_tiers()[name], **row)


@dataclass(frozen=True)
class Config:
    template: str = "{date}_{event-or-scene}_{people}_{seq}"
    max_people: int = 3
    image_exts: tuple[str, ...] = DEFAULT_IMAGE_EXTS
    video_exts: tuple[str, ...] = DEFAULT_VIDEO_EXTS
    sidecar_exts: tuple[str, ...] = DEFAULT_SIDECAR_EXTS
    ignore_stem_suffixes: tuple[str, ...] = ()  # spec 5.4: declared, never guessed
    event_gap_hours: float = 4.0
    event_gps_km: float = 50.0
    workers: int = 4
    caption_enabled: bool = False
    caption_model: str = "qwen2.5vl"
    ollama_port: int = 11434
    # Design 11 rev 17/18 (M10 amendments A1, B2). ONE caption request's timeout, and
    # this enricher's OWN worker count, which overrides `[enrich] workers` for it alone.
    # 120 s because a vision model on CPU is seconds to a minute per image and
    # `net.DEFAULT_TIMEOUT_S` (10 s) would fail every call on the hardware this product
    # is for. 1 worker because concurrent caption requests are not assumed to improve
    # throughput and can substantially increase memory pressure - a user who has
    # measured their own setup may raise it anywhere in the range.
    caption_timeout_s: int = 120
    caption_workers: int = 1
    faces_enabled: bool = False
    # A1/A12. EMPTY MEANS NOT CONFIGURED, and it is not a path pam will ever fill in:
    # there is no code in pam that can fetch a model, so an unset `model_dir` is the
    # state of every machine until the user puts two ONNX files somewhere and says where.
    # `faces_model.describe("")` is the sentence that says so.
    faces_model_dir: str = ""
    # A12: NULLABLE, AND UNSET. A cosine threshold is a property of an embedding space,
    # not of pam, and the `0.35` this line carried until M11 Task 7 was inherited from a
    # plan revision that had never run any stack - it was never measured for YuNet+SFace
    # or for anything else. The spike's only anchor is an upper one (three different real
    # people sat at 0.47, 0.65 and 0.72 apart), and the lower bound needs same-person
    # pairs across photographs, which needs the maintainer's labels. Until M11 Task 9 measures
    # one, `pam faces cluster` REFUSES TO RUN and names this key. A default nobody
    # measured would silently merge two people into one filename.
    faces_cluster_epsilon: float | None = None
    # This enricher's own worker count, overriding `[enrich] workers` for it alone, on
    # `caption_workers`' precedent. 1 because onnxruntime runs its own intra-op thread
    # pool and stacking process-level parallelism on top of it is how a user discovers
    # their machine's memory ceiling. Range 1-32, the same as every other worker count
    # here because it is the same kind of number.
    faces_workers: int = 1
    ocr_enabled: bool = False
    # Design 11 rev 27 (P10, D2). The folders, relative to the archive root, that hold
    # SCANNED PHOTOGRAPHS. EMPTY BY DEFAULT: pam never guesses which pictures are scans, because
    # the other pictures with no camera date are mostly SCREENSHOTS, whose dates are about
    # what is on the screen. KEPT AS WRITTEN - not lower-cased and not stripped of a leading
    # dot the way extensions are, because "." is an entry (the whole archive, only when
    # written) and the comparison is case-insensitive by path component at READ time
    # (`naming/dates.py::in_folders`), so narrowing the list is reversible without touching
    # a fact. `_check_ocr_folders` refuses each malformed shape by name.
    ocr_folders: tuple[str, ...] = ()
    # Design 11 rev 28 (Q3), on `faces_model_dir`'s rev 22 text: the directory holding the
    # two PP-OCRv4 files. EMPTY MEANS NOT CONFIGURED - pam never fetches a model - and
    # whether the folder exists is `available()`'s question at run time, never a refusal
    # here. Read, never written. No manifest.
    ocr_model_dir: str = ""
    # Design 11 rev 27 (P10). This enricher's own worker count, on `faces_workers`' precedent.
    ocr_workers: int = 1
    # Design 8.1's near-dupe threshold: "hamming distance <= threshold", over the 64-bit
    # dHash `pam/enrich/phash.py` writes. 8 bits of 64 is the value the dHash literature
    # settles on for "the same picture, differently processed"; 0 is exact-equality (which
    # `files.sha256` already answers better) and anything past ~16 starts grouping
    # unrelated photographs. NOTHING IS STORED UNDER IT - groups are derived at report
    # time - so changing it re-groups on the next report and costs no migration and no
    # re-enrichment. Design 11's sample has no `[enrich.phash]` table; adding one is an
    # amendment M9's close owes, and the key is here because design 8.1 says "threshold"
    # and this project's threshold are configuration.
    phash_near_dupe_distance: int = 8
    tiers: Mapping[str, TierBounds] = field(default_factory=_default_tiers)


# (section, key) -> (Config attribute, required python type)
_KEYMAP: dict[tuple[str, str], tuple[str, type]] = {
    ("naming", "template"): ("template", str),
    ("naming", "max_people"): ("max_people", int),
    ("scan", "image_exts"): ("image_exts", list),
    ("scan", "video_exts"): ("video_exts", list),
    ("scan", "sidecar_exts"): ("sidecar_exts", list),
    ("scan", "ignore_stem_suffixes"): ("ignore_stem_suffixes", list),
    ("events", "gap_hours"): ("event_gap_hours", (int, float)),  # type: ignore[dict-item]
    ("events", "gps_km"): ("event_gps_km", (int, float)),  # type: ignore[dict-item]
    ("enrich", "workers"): ("workers", int),
    ("enrich.caption", "enabled"): ("caption_enabled", bool),
    ("enrich.caption", "model"): ("caption_model", str),
    ("enrich.caption", "ollama_port"): ("ollama_port", int),
    ("enrich.caption", "timeout_s"): ("caption_timeout_s", int),
    ("enrich.caption", "workers"): ("caption_workers", int),
    ("enrich.faces", "enabled"): ("faces_enabled", bool),
    ("enrich.faces", "model_dir"): ("faces_model_dir", str),
    ("enrich.faces", "cluster_epsilon"): ("faces_cluster_epsilon", (int, float)),  # type: ignore[dict-item]
    ("enrich.faces", "workers"): ("faces_workers", int),
    ("enrich.ocr", "enabled"): ("ocr_enabled", bool),
    ("enrich.ocr", "folders"): ("ocr_folders", list),
    ("enrich.ocr", "model_dir"): ("ocr_model_dir", str),
    ("enrich.ocr", "workers"): ("ocr_workers", int),
    ("enrich.phash", "near_dupe_distance"): ("phash_near_dupe_distance", int),
}


def _flatten(data: dict[str, Any], prefix: str = "") -> list[tuple[str, str, Any]]:
    """Yield (section, key, value) for leaf keys; nested tables extend the section."""
    out: list[tuple[str, str, Any]] = []
    for key, value in data.items():
        if isinstance(value, dict) and prefix != "tiers":
            section = f"{prefix}.{key}" if prefix else key
            out.extend(_flatten(value, section))
        else:
            out.append((prefix, key, value))
    return out


def _check_ranges(cfg: Config) -> None:
    checks = [
        (1 <= cfg.workers <= 32, "workers must be between 1 and 32"),
        (0 <= cfg.max_people <= 10, "max_people must be between 0 and 10"),
        (cfg.event_gap_hours > 0, "gap_hours must be positive"),
        (cfg.event_gps_km > 0, "gps_km must be positive"),
        (1 <= cfg.ollama_port <= 65535, "ollama_port must be a valid TCP port (1-65535)"),
        # Design 11 rev 17/18's two ranges, spelled as written there. The upper bound on
        # `workers` is `[enrich] workers`' own, because it is the same kind of number.
        (1 <= cfg.caption_timeout_s <= 3600, "caption timeout_s must be between 1 and 3600"),
        (1 <= cfg.caption_workers <= 32, "caption workers must be between 1 and 32"),
        # A12: THE RANGE CHECK APPLIES ONLY WHEN THE KEY IS SET. `None` is not an
        # out-of-range value, it is the absence of one, and rejecting it here would make
        # the shipped default configuration illegal.
        (
            cfg.faces_cluster_epsilon is None or 0 < cfg.faces_cluster_epsilon <= 2,
            "cluster_epsilon must be in (0, 2]",
        ),
        (1 <= cfg.faces_workers <= 32, "faces workers must be between 1 and 32"),
        (1 <= cfg.ocr_workers <= 32, "ocr workers must be between 1 and 32"),
        # 64 is the whole hash: at that distance every image is a near-dupe of every other,
        # which is not a threshold anyone means. The upper bound is the hash width and not
        # a taste judgement, so it does not move when the default does.
        (
            0 <= cfg.phash_near_dupe_distance <= 64,
            "near_dupe_distance must be between 0 and 64 (the dHash is 64 bits)",
        ),
    ]
    for ok, message in checks:
        if not ok:
            raise ConfigError(message)
    # 11 rev 24: the range check applies ONLY to the keys a row HAS. There is no row
    # named here - the ordering is generated from that row's own vocabulary, so a row
    # that names one threshold is ordered against 0 and 1 and against nothing else, and
    # a row that names three is ordered across all three. Ordering a row's one knob
    # against fields its vocabulary excludes constrains the user by an artifact of a
    # shared dataclass rather than by a decision.
    for name, bounds in cfg.tiers.items():
        vocabulary = TIER_VOCABULARY.get(name, TIER_KEYS)
        thresholds = [key for key in ("floor", "low", "high") if key in vocabulary]
        for field_name in thresholds:
            value = getattr(bounds, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigError(f"[tiers] {name}: {field_name} must be a number")
        if (
            "max_tier_when_no_confidence" in vocabulary
            and bounds.max_tier_when_no_confidence not in ("high", "medium", "low")
        ):
            raise ConfigError(
                f"[tiers] {name}: max_tier_when_no_confidence must be one of: high, medium, low"
            )
        values = [getattr(bounds, key) for key in thresholds]
        ascending = [0.0, *values]
        descending = [*values, 1.0]
        if not all(a <= b for a, b in zip(ascending, descending, strict=True)):
            rule = " <= ".join(["0", *thresholds, "1"])
            raise ConfigError(f"[tiers] {name}: bounds must satisfy {rule}")


def _check_sidecar_exts(cfg: Config) -> None:
    """spec 5.4: a sidecar is NEVER a companion group's primary, "under any
    circumstances" - so an extension pam already treats as a photo or a video may
    not also be declared a sidecar.

    M5 open question 1. `scan.sidecar_exts` is free-form user config, and with
    `sidecar_exts = ["jpg"]` the primary election has nothing left to elect: every
    member of the group is a declared sidecar, so the fallback in
    `companions.group_companions` hands the election back to one of them and the
    unit is named from a file that carries no capture date. Reproduced at the M5
    review gate on two configurations.

    The refusal is here rather than in the election because there is no correct
    behaviour downstream of "a JPEG is a sidecar" - patching the election would
    close one symptom of a configuration that is meaningless in the first place.

    The three class lists are named separately rather than pre-unioned into a
    constant so the mutation gate can drop each operand on its own.
    """
    media = DISPLAY_EXTS | RAW_EXTS | LIVE_VIDEO_EXTS
    offenders = sorted(set(cfg.sidecar_exts) & media)
    if offenders:
        named = ", ".join(offenders)
        raise ConfigError(
            f"[scan] sidecar_exts declares {named} as a sidecar, but pam already "
            "reads those as photos or videos. A sidecar is a small companion file "
            "such as .xmp or .aae; it carries no capture date, so pam never lets one "
            "give a group of files its new name. Declaring a photo or video "
            "extension as a sidecar leaves pam with nothing to name the group from. "
            f"Remove {named} from sidecar_exts in pam.toml."
        )


# P10's "no drive": a drive letter and its colon, `C:` or `c:\`, at the start of an entry.
_DRIVE = re.compile(r"^[A-Za-z]:")


def _check_ocr_folders(cfg: Config) -> None:
    """Design 11 rev 27 (P10): each `[enrich.ocr] folders` entry is a path RELATIVE to the
    archive root - '/' or backslash separators, no drive, no leading separator, no '..'
    component.

    Each rule is its own refusal naming the entry, so the user is told which line to fix.
    An EMPTY entry is refused too: it is not "." (the whole archive, "only when written"),
    and reading it as one would widen the scope to every picture pam holds - screenshots
    included - on a typo.
    """
    for entry in cfg.ocr_folders:
        where = f"[enrich.ocr] folders entry {entry!r}"
        if not entry.strip():
            raise ConfigError(
                f'{where} is empty. Write the folder\'s name, or "." for the whole archive.'
            )
        if _DRIVE.match(entry):
            raise ConfigError(f"{where} names a drive. Folders are relative to the archive.")
        if entry[0] in "/\\":
            raise ConfigError(
                f"{where} starts with a separator. Folders are relative to the archive."
            )
        if ".." in re.split(r"[\\/]", entry):
            raise ConfigError(f"{where} contains '..'. Folders must stay inside the archive.")


def load_config(path: Path) -> Config:
    if not path.exists():
        cfg = Config()
        _check_ranges(cfg)
        _check_sidecar_exts(cfg)
        _check_ocr_folders(cfg)
        return cfg
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path.name} is not valid TOML: {e}") from None

    overrides: dict[str, Any] = {}
    tier_overrides: dict[str, TierBounds] = {}
    for section, key, value in _flatten(data):
        if section == "tiers":
            if not isinstance(value, dict):
                raise ConfigError(f"[tiers] {key} must be a table")
            tier_overrides[key] = _tier_override(key, value)
            continue
        mapping = _KEYMAP.get((section, key))
        if mapping is None:
            raise ConfigError(
                f"Unknown setting [{section}] {key} in {path.name} - check for typos."
            )
        attr, expected = mapping
        if expected is bool and not isinstance(value, bool):
            raise ConfigError(f"[{section}] {key} must be true or false")
        if expected is int and (isinstance(value, bool) or not isinstance(value, int)):
            raise ConfigError(f"[{section}] {key} must be an integer")
        if expected is str and not isinstance(value, str):
            raise ConfigError(f"[{section}] {key} must be a string")
        if expected is list:
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ConfigError(f"[{section}] {key} must be a list of strings")
            if attr in ("ignore_stem_suffixes", "ocr_folders"):
                # spec 5.4: a stem suffix keeps its leading dot ('.acr_'), so the
                # extension normalization below would destroy it. stem_key already
                # compares case-insensitively, so the value is kept verbatim. The same
                # holds for design 11's `[enrich.ocr] folders` (P10): "." is an entry,
                # and `naming/dates.py::in_folders` compares case-insensitively.
                value = tuple(value)
            else:
                value = tuple(s.lower().lstrip(".") for s in value)
        if isinstance(expected, tuple) and not (
            isinstance(value, (int, float)) and not isinstance(value, bool)
        ):
            raise ConfigError(f"[{section}] {key} must be a number")
        overrides[attr] = value

    if tier_overrides:
        merged = dict(_default_tiers())
        merged.update(tier_overrides)
        overrides["tiers"] = MappingProxyType(merged)
    cfg = Config(**overrides)
    _check_ranges(cfg)
    _check_sidecar_exts(cfg)
    _check_ocr_folders(cfg)
    return cfg
