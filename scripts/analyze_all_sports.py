"""
Talimat 05: research/all_sports/ kayıtlarının analizi (ağ isteği yok).

    python scripts/analyze_all_sports.py patterns     # pattern özeti, repoda olmayanlar
    python scripts/analyze_all_sports.py all          # docs/all-sports/ CSV'leri + statü/skor kontrolleri

Repo karşılaştırması: src/ ve araştırma script'lerindeki API yolu dizgileri (f-string yer tutucuları
herhangi bir yol parçasıyla eşleşir) gözlenen pattern'lerle eşleştirilir.
"""
from __future__ import annotations

import collections
import csv
import glob
import json
import os
import re
import shutil
import sys
from typing import Any, Dict, List, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from _all_sports_patterns import api_pattern  # noqa: E402
OUT = os.path.join(ROOT, "research", "all_sports")
DOCS = os.path.join(ROOT, "docs", "all-sports")
FULL_SAMPLE_BYTES = 20_000  # explore_all_sports.py ile aynı: altıysa tam, değilse kırpılmış

_PATH_LITERAL = re.compile(
    r"[\"'](?:\{self\.base_url\}|https://www\.sofascore\.com/api/v1)?"
    r"(/(?:event|sport|unique-tournament|team|player|tournament|season|search|config|category|stage|odds|manager|referee)"
    r"[^\"']*)[\"']"
)


def repo_patterns() -> Dict[str, List[Tuple[str, re.Pattern]]]:
    """{'src': [(literal, regex)], 'research': [...]}: repodaki API yolları."""
    groups = {
        "src": glob.glob(os.path.join(ROOT, "src", "**", "*.py"), recursive=True),
        "research": [
            os.path.join(ROOT, "scripts", n)
            for n in ("discover_status_taxonomy.py", "measure_finish_lag.py", "_research_common.py")
        ],
    }
    out: Dict[str, List[Tuple[str, re.Pattern]]] = {}
    for group, files in groups.items():
        seen = {}
        for path in files:
            with open(path, encoding="utf-8") as f:
                for m in _PATH_LITERAL.finditer(f.read()):
                    lit = m.group(1).split("?")[0]
                    if "{" in lit and lit.count("{") != lit.count("}"):
                        continue
                    # Yer tutucu: normalleştirilmiş parça (X) ya da tire içermeyen düz kelime (last/next/round)
                    rx = "^" + re.sub(r"\\\{[^}]*\\\}", "(?:X|[a-z]+)", re.escape(lit)) + "$"
                    seen[lit] = re.compile(rx)
        out[group] = sorted(seen.items())
    return out


def is_sofa_pattern(pattern: str) -> bool:
    return pattern.startswith("/") or ".sofascore." in pattern.split("/")[0]


def repo_use(pattern: str, repo: Dict[str, List[Tuple[str, re.Pattern]]]) -> str:
    if not pattern.startswith("/"):
        return "no"
    probe = re.sub(r"\{(id|date|sport|cc)\}", "X", pattern)
    for group in ("src", "research"):
        for lit, rx in repo[group]:
            if rx.match(probe):
                return f"{group}: {lit}"
    return "no"


def load_requests() -> List[Dict[str, Any]]:
    """Pattern'ler kayıttan değil URL'den yeniden hesaplanır (normalleştirme sonradan iyileşti)."""
    with open(os.path.join(OUT, "requests.jsonl"), encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    for r in rows:
        r["pattern"] = api_pattern(r["url"])
    return rows


def cmd_patterns() -> None:
    rows = load_requests()
    repo = repo_patterns()
    sofa = [r for r in rows if is_sofa_pattern(r["pattern"])]
    pats = collections.OrderedDict()
    for r in sofa:
        pats.setdefault(r["pattern"], r)
    missing = [p for p in pats if repo_use(p, repo) == "no"]
    third = {r["pattern"].split("/")[0] for r in rows if not is_sofa_pattern(r["pattern"])}
    print(f"SofaScore istek satırı {len(sofa)}, pattern {len(pats)}, repoda olmayan {len(missing)}; "
          f"üçüncü taraf alan adı {len(third)}")
    for p in pats:
        print(("  NEW " if p in missing else "  rep ") + p, "|", repo_use(p, repo))



# --- olaylar, stage'ler, statü/skor kontrolleri ----------------------------------------------------------

def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:60] or "x"


def load_events() -> Dict[str, Dict[int, Dict[str, Any]]]:
    """{spor: {id: son görülen kompakt olay}}. Spor, sayfanın değil olayın kendi slug'ı (çapraz listeler)."""
    out: Dict[str, Dict[int, Dict[str, Any]]] = collections.defaultdict(dict)
    for path in sorted(glob.glob(os.path.join(OUT, "events", "*.jsonl"))):
        if os.path.basename(path).startswith("_"):  # _stages.jsonl vb.: olay değil
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                e = json.loads(line)
                sport = e.get("sport") or os.path.basename(path)[:-6]
                e["_file"] = os.path.relpath(path, ROOT)
                out[sport][e["id"]] = e
    return out


def _walk(o: Any, fn) -> None:
    if isinstance(o, dict):
        fn(o)
        for v in o.values():
            _walk(v, fn)
    elif isinstance(o, list):
        for v in o:
            _walk(v, fn)


def load_stages() -> Dict[str, Dict[int, Dict[str, Any]]]:
    """Çok yarışmacılı model: status{type,description} + startDateTimestamp + id taşıyan nesneler (stage)."""
    out: Dict[str, Dict[int, Dict[str, Any]]] = collections.defaultdict(dict)
    saved = os.path.join(OUT, "events", "_stages.jsonl")
    if os.path.exists(saved):  # compact sonrası: örnekler azaldı, stage'ler burada
        with open(saved, encoding="utf-8") as f:
            for line in f:
                g = json.loads(line)
                out[g["sport"]][g["id"]] = g
        return out
    for path in sorted(glob.glob(os.path.join(OUT, "samples", "*", "*.json"))):
        with open(path, encoding="utf-8") as f:
            body = json.load(f).get("body")

        def visit(o: Dict[str, Any], path=path) -> None:
            st = o.get("status")
            if isinstance(st, dict) and "startDateTimestamp" in o and "id" in o and "homeScore" not in o:
                cat = ((o.get("uniqueStage") or {}).get("category") or {})
                sport = (cat.get("sport") or {}).get("slug") or os.path.basename(os.path.dirname(path))
                out[sport][o["id"]] = {
                    "id": o["id"], "sport": sport, "status": st, "type": (o.get("type") or {}).get("name"),
                    "name": o.get("name"), "keys": sorted(o.keys()), "_file": os.path.relpath(path, ROOT),
                }

        _walk(body, visit)
    return out


def triple(status: Dict[str, Any]) -> Tuple[str, str, str]:
    return (str(status.get("type", "")), "" if status.get("code") is None else str(status.get("code")),
            str(status.get("description", "")))


class _Capture(__import__("logging").Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: List[str] = []

    def emit(self, record) -> None:
        self.messages.append(record.getMessage())


def run_checks(events, stages):
    """classify_status + extract_scores her olaya (ve classify_status her stage'e) gerçekten çalıştırılır."""
    import logging

    from src import status as st

    cap = _Capture()
    logging.getLogger("src.status").addHandler(cap)
    logging.getLogger("src.status").setLevel(logging.WARNING)
    known_codes = {0} | set(st._COMPLETED_CODES) | set(st._WITHOUT_PLAY_CODES) | set(st._LIVE_CODES) | set(st._VOID_CODES)
    result = {}
    for sport in sorted(set(events) | set(stages)):
        evs = list(events.get(sport, {}).values())
        stg = list(stages.get(sport, {}).values())
        unknown = collections.Counter()
        classes = collections.Counter()
        extract_errors: List[str] = []
        extract_warnings = collections.Counter()
        supported = None
        for e in evs:
            cap.messages.clear()
            c = st.classify_status(e)
            classes[c.value] += 1
            if c is st.StatusClass.UNKNOWN:
                unknown[triple(e["status"] or {})] += 1
            try:
                sheet = st.extract_scores(e, sport)
                supported = type(sheet).__name__ != "ScoreSheet"
            except Exception as ex:  # çıkarım hatası: türü ve olay id'si
                extract_errors.append(f"{e['id']}: {ex.__class__.__name__}: {ex}")
            for m in cap.messages:
                extract_warnings[re.sub(r"\d{5,}", "{id}", m)[:140]] += 1
        stage_classes = collections.Counter()
        for g in stg:
            c = st.classify_status(g)
            stage_classes[c.value] += 1
            if c is st.StatusClass.UNKNOWN:
                unknown[("stage:" + triple(g["status"])[0], triple(g["status"])[1], triple(g["status"])[2])] += 1
        codes = collections.Counter(e["status"].get("code") for e in evs if (e.get("status") or {}).get("code") is not None)
        new_codes = sorted(c for c in codes if c not in known_codes)
        total = len(evs) + len(stg)
        result[sport] = {
            "events": len(evs), "stages": len(stg), "classes": dict(classes), "stage_classes": dict(stage_classes),
            "unknown": sum(unknown.values()), "unknown_rate": round(sum(unknown.values()) / total, 3) if total else None,
            "unknown_triples": {" / ".join(k): v for k, v in unknown.most_common()},
            "codes_seen": sorted(codes), "new_codes": new_codes,
            "known_code_share": round(sum(v for c, v in codes.items() if c in known_codes) / sum(codes.values()), 3) if codes else None,
            "extract_supported": supported, "extract_errors": extract_errors[:10], "extract_error_count": len(extract_errors),
            "extract_warnings": dict(extract_warnings.most_common(8)),
        }
    logging.getLogger("src.status").removeHandler(cap)
    return result


def write_triples(events, stages, checks) -> None:
    from src import status as st

    os.makedirs(os.path.join(DOCS, "status"), exist_ok=True)
    shutil.rmtree(os.path.join(OUT, "status_examples"), ignore_errors=True)  # her çalıştırmada yeniden üretilir
    for sport in sorted(set(events) | set(stages)):
        rows: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = collections.defaultdict(list)
        for e in events.get(sport, {}).values():
            rows[("event",) + triple(e.get("status") or {})].append(e)
        for g in stages.get(sport, {}).values():
            rows[("stage",) + triple(g["status"])].append(g)
        out = []
        for key, items in sorted(rows.items(), key=lambda kv: -len(kv[1])):
            ex = items[0]
            rel = os.path.join("research", "all_sports", "status_examples", sport,
                               f"{key[0]}_{slug(key[1] or 'none')}-{key[2] or 'none'}-{slug(key[3] or 'none')}__{ex['id']}.json")
            os.makedirs(os.path.dirname(os.path.join(ROOT, rel)), exist_ok=True)
            with open(os.path.join(ROOT, rel), "w", encoding="utf-8") as f:
                json.dump({k: v for k, v in ex.items() if not k.startswith("_")} | {"source_file": ex["_file"]},
                          f, ensure_ascii=False, indent=2)
            out.append({
                "model": key[0], "type": key[1], "code": key[2], "description": key[3], "count": len(items),
                "classify_status": st.classify_status(ex).value, "example_id": ex["id"], "sample_file": rel,
            })
        with open(os.path.join(DOCS, "status", f"{sport}-triples.csv"), "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out[0].keys()), lineterminator="\n")
            w.writeheader()
            w.writerows(out)


def score_structure(events) -> Dict[str, Any]:
    """Spor ve turnuva başına homeScore anahtar kümeleri (yalnızca bitmiş olaylar) ve time anahtarları."""
    out = {}
    for sport, evs in sorted(events.items()):
        fin = [e for e in evs.values() if (e.get("status") or {}).get("type") == "finished" and e.get("homeScore")]
        key_freq = collections.Counter(k for e in fin for k in (e.get("homeScore") or {}))
        sets = collections.Counter(tuple(sorted((e.get("homeScore") or {}).keys())) for e in fin)
        by_ut = collections.defaultdict(collections.Counter)
        for e in fin:
            by_ut[(e["unique_tournament"] or {}).get("name") or e.get("tournament")][tuple(sorted(e["homeScore"]))] += 1
        majority = sets.most_common(1)[0][0] if sets else ()
        deviating = {}
        for ut, c in by_ut.items():
            top = c.most_common(1)[0][0]
            if top != majority:
                ex = next(e for e in fin if ((e["unique_tournament"] or {}).get("name") or e.get("tournament")) == ut
                          and tuple(sorted(e["homeScore"])) == top)
                deviating[ut] = {"keys": list(top), "count": sum(c.values()), "example_id": ex["id"], "file": ex["_file"]}
        time_keys = collections.Counter(k for e in evs.values() for k in (e.get("time") or {}))
        top_keys = collections.Counter(k for e in evs.values() for k in e.get("top_keys") or [])
        if not top_keys:  # compact sonrası olay satırlarında top_keys yok: sayım events/_top_keys.json'da
            top_keys = collections.Counter(_saved_top_keys().get(sport, {}))
        out[sport] = {
            "finished": len(fin), "homeScore_key_freq": dict(key_freq.most_common()),
            "majority_keyset": list(majority), "keyset_variants": len(sets),
            "tournaments_deviating": deviating, "time_keys": dict(time_keys.most_common()),
            "defaultPeriodCount": dict(collections.Counter(e.get("defaultPeriodCount") for e in evs.values())),
            "event_top_keys": dict(top_keys.most_common()), "n_events": len(evs),
        }
    return out



# --- uç nokta envanteri ---------------------------------------------------------------------------

# (regex, etiket, gerekçe): ilk eşleşen kazanır. Etiket yararlılığa göre: historical = geçmiş maç/sezon
# verisi; live = maç sürerken değişen veri; settlement = sonucun kesinleşmesi için gereken alan (status,
# skor, winnerCode); none = reklam/bahis/medya/sosyal/yapılandırma.
LABELS: List[Tuple[str, str, str]] = [
    (r"odds|betting|branding|dsp/|offers/|/votes|fantasy|/media|sofascore-news|official-tweets|highlights|"
     r"ai-insights|/tv/|trending|transfer$|newly-added|/config/|/country/|/token/|_next/data|/search/suggestions|"
     r"editors|power-rankings|player-of-the-season|team-of-the-period|sport-video",
     "none", "reklam/bahis/medya/sosyal/yapılandırma; maç verisi değil"),
    (r"^/event/\{id\}$", "settlement", "event{status{type,code}, homeScore, awayScore, winnerCode, changes}: tek maçın kesin durumu"),
    (r"^/sport/\{sport\}/events/live$", "live", "events[] tüm canlı maçlar, status+skor"),
    (r"^/sport/\{id\}/event-count$", "live", "{sport: {live,total}}: spor başına canlı/toplam sayaç"),
    (r"^/sport/\{sport\}/live-tournaments$|live-categories", "live", "canlı turnuva/kategori kimlikleri ve sayıları"),
    (r"/event/\{id\}/(incidents|point-by-point|graph|innings|esports-games|live-match-tracker|shotmap|heatmap|"
     r"average-positions|statistics|lineups|best-players|player-of-the-match|managers|umpires|weather|pregame-form|"
     r"h2h|team-streaks|comments|at-bats)", "historical", "maç detayı; canlı sayfada tekrar çekilen bölümler live sütununda"),
    (r"/stage/", "historical", "çok yarışmacılı model (motor sporu, bisiklet): stage/substage, sıralama"),
    (r"scheduled|finished-upcoming|/events/(last|next|round)|/season/\{id\}/events|team-events|mma-events|"
     r"main-events|calendar|/rounds$|cuptrees", "historical", "tarih/tur/sezon bazlı maç listeleri"),
    (r"standings|/statistics|top-players|top-teams|player-statistics|team-statistics|seasons$|/info$|groups|venues|"
     r"career-statistics|rankings|characteristics|attribute-overviews|performance|goal-distributions|shot-action-areas|"
     r"draft|unique-tournaments|/categories|stage-seasons|races$", "historical", "sezon/takım/oyuncu toplu verisi"),
    (r"^/(team|player|unique-tournament|tournament|category|unique-stage)/\{id\}$", "historical", "varlık meta verisi"),
    (r"^/search/", "historical", "arama: varlık kimliği bulma"),
]


def label_for(pattern: str) -> Tuple[str, str]:
    for rx, lab, why in LABELS:
        if re.search(rx, pattern):
            return lab, why
    return "none", "sınıflandırılmadı; yanıt anahtarlarına bakınız"


def live_repeat(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    """Canlı maç sayfasında (page_type=event-live) aynı pattern'in 200 yanıtları arasındaki medyan aralık (sn)."""
    by = collections.defaultdict(list)
    for r in rows:
        if r.get("page_type") == "event-live" and r.get("status") == 200:
            by[(r["page"], r["pattern"])].append(r["ts"])
    gaps = collections.defaultdict(list)
    for (page, pat), ts in by.items():
        ts = sorted(ts)
        gaps[pat] += [b - a for a, b in zip(ts, ts[1:], strict=False) if b - a > 1.5]
    out = {}
    for pat, g in gaps.items():
        if len(g) >= 2:
            g = sorted(g)
            out[pat] = round(g[len(g) // 2], 1)
    return out


def write_endpoints(rows, repo) -> List[Dict[str, Any]]:
    sofa = [r for r in rows if is_sofa_pattern(r["pattern"])]
    repeat = live_repeat(sofa)
    by = collections.OrderedDict()
    for r in sofa:
        by.setdefault(r["pattern"], []).append(r)
    out = []
    for pat, rs in by.items():
        ok = [r for r in rs if r.get("status") == 200]
        # compact sonrası response_keys pattern başına tek satırda: yalnızca alanı taşıyan satırlar sayılır
        keys = collections.Counter(tuple(r["response_keys"] or []) for r in ok if "response_keys" in r)
        cc = collections.Counter(r.get("cache_control") for r in ok if r.get("cache_control"))
        lab, why = label_for(pat)
        if pat in repeat and pat.startswith("/event/") and lab in ("historical", "settlement"):
            # Canlı maç sayfası bu bölümü tekrar tekrar çekiyor: canlı izleme için de kaynak
            lab = f"{lab}+live"
            why += f"; canlı sayfada ~{repeat[pat]} sn arayla yeniden çekiliyor"
        sample = next((r["sample_file"] for r in rs if r.get("sample_file")), "")
        out.append({
            "pattern": pat,
            "sports": " ".join(sorted({r["sport"] for r in rs})),
            "page_types": " ".join(sorted({r.get("page_type") or "" for r in rs} - {""})),
            "in_repo": repo_use(pat, repo),
            "label": lab,
            "reason": why,
            "response_keys": " ".join(keys.most_common(1)[0][0]) if keys else "",
            "statuses": " ".join(f"{k}:{v}" for k, v in collections.Counter(str(r.get("status")) for r in rs).most_common()),
            "cache_control": cc.most_common(1)[0][0] if cc else "",
            "live_repeat_s": repeat.get(pat, ""),
            "requests": len(rs),
            "example": sample,
        })
    with open(os.path.join(DOCS, "endpoints.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    return out


# --- push kanalı ----------------------------------------------------------------------------------------

def push_analysis() -> Dict[str, Any]:
    """ws.jsonl: konu türleri, status.* taşıyan karelerde (alınma anı − changes.changeTimestamp)."""
    path = os.path.join(OUT, "ws.jsonl")
    rows = [json.loads(line) for line in open(path, encoding="utf-8")] if os.path.exists(path) else []
    subs = collections.Counter()
    lags = collections.defaultdict(list)
    finished = collections.defaultdict(list)
    msgs = collections.Counter()
    # İlk sürüm kareleri ham saklıyordu (text / utf8_preview): MSG'leri oradan da çöz
    legacy = []
    for r in rows:
        raw = r.get("text") or r.get("utf8_preview") or ""
        if r["kind"] == "frame" and raw.startswith("MSG "):
            for m in re.finditer(r"MSG (\S+) \S+ \d+\r\n(\{.*?\})\r\n", raw):
                try:
                    legacy.append({"kind": "recv", "op": "MSG", "subject": m.group(1), "ts": r["ts"],
                                   "body": json.loads(m.group(2))})
                except ValueError:
                    pass
    rows = rows + legacy
    for r in rows:
        if r["kind"] == "sent" and r["line"].startswith("SUB "):
            subs[re.sub(r"\d+", "{id}", r["line"].split(" ")[1])] += 1
        if r["kind"] == "recv" and r.get("op") in ("MSG", "HMSG") and isinstance(r.get("body"), dict):
            b = r["body"]
            msgs[re.sub(r"\d+", "{id}", r["subject"])] += 1
            ct = b.get("changes.changeTimestamp")
            if ct and any(k.startswith("status.") for k in b):
                lag = r["ts"] - ct
                key = r["subject"].split(".")[1] if r["subject"].startswith("sport.") else "event.{id}"
                lags[key].append(lag)
                if b.get("status.type") == "finished":
                    finished[key].append(round(lag, 1))
    counts_path = os.path.join(OUT, "ws_counts.json")
    if os.path.exists(counts_path):  # compact sonrası: ws.jsonl yalnız kanıt kareleri; toplamlar buradan
        with open(counts_path, encoding="utf-8") as f:
            counts = json.load(f)
        subs = collections.Counter(counts["sub_subjects"])
        msgs = collections.Counter(counts["msg_subjects_logged"])

    def dist(v):
        v = sorted(v)
        return {"n": len(v), "median": round(v[len(v) // 2], 1), "max": round(v[-1], 1), "min": round(v[0], 1)} if v else {"n": 0}
    return {
        "sub_subjects": dict(subs.most_common()),
        "msg_subjects_logged": dict(msgs.most_common()),
        "status_lag_s": {k: dist(v) for k, v in lags.items()},
        "finished_lag_s": {k: dist(v) for k, v in finished.items()},
        "finished_frames": sum(len(v) for v in finished.values()),
    }


def event_count_today() -> Dict[str, Dict[str, int]]:
    saved = os.path.join(OUT, "event_count_latest.json")
    if os.path.exists(saved):  # compact sonrası: en son örnek burada
        with open(saved, encoding="utf-8") as f:
            return json.load(f)
    best, ts = {}, ""
    for path in glob.glob(os.path.join(OUT, "samples", "*", "sport-id-event-count__*.json")):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if d["fetched_at_utc"] > ts:
            best, ts = d["body"], d["fetched_at_utc"]
    return {"at_utc": ts, **best}


def cmd_all() -> None:
    os.makedirs(DOCS, exist_ok=True)
    rows = load_requests()
    repo = repo_patterns()
    endpoints = write_endpoints(rows, repo)
    events = load_events()
    stages = load_stages()
    checks = run_checks(events, stages)
    write_triples(events, stages, checks)
    summary = {
        "requests": {
            "sofascore_rows": sum(1 for r in rows if is_sofa_pattern(r["pattern"])),
            # Gönderilen: yanıt alan + sayfanın kendisinin iptal ettiği (ERR_ABORTED); bu script'in gönderilmeden
            # iptal ettikleri (ERR_FAILED / ERR_BLOCKED_BY_CLIENT) sayılmaz
            "sofascore_sent": sum(1 for r in rows if is_sofa_pattern(r["pattern"]) and (
                r.get("status") is not None or "ERR_ABORTED" in (r.get("failure") or ""))),
            "third_party_rows": third_party_rows(rows),
            "first_ts": min([r["ts"] for r in rows] + _third_party_ts()[:1]),
            "last_ts": max([r["ts"] for r in rows] + _third_party_ts()[1:]),
        },
        "patterns": {
            "sofascore": len(endpoints),
            "not_in_repo": sum(1 for e in endpoints if e["in_repo"] == "no"),
            "by_label": dict(collections.Counter(e["label"] for e in endpoints)),
        },
        "event_count_today": event_count_today(),
        "checks": checks,
        "score_structure": score_structure(events),
        "push": push_analysis(),
    }
    with open(os.path.join(DOCS, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({k: summary[k] for k in ("requests", "patterns", "push")}, ensure_ascii=False, indent=1))



# --- kapanış: gizli bilgi temizliği ve boyut ---------------------------------------------------------------

_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_-]+)?")
# NATS INFO: "client_ip":"…" / "client_id":… (gövdede düz ya da kaçışlı JSON olarak)
_CLIENT_FIELDS = re.compile(r'(\\?"client_(?:ip|id)\\?"\s*:\s*)(?:\\?"[^"\\]*\\?"|\d+)')
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _mask_url(url: str) -> str:
    """Üçüncü taraf: yalnızca alan adı + ilk yol parçası. SofaScore: sorgu dizgisindeki token benzeri değerler."""
    from urllib.parse import urlparse

    u = urlparse(url)
    if not re.search(r"(^|\.)sofascore\.(com|app|net|io)$", u.hostname or ""):
        first = u.path.strip("/").split("/")[0]
        return f"{u.scheme}://{u.hostname}/{first}" + ("?<masked>" if u.query else "")
    return _JWT.sub("<masked-jwt>", url)


def _trim_id_dicts(o: Any, keep: int = 5) -> Any:
    """Kırpılmış örneklerde sayısal id anahtarlı büyük sözlükler (ör. team-events/total) ilk `keep` anahtara iner."""
    if isinstance(o, dict):
        keys = list(o)
        if len(keys) > 20 and all(str(k).lstrip("-").isdigit() for k in keys):
            out = {k: _trim_id_dicts(o[k], keep) for k in keys[:keep]}
            out["__trimmed_keys__"] = len(keys) - keep
            return out
        return {k: _trim_id_dicts(v, keep) for k, v in o.items()}
    if isinstance(o, list):
        return [_trim_id_dicts(v, keep) for v in o]
    return o


def cmd_finalize() -> None:
    """
    PR'a girmeden önce (keşif bittikten sonra) bir kez:
      - üçüncü taraf örnek gövdeleri silinir (reklam/analitik/Sportradar: şema çıkarılmaz, token saklanmaz);
      - requests.jsonl / ws.jsonl'da üçüncü taraf URL'leri alan adı + ilk parçaya indirilir, JWT/UUID maskelenir;
      - /token/captcha örnekleri ve gövdedeki JWT'ler maskelenir;
      - NATS PUB gövdeleri (ilk sürümde saklananlar) silinir;
      - olaylar (spor, id) başına son görülene tekilleştirilip olayın kendi sporunun dosyasına yazılır;
      - `none` etiketli pattern'lerin örnekleri tek kopyaya indirilir.
    """
    removed = collections.Counter()
    for path in glob.glob(os.path.join(OUT, "samples", "*", "*.json")):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        pat = api_pattern(d["url"])
        if not is_sofa_pattern(pat):
            os.remove(path)
            removed["third_party_sample"] += 1
            continue
        if d.get("trimmed"):
            trimmed = _trim_id_dicts(d["body"])
            if trimmed != d["body"]:
                d["body"] = trimmed
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(d, f, ensure_ascii=False, indent=2)
                removed["id_dict_trimmed"] += 1
        raw = json.dumps(d, ensure_ascii=False)
        if "/token/captcha" in d["url"] or _JWT.search(raw):
            d["body"] = json.loads(_JWT.sub("<masked-jwt>", json.dumps(d["body"], ensure_ascii=False)))
            d["masked"] = True
            with open(path, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
            removed["masked_sample"] += 1
    # none etiketli pattern'ler: spor başına kopyaların yalnızca ilki
    keep_none: Dict[str, str] = {}
    for path in sorted(glob.glob(os.path.join(OUT, "samples", "*", "*.json"))):
        with open(path, encoding="utf-8") as f:
            pat = api_pattern(json.load(f)["url"])
        if label_for(pat)[0] == "none":
            if pat in keep_none:
                os.remove(path)
                removed["duplicate_none_sample"] += 1
            else:
                keep_none[pat] = path
    existing = {os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(OUT, "samples", "*", "*.json"))}

    rows = []
    with open(os.path.join(OUT, "requests.jsonl"), encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            r["url"] = _mask_url(r["url"])
            r["page"] = _mask_url(r.get("page") or "")
            if r.get("origin"):
                r["origin"] = _mask_url(r["origin"])
            if r.get("sample_file") and r["sample_file"] not in existing:
                r["sample_file"] = None
            rows.append(r)
    with open(os.path.join(OUT, "requests.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    ws_rows = []
    with open(os.path.join(OUT, "ws.jsonl"), encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r.get("url"):
                r["url"] = _mask_url(r["url"])
            if r["kind"] == "sent":
                line_ = r.get("line", "")
                if not line_.startswith(("SUB ", "UNSUB ", "CONNECT ", "PUB ", "HPUB ")):
                    removed["ws_pub_body"] += 1
                    continue
                r["line"] = _UUID.sub("<uuid>", line_)
            if "sportradar" in (r.get("url") or "") and r["kind"] == "frame":
                removed["ws_third_party_frame"] += 1
                continue
            # NATS INFO bağlantının istemci IP'sini taşır: maskelenir; ilk sürümün ham base64 kopyası atılır
            if r.pop("base64", None) is not None:
                removed["ws_base64_dropped"] += 1
            raw = json.dumps(r, ensure_ascii=False)
            masked = _CLIENT_FIELDS.sub(
                lambda m: m.group(1) + ('\\"<masked>\\"' if "\\" in m.group(1) else '"<masked>"'), raw)
            if masked != raw:
                removed["ws_client_ip_masked"] += 1
                r = json.loads(masked)
            ws_rows.append(r)
    with open(os.path.join(OUT, "ws.jsonl"), "w", encoding="utf-8") as f:
        for r in ws_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    events = load_events()
    for path in glob.glob(os.path.join(OUT, "events", "*.jsonl")):
        os.remove(path)
    for sport, evs in events.items():
        with open(os.path.join(OUT, "events", f"{sport}.jsonl"), "w", encoding="utf-8") as f:
            for e in sorted(evs.values(), key=lambda e: e["id"]):
                f.write(json.dumps({k: v for k, v in e.items() if k != "_file"}, ensure_ascii=False) + "\n")
    print(dict(removed), "events:", {k: len(v) for k, v in events.items()})



# --- compact: repoya girecek veriyi küçültme (ham kayıt yerelde data/research_index/) --------------------

INFO_KEEP = ("version", "auth_required", "tls_required")
SAMPLE_CAP = 12_000
# Yapısı spora göre değişebilen uç noktalar: spor başına bir örnek tutulur
PER_SPORT_SAMPLES = (r"^/event/\{id\}($|/(incidents|statistics|lineups|innings|esports-games|point-by-point))|"
                     r"events/live$|scheduled-events|scheduled-tournaments|finished-upcoming|^/stage/|/team/\{id\}/events/last")


def _saved_top_keys() -> Dict[str, Dict[str, int]]:
    path = os.path.join(OUT, "events", "_top_keys.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _third_party_ts() -> List[float]:
    path = os.path.join(OUT, "third_party_hosts.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return [d["first_ts"], d["last_ts"]]


def third_party_rows(rows: List[Dict[str, Any]]) -> int:
    n = sum(1 for r in rows if not is_sofa_pattern(r["pattern"]))
    path = os.path.join(OUT, "third_party_hosts.json")
    if n == 0 and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)["rows"]
    return n


def _info_body(text: str) -> Dict[str, Any]:
    """NATS INFO: yalnızca version, auth_required, tls_required (kareler kısaltılmış olabilir: JSON değil, regex)."""
    out: Dict[str, Any] = {}
    for key in INFO_KEEP:
        m = re.search(r'"' + key + r'"\s*:\s*("([^"]*)"|true|false)', text)
        out[key] = (m.group(2) if m.group(2) is not None else m.group(1) == "true") if m else None
    return out


def _shrink(body: Any, limit: int) -> Any:
    """Örnek gövdesini `limit` baytın altına indirir: listeler 3→2→1 öğe, büyük sözlükler 10 anahtar, uzun metinler."""
    def cut(o: Any, n_list: int, n_keys: int) -> Any:
        if isinstance(o, dict):
            keys = list(o)
            out = {k: cut(o[k], n_list, n_keys) for k in keys[:n_keys] if k != "__trimmed_keys__"}
            if len(keys) > n_keys:
                out["__trimmed_keys__"] = len(keys) - n_keys
            return out
        if isinstance(o, list):
            kept = [cut(v, n_list, n_keys) for v in o if not (isinstance(v, dict) and "__trimmed__" in v)][:n_list]
            if len(o) > n_list:
                kept.append({"__trimmed__": len(o) - n_list})
            return kept
        if isinstance(o, str) and len(o) > 300:
            return o[:300] + "…"
        return o

    for n_list, n_keys in ((3, 10_000), (3, 30), (2, 20), (1, 12), (1, 6)):
        out = cut(body, n_list, n_keys)
        if len(json.dumps(out, ensure_ascii=False).encode("utf-8")) <= limit:
            return out
    return out


def cmd_compact() -> None:
    """
    Repoya girecek veriyi küçültür (finalize'dan sonra, bir kez). Ham kaydın tamamı bu adımdan önce
    data/research_index/ altına (gitignore'lu) kopyalanmış olmalı. Analiz (`all`) aynı CSV ve summary.json'u
    üretmeye devam eder: kaybolan toplamlar ws_counts.json, third_party_hosts.json ve events/_top_keys.json'a yazılır.
    """
    stats = collections.Counter()
    # 1) ws.jsonl: toplamlar önce, sonra yalnızca kanıt kareleri
    push = push_analysis()
    with open(os.path.join(OUT, "ws_counts.json"), "w", encoding="utf-8") as f:
        json.dump({"rows_before_compact": sum(1 for _ in open(os.path.join(OUT, "ws.jsonl"), encoding="utf-8")),
                   "sub_subjects": push["sub_subjects"], "msg_subjects_logged": push["msg_subjects_logged"]},
                  f, ensure_ascii=False, indent=2)
    rows = [json.loads(line) for line in open(os.path.join(OUT, "ws.jsonl"), encoding="utf-8")]
    expanded = []
    for r in rows:  # ilk sürümün ham kareleri: MSG/INFO'ya çözülür, ham metin atılır
        raw = r.get("text") or r.get("utf8_preview") or ""
        if r["kind"] == "frame":
            for m in re.finditer(r"MSG (\S+) \S+ \d+\r\n(\{.*?\})\r\n", raw):
                try:
                    expanded.append({"kind": "recv", "op": "MSG", "subject": m.group(1), "ts": r["ts"],
                                     "sport": r.get("sport"), "url": r.get("url"), "body": json.loads(m.group(2)),
                                     "legacy": True})
                except ValueError:
                    pass
            if raw.startswith("INFO "):
                expanded.append({"kind": "recv", "op": "INFO", "ts": r["ts"], "url": r.get("url"), "body": raw[5:]})
            continue
        expanded.append(r)
    keep = []
    seen = collections.Counter()
    for r in expanded:
        kind, op = r["kind"], r.get("op")
        if kind == "recv" and op == "INFO":
            r["body"] = _info_body(r["body"] if isinstance(r["body"], str) else json.dumps(r["body"]))
            key = "INFO"
        elif kind == "recv" and op in ("MSG", "HMSG"):
            b = r.get("body")
            if isinstance(b, dict) and any(k.startswith("status.") for k in b):
                keep.append(r)  # gecikme tablosunun dayanağı: hepsi
                continue
            key = "MSG " + re.sub(r"\d+", "{id}", r.get("subject") or "")
        elif kind == "sent":
            key = "SENT " + re.sub(r"\d+|<uuid>", "{x}", r["line"].split(" {")[0].rsplit(" ", 1)[0])
        else:
            key = f"{kind} {urlparse_host(r.get('url') or '')}"
        seen[key] += 1
        if seen[key] <= 3:
            keep.append(r)
    with open(os.path.join(OUT, "ws.jsonl"), "w", encoding="utf-8") as f:
        for r in keep:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    stats["ws_rows"] = len(keep)

    # 2) requests.jsonl: üçüncü taraf → host özeti; tekrar eden alanlar pattern başına bir kez
    rows = [json.loads(line) for line in open(os.path.join(OUT, "requests.jsonl"), encoding="utf-8")]
    third = [r for r in rows if not is_sofa_pattern(api_pattern(r["url"]))]
    if third:
        widget = sorted({(r["sport"], r.get("page_type") or "", r["page"]) for r in third if "sportradar" in (r.get("host") or "")})
        with open(os.path.join(OUT, "third_party_hosts.json"), "w", encoding="utf-8") as f:
            json.dump({"rows": len(third), "first_ts": min(r["ts"] for r in third),
                       "last_ts": max(r["ts"] for r in third), "hosts": dict(collections.Counter(r.get("host") for r in third).most_common()),
                       "sportradar_widget_pages": [{"sport": a, "page_type": b, "page": c} for a, b, c in widget]},
                      f, ensure_ascii=False, indent=2)
    sofa = [r for r in rows if is_sofa_pattern(api_pattern(r["url"]))]
    best_keys: Dict[str, Tuple[str, ...]] = {}
    by_pat = collections.defaultdict(collections.Counter)
    for r in sofa:
        if r.get("status") == 200 and r.get("response_keys") is not None:
            by_pat[api_pattern(r["url"])][tuple(r["response_keys"])] += 1
    for pat, c in by_pat.items():
        best_keys[pat] = c.most_common(1)[0][0]
    done = set()
    with open(os.path.join(OUT, "requests.jsonl"), "w", encoding="utf-8") as f:
        for r in sofa:
            pat = api_pattern(r["url"])
            if r.get("origin") == r.get("page") or (r.get("origin") or "").split("#")[0] == (r.get("page") or "").split("#")[0]:
                r.pop("origin", None)
            r.pop("pattern", None)  # URL'den yeniden hesaplanır
            if r.get("status") == 200 and pat not in done and pat in best_keys:
                r["response_keys"] = list(best_keys[pat])
                done.add(pat)
            else:
                r.pop("response_keys", None)
            for k in ("host", "resource_type", "method"):
                if r.get(k) in ("www.sofascore.com", "xhr", "fetch", "GET"):
                    r.pop(k)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    stats["third_party_rows_removed"] = len(third)

    # 3) samples. Önce silinince kaybolacak türetilmiş veriler: en son event-count, stage nesneleri
    latest = event_count_today()
    with open(os.path.join(OUT, "event_count_latest.json"), "w", encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False, indent=1)
    stages = load_stages()
    with open(os.path.join(OUT, "events", "_stages.jsonl"), "w", encoding="utf-8") as f:
        for sport, gs in sorted(stages.items()):
            for g in sorted(gs.values(), key=lambda g: g["id"]):
                f.write(json.dumps(g, ensure_ascii=False, separators=(",", ":")) + "\n")
    # Spora göre farklılaşabilen uç noktalarda spor başına bir örnek; genel uç noktalarda tek örnek;
    # README'nin atıf yaptığı dosyalar her durumda kalır. Her örnek ≤ SAMPLE_CAP.
    with open(os.path.join(DOCS, "README.md"), encoding="utf-8") as f:
        cited = set(re.findall(r"research/all_sports/(samples/[^`\s)]+\.json)", f.read()))
    by_pattern = collections.defaultdict(list)
    for path in sorted(glob.glob(os.path.join(OUT, "samples", "*", "*.json"))):
        with open(path, encoding="utf-8") as f:
            by_pattern[api_pattern(json.load(f)["url"])].append(path)
    # Kullanıcıya ait veri taşıyan yanıtlar (ör. /country/alpha2: istemci IP'si, şehir, TLS parmak izi) saklanmaz
    for pat in list(by_pattern):
        for path in list(by_pattern[pat]):
            with open(path, encoding="utf-8") as f:
                body = json.load(f).get("body")
            if isinstance(body, dict) and ("ip" in body or "ipAddress" in body):
                os.remove(path)
                by_pattern[pat].remove(path)
                stats["personal_sample_removed"] += 1
    for pat, paths in by_pattern.items():
        if not paths:
            continue
        per_sport = re.search(PER_SPORT_SAMPLES, pat) or len({os.path.basename(os.path.dirname(x)) for x in paths}) <= 3
        kept_sports = set()
        for path in sorted(paths, key=lambda x: (not x.endswith("__1.json"), x)):
            rel = os.path.relpath(path, OUT)
            sport = os.path.basename(os.path.dirname(path))
            keep = rel in cited or (path.endswith("__1.json") and (
                (per_sport and sport not in kept_sports) or not kept_sports))
            if keep:
                kept_sports.add(sport)
                continue
            os.remove(path)
            stats["samples_removed"] += 1
    for path in glob.glob(os.path.join(OUT, "samples", "*", "*.json")):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if len(json.dumps(d, ensure_ascii=False).encode("utf-8")) > SAMPLE_CAP:
            d["body"] = _shrink(d["body"], SAMPLE_CAP - 1500)
            d["trimmed"] = True
            stats["samples_shrunk"] += 1
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
    existing = {os.path.relpath(p, ROOT) for p in glob.glob(os.path.join(OUT, "samples", "*", "*.json"))}
    lines = [json.loads(line) for line in open(os.path.join(OUT, "requests.jsonl"), encoding="utf-8")]
    with open(os.path.join(OUT, "requests.jsonl"), "w", encoding="utf-8") as f:
        for r in lines:
            if r.get("sample_file") and r["sample_file"] not in existing:
                r["sample_file"] = None
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 4) events: top_keys sayımı ayrı dosyaya; skor içindeki iç içe sözlükler (innings) 2 girdiye
    events = load_events()
    top = {sp: dict(collections.Counter(k for e in evs.values() for k in e.get("top_keys") or []).most_common())
           for sp, evs in events.items()}
    if any(top.values()):
        with open(os.path.join(OUT, "events", "_top_keys.json"), "w", encoding="utf-8") as f:
            json.dump(top, f, ensure_ascii=False, indent=1)
    for sport, evs in events.items():
        with open(os.path.join(OUT, "events", f"{sport}.jsonl"), "w", encoding="utf-8") as f:
            for e in sorted(evs.values(), key=lambda e: e["id"]):
                row = {k: v for k, v in e.items() if k not in ("_file", "top_keys", "seen_at", "source_pattern")}
                if isinstance(row.get("changes"), dict):  # değişen alan listesi analizde kullanılmıyor
                    row["changes"] = {"changeTimestamp": row["changes"].get("changeTimestamp")}
                for side in ("homeScore", "awayScore"):
                    sc = row.get(side) or {}
                    for k, v in list(sc.items()):
                        if isinstance(v, dict) and len(v) > 2:
                            sc[k] = {kk: v[kk] for kk in list(v)[:2]} | {"__trimmed_keys__": len(v) - 2}
                f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(dict(stats))


def urlparse_host(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).hostname or ""


if __name__ == "__main__":
    {"patterns": cmd_patterns, "all": cmd_all, "finalize": cmd_finalize, "compact": cmd_compact}[sys.argv[1] if len(sys.argv) > 1 else "patterns"]()
