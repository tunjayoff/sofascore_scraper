"""
Saklanan maç kayıtlarını testler için elle değiştiren yardımcılar (plan maddesi ST-21).

İndirici maçları Store'a, v3 düzenine yazar (`v3/events/.../<id>/`: manifest + `<dilim>.json.gz`). Eskiden
testler bir kaydın durumunu dosyasını silerek ya da düzenleyerek kurardı (`h2h.json`'ı silmek, `observation.json`'ı
geriye almak). Buradaki işlevler aynı durumu her iki düzende kurar: v3'te yük dosyası ve manifest kaydı birlikte
değişir, ardından maç Store'un kancasıyla (`shadow_event`) yeniden dizinlenir; eski düzende dosya değişir ve aynı
kanca çağrılır. Yükler ve kayıtlar Store'dan okunur.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from src.store import api as store_api
from src.store import layout, open_store
from src.store import manifest as manifest_mod

PathLike = Union[str, "os.PathLike[str]"]
_V3 = "v3"


def _row(data_dir: PathLike, event_id: int) -> Any:
    row = open_store(data_dir).events.get(int(event_id))
    assert row is not None and row.has_event_payload, f"no stored event {event_id}"
    return row


def record_dir(data_dir: PathLike, event_id: int) -> Path:
    """Kaydın dizini: v3 dizini ya da eski düzen dizini."""
    row = _row(data_dir, event_id)
    rel = layout.event_dir(int(event_id)) if row.layout == _V3 else str(row.path)
    return Path(os.fspath(data_dir), *rel.split("/"))


def _reindex(data_dir: PathLike, event_id: int, directory: Optional[Path] = None) -> None:
    store_api.shadow_event(data_dir, int(event_id), directory)


def drop_slices(data_dir: PathLike, event_id: int, *keys: str) -> None:
    """
    Kaydın dilimlerini hiç istenmemiş gibi siler: yük dosyası, işareti ve hata kaydı (eski düzende `<dilim>.json`'ı
    silmenin karşılığı; eski düzende işaret dosyalarına dokunulmaz).
    """
    row = _row(data_dir, event_id)
    directory = record_dir(data_dir, event_id)
    if row.layout != _V3:
        for key in keys:
            (directory / f"{key}.json").unlink()
        _reindex(data_dir, event_id, directory)
        return
    manifest_file = directory / layout.MANIFEST_NAME
    found = manifest_mod.read_manifest(manifest_file)
    for key in keys:
        assert found.slices.pop(key, None) is not None, f"event {event_id} has no slice {key}"
        payload = directory / f"{key}{layout.PAYLOAD_SUFFIX}"
        if payload.exists():
            payload.unlink()
    manifest_mod.write_manifest(manifest_file, found)
    _reindex(data_dir, event_id)


def set_observed_at(data_dir: PathLike, event_id: int, observed_at: dt.datetime) -> None:
    """Kaydın gözlem anını değiştirir (eski düzende `observation.json`'daki `observed_at_utc`)."""
    row = _row(data_dir, event_id)
    directory = record_dir(data_dir, event_id)
    if row.layout != _V3:
        path = directory / "observation.json"
        observation = json.loads(path.read_text(encoding="utf-8"))
        observation["observed_at_utc"] = observed_at.astimezone(dt.timezone.utc).isoformat(timespec="seconds")
        replacement = path.with_name(path.name + ".new")
        replacement.write_text(json.dumps(observation), encoding="utf-8")
        os.replace(replacement, path)
        _reindex(data_dir, event_id, directory)
        return
    manifest_file = directory / layout.MANIFEST_NAME
    found = manifest_mod.read_manifest(manifest_file)
    assert found.observation is not None, f"event {event_id} has no observation"
    found.observation.observed_at = observed_at.astimezone(dt.timezone.utc)
    manifest_mod.write_manifest(manifest_file, found)
    _reindex(data_dir, event_id)


def stored_slices(data_dir: PathLike, event_id: int) -> List[str]:
    """Yükü saklanan dilimler (`event` dahil), ada göre sıralı; kayıt yoksa boş liste."""
    row = open_store(data_dir).events.get(int(event_id))
    if row is None or not row.has_event_payload:
        return []
    return sorted(info.key for info in open_store(data_dir).events.slices(int(event_id)) if info.has_payload)


def _iso(moment: Any) -> Optional[str]:
    if moment is None:
        return None
    if isinstance(moment, (int, float)):
        moment = dt.datetime.fromtimestamp(moment, dt.timezone.utc)
    return moment.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def legacy_view(data_dir: PathLike, event_id: int) -> Dict[str, Any]:
    """
    Kaydın Store'daki hali, eski düzen dizininin dosyaları biçiminde: dosya adı → içerik (`basic.json`, yükü olan
    her dilimin `<dilim>.json`'ı, `observation.json`, `_unavailable.json`, `_slice_status.json`; boş olan işaret
    dosyası yer almaz). Eski düzende dosyalara bakan testler aynı soruyu her iki düzende de böyle sorar. Zamanlar
    tam saniyedir; `_unavailable.json`'daki sayı kesin ve doğrulanmamış sayımların toplamıdır. Kayıt yoksa {}.
    """
    store = open_store(data_dir)
    row = store.events.get(int(event_id))
    if row is None or not row.has_event_payload:
        return {}
    out: Dict[str, Any] = {}
    unavailable: Dict[str, int] = {}
    status: Dict[str, Dict[str, Any]] = {}
    for info in store.events.slices(int(event_id)):
        if info.has_payload:
            name = "basic" if info.key == "event" else info.key
            out[f"{name}.json"] = store.events.payload(int(event_id), info.key)
        total = info.empty_count + info.unverified_empty_count
        if total:
            unavailable[info.key] = total
        entry: Dict[str, Any] = {}
        if info.empty_count:
            entry["empty"] = {"count": info.empty_count, "at": _iso(info.checked_at)}
        if info.error is not None:
            entry["error"] = {"reason": info.error.reason, "status": info.error.http_status,
                              "at": _iso(info.error.at), "count": info.error.count}
        if entry:
            status[info.key] = entry
    if row.observed_at is not None:
        out["observation.json"] = {"observed_at_utc": _iso(row.observed_at), "change_ts": row.change_ts}
    if unavailable:
        out["_unavailable.json"] = unavailable
    if status:
        out["_slice_status.json"] = status
    return out


def slice_marks(data_dir: PathLike, event_id: int) -> Dict[str, Dict[str, Any]]:
    """
    Dilimlerin işaretleri (eski düzendeki `_unavailable.json` + `_slice_status.json`'ın karşılığı): sayacı ya da
    hata kaydı olan her dilim → {"empty": kesin sayım, "unverified": doğrulanmamış sayım, "error": neden}.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for info in open_store(data_dir).events.slices(int(event_id)):
        if info.empty_count or info.unverified_empty_count or info.error is not None:
            out[info.key] = {"empty": info.empty_count, "unverified": info.unverified_empty_count,
                             "error": info.error.reason if info.error is not None else None}
    return out
