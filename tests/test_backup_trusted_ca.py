"""
Güvenilen sertifikalar (`CONFIG_DIR/ca/*.pem`, #230) yedeğe girer ve geri yüklenir (docs/design/01-storage.md
9.1 ve 9.2; plan bölüm 19'un "Backups do not include the `ca` folder" maddesi).

  * Ayar taşıyan kapsamlar (`all`, `state`) güven deposunun okuyacağı her `.pem` dosyasını `config/ca.<ad>`
    olarak alır; `backup.json` adlarını ve SHA-256'larını listeler, `verify` denetler. `.bundle/`, başka dosyalar
    ve `data` kapsamı almaz.
  * Üyeler `config/` altında düzdür: eski sürümler onları ayar dosyası sayıp atlar, yedeği reddetmez.
  * Geri yükleme dosyaları 0600 ve atomik yazar; aynı içerikli dosyaya dokunmaz, başka içerikliyi değiştirir,
    yedekte olmayan sertifikayı silmez; bir adım başarısız olursa önceki hal geri gelir.
  * Servis deponun yerini kendisi verir; geri yüklemeden sonra birleşik CA dosyası yeni sertifikayı taşır,
    köprü tarayıcısı yeniden başlatılmak üzere kapatılır ve günlüğe parmak izleriyle tek satır yazılır.

Sertifikalar sahtedir ve test oturumunda üretilir (tests/trust_pki.py); hiçbir istek gönderilmez.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

import store_fixtures as sf
import trust_pki
from sofascore_scraper.client import bridge, trust
from sofascore_scraper.services.backup import BackupService
from sofascore_scraper.store import open_store
from sofascore_scraper.store import backup as backup_mod
from test_store_backup import backup_all, into, manifest, rich_store, write_zip

POSIX = pytest.mark.skipif(os.name != "posix", reason="POSIX izin bitleri")
PREFIX = "config/ca."


def _pem(name: str) -> bytes:
    return trust_pki.pki().pem(name).encode("ascii")


def _fingerprint(name: str) -> str:
    return hashlib.sha256(trust_pki.pki().der(name)).hexdigest()


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _ca_dir(root: Path, files: Dict[str, bytes]) -> Path:
    directory = root / "config" / "ca"
    directory.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (directory / name).write_bytes(data)
    return directory


def _members(path: str) -> List[str]:
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()


def _manifest_of(path: str) -> Dict[str, Any]:
    with zipfile.ZipFile(path) as zf:
        return json.loads(zf.read("backup.json"))


def _source(tmp_path: Path, files: Dict[str, bytes], scope: str = "all") -> Tuple[backup_mod.BackupInfo, Path]:
    store, _ = rich_store(tmp_path / "source")
    directory = _ca_dir(tmp_path / "source", files)
    return backup_all(store, scope, ca_dir=str(directory)), directory


# --- yedek -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("scope,included", [("all", True), ("state", True), ("data", False)])
def test_the_settings_scopes_take_every_pem_file_of_the_store(tmp_path: Path, scope: str, included: bool) -> None:
    root, other = _pem("root"), _pem("other-root")
    store, _ = rich_store(tmp_path / "source")
    directory = _ca_dir(tmp_path / "source", {"trusted-0123456789abcdef.pem": root, "Corp Root.PEM": other,
                                              "notes.txt": b"not a certificate", ".pem": b"no stem"})
    (directory / ".bundle").mkdir()
    (directory / ".bundle" / "ca-bundle.pem").write_bytes(root + other)  # türetilmiş: girmez
    (directory / ".trusted-x.pem.ab12cd34.tmp").write_bytes(root)  # yarım yazılmış: girmez
    info = backup_all(store, scope, ca_dir=str(directory))

    names = _members(info.path)
    found = sorted(n for n in names if n.startswith(PREFIX))
    document = _manifest_of(info.path)
    if not included:
        assert found == [] and "trusted_ca" not in document and "trusted_ca_files" not in document["counts"]
        return
    assert found == ["config/ca.Corp Root.PEM", "config/ca.trusted-0123456789abcdef.pem"]
    assert not [n for n in names if "bundle" in n or n.endswith(".tmp") or "notes" in n]
    assert document["trusted_ca"] == [
        {"file": "Corp Root.PEM", "file_sha256": hashlib.sha256(other).hexdigest()},
        {"file": "trusted-0123456789abcdef.pem", "file_sha256": hashlib.sha256(root).hexdigest()},
    ]
    assert document["counts"]["trusted_ca_files"] == 2
    with zipfile.ZipFile(info.path) as zf:
        assert zf.read("config/ca.trusted-0123456789abcdef.pem") == root
    # Eski sürümler `config/<ad>` üyesini ayar dosyası sayıp atlar; alt dizindeki bir üyeyi tanımaz ve bütün
    # yedeği reddederdi (3.1.2'nin `_plan_v2`'si). Üyeler bu yüzden düzdür.
    assert all(n.count("/") == 1 for n in found)


def test_without_certificates_the_backup_is_as_before(tmp_path: Path) -> None:
    info, _ = _source(tmp_path, {})
    document = _manifest_of(info.path)
    assert "trusted_ca" not in document and "trusted_ca_files" not in document["counts"]
    assert not [n for n in _members(info.path) if n.startswith(PREFIX)]
    store, _ = rich_store(tmp_path / "other")
    missing = backup_all(store, ca_dir=str(tmp_path / "no-such-folder"))
    assert "trusted_ca" not in _manifest_of(missing.path)


def test_a_config_file_never_takes_the_name_space_of_the_certificates(tmp_path: Path) -> None:
    store, _ = rich_store(tmp_path / "source")
    odd = tmp_path / "ca.looks-like-one.pem"
    odd.write_bytes(b"a config file")
    info = backup_all(store, config_files=[str(odd)])
    assert "config/ca.looks-like-one.pem" not in _members(info.path)


def test_verify_covers_the_certificates(tmp_path: Path) -> None:
    info, _ = _source(tmp_path, {"trusted-a.pem": _pem("root")})
    store = open_store(tmp_path / "source" / "data")
    assert store.backup.verify(info.name).ok

    target = tmp_path / "check" / "data"
    open_store(target)
    listed = [{"file": "trusted-a.pem", "file_sha256": "0" * 64}, {"file": "gone.pem", "file_sha256": "0" * 64}]
    name = write_zip(target, "backup_state_20261120_100000.zip", {
        "backup.json": manifest(scope="state", trusted_ca=listed), "config/ca.trusted-a.pem": _pem("root")})
    check = open_store(target).backup.verify(name)
    assert not check.ok
    assert check.problems == ("config/ca.trusted-a.pem: the SHA-256 does not match backup.json",
                              "config/ca.gone.pem: listed in backup.json but missing")


# --- geri yükleme ----------------------------------------------------------------------------------------

@POSIX
def test_restore_writes_new_files_replaces_changed_ones_and_keeps_the_rest(tmp_path: Path) -> None:
    root, other, issuing = _pem("root"), _pem("other-root"), _pem("issuing")
    info, _ = _source(tmp_path, {"same.pem": root, "changed.pem": other, "new.pem": issuing})
    target_dir = tmp_path / "target" / "data"
    name = into(info.path, target_dir)
    directory = _ca_dir(tmp_path / "target", {"same.pem": root, "changed.pem": issuing, "mine.pem": other})
    os.utime(directory / "same.pem", ns=(1_000_000_000, 1_000_000_000))
    store = open_store(target_dir)

    planned = store.backup.restore(name, dry_run=True, ca_dir=str(directory))
    assert [m for m in planned.restored if m.startswith(PREFIX)] == ["config/ca.changed.pem", "config/ca.new.pem"]
    assert [m for m in planned.replaced if m.startswith(PREFIX)] == ["config/ca.changed.pem"]
    assert not (directory / "new.pem").exists() and (directory / "changed.pem").read_bytes() == issuing

    report = store.backup.restore(name, ca_dir=str(directory))

    assert [m for m in report.restored if m.startswith(PREFIX)] == ["config/ca.changed.pem", "config/ca.new.pem"]
    assert [m for m in report.replaced if m.startswith(PREFIX)] == ["config/ca.changed.pem"]
    assert not [m for m in report.skipped if m.startswith(PREFIX)] and report.verify_ok is True
    assert (directory / "changed.pem").read_bytes() == other  # yedek kazanır
    assert (directory / "new.pem").read_bytes() == issuing and _mode(directory / "new.pem") == 0o600
    assert (directory / "same.pem").stat().st_mtime_ns == 1_000_000_000  # dokunulmadı
    assert (directory / "mine.pem").read_bytes() == other  # yedekte yok: silinmez
    assert not [p for p in directory.iterdir() if p.name.endswith(".tmp")]


@POSIX
def test_restore_creates_a_private_store_when_there_was_none(tmp_path: Path) -> None:
    info, _ = _source(tmp_path, {"trusted-a.pem": _pem("root")}, "state")
    target_dir = tmp_path / "target" / "data"
    name = into(info.path, target_dir)
    directory = tmp_path / "target" / "fresh-config" / "ca"

    report = open_store(target_dir).backup.restore(name, ca_dir=str(directory))

    assert "config/ca.trusted-a.pem" in report.restored and "config/ca.trusted-a.pem" not in report.replaced
    assert (directory / "trusted-a.pem").read_bytes() == _pem("root")
    assert _mode(directory) == 0o700 and _mode(directory / "trusted-a.pem") == 0o600


def test_without_a_store_the_certificates_are_skipped(tmp_path: Path) -> None:
    info, _ = _source(tmp_path, {"trusted-a.pem": _pem("root")})
    target_dir = tmp_path / "target" / "data"
    name = into(info.path, target_dir)

    report = open_store(target_dir).backup.restore(name)

    assert "config/ca.trusted-a.pem" in report.skipped and "config/ca.trusted-a.pem" not in report.restored


def test_an_old_backup_without_certificates_restores_and_leaves_the_store_alone(tmp_path: Path) -> None:
    store, _ = rich_store(tmp_path / "source")
    info = backup_all(store)  # bu sürümden önceki yedek gibi: sertifika üyesi ve `trusted_ca` yok
    target_dir = tmp_path / "target" / "data"
    name = into(info.path, target_dir)
    directory = _ca_dir(tmp_path / "target", {"mine.pem": _pem("root")})

    report = open_store(target_dir).backup.restore(name, ca_dir=str(directory))

    assert not [m for m in report.restored + report.replaced + report.skipped if m.startswith(PREFIX)]
    assert report.verify_ok is True and [p.name for p in directory.iterdir()] == ["mine.pem"]


def test_an_oversized_certificate_member_is_skipped(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    target_dir = tmp_path / "data"
    open_store(target_dir)
    name = write_zip(target_dir, "backup_state_20261120_100000.zip", {
        "backup.json": manifest(scope="state"), "config/ca.big.pem": b"x" * (1024 * 1024 + 1)})
    directory = tmp_path / "config" / "ca"

    with caplog.at_level(logging.WARNING):
        report = open_store(target_dir).backup.restore(name, ca_dir=str(directory))

    assert "config/ca.big.pem" in report.skipped and not directory.exists()
    assert "config/ca.big.pem" in caplog.text


def test_a_failed_step_puts_the_previous_certificates_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    info, _ = _source(tmp_path, {"a.pem": _pem("root"), "b.pem": _pem("other-root")})
    target, fixture = rich_store(tmp_path / "target")
    name = into(info.path, fixture.data_dir)
    directory = _ca_dir(tmp_path / "target", {"a.pem": _pem("issuing")})
    real = backup_mod.files.write_bytes

    def failing(path: Any, data: bytes, **kwargs: Any) -> None:
        if os.fspath(path) == str(directory / "b.pem") and data == _pem("other-root"):
            raise OSError(28, "No space left on device")
        real(path, data, **kwargs)

    monkeypatch.setattr(backup_mod.files, "write_bytes", failing)
    with pytest.raises(Exception) as caught:
        target.backup.restore(name, force=True, ca_dir=str(directory))

    assert getattr(caught.value, "errno", None) == 28
    assert (directory / "a.pem").read_bytes() == _pem("issuing") and not (directory / "b.pem").exists()


# --- servis ----------------------------------------------------------------------------------------------

def test_the_service_carries_the_trust_decision_to_another_machine(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    restarts: List[bool] = []
    monkeypatch.setattr(bridge, "restart_for_trust_change", lambda: restarts.append(True) or True)
    monkeypatch.setenv("SOFASCORE_CONFIG_DIR", str(tmp_path / "source" / "config"))
    certificate = trust.pem_certificates(trust_pki.pki().pem("root"))[0]
    trust.add(certificate)
    source = open_store(sf.build_fixture("canonical", tmp_path / "source" / "data").data_dir)
    info = BackupService(source).create("all")
    assert BackupService(source).verify(info.name).ok

    # Başka makine: depoda yalnızca başka bir sertifika var ve birleşik CA dosyası ona göre yazılmış
    monkeypatch.setenv("SOFASCORE_CONFIG_DIR", str(tmp_path / "target" / "config"))
    trust.add(trust.pem_certificates(trust_pki.pki().pem("other-root"))[0])
    bundle = trust.bundle_path()
    assert bundle is not None and certificate.pem not in Path(bundle).read_text(encoding="utf-8")
    target_dir = tmp_path / "target" / "data"
    name = into(info.path, target_dir)

    assert BackupService(open_store(target_dir)).restore(name, dry_run=True).restored and restarts == []
    with caplog.at_level(logging.INFO):
        report = BackupService(open_store(target_dir)).restore(name)

    stored_file = f"trusted-{certificate.sha256[:16]}.pem"
    assert f"config/ca.{stored_file}" in report.restored
    assert {item.certificate.sha256 for item in trust.stored()} == {certificate.sha256, _fingerprint("other-root")}
    bundle = trust.bundle_path()
    assert bundle is not None and certificate.pem in Path(bundle).read_text(encoding="utf-8")
    assert restarts == [True]
    lines = [r.getMessage() for r in caplog.records if "Trusted certificates restored" in r.getMessage()]
    assert len(lines) == 1 and "1 certificate(s) in 1 file(s)" in lines[0] and certificate.sha256 in lines[0]
    assert str(tmp_path) not in lines[0]

    # İkinci kez: dosya aynı, yazılmaz; tarayıcı yeniden başlatılmaz
    again = BackupService(open_store(target_dir)).restore(name, force=True)
    assert not [m for m in again.restored if m.startswith(PREFIX)] and restarts == [True]
