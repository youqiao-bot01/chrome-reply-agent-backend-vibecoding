"""
Playbook 结构化输出：可查询字段标志与 prose → flag 解析。

允许取值以 ``knowledge_chunk_vocabulary.yaml`` 为准；店铺筛选条件在
``_meta.shop_extensions.<shop>.condition_fields``。
"""

from __future__ import annotations

import re
from typing import Any

_PRIORITY_NODES = frozenset({"A1", "A2", "A3", "A4", "B1", "B2", "B3", "C1", "C2"})

_GMV_PROSE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"gmv\s*>\s*\$?\s*5", re.I), "gmv_gt_5000"),
    (re.compile(r"gmv\s*≥\s*\$?\s*1\s*k|gmv\s*>=\s*\$?\s*1\s*k|gmv_gte", re.I), "gmv_gte_1000"),
    (re.compile(r"gmv\s*<\s*\$?\s*1\s*k|gmv\s*lt", re.I), "gmv_lt_1000"),
    (re.compile(r"1\s*,?\s*000\s*≤.*gmv.*≤.*5\s*,?\s*000|1000.*5000", re.I), "gmv_1000_5000"),
    (re.compile(r"monthly\s+gmv\s*>\s*\$?\s*5", re.I), "gmv_gt_5000"),
]

_NODE_PROSE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"rejected?\s+(?:pure\s+commission\s+)?\(?(A1)\)?|prev.*\bA1\b.*reject", re.I), "prev_node:A1"),
    (re.compile(r"rejected?\s+\(?(A2)\)?|prev.*\bA2\b", re.I), "prev_node:A2"),
    (re.compile(r"rejected?\s+\(?(A3)\)?|prev.*\bA3\b", re.I), "prev_node:A3"),
    (re.compile(r"rejected?\s+all\s+prior|\(A1-A3\)", re.I), "prev_node:A3"),
    (re.compile(r"\b(A1)\b.*(?:open|pure|commission)", re.I), "priority_node:A1"),
    (re.compile(r"\b(A2)\b.*cpm|cpm.*\b(A2)\b", re.I), "priority_node:A2"),
    (re.compile(r"\b(A3)\b.*flat|flat fee.*\b(A3)\b", re.I), "priority_node:A3"),
    (re.compile(r"\b(A4)\b|creator.*quote|bottom.?line", re.I), "priority_node:A4"),
    (re.compile(r"\b(B1)\b|sample.?interest|candidate pool", re.I), "priority_node:B1"),
    (re.compile(r"\b(B2)\b|sample.?request|shipping address", re.I), "priority_node:B2"),
    (re.compile(r"\b(C1)\b|ai ugc interest", re.I), "priority_node:C1"),
    (re.compile(r"\b(C2)\b|portal link|enroll", re.I), "priority_node:C2"),
]

_REPLY_PROSE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"replied\s+'?OK'|accept.*pure commission|reply.*\bok\b", re.I), "reply_ok"),
    (re.compile(r"replied\s+'?Sure'|\breply_sure\b", re.I), "reply_sure"),
    (re.compile(r"\bYes\b|\bInterested\b|strong interest", re.I), "reply_interested"),
    (re.compile(r"rejected|declined|refused", re.I), "reply_reject"),
    (re.compile(r"provides?\s+a\s+quote|bottom.?line|minimum rate", re.I), "reply_quote"),
    (re.compile(r"replied\s+'?AI'|\bAI\b.*trigger", re.I), "reply_ai"),
    (re.compile(r"asked for flat fee|flat fee only", re.I), "reply_quote"),
]

_MISC_PROSE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"b.?tier not selected|b\s*层未被选中", re.I), "b_tier_not_selected"),
    (re.compile(r"skip\s+a2", re.I), "skip_a2"),
    (re.compile(r"whatsapp|\bwa\b", re.I), "channel_wa"),
    (re.compile(r"portal", re.I), "channel_portal"),
    (re.compile(r"store backend|店铺后台", re.I), "channel_store"),
]


def _vocabulary():
    from hubstudio_python.models.knowledge_chunk_vocabulary import get_vocabulary

    return get_vocabulary()


def allowed_other_creator_condition_flags(shop: str | None = None) -> frozenset[str]:
    return _vocabulary().flag_values_for_shop(shop)


def allowed_creator_types(shop: str | None = None) -> frozenset[str]:
    return _vocabulary().allowed_values("creator_type", shop)


def _shop_from_record(record: dict[str, Any]) -> str | None:
    shop = record.get("applicable_shops")
    if isinstance(shop, list):
        shop = shop[0] if shop else None
    s = str(shop or "").strip()
    if s and s.upper() != "GEN":
        return s
    source = str(record.get("source_file") or "").replace("\\", "/")
    parts = [p for p in source.split("/") if p and p not in {".", ".."}]
    return parts[0] if len(parts) >= 2 else None


def _split_tokens(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            out.extend(_split_tokens(item))
        return out
    s = str(raw).strip()
    if not s:
        return []
    parts = re.split(r"[,，;；\n]+", s)
    return [p.strip() for p in parts if p.strip()]


def _flags_from_prose(text: str) -> list[str]:
    found: list[str] = []
    for patterns in (
        _GMV_PROSE_PATTERNS,
        _NODE_PROSE_PATTERNS,
        _REPLY_PROSE_PATTERNS,
        _MISC_PROSE_PATTERNS,
    ):
        for pattern, flag in patterns:
            if pattern.search(text):
                found.append(flag)
    return found


def canonical_creator_type(raw: object, shop: str | None = None) -> str:
    vocab = _vocabulary()
    s = str(raw or "").strip()
    if not s:
        return "GEN"
    canon = vocab.canonicalize("creator_type", s, default="GEN", shop=shop)
    return canon or "GEN"


def normalize_other_creator_conditions(raw: object, shop: str | None = None) -> list[str] | None:
    """将描述或混合文本转为词汇表允许的标志数组。"""
    allowed = allowed_other_creator_condition_flags(shop)
    tokens = _split_tokens(raw)
    flags: list[str] = []
    seen: set[str] = set()

    def _add(flag: str) -> None:
        if flag in allowed and flag not in seen:
            seen.add(flag)
            flags.append(flag)

    for token in tokens:
        if token in allowed:
            _add(token)
            continue
        if token.upper() in _PRIORITY_NODES:
            candidate = f"priority_node:{token.upper()}"
            if candidate in allowed:
                _add(candidate)
            continue
        for flag in _flags_from_prose(token):
            _add(flag)

    if not flags and isinstance(raw, str) and raw.strip():
        for flag in _flags_from_prose(raw):
            _add(flag)

    return flags or None


def format_condition_vocabulary_for_prompt(shop: str | None = None) -> str:
    return _vocabulary().format_for_prompt(shop=shop)


def normalize_playbook_record_fields(record: dict[str, Any]) -> dict[str, Any]:
    """规范化单条 Playbook 结构化记录的可查询字段（词汇表收敛）。"""
    vocab = _vocabulary()
    out = dict(record)
    shop = _shop_from_record(out)

    if "intent_category" in out:
        ic = vocab.canonicalize_intent(out["intent_category"], shop=shop)
        if ic:
            out["intent_category"] = ic
        else:
            out.pop("intent_category", None)

    for fname in (
        "creator_type",
        "creator_progress",
        "creator_emotion",
        "creator_reply_frequency",
        "rule_type",
        "applicable_shops",
    ):
        if fname not in out:
            continue
        raw = out[fname]
        if isinstance(raw, list):
            canon_list = []
            for item in raw:
                c = vocab.canonicalize(fname, item, shop=shop)
                if c:
                    canon_list.append(c)
            if canon_list:
                out[fname] = canon_list if len(canon_list) > 1 else canon_list[0]
            else:
                out.pop(fname, None)
        else:
            c = vocab.canonicalize(fname, raw, shop=shop)
            if c:
                out[fname] = c
            else:
                out.pop(fname, None)

    if "other_creator_conditions" in out:
        flags = normalize_other_creator_conditions(out["other_creator_conditions"], shop=shop)
        if flags:
            out["other_creator_conditions"] = flags
        else:
            out.pop("other_creator_conditions", None)

    from hubstudio_python.models.knowledge_desensitize import resolve_key_information_for_record

    ki = resolve_key_information_for_record(out, shop)
    if ki:
        out["key_information"] = ki
    else:
        out.pop("key_information", None)

    return out
