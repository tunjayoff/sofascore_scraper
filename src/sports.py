"""
Spor kayıt defteri: desteklenen sporlar ve maç detay dilimleri tek yerde.

Okuyanlar: src/status.py (skor ailesi), src/watcher.py (izleyici parametreleri), src/match_data_fetcher.py
(istenecek detay uç noktaları), src/web/league_sports.py (liglerin sporu), src/web/routes/sports.py (GET /api/sports),
main.py (--sport seçenekleri), src/services/planning.py (bir maçın ihtiyacı: beklenen dilimler).

Yeni spor eklemek:
  1. SPORTS'a bir SportSpec (periyot ailesindeyse `period_format`, set ailesindeyse `set_format` ile).
  2. Skor biçimi mevcut ailelerden (docs/all-sports/README.md, "Sınıf gerekçeleri") biri değilse
     src/status.py'de yeni bir extract_scores dalı ve src/schema'da yeni bir skor modeli.
  3. Spora özel detay uç noktası varsa DETAIL_SLICES'a bir satır (ya da var olan satırın `sports` kümesine ekleme);
     ortak bir dilim o sporda yoksa `not_in`, veri hep gelmiyorsa `optional_in` kümesine (DETAIL_SLICES'ın
     üstündeki kurallar; kanıt tests/fixtures/sport_slices/evidence.json'a girer).
  4. Web arayüzü listeyi henüz buradan almıyor: frontend/src/lib/sport.ts ve locales/{tr,en}.ts
     (tests/test_sports_registry.py ikisinin eşit kaldığını denetler).

Bu modül başka hiçbir src modülünü içe aktarmaz (döngüsel bağımlılık olmasın).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, FrozenSet, Iterable, Literal, Mapping, Optional, Tuple, Union

# Skorun biçimi (src/status.py'deki ScoreSheet alt sınıfı):
#   football: devre / 90 dk / uzatma / penaltı ayrımı (FootballScores)
#   periods:  periyot toplamları, normal süre, uzatma (BasketballScores)
#   sets:     kazanılan set + set başına oyun/sayı, tie-break (TennisScores, SetsScores)
#   innings:  beyzbol: inning başına sayı (run), uzatma inning'leri, isabet ve hata toplamı (InningsScores)
#   cricket:  kriket: iki tarafın innings'leri: sayı, düşen kale, over (CricketScores)
#   fight:    skor yok; sonuç yöntemi ve son raunt (MMA; FightScores)
ScoreFamily = Literal["football", "periods", "sets", "innings", "cricket", "fight"]

# Periyot ailesinde normal sürenin bölünüşü (src/status.py BasketballScores.format, şemada PeriodsScore.format):
#   quarters: dört çeyrek (period1..period4)
#   halves:   iki yarı; basketbolun iki yarılı liglerinde period2/period4, öteki sporlarda period1/period2
#   thirds:   üç periyot (period1..period3)
PeriodFormat = Literal["quarters", "halves", "thirds"]

# Set ailesinde bir setin neyle sayıldığı (src/status.py SetsScores.format; plan maddesi SP-2):
#   games:  oyun; set tie-break'i ve match tie-break olabilir (padel; tenis kendi çizelgesini kullanır)
#   points: sayı (voleybol, badminton, masa tenisi); tie-break yok
#   frames: yalnızca kazanılan frame sayısı (snooker); periodN set skoru değildir
#   legs:   set başına leg (dart, set usulü maç: olayda bestOfSets var)
#   legs_won:  yalnızca kazanılan leg sayısı (dart, setsiz maç). Kayıt defterinde durmaz: src/status.py olayın
#              bestOfSets alanı yoksa `legs`'i buna çevirir
#   games_won: yalnızca kazanılan oyun (harita) sayısı (e-spor). periodN yalnızca oyunu kimin aldığını (1 / 0)
#              söyler ve oynanmamış oyunlar da 0-0 gelir; oyunların skoru /event/{id}/esports-games dilimindedir
SetFormat = Literal["games", "points", "frames", "legs", "legs_won", "games_won"]

# Bitişe yakınlık kuralı (src/watcher.py'deki _NEAR_END_RULES):
#   football_minute: 2. yarı ≥ 80. dk ya da uzatma dakikası görüldü; uzatma/penaltı kodları
#   played_ratio:    oynanan süre ≥ normal sürenin %90'ı; saat yoksa son periyot kodu
#   last_set:        son set (defaultPeriodCount)
#   never:           kural yok; maç sayfası yalnızca listeden düşünce ya da takılınca okunur
NearEndRule = Literal["football_minute", "played_ratio", "last_set", "never"]

# Askıya alınan maç aynı id ile ertesi güne kayabiliyor (docs/status-matrix: B9/T6, B13) → başlangıç + 4 sa
DEFAULT_STUCK_AFTER_SECONDS = 4 * 3600


@dataclass(frozen=True)
class WatcherParams:
    """Canlı izleyicinin spora göre değişen parametreleri (varsayılanlar kayıtlı olmayan spor için de geçerli)."""

    near_end_rule: NearEndRule = "never"
    # Bu kadar süre geçtiği halde bitmeyen (canlı ya da başlamamış) maç "takılı" sayılır
    stuck_after_seconds: int = DEFAULT_STUCK_AFTER_SECONDS
    # True: süre startTimestamp'ten değil gerçek oyun başlangıcından sayılır (src/watcher.py play_start)
    stuck_from_play_start: bool = False


DEFAULT_WATCHER_PARAMS = WatcherParams()


@dataclass(frozen=True)
class SportSpec:
    slug: str  # SofaScore slug'ı: /sport/{slug}/..., tournament.category.sport.slug
    name: str  # SofaScore'un İngilizce adı (tournament.category.sport.name; arama sonuçlarında bu gelir)
    i18n_key: str  # web arayüzündeki ad: frontend/src/locales/{tr,en}.ts
    score_family: ScoreFamily
    watcher: WatcherParams = DEFAULT_WATCHER_PARAMS
    aliases: Tuple[str, ...] = ()  # normalize_sport için ek tam adlar (küçük harf)
    # Periyot ailesinde normal sürenin sabit bölünüşü. None: skorlardan sezilir (basketbol: çeyrek ya da iki yarı)
    period_format: Optional[PeriodFormat] = None
    # Set ailesinde setin birimi. None: tenis (2.x çizelgesi TennisScores, retired / walkover bayraklarıyla)
    set_format: Optional[SetFormat] = None

    @property
    def detail_slices(self) -> Tuple[str, ...]:
        """Bu spor için istenen detay dilimlerinin anahtarları (DETAIL_SLICES sırasıyla)."""
        return tuple(s.key for s in slices_for(self.slug))


SPORTS: Tuple[SportSpec, ...] = (
    SportSpec(
        slug="football",
        name="Football",
        i18n_key="sport.football",
        score_family="football",
        watcher=WatcherParams(near_end_rule="football_minute"),
        aliases=("soccer",),
    ),
    SportSpec(
        slug="basketball",
        name="Basketball",
        i18n_key="sport.basketball",
        score_family="periods",
        watcher=WatcherParams(near_end_rule="played_ratio"),
    ),
    SportSpec(
        slug="tennis",
        name="Tennis",
        i18n_key="sport.tennis",
        score_family="sets",
        # startTimestamp planlanan saattir (aynı kortta sıra, yağmur); retro verisinde bitmiş 60 maçın 5'i
        # başlangıçtan > 4 sa sonra bitti (maks 5,46 sa). Ölçü gerçek oyun başlangıcı, eşik 6 sa.
        watcher=WatcherParams(near_end_rule="last_set", stuck_after_seconds=6 * 3600, stuck_from_play_start=True),
    ),
    # --- A sınıfı, periyot tabanlı sporlar (docs/all-sports/README.md, "Sınıf gerekçeleri"; plan maddesi SP-1) ---
    # Ad: SofaScore'un tournament.category.sport.name değeri (research/all_sports/samples). Skorlar basketbolun
    # anahtarlarıyla gelir (periodN, normaltime, overtime, current); bölünüş sabittir.
    # Bitişe yakınlık: kayıtlı yüklerin çoğu ya da bir kısmı time.periodLength / totalPeriodCount / played taşıyan
    # sporlarda `played_ratio`; hiç saat verisi görülmeyenlerde (Aussie kuralları, futsal, florbol) `never`.
    SportSpec(
        slug="american-football",
        name="American football",
        i18n_key="sport.american-football",
        score_family="periods",
        period_format="quarters",
        # Bitmiş 30 maçın 1'inde son değişiklik başlangıçtan 4,05 sa sonra (research/all_sports/events)
        watcher=WatcherParams(near_end_rule="played_ratio", stuck_after_seconds=5 * 3600),
    ),
    SportSpec(
        slug="aussie-rules",
        name="Aussie rules",
        i18n_key="sport.aussie-rules",
        score_family="periods",
        period_format="quarters",
    ),
    SportSpec(
        slug="ice-hockey",
        name="Hockey",  # SofaScore buz hokeyine "Hockey" der
        i18n_key="sport.ice-hockey",
        score_family="periods",
        period_format="thirds",
        watcher=WatcherParams(near_end_rule="played_ratio"),
        aliases=("ice hockey",),
    ),
    SportSpec(
        slug="handball",
        name="Handball",
        i18n_key="sport.handball",
        score_family="periods",
        period_format="halves",
        watcher=WatcherParams(near_end_rule="played_ratio"),
    ),
    SportSpec(
        slug="rugby",
        name="Rugby",
        i18n_key="sport.rugby",
        score_family="periods",
        period_format="halves",
        watcher=WatcherParams(near_end_rule="played_ratio"),
    ),
    SportSpec(
        slug="futsal",
        name="Futsal",
        i18n_key="sport.futsal",
        score_family="periods",
        period_format="halves",
    ),
    SportSpec(
        slug="minifootball",
        name="Minifootball",
        i18n_key="sport.minifootball",
        score_family="periods",
        period_format="halves",
        watcher=WatcherParams(near_end_rule="played_ratio"),
        aliases=("mini football",),
    ),
    SportSpec(
        slug="floorball",
        name="Floorball",
        i18n_key="sport.floorball",
        score_family="periods",
        period_format="thirds",
    ),
    # --- A sınıfı, set tabanlı sporlar (docs/all-sports/README.md, "Sınıf gerekçeleri"; plan maddesi SP-2) ---
    # Skorlar tenisin anahtarlarıyla gelir: `current` kazanılan set, `periodN` o setteki sayı ya da oyun.
    # Çekilme ve hükmen galibiyet skorda bayrak değil, durumdur (04-schema-v1.md karar 10): bu sporların çizelgesi
    # (SetsScores) retired / walkover taşımaz. Bitişe yakınlık `last_set`: defaultPeriodCount, yoksa 3 set; kod
    # 8-12 (1.-5. set). Snooker'da set kodu yok (canlı kod 20 "Started"): `never`.
    SportSpec(
        slug="volleyball",
        name="Volleyball",
        i18n_key="sport.volleyball",
        score_family="sets",
        set_format="points",
        # Bitmiş 19 maçın 3'ünde son değişiklik başlangıçtan 4,2 / 4,22 / 5,17 sa sonra (research/all_sports/events)
        watcher=WatcherParams(near_end_rule="last_set", stuck_after_seconds=6 * 3600),
    ),
    SportSpec(
        slug="badminton",
        name="Badminton",
        i18n_key="sport.badminton",
        score_family="sets",
        set_format="points",
        watcher=WatcherParams(near_end_rule="last_set"),
    ),
    SportSpec(
        slug="table-tennis",
        name="Table tennis",
        i18n_key="sport.table-tennis",
        score_family="sets",
        set_format="points",
        watcher=WatcherParams(near_end_rule="last_set"),
    ),
    SportSpec(
        slug="padel",
        name="Padel",
        i18n_key="sport.padel",
        score_family="sets",
        set_format="games",
        # Tenis gibi sıralı kort programı: startTimestamp planlanan saattir; ölçü gerçek oyun başlangıcı, eşik 6 sa
        watcher=WatcherParams(near_end_rule="last_set", stuck_after_seconds=6 * 3600, stuck_from_play_start=True),
    ),
    SportSpec(
        slug="snooker",
        name="Snooker",
        i18n_key="sport.snooker",
        score_family="sets",
        set_format="frames",
        # Uzun maçlar oturumlara bölünür; bitmiş 4 maçın birinde son değişiklik başlangıçtan 4,27 sa sonra
        watcher=WatcherParams(stuck_after_seconds=6 * 3600),
    ),
    # --- B sınıfı: kendi durum ya da skor mantığı olan sporlar (docs/all-sports/README.md; plan maddesi SP-3) ---
    # Bitişe yakınlık kuralı hiçbirinde yok (`never`): inning, oyun ve raunt kodları için kural tanımlı değil.
    SportSpec(
        slug="baseball",
        name="Baseball",
        i18n_key="sport.baseball",
        score_family="innings",
        # Ölçülemedi: araştırmadaki bitmiş maçların changeTimestamp'i sonradan yapılan düzeltmelerdir. Uzatma
        # inning'leri ve yağmur arası maçı 4 saatin ötesine taşıyabildiği için eşik 6 sa.
        watcher=WatcherParams(stuck_after_seconds=6 * 3600),
    ),
    SportSpec(
        slug="cricket",
        name="Cricket",
        i18n_key="sport.cricket",
        score_family="cricket",
        # Birinci sınıf maçlar günlerce sürer; gün sonu durumu `willcontinue` (kod 141, "End of day 1") canlı
        # sayılır (src/status.py). Dört günlük bir maçın son değişikliği başlangıçtan 32,6 sa sonra
        # (research/all_sports/events). Beş günlük test maçı ve aralar için eşik 6 gün.
        watcher=WatcherParams(stuck_after_seconds=6 * 86400),
    ),
    SportSpec(
        slug="esports",
        name="E-sports",  # SofaScore'un adı (research/all_sports/samples/esports)
        i18n_key="sport.esports",
        score_family="sets",
        set_format="games_won",
        # bestOf 5'e kadar seriler; araştırmada bitmiş seri yok. Eşik 6 sa.
        watcher=WatcherParams(stuck_after_seconds=6 * 3600),
    ),
    SportSpec(
        slug="darts",
        name="Darts",
        i18n_key="sport.darts",
        score_family="sets",
        # Set usulü maç (bestOfSets); setsiz maç src/status.py'de `legs_won` olur. Bitmiş 9 maçın en uzunu
        # başlangıçtan 0,85 sa sonra bitti: varsayılan takılı eşiği (4 sa) yeter.
        set_format="legs",
    ),
    SportSpec(
        slug="mma",
        name="Mixed Martial Arts",  # SofaScore'un adı (research/all_sports/samples/mma)
        i18n_key="sport.mma",
        score_family="fight",
    ),
)

_BY_SLUG: Dict[str, SportSpec] = {s.slug: s for s in SPORTS}

# normalize_sport'un kayıt defterinden önceki eşlemesi: alt dizgi, bu sırayla. Davranış değişmesin diye
# korunuyor; tam eşleşme (slug / ad / alias) her zaman önce denenir. Bu yüzden "american-football" ve
# "minifootball" (SP-1) ile "table-tennis" (SP-2) kendi slug'larına çözülür. Yeni sporlar buraya EKLENMEZ.
_LEGACY_SUBSTRINGS: Tuple[Tuple[str, str], ...] = (
    ("basket", "basketball"),
    ("tennis", "tennis"),
    ("football", "football"),
    ("soccer", "football"),
)


def _name_key(raw: str) -> str:
    return raw.strip().lower().replace(" ", "-")


# Tam eşleşme tablosu: slug, ad ("Table tennis" → "table-tennis") ve alias'lar
_BY_EXACT_NAME: Dict[str, str] = {
    _name_key(raw): spec.slug for spec in SPORTS for raw in (spec.slug, spec.name, *spec.aliases)
}


def sport_slugs() -> Tuple[str, ...]:
    """Desteklenen sporların slug'ları, kayıt sırasıyla."""
    return tuple(s.slug for s in SPORTS)


def get_sport(slug: object) -> Optional[SportSpec]:
    """Slug'ı tam eşleşen spor; yoksa None. Serbest metin (ad, büyük harf) için önce normalize_sport."""
    return _BY_SLUG.get(slug) if isinstance(slug, str) else None


def normalize_sport(raw: object) -> Optional[str]:
    """
    SofaScore'un verdiği spor adını ya da slug'ını ("Football", "football", "Tennis") kayıtlı slug'a çevirir;
    tanınmıyorsa None.
    """
    s = str(raw or "").strip().lower()
    exact = _BY_EXACT_NAME.get(_name_key(s))
    if exact:
        return exact
    for marker, slug in _LEGACY_SUBSTRINGS:
        if marker in s and slug in _BY_SLUG:
            return slug
    return None


def event_sport_slug(event: Optional[Dict[str, Any]]) -> Optional[str]:
    """Olayın sporu: tournament.category.sport.slug (yoksa name), küçük harf. Normalize edilmez; yoksa None."""
    node: Any = event
    for key in ("tournament", "category", "sport"):
        node = node.get(key) if isinstance(node, dict) else None
    if not isinstance(node, dict):
        return None
    raw = node.get("slug") or node.get("name")
    return str(raw).lower() if raw else None


def score_family(slug: object) -> Optional[ScoreFamily]:
    spec = get_sport(slug)
    return spec.score_family if spec else None


def period_format(slug: object) -> Optional[PeriodFormat]:
    """Periyot ailesindeki sporun sabit bölünüşü; sezilen (basketbol) ya da kayıtlı olmayan sporda None."""
    spec = get_sport(slug)
    return spec.period_format if spec else None


def set_format(slug: object) -> Optional[SetFormat]:
    """Set ailesindeki sporun set birimi; tenis (kendi çizelgesi) ya da kayıtlı olmayan sporda None."""
    spec = get_sport(slug)
    return spec.set_format if spec else None


def watcher_params(slug: object) -> WatcherParams:
    """Kayıtlı olmayan spor için varsayılanlar: bitişe yakınlık kuralı yok, takılı eşiği başlangıç + 4 sa."""
    spec = get_sport(slug)
    return spec.watcher if spec else DEFAULT_WATCHER_PARAMS


# --- Dilim kayıt defteri --------------------------------------------------------------------

# Dilimin sahibi: yükü hangi varlığa aittir (docs/design/02-services.md 3.1). Bugün kayıtlı her dilim maçındır.
SliceOwner = Literal["event", "season", "tournament", "team", "player", "sport"]
# Maçın evresi: dilim hangi evrede var olabilir (yalnızca maç sahipli dilimlerde anlamlı)
Phase = Literal["pre", "live", "post"]

OWNERS: Tuple[str, ...] = ("event", "season", "tournament", "team", "player", "sport")
PHASES: Tuple[str, ...] = ("pre", "live", "post")
ALL_PHASES: FrozenSet[str] = frozenset(PHASES)
# Dilim grupları: seçimde bir dilim anahtarı yerine grup adı da yazılabilir ([defaults] slices = ["core"])
GROUPS: Tuple[str, ...] = ("core", "odds", "standings", "statistics", "squads", "rankings", "live")
PROVIDER_SUBS = "provider"  # subs="provider": alt anahtar yapılandırılan bahis sağlayıcısının kimliğidir


@dataclass(frozen=True)
class SliceSpec:
    """
    Bir dilimin tanımı (docs/design/02-services.md 3.1). Bugünkü bütün dilimler bir maçın /event/{id} dışındaki
    detay uç noktalarıdır; yanıt maçın kaydına `key` adıyla yazılır.

    Varsayılanlar bugünkü davranışı verir: sahibi maç, alt anahtarı yok, her evrede istenebilir (bugün hiçbir
    yol evreye bakmaz), geçmişi tutulmaz, bir kez alınır, grubu `core`, her sporda aynı tamlık kuralı.

    `not_in` ve `optional_in` tasarımdaki alanlara eklidir: aynı uç nokta bir sporda hiç yok, ötekinde her
    maçta yok olabilir; dilim anahtarı ise her sporda aynı kalır (Store'un adresi spordan bağımsızdır).
    """

    key: str  # match_data anahtarı ve dosya adı
    path: str  # API kök adresine eklenir; {event_id} yer tutucusu
    # Hangi sporlarda istenir. None: hepsinde (kayıt defterinde olmayan ya da sporu bilinmeyen maç dahil)
    sports: Optional[FrozenSet[str]] = None
    # Kullanıcı seçimi yokken istenir mi (`select_slices`, seçim None)
    default_enabled: bool = True
    # True: tamlık hesabına girer (eksikse yeniden istenir, bitmiş maçta boş gelirse "yok" sayılır), tek maç
    # indirmede de istenir ve {key}.json diskten okunur. False: yalnızca toplu indirmede, en iyi çabayla.
    # Tasarımdaki adı `counts_for_completeness` (aşağıdaki özellik); alan adı okuyucular için `required` kalır.
    required: bool = True
    owner: SliceOwner = "event"
    # Alt anahtarlar: None = yok (""), bir demet (ör. ("total", "home", "away")) ya da "provider"
    subs: Union[Tuple[str, ...], Literal["provider"], None] = None
    phases: FrozenSet[str] = ALL_PHASES
    group: str = "core"
    keep_history: bool = False  # değişen yük dilimin geçmişine eklenir (ör. oranlar)
    max_age: Optional[timedelta] = None  # sahibi maç olmayan dilimde: bundan eskiyse yeniden alınır (None = bir kez)
    # SofaScore'un bu dilimi sunmadığı sporlar: orada hiç istenmez (`sports` None iken de). Kanıt:
    # DETAIL_SLICES'ın üstündeki not
    not_in: FrozenSet[str] = frozenset()
    # Dilimin istendiği ama tamlık hesabına girmediği sporlar (`required` True iken): orada veri hep gelmiyor
    optional_in: FrozenSet[str] = frozenset()

    def __post_init__(self) -> None:
        if self.owner not in OWNERS:
            raise ValueError(f"slice {self.key!r}: owner must be one of {', '.join(OWNERS)}, got {self.owner!r}")
        if not self.phases or not set(self.phases) <= ALL_PHASES:
            raise ValueError(f"slice {self.key!r}: phases must be a non-empty subset of {', '.join(PHASES)}")
        if self.group not in GROUPS:
            raise ValueError(f"slice {self.key!r}: group must be one of {', '.join(GROUPS)}, got {self.group!r}")
        if not (self.subs is None or self.subs == PROVIDER_SUBS
                or (isinstance(self.subs, tuple) and self.subs and all(isinstance(s, str) and s for s in self.subs))):
            raise ValueError(f"slice {self.key!r}: subs must be None, 'provider' or a tuple of names")
        if self.not_in & self.optional_in:
            raise ValueError(f"slice {self.key!r}: a sport cannot be in both not_in and optional_in")
        if self.optional_in and not self.required:
            raise ValueError(f"slice {self.key!r}: optional_in needs a required slice")
        if self.sports is not None and not (self.not_in | self.optional_in) <= self.sports:
            raise ValueError(f"slice {self.key!r}: not_in and optional_in must be subsets of sports")

    @property
    def counts_for_completeness(self) -> bool:
        """
        Tasarımdaki ad: `required`. Spordan bağımsız varsayılandır (sporu bilinmeyen maç ve `optional_in`'de
        olmayan her spor); bir sporun maçı için `counts_in(spor)`.
        """
        return self.required

    def applies_to(self, sport: Optional[str]) -> bool:
        return (self.sports is None or sport in self.sports) and sport not in self.not_in

    def counts_in(self, sport: Optional[str]) -> bool:
        """Bu spordaki bir maçta tamlık hesabına girer mi (dilim o sporda istenmiyorsa hayır)."""
        return self.required and self.applies_to(sport) and sport not in self.optional_in

    def valid_in(self, phase: Optional[str]) -> bool:
        """Dilim bu evrede var olabilir mi; evre bilinmiyorsa (None) evet."""
        return phase is None or phase in self.phases

    def url(self, base_url: str, event_id: object) -> str:
        return base_url + self.path.format(event_id=event_id)


# Eski ad: bugünkü bütün dilimler maç detayıdır
DetailSlice = SliceSpec


# Her sporun dilimleri, sitenin maç sayfasının istediklerinden (research/all_sports, PR #17):
#   * bitmiş maçta veriyle yanıtlanan ve bitmiş maçta hiç 404 almayan dilim tamlık hesabına girer (`required`);
#   * başlamış (canlı ya da bitmiş) bir maçta 404 alan, bitmiş maçta verisi görülmeyen dilim istenir ama tamlık
#     hesabına girmez (`optional_in`): eksik kalınca maç yeniden doldurulmaz;
#   * sayfa açılınca istenen bir dilim (istatistik, kadro, olaylar) o sporun eksiksiz yüklenmiş canlı ya da
#     bitmiş maç sayfasında hiç istenmediyse o sporda istenmez (`not_in`). Eksiksiz: sayfa /event/{id}'yi ve
#     /event/{id}/pregame-form'u istedi. H2H ve seriler bitmiş maçta ayrı sekmededir: sekmesi açılmayan sporda
#     yokluk kanıt sayılmaz;
#   * kanıt yoksa ya da yetersizse (sayfası açılmamış, 403 almış ya da yarım yüklenmiş sporlar) bugünkü
#     davranış kalır. Futbol, basketbol ve tenis değişmez (goldenları bugünkü davranışı sabitler); kanıtın
#     onlar için önerdikleri tests/test_sport_slices.py'de PROPOSALS'tadır.
# Kanıt tablosu: tests/fixtures/sport_slices/evidence.json (research/all_sports'tan türer; testler denetler).
#
# Sıra önemli: istek sırası ve DETAIL_SLICE_KEYS / REQUIRED_FILES sırası buradan türer
DETAIL_SLICES: Tuple[SliceSpec, ...] = (
    # Kriket, futsal, padel: bitmiş maçta 404. E-spor, snooker: canlı maçta 404
    SliceSpec("statistics", "/event/{event_id}/statistics",
              optional_in=frozenset({"cricket", "futsal", "padel", "esports", "snooker"})),
    SliceSpec("team_streaks", "/event/{event_id}/team-streaks"),
    # Bitmiş maçta 404: kriket, dart, futsal, buz hokeyi, padel. Canlı maçta 404: e-spor, snooker. Veriyle
    # yalnızca MMA'da (bitmiş) ve su topunda (başlamamış) görüldü
    SliceSpec("pregame_form", "/event/{event_id}/pregame-form",
              optional_in=frozenset({"cricket", "darts", "futsal", "ice-hockey", "padel", "esports", "snooker"})),
    SliceSpec("h2h", "/event/{event_id}/h2h"),
    # Bireysel sporların sayfası kadro istemiyor (dart, MMA, padel, snooker); futsal ve mini futbolda bitmiş
    # maçta 404
    SliceSpec("lineups", "/event/{event_id}/lineups",
              not_in=frozenset({"darts", "mma", "padel", "snooker"}),
              optional_in=frozenset({"futsal", "minifootball"})),
    SliceSpec("incidents", "/event/{event_id}/incidents",
              not_in=frozenset({"darts", "esports", "mma", "padel", "snooker"})),
    # Dartta da var: set ve leg başına kalan sayılar (bitmiş maçta veriyle; research/all_sports/samples/darts).
    # Teniste bugünkü gibi isteğe bağlı kalır
    SliceSpec("point_by_point", "/event/{event_id}/point-by-point", sports=frozenset({"tennis", "darts"}),
              optional_in=frozenset({"tennis"})),
    # E-sporda her oyunun (haritanın) durumu, kazananı ve yarı skorları (SP-3). Oyun nesneleri maç olarak
    # dizinlenmez: yanıt maçın bir dilimidir. Oyunlar maç başlayınca vardır.
    SliceSpec("esports_games", "/event/{event_id}/esports-games", sports=frozenset({"esports"}), required=False,
              phases=frozenset({"live", "post"})),
    # Kriketin skor kartı: her innings'in vuran ve atan takımı, sayı, düşen kale, over ve ekstralar. Maç
    # başlayınca vardır; bitmiş maçta veriyle yanıtlandı (research/all_sports/samples/cricket)
    SliceSpec("innings", "/event/{event_id}/innings", sports=frozenset({"cricket"}),
              phases=frozenset({"live", "post"})),
)


def get_slice(key: str) -> Optional[SliceSpec]:
    return next((s for s in DETAIL_SLICES if s.key == key), None)


class UnknownSliceName(ValueError):
    """Seçimde kayıt defterinde olmayan bir ad (ne dilim anahtarı ne grup adı)."""

    def __init__(self, name: str, known: Tuple[str, ...]) -> None:
        super().__init__(f"unknown slice or slice group {name!r}; expected one of {', '.join(known)}")
        self.name = name
        self.known = known


@dataclass(frozen=True)
class SliceSelection:
    """
    Kullanıcının dilim seçimi, katmanları çözülmüş halde (02-services.md 3.1: takibin `slices`'ı →
    `[slices.<spor>]` → `[defaults] slices` → kayıt defterinin `default_enabled`'ı). Adlar dilim anahtarı ya da
    grup adıdır; grup adı o gruptaki bütün dilimleri seçer. Bir ad hem anahtar hem grup olabilir (tasarımda
    `statistics` ikisi de): o zaman ikisini de seçer.

    base: None = kayıt defterinin `default_enabled` dilimleri; bir demet = yalnızca bu adlar.
    enable / disable: tabana eklenen ve tabandan çıkarılan adlar (disable sonra uygulanır).
    layers: enable / disable'dan sonra sırayla uygulanan (ekle, çıkar) çiftleri; sonraki katman öncekini ezer
    (`resolve_selection`: önce sporun, sonra takibin farkı). Bir katmanın içinde de çıkarma sonra uygulanır.
    """

    base: Optional[Tuple[str, ...]] = None
    enable: Tuple[str, ...] = ()
    disable: Tuple[str, ...] = ()
    layers: Tuple[Tuple[Tuple[str, ...], Tuple[str, ...]], ...] = ()

    def names(self) -> Tuple[str, ...]:
        """Seçimde geçen bütün adlar (denetim için)."""
        out: Tuple[str, ...] = (*(self.base or ()), *self.enable, *self.disable)
        for enable, disable in self.layers:
            out += (*enable, *disable)
        return out


def known_slice_names() -> Tuple[str, ...]:
    """Seçimde geçerli adlar: kayıtlı dilim anahtarları (tablo sırasıyla), sonra öteki grup adları."""
    keys = tuple(s.key for s in DETAIL_SLICES)
    return keys + tuple(group for group in GROUPS if group not in keys)


def check_slice_names(names: Iterable[str]) -> None:
    """Her ad bir dilim anahtarı ya da grup adı olmalı; değilse UnknownSliceName (ilk bilinmeyen ad)."""
    known = known_slice_names()
    for name in names:
        if name not in known:
            raise UnknownSliceName(str(name), known)


# Takibin `slices` alanının biçimleri (src/config/settings.py FollowSpec, follows.slices_json):
#   {"include": [...]}                  yalnızca bu adlar
#   {"enable": [...], "disable": [...]} varsayılan seçime (sporun farkı dahil) göre fark
FOLLOW_SLICE_KEYS: Tuple[str, ...] = ("include", "enable", "disable")


def follow_slices(value: Any) -> Optional[Dict[str, Tuple[str, ...]]]:
    """
    Takibin `slices` değerini denetler ve olağan biçimine çevirir: None, {"include": (...)} ya da
    {"enable": (...), "disable": (...)}. Bir ad listesi {"include": ...} sayılır. Biçim bozuksa ValueError,
    bilinmeyen ad UnknownSliceName (o da bir ValueError).
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        value = {"include": list(value)}
    if not isinstance(value, Mapping):
        raise ValueError('slices: expected null, a list of slice names, {"include": [...]} or '
                         '{"enable": [...], "disable": [...]}')
    unknown = sorted(str(key) for key in value if key not in FOLLOW_SLICE_KEYS)
    if unknown:
        raise ValueError(f"slices: unknown key {unknown[0]!r}; expected include, or enable and disable")
    if "include" in value and ("enable" in value or "disable" in value):
        raise ValueError("slices: give include, or enable and disable, not both")
    out: Dict[str, Tuple[str, ...]] = {}
    for key in FOLLOW_SLICE_KEYS:
        if key not in value:
            continue
        names = value[key] if value[key] is not None else []
        if isinstance(names, str) or not isinstance(names, (list, tuple)) \
                or not all(isinstance(name, str) and name.strip() for name in names):
            raise ValueError(f"slices.{key}: expected a list of slice names")
        out[key] = tuple(dict.fromkeys(name.strip() for name in names))
    if "include" not in out:
        out = {"enable": out.get("enable", ()), "disable": out.get("disable", ())}
    check_slice_names(name for names in out.values() for name in names)
    return out


def resolve_selection(defaults: Optional[Iterable[str]] = None, *, sport_enable: Iterable[str] = (),
                      sport_disable: Iterable[str] = (), follow: Any = None) -> SliceSelection:
    """
    Katmanları tek bir seçime çevirir (02-services.md 3.1; P12'nin eşlemesi):

      takibin {"include": [...]}       → base; sporun farkı ve varsayılanlar uygulanmaz (yalnızca bu adlar)
      [defaults] slices                → base (None: kayıt defterinin `default_enabled`'ı)
      [slices.<spor>] enable / disable → ilk katman
      takibin enable / disable         → ikinci katman (sporun farkını ezer)

    Adlar denetlenir: bilinmeyen ad UnknownSliceName; takibin biçimi bozuksa ValueError.
    """
    resolved = follow_slices(follow)
    if resolved is not None and "include" in resolved:
        selection = SliceSelection(base=resolved["include"])
    else:
        layers = [(tuple(sport_enable), tuple(sport_disable))]
        if resolved is not None:
            layers.append((resolved["enable"], resolved["disable"]))
        selection = SliceSelection(base=tuple(defaults) if defaults is not None else None,
                                   layers=tuple(layer for layer in layers if layer[0] or layer[1]))
    check_slice_names(selection.names())
    return selection


def _named(spec: SliceSpec, names: Iterable[str]) -> bool:
    return any(name == spec.key or name == spec.group for name in names)


def select_slices(
    owner: str,
    sport: Optional[str],
    selection: Union[SliceSelection, Iterable[str], None] = None,
    *,
    phase: Optional[str] = None,
) -> Tuple[SliceSpec, ...]:
    """
    Bir sahibin (bugün yalnızca "event") bu sporda istenecek dilimleri, tablo sırasıyla.

    sport: olayın küçük harfli slug'ı (event_sport_slug); kayıt defterinde olmayan ya da bilinmeyen (None / "")
    spor yalnızca her sporda geçerli dilimleri alır. selection: None = kayıt defterinin varsayılanları; bir ad
    listesi = `SliceSelection(base=...)`. Bilinmeyen ad UnknownSliceName'dir (yapılandırmadaki adlar burada
    denetlenir). phase: verilirse yalnızca o evrede var olabilen dilimler.

    Dilim seçiminin tek geçtiği yer burasıdır; `slices_for` buna devreder.
    """
    if owner not in OWNERS:
        raise ValueError(f"owner must be one of {', '.join(OWNERS)}, got {owner!r}")
    if phase is not None and phase not in PHASES:
        raise ValueError(f"phase must be one of {', '.join(PHASES)} or None, got {phase!r}")
    if selection is not None and not isinstance(selection, SliceSelection):
        if isinstance(selection, str):
            raise ValueError("selection: expected a list of slice names, got a single string")
        selection = SliceSelection(base=tuple(selection))
    if selection is not None:
        check_slice_names(selection.names())

    def selected(spec: SliceSpec) -> bool:
        if selection is None:
            return spec.default_enabled
        chosen = spec.default_enabled if selection.base is None else _named(spec, selection.base)
        if _named(spec, selection.enable):
            chosen = True
        if _named(spec, selection.disable):
            chosen = False
        for enable, disable in selection.layers:
            if _named(spec, enable):
                chosen = True
            if _named(spec, disable):
                chosen = False
        return chosen

    return tuple(
        s for s in DETAIL_SLICES
        if s.owner == owner and s.applies_to(sport) and s.valid_in(phase) and selected(s)
    )


def slices_for(sport: Optional[str], required_only: bool = False) -> Tuple[SliceSpec, ...]:
    """
    Bu spordaki bir maç için istenecek dilimler, tablo sırasıyla (kayıt defterinin varsayılan seçimi;
    `select_slices("event", sport)`). `sport` olayın küçük harfli slug'ıdır (event_sport_slug); kayıt
    defterinde olmayan ya da bilinmeyen (None / "") spor yalnızca her sporda geçerli dilimleri alır.
    required_only: yalnızca bu sporda tamlık hesabına girenler (`counts_in`).
    """
    return tuple(s for s in select_slices("event", sport) if s.counts_in(sport) or not required_only)
