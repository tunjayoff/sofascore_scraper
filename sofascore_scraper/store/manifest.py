"""
Varlık başına `manifest.json` (docs/design/01-storage.md, bölüm 4.2): veri sınıfları, okuma, yazma, doğrulama.

Manifest, bir varlığın (maç, turnuva, sezon, takım, oyuncu, spor) dilimlerinin durumunu taşır ve
kaynaktır; katalog satırları onu yansıtır. Sıkıştırılmaz, girintili yazılır, atomik değiştirilir.

Biçim 1. Alan eklemek biçim numarasını değiştirmez: bu sürümün tanımadığı alanlar `extra` içinde
saklanır ve yeniden yazarken aynen korunur, böylece eski sürüm yeni sürümün alanlarını silmez.
Anlamı değişen bir biçim (format > MANIFEST_FORMAT) okunmaz: SchemaTooNew.

Zamanlar dosyada ISO 8601 (UTC), bellekte saat dilimli `datetime`'dır.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union

from sofascore_scraper.store import files, layout
from sofascore_scraper.store.errors import LayoutError, PayloadCorrupt, SchemaTooNew

PathLike = Union[str, "os.PathLike[str]"]

MANIFEST_FORMAT = 1
STATES: Tuple[str, ...] = ("ok", "empty", "error")


@dataclass
class EmptyMark:
    """Kesin "veri yok" yanıtlarının sayacı (bugünkü _unavailable.json + _slice_status.json'ın yerine)."""

    count: int = 0  # doğrulanmış "veri yok" yanıtları
    unverified: int = 0  # eski düzenden gelen, kesin yanıtla desteklenmeyen sayım
    reason: Optional[str] = None  # "404" | "empty"
    at: Optional[datetime] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ErrorMark:
    """Son başarısız deneme."""

    reason: str = "other"  # "403" | "429" | "5xx" | "timeout" | "network" | "parse" | "other" | "corrupt"
    status: Optional[int] = None  # HTTP durum kodu
    at: Optional[datetime] = None
    count: int = 1
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HistoryMark:
    """Dilimin geçmiş dosyasındaki (_history/...) anlık görüntü sayısı ve sonuncusunun özeti."""

    count: int = 0
    last_sha256: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SliceEntry:
    state: str  # STATES'ten biri
    fetched_at: Optional[datetime] = None  # saklanan yükün alındığı an
    checked_at: Optional[datetime] = None  # sonucu ne olursa olsun son deneme
    stored_bytes: Optional[int] = None  # dosyadaki (sıkıştırılmış) boyut; JSON'da "bytes"
    raw_bytes: Optional[int] = None  # sıkıştırılmamış boyut
    sha256: Optional[str] = None  # sıkıştırılmamış baytların özeti
    empty: Optional[EmptyMark] = None
    error: Optional[ErrorMark] = None
    history: Optional[HistoryMark] = None
    meta: Optional[Dict[str, Any]] = None  # durumun yanında tutulan küçük JSON (ör. {"complete": true})
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_payload(self) -> bool:
        return self.sha256 is not None


@dataclass
class Observation:
    """Bugünkü observation.json'ın iki alanı (sofascore_scraper/status.py) ve yapışkan `status_regressed` bayrağı."""

    observed_at: Optional[datetime] = None  # JSON'da "observed_at_utc"
    change_ts: Optional[int] = None  # SofaScore'un changes.changeTimestamp değeri
    status_regressed: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Manifest:
    kind: str  # layout.KINDS'ten biri
    id: int
    created_at: datetime
    updated_at: datetime
    format: int = MANIFEST_FORMAT
    migrated_from: Optional[str] = None  # yalnızca eski düzenden gelen varlıkta: eski dizin (DATA_DIR'e göre)
    observation: Optional[Observation] = None
    slices: Dict[str, SliceEntry] = field(default_factory=dict)  # ad: "statistics" ya da "odds_all/1"
    extra: Dict[str, Any] = field(default_factory=dict)


# --- JSON <-> veri sınıfı ------------------------------------------------------------------------
# Okurken tipi uymayan değer olduğu gibi bırakılır; `validate` onu sorun olarak bildirir. Böylece
# doğrulama kuralları tek yerdedir ve hem okunan hem yazılan manifest aynı denetimden geçer.

def _ts_in(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value)
    except ValueError:
        return value
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _ts_out(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _rest(data: Mapping[str, Any], known: Iterable[str]) -> Dict[str, Any]:
    names = set(known)
    return {k: v for k, v in data.items() if k not in names}


def _empty_in(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    return EmptyMark(count=data.get("count", 0), unverified=data.get("unverified", 0), reason=data.get("reason"),
                     at=_ts_in(data.get("at")), extra=_rest(data, ("count", "unverified", "reason", "at")))


def _error_in(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    return ErrorMark(reason=data.get("reason"), status=data.get("status"), at=_ts_in(data.get("at")),
                     count=data.get("count", 1), extra=_rest(data, ("reason", "status", "at", "count")))


def _history_in(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    return HistoryMark(count=data.get("count", 0), last_sha256=data.get("last_sha256"),
                       extra=_rest(data, ("count", "last_sha256")))


_SLICE_KEYS = ("state", "fetched_at", "checked_at", "bytes", "raw_bytes", "sha256", "empty", "error", "history", "meta")


def _slice_in(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    return SliceEntry(
        state=data.get("state"),
        fetched_at=_ts_in(data.get("fetched_at")),
        checked_at=_ts_in(data.get("checked_at")),
        stored_bytes=data.get("bytes"),
        raw_bytes=data.get("raw_bytes"),
        sha256=data.get("sha256"),
        empty=_empty_in(data.get("empty")),
        error=_error_in(data.get("error")),
        history=_history_in(data.get("history")),
        meta=data.get("meta"),
        extra=_rest(data, _SLICE_KEYS),
    )


_OBSERVATION_KEYS = ("observed_at_utc", "change_ts", "status_regressed")


def _observation_in(data: Any) -> Any:
    if not isinstance(data, dict):
        return data
    return Observation(observed_at=_ts_in(data.get("observed_at_utc")), change_ts=data.get("change_ts"),
                       status_regressed=data.get("status_regressed", False), extra=_rest(data, _OBSERVATION_KEYS))


_MANIFEST_KEYS = ("format", "kind", "id", "created_at", "updated_at", "migrated_from", "observation", "slices")


def _with_extra(known: Dict[str, Any], extra: Mapping[str, Any]) -> Dict[str, Any]:
    """None olan alanlar yazılmaz; tanınmayan alanlar sona eklenir (bilinen bir alanı ezemez)."""
    out = {k: v for k, v in known.items() if v is not None}
    for key, value in extra.items():
        out.setdefault(key, value)
    return out


def _slice_out(entry: SliceEntry) -> Dict[str, Any]:
    empty, error, history = entry.empty, entry.error, entry.history
    return _with_extra({
        "state": entry.state,
        "fetched_at": _ts_out(entry.fetched_at) if entry.fetched_at else None,
        "checked_at": _ts_out(entry.checked_at) if entry.checked_at else None,
        "bytes": entry.stored_bytes,
        "raw_bytes": entry.raw_bytes,
        "sha256": entry.sha256,
        "empty": _with_extra({"count": empty.count, "unverified": empty.unverified, "reason": empty.reason,
                              "at": _ts_out(empty.at) if empty.at else None}, empty.extra) if empty else None,
        "error": _with_extra({"reason": error.reason, "status": error.status,
                              "at": _ts_out(error.at) if error.at else None, "count": error.count},
                             error.extra) if error else None,
        "history": _with_extra({"count": history.count, "last_sha256": history.last_sha256},
                               history.extra) if history else None,
        "meta": entry.meta,
    }, entry.extra)


# --- doğrulama -----------------------------------------------------------------------------------

def _is_int(value: Any, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _is_ts(value: Any) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _validate_slice(name: str, entry: Any, problems: List[str]) -> None:
    def bad(what: str) -> None:
        problems.append(f"slices[{name!r}]: {what}")

    try:
        layout.split_slice_name(name)
    except LayoutError:
        bad("invalid slice name")
    if not isinstance(entry, SliceEntry):
        bad("not an object")
        return
    if entry.state not in STATES:
        bad(f"invalid state {entry.state!r}")
    for label, value in (("fetched_at", entry.fetched_at), ("checked_at", entry.checked_at)):
        if value is not None and not _is_ts(value):
            bad(f"{label} is not a valid time")
    payload_fields = (entry.stored_bytes, entry.raw_bytes, entry.sha256)
    if any(v is not None for v in payload_fields):
        if not (_is_int(entry.stored_bytes) and _is_int(entry.raw_bytes) and _is_sha256(entry.sha256)):
            bad("bytes, raw_bytes and sha256 must be given together and be valid")
    elif entry.state == "ok":
        bad("state ok but no payload information (sha256)")
    if entry.state == "error" and entry.error is None:
        bad("state error but no error field")
    if entry.empty is not None:
        mark = entry.empty
        if not isinstance(mark, EmptyMark):
            bad("empty is not an object")
        elif not (_is_int(mark.count) and _is_int(mark.unverified)
                  and (mark.reason is None or isinstance(mark.reason, str))
                  and (mark.at is None or _is_ts(mark.at))):
            bad("empty is not valid")
    if entry.error is not None:
        failure = entry.error
        if not isinstance(failure, ErrorMark):
            bad("error is not an object")
        elif not (isinstance(failure.reason, str) and failure.reason
                  and (failure.status is None or _is_int(failure.status))
                  and (failure.at is None or _is_ts(failure.at))
                  and _is_int(failure.count, 1)):
            bad("error is not valid")
    if entry.history is not None:
        history = entry.history
        if not isinstance(history, HistoryMark):
            bad("history is not an object")
        elif not (_is_int(history.count) and (history.last_sha256 is None or _is_sha256(history.last_sha256))):
            bad("history is not valid")
    if entry.meta is not None and not isinstance(entry.meta, dict):
        bad("meta is not an object")


def validate(manifest: Manifest) -> List[str]:
    """Biçim 1 kurallarına göre sorunların listesi; boş liste = geçerli. Hata fırlatmaz."""
    problems: List[str] = []
    if not _is_int(manifest.format, 1):
        problems.append(f"invalid format {manifest.format!r}")
    if manifest.kind not in layout.KINDS:
        problems.append(f"invalid kind {manifest.kind!r}")
    if not _is_int(manifest.id):
        problems.append(f"invalid id {manifest.id!r}")
    for label, value in (("created_at", manifest.created_at), ("updated_at", manifest.updated_at)):
        if not _is_ts(value):
            problems.append(f"{label} is not a valid time")
    if manifest.migrated_from is not None and not (isinstance(manifest.migrated_from, str) and manifest.migrated_from):
        problems.append("migrated_from must be a non-empty string")
    observation = manifest.observation
    if observation is not None:
        if not isinstance(observation, Observation):
            problems.append("observation is not an object")
        elif not ((observation.observed_at is None or _is_ts(observation.observed_at))
                  and (observation.change_ts is None or _is_int(observation.change_ts))
                  and isinstance(observation.status_regressed, bool)):
            problems.append("observation is not valid")
    if not isinstance(manifest.slices, dict):
        problems.append("slices is not an object")
    else:
        for name, entry in manifest.slices.items():
            _validate_slice(name, entry, problems)
    return problems


# --- dışa açık işlevler --------------------------------------------------------------------------

def from_dict(data: Any, path: Optional[PathLike] = None) -> Manifest:
    """
    Ayrıştırılmış manifest.json → Manifest. Kurallara uymuyorsa PayloadCorrupt; biçimi bu sürümün
    bildiğinden yeniyse SchemaTooNew. `path` yalnızca hata iletisi içindir.
    """
    where = os.fspath(path) if path is not None else None
    if not isinstance(data, dict):
        raise PayloadCorrupt(f"The manifest is not a JSON object: {where}", path=where)
    found = data.get("format")
    if _is_int(found) and found > MANIFEST_FORMAT:
        raise SchemaTooNew(path=where, component="manifest", found=found, supported=MANIFEST_FORMAT)
    slices = data.get("slices", {})
    manifest = Manifest(
        kind=data.get("kind"),
        id=data.get("id"),
        created_at=_ts_in(data.get("created_at")),
        updated_at=_ts_in(data.get("updated_at")),
        format=found,
        migrated_from=data.get("migrated_from"),
        observation=_observation_in(data.get("observation")),
        slices={name: _slice_in(entry) for name, entry in slices.items()} if isinstance(slices, dict) else slices,
        extra=_rest(data, _MANIFEST_KEYS),
    )
    problems = validate(manifest)
    if problems:
        detail = "; ".join(problems)
        raise PayloadCorrupt(f"Invalid manifest ({detail}): {where}", path=where, detail=detail)
    return manifest


def to_dict(manifest: Manifest) -> Dict[str, Any]:
    """Manifest → yazılacak JSON nesnesi (her zaman güncel biçimde). Kurallara uymuyorsa LayoutError."""
    problems = validate(manifest)
    if problems:
        detail = "; ".join(problems)
        raise LayoutError(f"An invalid manifest cannot be written ({detail})", detail=detail)
    observation = manifest.observation
    return _with_extra({
        "format": MANIFEST_FORMAT,
        "kind": manifest.kind,
        "id": manifest.id,
        "created_at": _ts_out(manifest.created_at),
        "updated_at": _ts_out(manifest.updated_at),
        "migrated_from": manifest.migrated_from,
        "observation": _with_extra({
            "observed_at_utc": _ts_out(observation.observed_at) if observation.observed_at else None,
            "change_ts": observation.change_ts,
            "status_regressed": observation.status_regressed,
        }, observation.extra) if observation else None,
        "slices": {name: _slice_out(entry) for name, entry in manifest.slices.items()},
    }, manifest.extra)


def read_manifest(path: PathLike) -> Manifest:
    """manifest.json'ı okur. Dosya yoksa PayloadMissing; JSON değilse ya da kurallara uymuyorsa PayloadCorrupt."""
    raw = files.read_bytes(path)
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError) as e:
        detail = str(e) or type(e).__name__
        raise PayloadCorrupt(f"The manifest could not be read ({detail}): {os.fspath(path)}",
                             path=os.fspath(path), detail=detail) from e
    return from_dict(data, path)


def write_manifest(path: PathLike, manifest: Manifest, *, durable: Optional[bool] = None) -> None:
    """Manifesti doğrular ve atomik yazar (girintili, sıkıştırılmamış). Yük dosyalarından sonra çağrılır."""
    text = json.dumps(to_dict(manifest), ensure_ascii=False, indent=2) + "\n"
    files.write_bytes(path, text.encode("utf-8"), durable=durable)
