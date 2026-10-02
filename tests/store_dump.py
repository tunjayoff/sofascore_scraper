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

İki düzen ayrı ayrı dökülür (`dump_legacy`, `dump_v3`) ve `dump` ikisini birleştirir: aynı maç, aynı program
sayfası ya da aynı turnuvanın sezon listesi iki düzende de duruyorsa v3'teki geçerlidir (okuyucuların ve
yeniden kurmanın kuralı, bölüm 3.4 adım 5). Değişiklik günlüğünde eski dosyanın satırları (sıra numarası satır
numarası) ile v3 parçalarının satırları (sıra numarası satırın içinde; dökümde satırdan çıkarılır) sıra
numarasına göre birleşir.

v3 dökümü dosyaları kendisi okur: durum ve sayaçlar manifestten, yük özeti yük dosyasının kendisinden gelir
(manifestteki özetten değil), böylece manifesti ile dosyası uyuşmayan bir dilim dökümde de farklı görünür.
Dilim adı alt anahtarsız dilimde anahtarın kendisidir, alt anahtarlı dilimde "anahtar/alt".

Bilerek dökülmeyenler: dosya zamanları (`fetched_at`, işaretlerin `at` alanı; yazıcı değişince değişir),
türetilmiş özetler (`*_summary.*`, `processed/`), izleyici dosyaları (taşınmaz) ve geçerli sayılmayan
kopyalar (aynı maçın eski dizini, aynı turnuvanın eski sezon listesi).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Union

from src.store import changes as changes_mod
from src.store import codec, indexer, layout
from src.store import manifest as manifest_mod
from src.store.errors import StoreError
from src.store.legacy import LegacyEvent, LegacyReader, LegacySlice
from src.store.manifest import Manifest, SliceEntry

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


def _numbered(path: str) -> List[int]:
    """Dizindeki, adı bir kimliğin kurallı yazımı olan alt dizinler; dizin yoksa boş liste."""
    try:
        names = os.listdir(path)
    except (FileNotFoundError, NotADirectoryError):
        return []
    return sorted(int(name) for name in names
                  if name.isascii() and name.isdigit() and str(int(name)) == name
                  and os.path.isdir(os.path.join(path, name)))


def _v3_manifest(data_dir: PathLike, rel: str, kind: str, entity_id: int) -> Optional[Manifest]:
    """Varlık dizininin manifesti; yoksa, okunamıyorsa ya da başka bir varlığınsa None (o varlık dökülmez)."""
    try:
        found = manifest_mod.read_manifest(layout.resolve(data_dir, layout.manifest_path(rel)))
    except StoreError:
        return None
    return found if (found.kind, found.id) == (kind, entity_id) else None


def _v3_payload(data_dir: PathLike, rel: str, name: str) -> Any:
    key, sub = layout.split_slice_name(name)
    return codec.read_payload(layout.resolve(data_dir, layout.slice_path(rel, key, sub)))


def _v3_slice(data_dir: PathLike, rel: str, name: str, entry: SliceEntry) -> Dict[str, Any]:
    empty, error = entry.empty, entry.error
    return {
        "state": entry.state,
        "sha256": payload_hash(_v3_payload(data_dir, rel, name)) if entry.has_payload else None,
        "empty_count": empty.count if empty else 0,
        "unverified_empty_count": empty.unverified if empty else 0,
        "error": {"reason": error.reason, "status": error.status, "count": error.count} if error else None,
    }


def dump_v3_event(data_dir: PathLike, event_id: int) -> Optional[Dict[str, Any]]:
    """Bir maçın v3 dizininin dökümü (`dump()["events"][kimlik]` biçiminde); manifesti okunamıyorsa None."""
    rel = layout.event_dir(event_id)
    found = _v3_manifest(data_dir, rel, "event", event_id)
    if found is None:
        return None
    observation = found.observation
    return {
        "observation": None if observation is None else {
            "observed_at_utc": _utc_iso(observation.observed_at),
            "change_ts": observation.change_ts,
            "status_regressed": observation.status_regressed,
        },
        "slices": {name: _v3_slice(data_dir, rel, name, entry) for name, entry in found.slices.items()},
    }


def dump_legacy_event(data_dir: PathLike, directory: str) -> Optional[Dict[str, Any]]:
    """Eski düzendeki bir maç dizininin (DATA_DIR'e göre yol) dökümü; dizin bir maç dizini değilse None."""
    reader = LegacyReader(data_dir)
    candidate = reader.event_dir_at(directory)
    return _event(reader.read_event(candidate, payloads=True)) if candidate is not None else None


def dump_v3(data_dir: PathLike) -> Dict[str, Any]:
    """
    v3 ağacının ve `changes/` parçalarının dökümü, `dump_legacy` ile aynı biçimde. Manifesti okunamayan dizin
    dökülmez. Program sayfaları sezon dizininin `schedule/<alt anahtar>` dilimleri, sezon listesi turnuva
    dizininin `seasons` dilimidir (bölüm 2.3 ve 4.2).
    """
    events: Dict[str, Any] = {}
    for event_id, _rel in indexer.scan_v3_events(data_dir):
        event = dump_v3_event(data_dir, event_id)
        if event is not None:
            events[str(event_id)] = event

    schedules: Dict[str, Dict[str, Any]] = {}
    season_lists: Dict[str, Any] = {}
    for tournament_id in _numbered(layout.resolve(data_dir, layout.TOURNAMENTS_DIR)):
        rel = layout.tournament_dir(tournament_id)
        found = _v3_manifest(data_dir, rel, "tournament", tournament_id)
        entry = found.slices.get("seasons") if found is not None else None
        if entry is not None and entry.has_payload:
            payload = _v3_payload(data_dir, rel, "seasons")
            listed = payload.get("seasons") if isinstance(payload, dict) else None
            season_lists[str(tournament_id)] = {"sha256": payload_hash(payload),
                                                "seasons": len(listed) if isinstance(listed, list) else 0}
        for season_id in _numbered(layout.resolve(data_dir, f"{rel}/seasons")):
            season_rel = layout.season_dir(tournament_id, season_id)
            season = _v3_manifest(data_dir, season_rel, "season", season_id)
            for name, page in sorted(season.slices.items()) if season is not None else ():
                key, sub = layout.split_slice_name(name)
                if key != "schedule" or not page.has_payload:
                    continue
                schedules.setdefault(f"{tournament_id}/{season_id}", {})[sub] = {
                    "sha256": payload_hash(_v3_payload(data_dir, season_rel, name)), "meta": dict(page.meta or {})}

    changes: List[Dict[str, Any]] = []
    for segment in changes_mod.segments(data_dir):
        if segment == changes_mod.LEGACY_SEGMENT:
            continue
        for line in changes_mod.read_segment(data_dir, segment):
            row = json.loads(line.line)
            row.pop("seq", None)
            changes.append({"seq": line.seq, "row": row})
    return {"events": events, "schedules": schedules, "season_lists": season_lists,
            "changes": sorted(changes, key=lambda change: change["seq"])}


def dump(data_dir: PathLike, league_names: Optional[Mapping[int, str]] = None) -> Dict[str, Any]:
    """Veri dizininin mantıksal dökümü: iki düzen birlikte, v3'teki kopya eski düzendekinin önünde."""
    out = dump_legacy(data_dir, league_names)
    new = dump_v3(data_dir)
    out["events"].update(new["events"])
    out["events"] = {key: out["events"][key] for key in sorted(out["events"], key=int)}
    for season, pages in new["schedules"].items():
        out["schedules"].setdefault(season, {}).update(pages)
    out["season_lists"].update(new["season_lists"])
    out["changes"] = sorted(out["changes"] + new["changes"], key=lambda change: change["seq"])
    return out


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
