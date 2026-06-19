"""
知识库脱敏：正文中的 URL / 店铺敏感值 → ``{restore_slot}`` 占位符。

还原在回复生成阶段通过 ``shop_restore_values.yaml`` 完成。
``key_information`` 标明需并入最终回复的具体内容项；**仅**允许第 1 步词汇表已抽取、且对应
``restore_slots`` 在 ``shop_restore_values.yaml`` 中有运营定值的标签。
"""

from __future__ import annotations

import re
from typing import Any

from hubstudio_python.models.knowledge_chunk_vocabulary import get_vocabulary
from hubstudio_python.models.shop_restore_values import load_shop_restore_slots

_URL_RE = re.compile(r"https?://[^\s\)\]\"']+", re.I)
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")


def _shop_from_record(record: dict[str, Any]) -> str | None:
    shop = record.get("applicable_shops")
    if isinstance(shop, list):
        shop = shop[0] if shop else None
    s = str(shop or "").strip()
    if s and s.upper() != "GEN":
        return s
    source = str(record.get("source_file") or "").replace("\\", "/")
    parts = [p for p in source.split("/") if p]
    return parts[0] if len(parts) >= 2 else None


def _value_to_slot_map(shop: str) -> dict[str, str]:
    slots = load_shop_restore_slots(shop)
    return {v: k for k, v in slots.items() if v}


def _extract_placeholders(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in _PLACEHOLDER_RE.finditer(text or ""):
        key = m.group(1)
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


# restore_slot 前缀 → key_information 规范 tag（须与 vocabulary overlay 一致）
_SLOT_PREFIX_TO_KEY_INFO_CANON: list[tuple[str, str]] = [
    ("wa_group_", "whatsapp_group"),
    ("whatsapp_form_", "whatsapp_form"),
    ("affiliate_link_", "affiliate_link"),
    ("commission_rate_", "commission_rate"),
    ("creator_portal_", "creator_portal"),
    ("creator_team_", "creator_team_link"),
    ("community_link_", "community_link"),
    ("manager_wechat_", "manager_wechat"),
    ("manager_contact_", "manager_contact"),
    ("ai_ugc_", "ai_ugc_portal"),
]


def key_information_canon_from_slot(slot_id: str) -> str | None:
    for prefix, canon in _SLOT_PREFIX_TO_KEY_INFO_CANON:
        if slot_id.startswith(prefix):
            return canon
    return None


def key_information_tags_with_values(shop: str) -> frozenset[str]:
    """店铺第 1 步已抽取、且在 shop_restore_values 中有定值的 key_information 规范 tag。"""
    valued_slots = load_shop_restore_slots(shop)
    if not valued_slots:
        return frozenset()
    vocab = get_vocabulary()
    extracted = vocab.allowed_values("key_information", shop=shop)
    tags: set[str] = set()
    for slot_id in valued_slots:
        canon = key_information_canon_from_slot(slot_id)
        if not canon:
            continue
        if extracted and canon not in extracted:
            continue
        tags.add(canon)
    return frozenset(tags)


def _preferred_key_information_label(canon: str, shop: str | None) -> str:
    spec = get_vocabulary().field_spec("key_information", shop=shop) or {}
    aliases = spec.get("aliases") or {}
    als = aliases.get(canon) if isinstance(aliases, dict) else None
    if isinstance(als, list) and als:
        return str(als[0])
    return canon.replace("_", " ")


def _normalize_key_information(raw: object) -> list[str] | None:
    if raw is None:
        return None
    items: list[str] = []
    if isinstance(raw, list):
        src = raw
    elif isinstance(raw, str) and raw.strip():
        src = re.split(r"[,，;；\n]+", raw)
    else:
        return None
    seen: set[str] = set()
    for item in src:
        s = str(item).strip()
        if s and s not in seen:
            seen.add(s)
            items.append(s)
    return items or None


def _record_restore_slots(record: dict[str, Any]) -> list[str]:
    raw_slots = record.get("restore_slots")
    slots: list[str] = []
    if isinstance(raw_slots, list):
        slots = [str(s) for s in raw_slots if str(s).strip()]
    elif isinstance(raw_slots, str) and raw_slots.strip():
        slots = [raw_slots.strip()]
    blob = "\n".join(str(record.get(k) or "") for k in ("content", "encontent"))
    seen = set(slots)
    for ph in _extract_placeholders(blob):
        if ph not in seen:
            seen.add(ph)
            slots.append(ph)
    return slots


def resolve_key_information_for_record(record: dict[str, Any], shop: str | None) -> list[str] | None:
    """
    解析/过滤 ``key_information``：仅保留本记录 ``restore_slots`` 中、已在店铺词汇表抽取、
    且在 ``shop_restore_values.yaml`` 有定值的可还原项。
    """
    if not shop:
        return None

    shop_tags = key_information_tags_with_values(shop)
    if not shop_tags:
        return None

    valued_slots = load_shop_restore_slots(shop)
    backed_canons: set[str] = set()
    for slot in _record_restore_slots(record):
        if slot not in valued_slots:
            continue
        canon = key_information_canon_from_slot(slot)
        if canon and canon in shop_tags:
            backed_canons.add(canon)

    if not backed_canons:
        return None

    vocab = get_vocabulary()
    existing = _normalize_key_information(record.get("key_information"))
    labels: list[str] = []
    seen: set[str] = set()

    if existing:
        for item in existing:
            canon = vocab.canonicalize("key_information", item, shop=shop)
            if canon and canon in backed_canons:
                label = _preferred_key_information_label(canon, shop)
                if label not in seen:
                    seen.add(label)
                    labels.append(label)
        return labels or None

    for slot in _record_restore_slots(record):
        if slot not in valued_slots:
            continue
        canon = key_information_canon_from_slot(slot)
        if not canon or canon not in backed_canons:
            continue
        label = _preferred_key_information_label(canon, shop)
        if label not in seen:
            seen.add(label)
            labels.append(label)
    return labels or None


def derive_key_information(record: dict[str, Any]) -> list[str] | None:
    """从记录的 restore_slots（有定值）推导 key_information；已有值时仅做过滤。"""
    return resolve_key_information_for_record(record, _shop_from_record(record))


def _normalize_placeholder_syntax(text: str) -> str:
    return re.sub(r"\$\{([a-zA-Z0-9_]+)\}", r"{\1}", text or "")


def desensitize_text(text: str, shop: str | None) -> str:
    if not text or not shop:
        return _normalize_placeholder_syntax(text)
    rev = _value_to_slot_map(shop)
    out = text
    for literal, slot in rev.items():
        if literal in out:
            out = out.replace(literal, "{" + slot + "}")
    for url in _URL_RE.findall(out):
        slot = rev.get(url)
        if slot:
            out = out.replace(url, "{" + slot + "}")
    return _normalize_placeholder_syntax(out)


def desensitize_playbook_record(record: dict[str, Any]) -> dict[str, Any]:
    """脱敏 content/encontent，保留或补全 key_information，汇总 restore_slots。"""
    out = dict(record)
    shop = _shop_from_record(out)
    if shop:
        from hubstudio_python.kb.service.pipelines.vocabulary_merge import restore_slot_ids_for_shop

        allowed_slots = frozenset(restore_slot_ids_for_shop(shop))
    else:
        allowed_slots = get_vocabulary().allowed_values("restore_slots")

    for field in ("content", "encontent", "title", "entitle"):
        if field in out and isinstance(out[field], str):
            out[field] = desensitize_text(out[field], shop)

    blob = "\n".join(str(out.get(k) or "") for k in ("content", "encontent"))
    slots: list[str] = []
    seen: set[str] = set()
    for ph in _extract_placeholders(blob):
        if ph in allowed_slots and ph not in seen:
            seen.add(ph)
            slots.append(ph)

    if slots:
        out["restore_slots"] = slots
    else:
        out.pop("restore_slots", None)

    ki = derive_key_information(out)
    if ki:
        out["key_information"] = ki
    else:
        out.pop("key_information", None)

    return out


def format_restore_slots_for_prompt(shop: str | None = None) -> str:
    if shop:
        from hubstudio_python.kb.service.pipelines.vocabulary_merge import restore_slot_ids_for_shop

        allowed = restore_slot_ids_for_shop(shop)
        slot_source = f"shop `{shop}` vocabulary (`schema/vocabulary/{shop}.yaml`)"
    else:
        vocab = get_vocabulary()
        allowed = sorted(vocab.allowed_values("restore_slots"))
        slot_source = "`schema/vocabulary/merged.yaml` (generic + `_meta.shop_extensions`)"
    lines = [
        "## Desensitized storage & reply restore",
        "",
        "- **Never** put real URLs, WhatsApp links, affiliate links, or shop-specific secrets in "
        "`content` / `encontent`. Use `{restore_slot}` placeholders only.",
        "- **Only** use restore_slot IDs that exist in the shop's `shop_restore_values.yaml` — "
        "**do NOT invent** slots like `sample_form_link` or `creator_portal_link` if not configured.",
        "- For sample forms / Portal without a configured link: describe the action in prose "
        '(e.g. "I\'ll send you the form link") without a `{placeholder}`.',
        "- **key_information**: ONLY items from the **allowed list below** — supplementary content "
        "merged at reply time from `shop_restore_values.yaml`. Do NOT list policy prose, sample "
        "forms, CPM rates, or anything without a configured restore value.",
        "- **restore_slots**: JSON array of slot IDs used in the script — filled at reply time from "
        "`schema/restore/<shop>.yaml`.",
        "- **answer_purpose**: one sentence — what question this rule answers for the creator.",
        "- **creator_action_guide**: one sentence — what the creator should **do** next (clear action).",
        "",
        f"Allowed **restore_slots** (from {slot_source}): "
        + ", ".join(f"``{s}``" for s in allowed),
        "",
    ]
    if shop:
        ki_tags = key_information_tags_with_values(shop)
        if ki_tags:
            ki_labels = [_preferred_key_information_label(t, shop) for t in sorted(ki_tags)]
            lines.append(
                "Allowed **key_information** (document-extracted + has restore value): "
                + ", ".join(f"``{l}``" for l in ki_labels)
            )
            lines.append(
                "- Set **key_information** only when this record's `restore_slots` include a slot "
                "that maps to one of the tags above. Omit otherwise."
            )
            lines.append("")
        else:
            lines.append(
                "Allowed **key_information**: *(none — no restore values configured for this shop yet)*"
            )
            lines.append("- Omit **key_information** on all records until restore values exist.")
            lines.append("")
    return "\n".join(lines)
