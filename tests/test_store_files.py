"""
src/store/errors.py ve src/store/files.py: Store hata sınıfları ve dosya ilkelleri
(docs/design/01-storage.md, bölüm 2.3 ve 4.4).

src/fsutil.py artık files.py'yi yeniden dışa açar; 2.x davranışının testleri tests/test_fsutil.py'de
değişmeden durur. Buradaki testler yeni katmanı dener: StoreError çevirisi, Windows'taki yeniden
deneme (her platformda, sahte os.replace ile), isteğe bağlı fsync, hazırlık ve çöp dizinleri.
"""
from __future__ import annotations

import errno
import os
import sys

import pytest

import src.store as store_package
from src import fsutil
from src.exceptions import StorageError
from src.store import (
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
    files,
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


def test_legacy_helpers_still_raise_the_plain_os_error(tmp_path, monkeypatch):
    """2.x çağıranları OSError yakalayıp kendileri StorageError'a çevirir (src/match_data_fetcher.py)."""
    def boom(src, dst):
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(files.os, "replace", boom)
    for write in (lambda: files.atomic_write_bytes(tmp_path / "a.bin", b"x"),
                  lambda: files.atomic_write_text(str(tmp_path / "a.txt"), "x"),
                  lambda: files.atomic_write_json(str(tmp_path / "a.json"), {"x": 1})):
        with pytest.raises(OSError) as caught:
            write()
        assert not isinstance(caught.value, StorageError)
    monkeypatch.undo()

    assert os.listdir(tmp_path) == []


def test_fsutil_re_exports_the_same_functions():
    assert fsutil.atomic_write_text is files.atomic_write_text
    assert fsutil.atomic_write_json is files.atomic_write_json
    assert fsutil.fcntl is files.fcntl


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


def test_exhausted_retries_reach_legacy_callers_as_permission_error(tmp_path, monkeypatch, pauses):
    target = tmp_path / "leagues.txt"
    busy = _BusyReplace(failures=10**6)
    monkeypatch.setattr(files, "_WINDOWS", True)
    monkeypatch.setattr(files.os, "replace", busy)

    with pytest.raises(PermissionError) as caught:
        fsutil.atomic_write_text(str(target), "x")

    assert busy.calls == 11
    assert caught.value.errno == errno.EACCES and caught.value.filename == str(target)
    assert os.listdir(tmp_path) == []


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
    files.atomic_write_bytes(tmp_path / "b.bin", b"x")

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


def test_legacy_helpers_never_fsync(tmp_path, monkeypatch, fsyncs):
    """2.x yazıcıları bugünkü gibi kalır: STORE_DURABILITY yalnızca Store'un kendi yazmalarını etkiler."""
    monkeypatch.setenv(files.DURABILITY_ENV, "full")

    fsutil.atomic_write_text(str(tmp_path / "leagues.txt"), "x")
    fsutil.atomic_write_json(str(tmp_path / "seasons.json"), {"x": 1})

    assert fsyncs == []


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


# --- config dosyası kilidi (2.x'ten taşındı) ---------------------------------------------------------

def test_file_lock_without_fcntl_locks_nothing(tmp_path, monkeypatch):
    """Windows dalı, her platformda: gövde çalışır, kilit dosyası açılmaz (tests/test_fsutil.py aynısını fsutil için dener)."""
    monkeypatch.setattr(files, "fcntl", None)
    target = tmp_path / "missing-dir" / "leagues.txt"

    with files.file_lock(str(target)):
        with files.file_lock(str(target)):
            pass

    assert not (tmp_path / "missing-dir").exists()


@pytest.mark.skipif(files.fcntl is None, reason="fcntl yok: kilit dosyası bu platformda hiç açılmıyor")
def test_file_lock_keeps_its_lock_file_next_to_the_target(tmp_path):
    target = tmp_path / "cfg" / "leagues.txt"

    with files.file_lock(str(target)):
        assert (tmp_path / "cfg" / "leagues.txt.lock").is_file()

    assert os.listdir(tmp_path / "cfg") == ["leagues.txt.lock"]
