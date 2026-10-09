"""
Sunucular ve otomasyon için komut satırı (docs/design/02-services.md bölüm 4).

    ssc <komut> [seçenekler]            # `pip install -e .` ile kurulan komut (pyproject.toml)
    python -m sofascore_scraper.cli.main <komut> ...  # kurulum gerektirmez; proje klasöründen

İlkeler: soru sormaz; stdout yalnızca sonucu taşır; loglar ve hatalar stderr'e gider; her komut yeniden
çalıştırılabilir; çıkış kodları sözleşmenin parçasıdır (sofascore_scraper/cli/exit_codes.py).

Modüller:
  main.py        giriş noktası (`sofascore_scraper.cli.main:main`): argparse ağacı, genel bayraklar, hata yazımı
  output.py      çıktı kuralları ve JSON zarfı
  exit_codes.py  çıkış kodu tablosu
  commands/      her komut kendini kaydeden bir modül (commands/__init__.py)

Depo kökündeki `main.py` bu CLI'nin yönlendiricisidir (`python main.py sync` = `ssc sync`). 2.x'in bayrakları 3.1'de
kalktı (P30): onlarla çalıştırma, yerine geçen komutu söyleyen bir kullanım hatasıdır (cli/removed_flags.py).

Bu dosya yalnızca standart kütüphaneyi ve sofascore_scraper/version.py'yi içe aktarır: `ssc --version` paketler
kurulmadan da çalışır.
"""
from __future__ import annotations

from sofascore_scraper.version import __version__

# Konsol betiğinin adı (pyproject.toml [project.scripts])
PROG = "ssc"
# Kurulum yapılmadan çalıştırma biçimi; yardım metinlerinde komut adı olarak görünür
MODULE_PROG = "python -m sofascore_scraper.cli.main"
VERSION_TEXT = f"SofaScore Scraper {__version__}"
# Depo kökündeki `main.py`nin adı (yönlendirici); kullanım ipucu onun yerine kurulu komutun adını (PROG) gösterir
LEGACY_PROG = "python main.py"

__all__ = ["LEGACY_PROG", "MODULE_PROG", "PROG", "VERSION_TEXT"]
