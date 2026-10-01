# push_run_2026-10-01 — ham özet

Bu klasör `docs/push-channel/README.md`'nin dayandığı özet kayıttır. Ham kaydın tamamı (tam `requests.jsonl`,
`ws.jsonl` vb.) yerelde, gitignore'lu `data/research_index/push_run_2026-10-01*` altındadır.

- `summary_main.json`, `transitions_main.csv`, `status_frames_main.jsonl` — 3 sporlu ana koşu (99 dk).
- `direct/` — doğrudan bağlantı deneyi (Talimat 07): `direct.jsonl` (olay günlüğü; kimlik yalnız grup no),
  `direct_status_frames.jsonl` ve `page_status_frames.jsonl` (karşılaştırma), `metrics.jsonl`, `run.jsonl`,
  `polls.jsonl`.
- `light/` — hafif sayfa üretim maliyeti ölçümü: `metrics.jsonl`, `subs.jsonl`, `run.jsonl`.

Gizli bilgi: push `CONNECT` kimlik bilgisi hiçbir dosyada yok (yalnızca tuzlu HMAC "grup" numarası); `INFO`
karelerinden yalnızca version/auth_required/tls_required; istemci IP'si/şehri/TLS parmak izi hiç yazılmadı.
