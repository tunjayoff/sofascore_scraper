"""
Talimat 06: push kanalı koşusunun analizi (ağ isteği yok).

    python scripts/analyze_push_run.py RAW_DIR [OUT_DIR]

RAW_DIR: scripts/push_channel_run.py çıktısı (yerelde data/research_index/ altında). OUT_DIR verilirse repoya
girecek özet oraya yazılır: summary.json, status_frames.jsonl (kompakt), coverage_misses.csv, transitions.csv.

Tanımlar:
  - Yoklama geçişi: aynı sporun ardışık iki /events/live yanıtı arasında bir olayın
      * kodunun değişmesi (ör. 6 → 31),
      * listeye girmesi (başladı) ya da listeden düşmesi (bitti / ertelendi / iptal).
    Gözlem anı ikinci yanıtın geldiği an (`done`).
  - Push'ta karşılığı: aynı olay için, iki yanıt arasındaki pencerede (±120 sn pay) gelen bir status karesi;
    kod değişiminde karenin kodu yeni koda eşit olmalı, listeden düşmede herhangi bir status karesi yeterli.
  - Kopma: sekmenin push bağlantısının kapanması; kendi testlerimizin yeniden yüklemeleri ayrıca sayılır.
"""
from __future__ import annotations

import collections
import csv
import json
import math
import os
import sys
from typing import Any, Dict, List

MAIN_TABS = ("football", "tennis", "basketball")
PAD = 120.0


def load(raw: str, name: str) -> List[Dict[str, Any]]:
    path = os.path.join(raw, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def dist(v: List[float]) -> Dict[str, Any]:
    v = sorted(v)
    if not v:
        return {"n": 0}
    out = {"n": len(v), "min": round(v[0], 1), "median": round(v[len(v) // 2], 1), "max": round(v[-1], 1)}
    if len(v) >= 10:
        out["p90"] = round(v[math.ceil(0.9 * len(v)) - 1], 1)
    return out


def connections(ws_events: List[Dict[str, Any]], run: List[Dict[str, Any]]) -> Dict[str, Any]:
    reloads = [r["ts"] for r in run if r["event"] in ("cred-test-reload", "challenge", "challenge-on-open")]
    out = {}
    for tab in MAIN_TABS:
        ev = [e for e in ws_events if e["tab"] == tab]
        opens = [e for e in ev if e["event"] == "open"]
        closes = [e for e in ev if e["event"] in ("close", "socketerror")]
        if not opens:
            continue
        gaps = []
        for c in closes:
            nxt = next((o for o in opens if o["ts"] >= c["ts"]), None)
            deliberate = any(abs(c["ts"] - t) < 30 for t in reloads)
            gaps.append({"closed_at": c["ts"], "reopened_after_s": round(nxt["ts"] - c["ts"], 1) if nxt else None,
                         "deliberate": deliberate, "event": c["event"]})
        out[tab] = {"connections": len(opens), "closes": len(closes),
                    "natural_closes": sum(1 for g in gaps if not g["deliberate"]), "gaps": gaps}
    return out


def resubscribed(subs: List[Dict[str, Any]], ws_events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Her bağlantıda sport.{spor} aboneliği yapıldı mı (yeniden bağlanmalar dahil)."""
    out = {}
    for e in ws_events:
        if e["event"] == "open" and e["tab"] in MAIN_TABS:
            subj = [s["subject"] for s in subs if s["conn"] == e["conn"] and s["op"] == "SUB"]
            out[str(e["conn"])] = {"tab": e["tab"], "sport_sub": f"sport.{e['tab']}" in subj,
                                   "event_subs": sum(1 for x in subj if x.startswith("event.")), "opened": e["ts"]}
    return out


def connected_at(ws_events: List[Dict[str, Any]], tab: str):
    """tab'ın push bağlantısının açık olduğu aralıklar."""
    spans, start = [], None
    for e in sorted((e for e in ws_events if e["tab"] == tab), key=lambda e: e["ts"]):
        if e["event"] == "open":
            if start is None:
                start = e["ts"]
        elif e["event"] in ("close", "socketerror") and start is not None:
            spans.append((start, e["ts"]))
            start = None
    if start is not None:
        spans.append((start, float("inf")))
    return lambda t: any(a <= t <= b for a, b in spans)


def transitions(polls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    by_sport = collections.defaultdict(list)
    for p in polls:
        if p.get("status") == 200:
            by_sport[p["sport"]].append(p)
    for sport, ps in by_sport.items():
        ps.sort(key=lambda p: p["ts"])
        for a, b in zip(ps, ps[1:], strict=False):
            ea = {e["id"]: e for e in a["events"]}
            eb = {e["id"]: e for e in b["events"]}
            base = {"sport": sport, "from_ts": a["ts"], "seen_ts": b["done"]}
            for i, e in eb.items():
                if i not in ea:
                    out.append({**base, "id": i, "kind": "entered", "code": e["code"], "ut": e["ut_name"]})
                elif ea[i]["code"] != e["code"]:
                    out.append({**base, "id": i, "kind": "code", "from_code": ea[i]["code"], "code": e["code"],
                                "ut": e["ut_name"]})
            for i, e in ea.items():
                if i not in eb:
                    out.append({**base, "id": i, "kind": "dropped", "from_code": e["code"], "ut": e["ut_name"]})
    return out


def match_push(trs: List[Dict[str, Any]], frames: List[Dict[str, Any]], ws_events) -> None:
    by_id = collections.defaultdict(list)
    for f in frames:
        by_id[f["id"]].append(f)
    alive = {t: connected_at(ws_events, t) for t in MAIN_TABS}
    for t in trs:
        lo, hi = t["from_ts"] - PAD, t["seen_ts"] + PAD
        cand = [f for f in by_id.get(t["id"], []) if lo <= f["ts"] <= hi]
        if t["kind"] == "code" or t["kind"] == "entered":
            cand = [f for f in cand if f.get("status_code") == t["code"]]
        t["push"] = bool(cand)
        t["push_ts"] = min(f["ts"] for f in cand) if cand else None
        t["push_before_poll_s"] = round(t["seen_ts"] - t["push_ts"], 1) if cand else None
        is_alive = alive.get(t["sport"])
        t["tab_connected"] = bool(is_alive and is_alive(t["seen_ts"]) and is_alive(t["from_ts"]))


def main() -> None:
    raw = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else None
    run = load(raw, "run.jsonl")
    ws_events = load(raw, "ws_events.jsonl")
    subs = load(raw, "subs.jsonl")
    frames = load(raw, "status_frames.jsonl")
    polls = load(raw, "polls.jsonl")
    pingpong = load(raw, "pingpong.jsonl")
    metrics = load(raw, "metrics.jsonl")
    with open(os.path.join(raw, "api_counts.json"), encoding="utf-8") as f:
        api = json.load(f)
    start = next(r["ts"] for r in run if r["event"] == "start")
    end = next((r["ts"] for r in run if r["event"] == "end"), max(p["ts"] for p in polls))

    # 1. kapsam
    trs = transitions(polls)
    match_push(trs, frames, ws_events)
    connected = [t for t in trs if t["tab_connected"]]
    cov = {}
    for sport in MAIN_TABS:
        ts = [t for t in connected if t["sport"] == sport]
        by_kind = {}
        for kind in ("code", "entered", "dropped"):
            k = [t for t in ts if t["kind"] == kind]
            by_kind[kind] = {"n": len(k), "in_push": sum(t["push"] for t in k),
                             "pct": round(100 * sum(t["push"] for t in k) / len(k), 1) if k else None}
        misses = collections.Counter(t["ut"] for t in ts if not t["push"])
        cov[sport] = {"transitions": len(ts), "in_push": sum(t["push"] for t in ts),
                      "pct": round(100 * sum(t["push"] for t in ts) / len(ts), 1) if ts else None,
                      "by_kind": by_kind, "misses_by_tournament": dict(misses.most_common(15))}
    poll_ids = collections.defaultdict(set)
    for p in polls:
        if p.get("status") == 200:
            poll_ids[p["sport"]].update(e["id"] for e in p["events"])
    push_ids = collections.defaultdict(set)
    for f in frames:
        if f["tab"] in MAIN_TABS:
            push_ids[f["tab"]].add(f["id"])
    ids = {s: {"poll_live_ids": len(poll_ids[s]), "push_status_ids": len(push_ids[s]),
               "push_only_ids": len(push_ids[s] - poll_ids[s])} for s in MAIN_TABS}

    # 2. dayanıklılık
    conns = connections(ws_events, run)
    resub = resubscribed(subs, ws_events)
    gap_losses = [t for t in trs if not t["tab_connected"]]
    ping = {}
    for d, cmd in (("sent", "PING"), ("recv", "PING")):
        for tab in MAIN_TABS:
            ts = sorted(p["ts"] for p in pingpong if p["tab"] == tab and p["dir"] == d and p["cmd"] == cmd)
            # bağlantı başına ardışık aralıklar
            by_conn = collections.defaultdict(list)
            for p in pingpong:
                if p["tab"] == tab and p["dir"] == d and p["cmd"] == cmd:
                    by_conn[p["conn"]].append(p["ts"])
            gaps = [b - a for v in by_conn.values() for a, b in zip(sorted(v), sorted(v)[1:], strict=False)]
            ping[f"{tab}:{d}:{cmd}"] = dist(gaps) | {"count": len(ts)}

    # 3. kimlik bilgisi
    creds = [{"tab": e["tab"], "conn": e["conn"], "group": e["cred_group"]} for e in ws_events if e["event"] == "connect"]

    # 4. gecikme
    fin = [f for f in frames if f.get("status_type") == "finished" and f.get("change_ts") and f["tab"] in MAIN_TABS]
    lag_push = {s: dist([f["ts"] - f["change_ts"] for f in fin if f["tab"] == s]) for s in MAIN_TABS}
    first_fin = {}
    for f in sorted(fin, key=lambda f: f["ts"]):
        first_fin.setdefault((f["tab"], f["id"]), f)
    poll_lag = collections.defaultdict(list)
    push_vs_poll = collections.defaultdict(list)
    for t in trs:
        if t["kind"] == "dropped":
            f = first_fin.get((t["sport"], t["id"]))
            if f:
                poll_lag[t["sport"]].append(t["seen_ts"] - f["change_ts"])
                push_vs_poll[t["sport"]].append(t["seen_ts"] - f["ts"])
    lag_poll = {s: dist(poll_lag[s]) for s in MAIN_TABS}
    lead = {s: dist(push_vs_poll[s]) for s in MAIN_TABS}

    # 5. abonelik modeli
    sub_model = collections.defaultdict(collections.Counter)
    for s in subs:
        if s["op"] == "SUB":
            kind = s["subject"].split(".")[0] + ("." + s["subject"].split(".")[1] if s["subject"].startswith("sport.") else ".{id}")
            sub_model[s["tab"]][kind] += 1

    # 6. maliyet
    cost = [{"min": round((m["ts"] - start) / 60), "rss_mb": m["rss_mb"], "cpu_pct_sum": m["cpu_pct_sum"],
             "processes": m["processes"], "js_heap_mb": m["js_heap_mb"], "api_requests": m["api_requests"]}
            for m in metrics]

    summary = {
        "window_utc": [start, end], "duration_min": round((end - start) / 60, 1),
        "api_requests": api["total"], "api_by_pattern": api["by_pattern"],
        "idle_dropped": sum(api.get("idle_dropped", {}).values()), "idle_dropped_by_pattern": api.get("idle_dropped", {}),
        "polls": collections.Counter(f"{p['sport']}:{p.get('status')}" for p in polls),
        "status_frames": collections.Counter(f["tab"] for f in frames),
        "coverage": cov, "ids": ids,
        "connections": conns, "resubscribed": resub,
        "transitions_while_disconnected": len(gap_losses),
        "pingpong_intervals_s": ping,
        "credential_groups": creds, "distinct_credential_groups": len({c["group"] for c in creds}),
        "lag_push_finished_s": lag_push, "lag_poll_dropped_s": lag_poll, "poll_minus_push_s": lead,
        "subscriptions": {k: dict(v) for k, v in sub_model.items()},
        "cost": cost,
        "run_events": [r for r in run if r["event"] not in ("start", "end")],
    }
    print(json.dumps({k: summary[k] for k in ("duration_min", "api_requests", "coverage", "lag_push_finished_s",
                                              "lag_poll_dropped_s", "distinct_credential_groups")}, indent=1,
                     default=str))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=1, default=str)
        with open(os.path.join(out_dir, "status_frames.jsonl"), "w", encoding="utf-8") as f:
            for fr in frames:
                row = {k: fr.get(k) for k in ("ts", "tab", "conn", "subject", "id", "status_code", "status_type",
                                               "change_ts", "winnerCode")}
                f.write(json.dumps(row, separators=(",", ":")) + "\n")
        with open(os.path.join(out_dir, "transitions.csv"), "w", encoding="utf-8", newline="") as f:
            cols = ["sport", "id", "kind", "from_code", "code", "ut", "from_ts", "seen_ts", "push", "push_ts",
                    "push_before_poll_s", "tab_connected"]
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
            w.writeheader()
            for t in trs:
                w.writerow({k: (round(v, 3) if isinstance(v, float) else v) for k, v in t.items()})


if __name__ == "__main__":
    main()
