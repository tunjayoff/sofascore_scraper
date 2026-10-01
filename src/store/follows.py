"""
Takipler: state.db'deki `follows` tablosu (docs/design/01-storage.md, bölüm 2.3 "Follows"; plan maddesi ST-17).

Bir takip, uygulamanın izlediği bir varlıktır: turnuva (bugünkü "lig"), takım, oyuncu ya da tek bir maç.
Her satırın bir kaynağı (origin) vardır ve satır o kaynağa aittir:

  legacy   `config/leagues.txt` ve `config/league_sports.json`. Doğruluk kaynağı dosyalardır; tablo onların
           aynasıdır. Dosyalar tablodan asla yeniden yazılmaz: önce dosya yazılır (ConfigManager'ın bugünkü
           yolu), sonra `apply(..., origin="legacy")` çağrılır.
  config   yapılandırma dosyasının (sofascore.toml) `[[follow]]` girdileri. `update` ve `remove` bu satırlar
           için FollowManaged atar; satırlar yalnızca `apply(..., origin="config")` ile değişir.
  api      state.db'nin kendisi (`add`, `update`, `remove`).

`apply`, bir kaynağın satırlarını verilen listeye eşitler ve yalnızca o kaynağın satırlarını siler. İki kaynak
aynı varlığı isterse satır daha güçlü kaynağındır (config > api > legacy): güçlü kaynak satırı devralır,
zayıf kaynağın isteği uygulanmaz ve `ApplyResult.conflicts` içinde bildirilir. Bugünkü dosyanın iki
teklik kuralı tabloda da geçerlidir: varlık başına bir satır ve turnuva adı başına bir satır. Adı başka bir
satırın tuttuğu bir istek de (hangi kaynaktan olursa olsun) uygulanmaz ve bildirilir; `apply` bu yüzden
hata atmaz, `add` ve `update` ise FollowExists atar.

`apply_follows`, deposu açık olmayan çağıranlar içindir (ConfigManager, build_context): tabloyu kısa ömürlü
bir bağlantıyla günceller ve geride açık bir depo bırakmaz. Veri dizininde henüz state.db yoksa hiçbir şeye
dokunmaz; ayna, dizini bir depoya çevirmez.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union, cast

from src.store import layout
from src.store.errors import FollowExists, FollowManaged, StoreError
from src.store.lease import LeaseManager
from src.store.sqlite import to_store_error
from src.store.state import StateDb

if TYPE_CHECKING:
    from src.store.api import Store

PathLike = Union[str, "os.PathLike[str]"]

TOURNAMENT = "tournament"
KINDS: Tuple[str, ...] = (TOURNAMENT, "team", "player", "event")

ORIGIN_LEGACY = "legacy"
ORIGIN_CONFIG = "config"
ORIGIN_API = "api"
ORIGINS: Tuple[str, ...] = (ORIGIN_LEGACY, ORIGIN_CONFIG, ORIGIN_API)
# Aynı varlığı iki kaynak isterse satır kimin: büyük olan devralır, küçük olanın isteği uygulanmaz
_RANK: Mapping[str, int] = {ORIGIN_LEGACY: 0, ORIGIN_API: 1, ORIGIN_CONFIG: 2}

# `ApplyResult.conflicts` içindeki nedenler
CONFLICT_DUPLICATE_ID = "duplicate_id"  # aynı varlık listede bir kez daha var (sonraki geçerli)
CONFLICT_DUPLICATE_NAME = "duplicate_name"  # aynı turnuva adı listede bir kez daha var (sonraki geçerli)
CONFLICT_NAME_TAKEN = "name_taken"  # turnuva adını tabloda kalan başka bir satır tutuyor
CONFLICT_OWNED_BY = "owned_by_"  # + kaynak: varlığın satırı daha güçlü bir kaynağın

Seasons = Union[str, Sequence[int]]
_SEASON_WORDS = frozenset({"all", "current"})
_LAST_N = re.compile(r"last:[1-9][0-9]*")
_UPDATABLE: Tuple[str, ...] = ("name", "sport", "seasons", "slices", "live", "enabled")
_COLUMNS = ("id, kind, entity_id, sport, name, seasons, slices_json, live, enabled, origin, position, "
            "created_at, updated_at")
_SELECT = f"SELECT {_COLUMNS} FROM follows"

Key = Tuple[str, int]


@dataclass(frozen=True)
class FollowSpec:
    """
    İstenen bir takip. `seasons`: "all", "current", "last:N" ya da sezon kimlikleri. `slices`: None =
    varsayılan dilim seçimi; değilse JSON'a çevrilebilen bir seçim. Yapılandırma paketi aynı alanlarla
    kendi türünü tanımlar (src/config/settings.py); Store o paketi içe aktaramaz.
    """

    kind: str
    entity_id: int
    name: str
    sport: Optional[str] = None
    seasons: Seasons = "all"
    slices: Optional[Mapping[str, Any]] = None
    live: bool = False
    enabled: bool = True


@dataclass(frozen=True)
class Follow:
    """`follows` tablosunun bir satırı. Zamanlar epoch saniyedir (UTC)."""

    id: int
    kind: str
    entity_id: int
    name: str
    sport: Optional[str]
    seasons: Union[str, Tuple[int, ...]]
    slices: Optional[Mapping[str, Any]]
    live: bool
    enabled: bool
    origin: str
    position: int
    created_at: int
    updated_at: int

    def spec(self) -> FollowSpec:
        """Satırın istek hali (ör. yapılandırma metnine dökmek ya da değiştirip yeniden uygulamak için)."""
        return FollowSpec(kind=self.kind, entity_id=self.entity_id, name=self.name, sport=self.sport,
                          seasons=self.seasons, slices=self.slices, live=self.live, enabled=self.enabled)


@dataclass(frozen=True)
class FollowConflict:
    """Uygulanmayan bir istek ve nedeni (CONFLICT_* değerleri)."""

    kind: str
    entity_id: int
    name: str
    reason: str


@dataclass(frozen=True)
class ApplyResult:
    """`apply` sonucu. `unchanged`: istenen haliyle zaten tabloda duran satır sayısı."""

    origin: str
    added: Tuple[Follow, ...] = ()
    updated: Tuple[Follow, ...] = ()
    removed: Tuple[Follow, ...] = ()
    unchanged: int = 0
    conflicts: Tuple[FollowConflict, ...] = ()

    @property
    def changed(self) -> bool:
        """Tabloya bir şey yazıldı mı."""
        return bool(self.added or self.updated or self.removed)


# --- istek ve satır dönüşümleri -----------------------------------------------------------------------


@dataclass(frozen=True)
class _Wanted:
    """Doğrulanmış ve sütun değerlerine çevrilmiş bir istek."""

    kind: str
    entity_id: int
    name: str
    sport: Optional[str]
    seasons: str
    slices_json: Optional[str]
    live: int
    enabled: int

    @property
    def key(self) -> Key:
        return (self.kind, self.entity_id)

    def conflict(self, reason: str) -> FollowConflict:
        return FollowConflict(self.kind, self.entity_id, self.name, reason)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _seasons_text(value: Any) -> str:
    if isinstance(value, str):
        if value in _SEASON_WORDS or _LAST_N.fullmatch(value):
            return value
        raise ValueError(f"seasons: expected 'all', 'current', 'last:N' or season ids, got {value!r}")
    if isinstance(value, (list, tuple)) and all(_is_int(item) for item in value):
        return json.dumps(list(value), separators=(",", ":"))
    raise ValueError(f"seasons: expected 'all', 'current', 'last:N' or season ids, got {value!r}")


def _plain(value: Any) -> Any:
    """Salt okunur eşlemeleri ve demetleri JSON'un yazabildiği türlere çevirir."""
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_plain(item) for item in value]
        return sorted(items, key=repr) if isinstance(value, (set, frozenset)) else items
    return value


def _slices_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"slices: expected a mapping or None, got {value!r}")
    try:
        return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as e:
        raise ValueError(f"slices: not JSON serialisable ({e})") from e


def _wanted(spec: Any) -> _Wanted:
    """İsteği doğrular. Aynı alanları taşıyan her nesne kabul edilir (ör. src.config'in FollowSpec'i)."""
    kind = spec.kind
    if kind not in KINDS:
        raise ValueError(f"kind: expected one of {', '.join(KINDS)}, got {kind!r}")
    if not _is_int(spec.entity_id):
        raise ValueError(f"entity_id: expected an integer, got {spec.entity_id!r}")
    if not isinstance(spec.name, str) or not spec.name.strip():
        raise ValueError(f"name: expected a non-empty string, got {spec.name!r}")
    if spec.sport is not None and (not isinstance(spec.sport, str) or not spec.sport):
        raise ValueError(f"sport: expected a sport slug or None, got {spec.sport!r}")
    return _Wanted(
        kind=kind,
        entity_id=spec.entity_id,
        name=spec.name,
        sport=spec.sport,
        seasons=_seasons_text(spec.seasons),
        slices_json=_slices_text(spec.slices),
        live=int(bool(spec.live)),
        enabled=int(bool(spec.enabled)),
    )


def _check_origin(origin: str) -> str:
    if origin not in ORIGINS:
        raise ValueError(f"origin: expected one of {', '.join(ORIGINS)}, got {origin!r}")
    return origin


def _row_key(row: sqlite3.Row) -> Key:
    return (str(row["kind"]), int(row["entity_id"]))


def _follow(row: sqlite3.Row) -> Follow:
    seasons_text = str(row["seasons"])
    seasons: Union[str, Tuple[int, ...]] = seasons_text
    if seasons_text.startswith("["):
        try:
            seasons = tuple(int(item) for item in json.loads(seasons_text))
        except (TypeError, ValueError):
            seasons = seasons_text  # elle bozulmuş değer olduğu gibi görünür; sonraki yazım düzeltir
    slices: Optional[Mapping[str, Any]] = None
    if row["slices_json"] is not None:
        try:
            loaded = json.loads(row["slices_json"])
        except ValueError:
            loaded = None
        slices = loaded if isinstance(loaded, dict) else None
    return Follow(
        id=int(row["id"]),
        kind=str(row["kind"]),
        entity_id=int(row["entity_id"]),
        name=str(row["name"]),
        sport=row["sport"],
        seasons=seasons,
        slices=slices,
        live=bool(row["live"]),
        enabled=bool(row["enabled"]),
        origin=str(row["origin"]),
        position=int(row["position"]),
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )


def _same(row: sqlite3.Row, wanted: _Wanted, origin: str, position: Optional[int]) -> bool:
    return (
        row["name"] == wanted.name and row["sport"] == wanted.sport and row["seasons"] == wanted.seasons
        and row["slices_json"] == wanted.slices_json and int(row["live"]) == wanted.live
        and int(row["enabled"]) == wanted.enabled and row["origin"] == origin
        and (position is None or int(row["position"]) == position)
    )


# --- apply planı --------------------------------------------------------------------------------------


@dataclass
class _Plan:
    """Bir `apply` çağrısının tabloya yapacakları; satırlar okunarak hesaplanır, sonra yazılır."""

    deletes: List[sqlite3.Row]
    updates: List[Tuple[sqlite3.Row, _Wanted, int]]  # (eski satır, istek, konum)
    inserts: List[Tuple[_Wanted, int]]
    unchanged: int
    conflicts: List[FollowConflict]

    @property
    def writes(self) -> bool:
        return bool(self.deletes or self.updates or self.inserts)


def _deduplicate(desired: Sequence[_Wanted]) -> Tuple[List[Tuple[int, _Wanted]], List[FollowConflict]]:
    """
    Listede yinelenen varlık ya da turnuva adı varsa sonraki geçerlidir (leagues.txt'nin kuralı: aynı kimlik
    ya da aynı ad iki satırda geçerse son satır kazanır). Dönen çiftlerin ilki listedeki sıra, yani konumdur.
    """
    kept: List[Tuple[int, _Wanted]] = []
    conflicts: List[FollowConflict] = []
    keys: Set[Key] = set()
    names: Set[str] = set()
    for index in range(len(desired) - 1, -1, -1):
        wanted = desired[index]
        if wanted.key in keys:
            conflicts.append(wanted.conflict(CONFLICT_DUPLICATE_ID))
            continue
        if wanted.kind == TOURNAMENT and wanted.name in names:
            conflicts.append(wanted.conflict(CONFLICT_DUPLICATE_NAME))
            continue
        keys.add(wanted.key)
        if wanted.kind == TOURNAMENT:
            names.add(wanted.name)
        kept.append((index, wanted))
    kept.reverse()
    conflicts.reverse()
    return kept, conflicts


def _plan(rows: Sequence[sqlite3.Row], desired: Sequence[_Wanted], origin: str, prune: bool) -> _Plan:
    kept, conflicts = _deduplicate(desired)
    by_key: Dict[Key, sqlite3.Row] = {_row_key(row): row for row in rows}
    rejected: Dict[Key, str] = {}
    for _position, wanted in kept:
        current = by_key.get(wanted.key)
        if current is not None and current["origin"] != origin and _RANK[current["origin"]] > _RANK[origin]:
            rejected[wanted.key] = CONFLICT_OWNED_BY + str(current["origin"])
    # Turnuva adları: bu çağrının yeniden yazmadığı ve silmediği her satır adını tutmaya devam eder. Bir istek
    # reddedilince onun satırı da yerinde kalabilir (ve adını tutar); bu yüzden değişmeyene kadar yinelenir.
    while True:
        accepted_keys = {wanted.key for _p, wanted in kept if wanted.key not in rejected}
        taken = {
            row["name"] for row in rows
            if row["kind"] == TOURNAMENT and _row_key(row) not in accepted_keys
            and not (prune and row["origin"] == origin)
        }
        clashes = [wanted for _p, wanted in kept
                   if wanted.key in accepted_keys and wanted.kind == TOURNAMENT and wanted.name in taken]
        if not clashes:
            break
        for wanted in clashes:
            rejected[wanted.key] = CONFLICT_NAME_TAKEN

    plan = _Plan(deletes=[], updates=[], inserts=[], unchanged=0, conflicts=conflicts)
    for position, wanted in kept:
        reason = rejected.get(wanted.key)
        if reason is not None:
            plan.conflicts.append(wanted.conflict(reason))
            continue
        current = by_key.get(wanted.key)
        if current is None:
            plan.inserts.append((wanted, position))
        elif _same(current, wanted, origin, position):
            plan.unchanged += 1
        else:
            plan.updates.append((current, wanted, position))
    if prune:
        plan.deletes = [row for row in rows if row["origin"] == origin and _row_key(row) not in accepted_keys]
    return plan


# --- depo ---------------------------------------------------------------------------------------------


class FollowStore:
    """`follows` tablosu. Açık bir depoda `store.follows` olarak durur. Bütün yöntemler iş parçacığı güvenlidir."""

    def __init__(self, store: Store) -> None:
        self._state: StateDb = store._state

    # --- okuma ------------------------------------------------------------------------------

    def list(self, *, kind: Optional[str] = None, enabled: Optional[bool] = None,
             origin: Optional[str] = None) -> List[Follow]:
        """Takipler, konum sırasıyla (bir kaynağın satırları kendi listesindeki sırayı korur)."""
        where: List[str] = []
        params: List[Any] = []
        if kind is not None:
            where.append("kind = ?")
            params.append(kind)
        if enabled is not None:
            where.append("enabled = ?")
            params.append(int(bool(enabled)))
        if origin is not None:
            where.append("origin = ?")
            params.append(origin)
        sql = _SELECT + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY position, id"
        return [_follow(row) for row in self._state.connection().execute(sql, params).fetchall()]

    def get(self, kind: str, entity_id: int) -> Optional[Follow]:
        row = self._row(self._state.connection(), kind, entity_id)
        return None if row is None else _follow(row)

    def leagues(self) -> Dict[int, str]:
        """
        Turnuva takipleri, `ConfigManager.get_leagues()` biçiminde: {turnuva kimliği: ad}, konum sırasıyla.
        Bütün kaynaklar ve kapalı (enabled=False) takipler dahildir: ad, diskteki dizin adlarını çözmek içindir.
        """
        rows = self._state.connection().execute(
            "SELECT entity_id, name FROM follows WHERE kind = ? ORDER BY position, id", (TOURNAMENT,)
        ).fetchall()
        return {int(row["entity_id"]): str(row["name"]) for row in rows}

    # --- tek satır yazımı -------------------------------------------------------------------

    def add(self, spec: FollowSpec, *, origin: str = "api") -> Follow:
        """Yeni bir takip ekler (listenin sonuna). Varlık ya da turnuva adı zaten takipteyse FollowExists."""
        _check_origin(origin)
        wanted = _wanted(spec)
        now = int(time.time())
        with self._state.write() as conn:
            self._require_free(conn, wanted, ignore_id=None)
            position = int(conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM follows").fetchone()[0])
            self._insert(conn, wanted, origin, position, now)
            row = self._row(conn, wanted.kind, wanted.entity_id)
        assert row is not None
        return _follow(row)

    def update(self, kind: str, entity_id: int, **changes: Any) -> Follow:
        """
        Bir takibin alanlarını değiştirir: name, sport, seasons, slices, live, enabled. Satır yoksa KeyError,
        yapılandırma dosyasından geliyorsa FollowManaged, yeni turnuva adı başka bir satırdaysa FollowExists.
        """
        unknown = sorted(set(changes) - set(_UPDATABLE))
        if unknown:
            raise TypeError(f"update() got an unexpected field {unknown[0]!r}; allowed: {', '.join(_UPDATABLE)}")
        with self._state.write() as conn:
            row = self._row(conn, kind, entity_id)
            if row is None:
                raise KeyError((kind, entity_id))
            self._require_unmanaged(row)
            wanted = _wanted(replace(_follow(row).spec(), **changes))
            if not _same(row, wanted, str(row["origin"]), None):
                self._require_free(conn, wanted, ignore_id=int(row["id"]))
                self._update(conn, int(row["id"]), wanted, str(row["origin"]), int(row["position"]), int(time.time()))
                row = self._row(conn, kind, entity_id)
        assert row is not None
        return _follow(row)

    def remove(self, kind: str, entity_id: int) -> bool:
        """Takibi siler; satır yoksa False. Yapılandırma dosyasından gelen satır için FollowManaged."""
        with self._state.write() as conn:
            row = self._row(conn, kind, entity_id)
            if row is None:
                return False
            self._require_unmanaged(row)
            conn.execute("DELETE FROM follows WHERE id = ?", (int(row["id"]),))
        return True

    # --- bir kaynağı eşitleme ---------------------------------------------------------------

    def apply(self, desired: Sequence[FollowSpec], *, origin: str, prune: bool = True) -> ApplyResult:
        """
        `origin` kaynağının takiplerini `desired` listesine eşitler: eksikler eklenir, farklı olanlar
        güncellenir, listede olmayanlar silinir (prune=False ise kalır). Başka kaynakların satırları
        silinmez; daha zayıf bir kaynağın aynı varlık için tuttuğu satır devralınır. Uygulanamayan istekler
        hata atmaz, `conflicts` içinde döner. Yinelenebilir: tablo zaten istenen haldeyse hiçbir şey yazılmaz
        (yazma kilidi de alınmaz) ve `changed` False döner.
        """
        _check_origin(origin)
        wanted = [_wanted(spec) for spec in desired]
        plan = _plan(self._rows(self._state.connection()), wanted, origin, prune)
        if not plan.writes:
            return ApplyResult(origin=origin, unchanged=plan.unchanged, conflicts=tuple(plan.conflicts))
        with self._state.write() as conn:
            plan = _plan(self._rows(conn), wanted, origin, prune)  # kilit altında yeniden: arada değişmiş olabilir
            now = int(time.time())
            for row in plan.deletes:
                conn.execute("DELETE FROM follows WHERE id = ?", (int(row["id"]),))
            # İki satır ad değiştiriyorsa (A<->B) sırayla yazmak ad tekliğine takılır: önce adlar boşaltılır
            for row, new, _position in plan.updates:
                if row["kind"] == TOURNAMENT and row["name"] != new.name:
                    conn.execute("UPDATE follows SET name = ? WHERE id = ?",
                                 (f"~renaming~{uuid.uuid4().hex}~{int(row['id'])}", int(row["id"])))
            for row, new, position in plan.updates:
                self._update(conn, int(row["id"]), new, origin, position, now)
            for new, position in plan.inserts:
                self._insert(conn, new, origin, position, now)
            after = {_row_key(row): _follow(row) for row in self._rows(conn)}
        return ApplyResult(
            origin=origin,
            added=tuple(after[new.key] for new, _position in plan.inserts),
            updated=tuple(after[new.key] for _row, new, _position in plan.updates),
            removed=tuple(_follow(row) for row in plan.deletes),
            unchanged=plan.unchanged,
            conflicts=tuple(plan.conflicts),
        )

    # --- yardımcılar ------------------------------------------------------------------------

    @staticmethod
    def _rows(conn: sqlite3.Connection) -> List[sqlite3.Row]:
        return list(conn.execute(_SELECT + " ORDER BY position, id").fetchall())

    @staticmethod
    def _row(conn: sqlite3.Connection, kind: str, entity_id: int) -> Optional[sqlite3.Row]:
        row: Optional[sqlite3.Row] = conn.execute(
            _SELECT + " WHERE kind = ? AND entity_id = ?", (kind, entity_id)
        ).fetchone()
        return row

    @staticmethod
    def _require_unmanaged(row: sqlite3.Row) -> None:
        if row["origin"] == ORIGIN_CONFIG:
            raise FollowManaged(
                f"{FollowManaged.default_message}: {row['kind']} {row['entity_id']} ({row['name']})"
            )

    @staticmethod
    def _require_free(conn: sqlite3.Connection, wanted: _Wanted, *, ignore_id: Optional[int]) -> None:
        """Varlık ya da turnuva adı (ignore_id dışındaki) bir satırdaysa FollowExists."""
        holder = conn.execute(
            "SELECT id FROM follows WHERE kind = ? AND entity_id = ?", (wanted.kind, wanted.entity_id)
        ).fetchone()
        if holder is not None and int(holder["id"]) != ignore_id:
            raise FollowExists(f"{FollowExists.default_message}: {wanted.kind} {wanted.entity_id}")
        if wanted.kind == TOURNAMENT:
            holder = conn.execute(
                "SELECT id, entity_id FROM follows WHERE kind = ? AND name = ?", (TOURNAMENT, wanted.name)
            ).fetchone()
            if holder is not None and int(holder["id"]) != ignore_id:
                raise FollowExists(
                    f"{FollowExists.default_message}: {wanted.name!r} adı {TOURNAMENT} {holder['entity_id']} "
                    "için kullanılıyor"
                )

    @staticmethod
    def _insert(conn: sqlite3.Connection, wanted: _Wanted, origin: str, position: int, now: int) -> None:
        conn.execute(
            "INSERT INTO follows (kind, entity_id, sport, name, seasons, slices_json, live, enabled, origin, "
            "position, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (wanted.kind, wanted.entity_id, wanted.sport, wanted.name, wanted.seasons, wanted.slices_json,
             wanted.live, wanted.enabled, origin, position, now, now),
        )

    @staticmethod
    def _update(conn: sqlite3.Connection, row_id: int, wanted: _Wanted, origin: str, position: int, now: int) -> None:
        conn.execute(
            "UPDATE follows SET sport = ?, name = ?, seasons = ?, slices_json = ?, live = ?, enabled = ?, "
            "origin = ?, position = ?, updated_at = ? WHERE id = ?",
            (wanted.sport, wanted.name, wanted.seasons, wanted.slices_json, wanted.live, wanted.enabled,
             origin, position, now, row_id),
        )


# --- deposu açık olmayan çağıranlar -------------------------------------------------------------------


class _StateOnly:
    """`FollowStore`un bir depodan kullandığı tek şey: state.db. `apply_follows` cepheyi açmadan bunu verir."""

    def __init__(self, state: StateDb) -> None:
        self._state = state


def apply_follows(data_dir: PathLike, desired: Union[Sequence[FollowSpec], Callable[[], Sequence[FollowSpec]]], *,
                  origin: str, prune: bool = True, create: bool = False) -> Optional[ApplyResult]:
    """
    `desired` listesini veri dizininin `follows` tablosuna uygular (`FollowStore.apply`) ve bağlantıyı kapatır.

    Dizinde state.db yoksa: create=False iken hiçbir şeye dokunulmaz ve None döner (dizin henüz bir depo
    değil; tablo, depoyu ilk açan süreçten sonraki ilk çağrıda dolar); create=True iken `.meta/state.db`
    kurulur. Var olan dosya eski şemadaysa geçişleri, her açılışta olduğu gibi `maintenance` kilidi altında
    uygulanır. Hatalar StoreError olarak çıkar (kilit zaman aşımı StoreBusy, daha yeni şema SchemaTooNew).

    `desired` listeyi döndüren bir işlev de olabilir; yalnızca yazılacak bir tablo varsa çağrılır. Listeyi
    kurmak için dosya okuyan çağıran (lig dosyalarının aynası) böylece depo yokken hiçbir şey okumaz.
    """
    _check_origin(origin)
    root = os.path.abspath(os.fspath(data_dir))
    state_path = layout.resolve(root, layout.STATE_DB)
    if not os.path.isfile(state_path):
        if not create:
            return None
        try:
            os.makedirs(os.path.dirname(state_path), exist_ok=True)
        except OSError as e:
            raise StoreError.from_exception(e, os.path.dirname(state_path)) from e
    specs = desired() if callable(desired) else desired
    try:
        state = StateDb(state_path, migration_guard=LeaseManager.for_data_dir(root).migration_guard)
    except sqlite3.Error as e:
        raise to_store_error(e, state_path) from e
    try:
        return FollowStore(cast("Store", _StateOnly(state))).apply(specs, origin=origin, prune=prune)
    except sqlite3.Error as e:
        raise to_store_error(e, state_path) from e
    finally:
        state.close()


__all__ = [
    "CONFLICT_DUPLICATE_ID",
    "CONFLICT_DUPLICATE_NAME",
    "CONFLICT_NAME_TAKEN",
    "CONFLICT_OWNED_BY",
    "KINDS",
    "ORIGINS",
    "ORIGIN_API",
    "ORIGIN_CONFIG",
    "ORIGIN_LEGACY",
    "TOURNAMENT",
    "ApplyResult",
    "Follow",
    "FollowConflict",
    "FollowSpec",
    "FollowStore",
    "apply_follows",
]
