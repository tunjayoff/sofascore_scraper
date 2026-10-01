#!/bin/sh
# Konteyner giriş noktası.
#
#   (argümansız) | web   → web arayüzü, 0.0.0.0:${PORT:-8000}
#   başka her şey        → python main.py "$@"   (örn. --version, --help, --headless --update-all)
#
# Web sunucusu `main.py --web --host 0.0.0.0` ile DEĞİL, doğrudan uvicorn ile başlatılır.
# Konteynerde 0.0.0.0 yalnızca konteynerin kendi ağ arayüzüdür; dışarıya ne açılacağına `-p`
# karar verir. main.py ise 0.0.0.0'ı "ağa açıldı" sayar: SOFASCORE_ALLOWED_HOSTS verilmeden
# başlamaz ve erişim belirteci yoksa her başlangıçta uyarır; yalnızca 127.0.0.1'de yayımlanan
# bir konteyner için ikisi de yanlış olurdu. Doğrudan uvicorn ile DNS rebinding koruması
# varsayılan haliyle (localhost, 127.0.0.1) açık kalır. Arayüze başka bir adla ya da IP ile
# erişilecekse SOFASCORE_ALLOWED_HOSTS ortam değişkeniyle o ad eklenir; port ağa açılıyorsa
# SOFASCORE_API_TOKEN da ayarlanmalıdır (uygulama konteynerde bunu kendisi uyaramaz).
set -eu

cd /app

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
unlock_stale_profile() {
    profile="${SOFASCORE_BROWSER_PROFILE:-}"
    [ -n "$profile" ] || return 0
    mkdir -p "$profile" 2>/dev/null || return 0
    [ -w "$profile" ] || return 0
    exec 9>"$profile/.container.lock" || return 0
    if flock -n 9; then
        rm -f "$profile/SingletonLock" "$profile/SingletonCookie" "$profile/SingletonSocket"
    else
        echo "sofascore-entrypoint: UYARI: $profile başka bir konteyner tarafından kullanılıyor;" \
            "tarayıcı bu konteynerde açılamaz (önce diğerini durdurun)." >&2
    fi
}

case "${1:-web}" in
    # Tarayıcıya ihtiyaç duymayan sorgular profile dokunmaz
    --version | --help | -h) ;;
    *) unlock_stale_profile ;;
esac

if [ "$#" -eq 0 ] || [ "$1" = "web" ]; then
    exec python -m uvicorn src.web.app:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8000}"
fi

exec python main.py "$@"
