# SofaScore Scraper

**English:** [README.md](README.md)

SofaScore’un herkese açık HTTP API’lerinden futbol maç verisi indiren, yerelde (JSON ve CSV) saklayan ve **terminal** veya **web** arayüzüyle sunan Python uygulaması.

Bu proje SofaScore ile bağlantılı değildir. İstek hızına dikkat edin ve ilgili kullanım koşullarına uyun.

## Özellikler

- **Ligler** — Turnuva ID yapılandırması; web’de lig eklerken uzaktan arama.
- **Sezon ve fikstür** — Sezon listesi ve maç listeleri; lig, sezon, tarih filtreleri.
- **Maç detayları** — İstatistik, kadro, olaylar, H2H vb. JSON dilimleri; isteğe bağlı paralel çekim, ilerleme ve iptal (web).
- **Web arayüzü** — Pano, ligler, fikstür (sihirbazlı çekim), maç sayfası, istatistikler, ayarlar (`.env` tabanlı, yedek/geri yükleme/temizleme), arka plan işlem durumu (SSE).
- **Terminal arayüzü** — Tarayıcı olmadan etkileşimli menü.
- **Otomasyon** — CI/script için headless bayrakları (`--update-all`, `--fetch-mode`, `--league-id`, `--csv-export`, yollar).
- **Dışa aktarım** — İşlenmiş “tüm maçlar” CSV’si ve API üzerinden export.

## Gereksinimler

- Python **3.10+** (önerilen: 3.11+).
- **Git** — `curl | bash` ile tek satır kurulum için gerekli (depoyu klonlar); elle indiriyorsanız isteğe bağlı.
- SofaScore’a ağ erişimi.

## Kurulum

### Hızlı kurulum (betik)

Resmi depo: [github.com/tunjayoff/sofascore_scraper](https://github.com/tunjayoff/sofascore_scraper).

**Linux / macOS / Git Bash** — depoyu zaten klonladıysanız:

```bash
chmod +x scripts/install.sh   # bir kez
./scripts/install.sh
```

**Tek satır** (depoyu klonlar, `.venv` kurar, bağımlılıkları yükler, `.env` oluşturur):

```bash
curl -fsSL https://raw.githubusercontent.com/tunjayoff/sofascore_scraper/main/scripts/install.sh | bash
```

- İsteğe bağlı **ilk argüman** (URL değilse): hedef klasör adı (varsayılan `sofascore_scraper`); veya `SOFASCORE_SCRAPER_DIR`.
- Başka bir çatalla varsayılan kaynak: `export SOFASCORE_SCRAPER_REPO=https://github.com/SIZ/fork.git` ardından yukarıdaki `curl | bash`, veya tam git URL’sini `bash -s` ile verin:  
  `curl ... | bash -s -- https://github.com/SIZ/fork.git [klasör]`
- Gerekirse: `SOFASCORE_SCRAPER_DEFAULT_REPO`.

**Windows** — PowerShell (klon gerekirse betik kendisi yapar):

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned   # gerekirse, bir kez
Invoke-RestMethod https://raw.githubusercontent.com/tunjayoff/sofascore_scraper/main/scripts/install.ps1 | Invoke-Expression
```

veya klon sonrası:

```powershell
.\scripts\install.ps1
```

Açıkça adres / klasör:

```powershell
.\scripts\install.ps1 -RepoUrl https://github.com/tunjayoff/sofascore_scraper.git -InstallDir sofascore_scraper
```

CMD: `scripts\install.bat`. Ortam: `SOFASCORE_SCRAPER_REPO`, `SOFASCORE_SCRAPER_DIR`, `SOFASCORE_SCRAPER_DEFAULT_REPO`.

**Önkoşullar:** **Git** (tek satır / klon yolu için), **PATH** üzerinde **Python 3.10+**. Betikler `git`, `python`, `venv` veya `pip` hata verirse anlaşılır Türkçe/İngilizce iletir (ör. Ubuntu’da `python3-venv` eksikliği).

### Elle kurulum

```bash
git clone https://github.com/tunjayoff/sofascore_scraper.git
cd sofascore_scraper
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Örnek ortam dosyasını kopyalayıp düzenleyin:

```bash
cp .env.example .env
```

## Yapılandırma

### Ortam değişkenleri (`.env`)

Tüm anahtarlar `.env.example` içinde. Sık kullanılanlar:

| Değişken | Açıklama |
|----------|----------|
| `DATA_DIR` | Verinin kök dizini (varsayılan `data`). Web `ConfigManager` üzerinden okur. |
| `LANGUAGE` | Arayüz dili: `en` veya `tr`. |
| `MAX_CONCURRENT` | Paralel detay isteği üst sınırı. |
| `USE_PROXY` / `PROXY_URL` | İsteğe bağlı proxy. |
| `FETCH_ONLY_FINISHED` | Liste çekerken tercihen bitmiş maçlar. |
| `RATE_LIMIT_*` / `SERVER_ERROR_*` | Çok hata durumunda devreye giren eşikler. |

Web **Ayarlar** sayfasından birçok değer düzenlenir; kayıt `.env`’i günceller.

### Lig listesi (`config/leagues.txt`)

Her satırda SofaScore **unique tournament** sayısal ID’si ve görünen ad (uygulama bu formatı yönetir). ID, SofaScore turnuva URL’sinde yer alır (ör. `.../premier-league/17` → `17`).

CLI ile özel yol:

```bash
python main.py --config /yol/leagues.txt --data-dir /yol/veri
```

`--config` ve `--data-dir` yalnızca **etkileşimli** ve **headless** modda geçerlidir. Web sunucusu projedeki `.env` ile tekil `ConfigManager` kullanır; hem CLI hem web kullanacaksanız `DATA_DIR` vb. ile aynı veri dizinine hizalayın.

## Kullanım

### Nasıl kullanılır? (hızlı başlangıç)

**Web (çoğu kullanıcı için uygun)**

1. **Kurulum** ve **Yapılandırma** adımlarını tamamlayın (`pip install`, `cp .env.example .env`). İsterseniz `LANGUAGE=tr` veya `en` ve veriyi başka yere almak için `DATA_DIR` ayarlayın.
2. Sunucuyu başlatın: `python main.py --web`, tarayıcıda `http://127.0.0.1:8000` açın.
3. **Ligler** — En az bir turnuva ekleyin: SofaScore üzerinden arama veya turnuva URL’sindeki sayısal ID ile kayıt.
4. **Fikstür** — Lig (ve gerekirse sezon) seçin. **Çek / Fetch** sihirbazıyla lig ve sezonları seçip tam senkron veya sadece detay çekimini başlatabilirsiniz; tek tuşlu geniş güncellemeler de vardır.
5. İndirme sürerken **ilerleme** kartı görünür; sayfalar arasında genelde gezinebilirsiniz. Takılma olursa **Ayarlar → performans** (eşzamanlılık, zaman aşımı) ve günlüklere bakın.
6. **Maç satırına** tıklayarak detay sayfasına gidin. Veri eksikse sayfadaki aksiyonlar veya fikstürden tekrar **detay** çekimi kullanılabilir.
7. **İstatistikler** özet ve kapsam gösterir; **Ayarlar** `.env` ile uyumlu sekmeler (genel, ağ, performans, veri araçları). Riskli temizlik öncesi yedek alın.

**Terminal menüsü**

`python main.py` ile numaralı menülerden ilerleyin: lig, sezon, maç listesi, detay, istatistik, CSV. Web’deki sihirbazın karşılığı yok; istemlerle lig ve seçenek belirlersiniz.

**İpuçları**

- Büyük ligde ilk **tam** çekim uzun sürebilir; önce tek lig ve sihirbazla az sayıda sezon deneyin.
- Hız sınırı veya çok hata görürseniz **Ayarlar**’dan **MAX_CONCURRENT** düşürüp bekleme sürelerini hafif artırın; `--ignore-rate-limit` yalnız bilinçli kullanımda.
- **Web** ile **CLI/headless** aynı veriyi paylaşacaksa `.env` içindeki `DATA_DIR` ile komut satırındaki `--data-dir` değerini hizalayın.

### Etkileşimli terminal

```bash
python main.py
```

### Web uygulaması

```bash
python main.py --web
```

Varsayılan adres: `http://127.0.0.1:8000` (sunucu `0.0.0.0:8000` dinler). Sağlık kontrolü: `GET /health`.

Arka plan işlemleri `GET /api/scrape/status` ve `GET /api/scrape/stream` (SSE) ile izlenir. Ağır dosya/pandas işleri event loop dışına alındığından uzun çekimler sırasında arayüz genelde yanıt vermeye devam eder.

### Headless / otomasyon

`--headless` ile birlikte **`--update-all` ve/veya `--csv-export`** zorunludur; aksi halde çıkış kodu **2** olur.

| Bayrak | Anlamı |
|--------|--------|
| `--headless` | Menüsüz çalışma |
| `--update-all` | Çekim akışını çalıştır |
| `--fetch-mode full` | Sezon + maç listeleri + detay (varsayılan) |
| `--fetch-mode details` | Yalnız maç detayları (mevcut özet/fikstür CSV’lerine dayanır) |
| `--league-id ID` | `--update-all`’ı tek yapılandırılmış lige indir |
| `--csv-export` | İşlenmiş CSV veri setini üret/aktar |
| `--ignore-rate-limit` | Circuit breaker’ı kapatır (dikkatli kullanın) |

Örnekler:

```bash
python main.py --headless --update-all
python main.py --headless --update-all --fetch-mode details --league-id 52
python main.py --headless --csv-export --data-dir ./data
```

Çıkış kodları: **0** başarı (veya scraper’ın set ettiği `APP_EXIT_CODE`), **1** beklenmeyen hata, **2** headless’te işlem belirtilmedi.

### Yardım

```bash
python main.py --help
```

## Veri yapısı (`DATA_DIR` altında)

Tipik düzen:

```text
data/
├── seasons/           # Lig başına sezon meta dosyaları
├── matches/           # Lig ve sezona göre maç / özet CSV
├── match_details/     # Maç başına JSON (basic, statistics, …)
│   └── processed/     # Birleştirilmiş CSV export
└── datasets/          # Yardımcı / ayrılmış kullanım
```

Lig adlandırma ve migrasyonlara göre alt yollar biraz farklı olabilir.

## REST API (özet)

HTML sayfaları kök yollarla; JSON API öneki **`/api`**.

- **Ligler**: listele, ekle, sil, ara (yerel/uzak), sezonlar, sezon yenileme, eksik detay listesi.
- **Maçlar**: sayfalı fikstür, tek maç JSON, tek maç çekme.
- **Scraper**: `POST /api/fetch` (gövde: `full` | `details`, isteğe bağlı lig ve sihirbaz `selections`), iptal, durum, SSE akışı.
- **Pano / istatistik / ayarlar**: Web panellerine JSON; ayarlar `.env` ile uyumlu.
- **Veri**: yedek zip, kapsam seçerek temizleme, CSV export.

Sunucu çalışırken OpenAPI: `GET /docs`.

## Geliştirme

Web’i doğrudan uvicorn ile:

```bash
uvicorn src.web.app:app --reload --host 0.0.0.0 --port 8000
```

## Katkıda bulunma

Katkılarınızı memnuniyetle karşılıyoruz. Şu şekillerde destek olabilirsiniz:

- **Hata bildirimi** — Sorunu yeniden üreten adımlar, beklenen / gerçek davranış, işletim sistemi ve Python sürümü ile ilgili `.env` anahtarlarını (gizli bilgi paylaşmadan) bir issue’da paylaşın.
- **Özellik önerisi** — Kullanım senaryosu ve kısıtları yazın; bakıcılar kapsamı issue üzerinde değerlendirebilir.
- **Pull request** — Repo’yu fork’layın, odaklı bir dal kullanın, değişiklikleri küçük ve tek konuda tutun; PR’da *ne* ve *neden* olduğunu açıklayın. Mevcut kod stiline uyun; gereksiz geniş refaktörden kaçının. Kullanıcıya dönük metin değiştiriyorsanız `locales/en.json` ve `locales/tr.json` dosyalarını güncellemeyi düşünün.
- **Dokümantasyon ve çeviri** — Bu README’ler veya yerelleştirme metinleri için iyileştirmeler değerlidir.

Katkılarınız MIT lisansı ile uyumlu kabul edilir; ayrı bir katılım sözleşmesi yoktur. Issue ve inceleme süreçlerinde saygılı iletişim rica edilir. Fikrin uyarlılığından emin değilseniz önce issue açmak iyi bir başlangıçtır.

## Lisans

MIT — ayrıntılar için [LICENSE](LICENSE) dosyasına bakın.
