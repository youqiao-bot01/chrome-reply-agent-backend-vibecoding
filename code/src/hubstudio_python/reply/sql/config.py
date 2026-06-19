"""SQLite 配置：``config.yaml`` → ``db`` 节 + 环境变量。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


def _project_root() -> Path:
    from hubstudio_python.config import _project_root as repo_root

    return repo_root()


@dataclass(frozen=True)
class DbConfig:
    enabled: bool = True
    path: Path = Path("./tests/test_mock/local/reply.db")

    def sqlite_path(self) -> Path:
        p = self.path
        if not p.is_absolute():
            p = _project_root() / p
        return p


def db_config_from_env() -> DbConfig:
    enabled_raw = os.environ.get("HUBSTUDIO_DB_ENABLED", "true").strip().lower()
    enabled = enabled_raw not in ("0", "false", "no", "off")
    path_raw = os.environ.get("HUBSTUDIO_DB_PATH", "./tests/test_mock/local/reply.db").strip()
    return DbConfig(enabled=enabled, path=Path(path_raw or "./tests/test_mock/local/reply.db"))


def apply_db_settings_from_yaml(data: Mapping[str, Any] | None) -> None:
    if not data:
        return
    db = data.get("db")
    if not isinstance(db, dict):
        return
    en = db.get("enabled")
    if isinstance(en, bool):
        os.environ["HUBSTUDIO_DB_ENABLED"] = "true" if en else "false"
    elif en is not None and str(en).strip():
        os.environ["HUBSTUDIO_DB_ENABLED"] = str(en).strip()
    path = db.get("path")
    if path is not None and str(path).strip():
        os.environ["HUBSTUDIO_DB_PATH"] = str(path).strip()
