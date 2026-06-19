"""回复流水线分步追踪：打印每步分析了什么。"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any


def stderr_trace_enabled(payload: dict[str, Any] | None = None) -> bool:
    """控制台 Step 1–9 是否打印（``reply-serve`` 默认开，``--no-trace`` / 环境变量可关）。"""
    if payload is not None and payload.get("_no_stderr_trace"):
        return False
    raw = os.environ.get("HUBSTUDIO_REPLY_TRACE", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def trace_enabled(payload: dict[str, Any] | None = None) -> bool:
    """是否在 JSON 响应中附带完整 ``steps``（``?trace=1`` 或 payload ``trace: true``）。"""
    if payload is not None and payload.get("trace"):
        return True
    return False


def _emit_stderr(text: str) -> None:
    try:
        print(text, file=sys.stderr, flush=True)
    except (UnicodeEncodeError, OSError):
        enc = getattr(sys.stderr, "encoding", None) or "utf-8"
        sys.stderr.buffer.write(text.encode(enc, errors="replace") + b"\n")
        sys.stderr.flush()


def log_reply_summary(
    *,
    shop: str,
    latest_creator_message: str,
    intent_category: str,
    intent_confidence: str,
    intent_matched_by: str,
    matched_chunks: int,
    reply: str,
    error: str | None = None,
    silent: bool = False,
) -> None:
    """每次回复结束后打印一行摘要（不受 ``trace`` 开关影响）。"""
    latest = (latest_creator_message or "").strip()
    preview = latest[:60] + ("…" if len(latest) > 60 else "")
    parts = [
        f"[reply] shop={shop or '?'}",
        f"latest={preview!r}" if preview else "latest=(empty)",
        f"intent={intent_category or 'GEN'}({intent_confidence or '?'}/{intent_matched_by or '?'})",
        f"matched={matched_chunks}",
        f"reply_len={len(reply or '')}",
    ]
    if error:
        parts.append(f"reason={error}")
    elif silent and not (reply or "").strip():
        parts.append("reason=silent_empty")
    if matched_chunks > 0 and not (reply or "").strip():
        parts.append("hint=strict_rule_filter_or_no_script")
    _emit_stderr(" ".join(parts))


@dataclass
class ReplyStep:
    step: int
    name: str
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "name": self.name,
            "summary": self.summary,
            "detail": self.detail,
        }


class ReplyTracer:
    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.steps: list[ReplyStep] = []
        self._counter = 0

    def record(self, name: str, summary: str, **detail: Any) -> None:
        self._counter += 1
        step = ReplyStep(
            step=self._counter,
            name=name,
            summary=summary,
            detail={k: v for k, v in detail.items() if v is not None},
        )
        self.steps.append(step)
        if self.enabled:
            self._print(step)

    def _print(self, step: ReplyStep) -> None:
        lines = [f"\n[reply] Step {step.step}: {step.name}", f"  → {step.summary}"]
        for key, val in step.detail.items():
            if val == "" or val == []:
                continue
            if isinstance(val, (dict, list)):
                blob = json.dumps(val, ensure_ascii=False, indent=2)
                indented = "\n".join(f"    {line}" for line in blob.splitlines())
                lines.append(f"  {key}:\n{indented}")
            else:
                lines.append(f"  {key}: {val}")
        text = "\n".join(lines)
        _emit_stderr(text)

    def to_dict_list(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.steps]
