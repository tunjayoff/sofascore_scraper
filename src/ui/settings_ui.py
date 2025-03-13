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
            
            # Mevcut yapılandırmayı .env dosyasından al
            import os
            import dotenv
            
            # .env dosyasını yükle
            dotenv_path = dotenv.find_dotenv()
            if not dotenv_path:
                dotenv_path = ".env"  # Varsayılan konum
            dotenv.load_dotenv(dotenv_path)
            
            # Mevcut değerleri al
            base_url = os.getenv("API_BASE_URL", "https://www.sofascore.com/api/v1")
            use_proxy = os.getenv("USE_PROXY", "false").lower() == "true"
            proxy_url = os.getenv("PROXY_URL", "")
            timeout = int(os.getenv("REQUEST_TIMEOUT", "30"))
            retry_count = int(os.getenv("MAX_RETRIES", "3"))
            max_concurrent = int(os.getenv("MAX_CONCURRENT", "25"))
            user_agent = os.getenv("USER_AGENT", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36")
            
            # Mevcut yapılandırmayı göster
            print(f"{COLORS['INFO']}Mevcut Yapılandırma:")
            print(f"  Base URL: {COLORS['SUCCESS']}{base_url}")
            print(f"  İstek Zaman Aşımı: {COLORS['SUCCESS']}{timeout} saniye")
            print(f"  Yeniden Deneme Sayısı: {COLORS['SUCCESS']}{retry_count}")
            print(f"  Maksimum Eşzamanlı İstek: {COLORS['SUCCESS']}{max_concurrent}")
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
                new_timeout = input(f"İstek Zaman Aşımı (saniye) [{timeout}]: ").strip()
                new_timeout = int(new_timeout) if new_timeout else timeout
            except ValueError:
                print(f"{COLORS['WARNING']}⚠️ Geçersiz değer, varsayılan kullanılıyor: {timeout}")
                new_timeout = timeout

            try:
                new_retry_count = input(f"Yeniden Deneme Sayısı [{retry_count}]: ").strip()
                new_retry_count = int(new_retry_count) if new_retry_count else retry_count
            except ValueError:
                print(f"{COLORS['WARNING']}⚠️ Geçersiz değer, varsayılan kullanılıyor: {retry_count}")
                new_retry_count = retry_count
                
            try:
                new_max_concurrent = input(f"Maksimum Eşzamanlı İstek [{max_concurrent}]: ").strip()
                new_max_concurrent = int(new_max_concurrent) if new_max_concurrent else max_concurrent
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
            
            # Yapılandırmayı .env dosyasına kaydet
            changes = {
                "API_BASE_URL": new_base_url,
                "REQUEST_TIMEOUT": str(new_timeout),
                "MAX_RETRIES": str(new_retry_count),
                "MAX_CONCURRENT": str(new_max_concurrent),
                "USE_PROXY": "true" if new_use_proxy else "false",
            }
            
            if new_use_proxy and new_proxy_url:
                changes["PROXY_URL"] = new_proxy_url
            
            # .env dosyasını güncelle
            success = self._update_env_file(dotenv_path, changes)
            
            if success:
                print(f"\n{COLORS['SUCCESS']}✅ API yapılandırması başarıyla güncellendi.")
                print(f"{COLORS['INFO']}Değişikliklerin etkili olması için uygulamayı yeniden başlatmanız gerekebilir.")
            else:
                print(f"\n{COLORS['WARNING']}❌ Yapılandırma kaydedilirken bir hata oluştu.")
                
        except Exception as e:
            logger.error(f"API yapılandırması düzenlenirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
            
    def _update_env_file(self, env_path: str, changes: Dict[str, str]) -> bool:
        """
        .env dosyasını güncelleyen yardımcı fonksiyon
        
        Args:
            env_path: .env dosyasının yolu 
            changes: Güncellenecek değerler sözlüğü
            
        Returns:
            bool: Başarılı olursa True, değilse False
        """
        try:
            import os
            import dotenv
            from pathlib import Path
            
            # .env dosyasının varlığını kontrol et
            if not os.path.exists(env_path):
                # .env dosyası yoksa temel bir template oluştur
                with open(env_path, 'w', encoding='utf-8') as f:
                    f.write("# SofaScore Scraper API ayarları\n")
                    f.write("API_BASE_URL=https://www.sofascore.com/api/v1\n")
                    f.write("REQUEST_TIMEOUT=30\n")
                    f.write("MAX_RETRIES=3\n")
                    f.write("MAX_CONCURRENT=25\n")
                    f.write("USE_PROXY=false\n")
                    f.write("PROXY_URL=\n")
                    f.write("WAIT_TIME_MIN=0.2\n")
                    f.write("WAIT_TIME_MAX=0.5\n")
                    f.write("FETCH_ONLY_FINISHED=true\n")
                    f.write("SAVE_EMPTY_ROUNDS=false\n")
                    f.write("DATA_DIR=data\n")
            
            # .env dosyasını güncelle
            for key, value in changes.items():
                dotenv.set_key(env_path, key, value)
            
            return True
            
        except Exception as e:
            logger.error(f".env dosyası güncellenirken hata: {str(e)}")
            return False
    
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
                os.makedirs(os.path.join(new_data_dir, "match_data"), exist_ok=True)
                os.makedirs(os.path.join(new_data_dir, "datasets"), exist_ok=True)
                os.makedirs(os.path.join(new_data_dir, "reports"), exist_ok=True)
                
                # Verileri taşı
                self._move_directory_contents(os.path.join(current_data_dir, "seasons"), os.path.join(new_data_dir, "seasons"))
                self._move_directory_contents(os.path.join(current_data_dir, "matches"), os.path.join(new_data_dir, "matches"))
                self._move_directory_contents(os.path.join(current_data_dir, "match_data"), os.path.join(new_data_dir, "match_data"))
                self._move_directory_contents(os.path.join(current_data_dir, "datasets"), os.path.join(new_data_dir, "datasets"))
                self._move_directory_contents(os.path.join(current_data_dir, "reports"), os.path.join(new_data_dir, "reports"))
                
                print(f"\n{COLORS['SUCCESS']}✅ Veriler başarıyla taşındı.")
            
            # Yapılandırmayı güncelle
            if not self.config_manager.config.get("general"):
                self.config_manager.config["general"] = {}
            
            self.config_manager.config["general"]["data_dir"] = new_data_dir
            
            # Yapılandırmayı kaydet
            success = self.config_manager.save_config()
            
            if success:
                print(f"\n{COLORS['SUCCESS']}✅ Veri dizini başarıyla güncellendi. Uygulamayı yeniden başlatmanız gerekiyor.")
            else:
                print(f"\n{COLORS['WARNING']}❌ Yapılandırma kaydedilirken bir hata oluştu.")
                
        except Exception as e:
            logger.error(f"Veri dizini değiştirilirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def _edit_display_settings(self) -> None:
        """Görüntüleme ayarlarını düzenler."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}Görüntüleme Ayarları:")
            print("-" * 50)
            
            # Mevcut yapılandırmayı al
            current_config = self.config_manager.config.get("display", {})
            use_color = current_config.get("use_color", True)
            date_format = current_config.get("date_format", "%Y-%m-%d %H:%M:%S")
            
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
            
            # Yapılandırmayı güncelle
            if not self.config_manager.config.get("display"):
                self.config_manager.config["display"] = {}
            
            self.config_manager.config["display"]["use_color"] = new_use_color
            self.config_manager.config["display"]["date_format"] = new_date_format
            
            # Yapılandırmayı kaydet
            success = self.config_manager.save_config()
            
            if success:
                print(f"\n{COLORS['SUCCESS']}✅ Görüntüleme ayarları başarıyla güncellendi.")
            else:
                print(f"\n{COLORS['WARNING']}❌ Yapılandırma kaydedilirken bir hata oluştu.")
                
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
            print(f"\n{COLORS['INFO']}Tüm veriler yedekleniyor...")
            
            # Yedekleme dizinini al
            backup_dir = input("Yedekleme Dizini: ").strip()
            
            if not backup_dir:
                print(f"\n{COLORS['WARNING']}❌ Yedekleme dizini belirtilmedi.")
                return
            
            # Dizini oluştur
            os.makedirs(backup_dir, exist_ok=True)
            
            # Alt dizinleri oluştur
            os.makedirs(os.path.join(backup_dir, "config"), exist_ok=True)
            os.makedirs(os.path.join(backup_dir, "data"), exist_ok=True)
            
            # Yapılandırma dosyasını kopyala
            config_file = self.config_manager.config_path
            if os.path.exists(config_file):
                shutil.copy2(config_file, os.path.join(backup_dir, "config"))
            
            # Veri dizinlerini kopyala
            data_dirs = ["seasons", "matches", "match_data", "datasets", "reports"]
            
            for dir_name in data_dirs:
                src_dir = os.path.join(self.data_dir, dir_name)
                dest_dir = os.path.join(backup_dir, "data", dir_name)
                
                if os.path.exists(src_dir):
                    if os.path.exists(dest_dir):
                        shutil.rmtree(dest_dir)
                    shutil.copytree(src_dir, dest_dir)
            
            print(f"\n{COLORS['SUCCESS']}✅ Tüm veriler başarıyla yedeklendi: {backup_dir}")
            
        except Exception as e:
            logger.error(f"Tüm veriler yedeklenirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def _backup_selected_data(self) -> None:
        """Seçili veri tiplerini yedekler."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}Seçili Veri Türlerini Yedekle:")
            print("-" * 50)
            print("1. ⚙️ Yapılandırma")
            print("2. 📅 Sezon Verileri")
            print("3. 🎮 Maç Verileri")
            print("4. 📈 Maç Detayları")
            print("5. 📊 CSV Veri Setleri")
            print("6. 📝 Raporlar")
            
            data_types = []
            selections = input("\nSeçiminiz (virgülle ayrılmış numaralar): ").strip()
            
            for selection in selections.split(","):
                selection = selection.strip()
                if selection == "1":
                    data_types.append("config")
                elif selection == "2":
                    data_types.append("seasons")
                elif selection == "3":
                    data_types.append("matches")
                elif selection == "4":
                    data_types.append("match_data")
                elif selection == "5":
                    data_types.append("datasets")
                elif selection == "6":
                    data_types.append("reports")
            
            if not data_types:
                print(f"\n{COLORS['WARNING']}❌ Hiçbir veri türü seçilmedi.")
                return
            
            # Yedekleme dizinini al
            backup_dir = input("Yedekleme Dizini: ").strip()
            
            if not backup_dir:
                print(f"\n{COLORS['WARNING']}❌ Yedekleme dizini belirtilmedi.")
                return
            
            # Dizini oluştur
            os.makedirs(backup_dir, exist_ok=True)
            
            # Seçili veri türlerini yedekle
            if "config" in data_types:
                os.makedirs(os.path.join(backup_dir, "config"), exist_ok=True)
                config_file = self.config_manager.config_path
                if os.path.exists(config_file):
                    shutil.copy2(config_file, os.path.join(backup_dir, "config"))
                    print(f"{COLORS['SUCCESS']}✓ Yapılandırma yedeklendi.")
            
            data_dir_mapping = {
                "seasons": "seasons",
                "matches": "matches",
                "match_data": "match_data",
                "datasets": "datasets",
                "reports": "reports"
            }
            
            for data_type in data_types:
                if data_type in data_dir_mapping:
                    src_dir = os.path.join(self.data_dir, data_dir_mapping[data_type])
                    dest_dir = os.path.join(backup_dir, data_dir_mapping[data_type])
                    
                    if os.path.exists(src_dir):
                        if os.path.exists(dest_dir):
                            shutil.rmtree(dest_dir)
                        shutil.copytree(src_dir, dest_dir)
                        print(f"{COLORS['SUCCESS']}✓ {data_type} verileri yedeklendi.")
                    else:
                        print(f"{COLORS['WARNING']}✗ {data_type} dizini bulunamadı.")
            
            print(f"\n{COLORS['SUCCESS']}✅ Seçili veriler başarıyla yedeklendi: {backup_dir}")
            
        except Exception as e:
            logger.error(f"Seçili veriler yedeklenirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def restore_data(self) -> None:
        """Veri geri yükleme işlemleri."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}Veri Geri Yükleme:")
            print("-" * 50)
            
            # Yedek dizinini al
            backup_dir = input("Yedek Dizini: ").strip()
            
            if not backup_dir or not os.path.exists(backup_dir):
                print(f"\n{COLORS['WARNING']}❌ Geçerli bir yedek dizini belirtilmedi.")
                return
            
            print(f"\n{COLORS['INFO']}Yedek içeriği kontrol ediliyor...")
            
            # Kullanılabilir veri türlerini kontrol et
            available_data_types = []
            
            if os.path.exists(os.path.join(backup_dir, "config")):
                available_data_types.append("config")
            
            data_dir_mapping = {
                "seasons": "Sezon Verileri",
                "matches": "Maç Verileri",
                "match_data": "Maç Detayları",
                "datasets": "CSV Veri Setleri",
                "reports": "Raporlar"
            }
            
            for data_type, display_name in data_dir_mapping.items():
                if os.path.exists(os.path.join(backup_dir, data_type)):
                    available_data_types.append(data_type)
                elif os.path.exists(os.path.join(backup_dir, "data", data_type)):
                    available_data_types.append(f"data/{data_type}")
            
            if not available_data_types:
                print(f"\n{COLORS['WARNING']}❌ Yedekte geçerli veri bulunamadı.")
                return
            
            print(f"\n{COLORS['INFO']}Mevcut veri türleri:")
            
            for i, data_type in enumerate(available_data_types, 1):
                if data_type == "config":
                    print(f"{i}. ⚙️ Yapılandırma")
                elif data_type.endswith("seasons"):
                    print(f"{i}. 📅 Sezon Verileri")
                elif data_type.endswith("matches"):
                    print(f"{i}. 🎮 Maç Verileri")
                elif data_type.endswith("match_data"):
                    print(f"{i}. 📈 Maç Detayları")
                elif data_type.endswith("datasets"):
                    print(f"{i}. 📊 CSV Veri Setleri")
                elif data_type.endswith("reports"):
                    print(f"{i}. 📝 Raporlar")
            
            # Geri yüklenecek verileri seç
            selections = input("\nGeri yüklenecek veriler (virgülle ayrılmış numaralar, tümü için boş bırakın): ").strip()
            
            selected_data_types = []
            
            if not selections:
                selected_data_types = available_data_types
            else:
                for selection in selections.split(","):
                    try:
                        index = int(selection.strip()) - 1
                        if 0 <= index < len(available_data_types):
                            selected_data_types.append(available_data_types[index])
                    except ValueError:
                        pass
            
            if not selected_data_types:
                print(f"\n{COLORS['WARNING']}❌ Hiçbir veri türü seçilmedi.")
                return
            
            # Mevcut verilerin üzerine yazma onayı
            confirm = input(f"\n{COLORS['WARNING']}DİKKAT: Bu işlem mevcut verilerin üzerine yazacak. Devam etmek istiyor musunuz? (e/h): ").strip().lower()
            
            if confirm not in ["e", "evet", "y", "yes", "true", "1"]:
                print(f"\n{COLORS['INFO']}Geri yükleme işlemi iptal edildi.")
                return
            
            # Verileri geri yükle
            for data_type in selected_data_types:
                if data_type == "config":
                    config_file = os.path.join(backup_dir, "config", os.path.basename(self.config_manager.config_path))
                    if os.path.exists(config_file):
                        shutil.copy2(config_file, self.config_manager.config_path)
                        print(f"{COLORS['SUCCESS']}✓ Yapılandırma geri yüklendi.")
                else:
                    if data_type.startswith("data/"):
                        # data/ dizini içindeki veriler
                        src_dir = os.path.join(backup_dir, data_type)
                        dest_dir = os.path.join(self.data_dir, data_type.split("/")[1])
                    else:
                        # Doğrudan kök dizindeki veriler
                        src_dir = os.path.join(backup_dir, data_type)
                        dest_dir = os.path.join(self.data_dir, data_type)
                    
                    if os.path.exists(src_dir):
                        if os.path.exists(dest_dir):
                            shutil.rmtree(dest_dir)
                        shutil.copytree(src_dir, dest_dir)
                        
                        display_name = data_dir_mapping.get(data_type.split("/")[-1], data_type)
                        print(f"{COLORS['SUCCESS']}✓ {display_name} geri yüklendi.")
            
            print(f"\n{COLORS['SUCCESS']}✅ Veriler başarıyla geri yüklendi. Uygulamayı yeniden başlatmanız gerekiyor.")
            
        except Exception as e:
            logger.error(f"Veri geri yükleme işlemi sırasında hata: {str(e)}")
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
            data_dirs = ["seasons", "matches", "match_data", "datasets", "reports"]
            
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
                "3": "match_data",
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