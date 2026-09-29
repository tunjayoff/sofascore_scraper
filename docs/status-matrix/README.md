# SofaScore `status` taksonomisi ve bitiş gecikmesi

Futbol, basketbol ve tenis için SofaScore `status` nesnesinin (`type`, `code`, `description`)
canlı API'den toplanmış örneklerle belgelenmesi. Her satırın arkasında `data/status_samples/`
altında ham bir API yanıtı (`event` + `fetched_at_utc` + `source_endpoint`) vardır; örneği
olmayan durum "bulunamadı" olarak, arama yöntemiyle birlikte yazılmıştır.

Veri toplama: 29.09.2026, 13:15–16:00 UTC. Tarama penceresi: 15.09.2026–06.10.2026.

Script'ler (yeniden çalıştırılabilir): `scripts/discover_status_taxonomy.py` (A–C),
`scripts/measure_finish_lag.py` (D), ortak parçalar `scripts/_research_common.py`.
Talimat 01 için kırpılmış kopyalar: `tests/fixtures/status/{sport}/`
(yalnızca `status`, `winnerCode`, `aggregatedWinnerCode`, `homeScore`, `awayScore`,
`startTimestamp`, `time`, `changes`).

> **Açık iş:** Basketbol bitiş gecikmesi ölçümü (Euroleague + Eurocup, 13 maç) bu belge yazılırken
> sürüyordu; sonuçlar ayrı commit'le eklenecek.

---

## Yöntem ve talimattan sapmalar

| Konu | Talimat | Yapılan | Neden |
|---|---|---|---|
| Tarih listesi | `/sport/{sport}/scheduled-events/{gün}` | `/sport/{sport}/scheduled-tournaments/{gün}/page/{n}` + `/unique-tournament/{ut}/scheduled-events/{gün}` | Eski yol üç sporda da 404 ([bulgu 1](#önbellektutarlılık-bulguları)). Sitenin kendi tarih sayfası bu iki yolu çağırıyor (headless tarayıcıda sayfanın ağ istekleri izlenerek görüldü). |
| HTTP istemcisi | `make_api_request` | Repodaki `BrowserBridge` sayfasından `fetch()` | `make_api_request` yanıt başlıklarını döndürmüyor. 29.09.2026'da her curl isteği 403 challenge aldı; `make_api_request` da isteği bu köprüye devrediyor. Aynı tarayıcı/TLS/challenge çözümü; yeni istemci yok. `cache: "no-store"`: tarayıcının yerel önbelleği değil sunucunun yanıtı ölçüldü. |
| Hız | ≤ 1 istek/sn | Süreçler arası paylaşılan kilit dosyasıyla toplamda ≤ 1 istek/sn | Aynı anda birden çok süreç çalıştı. |
| Devre kesici | Mevcut mantık açık kalsın | Araştırma istemcisinde: 10 ardışık hata → 5 dk bekleme, 3 beklemeden sonra dur | Repodaki devre kesici yalnızca maç detayı toplu çekiminde; senkron yolda yok. Çalışma boyunca tetiklenmedi. |
| Tarama genişliği | Her gün tüm maçlar | Her spor/gün için kategori önceliğine göre ilk 25 turnuvanın maçları | Futbolda günde 741–2.043 turnuva; tamamı saatler sürerdi. Taranan: futbol 2.406, basketbol 2.542, tenis 2.212 farklı maç. |
| Canlı anlık görüntü | 3 farklı bölge saati | 13:43, 15:02, 15:34/15:51 UTC | Süre kısıtı; Asya sabahı / Amerika gecesi görüntüsü yok. |
| D dosya adları | `data/finish_lag/{date}.jsonl` | `{date}_{sport}.jsonl`, `{date}_{sport}_summary.json` | Üç spor aynı anda yazıyor. |
| D maç seçimi | Maç günü, 10–20 maç, 6 saat | Hafta içi; futbol ve tenis için **bitmeye yakın canlı maçlar** (futbol 2. yarı 32–43. dk, tenis son set), basketbol için akşam maçları | Süre kısıtı. `last_inprogress_seen` ve bitiş gözlendi; maçların başı gözlenmedi. |
| D bitiş sonrası izleme | 6 saate kadar | Futbol son bitişten ~2 dk, tenis ~50 dk sonra durduruldu; özet `--summarize` ile kayıtlardan üretildi | Süre kısıtı. `score_changed_after_finished` bu pencereyle sınırlı. |
| "Gecikme ölçümü" bölümü | Proje README'si | Bu belge | Talimat mevcut dosyaların değişmemesini de istiyor. |
| `data/` dosyaları | PR'a dahil | `git add -f` | `data/` `.gitignore`'da; `.gitignore` değiştirilmedi. `data/status_samples/_index/` (8,5 MB tarama indeksi) PR'a eklenmedi; `scan` ile yeniden üretilebilir. |

---

## Durum evreni

Kaynak: `docs/status-matrix/{sport}-triples.csv` (sayılar: taranan maçların son görülen durumu).
Yorum sütunu: `not_started | live | finished | terminal_not_finished | unknown`.

### Futbol

| type | code | description | adet | yorum | örnek |
|---|---|---|---|---|---|
| notstarted | 0 | Not started | 1765 | not_started | `football/A_notstarted-0-not-started__17184998.json` |
| inprogress | 6 | 1st half | 39 | live | `football/A_inprogress-6-1st-half__17018554.json` |
| inprogress | 31 | Halftime | 17 | live | `football/A_inprogress-31-halftime__17018588.json` |
| inprogress | 7 | 2nd half | 50 | live | `football/A_inprogress-7-2nd-half__17018572.json` |
| inprogress | 20 | Started | 92 | live | `football/A_inprogress-20-started__16982821.json` |
| finished | 100 | Ended | 2208 | finished | `football/A_finished-100-ended__17099711.json` |
| finished | 110 | AET | 4 | finished | `football/A_finished-110-aet__17148332.json` |
| finished | 120 | AP | 59 | finished | `football/A_finished-120-ap__17090707.json` |
| postponed | 60 | Postponed | 52 | terminal_not_finished | `football/A_postponed-60-postponed__16539815.json` |
| canceled | 70 | Canceled | 38 | terminal_not_finished | `football/A_canceled-70-canceled__16741834.json` |
| interrupted | 80 | Interrupted | 2 | terminal_not_finished | `football/A_interrupted-80-interrupted__17148292.json` |

### Basketbol

| type | code | description | adet | yorum | örnek |
|---|---|---|---|---|---|
| notstarted | 0 | Not started | 1678 | not_started | `basketball/A_notstarted-0-not-started__17079409.json` |
| inprogress | 13 | 1st quarter | 13 | live | `basketball/A_inprogress-13-1st-quarter__17203938.json` |
| inprogress | 14 | 2nd quarter | 4 | live | `basketball/A_inprogress-14-2nd-quarter__17062025.json` |
| inprogress | 15 | 3rd quarter | 1 | live | `basketball/A_inprogress-15-3rd-quarter__17157547.json` |
| inprogress | 16 | 4th quarter | 1 | live | `basketball/A_inprogress-16-4th-quarter__17203938.json` |
| inprogress | 30 | Pause | 3 | live | `basketball/A_inprogress-30-pause__17157547.json` |
| finished | 100 | Ended | 2627 | finished | `basketball/A_finished-100-ended__17092269.json` |
| finished | 110 | AET | 62 | finished | `basketball/A_finished-110-aet__16346148.json` |
| finished | 91 | Walkover | 3 | finished | `basketball/A_finished-91-walkover__17102381.json` |
| postponed | 60 | Postponed | 56 | terminal_not_finished | `basketball/A_postponed-60-postponed__16623552.json` |
| canceled | 70 | Canceled | 33 | terminal_not_finished | `basketball/A_canceled-70-canceled__17110363.json` |
| canceled | 90 | Abandoned | 4 | terminal_not_finished | `basketball/A_canceled-90-abandoned__17100305.json` |
| interrupted | 80 | Interrupted | 1 | terminal_not_finished | `basketball/A_interrupted-80-interrupted__17114923.json` |

### Tenis

| type | code | description | adet | yorum | örnek |
|---|---|---|---|---|---|
| notstarted | 0 | Not started | 507 | not_started | `tennis/A_notstarted-0-not-started__17204702.json` |
| inprogress | 8 | 1st set | 46 | live | `tennis/A_inprogress-8-1st-set__17196662.json` |
| inprogress | 9 | 2nd set | 72 | live | `tennis/A_inprogress-9-2nd-set__17208186.json` |
| inprogress | 10 | 3rd set | 18 | live | `tennis/A_inprogress-10-3rd-set__17202152.json` |
| inprogress | 20 | Started | 2 | live | `tennis/A_inprogress-20-started__17209431.json` |
| finished | 100 | Ended | 3092 | finished | `tennis/A_finished-100-ended__17204710.json` |
| finished | 92 | Retired | 94 | finished | `tennis/A_finished-92-retired__17083330.json` |
| finished | 91 | Walkover | 40 | finished | `tennis/A_finished-91-walkover__17058663.json` |
| canceled | 70 | Canceled | 97 | terminal_not_finished | `tennis/A_canceled-70-canceled__17084262.json` |
| interrupted | 80 | Interrupted | 5 | terminal_not_finished | `tennis/A_interrupted-80-interrupted__17201545.json` |
| suspended | 81 | Suspended | 1 | terminal_not_finished | `tennis/A_suspended-81-suspended__17208583.json` |

Görülen kodlar ve sporları (bir kod birden çok sporda görüldüyse description'ı aynıydı):

| code | description | futbol | basketbol | tenis |
|---|---|---|---|---|
| 0 | Not started | ✓ | ✓ | ✓ |
| 6 / 7 | 1st half / 2nd half | ✓ | | |
| 8 / 9 / 10 | 1st / 2nd / 3rd set | | | ✓ |
| 13 / 14 / 15 / 16 | 1st–4th quarter | | ✓ | |
| 20 | Started | ✓ | | ✓ |
| 30 | Pause | | ✓ | |
| 31 | Halftime | ✓ | | |
| 60 | Postponed | ✓ | ✓ | |
| 70 | Canceled | ✓ | ✓ | ✓ |
| 80 | Interrupted | ✓ | ✓ | ✓ |
| 81 | Suspended | | | ✓ |
| 90 | Abandoned | | ✓ | |
| 91 | Walkover | | ✓ | ✓ |
| 92 | Retired | | | ✓ |
| 100 | Ended | ✓ | ✓ | ✓ |
| 110 | AET | ✓ | ✓ | |
| 120 | AP | ✓ | | |

---

## Edge case tablosu

Dosya yolları `data/status_samples/` altındadır. Kaynak: `data/status_samples/_cases_{sport}.json`.
**Canlı durumlar:** örnek toplama adımı canlı maçları sonradan çektiği için `B3_*`/`B4_*`/`T5_*`
dosyalarının bazıları maç bitmiş haliyle kaydedildi; canlı durumun kanıtı anlık görüntü sırasında
kaydedilen `A_inprogress-*` dosyalarıdır ve tabloda onlara referans verilir.

### Ortak (B)

| # | Durum | Futbol | Basketbol | Tenis |
|---|---|---|---|---|
| B1 | Gelecek maç (7+ gün) | `football/B1_future_7d__13500881.json`: `notstarted/0`, `homeScore={}`, `changes.changeTimestamp=0` | `basketball/B1_future_7d__16561604.json`: aynı | **Bulunamadı.** Yöntem: tarama 7 gün ileri; tenis günleri 03.10–06.10 için `scheduled-tournaments` 200 döndü ama boş (0 turnuva) |
| B2 | Bugün, başlamamış | `football/B2_today_not_started__17204606.json`: `notstarted/0` (B1'den farkı yok) | `basketball/B2_today_not_started__16670564.json`: aynı | `tennis/B2_today_not_started__17211226.json`: aynı |
| B3 | Canlı, ilk periyot | `football/A_inprogress-6-1st-half__17018554.json` | `basketball/A_inprogress-13-1st-quarter__17203938.json` | `tennis/A_inprogress-8-1st-set__17194803.json` |
| B4 | Canlı, ara | `football/A_inprogress-31-halftime__17018588.json` (`31 Halftime`) | `basketball/A_inprogress-30-pause__17157547.json` (`30 Pause`) | **Bulunamadı** (setler arası ayrı bir durum görülmedi). Yöntem: 3 canlı anlık görüntü |
| B5 | Canlı, son periyot | `football/A_inprogress-7-2nd-half__17018572.json` | `basketball/A_inprogress-16-4th-quarter__17203938.json` | `tennis/A_inprogress-10-3rd-set__17202152.json` |
| B6 | Normal bitmiş | `football/B6_finished_regular__16837335.json`: `finished/100`, `winnerCode=1` | `basketball/B6_finished_regular__16484334.json` | `tennis/B6_finished_regular__17204710.json` |
| B7 | Ertelenmiş | `football/B7_postponed__16539815.json`: `postponed/60`, skor `{}`, `time={}` | `basketball/B7_postponed__16623552.json` | **Bulunamadı** (2.212 tenis maçında `postponed` yok) |
| B8 | İptal | `football/B8_canceled__16425949.json`: `canceled/70`, skor `{}` | `basketball/B8_canceled__17060394.json` (**`canceled/90 Abandoned`**, skor dolu) | `tennis/B8_canceled__17081854.json`: `canceled/70`, skor `{}` |
| B9 | Interrupted | `football/B9_interrupted__17148292.json`: `interrupted/80`, skor `0-0` | `basketball/B9_interrupted__17114923.json` | `tennis/B9_interrupted__17201545.json`: set skorları duruyor |
| B9 | Suspended | **Bulunamadı** | **Bulunamadı** | `tennis/B9_suspended__17208583.json`: `suspended/81` |
| B9 | Abandoned | **Bulunamadı** | `basketball/B9_abandoned__17100305.json`: **type `canceled`**, code 90, skor dolu (59-57, 4. çeyrek 0-0) | **Bulunamadı** |
| B10 | Ertelenip yeniden programlanmış (aynı id?) | **Bulunamadı.** Yöntem: taramada `postponed/canceled` görülen 30 maç 16:0x UTC'de yeniden çekildi; 30/30 aynı durum, aynı `startTimestamp` (`_recheck_football.json`) | **Bulunamadı**, aynı yöntem, 30/30 değişmedi | **Bulunamadı**, aynı yöntem, 30/30 değişmedi. Ancak bkz. B13/T6: askıya alınan maç aynı id ile ertesi güne taşındı |
| B11 | Hükmen | **Bulunamadı** (`walkover/awarded/forfeit` içeren durum yok) | `basketball/B11_walkover_awarded__17102381.json`: `finished/91 Walkover`, `winnerCode=1`, skor `{}` | `tennis/B11_walkover_awarded__17058663.json`: `finished/91`, `winnerCode=1`, skor `{}` |
| B12 | Listelenip sonra 404 | **Bulunamadı.** Yöntem: B10'daki 30 maç; 30/30 HTTP 200 | **Bulunamadı**, 30/30 HTTP 200 | **Bulunamadı**, 30/30 HTTP 200 |
| B13 | Başlangıç saati değişmiş | `football/B13_start_time_changed__16741904.json`: 1791108000 → 1791118800 (+3 sa), hâlâ `notstarted`; `…__16867839.json`: gözlenen 1790688600 ve 1790688840 (+240 sn), oynanmış (`finished/100`) | `basketball/B13_start_time_changed__16460700.json` ve `…__16460608.json`: ~+47 sa, `notstarted` | `tennis/B13_start_time_changed__17208583.json`: askıya alınmış maç 1790683200 → 1790784000 (ertesi gün, **aynı id**); `…__17196038.json`: +300 sn, oynanıyor |

### Futbol (F)

| # | Durum | Sonuç |
|---|---|---|
| F1 | AET | `football/F1_aet__17148332.json`: `finished/110`; `normaltime=1-1`, `extra1=1-0`, `extra2=0-0`, `overtime=1-0`, `current=display=2-1` |
| F2 | AP | `football/F2_penalties__16950622.json`: `finished/120`; `normaltime=display=3-3`, `penalties=7-6`, **`current=10-9` (penaltılar dahil)**, `winnerCode=1` |
| F3 | Canlı uzatma | **Bulunamadı** (3 canlı anlık görüntüde uzatma yok) |
| F4 | Canlı penaltılar | **Bulunamadı** |
| F5 | Uzatma öncesi ara | **Bulunamadı** |
| F6 | Kupa: 90 dk berabere, toplamla bitmiş | `football/F6_cup_draw_aggregate__16872361.json`: `finished/100 Ended`, `current=1-1`, **`winnerCode=3`**, `aggregatedWinnerCode=2`, `aggregated=1-2` |
| F7 | "Will continue" | **Bulunamadı** |
| F8 | Maç içi duraklama | **Bulunamadı** (canlı maçlarda `Pause` görülmedi) |
| F9 | Bitiş sonrası değişiklik | `football/F9_changed_after_finish__16837335.json`: `changes.changeTimestamp` başlangıçtan +22,6 sa; `changes.changes=["providerLock.status","providerLock.statusSource"]`, skor değil. 430 maçta `changeTimestamp > start + 4 sa` |

### Basketbol (K)

| # | Durum | Sonuç |
|---|---|---|
| K1 | Uzatmalı bitmiş | `basketball/K1_overtime_finished__16346148.json`: **`finished/110 AET`** (basketbolda da "AET"); `normaltime=92-92`, `overtime=8-9`, `current=100-101`, `period1..4` dolu |
| K2 | Canlı uzatma | **Bulunamadı** |
| K3 | İki yarı formatı | `basketball/K3_two_halves__16694075.json`: skorlar **`period2` ve `period4`** alanlarında (38+43=81), `period1/period3` yok. Dört çeyrek: `basketball/K3_four_quarters__16484334.json` |
| K4 | Devre arası vs çeyrek arası | `basketball/A_inprogress-30-pause__17157547.json`: `inprogress/30 Pause`, skorda yalnızca `period1`/`period2` → 2. çeyrekten sonraki ara (devre arası) da `Pause`. `31 Halftime` basketbolda görülmedi; 1.–2. veya 3.–4. çeyrek arası örneği **bulunamadı** (3 canlı anlık görüntü) |
| K5 | Hükmen | `basketball/K5_forfeit__17102381.json` = B11: `finished/91 Walkover`, skor `{}` (20-0 gibi bir skor yok) |

### Tenis (T)

| # | Durum | Sonuç |
|---|---|---|
| T1 | Normal bitmiş | `tennis/T1_finished__17204710.json`: `current=display=normaltime=2-0` (set sayısı), `period1..2` oyun sayıları, `point` son sayı |
| T2 | Retired | `tennis/T2_retired__17081861.json`: **`finished/92 Retired`**, `winnerCode=1`; `current=1-1`, yarım kalan set `period3=1-0`, `normaltime` alanı **yok** |
| T3 | Walkover | `tennis/T3_walkover__17058663.json`: `finished/91 Walkover`, `winnerCode=1`, skor `{}` |
| T4 | Defaulted | **Bulunamadı** (`default` içeren durum yok) |
| T5 | Canlı tie-break | Canlı liste yanıtında `period{n}TieBreak` alanı görüldü (14 maç, tarama indeksi); ham `/event` örneği maç bittikten sonra alındı: `tennis/T5_live_tiebreak__17201991.json` (`period3TieBreak=9-6`) |
| T6 | Askıya alınmış / ertesi güne kalmış | `tennis/B9_suspended__17208583.json` (`suspended/81`, çiftler) — aynı id ile `startTimestamp` ertesi güne taşındı (B13). `tennis/T6_suspended__17201545.json`: `interrupted/80` |
| T7 | Çiftler | `tennis/T7_doubles__17207542.json`: `homeTeam.subTeams` dolu; status davranışı teklerle aynı |
| T8 | Maç tie-break (10 puan) | `tennis/T8_match_tiebreak__17078471.json`: 3. set `period3=10-4` olarak (oyun değil puan), `current=2-1` |
| T9 | Challenger / ITF | `tennis/T9_challenger_itf__17208186.json`. Taranan Challenger/ITF maçlarında görülen ama ana turda (ATP, WTA, WTA 125, UTR) görülmeyen üçlüler: `inprogress/20 Started`, `interrupted/80`, `suspended/81`. Ana turda görülen her üçlü Challenger/ITF'te de görüldü |
| T10 | Bye | **Bulunamadı.** Yöntem: 2.212 tenis maçında takım adında "bye" arandı; yok |

---

## Alan güvenilirliği

Değerler yukarıdaki örnek dosyalardan.

| Alan | Gözlem |
|---|---|
| `status.type` | `finished` hükmen sonuçları da kapsıyor: `91 Walkover` (basketbol, tenis), `92 Retired` (tenis). `Abandoned` ise **`canceled`** altında (basketbol, code 90). Tek başına `type == "finished"` "maç oynandı" demek değil. |
| `status.code` | Üç sporda tutarlı (bkz. durum evreni). Bitişte gözlenen kodlar: 100, 110, 120, 91, 92. D ölçümünde 22/22 maç `finished`'a code 100 ile geçti. |
| `status.description` | Kodla birebir; basketbol uzatma da `AET`. |
| `winnerCode` | Bitmiş maçlarda 1/2; beraberlikte 3 (F6). Walkover/Retired'da dolu. `postponed/canceled/interrupted`'da yok. |
| `aggregatedWinnerCode` | Yalnızca iki ayaklı kupa eşleşmesinde (F6); `winnerCode=3` iken turu geçen tarafı gösteriyor. |
| `homeScore.current` | Futbol AP'de **penaltılar dahil** (10-9); tenis'te set sayısı. |
| `homeScore.display` | Futbol AP'de penaltısız (3-3). |
| `homeScore.normaltime` | Futbol: 90 dk skoru (AET/AP dahil). Basketbol: uzatmasız skor. Tenis: set sayısı; **Retired'da yok**. |
| `extra1/extra2/overtime/penalties` | Futbol AET/AP'de dolu; basketbol uzatmada `overtime`. |
| `period1..N` | Futbol yarılar, basketbol çeyrekler (iki yarı formatında `period2`/`period4`), tenis setlerde oyun; maç tie-break'te puan. |
| `period{n}TieBreak` | Tenis tie-break puanları. |
| `aggregated` | Kupa toplam skoru (F6). |
| `time` | `currentPeriodStartTimestamp`, `lastPeriodEndTimestamp`, futbolda `injuryTime1/2`, basketbolda `played/clockRunning`; ertelenmiş/iptal maçlarda `{}`. |
| `changes.changeTimestamp` / `changes.changes` | Son değişikliğin zamanı ve değişen alanların listesi (ör. `status.code`, `homeScore.normaltime`, `providerLock.status`). Gelecek maçta `changeTimestamp=0`. Bitişten saatler sonra değişen alanlar gözlenen örnekte `providerLock.*` (skor değil). Bitiş sonrası skor düzeltmesi tespiti için kullanılabilir, ancak skor düzeltmesi içeren bir örnek **bulunamadı**. |
| `startTimestamp` | Değişebiliyor (B13: +240 sn ile +47 sa arası); askıya alınan tenis maçında aynı id ile ertesi güne taşındı. |

---

## Önbellek/tutarlılık bulguları

1. **`/sport/{sport}/scheduled-events/{gün}` artık yok.** 29.09.2026'da üç sporda 404
   `{"error": {"code": 404, "message": "Not Found"}}`, `Cache-Control: public, max-age=3600`; ETag üçünde
   aynı (`W/"80440c4b02"`). `.../inverse`, `.../page/1`, `/sport/{sport}/events/scheduled/{gün}` de 404.
2. **`Cache-Control` değerleri** (D ölçümündeki 4.386 gözlem + tutarlılık karşılaştırması):

   | Uç nokta | Cache-Control |
   |---|---|
   | `/sport/{sport}/events/live` | `max-age=5, public, s-maxage=5, stale-while-revalidate=60` |
   | `/event/{id}` | `max-age=10, public, s-maxage=172800` |
   | `/unique-tournament/{ut}/scheduled-events/{gün}` | `max-age=10, public, s-maxage=86400` |
   | `/sport/{sport}/scheduled-tournaments/{gün}/page/{n}` | `max-age=10, public, s-maxage=900` |
   | scraper'ın liste yolu (`.../events/round/{r}` veya `.../events/last/0`) | iki değer: `max-age=60, public, s-maxage=86400` ve `max-age=60, public, s-maxage=60` |
   | 404 | `public, max-age=3600` |

3. **`Age` başlığı hiç gelmedi** (4.386 gözlemin hiçbirinde). ETag ve Date var.
4. **Aynı dakikada maç sayfası ve turnuva listesi** (`data/status_samples/_consistency/2026-09-29T134841+0000.json`):
   21 canlı maçta (10 futbol, 10 tenis, 1 basketbol) status üçlüsü 21/21 aynı. Skor 1 maçta farklı:
   futbol 16461725, `/event` 1-8, liste 1-9 (istekler arası ~1 sn).
5. **Token olmadan her istek 403**, önbellekli olması beklenen `/unique-tournament/17/seasons` dahil.

---

## Gecikme ölçümü

Ham gözlemler: `data/finish_lag/2026-09-29_{sport}.jsonl`; özetler: `…_summary.json`.
Birim saniye. `lag_*` tanımları talimattaki gibi.

**Çözünürlük sınırı:** Bu iki çalıştırmada her gözleme turun başlangıç zamanı yazıldı ve dört kaynak
aynı turda sırayla (önce maç sayfaları, ~15–30 sn sonra listeler) sorgulandı. Bu yüzden:
`lag_event`/`lag_live` tur aralığına eşit çıkıyor (gerçek gecikme 0 ile bu değer arasında) ve liste
kaynakları maç sayfasından **bir tur önce** "finished" görünebiliyor (negatif değerler). Script artık
her yanıtın geldiği anı (`fetched_at_utc`) kaydediyor; basketbol ölçümü bununla yapılıyor.

| | Futbol (11 maç) | Tenis (12 maç) |
|---|---|---|
| Tam yaşam döngüsü (inprogress → finished) | 11 | 11 (1 maç durdurulduğunda 2. setteydi) |
| Gerçek tur aralığı | 60 sn | 93–130 sn (3 süreç hız bütçesini paylaştı) |
| `lag_event` medyan / maks | 60 / 81 | 93 / 130 |
| `lag_live` medyan / maks | 60 / 81 | 94 / 186 |
| `lag_season_list` medyan / maks | 0 / 60 (n=9) | −93 / 60 |
| `lag_scheduled` medyan / maks | −60 / 0 | −93 / 0 |
| `score_changed_after_finished` | 0/11 | 0/11 |
| `status_code_at_finish` | 100 ×11 | 100 ×11 |
| `Age` başlığı | hiç | hiç |

Futbolda 2 maç (17206702, 17206703) için `lag_season_list` yok: maç bilgisinde sezon olmadığı için
scraper'ın liste yolu kurulamıyor; bu maçlar scraper'ın listelerinde de görünmez.

**Basketbol:** ölçüm sürüyor (açık iş).

---

## Sürprizler ve açık sorular

- `inprogress/20 Started`: periyot bilgisi olmayan canlı durum; canlı futbol maçlarının 16/44 (13:43 UTC)
  ve 30/56'sında (15:02 UTC), tenis'te yalnızca Challenger/ITF maçlarında. Hangi kapsam seviyesinde
  görüldüğü açık soru.
- Basketbolda `Abandoned` `canceled` türünde (code 90) ve yarım kalan skor dolu.
- Basketbol uzatması `AET` (code 110) olarak geçiyor.
- Futbol AP'de `current` penaltıları içeriyor, `display` içermiyor.
- Futbol 16867839'un `startTimestamp`'i gözlemler arasında 1790688600 ve 1790688840 olarak görüldü; hangi
  kaynağın hangisini verdiği ayrıştırılmadı.
- Scraper'ın liste yolu için iki farklı `s-maxage` (86400 ve 60) görüldü; gözlemlerde yol kaydedilmediği
  için hangisinin hangi yola ait olduğu bu veriden ayrılamıyor. `s-maxage=86400` görülmesine rağmen
  liste, maç sayfasından geride kalmadı (medyan `lag_season_list` 0 / −93).
- Tenis programı ~2 gün ileriyi gösteriyor (03.10 sonrası boş).
- 16 günlük pencerede ertelenmiş/iptal 90 maçın hiçbiri yeniden programlanmadı ya da 404 olmadı.
- Tenis bye'ları event olarak hiç listelenmiyor olabilir.
- Repodaki `BrowserBridge.fetch_json` tarayıcının varsayılan önbellek modunu kullanıyor; `max-age=10`/`60`
  olan uç noktalarda scraper'ın gördüğü yanıt sunucununkinden bu kadar eski olabilir (bu ölçümde `no-store`).

---

## İstek sayısı ve süre

| Kalem | İstek | Süre | Kaynak |
|---|---|---|---|
| Keşif (scan, live-snapshot, headers, samples) | 2.296 | 3.653 sn | `data/status_samples/_requests.jsonl` |
| B10/B12 yeniden kontrol + B13 örnekleri | 96 | — | `data/status_samples/_recheck_{sport}.json` |
| D futbol | 626 (son tur sayacı) | 13:42–14:14 UTC | `…_football.jsonl` |
| D tenis | 2.203 (son tur sayacı) | 13:42–15:02 UTC | `…_tennis.jsonl` |
| **Toplam (kayıtlı)** | **5.221** | | |

Ek olarak kayda geçmeyen istekler: uç nokta keşfi, script kabul testleri ve iptal edilen ilk ölçüm
denemeleri (tahmin yazılmadı). Hız sınırı: tüm süreçlerde toplam ≤ 1 istek/sn. Devre kesici tetiklenmedi.
