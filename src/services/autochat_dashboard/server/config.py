"""Server Configuration for Autochat Dashboard.

Reads configuration with priority:
1. Environment variables (`AUTOCHAT_DASHBOARD_*`)
2. `config/chat/autochat_dashboard.yaml` (if present)
3. `example_config/chat/autochat_dashboard.yaml`
4. Built-in defaults
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic_settings import BaseSettings


def _load_yaml_config() -> Dict[str, Any]:
    candidates = [
        Path("config/chat/autochat_dashboard.yaml"),
        Path("example_config/chat/autochat_dashboard.yaml"),
    ]
    for path in candidates:
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict):
                    mapping = {
                        "host": "HOST",
                        "port": "PORT",
                        "db_path": "DB_PATH",
                        "public_url": "PUBLIC_URL",
                        "cookie_secure": "COOKIE_SECURE",
                        "cookie_name": "COOKIE_NAME",
                        "jwt_secret": "JWT_SECRET",
                        "jwt_algorithm": "JWT_ALGORITHM",
                        "jwt_expire_seconds": "JWT_EXPIRE_SECONDS",
                    }
                    cleaned = {}
                    for k, v in data.items():
                        if k in mapping and v is not None and v != "":
                            cleaned[mapping[k]] = v
                    return cleaned
            except Exception:
                pass
    return {}


class ServerSettings(BaseSettings):
    HOST: str = "0.0.0.0"
    PORT: int = 18088
    DB_PATH: str = "data/chat/autochat/engine/autochat.sqlite"
    PUBLIC_URL: Optional[str] = None

    # Security & Auth
    JWT_SECRET: str = secrets.token_urlsafe(32)
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_SECONDS: int = 7200  # 2 hours
    COOKIE_NAME: str = "lunabot_sid"
    COOKIE_SECURE: bool = False  # Controlled via config or AUTOCHAT_DASHBOARD_COOKIE_SECURE

    # Frontend SPA assets
    STATIC_DIR: str = str(Path(__file__).resolve().parent.parent / "static")

    class Config:
        env_prefix = "AUTOCHAT_DASHBOARD_"
        case_sensitive = True


def get_server_settings() -> ServerSettings:
    yaml_config = _load_yaml_config()
    final_kwargs = dict(yaml_config)
    for key in [
        "HOST",
        "PORT",
        "DB_PATH",
        "PUBLIC_URL",
        "COOKIE_SECURE",
        "COOKIE_NAME",
        "JWT_SECRET",
        "JWT_ALGORITHM",
        "JWT_EXPIRE_SECONDS",
    ]:
        env_val = os.environ.get(f"AUTOCHAT_DASHBOARD_{key}")
        if env_val is not None:
            if key in ("PORT", "JWT_EXPIRE_SECONDS"):
                try:
                    final_kwargs[key] = int(env_val)
                except ValueError:
                    pass
            elif key in ("COOKIE_SECURE",):
                final_kwargs[key] = env_val.lower() in ("true", "1", "yes")
            else:
                final_kwargs[key] = env_val
    return ServerSettings(**final_kwargs)


settings = get_server_settings()
