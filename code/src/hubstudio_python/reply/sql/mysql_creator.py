"""``influencer_platform.us_region_creator`` 通用 MySQL 读写。"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Iterator

from hubstudio_python.reply.sql.mysql_config import MysqlConfig, mysql_config_from_env

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _qident(name: str) -> str:
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"invalid SQL identifier: {name!r}")
    return f"`{name}`"


@contextmanager
def mysql_connection(config: MysqlConfig | None = None) -> Iterator[Any]:
    import pymysql

    cfg = config or mysql_config_from_env()
    if not cfg.enabled:
        raise RuntimeError("MySQL is disabled (HUBSTUDIO_MYSQL_ENABLED=false)")
    if not cfg.user:
        raise RuntimeError("MySQL user not configured (HUBSTUDIO_MYSQL_USER)")
    conn = pymysql.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        database=cfg.database,
        charset="utf8mb4",
        autocommit=False,
        connect_timeout=cfg.connect_timeout,
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def resolve_mysql_creator_key(
    creator_id: str = "",
    creator_name: str = "",
) -> str:
    """MySQL 主键：``creator_id_column`` 现为 ``creators_name``，插件常只传 ``creatorName``。"""
    return str(creator_id or creator_name or "").strip()


def update_creator_columns(
    creator_id: str,
    updates: dict[str, Any],
    *,
    config: MysqlConfig | None = None,
) -> dict[str, Any]:
    cfg = config or mysql_config_from_env()
    cid = str(creator_id or "").strip()
    if not cfg.enabled:
        return {"updated": False, "reason": "mysql_disabled"}
    if not cid:
        return {"updated": False, "reason": "missing_creator_id"}
    if not updates:
        return {"updated": False, "reason": "empty_updates"}

    table = _qident(cfg.creator_table)
    id_col = _qident(cfg.creator_id_column)
    set_parts = []
    values: list[Any] = []
    for col, val in updates.items():
        set_parts.append(f"{_qident(col)} = %s")
        values.append(val)
    values.append(cid)
    sql = f"UPDATE {table} SET {', '.join(set_parts)} WHERE {id_col} = %s"

    with mysql_connection(cfg) as conn:
        with conn.cursor() as cur:
            affected = cur.execute(sql, tuple(values))
    return {
        "updated": affected > 0,
        "creator_id": cid,
        "columns": dict(updates),
        "table": cfg.creator_table,
        "database": cfg.database,
        "rows_affected": affected,
    }


def update_join_wa(
    creator_id: str,
    join_wa: int,
    *,
    config: MysqlConfig | None = None,
) -> dict[str, Any]:
    cfg = config or mysql_config_from_env()
    return update_creator_columns(
        creator_id,
        {cfg.join_wa_column: int(join_wa)},
        config=cfg,
    )


def get_negotiation_round(
    creator_id: str,
    *,
    config: MysqlConfig | None = None,
) -> int | None:
    """读取 ``negotiation_rounds``；无记录或 NULL 时返回 ``None``。"""
    cfg = config or mysql_config_from_env()
    cid = str(creator_id or "").strip()
    if not cfg.enabled or not cid:
        return None

    table = _qident(cfg.creator_table)
    id_col = _qident(cfg.creator_id_column)
    round_col = _qident(cfg.negotiation_rounds_column)
    sql = f"SELECT {round_col} AS rnd FROM {table} WHERE {id_col} = %s LIMIT 1"

    with mysql_connection(cfg) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (cid,))
            row = cur.fetchone()
    if not row:
        return None
    val = row.get("rnd")
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def update_negotiation_round(
    creator_id: str,
    round_num: int,
    *,
    config: MysqlConfig | None = None,
) -> dict[str, Any]:
    cfg = config or mysql_config_from_env()
    rnd = max(0, int(round_num))
    return update_creator_columns(
        creator_id,
        {cfg.negotiation_rounds_column: rnd},
        config=cfg,
    )
