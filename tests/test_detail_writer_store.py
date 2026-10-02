"""
Detay yazıcısı Store'a yazar (plan maddesi ST-21; docs/design/01-storage.md 2.3, 5.3, 6.2 ve karar 4).

Sabitlenen kurallar:
  * Yeni maç v3 düzenine yazılır (`v3/events/.../<id>/`); eski düzende duran maç ilk yazmasında önce v3'e
    yükseltilir ve eski dizinine dokunulmaz, silinmez (karar 4, S3).
  * CSV dışa aktarımı (EX-1, `store.events` üzerinden okur) v3'e yazılmış maçları aynen görür: aynı maçlar eski
    düzende de, yalnızca v3'te de, yükseltilmiş halde de aynı CSV'yi verir.
  * Değişiklik satırı yarıda kesilen bir yazmada kaybolmaz (FX-12'nin niyet dosyası): yenileme `observe` ile yazar.

Ağ yok.
"""
from __future__ import annotations

import contextlib
import io
from pathlib import Path
from typing import Any, Dict, Iterator
from unittest.mock import MagicMock, patch

import pytest

import store_dump
import store_fixtures as sf
from src.match_data_fetcher import MatchDataFetcher
from src.services.export import ExportService, ExportSpec
from src.store import EventQuery, open_store
from src.store import events as events_mod


def _fetcher(data_dir: Path) -> MatchDataFetcher:
    return MatchDataFetcher(MagicMock(), data_dir=str(data_dir))


def _csv(data_dir: Path) -> str:
    out = io.StringIO()
    ExportService(open_store(data_dir)).export(ExportSpec(), out)
    return out.getvalue()


def _tree(root: Path) -> Dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def canonical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sf.LegacyFixture:
    fixture = sf.build_fixture("canonical", tmp_path / "data")
    monkeypatch.setenv("DATA_DIR", str(fixture.data_dir))
    return fixture


def test_the_csv_export_sees_matches_written_by_the_new_writer_unchanged(canonical: sf.LegacyFixture,
                                                                         tmp_path: Path) -> None:
    """Aynı maçlar bir yanda eski düzende (fabrika), öte yanda yalnızca v3'te (yazıcı): CSV satır satır aynı."""
    legacy_csv = _csv(canonical.data_dir)
    source = _fetcher(canonical.data_dir)
    fresh = tmp_path / "fresh"
    writer = _fetcher(fresh)
    for event_id in canonical.detail_ids:
        data = source._load_match_data_from_dir("", str(event_id))
        writer._save_match_data(str(event_id), data)

    store = open_store(fresh)
    rows = list(store.events.iter(EventQuery(has_details=True)))
    assert {row.layout for row in rows} == {"v3"} and len(rows) == len(canonical.detail_ids)
    assert not [p for p in (fresh / "match_details").rglob("*") if p.is_file()]  # eski düzene hiçbir şey yazılmadı
    assert _csv(fresh) == legacy_csv


def test_a_record_of_the_old_layout_is_promoted_by_its_next_write_and_its_folder_is_kept(
        canonical: sf.LegacyFixture) -> None:
    """
    Eski düzendeki her kayıt bir kez daha yazılır (aynı veriyle): v3'e yükseltilir, eski ağaç bayt bayt aynı kalır,
    mantıksal döküm ve CSV değişmez.
    """
    data = canonical.data_dir
    before_tree = _tree(data / "match_details")
    before_dump = store_dump.dump(data, canonical.leagues)
    before_csv = _csv(data)
    fetcher = _fetcher(data)
    for event_id in canonical.detail_ids:
        fetcher._save_match_data(str(event_id), fetcher._load_match_data_from_dir("", str(event_id)))

    store = open_store(data)
    rows = list(store.events.iter(EventQuery(has_details=True)))
    assert {row.layout for row in rows} == {"v3"} and all(row.legacy_path for row in rows)
    assert _tree(data / "match_details") == before_tree
    after_dump = store_dump.dump(data, canonical.leagues)
    # Yeniden yazma gözlemi yeniler ve verisi olan dilimlerin işaretlerini siler (eski yazıcının kuralı); gerisi aynı
    for dump in (before_dump, after_dump):
        for event in dump["events"].values():
            event.pop("observation")
            for entry in event["slices"].values():
                if entry["state"] == "ok":
                    entry.update(empty_count=0, unverified_empty_count=0, error=None)
    assert store_dump.diff(before_dump, after_dump) == []
    assert _csv(data) == before_csv
    assert store.catalog.diff_from_rebuild() == []


@contextlib.contextmanager
def _serving(event: Dict[str, Any]) -> Iterator[Any]:
    """Sahte SofaScore (tests/fakes/sofascore.py): yalnızca bu maçın /event'i; yenileme boru hattından geçer (P13)."""
    import copy

    from fakes.sofascore import FakeSofaScore

    fake = FakeSofaScore()
    fake.add_event(copy.deepcopy(event))
    with fake:
        yield fake


def test_a_change_row_survives_a_refresh_interrupted_before_the_change_log(
        canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Yenileme `observe` ile yazar: olay yükü yerine konduktan sonra, değişiklik günlüğüne satır eklenmeden kesilen
    yazmanın satırı kaybolmaz (FX-12): maça bir sonraki yazma onu ekler, satır bir kez yazılır.
    """
    data = canonical.data_dir
    store = open_store(data)
    event_id = sf.event_id(sf.PL_LIV)
    fetcher = _fetcher(data)
    old = store.events.payload(event_id)
    new = {**old, "homeScore": {**old["homeScore"], "current": old["homeScore"]["current"] + 1}}
    last = store.changes.last_seq()

    def killed(self: events_mod.EventStore, step: str) -> None:
        if step == events_mod.STEP_MANIFEST:
            raise KeyboardInterrupt("process killed after the payload, before the change log")

    with _serving(new), \
            patch.object(events_mod.EventStore, "_checkpoint", killed), pytest.raises(KeyboardInterrupt):
        fetcher.refresh_match(str(event_id))
    assert store.changes.last_seq() == last  # satır henüz günlükte yok; niyet dosyasında

    with _serving(new):
        assert fetcher.refresh_match(str(event_id)) is not None
    rows = store.changes.list(event_id=event_id, after_seq=last)
    assert len(rows) == 1 and "homeScore.current" in rows[0].fields
    assert store.catalog.diff_from_rebuild() == []
