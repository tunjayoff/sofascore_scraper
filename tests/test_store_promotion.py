"""
Hazırlık alanının sahiplerine göre temizlenmesi (plan maddesi ST-20; karar S16, docs/design/01-storage.md
bölüm 9.3): `.meta/tmp` altındaki girdiler sahibinin adını taşır ve bir kilit yalnızca kendi girdilerini siler.
Eski düzendeki bir maça yazma (v3'e yükseltme) testleri, yazıcıyla birlikte bu dosyaya eklenir.
"""
from __future__ import annotations

import gc
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.store import StoreError, open_store
from src.store import files, layout
from src.store import lease as lease_mod
from src.store.lease import LeaseManager


# --- hazırlık alanının temizlenmesi: bir kilit yalnızca kendi girdilerini siler -------------------------

def tmp_entries(data_dir: Any) -> List[str]:
    try:
        return sorted(os.listdir(layout.resolve(data_dir, layout.TMP_DIR)))
    except FileNotFoundError:
        return []


def test_held_here_follows_the_leases_of_this_process(tmp_path: Path) -> None:
    first = LeaseManager.for_data_dir(tmp_path / "data")
    second = LeaseManager.for_data_dir(tmp_path / "data")
    elsewhere = LeaseManager.for_data_dir(tmp_path / "other")
    assert first.data_dir == os.path.abspath(tmp_path / "data")
    assert LeaseManager(tmp_path / "locks").data_dir is None  # bir veri dizininin kilit dizini değil

    lease = first.acquire("writer")
    assert first.held_here("writer") and second.held_here("writer") and not elsewhere.held_here("writer")
    assert not first.held_here("live") and not first.held_here("maintenance")
    lease.release()
    assert not first.held_here("writer") and not second.held_here("writer")

    dropped = second.acquire("live")
    assert first.held_here("live")
    del dropped  # bırakılmadan çöpe giden kilit de düşer
    gc.collect()
    assert not first.held_here("live")


def _aged(path: str, seconds: float) -> str:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))
    return os.path.basename(path)


def test_a_lease_purges_only_the_staging_entries_of_its_own_holder(tmp_path: Path) -> None:
    store = open_store(tmp_path / "data")
    data_dir = store.data_dir
    day = lease_mod.STAGING_KEEP_SECONDS

    def populate() -> Dict[str, str]:
        made = {
            "writer": os.path.basename(files.new_staging_dir(data_dir, "writer")),
            "writer_old": _aged(files.new_staging_dir(data_dir, "writer"), 3 * day),
            "live": os.path.basename(files.new_staging_dir(data_dir, "live")),
            "live_old": _aged(files.new_staging_dir(data_dir, "live"), 3 * day),
            "export": os.path.basename(files.new_staging_dir(data_dir, "export")),
            "export_old": _aged(files.new_staging_dir(data_dir, "export"), day + 60),
            "put": os.path.basename(files.new_staging_dir(data_dir, "put")),
            "put_old": _aged(files.new_staging_dir(data_dir, "put"), day + 60),
            "plain": os.path.basename(files.new_staging_dir(data_dir)),
            "plain_old": _aged(files.new_staging_dir(data_dir), 2 * day),
        }
        files.write_bytes(os.path.join(layout.resolve(data_dir, layout.TMP_DIR), made["writer"], "x.json.gz"), b"x")
        return made

    def left(made: Dict[str, str]) -> List[str]:
        present = set(tmp_entries(data_dir))
        return sorted(label for label, name in made.items() if name in present)

    made = populate()
    trash = layout.resolve(data_dir, layout.TRASH_DIR)
    os.makedirs(os.path.join(trash, "16837335.abc"))
    everything = sorted(made)

    for name in ("maintenance", "sinks", "watcher:tennis"):  # bu kilitler hiçbir şeyi silmez
        with store.lease(name):
            assert left(made) == everything
    assert os.listdir(trash) == ["16837335.abc"]

    with store.lease("live"):
        assert left(made) == sorted(set(everything) - {"live", "live_old"})
        assert os.listdir(trash) == ["16837335.abc"]

    made = populate()
    with store.lease("writer", purpose="job"):
        # kendi girdileri (yaşına bakılmadan), kilide bağlı olmayan eski girdiler ve çöp; canlı servisinkiler durur
        assert left(made) == ["export", "live", "live_old", "plain", "put"]
        assert os.listdir(trash) == []
    assert files.purge_staging(data_dir) >= 5 and tmp_entries(data_dir) == []


def test_purge_staging_filters_by_holder_and_age(tmp_path: Path) -> None:
    data = tmp_path / "data"
    assert files.purge_staging(data, "writer") == 0  # .meta henüz yok
    names = {label: os.path.basename(files.new_staging_dir(data, label)) for label in ("writer", "live", "a.b", "")}
    old = _aged(files.new_staging_dir(data, "export"), 100)
    tmp = Path(layout.resolve(data, layout.TMP_DIR))
    (tmp / "writer").write_bytes(b"x")  # noktasız ad: etiketi yok

    assert [files.staging_holder(n) for n in (names["writer"], names["a.b"], names[""], "writer")] == [
        "writer", "a.b", "", ""]
    assert files.purge_staging(data, "export", older_than=3600) == 0  # yeterince eski değil
    assert files.purge_staging(data, older_than=50, skip=("export",)) == 0
    assert files.purge_staging(data, "export", older_than=50) == 1 and not (tmp / old).exists()
    assert files.purge_staging(data, "writer") == 1
    assert sorted(os.listdir(tmp)) == sorted([names["live"], names["a.b"], names[""], "writer"])
    assert files.purge_staging(data, skip=("live", "a.b")) == 2
    assert sorted(os.listdir(tmp)) == sorted([names["live"], names["a.b"]])
    assert files.purge_staging(data) == 2 and os.listdir(tmp) == []


def test_a_failing_purge_does_not_refuse_the_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                   caplog: pytest.LogCaptureFixture) -> None:
    store = open_store(tmp_path / "data")

    def refuse(*args: Any, **kwargs: Any) -> int:
        raise StoreError("izin yok", errno_code=13)

    monkeypatch.setattr(files, "purge_staging", refuse)
    with caplog.at_level(logging.WARNING):
        with store.lease("writer") as lease:
            assert lease.held and store.lease_holder("writer") is not None
    assert [r.getMessage() for r in caplog.records if r.name == "Store" and r.levelno >= logging.WARNING] == [
        "Leftover staging entries could not be removed (writer): izin yok"]
