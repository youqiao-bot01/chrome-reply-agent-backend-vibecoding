"""
在线回复：MySQL ``negotiation_rounds`` ↔ ``priority_node`` / ``prev_node`` 自动同步。

前端不传谈判 flags；读库推导匹配上下文，回复后按命中规则写回轮次。
"""

from __future__ import annotations

import re
from typing import Any

from hubstudio_python.reply.service.rules.derive import message_to_reply_flags
from hubstudio_python.reply.service.rules.negotiation import (
    DEFAULT_A_TIER_CHAIN,
    apply_negotiation_to_context,
    negotiation_node_flags,
    round_from_priority_node,
    supports_a_tier_price_negotiation,
)
from hubstudio_python.reply.service.rules.schema import CreatorRuleContext

_FLAT_FEE_ONLY_RE = re.compile(
    r"flat\s+fee\s+only|fixed\s+price\s+only|only\s+(?:do\s+)?flat\s+fee|"
    r"i\s+only\s+(?:do\s+)?flat\s+fee|one[- ]?video\s+flat\s+fee\s+only",
    re.I,
)
_REJECT_RE = re.compile(
    r"\b(no\b|not interested|pass|decline|too low|so low|can'?t do|won'?t|reject)\b|"
    r"(?:price|proce|pay|rate|commission)\s+is\s+(?:too\s+)?low|not\s+enough",
    re.I,
)


def strip_a_tier_negotiation_flags(flags: list[str]) -> list[str]:
    """去掉旧 priority/prev 节点，避免 SQLite/前端残留与 MySQL 轮次冲突。"""
    return [
        f
        for f in flags
        if not (
            isinstance(f, str)
            and (f.startswith("priority_node:") or f.startswith("prev_node:"))
        )
    ]


def _message_implies_reject(message: str) -> bool:
    flags = message_to_reply_flags(message)
    if "reply_reject" in flags or "reply_not_interested" in flags:
        return True
    text = (message or "").strip()
    return bool(text and _REJECT_RE.search(text))


def _message_implies_flat_fee_only(message: str, intent_category: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    ic = (intent_category or "").strip()
    if ic == "Flat Fee" and _FLAT_FEE_ONLY_RE.search(text):
        return True
    return bool(_FLAT_FEE_ONLY_RE.search(text))


def infer_effective_negotiation_round(
    stored_round: int | None,
    *,
    latest_message: str,
    intent_category: str,
) -> tuple[int, bool]:
    """
    根据库内轮次 + 达人最新消息推导本次匹配用轮次。

    :return: ``(round, skip_a2)`` — ``skip_a2`` 表示 A1 直跳 A3（prev=A1）。
    """
    rnd = max(0, stored_round) if stored_round is not None else 0
    ic = (intent_category or "").strip()

    if rnd == 0 and _message_implies_flat_fee_only(latest_message, intent_category):
        return 2, True

    if _message_implies_reject(latest_message):
        rnd = min(rnd + 1, len(DEFAULT_A_TIER_CHAIN) - 1)

    # 达人已在问 CPM/底金 → 至少 A2；问一口价 → 至少 A3（与 Playbook A1→A2→A3 一致）
    if ic == "CPM Rate":
        rnd = max(rnd, 1)
    elif ic == "Flat Fee":
        rnd = max(rnd, 2)
    elif ic == "Creator Quote":
        rnd = max(rnd, 3)

    return rnd, False


def priority_node_from_rule(rule: dict[str, Any]) -> str | None:
    raw = rule.get("other_creator_conditions") or []
    if isinstance(raw, str):
        raw = [raw]
    pri: str | None = None
    for item in raw:
        if not isinstance(item, str):
            continue
        if item.startswith("priority_node:"):
            pri = item.split(":", 1)[1].strip().upper()
    return pri or None


def prev_node_from_rule(rule: dict[str, Any]) -> str | None:
    raw = rule.get("other_creator_conditions") or []
    if isinstance(raw, str):
        raw = [raw]
    for item in raw:
        if isinstance(item, str) and item.startswith("prev_node:"):
            return item.split(":", 1)[1].strip().upper()
    return None


def _resolve_stored_negotiation_round(
    *,
    creator_id: str,
    creator_progress: object,
) -> tuple[int | None, str]:
    """``creatorProgress`` 里显式 ``negotiation_rounds=N`` 优先；否则读 MySQL。"""
    from hubstudio_python.reply.service.rules.negotiation import parse_negotiation_round

    parsed = parse_negotiation_round(creator_progress)
    if parsed is not None:
        return parsed, "creator_progress"

    cid = str(creator_id or "").strip()
    if cid:
        from hubstudio_python.reply.sql.mysql_creator import get_negotiation_round

        stored = get_negotiation_round(cid)
        if stored is not None:
            return stored, "mysql"
    return None, "none"


def apply_negotiation_state_for_request(
    ctx: CreatorRuleContext,
    *,
    creator_id: str = "",
    creator_progress: object = None,
    latest_message: str,
    intent_category: str,
) -> dict[str, Any] | None:
    """
    谈判节点 **由后端推导**，前端勿传 ``priority_node:*`` / ``prev_node:*``。

    轮次来源（优先级）：
    1. ``creatorProgress`` 中的 ``negotiation_rounds=N`` / ``price_negotiation_rounds=N``
    2. MySQL ``negotiation_rounds``（按 ``creators_name`` = 插件 ``creatorId`` 查）
    3. 默认 0

    再结合达人最新消息 / 意图（CPM Rate 等）推导 ``effective_round`` → flags。
    """
    if not supports_a_tier_price_negotiation(ctx):
        return None

    flags = strip_a_tier_negotiation_flags(list(ctx.other_creator_conditions or []))
    ctx.other_creator_conditions = flags

    stored, round_source = _resolve_stored_negotiation_round(
        creator_id=creator_id,
        creator_progress=creator_progress if creator_progress is not None else ctx.creator_progress,
    )
    effective, skip_a2 = infer_effective_negotiation_round(
        stored,
        latest_message=latest_message,
        intent_category=intent_category,
    )

    if skip_a2:
        applied = apply_negotiation_to_context(
            ctx,
            priority_node="A3",
            prev_node="A1",
        )
        return {
            "stored_round": stored,
            "round_source": round_source,
            "effective_round": effective,
            "skip_a2": True,
            "priority_node": "A3",
            "prev_node": "A1",
            "applied": applied,
            "flags": negotiation_node_flags(priority_node="A3", prev_node="A1"),
        }

    applied = apply_negotiation_to_context(ctx, negotiation_round=effective)
    nodes = negotiation_node_flags(
        priority_node=DEFAULT_A_TIER_CHAIN[effective] if effective < len(DEFAULT_A_TIER_CHAIN) else None,
        prev_node=DEFAULT_A_TIER_CHAIN[effective - 1] if effective > 0 else None,
    )
    return {
        "stored_round": stored,
        "round_source": round_source,
        "effective_round": effective,
        "skip_a2": False,
        "applied": applied,
        "flags": nodes,
    }


def apply_negotiation_state_from_mysql(
    ctx: CreatorRuleContext,
    *,
    creator_id: str,
    latest_message: str,
    intent_category: str,
) -> dict[str, Any] | None:
    """兼容旧名：见 ``apply_negotiation_state_for_request``。"""
    return apply_negotiation_state_for_request(
        ctx,
        creator_id=creator_id,
        latest_message=latest_message,
        intent_category=intent_category,
    )


def persist_negotiation_round_after_reply(
    creator_id: str,
    *,
    ctx: CreatorRuleContext | None,
    matched_rule_record: dict[str, Any],
) -> dict[str, Any] | None:
    """命中规则后，把 ``priority_node`` 对应轮次写回 MySQL ``negotiation_rounds``。"""
    if ctx is None or not supports_a_tier_price_negotiation(ctx):
        return None
    cid = str(creator_id or "").strip()
    if not cid or not matched_rule_record:
        return None

    pri = priority_node_from_rule(matched_rule_record)
    if not pri or not pri.startswith("A"):
        return None
    rnd = round_from_priority_node(pri)
    if rnd is None:
        return None

    from hubstudio_python.reply.sql.mysql_creator import update_negotiation_round

    mysql_result = update_negotiation_round(cid, rnd)
    return {
        "negotiation_round": rnd,
        "priority_node": pri,
        "prev_node": prev_node_from_rule(matched_rule_record),
        "mysql": mysql_result,
    }
