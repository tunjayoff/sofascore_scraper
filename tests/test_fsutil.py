"""
src/fsutil.py: atomik yazma ve config dosyalarını koruyan süreçler arası kilit.

Config (leagues.txt, league_sports.json), sezon/maç JSON'ları ve watcher durumu bu iki
yardımcıdan geçer. Buradaki testler platformdan bağımsız yazılmıştır; Windows'ta bilinen
eksikler `xfail` ile işaretlidir (gerekçe işaretin içinde).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from src import fsutil
from src.fsutil import atomic_write_json, atomic_write_text, file_lock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WINDOWS = sys.platform == "win32"
needs_fcntl = pytest.mark.skipif(fsutil.fcntl is None, reason="fcntl yok: kilit dosyası bu platformda hiç açılmıyor")
no_lock_on_windows = pytest.mark.xfail(
    WINDOWS,
    strict=True,
    reason=(
        "Bilinen eksik: fcntl olmayan platformda (Windows) file_lock hiçbir şey kilitlemez "
        "(src/fsutil.py) — CLI ve web aynı anda leagues.txt / league_sports.json düzenlerse "
        "bir tarafın değişikliği kaybolabilir. src/throttle.py'deki kilit msvcrt ile Windows'u destekliyor."
    ),
)
replace_fails_on_windows = pytest.mark.xfail(
    WINDOWS,
    strict=False,  # yarış durumu: Windows'ta neredeyse her çalıştırmada görülür ama garanti değildir
    reason=(
        "Bilinen hata (Windows): hedef dosya başka bir iş parçacığı/süreç tarafından açıkken ya da aynı "
        "anda değiştirilirken os.replace PermissionError (WinError 5, 'Access is denied') verir. "
        "atomic_write_text yerine koymayı 20 ms arayla 10 kez yeniden dener (src/store/files.py, PR #37); "
        "denemeler tükenirse yazma kaybolur ve hata çağırana çıkar. CI windows-latest: aynı dosyaya yazan "
        "6 iş parçacığı artık geçiyor (XPASS); dosyayı sıkı döngüde açık tutan okuyucu varken tek yazıcı "
        "hâlâ başarısız, çünkü okuyucu yeniden denemelerden uzun sürüyor."
    ),
)


def _leftovers(directory) -> list[str]:
    """Hedef dizinde kalan geçici dosyalar (atomic_write_text '.<ad>.<rastgele>.tmp' kullanır)."""
    return sorted(n for n in os.listdir(directory) if n.endswith(".tmp"))


# --- atomic_write_text ------------------------------------------------------------------------

def test_atomic_write_text_writes_content_and_creates_parent_directories(tmp_path):
    target = tmp_path / "a" / "b" / "leagues.txt"

    atomic_write_text(str(target), "Süper Lig: 52\n")

    assert target.read_text(encoding="utf-8") == "Süper Lig: 52\n"
    assert _leftovers(target.parent) == []


def test_atomic_write_text_replaces_existing_file(tmp_path):
    target = tmp_path / "state.txt"
    target.write_text("old content that is longer than the new one", encoding="utf-8")

    atomic_write_text(str(target), "new")

    assert target.read_text(encoding="utf-8") == "new"
    assert os.listdir(tmp_path) == ["state.txt"]


def test_atomic_write_text_keeps_line_endings_byte_for_byte(tmp_path):
    """newline="": Windows'ta '\\n' → '\\r\\n' çevirisi yapılmaz, CSV'deki '\\r\\n' de ikiye katlanmaz."""
    target = tmp_path / "summary.csv"

    atomic_write_text(str(target), "a,b\r\n1,2\r\nplain\nend")

    assert target.read_bytes() == b"a,b\r\n1,2\r\nplain\nend"


def test_atomic_write_text_default_encoding_is_utf8_regardless_of_locale(tmp_path):
    target = tmp_path / "names.txt"

    atomic_write_text(str(target), "İstanbul Başakşehir – Göztepe")

    assert target.read_bytes() == "İstanbul Başakşehir – Göztepe".encode("utf-8")


def test_atomic_write_text_honours_explicit_encoding(tmp_path):
    target = tmp_path / "latin.txt"

    atomic_write_text(str(target), "Größe", encoding="latin-1")

    assert target.read_bytes() == "Größe".encode("latin-1")


def test_atomic_write_text_accepts_bare_filename_in_current_directory(tmp_path, monkeypatch):
    """Dizin bileşeni olmayan yol (örn. varsayılan '.env'): geçici dosya çalışma dizininde açılır."""
    monkeypatch.chdir(tmp_path)

    atomic_write_text("bare.txt", "x")

    assert (tmp_path / "bare.txt").read_text(encoding="utf-8") == "x"
    assert os.listdir(tmp_path) == ["bare.txt"]


def test_failed_replace_keeps_old_file_and_removes_temp_file(tmp_path, monkeypatch):
    target = tmp_path / "leagues.txt"
    target.write_text("Premier League: 17\n", encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(fsutil.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        atomic_write_text(str(target), "half written")
    monkeypatch.undo()

    assert target.read_text(encoding="utf-8") == "Premier League: 17\n"
    assert os.listdir(tmp_path) == ["leagues.txt"]


def test_failed_encoding_keeps_old_file_and_removes_temp_file(tmp_path):
    """Yazma yarıda kesilirse (burada: kodlanamayan karakter) hedef dosya eski haliyle kalır."""
    target = tmp_path / "leagues.txt"
    target.write_text("Premier League: 17\n", encoding="utf-8")

    with pytest.raises(UnicodeEncodeError):
        atomic_write_text(str(target), "ok so far ... İ", encoding="ascii")

    assert target.read_text(encoding="utf-8") == "Premier League: 17\n"
    assert os.listdir(tmp_path) == ["leagues.txt"]


def test_interrupt_during_write_removes_temp_file(tmp_path, monkeypatch):
    """Ctrl+C (KeyboardInterrupt, Exception değil) de geçici dosyayı geride bırakmaz."""
    target = tmp_path / "state.json"

    def interrupted(src, dst):
        raise KeyboardInterrupt

    monkeypatch.setattr(fsutil.os, "replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        atomic_write_text(str(target), "{}")
    monkeypatch.undo()

    assert os.listdir(tmp_path) == []


# --- atomic_write_json ------------------------------------------------------------------------

def test_atomic_write_json_defaults_are_readable_utf8_with_indent(tmp_path):
    target = tmp_path / "seasons.json"

    atomic_write_json(str(target), {"name": "Süper Lig", "ids": [1, 2]})

    raw = target.read_text(encoding="utf-8")
    assert json.loads(raw) == {"name": "Süper Lig", "ids": [1, 2]}
    assert "Süper Lig" in raw  # ensure_ascii=False: ü kaçışı yok
    assert raw.startswith('{\n  "name"')  # indent=2


def test_atomic_write_json_passes_dump_options_through(tmp_path):
    target = tmp_path / "compact.json"

    atomic_write_json(str(target), {"b": "ü", "a": 1}, indent=None, ensure_ascii=True, sort_keys=True)

    assert target.read_text(encoding="utf-8") == '{"a": 1, "b": "\\u00fc"}'


def test_atomic_write_json_with_unserialisable_data_touches_nothing(tmp_path):
    target = tmp_path / "state.json"
    target.write_text('{"ok": true}', encoding="utf-8")

    with pytest.raises(TypeError):
        atomic_write_json(str(target), {"bad": object()})

    assert target.read_text(encoding="utf-8") == '{"ok": true}'
    assert os.listdir(tmp_path) == ["state.json"]


# --- eşzamanlı yazma / okuma ---------------------------------------------------------------------

def _payload(writer: int, size: int = 4000) -> dict:
    return {"writer": writer, "rows": [writer] * size}


@replace_fails_on_windows
def test_concurrent_writers_leave_one_complete_file(tmp_path):
    """Aynı dosyaya aynı anda yazan iş parçacıkları: sonuç yazılanlardan biridir, karışık/yarım değil."""
    target = tmp_path / "state.json"
    errors: list[BaseException] = []
    start = threading.Barrier(6)

    def writer(n: int) -> None:
        try:
            start.wait(timeout=10)
            for _ in range(15):
                atomic_write_json(str(target), _payload(n))
        except BaseException as e:  # iş parçacığındaki hata testte görünsün
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert errors == []
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data == _payload(data["writer"])
    assert _leftovers(tmp_path) == []


@replace_fails_on_windows
def test_reader_never_sees_a_partial_file_while_it_is_rewritten(tmp_path):
    """
    Web sunucusu bir dosyayı okurken CLI/iş parçacığı yeniden yazabilir: okuyan taraf ya eski
    ya yeni içeriği görmeli, yazan taraf da okuyucu yüzünden hata almamalı.
    """
    target = tmp_path / "state.json"
    atomic_write_json(str(target), _payload(0))
    stop = threading.Event()
    reader_errors: list[BaseException] = []
    writer_errors: list[BaseException] = []
    reads = 0

    def reader() -> None:
        nonlocal reads
        while not stop.is_set():
            try:
                with open(target, encoding="utf-8") as f:
                    data = json.load(f)
                assert data == _payload(data["writer"])
                reads += 1
            except BaseException as e:
                reader_errors.append(e)
                return

    def writer() -> None:
        try:
            for n in range(1, 120):
                atomic_write_json(str(target), _payload(n))
        except BaseException as e:
            writer_errors.append(e)

    r = threading.Thread(target=reader)
    w = threading.Thread(target=writer)
    r.start()
    w.start()
    w.join(timeout=60)
    stop.set()
    r.join(timeout=10)

    assert writer_errors == []
    assert reader_errors == []
    assert reads > 0
    assert _leftovers(tmp_path) == []


# --- file_lock --------------------------------------------------------------------------------

_HOLD_LOCK = """
import sys
from src.fsutil import file_lock

with file_lock(sys.argv[1]):
    print("locked", flush=True)
    sys.stdin.readline()  # üst süreç bir satır yazana kadar kilidi tut
print("released", flush=True)
"""


def _acquire_in_thread(path: str) -> tuple[threading.Thread, threading.Event]:
    acquired = threading.Event()

    def take() -> None:
        with file_lock(path):
            acquired.set()

    t = threading.Thread(target=take, daemon=True)
    t.start()
    return t, acquired


@needs_fcntl
def test_file_lock_keeps_its_lock_file_next_to_the_target(tmp_path):
    target = tmp_path / "cfg" / "leagues.txt"

    with file_lock(str(target)):
        assert (tmp_path / "cfg" / "leagues.txt.lock").is_file()

    # Kilit hedefi oluşturmaz/değiştirmez; .lock dosyası sonraki kullanım için kalır
    assert not target.exists()
    assert os.listdir(tmp_path / "cfg") == ["leagues.txt.lock"]


@no_lock_on_windows
def test_file_lock_makes_a_second_process_wait(tmp_path):
    """CLI ve web ayrı süreçlerdir: biri kilidi tutarken diğeri içeri girememeli."""
    target = str(tmp_path / "leagues.txt")
    child = subprocess.Popen(
        [sys.executable, "-c", _HOLD_LOCK, target],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "locked"

        thread, acquired = _acquire_in_thread(target)
        assert not acquired.wait(0.5), "ikinci süreç, kilit başka süreçteyken kritik bölgeye girdi"

        child.stdin.write("\n")
        child.stdin.flush()
        assert acquired.wait(10), "kilit bırakıldıktan sonra alınamadı"
        thread.join(timeout=10)
        assert child.stdout.readline().strip() == "released"
    finally:
        if child.poll() is None:
            try:
                child.stdin.close()
            except OSError:
                pass
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        child.stdout.close()


@no_lock_on_windows
def test_file_lock_serialises_threads_of_one_process(tmp_path):
    """Web uygulamasında iş parçacığı + istek işleyicileri aynı dosyayı düzenler: aynı anda tek sahip."""
    target = str(tmp_path / "league_sports.json")
    start = threading.Barrier(4)
    guard = threading.Lock()
    inside = 0
    max_inside = 0
    errors: list[BaseException] = []

    def worker() -> None:
        nonlocal inside, max_inside
        try:
            start.wait(timeout=10)
            for _ in range(3):
                with file_lock(target):
                    with guard:
                        inside += 1
                        max_inside = max(max_inside, inside)
                    time.sleep(0.02)
                    with guard:
                        inside -= 1
        except BaseException as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert errors == []
    assert max_inside == 1


def test_file_lock_is_released_when_the_body_raises(tmp_path):
    target = str(tmp_path / "leagues.txt")

    with pytest.raises(ValueError, match="inside"):
        with file_lock(target):
            raise ValueError("inside")

    thread, acquired = _acquire_in_thread(target)
    assert acquired.wait(10), "istisnadan sonra kilit bırakılmadı"
    thread.join(timeout=10)


def test_file_lock_without_fcntl_runs_the_body_and_locks_nothing(tmp_path, monkeypatch):
    """
    fcntl'in olmadığı dal (Windows), her platformda: gövde çalışır, istisna aynen yayılır,
    ama kilit dosyası açılmaz ve ikinci bir sahip beklemeden içeri girer. Bu, mevcut davranışın
    kaydıdır — yukarıdaki `no_lock_on_windows` testleri eksik korumanın kendisini gösterir.
    """
    monkeypatch.setattr(fsutil, "fcntl", None)
    target = tmp_path / "missing-dir" / "leagues.txt"
    ran = []

    with file_lock(str(target)):
        ran.append("outer")
        with file_lock(str(target)):  # gerçek kilitte bu satır sonsuza dek beklerdi
            ran.append("inner")

    assert ran == ["outer", "inner"]
    assert not (tmp_path / "missing-dir").exists()

    with pytest.raises(ValueError, match="inside"):
        with file_lock(str(target)):
            raise ValueError("inside")
