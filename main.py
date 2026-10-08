#!/usr/bin/env python3
"""
SofaScore Scraper'ın depo kökündeki giriş noktası: `ssc` komutunun (sofascore_scraper/cli/main.py) yönlendiricisi.

    python main.py <komut> [seçenekler]     `ssc <komut>` ile aynı: sync, status, serve, ...
    python main.py                          komut verilmedi: komut listesi ve kullanım hatası (çıkış kodu 2)
    python main.py --version                sürüm

Kurulum yapılmadan çalışır (proje klasöründen); kurulumdan sonra `ssc` aynı komuttur. 2.x'in bayrakları
(`--headless --update-all`, `--refresh-only`, `--watch`, `--web`, `--doctor`, ...) 3.0.0'da kullanımdan kalktı ve 3.1'de
silindi (plan maddesi P30): onlarla çalıştırma bir kullanım hatasıdır ve ileti yerine geçen `ssc` komutunu söyler
(sofascore_scraper/cli/removed_flags.py).

Çıkış kodları yeni CLI'nin tablosudur (sofascore_scraper/cli/exit_codes.py): 0 başarı, 2 kullanım ya da yapılandırma
hatası, 3 kısmi başarı, 4 SofaScore engelledi (devre kesici), 5 depolama hatası, 6 veri dizini başka bir sürecin
kilidinde, 130 / 143 Ctrl+C / SIGTERM ile iptal.
"""

import sys
from typing import Optional, Sequence

from sofascore_scraper.cli import LEGACY_PROG as PROG


def main(argv: Optional[Sequence[str]] = None) -> int:
    """`ssc`yi bu süreçte çalıştırır; yardım ve hata metinlerinde komut adı `python main.py`dir."""
    from sofascore_scraper.cli.main import main as cli_main

    return int(cli_main(None if argv is None else list(argv), prog=PROG))


if __name__ == "__main__":
    sys.exit(main())
