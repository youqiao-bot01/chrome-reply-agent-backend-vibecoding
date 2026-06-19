"""Embedding 结果 JSON 的读写（与 ``*.chunks.json`` 配套）。"""

from __future__ import annotations

import json
from pathlib import Path


def write_embeddings_json(path: Path, rows: list[dict[str, object]]) -> None:
    """写入 ``[{ "id": ..., "embedding": [...] }, ...]`` 结构。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_embeddings_json(path: Path) -> list[dict[str, object]]:
    """读取 embeddings 文件；不存在则返回空列表。"""
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))
