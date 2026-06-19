"""
由 ``CreatorRuleContext`` 构建 Chroma metadata where 子句（精确维度；flags 需后过滤）。
"""

from __future__ import annotations

from typing import Any

from hubstudio_python.reply.service.rules.schema import CONDITION_SCALAR_FIELDS, CreatorRuleContext, GENERAL


def build_chroma_where(ctx: CreatorRuleContext | dict[str, Any]) -> dict[str, Any]:
    """
    构建 Chroma ``where`` 条件。

    说明：
    - 仅包含标量 enum 字段；``other_creator_conditions`` 在 Chroma 中为 JSON 字符串，需 ``matches_rule`` 二次过滤。
    - ``GEN`` 维度不写入 where（表示不限）。
    """
    data = ctx.to_match_dict() if isinstance(ctx, CreatorRuleContext) else dict(ctx)
    clauses: list[dict[str, Any]] = []

    for fname in CONDITION_SCALAR_FIELDS:
        val = data.get(fname)
        if val is None:
            continue
        if isinstance(val, list):
            if not val:
                continue
            if len(val) == 1:
                clauses.append({fname: {"$eq": val[0]}})
            else:
                clauses.append({fname: {"$in": val}})
        else:
            s = str(val).strip()
            if s and s.upper() != GENERAL:
                clauses.append({fname: {"$eq": s}})

    if not clauses:
        return {}
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}
