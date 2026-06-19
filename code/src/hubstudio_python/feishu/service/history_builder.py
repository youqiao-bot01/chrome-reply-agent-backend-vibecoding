"""
达人完整行为历史记录构建模块。

从多张数据库表查询达人的所有行为，按时间顺序拼接成一条完整的历史记录文本。

支持的行为类型：
- 消息回复（AI回复 / 关键词回复）
- TTO 签约
- CAP 签约
- 加窗
- 申样
- TAP 发布视频
- TTO 发布视频
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 时间格式化工具
# ──────────────────────────────────────────────

def _fmt_dt(value: Any) -> str:
    """把各种时间格式统一转成 YYYY-MM-DD HH:MM:SS 字符串。"""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, (int, float)):
        # Unix 时间戳（秒）
        try:
            return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return str(value)
    s = str(value).strip()
    return s


def _parse_dt(value: Any) -> datetime | None:
    """把各种时间格式解析成 datetime 对象，用于排序。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(int(value))
        except Exception:
            return None
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _fetch_all(conn: pymysql.connections.Connection, sql: str, params: tuple) -> list[dict[str, Any]]:
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return list(rows) if rows else []
    except Exception as exc:
        logger.warning("SQL 查询失败（已跳过）: %s | 错误: %s", sql[:120], exc)
        return []


# ──────────────────────────────────────────────
# 各行为查询函数
# ──────────────────────────────────────────────

def fetch_reply_events(
    conn: pymysql.connections.Connection,
    creator_name: str,
    source: str,
) -> list[dict[str, Any]]:
    """查询 AI 回复和关键词回复记录。"""
    events: list[dict[str, Any]] = []

    # AI 回复
    sql_ai = """
        SELECT create_time, source, reply_result
        FROM controlpastmessagesdata.auto_reply_plugin_ai_reply_info
        WHERE creator_name = %s
        ORDER BY create_time ASC
    """
    for row in _fetch_all(conn, sql_ai, (creator_name,)):
        t = _fmt_dt(row.get("create_time"))
        src = str(row.get("source") or "").strip()
        reply = str(row.get("reply_result") or "").strip()
        reply_short = reply
        events.append({
            "dt": _parse_dt(row.get("create_time")),
            "text": f"[{t}] 在 {src} 收到AI回复：{reply_short}",
        })

    # 关键词回复
    sql_kw = """
        SELECT create_time, source, key_words, reply_text
        FROM controlpastmessagesdata.auto_reply_plugin_keywords_reply_info
        WHERE creator_name = %s
        ORDER BY create_time ASC
    """
    for row in _fetch_all(conn, sql_kw, (creator_name,)):
        t = _fmt_dt(row.get("create_time"))
        src = str(row.get("source") or "").strip()
        kw = str(row.get("key_words") or "").strip()
        events.append({
            "dt": _parse_dt(row.get("create_time")),
            "text": f"[{t}] 在 {src} 触发关键词回复【{kw}】",
        })

    return events


def fetch_tto_signup_events(
    conn: pymysql.connections.Connection,
    creator_name: str,
) -> list[dict[str, Any]]:
    """查询 TTO 签约记录。"""
    sql = """
        SELECT handle_name, established_time
        FROM controlpastmessagesdata.tto_tiktok_creator_relation
        WHERE CONVERT(handle_name USING utf8mb4) COLLATE utf8mb4_unicode_520_ci
            = CONVERT(%s USING utf8mb4) COLLATE utf8mb4_unicode_520_ci
        ORDER BY established_time ASC
    """
    events: list[dict[str, Any]] = []
    for row in _fetch_all(conn, sql, (creator_name,)):
        t = _fmt_dt(row.get("established_time"))
        if not t:
            continue
        events.append({
            "dt": _parse_dt(row.get("established_time")),
            "text": f"[{t}] 达人在TTO接受签约",
        })
    return events


def fetch_cap_signup_events(
    conn: pymysql.connections.Connection,
    creator_name: str,
) -> list[dict[str, Any]]:
    """查询 CAP 签约记录。"""
    sql = """
        SELECT creator_info_user_name, effective_start_date_second, effective_end_date_second, status
        FROM controlpastmessagesdata.linked_data
        WHERE CONVERT(creator_info_user_name USING utf8mb4) COLLATE utf8mb4_unicode_520_ci
            = CONVERT(%s USING utf8mb4) COLLATE utf8mb4_unicode_520_ci
        ORDER BY effective_start_date_second ASC
    """
    events: list[dict[str, Any]] = []
    for row in _fetch_all(conn, sql, (creator_name,)):
        t = _fmt_dt(row.get("effective_start_date_second"))
        end_t = _fmt_dt(row.get("effective_end_date_second"))
        if not t:
            continue
        expire_text = f"，到期时间：{end_t}" if end_t else ""
        events.append({
            "dt": _parse_dt(row.get("effective_start_date_second")),
            "text": f"[{t}] 达人在CAP接受签约{expire_text}",
        })
    return events


def fetch_showcase_events(
    conn: pymysql.connections.Connection,
    creator_name: str,
) -> list[dict[str, Any]]:
    """查询加窗和申样记录。"""
    # 先通过 user_name 查 oec_id
    sql_user = """
        SELECT DISTINCT oec_id
        FROM influencer_platform.creators_showcase
        WHERE CONVERT(user_name USING utf8mb4) COLLATE utf8mb4_unicode_520_ci
            = CONVERT(%s USING utf8mb4) COLLATE utf8mb4_unicode_520_ci
        LIMIT 1
    """
    user_rows = _fetch_all(conn, sql_user, (creator_name,))
    if not user_rows:
        return []
    oec_id = user_rows[0].get("oec_id")
    if not oec_id:
        return []

    sql = """
        SELECT
            cs.user_name,
            cs.effective_start_time,
            cs.order_expired_time,
            cs.sample_status,
            cs.free_sample_status,
            cs.campaign_id,
            COALESCE(pm.`简称`, cs.campaign_id) AS product_name,
            cs.mcn_txt AS shop_name
        FROM influencer_platform.creators_showcase cs
        LEFT JOIN influencer_platform.`产品映射` pm ON cs.campaign_id = pm.campaign_id
        WHERE cs.oec_id = %s
        ORDER BY cs.effective_start_time ASC
    """
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in _fetch_all(conn, sql, (oec_id,)):
        shop = str(row.get("shop_name") or "").strip()
        product = str(row.get("product_name") or row.get("campaign_id") or "").strip()
        product_short = product
        add_time = row.get("effective_start_time")
        if add_time:
            t = _fmt_dt(add_time)
            add_key = f"add_{t}_{product_short}"
            if add_key not in seen:
                seen.add(add_key)
                events.append({
                    "dt": _parse_dt(add_time),
                    "text": f"[{t}] 达人在{shop}加窗{product_short}",
                })

        # 申样事件
        sample_status = str(row.get("free_sample_status") or "").strip().lower()
        sample_time = row.get("order_expired_time")
        if sample_status and sample_status not in ("not requested", ""):
            # 申样时间优先用 order_expired_time，没有则用 effective_start_time
            ref_time = sample_time if sample_time else add_time
            if not ref_time:
                continue
            t = _fmt_dt(ref_time)
            sample_key = f"sample_{t}_{product_short}_{sample_status}"
            if sample_key not in seen:
                seen.add(sample_key)
                events.append({
                    "dt": _parse_dt(ref_time),
                    "text": f"[{t}] 达人在{shop}申样{product_short}（状态：{sample_status}）",
                })

    return events


def fetch_tap_video_events(
    conn: pymysql.connections.Connection,
    creator_name: str,
) -> list[dict[str, Any]]:
    """查询 TAP 发布视频记录。"""
    # 先通过 user_name 查 user_uid
    sql_uid = """
        SELECT DISTINCT user_uid
        FROM influencer_platform.messages_monitoring
        WHERE CONVERT(user_name USING utf8mb4) COLLATE utf8mb4_unicode_520_ci
            = CONVERT(%s USING utf8mb4) COLLATE utf8mb4_unicode_520_ci
        LIMIT 1
    """
    uid_rows = _fetch_all(conn, sql_uid, (creator_name,))
    if not uid_rows:
        return []
    user_uid = uid_rows[0].get("user_uid")
    if not user_uid:
        return []

    sql = """
        SELECT
            vm.video_create_time,
            mm.user_name,
            COALESCE(pm.`简称`, vm.product_id) AS product_name,
            mm.campaign_id
        FROM influencer_platform.videos_monitoring vm
        JOIN influencer_platform.messages_monitoring mm
            ON vm.user_uid = mm.user_uid AND vm.campaign_id = mm.campaign_id
        LEFT JOIN influencer_platform.`产品映射` pm ON vm.product_id = pm.campaign_id
        WHERE vm.user_uid = %s
        ORDER BY vm.video_create_time ASC
    """
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in _fetch_all(conn, sql, (user_uid,)):
        video_time = row.get("video_create_time")
        if not video_time:
            continue
        product = str(row.get("product_name") or "").strip()
        product_short = product
        t = _fmt_dt(video_time)
        key = f"{t}_{product_short}"
        if key in seen:
            continue
        seen.add(key)
        events.append({
            "dt": _parse_dt(video_time),
            "text": f"[{t}] 达人在TAP发布视频，挂链商品{product_short}",
        })
    return events


def fetch_tto_video_events(
    conn: pymysql.connections.Connection,
    creator_name: str,
) -> list[dict[str, Any]]:
    """查询 TTO 发布视频记录。"""
    # 先通过 handle_name 查 aio_creator_id
    sql_id = """
        SELECT aio_creator_id
        FROM tto_platform.tto_creator
        WHERE CONVERT(handle_name USING utf8mb4) COLLATE utf8mb4_unicode_520_ci
            = CONVERT(%s USING utf8mb4) COLLATE utf8mb4_unicode_520_ci
        LIMIT 1
    """
    id_rows = _fetch_all(conn, sql_id, (creator_name,))
    if not id_rows:
        return []
    aio_creator_id = id_rows[0].get("aio_creator_id")
    if not aio_creator_id:
        return []

    sql = """
        SELECT
            tv.create_time AS video_create_time,
            tv.campaign_id,
            tc.handle_name
        FROM tto_platform.tto_video tv
        JOIN tto_platform.tto_creator tc ON tv.aio_creator_id = tc.aio_creator_id
        WHERE tv.aio_creator_id = %s
        ORDER BY tv.create_time ASC
    """
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in _fetch_all(conn, sql, (aio_creator_id,)):
        video_time = row.get("video_create_time")
        if not video_time:
            continue
        campaign = str(row.get("campaign_id") or "").strip()
        t = _fmt_dt(video_time)
        key = f"{t}_{campaign}"
        if key in seen:
            continue
        seen.add(key)
        events.append({
            "dt": _parse_dt(video_time),
            "text": f"[{t}] 达人在TTO发布视频，Campaign {campaign}",
        })
    return events


# ──────────────────────────────────────────────
# PostgreSQL：Creator Portal 行为查询
# ──────────────────────────────────────────────

def _fetch_all_pg(pg_conn: Any, sql: str, params: tuple) -> list[dict[str, Any]]:
    """PostgreSQL 查询封装。"""
    try:
        with pg_conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
        return [dict(zip(cols, row)) for row in rows]
    except Exception as exc:
        logger.warning("PG SQL 查询失败（已跳过）: %s | 错误: %s", sql[:120], exc)
        return []


def fetch_cp_events(pg_conn: Any, creator_name: str) -> list[dict[str, Any]]:
    """
    查询 Creator Portal 行为：搜索、注册、登录。
    数据来源：PostgreSQL platform.tto_market.video_users
    匹配字段：creator_name（TikTok 用户名）
    """
    if pg_conn is None:
        return []

    sql = """
        SELECT
            creator_name,
            created_at,
            paypal_account,
            last_login_at
        FROM tto_market.video_users
        WHERE creator_name = %s
          AND is_deleted = false
        ORDER BY created_at ASC
        LIMIT 10
    """
    events: list[dict[str, Any]] = []
    for row in _fetch_all_pg(pg_conn, sql, (creator_name,)):
        created_at = row.get("created_at")
        paypal = str(row.get("paypal_account") or "").strip()
        last_login = row.get("last_login_at")

        # CP 搜索：有记录即表示已搜索，时间用 created_at
        if created_at:
            t = _fmt_dt(created_at)
            events.append({
                "dt": _parse_dt(created_at),
                "text": f"[{t}] 达人在CP搜索自己名称",
            })

        # CP 注册：paypal_account 不为空表示已完成注册
        if paypal and created_at:
            t = _fmt_dt(created_at)
            events.append({
                "dt": _parse_dt(created_at),
                "text": f"[{t}] 达人在CP注册",
            })

        # CP 登录：last_login_at 不为空
        if last_login:
            t = _fmt_dt(last_login)
            events.append({
                "dt": _parse_dt(last_login),
                "text": f"[{t}] 达人在CP登录",
            })

    return events


# ──────────────────────────────────────────────
# 主入口：构建完整历史记录
# ──────────────────────────────────────────────

def build_creator_full_history(
    conn: pymysql.connections.Connection,
    creator_name: str,
    source: str = "",
    pg_conn: Any = None,
) -> str:
    """
    查询达人的所有行为，按时间顺序拼接成完整历史记录文本。

    Args:
        conn: MariaDB 数据库连接
        creator_name: 达人用户名（唯一标识）
        source: 店铺名称（用于过滤消息回复）
        pg_conn: PostgreSQL 连接（可选，用于查询 CP 行为）

    Returns:
        按时间排序的完整历史记录文本，每行一条事件
    """
    if not creator_name or not creator_name.strip():
        return ""

    name = creator_name.strip()
    all_events: list[dict[str, Any]] = []

    # 收集所有行为
    all_events.extend(fetch_reply_events(conn, name, source))
    all_events.extend(fetch_tto_signup_events(conn, name))
    all_events.extend(fetch_cap_signup_events(conn, name))
    all_events.extend(fetch_showcase_events(conn, name))
    all_events.extend(fetch_tap_video_events(conn, name))
    all_events.extend(fetch_tto_video_events(conn, name))
    all_events.extend(fetch_cp_events(pg_conn, name))

    if not all_events:
        return ""

    # 按时间排序（无时间的排最后）
    def _sort_key(e: dict[str, Any]) -> tuple:
        dt = e.get("dt")
        if dt is None:
            return (1, datetime.max)
        return (0, dt)

    all_events.sort(key=_sort_key)

    lines = [e["text"] for e in all_events if e.get("text")]
    result = "\n".join(lines)

    # 附加聊天记录（分隔线隔开，保持原始格式）
    chat_lines = _fetch_chat_messages(conn, name)
    if chat_lines:
        result += "\n\n--- 聊天记录 ---\n" + chat_lines

    return result


def _fetch_chat_messages(
    conn: pymysql.connections.Connection,
    creator_name: str,
) -> str:
    """
    从 message_info 字段提取聊天记录，只保留达人发的消息和卖家/AI 的消息。
    保持原始格式，不做时间解析，直接拼接展示。
    """
    sql = """
        SELECT message_info, source, create_time
        FROM controlpastmessagesdata.auto_reply_plugin_ai_reply_info
        WHERE CONVERT(creator_name USING utf8mb4) COLLATE utf8mb4_unicode_520_ci
            = CONVERT(%s USING utf8mb4) COLLATE utf8mb4_unicode_520_ci
        ORDER BY create_time ASC
    """
    rows = _fetch_all(conn, sql, (creator_name,))
    if not rows:
        return ""

    seen_messages: set[str] = set()
    all_lines: list[str] = []

    for row in rows:
        raw = row.get("message_info") or ""
        source = str(row.get("source") or "").strip()

        # 解析 message_info（可能是 JSON 字符串或普通字符串）
        import json
        text = ""
        if isinstance(raw, str) and raw.strip().startswith("{"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    parts = []
                    for k in sorted(parsed.keys(), key=lambda x: (0, f"{int(x):012d}") if x.isdigit() else (1, x)):
                        v = parsed.get(k)
                        if v:
                            parts.append(str(v).strip())
                    text = "\n".join(parts)
            except Exception:
                text = raw
        else:
            text = str(raw).strip()

        if not text:
            continue

        # 按行提取，过滤系统消息
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            if "[system]" in line.lower():
                continue
            # 去重
            if line in seen_messages:
                continue
            seen_messages.add(line)
            all_lines.append(line)

    return "\n".join(all_lines)
