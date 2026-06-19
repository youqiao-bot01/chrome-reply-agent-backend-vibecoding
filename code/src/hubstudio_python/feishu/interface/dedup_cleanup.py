"""
清理 MySQL：按“达人发言次数”全表去重（最终口径）。

去重规则：
- 同一店铺 source
- 同一达人 creator_name
- 同一“达人发言次数”（从 message_info 里统计 `[creator` 出现次数）
=> 只保留最早一条，其余全部删除（不考虑时间；AI 回复为空也照样参与）
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pymysql

from hubstudio_python.feishu.config import default_config_file, load_db_config_from_yaml_file

logger = logging.getLogger(__name__)

TABLE = "auto_reply_plugin_ai_reply_info"


def _detect_id_column(conn: pymysql.connections.Connection, table: str) -> str:
    with conn.cursor() as cur:
        cur.execute(f"SHOW COLUMNS FROM `{table}`")
        cols = [r["Field"] for r in cur.fetchall()]
    for cand in ("id", "ID", "row_id", "rowid", "uid"):
        if cand in cols:
            return cand
    with conn.cursor() as cur:
        cur.execute(f"SHOW KEYS FROM `{table}` WHERE Key_name = 'PRIMARY'")
        rows = cur.fetchall()
    if rows:
        col = rows[0].get("Column_name")
        if isinstance(col, str) and col:
            return col
    raise RuntimeError(f"无法识别 {table} 的主键列（用于删除）。现有列：{cols}")


def _to_dt(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace(" ", "T"))
    except Exception:
        return None


def _normalize_history_message_text(message_info: Any) -> str:
    if message_info is None:
        return ""
    if isinstance(message_info, dict):
        d = message_info
    else:
        s = str(message_info).strip()
        if not s:
            return ""
        if s.startswith("{") and s.endswith("}"):
            try:
                parsed = json.loads(s)
                d = parsed if isinstance(parsed, dict) else None
            except Exception:
                d = None
        else:
            d = None
        if d is None:
            return s

    def _k_sort(k: Any) -> tuple[int, str]:
        try:
            return (0, f"{int(k):012d}")
        except Exception:
            return (1, str(k))

    parts: list[str] = []
    for k in sorted(d.keys(), key=_k_sort):
        v = d.get(k)
        if v is None:
            continue
        t = str(v).strip()
        if t:
            parts.append(t)
    return "\n".join(parts).strip()


def run_dedup_cleanup(
    *,
    config_path: str | Path | None = None,
    dry_run: bool = False,
    batch: int = 500,
) -> dict[str, int]:
    config_file = Path(config_path) if config_path else default_config_file()
    if not config_file.is_file():
        raise SystemExit(f"缺少 {config_file}")

    batch = max(1, int(batch))
    db_cfg = load_db_config_from_yaml_file(config_file)
    conn = pymysql.connect(**db_cfg)
    try:
        id_col = _detect_id_column(conn, TABLE)
        logger.info(
            "目标表=%s 主键列=%s 去重键=(source,creator_name,creator_cnt) dry_run=%s",
            TABLE,
            id_col,
            dry_run,
        )

        order_by = "ORDER BY source ASC, creator_name ASC, create_time ASC"
        sql = (
            f"SELECT `{id_col}` AS _id, source, creator_name, reply_result, message_info, create_time "
            f"FROM `{TABLE}` "
            "WHERE source IS NOT NULL AND TRIM(source) <> '' "
            "AND creator_name IS NOT NULL AND TRIM(creator_name) <> '' "
            f"{order_by}"
        )
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()

        last_seen: dict[tuple[str, str, int], tuple[datetime, Any]] = {}
        to_delete: list[Any] = []
        kept = 0
        scanned = 0

        for r in rows:
            scanned += 1
            _id = r.get("_id")
            source = str(r.get("source") or "").strip()
            creator = str(r.get("creator_name") or "").strip()
            dt = _to_dt(r.get("create_time"))
            if not source or not creator or dt is None:
                continue

            mi = r.get("message_info")
            creator_cnt = _normalize_history_message_text(mi).lower().count("[creator")
            key = (source, creator, int(creator_cnt))
            prev = last_seen.get(key)
            if prev is None:
                last_seen[key] = (dt, _id)
                kept += 1
                continue
            to_delete.append(_id)

        logger.info("扫描行数=%s 保留(锚点)=%s 需删除重复=%s", scanned, kept, len(to_delete))
        deleted = 0
        if not dry_run and to_delete:
            with conn.cursor() as cur:
                for i in range(0, len(to_delete), batch):
                    chunk = to_delete[i : i + batch]
                    placeholders = ",".join(["%s"] * len(chunk))
                    cur.execute(f"DELETE FROM `{TABLE}` WHERE `{id_col}` IN ({placeholders})", chunk)
                    deleted += cur.rowcount
                conn.commit()
            logger.info("删除完成：deleted=%s", deleted)
        return {"scanned": scanned, "kept": kept, "to_delete": len(to_delete), "deleted": deleted}
    finally:
        conn.close()


def main(*, dry_run: bool = False, batch: int = 500, config_path: str | Path | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    run_dedup_cleanup(config_path=config_path, dry_run=dry_run, batch=batch)
