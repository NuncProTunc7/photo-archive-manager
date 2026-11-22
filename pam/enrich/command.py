"""`pam enrich` - design 12's command over design 8.1's runner.

ITS SUMMARY LIVES IN `pam/enrich/summary.py` (M13 Task 3, `m12.md` C8's cut): the outcome
records, the progress line and the closing block. This module builds and runs; that one prints.

WHY THIS MODULE EXISTS AT ALL, when the M9 plan's Task 12 file list names only
`cli.py`, `status.py`, `report.py` and `verify/checks.py`. Every other command in this
product is a four-line `cli.py` function over a `run_*` in a module of its own
(`scan/runner.py`, `naming/planner.py`, `execute/apply.py`, `verify/`, `forget.py`), and
the orchestration a command needs - archive resolution, the workspace lock, the session
log, spec 7.2's recovery, the connection - is ~150 lines that would take `cli.py` from
263 to well over the ~300-line budget while breaking the one shape every reader of this
codebase already knows. Task 7's file list did the same thing and shipped
`enrich/validate.py` and `enrich/eta.py` beside it; a plan file list on this project is
where the work lands, not a ban on the modules it needs.

WHAT `pam enrich <name>` DOES FOR A NAME PAM DOES NOT SHIP. It refuses, and it names
the enrichers that exist **today** - never the five of design 8.1. `faces` and `ocr`
are M11 and M12; offering them here, even as a disabled row or a "coming soon", is UX
1.4's dead end dressed as guidance, which the M9 plan forbids by name. A user who types
`pam enrich faces` gets the same sentence as a user who types `pam enrich capshun`, and
both sentences end with something that works. (`caption` joined `BUILDERS` in M10 Task 8
and is no longer one of them - and note that a caption enricher which is CONFIGURED OFF is
a different thing entirely: it is a row that exists, is named, and reports why it did not
run. That is amendment A2, not the dead end this paragraph forbids.)

THE `--archive` OPTION IS A DISCLOSED DEVIATION FROM DESIGN 12'S TABLE. That table's
`pam enrich` row shows no `--archive`, but the paragraph directly above it is normative
and says `--archive PATH` is "how **every** archive-scoped command is told which folder
it means", excepting only `pam scan`, which takes a positional path. Without it this
command is unusable on any machine that knows more than one archive, because spec 3 rev
8 refuses to auto-select - the maintainer's own `PAM_HOME` is exactly that machine. The row
is read as an omission from the table rather than as a decision against the paragraph,
and a one-line amendment is owed. Same shape as M9's other five: disclosed, not invented.
"""

import logging
import time
from collections.abc import Callable
from pathlib import Path
from sqlite3 import Connection
from typing import Any

import click

from pam.catalog import repo
from pam.catalog.db import migrate, open_catalog
from pam.config import Config, load_config
from pam.enrich.base import (
    Enricher,
    FileRecord,
    PILImageLoader,
    pil_image_loader,
    run_enricher,
)
from pam.enrich.caption import CaptionEnricher
from pam.enrich.faces import FacesEnricher
from pam.enrich.geocode import GeocodeEnricher
from pam.enrich.ocr import OcrEnricher
from pam.enrich.phash import PhashEnricher
from pam.enrich.sink import digest_refusal_named, generation_refusal, refresh_refusal_named
from pam.enrich.summary import EnricherOutcome, EnrichSummaryReport, progress
from pam.errors import PamError
from pam.execute.recover import run_recovery
from pam.execute.reporter import announce_interrupted
from pam.lock import WorkspaceLock
from pam.paths import resolve_archive, workspace_if_exists
from pam.sessionlog import close_session_log, open_session_log

# One enricher's builder: everything it needs comes from the connection, the archive
# root, the archive's config and the session logger, so a new enricher in M10-M12 is one
# function and one entry in `BUILDERS` below.
#
# `Config` JOINED THE SIGNATURE IN M10 TASK 8 and it is the shape amendment A1 asked for:
# the captioner needs `[enrich.caption]`'s model, port, timeout and worker count, and A1's
# own reasoning is that those are read "by `enrich/command.py` when it builds the
# captioner" rather than by a fifth method on design 8.1's Protocol. An enricher the
# runner treats differently is what that Protocol's docstring forbids, so the difference
# lives out here, in the caller, where it is one dictionary entry.
Builder = Callable[[Connection, Path, Config, logging.Logger], tuple[Enricher, PILImageLoader]]


def _no_image(file: FileRecord) -> Any:
    """The image loader for an enricher that reads no pixels.

    `run_enricher` takes `image_loader` as a REQUIRED argument on purpose (its own
    docstring: "a default that raised would be a stub the caller could forget about
    until it fired mid-run"). `geocode` reads the catalog's GPS columns and never opens
    a file, so the honest thing to hand it is a loader that says so if it is ever
    called, rather than `pil_image_loader`, which would import Pillow for a run that
    decodes nothing.
    """
    raise AssertionError("geocode reads no pixels; nothing should ask it for an image")


def _phash(
    conn: Connection, root: Path, config: Config, log: logging.Logger
) -> tuple[Enricher, PILImageLoader]:
    return PhashEnricher(log), pil_image_loader(root)


def _caption(
    conn: Connection, root: Path, config: Config, log: logging.Logger
) -> tuple[Enricher, PILImageLoader]:
    """Design 8.1's captioner, wired to design 11's `[enrich.caption]` table.

    The HOST is not configurable and is not meant to be: design 10's promise is that pam
    talks to THIS MACHINE, and `net.LoopbackHttpClient` would refuse anything else anyway.
    Only the port moves, because a user may run Ollama somewhere other than 11434.
    """
    return (
        CaptionEnricher(
            config.caption_model,
            port=config.ollama_port,
            timeout_s=config.caption_timeout_s,
            logger=log,
        ),
        pil_image_loader(root),
    )


def _faces(
    conn: Connection, root: Path, config: Config, log: logging.Logger
) -> tuple[Enricher, PILImageLoader]:
    """M11's `faces`, wired to design 11's `[enrich.faces]` table.

    IT TAKES THE SHARED LOADER, AND THAT IS INVARIANT I4 rather than a convenience. The
    crop the review UI renders in Task 10 comes off the same decoder as the raster the
    detector saw; measured at the spike, exiftool's three extraction tags return ZERO
    BYTES on a real HEIC while Pillow decodes the full raster, so a second decode path
    would render nothing for most of a real-world library. It is also what keeps
    `tests/test_phash.py::test_the_order_of_BUILDERS_is_not_load_bearing_for_heic`
    covering this enricher without being edited: that guard iterates every pixel builder
    and asks Pillow, in a fresh interpreter, whether HEIC is registered.

    `model_dir` may be empty and this must not raise: `available()` is where a machine
    with no pack gets its sentence, and building the enricher is how the runner gets to
    ask.
    """
    return FacesEnricher(config.faces_model_dir, log), pil_image_loader(root)


def _geocode(
    conn: Connection, root: Path, config: Config, log: logging.Logger
) -> tuple[Enricher, PILImageLoader]:
    # `repo.gps_by_file_id` is read once, on this thread, and handed to the enricher as
    # data: `enrich/base.py` gives an enricher no connection, and `check_same_thread` is
    # the backstop that would catch a shortcut here.
    return GeocodeEnricher(repo.gps_by_file_id(conn), log), _no_image


def _ocr(
    conn: Connection, root: Path, config: Config, log: logging.Logger
) -> tuple[Enricher, PILImageLoader]:
    """M12's `ocr`, wired to design 11's `[enrich.ocr]` table, on the shared loader (I4).

    The `file_mtime` set is `wants()`' third operand, read here on THIS thread and handed over
    as data - `geocode`'s precedent: an enricher gets no connection. With no folders configured
    nothing is in scope, so nothing is read from the catalog at all: `_run_one` reports that
    state before building, and `tests/test_phash.py`'s pixel-builder guard builds every pixel
    builder with a default `Config()` over an EMPTY in-memory connection.
    """
    ids = repo.file_ids_by_date_source(conn, "file_mtime") if config.ocr_folders else None
    enricher = OcrEnricher(config.ocr_model_dir, config.ocr_folders, ids or frozenset(), log)
    return enricher, pil_image_loader(root)


# THE ENRICHERS PAM SHIPS TODAY, in design 8.1's own order. This is deliberately NOT
# `repo.ENRICHERS`: that tuple is design 8.1's FIVE names and is what enrichment
# coverage is reported over, while this is what `pam enrich` can actually run. Building
# one from the other in either direction is how `pam enrich caption` would come to
# exist as a row that refuses - and it is also this project's most repeated defect
# (M6's `ENRICHERS`, M5's `open_plan`): two tables that read each other agree with any
# value of either.
BUILDERS: dict[str, Builder] = {
    "phash": _phash,
    "geocode": _geocode,
    "caption": _caption,
    # AFTER `phash` AND `geocode` (recorded clarification 4), and the order is not
    # cosmetic: HT-M10's F1 was `BUILDERS`' insertion order being silently load-bearing
    # for correctness, because only `phash` registered the HEIF opener. That hole is
    # closed - `pil_image_loader` registers it now, and a guard iterates every pixel
    # builder independently - but the cheap ordering is still the one where the fastest
    # enrichers run first, so an interrupted `pam enrich` has done the cheap work.
    "faces": _faces,
    # LAST (recorded clarification 2: phash, geocode, caption, faces, ocr - cheapest first).
    "ocr": _ocr,
}

# DESIGN 8.1 REV 17'S `enabled` KEYS (amendment A2), and the THIRD STATE they create.
# "`caption`, `faces` and `ocr` all ship `enabled = false`; `phash` and `geocode` have no
# such key and always run." `faces` (M11) and `ocr` (M12) joined this map on the day they
# joined `BUILDERS`, and an enricher missing from it always runs.
#
# The value is the KEY the user is shown - naming the setting that turns it on - and the
# predicate that reads it. Spelled from design 11's table by hand rather than derived from
# `Config`'s attribute names: a map built out of the thing it is supposed to check agrees
# with any value of it.
DISABLEABLE: dict[str, tuple[str, Callable[[Config], bool]]] = {
    "caption": ("[enrich.caption] enabled", lambda config: config.caption_enabled),
    "faces": ("[enrich.faces] enabled", lambda config: config.faces_enabled),
    "ocr": ("[enrich.ocr] enabled", lambda config: config.ocr_enabled),
}

# P13 (M12): ENABLED, AND CONFIGURED TO READ NOTHING - a sibling of the disabled state, exit 0,
# no `!`, no `Run: pam doctor`. The text after `not run - `, then its hint, spelled from
# design 12 rev 27 by hand. Returns None when the enricher has something to do.
OCR_NO_FOLDERS = (
    "[enrich.ocr] folders is empty in pam.toml.",
    '  List the folders that hold scanned photos, for example folders = ["Scans"].',
)
UNSCOPED: dict[str, Callable[[Config], tuple[str, ...] | None]] = {
    "ocr": lambda config: None if config.ocr_folders else OCR_NO_FOLDERS,
}


# PER-ENRICHER WORKER COUNTS (amendments A1, B2). `[enrich.caption] workers` overrides
# `[enrich] workers` FOR THE CAPTIONER ONLY; every other enricher uses the global one.
# The default of 1 rests on memory pressure and on the absence of a demonstrated gain - a
# user who has measured their own setup may raise it anywhere in the 1-32 range, and the
# runner works at every value in it.
WORKER_OVERRIDES: dict[str, Callable[[Config], int]] = {
    "caption": lambda config: config.caption_workers,
    "faces": lambda config: config.faces_workers,
    "ocr": lambda config: config.ocr_workers,
}


def _selected(name: str | None) -> list[str]:
    """The enrichers to run: one by name, or every one pam ships.

    An unknown name is refused BEFORE the workspace lock is taken and before any log is
    opened, because nothing about it depends on the archive.
    """
    if name is None:
        return list(BUILDERS)
    if name not in BUILDERS:
        raise PamError(
            f"pam has no {name!r} enricher. Today pam enrich understands:"
            f" {', '.join(BUILDERS)}. Run: pam enrich (with no name) to run them all."
        )
    return [name]


def run_enrich(
    name: str | None,
    archive: Path | None,
    *,
    refresh_older_than_model: str | None = None,
) -> EnrichSummaryReport:
    """Run one enricher, or every enricher pam ships, over one archive."""
    names = _selected(name)
    # S8 (M13 Task 3, `m12.md` C4): the flag with `faces` or `ocr` NAMED is a usage error, refused
    # HERE - before the archive's `pam.toml` is read - so it exits 1 whether or not the enricher
    # is enabled, scoped or has its pack. Keyed on the name, sink sentence first, then digest
    # (`faces` is both and must keep hitting the sink sentence). A NAMED enricher only: with no
    # name every enricher runs, and refusing the whole command would take the refresh away from
    # the others - that path is unchanged (`m13-progress.md` records what it does).
    if name is not None and refresh_older_than_model is not None:
        for refusal in (refresh_refusal_named, digest_refusal_named):
            if (sentence := refusal(name)) is not None:
                raise PamError(sentence)
    root = resolve_archive(archive)
    # NO `root.is_dir()` GUARD HERE, and that is deliberate rather than an omission.
    # `pam plan` carries one; `resolve_archive` has already made it unreachable, because
    # spec 3 rev 8 refuses an explicit path that is not a directory AND refuses to
    # auto-select an archive it cannot see - both for J9's reason, that recovery must
    # never branch on the disk of an archive pam cannot see. A second copy of a check
    # nothing can reach is the shape this project's deletion gate has fired on three
    # times. `test_pam_enrich_refuses_an_archive_it_cannot_see` pins the property from
    # the outside, where a user meets it.
    ws = workspace_if_exists(root)
    if ws is None:
        raise PamError(f"pam has not scanned {root} yet. Run: pam scan {root}")
    # `[enrich] workers` has been validated (1-32) since M9 Task 7 and read by nothing.
    # This is its first and only reader.
    config = load_config(ws.config_path)
    report = EnrichSummaryReport(archive_root=root)
    started = time.monotonic()
    # The lock is what stops two `pam enrich` processes resuming the SAME
    # `enrichment_runs` row: `open_run` hands the newest unfinished run to whoever asks,
    # so without it two processes would share one cursor and each would advance it past
    # the other's work. Taken once around every selected enricher rather than once per
    # enricher, so a second process cannot slip between two of them.
    with WorkspaceLock(ws.lock_path):
        logger, log_path = open_session_log(ws.logs_dir, suffix="enrich")
        try:
            conn = open_catalog(ws.db_path)
            try:
                migrate(conn)
                logger.info("enrich start: root=%s enrichers=%s", root, ",".join(names))
                # Spec 7.2, inside the lock and before this command's own work - the
                # same position `pam plan` puts it in. Enrichment is keyed by content
                # hash, but the file rows it walks are the ones a half-finished rename
                # left behind, so it runs over a corrected catalog or not at all.
                run_recovery(conn, root, logger)
                for enricher_name in names:
                    report.outcomes.append(
                        _run_one(
                            conn,
                            enricher_name,
                            root,
                            config,
                            refresh_older_than_model,
                            logger,
                        )
                    )
            finally:
                conn.close()
        except BaseException:
            # Spec 13's log path is unconditional, and `cli.py`'s traceback handler
            # prints none. One implementation, in `execute/reporter.py`; the empty
            # session id is the truth - an enrich run has a log, not an undoable session.
            announce_interrupted("", log_path)
            raise
        finally:
            close_session_log(logger)
    report.elapsed_s = time.monotonic() - started
    report.log_path = log_path
    return report


def _run_one(
    conn: Connection,
    name: str,
    root: Path,
    config: Config,
    refresh_older_than_model: str | None,
    logger: logging.Logger,
) -> EnricherOutcome:
    """Build one enricher, check it is ready, and run it. Never raises for a missing dep.

    THE ENABLED CHECK COMES FIRST, BEFORE THE ENRICHER IS EVEN BUILT (amendment A2). A
    disabled captioner must not resolve a host, must not dial a port and must not build a
    client: the user turned it off, and an "off" that still opens a socket is not off.
    """
    if name in DISABLEABLE:
        key, is_enabled = DISABLEABLE[name]
        if not is_enabled(config):
            logger.info("enrich %s: not run - %s is false", name, key)
            return EnricherOutcome(name=name, disabled=key)
    if name in UNSCOPED and (idle := UNSCOPED[name](config)) is not None:
        logger.info("enrich %s: not run - %s", name, idle[0])
        return EnricherOutcome(name=name, idle=idle)
    workers = WORKER_OVERRIDES[name](config) if name in WORKER_OVERRIDES else config.workers
    enricher, loader = BUILDERS[name](conn, root, config, logger)
    ready, reason = enricher.available()
    if not ready:
        logger.warning("enrich %s: not available - %s", name, reason)
        return EnricherOutcome(name=name, refusal=reason)
    # A6: ONE ARCHIVE, ONE GENERATION, and it is checked HERE - after `available()`, so a
    # machine with no pack hears about the pack first, and before a single file is walked,
    # because the whole point is that no second generation is written. It is a REFUSAL and
    # not an exception: the archive is intact, the user has a route, and `had_errors`
    # already carries a refusal to the exit code.
    if (conflict := generation_refusal(conn, enricher)) is not None:
        logger.warning("enrich %s: refused - %s", name, conflict)
        return EnricherOutcome(name=name, refusal=conflict)
    click.echo(f"Enriching with {name} ...")
    try:
        result = run_enricher(
            conn,
            enricher,
            loader,
            workers=workers,
            refresh_older_than_model=refresh_older_than_model,
            progress=progress,
            logger=logger,
        )
    except ValueError as error:
        # `run_enricher` raises this for a refresh threshold newer than the installed
        # model. It is an ordinary mistake in a command line, not a defect, so it gets
        # spec 13's printed message and exit 1 rather than a traceback. `errors.py` is
        # not in Task 7's file list, which is why the translation lives at the caller.
        raise PamError(str(error)) from error
    # N9: design 8.1's Protocol gains nothing. The command holds the concrete enricher it
    # built and reads the identity it recorded when it loaded its sessions - `faces` and,
    # since M12 Task 13, `ocr` carry `loaded_model`; every other enricher reports none.
    model = getattr(enricher, "loaded_model", None) or ""
    return EnricherOutcome(name=name, result=result, model=model)
