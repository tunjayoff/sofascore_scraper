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
    
    def edit_config(self) -> None:
        """Yapılandırma dosyasını düzenler."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}Yapılandırma Düzenleme:")
            print("-" * 50)
            print("1. 🔧 API Yapılandırmasını Düzenle")
            print("2. 📂 Veri Dizinini Değiştir")
            print("3. 📊 Görüntüleme Ayarlarını Düzenle")
            
            choice = input("\nSeçenek (1-3): ")
            
            if choice == "1":
                self._edit_api_config()
            elif choice == "2":
                self._change_data_directory()
            elif choice == "3":
                self._edit_display_settings()
            else:
                print(f"\n{COLORS['WARNING']}❌ Geçersiz seçenek!")
                
        except Exception as e:
            logger.error(f"Yapılandırma düzenlenirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def _edit_api_config(self) -> None:
        """API yapılandırmasını düzenler."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}API Yapılandırması:")
            print("-" * 50)
            
            # Mevcut değerleri ConfigManager'dan al
            base_url = self.config_manager.get_api_base_url()
            request_timeout = os.getenv("REQUEST_TIMEOUT", "30")
            max_retries = os.getenv("MAX_RETRIES", "3")
            use_proxy = self.config_manager.get_use_proxy()
            proxy_url = self.config_manager.get_proxy_url()
            max_concurrent = os.getenv("MAX_CONCURRENT", "25")
            
            # Mevcut yapılandırmayı göster
            print(f"{COLORS['INFO']}Mevcut Yapılandırma:")
            print(f"  Base URL: {COLORS['SUCCESS']}{base_url}")
            print(f"  İstek Zaman Aşımı: {COLORS['SUCCESS']}{request_timeout} saniye")
            print(f"  Yeniden Deneme Sayısı: {COLORS['SUCCESS']}{max_retries}")
            print(f"  Paralel İstek Sayısı: {COLORS['SUCCESS']}{max_concurrent}")
            print(f"  Proxy Kullan: {COLORS['SUCCESS']}{use_proxy}")
            if use_proxy:
                print(f"  Proxy URL: {COLORS['SUCCESS']}{proxy_url}")
            
            # Yeni değerleri al
            print(f"\n{COLORS['INFO']}Yeni değerler için Enter tuşuna basarak mevcut değeri koruyabilirsiniz:")
            
            # Ana API Ayarları
            print(f"\n{COLORS['SUBTITLE']}Ana API Ayarları:")
            new_base_url = input(f"Base URL [{base_url}]: ").strip() or base_url
            
            # Performans Ayarları
            print(f"\n{COLORS['SUBTITLE']}Performans Ayarları:")
            try:
                new_request_timeout = input(f"İstek Zaman Aşımı (saniye) [{request_timeout}]: ").strip()
                new_request_timeout = new_request_timeout if new_request_timeout else request_timeout
            except ValueError:
                print(f"{COLORS['WARNING']}⚠️ Geçersiz değer, varsayılan kullanılıyor: {request_timeout}")
                new_request_timeout = request_timeout

            try:
                new_max_retries = input(f"Yeniden Deneme Sayısı [{max_retries}]: ").strip()
                new_max_retries = new_max_retries if new_max_retries else max_retries
            except ValueError:
                print(f"{COLORS['WARNING']}⚠️ Geçersiz değer, varsayılan kullanılıyor: {max_retries}")
                new_max_retries = max_retries
                
            try:
                new_max_concurrent = input(f"Paralel İstek Sayısı [{max_concurrent}]: ").strip() 
                new_max_concurrent = new_max_concurrent if new_max_concurrent else max_concurrent
            except ValueError:
                print(f"{COLORS['WARNING']}⚠️ Geçersiz değer, varsayılan kullanılıyor: {max_concurrent}")
                new_max_concurrent = max_concurrent

            # Proxy Ayarları
            print(f"\n{COLORS['SUBTITLE']}Proxy Ayarları:")
            use_proxy_input = input(f"Proxy Kullan (e/h) [{use_proxy}]: ").strip().lower()
            if use_proxy_input:
                new_use_proxy = use_proxy_input in ["e", "evet", "y", "yes", "true", "1"]
            else:
                new_use_proxy = use_proxy
            
            new_proxy_url = ""
            if new_use_proxy:
                new_proxy_url = input(f"Proxy URL [{proxy_url}]: ").strip() or proxy_url
            
            # Çevre değişkenlerini güncelle
            success1 = self.config_manager.update_env_variable("API_BASE_URL", new_base_url)
            success2 = self.config_manager.update_env_variable("REQUEST_TIMEOUT", new_request_timeout)
            success3 = self.config_manager.update_env_variable("MAX_RETRIES", new_max_retries)
            success4 = self.config_manager.update_env_variable("MAX_CONCURRENT", new_max_concurrent)
            success5 = self.config_manager.update_env_variable("USE_PROXY", str(new_use_proxy).lower())
            success6 = self.config_manager.update_env_variable("PROXY_URL", new_proxy_url if new_use_proxy else "")
            
            if success1 and success2 and success3 and success4 and success5 and success6:
                print(f"\n{COLORS['SUCCESS']}✅ API yapılandırması başarıyla güncellendi.")
                print(f"{COLORS['INFO']}ℹ️ Değişikliklerin tam olarak etkili olabilmesi için uygulamayı yeniden başlatmanız gerekebilir.")
            else:
                print(f"\n{COLORS['WARNING']}❌ API yapılandırması güncellenirken bir hata oluştu.")
                
        except Exception as e:
            logger.error(f"API yapılandırması düzenlenirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
            
    def _change_data_directory(self) -> None:
        """Veri dizinini değiştirir."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}Veri Dizini Değiştirme:")
            print("-" * 50)
            
            # Mevcut veri dizinini göster
            current_data_dir = self.data_dir
            print(f"{COLORS['INFO']}Mevcut Veri Dizini: {COLORS['SUCCESS']}{current_data_dir}")
            
            # Yeni veri dizinini al
            new_data_dir = input(f"\nYeni Veri Dizini [{current_data_dir}]: ").strip() or current_data_dir
            
            # Aynı dizin ise işlem yapma
            if new_data_dir == current_data_dir:
                print(f"\n{COLORS['INFO']}Veri dizini değiştirilmedi.")
                return
            
            # Yeni dizini oluştur
            os.makedirs(new_data_dir, exist_ok=True)
            
            # Verileri taşımak isteyip istemediğini sor
            move_data = input(f"\nMevcut verileri yeni dizine taşımak istiyor musunuz? (e/h): ").strip().lower()
            
            if move_data in ["e", "evet", "y", "yes", "true", "1"]:
                print(f"\n{COLORS['INFO']}Veriler taşınıyor...")
                
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
                
                print(f"\n{COLORS['SUCCESS']}✅ Veriler başarıyla taşındı.")
            
            # Çevre değişkenini güncelle
            success = self.config_manager.update_env_variable("DATA_DIR", new_data_dir)
            
            if success:
                print(f"\n{COLORS['SUCCESS']}✅ Veri dizini başarıyla güncellendi.")
                print(f"{COLORS['INFO']}ℹ️ Değişikliklerin tam olarak etkili olabilmesi için uygulamayı yeniden başlatmanız gerekiyor.")
            else:
                print(f"\n{COLORS['WARNING']}❌ Veri dizini güncellenirken bir hata oluştu.")
                
        except Exception as e:
            logger.error(f"Veri dizini değiştirilirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def _edit_display_settings(self) -> None:
        """Görüntüleme ayarlarını düzenler."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}Görüntüleme Ayarları:")
            print("-" * 50)
            
            # Mevcut değerleri ConfigManager'dan al
            use_color = self.config_manager.get_use_color()
            date_format = self.config_manager.get_date_format()
            
            # Mevcut yapılandırmayı göster
            print(f"{COLORS['INFO']}Mevcut Yapılandırma:")
            print(f"  Renk Kullan: {COLORS['SUCCESS']}{use_color}")
            print(f"  Tarih Formatı: {COLORS['SUCCESS']}{date_format}")
            
            # Yeni değerleri al
            print(f"\n{COLORS['INFO']}Yeni değerler için Enter tuşuna basarak mevcut değeri koruyabilirsiniz:")
            
            use_color_input = input(f"Renk Kullan (e/h) [{use_color}]: ").strip().lower()
            if use_color_input:
                new_use_color = use_color_input in ["e", "evet", "y", "yes", "true", "1"]
            else:
                new_use_color = use_color
            
            new_date_format = input(f"Tarih Formatı [{date_format}]: ").strip() or date_format
            
            # Çevre değişkenlerini güncelle
            success1 = self.config_manager.update_env_variable("USE_COLOR", str(new_use_color).lower())
            success2 = self.config_manager.update_env_variable("DATE_FORMAT", new_date_format)
            
            if success1 and success2:
                print(f"\n{COLORS['SUCCESS']}✅ Görüntüleme ayarları başarıyla güncellendi.")
                print(f"{COLORS['INFO']}ℹ️ Değişikliklerin tam olarak etkili olabilmesi için uygulamayı yeniden başlatmanız gerekebilir.")
            else:
                print(f"\n{COLORS['WARNING']}❌ Görüntüleme ayarları güncellenirken bir hata oluştu.")
                
        except Exception as e:
            logger.error(f"Görüntüleme ayarları düzenlenirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def backup_data(self) -> None:
        """Veri yedekleme işlemleri."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}Veri Yedekleme:")
            print("-" * 50)
            print("1. 💾 Tüm Verileri Yedekle")
            print("2. 📦 Seçili Veri Tiplerini Yedekle")
            
            choice = input("\nSeçenek (1-2): ")
            
            if choice == "1":
                self._backup_all_data()
            elif choice == "2":
                self._backup_selected_data()
            else:
                print(f"\n{COLORS['WARNING']}❌ Geçersiz seçenek!")
                
        except Exception as e:
            logger.error(f"Veri yedekleme işlemi sırasında hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def _backup_all_data(self) -> None:
        """Tüm verileri yedekler."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}Tüm Verileri Yedekleme:")
            print("-" * 50)
            
            # Yedekleme dizinini al
            backup_dir = input(f"Yedekleme Dizini [backup]: ").strip() or "backup"
            
            # Yedekleme dizinini oluştur
            os.makedirs(backup_dir, exist_ok=True)
            
            # Alt dizinleri oluştur
            os.makedirs(os.path.join(backup_dir, "config"), exist_ok=True)
            os.makedirs(os.path.join(backup_dir, "data"), exist_ok=True)
            
            # Yapılandırma dosyalarını yedekle
            print(f"\n{COLORS['INFO']}Yapılandırma dosyaları yedekleniyor...")
            
            # leagues.txt dosyasını yedekle
            league_file = self.config_manager.league_config_path
            if os.path.exists(league_file):
                league_backup = os.path.join(backup_dir, "config", os.path.basename(league_file))
                shutil.copy2(league_file, league_backup)
                print(f"{COLORS['SUCCESS']}✓ Lig yapılandırması yedeklendi: {league_backup}")
            
            # .env dosyasını yedekle
            env_file = os.path.join(os.getcwd(), ".env")
            if os.path.exists(env_file):
                env_backup = os.path.join(backup_dir, "config", ".env")
                shutil.copy2(env_file, env_backup)
                print(f"{COLORS['SUCCESS']}✓ Çevre değişkenleri yedeklendi: {env_backup}")
            
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
            
            print(f"\n{COLORS['SUCCESS']}✅ Tüm veriler başarıyla yedeklendi: {os.path.abspath(backup_dir)}")
            
        except Exception as e:
            logger.error(f"Veri yedeklenirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def _backup_selected_data(self) -> None:
        """Seçili veri tiplerini yedekler."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}Seçili Veri Tiplerini Yedekleme:")
            print("-" * 50)
            
            # Yedekleme dizinini al
            backup_dir = input(f"Yedekleme Dizini [backup]: ").strip() or "backup"
            
            # Yedekleme dizinini oluştur
            os.makedirs(backup_dir, exist_ok=True)
            
            # Yedeklenecek veri tiplerini seç
            print(f"\n{COLORS['INFO']}Yedeklenecek veri tiplerini seçin:")
            print("1. 📝 Yapılandırma Dosyaları")
            print("2. 🏆 Lig ve Sezon Verileri")
            print("3. ⚽ Maç Verileri")
            print("4. 📊 Maç Detayları")
            
            choices = input("\nSeçimleriniz (örn: 1,2,3): ").strip()
            selected = [int(c.strip()) for c in choices.split(",") if c.strip().isdigit()]
            
            # Seçim yoksa çık
            if not selected:
                print(f"\n{COLORS['WARNING']}⚠️ Hiçbir veri tipi seçilmedi!")
                return
            
            # Yapılandırma dosyalarını yedekle
            if 1 in selected:
                print(f"\n{COLORS['INFO']}Yapılandırma dosyaları yedekleniyor...")
                os.makedirs(os.path.join(backup_dir, "config"), exist_ok=True)
                
                # leagues.txt dosyasını yedekle
                league_file = self.config_manager.league_config_path
                if os.path.exists(league_file):
                    league_backup = os.path.join(backup_dir, "config", os.path.basename(league_file))
                    shutil.copy2(league_file, league_backup)
                    print(f"{COLORS['SUCCESS']}✓ Lig yapılandırması yedeklendi: {league_backup}")
                
                # .env dosyasını yedekle
                env_file = os.path.join(os.getcwd(), ".env")
                if os.path.exists(env_file):
                    env_backup = os.path.join(backup_dir, "config", ".env")
                    shutil.copy2(env_file, env_backup)
                    print(f"{COLORS['SUCCESS']}✓ Çevre değişkenleri yedeklendi: {env_backup}")
            
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
                print(f"\n{COLORS['INFO']}Maç verileri yedekleniyor...")
                src_dir = os.path.join(self.data_dir, "matches")
                if os.path.exists(src_dir) and os.path.isdir(src_dir):
                    dst_dir = os.path.join(backup_dir, "data", "matches")
                    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
                    print(f"{COLORS['SUCCESS']}✓ Maç verileri yedeklendi: {dst_dir}")
            
            # Maç detaylarını yedekle
            if 4 in selected:
                print(f"\n{COLORS['INFO']}Maç detayları yedekleniyor...")
                src_dir = os.path.join(self.data_dir, "match_details")
                if os.path.exists(src_dir) and os.path.isdir(src_dir):
                    dst_dir = os.path.join(backup_dir, "data", "match_details")
                    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
                    print(f"{COLORS['SUCCESS']}✓ Maç detayları yedeklendi: {dst_dir}")
            
            print(f"\n{COLORS['SUCCESS']}✅ Seçili veriler başarıyla yedeklendi: {os.path.abspath(backup_dir)}")
            
        except Exception as e:
            logger.error(f"Veri yedeklenirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def restore_data(self) -> None:
        """Veri geri yükleme işlemleri."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}Veri Geri Yükleme:")
            print("-" * 50)
            
            # Yedek dizinini al
            backup_dir = input(f"Yedek Dizini [backup]: ").strip() or "backup"
            
            # Yedek dizini kontrol et
            if not os.path.exists(backup_dir) or not os.path.isdir(backup_dir):
                print(f"\n{COLORS['WARNING']}⚠️ Belirtilen yedek dizini bulunamadı: {backup_dir}")
                return
            
            # Geri yüklenecek veri tiplerini seç
            print(f"\n{COLORS['INFO']}Geri yüklenecek veri tiplerini seçin:")
            print("1. 📝 Yapılandırma Dosyaları")
            print("2. 🏆 Lig ve Sezon Verileri")
            print("3. ⚽ Maç Verileri")
            print("4. 📊 Maç Detayları")
            
            choices = input("\nSeçimleriniz (örn: 1,2,3): ").strip()
            selected = [int(c.strip()) for c in choices.split(",") if c.strip().isdigit()]
            
            # Seçim yoksa çık
            if not selected:
                print(f"\n{COLORS['WARNING']}⚠️ Hiçbir veri tipi seçilmedi!")
                return
            
            # Yapılandırma dosyalarını geri yükle
            if 1 in selected:
                print(f"\n{COLORS['INFO']}Yapılandırma dosyaları geri yükleniyor...")
                
                # leagues.txt dosyasını geri yükle
                league_file = os.path.join(backup_dir, "config", os.path.basename(self.config_manager.league_config_path))
                if os.path.exists(league_file):
                    shutil.copy2(league_file, self.config_manager.league_config_path)
                    print(f"{COLORS['SUCCESS']}✓ Lig yapılandırması geri yüklendi: {self.config_manager.league_config_path}")
                
                # .env dosyasını geri yükle
                env_file = os.path.join(backup_dir, "config", ".env")
                if os.path.exists(env_file):
                    shutil.copy2(env_file, os.path.join(os.getcwd(), ".env"))
                    print(f"{COLORS['SUCCESS']}✓ Çevre değişkenleri geri yüklendi: .env")
            
            # Lig ve sezon verilerini geri yükle
            if 2 in selected:
                print(f"\n{COLORS['INFO']}Lig ve sezon verileri geri yükleniyor...")
                src_dir = os.path.join(backup_dir, "data", "seasons")
                if os.path.exists(src_dir) and os.path.isdir(src_dir):
                    dst_dir = os.path.join(self.data_dir, "seasons")
                    os.makedirs(dst_dir, exist_ok=True)
                    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
                    print(f"{COLORS['SUCCESS']}✓ Sezon verileri geri yüklendi: {dst_dir}")
            
            # Maç verilerini geri yükle
            if 3 in selected:
                print(f"\n{COLORS['INFO']}Maç verileri geri yükleniyor...")
                src_dir = os.path.join(backup_dir, "data", "matches")
                if os.path.exists(src_dir) and os.path.isdir(src_dir):
                    dst_dir = os.path.join(self.data_dir, "matches")
                    os.makedirs(dst_dir, exist_ok=True)
                    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
                    print(f"{COLORS['SUCCESS']}✓ Maç verileri geri yüklendi: {dst_dir}")
            
            # Maç detaylarını geri yükle
            if 4 in selected:
                print(f"\n{COLORS['INFO']}Maç detayları geri yükleniyor...")
                src_dir = os.path.join(backup_dir, "data", "match_details")
                if os.path.exists(src_dir) and os.path.isdir(src_dir):
                    dst_dir = os.path.join(self.data_dir, "match_details")
                    os.makedirs(dst_dir, exist_ok=True)
                    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
                    print(f"{COLORS['SUCCESS']}✓ Maç detayları geri yüklendi: {dst_dir}")
            
            # Yapılandırmayı yeniden yükle
            if 1 in selected:
                success = self.config_manager.reload_config()
                if success:
                    print(f"{COLORS['SUCCESS']}✓ Yapılandırma yeniden yüklendi.")
                else:
                    print(f"{COLORS['WARNING']}⚠️ Yapılandırma yeniden yüklenemedi!")
            
            print(f"\n{COLORS['SUCCESS']}✅ Seçili veriler başarıyla geri yüklendi.")
            print(f"{COLORS['INFO']}ℹ️ Değişikliklerin tam olarak etkili olabilmesi için uygulamayı yeniden başlatmanız gerekebilir.")
            
        except Exception as e:
            logger.error(f"Veri geri yüklenirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def clear_data(self) -> None:
        """Veri temizleme işlemleri."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}Veri Temizleme:")
            print("-" * 50)
            print("1. 🗑️ Tüm Verileri Temizle")
            print("2. 🧹 Seçili Veri Türlerini Temizle")
            
            choice = input("\nSeçenek (1-2): ")
            
            if choice == "1":
                self._clear_all_data()
            elif choice == "2":
                self._clear_selected_data()
            else:
                print(f"\n{COLORS['WARNING']}❌ Geçersiz seçenek!")
                
        except Exception as e:
            logger.error(f"Veri temizleme işlemi sırasında hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def _clear_all_data(self) -> None:
        """Tüm verileri temizler."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            # Onay al
            confirm = input(f"\n{COLORS['WARNING']}DİKKAT: Bu işlem TÜM verileri silecek ve geri alınamaz! Devam etmek istiyor musunuz? (e/h): ").strip().lower()
            
            if confirm not in ["e", "evet", "y", "yes", "true", "1"]:
                print(f"\n{COLORS['INFO']}Temizleme işlemi iptal edildi.")
                return
            
            # İkinci onay
            confirm2 = input(f"\n{COLORS['WARNING']}UYARI: Bu işlem sonucunda tüm verileriniz kaybolacak. Son kez onaylıyor musunuz? (e/h): ").strip().lower()
            
            if confirm2 not in ["e", "evet", "y", "yes", "true", "1"]:
                print(f"\n{COLORS['INFO']}Temizleme işlemi iptal edildi.")
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
            
            print(f"\n{COLORS['SUCCESS']}✅ Tüm veriler başarıyla temizlendi.")
            
        except Exception as e:
            logger.error(f"Tüm veriler temizlenirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def _clear_selected_data(self) -> None:
        """Seçili veri türlerini temizler."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}Seçili Veri Türlerini Temizle:")
            print("-" * 50)
            print("1. 📅 Sezon Verileri")
            print("2. 🎮 Maç Verileri")
            print("3. 📈 Maç Detayları")
            print("4. 📊 CSV Veri Setleri")
            print("5. 📝 Raporlar")
            
            data_types = []
            selections = input("\nSeçiminiz (virgülle ayrılmış numaralar): ").strip()
            
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
                print(f"\n{COLORS['WARNING']}❌ Hiçbir veri türü seçilmedi.")
                return
            
            # Onay al
            confirm = input(f"\n{COLORS['WARNING']}DİKKAT: Bu işlem seçili veri türlerini silecek! Devam etmek istiyor musunuz? (e/h): ").strip().lower()
            
            if confirm not in ["e", "evet", "y", "yes", "true", "1"]:
                print(f"\n{COLORS['INFO']}Temizleme işlemi iptal edildi.")
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
            
            print(f"\n{COLORS['SUCCESS']}✅ Seçili veri türleri başarıyla temizlendi.")
            
        except Exception as e:
            logger.error(f"Seçili veri türleri temizlenirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def show_about(self) -> None:
        """Program hakkında bilgi gösterir."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}SofaScore Scraper Hakkında:")
            print("-" * 50)
            print(f"{COLORS['INFO']}Versiyon: {COLORS['SUCCESS']}1.0.0")
            print(f"{COLORS['INFO']}Geliştirici: {COLORS['SUCCESS']}SofaScore Scraper Ekibi")
            print(f"{COLORS['INFO']}Lisans: {COLORS['SUCCESS']}MIT")
            print(f"{COLORS['INFO']}Açıklama: {COLORS['SUCCESS']}SofaScore API kullanarak futbol maç verilerini çeken ve analiz eden bir uygulama.")
            
            print(f"\n{COLORS['SUBTITLE']}Kütüphaneler:")
            print(f"{COLORS['INFO']}requests: HTTP istekleri için")
            print(f"{COLORS['INFO']}colorama: Renkli terminal çıktısı için")
            print(f"{COLORS['INFO']}pandas: Veri analizi için")
            
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