from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Mapping

import pymysql
import yaml
from pymysql import err as pymysql_err
from pymysql.cursors import DictCursor


def code_root() -> Path:
    """``code/src/hubstudio_python/feishu/config.py`` 向上三级为 ``code/``。"""
    return Path(__file__).resolve().parents[3]


def repo_root() -> Path:
    from hubstudio_python.config import _project_root

    return _project_root()


def default_config_file() -> Path:
    override = os.environ.get("HUBSTUDIO_CONFIG_FILE", "").strip()
    if override:
        p = Path(override)
        return p if p.is_absolute() else code_root() / p
    return code_root() / "config.yaml"


def load_project_yaml(path: str | Path | None = None) -> dict[str, Any]:
    config_file = Path(path) if path else default_config_file()
    if not config_file.is_file():
        return {}
    raw = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def config_value(section: Mapping[str, Any], key: str, env_key: str | None = None) -> Any:
    env_name_key = env_key or f"{key}_env"
    raw_env_name = section.get(env_name_key)
    if isinstance(raw_env_name, str) and raw_env_name.strip():
        value = os.environ.get(raw_env_name.strip())
        if value is not None and str(value).strip():
            return value
    return section.get(key)


def _resolve_db_section(doc: Mapping[str, Any]) -> dict[str, Any] | None:
    feishu_db = doc.get("feishu_db")
    if isinstance(feishu_db, dict):
        if str(feishu_db.get("driver", "")).strip().lower() == "sqlite":
            return feishu_db
        if feishu_db.get("host"):
            return feishu_db
    db = doc.get("db")
    if isinstance(db, dict) and db.get("host"):
        return db
    mysql = doc.get("mysql")
    if isinstance(mysql, dict) and mysql.get("host"):
        database = mysql.get("ai_reply_log_database") or mysql.get("database")
        if database:
            merged = dict(mysql)
            merged["database"] = database
            return merged
    return None


def _sqlite_path(section: Mapping[str, Any]) -> Path:
    raw = config_value(section, "path") or "./test_mock/local/feishu_test.db"
    p = Path(str(raw))
    if not p.is_absolute():
        p = repo_root() / p
    return p


def load_db_config_from_yaml_file(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise RuntimeError(f"{path}: YAML 根节点必须是映射（键值对象）")
    db = _resolve_db_section(data)
    if not isinstance(db, dict):
        raise RuntimeError(
            f"{path}: 需要 feishu_db: 节点（MySQL 或 driver: sqlite），"
            "或 mysql: + ai_reply_log_database"
        )
    if str(db.get("driver", "")).strip().lower() == "sqlite":
        return {"driver": "sqlite", "path": _sqlite_path(db)}

    host = config_value(db, "host")
    port = config_value(db, "port")
    user = config_value(db, "user")
    database = config_value(db, "database")
    if database is None and "name" in db:
        database = config_value(db, "name")
    if not host or port is None or not user or database is None:
        raise RuntimeError(f"{path}: 数据库配置缺少 host, port, user, database（或 name）")

    env_key = db.get("password_env")
    if isinstance(env_key, str) and env_key.strip():
        name = env_key.strip()
        raw = os.environ.get(name)
        if raw is None:
            raise RuntimeError(f"{path}: 已配置 password_env: {name!r}，但当前进程未设置该环境变量。")
        pwd = str(raw).strip()
        if not pwd:
            raise RuntimeError(f"{path}: 环境变量 {name!r} 为空（password_env）。")
    else:
        password = db.get("password")
        if password is None:
            raise RuntimeError(
                f"{path}: 请设置 password，或设置 password_env 指向环境变量名。\n"
                "密码含 @ 时推荐：password: \"你的@密码\"（英文双引号），或使用 password_env。"
            )
        if not isinstance(password, (str, int)):
            raise RuntimeError(f"{path}: password 必须是字符串或数字，当前类型为 {type(password).__name__}")
        pwd = str(password).strip()
        if not pwd:
            raise RuntimeError(
                f"{path}: password 为空。MySQL 会报 (using password: NO)。\n"
                "若含 @ & ! # 等，请用英文双引号包住整段，例如：password: \"p@ss&word\"\n"
                "或改用 password_env，在系统/终端里设置环境变量后再运行。"
            )
    return {
        "host": str(host),
        "port": int(port),
        "user": str(user),
        "password": pwd,
        "database": str(database),
        "charset": "utf8mb4",
        "cursorclass": DictCursor,
    }


def connect(**kwargs: Any) -> Any:
    if str(kwargs.pop("driver", "")).lower() == "sqlite":
        db_path = kwargs.pop("path")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn
    try:
        return pymysql.connect(**kwargs)
    except pymysql_err.OperationalError as e:
        errno, msg = e.args[0], str(e.args[1]) if len(e.args) > 1 else ""
        if errno == 1045 and "using password: NO" in msg:
            raise RuntimeError(
                "数据库拒绝连接：客户端未发送密码（与空 password 等价）。\n"
                "请检查 config.yaml 里 password 是否非空；密码务必写在对应缩进块内，并建议用双引号包裹。"
            ) from e
        raise


def _sql_for_sqlite(sql: str) -> str:
    return sql.replace("`", "").replace("%s", "?")


def fetch_all(conn: Any, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    if isinstance(conn, sqlite3.Connection):
        cur = conn.execute(_sql_for_sqlite(sql), params)
        return [dict(row) for row in cur.fetchall()]
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return list(rows) if isinstance(rows, list) else []


def load_pg_config(doc: Mapping[str, Any]) -> dict[str, Any] | None:
    pg = doc.get("pg")
    if not isinstance(pg, dict):
        return None
    host = config_value(pg, "host")
    port = config_value(pg, "port")
    user = config_value(pg, "user")
    password = config_value(pg, "password")
    database = config_value(pg, "database")
    if not host or port is None or not user or database is None:
        return None
    return {
        "host": str(host),
        "port": int(port),
        "user": str(user),
        "password": str(password or ""),
        "database": str(database),
    }
