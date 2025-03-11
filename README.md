# SofaScore Scraper

Bu proje, SofaScore API'sini kullanarak futbol maçlarının verilerini çekmek, analiz etmek ve yönetmek için geliştirilmiş bir Python uygulamasıdır. Farklı ligler, sezonlar ve maçlar hakkında kapsamlı veri toplama, işleme ve dışa aktarma imkanı sunar.

<div align="center">
    
![SofaScore Scraper](https://img.shields.io/badge/SofaScore-Scraper-blue)
![Python](https://img.shields.io/badge/Python-3.8%2B-brightgreen)
![License](https://img.shields.io/badge/License-MIT-yellow)
    
</div>

## 📋 İçindekiler

- [Özellikler](#özellikler)
- [Sistem Gereksinimleri](#sistem-gereksinimleri)
- [Kurulum](#kurulum)
- [Kullanım](#kullanım)
  - [Ana Menü](#ana-menü)
  - [Lig İşlemleri](#lig-işlemleri)
  - [Sezon İşlemleri](#sezon-işlemleri)
  - [Maç İşlemleri](#maç-işlemleri)
  - [Maç Detayları](#maç-detayları)
- [Konfigürasyon](#konfigürasyon)
- [Veri Yapısı](#veri-yapısı)
- [Çıktılar ve Veri Formatları](#çıktılar-ve-veri-formatları)
- [Nasıl Yapılır (How-to)](#nasıl-yapılır-how-to)
  - [Yeni Bir Lig Ekleme](#yeni-bir-lig-ekleme)
  - [Bir Sezonun Tüm Maçlarını Çekme](#bir-sezonun-tüm-maçlarını-çekme)
  - [CSV Veri Seti Oluşturma](#csv-veri-seti-oluşturma)
  - [Maç Detaylarını Analiz Etme](#maç-detaylarını-analiz-etme)
  - [Veri Analizi İçin Dışa Aktarma](#veri-analizi-için-dışa-aktarma)
- [Sık Sorulan Sorular (SSS)](#sık-sorulan-sorular-sss)
- [Mimari ve Geliştirme](#mimari-ve-geliştirme)
- [Sorun Giderme](#sorun-giderme)
- [Katkıda Bulunma](#katkıda-bulunma)
- [Lisans](#lisans)

## ✨ Özellikler

SofaScore Scraper, aşağıdaki temel özellikleri sunar:

- **Lig Yönetimi**:
  - Ligleri listeleme, ekleme ve kaldırma
  - Desteklenen tüm SofaScore liglerini görüntüleme
  - Lig ID'lerini otomatik tespit etme

- **Sezon İşlemleri**:
  - Liglere ait tüm sezonları çekme ve listeleme
  - Aktif sezonları otomatik tespit etme
  - Geçmiş ve gelecek sezonları yönetme

- **Maç Verileri**:
  - Belirli bir lig ve sezon için maç listelerini çekme
  - Haftalık/turlu maç verilerini görüntüleme
  - Tüm ligler için toplu maç verisi toplama
  - Akıllı sezon seçimi ile eksik verileri tamamlama

- **Maç Detayları**:
  - Maç istatistiklerini çekme
  - Takım serilerini görüntüleme
  - Maç öncesi form verilerini toplama
  - Karşılıklı istatistikleri (H2H) inceleme
  - Maç olaylarını ve skorlarını analiz etme

- **Veri Dışa Aktarma**:
  - Maç verilerini CSV formatına dönüştürme
  - Lig bazlı veya tüm liglerin verilerini tek seferde dışa aktarma
  - Tek bir maçın detaylarını CSV formatında kaydetme

- **Kullanıcı Arayüzü**:
  - Sezgisel terminal tabanlı menü sistemi
  - Renkli ve kategorize edilmiş çıktılar
  - İlerleme çubukları ve işlem durum göstergeleri
  - Detaylı hata mesajları ve loglama

## 💻 Sistem Gereksinimleri

- Python 3.8 veya üzeri
- pip (Python paket yöneticisi)
- İnternet bağlantısı (SofaScore API'ye erişmek için)
- 100 MB+ disk alanı (toplanan verilerin miktarına bağlı olarak değişir)

## 🔧 Kurulum

### 1. Projeyi İndirme

GitHub deposundan projeyi klonlayın:

```bash
git clone https://github.com/tunjayoff/sofascore_scraper.git
cd sofascore_scraper
```

Alternatif olarak, projeyi ZIP olarak indirip açabilirsiniz.

### 2. Sanal Ortam Oluşturma (Opsiyonel ama Önerilen)

Python sanal ortamı oluşturmak, paket çakışmalarını önlemeye yardımcı olur:

```bash
# Sanal ortam oluşturma
python -m venv venv

# Sanal ortamı aktifleştirme
# Linux/MacOS için:
source venv/bin/activate
# Windows için:
venv\Scripts\activate
```

### 3. Bağımlılıkları Yükleme

Gerekli Python paketlerini yükleyin:

```bash
pip install -r requirements.txt
```

### 4. Çevre Değişkenleri (Opsiyonel)

`.env` dosyasını kullanarak çevre değişkenlerini yapılandırabilirsiniz:

```bash
# .env.example dosyasını kopyalayın
cp .env.example .env
# Düzenleyin
nano .env  # veya tercih ettiğiniz metin editörü
```

## 📘 Kullanım

Uygulamayı çalıştırmak için ana dizinde şu komutu kullanın:

```bash
python main.py
```

Belirli parametrelerle çalıştırmak için:

```bash
# Hata ayıklama modunda çalıştırma
python main.py --debug

# Belirli bir konfigürasyon dosyasıyla çalıştırma
python main.py --config=custom_config.json

# Sadece belirli bir görevi çalıştırma (sezon verilerini çekme)
python main.py --task=fetch_seasons --league=52
```

### Ana Menü

Uygulama başladığında karşınıza bir ana menü gelecektir:

```
SofaScore Scraper v1.0.0
==========================================

Ana Menü:
--------------------------------------------------
1. 🏆 Lig İşlemleri
2. 🗓️ Sezon İşlemleri
3. ⚽ Maç İşlemleri
4. 📊 Maç Detayları
5. ⚙️ Ayarlar
0. 🚪 Çıkış

Seçiminiz (0-5): 
```

### Lig İşlemleri

1. **Ligleri Listele**: Kayıtlı ligleri görüntüler
2. **Lig Ekle**: Yeni bir lig ekler (Lig adı ve ID'si gereklidir)
3. **Lig Sil**: Mevcut bir ligi kaldırır
4. **Lig Ara**: Ligleri ada göre arar
5. **Ana Menüye Dön**: Ana menüye geri döner

### Sezon İşlemleri

1. **Sezonları Listele**: Kayıtlı sezonları görüntüler
2. **Sezon Verilerini Çek**: Belirli bir lig için sezon verilerini çeker
3. **Tüm Ligler İçin Sezon Verilerini Çek**: Tüm ligler için sezon verilerini çeker
4. **Ana Menüye Dön**: Ana menüye geri döner

### Maç İşlemleri

1. **Maçları Listele**: Çekilen maçları listeler
2. **Maç Verilerini Çek**: Belirli bir lig ve sezon için maç verilerini çeker
3. **Tüm Ligler İçin Maç Verilerini Çek**: Tüm ligler için maç verilerini çeker
4. **Ana Menüye Dön**: Ana menüye geri döner

### Maç Detayları

1. **Maç Detaylarını Çek**: Belirli maçlar için detaylı verileri çeker
2. **Tüm Maçlar İçin Detayları Çek**: Tüm maçlar için detaylı verileri çeker
3. **CSV Veri Seti Oluştur**: Maç verilerini CSV formatına dönüştürür
4. **Ana Menüye Dön**: Ana menüye geri döner

## ⚙️ Konfigürasyon

### Lig Yapılandırması

Lig bilgilerini `config/leagues.txt` dosyasında yönetebilirsiniz:

```
# Format: Lig Adı: ID
Premier League: 17
LaLiga: 8
Serie A: 23
Bundesliga: 35
Ligue 1: 34
Süper Lig: 52
```

### Genel Yapılandırma

Uygulama ayarlarını `config/config.json` dosyasında düzenleyebilirsiniz:

```json
{
  "data_dir": "data",
  "seasons_dir": "seasons",
  "matches_dir": "matches",
  "match_details_dir": "match_details",
  "max_retry_count": 3,
  "batch_size": 100,
  "max_concurrent_requests": 30,
  "log_level": "INFO"
}
```

## 📂 Veri Yapısı

SofaScore Scraper, topladığı verileri aşağıdaki yapıda organize eder:

```
data/
├── seasons/
│   └── {lig_id}_{lig_adı}/
│       └── seasons.json
├── matches/
│   └── {lig_id}_{lig_adı}/
│       └── {sezon_id}_{sezon_adı}/
│           ├── round_1.json
│           ├── round_2.json
│           └── ...
└── match_details/
    └── {lig_adı}/
        └── season_{sezon_adı}/
            └── {maç_id}/
                ├── full_data.json
                ├── basic.json
                ├── statistics.json
                ├── team_streaks.json
                ├── pregame_form.json
                └── h2h.json
```

### Veri Dosyaları

1. **seasons.json**: Bir lig için tüm sezonların listesi
2. **round_X.json**: Bir sezonun belirli bir turu/haftası için maçlar
3. **full_data.json**: Bir maç için toplanan tüm veriler
4. **basic.json**: Maçın temel bilgileri (takımlar, skor, tarih, vb.)
5. **statistics.json**: Maç istatistikleri (şutlar, paslar, korneler, vb.)
6. **team_streaks.json**: Takımların seriler/istatistikleri
7. **pregame_form.json**: Maç öncesi takım formları
8. **h2h.json**: Takımlar arası karşılaşma geçmişi

## 📊 Çıktılar ve Veri Formatları

### CSV Çıktıları

CSV dosyaları `data/match_details/processed/` dizinine kaydedilir:

1. **Tek Maç CSV**: `{maç_id}_{timestamp}.csv`
2. **Lig Maçları CSV**: `{lig_adı}_{timestamp}.csv`
3. **Tüm Maçlar CSV**: `all_matches_{timestamp}.csv`

Örnek CSV çıktısı:

```csv
match_id,tournament_name,season_name,round,home_team_name,away_team_name,home_score_ft,away_score_ft,match_date,home_possession,away_possession,home_shots_total,away_shots_total,home_shots_on_target,away_shots_on_target
10257123,Premier League,2023/2024,38,Manchester City,West Ham,3,1,1621789200,65,35,23,5,12,2
```

### JSON Veri Yapısı

JSON dosyaları, SofaScore API'nin döndürdüğü veri yapısını korur, ancak bazı durumlarda ek bilgilerle zenginleştirilir.

Örnek bir `basic.json` dosyası:

```json
{
  "tournament": {
    "name": "Premier League",
    "slug": "premier-league",
    "category": {
      "name": "England",
      "slug": "england",
      "sport": {
        "name": "Football",
        "slug": "football",
        "id": 1
      },
      "id": 1,
      "flag": "england"
    },
    "uniqueTournament": {
      "name": "Premier League",
      "slug": "premier-league",
      "category": {
        "name": "England",
        "slug": "england",
        "sport": {
          "name": "Football",
          "slug": "football",
          "id": 1
        },
        "id": 1,
        "flag": "england"
      },
      "userCount": 1327093,
      "hasEventPlayerStatistics": true,
      "crowdsourcingEnabled": false,
      "hasPerformanceGraphFeature": true,
      "id": 17,
      "hasPositionGraph": true
    },
    "primaryColorHex": "#3c1c5a",
    "secondaryColorHex": "#000000",
    "id": 29415
  }
}
```

## 🛠 Nasıl Yapılır (How-to)

### Yeni Bir Lig Ekleme

Yeni bir lig eklemek için iki yöntem vardır:

#### 1. Uygulama Üzerinden:

1. Ana menüden "Lig İşlemleri"ni seçin (1)
2. "Lig Ekle" seçeneğini seçin (2)
3. SofaScore'dan lig ID'sini öğrenmek için isim ile arama yapın
4. Lig adını ve ID'sini girin

#### 2. Doğrudan `leagues.txt` Dosyası Üzerinden:

1. `config/leagues.txt` dosyasını bir metin editöründe açın
2. Yeni ligi şu formatla ekleyin: `Lig Adı: ID`

```
Premier League: 17
LaLiga: 8
Serie A: 23
Bundesliga: 35
Ligue 1: 34
Süper Lig: 52
Brasileirão Betano: 325
```

### Bir Sezonun Tüm Maçlarını Çekme

Belirli bir lig ve sezon için tüm maçları çekmek için:

1. Ana menüden "Maç İşlemleri"ni seçin (3)
2. "Maç Verilerini Çek" seçeneğini seçin (2)
3. Ligler listesinden hedef ligi seçin
4. Sezon filtreleme seçeneğinden "Belirli Bir Sezon" seçin (3)
5. Sezon listesinden istediğiniz sezonu seçin

**Python kodunda programatik olarak kullanım:**

```python
from src.config_manager import ConfigManager
from src.match_fetcher import MatchFetcher
from src.season_fetcher import SeasonFetcher

# Config yöneticisini başlat
config = ConfigManager()

# Sezon ve maç çekicilerini başlat
season_fetcher = SeasonFetcher(config)
match_fetcher = MatchFetcher(config, season_fetcher)

# Süper Lig (ID: 52) için aktif sezonu al
season_id = season_fetcher.get_current_season_id(52)

# Süper Lig'in aktif sezonu için tüm maçları çek
success = match_fetcher.fetch_matches_for_season(52, season_id)

if success:
    print("Tüm maçlar başarıyla çekildi!")
else:
    print("Maç çekme işlemi başarısız!")
```

### CSV Veri Seti Oluşturma

Çekilen maç verilerini CSV formatına dönüştürmek için:

1. Ana menüden "Maç Detayları"nı seçin (4)
2. "CSV Veri Seti Oluştur" seçeneğini seçin (3)
3. Dönüştürme seçeneklerinden birini seçin:
   - Tek Maç CSV
   - Belirli Bir Lig İçin CSV
   - Tüm Ligler İçin CSV

**Belirli bir lig için CSV veri seti oluşturma örneği:**

```python
from src.config_manager import ConfigManager
from src.match_data_fetcher import MatchDataFetcher

# Config yöneticisini başlat
config = ConfigManager()

# Maç veri çekicisini başlat
match_data_fetcher = MatchDataFetcher(config)

# Süper Lig (ID: 52) için CSV veri seti oluştur
csv_paths = match_data_fetcher.convert_league_matches_to_csv("52")

if csv_paths:
    print(f"CSV dosyaları oluşturuldu: {csv_paths}")
else:
    print("CSV oluşturma işlemi başarısız!")
```

### Maç Detaylarını Analiz Etme

Belirli bir maçın detaylarını çekmek ve analiz etmek için:

1. Ana menüden "Maç Detayları"nı seçin (4)
2. "Maç Detaylarını Çek" seçeneğini seçin (1)
3. Maç ID'sini girin (Maç ID'lerini "Maçları Listele" seçeneğinden bulabilirsiniz)

**Örnek: Bir maçın istatistiklerini Python'da analiz etme:**

```python
import json
from src.config_manager import ConfigManager
from src.match_data_fetcher import MatchDataFetcher

# Config yöneticisini başlat
config = ConfigManager()

# Maç veri çekicisini başlat
match_data_fetcher = MatchDataFetcher(config)

# Maç ID'si
match_id = "10257123"  # Örnek bir maç ID'si

# Maç detaylarını çek
success = match_data_fetcher.fetch_match_details(match_id)

if success:
    # Maç dizinini bul
    match_path = match_data_fetcher._find_match_path(match_id)
    if match_path:
        league_dir, season_dir, match_dir = match_path
        
        # İstatistikleri oku
        stats_file = os.path.join(match_dir, "statistics.json")
        with open(stats_file, 'r', encoding='utf-8') as f:
            stats = json.load(f)
        
        # İstatistikleri analiz et
        print(f"Maç ID: {match_id}")
        
        # Temel istatistikleri çıkar
        for period in stats.get("statistics", []):
            if period.get("period") == "ALL":
                print("\nMaç İstatistikleri:")
                for group in period.get("groups", []):
                    print(f"\n{group.get('groupName')}:")
                    for item in group.get("statisticsItems", []):
                        print(f"  - {item.get('name')}: Ev {item.get('homeValue')} - Deplasman {item.get('awayValue')}")
```

### Veri Analizi İçin Dışa Aktarma

Verileri Python analiz araçlarıyla (Pandas, NumPy, vb.) kullanmak için:

```python
import pandas as pd
import os
from src.config_manager import ConfigManager
from src.match_data_fetcher import MatchDataFetcher

# Config yöneticisini başlat
config = ConfigManager()

# Maç veri çekicisini başlat
match_data_fetcher = MatchDataFetcher(config)

# Süper Lig (ID: 52) için CSV dosyasını oluştur
csv_paths = match_data_fetcher.convert_league_matches_to_csv("52")

if csv_paths and csv_paths[0]:
    # İlk CSV dosyasını Pandas DataFrame'e yükle
    df = pd.read_csv(csv_paths[0])
    
    # Veri analizi
    print(f"Toplam maç sayısı: {len(df)}")
    print(f"Ev sahibi gol ortalaması: {df['home_score_ft'].mean():.2f}")
    print(f"Deplasman gol ortalaması: {df['away_score_ft'].mean():.2f}")
    
    # En çok gol atan takımlar
    home_goals = df.groupby('home_team_name')['home_score_ft'].sum()
    away_goals = df.groupby('away_team_name')['away_score_ft'].sum()
    
    # Toplam goller
    team_goals = pd.DataFrame({
        'Ev Golleri': home_goals,
        'Deplasman Golleri': away_goals
    }).fillna(0)
    
    team_goals['Toplam Goller'] = team_goals['Ev Golleri'] + team_goals['Deplasman Golleri']
    print("\nEn çok gol atan 5 takım:")
    print(team_goals.sort_values('Toplam Goller', ascending=False).head(5))
```

## ❓ Sık Sorulan Sorular (SSS)

### 1. Lig ID'sini nasıl bulabilirim?

SofaScore web sitesinde veya mobil uygulamasında, ligin URL'sine bakabilirsiniz. Örneğin, Süper Lig için URL `https://www.sofascore.com/tournament/football/turkey/super-lig/52` şeklindedir. Buradaki son sayı (52) lig ID'sidir.

Alternatif olarak, uygulama içinde "Lig Ara" özelliğini kullanarak isimle arama yapabilirsiniz.

### 2. Maç ID'sini nasıl bulabilirim?

Maç ID'lerini birkaç yöntemle bulabilirsiniz:
- Uygulamada "Maçları Listele" seçeneğini kullanarak
- SofaScore web sitesinde maç sayfasına giderek URL'den (örn: `https://www.sofascore.com/event/10257123`)
- Çektiğiniz maç verilerini içeren JSON dosyalarından

### 3. Rate-limiting hatalarıyla karşılaşıyorum. Ne yapmalıyım?

SofaScore API, kısa sürede çok fazla istek yapıldığında rate-limiting uygulayabilir. Bu durumda şunları deneyebilirsiniz:
- İstek sayısını azaltmak için `max_concurrent_requests` değerini düşürün
- Batch işlemler arasındaki bekleme süresini artırın
- Daha az veri çekerek başlayın ve zamanla artırın

### 4. Çekilen veriler nerede saklanır?

Tüm veriler `data/` dizini altında saklanır:
- Sezon verileri: `data/seasons/`
- Maç listeleri: `data/matches/`
- Maç detayları: `data/match_details/`
- CSV çıktıları: `data/match_details/processed/`

### 5. Farklı bir dilde çalıştırabilir miyim?

Şu anda uygulama Türkçe olarak geliştirilmiştir. Farklı diller için destek eklemeyi planlıyoruz.

## 🏗 Mimari ve Geliştirme

SofaScore Scraper, modüler bir mimari kullanılarak geliştirilmiştir:

### Ana Bileşenler

1. **ConfigManager**: Konfigürasyon yönetimi
2. **SeasonFetcher**: Sezon verilerini çekme ve yönetme
3. **MatchFetcher**: Maç listelerini çekme ve yönetme
4. **MatchDataFetcher**: Detaylı maç verilerini çekme ve işleme
5. **SofaScoreUI**: Ana kullanıcı arayüzü 
6. **UI Modülleri**: Farklı işlemler için özel UI sınıfları

### Veri Akışı

```
ConfigManager → SeasonFetcher → MatchFetcher → MatchDataFetcher → CSV/JSON Çıktılar
```

### API İstekleri

SofaScore API'si resmi olarak belgelenmemiştir. Bu proje, web sitesinin ve mobil uygulamanın kullandığı aynı API'leri kullanır:

```
https://www.sofascore.com/api/v1/...
```

### Paralel İşleme

Maç detayları çekilirken, işlem hızını artırmak için asenkron HTTP istekleri kullanılır. Bu, `aiohttp` kütüphanesi ile gerçekleştirilir.

### Geliştirici İçin Notlar

Kodu genişletmek veya değiştirmek isteyenler için:

- Yeni bir veri türü eklemek için `MatchDataFetcher` sınıfını genişletin
- Yeni bir UI modülü için `src/ui/` altında yeni bir sınıf oluşturun
- API davranışı değişirse `utils.py` içindeki `make_api_request` fonksiyonunu güncelleyin

## 🔍 Sorun Giderme

### Sık Karşılaşılan Sorunlar

1. **API Hataları**: SofaScore API'de değişiklikler olabileceğinden, güncellemeler gerekebilir.
2. **Rate Limiting**: Çok fazla istek gönderildiğinde API istek sınırlamalarına takılabilirsiniz.
3. **Veri Boşlukları**: Bazı maçlarda veya liglerde eksik veriler olabilir.

### Loglama

Hata mesajları `logs/` dizininde kaydedilir. Sorun yaşadığınızda logları kontrol edin.

### Temel Sorun Giderme Adımları

1. **Güncel Sürüm Kontrolü**: Projenin en son sürümünü kullandığınızdan emin olun
2. **Bağımlılık Kontrolü**: Tüm gerekli paketlerin doğru sürümlerle yüklendiğini kontrol edin
3. **Konfigürasyon Kontrolü**: Konfigürasyon dosyalarının doğru formatta olduğunu kontrol edin
4. **Log İncelemesi**: Hata mesajları için log dosyalarını inceleyin
5. **Ağ Kontrolü**: SofaScore API'ye erişim sağlanabiliyor mu kontrol edin

## 🤝 Katkıda Bulunma

Projeye katkıda bulunmak için:

1. Bu depoyu "fork"layın
2. Yeni bir dal oluşturun (`git checkout -b özellik/yenilik`)
3. Değişikliklerinizi commit edin (`git commit -m 'Yeni özellik eklendi'`)
4. Dalınızı push edin (`git push origin özellik/yenilik`)
5. Bir "Pull Request" açın

## 📄 Lisans

Bu proje MIT lisansı altında lisanslanmıştır. Detaylar için [LICENSE](LICENSE) dosyasına bakın.

---

Geliştirici: [Tunjay Orucov](https://github.com/tunjayoff)  
Sürüm: 1.0.0  
Son güncelleme: Mart 2024 