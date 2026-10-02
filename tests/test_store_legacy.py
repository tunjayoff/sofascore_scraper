"""
src/store/legacy.py: eski düzenin salt okunur okuyucusu (plan maddesi ST-05).

Üç şey denetlenir:
  1. Okuyucu, `tests/store_fixtures.py`'nin kurduğu her biçimi (L1-L5, program, özetler, sezon listeleri,
     değişiklik günlüğü, izleyici dosyaları) tanır ve bölüm 2.3 / 5.1 / 5.2'deki kuralları uygular.
  2. Bugünkü okuyucularla ilişkisi: maç kümesi `MatchDataFetcher._build_match_index` ile aynıdır (RD-1'den
     beri o da depodan okur), beklenen dilimler `_expected_slices` ile aynıdır, ve bugünkü ağaç
     gezginlerinin hangisinin daha az ya da daha çok maç bulduğu `WALKER_DIFFERENCES` tablosunda durur.
  3. Modül yalnızca okur ve katman kuralına uyar.

Mantıksal döküm (`tests/store_dump.py`) de burada sınanır.
"""
from __future__ import annotations

import ast
import csv
import datetime as dt
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import pytest

import legacy_writer
import store_dump
import store_fixtures as sf
from src import match_data_fetcher as mdf
from src import refresh, slices, sports, status, watcher
from src.config_manager import ConfigManager
from src.match_data_fetcher import MatchDataFetcher
from src.paths import safe_name
from src.services import stats as stats_service
from src.store import Ref, Store, catalog, codec, derive, layout, legacy, open_store
from src.store.errors import LayoutError, PayloadCorrupt, PayloadMissing, StoreError
from src.store.legacy import LegacyEvent, LegacyProblem, LegacyReader, LegacyReport, LegacySliceError

ROOT = Path(__file__).resolve().parent.parent
LEGACY_SOURCE = ROOT / "src" / "store" / "legacy.py"
PL_DIR = "match_details/17_Premier_League/season_Premier_League_26_27"
UTC = dt.timezone.utc


# --- yardımcılar ------------------------------------------------------------------------------


@pytest.fixture(params=sf.FIXTURE_NAMES)
def fx(request: pytest.FixtureRequest, tmp_path: Path) -> sf.LegacyFixture:
    return sf.build_fixture(request.param, tmp_path / "data")


@pytest.fixture
def canonical(tmp_path: Path) -> sf.LegacyFixture:
    return sf.build_fixture("canonical", tmp_path / "data")


@pytest.fixture
def old_forms(tmp_path: Path) -> sf.LegacyFixture:
    return sf.build_fixture("legacy", tmp_path / "data")


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Yenileme kararı `time.time()`'a bakar; fabrikadaki gözlem zamanları FIXTURE_NOW'a göre seçilmiştir."""
    monkeypatch.setattr(time, "time", lambda: float(sf.FIXTURE_NOW))


def scan(data_dir: Path, **kwargs: Any) -> Tuple[Dict[int, LegacyEvent], LegacyReport]:
    report = LegacyReport()
    events = list(LegacyReader(data_dir, **kwargs).iter_events(report=report))
    assert [e.event_id for e in events] == sorted(e.event_id for e in events), "kimliğe göre sıralı gelmeli"
    return {e.event_id: e for e in events}, report


def write(root: Path, rel: str, data: Any, mtime: Optional[int] = None) -> Path:
    """Dosya yazar: bayt ise aynen, değilse fabrikanın JSON biçimiyle."""
    path = root.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else sf.dump_json(data))
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def event_of(eid: int, sport: str = "football") -> Dict[str, Any]:
    """Küçük bir /event yükü: okuyucunun baktığı tek alan `id`'dir."""
    return {"id": eid, "tournament": {"category": {"sport": {"slug": sport}}}, "startTimestamp": sf.FIXTURE_NOW}


def tree_state(root: Path) -> Dict[str, Tuple[str, int]]:
    """Ağacın tamamı: göreli yol → (içerik özeti ya da "dir", mtime_ns)."""
    out: Dict[str, Tuple[str, int]] = {}
    for path in sorted(root.rglob("*")):
        digest = "dir" if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()
        out[path.relative_to(root).as_posix()] = (digest, path.stat().st_mtime_ns)
    return out


def fetcher_for(data_dir: Path) -> MatchDataFetcher:
    return MatchDataFetcher(config_manager=ConfigManager(), data_dir=str(data_dir))


# --- sabitler yazıcılardakilerle aynı ---------------------------------------------------------


def test_names_equal_the_writers_constants() -> None:
    """Store, yazıcı modüllerini içe aktaramaz (katman kuralı); adların eşit kaldığını bu test güvenceye alır."""
    assert legacy.UNAVAILABLE_FILE == mdf.UNAVAILABLE_FILE
    assert legacy.SLICE_STATUS_FILE == mdf.SLICE_STATUS_FILE
    assert legacy.NO_TOURNAMENT_DIR == mdf.NO_TOURNAMENT_DIR
    assert f"{legacy.BASIC_KEY}.json" == legacy.BASIC_FILE == mdf.REQUIRED_FILES[0]
    assert legacy.OBSERVATION_KEY == status.OBSERVATION_KEY
    assert legacy.OBSERVATION_FILE == f"{status.OBSERVATION_KEY}.json"
    assert legacy.CHANGES_FILE == refresh.SCORE_CHANGES_FILE
    assert legacy.WATCH_EVENTS_FILE == watcher.WATCH_EVENTS_FILE
    assert legacy._WATCH_STATE_RE.fullmatch(watcher.WATCH_STATE_FILE.format(sport="table-tennis")).group(1) == \
        "table-tennis"
    assert LegacyReader("x").known_slices == tuple(s.key for s in sports.DETAIL_SLICES)
    for name in ("a b/c\\d", "Premier League", "Wimbledon, Men"):
        assert legacy._safe_name(name) == safe_name(name) == sf.safe_name(name)


# --- keşif: her biçim ---------------------------------------------------------------------------

# Okuyucunun bulup dizin ağacını gezen hiçbir gezginin bulamadığı tek kayıt: yalnızca birleşik dosyası (L4)
# olan düz dizin. Tasarım (5.1) onu maç dizini sayar. Depodan okuyanlar (RD-1: `_find_match_path`,
# `_build_match_index`, `/api/matches/{id}`) onu bulur; gezginler için `basic.json`'ı olmayan dizin görünmez.
COMBINED_ONLY = {"legacy": {17018554}}
# Dizini olduğu halde maç sayılmayanlar (olay yükü yok): fixture adı → maç id'leri
NO_EVENT_PAYLOAD = {"legacy": {17018572}}


def test_event_ids_equal_build_match_index(fx: sf.LegacyFixture) -> None:
    events, _ = scan(fx.data_dir)
    index = fetcher_for(fx.data_dir)._build_match_index()
    assert all(mid.isdigit() for mid in index)
    assert set(events) == {int(mid) for mid in index}  # RD-1: dizin de depodan okunur (birleşik dosyalı dizin dahil)
    assert COMBINED_ONLY.get(fx.name, set()) <= set(events)
    assert set(events) == set(fx.detail_ids) - NO_EVENT_PAYLOAD.get(fx.name, set())


def test_every_fixture_directory_is_classified(fx: sf.LegacyFixture) -> None:
    """Fabrikanın yazdığı her maç dizini aynı biçim, yol ve dosyalarla bulunur (ya da sorun olarak bildirilir)."""
    reader = LegacyReader(fx.data_dir)
    report = LegacyReport()
    found = {d.path: d for d in reader.event_dirs(report)}
    assert list(found) == sorted(found)
    for record in fx.details:
        if not record.has_basic and not record.combined:
            assert LegacyProblem(record.path, legacy.PROBLEM_NO_EVENT, "maç dizininde olay yükü yok") in report.problems
            assert record.path not in found
            continue
        entry = found.pop(record.path)
        assert (entry.name, entry.form, entry.has_basic, entry.combined) == \
            (str(record.event_id), record.form, record.has_basic, record.combined)
        assert entry.entries == record.files
        assert entry.sig == reader.signature(record.path) == \
            f"{(fx.data_dir / record.path).stat().st_mtime_ns}:{len(record.files)}"
        assert entry.tournament_id == (record.league_id if record.form == "L1" else None)
        assert reader.event_dir_at(record.path) == entry
    assert not found


def test_forms_of_the_legacy_fixture(old_forms: sf.LegacyFixture) -> None:
    events, report = scan(old_forms.data_dir)
    forms = {eid: (e.dir.form, e.dir.combined, e.dir.league_dir, e.dir.season_dir) for eid, e in events.items()}
    assert forms == {
        15000001: ("L2", False, "LaLiga", "season_LaLiga_25_26"),
        15500001: ("L5", False, "_no_tournament", "football"),
        15500002: ("L5", False, "_no_tournament", "tennis"),
        15500003: ("L5", False, "_no_tournament", "unknown"),
        16837335: ("L1", False, "17_Premier_League", "season_Premier_League_26_27"),
        16867839: ("L3", False, None, None),
        17018554: ("L3", True, None, None),
        17099711: ("L1", True, "17_Premier_League", "season_Premier_League_26_27"),
        17185003: ("L1", False, "17_Premier_League", "season_Premier_League_26_27"),
    }
    assert report.problems == [
        LegacyProblem(f"{PL_DIR}/17018572", "no_event_payload", "maç dizininde olay yükü yok"),
        LegacyProblem(f"{PL_DIR}/17185003/statistics.json", "corrupt",
                      "Expecting ':' delimiter: line 4 column 15 (char 40)"),
    ]


def test_processed_directory_and_stray_files_are_skipped(tmp_path: Path) -> None:
    write(tmp_path, "match_details/processed/basic.json", event_of(1))
    write(tmp_path, "match_details/processed/season_x/5/basic.json", event_of(5))
    write(tmp_path, "match_details/README.txt", b"not a directory")
    write(tmp_path, "match_details/17_PL/notes.txt", b"file at season level")
    write(tmp_path, "match_details/17_PL/season_x/notes.txt", b"file at match level")
    write(tmp_path, "match_details/17_PL/season_x/7/basic.json", event_of(7))
    events, report = scan(tmp_path)
    assert list(events) == [7] and not report.problems


def test_missing_trees_give_empty_results(tmp_path: Path) -> None:
    reader = LegacyReader(tmp_path / "does-not-exist")
    report = LegacyReport()
    assert not reader.has_data()
    assert reader.event_dirs(report) == [] and list(reader.iter_events(report=report)) == []
    assert reader.schedule_pages(report) == [] and reader.summary_files() == []
    assert reader.season_lists(report=report) == [] and reader.change_log(report) == []
    assert reader.watch_events(report) == [] and reader.watch_states(report) == []
    assert reader.signature("match_details") is None and reader.event_dir_at("match_details/1") is None
    assert report == LegacyReport()


def test_has_data(fx: sf.LegacyFixture) -> None:
    assert LegacyReader(fx.data_dir).has_data() is (fx.name != "empty")


# --- maç dizini tanıma kuralı -------------------------------------------------------------------


def test_event_directory_needs_a_payload_whose_id_is_the_directory_name(tmp_path: Path) -> None:
    write(tmp_path, "match_details/17_PL/season_x/100/basic.json", event_of(100))
    write(tmp_path, "match_details/17_PL/season_x/101/basic.json", event_of(999))  # başka maçın yükü
    write(tmp_path, "match_details/17_PL/season_x/102/basic.json", b'{"id": 102, "tourn')  # yarım dosya
    write(tmp_path, "match_details/17_PL/season_x/103/basic.json", {"id": "103"})  # id metin
    write(tmp_path, "match_details/17_PL/season_x/104/basic.json", [104])  # nesne değil
    write(tmp_path, "match_details/17_PL/season_x/0105/basic.json", event_of(105))  # baştaki sıfır
    write(tmp_path, "match_details/17_PL/season_x/abc/basic.json", event_of(106))  # sayı olmayan ad
    write(tmp_path, "match_details/17_PL/season_x/107/basic.json", {"id": True})
    write(tmp_path, "match_details/17_PL/season_x/108/statistics.json", {"statistics": []})  # olay yükü yok
    write(tmp_path, "match_details/109/statistics.json", {"statistics": []})  # düz dizinde olay yükü yok
    write(tmp_path, "match_details/17_PL/season_x/110/basic.json", b"null")
    events, report = scan(tmp_path)
    assert list(events) == [100]
    base = "match_details/17_PL/season_x"
    assert {(p.path, p.kind) for p in report.problems} == {
        (f"{base}/101", "id_mismatch"), (f"{base}/102", "corrupt"), (f"{base}/103", "id_mismatch"),
        (f"{base}/104", "id_mismatch"), (f"{base}/0105", "id_mismatch"), (f"{base}/abc", "id_mismatch"),
        (f"{base}/107", "id_mismatch"), (f"{base}/108", "no_event_payload"),
        ("match_details/109", "no_event_payload"), (f"{base}/110", "no_event_payload"),
    }
    reader = LegacyReader(tmp_path)
    with pytest.raises(LayoutError):
        reader.read_event(reader.event_dir_at(f"{base}/101"))
    with pytest.raises(PayloadCorrupt):
        reader.read_event(reader.event_dir_at(f"{base}/102"))
    with pytest.raises(PayloadMissing):
        reader.read_event(reader.event_dir_at(f"{base}/110"))
    assert reader.event_dir_at(f"{base}/108") is None and reader.event_dir_at("matches/1") is None


def test_first_level_directory_with_basic_json_is_a_flat_event_as_today(tmp_path: Path) -> None:
    """
    Birinci düzeyde basic.json varsa dizin düz kayıttır, altına bakılmaz (RD-1 öncesinin `_build_match_index`'i
    gibi). O dizin, kimliği adına uymadığı için maç sayılmaz; depodan okuyan `_build_match_index` de onu vermez
    (eskiden dizin adıyla, "17_PL" olarak veriyordu).
    """
    write(tmp_path, "match_details/17_PL/basic.json", event_of(1))
    write(tmp_path, "match_details/17_PL/season_x/7/basic.json", event_of(7))
    events, report = scan(tmp_path)
    assert events == {} and [(p.path, p.kind) for p in report.problems] == [("match_details/17_PL", "id_mismatch")]
    assert fetcher_for(tmp_path)._build_match_index() == {}


# --- aynı maç birden çok yerde -------------------------------------------------------------------


def test_duplicate_id_newest_basic_json_wins(old_forms: sf.LegacyFixture) -> None:
    events, report = scan(old_forms.data_dir)
    winner = events[16837335]
    assert winner.path == f"{PL_DIR}/16837335" and winner.duplicates == ("match_details/16837335",)
    assert winner.event["status"]["type"] == "finished"  # düz dizindeki bayat kopya "inprogress"
    assert [(s.kind, s.key, s.path, s.winner) for s in report.superseded] == [
        ("event", "16837335", "match_details/16837335", f"{PL_DIR}/16837335"),
    ]
    assert all(not e.duplicates for eid, e in events.items() if eid != 16837335)


@pytest.mark.parametrize("flat_age, nested_age, winner", [
    (0, 10, "flat"),  # düz dizindeki daha yeni
    (10, 0, "nested"),
    (5, 5, "nested"),  # eşitlikte lig/sezon dizini
])
def test_duplicate_rule_is_by_mtime_then_form(tmp_path: Path, flat_age: int, nested_age: int, winner: str) -> None:
    paths = {"flat": "match_details/42", "nested": "match_details/17_PL/season_x/42",
             "no_tournament": "match_details/_no_tournament/football/42"}
    ages = {"flat": flat_age, "nested": nested_age, "no_tournament": 20}
    for name, base in paths.items():
        write(tmp_path, f"{base}/basic.json", {**event_of(42), "copy": name}, sf.BASE_MTIME - ages[name])
    events, report = scan(tmp_path)
    assert events[42].event["copy"] == winner and events[42].path == paths[winner]
    assert set(events[42].duplicates) == set(paths.values()) - {paths[winner]}
    assert events[42].duplicates[-1] == paths["no_tournament"]  # en eskisi sonda
    assert len(report.superseded) == 2 and {s.winner for s in report.superseded} == {paths[winner]}


def test_unreadable_newest_copy_falls_back_to_the_older_one(tmp_path: Path) -> None:
    write(tmp_path, "match_details/42/basic.json", b"{", sf.BASE_MTIME)
    write(tmp_path, "match_details/17_PL/season_x/42/basic.json", event_of(42), sf.BASE_MTIME - 100)
    events, report = scan(tmp_path)
    assert events[42].path == "match_details/17_PL/season_x/42" and events[42].duplicates == ()
    assert [(p.path, p.kind) for p in report.problems] == [("match_details/42", "corrupt")]
    assert report.superseded == []


def test_combined_only_copy_uses_the_combined_file_mtime(tmp_path: Path) -> None:
    write(tmp_path, "match_details/42/42.json", {"basic": {**event_of(42), "copy": "combined"}}, sf.BASE_MTIME)
    write(tmp_path, "match_details/17_PL/season_x/42/basic.json", event_of(42), sf.BASE_MTIME - 100)
    events, _ = scan(tmp_path)
    assert events[42].event["copy"] == "combined" and events[42].dir.mtime_ns == sf.BASE_MTIME * 10**9


# --- dilim durumu ve işaret sayaçları (bölüm 2.3) -------------------------------------------------


def counters(event: LegacyEvent) -> Dict[str, Tuple[str, bool, int, int, Optional[Tuple[str, Optional[int], int]]]]:
    return {
        s.key: (s.state, s.has_payload, s.empty_count, s.unverified_empty_count,
                (s.error.reason, s.error.status, s.error.count) if s.error else None)
        for s in event.slices if s.key != "event"
    }


OK = ("ok", True, 0, 0, None)
ALL_OK = {key: OK for key in sf.REQUIRED_SLICES}


def test_marker_combinations_of_the_canonical_fixture(canonical: sf.LegacyFixture) -> None:
    """observation / _unavailable / _slice_status dosyalarının sekiz birleşimi (fabrikadaki sırayla)."""
    events, report = scan(canonical.data_dir)
    assert report == LegacyReport()
    table = {eid: (events[eid].observation is not None, counters(events[eid])) for eid in events}
    # 100: yalnızca gözlem
    assert table[16837335] == (True, ALL_OK) and table[17099711] == (True, ALL_OK)
    # 000: hiçbiri
    assert table[16867839] == (False, ALL_OK)
    # 010: eski sürümün saydığı "yok" → doğrulanmamış sayım
    assert table[17018554] == (False, {**ALL_OK, "lineups": ("empty", False, 0, 2, None),
                                       "incidents": ("empty", False, 0, 2, None)})
    # 110: eşiğin altında sayım
    assert table[17185003] == (True, {**ALL_OK, "lineups": ("empty", False, 0, 1, None)})
    # 001: başarısız istek
    assert table[17018572] == (False, {**ALL_OK, "statistics": ("error", False, 0, 0, ("403", 403, 2))})
    # 101
    assert table[17184988] == (True, {**ALL_OK, "h2h": ("error", False, 0, 0, ("timeout", None, 1))})
    # 011: kesin yanıtla doğrulanmış "yok"
    assert table[16872361] == (False, {**ALL_OK, "lineups": ("empty", False, 2, 0, None),
                                       "incidents": ("empty", False, 2, 0, None)})
    # 111: lineups 2 sayımın 1'i doğrulanmış; incidents 1 doğrulanmış + hata; pregame_form 3 doğrulanmamış
    assert table[16951514] == (True, {**ALL_OK, "lineups": ("empty", False, 1, 1, None),
                                      "incidents": ("error", False, 1, 0, ("5xx", 503, 1)),
                                      "pregame_form": ("empty", False, 0, 3, None)})
    # dosyası olan ama içinde veri olmayan dilim: yüküyle birlikte "empty"
    assert table[17092269][1]["lineups"] == ("empty", True, 2, 0, None)
    # hükmen: bütün dilimler "yok"
    assert table[17102381][1] == {key: ("empty", False, 0, 2, None) for key in sf.REQUIRED_SLICES}
    # tenis: isteğe bağlı point_by_point dosyası da bir dilimdir
    assert table[17204710][1] == {
        "statistics": OK, "team_streaks": OK, "h2h": OK, "point_by_point": OK,
        **{key: ("empty", False, 2, 0, None) for key in ("pregame_form", "lineups", "incidents")},
    }
    # işaret zamanları
    cry = events[16951514]
    at = dt.datetime(2026, 9, 29, 18, 0, tzinfo=UTC)
    assert cry.slice("lineups").empty_at == at and cry.slice("pregame_form").empty_at is None
    assert cry.slice("incidents").error == LegacySliceError("5xx", 503, at, 1)
    assert cry.slice("lineups").settled_empty() and not cry.slice("incidents").settled_empty()
    assert cry.slice("incidents").settled_empty(threshold=1) and cry.slice("missing") is None


def test_event_slice_and_file_facts(canonical: sf.LegacyFixture) -> None:
    events, _ = scan(canonical.data_dir)
    event = events[16837335]
    first = event.slices[0]
    path = canonical.data_dir / PL_DIR / "16837335" / "basic.json"
    assert (first.key, first.state, first.has_payload, first.in_combined) == ("event", "ok", True, False)
    assert first.path == f"{PL_DIR}/16837335/basic.json"
    assert first.fetched_at == float(sf.BASE_MTIME) and first.size == path.stat().st_size
    assert [s.key for s in event.slices] == ["event", *sf.REQUIRED_SLICES]
    assert event.event == json.loads(path.read_bytes()) and event.event_id == event.event["id"] == 16837335
    assert event.payloads is not None and set(event.payloads) == {"event", *sf.REQUIRED_SLICES}
    assert event.payloads["event"] is event.event
    assert event.extra_files == () and event.combined_extra_keys == () and event.problems == ()
    reader = LegacyReader(canonical.data_dir)
    light = reader.read_event(event.dir, payloads=False)
    assert light.payloads is None and light.slices == event.slices
    assert [e.event_id for e in reader.iter_events(payloads=False)] == list(events)  # rapor vermeden de çalışır


def expected_today(fetcher: MatchDataFetcher, event: LegacyEvent) -> List[str]:
    """Eski yazıcının dosya tabanlı kuralı (ST-21'e kadar `MatchDataFetcher._expected_slices`; tests/legacy_writer.py)."""
    match_dir = str(Path(fetcher.data_dir).joinpath(*event.path.split("/")))
    return legacy_writer.expected_slices(match_dir, sports.event_sport_slug(event.event) or "",
                                         mdf.UNAVAILABLE_AFTER_ATTEMPTS)


def expected_from_record(event: LegacyEvent) -> List[str]:
    """Beklenen dilimler, kayıttan: sporun `required` dilimleri, eşiğe ulaşmış "yok" sayımı olanlar hariç."""
    sport = sports.event_sport_slug(event.event) or ""
    out = []
    for detail in sports.slices_for(sport, required_only=True):
        entry = event.slice(detail.key)
        if entry is None or not entry.settled_empty(mdf.UNAVAILABLE_AFTER_ATTEMPTS):
            out.append(detail.key)
    return out


def test_expected_slices_equal_today_for_every_event(fx: sf.LegacyFixture) -> None:
    events, _ = scan(fx.data_dir)
    fetcher = fetcher_for(fx.data_dir)
    for event in events.values():
        assert expected_from_record(event) == expected_today(fetcher, event), event.path


MARKER_VALUES: Sequence[Any] = (None, 0, 1, 2, 3, -1, True, "2", "x", 1.9, [2])
CONFIRMED_VALUES: Sequence[Any] = (None, 0, 1, 2, 5, -1, True, "2", {"count": 2})


@pytest.mark.parametrize("unavailable", MARKER_VALUES, ids=lambda v: f"unavailable={v!r}")
def test_marker_mapping_for_every_count_combination(tmp_path: Path, unavailable: Any) -> None:
    """
    `_unavailable.json[k] = c`, `_slice_status.json[k].empty.count = n` → empty_count = min(c, n),
    unverified = c - min(c, n). Toplam her zaman bugünkü sayıdır, yani "beklenen dilimler" değişmez;
    bozuk değerlerde de bugünkü okuyucuyla aynı sonuç çıkar.
    """
    base = "match_details/17_PL/season_x"
    for index, confirmed in enumerate(CONFIRMED_VALUES):
        match_dir = f"{base}/{500 + index}"
        write(tmp_path, f"{match_dir}/basic.json", event_of(500 + index))
        if unavailable is not None:
            write(tmp_path, f"{match_dir}/_unavailable.json", {"lineups": unavailable, "h2h": 2})
        if confirmed is not None:
            entry = confirmed if isinstance(confirmed, dict) else {"empty": {"count": confirmed, "at": sf.MARKER_AT}}
            write(tmp_path, f"{match_dir}/_slice_status.json", {"lineups": entry})
    events, _ = scan(tmp_path)
    fetcher = fetcher_for(tmp_path)
    assert len(events) == len(CONFIRMED_VALUES)
    for index, confirmed in enumerate(CONFIRMED_VALUES):
        event = events[500 + index]
        today = legacy_writer.load_unavailable(str(tmp_path / base / str(500 + index)))
        count = max(today.get("lineups", 0), 0)
        valid = confirmed if type(confirmed) is int and confirmed > 0 else 0
        entry = event.slice("lineups")
        got = (entry.empty_count, entry.unverified_empty_count) if entry else (0, 0)
        assert got == (min(count, valid), count - min(count, valid)), (unavailable, confirmed)
        assert expected_from_record(event) == expected_today(fetcher, event), (unavailable, confirmed)
        if today:  # dosya okunabildiyse öteki dilimin sayımı da gelir
            assert event.slice("h2h").unverified_empty_count == 2


def test_infinite_marker_count_is_reported_not_raised(tmp_path: Path) -> None:
    """Python'un json modülü `Infinity`'yi kabul eder; `int()` onu çeviremez. Dosya yok sayılır."""
    base = "match_details/7"
    write(tmp_path, f"{base}/basic.json", event_of(7))
    write(tmp_path, f"{base}/_unavailable.json", b'{"lineups": Infinity}')
    events, report = scan(tmp_path)
    assert [s.key for s in events[7].slices] == ["event"]
    assert [(p.path, p.kind) for p in report.problems] == [(f"{base}/_unavailable.json", "malformed")]


@pytest.mark.skipif(os.name == "nt" or getattr(os, "geteuid", lambda: 1)() == 0, reason="izin bitleri gerekir")
def test_unreadable_directory_or_file_is_reported_and_the_scan_continues(tmp_path: Path) -> None:
    base = "match_details/17_PL"
    write(tmp_path, f"{base}/season_a/7/basic.json", event_of(7))
    locked_file = write(tmp_path, f"{base}/season_a/8/basic.json", event_of(8))
    write(tmp_path, f"{base}/season_b/9/basic.json", event_of(9))
    write(tmp_path, "matches/17_PL/1_x/round_1.json", {"events": []})
    locked_dirs = [tmp_path / base / "season_b", tmp_path / "matches" / "17_PL" / "1_x"]
    locked_file.chmod(0)
    for path in locked_dirs:
        path.chmod(0)
    try:
        events, report = scan(tmp_path)
        reader = LegacyReader(tmp_path)
        assert reader.schedule_pages(report) == [] and reader.summary_files(report) == []
        with pytest.raises(StoreError):
            reader.signature(f"{base}/season_b")
    finally:
        locked_file.chmod(0o644)
        for path in locked_dirs:
            path.chmod(0o755)
    assert list(events) == [7]
    assert [(p.path, p.kind) for p in report.problems] == [
        (f"{base}/season_b", "unreadable"), (f"{base}/season_a/8", "unreadable"),
        ("matches/17_PL/1_x", "unreadable"), ("matches/17_PL/1_x", "unreadable"),
    ]


def test_unreadable_marker_files_count_as_absent_and_are_reported(tmp_path: Path) -> None:
    base = "match_details/17_PL/season_x/7"
    write(tmp_path, f"{base}/basic.json", event_of(7))
    write(tmp_path, f"{base}/_unavailable.json", b"{")
    write(tmp_path, f"{base}/_slice_status.json", [1])
    write(tmp_path, f"{base}/observation.json", b"")
    events, report = scan(tmp_path)
    assert [s.key for s in events[7].slices] == ["event"] and events[7].observation is None
    assert [(p.path, p.kind) for p in report.problems] == [
        (f"{base}/_unavailable.json", "corrupt"), (f"{base}/_slice_status.json", "corrupt"),
        (f"{base}/observation.json", "corrupt"),
    ]
    assert events[7].problems == tuple(report.problems)
    assert legacy_writer.load_unavailable(str(tmp_path / base)) == {} == legacy_writer.load_slice_status(
        str(tmp_path / base))


def test_slice_states_from_files(tmp_path: Path) -> None:
    base = "match_details/17_PL/season_x/7"
    event = sf.basic_payload(sf.PL_ARS)
    write(tmp_path, f"{base}/basic.json", event_of(7))
    write(tmp_path, f"{base}/statistics.json", sf.slice_payload("statistics", event))  # veri var
    write(tmp_path, f"{base}/lineups.json", sf.slice_payload("lineups", event, empty=True))  # boş 200
    write(tmp_path, f"{base}/incidents.json", sf.slice_payload("incidents", event, empty=True))  # boş + hata
    write(tmp_path, f"{base}/h2h.json", b'{"teamDuel": {"homeW')  # yarım dosya
    write(tmp_path, f"{base}/team_streaks.json", "not an object")  # geçerli JSON, kural okuyamaz
    write(tmp_path, f"{base}/pregame_form.json", sf.slice_payload("pregame_form", event))
    write(tmp_path, f"{base}/point_by_point.json", {"pointByPoint": []})  # kendi kuralı var: boş liste = veri yok
    write(tmp_path, f"{base}/_unavailable.json", {"lineups": 1, "pregame_form": 2, "odds": 2})
    write(tmp_path, f"{base}/_slice_status.json", {
        "lineups": sf.empty_marker(1), "incidents": sf.error_marker("429", 429, 3),
        "pregame_form": sf.error_marker("timeout", None), "h2h": sf.error_marker("5xx", 502),
    })
    write(tmp_path, f"{base}/notes.txt", b"x")
    write(tmp_path, f"{base}/.basic.json.abc.tmp", b"{")
    (tmp_path / base / "sub").mkdir()
    events, report = scan(tmp_path)
    event7 = events[7]
    assert counters(event7) == {
        "statistics": OK,
        "team_streaks": ("error", True, 0, 0, ("corrupt", None, 1)),
        # veri varken duran işaretler olduğu gibi aktarılır; durum yine "ok"
        "pregame_form": ("ok", True, 0, 2, ("timeout", None, 1)),
        "h2h": ("error", False, 0, 0, ("corrupt", None, 1)),
        "lineups": ("empty", True, 1, 0, None),
        "incidents": ("error", True, 0, 0, ("429", 429, 3)),
        "point_by_point": ("empty", True, 0, 0, None),
    }
    assert event7.slice("h2h").path == f"{base}/h2h.json" and event7.slice("h2h").fetched_at is None
    assert set(event7.payloads) == {"event", "statistics", "team_streaks", "pregame_form", "lineups", "incidents",
                                    "point_by_point"}
    assert event7.extra_files == (".basic.json.abc.tmp", "notes.txt", "sub/")
    assert [(p.path, p.kind) for p in report.problems] == [
        (base, "unknown_name"), (f"{base}/team_streaks.json", "malformed"), (f"{base}/h2h.json", "corrupt"),
    ]


_STAT_GROUPS = [{"statisticsItems": [{"key": "shots"}]}]
CORRUPT = ("error", True, 0, 0, ("corrupt", None, 1))
EMPTY = ("empty", True, 0, 0, None)

# (dilim, dosyanın gövdesi, okuyucunun verdiği durum). Üç yanıt: kural "veri var" derse `ok`, "veri yok" derse
# yüküyle `empty`, okuyamazsa `error` / `corrupt` ve `malformed` sorunu (src.slices.slice_body_state).
BODY_STATE_CASES: List[Tuple[str, Any, Tuple[Any, ...]]] = [
    # okunamayan gövdeler: eskiden yüklem hata fırlatıyordu ve okuyucu yakalıyordu; durum aynı
    ("statistics", "abc", CORRUPT),
    ("statistics", 0, CORRUPT),
    ("statistics", ["x"], CORRUPT),
    ("statistics", [None, {"period": "1ST", "groups": _STAT_GROUPS}], CORRUPT),
    ("statistics", {"statistics": [{"period": "ALL", "groups": ["x"]}]}, CORRUPT),
    ("statistics", {"statistics": {"period": "ALL"}}, CORRUPT),
    ("h2h", {"teamDuel": ["x"]}, CORRUPT),
    ("h2h", {"teamDuel": "abc", "matches": [1]}, CORRUPT),
    ("team_streaks", ["x"], CORRUPT),
    ("team_streaks", "abc", CORRUPT),
    # eski yüklemin açıkça elediği yanlış türler eskisi gibi "veri yok"tur
    ("lineups", ["x"], EMPTY),
    ("lineups", {"home": {"players": "abc"}}, EMPTY),
    ("h2h", ["x"], EMPTY),
    ("pregame_form", ["x"], EMPTY),
    ("incidents", "abc", EMPTY),
    ("incidents", {"incidents": {"a": 1}}, EMPTY),
    ("team_streaks", {"general": {"name": "Wins"}}, EMPTY),
    # point_by_point: kendi kuralı (eskiden dolu olan her değer `ok` idi)
    ("point_by_point", {"pointByPoint": [{"games": []}]}, OK),
    ("point_by_point", {"pointByPoint": []}, EMPTY),
    ("point_by_point", {"pointByPoint": None}, EMPTY),
    ("point_by_point", {"error": {"code": 404}}, EMPTY),
    ("point_by_point", {}, EMPTY),
    ("point_by_point", [], EMPTY),
    ("point_by_point", {"pointByPoint": {"games": []}}, CORRUPT),
    ("point_by_point", [{"games": []}], CORRUPT),
    ("point_by_point", "abc", CORRUPT),
    ("point_by_point", 0, CORRUPT),
]


@pytest.mark.parametrize("combined", [False, True], ids=["own-file", "combined-file"])
def test_slice_state_follows_the_three_answers_of_the_presence_rule(tmp_path: Path, combined: bool) -> None:
    """Her gövde ayrı bir maç dizininde: bir kez dilimin kendi dosyasında, bir kez birleşik dosyada (L4)."""
    for index, (key, body, _) in enumerate(BODY_STATE_CASES, start=1):
        base = f"match_details/17_PL/season_x/{index}"
        write(tmp_path, f"{base}/basic.json", event_of(index, "tennis"))
        if combined:
            write(tmp_path, f"{base}/{index}.json", {"basic": event_of(index, "tennis"), key: body})
        else:
            write(tmp_path, f"{base}/{key}.json", body)
    events, report = scan(tmp_path)
    assert len(events) == len(BODY_STATE_CASES)
    malformed = []
    for index, (key, body, expected) in enumerate(BODY_STATE_CASES, start=1):
        base = f"match_details/17_PL/season_x/{index}"
        answer = slices.slice_body_state(key, body)
        assert answer == {OK: "data", EMPTY: "no_data", CORRUPT: "malformed"}[expected], (key, body)
        assert counters(events[index]) == {key: expected}, (key, body)
        assert events[index].payloads[key] == body  # yük, durumu ne olursa olsun okunur
        if expected == CORRUPT:
            malformed.append((f"{base}/{index}.json" if combined else f"{base}/{key}.json", "malformed"))
    assert [(p.path, p.kind) for p in report.problems] == malformed


def test_reader_never_raises_on_a_slice_body(tmp_path: Path) -> None:
    """Kurallar toplamdır: okuyucu hiçbir geçerli JSON gövdesinde hata yakalamak zorunda kalmaz."""
    bodies: List[Any] = [None, True, False, 0, 1, 1.5, "", "abc", [], ["x"], [None], [[]], [{}], {}, {"a": None},
                         {"statistics": "abc"}, {"statistics": 5}, {"statistics": [[1]]}, {"teamDuel": 5},
                         {"general": 5}, {"incidents": 5}, {"pointByPoint": 5}, {"home": 5, "away": [1]},
                         {"homeTeam": 5, "awayTeam": [1]}]
    cases = [(key, body) for key in LegacyReader(tmp_path).known_slices for body in bodies]
    for index, (key, body) in enumerate(cases, start=1):
        write(tmp_path, f"match_details/{index}/basic.json", event_of(index))
        write(tmp_path, f"match_details/{index}/{key}.json", body)
    events, report = scan(tmp_path)
    assert len(events) == len(cases) == 7 * len(bodies)
    state_of = {"data": "ok", "no_data": "empty", "malformed": "error"}
    seen = set()
    for index, (key, body) in enumerate(cases, start=1):
        entry = events[index].slice(key)
        assert entry.state == state_of[slices.slice_body_state(key, body)], (key, body)
        assert entry.has_payload and (entry.error is not None) is (entry.state == "error")
        seen.add(entry.state)
    assert seen == {"ok", "empty", "error"}
    assert {p.kind for p in report.problems} == {"malformed"}
    assert len(report.problems) == sum(1 for event in events.values() for s in event.slices if s.state == "error")


def test_known_slices_can_be_given(tmp_path: Path) -> None:
    base = "match_details/17_PL/season_x/7"
    write(tmp_path, f"{base}/basic.json", event_of(7))
    write(tmp_path, f"{base}/statistics.json", {"statistics": []})
    write(tmp_path, f"{base}/odds.json", {"markets": [1]})
    events, _ = scan(tmp_path, known_slices=("odds",))
    assert [s.key for s in events[7].slices] == ["event", "odds"] and events[7].extra_files == ("statistics.json",)


@pytest.mark.usefixtures("frozen_clock")
def test_missing_slices_equal_today_s_refill_need(fx: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Kayıttan hesaplanan "eksik dilim var" kararı, `_needs_detail_fetch` == "refill" ile aynıdır. P13'ten beri
    canlı kayıt (`none`: canlı servisin işi) ve daha yeni bir listenin bayatlamış saydığı kayıt (`refresh`) bu kuralın
    dışındadır; onların kararı ayrıca sabitlenir.
    """
    from src.store import open_store

    for key in ("REFRESH_WINDOW_HOURS", "REFRESH_MIN_INTERVAL_HOURS", "REFRESH_LEGACY"):
        monkeypatch.delenv(key, raising=False)
    events, _ = scan(fx.data_dir)
    fetcher = fetcher_for(fx.data_dir)
    store = open_store(fx.data_dir)
    for eid, event in events.items():
        missing = [k for k in expected_from_record(event) if event.slice(k) is None or event.slice(k).state != "ok"]
        need = fetcher._needs_detail_fetch(str(eid))
        row = store.events.get(eid)
        if row is not None and row.stale:
            assert need == "refresh", (event.path, need)
            continue
        if row is not None and row.status_class == "live":
            assert need == "none", (event.path, need)
            continue
        assert (need == "refill") is bool(missing), (event.path, need, missing)
        if eid in COMBINED_ONLY.get(fx.name, set()):
            assert need == "none" and not missing  # RD-1'den önce bulunamıyordu ("full"); kayıt tam


# --- gözlem -------------------------------------------------------------------------------------


def test_observation_records(canonical: sf.LegacyFixture) -> None:
    events, _ = scan(canonical.data_dir)
    observed = events[16837335].observation
    assert observed.observed_at == dt.datetime(2026, 9, 19, 6, 0, tzinfo=UTC)
    assert observed.change_ts == events[16837335].event["changes"]["changeTimestamp"]
    assert observed.status_regressed is False and set(observed.raw) == {"observed_at_utc", "change_ts"}
    assert events[16867839].observation is None  # eski kayıt
    partial = events[14025001].observation  # observed_at_utc'siz dosya: gözlemsiz gibi işlenir
    assert (partial.observed_at, partial.change_ts, partial.status_regressed) == (None, 1779598000, False)
    assert events[17060394].observation.status_regressed is True


@pytest.mark.parametrize("value, expected", [
    ("2026-09-19T06:00:00+00:00", dt.datetime(2026, 9, 19, 6, 0, tzinfo=UTC)),
    ("2026-09-19T06:00:00Z", dt.datetime(2026, 9, 19, 6, 0, tzinfo=UTC)),
    ("2026-09-19T09:00:00+03:00", dt.datetime(2026, 9, 19, 6, 0, tzinfo=UTC)),
    ("2026-09-19T06:00:00", dt.datetime(2026, 9, 19, 6, 0, tzinfo=UTC)),  # saat dilimi yok: UTC
    ("yesterday", None), ("", None), (None, None), (1789790400, None),
])
def test_observation_time_parsing(tmp_path: Path, value: Any, expected: Optional[dt.datetime]) -> None:
    base = "match_details/7"
    write(tmp_path, f"{base}/basic.json", event_of(7))
    write(tmp_path, f"{base}/observation.json", {"observed_at_utc": value, "change_ts": "x", "status_regressed": 1})
    observation = scan(tmp_path)[0][7].observation
    assert observation.observed_at == expected and observation.change_ts is None
    assert observation.status_regressed is True
    if isinstance(value, str) and expected is not None and value.endswith(("Z", "+00:00", "+03:00")):
        assert refresh._parse_utc(value) == expected.timestamp()  # bugünkü yenileme kuralıyla aynı an


# --- L4: birleşik dosya ---------------------------------------------------------------------------


def test_combined_file_next_to_basic_json(old_forms: sf.LegacyFixture) -> None:
    """Olay yükü basic.json'dan, dilimler birleşik dosyadan; gözlem dosyası da okunur (RD-1'den beri yükleyici de okur)."""
    events, _ = scan(old_forms.data_dir)
    event = events[17099711]
    base = f"{PL_DIR}/17099711"
    assert event.dir.combined and event.dir.has_basic
    assert (event.slices[0].path, event.slices[0].in_combined) == (f"{base}/basic.json", False)
    assert counters(event) == ALL_OK
    assert {(s.path, s.in_combined, s.size) for s in event.slices[1:]} == {(f"{base}/17099711.json", True, None)}
    assert event.observation.observed_at == dt.datetime(2026, 9, 15, 13, 10, tzinfo=UTC)
    today = fetcher_for(old_forms.data_dir)._load_match_data_from_dir(str(old_forms.data_dir / base), "17099711")
    observation = today.pop("observation")  # RD-1: depodan okunur; eskiden gözlem okunmaz, kayıt hiç yenilenmezdi
    assert {("event" if k == "basic" else k): v for k, v in today.items()} == event.payloads
    assert observation["observed_at_utc"] == "2026-09-15T13:10:00+00:00"


def test_combined_file_alone_is_an_event_directory(old_forms: sf.LegacyFixture) -> None:
    events, _ = scan(old_forms.data_dir)
    event = events[17018554]
    assert (event.path, event.dir.has_basic, event.dir.combined) == ("match_details/17018554", False, True)
    assert {(s.path, s.in_combined) for s in event.slices} == {("match_details/17018554/17018554.json", True)}
    assert counters(event) == ALL_OK and event.event["id"] == 17018554
    # RD-1: yer katalogdan sorulur; dizini gezen eski arama bu kaydı bulamıyordu
    assert fetcher_for(old_forms.data_dir)._find_match_path("17018554") == (
        None, None, os.path.join(str(old_forms.data_dir), "match_details", "17018554"))


def test_slice_file_wins_over_the_combined_copy(tmp_path: Path) -> None:
    base = "match_details/7"
    event = sf.basic_payload(sf.PL_ARS)
    full = sf.slice_payload("statistics", event)
    write(tmp_path, f"{base}/basic.json", {**event_of(7), "copy": "file"})
    write(tmp_path, f"{base}/statistics.json", full)
    write(tmp_path, f"{base}/h2h.json", b"{")  # bozuk dosya: birleşik kopyaya düşülür, sorun bildirilir
    write(tmp_path, f"{base}/7.json", {
        "basic": {**event_of(7), "copy": "combined"}, "statistics": {"statistics": []},
        "h2h": sf.slice_payload("h2h", event), "lineups": None, "incidents": {"incidents": []},
        "observation": {"observed_at_utc": "2026-09-19T06:00:00+00:00", "change_ts": 5}, "odds": {"x": 1},
    })
    events, report = scan(tmp_path)
    event7 = events[7]
    assert event7.event["copy"] == "file" and event7.payloads["statistics"] == full
    assert {s.key: (s.state, s.in_combined) for s in event7.slices} == {
        "event": ("ok", False), "statistics": ("ok", False), "h2h": ("ok", True), "incidents": ("empty", True),
    }
    assert event7.observation.change_ts == 5  # observation.json yok: birleşik dosyadaki kullanılır
    assert event7.combined_extra_keys == ("odds",) and event7.extra_files == ()
    assert [(p.path, p.kind) for p in report.problems] == [(f"{base}/h2h.json", "corrupt")]


def test_broken_basic_json_falls_back_to_the_combined_copy(tmp_path: Path) -> None:
    base = "match_details/7"
    write(tmp_path, f"{base}/basic.json", b"{")
    write(tmp_path, f"{base}/7.json", {"basic": event_of(7)})
    events, report = scan(tmp_path)
    assert events[7].slices[0].in_combined and [(p.path, p.kind) for p in report.problems] == [
        (f"{base}/basic.json", "corrupt")]


def test_broken_combined_file_is_reported(tmp_path: Path) -> None:
    write(tmp_path, "match_details/7/basic.json", event_of(7))
    write(tmp_path, "match_details/7/7.json", b"[1, 2")
    write(tmp_path, "match_details/8/8.json", b"[1, 2]")
    write(tmp_path, "match_details/9/9.json", {"statistics": {}})
    events, report = scan(tmp_path)
    assert list(events) == [7] and events[7].slices[0].in_combined is False
    assert [(p.path, p.kind) for p in report.problems] == [
        ("match_details/7/7.json", "corrupt"), ("match_details/8", "corrupt"),
        ("match_details/9", "no_event_payload"),
    ]


def test_read_payload(old_forms: sf.LegacyFixture) -> None:
    reader = LegacyReader(old_forms.data_dir)
    events, _ = scan(old_forms.data_dir)
    for event in events.values():
        for key, payload in event.payloads.items():
            assert reader.read_payload(event.path, key) == payload
            assert json.loads(reader.read_payload(event.dir, key, raw=True)) == payload
        assert reader.read_payload(event.path) == event.event
        assert reader.read_payload(event.path, "odds") is None
    nested = f"{PL_DIR}/16837335"
    assert reader.read_payload(nested, raw=True) == (old_forms.data_dir / nested / "basic.json").read_bytes()
    combined = reader.read_payload("match_details/17018554", "statistics", raw=True)
    assert combined == codec.canonical_bytes(events[17018554].payloads["statistics"])
    assert reader.read_payload("match_details/1", "statistics") is None
    with pytest.raises(PayloadCorrupt):
        reader.read_payload(f"{PL_DIR}/17185003", "statistics")


# --- program ------------------------------------------------------------------------------------


@pytest.mark.parametrize("name, expected", [
    ("round_1.json", ("round", "round_1")), ("round_28_semifinals.json", ("round", "round_28_semifinals")),
    ("round_3_round-of-16.json", ("round", "round_3_round-of-16")), ("round_1_full.json", ("round", "round_1_full")),
    ("events_last_0.json", ("page", "last_0")), ("events_next_12.json", ("page", "next_12")),
    ("round_x.json", None), ("round_.json", None), ("events_prev_0.json", None), ("events_last_x.json", None),
    ("round_1.csv", None), ("round_1_matches.csv", None), ("summary.json", None),
    ("round_1_çeyrek.json", None), (f"round_1_{'x' * 80}.json", None),
    # slug'ında büyük harf olan tur dosyası: alt anahtar küçük harfe katlanır (v3 alt anahtarları küçük harftir)
    ("round_1_Final.json", ("round", "round_1_final")), ("round_29_FINAL.json", ("round", "round_29_final")),
    ("round_3_Round-of-16.json", ("round", "round_3_round-of-16")),
    ("round_2_Qualification.Round_1.json", ("round", "round_2_qualification.round_1")),
    # katlanan yalnızca slug'dır: önekler ve uzantı yazıcının yazdığı gibi küçük harf olmalı
    ("Round_1.json", None), ("ROUND_1_final.json", None), ("round_1_Final.JSON", None),
    ("Events_last_0.json", None), ("events_Last_0.json", None),
    ("round_1_Çeyrek.json", None), (f"round_1_{'X' * 80}.json", None), (f"round_1_{'X' * 72}.json",
                                                                        ("round", f"round_1_{'x' * 72}")),
])
def test_schedule_sub(name: str, expected: Optional[Tuple[str, str]]) -> None:
    assert legacy.schedule_sub(name) == expected
    if expected is not None:
        assert expected[1] == expected[1].lower() == layout.validate_sub(expected[1])


def test_round_file_with_an_upper_case_slug_is_a_schedule_page(tmp_path: Path) -> None:
    """
    FX-4'ten beri v3 alt anahtarları küçük harftir; `round_29_Final.json` o günden beri program sayfası
    sayılmıyor, `unknown_name` olarak bildiriliyordu. Alt anahtar katlanır, dosyanın adı ve yolu aynı kalır.
    """
    season = "matches/19_FA_Cup/97110_FA_Cup_26_27"
    write(tmp_path, f"{season}/round_28_semifinals.json", {"events": [{"id": 1}], "_complete": True}, sf.BASE_MTIME)
    write(tmp_path, f"{season}/round_29_Final.json", {"events": [{"id": 2}], "_complete": True}, sf.BASE_MTIME + 1)
    write(tmp_path, f"{season}/Round_30.json", {"events": [{"id": 3}]}, sf.BASE_MTIME + 2)
    reader = LegacyReader(tmp_path)
    report = LegacyReport()
    pages = reader.schedule_pages(report)

    assert [(p.path, p.kind, p.sub, p.superseded_by) for p in pages] == [
        (f"{season}/round_28_semifinals.json", "round", "round_28_semifinals", None),
        (f"{season}/round_29_Final.json", "round", "round_29_final", None),
    ]
    schedule = reader.read_schedule(pages[1])
    assert (schedule.payload, schedule.meta) == ({"events": [{"id": 2}]}, {"complete": True})
    assert reader.signature(pages[1].path) is not None and (tmp_path / season / "round_29_Final.json").is_file()
    assert [(p.path, p.kind) for p in report.problems] == [(f"{season}/Round_30.json", "unknown_name")]
    assert report.superseded == []


def test_round_files_that_differ_only_in_case_are_one_page(tmp_path: Path) -> None:
    """Aynı dizinde `round_1_Final.json` ve `round_1_final.json`: tek sayfa, en yenisi geçerli."""
    season = "matches/19_FA_Cup/97110_FA_Cup_26_27"
    write(tmp_path, f"{season}/round_1_final.json", {"events": [{"id": 1}]}, sf.BASE_MTIME)
    write(tmp_path, f"{season}/round_1_Final.json", {"events": [{"id": 2}]}, sf.BASE_MTIME + 5)
    if len(os.listdir(tmp_path / season)) != 2:
        pytest.skip("dosya sistemi büyük/küçük harf ayırmıyor: iki ad tek dosya")
    reader = LegacyReader(tmp_path)
    report = LegacyReport()
    pages = reader.schedule_pages(report)

    assert [(p.path, p.sub, p.superseded_by) for p in pages] == [
        (f"{season}/round_1_final.json", "round_1_final", f"{season}/round_1_Final.json"),
        (f"{season}/round_1_Final.json", "round_1_final", None),
    ]
    assert [(s.kind, s.key, s.path, s.winner) for s in report.superseded] == [
        ("schedule", "19/97110/round_1_final", f"{season}/round_1_final.json", f"{season}/round_1_Final.json")]
    assert report.problems == []


def test_catalog_picks_up_the_folded_round_page_and_the_new_slice_rule(tmp_path: Path) -> None:
    """
    Dizinleyici değişmeden: büyük harfli tur dosyası katalogda bir program dilimidir ve listelediği maç bir
    olay satırıdır; boş `pointByPoint` listesi `empty` dilimdir. Eski türetme sürümüyle kurulmuş katalog
    açılışta bir kez yeniden kurulur.
    """
    data = tmp_path / "data"
    season = "matches/19_FA_Cup/97110_FA_Cup_26_27"
    listed = sf.basic_payload(sf.PL_ARS)
    write(data, f"{season}/round_29_Final.json", {"events": [listed], "_complete": True})
    write(data, "match_details/7/basic.json", event_of(7, "tennis"))
    write(data, "match_details/7/point_by_point.json", {"pointByPoint": []})
    final = Ref.season(19, 97110)

    def read(store: Store) -> Tuple[Any, ...]:
        page = store.entities.slice(final, "schedule", "round_29_final")
        points = store.events.slice(7, "point_by_point")
        return (page.state, page.has_payload, store.entities.payload(final, "schedule", "round_29_final"),
                store.events.get(listed["id"]) is not None, points.state, points.has_payload)

    expected = ("ok", True, {"events": [listed]}, True, "empty", True)
    store = open_store(data)
    try:
        assert read(store) == expected
        assert [s.sub for s in store.entities.slices(final)] == ["round_29_final"]
        assert store.info(sizes=False).derive_version == derive.DERIVE_VERSION == 2
    finally:
        store.close()

    # Eski kuralların kurduğu katalog: sayfa yok, dilim `ok`, sürüm 1
    with sqlite3.connect(catalog.catalog_path(data)) as conn:
        conn.execute("DELETE FROM entity_slices WHERE sub = 'round_29_final'")
        conn.execute("DELETE FROM events WHERE id = ?", (listed["id"],))
        conn.execute("UPDATE event_slices SET state = 'ok' WHERE key = 'point_by_point'")
        conn.execute("UPDATE meta SET value = '1' WHERE key = 'derive_version'")
    conn.close()
    stale = open_store(data, sync_catalog=False)
    try:
        assert stale.info(sizes=False).catalog_rebuild_reason == "derive_version"
    finally:
        stale.close()
    store = open_store(data)
    try:
        assert read(store) == expected
        assert store.info(sizes=False).catalog_rebuild_reason is None
    finally:
        store.close()


def test_schedule_pages_of_the_canonical_fixture(canonical: sf.LegacyFixture) -> None:
    reader = LegacyReader(canonical.data_dir)
    report = LegacyReport()
    pages = reader.schedule_pages(report)
    assert report == LegacyReport() and all(p.superseded_by is None for p in pages)
    first = canonical.data_dir / pages[0].path
    assert reader.signature(pages[0].path) == f"{first.stat().st_mtime_ns}:{first.stat().st_size}"
    assert [p.path for p in pages] == sorted(p.path for p in pages)  # mtime eşit: yola göre
    by_season: Dict[Tuple[int, int], Dict[str, Tuple[str, Dict[str, Any], List[int]]]] = {}
    for page in pages:
        schedule = reader.read_schedule(page)
        assert "_complete" not in schedule.payload and schedule.page is page
        assert page.fetched_at == float(sf.BASE_MTIME) and page.size == os.path.getsize(reader.resolve(page.path))
        by_season.setdefault((page.tournament_id, page.season_id), {})[page.sub] = (
            page.kind, dict(schedule.meta), [e["id"] for e in schedule.events])
    filtered = {"filtered": True}
    assert by_season == {
        (17, 96668): {
            "round_1": ("round", {"complete": True}, [16837335, 17099711, 16867839, 17018554]),
            "round_2": ("round", {"complete": False}, [17185003, 17018572, 17184988, 16872361, 16951514, 16837399,
                                                        17184998, 16539815]),
            "round_3": ("round", {"complete": False}, [17211671]),
        },
        (17, 76986): {"round_38": ("round", {"complete": True}, [14025001, 14025002])},
        (19, 97110): {"round_28_semifinals": ("round", {"complete": True}, [16950622, 17090707]),
                      "round_29_final": ("round", {"complete": True}, [17148332])},
        (132, 80229): {"last_0": ("page", filtered, [16484334, 17092269, 16346148]),
                       "last_1": ("page", filtered, [17102381, 17203939, 17060394])},
        (2361, 79116): {"last_0": ("page", filtered, [17204710, 17206241, 17081861, 17058663, 17078471, 17207542])},
        # FETCH_ONLY_FINISHED=false ile yazılmış sayfalar da dosyadan ayırt edilemez: "filtered" alır
        (8, 97532): {"last_0": ("page", filtered, [16990001, 16599919, 17148292]),
                     "next_0": ("page", filtered, [17211681, 16425949])},
    }
    listed = {eid for season in by_season.values() for _, _, ids in season.values() for eid in ids}
    assert listed >= set(canonical.listed_ids)


def test_schedule_pages_of_the_legacy_fixture(old_forms: sf.LegacyFixture) -> None:
    reader = LegacyReader(old_forms.data_dir)
    report = LegacyReport()
    pages = {p.path: p for p in reader.schedule_pages(report)}
    base = "matches/17_Premier_League"
    assert {path: (p.sub, p.superseded_by) for path, p in pages.items()} == {
        f"{base}/96668_Premier_League_26_27/round_1.json": ("round_1", None),
        f"{base}/96668_Premier_League_26_27/round_2.json": ("round_2", None),
        # aynı sezonun ikinci dizini: mtime eşit olduğundan yolu küçük olan geçerli
        f"{base}/96668_Season_96668/round_1.json": ("round_1", f"{base}/96668_Premier_League_26_27/round_1.json"),
        # ilk sürümün dosyası tur dosyası kalıbına uyar: "full" adlı kupa turu gibi okunur
        "matches/8_LaLiga/77559_LaLiga_25_26/round_1_full.json": ("round_1_full", None),
    }
    assert [(s.kind, s.key, s.path) for s in report.superseded] == [
        ("schedule", "17/96668/round_1", f"{base}/96668_Season_96668/round_1.json")]
    assert report.problems == []
    old = reader.read_schedule(pages[f"{base}/96668_Premier_League_26_27/round_1.json"])
    assert old.meta == {"filtered": True}  # `_complete` anahtarı olmayan eski tur dosyası
    assert reader.read_schedule(pages[f"{base}/96668_Season_96668/round_1.json"]).meta == {"complete": True}
    assert reader.read_schedule(pages["matches/8_LaLiga/77559_LaLiga_25_26/round_1_full.json"]).meta == {
        "filtered": True}


def test_schedule_duplicates_newest_wins_and_order_is_by_mtime(tmp_path: Path) -> None:
    write(tmp_path, "matches/17_PL/1_New_Name/round_1.json", {"events": [], "_complete": 0}, sf.BASE_MTIME)
    write(tmp_path, "matches/17_PL/1_Old_Name/round_1.json", {"events": [{"id": 1}]}, sf.BASE_MTIME - 50)
    write(tmp_path, "matches/17_PL/1_Old_Name/events_last_0.json", {"events": []}, sf.BASE_MTIME - 99)
    write(tmp_path, "matches/17_PL/1_Old_Name/notes.json", {}, sf.BASE_MTIME)
    write(tmp_path, "matches/17_PL/1_Old_Name/broken.txt", b"x")
    write(tmp_path, "matches/17_PL/1_Old_Name/round_2.json", b"[]", sf.BASE_MTIME + 1)
    write(tmp_path, "matches/17_PL/1_Old_Name/round_3.json", b"{", sf.BASE_MTIME + 2)
    write(tmp_path, "matches/17_PL/Season_Without_Id/round_1.json", {"events": []})
    write(tmp_path, "matches/Old_League/1_x/round_1.json", {"events": []})
    write(tmp_path, "matches/readme.txt", b"x")
    reader = LegacyReader(tmp_path)
    report = LegacyReport()
    pages = reader.schedule_pages(report)
    assert [(p.path.split("/", 2)[2], p.sub, p.superseded_by is not None) for p in pages] == [
        ("1_Old_Name/events_last_0.json", "last_0", False), ("1_Old_Name/round_1.json", "round_1", True),
        ("1_New_Name/round_1.json", "round_1", False), ("1_Old_Name/round_2.json", "round_2", False),
        ("1_Old_Name/round_3.json", "round_3", False),
    ]
    assert reader.read_schedule(pages[2]).meta == {"complete": False}
    assert reader.summary_files() == []
    assert [(p.path, p.kind) for p in report.problems] == [
        ("matches/17_PL/1_Old_Name/notes.json", "unknown_name"),
        ("matches/17_PL/Season_Without_Id", "unknown_name"), ("matches/Old_League", "unknown_name"),
    ]
    for broken in pages[3:]:
        with pytest.raises(PayloadCorrupt):
            reader.read_schedule(broken)


# --- özetler ------------------------------------------------------------------------------------


def test_summary_files(fx: sf.LegacyFixture) -> None:
    reader = LegacyReader(fx.data_dir)
    summaries = reader.summary_files()
    csv_paths = [s.path for s in summaries if s.kind in ("summary_csv", "matches_csv") and not s.nested]
    assert sorted(csv_paths) == sorted(fx.summary_files)
    assert [s.path for s in summaries] == sorted(s.path for s in summaries)
    # Aynı dosyalar: `_season_summary_files` (lig dizini başına), sezon id'si olanlar
    today: Set[str] = set()
    matches_dir = fx.data_dir / "matches"
    for league in sorted(matches_dir.iterdir()) if matches_dir.is_dir() else []:
        for path in MatchDataFetcher._season_summary_files(str(league), None, 0):
            today.add(Path(path).relative_to(fx.data_dir).as_posix())
    assert {s.path for s in summaries if s.path.endswith(".csv") and s.season_id is not None} == today
    for summary in summaries:
        if summary.kind == "summary_json":
            assert isinstance(reader.read_summary_json(summary), list)
            with pytest.raises(LayoutError):
                reader.read_summary_rows(summary)
            continue
        with open(reader.resolve(summary.path), newline="", encoding="utf-8") as f:
            assert reader.read_summary_rows(summary) == list(csv.DictReader(f))


def test_summary_files_of_the_legacy_fixture(old_forms: sf.LegacyFixture) -> None:
    reader = LegacyReader(old_forms.data_dir)
    summaries = {s.path: s for s in reader.summary_files()}
    assert {path: (s.tournament_id, s.season_id, s.kind, s.nested) for path, s in summaries.items()} == {
        "matches/17_Premier_League/76986_Premier_League_25_26_matches.csv": (17, 76986, "matches_csv", False),
        "matches/17_Premier_League/96668_Premier_League_26_27_summary.csv": (17, 96668, "summary_csv", False),
        "matches/17_Premier_League/96668_Premier_League_26_27_summary.json": (17, 96668, "summary_json", False),
        "matches/17_Premier_League/96668_Season_96668_summary.csv": (17, 96668, "summary_csv", False),
        "matches/17_Premier_League/96668_Season_96668_summary.json": (17, 96668, "summary_json", False),
        "matches/8_LaLiga/77559_LaLiga_25_26/round_1_matches.csv": (8, 77559, "matches_csv", True),
        "matches/8_LaLiga/77559_LaLiga_25_26_summary.csv": (8, 77559, "summary_csv", False),
        "matches/8_LaLiga/77559_LaLiga_25_26_summary.json": (8, 77559, "summary_json", False),
    }
    # Tur / sayfa dosyası olmayan sezon (yalnızca eski `_matches.csv`): katalog bu satırlardan kurulur
    with_pages = {(p.tournament_id, p.season_id) for p in reader.schedule_pages()}
    only_csv = {(s.tournament_id, s.season_id) for s in summaries.values()} - with_pages
    assert only_csv == {(17, 76986)}
    rows = reader.read_summary_rows(summaries["matches/17_Premier_League/76986_Premier_League_25_26_matches.csv"])
    assert [row["match_id"] for row in rows] == ["14025001", "14025002"] and list(rows[0]) == sf.SUMMARY_COLUMNS
    nested = reader.read_summary_rows(summaries["matches/8_LaLiga/77559_LaLiga_25_26/round_1_matches.csv"])
    assert list(nested[0]) == sf.OLD_ROUND_CSV_COLUMNS and nested[0]["home_team"] == "Atlético Madrid"


# --- sezon listeleri ----------------------------------------------------------------------------


def test_season_lists_of_the_canonical_fixture(canonical: sf.LegacyFixture) -> None:
    lists = LegacyReader(canonical.data_dir).season_lists()
    assert [(s.tournament_id, s.label, s.kind, s.superseded_by, [x["id"] for x in s.seasons]) for s in lists] == [
        (132, "NBA", "json", None, [80229, 65360]),
        (17, "Premier_League", "json", None, [96668, 76986, 61627]),
        (19, "FA_Cup", "json", None, [97110, 77210]),
        (2361, "Wimbledon,_Men", "json", None, [79116, 63966]),
        (8, "LaLiga", "json", None, [97532, 77559]),
    ]
    assert all(s.fetched_at == float(sf.BASE_MTIME) for s in lists)
    assert lists[1].payload == json.loads((canonical.data_dir / lists[1].path).read_bytes())


def test_season_list_rule_newest_file_of_any_name(old_forms: sf.LegacyFixture) -> None:
    """
    Premier League'in iki dosyası var; adı ne olursa olsun yenisi geçerli. RD-5'ten beri okuyucular da
    (web uç noktası, SeasonFetcher) aynı dosyayı seçer; eskiden web okuyucusu yalın `17_seasons.json`'ı
    seçiyordu (30 gün eski, iki sezon).
    """
    reader = LegacyReader(old_forms.data_dir)
    report = LegacyReport()
    lists = reader.season_lists(old_forms.leagues, report)
    newest = "seasons/17_Premier_League_seasons.json"
    assert [(s.tournament_id, s.label, s.kind, s.path, s.superseded_by) for s in lists] == [
        (8, "LaLiga", "csv", "league_seasons.csv", "seasons/LaLiga_seasons.json"),
        (17, "Premier_League", "csv", "league_seasons.csv", newest),
        (17, "Premier_League", "json", newest, None),
        (17, "", "json", "seasons/17_seasons.json", newest),
        (2361, "Wimbledon, Men", "json", "seasons/2361_Wimbledon, Men_seasons.json", None),
        (8, "LaLiga", "json", "seasons/LaLiga_seasons.json", None),
    ]
    assert [x["id"] for x in lists[2].seasons] == [96668, 76986, 61627]
    assert lists[0].payload == {"seasons": [{"id": 77559, "name": "LaLiga 25/26", "year": "25/26"}]}
    assert [(s.kind, s.key, s.path, s.winner) for s in report.superseded] == [
        ("season_list", "8", "league_seasons.csv", "seasons/LaLiga_seasons.json"),
        ("season_list", "17", "league_seasons.csv", newest),
        ("season_list", "17", "seasons/17_seasons.json", newest),
    ]
    assert report.problems == []
    from src.services import tournaments
    from src.store import open_store
    today = tournaments.seasons_of(open_store(old_forms.data_dir), 17)
    assert [x["id"] for x in today] == [96668, 76986, 61627]

    # Lig adları verilmezse `<ad>_seasons.json`'ın turnuvası bilinmez; CSV o turnuva için geçerli kalır
    report = LegacyReport()
    unnamed = {(s.path, s.tournament_id): s.superseded_by for s in reader.season_lists(report=report)}
    assert unnamed[("seasons/LaLiga_seasons.json", None)] is None and unnamed[("league_seasons.csv", 8)] is None
    assert [(p.path, p.kind) for p in report.problems] == [("seasons/LaLiga_seasons.json", "unresolved_tournament")]


def test_season_list_newest_wins_whatever_the_name(tmp_path: Path) -> None:
    write(tmp_path, "seasons/17_seasons.json", {"seasons": [{"id": 1}]}, sf.BASE_MTIME)
    write(tmp_path, "seasons/17_Premier_League_seasons.json", {"seasons": [{"id": 2}]}, sf.BASE_MTIME - 1)
    write(tmp_path, "seasons/Premier League_seasons.json", {"seasons": [{"id": 3}]}, sf.BASE_MTIME - 2)
    write(tmp_path, "seasons/8_A_seasons.json", {"seasons": []}, sf.BASE_MTIME)
    write(tmp_path, "seasons/8_B_seasons.json", {"seasons": "x"}, sf.BASE_MTIME)  # eşitlikte yolu küçük olan
    write(tmp_path, "seasons/9_seasons.json", b"{")
    write(tmp_path, "seasons/10_seasons.json", [1])
    write(tmp_path, "seasons/notes.txt", b"x")
    write(tmp_path, "league_seasons.csv", "Liga Adı,Lig ID,Sezon ID,Sezon Adı,Sezon Yılı\r\nX,x,1,A,1\r\n"
          "Y,20,5,S,2020\r\n".encode("utf-8"))
    report = LegacyReport()
    lists = LegacyReader(tmp_path).season_lists({17: "Premier League"}, report)
    assert {s.path: s.superseded_by for s in lists if s.tournament_id == 17} == {
        "seasons/17_seasons.json": None, "seasons/17_Premier_League_seasons.json": "seasons/17_seasons.json",
        "seasons/Premier League_seasons.json": "seasons/17_seasons.json",
    }
    assert {s.path: s.superseded_by for s in lists if s.tournament_id == 8} == {
        "seasons/8_A_seasons.json": None, "seasons/8_B_seasons.json": "seasons/8_A_seasons.json"}
    assert [s.seasons for s in lists if s.path == "seasons/8_B_seasons.json"] == [[]]
    assert [(s.tournament_id, s.label, s.seasons) for s in lists if s.kind == "csv"] == [
        (20, "Y", [{"id": 5, "name": "S", "year": "2020"}])]
    assert [(p.path, p.kind) for p in report.problems] == [
        ("seasons/10_seasons.json", "corrupt"), ("seasons/9_seasons.json", "corrupt"),
        ("league_seasons.csv", "malformed"),
    ]


def test_csv_files_that_are_not_utf8(tmp_path: Path) -> None:
    write(tmp_path, "league_seasons.csv", b"Liga Ad\xfd,Lig ID,Sezon ID\r\n")  # Latin-5 ile yazılmış başlık
    write(tmp_path, "matches/17_PL/1_x_summary.csv", b"match_id,home_team\r\n1,Be\xfeikta\xfe\r\n")
    write(tmp_path, "matches/17_PL/2_x_summary.csv", "\ufeffmatch_id,home_team\r\n2,Beşiktaş\r\n".encode("utf-8"))
    reader = LegacyReader(tmp_path)
    report = LegacyReport()
    assert reader.season_lists(report=report) == []
    assert [(p.path, p.kind) for p in report.problems] == [("league_seasons.csv", "corrupt")]
    broken, with_bom = reader.summary_files()
    with pytest.raises(PayloadCorrupt):
        reader.read_summary_rows(broken)
    assert reader.read_summary_rows(with_bom) == [{"match_id": "2", "home_team": "Beşiktaş"}]


# --- değişiklik günlüğü ve izleyici dosyaları ----------------------------------------------------


def test_change_log_and_watcher_files_of_the_canonical_fixture(canonical: sf.LegacyFixture) -> None:
    reader = LegacyReader(canonical.data_dir)
    report = LegacyReport()
    changes = reader.change_log(report)
    assert [(c.seq, c.row) for c in changes] == [(1, sf.SCORE_CHANGES[0]), (2, sf.SCORE_CHANGES[1])]
    raw = (canonical.data_dir / "score_changes.jsonl").read_text(encoding="utf-8").splitlines()
    assert [c.line for c in changes] == raw
    assert [(e.seq, e.row) for e in reader.watch_events(report)] == [(1, sf.WATCH_EVENTS[0]), (2, sf.WATCH_EVENTS[1])]
    states = reader.watch_states(report)
    assert [(s.sport, s.path, s.state) for s in states] == [
        ("football", "watch_state_football.json", sf.WATCH_STATE["football"]),
        ("tennis", "watch_state_tennis.json", sf.WATCH_STATE["tennis"]),
    ]
    assert report == LegacyReport()


def test_change_log_sequence_is_the_line_number(tmp_path: Path) -> None:
    lines = [b'{"event_id": 1}', b"", b"not json", b'{"event_id": 4}\r', b"[5]", b"   ",
             b'{"event_id": 7, "ad": "\xc3\xa7"}', b"\xff\xfe", b'{"event_id": 9}']
    write(tmp_path, "score_changes.jsonl", b"\n".join(lines) + b"\n" + b'{"event_id": 10')
    reader = LegacyReader(tmp_path)
    report = LegacyReport()
    changes = reader.change_log(report)
    assert [(c.seq, c.row) for c in changes] == [
        (1, {"event_id": 1}), (4, {"event_id": 4}), (7, {"event_id": 7, "ad": "ç"}), (9, {"event_id": 9})]
    assert changes[1].line == '{"event_id": 4}'
    assert [(p.kind, p.detail.split(":")[0]) for p in report.problems] == [
        ("torn_line", "satır 10"), ("corrupt", "satır 3"), ("malformed", "satır 5"), ("corrupt", "satır 8")]
    assert all(p.path == "score_changes.jsonl" for p in report.problems)
    # Dosyanın sonuna yeni satır eklenince eski satırların numarası değişmez
    with open(tmp_path / "score_changes.jsonl", "ab") as f:
        f.write(b'}\n{"event_id": 11}\n')
    assert [(c.seq, c.row["event_id"]) for c in reader.change_log()] == [
        (1, 1), (4, 4), (7, 7), (9, 9), (10, 10), (11, 11)]


def test_watch_state_files(tmp_path: Path) -> None:
    write(tmp_path, "watch_state_table-tennis.json", {"1": {"class": "live"}})
    write(tmp_path, "watch_state_football.json", b"{")
    write(tmp_path, "watch_state_tennis.json", [1])
    write(tmp_path, "watch_state_.json", {})
    write(tmp_path, "watch_state_x.json/inner.txt", b"x")
    report = LegacyReport()
    states = LegacyReader(tmp_path).watch_states(report)
    assert [(s.sport, s.state) for s in states] == [("table-tennis", {"1": {"class": "live"}})]
    assert [(p.path, p.kind) for p in report.problems] == [
        ("watch_state_football.json", "corrupt"), ("watch_state_tennis.json", "corrupt")]


# --- bugünkü gezginlerle karşılaştırma (01-storage.md, bölüm 1.2) -------------------------------

def _csv_export_ids(fetcher: MatchDataFetcher) -> List[str]:
    path = fetcher.create_csv_dataset()
    if not path:
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [row["match_id"] for row in csv.DictReader(f)]


def walker_results(data_dir: Path, candidates: Set[int]) -> Dict[str, Set[int]]:
    """
    Bugünkü her ağaç gezgininin bulduğu maç id'leri (hepsi sayı; sayı olmayan ad gelirse test düşer).

    `refresh_due_ids` ve web'in eksik detayları RD-3'ten beri ağacı gezmez, katalogdan okur; tabloda değiller.
    `reset_unavailable_markers` da ST-21'den beri Store'a sorar (`Store.events.reset_empty_markers`).
    """
    fetcher = fetcher_for(data_dir)

    def ids(names: Any) -> Set[int]:
        return {int(name) for name in names}

    return {
        "build_match_index": ids(fetcher._build_match_index()),
        "find_match_path": {eid for eid in candidates if fetcher._find_match_path(str(eid))},
        "csv_export": ids(_csv_export_ids(fetcher)),
    }


WALKERS = ("build_match_index", "find_match_path", "csv_export")

# Karakterizasyon tablosu: (fixture, gezgin) → (okuyucunun bulup gezginin bulamadığı, gezginin bulup
# okuyucunun maç saymadığı) id'ler. Tabloda olmayan çift için fark yoktur: `canonical`, `processed_only` ve
# `empty` dizinlerinde bütün gezginler okuyucuyla aynı kümeyi bulur.
WALKER_DIFFERENCES: Dict[Tuple[str, str], Tuple[Set[int], Set[int]]] = {
    # Boş: `build_match_index` ve `find_match_path` RD-1'den, CSV dışa aktarma EX-1'den beri depodan okur ve
    # okuyucuyla aynı kümeyi bulur (yalnızca birleşik dosyası olan dizin de dahil)
}


def reader_ids_for(walker: str, events: Dict[int, LegacyEvent]) -> Set[int]:
    return set(events)


def test_walker_characterization_table(fx: sf.LegacyFixture) -> None:
    events, _ = scan(fx.data_dir)
    names = {int(p.name) for p in (fx.data_dir / "match_details").rglob("*") if p.is_dir() and p.name.isdigit()} \
        if (fx.data_dir / "match_details").is_dir() else set()
    results = walker_results(fx.data_dir, set(events) | names | {1})
    assert tuple(results) == WALKERS
    actual = {}
    for walker, found in results.items():
        mine = reader_ids_for(walker, events)
        if mine != found:
            actual[(fx.name, walker)] = (mine - found, found - mine)
    expected = {key: value for key, value in WALKER_DIFFERENCES.items() if key[0] == fx.name}
    assert actual == expected


def test_csv_export_lists_every_event_once(old_forms: sf.LegacyFixture) -> None:
    """Altın dosyalardaki 9. tuhaflık EX-1'de gitti: dışa aktarma düz dizinleri artık iki kez yazmaz."""
    exported = _csv_export_ids(fetcher_for(old_forms.data_dir))
    assert len(exported) == len(set(exported))
    assert exported.count("16837335") == 1 and exported.count("16867839") == 1


def test_counting_walkers(fx: sf.LegacyFixture, capsys: pytest.CaptureFixture[str]) -> None:
    """
    Yalnızca sayı veren iki okuyucu. İstatistikler katalogdan gelir (RD-4): okuyucunun bulduğu her maç bir kez
    sayılır. Dosya raporu hâlâ ağacı gezer ve yalnızca `season_*` dizinlerine bakar.
    """
    events, _ = scan(fx.data_dir)
    in_season_dirs = {eid for eid, e in events.items() if (e.dir.season_dir or "").startswith("season_")}
    system = stats_service.system_stats(str(fx.data_dir), fx.leagues)
    assert system["details"] == len(events)
    report = fetcher_for(fx.data_dir).generate_file_report()
    capsys.readouterr()
    # dosya raporu basic.json'a da bakmaz: olay yükü olmayan dizini de sayar
    without_payload = NO_EVENT_PAYLOAD.get(fx.name, set())
    assert report["overall_stats"]["total_matches"] == len(in_season_dirs) + len(without_payload)


# --- yalnızca okur; katman kuralı -----------------------------------------------------------------


def exercise_every_reader(data_dir: Path, leagues: Dict[int, str]) -> None:
    reader = LegacyReader(data_dir)
    report = LegacyReport()
    reader.has_data()
    for directory in reader.event_dirs(report):
        reader.signature(directory.path)
        reader.event_dir_at(directory.path)
    for event in reader.iter_events(report=report):
        for key in event.payloads or {}:
            reader.read_payload(event.path, key, raw=True)
    for page in reader.schedule_pages(report):
        reader.read_schedule(page)
    for summary in reader.summary_files():
        if summary.path.endswith(".csv"):
            reader.read_summary_rows(summary)
        else:
            reader.read_summary_json(summary)
    reader.season_lists(leagues, report)
    reader.change_log(report)
    reader.watch_events(report)
    reader.watch_states(report)
    store_dump.dump(data_dir, leagues)


def test_reading_changes_nothing_on_disk(fx: sf.LegacyFixture) -> None:
    before = tree_state(fx.data_dir)
    exercise_every_reader(fx.data_dir, fx.leagues)
    assert tree_state(fx.data_dir) == before
    if fx.name == "empty":
        assert before == {}  # boş dizinde `.meta` ya da başka bir şey oluşturulmaz


def _everything(data_dir: Path, leagues: Dict[int, str]) -> List[Any]:
    reader = LegacyReader(data_dir)
    report = LegacyReport()
    return [
        list(reader.iter_events(report=report)), reader.event_dirs(report), reader.schedule_pages(report),
        reader.summary_files(report), reader.season_lists(leagues, report), reader.change_log(report),
        reader.watch_states(report), report, store_dump.dump(data_dir, leagues),
    ]


@pytest.mark.parametrize("order", ["reversed", "by_length"])
def test_results_do_not_depend_on_directory_listing_order(fx: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch,
                                                           order: str) -> None:
    """Dizin listeleme sırası dosya sistemine göre değişir; okuyucu her düzeyde ada göre sıralar."""
    expected = _everything(fx.data_dir, fx.leagues)
    real_scandir = os.scandir

    class Shuffled:
        def __init__(self, path: Any) -> None:
            with real_scandir(path) as scan:
                entries = sorted(scan, key=lambda e: e.name)
            self.entries = entries[::-1] if order == "reversed" else sorted(entries, key=lambda e: -len(e.name))

        def __enter__(self) -> Any:
            return iter(self.entries)

        def __exit__(self, *exc: Any) -> bool:
            return False

        def __iter__(self) -> Any:
            return iter(self.entries)

        def close(self) -> None:
            pass

    monkeypatch.setattr(os, "scandir", Shuffled)
    assert _everything(fx.data_dir, fx.leagues) == expected


WRITING_CALLS = {
    "open", "write_bytes", "write_text", "write_payload", "atomic_write_bytes", "atomic_write_text",
    "atomic_write_json", "replace", "rename", "renames", "remove", "unlink", "rmdir", "removedirs", "rmtree",
    "remove_tree", "mkdir", "makedirs", "utime", "chmod", "truncate", "touch", "move", "copy", "copy2", "copyfile",
    "copytree", "move_to_trash", "publish_dir", "new_staging_dir", "purge_staging", "purge_trash", "symlink", "link",
    "mkstemp", "mkdtemp", "NamedTemporaryFile", "TemporaryDirectory",
}


def test_module_has_no_writing_call() -> None:
    """Statik denetim: modülde dosya sistemini değiştiren (ya da `open` ile dosya açan) hiçbir çağrı yok."""
    tree = ast.parse(LEGACY_SOURCE.read_text(encoding="utf-8"))
    bare: Set[str] = set()
    owned: Set[Tuple[str, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            bare.add(node.func.id)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            owned.add((owner.id if isinstance(owner, ast.Name) else "", node.func.attr))
    # `replace` adıyla yalnızca dataclasses.replace (çıplak ad) ve str.replace çağrılır
    assert not bare & (WRITING_CALLS - {"replace"}), sorted(bare & WRITING_CALLS)
    writing = {(owner, attr) for owner, attr in owned if attr in WRITING_CALLS - {"replace"}}
    writing |= {(owner, attr) for owner, attr in owned if attr == "replace" and owner in ("os", "files", "shutil")}
    assert not writing, sorted(writing)
    assert {owner for owner, _ in owned} >= {"os", "codec", "files"}  # denetim gerçekten çağrıları görüyor
    assert {attr for owner, attr in owned if owner == "os"} <= {"scandir", "stat", "fspath"}
    assert {attr for owner, attr in owned if owner == "files"} == {"read_bytes"}
    assert {attr for owner, attr in owned if owner == "codec"} <= {"read_payload", "read_raw", "canonical_bytes"}


def test_module_imports_only_what_the_store_may_import() -> None:
    """Katman kuralı (01-storage.md 2.1): Store yalnızca sports, status, slices, exceptions, version'ı içe aktarır."""
    allowed = {"src.sports", "src.status", "src.slices", "src.exceptions", "src.version"}
    tree = ast.parse(LEGACY_SOURCE.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            imported.add(node.module or "")
    assert not imported & {"shutil", "tempfile", "sqlite3", "pathlib", "glob"}
    from_src = {name for name in imported if name == "src" or name.startswith("src.")}
    assert {name for name in from_src if not name.startswith("src.store")} <= allowed
    assert from_src >= {"src.slices", "src.sports", "src.store", "src.store.errors"}


def test_import_is_light() -> None:
    """Modül pandas, günlükçü ya da istek katmanını yüklemez; `src.store` kökü de onu dışa açmaz."""
    code = (
        "import sys, src.store, src.store.legacy\n"
        "heavy = [m for m in ('pandas', 'tqdm', 'rich', 'dotenv', 'curl_cffi', 'src.logger', 'src.utils',"
        " 'src.match_data_fetcher', 'src.config_manager') if m in sys.modules]\n"
        "assert not heavy, heavy\n"
        "assert not any(name.startswith('Legacy') for name in src.store.__all__)\n"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


# --- mantıksal döküm ----------------------------------------------------------------------------


def test_dump_of_the_canonical_fixture(canonical: sf.LegacyFixture) -> None:
    dump = store_dump.dump(canonical.data_dir)
    assert list(dump) == ["events", "schedules", "season_lists", "changes"]
    assert sorted(map(int, dump["events"])) == canonical.detail_ids and len(dump["events"]) == 23
    assert json.loads(json.dumps(dump)) == dump  # yalnızca JSON türleri
    cry = dump["events"]["16951514"]
    basic = json.loads((canonical.data_dir / PL_DIR / "16951514" / "basic.json").read_bytes())
    assert cry["observation"] == {"observed_at_utc": "2026-09-17T06:00:00+00:00",
                                  "change_ts": basic["changes"]["changeTimestamp"], "status_regressed": False}
    assert cry["slices"]["event"] == {"state": "ok", "sha256": store_dump.payload_hash(basic), "empty_count": 0,
                                      "unverified_empty_count": 0, "error": None}
    assert cry["slices"]["lineups"] == {"state": "empty", "sha256": None, "empty_count": 1,
                                        "unverified_empty_count": 1, "error": None}
    assert cry["slices"]["incidents"] == {"state": "error", "sha256": None, "empty_count": 1,
                                          "unverified_empty_count": 0,
                                          "error": {"reason": "5xx", "status": 503, "count": 1}}
    assert dump["events"]["16867839"]["observation"] is None
    assert {season: sorted(pages) for season, pages in dump["schedules"].items()} == {
        "17/96668": ["round_1", "round_2", "round_3"], "17/76986": ["round_38"],
        "19/97110": ["round_28_semifinals", "round_29_final"], "132/80229": ["last_0", "last_1"],
        "2361/79116": ["last_0"], "8/97532": ["last_0", "next_0"],
    }
    round_1 = json.loads((canonical.data_dir / "matches/17_Premier_League/96668_Premier_League_26_27/round_1.json")
                         .read_bytes())
    assert round_1.pop("_complete") is True
    assert dump["schedules"]["17/96668"]["round_1"] == {"sha256": store_dump.payload_hash(round_1),
                                                        "meta": {"complete": True}}
    assert {tid: entry["seasons"] for tid, entry in dump["season_lists"].items()} == {
        "8": 2, "17": 3, "19": 2, "132": 2, "2361": 2}
    assert dump["changes"] == [{"seq": 1, "row": sf.SCORE_CHANGES[0]}, {"seq": 2, "row": sf.SCORE_CHANGES[1]}]


def test_dump_is_deterministic_and_ignores_file_times(fx: sf.LegacyFixture, tmp_path: Path) -> None:
    first = store_dump.dump(fx.data_dir, fx.leagues)
    again = sf.build_fixture(fx.name, tmp_path / "again")
    for path in again.data_dir.rglob("*"):
        if path.is_file():
            os.utime(path, (sf.BASE_MTIME + 5, sf.BASE_MTIME + 5))  # sıra değişmeden bütün zamanlar kayar
    assert store_dump.diff(first, store_dump.dump(again.data_dir, again.leagues)) == []
    assert first == store_dump.dump(fx.data_dir, fx.leagues)
    if fx.name == "empty":
        assert first == {"events": {}, "schedules": {}, "season_lists": {}, "changes": []}


def test_dump_of_the_legacy_fixture(old_forms: sf.LegacyFixture) -> None:
    dump = store_dump.dump(old_forms.data_dir, old_forms.leagues)
    assert sorted(map(int, dump["events"])) == [15000001, 15500001, 15500002, 15500003, 16837335, 16867839, 17018554,
                                                 17099711, 17185003]
    # yarım dilim dosyası: yükü yok, durum error/corrupt
    assert dump["events"]["17185003"]["slices"]["statistics"] == {
        "state": "error", "sha256": None, "empty_count": 0, "unverified_empty_count": 0,
        "error": {"reason": "corrupt", "status": None, "count": 1}}
    assert dump["schedules"] == {
        "17/96668": {"round_1": dump["schedules"]["17/96668"]["round_1"],
                     "round_2": dump["schedules"]["17/96668"]["round_2"]},
        "8/77559": {"round_1_full": dump["schedules"]["8/77559"]["round_1_full"]},
    }
    assert dump["schedules"]["17/96668"]["round_1"]["meta"] == {"filtered": True}
    assert {tid: entry["seasons"] for tid, entry in dump["season_lists"].items()} == {"8": 2, "17": 3, "2361": 1}
    # Lig adları olmadan LaLiga'nın listesi `league_seasons.csv`'den gelir
    assert store_dump.dump(old_forms.data_dir)["season_lists"]["8"]["seasons"] == 1


def _one_event(root: Path, base: str, combined: bool, eid: int = 7) -> None:
    event = {**sf.basic_payload(sf.PL_LIV), "id": eid}
    slices = {key: sf.slice_payload(key, event) for key in ("statistics", "h2h")}
    slices["lineups"] = sf.slice_payload("lineups", event, empty=True)
    if combined:
        write(root, f"{base}/{eid}.json", {"basic": event, **slices})
    else:
        write(root, f"{base}/basic.json", event)
        for key, payload in slices.items():
            write(root, f"{base}/{key}.json", payload)
    write(root, f"{base}/observation.json", sf.observation_payload(event, "2026-09-15T13:10:00+00:00"))
    write(root, f"{base}/_unavailable.json", {"lineups": 2, "incidents": 1})
    write(root, f"{base}/_slice_status.json", {"lineups": sf.empty_marker(2), "incidents": sf.error_marker("403", 403)})


@pytest.mark.parametrize("base, combined", [
    ("match_details/8_LaLiga/season_x/7", True), ("match_details/LaLiga/season_x/7", False),
    ("match_details/7", False), ("match_details/7", True), ("match_details/_no_tournament/football/7", False),
])
def test_dump_is_the_same_for_every_legacy_form(tmp_path: Path, base: str, combined: bool) -> None:
    _one_event(tmp_path / "reference", "match_details/17_Premier_League/season_x/7", combined=False)
    _one_event(tmp_path / "other", base, combined=combined)
    reference = store_dump.dump(tmp_path / "reference")
    assert store_dump.diff(reference, store_dump.dump(tmp_path / "other")) == []
    assert reference["events"]["7"]["slices"]["lineups"]["state"] == "empty"
    assert reference["events"]["7"]["slices"]["lineups"]["sha256"] is not None
    assert reference["events"]["7"]["slices"]["incidents"]["error"] == {"reason": "403", "status": 403, "count": 1}


def test_dump_sees_every_logical_change(canonical: sf.LegacyFixture) -> None:
    before = store_dump.dump(canonical.data_dir)
    base = canonical.data_dir / PL_DIR / "16837335"
    basic = json.loads((base / "basic.json").read_bytes())
    basic["homeScore"]["current"] += 1
    (base / "basic.json").write_bytes(sf.dump_json(basic))
    (base / "lineups.json").unlink()
    (base / "_unavailable.json").write_bytes(sf.dump_json({"lineups": 1}))
    (base / "observation.json").unlink()
    with open(canonical.data_dir / "score_changes.jsonl", "ab") as f:
        f.write(b'{"event_id": 1}\n')
    (canonical.data_dir / "seasons" / "8_LaLiga_seasons.json").write_bytes(sf.dump_json({"seasons": []}))
    (canonical.data_dir / "matches/19_FA_Cup/97110_FA_Cup_26_27/round_29_final.json").unlink()
    changed = store_dump.diff(before, store_dump.dump(canonical.data_dir))
    assert [line.split(":")[0] for line in changed] == [
        "$.changes", "$.events.16837335.observation", "$.events.16837335.slices.event.sha256",
        "$.events.16837335.slices.lineups.sha256", "$.events.16837335.slices.lineups.state",
        "$.events.16837335.slices.lineups.unverified_empty_count", "$.schedules.19/97110.round_29_final",
        "$.season_lists.8.seasons", "$.season_lists.8.sha256",
    ]
    # Yalnızca biçim değişirse (girinti, sıkıştırma) döküm değişmez
    same = json.loads((base / "statistics.json").read_bytes())
    (base / "statistics.json").write_bytes(json.dumps(same, separators=(",", ":")).encode("utf-8"))
    assert store_dump.diff(store_dump.dump(canonical.data_dir), store_dump.dump(canonical.data_dir)) == []
    assert store_dump.dump(canonical.data_dir)["events"]["16837335"]["slices"]["statistics"] == \
        before["events"]["16837335"]["slices"]["statistics"]


def test_dump_writes_observation_times_in_utc(tmp_path: Path) -> None:
    for name, observed in (("a", "2026-09-19T09:00:00+03:00"), ("b", "2026-09-19T06:00:00Z")):
        write(tmp_path / name, "match_details/7/basic.json", event_of(7))
        write(tmp_path / name, "match_details/7/observation.json", {"observed_at_utc": observed, "change_ts": 1})
    first = store_dump.dump(tmp_path / "a")
    assert first == store_dump.dump(tmp_path / "b")
    assert first["events"]["7"]["observation"]["observed_at_utc"] == "2026-09-19T06:00:00+00:00"


def test_diff_reports_paths() -> None:
    assert store_dump.diff({"a": 1, "b": [1, 2], "c": {"d": None}}, {"a": 1.0, "b": [1], "e": 0, "c": {"d": None}}) == [
        "$.a: 1 -> 1.0", "$.b: uzunluk 2 -> 1", "$.e: eklendi"]
    assert store_dump.diff({"a": 1}, {}) == ["$.a: silindi"]
