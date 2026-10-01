"""
03-B doğrulama koşusu (2026-10-03) için ortak parçalar: pick.py ve run.py.

Hız: üç spor ayrı süreçte çalışır; her istek scripts/_research_common.py'deki Client'tan geçer.
Client her gerçek istekten (403 tekrarları dahil) önce süreçler arası kilit dosyasıyla sıra
alır, yani pick + üç izleyici + son kontrol toplamda en fazla 1 istek/sn. src/'ye dokunulmaz;
MatchWatcher yalnızca fetch_json olarak bu istemciyi alır (kalıcı çözüm: issue #16).
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, Optional

# Bu dosya <kök>/research/watch_run_2026-10-03/ altında; kök, koşunun çalıştığı main kopyası
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import scripts._research_common as rc  # noqa: E402
from scripts._research_common import SPORTS, Client, utc_now, write_json  # noqa: E402

__all__ = ["ROOT", "SPORTS", "Client", "fetch_json", "log_requests", "utc_now", "write_json"]


def log_requests(path: str, tag: str) -> None:
    """Her gerçek isteğin, ortak kilitten sıra aldığı anı <path>'e yazar (bütçe kanıtı)."""
    wait_slot = rc._wait_rate_slot

    def logged() -> None:
        wait_slot()
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.time():.3f} {tag}\n")

    rc._wait_rate_slot = logged


def fetch_json(client: Client, path: str) -> Optional[Dict[str, Any]]:
    """MatchWatcher'ın fetch_json sözleşmesi: 200 → gövde, 404 → None, diğerleri → hata."""
    r = client.get(path)
    if r.get("status") == 200:
        return r.get("body")
    if r.get("status") == 404:
        return None
    raise RuntimeError(f"HTTP {r.get('status')}: {path}")
