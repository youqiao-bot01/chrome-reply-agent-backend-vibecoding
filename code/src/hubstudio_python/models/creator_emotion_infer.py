"""从达人最新消息推断 ``creator_emotion``。"""

from __future__ import annotations

import re

from hubstudio_python.reply.service.rules.derive import message_to_reply_flags
from hubstudio_python.reply.service.rules.schema import GENERAL

_REJECT_RE = re.compile(
    r"\b(no\b|not interested|pass|decline|stop|unsubscribe|reject|won'?t)\b|"
    r"don'?t\s+want|not\s+for\s+me",
    re.I,
)
_NOT_INTERESTED_RE = re.compile(
    r"not\s+interested|no\s+thanks|maybe\s+later|not\s+now|pass\s+on|"
    r"不感兴趣|暂时不|不考虑",
    re.I,
)
_INTERESTED_RE = re.compile(
    r"\b(yes|sure|ok|interested|sounds?\s+good|i'?m\s+in|deal|let'?s\s+do)\b|"
    r"感兴趣|可以|好的",
    re.I,
)


def infer_creator_emotion(
    message: str,
    *,
    shop: str = "",
    context_text: str = "",
    preset: str = "",
) -> str:
    preset_val = str(preset or "").strip()
    if preset_val and preset_val.upper() != GENERAL:
        return preset_val

    text = (message or "").strip()
    if not text:
        return GENERAL

    shop_key = str(shop or "").strip().lower()
    if shop_key == "toolant":
        from hubstudio_python.models.toolant_intent import enrich_toolant_match_context

        _flags, emotion = enrich_toolant_match_context(
            message=text,
            context_text=context_text,
            flags=message_to_reply_flags(text),
            creator_emotion=GENERAL,
        )
        if emotion and emotion.upper() != GENERAL:
            return emotion

    flags = message_to_reply_flags(text)
    if "reply_reject" in flags or _REJECT_RE.search(text):
        return "拒绝合作"
    if _NOT_INTERESTED_RE.search(text):
        return "当前聊天主题不感兴趣"
    if "reply_interested" in flags or "reply_yes" in flags or _INTERESTED_RE.search(text):
        return "感兴趣或同意"

    return GENERAL
