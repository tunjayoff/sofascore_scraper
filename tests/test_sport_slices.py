"""
Her spor SofaScore'un o sporda sunduğu detay dilimlerini ister (sofascore_scraper/sports.py, DETAIL_SLICES).

Kanıt: sitenin maç sayfalarının istekleri ve aldığı yanıtlar (research/all_sports: PR #17'nin keşfi ve onarılmış
keşif aracının koşusu lv-20261009, PR #183), spor ve uç nokta başına tests/fixtures/sport_slices/evidence.json'da
(tests/sport_evidence.py türetir; hangi koşunun neyi kanıtladığı oradaki notta). Kurallar
(tests/sport_evidence.py `verdict`, sofascore_scraper/sports.py DETAIL_SLICES'ın üstündeki not):

  required   bitmiş maçta veriyle yanıtlandı, bitmiş maçta 404 almadı     → istenir, tamlık hesabına girer
  optional   başlamış maçta 404 aldı, bitmiş maçta hep veriyle gelmedi    → istenir, tamlık hesabına girmez
  open_data  yalnızca bitmemiş maçta veriyle yanıtlandı                   → ortak dilim değişmez; spora özel
                                                                            dilim istenir, tamlığa girmez
  absent     sayfa açılınca istenen dilim, eksiksiz yüklenmiş canlı ya da → istenmez
             bitmiş sayfada hiç istenmedi
  unknown    kanıt yok ya da yetersiz                                     → ortak dilim değişmez; spora özel
                                                                            dilim istenmez

Futbol, basketbol ve tenis için kanıtın önerdikleri (#121'in üç önerisi) canlı doğrulamadan sonra sahibin
kararıyla (2026-10-08) uygulandı (FX-16): DECIDED'da durur. Kararın ek kanıtı uygulama tarafındandır: canlı
doğrulamada her spordan bir bitmiş (2026-10-07, 20 spor) ve bir canlı maç (2026-10-08, 13 spor) bütün dilimleriyle
tek maç takibi olarak indirildi; tablolar tests/fixtures/sport_slices/slice-matrix-{finished,live}.txt.

evidence.json onarılmış araçla (FX-29, FX-29b) yeniden üretildi (FX-31): kanıtın önerdikleri uygulandı (APPLIED).
Ragbi, florbol, voleybol ve mini futbol için kanıtın önerdikleri (FX-31) sahibin kararını bekledi; en çok takip
edilen üç ligin bitmiş maçlarıyla (koşu lv-20261009b) yeniden bakıldıktan sonra sahibin kararıyla (2026-10-09)
uygulandı (FX-36): DECIDED'da durur. Kararın bir hücresi kanıtın kuralından ayrılır (florbol olayları:
DECIDED_AGAINST_THE_RULE). docs/all-sports/README.md, "Karar".

FX-37: canlı maç sayfaları (lv-20261009c) ve aynı maçların bitmiş sayfaları (lv-20261009d) da onarılmış koşular.
Aynı maçta hiçbir dilimin yanıtı canlıdan bitmişe değişmedi (SAME_MATCH). Yeni yanıtlarla kural üç hücrede kayıt
defterinden ayrılır; kayıt defteri değişmedi, hücreler sahibin kararını bekler (PENDING). Ağ yok.
"""
from __future__ import annotations

import collections
import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import pytest

import sport_evidence as evidence_mod
from characterization import WORLD, pin_default_settings
from fakes.sofascore import SITE_ROOT, FakeSofaScore
from sport_evidence import ABSENT, OPEN_DATA, OPTIONAL, REQUIRED, UNKNOWN
from sofascore_scraper import slices, sports
from sofascore_scraper.services import planning
from sofascore_scraper.services.pipeline import FetchPipeline
from sofascore_scraper.services.query import RefreshPolicy, required_detail_keys
from sofascore_scraper.slices import BODY_DATA, BODY_NO_DATA, SLICE_OK, Outcome
from sofascore_scraper.store import Ref, open_store

FIXTURES = Path(__file__).parent / "fixtures" / "sport_slices"
EVIDENCE = evidence_mod.load()
COMMON_KEYS = ("statistics", "team_streaks", "pregame_form", "h2h", "lineups", "incidents")

# Sahibin kararıyla uygulanan hücreler: (spor, dilim) → kararın yeri (kayıt defteri buna uyar). Kanıtın yargısı da
# budur, DECIDED_AGAINST_THE_RULE'dakiler dışında.
DECIDED: Dict[Tuple[str, str], str] = {
    # #121'in futbol, basketbol ve tenis için önerdikleri (2026-10-08, FX-16)
    ("football", "pregame_form"): OPTIONAL,  # bitmiş iki maçta ve canlı maçta 404
    ("basketball", "pregame_form"): OPTIONAL,  # bitmiş maçta 404
    ("tennis", "pregame_form"): OPTIONAL,  # bitmiş ve canlı maçta 404
    ("tennis", "lineups"): ABSENT,  # iki eksiksiz sayfa (bitmiş, canlı) istemedi
    ("tennis", "incidents"): ABSENT,
    ("tennis", "point_by_point"): REQUIRED,  # bitmiş ve canlı maçta veriyle
    # FX-31'in ragbi, voleybol, florbol ve mini futbol önerisi, lv-20261009b'nin üst lig maçlarıyla (2026-10-09,
    # FX-36). Ragbi pregame_form ve voleybol olayları öneride yoktu (lv-20261009'un tek bitmiş maçında veriyle)
    ("rugby", "statistics"): OPTIONAL,  # üst ligin üç maçında 200, lv-20261009'un bitmiş maçında 404
    ("rugby", "lineups"): OPTIONAL,  # aynı
    ("rugby", "incidents"): OPTIONAL,  # aynı
    ("rugby", "pregame_form"): OPTIONAL,  # üst ligde 200, 404, 200
    ("volleyball", "statistics"): OPTIONAL,  # üst ligde 404, 404, 200
    ("volleyball", "lineups"): OPTIONAL,  # bütün bitmiş maçlarda 404
    ("volleyball", "incidents"): OPTIONAL,  # üst ligde 200, 200, 404 (Asya Oyunları)
    ("volleyball", "pregame_form"): OPTIONAL,  # bütün bitmiş maçlarda 404
    ("floorball", "lineups"): ABSENT,  # eksiksiz bitmiş ve başlamamış sayfaların hiçbiri istemedi
    ("floorball", "statistics"): OPTIONAL,  # bütün bitmiş maçlarda 404
    ("floorball", "pregame_form"): OPTIONAL,  # üst ligde 200, 404, 200
    ("floorball", "incidents"): REQUIRED,  # üst ligde 200 x 3; kural: isteğe bağlı (DECIDED_AGAINST_THE_RULE)
    ("minifootball", "statistics"): OPTIONAL,  # üst ligde 404, 200, 200
    ("minifootball", "incidents"): OPTIONAL,  # aynı
    ("minifootball", "pregame_form"): OPTIONAL,  # bütün maçlarda 404
}

# Kararın kanıtın kuralından ayrıldığı hücreler: (spor, dilim) → kuralın yargısı. Florbol olayları: üst liglerin üç
# bitmiş maçında (lv-20261009b) veriyle, lv-20261009'un bitmiş maçında 200, ötekinde 404; kural "bitmiş maçta bir
# 404" yüzünden isteğe bağlı der, sahip tamlığa girmesine karar verdi (FX-36).
DECIDED_AGAINST_THE_RULE: Dict[Tuple[str, str], str] = {
    ("floorball", "incidents"): OPTIONAL,
}

# FX-37: lv-20261009c ve lv-20261009d'nin yanıtlarıyla kuralın kayıt defterinden ayrıldığı hücreler, sahibin kararını
# bekler: (spor, dilim) → (kuralın yargısı, kayıt defterinin bugünkü yeri). Kayıt defteri değişmedi.
PENDING: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("basketball", "lineups"): (OPTIONAL, REQUIRED),
    ("handball", "pregame_form"): (OPTIONAL, REQUIRED),
    ("mma", "statistics"): (OPTIONAL, REQUIRED),
}
# Hücreyi değiştiren yanıtlar (200 dışı): (koşu, sayfa, maç, maçın durumu, HTTP kodu). Basketbol kadrosu iki canlı
# sayfada ve 17220735'in bitmiş sayfasında, hentbol pregame-form'u aynı maçın canlı ve bitmiş sayfasında 404 aldı
# (sayfanın kendi maçı). MMA istatistiği komşu maçtan: canlı 12606166'nın sayfası bitmiş 12607782'yi de istedi
# (FX-36'nın bulgusu; kural onu da sayar). MMA'nın iki canlı sayfası kendi maçının istatistiğini 200 aldı.
PENDING_ANSWERS: Dict[Tuple[str, str], List[Tuple[str, int, int, str, str]]] = {
    ("basketball", "lineups"): [("lv-20261009c", 16624761, 16624761, "live", "404"),
                                ("lv-20261009c", 17220735, 17220735, "live", "404"),
                                ("lv-20261009d", 17220735, 17220735, "finished", "404")],
    ("handball", "pregame_form"): [("lv-20261009c", 16642082, 16642082, "live", "404"),
                                   ("lv-20261009d", 16642082, 16642082, "finished", "404")],
    ("mma", "statistics"): [("lv-20261009c", 12606166, 12607782, "finished", "404")],
}

# FX-36: lv-20261009b'nin bitmiş maç sayfalarında (her sporun en çok takip edilen üç ligi) dört dilimin HTTP
# kodları, sayfa başına (sıra önemsiz); "-": sayfa istemedi
TOP_LEAGUE_ANSWERS: Dict[str, Dict[str, List[str]]] = {
    "rugby": {"statistics": ["200", "200", "200"], "lineups": ["200", "200", "200"],
              "incidents": ["200", "200", "200"], "pregame_form": ["200", "200", "404"]},
    "volleyball": {"statistics": ["200", "404", "404"], "lineups": ["404", "404", "404"],
                   "incidents": ["200", "200", "404"], "pregame_form": ["404", "404", "404"]},
    "floorball": {"statistics": ["404", "404", "404"], "lineups": ["-", "-", "-"],
                  "incidents": ["200", "200", "200"], "pregame_form": ["200", "200", "404"]},
    "minifootball": {"statistics": ["200", "200", "404"], "lineups": ["200", "200", "404"],
                     "incidents": ["200", "200", "404"], "pregame_form": ["404", "404", "404"]},
}

# FX-31'in lv-20261009'un kanıtıyla kayıt defterine uyguladıkları: (spor, dilim) → kanıtın yargısı. İsteğe bağlı ya
# da istenmez olan her hücrede uygulama tarafı bitmiş tablo da veri görmedi ("-").
APPLIED: Dict[Tuple[str, str], str] = {
    ("american-football", "pregame_form"): OPTIONAL,  # bitmiş maçta 404 (öteki bitmiş maçta 403)
    ("badminton", "pregame_form"): OPTIONAL,  # bitmiş maçta 404
    ("badminton", "lineups"): ABSENT,  # eksiksiz bitmiş sayfa istemedi
    ("badminton", "point_by_point"): REQUIRED,  # bitmiş maçta veriyle, başlamamış maçta 404
    ("table-tennis", "pregame_form"): OPTIONAL,  # bitmiş maçta 404
    ("table-tennis", "lineups"): ABSENT,  # eksiksiz bitmiş sayfa istemedi
    ("table-tennis", "point_by_point"): REQUIRED,  # bitmiş maçta veriyle, başlamamış maçta 404
    ("baseball", "pregame_form"): OPTIONAL,  # bitmiş ve canlı maçta 404
    ("baseball", "incidents"): ABSENT,  # iki eksiksiz bitmiş ve bir canlı sayfa istemedi
    ("futsal", "incidents"): OPTIONAL,  # bir bitmiş maçta 200 (#121), ötekinde 404
    ("mma", "pregame_form"): OPTIONAL,  # bir bitmiş maçta 200 (#121), ötekinde 404
    ("esports", "esports_games"): REQUIRED,  # bitmiş ve canlı maçta veriyle, başlamamış maçta 404
}
APP_SIDE = ("slice-matrix-finished.txt", "slice-matrix-live.txt")
# Uygulama tarafı tablonun sütun adları → dilim anahtarları
_MATRIX_COLUMNS = {"stat": "statistics", "lin": "lineups", "inc": "incidents", "form": "pregame_form", "h2h": "h2h",
                   "strk": "team_streaks", "pbp": "point_by_point"}

# Kanıttaki, kayıt defterinde dilimi olmayan maç uç noktaları ve neden (tablonun tamamı hesaba katılsın diye)
OTHER_ENDPOINTS: Dict[str, str] = {
    "/": "the event itself (/event/{id}), not a slice",
    "/votes": "fan votes, not match data",
    "/at-bats": "baseball only (200 on finished, live and not-started matches; 404 in every other sport): proposal",
    "/umpires": "baseball only (200 on finished and live matches, 404 on a not-started one): proposal",
    "/weather": "baseball only (200 on finished and live matches, 404 on a not-started one): proposal",
    "/comments": "baseball only (200 on finished and live matches, 404 on a not-started one): proposal",
    "/managers": "team managers (200 in nine team sports, 404 in the rest): proposal, not sport-specific",
    "/best-players": "player ratings (basketball, American football, handball): proposal, not sport-specific",
    "/best-players/summary": "football player ratings: proposal, not sport-specific",
    "/player-of-the-match": "football fan poll: proposal",
    "/average-positions": "football player positions: proposal",
    "/heatmap/{id}": "football player heatmap (one request per player): proposal",
    "/shotmap": "football shot map: proposal",
    "/graph": "momentum graph (football, basketball, American football, handball): proposal",
    "/graph/sequence": "volleyball point sequence (200 on one finished match): proposal",
    "/tennis-power": "tennis momentum (200 on one finished match): proposal",
    "/graph/win-probability": "404 in every sport",
    "/live-match-tracker": "live widget",
    "/highlights": "media (video links)",
    "/official-tweets": "media",
    "/media/summary/country/{cc}": "media, per country",
    "/sport-video-highlights/country/{cc}/extended": "media, per country",
    "/ai-insights/{lang}": "generated text, per language",
    "/ai-insights-postmatch/{lang}": "generated text, per language",
}


def _endpoint(spec: sports.SliceSpec) -> str:
    return spec.path.replace("/event/{event_id}", "")


def _cells() -> List[Tuple[str, sports.SliceSpec, str]]:
    return [(sport, spec, evidence_mod.verdict(EVIDENCE, sport, _endpoint(spec)))
            for sport in sports.sport_slugs() for spec in sports.DETAIL_SLICES]


# --- kanıt tablosu -----------------------------------------------------------------------------------------


@pytest.mark.skipif(not (evidence_mod.RESEARCH / "requests.jsonl").exists(), reason="research data not present")
def test_the_evidence_table_is_derived_from_the_research_data():
    assert json.loads(json.dumps(evidence_mod.derive())) == EVIDENCE


def test_every_endpoint_in_the_evidence_is_a_slice_or_explained():
    paths = {_endpoint(spec) for spec in sports.DETAIL_SLICES}
    seen = {endpoint for by_endpoint in EVIDENCE["answers"].values() for endpoint in by_endpoint}
    assert seen - paths - set(OTHER_ENDPOINTS) == set()
    assert set(OTHER_ENDPOINTS) <= seen  # açıklamalar kanıtta geçen uç noktalar


def test_the_evidence_covers_the_recorded_pages():
    pages = EVIDENCE["pages"]
    # Yokluk kanıtı yalnızca onarılmış koşunun sayfalarından (tests/sport_evidence.py, kural 2)
    assert {row["run_id"] for rows in pages.values() for row in rows} == set(evidence_mod.REPAIRED_RUNS)
    # Eksiksiz yüklenmiş canlı ya da bitmiş maç sayfası: yokluk yalnızca bunlarda kanıttır. Kayıtlı 21 sporun
    # aussie-rules dışında hepsinde var
    complete = sorted(sport for sport, rows in pages.items()
                      if any(row["complete"] and row["state"] in ("finished", "live") for row in rows))
    assert complete == sorted(set(sports.sport_slugs()) - {"aussie-rules"})
    # Aussie rules: yalnızca başlamamış maç sayfası; bandy ve su topu (kayıtlı değil) yalnızca eski koşuda
    assert [row["state"] for row in pages["aussie-rules"]] == ["notstarted"]
    assert "bandy" not in pages and "waterpolo" not in pages
    # Koşunun "başlamamış" diye açtığı ama açıldığında başlamış (ya da bitmiş) maçlar, kendi /event yanıtına göre
    states = {row["event_id"]: row["state"] for rows in pages.values() for row in rows}
    assert (states[16685163], states[16546323], states[17212667], states[17279017]) == ("live", "live", "live",
                                                                                        "finished")
    assert states[16183708] == "notstarted"  # sayfa kapandıktan sonra başladı


# --- kayıt defteri kanıta uyar -------------------------------------------------------------------------------


@pytest.mark.parametrize("sport,spec,found", _cells(), ids=lambda v: getattr(v, "key", v))
def test_the_registry_follows_the_evidence(sport: str, spec: sports.SliceSpec, found: str):
    applies, counts = spec.applies_to(sport), spec.counts_in(sport)
    found = DECIDED.get((sport, spec.key), found)  # sahibin kararı kuraldan ayrılabilir (DECIDED_AGAINST_THE_RULE)
    if (sport, spec.key) in PENDING:  # kural ayrıldı, karar bekleniyor: kayıt defteri bugünkü yerinde kalır
        rule, found = PENDING[(sport, spec.key)]
        assert evidence_mod.verdict(EVIDENCE, sport, _endpoint(spec)) == rule
    if found == REQUIRED:
        assert applies and counts
    elif found == OPTIONAL:
        assert applies and not counts
    elif found == ABSENT:
        assert not applies
    elif spec.sports is None:  # ortak dilim, kanıt yetersiz ya da yalnızca bitmemiş maçta veri: değişmez
        assert applies and counts is spec.required
    elif found == OPEN_DATA:
        assert applies and not counts
    else:
        assert found == UNKNOWN and not applies


def test_no_sport_requests_a_slice_its_match_page_never_asks_for():
    for sport, spec, found in _cells():
        if DECIDED.get((sport, spec.key), found) == ABSENT:
            assert spec.key not in {s.key for s in sports.slices_for(sport)}, (sport, spec.key)
            assert spec.key not in planning.expected_slice_keys(sport, phase="post"), (sport, spec.key)


def _answers(spec: sports.SliceSpec) -> List[str]:
    return [code for by_endpoint in EVIDENCE["answers"].values() for code in by_endpoint.get(_endpoint(spec), ())]


@pytest.mark.parametrize("spec", sports.DETAIL_SLICES, ids=lambda spec: spec.key)
def test_the_phases_follow_the_evidence(spec: sports.SliceSpec):
    """
    Bir dilim başlamamış maçta yalnızca hiçbir başlamamış maç onu veriyle yanıtlamadıysa istenmez. Canlı maç
    evrelerden hiç çıkmaz: canlı kanıt az (lv-20261009'da canlı sayfa yok), canlıdaki yokluk evreyi daraltmaz.
    """
    assert {"live", "post"} <= spec.phases
    if "pre" not in spec.phases:
        assert not [code for code in _answers(spec) if code == "notstarted:200"], spec.key
        assert spec.key in ("statistics", "point_by_point", "esports_games", "innings")


def test_statistics_and_point_by_point_exist_once_the_match_started():
    """lv-20261009: 15 sporun başlamamış 15 maç sayfası istatistik istemedi; point-by-point'e 404 aldı."""
    started = {"finished", "live"}
    for key, codes in (("statistics", set()), ("point_by_point", {"notstarted:404"})):
        answers = _answers(sports.get_slice(key))
        assert {code for code in answers if code.split(":")[0] not in started} == codes, key
    not_started = [(row["run_id"], row["event_id"]) for rows in EVIDENCE["pages"].values() for row in rows
                   if row["state"] == "notstarted" and row["complete"]]
    # lv-20261009b yalnızca bitmiş maç sayfaları açtı; florbolun 16597412 sayfası başlamamış 16597419'un olaylarını,
    # pregame-form'unu ve serilerini de istedi, kural onu da sayfa sayar (istatistik istemedi)
    assert len([cell for cell in not_started if cell[0] == "lv-20261009"]) == 15
    assert [cell for cell in not_started if cell[0] != "lv-20261009"] == [("lv-20261009b", 16597419)]
    assert "statistics" not in planning.expected_slice_keys("football", phase="pre")
    for key in ("statistics", "point_by_point"):
        assert key not in {s.key for s in sports.select_slices("event", "tennis", phase="pre")}
        assert key in {s.key for s in sports.select_slices("event", "tennis", phase="live")}


# --- sahibin kararı (FX-16) ve uygulama tarafı kanıt ----------------------------------------------------------


def _matrix(name: str) -> Dict[str, Dict[str, str]]:
    """Uygulama tarafı tablo: spor → dilim anahtarı → hücre (Y, -, not_, .)."""
    lines = [line.split() for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")]
    header = lines[0]
    assert header[0] == "sport"
    table: Dict[str, Dict[str, str]] = {}
    for row in lines[1:]:
        if row[0] not in sports.sport_slugs():  # açıklama satırı
            continue
        table[row[0]] = {_MATRIX_COLUMNS[column]: cell for column, cell in zip(header[1:], row[1:], strict=True)
                         if column in _MATRIX_COLUMNS}
    return table


def test_the_decided_cells_are_what_the_evidence_says():
    for (sport, key), verdict in DECIDED.items():
        expected = DECIDED_AGAINST_THE_RULE.get((sport, key), verdict)
        assert evidence_mod.verdict(EVIDENCE, sport, _endpoint(sports.get_slice(key))) == expected, (sport, key)
    assert set(DECIDED_AGAINST_THE_RULE) <= set(DECIDED)
    assert all(DECIDED[cell] != verdict for cell, verdict in DECIDED_AGAINST_THE_RULE.items())


def test_the_app_side_evidence_backs_the_decision():
    """Canlı doğrulamanın uygulama tarafı tabloları kararın dayandığı gözlemleri taşır (2026-10-08)."""
    finished, live = (_matrix(name) for name in APP_SIDE)
    assert len(finished) == 20 and len(live) == 13
    # Teniste point_by_point bitmiş ve canlı maçta veriyle: tamlığa girer
    assert finished["tennis"]["point_by_point"] == live["tennis"]["point_by_point"] == "Y"
    # Teniste kadro ve olaylar hiç veriyle gelmedi (bitmiş maçta yok, canlı maçta gövdesiz): istenmez
    for key in ("lineups", "incidents"):
        assert finished["tennis"][key] == "-" and live["tennis"][key] == "not_"
    # pregame_form basketbolda ve teniste bitmiş maçta veri getirmedi, basketbolda canlı maçta getirdi; futbolda
    # iki maçta da getirdi, #121'in üç sayfasında 404, lv-20261009'un iki bitmiş sayfasında 200 aldı
    # (evidence.json): her maçta gelmez, isteğe bağlı
    assert finished["basketball"]["pregame_form"] == finished["tennis"]["pregame_form"] == "-"
    assert live["basketball"]["pregame_form"] == "Y"
    assert finished["football"]["pregame_form"] == live["football"]["pregame_form"] == "Y"


# --- FX-31: lv-20261009'un kanıtı ------------------------------------------------------------------------------


def test_the_applied_cells_are_what_the_evidence_says():
    for (sport, key), verdict in APPLIED.items():
        assert evidence_mod.verdict(EVIDENCE, sport, _endpoint(sports.get_slice(key))) == verdict, (sport, key)
    assert not set(APPLIED) & set(DECIDED)


def test_the_app_side_evidence_saw_no_data_where_the_page_evidence_says_optional_or_absent():
    """Uygulama tarafı bitmiş tablo (2026-10-07) uygulanan her isteğe bağlı ya da istenmez hücrede "-"."""
    finished, _live = (_matrix(name) for name in APP_SIDE)
    for (sport, key), verdict in APPLIED.items():
        if verdict != REQUIRED:
            assert finished[sport][key] == "-", (sport, key)


# --- FX-36: sahibin ragbi, voleybol, florbol ve mini futbol kararı (lv-20261009b) ------------------------------


def _top_league_answers() -> Dict[str, Dict[str, List[str]]]:
    """lv-20261009b'nin bitmiş maç sayfalarında sayfanın kendi maçının dört dilimine aldığı kodlar."""
    keys = {"/statistics": "statistics", "/lineups": "lineups", "/incidents": "incidents",
            "/pregame-form": "pregame_form"}
    pages: Dict[Tuple[str, str], Dict[str, set]] = {}
    with open(evidence_mod.RESEARCH / "requests.jsonl", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("run_id") != "lv-20261009b" or row.get("page_type") != "event-finished":
                continue
            page_id = row["page"].rsplit("#id:", 1)[-1]
            pages.setdefault((row["sport"], page_id), {})
            path = row["url"].split("?", 1)[0].split("/api/v1/event/", 1)[-1]
            event_id, _, suffix = path.partition("/")
            if event_id == page_id and "/" + suffix in keys and row.get("status") is not None:
                pages[(row["sport"], page_id)].setdefault(keys["/" + suffix], set()).add(str(row["status"]))
    table: Dict[str, Dict[str, List[str]]] = {}
    for (sport, _page_id), by_key in pages.items():
        for key in keys.values():
            codes = by_key.get(key, {"-"})
            assert len(codes) == 1, (sport, key, codes)  # GET ve HEAD aynı kodu aldı
            table.setdefault(sport, {}).setdefault(key, []).extend(codes)
    return {sport: {key: sorted(codes) for key, codes in by_key.items()} for sport, by_key in table.items()}


@pytest.mark.skipif(not (evidence_mod.RESEARCH / "requests.jsonl").exists(), reason="research data not present")
def test_the_top_league_pages_are_the_decisions_evidence():
    assert _top_league_answers() == TOP_LEAGUE_ANSWERS


def test_the_decision_follows_the_top_league_answers():
    """
    Üst liglerin bitmiş maçlarında bir 404 ya da hiç veri yoksa isteğe bağlı; hiç istenmediyse istenmez; üçünde
    de veriyle ve lv-20261009'un bitmiş maçında 404'se de isteğe bağlı (ragbi). Florbol olayları ayrık: üçünde
    veriyle, tamlığa girer.
    """
    for sport, by_key in TOP_LEAGUE_ANSWERS.items():
        for key, codes in by_key.items():
            decided = DECIDED.get((sport, key))
            if key == "lineups" and sport == "minifootball":  # FX-31'den beri isteğe bağlı
                assert decided is None and APPLIED.get((sport, key)) is None
                decided = OPTIONAL
            if set(codes) == {"-"}:
                assert decided == ABSENT, (sport, key)
            elif "404" in codes:
                assert decided == OPTIONAL, (sport, key)
            else:
                assert decided in (OPTIONAL, REQUIRED), (sport, key)
            spec = sports.get_slice(key)
            assert spec.applies_to(sport) is (decided != ABSENT)
            assert spec.counts_in(sport) is (decided == REQUIRED)
    assert [cell for cell, verdict in DECIDED.items() if cell[0] in TOP_LEAGUE_ANSWERS and verdict == REQUIRED] \
        == [("floorball", "incidents")]


def test_the_app_side_evidence_and_the_four_sport_decision():
    """
    Uygulama tarafı bitmiş tabloda (2026-10-07; spor başına bir, büyük olasılıkla alt lig maçı) kararın isteğe bağlı
    ya da istenmez dediği hücrelerin çoğu "-". Ragbi pregame_form ve voleybol olayları orada veriyle geldi: üst
    liglerde de bir maçta 404, isteğe bağlı. Florbol olayları orada veri getirmedi, ama tamlığa girer (sahibin kararı).
    """
    finished, _live = (_matrix(name) for name in APP_SIDE)
    with_data = {(sport, key) for (sport, key), verdict in DECIDED.items()
                 if sport in TOP_LEAGUE_ANSWERS and verdict != REQUIRED and finished[sport][key] != "-"}
    assert with_data == {("rugby", "pregame_form"), ("volleyball", "incidents")}
    assert finished["floorball"]["incidents"] == "-"


# --- FX-37: canlı ve bitmiş, aynı maç (lv-20261009c, lv-20261009d) ---------------------------------------------

LIVE_RUN, FINISHED_RUN = "lv-20261009c", "lv-20261009d"

# lv-20261009d'nin bitmiş sayfalarındaki 13 maç (spor başına bir), lv-20261009c'de canlı sayfa olarak da açıldı:
# spor → (maç, dilim → sayfanın kendi maçı için iki ziyarette de aldığı HTTP kodu). Sayfanın hiç istemediği dilim
# tabloda yok; iki ziyaret aynı dilimleri istedi.
SAME_MATCH: Dict[str, Tuple[int, Dict[str, str]]] = {
    "badminton": (17289295, {"statistics": "200", "team_streaks": "200", "pregame_form": "404", "h2h": "200",
                             "incidents": "200", "point_by_point": "200"}),
    "basketball": (17220735, {"statistics": "200", "team_streaks": "200", "pregame_form": "404", "h2h": "200",
                              "lineups": "404", "incidents": "200"}),
    "cricket": (15884178, {"statistics": "404", "pregame_form": "404", "lineups": "200", "incidents": "200",
                           "innings": "200"}),
    "darts": (17278965, {"statistics": "200", "team_streaks": "200", "pregame_form": "404", "h2h": "200",
                         "point_by_point": "200"}),
    "esports": (17280904, {"statistics": "404", "team_streaks": "200", "pregame_form": "404", "h2h": "200",
                           "lineups": "200", "esports_games": "200"}),
    "football": (16653102, {"statistics": "200", "pregame_form": "200", "lineups": "200", "incidents": "200"}),
    "futsal": (16982330, {"statistics": "200", "team_streaks": "200", "pregame_form": "200", "h2h": "200",
                          "lineups": "404", "incidents": "200"}),
    "handball": (16642082, {"statistics": "200", "pregame_form": "404", "lineups": "200", "incidents": "200"}),
    "ice-hockey": (16347609, {"statistics": "200", "pregame_form": "200", "lineups": "200", "incidents": "200"}),
    "minifootball": (17063783, {"statistics": "200", "team_streaks": "200", "pregame_form": "404", "h2h": "200",
                                "lineups": "404", "incidents": "200"}),
    "table-tennis": (17289647, {"statistics": "200", "team_streaks": "200", "pregame_form": "404", "h2h": "200",
                                "incidents": "200", "point_by_point": "200"}),
    "tennis": (17218936, {"statistics": "200", "team_streaks": "200", "pregame_form": "404", "h2h": "200",
                          "point_by_point": "200"}),
    "volleyball": (17186419, {"statistics": "404", "team_streaks": "200", "pregame_form": "200", "h2h": "200",
                              "lineups": "404", "incidents": "200"}),
}
# Kayıt defterinde dilimi olmayan ve aynı maçta canlıdan bitmişe değişen uç noktalar: (spor, uç nokta) →
# (canlı, bitmiş); "-": o ziyaret istemedi. /live-match-tracker yalnızca canlı sayfada istenir (11 maçın hepsinde).
SAME_MATCH_OTHER: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("football", "/ai-insights/{lang}"): ("200", "-"),
    ("football", "/ai-insights-postmatch/{lang}"): ("-", "200"),
    ("football", "/highlights"): ("404", "200"),
    ("football", "/media/summary/country/{cc}"): ("404", "200 404"),
    ("football", "/sport-video-highlights/country/{cc}/extended"): ("-", "404"),
    ("ice-hockey", "/highlights"): ("404", "200"),
    ("ice-hockey", "/media/summary/country/{cc}"): ("404", "200"),
    ("ice-hockey", "/sport-video-highlights/country/{cc}/extended"): ("-", "404"),
    ("volleyball", "/graph/sequence"): ("200", "404"),
}
# lv-20261009c'nin "canlı" diye açtığı dart ve masa tenisi sayfalarının maçı sayfa açıldığında bitmişti (maçın kendi
# /event/{id} yanıtı): bu iki sporda iki ziyaret de bitmiş maçtır
FINISHED_ON_THE_LIVE_VISIT = ("darts", "table-tennis")


def _own_page_answers(run_ids: Tuple[str, ...]) -> Dict[Tuple[str, str, int], Dict[str, set]]:
    """(koşu, spor, sayfa) → uç nokta → HTTP kodları: maç sayfasının kendi maçı için aldıkları (oranlar hariç)."""
    pages: Dict[Tuple[str, str, int], Dict[str, set]] = {}
    with open(evidence_mod.RESEARCH / "requests.jsonl", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("run_id") not in run_ids or not str(row.get("page_type") or "").startswith("event"):
                continue
            page_id = int(row["page"].rsplit("#id:", 1)[-1])
            found = evidence_mod._endpoint(row["url"])
            if found is None or found[0] != page_id or row.get("status") is None:
                continue
            if evidence_mod._ODDS.search(found[1]):
                continue
            pages.setdefault((row["run_id"], row["sport"], page_id), {}).setdefault(found[1], set()).add(
                str(row["status"]))
    return pages


def _finished_page_ids() -> Dict[int, str]:
    """lv-20261009d'nin bitmiş maç sayfaları: maç → spor (sayfa adresinin #id:'si)."""
    ids: Dict[int, str] = {}
    with open(evidence_mod.RESEARCH / "requests.jsonl", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("run_id") == FINISHED_RUN and row.get("page_type") == "event-finished":
                ids[int(row["page"].rsplit("#id:", 1)[-1])] = row["sport"]
    return ids


@pytest.mark.skipif(not (evidence_mod.RESEARCH / "requests.jsonl").exists(), reason="research data not present")
def test_no_slice_answers_live_but_not_once_the_same_match_finished():
    ids = _finished_page_ids()
    assert ids == {event_id: sport for sport, (event_id, _codes) in SAME_MATCH.items()}
    answers = _own_page_answers((LIVE_RUN, FINISHED_RUN))
    paths = {_endpoint(spec): spec.key for spec in sports.DETAIL_SLICES}
    other: Dict[Tuple[str, str], Tuple[str, str]] = {}
    for event_id, sport in ids.items():
        live, finished = answers[(LIVE_RUN, sport, event_id)], answers[(FINISHED_RUN, sport, event_id)]
        slices_seen = {}
        for endpoint in sorted(set(live) | set(finished)):
            pair = tuple(" ".join(sorted(codes.get(endpoint, {"-"}))) for codes in (live, finished))
            if endpoint in paths:
                # Kayıt defterinin hiçbir dilimi canlıda 200 alıp bitmişte başka bir şey almadı
                assert not (pair[0] == "200" and pair[1] != "200"), (sport, endpoint, pair)
                assert pair[0] == pair[1], (sport, endpoint, pair)
                slices_seen[paths[endpoint]] = pair[1]
            elif pair[0] != pair[1]:
                other[(sport, endpoint)] = pair
        assert slices_seen == SAME_MATCH[sport][1], sport
    lmt = "/live-match-tracker"
    assert {cell for cell, pair in other.items() if cell[1] == lmt and pair[1] == "-"} == {
        (sport, lmt) for sport in SAME_MATCH if sport not in FINISHED_ON_THE_LIVE_VISIT}
    assert {cell: pair for cell, pair in other.items() if cell[1] != lmt} == SAME_MATCH_OTHER


def test_the_same_match_was_live_then_finished():
    """Maçın isteğin anındaki durumu (evidence.json): 11 maç ilk ziyarette canlı, 13'ü ikincide bitmiş."""
    states = {(row["run_id"], row["event_id"]): row["state"] for rows in EVIDENCE["pages"].values() for row in rows}
    for sport, (event_id, _codes) in SAME_MATCH.items():
        assert states[(FINISHED_RUN, event_id)] == "finished", sport
        expected = "finished" if sport in FINISHED_ON_THE_LIVE_VISIT else "live"
        assert states[(LIVE_RUN, event_id)] == expected, sport


def test_the_live_pages_back_the_not_in_rows():
    """
    Tam canlı sayfaların hiçbiri kadro istemedi: tenis, badminton, MMA, florbol; olay istemedi: tenis, MMA, e-spor.
    Dart ve masa tenisinin lv-20261009c sayfaları bitmiş maçtı (FINISHED_ON_THE_LIVE_VISIT); onlar da istemedi.
    """
    live_pages = [(row["run_id"], sport, row["event_id"]) for sport, rows in EVIDENCE["pages"].items() for row in rows
                  if row["run_id"] in (LIVE_RUN, FINISHED_RUN) and row["state"] == "live" and row["complete"]]
    answers = _own_page_answers((LIVE_RUN, FINISHED_RUN))
    asked = collections.defaultdict(set)
    for run_id, sport, event_id in live_pages:
        asked[sport] |= set(answers.get((run_id, sport, event_id), {}))
    assert {sport for sport in asked if "/lineups" not in asked[sport]} == {"tennis", "badminton", "mma", "floorball"}
    assert {sport for sport in asked if "/incidents" not in asked[sport]} == {"tennis", "mma", "esports"}
    for sport in ("tennis", "badminton", "mma", "floorball"):
        assert not sports.get_slice("lineups").applies_to(sport), sport
    for sport in ("tennis", "mma", "esports"):
        assert not sports.get_slice("incidents").applies_to(sport), sport
    for sport, never in (("darts", {"/lineups", "/incidents"}), ("table-tennis", {"/lineups"})):
        visited = [codes for key, codes in answers.items() if key[0] == LIVE_RUN and key[1] == sport]
        assert len(visited) == 2 and not any(never & set(codes) for codes in visited), sport


# --- FX-37: kuralın kayıt defterinden ayrıldığı hücreler, sahibin kararını bekler ------------------------------


def test_the_pending_cells_are_what_the_evidence_says():
    for (sport, key), (rule, registry) in PENDING.items():
        spec = sports.get_slice(key)
        assert evidence_mod.verdict(EVIDENCE, sport, _endpoint(spec)) == rule != registry, (sport, key)
        assert spec.applies_to(sport) and spec.counts_in(sport) is (registry == REQUIRED)
    assert not set(PENDING) & (set(DECIDED) | set(APPLIED))


@pytest.mark.skipif(not (evidence_mod.RESEARCH / "requests.jsonl").exists(), reason="research data not present")
def test_the_pending_cells_answers():
    """Bekleyen hücrelerin lv-20261009c/d'deki 200 dışı yanıtları; maçın durumu isteğin anındaki durum."""
    events = evidence_mod._Events(evidence_mod.RESEARCH)
    found: Dict[Tuple[str, str], set] = {cell: set() for cell in PENDING}
    endpoints = {_endpoint(sports.get_slice(key)): key for _sport, key in PENDING}
    with open(evidence_mod.RESEARCH / "requests.jsonl", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row.get("run_id") not in (LIVE_RUN, FINISHED_RUN) or row.get("status") in (None, 200):
                continue
            if not str(row.get("page_type") or "").startswith("event"):
                continue
            hit = evidence_mod._endpoint(row["url"])
            if hit is None or hit[1] not in endpoints:
                continue
            sport, state = events.at(hit[0], row["run_id"], float(row["ts"]), row["sport"])
            if (sport, endpoints[hit[1]]) in found:
                found[(sport, endpoints[hit[1]])].add(
                    (row["run_id"], int(row["page"].rsplit("#id:", 1)[-1]), hit[0], state, str(row["status"])))
    assert found == {cell: set(rows) for cell, rows in PENDING_ANSWERS.items()}


@pytest.mark.parametrize("sport", ["football", "basketball", "tennis"])
def test_pregame_form_is_requested_but_not_awaited_in_the_main_sports(sport: str):
    spec = sports.get_slice("pregame_form")
    assert spec.applies_to(sport) and not spec.counts_in(sport)
    assert "pregame_form" in sports.get_sport(sport).detail_slices
    assert "pregame_form" not in planning.expected_slice_keys(sport, phase="post")


@pytest.mark.parametrize("sport,expected,counted", [
    # FX-16: pregame_form istenir ama tamlığa girmez; teniste kadro ve olaylar istenmez, point_by_point tamlığa girer
    ("football", COMMON_KEYS, ("statistics", "team_streaks", "h2h", "lineups", "incidents")),
    ("basketball", COMMON_KEYS, ("statistics", "team_streaks", "h2h", "lineups", "incidents")),
    ("tennis", ("statistics", "team_streaks", "pregame_form", "h2h", "point_by_point"),
     ("statistics", "team_streaks", "h2h", "point_by_point")),
    ("ice-hockey", COMMON_KEYS, ("statistics", "team_streaks", "h2h", "lineups", "incidents")),
    ("futsal", COMMON_KEYS, ("team_streaks", "h2h")),
    # FX-36: ragbi ve voleybolda dört dilim isteğe bağlı; florbolda kadro istenmez, olaylar tamlığa girer; mini
    # futbolda yalnızca seriler ve h2h tamlığa girer
    ("minifootball", COMMON_KEYS, ("team_streaks", "h2h")),
    ("rugby", COMMON_KEYS, ("team_streaks", "h2h")),
    ("volleyball", COMMON_KEYS, ("team_streaks", "h2h")),
    ("floorball", ("statistics", "team_streaks", "pregame_form", "h2h", "incidents"),
     ("team_streaks", "h2h", "incidents")),
    ("padel", ("statistics", "team_streaks", "pregame_form", "h2h"), ("team_streaks", "h2h")),
    ("snooker", ("statistics", "team_streaks", "pregame_form", "h2h"), ("team_streaks", "h2h")),
    ("cricket", COMMON_KEYS + ("innings",), ("team_streaks", "h2h", "lineups", "incidents", "innings")),
    ("esports", ("statistics", "team_streaks", "pregame_form", "h2h", "lineups", "esports_games"),
     ("team_streaks", "h2h", "lineups", "esports_games")),
    ("darts", ("statistics", "team_streaks", "pregame_form", "h2h", "point_by_point"),
     ("statistics", "team_streaks", "h2h", "point_by_point")),
    ("mma", ("statistics", "team_streaks", "pregame_form", "h2h"), ("statistics", "team_streaks", "h2h")),
    # FX-31 (lv-20261009): pregame_form isteğe bağlı; badminton ve masa tenisinde kadro istenmez, point_by_point
    # tamlığa girer; beyzbolda olaylar istenmez
    ("american-football", COMMON_KEYS, ("statistics", "team_streaks", "h2h", "lineups", "incidents")),
    ("badminton", ("statistics", "team_streaks", "pregame_form", "h2h", "incidents", "point_by_point"),
     ("statistics", "team_streaks", "h2h", "incidents", "point_by_point")),
    ("table-tennis", ("statistics", "team_streaks", "pregame_form", "h2h", "incidents", "point_by_point"),
     ("statistics", "team_streaks", "h2h", "incidents", "point_by_point")),
    ("baseball", ("statistics", "team_streaks", "pregame_form", "h2h", "lineups"),
     ("statistics", "team_streaks", "h2h", "lineups")),
    # kanıt bugünkü altı dilime uyuyor (hentbol) ya da yetersiz (aussie-rules: yalnızca başlamamış maç)
    ("aussie-rules", COMMON_KEYS, COMMON_KEYS),
    ("handball", COMMON_KEYS, COMMON_KEYS),
    # kayıtlı olmayan ya da bilinmeyen spor: bugünkü altı dilim
    ("waterpolo", COMMON_KEYS, COMMON_KEYS),
    ("", COMMON_KEYS, COMMON_KEYS),
    (None, COMMON_KEYS, COMMON_KEYS),
])
def test_slices_and_completeness_per_sport(sport, expected, counted):
    assert tuple(s.key for s in sports.slices_for(sport)) == expected
    assert tuple(s.key for s in sports.slices_for(sport, required_only=True)) == counted
    assert planning.expected_slice_keys(sport, phase="post") == counted


def test_innings_exist_once_the_match_started():
    assert "innings" not in planning.expected_slice_keys("cricket", phase="pre")
    assert "innings" not in {s.key for s in sports.select_slices("event", "cricket", phase="pre")}
    assert "innings" in planning.expected_slice_keys("cricket", phase="live")


def test_slice_spec_checks_the_per_sport_sets():
    with pytest.raises(ValueError):
        sports.SliceSpec("x", "/event/{event_id}/x", not_in=frozenset({"padel"}), optional_in=frozenset({"padel"}))
    with pytest.raises(ValueError):
        sports.SliceSpec("x", "/event/{event_id}/x", required=False, optional_in=frozenset({"padel"}))
    with pytest.raises(ValueError):
        sports.SliceSpec("x", "/event/{event_id}/x", sports=frozenset({"tennis"}), not_in=frozenset({"padel"}))
    spec = sports.SliceSpec("x", "/event/{event_id}/x", not_in=frozenset({"padel"}),
                            optional_in=frozenset({"futsal"}))
    assert (spec.applies_to("padel"), spec.counts_in("padel")) == (False, False)
    assert (spec.applies_to("futsal"), spec.counts_in("futsal")) == (True, False)
    assert (spec.applies_to(None), spec.counts_in(None)) == (True, True)


# --- yeni dilimlerin "veri var mı" kuralı, kayıtlı yanıtlarla --------------------------------------------------


def _recorded(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))["body"]


def test_cricket_innings_recorded_answer_has_data():
    body = _recorded("cricket_innings__16526539.json")
    assert slices.slice_body_state("innings", body) == BODY_DATA
    assert slices.has_innings_data_dict({"innings": body})
    assert slices.slice_body_state("innings", {"error": {"code": 404, "message": "Not Found"}}) == BODY_NO_DATA


def test_darts_point_by_point_recorded_answer_has_data():
    body = _recorded("darts_point_by_point__17099318.json")
    assert [leg_set["set"] for leg_set in body["pointByPoint"]] and "legs" in body["pointByPoint"][0]
    assert slices.slice_body_state("point_by_point", body) == BODY_DATA
    assert slices.slice_body_state("point_by_point", {"pointByPoint": []}) == BODY_NO_DATA


# --- boru hattı her sporda tam olarak kayıtlı dilimleri ister ------------------------------------------------


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    pin_default_settings(monkeypatch)


@pytest.fixture
def fake() -> Iterator[FakeSofaScore]:
    with FakeSofaScore.from_file(WORLD) as world:
        yield world


def _finished_event(event_id: int, sport: str) -> Dict[str, Any]:
    return {
        "id": event_id,
        "tournament": {"name": "Cup", "uniqueTournament": {"id": 77, "name": "Cup"},
                       "category": {"id": 1, "name": "World", "sport": {"name": sport, "slug": sport}}},
        "season": {"id": 5, "name": "Cup 26", "year": "26"},
        "homeTeam": {"id": 1, "name": "Home"}, "awayTeam": {"id": 2, "name": "Away"},
        "status": {"code": 100, "description": "Ended", "type": "finished"},
        "startTimestamp": 1790000000,
    }


# Veri taşıyan en küçük gövdeler (tests/test_slices.py PRESENT ile aynı biçim)
_BODIES: Dict[str, Any] = {
    "statistics": {"statistics": [{"period": "ALL", "groups": [{"statisticsItems": [{"key": "shots"}]}]}]},
    "team_streaks": {"general": [{"name": "Wins", "value": "3"}]},
    "pregame_form": {"homeTeam": {"form": ["W"]}, "awayTeam": {"form": ["L"]}},
    "h2h": {"teamDuel": {"homeWins": 1, "awayWins": 0, "draws": 0}},
    "lineups": {"home": {"players": [{"player": {"id": 1}}]}, "away": {"players": []}},
    "incidents": {"incidents": [{"incidentType": "goal"}]},
    "point_by_point": {"pointByPoint": [{"games": []}]},
    "esports_games": {"games": [{"id": 1, "status": {"code": 100, "type": "finished"}, "winnerCode": 1}]},
    "innings": {"innings": [{"number": 1, "score": 120, "wickets": 3, "overs": 20}]},
}


def _run(store: Any, event_ids: List[int]) -> Any:
    items = [planning.WorkItem(Ref.event(event_id), "full", (), None, "test") for event_id in event_ids]
    return FetchPipeline(store, concurrency=5).run_sync(items)


@pytest.mark.parametrize("sport", sports.sport_slugs())
def test_the_pipeline_requests_exactly_the_registered_slices(sport: str, fake: FakeSofaScore, tmp_path: Path):
    event_id = 9700000 + sports.sport_slugs().index(sport)
    fake.add(f"/event/{event_id}", {"event": _finished_event(event_id, sport)})
    # Her kayıtlı uç nokta veriyle yanıtlar: istenmeyen bir dilim isteği yine de kayda düşer
    for spec in sports.DETAIL_SLICES:
        fake.add(spec.path.format(event_id=event_id), _BODIES[spec.key])
    store = open_store(tmp_path / "data")

    [result] = _run(store, [event_id]).results

    assert result.ok
    requested = sorted(r.path for r in fake.requests if r.path not in (SITE_ROOT, f"/event/{event_id}"))
    assert requested == sorted(spec.path.format(event_id=event_id) for spec in sports.slices_for(sport))
    assert list(result.slices) == [spec.key for spec in sports.slices_for(sport)]


@pytest.mark.parametrize("sport", sports.sport_slugs())
def test_a_finished_match_without_its_optional_slices_is_complete_after_one_fetch(sport: str, fake: FakeSofaScore,
                                                                                 tmp_path: Path):
    """Tamlığa girmeyen dilimler 404 alınca maç yeniden doldurulmaz: bitmiş maç başına istek 1 + dilim sayısı."""
    event_id = 9800000 + sports.sport_slugs().index(sport)
    fake.add(f"/event/{event_id}", {"event": _finished_event(event_id, sport)})
    for spec in sports.slices_for(sport, required_only=True):
        fake.add(spec.path.format(event_id=event_id), _BODIES[spec.key])
    store = open_store(tmp_path / "data")

    _run(store, [event_id])

    sent = [r for r in fake.requests if r.path != SITE_ROOT]
    assert len(sent) == 1 + len(sports.slices_for(sport))
    policy = RefreshPolicy(now=1790000000 + 86400 * 30, window_s=0, min_interval_s=0)
    assert planning.plan_items(store, [event_id], policy) == []


# --- Store: her spor kendi beklediği dilimlere bakılarak eksik sayılır ---------------------------------------


def _put(store: Any, event_id: int, sport: str, keys: Tuple[str, ...]) -> None:
    outcomes = {"event": Outcome(SLICE_OK, _finished_event(event_id, sport))}
    outcomes.update({key: Outcome(SLICE_OK, _BODIES[key]) for key in keys})
    store.events.put(event_id, outcomes)


def test_missing_uses_each_sports_own_required_slices(tmp_path: Path):
    store = open_store(tmp_path / "data")
    _put(store, 1, "padel", ("team_streaks", "h2h"))  # padel: kadro ve olaylar yok, öteki ikisi isteğe bağlı
    _put(store, 2, "football", ("statistics", "team_streaks", "pregame_form", "h2h", "incidents"))
    _put(store, 3, "cricket", ("team_streaks", "h2h", "lineups", "incidents"))  # innings eksik
    _put(store, 4, "waterpolo", ("team_streaks", "h2h"))  # kayıtlı değil: ortak altı dilim

    rows = {row.event_id: row.missing_keys
            for row in store.events.missing(None, required_detail_keys(), exclusive=True)}

    assert rows == {2: ("lineups",), 3: ("innings",), 4: ("statistics", "pregame_form", "lineups", "incidents")}
    # exclusive olmadan "" her spora eklenir (eski anlam): padel de ortak dilimleri bekler
    union = {row.event_id for row in store.events.missing(None, {"": COMMON_KEYS})}
    assert union == {1, 2, 3, 4}


def test_required_detail_keys_lists_every_registered_sport():
    required = required_detail_keys()
    assert required[""] == COMMON_KEYS
    assert set(required) == {""} | set(sports.sport_slugs())
    for sport in sports.sport_slugs():
        assert required[sport] == tuple(s.key for s in sports.slices_for(sport, required_only=True))
