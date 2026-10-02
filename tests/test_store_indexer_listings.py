"""
Listelerin, sezon listelerinin ve değişiklik günlüğünün dizinlenmesi: src/store/entities.py,
src/store/changes.py ve bunları sıraya koyan src/store/indexer.py (plan maddesi ST-08;
docs/design/01-storage.md bölüm 3.4, 5.2, 8.2 ve 8.5).

Altın dosyalar tests/golden/catalog_listings/<dizin>.json, `tests/store_fixtures.py`'nin kurduğu her veri
dizininin **tamamı** için yeniden kurmanın yazdığı satırları tutar: liste satırları (olay yükü olmayan
maçlar) bütün sütunlarıyla, olay yükü olan maçların liste sütunları, program ve sezon listesi dilimleri,
sezonlar, turnuvalar, değişiklik günlüğü ve imzası tutulan kökler. Maç dizinlerinden gelen satırlar
tests/golden/catalog/ altındadır (tests/test_store_indexer.py). Çıktı bilerek değiştiyse:

    REGEN_CATALOG_GOLDEN=1 python -m pytest tests/test_store_indexer_listings.py

Ağ yok. Uzlaştırma (reconcile) tests/test_store_reconcile.py'dedir; oradaki testler buradaki yardımcıları
kullanır.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

import pytest

import store_fixtures as sf
from src import refresh
from src.store import catalog as catalog_mod
from src.store import changes, codec, derive, entities, files, indexer, layout, manifest
from src.store.catalog import Catalog
from src.store.indexer import CatalogAdmin
from src.store.legacy import LegacyReader
from src.store.manifest import Manifest, Observation, SliceEntry

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = Path(__file__).parent / "golden" / "catalog_listings"
REGEN = os.getenv("REGEN_CATALOG_GOLDEN") == "1"
UTC = dt.timezone.utc
NOW = sf.FIXTURE_NOW
BASE = sf.BASE_MTIME

PL_SEASON = "matches/17_Premier_League/96668_Premier_League_26_27"
PL_DETAILS = "match_details/17_Premier_League/season_Premier_League_26_27"
ARS = sf.event_id(sf.PL_ARS)
LIV = sf.event_id(sf.PL_LIV)
LEE = sf.event_id(sf.PL_LEE)

# Kataloğun, yeniden kurmanın dosyalardan türettiği bütün tabloları ve sıraları
TABLES: Dict[str, str] = {
    "events": "id",
    "event_slices": "event_id, key, sub",
    "event_participants": "event_id, side",
    "entity_slices": "kind, entity_id, key, sub",
    "participants": "id",
    "tournaments": "id",
    "seasons": "id",
    "sports": "slug",
    "categories": "id",
    "changes": "seq",
    "legacy_roots": "path",
    "pending_writes": "kind, entity_id",
}
# Satırı silinmeyen, "yalnızca yoksa eklenen" varlık tabloları: uzlaştırma ile yeniden kurma arasında
# `updated_at` (ve artık hiçbir dosyada geçmeyen satırlar) farklı olabilir
ENTITY_TABLES = ("participants", "tournaments", "seasons", "sports", "categories")


# --- yardımcılar --------------------------------------------------------------------------------------

@pytest.fixture
def make_admin() -> Iterator[Callable[..., CatalogAdmin]]:
    opened: List[CatalogAdmin] = []

    def make(data_dir: Path, **kwargs: Any) -> CatalogAdmin:
        admin = CatalogAdmin(data_dir, clock=lambda: float(NOW), **kwargs)
        opened.append(admin)
        return admin

    yield make
    for admin in opened:
        admin.close()


@pytest.fixture(params=sf.FIXTURE_NAMES)
def fx(request: pytest.FixtureRequest, tmp_path: Path) -> sf.LegacyFixture:
    return sf.build_fixture(request.param, tmp_path / "data")


@pytest.fixture
def canonical(tmp_path: Path) -> sf.LegacyFixture:
    return sf.build_fixture("canonical", tmp_path / "data")


@pytest.fixture
def old_forms(tmp_path: Path) -> sf.LegacyFixture:
    return sf.build_fixture("legacy", tmp_path / "data")


def rows(cat: Catalog, table: str, where: str = "", order: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = f'SELECT * FROM "{table}" {where} ORDER BY {order or TABLES.get(table, "1")}'
    return [dict(row) for row in cat.connection().execute(sql)]


def event_row(cat: Catalog, event_id: int) -> Optional[Dict[str, Any]]:
    found = rows(cat, "events", f"WHERE id = {int(event_id)}")
    return found[0] if found else None


def listing_rows(cat: Catalog) -> Dict[int, Dict[str, Any]]:
    return {row["id"]: row for row in rows(cat, "events", "WHERE has_event_payload = 0 AND layout IS NULL")}


def schedule_slices(cat: Catalog, season_id: int) -> Dict[str, Dict[str, Any]]:
    found = rows(cat, "entity_slices", f"WHERE kind = 'season' AND entity_id = {int(season_id)}")
    return {row["sub"]: row for row in found}


def snapshot(cat: Catalog, *, skip: Sequence[str] = ()) -> Dict[str, List[Dict[str, Any]]]:
    """Dosyalardan türeyen bütün satırlar (meta tablosu kurulumun kendisini anlatır, burada yoktur)."""
    return {table: rows(cat, table) for table in TABLES if table not in skip}


def facts(shot: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    """Görüntünün varlık tabloları dışındaki bölümü: uzlaştırma ile yeniden kurma burada satır satır eşittir."""
    return {table: found for table, found in shot.items() if table not in ENTITY_TABLES}


def undated(shot: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    """Görüntünün tamamı, varlık tablolarının `updated_at` sütunu olmadan (satırı ilk yazan kaynağın zamanı)."""
    return {table: [{k: v for k, v in row.items() if k != "updated_at"} for row in found]
            if table in ENTITY_TABLES else found for table, found in shot.items()}


def rebuilt(data_dir: Path, tmp_path: Path, **kwargs: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Aynı ağacın ayrı bir katalog dosyasına sıfırdan kurulmuş hali (karşılaştırma için)."""
    path = tmp_path / f"fresh-{len(list(tmp_path.glob('fresh-*')))}" / "catalog.db"
    with Catalog(path) as other:
        admin = CatalogAdmin(data_dir, other, clock=lambda: float(NOW), **kwargs)
        assert admin.rebuild().completed
        return snapshot(other)


_bumped: Dict[str, int] = {}


def bump(path: Path, seconds: int = 5) -> None:
    """
    mtime'ı ileri alır: iki değişiklik aynı saat tikine düşüp imzayı aynı bırakmasın. Yeni değer, bu yola daha
    önce verilmiş her değerden de büyüktür (işletim sistemi, dizine her yazmada mtime'ı kaba saatine geri çeker).
    """
    st = path.stat()
    stamp = max(st.st_mtime_ns, _bumped.get(str(path), 0)) + seconds * 1_000_000_000
    os.utime(path, ns=(st.st_atime_ns, stamp))
    _bumped[str(path)] = stamp


def write(root: Path, rel: str, data: Any, mtime: int = BASE) -> Path:
    """Eski düzen dosyası yazar (bayt ise aynen, değilse fabrikanın JSON biçimiyle) ve dizinin imzasını değiştirir."""
    path = root.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else sf.dump_json(data))
    os.utime(path, (mtime, mtime))
    bump(path.parent)
    return path


def remove(root: Path, rel: str) -> None:
    path = root.joinpath(*rel.split("/"))
    if path.is_dir():
        files.remove_tree(path)
    else:
        path.unlink()
    if path.parent.exists():
        bump(path.parent)


def page(root: Path, rel: str, events: Sequence[Dict[str, Any]], mtime: int = BASE, **extra: Any) -> Path:
    """Bir tur dosyası ya da olay sayfası: {"events": [...], "hasNextPage": false, ...}."""
    return write(root, rel, {"events": list(events), "hasNextPage": False, **extra}, mtime)


def read_json(root: Path, rel: str) -> Any:
    return json.loads(root.joinpath(*rel.split("/")).read_text(encoding="utf-8"))


def detail(root: Path, ev: sf.Ev, base: Optional[str] = None, *, slices: Tuple[str, ...] = (), mtime: int = BASE,
           observed: Optional[str] = None, **edits: Any) -> str:
    """Küçük bir eski düzen maç dizini (L1); dizinin yolunu döndürür."""
    event = sf.basic_payload(ev)
    event.update(edits)
    base = base or f"{PL_DETAILS}/{event['id']}"
    write(root, f"{base}/basic.json", event, mtime)
    for key in slices:
        write(root, f"{base}/{key}.json", sf.slice_payload(key, event), mtime)
    if observed is not None:
        write(root, f"{base}/observation.json", sf.observation_payload(event, observed), mtime)
    return base


def write_v3(data_dir: Path, payload: Dict[str, Any], *, at: dt.datetime,
             observed: Optional[dt.datetime] = None) -> str:
    """Yalnızca olay yükü olan bir v3 maç dizini (henüz v3 yazıcısı yok: test kendisi kurar)."""
    event_id = int(payload["id"])
    rel = layout.event_dir(event_id)
    found = Manifest(kind="event", id=event_id, created_at=at, updated_at=at,
                     observation=Observation(observed_at=observed) if observed is not None else None)
    encoded = codec.write_payload(layout.resolve(data_dir, layout.slice_path(rel, "event")), payload)
    found.slices["event"] = SliceEntry(state="ok", fetched_at=at, checked_at=at, stored_bytes=encoded.stored_bytes,
                                       raw_bytes=encoded.raw_bytes, sha256=encoded.sha256)
    manifest.write_manifest(layout.resolve(data_dir, layout.manifest_path(rel)), found)
    return rel


def listed(ev: sf.Ev, **edits: Any) -> Dict[str, Any]:
    """Liste yanıtındaki olay nesnesi; `edits` üst düzey alanları değiştirir."""
    event = sf.event_payload(ev)
    event.update(edits)
    return event


def utc(stamp: int) -> str:
    return dt.datetime.fromtimestamp(stamp, UTC).isoformat()


def problem_kinds(report: Any) -> List[Tuple[str, str]]:
    return sorted((p.path, p.kind) for p in report.problems)


# --- altın satırlar: her fixture dizininin tamamı -------------------------------------------------------

def _compact(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in row.items() if v is not None}


def _golden_of(admin: CatalogAdmin, report: indexer.RebuildReport) -> Dict[str, Any]:
    cat = admin.catalog
    tables: Dict[str, List[Dict[str, Any]]] = {
        "listing_events": [_compact(row) for row in listing_rows(cat).values()],
        "listed_event_rows": [
            {"id": row["id"], "listed_in": row["listed_in"], "stale": row["stale"]}
            for row in rows(cat, "events", "WHERE has_event_payload = 1 AND listed_in IS NOT NULL")],
        "entity_slices": [_compact(row) for row in rows(cat, "entity_slices")],
        "seasons": [_compact(row) for row in rows(cat, "seasons")],
        "tournaments": [_compact(row) for row in rows(cat, "tournaments")],
        "changes": [_compact(row) for row in rows(cat, "changes")],
        # imza dizinin mtime'ına bağlıdır: altın dosyaya yalnızca hangi köklerin tutulduğu girer
        "legacy_roots": [{"path": row["path"], "kind": row["kind"]} for row in rows(cat, "legacy_roots")],
    }
    return {
        "derive_version": derive.DERIVE_VERSION,
        "report": {
            "counts": {"events": report.events, "listed": report.listed, "schedules": report.schedules,
                       "season_lists": report.season_lists, "changes": report.changes},
            # sorunun ayrıntısı (JSON hata metni) Python sürümüne göre değişir: altın dosyaya girmez
            "problems": [[p.layout, p.path, p.kind] for p in report.problems],
            "superseded_files": [[s.kind, s.key, s.path, s.winner] for s in report.superseded_files],
        },
        "tables": tables,
    }


def _write_golden(path: Path, data: Dict[str, Any]) -> None:
    def block(items: List[Any], indent: str) -> str:
        if not items:
            return "[]"
        lines = ",\n".join(f"{indent} {json.dumps(item, ensure_ascii=False)}" for item in items)
        return f"[\n{lines}\n{indent}]"

    report = data["report"]
    tables = ",\n".join(f'  {json.dumps(name)}: {block(found, "  ")}' for name, found in data["tables"].items())
    text = (
        "{\n"
        f' "derive_version": {data["derive_version"]},\n'
        f' "report": {{\n  "counts": {json.dumps(report["counts"])},\n'
        f'  "problems": {block(report["problems"], "  ")},\n'
        f'  "superseded_files": {block(report["superseded_files"], "  ")}\n }},\n'
        f' "tables": {{\n{tables}\n }}\n'
        "}\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def test_golden_listing_rows(fx: sf.LegacyFixture, make_admin) -> None:
    admin = make_admin(fx.data_dir, league_names=fx.leagues)
    report = admin.rebuild()
    produced = json.loads(json.dumps(_golden_of(admin, report)))
    path = GOLDEN_DIR / f"{fx.name}.json"
    if REGEN:
        _write_golden(path, produced)
    golden = json.loads(path.read_text(encoding="utf-8"))

    assert golden["derive_version"] == derive.DERIVE_VERSION, "türetme sürümü değişti: altın dosyayı yeniden üretin"
    assert produced["report"] == golden["report"]
    for table in produced["tables"]:
        assert produced["tables"][table] == golden["tables"][table], table
    assert report.completed and report.counts["events"] == report.events + report.listed


def test_goldens_cover_every_fixture_directory() -> None:
    assert sorted(p.stem for p in GOLDEN_DIR.glob("*.json")) == sorted(sf.FIXTURE_NAMES)


# --- liste satırları (bölüm 8.2, kural 1 ve 2) ----------------------------------------------------------

def test_listing_rows_are_the_derived_rows_of_the_newest_page(fx: sf.LegacyFixture, make_admin) -> None:
    """Olay yükü olmayan her listelenmiş maç: derive.event_row(liste öğesi, "listing") + dizinleyicinin sütunları."""
    admin = make_admin(fx.data_dir, league_names=fx.leagues)
    admin.rebuild()
    reader = LegacyReader(fx.data_dir)
    newest: Dict[int, Tuple[Any, Dict[str, Any]]] = {}
    for found in reader.schedule_pages():
        if found.superseded_by is None:
            for item in reader.read_schedule(found).events:
                newest[item["id"]] = (found, item)
    with_payload = {e.event_id for e in reader.iter_events(payloads=False)}

    produced = listing_rows(admin.catalog)
    from_pages = {i for i in newest if i not in with_payload}
    assert from_pages <= set(produced)
    for event_id in from_pages:
        found, item = newest[event_id]
        row = produced[event_id]
        assert {name: row[name] for name in derive.EVENT_DERIVED_COLUMNS} == derive.event_row(item, "listing")
        assert (row["row_source"], row["has_event_payload"], row["layout"], row["path"], row["legacy_path"],
                row["sig"], row["stale"], row["status_regressed"]) == ("listing", 0, None, None, None, None, 0, 0)
        assert (row["listed_in"], row["tournament_id"], row["season_id"]) == (
            found.sub, found.tournament_id, found.season_id)
        assert row["first_seen_at"] == row["updated_at"] == found.mtime_ns // 10 ** 9
    # sayfada geçmeyen liste satırları yalnızca özet CSV'sinden gelebilir (hiç sayfası olmayan sezon)
    assert {(produced[i]["tournament_id"], produced[i]["season_id"]) for i in set(produced) - from_pages} <= {
        (17, 76986)}
    # her liste satırının iki yarışmacı bağı vardır; olay dilimi yoktur
    links = rows(admin.catalog, "event_participants")
    for event_id, row in produced.items():
        mine = [(r["participant_id"], r["side"]) for r in links if r["event_id"] == event_id]
        assert mine == [(row[k], side) for side, k in ((1, "home_id"), (2, "away_id")) if row[k] is not None]
    assert not rows(admin.catalog, "event_slices",
                    "WHERE event_id IN (SELECT id FROM events WHERE has_event_payload = 0)")


def test_a_listing_never_changes_a_row_that_has_an_event_payload(canonical: sf.LegacyFixture, make_admin,
                                                                 tmp_path: Path) -> None:
    """Kural 1: olay yükü olan maçın satırı her zaman o yükten türer; liste yalnızca listed_in ve stale yazar."""
    admin = make_admin(canonical.data_dir, league_names=canonical.leagues)
    admin.rebuild()
    full = {row["id"]: row for row in rows(admin.catalog, "events", "WHERE has_event_payload = 1")}

    # aynı ağacın listeleri olmayan hali: maç satırları, liste sütunları dışında, birebir aynı
    bare = sf.build_fixture("canonical", tmp_path / "bare")
    for name in ("matches", "seasons"):
        files.remove_tree(bare.data_dir / name)
    plain = make_admin(bare.data_dir)
    plain.rebuild()
    alone = {row["id"]: row for row in rows(plain.catalog, "events")}
    assert set(alone) == set(full) == set(canonical.detail_ids)
    for event_id, row in full.items():
        other = alone[event_id]
        assert (other["listed_in"], other["stale"]) == (None, 0)
        assert {k: v for k, v in row.items() if k not in ("listed_in", "stale", "sig")} == {
            k: v for k, v in other.items() if k not in ("listed_in", "stale", "sig")}
    for table in ("event_slices",):
        assert rows(admin.catalog, table) == rows(plain.catalog, table)
    payload_links = [r for r in rows(admin.catalog, "event_participants") if r["event_id"] in full]
    assert payload_links == rows(plain.catalog, "event_participants")

    # olay yükleriyle aynı şeyi söyleyen listeler: sayfası olan her maç listed_in alır, stale yalnızca
    # liste yükten yeniyken ve farklıyken (sonradan iptal edilen maç: yük "Abandoned", liste "AET")
    assert all(row["listed_in"] is not None for row in full.values())
    assert {i for i, row in full.items() if row["stale"]} == {sf.event_id(sf.NBA_VOID)}
    void = full[sf.event_id(sf.NBA_VOID)]
    assert (void["status_description"], void["status_class"], void["listed_in"]) == ("Abandoned", "void", "last_1")


def test_the_newest_listing_wins(tmp_path: Path, make_admin) -> None:
    """Kural 2: liste satırı, maçı listeleyen en yeni sayfadan gelir (mtime, eşitlikte yol)."""
    data = tmp_path / "data"
    lee_old = listed(sf.PL_LEE, homeScore={"current": 0, "display": 0})
    lee_new = listed(sf.PL_LEE, homeScore={"current": 4, "display": 4})
    liv_a = listed(sf.PL_LIV, winnerCode=1)
    liv_b = listed(sf.PL_LIV, winnerCode=2)
    page(data, f"{PL_SEASON}/round_1.json", [lee_new, liv_a], BASE, _complete=True)
    page(data, f"{PL_SEASON}/events_last_0.json", [lee_old], BASE - 100)
    page(data, f"{PL_SEASON}/round_7.json", [liv_b], BASE, _complete=False)  # eşit mtime: yolu büyük olan
    # aynı sayfanın iki dizindeki kopyası: en yenisi geçerli, eskisi hiç okunmaz
    other_dir = "matches/17_Premier_League/96668_Season_96668"
    page(data, f"{other_dir}/round_9.json", [listed(sf.PL_BRE, winnerCode=1)], BASE - 50, _complete=True)
    page(data, f"{PL_SEASON}/round_9.json", [listed(sf.PL_BRE, winnerCode=3)], BASE - 60, _complete=True)
    admin = make_admin(data)

    report = admin.rebuild()

    found = listing_rows(admin.catalog)
    assert set(found) == {LEE, LIV, sf.event_id(sf.PL_BRE)}
    lee = found[LEE]
    assert (lee["home_score"], lee["listed_in"], lee["first_seen_at"], lee["updated_at"]) == (
        4, "round_1", BASE - 100, BASE)
    assert (found[LIV]["winner_code"], found[LIV]["listed_in"]) == (2, "round_7")
    bre = found[sf.event_id(sf.PL_BRE)]
    assert (bre["winner_code"], bre["listed_in"], bre["updated_at"]) == (1, "round_9", BASE - 50)
    assert [(s.kind, s.key, s.path, s.winner) for s in report.superseded_files] == [
        ("schedule", "17/96668/round_9", f"{PL_SEASON}/round_9.json", f"{other_dir}/round_9.json")]
    assert schedule_slices(admin.catalog, 96668)["round_9"]["path"] == f"{other_dir}/round_9.json"
    assert (report.listed, report.schedules, report.events) == (3, 4, 0)


def test_listed_event_gets_its_row_from_the_event_payload_once_it_has_one(tmp_path: Path, make_admin) -> None:
    data = tmp_path / "data"
    page(data, f"{PL_SEASON}/round_1.json", [listed(sf.PL_ARS), listed(sf.PL_LIV, startTimestamp=1)], BASE,
         _complete=True)
    detail(data, sf.PL_LIV, slices=("statistics",), mtime=BASE + 10)
    admin = make_admin(data)
    admin.rebuild()

    liv, ars = event_row(admin.catalog, LIV), event_row(admin.catalog, ARS)
    assert (ars["row_source"], ars["has_event_payload"], ars["layout"]) == ("listing", 0, None)
    payload = sf.basic_payload(sf.PL_LIV)
    assert {n: liv[n] for n in derive.EVENT_DERIVED_COLUMNS} == derive.event_row(payload, "event")
    assert (liv["layout"], liv["listed_in"], liv["stale"], liv["start_ts"]) == (
        "legacy", "round_1", 0, payload["startTimestamp"])  # liste daha eski: farklı olsa da stale değil
    assert (liv["first_seen_at"], liv["updated_at"]) == (BASE + 10, BASE + 10)
    # liste satırından kalan yarışmacı bağı (başka bir başlangıç zamanıyla) silinmiş olmalı
    links = rows(admin.catalog, "event_participants", f"WHERE event_id = {LIV}")
    assert [(r["start_ts"], r["side"]) for r in links] == [(payload["startTimestamp"], 1), (payload["startTimestamp"], 2)]


# --- stale (bölüm 8.2, kural 3) -------------------------------------------------------------------------

def _score(side: str, **changes: Any) -> Callable[[Dict[str, Any]], None]:
    def edit(event: Dict[str, Any]) -> None:
        event[side] = {**event[side], **changes}
    return edit


def _status(**changes: Any) -> Callable[[Dict[str, Any]], None]:
    def edit(event: Dict[str, Any]) -> None:
        event["status"] = {**event["status"], **changes}
    return edit


def _set(**changes: Any) -> Callable[[Dict[str, Any]], None]:
    return lambda event: event.update(changes)


def _drop(side: str, name: str) -> Callable[[Dict[str, Any]], None]:
    def edit(event: Dict[str, Any]) -> None:
        event[side] = {k: v for k, v in event[side].items() if k != name}
    return edit


# (liste öğesindeki değişiklik, `diff_basic` fark görür mü)
STALE_CASES: Dict[str, Tuple[Callable[[Dict[str, Any]], None], bool]] = {
    "same": (lambda event: None, False),
    "status_code": (_status(code=110), True),
    "status_description": (_status(description="AET"), True),
    "status_type": (_status(type="inprogress"), True),
    "winner_code": (_set(winnerCode=2), True),
    "start_time": (_set(startTimestamp=sf.basic_payload(sf.PL_ARS)["startTimestamp"] + 900), True),
    "score_current": (_score("homeScore", current=4), True),
    "score_display": (_score("awayScore", display=2), True),
    # katalog sütunlarına hiç girmeyen skor alt alanları da karşılaştırılır
    "score_period2": (_score("homeScore", period2=1), True),
    "score_new_field": (_score("awayScore", overtime=0), True),
    "score_missing_field": (_drop("homeScore", "normaltime"), True),
    "score_new_field_null": (_score("awayScore", overtime=None), False),  # eksik alan ile None eşittir
    "score_same_number": (_score("homeScore", current=3.0), False),  # 3 == 3.0
    # diff_basic'in bakmadığı alanlar
    "round": (_set(roundInfo={"round": 9}), False),
    "slug": (_set(slug="another-slug"), False),
    "team_name": (_set(homeTeam={"id": 42, "name": "Renamed"}), False),
    "change_timestamp": (_set(changes={"changeTimestamp": 5}), False),
}


def _stale_tree(data: Path, edit: Callable[[Dict[str, Any]], None], *, page_at: int, basic_at: int = BASE,
                observed: Optional[str] = None) -> Dict[str, Any]:
    item = listed(sf.PL_ARS)
    edit(item)
    page(data, f"{PL_SEASON}/round_1.json", [item], page_at, _complete=True)
    detail(data, sf.PL_ARS, mtime=basic_at, observed=observed)
    return item


@pytest.mark.parametrize("case", sorted(STALE_CASES))
def test_stale_is_set_exactly_when_a_newer_listing_differs_in_a_diff_basic_field(tmp_path: Path, make_admin,
                                                                                 case: str) -> None:
    edit, differs = STALE_CASES[case]
    data = tmp_path / "data"
    item = _stale_tree(data, edit, page_at=BASE + 60)
    assert bool(refresh.diff_basic(sf.basic_payload(sf.PL_ARS), item)) is differs  # durumun kendisi doğru kurulmuş
    admin = make_admin(data)
    admin.rebuild()

    row = event_row(admin.catalog, ARS)
    assert (row["stale"], row["listed_in"], row["row_source"]) == (int(differs), "round_1", "event")
    # satır olay yükünden türer: listedeki değer satıra geçmez
    assert {n: row[n] for n in derive.EVENT_DERIVED_COLUMNS} == derive.event_row(sf.basic_payload(sf.PL_ARS), "event")
    assert admin.verify(deep=True).ok


@pytest.mark.parametrize("page_at, basic_at, observed, stale", [
    (BASE + 60, BASE, None, 1),  # liste, olay yükünün dosyasından yeni
    (BASE, BASE, None, 0),  # aynı saniye: "daha yeni" değil
    (BASE - 60, BASE, None, 0),  # liste daha eski
    (BASE + 60, BASE, utc(BASE + 30), 1),  # gözlem anı listeden eski
    (BASE + 60, BASE, utc(BASE + 60), 0),  # gözlem listeyle aynı anda
    (BASE + 60, BASE, utc(BASE + 3600), 0),  # yük listeden sonra yeniden gözlendi (dosyası değişmeden)
    (BASE - 60, BASE, utc(BASE - 3600), 1),  # yükün zamanı gözlem anıdır, dosyanın mtime'ı değil
])
def test_stale_needs_a_listing_newer_than_the_event_payload(tmp_path: Path, make_admin, page_at: int, basic_at: int,
                                                            observed: Optional[str], stale: int) -> None:
    data = tmp_path / "data"
    _stale_tree(data, _set(winnerCode=2), page_at=page_at, basic_at=basic_at, observed=observed)
    admin = make_admin(data)
    admin.rebuild()
    assert event_row(admin.catalog, ARS)["stale"] == stale


def test_stale_for_a_v3_event(tmp_path: Path, make_admin) -> None:
    data = tmp_path / "data"
    at = dt.datetime.fromtimestamp(BASE, UTC)
    page(data, f"{PL_SEASON}/round_1.json", [listed(sf.PL_ARS, winnerCode=2), listed(sf.PL_LIV, winnerCode=2)],
         BASE + 60, _complete=True)
    write_v3(data, sf.basic_payload(sf.PL_ARS), at=at)  # yükün zamanı: olay diliminin fetched_at'i
    write_v3(data, sf.basic_payload(sf.PL_LIV), at=at, observed=at + dt.timedelta(hours=1))
    admin = make_admin(data)
    admin.rebuild()

    assert [(event_row(admin.catalog, i)["layout"], event_row(admin.catalog, i)["stale"]) for i in (ARS, LIV)] == [
        ("v3", 1), ("v3", 0)]
    assert admin.verify(deep=True).ok


def test_compare_digest_agrees_with_diff_basic_on_every_status_fixture() -> None:
    """Store `src.refresh`'i içe aktaramaz; karşılaştırma kuralının aynı olduğunu bu test tutar."""
    payloads = []
    for path in sorted(sf.STATUS_DIR.rglob("*.json")):
        body = json.loads(path.read_text(encoding="utf-8"))
        payloads.append({k: v for k, v in body.items() if k not in sf._META_KEYS})
    assert len(payloads) > 100
    digests = [entities.compare_digest(p) for p in payloads]
    equal = 0
    for (a, da), (b, db) in itertools.combinations(zip(payloads, digests, strict=True), 2):
        same = not refresh.diff_basic(a, b)
        assert (da == db) is same
        equal += same
    assert equal > 0  # eşit çiftler de var (aynı maçın iki ayrı durum dosyası)
    for payload in payloads:
        assert set(entities.diff_fields(payload)) == {k for k, v in refresh._flatten(payload).items() if v is not None}

    # beklenmeyen biçimler düşürmez; None olan ve hiç olmayan alan aynıdır
    assert entities.compare_digest(None) == entities.compare_digest({}) == entities.compare_digest(
        {"status": None, "homeScore": [], "winnerCode": None})
    assert entities.compare_digest({"homeScore": {"current": True}}) == entities.compare_digest(
        {"homeScore": {"current": 1.0}})
    assert entities.compare_digest({"homeScore": {"x": [1, {"a": 2.0}]}}) == entities.compare_digest(
        {"homeScore": {"x": (1, {"a": 2})}})
    assert entities.compare_digest({"winnerCode": "1"}) != entities.compare_digest({"winnerCode": 1})
    assert entities.is_stale(5, b"a", 4, b"b") and not entities.is_stale(4, b"a", 4, b"b")
    assert not entities.is_stale(5, b"a", 4, b"a") and not entities.is_stale(5, None, 4, b"b")
    assert not entities.is_stale(5, b"a", None, b"b") and not entities.is_stale(None, b"a", 4, b"b")


# --- özet CSV'leri ---------------------------------------------------------------------------------------

def _csv_rows(path: Path) -> List[Dict[str, str]]:
    import csv
    import io

    return list(csv.DictReader(io.StringIO(path.read_bytes().decode("utf-8"), newline="")))


def test_catalog_rows_equal_the_season_summary_csv(fx: sf.LegacyFixture, make_admin) -> None:
    """
    Sezon özetinin her satırı (bitmiş maçlar; FETCH_ONLY_FINISHED=false ile yazılmış sezonda hepsi) için
    katalog satırı aynı kimliği, takım adlarını, durum metnini ve `current` skorunu söyler. Tek istisna,
    listesi olay yükünden farklı olan maçtır: satırı yükten türer ve `stale = 1` taşır.
    """
    admin = make_admin(fx.data_dir, league_names=fx.leagues)
    admin.rebuild()
    compared, stale = 0, set()
    for rel in fx.summary_files:
        if "/round_" in rel:
            continue  # ilk sürümün tur dosyaları: sütunları farklı, ayrı testte
        for line in _csv_rows(fx.data_dir / rel):
            row = event_row(admin.catalog, int(line["match_id"]))
            assert row is not None, line["match_id"]
            if row["stale"]:
                stale.add(row["id"])
                continue
            assert (row["home_name"], row["away_name"], row["status_description"]) == (
                line["home_team"], line["away_team"], line["status"])
            # özet, `current` yoksa 0 yazar (src/match_fetcher.py); katalog yok olanı NULL tutar
            assert (row["home_score_current"] or 0, row["away_score_current"] or 0) == (
                int(line["home_score"]), int(line["away_score"]))
            assert dt.datetime.fromtimestamp(row["start_ts"]).isoformat() == line["match_date"]
            compared += 1
    if fx.name == "canonical":
        assert stale == {sf.event_id(sf.NBA_VOID)} and compared == 31
        pen = event_row(admin.catalog, sf.event_id(sf.CUP_PEN))  # penaltılar: current 10, normalleştirilmiş 3
        assert (pen["home_score"], pen["home_score_current"]) == (3, 10)
    elif fx.name == "legacy":
        assert not stale and compared == 12  # iki özet dosyasında geçen maçlar iki kez karşılaştırılır
    else:
        assert compared == 0


def test_season_without_page_files_is_indexed_from_its_summary_csv(old_forms: sf.LegacyFixture, make_admin) -> None:
    """Hiç tur / sayfa dosyası olmayan sezon (eski `_matches.csv`): satırlar CSV'den kurulur."""
    admin = make_admin(old_forms.data_dir, league_names=old_forms.leagues)
    report = admin.rebuild()
    cat = admin.catalog
    reader = LegacyReader(old_forms.data_dir)
    assert {(s.tournament_id, s.season_id) for s in reader.summary_files()} - {
        (p.tournament_id, p.season_id) for p in reader.schedule_pages()} == {(17, 76986)}

    found = {i: r for i, r in listing_rows(cat).items() if r["season_id"] == 76986}
    assert set(found) == {sf.event_id(sf.PL_OLD_A), sf.event_id(sf.PL_OLD_B)}
    for ev in (sf.PL_OLD_A, sf.PL_OLD_B):
        row = found[sf.event_id(ev)]
        event = sf.event_payload(ev)
        assert (row["tournament_id"], row["season_id"], row["sport"], row["stage_name"], row["round"],
                row["listed_in"], row["start_ts"]) == (
            17, 76986, "football", "Premier League", 38, "round_38", event["startTimestamp"])
        assert (row["home_name"], row["away_name"], row["home_score"], row["away_score"],
                row["home_score_current"], row["away_score_current"]) == (
            ev.home, ev.away, event["homeScore"]["current"], event["awayScore"]["current"],
            event["homeScore"]["current"], event["awayScore"]["current"])
        # CSV'de yalnızca durum metni var: sınıf ondan çıkar, tür ve kod bilinmez; takım kimliği yok
        assert (row["status_type"], row["status_code"], row["status_description"], row["status_class"]) == (
            None, None, "Ended", "completed")
        assert (row["home_id"], row["away_id"], row["stale"], row["first_seen_at"], row["updated_at"]) == (
            None, None, 0, BASE, BASE)
    assert schedule_slices(cat, 76986) == {}  # özet bir program dilimi değildir
    # sezonun adı sezon listesinden gelir; CSV'den boş bir turnuva satırı yazılmaz
    assert {r["id"]: r["name"] for r in rows(cat, "tournaments")} == {8: "LaLiga", 17: "Premier League"}
    assert report.problems == [p for p in report.problems if p.kind in ("no_event_payload", "corrupt")]

    # sayfası olan sezonda CSV okunmaz: satır sayfadaki olay nesnesinden gelir (takım kimlikleriyle)
    liga = event_row(cat, sf.event_id(sf.LIGA_B))
    assert (liga["row_source"], liga["listed_in"], liga["status_type"]) == ("listing", "round_1_full", "finished")
    assert liga["home_id"] is not None


def test_first_version_round_csv_and_summary_for_an_event_with_a_payload(tmp_path: Path, make_admin) -> None:
    data = tmp_path / "data"
    season = "matches/8_LaLiga/77559_LaLiga_25_26"
    events = [sf.event_payload(sf.LIGA_A), sf.event_payload(sf.LIGA_B)]
    # ilk sürümün tur başına CSV'si (13 sütun) sezon dizininde; yanında tur JSON'u yok
    write(data, f"{season}/round_1_matches.csv",
          sf.dump_csv(sf.OLD_ROUND_CSV_COLUMNS, [sf.old_round_row(e) for e in events]), BASE - 10)
    # lig dizininde bugünkü on sütunlu özet: daha yeni, aynı maç için geçerli olan bu
    summary = sf.summary_row("last_0", {**events[1], "homeScore": {"current": 7}})
    odd = {**sf.summary_row("final", events[0]), "match_id": 15000009, "home_score": "", "match_date": "not a date"}
    write(data, "matches/8_LaLiga/77559_LaLiga_25_26_summary.csv",
          sf.dump_csv(sf.SUMMARY_COLUMNS, [summary, odd, {**summary, "match_id": ""}]), BASE)
    detail(data, sf.LIGA_A, f"match_details/8_LaLiga/season_LaLiga_25_26/{events[0]['id']}",
           observed="2026-05-24T18:00:00+00:00", mtime=BASE - 100)
    admin = make_admin(data)

    report = admin.rebuild()

    cat = admin.catalog
    first = event_row(cat, events[0]["id"])  # olay yükü var: satır yükten, listed_in tur sütunundan
    assert (first["row_source"], first["listed_in"], first["stale"], first["home_id"]) == (
        "event", "round_1", 0, events[0]["homeTeam"]["id"])
    second = event_row(cat, events[1]["id"])  # iki CSV'de de var: en yenisi (lig dizinindeki özet)
    assert (second["row_source"], second["listed_in"], second["home_score_current"], second["round"],
            second["first_seen_at"], second["updated_at"], second["home_id"], second["status_type"]) == (
        "listing", "last_0", 7, None, BASE - 10, BASE, None, None)
    third = event_row(cat, 15000009)
    assert (third["listed_in"], third["round"], third["home_score_current"], third["start_ts"],
            third["status_class"], third["sport"]) == (None, None, None, None, "completed", "football")
    assert problem_kinds(report) == [("matches/8_LaLiga/77559_LaLiga_25_26_summary.csv", "malformed")]
    assert (report.listed, report.schedules) == (2, 0)
    # tur CSV'sinden gelen satır: takım kimlikleri, durum türü ve başlangıç zamanı sütunlardan
    remove(data, "matches/8_LaLiga/77559_LaLiga_25_26_summary.csv")
    admin.rebuild()
    second = event_row(cat, events[1]["id"])
    assert (second["home_id"], second["status_type"], second["status_class"], second["start_ts"], second["slug"],
            second["listed_in"], second["round"]) == (
        events[1]["homeTeam"]["id"], "finished", "completed", events[1]["startTimestamp"], events[1]["slug"],
        "round_1", 1)
    assert [r["id"] for r in rows(cat, "participants")] == sorted(
        {events[0]["homeTeam"]["id"], events[0]["awayTeam"]["id"], events[1]["homeTeam"]["id"],
         events[1]["awayTeam"]["id"]})
    assert admin.verify(deep=True).ok


def test_unreadable_summary_files_are_reported_and_do_not_abort(tmp_path: Path, make_admin) -> None:
    data = tmp_path / "data"
    league = "matches/8_LaLiga"
    row = sf.summary_row(1, sf.event_payload(sf.LIGA_A))
    write(data, f"{league}/77559_LaLiga_25_26_summary.csv", b"\xff\xfe not utf-8")
    write(data, f"{league}/77559_LaLiga_25_26_matches.csv",
          sf.dump_csv(sf.SUMMARY_COLUMNS, [{**row, "home_team": "x" * 200_000}]))  # csv modülünün alan sınırı
    write(data, f"{league}/77560_LaLiga_24_25_summary.csv", sf.dump_csv(sf.SUMMARY_COLUMNS, [row]))
    write(data, f"{league}/77560_LaLiga_24_25_summary.json", [{"events": [sf.event_payload(sf.LIGA_A)]}])
    # kimliği olmayan sezon dizinindeki özet hiçbir sezona yazılamaz
    write(data, f"{league}/old_files/round_1_matches.csv", sf.dump_csv(sf.SUMMARY_COLUMNS, [row]))
    admin = make_admin(data)

    report = admin.rebuild()

    assert report.completed and problem_kinds(report) == [
        (f"{league}/77559_LaLiga_25_26_matches.csv", "corrupt"),
        (f"{league}/77559_LaLiga_25_26_summary.csv", "corrupt"),
        (f"{league}/old_files", "unknown_name")]
    found = listing_rows(admin.catalog)  # özetin JSON'u okunmaz: satır CSV'den
    assert [(r["id"], r["season_id"], r["home_id"]) for r in found.values()] == [(sf.event_id(sf.LIGA_A), 77560, None)]
    assert [r["path"] for r in rows(admin.catalog, "legacy_roots")] == [league]


def test_summary_helpers() -> None:
    assert [entities.round_sub(v) for v in ("38", 3, "last_0", "next_12", "final", "", None, "-1", "2.0")] == [
        "round_38", "round_3", "last_0", "next_12", None, None, None, None, "round_2"]
    assert entities.summary_event({"match_id": "x"}, 1, 2) is None
    assert entities.summary_event({"match_id": "7", "home_score": "2.0", "away_score": "1.5"}, 1, 2) == {
        "id": 7, "tournament": {"uniqueTournament": {"id": 1}}, "season": {"id": 2}, "homeScore": {"current": 2}}
    local = dt.datetime(2026, 5, 24, 18, 0, 0)
    event = entities.summary_event({"match_id": 9, "match_date": local.isoformat(), "start_timestamp": ""}, 1, 2)
    assert event["startTimestamp"] == int(local.timestamp())


# --- sezon listeleri ve program dilimleri ----------------------------------------------------------------

def test_season_lists_give_listed_seasons_and_one_slice_per_tournament(old_forms: sf.LegacyFixture, make_admin) -> None:
    data = old_forms.data_dir
    admin = make_admin(data, league_names=old_forms.leagues)
    report = admin.rebuild()
    cat = admin.catalog

    # en yeni dosya geçerli (adı ne olursa olsun); CSV yalnızca JSON'u olmayan turnuva için
    lists = {r["entity_id"]: r for r in rows(cat, "entity_slices", "WHERE kind = 'tournament'")}
    assert {t: r["path"] for t, r in lists.items()} == {
        8: "seasons/LaLiga_seasons.json", 17: "seasons/17_Premier_League_seasons.json",
        2361: "seasons/2361_Wimbledon, Men_seasons.json"}
    size = (data / "seasons" / "17_Premier_League_seasons.json").stat().st_size
    assert {k: lists[17][k] for k in ("key", "sub", "state", "has_payload", "fetched_at", "checked_at",
                                      "stored_bytes", "raw_bytes", "meta_json", "layout")} == {
        "key": "seasons", "sub": "", "state": "ok", "has_payload": 1, "fetched_at": BASE, "checked_at": BASE,
        "stored_bytes": size, "raw_bytes": None, "meta_json": None, "layout": "legacy"}
    seasons = {r["id"]: r for r in rows(cat, "seasons")}
    assert [(i, seasons[i]["listed"], seasons[i]["position"]) for i in (96668, 76986, 61627)] == [
        (96668, 1, 0), (76986, 1, 1), (61627, 1, 2)]
    assert (seasons[96668]["name"], seasons[96668]["year"], seasons[96668]["sort_key"],
            seasons[96668]["tournament_id"], seasons[96668]["updated_at"]) == (
        "Premier League 26/27", "26/27", 2026.0, 17, BASE)
    assert report.season_lists == 3
    assert sorted((s.path, s.winner) for s in report.superseded_files if s.kind == "season_list") == [
        ("league_seasons.csv", "seasons/17_Premier_League_seasons.json"),
        ("league_seasons.csv", "seasons/LaLiga_seasons.json"),
        ("seasons/17_seasons.json", "seasons/17_Premier_League_seasons.json")]

    # ad → kimlik eşlemesi verilmezse adında kimlik olmayan dosyanın turnuvası bulunamaz: CSV devreye girer
    blind = make_admin(data, catalog=Catalog(str(data / ".meta" / "blind.db")))
    blind_report = blind.rebuild()
    assert ("seasons/LaLiga_seasons.json", "unresolved_tournament") in problem_kinds(blind_report)
    lists = {r["entity_id"]: r for r in rows(blind.catalog, "entity_slices", "WHERE kind = 'tournament'")}
    assert (lists[8]["path"], lists[8]["stored_bytes"]) == ("league_seasons.csv", None)
    assert [r["id"] for r in rows(blind.catalog, "seasons", "WHERE tournament_id = 8 AND listed = 1")] == [77559]
    blind.catalog.close()

    # eşleme bir işlev de olabilir (takip listesi değişebilir: her taramada yeniden sorulur)
    lazy = make_admin(data, catalog=Catalog(str(data / ".meta" / "lazy.db")), league_names=lambda: {8: "LaLiga"})
    assert lazy.rebuild().season_lists == 3
    lazy.catalog.close()


def test_season_rows_from_events_do_not_replace_the_season_list(tmp_path: Path, make_admin) -> None:
    data = tmp_path / "data"
    write(data, "seasons/17_Premier_League_seasons.json", {"seasons": [
        {"id": 96668, "name": "From the list", "year": "26/27"}, {"name": "no id"},
        {"id": 76986, "name": "Older", "year": "25/26"}]})
    write(data, "seasons/132_NBA_seasons.json", {"seasons": []})
    write(data, "seasons/19_FA_Cup_seasons.json", b"{ not json")
    page(data, f"{PL_SEASON}/round_1.json", [listed(sf.PL_ARS)], _complete=True)
    detail(data, sf.PL_LIV)
    admin = make_admin(data)
    report = admin.rebuild()

    seasons = {r["id"]: r for r in rows(admin.catalog, "seasons")}
    assert set(seasons) == {96668, 76986}
    assert (seasons[96668]["name"], seasons[96668]["listed"], seasons[96668]["position"]) == ("From the list", 1, 0)
    assert seasons[76986]["position"] == 2  # kimliği olmayan öğe sırayı tüketir
    empty = rows(admin.catalog, "entity_slices", "WHERE kind = 'tournament' AND entity_id = 132")[0]
    assert (empty["state"], empty["has_payload"]) == ("empty", 1)
    assert problem_kinds(report) == [("seasons/19_FA_Cup_seasons.json", "corrupt")]
    assert report.season_lists == 2


def test_schedule_slices_follow_the_page_files(canonical: sf.LegacyFixture, make_admin) -> None:
    data = canonical.data_dir
    write(data, f"{PL_SEASON}/round_4.json", b'{"events": [')  # yarım kalmış sayfa
    page(data, f"{PL_SEASON}/round_5.json", [], BASE + 7, _complete=False)  # boş tur
    page(data, f"{PL_SEASON}/round_6.json", [{"no": "id"}, 5, {"id": "17"}, {"id": True}], _complete=False)
    write(data, f"{PL_SEASON}/notes.json", {"events": []})  # program dosyası değil
    admin = make_admin(data, league_names=canonical.leagues)
    report = admin.rebuild()

    found = schedule_slices(admin.catalog, 96668)
    assert set(found) == {"round_1", "round_2", "round_3", "round_4", "round_5", "round_6"}
    size = (data / PL_SEASON / "round_2.json").stat().st_size
    assert found["round_2"] == {
        "kind": "season", "entity_id": 96668, "key": "schedule", "sub": "round_2", "state": "ok", "has_payload": 1,
        "fetched_at": BASE, "checked_at": BASE, "empty_count": 0, "unverified_empty_count": 0,
        "error_reason": None, "error_status": None, "error_at": None, "error_count": 0, "stored_bytes": size,
        "raw_bytes": None, "history_count": 0, "meta_json": '{"complete":false}', "layout": "legacy",
        "path": f"{PL_SEASON}/round_2.json"}
    assert found["round_1"]["meta_json"] == '{"complete":true}'
    broken = found["round_4"]
    assert (broken["state"], broken["has_payload"], broken["error_reason"], broken["error_count"],
            broken["fetched_at"], broken["checked_at"], broken["stored_bytes"], broken["meta_json"]) == (
        "error", 0, "corrupt", 1, None, BASE, None, None)
    assert (found["round_5"]["state"], found["round_5"]["has_payload"], found["round_5"]["fetched_at"]) == (
        "empty", 1, BASE + 7)
    assert found["round_6"]["state"] == "ok"  # öğeleri var ama hiçbiri kullanılamıyor: sorun olarak bildirilir
    pages = schedule_slices(admin.catalog, 80229)  # olay sayfaları süzülerek yazılmış sayılır
    assert {sub: row["meta_json"] for sub, row in pages.items()} == {
        "last_0": '{"filtered":true}', "last_1": '{"filtered":true}'}
    assert problem_kinds(report) == [
        (f"{PL_SEASON}/notes.json", "unknown_name"), (f"{PL_SEASON}/round_4.json", "corrupt"),
        (f"{PL_SEASON}/round_6.json", "malformed")]
    assert report.schedules == 11 + 3 and admin.verify(deep=True).ok


def test_listed_event_of_another_season_is_reported(tmp_path: Path, make_admin) -> None:
    """Bir sayfadaki maç o dizinin turnuvasına ve sezonuna yazılır; yük başka bir şey söylüyorsa bildirilir."""
    data = tmp_path / "data"
    foreign = listed(sf.PL_ARS, season={"id": 5, "name": "Other", "year": "20/21"})
    bare = {k: v for k, v in listed(sf.PL_LIV).items() if k not in ("season", "tournament")}
    page(data, f"{PL_SEASON}/round_1.json", [foreign, bare, listed(sf.PL_LEE), listed(sf.PL_BRE)], BASE + 60,
         _complete=True)
    detail(data, sf.PL_LEE, season={"id": 5, "name": "Other", "year": "20/21"})  # olay yükü başka sezon söylüyor
    detail(data, sf.PL_BRE)
    admin = make_admin(data)
    report = admin.rebuild()

    ars, liv = event_row(admin.catalog, ARS), event_row(admin.catalog, LIV)
    assert (ars["row_source"], ars["tournament_id"], ars["season_id"], ars["listed_in"]) == (
        "listing", 17, 96668, "round_1")
    # turnuvası / sezonu olmayan liste öğesi dizininkini alır (uyuşmazlık değil); sporu bilinmiyor
    assert (liv["tournament_id"], liv["season_id"], liv["sport"], liv["listed_in"]) == (17, 96668, "football", "round_1")
    lee = event_row(admin.catalog, LEE)
    assert (lee["row_source"], lee["season_id"], lee["listed_in"], lee["stale"]) == ("event", 5, None, 0)
    assert event_row(admin.catalog, sf.event_id(sf.PL_BRE))["listed_in"] == "round_1"
    assert problem_kinds(report) == [(f"{PL_DETAILS}/{LEE}", "season_mismatch"),
                                     (f"{PL_SEASON}/round_1.json", "season_mismatch")]
    assert admin.verify(deep=True).ok


def test_unstorable_listing_values_do_not_abort(tmp_path: Path, make_admin, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "data"
    text = json.dumps({"events": [listed(sf.PL_ARS), listed(sf.PL_LIV)], "_complete": True})
    text = text.replace('"Arsenal"', '"Ars\\ud800enal"')  # eşsiz vekil kod noktası
    write(data, f"{PL_SEASON}/round_1.json", text.encode("ascii"))
    real = derive.event_row

    def picky(payload: Any, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        if payload["id"] == LIV:
            raise TypeError("beklenmeyen biçim")
        return real(payload, *args, **kwargs)

    monkeypatch.setattr(derive, "event_row", picky)
    admin = make_admin(data)
    report = admin.rebuild()

    assert report.completed and event_row(admin.catalog, ARS)["home_name"] == "Ars?enal"
    assert event_row(admin.catalog, LIV) is None
    assert [(p.path, p.kind) for p in report.problems] == [(f"{PL_SEASON}/round_1.json", "malformed")]
    names = {r["name"] for r in rows(admin.catalog, "participants")}
    assert "Ars?enal" in names and "Liverpool" not in names


# --- değişiklik günlüğü ----------------------------------------------------------------------------------

def test_change_seq_is_the_line_number_and_survives_a_rebuild(canonical: sf.LegacyFixture, make_admin) -> None:
    admin = make_admin(canonical.data_dir, league_names=canonical.leagues)
    report = admin.rebuild()
    lines = (canonical.data_dir / "score_changes.jsonl").read_text(encoding="utf-8").splitlines()

    found = rows(admin.catalog, "changes")
    assert [r["seq"] for r in found] == [1, 2] and report.changes == 2
    assert [r["row_json"] for r in found] == lines
    first, second = found
    assert (first["event_id"], first["sport"], first["tournament_id"], first["status_regressed"], first["segment"],
            first["ts"]) == (17099711, "football", 17, 0, "score_changes.jsonl",
                             int(dt.datetime(2026, 9, 15, 13, 10, tzinfo=UTC).timestamp()))
    assert first["fields"] == "awayScore.current,awayScore.display,awayScore.normaltime,awayScore.period1"
    assert (second["event_id"], second["status_regressed"], second["tournament_id"]) == (17060394, 1, 132)
    assert second["fields"].split(",") == list(sf.SCORE_CHANGES[1]["changed"])

    before = rows(admin.catalog, "changes")
    assert admin.rebuild(mode="in_place").changes == 2 and rows(admin.catalog, "changes") == before
    assert admin.rebuild(mode="recreate").changes == 2 and rows(admin.catalog, "changes") == before


def test_change_lines_keep_their_numbers_when_other_lines_are_unusable(tmp_path: Path, make_admin) -> None:
    data = tmp_path / "data"
    good = dict(sf.SCORE_CHANGES[0])
    body = b"".join([
        json.dumps(good).encode() + b"\r\n",  # 1: Windows satır sonu
        b"\n",  # 2: boş satır numara harcar
        b"{ not json\n",  # 3
        json.dumps({**good, "event_id": "x"}).encode() + b"\n",  # 4: maç kimliği yok
        json.dumps({**good, "ts_utc": "yesterday", "event_id": 5}).encode() + b"\n",  # 5: tarihi okunmuyor
        json.dumps({"ts_utc": "2026-09-15T13:10:00", "event_id": 6, "sport": 3, "tournament": "x",
                    "changed": ["a"]}).encode() + b"\n",  # 6: saat dilimi yok = UTC; öteki alanlar kullanılamıyor
        # 7: JSON kaçışıyla yazılmış eşsiz vekil kod noktası SQLite'a yazılamaz: '?' olur
        b'{"ts_utc": "2026-09-15T13:10:00Z", "event_id": 7, "sport": "\\ud800", "changed": {"winnerCode": [1, 2]}}\n',
        json.dumps({**good, "event_id": 8}).encode(),  # 8: satır sonu yok: yarım yazma
    ])
    write(data, "score_changes.jsonl", body)
    admin = make_admin(data)
    report = admin.rebuild()

    found = rows(admin.catalog, "changes")
    assert [(r["seq"], r["event_id"]) for r in found] == [(1, 17099711), (6, 6), (7, 7)]
    at = int(dt.datetime(2026, 9, 15, 13, 10, tzinfo=UTC).timestamp())
    assert (found[1]["ts"], found[1]["sport"], found[1]["tournament_id"], found[1]["fields"]) == (at, None, None, "")
    assert (found[2]["ts"], found[2]["sport"], found[2]["fields"]) == (at, "?", "winnerCode")
    assert found[0]["row_json"] == json.dumps(good)  # satır sonu olmadan, yazıldığı gibi
    assert sorted((p.path, p.kind) for p in report.problems) == [
        ("score_changes.jsonl", "corrupt"), ("score_changes.jsonl", "malformed"),
        ("score_changes.jsonl", "malformed"), ("score_changes.jsonl", "torn_line")]
    assert report.changes == 3

    assert changes.change_row(3, {"ts_utc": "2026-09-15T13:10:00+03:00", "event_id": 1}, "{}", "x")["ts"] == at - 10800
    assert changes.change_row(3, {"ts_utc": None, "event_id": 1}, "{}", "x") is None
    assert changes.change_row(3, {"ts_utc": "2026-09-15T13:10:00Z", "event_id": True}, "{}", "x") is None


# --- yeniden kurma: belirlenimlilik, kipler, ilerleme -----------------------------------------------------

def test_rebuilding_the_whole_tree_twice_gives_identical_rows(fx: sf.LegacyFixture, make_admin, tmp_path: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    admin = make_admin(fx.data_dir, league_names=fx.leagues)
    first = admin.rebuild()
    before = snapshot(admin.catalog)

    second = admin.rebuild()
    assert (first.mode, second.mode) == ("recreate", "in_place") and snapshot(admin.catalog) == before
    assert admin.rebuild(mode="recreate").counts == first.counts and snapshot(admin.catalog) == before
    assert rebuilt(fx.data_dir, tmp_path, league_names=fx.leagues) == before
    for name in ("season_lists", "schedules", "listed", "changes", "events", "slices", "problems",
                 "superseded", "superseded_files"):
        assert getattr(second, name) == getattr(first, name), name

    # dizin listeleme sırası ve toplu yazma sınırı satırları değiştirmez
    real = os.scandir

    class Reversed:
        def __init__(self, path: Any) -> None:
            with real(path) as scan:
                self.entries = sorted(scan, key=lambda e: e.name, reverse=True)

        def __enter__(self) -> List[Any]:
            return self.entries

        def __exit__(self, *exc_info: Any) -> None:
            return None

    monkeypatch.setattr(os, "scandir", Reversed)
    monkeypatch.setattr(indexer, "_BATCH_EVENTS", 3)
    monkeypatch.setattr(entities, "_CHUNK", 2)
    admin.rebuild()
    assert snapshot(admin.catalog) == before
    assert admin.verify().ok and admin.verify(deep=True).ok


def test_rebuild_report_meta_and_progress(canonical: sf.LegacyFixture, make_admin) -> None:
    admin = make_admin(canonical.data_dir, league_names=canonical.leagues)
    stages: List[Tuple[str, int, int]] = []
    report = admin.rebuild(progress=lambda *step: stages.append(step))
    cat = admin.catalog

    # özetlere girmeyen maçlar da (süzülmeden yazılmış tur dosyalarındaki başlamamış / ertelenmiş maçlar) listelenir
    assert (report.events, report.listed, report.schedules, report.season_lists, report.changes) == (
        len(canonical.detail_ids), 12, 11, 5, 2)
    assert set(canonical.event_ids) < {r["id"] for r in rows(cat, "events")} and report.counts["events"] == 35
    assert report.counts == {t: len(rows(cat, t)) for t in report.counts}
    assert json.loads(cat.get_meta("counts")) == report.counts
    assert (report.counts["entity_slices"], report.counts["changes"], report.counts["legacy_roots"]) == (16, 2, 17)
    assert [s[0] for s in stages] == ["scan", "scan", "listings", "legacy_events", "finish", "finish"]
    assert stages[2] == ("listings", 6, 6)  # sayfası olan altı sezon
    stats = admin.stats()
    assert stats["events"]["by_layout"] == {"legacy": report.events, "listing": report.listed}
    assert stats["events"]["with_event_payload"] == report.events and stats["tables"] == report.counts
    kinds = {r["path"]: r["kind"] for r in rows(cat, "legacy_roots")}
    assert kinds["score_changes.jsonl"] == "changes_file" and kinds["matches/132_NBA"] == "league_dir"
    assert kinds["matches/132_NBA/80229_NBA_26_27"] == "schedule_dir"
    assert kinds["seasons/132_NBA_seasons.json"] == "seasons_file"
    assert all(r["scanned_at"] == NOW for r in rows(cat, "legacy_roots"))


def test_should_stop_during_the_listings_leaves_the_old_catalog(canonical: sf.LegacyFixture, make_admin) -> None:
    admin = make_admin(canonical.data_dir, league_names=canonical.leagues)
    admin.rebuild()
    before = snapshot(admin.catalog)
    page(canonical.data_dir, f"{PL_SEASON}/round_8.json", [listed(sf.PL_FUTURE)], _complete=False)
    asked = {"n": 0}

    def stop() -> bool:
        asked["n"] += 1
        return asked["n"] > 4  # iki tarama adımı ve iki sezondan sonra

    for mode in ("in_place", "recreate"):
        asked["n"] = 0
        report = admin.rebuild(should_stop=stop, mode=mode)
        assert (report.completed, asked["n"]) == (False, 5) and snapshot(admin.catalog) == before
    assert admin.rebuild().completed and "round_8" in schedule_slices(admin.catalog, 96668)


def test_in_place_rebuild_of_listings_is_invisible_until_commit(canonical: sf.LegacyFixture, make_admin) -> None:
    admin = make_admin(canonical.data_dir, league_names=canonical.leagues)
    admin.rebuild()
    old = snapshot(admin.catalog)
    page(canonical.data_dir, f"{PL_SEASON}/round_8.json", [listed(sf.PL_FUTURE, winnerCode=1)], BASE + 5,
         _complete=False)
    seen: List[Dict[str, List[Dict[str, Any]]]] = []
    with Catalog(admin.catalog.path) as reader:  # başka bir süreçteki okuyucu gibi
        report = admin.rebuild(progress=lambda *step: seen.append(snapshot(reader)))
        assert report.mode == "in_place" and len(seen) >= 6
        assert all(shot == old for shot in seen)
        assert event_row(reader, sf.event_id(sf.PL_FUTURE))["listed_in"] == "round_8"


# --- tek maçın yeniden dizinlenmesi ve doğrulama ---------------------------------------------------------

def test_event_whose_directory_vanished_falls_back_to_its_listing_row(canonical: sf.LegacyFixture, make_admin,
                                                                       tmp_path: Path) -> None:
    data = canonical.data_dir
    admin = make_admin(data, league_names=canonical.leagues)
    admin.rebuild()
    assert event_row(admin.catalog, ARS)["row_source"] == "event"

    remove(data, f"{PL_DETAILS}/{ARS}")
    assert admin.index_event(ARS) is None

    row = event_row(admin.catalog, ARS)
    item = next(e for e in read_json(data, f"{PL_SEASON}/round_1.json")["events"] if e["id"] == ARS)
    assert {n: row[n] for n in derive.EVENT_DERIVED_COLUMNS} == derive.event_row(item, "listing")
    assert (row["layout"], row["path"], row["sig"], row["listed_in"], row["stale"]) == (None, None, None, "round_1", 0)
    assert not rows(admin.catalog, "event_slices", f"WHERE event_id = {ARS}")
    assert facts(snapshot(admin.catalog)) == facts(rebuilt(data, tmp_path, league_names=canonical.leagues))
    assert admin.index_event(ARS) is None and event_row(admin.catalog, ARS) == row  # liste satırına dokunulmaz

    # doğrulamanın onarımı da aynı yoldan geçer
    remove(data, f"{PL_DETAILS}/{LIV}")
    report = admin.verify(repair=True)
    assert [(i.invariant, i.event_id, i.repaired) for i in report.issues] == [("I1", LIV, True)]
    assert event_row(admin.catalog, LIV)["row_source"] == "listing" and admin.verify(deep=True).ok
    assert facts(snapshot(admin.catalog)) == facts(rebuilt(data, tmp_path, league_names=canonical.leagues))

    # hiçbir sayfada listelenmeyen maçın satırı silinir
    with admin.catalog.write() as conn:
        conn.execute("UPDATE events SET listed_in = NULL WHERE id = ?", (LEE,))
    remove(data, f"{PL_DETAILS}/{LEE}")
    assert admin.index_event(LEE) is None and event_row(admin.catalog, LEE) is None


def test_index_event_keeps_listed_in_and_recomputes_stale(tmp_path: Path, make_admin) -> None:
    data = tmp_path / "data"
    page(data, f"{PL_SEASON}/round_1.json", [listed(sf.PL_ARS, winnerCode=2), listed(sf.PL_LIV)], BASE + 60,
         _complete=True)
    admin = make_admin(data)
    admin.rebuild()
    assert event_row(admin.catalog, ARS)["row_source"] == "listing"

    # maçın dizini gelir (yazıcı dizini söyler): liste daha yeni ve farklı
    base = detail(data, sf.PL_ARS, slices=("statistics",))
    assert admin.index_event(ARS, paths=[base]) == "legacy"
    row = event_row(admin.catalog, ARS)
    assert (row["row_source"], row["listed_in"], row["stale"], row["winner_code"]) == ("event", "round_1", 1, 1)
    assert facts(snapshot(admin.catalog)) == facts(rebuilt(data, tmp_path))

    # yük yeniden gözlenir (liste sonrasında): bayrak düşer, sayfa okunmaz
    write(data, f"{base}/observation.json", sf.observation_payload(sf.basic_payload(sf.PL_ARS), utc(BASE + 120)))
    assert admin.index_event(ARS) == "legacy"
    assert (event_row(admin.catalog, ARS)["stale"], event_row(admin.catalog, ARS)["listed_in"]) == (0, "round_1")
    assert facts(snapshot(admin.catalog)) == facts(rebuilt(data, tmp_path))

    # yük artık başka bir sezon söylüyor: sayfayla bağı kopar
    write(data, f"{base}/basic.json", {**sf.basic_payload(sf.PL_ARS), "season": {"id": 5, "name": "x"}}, BASE + 200)
    assert admin.index_event(ARS) == "legacy"
    assert (event_row(admin.catalog, ARS)["listed_in"], event_row(admin.catalog, ARS)["stale"]) == (None, 0)

    # sayfası okunamayan ya da maçı artık listelemeyen sezonda sütunlara dokunulmaz (uzlaştırma düzeltir)
    detail(data, sf.PL_LIV, mtime=BASE)
    with admin.catalog.write() as conn:
        conn.execute("UPDATE events SET stale = 1 WHERE id = ?", (LIV,))
    for content in (b"{", sf.dump_json({"events": [listed(sf.PL_ARS)]}), sf.dump_json({"events": "none"})):
        (data / PL_SEASON / "round_1.json").write_bytes(content)
        assert admin.index_event(LIV, paths=[f"{PL_DETAILS}/{LIV}"]) == "legacy"
        assert (event_row(admin.catalog, LIV)["listed_in"], event_row(admin.catalog, LIV)["stale"]) == ("round_1", 1)


# --- katmanlama ------------------------------------------------------------------------------------------

def test_new_modules_import_only_what_the_store_may_import() -> None:
    """Bölüm 2.1: Store yalnızca src.sports, src.status, src.slices, src.exceptions ve src.version'ı içe aktarabilir."""
    code = (
        "import sys, json; import src.store.entities, src.store.changes; "
        "print(json.dumps(sorted(m for m in sys.modules if m == 'src' or m.startswith('src.'))))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    loaded = set(json.loads(out.stdout))

    assert {m for m in loaded if not m.startswith("src.store")} == {
        "src", "src.exceptions", "src.slices", "src.sports", "src.status"}
    assert "src.store.indexer" not in loaded  # dizinleyici bu iki modülü kullanır, tersi değil
    assert catalog_mod.DERIVE_VERSION == derive.DERIVE_VERSION  # bu modüller sürümü kendileri tutmaz
