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

# Dil: açık ayar (APP_LANGUAGE; ortamda ya da .env'de) > sistem dili (LC_ALL, LC_MESSAGES, LANG) >
# İngilizce. Uygulamanın kuralıyla aynı (src/language.py); betik depo klonlanmadan önce de
# çalıştığı için burada yinelenir (start-sofascore.sh ve "Start SofaScore.command" ile aynı işlev).
# Eski LANGUAGE değişkeni yalnızca tam olarak tr / en ise ayar sayılır: GNU gettext de aynı adı kullanır.
detect_lang() {
  local value="${APP_LANGUAGE:-}"
  if [[ -z "$value" && -f .env ]]; then
    value="$(sed -n 's/^[[:space:]]*APP_LANGUAGE[[:space:]]*=//p' .env | tail -n 1 | sed 's/[[:space:]]#.*$//' | tr -d "\"'[:space:]")"
  fi
  for value in "$value" "${LANGUAGE:-}"; do
    case "$(printf '%s' "$value" | LC_ALL=C tr '[:upper:]' '[:lower:]')" in
      tr) echo tr; return ;;
      en) echo en; return ;;
    esac
  done
  value="${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}"
  case "$(printf '%s' "$value" | LC_ALL=C tr '[:upper:]' '[:lower:]')" in
    tr | tr[_.@-]*) echo tr ;;
    *) echo en ;;
  esac
}

UI_LANG="$(detect_lang)"

# msg "English text" "Türkçe metin"
msg() {
  if [[ "$UI_LANG" == "tr" ]]; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi
}

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
    msg "Project not found. Clone the repository and run this inside it, or:" \
      "Proje bulunamadı. Depoyu klonlayıp içine girin veya:" >&2
    echo "  curl -fsSL https://raw.githubusercontent.com/tunjayoff/sofascore_scraper/main/scripts/install.sh | bash" >&2
    exit 1
  fi

  if ! command -v git >/dev/null 2>&1; then
    msg "Error: git not found. Git is needed to clone the repository: https://git-scm.com/downloads" \
      "Hata: git bulunamadı. Git kurulu olmalı (klonlama için): https://git-scm.com/downloads" >&2
    exit 1
  fi

  if [[ -e "$dest" ]]; then
    msg "Error: '$dest' already exists. Delete it, choose another folder or set SOFASCORE_SCRAPER_DIR." \
      "Hata: '$dest' zaten var. Silin, başka klasör seçin veya SOFASCORE_SCRAPER_DIR kullanın." >&2
    exit 1
  fi

  msg "→ Cloning the repository: $clone_url → $dest" "→ Depo klonlanıyor: $clone_url → $dest" >&2
  if ! git clone --depth 1 "$clone_url" "$dest"; then
    msg "Error: git clone failed. Check the network, the repository URL and your permissions." \
      "Hata: git clone başarısız. Ağ / depo URL / izinleri kontrol edin." >&2
    exit 1
  fi
  printf '%s\n' "$(cd "$dest" && pwd)"
}

ROOT="$(resolve_root "${@:-}")"
cd "$ROOT"
# Var olan bir kurulumda .env'deki APP_LANGUAGE de sayılır
UI_LANG="$(detect_lang)"
msg "→ Project folder: $ROOT" "→ Proje dizini: $ROOT"

PYTHON=""
if command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON="python"
else
  msg "Error: python3 or python not found. Install Python 3.10+: https://www.python.org/downloads/" \
    "Hata: python3 veya python bulunamadı. Python 3.10+ kurun: https://www.python.org/downloads/" >&2
  exit 1
fi

if ! "$PYTHON" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"; then
  msg "Error: Python 3.10 or newer is required. Found: $($PYTHON --version 2>&1)" \
    "Hata: Python 3.10 veya üzeri gerekli. Mevcut: $($PYTHON --version 2>&1)" >&2
  exit 1
fi

if [[ ! -d ".venv" ]]; then
  msg "→ Creating the virtual environment (.venv)…" "→ Sanal ortam oluşturuluyor (.venv)…"
  if ! "$PYTHON" -m venv .venv; then
    msg "Error: python -m venv failed." "Hata: python -m venv başarısız." >&2
    echo "  • Ubuntu/Debian: sudo apt install python3-venv" >&2
    echo "  • Fedora: sudo dnf install python3-virtualenv" >&2
    echo "  • macOS (Homebrew): brew install python" >&2
    exit 1
  fi
fi

# shellcheck disable=SC1091
source ".venv/bin/activate"

msg "→ Installing dependencies…" "→ Bağımlılıklar yükleniyor…"
if ! python -m pip install --upgrade pip >/dev/null; then
  msg "Error: pip could not be upgraded. Check your internet connection and that python -m pip works." \
    "Hata: pip güncellenemedi. İnternet ve python -m pip erişimini kontrol edin." >&2
  exit 1
fi

# constraints.txt: CI'ın test ettiği, birlikte çalıştığı bilinen tam sürümler (dolaylı bağımlılıklar dahil)
if ! pip install -r requirements.txt -c constraints.txt; then
  msg "Error: pip install -r requirements.txt -c constraints.txt failed. It may be a compiler, SSL or network error; see the output above." \
    "Hata: pip install -r requirements.txt -c constraints.txt başarısız. Derleyici / SSL / ağ hatası olabilir; çıktıyı yukarıda kontrol edin." >&2
  exit 1
fi

# Köprü (BrowserBridge) tarayıcıyı Scrapling → patchright ile, channel="chromium" olarak başlatır:
# patchright'ın kendi Chromium derlemesi gerekir. Kurulu Google Chrome KULLANILMAZ; playwright'ın kurulum
# komutu ise yalnızca playwright ve patchright sürümleri denk geldiğinde aynı derlemeyi indirir.
msg "→ Installing the browser: patchright's Chromium (the browser the app reaches SofaScore with; a one-time download)…" \
  "→ Tarayıcı kuruluyor: patchright'ın Chromium'u (uygulamanın SofaScore'a eriştiği tarayıcı; tek seferlik indirme)…"
if ! python -m patchright install chromium --no-shell; then
  msg "Error: the browser could not be installed. Without it the app cannot fetch data from SofaScore (an installed Google Chrome is not used instead)." \
    "Hata: tarayıcı kurulamadı. O olmadan uygulama SofaScore'dan veri çekemez (kurulu Google Chrome onun yerine kullanılmaz)." >&2
  msg "  To try again: \"$ROOT/.venv/bin/python\" -m patchright install chromium --no-shell" \
    "  Yeniden denemek için: \"$ROOT/.venv/bin/python\" -m patchright install chromium --no-shell" >&2
fi

if [[ ! -f ".env" ]] && [[ -f ".env.example" ]]; then
  msg "→ Copied .env.example → .env (you can edit it)." "→ .env.example → .env kopyalandı (düzenleyebilirsiniz)."
  cp .env.example .env
  # .env proxy parolası ve erişim belirteci taşıyabilir: yalnızca sahibi okusun
  chmod 600 .env
fi

# Web arayüzü Node.js ile bir kez derlenir (frontend/ → frontend/dist/). Sürüm kuralı src/doctor.py'de.
if python -c "import sys; from src import doctor; sys.exit(0 if doctor.node_is_supported(doctor.installed_node_version()) else 1)"; then
  msg "→ Building the web UI (npm install && npm run build; this can take a few minutes)…" \
    "→ Web arayüzü derleniyor (npm install && npm run build; birkaç dakika sürebilir)…"
  # </dev/null: `curl | bash` ile çalışırken betiğin kendisi stdin'dedir; npm onu tüketmesin
  if ! (cd frontend && npm install </dev/null && npm run build </dev/null); then
    msg "Warning: the web UI could not be built (output above). The terminal modes still work; the web app shows a help page instead of the UI." \
      "Uyarı: web arayüzü derlenemedi (çıktı yukarıda). Terminal modları yine çalışır; web uygulaması arayüz yerine bir yardım sayfası gösterir." >&2
    msg "  To try again: cd \"$ROOT/frontend\" && npm install && npm run build" \
      "  Yeniden denemek için: cd \"$ROOT/frontend\" && npm install && npm run build" >&2
  fi
else
  NODE_FOUND="$(command -v node >/dev/null 2>&1 && node --version 2>/dev/null || msg "none" "yok")"
  msg "Warning: the web UI was not built: Node.js 20.19+ or 22.12+ and npm are required (Node.js found: $NODE_FOUND)." \
    "Uyarı: web arayüzü derlenmedi: Node.js 20.19+ veya 22.12+ ve npm gerekli (bulunan Node.js: $NODE_FOUND)." >&2
  msg "  Install Node.js from https://nodejs.org, then run this script again or start with ./start-sofascore.sh (it builds the UI too when Node.js is there)." \
    "  Node.js'i https://nodejs.org adresinden kurun, sonra bu betiği yeniden çalıştırın ya da ./start-sofascore.sh ile başlatın (Node.js varsa arayüzü o da derler)." >&2
  msg "  The terminal UI (python main.py) and headless mode work without Node.js." \
    "  Terminal arayüzü (python main.py) ve headless mod Node.js olmadan çalışır." >&2
fi

echo ""
msg "→ Checking the installation (python main.py --doctor)…" "→ Kurulum denetleniyor (python main.py --doctor)…"
echo ""
DOCTOR_STATUS=0
python -m src.doctor || DOCTOR_STATUS=$?

echo ""
if [[ "$DOCTOR_STATUS" -ne 0 ]]; then
  msg "The installation is not complete: apply the fixes on the [FAIL] lines above, then check again:" \
    "Kurulum tamamlanmadı: yukarıdaki [FAIL] satırlarındaki çözümleri uygulayın, sonra yeniden denetleyin:" >&2
  echo "  cd \"$ROOT\" && .venv/bin/python main.py --doctor" >&2
  exit 1
fi
msg "Installation complete." "Kurulum tamam."
msg "  Web UI:  cd \"$ROOT\" && ./start-sofascore.sh  → http://127.0.0.1:8000" \
  "  Web arayüzü:  cd \"$ROOT\" && ./start-sofascore.sh  → http://127.0.0.1:8000"
msg "  TUI:     cd \"$ROOT\" && source .venv/bin/activate && python main.py" \
  "  TUI:          cd \"$ROOT\" && source .venv/bin/activate && python main.py"
msg "  Check:   cd \"$ROOT\" && .venv/bin/python main.py --doctor" \
  "  Denetim:      cd \"$ROOT\" && .venv/bin/python main.py --doctor"
echo ""
