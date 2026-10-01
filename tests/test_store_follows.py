"""
src/store/follows.py: `follows` tablosu (docs/design/01-storage.md bölüm 2.3; plan maddesi ST-17).

  * FollowStore: ekleme, değiştirme, silme, `apply` (yinelenebilir; yalnızca kendi kaynağını budar),
    kaynaklar arası öncelik ve ad tekliği.
  * `apply_follows`: deposu açık olmayan çağıranların yolu; state.db yoksa hiçbir şeye dokunmaz.

Tümü çevrimdışı ve geçici dizinlerde.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Iterator, List, Optional, Tuple

import pytest

from src.config import FollowSpec as ConfigFollowSpec
from src.store import (
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
from src.store import api as api_mod
from src.store import follows as follows_mod

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
        "from src.store import FollowSpec, open_store\n"
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

