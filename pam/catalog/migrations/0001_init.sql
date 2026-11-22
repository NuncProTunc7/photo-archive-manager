-- 0001_init.sql - Photo Archive Manager catalog schema v1 (design spec section 4)

CREATE TABLE files (
  id INTEGER PRIMARY KEY,
  rel_path TEXT NOT NULL,
  filename TEXT NOT NULL,
  ext TEXT NOT NULL,
  media_type TEXT NOT NULL,
  size INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL,
  sha256 TEXT,
  status TEXT NOT NULL DEFAULT 'present',
  companion_group_id INTEGER REFERENCES companion_groups(id),
  first_seen_at TEXT NOT NULL,
  last_scanned_at TEXT NOT NULL,
  UNIQUE (rel_path)
);
CREATE INDEX idx_files_sha256 ON files(sha256);
CREATE INDEX idx_files_status ON files(status);

CREATE TABLE companion_groups (
  id INTEGER PRIMARY KEY,
  primary_file_id INTEGER NOT NULL,
  kind TEXT NOT NULL
);

CREATE TABLE capture (
  file_id INTEGER PRIMARY KEY REFERENCES files(id),
  capture_dt TEXT,
  capture_dt_source TEXT NOT NULL,
  tz_offset_min INTEGER,
  gps_lat REAL, gps_lon REAL,
  width INTEGER, height INTEGER,
  camera_make TEXT, camera_model TEXT,
  exif_json TEXT NOT NULL
);

CREATE TABLE inferred_facts (
  id INTEGER PRIMARY KEY,
  sha256 TEXT NOT NULL,
  kind TEXT NOT NULL,
  value_json TEXT NOT NULL,
  confidence REAL,
  model_name TEXT NOT NULL,
  model_version TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  superseded_by INTEGER REFERENCES inferred_facts(id),
  UNIQUE (sha256, kind, model_name, model_version)
);
CREATE INDEX idx_inferred_sha_kind ON inferred_facts(sha256, kind);

CREATE TABLE user_facts (
  id INTEGER PRIMARY KEY,
  sha256 TEXT NOT NULL,
  kind TEXT NOT NULL,
  value_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (sha256, kind)
);

CREATE TABLE persons (
  id INTEGER PRIMARY KEY,
  label TEXT NOT NULL UNIQUE,
  filename_label TEXT,
  hidden INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE face_instances (
  id INTEGER PRIMARY KEY,
  sha256 TEXT NOT NULL,
  bbox_json TEXT NOT NULL,
  embedding BLOB NOT NULL,
  cluster_id INTEGER,
  person_id INTEGER REFERENCES persons(id),
  det_confidence REAL NOT NULL,
  model_name TEXT NOT NULL,
  model_version TEXT NOT NULL
);
CREATE INDEX idx_faces_sha256 ON face_instances(sha256);
CREATE INDEX idx_faces_cluster ON face_instances(cluster_id);

CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  label TEXT,
  start_dt TEXT NOT NULL,
  end_dt TEXT NOT NULL,
  algorithm TEXT NOT NULL,
  algo_version TEXT NOT NULL,
  params_json TEXT NOT NULL
);

CREATE TABLE file_events (
  file_id INTEGER PRIMARY KEY REFERENCES files(id),
  event_id INTEGER NOT NULL REFERENCES events(id)
);
CREATE INDEX idx_file_events_event ON file_events(event_id);

CREATE TABLE plans (
  id INTEGER PRIMARY KEY,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL,
  template TEXT NOT NULL,
  config_json TEXT NOT NULL,
  app_version TEXT NOT NULL,
  schema_version INTEGER NOT NULL
);

CREATE TABLE plan_items (
  id INTEGER PRIMARY KEY,
  plan_id INTEGER NOT NULL REFERENCES plans(id),
  file_id INTEGER NOT NULL REFERENCES files(id),
  old_rel_path TEXT NOT NULL,
  generated_filename TEXT NOT NULL,
  new_filename TEXT NOT NULL,
  tokens_json TEXT NOT NULL,
  tier TEXT NOT NULL,
  trust_score INTEGER NOT NULL,
  trust_reasons_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'proposed'
);
CREATE INDEX idx_plan_items_plan_status ON plan_items(plan_id, status);

CREATE TABLE journal (
  id INTEGER PRIMARY KEY,
  session_id TEXT NOT NULL,
  plan_item_id INTEGER NOT NULL REFERENCES plan_items(id),
  op TEXT NOT NULL,
  old_rel_path TEXT NOT NULL,
  new_rel_path TEXT NOT NULL,
  size INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL,
  state TEXT NOT NULL,
  intent_at TEXT NOT NULL,
  resolved_at TEXT
);
CREATE INDEX idx_journal_session ON journal(session_id);
CREATE INDEX idx_journal_state ON journal(state);

CREATE TABLE enrichment_runs (
  id INTEGER PRIMARY KEY,
  enricher TEXT NOT NULL,
  model_name TEXT NOT NULL,
  model_version TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  cursor_file_id INTEGER
);
