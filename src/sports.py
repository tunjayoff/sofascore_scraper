"""
Spor kayıt defteri: desteklenen sporlar ve maç detay dilimleri tek yerde.

Okuyanlar: src/status.py (skor ailesi), src/watcher.py (izleyici parametreleri), src/match_data_fetcher.py
(istenecek detay uç noktaları), src/web/league_sports.py (liglerin sporu), src/web/routes/sports.py (GET /api/sports),
main.py (--sport seçenekleri).

Yeni spor eklemek:
  1. SPORTS'a bir SportSpec.
  2. Skor biçimi mevcut ailelerden (docs/all-sports/README.md, "Sınıf gerekçeleri") biri değilse
     src/status.py'de yeni bir extract_scores dalı.
  3. Spora özel detay uç noktası varsa DETAIL_SLICES'a bir satır (ya da var olan satırın `sports` kümesine ekleme).
  4. Web arayüzü listeyi henüz buradan almıyor: frontend/src/lib/sport.ts ve locales/{tr,en}.ts
     (tests/test_sports_registry.py ikisinin eşit kaldığını denetler).

Bu modül başka hiçbir src modülünü içe aktarmaz (döngüsel bağımlılık olmasın).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Literal, Optional, Tuple

# Skorun biçimi (src/status.py'deki ScoreSheet alt sınıfı):
#   football: devre / 90 dk / uzatma / penaltı ayrımı (FootballScores)
#   periods:  periyot toplamları, normal süre, uzatma (BasketballScores)
#   sets:     kazanılan set + set başına oyun/sayı, tie-break (TennisScores)
ScoreFamily = Literal["football", "periods", "sets"]

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
)

_BY_SLUG: Dict[str, SportSpec] = {s.slug: s for s in SPORTS}

# normalize_sport'un kayıt defterinden önceki eşlemesi: alt dizgi, bu sırayla. Davranış değişmesin diye
# korunuyor; tam eşleşme (slug / ad / alias) her zaman önce denenir. Bu yüzden ileride "table-tennis" ya da
# "american-football" kayıt defterine eklenince kendi slug'ına çözülür; eklenene kadar eskisi gibi
# "tennis" / "football" sayılır. Yeni sporlar buraya EKLENMEZ.
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


def watcher_params(slug: object) -> WatcherParams:
    """Kayıtlı olmayan spor için varsayılanlar: bitişe yakınlık kuralı yok, takılı eşiği başlangıç + 4 sa."""
    spec = get_sport(slug)
    return spec.watcher if spec else DEFAULT_WATCHER_PARAMS


# --- Maç detay dilimleri -----------------------------------------------------------------


@dataclass(frozen=True)
class DetailSlice:
    """Bir maçın /event/{id} dışındaki detay uç noktası; yanıt match_details/.../{id}/{key}.json'a yazılır."""

    key: str  # match_data anahtarı ve dosya adı
    path: str  # API kök adresine eklenir; {event_id} yer tutucusu
    # Hangi sporlarda istenir. None: hepsinde (kayıt defterinde olmayan ya da sporu bilinmeyen maç dahil)
    sports: Optional[FrozenSet[str]] = None
    # Kullanıcı ayarı yokken istenir mi. Dilimi spor başına açıp kapatan ayar eklendiğinde slices_for'da okunacak
    default_enabled: bool = True
    # True: tamlık hesabına girer (eksikse yeniden istenir, bitmiş maçta boş gelirse "yok" sayılır), tek maç
    # indirmede de istenir ve {key}.json diskten okunur. False: yalnızca toplu indirmede, en iyi çabayla.
    required: bool = True

    def applies_to(self, sport: Optional[str]) -> bool:
        return self.sports is None or sport in self.sports

    def url(self, base_url: str, event_id: object) -> str:
        return base_url + self.path.format(event_id=event_id)


# Sıra önemli: istek sırası ve DETAIL_SLICE_KEYS / REQUIRED_FILES sırası buradan türer
DETAIL_SLICES: Tuple[DetailSlice, ...] = (
    DetailSlice("statistics", "/event/{event_id}/statistics"),
    DetailSlice("team_streaks", "/event/{event_id}/team-streaks"),
    DetailSlice("pregame_form", "/event/{event_id}/pregame-form"),
    DetailSlice("h2h", "/event/{event_id}/h2h"),
    DetailSlice("lineups", "/event/{event_id}/lineups"),
    DetailSlice("incidents", "/event/{event_id}/incidents"),
    DetailSlice("point_by_point", "/event/{event_id}/point-by-point", sports=frozenset({"tennis"}), required=False),
)

def get_slice(key: str) -> Optional[DetailSlice]:
    return next((s for s in DETAIL_SLICES if s.key == key), None)


def slices_for(sport: Optional[str], required_only: bool = False) -> Tuple[DetailSlice, ...]:
    """
    Bu spordaki bir maç için istenecek dilimler, tablo sırasıyla. `sport` olayın küçük harfli slug'ıdır
    (event_sport_slug); kayıt defterinde olmayan ya da bilinmeyen (None / "") spor yalnızca her sporda
    geçerli dilimleri alır.

    Dilim seçiminin tek geçtiği yer burasıdır: kullanıcının spor başına dilim açıp kapatması eklendiğinde
    `default_enabled` yerine o ayar burada okunur.
    """
    return tuple(
        s for s in DETAIL_SLICES
        if s.default_enabled and s.applies_to(sport) and (s.required or not required_only)
    )
