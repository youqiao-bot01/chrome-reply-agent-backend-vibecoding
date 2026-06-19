"""
规则匹配上下文：与 ``*.chunks.json`` 条件字段同构，由数据库 + 会话状态组装。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

GENERAL = "GEN"

# 与 vocabulary.yaml 中 condition: true 且 queryable 的字段对齐
CONDITION_SCALAR_FIELDS: tuple[str, ...] = (
    "applicable_shops",
    "creator_type",
    "creator_type_and",
    "creator_progress",
    "intent_category",
    "creator_emotion",
    "creator_reply_frequency",
    "rule_type",
)

CONDITION_FLAG_FIELD = "other_creator_conditions"

# 字段内多值逻辑（与 _meta.condition_matching.within_field 一致）
FIELD_WITHIN_OR: frozenset[str] = frozenset({"creator_type"})
FIELD_WITHIN_AND: frozenset[str] = frozenset({"creator_type_and", CONDITION_FLAG_FIELD})


@dataclass
class CreatorRuleContext:
    """
    达人当前外在条件快照，用于与知识库规则做 AND 匹配。

    标量字段默认 ``GEN`` 表示该维度未知/不限；``other_creator_conditions`` 为已推导的标志列表。
    ``intent_category`` 通常由 NLU/向量检索填入，数据库一般不提供。
    """

    applicable_shops: str = GENERAL
    creator_type: str | list[str] = GENERAL
    creator_type_and: str | list[str] = GENERAL
    creator_progress: str | list[str] = GENERAL
    intent_category: str | None = None
    creator_emotion: str = GENERAL
    creator_reply_frequency: str = GENERAL
    rule_type: str = GENERAL
    other_creator_conditions: list[str] = field(default_factory=list)

    # 溯源（不参与规则匹配）
    creator_name: str = ""
    shop: str = ""
    monthly_gmv: float | None = None
    avg_video_views: int | None = None
    latest_message: str = ""
    raw_profile: dict[str, Any] = field(default_factory=dict)
    raw_message: dict[str, Any] = field(default_factory=dict)

    def to_match_dict(self) -> dict[str, Any]:
        """供 ``matches_rule`` / Chroma 预过滤使用的扁平 dict。"""
        out: dict[str, Any] = {}
        for fname in CONDITION_SCALAR_FIELDS:
            val = getattr(self, fname)
            if val is not None and val != "":
                out[fname] = val
        if self.other_creator_conditions:
            out[CONDITION_FLAG_FIELD] = list(self.other_creator_conditions)
        shop = str(self.shop or "").strip()
        if shop and (
            not out.get("applicable_shops")
            or str(out.get("applicable_shops") or "").upper() == GENERAL
        ):
            out["shop"] = shop
        return out

    def to_json_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("raw_profile", None)
        d.pop("raw_message", None)
        return {k: v for k, v in d.items() if v not in (None, "", [], {})}
