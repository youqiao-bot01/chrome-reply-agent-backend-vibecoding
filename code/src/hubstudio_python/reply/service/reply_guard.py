"""
回复输出校验：链接必须来自 Key information / 知识库；无匹配知识库不返回臆造内容；
末尾须带 ``— Linsey`` 署名；Top1 规则 ``key_information`` 定值须落地到正文。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from hubstudio_python.models.knowledge_desensitize import resolve_key_information_for_record
from hubstudio_python.models.shop_restore_values import load_shop_restore_slots

_URL_RE = re.compile(r"https?://[^\s\)\]\"'<>]+|wa\.me/[^\s\)\]\"'<>]+", re.I)
_MONEY_RE = re.compile(r"\$\s*\d[\d,]*(?:\.\d+)?")
_PERCENT_RE = re.compile(r"\b\d+(?:\.\d+)?\s*%")
_DEADLINE_RE = re.compile(
    r"\b(?:within|in|after)\s+\d+\s+(?:business\s+)?(?:days?|hours?|weeks?)\b",
    re.I,
)
_PLACEHOLDER_RE = re.compile(r"\[[^\]]+\]|\{[a-zA-Z_]+\}")
_LINSEY_SIGNATURE_RE = re.compile(r"\s*[—\-]\s*Linsey\s*$", re.I)
LINSEY_SIGNATURE_SUFFIX = "\n\n— Linsey"


@dataclass(frozen=True)
class ReplyValidation:
    ok: bool
    reason: str = ""
    required_urls: tuple[str, ...] = ()
    authorized_urls: tuple[str, ...] = ()
    reply_urls: tuple[str, ...] = ()
    missing_key_information: tuple[str, ...] = ()
    has_linsey_signature: bool = False


def _normalize_url(url: str) -> str:
    return url.rstrip(".,;:!?)\"'")


def extract_urls(text: str) -> list[str]:
    return [_normalize_url(m.group(0)) for m in _URL_RE.finditer(text or "")]


def has_linsey_signature(text: str) -> bool:
    return bool(_LINSEY_SIGNATURE_RE.search((text or "").rstrip()))


def ensure_linsey_signature(body: str) -> str:
    """统一末尾署名：换行 + ``— Linsey``（已有时先去掉再补，避免重复）。"""
    text = (body or "").rstrip()
    if not text:
        return text
    text = _LINSEY_SIGNATURE_RE.sub("", text).rstrip()
    return f"{text}{LINSEY_SIGNATURE_SUFFIX}"


def required_key_information_values(
    rule: dict[str, Any],
    shop: str,
    *,
    script: str = "",
) -> list[str]:
    """
    Top1 规则声明的 ``key_information`` 对应 restore 定值，须在最终回复中出现。
    """
    rec = dict(rule)
    rec.setdefault("applicable_shops", shop)
    ki = resolve_key_information_for_record(rec, shop) or []
    if not ki:
        return []

    slot_values = load_shop_restore_slots(shop)
    required: list[str] = []
    seen: set[str] = set()
    for slot in _rule_restore_slots(rule):
        val = str(slot_values.get(slot) or "").strip()
        if not val or val in seen:
            continue
        seen.add(val)
        required.append(val)

    if not required and script:
        for u in extract_urls(script):
            if u not in seen:
                seen.add(u)
                required.append(u)
    return required


def _rule_restore_slots(rule: dict[str, Any]) -> list[str]:
    raw = rule.get("restore_slots")
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        if raw.strip().startswith("["):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(x).strip() for x in parsed if str(x).strip()]
            except json.JSONDecodeError:
                pass
        return [raw.strip()]
    return []


def authorized_urls_for_references(
    references: list[tuple[dict[str, Any], str]],
    shop: str,
) -> set[str]:
    """Key information + restore_slots 展开后的合法 URL 集合。"""
    slot_values = load_shop_restore_slots(shop)
    urls: set[str] = set()
    for rule, script in references:
        for slot in _rule_restore_slots(rule):
            val = slot_values.get(slot, "")
            if val:
                urls.update(extract_urls(val))
        for u in extract_urls(script):
            urls.add(u)
        rec = dict(rule)
        rec.setdefault("applicable_shops", shop)
        ki = resolve_key_information_for_record(rec, shop) or []
        for tag in ki:
            for slot, val in slot_values.items():
                if val and tag.lower().replace(" ", "_") in slot.lower():
                    urls.update(extract_urls(val))
    return urls


def required_urls_for_top_rule(rule: dict[str, Any], shop: str, *, script: str) -> list[str]:
    """Top1 规则若带 key_information，则本回合回复须包含对应 URL（verbatim）。"""
    rec = dict(rule)
    rec.setdefault("applicable_shops", shop)
    ki = resolve_key_information_for_record(rec, shop) or []
    if not ki:
        return []

    slot_values = load_shop_restore_slots(shop)
    required: list[str] = []
    slots = _rule_restore_slots(rule)
    for slot in slots:
        val = slot_values.get(slot, "")
        if val and extract_urls(val):
            required.append(_normalize_url(extract_urls(val)[0]))

    if not required:
        script_urls = extract_urls(script)
        if script_urls:
            required.append(script_urls[0])
    return list(dict.fromkeys(required))


def _allowed_substrings(*texts: str) -> str:
    return "\n".join(t for t in texts if t)


def validate_reply_output(
    reply: str,
    *,
    references: list[tuple[dict[str, Any], str]],
    shop: str,
    context_text: str,
    reference_bundle: str,
) -> ReplyValidation:
    body = (reply or "").strip()
    if not body:
        return ReplyValidation(ok=False, reason="empty_reply")

    if not has_linsey_signature(body):
        return ReplyValidation(
            ok=False,
            reason="missing_linsey_signature",
            has_linsey_signature=False,
        )

    if _PLACEHOLDER_RE.search(body):
        return ReplyValidation(ok=False, reason="placeholder_in_reply", has_linsey_signature=True)

    authorized = authorized_urls_for_references(references, shop)
    reply_urls = extract_urls(body)
    allowed_text = _allowed_substrings(context_text, reference_bundle)

    for u in reply_urls:
        if u not in authorized:
            return ReplyValidation(
                ok=False,
                reason="unauthorized_url",
                authorized_urls=tuple(sorted(authorized)),
                reply_urls=tuple(reply_urls),
            )

    if references:
        top_rule, top_script = references[0]
        required = required_urls_for_top_rule(top_rule, shop, script=top_script)
        if required:
            missing = [u for u in required if u not in body]
            if missing:
                return ReplyValidation(
                    ok=False,
                    reason="missing_required_url",
                    required_urls=tuple(required),
                    authorized_urls=tuple(sorted(authorized)),
                    reply_urls=tuple(reply_urls),
                    has_linsey_signature=True,
                )

        ki_required = required_key_information_values(top_rule, shop, script=top_script)
        missing_ki = [v for v in ki_required if v not in body]
        if missing_ki:
            return ReplyValidation(
                ok=False,
                reason="missing_key_information",
                missing_key_information=tuple(missing_ki),
                authorized_urls=tuple(sorted(authorized)),
                reply_urls=tuple(reply_urls),
                has_linsey_signature=True,
            )

    for pattern, label in (
        (_MONEY_RE, "unauthorized_money"),
        (_PERCENT_RE, "unauthorized_percent"),
        (_DEADLINE_RE, "unauthorized_deadline"),
    ):
        for m in pattern.finditer(body):
            frag = m.group(0)
            if frag not in allowed_text:
                return ReplyValidation(ok=False, reason=label)

    return ReplyValidation(
        ok=True,
        authorized_urls=tuple(sorted(authorized)),
        reply_urls=tuple(reply_urls),
        required_urls=tuple(
            required_urls_for_top_rule(references[0][0], shop, script=references[0][1])
            if references
            else ()
        ),
        has_linsey_signature=True,
    )
