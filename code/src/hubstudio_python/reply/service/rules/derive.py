"""
从 GMV 数值、达人消息等推导 ``other_creator_conditions`` 与 ``creator_type``。
"""

from __future__ import annotations

import re
from typing import Any

from hubstudio_python.models.playbook_query_flags import normalize_other_creator_conditions

_GMV_NUMBER = re.compile(r"[\d,]+\.?\d*")


def parse_monthly_gmv(raw: object) -> float | None:
    """
    解析 ``auto_reply_plugin_people_info.GMV`` 常见写法。

    例：``8420``、``33.6``、``0-$5k``、``$1,234``。
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    lower = s.lower().replace(",", "")
    if "5k" in lower or "5000" in lower:
        if "0-" in lower or lower.startswith("0"):
            return 2500.0
        return 5000.0
    if lower.endswith("k"):
        m = _GMV_NUMBER.search(lower)
        if m:
            return float(m.group()) * 1000
    m = _GMV_NUMBER.search(lower.replace("$", ""))
    if not m:
        return None
    val = float(m.group())
    if val < 100 and "." in m.group():
        return val * 1000
    return val


def gmv_amount_to_flags(amount: float, *, tiers: list[dict[str, Any]] | None = None) -> list[str]:
    """按 GMV 数值、达人消息等推导 ``other_creator_conditions`` 与 ``creator_type``。"""
    flags: list[str] = []
    if amount > 5000:
        flags.append("gmv_gt_5000")
    elif amount >= 1000:
        flags.extend(["gmv_gte_1000", "gmv_1000_5000"])
    else:
        flags.append("gmv_lt_1000")
    return flags


def gmv_amount_to_creator_type(amount: float, tiers: list[dict[str, Any]] | None = None) -> str:
    tiers = tiers or [
        {"min": 5000, "creator_type": "高GMV达人"},
        {"min": 1000, "max": 5000, "creator_type": "纯佣带货达人"},
        {"max": 1000, "creator_type": "AI UGC达人"},
    ]
    for tier in tiers:
        t_min = tier.get("min")
        t_max = tier.get("max")
        if t_min is not None and amount < float(t_min):
            continue
        if t_max is not None and amount > float(t_max):
            continue
        return str(tier.get("creator_type") or "GEN")
    return "GEN"


def gmv_level_to_flags(level: object, mapping: dict[int | str, list[str]] | None = None) -> list[str]:
    if level is None:
        return []
    mapping = mapping or {
        0: ["gmv_lt_1000"],
        1: ["gmv_1000_5000"],
        2: ["gmv_gte_1000"],
        3: ["gmv_gt_5000"],
    }
    try:
        key = int(level)
    except (TypeError, ValueError):
        return []
    return list(mapping.get(key) or mapping.get(str(key)) or [])


def message_to_reply_flags(message: str) -> list[str]:
    """从达人最新消息推导 ``reply_*`` 标志。"""
    text = (message or "").strip()
    if not text:
        return []
    lower = text.lower()
    if lower in {"ok", "sure"}:
        return ["reply_ok"]
    if lower in {"yes", "interested"}:
        return ["reply_yes", "reply_interested"]
    if lower == "ai":
        return ["reply_ai"]
    if lower in {"stop", "unsubscribe", "dnc"}:
        return ["reply_reject"]
    if lower in {"no", "nope", "nah", "pass", "decline", "not interested", "no thanks", "no thank you"}:
        return ["reply_reject"]
    if re.search(r"\b(not interested|no thanks|won'?t|can'?t do)\b", lower):
        return ["reply_reject"]
    normalized = normalize_other_creator_conditions(text) or []
    return [f for f in normalized if f.startswith("reply_")]


def message_to_intent_hint(message: str) -> str | None:
    """粗粒度意图提示；规则见 ``schema/intent/generic.yaml`` + ``schema/intent/<shop>.yaml``。"""
    from hubstudio_python.models.intent_classification import classify_user_message

    result = classify_user_message(message)
    if result.matched_by == "none" or result.intent_category == "GEN":
        return None
    return result.intent_category
