"""`pam report` - skipped, duplicates and session (M6 Task 6; spec 8.1, 12; UX 1.4).

Everything here runs against an archive of REAL files on a real temp filesystem, scanned by the
real scanner through the fake exiftool. The six "why was this file not read" reasons
are DERIVED from the catalog rather than stored in it, so the ones that can be produced
by really failing to read a file are produced that way - a file exiftool returns nothing
for, and a file whose hashing raises - rather than by writing the catalog state the
report expects to find. A test that manufactures its own input can only confirm what
its author already believed about the scanner.

The two that cannot be produced from a real scan on this filesystem are made honestly
at the seam that produces them in life: a cloud placeholder is an NTFS attribute bit
(`walker.is_placeholder`), and the ordering rules between reasons are asserted against
`reason_for` directly, because an archive cannot hold a file that is missing AND a
placeholder at the same instant.
"""

import json
import sqlite3
from pathlib import Path

import pytest
from archives import FAKE_COMMAND, approve_and_apply, catalog, put

import pam.report as report_mod
import pam.report_duplicates as duplicates_mod
from pam.catalog import repo
from pam.catalog.db import connect_readonly
from pam.cli import main
from pam.errors import EXIT_EXPECTED, EXIT_OK
from pam.lock import WorkspaceLock
from pam.naming.planner import run_plan
from pam.paths import workspace_if_exists
from pam.report import (
    CLOUD_ONLY,
    MISSING,
    OTHER,
    PATH_CAP,
    SIDECAR,
    UNREAD,
    UNREADABLE,
    SessionReport,
    SkippedReport,
    reason_for,
    run_report_session,
    run_report_skipped,
    session_lines,
    skipped_lines,
)
from pam.report_duplicates import run_report_duplicates
from pam.scan.runner import SKIPPED_POINTER, ScanSummary, print_summary, run_scan
from pam.status import ArchiveStatus, next_steps, step_lines
from pam.webui import ReviewSummary, review_summary_lines
from pam.whereabouts import (
    BOTH,
    GONE,
    MOVED,
    Whereabouts,
    expected_and_other,
    where_is,
)

# One file's byte length, used as the stand-in for the NTFS attribute bit a real cloud
# placeholder carries. `is_placeholder` takes a stat result, not a path, so size is the
# only property of a file a test can see through that seam - and a deliberately odd
# length cannot collide with anything else the fixtures write.
CLOUD_SIZE = 4242


def output(args, capsys):
    """Run one command through `main`, returning (exit code, everything it printed).

    THROUGH `main`, for the reason `tests/test_status.py::output` gives: spec 13's
    exit-code mapping lives there and nowhere else, so a refusal driven any other way
    escapes as an exception and its message - which is most of what is asserted here -
    reaches no stream at all.
    """
    code = main(args)
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def scanned(tmp_path, names=("IMG_1.JPG",), extra=()):
    """An archive of real files, scanned. `extra` files are written with no tags sidecar."""
    root = tmp_path / "photos"
    root.mkdir()
    for name in names:
        put(root, name)
    for name, content in extra:
        (root / name).write_bytes(content)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    return root


def facts(root):
    ws = workspace_if_exists(root)
    assert ws is not None
    conn = connect_readonly(ws.db_path)
    try:
        return {f.rel_path: f for f in repo.report_facts(conn)}
    finally:
        conn.close()


def reasons(root):
    """rel_path -> reason, for every file the report would list. Read-only."""
    report = run_report_skipped(root)
    return {path: reason for reason, paths in report.by_reason.items() for path in paths}


# --- pam report skipped: the six reasons ----------------------------------------------


def test_skipped_derives_all_five_reasons_from_one_real_archive(tmp_path, capsys):
    """The whole point of the command: every file pam did not read, and why.

    `unreadable.jpg` is the fake exiftool's contract for "no record came back for this
    path"; `.tags.json` files are the fake exiftool's own sidecars and are typed `other`
    exactly as any unrecognised extension is, which is why they appear below rather than
    being filtered out of the assertion. Filtering them would be asserting against a
    tidier archive than the one on disk.
    """
    root = scanned(
        tmp_path,
        names=("IMG_1.JPG",),
        extra=(("unreadable.jpg", b"cannot be read"), ("notes.txt", b"a note")),
    )
    (root / "IMG_1.xmp").write_bytes(b"an xmp sidecar")
    (root / "gone.JPG").write_bytes(b"about to vanish")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    (root / "gone.JPG").unlink()
    run_scan(root, exiftool_command=FAKE_COMMAND)

    found = reasons(root)

    assert found["gone.JPG"] == MISSING
    assert found["unreadable.jpg"] == UNREAD
    assert found["IMG_1.xmp"] == SIDECAR
    assert found["notes.txt"] == OTHER
    assert found["IMG_1.JPG.tags.json"] == OTHER
    # The one file pam really did read is absent from the report entirely.
    assert "IMG_1.JPG" not in found

    code, text = output(["report", "skipped", "--archive", str(root)], capsys)
    assert code == EXIT_OK
    assert "unreadable.jpg" in text
    assert "gone.JPG" in text


def test_a_file_exiftool_returns_nothing_for_lands_in_could_not_read_and_nowhere_else(
    tmp_path,
):
    """Predicate 3, reproduced rather than assumed - half one.

    The scanner upserts a `capture` row even when exiftool returned no record for the
    path (`runner._scan` calls `upsert_capture` unconditionally), so "has a capture row"
    is NOT evidence pam read anything. The row it writes holds an empty `exif_json`, and
    that is the fact the report reads. A predicate that only checked for the row's
    EXISTENCE would report this file as read.
    """
    root = scanned(tmp_path, names=("IMG_1.JPG",), extra=(("unreadable.jpg", b"nope"),))

    fact = facts(root)["unreadable.jpg"]
    assert fact.has_hash, "the scanner still hashes a file exiftool could not read"
    assert not fact.has_metadata
    assert reason_for(fact) == UNREAD

    found = reasons(root)
    assert found["unreadable.jpg"] == UNREAD
    assert [p for p, r in found.items() if r == UNREAD] == ["unreadable.jpg"]


def test_a_file_whose_hashing_fails_lands_in_could_not_read_and_nowhere_else(
    tmp_path, monkeypatch
):
    """Predicate 3, reproduced rather than assumed - half two.

    A hashing failure is caught per file in `runner._scan` and leaves `files.sha256`
    NULL while the capture row is written normally. So the two halves of predicate 3 are
    genuinely independent: this file has metadata and no fingerprint, the file above has
    a fingerprint and no metadata, and either alone means pam did not fully read it.
    """
    import pam.scan.runner as runner

    real = runner.sha256_file

    def fail_for_one(path):
        if path.name == "IMG_2.JPG":
            raise OSError("the disk said no")
        return real(path)

    monkeypatch.setattr(runner, "sha256_file", fail_for_one)
    root = scanned(tmp_path, names=("IMG_1.JPG", "IMG_2.JPG"))

    fact = facts(root)["IMG_2.JPG"]
    assert not fact.has_hash
    assert fact.has_metadata, "exiftool succeeded; only the hashing failed"
    assert reason_for(fact) == UNREAD

    found = reasons(root)
    assert found["IMG_2.JPG"] == UNREAD
    assert "IMG_1.JPG" not in found


def test_a_video_pam_could_not_read_is_could_not_read_and_not_an_unknown_type(
    tmp_path, monkeypatch
):
    """Predicate 3's THIRD operand: the media types it covers, tested with a video.

    Every other predicate-3 test uses an image, so `("image", "video")` shrinking to
    `("image",)` changes nothing they assert - and the difference it makes is not a near
    miss. An unread video falls past the sidecar branch to `other`, whose printed
    explanation is "pam does not recognise these file types ... add their extensions to
    pam.toml": a wrong reason AND an instruction that cannot help, since `.mov` is
    already in the configured list. The video pam DID read lands there too, so a report
    about what pam did not read starts listing files it read perfectly well.

    Driven through a hashing failure rather than through the fake exiftool's no-record
    contract, which is keyed to the literal name `unreadable.jpg` and so cannot produce
    an unread VIDEO without widening shared test infrastructure.
    """
    import pam.scan.runner as runner

    real = runner.sha256_file

    def fail_for_the_video(path):
        if path.name == "IMG_2.MOV":
            raise OSError("the disk said no")
        return real(path)

    monkeypatch.setattr(runner, "sha256_file", fail_for_the_video)
    root = scanned(tmp_path, names=("IMG_1.MOV", "IMG_2.MOV"))

    fact = facts(root)["IMG_2.MOV"]
    assert fact.media_type == "video"
    assert not fact.has_hash and fact.has_metadata
    assert reason_for(fact) == UNREAD

    found = reasons(root)
    assert found["IMG_2.MOV"] == UNREAD
    assert "IMG_1.MOV" not in found


def test_a_cloud_placeholder_is_cloud_only_and_never_could_not_read(tmp_path, monkeypatch):
    """The ordering rule that matters most, because both files look identical afterwards.

    A placeholder is excluded from reading (spec 5.2), so it ends the scan with no
    fingerprint and no metadata - byte-identical, in the catalog, to a file pam tried to
    read and failed on. Only `files.placeholder` separates them, and testing it AFTER
    predicate 3 would report every cloud file in the archive as unreadable.
    """
    monkeypatch.setattr("pam.scan.walker.is_placeholder", lambda st: st.st_size == CLOUD_SIZE)
    root = scanned(tmp_path, names=("IMG_1.JPG",), extra=(("cloud.JPG", b"x" * CLOUD_SIZE),))

    fact = facts(root)["cloud.JPG"]
    assert fact.placeholder
    assert not fact.has_hash and not fact.has_metadata
    assert reason_for(fact) == CLOUD_ONLY
    assert reasons(root)["cloud.JPG"] == CLOUD_ONLY


def with_record(root, name, record):
    """One archive file plus the exact exiftool record the fake will return for it.

    The fake exiftool merges `<path>.tags.json` into what it emits, so this hands the
    scanner the record REAL exiftool returns for a file it cannot identify - measured
    with exiftool 13.59: an empty file comes back as `{"Error": "File is empty"}` with
    no `FileType` and no tags at all.
    """
    (root / name).write_bytes(f"the only copy of {name}".encode())
    (root / f"{name}.tags.json").write_text(json.dumps(record), encoding="utf-8")


def test_a_file_exiftool_could_not_identify_is_listed_and_says_what_to_do(tmp_path, capsys):
    """M7 Task 5, and the gap it closes: this file was in NO group before.

    THE FIRST TWO ASSERTIONS ARE THE POINT. A record carrying an `Error` key is still a
    record, so `exif_json` is not `{}` and `has_metadata` is TRUE; the bytes are readable
    even when the format is not, so `has_hash` is true as well. Predicate 3 therefore
    classified this file as one pam READ - it did not appear in `pam report skipped` at
    all, under any heading - while UX 1.4 promises the user that files pam could not read
    are "skipped and listed in the report". So the new reason is not a re-labelling of
    `unread`; without it there is nothing to re-label.
    """
    root = tmp_path / "photos"
    root.mkdir()
    put(root, "IMG_1.JPG")
    with_record(root, "empty.jpg", {"Error": "File is empty"})
    run_scan(root, exiftool_command=FAKE_COMMAND)

    fact = facts(root)["empty.jpg"]
    assert fact.has_hash and fact.has_metadata, (
        "an Error record is still a record - this is why `unread` cannot catch it"
    )
    assert fact.has_read_error
    assert reason_for(fact) == UNREADABLE
    assert reasons(root)["empty.jpg"] == UNREADABLE
    assert "IMG_1.JPG" not in reasons(root)

    code, text = output(["report", "skipped", "--archive", str(root)], capsys)

    assert code == EXIT_OK
    assert "empty.jpg" in text
    # UX 1.4's three parts, in order: what happened, what it means, what to do.
    assert "could not tell what they are" in text
    assert "no camera information at all" in text
    assert "Replace it" in text and "pam scan" in text
    # And the honest note: pam has not stopped renaming it, and the date it will use is
    # the worst one it has. A report that left this out would let the user believe the
    # file was excluded from plans.
    assert "least reliable date" in text


def test_a_warning_only_file_is_not_listed_as_unreadable(tmp_path):
    """The direction that would hurt, pinned on the report side too.

    About half of the capture rows in a real-world library carry an exiftool `Warning`
    and every one of those files got its date. If this group ever matched `Warning` as
    well as `Error`, half a real-world library would be listed under a heading saying pam
    could make nothing of it - while the same files are in his plans with real dates.
    """
    root = tmp_path / "photos"
    root.mkdir()
    with_record(root, "damaged.jpg", {"FileType": "JPEG", "Warning": "JPEG format error"})
    with_record(
        root,
        "chatty.MOV",
        {
            "CreateDate": "2023:08:28 11:59:43",
            "Warning": "[minor] The ExtractEmbedded option may find more tags",
        },
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)

    assert not facts(root)["damaged.jpg"].has_read_error
    assert not facts(root)["chatty.MOV"].has_read_error
    found = reasons(root)
    assert "damaged.jpg" not in found and "chatty.MOV" not in found
    assert UNREADABLE not in run_report_skipped(root).by_reason


@pytest.mark.parametrize(
    "blob",
    [
        "not json at all",
        '"an Error happened while this was copied"',
        "null",
        "[1, 2]",
    ],
)
def test_a_damaged_capture_blob_is_never_reported_as_a_photo_to_replace(tmp_path, blob):
    """`_has_read_error`'s two guards, which pam itself cannot reach (M7 review, M598a/M599a).

    pam writes every `exif_json` with `json.dumps` of an exiftool record, so a blob that
    does not parse - or that parses to something other than an object - means the CATALOG
    is damaged, not the photo. `pam verify` is the command with an opinion about that;
    this report must not tell the user to replace a file from backup over it.

    THE MIDDLE TWO ROWS ARE WHY THE `isinstance(record, dict)` OPERAND EXISTS. Without
    it, a blob that parses to a JSON string turns the key lookup into a SUBSTRING TEST -
    the mistake CLAUDE.md records this project making twice - and a blob that parses to
    `null` raises `TypeError`, taking the whole report down instead of one file. Planted
    with the suite's own SQL, because no scan can produce either state.
    """
    root = tmp_path / "photos"
    root.mkdir()
    put(root, "IMG_1.JPG")
    run_scan(root, exiftool_command=FAKE_COMMAND)

    conn = catalog(root)
    try:
        conn.execute("UPDATE capture SET exif_json = ?", (blob,))
        conn.commit()
    finally:
        conn.close()

    assert not facts(root)["IMG_1.JPG"].has_read_error
    assert "IMG_1.JPG" not in reasons(root)


def test_the_unreadable_group_survives_a_rescan_that_reads_nothing(tmp_path):
    """Where the standing record lives, and why `pam scan`'s exit code is not it.

    P2 zero-work means an unchanged file is never re-read, so the second `pam scan`
    records no error and exits 0 (`tests/test_scan_cli.py` pins that half). The catalog
    still holds exiftool's answer from the scan that DID read it, and this report is
    derived from the catalog - so the file stays listed until somebody replaces it.
    """
    root = tmp_path / "photos"
    root.mkdir()
    with_record(root, "empty.jpg", {"Error": "File is empty"})
    run_scan(root, exiftool_command=FAKE_COMMAND)
    second = run_scan(root, exiftool_command=FAKE_COMMAND)

    assert second.read == 0 and second.errors == []
    assert reasons(root)["empty.jpg"] == UNREADABLE


def test_unreadable_is_ranked_between_cloud_only_and_unread(tmp_path):
    """The placement decision, written down where it can fail.

    ORDER, and what each neighbour costs if it moves:

      * BELOW `missing` and `cloud-only`. A deleted file is gone first, and a cloud-only
        file was never opened this scan - pam did not try, so it cannot have failed. Both
        already carry a stale `Error` blob from whichever earlier scan did read them, so
        this is reachable rather than theoretical.
      * ABOVE `unread`. `unread` is documented as the RESIDUE - "pam has no fingerprint
        or no camera information and the reason is in the log". This one knows the
        reason, in exiftool's own words, so a specific answer must outrank the shrug.
      * ABOVE `sidecar` and `other`, following the rule the `REASONS` comment already
        states: those two are properties of the file's TYPE and are permanently true,
        while this describes what happened on a particular scan. Nothing reaches it that
        way today - neither type is ever read - but if a later milestone reads sidecars,
        "pam could not tell what this is" is the true reason and "kept with a photo" is
        not.

    Asserted against `reason_for` directly, for the reason the missing-outranks test
    gives: no archive can hold a file that is a placeholder AND has been read.
    """
    base = dict(rel_path="x.jpg", status="present", has_hash=True, has_metadata=True)

    cloud = repo.ReportFact(media_type="image", placeholder=True, has_read_error=True, **base)
    assert reason_for(cloud) == CLOUD_ONLY

    # No fingerprint AND an error: `unread`'s condition is satisfied too, so this pins
    # which of the two the user is told about.
    both = repo.ReportFact(
        rel_path="x.jpg",
        media_type="image",
        status="present",
        placeholder=False,
        has_hash=False,
        has_metadata=False,
        has_read_error=True,
    )
    assert reason_for(both) == UNREADABLE

    for media_type in ("sidecar", "other"):
        typed = repo.ReportFact(
            media_type=media_type, placeholder=False, has_read_error=True, **base
        )
        assert reason_for(typed) == UNREADABLE

    # And with the error gone, each neighbour is itself again - so the assertions above
    # are about the new operand and not about the fixtures.
    assert (
        reason_for(
            repo.ReportFact(
                media_type="sidecar", placeholder=False, has_read_error=False, **base
            )
        )
        == SIDECAR
    )
    assert (
        reason_for(
            repo.ReportFact(media_type="image", placeholder=False, has_read_error=False, **base)
        )
        is None
    )


def test_missing_outranks_every_other_reason(tmp_path):
    """A file that is not there any more cannot also be reported as a type problem.

    Asserted against `reason_for` directly: an archive cannot hold a file that is
    missing and a placeholder at the same instant, so the only way to pin the order is
    to hand the classifier a fact that is both. That is not a manufactured input - it is
    exactly the row a deleted placeholder leaves behind after the next scan.
    """
    for media_type in ("image", "sidecar", "other"):
        fact = repo.ReportFact(
            rel_path="gone",
            media_type=media_type,
            status="missing",
            placeholder=True,
            has_hash=True,
            has_metadata=True,
            # M7 Task 5: and the new reason is under it too. A file whose last scan
            # could make nothing of it, and which has since been deleted, is gone
            # first - telling the user to replace a file that is no longer there
            # would be a dead end (UX 1.4).
            has_read_error=True,
        )
        assert reason_for(fact) == MISSING


def test_a_file_pam_fully_read_has_no_reason_at_all(tmp_path):
    """The negative case, so the classifier cannot pass by returning a reason for
    everything. `run_report_skipped` counts total files separately for the same reason:
    "3 files, of 400" is checkable against `pam status` and "3 files" is not."""
    root = scanned(tmp_path, names=("IMG_1.JPG",))
    report = run_report_skipped(root)

    assert reason_for(facts(root)["IMG_1.JPG"]) is None
    assert "IMG_1.JPG" not in reasons(root)
    assert report.total_files == report.skipped + 1


def test_skipped_caps_the_listing_but_never_the_count(tmp_path, capsys):
    """A truncated count is a lie; a truncated list is an inconvenience (UX 2)."""
    extra = tuple((f"note_{i:03d}.txt", f"note {i}".encode()) for i in range(PATH_CAP + 5))
    root = scanned(tmp_path, names=("IMG_1.JPG",), extra=extra)

    report = run_report_skipped(root)
    # PATH_CAP + 5 `.txt` files, plus the fake exiftool's own `IMG_1.JPG.tags.json`,
    # which is an unrecognised extension exactly as they are.
    assert len(report.by_reason[OTHER]) == PATH_CAP + 6

    _, text = output(["report", "skipped", "--archive", str(root)], capsys)

    listed = [
        line
        for line in text.splitlines()
        if line.startswith("    ") and not line.strip().startswith("...")
    ]
    assert len(listed) == PATH_CAP
    assert "... and 6 more" in text
    # The count is stated in full even though the listing is not.
    assert f"{PATH_CAP + 6:,} files" in text


# M8 amendment B2, written out here from the amendment and from UX 3 rather than
# imported from any of the three modules that print it. B2's rule is that "two surfaces
# naming the same command must not describe it differently", and an expectation fetched
# from one of those surfaces could only ever say that surface agrees with itself.
PAM_PLAN_STEP = "Next: pam plan   (propose new names - renames nothing)"
# M9 Task 12. UX §3's scan row carries the parenthesis verbatim - "(add AI understanding
# first)" - and UX §3 rev 8 held the slot for it explicitly ("`or: pam enrich` is M9's
# line and is not printed yet"). Spelled from that block, not from `status.next_steps`,
# for the reason above the constant it sits beside.
PAM_ENRICH_STEP = "  or: pam enrich   (add AI understanding first)"


def test_every_surface_that_points_at_pam_plan_says_the_same_sentence(tmp_path, capsys):
    """B2's actual rule, which nothing enforced: the THREE surfaces, compared.

    M9 Task 5 converged the format and copied `pam/report.py`'s reason into `pam scan`,
    but each surface pins only itself, so the wording could still diverge with a green
    suite - and did, under mutation, at the source site (M1127/M1128).

    `pam status` and `pam scan` are driven; `pam report skipped` is asserted in
    `test_skipped_says_what_to_do_next_when_it_skipped_nothing` above. All three answer
    to the literal above, so a change to any one of them alone is a failure here.
    """
    scanned = ArchiveStatus(root=tmp_path / "photos", reachable=True, scanned=True)
    assert step_lines(next_steps(scanned)) == [PAM_PLAN_STEP, PAM_ENRICH_STEP]

    print_summary(ScanSummary(total=1, images=1, new=1, read=1))
    printed = capsys.readouterr().out.splitlines()
    assert PAM_PLAN_STEP in printed
    # M9 Task 12: the same rule applied to the command UX §3 rev 8 was holding the
    # `  or:` slot for. `pam scan` and `pam status` leave a user in the same state and
    # now print the same pair, so the second line is compared here too - and it is
    # asserted as the WHOLE block on `next_steps` above rather than by membership, so
    # dropping either half of the pair from that surface fails here.
    assert PAM_ENRICH_STEP in printed


# The other two commands B2 reaches, written out here the same way and for the same
# reason. `pam review` and `pam apply` are each named by THREE surfaces - `pam status`'s
# guide, `pam plan`'s closing block, and `pam review`'s own summary - and until M9
# Task 5's controller pass the third printed them with no reason at all, which is the
# format B2 retired. Spelled from the amendment, not fetched from `next_steps`.
PAM_REVIEW_WHY = "see & approve the plan"
PAM_APPLY_WHY = "renames only what you approve"


def test_every_surface_that_points_at_review_or_apply_says_the_same_sentence(tmp_path):
    """B2 across the second and third commands, including the surface it forgot.

    B2's measured table named `webui/__init__.py` x2 as drift, and no M9 task's file
    list reached them - Task 5's review raised it and the controller closed it. Asserted
    on the WHOLE line: a `startswith`/`in` pin cannot see a reason appended after the
    command, which is exactly how the reasonless form survived four milestones and how
    M1124 survived this task's own mutation pass.
    """
    plan_id = 41
    archive = ArchiveStatus(
        root=tmp_path / "photos", reachable=True, scanned=True, open_plan_ids=(plan_id,)
    )
    expected_review = f"Next: pam review {plan_id}   ({PAM_REVIEW_WHY})"
    expected_apply = f"  or: pam apply {plan_id}   ({PAM_APPLY_WHY})"

    assert step_lines(next_steps(archive)) == [expected_review, expected_apply]

    # `pam review`'s own closing block names ONE of the two, chosen by whether anything
    # was approved - so both branches are driven, and each must carry its reason.
    approved = review_summary_lines(ReviewSummary(plan_id=plan_id, approved=3))
    assert approved[-1] == f"Next: pam apply {plan_id}   ({PAM_APPLY_WHY})"

    nothing_approved = review_summary_lines(ReviewSummary(plan_id=plan_id, approved=0))
    assert nothing_approved[-1] == expected_review
    # The sentence that explains the branch survives beside it (UX 1.4: never a dead end).
    assert "Nothing is approved yet, so pam apply has nothing to do." in nothing_approved


def test_skipped_says_what_to_do_next_when_it_skipped_nothing(tmp_path):
    """UX 1.4: an empty report still says what to do next rather than printing nothing.

    Rendered from a constructed report rather than from a scan, because every archive
    the fake exiftool can build contains its own `.tags.json` sidecars and therefore
    always skips something. The branch is real - a genuine archive of nothing but
    readable JPEGs reaches it - and this is the only way to reach it here.
    """
    report = SkippedReport(archive_root=tmp_path / "photos", total_files=400)

    lines = skipped_lines(report)

    assert report.skipped == 0
    assert "0 files, of 400 pam knows about here." in lines[2]
    # M9 Task 5 review / FINDING 2: `any(line.startswith("Next:"))` was the whole
    # assertion, so this line could name ANY command with any reason and stay green -
    # measured: rewriting it to `Next: pam undo   (put those files back)` passed 2289
    # tests, on the branch UX 1.4 exists to keep off a dead end. The whole line now.
    assert PAM_PLAN_STEP in lines


# --- pam report duplicates ------------------------------------------------------------


def test_duplicates_groups_files_with_identical_bytes(tmp_path, capsys):
    root = tmp_path / "photos"
    root.mkdir()
    put(root, "IMG_1.JPG", content=b"the very same photo")
    put(root, "IMG_2.JPG", content=b"the very same photo")
    put(root, "IMG_3.JPG", content=b"a different photo")
    run_scan(root, exiftool_command=FAKE_COMMAND)

    report = run_report_duplicates(root)

    groups = {g.rel_paths for g in report.groups}
    assert ("IMG_1.JPG", "IMG_2.JPG") in groups
    assert not any("IMG_3.JPG" in g for g in groups)

    code, text = output(["report", "duplicates", "--archive", str(root)], capsys)
    assert code == EXIT_OK
    assert "IMG_1.JPG" in text and "IMG_2.JPG" in text
    assert "IMG_3.JPG" not in text


def test_duplicates_says_what_it_compared_whether_or_not_it_found_anything(tmp_path, capsys):
    """Vision principle 9: reporting a subset as if it were the whole is THE failure.

    Until M13 this pinned the disclaimer that look-alikes were "not found yet". H2 (M13 Task
    9) deleted that sentence, because pam now looks; what replaces it is the look-alike
    section and the coverage sentence - how many images could be compared, and that videos
    are not - said in BOTH branches. The disclaimer sat above the empty/populated split and
    a later edit could push it into one side (mutation M406); so can this.
    """
    root = scanned(tmp_path, names=("IMG_1.JPG",))

    _, text = output(["report", "duplicates", "--archive", str(root)], capsys)

    assert "byte-for-byte identical" in text
    assert "no identical files" in text
    assert "Look-alikes - photos that look the same" in text
    assert "0 of 1 images have one. Run pam enrich phash for the rest." in text
    assert "Videos are not compared. pam deletes nothing - this is a list for you." in text
    assert "not found yet" not in text and "later version" not in text

    dupes = tmp_path / "dupes"
    dupes.mkdir()
    put(dupes, "IMG_1.JPG", content=b"the very same photo")
    put(dupes, "IMG_2.JPG", content=b"the very same photo")
    run_scan(dupes, exiftool_command=FAKE_COMMAND)

    _, text = output(["report", "duplicates", "--archive", str(dupes)], capsys)

    assert "IMG_1.JPG" in text and "IMG_2.JPG" in text
    assert "byte-for-byte identical: 1 group, 2 files" in text
    assert "Look-alikes - photos that look the same" in text
    assert "0 of 2 images have one. Run pam enrich phash for the rest." in text
    assert "Videos are not compared." in text


def test_companion_members_are_not_duplicates_of_each_other(tmp_path):
    """A `.JPG` and its `.XMP` differ in bytes anyway, and sidecars are never hashed -
    but this is asserted rather than assumed, because a future change to WHAT gets
    hashed could make a companion group look like a pile of duplicates, and the user
    would be told their photo and its sidecar are the same file."""
    root = tmp_path / "photos"
    root.mkdir()
    put(root, "IMG_1.JPG")
    (root / "IMG_1.xmp").write_bytes(b"an xmp sidecar")
    run_scan(root, exiftool_command=FAKE_COMMAND)

    conn = catalog(root)
    try:
        membership = repo.companion_membership(conn)
    finally:
        conn.close()
    assert any("IMG_1.xmp" in members for _kind, members in membership), (
        "the fixture must really produce a companion group, or this proves nothing"
    )

    assert run_report_duplicates(root).groups == ()


def test_a_missing_file_is_never_paired_with_a_present_one(tmp_path):
    """Two rows sharing a digest are only duplicates if both files are really there.

    Pairing a deleted file with the one that survived would invite the user to choose
    between two photos when only one of them exists.
    """
    root = tmp_path / "photos"
    root.mkdir()
    put(root, "IMG_1.JPG", content=b"the very same photo")
    put(root, "IMG_2.JPG", content=b"the very same photo")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert len(run_report_duplicates(root).groups) == 1

    (root / "IMG_2.JPG").unlink()
    run_scan(root, exiftool_command=FAKE_COMMAND)

    assert run_report_duplicates(root).groups == ()


def test_a_missing_file_is_left_out_of_a_group_that_survives_without_it(tmp_path):
    """The other half of "`status = 'present'` on BOTH sides", which the pair above
    cannot reach: when a digest still has two live files, the deleted third must not be
    listed among them (mutation M401).

    With two files the outer filter is invisible - deleting one drops the digest below
    the subquery's `COUNT(*) > 1` and no group is produced either way. Three is the
    smallest archive that tells the two filters apart, and it is the case that does the
    damage: the group is real, the user is deciding which copy to keep, and one of the
    paths offered is a file that is not on the disk at all.
    """
    root = tmp_path / "photos"
    root.mkdir()
    for name in ("IMG_1.JPG", "IMG_2.JPG", "IMG_3.JPG"):
        put(root, name, content=b"the very same photo")
    run_scan(root, exiftool_command=FAKE_COMMAND)
    assert run_report_duplicates(root).groups[0].rel_paths == (
        "IMG_1.JPG",
        "IMG_2.JPG",
        "IMG_3.JPG",
    )

    (root / "IMG_3.JPG").unlink()
    run_scan(root, exiftool_command=FAKE_COMMAND)

    groups = run_report_duplicates(root).groups
    assert len(groups) == 1
    assert groups[0].rel_paths == ("IMG_1.JPG", "IMG_2.JPG")


# --- pam report session ---------------------------------------------------------------


# What `applied()` renames `IMG_1.JPG` to, from `archives.DATE`. Spelled out so a
# fixture change that moved the name breaks these tests loudly rather than making the
# "where are the files now" assertions below quietly vacuous.
APPLIED_NAME = "2019-07-04_001.JPG"
ORIGINAL_NAME = "IMG_1.JPG"


def applied(tmp_path):
    """An archive of real files, scanned, planned and applied. Returns (root, session_id)."""
    root = tmp_path / "photos"
    root.mkdir()
    put(root, ORIGINAL_NAME)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan = run_plan(root)
    assert plan.plan_id is not None
    summary = approve_and_apply(plan.plan_id, root, confirm=None)
    assert summary.session_id
    assert (root / APPLIED_NAME).exists(), "the fixture no longer produces this name"
    return root, summary.session_id


def undo_session_of(root, applied_session_id):
    """The id of the session `pam undo` opened for `applied_session_id`."""
    conn = catalog(root)
    try:
        others = [
            c.session_id for c in repo.sessions(conn) if c.session_id != applied_session_id
        ]
    finally:
        conn.close()
    assert len(others) == 1, "the undo must have opened a session of its own"
    return others[0]


def test_session_prints_the_stored_report_and_what_the_catalog_says_now(tmp_path, capsys):
    root, session_id = applied(tmp_path)

    code, text = output(["report", "session", session_id, "--archive", str(root)], capsys)

    assert code == EXIT_OK
    assert "1 renamed" in text, "the catalog half"
    assert f"# Renaming run {session_id}" in text, "the stored report, verbatim"
    assert "1 file(s) renamed" in text, "the stored report, verbatim"
    assert "Next:" in text


def test_session_says_when_the_run_has_since_been_undone(tmp_path, capsys):
    """The reason the catalog half exists at all: time passes after a report is written.

    The stored file still says "1 renamed" - it was true when it was written - so a
    command that printed only the file would tell a user their photos are under their
    new names when they are not.
    """
    root, session_id = applied(tmp_path)
    assert main(["undo", session_id, "--archive", str(root)]) == EXIT_OK
    capsys.readouterr()

    _, text = output(["report", "session", session_id, "--archive", str(root)], capsys)

    assert "already undone" in text


def test_session_reports_on_an_UNDO_session_in_the_words_undo_uses(tmp_path, capsys):
    """The verb branch, reached THROUGH the report view (M6 Task 6 review, M416).

    The test above reports on the *apply* session after an undo, so the branch that
    renders an undo session as "put back" is never reached from here - blanking it left
    only `pam status` and `pam undo`'s chooser failing, and this command untouched. It
    is a real path: after an undo, `pam status` names the undo session as the most
    recent one, and its id is the one a user then types.

    The sharing test below cannot cover this. It compares the report's description
    against `describe_session` of the same counts, so both sides move together under any
    mutation of the shared renderer: it pins that there is no THIRD copy, not that the
    one copy says the right thing here.
    """
    root, session_id = applied(tmp_path)
    assert main(["undo", session_id, "--archive", str(root)]) == EXIT_OK
    undo_id = undo_session_of(root, session_id)
    capsys.readouterr()

    code, text = output(["report", "session", undo_id, "--archive", str(root)], capsys)

    assert code == EXIT_OK
    assert "put back" in text
    assert "an undo cannot itself be undone" in text
    assert "renamed" not in text.split("Next:")[0], (
        "an undo session must not be described with the apply verb"
    )


def test_session_uses_the_one_shared_session_renderer(tmp_path):
    """`reporter.describe_session` is THE single implementation, shared with `pam undo`'s
    chooser and `pam status`. A third copy would let the three drift into calling one
    session three different things, which is the defect M6 Task 5 closed for the first
    two."""
    from pam.execute.reporter import describe_session

    root, session_id = applied(tmp_path)
    report = run_report_session(root, session_id)

    assert report.catalog_description == describe_session(report.counts)


def test_session_still_prints_the_catalog_half_with_no_stored_report(tmp_path, capsys):
    """A run whose report failed to write is exactly the run somebody comes looking for.

    Failing here would deny them the half that is still true, and the report is written
    best-effort AFTER the renames precisely so that its failure never takes a run down.
    """
    root, session_id = applied(tmp_path)
    ws = workspace_if_exists(root)
    assert ws is not None
    (ws.reports_dir / f"session-{session_id}.md").unlink()

    code, text = output(["report", "session", session_id, "--archive", str(root)], capsys)

    assert code == EXIT_OK
    assert "1 renamed" in text
    assert "not in the workspace" in text


def test_an_unknown_session_lists_the_ones_pam_does_have(tmp_path, capsys):
    """UX 1.4, in the shape `execute/undo.py::_unknown_session_message` already uses -
    the archive named, the sessions listed with their descriptions, one `Run:` line."""
    root, session_id = applied(tmp_path)

    code, text = output(
        ["report", "session", "s-19700101-000000", "--archive", str(root)], capsys
    )

    assert code == EXIT_EXPECTED
    assert "pam has no session s-19700101-000000" in text
    assert str(root) in text
    assert session_id in text and "1 renamed" in text
    assert "Run: pam report session <session id>" in text


def test_an_unknown_session_in_an_archive_with_no_sessions_says_what_to_do(tmp_path, capsys):
    root = scanned(tmp_path, names=("IMG_1.JPG",))

    code, text = output(
        ["report", "session", "s-19700101-000000", "--archive", str(root)], capsys
    )

    assert code == EXIT_EXPECTED
    assert "has not applied anything to this folder yet" in text
    assert "Run: pam plan, then pam apply <plan id>" in text


# --- pam report session: where the files are NOW (M7 Task 4, condition 6) --------------
#
# spec 7.0's closing paragraph names two states pam cannot produce and something outside
# pam can - a `done` row whose new path is gone, and one whose old path exists again -
# and rules that they are "reported for human attention and acted on never". spec 7.2
# reports them ONCE, on stderr, at the moment recovery meets them; nothing persisted
# them, and `pam verify` correctly says the catalog is clean, because the catalog IS
# clean. It is the archive that changed. These tests are the durable half.
#
# Every one of them reaches its state by renaming or deleting a real file OUTSIDE pam,
# which is exactly the user behaviour spec 7.0 calls legitimate. None of them writes a
# journal row by hand.

# spec 7.0 J2 spells the legal transitions as `intent -> done`, `intent -> failed` and
# `done -> reverted`, so a journal row is RESOLVED in exactly these three states. Spelled
# out here from the spec rather than imported from the production vocabulary: an oracle
# built from the definition it checks is a survivor waiting to happen, and this project
# has been bitten by it twice (`repo.open_plan` in M5, `repo.ENRICHERS` in M6).
SPEC_RESOLVED_STATES = ("done", "failed", "reverted")


def where(root, session_id):
    """kind -> the expected paths reported under it, for one session. Read-only."""
    report = run_report_session(root, session_id)
    found = {}
    for entry in report.whereabouts.elsewhere:
        found.setdefault(entry.kind, []).append(entry.expected)
    return found


def test_the_resolved_states_the_probe_reads_are_the_three_the_spec_names():
    """An independent second spelling of spec 7.0 J2's transition targets.

    If a state is dropped from the production tuple, every row in it silently stops
    being probed and the report goes on printing a reassuring count line - the shape of
    the `pam status` enricher defect M6 closed, one table over.
    """
    assert sorted(repo.RESOLVED_STATES) == sorted(SPEC_RESOLVED_STATES)
    assert "intent" not in repo.RESOLVED_STATES


@pytest.mark.parametrize(
    ("state", "expected", "other"),
    [
        # spec 7.0 J4: `done` means pam performed the rename, so the file is at the name
        # the row moved it TO.
        ("done", "new.JPG", "old.JPG"),
        # `failed` follows `intent` only: the move was journalled and did not happen, so
        # the file is still at the name it started under.
        ("failed", "old.JPG", "new.JPG"),
        # `reverted` follows `done`: the move happened and spec 7.3's revert put it back.
        ("reverted", "old.JPG", "new.JPG"),
    ],
)
def test_each_resolved_state_says_where_its_file_should_be(state, expected, other):
    """The direction table, read off spec 7.0 J2/J4 rather than off the code.

    It is the same table for both ops, and that is not a coincidence to be re-derived
    per op: `undo.py` builds its rows REVERSED (the journal's `new_rel_path` is where
    the file is now, its `old_rel_path` is where it goes back to), so `old -> new` is
    the direction of the move a row records whichever op wrote it.
    """
    row = repo.JournalRow(
        id=1,
        session_id="s-20260813-120000",
        plan_item_id=1,
        op=repo.OP_RENAME,
        old_rel_path="old.JPG",
        new_rel_path="new.JPG",
        size=1,
        mtime_ns=1,
        state=state,
        intent_at="2026-08-13T12:00:00",
        resolved_at="2026-08-13T12:00:01",
    )

    assert expected_and_other(row) == (expected, other)


def test_a_session_nobody_touched_reports_every_file_still_in_place(tmp_path, capsys):
    """The reassurance, and the case that must not cry wolf: every file of a human test's run."""
    root, session_id = applied(tmp_path)

    report = run_report_session(root, session_id)

    assert report.whereabouts == Whereabouts(in_place=1, elsewhere=(), unresolved=0)

    code, text = output(["report", "session", session_id, "--archive", str(root)], capsys)
    assert code == EXIT_OK
    assert "Still where this run left them: 1 of 1." in text
    assert "Not where this run left them" not in text


def test_a_file_renamed_outside_pam_is_named_with_both_of_its_names(tmp_path, capsys):
    """The state spec 7.0 calls legitimate user behaviour, made durable.

    `pam verify` is right to stay quiet about this - the catalog is self-consistent, and
    what changed is the archive - so this report is the only place a user can ask the
    question again after the console has scrolled away.
    """
    root, session_id = applied(tmp_path)
    # Put back under its old name by hand rather than by `pam undo` - which is the whole
    # distinction: an undo writes journal rows and this writes none, so pam's last record
    # of the file still says it is under the name the apply gave it.
    (root / APPLIED_NAME).rename(root / ORIGINAL_NAME)

    assert where(root, session_id) == {MOVED: [APPLIED_NAME]}

    code, text = output(["report", "session", session_id, "--archive", str(root)], capsys)
    assert code == EXIT_OK
    assert "Still where this run left them: 0 of 1." in text
    assert APPLIED_NAME in text.split("Still where")[1]
    assert "Run: pam scan" in text


def test_a_file_gone_from_both_names_is_named_and_pam_claims_no_more_than_it_knows(
    tmp_path, capsys
):
    root, session_id = applied(tmp_path)
    (root / APPLIED_NAME).rename(root / "somewhere-else.JPG")

    # Renamed to a THIRD name, not to the one it came from: pam has two names for this
    # file and neither is in the folder, so "pam cannot find it" is the whole of what it
    # may say. It must not claim the file is lost - `somewhere-else.JPG` is right there -
    # and it must not go looking for it, which is a directory walk this probe will not do.
    assert where(root, session_id) == {GONE: [APPLIED_NAME]}

    (root / "somewhere-else.JPG").unlink()

    assert where(root, session_id) == {GONE: [APPLIED_NAME]}

    _, text = output(["report", "session", session_id, "--archive", str(root)], capsys)
    assert "cannot find" in text
    assert APPLIED_NAME in text and ORIGINAL_NAME in text


def test_something_at_both_names_is_reported_and_never_guessed_at(tmp_path, capsys):
    """spec 7.0's own example, and the one recovery refuses to resolve.

    pam renamed the file; something then put a file back under the old name. pam cannot
    know which one the user wants, so the report names both and asks a human - it does
    not pick, and it does not stay quiet.
    """
    root, session_id = applied(tmp_path)
    (root / ORIGINAL_NAME).write_bytes(b"something else entirely")

    assert where(root, session_id) == {BOTH: [APPLIED_NAME]}

    _, text = output(["report", "session", session_id, "--archive", str(root)], capsys)
    assert APPLIED_NAME in text and ORIGINAL_NAME in text
    assert "keep the one you want" in text
    # Nothing moved: a report that repaired anything would be a report that writes.
    assert (root / APPLIED_NAME).exists() and (root / ORIGINAL_NAME).exists()


def test_an_undo_of_a_session_does_not_make_that_session_report_its_files_as_moved(
    tmp_path, capsys
):
    """THE false alarm this probe would otherwise raise on every archive ever undone.

    spec 7.0 J2 keeps the apply's `rename` rows `done` for ever, so reading each row's
    own state alone says "the new name should be there" long after `pam undo` legitimately
    put every file back. On a human test's session that is every file reported as interfered with
    on a perfectly healthy archive, directly under a catalog half that says "already
    undone" - and a report that cries wolf about every file is one nobody reads the day it
    is right. So the disk is compared against the LAST thing pam recorded about each file,
    not against the first.
    """
    root, session_id = applied(tmp_path)
    assert main(["undo", session_id, "--archive", str(root)]) == EXIT_OK
    capsys.readouterr()
    assert (root / ORIGINAL_NAME).exists(), "the undo must really have put the file back"

    report = run_report_session(root, session_id)

    assert report.whereabouts == Whereabouts(in_place=1, elsewhere=(), unresolved=0)

    _, text = output(["report", "session", session_id, "--archive", str(root)], capsys)
    assert "already undone" in text, "the catalog half still leads"
    assert "Still where this run left them: 1 of 1." in text


def test_the_undo_direction_of_the_probe_fires_too(tmp_path):
    """The mirrored case: an undo put the file back, and something moved it again.

    Reported against the UNDO session, whose rows run the other way round - so a probe
    that only understood an apply's direction would call this file in place.
    """
    root, session_id = applied(tmp_path)
    assert main(["undo", session_id, "--archive", str(root)]) == EXIT_OK
    undo_id = undo_session_of(root, session_id)
    (root / ORIGINAL_NAME).rename(root / APPLIED_NAME)

    assert where(root, undo_id) == {MOVED: [ORIGINAL_NAME]}
    # And the apply session says the same thing about the same file, because both are
    # governed by the same last record of where pam put it.
    assert where(root, session_id) == {MOVED: [ORIGINAL_NAME]}


def test_a_pair_differing_only_in_case_is_never_reported_as_two_files(tmp_path):
    """The stated limitation, asserted rather than left in a comment.

    Windows path lookup is case-insensitive, so for a case-only rename BOTH paths resolve
    to the one file and `Path.exists()` cannot tell them apart. Almost every item in
    a real-world library change only the extension's case, so a probe that read that
    as "a file is at both names" would report almost the whole archive as interfered
    with. The honest answer is the only one the filesystem supports: it is there, or it
    is not.
    """
    root = tmp_path / "photos"
    root.mkdir()
    (root / "IMG_1.JPG").write_bytes(b"one photo, one file")

    assert where_is(root, "IMG_1.JPG", "IMG_1.jpg") is None
    assert where_is(root, "IMG_1.jpg", "IMG_1.JPG") is None

    (root / "IMG_1.JPG").unlink()

    assert where_is(root, "IMG_1.JPG", "IMG_1.jpg") == GONE


@pytest.mark.parametrize(
    ("expected_there", "other_there", "answer"),
    [
        # Both names answer yes. On NTFS this is what ONE file looks like through a
        # case-only pair; on a case-sensitive filesystem it is genuinely two files, and
        # the answer is deliberately the same, because pam cannot tell those two
        # situations apart from here and must not report "a file is at both names" on
        # the strength of a guess about the host.
        (True, True, None),
        # The two rows NTFS CANNOT PRODUCE, and the whole reason this test exists. On a
        # case-sensitive filesystem a case-only rename shows up as exactly one of them,
        # and `where_is` answers "it is there" for both - the stated limitation, now
        # asserted rather than left in a comment.
        (True, False, None),
        (False, True, None),
        # Neither name answers: the one thing the filesystem CAN settle either way.
        (False, False, GONE),
    ],
)
def test_a_case_only_pair_is_answered_from_is_it_there_at_all_alone(
    monkeypatch, expected_there, other_there, answer
):
    """The half of the case branch that no filesystem in this suite can exercise.

    `where_is`'s docstring says the branch "keeps the answer the same on a
    case-SENSITIVE filesystem, where the two paths really are two files". Every other
    test of it runs on NTFS, where a case-only pair has `expected.exists() ==
    other.exists()` ALWAYS - so the two operands of `expected_there or other_there` are
    indistinguishable there, and `or` can be changed to `and` with the whole suite green
    (M569, reviewer's finding, closed here).

    The four filesystem answers are supplied directly, which is the only way to state
    the claim on this machine. It is not a re-spelling of the production expression: it
    is the table spec 7.0 implies for a pair pam cannot distinguish - three yeses and
    one no - written out, so `and`, an inverted test, or a swapped return each turn a
    row red.
    """
    present = {"IMG_1.JPG": expected_there, "IMG_1.jpg": other_there}
    # Scoped to this one fake root, with everything else falling through to the real
    # call: pytest reads files of its own while formatting a failure, so a blanket
    # replacement turns a red assertion into an INTERNALERROR and hides which row failed.
    real_exists = Path.exists
    root = Path("case-only-archive")

    def only_here(self):
        return present[self.name] if self.parent == root else real_exists(self)

    monkeypatch.setattr(Path, "exists", only_here)

    assert where_is(root, "IMG_1.JPG", "IMG_1.jpg") == answer


def test_rows_the_probe_cannot_read_yet_are_counted_rather_than_dropped(
    tmp_path, set_fault, capsys
):
    """A row still in `intent` says pam MEANT to rename and nothing about the disk (J3).

    `pam report` runs no recovery, on purpose (its module docstring gives the argument),
    so it is the one command that can meet an unresolved row. Guessing at it would be
    spec 7.2's forbidden guess in a read-only command; dropping it silently would make
    the count line describe a subset while reading as the whole, which is the failure
    vision principle 9 names. It is counted, said out loud, and pointed at the command
    that does repair.
    """
    root = tmp_path / "photos"
    root.mkdir()
    put(root, ORIGINAL_NAME)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    plan = run_plan(root)
    assert plan.plan_id is not None
    set_fault.arm("crash:after_intent:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan.plan_id, root, confirm=None)
    set_fault.next_invocation()
    capsys.readouterr()

    conn = catalog(root)
    try:
        session_id = repo.sessions(conn)[0].session_id
    finally:
        conn.close()
    report = run_report_session(root, session_id)

    assert report.whereabouts == Whereabouts(in_place=0, elsewhere=(), unresolved=1)

    _, text = output(["report", "session", session_id, "--archive", str(root)], capsys)
    assert "Run: pam verify" in text


# --- H3: the missing report, explained truthfully by op --------------------------------


def test_a_missing_report_for_a_RENAME_session_still_offers_the_interrupted_reason(
    tmp_path, capsys
):
    """There it is true: the report is written best-effort after the renames, so a run
    that died before its end really does leave the catalog half and no file."""
    root, session_id = applied(tmp_path)
    ws = workspace_if_exists(root)
    assert ws is not None
    (ws.reports_dir / f"session-{session_id}.md").unlink()

    _, text = output(["report", "session", session_id, "--archive", str(root)], capsys)

    assert "interrupted" in text
    assert str(ws.reports_dir / f"session-{session_id}.md") in text


def test_a_missing_report_for_an_UNDO_session_says_there_is_nothing_missing(tmp_path, capsys):
    """HT-M6 finding H3. `write_session_report` has exactly one call site, in `apply.py`,
    so an undo session's file will NEVER exist - and offering "the run was interrupted"
    as the explanation invents a failure for the designed case. The user reading it has
    just watched their files go back correctly.
    """
    root, session_id = applied(tmp_path)
    assert main(["undo", session_id, "--archive", str(root)]) == EXIT_OK
    undo_id = undo_session_of(root, session_id)
    capsys.readouterr()

    code, text = output(["report", "session", undo_id, "--archive", str(root)], capsys)

    assert code == EXIT_OK
    assert "interrupted" not in text
    assert "putting files back" in text
    # The catalog half is still printed, which is the half that was always true.
    assert "put back" in text


def test_a_session_holding_BOTH_ops_gets_the_interrupted_reason_not_the_undo_one(tmp_path):
    """The op branch's THIRD case, which `SessionCounts.ops` exists to keep distinct.

    `ops` is a tuple of the DISTINCT ops present rather than a single string precisely
    because a session holding two of them is catalog corruption, not an undo - so the
    branch must test the tuple for EQUALITY and not for membership. Both other H3 tests
    have single-op sessions and pass either way: relaxing `== (OP_UNDO_RENAME,)` to
    `OP_UNDO_RENAME in ...` left the whole suite green (M579, reviewer's finding, closed
    here).

    Built as a value rather than reached through a real run, because pam cannot produce
    this state: `apply.py` and `undo.py` each open their own session. What is under test
    is the branch's reading of the tuple, and the tuple is the input.
    """
    session_id = "s-20260813-120000"
    report = SessionReport(
        archive_root=tmp_path,
        session_id=session_id,
        counts=repo.SessionCounts(
            session_id=session_id,
            done=2,
            failed=0,
            reverted=0,
            intent=0,
            undone=0,
            ops=(repo.OP_RENAME, repo.OP_UNDO_RENAME),
        ),
        catalog_description="2 renamed",
        stored_report_path=tmp_path / f"session-{session_id}.md",
        stored_report=None,
        whereabouts=Whereabouts(in_place=2, elsewhere=(), unresolved=0),
    )

    text = "\n".join(session_lines(report))

    assert "interrupted" in text
    assert "putting files back" not in text


# --- the shape of the command ---------------------------------------------------------


def test_bare_pam_report_names_its_three_subjects_and_what_to_do_next(capsys):
    """UX 1.4: never a dead end. Not a flag dump - that is `pam report --help`."""
    code, text = output(["report"], capsys)

    assert code == EXIT_OK
    assert "pam report skipped" in text
    assert "pam report duplicates" in text
    assert "pam report session" in text
    assert "Next:" in text


@pytest.mark.parametrize(
    "argv",
    [
        ["report", "skipped"],
        ["report", "duplicates"],
        ["report", "session", "s-19700101-000000"],
    ],
)
def test_report_answers_while_another_pam_holds_the_workspace_lock(tmp_path, capsys, argv):
    """It takes NO lock, for `pam/status.py`'s reason: a read-only lookup that refused
    to answer during an apply would refuse exactly when a user most wants to look
    something up. Driven with the lock genuinely held, not by reading the source."""
    root = scanned(tmp_path, names=("IMG_1.JPG",))
    ws = workspace_if_exists(root)
    assert ws is not None

    with WorkspaceLock(ws.lock_path):
        code, text = output([*argv, "--archive", str(root)], capsys)

    # The session subcommand's archive has no sessions, so it refuses - on the session
    # id, never on the lock, which is what this asserts.
    assert "another pam" not in text.lower()
    assert code in (EXIT_OK, EXIT_EXPECTED)


@pytest.mark.parametrize(
    "argv",
    [["report", "skipped"], ["report", "duplicates"]],
)
def test_report_writes_nothing_at_all(tmp_path, capsys, argv):
    """No catalog write, no session log, no recovery. `pam/report.py`'s docstring says
    why; this is the assertion that keeps it true when somebody adds a fourth subject."""
    root, _session_id = applied(tmp_path)
    ws = workspace_if_exists(root)
    assert ws is not None

    before_db = ws.db_path.stat().st_mtime_ns
    before_logs = sorted(p.name for p in ws.logs_dir.iterdir())
    before_reports = sorted(p.name for p in ws.reports_dir.iterdir())

    code, _ = output([*argv, "--archive", str(root)], capsys)

    assert code == EXIT_OK
    assert ws.db_path.stat().st_mtime_ns == before_db
    assert sorted(p.name for p in ws.logs_dir.iterdir()) == before_logs
    assert sorted(p.name for p in ws.reports_dir.iterdir()) == before_reports


@pytest.mark.parametrize("subject", ["skipped", "duplicates", "session"])
def test_every_subject_opens_the_connection_it_reads_through_read_only(
    tmp_path, capsys, monkeypatch, subject
):
    """The read-only guarantee asserted about the connection `pam report` ACTUALLY builds.

    THE GAP THIS CLOSES, found by mutation (M421) - and it is M321's gap one command
    over, which is why it was worth running here at all. `test_report_writes_nothing_at_all`
    above compares the catalog's bytes and the two directory listings, and that passes
    for exactly as long as the three subjects happen not to write. Swapping
    `connect_readonly` for `connect` inside `pam/report.py` passed the ENTIRE suite -
    1169 tests - because a report only ever reads, so nothing it does can tell the two
    handles apart. That turns the module docstring's "writes nothing at all" back into a
    promise about today's queries, which is the thing it exists to stop being.

    So the probe runs on the REAL connection, from inside the run, while it is still
    open, and it is taken from whatever name the module binds - not from an import of
    `connect_readonly` here, which would go on returning a read-only handle under the
    mutation and prove nothing.

    `CREATE TABLE` rather than an `UPDATE`, because it attempts a write whatever the
    catalog happens to contain: an `UPDATE` matching no rows need never reach the disk,
    so it would make the probe depend on the fixture having the right rows in it.

    `probed` is asserted afterwards for the M355 reason: a patch that never fires would
    make this test pass by doing nothing at all. Measured, not assumed - pointing the
    patch at a name nothing calls fails all three parametrizations on that line (M423).
    """
    root, session_id = applied(tmp_path)
    probed = []
    # M13 Task 9 moved `duplicates` to its own module, which binds its own opener. M15 Task 8
    # (E1): the opener is the read-only choke point, `open_catalog_readonly`.
    module = duplicates_mod if subject == "duplicates" else report_mod
    opener = module.open_catalog_readonly

    def probe_then_return(db_path):
        conn = opener(db_path)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("CREATE TABLE probe_the_handle (x)")
        probed.append(conn)
        return conn

    monkeypatch.setattr(module, "open_catalog_readonly", probe_then_return)
    argv = ["report", subject]
    if subject == "session":
        argv.append(session_id)
    code, _ = output([*argv, "--archive", str(root)], capsys)

    assert code == EXIT_OK
    assert probed, "the report never opened a catalog - the probe proved nothing"


def test_the_scan_warning_box_points_at_a_command_that_really_exists(tmp_path, capsys):
    """UX 2's boxed warning ends "Details: pam report skipped", normative since M2 and
    unkept until the command shipped. A pointer pam offers must exist (M4 finding 12).

    The EXACT string pam printed is taken out of the warning box and driven through the
    CLI, rather than asserted as a substring: a substring match passes for a pointer at
    a command that was renamed, which is precisely the failure mode.
    """
    monkeypatch_target = "pam.scan.walker.detect_sync_root"
    from unittest import mock

    root = tmp_path / "photos"
    root.mkdir()
    put(root, "IMG_1.JPG")
    with mock.patch(monkeypatch_target, return_value="OneDrive"):
        code, text = output(["scan", str(root)], capsys)
    assert code == EXIT_OK

    pointers = [
        line.strip(" |").removeprefix("Details:").strip()
        for line in text.splitlines()
        if "Details:" in line
    ]
    assert pointers == [SKIPPED_POINTER], "the warning box lost its last line"

    code, _ = output([*pointers[0].split()[1:], "--archive", str(root)], capsys)
    assert code == EXIT_OK, "the command the warning box names does not resolve"


def test_report_refuses_a_folder_pam_has_not_scanned(tmp_path, capsys):
    """`workspace_if_exists`, never `workspace_for`: a report that made a workspace as a
    side effect would answer "0 files skipped" for a folder pam has never looked at."""
    unknown = tmp_path / "never-scanned"
    unknown.mkdir()

    code, text = output(["report", "skipped", "--archive", str(unknown)], capsys)

    assert code == EXIT_EXPECTED
    assert "has not scanned" in text
    assert "Run: pam scan" in text
    assert workspace_if_exists(unknown) is None, "the refusal created a workspace"
