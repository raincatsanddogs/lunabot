"""Database Schema Bootstrapper for LunaBot Autochat Dashboard.

Ensures idempotent creation of sidecar control tables, triggers, and indices,
and applies WAL performance pragmas upon initialization.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("AutochatDashboard.Bootstrap")


def get_ddl_content() -> str:
    ddl_path = Path(__file__).resolve().parent.parent / "server" / "db" / "ddl.sql"
    if not ddl_path.exists():
        raise FileNotFoundError(f"DDL file not found at: {ddl_path}")
    return ddl_path.read_text(encoding="utf-8")


def bootstrap_database(db_path: str | Path) -> None:
    """Bootstrap sidecar tables in SQLite database.
    
    Safe to call concurrently or repeatedly across runs.
    """
    path = Path(db_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Bootstrapping autochat dashboard tables in: {path}")

    conn = sqlite3.connect(str(path), timeout=10.0)
    try:
        # 1. Apply WAL and concurrency pragmas
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA cache_size = -16000;")
        conn.execute("PRAGMA foreign_keys = ON;")

        # 2. Execute sidecar DDL statements
        ddl = get_ddl_content()
        conn.executescript(ddl)
        conn.commit()
        logger.info("Autochat dashboard database schema successfully bootstrapped.")
    except Exception as exc:
        logger.error(f"Failed to bootstrap dashboard database: {exc}", exc_info=True)
        raise
    finally:
        conn.close()
