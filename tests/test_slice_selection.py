"""
Kullanıcının seçtiği dilimler, uçtan uca (plan maddesi P27; docs/design/02-services.md 3.1 ve 3.2).

Ağ yok: indirmeler tests/fakes/sofascore.py'deki sahte taşıyıcıya gider (G-01'in dünyası); istek katmanı,
planlayıcı, boru hattı, yapılandırma yükleyicisi ve Store gerçektir. Sınananlar:

  * katmanlar: takibin `slices`'ı → `[slices.<spor>]` → `[defaults] slices` → kayıt defterinin varsayılanı;
    bilinmeyen ad yapılandırma hatasıdır;
  * her seçimin istek kümesi: seçilmeyen dilim hiç istenmez ve durumu `not_requested` kalır;
  * tamlık yalnızca seçilmiş ve sporda tamlık hesabına giren dilimlere bakar;
  * evre tablosu: dilim yalnızca var olabildiği evrede istenir;
  * varsayılan yapılandırma bugünkü kayıt defterinin seçimini verir (goldenlar değişmez);
  * ayarlar API'si (`defaults.slices`, `slices.<spor>`, ayarların üst verisi), takip API'sinin `slices`'ı,
    `/sports`'un dilim alanları;
  * listeler her durumdaki maçı verir, durum süzgeciyle; eski FETCH_ONLY_FINISHED okurken varsayılan süzgeçtir.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterator, List, Optional

import pytest
from fastapi.testclient import TestClient

import conftest
from characterization import WORLD, pin_default_settings
from fakes.sofascore import SITE_ROOT, FakeSofaScore
from src import redact, sports
from src.config import loader, overrides
from src.config_manager import ConfigManager
from src.exceptions import ConfigError
from src.services import planning
from src.services.listing import ListingService
from src.services.pipeline import FetchPipeline
from src.services.query import QueryService, RefreshPolicy
from src.services.status import only_finished_setting
from src.sports import SliceSelection, SliceSpec, UnknownSliceName, resolve_selection, select_slices
from src.store import FollowSpec, Scope, Store, open_store
from src.web import deps
from src.web.app import app
from src.web.jobs import default_db_path

client = TestClient(app)

LEAGUE = 17
FOOTBALL = 9100001  # turnuva 17, ev sahibi 42, konuk 38; bitmiş
TENNIS = 9200001  # turnuva 2361; bitmiş, point_by_point isteğe bağlı
FOOTBALL_SLICES = ("statistics", "team_streaks", "pregame_form", "h2h", "lineups", "incidents")


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    pin_default_settings(monkeypatch)
    loader.reset()
    yield
    monkeypatch.undo()
    loader.reset()


@pytest.fixture
def configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[str], Path]:
    """`configure(toml)`: yapılandırma dosyasını yazar ve etkin ayarları ondan yeniden kurar."""

    def write(toml: str) -> Path:
        path = tmp_path / "sofascore.toml"
        path.write_text(toml, encoding="utf-8")
        monkeypatch.setenv(loader.CONFIG_ENV, str(path))
        loader.reset()
        return path

    return write


@pytest.fixture
def fake() -> Iterator[FakeSofaScore]:
    with FakeSofaScore.from_file(WORLD) as world:
        yield world


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return open_store(tmp_path / "data")


def _download(store: Store, ids: List[int], selection: Any = planning.CONFIGURED) -> None:
    items = planning.plan_items(store, ids, RefreshPolicy.current(), selection=selection)
    FetchPipeline(store, concurrency=1, selection=selection).run_sync(items)


def _requested(fake: FakeSofaScore) -> Dict[int, List[str]]:
    """Maç başına istenen dilimler (/event hariç), kimlik → sıralı anahtar yolları."""
    found: Dict[int, List[str]] = {}
    for request in fake.requests:
        parts = request.path.split("/")
        if request.path != SITE_ROOT and len(parts) >= 3 and parts[1] == "event":
            found.setdefault(int(parts[2]), [])
            if len(parts) > 3:
                found[int(parts[2])].append("/".join(parts[3:]))
    return {event_id: sorted(paths) for event_id, paths in sorted(found.items())}


def _keys(selection: Any, sport: Optional[str], phase: Optional[str] = None) -> List[str]:
    return [spec.key for spec in select_slices("event", sport, selection, phase=phase)]


# --- katmanlar --------------------------------------------------------------------------------------------


def test_layers_resolve_in_the_designed_order() -> None:
    assert resolve_selection() == SliceSelection()
    assert _keys(resolve_selection(["core"]), "football") == list(FOOTBALL_SLICES)
    # [slices.<spor>] varsayılan seçime eklenir ya da ondan çıkarır
    assert _keys(resolve_selection(["statistics", "h2h"], sport_disable=["h2h"], sport_enable=["lineups"]),
                 "football") == ["statistics", "lineups"]
    # Takibin farkı sporun farkını ezer: spor çıkarır, takip geri ekler
    assert _keys(resolve_selection(["core"], sport_disable=["lineups"], follow={"enable": ["lineups"]}),
                 "football") == list(FOOTBALL_SLICES)
    assert _keys(resolve_selection(["core"], sport_enable=["lineups"], follow={"disable": ["core"]}), "football") == []
    # Takibin listesi yalnızca kendisidir: sporun farkı ve varsayılanlar uygulanmaz
    assert _keys(resolve_selection(["statistics"], sport_enable=["h2h"], follow={"include": ["incidents"]}),
                 "football") == ["incidents"]
    assert _keys(resolve_selection(follow=["incidents", "lineups"]), "football") == ["lineups", "incidents"]


def test_follow_slices_are_checked_and_normalised() -> None:
    assert sports.follow_slices(None) is None
    assert sports.follow_slices(["h2h", " h2h"]) == {"include": ("h2h",)}
    assert sports.follow_slices({"enable": ["odds"]}) == {"enable": ("odds",), "disable": ()}
    for bad in ({"include": ["h2h"], "enable": []}, {"only": ["h2h"]}, {"include": "h2h"}, "h2h", {"enable": [""]}):
        with pytest.raises(ValueError):
            sports.follow_slices(bad)
    with pytest.raises(UnknownSliceName, match="nope"):
        sports.follow_slices({"disable": ["nope"]})
    with pytest.raises(UnknownSliceName):
        resolve_selection(["core"], sport_enable=["nope"])


def test_the_narrowest_follow_wins() -> None:
    policy = planning.SelectionPolicy(defaults=("core",), follows={
        ("event", FOOTBALL): {"include": ("h2h",)},
        ("tournament", LEAGUE): {"include": ("statistics",)},
        ("team", 38): {"include": ("lineups",)},
    })
    assert _keys(policy.for_event("football", event_id=FOOTBALL, tournament_id=LEAGUE, team_ids=(42, 38)),
                 "football") == ["h2h"]
    assert _keys(policy.for_event("football", event_id=1, tournament_id=LEAGUE, team_ids=(42, 38)),
                 "football") == ["statistics"]
    assert _keys(policy.for_event("football", event_id=1, tournament_id=8, team_ids=(42, 38)), "football") == ["lineups"]
    assert _keys(policy.for_event("football", event_id=1, tournament_id=8, team_ids=(1, 2)),
                 "football") == list(FOOTBALL_SLICES)


@pytest.mark.parametrize("toml, where", [
    ('[defaults]\nslices = ["core", "nope"]\n', "[defaults] slices"),
    ('[slices.football]\nenable = ["nope"]\n', "[slices].football"),
    ('[[follow]]\ntournament = 17\nslices = {enable = ["nope"]}\n', "[[follow]] #1 slices"),
    ('[[follow]]\ntournament = 17\nslices = ["core", "nope"]\n', "[[follow]] #1 slices"),
])
def test_unknown_names_are_a_config_error(tmp_path: Path, toml: str, where: str) -> None:
    path = tmp_path / "sofascore.toml"
    path.write_text(toml, encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown slice or slice group 'nope'") as raised:
        loader.load_settings(config_file=path, environ={}, dotenv_values=None, overrides_file=None)
    assert where in str(raised.value)


def test_the_config_file_becomes_the_policy(configure: Callable[[str], Path]) -> None:
    configure('[defaults]\nslices = ["statistics", "h2h", "lineups"]\n'
              '[slices.football]\ndisable = ["lineups"]\n'
              '[slices.tennis]\nenable = ["point_by_point"]\n'
              '[[follow]]\ntournament = 2361\nslices = {disable = ["h2h"]}\n')
    policy = planning.configured_policy()

    assert policy.defaults == ("statistics", "h2h", "lineups")
    assert _keys(policy.for_sport("football"), "football") == ["statistics", "h2h"]
    assert _keys(policy.for_sport("tennis"), "tennis") == ["statistics", "h2h", "lineups", "point_by_point"]
    assert _keys(policy.for_event("tennis", tournament_id=2361), "tennis") == ["statistics", "lineups",
                                                                              "point_by_point"]
    assert planning.configured_policy() is policy  # ayarlar değişmedikçe önbellekten


def test_the_default_configuration_selects_what_the_registry_selects() -> None:
    """G-01 ve CLI goldenlarının dayanağı: varsayılan yapılandırmayla istekler ve dosyalar main ile aynıdır."""
    policy = planning.configured_policy()
    assert policy.defaults is None and not policy.sports and not policy.follows
    for sport in (*sports.sport_slugs(), None, "quidditch"):
        for phase in (None, *sports.PHASES):
            assert _keys(policy.for_sport(sport), sport, phase) == _keys(None, sport, phase), (sport, phase)


def test_an_unset_default_keeps_the_registry_s_default_off_slices_off(configure: Callable[[str], Path],
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """
    [defaults] slices verilmediyse taban kayıt defterinin `default_enabled`'ıdır (ör. P28'in oranları): ayarın
    gösterilen varsayılanı ["core"] olsa da. Yazılan ["core"] ise grubun her dilimini seçer.
    """
    off = SliceSpec("probe", "/event/{event_id}/probe", default_enabled=False)
    monkeypatch.setattr(sports, "DETAIL_SLICES", (*sports.DETAIL_SLICES, off))

    assert "probe" not in _keys(planning.configured_policy().for_sport("football"), "football")
    configure('[defaults]\nslices = ["core"]\n')
    assert "probe" in _keys(planning.configured_policy().for_sport("football"), "football")
    configure('[slices.football]\nenable = ["probe"]\n')
    assert "probe" in _keys(planning.configured_policy().for_sport("football"), "football")
    assert "probe" not in _keys(planning.configured_policy().for_sport("tennis"), "tennis")


# --- istek kümeleri ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("selection, football, tennis", [
    (None, FOOTBALL_SLICES, ("statistics", "team_streaks", "pregame_form", "h2h", "lineups", "incidents",
                             "point_by_point")),
    (["core"], FOOTBALL_SLICES, ("statistics", "team_streaks", "pregame_form", "h2h", "lineups", "incidents",
                                 "point_by_point")),
    (["statistics"], ("statistics",), ("statistics",)),
    (SliceSelection(base=("core",), disable=("lineups", "incidents")), ("statistics", "team_streaks", "pregame_form",
                                                                         "h2h"),
     ("statistics", "team_streaks", "pregame_form", "h2h", "point_by_point")),
    (["point_by_point"], (), ("point_by_point",)),
    ([], (), ()),
])
def test_only_selected_slices_are_requested(fake: FakeSofaScore, store: Store, selection: Any,
                                            football: tuple, tennis: tuple) -> None:
    _download(store, [FOOTBALL, TENNIS], selection)

    paths = {key: sports.get_slice(key).path.split("/")[-1] for key in sports.known_slice_names()  # type: ignore[union-attr]
             if sports.get_slice(key) is not None}
    assert _requested(fake) == {FOOTBALL: sorted(paths[key] for key in football),
                                TENNIS: sorted(paths[key] for key in tennis)}
    for event_id, chosen, sport in ((FOOTBALL, football, "football"), (TENNIS, tennis, "tennis")):
        state = next(iter(store.events.states(Scope(event_ids=(event_id,)))))
        for spec in sports.slices_for(sport):
            # Seçilmeyen dilim istenmedi; satırı yoktur ve durumu `not_requested` olarak bildirilir
            assert (state.slice(spec.key).state != "not_requested") == (spec.key in chosen), (event_id, spec.key)


def test_the_configured_selection_reaches_the_pipeline(configure: Callable[[str], Path], fake: FakeSofaScore,
                                                       store: Store) -> None:
    """Seçim verilmeyen yollar (eşitleme, liste servisi, tek maç) yapılandırmanın seçimini kullanır."""
    configure('[defaults]\nslices = ["statistics", "h2h"]\n'
              '[[follow]]\ntournament = 17\nslices = ["lineups"]\n')
    _download(store, [FOOTBALL, TENNIS])

    assert _requested(fake) == {FOOTBALL: ["lineups"], TENNIS: ["h2h", "statistics"]}


def test_a_follow_in_the_follows_table_selects_for_its_events(fake: FakeSofaScore, store: Store) -> None:
    """API'den eklenen takip (origin api): seçimi takip tablosundadır."""
    store.follows.add(FollowSpec(kind="team", entity_id=42, name="Home side", slices={"include": ["incidents"]}),
                      origin="api")
    _download(store, [FOOTBALL, TENNIS])

    assert _requested(fake)[FOOTBALL] == ["incidents"]
    assert len(_requested(fake)[TENNIS]) == 7


# --- tamlık ------------------------------------------------------------------------------------------------


def test_completeness_counts_only_selected_counting_slices(fake: FakeSofaScore, store: Store) -> None:
    _download(store, [FOOTBALL, TENNIS], ["statistics", "point_by_point"])
    policy = RefreshPolicy.current()
    states = {state.event.id: state for state in store.events.states(Scope(event_ids=(FOOTBALL, TENNIS)))}

    for selection in (["statistics"], ["statistics", "point_by_point"], ["point_by_point"]):
        assert planning.compute_need(states[FOOTBALL], selection, policy) == "none"
        assert planning.compute_need(states[TENNIS], selection, policy) == "none"
    # Seçimi genişleyen maç eksik dilimleri için yeniden doldurulur; isteğe bağlı dilim (tenisin point_by_point'i)
    # tamlığa girmez ama doldurmada istenir
    assert planning.compute_need(states[FOOTBALL], ["statistics", "h2h"], policy) == "refill"
    assert planning.missing_slice_keys(states[TENNIS], ["core"]) == ("team_streaks", "pregame_form", "h2h", "lineups",
                                                                    "incidents")
    item = planning.work_item(TENNIS, states[TENNIS], "refill", ["h2h", "point_by_point"])
    assert item is not None and item.slices == (("h2h", ""),)
    assert planning.event_needs(store, [FOOTBALL, TENNIS], policy) == {FOOTBALL: "refill", TENNIS: "refill"}
    assert planning.event_needs(store, [FOOTBALL, TENNIS], policy, selection=["statistics"]) == {
        FOOTBALL: "none", TENNIS: "none"}


def test_a_refill_requests_only_the_selected_missing_slices(fake: FakeSofaScore, store: Store) -> None:
    _download(store, [FOOTBALL], ["statistics"])
    fake.reset_log()
    _download(store, [FOOTBALL], ["statistics", "h2h"])

    assert _requested(fake) == {FOOTBALL: ["h2h"]}


# --- evreler -----------------------------------------------------------------------------------------------


def test_the_phase_table() -> None:
    assert {status: planning.phase_of(status) for status in (
        "not_started", "void", "live", "completed", "decided_without_play", "unknown", None)} == {
        "not_started": "pre", "void": "pre", "live": "live", "completed": "post", "decided_without_play": "post",
        "unknown": None, None: None,
    }
    for phase, esports, cricket in (("pre", [], []), ("live", ["esports_games"], ["innings"]),
                                    ("post", ["esports_games"], ["innings"]), (None, ["esports_games"], ["innings"])):
        assert [key for key in _keys(["core"], "esports", phase) if key == "esports_games"] == esports
        assert [key for key in _keys(["core"], "cricket", phase) if key == "innings"] == cricket


def test_a_slice_is_expected_only_in_its_phases(monkeypatch: pytest.MonkeyPatch) -> None:
    probe = SliceSpec("probe", "/event/{event_id}/probe", phases=frozenset({"pre", "live"}))
    monkeypatch.setattr(sports, "DETAIL_SLICES", (*sports.DETAIL_SLICES, probe))

    assert "probe" in planning.expected_slice_keys("football", ["core"], phase="pre")
    assert "probe" in planning.expected_slice_keys("football", ["core"], phase="live")
    assert "probe" not in planning.expected_slice_keys("football", ["core"], phase="post")
    assert "probe" not in planning.expected_slice_keys("football", ["statistics"], phase="pre")


# --- ayarlar API'si ----------------------------------------------------------------------------------------


@pytest.fixture
def sandbox(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """overrides.json; test bitince dosya, `.env`, ortam ve etkin ayarlar eski haline döner."""
    path = overrides.overrides_path()
    assert not path.exists()
    env_before = Path(conftest.ENV_FILE).read_bytes()
    environ_before = dict(os.environ)
    loader.reload()
    yield path
    monkeypatch.undo()
    for leftover in (path, Path(f"{path}.lock")):
        leftover.unlink(missing_ok=True)
    Path(conftest.ENV_FILE).write_bytes(env_before)
    for name in set(os.environ) - set(environ_before):
        del os.environ[name]
    os.environ.update(environ_before)
    loader.reload()
    redact.refresh()
    deps.job_store().rebind(default_db_path(conftest.DATA_DIR))


def _settings_doc() -> Dict[str, Any]:
    response = client.get("/api/v1/settings")
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _patch(values: Dict[str, Any]) -> Any:
    return client.patch("/api/v1/settings", json={"values": values})


def test_the_default_selection_can_be_changed_through_the_api(sandbox: Path) -> None:
    rows = {row["key"]: row for row in _settings_doc()["settings"]}
    assert rows["defaults.slices"]["value"] == ["core"] and rows["defaults.slices"]["writable"] is True

    assert _patch({"defaults.slices": ["statistics", "h2h"]}).status_code == 200
    assert json.loads(sandbox.read_text(encoding="utf-8")) == {"defaults": {"slices": ["statistics", "h2h"]}}
    assert planning.configured_policy().defaults == ("statistics", "h2h")

    refused = _patch({"defaults.slices": ["statistics", "nope"]})
    assert refused.status_code == 422, refused.text
    assert "unknown slice or slice group 'nope'" in refused.json()["error"]["details"]["errors"][0]["message"]
    assert planning.configured_policy().defaults == ("statistics", "h2h")


def test_a_sport_s_selection_can_be_changed_through_the_api(sandbox: Path) -> None:
    rows = {row["sport"]: row for row in _settings_doc()["slices"]}
    assert list(rows) == list(sports.sport_slugs())
    assert rows["football"] == {"sport": "football", "enable": [], "disable": [], "source": "default",
                                "source_name": "", "locked": False, "writable": True}

    assert _patch({"slices.football": {"disable": ["lineups", "incidents"]}}).status_code == 200
    assert json.loads(sandbox.read_text(encoding="utf-8")) == {
        "slices": {"football": {"enable": [], "disable": ["lineups", "incidents"]}}}
    row = {row["sport"]: row for row in _settings_doc()["slices"]}["football"]
    assert (row["disable"], row["source"], row["source_name"]) == (["lineups", "incidents"], "overrides", str(sandbox))
    assert _keys(planning.configured_policy().for_sport("football"), "football") == list(FOOTBALL_SLICES[:4])

    for bad, status in (({"enable": ["nope"]}, 422), ({"only": []}, 422), (["h2h"], 422)):
        assert _patch({"slices.football": bad}).status_code == status
    assert _patch({"slices.quidditch": {"enable": []}}).status_code == 422

    assert _patch({"slices.football": None}).status_code == 200
    assert not sandbox.exists() or "slices" not in json.loads(sandbox.read_text(encoding="utf-8"))
    assert _keys(planning.configured_policy().for_sport("football"), "football") == list(FOOTBALL_SLICES)


def test_a_sport_s_selection_pinned_by_the_config_file_is_refused(sandbox: Path,
                                                                   configure: Callable[[str], Path]) -> None:
    path = configure('[slices.tennis]\ndisable = ["point_by_point"]\n')
    loader.reload()
    rows = {row["sport"]: row for row in _settings_doc()["slices"]}
    assert rows["tennis"] == {"sport": "tennis", "enable": [], "disable": ["point_by_point"], "source": "file",
                              "source_name": str(path), "locked": True, "writable": False}
    assert rows["football"]["locked"] is False

    refused = _patch({"slices.tennis": {"enable": ["point_by_point"]}})
    assert refused.status_code == 400 and refused.json()["error"]["details"]["locked"][0]["key"] == "slices.tennis"
    assert not sandbox.exists()


def test_every_setting_has_its_metadata(sandbox: Path) -> None:
    document = _settings_doc()
    metadata = {row["key"]: row for row in document["metadata"]}

    assert list(metadata) == [row["key"] for row in document["settings"]]
    assert metadata["client.rate"] == {
        "key": "client.rate", "section": "client", "type": "rate",
        "description": "Requests per second across all processes; 0 or \"off\" removes the limit.",
        "minimum": 0.0, "exclusive_minimum": False, "maximum": 1000.0, "choices": None, "max_length": None,
        "restart_needed": False,
    }
    assert metadata["display.language"]["type"] == "choice" and metadata["display.language"]["choices"] == ["en", "tr"]
    assert metadata["server.port"]["restart_needed"] is True and metadata["server.port"]["maximum"] == 65535
    assert metadata["defaults.slices"]["type"] == "string_list"
    assert metadata["client.base_url"]["max_length"] == 500
    assert {row["type"] for row in metadata.values()} <= {"string", "path", "url", "integer", "number", "boolean",
                                                          "choice", "string_list", "rate", "seasons"}


# --- takip API'si ------------------------------------------------------------------------------------------


@pytest.fixture
def leagues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """tests/test_api_v1_follows.py ile aynı düzen: geçici lig dosyası ve boş bir veri dizini."""
    config = tmp_path / "config"
    config.mkdir()
    (config / "leagues.txt").write_text("# leagues\nPremier League: 17\n", encoding="utf-8")
    (config / "league_sports.json").write_text(json.dumps({"17": "football"}), encoding="utf-8")
    data = tmp_path / "follows-data"
    monkeypatch.setenv("DATA_DIR", str(data))
    open_store(data)
    manager = ConfigManager()
    monkeypatch.setattr(manager, "league_config_path", str(config / "leagues.txt"))
    monkeypatch.setattr(manager, "_leagues_mtime", None)
    manager.mirror_follows()
    yield config / "leagues.txt"
    monkeypatch.undo()
    manager._leagues_mtime = None
    manager.get_leagues()


@pytest.fixture
def with_config_file(monkeypatch: pytest.MonkeyPatch) -> None:
    real = deps.loaded_settings
    monkeypatch.setattr(deps, "loaded_settings",
                        lambda: SimpleNamespace(settings=real().settings, config_file="/etc/sofascore.toml"))


def test_a_follow_takes_a_selection(leagues: Path) -> None:
    created = client.post("/api/v1/follows", json={"kind": "team", "entity_id": 42, "name": "Arsenal",
                                                   "slices": {"include": ["statistics", "odds"]}})
    assert created.status_code == 201, created.text
    assert created.json()["data"]["slices"] == {"include": ["statistics", "odds"]}
    assert "slices" in created.json()["data"]["writable"]
    stored = open_store(leagues.parent.parent / "follows-data").follows.get("team", 42)
    assert stored is not None and stored.slices == {"include": ["statistics", "odds"]}

    changed = client.patch("/api/v1/follows/team:42", json={"slices": {"disable": ["lineups"]}})
    assert changed.status_code == 200 and changed.json()["data"]["slices"] == {"enable": [], "disable": ["lineups"]}
    reset = client.patch("/api/v1/follows/team:42", json={"slices": None})
    assert reset.status_code == 200 and reset.json()["data"]["slices"] is None
    untouched = client.patch("/api/v1/follows/team:42", json={"live": True})
    assert untouched.json()["data"]["slices"] is None


@pytest.mark.parametrize("slices", [{"include": ["nope"]}, {"include": ["h2h"], "enable": ["odds"]}, {"only": ["h2h"]}])
def test_an_invalid_selection_is_refused(leagues: Path, slices: Dict[str, Any]) -> None:
    refused = client.post("/api/v1/follows", json={"kind": "team", "entity_id": 42, "name": "Arsenal",
                                                   "slices": slices})
    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["details"] == {"field": "slices"}
    assert client.get("/api/v1/follows/team:42").status_code == 404


def test_a_league_file_follow_cannot_hold_a_selection(leagues: Path) -> None:
    refused = client.post("/api/v1/follows", json={"entity_id": 8, "name": "LaLiga", "slices": {"include": ["h2h"]}})
    assert refused.status_code == 400
    assert refused.json()["error"]["details"]["unsupported"] == ["slices"]
    patched = client.patch("/api/v1/follows/tournament:17", json={"slices": {"include": ["h2h"]}})
    assert patched.status_code == 400 and patched.json()["error"]["details"]["unsupported"] == ["slices"]
    assert client.patch("/api/v1/follows/tournament:17", json={"slices": None}).status_code == 200


def test_with_a_config_file_a_tournament_follow_holds_its_selection(leagues: Path, with_config_file: None) -> None:
    created = client.post("/api/v1/follows", json={"entity_id": 8, "name": "LaLiga",
                                                   "slices": {"enable": ["odds"], "disable": ["lineups"]}})
    assert created.status_code == 201, created.text
    assert created.json()["data"]["origin"] == "api"
    store = open_store(leagues.parent.parent / "follows-data")
    policy = planning.configured_policy(store)
    assert _keys(policy.for_event("football", tournament_id=8), "football") == [
        key for key in FOOTBALL_SLICES if key != "lineups"]


# --- /sports ---------------------------------------------------------------------------------------------


def test_sports_carry_the_registry_fields_and_the_configured_selection(configure: Callable[[str], Path]) -> None:
    football = client.get("/api/v1/sports/football").json()["data"]
    first = football["slices"][0]
    assert first == {"key": "statistics", "path": "/event/{event_id}/statistics", "required": True,
                     "default_enabled": True, "selected": True, "group": "core", "owner": "event",
                     "phases": ["pre", "live", "post"], "keep_history": False, "max_age_seconds": None}
    esports = {s["key"]: s for s in client.get("/api/v1/sports/esports").json()["data"]["slices"]}
    assert esports["esports_games"]["phases"] == ["live", "post"] and esports["esports_games"]["required"] is False

    configure('[defaults]\nslices = ["statistics"]\n[slices.football]\nenable = ["h2h"]\n')
    selected = [s["key"] for s in client.get("/api/v1/sports/football").json()["data"]["slices"] if s["selected"]]
    assert selected == ["statistics", "h2h"]


# --- listeler ve durum süzgeci -----------------------------------------------------------------------------


def test_lists_show_every_status_and_filter_by_status(fake: FakeSofaScore, tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "lists"
    store = open_store(data)
    ListingService(store, only_finished=True, concurrency=1).schedule(LEAGUE, 61627)  # 9100001-9100003 bitmiş, 9100004 başlamamış
    monkeypatch.setenv("DATA_DIR", str(data))

    def listed(**params: Any) -> List[int]:
        response = client.get("/api/v1/events", params=params)
        assert response.status_code == 200, response.text
        return sorted(item["id"] for item in response.json()["data"])

    assert listed() == [9100001, 9100002, 9100003, 9100004]
    assert listed(status="not_started") == [9100004]
    assert listed(status=["completed", "not_started"]) == [9100001, 9100002, 9100003, 9100004]
    assert listed(status="live") == []

    # Eski listeler: FETCH_ONLY_FINISHED okurken uygulanan varsayılan süzgeçtir
    query = QueryService(store)
    for value, expected in (("true", [9100001, 9100002, 9100003]), ("false", [9100001, 9100002, 9100003, 9100004])):
        monkeypatch.setenv("FETCH_ONLY_FINISHED", value)
        rows = query.season_matches_legacy(61627, LEAGUE, only_finished=only_finished_setting())
        assert sorted(int(row["match_id"]) for row in rows) == expected
