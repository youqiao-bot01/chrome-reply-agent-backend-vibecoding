"""
``manifest.json``：记录每个源 docx 对应的输出 chunks 文件、切片数量与更新时间。

键为 ``source_file``（文件名），值为 ``ManifestEntry.to_dict()``。
"""

from __future__ import annotations

import json
from pathlib import Path

from hubstudio_python.models import ManifestEntry


def load_manifest(path: Path) -> dict[str, dict[str, object]]:
    """读取 manifest；文件不存在时返回空字典。"""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def upsert_manifest_entry(path: Path, entry: ManifestEntry) -> None:
    """按 ``entry.source_file`` 合并写入一条记录（整文件重写）。"""
    manifest = load_manifest(path)
    manifest[entry.source_file] = entry.to_dict()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def remove_manifest_entries(path: Path, source_files: list[str]) -> int:
    """从 manifest 移除若干 ``source_file`` 条目；返回实际删除条数。"""
    if not source_files:
        return 0
    manifest = load_manifest(path)
    removed = 0
    for name in source_files:
        if name in manifest:
            del manifest[name]
            removed += 1
    if removed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return removed
