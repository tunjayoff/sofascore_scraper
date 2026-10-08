"""
Turnuva okuma servisi ve ona geçen okuyucular (plan maddesi RD-5): sezon listeleri, "maç listesi indirilmiş mi"
sorusu ve liglerin sporu katalogdan okunur.

Sınananlar:

  * sezon listesi kuralı: bir ligin birden çok dosyası varsa adı ne olursa olsun en yenisi; okuyucular (servis,
    yapılandırılmış adla servis, SeasonFetcher) aynı listeyi verir;
  * okunamayan listeler için verilen yanıtlar;
  * adında kimlik olmayan dosyanın (`<ad>_seasons.json`), takip tablosu başka bir ad taşısa da, ligin
    yapılandırmadaki adıyla bulunması (plan bölüm 15 satır 74);
  * bugünkü yazıcıların ürettiği dizinde (canonical) hiçbir şeyin değişmediği.

2.x'in `GET /api/leagues`, `/api/leagues/search` ve `/api/leagues/{id}/seasons` yolları 3.1'de kalktı (P30);
`get_seasons` o yolun servisi nasıl çağırdığını (yapılandırmadaki lig adıyla) yeniden üretir.

Testin kendi yazdığı dosyaları katalog, depo bir sonraki açılışta görür (süreç içinde açık duran depo dosya
sistemini izlemez); bu yüzden dosya yazan adımlardan sonra depo `reopened` ile kapatılıp açılır.
"""
from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import pytest

import conftest
import store_fixtures as sf
from sofascore_scraper.season_fetcher import SeasonFetcher
from sofascore_scraper.services import tournaments
from sofascore_scraper.store import FollowSpec, Store, StoreError, open_store
from sofascore_scraper.web import deps, league_sports

LEAGUES_FILE = os.path.join(conftest.CONFIG_DIR, "leagues.txt")
SPORTS_FILE = os.path.join(conftest.CONFIG_DIR, "league_sports.json")
OLDER, NEWER = sf.BASE_MTIME - 30 * sf.DAY, sf.BASE_MTIME


class Leagues:
    """SeasonFetcher'ın yapılandırmadan kullandığı iki yöntem."""

    def __init__(self, leagues: Optional[Dict[int, str]] = None) -> None:
        self.leagues = dict(leagues or {})

    def get_leagues(self) -> Dict[int, str]:
        return dict(self.leagues)

    def get_league_by_id(self, league_id: int) -> Optional[str]:
        return self.leagues.get(league_id)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Testin veri dizini; web katmanı ve Store sınırının çalışma zamanı denetimi de ona bakar (DATA_DIR)."""
    path = tmp_path / "data"
    path.mkdir()
    monkeypatch.setenv("DATA_DIR", str(path))
    return path


def write(data_dir: Path, rel: str, content: Any, mtime: int = NEWER) -> None:
    path = data_dir.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else json.dumps(content).encode("utf-8"))
    os.utime(path, (mtime, mtime))


def season_list(*season_ids: int) -> Dict[str, Any]:
    return {"seasons": [{"id": season_id, "name": f"S {season_id}", "year": "26/27"} for season_id in season_ids]}


def reopened(data_dir: Path) -> Store:
    """Depoyu yeni bir süreç açıyormuş gibi açar: katalog dosyalarla yeniden eşitlenir."""
    open_store(data_dir).close()
    return open_store(data_dir)


def ids(seasons: Optional[List[Any]]) -> Optional[List[Any]]:
    return None if seasons is None else [s["id"] if isinstance(s, dict) else s for s in seasons]


def get_seasons(league_id: int) -> Dict[str, Any]:
    """Ligin saklanan sezon listesi, yapılandırmadaki adıyla (2.x'in `GET /api/leagues/{id}/seasons` yanıtı)."""
    manager = deps.config_manager()
    seasons = tournaments.seasons_of(open_store(manager.get_data_dir()), league_id,
                                     name=manager.get_league_by_id(league_id))
    return {"seasons": seasons or [], "fetched": seasons is not None}


@contextlib.contextmanager
def configured(leagues: Dict[int, str], sports: Optional[bytes] = None) -> Iterator[None]:
    """
    Uygulamanın lig listesini (ve verildiyse spor dosyasını, bayt bayt) testin config dizinine yazar; çıkışta
    ikisi de eski içeriğine döner. `sports=None`: spor dosyası yok.
    """
    def read(path: str) -> Optional[bytes]:
        try:
            with open(path, "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def put(path: str, content: Optional[bytes]) -> None:
        if content is None:
            with contextlib.suppress(FileNotFoundError):
                os.remove(path)
            return
        previous = os.stat(path).st_mtime_ns if os.path.exists(path) else 0
        with open(path, "wb") as f:
            f.write(content)
        stamp = max(time.time_ns(), previous + 1_000_000_000)  # ConfigManager değişikliği kesin görsün
        os.utime(path, ns=(stamp, stamp))

    before = read(LEAGUES_FILE), read(SPORTS_FILE)
    lines = ["# League configuration file", ""] + [f"{name}: {league_id}" for league_id, name in leagues.items()]
    put(LEAGUES_FILE, ("\n".join(lines) + "\n").encode("utf-8"))
    put(SPORTS_FILE, sports)
    try:
        yield
    finally:
        put(LEAGUES_FILE, before[0])
        put(SPORTS_FILE, before[1])


# --- sezon listesi kuralı: en yeni dosya, adı ne olursa olsun --------------------------------------------


def test_several_season_list_files_every_reader_uses_the_newest(data_dir: Path) -> None:
    """
    Davranış değişikliği (RD-5). Eskiden web uç noktası yalın `<id>_seasons.json`'ı (daha eski olsa da),
    SeasonFetcher ise ligin yapılandırmadaki adıyla yazılmış dosyayı (daha eski olsa da) seçerdi.
    """
    # 54: yalın ad eski, adlı dosya yeni → adlı dosya (web eskiden 541'i verirdi)
    write(data_dir, "seasons/54_seasons.json", season_list(541), OLDER)
    write(data_dir, "seasons/54_Named_seasons.json", season_list(542), NEWER)
    # 55: ligin adıyla yazılmış dosya eski, yalın ad yeni → yalın ad (SeasonFetcher eskiden 551'i verirdi)
    write(data_dir, "seasons/55_Cup_seasons.json", season_list(551), OLDER)
    write(data_dir, "seasons/55_seasons.json", season_list(552), NEWER)
    # 56: üç adlı dosya; en yenisi başka bir adla (lig yeniden adlandırılmış) yazılmış
    write(data_dir, "seasons/56_Old_Name_seasons.json", season_list(561), OLDER)
    write(data_dir, "seasons/56_Liga_seasons.json", season_list(562), OLDER + sf.DAY)
    write(data_dir, "seasons/56_unknown_league_56_seasons.json", season_list(563), NEWER)
    leagues = {54: "Named", 55: "Cup", 56: "Liga"}
    expected = {54: [542], 55: [552], 56: [563]}

    store = open_store(data_dir)
    fetcher = SeasonFetcher(Leagues(leagues), str(data_dir))
    with configured(leagues):
        for league_id, wanted in expected.items():
            assert ids(tournaments.seasons_of(store, league_id)) == wanted
            assert ids(tournaments.seasons_of(store, league_id, name=leagues[league_id])) == wanted
            assert ids(fetcher.get_seasons_for_league(league_id)) == wanted
            assert ids(fetcher.league_seasons[league_id]) == wanted
            assert fetcher.get_season_name(league_id, wanted[0]) == f"S {wanted[0]}"
            body = get_seasons(league_id)
            assert (ids(body["seasons"]), body["fetched"]) == (wanted, True)
    # Yapılandırılmamış lig için de aynı (uç nokta ve SeasonFetcher lig listesine bağlı değildir)
    unconfigured = SeasonFetcher(Leagues(), str(data_dir))
    assert {lid: ids(unconfigured.get_seasons_for_league(lid)) for lid in expected} == expected
    assert {lid: ids(get_seasons(lid)["seasons"]) for lid in expected} == expected


def test_the_csv_list_counts_only_for_a_league_without_a_json_list(data_dir: Path) -> None:
    """İlk sürümün `league_seasons.csv` dosyası: JSON listesi olan lig için, daha yeni olsa da kullanılmaz."""
    write(data_dir, "league_seasons.csv", sf.dump_csv(sf.SEASONS_CSV_COLUMNS, [
        {"Liga Adı": "Premier League", "Lig ID": 17, "Sezon ID": 1, "Sezon Adı": "PL 1", "Sezon Yılı": "01/02"},
        {"Liga Adı": "LaLiga", "Lig ID": 8, "Sezon ID": 2, "Sezon Adı": "LaLiga 2", "Sezon Yılı": "02/03"},
    ]), NEWER)
    write(data_dir, "seasons/17_Premier_League_seasons.json", season_list(170), OLDER)
    store = open_store(data_dir)

    assert ids(tournaments.seasons_of(store, 17)) == [170]
    assert tournaments.seasons_of(store, 8) == [{"id": 2, "name": "LaLiga 2", "year": "02/03"}]
    fetcher = SeasonFetcher(Leagues({17: "Premier League", 8: "LaLiga"}), str(data_dir))
    # Eskiden CSV yalnızca hiçbir ligin JSON dosyası yokken okunurdu; şimdi lig başına karar verilir
    assert {lid: ids(seasons) for lid, seasons in fetcher.league_seasons.items()} == {17: [170], 8: [2]}
    assert get_seasons(8) == {"seasons": [{"id": 2, "name": "LaLiga 2", "year": "02/03"}], "fetched": True}


# --- okunamayan listeler ---------------------------------------------------------------------------------

UNREADABLE = {
    "truncated": b'{"seasons": [{"id": 521',
    "array instead of an object": b'[{"id": 501, "name": "bare list"}]',
    "text": b'"seasons"',
    "not UTF-8": b'{"seasons": [{"id": 1, "name": "\xfd"}]}',
    "empty file": b"",
}


@pytest.mark.parametrize("content", UNREADABLE.values(), ids=UNREADABLE.keys())
def test_an_unreadable_file_is_not_a_season_list(data_dir: Path, content: bytes) -> None:
    """
    Karar (RD-5): okunamayan dosya sezon listesi sayılmaz. Ligin başka dosyası yoksa listesi yoktur (uç nokta
    200, boş liste, `fetched: false`); varsa, daha eski de olsa, okunabilen en yeni dosya geçerlidir.
    """
    write(data_dir, "seasons/52_Broken_seasons.json", content, NEWER)
    write(data_dir, "seasons/53_Broken_seasons.json", content, NEWER)
    write(data_dir, "seasons/53_seasons.json", season_list(531), OLDER)
    store = open_store(data_dir)
    fetcher = SeasonFetcher(Leagues({52: "Broken", 53: "Broken too"}), str(data_dir))

    assert tournaments.seasons_of(store, 52) is None
    assert fetcher.get_seasons_for_league(52) == []
    assert get_seasons(52) == {"seasons": [], "fetched": False}

    assert ids(tournaments.seasons_of(store, 53)) == [531]
    assert ids(fetcher.get_seasons_for_league(53)) == [531]
    assert get_seasons(53) == {"seasons": season_list(531)["seasons"], "fetched": True}
    assert set(fetcher.league_seasons) == {53}


@pytest.mark.parametrize("content", [
    b'{"id": 511, "name": "no seasons key"}', b'{"seasons": null}', b'{"seasons": "text"}', b'{"seasons": []}',
], ids=["no seasons key", "null", "text", "empty list"])
def test_a_list_file_without_a_list_of_seasons_is_an_empty_list(data_dir: Path, content: bytes) -> None:
    """Nesne okunuyor ama `seasons` bir liste değil: liste vardır ve boştur (`fetched: true`, eskisi gibi)."""
    write(data_dir, "seasons/51_Odd_seasons.json", content)
    store = open_store(data_dir)

    assert tournaments.seasons_of(store, 51) == []
    assert SeasonFetcher(Leagues({51: "Odd"}), str(data_dir)).get_seasons_for_league(51) == []
    assert get_seasons(51) == {"seasons": [], "fetched": True}


def test_a_list_with_a_byte_order_mark_is_read_and_stored_as_is(data_dir: Path) -> None:
    """BOM'lu dosya eskiden okunamıyordu (`fetched: false`); yük, fazladan anahtarlarıyla saklandığı gibi döner."""
    write(data_dir, "seasons/59_BOM_seasons.json", b'\xef\xbb\xbf{"seasons": [{"id": 591, "x": [1], "editor": false}]}')

    assert get_seasons(59) == {"seasons": [{"id": 591, "x": [1], "editor": False}], "fetched": True}
    assert tournaments.seasons_of(open_store(data_dir), 59) == [{"id": 591, "x": [1], "editor": False}]


def test_a_league_without_any_list(data_dir: Path) -> None:
    store = open_store(data_dir)
    assert tournaments.seasons_of(store, 5) is None
    assert tournaments.seasons_of(store, 5, name="Five") is None
    assert tournaments.season_lists(store, {5: "Five"}) == {}
    assert get_seasons(5) == {"seasons": [], "fetched": False}


def test_a_store_that_cannot_be_read_is_answered_like_a_missing_list(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Depolama hatası SeasonFetcher'ı düşürmez: boş liste ve bir hata satırı."""
    write(data_dir, "seasons/17_seasons.json", season_list(1))
    fetcher = SeasonFetcher(Leagues({17: "Premier League"}), str(data_dir))

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise StoreError("catalog.db cannot be read")

    monkeypatch.setattr(tournaments, "seasons_of", broken)
    monkeypatch.setattr(tournaments, "season_lists", broken)

    assert fetcher.get_seasons_for_league(17) == []
    assert fetcher.league_seasons == {}
    assert fetcher.get_season_name(17, 1) == "Season_1"
    assert "Stored season list of league 17 could not be read" in caplog.text
    assert "Stored season lists could not be loaded" in caplog.text


# --- adında kimlik olmayan dosya (plan bölüm 15, satır 74) -----------------------------------------------


def test_a_name_only_file_is_resolved_with_the_configured_league_name(data_dir: Path) -> None:
    """
    `LaLiga_seasons.json`: turnuvası addan bulunur. Takip tablosu boşken (depo yeni açılmış) ya da aynı adı
    taşırken de bulunur; ad verilmezse ve katalog adı bilmiyorsa bulunmaz.
    """
    write(data_dir, "seasons/LaLiga_seasons.json", season_list(81, 82))
    store = open_store(data_dir)
    assert store.follows.leagues() == {}

    assert tournaments.seasons_of(store, 8) is None  # katalog dosyayı turnuvasına bağlayamadı
    assert ids(tournaments.seasons_of(store, 8, name="LaLiga")) == [81, 82]
    assert tournaments.seasons_of(store, 8, name="Serie A") is None
    assert tournaments.seasons_of(store, 9, name="LaLiga 2") is None
    fetcher = SeasonFetcher(Leagues({8: "LaLiga"}), str(data_dir))
    assert ids(fetcher.get_seasons_for_league(8)) == [81, 82]
    assert ids(fetcher.league_seasons[8]) == [81, 82] and fetcher.get_season_name(8, 82) == "S 82"
    assert SeasonFetcher(Leagues(), str(data_dir)).get_seasons_for_league(8) == []

    # Takipler lig dosyasının aynası olduğunda (yapılandırma dosyası yok) katalog da aynı sonucu verir
    store.follows.apply([FollowSpec(kind="tournament", entity_id=8, name="LaLiga")], origin="legacy")
    store = reopened(data_dir)
    assert ids(tournaments.seasons_of(store, 8)) == [81, 82]
    assert ids(tournaments.seasons_of(store, 8, name="LaLiga")) == [81, 82]


def test_a_name_only_file_is_found_when_the_config_file_names_the_league_differently(data_dir: Path) -> None:
    """
    Plan bölüm 15 satır 74. Yapılandırma dosyası (sofascore.toml) turnuvaya başka bir ad verince takip tablosu
    o adı taşır; diskteki dosya ve dizin adları ise leagues.txt'teki addan kurulmuştur. Katalog dosyayı
    bağlayamaz; okuyucular ligin leagues.txt'teki adını verdikleri için yine bulurlar.
    """
    write(data_dir, "seasons/LaLiga_seasons.json", season_list(81, 82))
    store = open_store(data_dir)
    store.follows.apply([FollowSpec(kind="tournament", entity_id=8, name="la-liga")], origin="config")
    store = reopened(data_dir)
    assert store.follows.leagues() == {8: "la-liga"}
    assert tournaments.seasons_of(store, 8) is None

    assert ids(tournaments.seasons_of(store, 8, name="LaLiga")) == [81, 82]
    assert {lid: ids(s) for lid, s in tournaments.season_lists(store, {8: "LaLiga"}).items()} == {8: [81, 82]}
    fetcher = SeasonFetcher(Leagues({8: "LaLiga"}), str(data_dir))
    assert ids(fetcher.get_seasons_for_league(8)) == [81, 82]
    assert fetcher.get_season_name(8, 81) == "S 81"
    with configured({8: "LaLiga"}):
        assert get_seasons(8) == {"seasons": season_list(81, 82)["seasons"], "fetched": True}


@pytest.mark.parametrize("follow_name", [None, "LaLiga", "la-liga"], ids=["no follow", "same name", "config name"])
def test_a_name_only_file_takes_part_in_the_newest_rule(data_dir: Path, follow_name: Optional[str]) -> None:
    """Addan çözülen dosya da öteki dosyalarla aynı kurala girer: en yenisi kazanır, CSV yalnızca JSON yokken."""
    write(data_dir, "league_seasons.csv", sf.dump_csv(sf.SEASONS_CSV_COLUMNS, [
        {"Liga Adı": "LaLiga", "Lig ID": 8, "Sezon ID": 1, "Sezon Adı": "csv", "Sezon Yılı": "01/02"},
        {"Liga Adı": "Serie A", "Lig ID": 23, "Sezon ID": 2, "Sezon Adı": "csv", "Sezon Yılı": "01/02"},
    ]), NEWER + sf.DAY)
    write(data_dir, "seasons/LaLiga_seasons.json", season_list(81), NEWER)  # 8: addan, en yeni JSON
    write(data_dir, "seasons/8_seasons.json", season_list(80), OLDER)
    write(data_dir, "seasons/Serie A_seasons.json", season_list(231), OLDER)  # 23: addan, daha eski
    write(data_dir, "seasons/23_Serie_A_seasons.json", season_list(232), NEWER)
    write(data_dir, "seasons/Liga Portugal_seasons.json", season_list(2381), OLDER)  # 238: yalnızca addan
    store = open_store(data_dir)
    if follow_name is not None:
        store.follows.apply([FollowSpec(kind="tournament", entity_id=8, name=follow_name)], origin="config")
        store = reopened(data_dir)
    names = {8: "LaLiga", 23: "Serie A", 238: "Liga Portugal"}
    expected = {8: [81], 23: [232], 238: [2381]}

    assert {lid: ids(tournaments.seasons_of(store, lid, name=name)) for lid, name in names.items()} == expected
    assert {lid: ids(s) for lid, s in tournaments.season_lists(store, names).items()} == expected
    fetcher = SeasonFetcher(Leagues(names), str(data_dir))
    assert {lid: ids(fetcher.get_seasons_for_league(lid)) for lid in names} == expected
    assert {lid: ids(s) for lid, s in fetcher.league_seasons.items()} == expected


def test_stored_lists_of_unconfigured_leagues_are_loaded_too(data_dir: Path) -> None:
    """
    `league_seasons` (terminal menüsünün sezon listesi): yapılandırılmamış ve hiçbir maçı bilinmeyen bir ligin
    saklanan listesi de yüklenir, eskiden olduğu gibi.
    """
    write(data_dir, "seasons/17_Premier_League_seasons.json", season_list(171))
    write(data_dir, "seasons/23_Serie_A_seasons.json", season_list(231))
    write(data_dir, "seasons/Unknown Cup_seasons.json", season_list(991))  # turnuvası bilinmiyor: yüklenemez
    fetcher = SeasonFetcher(Leagues({17: "Premier League", 35: "Bundesliga"}), str(data_dir))

    assert {lid: ids(s) for lid, s in fetcher.league_seasons.items()} == {17: [171], 23: [231]}
    assert fetcher.get_season_name(23, 231) == "S 231"
    assert fetcher.get_season_name(35, 1) == "Season_1" and 35 not in fetcher.league_seasons


# --- SeasonFetcher ---------------------------------------------------------------------------------------


def test_the_fetcher_does_not_open_the_store_until_a_list_is_read(
    data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Kurucu `.meta/` altında bir şey yaratmaz (build_context depo açmadan kurulabilir); listeler ilk okumada yüklenir."""
    write(data_dir, "seasons/17_Premier_League_seasons.json", season_list(171, 172))
    with caplog.at_level("INFO"):
        fetcher = SeasonFetcher(Leagues({17: "Premier League"}), str(data_dir))
        assert not (data_dir / ".meta").exists()
        assert "Season lists loaded" not in caplog.text

        assert ids(fetcher.league_seasons[17]) == [171, 172]
    assert (data_dir / ".meta" / "catalog.db").is_file()
    assert "Season lists loaded from the catalog: 1 league(s), 2 season(s)" in caplog.text
    caplog.clear()
    assert ids(fetcher.league_seasons[17]) == [171, 172] and "Season lists loaded" not in caplog.text  # bir kez

    fetcher.league_seasons = {238: [{"id": 1, "name": "assigned"}]}
    assert fetcher.get_season_name(238, 1) == "assigned"


def test_a_fetched_list_is_what_the_readers_see_next(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Yazıcı kancası kataloğu günceller: çekilen liste, eski bir dosya dururken de, hemen okunur."""
    write(data_dir, "seasons/17_seasons.json", season_list(1), OLDER)
    fetcher = SeasonFetcher(Leagues({17: "Premier League"}), str(data_dir))
    assert ids(fetcher.get_seasons_for_league(17)) == [1]
    fresh = {"seasons": [{"id": 96668, "name": "Premier League 26/27", "year": "26/27", "editor": False}]}
    monkeypatch.setattr("sofascore_scraper.season_fetcher.make_api_request", lambda url, **kwargs: fresh)

    assert fetcher.fetch_seasons_checked(17) == fresh["seasons"]

    assert fetcher.get_seasons_for_league(17) == fresh["seasons"]
    assert fetcher.get_season_name(17, 96668) == "Premier League 26/27"
    assert SeasonFetcher(Leagues(), str(data_dir)).get_seasons_for_league(17) == fresh["seasons"]
    assert get_seasons(17) == {"seasons": fresh["seasons"], "fetched": True}
    # ST-22: liste v3 düzenine yazılır; eski dosya yerinde kalır, okunmaz
    assert sorted(p.name for p in (data_dir / "seasons").iterdir()) == ["17_seasons.json"]
    assert (data_dir / "v3" / "tournaments" / "17" / "seasons.json.gz").is_file()


def test_a_list_missing_from_the_bulk_load_is_asked_for_by_id(data_dir: Path) -> None:
    """Sezon adı dizin adına girer: liste belleğe alındıktan sonra yazılmış olsa da `Season_<id>`'ye düşülmez."""
    fetcher = SeasonFetcher(Leagues({17: "Premier League"}), str(data_dir))
    assert fetcher.league_seasons == {}
    write(data_dir, "seasons/23_Serie_A_seasons.json", season_list(231))
    reopened(data_dir)  # başka bir sürecin yazdığı liste

    assert fetcher.get_season_name(23, 231) == "S 231"
    assert fetcher.get_season_name(23, 999) == "Season_999"
    assert fetcher.get_season_name(35, 1) == "Season_1" and set(fetcher.league_seasons) == {23}


# --- maç listesi indirilmiş mi ---------------------------------------------------------------------------


def _event(event_id: int, tournament_id: int, season_id: int, start: int) -> Dict[str, Any]:
    return {
        "id": event_id, "startTimestamp": start,
        "tournament": {"name": "T", "uniqueTournament": {"id": tournament_id, "name": "T"},
                       "category": {"sport": {"slug": "football", "name": "Football"}}},
        "season": {"id": season_id, "name": f"S {season_id}", "year": "26/27"},
        "status": {"code": 100, "description": "Ended", "type": "finished"},
        "homeTeam": {"id": event_id * 10, "name": "Home"}, "awayTeam": {"id": event_id * 10 + 1, "name": "Away"},
        "homeScore": {"current": 1}, "awayScore": {"current": 0},
    }


def _downloaded(store: Store, league_id: int, season_id: int) -> Tuple[int, bool]:
    """(saklanan program sayfası, sezonun maç listesi indirilmiş mi), katalogdan (eski `SeasonFetcher._downloaded_matches`)."""
    pages = tournaments.schedule_pages(store, league_id, season_id)
    return pages, pages > 0 or tournaments.has_matches(store, league_id, season_id)


def _files_say(data_dir: Path, league_id: int, league_name: Optional[str], season: Dict[str, Any]) -> Tuple[int, bool]:
    """RD-5'ten önceki kural: ligin ve sezonun adından kurulan dizindeki sayfa dosyaları ya da özet JSON'u."""
    league_dir = data_dir / "matches" / f"{league_id}_{sf.safe_name(league_name or f'League_{league_id}')}"
    season_label = f"{season['id']}_{sf.safe_name(season.get('name', ''))}"
    season_dir = league_dir / season_label
    count = 0
    if season_dir.is_dir():
        count = len([p for p in season_dir.iterdir() if p.name.startswith(("round_", "events_")) and p.suffix == ".json"])
    return count, count > 0 or (league_dir / f"{season_label}_summary.json").exists()


def test_on_a_directory_written_by_todays_code_nothing_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """canonical: sezon listeleri dosyadakiyle, sayfa sayıları ve "indirilmiş" yanıtı dizindekiyle aynı."""
    fx = sf.build_fixture("canonical", tmp_path / "data")
    monkeypatch.setenv("DATA_DIR", str(fx.data_dir))
    store = open_store(fx.data_dir)
    fetcher = SeasonFetcher(Leagues(fx.leagues), str(fx.data_dir))
    checked = 0
    for league_id, name in fx.leagues.items():
        path = fx.data_dir / "seasons" / f"{league_id}_{sf.safe_name(name)}_seasons.json"
        on_disk = json.loads(path.read_bytes())["seasons"] if path.exists() else None
        assert tournaments.seasons_of(store, league_id) == on_disk
        assert tournaments.seasons_of(store, league_id, name=name) == on_disk
        assert fetcher.get_seasons_for_league(league_id) == (on_disk or [])
        assert fetcher.league_seasons.get(league_id) == on_disk
        assert get_seasons(league_id) == {"seasons": on_disk or [], "fetched": on_disk is not None}
        for season in on_disk or []:
            expected = _files_say(fx.data_dir, league_id, name, season)
            assert _downloaded(store, league_id, season["id"]) == expected, (league_id, season)
            assert tournaments.schedule_pages(store, league_id, season["id"]) == expected[0]
            checked += expected[1]
    assert checked >= 5  # dizinde gerçekten indirilmiş sezonlar var
    assert set(fetcher.league_seasons) == {lid for lid in fx.leagues if tournaments.seasons_of(store, lid) is not None}


def test_downloaded_seasons_are_seen_whatever_their_directory_is_called(data_dir: Path) -> None:
    """
    Eski biçimlerde düzelen durumlar: sezon dizini başka bir adla yazılmış (sezon adı bilinmeden `Season_<id>`,
    lig yapılandırılmamışken `League_<id>`) ve sayfası kalmamış, yalnızca özet CSV'si olan sezon.
    """
    write(data_dir, "seasons/17_Premier_League_seasons.json", season_list(96668, 76986, 61627, 500))
    write(data_dir, "matches/17_League_17/96668_Season_96668/round_1.json",
          {"events": [_event(1, 17, 96668, 1_790_000_000)], "_complete": True})
    write(data_dir, "matches/17_League_17/96668_Season_96668/round_2.json",
          {"events": [_event(2, 17, 96668, 1_790_100_000)], "_complete": True})
    write(data_dir, "matches/17_Premier_League/96668_S_96668/round_2.json",  # aynı sayfanın ikinci kopyası
          {"events": [_event(2, 17, 96668, 1_790_100_000)], "_complete": True}, OLDER)
    write(data_dir, "matches/17_Premier_League/76986_S_76986_summary.csv", sf.dump_csv(sf.SUMMARY_COLUMNS, [
        sf.summary_row(1, _event(3, 17, 76986, 1_760_000_000)),
    ]))
    # 61627: yalnızca tek bir maçın kendi detayı var (maç listesi indirilmedi)
    write(data_dir, "match_details/17_Premier_League/season_S_61627/4/basic.json", _event(4, 17, 61627, 1_730_000_000))
    store = open_store(data_dir)
    fetcher = SeasonFetcher(Leagues({17: "Premier League"}), str(data_dir))

    assert tournaments.schedule_pages(store, 17, 96668) == 2
    assert _downloaded(store, 17, 96668) == (2, True)
    assert _downloaded(store, 17, 76986) == (0, True)
    assert (tournaments.schedule_pages(store, 17, 76986), tournaments.has_matches(store, 17, 76986)) == (0, True)
    assert _downloaded(store, 17, 61627) == (0, False)
    assert _downloaded(store, 17, 500) == (0, False)
    assert fetcher.get_seasons_for_league(17)


def test_sport_of_a_tournament_comes_from_the_payloads_not_from_directory_names(data_dir: Path) -> None:
    """
    Maçın turnuvası yükünde yazandır. Eskiden yalnızca `match_details/<lig id>_*/` altındaki maçlara bakılırdı:
    kimliksiz lig dizinindeki ve `_no_tournament/` altındaki maçların ligi spor alamazdı.
    """
    basket = _event(21, 132, 80229, 1_790_000_000)
    basket["tournament"]["category"]["sport"] = {"slug": "basketball", "name": "Basketball"}
    volley = _event(22, 777, 5, 1_790_000_000)
    volley["tournament"]["category"]["sport"] = {"slug": "waterpolo", "name": "Waterpolo"}
    write(data_dir, "match_details/NBA/season_NBA_26_27/21/basic.json", basket)  # lig dizininde kimlik yok
    write(data_dir, "match_details/_no_tournament/football/23/basic.json", _event(23, 17, 96668, 1_790_000_000))
    write(data_dir, "match_details/777_CEV/season_CEV/22/basic.json", volley)
    store = open_store(data_dir)

    assert tournaments.sport_of(store, 132) == "basketball"
    assert tournaments.sport_of(store, 17) == "football"
    assert tournaments.sport_of(store, 777) is None  # kayıtlı bir spor değil (sofascore_scraper/sports.py)
    assert tournaments.sport_of(store, 8) is None
    assert league_sports.infer_from_data(str(data_dir), 132) == "basketball"
    assert league_sports.infer_from_data(str(data_dir), 2 ** 70) is None  # kataloğun tutamayacağı bir kimlik


def test_sports_for_prefers_the_stored_sport_and_writes_nothing(data_dir: Path, tmp_path: Path) -> None:
    write(data_dir, "match_details/17_Premier_League/season_S/23/basic.json", _event(23, 17, 96668, 1_790_000_000))
    write(data_dir, "match_details/8_LaLiga/season_S/24/basic.json", _event(24, 8, 97532, 1_790_000_000))
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    leagues_file = str(config_dir / "leagues.txt")
    sidecar = config_dir / "league_sports.json"

    assert league_sports.sports_for(leagues_file, str(data_dir), [17, 8, 35]) == {17: "football", 8: "football", 35: None}
    assert sorted(p.name for p in config_dir.iterdir()) == []  # öğrenilen spor dosyaya yazılmaz

    sidecar.write_bytes(b'{"17": "Tennis", "999": "basketball", "not-an-id": "football", "35": "curling"}')
    before = sidecar.read_bytes()
    assert league_sports.sports_for(leagues_file, str(data_dir), [17, 8, 35]) == {17: "tennis", 8: "football", 35: None}
    assert sidecar.read_bytes() == before and sorted(p.name for p in config_dir.iterdir()) == ["league_sports.json"]


def test_a_store_that_cannot_be_opened_leaves_the_sport_unknown(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Lig listesi yapılandırmadır: katalog okunamıyor diye sporu soran düşmez, spor bilinmiyor kalır."""
    def refuse(*args: Any, **kwargs: Any) -> Store:
        raise StoreError("state.db is newer than this build")

    monkeypatch.setattr("sofascore_scraper.store.open_store", refuse)

    assert league_sports.infer_from_data(str(data_dir), 17) is None
    assert "Sport of league 17 could not be read from the catalog" in caplog.text
