"""
Web arayüzünde kaydedilen ayarlar (`CONFIG_DIR/overrides.json`) yedeğe girer ve geri yüklenir (plan maddesi
FX-22; docs/design/01-storage.md bölüm 9).

  * Ayar taşıyan kapsamlar (`all`, `state`) dosyayı `config/overrides.json` olarak alır, `data` almaz (2.x'in
    `config` kapsamı 3.1'de kalktı).
    Dosya proxy adresini parolasıyla taşıyabilir: onu taşıyan yedek yalnızca sahibince okunur (0600).
  * Geri yükleme dosyayı veriyle aynı adımda, 0600 izniyle yerine koyar; bir adım başarısız olursa önceki hali
    geri gelir. Yeri verilmezse ya da belge bozuksa dosyaya dokunulmaz (`skipped`); içerik günlüğe yazılmaz.
  * Dosyayı taşımayan eski yedekler eskisi gibi geri yüklenir ve bugünkü dosyaya dokunmaz.
  * Servis (`ssc backup`, web işi) dosyanın yerini kendisi verir ve geri yüklemeden sonra ayarları yeniden
    yükler; ayarlar kurulamazsa önceki dosya geri konur.
"""
from __future__ import annotations

import json
import logging
import os
import stat
import zipfile
from pathlib import Path
from typing import Any, Iterator, List

import pytest

import conftest
import store_fixtures as sf
from sofascore_scraper import redact
from sofascore_scraper.config import loader, overrides
from sofascore_scraper.services.backup import BackupService
from sofascore_scraper.store import Store, open_store
from sofascore_scraper.store import backup as backup_mod
from test_store_backup import STAMP, backup_all, into, manifest, rich_store, state_rows, write_zip

SECRET = "s3cret-pass"
DOCUMENT = {"client": {"proxy": f"http://user:{SECRET}@proxy.invalid:8080", "retries": 7}}
MEMBER = "config/overrides.json"
POSIX = pytest.mark.skipif(os.name != "posix", reason="POSIX izin bitleri")


def _write(path: Path, document: Any) -> bytes:
    data = (json.dumps(document, indent=2) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _source_backup(tmp_path: Path, scope: str = "all") -> tuple[Store, backup_mod.BackupInfo, bytes]:
    store, _ = rich_store(tmp_path / "source")
    settings = tmp_path / "source" / "config" / "overrides.json"
    data = _write(settings, DOCUMENT)
    return store, backup_all(store, scope, overrides_file=str(settings)), data


# --- yedek -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("scope,included", [("all", True), ("state", True), ("data", False)])
def test_the_settings_scopes_take_the_overrides_file(tmp_path: Path, scope: str, included: bool) -> None:
    _, info, data = _source_backup(tmp_path, scope)

    with zipfile.ZipFile(info.path) as zf:
        assert (MEMBER in zf.namelist()) is included
        if included:
            assert zf.read(MEMBER) == data
    assert not info.with_env and "_with_env" not in info.name


@POSIX
def test_a_backup_holding_the_overrides_file_is_private(tmp_path: Path) -> None:
    store, info, _ = _source_backup(tmp_path, "state")
    assert _mode(Path(info.path)) == 0o600
    # Dosya yoksa (ayar kaydedilmemiş) yedek eskisi gibidir: üye yok, izin umask'e kalır
    plain = store.backup.create("state", overrides_file=str(tmp_path / "missing.json"),
                                now=STAMP.replace(second=5))
    with zipfile.ZipFile(plain.path) as zf:
        assert MEMBER not in zf.namelist()


def test_a_config_file_named_overrides_json_is_not_added_twice(tmp_path: Path) -> None:
    store, _ = rich_store(tmp_path / "source")
    settings = tmp_path / "config" / "overrides.json"
    _write(settings, DOCUMENT)
    info = backup_all(store, "state", config_files=[str(settings)], overrides_file=str(settings))
    with zipfile.ZipFile(info.path) as zf:
        assert zf.namelist().count(MEMBER) == 1


# --- geri yükleme ----------------------------------------------------------------------------------------

@POSIX
def test_restore_puts_the_overrides_file_back_privately(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _, info, data = _source_backup(tmp_path)
    target_dir = tmp_path / "target" / "data"
    name = into(info.path, target_dir)
    target = open_store(target_dir)
    settings = tmp_path / "target" / "config" / "overrides.json"
    _write(settings, {"client": {"rate": 1}})
    os.chmod(settings, 0o644)

    planned = target.backup.restore(name, dry_run=True, overrides_file=str(settings))
    assert MEMBER in planned.restored and MEMBER in planned.replaced and MEMBER not in planned.skipped
    assert json.loads(settings.read_text(encoding="utf-8")) == {"client": {"rate": 1}}

    with caplog.at_level(logging.DEBUG):
        report = target.backup.restore(name, overrides_file=str(settings))

    assert report.restored[-1] == MEMBER and MEMBER in report.replaced and report.verify_ok is True
    assert settings.read_bytes() == data and _mode(settings) == 0o600
    assert SECRET not in caplog.text
    assert not [p for p in settings.parent.iterdir() if p.name.endswith(".tmp")]


def test_restore_creates_the_overrides_file_when_there_was_none(tmp_path: Path) -> None:
    _, info, data = _source_backup(tmp_path, "state")
    target_dir = tmp_path / "target" / "data"
    name = into(info.path, target_dir)
    settings = tmp_path / "target" / "fresh-config" / "overrides.json"

    report = open_store(target_dir).backup.restore(name, overrides_file=str(settings))

    assert report.restored[-1] == MEMBER and MEMBER not in report.replaced
    assert settings.read_bytes() == data


def test_without_a_target_the_overrides_file_is_skipped(tmp_path: Path) -> None:
    _, info, _ = _source_backup(tmp_path)
    target_dir = tmp_path / "target" / "data"
    name = into(info.path, target_dir)

    report = open_store(target_dir).backup.restore(name)

    assert MEMBER in report.skipped and MEMBER not in report.restored


def test_an_old_backup_without_the_file_restores_and_leaves_the_settings_alone(tmp_path: Path) -> None:
    store, _ = rich_store(tmp_path / "source")
    info = backup_all(store)  # FX-22'den önceki yedek gibi: ayar belgesi yok
    target_dir = tmp_path / "target" / "data"
    name = into(info.path, target_dir)
    settings = tmp_path / "target" / "config" / "overrides.json"
    before = _write(settings, {"display": {"language": "tr"}})

    report = open_store(target_dir).backup.restore(name, overrides_file=str(settings))

    assert MEMBER not in report.restored + report.replaced + report.skipped
    assert report.verify_ok is True and settings.read_bytes() == before


@pytest.mark.parametrize("payload", [b"{not json", b"[1, 2]", b"\xff\xfe"])
def test_a_broken_overrides_member_is_skipped_and_not_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture,
                                                            payload: bytes) -> None:
    target_dir = tmp_path / "data"
    open_store(target_dir)
    name = write_zip(target_dir, "backup_config_20261002_210000.zip",
                     {"backup.json": manifest(scope="config"), MEMBER: payload + SECRET.encode()})
    settings = tmp_path / "config" / "overrides.json"

    with caplog.at_level(logging.WARNING):
        report = open_store(target_dir).backup.restore(name, overrides_file=str(settings))

    assert MEMBER in report.skipped and MEMBER not in report.restored and not settings.exists()
    assert "overrides.json" in caplog.text and SECRET not in caplog.text


def test_a_failed_step_puts_the_previous_overrides_file_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, info, _ = _source_backup(tmp_path)
    target, target_fixture = rich_store(tmp_path / "target")
    name = into(info.path, target_fixture.data_dir)
    settings = tmp_path / "target" / "config" / "overrides.json"
    before = _write(settings, {"client": {"rate": 1}})
    before_state = state_rows(target)
    real = backup_mod.files.write_bytes
    calls: List[str] = []

    def failing(path: Any, data: bytes, **kwargs: Any) -> None:
        if os.fspath(path) == str(settings):
            calls.append("write")
            if len(calls) == 1:
                real(path, data, **kwargs)  # yeni belge yazıldı, sonra disk doldu
                raise OSError(28, "No space left on device")
        real(path, data, **kwargs)

    monkeypatch.setattr(backup_mod.files, "write_bytes", failing)
    with pytest.raises(Exception) as caught:
        target.backup.restore(name, force=True, overrides_file=str(settings))

    assert getattr(caught.value, "errno", None) == 28
    assert settings.read_bytes() == before and len(calls) == 2
    assert state_rows(target) == before_state


# --- servis ----------------------------------------------------------------------------------------------

@pytest.fixture
def sandbox() -> Iterator[Path]:
    """Ayar yükleyicisinin okuduğu overrides.json; test bitince dosya, ortam ve etkin ayarlar eski haline döner."""
    path = overrides.overrides_path()
    assert path == Path(conftest.CONFIG_DIR) / "overrides.json" and not path.exists()
    environ_before = dict(os.environ)
    yield path
    for leftover in (path, Path(f"{path}.lock")):
        leftover.unlink(missing_ok=True)
    for name in set(os.environ) - set(environ_before):
        del os.environ[name]
    os.environ.update(environ_before)
    loader.reload()
    redact.refresh()


def _service_backup(tmp_path: Path, sandbox: Path) -> tuple[str, Path]:
    source = open_store(sf.build_fixture("canonical", tmp_path / "source" / "data").data_dir)
    _write(sandbox, DOCUMENT)
    info = BackupService(source).create("all")
    sandbox.unlink()
    loader.reload()
    target_dir = tmp_path / "target" / "data"
    return into(info.path, target_dir), target_dir


def test_the_service_backs_up_and_restores_the_settings(tmp_path: Path, sandbox: Path,
                                                        caplog: pytest.LogCaptureFixture) -> None:
    name, target_dir = _service_backup(tmp_path, sandbox)
    assert loader.active().settings.client.retries != 7

    with caplog.at_level(logging.DEBUG):
        report = BackupService(open_store(target_dir)).restore(name)

    assert MEMBER in report.restored
    assert json.loads(sandbox.read_text(encoding="utf-8")) == DOCUMENT
    assert loader.active().settings.client.retries == 7  # ayarlar yeniden yüklendi
    assert SECRET not in caplog.text
    if os.name == "posix":
        assert _mode(sandbox) == 0o600


def test_settings_that_cannot_be_loaded_are_not_kept(tmp_path: Path, sandbox: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    name, target_dir = _service_backup(tmp_path, sandbox)
    before = _write(sandbox, {"client": {"retries": 8}})
    loader.reload()
    real, calls = loader.reload, []

    def failing_once() -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise ValueError(f"cannot use {SECRET}")
        return real()

    monkeypatch.setattr(loader, "reload", failing_once)
    report = BackupService(open_store(target_dir)).restore(name, force=True)

    assert MEMBER in report.skipped and MEMBER not in report.restored and MEMBER not in report.replaced
    assert sandbox.read_bytes() == before and len(calls) == 2
    assert loader.active().settings.client.retries == 8


def test_a_settings_save_leaves_no_lock_file_and_a_backup_takes_none(tmp_path: Path, sandbox: Path) -> None:
    """
    FX-25: ayar kaydından sonra `overrides.json.lock` kalmaz (kilit dosyası iş bitince silinir) ve yedek,
    önceki bir sürümün bıraktığı `.lock` dosyasını da almaz: ayar dosyaları adlarıyla verilir.
    """
    from sofascore_scraper import config_files

    overrides.write_overrides({"client.retries": 7})
    assert sandbox.is_file()
    lock = Path(f"{sandbox}.lock")
    if config_files.fcntl is not None:
        assert not lock.exists()

    lock.write_text("", encoding="utf-8")  # eski bir sürümden kalan kilit dosyası
    source = open_store(sf.build_fixture("canonical", tmp_path / "source" / "data").data_dir)
    info = BackupService(source).create("all", config_files=[str(sandbox.parent / "league_sports.json")])

    with zipfile.ZipFile(info.path) as zf:
        names = zf.namelist()
    assert MEMBER in names
    assert [n for n in names if n.endswith(".lock")] == []
