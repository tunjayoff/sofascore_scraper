"""
Maç dışı varlıkların yazıcısı `EntityStore.put` ve onu kullanan program / sezon listesi yazıcıları (plan maddesi
ST-22; docs/design/01-storage.md bölüm 2.3, 3.4, 4.2 ve 8.2).

Ölçütler:
  * Sezon listeleri ve program sayfaları `v3/tournaments/` altına yazılır; eski düzen dosyalarına dokunulmaz.
  * Her yazmadan sonra katalog, aynı ağacın sıfırdan kurulmuş haline eşittir (`diff_from_rebuild() == []`).
  * Aynı sahte API yanıtlarından yeni yazıcının ürettiği mantıksal döküm (tests/store_dump.py) eski yazıcının
    dosyalarınınkine eşittir; çekilen sezonun liste satırları eski sezon özeti CSV'sinin satırlarına eşittir.
  * Aynı alt anahtarın v3 sayfası eski düzendekinin, v3 sezon listesi eski düzen listesinin yerine geçer.

Tümü çevrimdışıdır: ağ istekleri yamalanır.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from unittest.mock import MagicMock, patch

import pytest

import store_dump
import store_fixtures as sf
from schedule_runner import list_schedule
from sofascore_scraper.match_fetcher import MatchFetcher
from sofascore_scraper.season_fetcher import SeasonFetcher
from sofascore_scraper.services.query import QueryService
from sofascore_scraper.slices import SLICE_EMPTY, SLICE_FAILED, SLICE_OK, SLICE_SKIPPED, Outcome
from sofascore_scraper.store import (
    CategoryRow,
    EventQuery,
    PutResult,
    Ref,
    Scope,
    SportRow,
    Store,
    StoreError,
    open_store,
)
from sofascore_scraper.store.legacy import schedule_sub as legacy_schedule_sub

PL = sf.PL.id
PL_SEASON = sf.PL_2627.id
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def canonical(tmp_path: Path) -> sf.LegacyFixture:
    return sf.build_fixture("canonical", tmp_path / "data")


def differences(store: Store) -> List[str]:
    return store.catalog.diff_from_rebuild()


def page(*events: sf.Ev, has_next: bool = False) -> Dict[str, Any]:
    return {"events": [sf.event_payload(ev) for ev in events], "hasNextPage": has_next}


def season_list(*seasons: sf.Season) -> Dict[str, Any]:
    return {"seasons": [{"name": s.name, "year": s.year, "editor": False, "id": s.id} for s in seasons]}


def tree(root: Path) -> Dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def legacy_tree(root: Path) -> Dict[str, bytes]:
    """Eski düzen ağaçları (`seasons/`, `matches/`, `match_details/`): ST-22 bunlara dokunmaz."""
    return {name: data for name, data in tree(root).items() if name.split("/", 1)[0] in
            ("seasons", "matches", "match_details")}


# --- EntityStore.put: dosyalar, manifest, katalog ----------------------------------------------------------

def test_a_season_list_is_written_to_the_tournament_directory(tmp_path: Path) -> None:
    store = open_store(tmp_path / "data")
    payload = season_list(sf.PL_2627, sf.PL_2526)

    result = store.entities.put(Ref.tournament(PL), {"seasons": Outcome(SLICE_OK, payload, fetched_at=NOW)})

    assert result == PutResult(created=True, event_written=False, superseded=False, written=("seasons",),
                               change_seq=None, promoted=False)
    directory = tmp_path / "data" / "v3" / "tournaments" / str(PL)
    assert sorted(p.name for p in directory.iterdir()) == ["manifest.json", "seasons.json.gz"]
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "tournament" and manifest["id"] == PL
    assert manifest["slices"]["seasons"]["state"] == "ok"
    assert store.entities.payload(Ref.tournament(PL), "seasons") == payload
    assert [(s.id, s.listed, s.position) for s in store.entities.seasons(PL)] == [
        (sf.PL_2627.id, True, 0), (sf.PL_2526.id, True, 1)]
    info = store.entities.slice(Ref.tournament(PL), "seasons")
    assert info.state == "ok" and info.has_payload and info.fetched_at == NOW
    assert differences(store) == []


def test_a_schedule_page_gives_listing_rows(tmp_path: Path) -> None:
    store = open_store(tmp_path / "data")
    ref = Ref.season(PL, PL_SEASON)

    store.entities.put(ref, {("schedule", "round_1"): Outcome(SLICE_OK, page(sf.PL_ARS, sf.PL_NOT_STARTED),
                                                               meta={"complete": False})})

    schedule = tmp_path / "data" / "v3" / "tournaments" / str(PL) / "seasons" / str(PL_SEASON) / "schedule"
    assert [p.name for p in schedule.iterdir()] == ["round_1.json.gz"]
    info = store.entities.slice(ref, "schedule", "round_1")
    assert info.meta == {"complete": False} and info.state == "ok"
    rows = store.events.list(EventQuery(scope=Scope(season_ids=[PL_SEASON])), with_total=True)
    assert rows.total == 2
    assert {(row.id, row.row_source, row.listed_in) for row in rows.items} == {
        (sf.event_id(sf.PL_ARS), "listing", "round_1"), (sf.event_id(sf.PL_NOT_STARTED), "listing", "round_1")}
    assert differences(store) == []


def test_a_second_page_and_a_rewrite_keep_the_catalog_equal_to_a_rebuild(tmp_path: Path) -> None:
    store = open_store(tmp_path / "data")
    ref = Ref.season(PL, PL_SEASON)
    later = NOW + timedelta(hours=1)
    store.entities.put(ref, {("schedule", "round_1"): Outcome(SLICE_OK, page(sf.PL_ARS), fetched_at=NOW)})
    store.entities.put(ref, {("schedule", "round_2"): Outcome(SLICE_OK, page(sf.PL_LIV), fetched_at=NOW)})
    # Tur 1 yeniden çekildi: maç artık o turda değil, başka bir maç geldi
    result = store.entities.put(ref, {("schedule", "round_1"): Outcome(SLICE_OK, page(sf.PL_LEE), fetched_at=later)})

    assert result.written == ("schedule/round_1",) and not result.created
    listed = {row.id: row.listed_in for row in store.events.iter(EventQuery(scope=Scope(season_ids=[PL_SEASON])))}
    assert listed == {sf.event_id(sf.PL_LIV): "round_2", sf.event_id(sf.PL_LEE): "round_1"}
    assert differences(store) == []


def test_an_unchanged_payload_is_not_rewritten(tmp_path: Path) -> None:
    store = open_store(tmp_path / "data")
    ref = Ref.season(PL, PL_SEASON)
    store.entities.put(ref, {("schedule", "last_0"): Outcome(SLICE_OK, page(sf.PL_ARS), fetched_at=NOW)})
    later = NOW + timedelta(minutes=5)

    result = store.entities.put(ref, {("schedule", "last_0"): Outcome(SLICE_OK, page(sf.PL_ARS), fetched_at=later)})

    assert result.written == ()
    assert store.entities.slice(ref, "schedule", "last_0").fetched_at == later
    assert differences(store) == []


def test_empty_failed_and_skipped_outcomes(tmp_path: Path) -> None:
    store = open_store(tmp_path / "data")
    ref = Ref.season(PL, PL_SEASON)
    store.entities.put(ref, {
        ("schedule", "round_1"): Outcome(SLICE_EMPTY, {"events": []}, meta={"complete": False}),
        ("schedule", "round_2"): Outcome(SLICE_EMPTY, reason="404", http_status=404),
        ("schedule", "round_3"): Outcome(SLICE_FAILED, reason="5xx", http_status=503),
        ("schedule", "round_4"): Outcome(SLICE_SKIPPED, reason="breaker"),
    }, count_empties=["schedule"])

    infos = {info.sub: info for info in store.entities.slices(ref)}
    assert sorted(infos) == ["round_1", "round_2", "round_3"]
    assert (infos["round_1"].state, infos["round_1"].has_payload, infos["round_1"].empty_count) == ("empty", True, 1)
    assert (infos["round_2"].state, infos["round_2"].has_payload, infos["round_2"].empty_count) == ("empty", False, 1)
    assert infos["round_3"].state == "error" and infos["round_3"].error is not None
    assert infos["round_3"].error.http_status == 503
    # Hata, verisi olan dilimin durumunu düşürmez
    store.entities.put(ref, {("schedule", "round_1"): Outcome(SLICE_OK, page(sf.PL_ARS))})
    store.entities.put(ref, {("schedule", "round_1"): Outcome(SLICE_FAILED, reason="429", http_status=429)})
    info = store.entities.slice(ref, "schedule", "round_1")
    assert info.state == "ok" and info.has_payload and info.error is not None and info.empty_count == 0
    assert differences(store) == []


def test_keep_history_appends_snapshots_of_changed_payloads(tmp_path: Path) -> None:
    store = open_store(tmp_path / "data")
    ref = Ref.tournament(PL)
    for seasons in ((sf.PL_2526,), (sf.PL_2526,), (sf.PL_2627, sf.PL_2526)):
        store.entities.put(ref, {"seasons": Outcome(SLICE_OK, season_list(*seasons))}, keep_history=["seasons"])

    snapshots = list(store.history.snapshots(ref, "seasons"))
    assert [len(s.payload["seasons"]) for s in snapshots] == [1, 2]
    assert store.entities.slice(ref, "seasons").history_count == 2
    assert differences(store) == []


def test_put_rejects_what_it_cannot_write(tmp_path: Path) -> None:
    store = open_store(tmp_path / "data")
    ok = Outcome(SLICE_OK, page(sf.PL_ARS))
    with pytest.raises(ValueError, match="Store.events.put"):
        store.entities.put(Ref.event(1), {"event": ok})
    with pytest.raises(ValueError, match="Ref.season"):
        store.entities.put(Ref("season", PL_SEASON), {("schedule", "round_1"): ok})
    with pytest.raises(StoreError):  # büyük harfli alt anahtar (FX-4): katlanmaz, reddedilir
        store.entities.put(Ref.season(PL, PL_SEASON), {("schedule", "round_1_Final"): ok})
    store.entities.put(Ref.season(PL, PL_SEASON), {("schedule", "round_1"): ok})
    with pytest.raises(ValueError, match=f"tournament {PL}"):  # bir sezon kimliği tek bir turnuvanın altında
        store.entities.put(Ref.season(sf.FA_CUP.id, PL_SEASON), {("schedule", "round_1"): ok})
    assert not (tmp_path / "data" / "v3" / "tournaments" / str(sf.FA_CUP.id)).exists()
    assert store.entities.put(Ref.tournament(PL), {}).written == ()
    assert store._catalog.connection().execute("SELECT count(*) FROM pending_writes").fetchone()[0] == 0
    assert differences(store) == []


def test_a_read_only_store_cannot_be_written(tmp_path: Path) -> None:
    open_store(tmp_path / "data").close()
    store = open_store(tmp_path / "data", readonly=True)
    try:
        with pytest.raises(StoreError):
            store.entities.put(Ref.tournament(PL), {"seasons": Outcome(SLICE_OK, season_list(sf.PL_2627))})
    finally:
        store.close()


def test_a_write_cut_short_is_recovered_by_the_next_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Yük dosyası yazıldı, manifest yazılamadı: işaret kalır, açılıştaki uzlaştırma varlığı yeniden dizinler."""
    data = tmp_path / "data"
    store = open_store(data)
    ref = Ref.season(PL, PL_SEASON)
    store.entities.put(ref, {("schedule", "round_1"): Outcome(SLICE_OK, page(sf.PL_ARS))})

    def crash(step: str) -> None:
        if step.startswith("payload:"):
            raise RuntimeError("crash")

    monkeypatch.setattr(store.entities, "_checkpoint", crash)
    with pytest.raises(RuntimeError):
        store.entities.put(ref, {("schedule", "round_2"): Outcome(SLICE_OK, page(sf.PL_LIV))})
    pending = store._catalog.connection().execute("SELECT kind, entity_id FROM pending_writes").fetchall()
    assert [tuple(row) for row in pending] == [("season", PL_SEASON)]
    monkeypatch.undo()

    report = store.catalog.reconcile()
    assert report.pending == 1 and report.pending_skipped == 0
    assert [info.sub for info in store.entities.slices(ref)] == ["round_1"]  # manifest round_2'yi bilmiyor
    assert differences(store) == []
    store.entities.put(ref, {("schedule", "round_2"): Outcome(SLICE_OK, page(sf.PL_LIV))})
    assert [info.sub for info in store.entities.slices(ref)] == ["round_1", "round_2"]
    assert differences(store) == []


# --- iki düzen birlikte ----------------------------------------------------------------------------------

def test_a_v3_page_replaces_the_legacy_page_of_the_same_sub(canonical: sf.LegacyFixture) -> None:
    data = canonical.data_dir
    before = legacy_tree(data)
    store = open_store(data)
    ref = Ref.season(PL, PL_SEASON)
    assert {i.sub: i for i in store.entities.slices(ref)}["round_1"].stored_bytes is not None

    store.entities.put(ref, {("schedule", "round_1"): Outcome(SLICE_OK, page(sf.PL_ARS), meta={"complete": True})})

    assert legacy_tree(data) == before  # eski düzen dosyası silinmez ve değişmez (karar 4)
    infos = {info.sub: info for info in store.entities.slices(ref)}
    assert sorted(infos) == ["round_1", "round_2", "round_3"]  # öteki eski sayfalar okunmaya devam eder
    assert store.entities.payload(ref, "schedule", "round_1") == page(sf.PL_ARS)
    rows = {row.id: row.listed_in for row in store.events.iter(EventQuery(scope=Scope(season_ids=[PL_SEASON])))}
    assert rows[sf.event_id(sf.PL_ARS)] == "round_1"
    assert rows[sf.event_id(sf.PL_LIV)] is None  # yalnızca eski round_1'de listeleniyordu (detayı var, satırı kalır)
    assert rows[sf.event_id(sf.PL_NO_DETAIL)] == "round_2"
    assert differences(store) == []
    store.close()
    assert differences(open_store(data)) == []


def test_a_v3_season_list_replaces_the_legacy_file(canonical: sf.LegacyFixture) -> None:
    data = canonical.data_dir
    before = legacy_tree(data)
    store = open_store(data)
    assert [s.id for s in store.entities.seasons(PL) if s.listed] == [sf.PL_2627.id, sf.PL_2526.id, sf.PL_2425.id]

    store.entities.put(Ref.tournament(PL), {"seasons": Outcome(SLICE_OK, season_list(sf.PL_2627))})

    assert legacy_tree(data) == before
    assert [(s.id, s.position) for s in store.entities.seasons(PL) if s.listed] == [(sf.PL_2627.id, 0)]
    assert store.entities.payload(Ref.tournament(PL), "seasons") == season_list(sf.PL_2627)
    assert store.entities.slice(Ref.tournament(PL), "seasons").stored_bytes is not None
    assert differences(store) == []
    # Eski düzen listesi değişince (uzlaştırma bütün listeleri yeniden yazar) v3 listesi geçerli kalır
    seasons_file = data / "seasons" / "17_Premier_League_seasons.json"
    seasons_file.write_text(json.dumps(season_list(sf.PL_2425)), encoding="utf-8")
    stamp = time.time() + 5
    os.utime(seasons_file, (stamp, stamp))
    store.catalog.reconcile()
    assert [s.id for s in store.entities.seasons(PL) if s.listed] == [sf.PL_2627.id]
    assert differences(store) == []


def test_an_event_with_a_payload_is_attached_to_a_v3_page_and_stays_equal_to_a_rebuild(tmp_path: Path) -> None:
    """Olay yükü olan maç v3 sayfasına bağlanır (`listed_in`, `stale`); yük yeniden yazılınca da (bölüm 8.2)."""
    store = open_store(tmp_path / "data")
    event = sf.basic_payload(sf.PL_ARS)
    event_id = sf.event_id(sf.PL_ARS)
    store.events.put(event_id, {"event": Outcome(SLICE_OK, event, fetched_at=NOW)})
    listed = sf.event_payload(sf.PL_ARS)
    listed["homeScore"] = {"current": 9}  # liste olay yükünden yeni ve farklı: stale
    ref = Ref.season(sf.PL.id, sf.PL_2627.id)
    store.entities.put(ref, {("schedule", "round_1"): Outcome(SLICE_OK, {"events": [listed]},
                                                               fetched_at=NOW + timedelta(hours=1))})
    row = store.events.get(event_id)
    assert row is not None and row.listed_in == "round_1" and row.stale and row.has_event_payload
    assert differences(store) == []

    store.events.put(event_id, {"event": Outcome(SLICE_OK, event, fetched_at=NOW + timedelta(hours=2))})
    row = store.events.get(event_id)
    assert row is not None and row.listed_in == "round_1" and not row.stale
    assert differences(store) == []


def test_clear_removes_the_v3_schedules_and_season_lists(canonical: sf.LegacyFixture) -> None:
    data = canonical.data_dir
    store = open_store(data)
    store.entities.put(Ref.season(PL, PL_SEASON), {("schedule", "round_9"): Outcome(SLICE_OK, page(sf.PL_ARS))})
    store.entities.put(Ref.tournament(PL), {"seasons": Outcome(SLICE_OK, season_list(sf.PL_2627))})

    store.clear("schedules")
    assert not (data / "v3" / "tournaments" / str(PL) / "seasons").exists()
    assert (data / "v3" / "tournaments" / str(PL) / "seasons.json.gz").exists()
    assert store.entities.slices(Ref.season(PL, PL_SEASON)) == []
    assert differences(store) == []

    store.clear("seasons")
    assert not (data / "v3" / "tournaments" / str(PL)).exists()
    assert store.entities.payload(Ref.tournament(PL), "seasons") is None
    assert differences(store) == []


# --- kategoriler ve sporlar --------------------------------------------------------------------------------

def test_categories_and_sports_are_read_from_the_catalog(tmp_path: Path) -> None:
    store = open_store(tmp_path / "data")
    listed = []
    for ev, category_id, name in ((sf.PL_ARS, 1, "England"), (sf.NBA_A, 15, "USA"), (sf.WIM_A, 3, "ATP")):
        item = sf.event_payload(ev)
        item["tournament"]["category"].update({"id": category_id, "slug": name.lower(), "alpha2": name[:2]})
        listed.append(item)
    store.entities.put(Ref.season(PL, PL_SEASON), {("schedule", "last_0"): Outcome(SLICE_OK, {"events": listed})})

    assert [s.slug for s in store.entities.sports()] == ["basketball", "football", "tennis"]
    football = store.entities.sport("football")
    assert isinstance(football, SportRow) and football.slug == "football"
    assert store.entities.sport("curling") is None
    england = store.entities.category(1)
    assert england == CategoryRow(1, "football", "England", "england", "En")
    assert [c.id for c in store.entities.categories()] == [3, 1, 15]  # ada göre
    assert store.entities.categories(sport="football") == [england]
    assert [c.id for c in store.entities.categories(ids=[15, 3])] == [3, 15]
    assert store.entities.category(123456789) is None
    assert len(store.entities.categories(limit=1)) == 1
    with pytest.raises(ValueError):
        store.entities.categories(limit=0)
    with pytest.raises(ValueError):
        store.entities.sport(1)  # type: ignore[arg-type]


# --- yazıcılar: program (listing.ScheduleLister) ve SeasonFetcher ------------------------------------------------------------

def _api(league: sf.League, season: sf.Season, listings: Sequence[sf.Listing]) -> Any:
    """Listelerden sahte SofaScore: turlar `/rounds` ve tur uç noktalarından, sayfalar `events/last|next`'ten."""
    base = f"/unique-tournament/{league.id}/season/{season.id}"
    rounds = [listing for listing in listings if listing.kind == "round"]
    pages = {f"{base}/events/{listing.label}": listing for listing in listings if listing.kind == "page"}

    async def api(session: Any, url: str, max_retries: Optional[int] = None) -> Dict[str, Any]:
        if url == f"{base}/rounds":
            return {"rounds": [{"round": r.label, **({"slug": r.slug} if r.slug else {})} for r in rounds]}
        for listing in rounds:
            if url == MatchFetcher.build_round_events_url(league.id, season.id, int(listing.label), listing.slug):
                return {"events": [sf.event_payload(ev) for ev in listing.events], "hasNextPage": listing.has_next}
        found = pages.get(url)
        if found is None:
            from sofascore_scraper.exceptions import ResourceNotFoundError
            raise ResourceNotFoundError(url)
        return {"events": [sf.event_payload(ev) for ev in found.events], "hasNextPage": found.has_next}

    return api


SEASONS = [
    (sf.PL, sf.PL_2627, sf.PL_ROUNDS),
    (sf.FA_CUP, sf.FA_2627, sf.CUP_ROUNDS),
    (sf.NBA, sf.NBA_2627, sf.NBA_PAGES),
    (sf.WIMBLEDON, sf.WIM_2026, sf.WIM_PAGES),
    (sf.LALIGA, sf.LALIGA_2627, sf.LIGA_PAGES),
]


@pytest.mark.parametrize("league, season, listings", SEASONS,
                         ids=["rounds", "cup-rounds", "pages", "pages-tennis", "pages-unfiltered"])
def test_the_schedule_writer_stores_what_the_legacy_writer_wrote(league: sf.League, season: sf.Season,
                                                                  listings: Sequence[sf.Listing], tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Aynı yanıtlardan: v3'teki program sayfalarının mantıksal dökümü (yük özeti ve meta) eski yazıcının dosyalarınınkine
    eşittir; sezonun liste satırları eski sezon özeti CSV'sinin satırlarına eşittir. Özet dosyası yazılmaz.
    """
    filtered = all(listing.filtered for listing in listings)
    monkeypatch.setattr("sofascore_scraper.utils.FETCH_ONLY_FINISHED", filtered)
    built = sf.build_fixture("canonical", tmp_path / "built")
    key = f"{league.id}/{season.id}"
    expected = store_dump.dump_legacy(built.data_dir)["schedules"][key]
    data = tmp_path / "written"
    assert list_schedule(data, league.id, season.id, _api(league, season, listings), only_finished=filtered).chunks

    written = store_dump.dump(data)
    assert written["schedules"] == {key: expected}
    # Eski düzende hiçbir dosya yazılmaz: tur / sayfa dosyaları, özet JSON'u ve özet CSV'si (karar S4)
    assert [name for name in tree(data) if name.startswith("matches/")] == []

    store = open_store(data)
    rows = QueryService(store).listed_events(tournament_ids=(league.id,), season_ids=(season.id,), only_finished=False)
    _results, summary = sf.summary_of(listings)
    have = {row.id: (row.home_name, row.away_name, row.status_description) for row in rows}
    want = {int(row["match_id"]): (row["home_team"], row["away_team"], row["status"]) for row in summary}
    assert {mid: have[mid] for mid in want} == want  # özetin her satırı listede, aynı takımlar ve durumla
    assert differences(store) == []


def test_a_complete_round_is_not_fetched_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sofascore_scraper.utils.FETCH_ONLY_FINISHED", True)
    data = tmp_path / "data"
    calls: List[str] = []
    api = _api(sf.PL, sf.PL_2627, sf.PL_ROUNDS)

    async def counting(session: Any, url: str, max_retries: Optional[int] = None) -> Dict[str, Any]:
        calls.append(url)
        return await api(session, url, max_retries)

    list_schedule(data, PL, PL_SEASON, counting, only_finished=True)
    first = len(calls)
    calls.clear()
    list_schedule(data, PL, PL_SEASON, counting, only_finished=True)
    assert first == 4  # /rounds ve üç tur
    assert [url.rsplit("/", 1)[1] for url in calls] == ["rounds"]  # bitmemiş turlar TTL içinde, bitmiş tur hep
    assert differences(open_store(data)) == []


def test_the_season_list_writer_stores_the_response_in_the_store(canonical: sf.LegacyFixture) -> None:
    data = canonical.data_dir
    before = legacy_tree(data)
    config = MagicMock()
    config.get_league_by_id.return_value = sf.BUNDESLIGA.name
    fetcher = SeasonFetcher(config, data_dir=str(data))
    payload = {"seasons": [{"id": 77001, "name": "Bundesliga 26/27", "year": "26/27"}]}

    fetcher._save_seasons_json(sf.BUNDESLIGA.id, payload)

    assert legacy_tree(data) == before  # seasons/<id>_<ad>_seasons.json yazılmaz
    assert (data / "v3" / "tournaments" / str(sf.BUNDESLIGA.id) / "seasons.json.gz").is_file()
    assert fetcher.get_seasons_for_league(sf.BUNDESLIGA.id) == payload["seasons"]
    store = open_store(data)
    assert store_dump.dump(data)["season_lists"][str(sf.BUNDESLIGA.id)] == {
        "sha256": store_dump.payload_hash(payload), "seasons": 1}
    assert differences(store) == []


def test_a_season_list_that_cannot_be_stored_is_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    config = MagicMock()
    fetcher = SeasonFetcher(config, data_dir=str(tmp_path / "data"))
    with patch.object(fetcher, "_store", side_effect=StoreError("disk full")):
        fetcher._save_seasons_json(PL, season_list(sf.PL_2627))
    assert any("could not be stored" in record.getMessage() for record in caplog.records)


def test_schedule_subs_follow_the_legacy_file_names() -> None:
    assert MatchFetcher.schedule_sub("round", 12) == "round_12"
    assert MatchFetcher.schedule_sub("round", 1, "Final") == "round_1_final"
    assert MatchFetcher.schedule_sub("round", 3, "quarter finals") == "round_3_quarter-finals"
    assert MatchFetcher.schedule_sub("last", 0) == "last_0" and MatchFetcher.schedule_sub("next", 2) == "next_2"
    with pytest.raises(ValueError):
        MatchFetcher.schedule_sub("week", 1)
    # Eski sürümün dosya adından okuyucunun çıkardığı alt anahtarla aynı: eski tur dosyası önbellek olarak bulunur
    for name, args in (("round_12.json", ("round", 12)), ("round_1_Final.json", ("round", 1, "Final")),
                       ("events_last_0.json", ("last", 0))):
        assert legacy_schedule_sub(name)[1] == MatchFetcher.schedule_sub(*args)
