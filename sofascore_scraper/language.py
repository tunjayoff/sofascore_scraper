"""
Arayüz dili için tek kural. CLI, `doctor`, başlatıcı (scripts/start_web.py) ve web sunucusu
bunu kullanır; kurulum betikleri (scripts/install.sh, install.ps1) ve web arayüzü
(frontend/src/i18n.ts) aynı kuralı kendi dillerinde uygular:

    açık ayar  >  algılanan sistem dili  >  İngilizce

  açık ayar     `display.language`: SOFASCORE_DISPLAY__LANGUAGE (ortam ya da .env), sofascore.toml'daki
                ya da Ayarlar sayfasındaki dil (overrides.json). Uygulama onu ayar yükleyicisinden okur
                (sofascore_scraper/i18n.py); yükleyiciyi yüklemeyen başlatıcı ve başlatma betikleri aynı
                sırayı sofascore_scraper/doctor.py'nin Context'inden alır (FX-22). 2.x'in APP_LANGUAGE ve
                LANGUAGE adları 3.1'de okunmaz (plan maddesi P30).
  sistem dili   POSIX önceliğiyle LC_ALL, LC_MESSAGES, LANG: ilk dolu olan belirler. Hiçbiri
                yoksa Windows'ta kullanıcının arayüz dili.
  İngilizce     desteklenmeyen her dil (de_DE, C, POSIX, ...) için de geçerli.

Yalnızca standart kütüphaneyi kullanır ve Python 3.10'dan eski sürümlerde de içe aktarılabilir:
sofascore_scraper/doctor.py paketler kurulmadan önce de çalışır.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Callable, Mapping, Optional

SUPPORTED_LANGUAGES = ("en", "tr")
DEFAULT_LANGUAGE = "en"

# Açık ayarın ortamdaki adı (sofascore_scraper/config/loader.env_name("display.language"))
EXPLICIT_KEYS = ("SOFASCORE_DISPLAY__LANGUAGE",)
LOCALE_KEYS = ("LC_ALL", "LC_MESSAGES", "LANG")
ENV_KEYS = EXPLICIT_KEYS + LOCALE_KEYS


def language_of(tag: object) -> Optional[str]:
    """Yerel ayar adından dil: "tr_TR.UTF-8", "tr-TR", "TR" -> "tr"; desteklenmeyen ya da boş -> None."""
    code = re.split(r"[_.@-]", str(tag or "").strip().lower(), maxsplit=1)[0]
    return code if code in SUPPORTED_LANGUAGES else None


def explicit_language(environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Kullanıcının açıkça seçtiği dil; seçmediyse (ya da değer desteklenmiyorsa) None."""
    env = os.environ if environ is None else environ
    for key in EXPLICIT_KEYS:
        value = str(env.get(key) or "").strip().lower()
        if value in SUPPORTED_LANGUAGES:
            return value
    return None


def _windows_ui_language() -> Optional[str]:
    """Windows'ta kullanıcının arayüz dili ("tr_TR" gibi); okunamazsa None."""
    try:
        import ctypes
        import locale

        return locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage())  # type: ignore[attr-defined]
    except Exception:
        return None


def detected_language(
    environ: Optional[Mapping[str, str]] = None,
    platform: Optional[str] = None,
    windows_ui_language: Optional[Callable[[], Optional[str]]] = None,
) -> Optional[str]:
    """Sistemin dili, destekleniyorsa; değilse None."""
    env = os.environ if environ is None else environ
    for key in LOCALE_KEYS:
        value = str(env.get(key) or "").strip()
        if value:
            return language_of(value)
    if (platform or sys.platform) == "win32":
        return language_of((windows_ui_language or _windows_ui_language)())
    return None


def resolve_language(
    environ: Optional[Mapping[str, str]] = None,
    platform: Optional[str] = None,
    default: str = DEFAULT_LANGUAGE,
) -> str:
    """Kuralın tamamı: açık ayar > sistem dili > `default` (İngilizce)."""
    return explicit_language(environ) or detected_language(environ, platform) or default


__all__ = [
    "DEFAULT_LANGUAGE",
    "ENV_KEYS",
    "SUPPORTED_LANGUAGES",
    "detected_language",
    "explicit_language",
    "language_of",
    "resolve_language",
]
