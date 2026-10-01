"""
src/slices.py: sonuç tipi (Outcome) ve "bu yanıtta veri var mı" yüklemleri.

Beklenen değerler tabloya elle yazıldı ve yüklemler src/slices.py'ye taşınmadan ÖNCEKİ kodla
(MatchDataFetcher metotları, src/match_data_fetcher.py'deki SliceOutcome) doğrulandı. Aynı tablolar
IMPLEMENTATIONS'taki her uygulamaya uygulanır: MatchDataFetcher'ın eski metotları ile src.slices'taki
işlevler aynı tablodan geçer, yani aynı sonucu verir.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest

from src.exceptions import (
    APIError,
    CircuitOpenError,
    DataParsingError,
    NetworkError,
    RateLimitError,
    ResourceNotFoundError,
)
from src import match_data_fetcher, slices
from src.match_data_fetcher import SLICE_EMPTY, SLICE_FAILED, SLICE_OK, MatchDataFetcher, SliceOutcome
from src.slices import SLICE_SKIPPED, Outcome

FIXTURES = Path(__file__).parent / "fixtures" / "status"

# Kendi yüklemi olan dilimler: anahtar → MatchDataFetcher'daki metot adı. src.slices'taki işlev aynı adı
# baştaki alt çizgi olmadan taşır.
TYPED_METHODS = {
    "statistics": "_statistics_has_data",
    "lineups": "_has_lineups_data_dict",
    "h2h": "_has_h2h_data_dict",
    "pregame_form": "_has_pregame_form_data_dict",
    "team_streaks": "_has_team_streaks_data_dict",
    "incidents": "_has_incidents_data_dict",
}
# Kendi yüklemi olmayan anahtarlar: değer "truthy" ise veri var sayılır
GENERIC_KEYS = ("basic", "point_by_point", "graph")
ALL_KEYS = tuple(TYPED_METHODS) + GENERIC_KEYS

# Yüklemler örnek durumuna bakmaz; kurucu (dizin tarama, ConfigManager) burada gereksiz
_FETCHER = MatchDataFetcher.__new__(MatchDataFetcher)

Predicate = Callable[[str, Dict[str, Any]], bool]


def _method_dispatch(key: str, d: Dict[str, Any]) -> bool:
    return _FETCHER.match_detail_slice_present(key, d)


def _method_direct(key: str, d: Dict[str, Any]) -> bool:
    name = TYPED_METHODS.get(key)
    return getattr(_FETCHER, name)(d) if name else _FETCHER.match_detail_slice_present(key, d)


def _function_direct(key: str, d: Dict[str, Any]) -> bool:
    name = TYPED_METHODS.get(key)
    return getattr(slices, name.lstrip("_"))(d) if name else slices.match_detail_slice_present(key, d)


IMPLEMENTATIONS: List[Tuple[str, Predicate]] = [
    ("method-dispatch", _method_dispatch),
    ("method-direct", _method_direct),
    ("function-dispatch", slices.match_detail_slice_present),
    ("function-direct", _function_direct),
]

# İçinde veri olan gerçekçi yanıtlar (tests/test_data_integrity.py'deki PRESENT ile aynı biçimler)
PRESENT: Dict[str, Any] = {
    "basic": {"id": 4242, "status": {"code": 100, "type": "finished"}},
    "statistics": {"statistics": [{"period": "ALL", "groups": [{"statisticsItems": [{"key": "shots"}]}]}]},
    "team_streaks": {"general": [{"name": "Wins", "value": "3"}]},
    "pregame_form": {"homeTeam": {"form": ["W"]}, "awayTeam": {"form": ["L"]}},
    "h2h": {"teamDuel": {"homeWins": 1, "awayWins": 0, "draws": 0}},
    "lineups": {"home": {"players": [{"player": {"id": 1}}]}, "away": {"players": []}},
    "incidents": {"incidents": [{"incidentType": "goal"}]},
    "point_by_point": {"pointByPoint": [{"games": []}]},
    "graph": {"graphPoints": [{"minute": 1, "value": 3}]},
}

_STAT_ITEMS = [{"statisticsItems": [{"key": "shots"}]}]

# (anahtar, yanıt gövdesi, beklenen). Yüklem {anahtar: gövde} sözlüğüyle çağrılır.
BODY_CASES: List[Tuple[str, Any, bool]] = [
    # --- statistics: ALL periyodunda (yoksa ilk periyotta) en az bir dolu grup
    ("statistics", {}, False),
    ("statistics", [], False),
    ("statistics", {"statistics": []}, False),
    ("statistics", {"statistics": None}, False),
    ("statistics", {"statistics": [{"period": "ALL", "groups": []}]}, False),
    ("statistics", {"statistics": [{"period": "ALL"}]}, False),
    ("statistics", {"statistics": [{"period": "ALL", "groups": [{"statisticsItems": []}]}]}, False),
    ("statistics", {"statistics": [{"period": "ALL", "groups": [{"groupName": "x"}]}]}, False),
    ("statistics", {"statistics": [{"period": "ALL", "groups": _STAT_ITEMS}]}, True),
    ("statistics", [{"period": "ALL", "groups": _STAT_ITEMS}], True),  # sarmalayıcısız liste
    # ALL yoksa ilk periyot sayılır, sonrakiler sayılmaz
    ("statistics", [{"period": "1ST", "groups": _STAT_ITEMS}, {"period": "2ND"}], True),
    ("statistics", [{"period": "1ST"}, {"period": "2ND", "groups": _STAT_ITEMS}], False),
    # ALL varsa yalnızca ona bakılır
    ("statistics", [{"period": "1ST"}, {"period": "ALL", "groups": _STAT_ITEMS}], True),
    ("statistics", [{"period": "1ST", "groups": _STAT_ITEMS}, {"period": "ALL", "groups": []}], False),
    # --- lineups: iki taraftan birinde en az bir oyuncu
    ("lineups", {}, False),
    ("lineups", [], False),
    ("lineups", ["x"], False),
    ("lineups", {"confirmed": True}, False),
    ("lineups", {"home": {"players": []}, "away": {"players": []}}, False),
    ("lineups", {"home": {"players": "abc"}}, False),
    ("lineups", {"home": {"formation": "4-4-2"}, "away": None}, False),
    ("lineups", {"home": [1], "away": {"players": [1]}}, True),
    ("lineups", {"home": {"players": [{"player": {"id": 1}}]}}, True),
    # --- h2h: teamDuel sayılarından biri (0 dahil) ya da bir maç listesi
    ("h2h", {}, False),
    ("h2h", ["x"], False),
    ("h2h", {"teamDuel": None, "managerDuel": None}, False),
    ("h2h", {"teamDuel": {"homeWins": None, "awayWins": None, "draws": None}}, False),
    ("h2h", {"managerDuel": {"homeWins": 1}}, False),
    ("h2h", {"teamDuel": {"homeWins": 0}}, True),
    ("h2h", {"teamDuel": {"draws": 2}}, True),
    ("h2h", {"matches": [1]}, True),
    ("h2h", {"teamDuel": None, "events": [1]}, True),
    ("h2h", {"teamDuel": {"matches": [1]}}, True),
    ("h2h", {"matches": [], "events": []}, False),
    # --- pregame_form: bir takımda form listesi ya da position / value / avgRating
    ("pregame_form", {}, False),
    ("pregame_form", ["x"], False),
    ("pregame_form", {"label": "x"}, False),
    ("pregame_form", {"homeTeam": {"form": []}, "awayTeam": {"form": []}}, False),
    ("pregame_form", {"homeTeam": {"form": ["W"]}}, True),
    ("pregame_form", {"homeTeam": {"position": 0}}, True),
    ("pregame_form", {"awayTeam": {"value": "12"}}, True),
    ("pregame_form", {"homeTeam": "x", "awayTeam": {"avgRating": "6.9"}}, True),
    # --- team_streaks: yalnızca "general" listesi sayılır
    ("team_streaks", {}, False),
    ("team_streaks", {"general": [], "head2head": []}, False),
    ("team_streaks", {"head2head": [{"name": "Wins"}]}, False),
    ("team_streaks", {"general": {"name": "Wins"}}, False),
    ("team_streaks", {"general": [{"name": "Wins"}]}, True),
    # --- incidents: {"incidents": [...]} ya da doğrudan liste
    ("incidents", {}, False),
    ("incidents", [], False),
    ("incidents", {"incidents": []}, False),
    ("incidents", {"incidents": {"a": 1}}, False),
    ("incidents", "abc", False),
    ("incidents", {"incidents": [{"incidentType": "goal"}]}, True),
    ("incidents", [{"incidentType": "goal"}], True),
    # --- kendi yüklemi olmayan anahtarlar: bool(değer)
    ("basic", {}, False),
    ("basic", {"id": 1}, True),
    ("point_by_point", {}, False),
    ("point_by_point", [], False),
    ("point_by_point", {"pointByPoint": []}, True),  # içi boş ama sözlük dolu: veri var sayılır
    ("graph", 0, False),
    ("graph", [1], True),
]

# Beklenmeyen biçimde gövde: yüklem AttributeError ile düşer (taşıma bunu değiştirmez)
MALFORMED_CASES: List[Tuple[str, Any]] = [
    ("statistics", "abc"),
    ("statistics", 0),  # yalnızca None "yok" sayılır; 0 sözlük gibi okunmaya çalışılır
    ("statistics", ["x"]),
    ("statistics", [None, {"period": "1ST", "groups": _STAT_ITEMS}]),  # ALL yokken ilk öğe None
    ("statistics", {"statistics": [{"period": "ALL", "groups": ["x"]}]}),
    ("h2h", {"teamDuel": ["x"]}),
    ("team_streaks", ["x"]),
    ("team_streaks", "abc"),
]


def _impl_params(cases: List[tuple]) -> List[Any]:
    return [
        pytest.param(impl, *case, id=f"{name}:{case[0]}:{index}")
        for name, impl in IMPLEMENTATIONS
        for index, case in enumerate(cases)
    ]


@pytest.mark.parametrize("impl,key,body,expected", _impl_params(BODY_CASES))
def test_presence_of_hand_made_bodies(impl: Predicate, key: str, body: Any, expected: bool):
    assert impl(key, {key: body}) is expected


@pytest.mark.parametrize("impl,key,body", _impl_params(MALFORMED_CASES))
def test_malformed_body_raises_attribute_error(impl: Predicate, key: str, body: Any):
    with pytest.raises(AttributeError):
        impl(key, {key: body})


@pytest.mark.parametrize("name,impl", IMPLEMENTATIONS, ids=[name for name, _ in IMPLEMENTATIONS])
@pytest.mark.parametrize("key", ALL_KEYS)
def test_missing_or_null_slice_is_absent(name: str, impl: Predicate, key: str):
    assert impl(key, {}) is False
    assert impl(key, {key: None}) is False
    # Yalnızca kendi anahtarına bakar: başka dilimlerin verisi sonucu değiştirmez
    others = {k: v for k, v in PRESENT.items() if k != key}
    assert impl(key, others) is False
    assert impl(key, dict(others, **{key: None})) is False


@pytest.mark.parametrize("name,impl", IMPLEMENTATIONS, ids=[name for name, _ in IMPLEMENTATIONS])
@pytest.mark.parametrize("key", ALL_KEYS)
def test_realistic_body_is_present(name: str, impl: Predicate, key: str):
    assert impl(key, {key: PRESENT[key]}) is True
    assert impl(key, PRESENT) is True


def _status_fixtures() -> List[Path]:
    return sorted(FIXTURES.glob("*/*.json"))


def test_status_fixtures_exist():
    assert len(_status_fixtures()) > 100


@pytest.mark.parametrize("path", _status_fixtures(), ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_status_fixture_bodies(path: Path):
    """
    Gerçek /event yanıtı her dilimin gövdesi olarak verilir: "basic" (ve yüklemi olmayan anahtarlar) için veri
    vardır; kendi yüklemi olan dilimler, o dilimin biçiminde olmayan dolu bir gövdeyi "yok" sayar.
    """
    event = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(event, dict) and event
    for name, impl in IMPLEMENTATIONS:
        for key in ALL_KEYS:
            expected = key in GENERIC_KEYS
            assert impl(key, {key: event}) is expected, (name, key)
            # Maçın kendisi yalnızca "basic"te durduğunda diğer dilimler yok
            assert impl(key, {"basic": event}) is (key == "basic"), (name, key)


# --- tipli sonuç ---------------------------------------------------------------------------

# (hata, durum, neden, HTTP kodu)
ERROR_CASES: List[Tuple[BaseException, str, str, Optional[int]]] = [
    (ResourceNotFoundError("x"), SLICE_EMPTY, "404", 404),
    (RateLimitError(status_code=429, url="/x"), SLICE_FAILED, "429", 429),
    (RateLimitError(status_code=503, url="/x"), SLICE_FAILED, "5xx", 503),
    (APIError("HTTP 403 Forbidden", status_code=403), SLICE_FAILED, "403", 403),
    (APIError("HTTP 500", status_code=500), SLICE_FAILED, "5xx", 500),
    # ResourceNotFoundError olmayan 404 kesin "yok" sayılmaz
    (APIError("HTTP 404", status_code=404), SLICE_FAILED, "404", 404),
    (APIError("durum kodu yok"), SLICE_FAILED, "other", None),
    (NetworkError("İstek başarısız: curl: (28) Operation timed out"), SLICE_FAILED, "timeout", None),
    (NetworkError("İstek başarısız: curl: (7) Failed to connect"), SLICE_FAILED, "network", None),
    (DataParsingError("JSON ayrıştırma hatası"), SLICE_FAILED, "parse", None),
    # Açık devre kesici bugün "başarısız" sonuçtur (neden: breaker)
    (CircuitOpenError("/x"), SLICE_FAILED, "breaker", None),
    (ValueError("boom"), SLICE_FAILED, "other", None),
    (RuntimeError("read timed out"), SLICE_FAILED, "timeout", None),
]


@pytest.mark.parametrize(
    "exc,status,reason,http_status", ERROR_CASES, ids=[f"{type(c[0]).__name__}-{c[2]}" for c in ERROR_CASES]
)
def test_outcome_from_error(exc: BaseException, status: str, reason: str, http_status: Optional[int]):
    outcome = SliceOutcome.from_error(exc)
    assert isinstance(outcome, SliceOutcome)
    assert (outcome.status, outcome.reason, outcome.http_status) == (status, reason, http_status)
    assert outcome.data is None
    assert outcome.failed is (status == SLICE_FAILED)


def test_outcome_status_values():
    assert (SLICE_OK, SLICE_EMPTY, SLICE_FAILED) == ("ok", "empty", "failed")


def test_outcome_defaults_and_positional_order():
    bare = SliceOutcome(SLICE_OK)
    assert (bare.status, bare.data, bare.reason, bare.http_status, bare.failed) == ("ok", None, None, None, False)
    full = SliceOutcome(SLICE_FAILED, {"a": 1}, "429", 429)
    assert (full.status, full.data, full.reason, full.http_status, full.failed) == ("failed", {"a": 1}, "429", 429, True)
    assert SliceOutcome(SLICE_EMPTY, reason="empty").failed is False


def test_outcome_is_frozen_and_compares_by_value():
    outcome = SliceOutcome(SLICE_EMPTY, reason="404", http_status=404)
    assert outcome == SliceOutcome(SLICE_EMPTY, reason="404", http_status=404)
    assert outcome != SliceOutcome(SLICE_EMPTY, reason="empty")
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.status = SLICE_OK  # type: ignore[misc]


# --- src/slices.py'ye taşıma ---------------------------------------------------------------

def test_moved_names_stay_importable_from_match_data_fetcher():
    assert slices.SliceOutcome is Outcome
    assert match_data_fetcher.SliceOutcome is Outcome
    for name in ("SLICE_OK", "SLICE_EMPTY", "SLICE_FAILED"):
        assert getattr(match_data_fetcher, name) is getattr(slices, name)
    for method in list(TYPED_METHODS.values()) + ["match_detail_slice_present"]:
        assert callable(getattr(MatchDataFetcher, method))
        assert callable(getattr(slices, method.lstrip("_")))


def test_outcome_fields_and_new_defaults():
    names = [f.name for f in dataclasses.fields(Outcome)]
    # İlk dört alan eski SliceOutcome'ın sırasıdır (konumla kurulan sonuçlar bozulmasın)
    assert names == ["status", "data", "reason", "http_status", "fetched_at", "via", "meta"]
    bare = Outcome(SLICE_OK)
    assert (bare.fetched_at, bare.via, bare.meta) == (None, None, None)
    # Yeni alanlar verilmediğinde eşitlik eskisi gibidir
    assert Outcome(SLICE_EMPTY, reason="empty") == SliceOutcome(SLICE_EMPTY, None, "empty", None)


def test_outcome_carries_new_fields():
    at = dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.timezone.utc)
    outcome = Outcome(SLICE_OK, data={"events": []}, fetched_at=at, via="bridge", meta={"complete": True})
    assert (outcome.fetched_at, outcome.via, outcome.meta) == (at, "bridge", {"complete": True})
    assert outcome != Outcome(SLICE_OK, data={"events": []})
    assert outcome == dataclasses.replace(
        Outcome(SLICE_OK, data={"events": []}, via="bridge"), fetched_at=at, meta={"complete": True}
    )


def test_skipped_is_a_status_of_its_own():
    assert SLICE_SKIPPED == "skipped"
    skipped = Outcome(SLICE_SKIPPED, reason="not_selected")
    assert skipped.failed is False
    assert skipped.status not in (SLICE_OK, SLICE_EMPTY, SLICE_FAILED)


def test_from_error_does_not_set_new_fields():
    for exc in (ResourceNotFoundError("x"), RateLimitError(status_code=429, url="/x")):
        outcome = Outcome.from_error(exc)
        assert (outcome.fetched_at, outcome.via, outcome.meta) == (None, None, None)


_PURITY_PROBE = """
import sys
import src.slices
print(",".join(sorted(m for m in sys.modules if m == "src" or m.startswith("src."))))
print(",".join(sorted(m for m in ("pandas", "tqdm", "rich", "dotenv", "curl_cffi") if m in sys.modules)))
"""


def test_slices_module_is_pure():
    """
    src.slices import edildiğinde çekici, istek katmanı, günlükçü ve ağır kitaplıklar yüklenmez: depo ve
    servisler yüklemleri bunlara bağlanmadan çağırabilir. Temiz bir yorumlayıcıda bakılır (bu süreçte
    diğer testler hepsini zaten yüklemiş olur).
    """
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "-c", _PURITY_PROBE], cwd=root, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0].split(",") == ["src", "src.exceptions", "src.slices"]
    assert lines[1:] in ([], [""])
