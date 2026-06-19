"""
加载 ``rag_data/schema/vocabulary/merged.yaml``，提供字段取值收敛与 prompt 渲染。

维护源：``vocabulary/generic.yaml`` + ``vocabulary/<shop>.yaml`` → ``vocabulary --init`` 合并。

筛选条件取值（creator_progress / intent_category / other_creator_conditions 等）
在 ``_meta.shop_extensions.<shop>.condition_fields``，**不**与全局 fields 混并。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from hubstudio_python.config import _project_root
from hubstudio_python.models.vocabulary_constants import SHOP_SCOPED_FIELD_NAMES


def default_vocabulary_path() -> Path:
    from hubstudio_python.models.schema_layout import vocabulary_merged_path

    return vocabulary_merged_path()


def _shop_from_record(record: dict[str, Any]) -> str | None:
    shop = record.get("applicable_shops")
    if isinstance(shop, list):
        shop = shop[0] if shop else None
    s = str(shop or "").strip()
    if s and s.upper() != "GEN":
        return s
    source = str(record.get("source_file") or "").replace("\\", "/")
    parts = [p for p in source.split("/") if p and p not in {".", ".."}]
    return parts[0] if len(parts) >= 2 else None


def _union_values(base: list[Any], extra: list[Any]) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for item in [*base, *extra]:
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _compose_shop_field_spec(base: dict[str, Any], shop_spec: dict[str, Any]) -> dict[str, Any]:
    """运行期组合：通用 baseline + 店铺专用（存储层二者不重复）。"""
    out = dict(base)
    out.update(shop_spec)
    base_vals = list(base.get("values") or [])
    shop_vals = list(shop_spec.get("values") or [])
    merged_vals = _union_values(base_vals, shop_vals)
    if merged_vals:
        out["values"] = merged_vals
    base_aliases = dict(base.get("aliases") or {})
    shop_aliases = dict(shop_spec.get("aliases") or {})
    if base_aliases or shop_aliases:
        out["aliases"] = {**base_aliases, **shop_aliases}
    base_groups = dict(base.get("groups") or {})
    shop_groups = dict(shop_spec.get("groups") or {})
    if base_groups or shop_groups:
        merged_groups = dict(base_groups)
        for gname, gvals in shop_groups.items():
            if isinstance(gvals, list):
                merged_groups[str(gname)] = _union_values(
                    list(merged_groups.get(gname) or []),
                    gvals,
                )
            else:
                merged_groups[str(gname)] = gvals
        out["groups"] = merged_groups
    return out


def _build_alias_map(spec: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for canonical in spec.get("values") or []:
        mapping[str(canonical).casefold()] = str(canonical)
    aliases = spec.get("aliases") or {}
    if isinstance(aliases, dict):
        for canonical, alias_list in aliases.items():
            canon = str(canonical)
            mapping[str(canon).casefold()] = canon
            for alias in alias_list or []:
                mapping[str(alias).casefold()] = canon
    groups = spec.get("groups") or {}
    if isinstance(groups, dict):
        for group_flags in groups.values():
            if isinstance(group_flags, list):
                for f in group_flags:
                    mapping[str(f).casefold()] = str(f)
    return mapping


def _flags_from_spec(spec: dict[str, Any]) -> set[str]:
    flags: set[str] = set()
    groups = spec.get("groups") or {}
    if isinstance(groups, dict):
        for group_flags in groups.values():
            if isinstance(group_flags, list):
                for f in group_flags:
                    flags.add(str(f))
    return flags


@dataclass
class KnowledgeChunkVocabulary:
    raw: dict[str, Any]
    fields: dict[str, Any]
    alias_to_canonical: dict[str, dict[str, str]] = field(default_factory=dict)
    flag_values: frozenset[str] = frozenset()
    shop_condition_fields: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    shop_alias_maps: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)
    shop_flag_values: dict[str, frozenset[str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> KnowledgeChunkVocabulary:
        vocab_path = path or default_vocabulary_path()
        data = yaml.safe_load(vocab_path.read_text(encoding="utf-8")) or {}
        fields = data.get("fields") or {}
        alias_maps: dict[str, dict[str, str]] = {}
        flags: set[str] = set()

        for fname, spec in fields.items():
            if not isinstance(spec, dict):
                continue
            alias_maps[fname] = _build_alias_map(spec)
            flags.update(_flags_from_spec(spec))

        shop_cf: dict[str, dict[str, dict[str, Any]]] = {}
        shop_aliases: dict[str, dict[str, dict[str, str]]] = {}
        shop_flags: dict[str, frozenset[str]] = {}

        shop_ext = (data.get("_meta") or {}).get("shop_extensions") or {}
        if isinstance(shop_ext, dict):
            for shop, ext in shop_ext.items():
                if not isinstance(ext, dict):
                    continue
                cf = ext.get("condition_fields") or {}
                if not isinstance(cf, dict):
                    continue
                shop_cf[str(shop)] = {str(k): dict(v) for k, v in cf.items() if isinstance(v, dict)}
                shop_aliases[str(shop)] = {
                    str(fname): _build_alias_map(spec)
                    for fname, spec in shop_cf[str(shop)].items()
                }
                sflags: set[str] = set()
                for fname, spec in shop_cf[str(shop)].items():
                    sflags.update(_flags_from_spec(spec))
                shop_flags[str(shop)] = frozenset(sflags)

        return cls(
            raw=data,
            fields=fields,
            alias_to_canonical=alias_maps,
            flag_values=frozenset(flags),
            shop_condition_fields=shop_cf,
            shop_alias_maps=shop_aliases,
            shop_flag_values=shop_flags,
        )

    def shop_names(self) -> list[str]:
        return sorted(self.shop_condition_fields.keys())

    def _effective_spec(self, field_name: str, shop: str | None) -> dict[str, Any]:
        base = self.fields.get(field_name)
        base_dict = dict(base) if isinstance(base, dict) else {}
        if shop and field_name in SHOP_SCOPED_FIELD_NAMES:
            shop_spec = (self.shop_condition_fields.get(shop) or {}).get(field_name)
            if isinstance(shop_spec, dict) and shop_spec:
                return _compose_shop_field_spec(base_dict, shop_spec)
            return base_dict
        return base_dict

    def field_spec(self, name: str, shop: str | None = None) -> dict[str, Any]:
        if shop and name in SHOP_SCOPED_FIELD_NAMES:
            shop_spec = (self.shop_condition_fields.get(shop) or {}).get(name)
            base = self.fields.get(name)
            base_dict = dict(base) if isinstance(base, dict) else {}
            if isinstance(shop_spec, dict) and shop_spec:
                return _compose_shop_field_spec(base_dict, shop_spec)
            return base_dict
        spec = self.fields.get(name)
        return dict(spec) if isinstance(spec, dict) else {}

    def allowed_values(self, field_name: str, shop: str | None = None) -> frozenset[str]:
        spec = self._effective_spec(field_name, shop)
        vals = spec.get("values")
        if isinstance(vals, list) and vals:
            return frozenset(str(v) for v in vals)
        if field_name == "other_creator_conditions":
            if shop and shop in self.shop_flag_values:
                return self.shop_flag_values[shop]
            return self.flag_values
        return frozenset()

    def flag_values_for_shop(self, shop: str | None) -> frozenset[str]:
        if shop and shop in self.shop_flag_values:
            return self.shop_flag_values[shop]
        return self.flag_values

    def canonicalize(
        self,
        field_name: str,
        raw: object,
        *,
        default: str | None = None,
        shop: str | None = None,
    ) -> str | None:
        s = str(raw or "").strip()
        if not s:
            return default
        allowed = self.allowed_values(field_name, shop)
        if shop and field_name in SHOP_SCOPED_FIELD_NAMES:
            shop_map = (self.shop_alias_maps.get(shop) or {}).get(field_name) or {}
            mapped = shop_map.get(s.casefold())
            if mapped:
                return mapped
        alias_map = self.alias_to_canonical.get(field_name, {})
        mapped = alias_map.get(s.casefold())
        if mapped:
            return mapped
        if s in allowed:
            return s
        if allowed:
            for candidate in allowed:
                if candidate.casefold() == s.casefold():
                    return candidate
        if default is not None:
            return default
        return s if not allowed else None

    def canonicalize_intent(self, raw: object, shop: str | None = None) -> str | None:
        s = str(raw or "").strip()
        if not s:
            return None
        s = re.sub(r"\s+(Negotiation|Handling|Enrollment|Interest|Transfer|Support)\s*$", "", s, flags=re.I)
        s = re.sub(r"\s+", " ", s).strip()
        canon = self.canonicalize("intent_category", s, shop=shop)
        if canon:
            return canon
        if len(s) > 48:
            s = s[:48].rsplit(" ", 1)[0]
        return s or None

    def canonicalize_flags(self, raw: object, shop: str | None = None) -> list[str] | None:
        from hubstudio_python.models.playbook_query_flags import normalize_other_creator_conditions

        return normalize_other_creator_conditions(raw, shop=shop)

    def format_for_prompt(self, shop: str | None = None) -> str:
        meta = self.raw.get("_meta") or {}
        cm = meta.get("condition_matching") or {}
        shop_line = f" for shop **`{shop}`**" if shop else ""
        lines = [
            f"## Field vocabulary{shop_line} (MUST use canonical values)",
            "",
            "### Rule matching semantics",
            "- **Every non-`GEN` condition on a record is mandatory** — the creator/session must satisfy **ALL** of them for this rule to apply (cross-field **AND**).",
            "- **`GEN` or empty** on a dimension = wildcard (no constraint on that dimension).",
            "- **Within-field exceptions**: `creator_type` multi-value = **OR**; `creator_type_and` / `other_creator_conditions` = **AND** (all flags).",
            "- When playbook branches differ by OR tier (A vs B) with **different replies**, split into **separate records**.",
            "",
        ]
        if cm.get("summary"):
            lines.append(f"- {cm['summary']}")
            lines.append("")
        lines.append("Queryable fields — use **only** listed canonical values for this shop:")
        lines.append("")

        prompt_fields = (
            "rule_type",
            "applicable_shops",
            "creator_type",
            "creator_progress",
            "intent_category",
            "creator_emotion",
            "creator_reply_frequency",
            "other_creator_conditions",
            "key_information",
            "restore_slots",
        )
        for fname in prompt_fields:
            spec = self.field_spec(fname, shop=shop)
            if not spec:
                continue
            label = spec.get("label") or fname
            note = spec.get("note") or spec.get("excel_header") or ""
            lines.append(f"### `{fname}` — {label}")
            if note:
                lines.append(f"- {note}")
            if fname == "other_creator_conditions":
                groups = spec.get("groups") or {}
                for gname, gvals in groups.items():
                    if isinstance(gvals, list) and gvals:
                        lines.append(f"- **{gname}**: {', '.join(gvals)}")
            elif fname == "restore_slots":
                from hubstudio_python.kb.service.pipelines.vocabulary_merge import restore_slot_ids_for_shop

                if shop:
                    slots = restore_slot_ids_for_shop(shop)
                    if slots:
                        lines.append(f"- Slots: {', '.join(f'``{v}``' for v in slots)}")
            else:
                vals = spec.get("values") or []
                if vals:
                    lines.append(f"- Values: {', '.join(f'``{v}``' for v in vals)}")
                aliases = spec.get("aliases") or {}
                if isinstance(aliases, dict) and aliases:
                    lines.append("- Aliases → canonical:")
                    for canon, als in aliases.items():
                        if als:
                            lines.append(f"  - ``{canon}`` ← {', '.join(f'``{a}``' for a in als)}")
            lines.append("")

        if shop:
            neg = ((meta.get("shop_extensions") or {}).get(shop) or {}).get("negotiation")
            if isinstance(neg, dict) and neg.get("rounds"):
                lines.append(f"### Negotiation (`{shop}`)")
                lines.append(f"- {neg.get('scope', '')}")
                for row in neg.get("rounds") or []:
                    if isinstance(row, dict):
                        lines.append(f"  - {row.get('node')}: {row.get('offer')} → `{row.get('flags')}`")
                lines.append("")

        return "\n".join(lines)

    def validate_record(self, record: dict[str, Any]) -> list[str]:
        shop = _shop_from_record(record)
        errors: list[str] = []
        for fname in (
            "rule_type",
            "applicable_shops",
            "creator_type",
            "creator_progress",
            "intent_category",
            "creator_emotion",
            "language",
            "restore_slots",
        ):
            if fname not in record:
                continue
            val = record[fname]
            allowed = self.allowed_values(fname, shop)
            if isinstance(val, list):
                for item in val:
                    canon = self.canonicalize(fname, item, shop=shop)
                    if allowed and (canon not in allowed):
                        errors.append(f"{fname}: invalid value `{item}` for shop `{shop}`")
            else:
                canon = self.canonicalize(fname, val, shop=shop)
                if allowed and str(val).strip() and (canon not in allowed):
                    errors.append(f"{fname}: invalid value `{val}` (canonical `{canon}`) for shop `{shop}`")
        occ = record.get("other_creator_conditions")
        if occ is not None:
            allowed_flags = self.flag_values_for_shop(shop)
            items = occ if isinstance(occ, list) else [occ]
            for item in items:
                if str(item) not in allowed_flags:
                    errors.append(f"other_creator_conditions: invalid flag `{item}` for shop `{shop}`")
        rs = record.get("restore_slots")
        if rs is not None and shop:
            from hubstudio_python.kb.service.pipelines.vocabulary_merge import restore_slot_ids_for_shop

            allowed_rs = frozenset(restore_slot_ids_for_shop(shop))
            items = rs if isinstance(rs, list) else [rs]
            for item in items:
                if str(item) not in allowed_rs:
                    errors.append(f"restore_slots: invalid slot `{item}` for shop `{shop}`")
        return errors


@lru_cache(maxsize=1)
def get_vocabulary() -> KnowledgeChunkVocabulary:
    return KnowledgeChunkVocabulary.load()


def reload_vocabulary() -> KnowledgeChunkVocabulary:
    get_vocabulary.cache_clear()
    return get_vocabulary()
