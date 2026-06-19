"""回复生成后写入 MySQL 审计表。"""

from __future__ import annotations

from typing import Any

from hubstudio_python.reply.service.rules.schema import GENERAL
from hubstudio_python.reply.sql.mysql_ai_reply import AiReplyLogRecord, insert_ai_reply_log


def parse_bool_flag(raw: object) -> bool:
    if raw is None:
        return False
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return int(raw) != 0
    s = str(raw).strip().lower()
    return s in {"1", "true", "yes", "on", "y"}


def _distance_to_similarity(distance: float | None) -> float | None:
    if distance is None:
        return None
    try:
        d = float(distance)
    except (TypeError, ValueError):
        return None
    return round(max(0.0, min(1.0, 1.0 / (1.0 + d))), 6)


def _top_match_fields(
    matched_rules: list[tuple[dict[str, Any], float | None]] | None,
) -> tuple[str, str, str, float | None, float | None]:
    if not matched_rules:
        return "", "", "", None, None
    rule, dist = matched_rules[0]
    title = str(rule.get("title") or rule.get("entitle") or "").strip()
    content = str(
        rule.get("content") or rule.get("encontent") or rule.get("reference_script") or ""
    ).strip()
    match_type = str(rule.get("rule_type") or rule.get("intent_category") or "").strip()
    vec_sim = _distance_to_similarity(dist)
    return title, content[:4000], match_type, vec_sim, vec_sim


def persist_ai_reply_log(
    *,
    shop: str,
    creator_name: str,
    context_text: str,
    message_input_ai: str,
    reply_result: str,
    creator_emotion: str = GENERAL,
    product_id: str = "",
    campaign_id: str = "",
    matched_rules: list[tuple[dict[str, Any], float | None]] | None = None,
) -> dict[str, Any]:
    title, content, match_type, vec_sim, final_sim = _top_match_fields(matched_rules)
    record = AiReplyLogRecord(
        source=shop,
        creator_name=creator_name,
        message_info=context_text,
        message_input_ai=message_input_ai,
        reply_result=reply_result,
        match_content=content,
        match_title=title,
        match_type=match_type,
        vector_similarity_score=vec_sim,
        final_similarity_score=final_sim,
        emotion=creator_emotion or GENERAL,
        discuss_product=product_id,
        campaign_id=campaign_id,
    )
    return insert_ai_reply_log(record)
