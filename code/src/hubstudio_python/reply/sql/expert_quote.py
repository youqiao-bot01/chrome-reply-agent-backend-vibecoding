"""写入 ``influencer_platform.us_region_creator.expert_quote``。"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from hubstudio_python.reply.sql.mysql_config import MysqlConfig, mysql_config_from_env
from hubstudio_python.reply.sql.mysql_creator import update_creator_columns


def format_expert_quote(amount_usd: float) -> str:
    d = Decimal(str(amount_usd)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(d, "f")


def update_expert_quote(
    creator_id: str,
    amount_usd: float,
    *,
    config: MysqlConfig | None = None,
) -> dict[str, Any]:
    cfg = config or mysql_config_from_env()
    cid = str(creator_id or "").strip()
    if not cfg.enabled:
        return {"updated": False, "reason": "mysql_disabled"}
    if not cid:
        return {"updated": False, "reason": "missing_creator_id"}

    quote_value = format_expert_quote(amount_usd)
    result = update_creator_columns(
        cid,
        {cfg.expert_quote_column: quote_value},
        config=cfg,
    )
    result["expert_quote"] = quote_value
    return result
