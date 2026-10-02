"""
Veri yedeği (plan maddesi ST-19; docs/design/01-storage.md bölüm 9.1).

Yedek, web API'sinin bugünkü zip düzeninde ve adıyla üretilir: `backups/backup_<kapsam>[_with_env]_<zaman>.zip`;
ayar dosyaları zip'in kökünde (dosya adlarıyla), `.env` yalnızca istenirse `.env` adıyla, veri ağaçları
`<veri dizininin adı>/seasons/...` biçiminde. Altın dosya (tests/golden/backup/members.json) sabit veri
dizinlerinde (`store_fixtures`) her kapsamın üye listesini tutar: ad, boyut, CRC ve sıkıştırma türü (zaman
damgası ve dosya tarihleri hariç). Yeniden üretmek için:

    REGEN_BACKUP_GOLDEN=1 python -m pytest tests/test_backup_service.py
"""
from __future__ import annotations

import json
import os
import re
import stat
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

import store_fixtures as sf
from src.web.routes import data as data_routes

GOLDEN = Path(__file__).resolve().parent / "golden" / "backup" / "members.json"
REGEN = os.getenv("REGEN_BACKUP_GOLDEN") == "1"
SCOPES = ("all", "config", "seasons", "matches", "match_details")
NAME_RE = r"backup_{scope}{env}_\d{{8}}_\d{{6}}\.zip"
ENV_TEXT = "PROXY_URL=http://example.invalid:1\n"


def _members(path: str) -> List[List[Any]]:
    with zipfile.ZipFile(path) as zf:
        return sorted([info.filename, info.file_size, info.CRC, info.compress_type] for info in zf.infolist())


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Web yollarının gördüğü ayarlar: lig dosyası, spor eşlemesi ve .env bu testin dizininde."""
    config = tmp_path / "config"
    config.mkdir()
    (config / "leagues.txt").write_text("17: Premier League\n", encoding="utf-8")
    (config / "league_sports.json").write_text('{"17": "football"}\n', encoding="utf-8")
    env = tmp_path / "secret.env"
    env.write_text(ENV_TEXT, encoding="utf-8")
    monkeypatch.setenv("SOFASCORE_ENV_FILE", str(env))
    monkeypatch.setattr(data_routes.config_manager, "league_config_path", str(config / "leagues.txt"))
    return tmp_path


def _use_data_dir(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> None:
    monkeypatch.setattr(data_routes.config_manager, "get_data_dir", lambda: str(data_dir))


def _all_members(root: Path, monkeypatch: pytest.MonkeyPatch) -> Dict[str, List[List[Any]]]:
    found: Dict[str, List[List[Any]]] = {}
    for name in sf.FIXTURE_NAMES:
        fixture = sf.build_fixture(name, root / name / "data")
        _use_data_dir(monkeypatch, fixture.data_dir)
        for scope in SCOPES:
            for include_env in (False, True):
                made = data_routes._create_backup_sync(scope, include_env)
                path = os.path.join(fixture.data_dir, "backups", made["filename"])
                with_env = include_env and scope in ("all", "config")
                assert re.fullmatch(NAME_RE.format(scope=scope, env="_with_env" if with_env else ""), made["filename"])
                assert made["download_url"] == f"/api/data/backups/{made['filename']}"
                found[f"{name}/{scope}{'/env' if include_env else ''}"] = _members(path)
                os.remove(path)
    return found


def test_the_backup_members_equal_the_golden(configured: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    found = _all_members(configured / "fixtures", monkeypatch)
    if REGEN:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(found, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    assert found == json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX izin bitleri")
def test_a_backup_with_env_is_private(configured: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = sf.build_fixture("canonical", configured / "data")
    _use_data_dir(monkeypatch, fixture.data_dir)
    made = data_routes._create_backup_sync("config", True)
    path = fixture.data_dir / "backups" / made["filename"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with zipfile.ZipFile(path) as zf:
        assert zf.read(".env").decode("utf-8") == ENV_TEXT
