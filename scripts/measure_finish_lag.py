#!/usr/bin/env python3
"""
Maçın gerçekten bitmesi ile SofaScore'un "finished" demesi arasındaki gecikmeyi ölçer.

Her turda dört kaynak sorgulanır ve her gözlem research/finish_lag/{tarih}_{spor}.jsonl'a yazılır:
  event        /event/{id}
  live         /sport/{sport}/events/live                  (tur başına 1 istek)
  season_list  scraper'ın kendi liste yolu: tur bazlı liglerde
               /unique-tournament/{ut}/season/{s}/events/round/{r}[/slug/..], diğerlerinde
               .../events/last/0 — seçim src/match_fetcher.py ile aynı (lig/tur başına 1 istek)
  scheduled    /unique-tournament/{ut}/scheduled-events/{tarih} (lig/gün başına 1 istek).
               Talimattaki /sport/{sport}/scheduled-events/{tarih} artık 404 döndürüyor;
               sitenin kendisi tarih listesi için scheduled-tournaments + turnuva bazlı
               scheduled-events kullanıyor. Başlangıçta eski yol bir kez denenip sonucu özete yazılır.

Bitince her maç için özet: stdout + research/finish_lag/{tarih}_{spor}_summary.json.

Örnek:
  python scripts/measure_finish_lag.py --sport football --league-id 17 --date 2026-10-03
  python scripts/measure_finish_lag.py --sport tennis --event-ids 123,456 --interval 60 --until 6
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import signal
import statistics
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _research_common import ROOT, SPORTS, Client, cache_headers, utc_now, write_json  # noqa: E402

from src.match_fetcher import MatchFetcher  # noqa: E402

OUT_DIR = os.path.join(ROOT, "research", "finish_lag")
SCORE_KEYS = ("current", "display", "normaltime", "period1", "period2", "period3", "period4", "period5",
              "overtime", "extra1", "extra2", "penalties", "aggregated")
# Hepsi bitince skor düzeltmesi görmek için bu kadar daha izlenir
POST_FINISH_WATCH_S = 30 * 60


def interval_arg(v: str) -> int:
    n = int(v)
    if n < 30:
        raise argparse.ArgumentTypeError("--interval en az 30 sn olmalı (hız sınırı)")
    return n


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sport", choices=SPORTS, help="zorunlu (--summarize hariç)")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--event-ids", help="virgülle ayrılmış event id'leri")
    src.add_argument("--summarize", metavar="JSONL", help="ağa çıkmadan, kayıtlı gözlemlerden özeti yeniden üret")
    src.add_argument("--league-id", type=int, help="unique-tournament id (--date ile)")
    p.add_argument("--date", help="YYYY-MM-DD (--league-id ile; UTC gün)")
    p.add_argument("--max-events", type=int, default=20, help="--league-id ile en fazla kaç maç (varsayılan 20)")
    p.add_argument("--interval", type=interval_arg, default=60, help="tur aralığı, sn (en az 30, varsayılan 60)")
    p.add_argument("--until", type=float, default=6.0, help="en fazla kaç saat izlenecek (varsayılan 6)")
    a = p.parse_args(argv)
    if a.summarize:
        return a
    if not a.sport:
        p.error("--sport gerekli")
    if a.league_id and not a.date:
        p.error("--league-id için --date gerekli")
    return a


def score_of(side: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    side = side or {}
    return {k: side[k] for k in SCORE_KEYS if k in side} | {k: v for k, v in side.items() if k.endswith("TieBreak")}


def observation(ts, eid, source, ev, resp, present) -> Dict[str, Any]:
    st = (ev or {}).get("status") or {}
    return {
        # ts_utc: turun başlangıcı; fetched_at_utc: bu yanıtın geldiği an (gecikmeler bununla hesaplanır)
        "ts_utc": ts, "fetched_at_utc": utc_now(), "event_id": eid, "source": source,
        "status_type": st.get("type"), "status_code": st.get("code"), "status_desc": st.get("description"),
        "present_in_source": present, "http_status": resp.get("status"),
        "score_json": {"home": score_of((ev or {}).get("homeScore")), "away": score_of((ev or {}).get("awayScore"))} if ev else None,
        "change_ts": ((ev or {}).get("changes") or {}).get("changeTimestamp"),
        "changed_fields": ((ev or {}).get("changes") or {}).get("changes"),
        # Maç saati (periyot başlangıcı, uzatma dakikaları vb.): beklenen bitiş anı buradan hesaplanır
        "time": (ev or {}).get("time") if source == "event" else None,
        "start_ts": (ev or {}).get("startTimestamp"),
        "cache_headers_json": cache_headers(resp),
    }


def find_events_for_league(c: Client, league_id: int, date: str, limit: int) -> List[int]:
    r = c.get(f"/unique-tournament/{league_id}/scheduled-events/{date}")
    events = (r.get("body") or {}).get("events") or []
    if not events:  # talimattaki yol: events/last + events/next, tarihe göre süz
        seasons = ((c.get(f"/unique-tournament/{league_id}/seasons").get("body") or {}).get("seasons") or [])
        if seasons:
            sid = seasons[0]["id"]
            for kind in ("last", "next"):
                events += (c.get(f"/unique-tournament/{league_id}/season/{sid}/events/{kind}/0").get("body") or {}).get("events") or []
    day = dt.date.fromisoformat(date)
    ids = [e["id"] for e in events
           if dt.datetime.fromtimestamp(e.get("startTimestamp", 0), dt.timezone.utc).date() == day]
    return list(dict.fromkeys(ids))[:limit]


def season_list_path(c: Client, meta: Dict[str, Any], rounds_cache: Dict) -> Optional[str]:
    """Scraper'ın bu maç için kullanacağı liste yolu (src/match_fetcher.py ile aynı karar)."""
    ut, sid = meta.get("ut"), meta.get("season")
    if not ut or not sid:
        return None
    key = (ut, sid)
    if key not in rounds_cache:
        rounds_cache[key] = ((c.get(f"/unique-tournament/{ut}/season/{sid}/rounds").get("body") or {}).get("rounds") or [])
    rounds = rounds_cache[key]
    rnd = meta.get("round")
    if MatchFetcher.is_week_based_rounds(rounds) and isinstance(rnd, int):
        return MatchFetcher.build_round_events_url(ut, sid, rnd, meta.get("round_slug"))
    return f"/unique-tournament/{ut}/season/{sid}/events/last/0"


def summarize_only(path: str) -> int:
    ids, meta = [], {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["event_id"] not in meta:
                ids.append(r["event_id"])
                meta[r["event_id"]] = {"kickoff": None, "season_list": None}
            if r["source"] == "event" and r.get("start_ts"):
                meta[r["event_id"]]["kickoff"] = r["start_ts"]
    summary = summarize(path, ids, meta)
    summary["notes"] = {"summarized_from": os.path.relpath(path, ROOT), "summarized_at_utc": utc_now()}
    write_json(path.replace(".jsonl", "_summary.json"), summary)
    print_table(summary)
    return 0


_stop = False


def _request_stop(signum, frame):
    global _stop
    _stop = True
    print("Durdurma sinyali alındı; tur bitince özet yazılacak.", flush=True)


def main(argv=None) -> int:
    a = parse_args(argv)
    if a.summarize:
        return summarize_only(a.summarize)
    signal.signal(signal.SIGTERM, _request_stop)
    c = Client()
    started = time.monotonic()
    notes: Dict[str, Any] = {}
    try:
        today = a.date or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        legacy = c.get(f"/sport/{a.sport}/scheduled-events/{today}")
        notes["legacy_scheduled_events_status"] = legacy.get("status")

        ids = [int(x) for x in a.event_ids.split(",") if x.strip()] if a.event_ids else \
            find_events_for_league(c, a.league_id, a.date, a.max_events)
        if not ids:
            print("İzlenecek maç bulunamadı.", file=sys.stderr)
            return 1

        meta: Dict[int, Dict[str, Any]] = {}
        for eid in ids:
            ev = (c.get(f"/event/{eid}").get("body") or {}).get("event") or {}
            ri = ev.get("roundInfo") or {}
            meta[eid] = {
                "ut": ((ev.get("tournament") or {}).get("uniqueTournament") or {}).get("id"),
                "season": (ev.get("season") or {}).get("id"),
                "round": ri.get("round"),
                "round_slug": ri.get("slug"),
                "kickoff": ev.get("startTimestamp"),
                "date": dt.datetime.fromtimestamp(ev.get("startTimestamp", 0), dt.timezone.utc).strftime("%Y-%m-%d"),
            }
        rounds_cache: Dict = {}
        for m in meta.values():
            m["season_list"] = season_list_path(c, m, rounds_cache)

        run_date = a.date or min(m["date"] for m in meta.values())
        os.makedirs(OUT_DIR, exist_ok=True)
        jsonl = os.path.join(OUT_DIR, f"{run_date}_{a.sport}.jsonl")
        print(f"{len(ids)} maç izleniyor → {jsonl}", flush=True)

        finished_at: Optional[float] = None
        turns = 0
        deadline = started + a.until * 3600
        with open(jsonl, "a", encoding="utf-8") as out:
            while True:
                t0 = time.monotonic()
                ts = utc_now()
                rows: List[Dict[str, Any]] = []

                def record(eid, source, ev, resp, present, _rows=rows, _ts=ts):
                    _rows.append(observation(_ts, eid, source, ev, resp, present))

                for eid in ids:
                    r = c.get(f"/event/{eid}")
                    record(eid, "event", (r.get("body") or {}).get("event"), r, r.get("status") == 200)

                def list_source(name: str, path: Optional[str], members: List[int], record=record):
                    if not path:
                        for eid in members:
                            record(eid, name, None, {}, False)
                        return
                    r = c.get(path)
                    by_id = {e.get("id"): e for e in (r.get("body") or {}).get("events") or []}
                    for eid in members:
                        record(eid, name, by_id.get(eid), r, eid in by_id)

                list_source("live", f"/sport/{a.sport}/events/live", ids)
                for path in sorted({m["season_list"] for m in meta.values() if m["season_list"]}):
                    list_source("season_list", path, [e for e, m in meta.items() if m["season_list"] == path])
                for key in sorted({(m["ut"], m["date"]) for m in meta.values() if m["ut"]}):
                    list_source("scheduled", f"/unique-tournament/{key[0]}/scheduled-events/{key[1]}",
                                [e for e, m in meta.items() if (m["ut"], m["date"]) == key])

                for row in rows:
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()

                ev_rows = [x for x in rows if x["source"] == "event"]
                done = sum(1 for x in ev_rows if x["status_type"] == "finished")
                live = sum(1 for x in ev_rows if x["status_type"] == "inprogress")
                print(f"{ts} tur: {len(ev_rows)} maç, {live} canlı, {done} bitti; toplam istek {c.requests}", flush=True)

                terminal = all(x["status_type"] in ("finished", "canceled", "postponed") for x in ev_rows)
                if terminal and turns == 0:
                    notes["stopped_early"] = "ilk turda tüm maçlar zaten bitmişti; ölçülecek geçiş yok, tek tur yazıldı"
                    break
                turns += 1
                if terminal and finished_at is None:
                    finished_at = time.monotonic()
                if time.monotonic() >= deadline:
                    break
                if _stop:
                    notes["stopped_early"] = "SIGTERM ile durduruldu"
                    break
                if finished_at is not None and time.monotonic() - finished_at >= POST_FINISH_WATCH_S:
                    notes["stopped_early"] = "tüm maçlar bitti; skor düzeltmesi için 30 dk daha izlendi"
                    break
                wait_until = t0 + a.interval
                while not _stop and time.monotonic() < wait_until:
                    time.sleep(1)

        summary = summarize(jsonl, ids, meta)
        summary["notes"] = notes | {
            "requests_total": c.requests, "requests_by_status": c.by_status,
            "duration_s": round(time.monotonic() - started), "interval_s": a.interval,
        }
        write_json(os.path.join(OUT_DIR, f"{run_date}_{a.sport}_summary.json"), summary)
        print_table(summary)
        return 0
    finally:
        c.close()


def _ts(s: Optional[str]) -> Optional[float]:
    return dt.datetime.fromisoformat(s).timestamp() if s else None


def summarize(jsonl: str, ids: List[int], meta: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    obs: Dict[int, List[Dict[str, Any]]] = {e: [] for e in ids}
    with open(jsonl, encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row["event_id"] in obs:
                obs[row["event_id"]].append(row)

    def at(r):
        return r.get("fetched_at_utc") or r["ts_utc"]

    def first(rows, pred):
        return next((at(r) for r in rows if pred(r)), None)

    def diff(a_ts, b_ts):
        a, b = _ts(a_ts), _ts(b_ts)
        return round(a - b) if a is not None and b is not None else None

    events = []
    for eid in ids:
        rows = sorted(obs[eid], key=at)
        ev = [r for r in rows if r["source"] == "event"]
        live = [r for r in rows if r["source"] == "live"]
        inprog = [at(r) for r in ev if r["status_type"] == "inprogress"]
        last_inprogress = inprog[-1] if inprog else None
        first_finished = first(ev, lambda r: r["status_type"] == "finished")
        seen_live = [r for r in live if r["present_in_source"]]
        dropped = first([r for r in live if seen_live and at(r) > at(seen_live[0])], lambda r: not r["present_in_source"])
        ff_season = first([r for r in rows if r["source"] == "season_list"], lambda r: r["status_type"] == "finished")
        ff_sched = first([r for r in rows if r["source"] == "scheduled"], lambda r: r["status_type"] == "finished")
        finished_scores = [json.dumps(r["score_json"], sort_keys=True) for r in ev if r["status_type"] == "finished"]

        # Geçişin gerçek anı: ilk "finished" gözlemindeki changes.changeTimestamp. Geçerli sayılması için
        # son "inprogress" gözlemi ile ilk "finished" gözlemi arasında kalmalı. Eski kayıtlarda gözleme
        # turun başlangıç zamanı yazıldığından change_ts ilk "finished" gözleminden sonra görünebilir:
        # bu durum "belirsiz" işaretlenir (artefakt), gecikmeler hesaplanmaz.
        ff_row = next((r for r in ev if r["status_type"] == "finished"), None)
        transition = ff_row.get("change_ts") if ff_row else None
        status_fields = ff_row.get("changed_fields") if ff_row else None
        if transition is None:
            transition_state = None
        elif status_fields is not None and not any(f.startswith("status.") for f in status_fields):
            transition_state = "son_degisiklik_status_degil"
        elif last_inprogress and _ts(last_inprogress) <= transition <= _ts(first_finished):
            transition_state = "gecerli"
        else:
            transition_state = "belirsiz"
        tr = transition if transition_state == "gecerli" else None

        def since_transition(ts_iso, _tr=tr):
            return round(_ts(ts_iso) - _tr) if _tr is not None and ts_iso else None

        # Basketbol: son düdük = "finished"tan önceki son saat durması (oynanan süre normal süreye ulaşmışsa)
        whistle = None
        for r in ev:
            if first_finished and at(r) > first_finished:
                break
            t = r.get("time") or {}
            regulation = (t.get("periodLength") or 0) * (t.get("totalPeriodCount") or 0)
            if t.get("clockRunning") is False and regulation and (t.get("played") or 0) >= regulation and t.get("clockRunningLastUpdated"):
                whistle = t["clockRunningLastUpdated"]
        max_age = {}
        for r in rows:
            age = (r.get("cache_headers_json") or {}).get("age")
            if age is not None and str(age).isdigit():
                max_age[r["source"]] = max(max_age.get(r["source"], 0), int(age))
        events.append({
            "event_id": eid,
            "kickoff": meta[eid].get("kickoff"),
            "season_list_path": meta[eid].get("season_list"),
            "last_inprogress_seen": last_inprogress,
            "first_finished_seen": first_finished,
            "dropped_from_live": dropped,
            "first_finished_seen_season_list": ff_season,
            "first_finished_seen_scheduled": ff_sched,
            "transition_ts": transition,
            "transition_state": transition_state,
            "polling_lag": since_transition(first_finished),
            "lag_event": diff(first_finished, last_inprogress),
            "lag_live": since_transition(dropped),
            "lag_season_list": since_transition(ff_season),
            "lag_scheduled": since_transition(ff_sched),
            "expected_ft": whistle,
            "lag_whistle": round(tr - _ts(whistle)) if tr is not None and whistle else None,
            # Tur zamanına göre (eski tanım; karşılaştırma için)
            "lag_live_turn": diff(dropped, last_inprogress),
            "lag_season_list_turn": diff(ff_season, first_finished),
            "lag_scheduled_turn": diff(ff_sched, first_finished),
            "max_age_header_seen": max_age,
            "cache_control_seen": sorted({(r.get("cache_headers_json") or {}).get("cache-control") or "-" for r in rows}),
            "score_changed_after_finished": len(set(finished_scores)) > 1,
            "status_code_at_finish": next((r["status_code"] for r in ev if r["status_type"] == "finished"), None),
            "full_lifecycle": bool(last_inprogress and first_finished),
            "final_status": (ev[-1]["status_type"], ev[-1]["status_code"], ev[-1]["status_desc"]) if ev else None,
        })

    def stats(key):
        vals = [e[key] for e in events if e[key] is not None]
        return {"n": len(vals), "median": statistics.median(vals) if vals else None, "max": max(vals) if vals else None}

    return {
        "events": events,
        "aggregate": {k: stats(k) for k in ("polling_lag", "lag_event", "lag_live", "lag_season_list", "lag_scheduled",
                                            "lag_whistle", "lag_live_turn", "lag_season_list_turn", "lag_scheduled_turn")}
        | {"full_lifecycle": sum(e["full_lifecycle"] for e in events),
           "transition_state": dict(collections.Counter(e["transition_state"] for e in events)),
           "score_changed_after_finished": sum(e["score_changed_after_finished"] for e in events)},
    }


def print_table(summary: Dict[str, Any]) -> None:
    cols = ("event_id", "transition_state", "polling_lag", "lag_event", "lag_live", "lag_season_list", "lag_scheduled",
            "lag_whistle", "status_code_at_finish", "score_changed_after_finished")
    print("\t".join(cols))
    for e in summary["events"]:
        print("\t".join(str(e[c]) for c in cols))
    print("aggregate:", json.dumps(summary["aggregate"]))
    print("notes:", json.dumps(summary["notes"], ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
