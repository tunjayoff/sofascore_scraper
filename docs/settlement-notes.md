# Sonuçlandırma notları

Sonuçlandırma **bu repoda yapılmaz**; ayrı bir serviste yapılacak. Bu belge o servisin kullanacağı kuralları
araştırma verisine bağlar. Sınıflar ve skor alanları: `src/status.py` (`classify_status`, `extract_scores`).
Kaynak: `docs/status-matrix/README.md` (aşağıda "README") ve `research/finish_lag/retro_2026-09-29_{sport}.jsonl`.

"Üst lig" README'deki tanımdır: futbol ve basketbolda SofaScore'un oyuncu istatistiği yayınladığı turnuvalar
(`hasEventPlayerStatistics`, turnuva ya da maç seviyesinde); teniste kategori adı (ATP, WTA, Grand Slam, WTA 125).
Bu SofaScore'un kapsam seviyesidir, ligin gerçek seviyesi değil: basketbolda "üst" tarafta U17/U19 ligleri var.

## Kurallar

| Durum | Kural | Dayanak (README) |
|---|---|---|
| `LIVE` | İzle: `events/live` + bitişe yakın `event/{id}` | "Önbellek/tutarlılık bulguları": `events/live` `max-age=5, s-maxage=5`; "Basketbol düdük → finished": `lag_whistle` medyan 20 sn, maks 302 sn (n = 9) |
| `COMPLETED` ilk görüldüğünde | **Geçici sonuç.** Skor anlık görüntüsü (`extract_scores`) ve `observation.json` (`observed_at_utc`, `change_ts`) saklanır | "Geriye dönük: bitiş sonrası güncellemeler": `finished` görüldükten sonra skor değişebiliyor |
| Kesinleşme kontrolü | `event/{id}` yeniden okunur, **skor alanları saklananla karşılaştırılır**. Aynıysa kesin; farklıysa yeniden sonuçlandır ve uyar. `changes`'a güvenilmez | Aynı bölüm: `changes` yalnızca son güncellemeyi gösterir; sonraki `time.*` / `providerLock.*` güncellemesi skor değişikliğini gizler (tenis 333/400, futbol 133/400) |
| Kesinleşme gecikmesi | Üst lig futbol/tenis/basketbol **T+2 sa**; alt lig futbol/tenis **T+6 sa**; alt lig basketbol **T+72 sa ya da elle** (T = bitiş) | Aşağıdaki tablo |
| `DECIDED_WITHOUT_PLAY` | Otomatik sonuçlanmaz; piyasa kuralına göre (retired/walkover kuralı bahisçiye göre değişir) | "Durum evreni": `finished/91 Walkover`, `finished/92 Retired`; T2: Retired'da `normaltime` yok, `winnerCode` var |
| `VOID` | Sonuçlanmaz, inceleme kuyruğu; `startTimestamp` yeniden okunur | B9: basketbol Abandoned `canceled/90`, skor 59-57 dolu; T6/B13: askıya alınan tenis maçı aynı id ile ertesi güne taşındı |
| `UNKNOWN` | Durdur, uyar | `classify_status` bilinmeyen `type`/`code`'u loglar |

## Kesinleşme gecikmesinin dayanağı

Retro ölçümü (29.09.2026, her sporda en yeni 400 bitmiş maç, 25–29.09 başlangıçlı) son güncellemenin zamanını
**maç başlangıcından** ölçer (`change_ts − startTimestamp`); bitiş anı, son güncellemesi skor olan maçlarda
kaydedilmez. Bu yüzden bitişe göre pencere, aşağıdaki iki ölçümün yan yana okunmasıdır; tek tek maçlar için
"bitişten sonra" süre ölçülmedi.

| Spor, seviye | Maç süresi: son güncellemesi `finished` geçişi olan maçlarda başlangıçtan geçiş (sa) | Nihai skor değişikliği, başlangıçtan (sa) | Yalnız periyot skoru değişikliği, başlangıçtan (sa) | Kural |
|---|---|---|---|---|
| Futbol, üst | n 124; medyan 1,91, maks 2,68 | yok (0/160) | yok | T+2 |
| Tenis, üst | n 28; medyan 1,69 | yok (0/189) | n 5; maks 2,69 (Beijing 17201571) | T+2 |
| Basketbol, üst | n 138; medyan 1,92, maks 3,99 | n 2; 2,95 (Germany BBL 16738305), 2,99 (Pro B 16476744) | n 2; 2,29 ve **16,38** (U19 Eccellenza 17101022) | T+2 |
| Futbol, alt | n 118; medyan 1,94 | n 3; maks 5,37 (Club Friendly Games 17184515) | n 3; maks 3,23 | T+6 |
| Tenis, alt | n 32; medyan 1,21 | yok (0/211) | n 2; maks 2,04 | T+6 |
| Basketbol, alt | n 177; medyan **5,57**, maks 68,64 | n 25; medyan 43,82, maks 66,40 (FIBA 3x3 Challenger Mataró 17213573) | n 53; medyan 8,55, maks 24,50 | T+72 ya da elle |

Okurken:
- **Üst lig basketbol T+2 ile çelişen bir gözlem var:** U19 Eccellenza 17101022'de yalnız periyot skorları
  başlangıçtan 16,38 sa sonra değişti. Nihai skor değişmedi; maç sonucu piyasası için T+2 yetiyor, periyot
  piyasaları için bu örnekte yetmiyor. Maç kapsam bayrağına göre "üst", gerçekte gençlik ligi.
- Üst ligde gözlenen nihai skor değişikliği 2 maç (basketbol): başlangıçtan 2,95 ve 2,99 sa sonra; üst lig
  basketbol maçları başlangıçtan medyan 1,92 sa sonra bitiyor.
- Alt lig basketbolda `finished` geçişinin kendisi geç geliyor (başlangıçtan medyan 5,57 sa); T+72 bitişe göre
  sayılırsa gözlenen en geç nihai skor değişikliği (başlangıçtan 66,40 sa) içinde kalıyor.
- Üst ligdeki düzeltme örneklemi küçük (futbol 0, tenis 5, basketbol 4 maç) ve 3–4 günlük; pencereler bu
  örneklemin gördüğünü kapsar, daha uzun bir gözlemle yeniden bakılmalı.
- Sayılar alt sınırdır: son güncelleme skor dışıysa daha önceki skor değişikliği görünmez (README, "Okuma notları").
