import json
import sys
from pathlib import Path

from click.testing import CliRunner

import pam.scan.exiftool as exiftool_mod
from pam.cli import cli, main
from pam.errors import ExifToolError

FAKE = Path(__file__).parent / "fake_exiftool.py"
FAKE_COMMAND = [sys.executable, str(FAKE)]


def make_archive(tmp_path):
    root = tmp_path / "photos"
    (root / "2019").mkdir(parents=True)
    (root / "2019" / "IMG_0001.jpg").write_bytes(b"jpeg-bytes-1")
    (root / "2019" / "IMG_0001.cr2").write_bytes(b"raw-bytes-1")
    (root / "2019" / "IMG_0002.jpg").write_bytes(b"jpeg-bytes-2")
    (root / "2019" / "IMG_0002.jpg.tags.json").write_text(
        json.dumps({"DateTimeOriginal": "2019:07:04 18:23:11", "Make": "Canon"}),
        encoding="utf-8",
    )
    (root / "clip.mp4").write_bytes(b"video")
    (root / "notes.txt").write_bytes(b"text")
    return root


def test_cli_scan_happy_path(tmp_path, monkeypatch):
    root = make_archive(tmp_path)
    monkeypatch.setattr(exiftool_mod, "default_command", lambda: FAKE_COMMAND)
    result = CliRunner().invoke(cli, ["scan", str(root)])
    assert result.exit_code == 0
    assert "Scan complete." in result.output
    assert "6 files found" in result.output
    assert "4 files read" in result.output
    assert "Next:" in result.output


def test_sync_warning_box_printed(tmp_path, monkeypatch):
    root = make_archive(tmp_path)
    monkeypatch.setattr("pam.scan.walker.detect_sync_root", lambda p: "OneDrive")
    monkeypatch.setattr(exiftool_mod, "default_command", lambda: FAKE_COMMAND)
    result = CliRunner().invoke(cli, ["scan", str(root)])
    assert result.exit_code == 0
    assert "Warning" in result.output
    assert "OneDrive" in result.output


def test_cli_scan_bad_path_exits_1(tmp_path, capsys):
    assert main(["scan", str(tmp_path / "nope")]) == 1
    assert "not a folder" in capsys.readouterr().err


def test_cli_scan_points_at_pam_plan(tmp_path, monkeypatch):
    # Finding 11: the closing hint said "(Rename planning arrives in a later
    # update: pam plan)". It ships - M4 tagged it.
    #
    # M8 Task 3 / C2 (finding F3): this test used to pin "  Next: pam plan" -
    # the continuation label - which pinned the shipped deviation (two
    # `Next:` lines, the first naming `pam scan` itself) as the expectation
    # rather than catching it. Second time in two milestones a test has
    # pinned the wrong label (see m7.md's finding on the same pattern in
    # `pam review`'s closing block). UX §3 rev 8 is unambiguous: `pam scan`
    # closes with `Next: pam plan`, not `  or:`/`  Next:` - `pam enrich` is
    # M9's `  or:` line and does not print yet.
    root = make_archive(tmp_path)
    monkeypatch.setattr(exiftool_mod, "default_command", lambda: FAKE_COMMAND)
    result = CliRunner().invoke(cli, ["scan", str(root)])
    # M824 (review): those two operands pinned the two-SPACE continuation shape, which
    # was incidental to the old block rather than the defect. Reintroducing F3 itself -
    # a `Next:` label on the re-run reassurance, naming `pam scan` - left all 1870 tests
    # green. So the expectation is spelled out from UX §3 rev 8 instead of from the
    # string the old code happened to print: the block is exactly ONE label line, and
    # `  or:` belongs to `pam enrich`, which does not ship until M9.
    labels = [
        line
        for line in result.output.splitlines()
        if line.lstrip().startswith(("Next:", "or:"))
    ]
    #
    # M9 Task 5 / B2: the expectation is flipped, not the code corrected to meet it.
    # `["Next: pam plan"]` pinned the REASONLESS shape - the third of the three
    # treatments the M8 review measured - and B2 makes `Next: <cmd>   (<why>)` the one
    # format. The reason is spelled out here from UX §3 and from the sentence
    # `pam/report.py` already prints for this same command, NOT read back from
    # `status.next_steps`: an expectation built from the production constant would
    # accept any wording the code happened to emit.
    #
    # M9 TASK 12: THE `  or:` LINE ARRIVES, and the expectation is flipped a second
    # time. UX §3 rev 8 said `or: pam enrich` "is M9's line and is not printed yet";
    # `pam enrich` ships in this task, so the block is now UX §3's finished pair. Both
    # lines are spelled here from UX §3 - `pam plan`'s reason from rev 9's ruling that
    # `pam scan` uses `pam report`'s existing sentence, `pam enrich`'s from the
    # parenthesis UX §3's own scan row carries for it - and NOT from `status.next_steps`,
    # for the reason above. The list is compared whole, so DROPPING the `or:` line is as
    # much a failure as misspelling it: an `in` assertion would pass over a block that
    # had lost half of itself.
    assert labels == [
        "Next: pam plan   (propose new names - renames nothing)",
        "  or: pam enrich   (add AI understanding first)",
    ], labels
    assert "arrives in a later update" not in result.output


def test_cli_scan_with_an_unreadable_file_exits_one_and_still_summarizes(tmp_path, monkeypatch):
    # Finding 9 / spec 13 rev 6. This deliberately CHANGES pam scan, which has
    # exited 0 with errors since M2: scan, plan and apply must not disagree about
    # what an error means. The summary still prints in full - only the exit code
    # changes, so a script can tell a complete scan from one missing files.
    def unreadable(path):
        raise OSError("The file is in use by another process")

    root = make_archive(tmp_path)
    monkeypatch.setattr(exiftool_mod, "default_command", lambda: FAKE_COMMAND)
    monkeypatch.setattr("pam.scan.runner.sha256_file", unreadable)
    result = CliRunner().invoke(cli, ["scan", str(root)])
    assert result.exit_code == 1, result.output
    assert "Scan complete." in result.output
    assert "6 files found" in result.output
    # Finding 10: the '!' symbol, because colour is never the only signal (UX 2).
    assert "  ! 4 files could not be read - skipped and listed in the log." in result.output
    assert main(["scan", str(root)]) == 1  # and through main(), not just CliRunner


def test_cli_scan_missing_exiftool_exits_1(tmp_path, monkeypatch, capsys):
    root = tmp_path / "photos"
    root.mkdir()

    def no_exiftool():
        raise ExifToolError("exiftool was not found on PATH. Run: pam doctor")

    monkeypatch.setattr(exiftool_mod, "default_command", no_exiftool)
    assert main(["scan", str(root)]) == 1
    assert "pam doctor" in capsys.readouterr().err


def write_record(root, name, record):
    """One archive file plus the exact exiftool record the fake will return for it."""
    (root / name).write_bytes(f"the only copy of {name}".encode())
    (root / f"{name}.tags.json").write_text(json.dumps(record), encoding="utf-8")


def test_cli_scan_with_a_file_exiftool_could_not_identify_exits_one(tmp_path, monkeypatch):
    """M7 Task 5 / spec 13 rev 6, driven through the CLI rather than the summary object.

    The exit code is the half a script reads and the '!' line is the half a person
    reads, and neither is produced by `run_scan` - they are `pam/cli.py`'s mapping of
    `summary.had_errors` and `print_summary`'s branch. A test that only checked
    `summary.errors` would pass with either of them deleted.
    """
    root = make_archive(tmp_path)
    write_record(root, "empty.jpg", {"Error": "File is empty"})
    monkeypatch.setattr(exiftool_mod, "default_command", lambda: FAKE_COMMAND)

    result = CliRunner().invoke(cli, ["scan", str(root)])

    assert result.exit_code == 1, result.output
    assert "Scan complete." in result.output
    assert "  ! 1 files could not be read - skipped and listed in the log." in result.output

    # And through `main()`, not just CliRunner - a FRESH archive, because a re-scan of
    # this one reads nothing (see the test below).
    other = make_archive(tmp_path / "second")
    write_record(other, "zeroed.jpg", {"Error": "File header is all binary zeros"})
    assert main(["scan", str(other)]) == 1


def test_a_rescan_reads_nothing_and_so_reports_no_error_again(tmp_path, monkeypatch):
    """An exit code is a statement about THIS run, and `pam report skipped` is the record.

    P2 zero-work (spec 5.1): a file whose bytes and mtime have not changed and whose
    catalog row is complete is not re-read, so exiftool is never asked about it again
    and there is no new error to record. The file is still unidentifiable and pam still
    says so - in `pam report skipped`, which reads the catalog rather than the run
    (`tests/test_report.py` pins that half).

    Asserted rather than left implicit because the alternative reading - "a scan of an
    archive containing a broken file always exits 1" - is the one a person would guess,
    and a later change that made it true would silently cost the zero-work guarantee.
    """
    root = make_archive(tmp_path)
    write_record(root, "empty.jpg", {"Error": "File is empty"})
    monkeypatch.setattr(exiftool_mod, "default_command", lambda: FAKE_COMMAND)

    assert main(["scan", str(root)]) == 1
    assert main(["scan", str(root)]) == 0


def test_cli_scan_with_only_warnings_still_exits_zero(tmp_path, monkeypatch):
    """The regression this task must NOT ship.

    Half a real-world library (about half of its capture rows) carries an exiftool
    `Warning`, every one of them a video that got its date. If the scanner ever reads
    the `Warning` key the way it reads `Error`, an ordinary scan of an ordinary archive
    exits 1 and tells the user that half their videos could not be read. Pinned from
    the direction that hurts: exit 0, and the '!' line absent.
    """
    root = make_archive(tmp_path)
    write_record(root, "damaged.jpg", {"FileType": "JPEG", "Warning": "JPEG format error"})
    write_record(
        root,
        "clip.MOV",
        {
            "CreateDate": "2023:08:28 11:59:43",
            "Warning": "[minor] The ExtractEmbedded option may find more tags in the media data",
        },
    )
    monkeypatch.setattr(exiftool_mod, "default_command", lambda: FAKE_COMMAND)

    result = CliRunner().invoke(cli, ["scan", str(root)])

    assert result.exit_code == 0, result.output
    assert "could not be read" not in result.output
