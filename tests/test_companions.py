import pytest

from pam.scan.companions import CompanionGroup, group_companions, stem_key


def test_raw_jpeg_pair():
    groups = group_companions(["a/IMG_1.CR2", "a/IMG_1.JPG"])
    assert groups == [
        CompanionGroup(
            kind="raw_jpeg",
            primary_rel_path="a/IMG_1.JPG",
            member_rel_paths=("a/IMG_1.CR2", "a/IMG_1.JPG"),
        )
    ]


def test_live_photo_pair_primary_is_display():
    groups = group_companions(["a/IMG_2.heic", "a/IMG_2.mov"])
    assert groups[0].kind == "live_photo"
    assert groups[0].primary_rel_path == "a/IMG_2.heic"


def test_xmp_sidecar_with_jpg():
    groups = group_companions(["a/pic.jpg", "a/pic.xmp"])
    assert groups[0].kind == "sidecar"
    assert groups[0].primary_rel_path == "a/pic.jpg"


def test_xmp_sidecar_with_raw_only_primary_is_raw():
    groups = group_companions(["a/pic.nef", "a/pic.xmp"])
    assert groups[0].kind == "sidecar"
    assert groups[0].primary_rel_path == "a/pic.nef"


def test_raw_jpg_xmp_triple_is_raw_jpeg_with_three_members():
    groups = group_companions(["a/x.cr2", "a/x.jpg", "a/x.xmp"])
    assert groups[0].kind == "raw_jpeg"
    assert groups[0].member_rel_paths == ("a/x.cr2", "a/x.jpg", "a/x.xmp")
    assert groups[0].primary_rel_path == "a/x.jpg"


def test_case_insensitive_stems_group_together():
    groups = group_companions(["a/img_1.cr2", "a/IMG_1.jpg"])
    assert len(groups) == 1
    assert groups[0].kind == "raw_jpeg"


def test_same_stem_different_directories_do_not_group():
    assert group_companions(["a/IMG_1.cr2", "b/IMG_1.jpg"]) == []


def test_same_class_pair_does_not_group():
    assert group_companions(["a/x.jpg", "a/x.png"]) == []


def test_lone_files_and_unrelated_exts_do_not_group():
    assert group_companions(["a/x.jpg", "a/y.cr2", "a/x.txt"]) == []


def test_multiple_groups_sorted_deterministically():
    groups = group_companions(["z/b.jpg", "z/b.mov", "a/a.cr2", "a/a.jpg", "a/lone.jpg"])
    assert [g.kind for g in groups] == ["raw_jpeg", "live_photo"]


def test_full_quad_locks_kind_precedence_forever():
    # RAW + display + video + xmp: precedence is raw_jpeg > live_photo > sidecar.
    groups = group_companions(["a/IMG_1.JPG", "a/IMG_1.CR2", "a/IMG_1.MOV", "a/IMG_1.XMP"])
    assert len(groups) == 1
    group = groups[0]
    assert group.kind == "raw_jpeg"
    assert group.primary_rel_path == "a/IMG_1.JPG"
    assert group.member_rel_paths == (
        "a/IMG_1.CR2",
        "a/IMG_1.JPG",
        "a/IMG_1.MOV",
        "a/IMG_1.XMP",
    )


def test_aae_joins_its_stem_mates(default_sidecar_exts=("xmp", "aae")):
    # spec 5.4 rev 5: Apple's edit sidecar pairs by stem exactly as .xmp does.
    groups = group_companions(
        ["IMG_5830.HEIC", "IMG_5830.MOV", "IMG_5830.AAE"], default_sidecar_exts
    )
    assert len(groups) == 1
    assert groups[0].member_rel_paths == ("IMG_5830.AAE", "IMG_5830.HEIC", "IMG_5830.MOV")
    assert groups[0].primary_rel_path == "IMG_5830.HEIC"  # display image still primary


def test_a_lone_sidecar_is_not_a_group():
    assert group_companions(["orphan.AAE"], ("xmp", "aae")) == []


def test_raw_and_jpeg_group_when_the_stem_matches():
    # First real-data confirmation of the other spec 5.4 class (the M2 HT folder
    # was all live photos). Display image is primary even though the RAW sorts first.
    groups = group_companions(["DSC_3961.acr_.NEF", "DSC_3961.acr_.jpg"], ("xmp", "aae"))
    assert len(groups) == 1 and groups[0].kind == "raw_jpeg"
    assert groups[0].primary_rel_path == "DSC_3961.acr_.jpg"


def test_an_ignored_suffix_groups_a_raw_with_its_export():
    # C16: a real-world case. DSC_3579.NEF + its ACR export now share a stem
    # once '.acr_' is declared, so they rename as one unit.
    groups = group_companions(["DSC_3579.NEF", "DSC_3579.acr_.jpg"], ("xmp", "aae"), (".acr_",))
    assert len(groups) == 1 and groups[0].kind == "raw_jpeg"
    assert groups[0].member_rel_paths == ("DSC_3579.NEF", "DSC_3579.acr_.jpg")
    assert groups[0].primary_rel_path == "DSC_3579.acr_.jpg"


def test_ignored_suffix_matching_is_case_insensitive():
    assert len(group_companions(["a.NEF", "a.ACR_.jpg"], ("xmp", "aae"), (".acr_",))) == 1


def test_a_suffix_that_is_the_whole_stem_is_not_stripped():
    # Would otherwise produce an empty grouping key and bucket unrelated files.
    assert stem_key("acr_.jpg", (".acr_", "acr_")) == ("", "acr_")


def test_ambiguous_stripping_degrades_to_exact_stems():
    # spec 5.4 safety property 2, and the reason this feature needs a guard at all.
    # Ignoring both suffixes would put two NEFs in one group, and both would want
    # the identical new filename. The real 4-file cluster from a real-world library:
    cluster = [
        "DSC_3579 (1).NEF",
        "DSC_3579.NEF",
        "DSC_3579.acr_ (1).jpg",
        "DSC_3579.acr_.jpg",
    ]
    # With only '.acr_' ignored, exactly the unambiguous pair groups:
    groups = group_companions(cluster, ("xmp", "aae"), (".acr_",))
    assert [g.member_rel_paths for g in groups] == [("DSC_3579.NEF", "DSC_3579.acr_.jpg")]
    # Ignoring ' (1)' as well makes every stem collide -> no group at all.
    assert group_companions(cluster, ("xmp", "aae"), (".acr_", " (1)")) == []


def test_an_ambiguous_sibling_does_not_break_a_legitimate_pair():
    # The regression the naive guard would cause: IMG_1.jpg + IMG_1.cr2 must still
    # group even though IMG_1-edited.jpg makes the stripped bucket ambiguous.
    groups = group_companions(
        ["IMG_1.jpg", "IMG_1.cr2", "IMG_1-edited.jpg"], ("xmp", "aae"), ("-edited",)
    )
    assert [g.member_rel_paths for g in groups] == [("IMG_1.cr2", "IMG_1.jpg")]


@pytest.mark.parametrize(
    "ignore", [(), (".acr_",), (".acr_", " (1)"), ("-edited",), (".ACR_",)]
)
def test_no_group_ever_holds_two_members_with_one_extension(ignore):
    # spec 5.4 safety property 1, unconditional: two members sharing an extension
    # would both resolve to the identical new filename.
    paths = [
        "DSC_3579 (1).NEF",
        "DSC_3579.NEF",
        "DSC_3579.acr_ (1).jpg",
        "DSC_3579.acr_.jpg",
        "IMG_1.jpg",
        "IMG_1.cr2",
        "IMG_1-edited.jpg",
    ]
    for group in group_companions(paths, ("xmp", "aae"), ignore):
        exts = [m.rsplit(".", 1)[1].lower() for m in group.member_rel_paths]
        assert len(exts) == len(set(exts)), group.member_rel_paths


def test_a_video_with_only_an_aae_sidecar_elects_the_video():
    # spec 5.4 rev 6 / finding 6, measured on a real-world library: '.aae'
    # sorts before '.mov', so the pre-rev-6 'members_sorted[0]' fallback elected
    # the SIDECAR. A sidecar has no capture row, so the whole unit was then named
    # from nothing - IMG_5678.MOV + IMG_5678.AAE became 'undated_001.*' with the
    # video's recorded qt_utc date extracted correctly and then discarded.
    groups = group_companions(["IMG_5678.MOV", "IMG_5678.AAE"], ("xmp", "aae"))
    assert len(groups) == 1 and groups[0].kind == "sidecar"
    assert groups[0].primary_rel_path == "IMG_5678.MOV"


@pytest.mark.parametrize(
    "paths",
    [
        ["a/x.jpg", "a/x.xmp"],
        ["a/x.nef", "a/x.xmp"],
        ["a/x.mov", "a/x.aae"],
        ["a/x.mp4", "a/x.xmp"],
        ["a/x.heic", "a/x.mov", "a/x.aae"],
        ["a/x.cr2", "a/x.jpg", "a/x.xmp"],
        ["a/x.JPG", "a/x.CR2", "a/x.MOV", "a/x.XMP"],
        ["a/x.AAE", "a/x.MOV"],  # the sidecar sorts first AND is listed first
        ["a/x.aae", "a/x.nef"],
        ["a/x.aae", "a/x.xmp", "a/x.mov"],
    ],
)
def test_no_group_primary_is_ever_a_sidecar(paths):
    # spec 5.4 rev 6, stated as a property over every group shape in the suite: a
    # sidecar carries no facts, so electing one guarantees a name built from
    # nothing. This is the invariant behind finding 6, not just its one case.
    groups = group_companions(paths, ("xmp", "aae"))
    # Every case here is a single stem-mate cluster: pin the count too, not just
    # non-emptiness - a regression that collapsed or split the cluster would
    # otherwise still pass this loop.
    assert len(groups) == 1, paths
    for group in groups:
        primary_ext = group.primary_rel_path.rsplit(".", 1)[1].lower()
        assert primary_ext not in ("xmp", "aae"), group


@pytest.mark.parametrize(
    ("paths", "expected_primary"),
    [
        (["a/x.jpg", "a/x.cr2"], "a/x.cr2"),
        (["a/x.jpg", "a/x.mov"], "a/x.mov"),
    ],
)
def test_a_declared_sidecar_loses_the_primary_election_to_a_lower_class(
    paths, expected_primary
):
    # Mutation-gate finding, closed here. Removing the `not in sidecars` filter
    # left the whole suite green, because the display > RAW > video ordering
    # already covers finding 6's .MOV + .AAE case on its own. It is NOT redundant:
    # scan.sidecar_exts is free-form user config with no cross-check against the
    # class lists, so a user may declare an extension that is ALSO a display or
    # video extension. The ordering then hands the election straight back to the
    # sidecar - and spec 5.4 rev 6 says a sidecar is never primary "under any
    # circumstances", not "unless a class list disagrees".
    groups = group_companions(paths, ("xmp", "aae", "jpg"))
    assert len(groups) == 1
    assert groups[0].primary_rel_path == expected_primary


def test_an_exact_rekey_can_never_land_on_another_stems_stripped_key():
    # spec 5.4 rev 6 / finding 1, the minimal reproduction verbatim. Before the
    # namespace tags this produced a raw_jpeg group of 'A-edited-x.cr2' +
    # 'A-edited.jpg': the ambiguous bucket 'a' re-keyed 'A-edited.jpg' to its
    # EXACT stem 'a-edited', which collided with the STRIPPED key of
    # 'A-edited-x.cr2' in the same dict. Those two files share neither an exact
    # nor a stripped stem, and pam bound them into one rename unit - the single
    # mistake spec 5.4 exists to prevent. Needs two entries in the list to fire.
    assert (
        group_companions(
            ["A-edited-x.cr2", "A-edited.jpg", "A.jpg"], ("xmp", "aae"), ("-edited", "-x")
        )
        == []
    )


def test_the_unfavourable_ambiguity_example_is_pinned_as_designed():
    # spec 5.4 rev 6's own worked example, written down so nobody "fixes" it. All
    # three share the stripped stem 'dsc_3579' and two are .nef, so the bucket is
    # ambiguous and EVERY member degrades to its exact stem - including the
    # legitimate .NEF + .acr_.jpg pair the suffix was configured to create. That
    # is safety property 2 working: pam refuses to guess which of two
    # same-extension candidates is the real partner.
    cluster = ["DSC_3579.NEF", "DSC_3579.acr_.jpg", "DSC_3579 (1).NEF"]
    assert group_companions(cluster, ("xmp", "aae"), (".acr_", " (1)")) == []


def test_an_editor_suffix_defeats_grouping_and_that_is_intended():
    # Real ACR/Lightroom output: DSC_3579.NEF exports as DSC_3579.acr_.jpg. Same
    # shot, same capture second, DIFFERENT stems -> two units, two names. spec 5.4
    # forbids heuristics for a reason: guessing that '.acr_' is an editor suffix
    # would eventually bind unrelated files and rename them together, which is the
    # one mistake a renamer must never make. Pinned so nobody "fixes" this with
    # fuzzy stem matching. C16 makes it opt-in per-suffix instead, which is a
    # user declaration rather than a guess - see the test above.
    assert group_companions(["DSC_3579.NEF", "DSC_3579.acr_.jpg"], ("xmp", "aae")) == []
