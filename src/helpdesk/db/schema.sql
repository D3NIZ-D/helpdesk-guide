-- helpdesk-guide compiled content + local telemetry store.
--
-- Content is authored as YAML under content/ and compiled into this file;
-- nothing here is edited by hand at runtime except the telemetry tables.
-- Recompiling replaces every content row, so the database is disposable
-- and the YAML is the source of truth (design doc section 4).
--
-- Enumerated values are stored in their canonical English spelling; the
-- loader folds Turkish input spellings before they reach the database.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── Content ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS categories (
  id        INTEGER PRIMARY KEY,
  code      TEXT NOT NULL UNIQUE,           -- 'donanim/goruntu'
  name      TEXT NOT NULL,
  parent_id INTEGER REFERENCES categories(id)
);

CREATE TABLE IF NOT EXISTS records (
  id           INTEGER PRIMARY KEY,
  code         TEXT NOT NULL UNIQUE,        -- 'DSP-001'
  tier         TEXT NOT NULL DEFAULT 'runbook'
                 CHECK(tier IN ('runbook','guide','reference')),
  lang         TEXT NOT NULL DEFAULT 'tr',
  title        TEXT NOT NULL,
  summary      TEXT,
  category_id  INTEGER NOT NULL REFERENCES categories(id),
  severity     TEXT CHECK(severity IN ('low','medium','high','critical')),
  status       TEXT NOT NULL DEFAULT 'draft'
                 CHECK(status IN ('draft','published','archived')),
  -- Confidence ladder (section 22.5).  Multiplies the match score so that
  -- unverified content can never outrank content somebody stood behind.
  verification TEXT NOT NULL DEFAULT 'draft'
                 CHECK(verification IN ('verified','reviewed','draft','generated')),
  os_scope     TEXT,                        -- JSON array
  asset_scope  TEXT,                        -- JSON array
  tags         TEXT,                        -- JSON array
  entry_node   TEXT,                        -- runbooks only
  fix_summary  TEXT,                        -- reference cards only
  likely_causes TEXT,                       -- JSON array, reference cards
  source_json  TEXT,                        -- JSON {name,url,note}
  related_runbook TEXT,
  related      TEXT,                        -- JSON array of codes
  merged_into  TEXT,
  version      INTEGER NOT NULL DEFAULT 1,
  author       TEXT,
  reviewed_at  TEXT,
  review_due   TEXT,                        -- staleness check
  content_hash TEXT NOT NULL,               -- SHA-256 of the source YAML
  source_path  TEXT,
  updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_records_status ON records(status, tier);
CREATE INDEX IF NOT EXISTS idx_records_category ON records(category_id);
CREATE INDEX IF NOT EXISTS idx_records_lang ON records(lang);

CREATE TABLE IF NOT EXISTS aliases (
  id         INTEGER PRIMARY KEY,
  record_id  INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
  term       TEXT NOT NULL,                 -- as written
  term_norm  TEXT NOT NULL,                 -- lowercased, folded, punctuation-free
  term_key   TEXT NOT NULL,                 -- stemmed, order-insensitive
  weight     REAL NOT NULL DEFAULT 1.0,
  kind       TEXT CHECK(kind IN ('symptom','error_code','product','abbreviation'))
);
CREATE INDEX IF NOT EXISTS idx_alias_norm ON aliases(term_norm);
CREATE INDEX IF NOT EXISTS idx_alias_key  ON aliases(term_key);

CREATE TABLE IF NOT EXISTS nodes (
  id             INTEGER PRIMARY KEY,
  record_id      INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
  key            TEXT NOT NULL,             -- 'N10', unique within a record
  type           TEXT NOT NULL CHECK(type IN
                   ('question','instruction','measurement','decision',
                    'resolution','escalation')),
  title          TEXT NOT NULL,
  body_md        TEXT,
  verify_text    TEXT,                      -- "How you know: the LED turns white"
  risk           TEXT CHECK(risk IN ('low','medium','high')) DEFAULT 'low',
  requires_admin INTEGER NOT NULL DEFAULT 0,
  requires_user_downtime INTEGER NOT NULL DEFAULT 0,
  est_seconds    INTEGER,
  rollback_md    TEXT,                      -- mandatory when risk = 'high'
  media          TEXT,                      -- JSON array of media paths
  root_cause     TEXT,
  closure_note   TEXT,
  followup_md    TEXT,
  part_required  TEXT,
  escalate_to    TEXT,
  summary_template TEXT,
  order_idx      INTEGER NOT NULL DEFAULT 0,
  UNIQUE(record_id, key)
);

CREATE TABLE IF NOT EXISTS edges (
  id         INTEGER PRIMARY KEY,
  node_id    INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  label      TEXT NOT NULL,                 -- "No, it never lights up"
  target_key TEXT NOT NULL,                 -- 'N20'
  order_idx  INTEGER NOT NULL DEFAULT 0,
  condition  TEXT                           -- optional: 'os == "windows"'
);
CREATE INDEX IF NOT EXISTS idx_edges_node ON edges(node_id, order_idx);

CREATE TABLE IF NOT EXISTS steps (
  id             INTEGER PRIMARY KEY,
  record_id      INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
  order_idx      INTEGER NOT NULL,
  title          TEXT NOT NULL,
  note           TEXT,
  body_md        TEXT,
  risk           TEXT CHECK(risk IN ('low','medium','high')) DEFAULT 'low',
  requires_admin INTEGER NOT NULL DEFAULT 0,
  est_seconds    INTEGER,
  UNIQUE(record_id, order_idx)
);

CREATE TABLE IF NOT EXISTS disambiguation (
  id         INTEGER PRIMARY KEY,
  record_id  INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
  question   TEXT NOT NULL,
  answer     TEXT NOT NULL,
  target_code TEXT NOT NULL,
  order_idx  INTEGER NOT NULL DEFAULT 0
);

-- ── Search index ──────────────────────────────────────────────────────
-- One FTS table per language would be ideal (normalisation rules differ),
-- but FTS5 cannot be created dynamically without churn, so language is a
-- filter column and the normaliser is selected per query instead.

CREATE VIRTUAL TABLE IF NOT EXISTS fts_records USING fts5(
  title,
  aliases,
  body,
  tags,
  record_id UNINDEXED,
  lang UNINDEXED,
  tokenize = "unicode61 remove_diacritics 2"
);

-- ── Telemetry (local only, never leaves the machine) ──────────────────

CREATE TABLE IF NOT EXISTS sessions (
  id            INTEGER PRIMARY KEY,
  started_at    TEXT NOT NULL,
  ended_at      TEXT,
  query_raw     TEXT NOT NULL,
  query_norm    TEXT NOT NULL,
  record_id     INTEGER REFERENCES records(id),
  record_version INTEGER,                   -- pinned at session start
  match_score   REAL,
  match_method  TEXT,                       -- alias_exact | alias_key | fts | manual
  outcome       TEXT CHECK(outcome IN
                  ('resolved','escalated','abandoned','unresolved')),
  root_cause    TEXT,
  resolution_node TEXT,
  duration_s    INTEGER,
  context_json  TEXT,                       -- {os, asset, dock, ...}
  -- The walk itself, so a browser refresh or a server restart does not
  -- lose a call that is already six steps in.
  state_json    TEXT,
  agent_ref     TEXT                        -- pseudonym or local user hash
);
CREATE INDEX IF NOT EXISTS idx_sessions_record ON sessions(record_id, started_at);
CREATE INDEX IF NOT EXISTS idx_sessions_outcome ON sessions(outcome, started_at);

CREATE TABLE IF NOT EXISTS session_steps (
  id         INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  node_key   TEXT NOT NULL,
  node_title TEXT NOT NULL,
  answer     TEXT,
  skipped    INTEGER NOT NULL DEFAULT 0,    -- kept separate: matters on escalation
  entered_at TEXT NOT NULL,
  dwell_s    INTEGER,
  order_idx  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_steps ON session_steps(session_id, order_idx);

CREATE TABLE IF NOT EXISTS knowledge_gaps (
  id         INTEGER PRIMARY KEY,
  query_norm TEXT NOT NULL UNIQUE,
  query_raw  TEXT,
  hit_count  INTEGER NOT NULL DEFAULT 1,
  best_score REAL,
  first_seen TEXT NOT NULL,
  last_seen  TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'open'
               CHECK(status IN ('open','content_written','invalid'))
);

CREATE TABLE IF NOT EXISTS feedback (
  id         INTEGER PRIMARY KEY,
  record_id  INTEGER REFERENCES records(id) ON DELETE CASCADE,
  node_key   TEXT,
  kind       TEXT NOT NULL CHECK(kind IN ('worked','did_not_work','content_error')),
  note       TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_record ON feedback(record_id, kind);

-- ── Build metadata ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS build_info (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
