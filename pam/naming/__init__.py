"""Naming: the pure core (sanitizer, template engine, tokens, consistency, collision)
plus three modules that are NOT pure. `planner` takes the workspace lock, opens the
session log, migrates and commits (N8). Two it calls with its connection reach the
catalog through `catalog/repo/` and never commit: `events` replaces the event partition
inside the planner's transaction, and `stale_grouping` (M14 Task 5h, Z2) only reads.
"""
