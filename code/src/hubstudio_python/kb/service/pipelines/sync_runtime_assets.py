"""将 ``rag_data/kb/output/`` 中回复运行期需要的文件同步到 ``code/assets/kb/``。"""

from __future__ import annotations

import shutil
from pathlib import Path

from hubstudio_python.models.rag_layout import runtime_kb_dir

_RUNTIME_KB_FILES = ("gen.chunks.json", "shop_tier_intent_hierarchy.json")


def sync_runtime_kb_assets(kb_output_dir: Path) -> list[str]:
    dest = runtime_kb_dir()
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in _RUNTIME_KB_FILES:
        src = kb_output_dir / name
        if src.is_file():
            shutil.copy2(src, dest / name)
            copied.append(name)
    return copied
