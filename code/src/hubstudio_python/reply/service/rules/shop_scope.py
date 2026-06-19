"""
知识库规则 ↔ 请求店铺 作用域：仅允许「本店 + GEN 通用」，禁止跨店引用。
"""

from __future__ import annotations

import json
from typing import Any

from hubstudio_python.reply.service.rules.schema import GENERAL

_GENERAL_SHOPS = frozenset({"", "gen", "generic"})


def _norm_shop(name: str) -> str:
    return (name or "").strip().casefold()


def _is_general_shop_tag(tag: str) -> bool:
    n = _norm_shop(tag)
    return n in _GENERAL_SHOPS or n == GENERAL.casefold()


def parse_applicable_shops(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    s = str(raw).strip()
    if not s:
        return []
    if s.startswith("["):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            pass
    return [s]


def shop_from_source_file(source_file: object) -> str | None:
    """从 ``source_file`` 首段推断店铺（``toolant/...``、``Linknlatch/...``）。"""
    sf = str(source_file or "").replace("\\", "/").strip()
    if not sf or "/" not in sf:
        return None
    first = sf.split("/", 1)[0].strip()
    return first or None


def rule_allowed_for_shop(rule: dict[str, Any], request_shop: str) -> bool:
    """
    规则是否可用于当前请求店铺。

    - ``applicable_shops`` 含请求店名 → 允许
    - 仅 ``GEN`` / 空（通用）→ 允许任意店
    - 含其它店名、不含请求店 → **拒绝**
    - 无 ``applicable_shops`` 时看 ``source_file`` 首段；仍无法归属 → 拒绝
    """
    req = _norm_shop(request_shop)
    if not req:
        return False

    tags = parse_applicable_shops(rule.get("applicable_shops"))
    if tags:
        norm_tags = [_norm_shop(t) for t in tags]
        if all(_is_general_shop_tag(t) for t in tags):
            return True
        if req in norm_tags:
            return True
        return False

    src = shop_from_source_file(rule.get("source_file"))
    if src:
        if _is_general_shop_tag(src):
            return True
        return _norm_shop(src) == req

    return False


def filter_rules_for_shop(
    rules: list[dict[str, Any]],
    request_shop: str,
) -> list[dict[str, Any]]:
    return [r for r in rules if rule_allowed_for_shop(r, request_shop)]
