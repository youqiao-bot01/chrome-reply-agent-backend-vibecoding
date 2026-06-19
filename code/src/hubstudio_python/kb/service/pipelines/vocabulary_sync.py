"""
从 ``*.chunks.json`` 同步 / 扩充 ``knowledge_chunk_vocabulary.yaml``。

- ``sync_vocabulary_from_chunks``：以 Excel 话术库为准，合并已有取值与别名；
- ``extend_vocabulary_from_records``：Playbook 结构化后，无法映射的新词写入 YAML。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from hubstudio_python.models.knowledge_chunk_vocabulary import (
    default_vocabulary_path,
    reload_vocabulary,
)

# 从 chunks 行内抽取、合并进 YAML ``values`` 的字段
_SYNC_ENUM_FIELDS = (
    "rule_type",
    "applicable_shops",
    "creator_type",
    "creator_type_and",
    "creator_progress",
    "intent_category",
    "creator_emotion",
    "creator_reply_frequency",
    "language",
    "restore_slots",
)

# 结构化产出中若无法 canonicalize，则追加到 YAML values
_DYNAMIC_ENUM_FIELDS = (
    "intent_category",
    "creator_type",
    "creator_progress",
    "creator_emotion",
    "key_information",
    "restore_slots",
    "rule_type",
    "applicable_shops",
)

# Excel ``key_information`` 原文 → canonical tag（其余原文写入 aliases）
_KEY_INFO_EXCEL_ALIASES: dict[str, str] = {
    "WhatsApp Group Link": "whatsapp_group",
    "B-level WhatsApp Group Link": "whatsapp_group",
    "WhatsApp Group Form": "whatsapp_form",
    "product link": "affiliate_link",
    "creator portal link": "creator_portal",
    "A-level community link": "community_link",
    "S-level community link": "community_link",
    "A-level manager name": "manager_contact",
    "S-level manager name": "manager_contact",
    "A-level manager Wechat": "manager_wechat",
    "S-level manager Wechat": "manager_wechat",
    "TTO Creator Team link": "creator_team_link",
}

# 跨字段别名（Excel 历史写法 → canonical）
_CROSS_FIELD_ALIASES: dict[str, dict[str, list[str]]] = {
    "rule_type": {
        "AI执行动作": ["执行动作"],
    },
}


def _yaml_header() -> str:
    return """# 知识库 chunks 字段词汇表（唯一维护源）
#
# 用途：
# - DeepSeek 结构化 Playbook / Excel 切片写入时的枚举收敛
# - Chroma / 业务库 where 条件与 shop_tier_intent_hierarchy 对齐
# - 运行 `uv run python main.py vocabulary --sync-from-chunks` 从 Excel chunks 合并取值
# - 运行 `uv run python main.py vocabulary --init --force` 合并
#
# 维护约定：
# - 新增场景时优先在 intent_category / other_creator_conditions 中补 canonical 值
# - aliases 用于把模型或历史文案映射到 canonical，避免同义发散
# - Playbook 结构化无法映射的新词会自动追加（见 _meta.auto_added）

"""


def _split_cell_values(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            out.extend(_split_cell_values(item))
        return out
    s = str(raw).strip()
    if not s:
        return []
    if re.search(r"[,，;；]", s):
        return [p.strip() for p in re.split(r"[,，;；]+", s) if p.strip()]
    return [s]


def extract_field_values_from_records(records: list[dict[str, Any]]) -> dict[str, set[str]]:
    agg: dict[str, set[str]] = {f: set() for f in _SYNC_ENUM_FIELDS}
    for row in records:
        if not isinstance(row, dict):
            continue
        for fname in _SYNC_ENUM_FIELDS:
            if fname not in row:
                continue
            for val in _split_cell_values(row[fname]):
                agg[fname].add(val)
    return agg


def load_chunks_records(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        recs = data.get("records")
        if isinstance(recs, list):
            return [r for r in recs if isinstance(r, dict)]
    return []


def _sorted_values(values: set[str]) -> list[str]:
    def key(v: str) -> tuple[int, str]:
        if v == "GEN":
            return (0, v)
        return (1, v.casefold())

    return sorted(values, key=key)


def _merge_values_list(existing: list[Any] | None, incoming: set[str]) -> tuple[list[str], list[str]]:
    current = [str(x) for x in (existing or [])]
    seen = {x.casefold(): x for x in current}
    added: list[str] = []
    for val in _sorted_values(incoming):
        if val.casefold() not in seen:
            seen[val.casefold()] = val
            added.append(val)
    merged = _sorted_values(set(seen.values()))
    return merged, added


def _merge_aliases(
    existing: dict[str, Any] | None,
    new_aliases: dict[str, list[str]],
) -> tuple[dict[str, list[str]], list[str]]:
    out: dict[str, list[str]] = {}
    if isinstance(existing, dict):
        for k, v in existing.items():
            out[str(k)] = [str(x) for x in (v or [])]
    added_log: list[str] = []
    for canon, alias_list in new_aliases.items():
        bucket = out.setdefault(canon, [])
        seen = {a.casefold() for a in bucket}
        for alias in alias_list:
            a = str(alias).strip()
            if not a or a.casefold() in seen or a == canon:
                continue
            if a.casefold() == canon.casefold():
                continue
            bucket.append(a)
            seen.add(a.casefold())
            added_log.append(f"{canon} ← {a}")
    return out, added_log


def _apply_key_information_from_excel(fields: dict[str, Any], records: list[dict[str, Any]]) -> list[str]:
    """把 Excel key_information 原文并入 tag values / aliases。"""
    spec = fields.setdefault(
        "key_information",
        {"label": "关键信息（语义标签）", "type": "tag_array", "values": [], "aliases": {}},
    )
    values_incoming: set[str] = set()
    alias_incoming: dict[str, list[str]] = {}
    for row in records:
        for raw in _split_cell_values(row.get("key_information")):
            canon = _KEY_INFO_EXCEL_ALIASES.get(raw)
            if canon:
                values_incoming.add(canon)
                alias_incoming.setdefault(canon, [])
                if raw not in alias_incoming[canon] and raw != canon:
                    alias_incoming[canon].append(raw)
            else:
                tag = re.sub(r"\s+", "_", raw.strip().lower())
                tag = re.sub(r"[^\w]+", "_", tag).strip("_")
                if tag:
                    values_incoming.add(tag)
                    alias_incoming.setdefault(tag, []).append(raw)

    merged_vals, added_vals = _merge_values_list(spec.get("values"), values_incoming)
    spec["values"] = merged_vals
    merged_aliases, added_aliases = _merge_aliases(spec.get("aliases"), alias_incoming)
    if merged_aliases:
        spec["aliases"] = merged_aliases
    return [f"key_information value +{v}" for v in added_vals] + [
        f"key_information alias {a}" for a in added_aliases
    ]


def merge_vocabulary_data(
    data: dict[str, Any],
    extracted: dict[str, set[str]],
    *,
    records_for_key_info: list[dict[str, Any]] | None = None,
) -> list[str]:
    """合并抽取结果到 YAML data，返回变更日志。"""
    fields = data.setdefault("fields", {})
    log: list[str] = []

    for fname in _SYNC_ENUM_FIELDS:
        if fname not in fields or fname not in extracted:
            continue
        spec = fields[fname]
        if not isinstance(spec, dict):
            continue
        if fname == "other_creator_conditions":
            continue
        merged, added = _merge_values_list(spec.get("values"), extracted[fname])
        if added:
            spec["values"] = merged
            log.extend(f"{fname} +{v}" for v in added)

    for fname, alias_map in _CROSS_FIELD_ALIASES.items():
        spec = fields.get(fname)
        if not isinstance(spec, dict):
            continue
        merged_aliases, added = _merge_aliases(spec.get("aliases"), alias_map)
        if added:
            spec["aliases"] = merged_aliases
            log.extend(f"{fname} alias {a}" for a in added)

    if records_for_key_info:
        log.extend(_apply_key_information_from_excel(fields, records_for_key_info))

    meta = data.setdefault("_meta", {})
    meta["updated"] = str(date.today())
    sources = meta.setdefault("sync_sources", [])
    if isinstance(sources, list):
        pass
    else:
        meta["sync_sources"] = []
    return log


def save_vocabulary_yaml(data: dict[str, Any], path: Path | None = None) -> Path:
    out = path or default_vocabulary_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=120,
    )
    out.write_text(_yaml_header() + body, encoding="utf-8")
    return out


@dataclass
class VocabularySyncResult:
    yaml_path: str
    chunks_path: str
    record_count: int
    changes: list[str] = field(default_factory=list)


def sync_vocabulary_from_chunks(
    chunks_path: Path,
    *,
    yaml_path: Path | None = None,
) -> VocabularySyncResult:
    records = load_chunks_records(chunks_path)
    extracted = extract_field_values_from_records(records)
    path = yaml_path or default_vocabulary_path()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {"fields": {}}
    if not isinstance(data, dict):
        data = {"fields": {}}
    changes = merge_vocabulary_data(data, extracted, records_for_key_info=records)
    sources = data.setdefault("_meta", {}).setdefault("sync_sources", [])
    src_name = chunks_path.name
    if isinstance(sources, list) and src_name not in sources:
        sources.append(src_name)
    save_vocabulary_yaml(data, path)
    reload_vocabulary()
    return VocabularySyncResult(
        yaml_path=str(path.resolve()),
        chunks_path=str(chunks_path.resolve()),
        record_count=len(records),
        changes=changes,
    )


@dataclass
class VocabularyExtendResult:
    yaml_path: str
    added: list[str]


def extend_vocabulary_from_records(
    records: list[dict[str, Any]],
    *,
    yaml_path: Path | None = None,
    source_label: str = "structure-playbook",
) -> VocabularyExtendResult:
    """
    将结构化记录中仍无法 canonicalize 的枚举值追加进 YAML（动态维护）。
    """
    path = yaml_path or default_vocabulary_path()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {"fields": {}}
    if not isinstance(data, dict):
        data = {"fields": {}}
    fields = data.setdefault("fields", {})
    vocab = reload_vocabulary()
    added_log: list[str] = []

    incoming: dict[str, set[str]] = {f: set() for f in _DYNAMIC_ENUM_FIELDS}
    for rec in records:
        if not isinstance(rec, dict):
            continue
        for fname in _DYNAMIC_ENUM_FIELDS:
            if fname not in rec:
                continue
            allowed = vocab.allowed_values(fname)
            if not allowed:
                continue
            for val in _split_cell_values(rec[fname]):
                canon = vocab.canonicalize(fname, val) or val
                if str(canon) not in allowed:
                    incoming[fname].add(str(canon))

    for fname, vals in incoming.items():
        if not vals:
            continue
        spec = fields.get(fname)
        if not isinstance(spec, dict):
            continue
        merged, added = _merge_values_list(spec.get("values"), vals)
        if added:
            spec["values"] = merged
            added_log.extend(f"{fname} +{v}" for v in added)

    if added_log:
        meta = data.setdefault("_meta", {})
        meta["updated"] = str(date.today())
        auto = meta.setdefault("auto_added", [])
        if isinstance(auto, list):
            auto.append({"source": source_label, "date": str(date.today()), "items": added_log})
        save_vocabulary_yaml(data, path)
        reload_vocabulary()

    return VocabularyExtendResult(yaml_path=str(path.resolve()), added=added_log)
