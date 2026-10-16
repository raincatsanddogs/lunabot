"""Server Configuration for Autochat Dashboard.

Reads environment variables with typed fallbacks using Pydantic Settings.
"""
from __future__ import annotations

import secrets
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings


class ServerSettings(BaseSettings):
    HOST: str = "0.0.0.0"
    PORT: int = 18088
    DB_PATH: str = "data/chat/autochat/engine/autochat.sqlite"
    
    # Security & Auth
    JWT_SECRET: str = secrets.token_urlsafe(32)
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_SECONDS: int = 7200  # 2 hours
    COOKIE_NAME: str = "lunabot_sid"
    COOKIE_SECURE: bool = False  # Set to True in production HTTPS
    
    # Frontend SPA assets
    STATIC_DIR: str = str(Path(__file__).resolve().parent.parent / "static")

    class Config:
        env_prefix = "AUTOCHAT_DASHBOARD_"
        case_sensitive = True


settings = ServerSettings()
