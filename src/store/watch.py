"""
İzleyici durumu: maç başına son bilinen durum, state.db'nin `watch_state` tablosunda
(docs/design/01-storage.md, bölüm 2.3 `WatchStateStore` ve 9.3).

2.x izleyicisi durumunu `watch_state_<spor>.json` dosyasında tutardı (maç id'si → durum sözlüğü). Burada
aynı sözlük maç başına bir satırdır; bir izleyicinin satırları `watcher` adıyla ayrılır (bugün spor adı).
Biten maçların satırları WATCH_STATE_RETENTION_SECONDS sonra, bir sonraki `save` sırasında silinir.

2.x dosyalarıyla ilgili üç yardımcı da buradadır, çünkü DATA_DIR'e yalnızca Store dokunur:
  * `import_legacy`: `watch_state_<spor>.json` dosyasını **bir kez** tabloya alır (bölüm 8.3).
  * `mirror_legacy_state`: izleyici, canlı servis onun yerini alana kadar (plan maddesi P23) durum
    dosyasını da yazmayı sürdürür; dosya bir kopyadır, yetkili olan tablodur.
  * `append_legacy_events`: `watch_events.jsonl` satırları. Bayt olarak ve LF ile eklenir: 2.x metin
    kipinde yazdığı için Windows'ta CRLF üretiyordu, öteki bütün veri dosyaları LF'dir.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, Mapping, Optional, Sequence

from src.store import files, layout
from src.store.errors import LayoutError, PayloadMissing, StoreError

if TYPE_CHECKING:
    from src.store.api import Store

logger = logging.getLogger("Store")

WATCH_STATE_RETENTION_SECONDS = 7 * 86400  # bitmiş maçın durumu bu kadar saklanır (bölüm 9.3)

# 2.x dosya adları (src/watcher.py ve src/store/legacy.py'deki sabitlerle aynı; testler eşitliği denetler)
LEGACY_EVENTS_FILE = "watch_events.jsonl"
LEGACY_STATE_FILE = "watch_state_{sport}.json"
META_IMPORTED_PREFIX = "imported_watch_state:"  # meta anahtarı: + spor adı

_EVENT_KEY_RE = re.compile(r"-?[0-9]+")


def legacy_state_file(sport: str) -> str:
    """Sporun 2.x durum dosyasının adı. Spor adı tek bir yol bileşeni olmalıdır; değilse LayoutError."""
    if (not isinstance(sport, str) or not sport or sport in (".", "..")
            or any(ch in sport for ch in ("/", "\\", "\0"))):
        raise LayoutError(f"Geçersiz spor adı: {sport!r}")
    return LEGACY_STATE_FILE.format(sport=sport)


def _event_key(key: Any) -> int:
    """Durum sözlüğünün anahtarı (maç id'si, metin ya da tam sayı) → tam sayı; değilse StoreError."""
    if isinstance(key, bool):
        raise StoreError(f"Geçersiz maç kimliği: {key!r}", detail="event_id")
    if isinstance(key, int):
        return key
    if isinstance(key, str) and _EVENT_KEY_RE.fullmatch(key):
        return int(key)
    raise StoreError(f"Geçersiz maç kimliği: {key!r}", detail="event_id")


def _dump(event_id: int, value: Any) -> str:
    if not isinstance(value, Mapping):
        raise StoreError(f"İzleyici durumu bir sözlük olmalı (maç {event_id}): {type(value).__name__}",
                         detail="state")
    try:
        # Anahtar sırası korunur: yeniden yüklenen durum, yazılan sözlükle aynı sırada döner
        return json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as e:
        raise StoreError(f"İzleyici durumu JSON'a çevrilemedi (maç {event_id}): {e}", detail=str(e)) from e


class WatchStateStore:
    """`Store.watch`: izleyicilerin maç başına durumu (2.x'in `watch_state_<spor>.json` dosyasının yerine)."""

    def __init__(self, store: "Store", *, retention_s: Optional[float] = WATCH_STATE_RETENTION_SECONDS,
                 clock: Callable[[], float] = time.time) -> None:
        self._state = store._state
        self._data_dir = os.fspath(store.data_dir)
        self._retention_s = retention_s  # None: hiçbir satır yaşı yüzünden silinmez
        self._clock = clock

    # --- tablo ------------------------------------------------------------------------------

    def load(self, watcher: str) -> Dict[str, Dict[str, Any]]:
        """
        İzleyicinin durumu: maç id'si (metin, dosyadaki gibi) → durum sözlüğü, maç id'si sırasıyla.
        Okunamayan satır atlanır (bir sonraki `save` üzerine yazar).
        """
        out: Dict[str, Dict[str, Any]] = {}
        for event_id, state_json in self._state.connection().execute(
                "SELECT event_id, state_json FROM watch_state WHERE watcher = ? ORDER BY event_id", (watcher,)):
            try:
                value = json.loads(state_json)
            except ValueError:
                value = None
            if not isinstance(value, dict):
                logger.warning("Watch state of event %s (watcher %s) is unreadable; ignored", event_id, watcher)
                continue
            out[str(event_id)] = value
        return out

    def save(self, watcher: str, state: Mapping[str, Mapping[str, Any]], *,
             changed: Optional[Iterable[str]] = None) -> None:
        """
        Durumu tek işlemde yazar.

        changed=None: izleyicinin saklanan durumu `state` olur (farklı satırlar yazılır, `state`te olmayan
        satırlar silinir). changed verilirse yalnızca o maçların satırlarına dokunulur: `state`te olan
        yazılır, olmayan silinir; çağıran neyin değiştiğini biliyorsa bütün durumu karşılaştırmak gerekmez.

        Her iki kipte de, bu çağrının yazmadığı satırlardan bitmiş (`done`) ve `retention_s`'den eski
        olanlar silinir.
        """
        if not isinstance(watcher, str) or not watcher:
            raise StoreError(f"İzleyici adı boş olamaz: {watcher!r}", detail="watcher")
        rows = {_event_key(key): value for key, value in state.items()}
        now = int(self._clock())
        with self._state.write() as conn:
            if changed is None:
                stored = {int(event_id): str(text) for event_id, text in conn.execute(
                    "SELECT event_id, state_json FROM watch_state WHERE watcher = ?", (watcher,))}
                writes = {event_id: text for event_id, text in
                          ((event_id, _dump(event_id, value)) for event_id, value in rows.items())
                          if stored.get(event_id) != text}
                deletes = [event_id for event_id in stored if event_id not in rows]
            else:
                ids = list(dict.fromkeys(_event_key(key) for key in changed))
                writes = {event_id: _dump(event_id, rows[event_id]) for event_id in ids if event_id in rows}
                deletes = [event_id for event_id in ids if event_id not in rows]
            conn.executemany(
                """
                INSERT INTO watch_state (watcher, event_id, state_json, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(watcher, event_id) DO UPDATE SET
                    state_json = excluded.state_json, updated_at = excluded.updated_at
                """,
                [(watcher, event_id, text, now) for event_id, text in writes.items()],
            )
            conn.executemany("DELETE FROM watch_state WHERE watcher = ? AND event_id = ?",
                             [(watcher, event_id) for event_id in deletes])
            if self._retention_s is not None:
                # `done` JSON'un içindedir; SQLite'ın JSON işlevlerine güvenilmez (3.38 öncesinde isteğe bağlı)
                expired = []
                for event_id, text in conn.execute(
                        "SELECT event_id, state_json FROM watch_state WHERE watcher = ? AND updated_at < ?",
                        (watcher, now - self._retention_s)):
                    try:
                        value = json.loads(text)
                    except ValueError:
                        continue
                    if isinstance(value, dict) and value.get("done"):
                        expired.append((watcher, int(event_id)))
                conn.executemany("DELETE FROM watch_state WHERE watcher = ? AND event_id = ?", expired)

    # --- 2.x dosyaları ----------------------------------------------------------------------

    def import_legacy(self, sport: str, *, watcher: Optional[str] = None) -> Optional[int]:
        """
        `watch_state_<spor>.json` dosyasını, daha önce alınmadıysa, `watcher`'ın (varsayılan: spor adı)
        durumuna ekler ve alınan maç sayısını döndürür; daha önce alındıysa None.

        Bir kez yapılır: sonuç `meta` tablosuna yazılır (dosya yoksa ya da bozuksa da, 2.x izleyicisi
        o durumda boş durumla başlardı). Tabloda zaten satırı olan maçın satırı değişmez. Dosya
        okunamıyorsa (izin, G/Ç hatası) kayıt yazılmaz ve bir sonraki çağrı yeniden dener.
        """
        name = legacy_state_file(sport)
        target = watcher if watcher is not None else sport
        key = META_IMPORTED_PREFIX + sport
        if self._state.meta_get(key) is not None:
            return None
        record: Dict[str, Any] = {"file": name, "watcher": target, "found": False, "rows": 0, "imported": 0}
        rows: Dict[int, str] = {}
        try:
            raw: Optional[bytes] = files.read_bytes(layout.resolve(self._data_dir, name))
        except PayloadMissing:
            raw = None
        except StoreError as e:
            logger.warning("Legacy watch state %s could not be read; it will be tried again: %s", name, e.detail)
            return 0
        if raw is not None:
            record["found"] = True
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                data = None
            if not isinstance(data, dict):
                record["problem"] = "corrupt"
                logger.warning("Legacy watch state %s is not a JSON object; ignored", name)
            else:
                record["rows"] = len(data)
                for event_key, value in data.items():
                    try:
                        event_id = _event_key(event_key)
                        rows[event_id] = _dump(event_id, value)
                    except StoreError:
                        continue  # 2.x izleyicisi de bu girdiyle çalışamazdı
        now = int(self._clock())
        with self._state.write() as conn:
            # İşlem içinde yeniden bakılır: aynı anda başlayan iki süreçten yalnızca biri alır
            if conn.execute("SELECT 1 FROM meta WHERE key = ?", (key,)).fetchone() is not None:
                return None
            imported = 0
            for event_id, text in rows.items():
                imported += conn.execute(
                    "INSERT INTO watch_state (watcher, event_id, state_json, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(watcher, event_id) DO NOTHING",
                    (target, event_id, text, now),
                ).rowcount
            record["imported"] = imported
            record["at"] = now
            conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)",
                         (key, json.dumps(record, ensure_ascii=False, sort_keys=True)))
        if record["found"]:
            logger.info("Legacy watch state %s imported: %s of %s entries", name, imported, record["rows"])
        return imported

    def mirror_legacy_state(self, sport: str, state: Mapping[str, Mapping[str, Any]]) -> None:
        """
        Durumu 2.x biçiminde `watch_state_<spor>.json` dosyasına da yazar (atomik; girintili JSON).
        Dosya yalnızca bir kopyadır: hiçbir 3.x kodu onu geri okumaz (ilk `import_legacy` dışında).
        """
        path = layout.resolve(self._data_dir, legacy_state_file(sport))
        try:
            text = json.dumps(state, ensure_ascii=False, indent=2)
        except (TypeError, ValueError) as e:
            raise StoreError(f"İzleyici durumu JSON'a çevrilemedi: {e}", path=path, detail=str(e)) from e
        files.write_bytes(path, text.encode("utf-8"))

    def append_legacy_events(self, lines: Sequence[str]) -> None:
        """
        Satırları `watch_events.jsonl` dosyasının sonuna ekler: her biri UTF-8 ve LF ile, hepsi tek yazmada
        (aynı dosyaya ekleyen başka bir izleyicinin satırlarıyla karışmaz).
        """
        if not lines:
            return
        path = layout.resolve(self._data_dir, LEGACY_EVENTS_FILE)
        data = "".join(f"{line}\n" for line in lines).encode("utf-8")
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "ab") as f:
                f.write(data)
                if files.durability_full():
                    f.flush()
                    os.fsync(f.fileno())
        except OSError as e:
            raise StoreError.from_exception(e, path) from e


__all__ = [
    "LEGACY_EVENTS_FILE",
    "LEGACY_STATE_FILE",
    "WATCH_STATE_RETENTION_SECONDS",
    "WatchStateStore",
    "legacy_state_file",
]
