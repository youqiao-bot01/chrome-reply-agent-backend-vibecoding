"""``rag_data/kb/input/gen.txt`` → 跨店 GEN 策略切片（非店铺话术，供 AI 全局理解）。"""

from __future__ import annotations

import re
from typing import Any

from hubstudio_python.models import SourceDocument

_ITEM_RE = re.compile(r"^\s*(\d+)\.\s*(.*)$", re.MULTILINE)


def _split_numbered_items(text: str) -> list[tuple[str, str]]:
    """按 ``1. …`` ``2. …`` 分段；同号续行合并进上一段。"""
    body = (text or "").strip()
    if not body:
        return []
    matches = list(_ITEM_RE.finditer(body))
    if not matches:
        return [("1", body)]
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        num = m.group(1).strip()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        block = body[start:end].strip()
        # 去掉行首 ``N. ``，保留正文
        lines = block.splitlines()
        if lines:
            lines[0] = re.sub(r"^\s*\d+\.\s*", "", lines[0]).strip()
        content = "\n".join(ln.rstrip() for ln in lines if ln.strip()).strip()
        if content:
            out.append((num, content))
    return out


def _title_from_body(body: str, *, max_len: int = 72) -> str:
    first = body.split("\n", 1)[0].strip()
    if len(first) <= max_len:
        return first
    return first[: max_len - 1].rstrip() + "…"


def split_gen_policy_to_records(document: SourceDocument) -> list[dict[str, Any]]:
    """将 ``gen.txt`` 切成 ``applicable_shops=GEN`` 的策略说明 records。"""
    text = document.path.read_text(encoding="utf-8")
    items = _split_numbered_items(text)
    records: list[dict[str, Any]] = []
    for seq, (num, body) in enumerate(items, start=1):
        title = _title_from_body(body)
        chunk_id = f"GEN-{seq}"
        records.append(
            {
                "id": chunk_id,
                "source_chunk_id": chunk_id,
                "source_file": document.source_file,
                "applicable_shops": "GEN",
                "creator_type": "GEN",
                "creator_progress": "GEN",
                "intent_category": "GEN",
                "creator_reply_frequency": "GEN",
                "creator_emotion": "GEN",
                "rule_type": "策略说明",
                "answer_purpose": f"Global seller policy #{num} (all shops).",
                "title": title,
                "content": body,
                "entitle": title,
                "encontent": body,
                "language": "zh",
            }
        )
    return records
