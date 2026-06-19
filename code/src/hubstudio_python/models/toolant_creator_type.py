"""toolant 达人档位：对外称呼 ↔ 知识库 A/B/C-level。"""

from __future__ import annotations

# 对外三档（前端 / API creatorType）
TOOLANT_CREATOR_TYPE_HIGH_GMV = "高GMV达人"
TOOLANT_CREATOR_TYPE_PURE_COMMISSION = "纯佣带货达人"
TOOLANT_CREATOR_TYPE_AI_UGC = "AI UGC达人"

TOOLANT_USER_FACING_TYPES: frozenset[str] = frozenset(
    {
        TOOLANT_CREATOR_TYPE_HIGH_GMV,
        TOOLANT_CREATOR_TYPE_PURE_COMMISSION,
        TOOLANT_CREATOR_TYPE_AI_UGC,
    }
)

# 知识库 playbook / add1 仍用 A/B/C-level；匹配时视为同档
TOOLANT_CREATOR_TYPE_EQUIVALENCE: tuple[frozenset[str], ...] = (
    frozenset({TOOLANT_CREATOR_TYPE_HIGH_GMV, "A-level", "S-level", "SS-level"}),
    frozenset({TOOLANT_CREATOR_TYPE_PURE_COMMISSION, "纯佣寄样达人", "B-level"}),
    frozenset({TOOLANT_CREATOR_TYPE_AI_UGC, "C-level"}),
)

_RAW_TO_CANONICAL: dict[str, str] = {}
for _group in TOOLANT_CREATOR_TYPE_EQUIVALENCE:
    _canon = next((x for x in _group if x in TOOLANT_USER_FACING_TYPES), None)
    if not _canon:
        continue
    for _token in _group:
        _RAW_TO_CANONICAL[_token.casefold()] = _canon


def _as_type_list(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    s = str(value or "").strip()
    return [s] if s else []


def expand_toolant_creator_types(value: str | list[str]) -> set[str]:
    """展开为同档所有别名（含 A/B/C-level），供 matches_rule 使用。"""
    out: set[str] = set()
    for item in _as_type_list(value):
        out.add(item)
        for group in TOOLANT_CREATOR_TYPE_EQUIVALENCE:
            if item in group:
                out.update(group)
    return out


def canonical_toolant_creator_type(raw: object) -> str | None:
    """任意写法 → 对外 canonical（高GMV / 纯佣带货 / AI UGC）。"""
    s = str(raw or "").strip()
    if not s:
        return None
    return _RAW_TO_CANONICAL.get(s.casefold(), s if s in TOOLANT_USER_FACING_TYPES else None)


def normalize_toolant_creator_type(raw: object) -> str | list[str]:
    """
    请求侧 creator_type 规范为对外三档之一；未知则原样返回。
    列表时逐项 canonical，去重保序。
    """
    if isinstance(raw, list):
        out: list[str] = []
        seen: set[str] = set()
        for item in raw:
            canon = canonical_toolant_creator_type(item) or str(item).strip()
            if canon and canon not in seen:
                seen.add(canon)
                out.append(canon)
        if not out:
            return "GEN"
        return out if len(out) > 1 else out[0]
    canon = canonical_toolant_creator_type(raw)
    if canon:
        return canon
    s = str(raw or "").strip()
    return s or "GEN"


def is_toolant_head_tier(creator_type: object) -> bool:
    """高 GMV 头达档（可走 A1–A4 议价）。"""
    expanded = expand_toolant_creator_types(_as_type_list(str(creator_type or "")))
    return bool(expanded & {"高GMV达人", "A-level", "S-level", "SS-level"})


def is_toolant_pure_commission_tier(creator_type: object) -> bool:
    expanded = expand_toolant_creator_types(_as_type_list(str(creator_type or "")))
    return bool(expanded & {TOOLANT_CREATOR_TYPE_PURE_COMMISSION, "纯佣寄样达人", "B-level"})


def is_toolant_ai_ugc_tier(creator_type: object) -> bool:
    expanded = expand_toolant_creator_types(_as_type_list(str(creator_type or "")))
    return bool(expanded & {TOOLANT_CREATOR_TYPE_AI_UGC, "C-level"})
