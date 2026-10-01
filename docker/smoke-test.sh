#!/usr/bin/env bash
# İmajın çevrimdışı duman testi. Yayın iş akışı imajı göndermeden önce çalıştırır; yerelde:
#
#   docker build -t sofascore-scraper .
#   docker/smoke-test.sh sofascore-scraper            # sürüm pyproject.toml'dan okunur
#
# Bütün konteynerler --network none ile çalışır: SofaScore'a (ya da başka bir yere) istek
# atılamaz. Tarayıcı yalnızca konteynerin kendi /health sayfasını açar; challenge çözümü ve
# gerçek API istekleri burada SINANMAZ.
set -euo pipefail

IMAGE="${1:?kullanım: docker/smoke-test.sh IMAJ [SÜRÜM]}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${2:-$(python3 "${ROOT}/scripts/release.py" version)}"
NAME="sofascore-smoke-$$"

cleanup() { docker rm -f -v "${NAME}" >/dev/null 2>&1 || true; }
trap cleanup EXIT

step() { printf '\n== %s\n' "$*"; }

step "--version"
out="$(docker run --rm --network none "${IMAGE}" --version)"
echo "${out}"
test "${out}" = "SofaScore Scraper ${VERSION}"

step "--help"
out="$(docker run --rm --network none "${IMAGE}" --help)"
grep -q -- "--headless" <<<"${out}"
echo "ok"

step "web arayüzü (varsayılan komut) ve HEALTHCHECK"
docker run -d --name "${NAME}" --network none --shm-size=1g "${IMAGE}" >/dev/null
status="starting"
for _ in $(seq 1 60); do
    status="$(docker inspect --format '{{.State.Health.Status}}' "${NAME}")"
    [ "${status}" = "healthy" ] && break
    [ "$(docker inspect --format '{{.State.Running}}' "${NAME}")" = "true" ] || break
    sleep 2
done
if [ "${status}" != "healthy" ]; then
    docker logs "${NAME}" || true
    echo "konteyner sağlıklı duruma geçmedi (durum: ${status})" >&2
    exit 1
fi
test "$(docker exec "${NAME}" id -u)" != "0"
echo "healthy; uid=$(docker exec "${NAME}" id -u)"

step "/health, SPA index, Host kontrolü"
docker exec -i -e EXPECTED_VERSION="${VERSION}" "${NAME}" python - <<'PY'
import json
import os
import urllib.error
import urllib.request

base = "http://127.0.0.1:" + os.environ.get("PORT", "8000")
health = json.load(urllib.request.urlopen(base + "/health", timeout=5))
core = {k: health.get(k) for k in ("status", "version", "ui")}
assert core == {"status": "ok", "version": os.environ["EXPECTED_VERSION"], "ui": "vue-spa"}, health
# Henüz SofaScore'a istek atılmadı: köprü "ok" bildirir; ortak istek bütçesi varsayılan olarak açıktır
assert health["bridge"]["state"] == "ok", health
assert health["throttle"]["enabled"] is True and health["throttle"]["error"] is None, health
index = urllib.request.urlopen(base + "/", timeout=5).read().decode()
assert '<div id="app">' in index, index[:200]
assert urllib.request.urlopen(base + "/settings", timeout=5).read().decode() == index  # SPA fallback
try:
    urllib.request.urlopen(urllib.request.Request(base + "/health", headers={"Host": "evil.example"}), timeout=5)
except urllib.error.HTTPError as e:
    assert e.code == 400, e.code
else:
    raise AssertionError("foreign Host header was accepted")
print("ok:", health)
PY

step "headless Chromium (BrowserBridge ile aynı başlatma, yalnızca yerel sayfa)"
docker exec -i "${NAME}" python - <<'PY'
import asyncio
import os

from scrapling.fetchers import AsyncStealthySession


async def main() -> None:
    session = AsyncStealthySession(
        headless=True,
        solve_cloudflare=True,
        user_data_dir=os.environ["SOFASCORE_BROWSER_PROFILE"],
        proxy=None,
        block_webrtc=True,
        timeout=60000,
        max_pages=2,
    )
    await session.start()
    try:
        page = await session.context.new_page()
        url = "http://127.0.0.1:" + os.environ.get("PORT", "8000") + "/health"
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        body = await page.evaluate('async () => (await fetch("/health", {cache: "no-store"})).json()')
        assert body["status"] == "ok", body
        print("ok: in-page fetch ->", body)
    finally:
        await session.close()


asyncio.run(main())
PY

printf '\nDuman testi geçti: %s (%s)\n' "${IMAGE}" "${VERSION}"
