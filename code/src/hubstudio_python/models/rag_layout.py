"""
``rag_data/`` 顶层布局：知识库（kb）与在线回复（reply）分目录；schema 为二者共用。
"""

from __future__ import annotations

from pathlib import Path

from hubstudio_python.config import _project_root


def _repo_root(base: Path | None = None) -> Path:
    return base if base is not None else _project_root()


def rag_root(*, base: Path | None = None) -> Path:
    return _repo_root(base) / "rag_data"


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
    """权威 supplement manifest：``authoritative_sources.yaml``。"""
    return kb_root(base=base) / "authoritative_sources.yaml"


def reply_root(*, base: Path | None = None) -> Path:
    return rag_root(base=base) / "reply"


def reply_examples_dir(*, base: Path | None = None) -> Path:
    return reply_root(base=base) / "examples"


def reply_rules_dir(*, base: Path | None = None) -> Path:
    return reply_root(base=base) / "rules"
