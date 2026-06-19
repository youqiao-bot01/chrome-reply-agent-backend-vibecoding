"""
A 层 CPM / 一口价话术：由 **平均播放量 avgVV**（非 GMV）计算 restore 占位符。

Playbook 公式（toolant A2 $2 CPM）：
- 单条预估 = avgVV × $0.002
- 单条封顶 = avgVV × $0.004
- 月度底金预估 = videoCount × 单条预估

A3 一口价：avgVV / 1000 × $3，半款 = × $0.0015
"""

from __future__ import annotations

import re
from typing import Any

_A2_CPM_USD_PER_VIEW = 0.002
_A2_CAP_MULTIPLIER = 2.0
_A3_CPM_USD_PER_VIEW = 0.003
_DEFAULT_VIDEO_COUNT = 4

_AVG_VV_PAYLOAD_KEYS = (
    "avgVideoViews",
    "avg_video_views",
    "avgVv",
    "avg_vv",
    "avg",
)


def _int_or_none(raw: object) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 0 else None
    if isinstance(raw, float):
        return int(raw) if raw >= 0 else None
    s = str(raw).strip().replace(",", "")
    if not s:
        return None
    try:
        val = float(s)
    except ValueError:
        return None
    return int(val) if val >= 0 else None


def parse_avg_video_views(data: dict[str, Any]) -> int | None:
    """从 API payload 解析平均播放量（avgVV / avgVideoViews / avg）。"""
    for key in _AVG_VV_PAYLOAD_KEYS:
        if key in data:
            parsed = _int_or_none(data.get(key))
            if parsed is not None:
                return parsed
    return None


def parse_video_count(data: dict[str, Any]) -> int:
    for key in ("videoCount", "video_count"):
        if key in data:
            parsed = _int_or_none(data.get(key))
            if parsed is not None and parsed > 0:
                return parsed
    return _DEFAULT_VIDEO_COUNT


def _fmt_usd(amount: float) -> str:
    rounded = round(amount, 2)
    if abs(rounded - round(rounded)) < 1e-9:
        return str(int(round(rounded)))
    text = f"{rounded:.2f}".rstrip("0").rstrip(".")
    return text


def _fmt_views(avg_vv: int) -> str:
    return f"{avg_vv:,}"


def build_avgvv_restore_extra(
    avg_video_views: int,
    *,
    video_count: int = _DEFAULT_VIDEO_COUNT,
    creator_name: str = "",
) -> dict[str, str]:
    """计算 playbook ``{avgVV}`` / ``{avgVV*0.002}`` 等占位符替换值。"""
    vv = max(0, int(avg_video_views))
    n = max(1, int(video_count))
    per_a2 = vv * _A2_CPM_USD_PER_VIEW
    cap_a2 = vv * _A2_CPM_USD_PER_VIEW * _A2_CAP_MULTIPLIER
    per_a3 = vv * _A3_CPM_USD_PER_VIEW
    half_a3 = per_a3 / 2.0
    total = n * per_a2

    extra: dict[str, str] = {
        "avgVV": _fmt_views(vv),
        "videoCount": str(n),
        "total": _fmt_usd(total),
        "avgVV*0.002": _fmt_usd(per_a2),
        "avgVV*0.004": _fmt_usd(cap_a2),
        "avgVV*0.003": _fmt_usd(per_a3),
        "avgVV*0.0015": _fmt_usd(half_a3),
    }
    if creator_name.strip():
        extra["creator"] = creator_name.strip()
    return extra


def rule_needs_avgvv(rule: dict[str, Any]) -> bool:
    raw = rule.get("restore_slots") or []
    if isinstance(raw, str):
        raw = [raw]
    return "avgVV" in {str(x).strip() for x in raw if str(x).strip()}


def earnings_restore_extra_for_request(
    req: Any,
    *,
    rule: dict[str, Any] | None = None,
) -> dict[str, str]:
    """按请求中的 avgVV 生成 ``apply_restore_slots(..., extra=...)`` 字典。"""
    avg = getattr(req, "avg_video_views", None)
    if avg is None:
        return {}
    if rule is not None and not rule_needs_avgvv(rule):
        return {}
    return build_avgvv_restore_extra(
        int(avg),
        video_count=getattr(req, "video_count", None) or _DEFAULT_VIDEO_COUNT,
        creator_name=str(getattr(req, "creator_name", "") or ""),
    )


def cpm_summary_for_prompt(avg_video_views: int, *, video_count: int = _DEFAULT_VIDEO_COUNT) -> str:
    extra = build_avgvv_restore_extra(avg_video_views, video_count=video_count)
    return (
        f"Creator avg video views (avgVV, NOT monthly GMV): {extra['avgVV']}\n"
        f"Estimated per-video base @ $2 CPM: ${extra['avgVV*0.002']}\n"
        f"Per-video cap @ 2× avg: ${extra['avgVV*0.004']}\n"
        f"Estimated monthly base ({video_count} videos): ${extra['total']}"
    )


def reply_lacks_cpm_numbers(text: str, *, extra: dict[str, str]) -> bool:
    """LLM 回复是否未包含已算好的 CPM 金额（用于回退参考脚本）。"""
    body = (text or "").strip()
    if not body or not extra:
        return False
    per = extra.get("avgVV*0.002")
    if not per:
        return False
    if re.search(rf"\$\s*{re.escape(per)}\b", body):
        return False
    if re.search(r"\$\s*\d", body):
        return False
    return True
