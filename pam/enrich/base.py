"""Spec 8.1's enricher interface and the runner that owns everything else.

The Protocol below is spec 8.1's normative block. The runner is here too because spec
8.1 says it is ("The **runner** (also in `base.py`) owns everything else, identically
for all enrichers"); only the pieces that are testable on their own - the ETA
(`eta.py`), spec 6.4's write-time validation (`validate.py`) and, since M11 Task 8, the
write step itself (`sink.py`) - are split out, and each says in its own docstring why.

THE SINGLE WRITER IS STRUCTURAL, NOT A CONVENTION. `_enrich_one` is a MODULE-LEVEL
function whose parameters do not include a connection and which closes over nothing
(`co_freevars == ()`), so a worker has no route to `conn` short of someone editing this
file. Underneath that, `sqlite3.connect` defaults to `check_same_thread=True` and
`catalog/db.py` does not turn it off, so a worker that found the connection anyway would
raise `ProgrammingError` rather than corrupt anything. Both layers are pinned by tests:
one reads the code object, one drives a fake enricher that tries the write.

THE WRITER MOVED OUT AND THE ARGUMENT MOVED WITH IT (M11 Task 8 Step 1, I16).
`sink.write_facts` used to be a closure in `run_enricher`, so no worker had a name for
it; it is now module-level and takes `conn`, and what keeps it on the calling thread is
`flush` - the one call site, which runs on this thread. Neither test above moved with
it, so a third watches the thread the writer ACTUALLY RUNS ON: the rows it leaves behind
cannot tell "never ran on a worker" from "ran on one and got away with it".

WHAT THE CURSOR MEANS. `enrichment_runs.cursor_file_id` is the id of the last file this
run has WALKED AND COMMITTED - walked, not enriched, because a file the cache skipped
has been dealt with just as finally as one that produced a fact, and a cursor that only
moved over enriched files would re-walk every cached file on every resume. It is written
in the SAME transaction as that batch's facts, so it can never claim more than is
committed. A resume takes files with `id > cursor_file_id`. The off-by-one that matters
is the safe one: a cursor that lags re-does at most one batch, and the cache skip makes
that cheap, while a cursor that leads would lose files silently.
"""

import importlib
import io
import logging
import sqlite3
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from pam.catalog import repo
from pam.catalog.repo.files import FileRow
from pam.enrich.eta import Eta
from pam.enrich.sink import accepted_kinds, digest_refusal, refresh_refusal, write_facts
from pam.scan import exiftool, walker

# Spec 8.1: "single-writer DB commits batched every 50 files". Fifty FILES WALKED, which
# is also the dispatch chunk: one boundary, so a batch is never half written.
COMMIT_BATCH = 50

# Spec 8.1's `FileRecord` is the catalog's file row. It is an alias rather than a second
# dataclass because a parallel row type is two artefacts that have to agree - the shape
# M8's M922/M924 named - and every field `wants()` needs for the enrichers this
# milestone ships is already on `FileRow`: `media_type` (spec 8.1 rev 3: pixel enrichers
# refuse videos), `placeholder` (vision R2: reading one hydrates it), `ext`, `rel_path`
# and `sha256`.
#
# IT IS NOT COMPLETE FOR EVERY `wants()` SPEC 8.1 NAMES, and that is deliberate rather
# than overlooked. Its two other examples - "ocr: scans only" (needs
# `capture.capture_dt_source`) and "geocode: has GPS" (needs `capture.gps_lat/lon`) -
# read the `capture` table, which `catalog/repo/enrichment.py` does not reach and cannot
# reach without a sixth entry in `guards.ALLOWED_REPO_REACHES`. That decision belongs to
# Task 9, the first task that actually needs GPS; adding the reach here, for a task that
# ships no enricher, would spend a guard on a hypothesis. Widening this alias to a
# composite row later is additive and breaks nothing.
FileRecord = FileRow


@dataclass(frozen=True)
class InferredFact:
    """What an enricher inferred - the fact WITHOUT its provenance.

    `sha256`, `model_name`, `model_version` and `generated_at` are columns of
    `inferred_facts` and are stamped by the runner, not by the enricher. An enricher
    that had to supply its own `sha256` could file a fact against the wrong bytes, and
    one that stamped its own `model_version` could disagree with the identity the cache
    skip and the resume cursor are keyed on - the two halves would drift and the run
    would re-enrich the archive forever.

    `value` is a mapping and the runner serializes it (`validate.fact_value_json`), so
    no enricher hand-rolls JSON and every `value_json` in the catalog is spelled the
    same way. Spec 6.4's declared schemas are all objects.
    """

    kind: str
    value: Mapping[str, Any]
    confidence: float | None = None


class PILImageLoader(Protocol):
    """Spec 8.1's loader: opens the decoded image for one file.

    Typed as returning `Any` on purpose. The concrete type is `PIL.Image.Image` and
    Pillow arrives with Task 8; naming it here - even under `TYPE_CHECKING` - would put
    a third-party name into the one module every enricher must import, and this task
    decodes no pixels at all. Task 8 owns the real loader and may narrow this.
    """

    def __call__(self, file: FileRecord) -> Any: ...


class Enricher(Protocol):
    """Spec 8.1's normative interface. Nothing may be added to it here.

    The runner owns resume, the cache, concurrency, batching, the ETA, validation and
    re-enrichment, "identically for all enrichers" - so an enricher that needed a
    method the runner calls would be an enricher the runner treats differently.
    """

    name: str
    model_name: str
    model_version: str

    def available(self) -> tuple[bool, str]: ...
    def cost_estimate_ms(self) -> int: ...
    def wants(self, file: FileRecord) -> bool: ...
    def enrich(self, file: FileRecord, image: PILImageLoader) -> list[InferredFact]: ...


@dataclass(frozen=True)
class Progress:
    """One progress tick, emitted after each committed batch. Spec 8.1's ETA display."""

    walked: int
    total: int
    enriched: int
    eta_ms: float


@dataclass(frozen=True)
class EnrichSummary:
    """What one run did. Every skip has its own counter, and that is the point.

    A single `skipped` total cannot tell "the cache had it" from "the enricher did not
    want it" from "another file has the same bytes", and those three are the three
    mechanisms spec 8.1 asks this runner to get right. A test that can only read a
    total is a test that passes when two of them are broken in opposite directions.

    A `finished: bool` was here and is deleted (M9 Task 7, stage-2 gate). It was
    hard-coded `True` at the one construction site and read nowhere - and worse than
    unread, UNREACHABLE: `run_enricher` cannot return without finishing, so `False` was
    a value the type permitted and the code could not produce. The deletion gate has
    fired on exactly this shape three times on this project (`repo.open_plan`,
    `PlanRow`, `StemResult.truncated`); a field that carries no information is a
    question a later milestone will waste time answering. What a caller actually needs
    to know about an interrupted run is on disk, in `enrichment_runs.finished_at`.
    """

    run_id: int
    total: int
    walked: int
    enriched: int
    skipped_cached: int
    skipped_wants: int
    skipped_duplicate: int
    skipped_no_sha: int
    failed: int
    facts_written: int
    facts_dropped: int
    facts_conflicted: int
    superseded: int
    cursor_file_id: int | None
    # P3 (M12 Task 4): `facts_written`, split BY THE FACT'S KIND - never by enricher, and
    # filled at the same two increments in `sink.write_facts`, so it always sums to
    # `facts_written`. The runner still treats no enricher differently: it is `command.py`
    # that knows `faces`' result kind is `face` and its marker is bookkeeping. Defaulted
    # empty so a summary built by hand elsewhere stays constructible - an extension.
    facts_written_by_kind: Mapping[str, int] = field(default_factory=dict)
    # V1 (design 8.1 rev 34): files the loader refused because they became cloud-only after
    # the last scan. Not `failed` - nothing is wrong with them - and nothing was stored.
    cloud_only: int = 0


def _now() -> str:
    """The project's timestamp spelling (`scan/runner.py`, `status.py`)."""
    return datetime.now().isoformat(timespec="seconds")


def version_key(version: str) -> tuple[tuple[int, int, str], ...]:
    """A sortable key for a model version string.

    `inferred_facts.model_version` is TEXT and spec 8.1's example threshold is `2.0`, so
    the comparison "older than" cannot be done in SQL: SQLite orders '10.0' before
    '2.0'. Dotted decimal parts compare as numbers and everything else as text, with
    numbers ordering first, so `1.9 < 1.10 < 2.0 < 2.0a`.

    `isdecimal()`, never `isdigit()`. CLAUDE.md records the trap and the M9 handoff
    records a comment that got it wrong in both directions: `'2'.isdigit()` is True for
    the Arabic-Indic digit and `int()` accepts it, while `'2'.isdigit()` is also True
    for the superscript and `int()` RAISES. `isdecimal()` is exactly the predicate that
    means "int() will succeed".
    """
    return tuple(
        (0, int(part), "") if part.isdecimal() else (1, 0, part) for part in version.split(".")
    )


def eligible_for_enrichment(file: FileRecord, covered: frozenset[str] | set[str]) -> bool:
    """Spec 8.1 rev 16's `files_remaining` predicate, in ONE place.

    "`files_remaining` counts only files the runner does not already know it will skip -
    a file with no `sha256`, and a file already covered by a non-superseded fact from
    this `(name, model_name, model_version)`." Both the total computed before the walk
    and the running count incremented during it go through here, because two spellings
    of one predicate is two things that can drift and only one of them would be wrong.

    `wants()` is deliberately NOT consulted: it is unpredictable and calling it here
    would call it twice per file. Rev 16 says the residual overstatement that leaves is
    expected and is not a defect.
    """
    return file.sha256 is not None and file.sha256 not in covered


@dataclass
class _Counts:
    """The runner's tallies, in one mutable place so `flush` can add to them."""

    walked: int = 0
    enriched: int = 0
    skipped_cached: int = 0
    skipped_wants: int = 0
    skipped_duplicate: int = 0
    skipped_no_sha: int = 0
    failed: int = 0
    cloud_only: int = 0
    # Spec 8.1 rev 16's ETA numerator, and NOT `walked`. `walked` is the progress line's
    # numerator and UX 2's percentage - every file the runner has passed, including the
    # ones it skipped - so a single counter cannot serve both without one of the two
    # displays lying.
    eligible_walked: int = 0
    facts_written: int = 0
    # `facts_written` per fact KIND (P3). Incremented beside it, never instead of it.
    facts_by_kind: dict[str, int] = field(default_factory=dict)
    facts_dropped: int = 0
    facts_conflicted: int = 0
    superseded: int = 0


def _enrich_one(
    enricher: Enricher,
    loader: PILImageLoader,
    clock: Callable[[], float],
    file: FileRecord,
) -> tuple[FileRecord, list[InferredFact], float, BaseException | None]:
    """One worker's whole job: call the enricher, time it, and never touch the catalog.

    MODULE-LEVEL AND CLOSING OVER NOTHING. That is the single-writer rule made
    structural: there is no enclosing scope holding a connection, and no parameter that
    could carry one. The runner submits `functools.partial(_enrich_one, enricher,
    loader, clock)` rather than a lambda for the same reason - a lambda written inside
    the runner is a closure over the runner's frame, and the next person to need one
    more value in the worker would reach for the frame instead of the argument list.
    `tests/test_enrich_runner.py` asserts the empty `co_freevars` and watches what the
    workers are actually handed, because an edit that turned this into a closure would
    be invisible to any test that only watched the outcome.

    `file` is last so the partial binds cleanly for `pool.map`.

    A broad `except Exception` because one unreadable file must not end a run over
    twenty thousand of them; the exception is returned to the writer, which logs it and
    counts the file as failed. `BaseException` is deliberately NOT caught, so a
    `KeyboardInterrupt` still stops the run - that is the interruption spec 8.1's
    resume exists for.
    """
    start = clock()
    try:
        facts = list(enricher.enrich(file, loader))
    except Exception as exc:
        return file, [], (clock() - start) * 1000.0, exc
    return file, facts, (clock() - start) * 1000.0, None


def run_enricher(
    conn: sqlite3.Connection,
    enricher: Enricher,
    image_loader: PILImageLoader,
    *,
    workers: int,
    refresh_older_than_model: str | None = None,
    progress: Callable[[Progress], None] | None = None,
    logger: logging.Logger | None = None,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], str] = _now,
) -> EnrichSummary:
    """Spec 8.1's runner: resume, cache, concurrency, batching, ETA, validation, refresh.

    `image_loader` is required rather than defaulted. This task decodes no pixels and
    has no loader to offer, and a default that raised would be a stub the caller could
    forget about until it fired mid-run.

    `refresh_older_than_model` is spec 8.1's `--refresh-older-than-model`. What it
    actually changes is the SUPERSEDE, not which files run: the cache skip is keyed on
    `(name, model_name, model_version)`, so after a model upgrade nothing is cached at
    the new identity and every file re-runs whether the flag is passed or not. Without
    the flag both facts stay live side by side (`_fact_coverage` still counts the file
    once - it counts DISTINCT files, as its own docstring says); with it, each old fact
    is pointed at its replacement as that replacement is inserted.
    """
    log = logger or logging.getLogger("pam")
    # A7: every kind this enricher may emit, `inferred_facts` kinds and sink kinds
    # alike. The runner does not know which are which - `sink.write_facts` routes on the
    # KIND - which is spec 8.1's rule that the runner treats no enricher differently.
    kinds = accepted_kinds(enricher.name)
    if refresh_older_than_model is not None and (bad := refresh_refusal(enricher)) is not None:
        # A9 - THE SECOND LAYER since M13 Task 3 (S8): `pam enrich <name>` refuses the flag
        # for `faces` and `ocr` in `command.py` BEFORE any configuration is read, so a user
        # meets that refusal first. This one stays for every caller that reaches the runner
        # directly (and the no-name `pam enrich`, which S8 leaves unchanged). It is keyed
        # on the enricher having a SINK, which is a property of its kinds - the runner
        # still treats no enricher differently.
        raise ValueError(bad)
    if refresh_older_than_model is not None and (bad := digest_refusal(enricher)) is not None:
        # P13 (M12 Task 13): a digest has no order. Checked BEFORE the version comparison
        # below, which would otherwise compare two hex strings as if they were versions.
        raise ValueError(bad)
    if refresh_older_than_model is not None and version_key(
        enricher.model_version
    ) < version_key(refresh_older_than_model):
        # Refusing rather than clamping. The facts this enricher is about to write are
        # themselves older than the threshold, so they would be selected for supersede
        # by the very run producing them - and the insert would hit
        # `UNIQUE (sha256, kind, model_name, model_version)` on its own predecessor,
        # which no `ON CONFLICT REPLACE` is allowed to paper over on this project.
        raise ValueError(
            f"--refresh-older-than-model {refresh_older_than_model} is newer than"
            f" {enricher.name}'s installed model version {enricher.model_version};"
            " re-running would rewrite facts the refresh itself calls stale"
        )

    open_run = repo.open_run(conn, enricher.name, enricher.model_name, enricher.model_version)
    if open_run is None:
        run_id = repo.start_run(
            conn, enricher.name, enricher.model_name, enricher.model_version, now()
        )
        cursor: int | None = None
        conn.commit()
    else:
        run_id, cursor = open_run.id, open_run.cursor_file_id

    files = repo.files_to_enrich(conn, cursor)
    live = repo.live_facts(conn, kinds)
    covered = {
        f.sha256
        for f in live
        if f.model_name == enricher.model_name and f.model_version == enricher.model_version
    }
    targets: dict[tuple[str, str], list[int]] = {}
    if refresh_older_than_model is not None:
        threshold = version_key(refresh_older_than_model)
        for f in live:
            if f.model_name == enricher.model_name and version_key(f.model_version) < threshold:
                targets.setdefault((f.sha256, f.kind), []).append(f.id)

    counts = _Counts()
    # Known in full BEFORE the walk, which is exactly what rev 16 relies on: neither
    # half of the predicate costs a `wants()` call or a file read.
    eligible_total = sum(1 for f in files if eligible_for_enrichment(f, covered))
    eta = Eta(prior_ms=enricher.cost_estimate_ms())
    in_flight: set[str] = set()
    pending: list[FileRecord] = []
    committed_cursor = cursor

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pam-enrich") as pool:

        def flush(cursor_id: int) -> None:
            """Run the pending files, write their facts, advance the cursor, commit."""
            nonlocal committed_cursor
            work = partial(_enrich_one, enricher, image_loader, clock)
            for file, facts, elapsed_ms, exc in pool.map(work, pending):
                if isinstance(exc, CloudOnlyNowError):
                    # V1: counted, never stored - no fact, so the next run retries it.
                    counts.cloud_only += 1
                    in_flight.discard(str(file.sha256))
                    log.info("enrich %s: %s skipped - %s", enricher.name, file.rel_path, exc)
                    continue
                if exc is not None:
                    counts.failed += 1
                    # A failure produced no fact, so nothing caches these bytes and the
                    # sha must stop standing in the way of the file's duplicates: a twin
                    # that is readable when this one is not (a locked copy, a path over
                    # the limit) is the only route left to enriching this content at all.
                    # Releasing it here closes the CROSS-BATCH half only - a duplicate
                    # walked in the SAME batch was already refused at dispatch, before
                    # this loop ran. That residual is pinned by
                    # `test_a_duplicate_in_the_SAME_batch_as_a_failure_is_still_not_retried`.
                    in_flight.discard(str(file.sha256))
                    log.warning(
                        "enrich %s: %s failed - %s: %s",
                        enricher.name,
                        file.rel_path,
                        type(exc).__name__,
                        exc,
                    )
                    continue
                eta.observe(elapsed_ms)
                counts.enriched += 1
                write_facts(
                    conn,
                    enricher,
                    file,
                    facts,
                    kinds=kinds,
                    counts=counts,
                    targets=targets,
                    now=now,
                    log=log,
                )
            repo.set_run_cursor(conn, run_id, cursor_id)
            conn.commit()
            committed_cursor = cursor_id
            pending.clear()
            if progress is not None:
                progress(
                    Progress(
                        walked=counts.walked,
                        total=len(files),
                        enriched=counts.enriched,
                        eta_ms=eta.remaining_ms(
                            eligible_total - counts.eligible_walked, workers
                        ),
                    )
                )

        dirty = False
        for file in files:
            counts.walked += 1
            if eligible_for_enrichment(file, covered):
                counts.eligible_walked += 1
            dirty = True
            if file.sha256 is None:
                counts.skipped_no_sha += 1
                log.warning(
                    "enrich %s: %s has no sha256 - inferred_facts is content-addressed"
                    " (spec 4), so there is nothing to file the fact against",
                    enricher.name,
                    file.rel_path,
                )
            elif file.sha256 in covered:
                counts.skipped_cached += 1
            elif file.sha256 in in_flight:
                # Same bytes as a file already dispatched in this run. Spec 4 keys the
                # fact tables by sha256 so one fact covers both, and the check is made
                # HERE, at dispatch, rather than after the write: the second file would
                # otherwise be handed to a worker and DECODED before anything noticed -
                # on a real enricher, one exact duplicate is one whole image decoded and
                # one model call spent for a fact that already exists - and its fact
                # would then be refused by `fact_id_at` and counted as a CONFLICT rather
                # than as the duplicate it is. NOT a write race: `write_facts` runs on the
                # calling thread and `pool.map` yields in order, so two workers can
                # never reach the same UNIQUE tuple - that is what the single writer
                # buys, and it is why the argument for this check is cost and honest
                # counting rather than corruption.
                counts.skipped_duplicate += 1
            elif not enricher.wants(file):
                counts.skipped_wants += 1
            else:
                pending.append(file)
                in_flight.add(file.sha256)
            if counts.walked % COMMIT_BATCH == 0:
                flush(file.id)
                dirty = False
        if dirty:
            flush(files[-1].id)

    repo.finish_run(conn, run_id, now())
    conn.commit()
    return EnrichSummary(
        run_id=run_id,
        total=len(files),
        walked=counts.walked,
        enriched=counts.enriched,
        skipped_cached=counts.skipped_cached,
        skipped_wants=counts.skipped_wants,
        skipped_duplicate=counts.skipped_duplicate,
        skipped_no_sha=counts.skipped_no_sha,
        failed=counts.failed,
        facts_written=counts.facts_written,
        facts_written_by_kind=dict(counts.facts_by_kind),
        cloud_only=counts.cloud_only,
        facts_dropped=counts.facts_dropped,
        facts_conflicted=counts.facts_conflicted,
        superseded=counts.superseded,
        cursor_file_id=committed_cursor,
    )


# --------------------------------------------------------------------------------------
# Spec 8.1's `PILImageLoader`, and its RAW branch (rev 17 / amendment A4).
#
# IT LIVES HERE, BESIDE THE PROTOCOL IT SATISFIES, SINCE M10 TASK 6 (`m9.md`'s C5). M9
# left it in `phash.py` because `phash` was the only consumer and a shape predicted from
# one example is a guess; the captioner is the second consumer and lands in this same
# milestone, and the RAW branch below is a decision no single enricher should own.
# --------------------------------------------------------------------------------------

# WHICH EXTENSIONS ARE RAW. Spelled BY HAND from spec 11's `scan.image_exts` default
# ("jpg","jpeg","heic","heif","png","tif","tiff","cr2","nef","arw","dng"), which is also
# the four spec 8.1 rev 17 names in as many words. It is deliberately NOT derived from
# "whatever Pillow refused to open": a branch keyed on the failure it exists to prevent
# cannot be tested for having the right members, and it would silently swallow a JPEG
# that failed to decode for some other reason. `files.ext` is lowercase and dotless
# (`scan/walker.py`), so this set is compared against it directly.
RAW_EXTS = frozenset({"cr2", "nef", "arw", "dng"})


class CloudOnlyNowError(RuntimeError):
    """The file became a cloud placeholder after the last scan, so the loader did not read it.

    Design 8.1 rev 34 (V1). `files_to_enrich` trusts the catalog's placeholder flag, which
    is what the last scan saw; reading a file OneDrive has freed since would hydrate it
    (vision R2). The runner counts this apart from `failed` and STORES NOTHING for the
    file, so it is retried once it is local again.
    """


def _refuse_cloud_only(source: Path) -> None:
    """Raise `CloudOnlyNowError` if `source` is a placeholder NOW - called right before a read."""
    if walker.placeholder_now(source):
        raise CloudOnlyNowError(f"{source.name} became cloud-only after the last scan")


class PreviewUnavailableError(RuntimeError):
    """A RAW file from which no embedded preview of any kind could be extracted.

    SPEC 8.1 REV 17 IS EXPLICIT THAT THIS IS STILL A FAILURE: "a RAW carrying no
    embedded preview of any kind is still unreadable, is counted as a failure, and is
    named in the log. The honest count is what surfaced F1 in the first place." A loader
    that silently produced nothing here would hide exactly the finding this branch
    fixes - HT-M9 saw three of six real `.NEF` files fail, and it saw them because the
    count was honest.
    """


def pil_image_loader(root: Path) -> PILImageLoader:
    """Spec 8.1's `PILImageLoader` over an archive root: `FileRecord` -> a decoded image.

    Returns a CONTEXT MANAGER, which is what `PIL.Image.Image` is, and the enrichers use
    it as one. Twenty thousand files is twenty thousand open descriptors if nobody closes
    them, and `Image.open` is lazy - the `load()` here is what makes a truncated or
    corrupt file fail HERE, inside the worker's `except Exception`, instead of later.

    THE RAW BRANCH IS SPEC 8.1 REV 17 / AMENDMENT A4, and it is the answer to HT-M9's
    F1: Pillow cannot decode a camera RAW and `pillow-heif` does not add one, so half of
    the real `.NEF` files a human test tried failed `phash` with `UnidentifiedImageError`. What
    the loader opens instead is the full-size preview the CAMERA embedded, extracted by
    the same read-only exiftool chain spec 8.2 uses for the review overlay. The embedded
    preview IS the photograph - it is what every operating system, browser and camera
    back shows for that file - and `rawpy`/LibRaw would be a sixth runtime dependency
    with a native build. A pixel enricher never learns the difference.

    THE IMPORT IS DEFERRED and stays that way. `pam --version` must not pay for Pillow;
    `tests/test_deps.py::EAGER_IMPORT_CLOSURE` is `{click, pam}` because of it.

    THE HEIF OPENER IS REGISTERED HERE, AND THIS IS HT-M10's F1 (M10 Task 13). Until that
    human test, `pillow_heif.register_heif_opener()` was called in exactly one place in the
    package - `phash.py::_load()`, inside `PhashEnricher.available()` - and this loader, which
    both pixel enrichers use, never called it. Registration is a global, process-wide mutation
    of Pillow's format registry, so `pam enrich` with no name worked (`BUILDERS` is ordered
    `phash, geocode, caption`, and phash registered the opener for everyone), while
    `pam enrich caption` ALONE never built `PhashEnricher` at all and every `.heic` raised
    `UnidentifiedImageError`. Measured on a real-world library: **almost every image
    in it silently unreadable** by the milestone's headline enricher, run exactly the way its
    own documentation says to run it.

    THE ORDER OF `BUILDERS` WAS SILENTLY LOAD-BEARING FOR CORRECTNESS, which is the part worth
    keeping in mind rather than the missing line. Nothing said so, `command.py`'s own comment
    invites that map to be re-spelled by hand, and a milestone that listed `caption` first
    would have broken the DEFAULT invocation too.

    `phash.py` KEEPS ITS OWN CALL AND THAT IS NOT REDUNDANT. The two protect different things
    and neither subsumes the other: this one guarantees that *the loader pam ships* can open
    every format `scan.image_exts` names, for every enricher that takes a loader - today
    `caption`, tomorrow M11's `faces` and M12's `ocr`; phash's guarantees that *that enricher*
    decodes HEIC under any loader it is handed, including a test's. `phash.py`'s comment
    argues for its own call and its reasoning is sound; what it did not say is that the
    argument leaves the loader uncovered. (And at this close M1672 measured that phash's own
    call was itself UNPINNED - deleting it left all 2962 tests green, because this line now
    registers for the whole process. The test that claimed to cover it ran in-process, where
    any earlier registration satisfies it; it now runs in a fresh interpreter and kills the
    mutation. Both calls are load-bearing and both are now pinned.)

    NO `MODEL_VERSION` BUMPS, on `phash.py`'s own precedent: a file that FAILED stored no
    fact, so there is no cached answer to invalidate. Not-decodable to decodable is a gained
    file, never a changed one.
    """
    image_module = importlib.import_module("PIL.Image")
    # Idempotent and cheap after the first call (a dict lookup); done once per loader rather
    # than once per file. The import is not guarded: `pillow-heif` is a declared runtime
    # dependency, and an install without it is one `pam doctor` reports - failing loudly here
    # beats captioning 8% of an archive and calling it done, which is the defect above.
    importlib.import_module("pillow_heif").register_heif_opener()

    def load(file: FileRecord) -> Any:
        source = root / file.rel_path
        if file.ext in RAW_EXTS:
            decoded = image_module.open(io.BytesIO(_raw_preview(source)))
        else:
            _refuse_cloud_only(source)
            decoded = image_module.open(source)
        decoded.load()
        return decoded

    return load


def _raw_preview(source: Path) -> bytes:
    """The camera's embedded preview for one RAW file, or `PreviewUnavailableError`.

    Spec 8.2's chain, in spec 8.2's order, taken from the constant rather than respelled:
    `-PreviewImage`, then `-JpgFromRaw`, then `-ThumbnailImage`. THE ORDER DECIDES WHICH
    RENDERING OF THE PHOTOGRAPH EVERY RAW FILE'S HASH IS COMPUTED OVER, which is a
    stronger claim than the overlay makes of it, so `tests/test_exiftool.py` drives the
    chain over a fixture whose three tags carry three DIFFERENT payloads.

    `extract_binary`'s two empty answers are one answer here. `b""` means the camera
    embedded nothing and `None` means the extraction never completed, and neither hands
    this function any pixels; the enricher's caller counts the file failed either way, so
    a third outcome would be a distinction the log could carry and nothing could act on.
    """
    _refuse_cloud_only(source)
    data = exiftool.extract_binary(source, exiftool.PREVIEW_ATTEMPTS)
    if not data:
        raise PreviewUnavailableError(
            f"{source.name} is a RAW file with no embedded preview pam can read"
            " (pam does not decode RAW; it reads the preview the camera wrote)"
        )
    return data
