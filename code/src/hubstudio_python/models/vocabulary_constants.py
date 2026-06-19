"""词汇表常量（避免 models ↔ pipelines 循环引用）。"""

from __future__ import annotations

from typing import Any

# 筛选条件 / 店铺业务枚举：只存 shop_extensions，不并入全局 fields.values
SHOP_SCOPED_FIELD_NAMES = frozenset({
    "applicable_shops",
    "creator_type",
    "creator_progress",
    "intent_category",
    "other_creator_conditions",
    "key_information",
    "restore_slots",
})


def generic_values_for_field(field_name: str, base_fields: dict[str, Any]) -> frozenset[str]:
    """通用 seed 中该字段的 baseline 取值（店铺 overlay 不应重复写入）。"""
    if field_name not in SHOP_SCOPED_FIELD_NAMES:
        return frozenset()
    spec = base_fields.get(field_name)
    if not isinstance(spec, dict):
        return frozenset()
    vals = spec.get("values")
    if not isinstance(vals, list):
        return frozenset()
    return frozenset(str(v) for v in vals if v is not None and str(v).strip())


def strip_generic_from_field_spec(
    spec: dict[str, Any],
    generic_values: frozenset[str],
) -> dict[str, Any]:
    """从店铺 field spec 去掉与通用层重复的 values / aliases。"""
    if not generic_values:
        return spec
    out = dict(spec)
    vals = out.get("values")
    if isinstance(vals, list):
        out["values"] = [v for v in vals if str(v) not in generic_values]
    aliases = out.get("aliases")
    if isinstance(aliases, dict):
        out["aliases"] = {
            str(k): v for k, v in aliases.items() if str(k) not in generic_values
        }
    return out
