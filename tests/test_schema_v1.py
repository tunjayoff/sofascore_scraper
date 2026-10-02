"""
Normalleştirilmiş şema v1 (plan maddesi SC-1; docs/design/04-schema-v1.md): modeller, eşleyiciler, JSON Schema.

Altın dosyalar tests/golden/schema altındadır:

  events.json        tests/fixtures/status altındaki her gerçek yanıtın Event kaydı
  payloads.json      inputs/ altındaki tam olay yüklerinin Event kaydı ve içlerindeki varlıklar
  store.json         kanonik fixture dizininin Store satırlarından kayıtlar (dilim, değişiklik, akış olayı, ...)
  json_schema.json   üretilen JSON Schema belgesi
  inputs/*.json      research/status_samples'tan alınmış tam olay yükleri (çeviri alanları atılmış)

Çıktı bilerek değiştirildiyse altın dosyalar şöyle yeniden üretilir ve fark gözden geçirilir:

    REGEN_SCHEMA_GOLDEN=1 python -m pytest tests/test_schema_v1.py

Belgedeki alan tabloları modellerden üretilir; model değişince tablolar şöyle yenilenir:

    REGEN_SCHEMA_DOC=1 python -m pytest tests/test_schema_v1.py

Alan silmek, yeniden adlandırmak ya da anlamını değiştirmek SCHEMA_VERSION'ı artırır (belge, bölüm 3).

Ağ yok.
"""
from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import json
import os
import re
import subprocess
import sys
import typing
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import pytest

import store_fixtures as sf
from src import schema
from src.refresh import DEFAULT_REFRESH_WINDOW_HOURS
from src.schema import jsonschema as schema_doc
from src.schema import mappers, models
from src.sports import SPORTS, PeriodFormat, ScoreFamily, period_format, score_family, set_format
from src.status import StatusClass, classify_status
from src.store import (
    ChangeRow,
    EventQuery,
    EventRow,
    Ref,
    SliceInfo,
    Store,
    StreamEvent,
    StreamRecord,
    derive,
    open_store,
)
from src.store import SliceError as StoreSliceError
from src.store.indexer import CatalogAdmin

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "status"
GOLDEN_DIR = Path(__file__).parent / "golden" / "schema"
INPUTS = sorted((GOLDEN_DIR / "inputs").glob("*.json"))
PATHS = sorted(FIXTURES.glob("*/*.json"))
DOC = ROOT / "docs" / "design" / "04-schema-v1.md"
PACKAGE = ROOT / "src" / "schema"

REGEN = os.getenv("REGEN_SCHEMA_GOLDEN") == "1"
REGEN_DOC = os.getenv("REGEN_SCHEMA_DOC") == "1"
WINDOW_S = DEFAULT_REFRESH_WINDOW_HOURS * 3600
UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{3})?Z")


# --- yardımcılar --------------------------------------------------------------------------------------

def _key(path: Path) -> str:
    return f"{path.parent.name}/{path.stem}"


def _load(path: Path) -> Tuple[Dict[str, Any], str, float]:
    """Durum fixture'ı: yanıt (kimliği `event_id`'den), spor (dizin adı) ve yanıtın alındığı an."""
    event = json.loads(path.read_text(encoding="utf-8"))
    event["id"] = event["event_id"]
    return event, path.parent.name, dt.datetime.fromisoformat(event["fetched_at_utc"]).timestamp()


def _event_record(path: Path) -> Dict[str, Any]:
    event, sport, fetched_at = _load(path)
    return schema.event_from_row(derive.event_row(event, "event", fetched_at, sport=sport)).to_dict()


def _load_input(path: Path) -> Tuple[Dict[str, Any], float]:
    """Tam olay yükü ve alındığı an."""
    document = json.loads(path.read_text(encoding="utf-8"))
    return document["event"], dt.datetime.fromisoformat(document["fetched_at_utc"]).timestamp()


def _payload_records(path: Path) -> Dict[str, Any]:
    """Bir tam yükten çıkan bütün kayıtlar: maç ve içinde geçen varlıklar (derive satırları üzerinden)."""
    event, fetched_at = _load_input(path)
    rows = derive.event_entity_rows(event, updated_at=fetched_at)
    return {
        "event": schema.event_from_row(derive.event_row(event, "event", fetched_at)).to_dict(),
        "sport": _dict(schema.sport_from_row(rows.sport or {})),
        "category": _dict(schema.category_from_row(rows.category or {})),
        "tournament": _dict(schema.tournament_from_row(rows.tournament or {})),
        "season": _dict(schema.season_from_row(rows.season or {})),
        "participants": [_dict(schema.participant_from_row(row)) for row in rows.participants],
    }


def _dict(record: Optional[models.Model]) -> Optional[Dict[str, Any]]:
    return record.to_dict() if record is not None else None


def _write_lines(path: Path, records: Mapping[str, Any]) -> None:
    """Anahtar başına bir satır: fark okunur kalsın."""
    lines = [f" {json.dumps(key)}: {json.dumps(records[key], ensure_ascii=False)}" for key in sorted(records)]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8", newline="\n")


def _golden(name: str, build: Any) -> Any:
    path = GOLDEN_DIR / name
    if REGEN:
        built = build()
        if name == "json_schema.json":
            path.write_text(json.dumps(built, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
        else:
            _write_lines(path, built)
    return json.loads(path.read_text(encoding="utf-8"))


def validate(instance: Any, schema_: Mapping[str, Any], root: Mapping[str, Any], where: str = "$") -> List[str]:
    """Üretilen belgenin kullandığı JSON Schema alt kümesi için doğrulayıcı; sorunların listesini döndürür."""
    if "$ref" in schema_:
        target = root["$defs"][schema_["$ref"].rsplit("/", 1)[1]]
        return validate(instance, target, root, where)
    for keyword in ("oneOf", "anyOf"):
        if keyword in schema_:
            matches = [option for option in schema_[keyword] if not validate(instance, option, root, where)]
            wrong = len(matches) != 1 if keyword == "oneOf" else not matches
            return [f"{where}: matches {len(matches)} of the {keyword} options"] if wrong else []
    problems: List[str] = []
    if "const" in schema_ and instance != schema_["const"]:
        problems.append(f"{where}: expected {schema_['const']!r}, got {instance!r}")
    if "enum" in schema_ and instance not in schema_["enum"]:
        problems.append(f"{where}: {instance!r} is not one of {schema_['enum']!r}")
    wanted = schema_.get("type")
    if wanted is not None:
        kinds = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float),
                 "boolean": bool, "null": type(None)}
        names = [wanted] if isinstance(wanted, str) else list(wanted)

        def is_kind(name: str) -> bool:
            if name in ("integer", "number") and isinstance(instance, bool):
                return False
            return isinstance(instance, kinds[name])

        if not any(is_kind(name) for name in names):
            return problems + [f"{where}: expected {wanted}, got {type(instance).__name__}"]
    if schema_.get("format") == "date-time" and isinstance(instance, str) and not UTC_RE.fullmatch(instance):
        problems.append(f"{where}: {instance!r} is not an ISO 8601 UTC time with Z")
    if isinstance(instance, dict):
        problems += [f"{where}: missing {key!r}" for key in schema_.get("required", ()) if key not in instance]
        for key, sub in schema_.get("properties", {}).items():
            if key in instance:
                problems += validate(instance[key], sub, root, f"{where}.{key}")
    if isinstance(instance, list) and "items" in schema_:
        for index, item in enumerate(instance):
            problems += validate(item, schema_["items"], root, f"{where}[{index}]")
    return problems


def check(record: Any, name: str) -> List[str]:
    """Kaydın `name` modelinin şemasına uymayan yerleri."""
    document = schema.json_schema()
    return validate(record, document["$defs"][name], document)


def build_catalog(data_dir: Path, leagues: Optional[Dict[int, str]] = None) -> None:
    """Kataloğu dosyalardan kurar (`open_store` bunu henüz yapmıyor)."""
    store = open_store(data_dir)
    try:
        CatalogAdmin(store.data_dir, store._catalog, clock=lambda: float(sf.FIXTURE_NOW), league_names=leagues).rebuild()
    finally:
        store.close()


@pytest.fixture(scope="module")
def canon_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Kanonik fixture dizini, kataloğu kurulmuş ve akış günlüğüne iki olay eklenmiş halde."""
    fx = sf.build_fixture("canonical", tmp_path_factory.mktemp("schema_canonical") / "data")
    build_catalog(fx.data_dir, fx.leagues)
    store = open_store(fx.data_dir)
    try:
        store.streams.append("live", [
            # 2.x izleyicisinin yazdığı biçim (ST-18): `data` olduğu gibi taşınır
            StreamEvent(type="live.status_changed", event_id=sf.event_id(sf.PL_ARS), sport="football",
                        tournament_id=17, source="poll", ts=1790856000.1234,
                        data={"from": "live", "to": "completed", "at_utc": "2026-10-01T12:00:00+00:00",
                              "change_ts": 1790855999, "source": "live", "provisional": True}),
        ])
        store.streams.append("system", [
            StreamEvent(type="system.blocked", source="system", ts=1790856001, data={"reason": "403"}),
        ])
    finally:
        store.close()
    return Path(fx.data_dir)


@pytest.fixture
def canon(canon_dir: Path) -> Store:
    """Kanonik dizinin Store'u (tests/conftest.py her testten sonra açık depoları kapatır)."""
    return open_store(canon_dir)


def _store_records(store: Store) -> Dict[str, Any]:
    """Kanonik dizinin Store satırlarından kayıtlar: her model en az bir kez."""
    events = {str(row.id): schema.event_from_row(row).to_dict() for row in store.events.iter(EventQuery())}
    slices = {}
    for event_id in (sf.event_id(sf.PL_ARS), sf.event_id(sf.WIM_A), sf.event_id(sf.NBA_VOID)):
        for info in store.events.slices(event_id):
            slices[f"event/{event_id}/{info.key}"] = schema.slice_from_info(info).to_dict()
    for info in store.entities.slices(Ref.tournament(17)):
        slices[f"tournament/17/{info.key}"] = schema.slice_from_info(info).to_dict()
    for info in store.entities.slices(Ref.season(17, 96668)):
        slices[f"season/96668/{info.key}/{info.sub}"] = schema.slice_from_info(info).to_dict()
    return {
        **{f"event/{key}": value for key, value in events.items()},
        **{f"slice/{key}": value for key, value in slices.items()},
        **{f"change/{row.seq}": schema.change_from_row(row).to_dict() for row in store.changes.list()},
        **{f"tournament/{row.id}": _dict(schema.tournament_from_row(row)) for row in store.entities.tournaments()},
        **{f"season/{row.id}": _dict(schema.season_from_row(row)) for row in store.entities.seasons(17)},
        **{f"participant/{row.id}": _dict(schema.participant_from_row(row))
           for row in store.entities.participants(text="united")},
        **{f"live_event/{record.seq}": schema.live_event_from_record(record).to_dict()
           for record in store.streams.read(after=0).events},
    }


# --- paket: saflık ve sürüm ---------------------------------------------------------------------------

def test_version_and_ids():
    assert schema.SCHEMA_VERSION == 1
    assert schema.SCHEMA_ID == "sofascore.data/1"
    assert schema.EVENT_ENVELOPE_ID == "sofascore.event/1"
    assert [model.__name__ for model in schema.RECORDS] == [
        "Sport", "Category", "Tournament", "Season", "Participant", "Event", "Slice", "Change", "LiveEvent"]
    assert set(schema.RECORDS) <= set(schema.MODELS)


def _imports(path: Path) -> List[Tuple[int, str, bool]]:
    """Dosyadaki `src.*` içe aktarmaları: (satır, modül, yalnızca tür denetimi için mi)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    typing_only = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and getattr(node.test, "id", None) == "TYPE_CHECKING":
            typing_only.update(id(child) for body in node.body for child in ast.walk(body))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src"):
            found.append((node.lineno, node.module, id(node) in typing_only))
        elif isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name, id(node) in typing_only)
                         for alias in node.names if alias.name.startswith("src"))
    return found


def test_package_imports_only_domain_modules():
    """Alan modülleri kendilerinden yukarıdaki hiçbir şeyi içe aktarmaz (02-services.md 2.1, madde 4)."""
    allowed = ("src.schema", "src.sports", "src.status", "src.refresh")
    runtime = [(path.name, line, module) for path in sorted(PACKAGE.glob("*.py"))
               for line, module, typing_only in _imports(path) if not typing_only]
    assert runtime, "the package imports nothing?"
    assert [item for item in runtime if not item[2].startswith(allowed)] == []
    # src.store yalnızca tür denetimi için anılır
    hinted = {module for path in PACKAGE.glob("*.py") for _line, module, typing_only in _imports(path) if typing_only}
    assert hinted == {"src.store"}


def test_importing_the_package_loads_no_store_and_no_io_module():
    code = ("import sys, json; import src.schema; "
            "print(json.dumps(sorted(m for m in sys.modules if m == 'src' or m.startswith('src.') "
            "or m in ('sqlite3', 'requests', 'curl_cffi', 'fastapi'))))")
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == [
        "src", "src.refresh", "src.schema", "src.schema.jsonschema", "src.schema.mappers", "src.schema.models",
        "src.sports", "src.status",
    ]


def test_enums_follow_the_domain_modules():
    assert typing.get_args(models.StatusClassName) == tuple(member.value for member in StatusClass)
    assert typing.get_args(ScoreFamily) == ("football", "periods", "sets")
    families = {typing.get_args(typing.get_type_hints(model)["family"])[0]
                for model in (models.FootballScore, models.PeriodsScore, models.SetsScore)}
    assert families == set(typing.get_args(ScoreFamily)) == {spec.score_family for spec in SPORTS}
    assert typing.get_args(models.SliceState) == ("ok", "empty", "error", "not_requested")
    assert mappers.TERMINAL_CLASSES == {"completed", "decided_without_play", "void"}
    assert mappers.DEFAULT_REFRESH_WINDOW_S == 72 * 3600


# --- modeller -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("model", schema.MODELS, ids=lambda model: model.__name__)
def test_every_field_carries_its_contract(model):
    assert dataclasses.is_dataclass(model) and issubclass(model, models.Model)
    assert model.SUMMARY and model.SUMMARY.endswith(".")
    assert model.__dataclass_params__.frozen
    for item in dataclasses.fields(model):
        assert item.default is dataclasses.MISSING, f"{model.__name__}.{item.name}: every field is required"
        assert item.metadata["doc"].endswith("."), f"{model.__name__}.{item.name}"
        assert item.metadata["source"], f"{model.__name__}.{item.name}: no source"
        assert "|" not in item.metadata["doc"] + item.metadata["source"] + item.metadata["unit"]
        if item.name.endswith("_utc"):
            assert item.metadata["format"] == "date-time" and item.metadata["unit"] == "ISO 8601 UTC"
        if item.name.endswith("change_ts"):
            assert item.metadata["unit"] == "epoch seconds"


def test_to_dict_is_plain_json_in_field_order():
    record = models.Event(
        id=1, sport="tennis", category_id=None, tournament_id=None, season_id=None, stage=None, round=None,
        start_utc=None, status=models.Status(type=None, code=None, description=None, class_="unknown"),
        participants=models.EventParticipants(home=None, away=models.EventParticipant(id=2, name="B")),
        score=models.SetsScore(family="sets", home=None, away=None, sets_won=None, match_tiebreak=False, sets=(
            models.SetScore(number=1, home=6, away=4, tiebreak=None),)),
        winner=None, aggregate=None, slug=None, custom_id=None,
        quality=models.Quality(source="listing", observed_at_utc=None, change_ts=None, settlement="open",
                               provisional=False, tier_hint=None, stale=False, status_regressed=False),
    )
    plain = record.to_dict()

    assert list(plain) == [models.json_name(item) for item in dataclasses.fields(models.Event)]
    assert plain["status"] == {"type": None, "code": None, "description": None, "class": "unknown"}
    assert plain["score"]["sets"] == [{"number": 1, "home": 6, "away": 4, "tiebreak": None}]
    assert plain["participants"] == {"home": None, "away": {"id": 2, "name": "B"}}
    assert json.loads(json.dumps(plain)) == plain
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.id = 2  # type: ignore[misc]


# --- zaman --------------------------------------------------------------------------------------------

def test_utc_text():
    assert schema.utc_text(1790856000) == "2026-10-01T12:00:00Z"
    assert schema.utc_text(1790856000.9) == "2026-10-01T12:00:00Z"
    assert schema.utc_text(1790856000.1234, milliseconds=True) == "2026-10-01T12:00:00.123Z"
    assert schema.utc_text(0) == "1970-01-01T00:00:00Z"
    assert schema.utc_text(-1) == "1969-12-31T23:59:59Z"  # Windows'ta fromtimestamp bunu veremez
    aware = dt.datetime(2026, 10, 1, 15, 0, tzinfo=dt.timezone(dt.timedelta(hours=3)))
    assert schema.utc_text(aware) == "2026-10-01T12:00:00Z"
    assert schema.utc_text(dt.datetime(2026, 10, 1, 12, 0)) == "2026-10-01T12:00:00Z"  # dilimsiz: UTC
    for bad in (None, True, "2026-10-01", float("nan"), float("inf"), 10 ** 20):
        assert schema.utc_text(bad) is None


# --- altın kayıtlar: durum fixture'ları ----------------------------------------------------------------

@pytest.fixture(scope="module")
def golden_events() -> Dict[str, Any]:
    return _golden("events.json", lambda: {_key(path): _event_record(path) for path in PATHS})


def test_golden_covers_every_status_fixture(golden_events):
    """Yeni bir durum fixture'ı eklenince altın dosya yeniden üretilir (REGEN_SCHEMA_GOLDEN=1)."""
    assert sorted(golden_events) == sorted(_key(path) for path in PATHS)
    assert {"football", "basketball", "tennis"} <= {key.split("/")[0] for key in golden_events}


@pytest.mark.parametrize("path", PATHS, ids=_key)
def test_status_fixture_maps_to_the_golden_record(path, golden_events):
    record = _event_record(path)

    assert record == golden_events[_key(path)]
    assert check(record, "Event") == []


def _expected_pair(home: Mapping[str, Any], away: Mapping[str, Any], key: str) -> Optional[Dict[str, Any]]:
    if home.get(key) is None and away.get(key) is None:
        return None
    return {"home": home.get(key), "away": away.get(key)}


def _expected_score(event: Mapping[str, Any], sport: str) -> Dict[str, Any]:
    """
    Skor, belgedeki "Source" sütunundaki yollardan yeniden hesaplanır: src.status ve src.store.derive'dan
    bağımsız ikinci bir okuma. Eşleyici zinciri (yük → derive → şema) bununla aynı sonucu vermelidir.
    """
    home, away = event.get("homeScore") or {}, event.get("awayScore") or {}
    code = (event.get("status") or {}).get("code")

    def headline(score: Mapping[str, Any]) -> Any:
        return score.get("display") if score.get("display") is not None else score.get("current")

    common = {"home": headline(home), "away": headline(away)}
    family = score_family(sport)
    if family is None:
        return {"family": None, **common}
    if family == "football":
        return {
            "family": "football", **common,
            "half_time": _expected_pair(home, away, "period1"),
            "regulation": _expected_pair(home, away, "normaltime"),
            "after_extra_time": _expected_pair(home, away, "display") if code in (110, 120) else None,
            "penalties": _expected_pair(home, away, "penalties"),
        }
    if family == "periods":
        present = [n for n in (1, 2, 3, 4) if _expected_pair(home, away, f"period{n}")]
        fixed = period_format(sport)  # basketbol dışındaki sporlar: kayıt defterindeki bölünüş, periodN = N
        quarters = 1 in present or 3 in present
        numbers = {n: n for n in present} if fixed or quarters else {2: 1, 4: 2}
        fmt = fixed if fixed else "quarters" if quarters else "halves"
        return {
            "family": "periods", **common,
            "format": fmt if present else None,
            "periods": [{"number": numbers[n], **_expected_pair(home, away, f"period{n}")} for n in present],
            "regulation": _expected_pair(home, away, "normaltime"),
            "overtime": _expected_pair(home, away, "overtime"),
            "final": _expected_pair(home, away, "current"),
            "penalties": _expected_pair(home, away, "penalties") if fixed else None,
        }
    unit = set_format(sport)  # None: tenis
    sets = []
    for n in range(1, 6 if unit is None else 8):
        games = _expected_pair(home, away, f"period{n}")
        tiebreak = _expected_pair(home, away, f"period{n}TieBreak") if unit in (None, "games") else None
        if unit is None and games is None:  # tenis: ilk boş sette durur
            break
        if unit == "frames" or (games is None and tiebreak is None):  # snooker: period1 bir set değildir
            continue
        sets.append({"number": n, **(games or {"home": None, "away": None}), "tiebreak": tiebreak})
    last = sets[-1] if len(sets) in (3, 5) and unit in (None, "games") else None
    return {
        "family": "sets", **common,
        "sets_won": _expected_pair(home, away, "current"),
        "sets": sets,
        "match_tiebreak": bool(last) and max(last["home"] or 0, last["away"] or 0) >= 10,
    }


@pytest.mark.parametrize("path", PATHS, ids=_key)
def test_record_equals_an_independent_reading_of_the_payload(path):
    """Altın kayıtların gözden geçirmesi: her alan, belgede yazan kaynak yolundan bir kez daha okunur."""
    event, sport, fetched_at = _load(path)
    record = _event_record(path)
    status = event["status"]
    aggregated = _expected_pair(event.get("homeScore") or {}, event.get("awayScore") or {}, "aggregated")
    sides = {1: "home", 2: "away", 3: "draw"}

    assert record["id"] == event["event_id"] and record["sport"] == sport
    assert record["start_utc"] == dt.datetime.fromtimestamp(
        event["startTimestamp"], dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert record["status"] == {"type": status.get("type"), "code": status.get("code"),
                                "description": status.get("description"), "class": classify_status(event).value}
    assert record["score"] == _expected_score(event, sport)
    assert record["winner"] == sides.get(event.get("winnerCode"))
    if aggregated is None and event.get("aggregatedWinnerCode") is None:
        assert record["aggregate"] is None
    else:
        assert record["aggregate"] == {**(aggregated or {"home": None, "away": None}),
                                       "winner": sides.get(event.get("aggregatedWinnerCode"))}
    # durum fixture'larında takım, turnuva ve tur yoktur: bilinmeyen her şey null'dır, boş metin değil
    assert record["participants"] == {"home": None, "away": None}
    assert [record[key] for key in ("category_id", "tournament_id", "season_id", "stage", "round", "slug",
                                    "custom_id")] == [None] * 7

    quality = record["quality"]
    change_ts = (event.get("changes") or {}).get("changeTimestamp")
    terminal = record["status"]["class"] in ("completed", "decided_without_play", "void")
    gap = int(fetched_at) - event["startTimestamp"]
    assert quality["source"] == "event"
    assert quality["observed_at_utc"] == event["fetched_at_utc"].replace("+00:00", "Z")
    assert quality["change_ts"] == (change_ts or None)
    assert quality["settlement"] == ("open" if not terminal else "provisional" if gap < WINDOW_S else "final")
    assert quality["provisional"] is (quality["settlement"] == "provisional")
    assert (quality["tier_hint"], quality["stale"], quality["status_regressed"]) == (None, False, False)


def test_golden_shows_every_status_class_and_every_settlement(golden_events):
    """Fixture kümesi sözleşmenin dallarını gerçekten gösteriyor (altın dosya boş dallardan ibaret değil)."""
    records = list(golden_events.values())
    assert {r["status"]["class"] for r in records} == {
        "not_started", "live", "completed", "decided_without_play", "void"}
    assert {r["quality"]["settlement"] for r in records} == {"open", "provisional", "final"}
    assert {"football", "periods", "sets"} <= {r["score"]["family"] for r in records}
    assert {r["winner"] for r in records} == {"home", "away", "draw", None}
    assert {r["score"].get("format") for r in records if r["score"]["family"] == "periods"} == {
        "quarters", "halves", "thirds", None} == {None, *typing.get_args(PeriodFormat)}
    assert any(r["score"].get("after_extra_time") for r in records)
    assert any(r["score"].get("penalties") for r in records if r["score"]["family"] == "football")
    assert any(r["score"].get("penalties") for r in records if r["score"]["family"] == "periods")
    assert any(r["score"].get("overtime") for r in records)
    assert any(r["score"].get("match_tiebreak") for r in records)
    assert any(s["tiebreak"] for r in records for s in r["score"].get("sets", ()))
    assert sum(r["aggregate"] is not None for r in records) == 3  # iki futbol maçı, bir hentbol rövanşı (SP-1)
    assert any(r["quality"]["change_ts"] is None for r in records)


# --- altın kayıtlar: tam yükler ------------------------------------------------------------------------

@pytest.fixture(scope="module")
def golden_payloads() -> Dict[str, Any]:
    return _golden("payloads.json", lambda: {path.stem: _payload_records(path) for path in INPUTS})


def test_inputs_are_the_reviewed_set(golden_payloads):
    assert sorted(golden_payloads) == sorted(path.stem for path in INPUTS)
    assert len(INPUTS) == 7
    for path in INPUTS:
        document = json.loads(path.read_text(encoding="utf-8"))
        assert sorted(document) == ["event", "fetched_at_utc", "source_file"]
        assert document["source_file"].startswith("research/status_samples/")


@pytest.mark.parametrize("path", INPUTS, ids=lambda path: path.stem)
def test_full_payload_maps_to_the_golden_records(path, golden_payloads):
    records = _payload_records(path)

    assert records == golden_payloads[path.stem]
    assert check(records["event"], "Event") == []
    for name in ("sport", "category", "tournament", "season"):
        assert records[name] is not None, name
        assert check(records[name], name.capitalize()) == []
    assert len(records["participants"]) == 2
    assert all(check(record, "Participant") == [] for record in records["participants"])


@pytest.mark.parametrize("path", INPUTS, ids=lambda path: path.stem)
def test_entities_equal_the_payload_paths(path):
    """Varlık alanları belgede yazan yoldan okunur ve olayın kimlikleri varlıklarınkiyle aynıdır."""
    event, _fetched_at = _load_input(path)
    records = _payload_records(path)
    tournament, category = event["tournament"], event["tournament"]["category"]
    unique = tournament["uniqueTournament"]
    record = records["event"]

    assert records["sport"] == {"slug": category["sport"]["slug"], "name": category["sport"]["name"],
                                "id": category["sport"]["id"],
                                "score_family": {"football": "football", "basketball": "periods",
                                                 "tennis": "sets"}[category["sport"]["slug"]]}
    assert records["category"] == {"id": category["id"], "sport": category["sport"]["slug"],
                                   "name": category["name"], "slug": category["slug"],
                                   "country_code": category.get("alpha2")}
    assert records["tournament"] == {"id": unique["id"], "sport": category["sport"]["slug"],
                                     "category_id": category["id"], "name": unique["name"], "slug": unique["slug"]}
    assert records["season"] == {"id": event["season"]["id"], "tournament_id": unique["id"],
                                 "name": event["season"]["name"], "year": event["season"]["year"]}
    for side, got in zip(("homeTeam", "awayTeam"), records["participants"], strict=True):
        team = event[side]
        assert got == {
            "id": team["id"], "sport": team["sport"]["slug"],
            "type": {0: "team", 1: "player", 2: "pair"}[team["type"]],
            "name": team["name"], "short_name": team.get("shortName"), "slug": team["slug"],
            "name_code": team.get("nameCode"), "country_code": (team.get("country") or {}).get("alpha2"),
            "gender": team.get("gender"), "national": team.get("national"),
        }

    assert record["sport"] == records["sport"]["slug"]
    assert record["category_id"] == records["category"]["id"]
    assert record["tournament_id"] == records["tournament"]["id"]
    assert record["season_id"] == records["season"]["id"]
    assert record["stage"] == {"id": tournament["id"], "name": tournament["name"]}
    round_info = event.get("roundInfo")
    assert record["round"] == (None if not round_info else {
        "number": round_info.get("round"), "name": round_info.get("name"), "slug": round_info.get("slug")})
    assert record["participants"] == {
        "home": {"id": event["homeTeam"]["id"], "name": event["homeTeam"]["name"]},
        "away": {"id": event["awayTeam"]["id"], "name": event["awayTeam"]["name"]},
    }
    assert (record["slug"], record["custom_id"]) == (event["slug"], event["customId"])
    flags = (unique.get("hasEventPlayerStatistics"), event.get("hasEventPlayerStatistics"))
    assert record["quality"]["tier_hint"] is (True if any(flags) else None if flags == (None, None) else False)


def test_inputs_cover_the_participant_types_and_a_country_category(golden_payloads):
    types = {p["type"] for records in golden_payloads.values() for p in records["participants"]}
    assert types == {"team", "player", "pair"}
    codes = {records["category"]["country_code"] for records in golden_payloads.values()}
    assert {"EN", None} <= codes  # ülke kategorisi ve ülke olmayan kategori (ATP)
    rounds = [records["event"]["round"] for records in golden_payloads.values()]
    assert any(r and r["name"] for r in rounds) and any(r and r["name"] is None for r in rounds)


# --- eşleyiciler: kenar durumlar -----------------------------------------------------------------------

def _row(**columns: Any) -> Dict[str, Any]:
    base = {"id": 7, "sport": "football", "status_class": "completed", "row_source": "event",
            "has_event_payload": 1, "start_ts": 1_790_000_000, "observed_at": 1_790_000_000 + 3600}
    return {**base, **columns}


@pytest.mark.parametrize("columns, window, expected", [
    ({}, WINDOW_S, "provisional"),
    ({"observed_at": 1_790_000_000 + WINDOW_S - 1}, WINDOW_S, "provisional"),
    ({"observed_at": 1_790_000_000 + WINDOW_S}, WINDOW_S, "final"),
    ({"observed_at": None}, WINDOW_S, "final"),  # gözlem kaydı olmayan eski kayıt
    ({"start_ts": None}, WINDOW_S, "final"),
    ({}, 0, "final"),  # yenileme politikası kapalı
    ({}, 3600, "final"),
    ({}, 3601, "provisional"),
    ({"status_class": "decided_without_play"}, WINDOW_S, "provisional"),
    ({"status_class": "void"}, WINDOW_S, "provisional"),
    # ertelenip başlangıcı ileri alınmış maç: fark negatif, pencere kapanana kadar geçici
    ({"status_class": "void", "start_ts": 1_790_000_000 + 10 * 86400}, WINDOW_S, "provisional"),
    ({"status_class": "not_started"}, WINDOW_S, "open"),
    ({"status_class": "live"}, WINDOW_S, "open"),
    ({"status_class": "unknown"}, WINDOW_S, "open"),
    ({"status_class": "live", "observed_at": 1_790_000_000 + 10 * WINDOW_S}, WINDOW_S, "open"),
    ({"has_event_payload": 0, "row_source": "listing", "observed_at": None}, WINDOW_S, "open"),
])
def test_settlement_follows_the_storage_design(columns, window, expected):
    """docs/design/01-storage.md 8.3: open / provisional / final; `provisional` yalnızca ortadakinde doğrudur."""
    quality = schema.event_from_row(_row(**columns), refresh_window_s=window).quality

    assert quality.settlement == expected
    assert quality.provisional is (expected == "provisional")


def test_event_row_edge_cases():
    record = schema.event_from_row(_row(
        sport="", status_class="something else", row_source=None, has_event_payload=0, winner_code=9,
        change_ts=0, tier_hint=0, stale=1, status_regressed=1, home_name="A", slug="", round_slug="r",
        scores_json="not json", home_score=2.0, away_score=True,
    )).to_dict()

    assert record["sport"] is None and record["slug"] is None  # boş metin yok
    assert record["status"]["class"] == "unknown"
    assert record["quality"] == {"source": "listing", "observed_at_utc": "2026-09-21T15:13:20Z", "change_ts": None,
                                 "settlement": "open", "provisional": False, "tier_hint": False, "stale": True,
                                 "status_regressed": True}
    assert record["winner"] is None
    assert record["participants"] == {"home": {"id": None, "name": "A"}, "away": None}
    assert record["round"] == {"number": None, "name": None, "slug": "r"}
    assert record["score"] == {"family": None, "home": 2, "away": None}
    assert check(record, "Event") == []

    with pytest.raises(ValueError):
        schema.event_from_row({"sport": "football"})


def test_score_keeps_the_family_of_the_sport_without_a_sheet():
    """Çizelgesi çıkarılamamış satır da sporunun yapısında gelir; spor kayıtlı değilse yalnızca başlık skoru."""
    football = schema.event_from_row(_row(scores_json=None, home_score=1, away_score=0)).score
    assert football == models.FootballScore(family="football", home=1, away=0, half_time=None, regulation=None,
                                            after_extra_time=None, penalties=None)
    plain = schema.event_from_row(_row(sport="waterpolo", scores_json=None, home_score=3, away_score=1)).score
    assert plain == models.PlainScore(family=None, home=3, away=1)


def test_sets_keep_a_tiebreak_without_its_set():
    sheet = json.dumps({"family": "sets", "sets_won": [1, 0], "games": [[7, 6]], "tiebreaks": {"1": [7, 3], "3": [1, 0]},
                        "match_tiebreak": False})
    score = schema.event_from_row(_row(sport="tennis", scores_json=sheet)).score.to_dict()
    assert score["sets"] == [
        {"number": 1, "home": 7, "away": 6, "tiebreak": {"home": 7, "away": 3}},
        {"number": 3, "home": None, "away": None, "tiebreak": {"home": 1, "away": 0}},
    ]


def test_numbered_sets_keep_their_number():
    """Tenis dışındaki set sporları (SP-2): `sets` sözlüğü set numarasıyla; canlı yükte yalnızca o anki set."""
    sheet = json.dumps({"family": "sets", "format": "points", "sets_won": [1, 2], "sets": {"4": [9, 6]},
                        "tiebreaks": {}, "match_tiebreak": False})
    score = schema.event_from_row(_row(sport="table-tennis", scores_json=sheet)).score.to_dict()
    assert score["sets"] == [{"number": 4, "home": 9, "away": 6, "tiebreak": None}]
    assert score["sets_won"] == {"home": 1, "away": 2}
    bad = json.dumps({"family": "sets", "sets": {"x": [1, 0], "2": [None, None], "3": "1-0", "1": [25, 20]}})
    assert schema.event_from_row(_row(sport="volleyball", scores_json=bad)).score.to_dict()["sets"] == [
        {"number": 1, "home": 25, "away": 20, "tiebreak": None}]


def test_malformed_sheets_and_lines_do_not_break_the_mappers():
    """Beklenmeyen biçimdeki çizelge ya da günlük satırı kaydı düşürmez; okunamayan parça null kalır."""
    sheet = json.dumps({"family": "periods", "format": "fifths", "final": [3, "x"], "regulation": [None, None],
                        "overtime": [1], "periods": {"period1": [1, 0], "periodX": [9, 9], "extra": [1, 1],
                                                     "period2": "1-0", "period3": [None, None]}})
    score = schema.event_from_row(_row(sport="basketball", scores_json=sheet)).score.to_dict()
    assert score == {"family": "periods", "home": None, "away": None, "format": None,
                     "periods": [{"number": 1, "home": 1, "away": 0}], "regulation": None, "overtime": None,
                     "final": {"home": 3, "away": None}, "penalties": None}
    assert schema.event_from_row(_row(scores_json="[1, 2]")).score.family == "football"  # nesne değil: boş çizelge

    row = ChangeRow(seq=1, ts=1790856000, event_id=9, sport="tennis", tournament_id=3, status_regressed=True,
                    fields=("winnerCode",), segment="score_changes.jsonl",
                    row={"changed": {"winnerCode": [1], "status.code": "100"}, "status_class": ["completed"],
                         "tier_hint": 1, "start_ts": "soon"})
    record = schema.change_from_row(row).to_dict()
    assert record["fields"] == [{"path": "winnerCode", "old": None, "new": None},
                                {"path": "status.code", "old": None, "new": None}]
    assert (record["old_status_class"], record["new_status_class"], record["tier_hint"]) == (None, None, None)
    assert (record["start_utc"], record["seconds_after_start"]) == (None, None)
    assert check(record, "Change") == []

    with pytest.raises(ValueError):  # gösterilemeyen zaman boş metne dönmez
        schema.change_from_row(dataclasses.replace(row, ts=10 ** 20))
    with pytest.raises(ValueError):
        schema.live_event_from_record(StreamRecord(seq=1, stream="live", ts=float("inf"), type="live.stuck", data={}))


def test_entity_mappers_need_an_id():
    assert schema.sport_from_row({"name": "Football"}) is None
    assert schema.category_from_row({"name": "England"}) is None
    assert schema.tournament_from_row({"name": "Premier League"}) is None
    assert schema.season_from_row({"id": 1}) is None  # turnuvası bilinmeyen sezon
    assert schema.participant_from_row({"name": "Arsenal"}) is None
    assert mappers.participant_type(7) == "other" and mappers.participant_type(None) is None


def test_registered_sports_are_the_registry():
    sports = [record.to_dict() for record in schema.registered_sports()]
    assert sports == [{"slug": spec.slug, "name": spec.name, "id": None, "score_family": spec.score_family}
                      for spec in SPORTS]
    assert schema.sport_from_spec(SPORTS[0], sport_id=1).id == 1
    # kayıtlı olmayan spor: ad yükten, skor ailesi yok
    assert schema.sport_from_row({"slug": "Waterpolo", "id": 24, "name": "Waterpolo"}).to_dict() == {
        "slug": "waterpolo", "name": "Waterpolo", "id": 24, "score_family": None}
    assert schema.sport_from_row({"slug": "tennis"}).to_dict() == {
        "slug": "tennis", "name": "Tennis", "id": None, "score_family": "sets"}


# --- Store satırları ----------------------------------------------------------------------------------

def test_store_rows_map_to_the_golden_records(canon: Store):
    records = _store_records(canon)

    assert records == _golden("store.json", lambda: records)
    kinds = {"event": "Event", "slice": "Slice", "change": "Change", "tournament": "Tournament",
             "season": "Season", "participant": "Participant", "live_event": "LiveEvent"}
    assert {key.split("/")[0] for key in records} == set(kinds)
    for key, record in records.items():
        assert check(record, kinds[key.split("/")[0]]) == [], key


def test_event_row_and_derived_columns_give_the_same_record(canon: Store):
    """Satır sınıfı ve aynı sütunları taşıyan sözlük aynı kaydı verir."""
    rows = list(canon.events.iter(EventQuery()))
    assert len(rows) == 35 and all(isinstance(row, EventRow) for row in rows)
    for row in rows:
        assert schema.event_from_row(dataclasses.asdict(row)) == schema.event_from_row(row)


def test_store_events_by_hand(canon: Store):
    """Kanonik dizinden elle denetlenmiş üç kayıt: detayı olan, yalnızca listeden bilinen, geri dönmüş."""
    arsenal = schema.event_from_row(canon.events.get(sf.event_id(sf.PL_ARS))).to_dict()
    assert arsenal["tournament_id"] == 17 and arsenal["season_id"] == 96668
    assert arsenal["stage"] == {"id": None, "name": "Premier League"}
    assert arsenal["round"] == {"number": 1, "name": None, "slug": None}
    assert [arsenal["participants"][side]["name"] for side in ("home", "away")] == ["Arsenal", "Chelsea"]
    assert arsenal["status"]["class"] == "completed" and arsenal["score"]["family"] == "football"
    assert arsenal["quality"]["source"] == "event" and arsenal["quality"]["tier_hint"] is True

    fixture = schema.event_from_row(canon.events.get(sf.event_id(sf.PL_FUTURE))).to_dict()
    assert fixture["quality"] == {"source": "listing", "observed_at_utc": None, "change_ts": None,
                                  "settlement": "open", "provisional": False, "tier_hint": True, "stale": False,
                                  "status_regressed": False}
    assert fixture["status"]["class"] == "not_started" and fixture["winner"] is None
    assert fixture["score"] == {"family": "football", "home": None, "away": None, "half_time": None,
                                "regulation": None, "after_extra_time": None, "penalties": None}

    regressed = schema.event_from_row(canon.events.get(sf.event_id(sf.NBA_VOID))).to_dict()
    assert regressed["status"]["class"] == "void"
    assert regressed["quality"]["status_regressed"] is True and regressed["quality"]["stale"] is True


def test_slices_by_hand(canon: Store):
    event_id = sf.event_id(sf.PL_ARS)
    info = canon.events.slice(event_id, "statistics")
    assert isinstance(info, SliceInfo)
    payload = canon.events.payload(event_id, "statistics")

    bare = schema.slice_from_info(info).to_dict()
    assert bare == {"owner_kind": "event", "owner_id": event_id, "key": "statistics", "sub": None, "state": "ok",
                    "has_payload": True, "fetched_at_utc": "2026-09-30T12:00:00Z",
                    "checked_at_utc": "2026-09-30T12:00:00Z", "error": None, "payload": None}
    full = schema.slice_from_info(info, payload=payload).to_dict()
    assert full["payload"] == payload and {**full, "payload": None} == bare  # ham yük olduğu gibi

    missing = schema.slice_from_info(canon.events.slice(event_id, "odds_all", "1")).to_dict()
    assert (missing["state"], missing["sub"], missing["has_payload"]) == ("not_requested", "1", False)

    failed = dataclasses.replace(info, state="error", has_payload=False, fetched_at=None, error=StoreSliceError(
        reason="403", http_status=403, at=dt.datetime(2026, 10, 1, 12, tzinfo=dt.timezone.utc), count=3))
    assert schema.slice_from_info(failed).to_dict()["error"] == {
        "reason": "403", "http_status": 403, "at_utc": "2026-10-01T12:00:00Z", "count": 3}
    with pytest.raises(ValueError):
        schema.slice_from_info(dataclasses.replace(info, state="skipped"))


def test_changes_by_hand(canon: Store):
    first, second = canon.changes.list()
    assert isinstance(first, ChangeRow)

    record = schema.change_from_row(first).to_dict()
    assert record == {
        "seq": 1, "recorded_at_utc": "2026-09-15T13:10:00Z", "event_id": 17099711, "sport": "football",
        "tournament_id": 17, "start_utc": "2026-09-15T04:00:00Z",
        "seconds_after_start": 1789477727 - 1789444800,  # SofaScore'un değişiklik anı - başlangıç
        "old_status_class": "completed", "new_status_class": "completed",
        "old_change_ts": 1789474674, "new_change_ts": 1789477727, "status_regressed": False, "tier_hint": True,
        "fields": [{"path": f"awayScore.{key}", "old": 0, "new": 1}
                   for key in ("current", "display", "normaltime", "period1")],
    }
    regressed = schema.change_from_row(second).to_dict()
    assert regressed["status_regressed"] is True
    assert (regressed["old_status_class"], regressed["new_status_class"]) == ("completed", "void")
    assert {"path": "status.type", "old": "finished", "new": "canceled"} in regressed["fields"]
    assert {"path": "winnerCode", "old": 1, "new": None} in regressed["fields"]
    assert [item["path"] for item in regressed["fields"]] == list(second.fields)


def test_change_without_a_readable_line_keeps_the_index_columns():
    row = ChangeRow(seq=5, ts=1790856000, event_id=9, sport=None, tournament_id=None, status_regressed=False,
                    fields=("status.code", "winnerCode"), row={}, segment="score_changes.jsonl")
    record = schema.change_from_row(row).to_dict()

    assert record["fields"] == [{"path": "status.code", "old": None, "new": None},
                                {"path": "winnerCode", "old": None, "new": None}]
    assert (record["start_utc"], record["seconds_after_start"], record["tier_hint"]) == (None, None, None)
    assert check(record, "Change") == []
    # SofaScore değişiklik anı vermediyse ölçü, değişikliğin görüldüğü andır (src/refresh.change_row)
    timed = dataclasses.replace(row, row={"start_ts": 1790856000 - 600, "new_change_ts": 0, "old_change_ts": 0})
    assert schema.change_from_row(timed).seconds_after_start == 600
    assert schema.change_from_row(timed).new_change_ts is None


def test_live_events_are_the_stream_envelope(canon: Store):
    first, second = canon.streams.read(after=0).events
    assert isinstance(first, StreamRecord)

    envelope = schema.live_event_from_record(first).to_dict()
    assert list(envelope) == ["stream", "seq", "type", "ts", "event_id", "sport", "tournament_id", "source", "data"]
    assert envelope == {
        "stream": "live", "seq": first.seq, "type": "live.status_changed", "ts": "2026-10-01T12:00:00.123Z",
        "event_id": sf.event_id(sf.PL_ARS), "sport": "football", "tournament_id": 17, "source": "poll",
        "data": dict(first.data),
    }
    system = schema.live_event_from_record(second).to_dict()
    assert (system["stream"], system["event_id"], system["sport"], system["ts"]) == (
        "system", None, None, "2026-10-01T12:00:01.000Z")
    assert system["data"] == {"reason": "403"}


# --- JSON Schema --------------------------------------------------------------------------------------

def test_json_schema_matches_the_golden_document():
    document = schema.json_schema()

    assert document == _golden("json_schema.json", schema.json_schema)
    assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert document["$id"] == "sofascore.data/1" and document["x-schema-version"] == 1
    assert list(document["$defs"]) == [model.__name__ for model in schema.MODELS]
    assert json.loads(json.dumps(document)) == document


@pytest.mark.parametrize("model", schema.MODELS, ids=lambda model: model.__name__)
def test_model_schema_follows_the_contract_rules(model):
    definition = schema.json_schema()["$defs"][model.__name__]
    names = [models.json_name(item) for item in dataclasses.fields(model)]

    assert definition["type"] == "object" and definition["description"] == model.SUMMARY
    assert definition["required"] == names == list(definition["properties"])  # her alan her kayıtta vardır
    assert "additionalProperties" not in definition  # alan eklemek serbesttir
    for item in dataclasses.fields(model):
        prop = definition["properties"][models.json_name(item)]
        assert prop["description"] == item.metadata["doc"] and prop["x-source"] == item.metadata["source"]
        assert prop.get("x-unit", "") == item.metadata["unit"]
        if item.metadata["open"]:
            assert "enum" not in prop and prop["examples"]  # açık sayım: bilinmeyen değer kaydı bozmaz
        if item.metadata["format"]:
            assert prop["format"] == "date-time"


def test_schema_types_by_hand():
    defs = schema.json_schema()["$defs"]

    assert defs["Event"]["properties"]["id"]["type"] == "integer"
    assert defs["Event"]["properties"]["sport"]["type"] == ["string", "null"]
    assert defs["Event"]["properties"]["winner"]["enum"] == ["home", "away", "draw", None]
    assert defs["Event"]["properties"]["score"]["oneOf"] == [
        {"$ref": f"#/$defs/{name}"} for name in ("FootballScore", "PeriodsScore", "SetsScore", "PlainScore")]
    assert defs["Event"]["properties"]["stage"]["anyOf"] == [{"$ref": "#/$defs/Stage"}, {"type": "null"}]
    assert defs["Status"]["properties"]["class"]["enum"] == [member.value for member in StatusClass]
    assert defs["FootballScore"]["properties"]["family"]["const"] == "football"
    assert defs["PlainScore"]["properties"]["family"]["type"] == "null"
    assert defs["PeriodsScore"]["properties"]["periods"]["items"] == {"$ref": "#/$defs/PeriodScore"}
    assert defs["SetsScore"]["properties"]["match_tiebreak"]["type"] == "boolean"
    assert defs["Quality"]["properties"]["settlement"]["enum"] == ["open", "provisional", "final"]
    assert defs["Quality"]["properties"]["observed_at_utc"]["format"] == "date-time"
    assert defs["Slice"]["properties"]["state"]["enum"] == ["ok", "empty", "error", "not_requested"]
    assert "type" not in defs["Slice"]["properties"]["payload"]  # herhangi bir JSON değeri
    assert defs["LiveEvent"]["properties"]["data"]["type"] == "object"
    assert defs["LiveEvent"]["properties"]["type"]["examples"][0] == "live.status_changed"
    assert defs["Sport"]["properties"]["score_family"] == {
        "type": ["string", "null"], "examples": ["football", "periods", "sets"],
        "description": defs["Sport"]["properties"]["score_family"]["description"],
        "x-source": "sport registry (`src/sports.py`)",
    }


def test_schema_rejects_what_the_contract_forbids(golden_events):
    record = golden_events["football/F2_penalties__16950622"]
    assert check(record, "Event") == []

    def broken(path: str, value: Any = dataclasses.MISSING) -> List[str]:
        copy = json.loads(json.dumps(record))
        node = copy
        *parents, last = path.split(".")
        for key in parents:
            node = node[key]
        if value is dataclasses.MISSING:
            del node[last]
        else:
            node[last] = value
        return check(copy, "Event")

    assert broken("slug") == ["$: missing 'slug'"]  # null olabilir ama eksik olamaz
    assert broken("id", "16950622") and broken("id", None) and broken("id", True)
    assert broken("status.class", "finished")  # kapalı sayım
    assert broken("winner", "1") and broken("quality.settlement", "settled")
    assert broken("start_utc", "2026-09-15 18:30:00") and broken("start_utc", "") and broken("start_utc", 1789497000)
    assert broken("quality.provisional", None) and broken("participants", None)
    assert broken("score.family", "sets")  # ailesinin yapısında olmayan skor
    assert broken("score.penalties", [7, 6])
    # serbest olanlar: bilinmeyen alan, bilinmeyen SofaScore durum tipi
    assert broken("status.type", "willcontinue") == [] and broken("extra_field", 1) == []


def test_record_schema_and_describe():
    document = schema.record_schema("Change")
    assert document["$ref"] == "#/$defs/Change" and document["$id"] == "sofascore.data/1/Change"
    assert document["$defs"] == schema.json_schema()["$defs"]
    with pytest.raises(KeyError):
        schema.record_schema("Odds")

    described = schema.describe()
    assert described == {"id": "sofascore.data/1", "version": 1,
                         "records": [model.__name__ for model in schema.RECORDS],
                         "event_envelope": "sofascore.event/1", "schema": schema.json_schema()}
    with pytest.raises(TypeError):
        schema_doc.type_schema(List[int], {})
    with pytest.raises(TypeError):
        schema_doc.type_schema(bytes, {})


# --- belge: docs/design/04-schema-v1.md -----------------------------------------------------------------

def _type_text(annotation: Any, meta: Mapping[str, Any]) -> Tuple[str, bool]:
    """Belgedeki "Type" hücresi ve alanın null olup olamayacağı."""
    names = {int: "integer", str: "string", bool: "boolean", float: "number"}
    if annotation is Any:
        return "any JSON value", True
    if annotation is type(None):
        return "null", True
    if annotation in names:
        known = ", ".join(f"`{value}`" for value in meta.get("known", ()))
        return (f"{names[annotation]}, open set: {known}" if known else names[annotation]), False
    if isinstance(annotation, type) and issubclass(annotation, models.Model):
        return f"[{annotation.__name__}](#{annotation.__name__.lower()})", False
    origin, args = typing.get_origin(annotation), typing.get_args(annotation)
    if origin is typing.Literal:
        values = ", ".join(f"`{value}`" for value in args)
        if len(args) == 1 and not meta.get("open"):
            return f"constant {values}", False
        return (f"string, open set: {values}" if meta.get("open") else f"string, one of {values}"), False
    if origin is typing.Union:
        members = [arg for arg in args if arg is not type(None)]
        text = " or ".join(_type_text(member, meta)[0] for member in members)
        return text, len(members) != len(args)
    if origin is tuple:
        return f"array of {_type_text(args[0], {})[0]}", False
    return "object", False


def render_fields(model: type) -> str:
    """Modelin belgedeki alan tablosu."""
    lines = ["| Field | Type | Null | Unit | Source | Meaning |", "|---|---|---|---|---|---|"]
    for name, annotation, meta in schema_doc.model_fields(model):
        text, nullable = _type_text(annotation, meta)
        lines.append(f"| `{name}` | {text} | {'yes' if nullable else 'no'} | {meta['unit']} | {meta['source']} "
                     f"| {meta['doc']} |")
    return "\n".join(lines)


def _doc_blocks(text: str) -> Dict[str, str]:
    return {name: body.strip("\n") for name, body in
            re.findall(r"<!-- fields:(\w+) -->\n(.*?)<!-- /fields:\1 -->", text, flags=re.S)}


def test_document_field_tables_are_generated_from_the_models():
    """Belgedeki her alan tablosu modelden üretilmiş halinin aynısıdır: belge ile kod birbirinden ayrılamaz."""
    text = DOC.read_text(encoding="utf-8")
    if REGEN_DOC:
        for model in schema.MODELS:
            text, count = re.subn(
                rf"(<!-- fields:{model.__name__} -->\n).*?(<!-- /fields:{model.__name__} -->)",
                lambda match, model=model: match.group(1) + render_fields(model) + "\n" + match.group(2),
                text, flags=re.S)
            assert count == 1, model.__name__
        DOC.write_text(text, encoding="utf-8", newline="\n")
    blocks = _doc_blocks(text)

    assert sorted(blocks) == sorted(model.__name__ for model in schema.MODELS)
    for model in schema.MODELS:
        assert blocks[model.__name__] == render_fields(model), model.__name__
        assert f"### {model.__name__}\n" in text or f"## {model.__name__}\n" in text or \
            f"#### {model.__name__}\n" in text, f"no heading for {model.__name__}"


def test_document_states_the_version_and_its_examples_are_valid():
    text = DOC.read_text(encoding="utf-8")

    assert f"`schema_version` is **{schema.SCHEMA_VERSION}**" in text
    assert f"`{schema.SCHEMA_ID}`" in text and f"`{schema.EVENT_ENVELOPE_ID}`" in text
    assert "\n## 9. Open questions\n" in text
    # Belgedeki her örnek kayıt (```json example:Model) kendi modelinin şemasına uyar
    examples = re.findall(r"```json example:(\w+)\n(.*?)\n```", text, flags=re.S)
    named = {name for name, _body in examples}
    assert {model.__name__ for model in schema.RECORDS} <= named <= {model.__name__ for model in schema.MODELS}
    for name, body in examples:
        assert check(json.loads(body), name) == [], name
