-- 0004_plan_unit_key.sql
-- spec rev 6 section 4: a plan freezes the companion grouping it was built
-- against, so section 7.3's apply never consults files.companion_group_id (which
-- pam scan recomputes from scratch on every scan).
ALTER TABLE plan_items ADD COLUMN unit_key TEXT NOT NULL DEFAULT '';
CREATE INDEX idx_plan_items_unit ON plan_items(plan_id, unit_key);
