"""THE SUITE CENSUS (M11 review condition C4) - the one terminal anchor.

Every hand-written control registry in this suite was, until M12, guarded only by the module it
sits in, and a module's own floor dies with the module. M11 answered thirteen consecutive
sole-guardian survivors with cross-module anchors - EIGHT of them at `808d8eb`; the other
results were closed with in-module floors, which is why `m11.md` 5.2's "thirteen" counts results
and not anchors - each of them its own sole guardian (`m11.md` 5.2), and the review then deleted
three unanchored modules whole with the suite green (M1960-M1962). This file replaces seven of
the anchors with ONE inventory, OUTSIDE every guarded module, checked at COLLECTION by
`conftest.py` - so a deleted module, a deleted control or a SWAPPED control is a collection
error before a single test runs. The eighth anchor,
`test_webui_static.py::test_the_node_driver_module_exists_and_registers_its_own_controls`,
STAYS (controller ruling R3, M12 Task 1): half of it checks a `.js` asset and a data list, which
an `ast` census cannot see. It is listed below, so it is no longer a sole guardian.

THE INVENTORY IS HAND-WRITTEN AND NEVER READ BACK FROM THE MODULES IT GUARDS. An inventory that
asks a module what it contains passes that module's deletion. Deleting a control is a deliberate
edit to this file, in the commit that deletes it, with the reason in the message.

IT IS THE AUTHORITATIVE INVENTORY, NOT A SECOND COPY (the M12 gate's I3). For a module that
keeps a hand-written registry (`LOAD_BEARING_CONTROLS` and its siblings), every name the census
lists must ALSO be a key of that registry, read with `ast` - so removing a REGISTRATION while the
test survives fails here, which neither the registry's own membership test (registered name ->
definition) nor a definition check can see.

WHAT IT CANNOT SEE, stated so nobody assumes it: a control that still EXISTS but has been
disarmed (a `skip`, an assertion weakened to `True`). That is the mutation pass's job. And an AST
definition is not proof of COLLECTION - `tests/test_census.py` owns that half, by collecting the
whole tree once in a subprocess.
"""

import ast
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent

# module file -> the test functions it must DEFINE. A method of a `Test*` class is spelled
# "TestClass::test_name". Each entry carries a comment naming the measurement or ruling that
# made it load-bearing. Where a module keeps a registry, the census lists EVERY name that
# registry holds (controller ruling R5), and the reason for each is written beside it THERE.
CENSUS: dict[str, frozenset[str]] = {
    # --- m11.md 2.4's three survivors (M1960-M1962) -----------------------------------------
    # M1960: the hunt itself - Q1-Q4's constructions and the hand-classified route table.
    # Every test in the module; it keeps no registry.
    "test_m11_safety_constructions.py": frozenset(
        {
            "test_every_POST_route_the_server_serves_is_classified_by_hand",
            "test_no_faces_route_reaches_a_plan_item_writer",
            "test_the_video_of_a_live_photo_takes_the_persons_name_the_photograph_earned",
            "test_the_face_rows_cover_the_photograph_and_not_its_video",
            "test_the_queue_offers_exactly_the_faces_the_label_will_write",
            "test_dismiss_ignores_only_the_faces_the_queue_showed",
            "test_a_cluster_whose_every_face_is_named_is_refused_BY_NAME",
        }
    ),
    # M12 Task 14 Step 2: the M12 hunt, Q1-Q5 (Q5 in its own commit). Every test in the module;
    # it keeps no registry, like its M11 sibling above.
    "test_m12_safety_constructions.py": frozenset(
        {
            "test_q1_a_units_two_readings_are_two_events_under_one_month_and_the_nav_says_so",
            "test_q1_decide_filter_on_either_event_decides_the_whole_unit_and_says_so",
            "test_q1_a_low_tier_unit_is_held_back_WHOLE_and_skip_and_reset_still_widen",
            "test_q1_a_companion_decided_the_OTHER_way_is_brought_along_and_named",
            "test_q1_the_unit_decided_through_one_event_applies_whole_and_undoes_exactly",
            "test_q1_attention_is_uniform_across_a_unit_whichever_member_carries_the_reading",
            "test_q2_the_sidecar_ocr_never_sees_is_uncovered_and_still_takes_the_scans_date",
            "test_q2_a_non_primary_members_own_camera_date_is_disclosed",
            "test_q3_the_live_route_table_is_exactly_the_hand_written_one",
            "test_q3_no_faces_route_reaches_a_plan_item_writer",
            "test_q3_decide_naming_ONE_member_writes_the_whole_unit",
            "test_q3_rename_and_restore_of_ONE_member_move_the_whole_units_stem",
            "test_q4_label_writes_exactly_the_faces_the_queue_offered_and_its_counts_are_true",
            "test_q4_dismiss_ignores_exactly_the_faces_the_queue_offered",
            "test_q4_a_cluster_named_above_the_floor_is_refused_BY_NAME_even_with_a_hidden_remainder",
            "test_q5_an_open_plan_applies_the_REVIEWED_name_after_a_later_ocr_change",
            "test_q5_pam_status_read_across_a_writers_commit_reports_before_or_after",
            "test_q5_pam_review_holds_the_lock_so_no_writer_commits_under_its_read_endpoints",
            "test_q5_clarification_8_a_fresh_catalog_reads_pams_own_names_as_filename_dates",
            "test_q5_the_status_instrument_pauses_at_every_read_status_makes_and_marks_one_case",
            "test_the_hash_oracle_reads_the_bytes_and_names_the_path_they_changed_at",
        }
    ),
    # M13 Task 16 Steps 1-2: the chain twice and the M13 hunt, Q1-Q6, plus the instruments'
    # own control. Every test in the module; it keeps no registry, like its M11/M12 siblings.
    "test_m13_safety_constructions.py": frozenset(
        {
            "test_the_instruments_see_a_rename_a_changed_byte_a_unit_and_the_accept_rule",
            "test_step1_the_full_chain_twice_on_a_scratch_tiny_library",
            "test_q1_look_alikes_differ_inside_one_unit_and_no_write_path_selects_by_them",
            "test_q1_no_write_module_reads_the_look_alike_field",
            "test_q1_accept_all_writes_whole_units_of_the_whole_plan_and_apply_moves_them_whole",
            "test_q2_a_unit_one_member_renamed_by_pam_and_one_by_the_user_is_read_per_file",
            "test_q3_the_live_route_table_is_exactly_the_hand_written_one_and_has_no_accept_route",
            "test_q3_every_plan_write_route_writes_the_whole_unit_from_one_member",
            "test_q4_pam_faces_calibrate_reads_and_never_writes_a_byte",
            "test_q5_pam_status_read_across_an_apply_reports_before_or_after",
            "test_q5_pam_verify_holds_the_lock_so_an_apply_under_it_renames_nothing",
            "test_q5_a_touch_after_apply_re_derives_only_what_an_mtime_may_decide",
            "test_q5_a_touch_between_plan_and_apply_refuses_the_whole_unit_and_moves_nothing",
            "test_q5_the_video_rule_after_apply_and_a_touch_compares_with_the_name_before_pam",
            "test_q6_each_review_write_then_a_plain_apply_renames_only_what_was_approved",
            "test_q6_apply_all_cancelled_approves_nothing_and_a_plain_apply_after_it_is_refused",
            "test_q6_a_faces_write_on_a_planned_archive_decides_no_plan_item",
            "test_q6_recovery_never_renames_an_undecided_item",
        }
    ),
    # M14 Task 8a: the chain through the page and the console, and the M14 hunt's Q1-Q4 and Q9.
    # Every test in the module; it keeps no registry, like its M11-M13 siblings. 8b extends it.
    "test_m14_safety_constructions.py": frozenset(
        {
            "test_step1_the_chain_through_the_page_and_through_the_console_is_one_implementation",
            "test_q1_a_list_group_holds_every_member_of_a_unit_whose_sidecar_has_no_capture",
            "test_q1_except_ids_naming_one_member_is_widened_back_and_named_overridden",
            "test_q1_a_shift_click_range_ending_inside_a_unit_writes_and_renames_the_unit_whole",
            "test_q1_a_show_more_boundary_inside_a_unit_selects_the_whole_unit",
            "test_q2_the_members_the_page_never_showed_are_counted_confirmed_and_renamed",
            "test_q3_the_live_route_table_of_pam_start_is_exactly_the_hand_written_one",
            "test_q3_every_read_route_and_the_preview_change_no_catalog_row_and_rename_nothing",
            "test_q3_every_plan_write_route_of_pam_start_writes_the_whole_unit_from_one_member",
            "test_q3_the_two_moving_routes_act_on_whole_units_only",
            "test_q4_label_and_dismiss_through_pam_start_write_exactly_the_faces_the_queue_offered",
            "test_q4_a_cluster_whose_every_face_is_named_is_refused_by_name_through_pam_start",
            "test_q9_every_call_on_the_way_to_the_verified_rename_is_listed_and_passes_the_runs_names",
            "test_q9_the_enumerator_sees_a_call_in_each_spelling",
            "test_q9_each_path_to_the_verified_rename_lists_a_flat_folder_once_per_run",
            "test_q5_a_confirm_screen_open_across_a_decision_in_another_tab_renames_nothing",
            "test_q5_a_selection_preview_held_across_a_read_job_writes_nothing_anywhere",
            "test_q5_the_look_alike_memory_does_not_outlive_its_plan",
            "test_q5_a_page_left_open_across_a_console_scan_after_the_server_stopped_renames_nothing",
            "test_q5_the_os_touches_a_file_between_confirm_and_rename",
            "test_q5_a_history_undo_of_a_session_a_later_page_rename_moved_again_moves_nothing",
            "test_q6_the_pages_apply_renames_only_what_was_approved_and_refuses_a_plan_with_none",
            "test_q6_the_selection_turns_no_undecided_item_into_a_rename",
            "test_q6_the_shortcuts_turn_no_undecided_item_into_a_rename",
            "test_q6_recovery_after_a_page_rename_dies_renames_no_undecided_item",
            "test_q7_the_control_the_persons_own_click_renames_the_four",
            "test_q7_no_request_without_a_person_at_the_page_reaches_a_rename",
            "test_q7_the_control_the_persons_own_undo_click_restores_the_four",
            "test_q7_no_request_without_a_person_at_the_page_reaches_an_undo",
            "test_q8_this_modules_read_spy_sees_a_file_opened_for_reading_by_either_open",
            "test_q8_the_lists_thumbnail_never_reads_a_file_that_became_cloud_only",
            "test_q8_the_folder_chooser_reads_no_file_in_the_folders_it_lists",
            "test_q8_the_read_jobs_progress_does_not_read_a_file_that_became_cloud_only",
            "test_q8_confirm_counts_and_the_rename_read_nothing_of_a_file_that_became_cloud_only",
            "test_the_hunt_holds_every_question_at_its_floor",
        }
    ),
    # M15 Task 15A: the M15 hunt - Q10 on real older workspaces, Q5 for distribution, Q3, Q6, and
    # the citation table's floors. Every test in the module; it keeps no registry (CITED and HUNT
    # are checked by the module itself at collection).
    "test_m15_safety_constructions.py": frozenset(
        {
            "test_q10_the_fixtures_are_the_older_pams_and_hold_what_the_hunt_needs",
            "test_q10_an_open_plan_an_older_pam_wrote_renames_only_its_approval_and_undoes",
            "test_q10_an_interrupted_run_an_older_pam_left_is_recovered_reported_and_renames_nothing",
            "test_q10_this_pams_forward_migration_is_refused_by_an_older_reader_and_nothing_changes",
            "test_q10_the_readme_states_the_pre_1_0_downgrade_gap",
            "test_q3_a_confirmed_pam_update_changes_no_catalog_byte_and_no_ledger_line",
            "test_q5_pam_update_while_a_real_pam_start_serves_is_refused_before_any_request",
            "test_q5_the_installer_while_pam_apply_waits_at_its_prompt_is_refused_and_apply_goes_on",
            "test_q6_the_retry_route_after_a_cloud_only_skip_renames_nothing_without_a_new_approval",
            "test_the_hunt_holds_every_question_at_its_floor",
        }
    ),
    # M1961: F3's archive-root proof - the function, the WIRING (M1953), the positive direction
    # (so the refusal cannot be "always refuse") and the end-to-end byte count.
    "test_faces_export.py": frozenset(
        {
            "test_the_destination_being_under_the_archive_root_RAISES_and_writes_nothing",
            "test_run_export_ITSELF_refuses_when_the_workspace_sits_inside_the_archive",
            "test_a_destination_outside_the_archive_root_is_allowed",
            "test_the_shipped_destination_is_the_workspace_and_never_the_archive",
        }
    ),
    # M1962: the normative budget (design 0), the budget applied to `pam/` (M1453), the 310
    # ceiling pinned at its measurement (M1941), and the counter's calibration.
    "test_line_budget.py": frozenset(
        {
            "test_the_budget_is_the_number_the_spec_states",
            "test_no_file_in_pam_is_over_the_budget",
            "test_every_ruled_over_budget_file_is_really_over_it",
            "test_the_counter_reproduces_the_numbers_v0_1_m7_published",
        }
    ),
    # M14 Task 0A (M13 C2, R2): `pam verify`'s three statements in one pass, held to the
    # pre-rewrite SQL and to literal tables from design 7.5. Every test in the module; it keeps no
    # registry. M2682 (Task 0A review): the module could be deleted whole with the suite green, and
    # it is the sole guardian of M2666, M2668, M2669 and M2670-M2672.
    "test_integrity_one_pass.py": frozenset(
        {
            "test_the_item_outcomes_agree_with_the_reference_on_the_journal_fixture",
            "test_a_two_resolution_item_reports_its_newest_resolution_literal_table",
            "test_an_intent_row_is_excluded_on_both_sides",
            "test_the_plan_outcomes_agree_with_the_reference_for_every_status_set",
            "test_the_after_apply_plan_counts_only_journaled_applied_items_literal_table",
            "test_the_after_undo_plan_is_filtered_out_by_its_status",
            "test_an_empty_status_set_returns_nothing",
            "test_the_companion_health_agrees_with_the_reference_on_the_group_fixture",
            "test_a_group_with_zero_members_is_reported_literal_table",
            "test_a_primary_that_is_not_a_member_is_reported_not_a_member",
            "test_an_empty_catalog_gives_empty_results",
            "test_each_statement_finishes_inside_two_seconds_at_twenty_thousand_items",
            "test_the_scale_results_are_the_hand_computed_shape",
            "test_this_module_imports_nothing_from_the_integrity_module_but_the_three_functions",
            "test_each_oracle_reads_its_reference_statement_and_never_calls_production",
        }
    ),
    # M14 Task 0B (M13 C1, V1 + X1): every content reader re-checks the LIVE placeholder
    # attributes. Every test in the module; it keeps no registry. The spy's own control
    # (`test_the_spy_sees_a_local_file_on_every_hook`) is the reason the module is here: deleting
    # a control assertion leaves every "never read" assertion green (T0B mutation 8).
    "test_live_placeholder.py": frozenset(
        {
            "test_placeholder_now_reads_the_live_attributes_and_an_oserror_is_false",
            "test_the_enrich_line_is_the_plans_copy_and_absent_at_zero",
            "test_the_spy_sees_a_local_file_on_every_hook",
            "test_verify_deep_skips_and_counts_a_file_that_became_cloud_only",
            "test_enrich_phash_skips_counts_and_stores_nothing_for_a_cloud_only_file",
            "test_a_thumbnail_request_never_reads_a_file_that_became_cloud_only",
            "test_the_raw_branch_never_extracts_a_preview_from_a_cloud_only_raw",
            "test_the_scanner_does_not_read_a_file_that_became_cloud_only_after_its_walk",
            # M14 Task 0B review: M2700's closing test.
            "test_a_cloud_only_files_local_twin_in_a_later_batch_is_still_enriched",
            # M14 Task 0B review: M2713's closing test.
            "test_the_offline_attribute_is_restored_even_when_the_test_fails",
        }
    ),
    # M14 Task 0C (M13 C3, R3; OD-6 (a)): migration 0008's NOCASE index under `repo.path_owner`.
    # Every test in the module; it keeps no registry. The case-variant test is what kills "drop
    # COLLATE NOCASE from path_owner" (the BINARY unique index would then serve it, fast and wrong);
    # the EXPLAIN test is what kills "drop the index from the migration".
    "test_path_owner_index.py": frozenset(
        {
            "test_a_case_variant_owner_is_found",
            "test_an_unowned_path_has_no_owner",
            "test_path_owner_reads_through_the_nocase_index",
            "test_the_index_is_rel_path_under_nocase",
            "test_an_existing_0007_catalog_migrates_to_0008_and_its_rows_are_found",
            # M14 Task 0C review: M2719's closing test (LIKE is not equality under NOCASE).
            "test_a_path_differing_only_at_a_like_wildcard_is_not_owned",
            # M14 Task 0C review: M2721's closing test (the EXPLAIN predicate's control).
            "test_the_plan_predicate_rejects_a_scan_that_names_the_index",
        }
    ),
    # M14 Task 0E (X4): one listing per folder per run. The equivalence property against an
    # independent oracle and its coverage check (Step 1), the spy's control and one count per
    # rename path (Step 3), interference watched at os.rename incl. the two platform-gated
    # tests and their guard (Step 4), stale "taken" and a failed rename (Step 5), and the
    # crash that leaves recovery its own memory (Step 7). Every test in the module.
    "test_directory_names.py": frozenset(
        {
            "test_memory_agrees_with_a_fresh_listing_at_every_step",
            "test_the_generated_cases_reach_every_answer",
            "test_without_a_runs_memory_every_rename_lists_the_folder",
            "test_apply_lists_a_flat_folder_once_per_run",
            "test_undo_lists_a_flat_folder_once_per_run",
            "test_apply_puts_a_failed_unit_back_without_listing_again",
            "test_undo_puts_a_failed_unit_back_without_listing_again",
            "test_recovery_puts_a_split_unit_back_listing_the_folder_once",
            "test_a_file_created_at_the_exact_target_after_the_listing_is_refused_before_rename",
            "test_a_case_variant_created_after_the_listing_is_refused_on_windows",
            "test_a_case_variant_created_mid_run_is_not_refused_on_a_case_sensitive_filesystem",
            "test_the_platform_gated_tests_are_exactly_the_hand_written_set",
            "test_a_target_lstat_cannot_answer_is_refused_and_never_renamed",
            "test_the_case_sensitive_residual_reaches_os_rename_and_overwrites_nothing",
            "test_a_name_deleted_after_the_listing_is_used_after_exactly_one_fresh_listing",
            "test_a_name_still_taken_is_refused_after_one_fresh_listing_with_todays_sentence",
            "test_a_failed_os_rename_changes_no_memory_first_and_forgets_the_folder",
            "test_a_crash_after_a_rename_leaves_recovery_its_own_fresh_memory",
        }
    ),
    # --- every absorbed anchor, under the module it anchored ------------------------------------
    # M1831: anchored from test_faces_cluster.py until M12 Task 1. All eight of the
    # anchor's names were already in this module's LOAD_BEARING_CONTROLS; the census lists all 15
    "test_config.py": frozenset(
        {
            "test_TierBounds_low_and_high_have_NO_dataclass_defaults",
            "test_a_caption_floor_of_0_9_is_STILL_REFUSED",
            "test_a_faces_floor_ABOVE_the_removed_low_is_accepted",
            "test_a_faces_floor_outside_0_to_1_is_STILL_refused",
            "test_a_key_outside_a_rows_vocabulary_is_refused_BY_NAME_with_the_reason",
            "test_a_misspelled_field_in_a_tier_row_is_STILL_refused",
            "test_a_partial_caption_override_keeps_every_key_it_does_not_name",
            "test_an_ocr_row_is_still_ordered_across_all_three_of_its_thresholds",
            "test_an_unknown_tier_name_is_refused_rather_than_silently_stored",
            "test_each_printed_row_takes_exactly_the_keys_the_spec_prints_for_it",
            "test_the_faces_row_design_11_prints_names_ONLY_a_floor",
            "test_the_ocr_row_refuses_max_tier_when_no_confidence_with_the_M12_reason",
            "test_the_row_vocabulary_is_the_one_the_dataclass_really_takes",
            "test_the_tiers_block_design_11_prints_is_a_configuration_pam_ACCEPTS",
            "test_the_tiers_rows_pam_reads_are_EXACTLY_the_three_design_11_prints",
        }
    ),
    # M1796: anchored from test_cluster.py until M12 Task 1. Three of the anchor's
    # names were unregistered here and were MOVED into LOAD_BEARING_CONTROLS (ruling R6); the
    # anchor's last name was anchor #2 itself, deleted by ruling R2
    "test_faces_cluster.py": frozenset(
        {
            "test_a_face_that_stops_grouping_is_UNCLUSTERED_rather_than_left_stale",
            "test_a_faces_floor_ABOVE_THE_REMOVED_LOW_reaches_the_command",
            "test_a_second_width_that_is_BELOW_THE_FLOOR_is_not_a_refusal",
            "test_above_the_tested_ceiling_the_command_refuses_and_writes_nothing",
            "test_an_interruption_during_the_write_leaves_every_cluster_id_untouched",
            "test_embeddings_of_two_widths_are_refused_even_under_one_generation",
            "test_nothing_is_written_when_the_epsilon_is_missing",
            "test_re_running_never_puts_STRANGERS_into_a_cluster_a_user_has_NAMED",
            "test_the_preamble_is_announced_BEFORE_the_clustering_runs",
            "test_the_refusal_suggests_no_value_of_its_own",
            "test_the_two_reads_partition_the_table",
            "test_two_model_generations_are_refused_with_both_named_and_a_route_out",
            "test_two_runs_over_an_unchanged_catalog_give_the_SAME_PARTITION",
            "test_with_no_cluster_epsilon_the_command_refuses_and_NAMES_THE_KEY",
        }
    ),
    # M1959: anchored from test_faces_purge.py until M12 Task 1. Keeps no registry
    "test_faces_round_trip.py": frozenset(
        {
            "test_the_fake_detector_is_not_uniform_across_the_corpus",
            "test_the_round_trip_enrich_cluster_label_export_purge_and_enrich_again",
        }
    ),
    # M1937: anchored from test_tokens.py until M12 Task 1. Keeps no registry
    "test_people_naming.py": frozenset(
        {
            "test_A16_no_stale_people_rule_exists",
            "test_a_BELOW_FLOOR_face_contributes_nothing_and_the_floor_is_the_parameter",
            "test_a_HIDDEN_person_contributes_nothing",
            "test_a_person_with_two_faces_in_one_photograph_is_named_once",
            "test_a_reviewed_away_face_contributes_nothing",
            "test_an_UNNAMED_clustered_face_never_names_a_photograph",
            "test_max_people_caps_the_token_and_the_source_id_is_the_lowest_id_NAMED",
            "test_max_people_zero_is_an_ABSENT_token_not_an_empty_one",
            "test_no_cap_sentence_when_the_template_does_not_emit_people",
            "test_people_NOT_in_the_name_earn_no_five",
            "test_people_are_ordered_by_the_value_that_REACHES_the_filename",
            "test_people_is_dropped_THIRD_after_scene_and_place",
            "test_q_does_not_find_a_HIDDEN_person",
            "test_q_does_not_find_a_below_floor_face_and_reads_the_floor_it_is_given",
            "test_the_cap_leaves_attention_unchanged",
            "test_the_people_token_is_spelled_from_A14",
        }
    ),
    # M1836: anchored from test_webui_api.py until M12 Task 1; one registration
    # MOVED in (ruling R6)
    "test_webui_faces.py": frozenset(
        {
            "test_a_crop_never_reaches_the_thumbnail_extraction_chain",
            "test_a_crop_of_a_portrait_photograph_is_the_pixels_the_detector_measured",
            "test_a_file_that_became_a_placeholder_since_the_scan_is_refused_and_never_opened",
            "test_a_geometry_recorded_under_another_orientation_is_refused",
            "test_a_geometry_whose_height_alone_disagrees_is_refused",
            "test_a_geometry_whose_width_alone_disagrees_is_refused",
            "test_label_attaches_every_face_in_the_cluster_and_says_how_many_of_each",
            "test_ping_reports_no_plan_without_asking_the_catalog_about_one",
            "test_the_crop_module_names_the_thumbnail_module_nowhere_in_its_source",
            "test_the_enricher_and_the_crop_endpoint_call_one_shared_upright_function",
            "test_the_fixture_can_tell_the_two_implementations_apart",
            "test_the_placeholder_spy_would_have_seen_an_open",
        }
    ),
    # M1878: anchored from test_webui_api.py until M12 Task 1; four
    # registrations MOVED in (ruling R6)
    "test_webui_faces_manage.py": frozenset(
        {
            "test_assign_with_a_null_person_clears_rather_than_sets",
            "test_dismiss_on_a_remainder_ignores_only_it",
            "test_dismiss_refuses_a_named_cluster_and_writes_nothing",
            "test_ignored_lists_both_states_with_counts_and_no_floor",
            "test_label_names_exactly_the_faces_shown_and_leaves_a_below_floor_face_unnamed",
            "test_label_of_a_fully_named_cluster_is_refused_naming_the_person",
            "test_label_on_a_remainder_names_only_it_and_never_moves_a_named_face",
            "test_label_then_unassigning_its_face_ids_restores_every_face_and_no_other",
            "test_merge_moves_faces_and_keeps_the_surviving_persons_label",
            "test_merge_refuses_true_as_into_when_person_one_is_somebody_else",
            "test_person_hidden_is_toggled_without_touching_the_filename_label",
            "test_person_lists_exactly_that_persons_unreviewed_faces_below_floor_included",
            "test_person_rename_onto_another_persons_label_is_refused_not_merged",
            "test_persons_counts_only_faces_that_are_not_reviewed_away",
            "test_state_distinguishes_not_a_face_from_dismissed",
            "test_state_null_clears_a_review_state",
            "test_the_queue_offers_a_remainder_with_only_its_own_faces_in_the_sample",
        }
    ),
    # M1879: anchored from test_webui_api.py until M12 Task 1; one registration
    # MOVED in (ruling R6)
    "test_webui_faces_split.py": frozenset(
        {
            "test_a_cluster_holding_two_embedding_widths_is_refused_with_a_sentence",
            "test_a_stale_undo_token_cannot_reverse_a_later_split_of_the_same_cluster",
            "test_a_write_that_fails_midway_leaves_every_cluster_id_unchanged",
            "test_confirm_after_the_cluster_changed_is_refused_and_writes_nothing",
            "test_confirm_applies_the_previewed_partition_with_fresh_ids_above_every_row",
            "test_confirm_without_a_preview_changes_nothing",
            "test_faces_that_never_separate_say_so_and_offer_nothing_to_confirm",
            "test_fresh_ids_clear_an_offerable_cluster_holding_the_largest_id",
            "test_only_the_most_recent_split_can_be_undone",
            "test_preview_separates_only_at_a_later_step_and_reports_that_steps_epsilon",
            "test_split_of_a_remainder_partitions_only_its_unnamed_faces",
            "test_undo_after_a_face_row_vanished_is_a_sentence_not_a_crash",
            "test_undo_after_membership_changed_is_refused_and_writes_nothing",
            "test_undo_restores_every_faces_prior_cluster_id",
        }
    ),
    # --- every hand-written registry, under its own module -------------------------------------
    # its LOAD_BEARING_CONTROLS: every name it registers; the reasons live there
    "private/test_bench_rows.py": frozenset(
        {
            "test_the_enrich_row_documents_that_it_is_TWO_INVOCATIONS_and_really_is",
        }
    ),
    # its LOAD_BEARING_CONTROLS: every name it registers; the reasons live there
    "test_ci_lanes.py": frozenset(
        {
            "test_a_REQUIRED_faces_stack_that_is_unavailable_fails_instead_of_skipping",
            "test_the_control_WITHOUT_the_requirement_the_same_collection_skips_and_passes",
            "test_the_faces_job_REQUIRES_the_stack_rather_than_skipping_it",
            "test_the_faces_job_exists_installs_the_extra_and_runs_the_marked_tests",
            "test_the_faces_job_fetches_the_same_pinned_pack_the_product_expects",
            "test_the_faces_job_installs_exiftool_and_gates_on_the_hand_listed_tests_PASSING",
            "test_the_faces_job_is_not_allowed_to_fail",
            "test_the_faces_job_never_asks_pam_to_download_anything",
            "test_the_faces_jobs_required_list_is_a_literal_and_names_every_marked_test",
            "test_the_ocr_job_exists_installs_both_extras_and_runs_the_marked_tests",  # M12 Task 12 B10
            "test_the_ocr_job_is_not_allowed_to_fail",  # M12 Task 12 B10
            "test_the_ocr_pack_step_verifies_q1s_three_hashes_from_the_recorded_source",  # M12 Task 12 B10
            "test_the_ocr_job_installs_nothing_from_the_wheel_and_proves_it",  # M12 Task 12 B10
            "test_the_ocr_job_REQUIRES_the_stack_and_points_it_at_the_pack_it_verified",  # M12 Task 12 B10
            "test_the_ocr_job_installs_exiftool_and_gates_on_the_hand_listed_tests_PASSING",  # M12 Task 12 B10
            "test_the_ocr_jobs_required_list_is_a_literal_and_names_every_marked_test",  # M12 Task 12 B10
            "test_a_REQUIRED_ocr_stack_that_is_unavailable_fails_instead_of_skipping",  # M12 Task 12 B10
            "test_the_control_WITHOUT_the_ocr_requirement_the_same_collection_skips_and_passes",  # M12 Task 12 B10
            "test_the_hand_rolled_reader_agrees_with_a_real_parser_about_every_command",
            "test_the_lane_check_can_tell_an_enumerated_lane_from_an_excluding_one",
            "test_the_lane_is_unconditional_and_runs_on_pushes_and_pull_requests",
            "test_the_narrowing_check_can_see_a_command_that_really_is_narrowed",
            "test_the_required_passed_script_counts_ONLY_a_pass",
            "test_the_required_passed_script_matches_a_base_name_and_EVERY_parametrization",
            "test_the_yaml_check_can_see_the_defect_it_exists_for",
        }
    ),
    # the clustering arithmetic; anchor #1 and its registration deleted at M12 Task 1
    "test_cluster.py": frozenset(
        {
            "test_a_BORDER_point_joins_one_cluster_and_never_BRIDGES_two",
            "test_a_chain_whose_ends_are_outside_epsilon_is_still_ONE_cluster",
            "test_a_pair_EXACTLY_at_epsilon_is_inside_it",
            "test_a_run_larger_than_one_block_gives_the_same_answer_as_the_reference",
            "test_pass_one_never_compares_more_than_BLOCK_ROWS_rows_at_once",
            "test_the_free_phase_really_produces_every_outcome_it_is_supposed_to_cover",
            "test_the_hand_computed_distances_are_what_the_definition_gives",
            "test_the_hand_computed_vectors_are_exactly_unit_length",
            "test_the_oracle_is_SCALE_INVARIANT_which_is_the_half_that_makes_it_cosine",
            "test_the_partition_does_not_change_when_a_vector_arrives_unnormalised",
            "test_the_published_estimate_is_not_off_by_ORDERS_OF_MAGNITUDE_against_a_real_clock",
            "test_the_tested_ceiling_IS_THE_TOP_RUNG_THAT_WAS_ACTUALLY_RUN",
        }
    ),
    # its LOAD_BEARING_CONTROLS: every name it registers; the reasons live there
    "test_doctor.py": frozenset(
        {
            "test_a_machine_without_the_faces_extra_is_a_correctly_installed_machine",
            "test_the_faces_check_reaches_no_archive_and_imports_neither_package",
            "test_a_machine_without_the_ocr_extra_is_a_correctly_installed_machine",  # M12 Task 12 B11
            "test_the_ocr_line_names_both_versions_the_platform_and_python",  # M12 Task 12 B11
            "test_the_ocr_check_is_optional_and_imports_neither_package",  # M12 Task 12 B11
            "test_the_faces_line_names_what_it_deliberately_does_not_check",
        }
    ),
    # its LOAD_BEARING_CONTROLS: every name it registers; the reasons live there
    "test_enrich_runner.py": frozenset(
        {
            "test_a_face_fact_leaves_inferred_facts_holding_EXACTLY_the_marker",
            "test_every_marker_counts_the_rows_the_table_really_holds",
            "test_the_fact_writer_runs_only_on_the_calling_thread",
        }
    ),
    # its LOAD_BEARING_CONTROLS: every name it registers; the reasons live there
    "test_events_grouping.py": frozenset(
        {
            "test_across_the_seeds_the_generator_produces_both_single_and_many_event_worlds",
            "test_across_the_seeds_the_generator_produces_files_with_and_without_gps",
            "test_group_events_does_not_mutate_the_list_it_was_given",
            "test_nothing_in_the_events_package_imports_a_third_party_module",
            "test_the_filesystem_tripwire_is_armed",
            "test_the_generated_worlds_actually_exercise_splitting",
            "test_the_property_tests_generator_lists_are_not_empty",
        }
    ),
    # its LOAD_BEARING_CONTROLS: every name it registers; the reasons live there
    "test_f12_unit_membership.py": frozenset(
        {
            "test_a_no_op_item_outside_the_actionable_statuses_is_never_resolved_to_applied",
            "test_a_partially_conforming_unit_gets_an_item_for_every_member",
            "test_a_refused_no_op_item_keeps_its_refusal_and_is_not_resolved",
            "test_an_external_rename_of_a_no_op_member_is_refused_with_a_route",
            "test_apply_resolves_the_no_op_item_without_a_journal_row_or_a_rename",
            "test_the_state_after_one_apply_and_an_editor_export_keeps_its_unit_whole",
        }
    ),
    # its LOAD_BEARING_CONTROLS: every name it registers; the reasons live there
    "test_faces_enricher.py": frozenset(
        {
            "test_BUILDERS_gains_faces_after_phash_and_geocode_in_that_order",
            "test_a_degenerate_box_embeds_to_nothing_rather_than_to_a_vector",
            "test_a_detection_that_embeds_to_nothing_is_dropped_logged_and_not_counted",
            "test_a_differing_model_NAME_is_refused_too_not_only_a_differing_version",
            "test_a_placeholder_video_is_refused_by_each_operand_on_its_own",
            "test_a_real_photograph_produces_one_marker_and_one_face_matching_the_fixture",
            "test_a_sub_floor_detection_is_STORED_and_the_floor_is_applied_when_it_is_READ",
            "test_every_disableable_enricher_names_the_key_that_turns_it_on",
            "test_refresh_still_works_for_an_enricher_without_a_sink",
            "test_the_candidate_threshold_sits_below_the_configured_floor_which_is_what_A11_needs",
            "test_the_enricher_re_types_none_of_the_packs_measured_constants",
            "test_the_faces_builder_takes_the_shared_decoder_and_survives_an_unset_model_dir",
            "test_the_generation_refusal_is_keyed_on_the_sink_and_not_on_the_name_faces",
            "test_the_literal_walk_would_actually_see_a_re_typed_constant",
            "test_the_open_watch_would_actually_see_an_open",
            "test_the_stored_embedding_is_128_float32s_and_is_unit_LENGTH",
            "test_wants_refuses_a_cloud_placeholder_and_no_file_is_ever_opened",
        }
    ),
    # its LOAD_BEARING_CONTROLS: every name it registers; the reasons live there
    "test_faces_model.py": frozenset(
        {
            "test_a_test_whose_NAME_mentions_faces_is_not_skipped_by_the_marker_hook",
            "test_describe_never_loads_a_session",
            "test_describe_refuses_a_pack_whose_bytes_changed_under_its_manifest",
            "test_describe_says_what_is_unset_rather_than_raising_when_model_dir_is_empty",
            "test_every_onnx_session_pins_the_cpu_execution_provider",
            "test_load_builds_both_sessions_and_pins_the_cpu_execution_provider",
            "test_no_module_under_pam_imports_onnxruntime_or_numpy_at_module_scope",
            "test_the_detector_reproduces_the_recorded_boxes_scores_and_landmarks",
            "test_the_faces_extra_never_reaches_the_eager_import_closure",
            "test_the_faces_marked_tests_are_exactly_the_ones_written_down",
            "test_load_writes_N9s_receipt_from_what_each_SESSION_reports",
            "test_the_missing_weight_file_sentence_names_where_the_file_comes_from",
            "test_the_real_runtime_writes_the_receipt_with_the_real_providers",
            "test_the_identity_digest_moves_when_a_constant_of_PAMS_OWN_moves",
            "test_the_identity_digest_moves_when_any_answer_changing_constant_moves",
            "test_the_module_scope_walk_can_see_an_import_it_is_supposed_to_catch",
            "test_the_provider_walk_would_notice_a_session_built_without_one",
            "test_the_recogniser_reproduces_the_recorded_embedding_and_would_see_a_channel_swap",
            "test_with_no_pack_every_marked_test_skips_and_none_of_them_runs",
        }
    ),
    # the Node driver's registry. Also anchored by test_webui_static.py's node-driver
    # anchor, which STAYS (ruling R3) for its .js half; this entry makes the test-name half terminal
    "test_faces_page.py": frozenset(
        {
            "test_faces_js_still_exports_the_seam_the_driver_reaches_through",
            "test_the_browser_never_reaches_the_commonjs_seam",
            "test_the_driver_and_the_exports_agree_about_what_is_reachable",
            "test_faces_js_exports_exactly_the_registered_seam",
            "test_the_faces_export_equality_refuses_an_extra_name_and_a_missing_one",
            "test_the_driver_names_every_transition_this_task_owes",
            "test_the_node_driver_executes_every_transition_it_claims",
            "test_the_node_driver_fails_when_merge_posts_to_the_surviving_persons_path",
            "test_the_node_driver_fails_when_not_a_face_is_posted_as_dismissed",
            "test_the_node_driver_fails_when_not_the_same_person_confirms_without_a_preview",
            "test_the_node_driver_fails_when_skip_starts_writing_to_the_server",
            "test_the_node_driver_fails_when_the_page_proposes_a_name",
            "test_the_node_driver_reports_a_script_it_cannot_even_load",
            "test_the_node_driver_ships_and_is_not_empty",
            "test_the_required_transition_list_is_not_empty",
        }
    ),
    # its LOAD_BEARING_CONTROLS: every name it registers; the reasons live there
    "test_faces_repo.py": frozenset(
        {
            "test_insert_face_stores_a_detection_below_every_plausible_floor",
            "test_merging_a_person_with_themselves_is_refused",
            "test_plan_items_with_people_tokens_finds_items_by_provenance_not_by_text",
            "test_set_person_names_every_face_in_the_cluster_but_not_a_reviewed_one",
            "test_set_person_names_only_faces_at_the_floor_and_the_floor_is_inclusive",
            "test_the_floor_hides_and_then_recovers_the_same_stored_row",
            "test_upsert_person_matches_case_insensitively_and_keeps_the_first_spelling",
        }
    ),
    # its LOAD_BEARING_CONTROLS: every name it registers; the reasons live there
    "test_faces_sink.py": frozenset(
        {
            "test_a_photograph_with_no_face_is_covered_and_is_skipped_on_the_second_run",
            "test_nothing_from_a_half_written_photograph_is_visible_when_the_sink_fails",
            "test_the_commit_walk_would_actually_see_a_commit",
            "test_the_embedding_never_passes_through_fact_value_json",
            "test_the_marker_count_is_the_runners_and_not_the_enrichers_claim",
            "test_the_runner_counts_what_it_stored_PER_KIND_and_the_kinds_sum_to_the_total",
            "test_the_sink_table_is_exactly_A7s",
            "test_the_write_step_never_commits",
            "test_three_detections_write_one_marker_and_three_rows_spelled_out_by_hand",
        }
    ),
    # its LOAD_BEARING_CONTROLS: every name it registers; the reasons live there
    "private/test_ht_m10_doc.py": frozenset(
        {
            "test_every_register_entry_reached_the_owners_page",
            "test_every_step_is_PRESENT_AND_NOT_GUTTED",
            "test_the_nav_steps_do_not_promise_a_SCENE_LABEL_no_event_can_carry",
            "test_the_pam_output_this_document_quotes_is_what_pam_really_prints",
            "test_the_preamble_carries_the_conventions_that_are_conditions_not_style",
        }
    ),
    # its LOAD_BEARING_CONTROLS: every name it registers; the reasons live there
    "private/test_ht_m11_doc.py": frozenset(
        {
            "test_LEG_B_ASKS_THE_MISLABEL_QUESTION_WITH_A_NUMBER_AND_DRIVES_EVERY_REPAIR",
            "test_every_register_entry_reached_the_owners_page",
            "test_every_step_is_PRESENT_AND_NOT_GUTTED",
            "test_no_POWERSHELL_block_carries_an_angle_bracket_at_all",
            "test_the_crop_cache_is_asked_ABOUT_and_never_hunted_FOR",
            "test_the_four_carry_forwards_are_recorded_and_never_TICKED",
            "test_the_pam_output_this_document_quotes_is_what_pam_really_prints",
            "test_the_provider_the_page_promises_is_the_provider_pam_PINS",
        }
    ),
    # M12 Task 14 Step 4: HT-M12's pin. Its LOAD_BEARING_CONTROLS registers EVERY test in the
    # module, so this lists every test; the reasons live there
    "private/test_ht_m12_doc.py": frozenset(
        {
            "test_the_document_exists_names_itself_and_is_not_a_stub",
            "test_the_preamble_carries_the_conventions_that_are_conditions_not_style",
            "test_the_preamble_states_the_placeholder_rule_in_BOTH_of_its_halves",
            "test_no_runnable_block_carries_a_placeholder_that_looks_like_a_value",
            "test_no_POWERSHELL_block_carries_an_angle_bracket_at_all",
            "test_a_register_entry_reaches_the_owners_page",
            "test_the_register_has_not_quietly_SHRUNK_and_neither_file_is_the_oracle",
            "test_every_register_row_names_the_STEP_this_module_expects",
            "test_a_step_is_PRESENT_AND_NOT_GUTTED",
            "test_the_steps_are_EXACTLY_the_plans_and_IN_ORDER",
            "test_all_four_legs_are_present_and_none_is_a_heading_over_nothing",
            "test_the_document_points_at_the_copy_and_never_at_the_original",
            "test_a_command_the_plan_gives_verbatim_is_in_a_RUNNABLE_block",
            "test_a_rule_the_plan_states_reaches_the_page",
            "test_every_NOT_RUN_condition_the_plan_names_is_on_the_page",
            "test_LEG_A_PROVES_THE_CAPTURE_CAN_SEE_BEFORE_IT_TRUSTS_A_ZERO",
            "test_LEG_C_PINS_THE_TEMPLATE_ON_EVERY_PLAN_AND_UNDOES_TO_ITS_OWN_SNAPSHOT",
            "test_LEG_D_ENDS_EXACT_AGAINST_THE_ORIGINAL_AND_NEVER_SETS_THE_KEY",
            "test_the_carry_forwards_are_recorded_and_never_TICKED",
            "test_the_record_is_asked_for_in_NUMBERS_and_names_where_it_goes",
            "test_the_pack_the_page_names_is_the_pack_pam_PINS",
            "test_the_provider_the_page_promises_is_the_provider_pam_PINS",
            "test_the_pam_output_this_document_quotes_is_what_pam_really_prints",
            "test_the_M11_document_and_its_pin_are_left_alone",
            "test_the_parametrized_controls_are_WIRED_to_every_row",
            "test_no_extent_control_in_this_file_can_be_deleted_without_saying_so",
            "test_a_register_phrase_is_IN_A_STEP_its_row_names",
            "test_the_two_snapshots_are_WRITTEN_where_and_when_the_plan_says",
            "test_blocks_FILTERS_by_language",
            "test_the_two_SAFETY_ORDERS_inside_a_step_are_pinned",
            "test_the_lists_are_at_least_as_long_as_the_census_says",
        }
    ),
    # M13 Task 16 Step 5: HT-M13's pin. Its LOAD_BEARING_CONTROLS registers EVERY test in the
    # module, so this lists every test; the reasons live there
    "private/test_ht_m13_doc.py": frozenset(
        {
            "test_the_document_exists_names_itself_and_is_not_a_stub",
            "test_every_leg_is_present_IN_ORDER_and_the_optional_ones_say_so",
            "test_a_step_is_PRESENT_AND_NOT_GUTTED",
            "test_the_steps_are_EXACTLY_the_plans_and_IN_ORDER",
            "test_each_step_sits_under_its_own_leg",
            "test_a_register_entry_is_IN_THE_STEP_the_plan_puts_it",
            "test_every_register_row_names_the_STEP_this_module_expects",
            "test_the_ids_on_the_page_are_EXACTLY_the_register_and_the_carried",
            "test_the_carried_items_are_carried_and_never_TICKED_and_H17_H19_are_retired",
            "test_a_command_the_plan_gives_is_in_a_RUNNABLE_block",
            "test_a_rule_the_plan_states_reaches_the_page",
            "test_the_pam_output_this_document_quotes_is_what_pam_really_prints",
            "test_the_document_points_at_the_copy_and_never_at_the_original",
            "test_no_runnable_block_carries_a_placeholder_or_an_angle_bracket",
            "test_the_snapshot_is_taken_WITH_ITS_COUNT_before_anything_is_copied_in",
            "test_LEG_B_puts_the_times_back_BEFORE_the_undo_and_ends_exact",
            "test_LEG_A_proves_K_then_the_refusal_then_undoes_newest_first_and_ends_exact",
            "test_the_close_proves_the_archive_the_catalog_and_the_config",
            "test_the_record_is_asked_for_and_with_the_stand_ins",
            "test_the_page_carries_no_real_personal_information",
            "test_the_M12_document_and_its_pin_are_left_alone",
            "test_the_lists_are_at_least_as_long_as_the_census_says",
            "test_the_parametrized_controls_are_WIRED_to_every_row",
            "test_no_extent_control_in_this_file_can_be_deleted_without_saying_so",
        }
    ),
    # M14 Task 9 Step 1: HT-M14's pin. Its LOAD_BEARING_CONTROLS registers EVERY test in the
    # module, so this lists every test; the reasons live there
    "private/test_ht_m14_doc.py": frozenset(
        {
            "test_the_document_exists_names_itself_and_is_not_a_stub",
            "test_every_leg_is_present_IN_ORDER_and_the_optional_ones_say_so",
            "test_a_step_is_PRESENT_AND_NOT_GUTTED",
            "test_the_steps_are_EXACTLY_the_plans_and_IN_ORDER",
            "test_each_step_sits_under_its_own_leg",
            "test_a_register_entry_is_IN_THE_STEP_the_plan_puts_it",
            "test_every_register_row_names_the_STEP_this_module_expects",
            "test_the_ids_on_the_page_are_EXACTLY_the_register_and_the_carried",
            "test_the_carried_items_are_carried_and_never_TICKED",
            "test_a_command_the_plan_gives_is_in_a_RUNNABLE_block",
            "test_a_rule_the_plan_states_reaches_the_page",
            "test_the_pam_output_this_document_quotes_is_what_pam_really_prints",
            "test_a_quote_with_a_value_in_it_matches_the_modules_FORMAT",
            "test_the_document_points_at_the_copy_and_never_at_the_original",
            "test_no_runnable_block_carries_a_placeholder_or_an_angle_bracket",
            "test_the_snapshot_is_taken_WITH_ITS_COUNT_before_anything_runs_or_is_copied_in",
            "test_LEG_B_writes_the_count_down_BEFORE_pam_status_and_compares_with_the_PROGRESS_LINE",
            "test_LEG_C_proves_K_twice_then_undoes_NEWER_from_Done_then_OLDER_from_History",
            "test_every_lock_taking_command_comes_AFTER_a_Ctrl_C",
            "test_LEG_D_opens_the_OLD_tab_before_the_rename_and_counts_POSTs_after_clicking_it",
            "test_LEG_E_precondition_FAILS_CLOSED_before_the_page_is_looked_at",
            "test_the_close_proves_the_archive_the_catalog_and_the_config",
            "test_the_record_TABLE_asks_for_the_numbers_to_copy",
            "test_the_record_is_asked_for_and_with_the_stand_ins",
            "test_the_page_carries_no_real_personal_information",
            "test_the_M13_document_and_its_pin_are_left_alone",
            "test_the_lists_are_at_least_as_long_as_the_census_says",
            "test_the_parametrized_controls_are_WIRED_to_every_row",
            "test_no_extent_control_in_this_file_can_be_deleted_without_saying_so",
        }
    ),
    # its LOAD_BEARING_CONTROLS: every name it registers; the reasons live there
    "test_m9_safety_constructions.py": frozenset(
        {
            "test_a_caption_written_between_apply_and_undo_does_not_change_what_undo_puts_back",
            "test_a_crash_between_the_members_of_a_partially_conforming_unit_is_healed_whole",
            "test_a_geocoded_event_label_selects_one_member_of_a_unit_and_decide_filter_widens_it",
            "test_a_partially_conforming_unit_survives_a_review_ui_rename_whole",
            "test_a_place_fact_selects_one_member_of_a_unit_and_decide_filter_widens_it",
            "test_a_superseded_caption_still_explains_the_plan_it_produced",
            "test_an_event_nav_group_selects_one_member_of_a_unit_and_decide_filter_widens_it",
            "test_every_unit_in_a_captioned_archive_agrees_about_tier_trust_and_attention",
            "test_pam_enrich_between_plan_and_apply_leaves_the_plan_and_its_unit_intact",
            "test_pam_enrich_caption_between_plan_and_apply_renames_to_exactly_what_was_reviewed",
            "test_pam_enrich_caption_over_a_raw_file_changes_not_one_byte_of_the_archive",
            "test_pam_enrich_never_opens_a_cloud_placeholder_that_still_carries_a_sha256",
            "test_q_matching_the_ignored_editor_suffix_selects_one_member_and_widens_to_the_unit",
            "test_refreshing_the_caption_model_after_a_plan_exists_does_not_move_that_plan",
            "test_the_captioner_is_inside_a_connect_watch_and_every_address_it_dials_is_loopback",
            "test_the_path_budget_is_the_units_and_not_each_members_own",
            "test_two_captioned_members_whose_stems_differ_still_take_the_primarys_scene",
        }
    ),
    # its LOAD_BEARING_CONTROLS: every name it registers; the reasons live there
    "test_net_canary.py": frozenset(
        {
            "test_a_captioner_host_answering_LOOPBACK_AND_OFF_BOX_is_refused_before_any_connection",
            "test_a_host_answering_both_loopback_and_off_box_is_refused_before_any_connection",
            "test_loading_a_real_model_pack_opens_no_socket_at_all",
            "test_pam_models_pull_refuses_an_off_box_host_without_dialling_it",
            "test_no_archive_command_opens_a_socket_or_loads_the_release_client",
            "test_the_release_client_canary_sees_the_lazy_import_and_a_real_connection",
            "test_reverse_geocoder_imports_and_answers_a_query_without_opening_a_socket",
            "test_the_alarm_fires_when_it_should",
            "test_the_canary_also_sees_connect_ex",
            "test_the_canary_covers_every_stage_spec_10_names",
            "test_the_canary_records_a_real_outbound_connection",
            "test_the_canary_sees_getaddrinfo_and_forwards_its_answer",
            "test_the_canary_sees_gethostbyname",
            "test_the_canary_sees_gethostbyname_ex",
            "test_the_canary_sees_sendmsg_where_the_platform_has_it",
            "test_the_canary_sees_sendto_and_the_datagram_arrives",
            "test_the_captioner_really_does_connect_when_the_address_is_loopback",
            "test_the_captioner_refuses_an_off_box_host_without_dialling_it",
            "test_the_doctor_probe_refuses_an_off_box_host_without_dialling_it",
            "test_the_enrich_stage_WITH_CAPTIONING_ON_opens_only_loopback_sockets",
            "test_the_enrich_stage_WITH_FACES_ON_opens_no_socket_at_all",
            "test_the_faces_loader_refuses_a_corrupt_pack_without_opening_a_socket",
            "test_the_faces_loader_refuses_a_missing_pack_without_opening_a_socket",
            "test_the_ocr_engine_loads_and_reads_without_a_python_socket",  # M12 Task 12 B12
            "test_the_geocoder_dataset_ships_inside_the_package_so_the_download_branch_is_dead",
            "test_the_lookup_classifier_answers_a_hand_written_table",
            "test_the_probe_really_does_connect_when_the_address_is_loopback",
            "test_the_send_wrappers_installed_are_exactly_what_this_platform_has",
            "test_the_watch_is_uninstalled_when_a_test_finishes",
            "test_the_whole_pipeline_opens_no_socket_at_all",
            "test_the_wrappers_forward_the_originals_exception_unchanged",
            "test_every_watch_consumer_calls_an_alarm",
            "test_no_pam_module_binds_a_socket_resolver_by_name",
            "test_the_page_chain_read_rename_undo_opens_only_loopback_sockets",
        }
    ),
    # its LOAD_BEARING_CONTROLS: every name it registers; the reasons live there
    "test_plan_cli.py": frozenset(
        {
            "test_a_number_held_by_a_ghost_row_is_EXPLAINED_and_not_merely_skipped",
            "test_no_ghost_rows_prints_no_ghost_row_LINE_AT_ALL",
            "test_the_ghost_row_sentence_is_SINGULAR_when_one_number_was_skipped",
            "test_the_ghost_row_sentence_offers_NO_Fix_AND_NO_Run_because_none_exists",
        }
    ),
    # its LOAD_BEARING_CONTROLS: every name it registers; the reasons live there
    "test_planner.py": frozenset(
        {
            "test_a_number_held_by_a_GHOST_ROW_is_reported_to_the_user",
            "test_an_archive_with_no_ghost_rows_reports_NOTHING_blocked",
            "test_the_family_REFUSES_a_stem_whose_seq_position_is_ambiguous",
            "test_the_family_is_scoped_to_the_items_OWN_DIRECTORY",
            "test_the_family_of_number_one_is_EMPTY_because_nothing_below_it_was_skipped",
            "test_the_family_spells_every_LOWER_number_and_never_its_own",
        }
    ),
    # its LOAD_BEARING_CONTROLS: every name it registers; the reasons live there
    "test_static_syntax.py": frozenset(
        {
            "test_every_discovered_page_loads_only_files_the_server_will_serve",
            "test_every_page_pulls_in_exactly_the_assets_its_spec_gives_it",
            "test_every_required_static_file_is_present_and_routable",
            "test_every_shipped_script_parses_as_javascript",
            "test_the_discovery_finds_a_real_inventory_rather_than_nothing",
            "test_the_installed_package_offers_the_same_static_files_the_filesystem_does",
            "test_the_labeling_page_ships_both_of_its_own_assets",
            "test_the_reference_reader_ignores_external_and_non_file_targets",
            "test_the_required_static_file_list_is_not_empty",
            "test_the_spec_side_reference_table_covers_every_page_that_ships",
            # M14 Task 1: the review page's script order, the load-order harness, UX 7's contrast
            "test_the_review_script_order_is_spelled_and_names_only_required_files",
            "test_the_review_page_loads_its_scripts_in_the_spec_order_and_none_inline",
            "test_the_script_parser_sees_a_swap_inline_code_and_a_commented_tag",
            "test_the_load_order_harness_ships_and_names_every_check",
            "test_the_load_order_harness_executes_exactly_the_checks_it_claims",
            "test_the_contrast_pair_list_is_not_empty_and_covers_both_themes",
            "test_the_contrast_formula_agrees_with_wcag_reference_values",
            "test_every_text_and_background_pair_clears_ux_7s_contrast_floor",
            # M14 Task 1 review (F-1-3 .. F-1-7)
            "test_every_token_ux_7_rev_16_spells_has_its_value_in_both_themes",
            # M14 Task 2 Step 6b (U4): the two phone-notice controls, rewritten to pin the
            # notice's retirement from the review page, renamed; + their positive control
            "test_the_review_page_has_no_phone_notice_and_the_faces_page_keeps_its_swap",
            "test_the_no_notice_check_refuses_a_review_page_that_is_still_swapped_out",
            "test_the_no_notice_check_accepts_the_faces_only_swap",
            # M14 Task 2 review R2 (M2870): the <body> class the swap's scope spares, and its
            # negative control
            "test_the_review_pages_body_opts_out_of_the_swap_and_the_faces_pages_does_not",
            "test_the_body_scope_check_refuses_a_page_pair_the_rule_would_get_wrong",
            "test_every_layout_shell_rule_is_scoped_to_the_review_page",
            "test_the_scope_check_refuses_an_unscoped_shell_rule",
            "test_every_review_control_meets_the_44px_touch_target",
            "test_every_static_file_is_served_with_the_type_its_extension_names",
        }
    ),
    # the fact_models inventory; anchors #3-#5 and their registrations deleted at
    # M12 Task 1 (15 -> 12, REGISTERED_CONTROLS_FLOOR with it)
    "test_webui_api.py": frozenset(
        {
            "test_a_card_whose_token_names_a_row_the_catalog_no_longer_holds_still_renders",
            "test_a_people_token_whose_id_collides_with_a_fact_on_the_page_gets_no_model",
            "test_a_token_pointing_at_a_row_that_is_not_there_costs_one_sentence_not_the_page",
            "test_an_item_with_no_ai_token_gets_no_sentence_and_no_entry",
            # M14 Task 2 Step 1a: GET items refuses an unknown parameter; its spelled known set
            "test_an_unknown_items_parameter_is_refused_by_name",
            "test_the_known_items_parameters_are_exactly_design_8_2s",
            "test_each_card_carries_its_own_models_and_a_page_fetched_alone_is_complete",
            "test_fact_models_covers_exactly_the_tokens_whose_source_is_inferred_facts",
            "test_fact_models_is_keyed_by_the_FACT_id_and_never_by_the_file_id",
            "test_on_one_card_the_scene_keeps_its_model_and_the_colliding_person_gets_nothing",
            "test_people_search_through_the_server_does_not_find_a_BELOW_FLOOR_face",
            "test_the_review_api_reads_the_faces_floor_out_of_the_archives_config",
            "test_the_served_tokens_are_the_planner_s_document_unedited_and_unreordered",
            "test_the_why_panel_names_a_person_and_reads_a_model_for_ai_tokens_only",
        }
    ),
    # FACES_LOAD_BEARING_CONTROLS, including the node-driver anchor (ruling R3)
    "test_webui_static.py": frozenset(
        {
            "test_a_task_11_control_is_live_and_posts_to_a_route_the_server_serves",
            "test_every_correction_route_faces_js_posts_is_one_the_server_serves",
            "test_every_id_faces_js_queries_exists_exactly_once_in_faces_html",
            "test_every_styled_face_class_is_one_faces_js_actually_builds",
            "test_faces_js_queries_a_plausible_number_of_ids",
            "test_no_faces_expectation_in_this_module_is_empty",
            "test_no_faces_expectation_lost_an_entry_without_its_floor_moving",
            "test_skip_and_ignore_are_described_as_different_things",
            "test_the_faces_cross_check_sees_an_id_the_real_page_stopped_declaring",
            "test_the_faces_plausibility_check_refuses_an_extraction_missing_any_one_anchor",
            "test_the_faces_plausibility_check_refuses_an_extraction_that_went_quiet",
            "test_the_faces_style_check_refuses_a_rule_that_declares_nothing",
            "test_the_live_control_check_refuses_a_button_that_ships_disabled",
            "test_the_node_driver_module_exists_and_registers_its_own_controls",
            "test_the_page_offers_a_way_back_from_having_skipped_everything",
            "test_the_route_check_refuses_a_route_the_server_does_not_serve",
            "test_the_stylesheet_styles_every_class_the_face_strip_renders",
        }
    ),
    # M12 Task 2 (m11.md C6, amendment P1): design 2's tree against the repository, both
    # directions, and the parser pinned on a hand-spelled sample. Keeps no registry
    "private/test_spec_tree.py": frozenset(
        {
            "test_the_parser_turns_a_sample_tree_into_exactly_the_paths_spelled_by_hand",
            "test_the_block_is_the_first_fence_under_the_heading_and_not_another_sections",
            "test_a_block_this_grammar_cannot_read_is_refused_rather_than_guessed",
            "test_the_comparison_sees_an_unnamed_file_and_a_phantom_entry_in_both_directions",
            "test_the_comparison_sees_a_tools_script_on_disk_that_the_tree_does_not_name",
            "test_design_section_2_names_every_module_and_every_entry_exists",
        }
    ),
    # M12 Task 3 (m11.md C5): app.js's spec rules EXECUTED under node, the export seam and the
    # DOM->pure wiring checked in the stdlib half that never skips. Keeps no registry
    "test_app_logic.py": frozenset(
        {
            "test_the_required_lists_are_not_empty",
            "test_the_node_driver_ships_and_is_not_empty",
            "test_the_driver_names_every_check_this_condition_owes",
            "test_app_js_exports_every_function_plan_task_3_names",
            "test_each_dom_function_renders_what_its_pure_function_returns",
            "test_each_dom_function_renders_the_result_and_decides_nothing_itself",
            "test_the_export_reader_sees_a_deleted_export",
            "test_the_node_driver_executes_exactly_the_checks_it_claims",
            # M13 Task 2 (C5): the export registry moved to EQUALITY, and its control
            "test_app_js_exports_exactly_the_registered_seam",
            "test_the_export_equality_refuses_an_extra_name_and_a_missing_one",
            # M13 Task 2 review (M2393, M2394): the bulk buttons' wiring and Accept all's count;
            # M14 Task 2 Step 6b final rewrote the first to U2 (Accept all wired, the five
            # preset buttons gone), renamed, and added the retired functions' stdlib pin
            "test_accept_all_is_wired_and_the_preset_buttons_are_gone",
            "test_no_review_script_exports_or_defines_a_retired_card_page_function",
            "test_accept_alls_count_is_the_plans_undecided_high_plus_medium",
        }
    ),
    # M13 Task 3 (C4, S8): `--refresh-older-than-model` with `faces` or `ocr` named is refused
    # before any configuration is read - every state built so only one operand holds, the
    # unparseable pam.toml, the load_config spy and its control, and the no-name path pinned
    # as measured. Keeps no registry
    "test_refresh_refusal.py": frozenset(
        {
            "test_the_state_tables_are_whole_at_collection",
            "test_the_refresh_flag_is_refused_in_every_state_before_any_config_is_read",
            "test_an_unparseable_pam_toml_still_gets_the_refusal_and_not_a_config_error",
            "test_the_unparseable_pam_toml_really_is_a_config_error_without_the_flag",
            "test_the_config_spy_records_a_call_when_the_flag_is_absent",
            "test_another_enricher_with_the_flag_still_reads_its_config",
            "test_no_name_with_default_config_refreshes_the_rest_and_reports_faces_and_ocr_disabled",
            "test_no_name_with_faces_ready_stops_the_whole_command_at_faces_with_exit_one",
            "test_no_name_with_ocr_ready_stops_at_ocr_with_exit_one",
            "test_no_name_with_ocr_enabled_and_no_folders_exits_zero_and_reports_no_refusal",
        }
    ),
    # M13 Task 4 (C1, S13): every lock-free reader reads ONE snapshot. The report-session
    # construction is the only measurement of a never-existed `pam report` state; the
    # transaction checks are the sole guard on each one-SELECT reader's wrap.
    "test_snapshot_reads.py": frozenset(
        {
            "test_report_session_read_across_an_undo_reports_before_or_after",
            "test_every_lock_free_read_runs_inside_one_transaction",
            "test_verify_runs_every_check_inside_one_snapshot",
            "test_a_writer_started_during_verifys_read_half_is_refused_the_lock",
            "test_read_snapshot_holds_one_state_and_ends_its_transaction",
        }
    ),
    # M13 Task 5 (C2, S9/H8): a name pam wrote is not evidence. The only end-to-end
    # constructions of R1 (`m12.md` 2.1) and its twins; every test in the module, which keeps no
    # registry - each is the sole guard of one operand of `repo.pre_pam_names`/`name_evidence`.
    "test_pam_written_names.py": frozenset(
        {
            "test_R1_an_ocr_dated_name_pam_wrote_is_not_read_back_as_a_filename_date",
            "test_the_file_mtime_twin_a_dated_name_from_an_mtime_is_not_evidence",
            "test_H8_a_filename_date_the_users_own_name_carried_survives_pams_rename",
            "test_negative_control_a_name_the_USER_gave_a_file_pam_renamed_is_evidence",
            "test_negative_control_a_name_the_USER_gave_a_file_pam_never_touched_is_evidence",
            "test_an_undone_rename_gives_the_name_back_to_the_user",
            "test_undoing_the_SECOND_rename_of_a_chain_leaves_the_first_one_pams",
            "test_a_chain_resolves_to_the_name_the_file_had_before_pams_FIRST_rename",
            "test_a_failed_rename_is_not_a_name_pam_wrote",
            "test_a_DIFFERENT_file_at_pams_path_is_the_users_and_its_name_is_evidence",
            "test_the_video_rule_compares_with_the_name_before_pam_not_pams_own",
            "test_video_rule_control_a_pre_pam_name_with_the_SAME_day_is_not_flagged",
            "test_a_reverted_rename_is_not_a_name_pam_wrote",
            "test_the_pre_pam_NAME_is_parsed_never_the_folder_it_sits_in",
            "test_the_video_rule_reads_the_pre_pam_name_in_a_subfolder_too",
            "test_a_chain_checks_the_size_pam_journalled_at_its_LAST_rename",
            "test_every_member_of_a_unit_pam_renamed_together_is_judged_alike",
        }
    ),
    # M13 Task 7 (C6, S7, O-1): a host name is refused before the resolver sees it. Registered at
    # the Task 7 review (F5): the module was anchored only by an `exists()` check, so deleting one
    # O-1 control left the suite green. The wire-path test is here because nothing else pins that
    # the checked address is the address dialled.
    "test_net.py": frozenset(
        {
            "test_a_public_hostname_is_refused_before_it_is_resolved",
            "test_a_name_that_looks_local_is_refused_before_it_is_resolved",
            "test_O1_localhost_in_any_case_and_loopback_literals_reach_the_resolver",
            "test_O1_an_off_box_literal_is_asked_and_then_refused_by_the_loopback_rule",
            "test_O1_every_other_name_is_refused_and_the_resolver_is_never_called",
            "test_the_wire_path_dials_the_ADDRESS_and_not_the_HOSTNAME",
            "test_the_network_allowlist_is_the_one_the_spec_names",
        }
    ),
    # M13 Task 6 (C3, S10): a unit whose members' own camera dates disagree with the name's
    # says so. Keeps no registry; every test is the sole guard of one operand of
    # `consistency.unit_date_reasons` (the tolerance, the objective sources, the name's date, the
    # unit-wide reasons) - and of its wiring, since every one drives the real `pam plan`.
    # M13 Task 7 (C7, S6(b)): P-2's `<= 0` branch and P-6's post-unclip drop are unreachable at
    # the table's constants. Keeps no registry; module-level asserts fail collection if either
    # sweep grid empties, and the first test pins the module's constants to the spec's literals.
    "test_ocr_unreachable.py": frozenset(
        {
            "test_the_modules_constants_are_the_specs_literals",
            "test_P2_never_takes_its_nonpositive_branch_in_the_reachable_domain",
            "test_the_thin_corner_really_raises_rather_than_returning_none",
            "test_P6_the_post_unclip_floor_is_met_by_every_box_that_passes_the_size_floor",
            "test_P6_boxes_keeps_every_component_that_clears_the_size_and_score_floors",
        }
    ),
    # M13 Task 9 (H2): the duplicates report moved to its own module (Step 0); the cut's only
    # guard against a second definition or an import path through `pam/report.py`. Then H2's
    # look-alikes end to end - by content, present files, a companion unit alone not reported,
    # the configured distance, the coverage sentence - each the sole guard of one rule.
    "test_report_duplicates.py": frozenset(
        {
            "test_the_moved_names_have_one_definition_and_no_re_export",
            "test_look_alikes_are_grouped_by_content_and_a_companion_unit_alone_is_not_one",
            "test_the_output_is_h2s_copy",
            "test_a_group_reaching_outside_one_companion_unit_is_reported_whole",
            "test_with_every_image_fingerprinted_the_coverage_sentence_asks_for_nothing",
            "test_a_missing_files_content_is_not_compared",
            "test_the_distance_is_the_configured_near_dupe_distance",
            # Task 9 review: F4 (M2507), F2 (M2501/M2502), F1 (M2498), F3 (M2505/M2506).
            "test_the_old_module_does_not_carry_a_moved_name_at_runtime",
            "test_a_placeholders_content_is_neither_compared_nor_counted",
            "test_two_companion_units_that_look_alike_are_reported",
            "test_groups_are_ordered_by_first_path_and_paths_within_a_group_are_sorted",
        }
    ),
    # M13 Task 9 (H2): the pigeonhole search is EXACT. The literal table is the only thing
    # spelled from the spec; the property run is the second implementation; the call count is
    # the only evidence the search is sub-quadratic.
    "test_phash_groups.py": frozenset(
        {
            "test_the_literal_table",
            "test_the_literal_table_also_holds_for_the_definition",
            "test_the_search_equals_the_all_pairs_definition_on_300_seeded_sets",
            "test_at_hash_bits_every_pair_qualifies_and_one_below_the_opposite_pair_does_not",
            "test_a_digest_of_the_wrong_width_is_refused_as_the_definition_refuses_it",
            "test_the_search_compares_under_15_percent_of_all_pairs_at_5000_hashes",
            # Task 9 review: F5 (M2508), and the order corner M2510 found unspelled.
            "test_a_digest_of_the_wrong_width_sharing_no_band_is_still_refused",
            "test_groups_are_ordered_by_smallest_id_where_the_first_merge_is_not_the_smallest",
        }
    ),
    # M13 Task 10 (H3): the review card's look-alikes through a real server - a single names
    # the other, a companion unit never names its own members, the cap of five and the count,
    # placeholders, and once per server. Every test in the module. No registry
    # M14 Task 2 (U25, U15): the list's groups are units' groups (T2.1, T2.2, T2.3) and the
    # row's note code is read off structure, never reason text (T2.7). Every test in the
    # module; it keeps no registry.
    "test_webui_listing.py": frozenset(
        {
            "test_a_groups_date_line_is_spelled_in_english_with_the_en_dash",
            "test_a_list_group_key_in_its_one_spelling_is_accepted",
            "test_a_malformed_list_group_is_a_400_naming_it",
            "test_a_sidecar_with_no_capture_row_is_listed_with_its_photo",
            "test_a_two_month_event_is_ONE_list_group",
            "test_a_unit_takes_the_event_of_its_lowest_id_member_that_has_one",
            "test_a_unit_with_no_event_takes_its_date_tokens_day_and_else_undated",
            "test_any_other_list_group_form_is_refused",
            "test_every_card_carries_a_note_code_from_u15s_vocabulary",
            "test_groups_are_in_date_order_with_undated_last_and_count_on_the_server",
            "test_paging_a_list_group_returns_every_id_exactly_once",
            "test_the_list_route_spells_u25s_fields_and_its_counts_add_up",
            "test_the_note_code_is_read_off_the_date_tokens_structure",
            "test_the_note_code_never_reads_a_reasons_text",
            # M14 Task 2 W9: the row's unit disclosure reads the catalog's group kind
            "test_every_card_carries_its_units_companion_kind_from_the_catalog",
            "test_a_page_holding_one_member_still_carries_the_units_kind",
            "test_a_units_kind_is_its_lowest_id_grouped_members_and_never_its_extensions",
            # M14 Task 2 review R1: the routes' own wiring (M2821, M2828, M2840-M2842, M2857-M2860)
            "test_a_cards_note_code_follows_its_date_token_to_the_capture_row",
            "test_the_list_routes_groups_are_spelled_field_by_field",
            "test_an_empty_list_group_is_no_filter_like_every_other_items_filter",
        }
    ),
    # M14 Task 3 slice 3a (U3, U17, U18): the selection's resolver and its two routes - the
    # preview writes nothing, U17's order (view-scoped groups, `all`, ids as named, except, THEN
    # widen), `low` over the widened set, the count-and-flag acknowledgement (T3 mutations 1-5,
    # 7, 8, each refusal watched at the write call and `total_changes`), the V3 unknown-field
    # refusal at every level, and the 409's fresh preview over the wire. Every test in the
    # module; it keeps no registry.
    "test_webui_selection.py": frozenset(
        {
            "test_a_field_outside_u17s_wire_format_is_refused_and_writes_nothing",
            "test_a_groups_box_in_the_needs_a_look_view_selects_only_that_views_rows",
            "test_a_groups_box_selects_the_whole_group_including_rows_the_page_never_loaded",
            "test_a_malformed_selection_is_a_400_naming_its_field_and_writes_nothing",
            "test_a_selection_with_no_low_name_needs_no_acknowledgement_but_refuses_a_stale_one",
            "test_a_stale_low_count_is_refused_and_writes_nothing",
            "test_all_is_the_view_and_ids_are_taken_as_named_whatever_the_view",
            "test_an_except_id_that_would_split_a_unit_is_widened_back_and_reported",
            "test_both_routes_are_served_under_the_token_and_a_409_carries_the_fresh_preview",
            "test_every_field_u17_names_is_accepted_at_its_level",
            "test_expected_low_must_be_a_literal_whole_number",
            "test_include_low_must_be_the_literal_true",
            "test_low_counts_the_widened_set_not_the_rows_the_user_named",
            "test_skip_and_reset_refuse_a_low_acknowledgement_they_cannot_use",
            "test_skip_needs_no_low_acknowledgement",
            "test_the_acknowledged_count_approves_every_low_name_and_says_how_many",
            "test_the_preview_is_read_only_and_spells_u17s_fields",
            # slice 3d: the date tree's `group` in the view (the controller's ruling)
            "test_a_view_group_is_validated_like_the_items_reader",
            "test_select_all_under_a_month_filter_covers_exactly_that_months_units",
        }
    ),
    # M14 Task 3 slice 3b (OD-1, R1 F7 + F9): the one-tap shortcuts - the callout's two numbers
    # add up (T3 mutation 9, an OCR-dated `medium` built by hand), a group's N is its write,
    # whole units of the whole plan under a one-member `q` (vision S3), the view-scoped `/list`
    # and its refusal, the shortcut body's refusals watched at the write, and M13's `Accept all`
    # unchanged. Every test in the module; it keeps no registry.
    "test_webui_shortcut.py": frozenset(
        {
            "test_a_groups_shortcut_writes_exactly_the_N_its_group_showed",
            # Task 3 review R1 F3 (M2918): a group the view shows only part of
            "test_a_groups_N_under_a_search_that_shows_part_of_it_is_the_views_and_so_is_its_write",
            "test_a_malformed_shortcut_is_a_400_naming_its_field_and_writes_nothing",
            "test_a_search_matching_one_member_of_a_unit_approves_both_members_or_neither",
            "test_accept_all_still_approves_a_needs_a_look_name_that_is_not_low",
            "test_an_all_low_group_has_no_shortcut_and_says_so",
            "test_an_empty_list_group_on_a_shortcut_is_no_filter",
            "test_the_list_accepts_every_view_parameter",
            "test_the_list_refuses_a_parameter_that_is_not_part_of_a_view",
            "test_the_lists_counts_are_the_views_rows",
            "test_the_other_N_and_the_needs_a_look_count_add_up",
            # slice 3d: the date tree's `group` in `/list` and the shortcut
            "test_a_shortcut_under_a_month_filter_approves_exactly_that_months_units",
            "test_the_lists_counts_under_a_month_filter_are_that_months_units",
        }
    ),
    # M14 Task 3 Step 4 (U3): the ONE bypass of the Low rule, found by a `Call`-node walk
    # (T3 mutation 11), its spelled caller list, the sanctioned pair, the alias check, the
    # trusted `listing.shortcut`, and the walker's own scratch-module control.
    "test_webui_low_bypass.py": frozenset(
        {
            "test_decide_stays_a_per_id_route",
            "test_nothing_hides_the_writer_behind_another_name",
            # Task 3 review R1 F2: the row writer under `_write_decisions`, and its control
            "test_only_the_spelled_functions_write_an_items_status_directly",
            "test_the_row_writer_walk_finds_a_direct_caller",
            "test_the_guard_finds_a_second_unguarded_caller",
            "test_the_only_unguarded_writers_are_the_sanctioned_two",
            "test_the_shortcut_the_guard_trusts_is_the_low_rule",
            "test_the_walker_finds_exactly_the_writers_callers_spelled_by_hand",
        }
    ),
    # M14 Task 2 Steps 3-4 (U2, U15, W2, W5, W7-W10): the list page executed under node - the
    # circle's toggle (T2.4), the progress line from the response (T2.5), the no-op sentence
    # (T2.6), the row copy spelled and floored at collection (T2.8), paging every id once. Every
    # test in the module; it keeps no registry.
    "test_webui_list_page.py": frozenset(
        {
            "test_the_required_check_list_is_not_empty",
            "test_the_node_driver_ships_and_is_not_empty",
            "test_the_row_scripts_carry_every_row_sentence_verbatim",
            "test_the_guard_the_negative_control_plants_against_is_in_the_row",
            "test_the_node_driver_executes_every_check_it_claims",
            "test_the_node_driver_fails_when_an_approved_circle_sends_approve",
            # M14 Task 2 Step 5: the field guard's run negative control
            "test_the_node_driver_fails_when_the_name_field_lets_x_z_c_through",
            # M14 Task 2 Step 6a: Accept all's list reload, its run negative control
            "test_the_node_driver_fails_when_accept_all_does_not_reload_the_list",
            # M14 Task 3 slice 3d: a shortcut's view, its run negative control
            "test_the_node_driver_fails_when_a_shortcut_drops_the_view",
        }
    ),
    # M14 Task 3 slice 3c (U3, U17, W13/W14): the selection as data and its bar, executed under
    # node - the group key standing for unloaded rows (T3 mutation 6), Approve waiting for the
    # preview, U3's sentences, the 409. Every test in the module; it keeps no registry.
    "test_webui_selection_page.py": frozenset(
        {
            "test_the_node_driver_ships_and_names_every_required_check",
            "test_the_bar_carries_u3s_sentences_verbatim",
            "test_the_bar_is_w13s_three_buttons_in_order_and_starts_hidden",
            "test_the_bar_reader_sees_a_missing_and_a_reordered_button",
            "test_the_lines_the_negative_controls_plant_against_are_there_once",
            "test_the_node_driver_executes_every_check_it_claims",
            "test_the_node_driver_fails_when_a_groups_box_enumerates_its_loaded_rows",
            "test_the_node_driver_fails_when_approve_sends_while_a_preview_is_in_flight",
        }
    ),
    # M14 Task 4 Step 7 (W18, U10, U12, U13; OD-4): Welcome executed under node - unbound vs
    # bound from the ping, the chooser, the bind's three outcomes, each refusal verbatim, the
    # paste box, Change, OD-4's sentence absent. Every test in the module; it keeps no registry.
    "test_webui_welcome_page.py": frozenset(
        {
            "test_the_node_driver_ships_and_names_every_required_check",
            "test_welcome_carries_w18s_copy_verbatim",
            "test_welcome_carries_w18s_lead_verbatim",
            "test_od4s_sentence_is_in_no_static_file",
            "test_the_lines_the_negative_controls_plant_against_are_there_once",
            "test_every_check_a_negative_control_names_is_required",
            "test_the_node_driver_executes_every_check_it_claims",
            "test_the_node_driver_fails_when_an_unbound_page_is_not_sent_to_welcome",
            "test_the_node_driver_fails_when_the_list_hides_recoverys_line",
            "test_the_node_driver_fails_when_a_refusal_is_reworded",
            # M14 Task 5e: the Reading step (U11, U12, U21) - its copy, and its controls run.
            "test_the_reading_step_carries_the_eta_phrases_verbatim",
            "test_the_core_carries_u12s_banner_verbatim",
            "test_the_node_driver_fails_when_an_eta_boundary_moves",
            "test_the_node_driver_fails_when_start_reading_is_offered_over_an_open_plan",
            "test_the_node_driver_fails_when_the_jobs_stopped_line_is_reworded",
            "test_the_node_driver_fails_when_the_stopped_banner_is_reworded",
            "test_the_node_driver_fails_when_a_double_click_posts_twice",
            "test_the_node_driver_fails_when_a_reload_does_not_resume_a_running_read",
            # M14 Task 5 review R2 (M3180): the poll scheduled twice - `tick`'s one-poll assertion
            "test_the_node_driver_fails_when_the_poll_is_scheduled_twice",
            "test_the_node_driver_fails_when_a_late_reload_answer_starts_a_second_poll",
            # M14 Task 6 Stage 2, S2-F2 (the controller's fix): recovery's line at a start
            "test_the_node_driver_fails_when_the_pings_recovery_line_is_hidden",
            "test_the_node_driver_fails_when_the_list_opens_over_a_starts_recovery",
        }
    ),
    # M14 Task 2 Step 6a (U16, A6): the preview overlay and the name editor on the list's rows,
    # executed under node - Enter, the thumbnail, C, the field's inert keys, the rename's body.
    # Every test in the module; it keeps no registry.
    "test_webui_detail_page.py": frozenset(
        {
            "test_the_node_driver_ships_and_names_every_required_check",
            "test_app_js_wires_the_detail_and_the_line_the_control_plants_against_exists",
            "test_the_node_driver_executes_every_check_it_claims",
            "test_the_node_driver_fails_when_c_opens_nothing",
            # Step 6b: the card's restore, moved onto the row
            "test_the_node_driver_fails_when_the_rows_restore_puts_nothing_back",
        }
    ),
    # M14 Task 4 Step 1: the `cli.py` cut is a PURE MOVE only while every help screen matches the
    # transcript recorded before it, and the command list is spelled, not read back. Every test in
    # the module; it keeps no registry.
    "test_cli_help_golden.py": frozenset(
        {
            "test_the_transcript_and_the_live_tree_list_exactly_the_spelled_commands",
            "test_every_help_screen_is_byte_identical_to_the_transcript_recorded_before_the_move",
            "test_the_faces_group_is_registered_from_cli_faces_and_shares_reports_archive_help",
        }
    ),
    # M14 Task 5b (Step 1, T5.8/T5.9, C5-2): the lock-held scan and plan bodies - the proof only
    # `WorkspaceLock.__enter__` makes, the hand-typed golden transcript from 2d38060, the stop
    # between files and the progress seam, and the server's proof. Every test in the module.
    "test_lock_held_bodies.py": frozenset(
        {
            "test_scan_then_plan_print_the_transcript_recorded_before_the_bodies_moved",
            "test_a_held_workspace_cannot_be_forged",
            "test_a_lock_built_without_a_workspace_gives_a_proof_for_nothing",
            "test_the_bodies_refuse_a_proof_for_another_folder_and_touch_nothing",
            "test_a_stopped_read_leaves_no_half_recorded_file",
            "test_progress_gets_every_file_and_its_default_is_the_console_printer",
            "test_a_file_freed_after_the_walk_still_ticks_progress_and_is_a_place_to_stop",
            "test_a_bound_start_server_holds_the_proof_for_its_folder_and_none_after",
            "test_a_plan_made_after_a_stopped_read_never_splits_a_companion_unit",
            "test_a_read_stopped_at_its_last_file_still_groups_and_plans_the_pair_as_one",
            "test_a_read_stopped_where_a_file_became_cloud_only_still_groups_the_pair",
            "test_a_stopped_scan_logs_that_it_stopped_last_and_never_that_it_was_done",
            # M14 Task 6a, Step 1: the apply and undo bodies under the held lock, and the CLI's
            # apply/undo transcripts typed by hand from `v0.1-m13`.
            "test_cli_apply_and_undo_print_the_transcripts_recorded_at_v0_1_m13",
            "test_apply_locked_and_undo_locked_rename_exactly_the_approved_under_the_held_lock",
            "test_apply_locked_and_undo_locked_refuse_another_folders_proof_and_rename_nothing",
            "test_apply_locked_and_undo_locked_refuse_a_released_proof_and_rename_nothing",
            "test_run_apply_and_run_undo_still_take_the_lock_themselves",
        }
    ),
    # M14 Task 6a, Step 2 (U19): the webui -> `pam.execute` guard, PER MODULE. The pinned pairs,
    # U19's per-module rows, the negative control over a scratch copy of the package, the unused
    # allowances (6b empties that set), and `ALLOWED_MOVE_SITES` unchanged. Only these five of the
    # module's tests are listed: the census grows by what a task adds.
    "test_webui_server.py": frozenset(
        {
            "test_the_webui_execute_allowances_are_named_pairs_under_the_forbidden_package",
            "test_the_webui_guard_holds_each_execute_import_to_the_module_it_is_listed_for",
            "test_an_unlisted_execute_import_planted_in_a_scratch_copy_fails_the_walk",
            "test_every_webui_execute_allowance_is_used_except_the_two_task_6b_will_use",
            "test_u19_leaves_the_move_sites_exactly_as_they_were",
        }
    ),
    # M14 Task 5c (U11, U12, U23, C5-5): the job registry - the busy refusal outside and INSIDE
    # the DB lock, join-then-recover after a job dies, cancel, server_close under a job, one log.
    "test_webui_jobs.py": frozenset(
        {
            # Task 5h review 5h-F5: a refused job shows the refusal's sentence; a crash shows none
            "test_a_job_refused_with_a_pam_error_shows_its_sentence_and_a_crash_shows_none",
            "test_a_decision_during_a_read_is_refused_and_writes_nothing",
            "test_a_write_that_passed_the_outside_check_is_refused_inside_the_lock",
            "test_every_write_route_is_refused_while_a_job_runs_and_only_cancel_is_served",
            "test_the_snapshot_reports_kind_state_progress_lines_and_recovery",
            "test_the_job_body_gets_the_proof_the_logger_and_no_connection",
            "test_a_second_job_is_refused_while_one_runs",
            "test_cancel_on_a_job_that_cannot_stop_is_ignored",
            "test_cancel_stops_a_reading_job_between_files",
            "test_change_folder_is_refused_while_a_job_runs_through_busy_with",
            "test_server_close_waits_for_a_running_read_before_releasing_the_lock",
            "test_a_job_that_dies_is_recovered_before_the_next_write",
            "test_the_dead_job_s_thread_is_joined_before_recovery_runs",
            "test_systemexit_in_a_thread_ends_the_thread_not_the_process",
            "test_a_job_writes_into_the_server_s_one_log_and_leaves_its_handler",
            "test_pam_start_s_log_opens_at_bind_moves_with_the_folder_and_closes_at_server_close",
            "test_an_unbound_pam_start_prints_no_log_line",
            "test_the_page_s_poll_alone_heals_a_dead_job_and_a_new_job_starts_clean",
            "test_cancel_after_a_read_has_ended_asks_nothing",
            "test_server_close_heals_a_job_that_died_before_releasing_the_lock",
        }
    ),
    # M14 Task 6b (U6, U11, U14, U18, U19; the plan's Stage 1 T6 list): confirm, rename and undo
    # from the page through `apply_locked` / `undo_locked` - every refusal watched at the verified
    # rename's CALL; the nonce (replaced, single-use, used before the job, bound to the folder and
    # the digest); `folder_confirmed`; Content-Type / Origin / Host; GET never moves; F1's page
    # twin; the busy refusal both ways; job death joined and recovered; Ctrl+C is a crash.
    "test_webui_rename.py": frozenset(
        {
            # M15 Task 7 Stage 2 F3: a page rename that dies writing its report names its session
            "test_a_page_rename_that_dies_writing_its_report_still_names_its_session",
            # Task 7 follow-up review (M3864): F3's deaths pinned, an empty parametrize skips
            "test_the_report_write_deaths_are_written_down",
            # M15 Task 5b (C6.4): a plan renamed in two sessions - each report, the newest undo
            "test_pam_review_names_every_session_of_a_plan_renamed_in_two",
            "test_pam_review_offers_undo_for_the_newest_session_that_can_still_be_undone",
            # the controller's R6-1 / R6-2: a page rename's per-unit progress and its own counts
            "test_a_page_rename_reports_its_progress_per_unit_and_its_own_counts",
            "test_the_confirmation_states_u6_s_facts_and_a_new_one_replaces_the_nonce",
            "test_a_plan_with_nothing_approved_has_no_confirmation",
            "test_the_page_renames_exactly_what_the_page_approved",
            "test_a_changed_decision_after_confirm_renames_nothing",
            "test_a_nonce_for_another_folder_is_refused",
            "test_a_replayed_apply_renames_nothing",
            "test_a_replay_during_the_job_renames_nothing",
            "test_folder_confirmed_must_be_the_literal_true",
            "test_a_form_post_is_the_empty_404",
            "test_a_foreign_origin_is_the_empty_404",
            "test_a_rebound_host_is_the_empty_404",
            "test_get_never_renames",
            "test_get_never_undoes",
            "test_a_refused_apply_ends_the_job_failed_with_the_refusal_s_sentence",
            "test_an_apply_of_a_plan_a_read_superseded_ends_failed_with_the_sentence",
            "test_a_j13_refusal_reaches_the_job_s_lines_and_renames_nothing",
            "test_rename_is_refused_while_reading",
            "test_undo_is_refused_while_reading",
            "test_a_page_undo_after_a_page_apply_puts_every_name_back",
            "test_a_replayed_undo_renames_nothing",
            "test_an_unknown_session_renames_nothing",
            "test_an_already_undone_session_renames_nothing",
            "test_an_undo_after_the_session_changed_renames_nothing",
            "test_a_page_apply_that_dies_leaves_no_plan_applying",
            "test_ctrl_c_during_a_page_rename_is_a_crash_and_says_so",
            # M14 Task 6c, ruling B1: `pam review` serves the same server, bound, and Step 8 for it
            "test_pam_review_serves_the_bound_start_server_and_the_page_renames_on_it",
            "test_ctrl_c_during_a_page_rename_under_pam_review_is_a_crash_and_says_so",
            # M14 Task 6c, ruling B2: the footer's N is the confirmation's own count
            "test_the_footer_n_is_the_confirm_screen_s_renames_and_not_the_approved_items",
            # M14 Task 6 Stage 1 server review (S1-F2, F3, F6): R1-F1's binding on the moving
            # routes; U19 by NAME, with its walker's own control; THE verified rename's one call
            # site; a moving POST that sends no Origin is served
            "test_a_moving_route_from_a_page_on_another_folder_is_refused_and_spends_nothing",
            "test_jobs_reaches_apply_and_undo_only_by_the_names_u19_lists",
            "test_the_execute_reach_walker_sees_a_second_apply_body_in_each_spelling",
            "test_the_verified_rename_is_called_only_in_execute_unit",
            "test_a_moving_post_with_no_origin_is_served",
            # The controller's fixes for that review: S1-F1, `pam review`'s closing block after a
            # page rename or undo; S1-F7, the footer's count memoised until a write or a job
            "test_pam_review_closes_on_the_session_after_a_page_rename",
            "test_pam_review_closes_without_undo_after_a_page_undo",
            "test_pam_review_still_closes_on_its_counts_when_nothing_was_renamed",
            "test_pam_review_closes_on_the_new_plan_after_a_page_read",
            "test_pam_review_closes_on_pam_plan_when_no_plan_is_open",
            "test_the_footer_count_is_not_recomputed_until_something_writes",
            "test_the_footer_count_is_recomputed_when_a_job_ends",
            # the Stage 1 page review (M3320, M3321, M3325): the closing after a page apply that
            # renamed nothing, and after one that died (S1P-F1, strict xfail); the footer per plan
            "test_pam_review_closes_on_pam_plan_after_a_page_apply_that_renamed_nothing",
            "test_pam_review_offers_no_undo_after_a_page_apply_that_died_before_renaming",
            # the controller's mutation of its S1P-F1 fix (M3368): another session's undo
            "test_pam_review_offers_no_undo_for_its_empty_session_while_an_earlier_one_renamed",
            "test_the_footer_count_is_the_plan_s_own_with_no_write_between",
        }
    ),
    # M14 Task 6c (U6, U22, W16-W21, rulings B2-B6): Confirm, the progress, Done and undo under
    # node - the driver's named checks, the copy verbatim, U6's order, and the controls run.
    "test_webui_confirm_page.py": frozenset(
        {
            "test_the_node_driver_ships_and_names_every_required_check",
            "test_confirm_carries_w16_w19_u11_and_u22s_copy_verbatim",
            "test_confirm_carries_the_tick_first_refusal_verbatim",
            "test_done_carries_w20_w21_and_u12s_copy_verbatim",
            "test_the_confirm_screen_is_in_u6s_order",
            "test_every_check_a_negative_control_names_is_required",
            "test_the_lines_the_negative_controls_plant_against_are_there_once",
            "test_the_node_driver_executes_every_check_it_claims",
            "test_the_node_driver_fails_when_a_defect_is_planted",
            # the Task 9Y review (M3488): the lists' floors spelled again in another hunk
            "test_the_spelled_lists_hold_at_their_floors",
            # the Stage 1 page review: ruling B5's overlay sentence, B6's wide-only pills
            "test_the_shortcuts_overlay_says_b5s_sentence",
            "test_the_step_pills_show_at_wide_widths_only",
            # M15 Task 5b: U12's and C6.2's copy in `pam-stopped.js`, cut out of `pam-done.js`
            "test_a_stopped_rename_carries_u12_and_c6_2s_copy_verbatim",
        }
    ),
    # M14 Task 7 (W22, X2, B4): History under node - the driver's named checks, the copy verbatim,
    # the page's parts, Done as the only way in (T7-R1), and the controls run.
    "test_webui_history_page.py": frozenset(
        {
            "test_the_node_driver_ships_and_names_every_required_check",
            "test_history_carries_w22s_copy_verbatim",
            "test_done_carries_b4s_link_verbatim",
            "test_the_page_carries_historys_parts",
            "test_history_is_reached_from_done_the_list_and_welcome",
            "test_every_check_a_negative_control_names_is_required",
            "test_the_lines_the_negative_controls_plant_against_are_there_once",
            "test_the_node_driver_executes_every_check_it_claims",
            "test_the_node_driver_fails_when_a_defect_is_planted",
        }
    ),
    # M14 Task 7 (W22, X2, T7-R1..R5): History's rows and the session report - the undoable rule
    # per row spelled in fixture SQL (T7 2-6), the report named by the id alone (T7 1), and the undo
    # from History through Done's routes, watched at the verified rename.
    "test_webui_history.py": frozenset(
        {
            "test_an_older_session_stays_undoable_after_the_newer_is_undone",
            # Task 7 review (M3383): `_report_path` names no path for an id of another shape.
            "test_an_id_of_another_shape_names_no_report_path",
            "test_an_id_of_the_written_shape_names_its_report_inside_the_reports_directory",
            "test_the_undo_session_is_never_a_row",
            "test_a_partially_undone_session_stays_undoable",
            "test_a_session_that_renamed_nothing_is_neither_undone_nor_undoable",
            "test_the_rows_are_newest_first_and_a_same_second_run_follows_its_twin",
            "test_an_archive_with_no_sessions_lists_none",
            "test_an_unbound_server_lists_nothing",
            "test_a_row_says_whether_its_report_exists",
            "test_the_report_is_the_written_file_as_plain_text",
            "test_the_report_is_served_as_text_plain_utf8",
            "test_a_session_id_with_dotdot_is_refused",
            "test_a_malformed_session_id_is_refused_before_any_read",
            "test_an_id_in_another_script_s_digits_is_refused_before_any_read",
            "test_a_well_formed_id_no_session_has_is_refused",
            "test_a_session_with_no_report_gets_a_plain_refusal",
            "test_the_report_route_never_renames",
            "test_undo_from_history_renames_the_files_back",
        }
    ),
    # M14 Task 5d (U11, U12, U18, U21, W23): the job routes - read (scan, then plan; a stopped read
    # never plans; P2; the new plan served and the old look-alikes dropped; a chosen folder's first
    # read is its bind), GET job and its ETA, cancel, the binding and busy pins, ping/bind's folder.
    "test_webui_read_job.py": frozenset(
        {
            "test_a_new_plan_does_not_serve_the_old_plans_look_alikes",
            "test_a_second_read_of_an_unchanged_folder_hashes_nothing",
            "test_a_stopped_read_never_plans_and_the_next_read_finishes_then_plans",
            "test_the_read_job_runs_the_lock_held_bodies_never_the_commands",
            "test_after_a_read_the_server_serves_the_new_open_plan",
            "test_the_enrich_sentence_is_said_only_when_no_place_fact_exists",
            "test_a_chosen_folder_is_bound_by_its_first_read",
            "test_a_read_body_with_an_unknown_field_is_refused_and_starts_nothing",
            "test_the_job_routes_carry_the_binding_and_the_busy_refusal",
            "test_cancel_is_served_while_busy_and_ignored_on_a_job_that_cannot_stop",
            "test_the_job_is_idle_unbound_and_bound_with_no_job",
            "test_the_job_reports_running_counts_and_the_eta_from_eta_py_s_formula",
            "test_ping_and_bind_name_the_bound_folder",
            "test_a_read_naming_an_earlier_choice_is_refused_and_creates_nothing",
            "test_the_chosen_folder_and_the_binding_are_read_inside_the_bind_lock",
            "test_a_choice_withdrawn_just_before_the_bind_lock_is_not_read",
            "test_a_read_job_writes_into_the_server_s_one_log",
            "test_a_read_publishes_its_plan_inside_the_request_lock",
            "test_a_read_that_rereads_a_changed_photo_does_not_say_nothing_new",
            "test_a_read_without_exiftool_is_refused_before_a_chosen_folder_is_bound",
            "test_a_template_pam_cannot_parse_is_refused_and_starts_nothing",
            "test_a_stopped_read_stores_the_grouping_a_full_scan_stores_and_still_never_plans",
        }
    ),
    # M14 Task 4a Step 2 (U10): the UNBOUND server - every archive route, from the live table AND
    # spelled from design 8.2, is 409 "Choose a folder first."; the allowed set; the bound control.
    # Every test in the module; it keeps no registry.
    "test_webui_launcher.py": frozenset(
        {
            "test_unbound_routes_say_choose_a_folder",
            "test_an_unbound_post_with_a_malformed_body_still_says_choose_a_folder",
            "test_a_read_with_nothing_chosen_says_choose_a_folder_before_its_body_or_exiftool",
            "test_the_unbound_allowed_set_is_spelled_and_answers",
            "test_an_unbound_server_has_no_workspace_and_no_configuration",
            "test_a_bound_pam_review_server_is_unchanged",
            # M14 Task 4b (U10): bind, change of folder, a failed bind - the lock watched at the
            # CALL and held or wanted by a real second process.
            "test_bind_heals_a_crashed_apply",
            "test_a_scanned_folder_binds_holds_its_lock_and_serves_its_plan",
            "test_change_folder_is_refused_while_reading",
            "test_after_change_folder_a_second_console_can_scan_the_old_folder",
            "test_a_failed_bind_leaves_the_server_unbound",
            "test_a_bind_that_fails_after_taking_the_lock_releases_it",
            "test_a_never_scanned_folder_is_chosen_not_bound",
            "test_a_bind_without_a_full_path_is_refused_and_changes_nothing",
            "test_stopping_the_server_releases_its_lock",
            "test_pam_review_servers_have_no_bind_route",
            # M14 Task 4c (U9 / OD-2): `pam start [PATH]` - the announcement, unbound, PATH
            # bound, a network PATH refused, and Ctrl+C's closing block with the lock released.
            "test_pam_start_announces_like_pam_review_with_its_own_purpose",
            "test_pam_start_without_a_path_serves_an_unbound_server",
            "test_pam_start_path_binds_a_scanned_folder",
            "test_pam_start_on_a_network_path_is_refused_with_the_sentence",
            "test_ctrl_c_ends_pam_start_with_its_closing_block_and_releases_the_lock",
            # Task 4 review R1: F1 and F2, built by the review, fixed by the controller.
            "test_a_page_on_the_old_folder_cannot_write_into_the_new_one",
            "test_a_change_of_folder_forgets_the_old_folder_s_look_alikes",
            "test_each_bind_is_numbered_and_ping_names_it",
            "test_a_write_naming_the_old_binding_is_refused_and_the_current_one_is_served",
            "test_a_read_naming_the_old_binding_is_refused",
            "test_a_read_with_no_binding_is_served_a_write_with_none_is_not",
            "test_pam_review_s_server_serves_a_write_with_no_binding",
            "test_the_binding_is_checked_again_inside_the_catalog_lock",
            "test_a_change_of_folder_forgets_the_split_memory",
            "test_a_change_of_folder_mid_request_is_refused_at_the_catalog",
        }
    ),
    # M14 Task 4c (U13): the folder chooser - folders only, sorted, hidden left out, NOTHING
    # opened (watched at the call) - and the one network refusal on both surfaces (chooser and
    # bind), each operand separately, with controls that never skip. Every test in the module;
    # it keeps no registry.
    "test_webui_folders.py": frozenset(
        {
            "test_the_chooser_lists_folders_only",
            "test_the_folders_are_sorted_ignoring_case",
            "test_a_dot_folder_is_left_out",
            "test_hidden_and_system_folders_are_left_out_on_windows",
            "test_listing_a_folder_opens_no_file",
            "test_the_top_level_lists_the_known_archives_and_the_drives",
            "test_the_drives_come_from_listdrives_where_python_has_it",
            "test_the_drives_fallback_probes_every_letter",
            "test_a_network_drive_is_left_out_of_the_drives_and_never_probed",
            "test_a_network_drive_listdrives_reports_is_left_out",
            "test_the_chooser_refuses_an_unknown_parameter_and_a_bad_path",
            "test_a_folder_pam_cannot_see_is_refused_with_resolve_archives_sentence",
            "test_the_chooser_answers_while_bound",
            "test_a_unc_path_is_refused_by_the_chooser_and_by_bind",
            "test_a_network_drive_is_refused_by_the_chooser_and_by_bind",
            "test_a_local_drive_is_not_a_network_drive_on_this_machine",
            "test_the_network_refusal_reads_both_operands_on_any_platform",
            "test_a_posix_network_mount_is_refused_when_the_mount_table_says_so",
        }
    ),
    "test_webui_lookalikes.py": frozenset(
        {
            "test_each_single_names_the_other_and_the_pair_names_neither_each_other_nor_them",
            "test_a_pair_that_looks_like_another_photo_names_it_and_still_not_each_other",
            "test_at_most_five_paths_are_listed_and_the_rest_are_counted",
            "test_a_placeholder_item_is_neither_listed_nor_given_look_alikes",
            "test_the_look_alikes_are_computed_once_per_server",
            # Task 10 review (uncommitted): M2517, M2518, M2519, M2520
            "test_a_placeholder_whose_bytes_a_present_file_still_holds_is_not_listed",
            "test_a_missing_file_whose_bytes_a_present_file_still_holds_is_not_listed",
            "test_one_server_answers_each_plan_for_that_plan",
            "test_the_archive_s_configured_distance_decides_the_groups",
        }
    ),
    # M13 Task 12 (P11): the {seq}-family rewrite's count and equivalence tests.
    "test_families_scale.py": frozenset(
        {
            "test_the_old_body_builds_n_n_minus_1_over_2_names_for_a_full_family",
            "test_a_full_family_builds_names_linear_in_its_highest_number",
            "test_the_new_body_equals_the_old_over_200_seeded_random_lists",
            "test_hand_built_corners_match_the_old_body",
            # Task 12 review (uncommitted): M2560/M2561 (F1)
            "test_the_reference_is_a_copy_and_never_calls_production",
            "test_literal_families_spelled_from_the_spec",
        }
    ),
    # M13 Task 13 (P1-P4, H6's large half): the large library's builder (determinism, the shares
    # read from the TIFF bytes) and the arithmetic every P verdict rests on.
    "private/test_large_library_builder.py": frozenset(
        {
            "test_two_builds_of_one_seed_are_identical_by_manifest",
            "test_a_different_seed_builds_a_different_library",
            "test_mtimes_are_deterministic_too",
            "test_the_count_is_exact_and_the_tree_is_at_most_four_deep",
            "test_the_shares_read_from_the_bytes",
            "test_companion_pairs_share_a_stem_and_a_folder",
            "test_the_builder_refuses_a_destination_that_is_not_empty",
            "test_the_builder_refuses_a_destination_inside_the_repository",
            "test_lost_progress_is_reread_beyond_the_uncommitted",
            "test_p3_holds_only_when_the_resume_walks_past_the_cursor_and_repeats_nothing",
            "test_p4_bound_is_one_and_a_half_times_the_10k_peak",
            "test_the_summary_parsers_read_what_pam_prints",
            "test_every_csv_row_is_the_width_of_the_header_and_names_the_large_library",
            "test_a_phase_that_failed_writes_no_row",
        }
    ),
    # M13 Task 13 Step 2: `tools/_proctree.py` - a launcher's whole tree found and killed, and a
    # reused PID never adopted (Task 16's `ht_run.py` reuses the module).
    "test_proctree.py": frozenset(
        {
            "test_the_tree_is_found_measured_and_killed_children_included",
            "test_descendants_walks_every_level_root_first",
            "test_a_reused_pid_does_not_adopt_an_older_process",
            "test_an_unopenable_process_is_neither_listed_nor_followed",
            # Task 13 review (uncommitted): M2580's closing test
            "test_kill_tree_waits_for_every_process_it_hit",
        }
    ),
    # M13 Task 16 Step 4: HT-M13's tools - `ht_manifest.py snapshot --expect` / `changed`,
    # `ht_run.py`, `ht_check.py r1` / `touch` / `untouch`, each pass beside each refusal. Every
    # test in the module; it keeps no registry.
    "private/test_ht_m13_tools.py": frozenset(
        {
            "test_ht_run_reports_the_trees_pids_and_peak_and_appends_one_row",
            "test_ht_run_peak_is_the_SUM_of_the_trees_working_sets_not_its_largest",
            "test_ht_run_refuses_without_the_separator_or_a_command",
            "test_the_default_paths_are_read_from_TEMP_at_call_time",
            "test_r1_PASSES_an_archive_whose_pam_named_file_is_dated_by_its_mtime",
            "test_r1_FAILS_a_pam_named_file_dated_by_the_name_pam_gave_it",
            "test_r1_does_not_FAIL_a_filename_date_the_name_BEFORE_pam_carried",
            "test_r1_REFUSES_an_archive_with_no_name_pam_wrote",
            "test_r1_REFUSES_an_archive_pam_has_never_seen",
            "test_touch_then_untouch_puts_every_st_mtime_ns_back_EXACTLY",
            "test_a_folder_not_named_Scans_is_refused_and_nothing_is_touched",
            "test_touch_refuses_to_overwrite_a_save_and_touches_nothing",
            "test_a_touch_that_dies_part_way_has_already_written_the_save",
            "test_untouch_refuses_a_missing_saved_name_and_changes_nothing",
            "test_the_tools_never_write_the_real_TEMP_files",
            "test_ht_run_runs_as_a_script",
        }
    ),
    # M15 Task 14 review (concern 5): ht_manifest's own tests, split out of the private HT-M13 module
    "test_ht_manifest_snapshot.py": frozenset(
        {
            "test_snapshot_with_a_wrong_count_says_STOP_and_writes_no_manifest",
            "test_snapshot_with_the_right_count_writes_the_manifest",
            "test_snapshot_refuses_an_expect_that_is_not_a_number",
            "test_changed_PASSES_with_exactly_the_renames_and_every_hash",
            "test_changed_FAILS_when_the_count_is_not_the_one_apply_reported",
            "test_changed_FAILS_when_a_hash_is_gone_even_with_the_right_count",
            "test_changed_counts_a_rename_between_two_identical_files_once",
            "test_changed_refuses_the_two_paths_swapped_and_a_missing_expect",
        }
    ),
    # M13 Task 13a (L1, design rev 32): every `catalog/repo/` statement whose `?` list grows with
    # the archive, driven past the build's own variable limit; red on `7946cf0` except the
    # control and the empty-keep case. Every test in the module; it keeps no registry.
    "test_repo_variable_limit.py": frozenset(
        {
            "test_the_build_refuses_a_statement_past_its_own_variable_limit",
            "test_replace_events_keeps_more_events_than_the_limit_and_deletes_exactly_the_rest",
            "test_replace_events_with_nothing_kept_still_empties_the_table",
            "test_assign_faces_moves_more_faces_than_the_limit_and_no_other",
            "test_set_review_state_marks_more_faces_than_the_limit_and_no_other",
            "test_plan_items_by_id_reads_more_items_than_the_limit",
            "test_plan_ids_for_items_sees_a_plan_whose_only_item_is_past_the_limit",
            # Task 13a review (uncommitted): M2571's closing test
            "test_plan_ids_for_items_are_ascending_where_a_set_would_not_be",
        }
    ),
    "test_unit_date_disagreement.py": frozenset(
        {
            "test_Q2_an_ocr_dated_display_primary_over_a_raw_dated_2003_names_the_raw",
            "test_365_calendar_days_apart_is_NOT_more_than_a_year",
            "test_366_calendar_days_apart_IS_more_than_a_year",
            "test_a_member_dated_EARLIER_than_the_name_is_compared_too",
            "test_the_reason_costs_nothing_its_tier_and_score_equal_an_agreeing_units",
            "test_a_member_dated_by_its_FILENAME_five_years_away_is_not_compared",
            "test_a_member_dated_by_its_MTIME_years_away_is_not_compared",
            "test_a_live_photos_video_dated_by_quicktime_is_compared",
            "test_a_unit_whose_members_agree_has_no_reason",
            "test_the_reason_is_on_EVERY_members_item",
            "test_a_name_with_NO_date_has_nothing_to_disagree_with",
        }
    ),
    # M12 Task 4 (C7, C10, N9, P3, P4): what the faces commands SAY - the closing line counts
    # faces found (HT-M11 D5), the model line the run loaded, the refusal with no digit. Keeps
    # no registry
    "test_faces_command_copy.py": frozenset(
        {
            "test_the_faces_line_counts_faces_found_and_the_model_line_follows_the_skips",
            "test_one_face_is_singular_and_no_faces_is_plural",
            "test_every_other_enricher_keeps_results_stored_and_prints_no_model_line",
            "test_the_command_prints_the_model_its_faces_enricher_LOADED",
            "test_a_faces_enricher_that_loaded_nothing_reports_no_model",
            "test_the_epsilon_refusal_still_suggests_no_value",
        }
    ),
    # M13 Task 14 (C9, H7): `pam faces calibrate`, which REPLACES M12 Task 4's
    # `tools/epsilon_sweep.py` and its `test_epsilon_sweep.py` (both deleted - the command has
    # every option the tool had). The tool's exclusion, counting and tie controls move here
    # unchanged; its required `--floor` control is gone with the tool (the command defaults to
    # `[tiers] faces.floor`). Every table and sentence is spelled by hand. Keeps no registry
    "test_faces_calibrate.py": frozenset(
        {
            "test_exclusion_is_by_the_first_reason_and_the_floor_is_inclusive",
            "test_every_adjacent_pair_of_reasons_is_decided_in_the_documented_order",
            "test_the_counts_at_one_epsilon_are_the_ones_worked_by_hand",
            "test_the_closest_cross_person_pair_is_person_c_and_person_d",
            "test_a_tie_keeps_the_pair_with_the_lowest_ids",
            "test_the_largest_clean_value_is_the_last_one_BEFORE_the_first_merge",
            "test_the_command_over_the_toy_catalog_prints_the_table_worked_by_hand",
            "test_two_people_apart_get_the_largest_clean_value_worked_by_hand",
            "test_one_named_person_gets_the_fewer_than_two_sentence",
            "test_the_people_are_counted_AFTER_the_exclusions",
            "test_a_hidden_person_is_still_a_person_the_sweep_keeps_apart",
            "test_both_catalog_reads_run_inside_one_snapshot",
            "test_the_connection_it_reads_through_refuses_a_write",
            "test_two_model_generations_are_refused_and_nothing_is_swept",
            "test_embeddings_of_two_widths_are_refused_and_nothing_is_swept",
            "test_the_floor_defaults_to_the_archives_faces_floor",
            "test_the_command_never_writes_pam_toml",
            "test_it_answers_while_another_command_holds_the_lock",
            "test_without_the_faces_extra_it_refuses_with_the_extras_own_sentence",
            "test_importing_the_command_imports_neither_numpy_nor_onnxruntime",
        }
    ),
    # M13 Task 15 (H6's medium half, M13-D7): `tools/q_measure.py`, vision Q1/Q2/Q4. The Q1
    # expectation is worked by hand from the tiny library; Q4's checker is the tool's own reading
    # of design 6.2, its independence from `pam.naming` pinned by AST and in a fresh interpreter.
    # Keeps no registry
    "private/test_q_measure.py": frozenset(
        {
            "test_q1_on_the_tiny_library_is_81_of_81_units_worked_by_hand",
            "test_q1_prints_its_line_verbatim_and_defaults_to_the_open_plan",
            "test_q1_a_primary_named_off_its_date_is_a_miss_and_a_member_is_not_counted",
            "test_q1_refuses_a_folder_pam_has_never_scanned",
            "test_q4_on_the_tiny_library_is_100_percent_of_102",
            "test_q4_refuses_one_hand_built_name_per_rule",
            "test_q4_accepts_the_hand_built_valid_names",
            "test_q4_duplicates_are_case_insensitive_per_folder_and_include_kept_files",
            "test_q4_counts_each_broken_name_once_and_says_why",
            "test_the_import_walker_catches_every_spelling",
            "test_the_import_walker_passes_what_is_not_an_import_of_naming",
            "test_the_tool_imports_nothing_from_pam_naming",
            "test_importing_the_tool_loads_no_pam_naming_module",
            "test_q2_score_on_a_literal_csv",
            "test_q2_score_refuses_blanks_and_names_the_rows",
            "test_q2_score_reads_excels_semicolon_save",
            "test_q2_sample_writes_a_blank_sheet_reproducibly_and_never_overwrites",
            "test_percentages_are_floored_never_rounded_up_to_100",
            # Task 15 review: the closing tests of M2613..M2626
            "test_q1_a_qt_utc_primary_is_judged_by_its_UTC_day_at_both_ends_of_it",
            "test_q1_already_conforming_files_are_printed_beside_the_ratio_and_never_in_it",
            "test_q1_a_read_file_with_no_recorded_date_source_is_counted_and_only_that",
            "test_q4_a_case_only_rename_and_a_swap_of_two_names_are_valid",
            "test_q2_sample_obeys_its_seed_and_copies_each_items_name_and_first_reason",
            "test_the_lists_are_at_least_as_long_as_the_census_says",
        }
    ),
    # M12 Task 5 (C8, P2, P3): the server fields the faces page's disclosures are built from -
    # F5's hidden_below_floor, O8's person_labels, the counter on every counted write - and
    # `pam status`'s faces line, with its zero case. Keeps no registry
    "test_faces_disclosures.py": frozenset(
        {
            "test_a_group_whose_faces_are_all_below_the_floor_is_counted_and_the_floor_is_named",
            "test_only_unnamed_unreviewed_faces_below_the_floor_make_a_group_hidden",
            "test_a_restore_names_the_person_its_faces_go_back_to",
            "test_every_counted_write_answers_with_the_counts_after_it",
            "test_status_names_faces_cluster_once_faces_coverage_is_non_zero",
            "test_status_says_nothing_about_faces_cluster_where_faces_never_ran",
            "test_the_faces_line_joins_every_state_of_a_scanned_archive_pam_can_see",
            "test_pam_status_prints_the_faces_line_from_the_coverage_it_computes",
        }
    ),
    # M14 Task 9Y (Y1): `pam status`'s approved line - the open plan's count with k and N spelled,
    # its place under `Plans:`, absent with no open plan, a superseded plan's approvals not
    # counted, and the page's `GET /api/plan/{id}` agreeing on one catalog (X3). The module's
    # other tests are not listed: they were never ruled load-bearing. Keeps no registry.
    "test_status.py": frozenset(
        {
            "test_status_counts_the_open_plans_approved_names",
            "test_the_approved_line_sits_under_the_plans_line_and_changes_no_other",
            "test_status_prints_no_approved_line_without_an_open_plan",
            "test_the_approved_count_is_the_open_plans_and_not_a_superseded_ones",
            "test_status_and_the_page_read_the_same_approved_count",
            # the Task 9Y review (M3463): a skipped name is not an approved one
            "test_a_skipped_name_is_not_counted_as_approved",
            # M15 Task 1 (AM1): the hand-spelled every-status table read through both paths
            # (M3519-M3521), and the cost half's sole guardian, the `plan_rows` spy (M3522)
            "test_status_and_the_page_footer_agree_with_a_hand_spelled_table_on_every_status",
            "test_status_does_not_load_the_plans_rows",
        }
    ),
    # M12 Task 9 (P8): the OCR date grammar. The literal table spelled from P8 (34 rows, floored
    # at collection), the rows from P8's sentences, confidence/text/tie-break, the purity and
    # keyword-only surface, and the second implementation of the rule. Keeps no registry
    "test_ocr_dates.py": frozenset(
        {
            "test_the_p8_table_row_by_row",
            "test_rows_spelled_from_p8s_sentences",
            "test_each_table_test_is_parametrized_over_its_WHOLE_table",
            "test_the_matched_text_is_the_substring_after_nfkc",
            "test_a_match_inside_a_longer_line_yields_only_its_substring",
            "test_confidence_is_the_HIGHEST_scoring_line_holding_the_exact_candidate",
            "test_equal_scores_go_to_the_EARLIEST_line",
            "test_a_line_holding_only_an_AMBIGUOUS_candidate_gives_no_confidence",
            "test_floor_is_keyword_only_and_has_no_default",
            "test_the_grammar_version_is_one",
            "test_the_module_imports_only_the_stdlib_the_plan_names",
            "test_INTERPRETATION_a_month_name_is_read_in_any_case",
            "test_INTERPRETATION_a_match_with_NO_valid_reading_is_not_a_candidate",
            "test_every_candidate_list_up_to_four_lines_agrees_with_the_reference",
            "test_the_reference_rule_matches_p8s_worked_examples",
        }
    ),
    # M12 Task 10 (P7, P9, P10, P11), the unit half: `[enrich.ocr]`'s rules each refused by
    # name, `in_folders` by component, the newest-reading read (selected before validated),
    # `resolve_date`'s four conditions each alone false, the tiers and sentences at every
    # score, and the two score rows charged before the clamp. Keeps no registry
    "test_naming_dates.py": frozenset(
        {
            "test_a_MALFORMED_newest_row_gives_NO_reading_and_never_the_older_one",
            "test_a_malformed_row_is_skipped_with_a_log_line",
            "test_a_superseded_row_is_not_live",
            "test_all_four_conditions_hold_the_ocr_date_is_the_effective_date",
            "test_an_empty_line_list_is_a_reading",
            "test_at_or_above_the_high_mark_only_the_source_row",
            "test_both_deductions_below_the_high_mark",
            "test_condition_1_alone_false_any_other_source_is_kept",
            "test_condition_2_alone_false_out_of_the_folders",
            "test_condition_3_alone_false_the_only_date_is_below_the_floor_and_SILENT",
            "test_condition_3_the_floor_is_the_configured_one",
            "test_condition_4_a_date_EQUAL_to_the_files_is_used",
            "test_condition_4_alone_false_a_date_LATER_than_the_file_is_not_used_and_said",
            "test_config_has_no_ocr_defaults_that_differ_from_the_dataclass",
            "test_date_deductions_is_required",
            "test_each_p10_rule_is_its_own_refusal_naming_the_entry",
            "test_folders_are_kept_exactly_as_written",
            "test_folders_must_be_a_list_of_strings",
            "test_in_folders_is_by_component_and_case_insensitive",
            "test_legal_entries_load",
            "test_no_date_token_no_deduction",
            "test_no_deduction_unless_p9_applied",
            "test_no_reading_no_upgrade",
            "test_only_the_ocr_text_kind_is_read_and_each_file_gets_its_own",
            "test_the_HIGHEST_id_is_the_reading",
            "test_the_deductions_are_charged_BEFORE_the_clamp",
            "test_the_high_mark_is_the_configured_one",
            "test_the_newer_reading_wins_even_when_its_identity_sorts_LOWER",
            "test_the_ocr_defaults_are_design_11s",
            "test_the_people_bonus_on_a_100_still_clamps",
            "test_the_sentence_quotes_the_matched_text_not_the_whole_line",
            "test_the_tier_and_the_provenance_sentence_at_every_score",
            "test_workers_override_and_its_range",
        }
    ),
    # M12 Task 10 - THE SHIPPING GATE, end to end: every expected name and sentence spelled by
    # hand from P9/P11, through `pam plan` and `GET items` (+ `whyLines` under node): P9's four
    # conditions each alone false, B6 at 0.6 / ocr.high / 0.99, I15's lifecycle, I16, I4, I3
    # (events, a unit's one stem), {seq} by OCR date, scope reversibility, adjacent prefixes,
    # the malformed-newest row, N2, the frozen config and the scoped confidence note. No registry
    "test_ocr_naming.py": frozenset(
        {
            "test_I15_the_newest_reading_decides_in_every_direction",
            "test_I16_the_floor_moves_both_ways_without_a_new_reading",
            "test_I3_a_file_P9_does_NOT_upgrade_is_grouped_under_the_date_its_name_carries",  # M2144
            "test_I3_a_unit_takes_ONE_stem_from_its_primary_and_each_member_keeps_its_own_event",
            "test_I3_control_without_the_ocr_dates_the_session_is_one_event",
            "test_I3_one_scanning_session_years_apart_is_three_events",
            "test_I4_only_the_mtime_dated_file_takes_the_ocr_date",
            "test_I7_a_units_date_token_is_its_PRIMARYS_reading_on_every_member",  # M2147
            "test_a_malformed_NEWEST_reading_over_a_valid_older_one_gives_no_date",
            "test_a_mixed_card_names_ONLY_the_capped_model_in_its_note",
            "test_a_token_with_NO_confidence_key_is_not_scored",
            "test_adjacent_prefixes_are_not_the_folder_and_case_does_not_matter",
            "test_all_four_conditions_hold_the_name_carries_the_ocr_date",
            "test_an_ocr_date_scored_EXACTLY_zero_is_a_scored_token",
            "test_condition_1_alone_false_a_filename_date_is_not_replaced",
            "test_condition_2_alone_false_outside_the_folders",
            "test_condition_3_alone_false_a_below_floor_date_is_suppressed_SILENTLY",
            "test_condition_4_alone_false_a_later_date_is_not_used_and_the_plan_says_so",
            "test_condition_4_the_SAME_day_as_the_file_is_used",
            "test_malformed_rows_are_skipped_with_a_log_line_and_the_plan_completes",
            "test_narrowing_the_folders_is_reversible_and_touches_no_reading",
            "test_no_date_token_no_ocr_deduction_and_no_ocr_sentence",
            "test_no_other_token_gains_a_confidence_key",
            "test_seq_control_by_mtime_z_comes_first",
            "test_seq_orders_by_the_EFFECTIVE_capture_dt_across_sources_in_one_scope",  # Stage 2, M2143
            "test_seq_orders_two_same_day_scans_by_their_OCR_date_not_their_mtime",
            "test_the_frozen_config_carries_all_three_ocr_keys",
            "test_the_note_skips_a_token_ONLY_when_it_carries_a_valid_numeric_confidence",
            "test_the_provenance_sentence_at_every_score_through_GET_items_and_the_why_panel",
            "test_two_capped_models_are_their_contribution",
        }
    ),
    # M12 Task 11 (P12) - attention's equivalence obligation as a table through real `pam plan`,
    # spelled from design 6.4 / 6.1 (rev 27): every flag class and date source with a literal
    # expected `attention` (incl. the new OCR operand, the `planner` row and the scene-not-date
    # control); every `consistency.RULES` entry has a row; and the summary's count, the
    # `attention=1` filter and `Approve High+Medium`'s scope over the OCR `medium` unit. No registry
    "test_attention_classes.py": frozenset(
        {
            "test_the_attention_class_table",
            "test_the_table_test_is_parametrized_over_the_WHOLE_table",
            "test_every_consistency_rule_has_a_row",
            "test_the_summary_count_and_the_attention_filter_move_together_and_the_operand_does_not_block",
        }
    ),
    # M12 Task 12 Part B (Step B5) - design 8.1 rev 28's OCR pipeline, P-1 to P-11, row by row,
    # every expectation a literal from the table; incl. R42-C's solid-colour tensor test, the SOLE
    # guardian of DET_CHANNELS. Every test in the module. No registry
    "test_ocr_pipeline.py": frozenset(
        {
            "test_working_size_is_p1_bounded_and_checked_before_any_resize",
            "test_working_image_resizes_to_the_target_and_passes_an_in_bounds_image_through",
            "test_det_size_is_the_references_arithmetic",
            "test_a_thin_image_is_a_per_file_failure_and_never_no_text",
            "test_the_det_max_side_refusal_comes_before_any_resize",
            "test_a_pure_red_image_is_channel_2_in_both_tensors",
            "test_dilate_grows_each_pixel_right_and_down_and_nowhere_else",
            "test_a_diagonal_only_touch_joins_one_component",
            "test_two_runs_one_column_apart_are_separate",
            "test_a_u_shape_is_one_component_its_two_arms_joined_by_a_later_row",
            "test_components_come_in_raster_order_of_their_first_pixel",
            "test_only_the_first_max_candidates_components_are_kept_in_raster_order",
            "test_each_drop_rule_fires_exactly_once_and_the_survivors_are_boxed_by_hand",
            "test_scale_back_rounds_half_integers_to_even",
            "test_a_box_is_clipped_at_every_image_border",
            "test_the_post_unclip_size_rule_either_side_of_its_boundary",
            "test_at_the_tables_min_size_neither_of_those_components_survives_p5",
            "test_reading_order_is_order_pngs_geometry_as_numbers",
            "test_equal_keys_keep_their_p4_order",
            "test_a_crop_is_half_open_over_the_box",
            "test_a_zero_width_or_zero_height_box_is_skipped",
            "test_a_crop_exactly_rotate_ratio_tall_is_turned_counter_clockwise",
            "test_a_crop_just_below_rotate_ratio_is_not_turned",
            "test_rec_tensor_shape_arithmetic_and_zero_padding",
            "test_rec_tensor_normalises_a_pixel_to_x_over_255_minus_half_over_half",
            "test_charset_is_blank_then_the_metadata_lines_then_a_space",
            "test_repeats_collapse_and_the_score_is_the_mean_of_the_kept_probabilities",
            "test_a_blank_between_two_equal_letters_keeps_both",
            "test_leading_and_trailing_spaces_are_stripped_but_still_scored",
            "test_an_all_blank_row_is_no_line",
            "test_a_line_that_is_only_spaces_is_no_line",
            "test_argmax_takes_the_first_index_on_an_exact_tie",
        }
    ),
    # M12 Task 12 Part B (Steps B6-B8) - the REAL Engine on a fake factory (providers per session,
    # the charset, pack validation before the factory, the 0.45 line), describe's Q4 sentences,
    # identity's Q2 inputs by hand, I17's lock, the fixture inventory, and the ocr-marked truth /
    # K11 / baseline / provider tests. Every test in the module. No registry
    "test_ocr_model.py": frozenset(
        {
            "test_constructing_an_engine_builds_nothing",
            "test_each_session_is_built_with_exactly_the_cpu_provider_list",
            "test_a_session_reporting_azure_is_refused_runs_nothing_and_is_retried",
            "test_a_charset_that_does_not_match_the_output_is_refused",
            "test_a_pack_that_fails_validation_is_refused_inside_read_before_the_factory",
            "test_a_line_scoring_0_45_is_returned_because_there_is_no_score_filter",
            "test_the_receipt_names_the_identity_and_each_sessions_providers",
            "test_a_failure_after_the_providers_check_publishes_no_receipt_and_is_retried",
            "test_an_image_below_min_side_reads_as_no_lines_and_runs_no_graph",
            "test_ocr_providers_ARE_the_faces_providers_object",
            "test_describe_an_unset_model_dir",
            "test_describe_a_folder_without_a_file",
            "test_describe_a_file_whose_bytes_are_not_the_pinned_ones",
            "test_describe_the_recogniser_is_checked_too",
            "test_describe_names_a_recogniser_whose_bytes_are_not_the_pinned_ones",
            "test_describe_accepts_a_pack_that_hashes_to_the_pins",
            "test_describe_names_the_extra_when_it_is_missing",
            "test_the_pinned_pack_constants_are_q1s",
            "test_the_identity_rows_are_exactly_q2s_inputs_both_ways",
            "test_the_identity_moves_when_each_input_moves",
            "test_the_identity_is_the_same_for_every_folder",
            "test_the_ocr_modules_never_reach_the_eager_import_closure",
            "test_eight_threads_on_a_cold_engine_build_each_session_once_and_never_overlap",
            "test_a_failed_initialisation_releases_the_lock_and_the_waiter_initialises",
            "test_the_fixture_inventory_is_the_same_thirteen_everywhere",
            "test_every_governed_fixture_meets_truth_json",
            "test_no_k11_fixture_yields_a_date_truth_does_not_name",
            "test_every_fixture_matches_the_regression_baseline",
            "test_the_real_sessions_run_on_the_cpu_provider_only",
            "test_the_ocr_marked_tests_are_exactly_the_ones_written_down",
            "test_the_fake_recogniser_output_really_decodes_to_one_line_below_the_floor",
        }
    ),
    # M12 Task 13 (P7, P13, Q2-Q4, I8, I17, R9-R11): the `ocr` enricher's controls - each of
    # Step 8's named mutations has its killer here (wants' operands, the placeholder OPEN, only
    # `ocr_text` rows, the marker on an empty reading, the raise retried, `line_lists`, upright
    # and RGB, lazy and once, the lock released and not held around decoding, both refusals,
    # Q4's and P13's sentences, and the 0.45 line decided at plan time).
    "test_ocr_enricher.py": frozenset(
        {
            "test_wants_is_true_when_all_four_operands_hold",
            "test_wants_refuses_each_operand_on_its_own",
            "test_a_cloud_placeholder_in_scope_is_never_OPENED",
            "test_the_open_watch_would_actually_see_an_open",
            "test_a_file_with_lines_stores_exactly_one_ocr_text_row_and_no_other_kind",
            "test_enrich_returns_exactly_one_ocr_text_fact_and_nothing_else",
            "test_a_file_with_no_text_is_covered_and_skipped_on_the_second_run",
            "test_an_engine_that_raises_stores_nothing_and_the_file_is_read_again",
            "test_a_malformed_line_from_the_engine_is_dropped_and_counted",
            "test_a_line_with_no_text_is_dropped_and_counted",
            "test_an_orientation_6_picture_reaches_the_engine_UPRIGHT_and_RGB",
            "test_a_greyscale_picture_reaches_the_engine_as_RGB_with_equal_channels",
            "test_a_run_builds_each_session_once_however_many_files_it_reads",
            "test_an_all_cached_run_makes_ZERO_factory_calls",
            "test_a_failed_initialisation_fails_one_file_and_the_next_read_retries_it",
            "test_the_lock_is_released_when_a_read_raises_and_the_waiter_completes",
            "test_two_workers_decode_at_the_same_time",
            "test_four_workers_store_the_same_readings_as_one_compared_by_content_hash",
            "test_the_bounded_lock_excludes_a_second_holder",
            "test_the_barrier_loader_refuses_a_decode_that_arrives_alone",
            "test_ocr_hits_the_digest_refusal_and_faces_still_hits_its_sink_refusal",
            "test_run_enricher_refuses_a_refresh_for_ocr_before_any_version_comparison",
            "test_the_refresh_refusal_is_p13s_sentence_and_exits_one",
            "test_an_unusable_pack_prints_q4s_sentence_exits_one_and_stores_nothing",
            "test_enabled_with_no_folders_prints_p13s_two_lines_and_exits_zero",
            "test_p13s_closing_lines_and_the_model_line",
            "test_a_line_scored_0_45_is_stored_and_the_floor_decides_at_plan_time_only",
            "test_enrich_then_plan_names_the_scan_with_the_date_and_says_where_it_came_from",
            # Task 13 Stage 2 review (M2268/M2269): both rasters `enrich` makes are CLOSED.
            "test_the_upright_frame_and_the_RGB_copy_are_both_CLOSED_by_the_time_enrich_returns",
            # Stage 1 F2 (`8faa575`): the SOLE killer of the `startswith(pack)` operand (M2270).
            "test_a_missing_extra_keeps_the_doctor_pointer_and_exits_one",
        }
    ),
    # M12 Task 12 Part B (Step B1) - Q1's `[ocr]` literal and its equality with `[faces]`
    "test_deps.py": frozenset(
        {
            "test_the_ocr_extra_is_exactly_q1s_two_requirements_and_equal_to_faces",
        }
    ),
    # M12 Task 14 Step 1 - the chain twice (tiny library; scratch copy with `Scans/`), the
    # real-pack run of it (`ocr`-marked), and the two controls that keep its fixture honest.
    "test_t1_ocr_chain.py": frozenset(
        {
            "test_the_m12_chain_over_the_tiny_library_with_ocr_on_touches_nothing_outside_its_folders",
            "test_the_m12_chain_puts_an_OCR_date_on_disk_and_takes_it_off_again",
            "test_the_m12_chain_on_the_REAL_pack_reads_the_rendered_imprints",
            "test_the_rendered_scans_are_what_the_tables_say",
            "test_the_stub_reads_each_image_its_own_line_and_nothing_past_the_script",
            "test_the_unit_stem_oracle_sees_a_split_it_is_shown",
        }
    ),
    # --- M13 Task 1: F1 (HT-M12, ruled 2026-10-08) - apply renames only what was approved ------
    # The suite's own approval helpers. Every test that applies a plan stands on them since F1;
    # one that approved a `skipped` item or reached another plan would quietly change what every
    # "a skip is not renamed" test means (mutation 12 of the Task 1 table).
    "test_archives_helpers.py": frozenset(
        {
            "test_approve_all_leaves_skipped_and_touches_only_its_plan",
            "test_approve_ids_approves_exactly_the_ids_it_is_given",
        }
    ),
    # `naming/accept.py`, the ONE definition of what `--all` (and Task 2's `Accept all`)
    # selects: the literal table, its wiring, and decide-filter's moved Low exclusion.
    "test_accept.py": frozenset(
        {
            "test_accept_all_selects_exactly_what_the_ruling_says",
            "test_the_table_really_reaches_the_parametrized_test",
            "test_accept_all_takes_a_one_shot_iterable",
            "test_exclude_low_units_keeps_order_and_counts_items_dropped",
            "test_exclude_low_units_with_no_low_unit_drops_nothing",
            # M14 OD-1: `also_hold`'s default changes nothing (T3 mutation 10), and its hold
            "test_the_default_holds_nothing_but_low_exactly_as_m13_ruled",
            "test_also_hold_holds_a_unit_whole_when_any_member_needs_a_look",
            "test_also_hold_never_reaches_a_decided_unit",
        }
    ),
    # The property itself, with a spy on `perform_verified_rename` (M697): exactly the approved
    # items, the refusal and its only-one-operand states, `--all`, the unit rule, the copy.
    "test_apply_approval.py": frozenset(
        {
            "test_apply_renames_exactly_the_approved_items_and_calls_rename_for_nothing_else",
            "test_an_untouched_plan_is_refused_and_nothing_moves",
            "test_a_plan_whose_only_approved_item_is_a_no_op_is_not_refused",
            "test_a_plan_with_nothing_approved_and_one_skip_is_refused",
            "test_some_approved_reports_what_it_left",
            "test_a_fully_approved_plan_prints_no_left_line",
            "test_all_accepts_everything_but_low_and_never_a_skip",
            "test_all_names_the_low_count_it_left_at_the_confirmation",
            "test_all_over_a_plan_with_nothing_acceptable_is_refused",
            "test_all_over_a_plan_whose_every_item_is_skipped_says_so",
            "test_cancelling_all_writes_no_approval",
            "test_all_never_unskips_a_member_of_a_mixed_unit",
            "test_a_unit_half_approved_is_left_whole",
            "test_skipped_beats_undecided_in_one_unit",
            "test_the_confirmation_counts_approved_only",
            # M14 OD-1: `--all` passes no `also_hold` (T3 mutation 10, the CLI's half)
            "test_all_calls_the_one_accept_rule_with_no_extra_hold",
        }
    ),
    # M13 Task 1 STAGE 2: the shipping gate driven end to end - the real server widening one id,
    # `pam apply`/`plan`/`undo` as subprocesses, a walk+sha256 oracle written in the module -
    # plus the oracle's own control (it must see a rename and a changed byte).
    "test_f1_stage2.py": frozenset(
        {
            "test_the_oracle_sees_a_rename_and_a_changed_byte",
            "test_f1_end_to_end_apply_renames_only_what_was_approved_then_all_then_undo",
        }
    ),
    # M14 Task 6 STAGE 2: the page's rename and undo through the REAL `pam start` / `pam review`
    # in a subprocess, over HTTP only, measured with `tools/ht_manifest.py` and the module's own
    # SQL - steps (a)-(h), B1's closing block, a reload mid-rename, and the three findings
    # S2-F1..S2-F3 as strict xfails until production is fixed. Step (i) lands at Task 7.
    "test_webui_rename_stage2.py": frozenset(
        {
            "test_stage2_a_to_f_the_page_renames_what_it_approved_and_puts_it_back",
            "test_stage2_g_a_page_rename_that_dies_mid_unit_is_recovered_by_the_next_request",
            "test_stage2_h_the_server_killed_mid_rename_is_put_right_by_the_next_start",
            "test_stage2_pam_review_closing_block_after_a_page_rename_of_one_file",
            "test_stage2_a_reload_mid_rename_finds_it_running_and_list_latency_is_recorded",
            "test_s2_f1_a_died_rename_still_names_the_session_that_renamed_files",
            "test_s2_f2_recovery_at_a_start_reaches_the_page",
            "test_s2_f3_an_exited_process_is_not_a_live_lock_holder",
            # M14 Task 7 review: Stage 2's step (i) (X2), an older session undone from History.
            "test_stage2_i_an_older_session_is_undone_through_its_history_row",
        }
    ),
    # M13 Task 2: `Accept all` through the real server - the mixed unit `_widen_to_units` would
    # get wrong (its skip re-approved), compared to a hand-spelled literal and to
    # `naming.accept.accept_all` on the same rows; and the filter field refused by name. The
    # module's other tests are not census-guarded; it keeps no registry.
    "test_webui_decide.py": frozenset(
        {
            "test_accept_all_approves_every_undecided_unit_but_low_and_never_a_skip",
            "test_accept_all_refuses_a_filter_field_by_name_and_writes_nothing",
            # M13 Task 2 review (M2395, M2396): the flag must be `true`; the action must be approve
            "test_accept_all_must_be_exactly_true_and_writes_nothing_otherwise",
            "test_accept_all_only_approves_and_writes_nothing_for_another_action",
            # M14 Task 0D (M13 V3): an unknown body field is refused in both branches; the
            # known set is spelled from design 8.2
            "test_an_unknown_body_field_is_refused_by_name_and_never_reaches_the_writer",
            "test_the_known_body_fields_are_exactly_design_8_2s",
            # M14 Task 5g (Z3, design 6.3 rule 2): rename and restore refuse a stem whose 5.4 key
            # another file in the folder holds, name it, and never call the writer; both read the
            # bound archive's `scan.ignore_stem_suffixes`
            "test_a_stem_whose_key_a_file_on_disk_holds_is_refused_by_name_and_never_written",
            "test_restore_refuses_a_stem_whose_key_another_item_now_holds",
            "test_both_endpoints_read_the_archives_ignore_stem_suffixes",
            # M14 Task 5g review: the key is case-insensitive (M3213), carries its folder in the
            # catalog and in the reservations (M3214, M3225), and a missing row holds one (M3221)
            "test_a_stem_held_in_another_case_is_refused",
            "test_a_stem_held_only_in_another_folder_is_free",
            "test_a_missing_files_row_holds_its_stem_key_in_the_review_ui",
            # Z3.1 / the Task 5g review's Q2: only the unit's files on disk free their key, so a
            # proposal never frees a key its files do not hold
            "test_a_units_proposed_name_does_not_free_a_key_its_files_do_not_hold",
        }
    ),
    # M14 Task 5g (Z3, finding 5f-F1, vision S3): a stem is unique per folder. 5f-F1 end to end
    # (two same-day events, then apply and a re-scan store no grouping), the `.acr_` key, the
    # unit's own names free to it, a case-only extension clash, a reservation holding its key,
    # and the seed reading the keys. Every test in the module; it keeps no registry.
    "test_stem_unique_per_folder.py": frozenset(
        {
            "test_two_same_day_events_get_different_stems_and_a_rescan_binds_nothing",
            "test_an_ignored_editor_suffix_holds_its_stem_key_against_a_new_name",
            "test_an_already_conforming_unit_keeps_its_name",
            "test_a_unit_whose_members_share_a_key_with_each_other_is_unaffected",
            "test_a_lowercase_jpg_clashes_with_an_existing_uppercase_mov_of_the_same_stem",
            "test_a_reservation_holds_its_key_for_the_next_unit",
            "test_a_thousand_keys_held_by_ANOTHER_extension_are_seeded_past_not_walked",
            # the Task 5g review: a reservation is filed under its folder (M3219), and a missing
            # row holds its key (M3216)
            "test_two_same_day_events_one_folder_down_get_different_stems",
            "test_a_missing_files_row_holds_its_key_against_a_new_name",
            # Z3.1 (5g-F1): a conforming unit keeps its own stem beside a missing row and an
            # ungrouped file of that stem; the own key frees disk files, never a reservation; and
            # Q1: a reservation into the key index needs its folder
            "test_a_conforming_unit_beside_a_missing_row_of_its_own_stem_keeps_its_name",
            "test_a_conforming_unit_beside_an_ungrouped_file_of_its_own_stem_keeps_its_name",
            "test_the_own_key_frees_the_files_on_disk_and_never_a_name_this_plan_reserved",
            "test_reserving_into_the_key_index_needs_the_folder",
        }
    ),
    # M14 Task 5h (Z2, R1-F1 route (c), vision S3): `pam plan` refuses over a grouping the
    # catalog does not match. Route (c) (a real `run_scan` interrupted before its grouping commit)
    # and its exit code, the two `pam.toml` edits (an ignored suffix removed, a sidecar extension
    # added - the current-config classification), the open plan left as it was, the check after
    # recovery, no false refusal (5f-F1's apply and undo, a placeholder member, a missing member),
    # each operand of the comparison (media type, kind, membership), and the spec's sentence.
    # Every test in the module; it keeps no registry.
    "test_stale_grouping.py": frozenset(
        {
            "test_a_scan_interrupted_before_its_grouping_commit_is_refused_until_a_scan_finishes",
            "test_the_refusal_exits_1_through_the_cli_and_prints_the_sentence",
            "test_removing_an_ignored_stem_suffix_without_a_rescan_is_refused",
            "test_an_ignored_stem_suffix_under_a_complete_scan_is_not_refused",
            "test_adding_a_sidecar_extension_without_a_rescan_is_refused",
            "test_an_open_plan_survives_the_refusal_unchanged",
            "test_removing_mov_from_video_exts_without_a_rescan_is_refused",
            "test_a_stored_kind_that_is_not_5_4s_is_refused",
            "test_a_member_replaced_under_an_interrupted_scan_is_refused",
            "test_the_check_runs_after_recovery_and_plans_by_the_recovered_paths",
            "test_no_refusal_after_an_apply_or_an_undo_of_two_same_day_events",
            "test_a_placeholder_member_under_a_complete_scan_is_not_refused",
            "test_a_missing_member_under_a_complete_scan_is_not_refused",
            "test_a_refused_plan_never_reaches_the_plan_writer",
            "test_scan_and_plan_each_use_the_one_shared_grouping_and_nothing_else_decides_it",
            "test_decide_groups_keeps_only_known_members_and_drops_a_group_it_cannot_anchor",
        }
    ),
    # --- this module's own controls --------------------------------------------------------------
    "test_census.py": frozenset(
        {
            "test_the_census_names_m11s_three_unanchored_survivors",
            "test_defined_tests_ignores_a_nested_def",
            "test_defined_tests_reads_a_test_class_method",
            "test_census_errors_names_a_missing_module_a_missing_control_and_an_empty_entry",
            "test_the_hook_fails_collection_when_a_guarded_module_is_gone",
            "test_the_real_tree_passes_the_census",
            "test_registered_names_reads_dict_set_and_frozenset_literals_and_refuses_a_comprehension",
            "test_a_removed_registration_fails_the_census",
            "test_an_unmapped_registry_is_reported",
            "test_registered_names_reads_every_form_the_suite_uses",
            "test_every_census_control_is_collected",
            # M14 Task 7 review (concern 12): an unlisted module declaring a registry.
            "test_a_registry_in_a_module_the_census_does_not_list_is_reported",
            "test_each_floor_fails_below_it_and_passes_at_it",
        }
    ),
    # M15 Task 2 Stage 1 review (M3543, S1-F1): AM2 through BOTH routes - the module was deleted
    # whole with the suite green (6039 passed), because its only other cover is the M14 hunt's
    # Q8 page case. Every test in the module; it keeps no registry.
    "test_apply_cloud_only_now.py": frozenset(
        {
            "test_a_mixed_plan_renames_the_rest_and_skips_the_unit_whole",
            "test_an_all_cloud_only_plan_renames_nothing",
            "test_a_unit_whose_only_cloud_only_member_is_its_no_op_is_skipped_whole",
            "test_the_retry_route_renames_the_unit_once_it_is_local_again",
            "test_undo_puts_back_a_file_that_became_cloud_only_after_the_rename",
            "test_the_cases_drive_both_routes",
            "test_the_console_says_am2s_sentence_only_when_a_unit_was_skipped",
            "test_the_report_carries_both_counts_and_history_reads_the_files_back",
            # M15 Task 5b: C6.5's singular and TOGETHER; C6.8(b)'s dropped zero line
            "test_one_file_is_singular_and_together_follows_only_a_unit_of_several",
            "test_the_report_drops_a_zero_not_renamed_line_over_the_cloud_only_line",
            # M15 Task 5b review (M3906): the units read back past a thousand
            "test_history_reads_both_counts_back_past_a_thousand_units",
        }
    ),
    # M15 Task 6 review (C7): the flat-folder cost pin (M3711's sole guardian) and the seed's
    # case/member/oracle controls (M3703, M3704, M3706, M3712 survived until these landed).
    "test_collision.py": frozenset(
        {
            "test_a_flat_folder_costs_each_unit_the_SAME_work_however_large_the_folder_is",
            "test_the_seed_lowercases_the_EXTENSION_as_written_for_every_member",
            "test_the_seed_lowercases_the_PREFIX_a_scene_label_puts_in_front_of_the_number",
            "test_a_name_whose_digit_is_not_an_ASCII_digit_neither_blocks_nor_crashes",
            "test_the_spelled_lookup_agrees_with_a_parse_of_every_name",
            "test_the_seed_reads_the_FIRST_member_extension_not_just_the_last",
        }
    ),
    # M15 Task 7 Stage 1 review (M3819): E6, recovery's report of an interrupted run - the module
    # was deleted whole with the suite green; nothing else in the suite reads a recovery report.
    # Every test in the module; it keeps no registry.
    "test_recovery_report.py": frozenset(
        {
            "test_the_m14_fixtures_are_there",
            "test_a_rename_the_crash_interrupted_after_it_happened_is_reported_as_renamed",
            "test_a_rename_the_crash_stopped_before_it_happened_is_reported_as_not_renamed",
            "test_a_unit_put_back_by_phase_two_is_reported_put_back",
            "test_a_crash_after_any_phase_still_ends_in_one_complete_correct_report",
            "test_a_crash_after_the_journal_is_final_and_before_the_report_is_retried",
            "test_a_report_a_crash_cut_short_is_written_again_whole",
            "test_a_split_unit_that_cannot_heal_yet_is_reported_unresolved_and_named",
            "test_a_failed_item_with_no_journal_row_is_named_not_renamed_with_no_reason",
            "test_a_session_with_no_journal_rows_gets_no_report",
            "test_an_ordinary_apply_writes_a_complete_report_and_recovery_leaves_it_alone",
            "test_an_undo_session_gets_no_report",
            "test_an_m14_report_of_an_interrupted_run_stays_byte_identical",
            "test_an_m14_report_of_a_finished_run_stays_byte_identical",
            "test_a_complete_recovery_report_is_never_rewritten",
            "test_access_denied_on_the_reports_folder_does_not_stop_recovery_or_the_command",
            "test_report_is_final_spelled_case_by_case",
            "test_the_ordinary_writer_and_the_recovery_writer_share_one_path_spelling",
            "test_a_later_plans_items_are_never_listed_in_an_earlier_sessions_report",
            "test_the_recovered_at_line_names_the_time_recovery_wrote_it_not_the_start",
            "test_the_never_decided_statuses_are_written_down",
            "test_a_session_an_older_pam_recovered_and_the_user_undid_is_reported_put_back",
            "test_a_report_a_power_cut_left_empty_is_written_again",
            "test_a_session_with_a_row_still_in_intent_is_not_reported",
            "test_a_disk_full_while_writing_the_report_does_not_stop_the_command",
            "test_only_this_plans_unrenamed_items_without_a_journal_row_are_listed",
            # Task 7 Stage 2 rulings F1 / F2: "interrupted" only for what THIS pass recovered
            "test_a_deleted_report_of_a_finished_run_is_not_called_interrupted",
            "test_a_deleted_reports_folder_brings_every_report_back_neutral",
            "test_only_the_session_this_pass_recovered_is_called_interrupted",
            "test_a_session_only_phase_three_finalized_stopped_part_way",
            "test_a_session_only_phase_two_healed_stopped_part_way",
            "test_a_session_an_older_pam_recovered_then_followed_by_another_is_reported_neutral",
            "test_the_neutral_line_names_the_time_it_was_written",
            # Task 7 follow-up review (M3850, M3852): phase 1 alone, and a strand is not a put-back
            "test_a_session_only_phase_one_resolved_stopped_part_way",
            "test_a_pass_that_could_not_put_a_member_back_is_not_called_interrupted",
        }
    ),
    # M15 Task 8 (E1): a newer pam's catalog is refused by every reader. The census of catalog
    # opens, each consumer's own refusal row, the read-only placement and the forward schema.
    # Every test in the module; it keeps no registry.
    "test_catalog_compat.py": frozenset(
        {
            "test_every_catalog_open_in_pam_is_in_the_census",
            "test_the_census_classifies_every_site_and_bound_sites_are_the_webuis",
            "test_the_census_walk_sees_a_raw_open_it_was_not_told_about",
            "test_a_fully_known_catalog_is_compatible",
            "test_a_catalog_with_no_migrations_table_is_compatible",
            "test_an_unknown_update_is_refused_with_e1s_sentence",
            "test_the_refusal_is_an_ordinary_pam_error_exit_one",
            "test_it_compares_by_name_not_by_count",
            "test_it_reads_every_row_not_only_the_newest",
            "test_the_sentence_names_the_newest_unknown_update",
            "test_check_compatible_writes_nothing",
            "test_migrate_itself_refuses_a_newer_catalog_before_applying_anything",
            "test_a_changed_checksum_keeps_its_existing_message",
            "test_a_real_forward_schema_is_refused_by_the_shipped_set",
            "test_every_checked_site_has_its_own_refusal_row",
            "test_every_consumer_refuses_a_newer_catalog_and_writes_nothing",
            "test_a_second_open_in_the_same_command_is_checked_too",
            "test_the_check_runs_read_only_before_the_first_writable_connection",
            "test_a_fully_known_catalog_opens_through_every_kind_of_consumer",
            # Task 8 Stage 1 review: F1 (M4012) and F2 (M4013, M4014)
            "test_every_released_migration_name_is_still_shipped",
            "test_a_catalog_a_released_pam_wrote_is_compatible",
            "test_the_alias_guard_sees_every_way_to_rename_an_opener",
            # Task 8 S2-F1: `pam status` / bare `pam` with no --archive name each newer folder
            "test_status_names_the_newer_folder_and_still_reports_the_healthy_one",
            "test_status_with_every_folder_healthy_is_unchanged_and_exits_zero",
            "test_status_with_only_a_newer_folder_names_it_and_exits_one",
            "test_bare_pam_names_the_newer_folder_and_still_guides_the_healthy_one",
            # S2-F1 reviewer closing tests (M4177, M4179)
            "test_bare_pam_shows_a_newer_folder_as_its_path_and_sentence_and_nothing_else",
            "test_a_folder_failing_for_another_reason_is_not_dressed_as_a_newer_catalog",
        }
    ),
    # M15 Task 9 (OD-8, OD-12): the version grammar and order, the package version, the
    # distribution name. Every test in the module; it keeps no registry.
    "test_update_version.py": frozenset(
        {
            "test_the_package_version_is_od_8s",
            "test_pyproject_and_the_package_agree_on_the_version",
            "test_the_distribution_is_photo_archive_manager_and_the_command_stays_pam",
            "test_the_package_version_parses_under_its_own_grammar",
            "test_the_tables_are_not_empty",
            "test_each_version_sorts_below_the_next",
            "test_the_whole_table_sorts_into_its_written_order",
            "test_a_valid_version_round_trips",
            "test_garbage_is_refused",
            "test_a_non_string_is_refused",
            "test_the_refusal_is_a_pam_error_naming_the_text",
            "test_rc_is_known_and_a_release_is_not",
            "test_the_fields_are_numbers",
            "test_the_module_imports_only_the_standard_library_and_pam",
        }
    ),
    # M15 Task 9 (E5): the release manifest, every field and relation, one bad manifest per
    # rule. Every test in the module; it keeps no registry.
    "test_update_manifest.py": frozenset(
        {
            "test_the_good_manifests_are_accepted",
            "test_the_bad_table_has_a_row_for_every_field",
            "test_a_bad_manifest_is_refused_by_its_own_rule",
            "test_the_refusal_is_a_pam_error",
            "test_validation_touches_no_filesystem",
            "test_parse_reads_json_text_and_bytes",
            "test_text_that_is_not_one_json_object_is_refused",
            "test_a_duplicate_key_is_refused_even_when_the_last_value_is_good",
            "test_to_json_round_trips",
        }
    ),
    # M15 Task 9 (E5): `tools/release_manifest.py`, literals only. Every test in the module;
    # it keeps no registry.
    "test_release_manifest_tool.py": frozenset(
        {
            "test_it_writes_the_manifest_beside_the_installer",
            "test_the_default_version_is_the_packages",
            "test_a_release_candidate",
            "test_a_payload_whose_inventory_differs_is_refused",
            "test_a_payload_without_a_migrations_folder_is_refused",
            "test_an_installer_named_for_another_version_is_refused",
            "test_a_channel_that_disagrees_with_the_version_is_refused",
            "test_a_missing_installer_is_refused",
            "test_an_existing_manifest_is_never_overwritten",
            "test_the_source_inventory_is_this_pams",
        }
    ),
    # M15 Task 9 (E12): the compatibility ledger - reading, appending, the reservation before a
    # migration, the three fault-harness boundaries, registration on open. Every test in the
    # module; it keeps no registry.
    "test_schema_ledger.py": frozenset(
        {
            "test_under_pytest_the_ledger_is_this_tests_private_file",
            "test_a_module_scoped_fixture_is_isolated_too",
            "test_the_guard_watches_the_spec_s_default_files",
            "test_the_guard_sees_a_created_and_an_appended_file_and_nothing_else",
            "test_an_escape_by_monkeypatch_subprocess_or_empty_override_fails_the_run",
            "test_an_escape_in_a_module_scoped_fixture_fails_the_session",
            "test_the_windows_handle_appends_wherever_its_file_pointer_is",
            "test_a_catalog_whose_first_migration_never_committed_opens_and_registers_nothing",
            "test_a_failed_append_to_a_readable_ledger_runs_no_migration_sql",
            "test_the_windows_default_is_localappdata_pam_install",
            "test_without_localappdata_it_is_under_the_platform_default_home",
            "test_a_custom_pam_home_does_not_move_the_ledger",
            "test_a_catalog_under_a_custom_pam_home_registers_in_the_one_ledger",
            "test_a_missing_ledger_is_no_constraint",
            "test_an_empty_ledger_is_no_constraint",
            "test_the_level_is_the_max_over_the_lines_not_the_last_line",
            "test_garbage_lines_are_ignored",
            "test_a_truncated_last_line_is_ignored",
            "test_a_crlf_line_still_counts",
            "test_only_garbage_is_no_constraint",
            "test_an_unreadable_ledger_is_refused_with_a_sentence",
            "test_an_access_denied_read_is_refused",
            "test_reserve_creates_the_folder_and_appends_one_line",
            "test_each_append_is_one_write_under_64_bytes_then_one_fsync",
            "test_a_short_write_is_a_failed_reservation",
            "test_a_name_that_is_not_a_migration_is_never_written",
            "test_an_unwritable_ledger_makes_reserve_refuse_naming_the_path",
            "test_register_appends_only_when_higher",
            "test_register_on_an_unwritable_ledger_logs_and_does_not_raise",
            "test_register_on_an_unreadable_ledger_writes_nothing",
            "test_two_processes_appending_at_once_lose_no_line",
            "test_migrating_a_new_catalog_reserves_the_newest_shipped_migration",
            "test_the_reservation_is_durable_before_the_first_executescript",
            "test_a_reservation_that_cannot_be_written_migrates_nothing",
            "test_nothing_pending_reserves_nothing",
            "test_an_upgrade_reserves_the_newest_pending_not_the_first",
            "test_a_crash_before_the_reservation_leaves_both_untouched",
            "test_a_crash_after_the_reservation_leaves_the_ledger_higher_than_the_catalog",
            "test_a_crash_after_one_migrations_commit_keeps_the_reservation",
            "test_a_crash_after_the_last_migrations_commit_leaves_ledger_and_catalog_equal",
            "test_a_crash_in_a_real_process_after_the_reservation_is_seen_by_the_next_one",
            "test_a_copied_already_migrated_catalog_is_registered_on_first_open",
            "test_every_kind_of_open_registers",
            "test_registration_does_not_lower_or_repeat",
            "test_a_failed_registration_does_not_stop_the_open",
            "test_a_refused_catalog_is_not_registered",
            "test_a_catalog_nothing_has_migrated_registers_nothing",
        }
    ),
    # M15 Task 13B (E11, E12, E13, OD-9, Q8; rulings 3-8): the installer COMPILED AND RUN as a test
    # build - its refusals, the setup.lock order, the ledger, components, cache, PATH, failure and
    # Repair. Every test in the module; marked `inno`, required on the installer job.
    "test_installer_behaviour.py": frozenset(
        {
            "test_a_silent_install_writes_the_record_the_cache_and_shortcuts_inside_its_own_root",
            "test_uninstall_removes_what_it_installed_and_keeps_every_file_of_the_users",
            "test_an_uninstall_with_nothing_of_the_users_leaves_no_folder",
            "test_setup_refuses_while_a_pam_runs_after_the_grace",
            "test_uninstall_refuses_while_a_pam_runs_and_removes_nothing",
            "test_a_pam_that_leaves_within_the_grace_does_not_block_setup",
            "test_the_updater_named_by_UPDATERPID_is_waited_for_past_the_grace",
            "test_pam_updates_own_argv_makes_setup_wait_for_that_pam_and_no_other",
            "test_a_stale_run_file_is_removed_and_does_not_block",
            "test_setup_holds_setup_lock_from_its_start_and_a_pam_started_then_refuses",
            "test_the_uninstaller_holds_setup_lock_in_the_process_that_deletes",
            "test_a_second_installer_is_refused_not_queued",
            "test_the_ledger_decides_whether_this_payload_may_replace_pam",
            "test_the_ledger_at_the_payloads_own_level_is_no_constraint",
            "test_a_missing_ledger_is_no_constraint_and_an_unreadable_one_refuses",
            "test_the_ledger_also_guards_an_upgrade_and_leaves_the_previous_pam_whole",
            "test_the_installer_reads_no_PAM_variable",
            "test_the_component_is_installed_kept_on_upgrade_and_removed_when_deselected",
            "test_the_component_selector_shows_the_label_pam_reads",
            "test_an_upgrade_leaves_no_file_of_a_package_the_new_payload_dropped",
            "test_the_cache_keeps_the_current_installer_and_the_previous_one_only",
            "test_an_in_use_file_aborts_the_upgrade_and_Repair_restores_the_previous_pam",
            "test_a_copy_that_fails_rolls_back_and_Repair_restores",
            "test_Repair_puts_back_a_quarantined_file",
            "test_cancelling_the_wizard_before_install_leaves_the_previous_pam_untouched",
            "test_cancelling_during_the_copy_aborts_and_Repair_restores",
            "test_the_PATH_task_appends_app_last_and_uninstall_removes_only_that_entry",
            "test_a_run_file_setup_cannot_probe_refuses_and_never_reads_as_absent",
            "test_deselecting_the_PATH_task_on_a_rerun_takes_the_entry_back",
            "test_an_app_folder_already_on_the_users_PATH_is_the_users_and_stays",
            "test_Repair_keeps_the_previous_versions_cached_installer",
            "test_a_folder_too_long_for_pams_files_is_refused_on_the_folder_page",
            "test_an_upgrade_into_a_folder_too_long_is_refused_before_the_copy",
            "test_the_iscc_these_tests_compile_with_reports_the_pinned_version",
        }
    ),
    # M15 Task 13B Stage 1 review: the installer tests' own instruments (the real-environment guard
    # and the real-root refusal), each read independently of `harness`.
    "test_installer_harness.py": frozenset(
        {
            "test_real_environment_sees_every_name_under_the_install_root_and_the_program_folders",
            "test_real_environment_reads_the_uninstall_list_and_the_PATH_value_and_kind",
            "test_the_behaviour_modules_fixture_fails_when_the_real_environment_changed",
            "test_the_behaviour_modules_fixture_passes_when_nothing_changed",
            "test_a_test_world_inside_the_real_install_root_is_refused",
        }
    ),
    # M15 Task 13B (OD-1, ruling 2/9): the build's pinned interpreter, lock split, launcher, scrub.
    "test_installer_build.py": frozenset(
        {
            "test_stage_ships_the_licence_and_the_third_party_notices_in_the_payload",  # Task 14
            # Task 15W: the licence texts the build adds, pinned from upstream, and their landing
            "test_the_committed_licence_texts_are_the_upstream_files_unchanged",
            "test_stage_ships_every_licence_text_into_the_payload",
            "test_the_target_is_read_from_the_lock_header",
            "test_the_lock_splits_into_its_two_sections_unchanged",
            "test_the_embeddable_is_pinned_by_hash",
            "test_a_wrong_or_unpinned_embeddable_is_refused",
            "test_the_launcher_runs_pam_entry_never_pam_cli",
            "test_the_pth_has_no_import_site",
            "test_scrub_removes_every_onnx_and_bin",
            "test_the_production_defines_come_from_pam_and_carry_no_test_key",
            "test_a_test_define_must_be_a_PamTest_one",
            "test_the_wheel_is_built_from_the_committed_tree_only_of_what_it_needs",
            "test_stage_scrubs_the_component_as_well_as_the_base",
            "test_a_production_compile_passes_no_PamTest_define",
            "test_scrub_refuses_when_a_model_survives_the_removal",
            "test_the_ci_installer_job_requires_inno_and_runs_the_behaviour_module",
            "test_the_longest_path_counts_the_component_below_site_packages_and_a_folders_temp_file",
            "test_the_build_is_pinned_to_inno_setup_6_7_4",
            "test_the_build_refuses_an_iscc_that_does_not_report_6_7_4",
            "test_the_build_accepts_an_iscc_that_reports_6_7_4",
            "test_an_iscc_that_cannot_run_is_refused",
        }
    ),
    # M15 Task 13B (ruling 7): the installer's source guards that never skip - uninstall's reach,
    # privileges, Restart Manager, pam update's flags, the order, test-only hooks, the component.
    "test_installer_source.py": frozenset(
        {
            "test_the_production_view_is_not_vacuous",
            "test_every_UninstallDelete_entry_is_one_exact_file_inside_app",
            "test_the_code_never_deletes_a_folder_and_deletes_files_only_in_the_installer_cache",
            "test_install_replaces_only_the_interpreter_and_exiftool_trees",
            "test_the_installer_is_per_user",
            "test_restart_manager_is_off_both_ways_and_no_mutex_stands_in_for_E11",
            "test_pam_updates_flags_are_the_ones_the_iss_agrees_with",
            "test_the_updater_waits_30_s_and_any_other_pam_gets_the_2_s_grace",
            "test_setup_takes_setup_lock_before_it_looks_for_a_running_pam",
            "test_the_run_file_grammar_matches_pams",
            "test_the_installer_reads_no_environment_variable",
            "test_the_test_hooks_are_reachable_only_from_a_test_build",
            "test_a_test_build_cannot_redirect_the_production_root_or_appid",
            "test_the_component_is_labelled_from_pam_and_kept_on_upgrade",
            "test_the_record_written_is_the_one_pam_reads",
            "test_the_structural_reader_is_not_vacuous",
            "test_M4601_the_exclusion_takes_setup_lock_FIRST_and_both_entries_use_it",
            "test_M4608_the_UPDATERPID_value_is_read_kept_and_used_twice",
            "test_M4619_the_updater_is_waited_for_only_while_its_run_file_is_held",
            "test_M4620_held_pids_never_counts_the_excluded_pid_and_the_updater_is_the_excluded_one",
            "test_F_P1_the_previous_PATH_entry_is_read_before_the_install_step_and_used_after",
            "test_F_P3_an_app_folder_already_on_the_PATH_is_not_recorded_as_the_installers",
            "test_F_P2_a_repair_keeps_the_previous_installer_installers_json_lists",
            "test_every_message_after_the_copy_started_names_the_Repair_pam_shortcut",
            "test_the_folder_is_checked_on_the_folder_page_and_again_before_the_copy",
            "test_the_payload_reaches_app_and_the_component_its_site_packages",
            "test_the_shortcuts_start_pam_check_for_updates_and_repair",
            "test_the_path_task_is_off_unless_chosen",
            "test_a_REQUIRED_inno_stack_that_is_unavailable_fails_instead_of_skipping",
            "test_the_control_WITHOUT_the_inno_requirement_the_same_collection_succeeds",
            "test_the_inno_marker_is_registered",
        }
    ),
    # M15 Task 11 (E13): install provenance - the `{app}` path rule, the record's well-formedness
    # (every malformed shape reads as pip), and `pam doctor`'s advice following the method with
    # its marks and exit code unmoved. Every test in the module; it keeps no registry.
    "test_install_method.py": frozenset(
        {
            "test_a_venv_named_python_with_a_stray_record_is_not_an_installer",
            "test_a_system_python_in_a_folder_named_python_is_not_an_installer",
            "test_the_embeddable_layout_with_its_pth_and_no_venv_marker_is_an_installer",
            "test_app_is_the_parent_of_the_interpreter_folder",
            "test_a_prefix_that_is_not_the_installed_layout_has_no_app",
            "test_a_stray_record_beside_a_non_installed_prefix_is_not_believed",
            "test_the_rule_reads_sys_prefix_when_no_prefix_is_given",
            "test_a_well_formed_installer_record_reads_as_the_installer",
            "test_a_newer_installers_extra_key_does_not_demote_it",
            "test_no_record_reads_as_pip",
            "test_record_state_reads_the_installer_with_its_app",
            "test_record_state_reads_no_record_when_the_layout_has_none",
            "test_record_state_reads_no_record_outside_the_installed_layout",
            "test_record_state_reads_every_malformed_record_as_malformed",
            "test_record_state_reads_an_unreadable_record_as_malformed",
            "test_provenance_still_folds_both_non_installer_states_into_pip",
            "test_a_malformed_record_reads_as_pip_and_never_raises",
            "test_the_oversize_row_is_a_good_record_but_for_its_size",
            "test_a_record_of_exactly_the_cap_is_read",
            "test_an_oversize_record_is_never_read_whole",
            "test_an_unreadable_record_reads_as_pip",
            "test_an_installed_pam_is_told_to_run_its_installer_for_every_repair",
            "test_an_installed_pams_missing_geocoder_dataset_is_a_repair",
            "test_a_pip_install_keeps_the_pip_advice",
            "test_a_malformed_record_gives_pip_advice_through_the_command",
            "test_an_unreadable_record_gives_pip_advice_through_the_command",
            "test_the_provenance_never_moves_the_exit_code_or_a_mark",
        }
    ),
    # M15 Task 13A (E11, spike 10C as admitted): every pam registers its run and refuses while setup
    # runs - the path rule, the hold (A1), the order (Dekker), the probe (A2), fail closed, A4, off
    # Windows, and the isolation of the REAL run registry. Every test in the module; it keeps no
    # registry.
    "test_run_registry.py": frozenset(
        {
            "test_the_root_is_LOCALAPPDATA_pam_install_whatever_PAM_HOME_says",
            "test_without_LOCALAPPDATA_the_root_is_dot_pam_in_the_home_folder",
            "test_the_override_names_the_root_and_an_empty_one_is_ignored",
            "test_main_holds_its_run_file_for_the_whole_command_and_it_goes_after",
            "test_the_run_file_is_held_exclusively_never_sharing_delete",
            "test_main_registers_BEFORE_it_checks_for_setup",
            "test_a_held_setup_lock_refuses_with_the_sentence_exit_1_runs_nothing_and_releases",
            "test_a_stale_setup_lock_is_removed_and_the_command_runs",
            "test_a_run_file_that_cannot_be_created_refuses_exit_1_and_runs_nothing",
            "test_registrations_in_one_process_share_one_file_nested_and_threaded",
            "test_a_real_pams_registration_is_seen_by_another_process",
            "test_ordinary_pams_run_side_by_side_against_a_stale_setup_lock",
            "test_a_killed_pam_leaves_nothing_behind",
            "test_the_race_a_registered_pam_is_seen_by_setup_then_refuses",
            "test_the_race_setup_first_then_a_pam_refuses",
            "test_a_pip_installed_pam_takes_part_too",
            "test_another_pam_is_probed_by_an_open_never_by_os_remove",
            "test_stale_run_files_are_removed_on_sight_and_foreign_names_are_kept",
            "test_no_registry_folder_means_no_other_pam",
            "test_a_run_file_that_cannot_be_probed_counts_as_running_never_as_none",
            "test_a_registry_that_cannot_be_listed_refuses",
            "test_a_real_pam_that_cannot_create_its_run_file_refuses",
            "test_a_setup_lock_that_cannot_be_probed_refuses_pam",
            "test_await_setup_returns_true_once_setup_holds_its_lock",
            "test_await_setup_gives_up_when_the_launched_setup_has_exited",
            "test_await_setup_gives_up_at_its_deadline",
            "test_off_windows_nothing_registers_and_no_other_pam_is_reported",
            "test_the_guard_watches_the_spec_s_default_run_folders",
            "test_a_module_scoped_fixture_is_isolated_too",
            "test_the_guard_sees_a_created_folder_and_a_registration_and_nothing_else",
            "test_an_escape_by_monkeypatch_or_subprocess_fails_the_run",
            "test_an_escape_in_a_module_scoped_fixture_fails_the_session",
            # the Task 13A review closures (M4229, M4241-M4243, M4250, M4253, M4254)
            "test_an_error_that_is_not_access_denied_refuses_at_once_never_after_a_retry",
            "test_a_probers_handle_on_this_pids_file_is_waited_out_not_refused",
            "test_a_delete_pending_file_of_this_pid_is_waited_out_not_refused",
            "test_await_setup_never_takes_a_probe_that_cannot_tell_for_a_sighting",
            "test_off_windows_no_windows_call_is_made_and_nothing_waits",
            # M15 Task 13A-F1: the console entry registers before `pam.cli` is imported
            "test_the_entry_module_imports_neither_click_nor_pam_cli",
            "test_the_entry_registers_and_checks_for_setup_before_pam_cli_or_click_is_imported",
            "test_the_entry_refuses_while_setup_runs_without_ever_importing_pam_cli",
            "test_the_entry_runs_the_command_under_one_run_file_and_it_goes_after",
            "test_the_entry_refuses_when_its_run_file_cannot_be_created_and_runs_nothing",
            "test_the_entry_refuses_a_setup_lock_it_cannot_probe_and_releases",
            # the Task 13A-F1 review closures (M4404, M4405, M4409)
            "test_the_entry_refuses_an_unregistrable_pam_without_ever_importing_pam_cli",
            "test_the_entry_refuses_a_setup_lock_it_cannot_probe_without_ever_importing_pam_cli",
            "test_the_entry_returns_the_exit_code_of_the_command_it_ran",
            "test_a_damaged_installed_pam_names_repair_and_releases_its_run_file",
            "test_a_damaged_pip_pam_is_told_to_reinstall",
        }
    ),
    # M15 Task 13A-F1: the wheel's console script is the light entry (`pam.entry:main`), in
    # `pyproject.toml` and in the installed metadata - a script pointed back at `pam.cli:main`
    # passes every in-process test and only reopens E11's import window. Only these two of the
    # module's tests are listed (its data-file controls predate the census and are its own).
    "test_packaging.py": frozenset(
        {
            "test_the_console_script_is_the_light_entry_not_pam_cli",
            "test_the_installed_console_script_is_the_light_entry_too",
        }
    ),
    # M15 Task 12 (E3, E10, OD-2): the release client over a real TLS loopback server - the host
    # allowlist before resolution, one redirect to the asset host, body caps, truncation, one
    # overall deadline, verification always on. Every test in the module; it keeps no registry.
    "test_update_fetch.py": frozenset(
        {
            "test_the_fixture_certificates_last_at_least_ten_years",
            "test_a_verified_request_reads_the_body_and_sends_only_host_user_agent_and_accept",
            "test_production_verifies_with_the_systems_trust_store_and_the_systems_resolver",
            "test_an_untrusted_certificate_is_refused_and_never_retried",
            "test_the_allowlist_admits_exactly_e3s_hosts_on_443",
            "test_the_allowlist_refuses_from_the_text_alone",
            "test_a_refused_first_url_is_never_looked_up",
            "test_a_download_follows_exactly_one_redirect_to_the_asset_host",
            "test_a_redirect_to_http_is_refused_and_not_followed",
            "test_a_redirect_off_the_allowlist_is_refused_before_getaddrinfo_is_called",
            "test_a_redirect_to_another_allowed_host_than_the_one_expected_is_refused",
            "test_discovery_follows_no_redirect",
            "test_a_second_redirect_is_refused",
            "test_an_oversize_body_is_refused_before_it_is_read",
            "test_an_oversize_body_with_no_length_is_refused_as_it_streams",
            "test_a_body_of_exactly_the_cap_is_read",
            "test_a_truncated_body_is_refused",
            "test_a_chunked_body_is_read_whole",
            "test_a_sink_receives_the_body_in_order",
            "test_a_rate_limited_answer_is_its_own_outcome_with_the_reset",
            "test_any_other_status_is_refused_with_it",
            "test_a_stalled_read_meets_the_overall_deadline",
            "test_slow_progress_under_every_socket_timeout_still_meets_the_deadline",
            "test_a_stalled_tls_handshake_meets_the_deadline",
            "test_a_lookup_that_never_returns_meets_the_deadline",
            "test_the_deadline_is_one_budget_across_the_redirect",
            "test_a_refused_connection_is_a_sentence_not_a_traceback",
            "test_a_failed_lookup_is_a_sentence_naming_the_host",
            "test_remaining_zero_on_any_other_status_is_not_the_rate_limit",
            "test_a_content_length_that_is_not_a_number_is_refused_in_a_sentence",
            "test_the_tcp_connect_is_armed_with_what_is_left_of_the_deadline",
            "test_a_lookup_that_never_returns_does_not_keep_pam_running",
            "test_a_certificate_issued_for_another_host_name_is_refused_and_never_retried",
        }
    ),
    # M15 Task 12 (E2, E4, E5, OD-8, OD-11): `pam update` end to end with spies on every dangerous
    # call - the provenance route, the exclusion, discovery, the downgrade refusal, the confirmation,
    # the verified download and its deletion, the launch. Every test in the module; no registry.
    "test_update_command.py": frozenset(
        {
            "test_a_pam_the_installer_did_not_put_here_is_told_the_pip_route_and_nothing_else",
            # Task 15W: the pip route is the release's wheel asset (the maintainer's decision 1)
            "test_the_wheel_asset_name_is_the_one_pip_wheel_writes",
            "test_the_documents_show_the_route_pam_prints_and_never_the_git_url",
            "test_a_release_carrying_the_wheel_and_the_source_archives_is_still_offered",
            "test_a_malformed_install_record_is_refused_with_no_guessed_command_and_no_network",
            "test_another_pam_running_is_refused_before_any_request",
            "test_a_pam_started_while_the_prompt_waits_is_refused_before_the_download",
            "test_a_registry_pam_cannot_read_refuses_too",
            "test_the_default_exclusion_reads_the_real_run_registry",
            "test_check_names_both_versions_and_downloads_nothing",
            "test_an_offer_with_no_new_migration_says_so",
            "test_the_same_version_is_up_to_date_and_nothing_follows",
            "test_an_older_release_is_up_to_date_too",
            "test_a_prerelease_moves_onto_its_stable_release",
            "test_a_skipped_release_still_names_every_migration_this_pam_lacks",
            "test_the_production_default_names_the_migrations_this_pam_really_ships",
            "test_the_rc_channel_offers_the_highest_admitted_release_of_either_kind",
            "test_the_rc_channel_moves_onto_a_stable_release_above_its_candidates",
            "test_the_stable_channel_refuses_a_latest_that_is_a_prerelease",
            "test_a_release_below_the_compatibility_ledger_is_refused",
            "test_github_refusals_are_named_and_change_nothing",
            "test_a_rate_limit_names_the_reset_time",
            "test_a_release_missing_an_asset_is_refused",
            "test_an_asset_url_other_than_the_one_pam_builds_is_refused",
            "test_a_manifest_size_that_is_not_the_assets_is_refused",
            "test_a_cancelled_prompt_downloads_and_launches_nothing",
            "test_a_confirmed_update_downloads_checks_and_launches_from_the_staging_folder",
            "test_a_hash_mismatch_deletes_the_staging_folder_and_launches_nothing",
            "test_a_release_republished_between_the_manifest_and_the_installer_is_refused",
            "test_a_short_download_deletes_the_staging_folder",
            "test_an_interrupted_download_deletes_the_staging_folder_and_still_interrupts",
            "test_a_launch_that_fails_deletes_the_download",
            "test_the_staging_folder_is_a_fresh_mkdtemp_named_pam_update",
            "test_the_installer_is_launched_detached_with_restart_manager_off_and_no_shell",
            "test_only_the_word_update_confirms",
            "test_a_closed_stdin_cancels",
            "test_pam_update_from_the_command_line_takes_the_pip_route_in_a_venv",
            "test_the_channel_option_admits_only_stable_and_rc",
            "test_an_asset_that_has_not_finished_uploading_is_refused",
            "test_a_release_with_two_files_of_one_name_is_refused",
            # Task 15W review (M5306): look-alike asset names are never bound
            "test_a_release_carrying_lookalike_names_binds_only_the_exact_two",
            "test_an_asset_url_that_only_resembles_the_one_pam_builds_is_refused",
            "test_an_installer_asset_with_no_usable_size_is_refused",
            "test_a_download_shorter_than_the_manifest_with_an_honest_length_is_refused_by_size",
            "test_an_unreadable_compatibility_ledger_refuses_before_any_download",
            "test_the_production_default_reads_the_real_compatibility_ledger",
            "test_a_release_refusal_ends_with_nothing_was_changed_and_downloads_nothing",
            "test_a_manifest_refusal_ends_with_nothing_was_changed_and_downloads_nothing",
            "test_a_draft_release_is_never_offered_on_the_stable_channel",
            "test_a_draft_release_is_never_offered_on_the_rc_channel",
            "test_an_rc_channel_entry_whose_prerelease_flag_disagrees_with_its_version_is_not_admitted",
            "test_the_installer_is_handed_none_of_pams_own_handles",
            "test_the_staging_folder_is_made_in_the_temp_folder_never_in_the_current_one",
            "test_the_updater_waits_ten_seconds_for_setup_as_10c_measured",
            "test_a_registered_pam_update_is_not_another_pam_to_itself",
            # M15 Task 12-G (F3): the reap's wiring - once, after the exclusion, its line, its default.
            "test_leftovers_are_reaped_once_after_the_exclusion_and_before_any_request",
            "test_nothing_is_reaped_while_another_pam_runs",
            "test_nothing_is_reaped_on_the_pip_route",
            "test_the_count_is_said_first_and_only_when_above_zero",
            "test_the_production_reap_is_the_leftovers_module",
            "test_the_production_reap_removes_a_real_leftover_in_temp",
        }
    ),
    # M15 Task 12-G (F3): the reap of earlier updates' installer downloads - every rule spelled from
    # the maintainer's list, the deletion calls watched. Every test in the module.
    "test_update_leftovers.py": frozenset(
        {
            "test_the_sentence_is_the_owners",
            "test_a_qualifying_folder_goes_its_file_first",
            "test_every_qualifying_folder_goes_and_a_release_candidate_counts",
            "test_a_folder_of_any_other_shape_is_left_alone_and_nothing_is_deleted",
            "test_a_subfolder_beside_the_installer_keeps_the_folder",
            "test_a_subfolder_alone_named_like_the_installer_is_not_a_file",
            "test_a_file_in_temp_named_like_a_staging_folder_is_not_a_folder",
            "test_only_folders_directly_in_temp_are_looked_at",
            "test_a_junction_named_like_a_staging_folder_is_never_followed",
            "test_a_symbolic_link_folder_is_never_followed",
            "test_a_symbolic_link_installer_is_never_removed",
            "test_nothing_is_looked_at_while_setup_runs",
            "test_nothing_is_removed_when_pam_cannot_tell_whether_setup_runs",
            "test_a_removal_that_fails_is_skipped_silently_and_the_others_still_go",
            "test_an_installer_still_open_is_left_and_not_counted",
            "test_the_defaults_are_the_temp_folder_and_the_real_setup_lock",
            "test_a_link_by_either_sign_is_not_plain_and_a_plain_entry_is",
        }
    ),
    # M15 Task 12 (E4): only `pam update`'s own function imports the release client - the AST walk,
    # the wiring, a fresh interpreter. Every test in the module; it keeps no registry.
    "test_update_isolation.py": frozenset(
        {
            "test_the_allowlist_is_the_one_e4_names",
            "test_only_the_update_command_imports_the_release_client",
            "test_the_walk_reaches_cli_py_and_the_whole_package",
            "test_the_guard_sees_every_route_in",
            "test_the_guard_does_not_report_a_name_that_only_looks_alike",
            "test_the_update_command_is_the_function_that_imports_the_client",
            "test_importing_the_cli_loads_no_release_client",
            "test_the_fresh_interpreter_probe_sees_a_loaded_client",
        }
    ),
    # M15 Task 14: the split's private halves (tests/private/ is never exported)
    "private/test_ci_lanes_private.py": frozenset(
        {
            "test_the_pre_commit_hook_runs_the_whole_suite_unnarrowed",
            # M15 Task 15A review: unregistered since `v0.1-m14` (the implementer's concern 6)
            "test_the_pre_commit_config_is_a_document_too",
            "test_no_control_in_this_file_can_be_deleted_without_saying_so",
        }
    ),
    "private/test_stale_grouping_private.py": frozenset(
        {"test_the_design_spec_carries_the_sentence_verbatim"}
    ),
    "private/test_cluster_private.py": frozenset(
        {"test_the_ceilings_ladder_is_the_one_the_m11_record_holds"}
    ),
    "private/test_sanity_private.py": frozenset({"test_the_mutation_records_read_as_text"}),
    # M15 Task 14: the export (gate 1, the brief's named refusals) and its census
    "test_export_public.py": frozenset(
        {
            "test_the_allowlist_is_exactly_the_one_the_plan_names",
            "test_a_public_path_is_selected",
            "test_a_private_path_is_not_selected",
            "test_an_export_writes_exactly_the_allowlisted_tracked_files_and_their_manifest",
            "test_a_reused_destination_holding_stale_files_is_refused_and_nothing_is_written",
            "test_an_untracked_pyc_and_untracked_files_are_not_exported_and_edits_are_not_either",
            "test_a_tracked_pycache_or_pyc_is_refused",
            "test_an_escaping_symlink_is_refused",
            "test_an_unlisted_binary_fixture_is_refused",
            "test_a_file_changed_after_the_manifest_was_generated_fails_gate_1",
            "test_a_private_file_added_after_generation_fails_gate_1",
            "test_a_missing_file_fails_gate_1",
            "test_a_tree_whose_own_manifest_is_not_the_approved_one_fails_gate_1",
            "test_a_tree_without_its_own_manifest_fails_gate_1_and_does_not_crash",
            "test_check_checkout_verifies_a_public_checkout_against_its_own_manifest",
            "test_check_checkout_refuses_a_public_checkout_without_a_manifest",
            "test_this_repositorys_head_exports_no_private_path_and_every_binary_is_listed",
            "test_the_path_lists_are_not_empty",
            "test_public_overlay_files_are_written_at_the_root",
            "test_a_listed_binary_passes_and_a_mention_outside_a_list_item_does_not_list_it",
            "test_an_overlay_path_colliding_with_an_allowlisted_one_is_refused",
            "test_an_unknown_commit_is_refused",
            "test_the_exported_tree_verifies_against_its_approved_manifest",
            "test_a_special_folder_fails_gate_1",
            "test_a_symlinked_file_fails_gate_1_even_when_its_bytes_match",
            "test_a_git_folder_at_the_root_is_not_part_of_the_tree",
            "test_the_cli_exits_1_on_a_changed_tree_and_names_the_file",
            "test_check_checkout_says_so_in_the_private_repository",
            "test_main_refuses_without_a_commit_and_a_staging_folder",
            "test_main_exports_and_prints_the_manifests_sha256",
            "test_os_walk_finds_no_symlinked_files_in_a_plain_export",
        }
    ),
    # M15 Task 14: the privacy scan (the brief's planted strings, the denylist's format)
    "test_privacy_scan.py": frozenset(
        {
            "test_the_pattern_set_is_the_one_the_plan_names",
            "test_a_pinned_verbatim_licence_is_exempt_from_the_denylist",
            "test_an_edited_licence_is_scanned_like_any_file",
            "test_the_same_bytes_at_another_path_are_not_exempt",
            "test_a_pinned_licence_still_gets_the_patterns_and_its_path_checked",
            "test_the_exemption_lists_exactly_the_committed_licence_texts_at_their_bytes",
            "test_a_planted_anonymised_archive_observation_is_flagged",
            "test_a_planted_noreply_address_passes",
            "test_each_pattern_flags_its_planted_shape",
            "test_a_denylist_hit_names_its_entry_number_and_never_echoes_the_text",
            "test_each_shape_of_entry_matches_as_a_whole_word",
            "test_no_denylist_is_refused_with_a_sentence_naming_the_path",
            "test_the_test_only_tls_keys_are_allowed_by_exact_folder_and_suffix",
            "test_the_scan_tool_does_not_flag_its_own_source",
            "test_every_file_the_public_tree_carries_is_clean_on_the_patterns",
            "test_the_figure_without_the_word_real_is_flagged_too",
            "test_the_enrichers_own_summary_copy_is_not_an_observation",
            "test_a_planted_real_looking_address_is_flagged",
            "test_an_example_domain_and_a_dll_version_pass",
            "test_the_stand_ins_and_the_public_names_pass",
            "test_the_planted_lists_are_not_empty",
            "test_a_denylist_entry_matches_a_whole_word_only",
            "test_an_underscore_or_a_digit_beside_an_entry_is_an_edge_not_a_letter",
            "test_a_name_in_a_file_name_is_flagged_as_line_0_without_a_manifest",
            "test_the_owners_format_loads_five_entries_skipping_comments_blanks_and_a_bom",
            "test_the_default_denylist_is_in_the_user_profile",
            "test_an_empty_denylist_is_refused",
            "test_a_binary_file_is_scanned_too_and_a_root_git_folder_is_not",
            "test_the_cli_exits_1_on_hits_and_0_when_clean",
            "test_this_module_does_not_flag_itself",
        }
    ),
    # M15 Task 14: the release's refusals and the public workflows, read with PyYAML
    "test_release_pins.py": frozenset(
        {
            "test_a_placeholder_sha256_is_refused_by_name",
            "test_a_sha256_that_is_not_64_lowercase_hex_is_refused",
            "test_a_tag_that_is_not_this_version_is_refused",
            "test_the_cli_accepts_the_committed_workflows_own_pins",
            "test_complete_pins_pass",
            "test_a_url_that_is_not_https_is_refused",
            "test_a_missing_pin_is_refused",
            "test_the_channel_follows_the_version_grammar",
            "test_split_lock_takes_out_the_sdist_only_package_and_keeps_the_rest",
            "test_split_lock_refuses_a_lock_without_the_package",
            "test_split_lock_on_the_shipped_lock_keeps_every_other_package",
        }
    ),
    "test_public_workflows.py": frozenset(
        {
            "test_the_exiftool_pins_are_exactly_the_measured_ones",
            "test_the_inno_pins_are_exactly_the_measured_ones_in_both_workflows",
            "test_gate_2_recomputes_the_checkout_before_anything_else_runs",
            "test_the_build_refuses_unpinned_inputs_before_it_downloads_anything",
            "test_exiftool_is_checked_by_hash_extracted_and_renamed_before_the_build",
            "test_the_release_writes_the_update_manifest_and_creates_a_draft_only",
            "test_ci_checks_the_checkout_against_its_manifest_first",
            "test_ci_runs_the_installer_job_with_inno_required_and_pinned",
            "test_every_versioned_notice_matches_its_pin_in_the_lock_or_the_release",
            "test_the_release_runs_on_a_v_tag_and_nothing_else",
            "test_the_release_jobs_are_the_four_the_plan_names_in_order",
            "test_the_pins_are_set_in_one_place_and_never_inline",
            "test_the_release_suite_is_the_full_suite_unnarrowed",
            "test_the_installer_job_installs_inno_by_hash_then_runs_the_13b_job_body",
            "test_the_wheels_come_from_the_lock_by_hash_and_the_build_from_the_lock",
            "test_only_the_build_job_may_write_and_the_default_is_read",
            "test_every_pinned_download_is_hashed_and_refused_on_a_mismatch",
            "test_the_build_compiles_with_the_iscc_its_pinned_step_installed",
            "test_neither_workflow_names_the_private_repository",
            # Task 15W review (M5308): PINNED_DOWNLOADS is sized by the census, outside the module
            "test_the_pinned_downloads_are_at_least_as_many_as_the_census_says",
            # Task 15W: the wheel and the GPL/LGPL source as release assets; no [OPEN] notice
            "test_the_source_archive_pins_are_exactly_the_measured_ones",
            "test_the_release_uploads_the_wheel_and_every_source_archive_it_downloaded",
            "test_the_build_refuses_a_wheel_not_named_as_pam_update_prints_it",
            "test_the_notices_hold_no_open_item",
            "test_the_notices_name_every_source_archive_the_release_uploads",
        }
    ),
    "test_public_coverage.py": frozenset(
        {
            "test_the_coverage_document_keeps_its_headings_and_its_floor",
            "test_every_node_id_the_coverage_document_names_is_collected",
            "test_the_public_census_names_only_modules_the_export_carries_and_none_from_private",
            "test_a_private_module_left_in_the_public_census_fails_the_public_check",
            "test_the_census_the_hook_checks_is_the_public_view_exactly_where_tests_private_is_absent",
            "test_every_module_the_coverage_document_names_is_public",
            "test_the_public_census_is_non_empty_and_meets_its_own_floors",
            "test_every_module_under_tests_private_is_keyed_as_private_in_the_census",
            "test_the_public_census_is_exactly_the_census_minus_its_private_keys",
            "test_the_public_census_passes_the_census_check_on_this_tree",
        }
    ),
}

# module file -> the names of the hand-written registries it defines at module level: a dict,
# set, tuple or frozenset(...) LITERAL of test-name strings (all four forms occur in tests/ - pass
# 2, A5, measured). Every census name for that module must be a key or member of ONE of them.
# A module absent from this map keeps no registry (e.g. the M11 hunt) - and `census_errors`
# CHECKS that claim: see `unmapped_registries`.
REGISTRIES: dict[str, tuple[str, ...]] = {
    "private/test_bench_rows.py": ("LOAD_BEARING_CONTROLS",),
    "test_ci_lanes.py": ("LOAD_BEARING_CONTROLS",),
    "private/test_ci_lanes_private.py": ("LOAD_BEARING_CONTROLS",),
    "test_cluster.py": ("LOAD_BEARING_CONTROLS",),
    "test_config.py": ("LOAD_BEARING_CONTROLS",),
    "test_doctor.py": ("LOAD_BEARING_CONTROLS",),
    "test_enrich_runner.py": ("LOAD_BEARING_CONTROLS",),
    "test_events_grouping.py": ("LOAD_BEARING_CONTROLS",),
    "test_f12_unit_membership.py": ("LOAD_BEARING_CONTROLS",),
    "test_faces_cluster.py": ("LOAD_BEARING_CONTROLS",),
    "test_faces_enricher.py": ("LOAD_BEARING_CONTROLS",),
    "test_faces_model.py": ("LOAD_BEARING_CONTROLS",),
    "test_faces_page.py": ("LOAD_BEARING_CONTROLS",),
    "test_faces_repo.py": ("LOAD_BEARING_CONTROLS",),
    "test_faces_sink.py": ("LOAD_BEARING_CONTROLS",),
    "private/test_ht_m10_doc.py": ("LOAD_BEARING_CONTROLS",),
    "private/test_ht_m11_doc.py": ("LOAD_BEARING_CONTROLS",),
    "private/test_ht_m12_doc.py": ("LOAD_BEARING_CONTROLS",),
    "private/test_ht_m13_doc.py": ("LOAD_BEARING_CONTROLS",),
    "private/test_ht_m14_doc.py": ("LOAD_BEARING_CONTROLS",),
    "test_m9_safety_constructions.py": ("LOAD_BEARING_CONTROLS",),
    "test_net_canary.py": ("LOAD_BEARING_CONTROLS",),
    "test_plan_cli.py": ("LOAD_BEARING_CONTROLS",),
    "test_planner.py": ("LOAD_BEARING_CONTROLS",),
    "test_static_syntax.py": ("LOAD_BEARING_CONTROLS",),
    "test_webui_api.py": ("LOAD_BEARING_CONTROLS",),
    "test_webui_faces.py": ("LOAD_BEARING_CONTROLS",),
    "test_webui_faces_manage.py": ("LOAD_BEARING_CONTROLS",),
    "test_webui_faces_split.py": ("LOAD_BEARING_CONTROLS",),
    "test_webui_static.py": ("FACES_LOAD_BEARING_CONTROLS",),
}

# Module-level names that match *CONTROLS* but are NOT registries of test names. Spelled out,
# so the completeness check below can tell a counter from a registry the census forgot
# (controller ruling R4 - the list is every such name in tests/ at M12 Task 1, not a sample).
NOT_REGISTRIES: frozenset[str] = frozenset(
    {
        # test_webui_api.py: a literal COUNT, the floor under that module's registry size (M1940)
        "REGISTERED_CONTROLS_FLOOR",
        # test_webui_static.py: a COUNT - how many selection controls UX 5 draws
        "UX_5_SELECTION_CONTROLS",
        # test_webui_static.py: a map from DOM BUTTON IDS to the routes they call, not test names
        "FACES_LIVE_CONTROLS",
    }
)
REGISTRIES_FLOOR = (
    30  # len(REGISTRIES) at M15 Task 14 (`private/test_ci_lanes_private.py`); 29 at
)
# M14 Task 9 Step 1 (`test_ht_m14_doc.py`); 28 at M13
# Task 16 Step 5 (27 at M12 Task 14, 26 at Task 1)

# The census's own floors, LITERALS. Raise them in the commit that adds an entry; lower them
# only with the reason in the commit message.
CENSUS_MODULES_FLOOR = (
    129  # len(CENSUS) at M15 Task 15A (`test_m15_safety_constructions.py`); 128 at the M15 Task 14 review (`test_ht_manifest_snapshot.py`, split out); 127 at M15 Task 14 (four private halves of split modules; `test_export_public.py`,
    # `test_privacy_scan.py`, `test_release_pins.py`, `test_public_workflows.py`,
    # `test_public_coverage.py`); 118 at M15 Task 12-G (`test_update_leftovers.py`); 117 at M15 Task 13B Stage 1 review (`test_installer_harness.py`); 116 at M15 Task 13B (`test_installer_behaviour.py`, `test_installer_source.py`, `test_installer_build.py`); 113 at M15 Task 13A-F1 (`test_packaging.py`, the console entry); 112 at M15 Task 12 (`test_update_fetch.py`, `test_update_command.py`,
    # `test_update_isolation.py`, E3/E4); 109 at M15 Task 13A (`test_run_registry.py`, E11); 108 at M15 Task 11 (`test_install_method.py`, E13); 107 at M15 Task 9 (`test_update_version.py`, `test_update_manifest.py`,
    # `test_release_manifest_tool.py`, `test_schema_ledger.py`); 103 at
    # M15 Task 8 (`test_catalog_compat.py`, E1); 102 at
    # M15 Task 7 Stage 1 (`test_recovery_report.py`, M3819); 101 at
    # M15 Task 6 review (`test_collision.py`); 100 at M15 Task 2 Stage 1 (`test_apply_cloud_only_now.py`, M3543); 99 at
    # M14 Task 9 Step 1 (`test_ht_m14_doc.py`, HT-M14's pin); 98 at M14
    # Task 9Y (`test_status.py`, Y1); 97 at M14 Task 8a
    # (`test_m14_safety_constructions.py`); 96 at M14 Task 7
    # (`test_webui_history_page.py`); 95 at its first commit
    # (`test_webui_history.py`); 94 at M14 Task 6 Stage 2
    # (`test_webui_rename_stage2.py`); 93 at M14 Task 6c
    # (`test_webui_confirm_page.py`); 92 at M14 Task 6b
    # (`test_webui_rename.py`); 91 at M14 Task 6a
    # (`test_webui_server.py`); 90 at M14 Task 5h
    # (`test_stale_grouping.py`); 89 at M14 Task 5g
    # (`test_stem_unique_per_folder.py`); 88 at M14 Task 5d
    # (`test_webui_read_job.py`); 87 at M14 Task 5c
    # (`test_webui_jobs.py`); 86 at M14 Task 5b
    # (`test_lock_held_bodies.py`); 85 at M14 Task 4d
    # (`test_webui_welcome_page.py`); 84 at M14 Task 4c
    # (`test_webui_folders.py`); 83 at M14 Task 4a Step 2
    # (`test_webui_launcher.py`); 82 at Step 1
    # (`test_cli_help_golden.py`); 81 at M14 Task 3 slice 3c
    # (`test_webui_selection_page.py`); 80 at slice 3b
    # (`test_webui_shortcut.py`,
    # `test_webui_low_bypass.py`); 78 at slice 3a (`test_webui_selection.py`); 77 at M14 Task 2
    # Step 6a (`test_webui_detail_page.py`); 76 at M14 Task 2
    # Steps 3-4 (`test_webui_list_page.py`); 75 at M14 Task 2
    # (`test_webui_listing.py`); 74 at M14 Task 0E (`test_directory_names.py`); 73 at M14 Task 0C
    # (`test_path_owner_index.py`); 72 at M14 Task 0B
    # (`test_live_placeholder.py`); 71 at M14 Task 0A review
    # (`test_integrity_one_pass.py`); 70 at M13 Task 16
    # Step 5 (`test_ht_m13_doc.py`); 69 at Step 4
    # (`test_ht_m13_tools.py`); 68 at Steps 1-2
    # (`test_m13_safety_constructions.py`); 67 at Task 15
    # (66 at Task 14, one module swapped for another; 66 at 13a,
    # 65 at 13, 63 at 12, 62 at 10; 61 at 9, 60 at Step 0)
)
# Total names at M13 Task 1: +21 for F1's three modules, +1 for M2367's closing test, +2 for
# Stage 2's module; 622 at the M12 close. M13 Task 2: +6 (two equality checks and their two
# controls, two `Accept all` server tests). M13 Task 3: +10 (`test_refresh_refusal.py`).
# M13 Task 4: +5 (`test_snapshot_reads.py`). M13 Task 5: +12 (`test_pam_written_names.py`),
# and +5 at its review (M2440, M2450-M2452's closing tests and the unit construction).
# M13 Task 6: +10 (`test_unit_date_disagreement.py`), and +1 at its review (M2468's closing test).
# M13 Task 7: +5 (`test_ocr_unreachable.py`), and +6 at its review (F5: `test_net.py`'s O-1
# controls and the wire-path test). M13 Task 9: +1 (`test_report_duplicates.py`, Step 0's cut),
# then +6 there (H2's look-alikes) and +6 (`test_phash_groups.py`); +6 at its review (the
# closing tests of M2498, M2501/M2502, M2505/M2506, M2507, M2508 and M2510's order corner).
# M13 Task 10: +5 (`test_webui_lookalikes.py`, H3); +4 at its review (closing tests).
# M13 Task 12: +4 (`test_families_scale.py`, P11); +2 at its review (M2560/M2561's closing tests).
# M13 Task 13: +14 (`test_large_library_builder.py`) and +4 (`test_proctree.py`).
# M13 Task 13a: +7 (`test_repo_variable_limit.py`, L1); +1 at its review (M2571's closing test).
# M13 Tasks 13/13a review: +1 (M2580's closing test, `test_proctree.py`).
# M13 Task 14: -7 (`test_epsilon_sweep.py`, deleted with its tool) and +15
# (`test_faces_calibrate.py`, which carries five of the seven over).
# M13 Task 14 review: +5 (the closing tests of M2599, M2600, M2601, M2603 and M2604,
# `test_faces_calibrate.py`).
# M13 Task 15: +18 (`test_q_measure.py`, Q1/Q2/Q4).
# M13 Task 15 review: +6 (the closing tests of M2613..M2626, `test_q_measure.py`).
# M13 Task 16: +18 (`test_m13_safety_constructions.py`, the chain and the hunt Q1-Q6).
# M13 Task 16 Step 4: +22 (`test_ht_m13_tools.py`, HT-M13's tools).
# M13 Task 16 Step 5: +24 (`test_ht_m13_doc.py`, HT-M13's pin).
# M13 Task 16 review: +2 (the closing tests of M2653 and M2657, `test_ht_m13_tools.py`).
# M14 Task 0A review: +15 (`test_integrity_one_pass.py`, registered late - M2682).
# M14 Task 0B: +8 (`test_live_placeholder.py`, V1 + X1).
# M14 Task 0B review: +2 (M2700's and M2713's closing tests, `test_live_placeholder.py`).
# M14 Task 0C: +5 (`test_path_owner_index.py`, OD-6 (a)).
# M14 Task 0C review: +2 (M2719's and M2721's closing tests, `test_path_owner_index.py`).
# M14 Task 0D: +2 (`test_webui_decide.py`, V3's refusal and its spelled known set).
# M14 Task 1: +8 (`test_static_syntax.py`: the script order by html.parser and its control, the
# load-order harness's two halves, UX 7's contrast check, its floor and its formula's control).
# M14 Task 1 review: +7 (`test_static_syntax.py`: UX 7 rev 16's token values, the phone
# notice and its control, U20's shell scope and its control, U4's touch target, served types).
# M14 Task 0E: +17 (`test_directory_names.py`, X4's one listing per folder per run);
# +1 at its stage-1 review (F3, the case-sensitive residual on every platform).
# M14 Task 2 Step 1a: +2 (`test_webui_api.py`, the items refusal and its spelled known set).
# M14 Task 2 Step 2: +14 (`test_webui_listing.py`, U25's list groups and U15's note code).
# M14 Task 2 Steps 3-4: +6 (`test_webui_list_page.py`, the list page under node).
# M14 Task 2 W9: +3 (`test_webui_listing.py`, `unit_kind` from the catalog's group kind).
# M14 Task 2 Step 5: +1 (`test_webui_list_page.py`, the field guard's run negative control).
# M14 Task 2 Step 6a: +4 (`test_webui_detail_page.py`, the preview and the editor under node)
# and +1 (`test_webui_list_page.py`, Accept all's reload control).
# M14 Task 2 Step 6b part 1: +1 (`test_webui_detail_page.py`, the row's restore run control).
# M14 Task 2 Step 6b final: +2, no control retired. Three registered controls were REWRITTEN to
# the retirement they now pin and renamed, keeping their registration: `test_static_syntax.py`'s
# two phone-notice controls (U4) and `test_app_logic.py`'s bulk-button wiring (U2). Added: the
# phone-notice check's positive control and `test_app_logic.py`'s stdlib pin on the card page's
# retired functions.
# M14 Task 2 review R1: +3 (`test_webui_listing.py`, the routes' own wiring: the note code's
# capture read, the list groups' fields, the empty `list_group`).
# M14 Task 2 review R2: +2 (`test_static_syntax.py`, the review page's <body> class and its
# negative control).
# M14 Task 3 slice 3a: +17 (`test_webui_selection.py`, the selection's resolver and routes).
# M14 Task 3 slice 3b: +20 (`test_webui_shortcut.py` 10, `test_webui_low_bypass.py` 6,
# `test_accept.py` 3, `test_apply_approval.py` 1: the shortcuts, Step 4's guard, OD-1's default).
# M14 Task 3 slice 3c: +8 (`test_webui_selection_page.py`, the selection and its bar under node).
# M14 Task 3 slice 3d: +5 (`test_webui_selection.py` 2, `test_webui_shortcut.py` 2: the date
# tree's `group` in the view; `test_webui_list_page.py` 1: the shortcut's view control).
# M14 Task 3 review R1: +3 (`test_webui_low_bypass.py` 2: the row writer's callers, and its
# control; `test_webui_shortcut.py` 1: a group's N under a view that shows part of it).
# M14 Task 4a Step 1: +3 (`test_cli_help_golden.py`, the `cli.py` cut's golden help transcript).
# M14 Task 4a Step 2: +5 (`test_webui_launcher.py`, the unbound server, U10).
# M14 Task 4b: +10 (`test_webui_launcher.py`, bind, change of folder and a failed bind, U10).
# M14 Task 4c: +22 (`test_webui_folders.py` 17, the chooser and the network refusal, U13;
# `test_webui_launcher.py` 5, `pam start [PATH]`, U9).
# M14 Task 4d: +10 (`test_webui_welcome_page.py`, Welcome under node, W18/U10/U12/U13, OD-4).
# M14 Task 4 review R1: +3 (`test_webui_folders.py` 1: listdrives' network drive, M3002;
# `test_webui_launcher.py` 2: F1 and F2 as strict xfails). M3012/M3013's closures add no test.
# M14 Task 5a: +1 (`test_webui_welcome_page.py`: the controls' named checks are required, R2-F3).
# M14 Task 5b: +7 (`test_lock_held_bodies.py`, a new module: T5.8, T5.9, C5-2, the server's proof).
# M14 Task 5c: +16 (`test_webui_jobs.py`, a new module: T5.1, T5.2, T5.4, T5.5, C5-5).
# M14 Task 5d: +13 (`test_webui_read_job.py`, a new module: T5.6, T5.7, C5-4, U10's chosen
# read, U21, the ETA, cancel, the binding and busy pins, W23's folder).
# M14 Task 5d concern 2, the controller's fix: +2 (`test_webui_read_job.py`: a choice made by
# another page is refused, outside and inside the bind lock).
# M14 Task 5d concern 2, M3070's closure: +1 (`test_webui_read_job.py`: a choice withdrawn by
# another page's failed bind just before the lock is not read).
# M14 Task 5e: +7 (`test_webui_welcome_page.py`: +8 - the ETA phrases, U12's banner copy and six
# run controls for the Reading step; -1 - `..._start_reading_is_active`, retired with the inactive
# step it guarded).
# M14 Task 5 review R1: +9 (`test_webui_jobs.py` 3: a poll alone heals a dead job, cancel after
# a read ended, server_close heals a dead job before the release; `test_webui_read_job.py` 5: the
# read writes into the one log, publishes under the request lock, a changed photo is not "nothing
# new", no exiftool and a bad template are refused; `test_lock_held_bodies.py` 1: R1-F1, a strict
# xfail - a plan after a stopped read must not split a companion unit).
# M14 Task 5 review R1, M3114's closure: +1 (`test_webui_launcher.py`: a read with nothing
# chosen says "Choose a folder first." before its body or exiftool).
# M14 Task 5 review R1, M3118/M3119's closure: +1 (`test_lock_held_bodies.py`: a file freed
# after the walk ticks progress and is a place to stop).
# M14 Task 5 review R2: +1 (`test_webui_welcome_page.py`: the poll scheduled twice, run).
# M14 Task 5, the controller's page-fix pass (M3190): +1 (`test_webui_welcome_page.py`: a late
# reload answer that starts a second poll, run).
# M14 Task 5f (R1-F1, C5-4 amended, Z1): +2 (`test_lock_held_bodies.py`: a read stopped at its
# last file still groups and plans the pair as one - R1-F1 route (b); `test_webui_read_job.py`: a
# stopped read stores the grouping a full scan of a copy stores). The R1-F1 strict xfail now passes
# and stays registered; it adds no name.
# M14 Task 5f Z1 review: +2 (`test_lock_held_bodies.py`: a read stopped in the became-cloud-only
# branch still groups the pair - M3192; a stopped scan logs `scan stopped` last and never `scan
# done` - M3195).
# M14 Task 5g (Z3, finding 5f-F1): +10 (`test_stem_unique_per_folder.py` 7, a new module: 5f-F1
# end to end, the `.acr_` key, own names, a unit's own shared key, the case clash, a reservation's
# key, the seed reading keys; `test_webui_decide.py` 3: rename's and restore's stem refusals, and
# both endpoints reading `scan.ignore_stem_suffixes`).
# M14 Task 5g (Z3) review: +5 (`test_stem_unique_per_folder.py` 2: 5f-F1 one folder down - M3219,
# a missing row holds its key - M3216; `test_webui_decide.py` 3: a stem in another case - M3213,
# a stem held only in another folder - M3214/M3225, a missing row in the UI - M3221).
# M14 Task 5g, the controller's Z3.1 fix (5g-F1, Q1, Q2): +5 (`test_stem_unique_per_folder.py` 4:
# a conforming unit beside a missing row and beside an ungrouped `.LRV` of its stem, the own key
# frees disk files and never a reservation, a key reservation needs its folder;
# `test_webui_decide.py` 1: a proposal frees no key its files do not hold).
# M14 Task 5h (Z2, R1-F1 route (c)): +14 (`test_stale_grouping.py`, a new module: route (c) end to
# end and through the CLI, the removed `.acr_` suffix and a configured one not refused (the
# implementer's own survivor on the suffix operand), the added sidecar extension, `mov` removed
# from `video_exts`, the open plan kept, a stored kind not 5.4's, a member replaced under an
# interrupted scan, the check after recovery, no false refusal after apply/undo, with a
# placeholder member and with a missing member, and the spec's sentence).
# M14 Task 5h (Z2) review: +3 (`test_stale_grouping.py`: a refused plan never reaches the plan
# writer - M3259; scan and plan each use the one shared grouping and nothing else calls
# `group_companions` - M3264/M3265; `decide_groups`' own known-member contract - M3261-M3263).
# M14 Task 5h review, the controller's fix (5h-F5): +1 (`test_webui_jobs.py`: a job refused with a
# `PamError` ends `failed` with that sentence as its line; any other exception with none).
# M14 Task 6a: +10 (`test_lock_held_bodies.py` 5: the apply/undo golden transcripts from
# `v0.1-m13`, the bodies under a held proof, another folder's and a released proof refused with
# no rename call, the shells still lock; `test_webui_server.py` 5, a module new to the census: U19's
# pinned pairs, the per-module rows, the scratch-copy plant, the unused allowances, the move sites).
# M14 Task 6b: +25 (`test_webui_rename.py`, a new module: the plan's Stage 1 T6 names - mutations
# 1-10 and 13 - plus the confirmation's facts, F1's page twin, the refused applies (nothing
# approved, a superseded plan, J13), undo's nonce replay, unknown, already-undone and stale
# sessions, the busy refusal for undo, a GET that never undoes, and Ctrl+C during a rename).
# M14 Task 6, the controller's rulings R6-1 (progress from `apply_locked` / `undo_locked`) and R6-2
# (the job's `result`): +1 (`test_webui_rename.py`).
# M14 Task 6c, ruling B1: +2 (`test_webui_rename.py`: `pam review` serves `pam start`'s server,
# bound, and a page rename runs on it; Ctrl+C during that rename is a crash, Step 8).
# M14 Task 6c: +10 (`test_webui_rename.py` 1: ruling B2, the footer's N is Confirm's `renames` on
# a fixture with an approved no-op member; `test_webui_confirm_page.py` 9, a new module: the
# driver's checks named and executed, the copy verbatim, U6's order, the 28 plants run).
# M14 Task 6 Stage 1 server review: +5 (`test_webui_rename.py`: R1-F1's binding refused on both
# moving routes and spending nothing; U19 pinned by name and its walker's control; the verified
# rename called only in `execute/unit.py`; a moving POST with no Origin served).
# The controller's S1-F1 / S1-F7 fixes: +5 (`test_webui_rename.py`: `pam review`'s closing block
# after a page rename, after a page undo, and unchanged with nothing renamed; the footer's count
# memoised until a POST, and recomputed when a job ends). +2: a superseded plan's closing names
# the new open plan after a page read, or `pam plan` when none is open.
# M14 Task 6 Stage 1 page review: +3 (`test_webui_rename.py`: the closing after a page apply
# that renamed nothing; S1P-F1, no undo for a session that renamed nothing - a strict xfail until
# the controller's fix; the footer's count per plan with no write between). +2 (`test_webui_confirm_page.py`: ruling
# B5's `?` overlay sentence; ruling B6's pills at wide widths only).
# The controller's S1P-F1/S1P-F2 fixes: +1 (`test_webui_rename.py`: no undo for this plan's empty
# session while an earlier plan's session could be undone - M3368, the session operand).
# M14 Task 6 Stage 2: +8 (`test_webui_rename_stage2.py`: steps (a)-(f), (g), (h), B1's closing,
# the reload mid-rename, and S2-F1..S2-F3's three strict xfails).
# The controller's S2-F1..F3 fixes: +2 (`test_webui_welcome_page.py`: S2-F2's two negative
# controls, recovery's line from the ping hidden, and the list opened over it).
# M14 Task 7: +17 (`test_webui_history.py`, a new module: History's rows - T7 2-6 spelled in
# fixture SQL, newest first, the empty archive, unbound, the report flag - the report route and its
# refusals - T7 1's `..`, the malformed ids, another script's digits, an unknown and a reportless
# session - and the undo from History through Done's routes).
# M14 Task 7: +9 (`test_webui_history_page.py`, a new module: the driver's 13 checks named and
# executed, W22's and B4's copy verbatim, the page's parts, Done as the only way in, the 19 plants
# run).
# M14 Task 7 review: +1 (`test_webui_rename_stage2.py`: Stage 2's step (i), X2 - plan A then plan B,
# B undone, A undone through its History row's id, `--exact`, `pam status` naming B's undo).
# M14 Task 7 review: +2 (`test_webui_history.py`: M3383's closure, `_report_path` names nothing for
# an id of another shape and the report inside `reports/` for the written shape, `-N` included).
# +1 (`test_census.py`: an unlisted module that declares a registry is reported - concern 12).
# M14 Task 8a: +15 (`test_m14_safety_constructions.py`, a new module: the chain through the page
# and the console, and the hunt's Q1 (4), Q2 (1), Q3 (4), Q4 (2) and Q9 (3)).
# M14 Task 8b: +11 (`test_m14_safety_constructions.py`: Q5 (5), Q6 (4), Q7 (2: the control and
# the ten requests)).
# M14 Task 8b: +5 (`test_m14_safety_constructions.py`: Q8 (4) and the hunt's own floors (1)); +1
# (`test_net_canary.py`: the page's chain under the socket watch, in LOAD_BEARING_CONTROLS).
# M14 Task 8 review: +4 (`test_m14_safety_constructions.py`: Q7 on the undo route (2: the control
# and the ten requests), the review's own construction (Q5), and the Q8 read spy's open-hook control).
# M14 Task 9Y: +5 (`test_status.py`, newly listed: Y1's approved line - the count, its place, its
# absence, the superseded plan, the page agreeing). Task 9Y review: +2 (`test_status.py`: a skip is not an
# approval, M3463; `test_webui_confirm_page.py`: the lists' floors in another hunk, M3488).
# M14 Task 9 Step 1: +29 (`test_ht_m14_doc.py`, HT-M14's pin).
# M15 Task 1 review (M3523): +2 (`test_status.py`: AM1's table and its `plan_rows` spy).
# M15 Task 2 Stage 1 review: +8 (`test_apply_cloud_only_now.py`, newly listed: M3543 deleted it
# whole with the suite green).
# M15 Task 6 review: +6 (`test_collision.py`, newly listed: the C7 pin and the seed's controls).
# M15 Task 5b: +5 (`test_webui_rename.py` 2, C6.4; `test_apply_cloud_only_now.py` 2, C6.5 and
# C6.8(b); `test_webui_confirm_page.py` 1, `pam-stopped.js`'s copy).
# M15 Task 7 Stage 1 review: +23 (`test_recovery_report.py`, newly listed: M3819 deleted it whole
# with the suite green; five of the twenty-three are the review's own closing tests).
# M15 Task 8: +19 (`test_catalog_compat.py`, E1).
# M15 Task 5b review: +1 (`test_apply_cloud_only_now.py`: History's units past 1,000, M3906).
# M15 Task 7 Stage 2 review: +3 (`test_recovery_report.py`: M3835, M3838, M3839).
# M15 Task 7 Stage 2 rulings: +8 (`test_recovery_report.py` 7, F1 and F2; `test_webui_rename.py`
# 1, F3).
# M15 Task 9: +73 (four modules). M15 Task 8 Stage 1 review: +3 (`test_catalog_compat.py`: F1
# M4012, F2 M4013/M4014).
# M15 Task 7 follow-up review: +3 (`test_recovery_report.py` 2, M3850 and M3852;
# `test_webui_rename.py` 1, M3864).
# +4 at M15 Task 8 S2-F1 (`test_catalog_compat.py`).
# +2 at M15 Task 8 S2-F1 review (`test_catalog_compat.py`: M4177, M4179).
# +17 at M15 Task 11 (`test_install_method.py`, newly listed: E13's provenance and doctor's advice).
# +7 at M15 Task 9 review (`test_schema_ledger.py` 6, `test_update_manifest.py` 1: F1-F4, F6).
# +32 at M15 Task 13A (`test_run_registry.py`, newly listed: E11's protocol, pam's side).
# +79 at M15 Task 12 (the three `test_update_*` modules, newly listed; `test_net_canary.py` 2,
# `test_net.py` 1, `test_install_method.py` 6).
# +1 at M15 Task 11 review (`test_install_method.py`: F1, M4197). +3 at its F3 fix (installed layout needs the embeddable `._pth`).
# +5 at M15 Task 13A review (`test_run_registry.py`: M4229/M4241, M4242, M4243, M4250, M4253/M4254).
# +8 at M15 Task 13A-F1 (`test_run_registry.py` 6, `test_packaging.py` 2, newly listed: the console
# entry registers before `pam.cli` is imported).
# +11 at M15 Task 12 Stage 1 review (`test_update_fetch.py` 4: M4317, M4321, M4322, M4324;
# `test_update_command.py` 7: M4333-M4336, M4338, M4355, M4356).
# +6 at M15 Task 12-F (`test_update_fetch.py` 1: the wrong-host certificate; `test_update_command.py` 5:
# `Nothing was changed.` on a release and a manifest refusal, draft x2, the prerelease-flag mismatch).
# +3 at M15 Task 13A-F1 review (`test_run_registry.py`: M4404/M4405 fresh-interpreter refusals, M4409 exit code).
# +58 at M15 Task 13B (`test_installer_behaviour.py` 27, `test_installer_source.py` 21,
# `test_installer_build.py` 10, newly listed: the installer, its guards and its build).
# +4 at M15 Task 12 Stage 2 review (`test_update_command.py`: M4513/M4501, M4511, M4510, M4509).
# +1 at M15 Task 12 Stage 2 F2 (`test_update_command.py`: the exclusion is checked again after the typed confirmation).
# +10 at M15 Task 13B Stage 1 review (`test_installer_harness.py` 5: M4613-M4615; `test_installer_build.py`
# 4: M4605, M4612, M4634, M4635; `test_installer_behaviour.py` 1: M4622).
# +21 at M15 Task 13B-F (`test_installer_behaviour.py` 6: F-P1, F-P3, F-P2, the path budget twice, the real
# ISCC's version; `test_installer_build.py` 5: the path budget, INNO_VERSION; `test_installer_source.py` 10:
# the structural guards for M4601/M4608/M4619/M4620, F-P1/F-P2/F-P3, Q8's messages, the path budget).
# +23 at M15 Task 12-G (`test_update_command.py` 6: the reap's wiring; `test_update_leftovers.py`
# 17, newly listed: the reap's rules).
# +2 at M15 Task 13B-G (`test_run_registry.py`: a damaged payload names Repair, or reinstall; O-1).
# +47 at M15 Task 14 (the five new public modules 44, `test_installer_build.py` 1, the private halves'
# new names 2: `test_cluster_private.py`, `test_sanity_private.py`; two names moved, none dropped).
# +52 at the M15 Task 14 review (F4, M4983: every test of the five new public modules is listed;
# F5's notices-version test).
# +10 at M15 Task 15A (`test_m15_safety_constructions.py`, newly listed: the M15 hunt).
# +2 at the M15 Task 15A review (`private/test_ci_lanes_private.py`: the config-document test and the
# floor, unregistered since `v0.1-m14`).
# +10 at M15 Task 15W (`test_update_command.py` 3, `test_installer_build.py` 2,
# `test_public_workflows.py` 5: the wheel asset, the source assets, the licence texts, no [OPEN]).
# +5 at M15 Task 15W, the licence ruling (`test_privacy_scan.py`: the hash-pinned licence
# texts are exempt from the denylist only).
# +2 at the M15 Task 15W review (M5306 the look-alike asset names, M5308 the PINNED_DOWNLOADS floor).
CENSUS_CONTROLS_FLOOR = 1953

# THE PUBLIC CENSUS'S FLOORS (M15 Task 14): the census minus its `private/` keys, measured at Task 14.
# The export runs against these; raise them with the full ones.
PUBLIC_CENSUS_MODULES_FLOOR = 115  # 114 at Task 14; +1 at Task 15A (the M15 hunt)
# 1762 at Task 14; +10 at 15A; +10 at 15W; +5 the licence exemption; +2 the 15W review.
PUBLIC_CENSUS_CONTROLS_FLOOR = 1789
PUBLIC_REGISTRIES_FLOOR = 23

# `tests/test_public_workflows.py`'s PINNED_DOWNLOADS, sized HERE, outside the module (the M2352 shape;
# Task 15W review, M5308): the four pinned downloads of Task 14 and the four GPL/LGPL source archives of
# Task 15W. Lower it only with the reason in the commit.
PUBLIC_WORKFLOWS_FLOORS = {"PINNED_DOWNLOADS": 8}

# `tests/test_ht_m12_doc.py`'s spelled lists, sized HERE, outside the module (M2352, F4): a list
# cut together with its own collection-time count is green inside the module that holds both.
# Literals from the M12 plan's HT-M12 section; lower one only with the reason in the commit.
HT_M12_DOC_FLOORS = {
    "REGISTER": 17,
    "ALL_STEPS": 27,
    "VERBATIM_COMMANDS": 20,
    "PLAN_SENTENCES": 37,
    "NOT_RUN_CONDITIONS": 4,
    "QUOTED": 40,
}

# `tests/test_ht_m13_doc.py`'s spelled lists, sized HERE, outside the module (the M2352 shape).
# Literals from the M13 plan's HT-M13 section; lower one only with the reason in the commit.
HT_M13_DOC_FLOORS = {
    "REGISTER": 10,
    "CARRIED": 5,
    "ALL_STEPS": 21,
    "VERBATIM_COMMANDS": 17,
    "PLAN_SENTENCES": 26,
    "QUOTED": 12,
}

# `tests/test_ht_m14_doc.py`'s spelled lists, sized HERE, outside the module (the M2352 shape).
# Literals from the M14 plan's HT-M14 section and Task 9's two dispatches; lower one only with the
# reason in the commit.
HT_M14_DOC_FLOORS = {
    "REGISTER": 18,
    "CARRIED": 6,
    "ALL_STEPS": 28,
    "VERBATIM_COMMANDS": 15,
    "PLAN_SENTENCES": 28,
    "QUOTED": 43,
    "QUOTED_SHAPES": 5,
    "RECORD_NUMBERS": 4,
    "AFTER_CTRL_C": 3,
}

# `tests/test_q_measure.py`'s hand-built lists, sized HERE, outside the module (M2626, the M2352
# shape): Q4's one-invalid-name-per-rule list and the import walker's planted sources. Literals
# from the Task 15 commit; lower one only with the reason in the commit.
Q_MEASURE_FLOORS = {
    "INVALID_NAMES": 11,
    "VALID_NAMES": 6,
    "PLANTED_NAMING_IMPORTS": 8,
    "PLANTED_CLEAN": 4,
}


def defined_tests(path: Path) -> set[str]:
    """The test functions `path` DEFINES at module level or on a `Test*` class. A real parser.

    Top level and class bodies only - a `def test_` NESTED inside another function is never
    collected, so it must never satisfy the census.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                names.add(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name.startswith("test_"):
                        names.add(f"{node.name}::{item.name}")
    return names


def _targets(node: ast.stmt) -> list[ast.expr]:
    if isinstance(node, ast.Assign):
        return node.targets
    if isinstance(node, ast.AnnAssign):
        return [node.target]
    return []


def registered_names(path: Path, registry: str) -> set[str] | None:
    """The string keys/members of the module-level literal `registry` in `path`, or None.

    A real parse: the assignment's value must be a dict, set, tuple, list or frozenset({...})
    LITERAL of string constants. Anything else - a comprehension, a name built at run time -
    returns None, and the caller reports it: a registry the census cannot read is a registry it
    cannot check.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not any(isinstance(t, ast.Name) and t.id == registry for t in _targets(node)):
            continue
        value = getattr(node, "value", None)
        if isinstance(value, ast.Call) and getattr(value.func, "id", "") == "frozenset":
            value = value.args[0] if value.args else None
        # ast.Dict -> its keys; ast.Set / ast.Tuple / ast.List -> their elts.
        elements = value.keys if isinstance(value, ast.Dict) else getattr(value, "elts", None)
        if elements is None:
            return None
        if not all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in elements):
            return None
        return {e.value for e in elements}
    return None


def unmapped_registries(path: Path, mapped: tuple[str, ...]) -> list[str]:
    """Module-level names in `path` matching *CONTROLS* that are neither mapped nor excluded.

    This is what makes REGISTRIES itself checked (pass 2, A5): a registry the census was never
    told about is reported, instead of passing silently because the map omits it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = {
        target.id
        for node in tree.body
        for target in _targets(node)
        if isinstance(target, ast.Name) and "CONTROLS" in target.id
    }
    return sorted(found - set(mapped) - NOT_REGISTRIES)


def census_errors(
    tests_dir: Path,
    census: dict[str, frozenset[str]],
    registries: dict[str, tuple[str, ...]],
    *,
    floors: tuple[int, int, int] | None,
    include_private: bool = True,
) -> list[str]:
    """Every way the suite has shrunk below its census. Empty means intact.

    EVERY input is explicit, so a test can drive every check on a hand-built census (pass 2, A5:
    rev 2 disabled the registry check for any census that was not the module constant). `floors`
    is (modules, controls, registries); the conftest hook passes the literals, a unit test may
    pass None to exercise the other checks on a small fixture.
    """
    errors: list[str] = []
    if not census:
        return ["the census is empty; it protects nothing"]
    if not registries:
        errors.append("the census maps no registries; REGISTRIES is empty")
    if floors is not None:
        modules_floor, controls_floor, registries_floor = floors
        if len(census) < modules_floor:
            errors.append(f"the census lists {len(census)} modules, floor {modules_floor}")
        total = sum(len(names) for names in census.values())
        if total < controls_floor:
            errors.append(f"the census lists {total} controls, floor {controls_floor}")
        if len(registries) < registries_floor:
            errors.append(
                f"REGISTRIES maps {len(registries)} modules, floor {registries_floor}"
            )
    for module, controls in sorted(census.items()):
        path = tests_dir / module
        if not controls:
            errors.append(f"{module}: the census names no controls for it")
        if not path.is_file():
            errors.append(f"{module} is GONE, and it held {sorted(controls)}")
            continue
        missing = sorted(controls - defined_tests(path))
        if missing:
            errors.append(f"{module} no longer defines {missing}")
        mapped = registries.get(module, ())
        for forgotten in unmapped_registries(path, mapped):
            errors.append(
                f"{module}: {forgotten} looks like a registry the census does not map"
            )
        if mapped:
            registered: set[str] = set()
            for name in mapped:
                found = registered_names(path, name)
                if found is None:
                    errors.append(f"{module}: registry {name} is gone or not a literal")
                else:
                    registered |= found
            unregistered = sorted(controls - registered)
            if unregistered:
                errors.append(f"{module} no longer REGISTERS {unregistered}")
    # A module the census does not list may still DECLARE a registry (M14 Task 7 review, its
    # concern 12, built: a new module carrying `LOAD_BEARING_CONTROLS` passed with every check
    # above green, because the unmapped-registry walk reached only the census's own modules). A
    # module that says some of its tests are load-bearing must be in the census. A module with no
    # registry stays opt-in by design: the census is an inventory of what was ruled load-bearing.
    # `include_private=False` asks the question of the tree as the export carries it (M15 Task 14).
    private = sorted(tests_dir.glob("private/test_*.py")) if include_private else []
    for path in [*sorted(tests_dir.glob("test_*.py")), *private]:
        key = path.relative_to(tests_dir).as_posix()
        if key in census:
            continue
        declared = unmapped_registries(path, ())
        if declared:
            errors.append(f"{key} declares {declared} but the census does not list it")
    return errors


# THE PUBLIC VIEW (M15 Task 14). `tests/private/` holds the assertions about this repository's
# private documents and tools, and the public export never carries it; a census key that starts
# with PRIVATE names a module there. The public tree runs the census minus those keys, against
# floors of its own, so its census is non-empty and complete for the tree it actually has.
PRIVATE = "private/"


def public_census() -> dict[str, frozenset[str]]:
    return {k: v for k, v in CENSUS.items() if not k.startswith(PRIVATE)}


def public_registries() -> dict[str, tuple[str, ...]]:
    return {k: v for k, v in REGISTRIES.items() if not k.startswith(PRIVATE)}


def active_census(
    tests_dir: Path,
) -> tuple[dict[str, frozenset[str]], dict[str, tuple[str, ...]], tuple[int, int, int]]:
    """The census `conftest.py` checks: the whole one where `tests/private/` exists (this
    repository), the public view where it does not (the export)."""
    if (tests_dir / "private").is_dir():
        return (
            CENSUS,
            REGISTRIES,
            (CENSUS_MODULES_FLOOR, CENSUS_CONTROLS_FLOOR, REGISTRIES_FLOOR),
        )
    floors = (
        PUBLIC_CENSUS_MODULES_FLOOR,
        PUBLIC_CENSUS_CONTROLS_FLOOR,
        PUBLIC_REGISTRIES_FLOOR,
    )
    return public_census(), public_registries(), floors
