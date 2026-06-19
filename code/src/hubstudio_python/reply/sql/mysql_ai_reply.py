"""``controlpastmessagesdata.auto_reply_plugin_ai_reply_info`` 回复审计日志。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from hubstudio_python.reply.sql.mysql_config import MysqlConfig, mysql_config_from_env
from hubstudio_python.reply.sql.mysql_creator import mysql_connection


@dataclass(frozen=True)
class AiReplyLogRecord:
    source: str
    creator_name: str
    message_info: str
    message_input_ai: str
    reply_result: str
    match_content: str = ""
    match_title: str = ""
    match_type: str = ""
    vector_similarity_score: float | None = None
    final_similarity_score: float | None = None
    emotion: str = ""
    discuss_product: str = ""
    campaign_id: str = ""


def insert_ai_reply_log(
    record: AiReplyLogRecord,
    *,
    config: MysqlConfig | None = None,
) -> dict[str, Any]:
    cfg = config or mysql_config_from_env()
    if not cfg.enabled:
        return {"inserted": False, "reason": "mysql_disabled"}
    if not cfg.ai_reply_log_database or not cfg.ai_reply_log_table:
        return {"inserted": False, "reason": "ai_reply_log_not_configured"}

    vec = record.vector_similarity_score
    final = record.final_similarity_score
    if final is None and vec is not None:
        final = vec

    message_input = record.message_input_ai
    if record.campaign_id.strip():
        message_input = (
            f"{message_input}\n\n<!-- campaignId={record.campaign_id.strip()} -->"
            if message_input
            else f"<!-- campaignId={record.campaign_id.strip()} -->"
        )

    table = f"`{cfg.ai_reply_log_database}`.`{cfg.ai_reply_log_table}`"
    sql = (
        f"INSERT INTO {table} "
        "(source, creator_name, message_info, message_input_ai, reply_result, "
        "match_content, match_title, match_type, vector_similarity_score, "
        "final_similarity_score, create_time, emotion, discuss_product) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    now = datetime.now().replace(microsecond=0)
    values = (
        record.source[:255] if record.source else "",
        record.creator_name[:255] if record.creator_name else "",
        record.message_info,
        message_input,
        record.reply_result,
        record.match_content,
        record.match_title[:512] if record.match_title else "",
        record.match_type[:128] if record.match_type else "",
        vec,
        final,
        now,
        record.emotion[:128] if record.emotion else "",
        record.discuss_product[:128] if record.discuss_product else "",
    )

    with mysql_connection(cfg) as conn:
        with conn.cursor() as cur:
            affected = cur.execute(sql, values)
            row_id = cur.lastrowid
    return {
        "inserted": affected > 0,
        "id": row_id,
        "table": cfg.ai_reply_log_table,
        "database": cfg.ai_reply_log_database,
    }
