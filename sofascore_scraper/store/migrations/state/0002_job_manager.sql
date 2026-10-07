-- state.db, geçiş 0002: iş yöneticisi (docs/design/01-storage.md bölüm 3.3; iş modeli 02-services.md 2.8).
-- `jobs` tablosuna işi başlatan yüz, servis belirtimi, bitiş hatası, kalp atışı ve yaratılış anı eklenir;
-- `job_events` işin olay günlüğüdür (aşama, ilerleme, günlük satırı, başarısız öğe, devre kesici).
-- Geçiş yeniden çalıştırılabilir: çalıştırıcı var olan bir sütunu ekleyen ALTER deyimini atlar (bölüm 7.3).

ALTER TABLE jobs ADD COLUMN origin_json TEXT;       -- yüz (cli|api|scheduler|library), pid, makine
ALTER TABLE jobs ADD COLUMN spec_json TEXT;         -- servis belirtimi
ALTER TABLE jobs ADD COLUMN error_json TEXT;        -- {code, message, details}
ALTER TABLE jobs ADD COLUMN heartbeat_at INTEGER;   -- epoch ms; yalnızca gösterim için (canlılık: satır + kilit)
ALTER TABLE jobs ADD COLUMN created_at TEXT;        -- ISO-8601 UTC; 0002'den önce yazılan satırlarda NULL (okuyan started_at kullanır)

CREATE TABLE IF NOT EXISTS job_events (
  job_id    TEXT    NOT NULL,
  seq       INTEGER NOT NULL,                       -- iş başına 1'den başlar
  ts_ms     INTEGER NOT NULL,
  type      TEXT    NOT NULL,                       -- started | phase | progress | log | failed | breaker | cancel_requested | finished
  data_json TEXT    NOT NULL,
  PRIMARY KEY (job_id, seq)
) WITHOUT ROWID;
