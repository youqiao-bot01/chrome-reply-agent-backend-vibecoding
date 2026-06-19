"""加载 ``gen.txt`` 构建出的 GEN 全局策略，供在线回复始终注入。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from hubstudio_python.config import load_paths

_POLICY_RULE_TYPE = "策略说明"
_GEN_SHOPS = frozenset({"GEN", "gen", "generic"})


def _is_gen_policy_record(rec: dict[str, Any]) -> bool:
    if str(rec.get("rule_type") or "").strip() != _POLICY_RULE_TYPE:
        return False
    shops = str(rec.get("applicable_shops") or "GEN").strip()
    return shops.upper() in _GEN_SHOPS or shops == ""


def _body_from_record(rec: dict[str, Any]) -> str:
    return str(rec.get("encontent") or rec.get("content") or "").strip()


@lru_cache(maxsize=1)
def load_gen_policy_records() -> tuple[dict[str, Any], ...]:
    paths = load_paths()
    chunk_path = paths.runtime_kb_dir / "gen.chunks.json"
    if not chunk_path.is_file():
        return ()
    raw = json.loads(chunk_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return ()
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict) and _is_gen_policy_record(item):
            body = _body_from_record(item)
            if body:
                out.append(item)
    out.sort(key=lambda r: str(r.get("id") or ""))
    return tuple(out)


def format_global_policy_for_prompt(*, shop: str = "") -> str:
    """格式化为 system/user 注入块；``shop`` 非空时标注当前店以便模型区分通用 vs 店规。"""
    records = load_gen_policy_records()
    if not records:
        return ""
    lines = [
        "### Global policy (GEN — all shops, mandatory context)",
        "These are cross-shop seller rules (not a reply script). Follow them when generating any reply.",
    ]
    if shop.strip():
        lines.append(f"Current request shop: {shop.strip()}")
    lines.append("")
    for rec in records:
        rid = str(rec.get("id") or "")
        purpose = str(rec.get("answer_purpose") or "").strip()
        body = _body_from_record(rec)
        header = f"#### {rid}"
        if purpose:
            header += f" — {purpose}"
        lines.extend([header, body, ""])
    return "\n".join(lines).strip()


def reload_global_policy_cache() -> None:
    load_gen_policy_records.cache_clear()
