import collections
import json
import math
import statistics as st
D = "research/watch_run_2026-09-29"
picked = json.load(open("research/watch_run_2026-09-29/picked.json"))
sport_of = {str(e): s for s, ids in picked.items() for e in ids}
evs = [json.loads(line) for line in open(D + "/watch_events.jsonl")]
try:
    summ = json.load(open(D + "/run_summary.json"))
except OSError:
    summ = None
state = json.load(open(D + "/watch_state.json"))
by_type = collections.Counter((sport_of.get(str(e["event_id"])), e["type"] + (":" + e["to"] if e["type"] == "status_changed" else "")) for e in evs)
print("olaylar:", dict(sorted(by_type.items(), key=str)))
import datetime as dt
lags = collections.defaultdict(list)
rows = []
for e in evs:
    if e["type"] == "status_changed" and e["to"] == "completed" and e.get("provisional") is not None and e.get("change_ts"):
        at = dt.datetime.fromisoformat(e["at_utc"]).timestamp()
        lag = at - e["change_ts"]
        s = sport_of.get(str(e["event_id"]))
        lags[s].append(lag)
        lags["all"].append(lag)
        rows.append((s, e["event_id"], e["from"], e.get("source"), round(lag)))
def dist(v):
    v = sorted(v)
    if not v:
        return "n 0"
    d = f"n {len(v)}; min {v[0]:.0f} / medyan {st.median(v):.0f} / maks {v[-1]:.0f} sn"
    if len(v) >= 10:
        d += f" / p90 {v[math.ceil(0.9*len(v))-1]:.0f}"
    return d
for k in ("football", "basketball", "tennis", "all"):
    print("gecikme", k, dist(lags[k]))
print("completed satırları (spor, id, from, kaynak, gecikme sn):", rows)
print("son durum:", collections.Counter((sport_of.get(k), v.get("class")) for k, v in state.items()))
print("özet:", summ)
