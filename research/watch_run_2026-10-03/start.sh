#!/usr/bin/env bash
# 03-B doğrulama koşusu: origin/main'in temiz bir kopyasından, üç spor ayrı süreçte,
# hepsi tek bir ≤ 1 istek/sn bütçesini paylaşarak (scripts/_research_common.py kilit dosyası).
# Çıktı: $BASE/out (sonra research/watch_run_2026-10-03/ altına kopyalanır).
set -euo pipefail

REPO=${REPO:-/home/tunjayoff/Desktop/OwnProjects/sofascore_scraper}
BASE=${BASE:-$HOME/.cache/sofascore_research/watch_run_2026-10-03}
REF=${REF:-origin/main}
N=${N:-10}
HOURS=${HOURS:-2}
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=$BASE/repo
OUT=$BASE/out
LOG=$BASE/logs
PY=$REPO/.venv/bin/python
S=$WORK/research/watch_run_2026-10-03

rm -rf "$WORK"
mkdir -p "$WORK" "$OUT" "$LOG"
git -C "$REPO" fetch -q origin main
git -C "$REPO" archive "$REF" | tar -x -C "$WORK"
git -C "$REPO" rev-parse "$REF" > "$OUT/commit.txt"
mkdir -p "$S"
cp "$HERE"/_common.py "$HERE"/pick.py "$HERE"/run.py "$HERE"/summarize.py "$S/"

echo "pick $(date -u +%FT%TZ)" >> "$LOG/start.log"
"$PY" "$S/pick.py" --out "$OUT" --n "$N" > "$LOG/pick.log" 2>&1

pids=()
for sport in football basketball tennis; do
  # Her süreç kendi Chrome profilini kullanır (aynı profili iki Chrome açamaz)
  SOFASCORE_BROWSER_PROFILE=$BASE/chrome_$sport "$PY" "$S/run.py" --sport "$sport" --out "$OUT" --hours "$HOURS" \
    > "$LOG/$sport.log" 2>&1 &
  pids+=($!)
done
trap 'kill -TERM "${pids[@]}" 2>/dev/null || true' TERM INT
wait "${pids[@]}" || true

"$PY" "$S/summarize.py" "$OUT" > "$OUT/summary.txt" 2>&1 || true
echo "done $(date -u +%FT%TZ)" >> "$LOG/start.log"
