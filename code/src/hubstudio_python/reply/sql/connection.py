"""SQLite 连接与 schema 初始化。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from hubstudio_python.reply.sql.config import DbConfig, db_config_from_env

_SCHEMA = """
CREATE TABLE IF NOT EXISTS creator_shop_state (
    creator_id TEXT NOT NULL,
    shop TEXT NOT NULL,
    creator_name TEXT NOT NULL DEFAULT '',
    creator_type TEXT NOT NULL DEFAULT 'GEN',
    creator_progress TEXT NOT NULL DEFAULT 'GEN',
    creator_emotion TEXT NOT NULL DEFAULT 'GEN',
    monthly_gmv REAL,
    other_creator_conditions TEXT NOT NULL DEFAULT '[]',
    shop_rejected INTEGER NOT NULL DEFAULT 0,
    extra_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (creator_id, shop)
);
CREATE INDEX IF NOT EXISTS idx_creator_shop_rejected
    ON creator_shop_state (shop, shop_rejected);
"""


def init_db(config: DbConfig | None = None) -> Path:
    from pathlib import Path

    cfg = config or db_config_from_env()
    if not cfg.enabled:
        return cfg.sqlite_path()
    path = cfg.sqlite_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()
    return path


@contextmanager
def get_connection(config: DbConfig | None = None) -> Iterator[sqlite3.Connection]:
    cfg = config or db_config_from_env()
    if not cfg.enabled:
        raise RuntimeError("local database is disabled (HUBSTUDIO_DB_ENABLED=false)")
    path = init_db(cfg)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
