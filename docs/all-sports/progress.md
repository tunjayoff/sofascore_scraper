# Talimat 05: ara raporlar

Bilgi amaçlı, onay değil. Bütçe: SofaScore alan adlarına giden XHR/fetch/EventSource istekleri (sitenin
kendi sayfalarının attığı; hepsi ortak kilitten geçerek ≤ 1 istek/sn). Üçüncü taraf istekleri kaydedilir
ama sayılmaz.

## 2026-10-01 12:14 UTC: ilk rapor (506 istek, 14 dk)

Planlanan ~300 istek eşiği geçildi: ilk sayfalar beklenenden pahalıydı (ana sayfa tek başına ~80–95 istek)
ve araç iki kez düzeltildi (aşağıda).

**Bakılan:** futbol ana sayfası (`/tr`, `/tr/football`), Canlı ve Bitti filtreleri, Oranlar bağlantısı;
spor menüsü (`links`).

**Bulunan:**
- **40 SofaScore pattern'i; 35'i repoda yok** (`python scripts/analyze_all_sports.py patterns`).
- **Spor menüsünde 27 spor var.** `/sport/{offset}/event-count` bugün maçı olan 20 sporun günlük toplam/canlı
  sayısını tek yanıtta veriyor (Cache-Control `max-age=10`):
  `research/all_sports/samples/football/sport-id-event-count__1.json`.

**En şaşırtıcı 3 bulgu:**
1. **Push kanalı var.** `wss://ws.sofascore.com:9222/` bir **NATS** sunucusu (`INFO {"version":"2.12.15", "auth_required":true, ...}`).
   Her sayfada açılıyor. `sport.football` konusunda olay nesnesinin **değişen alanları** noktalı yol olarak
   geliyor, ör. `{"status.code":100,"status.type":"finished","homeScore.current":8,"winnerCode":1,
   "changes.changeTimestamp":1790856421,"id":17124861}` (`research/all_sports/ws.jsonl`). Bitiş sinyali
   polling'den önce buradan geliyor olabilir; gecikmesi henüz ölçülmedi.
2. **"Bitti" filtresi farklı bir uç noktaya gidiyor.** `/sport/{sport}/finished-upcoming-tournaments/{date}`; repodaki
   `scheduled-tournaments` ile aynı değil.
3. **Boşta duran sayfa da istek atıyor.** Ana sayfa listedeki maçlar için `/event/{id}`, `/event/{id}/odds/{id}/all`
   ve `/event/{id}/votes` istiyor (önden yükleme). Ayrıca challenge sonrası ülke kodu `TR` → `XX` oluyor
   (`/config/top-unique-tournaments/XX/football`).

**Araç düzeltmeleri:**
- Dinleyici bağlam düzeyine taşındı; isteği yapan çerçeve (`origin`) yazılıyor.
- Challenge, sayfa yerleştikten sonra fark edilip köprünün çözücüsüyle aşılıyor.
- Komutlar arasında sayfanın kendiliğinden attığı istekler gönderilmeden iptal ediliyor; bütçe yalnızca
  yönlendirilen adımlara harcanıyor.
- Çerez katmanı varken tıklama `dispatch_event` ile yapılıyor.
- Pattern'lerde ülke kodu → `{cc}`, spor slug'ı → `{sport}`.

**Sırada:**
- NATS'ın gönderilen karelerini (SUB konuları) kaydetmek; CONNECT'teki kimlik alanları maskelenecek.
- Futbol maç sayfası ve sekmeleri → takım → oyuncu → turnuva/sezon/puan durumu → arama.
- Sonra diğer 26 spor; yeni pattern çıkan dallarda derinleşilecek.

**Bütçe:** 506 / 5.000 istek, 14 dk / 6 sa.

## 2026-10-01 12:44 UTC: rapor 2 (~1.700 istek, 44 dk)

**Bakılan:**
- Futbolda maç sayfası ve sekmeleri (Kadrolar, İstatistikler, H2H), takım, oyuncu, turnuva (Premier League:
  tarihe göre, istatistikler), arama ve canlı maç sayfasında 150 sn bekleme.
- Basketbol: maç sayfası ve sekmeleri (Eleme aşaması dahil).
- Tenis: maç, AI İçgörüleri, ATP sıralaması, oyuncu, turnuva (Kura).
- Motor sporları: kategori, yarış (F1 Azerbaycan GP: Sonuçlar, Yarış Akışı, Puan durumu) ve pilot.
- Bisiklet: yarış (Vuelta: 21 etap sekmesi). MMA: organizasyon, fight night, dövüş ve dövüşçü.
- Kriket: maç sayfası ve sekmeleri. Beyzbol: spor sayfası.

**Bulunan:**
- **166 SofaScore pattern'i; 151'i repoda yok.**
- **Canlı maç sayfası push'a rağmen polling yapıyor.** `/event/{id}/incidents` ~15 sn, `statistics`
  ve `live-match-tracker` ~10–30 sn, `/sport/{offset}/event-count` ~20 sn arayla.
  Yanıtlarda `Cache-Control: max-age=10, s-maxage=172800`.
- **NATS akışı:** `CONNECT` (user/pass; kayıtlarda maskelendi), sayfadaki her maç için `SUB event.{id}`,
  ana sayfada `sport.{sport}` konusu. Site analitik olaylarını da NATS'a gönderiyor (`PUB _EVENTS.<uuid>`);
  bu gövdeler saklanmıyor.
- **Önemli bulgu: çok yarışmacılı ayrı model.** Motor sporları ve bisiklet `event` değil `stage` kullanıyor:
  `/stage/{id}/substages`, `/stage/{id}/standings/competitor|team`, `/stage/{id}/driver-performance`,
  `/unique-stage/{id}/seasons`, `/calendar/{yyyy-mm}/{offset}/{sport}/stages`, `/team/{id}/stage-seasons`.
  Pilot ve bisikletçi `team` varlığı.
- MMA ve kriket `event` modelini kullanıyor; MMA'ya özgü `/unique-tournament/{id}/tournament/{id}/mma-events/all`,
  kriket'e özgü `/event/{id}/innings` var.
- Maçın `customId`'si bir uç noktada id yerine geçiyor: `/event/{customId}/h2h/events`.

**Araç düzeltmeleri:**
- Köprünün açılış sayfaları artık API çağırmayan `robots.txt`'e yönleniyor.
- Bu düzeltmeden önceki 5 yeniden başlatmada köprü ana sayfayı kilit kurulmadan açmıştı; o istekler
  sayılamadı (bitişte "Yapılmayanlar"da yazılacak).
- Bu script'in iptal ettiği istekler bütçeye sayılmıyor.

**Sırada:**
- Beyzbol, Amerikan futbolu, buz hokeyi, voleybol, hentbol, e-spor, dart, snooker, masa tenisi,
  badminton, ragbi, futsal, su topu, Aussie kuralları, plaj voleybolu, mini futbol, florbol, bandy, padel.
- Sonra durum üçlüleri ve `classify_status`/`extract_scores` çalıştırması.

**Bütçe:** ~1.700 / 5.000 istek, 44 dk / 6 sa.

## 2026-10-01 13:01 UTC: rapor 3 (~2.530 istek, 61 dk)

**Bakılan:**
- Spor sayfası (+ gerektiğinde Bitti/Canlı filtresi) ve bir maç sayfası + sekmeleri: beyzbol, buz hokeyi,
  Amerikan futbolu (NFL turnuva ve takım sayfası üzerinden), hentbol, voleybol, e-spor, dart, snooker, masa tenisi.
- Badminton: spor sayfası. Liste 28 sn içinde görünmedi; maç sayfası açılmadı. Liste yanıtlarından 145
  badminton olayı yakalandı.

**Bulunan:**
- Bu sporların maç sayfalarında yeni pattern çok az; çoğu futboldakiyle aynı `event` uç noktalarını kullanıyor.
- Yeni olanlar:
  - beyzbol: `/event/{id}/umpires`, `/event/{id}/weather`, `/event/{id}/comments` (404);
  - e-spor: `/event/{id}/esports-games` (oyun/harita listesi);
  - Amerikan futbolu turnuvası: `/unique-tournament/{id}/season/{id}/draft`.
- Sekmeler spora göre değişiyor, ama arkalarındaki uç noktalar ortak:
  - buz hokeyi: Skor / Cezalar;
  - masa tenisi: oyuncu adlarıyla sekme;
  - e-spor: Games ve oyun numaraları.
- Tarih seçici DOM tıklamasıyla açılmadı (açılır pencere). Tarihsel liste için turnuva ("Tarihe göre")
  ve takım sayfaları kullanıldı.

**Sırada:**
- Ragbi, futsal, su topu, Aussie kuralları, plaj voleybolu, mini futbol, florbol, bandy, padel.
- Sonra analiz: durum üçlüleri, `classify_status`/`extract_scores`, uç nokta envanteri.

**Bütçe:** ~2.530 / 5.000 istek, 61 dk / 6 sa.

## 2026-10-01 13:25 UTC: rapor 4 (~3.450 istek, ~110 dk)

**Bakılan:**
- Ragbi, futsal, su topu, mini futbol, florbol, bandy (kategori → turnuva), padel. Aussie kuralları ve plaj
  voleybolunda bugün maç yok; AFL araması `{"error": ...}` döndü.
- Canlı tenis sayfasında 120 sn bekleme.

**Bulunan:**
- **Stage modelinin statüsünde kod yok:** yalnızca `{type, description}`; sezon stage'inde `type` boş
  (`research/all_sports/samples/motorsport/stage-id-extended__1.json`).
- **Kriket'te yeni `status.type`: `willcontinue`.**
- **Tenis canlı sayfası iki üçüncü taraf kanal açıyor:** Sportradar LMT widget'ı (`lmt.fn.sportradar.com/common`,
  ~4 sn polling) ve `wss://ws.fn.sportradar.com/wss`. URL'de imzalı token var; gizli bilgi taramasında
  maskelenecek.
- **Tenis canlı sayfasında SofaScore polling:** `statistics` ve `point-by-point` ~10–25 sn arayla.
- **NATS konuları:** spor sayfası `sport.{sport}`, maç/liste sayfaları `event.{id}`, ayrıca `odds.{id}`.

**Araç:**
- `listen` adımı eklendi: sayfa açıldıktan sonra boşta beklenir. SofaScore API istekleri gönderilmeden iptal
  edilir; yalnızca sayfanın kendi NATS bağlantısının kareleri kaydedilir. Bütçe harcamadan push gecikmesi
  ölçmek için; kendi bağlantı yok, SUB denemesi yok.

**Sırada:**
- Futbol, tenis ve masa tenisi sayfalarında dinleme.
- Sonra README, envanter, gizli bilgi taraması ve PR.

**Bütçe:** ~3.450 / 5.000 istek, ~110 dk / 6 sa.

## 2026-10-01 14:40 UTC: bitiş (en az 3.349 gönderilmiş istek, keşif 2 sa 2 dk)

**Son adımlar:**
- **Push gecikmesi:** futbol, tenis ve masa tenisi spor sayfalarında 10'ar dk boşta dinlendi (toplam 30 dk). Bu
  sırada API istekleri gönderilmedi.
  - Bitiş karesi 36: medyan 0,5–1,2 sn, maks 1,8 sn. Küçük örneklem.
- **Eksik örnekli sporlar:** takım ve turnuva sayfalarından örnek alındı.
  - Hentbol: 29 bitmiş maç.
  - Aussie kuralları: 31 bitmiş maç (AFL Women).
  - Bandy, su topu ve plaj voleybolunda bitmiş örnek bulunamadı.
- **Gizli bilgi temizliği:**
  - NATS `INFO` karelerindeki istemci IP'si maskelendi.
  - Üçüncü taraf gövdeleri ve NATS analitik gövdeleri silindi.
  - `/token/captcha` gövdesi maskelendi.

**Düzeltme:** önceki raporlarda "27 spor" yazıyordu; spor menüsünde **26 spor** var (futbol + 25). Sayım hatası.

**Sonuç:** `docs/all-sports/README.md`.

**Bütçe:** en az 3.349 gönderilmiş SofaScore isteği (araç sayacı 3.788), 2 sa 2 dk / 6 sa.

## 2026-10-01: PR #17 inceleme düzeltmeleri

- **Veri küçültüldü (`analyze_all_sports.py compact`).** Repodaki veri ~15 MB'dan 3,9 MB'a indi. Ham kaydın
  tamamı yerelde, gitignore'lu `data/research_index/all_sports_2026-10-01/` altında.
- **NATS `INFO` kareleri sadeleştirildi:** yalnızca `version`, `auth_required`, `tls_required` kaldı. Sunucu
  kimliği ve küme adresleri atıldı.
- **`/country/alpha2` örneği çıkarıldı:** istemcinin IP'sini, şehrini ve TLS parmak izini taşıyordu.
- **Dal geçmişi yeniden yazıldı:** büyük dosyalar ve bu örnek hiçbir commit'te kalmadı.
