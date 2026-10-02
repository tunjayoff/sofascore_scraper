"""
Terminal menüsünün veri dosyalarını değiştiren işlevleri (src/ui/settings_ui.py) ve katalog
(docs/design/01-storage.md bölüm 3.4 ve 3.5).

Geri yükleme (`restore_data`) ve veri dizinini taşıma (`_change_data_directory`) dosyaları `shutil` ile
değiştirir. Deposu o süreçte zaten açık olan bir veri dizininde katalog bir sonraki açılışa kadar eski kalırdı:
aynı oturumun istatistikleri eski sayıları gösterir, maç araması silinmiş bir dosya için yanıt verirdi. İkisi de
ardından `shadow_cleared` kancasını çağırır (katalog dosyalardan yerinde yeniden kurulur), işlem yarıda kalsa da.

Temizleme (`_clear_all_data`, `_clear_selected_data`; plan maddesi ST-21) Store'un ağaçlarını
`MaintenanceService.clear` ile `maintenance` kilidi altında siler: eski klasörü ve v3 kopyasını birlikte
(karar S18); katalog `Store.clear` içinde aynı kilit altında yeniden kurulur. datasets/ ve reports/ Store ağacı
değildir: menü onları kendisi boşaltır.

Ölçüt tests/test_store_shadow.py'deki ile aynıdır: işlevden sonra katalog, aynı ağacın sıfırdan kurulmuş haline
eşittir (`CatalogAdmin.diff_from_rebuild() == []`) ve `StatusService(store).summary()` depo yeniden açılmadan
yeni sayıları gösterir. İşlevler gerçek dizinlerde çalışır; yalnızca sorular (`input`, modülün kendi ad
alanında) ve, yarıda kalan işlemler için, modülün gördüğü tek bir `shutil` işlevi ya da Store'un ağaç silmesi
(`src.store.files.remove_tree`) yamalanır.
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
from src.match_data_fetcher import MatchDataFetcher
from src.services.status import StatusService
from src.store import CatalogAdmin, LeaseHeld, Store, StoreError, open_store
from src.store import api as api_mod
from src.store import files as store_files
from src.ui import settings_ui
from src.ui.settings_ui import SettingsMenuHandler
from src.ui.stats_ui import StatsMenuHandler

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


def check_reports_only_the_copy_source(source: Path) -> None:
    """
    Paketin test sonu denetimini (`shadow_check`) şimdi çalıştırır. Denetim kancası bir kopyalamanın kaynağını
    da "yazıldı" sayar (tests/conftest.py, `shutil.copyfile`, `shutil.copytree` ve Windows'ta `shutil.copy2`'nin
    ürettiği `_winapi.CopyFile2` olaylarının iki yolunu da bildirir); yalnızca okunan kaynak dizin bu yüzden
    "kancasız yazıldı" diye bildirilir. Beklenen tek bulgu odur: hedef dizinin kataloğu yeniden kurulmuş
    haliyle karşılaştırılmış ve eşit bulunmuştur. Kaynağın bildirilmemesi de testi düşürür: kanca kopyalamayı
    görmemiş demektir, o zaman hedefe kancasız bir yazma da görünmez.
    """
    found = api_mod.shadow_check()
    assert [_normal(line.partition(UNHOOKED)[0]) for line in found if UNHOOKED in line] == [_normal(source)], found
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
    """
    Store maç detaylarını sildikten sonra matches/ silinemez (Store'un ağaç silmesi düşer): katalog diskte kalanı
    anlatır, menünün kendi dizinlerine (datasets/, reports/) sıra gelmez.
    """
    data = canonical.data_dir
    store = open_store(data)
    handler = handler_of(data)
    answer(monkeypatch, *replies)
    (data / "reports").mkdir(exist_ok=True)
    (data / "reports" / "r.json").write_bytes(b"{}")
    real_remove = store_files.remove_tree

    def failing(path: Any) -> bool:
        if Path(path) == data / "matches":
            raise StoreError(f"disk error: {path}", path=str(path))
        return real_remove(path)

    monkeypatch.setattr(store_files, "remove_tree", failing)

    getattr(handler, function)()  # hata kullanıcıya yazılır, dışarı çıkmaz

    out = capsys.readouterr().out
    assert "disk error" in out and handler.i18n.t("success_clear_all") not in out
    assert handler.i18n.t("success_clear_selected") not in out
    assert not any((data / "match_details").iterdir())
    assert any((data / "matches").iterdir()) and any((data / "seasons").iterdir())
    assert (data / "reports" / "r.json").exists()
    assert still_open(store, data)
    assert counts(store) == ONLY_LISTS
    assert differences(store) == []


def test_the_clear_removes_matches_of_both_layouts(canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    """
    Maç detaylarının temizliği eski klasörü ve v3 kopyasını birlikte siler (karar S18): yeniden yazılıp v3'e
    yükseltilen bir maç da, yalnızca v3'te duran yeni bir maç da gider. Ayrıca bildirim yazılır.
    """
    data = canonical.data_dir
    store = open_store(data)
    fetcher = MatchDataFetcher(MagicMock(), data_dir=str(data))
    fetcher._save_match_data(str(ARS), fetcher._load_match_data_from_dir("", str(ARS)))  # yükseltilir
    assert store.events.get(ARS).layout == "v3" and (data / "v3" / "events").is_dir()
    handler = handler_of(data)
    answer(monkeypatch, "3", YES)

    handler._clear_selected_data()

    out = capsys.readouterr().out
    assert handler.i18n.t("success_clear_selected") in out
    assert handler.i18n.t("success_dir_cleared", name="match_details") in out
    assert not (data / "v3" / "events").exists() and not any((data / "match_details").iterdir())
    assert not store.events.get(ARS).has_event_payload  # listede kalır (matches/), detayı gider
    assert counts(store) == ONLY_LISTS
    assert differences(store) == []


def test_the_menu_tells_which_layout_it_covers(canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    """Yedekleme ve geri yükleme yalnızca eski klasörleri kapsar; temizleme iki düzeni de: menü bunu söyler."""
    handler = handler_of(canonical.data_dir)
    answer(monkeypatch, "9", "9", str(canonical.data_dir / "does-not-exist"))
    handler.clear_data()
    handler.backup_data()
    handler.restore_data()

    out = capsys.readouterr().out
    assert out.count(handler.i18n.t("notice_menu_backup_old_layout")) == 2
    assert out.count(handler.i18n.t("notice_menu_clear_both_layouts")) == 1


def test_the_clear_takes_the_maintenance_lease_and_refuses_while_another_holder_has_it(
        canonical: sf.LegacyFixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """
    Temizleme `maintenance` kilidini alır: kilit başkasındaysa (burada: kilit yöneticisi LeaseHeld verir) hiçbir
    şey silinmez, hata kullanıcıya yazılır. Kilit alınırsa Store.clear onun altında çalışır (ikinci kez almaz).
    """
    data = canonical.data_dir
    store = open_store(data)
    handler = handler_of(data)
    taken: List[Tuple[str, str]] = []
    real_lease = Store.lease

    def busy(self: Store, name: str, *, purpose: str = "", wait: float = 0.0) -> Any:
        taken.append((name, purpose))
        raise LeaseHeld(f"lease {name} is held by another process", path=str(self.data_dir))

    monkeypatch.setattr(Store, "lease", busy)
    answer(monkeypatch, YES, YES)
    handler._clear_all_data()

    out = capsys.readouterr().out
    assert "held by another process" in out and handler.i18n.t("success_clear_all") not in out
    assert taken == [("maintenance", "op:clear")]
    assert any((data / "match_details").iterdir()) and counts(store) == FULL

    acquired: List[str] = []

    def spy(self: Store, name: str, **kwargs: Any) -> Any:
        acquired.append(name)
        return real_lease(self, name, **kwargs)

    monkeypatch.setattr(Store, "lease", spy)
    answer(monkeypatch, YES, YES)
    handler._clear_all_data()
    assert acquired == ["maintenance"] and counts(store) == EMPTY


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
    check_reports_only_the_copy_source(backup / "data")


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
    check_reports_only_the_copy_source(backup / "data")


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
    check_reports_only_the_copy_source(backup / "data")


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
    check_reports_only_the_copy_source(data)


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
    check_reports_only_the_copy_source(data)


# --- istatistik menüsü -----------------------------------------------------------------------------------

def test_the_statistics_menu_says_its_disk_sizes_cover_the_old_folders(canonical: sf.LegacyFixture,
                                                                       capsys: pytest.CaptureFixture[str]) -> None:
    """
    Sayılar katalogdan gelir (iki düzen), disk boyutları eski klasörlerden: not sistem görünümünde ve lig
    dökümünün başında bir kez yazılır.
    """
    config = MagicMock()
    config.get_leagues.return_value = {17: "Premier League", 8: "LaLiga"}
    colors = {name: "" for name in ("TITLE", "SUBTITLE", "WARNING", "INFO", "SUCCESS", "DIM")}
    stats = StatsMenuHandler(config, str(canonical.data_dir), colors)
    notice = stats.i18n.t("notice_menu_disk_old_layout")

    stats.show_system_stats()
    assert capsys.readouterr().out.count(notice) == 1
    for league_id in (17, 8):
        stats.show_league_stats(league_id)
    out = capsys.readouterr().out
    assert out.count(notice) == 1 and out.index(notice) < out.index("Premier League")
