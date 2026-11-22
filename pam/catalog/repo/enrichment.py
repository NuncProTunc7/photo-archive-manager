"""Catalog SQL for the enrichment tables. All SQL lives under catalog/repo/.

`inferred_facts` is spec 4's content-addressed fact table: keyed by `sha256`, not by
`file_id`, so duplicates share enrichment and the cache survives a rename. Spec 4's other
content-addressed table, `face_instances`, moved to `catalog/repo/faces.py` in M11 Task 8
along with `persons`; nothing here names it any more, and `faces` coverage is now the
per-file marker fact like every other enricher's (A10). The queries exist now because spec 12
makes "enrichment coverage" part of `pam status` now, and because a coverage row that
is absent reads as "not applicable" while a coverage row showing 0 reads as "not done
yet" - which is the true state of every enricher in this milestone.

The join to `files` is deliberate and is what makes the number a COVERAGE. Counting
`DISTINCT sha256` in the fact tables alone answers "how many distinct contents carry a
fact", which is not the question: two identical photos in the archive are two files and
both are covered by one fact row. The reach is the same shape as `plans.
files_for_planning`'s - a query that exists to serve one consumer, filed with the tables
it is about rather than with `files`.
"""

import json
import logging
import sqlite3
from dataclasses import dataclass

from pam.catalog.repo.files import FileRow

# `OcrLine` is the grammar's own input type (M12 Task 9). `pam/naming/ocr_dates.py` imports
# nothing but the stdlib, and `pam/naming/__init__.py` imports nothing at all, so this is
# not a cycle: the repo layer hands the planner the shape the grammar reads, rather than a
# tuple of pairs every caller would have to convert.
from pam.naming.ocr_dates import OcrLine

# Spec 8.1's five enrichers, in the order that section lists them. `pam status` prints
# one row per name, so this tuple is the display order as well as the vocabulary.
ENRICHERS = ("caption", "faces", "ocr", "phash", "geocode")

# How many ids `facts_by_id` binds in one statement. Well under SQLite's default
# `SQLITE_MAX_VARIABLE_NUMBER` on every build this project targets, so the query never
# depends on which limit the interpreter's SQLite was compiled with.
_ID_CHUNK = 400

# Which `inferred_facts.kind` values belong to which enricher. `faces` is absent because
# it has its own table (spec 4), not a fact kind.
#
# M9 OWNED THIS MAP AND HAS NOW REVISITED IT (Task 9). Spec 8.1 names the ENRICHERS but
# writes out only one enricher's kinds - "ocr ... emits `ocr_text` and ... `ocr_date`" -
# so through M8 the other three rows assumed the kind was spelled like the enricher,
# which was the smallest guess available and was still a guess.
#
# `geocode` WAS THE ONE THE GUESS GOT WRONG, and it is corrected here: the kind is
# `place`. Spec 6.1's token is `{place}`, spec 6.4's validation rule is written about
# "`place` tokens", and `enrich/validate.py::FACT_SCHEMAS` has carried a `place` row and
# deliberately NO `geocode` row since Task 7 - so before this correction a geocode fact
# was dropped at the boundary as a kind with no schema, which was the loud direction to
# be wrong in. `caption`, `ocr` and `phash` are confirmed by the enrichers that ship:
# M9 Task 8's `phash` emits `phash`, and M10-M12 own the other two.
#
# A kind an enricher emits and this map does not name counts towards nothing in
# `pam status`, which `tests/test_repo.py` pins so the limitation is visible in a test
# rather than only in this comment.
#
# M11 TASK 8 ADDS `faces`, AND IT IS THE PER-FILE MARKER ALONE (A7, I8). Spec 4 rev 22
# writes the kind out: `'faces'` is `{"count": N}`, INCLUDING N = 0, so the runner's cache
# can tell a photograph the detector looked at and found nothing in from one nobody has
# processed. Without it every faceless photograph is decoded again on every run, for ever.
#
# THE `face` KIND IS DELIBERATELY ABSENT FROM THIS MAP. A detection goes to
# `face_instances` through `enrich/sink.py`'s sink table, not to `inferred_facts`, so a
# `face` row here would make `pam status` count coverage over a table this query does not
# read. Coverage and the cache read the marker and only the marker.
#
# M12 TASK 13 CONFIRMS `ocr` AND REMOVES ITS SECOND KIND (design 8.1 rev 27, P7). The enricher
# stores ONE kind, `ocr_text`: the reading AND the per-file marker, written even when its line
# list is empty. `ocr_date` was never written by anything and is not a stored kind - the date is
# derived at plan time - so the guess "ocr ... emits `ocr_text` and ... `ocr_date`" above is
# superseded by the spec, not by this map.
_FACT_KINDS = {
    "caption": ("caption",),
    "faces": ("faces",),
    "ocr": ("ocr_text",),
    "phash": ("phash",),
    "geocode": ("place",),
}


def _fact_coverage(conn: sqlite3.Connection, kinds: tuple[str, ...]) -> int:
    """Files carrying a LIVE `inferred_facts` row of one of these kinds.

    `superseded_by IS NULL` because spec 8.1's re-enrichment marks the old fact
    superseded and writes a new one, and a file whose only fact of this kind has been
    superseded is not covered.

    THE CORRECTED ARITHMETIC (M9 Task 7 stage-1 review, M1188). This docstring used to
    say that counting both would report "a coverage of 200%". It cannot: the count is
    `COUNT(DISTINCT f.id)` and is bounded by the number of files. Dropping the clause
    OVER-counts - it credits a file whose fact of this kind is dead - which is what
    `tests/test_repo.py::test_enrichment_coverage_ignores_superseded_facts_and_unmapped_kinds`
    measures, with a fact superseded by one of a DIFFERENT kind. The runner cannot
    produce that state (it always supersedes with a same-kind replacement), so no test
    driven through `run_enricher` can distinguish the two readings of this clause.
    """
    placeholders = ",".join("?" * len(kinds))
    row = conn.execute(
        "SELECT COUNT(DISTINCT f.id) AS n FROM files f"
        " JOIN inferred_facts i ON i.sha256 = f.sha256"
        f" WHERE i.superseded_by IS NULL AND i.kind IN ({placeholders})",
        kinds,
    ).fetchone()
    return int(row["n"])


def enrichment_coverage(conn: sqlite3.Connection) -> dict[str, int]:
    """How many files each spec 8.1 enricher has covered. Every value is 0 until M9.

    Always all five keys, in `ENRICHERS` order, including the zeros. `pam status` prints
    what it is handed, so an enricher that has run over nothing must arrive here as a 0
    rather than as an absence.

    `faces` IS NO LONGER A SPECIAL CASE (M11 amendment A10). It used to be counted by a
    `_face_coverage` of its own - files with at least one row in `face_instances` - which
    answered a different question from every other row in this table: a photograph the
    detector looked at and found nobody in was reported as NOT COVERED, so the number
    fell as the enricher got better at telling faces from wallpaper, and it could never
    reach 100 % on any user's archive. A7's per-file marker is the fact that says
    "processed", including `{"count": 0}`, and it is read here exactly like every other
    enricher's. This is Task 8 Step 10, taken in Step 5's commit because ownership of
    `face_instances` moves to `catalog/repo/faces.py` in the same change and
    `guards.REPO_OWN_TABLES` requires one owner per table.
    """
    return {name: _fact_coverage(conn, _FACT_KINDS[name]) for name in ENRICHERS}


@dataclass(frozen=True)
class PlaceFact:
    """One file's live `place` fact: the name, and the row spec 6.1 must point at."""

    fact_id: int
    place: str


@dataclass(frozen=True)
class CaptionFact:
    """One file's live `caption` fact: the LABEL, and the row spec 6.1 must point at.

    THE `label`, NOT THE `sentence`, and that is spec 6.1 in as many words: "`{scene}`:
    caption enricher's short label (the enricher emits both a `label` <=3 words and a
    `sentence`; naming uses `label`)". The sentence is the prose half and belongs to the
    review UI's Why? panel; a filename built from it would be a filename built from a
    sentence.

    A separate dataclass from `PlaceFact` above rather than a shared `(fact_id, value)`
    pair, for the reason `EVENT_LABEL_SOURCE_TIER` and `PLACE_TIER` are separate constants
    in `naming/tokens.py`: they are different questions that happen to have the same shape
    today, and a shared type would make a change to one silently a change to the other.
    """

    fact_id: int
    label: str


def _live_fact_strings(
    conn: sqlite3.Connection, kind: str, field: str
) -> dict[int, tuple[int, str]]:
    """`{file id: (fact id, value[field])}` over LIVE facts of one kind.

    THE BODY `places_by_file_id` AND `captions_by_file_id` SHARE, and only the body: each
    caller names its own kind and its own field from the SPEC, and wraps the answer in its
    own dataclass. A single function parameterised by nothing would make the two agree by
    construction (M8's M922/M924); a single function parameterised by both makes them agree
    about SQL, which is the half that is genuinely one question.

    `superseded_by IS NULL` because spec 8.1's re-enrichment marks the old fact superseded
    and writes a new one, and a filename must never be built from a fact the catalog has
    retired.

    ONE FACT PER FILE, AND THE TIE IS BROKEN DETERMINISTICALLY. `inferred_facts` is UNIQUE
    on `(sha256, kind, model_name, model_version)`, so two LIVE rows of one kind for one
    content are possible the moment a second model version runs without superseding the
    first. The highest id wins - the most recently generated - because spec 6.2 rule 6
    requires the same inputs to produce the same name, and "whichever row SQLite returned
    first" is not a rule.

    A row whose `value_json` is not `{<field>: <non-empty str>}` is skipped rather than
    raised on. The runner validates every fact before insert (spec 6.4), so this cannot
    come from pam; it can come from a hand-edited catalog, and a filename built from a
    half-parsed fact is worse than a filename with the token missing (spec 6.1's graceful
    degradation is the designed answer to an absent token).

    Keyed by `files.id` because that is what the planner holds. Content-addressed facts
    mean two identical photos in the archive both get the token, which is the join's whole
    point and the same shape `_fact_coverage` uses.
    """
    rows = conn.execute(
        "SELECT f.id AS file_id, i.id AS fact_id, i.value_json FROM files f"
        " JOIN inferred_facts i ON i.sha256 = f.sha256"
        " WHERE i.superseded_by IS NULL AND i.kind = ?"
        " ORDER BY f.id, i.id",
        (kind,),
    )
    found: dict[int, tuple[int, str]] = {}
    for r in rows:
        try:
            value = json.loads(r["value_json"])
        except json.JSONDecodeError:
            continue
        text = value.get(field) if isinstance(value, dict) else None
        if isinstance(text, str) and text:
            found[int(r["file_id"])] = (int(r["fact_id"]), text)
    return found


def places_by_file_id(conn: sqlite3.Connection) -> dict[int, PlaceFact]:
    """Every present-or-not file's live `place` name, for spec 6.1's `{place}` token.

    THE KIND IS SPELLED `place` HERE AND IN `_FACT_KINDS` ABOVE, and both are spelled
    from the spec rather than from each other: spec 6.1's token is `{place}` and spec
    6.4's write-time rule is written about "`place` tokens". A query reading its kind out
    of `_FACT_KINDS["geocode"]` would agree with that map by construction and prove
    nothing about either (M8's M922/M924), and it would silently return a second kind's
    rows if geocode ever emitted one.

    `superseded_by IS NULL` for the reason `_fact_coverage` documents: spec 8.1's
    re-enrichment supersedes the old fact and writes a new one, and a filename must never
    be built from a fact the catalog has retired.

    ONE FACT PER FILE, AND THE TIE IS BROKEN DETERMINISTICALLY. `inferred_facts` is UNIQUE
    on `(sha256, kind, model_name, model_version)`, so two LIVE `place` rows for one
    content are possible the moment a second dataset version runs without superseding the
    first. The highest id wins - the most recently generated - because spec 6.2 rule 6
    requires the same inputs to produce the same name, and "whichever row SQLite returned
    first" is not a rule.

    A row whose `value_json` is not spec 6.4's `{"place": <non-empty str>}` is skipped
    rather than raised on. The runner validates every fact before insert, so this cannot
    come from pam; it can come from a hand-edited catalog, and a filename built from a
    half-parsed fact is worse than a filename with no place in it (spec 6.1's graceful
    degradation is the designed answer to an absent token).

    Keyed by `files.id` because that is what the planner holds. Content-addressed facts
    mean two identical photos in the archive both get the name, which is the join's whole
    point and the same shape `_fact_coverage` uses.
    """
    return {
        file_id: PlaceFact(fact_id=fact_id, place=name)
        for file_id, (fact_id, name) in _live_fact_strings(conn, "place", "place").items()
    }


def captions_by_file_id(conn: sqlite3.Connection) -> dict[int, CaptionFact]:
    """Every file's live caption LABEL, for spec 6.1's `{scene}` token.

    THE KIND IS SPELLED `caption` AND THE FIELD `label`, BOTH FROM THE SPEC and neither
    from another artefact in this repository. Spec 8.1: the caption enricher's "output
    parsed to `{label, sentence}`". Spec 6.1: "`{scene}`: caption enricher's short label
    ... naming uses `label`". Reading the kind out of `_FACT_KINDS["caption"]`, or the
    field out of `enrich/validate.py`'s `FACT_SCHEMAS`, would make this query agree with
    those tables by construction and prove nothing about either (M8's M922/M924).

    The `sentence` is deliberately not returned. It is spec 6.1's prose half, it belongs to
    the review UI, and nothing that builds a filename has any use for it.

    Everything else this needs - the live filter, the deterministic tie-break, the skip
    over a malformed row - is `_live_fact_strings`' and argued there.
    """
    return {
        file_id: CaptionFact(fact_id=fact_id, label=label)
        for file_id, (fact_id, label) in _live_fact_strings(conn, "caption", "label").items()
    }


@dataclass(frozen=True)
class OcrReading:
    """One file's authoritative OCR reading: the `ocr_text` row, and every line it holds.

    `lines` carries EVERY stored line with its score, in the engine's reading order. The
    floor is not applied here - design 6.1 rev 27 applies it at PLAN time, inside the
    grammar, so raising or lowering `[tiers] ocr.floor` changes the answer without
    re-reading the image (P9 condition 3, I16).
    """

    fact_id: int
    lines: tuple[OcrLine, ...]


def _ocr_lines(value_json: str) -> tuple[OcrLine, ...] | None:
    """Design 6.4 rev 27's shape for `ocr_text`, or None when the row does not have it.

    `{"lines": [{"text": <str>, "score": <number in [0, 1]>}, ...]}` - the object holds
    `lines` and nothing else, and each entry has EXACTLY `text` and `score`, the score a real
    number (not a bool, not NaN) in `[0, 1]`. An empty list is a valid reading: a
    photograph with no text in it (P7, A7's coverage argument).
    """
    try:
        value = json.loads(value_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict) or set(value) != {"lines"}:
        return None
    raw = value["lines"]
    if not isinstance(raw, list):
        return None
    lines: list[OcrLine] = []
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != {"text", "score"}:
            return None
        text, score = entry["text"], entry["score"]
        if not isinstance(text, str) or isinstance(score, bool):
            return None
        if not isinstance(score, (int, float)) or not 0 <= score <= 1:
            return None
        lines.append(OcrLine(text=text, score=float(score)))
    return tuple(lines)


def ocr_readings_by_file_id(
    conn: sqlite3.Connection, logger: logging.Logger | None = None
) -> dict[int, OcrReading]:
    """Every file's NEWEST live `ocr_text` reading, for design 6.1 rev 27's plan-time date.

    THE KIND IS SPELLED `ocr_text` FROM THE SPEC (design 8.1 rev 27, P7), never read out of
    `_FACT_KINDS["ocr"]`, on `captions_by_file_id`'s argument (M8's M922/M924).

    THE NEWEST ROW IS SELECTED FIRST AND VALIDATED SECOND (design 6.1 rev 27 condition 3,
    pass 2's A1), AND THAT IS WHERE THIS DIFFERS FROM `_live_fact_strings`, deliberately.
    That helper skips a malformed row and so falls through to an OLDER one; here a newest
    row that does not parse - a legacy shape, a hand edit - is logged and the file gets NO
    reading, because an older reading's date must never come back when a newer reading
    exists. The helper's return shape could not carry a list anyway, which is the second
    reason this is a sibling rather than a caller.

    "Newest" is the HIGHEST ID (I15), `_live_fact_strings`' rule: OCR's `model_version` is a
    digest and has no order (P7), so the identity string never decides. OCR supersedes
    nothing, so `superseded_by IS NULL` holds for every row pam writes; it is kept for
    the same reason every other live read keeps it.
    """
    log = logger or logging.getLogger("pam")
    rows = conn.execute(
        "SELECT f.id AS file_id, i.id AS fact_id, i.value_json FROM files f"
        " JOIN inferred_facts i ON i.sha256 = f.sha256"
        " WHERE i.superseded_by IS NULL AND i.kind = 'ocr_text'"
        " ORDER BY f.id, i.id",
    )
    newest: dict[int, tuple[int, str]] = {}
    for r in rows:
        newest[int(r["file_id"])] = (int(r["fact_id"]), str(r["value_json"]))
    found: dict[int, OcrReading] = {}
    for file_id, (fact_id, value_json) in newest.items():
        lines = _ocr_lines(value_json)
        if lines is None:
            log.warning(
                "ocr_text fact %d is malformed; file %d has no OCR reading", fact_id, file_id
            )
            continue
        found[file_id] = OcrReading(fact_id=fact_id, lines=lines)
    return found


def live_place_names(conn: sqlite3.Connection) -> frozenset[str]:
    """Spec 6.4 rev 17's place-name VOCABULARY: every live `place` value in the catalog.

    > "'A known place token' means a place name this archive's own catalog holds - the
    > distinct set of `place` values across live (`superseded_by IS NULL`)
    > `inferred_facts` rows."

    Taken literally, and the literal reading is why this is NOT derived from
    `places_by_file_id`. That query JOINS `files`, so a `place` fact whose content is no
    longer in the archive would silently leave the vocabulary; the sentence above is
    written about the fact rows and about nothing else. pam ships no gazetteer and
    `reverse_geocoder`'s dataset answers *coordinates -> place*, the wrong direction, so
    this set is the only place-name vocabulary that exists in the product.

    THIS IS WHAT MAKES SPEC 6.4'S CAPTION RULE NON-LOCAL (rev 18), which the spec accepts
    and bounds: whether one photograph's caption is flagged depends on what OTHER
    photographs are in the archive. It is bounded because the rule caps a TIER and can
    never change a NAME - a `low`-tier token still emits - so spec 6.2 rule 6's "same
    inputs, same name" is untouched.

    Raw values, not sanitized: sanitization is spec 6.4's MATCHING rule and belongs to the
    rule that does the matching, and a trust reason naming the place must be able to print
    the name the catalog actually holds.
    """
    rows = conn.execute(
        "SELECT DISTINCT value_json FROM inferred_facts"
        " WHERE superseded_by IS NULL AND kind = 'place'"
    )
    names: set[str] = set()
    for r in rows:
        try:
            value = json.loads(r["value_json"])
        except json.JSONDecodeError:
            continue
        name = value.get("place") if isinstance(value, dict) else None
        if isinstance(name, str) and name:
            names.add(name)
    return frozenset(names)


@dataclass(frozen=True)
class FactModel:
    """The model identity behind one `inferred_facts` row, and its confidence.

    Spec 8.2 rev 17's `fact_models` is `<model_name> <model_version>`; the CONFIDENCE
    comes back beside it because vision Q3 rev 4 asks a different question of the same
    row - whether the model emitted a score at all - and answering both from one read
    keeps the two halves of Q3 describing the same fact.
    """

    model_name: str
    model_version: str
    confidence: float | None


def facts_by_id(conn: sqlite3.Connection, ids: list[int]) -> dict[int, FactModel]:
    """The model identity of these `inferred_facts` rows, by id. Missing ids are absent.

    NO `superseded_by IS NULL` FILTER, AND THAT IS THE POINT (spec 8.2 rev 17). Provenance
    points at the row that PRODUCED THE NAME (spec 6.1 rev 14's write-time postcondition),
    not at whatever is current, so a plan reviewed before a `--refresh-older-than-model`
    run must still explain itself afterwards. Filtering to live rows here would make a
    historical plan's Why? panel go quiet exactly when the archive learned something new.

    WHY A DERIVED FIELD IS SAFE BEHIND A PLAN REVIEWED WEEKS AGO (spec 8.2 rev 18 / B4).
    `inferred_facts` rows are APPEND-AND-SUPERSEDE: the only write to an existing row
    anywhere in `pam/` is `supersede_facts` above, setting `superseded_by`, and nothing
    edits `sha256`, `kind`, `value_json`, `confidence`, `model_name`, `model_version` or
    `generated_at` after insert - the same guarantee spec 7.0 J2 gives the journal, for the
    same reason. So the pair this function serves cannot change under a plan that has
    already been reviewed, which is what makes deriving it at read time equivalent to
    having frozen it. **If a future milestone ever rewrites a fact's model identity in
    place, this stops being derivable** and the identity has to be frozen into
    `tokens_json` instead - a spec 6.1 amendment, named here so the dependency is visible
    from the side that would break it.

    CHUNKED, because the ids come from a page of up to `api.MAX_LIMIT` items each of which
    may carry several fact-backed tokens, and SQLite's bound-parameter limit is a property
    of the build rather than of this query.
    """
    found: dict[int, FactModel] = {}
    unique = sorted(set(ids))
    for start in range(0, len(unique), _ID_CHUNK):
        chunk = unique[start : start + _ID_CHUNK]
        placeholders = ",".join("?" * len(chunk))
        for r in conn.execute(
            "SELECT id, model_name, model_version, confidence FROM inferred_facts"
            f" WHERE id IN ({placeholders})",
            chunk,
        ):
            found[int(r["id"])] = FactModel(
                model_name=str(r["model_name"]),
                model_version=str(r["model_version"]),
                confidence=None if r["confidence"] is None else float(r["confidence"]),
            )
    return found


def fact_kinds(enricher: str) -> tuple[str, ...]:
    """The `inferred_facts.kind` values one spec 8.1 enricher emits.

    The runner's cache skip (spec 8.1) is stated over `(name, model_name,
    model_version)` while `inferred_facts` is keyed by `kind`, so SOMETHING has to map a
    name to its kinds. `_FACT_KINDS` above is that map and this is its one public
    reader - a second copy of it under `pam/enrich/` would be two artefacts that agree
    with each other and neither of which is right (M8's M922/M924).

    `faces` ANSWERS NOW, AND WHAT IT ANSWERS IS THE MARKER (M11 Task 8, A7). Through M10
    it raised, because spec 4 gave the enricher `face_instances` and no fact kind at all.
    Rev 22 gives it one - the per-file `{"count": N}` marker - so the enricher has an
    `inferred_facts` kind like every other, and its DETECTIONS reach `face_instances`
    through a sink keyed on the KIND `face`. That kind is not this map's business and is
    not in it.

    A name that is not an enricher still raises rather than returning `()`: an empty
    tuple would make every fact it emits get dropped as an unaccepted kind, silently.
    """
    if enricher not in _FACT_KINDS:
        raise KeyError(f"{enricher!r} has no inferred_facts kinds")
    return _FACT_KINDS[enricher]


def files_to_enrich(conn: sqlite3.Connection, after_id: int | None) -> list[FileRow]:
    """Present files with an id greater than `after_id`, in id order (spec 8.1 resume).

    `after_id` is `enrichment_runs.cursor_file_id`, and `None` means "nothing completed
    yet, start at the beginning". It is an explicit branch rather than `COALESCE(?, 0)`
    so that no cursor and a cursor of 0 stay two different statements.

    `status = 'present'` follows `plans.files_for_planning`: a `missing` file cannot be
    opened, so enriching it is impossible rather than merely wasteful. Its facts are
    kept - spec 7.5 check 2 calls them inert, not stale - and are reused by sha256 if
    the file comes back.

    ORDER BY id, not by rel_path: the cursor IS an id, and a resume that walked in any
    other order could not use it.
    """
    columns = (
        "SELECT id, rel_path, filename, ext, media_type, size, mtime_ns, sha256,"
        " status, placeholder, companion_group_id FROM files WHERE status = 'present'"
    )
    rows = (
        conn.execute(f"{columns} ORDER BY id")
        if after_id is None
        else conn.execute(f"{columns} AND id > ? ORDER BY id", (after_id,))
    )
    return [
        FileRow(
            id=r["id"],
            rel_path=r["rel_path"],
            filename=r["filename"],
            ext=r["ext"],
            media_type=r["media_type"],
            size=r["size"],
            mtime_ns=r["mtime_ns"],
            sha256=r["sha256"],
            status=r["status"],
            placeholder=bool(r["placeholder"]),
            companion_group_id=r["companion_group_id"],
        )
        for r in rows
    ]


@dataclass(frozen=True)
class LiveFact:
    """One non-superseded `inferred_facts` row, as the runner needs to see it."""

    id: int
    sha256: str
    kind: str
    model_name: str
    model_version: str


def live_facts(conn: sqlite3.Connection, kinds: tuple[str, ...]) -> list[LiveFact]:
    """Every LIVE fact of these kinds, whichever model wrote it.

    `superseded_by IS NULL` is the same load-bearing clause `_fact_coverage` documents,
    and it serves BOTH of the runner's readings of this list: the cache skip (a
    superseded fact must not skip a file, or a model upgrade could never re-run) and
    spec 8.1's `--refresh-older-than-model` (a fact already superseded must not be
    superseded again, which repoints a live edge and can close the cycle spec 7.5
    check 5 looks for).

    Not filtered by model here, on purpose. The two readings partition this list in
    DIFFERENT ways - equal identity for the cache, older-than for the refresh - and
    "older than" is a version comparison SQLite's TEXT ordering gets wrong ('10.0' <
    '2.0'). Splitting in Python puts it where a test can drive it.
    """
    placeholders = ",".join("?" * len(kinds))
    rows = conn.execute(
        "SELECT id, sha256, kind, model_name, model_version FROM inferred_facts"
        f" WHERE superseded_by IS NULL AND kind IN ({placeholders}) ORDER BY id",
        kinds,
    )
    return [
        LiveFact(
            id=int(r["id"]),
            sha256=str(r["sha256"]),
            kind=str(r["kind"]),
            model_name=str(r["model_name"]),
            model_version=str(r["model_version"]),
        )
        for r in rows
    ]


def fact_id_at(
    conn: sqlite3.Connection, sha256: str, kind: str, model_name: str, model_version: str
) -> int | None:
    """The id occupying this UNIQUE tuple, live or superseded, or None.

    DELIBERATELY NOT filtered by `superseded_by`, unlike everything else here: the
    constraint this exists to respect - `UNIQUE (sha256, kind, model_name,
    model_version)` - is not filtered by it either. A superseded row still occupies its
    tuple, so an insert over one raises `IntegrityError` just the same, and there is no
    `ON CONFLICT REPLACE` in this project to paper over it.
    """
    row = conn.execute(
        "SELECT id FROM inferred_facts"
        " WHERE sha256 = ? AND kind = ? AND model_name = ? AND model_version = ?",
        (sha256, kind, model_name, model_version),
    ).fetchone()
    return None if row is None else int(row["id"])


def insert_fact(
    conn: sqlite3.Connection,
    *,
    sha256: str,
    kind: str,
    value_json: str,
    confidence: float | None,
    model_name: str,
    model_version: str,
    generated_at: str,
) -> int:
    """One validated fact. `superseded_by` is NULL on insert - a new fact is live."""
    cur = conn.execute(
        "INSERT INTO inferred_facts"
        " (sha256, kind, value_json, confidence, model_name, model_version, generated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (sha256, kind, value_json, confidence, model_name, model_version, generated_at),
    )
    return int(cur.lastrowid or 0)


def supersede_facts(conn: sqlite3.Connection, ids: list[int], superseded_by: int) -> None:
    """Point these facts at their replacement (spec 8.1's model-upgrade path).

    `id != superseded_by` sits in the WHERE clause rather than in an assertion: a fact
    naming itself is a one-node cycle, and spec 7.5 check 5 reports cycles as errors. A
    row cannot legitimately be its own successor, so the guard belongs at the write.
    """
    conn.executemany(
        "UPDATE inferred_facts SET superseded_by = ? WHERE id = ? AND id != ?",
        [(superseded_by, i, superseded_by) for i in ids],
    )


@dataclass(frozen=True)
class EnrichmentRun:
    """One row of `enrichment_runs` - spec 8.1's resume bookkeeping."""

    id: int
    cursor_file_id: int | None


def open_run(
    conn: sqlite3.Connection, enricher: str, model_name: str, model_version: str
) -> EnrichmentRun | None:
    """The newest UNFINISHED run for this identity, or None.

    `finished_at IS NULL` is what makes a run resumable. A run that reached the end of
    the files wrote its `finished_at` and must never be resumed, or every later
    invocation would start from that run's cursor and walk nothing.
    """
    row = conn.execute(
        "SELECT id, cursor_file_id FROM enrichment_runs"
        " WHERE enricher = ? AND model_name = ? AND model_version = ?"
        " AND finished_at IS NULL ORDER BY id DESC LIMIT 1",
        (enricher, model_name, model_version),
    ).fetchone()
    if row is None:
        return None
    cursor = row["cursor_file_id"]
    return EnrichmentRun(
        id=int(row["id"]), cursor_file_id=None if cursor is None else int(cursor)
    )


def start_run(
    conn: sqlite3.Connection,
    enricher: str,
    model_name: str,
    model_version: str,
    started_at: str,
) -> int:
    """A fresh run row with no cursor. `cursor_file_id` stays NULL until a batch commits."""
    cur = conn.execute(
        "INSERT INTO enrichment_runs (enricher, model_name, model_version, started_at)"
        " VALUES (?, ?, ?, ?)",
        (enricher, model_name, model_version, started_at),
    )
    return int(cur.lastrowid or 0)


def set_run_cursor(conn: sqlite3.Connection, run_id: int, cursor_file_id: int) -> None:
    """Advance the cursor. Written in the SAME transaction as that batch's facts."""
    conn.execute(
        "UPDATE enrichment_runs SET cursor_file_id = ? WHERE id = ?", (cursor_file_id, run_id)
    )


def finish_run(conn: sqlite3.Connection, run_id: int, finished_at: str) -> None:
    """Close the run so `open_run` stops offering it for resume."""
    conn.execute(
        "UPDATE enrichment_runs SET finished_at = ? WHERE id = ?", (finished_at, run_id)
    )


def delete_facts_of_kinds(conn: sqlite3.Connection, kinds: tuple[str, ...]) -> int:
    """Delete every `inferred_facts` row of these kinds. Returns the count (A19).

    HERE AND NOT IN `faces.py`, for `plan_items_with_people_tokens`' reason: this is SQL
    over `inferred_facts`, which this module owns, and filing it with the table it is
    ABOUT rather than with the command that calls it is what keeps
    `guards.ALLOWED_REPO_REACHES` at five.

    SUPERSEDED ROWS GO TOO, and that is deliberate rather than sloppy. A19 says purge
    removes "every `faces` marker fact" and means all of pam's face records: a superseded
    marker is still a record that pam looked at this file for faces on this date with this
    model. It is also load-bearing for deletion 3's SYMPTOM - `_fact_coverage` reads only
    live rows, but a user who purges and is left with rows in a table is entitled to call
    that a failed deletion whatever pam's queries think of them.

    NO COMMIT. A19 puts all five deletions in one transaction and the COMMAND owns it.
    """
    if not kinds:
        return 0
    placeholders = ",".join("?" * len(kinds))
    cur = conn.execute(f"DELETE FROM inferred_facts WHERE kind IN ({placeholders})", kinds)
    return int(cur.rowcount)


def delete_enrichment_runs(conn: sqlite3.Connection, enricher: str) -> int:
    """Delete every `enrichment_runs` row for this enricher. Returns the count (A19).

    A19's deletion 4, and the symptom of leaving it is the one nobody would diagnose:
    `open_run` hands the newest unfinished run back for resume, so the next
    `pam enrich faces` starts at `cursor_file_id` - PAST every file the purged run
    already walked - and reports a successful pass over the remainder of the archive.

    EVERY ROW, not only the unfinished one. A finished run carries no cursor, but it is
    still a record that pam ran the face model over this archive on this date, which is
    exactly the kind of thing A19's "pam's own records" covers.
    """
    cur = conn.execute("DELETE FROM enrichment_runs WHERE enricher = ?", (enricher,))
    return int(cur.rowcount)


def count_facts_of_kinds(conn: sqlite3.Connection, kinds: tuple[str, ...]) -> int:
    """How many `inferred_facts` rows of these kinds exist. Superseded rows included.

    A19's purge has to know what is there BEFORE it decides whether there is anything to
    delete, and `_fact_coverage` cannot answer it: that one counts FILES with a LIVE fact,
    which is a different number and is 0 for a catalog whose only markers are superseded.
    The predicate here is `delete_facts_of_kinds`' predicate, so the confirmation box and
    the deletion cannot disagree about what the command is about to remove.
    """
    if not kinds:
        return 0
    placeholders = ",".join("?" * len(kinds))
    row = conn.execute(
        f"SELECT COUNT(*) FROM inferred_facts WHERE kind IN ({placeholders})", kinds
    ).fetchone()
    return int(row[0])


def count_enrichment_runs(conn: sqlite3.Connection, enricher: str) -> int:
    """How many `enrichment_runs` rows this enricher has. Finished ones included.

    `open_run` answers a different question - the newest RESUMABLE run - and a purge that
    used it would believe a catalog holding only finished runs had nothing to delete.
    """
    row = conn.execute(
        "SELECT COUNT(*) FROM enrichment_runs WHERE enricher = ?", (enricher,)
    ).fetchone()
    return int(row[0])
