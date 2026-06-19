from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests

logger = logging.getLogger(__name__)

# 只维护“最新一周”多维表格分表列表（运行后会自动回写本文件更新该常量）。
# - base_table_id: 本周主表 table_id）每周一更新
# - table_ids: 本周数据总量>2000时创建的分表 每次更新
BITABLE_WEEK_TABLES: dict[str, Any] = {
    "base_table_id": 'tblSm2ae64Ncgs2M',
    "table_ids": [
        'tblSm2ae64Ncgs2M',
    ],
}







def _persist_bitable_week_tables_to_source(*, base_table_id: str, table_ids: list[str]) -> None:
    """把 BITABLE_WEEK_TABLES 回写到当前 .py 文件，确保只维护最新一周。"""
    try:
        base_table_id = str(base_table_id).strip()
        uniq: list[str] = []
        seen: set[str] = set()
        for x in table_ids:
            s = str(x).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            uniq.append(s)
        if base_table_id and base_table_id not in seen:
            uniq.insert(0, base_table_id)
        elif uniq:
            base_table_id = uniq[0]

        src_path = Path(__file__).resolve()
        text = src_path.read_text(encoding="utf-8")
        new_block_lines = [
            "BITABLE_WEEK_TABLES: dict[str, Any] = {",
            f'    "base_table_id": {base_table_id!r},',
            '    "table_ids": [',
        ]
        for tid in uniq:
            new_block_lines.append(f"        {tid!r},")
        new_block_lines += ["    ],", "}", ""]
        new_block = "\n".join(new_block_lines)

        pat = re.compile(
            r"BITABLE_WEEK_TABLES:\s*dict\[str,\s*Any\]\s*=\s*\{[\s\S]*?\}\n",
            re.MULTILINE,
        )
        if not pat.search(text):
            logger.warning("未能在源码中定位 BITABLE_WEEK_TABLES 块，跳过自动回写")
            return
        updated = pat.sub(new_block + "\n", text, count=1)
        if updated != text:
            src_path.write_text(updated, encoding="utf-8")
            BITABLE_WEEK_TABLES["base_table_id"] = base_table_id
            BITABLE_WEEK_TABLES["table_ids"] = uniq
    except Exception as exc:
        logger.warning("自动回写 BITABLE_WEEK_TABLES 失败（已跳过）: %s", exc)


# 飞书开放平台：用 app_id/app_secret 交换 tenant_access_token 的固定接口地址（一般无需配置化）。
FEISHU_TENANT_TOKEN_URL = (
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
)

# 多维表格“列出记录”接口单页上限（用于清空表前分页拉取 record_id 列表）。
BITABLE_LIST_PAGE_SIZE = 500
# 多维表格“批量删除记录”接口单次上限（用于清空表时分批删除 record_id）。
BITABLE_DELETE_CHUNK = 500
# 写入多维表格时单次最多拉取条数（按 create_time DESC，即最新若干条）
BITABLE_WRITE_MAX_ROWS = 2000
# 多维表格 batch_create 单次最多允许的 records 数（超了会报 1254104 RecordAddOnceExceedLimit）
BITABLE_BATCH_CREATE_MAX = 500

# 飞书多维表格 API 用的 Base token / 数据表 id（URL …/base/{app_token}?table={table_id}）；
# 与开放平台自建应用的 App ID（cli_xxx）不是同一个字段。
DEFAULT_BITABLE_APP_TOKEN = "Soy8bTYIlaEzFQsn4OIcT9VzngS"


@dataclass(frozen=True)
class FeishuBitableWriteConfig:
    app_id: str
    app_secret: str
    app_token: str
    table_id: str
    clear_before_write: bool
    datetime_as_text: bool
    max_rows: int | None


def _feishu_bitable_app_secret(bi: Mapping[str, Any], config_path: Path) -> str:
    env_key = bi.get("app_secret_env")
    if isinstance(env_key, str) and env_key.strip():
        name = env_key.strip()
        raw = os.environ.get(name)
        if raw is None:
            hint = ""
            if len(name) > 16 and "_" not in name and name.isalnum():
                hint = (
                    "（提示：你填的很像 App Secret 本体；app_secret_env 应填「变量名」如 "
                    "FEISHU_APP_SECRET，密钥写在 app_secret 或该环境变量里。）"
                )
            raise RuntimeError(
                f"{config_path}: feishu.bitable 已配置 app_secret_env: {name!r}，"
                f"但当前进程未设置该环境变量。{hint}"
            )
        s = str(raw).strip()
        if not s:
            raise RuntimeError(
                f"{config_path}: 环境变量 {name!r} 为空（feishu.bitable.app_secret_env）。"
            )
        return s
    sec = bi.get("app_secret")
    if sec is None:
        raise RuntimeError(f"{config_path}: feishu.bitable 请设置 app_secret 或 app_secret_env。")
    if not isinstance(sec, (str, int)):
        raise RuntimeError(f"{config_path}: feishu.bitable.app_secret 必须是字符串或数字。")
    s = str(sec).strip()
    if not s:
        raise RuntimeError(f"{config_path}: feishu.bitable.app_secret 为空。")
    return s


def parse_bitable_write_config(
    doc: Mapping[str, Any], config_path: Path
) -> FeishuBitableWriteConfig | None:
    fei = doc.get("feishu")
    if not isinstance(fei, dict):
        return None
    bi = fei.get("bitable")
    if not isinstance(bi, dict):
        return None
    if bi.get("enabled") is False:
        return None

    at_raw = bi.get("app_token")
    tid_raw = bi.get("table_id")
    app_token = (
        at_raw.strip()
        if isinstance(at_raw, str) and at_raw.strip()
        else DEFAULT_BITABLE_APP_TOKEN
    )

    week_base_table_id = str(BITABLE_WEEK_TABLES.get("base_table_id") or "").strip()
    yaml_table_id = tid_raw.strip() if isinstance(tid_raw, str) and tid_raw.strip() else ""
    table_id = week_base_table_id or yaml_table_id
    if not table_id:
        logger.warning(
            "%s: feishu.bitable 未配置 table_id 且 BITABLE_WEEK_TABLES.base_table_id 为空，跳过多维表格写入",
            config_path,
        )
        return None

    app_id = (os.environ.get("FEISHU_APP_ID") or "").strip()
    if not app_id and isinstance(bi.get("app_id"), str):
        app_id = bi["app_id"].strip()
    if not app_id:
        logger.info("未配置 feishu.bitable.app_id（或 FEISHU_APP_ID），跳过多维表格写入")
        return None

    try:
        secret = _feishu_bitable_app_secret(bi, config_path)
    except RuntimeError as e:
        logger.warning("%s，跳过多维表格写入", e)
        return None

    clear_before = bi.get("clear_before_write")
    do_clear = False if clear_before is False else True

    dt_text = bi.get("datetime_as_text", True)
    if dt_text not in (True, False):
        dt_text = True

    mr = bi.get("max_rows")
    max_rows: int | None
    if mr is None:
        max_rows = None
    elif isinstance(mr, int):
        max_rows = mr if mr > 0 else None
    elif isinstance(mr, str) and mr.strip().isdigit():
        max_rows = int(mr.strip())
        if max_rows <= 0:
            max_rows = None
    else:
        max_rows = None

    return FeishuBitableWriteConfig(
        app_id=app_id,
        app_secret=secret,
        app_token=app_token.strip(),
        table_id=table_id.strip(),
        clear_before_write=do_clear,
        datetime_as_text=dt_text,
        max_rows=max_rows,
    )


def feishu_tenant_access_token(*, app_id: str, app_secret: str) -> str:
    r = requests.post(
        FEISHU_TENANT_TOKEN_URL,
        headers={"Content-Type": "application/json; charset=utf-8"},
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(
            f"获取 tenant_access_token 失败: code={data.get('code')} msg={data.get('msg')}"
        )
    token = data.get("tenant_access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("获取 tenant_access_token 失败: 响应缺少 tenant_access_token")
    return token


def _bitable_api_json(
    *,
    method: str,
    url: str,
    tenant_token: str,
    params: Mapping[str, Any] | None = None,
    json_body: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {tenant_token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    r = requests.request(
        method,
        url,
        headers=headers,
        params=dict(params) if params else None,
        json=json_body,
        timeout=90,
    )
    r.raise_for_status()
    out = r.json()
    if out.get("code") != 0:
        raise RuntimeError(f"多维表格 API 失败: code={out.get('code')} msg={out.get('msg')}")
    data = out.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("多维表格 API 异常: data 非对象")
    return data


def bitable_list_all_record_ids(*, app_token: str, table_id: str, tenant_token: str) -> list[str]:
    base = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}"
        f"/tables/{table_id}/records"
    )
    ids: list[str] = []
    page_token: str | None = None
    while True:
        params: dict[str, Any] = {"page_size": BITABLE_LIST_PAGE_SIZE}
        if page_token:
            params["page_token"] = page_token
        data = _bitable_api_json(method="GET", url=base, tenant_token=tenant_token, params=params)
        items = data.get("items")
        if not isinstance(items, list):
            break
        for it in items:
            if isinstance(it, dict):
                rid = it.get("record_id")
                if isinstance(rid, str) and rid:
                    ids.append(rid)
        if not data.get("has_more"):
            break
        pt = data.get("page_token")
        page_token = pt if isinstance(pt, str) and pt else None
        if not page_token:
            break
    return ids


def bitable_batch_delete_records(
    *, app_token: str, table_id: str, tenant_token: str, record_ids: list[str]
) -> None:
    if not record_ids:
        return
    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}"
        f"/tables/{table_id}/records/batch_delete"
    )
    for i in range(0, len(record_ids), BITABLE_DELETE_CHUNK):
        chunk = record_ids[i : i + BITABLE_DELETE_CHUNK]
        _bitable_api_json(
            method="POST",
            url=url,
            tenant_token=tenant_token,
            json_body={"records": chunk},
        )


def bitable_batch_create_records(
    *, app_token: str, table_id: str, tenant_token: str, records: list[dict[str, Any]]
) -> None:
    if not records:
        return
    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}"
        f"/tables/{table_id}/records/batch_create"
    )
    _bitable_api_json(method="POST", url=url, tenant_token=tenant_token, json_body={"records": records})


def bitable_rename_table(*, app_token: str, table_id: str, tenant_token: str, name: str) -> None:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}"
    _bitable_api_json(method="PATCH", url=url, tenant_token=tenant_token, json_body={"name": name})


def bitable_list_fields(*, app_token: str, table_id: str, tenant_token: str) -> list[dict[str, Any]]:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
    data = _bitable_api_json(method="GET", url=url, tenant_token=tenant_token)
    items = data.get("items")
    if not isinstance(items, list):
        return []
    return [it for it in items if isinstance(it, dict)]


def bitable_create_table(
    *, app_token: str, tenant_token: str, name: str, fields: list[dict[str, Any]] | None = None
) -> str:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables"
    table: dict[str, Any] = {"name": name}
    if fields:
        table["fields"] = fields
    data = _bitable_api_json(method="POST", url=url, tenant_token=tenant_token, json_body={"table": table})
    table_id = data.get("table_id")
    if not isinstance(table_id, str) or not table_id.strip():
        raise RuntimeError("新增数据表失败：响应缺少 table_id")
    return table_id.strip()


def bitable_delete_table(*, app_token: str, table_id: str, tenant_token: str) -> None:
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}"
    _bitable_api_json(method="DELETE", url=url, tenant_token=tenant_token)


def _strip_option_ids(prop: dict[str, Any]) -> dict[str, Any]:
    """
    复制字段 property 时，去掉单选/多选选项里的 id 字段。
    飞书不允许在创建新表时指定旧表的选项 id，否则报 800074082。
    """
    options = prop.get("options")
    if not isinstance(options, list):
        return prop
    cleaned_options = [
        {k: v for k, v in opt.items() if k != "id"}
        for opt in options
        if isinstance(opt, dict)
    ]
    return {**prop, "options": cleaned_options}


def _bitable_fields_for_create_from_existing(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    primary = None
    others: list[dict[str, Any]] = []
    for it in items:
        field_name = it.get("field_name")
        field_type = it.get("type")
        if not isinstance(field_name, str) or not field_name.strip():
            continue
        if not isinstance(field_type, int):
            continue
        if field_type >= 1000:
            continue
        row: dict[str, Any] = {"field_name": field_name.strip(), "type": field_type}
        prop = it.get("property")
        if isinstance(prop, dict) and prop:
            row["property"] = _strip_option_ids(prop)
        if it.get("is_primary") is True and primary is None:
            primary = row
        else:
            others.append(row)
    if primary is None:
        primary = {"field_name": "索引", "type": 1}
    return [primary] + others


def _cell_str(x: Any) -> str:
    if x is None:
        return ""
    s = str(x)
    return s.strip()


def _to_bitable_datetime_value(dt: Any, *, datetime_as_text: bool) -> Any:
    if dt is None:
        return "" if datetime_as_text else 0
    if isinstance(dt, datetime):
        if datetime_as_text:
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        return int(dt.timestamp() * 1000)
    s = str(dt).strip()
    return s if datetime_as_text else 0


def _normalize_history_message_text(message_info: Any) -> str:
    """
    兼容 message_info 可能是 dict / JSON 字符串 / 普通字符串。
    dict/JSON 时按 key 排序拼接 values；否则原样返回。
    """
    if message_info is None:
        return ""
    if isinstance(message_info, dict):
        d = message_info
    else:
        s = str(message_info).strip()
        if not s:
            return ""
        if s.startswith("{") and s.endswith("}"):
            try:
                parsed = json.loads(s)
                d = parsed if isinstance(parsed, dict) else None
            except Exception:
                d = None
        else:
            d = None
        if d is None:
            return s

    def _k_sort(k: Any) -> tuple[int, str]:
        try:
            return (0, f"{int(k):012d}")
        except Exception:
            return (1, str(k))

    parts: list[str] = []
    for k in sorted(d.keys(), key=_k_sort):
        v = d.get(k)
        if v is None:
            continue
        t = str(v).strip()
        if t:
            parts.append(t)
    return "\n".join(parts).strip()


_CREATOR_TAG_RE = re.compile(r"\[creator\s*\]", flags=re.IGNORECASE)


def extract_daren_utterance_from_history_summary(message_info: Any) -> str:
    """
    从「达人历史聊天记录汇总」里抽取达人侧发言：带 `[creator]` 角色标签的行/片段。

    典型一行格式：`[Feb 22 8:00 PM][creator] Interested`
    也兼容 JSON：`{"0":"...\\n[Yesterday 2:28 AM][creator] Thank you!!!\\n..."}`
    """
    raw = _normalize_history_message_text(message_info)
    if not raw:
        return ""
    text = raw.replace("\r\n", "\n")

    # 1) 按行：整行里出现 `[creator]`（允许 `creator` 与 `]` 之间有空格）
    out: list[str] = []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        if _CREATOR_TAG_RE.search(s):
            out.append(s)

    if out:
        return "\n".join(out)

    # 2) 单行大段里没有换行分隔时，用正则扫出 `[时间戳][creator] 正文` 片段
    seg_re = re.compile(r"\[[^\]\n]+\]\s*\[creator\s*\]\s*[^\n]+", flags=re.IGNORECASE)
    segs = [m.group(0).strip() for m in seg_re.finditer(text)]
    return "\n".join(segs).strip()


def extract_last_bracket_before_creator(daren_utterance: Any) -> str:
    s = _cell_str(daren_utterance)
    if not s:
        return ""
    last = ""
    for m in re.finditer(r"\[([^\[\]]+)\]\s*\[creator\s*\]", s, flags=re.IGNORECASE):
        v = (m.group(1) or "").strip()
        if v:
            last = v
    return last


def _feishu_text_segment_cell(text: str) -> list[dict[str, str]]:
    return [{"type": "text", "text": text}]


def _count_tag_occurrences_in_history_summary(message_info: Any) -> tuple[int, int]:
    """
    统计历史汇总里标签出现次数（大小写不敏感）。

    - creator_count: "[creator]" 出现次数
    - seller_count:  "[seller]" 出现次数
    """
    raw = _normalize_history_message_text(message_info)
    if not raw:
        return 0, 0
    # - "[creator" / "[seller"
    t = raw.lower()
    # 与正文标签一致：creator 用 `[creator` 前缀计数（覆盖 `[creator]`、`[creator ]` 等）
    creator_cnt = t.count("[creator")
    seller_cnt = t.count("[seller")
    return creator_cnt, seller_cnt


def bitable_fields_from_ai_reply_row(
    *,
    creator_name: Any,
    source: Any,
    create_time: Any,
    message_info: Any,
    reply_result: Any,
    full_history: Any = None,
    datetime_as_text: bool,
    long_text_as_segments: bool,
) -> dict[str, Any]:
    msg_text = _normalize_history_message_text(message_info)
    daren_utterance = extract_daren_utterance_from_history_summary(message_info)
    creator_cnt, seller_cnt = _count_tag_occurrences_in_history_summary(message_info)
    reply_freq = (creator_cnt / seller_cnt) if seller_cnt > 0 else 0.0

    msg_val: Any = _feishu_text_segment_cell(msg_text) if (long_text_as_segments and msg_text) else _cell_str(msg_text)
    rep_text = _cell_str(reply_result)
    rep_val: Any = _feishu_text_segment_cell(rep_text) if (long_text_as_segments and rep_text) else rep_text
    creator_cnt_s = _cell_str(creator_cnt)
    reply_freq_s = f"{reply_freq:.6f}"
    creator_cnt_val: Any = (
        _feishu_text_segment_cell(creator_cnt_s) if long_text_as_segments else creator_cnt_s
    )
    reply_freq_val: Any = (
        _feishu_text_segment_cell(reply_freq_s) if long_text_as_segments else reply_freq_s
    )

    # 完整历史记录
    history_text = _cell_str(full_history)
    history_val: Any = (
        _feishu_text_segment_cell(history_text) if (long_text_as_segments and history_text) else history_text
    )

    # 根据完整历史记录自动推断多选字段
    def _multiselect(options: list) -> list:
        return options if options else []

    cp_options: list = []
    signup_options: list = []
    if history_text:
        if "达人在CP搜索" in history_text:
            cp_options.append("搜索")
        if "达人在CP注册" in history_text:
            cp_options.append("注册")
        if "达人在TTO发布视频" in history_text or "达人在TAP发布视频" in history_text:
            cp_options.append("发视频")
        if "达人在TTO接受签约" in history_text:
            signup_options.append("TTO签约")
        if "达人在CAP接受签约" in history_text:
            signup_options.append("CAP签约")

    return {
        "达人ID": _cell_str(creator_name),
        "店铺": _cell_str(source),
        "最后达人问询时间": extract_last_bracket_before_creator(daren_utterance),
        "回复时间": _to_bitable_datetime_value(create_time, datetime_as_text=datetime_as_text),
        "达人历史聊天记录汇总": msg_val,
        "达人发言记录": _cell_str(daren_utterance),
        "AI回复": rep_val,
        "回复类型": "AI回复",
        # 为了“展示固定 6 位小数”，这里按文本写入（前提：飞书字段类型为文本）。
        "达人回复次数": creator_cnt_val,
        "达人回复频率": reply_freq_val,
        "完整历史记录": history_val,
        "Creator Portal": _multiselect(cp_options),
        "签约": _multiselect(signup_options),
    }


def feishu_bitable_report_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """多维表格写入时间窗（闭区间）。

    - 周一～周六：本周周日 16:30 ～ 现在
    - 周日：上周周日 16:30 ～ 现在（避免周日刚过 16:30 时区间过短导致清空却写不回）
    """
    end = now or datetime.now()
    d = end.date()
    # Python weekday: Monday=0 ... Sunday=6
    days_since_sun = (d.weekday() + 1) % 7
    sunday = d - timedelta(days=days_since_sun)
    start = datetime.combine(sunday, time(16, 30))
    if d.weekday() == 6:
        start = start - timedelta(days=7)
    return start, end


def sync_daily_report_to_bitable(
    cfg: FeishuBitableWriteConfig,
    tenant_token: str,
    *,
    conn: Any,
    start_inclusive: datetime,
    end_inclusive: datetime,
    fetch_rows_func: Any,
) -> None:
    """
    把 MySQL 区间内 AI 回复明细同步到飞书多维表格：
    - 可选清表
    - 超过上限自动拆分表
    - 维护 BITABLE_WEEK_TABLES 并回写到源码

    fetch_rows_func(conn, start, end) -> list[dict] 由日报模块提供（避免本模块依赖 pymysql）。
    """
    rows: list[dict[str, Any]] = fetch_rows_func(conn, start_inclusive, end_inclusive)
    if cfg.max_rows is not None and cfg.max_rows > 0:
        rows = rows[: cfg.max_rows]
    total = len(rows)

    table_name = f"{start_inclusive:%Y年%m月%d日}-{end_inclusive:%Y年%m月%d日}"
    logger.info("准备写入多维表格数据表: %s", table_name)
    logger.info(
        "多维表格写入总量=%s，分表数=%s（每表最多 %s）",
        total,
        (total + BITABLE_WRITE_MAX_ROWS - 1) // BITABLE_WRITE_MAX_ROWS if total else 0,
        BITABLE_WRITE_MAX_ROWS,
    )

    force_new_tables = end_inclusive.date().weekday() == 0
    week_base_table_id = str(BITABLE_WEEK_TABLES.get("base_table_id") or "").strip() or str(cfg.table_id).strip()

    # 周一时：若本周表已创建过（base_table_id 已不同于 config 里的旧表），直接复用，不重复创建
    already_created_this_week = (
        force_new_tables
        and week_base_table_id
        and week_base_table_id != str(cfg.table_id).strip()
    )

    if not force_new_tables or already_created_this_week:
        base_table_id = week_base_table_id
        raw_ids = BITABLE_WEEK_TABLES.get("table_ids")
        if isinstance(raw_ids, list):
            to_delete = []
            for tid in raw_ids:
                s = str(tid).strip()
                if not s:
                    continue
                if s == base_table_id or s == str(cfg.table_id).strip():
                    continue
                to_delete.append(s)
            if to_delete:
                for tid in reversed(to_delete):
                    try:
                        bitable_delete_table(app_token=cfg.app_token, table_id=tid, tenant_token=tenant_token)
                        logger.info("已删除历史分表: %s", tid)
                    except Exception as exc:
                        logger.warning("删除历史分表失败（已跳过）: %s %s", tid, exc)
        table_ids: list[str] = [base_table_id]
    else:
        try:
            field_items = bitable_list_fields(app_token=cfg.app_token, table_id=week_base_table_id, tenant_token=tenant_token)
            schema_fields = _bitable_fields_for_create_from_existing(field_items) if field_items else None
        except Exception as exc:
            logger.warning("读取多维表字段失败（将创建空表重试写入，可能因缺列失败）: %s", exc)
            schema_fields = None

        try:
            base_table_id = bitable_create_table(
                app_token=cfg.app_token,
                tenant_token=tenant_token,
                name=table_name,
                fields=schema_fields,
            )
        except RuntimeError as exc:
            # 同名表已存在（1254013 TableNameDuplicated）：查找已有表复用
            if "1254013" in str(exc):
                logger.info("同名表已存在，查找并复用: %s", table_name)
                url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{cfg.app_token}/tables"
                data = _bitable_api_json(method="GET", url=url, tenant_token=tenant_token)
                items = data.get("items") or []
                found = next(
                    (it["table_id"] for it in items if isinstance(it, dict) and it.get("name") == table_name),
                    None,
                )
                if found:
                    base_table_id = found
                    logger.info("复用已有表: table_id=%s", base_table_id)
                else:
                    raise
            else:
                raise
        # 新建表后再显式重命名一次（确保名称符合预期）
        bitable_rename_table(
            app_token=cfg.app_token,
            table_id=base_table_id,
            tenant_token=tenant_token,
            name=table_name,
        )
        table_ids = [base_table_id]

    # Step: 无论是否周一，都将“本次写入的主表”重命名为当前统计区间（避免表名停留在旧日期）。
    try:
        bitable_rename_table(
            app_token=cfg.app_token,
            table_id=base_table_id,
            tenant_token=tenant_token,
            name=table_name,
        )
        logger.info("已重命名多维表格数据表: table_id=%s name=%s", base_table_id, table_name)
    except Exception as exc:
        logger.warning(
            "多维表格重命名失败（已跳过，不影响写入）: table_id=%s name=%s err=%s",
            base_table_id,
            table_name,
            exc,
        )

    if cfg.clear_before_write:
        try:
            ids = bitable_list_all_record_ids(app_token=cfg.app_token, table_id=base_table_id, tenant_token=tenant_token)
            if ids:
                bitable_batch_delete_records(
                    app_token=cfg.app_token,
                    table_id=base_table_id,
                    tenant_token=tenant_token,
                    record_ids=ids,
                )
            logger.info("清空多维表格记录 %s 条", len(ids))
        except Exception as exc:
            logger.warning("清空多维表格失败（将继续写入）: %s", exc)

    # Step: 读取当前表字段名集合；写入时剔除不存在的字段，避免 FieldNameNotFound 整批失败。
    try:
        _items = bitable_list_fields(app_token=cfg.app_token, table_id=base_table_id, tenant_token=tenant_token)
        known_field_names = {str(it.get("field_name")).strip() for it in _items if isinstance(it, dict) and it.get("field_name")}
    except Exception as exc:
        known_field_names = set()
        logger.warning("读取多维表字段列表失败（将按原字段写入，可能失败）: %s", exc)

    def _chunked(seq: list[dict[str, Any]], size: int) -> Iterator[list[dict[str, Any]]]:
        for i in range(0, len(seq), size):
            yield seq[i : i + size]

    def _build_records(part_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        missing_seen: set[str] = set()
        for r in part_rows:
            fields = bitable_fields_from_ai_reply_row(
                creator_name=r.get("creator_name"),
                source=r.get("source"),
                create_time=r.get("create_time"),
                message_info=r.get("message_info"),
                reply_result=r.get("reply_result"),
                full_history=r.get("full_history"),
                datetime_as_text=cfg.datetime_as_text,
                long_text_as_segments=False,
            )
            if known_field_names:
                filtered: dict[str, Any] = {}
                for k, v in fields.items():
                    if k in known_field_names:
                        filtered[k] = v
                    else:
                        missing_seen.add(k)
                fields = filtered
            out.append({"fields": fields})
        if known_field_names and missing_seen:
            logger.warning(
                "多维表缺少以下字段（已自动跳过写入这些列）：%s",
                "，".join(sorted(missing_seen)),
            )
        return out

    parts = (total + BITABLE_WRITE_MAX_ROWS - 1) // BITABLE_WRITE_MAX_ROWS if total else 0
    for p in range(parts or 1):
        part_rows = rows[p * BITABLE_WRITE_MAX_ROWS : (p + 1) * BITABLE_WRITE_MAX_ROWS]
        table_id = base_table_id
        if p > 0:
            table_id = bitable_create_table(
                app_token=cfg.app_token,
                tenant_token=tenant_token,
                name=f"{table_name}-{p+1}",
                fields=None,
            )
            table_ids.append(table_id)
        records = _build_records(part_rows)
        for chunk in _chunked(records, BITABLE_BATCH_CREATE_MAX):
            bitable_batch_create_records(
                app_token=cfg.app_token,
                table_id=table_id,
                tenant_token=tenant_token,
                records=chunk,
            )

    _persist_bitable_week_tables_to_source(base_table_id=base_table_id, table_ids=table_ids)


def parse_reply_log_url_from_doc(doc: Mapping[str, Any]) -> str:
    fei = doc.get("feishu")
    if not isinstance(fei, dict):
        return ""
    for key in ("reply_log_url", "bitable_url", "reply_log_bitable_url"):
        u = fei.get(key)
        if isinstance(u, str) and u.strip():
            return u.strip()
    bi = fei.get("bitable")
    if isinstance(bi, dict):
        u = bi.get("url")
        if isinstance(u, str) and u.strip():
            return u.strip()
    return ""


def build_week_bitable_url_from_doc(doc: Mapping[str, Any]) -> str:
    base_table_id = str(BITABLE_WEEK_TABLES.get("base_table_id") or "").strip()
    if not base_table_id:
        return ""

    raw = parse_reply_log_url_from_doc(doc).strip()
    if raw:
        try:
            u = urlparse(raw)
            q = parse_qs(u.query, keep_blank_values=True)
            q["table"] = [base_table_id]
            new_q = urlencode(q, doseq=True)
            return urlunparse((u.scheme, u.netloc, u.path, u.params, new_q, u.fragment))
        except Exception:
            return raw

    fei = doc.get("feishu")
    app_token = DEFAULT_BITABLE_APP_TOKEN
    if isinstance(fei, dict):
        bi = fei.get("bitable")
        if isinstance(bi, dict) and isinstance(bi.get("app_token"), str) and bi["app_token"].strip():
            app_token = bi["app_token"].strip()
    if not app_token:
        return ""
    return f"https://linknlatch.feishu.cn/base/{app_token}?table={base_table_id}"

