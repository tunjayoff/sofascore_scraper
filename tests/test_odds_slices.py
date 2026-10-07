"""
Bahis oranı dilimleri (plan maddesi P28; docs/design/02-services.md 3.1): varsayılan olarak kapalı, seçilince
sağlayıcı alt anahtarıyla istenir, her değişen okuma dilimin geçmişine bir anlık görüntü ekler; başlamamış maçta
`max_age`'den eskiyse maç başlayana kadar yeniden, bitmiş maçta bir kez daha okunur.

Ağ yok: istekler tests/fakes/sofascore.py'nin sahte taşıyıcısına gider (G-01'in dünyası); istek katmanı,
planlayıcı, boru hattı ve Store gerçektir. Yanıt gövdeleri tests/fixtures/p28/ altında, research/all_sports
örneklerinden (kaynakları dosyalarında).
"""
from __future__ import annotations

import copy
import dataclasses
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List

import pytest
from fastapi.testclient import TestClient

from characterization import WORLD, pin_default_settings
from fakes.sofascore import FakeSofaScore
from sofascore_scraper import sports
from sofascore_scraper.client import endpoints
from sofascore_scraper.config import loader
from sofascore_scraper.schema import mappers, models
from sofascore_scraper.services import planning
from sofascore_scraper.services.export import DatasetSpec, ExportService
from sofascore_scraper.services.pipeline import FetchPipeline, body_state, run_extras
from sofascore_scraper.services.query import RefreshPolicy
from sofascore_scraper.slices import BODY_DATA, BODY_MALFORMED, BODY_NO_DATA
from sofascore_scraper.store import Ref, Store, open_store
from sofascore_scraper.web.app import app

FIXTURES = Path(__file__).parent / "fixtures" / "p28"
FINISHED = 9100001  # futbol, turnuva 17, sezon 61627, başlangıç 1724500000
NOT_STARTED = 9100004  # futbol, aynı sezon; dünyada başlangıcı 1900000000
ODDS_KEYS = ("odds_featured", "odds_all", "odds_changes", "winning_odds")
WITH_ODDS = planning.SelectionPolicy(defaults=("core", "odds"))

client = TestClient(app)


def body(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["body"]


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    pin_default_settings(monkeypatch)
    loader.reset()
    yield
    monkeypatch.undo()
    loader.reset()


@pytest.fixture
def fake() -> Iterator[FakeSofaScore]:
    with FakeSofaScore.from_file(WORLD) as world:
        yield world


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Store:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    return open_store(tmp_path / "data")


def add_odds(fake: FakeSofaScore, event_id: int, provider: int = 1) -> None:
    fake.add(f"/event/{event_id}/odds/{provider}/all", body("odds_all"))
    fake.add(f"/event/{event_id}/odds/{provider}/featured", body("odds_featured"))


def download(store: Store, ids: List[int], selection: Any, now: float | None = None) -> None:
    items = planning.plan_items(store, ids, RefreshPolicy.current(now), selection=selection)
    FetchPipeline(store, concurrency=1, selection=selection).run_sync(items)


def api_paths(fake: FakeSofaScore) -> List[str]:
    """İstenen API yolları (oturumun ısınma isteği, sitenin kökü, hariç)."""
    return [path for path in fake.paths() if path.startswith("/")]


def odds_paths(fake: FakeSofaScore) -> List[str]:
    return [path for path in fake.paths() if "odds" in path]


def policy_at(now: float) -> RefreshPolicy:
    return RefreshPolicy(now=now, window_s=72 * 3600, min_interval_s=3600)


# --- kayıt defteri --------------------------------------------------------------------------------------


def test_odds_slices_are_registered_off_by_default() -> None:
    odds = [spec for spec in sports.registered_slices() if spec.group == "odds" and spec.owner == "event"]
    assert [spec.key for spec in odds] == list(ODDS_KEYS)
    for spec in odds:
        assert spec.default_enabled is False and spec.required is False
        assert spec.subs == sports.PROVIDER_SUBS and spec.keep_history and spec.max_age == sports.ODDS_MAX_AGE
        assert spec.phases == sports.ALL_PHASES and "{sub}" in spec.path
    assert sports.get_slice("winning_odds").experimental
    assert not any(sports.get_slice(key).experimental for key in ("odds_all", "odds_featured", "odds_changes"))
    # Varsayılan seçim (kayıt defteri, `core`) hiçbir sporda ve evrede oran seçmez
    for sport in (*sports.sport_slugs(), None):
        for phase in (*sports.PHASES, None):
            for selection in (None, ["core"]):
                assert not [s for s in sports.select_slices("event", sport, selection, phase=phase)
                            if s.group == "odds"]
    assert sports.slices_for("football") == sports.select_slices("event", "football")
    # Seçimde grup adı da anahtar da geçer
    assert [s.key for s in sports.select_slices("event", "tennis", ["odds_all"])] == ["odds_all"]


def test_paths_carry_the_provider_as_the_sub_key() -> None:
    assert endpoints.event_slice("odds_all", 5, "1") == "/event/5/odds/1/all"
    assert endpoints.event_slice("winning_odds", 5, "7") == "/event/5/provider/7/winning-odds"
    assert endpoints.event_slice("statistics", 5) == "/event/5/statistics"
    with pytest.raises(ValueError):
        endpoints.event_slice("odds_all", 5)  # sağlayıcısız
    with pytest.raises(ValueError):
        endpoints.event_slice("statistics", 5, "1")
    with pytest.raises(ValueError):
        endpoints.event_slice("odds_all", 5, "../x")
    with pytest.raises(KeyError):
        endpoints.event_slice("standings", 5, "total")  # sezonun dilimi
    assert sports.get_slice("odds_all").sub_keys() == (sports.DEFAULT_ODDS_PROVIDER,)
    assert sports.get_slice("odds_all").sub_keys("3") == ("3",)


def test_the_odds_body_rule() -> None:
    assert body_state("odds_all", body("odds_all")) == BODY_DATA
    assert body_state("odds_all", {"markets": [], "eventId": 1}) == BODY_NO_DATA
    assert body_state("odds_all", None) == BODY_NO_DATA
    assert body_state("odds_all", {}) == BODY_NO_DATA
    assert body_state("odds_all", "markets") == BODY_MALFORMED
    assert body_state("odds_featured", body("odds_featured")) == BODY_DATA
    assert body_state("odds_changes", body("odds_changes")) == BODY_DATA
    assert body_state("winning_odds", {"home": {}}) == BODY_DATA  # biçimi bilinmiyor: dolu gövde yeter
    # P28 öncesi dilimlerin kuralı değişmez
    assert body_state("lineups", {"home": {"players": [1]}}) == BODY_DATA


# --- varsayılan: hiç istenmez --------------------------------------------------------------------------------


def test_the_default_selection_requests_no_odds(fake: FakeSofaScore, store: Store) -> None:
    add_odds(fake, FINISHED)
    download(store, [FINISHED, NOT_STARTED], planning.CONFIGURED)
    assert odds_paths(fake) == []
    assert not [info for info in store.events.slices(FINISHED) if info.key in ODDS_KEYS]
    assert planning.extras_selected(planning.CONFIGURED) is False
    fake.reset_log()
    assert run_extras(store, seasons=[(17, 61627)], tournament_ids=[17]) is None
    assert api_paths(fake) == []


# --- seçilince ----------------------------------------------------------------------------------------------


def test_selected_odds_are_stored_with_their_provider_and_a_snapshot(fake: FakeSofaScore, store: Store) -> None:
    add_odds(fake, FINISHED)
    download(store, [FINISHED], WITH_ODDS)

    assert sorted(odds_paths(fake)) == sorted([
        "/event/9100001/odds/1/featured", "/event/9100001/odds/1/all", "/event/9100001/odds/1/changes",
        "/event/9100001/provider/1/winning-odds"])
    found = {(info.key, info.sub): info for info in store.events.slices(FINISHED)}
    assert found[("odds_all", "1")].state == "ok" and found[("odds_all", "1")].meta == {"provider_id": 1}
    assert found[("odds_featured", "1")].state == "ok"
    # Bitmiş maçta 404 bir "veri yok" yanıtıdır ve sayılır
    assert found[("odds_changes", "1")].state == "empty" and found[("odds_changes", "1")].empty_count == 1
    assert found[("winning_odds", "1")].state == "empty"
    snapshots = list(store.history.snapshots(Ref.event(FINISHED), "odds_all", "1"))
    assert len(snapshots) == 1 and snapshots[0].payload == body("odds_all")
    assert store.events.payload(FINISHED, "odds_all", "1") == body("odds_all")
    # Oranlar tamlığa girmez: oranı eksik maç yine tamamdır
    assert "odds_all" not in planning.expected_slice_keys("football", WITH_ODDS)


def test_the_configured_provider_is_the_sub_key(fake: FakeSofaScore, store: Store) -> None:
    add_odds(fake, FINISHED, provider=7)
    download(store, [FINISHED], dataclasses.replace(WITH_ODDS, provider="7"))
    assert "/event/9100001/odds/7/all" in odds_paths(fake)
    assert not [path for path in odds_paths(fake) if "/1/" in path]
    assert store.events.slice(FINISHED, "odds_all", "7").meta == {"provider_id": 7}


def test_the_odds_country_is_recorded_only_when_the_user_sets_it(fake: FakeSofaScore, store: Store) -> None:
    """FX-15 (sahibin kararı): sağlayıcı her zaman, ülke yalnızca `[client] odds_country` verilmişse yazılır."""
    add_odds(fake, FINISHED)
    download(store, [FINISHED], dataclasses.replace(WITH_ODDS, country="TR"))
    assert store.events.slice(FINISHED, "odds_all", "1").meta == {"provider_id": 1, "country": "TR"}
    assert store.events.slice(FINISHED, "odds_featured", "1").meta == {"provider_id": 1, "country": "TR"}
    assert store.events.slice(FINISHED, "statistics").meta == {}  # öteki dilimler değişmez


def test_the_odds_country_comes_from_the_client_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "sofascore.toml"
    path.write_text('[client]\nodds_country = " tr "\n[defaults]\nslices = ["core", "odds"]\n', encoding="utf-8")
    monkeypatch.setenv(loader.CONFIG_ENV, str(path))
    loader.reset()
    try:
        assert planning.configured_policy().country == "TR"
    finally:
        loader.reset()
    assert planning.SelectionPolicy().country == ""  # varsayılan: yazılmaz


def test_the_provider_comes_from_the_client_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "sofascore.toml"
    path.write_text('[client]\nodds_provider = 4\n[defaults]\nslices = ["core", "odds"]\n', encoding="utf-8")
    monkeypatch.setenv(loader.CONFIG_ENV, str(path))
    loader.reset()
    policy = planning.configured_policy()
    assert policy.provider == "4" and planning.extras_selected(policy)


def test_an_odds_follow_selects_odds_for_its_events_only(fake: FakeSofaScore, store: Store) -> None:
    add_odds(fake, FINISHED)
    add_odds(fake, 9200001)
    policy = planning.SelectionPolicy(follows={("tournament", 17): {"enable": ("odds",), "disable": ()}})
    assert planning.extras_selected(policy)
    download(store, [FINISHED, 9200001], policy)
    assert {path.split("/")[2] for path in odds_paths(fake)} == {"9100001"}


# --- ne zaman okunur -----------------------------------------------------------------------------------------


def _info(fetched: float | None, *, empty: int = 0) -> Any:
    moment = datetime.fromtimestamp(fetched, timezone.utc) if fetched is not None else None
    return SimpleNamespace(state="ok" if fetched else "not_requested", has_payload=fetched is not None,
                           fetched_at=moment, settled_empty=lambda threshold=2: empty >= threshold)


def _row(status_class: str, start: int | None) -> Any:
    return SimpleNamespace(status_class=status_class, start_ts=start)


def test_the_timed_rule() -> None:
    spec = sports.get_slice("odds_all")
    start = 1_800_000_000
    due = planning.timed_slice_due
    # Başlamamış: hiç okunmadıysa ya da son okuma 30 dakikadan eskiyse; başlangıçtan sonra ve 7 günden önce hiç
    assert due(_info(None), spec, _row("not_started", start), start - 3600)
    assert not due(_info(start - 3600), spec, _row("not_started", start), start - 3600 + 60)
    assert due(_info(start - 3600), spec, _row("not_started", start), start - 3600 + 1800)
    assert not due(_info(None), spec, _row("not_started", start), start + 1)
    assert not due(_info(None), spec, _row("not_started", start), start - planning.PREMATCH_WINDOW_S - 1)
    assert not due(_info(None), spec, _row("not_started", None), start)
    # Bitmiş: son okuma başlangıçtan önceyse bir kez daha; sonra değil
    assert due(_info(start - 600), spec, _row("completed", start), start + 86400)
    assert due(_info(None), spec, _row("completed", start), start + 86400)
    assert not due(_info(start + 7200), spec, _row("completed", start), start + 86400 * 30)
    assert not due(_info(None, empty=2), spec, _row("completed", start), start + 86400)
    # Oynanıyor ya da void: hiç
    assert not due(_info(None), spec, _row("live", start), start + 60)
    assert not due(_info(None), spec, _row("void", start), start - 60)


def test_a_finished_event_gets_its_odds_once(fake: FakeSofaScore, store: Store) -> None:
    add_odds(fake, FINISHED)
    download(store, [FINISHED], planning.SelectionPolicy())
    assert odds_paths(fake) == []
    # Oranlar sonradan seçildi: tam maç bir kez daha okunur, yalnızca oranlar için
    needs = planning.event_needs(store, [FINISHED], RefreshPolicy.current(), selection=WITH_ODDS)
    assert needs == {FINISHED: "refill"}
    [item] = planning.plan_items(store, [FINISHED], RefreshPolicy.current(), selection=WITH_ODDS)
    assert item.slices == tuple((key, "1") for key in ODDS_KEYS)
    fake.reset_log()
    FetchPipeline(store, concurrency=1, selection=WITH_ODDS).run_sync([item])
    assert [path for path in api_paths(fake) if "odds" not in path] == ["/event/9100001"]
    # Okuma maçın başlangıcından sonra: bir daha istenmez (iki 404 alan dilim de ikinci kez istenir, sonra bırakılır)
    needs = planning.event_needs(store, [FINISHED], RefreshPolicy.current(), selection=WITH_ODDS)
    assert needs == {FINISHED: "refill"}
    [item] = planning.plan_items(store, [FINISHED], RefreshPolicy.current(), selection=WITH_ODDS)
    assert item.slices == (("odds_changes", "1"), ("winning_odds", "1"))
    FetchPipeline(store, concurrency=1, selection=WITH_ODDS).run_sync([item])
    assert planning.event_needs(store, [FINISHED], RefreshPolicy.current(), selection=WITH_ODDS) == {FINISHED: "none"}


def test_pre_match_odds_are_read_again_until_kick_off(fake: FakeSofaScore, store: Store) -> None:
    now = time.time()
    start = int(now) + 86400
    event = fake.event(NOT_STARTED)
    event["startTimestamp"] = start
    fake.add_event(event)
    add_odds(fake, NOT_STARTED)
    download(store, [NOT_STARTED], planning.SelectionPolicy())  # olay yükü saklanır, oran yok
    assert odds_paths(fake) == []

    items = planning.prematch_items(store, policy_at(now), tournament_ids=(17,), selection=WITH_ODDS)
    assert [(item.owner, item.need) for item in items] == [(Ref.event(NOT_STARTED), "refill")]
    assert items[0].slices == tuple((key, "1") for key in ODDS_KEYS)
    FetchPipeline(store, concurrency=1, selection=WITH_ODDS).run_sync(items)
    assert store.events.slice(NOT_STARTED, "odds_all", "1").state == "ok"
    # Bitmemiş maçta 404 sayılmaz: gövdesi olmayan dilim yazılmaz
    assert store.events.slice(NOT_STARTED, "odds_changes", "1").state == "not_requested"

    # max_age dolmadan: oranlar istenmez; ama okunamayan iki dilim yine istenir
    soon = planning.prematch_items(store, policy_at(now + 60), tournament_ids=(17,), selection=WITH_ODDS)
    assert [item.slices for item in soon] == [(("odds_changes", "1"), ("winning_odds", "1"))]
    # max_age dolunca hepsi; değişen oran geçmişe ikinci görüntüyü ekler, değişmeyen eklemez
    later = planning.prematch_items(store, policy_at(now + 1900), tournament_ids=(17,), selection=WITH_ODDS)
    assert later[0].slices == tuple((key, "1") for key in ODDS_KEYS)
    changed = copy.deepcopy(body("odds_all"))
    changed["markets"][0]["choices"][0]["fractionalValue"] = "5/2"
    fake.add(f"/event/{NOT_STARTED}/odds/1/all", changed)
    FetchPipeline(store, concurrency=1, selection=WITH_ODDS).run_sync(later)
    ref = Ref.event(NOT_STARTED)
    assert [snap.payload for snap in store.history.snapshots(ref, "odds_all", "1")] == [body("odds_all"), changed]
    assert len(list(store.history.snapshots(ref, "odds_featured", "1"))) == 1
    # Başlangıçtan sonra ve başlamamış başka maçın penceresi dışında: hiçbiri
    assert planning.prematch_items(store, policy_at(start + 1), tournament_ids=(17,), selection=WITH_ODDS) == []
    assert planning.prematch_items(store, policy_at(now), tournament_ids=(2361,), selection=WITH_ODDS) == []
    assert planning.prematch_items(store, policy_at(now), tournament_ids=(17,), selection=None) == []


def test_run_extras_reads_pre_match_odds(fake: FakeSofaScore, store: Store) -> None:
    now = time.time()
    event = fake.event(NOT_STARTED)
    event["startTimestamp"] = int(now) + 3600
    fake.add_event(event)
    add_odds(fake, NOT_STARTED)
    download(store, [NOT_STARTED], planning.SelectionPolicy())
    fake.reset_log()
    summary = run_extras(store, tournament_ids=[17], selection=WITH_ODDS)
    assert summary is not None and summary.ok == 1 and summary.total == 1
    assert sorted(api_paths(fake)) == sorted(["/event/9100004", *[
        endpoints.event_slice(key, NOT_STARTED, "1") for key in ODDS_KEYS]])


# --- şema, API ve dışa aktarma ------------------------------------------------------------------------------


P28_MODELS = (models.Odds, models.OddsMarket, models.OddsChoice, models.OddsLine, models.StandingsRow)


@pytest.mark.parametrize("model", P28_MODELS, ids=lambda model: model.__name__)
def test_the_new_models_carry_their_contract(model: type) -> None:
    assert dataclasses.is_dataclass(model) and issubclass(model, models.Model)
    assert model.SUMMARY.endswith(".") and model.__dataclass_params__.frozen
    for item in dataclasses.fields(model):
        assert item.default is dataclasses.MISSING
        assert item.metadata["doc"].endswith(".") and item.metadata["source"], f"{model.__name__}.{item.name}"
        if item.name.endswith("_utc"):
            assert item.metadata["format"] == "date-time" and item.metadata["unit"] == "ISO 8601 UTC"
    # FX-21'den beri sözleşmededirler: JSON Schema'da ve belgenin üretilmiş alan tablolarında
    assert model in models.MODELS


def test_odds_map_to_markets_and_flat_lines() -> None:
    odds = mappers.odds_from_payload(5, "odds_all", body("odds_all"), provider_id=1, fetched_at=1_790_000_000)
    assert odds is not None and odds.provider_id == 1 and odds.fetched_at_utc == "2026-09-21T14:13:20Z"
    first = odds.markets[0]
    assert (first.market_id, first.name, first.group, first.period, first.is_live) == (
        1, "Full time", "1X2", "Full-time", True)
    assert [(c.name, c.fractional, c.decimal, c.initial_decimal, c.change) for c in first.choices] == [
        ("1", "11/5", 3.2, 2.3, 1), ("X", "8/13", 1.615, 3.5, -1), ("2", "11/2", 6.5, 2.55, 1)]
    lines = mappers.odds_lines(odds)
    assert len(lines) == sum(len(market.choices) for market in odds.markets) == 6
    assert lines[0].to_dict()["choice"] == "1" and lines[0].market_name == "Full time"
    featured = mappers.odds_from_payload(5, "odds_featured", body("odds_featured"))
    assert featured is not None and [m.label for m in featured.markets] == ["default", "asian", "fullTime"]
    assert mappers.odds_from_payload(5, "odds_changes", body("odds_changes")) is None
    assert mappers.odds_from_payload(5, "odds_all", ["x"]) is None
    assert mappers.odds_from_payload(5, "odds_all", {"markets": ["x", {"choices": [{"x": 1}, "y"]}]}).markets[0].choices == ()
    assert [mappers.fraction_decimal(v) for v in ("1/1", "0/1", "3/0", "x", None, "1/2/3")] == [
        2.0, 1.0, None, None, None, None]


def test_the_api_lists_odds_and_their_snapshots(fake: FakeSofaScore, store: Store) -> None:
    add_odds(fake, FINISHED)
    download(store, [FINISHED, 9100002], WITH_ODDS)

    listed = client.get(f"/api/v1/events/{FINISHED}/odds").json()["data"]
    assert [(s["key"], s["sub"], s["state"]) for s in listed] == [
        ("odds_all", "1", "ok"), ("odds_changes", "1", "empty"), ("odds_featured", "1", "ok"),
        ("winning_odds", "1", "empty")]
    snaps = client.get(f"/api/v1/events/{FINISHED}/odds/odds_all").json()["data"]
    assert len(snaps) == 1 and snaps[0]["provider_id"] == 1 and snaps[0]["key"] == "odds_all"
    assert snaps[0]["markets"][0]["choices"][0]["decimal"] == 3.2
    assert client.get(f"/api/v1/events/{FINISHED}/odds/odds_featured", params={"history": "false", "sub": "1"}
                      ).json()["page"] == {"limit": 1, "next_cursor": None}
    assert client.get(f"/api/v1/events/{FINISHED}/odds/odds_all", params={"sub": "2"}).json()["data"] == []
    assert client.get("/api/v1/events/9100002/odds/odds_all").json()["data"] == []
    assert client.get("/api/v1/events/123/odds/odds_all").status_code == 404
    assert client.get(f"/api/v1/events/{FINISHED}/odds/odds_changes").status_code == 404
    assert client.get(f"/api/v1/events/{FINISHED}/odds/odds_all", params={"sub": "x"}).status_code == 422


def test_the_odds_dataset_has_one_row_per_outcome_and_snapshot(fake: FakeSofaScore, store: Store,
                                                              tmp_path: Path) -> None:
    add_odds(fake, FINISHED)
    download(store, [FINISHED, 9100002], WITH_ODDS)
    rows: List[Dict[str, Any]] = [record.to_dict() for record in ExportService(store).records("odds")]
    featured = sum(len(market["choices"]) for market in body("odds_featured")["featured"].values())
    assert len(rows) == 6 + featured
    assert {row["key"] for row in rows} == {"odds_all", "odds_featured"}
    assert {row["event_id"] for row in rows} == {FINISHED}
    result = ExportService(store).export(DatasetSpec(dataset="odds", format="csv"), tmp_path / "odds.csv")
    assert result.rows == len(rows)
    header = (tmp_path / "odds.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header.startswith("event_id,key,provider_id,fetched_at_utc,market_id,")
