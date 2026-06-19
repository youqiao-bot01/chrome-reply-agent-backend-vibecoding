from __future__ import annotations

import ast
import csv
import json
import logging
import os
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import yaml

from hubstudio_python.feishu.config import (
    connect,
    default_config_file,
    fetch_all,
    load_db_config_from_yaml_file,
)
from hubstudio_python.feishu.interface.bitable import (
    BITABLE_BATCH_CREATE_MAX,
    BITABLE_WRITE_MAX_ROWS,
    _bitable_api_json,
    bitable_fields_from_ai_reply_row,
    bitable_batch_create_records,
    bitable_batch_delete_records,
    bitable_create_table,
    bitable_list_all_record_ids,
    bitable_list_fields,
    feishu_tenant_access_token,
    parse_bitable_write_config,
)
from hubstudio_python.feishu.service.daily_report import send_daily_report_interactive_webhook

logger = logging.getLogger(__name__)

TOOLANT_SUBMIT_METADATA_ID = 72

TOOLANT_LOG_FIELDS = [
    "回复时间",
    "店铺",
    "达人ID",
    "最后达人问询时间",
    "是否加窗",
    "是否申样",
    "gmv",
    "批样审核",
    "达人历史聊天记录汇总",
    "达人发言记录",
    "AI回复",
    "达人回复次数",
    "达人回复频率",
    "回复类型",
]

DEPRECATED_TOOLANT_LOG_FIELDS = {
    "意向度",
    "运营备注",
    "Creator Portal",
    "签约",
    "完整历史记录",
    "GMV",
    "达人消息/上下文",
    "AI回复内容",
    "意图Top1",
    "意图Top3",
    "意图置信度",
    "达人情绪",
    "命中知识标题",
    "命中知识摘要",
    "是否转人工",
    "数据库记录ID",
}


def toolant_report_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """日报统计：昨日 16:30 ～ 现在（含）。"""
    end = now or datetime.now()
    start = datetime.combine((end - timedelta(days=1)).date(), time(16, 30))
    return start, end


def _toolant_doc(doc: Mapping[str, Any]) -> dict[str, Any]:
    value = doc.get("toolant")
    return value if isinstance(value, dict) else {}


def _toolant_source(doc: Mapping[str, Any]) -> str:
    value = _toolant_doc(doc).get("source")
    return str(value).strip() if value else "toolant"


def _toolant_webhooks(doc: Mapping[str, Any], cli_urls: list[str] | None) -> list[str]:
    if cli_urls is not None:
        return [u.strip() for u in cli_urls if isinstance(u, str) and u.strip()]
    env_url = os.environ.get("TOOLANT_FEISHU_WEBHOOK_URL", "").strip()
    if env_url:
        return [env_url]
    raw = _toolant_doc(doc).get("webhook_url")
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    if isinstance(raw, list):
        return [str(u).strip() for u in raw if str(u).strip()]
    return []


def _toolant_submit_report_url(doc: Mapping[str, Any]) -> str:
    value = _toolant_doc(doc).get("submit_report_url")
    return str(value).strip() if value else ""


def _toolant_log_table_name(doc: Mapping[str, Any]) -> str:
    value = _toolant_doc(doc).get("reply_log_table_name")
    return str(value).strip() if value else "ai_reply_log_test"


def _toolant_log_table_name_prefix(doc: Mapping[str, Any]) -> str:
    value = _toolant_doc(doc).get("reply_log_table_name_prefix")
    if value:
        return str(value).strip()
    value = _toolant_doc(doc).get("reply_log_table_name")
    if value:
        return str(value).strip()
    return "Toolant自动巡航日报问题回复log"


def _toolant_reply_log_app_token(doc: Mapping[str, Any]) -> str:
    value = _toolant_doc(doc).get("reply_log_app_token")
    return str(value).strip() if value else ""


def _toolant_reply_log_url(doc: Mapping[str, Any]) -> str:
    value = _toolant_doc(doc).get("reply_log_url")
    return str(value).strip() if value else ""


def _toolant_log_week_bounds(end: datetime) -> tuple[datetime, datetime]:
    d = end.date()
    days_since_sun = (d.weekday() + 1) % 7
    week_start_date = d - timedelta(days=days_since_sun)
    week_start = datetime.combine(week_start_date, time(16, 30))
    if d.weekday() == 6 and end < week_start:
        week_start -= timedelta(days=7)
    return week_start, week_start + timedelta(days=7)


def _toolant_week_table_name(doc: Mapping[str, Any], *, week_start: datetime, week_end: datetime, part: int = 1) -> str:
    use_date_only = _toolant_reply_log_app_token(doc) or _toolant_reply_log_url(doc)
    base = f"{week_start:%Y-%m-%d}_{week_end:%Y-%m-%d}" if use_date_only else f"{week_start:%Y年%m月%d日}-{week_end:%Y年%m月%d日}"
    if not use_date_only:
        base = f"{_toolant_log_table_name_prefix(doc)} {base}"
    return base if part == 1 else f"{base}-{part}"


def _json_or_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    text = str(value).strip()
    if not text:
        return ""
    if text[0] in "[{":
        try:
            return json.loads(text)
        except Exception:
            return text
    return text


def _as_text(value: Any, *, max_chars: int | None = None) -> str:
    if value is None:
        text = ""
    elif isinstance(value, list):
        parts = [_as_text(v) for v in value]
        text = "\n".join(p for p in parts if p)
    elif isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False)
    elif isinstance(value, datetime):
        text = value.strftime("%Y-%m-%d %H:%M:%S")
    else:
        text = str(value).strip()
    if max_chars is not None and len(text) > max_chars:
        return text[:max_chars].rstrip() + "..."
    return text


def _first_text(value: Any) -> str:
    parsed = _json_or_scalar(value)
    if isinstance(parsed, list):
        return _as_text(parsed[0]) if parsed else ""
    return _as_text(parsed)


def _top3_text(value: Any) -> str:
    parsed = _json_or_scalar(value)
    if isinstance(parsed, list):
        return " / ".join(_as_text(v) for v in parsed if _as_text(v))
    return _as_text(parsed)


def _is_handoff(reply_result: Any, match_title: Any) -> str:
    text = _as_text(_json_or_scalar(reply_result)).lower()
    title = _as_text(_json_or_scalar(match_title)).lower()
    if not text or "intent_skip" in text or "intent_classifier_handoff" in title:
        return "是"
    return "否"


def _message_text(row: Mapping[str, Any]) -> str:
    return _as_text(row.get("message_input_ai") or row.get("message_info"), max_chars=3000)


def _parse_product_ids(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    text = str(value).strip()
    if not text:
        return ()
    try:
        parsed = ast.literal_eval(text)
    except Exception:
        parsed = None
    if isinstance(parsed, (list, tuple, set)):
        return tuple(str(v).strip() for v in parsed if str(v).strip())
    if parsed is not None:
        parsed_text = str(parsed).strip()
        return (parsed_text,) if parsed_text else ()
    return tuple(p.strip().strip("'\"") for p in text.split(",") if p.strip().strip("'\"() "))


def fetch_toolant_submit_scope(conn: Any, *, metadata_id: int = TOOLANT_SUBMIT_METADATA_ID) -> tuple[str, tuple[str, ...]]:
    rows = fetch_all(
        conn,
        """
            SELECT campaign_id, products_id
            FROM controlpastmessagesdata.metadata_map
            WHERE id = %s
            LIMIT 1
        """,
        (metadata_id,),
    )
    if not rows:
        logger.warning("Toolant metadata_map id=%s not found; skip showcase/sample enrichment", metadata_id)
        return "", ()
    campaign_id = str(rows[0].get("campaign_id") or "").strip()
    product_ids = _parse_product_ids(rows[0].get("products_id"))
    if not campaign_id or not product_ids:
        logger.warning("Toolant metadata_map id=%s missing campaign_id/products_id; skip showcase/sample enrichment", metadata_id)
        return "", ()
    return campaign_id, product_ids


def fetch_toolant_ai_reply_rows(
    conn: Any,
    *,
    source: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    campaign_id, product_ids = fetch_toolant_submit_scope(conn)
    showcase_join = ""
    showcase_select = """
            COALESCE(NULLIF(TRIM(urc.gmv), ''), '') AS gmv,
            '否' AS has_showcase,
            '否' AS has_sample_request,
            '' AS sample_review
    """
    params: list[Any] = []
    if campaign_id and product_ids:
        product_placeholders = ", ".join(["%s"] * len(product_ids))
        showcase_join = f"""
        LEFT JOIN (
            SELECT
                cs.user_name,
                COUNT(*) AS showcase_count,
                MAX(NULLIF(TRIM(CAST(cs.gmv AS CHAR)), '')) AS showcase_gmv,
                MAX(
                    CASE
                        WHEN (cs.order_expired_time IS NOT NULL AND cs.order_expired_time > 0)
                            OR LOWER(TRIM(COALESCE(cs.free_sample_status, ''))) NOT IN ('', 'not requested', 'none', 'null')
                        THEN 1
                        ELSE 0
                    END
                ) AS has_sample_request,
                GROUP_CONCAT(
                    DISTINCT CASE
                        WHEN TRIM(COALESCE(cs.free_sample_status, '')) <> ''
                        THEN TRIM(cs.free_sample_status)
                        ELSE NULL
                    END
                    ORDER BY TRIM(cs.free_sample_status)
                    SEPARATOR '、'
                ) AS sample_review
            FROM influencer_platform.creators_showcase cs
            WHERE cs.campaign_id = %s
                AND cs.product_id IN ({product_placeholders})
            GROUP BY cs.user_name
        ) toolant_showcase
            ON CONVERT(TRIM(toolant_showcase.user_name) USING utf8mb4) COLLATE utf8mb4_general_ci =
               CONVERT(TRIM(ai.creator_name) USING utf8mb4) COLLATE utf8mb4_general_ci
        """
        showcase_select = """
            COALESCE(
                NULLIF(TRIM(urc.gmv), ''),
                NULLIF(TRIM(CAST(toolant_showcase.showcase_gmv AS CHAR)), ''),
                ''
            ) AS gmv,
            CASE WHEN COALESCE(toolant_showcase.showcase_count, 0) > 0 THEN '是' ELSE '否' END AS has_showcase,
            CASE WHEN COALESCE(toolant_showcase.has_sample_request, 0) > 0 THEN '是' ELSE '否' END AS has_sample_request,
            COALESCE(toolant_showcase.sample_review, '') AS sample_review
        """
        params.extend([campaign_id, *product_ids])

    sql = f"""
        SELECT
            ai.id,
            ai.creator_name,
            ai.source,
            ai.create_time,
            ai.message_info,
            ai.message_input_ai,
            ai.match_content,
            ai.match_title,
            ai.match_type,
            ai.final_similarity_score,
            ai.reply_result,
            ai.emotion,
            urc.creators_name AS people_info_name,
{showcase_select}
        FROM controlpastmessagesdata.auto_reply_plugin_ai_reply_info ai
        LEFT JOIN influencer_platform.us_region_creator urc
            ON CONVERT(TRIM(urc.creators_name) USING utf8mb4) COLLATE utf8mb4_general_ci =
               CONVERT(TRIM(ai.creator_name) USING utf8mb4) COLLATE utf8mb4_general_ci
{showcase_join}
        WHERE ai.create_time >= %s
            AND ai.create_time <= %s
            AND LOWER(TRIM(ai.source)) = LOWER(TRIM(%s))
            AND ai.creator_name IS NOT NULL
            AND TRIM(ai.creator_name) <> ''
        ORDER BY ai.create_time DESC
    """
    params.extend([start, end, source])
    return fetch_all(conn, sql, tuple(params))


def fetch_toolant_keyword_counts(
    conn: Any,
    *,
    source: str,
    start: datetime,
    end: datetime,
) -> tuple[int, list[tuple[str, int]], set[str]]:
    sql = """
        SELECT
            key_words,
            COUNT(*) AS cnt,
            COUNT(DISTINCT creator_name) AS creator_cnt
        FROM controlpastmessagesdata.auto_reply_plugin_keywords_reply_info
        WHERE create_time >= %s
            AND create_time <= %s
            AND LOWER(TRIM(source)) = LOWER(TRIM(%s))
        GROUP BY key_words
        ORDER BY cnt DESC
    """
    rows = fetch_all(conn, sql, (start, end, source))
    by_keyword: list[tuple[str, int]] = []
    total = 0
    for row in rows:
        name = str(row.get("key_words") or "").strip() or "(空)"
        cnt = int(row.get("cnt") or 0)
        creator_cnt = int(row.get("creator_cnt") or 0)
        total += cnt
        by_keyword.append((name, creator_cnt))

    creator_sql = """
        SELECT DISTINCT creator_name
        FROM controlpastmessagesdata.auto_reply_plugin_keywords_reply_info
        WHERE create_time >= %s
            AND create_time <= %s
            AND LOWER(TRIM(source)) = LOWER(TRIM(%s))
            AND creator_name IS NOT NULL
            AND TRIM(creator_name) <> ''
    """
    try:
        creator_rows = fetch_all(conn, creator_sql, (start, end, source))
    except Exception:
        creator_rows = []
    creators = {str(r.get("creator_name") or "").strip() for r in creator_rows}
    creators.discard("")
    return total, by_keyword, creators


def _format_keyword_line(total: int, by_keyword: list[tuple[str, int]]) -> str:
    if not by_keyword:
        return f"**关键词回复次数：** {total}次"
    inner = "；".join(f"【{name}】触发：{cnt}人" for name, cnt in by_keyword)
    return f"**关键词回复次数：** {total}次（其中 {inner}）"


def _build_report_markdown(
    *,
    start: datetime,
    end: datetime,
    ai_rows: list[dict[str, Any]],
    keyword_total: int,
    keyword_counts: list[tuple[str, int]],
    keyword_creators: set[str],
    reply_log_url: str,
    submit_report_url: str,
) -> str:
    ai_creators = {str(r.get("creator_name") or "").strip() for r in ai_rows}
    ai_creators.discard("")
    all_creators = ai_creators | keyword_creators
    handoff_count = sum(1 for row in ai_rows if _is_handoff(row.get("reply_result"), row.get("match_title")) == "是")
    ai_reply_count = max(len(ai_rows) - handoff_count, 0)
    total_process_count = len(ai_rows) + keyword_total
    lines = [
        f"**统计区间：** {start:%Y-%m-%d %H:%M} ~ {end:%Y-%m-%d %H:%M}",
        f"**回复达人人数：** {len(all_creators)}个人",
        f"**总回复次数：** {total_process_count}次",
        f"**AI回复次数：** {ai_reply_count}次；",

        _format_keyword_line(keyword_total, keyword_counts),
    ]
    if reply_log_url:
        lines.append(f"**问题回复log：** [飞书多维表格]({reply_log_url})")
    if submit_report_url:
        lines.append(f"**提报表：** [飞书多维表格]({submit_report_url})")
    return "\n".join(lines)


def _list_bitable_tables(*, app_token: str, tenant_token: str) -> list[dict[str, Any]]:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables"
    data = _bitable_api_json(method="GET", url=url, tenant_token=tenant_token)
    items = data.get("items")
    return [it for it in items if isinstance(it, dict)] if isinstance(items, list) else []


def _create_text_field(*, app_token: str, table_id: str, tenant_token: str, field_name: str) -> None:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
    _bitable_api_json(
        method="POST",
        url=url,
        tenant_token=tenant_token,
        json_body={"field_name": field_name, "type": 1},
    )


def _delete_field(*, app_token: str, table_id: str, tenant_token: str, field_id: str) -> None:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field_id}"
    _bitable_api_json(method="DELETE", url=url, tenant_token=tenant_token)


def _first_bitable_view_id(*, app_token: str, table_id: str, tenant_token: str) -> str:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/views"
    data = _bitable_api_json(method="GET", url=url, tenant_token=tenant_token)
    items = data.get("items")
    if not isinstance(items, list):
        return ""
    for item in items:
        view_id = str(item.get("view_id") or "").strip() if isinstance(item, dict) else ""
        if view_id:
            return view_id
    return ""


def _ensure_toolant_log_table(
    *,
    app_token: str,
    tenant_token: str,
    table_name: str,
) -> str:
    for table in _list_bitable_tables(app_token=app_token, tenant_token=tenant_token):
        if str(table.get("name") or "").strip() == table_name:
            table_id = str(table.get("table_id") or "").strip()
            if table_id:
                return table_id
    fields = [{"field_name": name, "type": 1} for name in TOOLANT_LOG_FIELDS]
    return bitable_create_table(
        app_token=app_token,
        tenant_token=tenant_token,
        name=table_name,
        fields=fields,
    )


def _ensure_toolant_log_fields(
    *,
    app_token: str,
    table_id: str,
    tenant_token: str,
) -> None:
    fields = bitable_list_fields(app_token=app_token, table_id=table_id, tenant_token=tenant_token)
    for field in fields:
        name = str(field.get("field_name") or "").strip()
        field_id = str(field.get("field_id") or "").strip()
        if name in DEPRECATED_TOOLANT_LOG_FIELDS and field_id and field.get("is_primary") is not True:
            _delete_field(app_token=app_token, table_id=table_id, tenant_token=tenant_token, field_id=field_id)

    existing = {
        str(field.get("field_name") or "").strip()
        for field in bitable_list_fields(app_token=app_token, table_id=table_id, tenant_token=tenant_token)
    }
    for name in TOOLANT_LOG_FIELDS:
        if name not in existing:
            _create_text_field(
                app_token=app_token,
                table_id=table_id,
                tenant_token=tenant_token,
                field_name=name,
            )


def _toolant_bitable_url(doc: Mapping[str, Any], *, table_id: str, tenant_token: str = "") -> str:
    feishu = doc.get("feishu") if isinstance(doc.get("feishu"), dict) else {}
    bi = feishu.get("bitable", {}) if isinstance(feishu, dict) else {}
    app_token = _toolant_reply_log_app_token(doc) or str(bi.get("app_token") or "").strip()
    raw = str(_toolant_reply_log_url(doc) or bi.get("url") or "").strip()
    if raw:
        try:
            parsed = urlparse(raw)
            qs = parse_qs(parsed.query, keep_blank_values=True)
            qs["table"] = [table_id]
            if app_token and tenant_token:
                view_id = _first_bitable_view_id(app_token=app_token, table_id=table_id, tenant_token=tenant_token)
                if view_id:
                    qs["view"] = [view_id]
            return urlunparse(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    parsed.params,
                    urlencode(qs, doseq=True),
                    parsed.fragment,
                )
            )
        except Exception:
            return raw
    if app_token:
        return f"https://linknlatch.feishu.cn/base/{app_token}?table={table_id}"
    return ""


def _bitable_records_from_ai_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in rows:
        base_fields = bitable_fields_from_ai_reply_row(
            creator_name=row.get("creator_name"),
            source=row.get("source"),
            create_time=row.get("create_time"),
            message_info=row.get("message_info") or row.get("message_input_ai"),
            reply_result=row.get("reply_result"),
            full_history=row.get("message_info") or row.get("message_input_ai"),
            datetime_as_text=True,
            long_text_as_segments=False,
        )
        base_fields["是否加窗"] = _as_text(row.get("has_showcase")) or "否"
        base_fields["是否申样"] = _as_text(row.get("has_sample_request")) or "否"
        base_fields["gmv"] = _as_text(row.get("gmv"))
        base_fields["批样审核"] = _as_text(row.get("sample_review"))
        fields = {name: base_fields.get(name, "") for name in TOOLANT_LOG_FIELDS}
        records.append({"fields": fields})
    return records


def sync_toolant_reply_log_to_bitable(
    *,
    doc: Mapping[str, Any],
    rows: list[dict[str, Any]],
    week_start: datetime,
    week_end: datetime,
) -> str:
    cfg = parse_bitable_write_config(doc, default_config_file())
    if cfg is None:
        logger.warning("未配置 feishu.bitable，跳过 Toolant AI 回复 log 表同步")
        return ""
    token = feishu_tenant_access_token(app_id=cfg.app_id, app_secret=cfg.app_secret)
    app_token = _toolant_reply_log_app_token(doc) or cfg.app_token
    records = _bitable_records_from_ai_rows(rows)
    parts = (len(records) + BITABLE_WRITE_MAX_ROWS - 1) // BITABLE_WRITE_MAX_ROWS if records else 1
    first_table_id = ""

    for part in range(1, parts + 1):
        table_id = _ensure_toolant_log_table(
            app_token=app_token,
            tenant_token=token,
            table_name=_toolant_week_table_name(
                doc,
                week_start=week_start,
                week_end=week_end,
                part=part,
            ),
        )
        if not first_table_id:
            first_table_id = table_id
        _ensure_toolant_log_fields(app_token=app_token, table_id=table_id, tenant_token=token)

        if cfg.clear_before_write:
            record_ids = bitable_list_all_record_ids(
                app_token=app_token,
                table_id=table_id,
                tenant_token=token,
            )
            if record_ids:
                bitable_batch_delete_records(
                    app_token=app_token,
                    table_id=table_id,
                    tenant_token=token,
                    record_ids=record_ids,
                )

        part_records = records[(part - 1) * BITABLE_WRITE_MAX_ROWS : part * BITABLE_WRITE_MAX_ROWS]
        for i in range(0, len(part_records), BITABLE_BATCH_CREATE_MAX):
            bitable_batch_create_records(
                app_token=app_token,
                table_id=table_id,
                tenant_token=token,
                records=part_records[i : i + BITABLE_BATCH_CREATE_MAX],
            )

    logger.info(
        "Toolant reply log synced: rows=%s tables=%s limit_per_table=%s",
        len(records),
        parts,
        BITABLE_WRITE_MAX_ROWS,
    )
    return _toolant_bitable_url(doc, table_id=first_table_id, tenant_token=token)


def write_missing_people_info_csv(
    *,
    doc: Mapping[str, Any],
    rows: list[dict[str, Any]],
    end: datetime,
) -> None:
    missing = [r for r in rows if not str(r.get("people_info_name") or "").strip()]
    logger.info("Toolant creator info missing: count=%s", len(missing))
    if not missing:
        return
    out_dir_raw = _toolant_doc(doc).get("missing_people_info_dir")
    out_dir = Path(str(out_dir_raw).strip()) if out_dir_raw else default_config_file().parent / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    by_creator: dict[str, dict[str, Any]] = {}
    for row in missing:
        creator = str(row.get("creator_name") or "").strip()
        if not creator:
            continue
        existing = by_creator.setdefault(
            creator,
            {"creator_name": creator, "latest_reply_time": row.get("create_time"), "reply_count": 0},
        )
        existing["reply_count"] += 1
        if row.get("create_time") and row.get("create_time") > existing.get("latest_reply_time"):
            existing["latest_reply_time"] = row.get("create_time")

    path = out_dir / f"missing_creator_info_toolant_{end:%Y%m%d}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["creator_name", "latest_reply_time", "reply_count"])
        writer.writeheader()
        for item in sorted(by_creator.values(), key=lambda x: str(x["creator_name"]).lower()):
            writer.writerow(
                {
                    "creator_name": item["creator_name"],
                    "latest_reply_time": _as_text(item["latest_reply_time"]),
                    "reply_count": item["reply_count"],
                }
            )
    logger.info("Toolant missing creator info CSV written: %s", path)


def main(
    *,
    webhook_urls: list[str] | None = None,
    send: bool = True,
    start: datetime | None = None,
    end: datetime | None = None,
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    config_file = default_config_file()
    with config_file.open(encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if not isinstance(doc, dict):
        raise SystemExit(f"{config_file}: YAML root must be a mapping")

    db_cfg = load_db_config_from_yaml_file(config_file)
    source = _toolant_source(doc)
    if start is None or end is None:
        default_start, default_end = toolant_report_bounds()
        start = start or default_start
        end = end or default_end

    conn = connect(**db_cfg)
    try:
        ai_rows = fetch_toolant_ai_reply_rows(conn, source=source, start=start, end=end)
        keyword_total, keyword_counts, keyword_creators = fetch_toolant_keyword_counts(
            conn,
            source=source,
            start=start,
            end=end,
        )
        log_week_start, log_week_end = _toolant_log_week_bounds(end)
        log_rows = fetch_toolant_ai_reply_rows(
            conn,
            source=source,
            start=log_week_start,
            end=end,
        )
        write_missing_people_info_csv(doc=doc, rows=ai_rows, end=end)
        reply_log_url = sync_toolant_reply_log_to_bitable(
            doc=doc,
            rows=log_rows,
            week_start=log_week_start,
            week_end=log_week_end,
        )
        body = _build_report_markdown(
            start=start,
            end=end,
            ai_rows=ai_rows,
            keyword_total=keyword_total,
            keyword_counts=keyword_counts,
            keyword_creators=keyword_creators,
            reply_log_url=reply_log_url,
            submit_report_url=_toolant_submit_report_url(doc),
        )
        print(body)
        if not send:
            logger.info("Toolant webhook send disabled")
            return
        hooks = _toolant_webhooks(doc, webhook_urls)
        if not hooks:
            logger.info("未配置 Toolant webhook，跳过飞书推送")
            return
        for hook in hooks:
            ok = send_daily_report_interactive_webhook(
                webhook_url=hook,
                body_markdown=body,
                title=str(_toolant_doc(doc).get("report_title") or "Toolant 自动巡航日报"),
            )
            if not ok:
                logger.warning("Toolant 飞书推送失败，已跳过该 webhook")
    finally:
        conn.close()
