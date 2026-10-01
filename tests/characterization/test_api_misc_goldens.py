"""
Karakterizasyon: veri okumayan /api uç noktalarının bugünkü yanıtları (plan maddesi G-04).

G-02 (`test_reader_goldens.py`) maç verisini okuyan uç noktaları sabitler; bu modül kalanları. Yanıtlar
`tests/snapshots/api/` altında durur:

  <dizin>.leagues.json           GET /api/leagues (spor: kayıtlı, yoksa indirilmiş maçtan çıkarılır) ve
                                 GET /api/leagues/search; `tests/store_fixtures.py`'nin her veri dizini için
  <dizin>.league_seasons.json    GET /api/leagues/{id}/seasons, aynı dizinler için
  league_seasons_files.json      aynı uç nokta: sezon listesi dosyasının adı ve içeriğiyle ilgili sınır durumlar
  settings.json                  GET /api/settings: varsayılanlar, her ayar verilmiş, ayrıştırılamayan değerler
  status.json                    GET /api/status
  sports.json                    GET /api/sports
  jobs.json                      GET /api/jobs (limit) ve /api/jobs/{id}: her bitiş biçiminden bir iş
  scrape_status.json             GET /api/scrape/status: aynı işlerin canlı yansısı, aşama aşama

Bu uç noktaları taşıyan plan maddeleri (ST-09, P09, P11, RD-5, P21) dosyaları değiştirmeden geçmeli ya da her
farkı tek tek açıklamalıdır. Davranış bilerek değiştirildiyse dosyalar şöyle yeniden üretilir:

    REGEN_API_GOLDENS=1 python -m pytest tests/characterization/test_api_misc_goldens.py

Karşılaştırma katıdır: değer türü ve anahtar sırası da sayılır. Çıktıyı sabitlemek için: lig listesi ve spor
dosyası testin config dizinine yazılıp geri alınır, ayarların okuduğu her ortam değişkeni test içinde
belirlenir, iş deposu geçici bir dizine bağlanır ve saati elle ilerletilir; iş kimlikleri (uuid) oluşturulma
sırasına göre `job-1`, `job-2` … olarak kaydedilir.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import pytest
from fastapi.testclient import TestClient

import conftest
import store_fixtures as sf
from src.jobs.progress import JobProgress
from src.web.app import app
from src.web.jobs import default_db_path
from src.web.routes import common

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "snapshots" / "api"
REGENERATE = os.getenv("REGEN_API_GOLDENS") == "1"
LINE_WIDTH = 118
UNKNOWN_LEAGUE = 999
LEAGUES_FILE = os.path.join(conftest.CONFIG_DIR, "leagues.txt")
SPORTS_FILE = os.path.join(conftest.CONFIG_DIR, "league_sports.json")

client = TestClient(app)


# --- altın dosya biçimi ----------------------------------------------------------------------


def _render(obj: Any, indent: int = 0, prefix: int = 0) -> str:
    """Belirlenimli JSON, fark okunur kalsın diye: satıra sığan değer tek satırda, gerisi bir düzey açılır."""
    compact = json.dumps(obj, ensure_ascii=False)
    if not isinstance(obj, (dict, list)) or not obj or indent + prefix + len(compact) <= LINE_WIDTH:
        return compact
    pad, close = " " * (indent + 1), "\n" + " " * indent
    if isinstance(obj, dict):
        heads = [json.dumps(key, ensure_ascii=False) + ": " for key in obj]
        items = [head + _render(value, indent + 1, len(head)) for head, value in zip(heads, obj.values(), strict=True)]
        return "{\n" + ",\n".join(pad + item for item in items) + close + "}"
    return "[\n" + ",\n".join(pad + _render(value, indent + 1) for value in obj) + close + "]"


def _differences(expected: Any, actual: Any, path: str = "$") -> List[str]:
    """Farklı yollar; tür de karşılaştırılır (3 ile 3.0, true ile 1 farklıdır), sözlükte anahtar sırası da."""
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


def check_golden(name: str, data: Any) -> None:
    path = SNAPSHOT_DIR / f"{name}.json"
    text = _render(json.loads(json.dumps(data))) + "\n"
    if REGENERATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))  # her platformda LF
        return
    assert path.is_file(), f"{path.name} yok: REGEN_API_GOLDENS=1 ile üretin"
    expected = path.read_text(encoding="utf-8")
    if expected != text:
        diffs = _differences(json.loads(expected), json.loads(text)) or ["yalnızca biçim farkı (yeniden üretin)"]
        shown = "\n  ".join(diffs[:40])
        pytest.fail(f"{path.name}: {len(diffs)} fark (beklenen -> şimdiki)\n  {shown}", pytrace=False)


def test_render_round_trips_and_differences_are_strict() -> None:
    data = {"a": [1, 2.0, True, None, "x" * 200], "b": {"c": {"d": list(range(60))}}, "e": {}, "f": [], "g": [[1], {}]}
    assert json.loads(_render(data)) == data
    rows = {"rows": [{"id": i, "name": "x" * 60} for i in range(2)]}
    assert _render(rows).count("\n") == 5 and _render(rows["rows"][0]).count("\n") == 0  # satır başına bir kayıt
    assert _differences({"a": 3}, {"a": 3.0}) and _differences({"a": 1}, {"a": True})
    assert _differences({"a": 1, "b": 2}, {"b": 2, "a": 1}) == ["$: key order"]
    assert _differences({"a": 1}, {"b": 1}) == ["$: keys removed ['a'], added ['b']"]
    assert _differences([1, 2], [1]) == ["$: length 2 -> 1"]
    assert not _differences(data, json.loads(json.dumps(data)))


# --- ortam ----------------------------------------------------------------------------------


def _get(url: str) -> Dict[str, Any]:
    """Yanıtın kaydı. 422'nin gövdesi pydantic sürümüne bağlı olduğundan yalnızca kodu tutulur."""
    r = client.get(url)
    if r.status_code == 422:
        return {"status": 422}
    return {"status": r.status_code, "body": r.json()}


def _read(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _write_leagues_file(data: bytes) -> None:
    """leagues.txt'yi yazar; mtime öncekinden en az 1 sn ileridedir ki ConfigManager değişikliği kesin görsün."""
    previous = os.stat(LEAGUES_FILE).st_mtime_ns
    with open(LEAGUES_FILE, "wb") as f:
        f.write(data)
    stamp = max(time.time_ns(), previous + 1_000_000_000)
    os.utime(LEAGUES_FILE, ns=(stamp, stamp))


def _write_sports_file(data: Optional[bytes]) -> None:
    if data is None:
        with contextlib.suppress(FileNotFoundError):
            os.remove(SPORTS_FILE)
        return
    with open(SPORTS_FILE, "wb") as f:
        f.write(data)


@contextlib.contextmanager
def _configured(leagues: Dict[int, str], sports: Optional[Dict[str, Any]] = None) -> Iterator[None]:
    """
    Testin config dizinine lig listesini ve spor dosyasını yazar (`sports=None`: dosya yok); çıkışta ikisi de
    eski içeriğine döner. `tests/conftest.py` düzenlenmez.
    """
    leagues_before, sports_before = _read(LEAGUES_FILE), _read(SPORTS_FILE)
    assert leagues_before is not None
    lines = ["# League configuration file", "# Format: League Name: ID", ""]
    lines += [f"{name}: {league_id}" for league_id, name in leagues.items()]
    _write_leagues_file(("\n".join(lines) + "\n").encode("utf-8"))
    _write_sports_file(None if sports is None else json.dumps(sports).encode("utf-8"))
    try:
        yield
    finally:
        _write_leagues_file(leagues_before)
        _write_sports_file(sports_before)


def _sports_file() -> Any:
    data = _read(SPORTS_FILE)
    return None if data is None else json.loads(data)


@pytest.fixture(params=sf.FIXTURE_NAMES)
def fx(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sf.LegacyFixture:
    """G-02'nin fabrikasıyla taze kurulmuş veri dizini; web katmanı (DATA_DIR) ona çevrilir."""
    fixture = sf.build_fixture(request.param, tmp_path / "data")
    monkeypatch.setenv("DATA_DIR", str(fixture.data_dir))
    return fixture


# --- GET /api/leagues, /api/leagues/search ----------------------------------------------------


def test_api_leagues(fx: sf.LegacyFixture) -> None:
    """
    Spor `config/league_sports.json`'dan gelir; orada yoksa ligin indirilmiş bir maçından (`match_details/
    <lig id>_*/*/*/basic.json`) okunur ve dosyaya yazılır: GET isteğinin yan etkisi, `sports_file_after` ile
    kaydedilir. `/api/leagues/search` yalnızca config'e bakar ve sporu hiç doldurmaz.
    """
    ids = list(fx.leagues)
    golden: Dict[str, Any] = {}
    with _configured(fx.leagues):
        first = _get("/api/leagues")
        golden["no sports file"] = {"response": first, "sports_file_after": _sports_file()}
        assert _get("/api/leagues") == first, "ikinci istek (spor artık dosyadan) aynı yanıtı vermeli"

    # Kayıtlı değer veriyle çelişse de kazanır; tanınmayan değer yok sayılır ve veriden yeniden çıkarılır
    stored: Dict[str, Any] = {str(ids[0]): "Tennis", str(UNKNOWN_LEAGUE): "basketball", "not-an-id": "football"}
    if len(ids) > 1:
        stored[str(ids[1])] = "curling"
    with _configured(fx.leagues, stored):
        golden["sports file with a stored, an unknown and an unconfigured entry"] = {
            "sports_file_before": stored, "response": _get("/api/leagues"), "sports_file_after": _sports_file(),
        }

    with _configured({}):
        golden["no leagues configured"] = {"response": _get("/api/leagues"), "sports_file_after": _sports_file()}

    with _configured(fx.leagues, {str(ids[0]): "football"}):
        golden["search"] = {q: _get(f"/api/leagues/search?q={q}") for q in ("LEAGUE", "la", "zz", "l")}
        golden["search"]["(q missing)"] = _get("/api/leagues/search")
    check_golden(f"{fx.name}.leagues", golden)


# --- GET /api/leagues/{id}/seasons ------------------------------------------------------------


def test_api_league_seasons(fx: sf.LegacyFixture) -> None:
    """Yapılandırılmış her lig ve bilinmeyen bir lig. Uç nokta lig listesine bakmaz, yalnızca `seasons/` dizinine."""
    golden = {f"league_id={lid}": _get(f"/api/leagues/{lid}/seasons") for lid in list(fx.leagues) + [UNKNOWN_LEAGUE]}
    golden["league_id=abc"] = _get("/api/leagues/abc/seasons")
    check_golden(f"{fx.name}.league_seasons", golden)


# (dosya adı, içerik, mtime'ın BASE_MTIME'a göre gün farkı); her lig id'si bir durumu anlatır
SEASON_FILES: Tuple[Tuple[str, bytes, int], ...] = (
    ("50_seasons.json", b'[{"id": 501, "name": "bare list"}]', 0),
    ("51_Dict_Without_Key_seasons.json", b'{"id": 511, "name": "no seasons key"}', 0),
    ("52_Truncated_seasons.json", b'{"seasons": [{"id": 521', 0),
    ("53_Older_Name_seasons.json", b'{"seasons": [{"id": 531, "name": "older file"}]}', -30),
    ("53_Newer_Name_seasons.json", b'{"seasons": [{"id": 532, "name": "newer file"}]}', 0),
    ("54_seasons.json", b'{"seasons": [{"id": 541, "name": "bare name, older"}]}', -30),
    ("54_Named_seasons.json", b'{"seasons": [{"id": 542, "name": "named, newer"}]}', 0),
    ("55_Null_seasons.json", b'{"seasons": null}', 0),
    ("56_Text_seasons.json", b'"seasons"', 0),
    ("57_Empty_seasons.json", b'{"seasons": []}', 0),
    ("58_Extra_Keys_seasons.json", b'{"league": {"id": 58}, "seasons": [{"id": 581, "year": "26/27", "x": [1]}]}', 0),
    ("59_BOM_seasons.json", b'\xef\xbb\xbf{"seasons": [{"id": 591}]}', 0),
    ("Sixty_seasons.json", b'{"seasons": [{"id": 601}]}', 0),
    ("61_seasons.csv", b"Sezon ID\n611\n", 0),
)


def test_api_league_seasons_file_forms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Hangi dosya okunur, içeriğinden ne döner: `<id>_seasons.json` adı `<id>_<ad>_seasons.json`'dan önce gelir
    (daha eski olsa da), birden çok adlı dosyadan mtime'ı en yeni olan seçilir; `seasons` anahtarı liste
    değilse boş liste ve `fetched: true`, dosya ayrıştırılamıyorsa boş liste ve `fetched: false` döner.
    """
    seasons_dir = tmp_path / "data" / "seasons"
    seasons_dir.mkdir(parents=True)
    for name, content, days in SEASON_FILES:
        (seasons_dir / name).write_bytes(content)
        os.utime(seasons_dir / name, (sf.BASE_MTIME + days * sf.DAY,) * 2)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    golden = {"files": [name for name, _content, _days in SEASON_FILES]}
    golden.update({f"league_id={lid}": _get(f"/api/leagues/{lid}/seasons") for lid in list(range(50, 62)) + [5]})
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "missing"))
    golden["league_id=50 (no data directory)"] = _get("/api/leagues/50/seasons")
    check_golden("league_seasons_files", golden)


# --- GET /api/settings, /api/status -----------------------------------------------------------

# GET /api/settings'in okuduğu her değişken. Dil kuralı (src/language.py) açık ayar yoksa sistem diline bakar:
# LC_ALL silinir ve LC_MESSAGES her senaryoda belirlenir ("C": desteklenmeyen dil), testi çalıştıran makinenin
# dili sonucu değiştirmesin diye.
SYSTEM_LANGUAGE_ENV = "LC_MESSAGES"
SETTINGS_ENV = (
    "APP_LANGUAGE", "LANGUAGE", "API_BASE_URL", "USE_PROXY", "PROXY_URL", "DATA_DIR", "USE_COLOR", "DATE_FORMAT",
    "MAX_CONCURRENT", "REQUEST_RATE_LIMIT", "WAIT_TIME_MIN", "WAIT_TIME_MAX", "REQUEST_TIMEOUT", "MAX_RETRIES",
    "RATE_LIMIT_THRESHOLD_CONSECUTIVE", "RATE_LIMIT_THRESHOLD_RATIO", "SERVER_ERROR_THRESHOLD_CONSECUTIVE",
    "FETCH_ONLY_FINISHED", "SAVE_EMPTY_ROUNDS", "REFRESH_WINDOW_HOURS", "LOG_LEVEL", "DEBUG",
)
SETTINGS_SCENARIOS: Dict[str, Dict[str, str]] = {
    "defaults: no variable set": {},
    "no variable set, Turkish system language": {SYSTEM_LANGUAGE_ENV: "tr_TR.UTF-8"},
    "every variable set": {
        SYSTEM_LANGUAGE_ENV: "tr_TR.UTF-8",
        "APP_LANGUAGE": "en", "API_BASE_URL": "https://api.sofascore.com/api/v1", "USE_PROXY": "true",
        "PROXY_URL": "http://scraper:s3cret@proxy.example.com:8080", "DATA_DIR": "/srv/sofascore/data",
        "USE_COLOR": "false", "DATE_FORMAT": "%d.%m.%Y %H:%M", "MAX_CONCURRENT": "3", "REQUEST_RATE_LIMIT": "2.5",
        "WAIT_TIME_MIN": "1", "WAIT_TIME_MAX": "2.5", "REQUEST_TIMEOUT": "30", "MAX_RETRIES": "5",
        "RATE_LIMIT_THRESHOLD_CONSECUTIVE": "7", "RATE_LIMIT_THRESHOLD_RATIO": "0.5",
        "SERVER_ERROR_THRESHOLD_CONSECUTIVE": "9", "FETCH_ONLY_FINISHED": "false", "SAVE_EMPTY_ROUNDS": "true",
        "REFRESH_WINDOW_HOURS": "24", "LOG_LEVEL": "DEBUG", "DEBUG": "true",
    },
    "other spellings": {
        "LANGUAGE": "en", "USE_PROXY": "TRUE", "PROXY_URL": "socks5://proxy.example.com:1080", "USE_COLOR": "True",
        "MAX_CONCURRENT": " 7 ", "REQUEST_RATE_LIMIT": "off", "WAIT_TIME_MIN": "0", "REQUEST_TIMEOUT": "010",
        "RATE_LIMIT_THRESHOLD_RATIO": "1", "FETCH_ONLY_FINISHED": "TRUE", "SAVE_EMPTY_ROUNDS": "True",
        "REFRESH_WINDOW_HOURS": "-5", "LOG_LEVEL": "warning", "DEBUG": "TRUE", "DATA_DIR": "relative/data",
    },
    "values that do not parse": {
        "APP_LANGUAGE": "de", "LANGUAGE": "en_US:en", "API_BASE_URL": "", "USE_PROXY": "yes",
        "PROXY_URL": "user:pw@proxy.example.com:3128", "DATA_DIR": "", "USE_COLOR": "0", "DATE_FORMAT": "",
        "MAX_CONCURRENT": "many", "REQUEST_RATE_LIMIT": "fast", "WAIT_TIME_MIN": "x", "WAIT_TIME_MAX": "",
        "REQUEST_TIMEOUT": "10.5", "MAX_RETRIES": "-", "RATE_LIMIT_THRESHOLD_CONSECUTIVE": "1e2",
        "RATE_LIMIT_THRESHOLD_RATIO": "90%", "SERVER_ERROR_THRESHOLD_CONSECUTIVE": "", "FETCH_ONLY_FINISHED": "1",
        "SAVE_EMPTY_ROUNDS": "yes", "REFRESH_WINDOW_HOURS": "soon", "LOG_LEVEL": "verbose", "DEBUG": "on",
    },
}


def _set_settings_env(patch: pytest.MonkeyPatch, values: Dict[str, str]) -> None:
    assert set(values) <= set(SETTINGS_ENV) | {SYSTEM_LANGUAGE_ENV}
    for key in SETTINGS_ENV + ("LC_ALL",):
        patch.delenv(key, raising=False)
    for key, value in {SYSTEM_LANGUAGE_ENV: "C", **values}.items():
        patch.setenv(key, value)


def test_api_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ayarlar ortam değişkenlerinden okunur (.env içe aktarılırken ortama yüklenir); dosyaya dokunulmaz."""
    golden: Dict[str, Any] = {}
    for name, values in SETTINGS_SCENARIOS.items():
        with monkeypatch.context() as patch:
            _set_settings_env(patch, values)
            golden[name] = {"env": values, "response": _get("/api/settings")}
    check_golden("settings", golden)


def test_api_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """`version` uygulamanın sürümü değil, koda yazılmış "1.0.0" değeridir."""
    golden: Dict[str, Any] = {}
    scenarios: Sequence[Tuple[str, Dict[int, str], Dict[str, str]]] = (
        ("one league, no language set", {17: "Premier League"}, {}),
        ("one league, no language set, Turkish system language", {17: "Premier League"},
         {SYSTEM_LANGUAGE_ENV: "tr_TR.UTF-8"}),
        ("three leagues, APP_LANGUAGE=en", {17: "Premier League", 8: "LaLiga", 132: "NBA"}, {"APP_LANGUAGE": "en"}),
        ("no leagues, APP_LANGUAGE=tr", {}, {"APP_LANGUAGE": "tr"}),
    )
    for name, leagues, values in scenarios:
        with monkeypatch.context() as patch, _configured(leagues):
            _set_settings_env(patch, values)
            golden[name] = _get("/api/status")
    check_golden("status", golden)


# --- GET /api/sports --------------------------------------------------------------------------


def test_api_sports() -> None:
    """Kayıt defterinin (src/sports.py) görünümü: spor eklendiğinde (SP-1 … SP-3) bu dosya bilerek yenilenir."""
    check_golden("sports", _get("/api/sports"))


# --- GET /api/jobs, /api/jobs/{id}, /api/scrape/status ----------------------------------------

JOBS_START = sf.FIXTURE_NOW  # 2026-10-01T12:00:00Z
PL = {"league_id": 17, "league_name": "Premier League"}
PL_SEASONS = ((96668, "Premier League 26/27"), (76986, "Premier League 25/26"))


class _Clock:
    """Elle ilerleyen saat: iş deposunun zaman damgaları ve JobProgress'in süre hesapları buna bakar."""

    def __init__(self, start: float) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += seconds

    def utc_now(self) -> str:
        return dt.datetime.fromtimestamp(self.now, dt.timezone.utc).replace(microsecond=0).isoformat()


class _JobScript:
    """
    Bir indirme işinin iş deposuna yaptığı çağrılar, `src/web/fetch_job.py`'deki sırayla ve aynı metinlerle;
    ağ ve indirme yok. İşin sonu (`complete`, `cancel`, `fail`) ayrı çağrılır ki araya durum kaydı girebilsin.
    """

    def __init__(self, store: Any, clock: _Clock, payload: Dict[str, Any], summary: str) -> None:
        self.store, self.clock, self.full = store, clock, payload["mode"] != "details"
        self.job_id: str = store.create_running(payload)
        self._state("Running", 0, f"Starting fetch for {summary}")
        phases = ["seasons", "matches", "details", "export"] if self.full else ["details", "export"]
        self.tracker = JobProgress(phases, lambda fields: store.update(**fields), clock=clock, wall_clock=clock)

    def _state(self, status: str, progress: int, task: str) -> None:  # fetch_job.update_state
        finished = status in ("Completed", "Failed", "Cancelled")
        self.store.update(status=status, progress=progress, current_task=task, append_log=f"[{status}] {task}",
                          finished=finished)

    def _note(self, task: str) -> None:  # fetch_job._note
        self.store.update(current_task=task, append_log=f"[Running] {task}")

    def listings(self, empty_seasons: int = 0) -> None:
        """Sezon listesi ve maç listesi aşamaları (yalnızca `full` işte)."""
        tracker = self.tracker
        tracker.start_phase("seasons", 1)
        tracker.set_context(**PL)
        self._note("Refreshing season list for league 17...")
        self.clock.tick(2)
        tracker.advance(1)
        tracker.start_phase("matches", len(PL_SEASONS))
        for index, (season_id, season_name) in enumerate(PL_SEASONS):
            tracker.set_context(**PL, season_name=season_name)
            self._note(f"Fetching matches: league 17, season {season_id}")
            self.clock.tick(3)
            tracker.advance(index + 1)
        self.store.update(schedule_empty_seasons=empty_seasons)

    def details(self, total: int, done: int, failed: Sequence[str] = (), refreshed: int = 0) -> None:
        """Detay aşaması: `total` maçtan `done` tanesi işlenir; `failed` listesindekiler başarısız sayılır."""
        tracker = self.tracker
        tracker.start_phase("details", 0)
        self._note("Checking which matches need details...")
        tracker.set_total(total)
        tracker.set_context(**PL)
        self._note(f"Fetching match details: league 17 ({total} matches)…")
        for n in range(1, done + 1):
            self.clock.tick(2)
            if n <= len(failed):
                tracker.add_failed(failed[n - 1], 17)
            if n <= refreshed:
                tracker.add_refreshed(str(16837335 + n), changed=n == 1)
            tracker.advance(n)

    def breaker(self, reason: str, what: str) -> None:  # fetch_job._report_breaker
        self.tracker.breaker(reason)
        self._note(f"Too many failed requests ({reason}); stopped fetching {what}.")

    def complete(self, task: str, empty_seasons: int = 0) -> None:
        self.tracker.start_phase("export", 1)
        self._note("Exporting data to CSV...")
        self.clock.tick(1)
        self.tracker.advance(1)
        self.store.update(result={"schedule_empty_seasons": empty_seasons, **self.tracker.result()})
        self._state("Completed", 100, task)

    def cancel(self) -> None:
        self._state("Cancelled", self.tracker.percent(), "Cancelled")

    def fail_storage(self, path: str, message: str) -> None:
        self.store.update(result={"error": "storage", "error_path": path, **self.tracker.result()})
        self._state("Failed", self.tracker.percent(), message)


def _payload(mode: str, league_id: Optional[int] = None, **selection: Any) -> Dict[str, Any]:
    """`FetchRequest.model_dump()` biçimi (src/web/routes/scrape.py)."""
    selections = [{"league_id": 17, "season_ids": None, "match_ids": None, **selection}] if selection else None
    return {"league_id": league_id, "mode": mode, "selections": selections}


def _record_jobs(store: Any, clock: _Clock) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """İşleri sırayla oynatır; (`/api/jobs` kaydı, `/api/scrape/status` kaydı) döner."""
    ids: List[str] = []

    def aliased(response: Dict[str, Any]) -> Dict[str, Any]:
        text = json.dumps(response)
        for number, job_id in enumerate(ids, start=1):
            text = text.replace(job_id, f"job-{number}")
        return json.loads(text)

    def start(payload: Dict[str, Any], summary: str) -> _JobScript:
        clock.tick(600)
        job = _JobScript(store, clock, payload, summary)
        ids.append(job.job_id)
        return job

    def status() -> Dict[str, Any]:
        return aliased(_get("/api/scrape/status"))

    statuses: Dict[str, Any] = {"empty job store": status()}
    jobs: Dict[str, Any] = {"empty job store": _get("/api/jobs")}

    job = start(_payload("full"), "All Leagues")
    job.listings()
    job.details(total=8, done=8, refreshed=2)
    job.complete("Background Task Completed Successfully.")
    statuses["job-1 completed: full fetch of all leagues"] = status()

    job = start(_payload("details", match_ids=[16837335, 17099711, 16867839]), "1 targeted selection(s)")
    job.details(total=3, done=3, failed=["17099711"])
    job.complete("Background Task Completed Successfully.")
    statuses["job-2 completed: three selected matches, one failed"] = status()

    job = start(_payload("full", league_id=17), "17")
    job.listings(empty_seasons=1)
    job.details(total=40, done=5, failed=["16837335", "17099711"])
    job.breaker("403", "match details")
    job.complete("(message of the circuit breaker stop, translated by the server)", empty_seasons=1)
    statuses["job-3 completed: stopped by the circuit breaker"] = status()

    job = start(_payload("full", season_ids=[96668, 76986]), "1 targeted selection(s)")
    job.listings()
    job.details(total=20, done=4)
    assert client.post("/api/scrape/cancel").status_code == 200
    statuses["job-4 running: cancel requested"] = status()
    job.cancel()
    statuses["job-4 cancelled"] = status()

    job = start(_payload("details", league_id=17), "17")
    job.details(total=6, done=2)
    job.fail_storage("/data/match_details/17_Premier_League", "(storage error message, translated by the server)")
    statuses["job-5 failed: data could not be written"] = status()

    job = start(_payload("details"), "All Leagues")
    job.details(total=10, done=4, failed=["16867839"])
    job.tracker.wait("429", 30)
    statuses["job-6 running: details phase, waiting for a 429 back-off"] = status()
    jobs["limit=1 while job-6 runs"] = aliased(_get("/api/jobs?limit=1"))

    # Sunucu yeniden başladığında çalışan satır "interrupted" olur ve canlı yansı boşa döner
    clock.tick(60)
    store.mark_stale_running_interrupted()
    statuses["after a restart: job-6 was interrupted, the mirror is idle"] = status()

    full = aliased(_get("/api/jobs"))
    rows = full["body"]["jobs"]
    jobs["default limit: newest first"] = full
    for query in ("limit=2", "limit=100"):
        response = aliased(_get(f"/api/jobs?{query}"))
        limit = int(query.split("=")[1])
        assert response["body"]["jobs"] == rows[:limit], f"{query}: satırlar tam listedekinden farklı"
        jobs[query] = {"status": response["status"], "job_ids": [row["id"] for row in response["body"]["jobs"]]}
    for query in ("limit=0", "limit=101", "limit=abc"):
        jobs[query] = _get(f"/api/jobs?{query}")
    for number, job_id in enumerate(ids, start=1):
        one = aliased(_get(f"/api/jobs/{job_id}"))
        assert one == {"status": 200, "body": next(r for r in rows if r["id"] == f"job-{number}")}
    jobs["/api/jobs/{id}"] = {
        "known id": "200, the same object as its row in the list (asserted by the test)",
        "unknown id": _get("/api/jobs/x"),
    }
    return jobs, statuses


@pytest.fixture(scope="module")
def job_records(tmp_path_factory: pytest.TempPathFactory) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    İş deposunu boş, geçici bir veritabanına bağlar, işleri oynatır, yanıtları kaydeder ve depoyu testlerin
    ortak dizinine geri bağlar. Zaman damgaları için deponun `_utc_now` işlevi elle ilerleyen saate çevrilir.
    """
    store = common._job_store
    if store.snapshot().get("is_running"):  # başka bir testten kalan iş
        store.update(status="Cancelled", progress=0, current_task="cleanup", finished=True)
    clock = _Clock(JOBS_START)
    store_module = sys.modules[type(store).__module__]
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(store_module, "_utc_now", clock.utc_now)
        store.rebind(default_db_path(str(tmp_path_factory.mktemp("jobs") / "data")))
        try:
            return _record_jobs(store, clock)
        finally:
            if store.snapshot().get("is_running"):
                store.update(status="Cancelled", progress=0, current_task="cleanup", finished=True)
            store.rebind(default_db_path(conftest.DATA_DIR))
            common._refresh_scraper_state()


def test_api_jobs(job_records: Tuple[Dict[str, Any], Dict[str, Any]]) -> None:
    """
    İş geçmişi satırları: durum küçük harfli veritabanı değeridir (`completed`, `failed`, `cancelled`,
    `interrupted`, `running`), devre kesicinin durdurduğu iş de `completed` görünür; `detail` satırda yoktur.
    """
    check_golden("jobs", job_records[0])


def test_api_scrape_status(job_records: Tuple[Dict[str, Any], Dict[str, Any]]) -> None:
    """Canlı yansı: durum büyük harfle başlar (`Idle`, `Running`, `Completed`, …); `detail` yalnızca burada vardır."""
    check_golden("scrape_status", job_records[1])


# --- dizin ----------------------------------------------------------------------------------


def test_every_golden_file_belongs_to_a_test() -> None:
    """Artık üretilmeyen bir dosya dizinde kalmasın: sessizce eskir."""
    expected = {f"{fixture}.{reader}.json" for fixture in sf.FIXTURE_NAMES for reader in ("leagues", "league_seasons")}
    expected |= {f"{name}.json" for name in ("league_seasons_files", "settings", "status", "sports", "jobs",
                                             "scrape_status")}
    assert {p.name for p in SNAPSHOT_DIR.iterdir()} == expected
