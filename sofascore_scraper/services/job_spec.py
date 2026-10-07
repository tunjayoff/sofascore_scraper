"""
İndirme işlerinin (sync, fetch, refresh) iş kaydındaki belirtimi (plan maddesi FX-20).

Kayıt, işi başlatan isteğin gövdesiyle aynı alan adlarını taşır (`POST /api/v1/jobs`, sofascore_scraper/web/api/v1/jobs.py:
SyncJobSpec ve RefreshJobSpec); servisin iç belirtimi (SyncSpec: `mode`) kaydedilmez:

    sync, fetch  {"league_id", "selections": [{"league_id", "season_ids", "match_ids"}], "follows", "only",
                  "event_ids"}
                  only: "seasons" yalnızca sezon listeleri; "events" yalnızca maç detayları (`ssc sync --only
                  events`; API gövdesinde yoktur, orada bu iş `fetch`tir); null tam indirme
                  event_ids: kimliğiyle seçilen maçlar (`fetch {event_ids}`, `ssc fetch event`); yanında
                  `selections` sunucunun onları ayırdığı turnuvalardır (turnuva başına `match_ids`; bilinmeyen maç
                  `league_id: 0`), böylece `GET /jobs?target=tournament:17` ligin maçlarını indiren işi bulur (G12)
    refresh      {"league_id", "event_ids"}; kimlikle seçilen maçlarda yanında aynı `selections`

İsteğe bağlı `names`: işin adını verdiği takiplerin ve liglerin adları, iş başlarken (`{"team:42": "Arsenal"}`).
Arayüz işi bununla adlandırır ("Takım #42" yerine), takip sonradan silinse ya da ligin verisi temizlense de. Ad
gövdenin alanı değildir; aynı işi yeniden başlatan gövde onu taşımaz.

FX-20'den önceki kayıtlar servisin belirtimidir (`mode`, kimlikle seçilen maçlar turnuva başına `selections`
olarak, yanında `event_ids`). `body` ikisini de okur ve gövde biçimine çevirir: API iş kayıtlarını hep bu biçimde
verir (`GET /api/v1/jobs`), zamanlayıcı kendi işlerini bu biçimde karşılaştırır.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, Optional, Sequence

from sofascore_scraper.logger import get_logger

if TYPE_CHECKING:
    from sofascore_scraper.services.sync import SyncSpec
    from sofascore_scraper.store import Store

logger = get_logger("JobSpec")

DOWNLOAD_KINDS = ("sync", "fetch", "refresh")
NAMES = "names"
# Adı kayda yazılan hedef türleri (maçlar sayıyla anılır)
_NAMED_KINDS = ("tournament", "team", "player")


def record(kind: str, spec: "SyncSpec", *, event_ids: Sequence[int] = (),
           names: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """
    Servisin belirtimi → iş kaydının belirtimi (istek gövdesinin alanlarıyla). event_ids: kimliğiyle seçilen maçlar
    (seçimleri bunlardan kurulmuş bir `fetch` ya da `refresh`); names: hedeflerin adları (boşsa yazılmaz).
    """
    ids = list(dict.fromkeys(int(e) for e in event_ids))
    selections = [{"league_id": s.league_id, "season_ids": list(s.season_ids), "match_ids": list(s.match_ids)}
                  for s in spec.selections]
    if kind == "refresh":
        if not ids:
            ids = [int(m) for s in spec.selections for m in s.match_ids]
        out: Dict[str, Any] = {"league_id": None if ids else spec.league_id, "event_ids": ids}
        if ids and selections:
            out["selections"] = selections  # maçların turnuvaları (`target` süzgeci, G12)
    else:
        out = {
            "league_id": spec.league_id,
            "selections": selections,
            "follows": list(getattr(spec, "follows", ()) or ()),
            "only": _only(kind, spec.mode),
            "event_ids": ids,
        }
    if names:
        out[NAMES] = dict(names)
    return out


def _only(kind: str, mode: Optional[str]) -> Optional[str]:
    if mode == "seasons":
        return "seasons"
    if mode == "details" and kind == "sync":
        return "events"
    return None


def body(kind: str, recorded: Mapping[str, Any]) -> Dict[str, Any]:
    """
    İş kaydının belirtimi → istek gövdesinin biçimi. FX-20'den önceki kayıtları (`mode`) da okur; indirme işi
    olmayan türlerin belirtimi olduğu gibi döner. Tanınmayan alanlar korunur (`names` dahil).
    """
    spec = dict(recorded)
    if kind not in DOWNLOAD_KINDS or "mode" not in spec:
        return spec
    mode = spec.pop("mode")
    selections = [s for s in (spec.pop("selections", None) or ()) if isinstance(s, Mapping)]
    event_ids = [int(e) for e in (spec.get("event_ids") or ()) if _is_id(e)]
    shaped = [{"league_id": s.get("league_id"), "season_ids": list(s.get("season_ids") or ()),
               "match_ids": list(s.get("match_ids") or ())} for s in selections]
    if kind == "refresh":
        if not event_ids:
            event_ids = [int(m) for s in selections for m in (s.get("match_ids") or ()) if _is_id(m)]
        spec["league_id"] = None if event_ids else spec.get("league_id")
        spec["event_ids"] = event_ids
        if event_ids and shaped:
            spec["selections"] = shaped
        spec.pop("follows", None)
        return spec
    spec["league_id"] = spec.get("league_id")
    spec["selections"] = shaped
    spec["follows"] = list(spec.get("follows") or ())
    spec["only"] = _only(kind, str(mode) if mode is not None else None)
    spec["event_ids"] = event_ids
    return spec


def same(kind: str, first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    """İki kayıt aynı işi mi tarif ediyor (eski ya da yeni biçim; adlar sayılmaz)."""
    a, b = body(kind, first), body(kind, second)
    a.pop(NAMES, None)
    b.pop(NAMES, None)
    return a == b


def names(store: "Store", targets: Iterable[str]) -> Dict[str, str]:
    """
    Hedeflerin (`tournament:17`, `team:42`, `player:7`) adları: takip tablosundaki ad, takip yoksa turnuvanın
    katalogdaki adı. Bulunamayan atlanır; okunamayan depo boş sonuçtur (iş yine başlar, arayüz numarayı gösterir).
    """
    found: Dict[str, str] = {}
    try:
        for target in dict.fromkeys(targets):
            kind, _, number = str(target).partition(":")
            if kind not in _NAMED_KINDS or not number.isdigit():
                continue
            entity_id = int(number)
            row = store.follows.get(kind, entity_id)
            name = row.name if row is not None else None
            if not name and kind == "tournament":
                tournament = store.entities.tournament(entity_id)
                name = tournament.name if tournament is not None else None
            if name:
                found[f"{kind}:{entity_id}"] = name
    except Exception as e:  # ad bir süsleme: okunamazsa iş adsız başlar
        logger.warning("Names of the job's targets could not be read: %s: %s", type(e).__name__, e)
    return found


def targets(recorded: Mapping[str, Any]) -> List[str]:
    """Kaydın adını verdiği turnuvalar, takımlar ve oyuncular (`league_id`, seçimlerin ligleri, takipler)."""
    found: Dict[str, None] = {}
    for value in (recorded.get("league_id"), recorded.get("tournament_id"),
                  *[s.get("league_id") for s in (recorded.get("selections") or ()) if isinstance(s, Mapping)]):
        if _is_id(value):
            found[f"tournament:{int(value)}"] = None
    for follow in recorded.get("follows") or ():
        kind = str(follow).partition(":")[0]
        if kind in _NAMED_KINDS:
            found[str(follow)] = None
    return list(found)


def _is_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


__all__ = ["DOWNLOAD_KINDS", "NAMES", "body", "names", "record", "same", "targets"]
