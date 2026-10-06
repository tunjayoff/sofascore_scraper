"""
Sunucular ve otomasyon için komut satırı (docs/design/02-services.md bölüm 4).

    ssc <komut> [seçenekler]            # `pip install -e .` ile kurulan komut (pyproject.toml)
    python -m src.cli.main <komut> ...  # kurulum gerektirmez; proje klasöründen

İlkeler: soru sormaz; stdout yalnızca sonucu taşır; loglar ve hatalar stderr'e gider; her komut yeniden
çalıştırılabilir; çıkış kodları sözleşmenin parçasıdır (src/cli/exit_codes.py).

Modüller:
  main.py        giriş noktası (`src.cli.main:main`): argparse ağacı, genel bayraklar, hata yazımı
  output.py      çıktı kuralları ve JSON zarfı
  exit_codes.py  çıkış kodu tablosu
  commands/      her komut kendini kaydeden bir modül (commands/__init__.py)

Depo kökündeki `main.py` (eski giriş noktası) bir kabuktur: eski bayrakları bu paketin komutlarına çevirir
(src/cli/legacy_flags.py, plan maddesi P19); bayraklarla birlikte P30'da kalkar.

Bu dosya yalnızca standart kütüphaneyi ve src/version.py'yi içe aktarır: `ssc --version` paketler
kurulmadan da çalışır.
"""
from __future__ import annotations

from src.version import __version__

# Konsol betiğinin adı (pyproject.toml [project.scripts])
PROG = "ssc"
# Kurulum yapılmadan çalıştırma biçimi; yardım metinlerinde komut adı olarak görünür
MODULE_PROG = "python -m src.cli.main"
VERSION_TEXT = f"SofaScore Scraper {__version__}"

__all__ = ["MODULE_PROG", "PROG", "VERSION_TEXT"]
