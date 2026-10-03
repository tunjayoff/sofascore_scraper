"""
Veri dizini istatistikleri — eski web rotaları (dashboard, /stats/system) sayımları buradan alır.

Bu modül ince bir aktarıcıdır (plan maddesi RD-4): sayımlar ve disk kullanımı `StatusService.summary()`'den
(src/services/status.py), yani katalogdan gelir; buradaki işlevler onu bugünkü yanıt anahtarlarına çevirir.
Sayım kuralları o modülün belgesindedir. Dosya ağacını gezen eski sayımlardan farkı: her maç ve her sezon
listesi bir kez sayılır (iki özet dosyasında geçen maç, iki dosyası olan sezon listesi), `_no_tournament/`
altındaki ve düz dizinlerdeki maçlar da detay sayılır, maç sayısı özet CSV'sinin satırlarından değil
katalogdaki maçlardan gelir.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, Iterable, Mapping, Optional

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
