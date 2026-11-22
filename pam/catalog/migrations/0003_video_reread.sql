-- 0003_video_reread.sql - M4/C13: videos were read with -fast2, which returns no
-- QuickTime metadata when the moov atom follows mdat (design spec 5.5 rev 4).
-- Clearing their hash re-arms the rev-3 one-time backfill, so the next scan
-- re-reads exactly the videos and nothing else. No schema change.

UPDATE files SET sha256 = NULL WHERE media_type = 'video';
