"""
Terminal menüsünün veri dosyalarını değiştiren işlevleri (src/ui/settings_ui.py) ve katalog
(docs/design/01-storage.md bölüm 3.4 ve 3.5).

Temizleme (`_clear_all_data`, `_clear_selected_data`), geri yükleme (`restore_data`) ve veri dizinini taşıma
(`_change_data_directory`) dosyaları `shutil` ile değiştirir. Deposu o süreçte zaten açık olan bir veri
dizininde katalog bir sonraki açılışa kadar eski kalırdı: aynı oturumun istatistikleri eski sayıları gösterir,
maç araması silinmiş bir dosya için yanıt verirdi. Her işlev artık ardından `shadow_cleared` kancasını çağırır
(katalog dosyalardan yerinde yeniden kurulur), işlem yarıda kalsa da.

Ölçüt tests/test_store_shadow.py'deki ile aynıdır: işlevden sonra katalog, aynı ağacın sıfırdan kurulmuş haline
eşittir (`CatalogAdmin.diff_from_rebuild() == []`) ve `StatusService(store).summary()` depo yeniden açılmadan
yeni sayıları gösterir. İşlevler gerçek dizinlerde çalışır; yalnızca sorular (`input`, modülün kendi ad
alanında) ve, yarıda kalan işlemler için, modülün gördüğü tek bir `shutil` işlevi yamalanır.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Tuple
from unittest.mock import MagicMock

import pytest

import src.store
import store_fixtures as sf
from src.services.status import StatusService
from src.store import CatalogAdmin, Store, open_store
from src.store import api as api_mod
from src.ui import settings_ui
from src.ui.settings_ui import SettingsMenuHandler

ARS = sf.event_id(sf.PL_ARS)  # detayı olan bir maç
NO_DETAIL = sf.event_id(sf.PL_NO_DETAIL)  # yalnızca listede (matches/ altında)

# `canonical` ağacının sayıları: (maç, detayı olan maç, sezon listelerindeki sezon)
FULL = (35, 23, 11)
EMPTY = (0, 0, 0)
ONLY_DETAILS = (23, 23, 0)  # seasons/ ve matches/ yok
ONLY_LISTS = (35, 0, 11)  # match_details/ yok
NO_SEASON_LISTS = (35, 23, 0)  # seasons/ yok
ONLY_SEASON_LISTS = (0, 0, 11)  # yalnızca seasons/

YES = "y"
UNHOOKED = ": written without a shadow hook afterwards: "


@pytest.fixture
def canonical(tmp_path: Path) -> sf.LegacyFixture:
    return sf.build_fixture("canonical", tmp_path / "data")


@pytest.fixture
def no_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Uygulamadaki hal: denetim kipi kapalı (kanca beklenmeyen hatayı yutar)."""
    monkeypatch.delenv(api_mod.SHADOW_CHECK_ENV)


def handler_of(data_dir: Path) -> SettingsMenuHandler:
    colors = {name: "" for name in ("SUBTITLE", "WARNING", "INFO", "SUCCESS")}
    return SettingsMenuHandler(MagicMock(), str(data_dir), colors)


def answer(monkeypatch: pytest.MonkeyPatch, *replies: str) -> None:
    """Menünün sorularını sırayla yanıtlar. Yalnızca `settings_ui` içindeki `input` adı değişir."""
    pending = list(replies)

    def fake_input(prompt: str = "") -> str:
        assert pending, f"unexpected question: {prompt!r}"
        return pending.pop(0)

    monkeypatch.setattr(settings_ui, "input", fake_input, raising=False)


def counts(store: Store) -> Tuple[int, int, int]:
    """İstatistiklerin gösterdiği sayılar, açık depodan (yeniden açmadan)."""
    summary = StatusService(store).summary(only_finished=False, sizes=False)
    return summary.matches, summary.details, summary.seasons


def differences(store: Store) -> List[str]:
    """Deponun kataloğu ile aynı ağacın sıfırdan kurulmuş hali arasındaki farklar."""
    return store.catalog.diff_from_rebuild()


def still_open(store: Store, data_dir: Path) -> bool:
    """Süreç aynı depoyu tutuyor: sayılar yeniden açılışın uzlaştırmasından gelmiyor."""
    return not store.closed and open_store(data_dir) is store


def break_shutil(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """
    `settings_ui`'nin gördüğü `shutil.<name>` her çağrıda düşer; ötekiler gerçek işlevlerdir. Yalnızca modülün
    `shutil` adı değişir: Store'un ve pytest'in kullandığı `shutil` aynı kalır. Sahte işlev dosyaya dokunmadan
    düşer, gerçek çağrıların yığınında test çerçevesi olmaz (denetim kancası onları ürün koduna sayar).
    """
    def failing(path: Any, *args: Any, **kwargs: Any) -> Any:
        raise OSError(f"disk error: {path}")

    real = {other: getattr(shutil, other) for other in ("rmtree", "copytree", "copy2")}
    monkeypatch.setattr(settings_ui, "shutil", SimpleNamespace(**{**real, name: failing}))


def _normal(path: Any) -> str:
    return os.path.normcase(os.path.realpath(os.fspath(path)))


def check_reports_at_most_the_copy_source(source: Path) -> None:
    """
    Paketin test sonu denetimini (`shadow_check`) şimdi çalıştırır. Denetim kancası bir kopyalamanın kaynağını
    da "yazıldı" sayar (tests/conftest.py, `shutil.copyfile` ve `shutil.copytree` olaylarının iki yolunu da
    bildirir); yalnızca okunan kaynak dizin bu yüzden "kancasız yazıldı" diye bildirilir. Olabilecek tek bulgu
    odur: hedef dizinin kataloğu yeniden kurulmuş haliyle karşılaştırılmış ve eşit bulunmuştur. Windows'ta
    `shutil.copy2` dosyayı `_winapi.CopyFile2` ile kopyalar: `shutil.copyfile` olayı (ve `open` olayları)
    yerine `_winapi.CopyFile2` olayı üretilir, kanca onu dinlemez. Yalnızca tek tek dosyaların kopyalandığı
    bir işlemde (`shutil.copytree` düşerse) kaynak orada bildirilmez. Başka her bulgu testi düşürür; hedefin
    kataloğu her platformda testin gövdesinde ve burada (kancasız olmayan satırlar) karşılaştırılır.
    """
    found = api_mod.shadow_check()
    reported = [_normal(line.partition(UNHOOKED)[0]) for line in found if UNHOOKED in line]
    assert reported in ([], [_normal(source)]), found
    assert [line for line in found if UNHOOKED not in line] == []


# --- temizleme -------------------------------------------------------------------------------------------

def test_clear_all_rebuilds_the_catalog_of_the_open_store(canonical: sf.LegacyFixture,
                                                          monkeypatch: pytest.MonkeyPatch,
                                                          capsys: pytest.CaptureFixture[str]) -> None:
    data = canonical.data_dir
    store = open_store(data)
    handler = handler_of(data)
    assert counts(store) == FULL and store.events.get(ARS).has_event_payload
    answer(monkeypatch, YES, YES)

    handler._clear_all_data()

    assert handler.i18n.t("success_clear_all") in capsys.readouterr().out
    assert not any((data / "match_details").iterdir()) and not any((data / "matches").iterdir())
    assert still_open(store, data)
    assert counts(store) == EMPTY and StatusService(store).summary(sizes=False).tournaments == ()
    assert store.events.get(ARS) is None and store.events.get(NO_DETAIL) is None  # arama silinen maçı bulmaz
    assert differences(store) == []


@pytest.mark.parametrize("selection, left", [
    ("3", ONLY_LISTS),
    ("1, 2", ONLY_DETAILS),
    ("1,2,3,4,5", EMPTY),
])
def test_clear_selected_rebuilds_the_catalog_of_the_open_store(canonical: sf.LegacyFixture,
                                                               monkeypatch: pytest.MonkeyPatch,
                                                               capsys: pytest.CaptureFixture[str],
                                                               selection: str, left: Tuple[int, int, int]) -> None:
    data = canonical.data_dir
    store = open_store(data)
    handler = handler_of(data)
    assert counts(store) == FULL
    answer(monkeypatch, selection, YES)

    handler._clear_selected_data()

    assert handler.i18n.t("success_clear_selected") in capsys.readouterr().out
    assert still_open(store, data)
    assert counts(store) == left
    assert differences(store) == []


@pytest.mark.parametrize("function, replies", [
    ("_clear_all_data", (YES, YES)),
    ("_clear_selected_data", ("1,2,3", YES)),
])
def test_a_clear_that_fails_half_way_still_rebuilds_the_catalog(canonical: sf.LegacyFixture,
                                                                monkeypatch: pytest.MonkeyPatch,
                                                                capsys: pytest.CaptureFixture[str],
                                                                function: str, replies: Tuple[str, ...]) -> None:
    """seasons/ altındaki dosyalar silindikten sonra ilk dizin silinemez: katalog diskte kalanı anlatır."""
    data = canonical.data_dir
    store = open_store(data)
    handler = handler_of(data)
    answer(monkeypatch, *replies)
    break_shutil(monkeypatch, "rmtree")

    getattr(handler, function)()  # hata kullanıcıya yazılır, dışarı çıkmaz

    out = capsys.readouterr().out
    assert "disk error" in out and handler.i18n.t("success_clear_all") not in out
    assert handler.i18n.t("success_clear_selected") not in out
    assert not any((data / "seasons").iterdir()) and any((data / "matches").iterdir())
    assert still_open(store, data)
    assert counts(store) == NO_SEASON_LISTS
    assert differences(store) == []


def test_a_catalog_that_cannot_be_rebuilt_does_not_fail_the_clear(canonical: sf.LegacyFixture,
                                                                  monkeypatch: pytest.MonkeyPatch,
                                                                  capsys: pytest.CaptureFixture[str],
                                                                  caplog: pytest.LogCaptureFixture) -> None:
    """Katalog ikincil bir kayıttır: kanca yazamazsa uyarı yazar; temizleme başarıyla biter."""
    data = canonical.data_dir
    store = open_store(data)
    handler = handler_of(data)
    answer(monkeypatch, YES, YES)
    rebuild = CatalogAdmin.rebuild

    def busy(self: CatalogAdmin, **kwargs: Any) -> Any:
        raise src.store.StoreBusy("catalog.db kilitli", path=self.catalog.path)

    monkeypatch.setattr(CatalogAdmin, "rebuild", busy)
    with caplog.at_level(logging.WARNING):
        handler._clear_all_data()

    out = capsys.readouterr().out
    assert handler.i18n.t("success_clear_all") in out and "catalog.db kilitli" not in out
    assert not any((data / "match_details").iterdir())
    warned = [r.getMessage() for r in caplog.records if r.name == "Store" and r.levelno >= logging.WARNING]
    assert len(warned) == 1 and "was not updated after a clear" in warned[0]
    assert api_mod.shadow_check() == []  # kancası başarısız olan dizin karşılaştırılmaz

    # Katalog yeniden yazılabiliyor: sonraki temizlemenin kancası kataloğu eşitler ve uyarı durumu silinir
    monkeypatch.setattr(CatalogAdmin, "rebuild", rebuild)
    answer(monkeypatch, YES, YES)
    handler._clear_all_data()
    assert counts(store) == EMPTY and differences(store) == []
    assert api_mod._shadow_key(data) not in api_mod._shadow_warned


# --- geri yükleme ----------------------------------------------------------------------------------------

def test_restore_rebuilds_the_catalog_of_the_open_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "data"
    backup = tmp_path / "backup"
    sf.build_fixture("canonical", backup / "data")
    store = open_store(data)
    handler = handler_of(data)
    assert counts(store) == EMPTY and store.events.get(ARS) is None
    answer(monkeypatch, str(backup), "2,3,4")

    handler.restore_data()

    assert handler.i18n.t("success_restore_selected") in capsys.readouterr().out
    assert still_open(store, data)
    assert counts(store) == FULL
    assert store.events.get(ARS).has_event_payload and store.events.get(NO_DETAIL) is not None
    assert differences(store) == []
    check_reports_at_most_the_copy_source(backup / "data")


def test_restore_rebuilds_the_catalog_only_when_data_was_restored(canonical: sf.LegacyFixture, tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Yalnızca yapılandırma geri yüklenirse veri dizini değişmez ve katalog yeniden kurulmaz (büyük dizinde
    pahalıdır); bir veri ağacı geri yüklenince kurulur. Yedek: var olan verinin eksik maç detayları.
    """
    data = canonical.data_dir
    backup = tmp_path / "backup"
    shutil.copytree(data / "match_details", backup / "data" / "match_details")
    shutil.rmtree(data / "match_details")
    store = open_store(data)
    handler = handler_of(data)
    handler.config_manager.league_config_path = str(tmp_path / "leagues.txt")
    rebuild = CatalogAdmin.rebuild
    rebuilt: List[Any] = []

    def spy(self: CatalogAdmin, **kwargs: Any) -> Any:
        rebuilt.append(kwargs)
        return rebuild(self, **kwargs)

    monkeypatch.setattr(CatalogAdmin, "rebuild", spy)
    assert counts(store) == ONLY_LISTS

    answer(monkeypatch, str(backup), "1")
    handler.restore_data()

    handler.config_manager.reload_config.assert_called_once_with()
    assert rebuilt == [] and counts(store) == ONLY_LISTS

    answer(monkeypatch, str(backup), "4")
    handler.restore_data()

    assert len(rebuilt) == 1
    assert still_open(store, data)
    assert counts(store) == FULL and store.events.get(ARS).has_event_payload
    assert differences(store) == []
    check_reports_at_most_the_copy_source(backup / "data")


def test_a_restore_that_fails_half_way_still_rebuilds_the_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                  capsys: pytest.CaptureFixture[str]) -> None:
    """
    Sezon listeleri geri yüklendikten sonra matches/ kopyalanamaz (hedefte aynı adda bir dosya var, dizin
    kurulamaz): katalog diske ulaşanı anlatır.
    """
    data = tmp_path / "data"
    backup = tmp_path / "backup"
    sf.build_fixture("canonical", backup / "data")
    data.mkdir()
    (data / "matches").write_bytes(b"")
    store = open_store(data)
    handler = handler_of(data)
    answer(monkeypatch, str(backup), "2,3,4")

    handler.restore_data()  # hata kullanıcıya yazılır, dışarı çıkmaz

    out = capsys.readouterr().out
    assert handler.i18n.t("success_restore_league_config") not in out
    assert handler.i18n.t("success_restore_season_data") in out
    assert handler.i18n.t("success_restore_selected") not in out
    assert (data / "matches").is_file() and not (data / "match_details").exists()
    assert still_open(store, data)
    assert counts(store) == ONLY_SEASON_LISTS
    assert differences(store) == []
    check_reports_at_most_the_copy_source(backup / "data")


def test_an_unexpected_catalog_error_does_not_fail_the_restore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                               capsys: pytest.CaptureFixture[str],
                                                               caplog: pytest.LogCaptureFixture,
                                                               no_check: None) -> None:
    data = tmp_path / "data"
    backup = tmp_path / "backup"
    fixture = sf.build_fixture("canonical", backup / "data")
    open_store(data)
    handler = handler_of(data)
    answer(monkeypatch, str(backup), "2,3,4")

    def broken(self: CatalogAdmin, **kwargs: Any) -> Any:
        raise RuntimeError("dizinleyicide hata")

    monkeypatch.setattr(CatalogAdmin, "rebuild", broken)
    with caplog.at_level(logging.ERROR):
        handler.restore_data()

    out = capsys.readouterr().out
    assert handler.i18n.t("success_restore_selected") in out and "dizinleyicide hata" not in out
    assert len(list((data / "match_details").rglob("basic.json"))) == len(fixture.details)  # dosyalar geri geldi
    assert [r.getMessage() for r in caplog.records if r.name == "Store" and r.levelno >= logging.ERROR] == [
        f"Unexpected error while updating the catalog of {data} after a clear"]


# --- veri dizinini taşıma --------------------------------------------------------------------------------

def test_moving_the_data_directory_rebuilds_the_catalog_of_the_target(canonical: sf.LegacyFixture, tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """Dosyalar yeni dizine kopyalanır; hedefin deposu bu süreçte açıksa kataloğu kopyalananı hemen gösterir."""
    data = canonical.data_dir
    target = tmp_path / "moved"
    store = open_store(data)
    target_store = open_store(target)
    handler = handler_of(data)
    assert counts(target_store) == EMPTY
    answer(monkeypatch, str(target), YES)

    handler._change_data_directory()

    handler.config_manager.update_env_variable.assert_called_once_with("DATA_DIR", str(target))
    assert still_open(target_store, target)
    assert counts(target_store) == FULL and target_store.events.get(ARS).has_event_payload
    assert differences(target_store) == []
    assert counts(store) == FULL and differences(store) == []  # kaynak yerinde durur (kopyalama)
    check_reports_at_most_the_copy_source(data)


def test_a_move_that_fails_half_way_still_rebuilds_the_catalog_of_the_target(
        canonical: sf.LegacyFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    """Sezon listeleri kopyalandıktan sonra ilk dizin kopyalanamaz: hedefin kataloğu diske ulaşanı anlatır."""
    data = canonical.data_dir
    target = tmp_path / "moved"
    open_store(data)
    target_store = open_store(target)
    handler = handler_of(data)
    answer(monkeypatch, str(target), YES)
    break_shutil(monkeypatch, "copytree")

    handler._change_data_directory()  # hata kullanıcıya yazılır, dışarı çıkmaz

    assert "disk error" in capsys.readouterr().out
    handler.config_manager.update_env_variable.assert_not_called()
    assert not any((target / "matches").iterdir())
    assert still_open(target_store, target)
    assert counts(target_store) == ONLY_SEASON_LISTS
    assert differences(target_store) == []
    check_reports_at_most_the_copy_source(data)
