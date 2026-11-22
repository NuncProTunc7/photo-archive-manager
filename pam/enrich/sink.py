"""Spec 8.1's write step: the one place a fact reaches the catalog. The SINGLE WRITER.

M11 Task 8 Step 1, invariant I16. This is `base.py`'s `write_facts` moved out VERBATIM
ahead of Task 8's own additions, on `naming/events.py`'s precedent: `base.py` was seven
executable lines from the ~300 budget and Task 8 is the milestone that adds a second
DESTINATION for a fact (A7's `face` rows go to `face_instances`, not to
`inferred_facts`). The routing table lands here, beside the writer it routes, rather
than inside the runner that must treat every enricher identically.

WHAT THE MOVE CHANGED, AND IT IS THE ONLY THING THAT DID. `write_facts` was a closure
nested in `run_enricher`, so "runs ONLY on the calling thread" was structural in the
same way `_enrich_one` closing over nothing is: a worker had no name to call. Module
level, it is a name any caller can reach and `conn` is a parameter, so the property now
rests on the CALL SITE - `flush`, which runs on the calling thread. That is why Step 1
demands the property be re-proved rather than assumed. `sqlite3.connect` defaults to
`check_same_thread=True` and `catalog/db.py` does not turn it off, so the second layer
is unmoved: a worker that called this function anyway would raise `ProgrammingError`
rather than corrupt anything. `tests/test_enrich_runner.py` watches the DANGEROUS CALL -
the thread this function actually runs on - and not only the rows it leaves behind.

Nothing else about the write step changed. The counters, the two drop paths, the
conflict refusal and the supersede are what they were at `v0.1-m10`.
"""

import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from pam.catalog import repo
from pam.enrich.validate import fact_error, fact_value_json

if TYPE_CHECKING:  # pragma: no cover - import-cycle avoidance, not behaviour
    # `base.py` imports this module at runtime, so it must not import `base` back. Every
    # name below is needed only as an annotation, which is what TYPE_CHECKING is for:
    # mypy sees the real types, the interpreter sees none.
    from pam.enrich.base import Enricher, FileRecord, InferredFact, _Counts

FactSink = Callable[[sqlite3.Connection, "Enricher", "FileRecord", "InferredFact"], None]


def _write_face_instance(
    conn: sqlite3.Connection,
    enricher: "Enricher",
    file: "FileRecord",
    fact: "InferredFact",
) -> None:
    """A `face` fact's destination: `face_instances` (spec 4), not `inferred_facts`.

    The fact has already passed `fact_error`, so `geometry` is a dict, `embedding` is
    bytes and `confidence` is a real number in [0,1] - which is why `float(...)` here is
    a conversion and not a hope. `det_confidence` is that confidence: spec 4's column is
    NOT NULL and `validate.FACT_SCHEMAS["face"]` carries `requires_confidence` for it.

    NO FLOOR (A11). Every structurally valid detection is stored. The threshold is
    applied when detections are READ, which is what makes raising and lowering it both
    reversible; a floor here is the defect A11 exists to undo.

    `sort_keys=True` on the geometry document for `validate.fact_value_json`'s reason:
    `bbox_json` is compared and diffed by people, and two spellings of one document must
    not read as two documents.
    """
    repo.insert_face(
        conn,
        sha256=str(file.sha256),
        bbox_json=json.dumps(dict(fact.value["geometry"]), sort_keys=True),
        embedding=bytes(fact.value["embedding"]),
        det_confidence=float(fact.confidence or 0.0),
        model_name=enricher.model_name,
        model_version=enricher.model_version,
    )


# A7: the fact KINDS that do not go to `inferred_facts`, and where each goes instead.
# A KIND, NEVER AN ENRICHER. The runner already dispatches on kind twice - `repo.fact_kinds`
# and `validate.fact_error(kind, ...)` - and destination is the third property of a kind.
# Design 8.1 forbids the runner treating an ENRICHER differently, and this does not.
#
# SPELLED FROM THE SPEC, never derived from `repo._FACT_KINDS` or `BUILDERS`: a map built out
# of the thing it routes agrees with any value of it (M6's ENRICHERS, M5's open_plan).
SINK_FACT_KINDS: dict[str, tuple[str, ...]] = {"faces": ("face",)}
SINKS: dict[str, FactSink] = {"face": _write_face_instance}

# A8: the MARKER kinds, and which sink kinds each one counts.
#
# READ THE KEY CAREFULLY - IT IS A `kind`, WHILE `SINK_FACT_KINDS`' KEY IS AN ENRICHER
# NAME. The two maps are spelled identically here only because spec 4 gives the `faces`
# enricher a kind of the same name; neither is derived from the other, and a milestone
# whose marker kind differs from its enricher name will find them different.
#
# A8, in as many words: "the marker's count is computed from the rows actually accepted,
# after validation - never before it, or coverage claims rows that were dropped". So the
# runner overwrites `count` with what the sink took, and an enricher cannot make coverage
# claim detections that never reached `face_instances`.
MARKER_COUNTS: dict[str, tuple[str, ...]] = {"faces": ("face",)}


def accepted_kinds(name: str) -> tuple[str, ...]:
    """Every kind this enricher may emit: its `inferred_facts` kinds plus its sink kinds.

    One function rather than the addition spelled at the call site, because the runner is
    not allowed to know that some enrichers have sinks - it asks what this enricher may
    emit and gets one answer, exactly as it did before A7.
    """
    return repo.fact_kinds(name) + SINK_FACT_KINDS.get(name, ())


def generation_refusal(conn: sqlite3.Connection, enricher: "Enricher") -> str | None:
    """A6: one archive, one face-model generation. The sentence, or None to proceed.

    KEYED ON THE SINK, NOT ON THE NAME `faces`, which is what A9's own wording asks for
    and what keeps this a property of a KIND rather than of an enricher the runner is
    supposed to treat like every other. An enricher with no sink kinds writes nothing to
    `face_instances` and cannot create a second generation there, so it is waved through
    without a query.

    WHY IT REFUSES RATHER THAN SUPERSEDING. `face_instances` has no `superseded_by` column
    and cannot grow one without a second migration this milestone has forbidden itself
    (I5). Re-embedding the archive under the new pack is not something pam can do - it
    would need the OLD pack to know which rows to replace with which - and silently mixing
    two embedding spaces is a failure a user cannot see: cosine distances between them are
    meaningless numbers that cluster into confident, wrong people.

    The count rides along in the sentence because it is the number a user needs to decide
    whether the purge is worth it.
    """
    if not SINK_FACT_KINDS.get(enricher.name):
        return None
    stale = [
        generation
        for generation in repo.model_generations(conn)
        if (generation.model_name, generation.model_version)
        != (enricher.model_name, enricher.model_version)
    ]
    if not stale:
        return None
    held = "; ".join(
        f"{g.model_name} {g.model_version} ({g.faces} faces)" for g in sorted(stale, key=str)
    )
    return (
        f"this archive already holds faces from another model generation: {held}."
        f" The installed one is {enricher.model_name} {enricher.model_version}, and"
        " embeddings from two generations cannot be compared. Run: pam faces export"
        " (to keep the names you have already given), then pam faces purge, then"
        " re-run this command."
    )


def refresh_refusal(enricher: "Enricher") -> str | None:
    """A9: `--refresh-older-than-model` is refused for an enricher with a sink.

    KEYED ON THE PRESENCE OF A SINK rather than on the name, in A9's own words. A refresh
    supersedes an old fact by pointing it at its replacement, and `face_instances` has no
    `superseded_by` to point with - so a refresh would leave two generations of detections
    side by side in one table with nothing marking either stale, which is exactly the
    state `generation_refusal` above exists to keep out. The route is the same one.
    """
    return refresh_refusal_named(enricher.name)


def refresh_refusal_named(name: str) -> str | None:
    """`refresh_refusal`, keyed on the enricher's NAME alone (M13 Task 3, S8).

    The command refuses the flag before any configuration is read, and building an `Enricher`
    needs the configuration - so the command asks by name. The object form delegates here: one
    sentence, one place.
    """
    if not SINK_FACT_KINDS.get(name):
        return None
    return (
        f"--refresh-older-than-model cannot be used with {name}: its detections"
        " live in face_instances, which has no superseded_by, so a refresh would leave"
        " two model generations side by side. Run: pam faces export, then pam faces"
        " purge, then re-run this command."
    )


# P7 / pass 2 A7 (M12 Task 13): the enrichers whose `model_version` is a DIGEST, which has no
# order - so "older than" means nothing and `--refresh-older-than-model` is refused for them.
# SPELLED FROM THE SPEC (design 8.1: faces' identity is A4's digest, ocr's is Q2's), never
# derived from a version string's shape. `faces` is here AND keeps its sink refusal above, which
# fires first: the two refusals are separate keys, and each must stay reachable on its own.
DIGEST_IDENTITIES = frozenset({"faces", "ocr"})


def digest_refusal(enricher: "Enricher") -> str | None:
    """P13: `--refresh-older-than-model` is refused for an enricher with a digest identity.

    The sentence separates the two things a user may want (D7): a NEW engine is used
    automatically, and going BACK to an earlier engine's readings is not possible in v1.0.
    """
    return digest_refusal_named(enricher.name)


def digest_refusal_named(name: str) -> str | None:
    """`digest_refusal`, keyed on the enricher's NAME alone (M13 Task 3, S8) - see
    `refresh_refusal_named` for why the command needs this form."""
    if name not in DIGEST_IDENTITIES:
        return None
    return (
        f"--refresh-older-than-model cannot be used with {name}: its model version"
        " is a digest, which has no order, so no reading is older than another. A new"
        f" {name} engine is used automatically - just run pam enrich {name}."
        " Going back to an earlier engine's readings is not something pam v1.0 can do."
    )


def _rejected(
    enricher: "Enricher",
    file: "FileRecord",
    fact: "InferredFact",
    *,
    kinds: tuple[str, ...],
    counts: "_Counts",
    log: logging.Logger,
) -> bool:
    """Is this fact dropped before it reaches any destination? Spec 6.4, unchanged.

    The two drop paths the write step has had since M9, lifted out of the loop so that
    the `inferred_facts` half and the sink half cannot drift into two readings of
    "well-formed". Both count and both log; a fact dropped silently is the thing spec
    6.4 forbids.
    """
    if fact.kind not in kinds:
        # `_FACT_KINDS` is what `pam status` counts coverage over, so a kind
        # outside it would be stored and then counted towards nothing - the
        # limitation that map's own comment records. Dropping it is louder.
        counts.facts_dropped += 1
        log.warning(
            "enrich %s: dropped a %r fact for %s - %s emits %s",
            enricher.name,
            fact.kind,
            file.rel_path,
            enricher.name,
            ", ".join(kinds),
        )
        return True
    if (bad := fact_error(fact.kind, fact.value, fact.confidence)) is not None:
        counts.facts_dropped += 1
        log.warning(
            "enrich %s: dropped a fact for %s - %s",
            enricher.name,
            file.rel_path,
            bad,
        )
        return True
    return False


def write_facts(
    conn: sqlite3.Connection,
    enricher: "Enricher",
    file: "FileRecord",
    facts: list["InferredFact"],
    *,
    kinds: tuple[str, ...],
    counts: "_Counts",
    targets: dict[tuple[str, str], list[int]],
    now: Callable[[], str],
    log: logging.Logger,
) -> None:
    """Validate and route one file's facts. Runs ONLY on the calling thread.

    The runner's state arrives as keyword-only arguments rather than as a bag object
    because a bag is a new abstraction and this began as a move: `kinds`, `counts`,
    `targets`, `now` and `log` are exactly the five names the closure read from
    `run_enricher`'s frame, spelled out so that a sixth is an edit to a signature and not
    a reach into a scope.

    `targets` is MUTATED - each supersede pops its entry - and that is the runner's
    contract, not this function's convenience: an old fact is pointed at its replacement
    exactly once, as that replacement is inserted.

    TWO PASSES, AND THE ORDER IS A8 (M11 Task 8 Step 4). Sink-routed facts are written
    first and counted as they are ACCEPTED; only then are the `inferred_facts` facts
    written, with any MARKER's `count` overwritten from that tally. A marker written
    first, or written from what the enricher claimed, would report detections that
    validation dropped - and coverage is exactly the number a user would then trust.

    PER-FILE ATOMICITY IS THE CALLER'S, AND IT ALREADY HOLDS. Nothing here commits: spec
    8.1 batches the commit every 50 files WALKED and `run_enricher` calls `conn.commit()`
    once per batch, so one file's marker and all of its `face` rows are inside a single
    transaction by construction - a strictly larger one than A8 asks for. A run
    interrupted anywhere inside a batch leaves every file in it looking unprocessed,
    which is the state A8 names. No transaction was widened to get this, and none may be
    narrowed: a `conn.commit()` in this function would split what A8 joins, which is the
    M5 defect (`m5.md`: a stray commit that split J5's transaction and passed 596 tests).
    """
    sha = str(file.sha256)
    accepted: dict[str, int] = {}
    for fact in facts:
        if fact.kind not in SINKS:
            continue
        if _rejected(enricher, file, fact, kinds=kinds, counts=counts, log=log):
            continue
        SINKS[fact.kind](conn, enricher, file, fact)
        counts.facts_written += 1
        counts.facts_by_kind[fact.kind] = counts.facts_by_kind.get(fact.kind, 0) + 1
        accepted[fact.kind] = accepted.get(fact.kind, 0) + 1
    for fact in facts:
        if fact.kind in SINKS:
            continue
        if (counted := MARKER_COUNTS.get(fact.kind)) is not None:
            fact = replace(
                fact,
                value={**dict(fact.value), "count": sum(accepted.get(k, 0) for k in counted)},
            )
        if _rejected(enricher, file, fact, kinds=kinds, counts=counts, log=log):
            continue
        taken = repo.fact_id_at(
            conn, sha, fact.kind, enricher.model_name, enricher.model_version
        )
        if taken is not None:
            counts.facts_conflicted += 1
            log.warning(
                "enrich %s: %s already carries a %r fact from %s %s - not rewritten",
                enricher.name,
                file.rel_path,
                fact.kind,
                enricher.model_name,
                enricher.model_version,
            )
            continue
        new_id = repo.insert_fact(
            conn,
            sha256=sha,
            kind=fact.kind,
            value_json=fact_value_json(fact.value),
            confidence=fact.confidence,
            model_name=enricher.model_name,
            model_version=enricher.model_version,
            generated_at=now(),
        )
        counts.facts_written += 1
        counts.facts_by_kind[fact.kind] = counts.facts_by_kind.get(fact.kind, 0) + 1
        if superseded := targets.pop((sha, fact.kind), []):
            repo.supersede_facts(conn, superseded, new_id)
            counts.superseded += len(superseded)
