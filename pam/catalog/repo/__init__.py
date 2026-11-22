"""All catalog SQL: typed functions, explicit UPSERTs, no SQL outside this package.

Split by table in M4 (it was one module through M3) because M5-M7 keep adding
queries. The public surface is unchanged and deliberately flat: callers do
`from pam.catalog import repo` and then `repo.upsert_file(...)`, exactly as before.

One module per table, with exactly four deliberate cross-table reaches (M4 finding
13 - the M4 plan documented only the first; M6 Task 5 added the fourth).

THE COUNT BELOW IS ENFORCED, NOT PROMISED. It drifted twice while it was only prose
(one reach, then three, then four), so M7 Task 3 made it mechanical: `tests/guards.py`
extracts the table names out of each module's SQL by AST and pins the resulting
(module, foreign table) set against `ALLOWED_REPO_REACHES`, and `tests/test_repo.py`
asserts it. That list holds FIVE pairs for these four reaches - reach 1 names two
foreign tables. What stays here is the REASONING for each reach, which no test can
carry; adding a reach means writing the reasoning here AND blessing the pair there, and
doing either alone is a red test.

1. `plans.files_for_planning` reads `files` LEFT JOIN `capture`; it exists to
   serve planning, so it lives with the plans.
2. `companions` reads and writes the `files` table: `create_companion_group` /
   `clear_companion_groups` write `files.companion_group_id` and
   `companion_membership` reads `files.rel_path`. The membership pointer lives on
   the file row, so the companion module owns both ends of the link.
3. `journal` reads `journal` JOIN `plan_items` in four functions. `unit_rows`, because
   spec 7.3 rev 6 defines a companion unit as `plan_items.unit_key` and the journal
   row carries only the `plan_item_id` - reaching for `files.companion_group_id`
   instead would be the M5 bug (the scanner recreates those groups every scan), so
   the join is the safe direction, not a shortcut. And `sessions`, because spec 7.0
   J2 keeps a `rename` row `done` forever, so whether a session has been PUT BACK is
   recorded only on the `plan_items` rows spec 7.4's undo moves to `undone`. And
   `pre_pam_names` (M13 Task 5, S9/H8), because the FILE a `done` rename moved is
   `plan_items.file_id` and the journal row does not carry it. And
   `unjournaled_unrenamed_items` (M15 Task 7, E6), because an item apply refused before its
   intent, or never reached, has a `plan_items` row and no journal row, and only the pair
   can say so.
   One table pair, one direction, four queries - not a fifth reach.
4. `enrichment` reads `inferred_facts`/`face_instances` JOIN `files` (M6 Task 5). Spec
   4 keys both fact tables by `sha256` rather than by `file_id`, so "how many FILES has
   this enricher covered" - which is what spec 12's "enrichment coverage" asks - cannot
   be answered inside those tables at all. Same shape as reach 1: a query that exists
   to serve one consumer, filed with the tables it is about. M9 Task 10 adds a second
   consumer to the same reach and no new one: `places_by_file_id` answers "which place
   name belongs to THIS FILE" for spec 6.1's `{place}` token, and that is the same
   sha256-to-file_id crossing for the same reason.

`events.py` (M8 Task 7) owns TWO tables, `events` and `file_events`, and is still not a
fifth reach. `file_events` is the membership side of `events` and holds nothing else -
`(file_id, event_id)`, with `file_id` a PRIMARY KEY - so the pair is one thing stored in
two tables, the same shape as `companion_groups` plus `files.companion_group_id`. No
query in that module names `files`, `capture` or anything else. Spec 8.3's grouping INPUT
does need `files` and `capture` together, and it lives in `plans.py` instead of there,
for the reason reach 1 gives: grouping is `pam plan`'s step 0 (spec 6.3 rule 5b) and the
query exists to serve the planner. Filing it with the writer would have bought events a
sixth reach for no gain.

`faces.py` (M11 Task 8) owns TWO tables, `face_instances` and `persons`, and is not a
fifth reach either, for `events.py`'s reason. `persons` holds nothing a face does not point
at - `face_instances.person_id` is the schema's only reference to it - and every write to
one happens in the same breath as a write to the other: labeling a cluster creates the
person and moves the faces, merging a person moves faces and deletes the row. A `persons.py`
of five statements would exist only so that `faces.py` could reach into it.

TWO OF THE M11 PLAN'S EIGHTEEN `faces.py` QUERIES ARE NOT THERE, AND THAT IS DISCLOSED
RATHER THAN QUIET (Task 8, reported at the task boundary). `plan_items_with_people_tokens`
(A19) reads `plan_items.tokens_json` and nothing else - A19 requires the inventory to be
made "independently of label text" - so it is SQL over one table and is filed with that
table, in `plan_items.py`; the public name `repo.plan_items_with_people_tokens` is
unchanged. `people_by_file_id` (Task 12) would be `face_instances` JOIN `persons` JOIN
`files`, a SIXTH reach, and it does not exist: Task 12 chose the COMPOSED form (owner
ruling, 2026-09-17). `faces.people_by_sha256` reads its own two tables and nothing else,
keyed by content, and each consumer maps a file onto it through the sha256 it already holds
- the planner through `all_files`, `review.plan_rows` through its own `files` join. No pair
was blessed, so `ALLOWED_REPO_REACHES` is unchanged.

`integrity.py` (M6 Task 3), `reports.py` (M6 Task 6) and `review.py` (M7 Task 8) are the
three modules here that are NOT keyed to a table, and none of them is a fifth reach -
they are a different shape of thing. Every query `integrity.py` holds serves spec 7.5's `pam verify`, and every one of
them asks about the RELATIONSHIP between two tables (a `capture` row without its `files`
row, a `plan_items` status disagreeing with a `journal` outcome). Filing them under
whichever table each names first would put each one where it does not belong and would
turn the four documented reaches above into more than a dozen. They are gathered under
their one consumer instead; that module's docstring carries the full reasoning.
`reports.py` follows the same rule for `pam report`: its `report_facts` reads `files`
LEFT JOIN `capture` to answer "which files did pam not read, and what is missing for
each", a question neither table can answer alone - and, since M13 Task 14, for
`pam faces calibrate`'s one read, `face_instances` and `persons` beside `files`' paths, a
lock-free reader with `pam report`'s shape. `review.py` follows it for spec 8.2's
review API: every read in it is `plan_items` JOIN `files` LEFT JOIN `capture`, because
one card shows the proposed name, the tier and the provenance (`plan_items`) beside the
media type, the on-disk extension case and the placeholder flag (`files`) and the
recorded EXIF orientation (`capture`). Filed by whichever table each named first, its
handful of queries would turn the four reaches above into a dozen. All three are named
exceptions in `guards.REPO_REACH_EXEMPT`, keyed to a consumer rather than to a table, and
the exemption cites this paragraph.

`enrichment.ENRICHERS` is deliberately NOT re-exported here (M6 review, deletion gate).
It had no production reader outside `enrichment.py` itself, and its three readers were
all tests building their expectation from the very tuple the production code iterates -
so truncating it to one enricher deleted four rows from `pam status` with the whole suite
green. The tests now spell spec 8.1's five names themselves. The constant stays where it
is used; only the front-door re-export is gone.

`plan_items` was peeled out of `plans.py` in M5 (finding 13): one module held two
tables, and M5's journal writes and item-status transitions push it past 250 lines.
Re-exported names must never collide with a submodule name - `repo.plans` and
`repo.plan_items` are attributes of this package, and a same-named function would
silently shadow one. `tests/test_repo.py` asserts it.
"""

from pam.catalog.repo.capture import CaptureFacts as CaptureFacts
from pam.catalog.repo.capture import date_sources as date_sources
from pam.catalog.repo.capture import file_ids_by_date_source as file_ids_by_date_source
from pam.catalog.repo.capture import gps_by_file_id as gps_by_file_id
from pam.catalog.repo.capture import upsert_capture as upsert_capture
from pam.catalog.repo.companions import clear_companion_groups as clear_companion_groups
from pam.catalog.repo.companions import companion_kinds as companion_kinds
from pam.catalog.repo.companions import companion_membership as companion_membership
from pam.catalog.repo.companions import companion_primaries as companion_primaries
from pam.catalog.repo.companions import create_companion_group as create_companion_group
from pam.catalog.repo.enrichment import CaptionFact as CaptionFact
from pam.catalog.repo.enrichment import EnrichmentRun as EnrichmentRun
from pam.catalog.repo.enrichment import FactModel as FactModel
from pam.catalog.repo.enrichment import LiveFact as LiveFact
from pam.catalog.repo.enrichment import OcrReading as OcrReading
from pam.catalog.repo.enrichment import PlaceFact as PlaceFact
from pam.catalog.repo.enrichment import captions_by_file_id as captions_by_file_id
from pam.catalog.repo.enrichment import count_enrichment_runs as count_enrichment_runs
from pam.catalog.repo.enrichment import count_facts_of_kinds as count_facts_of_kinds
from pam.catalog.repo.enrichment import delete_enrichment_runs as delete_enrichment_runs
from pam.catalog.repo.enrichment import delete_facts_of_kinds as delete_facts_of_kinds
from pam.catalog.repo.enrichment import enrichment_coverage as enrichment_coverage
from pam.catalog.repo.enrichment import fact_id_at as fact_id_at
from pam.catalog.repo.enrichment import fact_kinds as fact_kinds
from pam.catalog.repo.enrichment import facts_by_id as facts_by_id
from pam.catalog.repo.enrichment import files_to_enrich as files_to_enrich
from pam.catalog.repo.enrichment import finish_run as finish_run
from pam.catalog.repo.enrichment import insert_fact as insert_fact
from pam.catalog.repo.enrichment import live_facts as live_facts
from pam.catalog.repo.enrichment import live_place_names as live_place_names
from pam.catalog.repo.enrichment import ocr_readings_by_file_id as ocr_readings_by_file_id
from pam.catalog.repo.enrichment import open_run as open_run
from pam.catalog.repo.enrichment import places_by_file_id as places_by_file_id
from pam.catalog.repo.enrichment import set_run_cursor as set_run_cursor
from pam.catalog.repo.enrichment import start_run as start_run
from pam.catalog.repo.enrichment import supersede_facts as supersede_facts
from pam.catalog.repo.events import ALGO_VERSION as ALGO_VERSION
from pam.catalog.repo.events import ALGORITHM as ALGORITHM
from pam.catalog.repo.events import LABEL_SOURCES as LABEL_SOURCES
from pam.catalog.repo.events import EventMembership as EventMembership
from pam.catalog.repo.events import event_membership as event_membership
from pam.catalog.repo.events import replace_events as replace_events
from pam.catalog.repo.faces import REVIEW_STATES as REVIEW_STATES
from pam.catalog.repo.faces import UNSET as UNSET
from pam.catalog.repo.faces import ClusterSummary as ClusterSummary
from pam.catalog.repo.faces import ExportFace as ExportFace
from pam.catalog.repo.faces import FaceRow as FaceRow
from pam.catalog.repo.faces import ModelGeneration as ModelGeneration
from pam.catalog.repo.faces import PersonLabel as PersonLabel
from pam.catalog.repo.faces import PersonRow as PersonRow
from pam.catalog.repo.faces import assign_faces as assign_faces
from pam.catalog.repo.faces import cluster_faces as cluster_faces
from pam.catalog.repo.faces import clusters_summary as clusters_summary
from pam.catalog.repo.faces import export_faces as export_faces
from pam.catalog.repo.faces import face_by_id as face_by_id
from pam.catalog.repo.faces import faces_for_clustering as faces_for_clustering
from pam.catalog.repo.faces import ignored_faces as ignored_faces
from pam.catalog.repo.faces import insert_face as insert_face
from pam.catalog.repo.faces import max_cluster_id as max_cluster_id
from pam.catalog.repo.faces import merge_person as merge_person
from pam.catalog.repo.faces import model_generations as model_generations
from pam.catalog.repo.faces import people_by_sha256 as people_by_sha256
from pam.catalog.repo.faces import person_faces as person_faces
from pam.catalog.repo.faces import persons_all as persons_all
from pam.catalog.repo.faces import purge_faces as purge_faces
from pam.catalog.repo.faces import retained_cluster_ids as retained_cluster_ids
from pam.catalog.repo.faces import set_cluster as set_cluster
from pam.catalog.repo.faces import set_person as set_person
from pam.catalog.repo.faces import set_review_state as set_review_state
from pam.catalog.repo.faces import update_person as update_person
from pam.catalog.repo.faces import upsert_person as upsert_person
from pam.catalog.repo.files import FileRow as FileRow
from pam.catalog.repo.files import RehashCandidate as RehashCandidate
from pam.catalog.repo.files import all_files as all_files
from pam.catalog.repo.files import file_by_id as file_by_id
from pam.catalog.repo.files import files_by_sha256 as files_by_sha256
from pam.catalog.repo.files import last_scanned_at as last_scanned_at
from pam.catalog.repo.files import mark_missing as mark_missing
from pam.catalog.repo.files import media_type_counts as media_type_counts
from pam.catalog.repo.files import path_owner as path_owner
from pam.catalog.repo.files import placeholder_count as placeholder_count
from pam.catalog.repo.files import rehash_candidates as rehash_candidates
from pam.catalog.repo.files import set_file_path as set_file_path
from pam.catalog.repo.files import set_sha256 as set_sha256
from pam.catalog.repo.files import status_counts as status_counts
from pam.catalog.repo.files import touch_files as touch_files
from pam.catalog.repo.files import upsert_file as upsert_file
from pam.catalog.repo.integrity import CompanionGroupHealth as CompanionGroupHealth
from pam.catalog.repo.integrity import ItemOutcome as ItemOutcome
from pam.catalog.repo.integrity import PlanOutcome as PlanOutcome
from pam.catalog.repo.integrity import companion_group_health as companion_group_health
from pam.catalog.repo.integrity import duplicate_paths_nocase as duplicate_paths_nocase
from pam.catalog.repo.integrity import fact_ids as fact_ids
from pam.catalog.repo.integrity import fact_supersede_edges as fact_supersede_edges
from pam.catalog.repo.integrity import inert_fact_rows as inert_fact_rows
from pam.catalog.repo.integrity import item_outcomes as item_outcomes
from pam.catalog.repo.integrity import known_sha256 as known_sha256
from pam.catalog.repo.integrity import orphan_rows as orphan_rows
from pam.catalog.repo.integrity import plan_outcomes as plan_outcomes
from pam.catalog.repo.integrity import pragma_problems as pragma_problems
from pam.catalog.repo.integrity import rename_session_for_plan as rename_session_for_plan
from pam.catalog.repo.integrity import rename_sessions_for_plan as rename_sessions_for_plan
from pam.catalog.repo.journal import OP_RENAME as OP_RENAME
from pam.catalog.repo.journal import OP_UNDO_RENAME as OP_UNDO_RENAME
from pam.catalog.repo.journal import OPS as OPS
from pam.catalog.repo.journal import RESOLVED_STATES as RESOLVED_STATES
from pam.catalog.repo.journal import JournalEntry as JournalEntry
from pam.catalog.repo.journal import JournalInvariantError as JournalInvariantError
from pam.catalog.repo.journal import JournalRow as JournalRow
from pam.catalog.repo.journal import PrePamName as PrePamName
from pam.catalog.repo.journal import SessionCounts as SessionCounts
from pam.catalog.repo.journal import insert_intents as insert_intents
from pam.catalog.repo.journal import latest_resolved_rows as latest_resolved_rows
from pam.catalog.repo.journal import name_evidence as name_evidence
from pam.catalog.repo.journal import next_session_id as next_session_id
from pam.catalog.repo.journal import pre_pam_names as pre_pam_names
from pam.catalog.repo.journal import resolve as resolve
from pam.catalog.repo.journal import rows_in_state as rows_in_state
from pam.catalog.repo.journal import session_rows as session_rows
from pam.catalog.repo.journal import sessions as sessions
from pam.catalog.repo.journal import split_units as split_units
from pam.catalog.repo.journal import unit_rows as unit_rows
from pam.catalog.repo.journal import unjournaled_unrenamed_items as unjournaled_unrenamed_items
from pam.catalog.repo.plan_items import ITEM_STATUSES as ITEM_STATUSES
from pam.catalog.repo.plan_items import PEOPLE_SOURCE_TABLE as PEOPLE_SOURCE_TABLE
from pam.catalog.repo.plan_items import PlanItemDraft as PlanItemDraft
from pam.catalog.repo.plan_items import PlanItemRow as PlanItemRow
from pam.catalog.repo.plan_items import insert_plan_items as insert_plan_items
from pam.catalog.repo.plan_items import plan_ids_for_items as plan_ids_for_items
from pam.catalog.repo.plan_items import plan_item_status_counts as plan_item_status_counts
from pam.catalog.repo.plan_items import plan_items as plan_items
from pam.catalog.repo.plan_items import plan_items_by_id as plan_items_by_id
from pam.catalog.repo.plan_items import (
    plan_items_with_people_tokens as plan_items_with_people_tokens,
)
from pam.catalog.repo.plan_items import (
    set_plan_item_new_filename as set_plan_item_new_filename,
)
from pam.catalog.repo.plan_items import set_plan_item_status as set_plan_item_status
from pam.catalog.repo.plans import DatedFile as DatedFile
from pam.catalog.repo.plans import PlanFile as PlanFile
from pam.catalog.repo.plans import PlanHeader as PlanHeader
from pam.catalog.repo.plans import create_plan as create_plan
from pam.catalog.repo.plans import dated_files_for_events as dated_files_for_events
from pam.catalog.repo.plans import ext_of as ext_of
from pam.catalog.repo.plans import files_for_planning as files_for_planning
from pam.catalog.repo.plans import plan_already_conforming as plan_already_conforming
from pam.catalog.repo.plans import plan_header as plan_header
from pam.catalog.repo.plans import plan_status as plan_status
from pam.catalog.repo.plans import plan_status_counts as plan_status_counts
from pam.catalog.repo.plans import plans_in_status as plans_in_status
from pam.catalog.repo.plans import set_plan_status as set_plan_status
from pam.catalog.repo.plans import supersede_open_plans as supersede_open_plans
from pam.catalog.repo.reports import CalibrationFace as CalibrationFace
from pam.catalog.repo.reports import DuplicateGroup as DuplicateGroup
from pam.catalog.repo.reports import ReportFact as ReportFact
from pam.catalog.repo.reports import duplicate_groups as duplicate_groups
from pam.catalog.repo.reports import labelled_face_embeddings as labelled_face_embeddings
from pam.catalog.repo.reports import phash_by_sha256 as phash_by_sha256
from pam.catalog.repo.reports import report_facts as report_facts
from pam.catalog.repo.review import EVENT_MARK as EVENT_MARK
from pam.catalog.repo.review import SEARCHABLE_FACT_KINDS as SEARCHABLE_FACT_KINDS
from pam.catalog.repo.review import SORT_DATE as SORT_DATE
from pam.catalog.repo.review import SORT_TRUST_ASC as SORT_TRUST_ASC
from pam.catalog.repo.review import SORTS as SORTS
from pam.catalog.repo.review import TIERS as TIERS
from pam.catalog.repo.review import UNDATED as UNDATED
from pam.catalog.repo.review import EventGroup as EventGroup
from pam.catalog.repo.review import ItemFilter as ItemFilter
from pam.catalog.repo.review import MonthGroup as MonthGroup
from pam.catalog.repo.review import ReviewItem as ReviewItem
from pam.catalog.repo.review import YearGroup as YearGroup
from pam.catalog.repo.review import date_token_value as date_token_value
from pam.catalog.repo.review import event_key as event_key
from pam.catalog.repo.review import group_key as group_key
from pam.catalog.repo.review import nav_tree as nav_tree
from pam.catalog.repo.review import plan_rows as plan_rows
from pam.catalog.repo.review import select_items as select_items
from pam.catalog.repo.review import unit_sizes as unit_sizes
