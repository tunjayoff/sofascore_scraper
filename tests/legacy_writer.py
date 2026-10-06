"""
Eski düzen yazıcısının dondurulmuş kopyası: 3.0 öncesi sürümlerin bir maçı `match_details/` altına nasıl
yazdığı (plan maddesi ST-21 öncesindeki `MatchDataFetcher._save_match_data`, `_match_storage_dir` ve
`_update_slice_markers`, değiştirilmeden).

Uygulama artık eski düzene yazmaz (maçlar `Store.events.put` ile v3'e yazılır). Testler bu kopyayı iki iş için
kullanır:

  * kehanet: Store'un dilim durumu kurallarının eski yazıcınınkiyle aynı olduğunu gösteren testler
    (tests/test_store_put.py'deki durum tablosu) aynı sonuç dizisini bir yanda buraya, öte yanda `put`'a verir;
  * eski kayıt: önceki bir sürümün yazdığı bir maç dizinini (gözlemi olmayan kayıt, işaret dosyaları) kurmak.

Her yazmadan sonra maç kataloğa alınır (tests/catalog_index.py; eskiden Store'un kancası `shadow_event`): katalog
yazılanı görür.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from typing import Any, Dict, Mapping, Optional, Tuple

from src import breaker as request_breaker
from src.match_fetcher import MatchFetcher
from src.slices import SliceOutcome, match_detail_slice_present
from src.sports import event_sport_slug, slices_for
from catalog_index import LISTING_CHANGES, index_event, index_listings

UNAVAILABLE_FILE = "_unavailable.json"
SLICE_STATUS_FILE = "_slice_status.json"
NO_TOURNAMENT_DIR = "_no_tournament"
SCORE_CHANGES_FILE = "score_changes.jsonl"


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _path_part(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value)).strip(".") or "unknown"


def _write_json(path: str, data: Any) -> None:
    """
    Eski yazıcının yazdığı gibi (2.x: src.fsutil.atomic_write_json): okunur UTF-8, girinti 2, geçici dosya ve
    os.replace. Ürün kodundan geçmez: veri dizinine Store dışından yazan tek kod bu test yardımcısıdır.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
    os.replace(tmp, path)


def load_unavailable(match_dir: str) -> Dict[str, int]:
    try:
        with open(os.path.join(match_dir, UNAVAILABLE_FILE), "r", encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def load_slice_status(match_dir: str) -> Dict[str, Dict[str, Any]]:
    try:
        with open(os.path.join(match_dir, SLICE_STATUS_FILE), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}


def _confirmed_empty_count(entry: Optional[Dict[str, Any]]) -> int:
    empty = (entry or {}).get("empty")
    count = empty.get("count") if isinstance(empty, dict) else None
    return count if isinstance(count, int) and not isinstance(count, bool) and count > 0 else 0


def expected_slices(match_dir: str, sport: Optional[str] = None, threshold: int = 2) -> list:
    """Eski dosya tabanlı kural: sporun `required` dilimleri, `_unavailable.json`'da `threshold`a ulaşanlar hariç."""
    unavailable = load_unavailable(match_dir)
    return [d.key for d in slices_for(sport, required_only=True) if unavailable.get(d.key, 0) < threshold]


def legacy_match_dir(data_dir: Any, match_id: str, basic_data: Mapping[str, Any]) -> Tuple[Optional[str], Optional[str], str]:
    """Eski yazıcının maç için seçtiği yer: (lig dizini adı, sezon dizini adı, maç dizini)."""
    details = os.path.join(os.fspath(data_dir), "match_details")
    tournament_data = (basic_data.get("tournament") or {}).get("uniqueTournament") or {}
    tournament_id = tournament_data.get("id")
    if not tournament_id:
        sport_dir = _path_part(event_sport_slug(dict(basic_data)) or "unknown")
        return NO_TOURNAMENT_DIR, sport_dir, os.path.join(details, NO_TOURNAMENT_DIR, sport_dir, match_id)
    tournament_name = tournament_data.get("name") or "Unknown_League"
    safe_tournament_name = f"{tournament_id}_{tournament_name.replace(' ', '_').replace('/', '_')}"
    season_data = basic_data.get("season") or {}
    season_id = season_data.get("id")
    season_name = season_data.get("name", "Unknown_Season")
    season_year = season_data.get("year", "Unknown_Year")
    if season_name and season_name != "Unknown_Season":
        safe_season_name = f"season_{season_name.replace(' ', '_').replace('/', '_')}"
    elif season_year and season_year != "Unknown_Year":
        safe_season_name = f"season_{season_year.replace('/', '_')}"
    else:
        safe_season_name = f"season_{season_id}"
    return safe_tournament_name, safe_season_name, os.path.join(details, safe_tournament_name, safe_season_name, match_id)


def _update_slice_markers(match_dir: str, sport: Optional[str], match_data: Mapping[str, Any],
                          outcomes: Mapping[str, SliceOutcome]) -> None:
    unavailable = load_unavailable(match_dir)
    status = load_slice_status(match_dir)
    now = _utc_now_iso()
    unavailable_changed = status_changed = False
    for detail in slices_for(sport, required_only=True):
        key = detail.key
        if match_detail_slice_present(key, dict(match_data)):
            unavailable_changed |= unavailable.pop(key, None) is not None
            status_changed |= status.pop(key, None) is not None
            continue
        outcome = outcomes.get(key)
        if outcome is None:
            continue
        if outcome.failed and outcome.reason == request_breaker.BREAKER_OPEN:
            continue
        entry = status.setdefault(key, {})
        if outcome.failed:
            previous = entry.get("error") if isinstance(entry.get("error"), dict) else {}
            entry["error"] = {
                "reason": outcome.reason or request_breaker.OTHER,
                "status": outcome.http_status,
                "at": now,
                "count": int(previous.get("count") or 0) + 1,
            }
        else:
            unavailable[key] = unavailable.get(key, 0) + 1
            unavailable_changed = True
            entry["empty"] = {"count": _confirmed_empty_count(entry) + 1, "at": now}
            entry.pop("error", None)
        status_changed = True
    if unavailable_changed:
        _write_json(os.path.join(match_dir, UNAVAILABLE_FILE), unavailable)
    if status_changed:
        status_path = os.path.join(match_dir, SLICE_STATUS_FILE)
        if status:
            _write_json(status_path, status)
        elif os.path.exists(status_path):
            os.remove(status_path)


def save_legacy(data_dir: Any, match_id: Any, match_data: Mapping[str, Any],
                outcomes: Optional[Mapping[str, SliceOutcome]] = None, *, directory: Optional[str] = None) -> str:
    """
    Maçı eski yazıcı gibi yazar: `match_data`'daki her yük `<anahtar>.json` olarak (`observation` dahil), bitmiş
    maçta işaret dosyaları. directory: maç dizini (verilmezse eski yazıcının seçtiği yer). Maç dizinini döndürür.
    """
    mid = str(match_id)
    basic_data = match_data.get("basic") or {}
    match_dir = directory or legacy_match_dir(data_dir, mid, basic_data)[2]
    try:
        os.makedirs(match_dir, exist_ok=True)
        for data_type, data in match_data.items():
            if data is not None:
                _write_json(os.path.join(match_dir, f"{data_type}.json"), data)
        if MatchFetcher._is_finished_event(dict(basic_data)):
            _update_slice_markers(match_dir, event_sport_slug(dict(basic_data)) or "", match_data, outcomes or {})
    finally:
        index_event(data_dir, mid, match_dir)
    return match_dir


def append_legacy_change(data_dir: Any, row: Mapping[str, Any]) -> None:
    """Eski değişiklik günlüğüne (`score_changes.jsonl`) bir satır, eski yazıcı gibi metin kipinde."""
    with open(os.path.join(os.fspath(data_dir), SCORE_CHANGES_FILE), "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
    index_listings(data_dir, LISTING_CHANGES)


def _reset_match_markers(data_dir: Any, match_dir: str, include_confirmed: bool, threshold: int) -> int:
    unavailable = load_unavailable(match_dir)
    status = load_slice_status(match_dir)
    reopened = 0
    changed = status_changed = False
    for key, count in list(unavailable.items()):
        keep = 0 if include_confirmed else min(count, _confirmed_empty_count(status.get(key)))
        if keep == count:
            continue
        changed = True
        if count >= threshold > keep:
            reopened += 1
        if keep:
            unavailable[key] = keep
        else:
            del unavailable[key]
        if include_confirmed and isinstance(status.get(key), dict) and status[key].pop("empty", None) is not None:
            status_changed = True
            if not status[key]:
                del status[key]
    if not changed:
        return 0
    try:
        unavailable_path = os.path.join(match_dir, UNAVAILABLE_FILE)
        if unavailable:
            _write_json(unavailable_path, unavailable)
        else:
            os.remove(unavailable_path)
        if status_changed:
            status_path = os.path.join(match_dir, SLICE_STATUS_FILE)
            if status:
                _write_json(status_path, status)
            else:
                os.remove(status_path)
    finally:
        index_event(data_dir, os.path.basename(match_dir), match_dir)
    return reopened


def reset_legacy_markers(data_dir: Any, league_id: Any = None, include_confirmed: bool = False,
                         threshold: int = 2) -> Dict[str, int]:
    """Eski `reset_unavailable_markers`: lig/sezon/maç derinliğindeki dizinlerin işaret dosyalarını geri alır."""
    details = os.path.join(os.fspath(data_dir), "match_details")
    result = {"matches": 0, "slices": 0, "scanned": 0}
    if not os.path.isdir(details):
        return result
    for league_name in sorted(os.listdir(details)):
        league_path = os.path.join(details, league_name)
        if league_name == "processed" or not os.path.isdir(league_path):
            continue
        if league_id is not None and not league_name.startswith(f"{league_id}_"):
            continue
        for season_name in sorted(os.listdir(league_path)):
            season_path = os.path.join(league_path, season_name)
            if not os.path.isdir(season_path):
                continue
            for mid in sorted(os.listdir(season_path)):
                match_dir = os.path.join(season_path, mid)
                if not os.path.isfile(os.path.join(match_dir, UNAVAILABLE_FILE)):
                    continue
                result["scanned"] += 1
                reopened = _reset_match_markers(data_dir, match_dir, include_confirmed, threshold)
                if reopened:
                    result["matches"] += 1
                    result["slices"] += reopened
    return result
