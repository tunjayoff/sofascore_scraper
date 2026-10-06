# SofaScore Scraper

[![CI](https://github.com/tunjayoff/sofascore_scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/tunjayoff/sofascore_scraper/actions/workflows/ci.yml)

**English:** [README.md](README.md)

SofaScore’un herkese açık HTTP API’lerinden futbol, basketbol ve tenis maç verisi indiren, yerelde (JSON ve CSV) saklayan ve **web** arayüzüyle sunan, sunucularda ve betiklerde komut satırıyla (`ssc`) çalışan Python uygulaması.

Bu proje SofaScore ile bağlantılı değildir. İstek hızına dikkat edin ve ilgili kullanım koşullarına uyun.

## Özellikler

- **Üç spor** — Futbol, basketbol ve tenis. Her lig kendi sporunu hatırlar; web uygulamasının tamamı tek bir spora göre süzülebilir.
- **Ligler** — SofaScore’da arayarak (web) veya ID ile (`config/leagues.txt`) turnuva ekleme.
- **Sezonlar ve maçlar** — Bir veya birden fazla ligden sezon seçip tek seferde indirme; maçlara lig, sezon, tarih ve detayın inip inmediğine göre göz atma.
- **Maç detayları** — İstatistikler (periyot bazında), olaylar, kadrolar, aralarındaki maçlar ve form; spora göre yarı, çeyrek veya set bazında skor.
- **Web uygulaması** — Ligler, Maç indir, Maçlar, Etkinlik ve Ayarlar sayfaları; canlı ilerleme (SSE) ve anında etki eden Durdur; İngilizce ve Türkçe, ilk ziyarette tarayıcınızın diline göre; açık, koyu veya sisteme uyan tema.
- **Komut satırı** — Sunucular ve otomasyon için `ssc` (sync, fetch, export, backup, watch, serve); 2.x'in terminal menüsü 3.0'da kaldırıldı ([yerine ne geldi](#terminal-menüsünden-30da-kaldırıldı)).
- **Otomasyon** — CI/script için headless bayrakları (`--update-all`, `--fetch-mode`, `--league-id`, `--csv-export`, yollar).
- **Dışa aktarım** — Maçlar, dilimler ve düzeltmeler sabit, belgelenmiş bir şemada JSONL, CSV, Parquet ya da SQLite olarak; saklanan yükler olduğu gibi; 2.x'in “tüm maçlar” CSV’si. `ssc export` ya da HTTP API'den (v1 dışa aktarma işleri).

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
- **Platformlar:** Resmî olarak desteklenen platformlar Linux ve Docker'dır; Windows ve macOS elden geldiğince desteklenir (kurulum betikleri, başlatıcılar ve CI onları da kapsar, ama yalnızca orada görülen bir sorun sürüm yayımlamayı engellemez).
- **patchright için Chromium** — uygulamanın SofaScore'a eriştiği tarayıcı. Kurulum betikleri ve başlatıcı bir kez indirir (`python -m patchright install chromium --no-shell`). Bilgisayarda kurulu Google Chrome ya da Chromium **kullanılmaz**.
- **Node.js 20.19+ veya 22.12+ ve npm** — web uygulamasını (`frontend/`) derlemek için. Node.js kuruluysa kurulum betikleri ve `scripts/start_web.py` kendisi derler. Node.js yoksa terminal modları yine çalışır; web adresi uygulama yerine bir yardım sayfası gösterir.
- **Git** — `curl | bash` ile tek satır kurulum için gerekli (depoyu klonlar); elle indiriyorsanız isteğe bağlı.
- SofaScore’a ağ erişimi.

[Docker](#docker) ile bunların hiçbiri bilgisayarınızda gerekmez: imaj Python’u, derlenmiş web uygulamasını ve tarayıcıyı içerir.

## Kurulum

Birini seçin:

| Yol | Gerekenler | Kime uygun |
|-----|------------|------------|
| [Kurulum betiği](#hızlı-kurulum-betik) | Git, Python 3.10+, Node.js | Masaüstü: çift tıkla başlatıcı, `git pull` ile kolay güncelleme |
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

İmaj uygulamayı, derlenmiş web uygulamasını ve [BrowserBridge](#anti-bot-koruması-browserbridge)’in ihtiyaç duyduğu headless Chromium’u içerir. Root olmayan bir kullanıcıyla (uid 1000) çalışır ve varsayılan olarak `ssc serve`i (web uygulaması ve HTTP API) başlatır. Ayrıntılar, canlı servisin bir konteynerde çalıştırılması dahil, [docs/deploy/docker.md](docs/deploy/docker.md)'de (İngilizce).

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
| `/app/logs` | Log dosyası (`sofascore_scraper.log`, çevrilir, en çok yaklaşık 30 MB). Aynı satırlar stdout’a da yazılır (`docker logs`). Bkz. [Loglar ve tanılama](#loglar-ve-tanılama) |

Bilinmesi gerekenler:

- **Kullanıcı hesabı yoktur.** Örnekler portu yalnızca `127.0.0.1` üzerinde açar. Portu ağa açarsanız (`-p 8000:8000`) bir erişim belirteci ayarlayın (`-e SOFASCORE_API_TOKEN=<uzun rastgele değer>`): o olmadan porta ulaşabilen herkes veriyi okuyup silebilir ve ayarları değiştirebilir. Uygulama konteynerin içinde tüm arayüzleri dinler ve portun nasıl yayımlandığını göremez; bu yüzden belirteç yoksa her başlangıçta uyarır. Port yalnızca `127.0.0.1` üzerindeyse bu uyarıyı yok sayabilirsiniz. Ayrıca arayüzü açtığınız adı ya da IP’yi de bildirmeniz gerekir: `-e SOFASCORE_ALLOWED_HOSTS=localhost,127.0.0.1,sunucum.lan` (başka `Host` başlığıyla gelen istekler reddedilir; sağlık kontrolü kullandığı için `127.0.0.1` listede kalmalı). İzin listesi hiçbir yerde verilmemişse giriş noktası yerel adları kullanır. Bkz. [Güvenlik modeli](#güvenlik-modeli).
- **Ayarlar:** `.env.example` içindeki her değişken `-e` / `environment:` ile verilebilir. Bu şekilde verilen değişken her başlangıçta Ayarlar sayfasında kaydedilen değerin önüne geçer; bu yüzden yalnızca sabit kalmasını istediklerinizi verin (ör. `APP_LANGUAGE=tr`, `USE_PROXY` / `PROXY_URL`). `PORT` konteyner içindeki portu değiştirir.
- **Paylaşımlı bellek:** Chromium, Docker’ın 64 MB’lık varsayılanından fazlasına ihtiyaç duyar; `--shm-size=1g` (Compose’da `shm_size`) bunun içindir.
- **Klasör bağlama** (`-v ./data:/app/data`), klasör uid 1000 tarafından yazılabiliyorsa çalışır: `mkdir -p data config && sudo chown -R 1000:1000 data config`. Başka bir uid için imajı `--build-arg APP_UID=$(id -u) --build-arg APP_GID=$(id -g)` ile derleyin.
- **Diğer komutlar:** `serve [seçenekler]` `ssc serve`e gider; imaj adından sonraki öteki argümanlar `main.py`’ye gider: [komut satırının](#komut-satırı-ssc) bir komutu ya da bir sürüm daha eski bayraklar. Ör. `docker run --rm ghcr.io/tunjayoff/sofascore_scraper:latest --version` ya da aynı volume’larla zamanlanmış bir indirme: `docker compose run --rm sofascore-scraper sync`. Tarayıcı profilini aynı anda tek konteyner kullanabilir; bu şekilde indirme başlatmadan önce web konteynerini durdurun (`docker compose stop`). Profil meşgulken başlatılan ikinci konteyner uyarı yazar ve tarayıcısını açamaz.
- **Güncelleme:** `docker compose pull && docker compose up -d`. Veri, yapılandırma, tarayıcı profili ve log dosyaları volume’larda kalır.

### Sürüm arşivi

[Releases sayfasındaki](https://github.com/tunjayoff/sofascore_scraper/releases) her etiketli sürümde `sofascore-scraper-X.Y.Z.tar.gz` / `.zip` bulunur: o sürümün kaynağı ve derlenmiş web uygulaması; Node.js gerekmez. Arşivi açın ve [elle kurulum](#elle-kurulum)a `python -m venv` adımından devam edin ya da klasörün içinde `./scripts/install.sh` (Windows’ta `scripts\install.ps1`) çalıştırın.

### Elle kurulum

```bash
git clone https://github.com/tunjayoff/sofascore_scraper.git
cd sofascore_scraper
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt -c constraints.txt   # constraints.txt: CI'ın test ettiği tam sürümler
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
python main.py --doctor          # okunur rapor (ssc doctor ile aynı)
python main.py --doctor --json   # aynısı JSON olarak; betikler ve sunucular için (rapor zarfın "data" alanında)
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
| `budget` | Ortak istek bütçesi: varsayılanın (saniyede 5 istek) üstündeyse ya da kapalıysa uyarı. |

```text
[ OK ] Python: 3.14.0
[ OK ] Paketler: gerekli 13 paketin hepsi içe aktarılabiliyor
[FAIL] Tarayıcı: patchright 1.63.0 sürümünün Chromium derlemesi kurulu değil (beklenen yer: ...); kurulu Google Chrome kullanılmaz
       Çözüm: Şunu çalıştırın: .venv/bin/python -m patchright install chromium --no-shell
[WARN] Web arayüzü: derlenmemiş (frontend/dist yok): web uygulaması bunun yerine bir yardım sayfası gösterir; terminal modları onsuz çalışır
       Çözüm: cd frontend && npm install && npm run build   (ya da scripts/start_web.py ile başlatın, kendisi derler)
```

- **Çıkış kodu:** hiçbir denetim başarısız değilse `0` (uyarı olabilir), en az biri başarısızsa `1`. `--strict` uyarılarda da `1` ile çıkar.
- `--only python,browser` / `--skip frontend` denetim seçer; `--lang en|tr` dili belirler (varsayılan: uygulamanın dili, bkz. [Dil](#dil)).
- Uygulamanın kendi import'larından önce çalışır; paketler eksikken de çalışır (bildirdiği şeylerden biri de budur).
- `--live` ek olarak tarayıcı köprüsü üzerinden SofaScore'a **tek** gerçek istek atar. Başka hiçbir durumda atılmaz. Önce web uygulamasını durdurun: iki süreç aynı tarayıcı profilini açamaz.
- Başlatıcı (`scripts/start_web.py`) aynı denetimi her açılışta çalıştırır, eksik paketleri ve eksik tarayıcıyı kendisi kurar. Başka kod `src.doctor.run_checks()` / `src.doctor.report()` çağırabilir.

## Yapılandırma

### Ortam değişkenleri (`.env`)

Tüm anahtarlar `.env.example` içinde. Sık kullanılanlar:

| Değişken | Açıklama |
|----------|----------|
| `DATA_DIR` | Verinin kök dizini (varsayılan `data`). Web `ConfigManager` üzerinden okur. |
| `APP_LANGUAGE` | `en` veya `tr`. Boş (varsayılan) bırakılırsa dil sabitlenmez: sistem dili Türkçeyse Türkçe, değilse İngilizce kullanılır. Web uygulamasında **Ayarlar**’daki dil seçimi bu değeri yazar. Bkz. [Dil](#dil). |
| `MAX_CONCURRENT` | Paralel detay isteği üst sınırı. |
| `REQUEST_RATE_LIMIT` | **Tüm süreçlerin toplamı** için SofaScore'a saniyede istek sayısı (web uygulaması, CLI, her `--watch`, `--refresh-only`). Varsayılan `5`; daha yüksek bir değer ya da `0` / `off` (sınırsız) daha hızlıdır ama engellenme riskini artırır. Bkz. [Ortak istek bütçesi](#ortak-istek-bütçesi-tüm-süreçler). |
| `USE_PROXY` / `PROXY_URL` | İsteğe bağlı proxy: `http://`, `https://` ya da `socks5://`; örn. `http://kullanici:parola@sunucu:8080`. Web uygulamasında **Ayarlar → Bağlantı** altından da ayarlanır; kayıtlı parola bir daha gösterilmez (form ve API `***` gösterir, öyle bırakılırsa parola korunur). Yerleşik tarayıcı değişen proxy’yi uygulama yeniden başlayınca kullanır. |
| `FETCH_ONLY_FINISHED` | Maç listelerinde yalnız bitmiş maçları göster (`status.type == finished`). Varsayılan `true`. Listelenen her maç durumu ne olursa olsun saklanır; ayar neyin indirildiğini değil, listelerin neyi gösterdiğini süzer. |
| `REFRESH_WINDOW_HOURS` | Kaydedilen maçın başlangıçtan kaç saat boyunca geçici sayılıp yeniden okunacağı (varsayılan `72`, `0` = kapalı). Bkz. [Yenileme politikası](#yenileme-politikası). |
| `RATE_LIMIT_*` / `SERVER_ERROR_*` | Devre kesicinin eşikleri; işin tüm aşamalarında istek başına sayılır. Bkz. [Eksik dilimler, başarısız istekler ve devre kesici](#eksik-dilimler-başarısız-istekler-ve-devre-kesici). |
| `LOG_LEVEL` / `LOG_DIR` / `LOG_TO_FILE` / `LOG_MAX_MB` / `LOG_BACKUP_COUNT` | Log seviyesi, log dosyasının yeri ve çevrilmesi. Bkz. [Loglar ve tanılama](#loglar-ve-tanılama). |
| `SOFASCORE_API_TOKEN` | Web uygulaması ve API’si için isteğe bağlı erişim belirteci. Boş (varsayılan) = kapalı. Bkz. [Güvenlik modeli](#güvenlik-modeli). |
| `SOFASCORE_ALLOWED_HOSTS` | Web uygulamasının yanıt verdiği ana makine adları, virgülle ayrılmış (varsayılan `localhost,127.0.0.1,[::1]`). Bkz. [Güvenlik modeli](#güvenlik-modeli). |

Web **Ayarlar** sayfasından birçok değer düzenlenir; kayıt `.env`’i günceller.

### Dil

Uygulama İngilizce ve Türkçe konuşur. Terminal, `--doctor`, kurulum betikleri, başlatıcı ve web uygulaması dili aynı kuralla seçer:

1. **Sizin seçtiğiniz dil kazanır.** Bu, `.env` içindeki (ya da ortamdaki) `APP_LANGUAGE=en` veya `tr` değeridir; **Ayarlar** sayfasındaki dil seçimi de bu değeri yazar. Tarayıcı ayrıca kendisinde yapılan seçimi hatırlar.
2. **Seçim yoksa sistem dili kullanılır.** Terminal tarafı `LC_ALL`, `LC_MESSAGES` ve `LANG` değişkenlerine bakar (Windows’ta bunlar yoksa görüntüleme diline); web uygulaması tarayıcının dil listesine bakar ve elinde olan ilk dili alır.
3. **O da yoksa İngilizce.** Türkçe dışındaki her dil için de sonuç İngilizcedir.

Yeni kurulum hiçbir dili sabitlemez: `.env.example` içinde `APP_LANGUAGE` boş gelir; sistemi ya da tarayıcısı Türkçe olan Türkçe, diğer herkes İngilizce görür. Her yerde tek bir dili zorlamak için `APP_LANGUAGE` değerini ayarlayın. `.env` dosyasında `APP_LANGUAGE=tr` olan mevcut kurulum Türkçe kalır.

Log satırları (konsol ve log dosyası) dil ne olursa olsun şimdilik Türkçe yazılır; `--help` çıktısında `argparse`’ın kendi sözcükleri (`usage:`, `options:`) İngilizce kalır.

### Ortak istek bütçesi (tüm süreçler)

Her kod yolu kendi isteklerini sınırlar (`MAX_CONCURRENT`, beklemeler, izleyicinin 1 sn aralığı), ama ayrı süreçler birbirini görmez: spor başına bir `--watch`, bir web işi ve cron'dan `--refresh-only` birlikte çalışınca hızları toplanır. `REQUEST_RATE_LIMIT` hepsinin paylaştığı tek bütçedir. SofaScore'a giden her istek (curl ya da tarayıcı) önce, işletim sistemi dosya kilidiyle korunan küçük bir durum dosyasından sıradaki boş anı ayırır.

- **Varsayılan: tüm süreçlerin toplamı için saniyede `5` istek**; boşta geçen süreden sonra en fazla bir saniyelik bütçe (5 istek) art arda kullanılabilir. Bu, indirme yolunun kendi başına yapabildiğinin bilerek çok altındadır (varsayılan ayarlarla yaklaşık 20–60 istek/sn; ağsız ölçüm, yinelemek için `python scripts/bench_bulk_rate.py`): SofaScore'a binen yükü ve engellenme riskini düşük tutar.
- **Bir indirme ne kadar sürer.** Maç detayları futbolda maç başına 7 (teniste 8) istek tutar; 380 maçlık bir sezon yaklaşık 2.700 istektir: varsayılanla kabaca **9 dakika** (eskiden bir iki dakikaydı). Sınırı bütçe belirlediği sürece `MAX_CONCURRENT`'i yükseltmek indirmeyi hızlandırmaz.
- **Yükseltmek ya da kapatmak sizin kararınız ve sizin riskinizdir.** `.env` içinde `REQUEST_RATE_LIMIT=20` yazın (dört kat hızlı) ya da web uygulamasında **Ayarlar → Gelişmiş** altındaki **Ortak istek bütçesi**ni değiştirin; çalışan web uygulaması bunu hemen uygular, çalışan diğer süreçler `.env`'i başlarken okur. `0` ya da `off` sınırı tümüyle kaldırır: her süreç yine kendi başınadır ve olabildiğince hızlı istek atar. İkisi de SofaScore'un sizi engelleme olasılığını artırır; değer 5'in üstündeyken ya da kapalıyken Ayarlar sayfası bir uyarı gösterir.
- **Daha nazik olmak için düşürün**, ör. `1`. `1` ve altında istekler eşit aralıklı olur. Sırası için uzun süre bekleyen istek düşürülmez: bu bekleme, tarayıcı köprüsünün 120 sn'lik istek zaman aşımından sayılmaz.
- **İzleyiciler** ayrıca 1 istek/sn'lik ortak bir şeridi paylaşır: spor başına bir `--watch` çalışsa da istekler süreç başına değil toplamda en az 1 sn aralıklıdır.
- **Durumun yeri:** `~/.cache/sofascore_scraper/throttle/` (`SOFASCORE_THROTTLE_DIR` ile değişir). Bu klasörü paylaşan süreçler bütçeyi paylaşır; konteynerlerde ortak bir volume gösterin.
- **Hata durumunda:** süreç ölünce kilidi işletim sistemi bırakır; çöken süreç bayat kilit bırakamaz. Klasöre yazılamıyorsa ya da kilit 1 sn içinde alınamıyorsa istekler engellenmez: o süreç kendi sayacıyla devam eder, bir kez uyarı loglar ve 30 sn sonra dosyayı yeniden dener.

### Lig listesi (`config/leagues.txt`)

Her satır `Ad: ID` biçimindedir; ID, SofaScore **unique tournament** sayısal ID’sidir (turnuva URL’sinde yer alır, ör. `.../premier-league/17` → `17`). Dosya size aittir ve git’te takip edilmez: ilk çalıştırmada `config/leagues.example.txt`’den oluşturulur; o dosyada lig yoktur. Yeni kurulum boş başlar: ligleri web uygulamasından (**Ligler → Lig ekle**; ligin sporunu da kaydeder) ya da `ssc follows add` ile ekleyin. Web uygulamasında veya CLI’da lig ekleyip kaldırdığınızda satırları uygulama kendisi günceller.

CLI ile özel yol:

```bash
python main.py --config /yol/leagues.txt --data-dir /yol/veri
```

`--config` ve `--data-dir` **headless** bayraklarında geçerlidir. Web sunucusu projedeki `.env` ile tekil `ConfigManager` kullanır; hem CLI hem web kullanacaksanız `DATA_DIR` vb. ile aynı veri dizinine hizalayın.

## Kullanım

### Nasıl kullanılır? (hızlı başlangıç)

**Web (çoğu kullanıcı için uygun)**

1. **Kurulum** ve **Yapılandırma** adımlarını tamamlayın (`pip install`, `cp .env.example .env`). Veriyi `./data` dışında tutmak isterseniz `DATA_DIR` ayarlayın.
2. Uygulamayı başlatın: `./start-sofascore.sh` (veya `python scripts/start_web.py`; Windows'ta `Start SofaScore.bat`, macOS'ta `Start SofaScore.command` dosyasına çift tıklayın). Başlatıcı `.venv` yoksa oluşturur, [kurulumu denetler](#kurulumu-denetleme-doctor), eksikleri kurar (Python paketleri, tarayıcı), `frontend/dist/` yoksa ve Node.js kuruluysa web uygulamasını derler, sonra `http://127.0.0.1:8000` adresini açar. `ssc serve` (ya da `python -m src.cli.main serve`) yalnızca sunucuyu başlatır, hiçbir şey kurmaz.
   Kodu güncelledikten sonra (`git pull`) web uygulamasını kendiniz yeniden derleyin: `cd frontend && npm install && npm run build`. Başlatma betiği sadece `frontend/dist/` yoksa derler; aksi halde eski arayüzü görmeye devam edersiniz.
3. **Spor** — Kenar menünün üstündeki seçici (Tümü / Futbol / Basketbol / Tenis) bütün sayfaları süzer. Üzerinde çalıştığınız sporu seçin.
4. **Ligler** — Yeni kurulumda lig yoktur; sayfa bir **Lig ekle** düğmesiyle açılır. **Lig ekle** SofaScore’da arar; sonuçları spora göre süzüp **Ekle**’ye basın. Sporu bilinmeyen bir ligde (örneğin `config/leagues.txt`’ye elle eklenmiş) **Spor seç** kutusu çıkar; bir kez seçmeniz yeterli, kaydedilir.
5. **Maç indir** — Sol sütunda lig seçin, ortada sezonları işaretleyin (sezon listesi ilk seferde kendiliğinden gelir; **Son sezon** / **Son 3 sezon** kısayolları vardır). Birden fazla ligden sezon seçebilirsiniz; hepsi sağdaki **İndirme listesi**’nde toplanır. **N sezonu indir**’e basın. Maçlar ve maç detayları (istatistik, olaylar, kadrolar) birlikte indirilir.
6. İndirme sürerken kenar menünün altındaki kart ilerlemeyi gösterir; **Durdur** hemen etki eder: yeni istek gönderilmez, yeniden deneme beklemeleri kesilir; o an havada olan bir istek zaman aşımı (`REQUEST_TIMEOUT`) kadar sürebilir. Aynı anda tek indirme çalışır. **Etkinlik** şimdiki ve geçmiş indirmeleri listeler.
7. **Maçlar** — Lig, sezon, tarih ve **Detay** (olanlar / eksikler) ile süzün. Bir ligde detayı eksik maç varsa (genelde yarıda durdurulan bir indirmeden kalır) üstte **Eksikleri indir** şeridi çıkar. Bir satıra tıklayınca maç açılır: periyot skorları, özet, istatistikler, olaylar ve kadrolar.
8. **Ayarlar** — Dil ve tema; veri klasörü, disk kullanımı, **Yedek al** ve **Tüm veriyi sil**; **Bağlantı**: bağlantı testi (**Bağlantıyı sına**, yalnızca bastığınızda SofaScore’a tek bir istek gönderir ve ne olduğunu söyler) ve proxy; gelişmiş istek ayarları (zaman aşımı, eşzamanlılık, bekleme süreleri, deneme sayısı).

> **Tüm veriyi sil** indirilmiş bütün sezon, maç ve detayları siler ve geri alınamaz. Önce yedek alın. Yedek, `data/backups/` (yani `DATA_DIR` içi) altında `data/`, `leagues.txt` ve `league_sports.json` içeren bir zip’tir. `.env` proxy kimlik bilgisi ve erişim belirteci içerebileceği için dahil edilmez; isterseniz `POST /api/data/backup` isteğine `?include_env=true` ekleyin (böyle bir yedeğin dosya adında `_with_env` bulunur ve dosyayı yalnızca sahibi okuyabilir). Geri yüklemek için uygulamayı durdurun, `data/` klasörünü proje klasörüne (veya `DATA_DIR`’e) açın; `leagues.txt` ve `league_sports.json`’ı da geri istiyorsanız `config/` altına kopyalayın.

> **İndirme sürerken** **Yedek al**, **Tüm veriyi sil**, lig kaldırma ve veri klasörünü değiştirme bir mesajla reddedilir: indirmeyi durdurun ya da bitmesini bekleyin. Yedekleme ya da silme sürerken de indirme başlatılamaz. Veri klasörü değişikliği hemen geçerli olur (yeniden başlatma gerekmez): indirmeler ve **Etkinlik** geçmişi artık yeni klasörü kullanır (her veri klasörü kendi geçmişini `.meta/jobs.db` içinde tutar); eski klasördeki dosyalar taşınmaz.

**Komut satırı**

Terminal menüsü 3.0'da kaldırıldı: argümansız `python main.py` kısa bir yardım yazar ve 2 koduyla çıkar. Web uygulamasını, betikler ve sunucular için de [komut satırını](#komut-satırı-ssc) kullanın; menünün her öğesinin nereye gittiğini [bu tablo](#terminal-menüsünden-30da-kaldırıldı) söyler.

**İpuçları**

- Büyük ligde ilk indirme uzun sürebilir; önce tek lig ve az sayıda sezonla deneyin.
- Hız sınırı veya çok hata görürseniz **Ayarlar**’dan **ortak istek bütçesini** (`REQUEST_RATE_LIMIT`) düşürün (yükselttiyseniz ya da kapattıysanız varsayılan `5`’e dönün); `--ignore-rate-limit` yalnız bilinçli kullanımda.
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

### Loglar ve tanılama

Uygulamanın logladığı her şey konsola **ve** bir log dosyasına yazılır; başlatıcı penceresi kapandıktan ya da gece süren bir indirme başarısız olduktan sonra da çıktı elinizde kalır.

- **Nerede:** proje klasöründeki `logs/sofascore_scraper.log`. Klasörü `LOG_DIR` ile değiştirin (göreli yol çalışma dizinine değil, proje klasörüne göre çözülür). Web uygulaması ve komut satırı (`ssc` ile kullanımdan kalkan `--headless`, `--watch` ve `--refresh-only` bayrakları) aynı dosyaya yazar; her satırda süreç numarası bulunur.
- **Boyut:** dosya `LOG_MAX_MB`'a (varsayılan 5 MB) ulaşınca çevrilir ve `LOG_BACKUP_COUNT` (varsayılan 5) eski dosya `.1` … `.5` olarak tutulur; loglar yaklaşık 30 MB'ı geçmez. Windows'ta açık bir dosya yeniden adlandırılamaz: log dosyasına birden çok süreç yazarken (örneğin web uygulaması ve bir `--watch`) dosya çevrilmez ve sınırı aşabilir; tek süreç kaldığında yeniden çevrilir.
- **Seviye:** `LOG_LEVEL` (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`; `DEBUG=true` seviyeyi `DEBUG` yapar). **Ayarlar**'dan değiştirince çalışan web uygulamasında hemen geçerli olur, yeniden başlatmak gerekmez. O sırada çalışan diğer süreçler bir sonraki başlatılışlarında alır.
- **Gizli değerler maskelenir** (dosyada ve konsolda), satır yazılmadan önce: captcha token'ı, cookie'ler, `Authorization` başlıkları, proxy kimlik bilgisi (`http://kullanıcı:parola@host`, `http://***@host` olur) ve `.env`'de adı gizli bir şeye benzeyen anahtarların değerleri (`*TOKEN*`, `*SECRET*`, `*PASSWORD*`, `*_KEY`, …).
- **Sunucu / Docker:** konsol (stdout) her zaman açıktır ve kapsayıcıda birincil çıktıdır; stdout terminal değilse satırlar düz, zaman damgalı metindir. Docker imajında dosya `/app/logs` volume'una yazılır; yalnızca `docker logs` kullanmak için `LOG_TO_FILE=false` yapın. Klasöre yazılamıyorsa uygulama bunu bir kez söyler ve yalnızca konsolla devam eder.

**Sorun bildirirken** tanılama paketini ekleyin. Küçük bir zip'tir: `diagnostics.json` (uygulama sürümü ve commit'i, Python ve işletim sistemi, paket sürümleri, gizli değerleri maskelenmiş ayarlar, köprü sağlığı, istek bütçesi, [kurulum denetimi](#kurulumu-denetleme-doctor) sonuçları (tarayıcı başlatılmadan), son indirme işleri) ve `log_tail.txt` (son 1000 log satırı). Ev dizininiz `~` olarak yazılır; uygulamanın tanımadığı `.env` anahtarlarının değerleri pakete girmez. Göndermeden önce içine göz atın.

```bash
python main.py --diagnostics              # logs/sofascore-diagnostics-<zaman>.zip yazar ve yolunu basar
python main.py --diagnostics ./rapor.zip  # ya da istediğiniz yol / klasör
```

Web uygulaması çalışırken (Docker dahil) aynı paket `http://127.0.0.1:8000/api/diagnostics/bundle` adresinden iner; "SofaScore bizi engelliyor" bildirimleri için doğrusu budur: köprü sağlığı süreç başınadır, web uygulamasının durumunu yalnızca onun paketi taşır. `GET /api/logs?limit=200&level=WARNING` son log kayıtlarını JSON olarak döndürür.

### Web uygulaması

```bash
ssc serve                   # ya da: python -m src.cli.main serve
```

Varsayılan adres: `http://127.0.0.1:8000` (yapılandırma dosyasındaki `[server] host` ve `port` varsayılanı değiştirir). Sunucu yalnızca bu bilgisayarı dinler. `--host` onu ağa açar; önce [Güvenlik modeli](#güvenlik-modeli) bölümünü okuyun. Tek bir adres (`--host 192.168.1.5`) olduğu gibi çalışır. `--host 0.0.0.0` (tüm arayüzler) ayrıca izin verilen ana makine adlarını ister (`--allowed-hosts`, `[server] allowed_hosts` ya da `SOFASCORE_ALLOWED_HOSTS`), onlar olmadan çıkış kodu 2 ile başlamaz; `SOFASCORE_API_TOKEN` yoksa uygulama başlangıçta, porta ulaşabilen herkesin veriyi okuyup silebileceği ve ayarları değiştirebileceği konusunda uyarır. `--port` portu değiştirir, `--dev` kod değişince yeniden başlatır. Ctrl+C ya da SIGTERM onu 0 çıkış koduyla durdurur; yapılandırılmış sink'ler o çalışırken teslim edilir. Sağlık kontrolü: `GET /health` (sürümü de bildirir). `python main.py --web` bir sürüm daha çalışır ve `ssc serve`i çalıştırır. systemd servisi, ters vekil, yedekler: [docs/deploy/](docs/deploy/README.md) (İngilizce).

**Zamanlanmış indirmeler (isteğe bağlı, varsayılan olarak kapalı):** `ssc serve --scheduler` ya da yapılandırma dosyasında `[schedule] enabled = true`, dosyadaki `[[schedule.task]]` girdilerini web sunucusunun içinde çalıştırır: `run = "sync"`, `"fetch"`, `"refresh"`, `"backup"` ya da `"prune-history"`; `every = "6h"` ya da `cron = "15 */6 * * *"` (makinenin yerel saati). Her çalışma iş listesinde görünen sıradan bir iştir; görevin önceki çalışması ya da başka bir indirme veri klasörünü tutuyorsa çalışma atlanır ve günlüğe yazılır. `every` görevi iş geçmişindeki son çalışmasından sayar: sunucuyu yeniden başlatmak sayacı sıfırlamaz. `prune-history` `older_than` ister (örneğin `"90d"`) ve saklanan dilim geçmişinin (bahis oranları) daha eski anlık görüntülerini siler, her dilimin en yenisi kalır. `ssc config validate` görevleri denetler; `--no-scheduler` zamanlayıcıyı bir çalışma için kapatır.

Arka plan işlemleri `GET /api/scrape/status` ve `GET /api/scrape/stream` (SSE) ile izlenir. Ağır dosya işleri event loop dışına alındığından uzun çekimler sırasında arayüz genelde yanıt vermeye devam eder.

### Güvenlik modeli

Web uygulamasında **kullanıcı hesabı yoktur**. Varsayılan olarak yalnızca bu bilgisayarı dinler; bunun için tasarlanmıştır. Onu ağa açmak yönetici olarak sizin kararınızdır; kimlerin ulaşabileceğini sınırlamak da (güvenlik duvarı, VPN, ters vekil / reverse proxy) size düşer. Uygulama bu önlemleri boşa çıkarmaz ve hiçbir güvenlik duvarının durduramadığı, kendi tarayıcınız üzerinden gelen saldırılara karşı korur.

**Uygulamanın yaptıkları**

- **Yalnızca bildiği adlara yanıt verir** (DNS rebinding). Bir istek ancak `Host` başlığı izin listesindeyse yanıtlanır: varsayılan liste `localhost`, `127.0.0.1` ve `[::1]`. `SOFASCORE_ALLOWED_HOSTS` (virgülle ayrılmış; `.env`’de ya da ortamda) listenin yerine geçer ve her zaman yazıldığı gibi kullanılır. `--host 192.168.1.5` o adresi kendiliğinden ekler. `--host 0.0.0.0` (tüm arayüzler), `SOFASCORE_ALLOWED_HOSTS` hangi adlara yanıt verileceğini söyleyene kadar başlamaz. `--allow-any-host` (ya da `SOFASCORE_ALLOWED_HOSTS=*`) her ada yanıt verir; bu **güvensizdir** ve korumayı kapatır.
- **Başka sitelerin tetiklediği yazmaları reddeder** (CSRF). Hiçbir `GET` uç noktası bir şey değiştirmez. Durum değiştiren her istek (indirme başlatma ya da durdurma, ayar kaydetme, veri silme, yedek alma, SofaScore’da arama), tarayıcı onu başka bir sitenin gönderdiğini bildirdiğinde (`Sec-Fetch-Site`, `Origin`) `403` ile yanıtlanır. Bu başlıkların hiçbirini göndermeyen programlar (ör. curl) etkilenmez.
- **Her yanıtla güvenlik başlıkları gönderir**: Content-Security-Policy (betik, stil, yazı tipi ve istekler yalnızca uygulamanın kendisinden; satır içi betik, `eval` ve çerçeveleme yok), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` ve `Cross-Origin-Resource-Policy: same-origin`. İki istisna: bu politikadan önce derlenmiş bir web arayüzü hâlâ `eval` ister; yeniden derleyene kadar `'unsafe-eval'` ile (ve log’da bir uyarıyla) sunulur. API belge sayfaları `/docs` ve `/redoc` ise betiklerini bir CDN’den yükler ve kendilerine özel bir politika alır.
- **İsteğe bağlı erişim belirteci.** Varsayılan olarak kapalıdır. `SOFASCORE_API_TOKEN`’a uzun, rastgele bir değer verip uygulamayı yeniden başlatın; `python -c "import secrets; print(secrets.token_urlsafe(32))"` bir tane üretir. Bundan sonra her `/api` isteği belirteci ister. Programlar `Authorization: Bearer <belirteç>` gönderir. Web uygulaması onu bir kez sorar ve 30 gün geçerli, `HttpOnly`, `SameSite=Strict` bir oturum cookie’si tutar (canlı durum akışı da aynı cookie’yi kullanır); **Ayarlar → Genel → Oturumu kapat** oturumu bitirir, belirteci değiştirmek tüm oturumları bitirir. `GET /health` sağlık denetimleri için açık kalır, ama belirteç olmadan yalnızca `{"status": "ok"}` döndürür. Belirteç sabit sürede karşılaştırılır; log’da, tanılama paketinde ve API yanıtlarında hiç geçmez.
- **Başlangıçta bir kez uyarır**: yerel olmayan bir adresi belirteç olmadan dinliyorsa.
- **Gizli değerleri diskte özel tutar.** `.env` (proxy parolası, belirteçler) `0600`, tarayıcı profili klasörü (SofaScore cookie’leri) `0700` izinleriyle oluşturulur; var olanlar her başlangıçta daraltılır. `.env` içeren bir yedek bunu dosya adında söyler (`backup_…_with_env_….zip`) ve yalnızca sahibince okunur. Windows’ta dosyalar bunun yerine kullanıcı klasörünüzün izinlerine dayanır. Proxy parolası ayarlar API’sinde, log’larda, tanılama paketinde ve API’nin döndürdüğü hata iletilerinde maskelenir.

**Ağa açmadan önce sizin yapmanız gerekenler**

- `SOFASCORE_API_TOKEN` ayarlayın. O olmadan porta ulaşabilen herkes verinizi okuyup silebilir ve ayarları değiştirebilir.
- Porta kimlerin ulaşabileceğini sınırlayın (güvenlik duvarı, VPN). Yanlış belirteçler istemci adresi başına ve yalnızca bellekte sınırlanır (ters vekil arkasında bkz. [docs/deploy](docs/deploy/README.md#behind-a-reverse-proxy)); bu yüzden belirteç yine de uzun ve rastgele olmalıdır.
- Önüne TLS koyun. Uygulama düz HTTP konuşur: TLS’i sonlandıran bir ters vekil olmadan belirteç ve oturum cookie’si ağdan şifresiz geçer. Vekil özgün `Host` başlığını iletmelidir; iletmiyorsa gönderdiği ad `SOFASCORE_ALLOWED_HOSTS` içinde olmalıdır.
- Docker’da konteyner kendi ağı içinde her zaman tüm arayüzleri dinler ve kimin ulaşabileceğine `-p` karar verir; bu yüzden başlangıç uyarısı port yalnızca `127.0.0.1` üzerinde yayımlandığında da çıkar (o zaman yok sayılabilir): portu `127.0.0.1` dışına açıyorsanız belirteci ve `SOFASCORE_ALLOWED_HOSTS`’u kendiniz ayarlayın.

```bash
curl -H "Authorization: Bearer $SOFASCORE_API_TOKEN" http://127.0.0.1:8000/api/leagues
```

### Komut satırı (`ssc`)

Sunucular ve otomasyon için komut satırı. Hiçbir şey sormaz: sonuç stdout'a, loglar ve hatalar stderr'e gider; ne olduğunu çıkış kodu söyler. `pip install -e .` sonrası `ssc <komut>`, kurulumsuz `python main.py <komut>` ya da `python -m src.cli.main <komut>`.

```bash
ssc sync                                   # yapılandırılmış her lig: sezon listeleri, programlar, sonra detayı gereken maçlar
ssc sync --tournament 17 --only events     # tek lig, yalnızca maç detayları
ssc sync --dry-run                         # istek atmaz: neyin indirileceği ve en az kaç istek gerektiği
ssc fetch event 12345678 12345679          # bu maçlar, yapılandırılmış olsun olmasın
ssc fetch tournament 17 --season 61627     # tek turnuva (ya da bütün sezonları)
ssc refresh [--tournament 17] [--include-legacy]   # yalnızca geçici kayıtları yeniden oku (günlük cron)
ssc export --dataset events --format csv --out maclar.csv   # veri şemasının bir veri kümesi (events, slices, changes)
ssc export --out maclar.csv                # geniş CSV (legacy-wide-csv); --out yoksa: match_details/processed/
ssc export --schema raw --format jsonl --out ham.jsonl   # saklanan yükler olduğu gibi
ssc data recheck-unavailable [--all]       # "dilim yok" işaretlerini aç (istek atmaz)
ssc data clear --scope events --yes        # maç detaylarını sil; eski ve yeni düzende birlikte
ssc follows list | add | remove | export   # canlı servisin izledikleri; export [[follow]] tablolarını yazar
ssc status [--coverage] [--check]          # veri özeti, depo, kilitler, çalışan ve son iş, canlı servis
ssc jobs list | show ID | cancel ID | tail ID [--follow]   # iş geçmişi ve denetimi, süreçler arasında
ssc serve [--host H] [--port P]            # web uygulaması ve HTTP API (bkz. Web uygulaması)
ssc watch / ssc events                     # canlı servis ve olay günlüğü (bkz. İzleme modu)
ssc doctor | describe | config | version | diagnostics | migrate | catalog | backup
```

- **Makinece okunur çıktı.** `--json` tam olarak bir JSON belgesi yazar (`{"ok", "command", "schema": "sofascore.cli/1", "version", "data" | "error", ...}`; şeması `ssc describe schemas`'ta). Akış komutları (`events`, `jobs tail`) satır başına bir JSON nesnesi yazar, her birinde `type` vardır, akış `{"type": "end", ...}` satırıyla biter; akışın ortasındaki hata bir `{"type": "error", ...}` satırıdır. Boruyu erken kapatan okuyucu (`ssc events | head -1`) hata değildir.
- **Genel bayraklar** (komuttan önce ya da sonra): `--config DOSYA`, `--data-dir DİZİN`, `--json` / `--output text|json|ndjson`, `--quiet`, `--verbose`, `--log-level`, `--log-format text|json` (stderr'deki log satırları birer JSON nesnesi), `--no-color`, `--lang en|tr`, `--rate N|off`, `--ignore-breaker`, `--wait SANİYE` (meşgul veri klasörünü 6 ile çıkmak yerine bekle), `--progress none|text|ndjson` (işin ilerlemesi stderr'e).
- **Çıkış kodları:** **0** başarı ya da yapılacak iş yok, **1** genel hata (dışa aktarılacak maç olmayan `export`, bilinmeyen iş kimliği, sağlıksız depoda `status --check` da), **2** kullanım ya da yapılandırma hatası, **3** kısmi başarı (iş bitti ama bazı maçlar ya da listeler alınamadı), **4** SofaScore engelliyor: devre kesici işi durdurdu, **5** depolama hatası (disk dolu, izin yok), **6** veri klasörünü başka bir süreç tutuyor (hata onu adıyla söyler), **130** / **143** Ctrl+C / SIGTERM ile iptal.
- **İşi durdurmak.** Ctrl+C ya da SIGTERM çalışan `sync`, `fetch` ya da `refresh` işini iptal eder: istekler bir sonraki denetimde durur, yazılmakta olan maç ya bütün olarak yazılır ya da hiç yazılmaz, iş `cancelled` olarak saklanır, kilit bırakılır ve sonuç yine yazılır. İkinci Ctrl+C hemen çıkar. `ssc jobs cancel ID` bir işi başka herhangi bir süreçten (web uygulamasının işleri dahil) iptal eder.
- **Veri klasörü başına tek yazar.** İndirmeler ve `data recheck-unavailable` klasörün yazar kilidini tutar; kilit başkasındaysa **6** ile çıkar ve sahibini (süreç, makine, amaç, başlangıç) yazar. Okuma komutları (`status`, `events`, `export`, `jobs list`) kilit almaz.
- **Sink'ler.** `sofascore.toml`'un `[[sink]]` çıktıları (stdout, dosya, webhook) tek seferlik işlerin olaylarını da (`job.started`, `job.finished`) alır: iş başlamadan kaydedilirler, iş bitince en çok 10 sn boşaltılırlar. `ssc config validate` onları da denetler.
- **Veri kümelerini dışa aktarmak.** `ssc export --dataset events|slices|changes` veri şemasının kayıtlarını yazar (sürüm 1; alan alan `docs/design/04-schema-v1.md`'de, JSON Schema olarak `ssc describe schemas`'ta): `events` maçlardır (durum, skor, kazanan ve kaydın ne kadar güvenilir olduğu), `slices` bir maç hakkında saklanan her yanıtın durumu (istatistik, kadro, …; yüklerin kendisi ham dışa aktarmadadır), `changes` saklanan maçlarda bulunan düzeltmeler. `--format jsonl` (varsayılan) her satıra bir kaydı API'nin verdiği haliyle yazar; `csv`, `parquet` ve `sqlite` her alana bir sütun yazar, adı alanın yoludur (`status_class`, `score_home`, `quality_observed_at_utc`); listeler JSON metni, eksik değer boş hücredir. Parquet için isteğe bağlı `pyarrow` paketi gerekir (`pip install -e ".[parquet]"`). Süzgeçler: `--sport`, `--tournament`, `--season`, `--event`, `--status` (durum sınıfı), `--from` / `--to` (ISO tarih, UTC; `changes` için düzeltmenin kaydedildiği zaman). `--out` verilmezse dosya veri klasörünün `exports/` dizinine yazılır; `--out -` JSONL ya da CSV'yi stdout'a yazar; var olan dosyanın üzerine yalnızca `--force` ile yazılır. `--schema raw` saklanan SofaScore yüklerini yazar (`--dataset events`: yalnızca maç yükü; `--dataset` yoksa: her yük). `--json` sonucu kayıtların `schema_version`'ını verir. `--dataset` ve `--schema` olmadan `ssc export` yine 2.x'in geniş CSV'sini yazar. HTTP API'de aynı veri kümeleri API v1'in dışa aktarma işidir (`POST /api/v1/jobs`, `kind: "export"`; dosya `GET /api/v1/exports/{id}/download`).

### Terminal menüsünden (3.0'da kaldırıldı)

`python main.py` artık bir menü açmaz: insanlar web uygulamasını kullanır, komut satırı sunucular ve otomasyon içindir. Argümansız çalıştırıldığında kısa bir yardım yazar (web uygulaması `ssc serve`, komutlar `ssc --help`) ve **2** koduyla çıkar. Menünün her öğesinin bir karşılığı var:

| Menü öğesi | Şimdi |
|------------|-------|
| Ligler: listele, ekle, yeniden yükle, ara | Web uygulamasında **Ligler** (SofaScore'da ara, ekle, kaldır); `ssc follows list`, `ssc follows add tournament ID --name AD --sport SPOR`, `ssc follows remove`; `config/leagues.txt`. Her komut yapılandırmayı başlarken okur; yeniden yüklenecek bir şey yoktur. |
| Sezonlar: hepsini güncelle, bir ligi güncelle, listele | `ssc sync` (sezon listeleri, maç programları, ardından maç detayları), `ssc fetch tournament ID`; web uygulamasında **Maç indir** (bir ligin sezon listesini çeker ve yeniler); `GET /api/v1/tournaments/{id}/seasons` |
| Maçlar: bir lig, tüm ligler, listele | `ssc fetch tournament ID --season ID`, `ssc sync --tournament ID`, `ssc sync`; web uygulamasında **Maç indir** ve **Maçlar**; `GET /api/v1/events` |
| Maç detayları: ID ile, hepsi | `ssc fetch event ID…`, `ssc sync --only events`; web uygulamasında **Maçlar → Eksikleri indir** |
| Maç detayları: bir maçın, bir ligin, hepsinin CSV'si | `ssc export --event ID`, `ssc export --tournament ID`, `ssc export` (`--out YOL` dosyayı seçer); `GET /api/export/csv` |
| İstatistikler: sistem, ligler, rapor dosyası | `ssc status` (`--coverage` turnuva başına maç ve detay sayılarını ekler; `--json > rapor.json` bir rapor dosyası yazar); web uygulamasında **Genel bakış** |
| Ayarlar: API, veri klasörü, görünüm, dil | Web uygulamasında **Ayarlar**; `.env` ya da `sofascore.toml` (`ssc config show` her değeri ve nereden geldiğini listeler); `--lang` |
| Ayarlar: veri klasörünü taşı | Uygulamayı durdurun, klasörü taşıyın, sonra `DATA_DIR`'i (web uygulamasında **Ayarlar**, `.env` ya da `--data-dir`) yeni yere çevirin. |
| Ayarlar: yedekle, geri yükle, temizle | `ssc backup create`, `ssc backup restore AD --yes`, `ssc data clear --all --yes`; web uygulamasında **Ayarlar** (yedekle, tüm verileri sil) |
| Ayarlar: hakkında | `ssc version` |

### Headless / otomasyon (kullanımdan kalkan bayraklar)

`python main.py`nin bayrakları bir sürüm daha çalışır. Her çalıştırma [komut satırının](#komut-satırı-ssc) bir komutuna çevrilir ve stderr'e onu adıyla söyleyen tek bir satır yazar (`--headless --update-all` → `ssc sync`, `--refresh-only` → `ssc refresh`, `--headless --csv-export` → `ssc export`, `--recheck-unavailable` → `ssc data recheck-unavailable`, `--watch` → `ssc watch --source poll --stdout`, `--doctor` → `ssc doctor`, `--diagnostics` → `ssc diagnostics`, `--web` → verilen `--host`, `--port`, `--dev` ve `--allow-any-host` ile `ssc serve --host 127.0.0.1 --port 8000`); o komutun çıktı kurallarını ve çıkış kodlarını kullanır. Terminal menüsü yok: bayraksız `python main.py` kısa bir yardım yazar ve **2** koduyla çıkar ([menünün yerine ne geldi](#terminal-menüsünden-30da-kaldırıldı)). `--headless` ile birlikte **`--update-all` ve/veya `--csv-export`** zorunludur; aksi halde hiçbir şey çalışmadan çıkış kodu **2** olur.

| Bayrak | Anlamı |
|--------|--------|
| `--headless` | Bir eylem çalıştırır (2.x'te terminal menüsünü atlayan bayrak) |
| `--update-all` | Çekim akışını çalıştır |
| `--fetch-mode full` | Sezon + maç listeleri + detay (varsayılan) |
| `--fetch-mode details` | Yalnız maç detayları (mevcut özet/fikstür CSV’lerine dayanır) |
| `--league-id ID` | `--update-all`’ı tek yapılandırılmış lige indir |
| `--csv-export` | İşlenmiş CSV veri setini üret/aktar |
| `--ignore-rate-limit` | Circuit breaker’ı kapatır (dikkatli kullanın) |
| `--refresh-only` | Yalnızca geçici kayıtları yeniden okur (`--headless` gerekmez); bkz. [Yenileme politikası](#yenileme-politikası) |
| `--refresh-legacy` | `observation.json` öncesi kaydedilmiş maçları da bir kez yeniler |
| `--recheck-unavailable [legacy\|all]` | "Bu dilim bu maçta yok" işaretlerini yeniden açar; sonraki indirme dilimi yeniden ister, bayrağın kendisi istek göndermez. Bkz. [Eksik dilimler](#eksik-dilimler-başarısız-istekler-ve-devre-kesici) |
| `--watch` | Canlı izleyici: `--sport` ve `--league-ids` ya da `--event-ids` (isteğe bağlı `--watch-hours`); bkz. [İzleme modu](#izleme-modu) |
| `--doctor` | Ortamı denetler ve çıkar, `0` = hazır, `1` = bir şey başarısız (`--headless` gerekmez); bkz. [Kurulumu denetleme](#kurulumu-denetleme-doctor) |

Örnekler:

```bash
python main.py --headless --update-all
python main.py --headless --update-all --fetch-mode details --league-id 52
python main.py --headless --csv-export --data-dir ./data
```

Çıkış kodları [komut satırınınkilerdir](#komut-satırı-ssc): **0** başarı, **2** kullanım hatası, **3** kısmi başarı, **4** devre kesici çalışmayı durdurdu, **5** veri diske yazılamadı, **6** aynı veri klasörüne başka bir süreç zaten yazıyor, **130** / **143** iptal. 3.0'dan önce devre kesici **2**, depolama hatası **1**, Ctrl+C **0** idi; her isteği reddedilen bir çalıştırma ve dışa aktarılacak maçı olmayan bir CSV dışa aktarması **0** ile bitiyordu.

Bir veri klasörüne aynı anda yalnızca bir süreç yazar. Web uygulamasında ya da başka bir headless çalıştırmada bir indirme sürerken `--headless --update-all`, `--refresh-only` ve `--recheck-unavailable` başlamaz: kilidi kimin tuttuğunu (süreç numarası, makine, amaç, başlangıç) yazar ve **6** ile çıkar. Başka bir canlı servis ya da izleyici çalışırken `--watch` için de böyledir. Tek başına `--headless --csv-export` bundan etkilenmez. `--config` artık yapılandırma dosyasıdır (`sofascore.toml`); oraya verilen bir lig dosyası (`.txt`) eskisi gibi yok sayılır ve bunu bir uyarı söyler.

### Yardım

```bash
python main.py --help            # kullanımdan kalkan bayraklar
python main.py sync --help       # yeni komut satırının bir komutu (ya da: ssc sync --help)
python main.py --version
```

## Veri yapısı (`DATA_DIR` altında)

Tipik düzen:

```text
data/
├── v3/
│   ├── events/        # Maç detayları, maç başına bir dizin: manifest.json + sıkıştırılmış dilimler (*.json.gz)
│   └── tournaments/   # Lig ve sezon başına sezon listeleri ve maç programları (sıkıştırılmış)
├── changes/           # Yenilemede bulunan bitiş sonrası değişiklikler, ay başına bir dosya (bkz. Yenileme politikası)
├── seasons/           # Lig başına sezon meta dosyaları
├── matches/           # Lig ve sezona göre maç / özet CSV
├── match_details/     # Önceki sürümlerin yazdığı maç başına JSON (basic, statistics, …)
│   └── processed/     # Birleştirilmiş CSV export
├── datasets/          # Yardımcı / ayrılmış kullanım
└── score_changes.jsonl  # Önceki sürümlerin değişiklik kaydı (durur, artık eklenmez)
```

Lig adlandırma ve migrasyonlara göre alt yollar biraz farklı olabilir.

Maç detayları sıkıştırılmış olarak `v3/events/<id / 1.000.000>/<(id / 1.000) mod 1.000>/<id>/` altında saklanır. Önceki sürümlerin `match_details/` altına yazdığı dizinler yerinde kalır ve uygulamada okunmaya devam eder; böyle bir maç yeniden yazıldığında (eksik dilim tamamlama, yenileme, işaretlerin yeniden denetimi) önce bugünkü hali `v3/`'e kopyalanır, eski dizine dokunulmaz. `match_details/`'i doğrudan okuyan programlar bu sürümün indirdiği maçları görmez.

### Eski verinin yeni düzene taşınması (`ssc migrate`)

Önceki sürümlerin yazdığı veri kendiliğinden taşınmaz. `ssc migrate` onu siz istediğinizde dönüştürür: `match_details/` altındaki maç klasörleri, `matches/` altındaki tur ve sayfa dosyaları, `seasons/` altındaki sezon listeleri ve `score_changes.jsonl` (aynı sıra numaralarıyla `changes/0000-legacy.jsonl` dosyasına kopyalanır). Her yeni kopya yerine konmadan önce geri okunur ve eskisiyle karşılaştırılır. Eski dosyalar, silinmelerini de istemedikçe yerinde kalır; bu aynı çalıştırmada ya da sonra yapılabilir.

```bash
ssc migrate --dry-run                   # neyin dönüştürüleceği, önceki ve sonraki boyut; hiçbir şeyi değiştirmez
ssc migrate                             # dönüştür ve doğrula; eski dosyalar yerinde kalır
ssc migrate --tournament 17 --limit 200 # tek lig, en çok 200 maç; devam etmek için yeniden çalıştırın
ssc migrate --delete-legacy --yes       # yeni kopyası yeniden doğrulanan her eski kopyayı da sil
ssc migrate --purge-derived --yes       # programı olan sezonların özetlerini ve match_details/processed/'i sil
```

- Maç klasöründeki maç verisi olmayan dosyalar (örneğin kendi notlarınız) yeni klasörün `_extra/` alt klasörüne olduğu gibi kopyalanır.
- Yalnızca özet CSV dosyası olan (tur ya da sayfa dosyası olmayan) bir sezon dönüştürülemez: yerinde kalır, okunmaya devam eder ve listelenir. Tanınmayan klasör ve dosyalar da öyle.
- Aynı veri klasöründe bir indirme, `ssc watch` ya da `--watch` çalışırken komut reddedilir (çıkış kodu **6**). Bazı girdiler başarısız olursa çalışma biter, o girdilerin eski kopyası yerinde kalır ve çıkış kodu **3**'tür. `--json` sonucun tamamını yazar.
- Komut başlamadan önce `config/leagues.txt`'yi takiplere yansıtır (uygulamanın her başlangıçta yaptığı gibi): yalnızca lig adıyla adlandırılmış bir sezon listesi (`<ad>_seasons.json`) böyle çözülür.
- Sahibin verisinde (detaylı 423 maç, 90 program sayfası, 6 sezon listesi) dönüştürülen dosyalar 71,7 MB yerine 7,2 MB tutar; çalışma yaklaşık 3 saniye sürdü.

`ssc catalog verify [--deep] [--repair]`, `ssc catalog reconcile [--deep]` ve `ssc catalog rebuild` saklanan dosyaların dizinini (`.meta/catalog.db`) denetler, günceller ya da yeniden kurar. `scripts/catalog_tool.py`'nin yerini alırlar; lig klasörlerini yerinde yeniden adlandıran `scripts/migrate_match_details.py` de kaldırıldı, çünkü yeni düzen klasör adlarına bağlı değildir.

Maç listesi veri klasörünün dizininden (`.meta/catalog.db`) okunur; dizin 3.0 düzenini ve 2.x dosyalarını birlikte kapsar. `match_details/processed/` içindeki export CSV geri okunmaz.

`config/leagues.txt`’nin (CLI’ın da okuduğu `ad: id` listesi) yanında `config/league_sports.json` her ligin sporunu `{"<id>": "football" | "basketball" | "tennis"}` olarak saklar. Lig web’den eklendiğinde, arayüzde spor seçildiğinde veya o ligin indirilmiş bir maçından doldurulur.

Desteklenen sporlar tek yerde, `src/sports.py`’deki kayıt defterinde tanımlıdır: spor başına skor biçimi, canlı izleyicinin parametreleri ve o spor için istenen maç detay uç noktaları. CLI, indirici, izleyici ve web API’si bu kayıt defterini okur.

### Eksik dilimler, başarısız istekler ve devre kesici

Her maç dizininde detay dilimi başına bir dosya bulunur (`statistics`, `lineups`, `incidents`, …); dilim başına iki kayıt tutulur (maçın `manifest.json`'ında; önceki sürümlerin dizinlerinde `_unavailable.json` ve `_slice_status.json`):

- "yok" sayısı: bitmiş bir maçta SofaScore'un dilim için kaç kez **kesin** "burada bir şey yok" yanıtı verdiği: HTTP 404 ya da içinde veri olmayan bir 200 yanıtı. İki kesin yanıttan sonra dilim o maç için beklenmez (ör. teniste kadro yoktur) ve maç tam sayılır.
- hata kaydı: dilimin son **başarısız** isteği: `reason` (`403`, `429`, `5xx`, `timeout`, `network`, `parse`), HTTP kodu, UTC zamanı ve art arda kaç kez olduğu. Başarısız istek hiçbir zaman "yok" sayılmaz: dilim beklenmeye devam eder, maç eksik görünür ve sonraki indirme dilimi yeniden ister.

Önceki sürümler boş gelen her sonucu sayıyordu; engelleme ya da kesinti sırasında başarısız olan istekler de buna dahildi. O işaretler gerçek olanlardan ayırt edilemez. Bu yüzden oldukları gibi bırakılır ve kendiliğinden **sıfırlanmaz**: sıfırlansaydı, "kadrosu yok" işaretli her tenis maçı sonraki çalıştırmada yeniden istenirdi. Yeniden denetlemek için:

```bash
python main.py --recheck-unavailable                 # kesin yanıtla doğrulanmamış işaretleri yeniden aç
python main.py --recheck-unavailable --league-id 17  # tek lig
python main.py --recheck-unavailable all             # bütün işaretleri yeniden aç
python main.py --recheck-unavailable --headless --update-all --fetch-mode details   # aç, sonra indir
```

Bayrağın kendisi istek göndermez; yeniden açılan dilimleri bir sonraki detay indirmesi ister. Kesin yanıtla doğrulanmış işaretler korunur, bu yüzden ikinci kez çalıştırmak hiçbir şeyi değiştirmez.

**Devre kesici.** İş başına tek bir kesici her isteğin son halini sayar: sezon listeleri, maç listeleri, `/event`, her detay dilimi ve yenilemeler. Art arda `RATE_LIMIT_THRESHOLD_CONSECUTIVE` istek başarısız olunca (varsayılan 20), tüm isteklerin `RATE_LIMIT_THRESHOLD_RATIO` kadarı başarısız olunca (varsayılan 0,9; ilk 50 istekten sonra) ya da art arda `SERVER_ERROR_THRESHOLD_CONSECUTIVE` kez 5xx gelince (varsayılan 50) devre kesilir. Tarayıcı köprüsü iş sırasında `blocked` durumuna geçtiyse (bkz. [köprü sağlığı](#sofascore-bizi-engelliyor-mu-köprü-sağlığı)) ve işin kendi istekleri de 403 ile bitiyorsa daha erken kesilir. 404 bir yanıttır, başarısızlık değildir. Devre kesilince iş hiçbir aşamada yeni istek göndermez ve nedenini söyler: web iş kartında görünür, `--headless` ve `--refresh-only` 2 koduyla çıkar.

**Depolama hataları.** Dosyaları yazılamayan maç indirilmiş değil, başarısız olarak bildirilir. Neden her maçta tekrarlanacaksa (disk ya da kota dolu, izin yok, salt okunur dosya sistemi) iş, yolu ve nedeni söyleyen bir mesajla durur.

Her maç kimliğine göre saklanır, SofaScore unique-tournament kimliği olmayan maç da (önceki sürümler bunları `match_details/_no_tournament/<spor>/<maç id>/` altına yazardı; o dizinler yerinde kalır).

## Yenileme politikası

SofaScore bazı sonuçları maç bittikten sonra da düzenliyor. Araştırma ölçümünde (`docs/status-matrix/README.md`, "Geriye dönük") nihai ya da periyot skoru `finished` sonrasında değişti: alt lig basketbolda 255 maçın 78'inde, alt lig futbolda 240 maçın 6'sında, üst lig basketbolda 145 maçın 4'ünde. En geç nihai skor değişikliği başlangıçtan 66,4 sa sonra geldi. Bu yüzden bir kez indirilen maç, SofaScore'un son hâlinden farklı kalabilir.

- **Kayıt ne zaman yenilenir?** Kaydedilen her maç, bizim okuduğumuz anı (`observed_at_utc`) ve SofaScore'un `changes.changeTimestamp` değerini tutar (`manifest.json`'da; önceki sürümlerin dizinlerinde `basic.json` yanındaki `observation.json`'da). `observed_at_utc < startTimestamp + REFRESH_WINDOW_HOURS` (varsayılan **72**) olduğu sürece kayıt *geçici* sayılır. Geçici kayıtlar sonraki indirmede (web işi, `--update-all` ya da `--refresh-only`) yeniden okunur. Bu sırada yalnızca `/event/{id}` çekilir, istatistik ve kadro çekilmez. Yenilemeler yeni ve eksik maçlardan sonra çalışır, iş kartında da ayrı sayılır ("N yenilendi (M değişti)"). Aynı kayıt en fazla `REFRESH_MIN_INTERVAL_HOURS` (varsayılan 6, ileri düzey `.env` ayarı) saatte bir yeniden okunur; saatlik indirmede aynı maç 72 kez çekilmez. Pencere kapandıktan sonra yapılan bir okumayla kayıt kesinleşir ve bir daha çekilmez.
- **Eski kayıtlar.** Bu özellikten önce kaydedilen maçlarda `observation.json` yok. Bunlar kesin sayılır, yani varsayılan ayar mevcut veri için **hiç** ek istek yapmaz. `--refresh-legacy` bu kayıtların her birini bir kez yeniden okur.
- **Kapatma.** `REFRESH_WINDOW_HOURS=0` (`.env` ya da **Ayarlar**).
- **Değişiklik kaydı.** Status üçlüsü, `winnerCode`, `homeScore`/`awayScore` alt alanlarından biri ya da `startTimestamp` farklıysa saklanan maç sayfası yenisiyle değiştirilir ve `DATA_DIR/changes/<yyyy>-<mm>.jsonl` dosyasına (git'e girmez) bir satır eklenir (önceki sürümler `score_changes.jsonl`'a eklerdi; o dosya durur ve okunmaya devam eder). Satır maçla aynı adımda yazılır, yarıda kesilen bir yazmada kaybolmaz: o maça bir sonraki yazmada ya da bir sonraki açılışta eklenir. Satır biçimi için İngilizce README'deki örneğe bakın.
  - `changed`: her alanın eski ve yeni değeri. SofaScore'un `changes` alanı yalnızca değişen alanın adını verir; skorun *ne kadar* değiştiği ilk kez bu dosyada kayda geçer.
  - `hours_after_start`: yeni `changeTimestamp` eksi başlangıç.
  - `tier_hint`: SofaScore'un oyuncu istatistiği kapsam bayrağı. Ligin gerçek seviyesini değil, SofaScore'un kapsam seviyesini gösterir.
  - Oynanmış sayılan bir maç sonradan iptale dönerse (`completed` → `void`) satıra `"status_regressed": true` yazılır. Aynı işaret maçın gözlemine de konur ve bir daha silinmez. Kayıt **silinmez**; silme kararı kullanıcınındır.

```bash
python main.py --refresh-only                 # yalnızca geçici kayıtları yenile (ör. günlük cron)
python main.py --refresh-only --league-id 17  # tek lig
python main.py --refresh-only --refresh-legacy
```

## İzleme modu

`python main.py --watch` canlı maçları takip edip **olay** üretir; hiçbir sonucu sonuçlandırmaz. 3.0'dan beri `ssc watch --source poll --stdout`un takma adıdır (yalnızca yoklama: hiçbir zaman tarayıcı başlatmaz): olaylar stdout'a satır başına bir JSON zarfı (`sofascore.event/1`) olarak yazılır ve olay günlüğünde saklanır; `ssc events` ve yapılandırılmış sink'ler oradan okur. Olaylarla ne yapılacağına tüketici karar verir.

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
- **Olaylar.** `live.status_changed` (`from`, `to`, `change_ts`, `score`; ilk `completed` olayı yenileme penceresi kapanana kadar `provisional: true` taşır, bkz. [Yenileme politikası](#yenileme-politikası)), `live.score_changed` ve `live.stuck`; her birinde `event_id`, `sport`, `tournament_id`, `seq` ve `ts` vardır. `DATA_DIR/watch_events.jsonl` ve `watch_state_{sport}.json` artık yazılmaz; eski dosyalar silinebilir.
- **Yeniden başlatma.** Her maçın son bilinen durumu veri klasörünün deposunda tutulur; yeniden başlatmada aynı geçiş iki kez olay olmaz. Ctrl+C ya da SIGTERM temiz kapatır, çıkış kodu 0'dır. Bir veri klasöründe tek canlı servis çalışır (ikincisi **6** ile çıkar).
- **Ne zaman durur?** `--event-ids` ile izlenen maçların hepsi bitince servis kapanır.

Neden bu sayılar: `events/live` CDN'de 5 sn önbellekte kalıyor; araştırmada düdük → `finished` medyan 20 sn, en fazla 302 sn sürdü (`docs/status-matrix/README.md`). 30 sn'den sık sorgulamak fayda getirmez.

### `ssc watch` canlı kaynakları

Yeni komut satırının canlı servisi `ssc watch`, kaynağını `--source` ile, `sofascore.toml` içindeki `[live] source` ile ya da `SOFASCORE_LIVE__SOURCE` ile seçer (yukarıdaki eski `main.py --watch` her zaman yoklar):

| Kaynak | Nasıl çalışır | Bellek |
|---|---|---|
| `page` (varsayılan) | izlenen her spor için bir tarayıcı sayfası açık tutar ve SofaScore'un kendi sayfasının açtığı push bağlantısını dinler; o bağlantının kimlik bilgisini hiç okumaz | spor başına yaklaşık 1,8–2,6 GB |
| `poll` | yalnızca yukarıda anlatılan yoklama; tarayıcı yok | ek bellek yok |
| `direct` (açık seçim) | hafif bir istemci push sunucusuna kendisi bağlanır; kimlik bilgisi sayfanın kendi bağlantısından okunur | yaklaşık 0,2 GB |

Yoklama her kaynağın yedeğidir ve hep çalışır: push sağlıklıyken seyrek, bağlantı sessiz ya da kopukken her `poll_interval`'da ve her yeniden bağlanmadan sonra bir kez. `page` açılamaz ya da çökerse servis yoklamayla sürer; `direct`'e asla geçmez.

**`direct` hiçbir zaman sizin yerinize seçilmez.** Yalnızca `direct`'i kendiniz yazdığınızda kullanılır (`--source direct`, `[live] source = "direct"` ya da `SOFASCORE_LIVE__SOURCE=direct`); otomatik bir değer yoktur. Seçmeden önce bilin:

1. SofaScore'un kendi istemci kimlik bilgisini sitenin istemcisi dışında kullanır;
2. kimlik bilgisi ya da sunucu değişince haber vermeden bozulabilir;
3. IP adresinizin engellenmesine yol açabilir;
4. bilerek seçtiğiniz bir kullanım koşulları gri alanıdır.

Aynı dört madde `ssc watch --help`, `ssc describe config`, `ssc config validate` ve `ssc config show` tarafından yazılır ve servis bu kaynakla her başladığında log'a yazılır.

`direct` nasıl davranır:

- **Kimlik bilgisi.** Bir tarayıcı (profil `<profil>-live`) yalnızca kimlik bilgisini okumak için bir spor sayfası açar, bilgiyi o sayfanın kendi bağlantısının `CONNECT` karesinden alır ve kapanır. Kimlik bilgisi yalnızca bellekte tutulur: diske, `state.db`'ye, log'a, bir olaya ya da tanılama paketine hiç yazılmaz; bir log satırında görünürse maskelenir. Sunucu reddederse yeni bir sayfadan yeniden okunur; art arda beş başarısız denemeden sonra kaynak sağlıksız sayılır, servis yoklamayla sürer ve kaynak en çok 30 dakikada bir yeniden dener.
- **Hat üstünde.** Tek bağlantı; yalnızca abone olur (izlenen her spor için `sport.<spor>`), hiç yayın yapmaz ve joker konu kullanmaz; sitenin istemcisi gibi 120 sn'de bir PING gönderir. Sunucu bağlantıyı yaklaşık 30 dakikada bir düşürür; istemci artan aralıklarla yeniden bağlanır ve yeniden abone olur.
- **Proxy.** Bir proxy yapılandırılmışsa (`USE_PROXY` / `PROXY_URL`) `direct` bağlanmaz, çünkü bağlantı proxy'yi atlardı; servis onun yerine yoklar.

Ölçülen ve ölçülmeyen (`docs/push-channel/README.md`, bölüm 7): tek akşam, tek bölge, tek bağlantı, yalnızca `sport.football` konusu, yaklaşık 38 dakika ve bir yeniden bağlanma; istemci 216–226 MB kullandı ve düz bir istemci kabul edildi. Ölçülmeyen: tek bağlantıda birden çok konu, saatlerce süren çalışma, birden çok spor ve kimlik bilgisinin ne sıklıkla değiştiği.

`ssc watch`u bir systemd servisi ya da konteyner olarak çalıştırmak ve her kaynağın istediği bellek: [docs/deploy/watch.md](docs/deploy/watch.md) (İngilizce).

## REST API (özet)

Web uygulaması kök yollarda; JSON API öneki **`/api`**.

- **Ligler**: listele (her ligde `sport`), ekle (isteğe bağlı `sport`), sporu ayarlamak için `PATCH /api/leagues/{id}`, sil, ara (yerel: `GET /api/leagues/search`; uzak: `POST /api/leagues/search-remote?q=…`, her çağrı SofaScore’a istek attığı için `POST`; uzak sonuçlarda `sport`), sezonlar, sezon yenileme, eksik detay listesi.
  - Uzak arama ve sezon yenileme, başarısız olduğunda boş liste döndürmek yerine nedenini söyler. Hata gövdesi `{"detail": {"reason": "...", "message": "..."}}` biçimindedir; `reason` şunlardan biridir: `blocked` (SofaScore 403 yanıtladı), `browser` (challenge istedi ama yerleşik tarayıcı başlatılamadı), `rate_limited` (429/503), `network` (bağlantı yok, zaman aşımı, proxy), `not_found` (sezon yenileme: bu ID’de lig yok) ya da `upstream` (uygulamanın beklemediği bir yanıt). Durum kodu 502’dir; `rate_limited` için 503, `not_found` için 404. 200 ile gelen boş liste, SofaScore’un gerçekten bir şey bulamadığı anlamına gelir. Web uygulaması her nedeni bir sonraki adımla birlikte gösterir.
- **Sporlar**: `GET /api/sports` — desteklenen sporlar ve her biri için istenen maç detay dilimleri (`src/sports.py`’deki kayıt defterinin salt okunur görünümü).
- **Maçlar**: `GET /api/matches` — sayfalı; filtreler `league_id` (tek ID ya da virgülle birden fazla, ör. `17,8`), `season_id`, `date`, `details=present|missing`, `sort=asc|desc`; her satırda `has_details`. Ayrıca tek maç JSON ve tek maç çekme.
- **Scraper**: `POST /api/fetch` (gövde: `full` | `details`, `selections: [{league_id, season_ids, match_ids}]`), `POST /api/scrape/cancel` (sonrasında yeni istek gönderilmez, yeniden deneme beklemeleri kesilir), durum, SSE akışı.
- **Pano / istatistik / ayarlar**: Web panellerine JSON; ayarlar `.env` ile uyumlu.
- **Veri**: yedek zip, kapsam seçerek temizleme, CSV export (`GET /api/export/csv` var olan dışa aktarımı indirir, yoksa `404` döndürür; `POST /api/export/csv` önce onu oluşturur).
- **Erişim**: hiçbir `GET` uç noktası bir şey değiştirmez; başka bir sitenin tetiklediği durum değiştiren istekler `403` ile reddedilir. `SOFASCORE_API_TOKEN` ayarlıysa her `/api` isteği `Authorization: Bearer <belirteç>` ya da web uygulamasının oturum cookie’sini ister (`{"token": "..."}` ile `POST /api/auth/login`, `POST /api/auth/logout`, durum için `GET /api/auth`); aksi halde yanıt `401` ve `{"detail": {"code": "auth_required", "message": "..."}}` olur. Bkz. [Güvenlik modeli](#güvenlik-modeli).
- **İndirme sürerken reddedilenler**: `POST /api/data/clear`, `POST /api/data/backup` (`scope=config` hariç), `DELETE /api/leagues/{id}` ve `data_dir`’i değiştiren `POST /api/settings`, `409` ve `{"detail": {"code": "job_running", "message": "..."}}` döndürür. Bunlardan biri sürerken hem bunlar hem `POST /api/fetch`, `data_operation_running` koduyla `409` döndürür. Başarılı `data_dir` değişikliği `"data_dir_changed": true` içerir; oluşturulamayan klasör `data_dir_unusable` koduyla `400` döndürür.
- **Bypass Durumu**: `GET /api/bypass/status` (`health` ile: `ok` / `degraded` / `blocked`, bkz. [SofaScore bizi engelliyor mu?](#sofascore-bizi-engelliyor-mu-köprü-sağlığı)) ve canlı test `POST /api/bypass/test`: tarayıcı üzerinden tek bir istek; yanıtta `success`, `reason` (başarısızsa, yukarıdaki gibi), `browser_ready`, `has_token` / `is_valid` ve `health` bulunur. Hiçbir şey bunu kendiliğinden çağırmaz; web uygulamasında **Ayarlar → Bağlantı** altındaki **Bağlantıyı sına** düğmesidir.
- **Loglar / tanılama** (salt okunur, bkz. [Loglar ve tanılama](#loglar-ve-tanılama)): `GET /api/logs` (`limit` 1–2000, `level` = en düşük seviye), `GET /api/diagnostics` (özet, JSON), `GET /api/diagnostics/bundle` (zip indirme). Hiçbiri dosya yolu almaz.
- **Sağlık**: `GET /health` (`/api` öneki yok) `status`, `version`, `ui` alanlarının yanında `bridge` (aynı sağlık bloğu) ve `throttle` ([ortak istek bütçesi](#ortak-istek-bütçesi-tüm-süreçler)) döndürür. Erişim belirteci ayarlıysa, belirteci taşımayan çağıran yalnızca `{"status": "ok"}` alır.

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
  - Web uygulaması, **Ayarlar → Bağlantı**: aynı durum ve tek bir isteği kendiniz denemek için **Bağlantıyı sına** (tarayıcı çalışıyor mu, bot koruması geçildi mi, başarısızsa nedeni).
  - Log: istek başına değil, durum değişimi başına bir uyarı.
  - Komut satırı (`ssc` ve kullanımdan kalkan `--headless`, `--watch`, `--refresh-only` bayrakları): durum değişimi başına stderr'de, uygulama dilinde tek satır.
- Durum süreç başınadır: web uygulaması kendi köprüsünü, her CLI süreci kendininkini bildirir.

### Sunucu kurulumu (Linux / Docker)

[Docker imajı](#docker) tarayıcıyı ve sistem kütüphanelerini zaten içerir. Düz bir Linux sunucuda ya da kendi imajınızda:

```bash
pip install -r requirements.txt -c constraints.txt
python -m patchright install chromium --no-shell
# Yalnızca Debian/Ubuntu, bir kez: Chromium'un sistem kütüphaneleri (sudo kullanır)
python -m patchright install-deps chromium
# çıkış kodu 0 = hazır; tarayıcıyı bir kez about:blank ile açar, SofaScore'a istek atmaz
python main.py --doctor --skip frontend
```

## Geliştirme

Web’i otomatik yeniden yükleme ile:

```bash
ssc serve --dev             # ya da: python -m src.cli.main serve --dev
```

Web uygulaması `frontend/` altında bir Vue 3 + TypeScript + Vite projesidir (Pinia, vue-router, vue-i18n, Tailwind). Sunucu derlenmiş dosyaları `frontend/dist/`’ten sunar.

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173, /api isteklerini 127.0.0.1:8000'e yönlendirir
npm run build    # tip kontrolü (vue-tsc) + frontend/dist/ içine üretim derlemesi
```

Yapı: `src/views/` her sayfa bir dosya, `src/components/` ortak parçalar, `src/stores/` (ligler, spor filtresi, çalışan iş), `src/api/client.ts` bütün backend çağrıları, `src/locales/{tr,en}.ts` bütün arayüz metinleri.

Testler ve lint (CI aynısını Linux’ta Python 3.10 ve 3.14 ile çalıştırır; Python 3.14 ile çalışan Windows ve macOS işleri elden geldiğince desteklenir: sonuçlarını bildirir ama bir pull request’i ya da sürümü engellemez):

```bash
pip install -r requirements-dev.txt -c constraints.txt
ruff check .
python -m pytest -q
python -m pytest -q --cov   # kapsam ölçümüyle; pyproject.toml’daki tabanın altında başarısız olur
```

`requirements.txt` izin verilen sürüm aralıklarını listeler; `constraints.txt` her paketi (dolaylı olanlar dahil) birlikte çalıştığı bilinen sürümlere sabitler. CI, kurulum betikleri, başlatıcı ve Docker imajı hep bu sabitlerle kurar, böylece yeni çıkan bir paket sürümü ne CI’ı ne de yeni bir kurulumu habersizce bozabilir; haftalık bir iş akışı bunun yerine izin verilen en yeni sürümleri kurup aynı testleri çalıştırır, Dependabot da sabitler için güncelleme önerir.

`tests/conftest.py`, `DATA_DIR`, `config/` ve `.env`’i küçük sentetik bir veri setiyle geçici bir klasöre yönlendirir; testler verinize ve ayarlarınıza hiç dokunmaz. SofaScore’a istek atan testler `live` olarak işaretlidir ve varsayılan olarak atlanır; çalıştırmak için `python -m pytest -m live`. `browser` işaretli testler gerçek bir Chromium başlatır ve onlar da varsayılan olarak atlanır: `python -m pytest -m "browser and not live"` BrowserBridge’i SofaScore’a hiç istek atmadan yerel bir sahte siteye karşı çalıştırır (CI bunu her push’ta yapar).

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
- **Pull request** — Repo’yu fork’layın, odaklı bir dal kullanın, değişiklikleri küçük ve tek konuda tutun; PR’da *ne* ve *neden* olduğunu açıklayın. Mevcut kod stiline uyun; gereksiz geniş refaktörden kaçının. Kullanıcıya dönük metin değiştiriyorsanız iki dili de güncelleyin: web uygulaması için `frontend/src/locales/tr.ts` ve `en.ts`, komut satırı için `locales/tr.json` ve `locales/en.json`.
- **Dokümantasyon ve çeviri** — Bu README’ler veya yerelleştirme metinleri için iyileştirmeler değerlidir.

Katkı göndererek, katkınızın projenin lisansı altında sunulmasını ve proje sahibinin onu başka koşullarla da (örneğin ticari bir lisansla) sunabilmesini kabul etmiş olursunuz. Issue ve inceleme süreçlerinde saygılı iletişim rica edilir. Fikrin uyarlılığından emin değilseniz önce issue açmak iyi bir başlangıçtır.

## Lisans

[PolyForm Noncommercial 1.0.0](LICENSE). Bu yazılımı **ticari olmayan amaçlarla** kullanabilir, değiştirebilir ve paylaşabilirsiniz: kişisel kullanım, öğrenim, araştırma, hobi projeleri ile hayır kurumları, eğitim kurumları ve kamu kurumlarının kullanımı buna dahildir. **Ticari kullanım**, ayrı bir lisans alınmadan **yasaktır**; bunun için bir issue açabilir ya da [@tunjayoff](https://github.com/tunjayoff) ile iletişime geçebilirsiniz.

Bu değişiklikten önce yayımlanan sürümler MIT lisansı altında kalmaya devam eder.
