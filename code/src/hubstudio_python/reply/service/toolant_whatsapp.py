"""
toolant WhatsApp 引流反馈（``add1.txt`` §4）：

- 我加了 → ``join_WA=1``，按加窗/申样进度推进
- 找不到 → 重发群链接，再推加窗或申样
- 没有 WhatsApp → ``join_WA=2``，按进度推进
- 报 WhatsApp 号码 → 说明无法主动添加，请达人自行加群
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from hubstudio_python.models.shop_restore_values import apply_restore_slots, load_shop_restore_slots

_JOINED_RE = re.compile(
    r"(?:\b(?:i\s+)?joined\b|\b(?:i\s+)?added\b|already\s+in|"
    r"published\+joined|scanned\s*\+\s*completed|"
    r"我加了|已经加入|加入了群|加好了)",
    re.I,
)
_NOT_FOUND_RE = re.compile(
    r"(?:can'?t\s+find|cannot\s+find|didn'?t\s+find|not\s+find|"
    r"找不到|没找到|找不到群|链接打不开|link\s+doesn'?t\s+work|where\s+is\s+the\s+group)",
    re.I,
)
_NO_WA_RE = re.compile(
    r"(?:don'?t\s+have\s+whatsapp|no\s+whatsapp|without\s+whatsapp|"
    r"don'?t\s+use\s+whatsapp|没有\s*whatsapp|没有\s*wa|不用\s*whatsapp)",
    re.I,
)
_SHARED_NUMBER_RE = re.compile(
    r"(?:my\s+whatsapp\s+(?:is|number)|whatsapp\s*(?:is|:)?\s*(\+?\d[\d\s\-()]{7,20}))"
    r"|(?:whatsapp|wa).{0,20}(\+?\d[\d\s\-()]{9,18}\d)",
    re.I,
)
_PHONE_ONLY_RE = re.compile(r"(?:^|\s)(\+?\d[\d\s\-()]{9,18}\d)(?:\s|$)")


@dataclass(frozen=True)
class WhatsappFlowResult:
    detected: bool
    response_type: str
    join_wa_value: int | None
    conversion_beat: str
    reply: str
    mysql: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "detected": self.detected,
            "response_type": self.response_type,
            "join_wa_value": self.join_wa_value,
            "conversion_beat": self.conversion_beat,
            "mysql": self.mysql,
        }


def classify_whatsapp_response(message: str) -> str | None:
    text = (message or "").strip()
    if not text:
        return None
    if _NO_WA_RE.search(text):
        return "no_whatsapp"
    if _SHARED_NUMBER_RE.search(text) or (
        re.search(r"whatsapp|wa", text, re.I) and _PHONE_ONLY_RE.search(text)
    ):
        return "shared_number"
    if _NOT_FOUND_RE.search(text):
        return "not_found"
    if _JOINED_RE.search(text):
        return "joined"
    return None


def _progress_flags(progress: list[str] | str) -> set[str]:
    if isinstance(progress, list):
        items = progress
    elif progress:
        items = [str(progress)]
    else:
        items = []
    return {str(p).strip() for p in items if str(p).strip()}


def infer_toolant_conversion_beat(
    *,
    creator_progress: list[str] | str,
    response_type: str,
) -> str:
    """
    WhatsApp → 加窗 → 申样 → 解释/答疑（``gen.txt``）。

    已加 WA 且已申请批样 → ``sample_review``；否则按进度推下一步。
    """
    flags = _progress_flags(creator_progress)
    if "已申请批样" in flags:
        return "sample_review"
    if response_type == "joined" and "已加窗" in flags and "未申请批样" in flags:
        return "sample"
    if "未加窗" in flags or not flags:
        return "showcase"
    if "已加窗" in flags:
        return "sample"
    return "showcase"


def _gmv_tier(monthly_gmv: float | None) -> str:
    if monthly_gmv is not None and monthly_gmv >= 1000:
        return "gte_1k"
    return "lt_1k"


def _slot_values(shop: str, monthly_gmv: float | None) -> dict[str, str]:
    slots = load_shop_restore_slots(shop)
    tier = _gmv_tier(monthly_gmv)
    wa_key = f"wa_group_gmv_{tier}"
    aff_key = f"affiliate_link_gmv_{tier}"
    return {
        "wa_link": slots.get(wa_key, slots.get("wa_group_gmv_gte_1k", "")),
        "affiliate_link": slots.get(aff_key, slots.get("affiliate_link_gmv_gte_1k", "")),
    }


def _build_reply(
    *,
    response_type: str,
    beat: str,
    shop: str,
    monthly_gmv: float | None,
) -> str:
    links = _slot_values(shop, monthly_gmv)
    wa = links["wa_link"]
    aff = links["affiliate_link"]

    if response_type == "joined":
        if beat == "sample_review":
            body = (
                "Thank you for your support! We've received your sample application "
                "and will review it as soon as possible. We'll reach out once it's approved."
            )
        elif beat == "sample":
            body = (
                "Awesome — thanks for joining our WhatsApp group! "
                "Next, please apply for a sample: share your full name, shipping address, "
                "and phone number so we can prioritize your application."
            )
        else:
            body = (
                "Great — thanks for joining our WhatsApp community! "
                f"Next step: please add our product to your showcase using this link:\n{aff}\n"
                "Let me know once you've added it!"
            )
    elif response_type == "not_found":
        body = (
            "No worries if you're having trouble finding the group! "
            f"Here's the WhatsApp link again:\n{wa}\n\n"
            "If it still doesn't work, that's okay — we can keep going here. "
        )
        if beat == "sample":
            body += (
                "Please go ahead and apply for a sample when you're ready — "
                "send your shipping details and we'll prioritize your request."
            )
        elif beat == "sample_review":
            body += (
                "We've noted your sample application and will review it shortly. "
                "Thank you for your patience!"
            )
        else:
            body += (
                f"Please try adding our product to your showcase first:\n{aff}"
            )
    elif response_type == "no_whatsapp":
        body = "No problem — we can keep everything here in chat. "
        if beat == "sample_review":
            body += (
                "Thank you for applying for a sample! We'll review your request "
                "and get back to you as soon as possible."
            )
        elif beat == "sample":
            body += (
                "When you're ready, please share your shipping details to apply for a sample — "
                "we'll prioritize your application."
            )
        else:
            body += (
                f"Please add our product to your showcase using this link:\n{aff}\n"
                "Let me know once it's done!"
            )
    elif response_type == "shared_number":
        body = (
            "Thanks for sharing! For group management we're not able to add creators directly — "
            f"please tap this link to join our WhatsApp community yourself:\n{wa}\n"
            "Looking forward to connecting with you there!"
        )
    else:
        return ""

    return apply_restore_slots(f"{body}\n\n— Linsey", shop)


def try_handle_whatsapp_response(
    *,
    shop: str,
    creator_id: str,
    creator_name: str = "",
    latest_message: str,
    creator_progress: list[str] | str,
    monthly_gmv: float | None = None,
) -> WhatsappFlowResult | None:
    if shop.strip().lower() != "toolant":
        return None

    response_type = classify_whatsapp_response(latest_message)
    if not response_type:
        return None

    beat = infer_toolant_conversion_beat(
        creator_progress=creator_progress,
        response_type=response_type,
    )
    reply = _build_reply(
        response_type=response_type,
        beat=beat,
        shop=shop,
        monthly_gmv=monthly_gmv,
    )
    if not reply.strip():
        return None

    mysql_result = None
    join_wa: int | None = None
    if response_type == "joined":
        join_wa = 1
    elif response_type == "no_whatsapp":
        join_wa = 2

    from hubstudio_python.reply.sql.mysql_creator import resolve_mysql_creator_key, update_join_wa

    lookup_key = resolve_mysql_creator_key(creator_id, creator_name)
    if join_wa is not None and lookup_key:
        mysql_result = update_join_wa(lookup_key, join_wa)

    return WhatsappFlowResult(
        detected=True,
        response_type=response_type,
        join_wa_value=join_wa,
        conversion_beat=beat,
        reply=reply,
        mysql=mysql_result,
    )
