"""
Süreç ölümü: `put`'un, yükseltmenin, `reset_empty_markers`'ın ve `delete`'in her adımında (plan maddesi ST-20;
docs/design/01-storage.md bölüm 4.4, 5.4 "Crash safety" ve 6.2).

Her test bir alt süreçte bir yazma başlatır ve süreci protokolün belirli bir adımında `os._exit` ile öldürür:
istisna yok, temizlik yok, tıpkı `kill -9` gibi. Adımlar `EventStore._checkpoint`'in bildirdikleridir; iki
adım da atomik yazmanın ortasıdır (geçici dosya yazıldı, yerine konmadı: `tmp:<dosya adı>`). Alt süreç
`writer` kilidini tutar, yani ölümünden sonra "temiz kapanmadı" işareti de kalır.

Sonra veri dizini yeniden açılır ve şunlar beklenir:

  * yarım yazma işareti kalmaz, katalog ağacın sıfırdan kurulmuş haline eşittir, `verify(deep=True)` temizdir;
  * maçın mantıksal dökümü ya yazmadan önceki ya da sonraki haldir; dilimler tek tek yazıldığı için ara
    adımlarda her dilim ayrı ayrı "önce" ya da "sonra"dır, hiçbir zaman yarım değildir;
  * aynı yazma yeniden yapılınca sonuç, hiç ölmemiş bir çalıştırmanınkiyle aynıdır;
  * hazırlık alanındaki artıklar `writer` kilidi alınınca silinir (karar S16).

Bilinen sınır (bölüm 6.2'nin adım sırası): olay yükü yazıldıktan sonra, değişiklik günlüğü satırı eklenmeden
ölen süreçte değişiklik satırı kaybolur; yük yenidir ama günlükte izi yoktur. Testler bunu sabitler.
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
from src.store import Scope, Store, open_store
from src.store import layout

ROOT = Path(__file__).resolve().parent.parent
UTC = dt.timezone.utc
WHEN = dt.datetime(2026, 10, 1, 12, 1, 0, tzinfo=UTC)
CRASHED = 77  # alt sürecin, istenen adımda öldüğünü söyleyen çıkış kodu

ARS = sf.event_id(sf.PL_ARS)  # canonical: bütün dilimleri dolu, gözlemi var
BRE = sf.event_id(sf.PL_BRE)  # canonical: eski sürümün saydığı (doğrulanmamış) "yok" işaretleri
NEW = sf.event_id(sf.PL_NO_DETAIL)  # canonical: yalnızca listede

BEFORE, AFTER, MIXED = "before", "after", "mixed"
# senaryo → (adım, yeniden açıldıktan sonra maçın hali). "mixed": bazı dilimler yeni, manifest onlardan toparlandı
SCENARIOS: Dict[str, List[Tuple[str, str]]] = {
    "update": [  # v3'te duran maça yazma: olay yükü değişir (günlük satırı), bir dilim değişir, bir dilim 404
        ("marker", BEFORE), ("locked", BEFORE), ("tmp:event.json.gz", BEFORE), ("payload:event", MIXED),
        ("tmp:statistics.json.gz", MIXED), ("payload:statistics", MIXED), ("tmp:manifest.json", MIXED),
        ("manifest", AFTER), ("change_log", AFTER), ("indexed", AFTER), ("commit", AFTER), ("done", AFTER)],
    "create": [  # yeni maç: dizin hazırlık alanında kurulur, tek yeniden adlandırmayla görünür
        ("marker", BEFORE), ("locked", BEFORE), ("staged", BEFORE), ("published", AFTER), ("indexed", AFTER),
        ("commit", AFTER), ("done", AFTER)],
    "promote": [  # eski düzendeki maça yazma: önce yükseltme (mantıksal olarak hiçbir şey değişmez), sonra yazma
        ("marker", BEFORE), ("locked", BEFORE), ("staged", BEFORE), ("published", BEFORE), ("payload:event", MIXED),
        ("payload:statistics", MIXED), ("tmp:manifest.json", MIXED), ("manifest", AFTER), ("change_log", AFTER),
        ("indexed", AFTER), ("commit", AFTER), ("done", AFTER)],
    "reset": [  # eski düzendeki maçın sayaçlarını sıfırlama: yükseltme, sonra manifest
        ("marker", BEFORE), ("locked", BEFORE), ("staged", BEFORE), ("published", BEFORE), ("manifest", AFTER),
        ("indexed", AFTER), ("commit", AFTER), ("done", AFTER)],
    "delete": [  # iki düzende de duran maçı silme
        ("marker", BEFORE), ("locked", BEFORE), ("indexed", AFTER), ("commit", AFTER), ("done", AFTER)],
}
EVENT_OF = {"update": ARS, "create": NEW, "promote": ARS, "reset": BRE, "delete": ARS}
# olay yükü yazıldıktan sonra, günlük satırı eklenmeden ölen süreç: satır kaybolur
CHANGE_LOST = {"payload:event", "tmp:statistics.json.gz", "payload:statistics", "tmp:manifest.json", "manifest"}


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


def prepare(name: str, data_dir: Path) -> sf.LegacyFixture:
    """Senaryonun başlangıç hali; depo kapalı bırakılır."""
    if name == "update":  # boş dizinde, v3'te duran bir maç
        fixture = sf.build_fixture("empty", data_dir)
        store = open_store(data_dir)
        basic = sf.basic_payload(sf.PL_ARS)
        store.events.put(ARS, {"event": Outcome(SLICE_OK, basic, fetched_at=WHEN - dt.timedelta(hours=1)),
                               "statistics": Outcome(SLICE_OK, sf.slice_payload("statistics", basic))})
    else:
        fixture = sf.build_fixture("canonical", data_dir)
        store = open_store(data_dir)
        if name == "delete":  # maç iki düzende de dursun
            store.events.put(ARS, {"lineups": Outcome(SLICE_EMPTY, None, reason="404")})
    store.close()
    return fixture


CHILD = """
import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
import test_store_crash
test_store_crash.child(*sys.argv[1:])
"""


def child(data_dir: str, name: str, step: str) -> None:
    """Alt süreç: yazar kilidini alır, senaryoyu çalıştırır ve `step` adımında ölür."""
    store = open_store(data_dir)
    lease = store.lease("writer", purpose="job")
    if step.startswith("tmp:"):
        target = step[len("tmp:"):]
        real = os.replace

        def replace(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
            if os.path.basename(os.fspath(dst)) == target and layout.V3_DIR in Path(os.fspath(dst)).parts:
                os._exit(CRASHED)  # geçici dosya yazıldı, yerine konmadı
            real(src, dst, *args, **kwargs)

        os.replace = replace  # type: ignore[assignment]
    else:
        def checkpoint(reached: str) -> None:
            if reached == step:
                os._exit(CRASHED)

        store.events._checkpoint = checkpoint  # type: ignore[method-assign]
    run_scenario(store, name)
    lease.release()
    os._exit(0)  # adıma hiç gelinmedi


def crash(data_dir: Path, name: str, step: str) -> None:
    out = subprocess.run([sys.executable, "-c", CHILD, str(data_dir), name, step], cwd=ROOT, capture_output=True,
                         text=True, timeout=120)
    assert out.returncode == CRASHED, f"exit {out.returncode}\\n{out.stdout}\\n{out.stderr}"


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


def legacy_tree(root: Path) -> Dict[str, Tuple[bytes, int]]:
    out: Dict[str, Tuple[bytes, int]] = {}
    for top in ("match_details", "matches", "seasons"):
        for path in sorted((root / top).rglob("*")):
            if path.is_file():
                out[path.relative_to(root).as_posix()] = (path.read_bytes(), path.stat().st_mtime_ns)
    return out


def logical(fixture: sf.LegacyFixture) -> Dict[str, Any]:
    return store_dump.dump(fixture.data_dir, fixture.leagues)


@pytest.fixture(scope="module")
def outcomes(tmp_path_factory: pytest.TempPathFactory) -> Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Senaryo → (önceki döküm, hiç ölmeden tamamlanan yazmadan sonraki döküm)."""
    found: Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]] = {}
    for name in SCENARIOS:
        fixture = prepare(name, tmp_path_factory.mktemp(f"reference-{name}") / "data")
        before = logical(fixture)
        store = open_store(fixture.data_dir)
        run_scenario(store, name)
        store.close()
        found[name] = (before, logical(fixture))
        assert store_dump.diff(*found[name]) != []  # senaryo gerçekten bir şey değiştiriyor
    return found


# --- testler ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name, step, expected", [
    pytest.param(name, step, expected, id=f"{name}-{step.replace(':', '_')}")
    for name, steps in SCENARIOS.items() for step, expected in steps])
def test_a_process_killed_at_any_step_leaves_a_directory_that_recovers(
        tmp_path: Path, outcomes: Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]], name: str, step: str,
        expected: str) -> None:
    fixture = prepare(name, tmp_path / "data")
    data_dir = fixture.data_dir
    before, after = outcomes[name]
    assert logical(fixture) == before
    legacy_before = legacy_tree(data_dir) if name != "delete" else {}
    event = str(EVENT_OF[name])

    crash(data_dir, name, step)

    left_staged = staging(data_dir)
    assert bool(left_staged) == (step == "staged") and all(entry.startswith("writer.") for entry in left_staged)
    store = open_store(data_dir)  # açılış: temiz kapanmamış yazar, yarım yazma işareti
    consistent(store)
    assert legacy_tree(data_dir) == legacy_before or name == "delete"  # eski ağaca dokunulmadı
    found = logical(fixture)
    if expected == BEFORE:
        assert store_dump.diff(before, found) == []
    elif expected == AFTER:
        assert store_dump.diff(after["events"], found["events"]) == []
        assert found["changes"] == (before if step in CHANGE_LOST else after)["changes"]
    else:
        # dilimler tek tek "önce" ya da "sonra"; olay yükü yeni, gözlem dosyanın zamanından toparlandı
        slices = found["events"][event]["slices"]
        old, new = before["events"][event]["slices"], after["events"][event]["slices"]
        assert set(old) <= set(slices) <= set(new)
        assert all(entry in (old.get(key), new[key]) for key, entry in slices.items())
        assert slices["event"] == new["event"] != old["event"]
        assert found["events"][event]["observation"]["change_ts"] == after["events"][event]["observation"]["change_ts"]
        assert found["changes"] == before["changes"]
        assert {k: v for k, v in found["events"].items() if k != event} == {
            k: v for k, v in before["events"].items() if k != event}  # öteki maçlar yerinde
    if expected != BEFORE and name in ("update", "promote"):
        assert store.events.payload(int(event))["winnerCode"] == 3

    # hazırlık alanındaki artık, yazar kilidi alınınca silinir; aynı yazma yeniden yapılınca sonuç aynıdır
    with store.lease("writer", purpose="job") as lease:
        assert lease.unclean and staging(data_dir) == []
        run_scenario(store, name)
    final = logical(fixture)
    if expected == MIXED:
        # Toparlanan gözlemin anı dosyanın zamanıdır; yeniden gelen aynı yük ondan eski tarihlidir ve yok sayılır
        assert final["events"][event]["slices"] == after["events"][event]["slices"]
        final["events"][event]["observation"] = after["events"][event]["observation"]
    assert store_dump.diff(after["events"], final["events"]) == []
    lost = expected != BEFORE and step in CHANGE_LOST
    assert final["changes"] == (before if lost else after)["changes"]
    consistent(store)
    assert legacy_tree(data_dir) == legacy_before or name == "delete"


def test_every_checkpoint_of_the_protocol_is_covered(tmp_path: Path) -> None:
    """Senaryoların adım listeleri, yazmanın gerçekten bildirdiği adımlardır (yeni bir adım buraya da eklenir)."""
    for name, steps in SCENARIOS.items():
        fixture = prepare(name, tmp_path / name)
        store = open_store(fixture.data_dir)
        seen: List[str] = []
        store.events._checkpoint = seen.append  # type: ignore[method-assign]
        run_scenario(store, name)
        assert seen == [step for step, _ in steps if not step.startswith("tmp:")], name
        store.close()


def test_a_crash_between_marker_and_lock_of_an_unknown_event_leaves_nothing(tmp_path: Path) -> None:
    """Dizini hiç kurulmamış maçın işareti: açılış onu siler, katalogda maç görünmez."""
    fixture = prepare("create", tmp_path / "data")
    crash(fixture.data_dir, "create", "locked")
    raw = open_store(fixture.data_dir, sync_catalog=False)
    assert pending(raw) == [("event", NEW)]
    report = raw.catalog.reconcile(v3=True)
    assert (report.pending, report.events_indexed, report.events_removed) == (1, 0, 0)
    assert raw.events.get(NEW).row_source == "listing" and pending(raw) == []
    assert not os.path.exists(layout.resolve(fixture.data_dir, layout.event_dir(NEW)))


def test_child_helper_reports_a_step_that_is_never_reached(tmp_path: Path) -> None:
    fixture = prepare("create", tmp_path / "data")
    out = subprocess.run([sys.executable, "-c", CHILD, str(fixture.data_dir), "create", "payload:event"], cwd=ROOT,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr  # yeni maçta yük dosyaları hazırlık alanında yazılır
    assert logical(fixture)["events"][str(NEW)]["slices"]["statistics"]["state"] == "ok"
