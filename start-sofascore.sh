#!/usr/bin/env bash
# Linux: ./start-sofascore.sh  or double-click if file manager allows execute
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1
chmod +x scripts/start_web.py 2>/dev/null || true

# Language: explicit setting (APP_LANGUAGE in the environment or in .env) > system language
# (LC_ALL, LC_MESSAGES, LANG) > English. Same rule as the app (src/language.py) and
# scripts/install.sh; repeated here because this runs before Python is known to exist.
# The legacy LANGUAGE variable counts only when it is exactly tr / en: GNU gettext uses the same name.
# The app also reads the language from sofascore.toml and from the Settings page (config/overrides.json);
# this script cannot, so once Python is known to exist it asks the app instead (app_lang below).
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

# The app's answer: the rule above plus sofascore.toml and the Settings page's overrides.json
# (src/doctor.py, standard library only). Prints nothing when Python cannot answer.
app_lang() {
  python3 -c 'from src import doctor; print(doctor.Context().lang)' 2>/dev/null || true
}

# msg "English text" "Türkçe metin"
msg() {
  if [[ "$UI_LANG" == "tr" ]]; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi
}

# Keep the window open when something went wrong: a terminal opened by a double-click
# closes on exit and takes the error message with it.
pause_if_interactive() {
  if [ -t 0 ]; then read -r -p "$(msg "Press Enter to close…" "Kapatmak için Enter'a basın…")" || true; fi
}

if command -v python3 >/dev/null 2>&1; then
  python3 scripts/start_web.py
  status=$?
  if [ "$status" -ne 0 ]; then
    case "$(app_lang)" in tr) UI_LANG=tr ;; en) UI_LANG=en ;; esac
    pause_if_interactive
  fi
  exit "$status"
fi
msg "Python 3 not found. Install with your package manager, e.g.:" \
  "Python 3 bulunamadı. Paket yöneticinizle kurun, örneğin:"
msg "  sudo pacman -S python   # or apt install python3" \
  "  sudo pacman -S python   # ya da apt install python3"
pause_if_interactive
exit 1
