"""
Dışa aktarma servisi: indirilmiş maçlardan birleşik CSV (`match_details/processed/all_matches_*.csv`).

Bugünkü tek giriş `export_all_csv`'dir: web işinin son aşaması ve `POST /api/export/csv` onu çağırır. Terminal
menüsünün CSV adımının (src/ui/match_ui.py, `convert_to_csv(scope="all")`) yaptığı işi menü metni yazdırmadan
yapar; düzleştirme hâlâ MatchDataFetcher'dadır (plan maddesi EX-1 onu buraya taşır).
"""
from __future__ import annotations

from typing import Optional

from src.logger import get_logger
from src.services.context import ServiceContext

logger = get_logger("ExportService")


def export_all_csv(ctx: ServiceContext) -> Optional[str]:
    """
    İndirilmiş bütün maçları tek CSV dosyasına yazar; dosyanın yolunu döndürür.

    None: dosya üretilmedi (maç yok ya da hata). Menü adımı gibi hatayı yutar ve loglar: dışa aktarmadaki bir
    hata onu çağıran işi düşürmez (iş "Completed" biter, CSV eksik kalır). FetchCancelled BaseException olduğu
    için buradan geçer.
    """
    try:
        result = ctx.match_data_fetcher.convert_all_matches_to_csv()
    except Exception as exc:
        logger.error("CSV export failed: %s", exc)
        return None
    # `separate_by_league` verilmediği için sonuç tek bir yoldur; boş metin "üretilemedi" demektir
    return result if isinstance(result, str) and result else None
