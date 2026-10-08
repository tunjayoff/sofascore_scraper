"""
Testlerin veri dizini fabrikası (tests/store_fixtures.py): belirlenimci kurulum, eski düzenin her biçimi ve
bugünkü (ya da dondurulmuş eski) yazıcılarla aynı dosyalar ("fabrika sadakati").

Bu testler 3.1'e kadar okuyucu goldenlarıyla (G-02, tests/characterization/test_reader_goldens.py) bir
dosyadaydı; goldenlar 2.x'in okuyucularıyla birlikte kalktı (plan maddesi P30), fabrikanın testleri buraya taşındı.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List

import pytest

import legacy_writer
import store_fixtures as sf


@pytest.fixture(scope="module", autouse=True)
def _utc_timezone() -> Iterator[None]:
    """Özet CSV'lerindeki `match_date` ve `last_update` yerel saattir: altın dosyalar UTC ile kaydedildi."""
    if hasattr(time, "tzset"):
        before = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"
        time.tzset()
        try:
            yield
        finally:
            if before is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = before
            time.tzset()
        return
    # Windows: süreç içinde saat dilimi değiştirilemez; makine UTC değilse karşılaştırma anlamsız
    for ts in (sf.FIXTURE_NOW, sf.FIXTURE_NOW - 150 * sf.DAY):
        utc = dt.datetime.fromtimestamp(ts, dt.timezone.utc).replace(tzinfo=None)
        if dt.datetime.fromtimestamp(ts) != utc:
            pytest.skip("yerel saat dilimi UTC değil ve time.tzset yok")
    yield



# --- fabrikanın kendisi ---------------------------------------------------------------------


def _tree(root: Path) -> Dict[str, bytes]:
    """Ağaçtaki dosyalar; `.meta/` (Store'un kendi dosyaları: yazıcıların kancaları depoyu açar) dışında."""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and ".meta" not in p.relative_to(root).parts}


def _mtimes(root: Path) -> Dict[str, int]:
    return {p.relative_to(root).as_posix(): int(p.stat().st_mtime) for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("name", sf.FIXTURE_NAMES)
def test_factory_is_deterministic(name: str, tmp_path: Path) -> None:
    """İki kurulum bayt bayt ve mtime'larıyla aynıdır; hiçbir dosya `FIXTURE_NOW`'dan yeni değildir."""
    first = sf.build_fixture(name, tmp_path / "a")
    second = sf.build_fixture(name, tmp_path / "b")
    assert _tree(first.data_dir) == _tree(second.data_dir)
    assert first.details == second.details and first.listed == second.listed
    assert _mtimes(first.data_dir) == _mtimes(second.data_dir)
    assert all(mtime <= sf.FIXTURE_NOW for mtime in _mtimes(first.data_dir).values())


def test_factory_covers_every_legacy_form(tmp_path: Path) -> None:
    """docs/design/01-storage.md bölüm 5.1'deki her biçim en az bir dizinde var."""
    canonical = sf.build_fixture("canonical", tmp_path / "canonical")
    legacy = sf.build_fixture("legacy", tmp_path / "legacy")
    files = set(_tree(canonical.data_dir)) | set(_tree(legacy.data_dir))

    def has(pattern: str) -> bool:
        return any(re.fullmatch(pattern, f) for f in files)

    eid, name = r"\d+", r"[^/]+"
    assert has(rf"match_details/\d+_{name}/season_{name}/{eid}/basic\.json")  # L1
    assert has(rf"match_details/LaLiga/season_{name}/{eid}/basic\.json")  # L2
    assert has(rf"match_details/{eid}/basic\.json")  # L3
    assert has(rf"match_details/\d+_{name}/season_{name}/({eid})/\1\.json")  # L4, basic.json'ın yanında
    assert has(rf"match_details/({eid})/\1\.json")  # L4, tek başına
    assert has(rf"match_details/_no_tournament/(football|tennis|unknown)/{eid}/basic\.json")  # L5
    assert has(rf"matches/\d+_{name}/\d+_{name}/round_\d+\.json")
    assert has(rf"matches/\d+_{name}/\d+_{name}/round_\d+_[a-z]+\.json")  # kupa turu (slug)
    assert has(rf"matches/\d+_{name}/\d+_{name}/events_last_\d+\.json")
    assert has(rf"matches/\d+_{name}/\d+_{name}_summary\.json") and has(rf"matches/\d+_{name}/\d+_{name}_summary\.csv")
    assert has(rf"matches/\d+_{name}/\d+_{name}_matches\.csv")  # eski ad, lig dizininde
    assert has(rf"matches/\d+_{name}/\d+_{name}/round_\d+_matches\.csv")  # ilk sürüm, sezon dizininde
    assert {"seasons/17_Premier_League_seasons.json", "seasons/17_seasons.json", "seasons/LaLiga_seasons.json",
            "league_seasons.csv", "score_changes.jsonl", "watch_events.jsonl", "watch_state_football.json",
            "watch_state_tennis.json"} <= files

    # tur dosyaları: `_complete` true, false ve hiç yok
    rounds = [json.loads(data) for f, data in {**_tree(canonical.data_dir), **_tree(legacy.data_dir)}.items()
              if re.search(r"/round_\d+(_[a-z]+)?\.json$", f) and "_full" not in f]
    assert {r.get("_complete", "absent") for r in rounds} == {True, False, "absent"}

    # observation.json / _unavailable.json / _slice_status.json: sekiz birleşimin hepsi
    markers = ("observation.json", "_unavailable.json", "_slice_status.json")
    combos = {tuple(m in d.files for m in markers) for d in canonical.details}
    assert len(combos) == 8

    # penaltılarla biten futbol maçı: özet `current`'ı (10-9) yazar, `display` 3-3'tür
    basic = json.loads(_tree(canonical.data_dir)[sf.detail_dir(sf.Detail(sf.CUP_PEN)) + "/basic.json"])
    assert (basic["homeScore"]["current"], basic["homeScore"]["display"]) == (10, 3)
    summary = (canonical.data_dir / "matches/19_FA_Cup/97110_FA_Cup_26_27_summary.csv").read_text(encoding="utf-8")
    assert f"{basic['id']},Manchester City,Manchester United,10,9," in summary

    # iki özet dosyasında geçen maç ve iki sezon listesi olan lig
    twice = f",{sf.event_id(sf.PL_ARS)},"
    assert sum(twice in (legacy.data_dir / f).read_text(encoding="utf-8") for f in legacy.summary_files) == 2
    assert {"seasons/17_seasons.json", "seasons/17_Premier_League_seasons.json"} <= set(_tree(legacy.data_dir))


@pytest.mark.parametrize("name", sf.FIXTURE_NAMES)
def test_listed_matches_have_distinct_dates(name: str, tmp_path: Path) -> None:
    """
    GET /api/matches tarihe göre kararlı sıralar; tarihi eşit iki maçın sırası özet dosyalarının dizin listeleme
    sırasına kalır. Altın dosyalar bundan etkilenmesin diye fixture'da her maçın başlangıç saati farklıdır.
    """
    fixture = sf.build_fixture(name, tmp_path / "data")
    dates: Dict[str, str] = {}
    for rel in fixture.summary_files:
        with open(fixture.data_dir / rel, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                dates[row["match_id"]] = row["match_date"]
    assert len(set(dates.values())) == len(dates)


# --- fabrika sadakati: bugünkü yazıcılarla aynı dosyalar ------------------------------------------


# Program sayfalarının ve sezon özetinin sadakati (eski test_fidelity_schedule_and_summary_files) ST-22 ile
# tests/test_store_entities_put.py'ye taşındı: yazıcı artık v3'e yazar; aynı yanıtlardan v3'teki mantıksal döküm
# fabrikanın eski düzen dosyalarınınkine eşittir.


def test_fidelity_detail_directories(tmp_path: Path) -> None:
    """
    Maç dizininin yeri (L1, L5) ve dosyaları: eski düzen yazıcısı aynısını yazar. Yazıcı ST-21'den beri v3'e yazar;
    fabrika eski sürümlerin verisini kurar, karşılaştırma eski yazıcının dondurulmuş kopyasıyladır
    (tests/legacy_writer.py).
    """
    built_root = tmp_path / "built"
    built = _tree(sf.build_fixture("canonical", built_root / "canonical").data_dir)
    built.update(_tree(sf.build_fixture("legacy", built_root / "legacy").data_dir))
    for detail in (sf.Detail(sf.PL_ARS, observed="2026-09-19T06:00:00+00:00"), sf.Detail(sf.CUP_AET),
                   sf.Detail(sf.NBA_RECENT, observed="2026-10-01T08:00:00+00:00"),
                   sf.Detail(sf.LIGA_NEXT, slices=("team_streaks", "pregame_form", "h2h")),
                   sf.Detail(sf.FRIENDLY_A, form="L5", observed="2026-09-28T07:00:00+00:00"),
                   sf.Detail(sf.NO_SPORT_A, form="L5", slices=())):
        basic = sf.basic_payload(detail.ev)
        data: Dict[str, Any] = {"basic": basic, **{key: sf.slice_payload(key, basic) for key in detail.slices}}
        if detail.observed:
            data["observation"] = sf.observation_payload(basic, detail.observed)
        legacy_writer.save_legacy(tmp_path / "written", basic["id"], data)
        base = sf.detail_dir(detail)
        written = {f: d for f, d in _tree(tmp_path / "written").items() if f.startswith(base + "/")}
        assert written and written == {f: d for f, d in built.items() if f.startswith(base + "/")}, base


def test_fidelity_marker_files(tmp_path: Path) -> None:
    """_unavailable.json ve _slice_status.json: eski yazıcının ürettiği biçim fabrikanınkiyle aynı (zaman hariç)."""
    from sofascore_scraper.slices import SLICE_EMPTY, SLICE_FAILED, SliceOutcome

    basic = sf.basic_payload(sf.PL_BHA)
    data = {"basic": basic, **{key: sf.slice_payload(key, basic) for key in sf.REQUIRED_SLICES if key != "lineups"}}
    data["incidents"] = sf.slice_payload("incidents", basic, empty=True)
    outcomes = {"lineups": SliceOutcome(SLICE_EMPTY, reason="404", http_status=404),
                "incidents": SliceOutcome(SLICE_FAILED, reason="5xx", http_status=503)}
    for _ in range(2):
        legacy_writer.save_legacy(tmp_path, basic["id"], data, outcomes)
    folder = tmp_path / sf.detail_dir(sf.Detail(sf.PL_BHA))

    def without_time(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: without_time(v) for k, v in value.items() if k != "at"}
        return value

    assert json.loads((folder / "_unavailable.json").read_text(encoding="utf-8")) == {"lineups": 2}
    status = json.loads((folder / "_slice_status.json").read_text(encoding="utf-8"))
    expected = {"lineups": sf.empty_marker(2), "incidents": sf.error_marker("5xx", 503, 2)}
    assert without_time(status) == without_time(expected)
    for stamp in (status["lineups"]["empty"]["at"], expected["lineups"]["empty"]["at"]):
        assert dt.datetime.fromisoformat(stamp).utcoffset() == dt.timedelta(0) and len(stamp) == 25


def test_fidelity_slices_and_names() -> None:
    """Dilim listesi, "veri var mı" denetimleri, dizin adları ve gözlem kaydı kodla aynı."""
    from detail_fetch import LEGACY_DETAIL_KEYS as DETAIL_SLICE_KEYS
    from legacy_writer import NO_TOURNAMENT_DIR
    from sofascore_scraper.slices import match_detail_slice_present
    from sofascore_scraper.sports import slices_for
    from sofascore_scraper.status import observation_record
    from sofascore_scraper.store import legacy

    assert sf.REQUIRED_SLICES == DETAIL_SLICE_KEYS
    # FX-16: teniste pregame_form tamlığa girmez, point_by_point (fixture'ların SPORT_SPECIFIC_SLICES'ı) girer
    assert tuple(s.key for s in slices_for("tennis") if not s.counts_in("tennis")) == ("pregame_form",)
    assert all(s.counts_in("tennis") for s in slices_for("tennis") if s.key in sf.SPORT_SPECIFIC_SLICES)
    assert NO_TOURNAMENT_DIR in sf.detail_dir(sf.Detail(sf.FRIENDLY_A, form="L5"))
    for ev in (sf.PL_ARS, sf.NBA_A, sf.WIM_A):
        basic = sf.basic_payload(ev)
        for key in sf.REQUIRED_SLICES + sf.SPORT_SPECIFIC_SLICES:
            assert match_detail_slice_present(key, {key: sf.slice_payload(key, basic)}), key
            # Kayıtlı her dilimin kendi kuralı var: içi boş gövde (point_by_point'in boş listesi de) "var" sayılmaz
            empty = {key: sf.slice_payload(key, basic, empty=True)}
            assert match_detail_slice_present(key, empty) is False, key
        when = dt.datetime(2026, 9, 19, 6, tzinfo=dt.timezone.utc)
        assert observation_record(basic, when) == sf.observation_payload(basic, "2026-09-19T06:00:00+00:00")
    for name in ("Premier League", "Wimbledon, Men", "LaLiga 25/26", "a\\b"):
        assert sf.safe_name(name) == legacy.safe_name(name)
    assert sf.league_dir(sf.WIMBLEDON) == legacy.league_dir_name(sf.WIMBLEDON.id, sf.WIMBLEDON.name)
    assert sf.season_dir(sf.PL_2627) == f"{sf.PL_2627.id}_{legacy.safe_name(sf.PL_2627.name)}"
    seasons_file = f"{legacy.league_dir_name(sf.WIMBLEDON.id, sf.WIMBLEDON.name)}_seasons.json"
    assert seasons_file == "2361_Wimbledon,_Men_seasons.json"  # canonical dizinindeki ad


def _score_change_inputs() -> List[Any]:
    """(eski basic, yeni basic, spor, yazıldığı an) — fabrikadaki SCORE_CHANGES satırlarının kaynağı."""
    new = sf.basic_payload(sf.PL_LIV)
    old = json.loads(json.dumps(new))
    for key in ("current", "display", "normaltime", "period1"):
        old["awayScore"][key] -= 1
    old["changes"]["changeTimestamp"] -= 3053
    void_new = sf.basic_payload(sf.NBA_VOID, sf.NBA_VOID_BASIC)
    void_old = sf.basic_payload(sf.NBA_VOID)
    return [(old, new, "football", "2026-09-15T13:10:00+00:00"),
            (void_old, void_new, "basketball", "2026-09-21T00:00:00+00:00")]


def test_fidelity_score_changes() -> None:
    from sofascore_scraper.refresh import change_row, diff_basic

    rows = [change_row(old, new, diff_basic(old, new), sport, now=dt.datetime.fromisoformat(when))
            for old, new, sport, when in _score_change_inputs()]
    assert json.loads(json.dumps(rows)) == list(sf.SCORE_CHANGES)
