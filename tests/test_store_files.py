"""
sofascore_scraper/store/errors.py ve sofascore_scraper/store/files.py: Store hata sınıfları ve dosya ilkelleri
(docs/design/01-storage.md, bölüm 2.3 ve 4.4).

Config dosyalarının yazımı (2.x'in sofascore_scraper/fsutil.py'si) sofascore_scraper/config_files.py'dedir; testleri
tests/test_config_files.py'de. Buradaki testler Store katmanını dener: StoreError çevirisi, Windows'taki yeniden
deneme (her platformda, sahte os.replace ile), isteğe bağlı fsync, dosya izinleri (karar S12),
hazırlık ve çöp dizinleri.
"""
from __future__ import annotations

import errno
import os
import stat
import sys
from datetime import datetime, timezone
from typing import Iterator

import pytest

import sofascore_scraper.store as store_package
from sofascore_scraper.exceptions import StorageError
from sofascore_scraper.store import (
    CatalogCorrupt,
    FollowExists,
    FollowManaged,
    LayoutError,
    LeaseHeld,
    PayloadCorrupt,
    PayloadMissing,
    SchemaTooNew,
    StoreBusy,
    StoreError,
    UnknownEvent,
    codec,
    files,
    manifest,
)

WINDOWS = sys.platform == "win32"
SUBCLASSES = [
    LeaseHeld, StoreBusy, UnknownEvent, PayloadMissing, PayloadCorrupt, CatalogCorrupt, SchemaTooNew, LayoutError,
    FollowExists, FollowManaged,
]


def _leftovers(directory) -> list[str]:
    return sorted(n for n in os.listdir(directory) if n.endswith(".tmp"))


# --- hata sınıfları -------------------------------------------------------------------------------

def test_package_root_exports_every_error_class():
    """Tasarımdaki on alt sınıf (bölüm 2.3) ve taban, paket kökünden alınır; alt modülden değil."""
    assert len(SUBCLASSES) == len(set(SUBCLASSES)) == 10
    for cls in [StoreError, *SUBCLASSES]:
        assert cls.__name__ in store_package.__all__
        assert getattr(store_package, cls.__name__) is cls
        assert issubclass(cls, StoreError)


def test_store_error_is_a_storage_error_and_keeps_the_fatal_property():
    assert issubclass(StoreError, StorageError)

    for code in (errno.ENOSPC, errno.EACCES, errno.EPERM, errno.EROFS):
        assert StoreError("x", errno_code=code).fatal is True
    for code in (errno.EIO, errno.ENOENT, None):
        assert StoreError("x", errno_code=code).fatal is False

    error = StoreError("mesaj", path="/data/x", errno_code=errno.ENOSPC, detail="No space left on device")
    assert (str(error), error.message, error.path, error.errno, error.detail) == (
        "mesaj", "mesaj", "/data/x", errno.ENOSPC, "No space left on device",
    )


@pytest.mark.parametrize("cls", SUBCLASSES, ids=lambda c: c.__name__)
def test_every_subclass_is_a_non_fatal_store_error_with_a_default_message(cls):
    error = cls()

    assert isinstance(error, StoreError) and isinstance(error, StorageError)
    assert error.fatal is False
    assert str(error) == cls.default_message != StoreError.default_message
    assert str(cls("özel ileti", path="/p")) == "özel ileti"
    assert cls("özel ileti", path="/p").path == "/p"


@pytest.mark.parametrize("cls", [StoreError, *SUBCLASSES], ids=lambda c: c.__name__)
def test_from_exception_works_on_every_class_and_keeps_path_and_errno(cls):
    cause = OSError(errno.ENOSPC, "No space left on device", "/data/v3/events/1/event.json.gz")

    written = cls.from_exception(cause)
    read = cls.from_exception(OSError(errno.EIO, "Input/output error"), "/data/x.json.gz", reading=True)

    assert type(written) is cls and written.fatal is True
    assert written.path == "/data/v3/events/1/event.json.gz"
    assert "yazılamadı" in str(written) and "No space left on device" in str(written)
    assert type(read) is cls and read.fatal is False
    assert read.path == "/data/x.json.gz" and "okunamadı" in str(read)


def test_from_exception_accepts_a_serialisation_error():
    error = StoreError.from_exception(TypeError("Object of type set is not JSON serializable"), "/data/x")

    assert (error.errno, error.fatal, error.path) == (None, False, "/data/x")
    assert "set is not JSON serializable" in str(error)


def test_lease_held_carries_the_holder():
    error = LeaseHeld(name="writer", pid=4242, host="box", purpose="download", started_at=1790000000.0)

    assert (error.name, error.pid, error.host, error.purpose, error.started_at) == (
        "writer", 4242, "box", "download", 1790000000.0,
    )
    assert LeaseHeld().name == "" and LeaseHeld().pid is None


def test_schema_too_new_and_unknown_event_describe_themselves():
    too_new = SchemaTooNew(path="/data/.meta/schema.json", component="layout", found=4, supported=3)
    assert (too_new.component, too_new.found, too_new.supported) == ("layout", 4, 3)
    assert "layout: 4" in str(too_new) and "/data/.meta/schema.json" in str(too_new)

    unknown = UnknownEvent(event_id=16416346)
    assert unknown.event_id == 16416346 and "16416346" in str(unknown)


# --- atomik yazma ---------------------------------------------------------------------------------

def test_write_bytes_writes_content_and_creates_parent_directories(tmp_path):
    target = tmp_path / "v3" / "events" / "16" / "event.json.gz"

    files.write_bytes(target, b"\x1f\x8b payload")
    files.write_bytes(str(target), b"second")  # str ve PathLike ikisi de olur; var olan dosya değiştirilir

    assert target.read_bytes() == b"second"
    assert os.listdir(target.parent) == ["event.json.gz"]


def test_read_bytes_returns_the_file_and_maps_errors(tmp_path):
    target = tmp_path / "event.json.gz"
    target.write_bytes(b"abc")

    assert files.read_bytes(target) == b"abc"
    with pytest.raises(PayloadMissing) as missing:
        files.read_bytes(tmp_path / "absent.json.gz")
    assert missing.value.path == str(tmp_path / "absent.json.gz")
    with pytest.raises(StoreError) as not_a_file:  # dizin: POSIX'te EISDIR, Windows'ta EACCES
        files.read_bytes(tmp_path)
    assert not isinstance(not_a_file.value, PayloadMissing)
    assert "okunamadı" in str(not_a_file.value)


def test_failed_write_becomes_a_store_error_and_keeps_the_old_file(tmp_path, monkeypatch):
    target = tmp_path / "event.json.gz"
    target.write_bytes(b"old")

    def boom(src, dst):
        raise OSError(errno.ENOSPC, "No space left on device", dst)

    monkeypatch.setattr(files.os, "replace", boom)
    with pytest.raises(StoreError) as caught:
        files.write_bytes(target, b"new")
    monkeypatch.undo()

    assert caught.value.fatal is True and caught.value.errno == errno.ENOSPC
    assert caught.value.path == str(target)
    assert isinstance(caught.value.__cause__, OSError)
    assert target.read_bytes() == b"old"
    assert os.listdir(tmp_path) == ["event.json.gz"]


# --- Windows: yerine koymayı yeniden deneme (her platformda sahte os.replace ile) -------------------

class _BusyReplace:
    """İlk `failures` çağrıda PermissionError verir (hedef başka süreçte açık), sonra gerçekten yerine koyar."""

    def __init__(self, failures: int):
        self.failures = failures
        self.calls = 0
        self._real = os.replace

    def __call__(self, src, dst):
        self.calls += 1
        if self.calls <= self.failures:
            raise PermissionError(errno.EACCES, "Access is denied", dst)
        self._real(src, dst)


@pytest.fixture
def pauses(monkeypatch):
    """Yeniden denemeler arasındaki beklemeleri kaydeder; test gerçekten uyumaz."""
    recorded: list[float] = []
    monkeypatch.setattr(files.time, "sleep", recorded.append)
    return recorded


def test_replace_is_retried_on_windows_until_the_target_is_free(tmp_path, monkeypatch, pauses):
    target = tmp_path / "event.json.gz"
    target.write_bytes(b"old")
    busy = _BusyReplace(failures=3)
    monkeypatch.setattr(files, "_WINDOWS", True)
    monkeypatch.setattr(files.os, "replace", busy)

    files.write_bytes(target, b"new")

    assert busy.calls == 4
    assert pauses == [files.REPLACE_RETRY_PAUSE] * 3
    assert target.read_bytes() == b"new"
    assert _leftovers(tmp_path) == []


def test_exhausted_retries_raise_a_non_fatal_store_error(tmp_path, monkeypatch, pauses):
    """10 yeniden deneme, 20 ms aralık; sonra fatal olmayan StoreError: iş durmaz, yalnızca o yazma başarısızdır."""
    target = tmp_path / "event.json.gz"
    target.write_bytes(b"old")
    busy = _BusyReplace(failures=10**6)
    monkeypatch.setattr(files, "_WINDOWS", True)
    monkeypatch.setattr(files.os, "replace", busy)

    with pytest.raises(StoreError) as caught:
        files.write_bytes(target, b"new")

    assert (files.REPLACE_RETRIES, files.REPLACE_RETRY_PAUSE) == (10, 0.02)
    assert busy.calls == 11 and pauses == [0.02] * 10
    assert caught.value.fatal is False
    assert caught.value.path == str(target)
    assert isinstance(caught.value.__cause__, files.ReplaceBusy)
    assert target.read_bytes() == b"old"
    assert _leftovers(tmp_path) == []


def test_other_errors_are_not_retried_on_windows(tmp_path, monkeypatch, pauses):
    calls = []

    def boom(src, dst):
        calls.append(dst)
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(files, "_WINDOWS", True)
    monkeypatch.setattr(files.os, "replace", boom)

    with pytest.raises(StoreError) as caught:
        files.write_bytes(tmp_path / "event.json.gz", b"x")

    assert len(calls) == 1 and pauses == []
    assert caught.value.fatal is True


def test_permission_error_is_not_retried_outside_windows(tmp_path, monkeypatch, pauses):
    """POSIX'te PermissionError gerçek bir izin sorunudur: beklemeden çıkar ve fatal sayılır (bugünkü davranış)."""
    busy = _BusyReplace(failures=10**6)
    monkeypatch.setattr(files, "_WINDOWS", False)
    monkeypatch.setattr(files.os, "replace", busy)

    with pytest.raises(StoreError) as caught:
        files.write_bytes(tmp_path / "event.json.gz", b"x")

    assert busy.calls == 1 and pauses == []
    assert caught.value.fatal is True
    assert not isinstance(caught.value.__cause__, files.ReplaceBusy)


# --- fsync (STORE_DURABILITY) -----------------------------------------------------------------------

@pytest.fixture
def fsyncs(monkeypatch):
    recorded: list[int] = []
    monkeypatch.setattr(files.os, "fsync", recorded.append)
    return recorded


def test_nothing_is_fsynced_by_default(tmp_path, monkeypatch, fsyncs):
    monkeypatch.delenv(files.DURABILITY_ENV, raising=False)

    files.write_bytes(tmp_path / "a.json.gz", b"x")

    assert files.durability_full() is False
    assert fsyncs == []


@pytest.mark.parametrize("value", ["full", "FULL", " full "])
def test_store_durability_full_fsyncs_the_file_and_its_directory(tmp_path, monkeypatch, fsyncs, value):
    monkeypatch.setenv(files.DURABILITY_ENV, value)

    files.write_bytes(tmp_path / "a.json.gz", b"x")

    assert files.durability_full() is True
    assert len(fsyncs) == (1 if WINDOWS else 2)  # Windows'ta dizin fsync edilemez
    assert (tmp_path / "a.json.gz").read_bytes() == b"x"


def test_durable_argument_overrides_the_environment(tmp_path, monkeypatch, fsyncs):
    monkeypatch.setenv(files.DURABILITY_ENV, "full")
    files.write_bytes(tmp_path / "a.json.gz", b"x", durable=False)
    assert fsyncs == []

    monkeypatch.setenv(files.DURABILITY_ENV, "none")
    files.write_bytes(tmp_path / "b.json.gz", b"x", durable=True)
    assert len(fsyncs) == (1 if WINDOWS else 2)


# --- dosya izinleri (karar S12) ----------------------------------------------------------------------

posix_modes = pytest.mark.skipif(WINDOWS, reason="POSIX dosya izinleri Windows'ta yok")
UMASKS = [0o022, 0o077, 0o002, 0o027]


def _mode(path) -> int:
    """rwx bitleri; setgid'li bir üst dizinden miras kalabilen özel bitler sayılmaz."""
    return stat.S_IMODE(os.stat(path).st_mode) & 0o777


@pytest.fixture(params=UMASKS, ids=lambda m: f"umask-{m:03o}")
def umask(request) -> Iterator[int]:
    """Testi verilen umask ile çalıştırır, sonra eskisini geri koyar (umask süreç geneli bir ayardır)."""
    previous = os.umask(request.param)
    try:
        yield request.param
    finally:
        os.umask(previous)


@posix_modes
def test_write_bytes_gives_new_files_the_mode_of_the_umask(tmp_path, umask):
    """Docker bağlama noktasını başka kullanıcıyla okuyan ya da yedek alan hesap veriyi okuyabilmeli."""
    target = tmp_path / "event.json.gz"

    files.write_bytes(target, b"payload")

    assert _mode(target) == 0o666 & ~umask
    assert target.read_bytes() == b"payload"
    assert os.listdir(tmp_path) == ["event.json.gz"]


@posix_modes
def test_write_bytes_creates_parent_directories_with_the_mode_of_the_umask(tmp_path, umask):
    target = tmp_path / "v3" / "events" / "16" / "416" / "16416346" / "odds_all" / "1.json.gz"

    files.write_bytes(target, b"odds")

    assert _mode(target) == 0o666 & ~umask
    directory = target.parent
    while directory != tmp_path:
        assert _mode(directory) == 0o777 & ~umask, directory
        directory = directory.parent


@posix_modes
@pytest.mark.parametrize("old_mode", [0o600, 0o644, 0o666, 0o400])
def test_rewriting_a_file_gives_it_the_mode_of_the_umask_again(tmp_path, umask, old_mode):
    """Yerine konan dosya yeni dosyadır: eski dosyanın izni (ör. önceki sürümün yazdığı 0600) taşınmaz."""
    target = tmp_path / "manifest.json"
    target.write_bytes(b"old")
    os.chmod(target, old_mode)

    files.write_bytes(target, b"new")

    assert _mode(target) == 0o666 & ~umask
    assert target.read_bytes() == b"new"


@posix_modes
def test_payloads_and_manifests_follow_the_umask(tmp_path, umask):
    """Store'un bugünkü iki yazıcısı write_bytes'tan geçer: yük (codec) ve manifest."""
    moment = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
    payload_path = tmp_path / "v3" / "events" / "1" / "event.json.gz"
    manifest_path = tmp_path / "v3" / "events" / "1" / "manifest.json"

    codec.write_payload(payload_path, {"id": 1})
    manifest.write_manifest(manifest_path, manifest.Manifest(kind="event", id=1, created_at=moment, updated_at=moment))

    assert _mode(payload_path) == _mode(manifest_path) == 0o666 & ~umask
    assert codec.read_payload(payload_path) == {"id": 1}
    assert manifest.read_manifest(manifest_path).id == 1


@posix_modes
def test_durable_writes_follow_the_umask_too(tmp_path, umask):
    files.write_bytes(tmp_path / "a.json.gz", b"x", durable=True)

    assert _mode(tmp_path / "a.json.gz") == 0o666 & ~umask


@posix_modes
def test_a_published_directory_follows_the_umask_at_every_level(tmp_path, umask):
    """new_staging_dir + write_bytes + publish_dir: dizinler 0777, dosyalar 0666, ikisinden de umask düşer."""
    staged = files.new_staging_dir(tmp_path, "promote")
    files.write_bytes(os.path.join(staged, "event.json.gz"), b"event")
    files.write_bytes(os.path.join(staged, "manifest.json"), b"{}")
    files.write_bytes(os.path.join(staged, "odds_all", "1.json.gz"), b"odds")
    final = tmp_path / "v3" / "events" / "16" / "416" / "16416346"  # üst dizinleri publish_dir açar

    files.publish_dir(staged, final)

    seen_files, seen_dirs = [], []
    for root, dirnames, filenames in os.walk(tmp_path):
        seen_dirs += [os.path.join(root, name) for name in dirnames]
        seen_files += [os.path.join(root, name) for name in filenames]
    assert sorted(os.path.relpath(path, final) for path in seen_files) == [
        "event.json.gz", "manifest.json", os.path.join("odds_all", "1.json.gz"),
    ]
    assert len(seen_dirs) == 8  # .meta, .meta/tmp, v3, events, 16, 416, 16416346, odds_all
    assert {_mode(path) for path in seen_files} == {0o666 & ~umask}
    assert {_mode(path) for path in seen_dirs} == {0o777 & ~umask}


@posix_modes
def test_publish_dir_keeps_the_modes_the_directory_was_staged_with(tmp_path):
    """Yeniden adlandırma izinlere dokunmaz: Store dışı yoldan hazırlanan dizin kendi izniyle yayımlanır."""
    staged = files.new_staging_dir(tmp_path)
    os.close(os.open(os.path.join(staged, "private.bin"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    os.chmod(staged, 0o700)
    final = tmp_path / "v3" / "events" / "1"

    files.publish_dir(staged, final)

    assert (_mode(final), _mode(final / "private.bin")) == (0o700, 0o600)


@posix_modes
def test_trash_and_replace_keep_the_modes_of_what_they_move(tmp_path, umask):
    source, target = tmp_path / "new.bin", tmp_path / "current.bin"
    files.write_bytes(source, b"new")
    files.write_bytes(target, b"old")
    os.chmod(source, 0o640)

    files.replace(source, target)
    moved = files.move_to_trash(tmp_path, target)

    assert _mode(moved) == 0o640
    assert _mode(tmp_path / ".meta" / "trash") == 0o777 & ~umask


def test_write_bytes_never_touches_the_process_umask(tmp_path, monkeypatch):
    """
    os.umask değeri yalnızca değiştirerek okur; arada başka bir iş parçacığının açtığı dosya yanlış izin
    alırdı. İzin açılışta çekirdeğe bırakılır: umask ne okunur ne değiştirilir, chmod da yapılmaz.
    """
    def forbidden(*args, **kwargs):
        raise AssertionError("izin açılışta verilmeli")

    monkeypatch.setattr(os, "umask", forbidden)
    monkeypatch.setattr(os, "chmod", forbidden)
    if hasattr(os, "fchmod"):
        monkeypatch.setattr(os, "fchmod", forbidden)

    files.write_bytes(tmp_path / "v3" / "event.json.gz", b"x")
    staged = files.new_staging_dir(tmp_path)
    files.publish_dir(staged, tmp_path / "v3" / "events" / "1")
    monkeypatch.undo()

    assert (tmp_path / "v3" / "event.json.gz").read_bytes() == b"x"


def test_write_bytes_stores_the_bytes_untranslated(tmp_path):
    """Geçici dosya ikili kipte açılır (Windows'ta O_BINARY): satır sonları ve 0x1a aynen yazılır."""
    data = b"a\nb\r\nc\x1a\x00\xff"

    files.write_bytes(tmp_path / "a.json.gz", data)

    assert (tmp_path / "a.json.gz").read_bytes() == data


class _FixedIds:
    """uuid.uuid4 yerine: verilen onaltılık adları sırayla döndürür, sonuncusunu yineler."""

    def __init__(self, *names: str):
        self.names = list(names)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        name = self.names.pop(0) if len(self.names) > 1 else self.names[0]
        return type("Id", (), {"hex": name})()


def test_temporary_file_is_named_like_before_and_a_taken_name_is_skipped(tmp_path, monkeypatch):
    """`.<ad>.<rastgele>.tmp`; ad doluysa var olan dosyaya dokunulmaz, başka adla denenir."""
    taken = tmp_path / ".event.json.gz.aaaaaaaa.tmp"
    taken.write_bytes(b"someone else's")
    seen: list[str] = []
    real_replace = os.replace

    def recording_replace(src, dst):
        seen.append(os.path.basename(src))
        real_replace(src, dst)

    monkeypatch.setattr(files.uuid, "uuid4", _FixedIds("a" * 32, "b" * 32))
    monkeypatch.setattr(files.os, "replace", recording_replace)

    files.write_bytes(tmp_path / "event.json.gz", b"mine")
    monkeypatch.undo()

    assert seen == [".event.json.gz.bbbbbbbb.tmp"]
    assert taken.read_bytes() == b"someone else's"
    assert (tmp_path / "event.json.gz").read_bytes() == b"mine"
    assert sorted(os.listdir(tmp_path)) == [".event.json.gz.aaaaaaaa.tmp", "event.json.gz"]


def test_running_out_of_temporary_names_is_a_store_error(tmp_path, monkeypatch):
    taken = tmp_path / ".event.json.gz.aaaaaaaa.tmp"
    taken.write_bytes(b"someone else's")
    ids = _FixedIds("a" * 32)
    monkeypatch.setattr(files.uuid, "uuid4", ids)

    with pytest.raises(StoreError) as caught:
        files.write_bytes(tmp_path / "event.json.gz", b"mine")
    monkeypatch.undo()

    assert ids.calls == files.TMP_NAME_ATTEMPTS == 100
    assert caught.value.errno == errno.EEXIST and caught.value.fatal is False
    assert caught.value.path == str(tmp_path / "event.json.gz")
    assert taken.read_bytes() == b"someone else's"
    assert os.listdir(tmp_path) == [".event.json.gz.aaaaaaaa.tmp"]


def test_temporary_file_does_not_follow_a_symlink(tmp_path, monkeypatch):
    """Geçici adın yerinde bir bağ varsa hedefi ezilmez (O_EXCL): başka adla denenir."""
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"keep")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    try:
        (data_dir / ".event.json.gz.aaaaaaaa.tmp").symlink_to(outside)
    except OSError:
        pytest.skip("bu ortamda sembolik bağ oluşturulamıyor")
    monkeypatch.setattr(files.uuid, "uuid4", _FixedIds("a" * 32, "b" * 32))

    files.write_bytes(data_dir / "event.json.gz", b"mine")
    monkeypatch.undo()

    assert outside.read_bytes() == b"keep"
    assert (data_dir / "event.json.gz").read_bytes() == b"mine"


# --- yerine koyma ve silme --------------------------------------------------------------------------

def test_replace_moves_a_file_over_an_existing_one(tmp_path):
    source, target = tmp_path / "new.bin", tmp_path / "current.bin"
    source.write_bytes(b"new")
    target.write_bytes(b"old")

    files.replace(source, target)

    assert target.read_bytes() == b"new"
    assert os.listdir(tmp_path) == ["current.bin"]

    with pytest.raises(StoreError) as caught:
        files.replace(tmp_path / "absent.bin", target)
    assert caught.value.fatal is False and caught.value.errno == errno.ENOENT
    assert target.read_bytes() == b"new"


def test_remove_reports_whether_a_file_was_there(tmp_path):
    target = tmp_path / "x.json.gz"
    target.write_bytes(b"x")

    assert files.remove(target) is True
    assert files.remove(target) is False
    assert not target.exists()


def test_remove_tree_removes_directories_and_files(tmp_path):
    tree = tmp_path / "dir"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "a.json.gz").write_bytes(b"x")
    single = tmp_path / "single.txt"
    single.write_bytes(b"x")

    assert files.remove_tree(tree) is True
    assert files.remove_tree(single) is True
    assert files.remove_tree(tree) is False
    assert os.listdir(tmp_path) == []


def test_remove_tree_does_not_follow_a_symlink(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    link = tmp_path / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("bu ortamda sembolik bağ oluşturulamıyor")

    assert files.remove_tree(link) is True

    assert not link.exists() and (outside / "keep.txt").read_text(encoding="utf-8") == "keep"


# --- hazırlık (.meta/tmp) ve çöp (.meta/trash) ------------------------------------------------------

def test_staging_directories_are_new_and_live_under_meta_tmp(tmp_path):
    first = files.new_staging_dir(tmp_path, "promote")
    second = files.new_staging_dir(tmp_path, "promote")
    plain = files.new_staging_dir(str(tmp_path))

    tmp_root = str(tmp_path / ".meta" / "tmp")
    assert len({first, second, plain}) == 3
    for path in (first, second, plain):
        assert os.path.dirname(path) == tmp_root
        assert os.listdir(path) == []
    assert os.path.basename(first).startswith("promote.")


def test_publish_dir_moves_a_complete_directory_into_place(tmp_path):
    staged = files.new_staging_dir(tmp_path, "migrate")
    files.write_bytes(os.path.join(staged, "event.json.gz"), b"event")
    files.write_bytes(os.path.join(staged, "odds_all", "1.json.gz"), b"odds")
    final = tmp_path / "v3" / "events" / "16" / "416" / "16416346"  # üst dizinler henüz yok

    files.publish_dir(staged, final)

    assert sorted(os.listdir(final)) == ["event.json.gz", "odds_all"]
    assert (final / "odds_all" / "1.json.gz").read_bytes() == b"odds"
    assert not os.path.exists(staged)
    assert os.listdir(tmp_path / ".meta" / "tmp") == []


@pytest.mark.parametrize("existing", ["empty-dir", "full-dir", "file"])
def test_publish_dir_never_overwrites_an_existing_target(tmp_path, existing):
    staged = files.new_staging_dir(tmp_path)
    files.write_bytes(os.path.join(staged, "event.json.gz"), b"new")
    final = tmp_path / "v3" / "events" / "1"
    if existing == "file":
        final.parent.mkdir(parents=True)
        final.write_bytes(b"old")
    else:
        final.mkdir(parents=True)
        if existing == "full-dir":
            (final / "event.json.gz").write_bytes(b"old")

    with pytest.raises(StoreError) as caught:
        files.publish_dir(staged, final)

    assert caught.value.fatal is False and caught.value.errno == errno.EEXIST
    assert os.listdir(staged) == ["event.json.gz"]  # hazırlık dizini yerinde: çağıran karar verir
    if existing == "full-dir":
        assert (final / "event.json.gz").read_bytes() == b"old"


def test_publish_dir_fsyncs_the_parent_when_durable(tmp_path, fsyncs):
    staged = files.new_staging_dir(tmp_path)

    files.publish_dir(staged, tmp_path / "v3" / "events" / "1", durable=True)

    assert len(fsyncs) == (0 if WINDOWS else 1)


def test_move_to_trash_takes_the_directory_out_of_its_place_in_one_rename(tmp_path):
    legacy = tmp_path / "match_details" / "8_LaLiga" / "season_LaLiga_26_27" / "16416346"
    legacy.mkdir(parents=True)
    (legacy / "basic.json").write_text("{}", encoding="utf-8")

    moved = files.move_to_trash(tmp_path, legacy)

    assert not legacy.exists()
    assert os.path.dirname(moved) == str(tmp_path / ".meta" / "trash")
    assert os.path.basename(moved).startswith("16416346.")
    assert os.listdir(moved) == ["basic.json"]

    with pytest.raises(StoreError) as caught:  # artık orada değil
        files.move_to_trash(tmp_path, legacy)
    assert caught.value.errno == errno.ENOENT


def test_two_directories_with_the_same_name_do_not_collide_in_the_trash(tmp_path):
    first, second = tmp_path / "a" / "100", tmp_path / "b" / "100"
    first.mkdir(parents=True)
    second.mkdir(parents=True)

    assert files.move_to_trash(tmp_path, first) != files.move_to_trash(tmp_path, second)
    assert len(os.listdir(tmp_path / ".meta" / "trash")) == 2


def test_purge_empties_staging_and_trash_but_keeps_the_directories(tmp_path):
    assert files.purge_staging(tmp_path) == 0  # .meta henüz yok: hata değil
    assert files.purge_trash(tmp_path) == 0

    staged = files.new_staging_dir(tmp_path, "rebuild")
    files.write_bytes(os.path.join(staged, "a", "b.json.gz"), b"x")
    files.new_staging_dir(tmp_path)
    (tmp_path / ".meta" / "tmp" / "catalog.db.build").write_bytes(b"x")
    victim = tmp_path / "old"
    victim.mkdir()
    files.move_to_trash(tmp_path, victim)

    assert files.purge_staging(tmp_path) == 3
    assert files.purge_trash(tmp_path) == 1
    assert os.listdir(tmp_path / ".meta" / "tmp") == []
    assert os.listdir(tmp_path / ".meta" / "trash") == []
    assert files.purge_staging(tmp_path) == 0
