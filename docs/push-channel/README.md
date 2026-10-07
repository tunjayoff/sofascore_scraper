# SofaScore push kanalı (NATS): dayanıklılık, kapsam ve maliyet

SofaScore sitesi her sayfada SofaScore'un push sunucusuna bir **NATS**-over-WebSocket bağlantısı
açıyor ve canlı maç değişikliklerini oradan alıyor (bkz. `docs/all-sports/README.md`, "Canlı sinyal"). Bu belge,
canlı izlemeyi üretimde bu push kanalıyla yapmanın (yoklama yedekte) uygulanabilirliğini ölçer.

> **Bugünkü davranış.** Bu belge 2026-10-01'deki bir ölçümün kaydıdır. Uygulama bu ölçümden sonra `ssc watch`'a
> üç canlı kaynak ekledi: `page` (varsayılan; sayfanın kendi push bağlantısını dinler), `poll` (yalnız yoklama)
> ve `direct` (yalnızca açıkça seçilince; riskleri `ssc watch --help`'te yazar). Yoklama her kaynağın yedeğidir.
> Kullanım: [README](../../README.md#live-watching), "Live watching"; servis olarak çalıştırma:
> [docs/deploy/watch.md](../deploy/watch.md). Web arayüzünde canlı görünüm yoktur.

- **Tarih:** 2026-10-01 akşamı, Avrupa kupa maçları penceresi (~19:25–22:00 +03).
- **Script'ler (ağ keşfi; `src/` değişmez):**
  - `scripts/push_channel_run.py` — spor sayfalarını ayrı sekmelerde açar, her sekmenin **kendi** push
    bağlantısının karelerini kaydeder; yoklama tabanı (`/sport/{sport}/events/live`) ile karşılaştırır.
  - `scripts/push_light_probe.py` — tek, engellenmiş (reklam/analitik/görsel kapalı) sekmenin üretim maliyeti.
  - `scripts/push_direct_probe.py` — tarayıcı sekmesi olmadan düz bir Python istemcisiyle (aiohttp) doğrudan
    bağlantı deneyi (Talimat 07).
  - `scripts/analyze_push_run.py` — analiz (ağ isteği yok).
- **Ham kayıt** yerelde, gitignore'lu `data/research_index/push_run_2026-10-01/` (ve `_light_`, `_direct_`)
  altında. Repoya yalnızca özet + durum kareleri girer.
- **Sınırlar:** yalnızca sayfanın kendi bağlantısının kareleri dinlendi; ana koşuda ve hafif ölçümde kendi NATS
  bağlantısı açılmadı, SUB denenmedi. Doğrudan bağlantı deneyi (aşağıda) **Tuncay tarafından ayrıca ve
  doğrudan onaylandı** ve yalnızca onun verdiği çerçevede yapıldı. Tüm SofaScore istekleri ortak kilitle
  ≤ 1 istek/sn.
- **Gizli bilgi:** push `CONNECT` kimlik bilgisi (user/pass) yalnızca bellekte, rastgele tuzlu HMAC ile
  karşılaştırıldı; hiçbir yere yazılmadı, kayıtlarda yalnızca "kimlik grubu" numarası var. `INFO` karelerinden
  yalnızca `version`/`auth_required`/`tls_required` tutuldu (sunucu kimliği, küme IP'leri, istemci IP'si atıldı).

---

## Özet

1. **Kapsam neredeyse tam.** 99 dakikalık ana koşuda, yoklamanın gördüğü durum geçişlerinin push'ta bulunma
   oranı (bağlantının açık olduğu pencerelerde): futbol **%100** (234/234), tenis **%100** (92/92), basketbol
   **%99,2** (128/129; tek kaçan bir U14 turnuvası). Push ayrıca yoklamanın hiç listelemediği biten maçları da
   taşıdı (futbol 74, basketbol 25).
2. **Gecikme polling'in iki mertebe altında.** Push'ta maç bitişi, SofaScore'un kendi değişiklik anından
   **medyan 0,6–1,0 sn** sonra geliyor; yoklamada aynı bitiş **medyan 32–52 sn** sonra görünüyor.
3. **Bağlantı ~30 dakikada bir kopup kendiliğinden yeniden kuruluyor;** sitenin kodu her seferinde yeniden abone
   oluyor. Kopma pencerelerinde yoklamanın gördüğü 36 geçişin 22'si push'ta yoktu (üst sınır; aşağıda).
4. **Kimlik bilgisi sabit:** 15 bağlantının (yeniden yükleme, yeni sekme, yeni tarayıcı bağlamı dahil) hepsi
   tek grupta.
5. **Üretim maliyeti:** tek engellenmiş sekme ~1,8–2,6 GB; tarayıcısız düz istemci **~0,2 GB** aynı kapsamla.
6. **Doğrudan bağlantı deneyi (Talimat 07):** düz Python istemcisi kabul edildi, sayfanın `sport.football`
   akışını **birebir** aldı (ortak pencerede 225/225 kare, fark 0), bir kopmayı atlatıp sürdürdü, ~0,2 GB.

---

## 1. Kapsam (push vs yoklama)

Kaynak: `scripts/analyze_push_run.py data/research_index/push_run_2026-10-01`. Yoklama tabanı spor başına
dakikada bir `/sport/{sport}/events/live`. "Geçiş" = bir olayın kodunun değişmesi ya da listeye girmesi/düşmesi.
Push'ta karşılığı = aynı olay için ±120 sn penceresinde (kod değişiminde yeni koda eşit) bir durum karesi.

| Spor | Geçiş (bağlantı açıkken) | Push'ta bulundu | Oran | Kaçan |
|---|---|---|---|---|
| Futbol | 234 | 234 | %100 | — |
| Tenis | 92 | 92 | %100 | — |
| Basketbol | 129 | 128 | %99,2 | 1 (EYBL U14, listeye giriş) |

**Push yoklamadan daha eksiksiz:** push'ta görülüp canlı listede hiç görünmeyen olay id'leri — futbol 74,
basketbol 25, tenis 6. Türlerine bakıldığında çoğu **biten** maç (futbol 68, basketbol 25 finished): pollar
arası bitip `/events/live`'dan düşen maçlar; push bitişi yakalıyor, dakikalık yoklama o anlık görüntüde
göremiyor.

---

## 2. Gecikme

Push gecikmesi = `finished` durum karesinin alındığı an − `changes.changeTimestamp` (SofaScore'un kendi
değişiklik anı). Yoklama gecikmesi = aynı bitişi `/events/live`'dan düşerek görme anı − `changeTimestamp`.

| Spor | Push medyan / p90 / maks (n) | Yoklama medyan / maks |
|---|---|---|
| Futbol | 0,8 / 1,3 / 1,7 sn (135) | 36,9 / 78,9 sn |
| Tenis | 0,6 / 1,1 / 2,8 sn (28) | 31,5 / 63,3 sn |
| Basketbol | 1,0 / 1,2 / 1,6 sn (47) | 51,7 / 74,8 sn |

Yoklama gecikmesi 1 dakikalık aralıkla sınırlı (daha sık yoklama hız bütçesini yer). Push, bitişin skoru ve
`winnerCode`'u tek karede taşıyor.

---

## 3. Dayanıklılık

Kaynak: `ws_events.jsonl`, `pingpong.jsonl`.

- **Yeniden bağlanma:** üç sekme de ~30 dakikada bir koptu ve sitenin kodu kendiliğinden yeniden bağlanıp
  `sport.{sport}` + ekrandaki maçların `event.{id}` aboneliklerini yeniden kurdu (99 dk'da spor başına 3–4 döngü).
- **PING/PONG:** istemci 120 sn'de bir PING gönderiyor, sunucu PONG dönüyor; sunucu PING'i nadir.
- **Kopma pencerelerinde kayıp (üst sınır):** yoklamanın gördüğü 36 geçiş, bir sekmenin push bağlantısının
  kapalı göründüğü pencereye denk geldi; bunların **14'ü** yeniden bağlanmadan sonra push'ta yine göründü,
  **22'si** pencerede hiç görünmedi.
  - Bu bir **üst sınır**: Playwright `close` olayını güvenilir yollamadığı için "kapalı pencere" ataması kısı
    kısmen yaklaşık; bu geçişlerin bir kısmı gerçek kayıp değil, sekme yakalamasının durmasından kaynaklı
    artefakt olabilir.
  - Bağlantı saniyeler içinde geri geliyor, ama push yükü **tam durum taşımıyor**: bir kare, olayın yalnızca
    değişen alanlarını noktalı yol olarak ve yeni mutlak değerleriyle taşıyor (`research/all_sports/ws.jsonl`,
    bakılan 121 MSG karesi; örn. `{"cardsCode":"01","changes.changeTimestamp":...,"id":...}`). Kopma sırasında
    kaçan bir geçiş bu yüzden o olayın bir sonraki karesiyle düzelmiyor; onu yalnızca bir yoklama düzeltiyor.
    Üretimde **yoklama yedeği şart** — kopma pencereleri için.

---

## 4. Kimlik bilgisi sabit mi

`CONNECT` user/pass değerleri yalnızca bellekte, rastgele tuzlu HMAC ile karşılaştırıldı (değerler yazılmadı).
15 bağlantının tamamı — aynı sayfada yeniden yükleme, yeni sekme, **yeni tarayıcı bağlamı** (ayrı profil) ve
test başı/sonu dahil — **tek kimlik grubu**. Yani kimlik bilgisi oturuma/sekmeye özel değil; her anonim
ziyaretçiye giden paylaşımlı istemci jetonu gibi davranıyor. (Rotasyon aralığı bu pencerede gözlenmedi; bkz.
"Açık sorular".)

---

## 5. Abonelik modeli

Hangi sayfa hangi konulara abone oluyor (`subs.jsonl`):

| Sayfa türü | Abone olunan konular |
|---|---|
| Spor sayfası (`/tr/football`) | `sport.{sport}` **+** ekrandaki her maç için `event.{id}` |
| Maç sayfası | yalnızca o maçın `event.{id}`'si |
| Canlı filtre | `sport.{sport}` |
| Favoriler / haberler / turnuva / trendler | yalnızca `event.{id}` (+ bazen `odds.{id}`); **`sport.*` yok** |

**Sonuç:** bir sporun tüm maçlarını tek abonelikle izlemek için **spor sayfası** (ya da canlı filtre) yeterli;
`sport.{sport}` konusunu yalnızca o açıyor. Tek maç izlemek için maç sayfası / `event.{id}` yeterli.

---

## 6. Üretim maliyeti (sayfayı dinleme)

Kaynak: `scripts/push_light_probe.py` (tek sekme, reklam/analitik/görsel engelli). RSS, o profile ait tüm
Chrome süreçlerinin toplamı.

| Yapılandırma | RSS | Süreç / renderer | Not |
|---|---|---|---|
| Boş sekme (robots.txt), köprü açık | **1,1 GB** | 10 / 4 | Chrome'u ayakta tutmanın taban maliyeti |
| Tek futbol sekmesi (engelli), 1. dk | 1,8 GB | 13 / 7 | NATS + `sport.football` aboneliği kuruluyor |
| aynı, 30. dk | 2,6 GB | 13 / 7 | ~30 dk'da ~0,8 GB artış |
| aynı, yeniden bağlanmadan sonra | ~1,6–2,0 GB | 13 / 7 | 30 dk'daki kopma belleği geri veriyor |

- **Renderer sayısı tek sekmede 7'de sabit kaldı.** Üç sporlu ana koşuda renderer 14→43'e çıkmıştı; bu
  **sızıntı değil**: her ek sekme (3 spor + sonda sekmeleri) ve her gezinti kendi renderer sürecini açıyor.
  Tek sekmede büyüme yok.
- **30 dakikalık yeniden bağlanmanın belleğe etkisi olumlu:** kopmada eski bağlantının renderer/bellek'i
  serbest kalıyor, RSS düşüyor. Yani uzun süreli "sayfayı dinleme" belleği monoton büyütmüyor.
- **Engelsiz vs engelli:** ana koşuda engelsiz spor sekmeleri reklam iframe'leriyle ~2–3 GB; engelli tek sekme
  ~1,8–2,6 GB. Reklam engelleme sekme başına ~0,5–1 GB kazandırıyor ama taban yine GB mertebesinde.
- **En hafif push kaynağı:** denenen hafif sayfalar (favoriler, haberler, turnuva, trendler) push bağlantısı
  açıyor ama yalnızca `event.{id}`'ye abone oluyor; `sport.*` için spor sayfası gerekiyor. Yani "tarayıcıyla,
  tek sporu sport.* ile dinle" senaryosunda en hafif seçenek engelli spor sayfası (~1,8 GB sabit durumda).

---

## 7. Doğrudan bağlantı deneyi (Talimat 07)

**Yetki:** bu deney, önceki "kendi bağlantını açma" sınırının dışında ve **Tuncay tarafından doğrudan,
açıkça onaylandı** (her adım ayrıca onaylanarak).

**Yöntem:** tarayıcı sekmesi yok. Düz bir Python istemcisi (aiohttp WebSocket, **TLS parmak izi taklidi yok**)
SofaScore'un push sunucusuna bağlandı. Kimlik, köprünün açtığı futbol sayfasının **kendi `CONNECT`
karesinden** okundu, yalnızca bellekte tutuldu (bu, her anonim ziyaretçinin tarayıcısına giden paylaşımlı
jeton; kimsenin hesabı değil). Davranış asgari: **tek bağlantı, tek konu `sport.football`, yalnız dinleme**,
PUB yok, wildcard yok, 120 sn'de bir PING. Reddedilse durulacaktı.

**Sonuç:**
- **Kabul edildi.** El sıkışma + `INFO` (`auth_required`) + `CONNECT`+`SUB` sonrası `-ERR`
  gelmedi, `MSG` akışı başladı. Düz istemci (tarayıcı TLS taklidi olmadan) reddedilmedi.
- **Kapsam birebir.** Sayfa sekmesiyle ortak ~30 dk penceresinde: doğrudan 225 kare, sayfa 225 kare;
  ayrı (id, kod) kümesi 112'ye 112, **ikisinde de aynı, sıfır fark**.
- **Gecikme aynı:** doğrudan istemcide `finished` gecikmesi medyan 0,7 / maks 1,3 sn (sayfayla aynı mertebede).
- **Dayanıklılık:** bir kopma oldu; istemci (bizim kodumuz) **bir kez** yeniden bağlanıp yeniden abone oldu ve
  akışa devam etti. İkinci kopma / `-ERR` olmadı.
- **Bellek:** istemci süreci **~216→226 MB**, düz; yeniden bağlanmada 173 MB'a düştü. Sızıntı yok.

### Yan yana karşılaştırma

| Ölçüt | Sayfa (engelsiz) | Sayfa (engelli) | Doğrudan istemci |
|---|---|---|---|
| Kapsam | tam | tam | tam (pencerede birebir) |
| Bitiş gecikmesi (medyan) | ~0,7–1,0 sn | ~0,7–1,0 sn | 0,7 sn |
| Bellek | ~2–3 GB/sekme | ~1,8–2,6 GB/sekme | **~0,2 GB** |
| Kopmayı atlatma | evet (site kodu) | evet (site kodu) | evet (bir kez, bizim kodumuz) |
| Kırılganlık | captcha, sayfa DOM değişimi, GB bellek | aynı + engelleme bakımı | paylaşımlı jeton değişirse; TLS taklidi yok (sunucu ileride düz istemciyi engelleyebilir); kullanım şartları gri alanı |

**Değerlendirme:** doğrudan istemci üretim için teknik olarak uygulanabilir ve belleğin ~onda biri. Riski
teknik değil, dayanıklılık/uyum: (a) kimlik paylaşımlı bir jeton — SofaScore değiştirirse ya da düz (tarayıcı
olmayan) istemcileri engellerse kırılır; (b) tarayıcı dışı programatik erişim kullanım şartları açısından gri
alan. Üretim kararı ölçümün yapıldığı PR'ın kapsamı dışındaydı; bu belge yalnızca ölçümü sunar. (Sonradan
`direct`, varsayılan olmayan ve yalnızca açıkça seçilen bir kaynak olarak eklendi; yukarıdaki not.)

---

## Yapılmayanlar / sınırlar

- **Doğrudan istemci** yalnızca `sport.football`, tek bağlantı, ~38 dk denendi. Çoklu konu, uzun süre (saatler),
  birden çok spor, gün boyu kimlik rotasyonu denenmedi.
- **Playwright `close` olayı** güvenilir gelmediğinden kopma pencereleri yaklaşık; "kopmada kayıp" üst sınır.
- Ölçüm tek akşam, tek bölge, kupa maçları penceresinde. Düşük trafikli saatler / başka sporların canlı
  yükü farklı olabilir.
- Üç sporlu ana koşu, bellek koruması için erken bitirilmedi (boş bellek eşik üstünde kaldı); spor başına 3+
  yeniden bağlanma döngüsü görüldükten sonra elle durduruldu.

## Açık sorular

- Push `CONNECT` kimlik bilgisi ne sıklıkta değişiyor? Bu pencerede sabitti; gün/hafta ölçeği bilinmiyor.
- Sunucu, tarayıcı olmayan düz istemciyi uzun vadede ya da çok bağlantıda engelliyor mu? Tek bağlantı/38 dk'da
  engel görülmedi.
- `sport.{sport}` konusu o sporun **tüm** maçlarını mı taşıyor, yoksa bir alt küme mi? Kapsam %100 çıktı ama
  pencere tek akşamla sınırlı.
- Yeniden bağlanma neden ~30 dakikada bir? Sunucu tarafı bağlantı ömrü mü, istemci mi — ayırt edilmedi.
