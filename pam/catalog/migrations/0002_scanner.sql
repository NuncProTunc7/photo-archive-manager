-- 0002_scanner.sql - M2 scanner: cloud-placeholder flag on files (design spec section 5.2)

ALTER TABLE files ADD COLUMN placeholder INTEGER NOT NULL DEFAULT 0;
