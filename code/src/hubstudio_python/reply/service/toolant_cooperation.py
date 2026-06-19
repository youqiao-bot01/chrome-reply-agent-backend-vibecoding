"""toolant ``cooperation_intention`` 写 MySQL。"""

from __future__ import annotations

from typing import Any

COOPERATION_INTENTION_ONE_SAMPLE_TWO_VIDEOS = "one_sample_two_videos"


def write_cooperation_intention(
    creator_id: str,
    shop: str,
    value: str,
) -> dict[str, Any] | None:
    """写入 ``us_region_creator.cooperation_intention``。"""
    cid = str(creator_id or "").strip()
    if not cid or shop.strip().lower() != "toolant":
        return None
    if not value.strip():
        return None

    from hubstudio_python.reply.sql.mysql_config import mysql_config_from_env
    from hubstudio_python.reply.sql.mysql_creator import update_creator_columns

    cfg = mysql_config_from_env()
    mysql_result = update_creator_columns(
        cid,
        {cfg.cooperation_intention_column: value.strip()},
    )
    return {
        "cooperation_intention": value.strip(),
        "mysql": mysql_result,
    }
