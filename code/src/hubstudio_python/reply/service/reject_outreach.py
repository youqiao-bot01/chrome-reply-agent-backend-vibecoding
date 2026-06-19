"""GEN 规则 #1：达人首次表态拒绝 → 礼貌收尾并写 ``shop_rejected``；已拒绝则静默。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from hubstudio_python.reply.service.rules.derive import message_to_reply_flags
from hubstudio_python.reply.sql.creator_state import (
    CreatorShopState,
    get_creator_shop_state,
    upsert_creator_shop_state,
)
from hubstudio_python.reply.service.reply_guard import ensure_linsey_signature

_COMMISSION_PITCH_RE = re.compile(
    r"commission[- ]only|open plan|pure commission|commission only|纯佣",
    re.I,
)
_SHORT_REJECT = frozenset(
    {
        "no",
        "nope",
        "nah",
        "pass",
        "decline",
        "not interested",
        "no thanks",
        "no thank you",
        "not for me",
    }
)
_REJECT_PHRASE_RE = re.compile(
    r"\b(not interested|no thanks|no thank you|don'?t want|won'?t|can'?t do|pass)\b",
    re.I,
)


@dataclass(frozen=True)
class OutreachRejectResult:
    silent: bool
    reply: str
    db_updates: dict[str, Any]


def _context_has_commission_pitch(context_text: str) -> bool:
    return bool(_COMMISSION_PITCH_RE.search(context_text or ""))


def is_outreach_reject_message(message: str, context_text: str = "") -> bool:
    """达人明确拒绝合作（非 A 层议价中的「价太低」）。"""
    text = (message or "").strip()
    if not text:
        return False
    flags = message_to_reply_flags(text)
    if "reply_reject" in flags:
        return True
    lower = text.lower().strip().rstrip(".!")
    if lower in _SHORT_REJECT:
        return True
    if _REJECT_PHRASE_RE.search(text):
        return True
    return False


def is_toolant_a_negotiation_reject(
    *,
    shop: str,
    creator_type: str,
    message: str,
    context_text: str,
) -> bool:
    """
    toolant A 层对纯佣说「no」→ 走 A1→A2 议价，不是 GEN 劝退。
    """
    if shop.strip().lower() != "toolant":
        return False
    from hubstudio_python.models.toolant_creator_type import is_toolant_head_tier

    if not is_toolant_head_tier(creator_type):
        return False
    if not _context_has_commission_pitch(context_text):
        return False
    from hubstudio_python.models.toolant_intent import is_a1_commission_price_reject

    if is_a1_commission_price_reject(message=message, context_text=context_text):
        return True
    lower = (message or "").strip().lower().rstrip(".!")
    return lower in _SHORT_REJECT or "reply_reject" in message_to_reply_flags(message)


def _goodbye_reply(*, language: str) -> str:
    lang = (language or "English").strip().lower()
    if lang.startswith("zh") or "中文" in lang:
        body = "很遗憾听到这个消息，期待以后有机会合作，这里就不再打扰你了。祝你一切顺利！"
    else:
        body = (
            "I'm sorry to hear that. I hope we'll have a chance to work together in the future — "
            "I won't reach out again about this. Wishing you all the best!"
        )
    return ensure_linsey_signature(body)


def mark_shop_rejected(
    creator_id: str,
    shop: str,
    *,
    creator_name: str = "",
) -> dict[str, Any]:
    cid = str(creator_id or "").strip()
    if not cid:
        return {"shop_rejected": False, "reason": "missing_creator_id"}
    state = get_creator_shop_state(cid, shop) or CreatorShopState(
        creator_id=cid,
        shop=shop.strip(),
        creator_name=creator_name,
    )
    if creator_name and not state.creator_name:
        state.creator_name = creator_name
    state.shop_rejected = True
    state.creator_progress = "已拒绝"
    state.creator_emotion = "拒绝合作"
    upsert_creator_shop_state(state)
    return {
        "shop_rejected": True,
        "creator_progress": "已拒绝",
        "creator_emotion": "拒绝合作",
    }


def try_handle_outreach_reject(
    *,
    shop: str,
    creator_type: str,
    creator_id: str,
    creator_name: str,
    latest_message: str,
    context_text: str,
    language: str = "English",
    already_rejected: bool = False,
) -> OutreachRejectResult | None:
    """
    首次拒绝 → 固定劝退话术 + 写库；已拒绝 → 静默。
    非拒绝消息 → ``None``（继续正常 RAG 流程）。
    """
    if already_rejected:
        return OutreachRejectResult(
            silent=True,
            reply="",
            db_updates={"shop_rejected": True, "reason": "already_rejected"},
        )

    if not is_outreach_reject_message(latest_message, context_text):
        return None

    if is_toolant_a_negotiation_reject(
        shop=shop,
        creator_type=creator_type,
        message=latest_message,
        context_text=context_text,
    ):
        return None

    lookup = str(creator_id or creator_name or "").strip()
    reply = _goodbye_reply(language=language)
    db_updates = mark_shop_rejected(lookup, shop, creator_name=creator_name or lookup)
    db_updates["outreach_reject"] = True
    return OutreachRejectResult(silent=False, reply=reply, db_updates=db_updates)
