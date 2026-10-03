"""
Sorgu servisi ve depodan okuyan maç okuyucuları (plan maddeleri RD-1 ve RD-2).

Dört şey denetlenir:

  1. `QueryService.match_detail_legacy`: `GET /api/matches/{id}` yanıtının sözlüğü depodan okunur. Bugünkü
     kodun yazdığı dizinlerde sonuç, dizindeki dosyaların kendisidir (anahtar sırasıyla); eski biçimlerde
     tasarımın üç düzeltmesi görünür (docs/design/01-storage.md 5.1 ve 5.2).
  2. `GET /api/matches/{id}` o servisi çağırır: 200, 404 ve depo okunamadığında 500.
  3. `MatchDataFetcher._find_match_path`, `_build_match_index` ve `_load_match_data_from_dir` depodan okur;
     `begin_job_cache` yalnızca ihtiyaç önbelleğini tutar.
  4. `QueryService.matches_legacy` ve `season_matches_legacy` (`GET /api/matches`, `GET /api/seasons/{id}/matches`):
     bugünkü kodun yazdığı dizinde satırlar özet CSV'sinin satırlarıdır; "yalnızca bitmiş maçlar" ayarı okurken
     uygulanır; eski biçimlerde katalog dizin gezicilerinin yanlışlarını düzeltir; dışa aktarma CSV'sine geri
     düşülmez (karar S14).

Altın dosyalar (`tests/golden/readers/*.api_match_detail.json`, `*.fetcher.json`, `*.api_matches.json`,
`*.api_season_matches.json`) aynı davranışı yanıtın
tamamıyla sabitler; buradaki testler kuralları tek tek ve nedenleriyle söyler.
"""
from __future__ import annotations

import contextlib
import csv
import datetime
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import store_fixtures as sf
from src import refresh
from src.match_data_fetcher import (
    DETAIL_SLICE_KEYS,
    NO_TOURNAMENT_DIR,
    MatchDataFetcher,
    _stored_observation,
)
from src.services import query
from src.services.query import LEGACY_LIST_COLUMNS, QueryService, legacy_detail_keys
from src.status import OBSERVATION_KEY
from src.store import PayloadCorrupt, StoreError, open_store
from src.web.app import app

ARS = sf.event_id(sf.PL_ARS)  # legacy: iki yerde duran maç (lig/sezon dizini ve bayat düz kopya)
LIV = sf.event_id(sf.PL_LIV)  # legacy: basic.json'ın yanında birleşik dosya ve gözlem
LEE = sf.event_id(sf.PL_LEE)  # legacy: düz dizin
BRE = sf.event_id(sf.PL_BRE)  # legacy: yalnızca birleşik dosya
AVL = sf.event_id(sf.PL_AVL)  # legacy: yarıda kesilmiş statistics.json
NEW = sf.event_id(sf.PL_NEW)  # legacy: basic.json'sız dizin
NO_DETAIL = sf.event_id(sf.PL_NO_DETAIL)  # canonical: yalnızca listeden bilinen maç
WIM_B = sf.event_id(sf.WIM_B)  # canonical: tenis, point_by_point.json dosyası var
UNKNOWN = 1


# --- yardımcılar --------------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _default_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("REFRESH_WINDOW_HOURS", "REFRESH_MIN_INTERVAL_HOURS", "REFRESH_LEGACY", "FETCH_ONLY_FINISHED"):
        monkeypatch.delenv(key, raising=False)


def _fixture(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sf.LegacyFixture:
    """Taze kurulmuş veri dizini; web katmanı ve Store sınırı kaydedicisi (DATA_DIR) ona çevrilir."""
    fixture = sf.build_fixture(name, tmp_path / "data")
    monkeypatch.setenv("DATA_DIR", str(fixture.data_dir))
    return fixture


@pytest.fixture
def canonical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sf.LegacyFixture:
    return _fixture("canonical", tmp_path, monkeypatch)


@pytest.fixture
def old_forms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sf.LegacyFixture:
    return _fixture("legacy", tmp_path, monkeypatch)


def service(fixture: sf.LegacyFixture) -> QueryService:
    return QueryService(open_store(fixture.data_dir))


def folder(fixture: sf.LegacyFixture, record: sf.DetailRecord) -> Path:
    return fixture.data_dir.joinpath(*record.path.split("/"))


def record_of(fixture: sf.LegacyFixture, event_id: int, form: Optional[str] = None) -> sf.DetailRecord:
    found = [d for d in fixture.details if d.event_id == event_id and (form is None or d.form == form)]
    assert len(found) == 1, found
    return found[0]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def files_detail(directory: Path) -> Dict[str, Any]:
    """Dizini dosya dosya okuyan eski kural: `basic` ve dosyası olan `required` dilimler, tablo sırasıyla."""
    return {key: read_json(directory / f"{key}.json")
            for key in ("basic", *sf.REQUIRED_SLICES) if (directory / f"{key}.json").is_file()}


def reopen(fixture: sf.LegacyFixture) -> None:
    """Dosyalar deponun arkasından değişti (uygulama kapalıyken): bir sonraki açılış kataloğu uzlaştırır."""
    open_store(fixture.data_dir).close()


# --- QueryService.match_detail_legacy -------------------------------------------------------------------

def test_detail_keys_are_the_required_slices_in_table_order() -> None:
    assert legacy_detail_keys() == sf.REQUIRED_SLICES == DETAIL_SLICE_KEYS
    assert "point_by_point" not in legacy_detail_keys()  # isteğe bağlı dilim eski yanıtta yoktur


@pytest.mark.parametrize("name", ["canonical", "processed_only"])
def test_detail_of_a_directory_written_by_current_code_is_its_files(
        name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bugünkü yazıcıların ürettiği dizinlerde servis, dizindeki dosyaların kendisini verir: değer ve anahtar sırası."""
    fixture = _fixture(name, tmp_path, monkeypatch)
    queries = service(fixture)
    assert fixture.details and not any(d.combined or not d.has_basic for d in fixture.details)
    for record in fixture.details:
        expected = files_detail(folder(fixture, record))
        detail = queries.match_detail_legacy(record.event_id)
        assert detail == expected
        assert list(detail) == list(expected) and next(iter(detail)) == "basic"


def test_detail_leaves_optional_slices_out(canonical: sf.LegacyFixture) -> None:
    record = record_of(canonical, WIM_B)
    assert "point_by_point.json" in record.files
    detail = service(canonical).match_detail_legacy(WIM_B)
    assert detail is not None and "point_by_point" not in detail and OBSERVATION_KEY not in detail


def test_detail_is_none_for_listed_only_unknown_and_impossible_ids(canonical: sf.LegacyFixture) -> None:
    queries = service(canonical)
    assert open_store(canonical.data_dir).events.get(NO_DETAIL) is not None  # katalog maçı biliyor, yükü yok
    assert queries.match_detail_legacy(NO_DETAIL) is None
    assert queries.match_detail_legacy(UNKNOWN) is None
    assert queries.match_detail_legacy(-1) is None
    assert queries.match_detail_legacy(2 ** 63) is None  # kataloğun saklayamayacağı kimlik: hata değil, "yok"
    assert queries.match_detail_legacy(-(2 ** 63) - 1) is None


@pytest.mark.parametrize("bad", ["17", 17.0, True, None])
def test_detail_rejects_an_id_that_is_not_an_integer(canonical: sf.LegacyFixture, bad: Any) -> None:
    with pytest.raises(ValueError):
        service(canonical).match_detail_legacy(bad)


def test_detail_of_an_empty_data_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _fixture("empty", tmp_path, monkeypatch)
    assert service(fixture).match_detail_legacy(ARS) is None


def test_slice_file_holding_json_null_is_kept_as_none(canonical: sf.LegacyFixture) -> None:
    """Eski okuyucu gibi: dosyası olan dilim, içeriği `null` olsa da yanıtta anahtarıyla yer alır."""
    record = record_of(canonical, ARS)
    (folder(canonical, record) / "lineups.json").write_text("null", encoding="utf-8")
    detail = service(canonical).match_detail_legacy(ARS)
    assert detail is not None and detail["lineups"] is None
    assert list(detail) == ["basic", *sf.REQUIRED_SLICES]


# --- eski biçimler: tasarımın düzeltmeleri (01-storage.md 5.1, 5.2) ---------------------------------------

def test_directory_with_only_the_combined_file_is_an_event(old_forms: sf.LegacyFixture) -> None:
    """Düzeltme 1: yalnızca `<id>/<id>.json` olan dizin de maçtır; eski okuyucu 404 veriyordu."""
    record = record_of(old_forms, BRE)
    assert record.combined and not record.has_basic
    combined = read_json(folder(old_forms, record) / f"{BRE}.json")
    detail = service(old_forms).match_detail_legacy(BRE)
    assert detail == {key: combined[key] for key in ("basic", *sf.REQUIRED_SLICES)}
    assert list(detail) == ["basic", *sf.REQUIRED_SLICES]


def test_combined_file_next_to_basic_json_gives_the_same_answer(old_forms: sf.LegacyFixture) -> None:
    """Olay yükü basic.json'dan, dilimler birleşik dosyadan: eski okuyucunun verdiği sözlükle aynı."""
    record = record_of(old_forms, LIV)
    assert record.combined and record.has_basic
    directory = folder(old_forms, record)
    combined = read_json(directory / f"{LIV}.json")
    detail = service(old_forms).match_detail_legacy(LIV)
    assert detail == combined and list(detail) == list(combined)
    assert detail["basic"] == read_json(directory / "basic.json")


def test_separate_file_wins_over_the_combined_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Dilim önce kendi dosyasından okunur; birleşik dosyadaki tanınmayan anahtarlar yanıtta yer almaz. Eski okuyucu
    birleşik dosya varsa yalnızca onu, olduğu gibi döndürüyordu.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    base = tmp_path / "match_details" / "7"
    base.mkdir(parents=True)
    event = {"id": 7, "startTimestamp": sf.FIXTURE_NOW}
    (base / "basic.json").write_text(json.dumps({**event, "copy": "file"}), encoding="utf-8")
    (base / "h2h.json").write_text(json.dumps({"teamDuel": {"homeWins": 3}}), encoding="utf-8")
    (base / "7.json").write_text(json.dumps({
        "basic": {**event, "copy": "combined"}, "h2h": {"teamDuel": {"homeWins": 1}},
        "incidents": {"incidents": [{"time": 5}]}, "odds": {"x": 1},
    }), encoding="utf-8")
    assert QueryService(open_store(tmp_path)).match_detail_legacy(7) == {
        "basic": {**event, "copy": "file"}, "h2h": {"teamDuel": {"homeWins": 3}},
        "incidents": {"incidents": [{"time": 5}]},
    }


def test_truncated_slice_file_drops_only_that_slice(old_forms: sf.LegacyFixture) -> None:
    """Düzeltme 3: yarıda kesilmiş dilim dosyası yalnızca o dilimi düşürür; eski okuyucu 500 veriyordu."""
    directory = folder(old_forms, record_of(old_forms, AVL))
    with pytest.raises(ValueError):
        read_json(directory / "statistics.json")
    detail = service(old_forms).match_detail_legacy(AVL)
    assert detail is not None and list(detail) == ["basic", *[k for k in sf.REQUIRED_SLICES if k != "statistics"]]
    assert detail["basic"] == read_json(directory / "basic.json")


def test_directory_without_an_event_payload_is_not_an_event(old_forms: sf.LegacyFixture) -> None:
    assert not record_of(old_forms, NEW).has_basic
    assert service(old_forms).match_detail_legacy(NEW) is None


def test_event_stored_in_two_places_reads_the_copy_with_the_newest_event_payload(old_forms: sf.LegacyFixture) -> None:
    current, stale = record_of(old_forms, ARS, "L1"), record_of(old_forms, ARS, "L3")
    newest = read_json(folder(old_forms, current) / "basic.json")
    assert newest != read_json(folder(old_forms, stale) / "basic.json")
    detail = service(old_forms).match_detail_legacy(ARS)
    assert detail is not None and detail["basic"] == newest
    # bayat kopya yenilenirse (olay yükü artık en yeni) geçerli olan o olur
    replacement = folder(old_forms, stale) / "basic.json.new"
    replacement.write_text(json.dumps({**newest, "copy": "flat"}), encoding="utf-8")
    os.replace(replacement, folder(old_forms, stale) / "basic.json")
    os.utime(folder(old_forms, stale) / "basic.json", (sf.FIXTURE_NOW, sf.FIXTURE_NOW))  # öteki kopyadan yeni
    reopen(old_forms)
    assert service(old_forms).match_detail_legacy(ARS) == {"basic": {**newest, "copy": "flat"}}


# --- katalog dosyaların gerisindeyken ------------------------------------------------------------------

def test_slice_file_removed_behind_the_catalog_is_left_out(
        canonical: sf.LegacyFixture, caplog: pytest.LogCaptureFixture) -> None:
    """Katalog "yük var" derken dosya yok: yalnızca o dilim düşer, bir uyarı yazılır; kalanlar okunur."""
    directory = folder(canonical, record_of(canonical, ARS))
    queries = service(canonical)  # depo açık: katalog dosyalarla eşit
    expected = files_detail(directory)
    (directory / "h2h.json").unlink()
    with caplog.at_level(logging.WARNING, logger="QueryService"):
        detail = queries.match_detail_legacy(ARS)
    del expected["h2h"]
    assert detail == expected and list(detail) == list(expected)
    assert [r.getMessage() for r in caplog.records if r.name == "QueryService"][0].startswith(
        f"Event {ARS}: the stored h2h payload is unreadable and is left out")
    reopen(canonical)  # sonraki açılış uzlaştırır: artık uyarı da yok
    caplog.clear()
    assert service(canonical).match_detail_legacy(ARS) == expected and not caplog.records


def test_slice_file_truncated_behind_the_catalog_is_left_out(canonical: sf.LegacyFixture) -> None:
    directory = folder(canonical, record_of(canonical, ARS))
    queries = service(canonical)
    (directory / "statistics.json").write_text('{"statistics": [', encoding="utf-8")
    with pytest.raises(PayloadCorrupt):
        open_store(canonical.data_dir).events.payloads(ARS)
    detail = queries.match_detail_legacy(ARS)
    assert detail is not None and list(detail) == ["basic", *[k for k in sf.REQUIRED_SLICES if k != "statistics"]]


def test_event_payload_removed_behind_the_catalog_is_no_record(canonical: sf.LegacyFixture) -> None:
    directory = folder(canonical, record_of(canonical, ARS))
    queries = service(canonical)
    (directory / "basic.json").unlink()
    assert queries.match_detail_legacy(ARS) is None


# --- GET /api/matches/{id} ----------------------------------------------------------------------------

client = TestClient(app)


def test_route_answers_from_the_service(old_forms: sf.LegacyFixture) -> None:
    queries = service(old_forms)
    for event_id in (ARS, LIV, LEE, BRE, AVL):
        response = client.get(f"/api/matches/{event_id}")
        assert response.status_code == 200
        assert response.json() == queries.match_detail_legacy(event_id)
        assert list(response.json()) == list(queries.match_detail_legacy(event_id))
    for event_id in (NEW, UNKNOWN, 2 ** 70):
        response = client.get(f"/api/matches/{event_id}")
        assert (response.status_code, response.json()) == (404, {"detail": "Match details not found."})
    assert client.get("/api/matches/abc").status_code == 422


def test_route_reads_through_the_store_only(canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """Uç nokta indirici kurmaz, dizin ağacını gezmez: yanıt servisten gelir."""
    import src.match_data_fetcher as fetcher_module
    import src.web.api.legacy as routes

    monkeypatch.setattr(fetcher_module, "MatchDataFetcher", MagicMock(side_effect=AssertionError("not used")))
    seen = []
    real = QueryService.match_detail_legacy

    def spy(self: QueryService, event_id: int) -> Optional[Dict[str, Any]]:
        seen.append(event_id)
        return real(self, event_id)

    monkeypatch.setattr(routes.QueryService, "match_detail_legacy", spy)
    assert client.get(f"/api/matches/{ARS}").status_code == 200
    assert seen == [ARS]


def test_route_answers_500_when_the_store_cannot_be_read(
        canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(self: QueryService, event_id: int) -> Dict[str, Any]:
        raise StoreError("disk unreadable")

    monkeypatch.setattr(query.QueryService, "match_detail_legacy", broken)
    response = client.get(f"/api/matches/{ARS}")
    assert (response.status_code, response.json()) == (500, {"detail": "Error parsing match data."})


# --- MatchDataFetcher: yer arama ----------------------------------------------------------------------

@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Yenileme kararı `time.time()`'a bakar; fabrikadaki gözlem zamanları FIXTURE_NOW'a göre seçilmiştir."""
    monkeypatch.setattr(time, "time", lambda: float(sf.FIXTURE_NOW))


def fetcher_for(data_dir: Path) -> MatchDataFetcher:
    return MatchDataFetcher(config_manager=MagicMock(), data_dir=str(data_dir))


def test_find_match_path_gives_the_directory_of_every_legacy_form(old_forms: sf.LegacyFixture) -> None:
    fetcher = fetcher_for(old_forms.data_dir)
    details = os.path.join(str(old_forms.data_dir), "match_details")
    for event_id, form in ((ARS, "L1"), (LIV, "L1"), (LEE, "L3"), (BRE, "L3"), (sf.event_id(sf.LIGA_A), "L2"),
                           (sf.event_id(sf.FRIENDLY_A), "L5")):
        record = record_of(old_forms, event_id, form)
        parts = record.path.split("/")
        expected_dirs = (None, None) if form == "L3" else (parts[1], parts[2])
        assert fetcher._find_match_path(str(event_id)) == (*expected_dirs, os.path.join(details, *parts[1:]))
    assert fetcher._find_match_path(str(sf.event_id(sf.FRIENDLY_A)))[0] == NO_TOURNAMENT_DIR
    assert fetcher._find_match_path(str(NEW)) is None  # dizin var, olay yükü yok
    assert fetcher._find_match_path(str(UNKNOWN)) is None


def test_find_match_path_accepts_an_integer_and_only_canonical_ids(canonical: sf.LegacyFixture) -> None:
    fetcher = fetcher_for(canonical.data_dir)
    assert fetcher._find_match_path(ARS) == fetcher._find_match_path(str(ARS)) is not None  # type: ignore[arg-type]
    for text in ("", "abc", f"0{ARS}", f" {ARS}", f"{ARS}.0", f"-{ARS}", "١٢٣", str(2 ** 63), "processed"):
        assert fetcher._find_match_path(text) is None
    assert fetcher._find_match_path(str(NO_DETAIL)) is None  # yalnızca listeden bilinen maçın dizini yok


def test_find_match_path_keeps_a_relative_data_directory_relative(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dönen yol, indiricinin veri dizini nasıl verildiyse onunla kurulur (yazıcılar aynı yolu kullanır)."""
    sf.build_fixture("canonical", tmp_path / "data")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATA_DIR", "data")
    found = MatchDataFetcher(config_manager=MagicMock(), data_dir="data")._find_match_path(str(ARS))
    assert found is not None and not os.path.isabs(found[2])
    assert found[2] == os.path.join("data", "match_details", found[0], found[1], str(ARS))


def test_build_match_index_lists_every_stored_event_once(old_forms: sf.LegacyFixture) -> None:
    fetcher = fetcher_for(old_forms.data_dir)
    index = fetcher._build_match_index()
    with_payload = {d.event_id for d in old_forms.details if d.has_basic or d.combined}
    assert {int(mid) for mid in index} == with_payload and BRE in with_payload and NEW not in with_payload
    assert all(index[mid] == fetcher._find_match_path(mid) for mid in index)
    assert index[str(ARS)][2].endswith(record_of(old_forms, ARS, "L1").path.replace("/", os.sep))


# --- MatchDataFetcher: iş önbelleği --------------------------------------------------------------------

def test_job_cache_keeps_only_the_needs(old_forms: sf.LegacyFixture, frozen_clock: None) -> None:
    """Konum önbelleği kalktı: iş sırasında da yer katalogdan sorulur ve önbelleksiz aramayla aynıdır."""
    plain, cached = fetcher_for(old_forms.data_dir), fetcher_for(old_forms.data_dir)
    cached.begin_job_cache()
    assert not hasattr(cached, "_match_index") and cached._need_cache == {}
    ids = [str(event_id) for event_id in old_forms.event_ids + [UNKNOWN]]
    # iki yerde duran maç dahil: eskiden önbellek ilk listelenen (bayat) kopyayı, arama lig/sezon kopyasını seçiyordu
    assert [cached._find_match_path(mid) for mid in ids] == [plain._find_match_path(mid) for mid in ids]
    needs = {mid: cached._needs_detail_fetch(mid) for mid in ids}
    assert needs == {mid: plain._needs_detail_fetch(mid) for mid in ids} and cached._need_cache == needs
    cached.end_job_cache()
    assert cached._need_cache == {} and not hasattr(cached, "_match_index")


def test_match_saved_during_a_job_is_found_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Yazma kataloğu aynı işlemde günceller: iş içinde kaydedilen maçın yeri (v3) ve ihtiyacı hemen doğru okunur."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    fetcher = fetcher_for(tmp_path)
    fetcher.begin_job_cache()
    event = sf.basic_payload(sf.PL_ARS)
    assert fetcher._find_match_path(str(ARS)) is None and fetcher._needs_detail_fetch(str(ARS)) == "full"
    data = {"basic": event, **{key: sf.slice_payload(key, event) for key in sf.REQUIRED_SLICES}}
    fetcher._save_match_data(str(ARS), data)
    found = fetcher._find_match_path(str(ARS))
    assert found is not None and os.path.isfile(os.path.join(found[2], "event.json.gz"))
    assert fetcher._needs_detail_fetch(str(ARS)) == "none"
    loaded = fetcher._load_match_data_from_dir(found[2], str(ARS))
    assert loaded.pop(OBSERVATION_KEY)["observed_at_utc"] is not None  # Store her olay yükünü gözlem olarak saklar
    assert loaded == data


# --- MatchDataFetcher: kaydın yüklenmesi ---------------------------------------------------------------

def test_loader_gives_the_files_and_the_observation(canonical: sf.LegacyFixture) -> None:
    """Bugünkü kodun yazdığı dizinlerde: dosyaların kendisi ve gözlem (an, yapışkan bayrak)."""
    fetcher = fetcher_for(canonical.data_dir)
    observed, regressed = 0, 0
    for record in canonical.details:
        directory = folder(canonical, record)
        loaded = fetcher._load_match_data_from_dir(str(directory), str(record.event_id))
        observation = loaded.pop(OBSERVATION_KEY, None)
        assert loaded == files_detail(directory) and list(loaded) == list(files_detail(directory))
        stored = read_json(directory / "observation.json") if (directory / "observation.json").is_file() else {}
        moment = refresh._parse_utc(stored.get("observed_at_utc"))
        assert refresh._parse_utc((observation or {}).get("observed_at_utc")) == moment
        assert bool((observation or {}).get("status_regressed")) is bool(stored.get("status_regressed"))
        if moment is not None:
            assert observation["observed_at_utc"] == stored["observed_at_utc"]  # yazıcının biçimiyle aynı metin
            assert observation["change_ts"] == loaded["basic"]["changes"]["changeTimestamp"]
        observed += moment is not None
        regressed += bool(stored.get("status_regressed"))
    assert observed >= 5 and regressed >= 1


def test_loader_reads_the_observation_next_to_a_combined_file(old_forms: sf.LegacyFixture, frozen_clock: None) -> None:
    """Düzeltme 2: birleşik dosyası olan kaydın gözlemi okunur; eskiden okunmaz, kayıt hiç yenilenmezdi."""
    fetcher = fetcher_for(old_forms.data_dir)
    directory = folder(old_forms, record_of(old_forms, LIV))
    loaded = fetcher._load_match_data_from_dir(str(directory), str(LIV))
    assert loaded[OBSERVATION_KEY]["observed_at_utc"] == read_json(directory / "observation.json")["observed_at_utc"]
    assert fetcher._needs_detail_fetch(str(LIV)) == "refresh"
    assert str(LIV) in fetcher.refresh_due_ids()


def test_loader_reads_the_record_of_the_combined_only_directory(old_forms: sf.LegacyFixture, frozen_clock: None) -> None:
    fetcher = fetcher_for(old_forms.data_dir)
    found = fetcher._find_match_path(str(BRE))
    assert found is not None
    loaded = fetcher._load_match_data_from_dir(found[2], str(BRE))
    assert list(loaded) == ["basic", *sf.REQUIRED_SLICES]  # gözlemi yok: eski kayıt
    assert fetcher._needs_detail_fetch(str(BRE)) == "none"  # eskiden bulunamıyor, "full" sayılıyordu


def test_loader_drops_only_the_truncated_slice(old_forms: sf.LegacyFixture, frozen_clock: None) -> None:
    fetcher = fetcher_for(old_forms.data_dir)
    directory = folder(old_forms, record_of(old_forms, AVL))
    loaded = fetcher._load_match_data_from_dir(str(directory), str(AVL))
    assert [k for k in loaded if k != OBSERVATION_KEY] == ["basic", *[k for k in sf.REQUIRED_SLICES if k != "statistics"]]
    assert fetcher._needs_detail_fetch(str(AVL)) == "refill"


def test_loader_is_empty_for_what_is_not_a_record(old_forms: sf.LegacyFixture) -> None:
    fetcher = fetcher_for(old_forms.data_dir)
    assert fetcher._load_match_data_from_dir(str(folder(old_forms, record_of(old_forms, NEW))), str(NEW)) == {}
    assert fetcher._load_match_data_from_dir(str(old_forms.data_dir / "match_details" / "1"), "1") == {}
    assert fetcher._load_match_data_from_dir(str(old_forms.data_dir), "abc") == {}


def test_loader_reads_the_valid_copy_whatever_directory_is_given(old_forms: sf.LegacyFixture) -> None:
    """Hangi kopyanın okunacağını katalog söyler: bayat düz kopyanın yolu verilse de geçerli kayıt döner."""
    fetcher = fetcher_for(old_forms.data_dir)
    current, stale = folder(old_forms, record_of(old_forms, ARS, "L1")), folder(old_forms, record_of(old_forms, ARS, "L3"))
    loaded = fetcher._load_match_data_from_dir(str(stale), str(ARS))
    assert loaded["basic"] == read_json(current / "basic.json") != read_json(stale / "basic.json")
    assert fetcher._find_match_path(str(ARS))[2] == str(current)


def test_slice_file_removed_behind_the_catalog_is_a_refill_not_a_full_fetch(
        canonical: sf.LegacyFixture, frozen_clock: None) -> None:
    """Depo açıkken bir dilim dosyası silinirse (elle, başka bir araçla) maç yeniden baştan indirilmez."""
    fetcher = fetcher_for(canonical.data_dir)
    directory = folder(canonical, record_of(canonical, ARS))
    assert fetcher._needs_detail_fetch(str(ARS)) == "none"
    (directory / "h2h.json").unlink()
    loaded = fetcher._load_match_data_from_dir(str(directory), str(ARS))
    assert "basic" in loaded and "h2h" not in loaded and "lineups" in loaded
    assert fetcher._needs_detail_fetch(str(ARS)) == "refill"


def test_loader_swallows_a_store_error(canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch,
                                       caplog: pytest.LogCaptureFixture) -> None:
    """Eski yükleyici gibi: okuma hatası uyarı olarak yazılır, kayıt okunamadı sayılır (boş sözlük)."""
    fetcher = fetcher_for(canonical.data_dir)
    directory = str(folder(canonical, record_of(canonical, ARS)))

    def broken(self: QueryService, event_id: int) -> Dict[str, Any]:
        raise StoreError("disk unreadable")

    monkeypatch.setattr(query.QueryService, "match_detail_legacy", broken)
    with caplog.at_level(logging.WARNING, logger="MatchDataFetcher"):
        assert fetcher._load_match_data_from_dir(directory, str(ARS)) == {}
    assert any("disk unreadable" in record.getMessage() for record in caplog.records)


# --- gözlem: katalog satırından ------------------------------------------------------------------------

class _Row:
    def __init__(self, observed_at: Optional[int], change_ts: Optional[int], status_regressed: bool) -> None:
        self.observed_at, self.change_ts, self.status_regressed = observed_at, change_ts, status_regressed


@pytest.mark.parametrize("row, expected", [
    (_Row(None, None, False), None),
    (_Row(None, 1790000000, False), None),  # gözlem anı yok: eski kayıt gibi (yenileme kuralı aynı)
    (_Row(1789477800, 1789477727, False), {"observed_at_utc": "2026-09-15T13:10:00+00:00", "change_ts": 1789477727}),
    (_Row(1789477800, None, True),
     {"observed_at_utc": "2026-09-15T13:10:00+00:00", "change_ts": None, "status_regressed": True}),
    (_Row(None, 5, True), {"observed_at_utc": None, "change_ts": 5, "status_regressed": True}),
    (_Row(10 ** 18, 5, False), None),  # tarih olarak yazılamayan an: gözlem yok sayılır
])
def test_observation_from_the_catalog_row(row: _Row, expected: Optional[Dict[str, Any]]) -> None:
    assert _stored_observation(row) == expected  # type: ignore[arg-type]


@contextlib.contextmanager
def _serving(event: Dict[str, Any]) -> Iterator[Any]:
    """Sahte SofaScore (tests/fakes/sofascore.py): yalnızca bu maçın /event'i; yenileme boru hattından geçer (P13)."""
    import copy

    from fakes.sofascore import FakeSofaScore

    fake = FakeSofaScore()
    fake.add_event(copy.deepcopy(event))
    with fake:
        yield fake


def test_refresh_keeps_the_sticky_flag_read_from_the_store(canonical: sf.LegacyFixture) -> None:
    """`status_regressed` gözlemle birlikte depodan okunur ve yenilemede korunur (refresh_match)."""
    regressed = next(d for d in canonical.details
                     if (folder(canonical, d) / "observation.json").is_file()
                     and read_json(folder(canonical, d) / "observation.json").get("status_regressed"))
    fetcher = fetcher_for(canonical.data_dir)
    directory = folder(canonical, regressed)
    basic = read_json(directory / "basic.json")
    with _serving(basic):
        data = fetcher.refresh_match(str(regressed.event_id))
    assert data is not None and data[OBSERVATION_KEY]["status_regressed"] is True
    assert read_json(directory / "observation.json")["status_regressed"] is True


def _iter_slice_files(directory: Path) -> Iterator[str]:
    return (path.name for path in sorted(directory.iterdir()) if path.suffix == ".json")


def test_reading_writes_nothing_into_the_event_directories(old_forms: sf.LegacyFixture, frozen_clock: None) -> None:
    before = {d.path: (list(_iter_slice_files(folder(old_forms, d))), folder(old_forms, d).stat().st_mtime_ns)
              for d in old_forms.details}
    fetcher = fetcher_for(old_forms.data_dir)
    for event_id in old_forms.event_ids:
        fetcher._needs_detail_fetch(str(event_id))
        service(old_forms).match_detail_legacy(event_id)
    fetcher._build_match_index()
    assert before == {d.path: (list(_iter_slice_files(folder(old_forms, d))), folder(old_forms, d).stat().st_mtime_ns)
                      for d in old_forms.details}


# --- maç listeleri: QueryService.matches_legacy ve season_matches_legacy (RD-2) ------------------------

NBA_VOID = sf.event_id(sf.NBA_VOID)  # canonical: listede "Ended", saklanan olay yükü sonradan "Abandoned"
LIGA_UNFINISHED = {sf.event_id(ev) for ev in (sf.LIGA_POSTPONED, sf.LIGA_INTERRUPTED, sf.LIGA_CANCELED)}
LIGA_NEXT = sf.event_id(sf.LIGA_NEXT)  # canonical: bitmemiş, detayı indirilmiş
OLD_PL = (sf.event_id(sf.PL_OLD_A), sf.event_id(sf.PL_OLD_B))  # legacy: yalnızca `_matches.csv`'de
FLAT_OR_NO_TOURNAMENT = {sf.event_id(ev) for ev in (sf.FRIENDLY_A, sf.EXHIBITION_A, sf.NO_SPORT_A)}
LIGA_OLD = sf.event_id(sf.LIGA_A)  # legacy: kimliksiz lig dizini (L2)
ALL = 200


def everything(queries: QueryService, **kw: Any) -> List[Dict[str, Any]]:
    page = queries.matches_legacy(limit=ALL, **kw)
    assert page.total == len(page.items) < ALL
    return list(page.items)


def _csv_value(column: str, text: str) -> Any:
    """Özet CSV'sindeki metnin yanıttaki değeri (pandas'ın okuduğu gibi: tamsayı sütunlar sayı)."""
    if column in ("match_id", "home_score", "away_score") or (column == "round" and text.isdigit()):
        return int(text)
    return text


def test_list_rows_are_the_summary_csv_rows(canonical: sf.LegacyFixture) -> None:
    """Bugünkü kodun yazdığı dizinde her özet satırı, sütun sütun ve sırasıyla, listedeki satırdır."""
    rows = {item["match_id"]: item for item in everything(service(canonical), only_finished=False)}
    compared = 0
    for rel in canonical.summary_files:
        directory = rel.split("/")[1]
        with open(canonical.data_dir / rel, encoding="utf-8", newline="") as f:
            for line in csv.DictReader(f):
                event_id = int(line["match_id"])
                if event_id == NBA_VOID:
                    continue  # satırı saklanan olay yükündendir (aşağıdaki test)
                expected = {c: _csv_value(c, line[c]) for c in LEGACY_LIST_COLUMNS}
                assert list(rows[event_id])[:len(LEGACY_LIST_COLUMNS)] == list(LEGACY_LIST_COLUMNS)
                assert {c: rows[event_id][c] for c in LEGACY_LIST_COLUMNS} == expected, event_id
                assert rows[event_id]["league_folder"] == directory
                compared += 1
    assert compared >= 30


def test_row_of_an_event_with_details_comes_from_its_stored_payload(canonical: sf.LegacyFixture) -> None:
    row = next(i for i in everything(service(canonical)) if i["match_id"] == NBA_VOID)
    assert (row["status"], row["home_score"], row["round"]) == ("Abandoned", 59, "last_1")
    assert row["has_details"] is True


def test_only_finished_lists_finished_matches_and_those_with_details(canonical: sf.LegacyFixture) -> None:
    queries = service(canonical)
    every = {i["match_id"]: i for i in everything(queries, only_finished=False)}
    finished = {i["match_id"] for i in everything(queries)}
    store = open_store(canonical.data_dir)
    expected = {mid for mid in every
                if every[mid]["has_details"] or store.events.get(mid).status_class in query.FINISHED_CLASSES}
    assert finished == expected
    assert LIGA_UNFINISHED <= set(every) and not LIGA_UNFINISHED & finished
    assert LIGA_NEXT in finished and every[LIGA_NEXT]["status"] == "Not started"
    # Panodaki sayımla aynı kural (plan maddesi RD-4): altın dosyalarda `totals.matches` de 29'dur
    assert len(finished) == 29


def test_details_filters_split_the_list(canonical: sf.LegacyFixture) -> None:
    queries = service(canonical)
    for only_finished in (True, False):
        full = everything(queries, only_finished=only_finished)
        present = everything(queries, only_finished=only_finished, details=True)
        missing = everything(queries, only_finished=only_finished, details=False)
        assert present == [i for i in full if i["has_details"]]
        assert missing == [i for i in full if not i["has_details"]]


@pytest.mark.parametrize("only_finished", [True, False])
@pytest.mark.parametrize("sort", ["asc", "desc"])
@pytest.mark.parametrize("date", [None, "2026-09", "2026-09-29T13", "-29T", "13:"])
def test_pages_join_into_the_full_list(canonical: sf.LegacyFixture, only_finished: bool, sort: str,
                                       date: Optional[str]) -> None:
    queries = service(canonical)
    full = everything(queries, only_finished=only_finished, sort=sort, date=date)
    starts = [(i["match_date"], i["match_id"]) for i in full]
    assert starts == (sorted(starts) if sort == "asc" else sorted(starts, reverse=True))
    joined: List[Dict[str, Any]] = []
    for offset in range(0, len(full) + 3, 3):
        page = queries.matches_legacy(only_finished=only_finished, sort=sort, date=date, offset=offset, limit=3)
        assert page.total == len(full)
        joined += page.items
    assert joined == full


@pytest.mark.parametrize("date", ["2026-09-15", "2026-09-29T13", "2026-05", "2026", "1999", "17894", "2026-13",
                                  "T18:00", "-15T", "Ended", ""])
def test_date_filter_is_a_substring_of_match_date(old_forms: sf.LegacyFixture, date: str) -> None:
    queries = service(old_forms)
    full = everything(queries)
    assert everything(queries, date=date) == [i for i in full if date in i["match_date"]]


def test_date_range_of_an_iso_prefix() -> None:
    lo, hi = query._date_range("2026-09-15")
    assert lo is not None and hi is not None
    assert lo <= datetime.datetime(2026, 9, 15).timestamp() and datetime.datetime(2026, 9, 16).timestamp() <= hi
    assert query._date_range("2026-12")[1] is not None  # aralık yılın sonunu geçer
    for text in (None, "", "2026-13", "0000", "T13", "26-09", "2026-9"):
        assert query._date_range(text) == (None, None)


def test_league_and_season_filters(canonical: sf.LegacyFixture) -> None:
    queries = service(canonical)
    full = everything(queries)
    store = open_store(canonical.data_dir)
    tournament = {i["match_id"]: store.events.get(i["match_id"]).tournament_id for i in full}
    assert everything(queries, tournament_ids=[17]) == [i for i in full if tournament[i["match_id"]] == 17]
    assert everything(queries, tournament_ids=[17, 132]) == [i for i in full if tournament[i["match_id"]] in (17, 132)]
    in_season = everything(queries, tournament_ids=[17], season_id=sf.PL_2627.id)
    assert {i["season"] for i in in_season} == {sf.PL_2627.name} and in_season
    assert everything(queries, tournament_ids=[sf.LALIGA.id], season_id=sf.PL_2627.id) == []
    assert everything(queries, tournament_ids=[999]) == []


def test_old_forms_are_corrected(old_forms: sf.LegacyFixture) -> None:
    queries = service(old_forms)
    rows = {i["match_id"]: i for i in everything(queries)}
    # Yalnızca `_matches.csv`'si olan sezon listede
    assert set(OLD_PL) <= set(rows)
    # Düz ve turnuvasız dizinlerdeki detay: maç listede ve detayı var
    assert FLAT_OR_NO_TOURNAMENT <= set(rows) and all(rows[m]["has_details"] for m in FLAT_OR_NO_TOURNAMENT)
    assert {rows[m]["league_folder"] for m in FLAT_OR_NO_TOURNAMENT} == {"_no_tournament"}
    # has_details lig süzgecinden bağımsız; kimliksiz lig dizinindeki maçın lig dizini yazıcıların adı
    filtered = {i["match_id"]: i for i in everything(queries, tournament_ids=[sf.LALIGA.id])}
    assert all(filtered[m]["has_details"] == rows[m]["has_details"] for m in filtered)
    assert rows[LIGA_OLD]["has_details"] is True and rows[LIGA_OLD]["league_folder"] == "8_LaLiga"
    assert rows[LEE]["has_details"] is True  # düz dizin (L3)


def test_export_csv_is_not_a_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """processed_only'nin detayları listelenir; dışa aktarma CSV'sindeki öteki maçlar listelenmez (karar S14)."""
    fixture = _fixture("processed_only", tmp_path, monkeypatch)
    listed = {i["match_id"] for i in everything(service(fixture))}
    assert listed == {d.event_id for d in fixture.details}
    empty = tmp_path / "export_only"
    (empty / "match_details" / "processed").mkdir(parents=True)
    (empty / "match_details" / "processed" / "all_matches_1.csv").write_text("match_id,league_folder\n1,17_PL\n")
    monkeypatch.setenv("DATA_DIR", str(empty))
    assert QueryService(open_store(empty)).matches_legacy() == query.LegacyMatchPage((), 0)


def test_list_arguments_are_checked(canonical: sf.LegacyFixture) -> None:
    queries = service(canonical)
    for kw in ({"sort": "bogus"}, {"offset": -1}, {"limit": 0}, {"limit": True}, {"offset": "3"}):
        with pytest.raises(ValueError):
            queries.matches_legacy(**kw)


def test_season_rows_are_unique_and_ordered_by_start(old_forms: sf.LegacyFixture) -> None:
    queries = service(old_forms)
    rows = queries.season_matches_legacy(sf.PL_2627.id, sf.PL.id)
    ids = [r["match_id"] for r in rows]
    assert len(ids) == len(set(ids)) and ids  # iki özet dosyasında geçen maç bir kez
    assert [r["match_date"] for r in rows] == sorted(r["match_date"] for r in rows)
    assert all(list(r) == list(LEGACY_LIST_COLUMNS) for r in rows)
    listed = everything(queries, tournament_ids=[sf.PL.id], season_id=sf.PL_2627.id, sort="asc")
    assert rows == [{c: i[c] for c in LEGACY_LIST_COLUMNS} for i in listed]
    assert queries.season_matches_legacy(sf.PL_2627.id, 999) == []
    old = queries.season_matches_legacy(sf.PL_2526.id, sf.PL.id)  # yalnızca `_matches.csv`
    assert {r["match_id"] for r in old} == set(OLD_PL)


def test_season_rows_follow_the_only_finished_rule(canonical: sf.LegacyFixture) -> None:
    queries = service(canonical)
    every = {r["match_id"] for r in queries.season_matches_legacy(sf.LALIGA_2627.id, sf.LALIGA.id,
                                                                    only_finished=False)}
    finished = {r["match_id"] for r in queries.season_matches_legacy(sf.LALIGA_2627.id, sf.LALIGA.id)}
    assert finished == every - LIGA_UNFINISHED and LIGA_NEXT in finished


def test_list_routes_answer_from_the_service(old_forms: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """Uç noktalar dosya ağacını okumaz: özet CSV'si okuyan bir çağrı testi düşürür."""
    import pandas as pd

    monkeypatch.setattr(pd, "read_csv", MagicMock(side_effect=AssertionError("not used")))
    queries = service(old_forms)
    body = client.get("/api/matches?league_id=17,8&sort=asc&limit=5&offset=2").json()
    page = queries.matches_legacy(tournament_ids=[8, 17], sort="asc", offset=2, limit=5)
    assert body == {"items": list(page.items), "total": page.total, "limit": 5, "offset": 2, "sort": "asc"}
    body = client.get("/api/matches?details=missing&date=2026-05").json()
    assert body["items"] == list(queries.matches_legacy(details=False, date="2026-05").items)
    response = client.get(f"/api/seasons/{sf.PL_2627.id}/matches?league_id={sf.PL.id}")
    assert response.json() == {"matches": queries.season_matches_legacy(sf.PL_2627.id, sf.PL.id)}


def test_list_route_reads_the_only_finished_setting(canonical: sf.LegacyFixture,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    on = {i["match_id"] for i in client.get("/api/matches?limit=200").json()["items"]}
    monkeypatch.setenv("FETCH_ONLY_FINISHED", "false")
    off = {i["match_id"] for i in client.get("/api/matches?limit=200").json()["items"]}
    assert off - on == LIGA_UNFINISHED | {sf.event_id(ev) for ev in (sf.PL_NOT_STARTED, sf.PL_POSTPONED,
                                                                      sf.PL_FUTURE)}
