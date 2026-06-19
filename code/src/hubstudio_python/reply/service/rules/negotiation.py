"""
讨价还价轮次 ↔ Playbook 谈判节点（priority_node / prev_node）互推。

**仅 toolant 头部档（A-level 等）** 走 A1→A4 多轮议价；
B/C 等档位不接受报价即结束，勿写入 ``price_negotiation_rounds`` / ``priority_node:A*``。

Excel 侧 ``creator_progress`` 仅含 Excel「三级分类-达人进度」枚举；  
Playbook / 在线会话用 ``other_creator_conditions`` 中的 ``priority_node:*`` / ``prev_node:*``。  
运行时可将轮次 N 推导为节点 flags（``conditions/negotiation.py``），**不**写入词汇表 ``creator_progress``。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from hubstudio_python.reply.service.rules.schema import CreatorRuleContext, GENERAL

NEGOTIATION_ROUND_RE = re.compile(
    r"(?:price_)?negotiation_rounds\s*=\s*(\d+)",
    re.IGNORECASE,
)
# 兼容旧名
PRICE_NEGOTIATION_ROUND_RE = NEGOTIATION_ROUND_RE

DEFAULT_A_TIER_CHAIN: tuple[str, ...] = ("A1", "A2", "A3", "A4")

# 多轮议价仅 toolant + 头部档；B/C 走样品/门户等节点，不做 A1–A4 价格阶梯
PRICE_NEGOTIATION_SHOPS: frozenset[str] = frozenset({"toolant"})
A_TIER_NEGOTIATION_CREATOR_TYPES: frozenset[str] = frozenset(
    {"A-level", "S-level", "SS-level", "高GMV达人"}
)
A_TIER_NEGOTIATION_NODE_PREFIXES: tuple[str, ...] = ("priority_node:A", "prev_node:A")

A_TIER_ROUND_TABLE: tuple[tuple[int, str, str], ...] = (
    (0, "纯佣 A1", "priority_node:A1"),
    (1, "CPM A2（上一档被拒）", "priority_node:A2 + prev_node:A1"),
    (2, "一口价 A3", "priority_node:A3 + prev_node:A2"),
    (3, "收报价 A4", "priority_node:A4 + prev_node:A3"),
)


@dataclass(frozen=True)
class NegotiationNodes:
    """某一轮次对应的 Playbook 节点。"""

    round: int
    priority_node: str
    prev_node: str | None


def parse_negotiation_round(value: object) -> int | None:
    """从 ``creator_progress`` 字符串或列表解析 ``price_negotiation_rounds=N``。"""
    if value is None:
        return None
    items: list[str]
    if isinstance(value, list):
        items = [str(v).strip() for v in value if str(v).strip()]
    else:
        s = str(value).strip()
        items = [s] if s else []
    for item in items:
        m = NEGOTIATION_ROUND_RE.search(item)
        if m:
            return int(m.group(1))
    return None


def format_negotiation_round(round_num: int) -> str:
    return f"price_negotiation_rounds={round_num}"


def nodes_from_round(
    round_num: int,
    *,
    chain: tuple[str, ...] = DEFAULT_A_TIER_CHAIN,
) -> NegotiationNodes | None:
    if round_num < 0 or round_num >= len(chain):
        return None
    priority = chain[round_num]
    prev = chain[round_num - 1] if round_num > 0 else None
    return NegotiationNodes(round=round_num, priority_node=priority, prev_node=prev)


def round_from_priority_node(
    priority_node: str,
    *,
    chain: tuple[str, ...] = DEFAULT_A_TIER_CHAIN,
) -> int | None:
    token = str(priority_node or "").strip().upper()
    if not token:
        return None
    try:
        return chain.index(token)
    except ValueError:
        return None


def negotiation_node_flags(
    *,
    priority_node: str | None = None,
    prev_node: str | None = None,
) -> list[str]:
    flags: list[str] = []
    if priority_node:
        flags.append(f"priority_node:{priority_node.upper()}")
    if prev_node:
        flags.append(f"prev_node:{prev_node.upper()}")
    return flags


def _as_str_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    s = str(value).strip()
    return [s] if s else []


def _is_gen_token(value: object) -> bool:
    s = str(value or "").strip()
    return not s or s.upper() == GENERAL


def _shop_from_context(ctx: CreatorRuleContext | dict[str, Any]) -> str | None:
    if isinstance(ctx, CreatorRuleContext):
        for val in (ctx.shop, ctx.applicable_shops):
            for token in _as_str_list(val):
                if not _is_gen_token(token):
                    return token.strip().lower()
        return None
    for key in ("shop", "applicable_shops"):
        for token in _as_str_list(ctx.get(key)):
            if not _is_gen_token(token):
                return token.strip().lower()
    return None


def _creator_types_from_context(ctx: CreatorRuleContext | dict[str, Any]) -> set[str]:
    if isinstance(ctx, CreatorRuleContext):
        raw = ctx.creator_type
    else:
        raw = ctx.get("creator_type")
    return {t for t in _as_str_list(raw) if not _is_gen_token(t)}


def supports_a_tier_price_negotiation(ctx: CreatorRuleContext | dict[str, Any]) -> bool:
    """是否适用 toolant 头部档 A1–A4 多轮议价（否则勿桥接轮次/节点）。"""
    shop = _shop_from_context(ctx)
    if shop not in PRICE_NEGOTIATION_SHOPS:
        return False
    tiers = _creator_types_from_context(ctx)
    if not tiers:
        return False
    from hubstudio_python.models.toolant_creator_type import expand_toolant_creator_types

    expanded: set[str] = set()
    for t in tiers:
        expanded.update(expand_toolant_creator_types(t))
    return bool(expanded & A_TIER_NEGOTIATION_CREATOR_TYPES)


def rule_uses_a_tier_price_negotiation(
    *,
    creator_progress: object = None,
    other_creator_conditions: object = None,
) -> bool:
    """规则是否依赖 A 档多轮议价字段（轮次或 A1–A4 节点）。"""
    for token in _as_str_list(creator_progress):
        if PRICE_NEGOTIATION_ROUND_RE.search(token):
            return True
    for flag in _as_str_list(other_creator_conditions):
        if any(flag.startswith(prefix) for prefix in A_TIER_NEGOTIATION_NODE_PREFIXES):
            return True
    return False


def negotiation_meta_for_shop(mapping: dict[str, Any] | None, shop: str = "toolant") -> dict[str, Any]:
    """从词汇表 ``_meta`` 读取单店 negotiation（``shop_extensions.<shop>``）。"""
    if not mapping:
        return {}
    legacy = mapping.get("negotiation")
    if isinstance(legacy, dict) and legacy:
        return legacy
    ext = (mapping.get("shop_extensions") or {}).get(shop) or {}
    if isinstance(ext, dict):
        neg = ext.get("negotiation")
        return neg if isinstance(neg, dict) else {}
    return {}


def load_negotiation_chain(
    mapping: dict[str, Any] | None,
    *,
    shop: str = "toolant",
) -> tuple[str, ...]:
    neg = negotiation_meta_for_shop(mapping, shop)
    chain = neg.get("a_tier_round_to_priority")
    if isinstance(chain, list) and chain:
        return tuple(str(x).upper() for x in chain)
    return DEFAULT_A_TIER_CHAIN


def resolve_negotiation_state(
    *,
    negotiation_round: int | None = None,
    priority_node: str | None = None,
    prev_node: str | None = None,
    creator_progress: object = None,
    chain: tuple[str, ...] = DEFAULT_A_TIER_CHAIN,
) -> tuple[int | None, str | None, str | None, str | None]:
    """
    统一解析轮次与节点。

    返回 ``(round, priority, prev, progress_str)``；``progress_str`` 仅在有轮次时写入。
    """
    rnd = negotiation_round
    if rnd is None:
        rnd = parse_negotiation_round(creator_progress)
    pri = str(priority_node).strip().upper() if priority_node else None
    prev = str(prev_node).strip().upper() if prev_node else None

    if rnd is not None and not pri:
        nodes = nodes_from_round(rnd, chain=chain)
        if nodes:
            pri = nodes.priority_node
            if prev is None:
                prev = nodes.prev_node

    if rnd is None and pri:
        rnd = round_from_priority_node(pri, chain=chain)
        if prev is None and rnd is not None:
            nodes = nodes_from_round(rnd, chain=chain)
            if nodes:
                prev = nodes.prev_node

    progress_str = format_negotiation_round(rnd) if rnd is not None else None
    return rnd, pri, prev, progress_str


def apply_negotiation_to_context(
    ctx: CreatorRuleContext,
    *,
    negotiation_round: int | None = None,
    priority_node: str | None = None,
    prev_node: str | None = None,
    mapping: dict[str, Any] | None = None,
) -> bool:
    """
    将会话谈判状态写入 ``CreatorRuleContext``（进度 + 节点 flags）。

    仅 ``supports_a_tier_price_negotiation`` 为真时生效；否则 no-op 并返回 ``False``。
    """
    if not supports_a_tier_price_negotiation(ctx):
        return False
    shop = _shop_from_context(ctx) or "toolant"
    chain = load_negotiation_chain(mapping, shop=shop)
    rnd, pri, prev, progress_str = resolve_negotiation_state(
        negotiation_round=negotiation_round,
        priority_node=priority_node,
        prev_node=prev_node,
        creator_progress=ctx.creator_progress,
        chain=chain,
    )

    if progress_str:
        existing = ctx.creator_progress
        if isinstance(existing, list):
            kept = [p for p in existing if not PRICE_NEGOTIATION_ROUND_RE.search(str(p))]
            ctx.creator_progress = kept + [progress_str]
        elif existing and not _is_gen_progress(existing):
            ctx.creator_progress = [str(existing), progress_str]
        else:
            ctx.creator_progress = progress_str

    flags = list(ctx.other_creator_conditions or [])
    for f in negotiation_node_flags(priority_node=pri, prev_node=prev):
        if f not in flags:
            flags.append(f)
    ctx.other_creator_conditions = flags
    return True


def _is_gen_progress(value: object) -> bool:
    s = str(value or "").strip()
    return not s or s.upper() == "GEN"
