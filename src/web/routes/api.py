"""
/api router'ı: uç noktalar alanlarına göre routes/ altındaki modüllerde durur.

Bu modül onları tek router'da toplar ve testlerin/eski import'ların kullandığı
adları yeniden dışa aktarır.
"""
from fastapi import APIRouter

from src.web.routes import auth, data, diagnostics, leagues, matches, scrape, settings, sports
from src.web.routes.common import _job_store, config_manager  # noqa: F401
from src.web.routes.data import _backups_dir  # noqa: F401
from src.web.routes.matches import (  # noqa: F401
    _build_schedule_matches_dataframe,
    _filter_matches_df_by_league,
    _get_matches_sync,
    _parse_league_ids,
)

router = APIRouter()
for _module in (leagues, matches, scrape, settings, data, sports, diagnostics, auth):
    router.include_router(_module.router)
