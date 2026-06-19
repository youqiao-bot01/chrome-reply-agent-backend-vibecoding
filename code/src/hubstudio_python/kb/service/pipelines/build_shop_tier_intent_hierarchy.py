"""
**固定三层**（不展开 ``creator_type_and`` 的无限嵌套）：**店铺 → 档位（``creator_type``）→ 达人进度（``creator_progress``）→ 意图数组**。

- 档位键名与表格 **原文一致**：``A-level`` / ``B-level`` / ``S-level`` / ``SS-level``、``AI UGC达人``、``高GMV达人``、``纯佣寄样达人``、``GEN``。
- **达人进度** 键与表头「三级分类-达人进度」写入 ``creator_progress`` 的取值一致；空 / ``GEN`` 用 ``GEN``；多值与店铺、档位做笛卡尔。
- **不在此文件做「通用池展开」**：``GEN/GEN/GEN`` 与其它路径一样，只挂在对应键下；使用方按需 **GEN + 具体条件** 并列取集、去重，并可 **逐层回退到父级 GEN** 再并集去重，以界定适用范围。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hubstudio_python.models.chunk import canonical_intent_category

GENERAL = "GEN"


def _is_general_token(s: str) -> bool:
    t = (s or "").strip()
    if not t:
        return True
    return t.upper() == "GEN"


def _tokens(val: object) -> list[str]:
    """标量或列表 → 非空 token 列表；空 / GEN → [GEN]；用于店铺 / 达人进度（并列）。"""
    if val is None:
        return [GENERAL]
    if isinstance(val, list):
        raw = [str(x).strip() for x in val if str(x).strip()]
    else:
        s = str(val).strip()
        raw = [s] if s else []
    out: list[str] = []
    for x in raw:
        if _is_general_token(x):
            if GENERAL not in out:
                out.append(GENERAL)
        else:
            out.append(x)
    if not out:
        return [GENERAL]
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def _iter_chunks_files(output_dir: Path) -> list[Path]:
    if not output_dir.is_dir():
        return []
    return sorted(output_dir.glob("*.chunks.json"))


def _structured_full_without_chunks(output_dir: Path) -> list[Path]:
    """尚无对应 ``*.chunks.json`` 的 ``*.structured.full.json``（如 Playbook 仅结构化未 build）。"""
    chunk_stems = {p.name.replace(".chunks.json", "") for p in _iter_chunks_files(output_dir)}
    out: list[Path] = []
    for path in sorted(output_dir.glob("*.structured.full.json")):
        stem = path.name.replace(".structured.full.json", "")
        if stem not in chunk_stems:
            out.append(path)
    return out


def _records_from_structured_full(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        recs = data.get("records")
        if isinstance(recs, list):
            return [r for r in recs if isinstance(r, dict)]
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return []


def _ingest_records(
    tree: dict[str, dict[str, dict[str, set[str]]]],
    records: list[dict[str, Any]],
) -> int:
    count = 0
    for item in records:
        if not isinstance(item, dict):
            continue
        shops = _tokens(item.get("applicable_shops"))
        buckets = _creator_type_buckets(item.get("creator_type"))
        progs = _progress_tokens(item.get("creator_progress"))
        intent = _intent_label(item.get("intent_category"))
        for shop in shops:
            s_node = tree.setdefault(shop, {})
            for b in buckets:
                t_node = s_node.setdefault(b, {})
                for p in progs:
                    t_node.setdefault(p, set()).add(intent)
        count += 1
    return count


def _intent_label(val: object) -> str:
    if isinstance(val, list):
        parts = [
            canonical_intent_category(str(x).strip())
            for x in val
            if str(x).strip()
        ]
        s = " / ".join(parts) if parts else ""
    else:
        s = canonical_intent_category(str(val or "").strip())
    if not s or _is_general_token(s):
        return GENERAL
    return s


# 第二级「档位」键的展示顺序（键名仍与表格一致）
BUCKET_ORDER: tuple[str, ...] = (
    "A-level",
    "B-level",
    "C-level",
    "S-level",
    "SS-level",
    "AI UGC达人",
    "高GMV达人",
    "纯佣寄样达人",
    GENERAL,
)

_BUCKET_RANK = {k: i for i, k in enumerate(BUCKET_ORDER)}

_LEVEL_RE = re.compile(
    r"^\s*(?P<lv>SS|S|A|B|C)\s*[-–]?\s*level\s*$",
    re.IGNORECASE,
)


def _normalize_creator_token(raw: str) -> str:
    t = (raw or "").strip()
    if not t or t.upper() == "GEN":
        return GENERAL
    if t == "AI UGC达人":
        return "AI UGC达人"
    if t == "高GMV达人":
        return "高GMV达人"
    if t == "纯佣带货达人" or t == "纯佣寄样达人":
        return "纯佣带货达人"
    m = _LEVEL_RE.match(t)
    if m:
        lv = m.group("lv").upper()
        return f"{lv}-level"
    return GENERAL


def _creator_type_buckets(val: object) -> list[str]:
    if val is None:
        return [GENERAL]
    if isinstance(val, list):
        raw = [str(x).strip() for x in val if str(x).strip()]
    else:
        s = str(val or "").strip()
        raw = [s] if s else []
    if not raw:
        return [GENERAL]
    buckets: list[str] = []
    seen: set[str] = set()
    for x in raw:
        b = _normalize_creator_token(x)
        if b not in seen:
            seen.add(b)
            buckets.append(b)
    return buckets or [GENERAL]


def _intent_sort_key(k: str) -> tuple[int, str]:
    return (0 if k == GENERAL else 1, str(k))


def _progress_tokens(val: object) -> list[str]:
    """与 ``_tokens`` 相同语义：用于 ``creator_progress``（表头原文作键，仅 GEN 归一）。"""
    return _tokens(val)


def _serialize_tree(
    tree: dict[str, dict[str, dict[str, set[str]]]],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for shop in sorted(
        tree.keys(),
        key=lambda k: (0 if k == GENERAL else 1, str(k)),
    ):
        tmap = tree[shop]
        tier_keys = sorted(
            tmap.keys(),
            key=lambda k: (_BUCKET_RANK.get(str(k), len(BUCKET_ORDER)), str(k)),
        )
        out[shop] = {}
        for tk in tier_keys:
            pmap = tmap[tk]
            prog_keys = sorted(
                pmap.keys(),
                key=lambda k: (0 if k == GENERAL else 1, str(k)),
            )
            out[shop][tk] = {
                pk: sorted(pmap[pk], key=_intent_sort_key) for pk in prog_keys
            }
    return out


@dataclass(frozen=True)
class ShopTierIntentHierarchyResult:
    output_path: str
    chunks_files: int
    chunks_rows: int
    shop_count: int


def build_shop_tier_intent_hierarchy(
    output_dir: Path,
    *,
    output_filename: str = "shop_tier_intent_hierarchy.json",
) -> ShopTierIntentHierarchyResult:
    """
    扫描 ``output_dir`` 下全部 ``*.chunks.json``，以及尚无 chunks 的 ``*.structured.full.json``，
    写出 ``shop_tier_intent_hierarchy.json``。

    结构::

        { 店铺: { 档位: { 达人进度: [ 意图, ... ] } } }
    """
    tree: dict[str, dict[str, dict[str, set[str]]]] = {}
    chunk_files = _iter_chunks_files(output_dir)
    structured_files = _structured_full_without_chunks(output_dir)
    total_rows = 0
    for path in chunk_files:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            continue
        total_rows += _ingest_records(tree, raw)

    for path in structured_files:
        total_rows += _ingest_records(tree, _records_from_structured_full(path))

    nested = _serialize_tree(tree)

    source_files = [p.name for p in chunk_files] + [p.name for p in structured_files]
    out_path = output_dir / output_filename
    payload: dict[str, Any] = {
        "_meta": {
            "description": "固定三档键：店铺→creator_type档位→creator_progress(三级分类-达人进度)→意图数组；不含creator_type_and",
            "tier_buckets": list(BUCKET_ORDER),
            "progress_field": "creator_progress",
            "gen_usage_note": "使用方按需将 GEN 与具体店铺/档位/进度路径做并集后去重；可沿树向父级 GEN 回退再并集，以扩大匹配范围。",
            "source_chunk_files": source_files,
            "chunk_row_count": total_rows,
            "shop_count": len(tree),
        },
        "tree": nested,
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ShopTierIntentHierarchyResult(
        output_path=str(out_path.resolve()),
        chunks_files=len(source_files),
        chunks_rows=total_rows,
        shop_count=len(tree),
    )
