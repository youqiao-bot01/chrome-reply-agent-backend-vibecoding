from __future__ import annotations

import logging
import os
from collections import Counter
from datetime import datetime, time, timedelta
from typing import Any

import requests
import yaml
from pymysql import err as pymysql_err

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor as PgDictCursor

    _PSYCOPG2_AVAILABLE = True
except ImportError:
    _PSYCOPG2_AVAILABLE = False

from hubstudio_python.feishu.config import (
    config_value,
    connect,
    default_config_file,
    fetch_all,
    load_db_config_from_yaml_file,
    load_pg_config,
)
from hubstudio_python.feishu.interface.bitable import (
    build_week_bitable_url_from_doc,
    feishu_bitable_report_bounds,
    feishu_tenant_access_token,
    parse_bitable_write_config,
    sync_daily_report_to_bitable,
)
from hubstudio_python.feishu.interface.dedup_cleanup import run_dedup_cleanup
from hubstudio_python.feishu.service.history_builder import build_creator_full_history

logger = logging.getLogger(__name__)

_TABLES_WITH_CREATE_TIME = {
    "auto_reply_plugin_ai_reply_info",
    "auto_reply_plugin_keywords_reply_info",
}


def evening_report_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """日报统计：昨日 16:30 ～ 现在（含）。"""
    end = now or datetime.now()
    start = datetime.combine((end - timedelta(days=1)).date(), time(16, 30))
    return start, end


def count_rows_by_create_time_range(
    conn: Any,
    table: str,
    start_inclusive: datetime,
    end_inclusive: datetime,
) -> int:
    if table not in _TABLES_WITH_CREATE_TIME:
        raise ValueError(f"unsupported table: {table}")
    base = f"SELECT COUNT(*) AS cnt FROM `{table}` WHERE create_time >= %s AND create_time <= %s"
    params = (start_inclusive, end_inclusive)
    if table == "auto_reply_plugin_ai_reply_info":
        sql = (
            base
            + " AND source IS NOT NULL AND TRIM(source) <> ''"
            + " AND creator_name IS NOT NULL AND TRIM(creator_name) <> ''"
        )
        rows = fetch_all(conn, sql, params)
    elif table == "auto_reply_plugin_keywords_reply_info":
        try:
            sql = (
                base
                + " AND source IS NOT NULL AND TRIM(source) <> ''"
                + " AND creator_name IS NOT NULL AND TRIM(creator_name) <> ''"
            )
            rows = fetch_all(conn, sql, params)
        except pymysql_err.OperationalError:
            sql = base + " AND source IS NOT NULL AND TRIM(source) <> ''"
            rows = fetch_all(conn, sql, params)
    else:
        rows = fetch_all(conn, base, params)
    if not rows:
        return 0
    return int(rows[0].get("cnt") or 0)


def count_ai_reply_evening_window(conn: Any) -> int:
    start, end = evening_report_bounds()
    return count_rows_by_create_time_range(conn, "auto_reply_plugin_ai_reply_info", start, end)


def count_keywords_reply_evening_window(conn: Any) -> int:
    start, end = evening_report_bounds()
    return count_rows_by_create_time_range(conn, "auto_reply_plugin_keywords_reply_info", start, end)


def count_by_source_in_time_range(
    conn: Any,
    table: str,
    start_inclusive: datetime,
    end_inclusive: datetime,
) -> list[tuple[str, int]]:
    if table not in _TABLES_WITH_CREATE_TIME:
        raise ValueError(f"unsupported table for source count: {table}")
    base = (
        f"SELECT `source` AS src, COUNT(*) AS cnt FROM `{table}` "
        "WHERE create_time >= %s AND create_time <= %s "
    )
    params = (start_inclusive, end_inclusive)
    if table == "auto_reply_plugin_ai_reply_info":
        sql = (
            base
            + "AND source IS NOT NULL AND TRIM(source) <> '' "
            + "AND creator_name IS NOT NULL AND TRIM(creator_name) <> '' "
            + "GROUP BY `source` ORDER BY cnt DESC"
        )
        rows = fetch_all(conn, sql, params)
    else:
        try:
            sql = base + "AND source IS NOT NULL AND TRIM(source) <> '' GROUP BY `source` ORDER BY cnt DESC"
            rows = fetch_all(conn, sql, params)
        except pymysql_err.OperationalError:
            sql = base + "GROUP BY `source` ORDER BY cnt DESC"
            rows = fetch_all(conn, sql, params)
    out: list[tuple[str, int]] = []
    for row in rows:
        raw = row.get("src")
        label = "(空)" if raw is None else str(raw).strip() or "(空)"
        out.append((label, int(row["cnt"])))
    return out


def count_by_key_words_in_time_range(
    conn: Any,
    start_inclusive: datetime,
    end_inclusive: datetime,
    *,
    table: str = "auto_reply_plugin_keywords_reply_info",
) -> list[tuple[str, int]]:
    sql = (
        "SELECT `key_words` AS kw, COUNT(*) AS cnt "
        f"FROM `{table}` "
        "WHERE create_time >= %s AND create_time <= %s "
        "GROUP BY `key_words` ORDER BY cnt DESC"
    )
    rows = fetch_all(conn, sql, (start_inclusive, end_inclusive))
    out: list[tuple[str, int]] = []
    for row in rows:
        raw = row.get("kw")
        label = "(空)" if raw is None else str(raw).strip() or "(空)"
        out.append((label, int(row["cnt"])))
    return out


def merge_source_counts(
    ai_by_source: list[tuple[str, int]],
    kw_by_source: list[tuple[str, int]],
) -> list[tuple[str, int]]:
    c: Counter[str] = Counter()
    for s, n in ai_by_source:
        c[s] += n
    for s, n in kw_by_source:
        c[s] += n
    return sorted(c.items(), key=lambda x: (-x[1], x[0]))


def format_keyword_reply_line(kw_n: int, by_key_words: list[tuple[str, int]]) -> tuple[str, str]:
    if by_key_words:
        inner = "； ".join(f"【{name}】触发：{n}人" for name, n in by_key_words)
    else:
        inner = "无"
    plain = f"关键词回复次数：{kw_n}次（其中 {inner}）"
    md = f"**关键词回复次数：** {kw_n}次（其中 {inner}）"
    return md, plain


def format_talent_inquiry_line(
    ai_by_source: list[tuple[str, int]],
    kw_by_source: list[tuple[str, int]],
) -> tuple[str, str]:
    merged = merge_source_counts(ai_by_source, kw_by_source)
    total = sum(n for _, n in merged)
    if merged:
        inner = "，".join(f"{name}：{n}人" for name, n in merged)
    else:
        inner = "无"
    plain = f"达人问询数：{total}个人（{inner}）"
    md = f"**达人问询数：** {total}个人（{inner}）"
    return md, plain


def format_reply_log_line(url: str) -> tuple[str, str]:
    u = url.strip()
    if not u:
        return "", ""
    plain = f"问题回复log：{u}"
    md = f"**问题回复log：** 👉 [飞书多维表格]({u})"
    return plain, md


def send_daily_report_interactive_webhook(
    *,
    webhook_url: str,
    body_markdown: str,
    title: str = "自动巡航日报",
) -> bool:
    url = webhook_url.strip()
    if not url:
        logger.warning("飞书 webhook_url 为空，跳过发送")
        return False
    headers = {"Content-Type": "application/json"}
    md = body_markdown.strip()
    body = {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "config": {"update_multi": True},
            "body": {
                "direction": "vertical",
                "padding": "12px 12px 12px 12px",
                "elements": [
                    {
                        "tag": "markdown",
                        "content": md,
                        "text_align": "left",
                        "text_size": "normal_v2",
                        "margin": "0px 0px 0px 0px",
                    }
                ],
            },
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "subtitle": {"tag": "plain_text", "content": ""},
                "template": "blue",
                "padding": "12px 12px 12px 12px",
            },
        },
    }
    try:
        response = requests.post(url, headers=headers, json=body, timeout=30)
    except requests.RequestException as exc:
        logger.error("飞书 webhook 请求异常: %s", exc)
        return False
    if response.status_code != 200:
        logger.error("飞书 webhook HTTP %s: %s", response.status_code, response.text)
        return False
    try:
        data = response.json()
    except ValueError:
        logger.error("飞书 webhook 响应非 JSON: %s", response.text)
        return False
    ok = data.get("code") == 0 or data.get("StatusCode") == 0
    if ok:
        logger.info("飞书消息发送成功")
        return True
    logger.error("飞书消息发送失败: %s", response.text)
    return False


def fetch_ai_reply_rows_for_bitable(
    conn: Any,
    start_inclusive: datetime,
    end_inclusive: datetime,
    pg_conn: Any = None,
) -> list[dict[str, Any]]:
    base_sql = (
        "FROM `auto_reply_plugin_ai_reply_info` "
        "WHERE create_time >= %s AND create_time <= %s "
        "AND source IS NOT NULL AND TRIM(source) <> '' "
        "AND creator_name IS NOT NULL AND TRIM(creator_name) <> '' "
        "ORDER BY create_time DESC"
    )
    params = (start_inclusive, end_inclusive)
    selects = [
        ["creator_name", "source", "create_time", "message_info", "reply_result"],
        ["creator_name", "source", "create_time", "reply_result"],
        ["creator_name", "source", "create_time", "message_info"],
        ["creator_name", "source", "create_time"],
        ["source", "create_time"],
        ["create_time"],
    ]
    last_exc: Exception | None = None
    rows: list[dict[str, Any]] = []
    for cols in selects:
        try:
            sql = "SELECT " + ", ".join(cols) + " " + base_sql
            rows = fetch_all(conn, sql, params)
            for r in rows:
                r.setdefault("creator_name", "")
                r.setdefault("create_time", "")
                r.setdefault("source", "")
                r.setdefault("message_info", "")
                r.setdefault("reply_result", "")
            break
        except pymysql_err.OperationalError as exc:
            last_exc = exc
            continue
    if last_exc and not rows:
        raise last_exc

    history_cache: dict[str, str] = {}
    for r in rows:
        creator_name = str(r.get("creator_name") or "").strip()
        source = str(r.get("source") or "").strip()
        if not creator_name:
            r["full_history"] = ""
            continue
        cache_key = creator_name
        if cache_key not in history_cache:
            try:
                history = build_creator_full_history(conn, creator_name, source, pg_conn=pg_conn)
                history_cache[cache_key] = history
            except Exception as exc:
                logger.warning("构建达人历史记录失败 creator=%s: %s", creator_name, exc)
                history_cache[cache_key] = ""
        r["full_history"] = history_cache[cache_key]

    return rows


def _feishu_webhook_from_doc(doc: dict[str, Any]) -> str:
    fei = doc.get("feishu")
    if isinstance(fei, dict):
        u = fei.get("webhook_url")
        if isinstance(u, str):
            return u.strip()
    return ""


def main(webhook_urls: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    config_file = default_config_file()
    if not config_file.is_file():
        raise SystemExit(
            f"缺少 {config_file}。\n"
            "在 code/ 目录准备 config.yaml（可参考源仓库 config.yaml）。"
        )

    with config_file.open(encoding="utf-8") as f:
        yaml_doc = yaml.safe_load(f)
    if not isinstance(yaml_doc, dict):
        raise SystemExit(f"{config_file}: YAML 根节点必须是映射（键值对象）")

    db_cfg = load_db_config_from_yaml_file(config_file)
    conn = connect(**db_cfg)

    try:
        if str(db_cfg.get("driver", "")).lower() != "sqlite":
            run_dedup_cleanup(config_path=config_file, dry_run=False)
            logger.info("数据库去重完成")
        else:
            logger.info("跳过数据库去重（sqlite 测试库）")
    except Exception as exc:
        logger.warning("数据库去重失败（已跳过，不影响日报）: %s", exc)

    pg_conn = None
    if _PSYCOPG2_AVAILABLE:
        pg_cfg = load_pg_config(yaml_doc)
        if pg_cfg:
            try:
                pg_conn = psycopg2.connect(
                    host=pg_cfg["host"],
                    port=pg_cfg["port"],
                    user=pg_cfg["user"],
                    password=pg_cfg["password"],
                    database=pg_cfg["database"],
                    cursor_factory=PgDictCursor,
                    connect_timeout=10,
                )
                logger.info("PostgreSQL 连接成功，CP 行为数据将被包含")
            except Exception as exc:
                logger.warning("PostgreSQL 连接失败（CP 行为将被跳过）: %s", exc)
                pg_conn = None
    else:
        logger.info("psycopg2 未安装，CP 行为数据将被跳过")
    try:
        start, end = evening_report_bounds()
        ai_n = count_ai_reply_evening_window(conn)
        kw_n = count_keywords_reply_evening_window(conn)
        ai_by_src = count_by_source_in_time_range(conn, "auto_reply_plugin_ai_reply_info", start, end)
        kw_by_src = count_by_source_in_time_range(conn, "auto_reply_plugin_keywords_reply_info", start, end)
        kw_by_key = count_by_key_words_in_time_range(conn, start, end)
        kw_line_md, kw_line_plain = format_keyword_reply_line(kw_n, kw_by_key)
        talent_md, talent_text = format_talent_inquiry_line(ai_by_src, kw_by_src)

        start_end = f"{start:%Y-%m-%d %H:%M} ~ {end:%Y-%m-%d %H:%M}"
        plain_lines = [
            f"统计区间：{start_end}",
            talent_text,
            f"AI回复次数：{ai_n}次；",
            kw_line_plain,
        ]
        md_lines = [
            f"**统计区间：** {start_end}",
            talent_md,
            f"**AI回复次数：** {ai_n}次；",
            kw_line_md,
        ]

        bitable_cfg = parse_bitable_write_config(yaml_doc, config_file)
        if bitable_cfg:
            try:
                tt = feishu_tenant_access_token(app_id=bitable_cfg.app_id, app_secret=bitable_cfg.app_secret)
                bitable_start, bitable_end = feishu_bitable_report_bounds()
                logger.info("多维表格写入区间（周日 16:30～现在）: %s ~ %s", bitable_start, bitable_end)
                sync_daily_report_to_bitable(
                    bitable_cfg,
                    tt,
                    conn=conn,
                    start_inclusive=bitable_start,
                    end_inclusive=bitable_end,
                    fetch_rows_func=lambda c, s, e: fetch_ai_reply_rows_for_bitable(c, s, e, pg_conn=pg_conn),
                )
            except (OSError, RuntimeError, requests.RequestException) as exc:
                logger.error("多维表格写入失败（已跳过，不影响控制台与群消息）: %s", exc)

        reply_log_url = build_week_bitable_url_from_doc(yaml_doc)
        log_plain, log_md = format_reply_log_line(reply_log_url)
        if log_plain:
            plain_lines.append(log_plain)
        if log_md:
            md_lines.append(log_md)

        report = "\n".join(plain_lines) + "\n"
        body_markdown = "\n".join(md_lines)
        print(report)

        hooks: list[str]
        if webhook_urls is not None:
            hooks = webhook_urls
        else:
            env_hook = os.environ.get("FEISHU_WEBHOOK_URL")
            if isinstance(env_hook, str) and env_hook.strip():
                hooks = [env_hook.strip()]
            else:
                h = _feishu_webhook_from_doc(yaml_doc)
                hooks = [h] if h else []

        if hooks:
            for hook_url in hooks:
                ok = send_daily_report_interactive_webhook(webhook_url=hook_url, body_markdown=body_markdown)
                if not ok:
                    logger.warning("飞书推送失败（已跳过该 webhook）: %s", hook_url)
        else:
            logger.info("未配置 webhook_url，已跳过飞书推送")
    finally:
        conn.close()
        if pg_conn is not None:
            pg_conn.close()
