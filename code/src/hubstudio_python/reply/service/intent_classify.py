"""在线意图识别：店铺 × 档位 × 会话内容 → 意图 + 情绪等匹配维度。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from hubstudio_python.reply.service.rules.derive import message_to_reply_flags
from hubstudio_python.reply.service.rules.schema import GENERAL
from hubstudio_python.models.creator_emotion_infer import infer_creator_emotion
from hubstudio_python.models.intent_ranking import (
    RankedIntent,
    rank_user_message_intents,
    ranked_to_single_result,
)
from hubstudio_python.models.shop_intent_hierarchy import (
    allowed_intents_for_scope,
    intent_in_scope,
)
from hubstudio_python.reply.service.generate import (
    latest_creator_message,
    trim_context_text,
)


def _parse_creator_progress(raw: object) -> str:
    if raw is None:
        return GENERAL
    if isinstance(raw, list):
        parts = [str(p).strip() for p in raw if str(p).strip()]
        if not parts:
            return GENERAL
        for item in parts:
            if not re.search(r"negotiation_rounds\s*=", item, re.I):
                return item
        return parts[0]
    s = str(raw).strip()
    return s or GENERAL


@dataclass
class ClassifyIntentResult:
    ok: bool
    shop: str
    creator_type: str
    creator_progress: str
    latest_creator_message: str
    intent_category: str
    intent_confidence: str
    intent_matched_by: str
    intent_matched_rule: str
    creator_emotion: str
    creator_reply_frequency: str
    intent_valid_for_scope: bool
    allowed_intents: list[str]
    top_intents: list[RankedIntent] = field(default_factory=list)
    hints: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_api_dict(self) -> dict[str, Any]:
        """对外 API：``success`` + ``topIntents``；失败时附带 ``error``。"""
        out: dict[str, Any] = {
            "success": self.ok,
            "topIntents": [
                {
                    "intentCategory": item.intent_category,
                    "confidence": round(item.confidence, 4),
                    "reason": item.reason,
                }
                for item in self.top_intents
            ],
        }
        if self.error:
            out["error"] = self.error
        return out


def classify_creator_intent(payload: dict[str, Any]) -> ClassifyIntentResult:
    shop = str(payload.get("shop") or "").strip()
    creator_type = str(payload.get("creatorType") or payload.get("creator_type") or GENERAL).strip() or GENERAL
    creator_progress = _parse_creator_progress(
        payload.get("creatorProgress") or payload.get("creator_progress")
    )
    context_text = str(payload.get("contextText") or payload.get("context_text") or "")
    message = str(payload.get("message") or payload.get("latestMessage") or "").strip()
    latest = message or latest_creator_message(context_text)
    if not latest and context_text.strip():
        latest = context_text.strip()[:500]

    if not shop:
        return ClassifyIntentResult(
            ok=False,
            shop="",
            creator_type=creator_type,
            creator_progress=creator_progress,
            latest_creator_message=latest,
            intent_category=GENERAL,
            intent_confidence="low",
            intent_matched_by="none",
            intent_matched_rule="",
            creator_emotion=GENERAL,
            creator_reply_frequency=GENERAL,
            intent_valid_for_scope=False,
            allowed_intents=[],
            error="missing shop",
        )

    if not latest:
        return ClassifyIntentResult(
            ok=False,
            shop=shop,
            creator_type=creator_type,
            creator_progress=creator_progress,
            latest_creator_message="",
            intent_category=GENERAL,
            intent_confidence="low",
            intent_matched_by="none",
            intent_matched_rule="",
            creator_emotion=GENERAL,
            creator_reply_frequency=GENERAL,
            intent_valid_for_scope=False,
            allowed_intents=allowed_intents_for_scope(shop, creator_type, creator_progress=creator_progress),
            error="missing message or contextText",
        )

    snippet = trim_context_text(context_text or latest, max_chars=12000, max_messages=40)

    use_llm_raw = payload.get("useLlm") if "useLlm" in payload else payload.get("use_llm")
    use_llm: bool | None
    if use_llm_raw is None:
        use_llm = None
    else:
        use_llm = bool(use_llm_raw)

    allowed = allowed_intents_for_scope(shop, creator_type, creator_progress=creator_progress)

    top_intents = rank_user_message_intents(
        latest,
        shop=shop,
        creator_type=creator_type,
        context_text=snippet,
        use_llm=use_llm,
        allowed_intents=allowed,
        min_confidence=0.5,
        top_k=3,
    )
    intent_result = ranked_to_single_result(top_intents, latest)

    preset_emotion = str(payload.get("creatorEmotion") or payload.get("creator_emotion") or "").strip()
    creator_emotion = infer_creator_emotion(
        latest,
        shop=shop,
        context_text=snippet,
        preset=preset_emotion,
    )

    creator_reply_frequency = str(
        payload.get("creatorReplyFrequency") or payload.get("creator_reply_frequency") or GENERAL
    ).strip() or GENERAL

    valid = intent_in_scope(
        intent_result.intent_category,
        shop,
        creator_type,
        creator_progress=creator_progress,
    )

    hints = list(intent_result.hints or [])
    if message_to_reply_flags(latest):
        hints.append(f"reply_flags={message_to_reply_flags(latest)}")
    if not valid and allowed:
        hints.append("intent_not_in_shop_tier_progress_scope")

    return ClassifyIntentResult(
        ok=True,
        shop=shop,
        creator_type=creator_type,
        creator_progress=creator_progress,
        latest_creator_message=latest,
        intent_category=intent_result.intent_category,
        intent_confidence=intent_result.confidence,
        intent_matched_by=intent_result.matched_by,
        intent_matched_rule=intent_result.matched_rule or "",
        creator_emotion=creator_emotion,
        creator_reply_frequency=creator_reply_frequency,
        intent_valid_for_scope=valid,
        allowed_intents=allowed,
        top_intents=top_intents,
        hints=hints,
    )
