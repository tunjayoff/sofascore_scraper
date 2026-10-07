"""
Yedeği alan iş, geri yüklenen geçmişte yarıda kalmış görünmez (FX-23, bulgu F30).

Web'in ve zamanlayıcının `backup` işi yedeği kendisi çalışırken alır: state.db'nin kopyasında satırı "running"
durur. Geri yüklemeden sonra o satırın sahibi yoktur ve süpürme onu `interrupted` yapıyordu; Genel bakış her geri
yüklemeden sonra "Son Yedekleme işi yarıda kaldı" diyordu. İki düzeltme:

- yedek, aldıran işin satırını kopyada bitmiş (`completed`) yazar (`job_id`);
- geri yükleme, geri yüklenen geçmişte çalışıyor görünen ama bu veri dizininde bitmiş bir işin bugünkü satırını
  alır (düzeltmeden önce alınmış yedekler için).

Tümü çevrimdışı.
"""
from __future__ import annotations

import sqlite3
import zipfile
from pathlib import Path
from typing import Optional, Tuple

from sofascore_scraper.store import Store
from sofascore_scraper.store import backup as backup_mod
from test_store_backup import STAMP, into, rich_store


def _archived_job(path: str, job_id: str, tmp_path: Path) -> Optional[Tuple[str, Optional[str], int]]:
    """Yedekteki state.db'de işin (durum, bitiş zamanı, ilerleme) satırı."""
    target = tmp_path / "archived_state.db"
    with zipfile.ZipFile(path) as zf:
        target.write_bytes(zf.read(backup_mod.STATE_MEMBER))
    conn = sqlite3.connect(target)
    try:
        return conn.execute("SELECT status, finished_at, progress FROM jobs WHERE id = ?", (job_id,)).fetchone()
    finally:
        conn.close()


def _backup_job(store: Store) -> str:
    """Web'in `backup` işi gibi: `writer` kilidini tutan çalışan bir iş."""
    return store.jobs.create_running({"kind": "backup"}, kind="backup", spec={"scope": "all"})


def _status(store: Store, job_id: str) -> str:
    row = store._state.connection().execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return str(row[0])


def test_the_archive_records_the_job_that_takes_it_as_finished(tmp_path: Path) -> None:
    store, _ = rich_store(tmp_path / "source")
    job_id = _backup_job(store)

    info = store.backup.create("all", now=STAMP, job_id=job_id)
    store.jobs.update(status="Completed", finished=True)

    status, finished_at, progress = _archived_job(info.path, job_id, tmp_path)
    assert (status, progress) == ("completed", 100) and finished_at
    assert _status(store, job_id) == "completed"


def test_without_a_job_id_the_archive_is_unchanged(tmp_path: Path) -> None:
    """`ssc backup create` yedeği iş açmadan alır: kopyadaki başka bir çalışan satıra dokunulmaz."""
    store, _ = rich_store(tmp_path / "source")
    job_id = _backup_job(store)

    info = store.backup.create("all", now=STAMP)
    store.jobs.update(status="Completed", finished=True)

    assert _archived_job(info.path, job_id, tmp_path)[0] == "running"


def test_a_restore_elsewhere_shows_the_backup_job_finished(tmp_path: Path) -> None:
    source, _ = rich_store(tmp_path / "source")
    job_id = _backup_job(source)
    info = source.backup.create("all", now=STAMP, job_id=job_id)
    source.jobs.update(status="Completed", finished=True)
    target, fixture = rich_store(tmp_path / "target")
    name = into(info.path, fixture.data_dir)

    target.backup.restore(name, force=True)

    assert target.jobs.reap_stale() == 0
    assert _status(target, job_id) == "completed"
    assert not [j for j in target.jobs.list_jobs() if j.get("status") == "interrupted"]


def test_an_older_archive_restored_in_place_keeps_the_finished_record_of_its_job(tmp_path: Path) -> None:
    """Düzeltmeden önce alınmış yedek: kopyada iş "running"; aynı veri dizinine geri yüklenince bitmiş satır kalır."""
    store, fixture = rich_store(tmp_path / "source")
    job_id = _backup_job(store)
    store.jobs.append_event(job_id, "phase", {"phase": "backup"})
    info = store.backup.create("all", now=STAMP)  # eski davranış: iş kimliği verilmedi
    store.jobs.update(status="Completed", finished=True, result={"backup": {"name": info.name}})
    assert _archived_job(info.path, job_id, tmp_path)[0] == "running"
    events_before = len(store.jobs.read_events(job_id))

    store.backup.restore(info.name, force=True)

    assert store.jobs.reap_stale() == 0
    assert _status(store, job_id) == "completed"
    assert len(store.jobs.read_events(job_id)) == events_before
    assert store.jobs.get_job(job_id)["result"] == {"backup": {"name": info.name}}  # sonuç da canlı kayıttan
