"""
sofascore_scraper/store/follows.py: `follows` tablosu ve onu dolduran üç yol (docs/design/01-storage.md bölüm 2.3;
plan maddesi ST-17).

  * FollowStore: ekleme, değiştirme, silme, `apply` (yinelenebilir; yalnızca kendi kaynağını budar),
    kaynaklar arası öncelik ve ad tekliği.
  * `apply_follows`: deposu açık olmayan çağıranların yolu; state.db yoksa hiçbir şeye dokunmaz.
  * ConfigManager ve league_sports: `leagues.txt` ile `league_sports.json` doğruluk kaynağı olarak kalır,
    tablo onların aynasıdır; dosyalar tablodan asla yeniden yazılmaz.
  * build_context: yapılandırma dosyasının `[[follow]]` girdileri "config" kaynağıyla uygulanır.

Tümü çevrimdışı ve geçici dizinlerde.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Dict, Iterator, List, Optional, Tuple

import pytest

from sofascore_scraper.config import FollowSpec as ConfigFollowSpec
from sofascore_scraper.config import loader
from sofascore_scraper.config_manager import ConfigManager, mirror_league_follows
from sofascore_scraper.services.context import build_context
from sofascore_scraper.store import (
    ApplyResult,
    Follow,
    FollowConflict,
    FollowExists,
    FollowManaged,
    FollowSpec,
    FollowStore,
    SchemaTooNew,
    Store,
    StoreError,
    apply_follows,
    open_store,
)
from sofascore_scraper.store import api as api_mod
from sofascore_scraper.store import follows as follows_mod
from sofascore_scraper.web import league_sports

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PL = FollowSpec("tournament", 17, "Premier League", "football")
LALIGA = FollowSpec("tournament", 8, "LaLiga", "football")
NBA = FollowSpec("tournament", 132, "NBA", "basketball")


@pytest.fixture(autouse=True)
def _close_stores() -> Iterator[None]:
    """Testin açtığı depolar kayıt defterinde (ve dosyaları açık) kalmasın."""
    yield
    for store in list(api_mod._registry.values()):
        store.close()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def store(data_dir: Path) -> Store:
    return open_store(data_dir)


@pytest.fixture
def follows(store: Store) -> FollowStore:
    return store.follows


def _state_path(data_dir: Path) -> Path:
    return data_dir / ".meta" / "state.db"


def _rows(follows: FollowStore) -> List[Tuple[str, int, str, Optional[str], str, int]]:
    return [(f.kind, f.entity_id, f.name, f.sport, f.origin, f.position) for f in follows.list()]


def _data_version(data_dir: Path) -> Callable[[], int]:
    """Başka bir bağlantının gözünden: state.db'ye her yazım (commit) bu sayıyı değiştirir."""
    conn = sqlite3.connect(str(_state_path(data_dir)))

    def read() -> int:
        return int(conn.execute("PRAGMA data_version").fetchone()[0])

    read.close = conn.close  # type: ignore[attr-defined]
    return read


# === FollowStore: tek satır işlemleri ================================================================


def test_store_exposes_the_follows_table(store: Store, follows: FollowStore):
    assert isinstance(follows, FollowStore)
    assert follows.list() == [] and follows.leagues() == {} and follows.get("tournament", 17) is None
    assert store.info(sizes=False).rows["state"]["follows"] == 0


def test_add_returns_the_row_and_appends_at_the_end(follows: FollowStore):
    first = follows.add(PL)
    second = follows.add(FollowSpec("team", 42, "Arsenal", seasons="current", live=True, enabled=False))

    assert isinstance(first, Follow)
    assert (first.kind, first.entity_id, first.name, first.sport) == ("tournament", 17, "Premier League", "football")
    assert (first.seasons, first.slices, first.live, first.enabled) == ("all", None, False, True)
    assert (first.origin, first.position) == ("api", 0)
    assert first.created_at == first.updated_at > 0
    assert (second.origin, second.position, second.seasons, second.live, second.enabled) == (
        "api", 1, "current", True, False,
    )
    assert follows.get("tournament", 17) == first
    assert follows.list() == [first, second]
    assert first.spec() == PL


def test_add_refuses_a_duplicate_id_and_a_duplicate_tournament_name(follows: FollowStore):
    """Bugünkü leagues.txt kuralı: kimlik başına bir satır, turnuva adı başına bir satır."""
    follows.add(PL, origin="legacy")

    with pytest.raises(FollowExists):
        follows.add(FollowSpec("tournament", 17, "Another Name"))
    with pytest.raises(FollowExists):
        follows.add(FollowSpec("tournament", 99, "Premier League"))
    assert _rows(follows) == [("tournament", 17, "Premier League", "football", "legacy", 0)]

    # Ad tekliği yalnızca turnuvalar içindir; kimlik tekliği tür başınadır
    follows.add(FollowSpec("team", 17, "Premier League"))
    follows.add(FollowSpec("player", 1, "Premier League"))
    follows.add(FollowSpec("player", 2, "Premier League"))
    assert len(follows.list()) == 4


def test_follow_errors_are_store_errors():
    assert issubclass(FollowExists, StoreError) and issubclass(FollowManaged, StoreError)


def test_update_changes_the_given_fields_only(follows: FollowStore):
    follows.add(PL)

    changed = follows.update("tournament", 17, name="PL", sport=None, seasons="last:2",
                             slices={"include": ["core"]}, live=True, enabled=False)

    assert (changed.name, changed.sport, changed.seasons, changed.slices, changed.live, changed.enabled) == (
        "PL", None, "last:2", {"include": ["core"]}, True, False,
    )
    assert (changed.origin, changed.position) == ("api", 0)
    assert follows.update("tournament", 17, live=False).name == "PL"  # verilmeyen alanlar kalır
    assert follows.get("tournament", 17).live is False


def test_update_that_changes_nothing_writes_nothing(data_dir: Path, follows: FollowStore):
    follows.add(PL)
    version = _data_version(data_dir)
    before = version()

    assert follows.update("tournament", 17, name="Premier League", sport="football") == follows.get("tournament", 17)

    assert version() == before
    version.close()


def test_update_refuses_unknown_fields_missing_rows_and_taken_names(follows: FollowStore):
    follows.add(PL)
    follows.add(LALIGA)

    with pytest.raises(TypeError, match="origin"):
        follows.update("tournament", 17, origin="config")
    with pytest.raises(KeyError):
        follows.update("tournament", 999, name="x")
    with pytest.raises(FollowExists):
        follows.update("tournament", 17, name="LaLiga")
    with pytest.raises(ValueError):
        follows.update("tournament", 17, seasons="sometimes")
    assert follows.leagues() == {17: "Premier League", 8: "LaLiga"}


def test_remove_reports_whether_a_row_existed(follows: FollowStore):
    follows.add(PL)
    assert follows.remove("tournament", 17) is True
    assert follows.remove("tournament", 17) is False
    assert follows.list() == []


def test_update_and_remove_of_a_config_follow_raise_follow_managed(follows: FollowStore):
    follows.apply([PL], origin="config")
    follows.apply([LALIGA], origin="legacy")

    with pytest.raises(FollowManaged):
        follows.update("tournament", 17, enabled=False)
    with pytest.raises(FollowManaged):
        follows.remove("tournament", 17)
    assert follows.get("tournament", 17).enabled is True

    # legacy satırlar Store düzeyinde yazılabilir (servis önce dosyayı yazar, sonra yansıtır)
    assert follows.update("tournament", 8, sport=None).sport is None
    assert follows.remove("tournament", 8) is True


def test_adopt_moves_a_legacy_row_to_api_and_keeps_its_fields(follows: FollowStore):
    """FX-19: leagues.txt'in satırı takip tablosuna alınır; alanlar ve konum aynı, sonraki ayna geri almaz."""
    follows.apply([PL, LALIGA], origin="legacy")
    before = follows.get("tournament", 8)
    adopted = follows.adopt("tournament", 8)
    assert (adopted.origin, adopted.position, adopted.name, adopted.sport) == ("api", before.position, "LaLiga",
                                                                               "football")
    assert follows.adopt("tournament", 8) == adopted  # zaten api: hiçbir şey yazılmaz
    # Dosya satırı hâlâ dursa da ayna onu geri almaz; dosyadan çıkınca da silmez
    assert [c.reason for c in follows.apply([PL, LALIGA], origin="legacy").conflicts] == ["owned_by_api"]
    follows.apply([PL], origin="legacy")
    assert follows.get("tournament", 8).origin == "api"
    with pytest.raises(KeyError):
        follows.adopt("tournament", 99)
    with pytest.raises(ValueError):
        follows.adopt("tournament", 8, origin="legacy")
    follows.apply([NBA], origin="config", prune=False)
    with pytest.raises(FollowManaged):
        follows.adopt("tournament", 132)


def test_list_filters_and_orders_by_position(follows: FollowStore):
    follows.apply([PL, FollowSpec("team", 42, "Arsenal", enabled=False)], origin="config")
    follows.add(NBA)

    assert [f.entity_id for f in follows.list()] == [17, 42, 132]
    assert [f.entity_id for f in follows.list(kind="tournament")] == [17, 132]
    assert [f.entity_id for f in follows.list(enabled=True)] == [17, 132]
    assert [f.entity_id for f in follows.list(enabled=False)] == [42]
    assert [f.entity_id for f in follows.list(origin="api")] == [132]
    assert [f.entity_id for f in follows.list(kind="tournament", origin="config", enabled=True)] == [17]
    assert follows.list(origin="legacy") == []


def test_leagues_has_the_shape_of_config_manager_get_leagues(follows: FollowStore):
    """Bütün kaynakların turnuvaları, kapalı olanlar dahil, konum sırasıyla; başka türler girmez."""
    follows.apply([PL, LALIGA], origin="legacy")
    follows.add(FollowSpec("tournament", 132, "NBA", enabled=False))
    follows.add(FollowSpec("team", 42, "Arsenal"))

    leagues = follows.leagues()

    assert leagues == {17: "Premier League", 8: "LaLiga", 132: "NBA"}
    assert list(leagues) == [17, 8, 132]


@pytest.mark.parametrize("seasons, stored", [
    ("all", "all"), ("current", "current"), ("last:3", "last:3"), ((61627, 52186), (61627, 52186)),
    ([61627], (61627,)), ((), ()),
])
def test_seasons_round_trip(follows: FollowStore, seasons, stored):
    assert follows.add(FollowSpec("tournament", 17, "PL", seasons=seasons)).seasons == stored
    assert follows.get("tournament", 17).seasons == stored


def test_slices_round_trip_as_plain_json(follows: FollowStore):
    selection = MappingProxyType({"enable": ("odds", "standings"), "disable": ["lineups"]})

    row = follows.add(FollowSpec("tournament", 17, "PL", slices=selection))

    assert row.slices == {"enable": ["odds", "standings"], "disable": ["lineups"]}
    assert follows.add(FollowSpec("tournament", 8, "LaLiga", slices=None)).slices is None


@pytest.mark.parametrize("spec", [
    FollowSpec("league", 17, "PL"),
    FollowSpec("tournament", "17", "PL"),  # type: ignore[arg-type]
    FollowSpec("tournament", True, "PL"),
    FollowSpec("tournament", 17, "  "),
    FollowSpec("tournament", 17, "PL", sport=""),
    FollowSpec("tournament", 17, "PL", seasons="last:0"),
    FollowSpec("tournament", 17, "PL", seasons="sometimes"),
    FollowSpec("tournament", 17, "PL", seasons=("a",)),  # type: ignore[arg-type]
    FollowSpec("tournament", 17, "PL", slices=["core"]),  # type: ignore[arg-type]
    FollowSpec("tournament", 17, "PL", slices={"include": object()}),
])
def test_invalid_specs_are_rejected_before_anything_is_written(follows: FollowStore, spec: FollowSpec):
    with pytest.raises(ValueError):
        follows.add(spec)
    with pytest.raises(ValueError):
        follows.apply([PL, spec], origin="legacy")
    assert follows.list() == []


def test_unknown_origin_is_rejected(follows: FollowStore, data_dir: Path):
    with pytest.raises(ValueError, match="origin"):
        follows.add(PL, origin="file")
    with pytest.raises(ValueError, match="origin"):
        follows.apply([PL], origin="file")
    with pytest.raises(ValueError, match="origin"):
        apply_follows(data_dir, [PL], origin="file")


# === apply ===========================================================================================


def test_apply_makes_the_origin_equal_to_the_list(follows: FollowStore):
    result = follows.apply([PL, LALIGA], origin="legacy")

    assert isinstance(result, ApplyResult) and result.changed and result.origin == "legacy"
    assert [f.entity_id for f in result.added] == [17, 8]
    assert (result.updated, result.removed, result.unchanged, result.conflicts) == ((), (), 0, ())
    assert _rows(follows) == [
        ("tournament", 17, "Premier League", "football", "legacy", 0),
        ("tournament", 8, "LaLiga", "football", "legacy", 1),
    ]

    # 17 çıktı, 8 değişti ve öne geldi, 132 eklendi
    result = follows.apply([FollowSpec("tournament", 8, "La Liga", None), NBA], origin="legacy")

    assert [f.entity_id for f in result.added] == [132]
    assert [(f.entity_id, f.name, f.sport, f.position) for f in result.updated] == [(8, "La Liga", None, 0)]
    assert [f.entity_id for f in result.removed] == [17]
    assert _rows(follows) == [
        ("tournament", 8, "La Liga", None, "legacy", 0),
        ("tournament", 132, "NBA", "basketball", "legacy", 1),
    ]


def test_apply_is_idempotent_and_a_repeat_writes_nothing(data_dir: Path, follows: FollowStore):
    follows.apply([PL, LALIGA], origin="legacy")
    before = follows.list()
    version = _data_version(data_dir)
    seen = version()

    for _ in range(3):
        again = follows.apply([PL, LALIGA], origin="legacy")
        assert (again.changed, again.unchanged, again.added, again.updated, again.removed) == (False, 2, (), (), ())

    assert follows.list() == before  # updated_at dahil
    assert version() == seen
    version.close()


def test_a_repeat_does_not_wait_for_the_write_lock(data_dir: Path, follows: FollowStore):
    """Tablo güncelken ayna yazma kilidi almaz: başka bir süreç yazarken ConfigManager'ı bekletmez."""
    follows.apply([PL], origin="legacy")
    writer = sqlite3.connect(str(_state_path(data_dir)), isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")
    try:
        assert follows.apply([PL], origin="legacy").changed is False
    finally:
        writer.execute("ROLLBACK")
        writer.close()


def test_apply_prunes_only_its_own_origin(follows: FollowStore):
    follows.apply([PL], origin="legacy")
    follows.apply([LALIGA], origin="config")
    follows.add(NBA)

    result = follows.apply([], origin="legacy")

    assert [f.entity_id for f in result.removed] == [17]
    assert _rows(follows) == [
        ("tournament", 8, "LaLiga", "football", "config", 0),
        ("tournament", 132, "NBA", "basketball", "api", 1),
    ]
    assert follows.apply([], origin="config").removed[0].entity_id == 8
    assert follows.apply([], origin="api").removed[0].entity_id == 132
    assert follows.list() == []


def test_apply_without_prune_keeps_the_rows_that_are_not_listed(follows: FollowStore):
    follows.apply([PL, LALIGA], origin="legacy")

    result = follows.apply([FollowSpec("tournament", 8, "LaLiga", None), NBA], origin="legacy", prune=False)

    assert result.removed == () and [f.entity_id for f in result.updated] == [8]
    assert follows.leagues() == {17: "Premier League", 8: "LaLiga", 132: "NBA"}


def test_apply_can_swap_and_reuse_tournament_names(follows: FollowStore):
    follows.apply([PL, LALIGA, NBA], origin="legacy")
    ids = {f.entity_id: f.id for f in follows.list()}

    # 17 ile 8 ad değiştirir; 132 gider ve adı yeni bir kimliğe geçer
    result = follows.apply([
        FollowSpec("tournament", 17, "LaLiga"), FollowSpec("tournament", 8, "Premier League"),
        FollowSpec("tournament", 7, "NBA"),
    ], origin="legacy")

    assert result.conflicts == ()
    assert follows.leagues() == {17: "LaLiga", 8: "Premier League", 7: "NBA"}
    assert {f.entity_id: f.id for f in follows.list() if f.entity_id in (17, 8)} == {17: ids[17], 8: ids[8]}


def test_apply_keeps_the_later_of_two_duplicates(follows: FollowStore):
    """leagues.txt'nin kuralı: aynı kimlik ya da aynı ad iki satırda geçerse son satır geçerlidir."""
    result = follows.apply([
        FollowSpec("tournament", 17, "Old Name"),
        FollowSpec("tournament", 1, "Shared"),
        FollowSpec("tournament", 17, "New Name"),
        FollowSpec("tournament", 2, "Shared"),
    ], origin="legacy")

    assert result.conflicts == (
        FollowConflict("tournament", 17, "Old Name", "duplicate_id"),
        FollowConflict("tournament", 1, "Shared", "duplicate_name"),
    )
    assert _rows(follows) == [
        ("tournament", 17, "New Name", None, "legacy", 2),
        ("tournament", 2, "Shared", None, "legacy", 3),
    ]
    assert follows.apply([
        FollowSpec("tournament", 17, "Old Name"), FollowSpec("tournament", 1, "Shared"),
        FollowSpec("tournament", 17, "New Name"), FollowSpec("tournament", 2, "Shared"),
    ], origin="legacy").changed is False


def test_a_stronger_origin_takes_a_row_over_and_a_weaker_one_does_not(follows: FollowStore):
    follows.apply([PL, LALIGA], origin="legacy")
    legacy_row = follows.get("tournament", 17)

    taken = follows.apply([FollowSpec("tournament", 17, "premier-league", "football", seasons="last:2")],
                          origin="config")

    assert taken.conflicts == () and taken.added == ()
    row = follows.get("tournament", 17)
    assert (row.id, row.created_at) == (legacy_row.id, legacy_row.created_at)
    assert (row.origin, row.name, row.seasons) == ("config", "premier-league", "last:2")

    # Lig dosyası aynı ligi istemeye devam eder: satır config'in kalır, öteki lig etkilenmez
    mirrored = follows.apply([PL, LALIGA], origin="legacy")
    assert mirrored.conflicts == (FollowConflict("tournament", 17, "Premier League", "owned_by_config"),)
    assert (mirrored.changed, mirrored.unchanged) == (False, 1)
    assert follows.get("tournament", 17).origin == "config"
    assert follows.apply([PL], origin="api").conflicts[0].reason == "owned_by_config"

    # Yapılandırma dosyasından çıkınca satır gider; sonraki ayna onu legacy olarak geri getirir
    assert [f.entity_id for f in follows.apply([], origin="config").removed] == [17]
    assert follows.get("tournament", 17) is None
    assert [f.entity_id for f in follows.apply([PL, LALIGA], origin="legacy").added] == [17]
    assert follows.get("tournament", 17).origin == "legacy"


def test_legacy_does_not_take_over_an_api_row_but_config_does(follows: FollowStore):
    created = follows.add(FollowSpec("tournament", 17, "My PL", seasons="current"))

    result = follows.apply([PL], origin="legacy")
    assert result.conflicts == (FollowConflict("tournament", 17, "Premier League", "owned_by_api"),)
    assert follows.get("tournament", 17) == created

    follows.apply([FollowSpec("tournament", 17, "pl")], origin="config")
    assert (follows.get("tournament", 17).origin, follows.get("tournament", 17).id) == ("config", created.id)


def test_a_name_held_by_a_row_that_stays_is_reported_not_raised(follows: FollowStore):
    follows.apply([FollowSpec("tournament", 9, "Premier League")], origin="config")
    follows.apply([FollowSpec("tournament", 5, "Championship")], origin="legacy")

    # 17'nin adı config satırında; 17 uygulanmaz, listedeki öteki satır uygulanır
    result = follows.apply([PL, FollowSpec("tournament", 5, "Championship")], origin="legacy")
    assert result.conflicts == (FollowConflict("tournament", 17, "Premier League", "name_taken"),)
    assert follows.leagues() == {9: "Premier League", 5: "Championship"}

    # Güçlü kaynak da adı için başka bir kaynağın satırını silmez
    result = follows.apply([FollowSpec("tournament", 9, "Premier League"), FollowSpec("tournament", 6, "Championship")],
                           origin="config")
    assert result.conflicts == (FollowConflict("tournament", 6, "Championship", "name_taken"),)
    assert follows.leagues() == {9: "Premier League", 5: "Championship"}


def test_a_rejected_request_keeps_its_row_and_that_row_keeps_its_name(follows: FollowStore):
    """
    api listesi 5'i "C" yapmak ve 5'in eski adı "A"yı 7'ye vermek istiyor. "C" config satırında olduğu için 5
    değişmez; 5 değişmeyince "A" da boşalmaz. İkisi de bildirilir, tabloya dokunulmaz.
    """
    follows.apply([FollowSpec("tournament", 5, "A")], origin="legacy")
    follows.apply([FollowSpec("tournament", 9, "C")], origin="config")
    before = follows.list()

    result = follows.apply([FollowSpec("tournament", 7, "A"), FollowSpec("tournament", 5, "C")], origin="api")

    assert {(c.entity_id, c.reason) for c in result.conflicts} == {(7, "name_taken"), (5, "name_taken")}
    assert result.changed is False and follows.list() == before


def test_a_rejected_request_of_the_same_origin_loses_its_old_row(follows: FollowStore):
    """Ayna, uygulanamayan bir satırı eski haliyle bırakmaz: kaynağın satırları = liste eksi uygulanamayanlar."""
    follows.apply([FollowSpec("tournament", 5, "A")], origin="legacy")
    follows.apply([FollowSpec("tournament", 9, "C")], origin="config")

    result = follows.apply([FollowSpec("tournament", 5, "C")], origin="legacy")

    assert result.conflicts == (FollowConflict("tournament", 5, "C", "name_taken"),)
    assert [f.entity_id for f in result.removed] == [5]
    assert follows.leagues() == {9: "C"}


def test_apply_accepts_the_follow_spec_of_the_config_package(follows: FollowStore):
    """Yapılandırma paketi aynı alanlarla kendi türünü tanımlar; Store alanlara bakar, sınıfa değil."""
    spec = ConfigFollowSpec(kind="tournament", entity_id=17, name="premier-league", sport="football",
                            seasons=(61627,), slices=MappingProxyType({"include": ["core", "odds"]}), live=True)

    follows.apply([spec], origin="config")

    row = follows.get("tournament", 17)
    assert (row.name, row.sport, row.seasons, row.slices, row.live, row.enabled, row.origin) == (
        "premier-league", "football", (61627,), {"include": ["core", "odds"]}, True, True, "config",
    )


def test_two_stores_of_one_directory_see_the_same_table(data_dir: Path, follows: FollowStore):
    follows.apply([PL], origin="legacy")
    code = (
        "import sys\n"
        "from sofascore_scraper.store import FollowSpec, open_store\n"
        "store = open_store(sys.argv[1])\n"
        "assert store.follows.leagues() == {17: 'Premier League'}, store.follows.leagues()\n"
        "store.follows.apply([FollowSpec('tournament', 17, 'Premier League', 'football'),\n"
        "                     FollowSpec('tournament', 8, 'LaLiga')], origin='legacy')\n"
        "store.close()\n"
    )
    env = {**os.environ, "PYTHONPATH": ROOT}

    done = subprocess.run([sys.executable, "-c", code, str(data_dir)], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120, check=False)

    assert done.returncode == 0, done.stderr
    assert follows.leagues() == {17: "Premier League", 8: "LaLiga"}


def test_module_constants_match_the_schema(store: Store):
    """Tür ve kaynak adları geçiş dosyasındaki CHECK kısıtlarıyla aynı olmalı."""
    ddl = store._state.connection().execute(
        "SELECT sql FROM sqlite_master WHERE name = 'follows'").fetchone()[0]
    for value in (*follows_mod.KINDS, *follows_mod.ORIGINS):
        assert f"'{value}'" in ddl
    assert set(follows_mod._RANK) == set(follows_mod.ORIGINS)


def test_concurrent_applies_leave_one_of_the_requested_states(follows: FollowStore):
    """Aynı kaynağı aynı anda eşitleyen iş parçacıkları (web istekleri) birbirini bozmaz: son yazan kazanır."""
    first = [PL, LALIGA, NBA]
    second = [FollowSpec("tournament", 8, "Premier League"), FollowSpec("tournament", 17, "LaLiga"),
              FollowSpec("tournament", 7, "NBA")]
    errors: List[BaseException] = []
    start = threading.Barrier(6)

    def work(index: int) -> None:
        try:
            start.wait(timeout=30)
            for turn in range(25):
                follows.apply(first if (index + turn) % 2 else second, origin="legacy")
        except BaseException as e:  # noqa: BLE001 - iş parçacığındaki her hata testi düşürmeli
            errors.append(e)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert errors == []
    assert follows.leagues() in (
        {17: "Premier League", 8: "LaLiga", 132: "NBA"}, {8: "Premier League", 17: "LaLiga", 7: "NBA"},
    )


# === apply_follows: deposu açık olmayan çağıranlar ===================================================


def test_apply_follows_touches_nothing_when_the_directory_has_no_state_db(tmp_path: Path, data_dir: Path):
    assert apply_follows(data_dir, [PL], origin="legacy") is None
    assert not data_dir.exists()

    data_dir.mkdir()
    (data_dir / "matches").mkdir()
    assert apply_follows(data_dir, [PL], origin="legacy") is None
    assert sorted(p.name for p in data_dir.iterdir()) == ["matches"]


def test_apply_follows_calls_a_list_function_only_when_there_is_a_table(data_dir: Path):
    """Listeyi dosyalardan kuran çağıran, depo yokken o dosyaları okumaz."""
    calls: List[int] = []

    def desired() -> List[FollowSpec]:
        calls.append(1)
        return [PL]

    assert apply_follows(data_dir, desired, origin="legacy") is None
    assert calls == []

    open_store(data_dir).close()
    result = apply_follows(data_dir, desired, origin="legacy")
    assert result is not None and [f.entity_id for f in result.added] == [17] and calls == [1]


def test_apply_follows_with_create_sets_up_only_the_state_db(data_dir: Path):
    result = apply_follows(data_dir, [PL], origin="config", create=True)

    assert result is not None and [f.entity_id for f in result.added] == [17]
    meta = sorted(p.name for p in (data_dir / ".meta").iterdir())
    assert "state.db" in meta and "schema.json" not in meta and "catalog.db" not in meta
    assert sorted(p.name for p in data_dir.iterdir()) == [".meta"]
    assert api_mod._registry == {}  # geride açık bir depo kalmaz

    # Sonradan açılan depo aynı satırları görür ve iş geçmişi içe aktarımı yine yapılır
    store = open_store(data_dir)
    assert store.follows.leagues() == {17: "Premier League"}
    assert store._state.meta_get("imported_jobs_db") is not None


def test_apply_follows_updates_an_existing_store_and_leaves_none_open(data_dir: Path):
    open_store(data_dir).close()
    assert api_mod._registry == {}

    first = apply_follows(data_dir, [PL, LALIGA], origin="legacy")
    again = apply_follows(data_dir, [PL, LALIGA], origin="legacy")
    pruned = apply_follows(data_dir, [LALIGA], origin="legacy")
    kept = apply_follows(data_dir, [], origin="legacy", prune=False)

    assert first is not None and again is not None and pruned is not None and kept is not None
    assert (len(first.added), again.changed, [f.entity_id for f in pruned.removed], kept.changed) == (
        2, False, [17], False,
    )
    assert api_mod._registry == {}
    assert open_store(data_dir).follows.leagues() == {8: "LaLiga"}


def test_apply_follows_reports_an_unusable_state_db_as_store_error(data_dir: Path):
    meta = data_dir / ".meta"
    meta.mkdir(parents=True)
    _state_path(data_dir).write_bytes(b"this is not a database, " * 64)

    with pytest.raises(StoreError):
        apply_follows(data_dir, [PL], origin="legacy")


def test_apply_follows_refuses_a_newer_state_db(data_dir: Path):
    open_store(data_dir).close()
    conn = sqlite3.connect(str(_state_path(data_dir)))
    conn.execute("PRAGMA user_version = 9999")
    conn.commit()
    conn.close()

    with pytest.raises(SchemaTooNew):
        apply_follows(data_dir, [PL], origin="legacy")


# === ConfigManager ve league_sports: dosyalar doğruluk kaynağı, tablo ayna ===========================


class Setup:
    """Bir kurulum: geçici config dizini, geçici veri dizini ve o dosyaya bakan ConfigManager."""

    def __init__(self, root: Path) -> None:
        self.config_dir = root / "config"
        self.data_dir = root / "data"
        self.leagues_file = self.config_dir / "leagues.txt"
        self.sports_file = self.config_dir / "league_sports.json"
        self.config_dir.mkdir(parents=True)

    def write_leagues(self, text: str) -> None:
        """Dosyayı elle düzenler gibi yazar; mtime'ın değiştiği kesin olsun diye ileri alınır."""
        previous = self.leagues_file.stat().st_mtime_ns if self.leagues_file.exists() else 0
        self.leagues_file.write_text(text, encoding="utf-8", newline="\n")
        if self.leagues_file.stat().st_mtime_ns <= previous:
            later = previous + 2_000_000_000  # zaman damgası kaba olan dosya sistemlerinde de farklı
            os.utime(self.leagues_file, ns=(later, later))

    def write_sports(self, sports: Dict[int, str]) -> None:
        self.sports_file.write_text(json.dumps({str(k): v for k, v in sports.items()}), encoding="utf-8")

    def manager(self) -> ConfigManager:
        ConfigManager._instance = None
        return ConfigManager(str(self.leagues_file))

    def make_store(self) -> None:
        """Veri dizinini bir depoya çevirir (web sunucusunun iş deposu ya da bir CLI kilidi bunu yapar)."""
        open_store(self.data_dir).close()

    def table(self) -> List[Tuple[str, int, str, Optional[str], str, int]]:
        return _rows(open_store(self.data_dir).follows)

    def files(self) -> Dict[str, bytes]:
        """Config dizinindeki dosyalar (yazma kilidinin boş `.lock` dosyaları dışında)."""
        return {p.name: p.read_bytes() for p in sorted(self.config_dir.iterdir()) if p.suffix != ".lock"}


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Setup]:
    """ConfigManager tekilini kenara alır; DATA_DIR bu testin veri dizinidir."""
    saved = ConfigManager._instance
    box = Setup(tmp_path)
    monkeypatch.setenv("SOFASCORE_STORAGE__DATA_DIR", str(box.data_dir))
    yield box
    ConfigManager._instance = saved


def test_the_two_legacy_files_round_trip_through_the_mirror(setup: Setup):
    setup.write_leagues("# c\nPremier League: 17\nSerie A: Italy: 23\n132 NBA\nLaLiga: 8\n")
    setup.write_sports({17: "football", 132: "basketball", 999: "football", 8: "curling"})
    setup.make_store()
    before = setup.files()

    cm = setup.manager()

    assert setup.table() == [
        ("tournament", 17, "Premier League", "football", "legacy", 0),
        ("tournament", 23, "Serie A: Italy", None, "legacy", 1),
        ("tournament", 132, "NBA", "basketball", "legacy", 2),
        ("tournament", 8, "LaLiga", None, "legacy", 3),
    ]
    follows = open_store(setup.data_dir).follows
    # Tablodan okunan, dosyalardan okunanın aynısıdır (sırası dahil)
    assert follows.leagues() == cm.get_leagues() and list(follows.leagues()) == list(cm.get_leagues())
    stored_sports = {f.entity_id: f.sport for f in follows.list() if f.sport}
    assert stored_sports == {k: v for k, v in league_sports.load(str(setup.leagues_file)).items() if k in cm.leagues}
    assert all((f.seasons, f.slices, f.live, f.enabled) == ("all", None, False, True) for f in follows.list())
    assert setup.files() == before  # dosyalar yalnızca okundu


def test_constructing_a_config_manager_does_not_turn_the_data_directory_into_a_store(setup: Setup):
    setup.write_leagues("Premier League: 17\n")
    setup.data_dir.mkdir()

    cm = setup.manager()
    assert cm.add_league("LaLiga", 8) is True
    assert cm.mirror_follows() is None

    assert list(setup.data_dir.iterdir()) == []
    assert api_mod._registry == {}


def test_without_a_store_the_sport_sidecar_is_not_read(setup: Setup, monkeypatch: pytest.MonkeyPatch):
    """Depo yokken ConfigManager bugünkü kadar dosya okur: league_sports.json'a ayna için bakılmaz."""
    setup.write_leagues("Premier League: 17\n")
    setup.sports_file.write_text("{ broken", encoding="utf-8")
    reads: List[str] = []
    real_load = league_sports.load
    monkeypatch.setattr(league_sports, "load", lambda path: reads.append(path) or real_load(path))

    cm = setup.manager()
    cm.add_league("LaLiga", 8)
    assert reads == []

    setup.make_store()
    cm.mirror_follows()
    assert reads == [str(setup.leagues_file)]
    assert [row[1] for row in setup.table()] == [17, 8]  # okunamayan yan dosya: sporlar bilinmiyor


def test_the_table_fills_once_the_directory_becomes_a_store(setup: Setup):
    setup.write_leagues("Premier League: 17\n")
    cm = setup.manager()
    setup.make_store()
    assert setup.table() == []

    result = cm.mirror_follows()

    assert result is not None and [f.entity_id for f in result.added] == [17]
    assert cm.mirror_follows().changed is False


def test_add_and_remove_write_the_file_first_and_then_the_mirror(setup: Setup):
    setup.write_leagues("Premier League: 17\n")
    setup.make_store()
    cm = setup.manager()

    assert cm.add_league("LaLiga", 8) is True
    assert setup.leagues_file.read_text(encoding="utf-8") == "Premier League: 17\nLaLiga: 8\n"
    assert setup.table() == [
        ("tournament", 17, "Premier League", None, "legacy", 0),
        ("tournament", 8, "LaLiga", None, "legacy", 1),
    ]

    assert cm.remove_league(17) is True
    assert setup.leagues_file.read_text(encoding="utf-8") == "LaLiga: 8\n"
    assert setup.table() == [("tournament", 8, "LaLiga", None, "legacy", 0)]


def test_uniqueness_on_id_and_on_name_is_as_today(setup: Setup):
    setup.write_leagues("Premier League: 17\n")
    setup.make_store()
    cm = setup.manager()
    before = setup.files()

    assert cm.add_league("Another Name", 17) is False
    assert cm.add_league("Premier League", 99) is False
    assert cm.remove_league(404) is False

    assert setup.files() == before
    assert setup.table() == [("tournament", 17, "Premier League", None, "legacy", 0)]


def test_a_hand_edit_of_leagues_txt_is_picked_up_on_the_next_read(setup: Setup):
    setup.write_leagues("Premier League: 17\n")
    setup.make_store()
    cm = setup.manager()

    setup.write_leagues("# edited by hand\nLaLiga: 8\nEPL: 17\n")

    assert setup.table() == [("tournament", 17, "Premier League", None, "legacy", 0)]  # henüz kimse okumadı
    assert cm.get_leagues() == {8: "LaLiga", 17: "EPL"}
    assert setup.table() == [
        ("tournament", 8, "LaLiga", None, "legacy", 0),
        ("tournament", 17, "EPL", None, "legacy", 1),
    ]


def test_a_hand_edit_seen_by_a_refused_add_is_mirrored_too(setup: Setup):
    setup.write_leagues("Premier League: 17\n")
    setup.make_store()
    cm = setup.manager()
    setup.write_leagues("Premier League: 17\nLaLiga: 8\n")

    assert cm.add_league("LaLiga", 8) is False  # dosyada zaten var: kilit altında yeniden okununca görülür

    assert [row[1] for row in setup.table()] == [17, 8]


def test_reload_config_mirrors_again(setup: Setup):
    setup.write_leagues("Premier League: 17\n")
    setup.make_store()
    cm = setup.manager()
    open_store(setup.data_dir).follows.apply([], origin="legacy")  # tablo dosyanın gerisinde kaldı
    assert setup.table() == []

    assert cm.reload_config() is True

    assert [row[1] for row in setup.table()] == [17]


def test_the_files_are_never_rewritten_from_the_table(setup: Setup):
    setup.write_leagues("# my leagues\nPremier League: 17\n\nLaLiga: 8\n")
    setup.write_sports({17: "football"})
    setup.make_store()
    cm = setup.manager()
    before = setup.files()
    follows = open_store(setup.data_dir).follows

    # Tablo dosyalardan ayrışıyor: bir satır siliniyor, biri değişiyor, başka kaynaklardan satırlar ekleniyor
    follows.remove("tournament", 17)
    follows.update("tournament", 8, name="La Liga Santander", sport="basketball")
    follows.add(NBA)
    follows.apply([FollowSpec("tournament", 35, "bundesliga")], origin="config")

    assert cm.get_leagues() == {17: "Premier League", 8: "LaLiga"}  # dosyadan, tablodan değil
    result = cm.mirror_follows()

    assert setup.files() == before
    assert result is not None and result.changed
    assert sorted(setup.table(), key=lambda row: row[1]) == [
        ("tournament", 8, "LaLiga", None, "legacy", 1),
        ("tournament", 17, "Premier League", "football", "legacy", 0),
        ("tournament", 35, "bundesliga", None, "config", 0),
        ("tournament", 132, "NBA", "basketball", "api", 2),
    ]


def test_the_sport_sidecar_is_mirrored_after_every_write(setup: Setup):
    setup.write_leagues("Premier League: 17\nNBA: 132\n")
    setup.make_store()
    setup.manager()
    cfg = str(setup.leagues_file)

    league_sports.set_sport(cfg, 132, "basketball")
    assert [(row[1], row[3]) for row in setup.table()] == [(17, None), (132, "basketball")]
    assert json.loads(setup.sports_file.read_text(encoding="utf-8")) == {"132": "basketball"}

    league_sports.set_sport(cfg, 17, "football")
    league_sports.set_sport(cfg, 132, None)
    assert [(row[1], row[3]) for row in setup.table()] == [(17, "football"), (132, None)]

    # Sporu bilinen ama listede olmayan bir lig tabloya girmez
    league_sports.set_sport(cfg, 999, "tennis")
    assert [row[1] for row in setup.table()] == [17, 132]


def test_a_hand_edit_of_the_sidecar_reaches_the_table_with_the_next_mirror(setup: Setup):
    setup.write_leagues("Premier League: 17\n")
    setup.make_store()
    cm = setup.manager()

    setup.write_sports({17: "football"})
    assert setup.table() == [("tournament", 17, "Premier League", None, "legacy", 0)]

    cm.mirror_follows()
    assert setup.table() == [("tournament", 17, "Premier League", "football", "legacy", 0)]


def test_sidecar_writes_for_another_league_file_do_not_touch_this_mirror(setup: Setup, tmp_path: Path):
    setup.write_leagues("Premier League: 17\n")
    setup.make_store()
    setup.manager()
    other = tmp_path / "elsewhere" / "leagues.txt"
    other.parent.mkdir()

    league_sports.set_sport(str(other), 17, "tennis")
    mirror_league_follows(str(other))

    assert setup.table() == [("tournament", 17, "Premier League", None, "legacy", 0)]

    ConfigManager._instance = None  # süreçte ConfigManager yok: yapılacak bir şey de yok
    mirror_league_follows(str(setup.leagues_file))
    league_sports.set_sport(str(setup.leagues_file), 17, "football")
    assert setup.table() == [("tournament", 17, "Premier League", None, "legacy", 0)]


def test_one_name_under_two_ids_keeps_the_later_line_in_the_table(setup: Setup, caplog: pytest.LogCaptureFixture):
    """
    Elle düzenlenmiş dosyada aynı ad iki kimlikte olabilir; `get_leagues()` ikisini de verir. Tabloda turnuva
    adı tekildir: `get_league_by_name`in gösterdiği (son) satır kalır ve ayna hata vermez.
    """
    setup.write_leagues("Cup: 1\nPremier League: 17\nCup: 2\n")
    setup.make_store()
    with caplog.at_level(logging.WARNING):
        cm = setup.manager()

    assert cm.get_leagues() == {1: "Cup", 17: "Premier League", 2: "Cup"}
    assert cm.get_league_by_name("Cup") == 2
    assert setup.table() == [
        ("tournament", 17, "Premier League", None, "legacy", 1),
        ("tournament", 2, "Cup", None, "legacy", 2),
    ]
    assert not [r for r in caplog.records if "follows" in r.getMessage()]


def test_an_unusable_store_does_not_break_the_league_file(setup: Setup, caplog: pytest.LogCaptureFixture):
    setup.write_leagues("Premier League: 17\n")
    (setup.data_dir / ".meta").mkdir(parents=True)
    _state_path(setup.data_dir).write_bytes(b"this is not a database, " * 64)

    with caplog.at_level(logging.WARNING):
        cm = setup.manager()
        assert cm.add_league("LaLiga", 8) is True
        assert cm.remove_league(17) is True
        league_sports.set_sport(str(setup.leagues_file), 8, "football")

    assert cm.get_leagues() == {8: "LaLiga"}
    assert setup.leagues_file.read_text(encoding="utf-8") == "LaLiga: 8\n"
    assert league_sports.load(str(setup.leagues_file)) == {8: "football"}
    warnings = [r for r in caplog.records if "could not be mirrored into the follows table" in r.getMessage()]
    assert warnings and all(r.levelno == logging.WARNING for r in warnings)


def test_a_league_that_is_also_in_the_config_origin_stays_there(setup: Setup):
    setup.write_leagues("Premier League: 17\nLaLiga: 8\n")
    setup.make_store()
    open_store(setup.data_dir).follows.apply([FollowSpec("tournament", 17, "premier-league", seasons="last:2")],
                                             origin="config")

    cm = setup.manager()
    result = cm.mirror_follows()

    assert result is not None and [c.reason for c in result.conflicts] == ["owned_by_config"]
    assert setup.table() == [
        ("tournament", 17, "premier-league", None, "config", 0),
        ("tournament", 8, "LaLiga", None, "legacy", 1),
    ]
    assert cm.get_leagues() == {17: "Premier League", 8: "LaLiga"}


# === build_context: yapılandırma dosyasının [[follow]] girdileri =====================================

CONFIG_WITH_FOLLOWS = """
[[follow]]
name = "premier-league"
sport = "football"
tournament = 17
seasons = "last:2"
slices = ["core", "odds"]
live = true

[[follow]]
event = 17124861

[[follow]]
team = 42
name = "Arsenal"
enabled = false
"""


@pytest.fixture
def config_file(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[Optional[str]], None]]:
    """`write(toml)` yapılandırma dosyasını yazıp ayarları yeniden yükler; `write(None)` dosyayı kaldırır."""
    path = setup.config_dir / "sofascore.toml"
    loader.reset()

    def write(toml: Optional[str]) -> None:
        if toml is None:
            path.unlink()
            monkeypatch.setenv(loader.CONFIG_ENV, "none")
        else:
            path.write_text(toml, encoding="utf-8")
            monkeypatch.setenv(loader.CONFIG_ENV, str(path))
        loader.reload()

    yield write
    monkeypatch.setenv(loader.CONFIG_ENV, "none")
    loader.reset()


def test_build_context_applies_the_follows_of_the_config_file(setup: Setup, config_file):
    setup.write_leagues("LaLiga: 8\n")
    setup.write_sports({8: "football"})
    cm = setup.manager()
    config_file(CONFIG_WITH_FOLLOWS)

    build_context(cm, data_dir=str(setup.data_dir))

    assert api_mod._registry == {}  # bağlam kurulurken depo açık bırakılmaz
    meta = sorted(p.name for p in (setup.data_dir / ".meta").iterdir())
    assert "state.db" in meta and "schema.json" not in meta and "catalog.db" not in meta
    follows = open_store(setup.data_dir).follows
    assert [(f.kind, f.entity_id, f.name, f.origin, f.position) for f in follows.list()] == [
        ("tournament", 17, "premier-league", "config", 0),
        ("tournament", 8, "LaLiga", "legacy", 0),
        ("event", 17124861, "event-17124861", "config", 1),
        ("team", 42, "Arsenal", "config", 2),
    ]
    league = follows.get("tournament", 17)
    assert (league.sport, league.seasons, league.slices, league.live, league.enabled) == (
        "football", "last:2", {"include": ["core", "odds"]}, True, True,
    )
    assert follows.get("team", 42).enabled is False
    assert follows.get("tournament", 8).sport == "football"

    with pytest.raises(FollowManaged):
        follows.update("tournament", 17, enabled=False)
    with pytest.raises(FollowManaged):
        follows.remove("event", 17124861)


def test_build_context_follows_the_config_file_when_it_changes(setup: Setup, config_file):
    setup.write_leagues("Premier League: 17\n")
    cm = setup.manager()
    config_file(CONFIG_WITH_FOLLOWS)
    build_context(cm, data_dir=str(setup.data_dir))
    assert open_store(setup.data_dir).follows.get("tournament", 17).origin == "config"

    # Dosyadan iki girdi çıkar: turnuva lig dosyasında da olduğu için legacy olarak kalır
    config_file("[[follow]]\nteam = 42\nname = \"Arsenal FC\"\n")
    build_context(cm, data_dir=str(setup.data_dir))
    assert setup.table() == [
        ("team", 42, "Arsenal FC", None, "config", 0),
        ("tournament", 17, "Premier League", None, "legacy", 0),
    ]

    # Yapılandırma dosyası kaldırılır: config satırları gider, dosyalar olduğu gibi kalır
    before = {name: data for name, data in setup.files().items() if name != "sofascore.toml"}
    config_file(None)
    build_context(cm, data_dir=str(setup.data_dir))
    assert setup.table() == [("tournament", 17, "Premier League", None, "legacy", 0)]
    assert setup.files() == before


def test_build_context_is_idempotent(setup: Setup, config_file):
    setup.write_leagues("LaLiga: 8\n")
    cm = setup.manager()
    config_file(CONFIG_WITH_FOLLOWS)
    build_context(cm, data_dir=str(setup.data_dir))
    follows = open_store(setup.data_dir).follows  # açılış state.db'ye yazar: sayaçtan önce
    before = follows.list()
    version = _data_version(setup.data_dir)
    seen = version()

    build_context(cm, data_dir=str(setup.data_dir))
    build_context(cm, data_dir=str(setup.data_dir))

    assert version() == seen
    assert follows.list() == before
    version.close()


def test_build_context_without_follows_does_not_create_a_store(setup: Setup, config_file):
    """Bugünkü kurulum (yapılandırma dosyası yok) ve `[[follow]]` içermeyen dosya: `.meta/` kurulmaz."""
    setup.write_leagues("Premier League: 17\n")
    cm = setup.manager()

    build_context(cm, data_dir=str(setup.data_dir))
    config_file("[client]\nretries = 2\n")
    build_context(cm, data_dir=str(setup.data_dir))

    assert sorted(p.name for p in setup.data_dir.iterdir()) == ["datasets", "match_details"]
    assert api_mod._registry == {}


def test_build_context_mirrors_the_league_files_into_an_existing_store(setup: Setup):
    """ConfigManager kurulduğunda depo yoktu (yeni kurulum); iş başlarken tablo dolar."""
    setup.write_leagues("Premier League: 17\n")
    setup.write_sports({17: "football"})
    cm = setup.manager()
    setup.make_store()

    build_context(cm, data_dir=str(setup.data_dir))

    assert setup.table() == [("tournament", 17, "Premier League", "football", "legacy", 0)]


def test_build_context_reports_a_follow_it_cannot_apply_once(setup: Setup, config_file,
                                                             caplog: pytest.LogCaptureFixture,
                                                             monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("sofascore_scraper.services.context._reported_conflicts", set())
    setup.write_leagues("Premier League: 17\n")
    setup.make_store()
    cm = setup.manager()
    config_file("[[follow]]\ntournament = 99\nname = \"Premier League\"\n")

    with caplog.at_level(logging.WARNING):
        build_context(cm, data_dir=str(setup.data_dir))
        build_context(cm, data_dir=str(setup.data_dir))

    reports = [r.getMessage() for r in caplog.records if "not applied" in r.getMessage()]
    assert reports == ["Follow of the config file not applied: tournament 99 (Premier League): name_taken"]
    assert setup.table() == [("tournament", 17, "Premier League", None, "legacy", 0)]


def test_build_context_survives_an_unusable_state_db(setup: Setup, config_file, caplog: pytest.LogCaptureFixture):
    setup.write_leagues("Premier League: 17\n")
    cm = setup.manager()
    config_file(CONFIG_WITH_FOLLOWS)
    (setup.data_dir / ".meta").mkdir(parents=True)
    _state_path(setup.data_dir).write_bytes(b"this is not a database, " * 64)

    with caplog.at_level(logging.WARNING):
        ctx = build_context(cm, data_dir=str(setup.data_dir))

    assert ctx.data_dir == str(setup.data_dir)
    assert any("Follows of the config file could not be applied" in r.getMessage() for r in caplog.records)
