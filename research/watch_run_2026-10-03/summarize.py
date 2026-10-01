"""
Koşu özeti: python summarize.py [klasör] (varsayılan: bu dosyanın klasörü).

Okur: watch_events.jsonl, spor başına watch_state_{sport}.json / final_{sport}.json /
run_summary_{sport}.json, picked.json, picked_detail.json. Yazar: stdout ve summary.json.

  - completed: ilk `completed` olayı (provisional alanı taşıyan status_changed).
  - gecikme: olayın at_utc'si − SofaScore changeTimestamp'i (change_ts), saniye.
  - stuck: olay sayısı; "gerçekten takılı" = koşu sonundaki son okumada hâlâ bitmemiş
    (canlı, başlamamış ya da void). Bitmiş olanlar yanlış alarmdır.
  - istek: seçim + üç izleyici + son kontrol, gerçek HTTP istekleri (403 tekrarları dahil).
  - bütçe: requests.log (her isteğin ortak kilitten sıra aldığı an, tüm süreçler) → en küçük
    aralık ve herhangi bir 1 sn penceresindeki en çok istek.
"""
from __future__ import annotations

import bisect
import collections
import datetime as dt
import json
import math
import os
import statistics as st
import sys

SPORTS = ("football", "basketball", "tennis")
D = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))


def load(name, default=None):
    try:
        with open(os.path.join(D, name), encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return default


def dist(v):
    v = sorted(v)
    if not v:
        return {"n": 0}
    d = {"n": len(v), "min": round(v[0]), "median": round(st.median(v)), "max": round(v[-1])}
    if len(v) >= 10:
        d["p90"] = round(v[math.ceil(0.9 * len(v)) - 1])
    return d


picked = load("picked.json", {})
sport_of = {str(e): s for s, ids in picked.items() for e in ids}
with open(os.path.join(D, "watch_events.jsonl"), encoding="utf-8") as f:
    evs = [json.loads(line) for line in f if line.strip()]
states = {s: load(f"watch_state_{s}.json", {}) for s in SPORTS}
finals = {s: (load(f"final_{s}.json", {}) or {}).get("events", {}) for s in SPORTS}
runs = {s: load(f"run_summary_{s}.json") for s in SPORTS}
pick_requests = (load("picked_detail.json", {}) or {}).get("requests", 0)

by_type = collections.Counter(
    (sport_of.get(str(e["event_id"])), e["type"] + (":" + e["to"] if e["type"] == "status_changed" else "")) for e in evs
)

lags = collections.defaultdict(list)
completed_rows = []
for e in evs:
    if e["type"] == "status_changed" and e["to"] == "completed" and e.get("provisional") is not None and e.get("change_ts"):
        lag = dt.datetime.fromisoformat(e["at_utc"]).timestamp() - e["change_ts"]
        s = sport_of.get(str(e["event_id"]))
        lags[s].append(lag)
        lags["all"].append(lag)
        completed_rows.append({"sport": s, "event_id": e["event_id"], "from": e["from"], "lag_s": round(lag)})

stuck = []
for e in evs:
    if e["type"] == "stuck":
        s = sport_of.get(str(e["event_id"]))
        final = finals.get(s, {}).get(str(e["event_id"]), {})
        real = final.get("class") in ("live", "not_started", "void", "unknown", None)
        stuck.append({"sport": s, "event_id": e["event_id"], "final": final.get("description"), "real": real})

slots = []
try:
    with open(os.path.join(D, "requests.log"), encoding="utf-8") as f:
        slots = sorted(float(line.split()[0]) for line in f if line.strip())
except OSError:
    pass
gaps = [b - a for a, b in zip(slots, slots[1:], strict=False)]
# Bir isteğin başladığı andan itibaren 1 sn içinde başlayan istek sayısı (kendisi dahil), en büyüğü
per_window = max((bisect.bisect_left(slots, s + 1.0) - i for i, s in enumerate(slots)), default=0)
budget = {"logged": len(slots), "min_gap_s": round(min(gaps), 3) if gaps else None, "max_in_1s": per_window}

http = {s: (r or {}).get("http_requests", 0) for s, r in runs.items()}
duration = {
    s: round(((r or {}).get("ended", 0) - (r or {}).get("started", 0)) / 60, 1) if r else None for s, r in runs.items()
}
summary = {
    "picked": {s: len(ids) for s, ids in picked.items()},
    "events": {f"{k[0]}/{k[1]}": n for k, n in sorted(by_type.items(), key=str)},
    "completed": {s: len(lags[s]) for s in (*SPORTS, "all")},
    "lag_s": {s: dist(lags[s]) for s in (*SPORTS, "all")},
    "completed_rows": completed_rows,
    "stuck": {"n": len(stuck), "real": sum(x["real"] for x in stuck), "rows": stuck},
    "final_class": {s: dict(collections.Counter(v.get("class") for v in finals[s].values())) for s in SPORTS},
    "last_state_class": {s: dict(collections.Counter(v.get("class") for v in states[s].values())) for s in SPORTS},
    "requests": {"pick": pick_requests, **http, "total": pick_requests + sum(http.values())},
    "budget": budget,
    "duration_min": duration,
    "rate_req_per_s": {
        s: round(http[s] / ((runs[s]["ended"] - runs[s]["started"]) or 1), 3) if runs[s] else None for s in SPORTS
    },
}
with open(os.path.join(D, "summary.json"), "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
for k, v in summary.items():
    if k not in ("completed_rows",):
        print(f"{k}: {json.dumps(v, ensure_ascii=False)}")
