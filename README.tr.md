# SofaScore Scraper

Bu proje, SofaScore'dan futbol maçlarının verilerini çekmek, analiz etmek ve yönetmek için geliştirilmiş bir Python uygulamasıdır. Farklı ligler, sezonlar ve maçlar hakkında kapsamlı veri toplama, işleme ve dışa aktarma imkanı sunar.

<div align="center">
    
![SofaScore Scraper](https://img.shields.io/badge/SofaScore-Scraper-blue)
![Python](https://img.shields.io/badge/Python-3.8%2B-brightgreen)
![License](https://img.shields.io/badge/License-MIT-yellow)
    
</div>

For English documentation, please see [README.md](README.md).

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

- **Sezon İşlemleri**:
  - Liglere ait tüm sezonları çekme ve listeleme
  - Aktif sezonları otomatik tespit etme
  - Geçmiş ve gelecek sezonları yönetme

- **Maç Verileri**:
  - Belirli bir lig ve sezon için maç listelerini çekme
  - Haftalık/turlu maç verilerini görüntüleme
  - Tüm ligler için toplu maç verisi toplama

- **Maç Detayları**:
  - Maç istatistiklerini çekme
  - Takım serilerini görüntüleme
  - Maç öncesi form verilerini toplama
  - Karşılıklı istatistikleri (H2H) inceleme

- **Veri Dışa Aktarma**:
  - Maç verilerini CSV formatına dönüştürme
  - Lig bazlı veya tüm liglerin verilerini tek seferde dışa aktarma
  - Tek bir maçın detaylarını CSV formatında kaydetme

- **Çoklu Dil Desteği**:
  - Türkçe ve İngilizce dil seçenekleri
  - Uygulama içinden anlık dil değiştirme

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

### 4. Çevre Değişkenleri Yapılandırma

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
# Arayüz olmadan çalıştırma (headless mode)
python main.py --headless

# Tüm liglerin verilerini güncelleme
python main.py --headless --update-all

# Verileri CSV formatında dışa aktarma
python main.py --headless --csv-export
```

### Ana Menü

Uygulama başladığında karşınıza bir ana menü gelecektir:

```
SofaScore Scraper v1.0.0
==========================================

Ana Menü:
--------------------------------------------------
1. 🏆 Lig Yönetimi
2. 📅 Sezon Verileri
3. 📅 Fikstür & Sonuçlar
4. 📊 Maç İstatistikleri (Detaylı)
5. 📊 İstatistikler
6. ⚙️ Ayarlar
0. ❌ Çıkış

Seçiminiz (0-6): 
```

### Lig İşlemleri

1. **Ligleri Listele**: Kayıtlı ligleri görüntüler
2. **Yeni Lig Ekle**: Yeni bir lig ekler (Lig adı ve ID'si gereklidir)
3. **Lig Yapılandırmasını Yeniden Yükle**: Lig dosyasındaki değişiklikleri yükler
4. **Lig Ara**: Ligleri ada göre arar (Yeni!)
0. **Ana Menüye Dön**: Ana menüye geri döner

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

SofaScore Scraper, çevre değişkenleri ve lig yapılandırması olmak üzere iki temel yapılandırma yöntemi kullanır:

### 1. .env Dosyası

Proje, `.env` dosyası aracılığıyla çevre değişkenleri kullanarak konfigüre edilir. Uygulama ilk çalıştırıldığında otomatik olarak bir `.env` dosyası oluşturulur veya mevcut dosya kullanılır. Ayarlar menüsünden bu değişkenleri kolayca güncelleyebilirsiniz.

Örnek bir `.env` dosyası:

```
# API Yapılandırması
API_BASE_URL=https://www.sofascore.com/api/v1
REQUEST_TIMEOUT=20
MAX_RETRIES=3
MAX_CONCURRENT=25
WAIT_TIME_MIN=0.4
WAIT_TIME_MAX=0.8
USE_PROXY=false
PROXY_URL=

# Veri Yapılandırması
DATA_DIR=data
FETCH_ONLY_FINISHED=true
SAVE_EMPTY_ROUNDS=false

# Görüntüleme Ayarları
USE_COLOR=true
DATE_FORMAT=%Y-%m-%d %H:%M:%S

# Hata Ayıklama
LOG_LEVEL=INFO

DEBUG=false

# Dil Ayarı
LANGUAGE=tr
```

Ayarlar menüsünden şu yapılandırmaları değiştirebilirsiniz:
- API Yapılandırması (API URL, zaman aşımı, yeniden deneme sayısı, vb.)
- Veri Dizini (verilerin kaydedileceği konum)
- Görüntüleme Ayarları (renk kullanımı, tarih formatı)
- Dil Seçimi (Türkçe / İngilizce)
- Yedekleme ve Geri Yükleme işlemleri

### 2. Lig Yapılandırması

Lig bilgilerini `config/leagues.txt` dosyasında yönetebilirsiniz. Bu dosya, uygulamanın hangi ligleri takip edeceğini belirler:

```
# Format: Lig Adı: ID
Premier League: 17
LaLiga: 8
Serie A: 23
Bundesliga: 35
Ligue 1: 34
Süper Lig: 52
```

Lig işlemleri menüsünden ligleri ekleyebilir, düzenleyebilir veya kaldırabilirsiniz.

## 📂 Veri Yapısı

SofaScore Scraper, topladığı verileri aşağıdaki yapıda organize eder:

```
data/
├── seasons/
│   └── {lig_id}_{lig_adı}_seasons.json
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
                ├── basic.json
                ├── statistics.json
                ├── team_streaks.json
                ├── pregame_form.json
                ├── h2h.json
                └── lineups.json
```

### Veri Dosyaları

1. **seasons.json**: Bir lig için tüm sezonların listesi
2. **round_X.json**: Bir sezonun belirli bir turu/haftası için maçlar
3. **basic.json**: Maçın temel bilgileri (takımlar, skor, tarih, vb.)
4. **statistics.json**: Maç istatistikleri (şutlar, paslar, korneler, vb.)
5. **team_streaks.json**: Takımların seriler/istatistikleri
6. **pregame_form.json**: Maç öncesi takım formları
7. **h2h.json**: Takımlar arası karşılaşma geçmişi
8. **lineups.json**: Takım kadroları ve oyuncu bilgileri

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
Ana menüden "Lig İşlemleri" seçip "Lig Ekle" seçeneğini kullanabilirsiniz.

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

**Belirli bir lig için CSV veri seti oluşturma adımları:**

1. "Belirli Bir Lig İçin CSV" seçeneğini seçin
2. Görüntülenen lig listesinden, istediğiniz ligin numarasını girin
3. CSV dosyaları oluşturulduktan sonra ekranda dosya yolları görüntülenecektir

**Programlama ile CSV veri seti oluşturma örneği:**

```python
from src.config_manager import ConfigManager
from src.match_data_fetcher import MatchDataFetcher

# Config yöneticisini başlat
config = ConfigManager()

# Maç veri çekicisini başlat
match_data_fetcher = MatchDataFetcher(config)

# Süper Lig için CSV veri seti oluştur (ID ile)
csv_paths = match_data_fetcher.convert_league_matches_to_csv(52)

# veya lig adı ile de çalışabilir
# csv_paths = match_data_fetcher.convert_league_matches_to_csv("Süper Lig")

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
import os
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

SofaScore web sitesinde, ligin URL'sine bakabilirsiniz. Örneğin, Süper Lig için URL `https://www.sofascore.com/tournament/football/turkey/super-lig/52` şeklindedir. Buradaki son sayı (52) lig ID'sidir.

### 2. Maç ID'sini nasıl bulabilirim?

Maç ID'lerini birkaç yöntemle bulabilirsiniz:
- Uygulamada "Maçları Listele" seçeneğini kullanarak
- Çektiğiniz maç verilerini içeren JSON dosyalarından

### 3. Rate-limiting hatalarıyla karşılaşıyorum. Ne yapmalıyım?

SofaScore API, kısa sürede çok fazla istek yapıldığında rate-limiting uygulayabilir. Bu durumda şunları deneyebilirsiniz:
- `.env` dosyasında `MAX_CONCURRENT` değerini düşürün (örneğin 10'a)
- `WAIT_TIME_MIN` ve `WAIT_TIME_MAX` değerlerini artırın
- Daha az veri çekerek başlayın ve zamanla artırın

### 4. Çekilen veriler nerede saklanır?

Tüm veriler varsayılan olarak `data/` dizini altında saklanır (`.env` dosyasında `DATA_DIR` değişkeni ile değiştirilebilir):
- Sezon verileri: `data/seasons/`
- Maç listeleri: `data/matches/`
- Maç detayları: `data/match_details/`
- CSV çıktıları: `data/match_details/processed/`

### 5. Farklı bir dilde çalıştırabilir miyim?

Şu anda uygulama Türkçe olarak geliştirilmiştir. Farklı diller için destek eklemeyi planlıyoruz.

## 🏗 Mimari ve Geliştirme

SofaScore Scraper, modüler bir mimari kullanılarak geliştirilmiştir:

### Ana Bileşenler

1. **ConfigManager**: Konfigürasyon yönetimi ve çevre değişkenleri (.env dosyası)
2. **SeasonFetcher**: Sezon verilerini çekme ve yönetme
3. **MatchFetcher**: Maç listelerini çekme ve yönetme
4. **MatchDataFetcher**: Detaylı maç verilerini çekme ve işleme
5. **SofaScoreUI**: Ana kullanıcı arayüzü 
6. **UI Modülleri**: Farklı işlemler için özel UI sınıfları

### Veri Akışı

```
ConfigManager → SeasonFetcher → MatchFetcher → MatchDataFetcher → CSV/JSON Çıktılar
```

### Yapılandırma Yönetimi

Uygulama, yapılandırma için çevre değişkenlerini (.env dosyası) kullanır:

1. **Çevre Değişkenleri**: API URL, zaman aşımı, yeniden deneme sayısı, veri dizini gibi temel ayarlar
2. **Lig Yapılandırması**: Takip edilecek ligler ve ID'leri (leagues.txt dosyası)

ConfigManager sınıfı, bu yapılandırma kaynaklarını yönetir ve uygulamanın diğer bileşenlerine erişim sağlar.

### API İstekleri

SofaScore API'si resmi olarak belgelenmemiştir. Bu proje, web sitesinin ve mobil uygulamanın kullandığı aynı API'leri kullanır:

```
https://www.sofascore.com/api/v1/...
```

### Paralel İşleme

Maç detayları çekilirken, işlem hızını artırmak için asenkron HTTP istekleri kullanılır. Bu, `aiohttp` kütüphanesi ile gerçekleştirilir ve `.env` dosyasındaki `MAX_CONCURRENT` değişkeni ile kontrol edilebilir.

### Geliştirici İçin Notlar

Kodu genişletmek veya değiştirmek isteyenler için:

- Yeni bir veri türü eklemek için `MatchDataFetcher` sınıfını genişletin
- Yeni bir UI modülü için `src/ui/` altında yeni bir sınıf oluşturun
- API davranışı değişirse `utils.py` içindeki `make_api_request` fonksiyonunu güncelleyin
- Yeni çevre değişkenleri eklemek için `ConfigManager` sınıfını ve `.env.example` dosyasını güncelleyin

## 🔍 Sorun Giderme

### Sık Karşılaşılan Sorunlar

1. **API Hataları**: SofaScore API'de değişiklikler olabileceğinden, güncellemeler gerekebilir.
2. **Rate Limiting**: Çok fazla istek gönderildiğinde API istek sınırlamalarına takılabilirsiniz.
3. **Veri Boşlukları**: Bazı maçlarda veya liglerde eksik veriler olabilir.

### Loglama

Hata mesajları `logs/` dizininde kaydedilir. Sorun yaşadığınızda logları kontrol edin. Log seviyesi `.env` dosyasındaki `LOG_LEVEL` değişkeni ile kontrol edilebilir.

### Temel Sorun Giderme Adımları

1. **Güncel Sürüm Kontrolü**: Projenin en son sürümünü kullandığınızdan emin olun
2. **Bağımlılık Kontrolü**: `requirements.txt` dosyasındaki tüm paketlerin doğru sürümlerle yüklendiğini kontrol edin
3. **Çevre Değişkenleri Kontrolü**: `.env` dosyasının doğru yapılandırıldığından emin olun
4. **Log İncelemesi**: Hata mesajları için `logs/` dizinindeki dosyaları inceleyin
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

Geliştirici: [Tuncay Eşsiz](https://github.com/tunjayoff)  
Sürüm: 1.1.0  
Son güncelleme: 26 Aralık 2025 