"""pam plan orchestration: facts -> proposed names -> plans/plan_items (spec 6.3)."""

import json
import logging
from datetime import datetime
from pathlib import Path
from sqlite3 import Connection

from pam import __version__
from pam.catalog import repo
from pam.catalog.db import migrate, open_catalog, schema_version
from pam.config import Config, TierBounds, load_config
from pam.errors import PamError
from pam.execute.recover import run_recovery
from pam.execute.reporter import announce_interrupted
from pam.lock import HeldWorkspace, WorkspaceLock
from pam.naming import collision
from pam.naming.assess import assess_unit
from pam.naming.collision import split_rel_path
from pam.naming.dates import ResolvedDate, resolve_files
from pam.naming.events import group_events_step
from pam.naming.families import proposed_families
from pam.naming.ocr_dates import DATE_GRAMMAR_VERSION
from pam.naming.plan_summary import PlanSummary as PlanSummary
from pam.naming.stale_grouping import refuse_a_stale_grouping
from pam.naming.template import parse_template
from pam.naming.tokens import CAPTION_TIER_KEY, resolve_tokens
from pam.naming.units import Unit, _seq_numbers, _units, held_names
from pam.paths import resolve_archive, workspace_if_exists
from pam.scan import companions, walker
from pam.sessionlog import close_session_log, open_session_log

# M14 Task 5a: `PlanSummary` moved to `plan_summary.py` (a pure move) and is imported back above,
# so every importer that names it here is unchanged.


def run_plan(archive: Path | None, template_override: str | None = None) -> PlanSummary:
    """Build a rename plan for one archive. Touches no file on disk."""
    root = resolve_archive(archive)
    if not root.is_dir():
        raise PamError(
            f"{root} is not a folder pam can plan for. Run: pam scan <folder> first."
        )
    # pam plan must not create a workspace - that stays the exclusive privilege of
    # pam scan (N4). Otherwise a never-scanned folder reports "Nothing to rename.",
    # exits 0, and leaves an orphan workspace behind with no archive_root.txt.
    ws = workspace_if_exists(root)
    if ws is None:
        raise PamError(f"pam has not scanned {root} yet. Run: pam scan {root}")
    config = load_config(ws.config_path)
    # N5: `or` treats --template "" as "not given" and silently substitutes the
    # configured template. An empty template is a request parse_template must
    # refuse ("Naming template has no tokens."), not a request to ignore.
    template = config.template if template_override is None else template_override
    order = parse_template(template)  # TemplateError before any state is touched
    with WorkspaceLock(ws.lock_path, ws) as held:
        logger, log_path = open_session_log(ws.logs_dir, suffix="plan")
        try:
            summary = plan_locked(held, root, config, template, order, logger)
        except BaseException:
            # spec 13's log path is UNCONDITIONAL, and pam/cli.py's `except Exception`
            # prints a traceback with no `Log:` line. M5 closed this for apply and undo;
            # scan and plan were the remainder (M5 open question 6). `except
            # BaseException` because a `crash:` fault (SystemExit) and a real Ctrl+C
            # (KeyboardInterrupt) both fall outside `Exception`, and they are the two
            # cases where the user most needs the log path - M5's finding F2 exactly.
            # ONE implementation, in `execute/reporter.py`; a third copy would undo the
            # consolidation M5 Task 9 made. The empty session id is the truth: a plan
            # has a log, not an undoable session.
            announce_interrupted("", log_path)
            raise
        finally:
            close_session_log(logger)
    summary.log_path = log_path
    return summary


def plan_locked(
    held: HeldWorkspace,
    root: Path,
    config: Config,
    template: str,
    order: tuple[str, ...],
    logger: logging.Logger,
) -> PlanSummary:
    """The plan's body, under a lock the caller proves it holds (M14 Task 5, Step 1).

    `root` is resolved and scanned; `held` must name its workspace and still be held, or this
    raises AssertionError before touching anything. The caller owns the session log.
    """
    ws = held.require(workspace_if_exists(root))
    conn = open_catalog(ws.db_path)
    try:
        migrate(conn)
        logger.info("plan start: root=%s template=%s", root, template)
        # spec 7.2: inside the lock, before this command's own work. A plan
        # built over a catalog recovery has not yet corrected would describe
        # files by the names they had before a crash-completed rename.
        run_recovery(conn, root, logger)
        # Z2 (M14 Task 5h): AFTER recovery, which may correct `rel_path`s, and before any row.
        refuse_a_stale_grouping(conn, root, config)
        return _plan(conn, root, config, template, order, logger)
    finally:
        conn.close()


def _plan_unit(
    unit: Unit,
    seq: int,
    event: repo.EventMembership | None,
    place: repo.PlaceFact | None,
    scene: repo.CaptionFact | None,
    people: tuple[repo.PersonLabel, ...],
    order: tuple[str, ...],
    root: Path,
    taken: dict[str, set[str]],
    summary: PlanSummary,
    logger: logging.Logger,
    *,
    caption_tier: str,
    max_people: int,
    known_places: frozenset[str],
    date: ResolvedDate,
    ocr_bounds: TierBounds,
    name_evidence: str | None,
    keys: collision.StemKeys | None = None,  # Z3; `_plan` always passes it
) -> list[repo.PlanItemDraft]:
    # spec 6.1 rev 14: the unit's event is its PRIMARY's event - the same file whose
    # facts name the whole unit. None when the primary is in no event, which is every
    # undated file and anything spec 8.3 leaves ungrouped.
    # spec 6.1: the unit's place is its PRIMARY's place, for the same reason its event is
    # (the same file whose facts name the whole unit). A sidecar carries no capture row
    # and so no GPS, so this is also the only member that can have one.
    # spec 6.1: the unit's scene is its PRIMARY's caption, for the same reason its event and
    # its place are. `inferred_facts` is content-addressed and `wants()` is per file, so a
    # unit's video or `.AAE` sidecar carries no caption of its own - and must not, because
    # the whole unit takes ONE stem, built from the primary. That is also why `tier` and
    # `attention` stay uniform across a unit (spec 8.2 rev 15), which is what makes M8's
    # preset bulk-approval buttons safe. {people} is the PRIMARY's too, for the same reason: a
    # Live Photo's `.MOV` has no faces (`wants()` refuses video), and the unit takes one stem.
    facts = resolve_tokens(
        unit.primary,
        seq,
        event,
        place,
        scene,
        people,
        caption_tier=caption_tier,
        max_people=max_people,
        date=date,
    )
    values = {name: fact.value for name, fact in facts.items()}
    rel_dir, _ = split_rel_path(unit.primary.rel_path)
    dir_taken = taken.setdefault(rel_dir, set())
    assignment = collision.assign_unique_stem(
        order,
        values,
        archive_root_abs=str(root),
        rel_dir=rel_dir,
        member_exts={m.id: m.ext_as_written for m in unit.members},
        own_names=[m.filename for m in unit.members],
        seq=seq,
        dir_taken=dir_taken,
        keys=keys,
    )
    if assignment is None:
        summary.record_error(
            f"{unit.primary.rel_path}: no free name after {collision.MAX_ATTEMPTS} tries",
            len(unit.members),
        )
        logger.error("no free name: %s", unit.primary.rel_path)
        return []
    result, proposed = assignment.stem, assignment.names
    collision.reserve(dir_taken, proposed.values(), keys=keys, rel_dir=rel_dir)

    assessment = assess_unit(
        unit,
        facts,
        result,
        people,
        scene=scene,
        place=place,
        known_places=known_places,
        max_people=max_people,
        date=date,
        ocr_bounds=ocr_bounds,
        name_evidence=name_evidence,
    )

    # spec 6.3 rule 6: the unit's stable identity AS OF PLAN TIME. The primary's
    # rel_path is unique within the plan and readable in a debugging session, and a
    # lone file is simply a unit of one. spec 7.3 groups by this and never by
    # files.companion_group_id, which pam scan recomputes from scratch every scan:
    # plan -> scan (a stem-mate appears) -> apply would otherwise split a group
    # across two stems, which is the outcome 7.3 exists to prevent (vision S3).
    unit_key = f"{rel_dir}\x1f{unit.primary.rel_path}"

    # spec 6.3.3 rev 6: case-insensitive, matching 6.3.2 and 7.1. No two layers may
    # disagree about filename case - a case-only "rename" is an item 7.1 is guaranteed
    # to fail, which then reverts its whole companion unit (7.3).
    #
    # SPEC 7.3 REV 16 - THE MEMBERSHIP RULE, AND WHY THIS TEST IS PER UNIT AND NOT PER
    # MEMBER. "A plan's unit is the set of files `pam scan` grouped, as frozen at plan
    # time - not the subset of that set the plan happens to hold items for." Deciding
    # conformance per member and `continue`ing the conforming one is F12: with
    # `scan.ignore_stem_suffixes` non-empty a unit's members have DIFFERENT on-disk
    # stems (spec 5.4), so this predicate can be true for one member and false for
    # another, the plan holds a PROPER SUBSET of a companion unit, and every layer that
    # contains spec 7.3 - the preflight, spec 8.2's endpoints and the review page -
    # reasons over the plan's ITEMS and cannot see the member that has none. A name the
    # user then types splits the group across two stems (vision S3), `pam verify`
    # reports a clean 12/12, and the next scan makes it permanent.
    #
    # It needs no hand-rename to reach: apply a plan over a lone RAW, let the editor
    # export `<pam stem>.acr_.png` beside it - the exact convention the option exists
    # for - re-scan, re-plan, and the plan holds one of the unit's two members. That is
    # the steady state of an archive that has been through pam once.
    #
    # A REFUSAL WAS CONSIDERED AND REJECTED (spec 7.3 rev 16): it would be PERMANENT -
    # the state is stable across every re-plan - so the user's only route out would be
    # to rename outside pam. The conforming member gets an item whose proposed name IS
    # its current name; spec 7.0 J12 resolves that item at preflight without a journal
    # row and without an `os.rename`.
    #
    # UNDER THE DEFAULT CONFIGURATION THIS CHANGES NOTHING, and that is the acceptance
    # criterion: with `ignore_stem_suffixes` empty every member of a unit shares a stem,
    # so a unit is wholly conforming or wholly not and no plan gains an item.
    conforming = [
        member
        for member in unit.members
        if proposed[member.id].lower() == member.filename.lower()
    ]
    if len(conforming) == len(unit.members):
        # `already_conforming` counts files that got NO item, and only those - a no-op
        # item's file IS in the plan, so counting it here as well would make UX 4's
        # counts line stop reconciling.
        summary.already_conforming += len(unit.members)
        return []

    drafts: list[repo.PlanItemDraft] = []
    for member in unit.members:
        drafts.append(
            repo.PlanItemDraft(
                file_id=member.id,
                old_rel_path=member.rel_path,
                unit_key=unit_key,
                generated_filename=proposed[member.id],
                tokens_json=assessment.tokens_json,
                tier=assessment.item_tier,
                trust_score=assessment.trust_score,
                trust_reasons_json=assessment.trust_reasons_json,
            )
        )
    # Unconditional as of spec 7.3 rev 16: the wholly-conforming unit returned above, so
    # every unit reaching here emits an item for EVERY member and `drafts` cannot be
    # empty. The old `if drafts:` guard is now a branch no input can take.
    summary.tiers[assessment.item_tier] = summary.tiers.get(assessment.item_tier, 0) + len(
        drafts
    )
    return drafts


def _template_of(conn: Connection, plan_id: int) -> str | None:
    """AM6: the template the plan that applied a typed name was built from."""
    header = repo.plan_header(conn, plan_id)
    return header.template if header is not None else None


def _plan(
    conn: Connection,
    root: Path,
    config: Config,
    template: str,
    order: tuple[str, ...],
    logger: logging.Logger,
) -> PlanSummary:
    summary = PlanSummary(template=template)
    # design 6.1 rev 27 (P9): the OCR readings, read ONCE and before step 0, because BOTH of
    # this command's consumers resolve each file's EFFECTIVE date through
    # `naming/dates.py::resolve_date` - step 0's partition for every file, and the names.
    readings = repo.ocr_readings_by_file_id(conn, logger)
    ocr_bounds = config.tiers["ocr"]
    group_events_step(conn, config, summary, logger, readings=readings)
    files = repo.files_for_planning(conn, walker.READ_MEDIA_TYPES)
    # Each file's effective date replaces its catalog date BEFORE `_units`, so `{seq}`'s
    # order and scope, the consistency context and the score all see it with no further
    # wiring. The `ResolvedDate`s are kept by file id for the primaries' tokens.
    files, dates = resolve_files(files, readings, bounds=ocr_bounds, folders=config.ocr_folders)
    summary.total = len(files)
    units, skipped = _units(files, repo.companion_primaries(conn))
    summary.skipped_placeholders = skipped
    # Read AFTER step 0's grouping and in its transaction, so these are this run's
    # events (spec 6.3 rule 5b rev 14). Both consumers below are spec 6.1 rev 14's:
    # {seq}'s (event_id, date) scope, and the {event} token itself.
    memberships = repo.event_membership(conn)
    # spec 6.1's {place}: the live `place` facts spec 8.1's geocode enricher wrote. Read
    # once for the whole archive rather than per unit - `inferred_facts` is keyed by
    # sha256 and the join to `files` is the same one `pam status`'s coverage pays for, so
    # asking it 400 times would be 400 joins for one answer. An empty dict is the normal
    # state until `pam enrich` runs, and every {place} token is then simply absent.
    places = repo.places_by_file_id(conn)
    # spec 6.1's {scene}: the live `caption` facts spec 8.1's caption enricher wrote. Read
    # once for the whole archive, for the reason `places` is.
    captions = repo.captions_by_file_id(conn)
    # spec 6.4 rev 17's place-name vocabulary, and the one operand in this function that is
    # about the ARCHIVE rather than about a file. One read per plan run, handed to every
    # unit: rev 18's non-locality is a property of the rule, not a licence to query per
    # unit.
    known_places = repo.live_place_names(conn)
    # spec 11's `[tiers] caption`, read ONCE and here rather than inside `resolve_tokens`,
    # which is a pure function over catalog rows and takes no Config. The derivation - why
    # this key and not a literal - is written out at `resolve_tokens`' `caption_tier`
    # parameter. A missing row is a KeyError on purpose: `config._default_tiers` always
    # supplies one, and a catalog of tier bounds that has lost the enricher pam is about to
    # name files from is not something to paper over with a default.
    caption_tier = config.tiers[CAPTION_TIER_KEY].max_tier_when_no_confidence
    # spec 6.1's {people}, read once and keyed by CONTENT (owner ruling 2026-09-17: no sixth
    # repo reach). Each unit primary is mapped onto it through `files.sha256` below. The
    # floor is `[tiers] faces`' (A11), the same one the labeling page reads through.
    people = repo.people_by_sha256(conn, config.tiers["faces"].floor)
    # design 6.4, M13 (H8): the names pam wrote, read once - the video rule compares a
    # `qt_utc` date with the name the primary had BEFORE pam, never with pam's own. HT-M14 F1
    # (AM6): `{seq}` orders by that name too, and a name the user typed is kept (below).
    catalog = repo.all_files(conn)  # keyed by rel_path; also the primary -> sha256 map
    written = repo.pre_pam_names(conn)
    held = held_names(files, written)
    seq_of = _seq_numbers(units, memberships, {i: e.pre_pam_path for i, e in held.items()})
    # EVERY catalog rel_path is taken, not only the `present` ones. files.rel_path is
    # UNIQUE and rows are never deleted - a file removed outside pam leaves a `missing`
    # row holding its name forever. Proposing that name plans a rename that SUCCEEDS on
    # disk and then fails in `set_file_path` with an IntegrityError, leaving a journal
    # row in `intent` beside a completed rename. spec 7.2's recovery would adopt it and
    # hit the same constraint, so the row can never be resolved - breaking J7 ("after
    # recovery no row remains in intent") on a pass that runs at the start of every
    # invocation. Do not narrow this back to `present`.
    #
    # The left-behind report below still uses `present` only, and correctly so: it
    # warns about a stem-mate that is really on disk and will really be detached. A
    # ghost row has no partner left to strand.
    all_rows = catalog.values()
    present = [row.rel_path for row in all_rows if row.status == "present"]
    taken = collision.taken_names([row.rel_path for row in all_rows])
    # design 6.3 rule 2, Z3: the same rows' 5.4 grouping keys, so no new name shares a stem with a
    # file outside its unit (finding 5f-F1, vision S3). The config's suffixes, as grouping reads them.
    keys = collision.StemKeys([row.rel_path for row in all_rows], config.ignore_stem_suffixes)
    drafts: list[repo.PlanItemDraft] = []
    for unit in units:
        typed = held.get(unit.primary.id)
        typed_plan = typed.typed_in_plan if typed is not None else None
        if typed_plan is not None and _template_of(conn, typed_plan) == template:
            # AM6: the user typed this unit's name and pam applied it; the template that
            # proposed it is still in force, so pam does not offer to overturn it. Every member
            # shares the typed stem (design 7.3), so the unit gets no item, whole.
            summary.already_conforming += len(unit.members)
            continue
        drafts.extend(
            _plan_unit(
                unit,
                seq_of[unit.primary.id],
                memberships.get(unit.primary.id),
                places.get(unit.primary.id),
                captions.get(unit.primary.id),
                people.get(catalog[unit.primary.rel_path].sha256 or "", ()),
                order,
                root,
                taken,
                summary,
                logger,
                caption_tier=caption_tier,
                max_people=config.max_people,
                known_places=known_places,
                date=dates[unit.primary.id],
                ocr_bounds=ocr_bounds,
                name_evidence=repo.name_evidence(
                    written.get(unit.primary.id),
                    unit.primary.rel_path,
                    catalog[unit.primary.rel_path].size,
                ),
                keys=keys,
            )
        )
    for err in summary.errors:
        logger.error("plan: %s", err)

    # spec 6.3.5 rev 6: superseding is UNCONDITIONAL, and therefore lives above the
    # early return - a run that plans nothing supersedes too (N3). Otherwise a user
    # who renames by hand and re-plans is told "nothing to rename" while the
    # previous plan stays open, pointing at files the catalog has marked missing.
    # At most one open plan may exist and it must reflect the current catalog.
    # On the write path this stays in the same transaction as create_plan below, so
    # a crash can never leave the archive with no plan where one was superseded.
    repo.supersede_open_plans(conn)
    if not drafts:
        conn.commit()  # the supersede is the whole result of this run - keep it
        logger.info("plan: nothing to rename (%d files considered)", summary.total)
        return summary

    # spec 6.3.3a: a file sharing a directory and stem with something we rename,
    # but not renamed itself, gets detached from its partner. Those are stem-mates
    # outside scan.sidecar_exts (.lrv, .thm, Takeout .json). pam does not guess
    # which unknown extensions belong together - it counts them and says so.
    # spec 6.3.3a: "shares a stem" is companions.stem_key - the same definition
    # grouping uses, so scan.ignore_stem_suffixes applies here too and the two can
    # never disagree.
    def key(rel_path: str) -> tuple[str, str]:
        return companions.stem_key(rel_path, config.ignore_stem_suffixes)

    renamed_stems = {key(draft.old_rel_path) for draft in drafts}
    planned = {draft.old_rel_path for draft in drafts}
    for rel_path in present:
        if rel_path not in planned and key(rel_path) in renamed_stems:
            summary.left_behind += 1
            logger.warning("left behind (renaming its stem-mate): %s", rel_path)

    # A few before/after pairs for the summary, so the command is useful before
    # M7's review UI exists.
    summary.examples = [
        (split_rel_path(draft.old_rel_path)[1], draft.generated_filename)
        for draft in drafts[:3]
    ]
    # spec 6.3.4: the supersede above, step 0's grouping, and this write commit together.
    # spec 6.3 rule 5a rev 6: config_json freezes the GROUPING-relevant config too.
    # sidecar_exts and ignore_stem_suffixes determine companion membership, so a
    # plan that does not record them cannot be shown to have been built under the
    # rules now in force. Rev 14 adds gap_hours and gps_km by the same argument: they
    # determine EVENT membership, and event membership determines {event} and {seq}'s
    # scope. The keys are spelled bare, as max_people already is for [naming]
    # max_people - the section prefixes in rule 5a's prose identify pam.toml keys, and
    # this dict has never carried them.
    #
    # float() on both numbers, for the reason repo/events.py::_params_json gives (and
    # M912 pins there): `gap_hours = 4` in pam.toml and the 4.0 default are the same
    # parameter, and json.dumps writes them as `4` and `4.0`. Without the coercion the
    # frozen record and the events.params_json identity written by the SAME RUN would
    # disagree textually about the number both of them are recording.
    summary.plan_id = repo.create_plan(
        conn,
        now=datetime.now().isoformat(timespec="seconds"),
        template=template,
        config_json=json.dumps(
            {
                "template": template,
                "max_people": config.max_people,
                "sidecar_exts": list(config.sidecar_exts),
                "ignore_stem_suffixes": list(config.ignore_stem_suffixes),
                "gap_hours": float(config.event_gap_hours),
                "gps_km": float(config.event_gps_km),
                # rev 27 (P10): through design 6.1's plan-time OCR date these three now
                # determine event membership and the date token, by rule 5a's own criterion.
                # `float()` on the floor for `gap_hours`' reason above.
                "ocr_folders": list(config.ocr_folders),
                "ocr_floor": float(ocr_bounds.floor),
                "ocr_grammar_version": DATE_GRAMMAR_VERSION,
            },
            sort_keys=True,
        ),
        app_version=__version__,
        # UX spec 4's counts line. Frozen HERE or lost: these files get no
        # plan_items row, so apply cannot recompute the number (migration 0005).
        already_conforming=summary.already_conforming,
        schema_version=schema_version(conn),
    )
    repo.insert_plan_items(conn, summary.plan_id, drafts)
    conn.commit()
    summary.planned = len(drafts)
    # C2: the count behind "would be renamed". Case-insensitive, matching spec 6.3.3 and
    # `validator.is_noop`, and spelled here rather than imported from `pam.execute` -
    # `naming/` does not depend on `execute/`, and one shared helper across that seam
    # would be a layering inversion for a display line. The two readings cannot drift on
    # anything that matters, because this one governs a SENTENCE and the validator's
    # governs the RENAME.
    summary.noop_items = sum(
        1
        for draft in drafts
        if draft.generated_filename.lower() == split_rel_path(draft.old_rel_path)[1].lower()
    )
    # C11. A number skipped because a GHOST ROW holds it - a file the catalog still records
    # and the disk no longer has. Computed after assignment from what this function already
    # holds; a second question put to the collision search would be a second implementation
    # of "which names are taken", which is M4's ghost-row defect exactly.
    families = proposed_families(drafts)
    summary.blocked_by_missing = sorted(
        row.rel_path
        for row in all_rows
        if row.status == "missing" and row.rel_path.lower() in families
    )
    for rel_path in summary.blocked_by_missing:
        logger.warning("number held by a record with no file on disk: %s", rel_path)
    logger.info(
        "plan done: id=%s %d items (%d already conforming, %d placeholders skipped)",
        summary.plan_id,
        summary.planned,
        summary.already_conforming,
        summary.skipped_placeholders,
    )
    return summary
