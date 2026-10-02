"""
Yarıda kesilen yazmalar: `put`'un, yükseltmenin, `reset_empty_markers`'ın ve `delete`'in her adımında (plan
maddesi ST-20; docs/design/01-storage.md bölüm 4.4, 5.4 "Crash safety" ve 6.2).

Adımlar `EventStore._checkpoint`'in bildirdikleridir; bunlara atomik yazmanın ortası da eklenir (geçici dosya
yazıldı, yerine konmadı: `tmp:<dosya adı>`). İki tür kesinti sınanır:

  * **Süreç ölümü** (`kill`): yazma bir alt süreçte başlar ve süreç o adımda `os._exit` ile ölür: istisna yok,
    temizlik yok, tıpkı `kill -9` gibi. Alt süreç `writer` kilidini tutar; ölümünden sonra "temiz kapanmadı"
    işareti ve hazırlık alanındaki artıklar da kalır. Disk halleri birbirinden farklı olan adımlar için
    çalışır (`KILLED`); alt süreç başlatmak pahalıdır (Windows'ta saniyeler).
  * **Hata** (`error`): aynı adımda yazmanın içinden bir istisna çıkar (disk hatası, iptal). Bütün adımlar için
    çalışır. Süreç yaşamaya devam eder: katalog işlemi geri alınır, işaret (diske dokunulduysa) kalır.

İkisinden sonra da, deponun bir sonraki açılışının yaptığı uzlaştırmanın ardından şunlar beklenir:

  * yarım yazma işareti kalmaz, katalog ağacın sıfırdan kurulmuş haline eşittir, `verify(deep=True)` temizdir;
  * maçın mantıksal dökümü ya yazmadan önceki ya da sonraki haldir; dilimler tek tek yazıldığı için ara
    adımlarda her dilim ayrı ayrı "önce" ya da "sonra"dır, hiçbir zaman yarım değildir;
  * eski düzen ağacına dokunulmamıştır;
  * aynı yazma yeniden yapılınca sonuç, hiç kesilmemiş bir çalıştırmanınkiyle aynıdır;
  * hazırlık alanındaki artıklar `writer` kilidi alınınca silinir (karar S16).

Değişiklik satırı kaybolmaz (plan maddesi FX-12; bölüm 6.2): olay yükü değişirken satır, yük dosyası
değiştirilmeden önce niyet dosyasına yazılır. Olay yükü yazıldıktan sonra, satır günlüğe eklenmeden kesilen
yazmanın satırı açılıştaki uzlaştırmada günlüğe eklenir; satır eklenmiş ama niyet silinmemişse ikinci kez
eklenmez. Yük yazılmadan kesilen yazmanın niyeti atılır: yazma yinelenince satırı kendisi yazar.
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import pytest

import store_dump
import store_fixtures as sf
from src.slices import SLICE_EMPTY, SLICE_OK, Outcome
from src.store import Scope, Store, StoreError, open_store
from src.store import layout
from src.store.events import CHANGE_INTENT_DIR

ROOT = Path(__file__).resolve().parent.parent
UTC = dt.timezone.utc
WHEN = dt.datetime(2026, 10, 1, 12, 1, 0, tzinfo=UTC)
CRASHED = 77  # alt sürecin, istenen adımda öldüğünü söyleyen çıkış kodu
LEAGUE_DIR = "match_details/17_Premier_League/season_Premier_League_26_27"

ARS = sf.event_id(sf.PL_ARS)  # bütün dilimleri dolu, gözlemi var
BRE = sf.event_id(sf.PL_BRE)  # eski sürümün saydığı (doğrulanmamış) "yok" işaretleri
NEW = sf.event_id(sf.PL_NO_DETAIL)  # dizini olmayan maç

BEFORE, AFTER, MIXED = "before", "after", "mixed"
KILL, ERROR = "kill", "error"
# senaryo → (adım, yeniden açıldıktan sonra maçın hali). "mixed": bazı dilimler yeni, manifest onlardan toparlandı
SCENARIOS: Dict[str, List[Tuple[str, str]]] = {
    "update": [  # v3'te duran maça yazma: olay yükü değişir (günlük satırı), bir dilim değişir, bir dilim 404
        ("marker", BEFORE), ("locked", BEFORE), ("change_intent", BEFORE), ("tmp:event.json.gz", BEFORE),
        ("payload:event", MIXED),
        ("tmp:statistics.json.gz", MIXED), ("payload:statistics", MIXED), ("tmp:manifest.json", MIXED),
        ("manifest", AFTER), ("change_log", AFTER), ("indexed", AFTER), ("commit", AFTER), ("done", AFTER)],
    "create": [  # yeni maç: dizin hazırlık alanında kurulur, tek yeniden adlandırmayla görünür
        ("marker", BEFORE), ("locked", BEFORE), ("staged", BEFORE), ("published", AFTER), ("indexed", AFTER),
        ("commit", AFTER), ("done", AFTER)],
    "promote": [  # eski düzendeki maça yazma: önce yükseltme (mantıksal olarak hiçbir şey değişmez), sonra yazma
        ("marker", BEFORE), ("locked", BEFORE), ("change_intent", BEFORE), ("staged", BEFORE), ("published", BEFORE),
        ("payload:event", MIXED), ("payload:statistics", MIXED), ("tmp:manifest.json", MIXED), ("manifest", AFTER), ("change_log", AFTER),
        ("indexed", AFTER), ("commit", AFTER), ("done", AFTER)],
    "reset": [  # eski düzendeki maçın sayaçlarını sıfırlama: yükseltme, sonra manifest
        ("marker", BEFORE), ("locked", BEFORE), ("staged", BEFORE), ("published", BEFORE), ("manifest", AFTER),
        ("indexed", AFTER), ("commit", AFTER), ("done", AFTER)],
    "delete": [  # iki düzende de duran maçı silme
        ("marker", BEFORE), ("locked", BEFORE), ("indexed", AFTER), ("commit", AFTER), ("done", AFTER)],
}
# Süreç ölümüyle de sınanan adımlar: disk halleri birbirinden farklı olanlar. "marker" ile "locked" (yalnızca
# işaret; aşağıda kendi testi var), "indexed" ile "commit" (her şey yazıldı, işlem tamamlanmadı) aynı hali
# bırakır; "payload:statistics" ile "tmp:manifest.json" arasında diske yazılan bir şey yoktur; "done"da yazma
# bitmiştir.
KILLED: Dict[str, Tuple[str, ...]] = {
    "promote": ("staged", "published", "payload:event", "tmp:manifest.json", "manifest", "change_log", "commit"),
    "update": ("tmp:statistics.json.gz",),
    "create": ("staged",),
    "delete": ("indexed",),
}
EVENT_OF = {"update": ARS, "create": NEW, "promote": ARS, "reset": BRE, "delete": ARS}
CASES = [(mode, name, step, expected) for name, steps in SCENARIOS.items() for step, expected in steps
         for mode in (ERROR, KILL) if mode == ERROR or step in KILLED.get(name, ())]


class Interrupted(BaseException):
    """Yazmanın içinden çıkan, hiçbir `except Exception`'ın yutmadığı kesinti."""


# --- senaryolar (alt süreç ve test aynı işlevleri çalıştırır) -----------------------------------------------

def new_statistics(event_id: int) -> Dict[str, Any]:
    return {"statistics": [{"period": "ALL", "groups": [{"statisticsItems": [{"key": "crash", "value": event_id}]}]}]}


def on_change(old: Optional[Mapping[str, Any]], new: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if old is None:
        return None
    return {"ts_utc": WHEN.isoformat(), "event_id": new["id"], "sport": "football",
            "changed": {"winnerCode": [old.get("winnerCode"), new.get("winnerCode")]}}


def run_scenario(store: Store, name: str) -> None:
    """Senaryonun yazması. Sonucu, maçın o andaki haline bağlı değildir: yarıda kalmış bir denemeden sonra da aynıdır."""
    event_id = EVENT_OF[name]
    if name in ("update", "promote"):
        target = {**sf.basic_payload(sf.PL_ARS), "winnerCode": 3}
        store.events.put(event_id, {
            "event": Outcome(SLICE_OK, target, fetched_at=WHEN),
            "statistics": Outcome(SLICE_OK, new_statistics(event_id), fetched_at=WHEN),
            "lineups": Outcome(SLICE_EMPTY, None, reason="404", fetched_at=WHEN),
        }, count_empties=False, on_event_change=on_change)
    elif name == "create":
        basic = sf.basic_payload(sf.PL_NO_DETAIL)
        store.events.put(event_id, {"event": Outcome(SLICE_OK, basic, fetched_at=WHEN),
                                    "statistics": Outcome(SLICE_OK, new_statistics(event_id), fetched_at=WHEN)})
    elif name == "reset":
        store.events.reset_empty_markers(Scope(event_ids=[event_id]))
    elif name == "delete":
        store.events.delete(event_id)
    else:
        raise KeyError(name)


def _legacy_event(data_dir: Path, ev: sf.Ev, slices: Tuple[str, ...], **files: Any) -> None:
    """Küçük bir eski düzen maç dizini (L1): olay yükü, dilimler ve verilen öteki dosyalar."""
    basic = sf.basic_payload(ev)
    directory = data_dir.joinpath(*LEAGUE_DIR.split("/"), str(basic["id"]))
    directory.mkdir(parents=True)
    content = {"basic.json": basic, **{f"{key}.json": sf.slice_payload(key, basic) for key in slices}, **files}
    for name, payload in content.items():
        path = directory / name
        path.write_bytes(sf.dump_json(payload))
        os.utime(path, (sf.BASE_MTIME, sf.BASE_MTIME))


def prepare(name: str, data_dir: Path) -> None:
    """
    Senaryonun başlangıç hali; depo kapalı bırakılır. Ağaç bilerek küçüktür (iki maç): her test onu kurar,
    okur ve yeniden kurulmuş katalogla karşılaştırır.
    """
    data_dir.mkdir(parents=True)
    if name == "update":  # v3'te duran bir maç
        store = open_store(data_dir)
        basic = sf.basic_payload(sf.PL_ARS)
        store.events.put(ARS, {"event": Outcome(SLICE_OK, basic, fetched_at=WHEN - dt.timedelta(hours=1)),
                               "statistics": Outcome(SLICE_OK, sf.slice_payload("statistics", basic))})
    else:
        _legacy_event(data_dir, sf.PL_ARS, sf.REQUIRED_SLICES, **{
            "observation.json": sf.observation_payload(sf.basic_payload(sf.PL_ARS), "2026-09-19T06:00:00+00:00")})
        _legacy_event(data_dir, sf.PL_BRE, ("statistics", "h2h"), **{
            "_unavailable.json": {"lineups": 2, "incidents": 2, "pregame_form": 1}})
        (data_dir / "score_changes.jsonl").write_bytes(sf.dump_jsonl(sf.SCORE_CHANGES))
        store = open_store(data_dir)
        if name == "delete":  # maç iki düzende de dursun
            store.events.put(ARS, {"lineups": Outcome(SLICE_EMPTY, None, reason="404")})
    store.close()


# --- kesinti: süreç ölümü ve hata ----------------------------------------------------------------------

CHILD = """
import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
import test_store_crash
test_store_crash.child(*sys.argv[1:])
"""


def interrupt_at(store: Store, step: str, stop: Any) -> Any:
    """Yazmayı `step` adımında `stop()` ile keser; yaptığı değişikliği geri alan işlevi döndürür."""
    if step.startswith("tmp:"):
        target = step[len("tmp:"):]
        real = os.replace

        def replace(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
            if os.path.basename(os.fspath(dst)) == target and layout.V3_DIR in Path(os.fspath(dst)).parts:
                stop()  # geçici dosya yazıldı, yerine konmadı
            real(src, dst, *args, **kwargs)

        os.replace = replace  # type: ignore[assignment]

        def undo() -> None:
            os.replace = real

        return undo

    def checkpoint(reached: str) -> None:
        if reached == step:
            stop()

    store.events._checkpoint = checkpoint  # type: ignore[method-assign]

    def undo_checkpoint() -> None:
        del store.events._checkpoint

    return undo_checkpoint


def child(data_dir: str, name: str, step: str) -> None:
    """Alt süreç: yazar kilidini alır, senaryoyu çalıştırır ve `step` adımında ölür."""
    store = open_store(data_dir)
    lease = store.lease("writer", purpose="job")
    interrupt_at(store, step, lambda: os._exit(CRASHED))
    run_scenario(store, name)
    lease.release()
    os._exit(0)  # adıma hiç gelinmedi


def kill(data_dir: Path, name: str, step: str) -> None:
    out = subprocess.run([sys.executable, "-c", CHILD, str(data_dir), name, step], cwd=ROOT, capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == CRASHED, f"exit {out.returncode}\n{out.stdout}\n{out.stderr}"


def fail(data_dir: Path, name: str, step: str) -> None:
    """Aynı adımda, bu süreçte, yazmanın içinden çıkan bir kesinti."""
    store = open_store(data_dir)

    def stop() -> None:
        raise Interrupted(step)

    undo = interrupt_at(store, step, stop)
    try:
        with pytest.raises(Interrupted):
            run_scenario(store, name)
    finally:
        undo()
    store.close()


# --- yardımcılar --------------------------------------------------------------------------------------

def pending(store: Store) -> List[Tuple[str, int]]:
    return [(str(r[0]), int(r[1])) for r in store._catalog.connection().execute(
        "SELECT kind, entity_id FROM pending_writes ORDER BY kind, entity_id")]


def consistent(store: Store) -> None:
    assert pending(store) == []
    assert store.catalog.diff_from_rebuild() == []
    for deep in (False, True):
        report = store.catalog.verify(deep=deep)
        assert report.ok, [(i.invariant, i.kind, i.detail, i.path) for i in report.open_issues]
        assert report.leftovers == []


def staging(data_dir: Path) -> List[str]:
    try:
        return sorted(os.listdir(layout.resolve(data_dir, layout.TMP_DIR)))
    except FileNotFoundError:
        return []


def intents(data_dir: Path) -> List[str]:
    """Değişiklik satırlarının niyet dosyaları (bölüm 6.2)."""
    try:
        return sorted(os.listdir(layout.resolve(data_dir, CHANGE_INTENT_DIR)))
    except FileNotFoundError:
        return []


def legacy_tree(root: Path) -> Dict[str, Tuple[bytes, int]]:
    out: Dict[str, Tuple[bytes, int]] = {}
    for path in sorted((root / "match_details").rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = (path.read_bytes(), path.stat().st_mtime_ns)
    return out


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory: pytest.TempPathFactory) -> Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Senaryo → (önceki döküm, hiç kesilmeden tamamlanan yazmadan sonraki döküm)."""
    found: Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]] = {}
    for name in SCENARIOS:
        data_dir = tmp_path_factory.mktemp(f"reference-{name}") / "data"
        prepare(name, data_dir)
        before = store_dump.dump(data_dir)
        store = open_store(data_dir)
        run_scenario(store, name)
        store.close()
        found[name] = (before, store_dump.dump(data_dir))
        assert store_dump.diff(*found[name]) != []  # senaryo gerçekten bir şey değiştiriyor
    return found


# --- testler ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mode, name, step, expected", [
    pytest.param(*case, id="-".join(case[:3]).replace(":", "_")) for case in CASES])
def test_a_write_interrupted_at_any_step_leaves_a_directory_that_recovers(
        tmp_path: Path, outcomes: Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]], mode: str, name: str, step: str,
        expected: str) -> None:
    data_dir = tmp_path / "data"
    prepare(name, data_dir)
    before, after = outcomes[name]
    assert store_dump.dump(data_dir) == before
    legacy_before = legacy_tree(data_dir) if name != "delete" else {}
    event = str(EVENT_OF[name])

    (kill if mode == KILL else fail)(data_dir, name, step)

    left_staged = staging(data_dir)
    # ölen süreç hazırlık dizinini bırakır; hata yolunda yazıcı onu kendisi siler
    assert bool(left_staged) == (mode == KILL and step == "staged")
    assert all(entry.startswith("writer.") for entry in left_staged)
    store = open_store(data_dir)  # açılış: yarım yazma işareti (ve ölen süreçten sonra temiz kapanmamış yazar)
    consistent(store)
    assert intents(data_dir) == []  # niyet dosyası ya günlüğe eklendi ya atıldı
    assert legacy_tree(data_dir) == legacy_before or name == "delete"  # eski ağaca dokunulmadı
    found = store_dump.dump(data_dir)
    if expected == BEFORE:
        assert store_dump.diff(before, found) == []
    elif expected == AFTER:
        assert store_dump.diff(after["events"], found["events"]) == []
        assert found["changes"] == after["changes"]  # satır, yarıda kalan yazmanın niyetinden günlüğe eklendi
    else:
        # dilimler tek tek "önce" ya da "sonra"; olay yükü yeni, gözlem dosyanın zamanından toparlandı
        slices = found["events"][event]["slices"]
        old, new = before["events"][event]["slices"], after["events"][event]["slices"]
        assert set(old) <= set(slices) <= set(new)
        assert all(entry in (old.get(key), new[key]) for key, entry in slices.items())
        assert slices["event"] == new["event"] != old["event"]
        assert found["events"][event]["observation"]["change_ts"] == after["events"][event]["observation"]["change_ts"]
        assert found["changes"] == after["changes"]  # olay yükü yeni: satırı da günlükte
        assert {k: v for k, v in found["events"].items() if k != event} == {
            k: v for k, v in before["events"].items() if k != event}  # öteki maçlar yerinde
    if expected != BEFORE and name in ("update", "promote"):
        assert store.events.payload(int(event))["winnerCode"] == 3

    # hazırlık alanındaki artık, yazar kilidi alınınca silinir; aynı yazma yeniden yapılınca sonuç aynıdır
    with store.lease("writer", purpose="job") as lease:
        assert lease.unclean == (mode == KILL) and staging(data_dir) == []
        run_scenario(store, name)
    final = store_dump.dump(data_dir)
    if expected == MIXED:
        # Toparlanan gözlemin anı dosyanın zamanıdır; yeniden gelen aynı yük ondan eski tarihlidir ve yok sayılır
        assert final["events"][event]["slices"] == after["events"][event]["slices"]
        final["events"][event]["observation"] = after["events"][event]["observation"]
    assert store_dump.diff(after["events"], final["events"]) == []
    assert final["changes"] == after["changes"]  # satır bir kez: ne kayboldu ne yinelendi
    assert intents(data_dir) == []
    consistent(store)
    assert legacy_tree(data_dir) == legacy_before or name == "delete"


def test_every_checkpoint_of_the_protocol_is_covered(tmp_path: Path) -> None:
    """Senaryoların adım listeleri, yazmanın gerçekten bildirdiği adımlardır (yeni bir adım buraya da eklenir)."""
    for name, steps in SCENARIOS.items():
        prepare(name, tmp_path / name)
        store = open_store(tmp_path / name)
        seen: List[str] = []
        store.events._checkpoint = seen.append  # type: ignore[method-assign]
        run_scenario(store, name)
        assert seen == [step for step, _ in steps if not step.startswith("tmp:")], name
        assert set(KILLED.get(name, ())) <= {step for step, _ in steps}
        store.close()


def test_the_marker_of_an_event_whose_directory_was_never_built_is_cleared_on_open(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    prepare("create", data_dir)
    kill(data_dir, "create", "locked")
    raw = open_store(data_dir, sync_catalog=False)
    assert pending(raw) == [("event", NEW)]
    report = raw.catalog.reconcile(v3=True)
    assert (report.pending, report.events_indexed, report.events_removed) == (1, 0, 0)
    assert raw.events.get(NEW) is None and pending(raw) == []
    assert not os.path.exists(layout.resolve(data_dir, layout.event_dir(NEW)))


def _interrupted_update(data_dir: Path, step: str) -> Store:
    """`update` senaryosunun yazması bu süreçte `step` adımında kesilir; depo açık döner (uzlaştırma yok)."""
    prepare("update", data_dir)
    store = open_store(data_dir)

    def stop() -> None:
        raise Interrupted(step)

    undo = interrupt_at(store, step, stop)
    try:
        with pytest.raises(Interrupted):
            run_scenario(store, "update")
    finally:
        undo()
    return store


def _rerun(store: Store) -> Any:
    target = {**sf.basic_payload(sf.PL_ARS), "winnerCode": 3}
    return store.events.observe(ARS, target, observed_at=WHEN + dt.timedelta(minutes=1), on_event_change=on_change)


@pytest.mark.parametrize("step", ["payload:event", "manifest", "change_log"])
def test_the_next_write_of_the_event_adds_the_row_and_reports_it(
        tmp_path: Path, outcomes: Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]], step: str) -> None:
    """
    Açılış beklemeden: aynı maça bir sonraki yazma (yük aynı, kendi satırı yok) niyeti kapatır ve satırın sıra
    numarasını `change_seq` olarak döndürür; çağıran onu duyurabilir (canlı servisin `change.recorded` olayı).
    "change_log": satır dosyaya eklenmiş, dizin işlemi geri alınmış: ikinci kez eklenmez, numarası döner.
    """
    data_dir = tmp_path / "data"
    store = _interrupted_update(data_dir, step)
    assert intents(data_dir) == [f"{ARS}.json"]
    result = _rerun(store)
    after = outcomes["update"][1]
    assert result.change_seq == after["changes"][-1]["seq"] and not result.event_written
    assert store.changes.last_seq() == result.change_seq
    assert intents(data_dir) == []
    store.close()
    assert store_dump.dump(data_dir)["changes"] == after["changes"]
    consistent(open_store(data_dir))


def test_an_intent_whose_payload_never_landed_is_dropped_and_the_write_records_the_row_itself(
        tmp_path: Path, outcomes: Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]]) -> None:
    data_dir = tmp_path / "data"
    store = _interrupted_update(data_dir, "tmp:event.json.gz")
    assert intents(data_dir) == [f"{ARS}.json"]  # yük dosyasına dokunuldu (geçici dosya): niyet kaldı
    result = _rerun(store)
    after = outcomes["update"][1]
    assert result.event_written and result.change_seq == after["changes"][-1]["seq"]
    assert intents(data_dir) == []
    store.close()
    assert store_dump.dump(data_dir)["changes"] == after["changes"]


def test_an_error_after_the_intent_but_before_the_payload_drops_the_intent(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    store = _interrupted_update(data_dir, "change_intent")
    assert intents(data_dir) == [] and pending(store) == []  # diske dokunulmadı: ne niyet ne işaret
    store.close()


@pytest.mark.parametrize("content", [
    b"{not json",
    b"[]",
    b'{"event_id": %d, "sha256": "x", "row": {"event_id": %d}}' % (ARS, ARS),  # satırın ts_utc'si yok
    b'{"event_id": 1, "sha256": "x", "row": {}}',  # başka maçın niyeti
], ids=["not-json", "not-an-object", "row-without-ts", "other-event"])
def test_an_unreadable_intent_is_reported_and_removed(tmp_path: Path, caplog: pytest.LogCaptureFixture,
                                                      content: bytes) -> None:
    data_dir = tmp_path / "data"
    prepare("update", data_dir)
    before = store_dump.dump(data_dir)
    directory = Path(layout.resolve(data_dir, CHANGE_INTENT_DIR))
    directory.mkdir(parents=True)
    (directory / f"{ARS}.json").write_bytes(content)
    (directory / ".9.json.0a1b2c3d.tmp").write_bytes(b"{")  # ölen yazmanın yarım geçici dosyası
    (directory / "notes.txt").write_bytes(b"")  # adı kurala uymayan girdiye dokunulmaz
    with caplog.at_level("WARNING", logger="src.store.events"):
        store = open_store(data_dir)
        store.catalog.reconcile()
    assert any("change intent of an unfinished write cannot be read" in r.getMessage() for r in caplog.records)
    assert intents(data_dir) == ["notes.txt"]
    store.close()
    assert store_dump.dump(data_dir)["changes"] == before["changes"]


def test_a_row_that_is_not_json_fails_before_the_disk_is_touched(tmp_path: Path) -> None:
    """Satır JSON'a çevrilemezse StoreError, yük dosyası yazılmadan (önceden yük yazılıyor, satır kayboluyordu)."""
    data_dir = tmp_path / "data"
    prepare("update", data_dir)
    before = store_dump.dump(data_dir)
    store = open_store(data_dir)
    target = {**sf.basic_payload(sf.PL_ARS), "winnerCode": 3}
    with pytest.raises(StoreError, match="JSON"):
        store.events.observe(ARS, target, observed_at=WHEN, on_event_change=lambda old, new: {
            "ts_utc": WHEN.isoformat(), "event_id": ARS, "changed": {"winnerCode": [1, {3}]}})
    assert pending(store) == [] and intents(data_dir) == []
    store.close()
    assert store_dump.dump(data_dir) == before


def test_an_error_before_the_disk_is_touched_leaves_no_marker(tmp_path: Path) -> None:
    """Hata yolunda işaret yalnızca diske dokunulduysa kalır; dokunulmadıysa toparlanacak bir şey yoktur."""
    data_dir = tmp_path / "data"
    prepare("promote", data_dir)
    for step, left in (("locked", []), ("staged", []), ("published", [("event", ARS)]),
                       ("payload:event", [("event", ARS)])):
        fail(data_dir, "promote", step)
        raw = open_store(data_dir, sync_catalog=False)
        assert pending(raw) == left, step
        raw.close()
