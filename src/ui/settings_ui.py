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
            
            # .env dosyasının varlığını kontrol et
            env_path = os.path.join(os.getcwd(), ".env")
            env_exists = os.path.exists(env_path)
            
            # Mevcut değerleri .env dosyasından veya sistem çevre değişkenlerinden al
            base_url = os.getenv("API_BASE_URL", "https://www.sofascore.com/api/v1")
            request_timeout = os.getenv("REQUEST_TIMEOUT", "30")
            max_retries = os.getenv("MAX_RETRIES", "3")
            use_proxy = os.getenv("USE_PROXY", "false").lower() == "true"
            proxy_url = os.getenv("PROXY_URL", "")
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
            
            # .env dosyasını güncelle veya oluştur
            env_content = []
            
            # Eğer .env dosyası varsa, içeriğini oku
            if env_exists:
                try:
                    with open(env_path, 'r', encoding='utf-8') as f:
                        env_content = f.readlines()
                except Exception as e:
                    logger.error(f".env dosyası okunurken hata: {str(e)}")
                    env_content = []
            
            # Her bir değişken için .env içeriğini güncelle
            env_vars = {
                "API_BASE_URL": new_base_url,
                "REQUEST_TIMEOUT": new_request_timeout,
                "MAX_RETRIES": new_max_retries,
                "MAX_CONCURRENT": new_max_concurrent,
                "USE_PROXY": str(new_use_proxy).lower(),
                "PROXY_URL": new_proxy_url if new_use_proxy else ""
            }
            
            # Her değişken için içeriği düzenle
            updated_env = []
            for var_name, var_value in env_vars.items():
                var_found = False
                for line in env_content:
                    if line.strip().startswith(f"{var_name}="):
                        var_found = True
                        # Değeri güncelle
                        updated_env.append(f"{var_name}={var_value}\n")
                    else:
                        # Diğer satırları aynen koru
                        if line not in updated_env:
                            updated_env.append(line)
                
                # Eğer değişken .env dosyasında yoksa ekle
                if not var_found:
                    updated_env.append(f"{var_name}={var_value}\n")
            
            # Dosyaya yaz
            try:
                with open(env_path, 'w', encoding='utf-8') as f:
                    f.writelines(updated_env)
                print(f"\n{COLORS['SUCCESS']}✅ API yapılandırması başarıyla güncellendi.")
                print(f"{COLORS['INFO']}ℹ️ Değişikliklerin tam olarak etkili olabilmesi için uygulamayı yeniden başlatmanız gerekebilir.")
            except Exception as e:
                logger.error(f".env dosyası yazılırken hata: {str(e)}")
                print(f"\n{COLORS['WARNING']}❌ .env dosyası güncellenirken bir hata oluştu: {str(e)}")
                
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
            
            # .env dosyasını güncelle
            env_path = os.path.join(os.getcwd(), ".env")
            env_exists = os.path.exists(env_path)
            
            env_content = []
            # Eğer .env dosyası varsa, içeriğini oku
            if env_exists:
                try:
                    with open(env_path, 'r', encoding='utf-8') as f:
                        env_content = f.readlines()
                except Exception as e:
                    logger.error(f".env dosyası okunurken hata: {str(e)}")
                    env_content = []
            
            # Veri dizini değişkenini .env içeriğinde güncelle
            var_name = "DATA_DIR"
            var_value = new_data_dir
            
            var_found = False
            updated_env = []
            
            for line in env_content:
                if line.strip().startswith(f"{var_name}="):
                    var_found = True
                    # Değeri güncelle
                    updated_env.append(f"{var_name}={var_value}\n")
                else:
                    # Diğer satırları aynen koru
                    if line not in updated_env:
                        updated_env.append(line)
            
            # Eğer değişken .env dosyasında yoksa ekle
            if not var_found:
                updated_env.append(f"{var_name}={var_value}\n")
            
            # Dosyaya yaz
            try:
                with open(env_path, 'w', encoding='utf-8') as f:
                    f.writelines(updated_env)
                print(f"\n{COLORS['SUCCESS']}✅ Veri dizini başarıyla güncellendi.")
                print(f"{COLORS['INFO']}ℹ️ Değişikliklerin tam olarak etkili olabilmesi için uygulamayı yeniden başlatmanız gerekiyor.")
            except Exception as e:
                logger.error(f".env dosyası yazılırken hata: {str(e)}")
                print(f"\n{COLORS['WARNING']}❌ .env dosyası güncellenirken bir hata oluştu: {str(e)}")
            
            # Ayrıca config dosyasını da güncelle (geriye dönük uyumluluk için)
            if not self.config_manager.config.get("general"):
                self.config_manager.config["general"] = {}
            
            self.config_manager.config["general"]["data_dir"] = new_data_dir
            success = self.config_manager.save_config()
            
            if not success:
                print(f"\n{COLORS['WARNING']}⚠️ Yapılandırma dosyasında güncelleme başarısız oldu, ancak .env dosyası güncellendi.")
                
        except Exception as e:
            logger.error(f"Veri dizini değiştirilirken hata: {str(e)}")
            print(f"\n{COLORS['WARNING']}Hata: {str(e)}")
    
    def _edit_display_settings(self) -> None:
        """Görüntüleme ayarlarını düzenler."""
        COLORS = self.colors  # Kısa erişim için
        
        try:
            print(f"\n{COLORS['SUBTITLE']}Görüntüleme Ayarları:")
            print("-" * 50)
            
            # Mevcut değerleri .env veya config'den al
            use_color = os.getenv("USE_COLOR", "true").lower() == "true"
            date_format = os.getenv("DATE_FORMAT", "%Y-%m-%d %H:%M:%S")
            
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
            
            # .env dosyasını güncelle
            env_path = os.path.join(os.getcwd(), ".env")
            env_exists = os.path.exists(env_path)
            
            env_content = []
            # Eğer .env dosyası varsa, içeriğini oku
            if env_exists:
                try:
                    with open(env_path, 'r', encoding='utf-8') as f:
                        env_content = f.readlines()
                except Exception as e:
                    logger.error(f".env dosyası okunurken hata: {str(e)}")
                    env_content = []
            
            # Değişkenleri .env içeriğinde güncelle
            env_vars = {
                "USE_COLOR": str(new_use_color).lower(),
                "DATE_FORMAT": new_date_format
            }
            
            updated_env = []
            for var_name, var_value in env_vars.items():
                var_found = False
                for line in env_content:
                    if line.strip().startswith(f"{var_name}="):
                        var_found = True
                        # Değeri güncelle
                        updated_env.append(f"{var_name}={var_value}\n")
                    else:
                        # Diğer satırları aynen koru
                        if line not in updated_env:
                            updated_env.append(line)
                
                # Eğer değişken .env dosyasında yoksa ekle
                if not var_found:
                    updated_env.append(f"{var_name}={var_value}\n")
            
            # Dosyaya yaz
            try:
                with open(env_path, 'w', encoding='utf-8') as f:
                    f.writelines(updated_env)
                print(f"\n{COLORS['SUCCESS']}✅ Görüntüleme ayarları başarıyla güncellendi.")
                print(f"{COLORS['INFO']}ℹ️ Değişikliklerin tam olarak etkili olabilmesi için uygulamayı yeniden başlatmanız gerekebilir.")
            except Exception as e:
                logger.error(f".env dosyası yazılırken hata: {str(e)}")
                print(f"\n{COLORS['WARNING']}❌ .env dosyası güncellenirken bir hata oluştu: {str(e)}")
            
            # Ayrıca config dosyasını da güncelle (geriye dönük uyumluluk için)
            if not self.config_manager.config.get("display"):
                self.config_manager.config["display"] = {}
            
            self.config_manager.config["display"]["use_color"] = new_use_color
            self.config_manager.config["display"]["date_format"] = new_date_format
            
            success = self.config_manager.save_config()
            
            if not success:
                print(f"\n{COLORS['WARNING']}⚠️ Yapılandırma dosyasında güncelleme başarısız oldu, ancak .env dosyası güncellendi.")
                
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
            
            # .env dosyasını kopyala
            env_file = os.path.join(os.getcwd(), ".env")
            if os.path.exists(env_file):
                shutil.copy2(env_file, os.path.join(backup_dir, "config"))
            
            # Veri dizinlerini kopyala
            data_dirs = ["seasons", "matches", "match_details", "datasets", "reports"]
            
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
                    data_types.append("match_details")
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
                "match_details": "match_details",
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
                "match_details": "Maç Detayları",
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
                elif data_type.endswith("match_details"):
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