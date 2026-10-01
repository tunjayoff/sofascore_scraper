"""
Mantıksal döküm: bir veri dizininin düzenden bağımsız tarifi (docs/design/01-storage.md, bölüm 10;
plan maddesi ST-05).

Dökümde yol, dosya adı, dosya zamanı ve sıkıştırma yoktur; yalnızca saklanan şeyin kendisi vardır:

  events        maç id → gözlem + dilimler (durum, sayaçlar, yükün özeti)
  schedules     "<turnuva id>/<sezon id>" → program sayfası (v3 alt anahtarı) → yükün özeti + meta
  season_lists  turnuva id → sezon listesi yükünün özeti
  changes       değişiklik günlüğü satırları, sıra numarasıyla

Yük özeti, yükün kurallı JSON baytlarının sha256'sıdır (src/store/codec.py); eski düzendeki girintili
dosya ile v3'teki sıkıştırılmış dosya aynı yük için aynı özeti verir. Yazıcıları Store'a geçiren plan
maddeleri (ST-21, ST-22), taşıma (ST-23) ve yedekten dönüş (ST-24) dökümün önce ve sonra eşit kaldığını
gösterir.

Şimdilik yalnızca eski düzen dökülür (`dump_legacy`). v3 ağacının dökümü ve ikisinin birleşimi ST-20'de
eklenir; `dump` o zamana kadar eski düzenin dökümüdür ve `v3/` dizinine bakmaz.

Bilerek dökülmeyenler: dosya zamanları (`fetched_at`, işaretlerin `at` alanı; yazıcı değişince değişir),
türetilmiş özetler (`*_summary.*`, `processed/`), izleyici dosyaları (taşınmaz) ve geçerli sayılmayan
kopyalar (aynı maçın eski dizini, aynı turnuvanın eski sezon listesi).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Union

from src.store import codec
from src.store.legacy import LegacyEvent, LegacyReader, LegacySlice

PathLike = Union[str, "os.PathLike[str]"]


def payload_hash(payload: Any) -> str:
    """Yükün düzenden bağımsız özeti: kurallı JSON baytlarının sha256'sı."""
    return codec.sha256_hex(codec.canonical_bytes(payload))


def _utc_iso(moment: Optional[datetime]) -> Optional[str]:
    """Aynı an, hangi saat dilimiyle yazılmış olursa olsun aynı metni verir."""
    return moment.astimezone(timezone.utc).isoformat() if moment is not None else None


def _slice(entry: LegacySlice, payloads: Mapping[str, Any]) -> Dict[str, Any]:
    error = entry.error
    return {
        "state": entry.state,
        "sha256": payload_hash(payloads[entry.key]) if entry.has_payload else None,
        "empty_count": entry.empty_count,
        "unverified_empty_count": entry.unverified_empty_count,
        "error": {"reason": error.reason, "status": error.status, "count": error.count} if error else None,
    }


def _event(event: LegacyEvent) -> Dict[str, Any]:
    observation = event.observation
    payloads = event.payloads or {}
    return {
        "observation": None if observation is None else {
            "observed_at_utc": _utc_iso(observation.observed_at),
            "change_ts": observation.change_ts,
            "status_regressed": observation.status_regressed,
        },
        "slices": {entry.key: _slice(entry, payloads) for entry in event.slices},
    }


def dump_legacy(data_dir: PathLike, league_names: Optional[Mapping[int, str]] = None) -> Dict[str, Any]:
    """
    Eski düzen ağaçlarının dökümü. `league_names` (id → ad) yalnızca `<ad>_seasons.json` biçimindeki
    sezon listelerinin turnuvasını bulmak için gerekir; turnuvası bulunamayan liste dökülmez.
    """
    reader = LegacyReader(data_dir)
    events = {str(event.event_id): _event(event) for event in reader.iter_events(payloads=True)}

    schedules: Dict[str, Dict[str, Any]] = {}
    for page in sorted(reader.schedule_pages(), key=lambda p: (p.tournament_id, p.season_id, p.sub)):
        if page.superseded_by is not None:
            continue
        schedule = reader.read_schedule(page)
        season = schedules.setdefault(f"{page.tournament_id}/{page.season_id}", {})
        season[page.sub] = {"sha256": payload_hash(schedule.payload), "meta": dict(schedule.meta)}

    season_lists = {
        str(item.tournament_id): {"sha256": payload_hash(item.payload), "seasons": len(item.seasons)}
        for item in sorted(reader.season_lists(league_names), key=lambda s: s.tournament_id or 0)
        if item.tournament_id is not None and item.superseded_by is None
    }
    changes = [{"seq": line.seq, "row": dict(line.row)} for line in reader.change_log()]
    return {"events": events, "schedules": schedules, "season_lists": season_lists, "changes": changes}


def dump(data_dir: PathLike, league_names: Optional[Mapping[int, str]] = None) -> Dict[str, Any]:
    """Veri dizininin mantıksal dökümü. v3 ağacı ST-20'de eklenir; şimdilik `dump_legacy` ile aynıdır."""
    return dump_legacy(data_dir, league_names)


def diff(expected: Any, actual: Any, path: str = "$") -> List[str]:
    """İki döküm arasındaki farklar ("yol: beklenen -> şimdiki"); eşitse boş liste."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        out: List[str] = []
        for key in sorted(set(expected) | set(actual), key=str):
            if key not in actual:
                out.append(f"{path}.{key}: silindi")
            elif key not in expected:
                out.append(f"{path}.{key}: eklendi")
            else:
                out.extend(diff(expected[key], actual[key], f"{path}.{key}"))
        return out
    if isinstance(expected, list) and isinstance(actual, list):
        out = [f"{path}: uzunluk {len(expected)} -> {len(actual)}"] if len(expected) != len(actual) else []
        for index, (left, right) in enumerate(zip(expected, actual, strict=False)):
            out.extend(diff(left, right, f"{path}[{index}]"))
        return out
    same = type(expected) is type(actual) and expected == actual
    return [] if same else [f"{path}: {expected!r} -> {actual!r}"]
