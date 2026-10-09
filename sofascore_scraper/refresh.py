"""
Yenileme politikası: bitmiş bir maçın kaydı, başlangıcından `refresh.window_hours` geçene kadar geçicidir.

Dayanak: docs/status-matrix/README.md "Geriye dönük: bitiş sonrası güncellemeler" (alt lig basketbolda
nihai skor başlangıçtan 66,4 sa sonrasına kadar değişti). `changes` önceki değeri vermediği için
değişimin kendisi değişiklik günlüğüne eski ve yeni değeriyle yazılır.

Ayarlar (`[refresh]`) ayar yükleyicisinden çağrı anında okunur; 2.x'in REFRESH_* adları 3.1'de okunmaz.
"""
from __future__ import annotations

import datetime as dt
import time
from typing import Any, Dict, List, Optional, Tuple

from sofascore_scraper.status import StatusClass, classify_status

DEFAULT_REFRESH_WINDOW_HOURS = 72.0
# İki yenileme arası en az bu kadar saat: saatlik çekimde maç başına 72 istek olmasın. 6 sa, ilk yenilemeyi
# üst lig (başlangıçtan ≤ 3 sa) ve alt lig futbol (≤ 5,4 sa) düzeltmelerinin sonrasına denk getirir.
DEFAULT_REFRESH_MIN_INTERVAL_HOURS = 6.0
SCORE_CHANGES_FILE = "score_changes.jsonl"

# Karşılaştırılan alanlar: status üçlüsü, winnerCode, homeScore/awayScore'un tüm alt alanları, startTimestamp
_STATUS_FIELDS = ("type", "code", "description")


def _settings() -> Any:
    from sofascore_scraper.config import loader

    return loader.active_settings().refresh


def refresh_window_hours() -> float:
    """`refresh.window_hours`; 0 politikayı kapatır. Çağrı anında okunur (Ayarlar sayfası değiştirebilir)."""
    return max(0.0, float(_settings().window_hours))


def refresh_min_interval_hours() -> float:
    """`refresh.min_interval_hours` (ileri düzey): son gözlemden bu kadar süre geçmediyse yenileme yok."""
    return max(0.0, float(_settings().min_interval_hours))


def refresh_legacy_enabled() -> bool:
    """Gözlemi olmayan eski kayıtlar da bir kez yenilensin mi (`refresh.include_legacy`, `--include-legacy`)."""
    return bool(_settings().include_legacy)


def _parse_utc(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def refresh_due(basic: Dict[str, Any], observation: Dict[str, Any], now: Optional[float] = None) -> bool:
    """
    Kayıt geçici mi ve yenileme zamanı geldi mi? Kesin: observed_at_utc ≥ startTimestamp + pencere.
    Geçici kayıt, son gözlemden refresh.min_interval_hours geçmeden yeniden çekilmez.
    observation yoksa (eski kayıt) kesin sayılır; --refresh-legacy ile bir kez yenilenir.
    """
    window = refresh_window_hours()
    if window <= 0:
        return False
    observed = _parse_utc((observation or {}).get("observed_at_utc"))
    if observed is None:
        return refresh_legacy_enabled()
    start = (basic or {}).get("startTimestamp")
    if not isinstance(start, (int, float)):
        return False
    if observed >= start + window * 3600:
        return False
    now = time.time() if now is None else now
    return now - observed >= refresh_min_interval_hours() * 3600


def _flatten(event: Dict[str, Any]) -> Dict[str, Any]:
    status = event.get("status") or {}
    flat: Dict[str, Any] = {f"status.{k}": status.get(k) for k in _STATUS_FIELDS}
    flat["winnerCode"] = event.get("winnerCode")
    flat["startTimestamp"] = event.get("startTimestamp")
    for side in ("homeScore", "awayScore"):
        for k, v in (event.get(side) or {}).items():
            flat[f"{side}.{k}"] = v
    return flat


def diff_basic(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, List[Any]]:
    """Değişen alanlar: {alan: [eski, yeni]}; yeni ya da kaybolan alt alanlar None ile."""
    a, b = _flatten(old or {}), _flatten(new or {})
    return {k: [a.get(k), b.get(k)] for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)}


def status_regressed(old: Dict[str, Any], new: Dict[str, Any]) -> bool:
    """Oynanmış sayılan maç sonradan iptal edildi (COMPLETED → VOID)."""
    return classify_status(old) is StatusClass.COMPLETED and classify_status(new) is StatusClass.VOID


def _tier_hint(event: Dict[str, Any]) -> Optional[bool]:
    ut = ((event.get("tournament") or {}).get("uniqueTournament") or {})
    flags = (ut.get("hasEventPlayerStatistics"), event.get("hasEventPlayerStatistics"))
    return True if any(flags) else (None if all(f is None for f in flags) else False)


def change_row(
    old: Dict[str, Any],
    new: Dict[str, Any],
    changed: Dict[str, List[Any]],
    sport: Optional[str],
    now: Optional[dt.datetime] = None,
) -> Dict[str, Any]:
    """score_changes.jsonl satırı. hours_after_start: SofaScore'un değişiklik anı (yeni change_ts) − başlangıç."""
    now = now or dt.datetime.now(dt.timezone.utc)
    start = new.get("startTimestamp") or old.get("startTimestamp")
    new_ts = (new.get("changes") or {}).get("changeTimestamp")
    ref = new_ts if isinstance(new_ts, (int, float)) and new_ts else now.timestamp()
    ut = ((new.get("tournament") or {}).get("uniqueTournament") or {})
    row: Dict[str, Any] = {
        "ts_utc": now.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
        "event_id": new.get("id") or old.get("id"),
        "sport": sport,
        "tournament": {"id": ut.get("id"), "name": ut.get("name")},
        "tier_hint": _tier_hint(new),
        "start_ts": start,
        "hours_after_start": round((ref - start) / 3600, 2) if isinstance(start, (int, float)) else None,
        "changed": changed,
        "old_change_ts": (old.get("changes") or {}).get("changeTimestamp"),
        "new_change_ts": new_ts,
        "status_class": [classify_status(old).value, classify_status(new).value],
    }
    if status_regressed(old, new):
        row["status_regressed"] = True
    return row


def split_by_need(needs: List[Tuple[str, str]]) -> Tuple[List[str], List[str]]:
    """(id, need) listesinden: önce full/refill, sonra refresh (yenileme en sona)."""
    first = [mid for mid, need in needs if need in ("full", "refill")]
    refresh = [mid for mid, need in needs if need == "refresh"]
    return first, refresh
