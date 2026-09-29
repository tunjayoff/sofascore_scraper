"""Uygulama ayarları (.env) uç noktaları ve doğrulama modeli."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from src.refresh import refresh_window_hours
from src.web.routes.common import (
    config_manager,
    logger,
)

router = APIRouter(prefix="/api", tags=["api"])


_ALLOWED_API_HOSTS = {"www.sofascore.com", "api.sofascore.com"}


_REPO_ROOT = Path(__file__).resolve().parents[3]


def _no_control_chars(v: Optional[str]) -> Optional[str]:
    # .env satır tabanlı: yeni satır başka bir değişken enjekte eder
    if v is not None and any(c in v for c in "\r\n\x00"):
        raise ValueError("control characters are not allowed")
    return v


class SettingsUpdate(BaseModel):
    api_base_url: Optional[str] = None
    use_proxy: Optional[bool] = None
    proxy_url: Optional[str] = Field(default=None, max_length=500)
    data_dir: Optional[str] = Field(default=None, max_length=500)
    use_color: Optional[bool] = None
    date_format: Optional[str] = Field(default=None, max_length=50)
    language: Optional[Literal["tr", "en"]] = None
    max_concurrent: Optional[int] = Field(default=None, ge=1, le=50)
    wait_time_min: Optional[float] = Field(default=None, ge=0, le=60)
    wait_time_max: Optional[float] = Field(default=None, ge=0, le=60)
    request_timeout: Optional[int] = Field(default=None, ge=1, le=300)
    max_retries: Optional[int] = Field(default=None, ge=0, le=10)
    rate_limit_threshold_consecutive: Optional[int] = Field(default=None, ge=1, le=1000)
    rate_limit_threshold_ratio: Optional[float] = Field(default=None, gt=0, le=1)
    server_error_threshold_consecutive: Optional[int] = Field(default=None, ge=1, le=1000)
    fetch_only_finished: Optional[bool] = None
    save_empty_rounds: Optional[bool] = None
    refresh_window_hours: Optional[float] = Field(default=None, ge=0, le=720)
    log_level: Optional[Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]] = None
    debug: Optional[bool] = None

    _strip_controls = field_validator("api_base_url", "proxy_url", "data_dir", "date_format")(
        classmethod(lambda cls, v: _no_control_chars(v))
    )

    @field_validator("api_base_url")
    @classmethod
    def _api_host_allowed(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        u = urlparse(v)
        if u.scheme != "https" or u.hostname not in _ALLOWED_API_HOSTS:
            raise ValueError(f"api_base_url must be https on {sorted(_ALLOWED_API_HOSTS)}")
        return v

    @field_validator("proxy_url")
    @classmethod
    def _proxy_scheme(cls, v: Optional[str]) -> Optional[str]:
        if v:
            u = urlparse(v)
            if u.scheme not in ("http", "https", "socks5", "socks5h") or not u.hostname:
                raise ValueError("proxy_url must be http(s):// or socks5://host:port")
        return v

    @field_validator("data_dir")
    @classmethod
    def _data_dir_contained(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == config_manager.get_data_dir():
            return v
        resolved = Path(v).expanduser()
        if not resolved.is_absolute():
            resolved = _REPO_ROOT / resolved
        resolved = resolved.resolve()
        home = Path.home().resolve()
        if resolved in (home, _REPO_ROOT) or not (
            resolved.is_relative_to(_REPO_ROOT) or resolved.is_relative_to(home)
        ):
            raise ValueError("data_dir must be a folder inside the project or your home directory")
        return v


@router.get("/settings")
def get_all_settings():
    """Get all current settings."""
    return {
        "language": config_manager.get_language(),
        "api_base_url": config_manager.get_api_base_url(),
        "use_proxy": config_manager.get_use_proxy(),
        "proxy_url": config_manager.get_proxy_url(),
        "data_dir": config_manager.get_data_dir(),
        "use_color": config_manager.get_use_color(),
        "date_format": config_manager.get_date_format(),
        "max_concurrent": config_manager.get_max_concurrent(),
        "wait_time_min": config_manager.get_wait_time_min(),
        "wait_time_max": config_manager.get_wait_time_max(),
        "request_timeout": config_manager.get_request_timeout(),
        "max_retries": config_manager.get_max_retries(),
        "rate_limit_threshold_consecutive": config_manager.get_rate_limit_threshold_consecutive(),
        "rate_limit_threshold_ratio": config_manager.get_rate_limit_threshold_ratio(),
        "server_error_threshold_consecutive": config_manager.get_server_error_threshold_consecutive(),
        "fetch_only_finished": os.getenv("FETCH_ONLY_FINISHED", "true").lower() == "true",
        "save_empty_rounds": os.getenv("SAVE_EMPTY_ROUNDS", "false").lower() == "true",
        "refresh_window_hours": refresh_window_hours(),
        "log_level": os.getenv("LOG_LEVEL", "INFO"),
        "debug": os.getenv("DEBUG", "false").lower() == "true",
    }


@router.post("/settings")
def update_settings(settings: SettingsUpdate):
    """Update application settings."""
    try:
        updated = False
        env_map = {
            "language": ("APP_LANGUAGE", lambda v: v),
            "api_base_url": ("API_BASE_URL", lambda v: v),
            "use_proxy": ("USE_PROXY", lambda v: str(v).lower()),
            "proxy_url": ("PROXY_URL", lambda v: v),
            "data_dir": ("DATA_DIR", lambda v: v),
            "use_color": ("USE_COLOR", lambda v: str(v).lower()),
            "date_format": ("DATE_FORMAT", lambda v: v),
            "max_concurrent": ("MAX_CONCURRENT", lambda v: str(v)),
            "wait_time_min": ("WAIT_TIME_MIN", lambda v: str(v)),
            "wait_time_max": ("WAIT_TIME_MAX", lambda v: str(v)),
            "request_timeout": ("REQUEST_TIMEOUT", lambda v: str(v)),
            "max_retries": ("MAX_RETRIES", lambda v: str(v)),
            "rate_limit_threshold_consecutive": ("RATE_LIMIT_THRESHOLD_CONSECUTIVE", lambda v: str(v)),
            "rate_limit_threshold_ratio": ("RATE_LIMIT_THRESHOLD_RATIO", lambda v: str(v)),
            "server_error_threshold_consecutive": ("SERVER_ERROR_THRESHOLD_CONSECUTIVE", lambda v: str(v)),
            "fetch_only_finished": ("FETCH_ONLY_FINISHED", lambda v: str(v).lower()),
            "save_empty_rounds": ("SAVE_EMPTY_ROUNDS", lambda v: str(v).lower()),
            "refresh_window_hours": ("REFRESH_WINDOW_HOURS", lambda v: f"{float(v):g}"),
            "log_level": ("LOG_LEVEL", lambda v: v),
            "debug": ("DEBUG", lambda v: str(v).lower()),
        }

        settings_dict = settings.model_dump(exclude_none=True)
        for field, value in settings_dict.items():
            if field in env_map:
                env_key, converter = env_map[field]
                if config_manager.update_env_variable(env_key, converter(value)):
                    updated = True

        if updated:
            config_manager.reload_config()
            return {"status": "success", "message": "Settings updated successfully."}
        else:
            return {"status": "no_change", "message": "No settings were changed."}

    except Exception as e:
        logger.error(f"Failed to update settings: {e}")
        raise HTTPException(status_code=500, detail="Failed to update settings")
