"""Run the Autochat Dashboard Sidecar as an independent process.

Usage:
    python -m src.services.autochat_dashboard.server [--host 0.0.0.0] [--port 18088] [--db PATH]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from .config import settings
from .main import app


def main():
    parser = argparse.ArgumentParser(description="LunaBot Autochat Dashboard Sidecar")
    parser.add_argument("--host", default=settings.HOST, help=f"Host address (default: {settings.HOST})")
    parser.add_argument("--port", type=int, default=settings.PORT, help=f"Port (default: {settings.PORT})")
    parser.add_argument("--db", default=settings.DB_PATH, help=f"SQLite path (default: {settings.DB_PATH})")
    args = parser.parse_args()

    settings.HOST = args.host
    settings.PORT = args.port
    settings.DB_PATH = str(Path(args.db).resolve())

    print("=" * 64)
    print("🌙 LunaBot Autochat Dashboard Sidecar Starting...")
    print(f"📁 Database:    {settings.DB_PATH}")
    print(f"🌐 Listen on:   http://{settings.HOST}:{settings.PORT}")
    print(f"🔗 Local Web:   http://127.0.0.1:{settings.PORT}")
    print("=" * 64)

    uvicorn.run(app, host=settings.HOST, port=settings.PORT, log_level="info")


if __name__ == "__main__":
    main()
