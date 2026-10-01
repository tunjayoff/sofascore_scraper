-- catalog.db şeması, sürüm 1 (PRAGMA user_version = CATALOG_SCHEMA, src/store/catalog.py).
-- Kaynak: docs/design/01-storage.md, bölüm 3.3. Aşağıdaki DDL belgede basıldığı haliyle durur.
-- tests/test_store_catalog.py ikisini karşılaştırır.
-- Katalog tümüyle türetilmiştir ve göç betiği yoktur: buradaki her değişiklik CATALOG_SCHEMA artırılarak
-- yapılır, eski dosya yeniden kurulur. Belge de aynı değişiklikle güncellenir.

CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE sports (
  slug TEXT PRIMARY KEY,
  id   INTEGER,
  name TEXT
) WITHOUT ROWID;

CREATE TABLE categories (
  id     INTEGER PRIMARY KEY,
  sport  TEXT NOT NULL,
  name   TEXT,
  slug   TEXT,
  alpha2 TEXT
);

CREATE TABLE tournaments (
  id          INTEGER PRIMARY KEY,          -- uniqueTournament.id
  sport       TEXT,
  category_id INTEGER,
  name        TEXT,
  name_folded TEXT,                         -- casefolded, accents stripped (search)
  slug        TEXT,
  updated_at  INTEGER NOT NULL
);
CREATE INDEX tournaments_sport ON tournaments(sport, name_folded);

CREATE TABLE seasons (
  id            INTEGER PRIMARY KEY,        -- season.id
  tournament_id INTEGER NOT NULL,
  name          TEXT,
  year          TEXT,
  sort_key      REAL NOT NULL DEFAULT 0,    -- as SeasonFetcher._get_sortable_year_value(year)
  listed        INTEGER NOT NULL DEFAULT 0, -- 1: present in the tournament's season list payload
  position      INTEGER,                    -- order in the season list payload (0 = first)
  updated_at    INTEGER NOT NULL
);
CREATE INDEX seasons_tournament ON seasons(tournament_id, sort_key DESC, id DESC);

CREATE TABLE participants (
  id          INTEGER PRIMARY KEY,          -- team.id (competitor id space: teams, single players, pairs)
  sport       TEXT,
  name        TEXT,
  name_folded TEXT,
  short_name  TEXT,
  slug        TEXT,
  name_code   TEXT,
  country     TEXT,                         -- alpha2
  gender      TEXT,
  type        INTEGER,                      -- SofaScore team.type as given
  national    INTEGER,
  updated_at  INTEGER NOT NULL
);
CREATE INDEX participants_name ON participants(name_folded);

CREATE TABLE players (
  id          INTEGER PRIMARY KEY,          -- player.id (person id space; filled from squad and player payloads)
  name        TEXT,
  name_folded TEXT,
  slug        TEXT,
  team_id     INTEGER,
  position    TEXT,
  country     TEXT,
  updated_at  INTEGER NOT NULL
);
CREATE INDEX players_name ON players(name_folded);

CREATE TABLE events (
  id                 INTEGER PRIMARY KEY,
  sport              TEXT    NOT NULL DEFAULT '',  -- slug; '' when the payload does not say
  category_id        INTEGER,
  tournament_id      INTEGER,                      -- uniqueTournament.id; NULL when the event has none
  stage_id           INTEGER,                      -- tournament.id (the non-unique "tournament" object)
  stage_name         TEXT,                         -- tournament.name (the `tournament` column of today's lists)
  season_id          INTEGER,
  round              INTEGER,
  round_name         TEXT,
  round_slug         TEXT,
  start_ts           INTEGER,                      -- epoch seconds, UTC
  status_type        TEXT,
  status_code        INTEGER,
  status_description TEXT,
  status_class       TEXT    NOT NULL,             -- not_started|live|completed|decided_without_play|void|unknown
  home_id            INTEGER,
  away_id            INTEGER,
  home_name          TEXT,
  away_name          TEXT,
  home_score         INTEGER,                      -- homeScore.display, else .current
  away_score         INTEGER,
  home_score_current INTEGER,                      -- homeScore.current as given (legacy list and CSV shapes)
  away_score_current INTEGER,
  winner_code        INTEGER,
  scores_json        TEXT,                         -- normalised score sheet (src/status.extract_scores)
  slug               TEXT,
  custom_id          TEXT,
  -- quality
  observed_at        INTEGER,                      -- when the payload this row derives from was read; NULL = unknown (legacy)
  change_ts          INTEGER,                      -- changes.changeTimestamp
  observed_gap       INTEGER,                      -- observed_at - start_ts (NULL if either is NULL)
  status_regressed   INTEGER NOT NULL DEFAULT 0,
  tier_hint          INTEGER,
  stale              INTEGER NOT NULL DEFAULT 0,   -- a newer listing disagrees with the stored event payload
  -- provenance / storage
  row_source         TEXT    NOT NULL,             -- 'event' | 'listing'
  listed_in          TEXT,                         -- sub of the newest schedule slice that lists the event ('round_12', 'last_0')
  has_event_payload  INTEGER NOT NULL DEFAULT 0,
  layout             TEXT,                         -- 'v3' | 'legacy' | NULL (listing only)
  path               TEXT,                         -- legacy event directory relative to DATA_DIR; NULL for v3 (derived from id)
  legacy_path        TEXT,                         -- superseded legacy directory still on disk
  sig                TEXT,                         -- change signature of the indexed files (not reproduced by a rebuild)
  first_seen_at      INTEGER NOT NULL,
  updated_at         INTEGER NOT NULL
);
CREATE INDEX events_tournament_season ON events(tournament_id, season_id, start_ts, id);
CREATE INDEX events_sport_start       ON events(sport, start_ts, id);
CREATE INDEX events_start             ON events(start_ts, id);
CREATE INDEX events_season_round      ON events(season_id, round, start_ts);
CREATE INDEX events_live              ON events(sport, start_ts) WHERE status_class = 'live';
CREATE INDEX events_unsettled         ON events(observed_gap)
  WHERE has_event_payload = 1 AND observed_at IS NOT NULL;
CREATE INDEX events_unobserved        ON events(tournament_id, id)
  WHERE has_event_payload = 1 AND observed_at IS NULL;
CREATE INDEX events_legacy            ON events(id) WHERE layout = 'legacy' OR legacy_path IS NOT NULL;
CREATE INDEX events_stale             ON events(id) WHERE stale = 1;
CREATE INDEX events_open              ON events(start_ts, id) WHERE status_class IN ('not_started', 'live', 'unknown');
CREATE INDEX events_updated           ON events(updated_at, id);

CREATE TABLE event_participants (
  participant_id INTEGER NOT NULL,
  start_ts       INTEGER NOT NULL DEFAULT 0,
  event_id       INTEGER NOT NULL,
  side           INTEGER NOT NULL,                 -- 1 home, 2 away
  PRIMARY KEY (participant_id, start_ts, event_id)
) WITHOUT ROWID;
CREATE INDEX event_participants_event ON event_participants(event_id);

CREATE TABLE event_slices (
  event_id               INTEGER NOT NULL,
  key                    TEXT    NOT NULL,
  sub                    TEXT    NOT NULL DEFAULT '',
  state                  TEXT    NOT NULL CHECK (state IN ('ok', 'empty', 'error')),
  has_payload            INTEGER NOT NULL DEFAULT 0,
  fetched_at             INTEGER,
  checked_at             INTEGER,
  empty_count            INTEGER NOT NULL DEFAULT 0,
  unverified_empty_count INTEGER NOT NULL DEFAULT 0,
  error_reason           TEXT,
  error_status           INTEGER,
  error_at               INTEGER,
  error_count            INTEGER NOT NULL DEFAULT 0,
  stored_bytes           INTEGER,
  raw_bytes              INTEGER,
  history_count          INTEGER NOT NULL DEFAULT 0,
  meta_json              TEXT,
  PRIMARY KEY (event_id, key, sub)
) WITHOUT ROWID;
CREATE INDEX event_slices_not_ok ON event_slices(state, key, event_id) WHERE state != 'ok';

CREATE TABLE entity_slices (
  kind                   TEXT    NOT NULL,         -- tournament|season|team|player|sport
  entity_id              INTEGER NOT NULL,
  key                    TEXT    NOT NULL,
  sub                    TEXT    NOT NULL DEFAULT '',
  state                  TEXT    NOT NULL CHECK (state IN ('ok', 'empty', 'error')),
  has_payload            INTEGER NOT NULL DEFAULT 0,
  fetched_at             INTEGER,
  checked_at             INTEGER,
  empty_count            INTEGER NOT NULL DEFAULT 0,
  unverified_empty_count INTEGER NOT NULL DEFAULT 0,
  error_reason           TEXT,
  error_status           INTEGER,
  error_at               INTEGER,
  error_count            INTEGER NOT NULL DEFAULT 0,
  stored_bytes           INTEGER,
  raw_bytes              INTEGER,
  history_count          INTEGER NOT NULL DEFAULT 0,
  meta_json              TEXT,
  layout                 TEXT    NOT NULL,         -- 'v3' | 'legacy'
  path                   TEXT    NOT NULL,         -- payload file (legacy) or entity directory (v3), relative to DATA_DIR
  PRIMARY KEY (kind, entity_id, key, sub)
) WITHOUT ROWID;

CREATE TABLE slice_history (
  kind       TEXT    NOT NULL,                     -- 'event' or an entity kind
  entity_id  INTEGER NOT NULL,
  key        TEXT    NOT NULL,
  sub        TEXT    NOT NULL DEFAULT '',
  n          INTEGER NOT NULL,                     -- 1-based position in the history file
  fetched_at INTEGER NOT NULL,
  sha256     TEXT    NOT NULL,
  offset     INTEGER NOT NULL,                     -- byte offset of the gzip member
  length     INTEGER NOT NULL,                     -- byte length of the gzip member
  PRIMARY KEY (kind, entity_id, key, sub, n)
) WITHOUT ROWID;

CREATE TABLE changes (
  seq              INTEGER PRIMARY KEY,            -- stable: stored in the change-log line itself
  ts               INTEGER NOT NULL,
  event_id         INTEGER NOT NULL,
  sport            TEXT,
  tournament_id    INTEGER,
  status_regressed INTEGER NOT NULL DEFAULT 0,
  fields           TEXT    NOT NULL,               -- comma-separated changed field names
  row_json         TEXT    NOT NULL,               -- the log line as written
  segment          TEXT    NOT NULL                -- file it came from, relative to DATA_DIR
);
CREATE INDEX changes_event ON changes(event_id, seq);
CREATE INDEX changes_ts    ON changes(ts);

CREATE TABLE pending_writes (                      -- write-intent markers (crash recovery, 6.2)
  kind       TEXT    NOT NULL,
  entity_id  INTEGER NOT NULL,
  started_at INTEGER NOT NULL,
  PRIMARY KEY (kind, entity_id)
) WITHOUT ROWID;

CREATE TABLE legacy_roots (                        -- legacy directories seen by the last scan (incremental re-scan)
  path       TEXT PRIMARY KEY,                     -- relative to DATA_DIR
  kind       TEXT    NOT NULL,                     -- season_dir | league_dir | schedule_dir | seasons_file | flat_event | changes_file
  sig        TEXT    NOT NULL,                     -- "<mtime_ns>:<entry count or size>"
  scanned_at INTEGER NOT NULL
) WITHOUT ROWID;
