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
from typing import Any, Dict, List

import pytest

import store_fixtures as sf
from src.slices import SLICE_EMPTY, SLICE_FAILED, SLICE_OK, SLICE_SKIPPED, Outcome
from src.store import (
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
