#!/usr/bin/env bash
# Linux: ./start-sofascore.sh  or double-click if file manager allows execute
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
chmod +x scripts/start_web.py 2>/dev/null || true
if command -v python3 >/dev/null 2>&1; then
  exec python3 scripts/start_web.py
fi
echo "Python 3 not found. Install with your package manager, e.g.:"
echo "  sudo pacman -S python   # or apt install python3"
read -r -p "Press Enter to close…"
exit 1
