"""按店铺加载 ``restore/<shop>.yaml``，供回复生成阶段还原占位符。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from hubstudio_python.models.schema_layout import (
    discover_restore_shops,
    restore_shop_path,
)


def shop_restore_values_path(shop: str, *, base: Path | None = None) -> Path:
    return restore_shop_path(shop, base=base)


def shops_schema_dir(*, base: Path | None = None) -> Path:
    """已废弃：请用 ``restore_dir()``。"""
    from hubstudio_python.models.schema_layout import restore_dir

    return restore_dir(base=base)


@lru_cache(maxsize=16)
def load_shop_restore_slots(shop: str) -> dict[str, str]:
    path = shop_restore_values_path(shop)
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    slots = data.get("slots") or {}
    if not isinstance(slots, dict):
        return {}
    return {str(k): str(v) for k, v in slots.items() if v is not None and str(v).strip()}


def apply_restore_slots(text: str, shop: str, *, extra: dict[str, str] | None = None) -> str:
    """将 ``{slot_id}`` 替换为店铺配置中的具体值（生成回复时使用）。"""
    values = dict(load_shop_restore_slots(shop))
    if extra:
        values.update(extra)
    out = text
    for key, val in values.items():
        out = out.replace("{" + key + "}", val)
        out = out.replace("{{" + key + "}}", val)
    return out


def all_shop_restore_configs(shops_dir: Path | None = None) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for shop in discover_restore_shops(base=shops_dir.parent if shops_dir else None):
        out[shop] = load_shop_restore_slots(shop)
    return out
