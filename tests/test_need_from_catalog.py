"""
İndirme planı katalogdan (plan maddesi RD-3): `_needs_detail_fetch`, `pending_detail_ids`, `refresh_due_ids`,
`collect_detail_match_ids` dosya okumaz, kataloğa sorar (`planning.event_needs`, `refresh_due_events`,
`QueryService.detail_candidates`).

Dört küme:
  * özellik testi: rastgele dilim, işaret ve gözlem durumlarında katalogdan çıkan ihtiyaç, dosyalardan elle
    hesaplanan ihtiyaca (RD-3 öncesinin kuralı: dilim dosyaları, `_unavailable.json`, `observation.json`,
    `sofascore_scraper.refresh.refresh_due`) eşittir; yenilenecekler ve iş önbelleği de;
  * eski biçimler: düz, `_no_tournament/` ve kimliksiz lig dizinlerindeki kayıtlar yenilenir, lig süzgeci
    maçın turnuvasına bakar;
  * listeler: sezon sırası, "yalnızca bitmiş maçlar" ayarı, durumu bilinmeyen özet satırları;
  * katalog güncel değilse plan yapılmaz (CatalogNotCurrent).
Ağ yok.
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

import store_fixtures as sf
from sofascore_scraper import refresh
from sofascore_scraper.exceptions import StorageError
from sofascore_scraper.services.detail_phase import UNAVAILABLE_AFTER_ATTEMPTS, DetailPhase
from sofascore_scraper.services.query import (
    NEED_FULL,
    NEED_NONE,
    NEED_REFILL,
    NEED_REFRESH,
    CatalogNotCurrent,
    RefreshPolicy,
    required_detail_keys,
)
from sofascore_scraper.slices import match_detail_slice_present
from sofascore_scraper.sports import event_sport_slug, slices_for
from sofascore_scraper.services import planning
from sofascore_scraper.store import open_store
from test_store_read_api import HOUR, NOW, _random_details

UNKNOWN_ID = 1


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("SOFASCORE_REFRESH__WINDOW_HOURS", "SOFASCORE_REFRESH__MIN_INTERVAL_HOURS", "SOFASCORE_REFRESH__INCLUDE_LEGACY", "SOFASCORE_FETCH__ONLY_FINISHED"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(time, "time", lambda: float(NOW))  # yenileme kararı: saat FIXTURE_NOW'da durur


UNAVAILABLE_FILE = "_unavailable.json"  # eski düzenin "yok" sayaçları (tests/legacy_writer.py)


def details_of(data_dir: Path) -> DetailPhase:
    """Veri dizininin detay aşaması (bir işin ömrü: ihtiyaç önbelleği boş başlar)."""
    return DetailPhase(open_store(data_dir), MagicMock())


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None


def file_need(directory: Path) -> str:
    """RD-3 öncesinin kuralı, dosyalardan: basic.json, beklenen dilimler (işaretler hariç), gözlem."""
    basic = _read(directory / "basic.json")
    if not isinstance(basic, dict) or not basic:
        return NEED_FULL
    unavailable = _read(directory / UNAVAILABLE_FILE)
    counts = {str(k): int(v) for k, v in unavailable.items()} if isinstance(unavailable, dict) else {}
    for detail in slices_for(event_sport_slug(basic) or "", required_only=True):
        if counts.get(detail.key, 0) >= UNAVAILABLE_AFTER_ATTEMPTS:
            continue
        path = directory / f"{detail.key}.json"
        if not path.exists() or not match_detail_slice_present(detail.key, {detail.key: _read(path)}):
            return NEED_REFILL
    observation = _read(directory / "observation.json")
    if refresh.refresh_due(basic, observation if isinstance(observation, dict) else {}, now=float(NOW)):
        return NEED_REFRESH
    return NEED_NONE


# --- özellik testi ------------------------------------------------------------------------------------

@pytest.mark.parametrize("seed", range(8))
def test_needs_from_the_catalog_equal_the_file_based_ones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                          seed: int) -> None:
    rng = random.Random(1000 + seed)
    window_h, min_interval_h = rng.choice([(72, 6), (1, 0), (24, 12), (200, 1)])
    monkeypatch.setenv("SOFASCORE_REFRESH__WINDOW_HOURS", str(window_h))
    monkeypatch.setenv("SOFASCORE_REFRESH__MIN_INTERVAL_HOURS", str(min_interval_h))
    monkeypatch.setenv("SOFASCORE_REFRESH__INCLUDE_LEGACY", "true" if rng.random() < 0.5 else "false")
    builder = sf._Builder("random", tmp_path / "data", (sf.PL, sf.FA_CUP, sf.NBA, sf.WIMBLEDON, sf.LALIGA))
    for detail in _random_details(rng, window_h * HOUR, min_interval_h * HOUR):
        builder.detail(detail)
    fx = builder.fixture
    expected = {str(record.event_id): file_need(fx.data_dir / record.path) for record in fx.details}
    expected[str(UNKNOWN_ID)] = NEED_FULL
    ids = sorted(expected, key=int)
    # P13: tasarım tablosunun canlı ve bayat satırları dosya kuralının önüne geçer (canlı kayıt `none`: canlı
    # servisin işi; daha yeni bir listenin bayatlamış saydığı kayıt `refresh`)
    from sofascore_scraper.store import open_store

    # ST-27: açık kayıt (başlamamış, oynanıyor, bilinmiyor) `none`; void kayıt dilim beklemez, yalnızca yenilenir
    from sofascore_scraper.services.planning import SETTLED_CLASSES, refresh_due
    from sofascore_scraper.services.query import RefreshPolicy

    store = open_store(fx.data_dir)
    for mid in ids:
        row = store.events.get(int(mid))
        if row is not None and row.has_event_payload and row.stale:
            expected[mid] = NEED_REFRESH
        elif row is not None and row.has_event_payload and row.status_class not in SETTLED_CLASSES:
            expected[mid] = NEED_NONE
        elif row is not None and row.has_event_payload and row.status_class == "void":
            expected[mid] = NEED_REFRESH if refresh_due(row, RefreshPolicy.current()) else NEED_NONE

    plain = details_of(fx.data_dir)
    assert {mid: plain.needs([mid])[mid] for mid in ids} == expected
    # iş önbelleği: bütün kimlikler tek seferde hesaplanır, kararlar aynıdır
    cached = details_of(fx.data_dir)
    pending = cached.pending(ids)
    assert cached._needs == expected
    assert pending == [mid for mid in ids if expected[mid] in (NEED_FULL, NEED_REFILL)] + [
        mid for mid in ids if expected[mid] == NEED_REFRESH]
    by_path = sorted((record for record in fx.details if expected[str(record.event_id)] == NEED_REFRESH),
                     key=lambda record: record.path.split("/"))
    assert details_of(fx.data_dir).refresh_due() == [str(record.event_id) for record in by_path]


def test_identifiers_that_are_not_event_ids_need_a_full_fetch(tmp_path: Path) -> None:
    fx = sf.build_fixture("canonical", tmp_path / "data")
    details = details_of(fx.data_dir)
    stored = str(fx.detail_ids[0])
    needs = details.needs(["0" + stored, "abc", "-1", str(2 ** 64), ""])
    assert set(needs.values()) == {NEED_FULL}
    assert details.needs([stored])[stored] != NEED_FULL
    assert details.refresh_due("abc") == [] and details.refresh_due("017") == []


def test_required_detail_keys_follow_the_slice_table() -> None:
    required = required_detail_keys()
    assert required[""] == tuple(detail.key for detail in slices_for(None, required_only=True))
    # Store.events.missing(exclusive=True): kayıtlı sporun girdisi ""nin yerine geçer
    for sport, keys in required.items():
        assert keys == tuple(d.key for d in slices_for(sport or None, required_only=True))


# --- eski biçimler ------------------------------------------------------------------------------------

def test_refresh_reaches_every_old_form_and_the_league_filter_reads_the_tournament(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Düz, `_no_tournament/` ve kimliksiz lig dizinleri: RD-3'e kadar 2.x'in `refresh_due_ids`'inin ağaç gezintisi ilkine hiç,
    öteki ikisine lig süzgeciyle ulaşmıyordu. Pencere çok geniş: gözlemi olan her kayıt geçicidir.
    """
    fx = sf.build_fixture("legacy", tmp_path / "data")
    monkeypatch.setenv("SOFASCORE_REFRESH__INCLUDE_LEGACY", "true")
    monkeypatch.setenv("SOFASCORE_REFRESH__WINDOW_HOURS", "100000")
    details = details_of(fx.data_dir)
    store = open_store(fx.data_dir)
    due = details.refresh_due()
    paths = {str(row.id): row.path for row in (store.events.get(int(mid)) for mid in due) if row is not None}
    assert set(paths) == set(due)
    flat = {mid for mid, path in paths.items() if path.count("/") == 1}
    assert flat == {"16867839", "17018554"}  # 17018554: yalnızca birleşik dosya
    assert due == sorted(due, key=lambda mid: paths[mid].split("/"))  # eski gezintinin sırası
    for league_id in fx.leagues:
        expected = [mid for mid in due if store.events.get(int(mid)).tournament_id == league_id]  # type: ignore[union-attr]
        assert details.refresh_due(league_id) == expected == details.refresh_due(str(league_id))
    assert flat <= set(details.refresh_due(17))
    assert any(path.split("/")[1] == "_no_tournament" for path in paths.values())
    # kimliksiz lig dizinindeki kayıt (`LaLiga/`), lig süzgeciyle de
    no_id = {mid for mid, path in paths.items() if path.split("/")[1] == "LaLiga"}
    assert no_id and no_id <= set(details.refresh_due(8))


# --- listeler -----------------------------------------------------------------------------------------

def test_candidates_are_ordered_by_season_then_start_time(tmp_path: Path) -> None:
    fx = sf.build_fixture("canonical", tmp_path / "data")
    store = open_store(fx.data_dir)
    collected = details_of(fx.data_dir).candidates(17)
    assert collected is not None
    seasons = [store.events.get(int(mid)).season_id for mid in collected]  # type: ignore[union-attr]
    assert seasons == sorted(seasons, reverse=True)
    for season_id in set(seasons):
        starts = [store.events.get(int(mid)).start_ts for mid in collected  # type: ignore[union-attr]
                  if store.events.get(int(mid)).season_id == season_id]  # type: ignore[union-attr]
        assert starts == sorted(starts)
    assert set(collected) == {str(i) for (league, _), listed in fx.listed.items() if league == 17 for i in listed}


def test_the_only_finished_setting_applies_when_reading(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """LaLiga 26/27 ayar kapalıyken yazıldı: bitmemiş maçları ayar açıkken plana girmez, kapalıyken girer."""
    fx = sf.build_fixture("canonical", tmp_path / "data")
    details = details_of(fx.data_dir)
    finished_or_stored = details.candidates(8)
    monkeypatch.setenv("SOFASCORE_FETCH__ONLY_FINISHED", "false")
    everything = details.candidates(8)
    assert everything is not None and finished_or_stored is not None
    assert set(everything) == {str(i) for i in fx.listed[(8, 97532)]}
    assert set(finished_or_stored) < set(everything)
    store = open_store(fx.data_dir)
    for mid in set(everything) - set(finished_or_stored):
        row = store.events.get(int(mid))
        assert row is not None and row.status_class not in ("completed", "decided_without_play")
        assert not row.has_event_payload


def _summary_only_league(data_dir: Path, rows: str) -> None:
    """Tur dosyası olmayan, yalnızca özet CSV'si olan bir sezon (durum sütunu olmayabilir)."""
    summary = data_dir / "matches" / "17_Premier_League" / "61627_Premier_League_24_25_summary.csv"
    summary.parent.mkdir(parents=True)
    summary.write_text(rows, encoding="utf-8")


def test_summary_rows_without_a_status_are_kept(tmp_path: Path) -> None:
    """Durumu bilinmeyen özet satırı plana girer: özetin satırları RD-3'e kadar süzülmeden okunuyordu."""
    data_dir = tmp_path / "data"
    _summary_only_league(data_dir, "match_id\n501\n502\n")
    details = details_of(data_dir)
    assert details.candidates(17) == ["501", "502"]
    assert details.candidates() == ["501", "502"]
    assert details.candidates(8) is None  # listede maçı olmayan lig
    assert details.candidates(17, only_season_ids=[1]) == []  # lig var, sezon yok
    assert details.pending(["501", "502"]) == ["501", "502"]


# --- katalog güncel değilse ---------------------------------------------------------------------------

def test_no_plan_from_a_catalog_that_is_not_current(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = sf.build_fixture("canonical", tmp_path / "data")
    details = details_of(fx.data_dir)
    store = open_store(fx.data_dir)
    monkeypatch.setattr(type(store), "catalog_current", property(lambda self: False))
    for plan in (lambda: details.needs([str(fx.detail_ids[0])]), details.refresh_due,
                 details.candidates, lambda: details.pending(["1"])):
        with pytest.raises(CatalogNotCurrent) as caught:
            plan()
        assert isinstance(caught.value, StorageError)


def test_refresh_policy_reads_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOFASCORE_REFRESH__WINDOW_HOURS", "1.5")
    monkeypatch.setenv("SOFASCORE_REFRESH__MIN_INTERVAL_HOURS", "0")
    monkeypatch.setenv("SOFASCORE_REFRESH__INCLUDE_LEGACY", "true")
    assert RefreshPolicy.current(now=5.0) == RefreshPolicy(now=5.0, window_s=5400.0, min_interval_s=0.0,
                                                          include_unobserved=True)
    assert RefreshPolicy.current().now == float(NOW)


def test_needs_ask_the_catalog_in_chunks(tmp_path: Path) -> None:
    """Çok sayıda kimlik parça parça sorulur; bilinmeyen kimlikler `full`dur."""
    fx = sf.build_fixture("canonical", tmp_path / "data")
    store = open_store(fx.data_dir)
    ids: List[Optional[Any]] = list(range(1, 1300)) + list(fx.detail_ids) + [True, "x", "12"]
    needs = planning.event_needs(store, ids, RefreshPolicy.current())
    assert set(needs) == set(range(1, 1300)) | set(fx.detail_ids)
    assert all(needs[i] == NEED_FULL for i in range(1, 1300) if i != 12)
    stored: Dict[int, str] = {i: needs[i] for i in fx.detail_ids}
    assert stored == planning.event_needs(store, fx.detail_ids, RefreshPolicy.current())
