# SofaScore Scraper — konteyner imajı
#
#   docker build -t sofascore-scraper .
#   docker run --rm --shm-size=1g -p 127.0.0.1:8000:8000 \
#     -v sofascore-data:/app/data -v sofascore-config:/app/config \
#     -v sofascore-browser:/app/browser-profile sofascore-scraper
#
# Varsayılan komut web arayüzüdür (http://127.0.0.1:8000). Başka argüman verilirse
# main.py'ye geçer:  docker run --rm sofascore-scraper --version

# ---- 1) Web arayüzünü derle (Node yalnızca bu aşamada var) --------------------
FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
# Önce yalnızca kilit dosyaları: kaynak değişince npm ci katmanı yeniden çalışmaz
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 2) Çalışma imajı: Python + headless Chromium -----------------------------
# CI'nin test ettiği sürümlerden biri (3.10 ve 3.14)
FROM python:3.14-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Tarayıcı root olarak imaja kurulur, uygulama kullanıcısı oradan okur
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    # Ayarlar sayfası .env'e yazar: config volume'unda dursun ki konteyner yenilenince kaybolmasın
    SOFASCORE_ENV_FILE=/app/config/.env \
    # Çözülmüş challenge (cookie'ler) kendi volume'unda: yeniden başlatmada ilk istek hızlı olur
    SOFASCORE_BROWSER_PROFILE=/app/browser-profile \
    PORT=8000

WORKDIR /app

# Bağımlılıklar ve tarayıcı, uygulama kodundan önce: kod değişince bu katman yeniden kurulmaz.
# BrowserBridge'i Scrapling'in StealthySession'ı (patchright) açar; Chromium'u ve onun sistem
# kütüphanelerini (--with-deps) patchright kurar. Yeni headless modu tam Chromium ikilisini
# kullandığı için ayrı headless-shell indirilmez (--no-shell).
# tini: PID 1 olarak sinyalleri iletir ve Chromium'un öksüz kalan alt süreçlerini toplar.
COPY requirements.txt ./
RUN pip install -r requirements.txt \
    && apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && python -m patchright install --with-deps --no-shell chromium \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /root/.cache

# root olmayan kullanıcı; yalnızca volume dizinleri ona aittir (kod salt okunur kalır).
# --non-unique: APP_UID/APP_GID imajda zaten varsa (ör. gid 100 = users) derleme düşmesin.
# ARG'lar ağır katmandan sonra: başka bir uid ile derlemek tarayıcıyı yeniden indirmez.
ARG APP_UID=1000
ARG APP_GID=1000
RUN groupadd --non-unique --gid "${APP_GID}" app \
    && useradd --non-unique --uid "${APP_UID}" --gid app --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/data /app/config /app/logs /app/browser-profile \
    && chown app:app /app/data /app/config /app/logs /app/browser-profile

# pyproject.toml sürümün tek kaynağıdır (src/version.py çalışma anında okur)
COPY pyproject.toml main.py LICENSE README.md README.tr.md CHANGELOG.md ./
COPY src/ ./src/
COPY locales/ ./locales/
COPY --chown=app:app config/leagues.example.txt ./config/
COPY docker/entrypoint.sh /usr/local/bin/sofascore-entrypoint
COPY --from=frontend /build/frontend/dist ./frontend/dist
RUN chmod 0755 /usr/local/bin/sofascore-entrypoint

LABEL org.opencontainers.image.title="SofaScore Scraper" \
      org.opencontainers.image.description="Download football, basketball and tennis match data from SofaScore and browse it in a web app" \
      org.opencontainers.image.source="https://github.com/tunjayoff/sofascore_scraper" \
      org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0"

USER app

# data: indirilen veri ve iş geçmişi · config: leagues.txt, league_sports.json, .env
# logs: dönen log dosyası (LOG_DIR varsayılanı; aynı satırlar stdout'a da yazılır: docker logs)
# browser-profile: tarayıcı profili (çözülmüş challenge)
VOLUME ["/app/data", "/app/config", "/app/logs", "/app/browser-profile"]

EXPOSE 8000

# Yalnızca web sunucusu çalışırken anlamlıdır; tek seferlik komutlarda (--headless …) --no-healthcheck verin
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/health', timeout=4)" || exit 1

ENTRYPOINT ["tini", "--", "sofascore-entrypoint"]
CMD ["web"]
