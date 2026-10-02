"""
Sorgu servisi ve depodan okuyan maç okuyucuları (plan maddesi RD-1).

Üç şey denetlenir:

  1. `QueryService.match_detail_legacy`: `GET /api/matches/{id}` yanıtının sözlüğü depodan okunur. Bugünkü
     kodun yazdığı dizinlerde sonuç, dizindeki dosyaların kendisidir (anahtar sırasıyla); eski biçimlerde
     tasarımın üç düzeltmesi görünür (docs/design/01-storage.md 5.1 ve 5.2).
  2. `GET /api/matches/{id}` o servisi çağırır: 200, 404 ve depo okunamadığında 500.
  3. `MatchDataFetcher._find_match_path`, `_build_match_index` ve `_load_match_data_from_dir` depodan okur;
     `begin_job_cache` yalnızca ihtiyaç önbelleğini tutar.

Altın dosyalar (`tests/golden/readers/*.api_match_detail.json`, `*.fetcher.json`) aynı davranışı yanıtın
tamamıyla sabitler; buradaki testler kuralları tek tek ve nedenleriyle söyler.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

import store_fixtures as sf
from src.match_data_fetcher import DETAIL_SLICE_KEYS
from src.services.query import QueryService, legacy_detail_keys
from src.status import OBSERVATION_KEY
from src.store import PayloadCorrupt, open_store

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
