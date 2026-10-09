"""
Depodan okunan maç kayıtları (plan maddeleri RD-1 ve RD-2): eski düzenin her biçimi katalogdan bulunur ve okunur.

İki şey denetlenir:

  1. Maçın saklanan detayı, 2.x'in detay yanıtı biçiminde (tests/detail_fetch.py `legacy_detail`; 3.1'e kadar
     `QueryService.match_detail_legacy`). Bugünkü kodun yazdığı dizinlerde sonuç, dizindeki dosyaların kendisidir
     (anahtar sırasıyla); eski biçimlerde tasarımın üç düzeltmesi görünür (docs/design/01-storage.md 5.1 ve 5.2).
  2. Kaydın yeri, ihtiyacı ve gözlemi katalogdan okunur (tests/detail_fetch.py `Details`; 3.1'e kadar
     MatchDataFetcher); detay aşaması (sofascore_scraper/services/detail_phase.py) yalnızca ihtiyaçları önbellekte tutar.

2.x'in `/api/matches` yolları, liste biçimleri ve MatchDataFetcher 3.1'de kalktı (P30).
"""
from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterator, Optional
from unittest.mock import MagicMock

import pytest

import store_fixtures as sf
from sofascore_scraper import refresh
from detail_fetch import LEGACY_DETAIL_KEYS, Details, _stored_observation, legacy_detail
from sofascore_scraper.services.detail_phase import NO_TOURNAMENT_DIR, DetailPhase
from sofascore_scraper.status import OBSERVATION_KEY
from sofascore_scraper.store import PayloadCorrupt, Store, open_store

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
    for key in ("SOFASCORE_REFRESH__WINDOW_HOURS", "SOFASCORE_REFRESH__MIN_INTERVAL_HOURS", "SOFASCORE_REFRESH__INCLUDE_LEGACY", "SOFASCORE_FETCH__ONLY_FINISHED"):
        monkeypatch.delenv(key, raising=False)


def _fixture(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sf.LegacyFixture:
    """Taze kurulmuş veri dizini; web katmanı ve Store sınırı kaydedicisi (DATA_DIR) ona çevrilir."""
    fixture = sf.build_fixture(name, tmp_path / "data")
    monkeypatch.setenv("SOFASCORE_STORAGE__DATA_DIR", str(fixture.data_dir))
    return fixture


@pytest.fixture
def canonical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sf.LegacyFixture:
    return _fixture("canonical", tmp_path, monkeypatch)


@pytest.fixture
def old_forms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sf.LegacyFixture:
    return _fixture("legacy", tmp_path, monkeypatch)


def service(fixture: sf.LegacyFixture) -> Store:
    return open_store(fixture.data_dir)


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


# --- eski detay biçimi ----------------------------------------------------------------------------------

def test_detail_keys_are_the_required_slices_in_table_order() -> None:
    assert LEGACY_DETAIL_KEYS == sf.REQUIRED_SLICES
    assert "point_by_point" not in LEGACY_DETAIL_KEYS  # isteğe bağlı dilim eski yanıtta yoktur


@pytest.mark.parametrize("name", ["canonical", "processed_only"])
def test_detail_of_a_directory_written_by_current_code_is_its_files(
        name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bugünkü yazıcıların ürettiği dizinlerde servis, dizindeki dosyaların kendisini verir: değer ve anahtar sırası."""
    fixture = _fixture(name, tmp_path, monkeypatch)
    queries = service(fixture)
    assert fixture.details and not any(d.combined or not d.has_basic for d in fixture.details)
    for record in fixture.details:
        expected = files_detail(folder(fixture, record))
        detail = legacy_detail(queries, record.event_id)
        assert detail == expected
        assert list(detail) == list(expected) and next(iter(detail)) == "basic"


def test_detail_leaves_optional_slices_out(canonical: sf.LegacyFixture) -> None:
    record = record_of(canonical, WIM_B)
    assert "point_by_point.json" in record.files
    detail = legacy_detail(service(canonical), WIM_B)
    assert detail is not None and "point_by_point" not in detail and OBSERVATION_KEY not in detail


def test_detail_is_none_for_listed_only_unknown_and_impossible_ids(canonical: sf.LegacyFixture) -> None:
    queries = service(canonical)
    assert open_store(canonical.data_dir).events.get(NO_DETAIL) is not None  # katalog maçı biliyor, yükü yok
    assert legacy_detail(queries, NO_DETAIL) is None
    assert legacy_detail(queries, UNKNOWN) is None
    assert legacy_detail(queries, -1) is None
    assert legacy_detail(queries, 2 ** 63) is None  # kataloğun saklayamayacağı kimlik: hata değil, "yok"
    assert legacy_detail(queries, -(2 ** 63) - 1) is None


def test_detail_of_an_empty_data_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = _fixture("empty", tmp_path, monkeypatch)
    assert legacy_detail(service(fixture), ARS) is None


def test_slice_file_holding_json_null_is_kept_as_none(canonical: sf.LegacyFixture) -> None:
    """Eski okuyucu gibi: dosyası olan dilim, içeriği `null` olsa da yanıtta anahtarıyla yer alır."""
    record = record_of(canonical, ARS)
    (folder(canonical, record) / "lineups.json").write_text("null", encoding="utf-8")
    detail = legacy_detail(service(canonical), ARS)
    assert detail is not None and detail["lineups"] is None
    assert list(detail) == ["basic", *sf.REQUIRED_SLICES]


# --- eski biçimler: tasarımın düzeltmeleri (01-storage.md 5.1, 5.2) ---------------------------------------

def test_directory_with_only_the_combined_file_is_an_event(old_forms: sf.LegacyFixture) -> None:
    """Düzeltme 1: yalnızca `<id>/<id>.json` olan dizin de maçtır; eski okuyucu 404 veriyordu."""
    record = record_of(old_forms, BRE)
    assert record.combined and not record.has_basic
    combined = read_json(folder(old_forms, record) / f"{BRE}.json")
    detail = legacy_detail(service(old_forms), BRE)
    assert detail == {key: combined[key] for key in ("basic", *sf.REQUIRED_SLICES)}
    assert list(detail) == ["basic", *sf.REQUIRED_SLICES]


def test_combined_file_next_to_basic_json_gives_the_same_answer(old_forms: sf.LegacyFixture) -> None:
    """Olay yükü basic.json'dan, dilimler birleşik dosyadan: eski okuyucunun verdiği sözlükle aynı."""
    record = record_of(old_forms, LIV)
    assert record.combined and record.has_basic
    directory = folder(old_forms, record)
    combined = read_json(directory / f"{LIV}.json")
    detail = legacy_detail(service(old_forms), LIV)
    assert detail == combined and list(detail) == list(combined)
    assert detail["basic"] == read_json(directory / "basic.json")


def test_separate_file_wins_over_the_combined_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Dilim önce kendi dosyasından okunur; birleşik dosyadaki tanınmayan anahtarlar yanıtta yer almaz. Eski okuyucu
    birleşik dosya varsa yalnızca onu, olduğu gibi döndürüyordu.
    """
    monkeypatch.setenv("SOFASCORE_STORAGE__DATA_DIR", str(tmp_path))
    base = tmp_path / "match_details" / "7"
    base.mkdir(parents=True)
    event = {"id": 7, "startTimestamp": sf.FIXTURE_NOW}
    (base / "basic.json").write_text(json.dumps({**event, "copy": "file"}), encoding="utf-8")
    (base / "h2h.json").write_text(json.dumps({"teamDuel": {"homeWins": 3}}), encoding="utf-8")
    (base / "7.json").write_text(json.dumps({
        "basic": {**event, "copy": "combined"}, "h2h": {"teamDuel": {"homeWins": 1}},
        "incidents": {"incidents": [{"time": 5}]}, "odds": {"x": 1},
    }), encoding="utf-8")
    assert legacy_detail(open_store(tmp_path), 7) == {
        "basic": {**event, "copy": "file"}, "h2h": {"teamDuel": {"homeWins": 3}},
        "incidents": {"incidents": [{"time": 5}]},
    }


def test_truncated_slice_file_drops_only_that_slice(old_forms: sf.LegacyFixture) -> None:
    """Düzeltme 3: yarıda kesilmiş dilim dosyası yalnızca o dilimi düşürür; eski okuyucu 500 veriyordu."""
    directory = folder(old_forms, record_of(old_forms, AVL))
    with pytest.raises(ValueError):
        read_json(directory / "statistics.json")
    detail = legacy_detail(service(old_forms), AVL)
    assert detail is not None and list(detail) == ["basic", *[k for k in sf.REQUIRED_SLICES if k != "statistics"]]
    assert detail["basic"] == read_json(directory / "basic.json")


def test_directory_without_an_event_payload_is_not_an_event(old_forms: sf.LegacyFixture) -> None:
    assert not record_of(old_forms, NEW).has_basic
    assert legacy_detail(service(old_forms), NEW) is None


def test_event_stored_in_two_places_reads_the_copy_with_the_newest_event_payload(old_forms: sf.LegacyFixture) -> None:
    current, stale = record_of(old_forms, ARS, "L1"), record_of(old_forms, ARS, "L3")
    newest = read_json(folder(old_forms, current) / "basic.json")
    assert newest != read_json(folder(old_forms, stale) / "basic.json")
    detail = legacy_detail(service(old_forms), ARS)
    assert detail is not None and detail["basic"] == newest
    # bayat kopya yenilenirse (olay yükü artık en yeni) geçerli olan o olur
    replacement = folder(old_forms, stale) / "basic.json.new"
    replacement.write_text(json.dumps({**newest, "copy": "flat"}), encoding="utf-8")
    os.replace(replacement, folder(old_forms, stale) / "basic.json")
    os.utime(folder(old_forms, stale) / "basic.json", (sf.FIXTURE_NOW, sf.FIXTURE_NOW))  # öteki kopyadan yeni
    reopen(old_forms)
    assert legacy_detail(service(old_forms), ARS) == {"basic": {**newest, "copy": "flat"}}


# --- katalog dosyaların gerisindeyken ------------------------------------------------------------------

def test_slice_file_removed_behind_the_catalog_is_left_out(canonical: sf.LegacyFixture) -> None:
    """Katalog "yük var" derken dosya yok: yalnızca o dilim düşer; kalanlar okunur."""
    directory = folder(canonical, record_of(canonical, ARS))
    queries = service(canonical)  # depo açık: katalog dosyalarla eşit
    expected = files_detail(directory)
    (directory / "h2h.json").unlink()
    detail = legacy_detail(queries, ARS)
    del expected["h2h"]
    assert detail == expected and list(detail) == list(expected)
    reopen(canonical)  # sonraki açılış uzlaştırır
    assert legacy_detail(service(canonical), ARS) == expected


def test_slice_file_truncated_behind_the_catalog_is_left_out(canonical: sf.LegacyFixture) -> None:
    directory = folder(canonical, record_of(canonical, ARS))
    queries = service(canonical)
    (directory / "statistics.json").write_text('{"statistics": [', encoding="utf-8")
    with pytest.raises(PayloadCorrupt):
        open_store(canonical.data_dir).events.payloads(ARS)
    detail = legacy_detail(queries, ARS)
    assert detail is not None and list(detail) == ["basic", *[k for k in sf.REQUIRED_SLICES if k != "statistics"]]


def test_event_payload_removed_behind_the_catalog_is_no_record(canonical: sf.LegacyFixture) -> None:
    directory = folder(canonical, record_of(canonical, ARS))
    queries = service(canonical)
    (directory / "basic.json").unlink()
    assert legacy_detail(queries, ARS) is None


# --- kaydın yeri ----------------------------------------------------------------------------------------

@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Yenileme kararı `time.time()`'a bakar; fabrikadaki gözlem zamanları FIXTURE_NOW'a göre seçilmiştir."""
    monkeypatch.setattr(time, "time", lambda: float(sf.FIXTURE_NOW))


def details_for(data_dir: Path) -> Details:
    return Details(data_dir)


def phase_for(data_dir: Path) -> DetailPhase:
    """Bir işin detay aşaması (ihtiyaç önbelleği boş başlar)."""
    return DetailPhase(open_store(data_dir), MagicMock())


def test_location_gives_the_directory_of_every_legacy_form(old_forms: sf.LegacyFixture) -> None:
    details = details_for(old_forms.data_dir)
    root = os.path.join(str(old_forms.data_dir), "match_details")
    for event_id, form in ((ARS, "L1"), (LIV, "L1"), (LEE, "L3"), (BRE, "L3"), (sf.event_id(sf.LIGA_A), "L2"),
                           (sf.event_id(sf.FRIENDLY_A), "L5")):
        record = record_of(old_forms, event_id, form)
        parts = record.path.split("/")
        expected_dirs = (None, None) if form == "L3" else (parts[1], parts[2])
        assert details.location(str(event_id)) == (*expected_dirs, os.path.join(root, *parts[1:]))
    assert details.location(str(sf.event_id(sf.FRIENDLY_A)))[0] == NO_TOURNAMENT_DIR
    assert details.location(str(NEW)) is None  # dizin var, olay yükü yok
    assert details.location(str(UNKNOWN)) is None


def test_location_accepts_an_integer_and_only_canonical_ids(canonical: sf.LegacyFixture) -> None:
    details = details_for(canonical.data_dir)
    assert details.location(ARS) == details.location(str(ARS)) is not None
    for text in ("", "abc", f"0{ARS}", f" {ARS}", f"{ARS}.0", f"-{ARS}", "١٢٣", str(2 ** 63), "processed"):
        assert details.location(text) is None
    assert details.location(str(NO_DETAIL)) is None  # yalnızca listeden bilinen maçın dizini yok


# --- işin ihtiyaç önbelleği ----------------------------------------------------------------------------

def test_the_job_cache_keeps_only_the_needs(old_forms: sf.LegacyFixture, frozen_clock: None) -> None:
    """Detay aşaması yalnızca ihtiyaçları önbellekte tutar; kararlar önbelleksiz okumayla aynıdır."""
    details, phase = details_for(old_forms.data_dir), phase_for(old_forms.data_dir)
    assert phase._needs == {}
    ids = [str(event_id) for event_id in old_forms.event_ids + [UNKNOWN]]
    # iki yerde duran maç dahil: eskiden önbellek ilk listelenen (bayat) kopyayı, arama lig/sezon kopyasını seçiyordu
    needs = phase.needs(ids)
    assert needs == {mid: details.need(mid) for mid in ids} and phase._needs == needs


def test_match_saved_during_a_job_is_found_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Yazma kataloğu aynı işlemde günceller: kaydedilen maçın yeri (v3) ve ihtiyacı hemen doğru okunur."""
    monkeypatch.setenv("SOFASCORE_STORAGE__DATA_DIR", str(tmp_path))
    details = details_for(tmp_path)
    event = sf.basic_payload(sf.PL_ARS)
    assert details.location(str(ARS)) is None and details.need(str(ARS)) == "full"
    data = {"basic": event, **{key: sf.slice_payload(key, event) for key in sf.REQUIRED_SLICES}}
    details.save(str(ARS), data)
    found = details.location(str(ARS))
    assert found is not None and os.path.isfile(os.path.join(found[2], "event.json.gz"))
    assert details.need(str(ARS)) == "none"
    loaded = details.stored(str(ARS))
    assert loaded.pop(OBSERVATION_KEY)["observed_at_utc"] is not None  # Store her olay yükünü gözlem olarak saklar
    assert loaded == data


# --- kaydın okunması -----------------------------------------------------------------------------------

def test_the_record_gives_the_files_and_the_observation(canonical: sf.LegacyFixture) -> None:
    """Bugünkü kodun yazdığı dizinlerde: dosyaların kendisi ve gözlem (an, yapışkan bayrak)."""
    details = details_for(canonical.data_dir)
    observed, regressed = 0, 0
    for record in canonical.details:
        directory = folder(canonical, record)
        loaded = details.stored(str(record.event_id))
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


def test_the_observation_next_to_a_combined_file_is_read(old_forms: sf.LegacyFixture, frozen_clock: None) -> None:
    """Düzeltme 2: birleşik dosyası olan kaydın gözlemi okunur; eskiden okunmaz, kayıt hiç yenilenmezdi."""
    details = details_for(old_forms.data_dir)
    directory = folder(old_forms, record_of(old_forms, LIV))
    loaded = details.stored(str(LIV))
    assert loaded[OBSERVATION_KEY]["observed_at_utc"] == read_json(directory / "observation.json")["observed_at_utc"]
    assert details.need(str(LIV)) == "refresh"
    assert str(LIV) in phase_for(old_forms.data_dir).refresh_due()


def test_the_record_of_the_combined_only_directory_is_read(old_forms: sf.LegacyFixture, frozen_clock: None) -> None:
    details = details_for(old_forms.data_dir)
    assert details.location(str(BRE)) is not None
    loaded = details.stored(str(BRE))
    assert list(loaded) == ["basic", *sf.REQUIRED_SLICES]  # gözlemi yok: eski kayıt
    assert details.need(str(BRE)) == "none"  # eskiden bulunamıyor, "full" sayılıyordu


def test_only_the_truncated_slice_is_dropped(old_forms: sf.LegacyFixture, frozen_clock: None) -> None:
    details = details_for(old_forms.data_dir)
    loaded = details.stored(str(AVL))
    assert [k for k in loaded if k != OBSERVATION_KEY] == ["basic", *[k for k in sf.REQUIRED_SLICES if k != "statistics"]]
    assert details.need(str(AVL)) == "refill"


def test_what_is_not_a_record_reads_empty(old_forms: sf.LegacyFixture) -> None:
    details = details_for(old_forms.data_dir)
    assert details.stored(str(NEW)) == {}
    assert details.stored("1") == {}
    assert details.stored("abc") == {}


def test_the_valid_copy_is_read(old_forms: sf.LegacyFixture) -> None:
    """Hangi kopyanın okunacağını katalog söyler: bayat düz kopya değil, geçerli kayıt döner."""
    details = details_for(old_forms.data_dir)
    current, stale = folder(old_forms, record_of(old_forms, ARS, "L1")), folder(old_forms, record_of(old_forms, ARS, "L3"))
    loaded = details.stored(str(ARS))
    assert loaded["basic"] == read_json(current / "basic.json") != read_json(stale / "basic.json")
    assert details.location(str(ARS))[2] == str(current)


def test_slice_file_removed_behind_the_catalog_is_a_refill_not_a_full_fetch(
        canonical: sf.LegacyFixture, frozen_clock: None) -> None:
    """Depo açıkken bir dilim dosyası silinirse (elle, başka bir araçla) maç yeniden baştan indirilmez."""
    details = details_for(canonical.data_dir)
    directory = folder(canonical, record_of(canonical, ARS))
    assert details.need(str(ARS)) == "none"
    (directory / "h2h.json").unlink()
    loaded = details.stored(str(ARS))
    assert "basic" in loaded and "h2h" not in loaded and "lineups" in loaded
    assert details.need(str(ARS)) == "refill"


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
    """`status_regressed` gözlemle birlikte depodan okunur ve yenilemede korunur (boru hattının `refresh` birimi)."""
    regressed = next(d for d in canonical.details
                     if (folder(canonical, d) / "observation.json").is_file()
                     and read_json(folder(canonical, d) / "observation.json").get("status_regressed"))
    details = details_for(canonical.data_dir)
    directory = folder(canonical, regressed)
    basic = read_json(directory / "basic.json")
    with _serving(basic):
        data = details.refresh(str(regressed.event_id))
    assert data is not None and data[OBSERVATION_KEY]["status_regressed"] is True
    assert read_json(directory / "observation.json")["status_regressed"] is True


def _iter_slice_files(directory: Path) -> Iterator[str]:
    return (path.name for path in sorted(directory.iterdir()) if path.suffix == ".json")


def test_reading_writes_nothing_into_the_event_directories(old_forms: sf.LegacyFixture, frozen_clock: None) -> None:
    before = {d.path: (list(_iter_slice_files(folder(old_forms, d))), folder(old_forms, d).stat().st_mtime_ns)
              for d in old_forms.details}
    details = details_for(old_forms.data_dir)
    for event_id in old_forms.event_ids:
        details.need(str(event_id))
        details.location(str(event_id))
        legacy_detail(service(old_forms), event_id)
    assert before == {d.path: (list(_iter_slice_files(folder(old_forms, d))), folder(old_forms, d).stat().st_mtime_ns)
                      for d in old_forms.details}
