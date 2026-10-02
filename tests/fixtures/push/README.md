# Recorded push frames (P24)

`recorded_wire.jsonl` holds frames that SofaScore's own page received on its push connection (NATS over
WebSocket), written back in the NATS text protocol so that `tests/test_live_push_source.py` can feed them to the
frame reader exactly as a page would.

- Source: `research/all_sports/ws.jsonl` (recorded on 2026-10-01 by listening to the site's own connection; no
  connection of our own, no subscription). Only `recv` rows are used: the first `INFO` and every `MSG` on
  `sport.football`, `sport.tennis`, `sport.basketball`, `event.*` and `odds.*`.
- Each line: `at` (seconds after the first row), `subject`, `data` (the frame as the server sends it). The MSG
  bodies are the recorded bodies, serialised compactly; the byte length in the header is recomputed. The
  subscription ids are made up (1 to 3, 9).
- Masked or left out: the `CONNECT` frame (the credential) is not in the source and is not here; `INFO` keeps
  only `version`, `auth_required` and `tls_required` (no client address, no server id, no cluster details);
  the server address is not recorded.
