#!/usr/bin/env bash
# Linux: ./start-sofascore.sh  or double-click if file manager allows execute
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || exit 1
chmod +x scripts/start_web.py 2>/dev/null || true

# Keep the window open when something went wrong: a terminal opened by a double-click
# closes on exit and takes the error message with it.
pause_if_interactive() {
  if [ -t 0 ]; then read -r -p "Press Enter to close…" || true; fi
}

if command -v python3 >/dev/null 2>&1; then
  python3 scripts/start_web.py
  status=$?
  if [ "$status" -ne 0 ]; then pause_if_interactive; fi
  exit "$status"
fi
echo "Python 3 not found. Install with your package manager, e.g.:"
echo "  sudo pacman -S python   # or apt install python3"
pause_if_interactive
exit 1
