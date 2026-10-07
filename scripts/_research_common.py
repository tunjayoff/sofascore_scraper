"""
Araştırma script'lerinin ortak parçaları (discover_status_taxonomy.py, measure_finish_lag.py).

HTTP: SofaScore bugün her curl_cffi isteğini 403 challenge ile reddediyor; repodaki
make_api_request de bu yüzden isteği BrowserBridge'e devrediyor. Talimatın istediği yanıt
başlıklarını (Cache-Control, Age, ETag, Date) make_api_request döndürmediği için istekler
doğrudan aynı köprünün sayfasından yapılır: aynı tarayıcı, aynı TLS, aynı challenge çözümü.
Yeni bir HTTP istemcisi yoktur. `cache: "no-store"`: tarayıcının yerel HTTP önbelleği değil,
sunucunun/CDN'in o anki yanıtı ölçülür.

Hız: süreçler arası paylaşılan bir kilit dosyasıyla toplamda en fazla 1 istek/sn.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import fcntl
import hashlib
import json
import os
import sys
import tempfile
import time
from typing import Any, Dict, Optional

# Uygulamanın INFO logları script çıktısını boğmasın (uyarılar görünür kalır)
os.environ.setdefault("LOG_LEVEL", "WARNING")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import sofascore_scraper.challenge_solver as cs  # noqa: E402

API = "https://www.sofascore.com/api/v1"
SPORTS = ("football", "basketball", "tennis")  # sofascore_scraper/web/league_sports.py:SPORTS
CACHE_HEADERS = ("cache-control", "age", "etag", "date", "expires", "last-modified")
MIN_REQUEST_GAP = 1.0
_RATE_FILE = os.path.join(tempfile.gettempdir(), "sofascore_research_ratelimit")

_FETCH_WITH_HEADERS_JS = """async ([url, xReq, xCap, timeoutMs]) => {
    const headers = { "x-requested-with": xReq };
    if (xCap) headers["x-captcha"] = xCap;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
        const r = await fetch(url, { headers, cache: "no-store", signal: ctrl.signal });
        const h = {};
        r.headers.forEach((v, k) => { h[k] = v; });
        const text = await r.text();
        let body = null;
        try { body = JSON.parse(text); } catch (e) {}
        return { status: r.status, headers: h, body: body, text: body === null ? text.slice(0, 300) : null };
    } catch (err) {
        return { status: 0, headers: {}, body: null, text: String(err) };
    } finally {
        clearTimeout(timer);
    }
}"""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _wait_rate_slot() -> None:
    """Tüm araştırma süreçleri için ortak: son istekten bu yana en az MIN_REQUEST_GAP saniye."""
    with open(_RATE_FILE, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.seek(0)
            raw = f.read().strip()
            last = float(raw) if raw else 0.0
            wait = last + MIN_REQUEST_GAP - time.time()
            if wait > 0:
                time.sleep(wait)
            f.seek(0)
            f.truncate()
            f.write(str(time.time()))
            f.flush()
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


class Client:
    """Hız sınırlı, sayaçlı, devre kesicili istek katmanı (BrowserBridge üzerinden)."""

    def __init__(self, breaker_threshold: int = 10, breaker_pause: float = 300.0, max_pauses: int = 3):
        self.bridge = cs.BrowserBridge.get_instance()
        self.requests = 0
        self.by_status: Dict[str, int] = {}
        self._consecutive_failures = 0
        self._pauses = 0
        self.breaker_threshold = breaker_threshold
        self.breaker_pause = breaker_pause
        self.max_pauses = max_pauses

    async def _one(self, path: str) -> Dict[str, Any]:
        await asyncio.to_thread(_wait_rate_slot)
        self.requests += 1
        xreq = hashlib.sha256(str(int(time.time()) // 1800).encode()).hexdigest()[:6]
        return await self.bridge.evaluate(_FETCH_WITH_HEADERS_JS, [API + path, xreq, self.bridge.token, 20000])

    async def _get(self, path: str) -> Dict[str, Any]:
        await self.bridge.ensure_ready()
        r = await self._one(path)
        # 403: challenge çöz ve yeniden dene. Köprü, 30 sn içinde çözülmüş token'ı yeniden
        # çözmeden döndürür; tekrar denemeler bu pencereyi aşacak kadar bekler ki gerçekten
        # yeni bir çözüm yapılabilsin.
        for wait in (0, 5, 35, 35):
            if r["status"] != 403:
                break
            await asyncio.sleep(wait)
            await self.bridge.solve_challenge()
            r = await self._one(path)
        return r

    def get(self, path: str) -> Dict[str, Any]:
        """{status, headers, body, text}. 200/404 başarı sayılır; ardışık diğerleri devreyi keser."""
        try:
            r = asyncio.run(cs._run_on_background_loop(self._get(path), cs.REQUEST_TIMEOUT + 30))
        except Exception as e:  # tek istek hatası tüm taramayı düşürmesin; devre kesici sayar
            r = {"status": f"error:{e.__class__.__name__}", "headers": {}, "body": None, "text": str(e)[:300]}
        key = str(r.get("status"))
        self.by_status[key] = self.by_status.get(key, 0) + 1
        if r.get("status") in (200, 404):
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.breaker_threshold:
                self._pauses += 1
                if self._pauses > self.max_pauses:
                    raise RuntimeError(f"circuit breaker: {self.breaker_threshold} ardışık hata, {self.max_pauses} bekleme sonrası durduruldu")
                print(f"[breaker] {self.breaker_threshold} ardışık hata (son: {key}); {int(self.breaker_pause)} sn bekleniyor", flush=True)
                time.sleep(self.breaker_pause)
                self._consecutive_failures = 0
        return r

    def close(self) -> None:
        try:
            asyncio.run(cs._run_on_background_loop(self.bridge.close(), 30))
        except Exception:
            pass


def cache_headers(r: Dict[str, Any]) -> Dict[str, Optional[str]]:
    h = r.get("headers") or {}
    return {k: h.get(k) for k in CACHE_HEADERS if h.get(k) is not None}


def status_triple(event: Dict[str, Any]):
    st = event.get("status") or {}
    return st.get("type"), st.get("code"), st.get("description")


def write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
