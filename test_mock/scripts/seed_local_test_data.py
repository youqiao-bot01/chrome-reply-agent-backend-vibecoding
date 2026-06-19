#!/usr/bin/env python3
"""初始化 ``test_mock/local/`` 测试库（SQLite）。"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL = REPO_ROOT / "test_mock" / "local"
FEISHU_DB = LOCAL / "feishu_test.db"
REPLY_DB = LOCAL / "reply.db"


def _seed_feishu(conn: sqlite3.Connection) -> None:
    now = datetime.now()
    t1 = (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    t2 = (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    conn.executescript(
        """
        DROP TABLE IF EXISTS auto_reply_plugin_ai_reply_info;
        DROP TABLE IF EXISTS auto_reply_plugin_keywords_reply_info;
        CREATE TABLE auto_reply_plugin_ai_reply_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            create_time TEXT NOT NULL,
            source TEXT NOT NULL,
            creator_name TEXT NOT NULL,
            message_info TEXT,
            reply_result TEXT
        );
        CREATE TABLE auto_reply_plugin_keywords_reply_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            create_time TEXT NOT NULL,
            source TEXT NOT NULL,
            creator_name TEXT,
            key_words TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO auto_reply_plugin_ai_reply_info "
        "(create_time, source, creator_name, message_info, reply_result) VALUES (?,?,?,?,?)",
        (t1, "toolant", "demo_creator_1", "your price is too low", "We work on commission..."),
    )
    conn.execute(
        "INSERT INTO auto_reply_plugin_ai_reply_info "
        "(create_time, source, creator_name, message_info, reply_result) VALUES (?,?,?,?,?)",
        (t2, "Linknlatch", "demo_creator_2", "how to join?", "Please scan the QR code..."),
    )
    conn.execute(
        "INSERT INTO auto_reply_plugin_keywords_reply_info "
        "(create_time, source, creator_name, key_words) VALUES (?,?,?,?)",
        (t2, "toolant", "demo_creator_1", "price_too_low"),
    )
    conn.commit()


def _seed_reply(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS creator_shop_state (
            creator_id TEXT NOT NULL,
            shop TEXT NOT NULL,
            creator_progress TEXT,
            creator_emotion TEXT,
            shop_rejected INTEGER DEFAULT 0,
            negotiation_rounds INTEGER DEFAULT 0,
            updated_at TEXT,
            PRIMARY KEY (creator_id, shop)
        );
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO creator_shop_state "
        "(creator_id, shop, creator_progress, creator_emotion, shop_rejected, negotiation_rounds, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        ("demo_creator_1", "toolant", "未签约", "感兴趣或同意", 0, 0, datetime.now().isoformat()),
    )
    conn.commit()


def main() -> None:
    LOCAL.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(FEISHU_DB) as conn:
        _seed_feishu(conn)
    with sqlite3.connect(REPLY_DB) as conn:
        _seed_reply(conn)
    print(f"OK feishu_test.db -> {FEISHU_DB}")
    print(f"OK reply.db -> {REPLY_DB}")


if __name__ == "__main__":
    main()
    sys.exit(0)
