#!/usr/bin/env python3
"""
SofaScore `status` taksonomisi keşfi (futbol, basketbol, tenis).

Alt komutlar (her biri yeniden çalıştırılabilir; çıktılar yalnızca data/status_samples/ ve docs/):

  scan           A1 — /sport/{sport}/scheduled-tournaments/{gün}/page/N ile günün turnuvaları,
                 /unique-tournament/{ut}/scheduled-events/{gün} ile maçları; son 14 gün + gelecek 7 gün.
                 Her maçın özeti data/status_samples/_index/{sport}.jsonl'a eklenir.
                 (Talimattaki /sport/{sport}/scheduled-events/{gün} artık 404; bkz. docs/status-matrix.)
  live-snapshot  A1 — /sport/{sport}/events/live; görülen her yeni (spor, üçlü) için /event/{id} örneği.
  samples        A2 + B/F/K/T — her üçlü için ham /event/{id} örneği (farklı ligden ikinci örnek) ve
                 edge case kuralları; data/status_samples/{sport}/{case_id}__{event_id}.json
  headers        C — 10'ar maç: /event/{id} ile aynı dakikada turnuva listesi; durum ve önbellek başlıkları.
  report         docs/status-matrix/{sport}-triples.csv ve data/status_samples/_report.json

Tüm istekler scripts/_research_common.py üzerinden: en fazla 1 istek/sn, devre kesici açık.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import glob
import json
import os
import re
import sys
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _research_common import ROOT, SPORTS, Client, cache_headers, status_triple, utc_now, write_json  # noqa: E402

SAMPLES = os.path.join(ROOT, "data", "status_samples")
INDEX = os.path.join(SAMPLES, "_index")
DOCS = os.path.join(ROOT, "docs", "status-matrix")
REQUEST_LOG = os.path.join(SAMPLES, "_requests.jsonl")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "status")
FIXTURE_KEYS = ("status", "winnerCode", "aggregatedWinnerCode", "homeScore", "awayScore", "startTimestamp", "time", "changes")


# ---------------------------------------------------------------- index

def index_row(sport: str, ev: Dict[str, Any], source: str) -> Dict[str, Any]:
    t = ev.get("tournament") or {}
    ut = t.get("uniqueTournament") or {}
    typ, code, desc = status_triple(ev)
    return {
        "sport": sport, "event_id": ev.get("id"), "source": source, "seen_at_utc": utc_now(),
        "ut": ut.get("id"), "ut_name": ut.get("name"), "tournament": t.get("name"),
        "category": (t.get("category") or {}).get("name"),
        "start_ts": ev.get("startTimestamp"),
        "type": typ, "code": code, "desc": desc,
        "winnerCode": ev.get("winnerCode"), "aggregatedWinnerCode": ev.get("aggregatedWinnerCode"),
        "homeScore": ev.get("homeScore"), "awayScore": ev.get("awayScore"),
        "changes": ev.get("changes"),
        "home": (ev.get("homeTeam") or {}).get("name"), "away": (ev.get("awayTeam") or {}).get("name"),
        "doubles": bool((ev.get("homeTeam") or {}).get("subTeams")),
    }


def append_index(sport: str, rows: Iterable[Dict[str, Any]]) -> int:
    os.makedirs(INDEX, exist_ok=True)
    n = 0
    with open(os.path.join(INDEX, f"{sport}.jsonl"), "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def load_index(sport: str) -> List[Dict[str, Any]]:
    path = os.path.join(INDEX, f"{sport}.jsonl")
    if not os.path.exists(path):
        return []
    latest: Dict[Tuple[int, str], Dict[str, Any]] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            latest[(r["event_id"], r["source"])] = r
    return list(latest.values())


def log_requests(cmd: str, c: Client, started: float, extra: Optional[Dict[str, Any]] = None) -> None:
    os.makedirs(SAMPLES, exist_ok=True)
    row = {"cmd": cmd, "finished_at_utc": utc_now(), "requests": c.requests, "by_status": c.by_status,
           "duration_s": round(time.monotonic() - started)} | (extra or {})
    with open(REQUEST_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"[{cmd}] {c.requests} istek, {row['duration_s']} sn", flush=True)


# ---------------------------------------------------------------- A1: scan

def cmd_scan(a) -> None:
    c = Client()
    started = time.monotonic()
    today = dt.datetime.now(dt.timezone.utc).date()
    days = [today + dt.timedelta(days=d) for d in range(-a.days_back, a.days_forward + 1)]
    try:
        for sport in a.sports:
            done = {r["source"][5:] for r in load_index(sport) if r["source"].startswith("scan:")}
            for day in days:
                ds = day.isoformat()
                if ds in done and not a.rescan:
                    print(f"{sport} {ds}: zaten taranmış, atlanıyor (--rescan ile yeniden)", flush=True)
                    continue
                tours: List[Dict[str, Any]] = []
                for page in range(1, 30):
                    b = c.get(f"/sport/{sport}/scheduled-tournaments/{ds}/page/{page}").get("body") or {}
                    tours += b.get("scheduled") or []
                    if not b.get("hasNextPage"):
                        break
                uts: Dict[int, Tuple[int, int]] = {}
                for t in tours:
                    ut = (t.get("tournament") or {}).get("uniqueTournament") or {}
                    if ut.get("id"):
                        prio = ((t.get("tournament") or {}).get("category") or {}).get("priority") or 0
                        # timezoneEventCount: {saat dilimi: maç sayısı}; o gün için en büyük değer
                        tz = t.get("timezoneEventCount") or {}
                        n = max(tz.values()) if isinstance(tz, dict) and tz else (tz if isinstance(tz, int) else 0)
                        uts[ut["id"]] = (prio, uts.get(ut["id"], (0, 0))[1] + n)
                # Öncelik: kategori önceliği, sonra maç sayısı. Hepsi taranamıyorsa ilk N.
                chosen = sorted(uts, key=lambda k: uts[k], reverse=True)[: a.max_tournaments]
                rows = []
                for utid in chosen:
                    evs = (c.get(f"/unique-tournament/{utid}/scheduled-events/{ds}").get("body") or {}).get("events") or []
                    rows += [index_row(sport, e, f"scan:{ds}") for e in evs]
                n = append_index(sport, rows)
                print(f"{sport} {ds}: {len(uts)} turnuva, {len(chosen)} tarandı, {n} maç; toplam istek {c.requests}", flush=True)
    finally:
        log_requests("scan", c, started, {"sports": a.sports, "days": [days[0].isoformat(), days[-1].isoformat()],
                                          "max_tournaments": a.max_tournaments})
        c.close()


# ---------------------------------------------------------------- A1: live snapshots

def save_sample(sport: str, case_id: str, event_id: int, resp: Dict[str, Any], note: str = "") -> Optional[str]:
    body = resp.get("body") or {}
    ev = body.get("event")
    path = os.path.join(SAMPLES, sport, f"{case_id}__{event_id}.json")
    write_json(path, {
        "case_id": case_id, "event_id": event_id, "fetched_at_utc": utc_now(),
        "source_endpoint": f"/api/v1/event/{event_id}", "http_status": resp.get("status"),
        "cache_headers": cache_headers(resp), "note": note,
        "event": ev, "error": None if ev else body or resp.get("text"),
    })
    return os.path.relpath(path, ROOT)


def triple_id(t: Tuple[Any, Any, Any]) -> str:
    return "A_" + re.sub(r"[^a-z0-9]+", "-", f"{t[0]}_{t[1]}_{t[2]}".lower()).strip("-")


def existing_sample(sport: str, case_id: str) -> List[str]:
    return glob.glob(os.path.join(SAMPLES, sport, f"{case_id}__*.json"))


def cmd_live_snapshot(a) -> None:
    c = Client()
    started = time.monotonic()
    snap = utc_now()
    try:
        for sport in a.sports:
            r = c.get(f"/sport/{sport}/events/live")
            evs = (r.get("body") or {}).get("events") or []
            append_index(sport, [index_row(sport, e, f"live:{snap}") for e in evs])
            triples = collections.Counter(status_triple(e) for e in evs)
            print(f"{sport} canlı: {len(evs)} maç, {len(triples)} üçlü: {dict(triples)}", flush=True)
            # Görülen her üçlü için en fazla 2 örnek (farklı lig) — canlı durumlar ancak şimdi yakalanır
            for trip in triples:
                have = existing_sample(sport, triple_id(trip))
                leagues_have = {json.load(open(p)).get("event", {}).get("tournament", {}).get("uniqueTournament", {}).get("id") for p in have}
                for e in evs:
                    if len(have) >= 2:
                        break
                    ut = ((e.get("tournament") or {}).get("uniqueTournament") or {}).get("id")
                    if status_triple(e) == trip and ut not in leagues_have:
                        p = save_sample(sport, triple_id(trip), e["id"], c.get(f"/event/{e['id']}"), note=f"live snapshot {snap}")
                        have.append(p)
                        leagues_have.add(ut)
    finally:
        log_requests("live-snapshot", c, started, {"snapshot_utc": snap})
        c.close()


# ---------------------------------------------------------------- A2 + B/F/K/T: samples

def d(row) -> str:
    return (row.get("desc") or "").lower()


def has_period(row, key) -> bool:
    return any(key in (row.get(side) or {}) for side in ("homeScore", "awayScore"))


def score(row, side, key):
    return (row.get(side) or {}).get(key)


NOW = time.time()
DAY = 86400

COMMON: List[Tuple[str, str, Callable[[Dict[str, Any]], bool]]] = [
    ("B1_future_7d", "Gelecek maç (7+ gün sonra)", lambda r: r["type"] == "notstarted" and (r["start_ts"] or 0) >= NOW + 7 * DAY),
    ("B2_today_not_started", "Bugün, henüz başlamamış", lambda r: r["type"] == "notstarted" and 0 < (r["start_ts"] or 0) - NOW < DAY
        and dt.datetime.fromtimestamp(r["start_ts"], dt.timezone.utc).date() == dt.datetime.now(dt.timezone.utc).date()),
    ("B3_live_first_period", "Canlı, ilk periyot", lambda r: r["type"] == "inprogress" and d(r) in ("1st half", "1st quarter", "1st set", "1st period")),
    ("B4_live_break", "Canlı, ara", lambda r: r["type"] == "inprogress" and any(k in d(r) for k in ("halftime", "pause", "break", "awaiting"))),
    ("B5_live_last_period", "Canlı, son periyot", lambda r: r["type"] == "inprogress" and d(r) in ("2nd half", "4th quarter", "3rd set", "5th set")),
    ("B6_finished_regular", "Normal bitmiş", lambda r: r["type"] == "finished" and d(r) == "ended"),
    ("B7_postponed", "Ertelenmiş", lambda r: r["type"] == "postponed" or "postpon" in d(r)),
    ("B8_canceled", "İptal", lambda r: r["type"] in ("canceled", "cancelled") or "cancel" in d(r)),
    ("B9_interrupted", "Yarıda kalmış: interrupted", lambda r: "interrupt" in d(r) or r["type"] == "interrupted"),
    ("B9_suspended", "Yarıda kalmış: suspended", lambda r: "suspend" in d(r) or r["type"] == "suspended"),
    ("B9_abandoned", "Yarıda kalmış: abandoned", lambda r: "abandon" in d(r)),
    ("B11_walkover_awarded", "Hükmen (walkover / awarded / forfeit)", lambda r: any(k in d(r) for k in ("walkover", "awarded", "forfeit", "w.o"))),
]
SPORT_CASES: Dict[str, List[Tuple[str, str, Callable[[Dict[str, Any]], bool]]]] = {
    "football": [
        ("F1_aet", "Uzatmada bitmiş", lambda r: r["type"] == "finished" and (d(r) in ("aet", "after extra time") or r["code"] == 110)),
        ("F2_penalties", "Penaltılarla bitmiş", lambda r: r["type"] == "finished" and (d(r) in ("ap", "after penalties") or r["code"] == 120)),
        ("F3_live_extra_time", "Canlı uzatma", lambda r: r["type"] == "inprogress" and "extra" in d(r) and "await" not in d(r)),
        ("F4_live_penalties", "Canlı penaltılar", lambda r: r["type"] == "inprogress" and "penalt" in d(r)),
        ("F5_awaiting_extra", "Uzatma öncesi ara", lambda r: r["type"] == "inprogress" and "await" in d(r)),
        ("F6_cup_draw_aggregate", "Kupa: 90 dk berabere, toplamla bitmiş", lambda r: r["type"] == "finished" and d(r) == "ended"
            and r.get("aggregatedWinnerCode") and score(r, "homeScore", "current") == score(r, "awayScore", "current")),
        ("F7_will_continue", "Durmuş, devam edecek", lambda r: "continue" in d(r)),
        ("F8_paused", "Maç içi duraklama", lambda r: r["type"] == "inprogress" and "pause" in d(r) and "half" not in d(r)),
        ("F9_changed_after_finish", "Bitiş sonrası değişiklik", lambda r: r["type"] == "finished"
            and ((r.get("changes") or {}).get("changeTimestamp") or 0) > (r["start_ts"] or 0) + 4 * 3600),
    ],
    "basketball": [
        ("K1_overtime_finished", "Uzatmalı bitmiş", lambda r: r["type"] == "finished" and (has_period(r, "overtime") or "ot" in d(r).split())),
        ("K2_live_overtime", "Canlı uzatma", lambda r: r["type"] == "inprogress" and "overtime" in d(r)),
        ("K3_two_halves", "İki yarı formatı", lambda r: r["type"] == "finished" and has_period(r, "period2") and not has_period(r, "period3")),
        ("K3_four_quarters", "Dört çeyrek formatı", lambda r: r["type"] == "finished" and has_period(r, "period4")),
        ("K4_halftime", "Devre arası", lambda r: r["type"] == "inprogress" and "halftime" in d(r)),
        ("K4_quarter_break", "Çeyrek arası", lambda r: r["type"] == "inprogress" and any(k in d(r) for k in ("pause", "break")) and "half" not in d(r)),
        ("K5_forfeit", "Hükmen", lambda r: any(k in d(r) for k in ("forfeit", "awarded", "walkover"))),
    ],
    "tennis": [
        ("T1_finished", "Normal bitmiş", lambda r: r["type"] == "finished" and d(r) == "ended"),
        ("T2_retired", "Retired", lambda r: "retir" in d(r)),
        ("T3_walkover", "Walkover", lambda r: "walkover" in d(r) or "w.o" in d(r)),
        ("T4_defaulted", "Defaulted", lambda r: "default" in d(r)),
        ("T5_live_tiebreak", "Canlı tie-break", lambda r: r["type"] == "inprogress" and any(
            k.endswith("TieBreak") for side in ("homeScore", "awayScore") for k in (r.get(side) or {}))),
        ("T6_suspended", "Ertesi güne kalmış / askıya alınmış", lambda r: any(k in d(r) for k in ("suspend", "interrupt"))),
        ("T7_doubles", "Çiftler", lambda r: r.get("doubles")),
        ("T8_match_tiebreak", "Maç tie-break (10 puan)", lambda r: r["type"] == "finished" and max(
            score(r, "homeScore", "period3") or 0, score(r, "awayScore", "period3") or 0) >= 10),
        ("T9_challenger_itf", "Challenger / ITF", lambda r: any(k in (r.get("category") or "") + (r.get("tournament") or "")
                                                             for k in ("Challenger", "ITF"))),
        ("T10_bye", "Bye", lambda r: "bye" in ((r.get("home") or "") + " " + (r.get("away") or "")).lower()),
    ],
}


def cmd_samples(a) -> None:
    c = Client()
    started = time.monotonic()
    found: Dict[str, Dict[str, Any]] = {}
    try:
        for sport in a.sports:
            rows = load_index(sport)
            # A2: her üçlü için 1 örnek, farklı ligden 2. örnek
            by_triple: Dict[Tuple, List[Dict[str, Any]]] = collections.defaultdict(list)
            for r in rows:
                by_triple[(r["type"], r["code"], r["desc"])].append(r)
            for trip, rs in by_triple.items():
                cid = triple_id(trip)
                have = existing_sample(sport, cid)
                leagues = []
                for r in rs:
                    if len(have) + len(leagues) >= 2:
                        break
                    if r["ut"] in leagues:
                        continue
                    if have and r["ut"] == json.load(open(have[0])).get("event", {}).get("tournament", {}).get("uniqueTournament", {}).get("id"):
                        continue
                    save_sample(sport, cid, r["event_id"], c.get(f"/event/{r['event_id']}"), note=f"index source {r['source']}")
                    leagues.append(r["ut"])
            # B/F/K/T: kurala uyan ilk 1-2 maç
            for case_id, title, rule in COMMON + SPORT_CASES.get(sport, []):
                matches = [r for r in rows if safe(rule, r)]
                key = f"{sport}:{case_id}"
                if not matches:
                    found[key] = {"found": False, "title": title, "scanned": len(rows)}
                    continue
                have = existing_sample(sport, case_id)
                picked = []
                for r in matches:
                    if len(have) + len(picked) >= 2 or (picked and r["ut"] == picked[0]["ut"]):
                        continue
                    resp = c.get(f"/event/{r['event_id']}")
                    save_sample(sport, case_id, r["event_id"], resp, note=f"{title}; index source {r['source']}")
                    picked.append(r)
                found[key] = {"found": True, "title": title, "candidates": len(matches),
                              "files": [os.path.relpath(p, ROOT) for p in existing_sample(sport, case_id)]}
            # B10/B12: ertelenmiş/iptal maçlar bugün nasıl görünüyor (aynı id? yeni başlangıç? 404?)
            recheck = [r for r in rows if r["type"] in ("postponed", "canceled", "cancelled")][: a.recheck]
            for r in recheck:
                resp = c.get(f"/event/{r['event_id']}")
                ev = (resp.get("body") or {}).get("event") or {}
                if resp.get("status") == 404:
                    save_sample(sport, "B12_404_after_listed", r["event_id"], resp, note=f"listelendiğinde {r['type']} ({r['source']}), şimdi 404")
                elif ev and (ev.get("startTimestamp") != r["start_ts"] or status_triple(ev)[0] != r["type"]):
                    save_sample(sport, "B10_rescheduled_same_id", r["event_id"], resp,
                                note=f"listede {r['type']} start={r['start_ts']}; şimdi {status_triple(ev)} start={ev.get('startTimestamp')}")
            write_json(os.path.join(SAMPLES, f"_cases_{sport}.json"), {k: v for k, v in found.items() if k.startswith(sport)})
    finally:
        log_requests("samples", c, started)
        c.close()


def safe(rule, r) -> bool:
    try:
        return bool(rule(r))
    except Exception:
        return False


# ---------------------------------------------------------------- C: headers / consistency

def cmd_headers(a) -> None:
    c = Client()
    started = time.monotonic()
    out = []
    try:
        for sport in a.sports:
            live = (c.get(f"/sport/{sport}/events/live").get("body") or {}).get("events") or []
            day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
            for e in live[: a.per_sport]:
                ut = ((e.get("tournament") or {}).get("uniqueTournament") or {}).get("id")
                ev_resp = c.get(f"/event/{e['id']}")
                lst = c.get(f"/unique-tournament/{ut}/scheduled-events/{day}") if ut else {}
                in_list = next((x for x in (lst.get("body") or {}).get("events") or [] if x.get("id") == e["id"]), None)
                ev = (ev_resp.get("body") or {}).get("event") or {}
                out.append({
                    "sport": sport, "event_id": e["id"], "checked_at_utc": utc_now(),
                    "event_status": status_triple(ev), "list_status": status_triple(in_list) if in_list else None,
                    "live_status": status_triple(e),
                    "event_score": [(ev.get("homeScore") or {}).get("current"), (ev.get("awayScore") or {}).get("current")],
                    "list_score": [((in_list or {}).get("homeScore") or {}).get("current"), ((in_list or {}).get("awayScore") or {}).get("current")],
                    "event_headers": cache_headers(ev_resp), "list_headers": cache_headers(lst) if lst else None,
                    "list_endpoint": f"/unique-tournament/{ut}/scheduled-events/{day}",
                })
        path = os.path.join(SAMPLES, "_consistency", f"{utc_now().replace(':', '')}.json")
        write_json(path, out)
        diff = sum(1 for x in out if x["list_status"] and x["list_status"] != x["event_status"])
        print(f"{len(out)} maç; event≠liste durum farkı: {diff}; → {os.path.relpath(path, ROOT)}", flush=True)
    finally:
        log_requests("headers", c, started)
        c.close()


# ---------------------------------------------------------------- report

def cmd_report(a) -> None:
    os.makedirs(DOCS, exist_ok=True)
    report: Dict[str, Any] = {}
    for sport in SPORTS:
        rows = load_index(sport)
        counter: Dict[Tuple, List[Dict[str, Any]]] = collections.defaultdict(list)
        for r in rows:
            counter[(r["type"], r["code"], r["desc"])].append(r)
        with open(os.path.join(DOCS, f"{sport}-triples.csv"), "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["type", "code", "description", "count", "first_example_event_id", "sample_file"])
            for trip, rs in sorted(counter.items(), key=lambda kv: (-len(kv[1]), str(kv[0]))):
                files = existing_sample(sport, triple_id(trip))
                w.writerow([trip[0], trip[1], trip[2], len(rs), rs[0]["event_id"],
                            os.path.relpath(files[0], ROOT) if files else ""])
        report[sport] = {"events_indexed": len({r["event_id"] for r in rows}), "triples": len(counter)}
        # Talimat 01 için kırpılmış fixture'lar
        for p in glob.glob(os.path.join(SAMPLES, sport, "*.json")):
            s = json.load(open(p, encoding="utf-8"))
            ev = s.get("event")
            if not ev:
                continue
            fx = {"case_id": s["case_id"], "event_id": s["event_id"], "fetched_at_utc": s["fetched_at_utc"],
                  "source_file": os.path.relpath(p, ROOT)} | {k: ev[k] for k in FIXTURE_KEYS if k in ev}
            write_json(os.path.join(FIXTURES, sport, os.path.basename(p)), fx)
    requests = []
    if os.path.exists(REQUEST_LOG):
        requests = [json.loads(line) for line in open(REQUEST_LOG, encoding="utf-8")]
    report["requests_total"] = sum(r["requests"] for r in requests)
    report["duration_s_total"] = sum(r["duration_s"] for r in requests)
    write_json(os.path.join(SAMPLES, "_report.json"), report)
    print(json.dumps(report, indent=1))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("scan", "live-snapshot", "samples", "headers", "report"):
        sp = sub.add_parser(name)
        sp.add_argument("--sports", nargs="+", choices=SPORTS, default=list(SPORTS))
        if name == "scan":
            sp.add_argument("--days-back", type=int, default=14)
            sp.add_argument("--days-forward", type=int, default=7)
            sp.add_argument("--max-tournaments", type=int, default=60, help="gün başına en fazla kaç turnuva (varsayılan 60)")
            sp.add_argument("--rescan", action="store_true", help="index'te olan günleri de yeniden tara")
        if name == "samples":
            sp.add_argument("--recheck", type=int, default=40, help="B10/B12 için yeniden sorgulanacak ertelenmiş/iptal maç sayısı")
        if name == "headers":
            sp.add_argument("--per-sport", type=int, default=10)
    a = p.parse_args(argv)
    {"scan": cmd_scan, "live-snapshot": cmd_live_snapshot, "samples": cmd_samples,
     "headers": cmd_headers, "report": cmd_report}[a.cmd](a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
