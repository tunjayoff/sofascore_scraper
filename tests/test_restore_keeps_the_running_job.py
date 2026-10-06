"""
Geri yükleme çalışan işin kaydını hiçbir an kaybettirmez (plan maddesi FX-13'ün Windows düzeltmesi).

Geri yükleme yedeğin state.db'sini SQLite'ın yedekleme API'siyle açık veritabanının üzerine yazar
(`BackupManager._load_state`). Geri yüklemeyi yapan işin (API'nin `restore` işi) satırı ve olayları korunur.
Önceden bu satırlar yedeklemeden *sonra* ayrı bir işlemle geri yazılıyordu: arada, açık veritabanını okuyan başka
bir bağlantı (işi soran web isteği) işin satırını bulamıyordu. Windows'ta (yavaş `fsync`) bu an uzundu ve
`GET /api/v1/jobs/<id>` CI'da 404 verdi; Linux'ta an kısaydı, test geçiyordu.

Bu test o anı her platformda yakalar: yedekleme adımı biter bitmez, geri yükleme sürerken, başka bir bağlantıyla
state.db okunur (Windows'taki yavaş adımın taklidi). İşin satırı, olayları, kilit satırları ve yeni olay günlüğü
kimliği o anda da görünmelidir. Tümü çevrimdışı.
"""
from __future__ import annotations

import sqlite3
import types
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.store import Store, StoreError
from src.store import backup as backup_mod
from src.store.streams import META_STREAM_ID
from test_store_backup import backup_all, into, rich_store


def _peek(db_path: str, job_id: str) -> Dict[str, Any]:
    """Ayrı bir bağlantıyla (başka bir okuyucu gibi) state.db'nin o anki hali."""
    conn = sqlite3.connect(db_path)
    try:
        return {
            "job": conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone(),
            "events": conn.execute("SELECT count(*) FROM job_events WHERE job_id = ?", (job_id,)).fetchone()[0],
            "leases": sorted(row[0] for row in conn.execute("SELECT name FROM leases")),
            "stream_id": conn.execute("SELECT value FROM meta WHERE key = ?", (META_STREAM_ID,)).fetchone(),
        }
    finally:
        conn.close()


def _watch_the_swap(monkeypatch: pytest.MonkeyPatch, store: Store, job_id: str) -> List[Dict[str, Any]]:
    """
    `src.store.backup`un gördüğü sqlite3'ü, açık veritabanına yapılan her yedekleme adımının hemen ardından
    state.db'yi ayrı bir bağlantıyla okuyan bir bağlantı sınıfıyla değiştirir; okunanları döndürür.
    """
    seen: List[Dict[str, Any]] = []
    db_path = store._state.path

    class Watching(sqlite3.Connection):
        def backup(self, target: Any, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
            super().backup(target, *args, **kwargs)
            if target is store._state.connection():
                seen.append(_peek(db_path, job_id))

    def connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        kwargs.setdefault("factory", Watching)
        return sqlite3.connect(*args, **kwargs)

    shim = types.SimpleNamespace(**{name: getattr(sqlite3, name) for name in dir(sqlite3) if not name.startswith("__")})
    shim.connect = connect
    monkeypatch.setattr(backup_mod, "sqlite3", shim)
    return seen


def test_the_running_job_is_never_missing_while_state_db_is_swapped(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    source, _ = rich_store(tmp_path / "source")
    info = backup_all(source)
    target, fixture = rich_store(tmp_path / "target")
    name = into(info.path, fixture.data_dir)
    # Geri yüklemeyi yapan iş: `maintenance` kilidiyle çalışıyor (API'nin `restore` işi gibi), bir olay yazmış
    job_id = target.jobs.create_running({"kind": "restore"}, kind="restore", lease="maintenance")
    target.jobs.append_event(job_id, "phase", {"phase": "restore"})
    before = _peek(target._state.path, job_id)
    assert before["job"] == ("running",) and before["events"] >= 1 and "maintenance" in before["leases"]
    seen = _watch_the_swap(monkeypatch, target, job_id)

    target.backup.restore(name, force=True)

    assert seen, "the restore did not swap state.db through the backup API"
    for moment in seen:  # yedekleme adımının hemen ardından, geri yükleme bitmeden
        assert moment["job"] == ("running",), moment
        assert moment["events"] == before["events"], moment
        assert moment["leases"] == before["leases"], moment
        assert moment["stream_id"] != before["stream_id"], moment
    after = _peek(target._state.path, job_id)
    assert after["job"] == ("running",) and after["events"] == before["events"]
    assert target.jobs.read_events(job_id)[-1]["type"] == "phase"
    target.jobs.update(status="Completed", finished=True)


def test_a_rolled_back_restore_keeps_the_running_job_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Geri alma da önceki state.db'yi aynı yolla geri yazar: iş satırı orada da korunur."""
    source, _ = rich_store(tmp_path / "source")
    info = backup_all(source)
    target, fixture = rich_store(tmp_path / "target")
    name = into(info.path, fixture.data_dir)
    job_id = target.jobs.create_running({"kind": "restore"}, kind="restore", lease="maintenance")
    real = backup_mod.BackupManager._load_state
    calls: List[str] = []

    def failing(self: backup_mod.BackupManager, path: str) -> None:
        calls.append(path)
        real(self, path)
        if len(calls) == 1:
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(backup_mod.BackupManager, "_load_state", failing)
    seen = _watch_the_swap(monkeypatch, target, job_id)
    with pytest.raises(StoreError) as caught:
        target.backup.restore(name, force=True)

    assert getattr(caught.value, "errno", None) == 28
    assert len(calls) == 2 and len(seen) == 2
    assert all(moment["job"] == ("running",) for moment in seen), seen
    target.jobs.update(status="Completed", finished=True)
