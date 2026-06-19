"""读取 ``shop_tier_intent_hierarchy.json``，按店铺 × 档位 × 进度查可用意图。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from hubstudio_python.config import load_paths

GENERAL = "GEN"


def _norm_key(value: str) -> str:
    return str(value or "").strip() or GENERAL


def _find_shop_key(tree: dict[str, Any], shop: str) -> str | None:
    if not shop:
        return None
    if shop in tree:
        return shop
    lower = shop.strip().casefold()
    for key in tree:
        if str(key).strip().casefold() == lower:
            return key
    return None


def _find_tier_key(tier_map: dict[str, Any], creator_type: str) -> str | None:
    ct = _norm_key(creator_type)
    if ct in tier_map:
        return ct
    lower = ct.casefold()
    for key in tier_map:
        if str(key).strip().casefold() == lower:
            return key
    if GENERAL in tier_map:
        return GENERAL
    return None


def _find_progress_key(prog_map: dict[str, Any], creator_progress: str) -> str | None:
    cp = _norm_key(creator_progress)
    if cp in prog_map:
        return cp
    lower = cp.casefold()
    for key in prog_map:
        if str(key).strip().casefold() == lower:
            return key
    if GENERAL in prog_map:
        return GENERAL
    return None


@lru_cache(maxsize=1)
def _load_hierarchy_tree() -> dict[str, Any]:
    paths = load_paths()
    path = paths.runtime_kb_dir / "shop_tier_intent_hierarchy.json"
    if not path.is_file():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    tree = raw.get("tree") if isinstance(raw, dict) else None
    return tree if isinstance(tree, dict) else {}


def allowed_intents_for_scope(
    shop: str,
    creator_type: str,
    *,
    creator_progress: str = GENERAL,
) -> list[str]:
    """
    店铺 × 档位 × 进度下的可用意图（含向 ``GEN`` 父级回退并集，去重排序）。
    """
    tree = _load_hierarchy_tree()
    shop_key = _find_shop_key(tree, shop)
    if not shop_key:
        return []

    tier_map = tree.get(shop_key)
    if not isinstance(tier_map, dict):
        return []

    paths_to_try: list[tuple[str, str, str]] = []
    tier_key = _find_tier_key(tier_map, creator_type)
    prog_raw = _norm_key(creator_progress)

    if tier_key:
        prog_map = tier_map.get(tier_key)
        if isinstance(prog_map, dict):
            prog_key = _find_progress_key(prog_map, prog_raw)
            if prog_key:
                paths_to_try.append((shop_key, tier_key, prog_key))
            if prog_key != GENERAL and GENERAL in prog_map:
                paths_to_try.append((shop_key, tier_key, GENERAL))

    if tier_key != GENERAL and GENERAL in tier_map:
        gen_prog = tier_map[GENERAL]
        if isinstance(gen_prog, dict):
            prog_key = _find_progress_key(gen_prog, prog_raw)
            if prog_key:
                paths_to_try.append((shop_key, GENERAL, prog_key))
            if GENERAL in gen_prog:
                paths_to_try.append((shop_key, GENERAL, GENERAL))

    global_tree = tree.get(GENERAL)
    if isinstance(global_tree, dict) and GENERAL in global_tree:
        gprog = global_tree[GENERAL]
        if isinstance(gprog, dict) and GENERAL in gprog:
            paths_to_try.append((GENERAL, GENERAL, GENERAL))

    seen: set[str] = set()
    out: list[str] = []
    for s, t, p in paths_to_try:
        tier_map2 = tree.get(s)
        if not isinstance(tier_map2, dict):
            continue
        prog_map2 = tier_map2.get(t)
        if not isinstance(prog_map2, dict):
            continue
        intents = prog_map2.get(p)
        if not isinstance(intents, list):
            continue
        for item in intents:
            ic = str(item).strip()
            if ic and ic not in seen:
                seen.add(ic)
                out.append(ic)
    return out


def intent_in_scope(
    intent_category: str,
    shop: str,
    creator_type: str,
    *,
    creator_progress: str = GENERAL,
) -> bool:
    ic = str(intent_category or "").strip()
    if not ic or ic.upper() == GENERAL:
        return True
    allowed = allowed_intents_for_scope(
        shop, creator_type, creator_progress=creator_progress
    )
    if not allowed:
        return True
    if ic in allowed:
        return True
    from hubstudio_python.reply.service.rules.match import intent_matches_allowed_scope

    return intent_matches_allowed_scope(ic, allowed)
