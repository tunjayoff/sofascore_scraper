#!/bin/sh
# Konteyner giriş noktası.
#
#   (argümansız) | serve [seçenekler]   → ssc serve --host ${HOST:-0.0.0.0} --port ${PORT:-8000} [seçenekler]
#   başka her şey                       → python -m sofascore_scraper.cli.main "$@": CLI'nin bir komutu (sync,
#                                         watch, status, ...) ya da --version / --help. 2.x'in bayrakları
#                                         (--headless --update-all, --web, ...) 3.1'de kalktı: kullanım hatası
#                                         (çıkış kodu 2), ileti yerine geçen komutu söyler.
#
# Web sunucusu `ssc serve` ile başlar (karar D17; docs/deploy/docker.md). Konteynerde 0.0.0.0 yalnızca
# konteynerin kendi ağ arayüzüdür; dışarıya ne açılacağına `-p` karar verir. `serve` 0.0.0.0'ı "her arayüz"
# sayar ve izin verilen Host adları olmadan başlamaz. İzin listesi hiçbir yerde verilmemişse (ortam, config
# volume'undaki .env, yapılandırma dosyası) giriş noktası yalnızca yerel adları verir
# (SOFASCORE_SERVER__ALLOWED_HOSTS=localhost,127.0.0.1,[::1]): DNS rebinding koruması, doğrudan uvicorn ile
# başlatılan eski imajdaki gibi açık kalır. Arayüze başka bir adla ya da IP ile erişilecekse o adlar
# SOFASCORE_SERVER__ALLOWED_HOSTS ile verilir (Compose örneği bunu açıkça yapar). 2.x'in SOFASCORE_ALLOWED_HOSTS
# ve SOFASCORE_API_TOKEN adları kullanımdan kalktı: 3.1'de bir uyarıyla hâlâ okunur, 3.2'de kalkar (plan maddesi
# FX-35); eski adla verilmiş izin listesi de "verilmiş" sayılır. SOFASCORE_BROWSER_PROFILE 3.1'de okunmaz
# (`ssc doctor` yeni adları söyler).
#
# Erişim belirteci yoksa `serve` her başlangıçta uyarır: uygulama `-p 127.0.0.1:8000:8000` ile yalnızca bu
# makineye yayımlandığını göremez. O durumda uyarı yok sayılabilir; port ağa açıksa SOFASCORE_SERVER__TOKEN
# ayarlanmalıdır.
set -eu

cd "${APP_HOME:-/app}"

LOOPBACK_HOSTS="localhost,127.0.0.1,[::1]"

# --- Tarayıcı profilindeki bayat Chromium kilidi ------------------------------------------
# Chromium profil dizinine SingletonLock ("<hostname>-<pid>") bırakır. Konteyner durdurulunca
# kilit volume'da kalır; yeni konteynerin hostname'i farklı olduğu için Chromium "profil başka
# bir bilgisayarda kullanımda" deyip açılmaz (çıkış kodu 21) ve hiçbir istek yapılamaz.
# Kilidi körlemesine silmek de yanlış: aynı volume'u kullanan başka bir konteyner o an gerçekten
# çalışıyor olabilir. Bu yüzden önce profil dizininde bir flock alınır (aynı volume'u paylaşan
# konteynerler arasında geçerlidir, süreç ölünce çekirdek bırakır):
#   - alınabildiyse profili kullanan başka canlı konteyner yoktur → Chromium kilidi bayattır, silinir;
#   - alınamadıysa profil kullanımdadır → dokunulmaz, tarayıcı açılmayacağı için uyarı yazılır.
# fd 9 exec ile Python sürecine geçer; kilit konteyner yaşadığı sürece tutulur.
# `watch` canlı sayfalar için ikinci bir profil açar (<profil>-live, karar D10). Compose örneği onu da bir
# volume'da tutar; aynı bayat kilit orada da kalır, aynı kuralla (fd 8) açılır.
unlock_stale_dir() {
    # $1: profil dizini, $2: kilidi tutacak dosya tanımlayıcısı (8 ya da 9)
    mkdir -p "$1" 2>/dev/null || return 0
    [ -w "$1" ] || return 0
    eval "exec $2>\"\$1/.container.lock\"" || return 0
    if flock -n "$2"; then
        rm -f "$1/SingletonLock" "$1/SingletonCookie" "$1/SingletonSocket"
    else
        echo "sofascore-entrypoint: WARNING: $1 is in use by another container;" \
            "the browser cannot start in this one (stop the other container first)." >&2
    fi
}

unlock_stale_profile() {
    profile="${SOFASCORE_CLIENT__BROWSER_PROFILE:-}"
    [ -n "$profile" ] || return 0
    unlock_stale_dir "$profile" 9
    if [ "${1:-}" = "watch" ]; then
        unlock_stale_dir "${profile%/}-live" 8
    fi
}

# --- Host izin listesi -------------------------------------------------------------------
# Kullanıcı bir yerde verdiyse (ortamda, config volume'undaki .env'de ya da bir
# yapılandırma dosyası varsa onda) hiçbir şey yapılmaz: `serve` onu yazıldığı gibi kullanır, dosyada da
# yoksa açıkça söyleyip başlamaz. Hiçbir yerde verilmemişse yalnızca yerel adlar. Kullanımdan kalkan
# SOFASCORE_ALLOWED_HOSTS 3.1'de hâlâ okunur (3.2'de kalkar): onunla verilmiş liste de korunur, yoksa giriş
# noktasının yazdığı yeni ad onu ezerdi.
allowed_hosts_given() {
    [ -n "${SOFASCORE_SERVER__ALLOWED_HOSTS:-}" ] && return 0
    [ -n "${SOFASCORE_ALLOWED_HOSTS:-}" ] && return 0
    env_file="${SOFASCORE_ENV_FILE:-.env}"
    if [ -f "$env_file" ] &&
        grep -Eq '^[[:space:]]*(export[[:space:]]+)?SOFASCORE_(SERVER__)?ALLOWED_HOSTS[[:space:]]*=[[:space:]]*[^[:space:]#]' "$env_file"; then
        return 0
    fi
    # Yapılandırma dosyası (sofascore_scraper/config/loader.find_config_file ile aynı yerler): varsa karar onundur
    case "${SOFASCORE_CONFIG:-}" in
        "" | none | NONE | None) ;;
        *) return 0 ;;
    esac
    [ "${SOFASCORE_CONFIG:-}" = "" ] || return 1
    [ -f sofascore.toml ] && return 0
    [ -f "${SOFASCORE_CONFIG_DIR:-config}/sofascore.toml" ] && return 0
    return 1
}

case "${1:-serve}" in
    # Tarayıcıya ihtiyaç duymayan sorgular profile dokunmaz
    --version | --help | -h | version) ;;
    *) unlock_stale_profile "${1:-serve}" ;;
esac

if [ "$#" -eq 0 ] || [ "$1" = "serve" ]; then
    [ "$#" -eq 0 ] || shift
    if ! allowed_hosts_given; then
        export SOFASCORE_SERVER__ALLOWED_HOSTS="$LOOPBACK_HOSTS"
    fi
    exec python -m sofascore_scraper.cli.main serve --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}" "$@"
fi

exec python -m sofascore_scraper.cli.main "$@"
