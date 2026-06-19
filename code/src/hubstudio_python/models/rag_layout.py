"""
路径布局（均在 ``code/assets/`` 下）：

- ``rag_data/kb/`` — 知识库构建：input、output、增量清单
- ``schema/``、``prompt/``、``kb/`` — 回复运行期持续读取
"""

from __future__ import annotations

from pathlib import Path

from hubstudio_python.config import _project_root


def _repo_root(base: Path | None = None) -> Path:
    return base if base is not None else _project_root()


def code_root(*, base: Path | None = None) -> Path:
    return _repo_root(base) / "code"


def assets_root(*, base: Path | None = None) -> Path:
    return code_root(base=base) / "assets"


def rag_root(*, base: Path | None = None) -> Path:
    return assets_root(base=base) / "rag_data"


def kb_root(*, base: Path | None = None) -> Path:
    return rag_root(base=base) / "kb"


def kb_input_dir(*, base: Path | None = None) -> Path:
    return kb_root(base=base) / "input"


def kb_doc_dir(*, base: Path | None = None) -> Path:
    """已废弃：请用 ``kb_input_dir``。"""
    return kb_input_dir(base=base)


def kb_output_dir(*, base: Path | None = None) -> Path:
    return kb_root(base=base) / "output"


def kb_incremental_file(*, base: Path | None = None) -> Path:
    return kb_root(base=base) / "incremental_update.yaml"


def kb_authoritative_sources_file(*, base: Path | None = None) -> Path:
    return kb_root(base=base) / "authoritative_sources.yaml"


def runtime_kb_dir(*, base: Path | None = None) -> Path:
    """回复运行期：``gen.chunks.json``、``shop_tier_intent_hierarchy.json``。"""
    return assets_root(base=base) / "kb"


def prompt_sections_path(*, base: Path | None = None) -> Path:
    return assets_root(base=base) / "prompt" / "ai_prompt_sections.txt"
