#!/usr/bin/env bash
# S6: scripts/push_memory_probe.py'nin isteğe bağlı motorlarını kurar. Hepsi yalnızca $S6_ENGINES_DIR altına
# (varsayılan ~/.cache/s6-engines) gider: deponun .venv'ine, ~/.cache/ms-playwright'a ya da
# ~/.cache/sofascore_scraper'a hiçbir şey yazılmaz. Sürümler sabittir; prob kurulu olmayan motoru "skipped" yazar.
#
#   scripts/push_memory_engines.sh headless-shell   # chrome-headless-shell, patchright'ın Chromium sürümü (1243)
#   scripts/push_memory_engines.sh camoufox         # ayrı sanal ortam (playwright 1.62) + Camoufox tarayıcısı
#   scripts/push_memory_engines.sh lightpanda       # tek ikili, sha256 doğrulanır
#   scripts/push_memory_engines.sh all
#
# Lisans ve platform: chrome-headless-shell BSD-3-Clause (Chromium), x86_64 ve arm64 Linux. Camoufox MPL-2.0
# (tarayıcı) + MIT (python paketi), x86_64 ve arm64 Linux. Lightpanda AGPL-3.0, x86_64 ve aarch64 Linux.
set -euo pipefail

ENGINES="${S6_ENGINES_DIR:-$HOME/.cache/s6-engines}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-$REPO/.venv/bin/python}"

CAMOUFOX_VERSION="0.5.8"              # tarayıcı sürümünü paket sabitler: v156.0.1-beta.36 (browser-pin.json)
CAMOUFOX_PLAYWRIGHT="1.62.0"          # camoufox 0.5.8 playwright<1.63 ister; deponun sabiti 1.63.0
LIGHTPANDA_VERSION="1.0.0"
LIGHTPANDA_SHA256_X86_64="aa5a4b8ed53d1e38b3c73f5b2647d0a84a82e6744557f45f9a9c85858aa031c3"
LIGHTPANDA_SHA256_AARCH64="69791924bcee43b13b224af4c845622c5fe66fdbc1b8143bfaa39ca8f85244f5"

mkdir -p "$ENGINES/tmp"
export TMPDIR="$ENGINES/tmp"  # Camoufox'un 1,3 GB'lık arşivi /tmp'ye (tmpfs) inmesin

headless_shell() {
  PLAYWRIGHT_BROWSERS_PATH="$ENGINES/ms-playwright" "$PY" -m patchright install chromium-headless-shell
}

camoufox() {
  local venv="$ENGINES/camoufox-venv"
  [ -x "$venv/bin/python" ] || "$PY" -m venv "$venv"
  grep -v '^playwright==' "$REPO/constraints.txt" > "$ENGINES/tmp/constraints-camoufox.txt"
  echo "playwright==$CAMOUFOX_PLAYWRIGHT" >> "$ENGINES/tmp/constraints-camoufox.txt"
  "$venv/bin/python" -m pip install --upgrade pip
  "$venv/bin/python" -m pip install -r "$REPO/requirements.txt" -c "$ENGINES/tmp/constraints-camoufox.txt" \
    "camoufox==$CAMOUFOX_VERSION" "playwright==$CAMOUFOX_PLAYWRIGHT"
  XDG_CACHE_HOME="$ENGINES/xdg" "$venv/bin/python" -m camoufox fetch
}

lightpanda() {
  local arch sha dir="$ENGINES/lightpanda-$LIGHTPANDA_VERSION"
  case "$(uname -m)" in
    x86_64) arch=x86_64; sha="$LIGHTPANDA_SHA256_X86_64" ;;
    aarch64|arm64) arch=aarch64; sha="$LIGHTPANDA_SHA256_AARCH64" ;;
    *) echo "lightpanda: no build for $(uname -m)" >&2; return 1 ;;
  esac
  mkdir -p "$dir"
  curl -fL --retry 3 -o "$dir/lightpanda-$arch-linux" \
    "https://github.com/lightpanda-io/browser/releases/download/$LIGHTPANDA_VERSION/lightpanda-$arch-linux"
  echo "$sha  $dir/lightpanda-$arch-linux" | sha256sum -c -
  chmod +x "$dir/lightpanda-$arch-linux"
  ln -sf "lightpanda-$arch-linux" "$dir/lightpanda"
  LIGHTPANDA_DISABLE_TELEMETRY=true "$dir/lightpanda" version
}

case "${1:-all}" in
  headless-shell) headless_shell ;;
  camoufox) camoufox ;;
  lightpanda) lightpanda ;;
  all) headless_shell; camoufox; lightpanda ;;
  *) echo "usage: $0 [headless-shell|camoufox|lightpanda|all]" >&2; exit 2 ;;
esac
