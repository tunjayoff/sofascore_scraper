"""
FX-13'ün Store okumaları (docs/design/03-implementation-plan.md FX-13; ST-22 ve ST-23'ün notları):

  * `EntityStore.tournaments_with_slice`: sezon listesi saklanan turnuvalar, maçları, takipleri ya da satırları
    olmasa da; `layout_name` ile yalnızca v3'tekiler.
  * `services.tournaments.season_lists` bu listeyi kullanır: yalnızca v3'te duran, yapılandırılmamış ve takip
    edilmeyen bir turnuvanın listesi bulunur; v3 listesi adında kimlik olmayan eski bir dosyaya yenilmez.
  * `Migrator.last_run`: son taşıma çalıştırması (`migration_runs`), hiç yoksa None.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

import store_fixtures as sf
from sofascore_scraper.services import listing, tournaments
from sofascore_scraper.store import FollowSpec, LayoutError, Store, open_store


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    monkeypatch.setenv("SOFASCORE_STORAGE__DATA_DIR", str(path))
    return path


def season_list(*season_ids: int) -> Dict[str, Any]:
    return {"seasons": [{"id": season_id, "name": f"S {season_id}", "year": "26/27"} for season_id in season_ids]}


def write(data_dir: Path, rel: str, content: Any, mtime: int = sf.BASE_MTIME) -> None:
    path = data_dir.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(content).encode("utf-8"))
    os.utime(path, (mtime, mtime))


def ids(seasons: Optional[List[Any]]) -> Optional[List[Any]]:
    return None if seasons is None else [s["id"] for s in seasons]


def reopened(data_dir: Path) -> Store:
    open_store(data_dir).close()
    return open_store(data_dir)


def test_tournaments_with_a_season_list_are_listed_by_layout(data_dir: Path) -> None:
    write(data_dir, "seasons/17_seasons.json", season_list(171))  # eski düzen
    store = open_store(data_dir)
    listing.store_season_list(store, 99, season_list(991))  # v3, maçı ve takibi yok
    listing.store_season_list(store, 98, {"seasons": []})  # boş liste da saklanır (yük var)

    assert store.entities.tournaments_with_slice("seasons") == [17, 98, 99]
    assert store.entities.tournaments_with_slice("seasons", layout_name="v3") == [98, 99]
    assert store.entities.tournaments_with_slice("seasons", layout_name="legacy") == [17]
    assert store.entities.tournaments_with_slice("standings") == []
    with pytest.raises(LayoutError):
        store.entities.tournaments_with_slice("Not A Key")


def test_a_v3_only_list_of_an_unknown_tournament_is_found(data_dir: Path) -> None:
    """ST-22'nin notu: maçı, takibi ve satırı olmayan bir turnuvanın yalnızca v3'te duran listesi."""
    store = open_store(data_dir)
    listing.store_season_list(store, 4242, season_list(1, 2))
    store = reopened(data_dir)
    found = {lid: ids(s) for lid, s in tournaments.season_lists(store, {}).items()}
    assert found == {4242: [1, 2]}


def test_a_v3_list_wins_over_a_name_only_legacy_file(data_dir: Path) -> None:
    """
    Plan bölüm 15'in yeni satırı (ST-22): yapılandırmadaki ad takip adından farklıyken adında kimlik olmayan eski
    dosya (`LaLiga_seasons.json`) v3 listesinin önüne geçiyordu. v3 listesi saklandıysa o konuşur.
    """
    write(data_dir, "seasons/LaLiga_seasons.json", season_list(81))
    store = open_store(data_dir)
    store.follows.apply([FollowSpec(kind="tournament", entity_id=8, name="la-liga")], origin="config")
    store = reopened(data_dir)
    # v3 listesi yokken eski dosya addan bulunur (bugünkü kural)
    assert ids(tournaments.seasons_of(store, 8, name="LaLiga")) == [81]

    listing.store_season_list(store, 8, season_list(82, 83))
    assert ids(tournaments.seasons_of(store, 8, name="LaLiga")) == [82, 83]
    assert {lid: ids(s) for lid, s in tournaments.season_lists(store, {8: "LaLiga"}).items()} == {8: [82, 83]}


def test_last_migration_run(data_dir: Path) -> None:
    store = open_store(data_dir)
    assert store.migrate.last_run() is None

    report = store.migrate.run()
    run = store.migrate.last_run()
    assert run is not None
    assert run["id"] == report.run_id
    assert run["finished_at"] is not None and run["started_at"] <= run["finished_at"]
    assert run["events_failed"] == 0 and run["delete_legacy"] is False
    assert set(run) == {"id", "started_at", "finished_at", "delete_legacy", "events_done", "events_failed",
                        "bytes_before", "bytes_after"}
