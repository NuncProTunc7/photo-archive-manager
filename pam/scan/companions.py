"""Companion grouping: same directory + same stem -> RAW+JPEG / Live Photo / sidecar."""

import posixpath
from collections.abc import Container, Iterable
from dataclasses import dataclass

RAW_EXTS = frozenset({"cr2", "nef", "arw", "dng"})
DISPLAY_EXTS = frozenset({"jpg", "jpeg", "heic", "png", "tif", "tiff"})
LIVE_VIDEO_EXTS = frozenset({"mov", "mp4"})

# The media types (`walker.media_type_for`) whose files are offered to grouping at all. `pam scan`
# filters its walk by them and `pam plan`'s Z2 check its present rows: one tuple, so the two
# cannot disagree about which files a grouping is computed over.
GROUPED_MEDIA_TYPES = ("image", "video", "sidecar")


@dataclass(frozen=True)
class CompanionGroup:
    kind: str  # 'raw_jpeg' | 'live_photo' | 'sidecar'
    primary_rel_path: str
    member_rel_paths: tuple[str, ...]  # sorted; includes the primary


def _ext(rel_path: str) -> str:
    filename = posixpath.basename(rel_path)
    return filename.rsplit(".", 1)[1].casefold() if "." in filename else ""


def stem_key(rel_path: str, ignore_suffixes: tuple[str, ...] = ()) -> tuple[str, str]:
    """(directory, comparable stem) - the single definition of 'shares a stem'.

    Used by grouping and by the planner's left-behind report (spec 6.3.3a), so the
    two can never disagree about what counts as the same stem.
    """
    directory, filename = posixpath.split(rel_path)
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    folded = stem.casefold()
    for suffix in ignore_suffixes:
        lowered = suffix.casefold()
        if lowered and folded.endswith(lowered) and len(folded) > len(lowered):
            folded = folded[: -len(lowered)]
            break  # one suffix only: predictable, and combined entries are allowed
    return directory.casefold(), folded


def group_companions(
    rel_paths: Iterable[str],
    sidecar_exts: tuple[str, ...] = ("xmp",),
    ignore_stem_suffixes: tuple[str, ...] = (),
) -> list[CompanionGroup]:
    """Deterministic grouping per design spec 5.4 - no heuristics beyond it."""
    sidecars = frozenset(sidecar_exts)
    companion_exts = RAW_EXTS | DISPLAY_EXTS | LIVE_VIDEO_EXTS | sidecars
    candidates = [rel for rel in rel_paths if _ext(rel) in companion_exts]

    buckets: dict[tuple[str, str], list[str]] = {}
    for rel in candidates:
        buckets.setdefault(stem_key(rel, ignore_stem_suffixes), []).append(rel)

    # spec 5.4 safety property 2: if ignoring a suffix would put two files with the
    # same extension in one group, the suffix is not ignored for them - they
    # re-bucket by exact stem. Ambiguity always degrades to the stricter rule, and a
    # legitimate pair beside an ambiguous sibling still groups normally.
    # spec 5.4 rev 6: the two key spaces are tagged so they cannot collide. An exact
    # re-key that lands on another bucket's STRIPPED key would bind two files
    # sharing neither an exact nor a stripped stem into one rename unit - the one
    # mistake this section exists to prevent.
    resolved: dict[tuple[str, str, str], list[str]] = {}
    for key, members in buckets.items():
        member_exts = [_ext(m) for m in members]
        if len(member_exts) != len(set(member_exts)):
            for rel in members:
                directory, stem = stem_key(rel)
                resolved.setdefault((directory, stem, "exact"), []).append(rel)
        else:
            resolved.setdefault((*key, "stripped"), []).extend(members)

    groups: list[CompanionGroup] = []
    for members in resolved.values():
        if len(members) < 2:
            continue
        exts = {_ext(m) for m in members}
        # spec 5.4 safety property 1, unconditional: no group may hold two members
        # with the same extension - both would want the identical new filename.
        if len(exts) != len(members):
            continue
        has_raw = bool(exts & RAW_EXTS)
        has_display = bool(exts & DISPLAY_EXTS)
        has_video = bool(exts & LIVE_VIDEO_EXTS)
        has_sidecar = bool(exts & sidecars)
        if has_raw and has_display:
            kind = "raw_jpeg"
        elif has_display and has_video:
            kind = "live_photo"
        elif has_sidecar and (has_raw or has_display or has_video):
            kind = "sidecar"
        else:
            continue
        members_sorted = tuple(sorted(members))
        # spec 5.4 rev 6: display > RAW > video > first member, and a sidecar is
        # NEVER a primary. A sidecar carries no capture row, so electing one names
        # the whole unit from nothing (a real IMG_5678.MOV + IMG_5678.AAE pair
        # became 'undated_001.*' with the video's recorded date discarded).
        # The `non_sidecar or list(members_sorted)` fallback DOES fire, and the
        # comment that used to say it could not was wrong (M5 open question 1,
        # reproduced at the review gate). `sidecar_exts` is user config: declare
        # `["heic","mov","aae"]` and every member of a live-photo group is a
        # sidecar, `non_sidecar` is empty, and the fallback elects `x.aae`.
        # `sidecar_exts = ["jpg","xmp"]` reaches it too. `pam.config` now REFUSES
        # any sidecar_exts overlapping DISPLAY_EXTS / RAW_EXTS / LIVE_VIDEO_EXTS,
        # so no loadable configuration reaches this line - but it is kept, because
        # this function is also called directly with an arbitrary tuple and the
        # expression must stay total rather than lean on a caller's validation.
        non_sidecar = [m for m in members_sorted if _ext(m) not in sidecars]
        primary = next(
            (m for m in non_sidecar if _ext(m) in DISPLAY_EXTS),
            next(
                (m for m in non_sidecar if _ext(m) in RAW_EXTS),
                next(
                    (m for m in non_sidecar if _ext(m) in LIVE_VIDEO_EXTS),
                    (non_sidecar or list(members_sorted))[0],
                ),
            ),
        )
        groups.append(
            CompanionGroup(
                kind=kind,
                primary_rel_path=primary,
                member_rel_paths=members_sorted,
            )
        )
    groups.sort(key=lambda g: g.member_rel_paths[0])
    return groups


def decide_groups(
    rel_paths: Iterable[str],
    known: Container[str],
    sidecar_exts: tuple[str, ...],
    ignore_stem_suffixes: tuple[str, ...],
) -> list[CompanionGroup]:
    """EVERY group a scan stores, decided from paths and config alone (M14 Task 5h, Z2).

    THE ONE DEFINITION, and pure: `pam scan`'s grouping block stores what this returns, and
    `pam plan` refuses while the stored grouping differs from it (record
    `2026-11-13-m14-r1-f1-amendment.md`, Z2: "A third copy is forbidden"). `known` is the set
    of paths the catalog holds a row for; a group keeps only its known members, and is dropped
    unless its primary is known and at least two members are.
    """
    decided: list[CompanionGroup] = []
    for group in group_companions(rel_paths, sidecar_exts, ignore_stem_suffixes):
        members = tuple(m for m in group.member_rel_paths if m in known)
        if group.primary_rel_path not in known or len(members) < 2:
            continue
        decided.append(CompanionGroup(group.kind, group.primary_rel_path, members))
    return decided
