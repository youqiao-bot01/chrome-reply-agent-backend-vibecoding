"""JSON 落盘：切片列表与 embedding 行列表。"""

from __future__ import annotations

import json
from pathlib import Path

from hubstudio_python.models import KnowledgeChunk


def write_chunks_json(path: Path, chunks: list[KnowledgeChunk]) -> None:
    """将 ``KnowledgeChunk`` 序列化为 UTF-8 JSON（缩进 2，便于人工 diff）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [chunk.to_dict() for chunk in chunks]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_chunk_records_json(path: Path, records: list[dict]) -> None:
    """写入已展平的 chunks 记录（Playbook 结构化等同 Excel 同构 dict 列表）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_chunks_preview_md(path: Path, chunks: list[KnowledgeChunk]) -> None:
    """写出切块可读中间稿（Markdown），便于人工核对 HTML/Word 切分结果。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [f"# Chunks preview ({len(chunks)})\n\n"]
    for chunk in chunks:
        data = chunk.to_dict()
        cid = data.get("id", "")
        title = data.get("title", "")
        intent = data.get("intent_category", "")
        source = data.get("source_file", "")
        content = str(data.get("content", "") or "").strip()
        lines.append(f"## [{cid}] {title}\n\n")
        lines.append(f"- **intent**: {intent}\n")
        lines.append(f"- **source**: {source}\n\n")
        lines.append(content)
        lines.append("\n\n---\n\n")
    path.write_text("".join(lines), encoding="utf-8")
