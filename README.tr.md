# SofaScore Scraper

[![CI](https://github.com/tunjayoff/sofascore_scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/tunjayoff/sofascore_scraper/actions/workflows/ci.yml)

**English:** [README.md](README.md)

SofaScore’un herkese açık HTTP API’lerinden futbol, basketbol ve tenis maç verisi indiren, yerelde (JSON ve CSV) saklayan ve **web** veya **terminal** arayüzüyle sunan Python uygulaması.

Bu proje SofaScore ile bağlantılı değildir. İstek hızına dikkat edin ve ilgili kullanım koşullarına uyun.

## Özellikler

- **Üç spor** — Futbol, basketbol ve tenis. Her lig kendi sporunu hatırlar; web uygulamasının tamamı tek bir spora göre süzülebilir.
- **Ligler** — SofaScore’da arayarak (web) veya ID ile (`config/leagues.txt`) turnuva ekleme.
- **Sezonlar ve maçlar** — Bir veya birden fazla ligden sezon seçip tek seferde indirme; maçlara lig, sezon, tarih ve detayın inip inmediğine göre göz atma.
- **Maç detayları** — İstatistikler (periyot bazında), olaylar, kadrolar, aralarındaki maçlar ve form; spora göre yarı, çeyrek veya set bazında skor.
- **Web uygulaması** — Ligler, Maç indir, Maçlar, Etkinlik ve Ayarlar sayfaları; canlı ilerleme (SSE) ve anında etki eden Durdur; Türkçe ve İngilizce; açık, koyu veya sisteme uyan tema.
- **Terminal arayüzü** — Tarayıcı olmadan etkileşimli menü.
- **Otomasyon** — CI/script için headless bayrakları (`--update-all`, `--fetch-mode`, `--league-id`, `--csv-export`, yollar).
- **Dışa aktarım** — İşlenmiş “tüm maçlar” CSV’si ve API üzerinden export.

## Gereksinimler

- Python **3.10+** (önerilen: 3.11+).
- **Node.js 20.19+ veya 22.12+ ve npm** — web uygulamasını (`frontend/`) derlemek için. `scripts/start_web.py` ilk çalıştırmada kendisi derler.
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

Betiği çalıştırmadan önce okumak isterseniz önce indirin:

```bash
curl -fsSLo install.sh https://raw.githubusercontent.com/tunjayoff/sofascore_scraper/main/scripts/install.sh
less install.sh && bash install.sh
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
python -m playwright install chromium   # yalnızca Google Chrome kurulu değilse gerekir
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
| `APP_LANGUAGE` | `en` veya `tr`: terminal arayüzünün ve sunucu mesajlarının dili. Web uygulamasının dili **Ayarlar**’dan seçilir (orada değiştirmek bu değeri de günceller). |
| `MAX_CONCURRENT` | Paralel detay isteği üst sınırı. |
| `USE_PROXY` / `PROXY_URL` | İsteğe bağlı proxy. |
| `FETCH_ONLY_FINISHED` | Yalnız bitmiş maçları tut (`status.type == finished`). Varsayılan `true`. Henüz oynanmamış fikstürler schedule dosyalarına yazılmaz. |
| `REFRESH_WINDOW_HOURS` | Kaydedilen maçın başlangıçtan kaç saat boyunca geçici sayılıp yeniden okunacağı (varsayılan `72`, `0` = kapalı). Bkz. [Yenileme politikası](#yenileme-politikası). |
| `RATE_LIMIT_*` / `SERVER_ERROR_*` | Çok hata durumunda devreye giren eşikler. |

Web **Ayarlar** sayfasından birçok değer düzenlenir; kayıt `.env`’i günceller.

### Lig listesi (`config/leagues.txt`)

Her satır `Ad: ID` biçimindedir; ID, SofaScore **unique tournament** sayısal ID’sidir (turnuva URL’sinde yer alır, ör. `.../premier-league/17` → `17`). Dosya size aittir ve git’te takip edilmez: ilk çalıştırmada `config/leagues.example.txt`’den oluşturulur. Web uygulamasında veya CLI’da lig ekleyip kaldırdığınızda satırları uygulama kendisi günceller.

CLI ile özel yol:

```bash
python main.py --config /yol/leagues.txt --data-dir /yol/veri
```

`--config` ve `--data-dir` yalnızca **etkileşimli** ve **headless** modda geçerlidir. Web sunucusu projedeki `.env` ile tekil `ConfigManager` kullanır; hem CLI hem web kullanacaksanız `DATA_DIR` vb. ile aynı veri dizinine hizalayın.

## Kullanım

### Nasıl kullanılır? (hızlı başlangıç)

**Web (çoğu kullanıcı için uygun)**

1. **Kurulum** ve **Yapılandırma** adımlarını tamamlayın (`pip install`, `cp .env.example .env`). Veriyi `./data` dışında tutmak isterseniz `DATA_DIR` ayarlayın.
2. Uygulamayı başlatın: `./start-sofascore.sh` (veya `python scripts/start_web.py`). İlk çalıştırmada web uygulamasını derler, sonra `http://127.0.0.1:8000` adresini açar. Sadece sunucu için: `python main.py --web`.
   Kodu güncelledikten sonra (`git pull`) web uygulamasını kendiniz yeniden derleyin: `cd frontend && npm install && npm run build`. Başlatma betiği sadece `frontend/dist/` yoksa derler; aksi halde eski arayüzü görmeye devam edersiniz.
3. **Spor** — Kenar menünün üstündeki seçici (Tümü / Futbol / Basketbol / Tenis) bütün sayfaları süzer. Üzerinde çalıştığınız sporu seçin.
4. **Ligler** — **Lig ekle** SofaScore’da arar; sonuçları spora göre süzüp **Ekle**’ye basın. Sporu bilinmeyen bir ligde (örneğin `config/leagues.txt`’ye elle eklenmiş) **Spor seç** kutusu çıkar; bir kez seçmeniz yeterli, kaydedilir.
5. **Maç indir** — Sol sütunda lig seçin, ortada sezonları işaretleyin (sezon listesi ilk seferde kendiliğinden gelir; **Son sezon** / **Son 3 sezon** kısayolları vardır). Birden fazla ligden sezon seçebilirsiniz; hepsi sağdaki **İndirme listesi**’nde toplanır. **N sezonu indir**’e basın. Maçlar ve maç detayları (istatistik, olaylar, kadrolar) birlikte indirilir.
6. İndirme sürerken kenar menünün altındaki kart ilerlemeyi gösterir; **Durdur** hemen etki eder: yeni istek gönderilmez, yeniden deneme beklemeleri kesilir; o an havada olan bir istek zaman aşımı (`REQUEST_TIMEOUT`) kadar sürebilir. Aynı anda tek indirme çalışır. **Etkinlik** şimdiki ve geçmiş indirmeleri listeler.
7. **Maçlar** — Lig, sezon, tarih ve **Detay** (olanlar / eksikler) ile süzün. Bir ligde detayı eksik maç varsa (genelde yarıda durdurulan bir indirmeden kalır) üstte **Eksikleri indir** şeridi çıkar. Bir satıra tıklayınca maç açılır: periyot skorları, özet, istatistikler, olaylar ve kadrolar.
8. **Ayarlar** — Dil ve tema; veri klasörü, disk kullanımı, **Yedek al** ve **Tüm veriyi sil**; gelişmiş istek ayarları (zaman aşımı, eşzamanlılık, bekleme süreleri, deneme sayısı).

> **Tüm veriyi sil** indirilmiş bütün sezon, maç ve detayları siler ve geri alınamaz. Önce yedek alın. Yedek, `data/backups/` (yani `DATA_DIR` içi) altında `data/`, `leagues.txt` ve `league_sports.json` içeren bir zip’tir. `.env` proxy kimlik bilgisi içerebileceği için dahil edilmez; isterseniz `POST /api/data/backup` isteğine `?include_env=true` ekleyin. Geri yüklemek için uygulamayı durdurun, `data/` klasörünü proje klasörüne (veya `DATA_DIR`’e) açın; `leagues.txt` ve `league_sports.json`’ı da geri istiyorsanız `config/` altına kopyalayın.

**Terminal menüsü**

`python main.py` ile numaralı menülerden ilerleyin: lig, sezon, maç listesi, detay, istatistik, CSV. Web’deki Maç indir sayfasının karşılığı yok; istemlerle lig ve seçenek belirlersiniz.

**İpuçları**

- Büyük ligde ilk indirme uzun sürebilir; önce tek lig ve az sayıda sezonla deneyin.
- Hız sınırı veya çok hata görürseniz **Ayarlar**’dan **MAX_CONCURRENT** düşürüp bekleme sürelerini hafif artırın; `--ignore-rate-limit` yalnız bilinçli kullanımda.
- **Web** ile **CLI/headless** aynı veriyi paylaşacaksa `.env` içindeki `DATA_DIR` ile komut satırındaki `--data-dir` değerini hizalayın.
- Mümkünse içinde bitmiş maç olan sezonu seçin. En yeni etiket (ör. Avrupa `26/27`) çoğu zaman yalnızca fikstürdür; scraper gerekirse otomatik düşer.
- İndirmeyi durdurmak o ana kadar inenleri korur. Detayına sıra gelmeyen maçlar Maçlar’da **Detay: Yok** görünür; Maçlar’daki (veya Maç indir’deki) **Eksikleri indir** ile tamamlanır.

### Sorun giderme

**Sezonlar görünüyor ama çekim 0 maç buluyor** (`İşlenecek maç verisi bulunamadı` / `0it`)

1. SofaScore URL’sindeki **unique tournament** ID’sini doğrulayın (MLS için **`242`** — doğrudur).
2. Her lig sıralı hafta programı (`events/round/1..N`) sunmaz. **Premier League** tarzı ligler sunar; **MLS** ve bazıları sunmaz:
   - MLS `/rounds` boş veya yalnızca playoff tarzı ID’ler (ör. `227`) döner; `events/round/1` boş gelir.
   - Bu sezonlarda scraper sayfalı **`events/last` + `events/next`** kullanmalıdır.
   - Yalnız `1..50` hafta tarayan eski sürümler PL’yi indirir ama **MLS’te 0 maç** döner. Event-list yedeklemesini içeren sürüme güncelleyin, sezonları yenileyin ve çekimi tekrarlayın.
3. `FETCH_ONLY_FINISHED=true` (varsayılan) iken oynanmamış maçlar atılır. Yeni sezonda henüz bitmiş maç yoksa önceki sezonu seçin (veya bilerek fikstür istiyorsanız `FETCH_ONLY_FINISHED=false`).
4. Eski/retired sezon ID’leri de boş schedule üretir — ligi **Maç indir** sayfasında açıp sezonların üstündeki **Yenile**’ye basın, sonra tekrar indirin.

### Etkileşimli terminal

```bash
python main.py
```

### Web uygulaması

```bash
python main.py --web
```

Varsayılan adres: `http://127.0.0.1:8000`. Sunucu yalnızca bu bilgisayarı dinler. `--host 0.0.0.0` onu ağa açar ve **giriş yoktur**: erişebilen herkes ayarları değiştirip veriyi silebilir. `--port` portu değiştirir, `--dev` kod değişince yeniden başlatır. Sağlık kontrolü: `GET /health`.

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
| `--refresh-only` | Yalnızca geçici kayıtları yeniden okur (`--headless` gerekmez); bkz. [Yenileme politikası](#yenileme-politikası) |
| `--refresh-legacy` | `observation.json` öncesi kaydedilmiş maçları da bir kez yeniler |

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
├── datasets/          # Yardımcı / ayrılmış kullanım
└── score_changes.jsonl  # Yenilemede bulunan bitiş sonrası değişiklikler (bkz. Yenileme politikası)
```

Lig adlandırma ve migrasyonlara göre alt yollar biraz farklı olabilir.

Maç listesi `matches/` altındaki sezon özetlerinden okunur; `match_details/processed/` içindeki export CSV yalnızca hiç özet yoksa yedek olarak kullanılır.

`config/leagues.txt`’nin (CLI’ın da okuduğu `ad: id` listesi) yanında `config/league_sports.json` her ligin sporunu `{"<id>": "football" | "basketball" | "tennis"}` olarak saklar. Lig web’den eklendiğinde, arayüzde spor seçildiğinde veya o ligin indirilmiş bir maçından doldurulur.

## Yenileme politikası

SofaScore bazı sonuçları maç bittikten sonra da düzenliyor. Araştırma ölçümünde (`docs/status-matrix/README.md`, "Geriye dönük") nihai ya da periyot skoru `finished` sonrasında değişti: alt lig basketbolda 255 maçın 78'inde, alt lig futbolda 240 maçın 6'sında, üst lig basketbolda 145 maçın 4'ünde. En geç nihai skor değişikliği başlangıçtan 66,4 sa sonra geldi. Bu yüzden bir kez indirilen maç, SofaScore'un son hâlinden farklı kalabilir.

- **Kayıt ne zaman yenilenir?** Kaydedilen her maçta `basic.json` yanında bir `observation.json` bulunur. Bu dosya bizim okuduğumuz anı (`observed_at_utc`) ve SofaScore'un `changes.changeTimestamp` değerini tutar. `observed_at_utc < startTimestamp + REFRESH_WINDOW_HOURS` (varsayılan **72**) olduğu sürece kayıt *geçici* sayılır. Geçici kayıtlar sonraki indirmede (web işi, `--update-all` ya da `--refresh-only`) yeniden okunur. Bu sırada yalnızca `/event/{id}` çekilir, istatistik ve kadro çekilmez. Yenilemeler yeni ve eksik maçlardan sonra çalışır, iş kartında da ayrı sayılır ("N yenilendi (M değişti)"). Aynı kayıt en fazla `REFRESH_MIN_INTERVAL_HOURS` (varsayılan 6, ileri düzey `.env` ayarı) saatte bir yeniden okunur; saatlik indirmede aynı maç 72 kez çekilmez. Pencere kapandıktan sonra yapılan bir okumayla kayıt kesinleşir ve bir daha çekilmez.
- **Eski kayıtlar.** Bu özellikten önce kaydedilen maçlarda `observation.json` yok. Bunlar kesin sayılır, yani varsayılan ayar mevcut veri için **hiç** ek istek yapmaz. `--refresh-legacy` bu kayıtların her birini bir kez yeniden okur.
- **Kapatma.** `REFRESH_WINDOW_HOURS=0` (`.env` ya da **Ayarlar**).
- **Değişiklik kaydı.** Status üçlüsü, `winnerCode`, `homeScore`/`awayScore` alt alanlarından biri ya da `startTimestamp` farklıysa `basic.json` üzerine yazılır. Ayrıca `DATA_DIR/score_changes.jsonl` dosyasına (git'e girmez) bir satır eklenir. Satır biçimi için İngilizce README'deki örneğe bakın.
  - `changed`: her alanın eski ve yeni değeri. SofaScore'un `changes` alanı yalnızca değişen alanın adını verir; skorun *ne kadar* değiştiği ilk kez bu dosyada kayda geçer.
  - `hours_after_start`: yeni `changeTimestamp` eksi başlangıç.
  - `tier_hint`: SofaScore'un oyuncu istatistiği kapsam bayrağı. Ligin gerçek seviyesini değil, SofaScore'un kapsam seviyesini gösterir.
  - Oynanmış sayılan bir maç sonradan iptale dönerse (`completed` → `void`) satıra `"status_regressed": true` yazılır. Aynı işaret `observation.json`'a da konur. Kayıt **silinmez**; silme kararı kullanıcınındır.

```bash
python main.py --refresh-only                 # yalnızca geçici kayıtları yenile (ör. günlük cron)
python main.py --refresh-only --league-id 17  # tek lig
python main.py --refresh-only --refresh-legacy
```

## REST API (özet)

Web uygulaması kök yollarda; JSON API öneki **`/api`**.

- **Ligler**: listele (her ligde `sport`), ekle (isteğe bağlı `sport`), sporu ayarlamak için `PATCH /api/leagues/{id}`, sil, ara (yerel/uzak; uzak sonuçlarda `sport`), sezonlar, sezon yenileme, eksik detay listesi.
- **Maçlar**: `GET /api/matches` — sayfalı; filtreler `league_id` (tek ID ya da virgülle birden fazla, ör. `17,8`), `season_id`, `date`, `details=present|missing`, `sort=asc|desc`; her satırda `has_details`. Ayrıca tek maç JSON ve tek maç çekme.
- **Scraper**: `POST /api/fetch` (gövde: `full` | `details`, `selections: [{league_id, season_ids, match_ids}]`), `POST /api/scrape/cancel` (sonrasında yeni istek gönderilmez, yeniden deneme beklemeleri kesilir), durum, SSE akışı.
- **Pano / istatistik / ayarlar**: Web panellerine JSON; ayarlar `.env` ile uyumlu.
- **Veri**: yedek zip, kapsam seçerek temizleme, CSV export.
- **Bypass Durumu**: `GET /api/bypass/status` ve canlı test `POST /api/bypass/test`.

Sunucu çalışırken OpenAPI: `GET /docs`.

## Anti-Bot Koruması (BrowserBridge)

SofaScore düz HTTP istemcilerini reddeder: `curl_cffi` hangi TLS parmak izini gösterirse göstersin API istekleri `403 {"reason": "challenge"}` alır. Bu yüzden uygulama API çağrılarını gerçek bir tarayıcı sayfasının içinden yapar:

1. **BrowserBridge**, [Scrapling](https://github.com/D4Vinci/Scrapling)'in `StealthySession`'ı (gizlilik ayarlı patchright) ile headless bir Chromium başlatır ve tek bir sayfayı açık tutar.
2. **Challenge:** 403 challenge gelince `sofascore.com/captcha.html` açılır; Scrapling gömülü Cloudflare Turnstile'ı geçer ve oluşan `sofa_captcha` cookie'si API'yi açar. Aynı anda gelen 403'ler tek çözümü paylaşır; başarısız bir çözüm 3 dakika yeniden denenmez.
3. **Önce tarayıcı modu:** curl engellenip tarayıcı başarılı olunca istekler 10 dakika boyunca önce curl'de başarısız olmak yerine doğrudan tarayıcıdan yapılır.

Tarayıcı masaüstünde de sunucuda da **her zaman headless** çalışır; her yerde aynı kod yolu kullanılır. Ekran, Xvfb veya Google Chrome gerekmez. Üç sporda ölçüldü: Premier League (50 maç), Wimbledon (239) ve EuroBasket (76) tam sezonları ekransız ortamda %100 kapsamayla indi. Hata ayıklarken tarayıcıyı görmek için `SOFASCORE_BROWSER_HEADED=1`.

Tarayıcı profili (cookie'ler, çözülmüş challenge) `~/.cache/sofascore_scraper/chrome_profile` altındadır; `SOFASCORE_BROWSER_PROFILE` ile değiştirilebilir. Var olan profille yeniden başlatmada ilk istek yaklaşık 1–6 sn'de yanıtlanır.

### Sunucu kurulumu (Linux / Docker)

```bash
pip install -r requirements.txt
python -m playwright install chromium
# Yalnızca Debian/Ubuntu, bir kez: Chromium'un sistem kütüphaneleri (sudo kullanır)
python -m playwright install-deps chromium
```

## Geliştirme

Web’i otomatik yeniden yükleme ile:

```bash
python main.py --web --dev
```

Web uygulaması `frontend/` altında bir Vue 3 + TypeScript + Vite projesidir (Pinia, vue-router, vue-i18n, Tailwind). Sunucu derlenmiş dosyaları `frontend/dist/`’ten sunar.

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173, /api isteklerini 127.0.0.1:8000'e yönlendirir
npm run build    # tip kontrolü (vue-tsc) + frontend/dist/ içine üretim derlemesi
```

Yapı: `src/views/` her sayfa bir dosya, `src/components/` ortak parçalar, `src/stores/` (ligler, spor filtresi, çalışan iş), `src/api/client.ts` bütün backend çağrıları, `src/locales/{tr,en}.ts` bütün arayüz metinleri.

Testler ve lint (CI aynısını Python 3.10 ve 3.14’te çalıştırır):

```bash
pip install pytest pytest-asyncio httpx ruff
ruff check .
python -m pytest -q
```

`tests/conftest.py`, `DATA_DIR`, `config/` ve `.env`’i küçük sentetik bir veri setiyle geçici bir klasöre yönlendirir; testler verinize ve ayarlarınıza hiç dokunmaz. SofaScore’a istek atan testler `live` olarak işaretlidir ve varsayılan olarak atlanır; çalıştırmak için `python -m pytest -m live`.

## Katkıda bulunma

Katkılarınızı memnuniyetle karşılıyoruz. Şu şekillerde destek olabilirsiniz:

- **Hata bildirimi** — Sorunu yeniden üreten adımlar, beklenen / gerçek davranış, işletim sistemi ve Python sürümü ile ilgili `.env` anahtarlarını (gizli bilgi paylaşmadan) bir issue’da paylaşın.
- **Özellik önerisi** — Kullanım senaryosu ve kısıtları yazın; bakıcılar kapsamı issue üzerinde değerlendirebilir.
- **Pull request** — Repo’yu fork’layın, odaklı bir dal kullanın, değişiklikleri küçük ve tek konuda tutun; PR’da *ne* ve *neden* olduğunu açıklayın. Mevcut kod stiline uyun; gereksiz geniş refaktörden kaçının. Kullanıcıya dönük metin değiştiriyorsanız iki dili de güncelleyin: web uygulaması için `frontend/src/locales/tr.ts` ve `en.ts`, terminal arayüzü için `locales/tr.json` ve `locales/en.json`.
- **Dokümantasyon ve çeviri** — Bu README’ler veya yerelleştirme metinleri için iyileştirmeler değerlidir.

Katkı göndererek, katkınızın projenin lisansı altında sunulmasını ve proje sahibinin onu başka koşullarla da (örneğin ticari bir lisansla) sunabilmesini kabul etmiş olursunuz. Issue ve inceleme süreçlerinde saygılı iletişim rica edilir. Fikrin uyarlılığından emin değilseniz önce issue açmak iyi bir başlangıçtır.

## Lisans

[PolyForm Noncommercial 1.0.0](LICENSE). Bu yazılımı **ticari olmayan amaçlarla** kullanabilir, değiştirebilir ve paylaşabilirsiniz: kişisel kullanım, öğrenim, araştırma, hobi projeleri ile hayır kurumları, eğitim kurumları ve kamu kurumlarının kullanımı buna dahildir. **Ticari kullanım**, ayrı bir lisans alınmadan **yasaktır**; bunun için bir issue açabilir ya da [@tunjayoff](https://github.com/tunjayoff) ile iletişime geçebilirsiniz.

Bu değişiklikten önce yayımlanan sürümler MIT lisansı altında kalmaya devam eder.
