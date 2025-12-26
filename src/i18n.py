import json
import os
from typing import Dict, Optional
from pathlib import Path
from src.logger import get_logger

logger = get_logger("I18n")

class I18nManager:
    """
    Manages internationalization (i18n) and localization (l10n).
    Loads JSON based locale files and provides string retrieval.
    """
    
    def __init__(self, locale_dir: str = "locales", default_lang: str = "tr"):
        self.locale_dir = locale_dir
        # Try to load from env var first (set by ConfigManager)
        self.current_lang = os.getenv("LANGUAGE", default_lang)
        self.translations: Dict[str, Dict[str, str]] = {}
        self._load_locales()

    def _load_locales(self):
        """Loads all JSON files from the locales directory."""
        if not os.path.exists(self.locale_dir):
            os.makedirs(self.locale_dir, exist_ok=True)
            logger.warning(f"Locale directory created: {self.locale_dir}")
            return

        for filename in os.listdir(self.locale_dir):
            if filename.endswith(".json"):
                lang_code = filename.split(".")[0]
                try:
                    with open(os.path.join(self.locale_dir, filename), "r", encoding="utf-8") as f:
                        self.translations[lang_code] = json.load(f)
                    logger.debug(f"Loaded locale: {lang_code}")
                except Exception as e:
                    logger.error(f"Failed to load locale {filename}: {e}")

    def set_language(self, lang_code: str):
        """Sets the current language."""
        if lang_code in self.translations:
            self.current_lang = lang_code
            logger.info(f"Language set to: {lang_code}")
        else:
            logger.warning(f"Language {lang_code} not found, falling back to {self.current_lang}")
            # Try to load if it exists but wasn't loaded (e.g. added runtime)
            self._load_locales()
            if lang_code in self.translations:
                self.current_lang = lang_code

    def t(self, key: str, **kwargs) -> str:
        """
        Retrieves a translated string by key.
        Supports formatting with kwargs.
        """
        lang_data = self.translations.get(self.current_lang, {})
        text = lang_data.get(key)
        
        # Fallback to default language if key not found
        if text is None and self.current_lang != "tr":
             text = self.translations.get("tr", {}).get(key)

        if text is None:
            return key # Return key if translation missing

        try:
            return text.format(**kwargs)
        except KeyError as e:
            logger.error(f"Missing format key for '{key}': {e}")
            return text

# Global instance
_i18n_instance = None

def get_i18n(locale_dir: str = "locales") -> I18nManager:
    global _i18n_instance
    if _i18n_instance is None:
        _i18n_instance = I18nManager(locale_dir=locale_dir)
    return _i18n_instance
