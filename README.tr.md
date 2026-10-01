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

## Ekran görüntüleri

İki futbol ligi indirilmiş haliyle web uygulaması. Görseller GitHub temanıza (açık ya da koyu) uyar.

**Ligler — takip edilen ligler, indirilen maç sayısı ve detay kapsamı**

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/leagues-tr-dark.png">
  <img src="docs/screenshots/leagues-tr-light.png" alt="Ligler sayfası">
</picture>

**Maç indir — birden fazla ligden sezon seçip tek indirme listesinde topla**

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/download-tr-dark.png">
  <img src="docs/screenshots/download-tr-light.png" alt="Maç indir sayfası">
</picture>

**Maçlar — lige, sezona, tarihe ve detay durumuna göre filtrele**

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/matches-tr-dark.png">
  <img src="docs/screenshots/matches-tr-light.png" alt="Maçlar sayfası">
</picture>

**Maç — periyot skorları, önemli anlar, form ve aralarındaki maçlar**

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/match-tr-dark.png">
  <img src="docs/screenshots/match-tr-light.png" alt="Maç özeti">
</picture>

**Maç istatistikleri — maç geneli ya da periyot bazında**

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/match-stats-tr-dark.png">
  <img src="docs/screenshots/match-stats-tr-light.png" alt="Maç istatistikleri">
</picture>

## Gereksinimler

- Python **3.10+** (önerilen: 3.11+).
- **patchright için Chromium** — uygulamanın SofaScore'a eriştiği tarayıcı. Kurulum betikleri ve başlatıcı bir kez indirir (`python -m patchright install chromium --no-shell`). Bilgisayarda kurulu Google Chrome ya da Chromium **kullanılmaz**.
- **Node.js 20.19+ veya 22.12+ ve npm** — web uygulamasını (`frontend/`) derlemek için. Node.js kuruluysa kurulum betikleri ve `scripts/start_web.py` kendisi derler. Node.js yoksa terminal modları yine çalışır; web adresi uygulama yerine bir yardım sayfası gösterir.
- **Git** — `curl | bash` ile tek satır kurulum için gerekli (depoyu klonlar); elle indiriyorsanız isteğe bağlı.
- SofaScore’a ağ erişimi.

[Docker](#docker) ile bunların hiçbiri bilgisayarınızda gerekmez: imaj Python’u, derlenmiş web uygulamasını ve tarayıcıyı içerir.

## Kurulum

Birini seçin:

| Yol | Gerekenler | Kime uygun |
|-----|------------|------------|
| [Kurulum betiği](#hızlı-kurulum-betik) | Git, Python 3.10+, Node.js | Masaüstü: çift tıkla başlatıcı, terminal arayüzü, `git pull` ile kolay güncelleme |
| [Docker](#docker) | Docker | Sunucu ya da NAS; Python ve tarayıcıyı ana sisteme kurmak istemeyenler |
| [Sürüm arşivi](#sürüm-arşivi) | Python 3.10+ | Git ve Node.js olmadan sabit bir sürüm |
| [Elle kurulum](#elle-kurulum) | Git, Python 3.10+, Node.js | Geliştirme |

Hangi sürümün çalıştığını `python main.py --version` (ya da `GET /health`, ya da **Ayarlar** sayfasının altı) gösterir. Sürümler arasındaki değişiklikler [CHANGELOG.md](CHANGELOG.md) dosyasındadır (İngilizce).

### Hızlı kurulum (betik)

Resmi depo: [github.com/tunjayoff/sofascore_scraper](https://github.com/tunjayoff/sofascore_scraper).

**Linux / macOS / Git Bash** — depoyu zaten klonladıysanız:

```bash
chmod +x scripts/install.sh   # bir kez
./scripts/install.sh
```

**Tek satır** (depoyu klonlar, `.venv` kurar, bağımlılıkları ve tarayıcıyı yükler, Node.js kuruluysa web uygulamasını derler, `.env` oluşturur, sonunda [kurulumu denetler](#kurulumu-denetleme-doctor)):

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

**Önkoşullar:** **Git** (tek satır / klon yolu için), **PATH** üzerinde **Python 3.10+**. Betikler `git`, `python`, `venv` veya `pip` hata verirse anlaşılır Türkçe/İngilizce iletir (ör. Ubuntu’da `python3-venv` eksikliği). Node.js yoksa ya da eskiyse bunu bildirir, kurulumu durdurmaz. Betik kurulum denetimiyle biter ve uygulamanın ihtiyaç duyduğu bir şey eksikse 1 ile çıkar.

### Docker

İmaj uygulamayı, derlenmiş web uygulamasını ve [BrowserBridge](#anti-bot-koruması-browserbridge)’in ihtiyaç duyduğu headless Chromium’u içerir. Root olmayan bir kullanıcıyla (uid 1000) çalışır ve varsayılan olarak web uygulamasını başlatır.

```bash
docker run -d --name sofascore-scraper --shm-size=1g \
  -p 127.0.0.1:8000:8000 \
  -v sofascore-data:/app/data \
  -v sofascore-config:/app/config \
  -v sofascore-browser:/app/browser-profile \
  ghcr.io/tunjayoff/sofascore_scraper:latest
```

Ardından `http://127.0.0.1:8000` adresini açın. Compose ile depodaki [`docker-compose.yml`](docker-compose.yml) aynısını yapar:

```bash
docker compose up -d
docker compose logs -f      # uygulama stdout'a log yazar
```

İmajlar her etiketli sürümle `ghcr.io/tunjayoff/sofascore_scraper` adresinde yayımlanır (`latest`, `X.Y.Z`, `X.Y`). Henüz bir sürüm yoksa ya da güncel `main`’i istiyorsanız imajı depodan derleyin: `docker build -t sofascore-scraper .` (sonra imaj adı olarak `sofascore-scraper` kullanın) ya da `docker compose up -d --build`. `docker/smoke-test.sh sofascore-scraper` derlenmiş imajı hiç ağa çıkmadan sınar.

| Volume | İçeriği |
|--------|---------|
| `/app/data` | İndirilen her şey (`DATA_DIR`): sezonlar, maçlar, detaylar, yedekler, iş geçmişi |
| `/app/config` | `leagues.txt`, `league_sports.json` ve `.env` (**Ayarlar** sayfasında kaydedilen ayarlar) |
| `/app/browser-profile` | Çözülmüş challenge’ı taşıyan tarayıcı profili; saklanırsa yeniden başlatma hızlı olur |
| `/app/logs` | Ayrılmıştır. Uygulama şu an logları stdout’a yazar (`docker logs`) |

Bilinmesi gerekenler:

- **Giriş (parola) yoktur.** Örnekler portu yalnızca `127.0.0.1` üzerinde açar. Portu ağa açarsanız (`-p 8000:8000`) erişebilen herkes ayarları değiştirip veriyi silebilir; ayrıca arayüzü açtığınız adı ya da IP’yi de bildirmeniz gerekir: `-e SOFASCORE_ALLOWED_HOSTS=localhost,127.0.0.1,sunucum.lan` (başka `Host` başlığıyla gelen istekler reddedilir).
- **Ayarlar:** `.env.example` içindeki her değişken `-e` / `environment:` ile verilebilir. Bu şekilde verilen değişken her başlangıçta Ayarlar sayfasında kaydedilen değerin önüne geçer; bu yüzden yalnızca sabit kalmasını istediklerinizi verin (ör. `APP_LANGUAGE=tr`, `USE_PROXY` / `PROXY_URL`). `PORT` konteyner içindeki portu değiştirir.
- **Paylaşımlı bellek:** Chromium, Docker’ın 64 MB’lık varsayılanından fazlasına ihtiyaç duyar; `--shm-size=1g` (Compose’da `shm_size`) bunun içindir.
- **Klasör bağlama** (`-v ./data:/app/data`), klasör uid 1000 tarafından yazılabiliyorsa çalışır: `mkdir -p data config && sudo chown -R 1000:1000 data config`. Başka bir uid için imajı `--build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g)` ile derleyin.
- **Diğer komutlar:** imaj adından sonraki argümanlar `main.py`’ye gider; ör. `docker run --rm ghcr.io/tunjayoff/sofascore_scraper:latest --version` ya da aynı volume’larla zamanlanmış bir indirme: `docker compose run --rm sofascore-scraper --headless --update-all`. Tarayıcı profilini aynı anda tek konteyner kullanabilir; bu şekilde indirme başlatmadan önce web konteynerini durdurun (`docker compose stop`). Profil meşgulken başlatılan ikinci konteyner uyarı yazar ve tarayıcısını açamaz.
- **Güncelleme:** `docker compose pull && docker compose up -d`. Veri, yapılandırma ve tarayıcı profili volume’larda kalır.

### Sürüm arşivi

[Releases sayfasındaki](https://github.com/tunjayoff/sofascore_scraper/releases) her etiketli sürümde `sofascore-scraper-X.Y.Z.tar.gz` / `.zip` bulunur: o sürümün kaynağı ve derlenmiş web uygulaması; Node.js gerekmez. Arşivi açın ve [elle kurulum](#elle-kurulum)a `python -m venv` adımından devam edin ya da klasörün içinde `./scripts/install.sh` (Windows’ta `scripts\install.ps1`) çalıştırın.

### Elle kurulum

```bash
git clone https://github.com/tunjayoff/sofascore_scraper.git
cd sofascore_scraper
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m patchright install chromium --no-shell   # zorunlu: uygulamanın kullandığı tarayıcı
```

Tarayıcı adımı, Google Chrome kurulu olsa da gereklidir. Köprü sistemdeki tarayıcıyı değil, patchright'ın kendi Chromium derlemesini başlatır (Scrapling'in `StealthySession`'ı üzerinden). Bu komutta `playwright` değil `patchright` kullanın: iki paketin her biri kendi sürümünün beklediği derlemeyi indirir. `--no-shell`, köprünün hiç kullanmadığı ayrı headless shell'i indirmez.

Web uygulamasını derleyin (Node.js 20.19+ veya 22.12+ gerekir; yalnızca terminal modlarını kullanacaksanız atlayın):

```bash
cd frontend && npm install && npm run build && cd ..
```

Örnek ortam dosyasını kopyalayıp düzenleyin:

```bash
cp .env.example .env
```

### Kurulumu denetleme (doctor)

```bash
python main.py --doctor          # okunur rapor
python main.py --doctor --json   # aynısı JSON olarak; betikler ve sunucular için
```

Denetim SofaScore'a hiç bağlanmaz. Her satır `OK`, `WARN` ya da `FAIL` olur; her sorunun yanında tek satırlık çözümü yazar:

| Denetim | Neye bakar |
|---------|------------|
| `python` | Python 3.10 veya üzeri. |
| `packages` | `requirements.txt` içindeki her paket içe aktarılabiliyor; sabitlenen sürümler tutuyor. |
| `browser` | Köprünün başlattığı Chromium derlemesi kurulu **ve başlıyor** (headless, `about:blank` üzerinde, geçici bir profille). |
| `profile` | Tarayıcı profili klasörü yazılabilir; başka bir makine, geride kalmış bir tarayıcı ya da ölmüş bir süreç tarafından kilitli değil. |
| `data_dir`, `config_dir` | `DATA_DIR` ve `config/` yazılabilir. |
| `frontend` | `frontend/dist/` var. Yoksa yalnızca uyarıdır: terminal modları onsuz çalışır. |
| `env` | `.env` ayrıştırılabiliyor ve değerleri geçerli (sayılar, `true`/`false`, proxy adresi, dil, log düzeyi). |

```text
[ OK ] Python: 3.14.0
[ OK ] Paketler: gerekli 13 paketin hepsi içe aktarılabiliyor
[FAIL] Tarayıcı: patchright 1.63.0 sürümünün Chromium derlemesi kurulu değil (beklenen yer: ...); kurulu Google Chrome kullanılmaz
       Çözüm: Şunu çalıştırın: .venv/bin/python -m patchright install chromium --no-shell
[WARN] Web arayüzü: derlenmemiş (frontend/dist yok): web uygulaması bunun yerine bir yardım sayfası gösterir; terminal modları onsuz çalışır
       Çözüm: cd frontend && npm install && npm run build   (ya da scripts/start_web.py ile başlatın, kendisi derler)
```

- **Çıkış kodu:** hiçbir denetim başarısız değilse `0` (uyarı olabilir), en az biri başarısızsa `1`. `--strict` uyarılarda da `1` ile çıkar.
- `--only python,browser` / `--skip frontend` denetim seçer; `--lang en|tr` dili belirler (varsayılan: `APP_LANGUAGE`).
- Uygulamanın kendi import'larından önce çalışır; paketler eksikken de çalışır (bildirdiği şeylerden biri de budur).
- `--live` ek olarak tarayıcı köprüsü üzerinden SofaScore'a **tek** gerçek istek atar. Başka hiçbir durumda atılmaz. Önce web uygulamasını durdurun: iki süreç aynı tarayıcı profilini açamaz.
- Başlatıcı (`scripts/start_web.py`) aynı denetimi her açılışta çalıştırır, eksik paketleri ve eksik tarayıcıyı kendisi kurar. Başka kod `src.doctor.run_checks()` / `src.doctor.report()` çağırabilir.

## Yapılandırma

### Ortam değişkenleri (`.env`)

Tüm anahtarlar `.env.example` içinde. Sık kullanılanlar:

| Değişken | Açıklama |
|----------|----------|
| `DATA_DIR` | Verinin kök dizini (varsayılan `data`). Web `ConfigManager` üzerinden okur. |
| `APP_LANGUAGE` | `en` veya `tr`: terminal arayüzünün ve sunucu mesajlarının dili. Web uygulamasının dili **Ayarlar**’dan seçilir (orada değiştirmek bu değeri de günceller). |
| `MAX_CONCURRENT` | Paralel detay isteği üst sınırı. |
| `REQUEST_RATE_LIMIT` | **Tüm süreçlerin toplamı** için SofaScore'a saniyede istek sayısı (web uygulaması, CLI, her `--watch`, `--refresh-only`). Varsayılan `10 × MAX_CONCURRENT` (= `100`), `0` = kapalı. Bkz. [Ortak istek bütçesi](#ortak-istek-bütçesi-tüm-süreçler). |
| `USE_PROXY` / `PROXY_URL` | İsteğe bağlı proxy. |
| `FETCH_ONLY_FINISHED` | Yalnız bitmiş maçları tut (`status.type == finished`). Varsayılan `true`. Henüz oynanmamış fikstürler schedule dosyalarına yazılmaz. |
| `REFRESH_WINDOW_HOURS` | Kaydedilen maçın başlangıçtan kaç saat boyunca geçici sayılıp yeniden okunacağı (varsayılan `72`, `0` = kapalı). Bkz. [Yenileme politikası](#yenileme-politikası). |
| `RATE_LIMIT_*` / `SERVER_ERROR_*` | Çok hata durumunda devreye giren eşikler. |

Web **Ayarlar** sayfasından birçok değer düzenlenir; kayıt `.env`’i günceller.

### Ortak istek bütçesi (tüm süreçler)

Her kod yolu kendi isteklerini sınırlar (`MAX_CONCURRENT`, beklemeler, izleyicinin 1 sn aralığı), ama ayrı süreçler birbirini görmez: spor başına bir `--watch`, bir web işi ve cron'dan `--refresh-only` birlikte çalışınca hızları toplanır. `REQUEST_RATE_LIMIT` hepsinin paylaştığı tek bütçedir. SofaScore'a giden her istek (curl ya da tarayıcı) önce, işletim sistemi dosya kilidiyle korunan küçük bir durum dosyasından sıradaki boş anı ayırır.

- **Varsayılan: `MAX_CONCURRENT` başına 10 istek/sn, yani varsayılan ayarlarla `100` istek/sn**; boşta geçen süreden sonra en fazla bir saniyelik bütçe art arda kullanılabilir. Tek bir toplu indirmeyi yavaşlatmayacak şekilde seçildi: varsayılan ayarlarla indirme yolu kendi başına en fazla 60–70 istek/sn'ye çıkıyor ve bu tavan `MAX_CONCURRENT` ile büyüyor (ağsız ölçüm; yinelemek için `python scripts/bench_bulk_rate.py`). Varsayılanın kattığı şey, birden çok sürecin birlikte bu sınırı aşamamasıdır.
- **Daha nazik olmak için düşürün**, ör. `5`. `1` ve altında istekler eşit aralıklı olur. Toplu indirme de o oranda yavaşlar.
- **`0` kapatır**: her süreç yine kendi başınadır.
- **İzleyiciler** ayrıca 1 istek/sn'lik ortak bir şeridi paylaşır: spor başına bir `--watch` çalışsa da istekler süreç başına değil toplamda en az 1 sn aralıklıdır.
- **Durumun yeri:** `~/.cache/sofascore_scraper/throttle/` (`SOFASCORE_THROTTLE_DIR` ile değişir). Bu klasörü paylaşan süreçler bütçeyi paylaşır; konteynerlerde ortak bir volume gösterin.
- **Hata durumunda:** süreç ölünce kilidi işletim sistemi bırakır; çöken süreç bayat kilit bırakamaz. Klasöre yazılamıyorsa ya da kilit 1 sn içinde alınamıyorsa istekler engellenmez: o süreç kendi sayacıyla devam eder, bir kez uyarı loglar ve 30 sn sonra dosyayı yeniden dener.

### Lig listesi (`config/leagues.txt`)

Her satır `Ad: ID` biçimindedir; ID, SofaScore **unique tournament** sayısal ID’sidir (turnuva URL’sinde yer alır, ör. `.../premier-league/17` → `17`). Dosya size aittir ve git’te takip edilmez: ilk çalıştırmada `config/leagues.example.txt`’den oluşturulur; o dosyada lig yoktur. Yeni kurulum boş başlar: ligleri web uygulamasından (**Ligler → Lig ekle**; ligin sporunu da kaydeder) ya da terminal menüsünden ekleyin. Web uygulamasında veya CLI’da lig ekleyip kaldırdığınızda satırları uygulama kendisi günceller.

CLI ile özel yol:

```bash
python main.py --config /yol/leagues.txt --data-dir /yol/veri
```

`--config` ve `--data-dir` yalnızca **etkileşimli** ve **headless** modda geçerlidir. Web sunucusu projedeki `.env` ile tekil `ConfigManager` kullanır; hem CLI hem web kullanacaksanız `DATA_DIR` vb. ile aynı veri dizinine hizalayın.

## Kullanım

### Nasıl kullanılır? (hızlı başlangıç)

**Web (çoğu kullanıcı için uygun)**

1. **Kurulum** ve **Yapılandırma** adımlarını tamamlayın (`pip install`, `cp .env.example .env`). Veriyi `./data` dışında tutmak isterseniz `DATA_DIR` ayarlayın.
2. Uygulamayı başlatın: `./start-sofascore.sh` (veya `python scripts/start_web.py`; Windows'ta `Start SofaScore.bat`, macOS'ta `Start SofaScore.command` dosyasına çift tıklayın). Başlatıcı `.venv` yoksa oluşturur, [kurulumu denetler](#kurulumu-denetleme-doctor), eksikleri kurar (Python paketleri, tarayıcı), `frontend/dist/` yoksa ve Node.js kuruluysa web uygulamasını derler, sonra `http://127.0.0.1:8000` adresini açar. `python main.py --web` yalnızca sunucuyu başlatır, hiçbir şey kurmaz.
   Kodu güncelledikten sonra (`git pull`) web uygulamasını kendiniz yeniden derleyin: `cd frontend && npm install && npm run build`. Başlatma betiği sadece `frontend/dist/` yoksa derler; aksi halde eski arayüzü görmeye devam edersiniz.
3. **Spor** — Kenar menünün üstündeki seçici (Tümü / Futbol / Basketbol / Tenis) bütün sayfaları süzer. Üzerinde çalıştığınız sporu seçin.
4. **Ligler** — Yeni kurulumda lig yoktur; sayfa bir **Lig ekle** düğmesiyle açılır. **Lig ekle** SofaScore’da arar; sonuçları spora göre süzüp **Ekle**’ye basın. Sporu bilinmeyen bir ligde (örneğin `config/leagues.txt`’ye elle eklenmiş) **Spor seç** kutusu çıkar; bir kez seçmeniz yeterli, kaydedilir.
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

**Bir şey başlamıyor ya da bütün indirmeler başarısız**

`python main.py --doctor` çalıştırın. Neyin eksik olduğunu (çoğu zaman tarayıcı: `python -m patchright install chromium --no-shell`) ve çözümünü yazar. Tarayıcı başlatılamadığında uygulama 5 dakika yeniden denemez; nedeni giderip uygulamayı yeniden başlatın. Bkz. [Kurulumu denetleme](#kurulumu-denetleme-doctor).

**Tarayıcıda "Web arayüzü derlenmemiş" sayfası çıkıyor**

`frontend/dist/` yok. `cd frontend && npm install && npm run build` ile derleyin (Node.js 20.19+ veya 22.12+) ve sayfayı yenileyin; sunucuyu yeniden başlatmak gerekmez. Node.js yoksa, yayımlandığında [Releases sayfasındaki](https://github.com/tunjayoff/sofascore_scraper/releases) sürüm arşivini kullanın (web uygulaması derlenmiş gelir) ya da başka bir makinede derlenmiş `frontend/dist/` klasörünü kopyalayın. Bu sırada API ve terminal modları çalışır.

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

Varsayılan adres: `http://127.0.0.1:8000`. Sunucu yalnızca bu bilgisayarı dinler. `--host 0.0.0.0` onu ağa açar ve **giriş yoktur**: erişebilen herkes ayarları değiştirip veriyi silebilir. `--port` portu değiştirir, `--dev` kod değişince yeniden başlatır. Sağlık kontrolü: `GET /health` (sürümü de bildirir).

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
| `--watch` | Canlı izleyici: `--sport` ve `--league-ids` ya da `--event-ids` (isteğe bağlı `--watch-hours`); bkz. [İzleme modu](#izleme-modu) |
| `--doctor` | Ortamı denetler ve çıkar, `0` = hazır, `1` = bir şey başarısız (`--headless` gerekmez); bkz. [Kurulumu denetleme](#kurulumu-denetleme-doctor) |

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
python main.py --version
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

Desteklenen sporlar tek yerde, `src/sports.py`’deki kayıt defterinde tanımlıdır: spor başına skor biçimi, canlı izleyicinin parametreleri ve o spor için istenen maç detay uç noktaları. CLI, indirici, izleyici ve web API’si bu kayıt defterini okur.

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

## İzleme modu

`python main.py --watch` canlı maçları takip edip **olay** üretir; hiçbir sonucu sonuçlandırmaz. Olaylarla ne yapılacağına tüketici karar verir: ayrı bir servis, web arayüzü ya da `tail -f` ile siz.

```bash
python main.py --watch --sport football --league-ids 17,8     # bu liglerin tüm canlı maçları
python main.py --watch --sport tennis --event-ids 17196038,17210464 --watch-hours 3
```

- **Nasıl sorgular?** 30 sn'de bir `/sport/{sport}/events/live` (tek istek) çekilir. `/event/{id}` ise şu durumlarda çekilir:
  - izlenen canlı maç listeden düşünce hemen (bitişin en erken sinyali);
  - bitişe yakın maçta 30 sn'de bir;
  - takılı maçta 5 dk'da bir.
- **Bitişe yakın:**
  - futbol: 2. yarıda 80. dk ya da `injuryTime2` görülünce;
  - basketbol: normal sürenin `%90`'ı oynanınca, saat verisi yoksa son periyotta;
  - tenis: son sette.
- **Hız bütçesi.** Turda en fazla `WATCH_MAX_EVENT_POLLS` (varsayılan 20) maç sayfası çekilir, istekler arasında en az 1 sn bırakılır. Bu aralığı makinedeki tüm `--watch` süreçleri paylaşır (bkz. [Ortak istek bütçesi](#ortak-istek-bütçesi-tüm-süreçler)); spor başına bir izleyici çalışsa da toplam 1 istek/sn'nin altında kalır, birden çok yoğun izleyicide bir tur 30 sn'den uzun sürebilir. Daha çok maç bitişe yakınsa maç sayfası aralığı 60 sn'ye iner ve uyarı loglanır.
- **Takılı maç.** Başlangıçtan 4 sa sonra (tenis: `startTimestamp` yalnızca planlanan saat olduğu için gerçek 1. set başlangıcından 6 sa sonra; set süreleri yağmur arası gibi duraklamaları içermediğinden bu başlangıç geç çıkabilir ve `stuck` biraz geç gelebilir) hâlâ canlı ya da başlamamış maç için bir kez `stuck` olayı üretilir, sonra 5 dk'da bir okunur. İptale dönüp başlangıç saati ileri alınmışsa izlemede kalır (askıya alınan tenis maçı aynı id ile ertesi güne taşınabiliyor).
- **Olaylar.** `DATA_DIR/watch_events.jsonl` dosyasına satır başına bir JSON yazılır:
  - `status_changed` `{event_id, from, to, at_utc, change_ts, scores}`. `scores` `extract_scores` çıktısıdır. İlk `completed` olayı, yenileme penceresi kapanana kadar `provisional: true` taşır; bkz. [Yenileme politikası](#yenileme-politikası).
  - `score_changed` canlı skor içindir `{event_id, from, to, at_utc}`.
  - `stuck`.
- **Yeniden başlatma.** Her maçın son bilinen sınıfı `DATA_DIR/watch_state_{sport}.json`'da tutulur (spor başına ayrı dosya: aynı anda çalışan farklı spor izleyicileri birbirinin kaydını ezmez); yeniden başlatmada aynı geçiş iki kez olay olmaz. `watch_state.json` eski formattır, okunmaz; silinebilir. Ctrl+C temiz kapatır.
- **Ne zaman durur?** `--event-ids` ile izlenen maçların hepsi bitince izleyici kapanır.

Neden bu sayılar: `events/live` CDN'de 5 sn önbellekte kalıyor; araştırmada düdük → `finished` medyan 20 sn, en fazla 302 sn sürdü (`docs/status-matrix/README.md`). 30 sn'den sık sorgulamak fayda getirmez.

## REST API (özet)

Web uygulaması kök yollarda; JSON API öneki **`/api`**.

- **Ligler**: listele (her ligde `sport`), ekle (isteğe bağlı `sport`), sporu ayarlamak için `PATCH /api/leagues/{id}`, sil, ara (yerel/uzak; uzak sonuçlarda `sport`), sezonlar, sezon yenileme, eksik detay listesi.
- **Sporlar**: `GET /api/sports` — desteklenen sporlar ve her biri için istenen maç detay dilimleri (`src/sports.py`’deki kayıt defterinin salt okunur görünümü).
- **Maçlar**: `GET /api/matches` — sayfalı; filtreler `league_id` (tek ID ya da virgülle birden fazla, ör. `17,8`), `season_id`, `date`, `details=present|missing`, `sort=asc|desc`; her satırda `has_details`. Ayrıca tek maç JSON ve tek maç çekme.
- **Scraper**: `POST /api/fetch` (gövde: `full` | `details`, `selections: [{league_id, season_ids, match_ids}]`), `POST /api/scrape/cancel` (sonrasında yeni istek gönderilmez, yeniden deneme beklemeleri kesilir), durum, SSE akışı.
- **Pano / istatistik / ayarlar**: Web panellerine JSON; ayarlar `.env` ile uyumlu.
- **Veri**: yedek zip, kapsam seçerek temizleme, CSV export.
- **Bypass Durumu**: `GET /api/bypass/status` (`health` ile: `ok` / `degraded` / `blocked`, bkz. [SofaScore bizi engelliyor mu?](#sofascore-bizi-engelliyor-mu-köprü-sağlığı)) ve canlı test `POST /api/bypass/test`.
- **Sağlık**: `GET /health` (`/api` öneki yok) `status`, `version`, `ui` alanlarının yanında `bridge` (aynı sağlık bloğu) ve `throttle` ([ortak istek bütçesi](#ortak-istek-bütçesi-tüm-süreçler)) döndürür.

Sunucu çalışırken OpenAPI: `GET /docs`.

## Anti-Bot Koruması (BrowserBridge)

SofaScore düz HTTP istemcilerini reddeder: `curl_cffi` hangi TLS parmak izini gösterirse göstersin API istekleri `403 {"reason": "challenge"}` alır. Bu yüzden uygulama API çağrılarını gerçek bir tarayıcı sayfasının içinden yapar:

1. **BrowserBridge**, [Scrapling](https://github.com/D4Vinci/Scrapling)'in `StealthySession`'ı (gizlilik ayarlı patchright) ile headless bir Chromium başlatır ve tek bir sayfayı açık tutar.
2. **Challenge:** 403 challenge gelince `sofascore.com/captcha.html` açılır; Scrapling gömülü Cloudflare Turnstile'ı geçer ve oluşan `sofa_captcha` cookie'si API'yi açar. Aynı anda gelen 403'ler tek çözümü paylaşır; başarısız bir çözüm 3 dakika yeniden denenmez.
3. **Önce tarayıcı modu:** curl engellenip tarayıcı başarılı olunca istekler 10 dakika boyunca önce curl'de başarısız olmak yerine doğrudan tarayıcıdan yapılır.

Tarayıcı masaüstünde de sunucuda da **her zaman headless** çalışır; her yerde aynı kod yolu kullanılır. Ekran, Xvfb veya Google Chrome gerekmez. Üç sporda ölçüldü: Premier League (50 maç), Wimbledon (239) ve EuroBasket (76) tam sezonları ekransız ortamda %100 kapsamayla indi. Hata ayıklarken tarayıcıyı görmek için `SOFASCORE_BROWSER_HEADED=1`.

Tarayıcı profili (cookie'ler, çözülmüş challenge) `~/.cache/sofascore_scraper/chrome_profile` altındadır; `SOFASCORE_BROWSER_PROFILE` ile değiştirilebilir. Var olan profille yeniden başlatmada ilk istek yaklaşık 1–6 sn'de yanıtlanır.

### SofaScore bizi engelliyor mu? (köprü sağlığı)

Her şey tarayıcının challenge'ı çözmesine bağlı. Bu bozulduğunda işler yalnızca yavaşça başarısız oluyordu. Köprü artık isteklerinin sonucundan bir sağlık durumu tutuyor:

| Durum | Anlamı |
|-------|--------|
| `ok` | Son istek yanıt aldı (ya da henüz istek yapılmadı). |
| `degraded` | Art arda `BRIDGE_DEGRADED_AFTER` (varsayılan 3) istek başarısız oldu. |
| `blocked` | Art arda `BRIDGE_BLOCKED_AFTER` (varsayılan 10) istek başarısız oldu **ve** seri en az `BRIDGE_BLOCKED_MIN_SECONDS` sürdü (varsayılan 200 sn; bir challenge yeniden denemesinden uzun: şanssız tek bir çözümde aynı anda düşen on paralel istek henüz engel sayılmaz). |

- **Başarısızlık sayılanlar:** çözülemeyen challenge (ya da çözümden sonra da reddedilen istek), challenge sunulmayan 403, başlatılamayan tarayıcı. Çözülüp yinelenen 403 başarıdır. Ağ hatası, 5xx ve 429 seriyi ne uzatır ne sıfırlar. Yanıt alan tek istek durumu `ok` yapar.
- **Nerede görünür:**
  - `GET /health` → `bridge` ve `GET /api/bypass/status` → `health`: `state`, `consecutive_failures`, `last_success_at`, `failing_since`, `last_error` (`kind`: `challenge` / `forbidden` / `browser`), `thresholds`. `/health` içindeki `status` `ok` kalır: o, sunucunun ayakta olduğunu söyler.
  - Web uygulaması: durum `ok` değilken her sayfanın üstünde bir afiş. Kapatınca o seri için gizlenir; durum kötüleşirse ya da yeni bir seri başlarsa yeniden görünür.
  - Log: istek başına değil, durum değişimi başına bir uyarı.
  - Terminal modları (etkileşimli, `--headless`, `--watch`, `--refresh-only`): durum değişimi başına stderr'de, uygulama dilinde tek satır.
- Durum süreç başınadır: web uygulaması kendi köprüsünü, her CLI süreci kendininkini bildirir.

### Sunucu kurulumu (Linux / Docker)

[Docker imajı](#docker) tarayıcıyı ve sistem kütüphanelerini zaten içerir. Düz bir Linux sunucuda ya da kendi imajınızda:

```bash
pip install -r requirements.txt
python -m patchright install chromium --no-shell
# Yalnızca Debian/Ubuntu, bir kez: Chromium'un sistem kütüphaneleri (sudo kullanır)
python -m patchright install-deps chromium
# çıkış kodu 0 = hazır; tarayıcıyı bir kez about:blank ile açar, SofaScore'a istek atmaz
python main.py --doctor --skip frontend
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

### Sürüm yayımlama

Bakımcı için. Sürüm tek bir yerde yazılıdır: `pyproject.toml`. CLI, `/health`, web uygulaması, yayın iş akışı ve Docker imajı onu oradan okur.

1. `pyproject.toml` içindeki `version` değerini `X.Y.Z` yapın.
2. `CHANGELOG.md` içinde `## [Unreleased]` başlığını `## [X.Y.Z] - YYYY-AA-GG` olarak değiştirin, üstüne yeni ve boş bir `## [Unreleased]` ekleyin ve dosyanın sonundaki bağlantıları güncelleyin. Bu bölüm sürüm notları olur.
3. İkisini yerelde kontrol edin, sonra commit’leyip değişikliği `main`’e alın:

   ```bash
   python scripts/release.py check-tag vX.Y.Z   # etiket ↔ pyproject sürümü
   python scripts/release.py notes              # notları yazdırır; bölüm yoksa hata verir
   ```

4. O commit’i etiketleyip etiketi gönderin:

   ```bash
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin vX.Y.Z
   ```

Etiket `.github/workflows/release.yml` iş akışını başlatır: etiketin sürümle eşleştiğini denetler, CI ile aynı lint, test ve derlemeyi çalıştırır, Docker imajını derleyip duman testinden geçirir, `ghcr.io/tunjayoff/sofascore_scraper` adresine gönderir (`X.Y.Z`, `X.Y`, `latest`) ve kaynak + derlenmiş web uygulaması arşivleriyle GitHub Release’i oluşturur. Son ekli bir sürüm (`pyproject.toml`’da `X.Y.Z-rc.1`, etiket `vX.Y.Z-rc.1`) ön sürüm olarak yayımlanır ve `latest`’i değiştirmez. Kontroller geçmeden hiçbir şey yayımlanmaz ve yayın adımları tekrarlanabilir: bir adım geçici bir nedenle (ağ, kayıt deposu) başarısız olursa iş akışını yeniden çalıştırın.

İlk sürümden sonra GitHub’da paketin ayarlarını bir kez açıp herkese açık yapın (GHCR paketleri gizli başlar); aksi halde `docker pull` oturum açmayı gerektirir.

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
