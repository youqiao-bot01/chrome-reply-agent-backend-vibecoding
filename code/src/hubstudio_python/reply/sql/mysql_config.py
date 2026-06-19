"""MySQL 配置：``influencer_platform`` 等业务库。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class MysqlConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = ""
    password: str = ""
    database: str = "influencer_platform"
    creator_table: str = "us_region_creator"
    creator_id_column: str = "creators_name"
    expert_quote_column: str = "expert_quote"
    join_wa_column: str = "join_WA"
    cooperation_intention_column: str = "cooperation_intention"
    negotiation_rounds_column: str = "negotiation_rounds"
    ai_reply_log_database: str = "controlpastmessagesdata"
    ai_reply_log_table: str = "auto_reply_plugin_ai_reply_info"
    connect_timeout: int = 10


def mysql_config_from_env() -> MysqlConfig:
    enabled_raw = os.environ.get("HUBSTUDIO_MYSQL_ENABLED", "false").strip().lower()
    enabled = enabled_raw in ("1", "true", "yes", "on")
    port_raw = os.environ.get("HUBSTUDIO_MYSQL_PORT", "3306").strip() or "3306"
    try:
        port = int(port_raw)
    except ValueError:
        port = 3306
    timeout_raw = os.environ.get("HUBSTUDIO_MYSQL_CONNECT_TIMEOUT", "10").strip() or "10"
    try:
        timeout = int(timeout_raw)
    except ValueError:
        timeout = 10
    return MysqlConfig(
        enabled=enabled,
        host=os.environ.get("HUBSTUDIO_MYSQL_HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=port,
        user=os.environ.get("HUBSTUDIO_MYSQL_USER", "").strip(),
        password=os.environ.get("HUBSTUDIO_MYSQL_PASSWORD", "").strip(),
        database=os.environ.get("HUBSTUDIO_MYSQL_DATABASE", "influencer_platform").strip()
        or "influencer_platform",
        creator_table=os.environ.get("HUBSTUDIO_MYSQL_CREATOR_TABLE", "us_region_creator").strip()
        or "us_region_creator",
        creator_id_column=os.environ.get("HUBSTUDIO_MYSQL_CREATOR_ID_COLUMN", "creators_name").strip()
        or "creators_name",
        expert_quote_column=os.environ.get("HUBSTUDIO_MYSQL_EXPERT_QUOTE_COLUMN", "expert_quote").strip()
        or "expert_quote",
        join_wa_column=os.environ.get("HUBSTUDIO_MYSQL_JOIN_WA_COLUMN", "join_WA").strip() or "join_WA",
        cooperation_intention_column=os.environ.get(
            "HUBSTUDIO_MYSQL_COOPERATION_INTENTION_COLUMN", "cooperation_intention"
        ).strip()
        or "cooperation_intention",
        negotiation_rounds_column=os.environ.get(
            "HUBSTUDIO_MYSQL_NEGOTIATION_ROUNDS_COLUMN", "negotiation_rounds"
        ).strip()
        or "negotiation_rounds",
        ai_reply_log_database=os.environ.get(
            "HUBSTUDIO_MYSQL_AI_REPLY_LOG_DATABASE", "controlpastmessagesdata"
        ).strip()
        or "controlpastmessagesdata",
        ai_reply_log_table=os.environ.get(
            "HUBSTUDIO_MYSQL_AI_REPLY_LOG_TABLE", "auto_reply_plugin_ai_reply_info"
        ).strip()
        or "auto_reply_plugin_ai_reply_info",
        connect_timeout=timeout,
    )


def apply_mysql_settings_from_yaml(data: Mapping[str, Any] | None) -> None:
    if not data:
        return
    mysql = data.get("mysql")
    if not isinstance(mysql, dict):
        return
    mapping = {
        "HUBSTUDIO_MYSQL_ENABLED": mysql.get("enabled"),
        "HUBSTUDIO_MYSQL_HOST": mysql.get("host"),
        "HUBSTUDIO_MYSQL_PORT": mysql.get("port"),
        "HUBSTUDIO_MYSQL_USER": mysql.get("user"),
        "HUBSTUDIO_MYSQL_PASSWORD": mysql.get("password"),
        "HUBSTUDIO_MYSQL_DATABASE": mysql.get("database"),
        "HUBSTUDIO_MYSQL_CREATOR_TABLE": mysql.get("creator_table"),
        "HUBSTUDIO_MYSQL_CREATOR_ID_COLUMN": mysql.get("creator_id_column"),
        "HUBSTUDIO_MYSQL_EXPERT_QUOTE_COLUMN": mysql.get("expert_quote_column"),
        "HUBSTUDIO_MYSQL_JOIN_WA_COLUMN": mysql.get("join_wa_column"),
        "HUBSTUDIO_MYSQL_COOPERATION_INTENTION_COLUMN": mysql.get("cooperation_intention_column"),
        "HUBSTUDIO_MYSQL_NEGOTIATION_ROUNDS_COLUMN": mysql.get("negotiation_rounds_column"),
        "HUBSTUDIO_MYSQL_AI_REPLY_LOG_DATABASE": mysql.get("ai_reply_log_database"),
        "HUBSTUDIO_MYSQL_AI_REPLY_LOG_TABLE": mysql.get("ai_reply_log_table"),
        "HUBSTUDIO_MYSQL_CONNECT_TIMEOUT": mysql.get("connect_timeout"),
    }
    for env_name, raw in mapping.items():
        if raw is None:
            continue
        if isinstance(raw, bool):
            os.environ[env_name] = "true" if raw else "false"
        elif str(raw).strip():
            os.environ[env_name] = str(raw).strip()
