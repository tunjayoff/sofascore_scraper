"""
src/store/indexer.py ve src/store/verify.py: maçların ve dilimlerinin dizinlenmesi, yeniden kurma, doğrulama
(plan maddesi ST-07; docs/design/01-storage.md bölüm 3.4, 3.6, 5.2).

Altın dosyalar tests/golden/catalog/<dizin>.json, `tests/store_fixtures.py`'nin kurduğu her veri dizini
için yeniden kurmanın yazdığı satırları tutar (`sig` hariç: dizin mtime'ına bağlıdır). Çıktı bilerek
değiştiyse şöyle yeniden üretilir:

    REGEN_CATALOG_GOLDEN=1 python -m pytest tests/test_store_indexer.py

Ağ yok. v3 dizinlerini henüz hiçbir yazıcı üretmiyor (ST-20); testler onları manifest ve codec
modülleriyle kendisi kurar.

İmzalar mtime'a bakar ve dosya sistemlerinin saat çözünürlüğü kabadır: bir ağacı değiştiren testler
`bump` ile mtime'ı açıkça ileri alır.
"""
from __future__ import annotations

import datetime as dt
import gzip
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import pytest

import store_fixtures as sf
from src.store import catalog as catalog_mod
from src.store import codec, derive, files, indexer, layout, manifest, verify
from src.store.catalog import Catalog
from src.store.errors import CatalogCorrupt, StoreBusy, StoreError
from src.store.indexer import CatalogAdmin, IndexProblem, SupersededDir
from src.store.legacy import LegacyEvent, LegacyReader
from src.store.manifest import EmptyMark, ErrorMark, HistoryMark, Manifest, Observation, SliceEntry

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = Path(__file__).parent / "golden" / "catalog"
REGEN = os.getenv("REGEN_CATALOG_GOLDEN") == "1"
UTC = dt.timezone.utc
NOW = sf.FIXTURE_NOW
T0 = dt.datetime(2026, 9, 20, 10, 0, 0, tzinfo=UTC)
T1 = dt.datetime(2026, 9, 21, 11, 30, 0, tzinfo=UTC)

PL_DIR = "match_details/17_Premier_League/season_Premier_League_26_27"
ARS = 16837335  # canonical ve legacy dizinlerinde detayı olan maç (legacy'de iki yerde durur)

# Altın dosyaya giren tablolar ve sıraları (bu adımın doldurduğu tablolar)
GOLDEN_TABLES: Dict[str, str] = {
    "events": "id",
    "event_slices": "event_id, key, sub",
    "event_participants": "event_id, side",
    "participants": "id",
    "tournaments": "id",
    "seasons": "id",
    "sports": "slug",
    "categories": "id",
}
ALL_TABLES = (*GOLDEN_TABLES, "players", "entity_slices", "slice_history", "changes", "pending_writes",
              "legacy_roots", "meta")


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


def details_only(fixture: sf.LegacyFixture) -> sf.LegacyFixture:
    """
    Bu dosyanın testleri maç dizinlerini sınar: öteki kaynaklar (`matches/`, `seasons/`, `league_seasons.csv`,
    `score_changes.jsonl`) ağaçtan çıkarılır. Yeniden kurma onları da dizinler (ST-08); tam ağaçların
    satırları tests/test_store_indexer_listings.py ve tests/golden/catalog_listings/ altındadır.
    """
    for name in ("matches", "seasons"):
        files.remove_tree(fixture.data_dir / name)
    for name in ("league_seasons.csv", "score_changes.jsonl"):
        files.remove(fixture.data_dir / name)
    return fixture


@pytest.fixture(params=sf.FIXTURE_NAMES)
def fx(request: pytest.FixtureRequest, tmp_path: Path) -> sf.LegacyFixture:
    return details_only(sf.build_fixture(request.param, tmp_path / "data"))


@pytest.fixture
def canonical(tmp_path: Path) -> sf.LegacyFixture:
    return details_only(sf.build_fixture("canonical", tmp_path / "data"))


@pytest.fixture
def old_forms(tmp_path: Path) -> sf.LegacyFixture:
    return details_only(sf.build_fixture("legacy", tmp_path / "data"))


def rows(cat: Catalog, table: str, order: Optional[str] = None, where: str = "") -> List[Dict[str, Any]]:
    sql = f'SELECT * FROM "{table}" {where} ORDER BY {order or GOLDEN_TABLES.get(table, "1")}'
    return [dict(row) for row in cat.connection().execute(sql)]


def event_row(cat: Catalog, event_id: int) -> Optional[Dict[str, Any]]:
    found = rows(cat, "events", where=f"WHERE id = {int(event_id)}")
    return found[0] if found else None


def slice_rows(cat: Catalog, event_id: int) -> Dict[str, Dict[str, Any]]:
    found = rows(cat, "event_slices", where=f"WHERE event_id = {int(event_id)}")
    return {"/".join(p for p in (r["key"], r["sub"]) if p): r for r in found}


def snapshot(cat: Catalog, *, sig: bool = True) -> Dict[str, List[Dict[str, Any]]]:
    """
    Kataloğun bütün satırları; sig=False: imza sütunu atılır. Kurulumun kendisini anlatan meta satırları
    (ne zaman, hangi kiple, o andaki satır sayıları) dosyalardan türemez ve burada yoktur.
    """
    out: Dict[str, List[Dict[str, Any]]] = {}
    for table in ALL_TABLES:
        found = rows(cat, table, order=GOLDEN_TABLES.get(table) or "1, 2")
        if table == "events" and not sig:
            for row in found:
                row.pop("sig")
        if table == "meta":
            found = [row for row in found if row["key"] not in ("built_at", "build_mode", "counts")]
        out[table] = found
    return out


def count(cat: Catalog, table: str = "events") -> int:
    return int(cat.connection().execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])


def bump(path: Path, seconds: int = 5) -> None:
    """mtime'ı ileri alır: iki değişiklik aynı saat tikine düşüp imzayı aynı bırakmasın."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + seconds * 1_000_000_000))


def write(root: Path, rel: str, data: Any, mtime: int = sf.BASE_MTIME) -> Path:
    """Eski düzen dosyası yazar (bayt ise aynen, değilse fabrikanın JSON biçimiyle) ve dizinin imzasını değiştirir."""
    path = root.joinpath(*rel.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else sf.dump_json(data))
    os.utime(path, (mtime, mtime))
    bump(path.parent)
    return path


def legacy_detail(root: Path, ev: sf.Ev, base: Optional[str] = None, *, slices: Tuple[str, ...] = ("statistics",),
                  mtime: int = sf.BASE_MTIME, **edits: Any) -> str:
    """Küçük bir eski düzen maç dizini (L1); dizinin yolunu döndürür."""
    event = sf.basic_payload(ev)
    event.update(edits)
    base = base or f"{PL_DIR}/{event['id']}"
    write(root, f"{base}/basic.json", event, mtime)
    for key in slices:
        write(root, f"{base}/{key}.json", sf.slice_payload(key, event), mtime)
    return base


def write_v3(data_dir: Path, payload: Dict[str, Any], *, slices: Optional[Dict[str, Any]] = None,
             entries: Optional[Dict[str, SliceEntry]] = None, observation: Optional[Observation] = None,
             created: dt.datetime = T0, updated: dt.datetime = T1, migrated_from: Optional[str] = None) -> str:
    """Bir v3 maç dizini kurar: yükler codec ile, manifest en son yazılır. Dizinin göreli yolunu döndürür."""
    event_id = int(payload["id"])
    rel = layout.event_dir(event_id)
    found = Manifest(kind="event", id=event_id, created_at=created, updated_at=updated,
                     migrated_from=migrated_from, observation=observation)
    for name, data in {"event": payload, **(slices or {})}.items():
        key, sub = layout.split_slice_name(name)
        encoded = codec.write_payload(layout.resolve(data_dir, layout.slice_path(rel, key, sub)), data)
        found.slices[name] = SliceEntry(state="ok", fetched_at=updated, checked_at=updated,
                                        stored_bytes=encoded.stored_bytes, raw_bytes=encoded.raw_bytes,
                                        sha256=encoded.sha256)
    found.slices.update(entries or {})
    manifest.write_manifest(layout.resolve(data_dir, layout.manifest_path(rel)), found)
    return rel


def promote(data_dir: Path, event: LegacyEvent, *, at: dt.datetime = T1) -> str:
    """Eski düzendeki bir maçın v3 kopyasını kurar (eski dizin yerinde kalır): taşımanın yapacağının küçüğü."""
    assert event.payloads is not None
    rel = layout.event_dir(event.event_id)
    found = Manifest(kind="event", id=event.event_id, created_at=at, updated_at=at, migrated_from=event.path)
    if event.observation is not None:
        found.observation = Observation(observed_at=event.observation.observed_at,
                                        change_ts=event.observation.change_ts,
                                        status_regressed=event.observation.status_regressed)
    for entry in event.slices:
        out = SliceEntry(state=entry.state)
        if entry.has_payload:
            encoded = codec.write_payload(layout.resolve(data_dir, layout.slice_path(rel, entry.key)),
                                          event.payloads[entry.key])
            out.stored_bytes, out.raw_bytes, out.sha256 = encoded.stored_bytes, encoded.raw_bytes, encoded.sha256
            out.fetched_at = dt.datetime.fromtimestamp(int(entry.fetched_at or 0), UTC)
        if entry.empty_count or entry.unverified_empty_count:
            out.empty = EmptyMark(count=entry.empty_count, unverified=entry.unverified_empty_count, at=entry.empty_at)
        if entry.error is not None:
            out.error = ErrorMark(reason=entry.error.reason, status=entry.error.status, at=entry.error.at,
                                  count=entry.error.count)
        found.slices[entry.key] = out
    manifest.write_manifest(layout.resolve(data_dir, layout.manifest_path(rel)), found)
    return rel


def v3_file(data_dir: Path, event_id: int, name: str) -> Path:
    return Path(layout.resolve(data_dir, f"{layout.event_dir(event_id)}/{name}"))


def tree_state(root: Path, skip: Tuple[str, ...] = ()) -> Dict[str, Tuple[str, int]]:
    """Ağacın tamamı: göreli yol → (içerik özeti ya da "dir", mtime_ns)."""
    out: Dict[str, Tuple[str, int]] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(skip):
            continue
        digest = "dir" if path.is_dir() else codec.sha256_hex(path.read_bytes())
        out[rel] = (digest, path.stat().st_mtime_ns)
    return out


def issues(report: verify.VerifyReport) -> List[Tuple[str, str, Optional[int]]]:
    return [(issue.invariant, issue.kind, issue.event_id) for issue in report.issues]


# --- altın satırlar: her fixture dizini ---------------------------------------------------------------

def _compact(row: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in row.items() if v is not None}


def _golden_of(admin: CatalogAdmin, report: indexer.RebuildReport) -> Dict[str, Any]:
    tables = {}
    for table in GOLDEN_TABLES:
        found = rows(admin.catalog, table)
        for row in found:
            row.pop("sig", None)
        tables[table] = [_compact(row) for row in found]
    return {
        "derive_version": derive.DERIVE_VERSION,
        "report": {
            "events": report.events,
            "events_legacy": report.events_legacy,
            "events_v3": report.events_v3,
            "slices": report.slices,
            # sorunun ayrıntısı (JSON hata metni) Python sürümüne göre değişir: altın dosyaya girmez
            "problems": [[p.layout, p.path, p.kind] for p in report.problems],
            "superseded": [[s.event_id, s.path, s.winner, s.by] for s in report.superseded],
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
    head = {k: report[k] for k in ("events", "events_legacy", "events_v3", "slices")}
    tables = ",\n".join(f'  {json.dumps(name)}: {block(found, "  ")}' for name, found in data["tables"].items())
    text = (
        "{\n"
        f' "derive_version": {data["derive_version"]},\n'
        f' "report": {{\n  "counts": {json.dumps(head)},\n'
        f'  "problems": {block(report["problems"], "  ")},\n'
        f'  "superseded": {block(report["superseded"], "  ")}\n }},\n'
        f' "tables": {{\n{tables}\n }}\n'
        "}\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _read_golden(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    report = data["report"]
    data["report"] = {**report.pop("counts"), **report}
    return data


def test_golden_catalog_rows(fx: sf.LegacyFixture, make_admin) -> None:
    admin = make_admin(fx.data_dir)
    report = admin.rebuild()
    produced = json.loads(json.dumps(_golden_of(admin, report)))
    path = GOLDEN_DIR / f"{fx.name}.json"
    if REGEN:
        _write_golden(path, produced)
    golden = _read_golden(path)

    assert golden["derive_version"] == derive.DERIVE_VERSION, "türetme sürümü değişti: altın dosyayı yeniden üretin"
    assert produced["report"] == golden["report"]
    for table in GOLDEN_TABLES:
        assert produced["tables"][table] == golden["tables"][table], table
    assert report.completed and report.counts["events"] == len(golden["tables"]["events"])


def test_goldens_cover_every_fixture_directory() -> None:
    assert sorted(p.stem for p in GOLDEN_DIR.glob("*.json")) == sorted(sf.FIXTURE_NAMES)


def test_indexed_events_are_the_readers_events(fx: sf.LegacyFixture, make_admin) -> None:
    """Dizinleyici eski düzen kopyaları arasında LegacyReader.iter_events ile aynı dizini seçer."""
    admin = make_admin(fx.data_dir)
    admin.rebuild()
    winners = {e.event_id: e for e in LegacyReader(fx.data_dir).iter_events(payloads=False)}

    found = {row["id"]: row for row in rows(admin.catalog, "events")}
    assert {i: r["path"] for i, r in found.items()} == {i: e.path for i, e in winners.items()}
    assert sorted(found) == sorted(set(fx.detail_ids) - {17018572} if fx.name == "legacy" else fx.detail_ids)
    for event_id, row in found.items():
        assert (row["layout"], row["legacy_path"], row["sig"]) == ("legacy", None, winners[event_id].dir.sig)
        assert (row["row_source"], row["has_event_payload"], row["stale"], row["listed_in"]) == ("event", 1, 0, None)


def test_choice_between_copies_is_the_readers_choice(tmp_path: Path, make_admin) -> None:
    """Aynı maçın eski düzen kopyaları: en yeni olay yükü, eşitlikte lig dizini, okunamayan kopya atlanır."""
    data = tmp_path / "data"
    flat = {ev: f"match_details/{sf.event_id(ev)}" for ev in (sf.PL_ARS, sf.PL_LIV, sf.PL_LEE, sf.PL_BRE)}
    legacy_detail(data, sf.PL_ARS)  # aynı mtime: lig dizini önce gelir
    legacy_detail(data, sf.PL_ARS, flat[sf.PL_ARS])
    legacy_detail(data, sf.PL_LIV, mtime=sf.BASE_MTIME - 1)  # düz kopya daha yeni
    legacy_detail(data, sf.PL_LIV, flat[sf.PL_LIV])
    legacy_detail(data, sf.PL_LEE)  # en yeni kopya okunamıyor: sıradaki
    write(data, f"{flat[sf.PL_LEE]}/basic.json", b"{", mtime=sf.BASE_MTIME + 9)
    write(data, f"{flat[sf.PL_BRE]}/basic.json", b"{")  # tek kopya da okunamıyor: maç yok
    write(data, "match_details/0123/basic.json", {"id": 123})  # kurallı olmayan ad: kimlik tutmaz
    admin = make_admin(data)

    report = admin.rebuild()

    chosen = {row["id"]: row["path"] for row in rows(admin.catalog, "events")}
    assert chosen == {e.event_id: e.path for e in LegacyReader(data).iter_events(payloads=False)} == {
        sf.event_id(sf.PL_ARS): f"{PL_DIR}/{sf.event_id(sf.PL_ARS)}",
        sf.event_id(sf.PL_LIV): flat[sf.PL_LIV],
        sf.event_id(sf.PL_LEE): f"{PL_DIR}/{sf.event_id(sf.PL_LEE)}",
    }
    assert sorted((p.path, p.kind) for p in report.problems) == sorted([
        ("match_details/0123", "id_mismatch"), (flat[sf.PL_BRE], "corrupt"), (flat[sf.PL_LEE], "corrupt")])
    assert sorted((s.path, s.winner, s.by) for s in report.superseded) == [
        (flat[sf.PL_ARS], chosen[sf.event_id(sf.PL_ARS)], "legacy"),
        (f"{PL_DIR}/{sf.event_id(sf.PL_LIV)}", flat[sf.PL_LIV], "legacy"),
    ]
    quick = admin.verify()
    assert quick.ok and quick.events_read == 2  # okunamayan en yeni kopyası olan iki maç her seferinde okunur
    assert sorted((p.path, p.kind) for p in quick.problems) == sorted((p.path, p.kind) for p in report.problems)


def test_event_rows_are_the_derived_rows(fx: sf.LegacyFixture, make_admin) -> None:
    """I4: satırın yükten türeyen sütunları derive.event_row(olay yükü, gözlem) ile aynıdır."""
    admin = make_admin(fx.data_dir)
    admin.rebuild()
    for event in LegacyReader(fx.data_dir).iter_events(payloads=False):
        row = event_row(admin.catalog, event.event_id)
        observed = event.observation.observed_at if event.observation else None
        expected = derive.event_row(event.event, "event", observed)
        assert {name: row[name] for name in derive.EVENT_DERIVED_COLUMNS} == expected
        assert row["status_regressed"] == int(bool(event.observation and event.observation.status_regressed))
        assert set(row) == set(derive.EVENT_DERIVED_COLUMNS) | set(indexer.EVENT_STORAGE_COLUMNS) | {"stale", "listed_in"}


def test_slice_rows_follow_the_legacy_files(canonical: sf.LegacyFixture, make_admin) -> None:
    admin = make_admin(canonical.data_dir)
    admin.rebuild()
    at = int(dt.datetime.fromisoformat(sf.MARKER_AT).timestamp())

    # 111: lineups 2 sayımın 1'i doğrulanmış; incidents doğrulanmış 1 + hata; pregame_form doğrulanmamış 3
    mixed = slice_rows(admin.catalog, 16951514)
    assert set(mixed) == {"event", "statistics", "team_streaks", "h2h", "lineups", "incidents", "pregame_form"}
    assert (mixed["lineups"]["state"], mixed["lineups"]["empty_count"],
            mixed["lineups"]["unverified_empty_count"]) == ("empty", 1, 1)
    assert (mixed["incidents"]["state"], mixed["incidents"]["empty_count"], mixed["incidents"]["error_reason"],
            mixed["incidents"]["error_status"], mixed["incidents"]["error_count"]) == ("error", 1, "5xx", 503, 1)
    assert (mixed["pregame_form"]["state"], mixed["pregame_form"]["empty_count"],
            mixed["pregame_form"]["unverified_empty_count"]) == ("empty", 0, 3)
    for key in ("lineups", "incidents", "pregame_form"):
        assert (mixed[key]["has_payload"], mixed[key]["fetched_at"], mixed[key]["stored_bytes"]) == (0, None, None)
    assert mixed["lineups"]["checked_at"] == mixed["incidents"]["error_at"] == at
    assert mixed["pregame_form"]["checked_at"] is None  # hiçbir işaretinde zaman yok

    # yükü olan dilim: mtime, dosya boyutu; sıkıştırılmamış boyut dosya okunmadan bilinmez
    ok = mixed["statistics"]
    size = (canonical.data_dir / PL_DIR / "16951514" / "statistics.json").stat().st_size
    assert (ok["state"], ok["has_payload"], ok["fetched_at"], ok["checked_at"], ok["stored_bytes"],
            ok["raw_bytes"], ok["error_count"], ok["history_count"], ok["meta_json"], ok["sub"]) == (
        "ok", 1, sf.BASE_MTIME, sf.BASE_MTIME, size, None, 0, 0, None, "")

    # dosyası olan ama veri taşımayan dilim: yüküyle birlikte empty
    empty = slice_rows(admin.catalog, 17092269)["lineups"]
    assert (empty["state"], empty["has_payload"], empty["empty_count"], empty["fetched_at"]) == (
        "empty", 1, 2, sf.BASE_MTIME)

    # gözlem: var, yok, zamanı olmayan dosya, yapışkan bayrak
    observed = event_row(admin.catalog, ARS)
    assert observed["observed_at"] == int(dt.datetime(2026, 9, 19, 6, 0, tzinfo=UTC).timestamp())
    assert observed["observed_gap"] == observed["observed_at"] - observed["start_ts"]
    assert event_row(admin.catalog, 16867839)["observed_at"] is None  # observation.json yok
    assert event_row(admin.catalog, sf.event_id(sf.PL_OLD_A))["observed_at"] is None
    assert event_row(admin.catalog, sf.event_id(sf.NBA_VOID))["status_regressed"] == 1
    assert (observed["first_seen_at"], observed["updated_at"]) == (sf.BASE_MTIME, sf.BASE_MTIME)


def test_entity_rows_come_from_the_event_payloads(canonical: sf.LegacyFixture, make_admin) -> None:
    admin = make_admin(canonical.data_dir)
    admin.rebuild()
    cat = admin.catalog

    assert {r["id"]: (r["name"], r["sport"]) for r in rows(cat, "tournaments")} == {
        8: ("LaLiga", "football"), 17: ("Premier League", "football"), 19: ("FA Cup", "football"),
        132: ("NBA", "basketball"), 2361: ("Wimbledon, Men", "tennis")}
    seasons = {r["id"]: r for r in rows(cat, "seasons")}
    assert seasons[sf.PL_2627.id]["tournament_id"] == 17 and seasons[sf.PL_2627.id]["sort_key"] == 2026.0
    assert all((r["listed"], r["position"]) == (0, None) for r in seasons.values())  # sezon listesi değil
    assert {r["slug"] for r in rows(cat, "sports")} == {"football", "basketball", "tennis"}

    row = event_row(cat, ARS)
    links = rows(cat, "event_participants", where=f"WHERE event_id = {ARS}")
    assert [(r["participant_id"], r["side"], r["start_ts"]) for r in links] == [
        (row["home_id"], 1, row["start_ts"]), (row["away_id"], 2, row["start_ts"])]
    home = rows(cat, "participants", where=f"WHERE id = {row['home_id']}")[0]
    assert (home["name"], home["name_folded"], home["sport"], home["updated_at"]) == (
        "Arsenal", "arsenal", "football", sf.BASE_MTIME)
    assert count(cat, "participants") == len({r[k] for r in rows(cat, "events") for k in ("home_id", "away_id")})


def test_newest_event_payload_wins_the_participant_row(tmp_path: Path, make_admin) -> None:
    data = tmp_path / "data"
    team = {"id": 42, "name": "Old Name", "slug": "old"}
    older = legacy_detail(data, sf.PL_ARS, homeTeam=team, mtime=sf.BASE_MTIME - 100)
    legacy_detail(data, sf.PL_LIV, homeTeam={**team, "name": "New Name"}, mtime=sf.BASE_MTIME)
    legacy_detail(data, sf.PL_LEE, awayTeam={**team, "name": "Oldest Name"}, mtime=sf.BASE_MTIME - 500)
    admin = make_admin(data)
    admin.rebuild()

    def name() -> Tuple[str, int]:
        found = rows(admin.catalog, "participants", where="WHERE id = 42")[0]
        return found["name"], found["updated_at"]

    assert name() == ("New Name", sf.BASE_MTIME)  # tarama sırasından bağımsız: en yeni yük
    # tek maçın yeniden dizinlenmesi de aynı kuralı uygular: eski yük yeni satırı ezmez
    assert admin.index_event(ARS) == "legacy" and name() == ("New Name", sf.BASE_MTIME)
    write(data, f"{older}/basic.json", {**sf.basic_payload(sf.PL_ARS), "homeTeam": {**team, "name": "Newest"}},
          mtime=sf.BASE_MTIME + 50)
    assert admin.index_event(ARS) == "legacy" and name() == ("Newest", sf.BASE_MTIME + 50)


# --- yeniden kurma: belirlenimlilik ve kipler ---------------------------------------------------------

def test_rebuilding_twice_gives_identical_rows(fx: sf.LegacyFixture, make_admin) -> None:
    admin = make_admin(fx.data_dir)
    first = admin.rebuild()
    before = snapshot(admin.catalog)
    second = admin.rebuild()

    assert (first.mode, first.reason, second.mode, second.reason) == ("recreate", "missing", "in_place", None)
    assert snapshot(admin.catalog) == before  # imza dahil
    third = admin.rebuild(mode="recreate")
    assert third.mode == "recreate" and snapshot(admin.catalog) == before
    for report in (second, third):
        assert (report.events, report.slices, report.counts, report.problems, report.superseded) == (
            first.events, first.slices, first.counts, first.problems, first.superseded)


def test_rebuild_does_not_depend_on_the_listing_order(old_forms: sf.LegacyFixture, make_admin,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    admin = make_admin(old_forms.data_dir)
    admin.rebuild()
    before = snapshot(admin.catalog)
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
    admin.rebuild()
    assert snapshot(admin.catalog) == before


def test_rebuild_meta_and_report(canonical: sf.LegacyFixture, make_admin) -> None:
    admin = make_admin(canonical.data_dir)
    stages: List[Tuple[str, int, int]] = []
    report = admin.rebuild(progress=lambda stage, done, total: stages.append((stage, done, total)))
    cat = admin.catalog

    assert cat.inspect().usable and cat.quick_check() == []
    assert cat.get_meta("built_at") == str(NOW) and cat.get_meta("build_mode") == "recreate"
    assert cat.get_meta("built_by") == indexer.APP_VERSION and cat.get_meta("derive_version") == "1"
    assert json.loads(cat.get_meta("counts")) == report.counts
    assert report.counts == {t: count(cat, t) for t in ALL_TABLES if t != "meta"}
    assert (report.events, report.events_legacy, report.events_v3) == (len(canonical.detail_ids),) * 2 + (0,)
    assert report.slices == count(cat, "event_slices") and report.completed and report.seconds > 0
    # sqlite_stat1: sorgu planlayıcısı için istatistik toplandı
    assert cat.connection().execute("SELECT count(*) FROM sqlite_stat1").fetchone()[0] > 0
    assert [s[0] for s in stages] == ["scan", "scan", "legacy_events", "finish", "finish"]
    assert stages[2] == ("legacy_events", report.events, report.events)
    with pytest.raises(ValueError):
        admin.rebuild(mode="sideways")


def test_progress_is_reported_in_steps(tmp_path: Path, make_admin, monkeypatch: pytest.MonkeyPatch) -> None:
    data = tmp_path / "data"
    for n in range(7):
        legacy_detail(data, sf.Ev(sf.PL_ARS.case, sf.PL, sf.PL_2627, "A", "B", eid=100 + n), slices=())
        write_v3(data, {**sf.basic_payload(sf.PL_LIV), "id": 200 + n})
    monkeypatch.setattr(indexer, "_PROGRESS_EVERY", 3)
    monkeypatch.setattr(indexer, "_BATCH_EVENTS", 2)  # toplu yazma sınırı satırları değiştirmez
    stages: List[Tuple[str, int, int]] = []
    admin = make_admin(data)
    report = admin.rebuild(progress=lambda *step: stages.append(step))

    assert [s for s in stages if s[0] == "v3_events"] == [("v3_events", 3, 7), ("v3_events", 6, 7), ("v3_events", 7, 7)]
    assert [s for s in stages if s[0] == "legacy_events"] == [
        ("legacy_events", 3, 7), ("legacy_events", 6, 7), ("legacy_events", 7, 7)]
    assert (report.events_v3, report.events_legacy, count(admin.catalog)) == (7, 7, 14)


def _foreign_file(path: str, statement: str) -> None:
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute(statement)
    finally:
        conn.close()


@pytest.mark.parametrize("damage, reason, mode", [
    (None, "missing", "recreate"),
    ("PRAGMA user_version = 7", "schema_version", "recreate"),
    ("PRAGMA application_id = 99", "application_id", "recreate"),
    ("garbage", "corrupt", "recreate"),
    ("empty", "missing", "recreate"),
    ("DELETE FROM meta WHERE key = 'derive_version'", "derive_version", "in_place"),
    ("UPDATE meta SET value = '99' WHERE key = 'derive_version'", "derive_version", "in_place"),
    ("usable", None, "in_place"),
])
def test_auto_mode_follows_the_state_of_the_file(canonical: sf.LegacyFixture, make_admin, damage: Optional[str],
                                                 reason: Optional[str], mode: str) -> None:
    admin = make_admin(canonical.data_dir)
    path = admin.catalog.path
    if damage is not None:
        admin.rebuild()
        expected = snapshot(admin.catalog)
        admin.catalog.close()
        if damage == "garbage":
            Path(path).write_bytes(b"this is not a database" * 50)
        elif damage == "empty":
            Path(path).write_bytes(b"")
        elif damage != "usable":
            _foreign_file(path, damage)
    assert admin.catalog.inspect().rebuild_reason == reason

    report = admin.ensure() if damage != "usable" else admin.rebuild()

    assert report is not None and (report.mode, report.reason, report.completed) == (mode, reason, True)
    assert admin.catalog.inspect().usable and admin.ensure() is None  # artık kullanılabilir: bir daha kurulmaz
    assert count(admin.catalog) == len(canonical.detail_ids)
    if damage is not None:
        assert snapshot(admin.catalog) == expected
    leftovers = sorted(p.name for p in Path(path).parent.iterdir() if ".build" in p.name)
    assert leftovers == []


def test_in_place_is_refused_for_a_file_with_another_schema(canonical: sf.LegacyFixture, make_admin) -> None:
    admin = make_admin(canonical.data_dir)
    assert admin.rebuild(mode="in_place").mode == "in_place"  # dosya yoktu: şema yaratılıp yerinde kurulur
    admin.catalog.close()
    _foreign_file(admin.catalog.path, "PRAGMA user_version = 7")

    with pytest.raises(StoreError, match="yerinde yeniden kurulamaz"):
        admin.rebuild(mode="in_place")
    assert admin.catalog.inspect().rebuild_reason == "schema_version"  # dosyaya dokunulmadı
    assert admin.rebuild().mode == "recreate" and count(admin.catalog) == len(canonical.detail_ids)


def test_recreate_replaces_stale_build_and_wal_files(canonical: sf.LegacyFixture, make_admin) -> None:
    admin = make_admin(canonical.data_dir)
    path = Path(admin.catalog.path)
    path.parent.mkdir(parents=True)
    for name in (path.name + ".build", path.name + ".build-wal", path.name + ".build-shm", path.name + "-wal",
                 path.name + "-shm"):
        (path.parent / name).write_bytes(b"left over from a crash")
    path.write_bytes(b"not a database either")

    report = admin.rebuild()

    assert (report.mode, report.reason) == ("recreate", "corrupt")
    assert count(admin.catalog) == len(canonical.detail_ids) and admin.catalog.quick_check() == []
    admin.catalog.close()  # son bağlantı kapanınca SQLite kendi WAL dosyasını siler
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


def test_failed_replace_keeps_the_old_catalog(canonical: sf.LegacyFixture, make_admin,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    admin = make_admin(canonical.data_dir)
    admin.rebuild()
    before = snapshot(admin.catalog)
    legacy_detail(canonical.data_dir, sf.PL_FUTURE)

    def busy(src: Any, dst: Any) -> None:  # Windows: catalog.db başka bir süreçte açık
        raise StoreError("Dosya yerine konamadı, başka bir süreçte açık", path=str(dst))

    monkeypatch.setattr(files, "replace", busy)
    with pytest.raises(StoreError, match="başka bir süreçte açık"):
        admin.rebuild(mode="recreate")

    assert snapshot(admin.catalog) == before
    assert [p.name for p in Path(admin.catalog.path).parent.iterdir() if ".build" in p.name] == []


def test_in_place_failure_on_a_corrupt_file_falls_back_to_recreate(canonical: sf.LegacyFixture, make_admin,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    admin = make_admin(canonical.data_dir)
    admin.rebuild()
    real_clear = Catalog.clear
    calls = {"n": 0}

    def clear(self: Catalog) -> None:  # başlığı sağlam, sayfaları bozuk dosya: silme sırasında ortaya çıkar
        calls["n"] += 1
        if calls["n"] == 1:
            raise CatalogCorrupt("Veritabanı okunamıyor (database disk image is malformed)", path=self.path)
        real_clear(self)

    monkeypatch.setattr(Catalog, "clear", clear)
    report = admin.rebuild()
    assert (report.mode, report.reason, report.completed) == ("recreate", "corrupt", True)
    assert count(admin.catalog) == len(canonical.detail_ids)

    calls["n"] = 0
    with pytest.raises(CatalogCorrupt):  # kip açıkça istenmişse hata çağırana çıkar
        admin.rebuild(mode="in_place")


@pytest.mark.skipif(sys.platform == "win32", reason="Windows'ta açık veritabanı dosyasının yerine yenisi konamaz")
def test_another_connection_follows_a_recreated_catalog(canonical: sf.LegacyFixture, make_admin,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    admin = make_admin(canonical.data_dir)
    admin.rebuild()
    with Catalog(admin.catalog.path) as other:  # başka bir sürecin açık tuttuğu katalog
        assert count(other) == len(canonical.detail_ids)
        legacy_detail(canonical.data_dir, sf.PL_FUTURE)
        admin.rebuild(mode="recreate")
        monkeypatch.setattr(catalog_mod, "REOPEN_CHECK_SECONDS", 0.0)
        assert count(other) == len(canonical.detail_ids) + 1 and other.quick_check() == []


def test_shared_catalog_stays_open_and_attached_state_db_is_not_analysed(canonical: sf.LegacyFixture) -> None:
    data = canonical.data_dir
    state_path = layout.resolve(data, layout.STATE_DB)
    os.makedirs(os.path.dirname(state_path))
    conn = sqlite3.connect(state_path)
    conn.execute("CREATE TABLE stream_events (seq INTEGER PRIMARY KEY, stream TEXT NOT NULL)")
    conn.execute("CREATE INDEX stream_events_stream ON stream_events(stream, seq)")
    conn.executemany("INSERT INTO stream_events (stream) VALUES (?)", [("live",)] * 50)
    conn.commit()
    conn.close()

    with Catalog(catalog_mod.catalog_path(data), attach={"state": state_path}) as shared:
        shared.prepare()  # Store cephesinin açtığı katalog: şema var, satırlar henüz kurulmadı
        admin = CatalogAdmin(data, shared, clock=lambda: float(NOW))
        report = admin.ensure()
        admin.close()  # paylaşılan kataloğu kapatmaz

        assert report is not None and (report.mode, report.reason) == ("in_place", "derive_version")
        connection = shared.connection()
        assert connection.execute("SELECT count(*) FROM main.sqlite_stat1").fetchone()[0] > 0
        # düz ANALYZE state.db'yi de çözümler ve akış okuma planını değiştirir: yalnızca katalog çözümlenir
        assert connection.execute(
            "SELECT count(*) FROM state.sqlite_master WHERE name = 'sqlite_stat1'").fetchone()[0] == 0
        assert count(shared) == len(canonical.detail_ids)
        assert admin.rebuild(mode="recreate").completed and count(shared) == len(canonical.detail_ids)
        assert shared.connection().execute("SELECT count(*) FROM state.stream_events").fetchone()[0] == 50


def test_rebuild_waits_for_the_write_lock(canonical: sf.LegacyFixture, make_admin) -> None:
    admin = make_admin(canonical.data_dir)
    admin.rebuild()
    before = snapshot(admin.catalog)
    with Catalog(admin.catalog.path) as writer, Catalog(admin.catalog.path, busy_timeout_ms=50) as impatient:
        with writer.write():  # başka bir süreç yazıyor: katalogun yazma kilidi onda
            with CatalogAdmin(canonical.data_dir, impatient) as blocked:
                with pytest.raises(StoreBusy):
                    blocked.rebuild(mode="in_place")
                with pytest.raises(StoreBusy):
                    blocked.index_event(ARS)
                assert blocked.verify(repair=True).ok  # onarılacak bir şey yok: yazma kilidi beklenmez
        assert CatalogAdmin(canonical.data_dir, impatient).rebuild(mode="in_place").completed
    assert snapshot(admin.catalog) == before


# --- yerinde kurma: eşzamanlı okuyucu ve geri alma ------------------------------------------------------

def test_in_place_rebuild_is_invisible_to_a_concurrent_reader_until_commit(canonical: sf.LegacyFixture,
                                                                           make_admin) -> None:
    clock = {"now": float(NOW)}
    admin = CatalogAdmin(canonical.data_dir, clock=lambda: clock["now"])
    admin.rebuild()
    old_events = rows(admin.catalog, "events")
    legacy_detail(canonical.data_dir, sf.PL_FUTURE)
    files.remove_tree(canonical.data_dir / PL_DIR / str(ARS))
    clock["now"] += 3600

    seen: List[Tuple[str, List[Dict[str, Any]], Optional[str]]] = []
    with Catalog(admin.catalog.path) as reader:  # ayrı bağlantı: başka bir süreçteki okuyucu gibi
        def look(stage: str, done: int, total: int) -> None:
            seen.append((stage, rows(reader, "events"), reader.get_meta("built_at")))

        with reader.read() as held:  # kurulumdan önce başlamış okuma işlemi
            assert held.execute("SELECT count(*) FROM events").fetchone()[0] == len(old_events)
            report = admin.rebuild(progress=look)
            # commit olduktan sonra bile kendi anlık görüntüsünü görür
            assert [r["id"] for r in held.execute("SELECT id FROM events ORDER BY id")] == [
                r["id"] for r in old_events]

        assert report.mode == "in_place" and [s[0] for s in seen][-1] == "finish"
        # kurulumun her anında (satırlar silinmiş, yenileri yazılmışken de) okuyucu eski kataloğu görür
        assert all(found == old_events and built == str(NOW) for _, found, built in seen)
        new_ids = [r["id"] for r in rows(reader, "events")]
        assert ARS not in new_ids and sf.event_id(sf.PL_FUTURE) in new_ids
        assert len(new_ids) == len(old_events) and reader.get_meta("built_at") == str(NOW + 3600)
    admin.close()


def test_failed_in_place_rebuild_rolls_back_to_the_old_catalog(canonical: sf.LegacyFixture, make_admin,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    admin = make_admin(canonical.data_dir)
    admin.rebuild()
    before = snapshot(admin.catalog)
    legacy_detail(canonical.data_dir, sf.PL_FUTURE)
    real = indexer.legacy_event_record
    calls = {"n": 0}

    def flaky(event: LegacyEvent) -> indexer.EventRecord:
        calls["n"] += 1
        if calls["n"] == 5:
            raise RuntimeError("disk gitti")
        return real(event)

    monkeypatch.setattr(indexer, "_BATCH_EVENTS", 2)  # hata anında satırların bir kısmı yazılmış olsun
    monkeypatch.setattr(indexer, "legacy_event_record", flaky)
    with pytest.raises(RuntimeError):
        admin.rebuild()

    assert snapshot(admin.catalog) == before and admin.catalog.inspect().usable


@pytest.mark.parametrize("mode", ["in_place", "recreate"])
def test_should_stop_leaves_the_old_catalog(canonical: sf.LegacyFixture, make_admin, mode: str) -> None:
    admin = make_admin(canonical.data_dir)
    admin.rebuild()
    before = snapshot(admin.catalog)
    legacy_detail(canonical.data_dir, sf.PL_FUTURE)
    asked = {"n": 0}

    def stop() -> bool:
        asked["n"] += 1
        return asked["n"] > 6

    report = admin.rebuild(should_stop=stop, mode=mode)

    assert (report.completed, report.mode) == (False, mode) and asked["n"] == 7
    assert snapshot(admin.catalog) == before
    assert [p.name for p in Path(admin.catalog.path).parent.iterdir() if ".build" in p.name] == []
    assert admin.rebuild(should_stop=lambda: False, mode=mode).completed
    assert count(admin.catalog) == len(canonical.detail_ids) + 1


# --- bozuk dosyalar kurulumu durdurmaz ------------------------------------------------------------------

def test_corrupt_payloads_are_reported_and_do_not_abort(old_forms: sf.LegacyFixture, make_admin) -> None:
    data = old_forms.data_dir
    write(data, "match_details/16867839/basic.json", b'{"id": 1686')  # yarım kalmış olay yükü
    write(data, f"{PL_DIR}/17099711/_slice_status.json", b"[not json")
    write(data, f"{PL_DIR}/17099711/_unavailable.json", {"lineups": "many"})
    write(data, f"{PL_DIR}/17099711/observation.json", b"\xff\xfe")
    legacy_detail(data, sf.PL_FUTURE, f"{PL_DIR}/999", slices=())  # yükteki id dizin adına eşit değil
    admin = make_admin(data)

    report = admin.rebuild()

    assert report.completed
    found = {(p.path, p.kind) for p in report.problems}
    assert found == {
        ("match_details/16867839", "corrupt"),
        (f"{PL_DIR}/17099711/_slice_status.json", "corrupt"),
        (f"{PL_DIR}/17099711/_unavailable.json", "malformed"),
        (f"{PL_DIR}/17099711/observation.json", "corrupt"),
        (f"{PL_DIR}/999", "id_mismatch"),
        (f"{PL_DIR}/17018572", "no_event_payload"),  # fixture: basic.json'sız dizin
        (f"{PL_DIR}/17185003/statistics.json", "corrupt"),  # fixture: yarıda kesilmiş dilim dosyası
    }
    assert all(p.layout == "legacy" and p.detail for p in report.problems)
    ids = [r["id"] for r in rows(admin.catalog, "events")]
    assert 16867839 not in ids and 999 not in ids and 17018572 not in ids
    assert sorted(ids) == sorted(set(old_forms.detail_ids) - {16867839, 17018572})

    # bozuk dilim dosyası: satırı error / corrupt olur, yeniden çekilecekler arasına girer
    broken = slice_rows(admin.catalog, 17185003)["statistics"]
    assert (broken["state"], broken["has_payload"], broken["error_reason"], broken["error_count"],
            broken["fetched_at"]) == ("error", 0, "corrupt", 1, None)
    assert all(s["state"] == "ok" for key, s in slice_rows(admin.catalog, 17185003).items() if key != "statistics")
    # yan dosyaları bozuk maç: gözlemsiz ve işaretsiz sayılır, satırı yine yazılır
    assert event_row(admin.catalog, 17099711)["observed_at"] is None
    assert admin.verify(deep=True).ok


def test_payload_that_derive_cannot_handle_is_reported_and_skipped(canonical: sf.LegacyFixture, make_admin,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    write_v3(canonical.data_dir, sf.basic_payload(sf.PL_FUTURE))
    real = derive.event_row
    broken = {ARS, sf.event_id(sf.PL_FUTURE)}

    def picky(payload: Any, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        if payload["id"] in broken:
            raise TypeError("beklenmeyen biçim")
        return real(payload, *args, **kwargs)

    monkeypatch.setattr(derive, "event_row", picky)
    admin = make_admin(canonical.data_dir)

    report = admin.rebuild()

    assert report.completed and report.events == len(canonical.detail_ids) - 1
    assert [(p.layout, p.path, p.kind, p.detail) for p in report.problems] == [
        ("v3", layout.event_dir(sf.event_id(sf.PL_FUTURE)), "malformed", "beklenmeyen biçim"),
        ("legacy", f"{PL_DIR}/{ARS}", "malformed", "beklenmeyen biçim")]
    assert event_row(admin.catalog, ARS) is None and event_row(admin.catalog, sf.event_id(sf.PL_FUTURE)) is None
    assert indexer.problem_of(StoreError("disk"), "x", "legacy").kind == "unreadable"


def test_unstorable_values_do_not_abort(tmp_path: Path, make_admin) -> None:
    data = tmp_path / "data"
    base = f"{PL_DIR}/{ARS}"
    event = sf.basic_payload(sf.PL_ARS)
    text = json.dumps(event).replace('"Arsenal"', '"Ars\\ud800enal"', 1)  # eşsiz vekil kod noktası
    assert text != json.dumps(event)
    write(data, f"{base}/basic.json", text.encode("ascii"))
    write(data, f"{base}/_unavailable.json", json.dumps({"lineups": 10 ** 30, "incidents": -5}).encode())
    write(data, f"{base}/_slice_status.json", {
        "h2h": {"error": {"reason": "5xx", "status": 10 ** 40, "count": 10 ** 25, "at": "not a time"}}})
    admin = make_admin(data)

    assert admin.rebuild().completed
    row = event_row(admin.catalog, ARS)
    assert row["home_name"] == "Ars?enal"
    marks = slice_rows(admin.catalog, ARS)
    assert marks["lineups"]["unverified_empty_count"] == 2 ** 63 - 1 and "incidents" not in marks
    assert (marks["h2h"]["error_status"], marks["h2h"]["error_count"], marks["h2h"]["error_at"]) == (
        2 ** 63 - 1, 2 ** 63 - 1, None)
    assert admin.verify(deep=True).ok  # doğrulama aynı satırları türetir


# --- v3 dizinleri ---------------------------------------------------------------------------------------

def test_v3_event_rows_come_from_manifest_and_event_payload(tmp_path: Path, make_admin) -> None:
    data = tmp_path / "data"
    payload = sf.basic_payload(sf.PL_ARS)
    observed = dt.datetime(2026, 9, 19, 6, 0, tzinfo=UTC)
    at = dt.datetime(2026, 9, 20, 0, 0, 30, 750000, tzinfo=UTC)
    rel = write_v3(
        data, payload,
        slices={"statistics": sf.slice_payload("statistics", payload), "odds_all/1": {"markets": [1]}},
        entries={
            "pregame_form": SliceEntry(state="empty", checked_at=at,
                                       empty=EmptyMark(count=2, unverified=1, reason="404", at=at)),
            "lineups": SliceEntry(state="error", checked_at=at, empty=EmptyMark(count=1),
                                  error=ErrorMark(reason="429", status=429, at=at, count=3)),
            "schedule/round_3_final": SliceEntry(state="empty", meta={"complete": True, "b": [1]}),
        },
        observation=Observation(observed_at=observed, change_ts=123, status_regressed=True),
    )
    # geçmiş sayacı ve meta manifestten gelir
    path = layout.resolve(data, layout.manifest_path(rel))
    found = manifest.read_manifest(path)
    found.slices["odds_all/1"].history = HistoryMark(count=12, last_sha256="a" * 64)
    found.slices["odds_all/1"].meta = {"z": 1, "a": "ğ"}
    manifest.write_manifest(path, found)

    admin = make_admin(data)
    report = admin.rebuild()

    assert (report.events, report.events_v3, report.events_legacy, report.problems) == (1, 1, 0, [])
    row = event_row(admin.catalog, ARS)
    assert {n: row[n] for n in derive.EVENT_DERIVED_COLUMNS} == derive.event_row(payload, "event", observed)
    assert (row["layout"], row["path"], row["legacy_path"], row["status_regressed"]) == ("v3", None, None, 1)
    assert (row["first_seen_at"], row["updated_at"]) == (int(T0.timestamp()), int(T1.timestamp()))
    assert row["sig"] == indexer.file_signature(path) and row["sig"].endswith(f":{os.path.getsize(path)}")

    slices = slice_rows(admin.catalog, ARS)
    assert set(slices) == {"event", "statistics", "odds_all/1", "pregame_form", "lineups", "schedule/round_3_final"}
    entry = found.slices["statistics"]
    assert slices["statistics"] == {
        "event_id": ARS, "key": "statistics", "sub": "", "state": "ok", "has_payload": 1,
        "fetched_at": int(T1.timestamp()), "checked_at": int(T1.timestamp()), "empty_count": 0,
        "unverified_empty_count": 0, "error_reason": None, "error_status": None, "error_at": None,
        "error_count": 0, "stored_bytes": entry.stored_bytes, "raw_bytes": entry.raw_bytes, "history_count": 0,
        "meta_json": None}
    odds = slices["odds_all/1"]
    assert (odds["key"], odds["sub"], odds["history_count"], odds["meta_json"]) == (
        "odds_all", "1", 12, '{"a":"ğ","z":1}')
    form = slices["pregame_form"]
    assert (form["state"], form["has_payload"], form["empty_count"], form["unverified_empty_count"],
            form["checked_at"], form["fetched_at"], form["stored_bytes"]) == (
        "empty", 0, 2, 1, int(at.timestamp()), None, None)
    lineups = slices["lineups"]
    assert (lineups["state"], lineups["empty_count"], lineups["error_reason"], lineups["error_status"],
            lineups["error_at"], lineups["error_count"]) == ("error", 1, "429", 429, int(at.timestamp()), 3)
    assert slices["schedule/round_3_final"]["meta_json"] == '{"b":[1],"complete":true}'
    assert rows(admin.catalog, "participants")[0]["updated_at"] == int(T1.timestamp())
    assert admin.verify(deep=True).ok and admin.verify().events_read == 0


def test_event_in_both_layouts_resolves_to_v3_with_legacy_path(old_forms: sf.LegacyFixture, make_admin) -> None:
    data = old_forms.data_dir
    payload = sf.basic_payload(sf.PL_ARS)
    payload["homeScore"] = {**payload["homeScore"], "current": 9, "display": 9}  # v3 kopyası daha yeni
    rel = write_v3(data, payload, slices={"statistics": {"statistics": [{"period": "ALL", "groups": [1]}]}})
    admin = make_admin(data)

    report = admin.rebuild()

    row = event_row(admin.catalog, ARS)
    winner = f"{PL_DIR}/{ARS}"  # eski kopyalar arasında geçerli olan: lig dizinindeki, daha yeni basic.json
    assert (row["layout"], row["path"], row["legacy_path"], row["home_score"]) == ("v3", None, winner, 9)
    assert set(slice_rows(admin.catalog, ARS)) == {"event", "statistics"}  # eski dizinin dilimleri karışmaz
    assert slice_rows(admin.catalog, ARS)["statistics"]["raw_bytes"] is not None
    assert [s for s in report.superseded if s.event_id == ARS] == [
        SupersededDir(ARS, winner, rel, "v3"), SupersededDir(ARS, f"match_details/{ARS}", rel, "v3")]
    assert (report.events_v3, report.events_legacy) == (1, len(old_forms.detail_ids) - 2)
    assert count(admin.catalog) == len(old_forms.detail_ids) - 1  # maç bir kez dizinlenir
    # öteki maçlar eski düzende kalır
    assert event_row(admin.catalog, 17185003)["layout"] == "legacy"
    assert admin.catalog.connection().execute(
        "SELECT count(*) FROM events WHERE layout = 'legacy' OR legacy_path IS NOT NULL").fetchone()[0] == count(
        admin.catalog)
    assert admin.verify().ok and admin.verify(deep=True).ok


def test_promoted_events_keep_the_facts_of_the_legacy_rows(fx: sf.LegacyFixture, make_admin) -> None:
    """Her maçın v3 kopyası kurulunca katalog aynı şeyi söyler: yalnızca depolama sütunları değişir."""
    admin = make_admin(fx.data_dir)
    admin.rebuild()
    legacy_events = {r["id"]: r for r in rows(admin.catalog, "events")}
    legacy_slices = {i: slice_rows(admin.catalog, i) for i in legacy_events}
    for event in LegacyReader(fx.data_dir).iter_events(payloads=True):
        promote(fx.data_dir, event)

    report = admin.rebuild()

    assert (report.events_v3, report.events_legacy) == (len(legacy_events), 0)
    facts = ("state", "has_payload", "empty_count", "unverified_empty_count", "error_reason", "error_status",
             "error_at", "error_count", "fetched_at")
    for event_id, old in legacy_events.items():
        new = event_row(admin.catalog, event_id)
        for name in (*derive.EVENT_DERIVED_COLUMNS, "status_regressed", "stale", "listed_in"):
            assert new[name] == old[name], (event_id, name)
        assert (new["layout"], new["path"], new["legacy_path"]) == ("v3", None, old["path"])
        slices = slice_rows(admin.catalog, event_id)
        assert set(slices) == set(legacy_slices[event_id])
        for key, entry in slices.items():
            assert {n: entry[n] for n in facts} == {n: legacy_slices[event_id][key][n] for n in facts}, (event_id, key)
    assert admin.verify(deep=True).ok


def test_unreadable_v3_copy_falls_back_to_the_legacy_copy(canonical: sf.LegacyFixture, make_admin) -> None:
    data = canonical.data_dir
    events = {e.event_id: e for e in LegacyReader(data).iter_events(payloads=True)}
    ids = sorted(events)[:6]
    for event_id in ids:
        promote(data, events[event_id])
    v3_file(data, ids[0], "manifest.json").write_bytes(b"{ not json")
    v3_file(data, ids[1], "event.json.gz").write_bytes(b"\x1f\x8b broken gzip")
    v3_file(data, ids[2], "event.json.gz").unlink()
    # manifest başka bir maçın
    other = manifest.read_manifest(v3_file(data, ids[4], "manifest.json"))
    manifest.write_manifest(v3_file(data, ids[3], "manifest.json"), other)
    # bu sürümün bilmediği manifest biçimi
    raw = json.loads(v3_file(data, ids[5], "manifest.json").read_text(encoding="utf-8"))
    v3_file(data, ids[5], "manifest.json").write_text(json.dumps({**raw, "format": 2}), encoding="utf-8")
    admin = make_admin(data)

    report = admin.rebuild()

    kinds = {p.path: p.kind for p in report.problems if p.layout == "v3"}
    assert kinds == {
        layout.event_dir(ids[0]): "corrupt", layout.event_dir(ids[1]): "corrupt",
        layout.event_dir(ids[2]): "no_event_payload", layout.event_dir(ids[3]): "id_mismatch",
        layout.event_dir(ids[5]): "schema_too_new"}
    for event_id in (ids[0], ids[1], ids[2], ids[3], ids[5]):
        row = event_row(admin.catalog, event_id)
        assert (row["layout"], row["path"], row["legacy_path"]) == ("legacy", events[event_id].path, None)
    assert event_row(admin.catalog, ids[4])["layout"] == "v3"
    assert (report.events_v3, report.events_legacy) == (1, len(events) - 1)
    assert not [s for s in report.superseded if s.event_id != ids[4]]
    assert admin.verify().ok and admin.verify(deep=False).problems  # sorunlar doğrulamada da bildirilir
    # deep: manifesti okunamayan dizinde karşılaştırılacak bir şey yok; manifesti okunan dizinlerin yükleri denetlenir
    deep = admin.verify(deep=True)
    assert {i.event_id for i in deep.issues if i.invariant == "I2"} == {ids[1], ids[2], ids[3]}
    # başka maçın manifesti: bu dizindeki öteki dosyaların adını vermiyor
    assert {i.event_id for i in deep.issues if i.invariant != "I2"} <= {ids[3]}
    assert {i.invariant for i in deep.issues} <= {"I2", "I9"}


def test_v3_event_without_a_usable_copy_is_not_indexed(tmp_path: Path, make_admin) -> None:
    data = tmp_path / "data"
    payload = sf.basic_payload(sf.PL_ARS)
    write_v3(data, payload)
    write_v3(data, {**sf.basic_payload(sf.PL_LIV)}, entries={
        "event": SliceEntry(state="error", error=ErrorMark(reason="corrupt"))})  # olay dilimi ok değil
    wrong = write_v3(data, {**payload, "id": 5})
    codec.write_payload(layout.resolve(data, layout.slice_path(wrong, "event")), payload)  # yükteki id başka
    # kurallı yerinde olmayan dizin, rakam olmayan ad, gizli dosya
    stray = Path(layout.resolve(data, "v3/events/0/001/2000"))
    stray.mkdir(parents=True)
    (stray / "manifest.json").write_text("{}", encoding="utf-8")
    Path(layout.resolve(data, "v3/events/notes.txt")).write_text("x", encoding="utf-8")
    Path(layout.resolve(data, "v3/events/.DS_Store")).write_text("x", encoding="utf-8")
    admin = make_admin(data)

    report = admin.rebuild()

    assert [r["id"] for r in rows(admin.catalog, "events")] == [ARS]
    assert {(p.path, p.kind) for p in report.problems} == {
        (layout.event_dir(sf.event_id(sf.PL_LIV)), "no_event_payload"), (wrong, "id_mismatch"),
        ("v3/events/0/001/2000", "unknown_name"), ("v3/events/notes.txt", "unknown_name")}
    assert indexer.scan_v3_events(data) == [(5, wrong), (ARS, layout.event_dir(ARS)),
                                            (sf.event_id(sf.PL_LIV), layout.event_dir(sf.event_id(sf.PL_LIV)))]


# --- tek maçın yeniden dizinlenmesi ---------------------------------------------------------------------

def test_index_event_equals_a_rebuild(canonical: sf.LegacyFixture, make_admin) -> None:
    data = canonical.data_dir
    admin = make_admin(data)
    admin.rebuild()
    base = f"{PL_DIR}/17018572"  # statistics eksik, hata işareti var
    write(data, f"{base}/statistics.json", sf.slice_payload("statistics", sf.basic_payload(sf.PL_NEW)),
          mtime=sf.BASE_MTIME + 60)
    (data / base / "_slice_status.json").unlink()
    changed = sf.basic_payload(sf.PL_NEW)
    changed["startTimestamp"] += 3600  # event_participants anahtarı değişir: eski satır kalmamalı
    write(data, f"{base}/basic.json", changed, mtime=sf.BASE_MTIME + 60)
    write(data, f"{base}/observation.json", sf.observation_payload(changed, "2026-10-01T10:00:00+00:00"))

    assert admin.index_event(17018572) == "legacy"

    incremental = snapshot(admin.catalog)
    admin.rebuild()
    assert incremental == snapshot(admin.catalog)
    row = event_row(admin.catalog, 17018572)
    assert row["start_ts"] == changed["startTimestamp"] and row["updated_at"] == sf.BASE_MTIME + 60
    assert slice_rows(admin.catalog, 17018572)["statistics"]["state"] == "ok"
    assert count(admin.catalog, "event_participants") == 2 * count(admin.catalog)


def test_index_event_follows_the_event_between_layouts(canonical: sf.LegacyFixture, make_admin) -> None:
    data = canonical.data_dir
    admin = make_admin(data)
    admin.rebuild()
    legacy_path = event_row(admin.catalog, ARS)["path"]
    event = next(e for e in LegacyReader(data).iter_events(payloads=True) if e.event_id == ARS)

    promote(data, event)
    assert admin.index_event(ARS) == "v3"
    row = event_row(admin.catalog, ARS)
    assert (row["layout"], row["path"], row["legacy_path"]) == ("v3", None, legacy_path)

    files.remove_tree(data / legacy_path)  # taşıma eski dizini sildi
    assert admin.index_event(ARS) == "v3" and event_row(admin.catalog, ARS)["legacy_path"] is None

    files.remove_tree(layout.resolve(data, layout.event_dir(ARS)))
    before = count(admin.catalog)
    assert admin.index_event(ARS) is None and event_row(admin.catalog, ARS) is None
    assert count(admin.catalog) == before - 1 and slice_rows(admin.catalog, ARS) == {}
    assert admin.index_event(ARS) is None  # olmayan maç: yapılacak bir şey yok
    assert admin.verify(deep=True).ok

    # katalogda olmayan bir eski düzen maçı: yazıcı dizini söyler (başka maçın dizini sayılmaz)
    new_dir = legacy_detail(data, sf.PL_FUTURE)
    future = sf.event_id(sf.PL_FUTURE)
    assert admin.index_event(future) is None and admin.index_event(future, paths=[f"{PL_DIR}/17099711"]) is None
    assert admin.index_event(future, paths=[new_dir + "/", "match_details/nowhere/else/1"]) == "legacy"
    assert event_row(admin.catalog, future)["path"] == new_dir and admin.verify().ok
    files.remove_tree(data / new_dir)
    assert admin.index_event(future) is None and event_row(admin.catalog, future) is None

    # katalogda olmayan bir v3 maçı kimliğinden bulunur
    write_v3(data, sf.basic_payload(sf.PL_FUTURE))
    assert admin.index_event(future) == "v3"
    problems: List[IndexProblem] = []
    v3_file(data, future, "event.json.gz").write_bytes(b"")
    assert admin.index_event(future, problems=problems) is None
    assert [(p.layout, p.kind) for p in problems] == [("v3", "corrupt")]


# --- doğrulama -------------------------------------------------------------------------------------------

def test_verify_is_clean_after_a_rebuild_and_reads_nothing(fx: sf.LegacyFixture, make_admin,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    admin = make_admin(fx.data_dir)
    built = admin.rebuild()
    deep = admin.verify(deep=True)
    assert deep.ok and deep.issues == [] and deep.checked == verify.DEEP_CHECKS
    assert (deep.events, deep.events_read) == (built.events, built.events)
    assert deep.problems == built.problems and deep.superseded == built.superseded

    def no_reads(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("hızlı doğrulama yük dosyası okumamalı")

    monkeypatch.setattr(codec, "read_payload", no_reads)
    monkeypatch.setattr(files, "read_bytes", no_reads)
    state = tree_state(fx.data_dir, skip=(".meta",))  # .meta: SQLite'ın kendi WAL / paylaşılan bellek dosyaları
    quick = admin.verify()
    assert quick.ok and quick.checked == verify.QUICK_CHECKS and not quick.repair
    assert (quick.events, quick.events_read, quick.superseded) == (built.events, 0, built.superseded)
    assert tree_state(fx.data_dir, skip=(".meta",)) == state  # doğrulama veri dosyalarına dokunmaz


def test_verify_without_a_usable_catalog(canonical: sf.LegacyFixture, make_admin) -> None:
    admin = make_admin(canonical.data_dir)
    state = tree_state(canonical.data_dir)

    report = admin.verify(repair=True)
    assert issues(report) == [("catalog", "missing", None)] and not report.ok and report.events == 0
    assert report.checked == ("quick_check",) and report.issues[0].path == ".meta/catalog.db"
    assert tree_state(canonical.data_dir) == state  # katalog da yaratılmaz

    admin.rebuild()
    admin.catalog.close()
    _foreign_file(admin.catalog.path, "DELETE FROM meta WHERE key = 'derive_version'")
    assert issues(admin.verify()) == [("catalog", "derive_version", None)]
    Path(admin.catalog.path).write_bytes(b"garbage" * 100)
    assert issues(admin.verify(deep=True)) == [("catalog", "corrupt", None)]


def test_verify_runs_quick_check_on_the_state_db(canonical: sf.LegacyFixture, make_admin) -> None:
    admin = make_admin(canonical.data_dir)
    admin.rebuild()
    state_db = Path(layout.resolve(canonical.data_dir, layout.STATE_DB))
    assert verify.quick_check_file(str(state_db)) is None and not state_db.exists()  # yoksa yaratılmaz

    conn = sqlite3.connect(state_db)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    assert verify.quick_check_file(str(state_db)) == [] and admin.verify().ok

    state_db.write_bytes(b"SQLite format 3\x00" + b"\x01" * 4000)
    report = admin.verify()
    assert [(i.invariant, i.kind, i.path) for i in report.issues] == [("state", "quick_check", ".meta/state.db")]
    assert not report.ok and report.events == len(canonical.detail_ids)  # katalog yine de denetlenir


def test_quick_verify_finds_what_changed_behind_the_catalog(canonical: sf.LegacyFixture, make_admin) -> None:
    data = canonical.data_dir
    admin = make_admin(data)
    admin.rebuild()
    clean = snapshot(admin.catalog, sig=False)

    new_dir = legacy_detail(data, sf.PL_FUTURE)  # I3: dizin var, satır yok
    files.remove_tree(data / PL_DIR / str(ARS))  # I1: satır var, dizin yok
    base = f"{PL_DIR}/17018572"
    write(data, f"{base}/statistics.json", sf.slice_payload("statistics", sf.basic_payload(sf.PL_NEW)))  # I3: yeni dilim
    edited = sf.basic_payload(sf.PL_LIV)
    edited["homeScore"]["current"] = 7
    write(data, f"{PL_DIR}/17099711/basic.json", edited)  # I4: olay yükü değişti
    (data / PL_DIR / "16951514" / "statistics.json").unlink()  # I2: katalog yükü var diyor
    bump(data / PL_DIR / "16951514")
    untouched = data / PL_DIR / "17184988"
    bump(untouched)  # yalnızca imza eskidi: tutarsızlık değil

    report = admin.verify()

    assert sorted(issues(report)) == sorted([
        ("I3", "unindexed", sf.event_id(sf.PL_FUTURE)), ("I1", "no_event_directory", ARS),
        ("I3", "slices", 17018572), ("I4", "event_row", 17099711), ("I2", "slices", 16951514)])
    by_id = {i.event_id: i for i in report.issues}
    assert by_id[sf.event_id(sf.PL_FUTURE)].path == new_dir and by_id[ARS].path == f"{PL_DIR}/{ARS}"
    assert "statistics: state" in by_id[17018572].detail and "has_payload" in by_id[17018572].detail
    assert by_id[17099711].detail == "farklı sütunlar: home_score_current"
    assert by_id[16951514].detail == "statistics: dosyalarda yok"
    assert report.events_read == 6 and not report.ok and len(report.open_issues) == 5
    assert snapshot(admin.catalog, sig=False) == clean  # onarım istenmedi: katalog değişmedi

    repaired = admin.verify(repair=True)
    assert sorted(issues(repaired)) == sorted(issues(report))
    assert repaired.ok and all(i.repaired for i in repaired.issues)
    after = admin.verify()
    assert after.ok and after.issues == [] and after.events_read == 0  # eskimiş imza da tazelendi
    assert admin.verify(deep=True).ok
    repaired_rows = snapshot(admin.catalog)
    admin.rebuild()
    assert repaired_rows == snapshot(admin.catalog)  # onarım, yeniden kurmanın yazacağını yazar


def test_quick_verify_sees_layout_changes(canonical: sf.LegacyFixture, make_admin) -> None:
    data = canonical.data_dir
    admin = make_admin(data)
    admin.rebuild()
    events = {e.event_id: e for e in LegacyReader(data).iter_events(payloads=True)}
    promote(data, events[ARS])  # I5: katalog legacy diyor, v3 kopyası var
    assert issues(admin.verify()) == [("I5", "layout", ARS)]
    assert admin.verify(repair=True).ok and event_row(admin.catalog, ARS)["layout"] == "v3"

    files.remove_tree(layout.resolve(data, layout.event_dir(ARS)))  # I5: katalog v3 diyor, yalnızca eski dizin var
    report = admin.verify()
    assert issues(report) == [("I5", "layout", ARS)] and "'v3'" in report.issues[0].detail
    assert admin.verify(repair=True).ok
    row = event_row(admin.catalog, ARS)
    assert (row["layout"], row["path"], row["legacy_path"]) == ("legacy", events[ARS].path, None)

    # v3 satırının eski dizini silindi: legacy_path eskidi (satır farkı olarak bildirilir)
    promote(data, events[17099711])
    admin.index_event(17099711)
    files.remove_tree(data / events[17099711].path)
    report = admin.verify()
    assert issues(report) == [("I4", "event_row", 17099711)] and report.issues[0].detail.endswith("legacy_path")
    assert admin.verify(repair=True).ok and admin.verify().ok


def test_newer_duplicate_is_found_by_the_quick_check(old_forms: sf.LegacyFixture, make_admin) -> None:
    data = old_forms.data_dir
    admin = make_admin(data)
    admin.rebuild()
    assert event_row(admin.catalog, ARS)["path"] == f"{PL_DIR}/{ARS}"
    # düz dizindeki kopya yenilendi: artık geçerli olan o
    newer = sf.basic_payload(sf.PL_ARS)
    write(data, f"match_details/{ARS}/basic.json", newer, mtime=sf.BASE_MTIME + 100)

    report = admin.verify()
    # satır başka dizini gösteriyor; yeni geçerli kopyada eski dizinin dilimleri yok
    assert issues(report) == [("I4", "event_row", ARS), ("I2", "slices", ARS)]
    assert "path" in report.issues[0].detail and "statistics: dosyalarda yok" in report.issues[1].detail
    assert admin.verify(repair=True).ok
    assert event_row(admin.catalog, ARS)["path"] == f"match_details/{ARS}"
    assert set(slice_rows(admin.catalog, ARS)) == {"event"}
    after = admin.verify()
    assert after.ok and after.superseded == [SupersededDir(ARS, f"{PL_DIR}/{ARS}", f"match_details/{ARS}", "legacy")]

    # en yeni kopya okunamıyorsa sıradaki dizinlenir; hızlı doğrulama onu her seferinde yeniden okur
    write(data, f"match_details/{ARS}/basic.json", b"{", mtime=sf.BASE_MTIME + 200)
    assert admin.verify(repair=True).ok and event_row(admin.catalog, ARS)["path"] == f"{PL_DIR}/{ARS}"
    again = admin.verify()
    assert again.ok and again.events_read == 1
    assert [(p.path, p.kind) for p in again.problems if str(ARS) in p.path] == [(f"match_details/{ARS}", "corrupt")]


def test_deep_verify_finds_what_signatures_cannot(canonical: sf.LegacyFixture, make_admin) -> None:
    data = canonical.data_dir
    admin = make_admin(data)
    admin.rebuild()
    with admin.catalog.write() as conn:  # katalog kendi kendine bozuldu: dosyalar ve imzalar aynı
        conn.execute("UPDATE events SET home_score = 99, status_class = 'void' WHERE id = ?", (ARS,))
        conn.execute("UPDATE event_slices SET empty_count = 5 WHERE event_id = 16867839 AND key = 'h2h'")
        conn.execute("DELETE FROM event_participants WHERE event_id = 17099711 AND side = 2")
    # dosya yerinde değiştirildi ve dizinin mtime'ı aynı kaldı (os.replace kullanmayan bir düzenleme)
    target = data / PL_DIR / "17184988" / "lineups.json"
    directory = target.parent.stat()
    target.write_bytes(b"{ truncated")
    os.utime(target, (sf.BASE_MTIME, sf.BASE_MTIME))
    os.utime(target.parent, ns=(directory.st_atime_ns, directory.st_mtime_ns))

    assert admin.verify().ok  # hızlı kip imzalara bakar
    report = admin.verify(deep=True)
    assert sorted(issues(report)) == [
        ("I2", "slices", 17184988), ("I3", "slices", 16867839), ("I4", "event_participants", 17099711),
        ("I4", "event_row", ARS)]
    assert {i.event_id: i.detail for i in report.issues}[ARS] == "farklı sütunlar: status_class, home_score"
    assert [(p.path, p.kind) for p in report.problems] == [(f"{PL_DIR}/17184988/lineups.json", "corrupt")]

    assert admin.verify(deep=True, repair=True).ok
    assert admin.verify(deep=True).ok
    broken = slice_rows(admin.catalog, 17184988)["lineups"]
    assert (broken["state"], broken["has_payload"], broken["error_reason"]) == ("error", 0, "corrupt")
    assert target.read_bytes() == b"{ truncated"  # eski düzen dosyalarına dokunulmaz


def _v3_tree(data: Path) -> Dict[int, LegacyEvent]:
    events = {e.event_id: e for e in LegacyReader(data).iter_events(payloads=True)}
    for event in events.values():
        promote(data, event)
    return events


def test_deep_verify_checks_v3_payloads_against_the_manifest(canonical: sf.LegacyFixture, make_admin) -> None:
    data = canonical.data_dir
    events = _v3_tree(data)
    ids = sorted(events)
    admin = make_admin(data)
    admin.rebuild()
    assert admin.verify(deep=True).ok

    missing, corrupt, swapped, resized, bad_event = ids[0], ids[1], ids[2], ids[3], ids[4]
    v3_file(data, missing, "statistics.json.gz").unlink()
    v3_file(data, corrupt, "statistics.json.gz").write_bytes(b"\x1f\x8b\x08 not gzip at all")
    v3_file(data, swapped, "h2h.json.gz").write_bytes(codec.compress(codec.canonical_bytes({"other": 1})))
    # aynı içerik, başka baytlar (sona boş bir gzip üyesi): sha256 tutar, dosya boyutu tutmaz
    stored = v3_file(data, resized, "h2h.json.gz").read_bytes()
    v3_file(data, resized, "h2h.json.gz").write_bytes(stored + gzip.compress(b"", mtime=0))
    v3_file(data, bad_event, "event.json.gz").write_bytes(codec.compress(b"[1, 2"))
    # manifestin adını vermediği dosyalar ve yarım kalmış geçici dosya
    v3_file(data, ids[5], "notes.txt").write_text("x", encoding="utf-8")
    (v3_file(data, ids[5], "odds_all")).mkdir()
    v3_file(data, ids[5], "odds_all/1.json.gz").write_bytes(codec.compress(b"{}"))
    v3_file(data, ids[5], ".manifest.json.abc123.tmp").write_bytes(b"half")
    history = v3_file(data, ids[5], "_history/odds_all")
    history.mkdir(parents=True)
    (history / "_.jsonl.gz").write_bytes(b"")  # geçmiş dosyaları bu denetimin konusu değil
    manifests = {i: v3_file(data, i, "manifest.json").read_bytes() for i in ids}

    assert admin.verify().ok  # manifestler değişmedi: hızlı kip bunları görmez
    report = admin.verify(deep=True)

    found = {(i.invariant, i.kind, i.event_id, i.path.rsplit("/", 1)[-1], i.detail.split(":")[0].split(" (")[0])
             for i in report.issues}
    expected = {
        ("I2", "payload", missing, "statistics.json.gz", "statistics"),
        ("I2", "payload", corrupt, "statistics.json.gz", "statistics"),
        ("I2", "payload", swapped, "h2h.json.gz", "h2h"),
        ("I2", "payload", resized, "h2h.json.gz", "h2h"),
        ("I2", "payload", bad_event, "event.json.gz", "event"),
        # olay yükü okunamayan v3 dizini geçerli bir maç değil: diskte geçerli olan eski kopya
        ("I5", "layout", bad_event, events[bad_event].path.rsplit("/", 1)[-1], "katalog 'v3' diyor, diskte geçerli olan 'legacy'"),
        ("I9", "unknown_file", ids[5], "notes.txt", "manifestin adını vermediği dosya"),
        ("I9", "unknown_file", ids[5], "1.json.gz", "manifestin adını vermediği dosya"),
    }
    assert found == expected
    details = {(i.event_id, i.kind): i.detail for i in report.issues}
    assert details[(missing, "payload")] == "statistics: dosya yok"
    assert details[(corrupt, "payload")].startswith("statistics: açılamıyor")
    assert details[(swapped, "payload")] == "h2h: sha256 manifesttekinden farklı"
    assert details[(resized, "payload")].startswith("h2h: boyut manifesttekinden farklı (dosya ")
    assert details[(bad_event, "payload")].startswith("event: JSON değil")
    assert report.leftovers == [f"{layout.event_dir(ids[5])}/.manifest.json.abc123.tmp"]
    assert report.leftovers_removed == 0 and v3_file(data, ids[5], ".manifest.json.abc123.tmp").exists()
    assert {i: v3_file(data, i, "manifest.json").read_bytes() for i in ids} == manifests  # yalnızca okundu

    repaired = admin.verify(deep=True, repair=True)

    assert [i for i in repaired.open_issues if i.invariant != "I9"] == []
    assert repaired.leftovers_removed == 1 and not v3_file(data, ids[5], ".manifest.json.abc123.tmp").exists()
    # okunamayan yükler manifestte error / corrupt oldu; dosyalar silinmedi
    for event_id, name in ((missing, "statistics"), (corrupt, "statistics"), (swapped, "h2h"), (resized, "h2h")):
        entry = manifest.read_manifest(v3_file(data, event_id, "manifest.json")).slices[name]
        assert (entry.state, entry.has_payload, entry.error.reason, entry.error.count, entry.fetched_at) == (
            "error", False, "corrupt", 1, None)
        assert entry.error.at == entry.checked_at == dt.datetime.fromtimestamp(NOW, UTC)
        row = slice_rows(admin.catalog, event_id)[name]
        assert (row["state"], row["has_payload"], row["error_reason"], row["error_at"]) == (
            "error", 0, "corrupt", NOW)
    assert v3_file(data, corrupt, "statistics.json.gz").exists() and v3_file(data, swapped, "h2h.json.gz").exists()
    assert event_row(admin.catalog, missing)["updated_at"] == NOW
    # olay yükü okunamayan v3 dizini artık geçerli bir maç değil: eski kopyası dizinlenir
    row = event_row(admin.catalog, bad_event)
    assert (row["layout"], row["path"]) == ("legacy", events[bad_event].path)
    assert manifest.read_manifest(v3_file(data, bad_event, "manifest.json")).slices["event"].state == "error"
    # dokunulmayan dilimler ve manifestler aynen duruyor
    assert v3_file(data, ids[6], "manifest.json").read_bytes() == manifests[ids[6]]

    again = admin.verify(deep=True)
    assert sorted(issues(again)) == [("I9", "unknown_file", ids[5]), ("I9", "unknown_file", ids[5])]
    assert again.leftovers == [] and admin.verify().ok
    assert [p.kind for p in again.problems if p.layout == "v3"] == ["no_event_payload"]


def test_v3_event_that_lost_its_payload_is_removed_by_repair(tmp_path: Path, make_admin) -> None:
    data = tmp_path / "data"
    payload = sf.basic_payload(sf.PL_ARS)
    write_v3(data, payload, slices={"statistics": sf.slice_payload("statistics", payload)})
    write_v3(data, sf.basic_payload(sf.PL_LIV))
    admin = make_admin(data)
    admin.rebuild()

    assert verify.mark_corrupt(admin, ARS, ["statistics", "lineups"]) == []  # sağlam ya da olmayan dilim: dokunulmaz
    before = v3_file(data, ARS, "manifest.json").read_bytes()
    v3_file(data, ARS, "event.json.gz").unlink()
    assert admin.verify().ok  # manifest aynı: hızlı kip görmez

    report = admin.verify(deep=True)
    assert issues(report) == [("I2", "payload", ARS), ("I1", "no_event_directory", ARS)]
    assert "no_event_payload" in report.issues[1].detail and report.issues[1].path == layout.event_dir(ARS)
    assert v3_file(data, ARS, "manifest.json").read_bytes() == before

    assert admin.verify(deep=True, repair=True).ok
    assert [r["id"] for r in rows(admin.catalog, "events")] == [sf.event_id(sf.PL_LIV)]
    assert slice_rows(admin.catalog, ARS) == {} and count(admin.catalog, "event_participants") == 2
    found = manifest.read_manifest(v3_file(data, ARS, "manifest.json"))
    assert (found.slices["event"].state, found.slices["event"].error.reason) == ("error", "corrupt")
    assert found.slices["statistics"].state == "ok" and v3_file(data, ARS, "statistics.json.gz").exists()
    again = admin.verify(deep=True)  # dizin duruyor ama geçerli bir maç değil: sorun olarak bildirilir
    assert again.ok and [(p.layout, p.kind) for p in again.problems] == [("v3", "no_event_payload")]

    # ikinci kez bozulan yük: hata sayacı artar; manifestte yükü olmayan dilime dokunulmaz
    liv = sf.event_id(sf.PL_LIV)
    write_v3(data, sf.basic_payload(sf.PL_LIV), slices={"h2h": {"a": 1}}, entries={
        "lineups": SliceEntry(state="error", error=ErrorMark(reason="429", count=4))})
    found = manifest.read_manifest(v3_file(data, liv, "manifest.json"))
    found.slices["h2h"].error = ErrorMark(reason="corrupt", count=2)  # önceki bozulmadan kalan işaret
    manifest.write_manifest(v3_file(data, liv, "manifest.json"), found)
    v3_file(data, liv, "h2h.json.gz").write_bytes(b"x")
    codec.write_payload(v3_file(data, liv, "lineups.json.gz"), {"x": 1})
    assert verify.mark_corrupt(admin, liv, ["h2h", "lineups"]) == ["h2h"]
    found = manifest.read_manifest(v3_file(data, liv, "manifest.json"))
    assert (found.slices["h2h"].state, found.slices["h2h"].error.count) == ("error", 3)
    assert (found.slices["lineups"].error.reason, found.slices["lineups"].error.count) == ("429", 4)


def test_quick_verify_sees_a_changed_v3_manifest(tmp_path: Path, make_admin) -> None:
    data = tmp_path / "data"
    payload = sf.basic_payload(sf.PL_ARS)
    write_v3(data, payload)
    admin = make_admin(data)
    admin.rebuild()
    assert admin.verify().ok

    write_v3(data, payload, slices={"statistics": sf.slice_payload("statistics", payload)},
             updated=T1 + dt.timedelta(hours=1))
    report = admin.verify()
    assert issues(report) == [("I4", "event_row", ARS), ("I3", "slices", ARS)] and report.events_read == 1
    assert report.issues[0].detail == "farklı sütunlar: updated_at"
    assert report.issues[1].detail.startswith("event: fetched_at, checked_at; statistics: katalogda yok")
    assert admin.verify(repair=True).ok and admin.verify().events_read == 0
    assert set(slice_rows(admin.catalog, ARS)) == {"event", "statistics"}


def test_failed_quick_check_stops_the_comparison(canonical: sf.LegacyFixture, make_admin,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    admin = make_admin(canonical.data_dir)
    admin.rebuild()
    monkeypatch.setattr(Catalog, "quick_check", lambda self: ["row 3 missing from index events_start"])

    report = admin.verify(deep=True, repair=True)

    assert [(i.invariant, i.kind, i.detail, i.path, i.repaired) for i in report.issues] == [
        ("catalog", "quick_check", "row 3 missing from index events_start", ".meta/catalog.db", False)]
    assert (report.ok, report.events, report.checked) == (False, 0, ("quick_check",))  # bozuk dizinle satır okunmaz


def test_pending_writes_are_reported_and_cleared_by_repair(canonical: sf.LegacyFixture, make_admin) -> None:
    admin = make_admin(canonical.data_dir)
    admin.rebuild()
    with admin.catalog.write():
        admin.catalog.upsert("pending_writes", [
            {"kind": "event", "entity_id": ARS, "started_at": NOW},
            {"kind": "season", "entity_id": 96668, "started_at": NOW}])
    # yarım kalmış yazma: dosya değişti, katalog değişmedi
    write(canonical.data_dir, f"{PL_DIR}/{ARS}/incidents.json", {"incidents": []})

    report = admin.verify()
    assert issues(report) == [("I3", "slices", ARS), ("I6", "pending_write", ARS), ("I6", "pending_write", None)]
    repaired = admin.verify(repair=True)
    assert [(i.invariant, i.event_id, i.repaired) for i in repaired.issues] == [
        ("I3", ARS, True), ("I6", ARS, True), ("I6", None, False)]
    assert rows(admin.catalog, "pending_writes", order="kind") == [
        {"kind": "season", "entity_id": 96668, "started_at": NOW}]  # maç dışı varlıklar sonraki adımın işi
    assert issues(admin.verify()) == [("I6", "pending_write", None)]
    assert slice_rows(admin.catalog, ARS)["incidents"]["state"] == "empty"


def test_listing_only_rows_are_left_alone(canonical: sf.LegacyFixture, make_admin) -> None:
    """Liste satırları (sonraki adım) maç dizini olmadan durur: doğrulama onları eksik saymaz."""
    admin = make_admin(canonical.data_dir)
    admin.rebuild()
    listed = derive.event_row(sf.event_payload(sf.PL_NOT_STARTED), "listing")
    with admin.catalog.write():
        admin.catalog.upsert("events", [{**listed, "first_seen_at": NOW, "updated_at": NOW}])

    assert admin.verify().ok and admin.verify(deep=True, repair=True).ok
    assert admin.index_event(listed["id"]) is None  # dizini yok: dizinlenecek bir şey de, silinecek bir şey de yok
    row = event_row(admin.catalog, listed["id"])
    assert (row["row_source"], row["has_event_payload"], row["layout"]) == ("listing", 0, None)

    # maç dizini gelince satır olay yükünden yazılır; liste sütunlarına (listed_in, stale) dokunulmaz
    with admin.catalog.write() as conn:
        conn.execute("UPDATE events SET listed_in = 'round_2', stale = 1 WHERE id = ?", (listed["id"],))
    legacy_detail(canonical.data_dir, sf.PL_NOT_STARTED)
    assert issues(admin.verify()) == [("I3", "unindexed", listed["id"])]
    assert admin.verify(repair=True).ok
    row = event_row(admin.catalog, listed["id"])
    assert (row["row_source"], row["has_event_payload"], row["layout"], row["listed_in"], row["stale"]) == (
        "event", 1, "legacy", "round_2", 1)


# --- sayımlar ve araç -----------------------------------------------------------------------------------

def test_stats(old_forms: sf.LegacyFixture, make_admin) -> None:
    admin = make_admin(old_forms.data_dir)
    state = tree_state(old_forms.data_dir)
    assert admin.stats() == {
        "path": admin.catalog.path, "exists": False, "usable": False, "rebuild_reason": "missing",
        "schema_version": None, "derive_version": None, "size_bytes": None}
    assert tree_state(old_forms.data_dir) == state

    write_v3(old_forms.data_dir, sf.basic_payload(sf.PL_ARS))
    report = admin.rebuild()
    stats = admin.stats()

    assert (stats["exists"], stats["usable"], stats["rebuild_reason"], stats["schema_version"],
            stats["derive_version"], stats["journal_mode"]) == (True, True, None, 1, derive.DERIVE_VERSION, "wal")
    assert stats["size_bytes"] == os.path.getsize(admin.catalog.path) and stats["tables"] == report.counts
    assert stats["meta"]["built_at"] == str(NOW) and stats["meta"]["build_mode"] == "recreate"
    assert stats["events"] == {
        "by_layout": {"legacy": 8, "v3": 1}, "by_status_class": {"completed": 9},
        "by_sport": {"": 1, "football": 7, "tennis": 1}, "with_event_payload": 9, "observed": 3,
        "superseded_legacy_dirs": 1}
    assert stats["slices"]["by_state"]["error"] == 1 and sum(stats["slices"]["by_state"].values()) == report.slices

    admin.catalog.close()
    _foreign_file(admin.catalog.path, "PRAGMA user_version = 9")
    other = admin.stats()
    assert (other["usable"], other["rebuild_reason"], other["schema_version"]) == (False, "schema_version", 9)
    assert "tables" not in other and other["size_bytes"] > 0


def _tool() -> Any:
    spec = importlib.util.spec_from_file_location("catalog_tool_under_test", ROOT / "scripts" / "catalog_tool.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_catalog_tool(old_forms: sf.LegacyFixture, capsys: pytest.CaptureFixture, monkeypatch: pytest.MonkeyPatch,
                      tmp_path: Path) -> None:
    tool = _tool()
    data = str(old_forms.data_dir)

    assert tool.main(["--data-dir", str(tmp_path / "nowhere"), "stats"]) == tool.EXIT_USAGE
    assert "Veri dizini yok" in capsys.readouterr().err and not (tmp_path / "nowhere").exists()

    assert tool.main(["--data-dir", data, "stats"]) == 0
    assert "dosya yok" in capsys.readouterr().out and not (old_forms.data_dir / ".meta").exists()
    assert tool.main(["--data-dir", data, "verify"]) == tool.EXIT_ISSUES
    assert "catalog missing" in capsys.readouterr().out

    assert tool.main(["--data-dir", data, "rebuild", "--json"]) == 0
    built = json.loads(capsys.readouterr().out)
    assert (built["mode"], built["reason"], built["completed"], built["events"]) == ("recreate", "missing", True, 9)
    assert {p["kind"] for p in built["problems"]} == {"no_event_payload", "corrupt"} and len(built["superseded"]) == 1

    monkeypatch.setenv("DATA_DIR", data)  # --data-dir verilmezse ortam değişkeni
    assert tool.main(["rebuild", "--mode", "in_place"]) == 0
    captured = capsys.readouterr()
    assert "Katalog yeniden kuruldu (in_place" in captured.out and "maç: 9" in captured.out
    assert "Kullanılmayan eski kopyalar (1):" in captured.out and "legacy_events: 9/9" in captured.err

    assert tool.main(["verify", "--deep", "--json"]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["ok"] is True and checked["issues"] == [] and checked["events_read"] == 9
    assert tool.main(["stats", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["tables"]["events"] == 9
    assert tool.main(["stats"]) == 0
    assert "events.by_layout: {'legacy': 9}" in capsys.readouterr().out

    files.remove_tree(old_forms.data_dir / "match_details" / "16867839")
    assert tool.main(["verify"]) == tool.EXIT_ISSUES
    out = capsys.readouterr().out
    assert "I1 no_event_directory maç 16867839" in out and "1 tutarsızlık giderilmedi" in out
    assert tool.main(["verify", "--repair"]) == 0
    assert "[onarıldı]" in capsys.readouterr().out
    assert tool.main(["verify"]) == 0
    assert "Katalog dosyalarla tutarlı." in capsys.readouterr().out

    with pytest.raises(SystemExit) as usage:
        tool.main(["rebuild", "--mode", "sideways"])
    assert usage.value.code == 2
    capsys.readouterr()

    broken = tmp_path / "broken"
    (broken / ".meta" / "catalog.db").mkdir(parents=True)  # veritabanı dosyası açılamaz: depolama hatası
    assert tool.main(["--data-dir", str(broken), "stats"]) == tool.EXIT_STORAGE
    assert "Depolama hatası" in capsys.readouterr().err


def test_catalog_tool_runs_as_a_script(canonical: sf.LegacyFixture) -> None:
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "catalog_tool.py"), "--data-dir", str(canonical.data_dir),
         "rebuild", "--json"], cwd=str(canonical.data_dir), capture_output=True, text=True, check=True)
    assert json.loads(out.stdout)["events"] == len(canonical.detail_ids)


# --- katmanlama -----------------------------------------------------------------------------------------

def test_modules_import_only_what_the_store_may_import() -> None:
    """Bölüm 2.1: Store yalnızca src.sports, src.status, src.slices, src.exceptions ve src.version'ı içe aktarabilir."""
    code = (
        "import sys, json; import src.store.indexer, src.store.verify; "
        "print(json.dumps(sorted(m for m in sys.modules if m == 'src' or m.startswith('src.'))))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    loaded = set(json.loads(out.stdout))

    assert {m for m in loaded if not m.startswith("src.store")} == {
        "src", "src.exceptions", "src.slices", "src.sports", "src.status", "src.version"}
    assert {"src.store.indexer", "src.store.verify", "src.store.legacy", "src.store.catalog"} <= loaded


def test_store_package_root_exports_the_admin_and_its_reports() -> None:
    """ST-11: yönetim sınıfı ve raporları kökten alınır (`Store.catalog` onları döndürür)."""
    import src.store
    from src.store import verify as verify_mod

    exported = {"CatalogAdmin": indexer, "RebuildReport": indexer, "ReconcileReport": indexer,
                "IndexProblem": indexer, "SupersededDir": indexer, "VerifyReport": verify_mod,
                "VerifyIssue": verify_mod}
    assert set(exported) <= set(src.store.__all__)
    for name, module in exported.items():
        assert getattr(src.store, name) is getattr(module, name)
