-- state.db, geçiş 0001: docs/design/01-storage.md bölüm 3.3'teki DDL.
-- state.db yeniden kurulamayan her şeyi tutar (takipler, işler, olay akışları, sink imleçleri, izleyici
-- durumu, çalışma zamanı bilgileri, kilit sahipleri, taşıma geçmişi); catalog.db ise tümüyle türetilir.
-- Geçişler yeniden çalıştırılabilir yazılır (bölüm 7.3): her CREATE "IF NOT EXISTS" ile.

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
) WITHOUT ROWID;                                    -- stream_id, imported_jobs_db, legacy_follows_sig

CREATE TABLE IF NOT EXISTS follows (
  id          INTEGER PRIMARY KEY,
  kind        TEXT    NOT NULL CHECK (kind IN ('tournament', 'team', 'player', 'event')),
  entity_id   INTEGER NOT NULL,
  sport       TEXT,                                 -- kayıt defteri kısa adı; NULL = henüz bilinmiyor
  name        TEXT    NOT NULL,                     -- görünen ad (leagues.txt'nin ad sütunu)
  seasons     TEXT    NOT NULL DEFAULT 'all',       -- 'all' | 'current' | 'last:N' | sezon kimliklerinin JSON dizisi
  slices_json TEXT,                                 -- NULL = varsayılanlar; değilse JSON seçim
  live        INTEGER NOT NULL DEFAULT 0,
  enabled     INTEGER NOT NULL DEFAULT 1,
  origin      TEXT    NOT NULL DEFAULT 'api' CHECK (origin IN ('legacy', 'config', 'api')),
  position    INTEGER NOT NULL,                     -- görüntüleme ve dosya sırası
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL,
  UNIQUE (kind, entity_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS follows_tournament_name ON follows(name) WHERE kind = 'tournament';
CREATE INDEX IF NOT EXISTS follows_position ON follows(position, id);

CREATE TABLE IF NOT EXISTS jobs (                   -- 2.x .meta/jobs.db'nin sütunları, aynı sırayla
  id                        TEXT PRIMARY KEY,
  status                    TEXT    NOT NULL,
  progress                  INTEGER NOT NULL DEFAULT 0,
  current_task              TEXT    NOT NULL DEFAULT '',
  payload_json              TEXT,
  log_json                  TEXT    NOT NULL DEFAULT '[]',
  result_json               TEXT,
  started_at                TEXT,
  finished_at               TEXT,
  cancel_requested          INTEGER NOT NULL DEFAULT 0,
  matches_total             INTEGER NOT NULL DEFAULT 0,
  matches_done              INTEGER NOT NULL DEFAULT 0,
  matches_failed            INTEGER NOT NULL DEFAULT 0,
  schedule_empty_seasons    INTEGER NOT NULL DEFAULT 0,
  circuit_breaker_triggered INTEGER NOT NULL DEFAULT 0,
  circuit_breaker_reason    TEXT,
  eta_seconds               REAL,
  current_batch             TEXT    NOT NULL DEFAULT '',
  kind                      TEXT    NOT NULL DEFAULT 'fetch',   -- state.db'de yeni
  owner                     TEXT                                -- yeni: işi çalıştıran sürecin kilit sahibi kimliği
);
CREATE INDEX IF NOT EXISTS jobs_started ON jobs(started_at DESC);

CREATE TABLE IF NOT EXISTS stream_events (          -- kalıcı her akış: live, change, job, system
  seq           INTEGER PRIMARY KEY AUTOINCREMENT,  -- bütün akışlar için tek sıra numarası
  stream        TEXT    NOT NULL,
  ts_ms         INTEGER NOT NULL,                   -- kaydedildiği an (epoch ms, UTC)
  type          TEXT    NOT NULL,                   -- live.status_changed | change.recorded | job.finished | ...
  event_id      INTEGER,
  sport         TEXT,
  tournament_id INTEGER,
  source        TEXT,                               -- push | poll | job | system
  dedup_key     TEXT,
  payload_json  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS stream_events_stream ON stream_events(stream, seq);
CREATE INDEX IF NOT EXISTS stream_events_event  ON stream_events(event_id, seq);
CREATE INDEX IF NOT EXISTS stream_events_ts     ON stream_events(ts_ms);
CREATE UNIQUE INDEX IF NOT EXISTS stream_events_dedup ON stream_events(stream, dedup_key) WHERE dedup_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS sink_cursors (
  sink       TEXT PRIMARY KEY,
  seq        INTEGER NOT NULL,                      -- son teslim edilen stream_events.seq
  updated_at INTEGER NOT NULL,
  last_error TEXT
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS watch_state (
  watcher    TEXT    NOT NULL,
  event_id   INTEGER NOT NULL,
  state_json TEXT    NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (watcher, event_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS runtime (                -- süreçler arası küçük bilgiler (son köprü sağlık özeti)
  key        TEXT PRIMARY KEY,
  value_json TEXT    NOT NULL,
  pid        INTEGER,
  updated_at INTEGER NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS leases (
  name         TEXT PRIMARY KEY,
  holder       TEXT    NOT NULL,
  pid          INTEGER NOT NULL,
  host         TEXT    NOT NULL,
  purpose      TEXT    NOT NULL DEFAULT '',
  acquired_at  INTEGER NOT NULL,
  heartbeat_at INTEGER NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS migration_runs (
  id            INTEGER PRIMARY KEY,
  started_at    INTEGER NOT NULL,
  finished_at   INTEGER,
  dry_run       INTEGER NOT NULL,
  delete_legacy INTEGER NOT NULL,
  events_done   INTEGER NOT NULL DEFAULT 0,
  events_failed INTEGER NOT NULL DEFAULT 0,
  bytes_before  INTEGER NOT NULL DEFAULT 0,
  bytes_after   INTEGER NOT NULL DEFAULT 0,
  report_json   TEXT
);
