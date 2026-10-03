"""
`shadow_cleared` kancası (src/store/api.py; docs/design/01-storage.md bölüm 3.5): dizin ağaçları Store'un dışında
topluca değişince, o süreçte zaten açık olan deponun kataloğu kalan dosyalardan yerinde yeniden kurulur.

Kancanın çağıranı terminal menüsüydü (temizleme, geri yükleme, veri dizinini taşıma; plan maddesi P26 menüyü
kaldırdı). Bu testler kancanın kendisini sınar; daha önce tests/test_settings_ui_catalog.py onu menünün
işlevleri üzerinden sınıyordu. Ağaçları test kendisi değiştirir: denetim kipi (STORE_SHADOW_CHECK) bu testlerde
kapalıdır, yoksa paketin denetim kancası testin yazdıklarını ürün kodu dosyalara dokunduğu anda kataloğa alır
ve kancanın etkisi görünmezdi.

Ölçüt tests/test_store_shadow.py'deki ile aynıdır: kancadan sonra katalog, aynı ağacın sıfırdan kurulmuş haline
eşittir (`CatalogAdmin.diff_from_rebuild() == []`) ve `StatusService(store).summary()` depo yeniden açılmadan
yeni sayıları gösterir.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, List, Tuple

import pytest

import src.store
import store_fixtures as sf
from src.services.status import StatusService
from src.store import CatalogAdmin, Store, open_store
from src.store import api as api_mod

ARS = sf.event_id(sf.PL_ARS)  # detayı olan bir maç
NO_DETAIL = sf.event_id(sf.PL_NO_DETAIL)  # yalnızca listede (matches/ altında)

# `canonical` ağacının sayıları: (maç, detayı olan maç, sezon listelerindeki sezon)
FULL = (35, 23, 11)
EMPTY = (0, 0, 0)
ONLY_LISTS = (35, 0, 11)  # match_details/ yok
TREES = ("seasons", "matches", "match_details")


@pytest.fixture(autouse=True)
def no_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Uygulamadaki hal: denetim kipi kapalı (kanca beklenmeyen hatayı yutar, test sonu karşılaştırması yok)."""
    monkeypatch.delenv(api_mod.SHADOW_CHECK_ENV, raising=False)


def counts(store: Store) -> Tuple[int, int, int]:
    """İstatistiklerin gösterdiği sayılar, açık depodan (yeniden açmadan)."""
    summary = StatusService(store).summary(only_finished=False, sizes=False)
    return summary.matches, summary.details, summary.seasons


def still_open(store: Store, data_dir: Path) -> bool:
    """Süreç aynı depoyu tutuyor: sayılar yeniden açılışın uzlaştırmasından gelmiyor."""
    return not store.closed and open_store(data_dir) is store


def copy_trees(source: Path, target: Path, names: Tuple[str, ...] = TREES) -> None:
    """Store'un dışından toplu değişiklik (menünün geri yüklemesi ve taşıması böyle kopyalardı)."""
    for name in names:
        shutil.copytree(source / name, target / name, dirs_exist_ok=True)


def test_the_hook_rebuilds_the_catalog_of_the_open_store_after_a_copy(tmp_path: Path) -> None:
    data = tmp_path / "data"
    backup = sf.build_fixture("canonical", tmp_path / "backup").data_dir
    store = open_store(data)
    assert counts(store) == EMPTY and store.events.get(ARS) is None

    copy_trees(backup, data)
    src.store.shadow_cleared(data)

    assert still_open(store, data)
    assert counts(store) == FULL
    assert store.events.get(ARS).has_event_payload and store.events.get(NO_DETAIL) is not None
    assert store.catalog.diff_from_rebuild() == []


def test_the_hook_forgets_what_was_deleted_behind_the_store(tmp_path: Path) -> None:
    """Uzlaştırma varlık satırlarını silmez: silinen ağacın maçları ancak yerinde yeniden kurulumla katalogdan çıkar."""
    data = sf.build_fixture("canonical", tmp_path / "data").data_dir
    store = open_store(data)
    assert counts(store) == FULL

    shutil.rmtree(data / "match_details")
    src.store.shadow_cleared(data)

    assert still_open(store, data)
    assert counts(store) == ONLY_LISTS
    assert store.catalog.diff_from_rebuild() == []


def test_the_hook_rebuilds_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = sf.build_fixture("canonical", tmp_path / "data").data_dir
    open_store(data)
    rebuild = CatalogAdmin.rebuild
    rebuilt: List[Any] = []

    def spy(self: CatalogAdmin, **kwargs: Any) -> Any:
        rebuilt.append(kwargs)
        return rebuild(self, **kwargs)

    monkeypatch.setattr(CatalogAdmin, "rebuild", spy)
    src.store.shadow_cleared(data)

    assert rebuilt == [{"mode": api_mod.MODE_IN_PLACE}]


def test_an_unexpected_catalog_error_is_logged_and_does_not_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                 caplog: pytest.LogCaptureFixture) -> None:
    data = tmp_path / "data"
    backup = sf.build_fixture("canonical", tmp_path / "backup")
    open_store(data)
    copy_trees(backup.data_dir, data)

    def broken(self: CatalogAdmin, **kwargs: Any) -> Any:
        raise RuntimeError("dizinleyicide hata")

    monkeypatch.setattr(CatalogAdmin, "rebuild", broken)
    with caplog.at_level(logging.ERROR):
        src.store.shadow_cleared(data)  # dosyalar yerinde kalır; hata yalnızca loglanır

    assert len(list((data / "match_details").rglob("basic.json"))) == len(backup.details)
    assert [r.getMessage() for r in caplog.records if r.name == "Store" and r.levelno >= logging.ERROR] == [
        f"Unexpected error while updating the catalog of {data} after a clear"]
