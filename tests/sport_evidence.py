"""
Her sporun maç sayfasının istediği detay uç noktaları ve aldığı yanıtlar: research/all_sports'tan türeyen kanıt
tablosu ve dilim kayıt defterinin (sofascore_scraper/sports.py, DETAIL_SLICES) ondan çıkan kuralları.

Ağ yok: yalnızca research/all_sports/requests.jsonl, pages.jsonl ve events/*.jsonl okunur. Tablo
tests/fixtures/sport_slices/evidence.json'a yazılıdır; tests/test_sport_slices.py yazılı tablonun araştırma
verisinden yeniden türediğini ve kayıt defterinin tabloya uyduğunu denetler.

Veride üç koşu var: 2026-10-01'in keşfi (PR #17; satırlarında `run_id` yok) ve onarılmış keşif aracının
iki koşusu (FX-29 ve FX-29b'den sonra, her satırda `run_id`): `lv-20261009` (PR #183; her spordan maç sayfaları)
ve `lv-20261009b` (FX-36; ragbi, voleybol, florbol ve mini futbolun en çok takip edilen üç liginden 12 bitmiş
maç sayfası). Eski araç maç sayfalarının
isteklerinin çoğunu kaybetti: kaybolan istek ne gönderildi ne kaydedildi, iz bırakmadı (FX-29, FX-29b). İki
kural buradan çıkar:

  1. Yanıtlar (answers) bütün koşulardan sayılır. Kaydedilmiş bir yanıt SofaScore'un gerçek yanıtıdır; hata
     yalnızca istekleri düşürdü, yanıtları değiştirmedi. Eski yanıtları atmak gerçek kanıtı atmak olurdu:
     futbol ve basketbolda team-streaks ve h2h'nin bitmiş ve canlı maç yanıtları yalnızca eski koşudadır;
     futbolun ve buz hokeyinin bitmiş maçta pregame-form 404'leri de (FX-16'nın kararı) öyle. Aynı dilim bir
     maçta 200, ötekinde 404 alıyorsa (ör. ragbi lineups: eski koşuda 200, yenisinde 404) ikisi de doğrudur:
     dilim her maçta gelmez.
  2. Sayfalar (pages, yani "sayfa bu dilimi hiç istemedi" yokluk kanıtı) yalnızca onarılmış koşulardan
     (REPAIRED_RUNS) gelir. Eski koşunun maç sayfaları eksik: ragbi, mini futbol ve masa tenisi sayfaları
     kendi maçları için 4-6 uç nokta yanıtladı, onarılmış koşunun aynı sporlardaki sayfaları 13-15; eski
     ragbi sayfasında statistics ve incidents hiç yok, yeni sayfa ikisini de istedi. Kaybolan istek
     "istenmedi"den ayırt edilemez, bu yüzden eski sayfa yokluğa kanıt olamaz.

Maçın durumu isteğin anındaki durumdur. Onarılmış koşunun olay satırlarında `seen_at` vardır: isteğe zamanca
en yakın, maçın kendi /event/{id} yanıtından gelen gözlem (yoksa o koşunun herhangi bir gözlemi) kullanılır.
Gerekçe: koşunun "başlamamış" diye açtığı sayfalardan üçünün maçı sayfa açıldığında başlamıştı (basketbol
16685163, buz hokeyi 16546323, beyzbol 17212667), dartın 17279017'si bitmişti; Amerikan futbolunun 16183708'i
sayfa kapandıktan sonra başladı ve son gözlem ("inprogress") onu yanlışlıkla canlı sayardı. Eski koşunun
satırlarında zaman yok: eski koşunun maçın son gözlemi geçerlidir (yeni koşunun gözlemleri eski isteklerin
durumunu değiştirmez; 2026-10-01'de başlamamış 9 maç 2026-10-09'da bitmişti).

pages.jsonl'daki `request-gone` satırı (FX-29b) sayfanın istediği ama Chromium'un göndermeden düşürdüğü
isteğidir: maç sayfasındaki bir maç uç noktası için yanıtlara "<durum>:gone" olarak girer (sayfa onu istedi,
yokluk sayılmaz; yanıt yok, veri ya da 404 sayılmaz). `body_error` (gövde okunamadı) ve `sample_redacted`
satırları HTTP koduyla sayılır: gövdeye bakılmaz.

Yeniden üretmek (araştırma verisi değişirse; yeni bir onarılmış koşu REPAIRED_RUNS'a eklenir):
    python tests/sport_evidence.py > tests/fixtures/sport_slices/evidence.json
"""
from __future__ import annotations

import collections
import glob
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

REPO = Path(__file__).resolve().parent.parent
RESEARCH = REPO / "research" / "all_sports"
EVIDENCE_FILE = Path(__file__).resolve().parent / "fixtures" / "sport_slices" / "evidence.json"

# Keşif aracının onarılmış sürümüyle (FX-29, FX-29b) kaydedilmiş koşular: yalnızca bunların maç sayfaları yokluk
# kanıtıdır. Eski koşunun satırlarında run_id yoktur.
REPAIRED_RUNS = frozenset({"lv-20261009", "lv-20261009b"})

# Maçın durumu, olayın status.type'ından: bitmiş, başlamış (canlı) ya da başlamamış
FINISHED, LIVE, NOT_STARTED = "finished", "live", "notstarted"
_STATES = {"finished": FINISHED, "inprogress": LIVE, "willcontinue": LIVE, "notstarted": NOT_STARTED}
GONE = "gone"  # sayfa istedi, istek gönderilmeden düştü (request-gone): yanıt yok

# Bahis ve oran uç noktaları P28'in işidir; tabloya girmez
_ODDS = re.compile(r"/odds/|winning-odds|betting-odds")

# Sayfa açılınca istenen dilimler: eksiksiz yüklenmiş canlı ya da bitmiş maç sayfasında hiç istenmezse o sporda
# yoktur. H2H ve seriler (team-streaks) bitmiş maçta ayrı sekmededir; onlarda yokluk kanıt sayılmaz.
PAGE_LOAD_ENDPOINTS = frozenset({"/statistics", "/lineups", "/incidents"})

# Kanıta göre dilimin bir spordaki yeri
REQUIRED = "required"  # bitmiş maçta veriyle yanıtlandı, bitmiş maçta hiç 404 almadı
OPTIONAL = "optional"  # başlamış maçta 404 aldı ve bitmiş maçta hep veriyle gelmedi
OPEN_DATA = "open_data"  # yalnızca bitmemiş maçta veriyle yanıtlandı
ABSENT = "absent"  # sayfa açılınca istenen dilim, eksiksiz yüklenmiş canlı ya da bitmiş sayfada hiç istenmedi
UNKNOWN = "unknown"  # kanıt yok ya da yetersiz (sayfası açılmadı, 403, yarım yüklendi, yalnızca başlamamış maç)

# Olayın bir gözlemi (onarılmış koşu): (koşu, zaman, maçın kendi /event/{id} yanıtından mı, spor, durum)
_Seen = Tuple[str, float, bool, str, str]


def _endpoint(url: str) -> Optional[Tuple[int, str]]:
    """API adresi → (olay kimliği, uç nokta kalıbı: "/" ya da "/statistics", "/heatmap/{id}", ...)."""
    path = url.split("/api/v1/", 1)[-1].split("?", 1)[0]
    m = re.match(r"event/(\d+)(/.*)?$", path)
    if not m:
        return None
    suffix = m.group(2) or "/"
    suffix = re.sub(r"/country/[A-Z]{2}", "/country/{cc}", suffix)
    suffix = re.sub(r"/ai-insights(-postmatch)?/[a-z]{2}$", r"/ai-insights\1/{lang}", suffix)
    suffix = re.sub(r"/\d+", "/{id}", suffix)
    return int(m.group(1)), suffix


class _Events:
    """Olayların sporu ve durumu: eski koşudan son gözlem, onarılmış koşulardan zamanlı gözlemler."""

    def __init__(self, research: Path) -> None:
        self.old: Dict[int, Tuple[str, str]] = {}
        self.timed: Dict[int, List[_Seen]] = collections.defaultdict(list)
        for path in sorted(glob.glob(os.fspath(research / "events" / "*.jsonl"))):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    row = json.loads(line)
                    if "id" not in row or not row.get("sport"):
                        continue
                    kind = (row.get("status") or {}).get("type") or ""
                    sport, state = str(row["sport"]), _STATES.get(kind, kind)
                    if row.get("run_id") and row.get("seen_at") is not None:
                        self.timed[int(row["id"])].append((str(row["run_id"]), float(row["seen_at"]),
                                                           row.get("source_pattern") == "/event/{id}", sport, state))
                    elif not row.get("run_id"):
                        self.old[int(row["id"])] = (sport, state)

    def at(self, event_id: int, run_id: Optional[str], ts: float, page_sport: str) -> Tuple[str, str]:
        """(spor, durum) isteğin anında: onarılmış koşuda zamanca en yakın gözlem, eski koşuda son gözlem."""
        if run_id:
            seen = [s for s in self.timed.get(event_id, ()) if s[0] == run_id]
            own = [s for s in seen if s[2]] or seen
            if own:
                nearest = min(own, key=lambda s: (abs(s[1] - ts), s[1]))
                return nearest[3], nearest[4]
        return self.old.get(event_id, (page_sport, "unknown"))


def _gone_requests(research: Path) -> Iterable[Dict[str, Any]]:
    """pages.jsonl'daki request-gone satırları, bir maç sayfası açıkken düşenler (onarılmış koşular)."""
    path = research / "pages.jsonl"
    if not path.exists():
        return
    page_type, sport = "", ""
    with open(path, encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("op") == "goto":
                page_type, sport = str(row.get("page_type") or ""), str(row.get("sport") or "")
            elif (row.get("op") == "request-gone" and page_type.startswith("event")
                  and row.get("run_id") in REPAIRED_RUNS):
                yield {"url": row["url"], "ts": row["ts"], "run_id": row["run_id"], "sport": sport}


def derive(research: Path = RESEARCH) -> Dict[str, Any]:
    """
    Kanıt tablosu:
      answers: spor → uç nokta → ["<durum>:<HTTP kodu>", ...] (maçın isteğin anındaki durumu ve yanıtın kodu,
               ya da "<durum>:gone": istendi, gönderilmeden düştü; sıralı, tekil). Bütün koşulardan.
      pages:   spor → [{"event_id", "state", "complete", "run_id"}]: onarılmış koşularda maç sayfasında açılan
               maçlar. state: sayfanın /event/{id} yanıtının anındaki durum. complete: sayfa /event/{id}'yi ve
               /event/{id}/pregame-form'u istedi ve yanıt (200 ya da 404) aldı (403 alan sayfa sayılmaz)
    Yalnızca maç sayfalarındaki (sayfa tipi event…) istekler sayılır; oranlar hariç. Yanıtı kaydedilmemiş istek
    (kod yok) sayılmaz.
    """
    events = _Events(research)
    answers: Dict[str, Dict[str, Set[str]]] = collections.defaultdict(lambda: collections.defaultdict(set))
    seen: Dict[Tuple[str, int, str], Dict[str, str]] = collections.defaultdict(dict)
    with open(research / "requests.jsonl", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if not str(row.get("page_type") or "").startswith("event") or row.get("status") is None:
                continue
            found = _endpoint(row["url"])
            if found is None or _ODDS.search(found[1]):
                continue
            event_id, suffix = found
            run_id = row.get("run_id")
            sport, state = events.at(event_id, run_id, float(row["ts"]), str(row["sport"]))
            answers[sport][suffix].add(f"{state}:{row['status']}")
            # 403: istek engellendi, sayfanın ne istediği bilinmez. Eski koşunun sayfaları yokluğa kanıt değil.
            if row["status"] in (200, 404) and run_id in REPAIRED_RUNS:
                seen[(sport, event_id, run_id)].setdefault(suffix, state)
    for row in _gone_requests(research):
        found = _endpoint(row["url"])
        if found is None or _ODDS.search(found[1]):
            continue
        event_id, suffix = found
        sport, state = events.at(event_id, row["run_id"], float(row["ts"]), row["sport"])
        answers[sport][suffix].add(f"{state}:{GONE}")
    pages: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for (sport, event_id, run_id), states in sorted(seen.items()):
        if len(set(states) - {"/", "/votes"}) < 2:
            continue  # başka bir maçın sayfasında geçen maç (ör. H2H listesi): kendi sayfası açılmadı
        pages[sport].append({"event_id": event_id, "state": states.get("/", next(iter(states.values()))),
                             "complete": "/" in states and "/pregame-form" in states, "run_id": run_id})
    return {
        "source": "research/all_sports: requests.jsonl, pages.jsonl, events/*.jsonl; answers from every run "
                  "(2026-10-01, PR #17; lv-20261009, PR #183; lv-20261009b, FX-36), pages from the repaired runs "
                  f"({', '.join(sorted(REPAIRED_RUNS))})",
        "pages": {sport: pages[sport] for sport in sorted(pages)},
        "answers": {sport: {suffix: sorted(codes) for suffix, codes in sorted(by_suffix.items())}
                    for sport, by_suffix in sorted(answers.items())},
    }


def load() -> Dict[str, Any]:
    with open(EVIDENCE_FILE, encoding="utf-8") as f:
        return json.load(f)


def verdict(evidence: Dict[str, Any], sport: str, endpoint: str) -> str:
    """Kanıta göre `endpoint`'in (ör. "/statistics") `sport`taki yeri: REQUIRED, OPTIONAL, OPEN_DATA, ABSENT, UNKNOWN."""
    codes: Iterable[str] = evidence["answers"].get(sport, {}).get(endpoint, ())
    pairs = {tuple(code.split(":", 1)) for code in codes}
    finished_data = (FINISHED, "200") in pairs
    finished_404 = (FINISHED, "404") in pairs
    started_404 = finished_404 or (LIVE, "404") in pairs
    if finished_data and not finished_404:
        return REQUIRED
    if started_404 or finished_data:
        return OPTIONAL
    if any(state == "200" for _s, state in pairs):
        return OPEN_DATA
    if not pairs and endpoint in PAGE_LOAD_ENDPOINTS and any(
            page["complete"] and page["state"] in (FINISHED, LIVE) for page in evidence["pages"].get(sport, ())):
        return ABSENT
    return UNKNOWN


if __name__ == "__main__":
    json.dump(derive(), sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
