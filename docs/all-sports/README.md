# SofaScore'un tüm sporları: pasif keşif (Talimat 05)

SofaScore sitesinin kendi sayfaları bir kullanıcı gibi gezildi ve sayfaların attığı tüm XHR/fetch/WebSocket
trafiği kaydedildi. Amaç, projeyi üç spordan (futbol, basketbol, tenis) tüm sporlara genişletmek için gereken
bilgiyi çıkarmak: hangi sporlar var, veri modelleri mevcut modele ne kadar uyuyor, site hangi uç noktaları
kullanıyor, canlı sayfa nasıl tazeleniyor.

- **Tarih:** 01.10.2026, 12:00–14:02 UTC (Perşembe; hafta içi programı).
- **Script'ler:**
  - `scripts/explore_all_sports.py`: gezinti ve kayıt;
  - `scripts/analyze_all_sports.py`: analiz, `all` ve `finalize` komutları;
  - `scripts/_all_sports_patterns.py`: URL → pattern.
- **Repodaki veri:** `research/all_sports/`. Bu bir özet; ham kaydın tamamı yerelde,
  gitignore'lu `data/research_index/all_sports_2026-10-01/` altında (`python scripts/analyze_all_sports.py compact`
  ile küçültüldü).
  - `requests.jsonl`: her SofaScore isteği: zaman, spor, sayfa, sayfa tipi, URL, durum kodu, Cache-Control, örnek
    dosyası. Yanıt anahtarları pattern başına bir satırda.
  - `third_party_hosts.json`: sayılmayan üçüncü taraf istekleri; satır yerine host başına adet ve Sportradar
    widget'ının açıldığı sayfalar.
  - `samples/{spor}/`: yanıt örnekleri.
    - Spora göre değişebilen uç noktalarda (`/event/{id}` ve detayları, liste uç noktaları, `/stage/…`) spor başına
      bir örnek; diğerlerinde tek örnek.
    - 12 KB altıysa tam; değilse listeler ve büyük sözlükler kırpılmış (`"trimmed": true`).
  - `events/{spor}.jsonl`: yanıtlarda görülen olayların kompakt alanları, (spor, id) başına son görülen.
    - Analizde kullanılmayan alanlar atıldı; iç içe skor sözlükleri (`innings`) 2 girdiye kırpıldı.
    - `events/_top_keys.json`: olayın üst düzey anahtar sayımı. `events/_stages.jsonl`: stage nesneleri.
  - `status_examples/`: her durum üçlüsü için bir örnek.
  - `ws.jsonl`: push kanalının kanıt kareleri: durum değişikliği taşıyan tüm `MSG`'ler ve her kare türünden 3 örnek.
    Toplam sayılar `ws_counts.json`'da.
  - `pages.jsonl`: gezilen sayfalar ve tıklamalar.
  - `analyze_all_sports.py all` bu özetten aynı CSV'leri ve `summary.json`'u üretir. İstisnalar:
    - `endpoints.csv`'nin `example` sütunu kalan örneği gösterir;
    - stage üçlülerinde örnek id, en küçük id'li stage'dir.
- **Özet çıktılar:**
  - `docs/all-sports/endpoints.csv`: pattern envanteri;
  - `docs/all-sports/status/{spor}-triples.csv`: durum üçlüleri;
  - `docs/all-sports/summary.json`: kontrollerin makine okunur özeti;
  - `docs/all-sports/progress.md`: ara raporlar.

Yeniden üretmek için: `python scripts/analyze_all_sports.py all` (ağ isteği atmaz).

---

## Özet

1. **Sitenin spor menüsünde 26 spor var** (`scripts/_all_sports_patterns.py` → `SPORT_SLUGS`, ana sayfa
   bağlantılarından).
   - Örneği olan 23'ü normal `event` modelini kullanıyor.
   - **Motor sporları ve bisiklet ayrı bir `stage` modeline** dayanıyor (C sınıfı).
   - Plaj voleybolunda hiç örnek yok.
2. **`classify_status` olay modelindeki 23 sporun 22'sinde UNKNOWN vermiyor.** Sınıflandırma önce `status.type`'a
   baktığı için yeni kodlar (beyzbol 28/29, masa tenisi 11/12, e-spor 1001/1002, kriket 21) `inprogress` tipiyle
   doğru sınıfa düşüyor.
3. **İstisna kriket:** `type: "willcontinue"` (kod 141, "End of day 1") **UNKNOWN**. Stage modelinde de
   `status.code` yok; `finished` stage'ler ve tipi boş sezon stage'leri **UNKNOWN**.
4. **`extract_scores` yalnızca futbol, basketbol ve tenisi destekliyor.** Diğer 20 spor için "desteklenmeyen spor"
   uyarısıyla boş bir `ScoreSheet` dönüyor. Çoğunun skor yapısı ise mevcut sınıflardan biriyle aynı biçimde.
5. **Push kanalı var.** `wss://ws.sofascore.com:9222/`, kimlik doğrulamalı bir **NATS** sunucusu. Maç bitişi
   (`status.type: finished`), SofaScore'un kendi değişiklik anından **medyan 0,5–1,2 sn** sonra geliyor
   (36 kare; küçük örneklem). Polling ile daha önce ölçülen bitiş gecikmesi medyan 19 sn (futbol) ve 48 sn (tenis) idi.
6. **175 SofaScore pattern'i görüldü; 160'ı repoda yok.**

---

## Spor tablosu

Sütunlar:
- **Bugün:** `/sport/{offset}/event-count` yanıtında toplam/canlı maç sayısı
  (`research/all_sports/samples/football/sport-id-event-count__1.json`, 14:00 UTC; boşsa o gün maç yok).
- **Olay:** bu çalışmada yakalanan tekil olay ya da stage sayısı; **bitmiş:** bunların bitmiş olanları.
- **UNKNOWN:** `classify_status` sonucu UNKNOWN olan olay ve stage oranı.
- **Yeni kod:** `src/status.py`'deki kod kümelerinde olmayan, görülen `status.code` değerleri.

Durum üçlüleri ve örnekleri: `docs/all-sports/status/{spor}-triples.csv`. Skor anahtarlarının spor ve turnuva
bazında sıklığı: `docs/all-sports/summary.json` → `score_structure`.

| Spor (slug) | Bugün toplam/canlı | Olay / bitmiş | Etkinlik şekli | Skor yapısı (bitmiş olaylarda) | UNKNOWN | Yeni kod | Sınıf |
|---|---|---|---|---|---|---|---|
| football | 925 / 74 | 480 / 222 | event | `current, display, period1–2, normaltime` (+`extra1–2`, `overtime`, `penalties`) | 0 | — | (destekleniyor) |
| basketball | 114 / 2 | 198 / 141 | event | `period1–4, normaltime, overtime, current` | 0 | — | (destekleniyor) |
| tennis | 436 / 33 | 219 / 98 | event | `period1–5`, `periodNTieBreak`, `point`, `current` | 0 | — | (destekleniyor) |
| american-football | — / — | 113 / 75 | event | `period1–4, normaltime, overtime, current` | 0 | — | **A** |
| aussie-rules | 1 / 0 | 33 / 31 | event | `period1–4, normaltime, current` | 0 | — | **A** |
| ice-hockey | 55 / 3 | 44 / 6 | event | `period1–3, normaltime, overtime, current` | 0 | — | **A** (az örnek) |
| handball | 45 / 2 | 82 / 29 | event | `period1–2, normaltime, current` (+`overtime`, `penalties`, `aggregated`) | 0 | — | **A** |
| rugby | 21 / 0 | 47 / 21 | event | `period1–2, normaltime, current` | 0 | — | **A** |
| futsal | 50 / 1 | 26 / 16 | event | `current, display`; bazı turnuvalarda `period1–2, normaltime` | 0 | — | **A** |
| minifootball | 87 / 0 | 8 / 1 | event | `period1–2, normaltime, current` | 0 | — | **A** (1 bitmiş) |
| floorball | 4 / 0 | 7 / 4 | event | `period1–3, normaltime, current` | 0 | — | **A** (az örnek) |
| volleyball | 21 / 0 | 81 / 19 | event | `current` = set; `period1–5` = setteki sayı | 0 | — | **A** |
| badminton | 112 / 6 | 145 / 75 | event | `current` = oyun; `period1–3` = sayı | 0 | — | **A** |
| table-tennis | 1231 / 16 | 149 / 68 | event | `current` = set; `period1–5` = sayı | 0 | 11, 12 | **A** |
| padel | 46 / 0 | 48 / 14 | event | tenisle aynı: `period1–3`, `periodNTieBreak` | 0 | — | **A** |
| snooker | 8 / 3 | 9 / 4 | event | `current` = frame; `period1` = `current`'a eşit | 0 | — | **A** (az örnek) |
| darts | 24 / 1 | 50 / 9 | event | turnuvaya göre: setler (`period1–3` = leg) ya da yalnız leg | 0 | — | **B** |
| baseball | 12 / 1 | 506 / 482 | event | `innings{inningN{run,hits,errors}}`; bazı liglerde `period1–9` | 0 | 28, 29 | **B** |
| cricket | 12 / 1 | 17 / 5 | event | `innings{inningN{score,wickets,overs,runRate}}` | **5/17** | 21, 141 | **B** |
| esports | 75 / 7 | 7 / 1 | event + oyun alt olayları | maç: `display`; oyun: `period1–2` = yarı | 0 | 1001, 1002 | **B** |
| mma | — / — | 50 / 26 | event | **skor yok**; sonuç `winnerCode`, `winType`, `finalRound` | 0 | — | **B** |
| motorsport | — / — | 42 stage | **stage** | skor yok; sıralama `standings/competitor` | **28/42** | (kod yok) | **C** |
| cycling | — / — | 20 stage | **stage** | skor yok; etaplar `substages` | **16/20** | (kod yok) | **C** |
| bandy | — / — | 1 / 0 | event | bitmiş örnek yok | 0 | — | sınıflanmadı |
| waterpolo | 1 / 0 | 1 / 0 | event | bitmiş örnek yok | 0 | — | sınıflanmadı |
| beach-volley | — / — | 0 | — | örnek yok | — | — | sınıflanmadı |

### Sınıf gerekçeleri

**A: mevcut modelle çalışır (alan eşlemesi).**
- Durum sınıflandırması zaten doğru: UNKNOWN 0 (`summary.json` → `checks`).
- Skor yapısı mevcut üç sınıftan biriyle aynı biçimde:
  - **Dönem tabanlı** (basketbol gibi): Amerikan futbolu, Aussie kuralları, buz hokeyi, hentbol, ragbi, futsal,
    mini futbol, florbol.
  - **Set tabanlı** (tenis gibi; `current` = kazanılan set/oyun, `periodN` = o setteki sayı): voleybol,
    badminton, masa tenisi, padel.
  - **Snooker:** `current` = frame, `period1` her iki örnekte `current`'a eşit
    (`research/all_sports/events/snooker.jsonl`, id 17218595 ve 17220088).
- Örnekler:
  - Amerikan futbolu uzatması `110 AET` + `overtime`:
    `research/all_sports/status_examples/american-football/event_finished-110-aet__*.json`.
  - Hentbolda kod `120 AP` ile `penalties` ve `aggregated` anahtarları görüldü:
    `docs/all-sports/status/handball-triples.csv`, `summary.json` → `score_structure.handball`.
- Gereken iş: spor başına `extract_scores` dalı ya da mevcut sınıfa eşleme. Durum mantığı değişmez.

**B: yeni durum ya da skor mantığı ister.**
- **Kriket:**
  - Yeni durum tipi `willcontinue`; `classify_status` → UNKNOWN
    (`research/all_sports/status_examples/cricket/event_willcontinue-141-end-of-day-1__16586046.json`).
  - Skor `current/display` + `innings.inningN{score, wickets, overs, runRate}`.
  - Çok günlü maçta gün sonu bir ara durum.
  - Ayrı uç nokta: `/event/{id}/innings` (`research/all_sports/samples/cricket/event-id-innings__1.json`).
- **Beyzbol:**
  - Skor `innings.inningN{run, hits, errors}` (çoğunluk).
  - NPB, KBO ve MLB Preseason'da ayrıca `period1–9` + `normaltime` var (aşağıda "Turnuvaya göre farklılaşan yapılar").
  - Canlı kodlar 28/29 ("8th/9th Inning") bilinen kümede yok; tip sayesinde LIVE.
- **Dart:** aynı sporda iki biçim. `current`'ın birimi (set mi leg mi) turnuvaya göre değişiyor; olayın
  `bestOfSets` / `bestOfLegs` alanlarına bakan bir mantık gerekiyor
  (`research/all_sports/samples/darts/event-id__1.json`: `bestOfSets: 5`).
- **E-spor:**
  - Maç düzeyinde kodlar 1001/1002 ("First game"/"Second game").
  - Her oyun (harita) kendi `status`, `winnerCode` ve yarı skorlarına sahip bir alt nesne:
    `/event/{id}/esports-games` (`research/all_sports/samples/esports/event-id-esports-games__1.json`).
  - Bu çalışmanın olay toplayıcısı bu oyun nesnelerini de olay saydı (id 588243 gibi küçük id'ler).
- **MMA:**
  - Bitmiş dövüşte `homeScore`/`awayScore` boş.
  - Sonuç `winnerCode` + `winType` (ör. `"UD"`) + `finalRound`; `time.period1..5` raunt süreleri
    (`research/all_sports/samples/mma/event-id__1.json`).
  - Skor yerine sonuç yöntemi tutulmalı.

**C: ayrı etkinlik modeli ister (çok yarışmacılı).** Motor sporları ve bisiklet; bkz. "Stage modeli". Gerekçe:
- `event` yok; yarış/etap bir `stage`;
- `status`'ta `code` yok;
- skor yok, sonuç bir sıralama listesi;
- yarışmacılar `team` varlığı.

**Sınıflanmadı:** bandy, su topu ve plaj voleybolunda bitmiş maç örneği bulunamadı. Bugün maç yok ya da sezon
dışı; kategori sayfaları turnuva bağlantısı göstermedi (`research/all_sports/pages.jsonl`).

---

## Stage modeli (motor sporları, bisiklet)

Kaynak örnekler:
- `research/all_sports/samples/motorsport/stage-id-extended__1.json`
- `research/all_sports/samples/motorsport/stage-id-substages__1.json`
- `research/all_sports/samples/motorsport/stage-id-standings-competitor__1.json`
- bisiklet için `research/all_sports/samples/cycling/`

**Hiyerarşi:** `uniqueStage` (ör. Formula 1) → sezon stage'i (`type.name: "Season"`) → etkinlik stage'i
(`"Event"`, ör. Azerbaycan GP) → alt stage'ler (`"Practice"`, sıralama, yarış). Bisiklette sezon → yarış → etaplar
(Vuelta'da 21 etap sekmesi).

**Durum:**
- Yalnızca `{"description", "type"}`; **`code` yok**. Örnekler:
  - `{"description": "Not started", "type": "notstarted"}`;
  - sezon stage'inde `{"description": "", "type": ""}`.
- `classify_status`:
  - `notstarted` → NOT_STARTED;
  - `finished` (kodsuz) → **UNKNOWN** ("Finished" açıklaması yedek tablosunda yok);
  - boş tip → **UNKNOWN**.
- Sonuç: motor sporunda 42 stage'den 28'i, bisiklette 20'den 16'sı UNKNOWN
  (`docs/all-sports/status/motorsport-triples.csv`).
- Bisiklette `canceled` stage de görüldü (`research/all_sports/status_examples/cycling/stage_canceled-none-canceled__220862.json`).

**Sonuç verisi:**
- Skor alanı yok. Sıralama `/stage/{id}/standings/competitor` ve `/stage/{id}/standings/team` ile alınıyor.
- Yarışmacı bir `team` nesnesi (pilot da `team`).
- Pilot sayfası: `/team/{id}/stage-seasons`, `/team/{id}/stage-season/{id}/races`.

**Takvim:**
- `/calendar/{yyyy-mm}/{offset}/{sport}/stages` (`research/all_sports/samples/motorsport/calendar-2026-10-id-sport-stages__1.json`).
- `/stage/sport/{sport}/scheduled/{date}`.
- `/unique-stage/{id}/seasons`.

**Projeye etkisi:**
- `src/` yalnızca `event` bekliyor (maç listesi, detay dosyaları, `classify_status`, izleyici).
- Stage için ayrı bir çekici, ayrı bir durum eşlemesi (kodsuz) ve sonuç olarak skor yerine sıralama tutan bir
  depolama gerekir.

---

## Turnuvaya göre farklılaşan yapılar

`summary.json` → `score_structure.{spor}.tournaments_deviating`: bitmiş olaylarda, sporun çoğunluk anahtar
kümesinden farklı anahtar kümesi olan turnuvalar.

| Spor | Çoğunluk | Farklı turnuva → anahtarlar | Örnek id (`research/all_sports/events/{spor}.jsonl`) |
|---|---|---|---|
| baseball | `current, display, innings` | NPB, KBO, MLB Preseason: + `period1–9, normaltime` | 16754637, 16977348, 13599270 |
| darts | `current, display, normaltime` (yalnız leg) | World Grand Prix: + `period1–3` (set içi leg'ler) | 17099316 (WGP), 17180772 (Modus Super Series) |
| futsal | `current, display` | Copa do Brasil Feminino, Paranaense Série Prata: + `period1–2, normaltime`; U17 Paranaense: + `series` | 17226246, 17034059, 17121445 |
| table-tennis | `+ normaltime, period1–4` | Liga Pro, TT Cup: `normaltime` yok | 17214781, 17225601 |
| football | `period1–2, normaltime` | Arjantin amatör bölgesel turnuvalar: yalnız `current, display` | 17210405, 16879280 |

Voleybol ve tenisteki farklar (3/4/5 set, tie-break anahtarları) maçın uzunluğundan kaynaklanıyor; yapısal değil.

---

## "Bitti" filtresi ve tarih listeleri

Spor sayfasındaki **Bitti** filtresi repoda kullanılan `scheduled-tournaments`'ı değil,
**`/sport/{sport}/finished-upcoming-tournaments/{date}`** uç noktasını çağırıyor
(`research/all_sports/samples/football/sport-sport-finished-upcoming-tournaments-date__1.json`):

- **Yanıt bir indeks, olay listesi değil.** `{"finished": {turnuvaId: {saatDilimiOfseti: adet}}, "upcoming": {...}}`.
  Ofset saniye cinsinden; aynı gün için olası her saat dilimine göre o turnuvada kaç maç bittiği/kalacağı.
- Olaylar ardından turnuva başına `/unique-tournament/{id}/scheduled-events/{date}` ile geliyor; bu, repodaki
  araştırma script'lerinde de kullanılan yol.
- Cache-Control `max-age=60, s-maxage=300`. Karşılaştırma için: `scheduled-tournaments` `max-age=10, s-maxage=900`,
  `scheduled-events` `max-age=10, s-maxage=86400` (`docs/all-sports/endpoints.csv`).
- Kategori sayfası (ör. bandy İsveç) ise `/category/{id}/scheduled-events/{date}` kullanıyor.
- Görülen sporlar: Amerikan futbolu, dart, futbol, hentbol, buz hokeyi, voleybol.

**Tarih seçici** DOM tıklamasıyla açılmadı (açılır pencere). Geçmiş tarihli listeler için turnuva sayfasının
"Tarihe göre" sekmesi (`/unique-tournament/{id}/season/{id}/team-events/total`) ve takım sayfasının "Biten" listesi
(`/team/{id}/events/last/{n}`) kullanıldı.

---

## Canlı sinyal

### Push kanalı (NATS)

`wss://ws.sofascore.com:9222/`. Kaynak: `research/all_sports/ws.jsonl`.

- **Bağlantı:** sitenin her sayfası bağlanıyor.
  - Sunucu `INFO` karesinde `"version": "2.12.15"`, `"auth_required": true` diyor. Kayıtta INFO'dan yalnızca
    `version`, `auth_required` ve `tls_required` tutuldu.
  - İstemci `CONNECT` ile `user`/`pass` gönderiyor (`"lang": "nats.ws"`). **Kayıtta maskelendi.**
  - Bu çalışma yalnızca sitenin kendi bağlantısının karelerini kaydetti; kendi bağlantısını açmadı, SUB denemedi.
- **Abonelikler** (`SUB`):

  | Sayfa | Konu |
  |---|---|
  | spor sayfası | `sport.{sport}` (o sporun tüm olay değişiklikleri) |
  | maç, liste, turnuva, takım sayfaları | ekrandaki her maç için `event.{id}` |
  | bahis bileşenleri | `odds.{id}` |

  Toplam 6.981 `event.{id}` aboneliği görüldü (`summary.json` → `push.sub_subjects`).
- **Mesaj biçimi:** olay nesnesinin yalnız değişen alanları, noktalı yol olarak. Örnek:

  ```json
  {"winnerCode":1,"status.code":100,"status.description":"Ended","status.type":"finished",
   "homeScore.current":8,"homeScore.display":8,"awayScore.current":2,"awayScore.display":2,
   "statusDescription":"FT","lastPeriod":null,"changes.changeTimestamp":1790856421,"id":17124861}
  ```

  Bitişte gereken her şey (`status`, skor, `winnerCode`, `changes.changeTimestamp`) tek karede.
- Site analitik olaylarını da bu bağlantıyla yayımlıyor (`PUB _EVENTS.<uuid>`); bu gövdeler saklanmadı.

**Bitiş gecikmesi:** `status.type = finished` taşıyan karede, karenin alındığı an − `changes.changeTimestamp`.

| Konu | Kare | Medyan (sn) | Maks (sn) |
|---|---|---|---|
| `sport.table-tennis` | 15 | 0,5 | 1,8 |
| `sport.football` | 11 | 0,6 | 1,3 |
| `sport.tennis` | 8 | 0,6 | 1,2 |
| `sport.futsal` | 2 | 1,2 | 1,2 |

- **Küçük örneklem:** toplam 36 kare. Ölçüm, spor sayfalarında 10'ar dk (futbol, tenis, masa tenisi; toplam 30 dk)
  boşta dinleyerek ve gezinti sırasında yakalanan karelerle yapıldı.
  - Boşta dinlerken SofaScore API istekleri gönderilmeden iptal edildi; yalnızca push kareleri alındı.
  - Bütün durum değişikliklerinde (bitiş dışı da) medyan 0,5–1,2 sn (`summary.json` → `push.status_lag_s`).
- **Karşılaştırma:** polling ile ölçülen bitiş gecikmesi (`polling_lag`) medyan futbolda 19 sn, teniste 48 sn idi
  (`docs/status-matrix/README.md`, gecikme tablosu). Push bundan **iki mertebe hızlı**.
- `changes.changeTimestamp` saniye çözünürlükte, kare alınma anı milisaniye; 1 sn altı değerler bu yuvarlamayı
  içerir.

### Polling (canlı maç sayfası)

Push açıkken bile canlı maç sayfası polling yapıyor. Ölçüm 3 canlı maç sayfasında (2 futbol, 1 tenis) 120–150 sn
bekleyerek yapıldı (`research/all_sports/requests.jsonl`, `page_type = event-live`). Aynı pattern'in yanıtları
arasındaki medyan aralık (`docs/all-sports/endpoints.csv` → `live_repeat_s`):

| Pattern | Medyan aralık (sn) | Cache-Control |
|---|---|---|
| `/event/{id}/statistics` | 12,0 | `max-age=10, public, s-maxage=172800` |
| `/event/{id}/incidents` | 15,1 | `max-age=10, public, s-maxage=172800` |
| `/event/{id}` | 17,1 | `max-age=10, public, s-maxage=172800` |
| `/event/{id}/point-by-point` (tenis) | 21,0 | `max-age=0, public, s-maxage=86400` |
| `/event/{id}/live-match-tracker` | 21,3 | `max-age=1800, public, s-maxage=1800` |
| `/sport/{offset}/event-count` | 22,0 | `max-age=10, public` |

- `/sport/{sport}/events/live`: `max-age=5, s-maxage=5, stale-while-revalidate=60`.
- Aynı istek çoğu zaman iki kez kaydedildi: sayfa önceki isteği iptal edip (`ERR_ABORTED`) yeniden gönderiyor.
  Aralık hesabı yalnızca 200 yanıtlarıyla yapıldı.

### Üçüncü taraf canlı widget'ı

İki canlı maç sayfası **Sportradar Live Match Tracker** açtı:
- futbol: Japonya–Ekvador, penaltılar sırasında;
- tenis: Menšík–Bublik.

Widget `lmt.fn.sportradar.com` adresini ~4 sn arayla sorguluyor ve `ws.fn.sportradar.com` WebSocket'ini açıyor
(`research/all_sports/third_party_hosts.json` → `hosts`, `sportradar_widget_pages`). URL'lerde imzalı token olduğu
için yalnızca alan adı tutuldu; gövde ve şema saklanmadı.

Diğer canlı futbol sayfası (WSG Tirol–Jahn Regensburg) widget açmadı. Diğer sporlarda canlı maç sayfası açılmadığı
için bu sporların sayfalarının widget'a dayanıp dayanmadığı **bilinmiyor**.

---

## Uç nokta envanteri

`docs/all-sports/endpoints.csv`: 175 SofaScore pattern'i, 160'ı repoda yok. Pattern'de sayılar `{id}`, tarih
`{date}`, yıl-ay `{month}`, spor slug'ı `{sport}`, ülke kodu `{cc}`, maçın customId'si `{customId}`.

**Sütunlar:**
- `sports`: pattern'in görüldüğü sayfaların sporu.
- `page_types`: hangi sayfa ya da sekme tetikledi.
- `in_repo`: `src/` ya da araştırma script'lerinde eşleşen yol.
- `label`, `reason`, `response_keys`, `cache_control`, `live_repeat_s`, `example`.

**Etiketler:**

| Etiket | Pattern | Anlamı |
|---|---|---|
| `historical` | 91 | Geçmiş maç, sezon, takım, oyuncu verisi. |
| `historical+live` | 4 | Maç detayı; canlı sayfada tekrar tekrar çekiliyor (statistics, incidents, point-by-point, live-match-tracker). |
| `live` | 4 | `/sport/{sport}/events/live`, `/sport/{offset}/event-count`, `/sport/{sport}/live-tournaments`, `/sport/{sport}/live-categories`. |
| `settlement+live` | 1 | `/event/{id}`: tek maçın kesin durumu, skor, `winnerCode`. |
| `none` | 75 | Bahis, reklam, medya, sosyal, yapılandırma, AI içgörüleri. |

Etiket yararlılık yargısıdır; gerekçesi `reason` sütununda, dayanağı yanıt anahtarları.

### Öncelikli uç nokta adayları (spor başına en fazla 5)

| Spor grubu | Adaylar | Neden |
|---|---|---|
| Tüm event sporları | `/event/{id}`, `/sport/{sport}/events/live`, `/sport/{sport}/scheduled-tournaments/{date}/page/{n}` + `/unique-tournament/{id}/scheduled-events/{date}`, `/sport/{sport}/finished-upcoming-tournaments/{date}`, `/sport/{offset}/event-count` | Kesin durum; canlı liste; günlük liste; günün bitmiş turnuva indeksi; spor başına canlı sayaç |
| Kriket | `/event/{id}`, `/event/{id}/innings`, `/event/{id}/incidents`, `/event/{id}/lineups`, `/unique-tournament/{id}/scheduled-events/{date}` | Innings ayrıntısı skorun parçası |
| Beyzbol | `/event/{id}`, `/event/{id}/incidents`, `/event/{id}/statistics`, `/event/{id}/lineups`, `/unique-tournament/{id}/scheduled-events/{date}` | Skor `innings` içinde; inning ayrıntısı incidents'ta |
| E-spor | `/event/{id}`, `/event/{id}/esports-games`, `/event/{id}/statistics`, `/unique-tournament/{id}/scheduled-events/{date}`, `/sport/{sport}/events/live` | Oyun/harita sonuçları ayrı uç noktada |
| MMA | `/event/{id}`, `/unique-tournament/{id}/tournament/{id}/mma-events/all`, `/unique-tournament/{id}/scheduled-mma-main-events/{month}`, `/event/{id}/statistics`, `/team/{id}/career-statistics` | Sonuç (`winType`, `finalRound`) olayda; organizasyon başına etkinlik kartları |
| Motor sporları / bisiklet | `/stage/{id}/extended`, `/stage/{id}/substages`, `/stage/{id}/standings/competitor`, `/calendar/{month}/{offset}/{sport}/stages`, `/unique-stage/{id}/seasons` | Stage hiyerarşisi, sonuç sıralaması, takvim |
| Tenis, dart (point-by-point olanlar) | `/event/{id}`, `/event/{id}/point-by-point`, `/event/{id}/statistics`, `/team/{id}/rankings`, `/rankings/{id}` | Sayı sayı akış; sıralama |

## Maç detay dilimleri, spor başına

PR #121'den beri her spor, SofaScore'un kendi maç sayfasının o sporda istediği detay dilimlerini ister. Önceden
her spor aynı altı ortak dilimi zorunlu olarak alıyordu. İki spora özgü dilim eklendi: kriket `innings` ve dart
`point_by_point`. Futbol, basketbol ve tenis değişmedi.

- **Kaynak:** bu keşfin kaydı (`research/all_sports/`), yeni istek atılmadı. `tests/sport_evidence.py`
  tabloyu `requests.jsonl` ve `events/*.jsonl`'dan türetir: yalnızca maç sayfalarının istekleri, bahis hariç,
  maçın durumu HTTP koduyla birlikte. Sonuç `tests/fixtures/sport_slices/evidence.json`; bir test dosyanın
  araştırma verisinden yeniden türetilebildiğini denetler.
- **Kayıt defteri:** `src/sports.py`, `DETAIL_SLICES` ve üstündeki not. Bir dilim `not_in` sporlarında hiç
  istenmez; `optional_in` sporlarında istenir ama tamlık hesabına girmez (`SliceSpec.counts_in(spor)`).
  Kayıtlı olmayan ya da bilinmeyen bir spor eskisi gibi altı ortak dilimi alır.

**Kanıt zayıf.** Çoğu sporda tek bir maç sayfası var, yaklaşık 20 sn açık kaldı; bazı sporlarda hiç yok.
Keşif script'i adım bittikten sonra gelen istekleri atıyor; bunlar sayfanın açılıştaki ilk istekleri değil,
geç gelenler (polling, tembel yükleme). Bir sayfa `/event/{id}` ve `/event/{id}/pregame-form` istemiş ve
yanıt almışsa (200 ya da 404) **tam** sayılır; kayıttaki her tam sayfa pregame-form istemiş. Bitmiş maçta H2H
ve team-streaks bir sekmenin arkasında; sekmelere yalnızca futbol, basketbol, tenis ve kriket'te tıklandı. Bu
yüzden bir sayfanın bunları hiç istememesi dilimin olmadığına kanıt sayılmaz.

**Kurallar** (`verdict()`, spor ve dilim çifti başına):

| Kanıt | Sonuç |
|---|---|
| bitmiş maçta 200, bitmiş maçta 404 yok | istenir, tamlığa girer (**R**) |
| başlamış (canlı ya da bitmiş) maçta 404, temiz bitmiş veri yok | istenir, tamlığa girmez (*o*) |
| statistics, lineups ya da incidents tam bir canlı ya da bitmiş sayfada hiç istenmemiş | istenmez (✗) |
| yalnızca başlamamış maç kanıtı, sayfa yok, 403 ya da yarım sayfa | değişmez |

**Tablo.** Hücrede F, L ve N bitmiş, canlı ve başlamamış maçtır, ardından HTTP kodu gelir; `—` sayfanın uç
noktayı hiç istemediğini gösterir. Her hücrenin sonunda kayıt defterinin şimdi ne yaptığı yazar. "(yarım)":
sayfa `/event/{id}`'yi hiç istememiş.

| Spor | Maç sayfaları (olay, durum) | statistics | team-streaks | pregame-form | h2h | lineups | incidents | spora özgü |
|---|---|---|---|---|---|---|---|---|
| football | 17118211 F, 17167343 F, 17212341 L | F200 L200 **R** | F200 L200 **R** | F404 L404 **R**¹ | F200 L200 **R** | F200 L404 **R** | F200 L200 **R** | — |
| basketball | 17186711 F | F200 **R** | F200 **R** | F404 **R**¹ | F200 **R** | F200 **R** | F200 **R** | — |
| tennis | 17204708 F, 17206248 L | F200 L200 **R** | F200 **R** | F404 L404 **R**¹ | F200 **R** | — **R**¹ | — **R**¹ | point-by-point F200 L200 *o*¹ |
| american-football | 16183693 F, her istek 403 | F403 **R** | — **R** | F403 **R** | — **R** | F403 **R** | F403 **R** | — |
| aussie-rules | yok | — **R** | — **R** | — **R** | — **R** | — **R** | — **R** | — |
| ice-hockey | 16532341 F | F200 **R** | — **R** | F404 *o* | — **R** | F200 **R** | F200 **R** | — |
| handball | 17217388 N | — **R** | — **R** | N404 **R** | — **R** | N200 **R** | N404 **R** | — |
| rugby | 16237238 F (yarım) | — **R** | — **R** | — **R** | — **R** | F200 **R** | — **R** | — |
| futsal | 17226246 F | F404 *o* | — **R** | F404 *o* | — **R** | F404 *o* | F200 **R** | — |
| minifootball | 17218801 F (yarım) | F200 **R** | — **R** | — **R** | — **R** | F404 *o* | — **R** | — |
| floorball | 16956586 N | — **R** | — **R** | N404 **R** | — **R** | — **R** | N404 **R** | — |
| volleyball | 16450376 N | — **R** | — **R** | N404 **R** | — **R** | N404 **R** | N404 **R** | — |
| badminton | yok | — **R** | — **R** | — **R** | — **R** | — **R** | — **R** | — |
| table-tennis | 17220134 F (yarım) | — **R** | — **R** | — **R** | — **R** | — **R** | — **R** | — |
| padel | 17213167 F | F404 *o* | — **R** | F404 *o* | — **R** | — ✗ | — ✗ | — |
| snooker | 17220573 L | L404 *o* | — **R** | L404 *o* | — **R** | — ✗ | — ✗ | — |
| baseball | 17206542 N | — **R** | N200 **R** | N404 **R** | N200 **R** | N404 **R** | — **R** | at-bats N200 (öneri) |
| cricket | 16526539 F | F404 *o* | — **R** | F404 *o* | — **R** | F200 **R** | F200 **R** | innings F200 **R** (yeni) |
| esports | 17223320 L | L404 *o* | — **R** | L404 *o* | L200 **R** | L200 **R** | — ✗ | esports-games L200 *o* (SP-3) |
| darts | 17099318 F | F200 **R** | — **R** | F404 *o* | — **R** | — ✗ | — ✗ | point-by-point F200 **R** (yeni) |
| mma | 16822910 F | F200 **R** | F200 **R** | F200 **R** | — **R** | — ✗ | — ✗ | — |

¹ Kanıt öteki yönü gösteriyor; davranış golden'lar sabitlediği için korundu (aşağıda, öneriler).

**Bitmiş maç başına istek, önce → sonra.** İlk çekim 1 `/event` artı dilim başına bir istek. Önceden 404 veren
zorunlu bir dilim bir tamamlama turu daha açıyordu (1 + hâlâ `ok` olmayan her dilim). Sayım tablodaki yanıtları
varsayar: sitenin hiç istemediği dilim 404, kanıtı olmayan dilim veri döner sayılır.

| Spor | Önce → sonra | Değişiklik |
|---|---|---|
| cricket | 10 → 8 | statistics ve pregame_form isteğe bağlı; `innings` eklendi (zorunlu, yalnızca canlı ve bitmiş maçta) |
| darts | 11 → 6 | lineups ve incidents istenmiyor; pregame_form isteğe bağlı; `point_by_point` eklendi (zorunlu) |
| mma | 10 → 5 | lineups ve incidents istenmiyor |
| padel | 12 → 5 | lineups ve incidents istenmiyor; statistics ve pregame_form isteğe bağlı |
| snooker | 12 → 5 | lineups ve incidents istenmiyor; statistics ve pregame_form isteğe bağlı |
| esports | 12 → 7 | incidents istenmiyor; statistics ve pregame_form isteğe bağlı |
| futsal | 11 → 7 | statistics, lineups ve pregame_form isteğe bağlı |
| ice-hockey | 9 → 7 | pregame_form isteğe bağlı |
| minifootball | 9 → 7 | lineups isteğe bağlı |
| diğer 12 kayıtlı spor | değişmedi | — |

**Öneriler, canlı doğrulamayı bekliyor.** Futbol, basketbol ve tenis golden'larla sabit; bunlar ayrı bir karar
ister ve projenin sonundaki canlı doğrulama koşusundan (Talimat 07) sonra karara bağlanır:
- `pregame_form` üçünde de isteğe bağlı olsun: kayıttaki her bitmiş futbol, basketbol ve tenis maçında, canlı
  futbol ve tenis maçında da 404 verdi.
- Tenis `lineups` ve `incidents` istenmesin: iki tam tenis sayfası da (bir bitmiş, bir canlı) bunları istemedi.
- Tenis `point_by_point` zorunlu olsun: bitmiş maçta da canlı maçta da veriyle döndü.

`tests/test_sport_slices.py` içindeki `PROPOSALS` bu yargıları sabitler; kanıt değişirse test kırılır.

**Kanıtı yetmeyen spora özgü uç noktalar:** beyzbol `/at-bats` (yalnızca bir başlamamış maçta 200), beyzbol
`/umpires`, `/weather`, `/comments` (yalnızca 404), tenis `/tennis-power` (canlı sayfada istendi ama script
isteği attı, yanıt yok). **Hiç kanıt yok:** american-football (her istek 403), aussie-rules, badminton,
table-tennis, rugby ve minifootball (yarım sayfalar) ve yalnızca başlamamış maçı olan sporlar. Canlı doğrulama
koşusu spor başına bir bitmiş ve bir canlı maç sayfası açıp hangi detay uç noktalarının istendiğini ve
durumlarını kaydedecek, `tests/fixtures/sport_slices/evidence.json`'u yeniden üretecek; kayıt defteri o zaman
spor başına tek sayfadan fazlasına dayanır.

---

## Sürprizler

1. **Push kanalı:** bitiş, SofaScore'un değişiklik anından ~1 sn sonra geliyor; tek karede skor ve `winnerCode`.
2. **"Bitti" filtresi olay değil indeks döndürüyor** (saat dilimi ofsetine göre adet).
3. **Boşta duran sayfa da istek atıyor:** ana sayfa listedeki maçlar için önden `/event/{id}`, odds ve votes
   çağırıyor (`research/all_sports/requests.jsonl`, `page_type = home`).
4. **customId ile uç nokta:** `/event/{customId}/h2h/events`; sayısal id yerine maçın `customId`'si
   (`research/all_sports/samples/football/event-rbbsfic-h2h-events__1.json`).
5. **Challenge sonrası ülke kodu `XX`:** `/config/top-unique-tournaments/XX/football`
   (`research/all_sports/requests.jsonl`).
6. **Futbolda `code 100` ama `display` ≠ `normaltime`:** `extract_scores` uyarısı (`summary.json` → `checks.football.extract_warnings`).
7. **Beyzbolda `/event/{id}/comments` 404 döndü;** gövde yok, anlamı belirsiz.

## Açık sorular

- Buz hokeyinde penaltı atışıyla biten maç görülmedi (6 bitmiş örnek). `penalties` anahtarı gelir mi?
- Masa tenisinde 7 setlik maçlarda `period6–7` gelir mi? Görülen en fazla `period5`.
- Kriket `willcontinue` dışında çok günlü maçlarda başka ara durumlar (ör. "Stumps", "Lunch") var mı?
- Push kanalının kimlik bilgileri (`CONNECT` user/pass) sabit mi, oturuma mı bağlı? Bu çalışma kimlik bilgisini
  kullanmadı ve incelemedi.
- `sport.{sport}` konusu o sporun **bütün** olay değişikliklerini mi taşıyor, yoksa yalnız ekrandaki
  turnuvalarınkini mi? 10 dk'da futbolda 60, tenis 402, masa tenisi 546 mesaj (`research/all_sports/pages.jsonl`, `op = listen`).
- Sportradar widget'ı hangi sporların ve turnuvaların canlı sayfasında açılıyor? Yalnız 3 canlı sayfa açıldı.
- Snooker `period1`'in anlamı (iki örnekte `current`'a eşit) belirsiz.
- `/token/captcha` ne zaman çağrılıyor? Challenge sonrası görüldü; gövde maskelendi.

---

## Yapılmayanlar ve sınırlar

- **Bandy, su topu, plaj voleybolu:** bitmiş maç örneği yok (sezon dışı ya da bugün maç yok); sınıflanmadı.
  Aussie kurallarında AFL araması `{"error": ...}` döndü; örnekler AFL Women takım sayfasından.
- **Badminton, Aussie kuralları, bandy, su topu, plaj voleybolu:** maç sayfası açılmadı (liste sayfada görünmedi ya da
  maç yoktu). Durum üçlüleri liste yanıtlarından.
- **Tarih seçici:** DOM tıklamasıyla açılmadı; geçmiş tarihler turnuva ve takım sayfalarıyla tarandı.
- **Kapsam:** her spor aynı derinlikte incelenmedi. Futbol tam akışla (spor sayfası, canlı/bitti filtreleri, maç ve
  sekmeleri, takım, oyuncu, turnuva sekmeleri, arama) gezildi. Diğer sporlarda spor sayfası + bir maç sayfası ve
  sekmeleri; yeni pattern çıkan dallarda (motor sporları, bisiklet, MMA, kriket, tenis sıralaması, NFL turnuvası)
  derinleşildi. Maç sayfaları 18–22 sn bekleme içinde futboldakinden farklı pattern üretmediğinde geçildi
  (`research/all_sports/pages.jsonl` → `new_patterns`).
- **Sayfa dili:** site profil nedeniyle `/tr` dilinde açıldı (`/en/` → `/tr`). API yanıtları dilden bağımsız;
  sekme adları Türkçe okundu.
- **Bütçe ve süre:**
  - Kayıtta en az **3.349 gönderilmiş SofaScore isteği** var (yanıt alan + sayfanın kendisinin iptal ettiği).
    Aracın sayacı 3.788 (üst sınır; yeniden başlatmalarda kayıtlardan yeniden sayıldı).
  - Süre: 2 sa 2 dk kayıt, 6 sa sınırın içinde.
  - Üçüncü taraf istekleri (4.469 satır; `third_party_hosts.json`) sayılmadı.
  - **Sayılamayan istekler:** aracın ilk 5 yeniden başlatmasında köprü ana sayfayı (`/tr`) istek kilidi kurulmadan
    açtı. O isteklerin sayısı bilinmiyor; düzeltmeden sonra açılış sayfası API çağırmayan `robots.txt`.
- **Hız:**
  - Bu araçtan geçen her SofaScore isteği ortak kilitle ≤ 1 istek/sn.
  - Sayfa gezintileri ≥ 5 sn arayla.
  - Komutlar arasında sayfanın kendiliğinden attığı istekler gönderilmeden iptal edildi.
- **Gizli bilgi:**
  - NATS `CONNECT` kimlik alanları, `INFO` karelerindeki istemci IP'si, `/token/captcha` gövdeleri ve imzalı
    üçüncü taraf URL'leri maskelendi.
  - Üçüncü taraf yanıt gövdeleri ve NATS analitik gövdeleri silindi (`scripts/analyze_all_sports.py finalize`).
  - `/country/alpha2` yanıtı istemcinin IP'sini, şehrini ve TLS parmak izini taşıdığı için örneği repoya alınmadı
    (`compact`).
- **Kullanıcı içeriği:** yorum, oy ve profil sayfalarına gidilmedi. `/event/{id}/votes` yanıtları (toplu oy
  sayıları) bahis/etkileşim olarak `none` etiketli; ilk örnekleri dışında saklanmadı.
