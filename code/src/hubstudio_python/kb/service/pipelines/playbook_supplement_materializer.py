"""
权威 supplement 确定性落地（不依赖 LLM 记全）。

登记：``rag_data/kb/authoritative_sources.yaml`` → ``materializer`` 名 → 本模块函数。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from hubstudio_python.models.knowledge_desensitize import desensitize_playbook_record
from hubstudio_python.models.playbook_query_flags import normalize_playbook_record_fields
from hubstudio_python.kb.service.pipelines.authoritative_sources import (
    SupplementSpec,
    authoritative_materializers,
)

logger = logging.getLogger(__name__)

# LLM 产出的同主题规则 — authoritative 写入前移除
_TOOLANT_ADD1_DROP_PREFIXES = (
    "WA-Joined",
    "WA-NotFound",
    "WA-NoWhatsApp",
    "WA-MyNumber",
    "WA-HighGMV",
    "WA-LowGMV",
    "Affiliate-HighGMV",
    "Affiliate-LowGMV",
    "C-FirstVideoBonus",
    "C-AI-Earnings",
    "Add1-",
)

MaterializerFn = Callable[[list[dict[str, Any]], str, Path, SupplementSpec], list[str]]

# add1 链接分流：≥$1k 对应 A/B 档，<$1k 对应 C 档（与 toolant 达人类型一致）
_CREATOR_TYPE_LINK_GTE_1K: list[str] = ["A-level", "B-level"]
_CREATOR_TYPE_LINK_LT_1K = "C-level"


def _rec(
    *,
    shop: str,
    source_file: str,
    chunk_id: str,
    title: str,
    content: str,
    ai_acation: str = "",
    creator_type: str | list[str] = "GEN",
    creator_progress: str = "GEN",
    other_creator_conditions: list[str] | None = None,
    restore_slots: list[str] | None = None,
    key_information: list[str] | None = None,
    intent_category: str = "WhatsApp Feedback",
    answer_purpose: str = "",
    creator_action_guide: str = "",
) -> dict[str, Any]:
    body = content.rstrip() + "\n\n— Linsey"
    raw: dict[str, Any] = {
        "source_chunk_id": chunk_id,
        "source_file": source_file,
        "rule_type": "AI参考话术",
        "applicable_shops": shop,
        "creator_type": creator_type,
        "creator_progress": creator_progress,
        "intent_category": intent_category,
        "creator_reply_frequency": "GEN",
        "creator_emotion": "GEN",
        "title": title,
        "content": body,
        "entitle": title,
        "encontent": body,
        "language": "en",
        "answer_purpose": answer_purpose or f"Authoritative add1: {chunk_id}",
        "creator_action_guide": creator_action_guide or "Follow the next step in the reply.",
    }
    if ai_acation:
        raw["ai_acation"] = ai_acation
    if other_creator_conditions:
        raw["other_creator_conditions"] = other_creator_conditions
    if restore_slots:
        raw["restore_slots"] = restore_slots
    if key_information:
        raw["key_information"] = key_information
    return normalize_playbook_record_fields(desensitize_playbook_record(raw))


def _add1_section1_wa_invite(shop: str, source_file: str) -> list[dict[str, Any]]:
    """add1 §1：按达人类型分流 WhatsApp 群链接（A/B vs C）。"""
    wa_titles = (
        "WhatsApp group link?\nWA please.\nCan I join the WhatsApp group?\n"
        "Send me the WA link.\n群链接\nwhatsapp群\n拉我进whatsapp群"
    )
    return [
        _rec(
            shop=shop,
            source_file=source_file,
            chunk_id="Add1-1-WA-Invite-Gte1k",
            title=wa_titles,
            content=(
                "Sure! Here's the WhatsApp group for creators like you (A/B-tier, monthly GMV $1k+):\n"
                "{wa_group_gmv_gte_1k}\n"
                "Join to connect with other creators and get support from our team."
            ),
            creator_type=_CREATOR_TYPE_LINK_GTE_1K,
            intent_category="WhatsApp",
            other_creator_conditions=["reply_wa"],
            restore_slots=["wa_group_gmv_gte_1k"],
            key_information=["WhatsApp Group Link"],
            ai_acation="send_whatsapp_group_link",
            answer_purpose="add1 §1: A/B 档达人索取 WhatsApp 群链接。",
            creator_action_guide="Tap the link to join the WhatsApp group.",
        ),
        _rec(
            shop=shop,
            source_file=source_file,
            chunk_id="Add1-1-WA-Invite-Lt1k",
            title=wa_titles,
            content=(
                "Sure! Here's the WhatsApp group for growing creators (C-tier, under $1k GMV):\n"
                "{wa_group_gmv_lt_1k}\n"
                "Join for tips, support, and earning opportunities with our team."
            ),
            creator_type=_CREATOR_TYPE_LINK_LT_1K,
            intent_category="WhatsApp",
            other_creator_conditions=["reply_wa"],
            restore_slots=["wa_group_gmv_lt_1k"],
            key_information=["WhatsApp Group Link"],
            ai_acation="send_whatsapp_group_link",
            answer_purpose="add1 §1: C 档达人索取 WhatsApp 群链接。",
            creator_action_guide="Tap the link to join the WhatsApp group.",
        ),
    ]


def _add1_section2_affiliate(shop: str, source_file: str) -> list[dict[str, Any]]:
    """add1 §2：按达人类型分流加窗链接与佣金。"""
    aff_titles = (
        "I want to add your product to my shop.\nGive me the affiliate link.\n"
        "Can I get the product link?\nSend me the link to add to my shop.\n"
        "加窗\n挂链\n商品链接\n橱窗链接"
    )
    return [
        _rec(
            shop=shop,
            source_file=source_file,
            chunk_id="Add1-2-Affiliate-Gte1k",
            title=aff_titles,
            content=(
                "Sure! Here's your affiliate link to add our product to your showcase:\n"
                "{affiliate_link_gmv_gte_1k}\n"
                "You'll earn **5% commission** on every sale through this link."
            ),
            creator_type=_CREATOR_TYPE_LINK_GTE_1K,
            intent_category="Pure Commission",
            restore_slots=["affiliate_link_gmv_gte_1k"],
            key_information=["affiliate_link"],
            ai_acation="send_affiliate_link",
            answer_purpose="add1 §2: A/B 档达人加窗挂链（5% 佣金）。",
            creator_action_guide="Add the product to your showcase using the link.",
        ),
        _rec(
            shop=shop,
            source_file=source_file,
            chunk_id="Add1-2-Affiliate-Lt1k",
            title=aff_titles,
            content=(
                "Sure! Here's your affiliate link to add our product to your showcase:\n"
                "{affiliate_link_gmv_lt_1k}\n"
                "You'll earn **1% commission** on every sale through this link."
            ),
            creator_type=_CREATOR_TYPE_LINK_LT_1K,
            intent_category="Pure Commission",
            restore_slots=["affiliate_link_gmv_lt_1k"],
            key_information=["affiliate_link"],
            ai_acation="send_affiliate_link",
            answer_purpose="add1 §2: C 档达人加窗挂链（1% 佣金）。",
            creator_action_guide="Add the product to your showcase using the link.",
        ),
    ]


def _add1_section3_ai_ugc(shop: str, source_file: str) -> list[dict[str, Any]]:
    """add1 §3：低 GMV 达人 AI 视频带货模式（首条 $5，后续 $1/条，带货 1% 佣金）。"""
    ai_join_titles = (
        "AI\nI want to join AI UGC.\nTell me about AI videos.\n"
        "How does the AI program work?\nAI视频\nAI带货\n怎么参加AI"
    )
    earnings_titles = (
        "How much do I earn per AI video?\nIs it really $1 per video?\n"
        "What's the pay for AI videos?\nAI视频多少钱\n一条视频挣多少"
    )
    first_bonus_titles = (
        "I want to earn $5 for my first video.\nIs the first video really $5?\n"
        "$5 for the first video?\n第一个视频5美元\n首条视频奖励"
    )
    c_flags = ["reply_ai"]
    return [
        _rec(
            shop=shop,
            source_file=source_file,
            chunk_id="Add1-3-AI-UGC-Program",
            title=ai_join_titles,
            content=(
                "Great choice! Our AI UGC program is perfect for creators building momentum. "
                "We provide ready-made videos — you repost them on TikTok and earn:\n"
                "  · **$5 for your first video**\n"
                "  · **$1 for each video after that**\n"
                "  · **1% commission** on any sales you drive\n\n"
                "Reply **AI** and I'll get you set up on our Creator Portal!"
            ),
            creator_type="C-level",
            intent_category="AI-UGC Earnings",
            other_creator_conditions=c_flags,
            ai_acation="add_to_ai_ugc_roster",
            answer_purpose="add1 §3: 低 GMV 达人引入 AI 视频带货模式。",
            creator_action_guide="Reply AI to join the AI UGC program.",
        ),
        _rec(
            shop=shop,
            source_file=source_file,
            chunk_id="Add1-3-AI-UGC-Earnings",
            title=earnings_titles,
            content=(
                "Here's how AI UGC earnings work for you:\n"
                "  · First video: **$5**\n"
                "  · Each additional video: **$1**\n"
                "  · Sales commission: **1%** on orders from your posts\n\n"
                "Even if your GMV is under $1k, this is a great way to start earning with us!"
            ),
            creator_type="C-level",
            intent_category="AI-UGC Earnings",
            answer_purpose="add1 §3: 说明 AI 视频收益结构。",
            creator_action_guide="Reply AI to start or keep posting AI UGC videos.",
        ),
        _rec(
            shop=shop,
            source_file=source_file,
            chunk_id="Add1-3-AI-UGC-FirstVideoBonus",
            title=first_bonus_titles,
            content=(
                "Yes! Your **first AI UGC video earns $5** (instead of the usual $1). "
                "After that, it's $1 per video, plus **1% commission** if your posts drive sales. "
                "It's our way of helping newer creators get started strong!"
            ),
            creator_type="C-level",
            intent_category="AI-UGC Earnings",
            answer_purpose="add1 §3: 首条 AI 视频 $5 奖励说明。",
            creator_action_guide="Reply AI to join and claim your first-video bonus.",
        ),
    ]


def _link_tier_creator_type(tier: str) -> str | list[str]:
    return _CREATOR_TYPE_LINK_LT_1K if tier == "lt1k" else _CREATOR_TYPE_LINK_GTE_1K


def _wa_link_pair(tier: str) -> tuple[str, list[str]]:
    if tier == "lt1k":
        return "wa_group_gmv_lt_1k", ["wa_group_gmv_lt_1k"]
    return "wa_group_gmv_gte_1k", ["wa_group_gmv_gte_1k"]


def _aff_link_pair(tier: str) -> tuple[str, list[str]]:
    if tier == "lt1k":
        return "affiliate_link_gmv_lt_1k", ["affiliate_link_gmv_lt_1k"]
    return "affiliate_link_gmv_gte_1k", ["affiliate_link_gmv_gte_1k"]


def _add1_section4_wa_feedback(shop: str, source_file: str) -> list[dict[str, Any]]:
    """add1 §4：WhatsApp 引流反馈 + join_WA 写库 + 按进度推进。"""
    joined_titles = (
        "I joined the WhatsApp group.\nI'm in the group now.\nThanks, I joined.\n"
        "我加了\n已经加入\n加好了\n加入了群"
    )
    not_found_titles = (
        "I can't find the WhatsApp group.\nI didn't find the link.\nWhere is the group?\n"
        "找不到\n没找到\n找不到群\n链接打不开"
    )
    no_wa_titles = (
        "I don't have WhatsApp.\nNo WhatsApp.\nI don't use WA.\n"
        "没有whatsapp\n没有WA\n不用whatsapp"
    )
    shared_titles = (
        "My WhatsApp is +1234567890.\nHere's my WA number.\nAdd me on WhatsApp.\n"
        "我的whatsapp是\n我的WA是\nwhatsapp是+"
    )

    wa_flags = ["wa_feedback_joined"]
    not_found_flags = ["wa_feedback_not_found"]
    no_wa_flags = ["wa_feedback_no_whatsapp"]
    shared_flags = ["wa_feedback_shared_number"]

    out: list[dict[str, Any]] = []

    for tier in ("gte1k", "lt1k"):
        wa_slot, wa_restore = _wa_link_pair(tier)
        aff_slot, aff_restore = _aff_link_pair(tier)
        ctype = _link_tier_creator_type(tier)
        suffix = "Gte1k" if tier == "gte1k" else "Lt1k"

        out.extend(
            [
                _rec(
                    shop=shop,
                    source_file=source_file,
                    chunk_id=f"Add1-4-WA-Joined-Showcase-{suffix}",
                    title=joined_titles,
                    content=(
                        "Great — thanks for joining our WhatsApp community! "
                        "Next step: please add our product to your showcase using this link:\n"
                        f"{{{aff_slot}}}\nLet me know once you've added it!"
                    ),
                    creator_type=ctype,
                    ai_acation="join_wa_1",
                    creator_progress="未加窗",
                    other_creator_conditions=wa_flags,
                    restore_slots=aff_restore,
                    key_information=["affiliate_link"],
                    answer_purpose=f"add1 §4: 已加 WA，推加窗（{suffix}）。",
                    creator_action_guide="Add product to showcase.",
                ),
                _rec(
                    shop=shop,
                    source_file=source_file,
                    chunk_id=f"Add1-4-WA-NotFound-Showcase-{suffix}",
                    title=not_found_titles,
                    content=(
                        "No worries if you're having trouble finding the group! "
                        f"Here's the WhatsApp link again:\n{{{wa_slot}}}\n\n"
                        "If it still doesn't work, that's okay — we can keep going here. "
                        f"Please try adding our product to your showcase first:\n{{{aff_slot}}}"
                    ),
                    creator_type=ctype,
                    creator_progress="未加窗",
                    other_creator_conditions=not_found_flags,
                    restore_slots=wa_restore + aff_restore,
                    key_information=["WhatsApp Group Link", "affiliate_link"],
                    answer_purpose=f"add1 §4: 找不到 WA 群，重发链接并推加窗（{suffix}）。",
                ),
                _rec(
                    shop=shop,
                    source_file=source_file,
                    chunk_id=f"Add1-4-WA-NotFound-Sample-{suffix}",
                    title=not_found_titles,
                    content=(
                        f"No worries! Here's the WhatsApp link again:\n{{{wa_slot}}}\n\n"
                        "If it still doesn't work, that's okay — please go ahead and apply for a sample "
                        "when you're ready — send your shipping details and we'll prioritize your request."
                    ),
                    creator_type=ctype,
                    creator_progress="已加窗",
                    other_creator_conditions=not_found_flags + ["未申请批样"],
                    restore_slots=wa_restore,
                    key_information=["WhatsApp Group Link"],
                    answer_purpose=f"add1 §4: 找不到 WA 群，重发链接并推申样（{suffix}）。",
                ),
                _rec(
                    shop=shop,
                    source_file=source_file,
                    chunk_id=f"Add1-4-WA-NotFound-SampleReview-{suffix}",
                    title=not_found_titles,
                    content=(
                        f"Here's the link again:\n{{{wa_slot}}}\n\n"
                        "We've noted your sample application and will review it shortly. "
                        "Thank you for your patience!"
                    ),
                    creator_type=ctype,
                    creator_progress="已申请批样",
                    other_creator_conditions=not_found_flags,
                    restore_slots=wa_restore,
                    key_information=["WhatsApp Group Link"],
                    answer_purpose=f"add1 §4: 找不到 WA 群，已申样待审核（{suffix}）。",
                ),
                _rec(
                    shop=shop,
                    source_file=source_file,
                    chunk_id=f"Add1-4-WA-NoWhatsApp-Showcase-{suffix}",
                    title=no_wa_titles,
                    content=(
                        "No problem — we can keep everything here in chat. "
                        f"Please add our product to your showcase using this link:\n{{{aff_slot}}}\n"
                        "Let me know once it's done!"
                    ),
                    creator_type=ctype,
                    ai_acation="join_wa_2",
                    creator_progress="未加窗",
                    other_creator_conditions=no_wa_flags,
                    restore_slots=aff_restore,
                    key_information=["affiliate_link"],
                    answer_purpose=f"add1 §4: 无 WhatsApp，推加窗（{suffix}）。",
                ),
                _rec(
                    shop=shop,
                    source_file=source_file,
                    chunk_id=f"Add1-4-WA-SharedNumber-{suffix}",
                    title=shared_titles,
                    content=(
                        "Thanks for sharing! For group management we're not able to add creators directly — "
                        f"please tap this link to join our WhatsApp community yourself:\n{{{wa_slot}}}\n"
                        "Looking forward to connecting with you there!"
                    ),
                    creator_type=ctype,
                    other_creator_conditions=shared_flags,
                    restore_slots=wa_restore,
                    key_information=["WhatsApp Group Link"],
                    answer_purpose=f"add1 §4: 达人报 WA 号码，引导自行加群（{suffix}）。",
                ),
            ]
        )

    # 进度分支（不依赖 GMV 链接槽位）
    out.extend(
        [
            _rec(
                shop=shop,
                source_file=source_file,
                chunk_id="Add1-4-WA-Joined-Sample",
                title=joined_titles,
                content=(
                    "Awesome — thanks for joining our WhatsApp group! "
                    "Next, please apply for a sample: share your full name, shipping address, "
                    "and phone number so we can prioritize your application."
                ),
                ai_acation="join_wa_1",
                creator_progress="已加窗",
                other_creator_conditions=wa_flags + ["未申请批样"],
                answer_purpose="add1 §4: 已加 WA，已加窗，推申样。",
            ),
            _rec(
                shop=shop,
                source_file=source_file,
                chunk_id="Add1-4-WA-Joined-SampleReview",
                title=joined_titles,
                content=(
                    "Thank you for your support! We've received your sample application "
                    "and will review it as soon as possible. We'll reach out once it's approved."
                ),
                ai_acation="join_wa_1",
                creator_progress="已申请批样",
                other_creator_conditions=wa_flags,
                answer_purpose="add1 §4: 已加 WA 且已申样，感谢并告知审核中。",
            ),
            _rec(
                shop=shop,
                source_file=source_file,
                chunk_id="Add1-4-WA-NoWhatsApp-Sample",
                title=no_wa_titles,
                content=(
                    "No problem — we can keep everything here in chat. "
                    "When you're ready, please share your shipping details to apply for a sample — "
                    "we'll prioritize your application."
                ),
                ai_acation="join_wa_2",
                creator_progress="已加窗",
                other_creator_conditions=no_wa_flags + ["未申请批样"],
                answer_purpose="add1 §4: 无 WhatsApp，已加窗，推申样。",
            ),
            _rec(
                shop=shop,
                source_file=source_file,
                chunk_id="Add1-4-WA-NoWhatsApp-SampleReview",
                title=no_wa_titles,
                content=(
                    "No problem — we can keep everything here in chat. "
                    "Thank you for applying for a sample! We'll review your request "
                    "and get back to you as soon as possible."
                ),
                ai_acation="join_wa_2",
                creator_progress="已申请批样",
                other_creator_conditions=no_wa_flags,
                answer_purpose="add1 §4: 无 WhatsApp，已申样，感谢并告知审核中。",
            ),
        ]
    )
    return out


def _materialize_toolant_add1(
    records: list[dict[str, Any]],
    shop: str,
    doc_dir: Path,
    spec: SupplementSpec,
) -> list[str]:
    sup_path = doc_dir / spec.file
    if not sup_path.is_file():
        logger.warning("Authoritative supplement missing, skip materializer %s: %s", spec.materializer, sup_path)
        return []

    source_file = spec.file.replace("\\", "/")

    kept = [
        r
        for r in records
        if not any(str(r.get("source_chunk_id") or "").startswith(p) for p in _TOOLANT_ADD1_DROP_PREFIXES)
    ]
    records.clear()
    records.extend(kept)

    new_records: list[dict[str, Any]] = []
    new_records.extend(_add1_section1_wa_invite(shop, source_file))
    new_records.extend(_add1_section2_affiliate(shop, source_file))
    new_records.extend(_add1_section3_ai_ugc(shop, source_file))
    new_records.extend(_add1_section4_wa_feedback(shop, source_file))

    records.extend(new_records)
    applied = [r["source_chunk_id"] for r in new_records]
    logger.info(
        "Materialized %s: %d authoritative records from %s (§1=%d §2=%d §3=%d §4=%d)",
        spec.materializer,
        len(new_records),
        spec.file,
        2,
        2,
        3,
        len(new_records) - 7,
    )
    return applied


_MATERIALIZERS: dict[str, MaterializerFn] = {
    "toolant_add1": _materialize_toolant_add1,
}


def apply_authoritative_supplement_records(
    records: list[dict[str, Any]],
    *,
    shop: str,
    doc_dir: Path,
    project_root: Path | None = None,
) -> list[str]:
    """
    按 manifest 执行 authoritative materializer，就地修改 ``records``。

    :return: 写入的 ``source_chunk_id`` 列表（日志/验收用）。
    """
    applied: list[str] = []
    for spec in authoritative_materializers(shop, project_root=project_root):
        fn = _MATERIALIZERS.get(spec.materializer or "")
        if fn is None:
            logger.warning("Unknown materializer %r for %s", spec.materializer, spec.file)
            continue
        applied.extend(fn(records, shop, doc_dir, spec))
    return applied
