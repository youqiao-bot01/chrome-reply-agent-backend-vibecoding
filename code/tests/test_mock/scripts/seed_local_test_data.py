#!/usr/bin/env python3
"""初始化 ``tests/test_mock/local/`` 测试库（SQLite）。"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

MOCK_ROOT = Path(__file__).resolve().parents[1]
CODE = Path(__file__).resolve().parents[3]
LOCAL = MOCK_ROOT / "local"
FEISHU_DB = LOCAL / "feishu_test.db"
REPLY_DB = LOCAL / "reply.db"
