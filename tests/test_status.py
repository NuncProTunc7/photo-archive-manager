"""`pam status`, the bare-`pam` guide and `pam forget` (M6 Task 5; spec 3, 12; UX 3).

Everything here runs against an archive of REAL files on a real temp filesystem, scanned by the
real scanner through the fake exiftool, exactly as the execute tests do. The three
commands share one engine, so the assertions are always pairs: what the engine computed
AND what the user is shown, because a status report that is right in a dataclass and
wrong on the screen has failed at the only thing it does.

`pam forget` owns the one safety property in this file - it removes a POINTER and never
a catalog (spec 7.0 J2: journal rows are never deleted, and deleting the file they live
in is the same thing by another route) - so its round trip is asserted end to end:
forget, re-scan, and read the old sessions back out of the catalog that survived.

Its implementation moved to `pam/forget.py` in M6 Task 6 (the reader/writer cut - see
that module's docstring). Its tests stayed here because they are driven through the CLI
rather than through the module, and because the three commands' shared subject is
"which folders does pam know about", which is what these fixtures build.
"""

import logging
import shutil
from pathlib import Path

import pytest
from archives import FAKE_COMMAND, approve_and_apply, approve_ids, catalog, planned, put
from ollama_stub import Reply, generate_reply, scripted

from pam import status
from pam.catalog import repo
from pam.cli import main
from pam.enrich.command import run_enrich
from pam.errors import PamError
from pam.execute.undo import run_undo
from pam.lock import WorkspaceLock
from pam.naming.planner import run_plan
from pam.paths import ARCHIVE_POINTER, known_archives, resolve_archive, workspace_if_exists
from pam.scan.runner import run_scan
from pam.webui import api
from pam.webui.api import ApiRequest
from pam.webui.server import ReviewServer


def scanned(tmp_path, name="photos", files=("IMG_1.JPG",)):
    """An archive of real files, scanned but not planned. Returns its root."""
    root = tmp_path / name
    root.mkdir()
    for filename in files:
        put(root, filename)
    run_scan(root, exiftool_command=FAKE_COMMAND)
    return root


def unreachable(tmp_path, name="gone"):
    """A scanned archive whose folder is then deleted: known, but not there any more.

    This is spec 3 rev 8's second class, and the only one that can be manufactured -
    an unplugged drive, a disconnected share and a deleted folder are indistinguishable
    to the filesystem, which is exactly why pam refuses to guess between them.
    """
    root = scanned(tmp_path, name=name)
    shutil.rmtree(root)
    assert not root.exists()
    return root


def output(args, capsys):
    """Run one command through `main`, returning (exit code, everything it printed).

    THROUGH `main`, not through `CliRunner().invoke(cli, ...)`, because spec 13's
    exit-code mapping lives in `main` and nowhere else: invoking the group directly
    lets a `PamError` escape as an exception, so a refusal's message - which is most of
    what these tests assert - never reaches any stream at all. Both streams are joined
    because UX 2 puts the log path on stderr and the report on stdout, and a test that
    read only one of them would be asserting against half the screen.
    """
    code = main(args)
    captured = capsys.readouterr()
    return code, captured.out + captured.err


# --- pam status: spec 12's three subjects -------------------------------------------


def test_status_covers_counts_enrichment_coverage_and_plans(tmp_path, capsys):
    """Spec 12: "archive summary: counts, enrichment coverage, plans"."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])

    code, text = output(["status", "--archive", str(root)], capsys)

    assert code == 0
    assert str(root) in text
    # Four files, not two: `put` writes the fake exiftool's `.tags.json` beside each
    # photo and the walker records those too, which is the truth about the folder.
    assert "4 files: 2 image, 2 other" in text
    assert "4 present, 0 missing, 0 cloud-only" in text
    assert "Last scanned 20" in text
    assert "1 open" in text
    assert f"Next: pam review {plan_id}" in text
    assert f"  or: pam apply {plan_id}" in text


def test_status_prints_every_enricher_at_zero_rather_than_omitting_them(tmp_path, capsys):
    """The plan's reason, verbatim: a missing row reads as "not applicable" and a 0
    reads as "not done yet". No enricher exists before M9, so all five must be 0 AND
    all five must be on the screen."""
    root = scanned(tmp_path)

    code, text = output(["status", "--archive", str(root)], capsys)

    assert code == 0
    assert "Enrichment coverage, of 2 files:" in text
    # SPEC 8.1's FIVE NAMES, WRITTEN OUT, and deliberately NOT read from
    # `repo.ENRICHERS` (M6 review, finding F2). Looping over the production tuple made
    # this test agree with any value of it: truncating `ENRICHERS` to `("caption",)`
    # deleted four enrichers from the coverage table and all 1238 tests stayed green.
    # An oracle built from the definition it checks cannot catch that definition being
    # wrong - the same ruling the M5 review made about `repo.open_plan`. The five names
    # are the spec's, so the spec is what the test spells.
    for enricher in ("caption", "faces", "ocr", "phash", "geocode"):
        assert f"{enricher} 0" in text, f"{enricher} is missing from the coverage rows"


def test_status_reports_the_coverage_an_enrich_run_actually_produced(tmp_path, capsys):
    """M9 Task 12, Step 3. The zeros above were all this could assert before `pam enrich`
    existed - and an all-zero row reads the same whether the numbers are computed or
    hard-coded.

    So this one enriches for real and then reads the screen, and the expectation is the
    FIVE NAMES SPELLED FROM DESIGN 8.1 with the one that ran carrying its count. Not
    `for e in repo.ENRICHERS`, which is M6 finding F2 exactly: that loop passed for any
    value of the tuple, and four of five enrichers could be deleted from `pam status`
    with 1238 tests green. Not `repo.enrichment_coverage(conn)` either, which is the
    same defect one layer down - an oracle built from the production query agrees with
    whatever the query returns.

    `phash` is the enricher driven because it is the one that reads pixels, so the count
    is evidence a picture was really decoded rather than that a row was inserted.
    """
    root = tmp_path / "photos"
    root.mkdir()
    picture = Path(__file__).resolve().parent / "tiny_library" / "phash_pattern_base.png"
    (root / "picture.png").write_bytes(picture.read_bytes())
    (root / "picture.png.tags.json").write_text(
        '{"DateTimeOriginal": "2019:07:04 18:23:11"}', encoding="utf-8"
    )
    run_scan(root, exiftool_command=FAKE_COMMAND)
    run_enrich("phash", root)

    code, text = output(["status", "--archive", str(root)], capsys)

    assert code == 0
    lines = text.splitlines()
    heading = next(i for i, line in enumerate(lines) if "Enrichment coverage" in line)
    coverage = lines[heading + 1]
    assert coverage.split() == [
        "caption",
        "0",
        "faces",
        "0",
        "ocr",
        "0",
        "phash",
        "1",
        "geocode",
        "0",
    ], coverage


def test_status_reports_the_coverage_a_CAPTION_run_actually_produced(tmp_path, capsys):
    """M10 Task 11 Step 4, and the second enricher to reach this row with a number in it.

    THE EXPECTATION IS SPELLED FROM THE SPEC, BY HAND, AND IT IS SPELLED TWICE OVER.
    Design 8.1 names five enrichers in a fixed order and design 12 asks `pam status` for
    "enrichment coverage"; that order and those names are what the row below is, written
    out rather than read from `repo.ENRICHERS`. `for e in repo.ENRICHERS: assert f"{e} 0"`
    is M6 finding F2 exactly - it passed for ANY value of that tuple, and four of five
    enrichers could be deleted from this surface with 1238 tests green - and
    `repo.enrichment_coverage(conn)` is the same defect one layer down.

    THE SECOND HALF THE PHASH TEST ABOVE COULD NOT ASSERT: `caption`'s fact KIND. The
    coverage row is computed through `_FACT_KINDS`, a map whose `geocode` row was WRONG
    for three milestones (it assumed the kind was spelled like the enricher; design 6.1's
    token and 6.4's rule both say `place`). `caption`'s kind is `caption` - design 8.1's
    own word - so this test is what stands between that row and the same class of guess.
    Driven through the whole command, against the stub Ollama, so what is counted is a
    fact a model answer really produced and not a row a fixture inserted.

    ONE PICTURE IS CAPTIONED AND ONE IS NOT. `phash` runs over both, so the two columns
    carry DIFFERENT numbers - a coverage query that counted files rather than facts of
    this kind, or that read the wrong column, would have to produce 2 for both.
    """
    with scripted(
        generate_reply("Birthday Cake", "A child blowing out candles."),
        Reply(status=500, body={"error": "the model is unavailable"}),
    ) as stub:
        root = tmp_path / "photos"
        root.mkdir()
        picture = Path(__file__).resolve().parent / "tiny_library" / "phash_pattern_base.png"
        for index, name in enumerate(("a_one.png", "b_two.png")):
            (root / name).write_bytes(picture.read_bytes()[:-1] + bytes([index]))
            (root / f"{name}.tags.json").write_text(
                '{"DateTimeOriginal": "2019:07:04 18:23:11"}', encoding="utf-8"
            )
        run_scan(root, exiftool_command=FAKE_COMMAND)
        ws = workspace_if_exists(root)
        assert ws is not None
        ws.config_path.write_text(
            f"[enrich.caption]\nenabled = true\nollama_port = {stub.port}\n",
            encoding="utf-8",
        )
        run_enrich("phash", root)
        run_enrich("caption", root)

    code, text = output(["status", "--archive", str(root)], capsys)

    assert code == 0
    lines = text.splitlines()
    heading = next(i for i, line in enumerate(lines) if "Enrichment coverage" in line)
    assert lines[heading + 1].split() == [
        "caption",
        "1",
        "faces",
        "0",
        "ocr",
        "0",
        "phash",
        "2",
        "geocode",
        "0",
    ], lines[heading + 1]


def test_status_lists_every_known_archive_when_not_told_which(tmp_path, capsys):
    """The one place a per-archive summary must not force a choice first.

    Spec 3's resolution REFUSES to auto-select with several archives known, and `pam
    status` is precisely the command a user runs to find out which is which - so it
    reads `known_archives()` rather than `resolve_archive(None)`.
    """
    first = scanned(tmp_path, name="one")
    second = scanned(tmp_path, name="two")
    with pytest.raises(PamError):
        resolve_archive(None)

    code, text = output(["status"], capsys)

    assert code == 0
    assert "pam knows about 2 photo folders." in text
    assert str(first) in text and str(second) in text


def test_status_shows_an_unreachable_archive_as_known_but_unreachable(tmp_path, capsys):
    """Spec 3 rev 8 / J9: shown, never omitted - it is the state `pam forget` is for,
    so the two have to be discoverable together."""
    root = unreachable(tmp_path)

    code, text = output(["status"], capsys)

    assert code == 0
    assert str(root) in text
    assert "! pam has scanned this folder, but cannot see it right now." in text
    assert f"Next: pam forget {root}" in text


def test_unreachable_beats_unscanned_in_the_next_step_chain(tmp_path, capsys):
    """Review M384. `next_steps` is first-match-wins, and its ORDER is the claim.

    Swapping the first two branches changes nothing for any other fixture here, because
    every unreachable archive in this file is also scanned. The state that tells the two
    orders apart is unreachable AND unscanned - a workspace whose catalog is gone or was
    never finished, which is the premise `pam verify` is built on - and there the wrong
    order prints `Next: pam scan <a folder pam cannot see>`, a command that can only
    refuse. UX 1.4: the step offered must be one that can be taken.
    """
    root = scanned(tmp_path, name="gone")
    ws = workspace_if_exists(root)
    assert ws is not None
    shutil.rmtree(root)
    ws.db_path.unlink()

    code, text = output(["status"], capsys)

    assert code == 0
    assert f"Next: pam forget {root}" in text
    assert f"pam scan {root}" not in text, "pam cannot scan a folder it cannot see"


def test_status_reports_the_counts_of_an_unreachable_archive_too(tmp_path, capsys):
    """Its workspace is on this machine even when its photos are not. Reporting it as
    an empty archive would tell the user pam had forgotten it."""
    unreachable(tmp_path)

    summary = status.collect_status(None)

    assert len(summary.archives) == 1
    assert summary.archives[0].scanned
    assert summary.archives[0].total_files == 2


def test_status_says_so_for_a_folder_it_has_never_scanned(tmp_path, capsys):
    """A typo'd `--archive` must never read as a clean archive (vision principle 9)."""
    never = tmp_path / "never-scanned"
    never.mkdir()

    code, text = output(["status", "--archive", str(never)], capsys)

    assert code == 0
    assert "pam has not read this folder yet." in text
    assert f"Next: pam scan {never}" in text


def test_status_refuses_an_archive_path_that_is_not_there(tmp_path, capsys):
    """Spec 3 rev 8: an explicit path pam cannot see is refused, not summarized."""
    code, text = output(["status", "--archive", str(tmp_path / "nowhere")], capsys)

    assert code == 1
    assert "pam cannot see this folder" in text


def test_status_runs_no_recovery_and_writes_nothing(tmp_path, set_fault, capsys):
    """The deliberate deviation from every other archive-scoped command, pinned.

    `pam status` reports on N archives at once and must never run spec 7.2's recovery
    against one it cannot see (J9), so it runs none at all and opens the catalog
    `mode=ro`. The proof is an `intent` row surviving the command: every other
    archive-scoped command would have resolved it, and a `pam status` that quietly
    started writing would resolve it too.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    set_fault.arm("crash:after_intent:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root, confirm=lambda ctx: True)
    set_fault.next_invocation()

    code, _ = output(["status", "--archive", str(root)], capsys)

    assert code == 0
    conn = catalog(root)
    try:
        assert len(repo.rows_in_state(conn, "intent")) == 1, (
            "pam status resolved a journal row, so it ran recovery or wrote to the catalog"
        )
    finally:
        conn.close()


def test_status_names_the_most_recent_session_and_whether_it_can_be_undone(tmp_path, capsys):
    """Spec 12's third subject. The verb comes from the journal `op`, never the counts."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG", "IMG_2.JPG"])
    applied = approve_and_apply(plan_id, root, confirm=lambda ctx: True)

    code, text = output(["status", "--archive", str(root)], capsys)
    assert code == 0
    assert f"Most recent session {applied.session_id}: 2 renamed" in text
    assert "it can still be undone." in text
    assert f"Next: pam undo {applied.session_id}" in text

    run_undo(applied.session_id, root)

    code, text = output(["status", "--archive", str(root)], capsys)
    assert code == 0
    assert "2 put back" in text, "an undo session must not be described as a rename"
    assert "an undo cannot itself be undone" in text
    assert "there is nothing here left to undo." in text
    assert "pam undo" not in text, "pam status offered an undo of an undo"


# --- M14 Y1: the open plan's approved count, the page's own number ------------------
#
# Every k and every N below is a LITERAL, spelled from the fixture (five photos, five
# items), and the approvals are written with the suite's own SQL - never through
# `webui.api.counts`, which is the definition under test.

FIVE = ("IMG_1.JPG", "IMG_2.JPG", "IMG_3.JPG", "IMG_4.JPG", "IMG_5.JPG")


def _five_planned(tmp_path):
    root = scanned(tmp_path, files=FIVE)
    summary = run_plan(root)
    assert summary.plan_id is not None
    return root, summary.plan_id


def _approve_first(root, plan_id, k):
    """Approve the first `k` items of one plan by id, with the suite's own SQL."""
    conn = catalog(root)
    try:
        ids = [
            int(r[0])
            for r in conn.execute(
                "SELECT id FROM plan_items WHERE plan_id = ? ORDER BY id LIMIT ?", (plan_id, k)
            )
        ]
        assert len(ids) == k
        approve_ids(conn, ids)
    finally:
        conn.close()


def test_status_counts_the_open_plans_approved_names(tmp_path, capsys):
    """Y1: `pam status` says how many of the open plan's names are approved."""
    root, plan_id = _five_planned(tmp_path)
    _approve_first(root, plan_id, 2)

    code, text = output(["status", "--archive", str(root)], capsys)

    assert code == 0
    assert f"  Plan {plan_id}: 2 of 5 names approved." in text.splitlines()
    assert status.collect_status(root).archives[0].open_plan_approved == (2, 5)


def test_the_approved_line_sits_under_the_plans_line_and_changes_no_other(tmp_path, capsys):
    """Y1: one line added, under `Plans:`; every other line is the one printed before."""
    root, plan_id = _five_planned(tmp_path)
    _approve_first(root, plan_id, 3)

    _, text = output(["status", "--archive", str(root)], capsys)
    lines = text.splitlines()

    at = lines.index("  Plans: 1 open")
    assert lines[at + 1] == f"  Plan {plan_id}: 3 of 5 names approved."
    assert lines[at + 2] == "  No renames have been applied here yet."
    assert sum("names approved" in line for line in lines) == 1


def test_status_prints_no_approved_line_without_an_open_plan(tmp_path, capsys):
    """Y1: no open plan, no line - scanned only, and after the plan was applied."""
    root = scanned(tmp_path, files=FIVE)
    _, text = output(["status", "--archive", str(root)], capsys)
    assert "names approved" not in text
    assert status.collect_status(root).archives[0].open_plan_approved is None

    plan_id = run_plan(root).plan_id
    assert plan_id is not None
    _approve_first(root, plan_id, 2)
    approve_and_apply(plan_id, root, confirm=lambda ctx: True)

    code, text = output(["status", "--archive", str(root)], capsys)
    assert code == 0
    assert "names approved" not in text, "an applied plan is not open"


def test_the_approved_count_is_the_open_plans_and_not_a_superseded_ones(tmp_path, capsys):
    """Y1: approvals left on a superseded plan are not the open plan's."""
    root, first = _five_planned(tmp_path)
    _approve_first(root, first, 4)
    second = run_plan(root).plan_id
    assert second is not None and second != first
    conn = catalog(root)
    try:
        assert repo.plan_status(conn, first) != "open"
        left = conn.execute(
            "SELECT COUNT(*) FROM plan_items WHERE plan_id = ? AND status = 'approved'",
            (first,),
        ).fetchone()[0]
    finally:
        conn.close()
    _approve_first(root, second, 1)

    _, text = output(["status", "--archive", str(root)], capsys)

    assert f"  Plan {second}: 1 of 5 names approved." in text.splitlines()
    # The superseded plan's rows are still there to be miscounted, or the case proves nothing.
    assert left == 4


def test_status_and_the_page_read_the_same_approved_count(tmp_path, capsys):
    """X3 / Y1: the console and `GET /api/plan/{id}` give one number, on one catalog."""
    root, plan_id = _five_planned(tmp_path)
    _approve_first(root, plan_id, 3)
    ws = workspace_if_exists(root)
    assert ws is not None
    logger = logging.getLogger("pam.tests.status_y1")
    logger.propagate = False
    server = ReviewServer(workspace=ws, plan_id=plan_id, logger=logger)
    try:
        page = api.plan_summary(server, ApiRequest(params={"id": str(plan_id)}, query={}))
    finally:
        server.server_close()

    _, text = output(["status", "--archive", str(root)], capsys)

    assert (page["approved"], page["total"]) == (3, 5)
    assert f"  Plan {plan_id}: 3 of 5 names approved." in text.splitlines()


def test_a_skipped_name_is_not_counted_as_approved(tmp_path, capsys):
    """Y1 (Task 9Y review, M3463): a skip is a decision, not an approval. Every other Y1 fixture
    decides by approving only, so a count of `approved + skipped` - "names decided" - was green
    against all of them. Two approved and one skipped, by the suite's own SQL: 2, not 3."""
    root, plan_id = _five_planned(tmp_path)
    _approve_first(root, plan_id, 2)
    conn = catalog(root)
    try:
        third = conn.execute(
            "SELECT id FROM plan_items WHERE plan_id = ? ORDER BY id LIMIT 1 OFFSET 2",
            (plan_id,),
        ).fetchone()[0]
        conn.execute("UPDATE plan_items SET status = 'skipped' WHERE id = ?", (third,))
        conn.commit()
        skipped = conn.execute(
            "SELECT COUNT(*) FROM plan_items WHERE plan_id = ? AND status = 'skipped'",
            (plan_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    # The skip is really there to be miscounted, or the case proves nothing.
    assert skipped == 1

    code, text = output(["status", "--archive", str(root)], capsys)

    assert code == 0
    assert f"  Plan {plan_id}: 2 of 5 names approved." in text.splitlines()


# AM1 (M14 review R9Y-F4): `pam status` reads one GROUP BY, and the property is "the same K and M
# the page's progress line shows". The table below is SPELLED BY HAND from design 12's sentence
# and AM1's - nine items, one status each - and both readers are checked against it, never one
# against the other.

EVERY_STATUS = ("IMG_1.JPG", "IMG_2.JPG", "IMG_3.JPG", "IMG_4.JPG", "IMG_5.JPG",
                "IMG_6.JPG", "IMG_7.JPG", "IMG_8.JPG", "IMG_9.JPG")  # fmt: skip
# item position (1-based, by id) -> status. 2 proposed, 3 approved, 1 skipped, 1 applied,
# 1 failed, 1 undone.
EVERY_STATUS_TABLE = {
    1: "proposed",
    2: "proposed",
    3: "approved",
    4: "approved",
    5: "approved",
    6: "skipped",
    7: "applied",
    8: "failed",
    9: "undone",
}
# What the progress line says of that plan: K = 3 approved, M = 9 total, 1 skipped, and 5 not
# approved-or-skipped ("remaining" is everything else, 2 + 1 + 1 + 1).
PAGE_WANTS = {"approved": 3, "total": 9, "skipped": 1, "remaining": 5}


def _every_status_plan(tmp_path):
    root = scanned(tmp_path, files=EVERY_STATUS)
    plan_id = run_plan(root).plan_id
    assert plan_id is not None
    conn = catalog(root)
    try:
        ids = [
            int(r[0])
            for r in conn.execute(
                "SELECT id FROM plan_items WHERE plan_id = ? ORDER BY id", (plan_id,)
            )
        ]
        assert len(ids) == 9
        for position, item_id in enumerate(ids, start=1):
            conn.execute(
                "UPDATE plan_items SET status = ? WHERE id = ?",
                (EVERY_STATUS_TABLE[position], item_id),
            )
        conn.commit()
    finally:
        conn.close()
    return root, plan_id


def test_status_and_the_page_footer_agree_with_a_hand_spelled_table_on_every_status(
    tmp_path, capsys
):
    """AM1: a plan holding EVERY status; the CLI's K and M and the page's counts equal the table."""
    root, plan_id = _every_status_plan(tmp_path)
    ws = workspace_if_exists(root)
    assert ws is not None
    logger = logging.getLogger("pam.tests.status_am1")
    logger.propagate = False
    server = ReviewServer(workspace=ws, plan_id=plan_id, logger=logger)
    try:
        page = api.plan_summary(server, ApiRequest(params={"id": str(plan_id)}, query={}))
    finally:
        server.server_close()

    code, text = output(["status", "--archive", str(root)], capsys)

    assert code == 0
    assert f"  Plan {plan_id}: 3 of 9 names approved." in text.splitlines()
    assert status.collect_status(root).archives[0].open_plan_approved == (3, 9)
    for key, wanted in PAGE_WANTS.items():
        assert page[key] == wanted, (key, page)


def test_status_does_not_load_the_plans_rows(tmp_path, capsys, monkeypatch):
    """AM1's cost half: `pam status` must not read `repo.plan_rows` (4.67 s / 383 MB at 100k)."""
    root, plan_id = _every_status_plan(tmp_path)

    def refuse(*args, **kwargs):
        raise AssertionError("pam status loaded the whole plan through repo.plan_rows")

    monkeypatch.setattr(repo, "plan_rows", refuse)
    code, text = output(["status", "--archive", str(root)], capsys)

    assert code == 0
    assert f"  Plan {plan_id}: 3 of 9 names approved." in text.splitlines()


# --- bare `pam`: one test per state (UX 3) ------------------------------------------


def test_guide_with_no_archives_points_at_scan(capsys):
    code, text = output([], capsys)

    assert code == 0
    assert "pam has not scanned any photo folder yet." in text
    assert "Next: pam scan <your photo folder>" in text


def test_the_first_run_guide_carries_the_banner_and_pam_status_does_not(capsys):
    """HT-M6 finding H5, asserted in BOTH directions.

    The banner sat on `guide_lines`'s populated branch alone, so the one state where the
    reader has never run pam - and has the least context anyone using pam ever has - was
    the one state that never said nothing is renamed without their approval.

    The other direction matters just as much, and is why the fix is not inside
    `_no_archives_lines`. That helper is shared with `pam status` so the two views cannot
    describe the state differently; `pam status` is a REPORT and prints no banner in any
    state, so a fix pushed down into the helper would have made it print one here and
    nowhere else. The shared state description is asserted on both, so the sharing is
    proven still intact rather than assumed.
    """
    guide_code, guide = output([], capsys)
    status_code, report = output(["status"], capsys)

    assert (guide_code, status_code) == (0, 0)
    assert status.GUIDE_BANNER in guide
    assert status.GUIDE_BANNER not in report, "pam status grew a banner"
    assert "pam has not scanned any photo folder yet." in guide
    assert "pam has not scanned any photo folder yet." in report


def test_every_guide_state_carries_the_banner(tmp_path, capsys):
    """One banner, every state. The first-run state is the one that lost it, but a fix
    that only ever runs on that branch would be as easy to lose again - so the promise
    is driven across the states the section below tests one at a time."""
    _first_run_code, first_run = output([], capsys)
    scanned(tmp_path)
    _scanned_code, after_scan = output([], capsys)

    assert status.GUIDE_BANNER in first_run
    assert status.GUIDE_BANNER in after_scan


def test_the_banner_makes_the_approval_promise_in_words(tmp_path, capsys):
    """The M7 Task 2 review's finding (mutation M542), and the half `GUIDE_BANNER`
    cannot assert about itself.

    Every other test here asks whether `status.GUIDE_BANNER` is on the screen, which is
    true whatever that constant says: gut it to `"pam - Photo Archive Manager."` and the
    banner still appears, in both states, with the whole suite green - the promise vision
    principle 9 and UX 1 are built on, deleted from the first thing a new user reads, and
    nothing fails. That is the `repo.ENRICHERS` shape M6 closed and the reason CLAUDE.md
    says to spell an expectation from the SPEC rather than from the production symbol.

    So the words are written out here, in both states, and deliberately not built from
    the constant. `pam` is the one screen that has to state what pam will not do without
    being asked; a reworded, weaker or absent promise is a product change, and it should
    have to come through this line.
    """
    _first_run_code, first_run = output([], capsys)
    scanned(tmp_path)
    capsys.readouterr()  # the scan's own progress, which is not part of the next screen
    _scanned_code, after_scan = output([], capsys)

    promise = "pam - Photo Archive Manager. Nothing is renamed without your approval."
    assert first_run.splitlines()[0] == promise
    assert after_scan.splitlines()[0] == promise


def test_guide_after_a_scan_points_at_plan(tmp_path, capsys):
    root = scanned(tmp_path)

    code, text = output([], capsys)

    assert code == 0
    assert str(root) in text
    assert "2 files, last scanned" in text
    assert "Next: pam plan" in text


def test_guide_with_an_open_plan_points_at_review_then_apply(tmp_path, capsys):
    """M7 Task 13: UX 3's chain gained its middle link, so the open-plan state offers
    two steps and the ORDER is the claim. HT-M6 verified the guide moves the user to
    apply; M7 inserts review ahead of it without dropping it."""
    _, plan_id = planned(tmp_path, ["IMG_1.JPG"])

    code, text = output([], capsys)

    assert code == 0
    assert f"Next: pam review {plan_id}   (see & approve the plan)" in text
    assert f"  or: pam apply {plan_id}   (renames only what you approve)" in text
    assert text.index(f"Next: pam review {plan_id}") < text.index(f"  or: pam apply {plan_id}")


def test_guide_after_an_apply_points_at_undo_and_scan(tmp_path, capsys):
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    applied = approve_and_apply(plan_id, root, confirm=lambda ctx: True)

    code, text = output([], capsys)

    assert code == 0
    assert f"Next: pam undo {applied.session_id}" in text
    assert "  or: pam scan" in text


def test_guide_with_an_unreachable_archive_points_at_forget_or_reconnecting(tmp_path, capsys):
    root = unreachable(tmp_path)

    code, text = output([], capsys)

    assert code == 0
    assert "! pam cannot see this folder right now" in text
    assert f"Next: pam forget {root}" in text
    # M9 Task 5 review / FINDING 1: the WHOLE line, terminated. Every assertion about
    # this step used to be a prefix, and a prefix cannot see what is appended after it -
    # dropping `step_lines`' `if step.why` conditional rendered `... again   ()` and all
    # 2288 tests passed. This is the only state UX 3 gives a reasonless step to.
    assert "  or: connect the drive or network folder, then run pam status again\n" in text


def test_step_lines_renders_a_reasonless_step_with_NOTHING_after_the_text():
    """B2's format, both branches, spelled out from UX 3 rather than from the renderer.

    `NextStep.why` defaults to empty and UX 1.4's prose step ("connect the drive") is the
    one place that default is used in production, so the branch has exactly one live
    caller and is the easy half to lose. `==` on the whole list, not `in` on a prefix.
    """
    assert status.step_lines(
        (
            status.NextStep("pam forget C:\\gone", "if this folder is gone for good"),
            status.NextStep("connect the drive or network folder, then run pam status again"),
        )
    ) == [
        "Next: pam forget C:\\gone   (if this folder is gone for good)",
        "  or: connect the drive or network folder, then run pam status again",
    ]


def test_the_guide_is_not_a_flag_dump_and_names_help(tmp_path, capsys):
    """UX 3 draws the line explicitly: the flag dump is `pam --help`."""
    scanned(tmp_path)

    code, text = output([], capsys)

    assert code == 0
    assert "Usage:" not in text and "--version" not in text
    assert "pam --help" in text


def test_the_guide_and_the_full_report_render_the_same_state(tmp_path, capsys):
    """M6 plan clarification 6: one engine, two views. Both call `next_steps`, so the
    command each recommends cannot differ - which is the drift the clarification names.
    """
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    summary = status.collect_status(None)

    guide = "\n".join(status.guide_lines(summary))
    report = "\n".join(status.status_lines(summary))

    assert f"Next: pam review {plan_id}" in guide
    assert f"Next: pam review {plan_id}" in report
    assert f"  or: pam apply {plan_id}" in guide
    assert f"  or: pam apply {plan_id}" in report


# --- pam forget ---------------------------------------------------------------------


def test_forget_removes_the_pointer_and_keeps_the_catalog(tmp_path, capsys):
    """Clarification 5: a pointer, never a workspace, and never the archive."""
    root = scanned(tmp_path)
    ws = workspace_if_exists(root)
    assert ws is not None

    code, text = output(["forget", str(root)], capsys)

    assert code == 0
    assert known_archives() == []
    assert not (ws.root / ARCHIVE_POINTER).exists()
    assert ws.db_path.exists(), "pam forget deleted the catalog"
    assert (root / "IMG_1.JPG").exists(), "pam forget touched the archive"
    assert f"pam has forgotten {root}." in text


def test_forget_prints_what_it_removed_and_what_it_kept(tmp_path, capsys):
    """A command called "forget" must be unambiguous about what it did NOT forget."""
    root = scanned(tmp_path)
    ws = workspace_if_exists(root)
    assert ws is not None

    _, text = output(["forget", str(root)], capsys)

    assert "Removed: pam's pointer to this folder" in text
    assert "Kept:" in text and "the rename journal" in text
    assert str(ws.root) in text
    assert f"Kept:    every file in {root}" in text
    assert f"Next: pam scan {root}" in text


def test_forget_then_rescan_restores_the_pointer_with_the_history_intact(tmp_path, capsys):
    """The round trip IS the promise (plan Step 3). The old sessions must survive."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    applied = approve_and_apply(plan_id, root, confirm=lambda ctx: True)

    output(["forget", str(root)], capsys)
    assert known_archives() == []

    run_scan(root, exiftool_command=FAKE_COMMAND)

    assert [a.path for a in known_archives()] == [root]
    conn = catalog(root)
    try:
        assert [s.session_id for s in repo.sessions(conn)] == [applied.session_id]
    finally:
        conn.close()


def test_forget_refuses_an_archive_with_unfinished_renames(tmp_path, set_fault, capsys):
    """The safety property. Forgetting the pointer mid-repair hides the repair, and the
    journal is the only record that those renames were ever started (spec 7.0 J2)."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    set_fault.arm("crash:after_intent:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root, confirm=lambda ctx: True)
    set_fault.next_invocation()

    code, text = output(["forget", str(root)], capsys)

    assert code == 1
    assert "renames it has not finished" in text
    assert "1 unfinished rename(s):" in text
    assert "IMG_1.JPG -> 2019-07-04_001.JPG" in text
    assert "Run any pam command against it first" in text
    assert [a.path for a in known_archives()] == [root], "the pointer must still be there"


def test_forget_refusing_leaves_the_pointer_and_the_journal_row_alone(
    tmp_path, set_fault, capsys
):
    """The refusal must change nothing: neither the pointer it declined to remove nor
    the `intent` row it declined to resolve. `pam forget` runs no recovery."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    set_fault.arm("crash:after_intent:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root, confirm=lambda ctx: True)
    set_fault.next_invocation()

    code, _ = output(["forget", str(root)], capsys)

    assert code == 1
    assert [a.path for a in known_archives()] == [root]
    conn = catalog(root)
    try:
        assert len(repo.rows_in_state(conn, "intent")) == 1
    finally:
        conn.close()


def test_forget_refuses_while_another_pam_holds_the_workspace_lock(tmp_path, capsys):
    """Review M370. The lock is the whole reason the check and the deletion are safe.

    `_refuse_if_mid_repair` reads the journal and `forget_archive` then removes the
    pointer; without the lock an apply may write `intent` rows into the window between
    them, and the pointer every later command uses to FIND this archive is gone while
    spec 7.2 has renames to resolve. That is exactly the state the refusal exists to
    prevent, reached by a route the refusal cannot see.

    A race is not directly assertable, so what is asserted is the mechanism: while a
    lock is held, `pam forget` refuses and changes nothing.
    """
    root = scanned(tmp_path)
    ws = workspace_if_exists(root)
    assert ws is not None

    with WorkspaceLock(ws.lock_path):
        code, text = output(["forget", str(root)], capsys)

    assert code == 1
    assert "another pam" in text.lower() or "running" in text.lower()
    assert [a.path for a in known_archives()] == [root], "the pointer must still be there"
    assert ws.db_path.exists()


def test_forget_reads_the_journal_of_THIS_archive_and_not_of_another(
    tmp_path, set_fault, capsys
):
    """Review M369. The refusal must be keyed to the archive being forgotten.

    Every other mid-repair test here holds exactly ONE archive, so a `pam forget` that
    found its catalog by scanning PAM_HOME instead of by following the pointer would be
    reading the right file by accident in all of them - and with two archives it reads
    whichever workspace slug happens to sort first, which is a hash of the path.

    Both directions are asserted, and that is what makes the test order-independent: a
    single shared catalog is either the mid-repair one (and the CLEAN archive is then
    refused for renames that are not its own) or the clean one (and the mid-repair
    archive is forgotten while spec 7.2 still has work to do on it, which is the
    dangerous half). No sort order satisfies both.
    """
    mid_repair, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    clean = scanned(tmp_path, name="clean")
    set_fault.arm("crash:after_intent:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, mid_repair, confirm=lambda ctx: True)
    set_fault.next_invocation()

    code, text = output(["forget", str(clean)], capsys)
    assert code == 0, "another archive's unfinished renames must not block this one"
    assert "renames it has not finished" not in text

    code, text = output(["forget", str(mid_repair)], capsys)
    assert code == 1, "this archive's own unfinished renames must block it"
    assert "IMG_1.JPG -> 2019-07-04_001.JPG" in text
    assert [a.path for a in known_archives()] == [mid_repair]


def test_forget_refuses_an_unreachable_archive_mid_repair_with_the_reachable_fix(
    tmp_path, set_fault, capsys
):
    """UX 1.4: the fix differs because recovery cannot run against a folder pam cannot
    see (J9), so "run any pam command" there would be a dead end."""
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    set_fault.arm("crash:after_intent:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root, confirm=lambda ctx: True)
    set_fault.next_invocation()
    shutil.rmtree(root)

    code, text = output(["forget", str(root)], capsys)

    assert code == 1
    assert "pam cannot see this folder right now, so it cannot finish them." in text
    assert "Run any pam command against it first" not in text


def test_forget_refuses_an_unknown_path_and_lists_what_it_does_know(tmp_path, capsys):
    """UX 1.4: a refusal that does not say what pam knows is a dead end."""
    root = scanned(tmp_path)

    code, text = output(["forget", str(tmp_path / "not-an-archive")], capsys)

    assert code == 1
    assert "there is nothing to forget" in text
    assert "Folders pam knows about:" in text
    assert str(root) in text
    assert [a.path for a in known_archives()] == [root]


def test_forget_refuses_an_unknown_path_with_nothing_known_and_still_says_what_to_do(
    tmp_path, capsys
):
    """The branch the sweep below found empty. It stated a fact and stopped."""
    code, text = output(["forget", str(tmp_path / "not-an-archive")], capsys)

    assert code == 1
    assert "does not know about any photo folder yet" in text
    assert "Run: pam scan <your photo folder>" in text


def test_every_forget_refusal_says_what_to_do_next(tmp_path, set_fault, capsys):
    """UX 1.4 across ALL of `pam forget`'s refusals, in one place.

    `tests/test_verify.py::test_every_finding_offers_exactly_one_next_step` drives this
    rule from `ALL_CHECKS`, so it cannot reach a command that has no checks - measured
    at M6 Task 5, and `pam forget` has four distinct refusals it never saw. Each is
    produced here for real rather than by calling the message builders, because two of
    them branch on whether the folder is on disk at this instant.

    "What to do next" is asserted as an imperative the user can act on, which is what
    UX 1.4 means by never a dead end: a `Run:` line, or a sentence telling them to
    reconnect the folder. A refusal that only explains is the failure.
    """
    refusals = []

    # 1. Unknown path, with other archives known: pick one of those.
    known_root = scanned(tmp_path, name="known")
    code, text = output(["forget", str(tmp_path / "not-an-archive")], capsys)
    assert code == 1
    refusals.append(("unknown path, others known", text))

    # 2. Unknown path, nothing known at all.
    output(["forget", str(known_root)], capsys)
    assert known_archives() == []
    code, text = output(["forget", str(tmp_path / "not-an-archive")], capsys)
    assert code == 1
    refusals.append(("unknown path, nothing known", text))

    # 3. Mid-repair and REACHABLE: any pam command finishes it.
    root, plan_id = planned(tmp_path, ["IMG_1.JPG"])
    set_fault.arm("crash:after_intent:1")
    with pytest.raises(SystemExit):
        approve_and_apply(plan_id, root, confirm=lambda ctx: True)
    set_fault.next_invocation()
    code, text = output(["forget", str(root)], capsys)
    assert code == 1
    refusals.append(("mid-repair, reachable", text))

    # 4. Mid-repair and UNREACHABLE: recovery cannot run at all (J9), so reconnect.
    shutil.rmtree(root)
    code, text = output(["forget", str(root)], capsys)
    assert code == 1
    refusals.append(("mid-repair, unreachable", text))

    for where, text in refusals:
        actionable = "Run:" in text or "Run any pam command" in text or "Connect the" in text
        assert actionable, f"{where}: this refusal gives the user nothing to do"


def test_forget_matches_a_folder_however_the_user_spells_it(tmp_path, capsys):
    """The pointer holds one spelling; the user types another. `archive_key` is the one
    comparison, shared with `archive_slug` so the two cannot disagree."""
    root = scanned(tmp_path)

    code, _ = output(["forget", str(root).upper()], capsys)

    assert code == 0
    assert known_archives() == []


def test_forget_matches_an_UNREACHABLE_folder_however_the_user_spells_it(tmp_path, capsys):
    """Review M371. The reachable case above does NOT test `archive_key`'s lowercasing.

    On Windows `Path.resolve()` canonicalizes the case of the part of a path that
    EXISTS - `Path(str(root).upper()).resolve()` comes back spelled exactly as the
    folder is spelled on disk - so for a reachable archive the match succeeds whether
    or not `archive_key` lowercases, and the test above passes for the wrong reason
    (`.lower()` deleted: the whole suite stayed green).

    Resolution cannot canonicalize a path that is not there, so the raw casing survives
    and `.lower()` is the only thing left doing the work. That is the case `pam forget`
    exists for: an unplugged drive whose path the user retypes by hand, in whatever
    case they happen to use.
    """
    root = unreachable(tmp_path, name="Dead Drive")

    code, _ = output(["forget", str(root).upper()], capsys)

    assert code == 0, "an unreachable archive must match however its path is spelled"
    assert known_archives() == []


def test_forget_closes_the_scenario_spec_3_rev_8_created(tmp_path, capsys):
    """Two archives, one unreachable: resolution refuses to auto-select, and `pam
    forget` is what makes the refusal livable. Nothing else closed this.
    """
    live = scanned(tmp_path, name="live")
    dead = unreachable(tmp_path, name="dead")
    with pytest.raises(PamError) as refusal:
        resolve_archive(None)
    assert "more than one photo folder" in str(refusal.value)

    code, _ = output(["forget", str(dead)], capsys)

    assert code == 0
    assert resolve_archive(None) == live
    assert run_plan(None).plan_id is not None, (
        "an archive-scoped command must work again with no --archive"
    )
