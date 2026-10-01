"""
Kataloğun okuma API'si: `Store.events` (EventStore), `Store.entities` (EntityStore) ve `Store.changes`
(ChangeLog) (plan maddesi ST-30; docs/design/01-storage.md bölüm 2.3, 3.7, 6.3, 8.2-8.5).

Testler `tests/store_fixtures.py`'nin kurduğu veri dizinlerinde çalışır; katalog dizinleyiciyle
(`CatalogAdmin.rebuild`) kurulur, çünkü `open_store` onu kurmaz. Beklenen değerler fixture tanımlarından elle
çıkarılmıştır (hangi maçın hangi dilimi, hangi işareti, hangi gözlemi var: `CANONICAL_DETAILS`).

Dört küme:

  * her yöntem, elle denetlenmiş beklentilere karşı (kanonik dizin);
  * dosya tabanlı okuyucularla eşitlik: `missing()` = `_needs_detail_fetch == 'refill'` kümesi,
    `refresh_candidates()` = `refresh_due` (ve `refresh_due_ids`), rastgele işaret ve gözlem durumlarında da;
  * yük okurken dosyanın yer değiştirmesi (bölüm 6.3'teki bir kez yeniden deneme);
  * sorgu planları: API'nin gerçekten çalıştırdığı SQL, bölüm 3.7'deki dizinleri kullanır.

Ağ yok.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import random
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple
from unittest.mock import MagicMock

import pytest

import src.store
import store_fixtures as sf
import test_store_query_plans as plans
from src import refresh
from src.match_data_fetcher import MatchDataFetcher
from src.sports import event_sport_slug, slices_for
from src.store import (
    ChangeLog,
    ChangeRow,
    EntityStore,
    EventQuery,
    EventRow,
    EventState,
    EventStore,
    FollowSpec,
    LayoutError,
    MissingRow,
    Page,
    ParticipantRow,
    PayloadCorrupt,
    PayloadMissing,
    Ref,
    Scope,
    SeasonRow,
    SliceError,
    SliceInfo,
    Store,
    StoreError,
    TournamentRow,
    TournamentSummary,
    open_store,
)
from src.store import codec, layout, manifest
from src.store import events as events_mod
from src.store import sqlite as sqlite_mod
from src.store.indexer import CatalogAdmin
from src.store.legacy import LegacyReader
from src.store.manifest import SliceEntry
from test_store_indexer import promote

ROOT = Path(__file__).resolve().parent.parent
UTC = dt.timezone.utc
NOW = sf.FIXTURE_NOW
BASE = sf.BASE_MTIME
HOUR = 3600
WINDOW_S = 72 * HOUR  # src/refresh.py varsayılanları
MIN_INTERVAL_S = 6 * HOUR

ARS = sf.event_id(sf.PL_ARS)  # kesin kayıt, bütün dilimler tam
LIV = sf.event_id(sf.PL_LIV)  # geçici kayıt: yenilenir
LEE = sf.event_id(sf.PL_LEE)  # gözlemsiz eski kayıt
BRE = sf.event_id(sf.PL_BRE)
AVL = sf.event_id(sf.PL_AVL)  # lineups eşiğin altında: beklenir
NEW = sf.event_id(sf.PL_NEW)  # statistics başarısız: beklenir
TOT = sf.event_id(sf.PL_TOT)  # h2h başarısız + yenilenecek gözlem
CRY = sf.event_id(sf.PL_CRY)  # karışık işaretler
NO_DETAIL = sf.event_id(sf.PL_NO_DETAIL)  # yalnızca listede: bitmiş
NOT_STARTED = sf.event_id(sf.PL_NOT_STARTED)  # yalnızca listede: başlamamış
FUTURE = sf.event_id(sf.PL_FUTURE)
NBA_B = sf.event_id(sf.NBA_B)  # içinde veri olmayan dilim dosyaları
NBA_VOID = sf.event_id(sf.NBA_VOID)  # sonradan iptal: liste ile çelişir (stale)
WIM_A = sf.event_id(sf.WIM_A)
WIM_B = sf.event_id(sf.WIM_B)
WIM_TB = sf.event_id(sf.WIM_TB)
LIGA_NEXT = sf.event_id(sf.LIGA_NEXT)  # başlamamış maçın detayı
CUP_AET = sf.event_id(sf.CUP_AET)
CUP_PEN_2 = 17090707  # FA Cup yarı finalinin detayı olmayan maçı (Arsenal - Tottenham)

PL_DETAILS = "match_details/17_Premier_League/season_Premier_League_26_27"
PL_MATCHES = "matches/17_Premier_League/96668_Premier_League_26_27"
REQUIRED = {"": [detail.key for detail in slices_for(None, required_only=True)]}
FINISHED = ("completed", "decided_without_play")

# Kanonik dizinin maçları, başlangıç zamanı (eşitlikte kimlik) sırasıyla: fixture tanımlarından
CANONICAL_ORDER = [
    14025001, 14025002, 16484334, 17078471, 17099711, 17092269, 17081861, 16837335, 16950622, 16872361, 16346148,
    17058663, 17090707, 17060394, 16951514, 16990001, 17102381, 16539815, 16599919, 16425949, 17148292, 16837399,
    17148332, 17204710, 17206241, 17203939, 17018572, 17184988, 17018554, 17184998, 17185003, 17207542, 16867839,
    17211671, 17211681,
]
LISTING_ONLY = {14025002, 16346148, 16425949, 16539815, 16599919, 16837399, 17058663, 17090707, 17148292, 17184998,
                17207542, 17211671}


# --- yardımcılar --------------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _default_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("REFRESH_WINDOW_HOURS", "REFRESH_MIN_INTERVAL_HOURS", "REFRESH_LEGACY", "FETCH_ONLY_FINISHED"):
        monkeypatch.delenv(key, raising=False)


def build_catalog(data_dir: Path, leagues: Optional[Dict[int, str]] = None) -> None:
    """Kataloğu dosyalardan kurar (bunu ileride `open_store` yapacak; bugün dizinleyici ayrı çağrılır)."""
    store = open_store(data_dir)
    try:
        CatalogAdmin(store.data_dir, store._catalog, clock=lambda: float(NOW), league_names=leagues).rebuild()
    finally:
        store.close()


def admin_of(store: Store, leagues: Optional[Dict[int, str]] = None) -> CatalogAdmin:
    return CatalogAdmin(store.data_dir, store._catalog, clock=lambda: float(NOW), league_names=leagues)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Dict[str, sf.LegacyFixture]:
    """Dört fixture dizini, katalogları kurulmuş halde. Bu dizinlere yazan test kendi kopyasını kurar."""
    out: Dict[str, sf.LegacyFixture] = {}
    for name in sf.FIXTURE_NAMES:
        fx = sf.build_fixture(name, tmp_path_factory.mktemp(name) / "data")
        build_catalog(fx.data_dir, fx.leagues)
        out[name] = fx
    return out


@pytest.fixture
def canon(built: Dict[str, sf.LegacyFixture]) -> Store:
    return open_store(built["canonical"].data_dir)


@pytest.fixture
def old(built: Dict[str, sf.LegacyFixture]) -> Store:
    return open_store(built["legacy"].data_dir)


@pytest.fixture
def own(tmp_path: Path) -> Tuple[sf.LegacyFixture, Store]:
    """Testin değiştirebileceği kanonik dizin."""
    fx = sf.build_fixture("canonical", tmp_path / "data")
    build_catalog(fx.data_dir, fx.leagues)
    return fx, open_store(fx.data_dir)


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dosya tabanlı yenileme kararı `time.time()`'a bakar (src/refresh.py): saat FIXTURE_NOW'da durur."""
    monkeypatch.setattr(time, "time", lambda: float(NOW))


def fetcher_of(data_dir: Path) -> MatchDataFetcher:
    return MatchDataFetcher(config_manager=MagicMock(), data_dir=str(data_dir))


def ids(rows: Sequence[Any]) -> List[int]:
    return [row.id for row in rows]


def all_pages(store: Store, q: EventQuery) -> List[int]:
    """Sorgunun bütün sayfaları, konumla ilerleyerek."""
    out: List[int] = []
    cursor: Optional[str] = None
    for _ in range(1000):
        page = store.events.list(dataclasses.replace(q, cursor=cursor))
        out.extend(ids(page.items))
        cursor = page.next_cursor
        if cursor is None:
            return out
    raise AssertionError("sayfalama bitmiyor")


def epoch(text: str) -> int:
    return int(dt.datetime.fromisoformat(text).timestamp())


def at(seconds: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(seconds, UTC)


# --- cephe ve dışa açılan adlar -----------------------------------------------------------------------

WRITE_METHODS = ("put", "observe", "reset_empty_markers", "delete", "append")  # sonraki adımlarda eklenir


def test_store_has_the_event_store(canon: Store) -> None:
    names = {"EventStore", "Ref", "Scope", "EventQuery", "Page", "EventRow", "EventState", "SliceInfo", "SliceError",
             "MissingRow", "TournamentSummary"}

    assert isinstance(canon.events, EventStore)
    assert names <= set(src.store.__all__)
    assert src.store.EventStore is events_mod.EventStore
    assert not any(hasattr(EventStore, name) for name in WRITE_METHODS)


def test_an_unbuilt_catalog_answers_empty(tmp_path: Path) -> None:
    """`open_store` kataloğun şemasını yaratır ama kurmaz: sorular hata vermez, boş döner."""
    fx = sf.build_fixture("canonical", tmp_path / "data")
    store = open_store(fx.data_dir)

    assert store.info(sizes=False).catalog_rebuild_reason == "derive_version"
    assert store.events.get(ARS) is None
    assert store.events.list(EventQuery(), with_total=True) == Page((), None, 0)
    assert list(store.events.iter(EventQuery())) == [] and store.events.count(EventQuery()) == 0
    assert store.events.payload(ARS) is None and store.events.payloads(ARS) == {}
    assert store.events.slices(ARS) == [] and store.events.slice(ARS, "statistics").state == "not_requested"
    assert list(store.events.states()) == [] and list(store.events.missing(None, REQUIRED)) == []
    assert store.events.refresh_candidates(now=NOW, window_s=WINDOW_S, min_interval_s=MIN_INTERVAL_S,
                                           include_unobserved=True) == []
    assert store.events.stale() == [] and store.events.open_events(started_before=NOW) == []
    assert store.events.summary() == []


def test_a_closed_store_refuses_reads(canon: Store) -> None:
    canon.close()

    for call in (lambda: canon.events.get(ARS), lambda: canon.events.list(EventQuery()),
                 lambda: canon.events.payload(ARS), lambda: canon.events.slices(ARS),
                 lambda: list(canon.events.states()), lambda: canon.events.summary()):
        with pytest.raises(StoreError):
            call()


def test_read_modules_import_only_what_the_store_may_import() -> None:
    """Bölüm 2.1: Store yalnızca src.sports, src.status, src.slices, src.exceptions ve src.version'ı içe aktarabilir."""
    code = (
        "import sys, json; import src.store.events; "
        "print(json.dumps(sorted(m for m in sys.modules if m == 'src' or m.startswith('src.'))))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    loaded = set(json.loads(out.stdout))

    assert {m for m in loaded if not m.startswith("src.store")} == {
        "src", "src.exceptions", "src.slices", "src.sports", "src.status"}
    assert not loaded & {"src.store.api", "src.store.indexer", "src.store.entities", "src.store.state"}


# --- ortak türler -------------------------------------------------------------------------------------

def test_ref_constructors_and_validation() -> None:
    assert Ref.event(5) == Ref("event", 5)
    assert Ref.tournament(17) == Ref("tournament", 17)
    assert Ref.season(17, 96668) == Ref("season", 96668, 17)
    assert Ref.team(3928) == Ref("team", 3928) and Ref.player(1) == Ref("player", 1) and Ref.sport(1) == Ref("sport", 1)
    for bad in (lambda: Ref("league", 1), lambda: Ref("event", "5"),  # type: ignore[arg-type]
                lambda: Ref("event", True), lambda: Ref("season", 1, "17")):  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            bad()


def test_event_row_mirrors_the_events_table(canon: Store) -> None:
    columns = [row[1] for row in canon._catalog.connection().execute("PRAGMA main.table_info(events)")]

    assert [f.name for f in dataclasses.fields(EventRow)] == columns == list(events_mod.EVENT_COLUMNS)


def test_cursor_round_trip_and_rejections() -> None:
    for sort in events_mod.SORTS:
        for position in ((1789495200, ARS), (None, 7), (-5, 0)):
            assert events_mod.decode_cursor(events_mod.encode_cursor(sort, *position), sort) == position
    desc = events_mod.encode_cursor("start_desc", 10, 1)
    for cursor, sort in ((desc, "start_asc"), ("", "start_desc"), ("not-a-cursor", "start_desc"),
                         ("AAAA", "start_desc"), ("WzFd", "start_desc"), (5, "start_desc")):
        with pytest.raises(ValueError, match="cursor"):
            events_mod.decode_cursor(cursor, sort)  # type: ignore[arg-type]


def test_like_pattern_folds_and_escapes() -> None:
    assert events_mod.like_pattern("  Beşiktaş ") == "%besiktas%"
    assert events_mod.like_pattern("50%_a\\b") == "%50\\%\\_a\\\\b%"
    assert events_mod.like_pattern("   ") is None and events_mod.like_pattern(None) is None


# --- EventStore.get -----------------------------------------------------------------------------------

def test_get_an_event_with_a_payload(canon: Store) -> None:
    row = canon.events.get(ARS)
    payload = sf.basic_payload(sf.PL_ARS)

    assert row is not None
    assert (row.id, row.sport, row.tournament_id, row.season_id, row.round) == (ARS, "football", 17, 96668, 1)
    assert (row.status_type, row.status_code, row.status_class) == ("finished", 100, "completed")
    assert (row.home_name, row.away_name) == ("Arsenal", "Chelsea")
    assert row.start_ts == payload["startTimestamp"] == 1789495200
    assert (row.home_score_current, row.away_score_current) == (
        payload["homeScore"]["current"], payload["awayScore"]["current"])
    assert row.winner_code == payload["winnerCode"]
    assert (row.row_source, row.has_event_payload, row.layout) == ("event", True, "legacy")
    assert row.path == f"{PL_DETAILS}/{ARS}"
    assert row.legacy_path is None and row.listed_in == "round_1"
    assert row.observed_at == epoch("2026-09-19T06:00:00+00:00") and row.observed_gap == row.observed_at - row.start_ts
    assert (row.stale, row.status_regressed) == (False, False)
    assert row.first_seen_at == row.updated_at == BASE
    scores = row.scores()
    assert scores is not None and scores["family"] == "football"


def test_get_a_listing_only_event_and_an_unknown_one(canon: Store) -> None:
    row = canon.events.get(NO_DETAIL)

    assert row is not None
    assert (row.row_source, row.has_event_payload) == ("listing", False)
    assert (row.layout, row.path, row.sig) == (None, None, None)
    assert (row.tournament_id, row.season_id, row.listed_in, row.status_class) == (17, 96668, "round_2", "completed")
    assert (row.home_name, row.away_name) == ("Manchester City", "Hull City")
    assert row.observed_at is None and row.observed_gap is None
    assert canon.events.get(1) is None
    with pytest.raises(ValueError):
        canon.events.get("17")  # type: ignore[arg-type]


def test_get_reports_the_sticky_flags(canon: Store) -> None:
    row = canon.events.get(NBA_VOID)

    assert row is not None
    assert (row.status_class, row.status_regressed, row.stale) == ("void", True, True)


# --- EventStore.list / count / iter --------------------------------------------------------------------

def test_list_default_order_is_newest_first(canon: Store) -> None:
    page = canon.events.list(EventQuery(limit=5), with_total=True)

    assert ids(page.items) == CANONICAL_ORDER[::-1][:5]
    assert page.total == len(CANONICAL_ORDER) == 35
    assert page.next_cursor is not None
    assert all(isinstance(row, EventRow) for row in page.items)
    assert canon.events.list(EventQuery(limit=5)).total is None


@pytest.mark.parametrize("limit", [1, 2, 7, 34, 35, 36, 500])
def test_keyset_pages_cover_every_row_once_in_both_orders(canon: Store, limit: int) -> None:
    assert all_pages(canon, EventQuery(limit=limit)) == CANONICAL_ORDER[::-1]
    assert all_pages(canon, EventQuery(limit=limit, sort="start_asc")) == CANONICAL_ORDER


def test_the_last_page_has_no_cursor(canon: Store) -> None:
    exact = canon.events.list(EventQuery(limit=35))
    short = canon.events.list(EventQuery(scope=Scope(tournament_ids=[19]), limit=50))

    assert len(exact.items) == 35 and exact.next_cursor is None
    assert ids(short.items) == [17148332, 17090707, 16950622] and short.next_cursor is None


def test_offset_mode_is_the_old_paging(canon: Store) -> None:
    newest_first = CANONICAL_ORDER[::-1]
    page = canon.events.list(EventQuery(limit=10, offset=20), with_total=True)

    assert ids(page.items) == newest_first[20:30]
    assert page.next_cursor is None and page.total == 35
    assert ids(canon.events.list(EventQuery(limit=10, offset=0)).items) == newest_first[:10]
    assert canon.events.list(EventQuery(limit=10, offset=35), with_total=True) == Page((), None, 35)
    assert ids(canon.events.list(EventQuery(limit=10, offset=30, sort="start_asc")).items) == CANONICAL_ORDER[30:]


def test_iter_walks_everything_in_batches(canon: Store) -> None:
    for batch in (1, 4, 35, 1000):
        assert ids(list(canon.events.iter(EventQuery(), batch=batch))) == CANONICAL_ORDER[::-1]
        assert ids(list(canon.events.iter(EventQuery(sort="start_asc"), batch=batch))) == CANONICAL_ORDER
    # limit ve offset yok sayılır; cursor verilirse oradan sürer
    first = canon.events.list(EventQuery(limit=10))
    rest = canon.events.iter(EventQuery(limit=3, offset=7, cursor=first.next_cursor), batch=6)
    assert ids(list(rest)) == CANONICAL_ORDER[::-1][10:]
    football = canon.events.iter(EventQuery(scope=Scope(sport="basketball"), sort="start_asc"), batch=2)
    assert ids(list(football)) == [16484334, 17092269, 16346148, 17060394, 17102381, 17203939]


def _matching(canon: Store, **query: Any) -> List[int]:
    q = EventQuery(sort="start_asc", limit=500, **query)
    found = ids(canon.events.list(q).items)
    assert canon.events.count(q) == len(found)
    assert all_pages(canon, dataclasses.replace(q, limit=2)) == found
    return found


def test_scope_filters(canon: Store) -> None:
    assert _matching(canon, scope=Scope(sport="basketball")) == [
        16484334, 17092269, 16346148, 17060394, 17102381, 17203939]
    assert len(_matching(canon, scope=Scope(sport="football"))) == 23
    assert len(_matching(canon, scope=Scope(sport="tennis"))) == 6
    assert _matching(canon, scope=Scope(sport="handball")) == []
    assert _matching(canon, scope=Scope(tournament_ids=[19])) == [16950622, 17090707, 17148332]
    assert _matching(canon, scope=Scope(tournament_ids=[19, 132])) == [
        16484334, 17092269, 16950622, 16346148, 17090707, 17060394, 17102381, 17148332, 17203939]
    assert _matching(canon, scope=Scope(season_ids=[76986])) == [14025001, 14025002]
    assert _matching(canon, scope=Scope(tournament_ids=[19], season_ids=[76986])) == []  # alanlar birlikte uygulanır
    assert _matching(canon, scope=Scope(event_ids=[CRY, ARS, 1])) == [ARS, CRY]
    assert _matching(canon, scope=Scope(sport="football", event_ids=[ARS, NBA_B])) == [ARS]


def test_scope_by_participant(canon: Store) -> None:
    arsenal = canon.events.get(ARS).home_id  # type: ignore[union-attr]
    chelsea = canon.events.get(ARS).away_id  # type: ignore[union-attr]

    # Arsenal: ligde Chelsea ile, kupada Tottenham ile (yalnızca listede) ve finalde Manchester City ile
    assert _matching(canon, scope=Scope(participant_ids=[arsenal])) == [ARS, CUP_PEN_2, CUP_AET]
    # Chelsea: Arsenal maçı ve henüz oynanmamış Liverpool maçı (yalnızca listede)
    assert _matching(canon, scope=Scope(participant_ids=[chelsea])) == [ARS, FUTURE]
    assert _matching(canon, scope=Scope(participant_ids=[arsenal, chelsea])) == [ARS, CUP_PEN_2, CUP_AET, FUTURE]


def test_scope_followed_joins_the_follows_table(own: Tuple[sf.LegacyFixture, Store]) -> None:
    _, store = own
    assert store.events.count(EventQuery(scope=Scope(followed=True))) == 0

    store.follows.apply([FollowSpec("tournament", 19, "FA Cup"), FollowSpec("tournament", 132, "NBA", enabled=False),
                         FollowSpec("team", 3928, "Arsenal")], origin="api")

    # yalnızca etkin turnuva takipleri: kapalı NBA takibi ve takım takibi kapsamı genişletmez
    assert _matching(store, scope=Scope(followed=True)) == [16950622, 17090707, 17148332]
    assert _matching(store, scope=Scope(followed=True, sport="basketball")) == []
    store.follows.update("tournament", 132, enabled=True)
    assert len(_matching(store, scope=Scope(followed=True))) == 9
    assert sorted(s.event.id for s in store.events.states(Scope(followed=True, sport="football"))) == [
        16950622, 17090707, 17148332]


def test_status_filter(canon: Store) -> None:
    void = [NBA_VOID, 16539815, 16599919, 16425949, 17148292]

    assert len(_matching(canon, status_classes=FINISHED)) == 27
    assert _matching(canon, status_classes=("void",)) == void
    assert _matching(canon, status_classes=("not_started",)) == [NOT_STARTED, FUTURE, LIGA_NEXT]
    assert _matching(canon, status_classes=("not_started", "unknown", "live")) == [NOT_STARTED, FUTURE, LIGA_NEXT]
    assert _matching(canon, status_classes=("live",)) == []
    assert _matching(canon, status_classes=("void", "not_started")) == sorted(
        void + [NOT_STARTED, FUTURE, LIGA_NEXT], key=CANONICAL_ORDER.index)
    assert len(_matching(canon, status_classes=tuple(events_mod._STATUS_ORDER))) == 35
    with pytest.raises(ValueError, match="status_classes"):
        canon.events.count(EventQuery(status_classes=("finished",)))
    with pytest.raises(ValueError, match="status_classes"):
        canon.events.count(EventQuery(status_classes="void"))  # type: ignore[arg-type]


def test_time_round_details_and_update_filters(canon: Store) -> None:
    # iki uç da dahil
    assert _matching(canon, start_from=1790686800, start_to=1790688600) == [BRE, NOT_STARTED, AVL, 17207542, LEE]
    assert _matching(canon, start_from=1790686801, start_to=1790688599) == [AVL, 17207542]
    assert _matching(canon, start_from=1791306000) == [LIGA_NEXT]
    assert _matching(canon, start_to=1779595200) == [14025001]
    assert _matching(canon, scope=Scope(season_ids=[96668]), round=1) == [LIV, ARS, BRE, LEE]
    assert _matching(canon, round=38) == [14025001, 14025002]
    assert set(_matching(canon, has_details=False)) == LISTING_ONLY
    assert len(_matching(canon, has_details=True)) == 23
    assert _matching(canon, has_details=False, status_classes=("not_started",)) == [NOT_STARTED, FUTURE]
    # her dosyanın zamanı BASE: ondan sonra güncellenen satır yok
    assert len(_matching(canon, updated_after=BASE - 1)) == 35 and _matching(canon, updated_after=BASE) == []


def test_text_search_is_case_and_accent_insensitive(canon: Store, old: Store) -> None:
    assert _matching(canon, text="arsenal") == _matching(canon, text="  ARSENAL ") == [ARS, CUP_PEN_2, CUP_AET]
    assert _matching(canon, text="manchester") == [16950622, 16837399, CUP_AET, NOT_STARTED]
    assert _matching(canon, text="hove alb") == [16872361]  # "Brighton & Hove Albion"
    assert _matching(canon, text="arsenal", scope=Scope(tournament_ids=[19])) == [CUP_PEN_2, CUP_AET]
    assert _matching(canon, text="zzz") == []
    assert _matching(canon, text="%") == [] and _matching(canon, text="_") == []  # joker değil, düz metin
    assert len(_matching(canon, text="")) == 35  # boş metin süzmez
    # "Atlético Madrid": aksansız ve küçük harfle de bulunur
    assert ids(old.events.list(EventQuery(text="atletico")).items) == [15000001]
    assert ids(old.events.list(EventQuery(text="ATLÉTICO MAD")).items) == [15000001]


def test_text_search_does_not_see_rows_without_participant_ids(old: Store) -> None:
    """Özet CSV'sinden gelen liste satırında takım kimliği yoktur: adı yalnızca satırın kendisindedir."""
    row = old.events.get(14025001)

    assert row is not None and (row.home_name, row.home_id) == ("Southampton", None)
    assert old.events.count(EventQuery(text="southampton")) == 0


def test_list_argument_errors(canon: Store) -> None:
    cursor = canon.events.list(EventQuery(limit=1)).next_cursor
    for query in (EventQuery(limit=0), EventQuery(limit=-1), EventQuery(limit=True), EventQuery(sort="newest"),
                  EventQuery(offset=-1), EventQuery(offset=0, cursor=cursor), EventQuery(cursor="x"),
                  EventQuery(cursor=cursor, sort="start_asc"), EventQuery(round="1"),  # type: ignore[arg-type]
                  EventQuery(start_from="yesterday"),  # type: ignore[arg-type]
                  EventQuery(scope=Scope(tournament_ids=["17"])),  # type: ignore[list-item]
                  EventQuery(scope=Scope(event_ids="17")),  # type: ignore[arg-type]
                  EventQuery(scope=Scope(sport=17))):  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            canon.events.list(query)
    with pytest.raises(ValueError):
        list(canon.events.iter(EventQuery(), batch=0))


def _synthetic_events(store: Store, rows: Sequence[Tuple[int, Optional[int]]]) -> None:
    with store._catalog.write():
        store._catalog.upsert("events", [
            {"id": event_id, "status_class": "completed", "start_ts": start, "row_source": "listing",
             "first_seen_at": 1, "updated_at": 1} for event_id, start in rows])


@pytest.mark.parametrize("limit", [1, 2, 3, 4, 5, 9, 10])
def test_rows_without_a_start_time_are_paged_too(tmp_path: Path, limit: int) -> None:
    """
    Başlangıcı bilinmeyen satırlar azalan sırada sonda, artan sırada baştadır (kimlik sırasıyla); sayfa
    sınırı tarihli ve tarihsiz bölgenin içinde, arasında ve eşit zamanlı satırların ortasında olabilir.
    """
    store = open_store(tmp_path / "data")
    _synthetic_events(store, [(1, 100), (2, 100), (3, 100), (4, None), (5, 50), (6, None), (7, 200), (8, None),
                              (9, 50)])
    ascending = [4, 6, 8, 5, 9, 1, 2, 3, 7]

    assert all_pages(store, EventQuery(limit=limit, sort="start_asc")) == ascending
    assert all_pages(store, EventQuery(limit=limit)) == [7, 3, 2, 1, 9, 5, 8, 6, 4]
    assert ids(list(store.events.iter(EventQuery(sort="start_asc"), batch=limit))) == ascending
    assert ids(list(store.events.iter(EventQuery(), batch=limit))) == [7, 3, 2, 1, 9, 5, 8, 6, 4]
    assert ids(store.events.list(EventQuery(limit=9, offset=0, sort="start_asc")).items) == ascending
    assert all_pages(store, EventQuery(limit=limit, start_from=60)) == [7, 3, 2, 1]  # süzgeç tarihsizleri eler


def test_only_undated_rows(tmp_path: Path) -> None:
    store = open_store(tmp_path / "data")
    _synthetic_events(store, [(3, None), (1, None), (2, None)])

    assert all_pages(store, EventQuery(limit=2)) == [3, 2, 1]
    assert all_pages(store, EventQuery(limit=2, sort="start_asc")) == [1, 2, 3]


def test_reads_from_several_threads(canon: Store) -> None:
    """Her iş parçacığı kataloğa kendi bağlantısıyla gider: eşzamanlı okumalar birbirini bozmaz."""
    expected = (CANONICAL_ORDER[::-1], sf.basic_payload(sf.PL_ARS), len(canon.events.slices(CRY)))
    failures: List[BaseException] = []

    def reader() -> None:
        try:
            for _ in range(8):
                pages = all_pages(canon, EventQuery(limit=9))
                assert (pages, canon.events.payload(ARS), len(canon.events.slices(CRY))) == expected
                assert sum(1 for _ in canon.events.states(batch=10)) == 35
        except BaseException as exc:  # iş parçacığındaki hata ana iş parçacığına taşınır
            failures.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []


# --- yükler -------------------------------------------------------------------------------------------

def test_payload_of_a_legacy_event(built: Dict[str, sf.LegacyFixture], canon: Store) -> None:
    directory = built["canonical"].data_dir / PL_DETAILS / str(ARS)
    basic = (directory / "basic.json").read_bytes()
    statistics = (directory / "statistics.json").read_bytes()

    assert canon.events.payload(ARS) == json.loads(basic) == sf.basic_payload(sf.PL_ARS)
    assert canon.events.payload(ARS, "event", raw=True) == basic  # dosyadaki baytlar, olduğu gibi
    assert canon.events.payload(ARS, "statistics") == json.loads(statistics)
    assert canon.events.payload(ARS, "statistics", raw=True) == statistics


def test_payload_is_none_when_the_catalog_has_none(canon: Store) -> None:
    assert canon.events.payload(1) is None  # bilinmeyen maç
    assert canon.events.payload(NO_DETAIL) is None  # yalnızca listeden bilinen
    assert canon.events.payload(CRY, "lineups") is None  # "veri yok" işareti var, dosya yok
    assert canon.events.payload(ARS, "point_by_point") is None  # satırı olmayan dilim
    assert canon.events.payload(ARS, "statistics", "home") is None  # eski düzende alt anahtar yok
    assert canon.events.payload(ARS, "basic") is None  # eski dosya adı bir dilim anahtarı değildir
    assert canon.events.payload(NO_DETAIL, raw=True) is None


def test_payload_of_a_slice_file_without_data_is_still_returned(canon: Store) -> None:
    """Dilim dosyası var ama içinde veri yok: durum `empty`, yük yine de saklıdır (boş 200 gövdesi)."""
    info = canon.events.slice(NBA_B, "lineups")

    assert (info.state, info.has_payload, info.empty_count) == ("empty", True, 2)
    assert canon.events.payload(NBA_B, "lineups") == sf.slice_payload("lineups", sf.basic_payload(sf.NBA_B), empty=True)


@pytest.mark.parametrize("key, sub", [("../basic", ""), ("Statistics", ""), ("", ""), ("statistics", "../x"),
                                      ("statistics", "A"), ("con", ""), ("statistics", "_")])
def test_payload_rejects_names_that_are_not_slice_names(canon: Store, key: str, sub: str) -> None:
    with pytest.raises(LayoutError):
        canon.events.payload(ARS, key, sub)
    with pytest.raises(LayoutError):
        canon.events.slice(ARS, key, sub)


def test_payloads_of_a_legacy_event(canon: Store) -> None:
    found = canon.events.payloads(ARS)
    basic = sf.basic_payload(sf.PL_ARS)

    assert list(found) == ["event", "h2h", "incidents", "lineups", "pregame_form", "statistics", "team_streaks"]
    assert found["event"] == basic
    assert all(found[key] == sf.slice_payload(key, basic) for key in sf.REQUIRED_SLICES)
    assert list(canon.events.payloads(ARS, ["event", "h2h", "point_by_point"])) == ["event", "h2h"]
    assert list(canon.events.payloads(CRY)) == ["event", "h2h", "statistics", "team_streaks"]
    assert set(canon.events.payloads(WIM_A)) == {"event", "statistics", "team_streaks", "h2h", "point_by_point"}
    assert canon.events.payloads(NO_DETAIL) == {} and canon.events.payloads(1) == {}
    assert canon.events.payloads(ARS, []) == {}


def test_payloads_in_the_old_forms(built: Dict[str, sf.LegacyFixture], old: Store) -> None:
    """Birleşik dosya (L4), düz dizin (L3), yalnızca birleşik dosya, yarım kalmış dilim dosyası."""
    data_dir = built["legacy"].data_dir
    basic = sf.basic_payload(sf.PL_LIV)

    # basic.json + bütün dilimleri tutan tek dosya: dilimler birleşik dosyadan gelir
    assert old.events.payload(LIV) == basic
    assert old.events.payload(LIV, "statistics") == sf.slice_payload("statistics", basic)
    assert old.events.payload(LIV, "statistics", raw=True) == codec.canonical_bytes(
        sf.slice_payload("statistics", basic))
    assert set(old.events.payloads(LIV)) == {"event", *sf.REQUIRED_SLICES}
    # düz dizin
    assert old.events.get(LEE).path == f"match_details/{LEE}"  # type: ignore[union-attr]
    assert old.events.payload(LEE, raw=True) == (data_dir / "match_details" / str(LEE) / "basic.json").read_bytes()
    # basic.json'sız, yalnızca birleşik dosya: olay yükü oradaki `basic` anahtarıdır
    assert old.events.payload(BRE) == sf.basic_payload(sf.PL_BRE)
    assert old.events.payload(BRE, raw=True) == codec.canonical_bytes(sf.basic_payload(sf.PL_BRE))
    # yarıda kesilmiş dilim dosyası yalnızca o dilimi etkiler: katalogda yükü yoktur
    info = old.events.slice(AVL, "statistics")
    assert (info.state, info.has_payload) == ("error", False)
    assert info.error is not None and info.error.reason == "corrupt"
    assert old.events.payload(AVL, "statistics") is None
    assert "statistics" not in old.events.payloads(AVL) and "h2h" in old.events.payloads(AVL)


def _promote(store: Store, event_id: int, *, remove_legacy: bool = False) -> None:
    """Maçın v3 kopyasını kurar ve kataloğa işler; `remove_legacy`: eski dizin silinir (taşımanın sonu)."""
    row = store.events.get(event_id)
    assert row is not None and row.path is not None
    reader = LegacyReader(store.data_dir)
    candidate = reader.event_dir_at(row.path)
    assert candidate is not None
    promote(store.data_dir, reader.read_event(candidate))
    if remove_legacy:
        shutil.rmtree(store.data_dir / row.path)
    assert admin_of(store).index_event(event_id) == "v3"


def test_payload_of_a_v3_event(own: Tuple[sf.LegacyFixture, Store]) -> None:
    _, store = own
    before = store.events.payloads(ARS)
    _promote(store, ARS, remove_legacy=True)
    rel = layout.event_dir(ARS)
    odds = {"markets": [{"marketId": 1, "choices": [{"name": "1", "fractionalValue": "5/4"}]}]}
    encoded = codec.write_payload(layout.resolve(store.data_dir, layout.slice_path(rel, "odds_all", "1")), odds)
    found = manifest.read_manifest(layout.resolve(store.data_dir, layout.manifest_path(rel)))
    found.slices["odds_all/1"] = SliceEntry(state="ok", fetched_at=at(BASE), checked_at=at(BASE),
                                           stored_bytes=encoded.stored_bytes, raw_bytes=encoded.raw_bytes,
                                           sha256=encoded.sha256, meta={"provider": 1})
    manifest.write_manifest(layout.resolve(store.data_dir, layout.manifest_path(rel)), found)
    assert admin_of(store).index_event(ARS) == "v3"

    row = store.events.get(ARS)
    assert row is not None and (row.layout, row.path, row.legacy_path) == ("v3", None, None)
    assert store.events.payload(ARS) == before["event"]
    # v3'te saklanan baytlar kurallı JSON'dur (sıkıştırması açılmış halde döner)
    assert store.events.payload(ARS, raw=True) == codec.canonical_bytes(before["event"])
    assert store.events.payload(ARS, "odds_all", "1") == odds
    assert store.events.payload(ARS, "odds_all", "2") is None and store.events.payload(ARS, "odds_all") is None
    assert store.events.payloads(ARS) == {**before, "odds_all/1": odds}
    assert list(store.events.payloads(ARS, ["odds_all"])) == ["odds_all/1"]
    info = store.events.slice(ARS, "odds_all", "1")
    assert (info.key, info.sub, info.state, info.has_payload) == ("odds_all", "1", "ok", True)
    assert dict(info.meta) == {"provider": 1}
    assert (info.stored_bytes, info.raw_bytes) == (encoded.stored_bytes, encoded.raw_bytes)
    assert [(s.key, s.sub) for s in store.events.slices(ARS)][3:5] == [("lineups", ""), ("odds_all", "1")]
    state = next(iter(store.events.states(Scope(event_ids=[ARS]))))
    assert state.slice("odds_all", "1") == info and state.slice("odds_all").state == "not_requested"


def test_a_v3_copy_wins_over_the_legacy_directory(own: Tuple[sf.LegacyFixture, Store]) -> None:
    fx, store = own
    _promote(store, CRY)
    (fx.data_dir / PL_DETAILS / str(CRY) / "basic.json").write_text("{}", encoding="utf-8")  # artık okunmaz

    row = store.events.get(CRY)
    assert row is not None and (row.layout, row.path, row.legacy_path) == ("v3", None, f"{PL_DETAILS}/{CRY}")
    assert store.events.payload(CRY) == sf.basic_payload(sf.PL_CRY)


def test_a_corrupt_payload_file_raises(own: Tuple[sf.LegacyFixture, Store]) -> None:
    fx, store = own
    (fx.data_dir / PL_DETAILS / str(ARS) / "h2h.json").write_bytes(b'{"teamDuel": ')

    with pytest.raises(PayloadCorrupt):
        store.events.payload(ARS, "h2h")
    with pytest.raises(PayloadCorrupt):
        store.events.payloads(ARS)
    assert store.events.payload(ARS, "h2h", raw=True) == b'{"teamDuel": '  # ham okuma ayrıştırmaz


def test_a_legacy_payload_that_is_json_null_is_not_missing(own: Tuple[sf.LegacyFixture, Store]) -> None:
    fx, store = own
    (fx.data_dir / PL_DETAILS / str(ARS) / "h2h.json").write_bytes(b"null")
    assert admin_of(store).index_event(ARS) == "legacy"

    assert store.events.slice(ARS, "h2h").has_payload is True
    assert store.events.payload(ARS, "h2h") is None
    assert store.events.payload(ARS, "h2h", raw=True) == b"null"
    assert "h2h" in store.events.payloads(ARS) and store.events.payloads(ARS)["h2h"] is None


# --- bölüm 6.3: dosya yer değiştirirken okuma -------------------------------------------------------------

def test_reader_retries_once_when_the_event_moved_to_v3(own: Tuple[sf.LegacyFixture, Store],
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Okuyucu katalog satırını aldıktan sonra maç v3'e taşınır ve eski dizin silinir: ilk okuma dosyayı
    bulamaz, satır yeniden okunur ve yük yeni yerinden gelir.
    """
    _, store = own
    expected = store.events.payload(ARS, "statistics")
    reader = store.events._reader
    original = reader.read_payload
    calls: List[Tuple[str, str]] = []

    def moved_meanwhile(directory: Any, key: str = "event", *, raw: bool = False) -> Any:
        if not calls:
            _promote(store, ARS, remove_legacy=True)
        calls.append((str(directory), key))
        return original(directory, key, raw=raw)

    monkeypatch.setattr(reader, "read_payload", moved_meanwhile)

    assert store.events.payload(ARS, "statistics") == expected
    assert calls and calls[0] == (f"{PL_DETAILS}/{ARS}", "statistics")
    assert store.events.get(ARS).layout == "v3"  # type: ignore[union-attr]
    calls.clear()
    monkeypatch.setattr(reader, "read_payload", original)
    assert store.events.payload(ARS, "statistics", raw=True) == codec.canonical_bytes(expected)


def test_reader_retries_once_when_a_legacy_directory_was_renamed(own: Tuple[sf.LegacyFixture, Store],
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    fx, store = own
    expected = store.events.payloads(LEE)
    reader = store.events._reader
    original = reader.read_payload
    moved: List[str] = []

    def renamed_meanwhile(directory: Any, key: str = "event", *, raw: bool = False) -> Any:
        if not moved:
            target = "match_details/17_Premier_League/season_Renamed"
            (fx.data_dir / target).mkdir(parents=True)
            shutil.move(str(fx.data_dir / PL_DETAILS / str(LEE)), str(fx.data_dir / target / str(LEE)))
            moved.append(f"{target}/{LEE}")
            assert admin_of(store).index_event(LEE, paths=moved) == "legacy"
        return original(directory, key, raw=raw)

    monkeypatch.setattr(reader, "read_payload", renamed_meanwhile)

    assert store.events.payloads(LEE) == expected
    assert store.events.get(LEE).path == moved[0]  # type: ignore[union-attr]


def test_payload_missing_after_the_retry(own: Tuple[sf.LegacyFixture, Store]) -> None:
    """Katalog yük var diyor ama dosya yok ve satır da değişmedi: PayloadMissing."""
    fx, store = own
    (fx.data_dir / PL_DETAILS / str(ARS) / "h2h.json").unlink()
    shutil.rmtree(fx.data_dir / PL_DETAILS / str(CRY))

    for call in (lambda: store.events.payload(ARS, "h2h"), lambda: store.events.payload(ARS, "h2h", raw=True),
                 lambda: store.events.payloads(ARS), lambda: store.events.payload(CRY),
                 lambda: store.events.payload(CRY, "statistics")):
        with pytest.raises(PayloadMissing):
            call()
    assert store.events.payload(ARS, "statistics") is not None  # öteki dilimler okunur


def test_payload_is_none_when_the_row_vanished_before_the_retry(own: Tuple[sf.LegacyFixture, Store],
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """Maç bu arada silinir (dizini gider, satırı liste satırına döner): yeniden bakışta artık yük yoktur."""
    fx, store = own
    reader = store.events._reader
    original = reader.read_payload
    done: List[int] = []

    def deleted_meanwhile(directory: Any, key: str = "event", *, raw: bool = False) -> Any:
        if not done:
            shutil.rmtree(fx.data_dir / PL_DETAILS / str(ARS))
            assert admin_of(store).index_event(ARS) is None
            done.append(1)
        return original(directory, key, raw=raw)

    monkeypatch.setattr(reader, "read_payload", deleted_meanwhile)

    assert store.events.payload(ARS, "statistics") is None
    row = store.events.get(ARS)
    assert row is not None and (row.has_event_payload, row.row_source) == (False, "listing")


def test_read_with_retry_tries_exactly_twice() -> None:
    seen: List[str] = []

    def locate() -> Optional[str]:
        seen.append("locate")
        return "here"

    def read(where: str) -> Any:
        seen.append("read")
        raise PayloadMissing("yok", path=where)

    with pytest.raises(PayloadMissing):
        events_mod.read_with_retry(locate, read)
    assert seen == ["locate", "read", "locate", "read"]
    assert events_mod.read_with_retry(lambda: None, read, "absent") == "absent"


# --- dilim durumları ----------------------------------------------------------------------------------

def test_slices_of_an_event_with_mixed_markers(canon: Store) -> None:
    """PL_CRY: lineups 2 sayımın 1'i doğrulanmış, incidents eşiğin altında + hata, pregame_form doğrulanmamış."""
    found = {info.key: info for info in canon.events.slices(CRY)}
    marked = dt.datetime.fromisoformat(sf.MARKER_AT)

    assert list(found) == ["event", "h2h", "incidents", "lineups", "pregame_form", "statistics", "team_streaks"]
    assert all(info.ref == Ref.event(CRY) and info.sub == "" for info in found.values())
    for key in ("event", "h2h", "statistics", "team_streaks"):
        info = found[key]
        assert (info.state, info.has_payload, info.empty_count, info.unverified_empty_count, info.error) == (
            "ok", True, 0, 0, None)
        assert info.fetched_at == info.checked_at == at(BASE)
        assert info.stored_bytes and info.raw_bytes is None and info.history_count == 0 and dict(info.meta) == {}
    lineups, incidents, form = found["lineups"], found["incidents"], found["pregame_form"]
    assert (lineups.state, lineups.has_payload) == ("empty", False)
    assert (lineups.empty_count, lineups.unverified_empty_count) == (1, 1)
    assert lineups.fetched_at is None and lineups.checked_at == marked and lineups.stored_bytes is None
    assert (incidents.state, incidents.has_payload, incidents.empty_count, incidents.unverified_empty_count) == (
        "error", False, 1, 0)
    assert incidents.error == SliceError(reason="5xx", http_status=503, at=marked, count=1)
    assert (form.state, form.empty_count, form.unverified_empty_count, form.error) == ("empty", 0, 3, None)
    assert lineups.settled_empty() and form.settled_empty() and not incidents.settled_empty()
    assert incidents.settled_empty(threshold=1) and not form.settled_empty(threshold=4)


def test_slice_of_one_key_and_of_an_unknown_key(canon: Store) -> None:
    assert canon.events.slice(CRY, "incidents") == next(s for s in canon.events.slices(CRY) if s.key == "incidents")
    statistics = canon.events.slice(NEW, "statistics")
    assert (statistics.state, statistics.has_payload) == ("error", False)
    assert statistics.error == SliceError(reason="403", http_status=403, at=dt.datetime.fromisoformat(sf.MARKER_AT),
                                          count=2)
    unknown = canon.events.slice(ARS, "point_by_point")
    assert unknown == SliceInfo(ref=Ref.event(ARS), key="point_by_point", sub="", state="not_requested",
                                has_payload=False, fetched_at=None, checked_at=None, empty_count=0,
                                unverified_empty_count=0, error=None, stored_bytes=None, raw_bytes=None,
                                history_count=0)
    assert not unknown.settled_empty()
    assert canon.events.slice(1, "event").state == "not_requested"  # bilinmeyen maç
    assert canon.events.slices(NO_DETAIL) == [] and canon.events.slices(1) == []


def test_states_give_rows_and_slices_together(canon: Store) -> None:
    states = list(canon.events.states())

    assert [state.event.id for state in states] == sorted(CANONICAL_ORDER)
    assert all(isinstance(state, EventState) for state in states)
    for state in states:
        assert state.event == canon.events.get(state.event.id)
        assert list(state.slices) == canon.events.slices(state.event.id)
    by_id = {state.event.id: state for state in states}
    assert by_id[NO_DETAIL].slices == ()
    assert by_id[CRY].slice("incidents").state == "error" and by_id[CRY].slice("odds_all").state == "not_requested"
    for batch in (1, 3, 35):
        assert list(canon.events.states(batch=batch)) == states


def test_states_scope_and_status(canon: Store) -> None:
    def state_ids(*args: Any, **kwargs: Any) -> List[int]:
        return [state.event.id for state in canon.events.states(*args, **kwargs)]

    assert state_ids(Scope(tournament_ids=[19])) == [16950622, 17090707, 17148332]
    assert state_ids(Scope(tournament_ids=[132]), status_classes=("void",)) == [NBA_VOID]
    assert state_ids(Scope(sport="tennis"), status_classes=("decided_without_play",), batch=1) == [17058663, 17081861]
    assert state_ids(status_classes=("live",)) == []
    assert state_ids(None, status_classes=("not_started",)) == [NOT_STARTED, FUTURE, LIGA_NEXT]
    with pytest.raises(ValueError):
        canon.events.states(batch=0)


# --- missing ------------------------------------------------------------------------------------------

def test_missing_hand_checked(canon: Store) -> None:
    rows = {row.event_id: row for row in canon.events.missing(None, REQUIRED)}
    every = tuple(sf.REQUIRED_SLICES)

    assert all(isinstance(row, MissingRow) for row in rows.values())
    assert list(rows) == sorted(rows)
    # olay yükü olan, bitmiş ve eksiği olan maçlar
    assert {event_id: row.missing_keys for event_id, row in rows.items() if row.has_event_payload} == {
        CRY: ("incidents",),  # eşiğin altında + hata; lineups ve pregame_form yeterince denendi
        NEW: ("statistics",),  # başarısız istek sayılmaz
        WIM_TB: ("team_streaks", "pregame_form", "lineups", "incidents"),  # dosyası da işareti de yok
        TOT: ("h2h",),
        AVL: ("lineups",),  # bir kez boş geldi
        WIM_B: ("team_streaks",),
    }
    # yalnızca listeden bilinen bitmiş maçlar: olay yükü de yok, her dilim eksik
    assert {event_id for event_id, row in rows.items() if not row.has_event_payload} == {
        14025002, 16346148, 16837399, 17058663, 17090707, 17207542}
    assert all(row.missing_keys == every for row in rows.values() if not row.has_event_payload)
    assert rows[CRY].sport == "football" and rows[WIM_TB].sport == "tennis"


def test_missing_status_scope_threshold_and_limit(canon: Store) -> None:
    def found(scope: Optional[Scope] = None, **kwargs: Any) -> Dict[int, Tuple[str, ...]]:
        return {row.event_id: row.missing_keys for row in canon.events.missing(scope, REQUIRED, **kwargs)}

    everything = found(status_classes=())
    # durum süzülmezse iptal edilen kayıt ve başlamamış maçın detayı da gelir
    assert everything[NBA_VOID] == ("team_streaks", "pregame_form", "h2h", "lineups", "incidents")
    assert everything[LIGA_NEXT] == ("statistics", "lineups", "incidents")
    assert len(everything) == 20 and LISTING_ONLY <= set(everything)
    assert found(status_classes=("void",)).keys() == {NBA_VOID, 16539815, 16599919, 16425949, 17148292}
    assert found(Scope(tournament_ids=[2361])).keys() == {WIM_TB, WIM_B, 17058663, 17207542}
    assert found(Scope(season_ids=[96668], event_ids=[CRY, ARS, NO_DETAIL])).keys() == {CRY, NO_DETAIL}
    # eşik: 1 → bir kez boş gelen dilim artık beklenmez; 4 → üç kez boş gelen de beklenir
    assert AVL not in found(threshold=1)
    assert CRY not in found(threshold=1)  # incidents: 1 sayım yeter, hata durumu fark etmez
    assert found(threshold=1)[NEW] == ("statistics",)  # hiç sayımı olmayan başarısız dilim yine beklenir
    assert found(threshold=4)[CRY] == ("pregame_form", "lineups", "incidents")
    assert found(threshold=3)[sf.event_id(sf.PL_BRE)] == ("lineups", "incidents")
    assert list(found(limit=3)) == sorted(found())[:3]
    with pytest.raises(ValueError):
        canon.events.missing(None, REQUIRED, limit=0)
    with pytest.raises(ValueError):
        canon.events.missing(None, REQUIRED, status_classes=("done",))
    with pytest.raises(LayoutError):
        canon.events.missing(None, {"": ["Statistics"]})


def test_missing_required_per_sport(canon: Store) -> None:
    """"" her spora uygulanır; spora özgü anahtarlar yalnızca o sporun maçlarına. Sıra `required`'daki sıradır."""
    required = {"tennis": ["point_by_point", "statistics"], "": ["statistics"], "football": ["lineups"]}
    rows = {row.event_id: row.missing_keys for row in canon.events.missing(None, required, status_classes=())}

    assert rows[WIM_TB] == ("point_by_point",)  # statistics var; "" ile yinelenen anahtar bir kez sayılır
    # WIM_RET: statistics iki kez boş geldi (artık beklenmez), point_by_point hiç denenmedi
    assert rows[sf.event_id(sf.WIM_RET)] == ("point_by_point",)
    assert WIM_A not in rows and ARS not in rows
    assert rows[AVL] == ("lineups",) and rows[NEW] == ("statistics",)
    assert rows[17058663] == ("point_by_point", "statistics")  # yalnızca listede: tenisin bütün gerekenleri
    assert rows[NO_DETAIL] == ("statistics", "lineups")
    assert rows[16346148] == ("statistics",)  # basketbol: yalnızca ortak anahtar
    # hiçbir dilim gerekmese de olay yükü olmayan maç gelir (olay yükü eksik)
    nothing = {row.event_id: row for row in canon.events.missing(None, {}, status_classes=())}
    assert set(nothing) == LISTING_ONLY
    assert all(row.missing_keys == () and not row.has_event_payload for row in nothing.values())


def _file_needs(fetcher: MatchDataFetcher, event_ids: Sequence[int]) -> Dict[int, str]:
    return {event_id: fetcher._needs_detail_fetch(str(event_id)) for event_id in event_ids}


def _file_missing_keys(fetcher: MatchDataFetcher, event_id: int) -> Tuple[str, ...]:
    """Dosya tabanlı kural: beklenen dilimlerden (`_expected_slices`) verisi olmayanlar."""
    found = fetcher._find_match_path(str(event_id))
    assert found is not None
    directory = found[2]
    data = fetcher._load_match_data_from_dir(directory, str(event_id))
    sport = event_sport_slug(data["basic"]) or ""
    return tuple(key for key in fetcher._expected_slices(directory, sport)
                 if not fetcher.match_detail_slice_present(key, data))


@pytest.mark.parametrize("name", sf.FIXTURE_NAMES)
def test_missing_equals_the_file_based_refill_set(built: Dict[str, sf.LegacyFixture], frozen_clock: None,
                                                  name: str) -> None:
    fx = built[name]
    store = open_store(fx.data_dir)
    fetcher = fetcher_of(fx.data_dir)
    known = ids(list(store.events.iter(EventQuery())))
    needs = _file_needs(fetcher, known)
    rows = {row.event_id: row for row in store.events.missing(None, REQUIRED, status_classes=())}
    refill = {event_id for event_id, row in rows.items() if row.has_event_payload}

    assert refill == {event_id for event_id, need in needs.items() if need == "refill"}
    for event_id in refill - ({AVL} if name == "legacy" else set()):
        assert rows[event_id].missing_keys == _file_missing_keys(fetcher, event_id)
    # `full`: olay yükü yok. Tek fark: yalnızca birleşik dosyası olan dizini bugünkü okuyucu göremez
    full = {event_id for event_id, need in needs.items() if need == "full"}
    assert {event_id for event_id, row in rows.items() if not row.has_event_payload} == full - (
        {BRE} if name == "legacy" else set())
    if name == "canonical":
        assert len(refill) == 8 and len(full) == 12
    if name == "legacy":
        assert refill == {15500003, AVL} and store.events.get(BRE).has_event_payload  # type: ignore[union-attr]
        # yarıda kesilmiş dilim dosyası: bugünkü okuyucu maçın bütün dosyalarını bırakır, katalog yalnızca o dilimi
        assert rows[AVL].missing_keys == ("statistics",)
        assert _file_missing_keys(fetcher, AVL) == tuple(sf.REQUIRED_SLICES)


# --- refresh_candidates -------------------------------------------------------------------------------

def _candidates(store: Store, **kwargs: Any) -> List[int]:
    return store.events.refresh_candidates(now=NOW, window_s=WINDOW_S, min_interval_s=MIN_INTERVAL_S, **kwargs)


def test_refresh_candidates_hand_checked(canon: Store) -> None:
    # LIV: başlangıçtan 9 saat sonra gözlendi; CRY: 2,75 sa; TOT: 3,5 sa; WIM_A: 2,5 sa. Hepsi 6 saatten eski.
    assert _candidates(canon) == [CRY, LIV, TOT, WIM_A]
    # AVL ve NBA_RECENT de geçici ama gözlemleri taze (3 ve 4 saat önce)
    assert canon.events.refresh_candidates(now=NOW, window_s=WINDOW_S, min_interval_s=0) == sorted(
        [CRY, LIV, TOT, WIM_A, AVL, sf.event_id(sf.NBA_RECENT)])
    assert canon.events.refresh_candidates(now=NOW, window_s=WINDOW_S, min_interval_s=3 * HOUR) == sorted(
        [CRY, LIV, TOT, WIM_A, AVL, sf.event_id(sf.NBA_RECENT)])  # tam sınırda: zamanı gelmiştir
    assert canon.events.refresh_candidates(now=NOW, window_s=WINDOW_S, min_interval_s=3 * HOUR + 1) == sorted(
        [CRY, LIV, TOT, WIM_A, sf.event_id(sf.NBA_RECENT)])
    # pencere: gözlem başlangıç + pencereden sonra yapıldıysa kayıt kesindir (sınır dahil)
    assert canon.events.refresh_candidates(now=NOW, window_s=9900, min_interval_s=MIN_INTERVAL_S) == [WIM_A]
    assert canon.events.refresh_candidates(now=NOW, window_s=9901, min_interval_s=MIN_INTERVAL_S) == [CRY, WIM_A]
    assert canon.events.refresh_candidates(now=NOW, window_s=0, min_interval_s=0, include_unobserved=True) == []
    assert canon.events.refresh_candidates(now=NOW, window_s=-1, min_interval_s=0) == []
    # durum ve kapsam
    assert _candidates(canon, scope=Scope(tournament_ids=[17])) == [CRY, LIV, TOT]
    assert _candidates(canon, scope=Scope(sport="tennis")) == [WIM_A]
    assert _candidates(canon, status_classes=("void",)) == []
    assert _candidates(canon, status_classes=FINISHED) == [CRY, LIV, TOT, WIM_A]


def test_refresh_candidates_with_unobserved_records(canon: Store) -> None:
    unobserved = {sf.event_id(ev) for ev in (sf.PL_LEE, sf.PL_BRE, sf.PL_NEW, sf.PL_BHA, sf.PL_OLD_A, sf.CUP_AET,
                                             sf.NBA_WO, sf.WIM_B, sf.WIM_TB, sf.LIGA_NEXT)}

    assert _candidates(canon, include_unobserved=True) == sorted(unobserved | {CRY, LIV, TOT, WIM_A})
    assert _candidates(canon, include_unobserved=True, scope=Scope(tournament_ids=[2361])) == sorted(
        [WIM_A, WIM_B, WIM_TB])
    assert _candidates(canon, include_unobserved=True, status_classes=("not_started",)) == [LIGA_NEXT]


@pytest.mark.parametrize("name", sf.FIXTURE_NAMES)
def test_refresh_candidates_equal_refresh_due_ids(built: Dict[str, sf.LegacyFixture], frozen_clock: None,
                                                  monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """
    `refresh_due_ids` eksik dilimi olan maçı vermez (o `refill`dir): adaylardan `missing()` çıkarılınca iki
    küme eşittir. Eski biçimlerdeki bilinen farklar: birleşik dosyası olan kaydın gözlemini bugünkü okuyucu
    görmüyor (LIV: gözlemsiz sayılıyor), düz dizinleri de `refresh_due_ids` gezmiyor (LEE, BRE).
    """
    fx = built[name]
    store = open_store(fx.data_dir)
    fetcher = fetcher_of(fx.data_dir)
    refill = {row.event_id for row in store.events.missing(None, REQUIRED, status_classes=()) if row.has_event_payload}

    def catalog(**kwargs: Any) -> List[str]:
        return [str(event_id) for event_id in _candidates(store, **kwargs) if event_id not in refill]

    def differences(found: List[str], expected: List[str]) -> Set[str]:
        return set(found) ^ set(expected)

    known = differences(catalog(), fetcher.refresh_due_ids())
    assert known == ({str(LIV)} if name == "legacy" else set())
    for league_id in fx.leagues:
        scoped = catalog(scope=Scope(tournament_ids=[league_id]))
        assert differences(scoped, fetcher.refresh_due_ids(league_id)) <= known
    with monkeypatch.context() as patch:
        patch.setenv("REFRESH_LEGACY", "true")
        legacy_only = differences(catalog(include_unobserved=True), fetcher.refresh_due_ids())
        # düz dizinler (biri yalnızca birleşik dosya): bugünkü gezinti bunlara ulaşmaz. LIV iki tarafta da var:
        # katalogda geçici kayıt olarak, dosya tarafında gözlemsiz sayıldığı için
        assert legacy_only == ({str(LEE), str(BRE)} if name == "legacy" else set())
    with monkeypatch.context() as patch:
        patch.setenv("REFRESH_WINDOW_HOURS", "0")
        assert fetcher.refresh_due_ids() == [] == store.events.refresh_candidates(
            now=NOW, window_s=0, min_interval_s=MIN_INTERVAL_S)
    with monkeypatch.context() as patch:
        patch.setenv("REFRESH_MIN_INTERVAL_HOURS", "0")
        found = [str(i) for i in store.events.refresh_candidates(now=NOW, window_s=WINDOW_S, min_interval_s=0)
                 if i not in refill]
        assert differences(found, fetcher.refresh_due_ids()) == known


IN_PROGRESS = "football/A_inprogress-7-2nd-half__17018572"


def _random_details(rng: random.Random, window_s: int, min_interval_s: int) -> List[sf.Detail]:
    """Kanonik maçlar, rastgele dilim, işaret ve gözlem durumlarıyla. İlki hep: canlıya dönmüş, geçici bir kayıt."""
    out: List[sf.Detail] = []
    for position, base in enumerate(sf.CANONICAL_DETAILS):
        ev = base.ev
        start = sf.basic_payload(ev)["startTimestamp"]
        with_data = tuple(key for key in sf.REQUIRED_SLICES if rng.random() < 0.6)
        without = tuple(key for key in sf.REQUIRED_SLICES if key not in with_data and rng.random() < 0.3)
        unavailable = {key: rng.randint(1, 3) for key in sf.REQUIRED_SLICES if rng.random() < 0.4}
        status: Dict[str, Dict[str, Any]] = {}
        for key in sf.REQUIRED_SLICES:
            entry: Dict[str, Any] = {}
            if rng.random() < 0.3:
                entry.update(sf.empty_marker(rng.randint(1, 3)))
            if rng.random() < 0.3:
                entry.update(sf.error_marker(rng.choice(["403", "429", "5xx", "timeout"]), rng.choice([403, None])))
            if entry:
                status[key] = entry
        moments = [start + window_s - 1, start + window_s, start + window_s + 1, start + 600, start - HOUR,
                   NOW - min_interval_s, NOW - min_interval_s + 1, NOW - min_interval_s - 1, NOW - 60,
                   NOW - 30 * sf.DAY]
        observed: Optional[str] = None
        observation: Optional[Dict[str, Any]] = None
        kind = rng.random()
        if position == 0 or kind < 0.7:
            observed = at(start + 600 if position == 0 else rng.choice(moments)).isoformat(timespec="seconds")
        elif kind < 0.8:
            observation = {"observed_at_utc": rng.choice(["", "yesterday", None]), "change_ts": 5}
        out.append(sf.Detail(ev, slices=with_data, empty=without, observed=observed, observation=observation,
                             unavailable=unavailable or None, slice_status=status or None,
                             basic_case=IN_PROGRESS if position == 0 else base.basic_case))
    return out


@pytest.mark.parametrize("seed", range(12))
def test_needs_from_the_catalog_equal_the_file_based_ones_for_random_states(
        tmp_path: Path, frozen_clock: None, monkeypatch: pytest.MonkeyPatch, seed: int) -> None:
    """
    Rastgele işaret ve gözlem durumları: `missing()` = dosya tabanlı `refill` kümesi (eksik anahtarlarıyla),
    `refresh_candidates()` = `refresh_due` (maç maç) ve eksikler çıkınca `refresh_due_ids`. Pencere ve en az
    aralık da rastgeledir; gözlemler sınırların tam üstüne, bir saniye önüne ve arkasına da düşer.
    """
    rng = random.Random(seed)
    window_h, min_interval_h = rng.choice([(72, 6), (1, 0), (24, 12), (200, 1)])
    window_s, min_interval_s = window_h * HOUR, min_interval_h * HOUR
    legacy_too = rng.random() < 0.5
    monkeypatch.setenv("REFRESH_WINDOW_HOURS", str(window_h))
    monkeypatch.setenv("REFRESH_MIN_INTERVAL_HOURS", str(min_interval_h))
    monkeypatch.setenv("REFRESH_LEGACY", "true" if legacy_too else "false")
    builder = sf._Builder("random", tmp_path / "data", (sf.PL, sf.FA_CUP, sf.NBA, sf.WIMBLEDON, sf.LALIGA))
    for detail in _random_details(rng, window_s, min_interval_s):
        builder.detail(detail)
    fx = builder.fixture
    build_catalog(fx.data_dir)
    store = open_store(fx.data_dir)
    fetcher = fetcher_of(fx.data_dir)
    needs = _file_needs(fetcher, fx.detail_ids)

    rows = {row.event_id: row for row in store.events.missing(None, REQUIRED, status_classes=())}
    refill = {event_id for event_id, need in needs.items() if need == "refill"}
    assert set(rows) == refill and all(row.has_event_payload for row in rows.values())
    for event_id in refill:
        assert rows[event_id].missing_keys == _file_missing_keys(fetcher, event_id)

    candidates = store.events.refresh_candidates(now=NOW, window_s=window_s, min_interval_s=min_interval_s,
                                                 include_unobserved=legacy_too)
    due = []
    for record in fx.details:
        directory = fx.data_dir / record.path
        basic = json.loads((directory / "basic.json").read_bytes())
        observation_file = directory / "observation.json"
        observation = json.loads(observation_file.read_bytes()) if observation_file.exists() else {}
        if refresh.refresh_due(basic, observation, now=float(NOW)):
            due.append(record.event_id)
    assert candidates == sorted(due)
    assert [str(event_id) for event_id in candidates if event_id not in refill] == sorted(
        fetcher.refresh_due_ids(), key=int)
    assert sorted(event_id for event_id, need in needs.items() if need == "refresh") == [
        event_id for event_id in candidates if event_id not in refill]

    # durumu bitmemiş bir sınıfa dönen kayıt: durum süzülmedikçe aday kalır (bölüm 8.3)
    regressed = fx.details[0].event_id
    assert store.events.get(regressed).status_class == "live"  # type: ignore[union-attr]
    assert regressed in candidates
    assert regressed not in store.events.refresh_candidates(
        now=NOW, window_s=window_s, min_interval_s=min_interval_s, include_unobserved=legacy_too,
        status_classes=("completed", "decided_without_play", "void"))


# --- stale, open_events, summary ----------------------------------------------------------------------

def test_stale_rows(canon: Store) -> None:
    """NBA_VOID: saklanan olay yükü iptal diyor, daha yeni liste sayfası bitmiş diyor."""
    assert canon.events.stale() == [NBA_VOID]
    assert canon.events.stale(Scope(tournament_ids=[132])) == [NBA_VOID]
    assert canon.events.stale(Scope(tournament_ids=[17])) == []
    assert canon.events.stale(Scope(sport="basketball", event_ids=[NBA_VOID, ARS])) == [NBA_VOID]


def test_open_events(canon: Store) -> None:
    assert canon.events.open_events(started_before=NOW) == [NOT_STARTED, FUTURE]  # başlangıç sırasıyla
    assert canon.events.open_events(started_before=1790686800) == [NOT_STARTED]  # sınır dahil
    assert canon.events.open_events(started_before=1790686799) == []
    assert canon.events.open_events(started_before=NOW + 10 * sf.DAY) == [NOT_STARTED, FUTURE, LIGA_NEXT]
    assert canon.events.open_events(started_before=NOW + 10 * sf.DAY, scope=Scope(tournament_ids=[8])) == [LIGA_NEXT]
    with pytest.raises(ValueError):
        canon.events.open_events(started_before="now")  # type: ignore[arg-type]


def test_summary_per_tournament(canon: Store) -> None:
    found = {row.tournament_id: row for row in canon.events.summary()}

    assert list(found) == [8, 17, 19, 132, 2361]
    assert all(isinstance(row, TournamentSummary) for row in found.values())
    # Premier League: 26/27'de 13 listelenen maç (9'unun detayı var), 25/26'da 2 (1'inin detayı var)
    assert found[17] == TournamentSummary(
        tournament_id=17, events=15, with_payload=10, finished=12, seasons=2,
        by_status={"not_started": 2, "completed": 12, "void": 1},
        first_start_ts=1779595200, last_start_ts=1790758800, updated_at=BASE)
    assert (found[8].events, found[8].with_payload, found[8].finished, found[8].seasons) == (5, 2, 1, 1)
    assert dict(found[8].by_status) == {"not_started": 1, "completed": 1, "void": 3}
    assert (found[19].events, found[19].with_payload, found[19].finished) == (3, 2, 3)
    assert (found[132].events, found[132].with_payload, found[132].finished) == (6, 5, 5)
    assert dict(found[132].by_status) == {"completed": 4, "decided_without_play": 1, "void": 1}
    assert (found[2361].events, found[2361].with_payload, found[2361].finished) == (6, 4, 6)
    assert sum(row.events for row in found.values()) == 35 and sum(row.with_payload for row in found.values()) == 23
    assert canon.events.summary(Scope(tournament_ids=[17])) == [found[17]]
    assert [row.tournament_id for row in canon.events.summary(Scope(sport="football"))] == [8, 17, 19]
    assert canon.events.summary(Scope(sport="handball")) == []


def test_summary_groups_events_without_a_tournament(old: Store) -> None:
    """`_no_tournament/` altındaki maçların benzersiz turnuvası yoktur: tek satırda, kimliği None."""
    first = old.events.summary()[0]

    assert (first.tournament_id, first.events, first.with_payload, first.seasons) == (None, 3, 3, 1)
    assert [row.tournament_id for row in old.events.summary()] == [None, 8, 17]


# --- sorgu planları (bölüm 3.7) -------------------------------------------------------------------------

Statement = Tuple[str, Any]


@pytest.fixture(scope="module")
def synthetic_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """tests/test_store_query_plans.py'nin sentetik kataloğu (7.200 olay, 30.240 dilim satırı), `ANALYZE` ile."""
    data_dir = tmp_path_factory.mktemp("plans") / "data"
    store = open_store(data_dir)
    try:
        catalog = store._catalog
        with catalog.write():
            for table, rows in plans._rows().items():
                catalog.upsert(table, rows)
            catalog.stamp_derive_version()
        catalog.connection().execute("ANALYZE main")
        store.follows.apply([FollowSpec("tournament", t, f"Tournament {t}", enabled=bool(t % 3)) for t in range(1, 16)],
                            origin="api")
    finally:
        store.close()
    return data_dir


@pytest.fixture
def synthetic(synthetic_dir: Path) -> Store:
    return open_store(synthetic_dir)


@pytest.fixture
def explain(synthetic: Store, monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable[[], Any]], List[List[str]]]:
    """
    Bir API çağrısının çalıştırdığı her SELECT'in planı. Deyimler bağlantıdan, bağlı parametreleriyle
    birlikte yakalanır (parametre yerine sabit yazılmış bir sorgu başka bir plan alabilir).
    """
    statements: List[Statement] = []
    original = sqlite3.Connection.execute

    def recording(self: sqlite3.Connection, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        statements.append((sql, parameters))
        return original(self, sql, parameters)

    def run(call: Callable[[], Any]) -> List[List[str]]:
        statements.clear()
        with monkeypatch.context() as patch:
            patch.setattr(sqlite_mod.Connection, "execute", recording, raising=False)
            result = call()
            if isinstance(result, Iterator):
                list(result)
        conn = synthetic._catalog.connection()
        found = [(sql, params) for sql, params in statements if sql.lstrip().upper().startswith(("SELECT", "WITH"))]
        assert found, "çağrı hiçbir sorgu çalıştırmadı"
        return [plans.plan(conn, sql, params) for sql, params in found]

    return run


def _sorts(lines: List[str]) -> bool:
    return any("TEMP B-TREE FOR ORDER BY" in line for line in lines)


SEASON = Scope(tournament_ids=[17], season_ids=[171])


def test_plan_season_listing_and_next_page(synthetic: Store, explain: Any) -> None:
    first, = explain(lambda: synthetic.events.list(EventQuery(scope=SEASON, limit=25)))
    cursor = synthetic.events.list(EventQuery(scope=SEASON, limit=25)).next_cursor
    following, = explain(lambda: synthetic.events.list(EventQuery(scope=SEASON, limit=25, cursor=cursor)))
    listing, total = explain(lambda: synthetic.events.list(EventQuery(scope=SEASON, limit=25), with_total=True))

    for lines in (first, following, listing, total):
        assert plans.uses(lines, "e", "events_tournament_season") and not _sorts(lines)
    assert any("start_ts<?" in line for line in following)  # konum, dizinde bir aralıktır


def test_plan_whole_catalog_listing(synthetic: Store, explain: Any) -> None:
    newest, = explain(lambda: synthetic.events.list(EventQuery(limit=25)))
    offset, = explain(lambda: synthetic.events.list(EventQuery(limit=25, offset=5000)))
    oldest, = explain(lambda: synthetic.events.list(EventQuery(limit=25, sort="start_asc")))

    for lines in (newest, offset, oldest):
        assert plans.uses(lines, "e", "events_start") and not _sorts(lines)


def test_plan_sport_range_status_and_export_scan(synthetic: Store, explain: Any) -> None:
    ranged, = explain(lambda: synthetic.events.list(EventQuery(
        scope=Scope(sport="football"), start_from=plans.BASE_TS, start_to=plans.BASE_TS + 30 * 86400,
        status_classes=FINISHED)))
    batches = explain(lambda: synthetic.events.iter(EventQuery(scope=Scope(sport="football"), sort="start_asc"),
                                                    batch=1000))

    assert plans.uses(ranged, "e", "events_sport_start") and not _sorts(ranged)
    assert len(batches) == 2  # 1.800 futbol maçı, 1.000'lik parçalar
    for lines in batches:
        assert plans.uses(lines, "e", "events_sport_start") and not _sorts(lines)
    assert any("start_ts>?" in line for line in batches[1])


def test_plan_several_tournaments_finished_only(synthetic: Store, explain: Any) -> None:
    lines, = explain(lambda: synthetic.events.list(EventQuery(scope=Scope(tournament_ids=[3, 17, 29]),
                                                              status_classes=FINISHED)))

    assert plans.uses(lines, "e", "events_tournament_season")


def test_plan_name_search_and_participant_scope(synthetic: Store, explain: Any) -> None:
    search, = explain(lambda: synthetic.events.list(EventQuery(text="17005 uni")))
    participant, = explain(lambda: synthetic.events.list(EventQuery(scope=Scope(participant_ids=[17005]))))

    # ad araması katlanmış ad dizinini okur, oradan maçlara birincil anahtarla gider
    assert plans.uses(search, "p", "participants_name") and plans.by_primary_key(search, "ep")
    assert plans.by_primary_key(participant, "ep")
    assert len(synthetic.events.list(EventQuery(text="17005 uni", limit=500)).items) == 18


def test_plan_followed_tournaments(synthetic: Store, explain: Any) -> None:
    counted, = explain(lambda: synthetic.events.count(EventQuery(scope=Scope(followed=True))))
    states = explain(lambda: synthetic.events.states(Scope(followed=True), batch=5000))

    assert plans.uses(counted, "e", "events_tournament_season")
    assert plans.uses(counted, "f", "sqlite_autoindex_follows_1")
    assert plans.uses(states[0], "f", "sqlite_autoindex_follows_1")
    assert synthetic.events.count(EventQuery(scope=Scope(followed=True))) == 10 * 180  # 15 takipten 10'u etkin


def test_plan_dashboard_counts(synthetic: Store, explain: Any) -> None:
    for lines in explain(lambda: synthetic.events.summary(Scope(tournament_ids=[17]))):
        assert plans.uses(lines, "e", "events_tournament_season")
        assert any(line.startswith("SEARCH") for line in lines)
    totals, _ = explain(lambda: synthetic.events.summary())
    assert plans.uses(totals, "e", "events_tournament_season")  # turnuva sırası dizinden gelir


def test_plan_missing(synthetic: Store, explain: Any) -> None:
    required = {"football": ["statistics", "lineups"], "basketball": ["statistics"], "": ["incidents"]}
    season, = explain(lambda: synthetic.events.missing(SEASON, required))
    whole, = explain(lambda: synthetic.events.missing(None, required))

    assert plans.uses(season, "e", "events_tournament_season")
    for lines in (season, whole):
        assert plans.by_primary_key(lines, "s")  # (olay, anahtar) başına birincil anahtar araması
    assert any(line.startswith("SCAN") and plans._reads(line, "e") for line in whole)  # süzgeçsiz: tablo taraması


def test_plan_refresh_candidates(synthetic: Store, explain: Any) -> None:
    def call(**kwargs: Any) -> Callable[[], Any]:
        return lambda: synthetic.events.refresh_candidates(now=plans.NOW, window_s=WINDOW_S,
                                                           min_interval_s=MIN_INTERVAL_S, **kwargs)

    plain, = explain(call())
    with_status, = explain(call(status_classes=("completed", "decided_without_play", "void")))
    _, unobserved = explain(call(scope=Scope(tournament_ids=[17]), include_unobserved=True))

    assert plans.uses(plain, "e", "events_unsettled") and any("observed_gap<?" in line for line in plain)
    assert plans.uses(with_status, "e", "events_unsettled")
    assert plans.uses(unobserved, "e", "events_unobserved")


def test_plan_live_stale_and_open_events(synthetic: Store, explain: Any) -> None:
    live, = explain(lambda: synthetic.events.list(EventQuery(scope=Scope(sport="tennis"), status_classes=("live",))))
    live_count, = explain(lambda: synthetic.events.count(EventQuery(scope=Scope(sport="tennis"),
                                                                    status_classes=("live",))))
    stale, = explain(lambda: synthetic.events.stale())
    opened, = explain(lambda: synthetic.events.open_events(started_before=plans.NOW))
    open_list, = explain(lambda: synthetic.events.count(EventQuery(status_classes=("unknown", "live", "not_started"),
                                                                   start_to=plans.NOW)))

    assert plans.uses(live_count, "e", "events_live")
    assert plans.uses(live, "e", "events_live") or plans.uses(live, "e", "events_sport_start")
    assert plans.uses(stale, "e", "events_stale")
    assert plans.uses(opened, "e", "events_open") and not _sorts(opened)
    assert plans.uses(open_list, "e", "events_open")  # sıra ne olursa olsun dizinin yazımıyla kurulur


def test_plan_states_and_slices(synthetic: Store, explain: Any) -> None:
    events, slices = explain(lambda: synthetic.events.states(SEASON, batch=1000))
    one, = explain(lambda: synthetic.events.slices(10_000_500))
    located, = explain(lambda: synthetic.events.payload(10_000_500))

    assert plans.uses(events, "e", "events_tournament_season")
    assert plans.by_primary_key(slices, "event_slices") and plans.by_primary_key(one, "event_slices")
    assert plans.by_primary_key(located, "s") and plans.by_primary_key(located, "e")
    whole = explain(lambda: synthetic.events.states(batch=5000))
    assert len(whole) == 4 and not any(_sorts(lines) for lines in whole)  # kimlik sırası: tablo sırası


def test_plan_round_of_a_season(synthetic: Store, explain: Any) -> None:
    lines, = explain(lambda: synthetic.events.list(EventQuery(scope=Scope(season_ids=[171]), round=3)))

    assert plans.uses(lines, "e", "events_season_round")


def test_ids_are_written_as_literals_so_long_lists_work(synthetic: Store) -> None:
    """Kimlik listeleri bağlı parametre değil sabittir: SQLite'ın parametre sınırını (eski sürümlerde 999) aşar."""
    wanted = list(range(10_000_001, 10_005_001))

    assert synthetic.events.count(EventQuery(scope=Scope(event_ids=wanted))) == 5000
    assert sum(1 for _ in synthetic.events.states(Scope(event_ids=wanted), batch=2000)) == 5000


# --- EntityStore: turnuvalar, sezonlar, yarışmacılar -------------------------------------------------------

def test_store_has_the_entity_store(canon: Store, tmp_path: Path) -> None:
    assert isinstance(canon.entities, EntityStore)
    assert {"EntityStore", "TournamentRow", "SeasonRow", "ParticipantRow"} <= set(src.store.__all__)
    assert not any(hasattr(EntityStore, name) for name in WRITE_METHODS)
    # kurulmamış katalog boş yanıt verir
    unbuilt = open_store(tmp_path / "data")
    assert unbuilt.entities.tournaments() == [] and unbuilt.entities.seasons(17) == []
    assert unbuilt.entities.participants() == [] and unbuilt.entities.sport_of_tournament(17) is None
    assert unbuilt.entities.tournament(17) is None and unbuilt.entities.season(96668) is None
    assert unbuilt.entities.payload(Ref.tournament(17), "seasons") is None
    assert unbuilt.entities.slices(Ref.tournament(17)) == []
    # kapatılmış depo okumayı reddeder
    canon.close()
    for call in (lambda: canon.entities.tournaments(), lambda: canon.entities.seasons(17),
                 lambda: canon.entities.participants(), lambda: canon.entities.slices(Ref.tournament(17)),
                 lambda: canon.entities.payload(Ref.tournament(17), "seasons")):
        with pytest.raises(StoreError):
            call()


@pytest.mark.parametrize("key, sub", [("../seasons", ""), ("Seasons", ""), ("schedule", "../round_1"),
                                      ("schedule", "Round_1")])
def test_entity_payload_rejects_names_that_are_not_slice_names(canon: Store, key: str, sub: str) -> None:
    with pytest.raises(LayoutError):
        canon.entities.payload(Ref.season(17, 96668), key, sub)
    with pytest.raises(LayoutError):
        canon.entities.slice(Ref.season(17, 96668), key, sub)


def test_tournaments(canon: Store) -> None:
    found = canon.entities.tournaments()

    assert [(row.id, row.name, row.sport, row.slug) for row in found] == [
        (19, "FA Cup", "football", "fa-cup"), (8, "LaLiga", "football", "laliga"), (132, "NBA", "basketball", "nba"),
        (17, "Premier League", "football", "premier-league"), (2361, "Wimbledon, Men", "tennis", "wimbledon-men")]
    assert all(isinstance(row, TournamentRow) and row.updated_at == BASE for row in found)
    assert [row.id for row in canon.entities.tournaments(sport="football")] == [19, 8, 17]
    assert [row.id for row in canon.entities.tournaments(sport="football", limit=2)] == [19, 8]
    assert [row.id for row in canon.entities.tournaments(text="LIGA")] == [8]
    assert [row.id for row in canon.entities.tournaments(text="l", sport="football")] == [8, 17]
    assert canon.entities.tournaments(text="%") == [] and canon.entities.tournaments(sport="handball") == []
    assert canon.entities.tournament(17) == found[3]
    assert canon.entities.tournament(35) is None  # yapılandırılmış ama verisi olmayan lig
    with pytest.raises(ValueError):
        canon.entities.tournaments(limit=0)


def test_seasons_newest_first(canon: Store, old: Store) -> None:
    found = canon.entities.seasons(17)

    assert [(row.id, row.name, row.year, row.listed, row.position) for row in found] == [
        (96668, "Premier League 26/27", "26/27", True, 0), (76986, "Premier League 25/26", "25/26", True, 1),
        (61627, "Premier League 24/25", "24/25", True, 2)]
    assert all(isinstance(row, SeasonRow) and row.tournament_id == 17 for row in found)
    assert found[0].sort_key > found[1].sort_key > found[2].sort_key
    assert canon.entities.season(76986) == found[1]
    assert canon.entities.season(1) is None and canon.entities.seasons(35) == []
    # sezon listesi olan ama hiç maçı olmayan turnuva: turnuva satırı yok, sezonları var
    assert old.entities.tournament(2361) is None
    assert [(row.id, row.listed) for row in old.entities.seasons(2361)] == [(79116, True)]
    # aynı turnuvanın iki liste dosyasından yenisi geçerli: 26/27 de listede
    assert [row.id for row in old.entities.seasons(17)] == [96668, 76986, 61627]


def test_a_season_known_only_from_events_is_not_listed(tmp_path: Path) -> None:
    fx = sf.build_fixture("legacy", tmp_path / "data")
    build_catalog(fx.data_dir)  # lig adları verilmedi: `LaLiga_seasons.json` çözülemez, CSV geçerli olur
    store = open_store(fx.data_dir)

    assert [(row.id, row.listed, row.position) for row in store.entities.seasons(8)] == [(77559, True, 0)]
    friendly = store.entities.season(sf.NO_SEASON.id)
    assert friendly is None or friendly.listed is False


def test_participants(canon: Store) -> None:
    arsenal = canon.entities.participants(text="ARSENAL")

    assert [(row.name, row.sport) for row in arsenal] == [("Arsenal", "football")]
    assert isinstance(arsenal[0], ParticipantRow)
    assert arsenal[0].id == canon.events.get(ARS).home_id  # type: ignore[union-attr]
    assert [row.name for row in canon.entities.participants(text="manchester")] == [
        "Manchester City", "Manchester United"]
    basketball = [row.name for row in canon.entities.participants(sport="basketball", limit=500)]
    assert basketball[:3] == ["Atlanta Hawks", "Boston Celtics", "Brooklyn Nets"] and len(basketball) == 12
    assert [row.name for row in canon.entities.participants(text="EN", sport="basketball", limit=2)] == [
        "Denver Nuggets", "Golden State Warriors"]  # üçüncüsü (Phoenix Suns) sınırın dışında
    assert len(canon.entities.participants(sport="tennis", limit=500)) == 12
    assert len(canon.entities.participants()) == 50 and len(canon.entities.participants(limit=500)) > 50
    names = [row.name for row in canon.entities.participants(limit=500)]
    assert names == sorted(names, key=lambda name: name.casefold())
    assert canon.entities.participants(ids=[arsenal[0].id, 1]) == arsenal
    assert canon.entities.participants(ids=[arsenal[0].id], sport="tennis") == []
    with pytest.raises(ValueError):
        canon.entities.participants(ids=["1"])  # type: ignore[list-item]


def test_sport_of_tournament(own: Tuple[sf.LegacyFixture, Store]) -> None:
    _, store = own

    assert store.entities.sport_of_tournament(17) == "football"
    assert store.entities.sport_of_tournament(132) == "basketball"
    assert store.entities.sport_of_tournament(35) is None and store.entities.sport_of_tournament(1) is None
    # turnuva satırı sporu bilmiyorsa (ya da satır yoksa) maçlarında en çok geçen spor
    with store._catalog.write() as conn:
        conn.execute("UPDATE tournaments SET sport = NULL WHERE id = 132")
        conn.execute("DELETE FROM tournaments WHERE id = 2361")
        conn.execute("UPDATE events SET sport = 'tennis' WHERE id = ?", (NBA_B,))
        conn.execute("UPDATE events SET sport = '' WHERE id = ?", (NBA_VOID,))
    assert store.entities.sport_of_tournament(132) == "basketball"
    assert store.entities.sport_of_tournament(2361) == "tennis"
    assert store.entities.tournament(132).sport is None  # type: ignore[union-attr]


# --- EntityStore: dilimler ve yükler ----------------------------------------------------------------------

def test_season_list_slice_and_payload(built: Dict[str, sf.LegacyFixture], canon: Store) -> None:
    ref = Ref.tournament(17)
    stored = (built["canonical"].data_dir / "seasons" / "17_Premier_League_seasons.json").read_bytes()
    info = canon.entities.slice(ref, "seasons")

    assert canon.entities.slices(ref) == [info]
    assert (info.ref, info.key, info.sub, info.state, info.has_payload) == (ref, "seasons", "", "ok", True)
    assert info.fetched_at == info.checked_at == at(BASE) and info.stored_bytes == len(stored)
    payload = canon.entities.payload(ref, "seasons")
    assert payload == json.loads(stored)
    assert [season["id"] for season in payload["seasons"]] == [96668, 76986, 61627]
    assert canon.entities.payload(ref, "seasons", raw=True) == stored
    assert canon.entities.payload(Ref.tournament(35), "seasons") is None
    assert canon.entities.payload(ref, "standings", "total") is None
    assert canon.entities.slice(ref, "standings", "total").state == "not_requested"
    assert canon.entities.slices(Ref.team(3928)) == [] and canon.entities.slices(Ref.tournament(35)) == []


def test_schedule_slices_and_payloads(built: Dict[str, sf.LegacyFixture], canon: Store) -> None:
    data_dir = built["canonical"].data_dir
    season = Ref.season(17, 96668)
    found = canon.entities.slices(season)

    assert [(info.key, info.sub, info.state, dict(info.meta)) for info in found] == [
        ("schedule", "round_1", "ok", {"complete": True}), ("schedule", "round_2", "ok", {"complete": False}),
        ("schedule", "round_3", "ok", {"complete": False})]
    assert all(info.ref == season and info.has_payload for info in found)
    # tur dosyası: `_complete` bizim eklediğimiz anahtardır, yükte yoktur (meta'dadır)
    stored = json.loads((data_dir / PL_MATCHES / "round_1.json").read_bytes())
    payload = canon.entities.payload(season, "schedule", "round_1")
    assert stored["_complete"] is True and "_complete" not in payload
    assert payload == {key: value for key, value in stored.items() if key != "_complete"}
    assert [event["id"] for event in payload["events"]] == [ARS, LIV, LEE, BRE]
    assert canon.entities.payload(season, "schedule", "round_1", raw=True) == codec.canonical_bytes(payload)
    assert canon.entities.slice(season, "schedule", "round_2").meta == {"complete": False}
    assert canon.entities.payload(season, "schedule", "round_4") is None
    assert canon.entities.payload(season, "schedule") is None
    # olay sayfası: dosya olduğu gibi yüktür
    nba = Ref.season(132, 80229)
    page = (data_dir / "matches/132_NBA/80229_NBA_26_27/events_last_0.json").read_bytes()
    assert [(info.sub, dict(info.meta)) for info in canon.entities.slices(nba)] == [
        ("last_0", {"filtered": True}), ("last_1", {"filtered": True})]
    assert canon.entities.payload(nba, "schedule", "last_0") == json.loads(page)
    assert canon.entities.payload(nba, "schedule", "last_0", raw=True) == page
    assert canon.entities.payload(nba, "schedule", "last_0")["hasNextPage"] is True
    # kupa turu: alt anahtarda tur adı da var
    assert [info.sub for info in canon.entities.slices(Ref.season(19, 97110))] == [
        "round_28_semifinals", "round_29_final"]
    # sezonun turnuvası okuma için gerekmez
    assert canon.entities.slices(Ref("season", 96668)) == [dataclasses.replace(info, ref=Ref("season", 96668))
                                                          for info in found]


def test_season_list_that_lives_only_in_the_csv(tmp_path: Path) -> None:
    """İlk sürümün `league_seasons.csv` dosyası: JSON listesi olmayan turnuvanın listesi ondan kurulur."""
    fx = sf.build_fixture("legacy", tmp_path / "data")
    build_catalog(fx.data_dir)  # lig adları verilmedi: `LaLiga_seasons.json` çözülemez
    store = open_store(fx.data_dir)
    ref = Ref.tournament(8)

    info = store.entities.slice(ref, "seasons")
    assert (info.state, info.has_payload, info.stored_bytes) == ("ok", True, None)
    payload = store.entities.payload(ref, "seasons")
    assert payload == {"seasons": [{"id": 77559, "name": "LaLiga 25/26", "year": "25/26"}]}
    assert store.entities.payload(ref, "seasons", raw=True) == codec.canonical_bytes(payload)
    path, = store._catalog.connection().execute(
        "SELECT path FROM entity_slices WHERE kind = 'tournament' AND entity_id = 8").fetchone()
    assert path == "league_seasons.csv"
    (fx.data_dir / "league_seasons.csv").unlink()
    with pytest.raises(PayloadMissing):
        store.entities.payload(ref, "seasons")


def test_entity_payload_in_the_v3_layout(own: Tuple[sf.LegacyFixture, Store]) -> None:
    """v3 varlık dizinlerini henüz hiçbir şey yazmıyor; okuyucu satırın gösterdiği dizinden okur."""
    _, store = own
    ref = Ref.season(17, 96668)
    directory = layout.entity_dir("season", 96668, 17)
    standings = {"standings": [{"rows": [{"team": {"id": 3928}, "position": 1}]}]}
    encoded = codec.write_payload(layout.resolve(store.data_dir, layout.slice_path(directory, "standings", "total")),
                                  standings)
    with store._catalog.write():
        store._catalog.upsert("entity_slices", [{
            "kind": "season", "entity_id": 96668, "key": "standings", "sub": "total", "state": "ok", "has_payload": 1,
            "fetched_at": NOW, "checked_at": NOW, "stored_bytes": encoded.stored_bytes, "raw_bytes": encoded.raw_bytes,
            "meta_json": '{"round": 2}', "layout": "v3", "path": directory}])

    assert store.entities.payload(ref, "standings", "total") == standings
    assert store.entities.payload(ref, "standings", "total", raw=True) == codec.canonical_bytes(standings)
    info = store.entities.slice(ref, "standings", "total")
    assert (info.state, info.fetched_at, info.raw_bytes) == ("ok", at(NOW), encoded.raw_bytes)
    assert dict(info.meta) == {"round": 2}
    assert [(s.key, s.sub) for s in store.entities.slices(ref)] == [
        ("schedule", "round_1"), ("schedule", "round_2"), ("schedule", "round_3"), ("standings", "total")]
    assert store.entities.payload(ref, "standings", "home") is None


def test_entity_calls_with_an_event_ref_go_to_the_event_store(canon: Store) -> None:
    ref = Ref.event(CRY)

    assert canon.entities.slices(ref) == canon.events.slices(CRY)
    assert canon.entities.slice(ref, "incidents") == canon.events.slice(CRY, "incidents")
    assert canon.entities.payload(ref, "event") == canon.events.payload(CRY)
    assert canon.entities.payload(ref, "statistics", raw=True) == canon.events.payload(CRY, "statistics", raw=True)


def test_entity_reader_retries_once_when_the_file_was_replaced(own: Tuple[sf.LegacyFixture, Store],
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """Sezon listesi başka bir adla yeniden yazılır ve eskisi silinir: okuyucu yeni dosyayı bulur."""
    fx, store = own
    ref = Ref.tournament(17)
    expected = store.entities.payload(ref, "seasons")
    original = store.entities._read_file
    seen: List[str] = []

    def replaced_meanwhile(where: Any, *args: Any) -> Any:
        if not seen:
            old_file = fx.data_dir / "seasons" / "17_Premier_League_seasons.json"
            shutil.copy2(old_file, fx.data_dir / "seasons" / "17_seasons.json")
            old_file.unlink()
            assert admin_of(store, fx.leagues).reconcile().changed
        seen.append(where.path)
        return original(where, *args)

    monkeypatch.setattr(store.entities, "_read_file", replaced_meanwhile)

    assert store.entities.payload(ref, "seasons") == expected
    assert seen == ["seasons/17_Premier_League_seasons.json", "seasons/17_seasons.json"]


def test_entity_payload_missing_and_corrupt(own: Tuple[sf.LegacyFixture, Store]) -> None:
    fx, store = own
    (fx.data_dir / PL_MATCHES / "round_1.json").unlink()
    (fx.data_dir / PL_MATCHES / "round_2.json").write_bytes(b'{"events": [')
    (fx.data_dir / PL_MATCHES / "round_3.json").write_bytes(b"[]")
    season = Ref.season(17, 96668)

    with pytest.raises(PayloadMissing):
        store.entities.payload(season, "schedule", "round_1")
    with pytest.raises(PayloadMissing):
        store.entities.payload(season, "schedule", "round_1", raw=True)
    with pytest.raises(PayloadCorrupt):
        store.entities.payload(season, "schedule", "round_2")
    with pytest.raises(PayloadCorrupt):
        store.entities.payload(season, "schedule", "round_3")  # nesne değil


def test_plan_entity_queries(synthetic: Store, explain: Any) -> None:
    seasons, = explain(lambda: synthetic.entities.seasons(17))
    by_sport, = explain(lambda: synthetic.entities.tournaments(sport="football"))
    names, = explain(lambda: synthetic.entities.participants(text="17005"))
    slices, = explain(lambda: synthetic.entities.slices(Ref.season(17, 171)))

    assert plans.uses(seasons, "seasons", "seasons_tournament") and not _sorts(seasons)
    assert plans.uses(by_sport, "tournaments", "tournaments_sport") and not _sorts(by_sport)
    assert plans.uses(names, "participants", "participants_name") and not _sorts(names)
    assert plans.by_primary_key(slices, "entity_slices")
    # kimlik listesi sabit olarak yazılır: bağlı parametre sınırını aşan liste de çalışır
    assert len(synthetic.entities.participants(ids=list(range(1000, 41_000)), limit=5000)) == 800


# --- ChangeLog ----------------------------------------------------------------------------------------

def test_store_has_the_change_log(canon: Store, tmp_path: Path) -> None:
    assert isinstance(canon.changes, ChangeLog)
    assert {"ChangeLog", "ChangeRow"} <= set(src.store.__all__)
    assert not any(hasattr(ChangeLog, name) for name in WRITE_METHODS)
    unbuilt = open_store(tmp_path / "data")  # kurulmamış katalog
    assert unbuilt.changes.list() == [] and unbuilt.changes.last_seq() == 0
    canon.close()
    for call in (lambda: canon.changes.list(), lambda: canon.changes.last_seq()):
        with pytest.raises(StoreError):
            call()


def test_change_log_rows(canon: Store) -> None:
    found = canon.changes.list()
    score, void = found

    assert all(isinstance(row, ChangeRow) for row in found) and canon.changes.last_seq() == 2
    assert (score.seq, score.event_id, score.sport, score.tournament_id, score.status_regressed) == (
        1, LIV, "football", 17, False)
    assert score.ts == epoch("2026-09-15T13:10:00+00:00") and score.segment == "score_changes.jsonl"
    assert score.fields == ("awayScore.current", "awayScore.display", "awayScore.normaltime", "awayScore.period1")
    assert score.row == sf.SCORE_CHANGES[0] and score.row["changed"]["awayScore.current"] == [0, 1]
    assert (void.seq, void.event_id, void.sport, void.tournament_id, void.status_regressed) == (
        2, NBA_VOID, "basketball", 132, True)
    assert void.row == sf.SCORE_CHANGES[1] and "status.code" in void.fields and len(void.fields) == 21


def test_change_log_filters(canon: Store) -> None:
    def seqs(**kwargs: Any) -> List[int]:
        return [row.seq for row in canon.changes.list(**kwargs)]

    assert seqs(after_seq=0) == [1, 2] and seqs(after_seq=1) == [2] and seqs(after_seq=2) == []
    assert seqs(event_id=LIV) == [1] and seqs(event_id=NBA_VOID, after_seq=1) == [2] and seqs(event_id=ARS) == []
    assert seqs(since=epoch("2026-09-15T13:10:00+00:00")) == [1, 2]  # sınır dahil
    assert seqs(since=epoch("2026-09-15T13:10:01+00:00")) == [2] and seqs(since=NOW) == []
    assert seqs(limit=1) == [1] and seqs(limit=1, after_seq=1) == [2]
    for bad in ({"after_seq": -1}, {"limit": 0}, {"event_id": "5"}, {"since": "today"}, {"after_seq": True}):
        with pytest.raises(ValueError):
            canon.changes.list(**bad)  # type: ignore[arg-type]


def test_change_log_of_a_directory_without_one(built: Dict[str, sf.LegacyFixture]) -> None:
    store = open_store(built["empty"].data_dir)

    assert store.changes.list() == [] and store.changes.last_seq() == 0


def test_change_log_seq_is_the_line_number(own: Tuple[sf.LegacyFixture, Store]) -> None:
    """Dizine giremeyen satır da numara harcar: tüketicinin sakladığı `seq` yeniden kurmadan sonra da geçerlidir."""
    fx, store = own
    lines = (fx.data_dir / "score_changes.jsonl").read_bytes().splitlines()
    (fx.data_dir / "score_changes.jsonl").write_bytes(b"\n".join([lines[0], b"not json", lines[1], lines[0]]) + b"\n")
    admin_of(store, fx.leagues).rebuild()

    assert [(row.seq, row.event_id) for row in store.changes.list()] == [(1, LIV), (3, NBA_VOID), (4, LIV)]
    assert store.changes.last_seq() == 4
    assert [row.seq for row in store.changes.list(event_id=LIV, after_seq=1)] == [4]


def test_plan_change_log_queries(synthetic: Store, explain: Any) -> None:
    of_event, = explain(lambda: synthetic.changes.list(event_id=10_000_500))
    since, = explain(lambda: synthetic.changes.list(after_seq=5))

    assert plans.uses(of_event, "changes", "changes_event") and not _sorts(of_event)
    assert any("rowid>?" in line for line in since) and not _sorts(since)
