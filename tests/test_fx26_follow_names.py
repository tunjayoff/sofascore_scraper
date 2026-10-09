"""
Takip adlarında `/` (FX-26, canlı doğrulama M4): tenis, badminton ve padelde çiftlerin maçı "L. Andersson / O.
Andersson - …" adını taşır ve "Bu maçı takip et" bu adı önerir; ad kuralı onu reddediyordu.

`/` lig (turnuva) takibinde yasak kalır: lig adı leagues.txt'in `Ad: ID` satırına ve 2.x dizin adlarına girer
(`sofascore_scraper/store/legacy.py` `league_dir_name`). Takım, oyuncu ve maç takibinin adı yalnızca takip
tablosundadır (`Store.follows`), hiçbir dosya ya da dizin adına dönüşmez: orada `/` serbesttir. Satır sonu, `:` ve
`\\` her türde yasaktır.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from sofascore_scraper.config_manager import ConfigManager
from sofascore_scraper.errors import UsageError
from sofascore_scraper.services.follows import check_name
from sofascore_scraper.store import open_store
from sofascore_scraper.web.app import app

client = TestClient(app)
DOUBLES = "L. Andersson / O. Andersson – M. Nilsson / K. Berg"


@pytest.fixture
def leagues(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    config = tmp_path / "config"
    config.mkdir()
    (config / "leagues.txt").write_text("# leagues\nPremier League: 17\n", encoding="utf-8")
    (config / "league_sports.json").write_text(json.dumps({"17": "football"}), encoding="utf-8")
    data = tmp_path / "data"
    monkeypatch.setenv("SOFASCORE_STORAGE__DATA_DIR", str(data))
    open_store(data)
    manager = ConfigManager()
    monkeypatch.setattr(manager, "league_config_path", str(config / "leagues.txt"))
    monkeypatch.setattr(manager, "_leagues_mtime", None)
    manager.mirror_follows()
    yield config / "leagues.txt"
    monkeypatch.undo()
    manager._leagues_mtime = None
    manager.get_leagues()


def ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()["data"]


def refused(response: Any) -> str:
    assert response.status_code == 400, response.text
    body = response.json()["error"]
    assert body["code"] == "invalid_request" and body["details"]["field"] == "name"
    return str(body["message"])


def test_rule_by_kind() -> None:
    assert check_name(f"  {DOUBLES} ", "event") == DOUBLES
    assert check_name("Andersson / Andersson", "team") == "Andersson / Andersson"
    assert check_name("Mixed Doubles A/B", "player") == "Mixed Doubles A/B"
    for kind in ("tournament", "team", "player", "event"):
        for bad in ("La:Liga", "a\\b", "line\nbreak", "...", "", "x" * 81):
            with pytest.raises(UsageError):
                check_name(bad, kind)
    with pytest.raises(UsageError, match="'/'"):
        check_name("ATP/WTA Cup")
    with pytest.raises(UsageError, match="'/'"):
        check_name("ATP/WTA Cup", "tournament")


def test_a_doubles_match_is_followed_with_its_name(leagues: Path) -> None:
    follow = ok(client.post("/api/v1/follows", json={"kind": "event", "entity_id": 17260001, "name": DOUBLES,
                                                     "sport": "padel"}), 201)
    assert follow["name"] == DOUBLES
    assert ok(client.get("/api/v1/follows/event:17260001"))["name"] == DOUBLES
    # leagues.txt değişmedi: maç takibi yalnızca takip tablosunda
    assert "Andersson" not in leagues.read_text(encoding="utf-8")


def test_a_doubles_pair_as_team_and_a_rename(leagues: Path) -> None:
    ok(client.post("/api/v1/follows", json={"kind": "team", "entity_id": 400001, "name": "Andersson / Andersson",
                                            "sport": "padel"}), 201)
    renamed = ok(client.patch("/api/v1/follows/team:400001", json={"name": "L. Andersson / O. Andersson"}))
    assert renamed["name"] == "L. Andersson / O. Andersson"
    assert "':' or '\\'" in refused(client.patch("/api/v1/follows/team:400001", json={"name": "A: B"}))


def test_a_league_name_still_refuses_a_slash(leagues: Path) -> None:
    assert "'/'" in refused(client.post("/api/v1/follows", json={"entity_id": 8, "name": "La/Liga"}))
    assert "'/'" in refused(client.patch("/api/v1/follows/tournament:17", json={"name": "Premier/League"}))
    assert [line for line in leagues.read_text(encoding="utf-8").splitlines() if line and line[0] != "#"] == \
        ["Premier League: 17"]
