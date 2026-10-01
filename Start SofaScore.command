#!/bin/bash
# Double-click (or: open "Start SofaScore.command") on macOS
cd "$(dirname "$0")" || exit 1
chmod +x "scripts/start_web.py" 2>/dev/null || true
if command -v python3 >/dev/null 2>&1; then
  python3 scripts/start_web.py
  status=$?
  # Keep the window open when something went wrong, so the message can be read
  if [ "$status" -ne 0 ] && [ -t 0 ]; then read -r -p "Press Enter to close…" || true; fi
  exit "$status"
fi
echo "Python 3 not found. Install from https://www.python.org/downloads/ or: brew install python"
read -r -p "Press Enter to close…"
exit 1
