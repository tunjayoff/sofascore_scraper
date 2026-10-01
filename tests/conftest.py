"""
Test yalıtımı: testler gerçek .env, config/, data/ ve jobs.db'ye dokunmamalı.

Ortam değişkenleri modül yüklenirken ayarlanır — src.* modülleri import anında
.env'i ve DATA_DIR'i okuduğu için bunun herhangi bir test modülü import edilmeden
önce olması gerekir. Geçici dizine küçük, sentetik bir veri seti yazılır.
"""
from __future__ import annotations

import atexit
import csv
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="sofascore-tests-")
atexit.register(shutil.rmtree, _TMP, True)

DATA_DIR = os.path.join(_TMP, "data")
CONFIG_DIR = os.path.join(_TMP, "config")
ENV_FILE = os.path.join(_TMP, ".env")

LEAGUE_ID = 17
LEAGUE_NAME = "Premier League"
SEASON_ID = 96668
SEASON_NAME = "Premier League 26/27"
MATCH_IDS = (9000001, 9000002)

# Kullanıcının kabuğundan veya gerçek .env'den gelebilecek ayarları temizle
for _k in ("PROXY_URL", "USE_PROXY", "API_BASE_URL", "SOFA_CAPTCHA_TOKEN", "FETCH_ONLY_FINISHED", "SOFASCORE_API_TOKEN"):
    os.environ.pop(_k, None)
# Dil, testleri çalıştıranın kabuğuna bağlı olmasın (kural: açık ayar > sistem dili > İngilizce,
# src/language.py): açık ayar yok, ileti dili "C" → her makinede varsayılan dil (İngilizce).
# LC_MESSAGES yalnızca ileti dilidir; LANG'e dokunulmaz (karakter kodlaması ondan gelir).
for _k in ("APP_LANGUAGE", "LANGUAGE", "LC_ALL"):
    os.environ.pop(_k, None)
os.environ["LC_MESSAGES"] = "C"
# Log dosyası da geçici dizine: testler projedeki logs/ dizinine yazmaz (src/logger.py)
for _k in ("LOG_TO_FILE", "LOG_MAX_MB", "LOG_BACKUP_COUNT"):
    os.environ.pop(_k, None)
os.environ["LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ["DATA_DIR"] = DATA_DIR
os.environ["SOFASCORE_CONFIG_DIR"] = CONFIG_DIR
os.environ["SOFASCORE_ENV_FILE"] = ENV_FILE
# Ortak istek bütçesi (src/throttle.py) testlerde kapalı ve yalıtılmış: testler makinedeki gerçek
# süreçlerin bütçe dosyasına dokunmaz, sahte uyku sayaçlarına fazladan bekleme girmez.
os.environ["REQUEST_RATE_LIMIT"] = "0"
os.environ["SOFASCORE_THROTTLE_DIR"] = os.path.join(_TMP, "throttle")
# Tarayıcı profili de geçici dizinde: uygulama başlangıçta profil dizininin izinlerini daraltır
# (src/fsutil.harden_secret_paths); testler kullanıcının gerçek profiline dokunmaz.
os.environ["SOFASCORE_BROWSER_PROFILE"] = os.path.join(_TMP, "browser-profile")
# TestClient "testserver" Host başlığını kullanır
os.environ["SOFASCORE_ALLOWED_HOSTS"] = "localhost,127.0.0.1,testserver"


def _write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _basic(mid: int, home: str, away: str, hs: int, as_: int, ts: int) -> dict:
    return {
        "id": mid,
        "tournament": {
            "name": LEAGUE_NAME,
            "uniqueTournament": {"id": LEAGUE_ID, "name": LEAGUE_NAME},
        },
        "season": {"id": SEASON_ID, "name": SEASON_NAME, "year": "26/27"},
        "roundInfo": {"round": 1},
        "status": {"code": 100, "description": "Ended", "type": "finished"},
        "homeTeam": {"id": 1, "name": home},
        "awayTeam": {"id": 2, "name": away},
        "homeScore": {"current": hs},
        "awayScore": {"current": as_},
        "startTimestamp": ts,
    }


def _seed() -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(os.path.join(CONFIG_DIR, "leagues.txt"), "w", encoding="utf-8") as f:
        f.write(f"# League configuration file\n# Format: League Name: ID\n\n{LEAGUE_NAME}: {LEAGUE_ID}\n")
    _write_json(os.path.join(CONFIG_DIR, "league_sports.json"), {str(LEAGUE_ID): "football"})
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.write("# test env\nMAX_CONCURRENT=5\n")

    league_dir = f"{LEAGUE_ID}_{LEAGUE_NAME.replace(' ', '_')}"
    season_slug = SEASON_NAME.replace(" ", "_").replace("/", "_")
    _write_json(
        os.path.join(DATA_DIR, "seasons", f"{league_dir}_seasons.json"),
        {"seasons": [{"id": SEASON_ID, "name": SEASON_NAME, "year": "26/27"}]},
    )

    rows = [
        (MATCH_IDS[0], "Arsenal", "Coventry City", 3, 0, "2026-08-21T22:00:00", 1787353200),
        (MATCH_IDS[1], "Hull City", "Manchester United", 2, 0, "2026-08-22T14:30:00", 1787409000),
    ]
    summary_csv = os.path.join(DATA_DIR, "matches", league_dir, f"{SEASON_ID}_{season_slug}_summary.csv")
    os.makedirs(os.path.dirname(summary_csv), exist_ok=True)
    with open(summary_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["round", "match_id", "home_team", "away_team", "home_score", "away_score",
                    "match_date", "status", "tournament", "season"])
        for mid, home, away, hs, as_, date, _ts in rows:
            w.writerow([1, mid, home, away, hs, as_, date, "Ended", LEAGUE_NAME, SEASON_NAME])

    # Yalnızca ilk maçın detayı var; ikincisi "eksik detay" senaryosu için boş bırakılır
    mid, home, away, hs, as_, _date, ts = rows[0]
    details = os.path.join(DATA_DIR, "match_details", league_dir, f"season_{season_slug}", str(mid))
    _write_json(os.path.join(details, "basic.json"), _basic(mid, home, away, hs, as_, ts))


_seed()


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_request_layer(request, monkeypatch):
    """
    Her test temiz bir istek katmanıyla başlar: "önce tarayıcı" modu bir testten diğerine
    taşınmaz. `browser` işaretli olmayan testler gerçek bir tarayıcı başlatamaz.
    """
    import src.utils as utils
    import src.challenge_solver as cs
    from src import bridge_health

    monkeypatch.setattr(utils, "_browser_first_until", 0.0)
    bridge_health.reset()  # köprü sağlık durumu da testten teste taşınmaz
    if request.node.get_closest_marker("browser") is None:
        async def _no_real_browser(self):
            raise RuntimeError("tests must not launch a real browser (mark the test with @pytest.mark.browser)")

        monkeypatch.setattr(cs.BrowserBridge, "_launch", _no_real_browser)
    yield
