"""
toolant 意图后置校正。

业务定义：**强意向**（``interest``）= B 层达人已确认接受「1 个样品 + 2 条视频」方案；
仅 ``Yes/Interested``、或询问如何申请样品，均 **不是** 强意向。
"""

from __future__ import annotations

import re
from dataclasses import replace

from hubstudio_python.models.intent_classification import IntentClassificationResult

_SAMPLE_QUESTION_RE = re.compile(
    r"""
    \b(how|what|where|when|why|can\s+i|could\s+i|do\s+i|apply)\b
    .{0,48}
    \b(sample|samples)\b
    |
    \b(sample|samples)\b
    .{0,24}
    \b(apply|application|get|receive|ship)\b
    """,
    re.I | re.VERBOSE,
)
_SAMPLE_REQUEST_RE = re.compile(
    r"""
    \b(want|need|send|get|apply\s+for|request)\b
    .{0,32}
    \b(sample|samples)\b
    |
    \bfree\s+sample\b
    |
    \bsend\s+me\s+(the\s+)?sample\b
    """,
    re.I | re.VERBOSE,
)
_TWO_VIDEO_TERMS_RE = re.compile(
    r"\b(2|two)\s+(short\s+)?videos?\b|一条样品.*两条视频|1\s*sample.*2\s*video",
    re.I,
)
_ACCEPT_RE = re.compile(
    r"^(yes|ok|okay|sure|interested!?|i'?m\s+in|sounds\s+good|let'?s\s+do\s+it)\b",
    re.I,
)
_CONTEXT_TWO_VIDEO_PITCH_RE = re.compile(
    r"\b2\s+short\s+videos?\b|\basked\s+for\s+2\s+short\s+videos?\b|"
    r"two\s+videos?\s+back|2\s+videos?\s+in\s+return",
    re.I,
)


_CONTEXT_AI_UGC_PITCH_RE = re.compile(
    r"ai\s+ugc|ready-to-post\s+ai\s+video|no\s+filming|creator\s+portal|"
    r"\$1\s+per\s+video|\$5\s+bonus|reply\s+'ai'|scan\s+the\s+qr|"
    r"join\s+our\s+wa\s+group|easy\s+collab",
    re.I,
)

_CPM_NEGOTIATION_RE = re.compile(
    r"\bcpm\b|flat\s+fee\s+on\s+top|base\s+payment|base\s+rate|"
    r"what(?:'s|\s+is)\s+the\s+rate|can\s+you\s+do\s+cpm|guaranteed\s+payment",
    re.I,
)
_PRICE_TOO_LOW_RE = re.compile(
    r"too\s+low|so\s+low|not\s+enough|price\s+is|proce\s+is|rate\s+is\s+low|"
    r"commission\s+is\s+too|pay\s+is\s+too|need\s+(?:a\s+)?base|want\s+more",
    re.I,
)
_COMMISSION_PITCH_CTX_RE = re.compile(
    r"commission-only|pure.?commission|open\s+plan|commission\s+only",
    re.I,
)
_FLAT_FEE_ONLY_RE = re.compile(
    r"flat\s+fee\s+only|fixed\s+price\s+only|i\s+don'?t\s+want\s+cpm|"
    r"just\s+give\s+me\s+a\s+flat|flat\s+rate\s+only|one[- ]?video\s+flat\s+fee\s+only",
    re.I,
)


def is_a1_commission_price_reject(*, message: str, context_text: str = "") -> bool:
    """纯佣开场后达人嫌价低 → 应走 A2 CPM Offer，而非 Talent Plan 等泛收益话术。"""
    text = (message or "").strip()
    if not text:
        return False
    return bool(
        _PRICE_TOO_LOW_RE.search(text)
        and _COMMISSION_PITCH_CTX_RE.search(context_text or "")
    )


def _context_has_ai_ugc_pitch(context_text: str) -> bool:
    return bool(_CONTEXT_AI_UGC_PITCH_RE.search(context_text or ""))


def _context_has_b_tier_sample_pitch(context_text: str) -> bool:
    return _context_has_two_video_pitch(context_text)


def _is_bare_accept(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    lower = text.lower()
    return bool(_ACCEPT_RE.search(text) or lower in {"yes", "interested", "ok", "sure"})


def enrich_toolant_match_context(
    *,
    message: str,
    context_text: str,
    flags: list[str],
    creator_emotion: str,
) -> tuple[list[str], str]:
    """
    toolant 在线回复：按上下文补全 ``reply_*`` 与 ``creator_emotion``，便于匹配知识库多条规则。
    """
    out_flags = list(flags)
    emotion = creator_emotion

    if _is_bare_accept(message):
        for f in ("reply_yes", "reply_interested"):
            if f not in out_flags:
                out_flags.append(f)
        if _context_has_ai_ugc_pitch(context_text):
            if "reply_ai" not in out_flags:
                out_flags.append("reply_ai")
        if not emotion or emotion == "GEN":
            emotion = "感兴趣或同意"

    return out_flags, emotion


def _context_has_two_video_pitch(context_text: str) -> bool:
    return bool(_CONTEXT_TWO_VIDEO_PITCH_RE.search(context_text or ""))


def confirms_one_sample_two_videos(*, message: str, context_text: str = "") -> bool:
    """达人是否明确接受「1 样 2 视频」方案。"""
    text = (message or "").strip()
    if not text:
        return False
    lower = text.lower()
    ctx = context_text or ""

    if _TWO_VIDEO_TERMS_RE.search(text) and re.search(
        r"\b(yes|ok|sure|agree|accept|interested|i'?m\s+in|deal)\b", lower
    ):
        return True

    if _context_has_two_video_pitch(ctx) and (
        _ACCEPT_RE.search(text.strip())
        or lower in {"yes", "interested", "ok", "sure", "yes, i'm interested!"}
    ):
        return True

    return False


def is_sample_inquiry(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    if _SAMPLE_QUESTION_RE.search(text):
        return True
    if _SAMPLE_REQUEST_RE.search(text) and not confirms_one_sample_two_videos(
        message=text, context_text=""
    ):
        return True
    return False


def refine_toolant_intent(
    result: IntentClassificationResult,
    *,
    message: str,
    context_text: str = "",
) -> IntentClassificationResult:
    """校正 toolant 的 ``interest`` / ``ok`` 误分类。"""
    text = (message or "").strip()
    if not text:
        return result

    if is_sample_inquiry(text):
        return replace(
            result,
            intent_category="Ask for samples",
            confidence="high" if "?" in text or text.lower().startswith(("how", "what", "can")) else result.confidence,
            matched_by="toolant_refine",
            matched_rule="Ask for samples",
            hints=[*result.hints, "sample_inquiry_not_strong_intent"],
        )

    strong = confirms_one_sample_two_videos(message=text, context_text=context_text)
    ic = (result.intent_category or "").strip().casefold()
    rule = (result.matched_rule or "").strip().casefold()

    if strong:
        return replace(
            result,
            intent_category="interest",
            confidence="high",
            matched_by="toolant_refine" if result.matched_by != "exact" else result.matched_by,
            matched_rule="interest",
            hints=[*result.hints, "one_sample_two_videos_confirmed"],
        )

    if _context_has_ai_ugc_pitch(context_text) and _is_bare_accept(text):
        return replace(
            result,
            intent_category="AI-UGC Earnings",
            confidence="high",
            matched_by="toolant_refine",
            matched_rule="AI-UGC Earnings",
            hints=[*result.hints, "ai_ugc_pitch_accept"],
        )

    if _FLAT_FEE_ONLY_RE.search(text):
        return replace(
            result,
            intent_category="Flat Fee",
            confidence="high",
            matched_by="toolant_refine",
            matched_rule="Flat Fee",
            hints=[*result.hints, "a_tier_flat_fee_only"],
        )

    if is_a1_commission_price_reject(message=text, context_text=context_text):
        return replace(
            result,
            intent_category="CPM Rate",
            confidence="high",
            matched_by="toolant_refine",
            matched_rule="CPM Rate",
            hints=[*result.hints, "a1_reject_price_too_low_to_a2_cpm"],
        )

    if _CPM_NEGOTIATION_RE.search(text):
        return replace(
            result,
            intent_category="CPM Rate",
            confidence="high",
            matched_by="toolant_refine",
            matched_rule="CPM Rate",
            hints=[*result.hints, "a_tier_cpm_negotiation"],
        )

    if ic in {"interest", "强意向"} or rule in {"interest", "ok"}:
        if re.search(r"\b(interested|yes)\b", text, re.I):
            if _context_has_b_tier_sample_pitch(context_text) and not strong:
                return replace(
                    result,
                    intent_category="GEN",
                    confidence="medium",
                    matched_by="toolant_refine",
                    matched_rule="GEN",
                    hints=[*result.hints, "interested_without_two_video_confirmation"],
                )
            return result

    return result
