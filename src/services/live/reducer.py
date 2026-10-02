"""
Canlı indirgeyici: bir maçın son bilinen durumu + yeni gözlem → yeni durum + olaylar
(docs/design/02-services.md bölüm 8.1). Saf işlevdir: ağa, depoya ve saate dokunmaz; anı gözlem taşır.

Kural 2.x izleyicisinin `MatchWatcher._observe` yöntemidir (bugün `src/watcher.py` bunu çağırır):

  * Durum sınıfı değiştiyse `status_changed` (ilk `completed`te `provisional`: başlangıç + REFRESH_WINDOW_HOURS
    dolmadıysa sonuç geçicidir, 03-A);
  * maç önceki gözlemde de canlıydı ve başlık skoru değiştiyse `score_changed`;
  * başlangıçtan (tenisde gerçek oyun başlangıcından) sporun eşiği kadar sonra hâlâ canlı ya da başlamamışsa
    bir kez `stuck`.

Olaylar saklanan durumla gözlem arasındaki farktan çıkar, varsayılan bir önceki kareden değil: kaynak ara
durumları kaçırabilir (push bağlantısı yaklaşık 30 dakikada bir düşer; yoklama iki tur arasında biten maçı
görmeyebilir). Bu yüzden durum sınıfı atlayabilir (canlı → bitti, başlamadı → bitti) ve skor birden çok adım
ilerleyebilir; her biri tek olaydır.

Bu modül olayları iki biçimde verir:

  * `reduce`: 2.x olay sözlükleri (`watch_events.jsonl` satırı ve `--watch`'ın stdout satırı bu biçimdedir);
  * `stream_event`: aynı olayın `live` akışındaki hali (tür öneki, `data` ve yinelenme anahtarı).

`live` akışındaki `data` (04-schema-v1.md, "LiveEvent data"; P23 ile kesinleşti):

    live.status_changed  from, to (durum sınıfları), change_ts, provisional (ilk completed'te true/false,
                         öteki geçişlerde null), score (şemanın Score yapısı)
    live.score_changed   from, to (her biri {home, away}: başlık skoru), change_ts, score
    live.stuck           status_class, start_utc (eşiğin sayıldığı başlangıç)

Yinelenme anahtarı (`dedup_key`) maç, tür ve geçişten kurulur: `status_changed` ve `score_changed` için
`<id>:<tür>:<önce>><sonra>:<change_ts>`, `stuck` için `<id>:stuck:<başlangıç>`. Aynı geçişi iki kaynak
(push ve yoklama) ya da yeniden başlatılan servis gördüğünde olay bir kez saklanır. `change_ts` yoksa yerine
`-` yazılır: aynı iki sınıf arasındaki aynı skorlu ikinci geçiş o zaman yinelenme sayılır.
"""
from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from src.refresh import refresh_window_hours
from src.sports import watcher_params
from src.status import StatusClass, classify_status, extract_scores

logger = logging.getLogger(__name__)

LIVE_STREAM = "live"
STATUS_CHANGED = "status_changed"
SCORE_CHANGED = "score_changed"
STUCK = "stuck"
VIA_LIST = "live"  # olayı canlı liste gösterdi
VIA_EVENT = "event"  # olayı maç sayfası (/event/{id}) gösterdi

TERMINAL: Tuple[StatusClass, ...] = (StatusClass.COMPLETED, StatusClass.DECIDED_WITHOUT_PLAY)
TERMINAL_CLASSES: Tuple[str, ...] = tuple(cls.value for cls in TERMINAL)

LiveState = Dict[str, Any]  # bir maçın son bilinen durumu (store.watch satırı)


@dataclass(frozen=True)
class Observation:
    """
    Bir kaynağın bir maçı gördüğü an.

    event  maç nesnesi (canlı listedeki ya da /event yanıtındaki); `id` taşır
    via    hangi istek gösterdi: "live" (canlı liste) ya da "event" (maç sayfası)
    at     gözlem anı (epoch saniye, UTC)
    """

    event: Mapping[str, Any]
    via: str
    at: float


# --- bitişe yakınlık ve oyun başlangıcı ------------------------------------------------------------


def _near_end_football_minute(event: Mapping[str, Any], now: float) -> bool:
    """2. yarı ≥ 80. dk ya da injuryTime2 görüldü; uzatma/penaltı kodları."""
    code = (event.get("status") or {}).get("code")
    t = event.get("time") or {}
    if code in (6, 31):
        return False
    if code == 7:
        if t.get("injuryTime2") is not None:
            return True
        start = t.get("currentPeriodStartTimestamp")
        if not start:
            return False
        minute = ((t.get("initial") or 2700) + now - start) / 60
        return minute >= 80
    if code == 20:  # periyot bilgisi yok: başlangıçtan 95 dk (80 + devre arası)
        start = event.get("startTimestamp") or now
        return now - start >= 95 * 60
    return True  # uzatma, penaltılar ve bilinmeyen canlı kodlar


def _near_end_played_ratio(event: Mapping[str, Any], now: float) -> bool:
    """Son periyot (4. çeyrek ya da 2. yarı, uzatma) ve played ≥ %90 (played yoksa yalnız periyot)."""
    code = (event.get("status") or {}).get("code")
    t = event.get("time") or {}
    regulation = (t.get("periodLength") or 0) * (t.get("totalPeriodCount") or 0)
    played = t.get("played")
    if played is not None and regulation:
        # %90 ancak son periyotta (4. çeyrek / 2. yarı) ya da uzatmada aşılır
        return played >= 0.9 * regulation
    # Saat verisi yok (K7: çoğu alt lig): yalnızca periyot kodu; 16, uzatma ve bilinmeyen canlı kodlar
    return code not in (13, 14, 15, 30, 31)


def _near_end_last_set(event: Mapping[str, Any], now: float) -> bool:
    """Son set (defaultPeriodCount, yoksa 3)."""
    code = (event.get("status") or {}).get("code")
    sets = event.get("defaultPeriodCount") or 3
    return isinstance(code, int) and 8 <= code <= 12 and code - 7 >= sets


# Kural adı → kural; hangi sporun hangisini kullandığı src/sports.py'de (WatcherParams.near_end_rule).
# Tabloda olmayan ad ("never") ve kayıtlı olmayan spor: bitişe yakın sayılmaz.
NEAR_END_RULES: Dict[str, Callable[[Mapping[str, Any], float], bool]] = {
    "football_minute": _near_end_football_minute,
    "played_ratio": _near_end_played_ratio,
    "last_set": _near_end_last_set,
}


def near_end(event: Mapping[str, Any], sport: str, now: float) -> bool:
    """
    Bitişe yakın mı (maç sayfası da izlenir). Kuralı sporun kayıt defteri girdisi seçer:
      futbol: 2. yarı ≥ 80. dk ya da injuryTime2 görüldü; uzatma/penaltı kodları
      basketbol: son periyot (4. çeyrek ya da 2. yarı, uzatma) ve played ≥ %90 (played yoksa yalnız periyot)
      tenis: son set (defaultPeriodCount, yoksa 3)
    """
    rule = NEAR_END_RULES.get(watcher_params(sport).near_end_rule)
    return rule(event, now) if rule else False


def play_start(event: Mapping[str, Any]) -> Optional[float]:
    """
    Gerçek oyun başlangıcı (takılı maç süresi buradan sayılan sporlarda; bugün yalnızca tenis)
    = currentPeriodStartTimestamp − biten setlerin süreleri (time.periodN, sn);
    set süreleri yoksa currentPeriodStartTimestamp. Zaman bilgisi yoksa None (çağıran startTimestamp'e düşer).
    time.periodN oyun süresidir, yağmur arası gibi duraklamalar dahil değildir; bu yüzden hesaplanan başlangıç
    gerçek olandan geç çıkabilir ve stuck olayı biraz geç tetiklenir (zararsız yönde hata).
    """
    t = event.get("time") or {}
    current = t.get("currentPeriodStartTimestamp")
    if not isinstance(current, (int, float)) or not current:
        return None
    done = sum(v for k, v in t.items() if k.startswith("period") and k[6:].isdigit() and isinstance(v, (int, float)))
    return current - done


def score_key(event: Mapping[str, Any]) -> List[Any]:
    """Canlı skor karşılaştırması: display (futbolda current penaltıları içerir), yoksa current."""
    h, a = event.get("homeScore") or {}, event.get("awayScore") or {}
    return [h.get("display", h.get("current")), a.get("display", a.get("current"))]


def _utc(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds")


def tournament_of(event: Mapping[str, Any]) -> Any:
    return ((event.get("tournament") or {}).get("uniqueTournament") or {}).get("id")


# --- indirgeyici -----------------------------------------------------------------------------------


def reduce(state: Optional[Mapping[str, Any]], obs: Observation, sport: str, *,
           window_hours: Optional[Callable[[], float]] = None) -> Tuple[LiveState, List[Dict[str, Any]]]:
    """
    Maçın durumu ve yeni gözlem → (yeni durum, 2.x olayları). Verilen durum değiştirilmez.

    state         maçın saklanan durumu; None ya da boş: maç ilk kez görülüyor (önceki sınıf bilinmez,
                  geçiş olayı üretilmez)
    window_hours  REFRESH_WINDOW_HOURS'u veren işlev (varsayılan src.refresh); yalnızca ilk completed'te çağrılır
    """
    event = obs.event
    eid = str(event.get("id"))
    now = obs.at
    cls = classify_status(dict(event))
    s: LiveState = copy.deepcopy(dict(state)) if state else {"class": None, "done": False}
    s.setdefault("class", None)
    s.setdefault("done", False)
    emitted: List[Dict[str, Any]] = []
    prev = s.get("class")
    prev_start = s.get("start_ts")
    s["start_ts"] = event.get("startTimestamp", prev_start)
    s["tournament_id"] = tournament_of(event) or s.get("tournament_id")

    if prev is not None and prev != cls.value:
        scores = dataclasses.asdict(extract_scores(dict(event), sport))
        scores["status_class"] = cls.value
        ev: Dict[str, Any] = {
            "type": STATUS_CHANGED,
            "event_id": int(eid),
            "from": prev,
            "to": cls.value,
            "at_utc": _utc(now),
            "change_ts": (event.get("changes") or {}).get("changeTimestamp"),
            "source": obs.via,
            "scores": scores,
        }
        if cls is StatusClass.COMPLETED and not s.get("completed_emitted"):
            # 03-A: başlangıç + REFRESH_WINDOW_HOURS dolmadıysa sonuç geçici
            window = (window_hours or refresh_window_hours)()
            start = s.get("start_ts")
            closed = bool(window) and isinstance(start, (int, float)) and now >= start + window * 3600
            ev["provisional"] = not closed
            s["completed_emitted"] = True
        emitted.append(ev)
    elif cls is StatusClass.LIVE and prev == StatusClass.LIVE.value:
        key = score_key(event)
        if s.get("score") is not None and key != s.get("score"):
            emitted.append({
                "type": SCORE_CHANGED,
                "event_id": int(eid),
                "at_utc": _utc(now),
                "from": s.get("score"),
                "to": key,
                "change_ts": (event.get("changes") or {}).get("changeTimestamp"),
                "source": obs.via,
            })
    s["class"] = cls.value
    s["score"] = score_key(event)
    s["near_end"] = cls is StatusClass.LIVE and near_end(event, sport, now)

    if cls in TERMINAL:
        s["done"] = True
    elif cls is StatusClass.VOID:
        # Ertesi güne taşınmış maç aynı id ile izlenmeye devam eder (B13/T6)
        moved = isinstance(s["start_ts"], (int, float)) and s["start_ts"] > now
        s["done"] = not moved
    else:
        s["done"] = False

    params = watcher_params(sport)
    if params.stuck_from_play_start:
        s["play_start"] = play_start(event) or s.get("play_start")
        start = s.get("play_start") or s.get("start_ts")
    else:
        start = s.get("start_ts")
    limit = params.stuck_after_seconds
    stuck_now = (
        cls in (StatusClass.LIVE, StatusClass.NOT_STARTED)
        and isinstance(start, (int, float))
        and now > start + limit
    )
    if stuck_now and not s.get("stuck"):
        emitted.append({"type": STUCK, "event_id": int(eid), "at_utc": _utc(now), "start_ts": start,
                        "status_class": cls.value})
    if prev_start is not None and s["start_ts"] != prev_start:
        s["stuck"] = False  # yeni başlangıç: eşik yeniden sayılır
    else:
        s["stuck"] = stuck_now or bool(s.get("stuck") and cls in (StatusClass.LIVE, StatusClass.NOT_STARTED))
    # JSON'a yazılıp okunmuş hali: Pair → liste, enum → metin (saklanan ve yeniden yüklenen durumla aynı)
    return s, [json.loads(json.dumps(item, ensure_ascii=False)) for item in emitted]


# --- akış biçimi -----------------------------------------------------------------------------------


def _pair(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return {"home": value[0], "away": value[1]}
    return None


def score_of(event: Mapping[str, Any], sport: str) -> Optional[Dict[str, Any]]:
    """Maç nesnesinin skoru, şemanın Score yapısında (src/schema); çıkarılamazsa None."""
    from src.schema import mappers
    from src.sports import score_family
    from src.status import ScoreSheet

    def headline(side: Any) -> Any:
        side = side if isinstance(side, Mapping) else {}
        return side.get("display", side.get("current"))

    try:
        # Kataloğun satırıyla aynı alanlar (src/store/derive.py: home_score, away_score, scores_json): skor
        # kataloğa yazılan kuralla okunur
        family = score_family(sport)
        sheet_json = None
        if family is not None:
            common = {f.name for f in dataclasses.fields(ScoreSheet)}
            body = {k: v for k, v in dataclasses.asdict(extract_scores(dict(event), sport)).items() if k not in common}
            sheet_json = json.dumps({"family": family, **body}, ensure_ascii=False, default=str)
        row = {"sport": sport, "home_score": headline(event.get("homeScore")),
               "away_score": headline(event.get("awayScore")), "scores_json": sheet_json}
        return mappers.score_from_row(row).to_dict()
    except Exception as e:  # bozuk ya da eksik nesne: olay yine yazılır, skoru boş kalır
        logger.debug("Score of event %s could not be built (%s)", event.get("id"), type(e).__name__)
        return None


def _key_part(value: Any) -> str:
    return "-" if value is None else json.dumps(value, separators=(",", ":"))


def dedup_key(item: Mapping[str, Any]) -> str:
    """Olayın `live` akışındaki yinelenme anahtarı (modül açıklaması)."""
    eid, kind = item["event_id"], item["type"]
    if kind == STUCK:
        return f"{eid}:{STUCK}:{_key_part(item.get('start_ts'))}"
    return f"{eid}:{kind}:{_key_part(item.get('from'))}>{_key_part(item.get('to'))}:{_key_part(item.get('change_ts'))}"


def stream_data(item: Mapping[str, Any], event: Mapping[str, Any], sport: str) -> Dict[str, Any]:
    """2.x olayı → `live` akışındaki `data` (modül açıklaması). `event`: olayı üreten gözlemin maç nesnesi."""
    kind = item["type"]
    if kind == STATUS_CHANGED:
        return {"from": item.get("from"), "to": item.get("to"), "change_ts": item.get("change_ts"),
                "provisional": item.get("provisional"), "score": score_of(event, sport)}
    if kind == SCORE_CHANGED:
        return {"from": _pair(item.get("from")), "to": _pair(item.get("to")), "change_ts": item.get("change_ts"),
                "score": score_of(event, sport)}
    if kind == STUCK:
        start = item.get("start_ts")
        return {"status_class": item.get("status_class"),
                "start_utc": _utc(start).replace("+00:00", "Z") if isinstance(start, (int, float)) else None}
    return {k: v for k, v in item.items() if k not in ("type", "event_id")}


def stream_event(item: Mapping[str, Any], event: Mapping[str, Any], sport: str, *,
                 tournament_id: Any = None, source: str = "poll") -> Any:
    """2.x olayı → `live` akışına eklenecek StreamEvent (tür önekli, `data` ve yinelenme anahtarıyla)."""
    from src.store import StreamEvent

    return StreamEvent(
        type=f"{LIVE_STREAM}.{item['type']}",
        data=stream_data(item, event, sport),
        event_id=int(item["event_id"]),
        sport=sport,
        tournament_id=tournament_id if type(tournament_id) is int else None,
        source=source,
        dedup_key=dedup_key(item),
    )


__all__ = [
    "LIVE_STREAM",
    "NEAR_END_RULES",
    "SCORE_CHANGED",
    "STATUS_CHANGED",
    "STUCK",
    "TERMINAL",
    "TERMINAL_CLASSES",
    "VIA_EVENT",
    "VIA_LIST",
    "LiveState",
    "Observation",
    "dedup_key",
    "near_end",
    "play_start",
    "reduce",
    "score_key",
    "score_of",
    "stream_data",
    "stream_event",
    "tournament_of",
]
