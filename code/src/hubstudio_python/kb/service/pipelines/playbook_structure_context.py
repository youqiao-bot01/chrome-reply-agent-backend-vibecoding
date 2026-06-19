"""
Playbook 结构化第 2 步：注入第 1 步 analysis 上下文，并对齐谈判链 / 条件关系。
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

from hubstudio_python.models.knowledge_desensitize import desensitize_playbook_record
from hubstudio_python.models.playbook_query_flags import normalize_playbook_record_fields


def format_analysis_for_structure_prompt(analysis: dict[str, Any]) -> str:
    """将第 1 步 analysis 压缩为结构化 prompt 的强制约束块。"""
    shop = str(analysis.get("shop") or "").strip()
    overlay = analysis.get("vocabulary_overlay") or {}
    meta = overlay.get("_meta") or {}
    negotiation = meta.get("negotiation") or {}
    rels = analysis.get("condition_relationships") or []
    intents = (analysis.get("intent_classification") or {}).get("intents") or []

    lines = [
        "## Document analysis (MANDATORY — step 1 output, must be reflected in every record)",
        "",
        f"**Shop**: `{shop}`",
        "",
    ]
    summary = str(analysis.get("document_summary_zh") or "").strip()
    if summary:
        lines.extend([f"**Summary**: {summary}", ""])

    if negotiation.get("rounds"):
        lines.append("### A-tier negotiation ladder (toolant A-level ONLY)")
        lines.append(f"- Scope: {negotiation.get('scope', '')}")
        for note in negotiation.get("notes") or []:
            lines.append(f"- {note}")
        lines.append("")
        lines.append("| Node | Offer | Flags (AND) |")
        lines.append("|------|-------|-------------|")
        for row in negotiation.get("rounds") or []:
            if isinstance(row, dict):
                lines.append(
                    f"| {row.get('node')} | {row.get('offer')} | `{row.get('flags')}` |"
                )
        lines.append("")
        lines.extend([
            "**Negotiation record rules (critical):**",
            "1. Each node needs **separate records** for **accept** vs **reject/escalate** — never merge into one.",
            "2. **Accept** at node Ax: `other_creator_conditions` includes `priority_node:Ax` + `reply_ok` "
            "(or `reply_yes` / `reply_interested` / `reply_sure` as appropriate). "
            "`creator_emotion`: `感兴趣/同意`.",
            "3. **Reject / ask for more** at node Ax → **our reply** for next node Ay: "
            "`priority_node:Ay` + `prev_node:Ax`. "
            "Use `creator_emotion`: `GEN` or `拒绝合作` when creator declines current offer.",
            "4. **A1 opening outreach** (Linsey first message) is ONE record — "
            "`source_chunk_id`: `A1-Outreach`, `creator_emotion`: `GEN`, flags: `priority_node:A1` only "
            "(no `reply_ok`). `title` must NOT be OK/Sure — that belongs on the accept record.",
            "5. **A1 accept** (`A1-Accept`): creator says OK/Sure → push commission + sample; "
            "flags: `priority_node:A1`, `reply_ok`.",
            "6. **A2 offer** (`A2-Offer`): creator rejected pure commission / wants flat fee / asks rate; "
            "flags: `priority_node:A2`, `prev_node:A1`. Content = A2 CPM script.",
            "7. **A3 offer** (`A3-Offer`): $3 CPM capped flat fee for 1 video, 50/50 settlement; "
            "flags: `priority_node:A3`, `prev_node:A2`.",
            "8. **A4 collect quote** (`A4-Offer` / `A4-Quote`): flags: `priority_node:A4`, `prev_node:A3`.",
            "9. **Flat fee only** shortcut: creator says flat fee only at A1 → skip to A3 "
            "(`priority_node:A3`, `prev_node:A1`, intent Flat Fee).",
            "10. **toolant** tier routing uses `creator_type` (A/B/C-level) — do **not** add `gmv_*` flags.",
            "",
        ])

    if rels:
        lines.append("### Condition relationships (each MUST map to ≥1 record)")
        lines.append("")
        for rel in rels:
            if not isinstance(rel, dict):
                continue
            name = rel.get("name") or ""
            desc = rel.get("description_zh") or ""
            cond = rel.get("conditions") or {}
            flags = cond.get("other_creator_conditions") or []
            flag_s = ", ".join(f"`{f}`" for f in flags) if flags else "(see fields)"
            lines.append(f"- **{name}**: {desc}")
            lines.append(
                f"  - `creator_type`: `{cond.get('creator_type', 'GEN')}` · "
                f"flags: {flag_s}"
            )
        lines.append("")

    if intents:
        lines.append("### Intent → flags (use in `other_creator_conditions` + `title` triggers)")
        lines.append("")
        for intent in intents[:20]:
            if not isinstance(intent, dict):
                continue
            iid = intent.get("id") or ""
            scenario = intent.get("scenario_zh") or intent.get("label_zh") or ""
            co = intent.get("typical_co_conditions") or []
            examples = intent.get("examples") or intent.get("exact_replies") or []
            ex_preview = " / ".join(str(e) for e in examples[:3])
            co_s = ", ".join(f"`{c}`" for c in co) if co else ""
            lines.append(f"- `{iid}`: {scenario}")
            if co_s:
                lines.append(f"  - co_conditions: {co_s}")
            if ex_preview:
                lines.append(f"  - trigger examples: {ex_preview}")
        lines.append("")

    constants = analysis.get("constants") or []
    if constants:
        lines.append("### Constants / restore_slots (from analysis)")
        for c in constants[:12]:
            if isinstance(c, dict):
                lines.append(
                    f"- `{c.get('slot_id')}` ({c.get('value_kind')}): {c.get('description', '')}"
                )
        lines.append("")

    return "\n".join(lines)


def _flags_list(raw: object) -> list[str]:
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def _merge_flags(existing: object, required: list[str]) -> list[str]:
    out = _flags_list(existing)
    seen = set(out)
    for f in required:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _find_record(records: list[dict[str, Any]], *chunk_id_prefixes: str) -> dict[str, Any] | None:
    for rec in records:
        cid = str(rec.get("source_chunk_id") or "")
        for prefix in chunk_id_prefixes:
            if cid.startswith(prefix) or cid == prefix:
                return rec
    return None


def _a3_flat_fee_content() -> str:
    return (
        "I hear you—you want a clear fixed number, not a floating CPM. Here's a one-video flat fee offer:\n\n"
        "  · Based on your avg views, we cap at $3 CPM → one video flat fee = {avgVV}/1000 × $3.\n"
        "  · Single video only (not a monthly bundle).\n"
        "  · 50% when your draft is approved, 50% after the video goes live.\n\n"
        "If this works, reply Yes and we'll send the sample + contract details.\n\n"
        "— Linsey"
    )


def _patch_a1_chain(records: list[dict[str, Any]], shop: str) -> None:
    """拆分 A1 开场 vs 接受，补齐谈判链 accept/escalate 记录。"""
    a1 = _find_record(records, "A1-Pure-Commission", "A1-Outreach", "A1-Accept")
    a2 = _find_record(records, "A2-CPM", "A2-Offer", "A2-Accept")
    a3 = _find_record(records, "A3-Flat-Fee", "A3-Offer", "A3-Accept")
    a4 = _find_record(records, "A4-Creator-Quote", "A4-Offer", "A4-Quote")

    base_outreach = copy.deepcopy(a1) if a1 else {}
    outreach_content = base_outreach.get("content") or (
        "Hey {creator}! Loved your recent content—we'd love to send you our product for a sample collab.\n\n"
        "We work commission-only and can give you a higher % than the public Open Plan "
        "if you're open to a pure-commission setup. Sound good?\n\n— Linsey"
    )

    patched: list[dict[str, Any]] = []

    # A1 outreach — proactive, no OK/Sure in title
    outreach = {
        **base_outreach,
        "source_chunk_id": "A1-Outreach",
        "rule_type": "AI执行动作",
        "applicable_shops": shop,
        "creator_type": "A-level",
        "creator_progress": "GEN",
        "intent_category": "Pure Commission",
        "creator_emotion": "GEN",
        "ai_acation": "send A1 pure commission opening",
        "other_creator_conditions": _merge_flags(
            base_outreach.get("other_creator_conditions"),
            ["priority_node:A1"],
        ),
        "title": "A-level outreach · pure commission opening",
        "content": outreach_content,
        "entitle": "A-level outreach · pure commission opening",
        "encontent": outreach_content,
        "answer_purpose": "Open A-tier pure-commission negotiation (A1 opening pitch).",
        "creator_action_guide": "Reply OK/Sure to accept commission-only, or ask for a flat fee to escalate to A2.",
    }
    outreach.pop("key_information", None)
    patched.append(outreach)

    # A1 accept — creator OK/Sure
    accept_a1 = {
        **base_outreach,
        "source_chunk_id": "A1-Accept",
        "rule_type": "AI参考话术",
        "applicable_shops": shop,
        "creator_type": "A-level",
        "creator_progress": "GEN",
        "intent_category": "Pure Commission",
        "creator_emotion": "感兴趣/同意",
        "ai_acation": "push commission rate and sample form",
        "other_creator_conditions": _merge_flags(
            None,
            ["priority_node:A1", "reply_ok"],
        ),
        "title": "OK\nSure\nYes\nSounds good\nI'm in",
        "content": (
            "Amazing—here's your enhanced commission rate (above Open Plan) and the sample shipping form. "
            "Fill it out and we'll get your sample on the way!\n\n— Linsey"
        ),
        "entitle": "OK\nSure\nYes\nSounds good\nI'm in",
        "encontent": (
            "Amazing—here's your enhanced commission rate (above Open Plan) and the sample shipping form. "
            "Fill it out and we'll get your sample on the way!\n\n— Linsey"
        ),
        "answer_purpose": "Confirm A1 pure commission acceptance and deliver commission rate + sample form.",
        "creator_action_guide": "Complete the sample form to start the collaboration.",
    }
    accept_a1.pop("key_information", None)
    patched.append(accept_a1)

    # A2 offer — creator rejected A1 / wants flat fee
    a2_base = copy.deepcopy(a2) if a2 else {}
    a2_offer = {
        **a2_base,
        "source_chunk_id": "A2-Offer",
        "applicable_shops": shop,
        "creator_type": "A-level",
        "intent_category": "CPM Rate",
        "creator_emotion": "GEN",
        "other_creator_conditions": _merge_flags(
            a2_base.get("other_creator_conditions"),
            ["priority_node:A2", "prev_node:A1"],
        ),
        "title": (
            "I need a flat fee\n"
            "I want a base payment\n"
            "Can you do CPM?\n"
            "How much commission?\n"
            "What's the rate?"
        ),
        "answer_purpose": a2_base.get("answer_purpose")
        or "Offer $2 CPM base pay after creator rejects pure commission (A1→A2).",
        "creator_action_guide": "Accept the CPM offer or ask for a fixed price to move to A3.",
    }
    a2_offer.pop("key_information", None)
    patched.append(a2_offer)

    # A2 accept
    a2_accept = {
        **a2_base,
        "source_chunk_id": "A2-Accept",
        "applicable_shops": shop,
        "creator_type": "A-level",
        "intent_category": "CPM Rate",
        "creator_emotion": "感兴趣/同意",
        "other_creator_conditions": _merge_flags(
            None,
            ["priority_node:A2", "prev_node:A1", "reply_ok"],
        ),
        "title": "Deal\nYes let's do it\nI accept the CPM offer\nSounds good",
        "content": (
            "Perfect—we'll ship your sample, set up GMV tracking, and log this in our A-tier partner sheet. "
            "Base pay settles on the 1st of each month per the CPM terms we discussed.\n\n— Linsey"
        ),
        "entitle": "Deal\nYes let's do it\nI accept the CPM offer\nSounds good",
        "encontent": (
            "Perfect—we'll ship your sample, set up GMV tracking, and log this in our A-tier partner sheet. "
            "Base pay settles on the 1st of each month per the CPM terms we discussed.\n\n— Linsey"
        ),
        "answer_purpose": "Close A2 CPM deal after creator accepts.",
        "creator_action_guide": "Wait for sample shipment and publish videos per agreement.",
    }
    a2_accept.pop("key_information", None)
    patched.append(a2_accept)

    # A3 offer — reject A2 / want fixed price
    a3_base = copy.deepcopy(a3) if a3 else {}
    a3_offer = {
        **a3_base,
        "source_chunk_id": "A3-Offer",
        "applicable_shops": shop,
        "creator_type": "A-level",
        "intent_category": "Flat Fee",
        "creator_emotion": "GEN",
        "other_creator_conditions": _merge_flags(
            a3_base.get("other_creator_conditions"),
            ["priority_node:A3", "prev_node:A2"],
        ),
        "title": (
            "I want a fixed price\n"
            "Just give me a flat rate\n"
            "Flat fee only\n"
            "I don't want CPM"
        ),
        "content": a3_base.get("content") or _a3_flat_fee_content(),
        "entitle": (
            "I want a fixed price\n"
            "Just give me a flat rate\n"
            "Flat fee only\n"
            "I don't want CPM"
        ),
        "encontent": a3_base.get("encontent") or _a3_flat_fee_content(),
        "restore_slots": ["avgVV"],
        "answer_purpose": "Offer $3 CPM-capped one-video flat fee after creator rejects A2 (A2→A3).",
        "creator_action_guide": "Accept the flat fee terms or provide your minimum quote for A4.",
    }
    a3_offer.pop("key_information", None)
    patched.append(a3_offer)

    # A3 flat-fee-only shortcut from A1
    a3_skip = copy.deepcopy(a3_offer)
    a3_skip.update(
        {
            "source_chunk_id": "A3-Flat-Fee-Only-Shortcut",
            "other_creator_conditions": ["priority_node:A3", "prev_node:A1"],
            "title": "Flat fee only\nI only do flat fee\nFixed price only",
            "entitle": "Flat fee only\nI only do flat fee\nFixed price only",
            "answer_purpose": "Skip A2 CPM — offer A3 flat fee when creator demands fixed price at A1.",
        }
    )
    patched.append(a3_skip)

    # A4 offer — collect quote
    a4_base = copy.deepcopy(a4) if a4 else {}
    a4_offer = {
        **a4_base,
        "source_chunk_id": "A4-Offer",
        "applicable_shops": shop,
        "creator_type": "A-level",
        "intent_category": "Flat Fee",
        "creator_emotion": "GEN",
        "other_creator_conditions": _merge_flags(
            a4_base.get("other_creator_conditions"),
            ["priority_node:A4", "prev_node:A3"],
        ),
        "title": (
            "Still too low\n"
            "I need more\n"
            "Can you do better?\n"
            "Not interested in that offer"
        ),
        "content": (
            "Totally understand! Could you share your minimum rate per video so I can take it back to the brand? "
            "I'll keep it on file and reach out the moment they greenlight your number.\n\n— Linsey"
        ),
        "entitle": (
            "Still too low\n"
            "I need more\n"
            "Can you do better?\n"
            "Not interested in that offer"
        ),
        "encontent": (
            "Totally understand! Could you share your minimum rate per video so I can take it back to the brand? "
            "I'll keep it on file and reach out the moment they greenlight your number.\n\n— Linsey"
        ),
        "answer_purpose": "Collect creator minimum quote after A3 rejected (A3→A4).",
        "creator_action_guide": "Share your minimum rate per video.",
    }
    a4_offer.pop("key_information", None)
    patched.append(a4_offer)

    # A4 creator quote received
    a4_quote = {
        **a4_base,
        "source_chunk_id": "A4-Quote-Received",
        "applicable_shops": shop,
        "creator_type": "A-level",
        "intent_category": "Creator Quote",
        "creator_emotion": "GEN",
        "other_creator_conditions": _merge_flags(
            a4_base.get("other_creator_conditions"),
            ["priority_node:A4", "prev_node:A3", "reply_quote"],
        ),
        "title": "$XX is my min\nMy rate is $XX\nI need at least $XX\nHere's my minimum",
        "answer_purpose": "Acknowledge creator quote and submit to brand spreadsheet.",
        "creator_action_guide": "Wait for brand feedback on your quoted rate.",
    }
    a4_quote.pop("key_information", None)
    patched.append(a4_quote)

    # Remove old A1-A4 records replaced by chain
    drop_prefixes = (
        "A1-Pure-Commission",
        "A1-Outreach",
        "A1-Accept",
        "A2-",
        "A3-",
        "A4-",
    )
    kept = [
        r
        for r in records
        if not any(str(r.get("source_chunk_id") or "").startswith(p) for p in drop_prefixes)
    ]
    records.clear()
    records.extend(patched + kept)


def _patch_b_c_tiers(records: list[dict[str, Any]], shop: str) -> None:
    b_interest = _find_record(records, "B-Interest", "B-Sample-Interest")
    if b_interest:
        b_interest["ai_acation"] = "one_sample_two_videos"

    b_sample = _find_record(records, "B-SampleRequest", "B-Sample-Request", "B-Accept")
    if b_sample:
        b_sample["ai_acation"] = "one_sample_two_videos"
        b_sample["other_creator_conditions"] = _merge_flags(
            b_sample.get("other_creator_conditions"),
            ["reply_yes", "reply_interested"],
        )
        b_sample["creator_emotion"] = "感兴趣/同意"

    b_reject = _find_record(records, "B-Reject", "B-Decline")
    if b_reject is None:
        records.append(
            normalize_playbook_record_fields(
                desensitize_playbook_record(
                    {
                        "source_chunk_id": "B-Reject-Not-Selected",
                        "source_file": b_sample.get("source_file") if b_sample else f"{shop}/",
                        "rule_type": "AI参考话术",
                        "applicable_shops": shop,
                        "creator_type": "B-level",
                        "creator_progress": "GEN",
                        "intent_category": "AI-UGC Earnings",
                        "creator_emotion": "拒绝合作",
                        "other_creator_conditions": ["b_tier_not_selected"],
                        "title": "Not selected\nDidn't get picked\nBrand passed on me",
                        "content": (
                            "Thanks for your patience! The brand didn't select you for sample-out this round, "
                            "but we'd love to keep you on our AI UGC roster—you can still earn $1/video reposting "
                            "our content. Reply 'AI' if you'd like to join!\n\n— Linsey"
                        ),
                        "entitle": "Not selected\nDidn't get picked\nBrand passed on me",
                        "encontent": (
                            "Thanks for your patience! The brand didn't select you for sample-out this round, "
                            "but we'd love to keep you on our AI UGC roster—you can still earn $1/video reposting "
                            "our content. Reply 'AI' if you'd like to join!\n\n— Linsey"
                        ),
                        "answer_purpose": "B-tier not selected → downgrade path to C-tier AI UGC.",
                        "creator_action_guide": "Reply AI to join AI UGC program.",
                        "language": "en",
                    }
                )
            )
        )

    c_ai = _find_record(records, "C-AI-UGC", "C-AI")
    if c_ai:
        c_ai["other_creator_conditions"] = _merge_flags(
            c_ai.get("other_creator_conditions"),
            ["reply_ai"],
        )


def _strip_toolant_gmv_flags(records: list[dict[str, Any]], shop: str) -> None:
    """toolant 分档用 ``creator_type``；去掉 LLM 遗留的 ``gmv_*`` 匹配 flag。"""
    if shop.lower() != "toolant":
        return
    for rec in records:
        flags = rec.get("other_creator_conditions")
        if not isinstance(flags, list):
            continue
        kept = [f for f in flags if not str(f).startswith("gmv_")]
        if kept:
            rec["other_creator_conditions"] = kept
        else:
            rec.pop("other_creator_conditions", None)


def align_structured_records_with_analysis(
    records: list[dict[str, Any]],
    analysis: dict[str, Any],
    *,
    shop: str,
    source_file: str,
    doc_dir: Path | None = None,
    project_root: Path | None = None,
) -> list[dict[str, Any]]:
    """用 analysis 对齐结构化记录：谈判链、reply 标志、情绪。"""
    if not records or not analysis:
        return records

    out = copy.deepcopy(records)
    for rec in out:
        rec.setdefault("source_file", source_file)
        rec.setdefault("applicable_shops", shop)
        rec.setdefault("language", "en")

    overlay = analysis.get("vocabulary_overlay") or {}
    negotiation = ((overlay.get("_meta") or {}).get("negotiation")) or {}
    if negotiation.get("rounds") and shop.lower() == "toolant":
        _patch_a1_chain(out, shop)

    _patch_b_c_tiers(out, shop)

    if doc_dir is not None:
        from hubstudio_python.kb.service.pipelines.playbook_supplement_materializer import (
            apply_authoritative_supplement_records,
        )

        apply_authoritative_supplement_records(
            out,
            shop=shop,
            doc_dir=doc_dir,
            project_root=project_root,
        )

    _strip_toolant_gmv_flags(out, shop)

    stop = _find_record(out, "Appendix-Stop", "Stop")
    if stop:
        stop["other_creator_conditions"] = _merge_flags(
            stop.get("other_creator_conditions"),
            ["reply_reject", "reply_stop"],
        )
        stop["creator_emotion"] = "拒绝合作"

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for rec in out:
        rec = normalize_playbook_record_fields(desensitize_playbook_record(rec))
        if not rec.get("title") and not rec.get("content"):
            continue
        cid = str(rec.get("source_chunk_id") or "")
        if cid in seen_ids:
            cid = f"{cid}-{len(seen_ids)}"
            rec["source_chunk_id"] = cid
        seen_ids.add(cid)
        normalized.append(rec)

    for i, rec in enumerate(normalized, start=1):
        rec["id"] = str(i)

    return normalized


def analysis_negotiation_summary_json(analysis: dict[str, Any]) -> str:
    """供 prompt 附带的精简 JSON。"""
    payload = {
        "shop": analysis.get("shop"),
        "document_summary_zh": analysis.get("document_summary_zh"),
        "negotiation": (analysis.get("vocabulary_overlay") or {}).get("_meta", {}).get("negotiation"),
        "condition_relationships": analysis.get("condition_relationships"),
        "intent_count": len((analysis.get("intent_classification") or {}).get("intents") or []),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
