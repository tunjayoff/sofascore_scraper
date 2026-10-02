"""
Karakterizasyon: veri okuyan uç noktaların ve MatchDataFetcher okuyucularının bugünkü çıktısı (plan maddesi G-02).

`tests/store_fixtures.py`'nin kurduğu her veri dizini için şu okuyucuların çıktısı `tests/golden/readers/`
altında `<dizin>.<okuyucu>.json` olarak durur:

  api_matches            GET /api/matches (lig, sezon, tarih, details, sıralama, limit/offset)
  api_season_matches     GET /api/seasons/{id}/matches
  api_missing_details    GET /api/leagues/{id}/missing-details
  api_match_detail       GET /api/matches/{id}
  api_dashboard          GET /api/dashboard
  api_stats_system       GET /api/stats/system
  api_export_csv         POST /api/export/csv (üretir), GET /api/export/csv (birleşik ve lig süzgeçli)
  fetcher                _needs_detail_fetch, refresh_due_ids, collect_detail_match_ids, pending_detail_ids
  reset_markers          reset_unavailable_markers ve ardından işaret dosyaları

Okuyucuları kataloğa taşıyan plan maddeleri (RD-1 … RD-5, EX-1, P21) bu dosyaları değiştirmeden geçmeli ya da
her farkı tek tek açıklamalıdır. Davranış bilerek değiştirildiyse dosyalar şöyle yeniden üretilir:

    REGEN_READER_GOLDENS=1 python -m pytest tests/characterization/test_reader_goldens.py

Çıktıyı sabitlemek için: saat dilimi UTC'ye çekilir (özet CSV'lerindeki `match_date` ve `last_update` yerel
saattir), yenileme kararları için saat `FIXTURE_NOW`'da durur, lig listesi testin config dosyasına yazılır.
Dizin listeleme sırasına bağlı çıktılar (aşağıda tek tek belirtilir) sıralanarak kaydedilir.

Dosyanın sonundaki "fabrika sadakati" testleri, fabrikanın bugünkü yazıcılarla aynı dosyaları ürettiğini
denetler; yazıcılar kaldırıldığında (ST-21, ST-22, P15) onlarla birlikte silinir.
"""
from __future__ import annotations

import contextlib
import csv
import datetime as dt
import io
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Sequence
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import conftest
import store_fixtures as sf
from src.config_manager import ConfigManager
from src.match_data_fetcher import MatchDataFetcher
from src.web.app import app

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "golden" / "readers"
REGENERATE = os.getenv("REGEN_READER_GOLDENS") == "1"
READERS = ("api_matches", "api_season_matches", "api_missing_details", "api_match_detail", "api_dashboard",
           "api_stats_system", "api_export_csv", "fetcher", "reset_markers")
LINE_WIDTH = 118
SHORT_ITEM = 40  # bundan kısa öğeler satıra doldurulur
MAX_RECORD_KEYS = 32  # bundan az anahtarlı düz sözlük tek satırda kalır
UNKNOWN_LEAGUE = 999
UNKNOWN_ID = 1

client = TestClient(app)


def _fetcher(data_dir: Path) -> MatchDataFetcher:
    return MatchDataFetcher(config_manager=ConfigManager(), data_dir=str(data_dir))


# --- altın dosya biçimi ----------------------------------------------------------------------


def _is_leaf(obj: Any) -> bool:
    values = obj.values() if isinstance(obj, dict) else obj
    return not any(isinstance(v, (dict, list)) for v in values)


def _fill(items: List[str], pad: str) -> str:
    """Kısa öğeleri satırlara doldurur."""
    lines: List[str] = []
    for item in items:
        if lines and len(lines[-1]) + 2 + len(item) <= LINE_WIDTH:
            lines[-1] += ", " + item
        else:
            lines.append(pad + item)
    return ",\n".join(lines)


def _render(obj: Any, indent: int = 0, prefix: int = 0) -> str:
    """
    Belirlenimli JSON, fark okunur kalsın diye: satıra sığan değer tek satırda; yalnızca düz değer tutan küçük
    sözlük (bir satır, bir maç) hep tek satırda; kısa öğeler satıra doldurulur; gerisi bir düzey açılır.
    """
    compact = json.dumps(obj, ensure_ascii=False)
    if not isinstance(obj, (dict, list)) or not obj:
        return compact
    record = isinstance(obj, dict) and _is_leaf(obj) and len(obj) <= MAX_RECORD_KEYS
    if record or indent + prefix + len(compact) <= LINE_WIDTH:
        return compact
    pad, close = " " * (indent + 1), "\n" + " " * indent
    if isinstance(obj, dict):
        heads = [json.dumps(key, ensure_ascii=False) + ": " for key in obj]
        values = [_render(value, indent + 1, len(head)) for head, value in zip(heads, obj.values(), strict=True)]
        items = [head + value for head, value in zip(heads, values, strict=True)]
        short = all("\n" not in item and len(item) <= SHORT_ITEM for item in items)
        return "{\n" + (_fill(items, pad) if short else ",\n".join(pad + item for item in items)) + close + "}"
    items = [_render(value, indent + 1) for value in obj]
    short = all("\n" not in item and len(item) <= SHORT_ITEM for item in items)
    return "[\n" + (_fill(items, pad) if short else ",\n".join(pad + item for item in items)) + close + "]"


def _differences(expected: Any, actual: Any, path: str = "$") -> List[str]:
    """Farklı yollar; tür de karşılaştırılır (3 ile 3.0, true ile 1 farklıdır)."""
    if type(expected) is not type(actual):
        return [f"{path}: {expected!r} ({type(expected).__name__}) -> {actual!r} ({type(actual).__name__})"]
    if isinstance(expected, dict):
        out: List[str] = []
        if list(expected) != list(actual):
            removed = [k for k in expected if k not in actual]
            added = [k for k in actual if k not in expected]
            out.append(f"{path}: keys removed {removed}, added {added}" if removed or added else f"{path}: key order")
        for key in expected:
            if key in actual:
                out.extend(_differences(expected[key], actual[key], f"{path}.{key}"))
        return out
    if isinstance(expected, list):
        out = [f"{path}: length {len(expected)} -> {len(actual)}"] if len(expected) != len(actual) else []
        for i in range(min(len(expected), len(actual))):
            out.extend(_differences(expected[i], actual[i], f"{path}[{i}]"))
        return out
    return [] if expected == actual else [f"{path}: {expected!r} -> {actual!r}"]


def check_golden(fixture: str, reader: str, data: Dict[str, Any]) -> None:
    assert reader in READERS
    path = GOLDEN_DIR / f"{fixture}.{reader}.json"
    text = _render(json.loads(json.dumps(data))) + "\n"
    if REGENERATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))  # her platformda LF
        return
    assert path.is_file(), f"{path.name} yok: REGEN_READER_GOLDENS=1 ile üretin"
    expected = path.read_text(encoding="utf-8")
    if expected != text:
        diffs = _differences(json.loads(expected), json.loads(text)) or ["yalnızca biçim farkı (yeniden üretin)"]
        shown = "\n  ".join(diffs[:40])
        pytest.fail(f"{path.name}: {len(diffs)} fark (beklenen -> şimdiki)\n  {shown}", pytrace=False)


def test_render_round_trips_and_differences_are_strict() -> None:
    data = {"a": [1, 2.0, True, None, "x" * 200], "b": {"c": {"d": list(range(60))}}, "e": {}, "f": []}
    assert json.loads(_render(data)) == data
    assert max(len(line) for line in _render(data).splitlines()) <= LINE_WIDTH + 100  # 200 karakterlik metin
    records = {"rows": [{"id": i, "name": "x" * 150} for i in range(2)]}
    assert _render(records).count("\n") == 5  # satır başına bir kayıt
    assert _differences({"a": 3}, {"a": 3.0}) and _differences({"a": 1}, {"a": True})
    assert _differences({"a": 1, "b": 2}, {"b": 2, "a": 1}) == ["$: key order"]
    assert _differences([1, 2], [1]) == ["$: length 2 -> 1"]
    assert not _differences(data, json.loads(json.dumps(data)))


# --- ortam ----------------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _utc_timezone() -> Iterator[None]:
    """Özet CSV'lerindeki `match_date` ve `last_update` yerel saattir: altın dosyalar UTC ile kaydedildi."""
    if hasattr(time, "tzset"):
        before = os.environ.get("TZ")
        os.environ["TZ"] = "UTC"
        time.tzset()
        try:
            yield
        finally:
            if before is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = before
            time.tzset()
        return
    # Windows: süreç içinde saat dilimi değiştirilemez; makine UTC değilse karşılaştırma anlamsız
    for ts in (sf.FIXTURE_NOW, sf.FIXTURE_NOW - 150 * sf.DAY):
        utc = dt.datetime.fromtimestamp(ts, dt.timezone.utc).replace(tzinfo=None)
        if dt.datetime.fromtimestamp(ts) != utc:
            pytest.skip("yerel saat dilimi UTC değil ve time.tzset yok")
    yield


@pytest.fixture(autouse=True)
def _default_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("REFRESH_WINDOW_HOURS", "REFRESH_MIN_INTERVAL_HOURS", "REFRESH_LEGACY", "FETCH_ONLY_FINISHED"):
        monkeypatch.delenv(key, raising=False)


_config_mtime_ns = 0


def _write_league_config(text: bytes) -> None:
    """leagues.txt'yi yazar; mtime her yazışta artar ki ConfigManager değişikliği kesin görsün."""
    global _config_mtime_ns
    path = os.path.join(conftest.CONFIG_DIR, "leagues.txt")
    with open(path, "wb") as f:
        f.write(text)
    _config_mtime_ns = max(time.time_ns(), _config_mtime_ns + 1_000_000_000, os.stat(path).st_mtime_ns + 1)
    os.utime(path, ns=(_config_mtime_ns, _config_mtime_ns))


@contextlib.contextmanager
def _configured_leagues(leagues: Dict[int, str]) -> Iterator[None]:
    path = os.path.join(conftest.CONFIG_DIR, "leagues.txt")
    with open(path, "rb") as f:
        before = f.read()
    lines = ["# League configuration file", "# Format: League Name: ID", ""]
    lines += [f"{name}: {league_id}" for league_id, name in leagues.items()]
    _write_league_config(("\n".join(lines) + "\n").encode("utf-8"))
    try:
        yield
    finally:
        _write_league_config(before)


@pytest.fixture(params=sf.FIXTURE_NAMES)
def fx(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[sf.LegacyFixture]:
    """Taze kurulmuş veri dizini; web katmanı (DATA_DIR) ve lig listesi ona çevrilir."""
    fixture = sf.build_fixture(request.param, tmp_path / "data")
    monkeypatch.setenv("DATA_DIR", str(fixture.data_dir))
    with _configured_leagues(fixture.leagues):
        yield fixture


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Yenileme kararı `time.time()`'a bakar (src/refresh.py): saat FIXTURE_NOW'da durur."""
    monkeypatch.setattr(time, "time", lambda: float(sf.FIXTURE_NOW))


def _get(url: str) -> Dict[str, Any]:
    """Yanıtın kaydı. 422'nin gövdesi pydantic sürümüne bağlı olduğundan yalnızca kodu tutulur."""
    r = client.get(url)
    if r.status_code == 422:
        return {"status": 422}
    return {"status": r.status_code, "body": r.json()}


# --- GET /api/matches -------------------------------------------------------------------------


def _match_queries(fixture: sf.LegacyFixture) -> List[str]:
    leagues = list(fixture.leagues)
    queries = [
        "", "sort=asc", "sort=bogus", "limit=200&sort=asc", "limit=3", "limit=3&offset=3", "limit=3&offset=5000",
        "limit=0", "details=present", "details=missing", "details=other", "details=missing&sort=asc&limit=2",
        "date=2026-09-15", "date=2026-09-29T13", "date=2026-05", "date=1999", "date=17894",
        f"league_id={UNKNOWN_LEAGUE}", "league_id=abc", "season_id=1",
    ]
    for league_id in leagues:
        queries += [f"league_id={league_id}", f"league_id={league_id}&details=present",
                    f"league_id={league_id}&details=missing"]
    if len(leagues) > 1:
        queries.append(f"league_id={leagues[0]},{leagues[1]}&sort=asc")
        queries.append(f"league_id={leagues[1]},{UNKNOWN_LEAGUE},x")
    for league_id, season_id in fixture.listed:
        queries += [f"season_id={season_id}", f"league_id={league_id}&season_id={season_id}",
                    f"league_id={UNKNOWN_LEAGUE}&season_id={season_id}"]
    if leagues:
        queries.append(f"league_id={leagues[0]}&date=2026-09-29&details=present&sort=asc&limit=2&offset=1")
    return list(dict.fromkeys(queries))


def test_api_matches(fx: sf.LegacyFixture) -> None:
    """
    Kayıt biçimi: `rows` her maçın satırını bir kez tutar (`has_details` hariç), sorgular yalnızca
    (toplam, sayfa, [maç id, has_details]) listesini. Bir sorgu aynı maç için farklı bir satır döndürürse
    test bunu ayrıca bildirir. `has_details` sorguya göre değişebilir: lig süzgeci varken yalnızca
    `<lig id>_` önekli detay dizinlerine bakılır.
    """
    full = _get("/api/matches?limit=200&sort=asc")
    assert full["status"] == 200
    assert full["body"]["total"] <= 200, "fixture bir sayfaya sığmalı"
    everything = full["body"]["items"]
    rows = {str(item["match_id"]): {k: v for k, v in item.items() if k != "has_details"} for item in everything}
    assert len(rows) == len(everything)

    golden: Dict[str, Any] = {"rows": rows, "queries": {}}
    for query in _match_queries(fx):
        response = _get(f"/api/matches?{query}")
        if response["status"] == 200:
            body = response["body"]
            items = []
            for item in body["items"]:
                row = {k: v for k, v in item.items() if k != "has_details"}
                assert row == rows[str(item["match_id"])], f"{query}: satır tam listedekinden farklı"
                items.append([item["match_id"], item["has_details"]])
            meta = {"status": 200, **{k: body[k] for k in ("total", "limit", "offset", "sort")}}
            assert set(body) == set(meta) - {"status"} | {"items"}
            response = {"response": meta, "items": items}
        golden["queries"][query] = response
    check_golden(fx.name, "api_matches", golden)


# --- GET /api/seasons/{id}/matches --------------------------------------------------------------

# Bu sezonların iki özet dosyası var; satır sırası dizin listeleme sırasına bağlı olduğundan sıralanır
UNORDERED_SEASONS = {("legacy", 17, 96668)}


def test_api_season_matches(fx: sf.LegacyFixture) -> None:
    golden: Dict[str, Any] = {}
    pairs = list(fx.listed) + [(UNKNOWN_LEAGUE, 96668), (next(iter(fx.leagues)), 1)]
    for league_id, season_id in pairs:
        response = _get(f"/api/seasons/{season_id}/matches?league_id={league_id}")
        if (fx.name, league_id, season_id) in UNORDERED_SEASONS:
            response["body"]["matches"].sort(key=lambda row: json.dumps(row, sort_keys=True))
            response["order"] = "sorted here: two summary files, read in directory order"
        golden[f"season_id={season_id}&league_id={league_id}"] = response
    golden["season_id=96668 (league_id missing)"] = _get("/api/seasons/96668/matches")
    check_golden(fx.name, "api_season_matches", golden)


# --- GET /api/leagues/{id}/missing-details ------------------------------------------------------


def test_api_missing_details(fx: sf.LegacyFixture) -> None:
    golden: Dict[str, Any] = {}
    for league_id in list(fx.leagues) + [UNKNOWN_LEAGUE]:
        golden[f"league_id={league_id}"] = _get(f"/api/leagues/{league_id}/missing-details")
        seasons = [sid for lid, sid in fx.listed if lid == league_id] + [1]
        for season_id in seasons:
            url = f"/api/leagues/{league_id}/missing-details?season_id={season_id}"
            golden[f"league_id={league_id}&season_id={season_id}"] = _get(url)
    check_golden(fx.name, "api_missing_details", golden)


# --- GET /api/matches/{id} ----------------------------------------------------------------------


def test_api_match_detail(fx: sf.LegacyFixture) -> None:
    """Her bilinen maç (detayı olan, yalnızca listelenen) ve bilinmeyen bir id."""
    golden = {str(event_id): _get(f"/api/matches/{event_id}") for event_id in fx.event_ids + [UNKNOWN_ID]}
    check_golden(fx.name, "api_match_detail", golden)


# --- GET /api/dashboard, /api/stats/system ------------------------------------------------------


def test_api_dashboard(fx: sf.LegacyFixture) -> None:
    check_golden(fx.name, "api_dashboard", _get("/api/dashboard"))


def test_api_stats_system(fx: sf.LegacyFixture) -> None:
    check_golden(fx.name, "api_stats_system", _get("/api/stats/system"))


# --- GET /api/export/csv ------------------------------------------------------------------------

_TIMESTAMP = re.compile(r"\d{9,}")


def _processed_files(fixture: sf.LegacyFixture) -> List[str]:
    processed = fixture.data_dir / "match_details" / "processed"
    names = os.listdir(processed) if processed.is_dir() else []
    return sorted(name if name == sf.PROCESSED_CSV_NAME else _TIMESTAMP.sub("<ts>", name) for name in names)


def _export(url: str, method: str = "GET") -> Dict[str, Any]:
    """CSV yanıtı: sütunlar sırasıyla, satırlar sıralanmış (bugün dizin listeleme sırasıyla yazılır)."""
    r = client.request(method, url)
    if r.status_code != 200:
        return {"status": r.status_code, "body": r.json()}
    table = list(csv.reader(io.StringIO(r.content.decode("utf-8"), newline="")))
    disposition = r.headers.get("content-disposition", "")
    if sf.PROCESSED_CSV_NAME not in disposition:
        disposition = _TIMESTAMP.sub("<ts>", disposition)
    return {
        "status": 200,
        "content_type": r.headers.get("content-type"),
        "content_disposition": disposition,
        "columns": table[0],
        "rows": sorted(table[1:]),
    }


def test_api_export_csv(fx: sf.LegacyFixture) -> None:
    """
    GET yalnızca var olan dışa aktarma dosyasını okur; dosya yoksa 404 döner ve hiçbir şey üretmez (bir GET
    durum değiştirmez: başka bir sitedeki <img> onu tetikleyebilir). Dosyayı POST üretir (`match_details/
    processed/` altına; EX-1 dosya yazmayı kaldırır); sonraki GET'ler aynı dosyayı okur. Lig süzgeci dosyayı
    pandas'tan geçirir. `all`, eskiden üreten GET'in yanıtıydı; şimdi POST'un yanıtıdır (içerik aynı).
    """
    golden: Dict[str, Any] = {"processed_files_before": _processed_files(fx)}
    golden["get_before_create"] = _export("/api/export/csv")
    golden["all"] = _export("/api/export/csv", method="POST")
    golden["processed_files_after"] = _processed_files(fx)
    for league_id in list(fx.leagues) + [UNKNOWN_LEAGUE]:
        golden[f"league_id={league_id}"] = _export(f"/api/export/csv?league_id={league_id}")
    check_golden(fx.name, "api_export_csv", golden)


# --- MatchDataFetcher okuyucuları ---------------------------------------------------------------


def _needs(fetcher: MatchDataFetcher, ids: Sequence[int]) -> Dict[str, str]:
    return {str(event_id): fetcher._needs_detail_fetch(str(event_id)) for event_id in ids}


def test_fetcher_readers(fx: sf.LegacyFixture, frozen_clock: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    `collect_detail_match_ids()` (lig vermeden) ligleri dizin listeleme sırasıyla dolaşır: sıralanarak kaydedilir.
    Tek lig için sıra belirlidir (sezon id'si büyükten küçüğe, dosyadaki satır sırası).
    """
    fetcher = _fetcher(fx.data_dir)
    ids = fx.event_ids + [UNKNOWN_ID]
    leagues = list(fx.leagues)
    golden: Dict[str, Any] = {"_needs_detail_fetch": _needs(fetcher, ids)}

    golden["refresh_due_ids()"] = fetcher.refresh_due_ids()
    for league_id in leagues:
        golden[f"refresh_due_ids(league_id={league_id})"] = fetcher.refresh_due_ids(league_id)

    default = golden["_needs_detail_fetch"]
    for setting in ("REFRESH_LEGACY=true", "REFRESH_WINDOW_HOURS=0", "REFRESH_MIN_INTERVAL_HOURS=0"):
        with monkeypatch.context() as patch:
            patch.setenv(*setting.split("="))
            changed = {event_id: need for event_id, need in _needs(fetcher, ids).items() if need != default[event_id]}
            golden[setting] = {
                "_needs_detail_fetch (only where it differs from the default)": changed,
                "refresh_due_ids()": fetcher.refresh_due_ids(),
            }

    collected = fetcher.collect_detail_match_ids()
    golden["collect_detail_match_ids() sorted"] = None if collected is None else sorted(collected, key=int)
    for league_id in leagues + [UNKNOWN_LEAGUE]:
        key = f"collect_detail_match_ids(league_id='{league_id}'"
        golden[f"{key})"] = fetcher.collect_detail_match_ids(league_id=str(league_id))
        golden[f"{key}, max_seasons=1)"] = fetcher.collect_detail_match_ids(league_id=str(league_id), max_seasons=1)
        for season_id in [sid for lid, sid in fx.listed if lid == league_id]:
            golden[f"{key}, only_season_ids=[{season_id}])"] = fetcher.collect_detail_match_ids(
                league_id=str(league_id), only_season_ids=[season_id]
            )

    golden["pending_detail_ids(all ids ascending)"] = fetcher.pending_detail_ids([str(i) for i in ids])
    check_golden(fx.name, "fetcher", golden)


def test_needs_are_the_same_with_the_job_cache(fx: sf.LegacyFixture, frozen_clock: None) -> None:
    """
    İş önbelleği (`begin_job_cache`) aynı kararları verir. İki yerde duran maç hariç: önbellek dizini
    hangi kopyayı önce listelerse onu tutar, önbelleksiz arama ise lig/sezon dizinindekini seçer.
    """
    duplicated = {i for i in fx.detail_ids if sum(d.event_id == i for d in fx.details) > 1}
    ids = [i for i in fx.event_ids + [UNKNOWN_ID] if i not in duplicated]
    plain = _needs(_fetcher(fx.data_dir), ids)
    cached = _fetcher(fx.data_dir)
    cached.begin_job_cache()
    assert _needs(cached, ids) == plain


def _marker_files(fixture: sf.LegacyFixture) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for record in fixture.details:
        folder = fixture.data_dir.joinpath(*record.path.split("/"))
        state = {
            name: json.loads((folder / name).read_text(encoding="utf-8"))
            for name in ("_unavailable.json", "_slice_status.json")
            if (folder / name).is_file()
        }
        if state:
            out[record.path] = state
    return out


def test_reset_unavailable_markers(fx: sf.LegacyFixture, frozen_clock: None, tmp_path: Path) -> None:
    """Her çağrı taze bir kopyada: dönen sayaçlar, kalan işaret dosyaları ve değişen `_needs_detail_fetch`."""
    ids = fx.event_ids
    before = {"markers": _marker_files(fx), "_needs_detail_fetch": _needs(_fetcher(fx.data_dir), ids)}
    golden: Dict[str, Any] = {"before": before}
    calls: List[Dict[str, Any]] = [{}, {"include_confirmed": True}]
    calls += [{"league_id": league_id} for league_id in fx.leagues]
    for n, kwargs in enumerate(calls):
        copy = sf.build_fixture(fx.name, tmp_path / f"copy{n}")
        fetcher = _fetcher(copy.data_dir)
        label = ", ".join(f"{k}={v}" for k, v in kwargs.items())
        first = fetcher.reset_unavailable_markers(**kwargs)
        entry: Dict[str, Any] = {"result": first, "markers": _marker_files(copy)}
        if first["matches"]:
            entry["_needs_detail_fetch"] = _needs(fetcher, ids)
        entry["second_call"] = fetcher.reset_unavailable_markers(**kwargs)
        golden[f"reset_unavailable_markers({label})"] = entry
    check_golden(fx.name, "reset_markers", golden)


def test_no_stray_golden_files() -> None:
    expected = {f"{name}.{reader}.json" for name in sf.FIXTURE_NAMES for reader in READERS}
    assert set(os.listdir(GOLDEN_DIR)) == expected


# --- fabrikanın kendisi ---------------------------------------------------------------------


def _tree(root: Path) -> Dict[str, bytes]:
    """Ağaçtaki dosyalar; `.meta/` (Store'un kendi dosyaları: yazıcıların kancaları depoyu açar) dışında."""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and ".meta" not in p.relative_to(root).parts}


def _mtimes(root: Path) -> Dict[str, int]:
    return {p.relative_to(root).as_posix(): int(p.stat().st_mtime) for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("name", sf.FIXTURE_NAMES)
def test_factory_is_deterministic(name: str, tmp_path: Path) -> None:
    """İki kurulum bayt bayt ve mtime'larıyla aynıdır; hiçbir dosya `FIXTURE_NOW`'dan yeni değildir."""
    first = sf.build_fixture(name, tmp_path / "a")
    second = sf.build_fixture(name, tmp_path / "b")
    assert _tree(first.data_dir) == _tree(second.data_dir)
    assert first.details == second.details and first.listed == second.listed
    assert _mtimes(first.data_dir) == _mtimes(second.data_dir)
    assert all(mtime <= sf.FIXTURE_NOW for mtime in _mtimes(first.data_dir).values())


def test_factory_covers_every_legacy_form(tmp_path: Path) -> None:
    """docs/design/01-storage.md bölüm 5.1'deki her biçim en az bir dizinde var."""
    canonical = sf.build_fixture("canonical", tmp_path / "canonical")
    legacy = sf.build_fixture("legacy", tmp_path / "legacy")
    files = set(_tree(canonical.data_dir)) | set(_tree(legacy.data_dir))

    def has(pattern: str) -> bool:
        return any(re.fullmatch(pattern, f) for f in files)

    eid, name = r"\d+", r"[^/]+"
    assert has(rf"match_details/\d+_{name}/season_{name}/{eid}/basic\.json")  # L1
    assert has(rf"match_details/LaLiga/season_{name}/{eid}/basic\.json")  # L2
    assert has(rf"match_details/{eid}/basic\.json")  # L3
    assert has(rf"match_details/\d+_{name}/season_{name}/({eid})/\1\.json")  # L4, basic.json'ın yanında
    assert has(rf"match_details/({eid})/\1\.json")  # L4, tek başına
    assert has(rf"match_details/_no_tournament/(football|tennis|unknown)/{eid}/basic\.json")  # L5
    assert has(rf"matches/\d+_{name}/\d+_{name}/round_\d+\.json")
    assert has(rf"matches/\d+_{name}/\d+_{name}/round_\d+_[a-z]+\.json")  # kupa turu (slug)
    assert has(rf"matches/\d+_{name}/\d+_{name}/events_last_\d+\.json")
    assert has(rf"matches/\d+_{name}/\d+_{name}_summary\.json") and has(rf"matches/\d+_{name}/\d+_{name}_summary\.csv")
    assert has(rf"matches/\d+_{name}/\d+_{name}_matches\.csv")  # eski ad, lig dizininde
    assert has(rf"matches/\d+_{name}/\d+_{name}/round_\d+_matches\.csv")  # ilk sürüm, sezon dizininde
    assert {"seasons/17_Premier_League_seasons.json", "seasons/17_seasons.json", "seasons/LaLiga_seasons.json",
            "league_seasons.csv", "score_changes.jsonl", "watch_events.jsonl", "watch_state_football.json",
            "watch_state_tennis.json"} <= files

    # tur dosyaları: `_complete` true, false ve hiç yok
    rounds = [json.loads(data) for f, data in {**_tree(canonical.data_dir), **_tree(legacy.data_dir)}.items()
              if re.search(r"/round_\d+(_[a-z]+)?\.json$", f) and "_full" not in f]
    assert {r.get("_complete", "absent") for r in rounds} == {True, False, "absent"}

    # observation.json / _unavailable.json / _slice_status.json: sekiz birleşimin hepsi
    markers = ("observation.json", "_unavailable.json", "_slice_status.json")
    combos = {tuple(m in d.files for m in markers) for d in canonical.details}
    assert len(combos) == 8

    # penaltılarla biten futbol maçı: özet `current`'ı (10-9) yazar, `display` 3-3'tür
    basic = json.loads(_tree(canonical.data_dir)[sf.detail_dir(sf.Detail(sf.CUP_PEN)) + "/basic.json"])
    assert (basic["homeScore"]["current"], basic["homeScore"]["display"]) == (10, 3)
    summary = (canonical.data_dir / "matches/19_FA_Cup/97110_FA_Cup_26_27_summary.csv").read_text(encoding="utf-8")
    assert f"{basic['id']},Manchester City,Manchester United,10,9," in summary

    # iki özet dosyasında geçen maç ve iki sezon listesi olan lig
    twice = f",{sf.event_id(sf.PL_ARS)},"
    assert sum(twice in (legacy.data_dir / f).read_text(encoding="utf-8") for f in legacy.summary_files) == 2
    assert {"seasons/17_seasons.json", "seasons/17_Premier_League_seasons.json"} <= set(_tree(legacy.data_dir))


@pytest.mark.parametrize("name", sf.FIXTURE_NAMES)
def test_listed_matches_have_distinct_dates(name: str, tmp_path: Path) -> None:
    """
    GET /api/matches tarihe göre kararlı sıralar; tarihi eşit iki maçın sırası özet dosyalarının dizin listeleme
    sırasına kalır. Altın dosyalar bundan etkilenmesin diye fixture'da her maçın başlangıç saati farklıdır.
    """
    fixture = sf.build_fixture(name, tmp_path / "data")
    dates: Dict[str, str] = {}
    for rel in fixture.summary_files:
        with open(fixture.data_dir / rel, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                dates[row["match_id"]] = row["match_date"]
    assert len(set(dates.values())) == len(dates)


# --- fabrika sadakati: bugünkü yazıcılarla aynı dosyalar ------------------------------------------


def _match_fetcher(data_dir: Path, league: sf.League, season: sf.Season) -> Any:
    from src.match_fetcher import MatchFetcher

    config = MagicMock()
    config.get_leagues.return_value = {league.id: league.name}
    config.get_league_by_id.return_value = league.name
    seasons = MagicMock()
    seasons.get_season_name.return_value = season.name
    return MatchFetcher(config, seasons, data_dir=str(data_dir))


@pytest.mark.parametrize("league, season, listings", [
    (sf.PL, sf.PL_2627, sf.PL_ROUNDS),
    (sf.FA_CUP, sf.FA_2627, sf.CUP_ROUNDS),
    (sf.NBA, sf.NBA_2627, sf.NBA_PAGES),
    (sf.WIMBLEDON, sf.WIM_2026, sf.WIM_PAGES),
    (sf.LALIGA, sf.LALIGA_2627, sf.LIGA_PAGES),
], ids=["rounds", "cup-rounds", "pages", "pages-tennis", "pages-unfiltered"])
def test_fidelity_schedule_and_summary_files(league: sf.League, season: sf.Season, listings: Sequence[sf.Listing],
                                             tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tur dosyası, olay sayfası ve sezon özeti: MatchFetcher aynı yanıtlardan aynı baytları yazar."""
    from src.fsutil import atomic_write_json
    from src.paths import matches_season_dir

    monkeypatch.setattr("src.utils.FETCH_ONLY_FINISHED", all(listing.filtered for listing in listings))
    built = _tree(sf.build_fixture("canonical", tmp_path / "built").data_dir)
    fetcher = _match_fetcher(tmp_path / "written", league, season)
    out_dir = Path(matches_season_dir(str(tmp_path / "written"), league.id, league.name, season.id, season.name))
    results = []
    for listing in listings:
        raw = {"events": [sf.event_payload(ev) for ev in listing.events], "hasNextPage": listing.has_next}
        if listing.kind == "round":
            suffix = f"_{listing.slug}" if listing.slug else ""
            atomic_write_json(str(out_dir / f"round_{listing.label}{suffix}.json"),
                              {**raw, "_complete": fetcher._round_is_complete(raw)})
            result = fetcher._round_result(raw, listing.label)
        else:  # MatchFetcher._fetch_and_save_event_pages'in sayfa başına yaptığı
            kind, page = str(listing.label).split("/")
            result = fetcher._apply_finished_filter({**raw, "source": listing.label})
            atomic_write_json(str(out_dir / f"events_{kind}_{page}.json"), result)
            result = {**result, "round": f"{kind}_{page}"}
        if result:
            results.append(result)
    fetcher._save_season_summary(league.id, season.id, results)

    written = _tree(tmp_path / "written")
    assert written, "yazıcı hiçbir şey yazmadı"
    prefix = f"matches/{sf.league_dir(league)}/{sf.season_dir(season)}"
    assert written == {f: data for f, data in built.items() if f.startswith(prefix)}


def test_fidelity_detail_directories(tmp_path: Path) -> None:
    """Maç dizininin yeri (L1, L5) ve dosyaları: MatchDataFetcher._save_match_data aynısını yazar."""
    built_root = tmp_path / "built"
    built = _tree(sf.build_fixture("canonical", built_root / "canonical").data_dir)
    built.update(_tree(sf.build_fixture("legacy", built_root / "legacy").data_dir))
    fetcher = _fetcher(tmp_path / "written")
    for detail in (sf.Detail(sf.PL_ARS, observed="2026-09-19T06:00:00+00:00"), sf.Detail(sf.CUP_AET),
                   sf.Detail(sf.NBA_RECENT, observed="2026-10-01T08:00:00+00:00"),
                   sf.Detail(sf.LIGA_NEXT, slices=("team_streaks", "pregame_form", "h2h")),
                   sf.Detail(sf.FRIENDLY_A, form="L5", observed="2026-09-28T07:00:00+00:00"),
                   sf.Detail(sf.NO_SPORT_A, form="L5", slices=())):
        basic = sf.basic_payload(detail.ev)
        data: Dict[str, Any] = {"basic": basic, **{key: sf.slice_payload(key, basic) for key in detail.slices}}
        if detail.observed:
            data["observation"] = sf.observation_payload(basic, detail.observed)
        fetcher._save_match_data(str(basic["id"]), data)
        base = sf.detail_dir(detail)
        written = {f: d for f, d in _tree(tmp_path / "written").items() if f.startswith(base + "/")}
        assert written and written == {f: d for f, d in built.items() if f.startswith(base + "/")}, base


def test_fidelity_marker_files(tmp_path: Path) -> None:
    """_unavailable.json ve _slice_status.json: yazıcının ürettiği biçim fabrikanınkiyle aynı (zaman hariç)."""
    from src.match_data_fetcher import SLICE_EMPTY, SLICE_FAILED, SliceOutcome

    fetcher = _fetcher(tmp_path)
    basic = sf.basic_payload(sf.PL_BHA)
    data = {"basic": basic, **{key: sf.slice_payload(key, basic) for key in sf.REQUIRED_SLICES if key != "lineups"}}
    data["incidents"] = sf.slice_payload("incidents", basic, empty=True)
    outcomes = {"lineups": SliceOutcome(SLICE_EMPTY, reason="404", http_status=404),
                "incidents": SliceOutcome(SLICE_FAILED, reason="5xx", http_status=503)}
    for _ in range(2):
        fetcher._save_match_data(str(basic["id"]), data, outcomes)
    folder = tmp_path / sf.detail_dir(sf.Detail(sf.PL_BHA))

    def without_time(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: without_time(v) for k, v in value.items() if k != "at"}
        return value

    assert json.loads((folder / "_unavailable.json").read_text(encoding="utf-8")) == {"lineups": 2}
    status = json.loads((folder / "_slice_status.json").read_text(encoding="utf-8"))
    expected = {"lineups": sf.empty_marker(2), "incidents": sf.error_marker("5xx", 503, 2)}
    assert without_time(status) == without_time(expected)
    for stamp in (status["lineups"]["empty"]["at"], expected["lineups"]["empty"]["at"]):
        assert dt.datetime.fromisoformat(stamp).utcoffset() == dt.timedelta(0) and len(stamp) == 25


def test_fidelity_slices_and_names() -> None:
    """Dilim listesi, "veri var mı" denetimleri, dizin adları ve gözlem kaydı kodla aynı."""
    from src import paths
    from src.match_data_fetcher import DETAIL_SLICE_KEYS, NO_TOURNAMENT_DIR
    from src.sports import slices_for
    from src.status import observation_record

    assert sf.REQUIRED_SLICES == DETAIL_SLICE_KEYS
    assert tuple(s.key for s in slices_for("tennis") if not s.required) == sf.OPTIONAL_SLICES
    assert NO_TOURNAMENT_DIR in sf.detail_dir(sf.Detail(sf.FRIENDLY_A, form="L5"))
    fetcher = MatchDataFetcher.__new__(MatchDataFetcher)
    for ev in (sf.PL_ARS, sf.NBA_A, sf.WIM_A):
        basic = sf.basic_payload(ev)
        for key in sf.REQUIRED_SLICES + sf.OPTIONAL_SLICES:
            assert fetcher.match_detail_slice_present(key, {key: sf.slice_payload(key, basic)}), key
            # Bugünkü davranış: tabloda özel denetimi olmayan dilim (point_by_point) boş listeyle de "var" sayılır
            empty_is_present = key in sf.OPTIONAL_SLICES
            empty = {key: sf.slice_payload(key, basic, empty=True)}
            assert fetcher.match_detail_slice_present(key, empty) is empty_is_present, key
        when = dt.datetime(2026, 9, 19, 6, tzinfo=dt.timezone.utc)
        assert observation_record(basic, when) == sf.observation_payload(basic, "2026-09-19T06:00:00+00:00")
    for name in ("Premier League", "Wimbledon, Men", "LaLiga 25/26", "a\\b"):
        assert sf.safe_name(name) == paths.safe_name(name)
    assert sf.league_dir(sf.WIMBLEDON) == paths.league_dir_name(sf.WIMBLEDON.id, sf.WIMBLEDON.name)
    assert sf.season_dir(sf.PL_2627) == paths.season_dir_name(sf.PL_2627.id, sf.PL_2627.name)
    seasons_file = paths.seasons_file("d", sf.WIMBLEDON.id, sf.WIMBLEDON.name)
    assert os.path.basename(seasons_file) == "2361_Wimbledon,_Men_seasons.json"  # canonical dizinindeki ad


def _score_change_inputs() -> List[Any]:
    """(eski basic, yeni basic, spor, yazıldığı an) — fabrikadaki SCORE_CHANGES satırlarının kaynağı."""
    new = sf.basic_payload(sf.PL_LIV)
    old = json.loads(json.dumps(new))
    for key in ("current", "display", "normaltime", "period1"):
        old["awayScore"][key] -= 1
    old["changes"]["changeTimestamp"] -= 3053
    void_new = sf.basic_payload(sf.NBA_VOID, sf.NBA_VOID_BASIC)
    void_old = sf.basic_payload(sf.NBA_VOID)
    return [(old, new, "football", "2026-09-15T13:10:00+00:00"),
            (void_old, void_new, "basketball", "2026-09-21T00:00:00+00:00")]


def test_fidelity_score_changes() -> None:
    from src.refresh import change_row, diff_basic

    rows = [change_row(old, new, diff_basic(old, new), sport, now=dt.datetime.fromisoformat(when))
            for old, new, sport, when in _score_change_inputs()]
    assert json.loads(json.dumps(rows)) == list(sf.SCORE_CHANGES)


def _replay_watcher(data_dir: Path) -> None:
    """Fabrikadaki WATCH_EVENTS / WATCH_STATE'in kaynağı: canlı → gol → bitti (futbol), canlı (tenis)."""
    from src.watcher import MatchWatcher

    now = [1790690000.0]  # 2026-09-29T13:53:20Z

    def watcher(sport: str, event_id: int) -> Any:
        return MatchWatcher(sport, event_ids=[event_id], data_dir=str(data_dir), fetch_json=lambda path: None,
                            clock=lambda: now[0], sleep=lambda s: None)

    live = sf.event_payload(sf.PL_NEW, "football/A_inprogress-7-2nd-half__17018572")
    goal = json.loads(json.dumps(live))
    goal["homeScore"]["current"] += 1
    goal["homeScore"]["display"] += 1
    football = watcher("football", live["id"])
    football._observe(live, "live")
    now[0] += 60
    football._observe(goal, "live")
    now[0] += 600
    football._observe(sf.event_payload(sf.PL_NEW), "event")
    football._save_state()
    tennis_live = sf.event_payload(sf.WIM_LIVE)
    tennis = watcher("tennis", tennis_live["id"])
    tennis._observe(tennis_live, "live")
    tennis._save_state()


def test_fidelity_watcher_files(tmp_path: Path) -> None:
    _replay_watcher(tmp_path / "written")
    built = _tree(sf.build_fixture("canonical", tmp_path / "built").data_dir)
    # İzleyici Store üzerinden yazar: .meta/ (state.db, katalog) da oluşur; açık veritabanı dosyaları okunmaz.
    # 2.x dosyaları her platformda fabrikadakilerle bayt bayt aynıdır (watch_events.jsonl LF ile eklenir).
    root = tmp_path / "written"
    written = {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*"))
               if p.is_file() and p.relative_to(root).parts[0] != ".meta"}
    assert set(written) == {"watch_events.jsonl", "watch_state_football.json", "watch_state_tennis.json"}
    assert written == {f: built[f] for f in written}
