"""
Veri yedeği (plan maddesi ST-19; docs/design/01-storage.md bölüm 9.1).

Yedek biçim 2'dedir (ST-24): `backups/backup_<kapsam>[_with_env]_<zaman>.zip`; `backup.json`, ayar dosyaları
`config/<ad>`, `.env` yalnızca istenirse `config/.env`, veri ağaçları veri dizinine göre (`seasons/...`).
Altın dosya (tests/golden/backup/members.json) sabit veri dizinlerinde (`store_fixtures`) her kapsamın üye
listesini tutar: ad, boyut, CRC ve sıkıştırma türü (zaman damgası ve dosya tarihleri hariç; `backup.json`,
`.meta/schema.json` ve `.meta/state.db` her çalıştırmada değişen kimlik ve zaman taşıdığı için yalnızca adlarıyla).
Yeniden üretmek için:

    REGEN_BACKUP_GOLDEN=1 python -m pytest tests/test_backup_service.py
"""
from __future__ import annotations

import json
import os
import re
import stat
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

import store_fixtures as sf
from sofascore_scraper.services.backup import BackupService
from sofascore_scraper.services.maintenance import MaintenanceService
from sofascore_scraper.store import StoreError, open_store
from sofascore_scraper.web import deps, league_sports

GOLDEN = Path(__file__).resolve().parent / "golden" / "backup" / "members.json"
REGEN = os.getenv("REGEN_BACKUP_GOLDEN") == "1"
SCOPES = ("all", "config", "seasons", "matches", "match_details")
NAME_RE = r"backup_{scope}{env}_\d{{8}}_\d{{6}}\.zip"
ENV_TEXT = "PROXY_URL=http://example.invalid:1\n"


# Her yedekte değişen üyeler (deponun kimliği, zaman damgaları): boyutları ve CRC'leri altın dosyaya girmez
VOLATILE = ("backup.json", ".meta/schema.json", ".meta/state.db")


def _members(path: str) -> List[List[Any]]:
    """
    Üye başına ad, boyut, CRC ve sıkıştırma türü. Sabit dizinlerin CSV dosyaları maç saatlerini makinenin saat
    diliminde yazar (içerik makineye bağlı, boyut değil): onların CRC'si altın dosyaya girmez (None).
    """
    with zipfile.ZipFile(path) as zf:
        return sorted([info.filename, None if info.filename in VOLATILE else info.file_size,
                       None if info.filename.endswith(".csv") or info.filename in VOLATILE else info.CRC,
                       info.compress_type] for info in zf.infolist())


@pytest.fixture
def configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Web yollarının gördüğü ayarlar: lig dosyası, spor eşlemesi ve .env bu testin dizininde. Satır sonu her
    platformda "\\n": altın dosya üyelerin boyutunu ve CRC'sini tutar (Windows'ta "\\r\\n" olurdu).
    """
    config = tmp_path / "config"
    config.mkdir()
    (config / "leagues.txt").write_text("17: Premier League\n", encoding="utf-8", newline="\n")
    (config / "league_sports.json").write_text('{"17": "football"}\n', encoding="utf-8", newline="\n")
    env = tmp_path / "secret.env"
    env.write_text(ENV_TEXT, encoding="utf-8", newline="\n")
    monkeypatch.setenv("SOFASCORE_ENV_FILE", str(env))
    monkeypatch.setattr(deps.config_manager(), "league_config_path", str(config / "leagues.txt"))
    return tmp_path


def _use_data_dir(monkeypatch: pytest.MonkeyPatch, data_dir: Path) -> None:
    monkeypatch.setattr(deps.config_manager(), "get_data_dir", lambda: str(data_dir))


def _create(scope: str, include_env: bool = False) -> Any:
    """Web sürecinin yedeği (v1'in `backup` işinin gövdesi gibi): lig dosyası ve spor eşlemesi pakete girer."""
    manager = deps.config_manager()
    config_path = manager.league_config_path
    return BackupService(open_store(os.path.abspath(manager.get_data_dir()))).create(
        scope, config_files=(config_path, league_sports.sidecar_path(config_path)), include_secrets=include_env,
    )


def _all_members(root: Path, monkeypatch: pytest.MonkeyPatch) -> Dict[str, List[List[Any]]]:
    found: Dict[str, List[List[Any]]] = {}
    for name in sf.FIXTURE_NAMES:
        fixture = sf.build_fixture(name, root / name / "data")
        _use_data_dir(monkeypatch, fixture.data_dir)
        for scope in SCOPES:
            for include_env in (False, True):
                made = _create(scope, include_env)
                path = os.path.join(fixture.data_dir, "backups", made.name)
                with_env = include_env and scope in ("all", "config")
                assert re.fullmatch(NAME_RE.format(scope=scope, env="_with_env" if with_env else ""), made.name)
                found[f"{name}/{scope}{'/env' if include_env else ''}"] = _members(path)
                os.remove(path)
    return found


def test_the_backup_members_equal_the_golden(configured: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    found = _all_members(configured / "fixtures", monkeypatch)
    if REGEN:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(found, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    assert found == json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX izin bitleri")
def test_a_backup_with_env_is_private(configured: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = sf.build_fixture("canonical", configured / "data")
    _use_data_dir(monkeypatch, fixture.data_dir)
    made = _create("config", True)
    path = fixture.data_dir / "backups" / made.name
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with zipfile.ZipFile(path) as zf:
        assert zf.read("config/.env").decode("utf-8") == ENV_TEXT


# --- BackupService ve Store.backup ----------------------------------------------------------------------

def test_the_service_writes_through_the_store_and_lists_newest_first(configured: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = sf.build_fixture("canonical", configured / "data")
    store = open_store(fixture.data_dir)
    service = BackupService(store)
    leagues = str(configured / "config" / "leagues.txt")
    assert service.list() == []  # backups/ henüz yok

    older = store.backup.create("seasons", now=datetime(2026, 1, 2, 3, 4, 5))
    newer = service.create("config", config_files=(leagues, str(configured / "missing.json")), include_secrets=True)

    assert older.name == "backup_seasons_20260102_030405.zip" and older.created_at == "2026-01-02T03:04:05"
    assert (older.scope, older.with_env) == ("seasons", False)
    assert newer.scope == "config" and newer.with_env and newer.name.startswith("backup_config_with_env_")
    assert newer.size == os.path.getsize(newer.path) and os.path.dirname(newer.path) == store.backup.directory
    with zipfile.ZipFile(newer.path) as zf:
        assert sorted(zf.namelist()) == ["backup.json", "config/.env", "config/leagues.txt"]  # olmayan atlanır
    (fixture.data_dir / "backups" / "notes.txt").write_text("not a backup", encoding="utf-8")
    assert [info.name for info in service.list()] == [newer.name, older.name]


def test_secrets_stay_out_unless_asked(configured: Path) -> None:
    fixture = sf.build_fixture("empty", configured / "data")
    info = BackupService(open_store(fixture.data_dir)).create("all")
    assert not info.with_env and "_with_env" not in info.name
    with zipfile.ZipFile(info.path) as zf:
        assert ".env" not in zf.namelist()
    # .env yalnızca ayar kapsamlarında pakete girer
    data_only = open_store(fixture.data_dir).backup.create("matches", env_file=os.environ["SOFASCORE_ENV_FILE"])
    assert not data_only.with_env


def test_a_failed_backup_leaves_no_partial_archive(configured: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = sf.build_fixture("canonical", configured / "data")
    store = open_store(fixture.data_dir)
    real_write = zipfile.ZipFile.write
    calls: List[str] = []

    def failing_write(self: zipfile.ZipFile, filename: Any, arcname: Any = None, *args: Any, **kwargs: Any) -> None:
        calls.append(str(arcname))
        if len(calls) == 3:
            raise OSError(28, "No space left on device")
        real_write(self, filename, arcname, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "write", failing_write)
    with pytest.raises(StoreError) as caught:
        store.backup.create("all")
    assert caught.value.errno == 28 and caught.value.fatal
    assert list((fixture.data_dir / "backups").iterdir()) == []
    with pytest.raises(ValueError):
        store.backup.create("everything")


# --- MaintenanceService.clear ---------------------------------------------------------------------------

@pytest.mark.parametrize("scope, cleared, kept", [
    ("all", ("match_details", "matches", "seasons"), ()),
    ("match_details", ("match_details",), ("matches", "seasons")),
    ("events", ("match_details",), ("matches", "seasons")),
    ("matches", ("matches",), ("match_details", "seasons")),
    ("schedules", ("matches",), ("match_details", "seasons")),
    ("seasons", ("seasons",), ("match_details", "matches")),
])
def test_clear_maps_todays_scope_names_to_the_store(tmp_path: Path, scope: str, cleared: Tuple[str, ...],
                                                    kept: Tuple[str, ...]) -> None:
    fixture = sf.build_fixture("canonical", tmp_path / "data")
    store = open_store(fixture.data_dir)

    report = MaintenanceService(store=store).clear(scope, confirm=True)  # type: ignore[arg-type]

    assert report.cleared == cleared and report.catalog_rebuilt
    for tree in cleared:
        assert (fixture.data_dir / tree).is_dir() and not any((fixture.data_dir / tree).iterdir())
    for tree in kept:
        assert any((fixture.data_dir / tree).iterdir())
    assert (fixture.data_dir / "score_changes.jsonl").exists()  # değişiklik günlüğü temizlenmez
    assert store.catalog.diff_from_rebuild() == []


def test_clear_needs_a_confirmation_and_a_known_scope(tmp_path: Path) -> None:
    store = open_store(sf.build_fixture("canonical", tmp_path / "data").data_dir)
    service = MaintenanceService(store=store)
    with pytest.raises(ValueError):
        service.clear("all", confirm=False)
    with pytest.raises(ValueError):
        service.clear("everything", confirm=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        service.recheck_unavailable()  # bağlamsız servis yalnızca temizler
    with pytest.raises(ValueError):
        MaintenanceService()
    assert any((tmp_path / "data" / "match_details").iterdir())


def test_clear_reports_only_the_trees_that_existed(tmp_path: Path) -> None:
    fixture = sf.build_fixture("processed_only", tmp_path / "data")
    report = MaintenanceService(store=open_store(fixture.data_dir)).clear("all", confirm=True)
    assert report.cleared == ("match_details",)
    assert not (fixture.data_dir / "matches").exists() and not (fixture.data_dir / "seasons").exists()

