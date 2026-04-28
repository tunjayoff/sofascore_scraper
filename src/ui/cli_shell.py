"""
Terminal menü çerçevesi — çizgili başlık, durum özeti, numaralı seçenekler, prompt.
"""

from __future__ import annotations

import os
import platform
from typing import Dict, List, Sequence, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    pass


class CliShell:
    """Tek tip CLI menü çizimi ve kullanıcı girdisi."""

    VERSION = "1.0.0"

    def __init__(self, colors: Dict[str, str], i18n) -> None:
        self.c = colors
        self.i18n = i18n
        try:
            self.width = max(48, min(72, int(os.get_terminal_size().columns) - 2))
        except OSError:
            self.width = 58

    def _r(self) -> str:
        return self.c.get("RESET", "")

    def clear(self) -> None:
        if platform.system() == "Windows":
            os.system("cls")
        else:
            os.system("clear")

    def rule(self, char: str = "─") -> None:
        line = char * self.width
        dim = self.c.get("DIM", "")
        print(f"{dim}{line}{self._r()}")

    def breadcrumb(self, *parts: str) -> None:
        """İnce breadcrumb (ör. Ana › Lig)."""
        if not parts:
            return
        sep = f"{self.c.get('DIM', '')} › {self._r()}"
        body = sep.join(parts)
        print(f"\n{self.c.get('DIM', '')}{body}{self._r()}")

    def app_header(self) -> None:
        print(f"\n{self.c['TITLE']}  SofaScore Scraper{self._r()} {self.c.get('DIM', '')}v{self.VERSION}{self._r()}")
        self.rule("═")

    def status_summary(self, league_count: int, season_files: int) -> None:
        print(f"\n{self.c['SUBTITLE']}{self.i18n.t('system_status')}{self._r()}")
        self.rule("┄")
        lg = self.i18n.t("configured_leagues").rstrip(":")
        sn = self.i18n.t("loaded_seasons").rstrip(":")
        n = self.c.get("TITLE", "")
        ok = self.c.get("SUCCESS", "")
        print(
            f"  {ok}●{self._r()} {lg}  {n}{league_count}{self._r()}    "
            f"{ok}●{self._r()} {sn}  {n}{season_files}{self._r()}"
        )

    def section_title(self, text: str) -> None:
        print(f"\n{self.c['TITLE']}{text}{self._r()}")
        self.rule("─")

    def menu_options(self, entries: Sequence[Tuple[str, str]], *, back_key: str, back_label: str) -> None:
        """
        entries: (tuş, i18n_anahtar) veya etiket zaten çevrilmişse özel anahtar ile.
        """
        for key, label_ref in entries:
            label = self.i18n.t(label_ref)
            print(f"  {self.c.get('INFO', '')}[{key}]{self._r()}  {label}")
        print(f"  {self.c['WARNING']}[{back_key}]{self._r()}  {back_label}")

    def ask(self, prompt_key: str = "selection_prompt", **fmt: str) -> str:
        tpl = self.i18n.t(prompt_key, **fmt) if fmt else self.i18n.t(prompt_key)
        return input(f"\n{self.c.get('TITLE', '')}{tpl} {self._r()}").strip()

    def pause(self) -> None:
        input(f"{self.c.get('DIM', '')}{self.i18n.t('press_enter_to_continue')}{self._r()}")

    def invalid_choice(self) -> None:
        msg = f"{self.i18n.t('invalid_choice_error')} {self.i18n.t('press_enter_to_continue')}"
        input(f"{self.c['WARNING']}{msg}{self._r()}")


# Ana menü seçenekleri (tuş, i18n anahtarı) — tek yerde tanımlı
MAIN_MENU_ITEMS: List[Tuple[str, str]] = [
    ("1", "menu_league_management"),
    ("2", "menu_season_data"),
    ("3", "menu_match_data"),
    ("4", "menu_match_details"),
    ("5", "menu_stats"),
    ("6", "menu_settings"),
]

LEAGUE_MENU_ITEMS: List[Tuple[str, str]] = [
    ("1", "submenu_league_list"),
    ("2", "submenu_league_add"),
    ("3", "submenu_league_reload"),
    ("4", "submenu_league_search"),
]

SEASON_MENU_ITEMS: List[Tuple[str, str]] = [
    ("1", "submenu_season_update_all"),
    ("2", "submenu_season_update_one"),
    ("3", "submenu_season_list"),
]

MATCH_MENU_ITEMS: List[Tuple[str, str]] = [
    ("1", "submenu_match_fetch_one"),
    ("2", "submenu_match_fetch_all"),
    ("3", "submenu_match_list"),
]

MATCH_DATA_MENU_ITEMS: List[Tuple[str, str]] = [
    ("1", "submenu_match_details_fetch_one"),
    ("2", "submenu_match_details_fetch_all"),
    ("3", "submenu_match_details_csv"),
]

STATS_MENU_ITEMS: List[Tuple[str, str]] = [
    ("1", "submenu_stats_system"),
    ("2", "submenu_stats_league"),
    ("3", "submenu_stats_report"),
]

SETTINGS_MENU_ITEMS: List[Tuple[str, str]] = [
    ("1", "submenu_settings_config"),
    ("2", "submenu_settings_backup"),
    ("3", "submenu_settings_restore"),
    ("4", "submenu_settings_clean"),
    ("5", "submenu_settings_about"),
]
