"""
知识库 ``ai_acation`` → 业务库副作用（MySQL / 本地 SQLite）。

命中带 ``ai_acation`` 的规则并生成回复后，由 ``apply_post_reply_updates`` 调用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from hubstudio_python.reply.service.rules.schema import CreatorRuleContext
from hubstudio_python.reply.service.toolant_cooperation import (
    COOPERATION_INTENTION_ONE_SAMPLE_TWO_VIDEOS,
    write_cooperation_intention,
)

ActionHandler = Callable[["AiActionContext"], dict[str, Any] | None]

COOPERATION_INTENTION_AI_UGC = "ai_ugc_roster"

# 自然语言 / 旧 chunks 文案 → 规范 action id
_ACTION_ALIASES: dict[str, str] = {
    "one_sample_two_videos": COOPERATION_INTENTION_ONE_SAMPLE_TWO_VIDEOS,
    "one sample two videos": COOPERATION_INTENTION_ONE_SAMPLE_TWO_VIDEOS,
    "b_level_one_sample_two_videos": COOPERATION_INTENTION_ONE_SAMPLE_TWO_VIDEOS,
    "join_wa_1": "join_wa_1",
    "join_wa_2": "join_wa_2",
    "send flat fee offer": "send_flat_fee_offer",
    "send_cpm_offer": "send_cpm_offer",
    "send cpm offer + estimate": "send_cpm_offer",
    "send cpm offer estimate": "send_cpm_offer",
    "log creator's minimum rate to quote sheet": "log_creator_quote",
    "log creator minimum rate to quote sheet": "log_creator_quote",
    "log creators minimum rate to quote sheet": "log_creator_quote",
    "push commission rate and sample form": "push_commission_sample",
    "send a1 pure commission opening": "send_a1_opening",
    "send whatsapp group invite link": "send_whatsapp_group_link",
    "send_whatsapp_group_link": "send_whatsapp_group_link",
    "send affiliate link": "send_affiliate_link",
    "send_affiliate_link": "send_affiliate_link",
    "add to ai ugc roster, send portal link": "add_to_ai_ugc_roster",
    "add to ai ugc roster send portal link": "add_to_ai_ugc_roster",
    "add_to_ai_ugc_roster": "add_to_ai_ugc_roster",
    "add to dnc list, stop all outreach": "mark_dnc",
    "add to dnc list stop all outreach": "mark_dnc",
    "mark_dnc": "mark_dnc",
    "flat_fee_agreed": "flat_fee_agreed",
}


@dataclass
class AiActionContext:
    creator_id: str
    shop: str
    ai_acation: str = ""
    latest_message: str = ""
    generated_reply: str = ""
    intent_category: str = ""
    matched_rule_record: dict[str, Any] = field(default_factory=dict)
    ctx: CreatorRuleContext | None = None


def normalize_ai_action(raw: str) -> str:
    s = (raw or "").strip().lower()
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"[^\w_]", "", s)
    if not s:
        return ""
    if s in _ACTION_ALIASES:
        return _ACTION_ALIASES[s]
    # 原始 lower 再试（含撇号等）
    raw_lower = (raw or "").strip().lower()
    if raw_lower in _ACTION_ALIASES:
        return _ACTION_ALIASES[raw_lower]
    return s


def _write_expert_quote(ctx: AiActionContext, amount: float, *, quote_type: str) -> dict[str, Any]:
    from hubstudio_python.reply.sql.expert_quote import update_expert_quote

    mysql_result = update_expert_quote(ctx.creator_id, amount)
    return {
        "mysql": mysql_result,
        "expert_quote": amount,
        "quote_type": quote_type,
    }


def _handle_one_sample_two_videos(ctx: AiActionContext) -> dict[str, Any] | None:
    result = write_cooperation_intention(
        ctx.creator_id,
        ctx.shop,
        COOPERATION_INTENTION_ONE_SAMPLE_TWO_VIDEOS,
    )
    return result


def _handle_join_wa_1(ctx: AiActionContext) -> dict[str, Any] | None:
    from hubstudio_python.reply.sql.mysql_creator import update_join_wa

    return {"mysql": update_join_wa(ctx.creator_id, 1)}


def _handle_join_wa_2(ctx: AiActionContext) -> dict[str, Any] | None:
    from hubstudio_python.reply.sql.mysql_creator import update_join_wa

    return {"mysql": update_join_wa(ctx.creator_id, 2)}


def _handle_add_to_ai_ugc_roster(ctx: AiActionContext) -> dict[str, Any] | None:
    return write_cooperation_intention(ctx.creator_id, ctx.shop, COOPERATION_INTENTION_AI_UGC)


def _handle_mark_dnc(ctx: AiActionContext) -> dict[str, Any] | None:
    from hubstudio_python.reply.sql.config import db_config_from_env
    from hubstudio_python.reply.sql.creator_state import (
        CreatorShopState,
        get_creator_shop_state,
        upsert_creator_shop_state,
    )

    if not db_config_from_env().enabled:
        return None
    state = get_creator_shop_state(ctx.creator_id, ctx.shop) or CreatorShopState(
        creator_id=ctx.creator_id.strip(),
        shop=ctx.shop.strip(),
    )
    state.shop_rejected = True
    state.creator_progress = "已拒绝"
    state.creator_emotion = "拒绝合作"
    upsert_creator_shop_state(state)
    return {"sqlite": {"shop_rejected": True}}


def _handle_log_creator_quote(ctx: AiActionContext) -> dict[str, Any] | None:
    from hubstudio_python.reply.service.quote_extract import extract_usd_quote_from_text

    parsed = extract_usd_quote_from_text(ctx.latest_message, prefer_quote_hints=True)
    if not parsed:
        return {"parsed": False, "reason": "no_amount_in_creator_message"}
    body = _write_expert_quote(ctx, parsed.amount_usd, quote_type="creator_quote")
    body["extraction"] = parsed.to_dict()
    return body


def _handle_send_flat_fee_offer(ctx: AiActionContext) -> dict[str, Any] | None:
    """达人一口价：从达人最新消息解析 USD 报价 → ``expert_quote``。"""
    from hubstudio_python.reply.service.quote_extract import extract_usd_quote_from_text

    parsed = extract_usd_quote_from_text(ctx.latest_message, prefer_quote_hints=True)
    if not parsed:
        return {"parsed": False, "reason": "no_creator_flat_fee_in_message"}
    body = _write_expert_quote(ctx, parsed.amount_usd, quote_type="creator_flat_fee")
    body["extraction"] = parsed.to_dict()
    return body


def _handle_flat_fee_agreed(ctx: AiActionContext) -> dict[str, Any] | None:
    """达人接受一口价：同样从达人消息解析金额。"""
    return _handle_send_flat_fee_offer(ctx)


def _handle_send_cpm_offer(ctx: AiActionContext) -> dict[str, Any] | None:
    return {"noop": True, "note": "cpm_offer_sent_in_reply"}


def _handle_push_commission_sample(ctx: AiActionContext) -> dict[str, Any] | None:
    return {"noop": True, "note": "commission_sample_sent_in_reply"}


def _handle_send_a1_opening(ctx: AiActionContext) -> dict[str, Any] | None:
    return {"noop": True, "note": "a1_opening_sent_in_reply"}


def _handle_send_whatsapp_group_link(ctx: AiActionContext) -> dict[str, Any] | None:
    return {"noop": True, "note": "whatsapp_link_in_reply"}


def _handle_send_affiliate_link(ctx: AiActionContext) -> dict[str, Any] | None:
    return {"noop": True, "note": "affiliate_link_in_reply"}


_HANDLERS: dict[str, ActionHandler] = {
    COOPERATION_INTENTION_ONE_SAMPLE_TWO_VIDEOS: _handle_one_sample_two_videos,
    "join_wa_1": _handle_join_wa_1,
    "join_wa_2": _handle_join_wa_2,
    "send_flat_fee_offer": _handle_send_flat_fee_offer,
    "flat_fee_agreed": _handle_flat_fee_agreed,
    "log_creator_quote": _handle_log_creator_quote,
    "send_cpm_offer": _handle_send_cpm_offer,
    "push_commission_sample": _handle_push_commission_sample,
    "send_a1_opening": _handle_send_a1_opening,
    "send_whatsapp_group_link": _handle_send_whatsapp_group_link,
    "send_affiliate_link": _handle_send_affiliate_link,
    "add_to_ai_ugc_roster": _handle_add_to_ai_ugc_roster,
    "mark_dnc": _handle_mark_dnc,
}


def apply_ai_action_effects(ctx: AiActionContext) -> dict[str, Any] | None:
    """
    执行 ``ai_acation`` 对应的写库动作。

    :return: ``{"action": ..., ...}``；无匹配 handler 时 ``None``。
    """
    cid = str(ctx.creator_id or "").strip()
    if not cid:
        return None
    action = normalize_ai_action(ctx.ai_acation)
    if not action:
        return None
    handler = _HANDLERS.get(action)
    if handler is None:
        return None
    result = handler(ctx)
    if not result:
        return None
    return {"action": action, **result}


def apply_ai_action_effects_legacy(
    *,
    creator_id: str,
    shop: str,
    ai_acation: str,
) -> dict[str, Any] | None:
    """仅 creator_id + action 的简易入口（测试 / 旧调用）。"""
    return apply_ai_action_effects(
        AiActionContext(creator_id=creator_id, shop=shop, ai_acation=ai_acation)
    )
