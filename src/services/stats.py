"""
Veri dizini istatistikleri — web (dashboard, /stats/system) ve terminal arayüzü (istatistik menüsü, raporlar)
aynı sayımları buradan alır.

Bu modül ince bir aktarıcıdır (plan maddesi RD-4): sayımlar ve disk kullanımı `StatusService.summary()`'den
(src/services/status.py), yani katalogdan gelir; buradaki işlevler onu bugünkü yanıt anahtarlarına çevirir.
Sayım kuralları o modülün belgesindedir. Dosya ağacını gezen eski sayımlardan farkı: her maç ve her sezon
listesi bir kez sayılır (iki özet dosyasında geçen maç, iki dosyası olan sezon listesi), `_no_tournament/`
altındaki ve düz dizinlerdeki maçlar da detay sayılır, maç sayısı özet CSV'sinin satırlarından değil
katalogdaki maçlardan gelir.

Tek istisna lig başına disk boyutlarıdır (`league_stats(...)["disk"]`, yalnızca terminal arayüzü kullanır):
`Store.info` veri dizininin üst düzey girdilerini verir, lig başına döküm vermez. O sayılar bugünkü gibi lig
dizinleri gezilerek bulunur (`{lig_id}_` önekli dizinler; detaylar için eski ID'siz ad da kabul edilir).
"""
from __future__ import annotations

import datetime as _dt
import glob
import os
from typing import Any, Dict, Iterable, List, Mapping, Optional

from src.paths import safe_name
from src.services.status import DataSummary, StatusService
from src.store import open_store


def format_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# --- katalogdan: sayımlar ve toplam disk kullanımı --------------------------------------------------


def data_summary(data_dir: str, leagues: Iterable[int] = ()) -> DataSummary:
    """Veri dizininin özeti; `leagues` maçı olmasa da dökümde yer alacak lig kimlikleridir."""
    return StatusService(open_store(data_dir)).summary(tournament_ids=tuple(leagues))


def league_counts(summary: DataSummary, league_id: int, league_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Bir ligin sayıları, bugünkü anahtarlarla: seasons (SofaScore'daki sezon listesi), seasons_fetched (maçı
    bilinen sezon), matches, details, coverage (%) ve last_update (ligin dosyalarındaki en yeni değişiklik,
    yerel saatle ISO; detayı yoksa None).
    """
    counts = summary.tournament(league_id)
    last_update = None
    if counts.last_update is not None:
        last_update = _dt.datetime.fromtimestamp(counts.last_update).isoformat()
    return {
        "id": league_id,
        "name": league_name,
        "seasons": counts.seasons,
        "seasons_fetched": counts.seasons_with_events,
        "matches": counts.matches,
        "details": counts.details,
        "coverage": counts.coverage if counts.matches else 0,
        "last_update": last_update,
    }


def disk_usage(summary: DataSummary) -> Dict[str, Any]:
    """Veri dizininin disk kullanımı, bugünkü anahtarlarla. `total` dört alanın toplamıdır (`datasets` dahil)."""
    disk = summary.disk
    usage: Dict[str, Any] = {
        "seasons": disk.seasons if disk else 0,
        "matches": disk.matches if disk else 0,
        "details": disk.details if disk else 0,
        "datasets": disk.datasets if disk else 0,
    }
    usage["total"] = sum(usage.values())
    usage["formatted_total"] = format_size(usage["total"])
    return usage


def system_counts(summary: DataSummary, leagues: Mapping[int, str]) -> Dict[str, Any]:
    """Tüm veri dizini: toplamlar, yapılandırılmış ligler için döküm (`league_counts`) ve disk kullanımı."""
    return {
        "leagues": len(leagues),
        "seasons": summary.seasons,
        "matches": summary.matches,
        "details": summary.details,
        "league_breakdown": [league_counts(summary, lid, name) for lid, name in leagues.items()],
        "disk_usage": disk_usage(summary),
    }


# --- lig başına disk boyutları: dosya ağacından (terminal arayüzü) ------------------------------------


def dir_size(path: str) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def _league_dirs(parent: str, league_id: int, league_name: Optional[str] = None) -> List[str]:
    """`parent` altında bu lige ait dizinler: `{id}_*`, yoksa eski ID'siz ad."""
    if not os.path.isdir(parent):
        return []
    found = [os.path.join(parent, d) for d in os.listdir(parent) if d.startswith(f"{league_id}_")]
    if not found and league_name:
        legacy = os.path.join(parent, safe_name(league_name))
        if os.path.isdir(legacy):
            found.append(legacy)
    return [d for d in found if os.path.isdir(d)]


# --- bugünkü çağrı biçimleri --------------------------------------------------------------------------


def league_stats(data_dir: str, league_id: int, league_name: Optional[str] = None, *,
                 summary: Optional[DataSummary] = None) -> Dict[str, Any]:
    """
    Bir lig için `league_counts` ve ligin disk boyutları (`disk`: seasons, matches, details, total; lig
    dizinleri gezilerek). summary: hazır bir özet varsa o kullanılır, yoksa burada alınır.
    """
    if summary is None:
        summary = data_summary(data_dir, (league_id,))
    stats = league_counts(summary, league_id, league_name)
    seasons_files = glob.glob(os.path.join(data_dir, "seasons", f"{league_id}_*_seasons.json"))
    match_dirs = _league_dirs(os.path.join(data_dir, "matches"), league_id)
    detail_dirs = _league_dirs(os.path.join(data_dir, "match_details"), league_id, league_name)
    sizes = {
        "seasons": sum(os.path.getsize(f) for f in seasons_files),
        "matches": sum(dir_size(d) for d in match_dirs),
        "details": sum(dir_size(d) for d in detail_dirs),
    }
    sizes["total"] = sum(sizes.values())
    stats["disk"] = sizes
    return stats


def system_stats(data_dir: str, leagues: Mapping[int, str]) -> Dict[str, Any]:
    """`system_counts`; dökümdeki her lig ayrıca disk boyutlarını taşır (`league_stats`)."""
    summary = data_summary(data_dir, leagues)
    stats = system_counts(summary, leagues)
    stats["league_breakdown"] = [league_stats(data_dir, lid, name, summary=summary) for lid, name in leagues.items()]
    return stats
