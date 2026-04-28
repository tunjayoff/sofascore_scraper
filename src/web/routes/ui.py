from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import os

from src.config_manager import ConfigManager
from src.logger import get_logger
from src.i18n import get_i18n

router = APIRouter(include_in_schema=False)
logger = get_logger("WebUI")
config_manager = ConfigManager()
i18n = get_i18n()

# Setup Templates
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# Inject i18n into all templates
@router.on_event("startup")
async def startup_event():
    # Make i18n available in all templates
    templates.env.globals["i18n"] = i18n

@router.get("/", response_class=HTMLResponse)
async def view_dashboard(request: Request):
    """Render the Dashboard page."""
    # Ensure current language is set correctly in i18n based on config
    i18n.set_language(config_manager.get_language())
    
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
            "title": i18n.t("main_menu_title") + " - SofaScore Scraper",
            "leagues": config_manager.get_leagues(),
        },
    )

@router.get("/leagues", response_class=HTMLResponse)
async def view_leagues(request: Request):
    """Render the Leagues Management page."""
    i18n.set_language(config_manager.get_language())
    return templates.TemplateResponse(
        request=request,
        name="leagues.html",
        context={
            "request": request,
            "title": i18n.t("league_list_title") + " - SofaScore Scraper",
            "leagues": config_manager.get_leagues(),
        },
    )

@router.get("/schedule", response_class=HTMLResponse)
async def view_schedule(request: Request):
    """Render the Schedule & Results page."""
    i18n.set_language(config_manager.get_language())
    return templates.TemplateResponse(
        request=request,
        name="schedule.html",
        context={
            "request": request,
            "title": i18n.t("menu_match_data") + " - SofaScore Scraper",
            "leagues": config_manager.get_leagues(),
        },
    )

@router.get("/settings", response_class=HTMLResponse)
async def view_settings(request: Request):
    """Render the Settings page."""
    i18n.set_language(config_manager.get_language())
    
    # Prepare current settings
    current_settings = {
        "api_base_url": config_manager.get_api_base_url(),
        "use_proxy": config_manager.get_use_proxy(),
        "proxy_url": config_manager.get_proxy_url(),
        "data_dir": config_manager.get_data_dir(),
        "date_format": config_manager.get_date_format(),
        "use_color": config_manager.get_use_color(),
        "language": config_manager.get_language()
    }
    
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "request": request,
            "title": i18n.t("menu_settings") + " - SofaScore Scraper",
            "settings": current_settings,
        },
    )

@router.get("/stats", response_class=HTMLResponse)
async def read_stats(request: Request):
    """Render the statistics page."""
    return templates.TemplateResponse(
        request=request,
        name="stats.html",
        context={
            "request": request,
            "title": "SofaScore Scraper - Statistics",
            "leagues": config_manager.get_leagues(),
        },
    )

@router.get("/match/{match_id}", response_class=HTMLResponse)
async def read_match(request: Request, match_id: str):
    """Render the detailed match view page."""
    return templates.TemplateResponse(
        request=request,
        name="match_view.html",
        context={
            "request": request,
            "match_id": match_id,
            "leagues": config_manager.get_leagues(),
        },
    )
