"""
讨价还价场景：解析 USD/视频 报价，写入 MySQL ``expert_quote``。

触发：
- A4 达人主动报价（Creator Quote / reply_quote）
- A3 一口价谈妥（达人接受我方 flat fee offer）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from hubstudio_python.reply.service.rules.derive import message_to_reply_flags
from hubstudio_python.reply.service.rules.negotiation import supports_a_tier_price_negotiation
from hubstudio_python.reply.service.rules.schema import CreatorRuleContext

_ACCEPT_RE = re.compile(
    r"\b(deal|sounds good|let'?s do it|i accept|accepted|ok(?:ay)?|sure|yes)\b",
    re.I,
)
_DOLLAR_AMOUNT_RE = re.compile(
    r"""
    (?:
        \$\s*(\d{1,7}(?:\.\d{1,2})?)
        |
        (\d{1,7}(?:\.\d{1,2})?)\s*(?:usd|dollars?)
    )
    (?:\s*(?:/|\bper\b)\s*video|\s*per\s+vid\b)?
    """,
    re.I | re.VERBOSE,
)
_QUOTE_HINT_RE = re.compile(
    r"(?:min(?:imum)?|my\s+(?:rate|price|quote)|need\s+at\s+least|rate\s+is|"
    r"bottom\s*line|quoted|asking|want\s+\$|\$\d+\s+is\s+(?:too\s+)?low)",
    re.I,
)
_FLAT_FEE_CONTEXT_RE = re.compile(
    r"flat\s+(?:rate|fee)|one[- ]video|cpm\s+capped|that'?s\s+\$\s*(\d{1,7}(?:\.\d{1,2})?)\s+per\s+video",
    re.I,
)


@dataclass(frozen=True)
class QuoteExtraction:
    amount_usd: float
    quote_type: str  # creator_quote | flat_fee_agreed
    source: str  # creator_message | context_seller | generated_reply | llm
    raw_match: str
    confidence: str  # high | medium | low

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount_usd": self.amount_usd,
            "quote_type": self.quote_type,
            "source": self.source,
            "raw_match": self.raw_match,
            "confidence": self.confidence,
        }


def _amount_from_match(match: re.Match[str]) -> float | None:
    raw = match.group(1) or match.group(2)
    if not raw:
        return None
    try:
        val = float(raw.replace(",", ""))
    except ValueError:
        return None
    if val <= 0 or val > 1_000_000:
        return None
    return val


def _score_amount(text: str, match: re.Match[str], amount: float) -> float:
    start = match.start()
    window = text[max(0, start - 40): min(len(text), match.end() + 40)].lower()
    score = 0.0
    if _QUOTE_HINT_RE.search(window):
        score += 3.0
    if re.search(r"per\s+video|/video", window):
        score += 2.0
    if "$" in match.group(0):
        score += 1.0
    if 5 <= amount <= 5000:
        score += 0.5
    return score


def extract_usd_quote_from_text(text: str, *, prefer_quote_hints: bool = True) -> QuoteExtraction | None:
    """从单段文本中提取最可能的 USD/视频 报价。"""
    body = (text or "").strip()
    if not body:
        return None

    candidates: list[tuple[float, float, str]] = []
    for match in _DOLLAR_AMOUNT_RE.finditer(body):
        amount = _amount_from_match(match)
        if amount is None:
            continue
        score = _score_amount(body, match, amount)
        if not prefer_quote_hints and score < 2.0:
            score += 0.1
        candidates.append((score, amount, match.group(0).strip()))

    if not candidates:
        return None

    candidates.sort(key=lambda x: (-x[0], -x[1]))
    best_score, best_amount, raw = candidates[0]
    confidence = "high" if best_score >= 3 else ("medium" if best_score >= 2 else "low")
    return QuoteExtraction(
        amount_usd=best_amount,
        quote_type="creator_quote",
        source="text_regex",
        raw_match=raw,
        confidence=confidence,
    )


def _seller_messages_text(context_text: str) -> str:
    from hubstudio_python.reply.service.generate import parse_context_messages

    lines: list[str] = []
    for msg in parse_context_messages(context_text):
        if msg.get("role") == "seller" and msg.get("body"):
            lines.append(msg["body"])
    return "\n".join(lines)


def _creator_accepted(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    flags = message_to_reply_flags(text)
    if "reply_ok" in flags or "reply_yes" in flags or "reply_interested" in flags:
        return True
    return bool(_ACCEPT_RE.search(text))


def detect_quote_scenario(
    *,
    shop: str,
    ctx: CreatorRuleContext,
    latest_message: str,
    context_text: str,
    intent_category: str,
    matched_rules: list[tuple[dict[str, Any], float | None]],
) -> str | None:
    """
    判断是否应解析报价。

    :return: ``creator_quote`` | ``flat_fee_agreed`` | None
    """
    if shop.strip().lower() != "toolant":
        return None
    if not supports_a_tier_price_negotiation(ctx):
        return None

    ic = (intent_category or "").strip()
    flags = set(ctx.other_creator_conditions or [])
    flags.update(message_to_reply_flags(latest_message))

    top_rule = matched_rules[0][0] if matched_rules else {}
    top_intent = str(top_rule.get("intent_category") or "")
    top_nodes = {f for f in (top_rule.get("other_creator_conditions") or []) if isinstance(f, str)}

    if ic == "Creator Quote" or top_intent == "Creator Quote":
        return "creator_quote"
    if "reply_quote" in flags or _QUOTE_HINT_RE.search(latest_message or ""):
        return "creator_quote"
    if "priority_node:A4" in flags or "priority_node:A4" in top_nodes:
        if extract_usd_quote_from_text(latest_message or ""):
            return "creator_quote"

    if ic == "Flat Fee" or top_intent == "Flat Fee":
        if _creator_accepted(latest_message):
            return "flat_fee_agreed"
    if "priority_node:A3" in flags or "priority_node:A3" in top_nodes:
        if _creator_accepted(latest_message):
            return "flat_fee_agreed"

    return None


def extract_quote_for_scenario(
    *,
    scenario: str,
    latest_message: str,
    context_text: str,
    generated_reply: str = "",
) -> QuoteExtraction | None:
    if scenario == "creator_quote":
        parsed = extract_usd_quote_from_text(latest_message, prefer_quote_hints=True)
        if parsed:
            return QuoteExtraction(
                amount_usd=parsed.amount_usd,
                quote_type="creator_quote",
                source="creator_message",
                raw_match=parsed.raw_match,
                confidence=parsed.confidence,
            )
        llm = _llm_extract_quote(latest_message)
        if llm:
            return llm
        return None

    if scenario == "flat_fee_agreed":
        for source_name, text in (
            ("generated_reply", generated_reply),
            ("context_seller", _seller_messages_text(context_text)),
            ("creator_message", latest_message),
        ):
            if not text:
                continue
            m = re.search(
                r"that'?s\s+\$\s*(\d{1,7}(?:\.\d{1,2})?)\s+per\s+video",
                text,
                re.I,
            )
            if m:
                amount = float(m.group(1))
                return QuoteExtraction(
                    amount_usd=amount,
                    quote_type="flat_fee_agreed",
                    source=source_name,
                    raw_match=m.group(0),
                    confidence="high",
                )
            if _FLAT_FEE_CONTEXT_RE.search(text):
                parsed = extract_usd_quote_from_text(text, prefer_quote_hints=False)
                if parsed:
                    return QuoteExtraction(
                        amount_usd=parsed.amount_usd,
                        quote_type="flat_fee_agreed",
                        source=source_name,
                        raw_match=parsed.raw_match,
                        confidence=parsed.confidence,
                    )
        return None

    return None


def _llm_extract_quote(message: str) -> QuoteExtraction | None:
    text = (message or "").strip()
    if not text:
        return None
    try:
        from hubstudio_python.kb.service.chunking.zh_translate import ChunkTranslateConfig, _post_chat_with_retry
        from hubstudio_python.config import load_paths, load_project_yaml

        ai = load_project_yaml(load_paths().project_root).get("ai")
        if not isinstance(ai, dict):
            return None
        cfg = ChunkTranslateConfig.from_ai_yaml_section(ai)
        if cfg is None:
            return None
        system = (
            "Extract the creator's quoted price in USD per video from the message. "
            'Reply with JSON only: {"amount_usd": number|null, "confidence":"high|low"}'
        )
        raw = _post_chat_with_retry(cfg, system, text).strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = json.loads(raw)
        amount = data.get("amount_usd")
        if amount is None:
            return None
        val = float(amount)
        if val <= 0:
            return None
        return QuoteExtraction(
            amount_usd=val,
            quote_type="creator_quote",
            source="llm",
            raw_match=text[:120],
            confidence=str(data.get("confidence") or "low"),
        )
    except Exception:
        return None


def try_extract_and_save_expert_quote(
    *,
    creator_id: str,
    shop: str,
    ctx: CreatorRuleContext,
    latest_message: str,
    context_text: str,
    generated_reply: str,
    intent_category: str,
    matched_rules: list[tuple[dict[str, Any], float | None]],
) -> dict[str, Any] | None:
    """解析报价并写入 MySQL；返回摘要供 trace / db_updates。"""
    scenario = detect_quote_scenario(
        shop=shop,
        ctx=ctx,
        latest_message=latest_message,
        context_text=context_text,
        intent_category=intent_category,
        matched_rules=matched_rules,
    )
    if not scenario:
        return None

    extracted = extract_quote_for_scenario(
        scenario=scenario,
        latest_message=latest_message,
        context_text=context_text,
        generated_reply=generated_reply,
    )
    if not extracted:
        return {
            "scenario": scenario,
            "parsed": False,
            "reason": "no_amount_found",
        }

    from hubstudio_python.reply.sql.expert_quote import update_expert_quote

    mysql_result = update_expert_quote(creator_id, extracted.amount_usd)
    return {
        "scenario": scenario,
        "parsed": True,
        "extraction": extracted.to_dict(),
        "mysql": mysql_result,
    }
