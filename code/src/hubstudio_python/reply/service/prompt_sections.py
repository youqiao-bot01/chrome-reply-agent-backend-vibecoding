"""加载 ``rag_data/reply/ai_prompt_sections.txt`` 中的 RAG 提示词段落。"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from hubstudio_python.models.rag_layout import rag_root

_SECTION_RE = re.compile(r"^===== ([a-zA-Z0-9_.]+) =====\s*$", re.MULTILINE)


def _sections_path() -> Path:
    return rag_root() / "reply" / "ai_prompt_sections.txt"


@lru_cache(maxsize=1)
def load_prompt_sections() -> dict[str, str]:
    path = _sections_path()
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        m = _SECTION_RE.match(line.strip())
        if m:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = m.group(1)
            buf = []
            continue
        if current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def get_prompt_section(name: str, *, default: str = "") -> str:
    return load_prompt_sections().get(name, default)


def reload_prompt_sections() -> None:
    load_prompt_sections.cache_clear()
