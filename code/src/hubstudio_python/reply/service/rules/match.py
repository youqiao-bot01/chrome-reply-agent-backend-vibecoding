"""
知识库规则 ↔ 达人外在条件 的 AND 匹配。
"""

from __future__ import annotations

import json
from typing import Any

from hubstudio_python.reply.service.rules.negotiation import (
    PRICE_NEGOTIATION_ROUND_RE,
    rule_uses_a_tier_price_negotiation,
    supports_a_tier_price_negotiation,
)
from hubstudio_python.reply.service.rules.shop_scope import rule_allowed_for_shop
from hubstudio_python.reply.service.rules.schema import (
    CONDITION_FLAG_FIELD,
    CONDITION_SCALAR_FIELDS,
    FIELD_WITHIN_AND,
    FIELD_WITHIN_OR,
    CreatorRuleContext,
    GENERAL,
)

# 请求侧常为 GEN、不应拿来卡规则的维度
_CTX_OPTIONAL_FIELDS: frozenset[str] = frozenset({
    "rule_type",
    "creator_reply_frequency",
    "creator_type_and",
})

# 在线分类 intent 与知识库 chunk intent 的等价组（A 层议价）
_INTENT_EQUIVALENCE_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"How Many I can earn", "CPM Rate"}),
    frozenset({"How Many I can earn", "Flat Fee"}),
    frozenset({"How Many I can earn", "Creator Quote"}),
)

# 仅「不感兴趣 / 拒绝」类情绪在规则侧严格匹配；其余（感兴趣、中性等）为软提示
_STRICT_NOT_INTERESTED_EMOTIONS: frozenset[str] = frozenset({
    "当前聊天主题不感兴趣",
    "目前对产品问题不感兴趣",
    "拒绝合作",
})


def _is_gen(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, list):
        return not value or all(_is_gen(v) for v in value)
    s = str(value).strip()
    return not s or s.upper() == GENERAL


def _as_list(value: object) -> list[str]:
    if value is None or _is_gen(value):
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip() and not _is_gen(v)]
    s = str(value).strip()
    return [s] if s and not _is_gen(s) else []


def _progress_kv_pairs(value: object) -> list[tuple[str, str | None]]:
    """解析 creator_progress：普通枚举或 key=value（如 price_negotiation_rounds=1）。"""
    pairs: list[tuple[str, str | None]] = []
    for token in _as_list(value):
        if "=" in token:
            key, val = token.split("=", 1)
            pairs.append((key.strip().lower(), val.strip()))
        else:
            pairs.append((token, None))
    return pairs


def _creator_progress_match(rule_val: object, ctx_val: object) -> bool:
    """
    达人进度匹配：普通值走子集 AND；``price_negotiation_rounds=N`` 按 N 比较。
    """
    if _is_gen(rule_val):
        return True
    rule_pairs = _progress_kv_pairs(rule_val)
    ctx_pairs = _progress_kv_pairs(ctx_val)
    if not ctx_pairs:
        return False

    ctx_plain = {k for k, v in ctx_pairs if v is None}
    ctx_kv = {k: v for k, v in ctx_pairs if v is not None}

    for rk, rv in rule_pairs:
        if rk == "price_negotiation_rounds":
            ctx_round = ctx_kv.get("price_negotiation_rounds")
            if ctx_round is None:
                ctx_round_val = parse_negotiation_round_from_pairs(ctx_pairs)
                ctx_round = str(ctx_round_val) if ctx_round_val is not None else None
            if rv is None or ctx_round is None or str(rv) != str(ctx_round):
                return False
            continue
        if rv is not None:
            if ctx_kv.get(rk) != rv:
                return False
        elif rk not in ctx_plain and rk not in ctx_kv:
            return False
    return True


def parse_negotiation_round_from_pairs(pairs: list[tuple[str, str | None]]) -> int | None:
    for key, val in pairs:
        if key in {"price_negotiation_rounds", "negotiation_rounds"} and val is not None:
            try:
                return int(val)
            except ValueError:
                return None
    return None


def _creator_type_match(rule_val: object, ctx_val: object, *, shop: str) -> bool:
    """toolant：对外三档与 KB A/B/C-level 同档视为匹配。"""
    if _is_gen(rule_val):
        return True
    rule_items = _as_list(rule_val)
    ctx_items = _as_list(ctx_val)
    if not ctx_items:
        return False
    if shop.strip().lower() == "toolant":
        from hubstudio_python.models.toolant_creator_type import expand_toolant_creator_types

        rule_exp = expand_toolant_creator_types(rule_items)
        ctx_exp = expand_toolant_creator_types(ctx_items)
        return bool(rule_exp & ctx_exp)
    return bool(set(rule_items) & set(ctx_items))


def _scalar_field_match(
    rule_val: object,
    ctx_val: object,
    *,
    within_or: bool,
    shop: str = "",
    field_name: str = "",
) -> bool:
    """规则字段是否被上下文满足。"""
    if _is_gen(rule_val):
        return True
    rule_items = _as_list(rule_val)
    ctx_items = _as_list(ctx_val)
    if not ctx_items:
        return False
    if within_or and field_name == "creator_type":
        return _creator_type_match(rule_val, ctx_val, shop=shop)
    if within_or:
        return bool(set(rule_items) & set(ctx_items))
    return set(rule_items).issubset(set(ctx_items))


def _intent_category_match(rule_val: object, ctx_val: object) -> bool:
    if _scalar_field_match(rule_val, ctx_val, within_or=False):
        return True
    rule_items = _as_list(rule_val)
    ctx_items = _as_list(ctx_val)
    if not rule_items or not ctx_items:
        return False
    for group in _INTENT_EQUIVALENCE_GROUPS:
        if set(rule_items) <= group and set(ctx_items) <= group:
            return True
    return False


def intent_matches_allowed_scope(intent_category: str, allowed: list[str]) -> bool:
    """分类 intent 是否在店铺档位允许列表内（含等价 intent 组）。"""
    ic = str(intent_category or "").strip()
    if not ic or ic.upper() == GENERAL:
        return True
    if not allowed:
        return True
    if ic in allowed:
        return True
    for group in _INTENT_EQUIVALENCE_GROUPS:
        if ic in group and any(alt in allowed for alt in group):
            return True
    return False


def _creator_emotion_match(rule_val: object, ctx_val: object) -> bool:
    """
    情绪匹配：仅「不感兴趣 / 拒绝合作」类规则严格校验。

    - 规则为 GEN → 通过
    - 规则为不感兴趣类 → ctx 须同为不感兴趣类（严格）
    - 规则为感兴趣 / 中性等 → ctx 为 GEN 或未识别时仍可通过（软匹配）
    - ctx 已明确为不感兴趣 → 不匹配「感兴趣」类规则
    """
    if _is_gen(rule_val):
        return True
    rule_items = _as_list(rule_val)
    if not rule_items:
        return True

    rule_strict = set(rule_items) & _STRICT_NOT_INTERESTED_EMOTIONS
    ctx_items = _as_list(ctx_val)
    ctx_strict = set(ctx_items) & _STRICT_NOT_INTERESTED_EMOTIONS

    if rule_strict:
        if not ctx_strict:
            return False
        return bool(rule_strict & ctx_strict)

    if not ctx_items:
        return True

    if ctx_strict:
        return False

    return True


def _flags_match(rule_flags: list[str], ctx_flags: list[str]) -> bool:
    if not rule_flags:
        return True
    return set(rule_flags).issubset(set(ctx_flags or []))


def _rule_flags(rule: dict[str, Any]) -> list[str]:
    raw = rule.get(CONDITION_FLAG_FIELD)
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("["):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            except json.JSONDecodeError:
                pass
        return [p.strip() for p in s.split(",") if p.strip()]
    return []


def matches_rule(rule: dict[str, Any], ctx: CreatorRuleContext | dict[str, Any]) -> bool:
    """
    判断单条 chunks / 结构化记录是否匹配当前外在条件。

    规则侧非 GEN 字段均须被 ctx 满足（跨字段 AND）。
    """
    ctx_dict = ctx.to_match_dict() if isinstance(ctx, CreatorRuleContext) else dict(ctx)

    req_shop = str(
        ctx_dict.get("applicable_shops") or ctx_dict.get("shop") or ""
    ).strip()
    if not req_shop or req_shop.upper() == GENERAL:
        req_shop = str(ctx_dict.get("shop") or "").strip()
    if req_shop and not rule_allowed_for_shop(rule, req_shop):
        return False

    for fname in CONDITION_SCALAR_FIELDS:
        rule_val = rule.get(fname)
        if _is_gen(rule_val):
            continue
        ctx_val = ctx_dict.get(fname)
        if fname == "applicable_shops" and _is_gen(ctx_val):
            alt_shop = ctx_dict.get("shop")
            if alt_shop and not _is_gen(alt_shop):
                ctx_val = alt_shop
        if ctx_val is None or _is_gen(ctx_val):
            if fname in _CTX_OPTIONAL_FIELDS:
                continue
        if fname == "creator_progress":
            if not _creator_progress_match(rule_val, ctx_val):
                return False
            continue
        if fname == "intent_category":
            if not _intent_category_match(rule_val, ctx_val):
                return False
            continue
        if fname == "creator_emotion":
            if not _creator_emotion_match(rule_val, ctx_val):
                return False
            continue
        within_or = fname in FIELD_WITHIN_OR
        if not _scalar_field_match(
            rule_val,
            ctx_val,
            within_or=within_or,
            shop=req_shop,
            field_name=fname,
        ):
            return False

    rule_flags = _rule_flags(rule)
    ctx_flags = ctx_dict.get(CONDITION_FLAG_FIELD) or []
    if isinstance(ctx_flags, str):
        ctx_flags = _rule_flags({CONDITION_FLAG_FIELD: ctx_flags})
    if not _flags_match(rule_flags, list(ctx_flags)):
        return False

    if rule_uses_a_tier_price_negotiation(
        creator_progress=rule.get("creator_progress"),
        other_creator_conditions=rule_flags,
    ) and not supports_a_tier_price_negotiation(ctx_dict):
        return False

    return True


def filter_matching_rules(
    rules: list[dict[str, Any]],
    ctx: CreatorRuleContext | dict[str, Any],
) -> list[dict[str, Any]]:
    return [r for r in rules if matches_rule(r, ctx)]
