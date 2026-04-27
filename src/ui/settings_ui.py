"""
SofaScore Scraper için ayarlar işlemleri modülü.
Bu modül, yapılandırma, renk teması ve sistem ayarları gibi işlemleri içerir.
"""

import os
import json
import shutil
from typing import Dict, Any, Optional, List
from colorama import Fore, Style

from src.config_manager import ConfigManager
from src.logger import get_logger
from src.i18n import get_i18n

# Logger'ı al
logger = get_logger("SettingsUI")


class SettingsMenuHandler:
    """Ayarlar menü işlemleri sınıfı."""
    
    def __init__(
        self, 
        config_manager: ConfigManager,
        data_dir: str,
        colors: Dict[str, str]
    ):
        """
        SettingsMenuHandler sınıfını başlatır.
        
        Args:
            config_manager: Konfigürasyon yöneticisi
            data_dir: Veri dizini
            colors: Renk tanımlamaları sözlüğü
        """
        self.config_manager = config_manager
        self.data_dir = data_dir
        self.colors = colors
        self.i18n = get_i18n()
    
    def edit_config(self) -> None:
        """Yapılandırma dosyasını düzenler."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('settings_title')}")
            print("-" * 50)
            print(self.i18n.t('edit_api_config'))
            print(self.i18n.t('change_data_dir'))
            print(self.i18n.t('edit_display_settings'))
            print(self.i18n.t('change_language_option'))
            
            choice = input(f"\n{self.i18n.t('settings_option_prompt')} ").strip()
            
            if choice == "1":
                self._edit_api_config()
            elif choice == "2":
                self._change_data_directory()
            elif choice == "3":
                self._edit_display_settings()
            elif choice == "4":
                self._change_language()
            else:
                print(f"\n{COLORS['WARNING']}{self.i18n.t('error_invalid_option')}")
                
        except Exception as e:
            logger.error(f"Yapılandırma düzenlenirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def _edit_api_config(self) -> None:
        """API yapılandırmasını düzenler."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('api_config_title')}")
            print("-" * 50)
            
            # Mevcut değerleri ConfigManager'dan al
            base_url = self.config_manager.get_api_base_url()
            request_timeout = os.getenv("REQUEST_TIMEOUT", "30")
            max_retries = os.getenv("MAX_RETRIES", "3")
            use_proxy = self.config_manager.get_use_proxy()
            proxy_url = self.config_manager.get_proxy_url()
            max_concurrent = os.getenv("MAX_CONCURRENT", "25")
            
            # Mevcut yapılandırmayı göster
            print(f"{COLORS['INFO']}{self.i18n.t('current_config')}")
            print(f"  {self.i18n.t('base_url')} {COLORS['SUCCESS']}{base_url}")
            print(f"  {self.i18n.t('request_timeout')} {COLORS['SUCCESS']}{request_timeout} saniye")
            print(f"  {self.i18n.t('max_retries')} {COLORS['SUCCESS']}{max_retries}")
            print(f"  {self.i18n.t('max_concurrent')} {COLORS['SUCCESS']}{max_concurrent}")
            print(f"  {self.i18n.t('use_proxy')} {COLORS['SUCCESS']}{use_proxy}")
            if use_proxy:
                print(f"  {self.i18n.t('proxy_url')} {COLORS['SUCCESS']}{proxy_url}")
            
            # Yeni değerleri al
            print(f"\n{COLORS['INFO']}{self.i18n.t('enter_new_values_prompt')}")
            
            # Ana API Ayarları
            print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('main_api_settings')}")
            new_base_url = input(f"Base URL [{base_url}]: ").strip() or base_url
            
            # Performans Ayarları
            print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('performance_settings')}")
            try:
                new_request_timeout = input(f"{self.i18n.t('request_timeout')} [{request_timeout}]: ").strip()
                new_request_timeout = new_request_timeout if new_request_timeout else request_timeout
            except ValueError:
                print(f"{COLORS['WARNING']}{self.i18n.t('warning_invalid_value_default')}{request_timeout}")  # TODO: Buna da key eklenebilir ama user onayiyla atliyorum
                new_request_timeout = request_timeout

            try:
                new_max_retries = input(f"{self.i18n.t('max_retries')} [{max_retries}]: ").strip()
                new_max_retries = new_max_retries if new_max_retries else max_retries
            except ValueError:
                print(f"{COLORS['WARNING']}{self.i18n.t('warning_invalid_value_default')}{max_retries}")
                new_max_retries = max_retries
                
            try:
                new_max_concurrent = input(f"{self.i18n.t('max_concurrent')} [{max_concurrent}]: ").strip() 
                new_max_concurrent = new_max_concurrent if new_max_concurrent else max_concurrent
            except ValueError:
                print(f"{COLORS['WARNING']}{self.i18n.t('warning_invalid_value_default')}{max_concurrent}")
                new_max_concurrent = max_concurrent

            # Proxy Ayarları
            print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('proxy_settings')}")
            use_proxy_input = input(f"{self.i18n.t('use_proxy')} (e/h) [{use_proxy}]: ").strip().lower()
            if use_proxy_input:
                new_use_proxy = use_proxy_input in ["e", "evet", "y", "yes", "true", "1"]
            else:
                new_use_proxy = use_proxy
            
            new_proxy_url = ""
            if new_use_proxy:
                new_proxy_url = input(f"{self.i18n.t('proxy_url')} [{proxy_url}]: ").strip() or proxy_url
            
            # Çevre değişkenlerini güncelle
            success1 = self.config_manager.update_env_variable("API_BASE_URL", new_base_url)
            success2 = self.config_manager.update_env_variable("REQUEST_TIMEOUT", new_request_timeout)
            success3 = self.config_manager.update_env_variable("MAX_RETRIES", new_max_retries)
            success4 = self.config_manager.update_env_variable("MAX_CONCURRENT", new_max_concurrent)
            success5 = self.config_manager.update_env_variable("USE_PROXY", str(new_use_proxy).lower())
            success6 = self.config_manager.update_env_variable("PROXY_URL", new_proxy_url if new_use_proxy else "")
            
            if success1 and success2 and success3 and success4 and success5 and success6:
                print(f"\n{COLORS['SUCCESS']}✅ {self.i18n.t('api_config_updated')}")
                print(f"{COLORS['INFO']}ℹ️ {self.i18n.t('settings_applied_runtime')}")
            else:
                print(f"\n{COLORS['WARNING']}{self.i18n.t('error_update_api_config')}")
                
        except Exception as e:
            logger.error(f"API yapılandırması düzenlenirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
            
    def _change_data_directory(self) -> None:
        """Veri dizinini değiştirir."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('data_dir_change_title')}")
            print("-" * 50)
            
            # Mevcut veri dizinini göster
            current_data_dir = self.data_dir
            print(f"{COLORS['INFO']}{self.i18n.t('current_data_dir')} {COLORS['SUCCESS']}{current_data_dir}")
            
            # Yeni veri dizinini al
            new_data_dir = input(f"\n{self.i18n.t('new_data_dir_prompt')} [{current_data_dir}]: ").strip() or current_data_dir
            
            # Aynı dizin ise işlem yapma
            if new_data_dir == current_data_dir:
                print(f"\n{COLORS['INFO']}{self.i18n.t('data_dir_not_changed')}")
                return
            
            # Yeni dizini oluştur
            os.makedirs(new_data_dir, exist_ok=True)
            
            # Verileri taşımak isteyip istemediğini sor
            move_data = input(f"\n{self.i18n.t('move_data_prompt')} ").strip().lower()
            
            if move_data in ["e", "evet", "y", "yes", "true", "1"]:
                print(f"\n{COLORS['INFO']}{self.i18n.t('moving_data')}")
                
                # Alt dizinleri oluştur
                os.makedirs(os.path.join(new_data_dir, "seasons"), exist_ok=True)
                os.makedirs(os.path.join(new_data_dir, "matches"), exist_ok=True)
                os.makedirs(os.path.join(new_data_dir, "match_details"), exist_ok=True)
                os.makedirs(os.path.join(new_data_dir, "datasets"), exist_ok=True)
                os.makedirs(os.path.join(new_data_dir, "reports"), exist_ok=True)
                
                # Verileri taşı
                self._move_directory_contents(os.path.join(current_data_dir, "seasons"), os.path.join(new_data_dir, "seasons"))
                self._move_directory_contents(os.path.join(current_data_dir, "matches"), os.path.join(new_data_dir, "matches"))
                self._move_directory_contents(os.path.join(current_data_dir, "match_details"), os.path.join(new_data_dir, "match_details"))
                self._move_directory_contents(os.path.join(current_data_dir, "datasets"), os.path.join(new_data_dir, "datasets"))
                self._move_directory_contents(os.path.join(current_data_dir, "reports"), os.path.join(new_data_dir, "reports"))
                
                print(f"\n{COLORS['SUCCESS']}✅ {self.i18n.t('data_moved_success')}")
            
            # Çevre değişkenini güncelle
            success = self.config_manager.update_env_variable("DATA_DIR", new_data_dir)
            
            if success:
                print(f"\n{COLORS['SUCCESS']}✅ {self.i18n.t('data_dir_updated_success')}")
                print(f"{COLORS['INFO']}ℹ️ {self.i18n.t('restart_required')}")
            else:
                print(f"\n{COLORS['WARNING']}{self.i18n.t('error_update_data_dir')}")
                
        except Exception as e:
            logger.error(f"Veri dizini değiştirilirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def _edit_display_settings(self) -> None:
        """Görüntüleme ayarlarını düzenler."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('display_settings_title')}")
            print("-" * 50)
            
            # Mevcut değerleri ConfigManager'dan al
            use_color = self.config_manager.get_use_color()
            date_format = self.config_manager.get_date_format()
            
            # Mevcut yapılandırmayı göster
            print(f"{COLORS['INFO']}{self.i18n.t('current_config')}")
            print(f"  {self.i18n.t('use_color')} {COLORS['SUCCESS']}{use_color}")
            print(f"  {self.i18n.t('date_format')} {COLORS['SUCCESS']}{date_format}")
            
            # Yeni değerleri al
            print(f"\n{COLORS['INFO']}{self.i18n.t('enter_new_values_prompt')}")
            
            use_color_input = input(f"{self.i18n.t('use_color')} (e/h) [{use_color}]: ").strip().lower()
            if use_color_input:
                new_use_color = use_color_input in ["e", "evet", "y", "yes", "true", "1"]
            else:
                new_use_color = use_color
            
            new_date_format = input(f"{self.i18n.t('date_format')} [{date_format}]: ").strip() or date_format
            
            # Çevre değişkenlerini güncelle
            success1 = self.config_manager.update_env_variable("USE_COLOR", str(new_use_color).lower())
            success2 = self.config_manager.update_env_variable("DATE_FORMAT", new_date_format)
            
            if success1 and success2:
                print(f"\n{COLORS['SUCCESS']}✅ {self.i18n.t('display_settings_updated')}")
                print(f"{COLORS['INFO']}ℹ️ {self.i18n.t('settings_applied_runtime')}")
            else:
                print(f"\n{COLORS['WARNING']}{self.i18n.t('error_update_display_settings')}")
                
        except Exception as e:
            logger.error(f"Görüntüleme ayarları düzenlenirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")

    def _change_language(self) -> None:
        """Dil değiştirme işlemi."""
        COLORS = self.colors
        
        try:
            print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('language_selection_title')}")
            print("-" * 50)
            
            current_lang = self.config_manager.get_language()
            print(f"{COLORS['INFO']}{self.i18n.t('current_language')} {COLORS['SUCCESS']}{current_lang}")
            
            print(f"\n{self.i18n.t('menu_turkish')}")
            print("2. 🇬🇧 English (en)")
            
            choice = input(f"\n{self.i18n.t('language_choice_prompt')} ").strip()
            
            new_lang = None
            if choice == "1":
                new_lang = "tr"
            elif choice == "2":
                new_lang = "en"
            else:
                print(f"\n{COLORS['WARNING']}❌ {self.i18n.t('invalid_choice_bilingual')}")
                return
            
            if new_lang == current_lang:
                print(f"\n{COLORS['INFO']}{self.i18n.t('language_already_set', new_lang=new_lang)}")
                return

            if self.config_manager.set_language(new_lang):
                from src.i18n import get_i18n
                get_i18n().set_language(new_lang)
                print(f"\n{COLORS['SUCCESS']}✅ {self.i18n.t('language_changed_success')} {new_lang}")
                print(f"{COLORS['INFO']}ℹ️ {self.i18n.t('restart_required')}")
            else:
                 print(f"\n{COLORS['WARNING']}❌ {self.i18n.t('failed_to_change_language')}")

        except Exception as e:
            logger.error(f"Dil değiştirilirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def backup_data(self) -> None:
        """Veri yedekleme işlemleri."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}Veri Yedekleme:")
            print("-" * 50)
            print(f"{self.i18n.t('menu_backup_all')}")
            print(f"{self.i18n.t('menu_backup_selected')}")
            
            choice = input(f"\n{self.i18n.t('prompt_option_1_2')}")
            
            if choice == "1":
                self._backup_all_data()
            elif choice == "2":
                self._backup_selected_data()
            else:
                print(f"\n{COLORS['WARNING']}{self.i18n.t('error_invalid_option')}")
                
        except Exception as e:
            logger.error(f"Veri yedekleme işlemi sırasında hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def _backup_all_data(self) -> None:
        """Tüm verileri yedekler."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('title_backup_all')}")
            print("-" * 50)
            
            # Yedekleme dizinini al
            backup_dir = input(f"Yedekleme Dizini [backup]: ").strip() or "backup"
            
            # Yedekleme dizinini oluştur
            os.makedirs(backup_dir, exist_ok=True)
            
            # Alt dizinleri oluştur
            os.makedirs(os.path.join(backup_dir, "config"), exist_ok=True)
            os.makedirs(os.path.join(backup_dir, "data"), exist_ok=True)
            
            # Yapılandırma dosyalarını yedekle
            print(f"\n{COLORS['INFO']}{self.i18n.t('info_backing_up_config')}")
            
            # leagues.txt dosyasını yedekle
            league_file = self.config_manager.league_config_path
            if os.path.exists(league_file):
                league_backup = os.path.join(backup_dir, "config", os.path.basename(league_file))
                shutil.copy2(league_file, league_backup)
                print(f"{COLORS['SUCCESS']}{self.i18n.t('success_backup_league_config')}{league_backup}")
            
            # .env dosyasını yedekle
            env_file = os.path.join(os.getcwd(), ".env")
            if os.path.exists(env_file):
                env_backup = os.path.join(backup_dir, "config", ".env")
                shutil.copy2(env_file, env_backup)
                print(f"{COLORS['SUCCESS']}{self.i18n.t('success_backup_env_vars')}{env_backup}")
            
            # Veri dizinini yedekle
            print(f"\n{COLORS['INFO']}Veri dizini yedekleniyor...")
            
            # Alt dizinleri yedekle
            for subdir in ["seasons", "matches", "match_details"]:
                src_dir = os.path.join(self.data_dir, subdir)
                if os.path.exists(src_dir) and os.path.isdir(src_dir):
                    dst_dir = os.path.join(backup_dir, "data", subdir)
                    
                    # Dizini kopyala
                    print(f"{COLORS['INFO']}'{subdir}' dizini yedekleniyor...")
                    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
                    print(f"{COLORS['SUCCESS']}✓ '{subdir}' dizini yedeklendi: {dst_dir}")
            
            print(f"\n{COLORS['SUCCESS']}{self.i18n.t('success_backup_all')}{os.path.abspath(backup_dir)}")
            
        except Exception as e:
            logger.error(f"Veri yedeklenirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def _backup_selected_data(self) -> None:
        """Seçili veri tiplerini yedekler."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('title_backup_selected')}")
            print("-" * 50)
            
            # Yedekleme dizinini al
            backup_dir = input(f"Yedekleme Dizini [backup]: ").strip() or "backup"
            
            # Yedekleme dizinini oluştur
            os.makedirs(backup_dir, exist_ok=True)
            
            # Yedeklenecek veri tiplerini seç
            print(f"\n{COLORS['INFO']}{self.i18n.t('prompt_select_backup_types')}")
            print(f"{self.i18n.t('menu_config_files')}")
            print("2. 🏆 Lig ve Sezon Verileri")
            print(f"{self.i18n.t('menu_match_data')}")
            print(f"{self.i18n.t('menu_match_details')}")
            
            choices = input(f"\n{self.i18n.t('prompt_selections')}").strip()
            selected = [int(c.strip()) for c in choices.split(",") if c.strip().isdigit()]
            
            # Seçim yoksa çık
            if not selected:
                print(f"\n{COLORS['WARNING']}{self.i18n.t('warning_no_data_type_selected')}")
                return
            
            # Yapılandırma dosyalarını yedekle
            if 1 in selected:
                print(f"\n{COLORS['INFO']}{self.i18n.t('info_backing_up_config')}")
                os.makedirs(os.path.join(backup_dir, "config"), exist_ok=True)
                
                # leagues.txt dosyasını yedekle
                league_file = self.config_manager.league_config_path
                if os.path.exists(league_file):
                    league_backup = os.path.join(backup_dir, "config", os.path.basename(league_file))
                    shutil.copy2(league_file, league_backup)
                    print(f"{COLORS['SUCCESS']}{self.i18n.t('success_backup_league_config')}{league_backup}")
                
                # .env dosyasını yedekle
                env_file = os.path.join(os.getcwd(), ".env")
                if os.path.exists(env_file):
                    env_backup = os.path.join(backup_dir, "config", ".env")
                    shutil.copy2(env_file, env_backup)
                    print(f"{COLORS['SUCCESS']}{self.i18n.t('success_backup_env_vars')}{env_backup}")
            
            # Veri dizinini oluştur
            os.makedirs(os.path.join(backup_dir, "data"), exist_ok=True)
            
            # Lig ve sezon verilerini yedekle
            if 2 in selected:
                print(f"\n{COLORS['INFO']}Lig ve sezon verileri yedekleniyor...")
                src_dir = os.path.join(self.data_dir, "seasons")
                if os.path.exists(src_dir) and os.path.isdir(src_dir):
                    dst_dir = os.path.join(backup_dir, "data", "seasons")
                    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
                    print(f"{COLORS['SUCCESS']}✓ Sezon verileri yedeklendi: {dst_dir}")
            
            # Maç verilerini yedekle
            if 3 in selected:
                print(f"\n{COLORS['INFO']}{self.i18n.t('info_backing_up_match_data')}")
                src_dir = os.path.join(self.data_dir, "matches")
                if os.path.exists(src_dir) and os.path.isdir(src_dir):
                    dst_dir = os.path.join(backup_dir, "data", "matches")
                    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
                    print(f"{COLORS['SUCCESS']}{self.i18n.t('success_backup_match_data')}{dst_dir}")
            
            # Maç detaylarını yedekle
            if 4 in selected:
                print(f"\n{COLORS['INFO']}{self.i18n.t('info_backing_up_match_details')}")
                src_dir = os.path.join(self.data_dir, "match_details")
                if os.path.exists(src_dir) and os.path.isdir(src_dir):
                    dst_dir = os.path.join(backup_dir, "data", "match_details")
                    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
                    print(f"{COLORS['SUCCESS']}{self.i18n.t('success_backup_match_details')}{dst_dir}")
            
            print(f"\n{COLORS['SUCCESS']}{self.i18n.t('success_backup_selected')}{os.path.abspath(backup_dir)}")
            
        except Exception as e:
            logger.error(f"Veri yedeklenirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def restore_data(self) -> None:
        """Veri geri yükleme işlemleri."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('title_restore_data')}")
            print("-" * 50)
            
            # Yedek dizinini al
            backup_dir = input(f"Yedek Dizini [backup]: ").strip() or "backup"
            
            # Yedek dizini kontrol et
            if not os.path.exists(backup_dir) or not os.path.isdir(backup_dir):
                print(f"\n{COLORS['WARNING']}{self.i18n.t('warning_backup_dir_not_found')}{backup_dir}")
                return
            
            # Geri yüklenecek veri tiplerini seç
            print(f"\n{COLORS['INFO']}{self.i18n.t('prompt_select_restore_types')}")
            print(f"{self.i18n.t('menu_config_files')}")
            print("2. 🏆 Lig ve Sezon Verileri")
            print(f"{self.i18n.t('menu_match_data')}")
            print(f"{self.i18n.t('menu_match_details')}")
            
            choices = input(f"\n{self.i18n.t('prompt_selections')}").strip()
            selected = [int(c.strip()) for c in choices.split(",") if c.strip().isdigit()]
            
            # Seçim yoksa çık
            if not selected:
                print(f"\n{COLORS['WARNING']}{self.i18n.t('warning_no_data_type_selected')}")
                return
            
            # Yapılandırma dosyalarını geri yükle
            if 1 in selected:
                print(f"\n{COLORS['INFO']}{self.i18n.t('info_restoring_config_files')}")
                
                # leagues.txt dosyasını geri yükle
                league_file = os.path.join(backup_dir, "config", os.path.basename(self.config_manager.league_config_path))
                if os.path.exists(league_file):
                    shutil.copy2(league_file, self.config_manager.league_config_path)
                    print(f"{COLORS['SUCCESS']}{self.i18n.t('success_restore_league_config')}{self.config_manager.league_config_path}")
                
                # .env dosyasını geri yükle
                env_file = os.path.join(backup_dir, "config", ".env")
                if os.path.exists(env_file):
                    shutil.copy2(env_file, os.path.join(os.getcwd(), ".env"))
                    print(f"{COLORS['SUCCESS']}{self.i18n.t('success_restore_env_vars')}")
            
            # Lig ve sezon verilerini geri yükle
            if 2 in selected:
                print(f"\n{COLORS['INFO']}{self.i18n.t('info_restoring_league_season_data')}")
                src_dir = os.path.join(backup_dir, "data", "seasons")
                if os.path.exists(src_dir) and os.path.isdir(src_dir):
                    dst_dir = os.path.join(self.data_dir, "seasons")
                    os.makedirs(dst_dir, exist_ok=True)
                    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
                    print(f"{COLORS['SUCCESS']}{self.i18n.t('success_restore_season_data')}{dst_dir}")
            
            # Maç verilerini geri yükle
            if 3 in selected:
                print(f"\n{COLORS['INFO']}{self.i18n.t('info_restoring_match_data')}")
                src_dir = os.path.join(backup_dir, "data", "matches")
                if os.path.exists(src_dir) and os.path.isdir(src_dir):
                    dst_dir = os.path.join(self.data_dir, "matches")
                    os.makedirs(dst_dir, exist_ok=True)
                    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
                    print(f"{COLORS['SUCCESS']}{self.i18n.t('success_restore_match_data')}{dst_dir}")
            
            # Maç detaylarını geri yükle
            if 4 in selected:
                print(f"\n{COLORS['INFO']}{self.i18n.t('info_restoring_match_details')}")
                src_dir = os.path.join(backup_dir, "data", "match_details")
                if os.path.exists(src_dir) and os.path.isdir(src_dir):
                    dst_dir = os.path.join(self.data_dir, "match_details")
                    os.makedirs(dst_dir, exist_ok=True)
                    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
                    print(f"{COLORS['SUCCESS']}{self.i18n.t('success_restore_match_details')}{dst_dir}")
            
            # Yapılandırmayı yeniden yükle
            if 1 in selected:
                success = self.config_manager.reload_config()
                if success:
                    print(f"{COLORS['SUCCESS']}{self.i18n.t('success_config_reloaded')}")
                else:
                    print(f"{COLORS['WARNING']}{self.i18n.t('warning_config_reload_failed')}")
            
            print(f"\n{COLORS['SUCCESS']}{self.i18n.t('success_restore_selected')}")
            print(f"{COLORS['INFO']}{self.i18n.t('info_restart_required')}")
            
        except Exception as e:
            logger.error(f"Veri geri yüklenirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def clear_data(self) -> None:
        """Veri temizleme işlemleri."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}Veri Temizleme:")
            print("-" * 50)
            print(f"{self.i18n.t('menu_clear_all')}")
            print(f"{self.i18n.t('menu_clear_selected')}")
            
            choice = input(f"\n{self.i18n.t('prompt_option_1_2')}")
            
            if choice == "1":
                self._clear_all_data()
            elif choice == "2":
                self._clear_selected_data()
            else:
                print(f"\n{COLORS['WARNING']}{self.i18n.t('error_invalid_option')}")
                
        except Exception as e:
            logger.error(f"Veri temizleme işlemi sırasında hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def _clear_all_data(self) -> None:
        """Tüm verileri temizler."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            # Onay al
            confirm = input(f"\n{COLORS['WARNING']}{self.i18n.t('warning_clear_all_confirm')}").strip().lower()
            
            if confirm not in ["e", "evet", "y", "yes", "true", "1"]:
                print(f"\n{COLORS['INFO']}{self.i18n.t('info_clear_cancelled')}")
                return
            
            # İkinci onay
            confirm2 = input(f"\n{COLORS['WARNING']}{self.i18n.t('warning_clear_all_final')}").strip().lower()
            
            if confirm2 not in ["e", "evet", "y", "yes", "true", "1"]:
                print(f"\n{COLORS['INFO']}{self.i18n.t('info_clear_cancelled')}")
                return
            
            # Veri dizinlerini temizle
            data_dirs = ["seasons", "matches", "match_details", "datasets", "reports"]
            
            for dir_name in data_dirs:
                dir_path = os.path.join(self.data_dir, dir_name)
                if os.path.exists(dir_path):
                    for item in os.listdir(dir_path):
                        item_path = os.path.join(dir_path, item)
                        if os.path.isdir(item_path):
                            shutil.rmtree(item_path)
                        else:
                            os.remove(item_path)
                    print(f"{COLORS['SUCCESS']}✓ {dir_name} dizini temizlendi.")
            
            print(f"\n{COLORS['SUCCESS']}{self.i18n.t('success_clear_all')}")
            
        except Exception as e:
            logger.error(f"Tüm veriler temizlenirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def _clear_selected_data(self) -> None:
        """Seçili veri türlerini temizler."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('title_clear_selected')}")
            print("-" * 50)
            print("1. 📅 Sezon Verileri")
            print(f"{self.i18n.t('menu_clear_match_data')}")
            print(f"{self.i18n.t('menu_clear_match_details')}")
            print("4. 📊 CSV Veri Setleri")
            print("5. 📝 Raporlar")
            
            data_types = []
            selections = input(f"\n{self.i18n.t('prompt_selections_comma')}").strip()
            
            dir_mapping = {
                "1": "seasons",
                "2": "matches",
                "3": "match_details",
                "4": "datasets",
                "5": "reports"
            }
            
            for selection in selections.split(","):
                selection = selection.strip()
                if selection in dir_mapping:
                    data_types.append(dir_mapping[selection])
            
            if not data_types:
                print(f"\n{COLORS['WARNING']}{self.i18n.t('error_no_data_type_selected')}")
                return
            
            # Onay al
            confirm = input(f"\n{COLORS['WARNING']}{self.i18n.t('warning_clear_selected_confirm')}").strip().lower()
            
            if confirm not in ["e", "evet", "y", "yes", "true", "1"]:
                print(f"\n{COLORS['INFO']}{self.i18n.t('info_clear_cancelled')}")
                return
            
            # Seçili dizinleri temizle
            for dir_name in data_types:
                dir_path = os.path.join(self.data_dir, dir_name)
                if os.path.exists(dir_path):
                    for item in os.listdir(dir_path):
                        item_path = os.path.join(dir_path, item)
                        if os.path.isdir(item_path):
                            shutil.rmtree(item_path)
                        else:
                            os.remove(item_path)
                    print(f"{COLORS['SUCCESS']}✓ {dir_name} dizini temizlendi.")
            
            print(f"\n{COLORS['SUCCESS']}{self.i18n.t('success_clear_selected')}")
            
        except Exception as e:
            logger.error(f"Seçili veri türleri temizlenirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def show_about(self) -> None:
        """Program hakkında bilgi gösterir."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('about_title')}")
            print("-" * 50)
            print(f"{COLORS['INFO']}{self.i18n.t('version')} {COLORS['SUCCESS']}1.0.0")
            print(f"{COLORS['INFO']}{self.i18n.t('developer')} {COLORS['SUCCESS']}SofaScore Scraper Ekibi")
            print(f"{COLORS['INFO']}{self.i18n.t('license')} {COLORS['SUCCESS']}MIT")
            print(f"{COLORS['INFO']}{self.i18n.t('description')} {COLORS['SUCCESS']}{self.i18n.t('app_description')}")
            
            print(f"\n{COLORS['SUBTITLE']}{self.i18n.t('libraries')}")
            print(f"{COLORS['INFO']}{self.i18n.t('dependency_requests')}")
            print(f"{COLORS['INFO']}{self.i18n.t('dependency_colorama')}")
            print(f"{COLORS['INFO']}{self.i18n.t('dependency_pandas')}")
            
        except Exception as e:
            logger.error(f"Hakkında bilgisi görüntülenirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def _move_directory_contents(self, src_dir: str, dest_dir: str) -> None:
        """Bir dizinin içeriğini başka bir dizine taşır."""
        if not os.path.exists(src_dir):
            return
        
        # Hedef dizini oluştur
        os.makedirs(dest_dir, exist_ok=True)
        
        # İçeriği taşı
        for item in os.listdir(src_dir):
            src_path = os.path.join(src_dir, item)
            dest_path = os.path.join(dest_dir, item)
            
            if os.path.isdir(src_path):
                # Dizin varsa, rekürsif olarak taşı
                if os.path.exists(dest_path):
                    # Hedef dizin varsa, içeriğini taşı
                    self._move_directory_contents(src_path, dest_path)
                else:
                    # Dizin yoksa, direkt kopyala
                    shutil.copytree(src_path, dest_path)
            else:
                # Dosyayı kopyala
                shutil.copy2(src_path, dest_path) 