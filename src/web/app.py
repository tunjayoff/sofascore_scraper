import os
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import dotenv

from src.config_manager import ConfigManager
from src.logger import get_logger

# Load environment variables
dotenv.load_dotenv()

# Initialize Logger
logger = get_logger("WebApp")

# Initialize App
app = FastAPI(
    title="SofaScore Scraper Web UI",
    description="Web interface for SofaScore Scraper",
    version="1.0.0"
)

# Get Source Directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

# Mount Static Files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Configure Templates
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _js_single_quoted(value) -> str:
    """Güvenli şekilde tek tırnaklı JS string içine gömülür (x-data vb.)."""
    s = "" if value is None else str(value)
    return (
        s.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


templates.env.filters["js_sq"] = _js_single_quoted

# Initialize ConfigManager
config_manager = ConfigManager()

# Include Routers
from src.web.routes import api, ui
app.include_router(api.router)
app.include_router(ui.router)

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Render the dashboard/home page."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
            "title": "SofaScore Scraper - Dashboard",
            "leagues": config_manager.get_leagues(),
        },
    )

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "version": "1.0.0"}
