"""达人 × 店铺状态读写，供回复流水线组装 ``CreatorRuleContext``。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

from hubstudio_python.reply.sql.config import db_config_from_env
from hubstudio_python.reply.sql.connection import get_connection

if TYPE_CHECKING:
    from hubstudio_python.reply.service.rules.schema import CreatorRuleContext

_PROGRESS_SPLIT = re.compile(r"[,，;；]+")


@dataclass
class CreatorShopState:
    creator_id: str
    shop: str
    creator_name: str = ""
    creator_type: str = "GEN"
    creator_progress: str | list[str] = "GEN"
    creator_emotion: str = "GEN"
    monthly_gmv: float | None = None
    other_creator_conditions: list[str] = field(default_factory=list)
    shop_rejected: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["shop_rejected"] = bool(self.shop_rejected)
        return d


def _parse_progress(raw: str | None) -> str | list[str]:
    if not raw or raw.strip().upper() == "GEN":
        return "GEN"
    s = raw.strip()
    if s.startswith("["):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list) and parsed:
                return [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            pass
    if _PROGRESS_SPLIT.search(s):
        parts = [p.strip() for p in _PROGRESS_SPLIT.split(s) if p.strip()]
        return parts if len(parts) > 1 else (parts[0] if parts else "GEN")
    return s


def _row_to_state(row: Any) -> CreatorShopState:
    flags_raw = row["other_creator_conditions"] or "[]"
    try:
        flags = json.loads(flags_raw) if isinstance(flags_raw, str) else flags_raw
        if not isinstance(flags, list):
            flags = []
    except json.JSONDecodeError:
        flags = []
    extra_raw = row["extra_json"] or "{}"
    try:
        extra = json.loads(extra_raw) if isinstance(extra_raw, str) else {}
        if not isinstance(extra, dict):
            extra = {}
    except json.JSONDecodeError:
        extra = {}
    gmv = row["monthly_gmv"]
    return CreatorShopState(
        creator_id=str(row["creator_id"]),
        shop=str(row["shop"]),
        creator_name=str(row["creator_name"] or ""),
        creator_type=str(row["creator_type"] or "GEN"),
        creator_progress=_parse_progress(str(row["creator_progress"] or "GEN")),
        creator_emotion=str(row["creator_emotion"] or "GEN"),
        monthly_gmv=float(gmv) if gmv is not None else None,
        other_creator_conditions=[str(x) for x in flags],
        shop_rejected=bool(row["shop_rejected"]),
        extra=extra,
    )


def get_creator_shop_state(creator_id: str, shop: str) -> CreatorShopState | None:
    cfg = db_config_from_env()
    if not cfg.enabled or not creator_id.strip() or not shop.strip():
        return None
    with get_connection(cfg) as conn:
        row = conn.execute(
            "SELECT * FROM creator_shop_state WHERE creator_id = ? AND shop = ?",
            (creator_id.strip(), shop.strip()),
        ).fetchone()
    if row is None:
        return None
    return _row_to_state(row)


def upsert_creator_shop_state(state: CreatorShopState) -> CreatorShopState:
    cfg = db_config_from_env()
    if not cfg.enabled:
        return state
    prog = state.creator_progress
    if isinstance(prog, list):
        prog_json = json.dumps(prog, ensure_ascii=False)
    else:
        prog_json = str(prog or "GEN")
    with get_connection(cfg) as conn:
        conn.execute(
            """
            INSERT INTO creator_shop_state (
                creator_id, shop, creator_name, creator_type, creator_progress,
                creator_emotion, monthly_gmv, other_creator_conditions,
                shop_rejected, extra_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(creator_id, shop) DO UPDATE SET
                creator_name = excluded.creator_name,
                creator_type = excluded.creator_type,
                creator_progress = excluded.creator_progress,
                creator_emotion = excluded.creator_emotion,
                monthly_gmv = excluded.monthly_gmv,
                other_creator_conditions = excluded.other_creator_conditions,
                shop_rejected = excluded.shop_rejected,
                extra_json = excluded.extra_json,
                updated_at = datetime('now')
            """,
            (
                state.creator_id.strip(),
                state.shop.strip(),
                state.creator_name,
                state.creator_type,
                prog_json,
                state.creator_emotion,
                state.monthly_gmv,
                json.dumps(state.other_creator_conditions, ensure_ascii=False),
                1 if state.shop_rejected else 0,
                json.dumps(state.extra, ensure_ascii=False),
            ),
        )
    return state


def merge_db_into_request(req: Any) -> tuple[Any, CreatorShopState | None]:
    """
    用 DB 补全请求里未传的字段；请求显式传入的非空值优先。
    ``req`` 为 ``GenerateReplyRequest``。
    """
    creator_id = str(getattr(req, "creator_id", "") or "").strip()
    shop = str(getattr(req, "shop", "") or "").strip()
    if not creator_id or not shop:
        return req, None
    state = get_creator_shop_state(creator_id, shop)
    if state is None:
        return req, None

    if (not req.creator_type or req.creator_type == "GEN") and state.creator_type != "GEN":
        req.creator_type = state.creator_type
    if not req.creator_progress and state.creator_progress != "GEN":
        req.creator_progress = (
            list(state.creator_progress)
            if isinstance(state.creator_progress, list)
            else [str(state.creator_progress)]
        )
    if (not req.creator_emotion or req.creator_emotion == "GEN") and state.creator_emotion != "GEN":
        req.creator_emotion = state.creator_emotion
    if req.monthly_gmv is None and state.monthly_gmv is not None:
        req.monthly_gmv = state.monthly_gmv
    if not req.other_creator_conditions and state.other_creator_conditions:
        req.other_creator_conditions = list(state.other_creator_conditions)
    return req, state


def apply_post_reply_updates(
    *,
    creator_id: str,
    shop: str,
    intent_category: str,
    matched_rule: str,
    ai_acation: str = "",
    creator_name: str = "",
    latest_message: str = "",
    generated_reply: str = "",
    matched_rule_record: dict[str, Any] | None = None,
    ctx: CreatorRuleContext | None = None,
) -> dict[str, Any] | None:
    """回复成功后写回 DB（拒绝标记等），返回变更摘要。"""
    cfg = db_config_from_env()
    if not cfg.enabled or not creator_id.strip() or not shop.strip():
        return None

    state = get_creator_shop_state(creator_id, shop) or CreatorShopState(
        creator_id=creator_id.strip(),
        shop=shop.strip(),
        creator_name=creator_name,
    )
    if creator_name and not state.creator_name:
        state.creator_name = creator_name

    updates: dict[str, Any] = {}
    ic = (intent_category or "").strip().lower()
    rule = (matched_rule or "").strip().lower()
    action = (ai_acation or "").strip().lower()

    reject_hit = rule in {"reject", "stop"} or ic in {"reject", "拒绝", "stop"} or action in {
        "停止",
        "stop_outreach",
        "mark_shop_rejected",
    }
    if reject_hit and not state.shop_rejected:
        state.shop_rejected = True
        state.creator_progress = "已拒绝"
        state.creator_emotion = "拒绝合作"
        updates["shop_rejected"] = True
        updates["creator_progress"] = "已拒绝"
        upsert_creator_shop_state(state)

    from hubstudio_python.reply.sql.ai_action_effects import AiActionContext, apply_ai_action_effects

    action_effect = apply_ai_action_effects(
        AiActionContext(
            creator_id=creator_id,
            shop=shop,
            ai_acation=ai_acation,
            latest_message=latest_message,
            generated_reply=generated_reply,
            intent_category=intent_category,
            matched_rule_record=matched_rule_record or {},
            ctx=ctx,
        )
    )
    if action_effect:
        updates["ai_acation"] = action_effect

    if ctx and matched_rule_record:
        from hubstudio_python.reply.service.negotiation_sync import persist_negotiation_round_after_reply

        neg = persist_negotiation_round_after_reply(
            creator_id,
            ctx=ctx,
            matched_rule_record=matched_rule_record,
        )
        if neg:
            updates["negotiation_round"] = neg

    return updates or None
