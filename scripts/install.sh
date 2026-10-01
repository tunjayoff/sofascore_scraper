#!/usr/bin/env bash
# SofaScore Scraper — kurulum (Linux / macOS / Git Bash)
#
# Yerel (klonlanmış repo): ./scripts/install.sh
# Parametresiz uzak kurulum (resmi repo): curl -fsSL .../install.sh | bash
# Özel depo: curl ... | bash -s -- https://github.com/KULLANICI/fork.git [hedef_klasör]
# İlk argüman URL değilse → resmi depo klonlanır, klasör adı = 1. argüman (varsayılan: sofascore_scraper)

set -euo pipefail

DEFAULT_REMOTE_REPO="${SOFASCORE_SCRAPER_DEFAULT_REPO:-https://github.com/tunjayoff/sofascore_scraper.git}"
INSTALL_DIR="${SOFASCORE_SCRAPER_DIR:-sofascore_scraper}"

is_repo_url() {
  [[ "${1:-}" == http://* || "${1:-}" == https://* || "${1:-}" == git@* ]]
}

resolve_root() {
  local root=""
  local clone_url=""
  local dest=""

  if [[ -f "requirements.txt" ]] && [[ -f "main.py" ]]; then
    printf '%s\n' "$(pwd)"
    return
  fi

  local script_path="${BASH_SOURCE[0]:-}"
  if [[ -n "$script_path" ]] && [[ -f "$script_path" ]]; then
    local sd
    sd="$(cd "$(dirname "$script_path")" && pwd)"
    if [[ -f "$sd/../requirements.txt" ]] && [[ -f "$sd/../main.py" ]]; then
      printf '%s\n' "$(cd "$sd/.." && pwd)"
      return
    fi
  fi

  if is_repo_url "${1:-}"; then
    clone_url="$1"
    dest="${2:-$INSTALL_DIR}"
  else
    clone_url="${SOFASCORE_SCRAPER_REPO:-$DEFAULT_REMOTE_REPO}"
    dest="${1:-$INSTALL_DIR}"
  fi

  if [[ -z "$clone_url" ]]; then
    echo "Proje bulunamadı. Depoyu klonlayıp içine girin veya:" >&2
    echo "  curl -fsSL https://raw.githubusercontent.com/tunjayoff/sofascore_scraper/main/scripts/install.sh | bash" >&2
    exit 1
  fi

  if ! command -v git >/dev/null 2>&1; then
    echo "Hata: git bulunamadı. Git kurulu olmalı (klonlama için): https://git-scm.com/downloads" >&2
    exit 1
  fi

  if [[ -e "$dest" ]]; then
    echo "Hata: '$dest' zaten var. Silin, başka klasör seçin veya SOFASCORE_SCRAPER_DIR kullanın." >&2
    exit 1
  fi

  echo "→ Depo klonlanıyor: $clone_url → $dest" >&2
  if ! git clone --depth 1 "$clone_url" "$dest"; then
    echo "Hata: git clone başarısız. Ağ / depo URL / izinleri kontrol edin." >&2
    exit 1
  fi
  printf '%s\n' "$(cd "$dest" && pwd)"
}

ROOT="$(resolve_root "${@:-}")"
cd "$ROOT"
echo "→ Proje dizini: $ROOT"

PYTHON=""
if command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON="python"
else
  echo "Hata: python3 veya python bulunamadı. Python 3.10+ kurun: https://www.python.org/downloads/" >&2
  exit 1
fi

if ! "$PYTHON" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"; then
  echo "Hata: Python 3.10 veya üzeri gerekli. Mevcut: $($PYTHON --version 2>&1)" >&2
  exit 1
fi

if [[ ! -d ".venv" ]]; then
  echo "→ Sanal ortam oluşturuluyor (.venv)…"
  if ! "$PYTHON" -m venv .venv; then
    echo "Hata: python -m venv başarısız." >&2
    echo "  • Ubuntu/Debian: sudo apt install python3-venv" >&2
    echo "  • Fedora: sudo dnf install python3-virtualenv" >&2
    echo "  • macOS (Homebrew): brew install python" >&2
    exit 1
  fi
fi

# shellcheck disable=SC1091
source ".venv/bin/activate"

echo "→ Bağımlılıklar yükleniyor…"
if ! python -m pip install --upgrade pip >/dev/null; then
  echo "Hata: pip güncellenemedi. İnternet ve python -m pip erişimini kontrol edin." >&2
  exit 1
fi

if ! pip install -r requirements.txt; then
  echo "Hata: pip install -r requirements.txt başarısız. Derleyici / SSL / ağ hatası olabilir; çıktıyı yukarıda kontrol edin." >&2
  exit 1
fi

# Köprü (BrowserBridge) tarayıcıyı Scrapling → patchright ile, channel="chromium" olarak başlatır:
# patchright'ın kendi Chromium derlemesi gerekir. Kurulu Google Chrome KULLANILMAZ; playwright'ın kurulum
# komutu ise yalnızca playwright ve patchright sürümleri denk geldiğinde aynı derlemeyi indirir.
echo "→ Tarayıcı kuruluyor: patchright'ın Chromium'u (uygulamanın SofaScore'a eriştiği tarayıcı; tek seferlik indirme)…"
if ! python -m patchright install chromium --no-shell; then
  echo "Hata: tarayıcı kurulamadı. O olmadan uygulama SofaScore'dan veri çekemez (kurulu Google Chrome onun yerine kullanılmaz)." >&2
  echo "  Yeniden denemek için: \"$ROOT/.venv/bin/python\" -m patchright install chromium --no-shell" >&2
fi

if [[ ! -f ".env" ]] && [[ -f ".env.example" ]]; then
  echo "→ .env.example → .env kopyalandı (düzenleyebilirsiniz)."
  cp .env.example .env
fi

# Web arayüzü Node.js ile bir kez derlenir (frontend/ → frontend/dist/). Sürüm kuralı src/doctor.py'de.
if python -c "import sys; from src import doctor; sys.exit(0 if doctor.node_is_supported(doctor.installed_node_version()) else 1)"; then
  echo "→ Web arayüzü derleniyor (npm install && npm run build; birkaç dakika sürebilir)…"
  # </dev/null: `curl | bash` ile çalışırken betiğin kendisi stdin'dedir; npm onu tüketmesin
  if ! (cd frontend && npm install </dev/null && npm run build </dev/null); then
    echo "Uyarı: web arayüzü derlenemedi (çıktı yukarıda). Terminal modları yine çalışır; web uygulaması arayüz yerine bir yardım sayfası gösterir." >&2
    echo "  Yeniden denemek için: cd \"$ROOT/frontend\" && npm install && npm run build" >&2
  fi
else
  NODE_FOUND="$(command -v node >/dev/null 2>&1 && node --version 2>/dev/null || echo "yok")"
  echo "Uyarı: web arayüzü derlenmedi: Node.js 20.19+ veya 22.12+ ve npm gerekli (bulunan Node.js: $NODE_FOUND)." >&2
  echo "  Node.js'i https://nodejs.org adresinden kurun, sonra bu betiği yeniden çalıştırın ya da ./start-sofascore.sh ile başlatın (Node.js varsa arayüzü o da derler)." >&2
  echo "  Terminal arayüzü (python main.py) ve headless mod Node.js olmadan çalışır." >&2
fi

echo ""
echo "→ Kurulum denetleniyor (python main.py --doctor)…"
echo ""
DOCTOR_STATUS=0
python -m src.doctor || DOCTOR_STATUS=$?

echo ""
if [[ "$DOCTOR_STATUS" -ne 0 ]]; then
  echo "Kurulum tamamlanmadı: yukarıdaki [FAIL] satırlarındaki çözümleri uygulayın, sonra yeniden denetleyin:" >&2
  echo "  cd \"$ROOT\" && .venv/bin/python main.py --doctor" >&2
  exit 1
fi
echo "Kurulum tamam."
echo "  Web arayüzü:  cd \"$ROOT\" && ./start-sofascore.sh  → http://127.0.0.1:8000"
echo "  TUI:          cd \"$ROOT\" && source .venv/bin/activate && python main.py"
echo "  Denetim:      cd \"$ROOT\" && .venv/bin/python main.py --doctor"
echo ""
