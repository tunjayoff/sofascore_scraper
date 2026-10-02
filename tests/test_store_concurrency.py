"""
Eşzamanlı yazma ve okuma (plan maddesi ST-20; docs/design/01-storage.md bölüm 6.2 ve 6.3).

  * İki süreç aynı maçın farklı dilimlerini 500'er kez yazar: manifest kataloğun yazma kilidi altında okunup
    yazıldığı için hiçbiri ötekinin kaydını kaybetmez.
  * Okuyucular, yazar dilimleri yeniden yazarken hiçbir zaman geçersiz ya da yarım bir yük görmez: dosyalar
    atomik değiştirilir, Store okuyucuları dosyayı tek çağrıda okur ve kapatır.

Yazma kilidi `busy_timeout` içinde alınamazsa `put` StoreBusy fırlatır; Windows'ta yerine koyma, hedef başka
bir okuyucuda açıkken yeniden denemelere rağmen başarısız olabilir (kalıcı olmayan StoreError). İkisi de
çağıranın yeniden denemesi gereken geçici hatalardır; buradaki yazarlar öyle yapar ve sayısını bildirir.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

import store_fixtures as sf
from src.slices import SLICE_EMPTY, SLICE_OK, Outcome
from src.store import PayloadCorrupt, PayloadMissing, Store, StoreError, open_store
from src.store import layout, manifest

ROOT = Path(__file__).resolve().parent.parent
EVENT = sf.event_id(sf.PL_ARS)
ROUNDS = 500


# --- yükler: kendi sağlamasını taşır -------------------------------------------------------------------

def body(key: str, n: int) -> Dict[str, Any]:
    """Sürüm numarası ve içeriğinin özeti olan bir yük: yarım ya da karışmış bir okuma sağlamayı tutturamaz."""
    pad = (f"{key}-{n}-" * 400)[: 3000 + (n % 7) * 500]
    return {"key": key, "n": n, "pad": pad, "check": hashlib.sha256(f"{key}:{n}:{pad}".encode()).hexdigest()}


def valid(payload: Any, key: str) -> bool:
    return (isinstance(payload, dict) and payload.get("key") == key
            and payload.get("check") == hashlib.sha256(
                f"{key}:{payload.get('n')}:{payload.get('pad')}".encode()).hexdigest())


def put_with_retry(store: Store, outcomes: Dict[Any, Outcome], **kwargs: Any) -> int:
    """`put`; geçici hatada (StoreBusy, Windows'ta meşgul dosya) yeniden dener. Yeniden deneme sayısını döndürür."""
    retries = 0
    while True:
        try:
            store.events.put(EVENT, outcomes, **kwargs)
            return retries
        except StoreError as exc:
            if exc.fatal or retries >= 200:
                raise
            retries += 1
            time.sleep(0.01)


def pending(store: Store) -> List[Tuple[str, int]]:
    return [(str(r[0]), int(r[1])) for r in store._catalog.connection().execute(
        "SELECT kind, entity_id FROM pending_writes ORDER BY kind, entity_id")]


def consistent(store: Store) -> None:
    assert pending(store) == []
    assert store.catalog.diff_from_rebuild() == []
    for deep in (False, True):
        report = store.catalog.verify(deep=deep)
        assert report.ok, [(i.invariant, i.kind, i.detail, i.path) for i in report.open_issues]


# --- iki süreç, aynı maç, farklı dilimler --------------------------------------------------------------

WORKER = """
import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
import test_store_concurrency
test_store_concurrency.worker(*sys.argv[1:])
"""


def worker(data_dir: str, key: str, rounds: str) -> None:
    """Alt süreç: maçın `key` dilimini `rounds` kez yazar; her üçüncü turda bir "veri yok" yanıtı da kaydeder."""
    store = open_store(data_dir)
    sys.stdin.readline()  # iki süreç de hazır olunca birlikte başlar
    retries = 0
    for n in range(int(rounds)):
        outcomes: Dict[Any, Outcome] = {key: Outcome(SLICE_OK, body(key, n))}
        if n % 3 == 0:
            outcomes[(f"{key}_extra", "1")] = Outcome(SLICE_EMPTY, None, reason="404")
        retries += put_with_retry(store, outcomes)
    store.close()
    print(json.dumps({"key": key, "retries": retries}))


def test_two_processes_writing_different_slices_of_one_event_lose_nothing(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    store = open_store(data_dir)
    basic = sf.basic_payload(sf.PL_ARS)
    store.events.put(EVENT, {"event": Outcome(SLICE_OK, basic)})
    store.close()
    keys = ("statistics", "lineups")

    procs = [subprocess.Popen([sys.executable, "-c", WORKER, str(data_dir), key, str(ROUNDS)], cwd=ROOT,
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for key in keys]
    try:
        for proc in procs:
            assert proc.stdin is not None
            proc.stdin.write("go\n")
            proc.stdin.flush()
        results = [proc.communicate(timeout=300) for proc in procs]
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
    for proc, (out, err) in zip(procs, results, strict=True):
        assert proc.returncode == 0, err
        assert json.loads(out.strip().splitlines()[-1])["key"] in keys

    store = open_store(data_dir)
    found = manifest.read_manifest(layout.resolve(data_dir, layout.manifest_path(layout.event_dir(EVENT))))
    assert set(found.slices) == {"event", "statistics", "lineups", "statistics_extra/1", "lineups_extra/1"}
    for key in keys:
        payload = store.events.payload(EVENT, key)
        assert valid(payload, key) and payload["n"] == ROUNDS - 1  # her sürecin son yazdığı yerinde
        info = store.events.slice(EVENT, key)
        assert (info.state, info.has_payload) == ("ok", True)
        # sayaç, sürecin yazdığı her "veri yok" yanıtını saydı: kayıp güncelleme yok
        assert store.events.slice(EVENT, f"{key}_extra", "1").empty_count == len(range(0, ROUNDS, 3))
    assert store.events.payload(EVENT) == basic
    consistent(store)
    assert not os.path.exists(layout.resolve(data_dir, layout.TMP_DIR)) or os.listdir(
        layout.resolve(data_dir, layout.TMP_DIR)) == []


# --- okuyucular ve yazar ------------------------------------------------------------------------------

def test_readers_never_see_an_invalid_payload_while_slices_are_rewritten(tmp_path: Path) -> None:
    store = open_store(tmp_path / "data")
    basic = sf.basic_payload(sf.PL_ARS)
    store.events.put(EVENT, {"event": Outcome(SLICE_OK, basic), "statistics": Outcome(SLICE_OK, body("statistics", 0)),
                             ("odds_all", "1"): Outcome(SLICE_OK, body("odds_all", 0))})
    stop = threading.Event()
    problems: List[str] = []
    reads = [0, 0]
    retries = [0]
    busy = [0]

    def reader(index: int) -> None:
        try:
            while not stop.is_set():
                try:
                    parsed = store.events.payload(EVENT, "statistics")
                    raw = store.events.payload(EVENT, "odds_all", "1", raw=True)
                    everything = store.events.payloads(EVENT)
                except (PayloadCorrupt, PayloadMissing):
                    raise
                except StoreError:
                    if os.name != "nt":
                        raise
                    busy[0] += 1  # Windows: dosya tam yerine konurken açılamadı; geçici, içerik hatası değil
                    continue
                if not valid(parsed, "statistics"):
                    problems.append(f"statistics: {str(parsed)[:80]}")
                if not valid(json.loads(raw), "odds_all"):
                    problems.append(f"odds_all raw: {raw[:80]!r}")
                if not (valid(everything.get("statistics"), "statistics") and valid(everything.get("odds_all/1"), "odds_all")
                        and everything.get("event", {}).get("id") == EVENT):
                    problems.append(f"payloads: {sorted(everything)}")
                reads[index] += 1
        except Exception as exc:  # okuyucu hiçbir hata görmemeli (PayloadCorrupt, PayloadMissing dahil)
            problems.append(f"{type(exc).__name__}: {exc}")

    def writer() -> None:
        try:
            for n in range(1, 151):
                retries[0] += put_with_retry(store, {
                    "statistics": Outcome(SLICE_OK, body("statistics", n)),
                    ("odds_all", "1"): Outcome(SLICE_OK, body("odds_all", n)),
                    "event": Outcome(SLICE_OK, {**basic, "winnerCode": 1 + n % 3})})
        except Exception as exc:
            problems.append(f"writer: {type(exc).__name__}: {exc}")
        finally:
            stop.set()

    threads = [threading.Thread(target=reader, args=(0,)), threading.Thread(target=reader, args=(1,)),
               threading.Thread(target=writer)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=300)

    assert problems == [] and all(not thread.is_alive() for thread in threads)
    assert min(reads) > 0
    assert store.events.payload(EVENT, "statistics")["n"] == 150
    consistent(store)


def test_a_reader_keeps_reading_an_event_while_it_is_promoted(tmp_path: Path) -> None:
    """Bölüm 6.3: yükseltme sırasında okuyucu ya eski dizinden ya v3'ten okur; ikisi de aynı yüktür."""
    fixture = sf.build_fixture("canonical", tmp_path / "data")
    store = open_store(fixture.data_dir)
    ids = [int(r[0]) for r in store._catalog.connection().execute(
        "SELECT id FROM events WHERE layout = 'legacy' ORDER BY id")]
    expected = {event_id: store.events.payloads(event_id) for event_id in ids}
    stop = threading.Event()
    problems: List[str] = []
    reads = [0]

    def reader() -> None:
        try:
            while not stop.is_set():
                for event_id in ids:
                    found = store.events.payloads(event_id)
                    # yazarın eklediği dilim görünebilir; var olan dilimler hep aynı yükü verir
                    if {k: v for k, v in found.items() if k in expected[event_id]} != expected[event_id]:
                        problems.append(f"{event_id}: {sorted(found)}")
                    if store.events.payload(event_id) != expected[event_id]["event"]:
                        problems.append(f"{event_id}: event payload")
                reads[0] += 1
        except Exception as exc:
            problems.append(f"{type(exc).__name__}: {exc}")

    thread = threading.Thread(target=reader)
    thread.start()
    try:
        for event_id in ids:
            store.events.put(event_id, {("odds_all", "1"): Outcome(SLICE_OK, {"markets": [event_id]})})
        deadline = time.monotonic() + 30
        while reads[0] < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        stop.set()
        thread.join(timeout=60)

    assert problems == [] and reads[0] >= 2
    assert {store.events.get(event_id).layout for event_id in ids} == {"v3"}
    consistent(store)


def test_threads_writing_the_same_event_serialise_on_the_catalog_lock(tmp_path: Path) -> None:
    store = open_store(tmp_path / "data")
    store.events.put(EVENT, {"event": Outcome(SLICE_OK, sf.basic_payload(sf.PL_ARS))})
    keys = ("statistics", "lineups", "incidents", "h2h")
    errors: List[str] = []

    def write(key: str) -> None:
        try:
            for n in range(40):
                put_with_retry(store, {key: Outcome(SLICE_OK, body(key, n)),
                                       "pregame_form": Outcome(SLICE_EMPTY, None, reason="404")})
        except Exception as exc:
            errors.append(f"{key}: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=write, args=(key,)) for key in keys]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=300)

    assert errors == []
    assert {key: store.events.payload(EVENT, key)["n"] for key in keys} == dict.fromkeys(keys, 39)
    assert store.events.slice(EVENT, "pregame_form").empty_count == 40 * len(keys)  # hiçbir sayım kaybolmadı
    consistent(store)


@pytest.mark.skipif(os.name == "nt", reason="POSIX dosya izinleri")
def test_files_written_by_put_follow_the_umask(tmp_path: Path) -> None:
    """Karar S12: yük dosyaları, manifest, yükseltilen dizin ve günlük parçası sürecin umask'ine uyar."""
    fixture = sf.build_fixture("canonical", tmp_path / "data")
    store = open_store(fixture.data_dir)
    previous = os.umask(0o027)
    try:
        store.events.put(EVENT, {"statistics": Outcome(SLICE_OK, body("statistics", 1))})  # yükseltme + yazma
        other = EVENT + 1
        basic = {**sf.basic_payload(sf.PL_ARS), "id": other}
        store.events.put(other, {"event": Outcome(SLICE_OK, basic), ("odds_all", "1"): Outcome(SLICE_OK, {"m": 1})})
        store.events.observe(other, {**basic, "winnerCode": 3}, on_event_change=lambda old, new: {
            "ts_utc": "2026-10-01T12:00:00+00:00", "event_id": other})
    finally:
        os.umask(previous)

    modes: Dict[str, int] = {}
    for root in (layout.resolve(fixture.data_dir, layout.V3_DIR), layout.resolve(fixture.data_dir, layout.CHANGES_DIR)):
        for base, dirs, names in os.walk(root):
            for name in [*dirs, *names]:
                path = os.path.join(base, name)
                modes[os.path.relpath(path, fixture.data_dir)] = os.stat(path).st_mode & 0o777
    assert len(modes) > 15
    assert {mode for path, mode in modes.items() if os.path.isdir(os.path.join(fixture.data_dir, path))} == {0o750}
    assert {mode for path, mode in modes.items() if os.path.isfile(os.path.join(fixture.data_dir, path))} == {0o640}
