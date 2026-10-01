"""
Sahte SofaScore taşıyıcısı: istek katmanının (src/utils.make_api_request / make_api_request_async)
hemen altına, HTTP sınırına kurulur. İstek katmanının kendisi (yeniden deneme, tipli hatalar, devre
kesici bildirimi) gerçek kalır; yalnızca curl çağrıları ve beklemeler sahtedir.

Ne yapar:
  - hazır yanıt sunar: API yolu ("/event/1") → JSON; tanımadığı yol SofaScore gibi 404 döner
  - her isteği sırasıyla kaydeder (sync / async, yol, sonuç, oturum, TLS profili, eşzamanlılık)
  - hata enjekte eder: 403, 429, 5xx (herhangi bir durum kodu), zaman aşımı, bağlantı hatası
  - beklemeleri kaydeder ve atlar: istek katmanının beklemeleri (geri çekilme, istek sonrası bekleme,
    ortak istek bütçesinin sırası), uygulama kodundaki time.sleep / asyncio.sleep (yalnızca `src.*` ve
    `main` çağıranlar; diğerleri ve depolama katmanı (`src.store`) gerçekten bekler)

Kurulum süreç içi ve geri alınabilir (`install()` / `uninstall()` ya da `with`); hiçbir üretim
dosyası değişmez. Dünya (yollar ve hatalar) JSON'dan yüklenebilir (`from_file`), kayıt JSON'a
yazılabilir (`save_log`): alt süreçte çalışan testler (CLI goldenları) aynı sahteyi kullanır.

Kullanım:
    fake = FakeSofaScore.from_file(WORLD)
    fake.fail("/event/*/statistics", 403, times=2)
    with fake:
        ...  # istek atan kod
    assert fake.paths() == [...]
"""
from __future__ import annotations

import asyncio
import copy
import fnmatch
import importlib
import json
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from urllib.parse import urlsplit

import curl_cffi.requests as cffi_requests
from curl_cffi.requests import exceptions as cffi_exceptions

API_PREFIX = "/api/v1"
SITE_ROOT = "https://www.sofascore.com/"

# İstek katmanının gövdesinin bulunduğu modüller: `AsyncSession`, `_sleep` ve `_asleep` adları burada
# değiştirilir. Gövde src/client/transport.py'dedir (plan: P05); src.utils aynı adları yeniden dışa aktarır.
REQUEST_LAYER_MODULES: Tuple[str, ...] = ("src.utils", "src.client.transport")

# Beklemenin kaynağı: istek katmanı (yeniden deneme geri çekilmesi, istek sonrası kısa bekleme ve
# ortak istek bütçesi açıksa isteğin bütçede beklediği sıra: src/throttle.py)
REQUEST_LAYER = "request-layer"

# Uygulama modülü oldukları halde beklemeleri gerçek kalanlar (ad ya da paket öneki): SofaScore'u değil
# işletim sistemini beklerler. src/store/files.py, Windows'ta hedef dosya başka bir yerde açıkken yerine
# koymayı kısa aralıklarla yeniden dener; bekleme atlanırsa denemeler bir anda tükenir.
REAL_SLEEP_MODULES: Tuple[str, ...] = ("src.store",)

# Detay dilimlerinin uç noktaları. src/sports.py'den bilerek türetilmez: sahte, SofaScore'un bağımsız
# bir modelidir; koddaki tablo değişirse goldenlardaki istek yolları değişir ve fark görünür.
SLICE_PATHS: Dict[str, str] = {
    "statistics": "/event/{event_id}/statistics",
    "team_streaks": "/event/{event_id}/team-streaks",
    "pregame_form": "/event/{event_id}/pregame-form",
    "h2h": "/event/{event_id}/h2h",
    "lineups": "/event/{event_id}/lineups",
    "incidents": "/event/{event_id}/incidents",
    "point_by_point": "/event/{event_id}/point-by-point",
}

TIMEOUT = "timeout"
NETWORK = "network"

_REAL_TIME_SLEEP = time.sleep
_REAL_ASYNCIO_SLEEP = asyncio.sleep

_NOT_FOUND_BODY = '{"error": {"code": 404, "message": "Not Found"}}'
_REASONS = {200: "OK", 403: "Forbidden", 404: "Not Found", 429: "Too Many Requests",
            500: "Internal Server Error", 502: "Bad Gateway", 503: "Service Unavailable"}


def _is_app_module(name: str) -> bool:
    return name in ("main", "__main__", "src") or name.startswith("src.")


def _skips_sleep_of(name: str) -> bool:
    """`name` modülünün time.sleep / asyncio.sleep çağrısı kaydedilip atlanır mı?"""
    if any(name == real or name.startswith(real + ".") for real in REAL_SLEEP_MODULES):
        return False
    return _is_app_module(name)


@dataclass(frozen=True)
class RecordedRequest:
    """Taşıyıcıya ulaşan bir HTTP isteği (istek katmanının her denemesi ayrı kayıttır)."""

    index: int
    via: str  # "sync" (cffi_requests.get) | "async" (AsyncSession.get)
    path: str  # API köküne göre yol ("/event/1"); API dışı adreste tam URL
    outcome: str  # HTTP durum kodu ("200", "404", "403"...) ya da "timeout" / "network"
    session: Optional[int] = None  # async oturum numarası (açılış sırası)
    impersonate: Optional[str] = None  # sync istekte isteğe verilen TLS profili
    in_flight: int = 1  # bu istek başladığında uçuşta olan istek sayısı (kendisi dahil)
    probe: Any = None  # FakeSofaScore.probe() sonucu (istek anındaki bağlamı yakalamak için)

    @property
    def status(self) -> Optional[int]:
        return int(self.outcome) if self.outcome.isdigit() else None

    @property
    def label(self) -> str:
        return f"{self.via} {self.path} {self.outcome}"


@dataclass(frozen=True)
class RecordedSleep:
    source: str  # REQUEST_LAYER ya da bekleyen modülün adı ("src.match_data_fetcher")
    seconds: float
    kind: str  # "sync" | "async"


@dataclass(frozen=True)
class RecordedSession:
    number: int
    impersonate: Optional[str]


@dataclass
class Fault:
    """Yolu `pattern` ile (fnmatch) eşleşen isteklere verilecek hata; `times` kez (None: hep)."""

    pattern: str
    outcome: str  # durum kodu ("403") ya da TIMEOUT / NETWORK
    times: Optional[int] = None
    body: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    via: Optional[str] = None  # yalnızca "sync" ya da "async" istekler; None: ikisi de

    def matches(self, via: str, path: str) -> bool:
        if self.times is not None and self.times <= 0:
            return False
        return (self.via is None or self.via == via) and fnmatch.fnmatchcase(path, self.pattern)


class FakeResponse:
    """curl_cffi yanıtının istek katmanının okuduğu kısmı."""

    def __init__(self, status_code: int, text: str, headers: Optional[Dict[str, str]] = None) -> None:
        self.status_code = status_code
        self.reason = _REASONS.get(status_code, "")
        self.headers: Dict[str, str] = dict(headers or {})
        self.text = text

    def json(self) -> Any:
        return json.loads(self.text)


class _FakeAsyncSession:
    """`AsyncSession` yerine geçer: açılış ve kapanışı sahteye bildirir, GET'leri ona yönlendirir."""

    def __init__(self, fake: "FakeSofaScore", **kwargs: Any) -> None:
        self._fake = fake
        self.kwargs = kwargs
        self.cookies: Dict[str, str] = {}
        self.number = fake._open_session(kwargs.get("impersonate"))

    async def __aenter__(self) -> "_FakeAsyncSession":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def close(self) -> None:
        self._fake._close_session(self.number)

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        return await self._fake._async_get(self, url, kwargs)


LogEntry = Union[str, Dict[str, List[str]]]


class FakeSofaScore:
    """Sahte taşıyıcı. Dünya: `routes` (yol → JSON) ve `faults` (enjekte edilen hatalar)."""

    def __init__(self, routes: Optional[Dict[str, Any]] = None) -> None:
        self.routes: Dict[str, Any] = dict(routes or {})
        self.faults: List[Fault] = []
        # Her istekte çağrılır; sonucu RecordedRequest.probe'a yazılır (ör. o anki devre kesici)
        self.probe: Optional[Callable[[], Any]] = None
        self._lock = threading.RLock()
        self._requests: List[RecordedRequest] = []
        self._sleeps: List[RecordedSleep] = []
        self._sessions: List[RecordedSession] = []
        # İstekler ve oturum açılış/kapanışları tek zaman çizgisinde: ("request", kayıt) / ("open", n) / ("close", n)
        self._timeline: List[Tuple[str, Any]] = []
        self._in_flight = 0
        self._patches: List[Tuple[Any, str, Any]] = []

    # --- dünya ----------------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FakeSofaScore":
        fake = cls(data.get("routes") or {})
        fake.faults = [Fault(**f) for f in data.get("faults") or []]
        return fake

    @classmethod
    def from_file(cls, path: Any) -> "FakeSofaScore":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def to_dict(self) -> Dict[str, Any]:
        return {"routes": copy.deepcopy(self.routes), "faults": [asdict(f) for f in self.faults]}

    def add(self, path: str, payload: Any) -> None:
        """`path` (API köküne göre) için 200 yanıtı. Aynı yol yeniden verilirse üzerine yazılır."""
        self.routes[path] = payload

    def remove(self, path: str) -> None:
        """Yolu kaldırır: sonraki istekler 404 alır."""
        self.routes.pop(path, None)

    def add_event(self, event: Dict[str, Any], slices: Optional[Dict[str, Any]] = None) -> None:
        """`/event/{id}` ve verilen dilimler (anahtar: SLICE_PATHS; verilmeyen dilim 404 kalır)."""
        event_id = event["id"]
        self.add(f"/event/{event_id}", {"event": event})
        for key, payload in (slices or {}).items():
            self.add(SLICE_PATHS[key].format(event_id=event_id), payload)

    def event(self, event_id: Any) -> Dict[str, Any]:
        """Dünyadaki `/event/{id}` yanıtının `event` nesnesinin kopyası (testler değiştirip geri ekler)."""
        return copy.deepcopy(self.routes[f"/event/{event_id}"]["event"])

    def fail(
        self,
        pattern: str,
        status: int,
        *,
        times: Optional[int] = None,
        body: str = "",
        headers: Optional[Dict[str, str]] = None,
        via: Optional[str] = None,
    ) -> None:
        """Eşleşen isteklere HTTP `status` döndürür (403, 429, 5xx...). `times`: kaç istek; None: hep."""
        self.faults.append(Fault(pattern, str(int(status)), times, body, dict(headers or {}), via))

    def timeout(self, pattern: str, *, times: Optional[int] = None, via: Optional[str] = None) -> None:
        """Eşleşen istekler curl zaman aşımıyla düşer."""
        self.faults.append(Fault(pattern, TIMEOUT, times, via=via))

    def disconnect(self, pattern: str, *, times: Optional[int] = None, via: Optional[str] = None) -> None:
        """Eşleşen istekler bağlantı hatasıyla düşer."""
        self.faults.append(Fault(pattern, NETWORK, times, via=via))

    def clear_faults(self) -> None:
        self.faults.clear()

    # --- kayıt ----------------------------------------------------------------------------

    @property
    def requests(self) -> List[RecordedRequest]:
        with self._lock:
            return list(self._requests)

    @property
    def sleeps(self) -> List[RecordedSleep]:
        with self._lock:
            return list(self._sleeps)

    @property
    def sessions(self) -> List[RecordedSession]:
        with self._lock:
            return list(self._sessions)

    def paths(self, via: Optional[str] = None) -> List[str]:
        """İstek yolları, taşıyıcıya ulaşma sırasıyla (her deneme ayrı)."""
        return [r.path for r in self.requests if via is None or r.via == via]

    def count(self, path: str) -> int:
        return sum(1 for r in self.requests if r.path == path)

    def slept(self, source: str) -> List[float]:
        """`source`un (modül adı ya da REQUEST_LAYER) beklemeleri, saniye olarak ve sırasıyla."""
        return [s.seconds for s in self.sleeps if s.source == source]

    def reset_log(self) -> None:
        """Kaydı boşaltır (dünya ve hatalar kalır): çok adımlı akışta adımları ayrı ayrı okumak için."""
        with self._lock:
            self._requests.clear()
            self._sleeps.clear()
            self._sessions.clear()
            self._timeline.clear()

    def canonical_log(self) -> List[LogEntry]:
        """
        Karşılaştırılabilir istek kaydı. Açık bir async oturum yokken yapılan istekler sıralıdır
        ("sync /event/1 200"). Bir oturum açıkken yapılan her istek (oturumun kendi istekleri ve aynı
        anda to_thread ile atılan sync istekler) eşzamanlıdır: sıraları sözleşme değildir, bu yüzden
        `{"concurrent": [...]}` içinde sıralanmış olarak verilir.
        """
        with self._lock:
            timeline = list(self._timeline)
        log: List[LogEntry] = []
        open_sessions = 0
        window: Optional[List[str]] = None
        for kind, value in timeline:
            if kind == "open":
                if open_sessions == 0:
                    window = []
                open_sessions += 1
            elif kind == "close":
                open_sessions -= 1
                if open_sessions == 0 and window is not None:
                    log.append({"concurrent": sorted(window)})
                    window = None
            elif window is not None:
                window.append(value.label)
            else:
                log.append(value.label)
        if window is not None:  # oturum kapanmadan kayıt alındı
            log.append({"concurrent": sorted(window)})
        return log

    def save_log(self, path: Any) -> None:
        """Kaydı JSON olarak yazar (alt süreçte çalışan testler üst sürece böyle aktarır)."""
        data = {
            "requests": [asdict(r) for r in self.requests],
            "canonical": self.canonical_log(),
            "sleeps": [asdict(s) for s in self.sleeps],
            "sessions": [asdict(s) for s in self.sessions],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, default=str)

    # --- kurulum --------------------------------------------------------------------------

    def install(self) -> "FakeSofaScore":
        """curl çağrılarını ve beklemeleri sahteleriyle değiştirir. `uninstall()` geri alır."""
        if self._patches:
            raise RuntimeError("FakeSofaScore is already installed")
        self._set(cffi_requests, "get", self._sync_get)
        for name in REQUEST_LAYER_MODULES:
            module = importlib.import_module(name)
            self._set(module, "AsyncSession", self._new_session)
            self._set(module, "_sleep", self._request_sleep(module))
            self._set(module, "_asleep", self._request_asleep(module))
        self._set(time, "sleep", self._time_sleep)
        self._set(asyncio, "sleep", self._asyncio_sleep)
        return self

    def uninstall(self) -> None:
        while self._patches:
            target, name, original = self._patches.pop()
            setattr(target, name, original)

    def __enter__(self) -> "FakeSofaScore":
        return self.install()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.uninstall()

    def _set(self, target: Any, name: str, value: Any) -> None:
        self._patches.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    # --- beklemeler -----------------------------------------------------------------------

    def _record_sleep(self, source: str, seconds: float, kind: str) -> None:
        with self._lock:
            self._sleeps.append(RecordedSleep(source, float(seconds), kind))

    def _request_sleep(self, module: Any) -> Callable[[float], None]:
        def _sleep(seconds: float) -> None:
            module.raise_if_cancelled()  # gerçeği gibi: iptal edilen iş beklemeden çıkar
            self._record_sleep(REQUEST_LAYER, seconds, "sync")

        return _sleep

    def _request_asleep(self, module: Any) -> Callable[[float], Any]:
        async def _asleep(seconds: float) -> None:
            module.raise_if_cancelled()
            self._record_sleep(REQUEST_LAYER, seconds, "async")
            await _REAL_ASYNCIO_SLEEP(0)  # gerçeği gibi döngüye sıra verir

        return _asleep

    def _time_sleep(self, seconds: float) -> None:
        caller = sys._getframe(1).f_globals.get("__name__", "")
        if not _skips_sleep_of(caller):
            _REAL_TIME_SLEEP(seconds)
            return
        self._record_sleep(caller, seconds, "sync")

    def _asyncio_sleep(self, delay: float, result: Any = None) -> Any:
        caller = sys._getframe(1).f_globals.get("__name__", "")
        if not _skips_sleep_of(caller):
            return _REAL_ASYNCIO_SLEEP(delay, result)
        self._record_sleep(caller, delay, "async")
        return _REAL_ASYNCIO_SLEEP(0, result)

    # --- istekler -------------------------------------------------------------------------

    def _new_session(self, **kwargs: Any) -> _FakeAsyncSession:
        return _FakeAsyncSession(self, **kwargs)

    def _open_session(self, impersonate: Optional[str]) -> int:
        with self._lock:
            number = len(self._sessions) + 1
            self._sessions.append(RecordedSession(number, impersonate))
            self._timeline.append(("open", number))
            return number

    def _close_session(self, number: int) -> None:
        with self._lock:
            self._timeline.append(("close", number))

    @staticmethod
    def _api_path(url: str) -> str:
        """Tam URL → API köküne göre yol; API dışı adres (ana sayfa ısınması) olduğu gibi kalır."""
        parts = urlsplit(url)
        if not parts.path.startswith(API_PREFIX + "/"):
            return url
        path = parts.path[len(API_PREFIX):]
        return f"{path}?{parts.query}" if parts.query else path

    def _begin(self) -> int:
        with self._lock:
            self._in_flight += 1
            return self._in_flight

    def _end(self) -> None:
        with self._lock:
            self._in_flight -= 1

    def _respond(
        self, via: str, url: str, in_flight: int, session: Optional[int], impersonate: Optional[str]
    ) -> FakeResponse:
        """İsteği kaydeder ve yanıtı üretir; zaman aşımı / bağlantı hatasında curl hatası fırlatır."""
        path = self._api_path(url)
        probe = self.probe() if self.probe is not None else None
        with self._lock:
            fault = next((f for f in self.faults if f.matches(via, path)), None)
            if fault is not None and fault.times is not None:
                fault.times -= 1
            response: Optional[FakeResponse] = None
            if fault is not None:
                outcome = fault.outcome
                if outcome.isdigit():
                    response = FakeResponse(int(outcome), fault.body or _REASONS.get(int(outcome), ""), fault.headers)
            elif path in self.routes:
                outcome = "200"
                response = FakeResponse(200, json.dumps(self.routes[path]))
            elif path == SITE_ROOT:
                outcome = "200"
                response = FakeResponse(200, "<html></html>")
            else:
                outcome = "404"
                response = FakeResponse(404, _NOT_FOUND_BODY)
            record = RecordedRequest(len(self._requests) + 1, via, path, outcome, session, impersonate, in_flight, probe)
            self._requests.append(record)
            self._timeline.append(("request", record))
        if response is not None:
            return response
        if outcome == TIMEOUT:
            raise cffi_exceptions.Timeout(
                "Failed to perform, curl: (28) Operation timed out after 10000 milliseconds with 0 bytes received."
            )
        raise cffi_exceptions.ConnectionError("Failed to perform, curl: (7) Failed to connect to server.")

    def _sync_get(self, url: str, **kwargs: Any) -> FakeResponse:
        in_flight = self._begin()
        try:
            return self._respond("sync", url, in_flight, None, kwargs.get("impersonate"))
        finally:
            self._end()

    async def _async_get(self, session: _FakeAsyncSession, url: str, kwargs: Dict[str, Any]) -> FakeResponse:
        in_flight = self._begin()
        try:
            await _REAL_ASYNCIO_SLEEP(0)  # yanıt "ağdan" gelirken diğer görevler çalışır
            return self._respond("async", url, in_flight, session.number, None)
        finally:
            self._end()
