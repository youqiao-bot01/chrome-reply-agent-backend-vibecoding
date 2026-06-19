"""
扫描 ``rag_data/kb/input``：列出 ``.docx`` 并构造 ``SourceDocument``（路径 + 文件字节校验和）。

正文解析与切块由 ``chunking.word_outline_chunker`` 使用 ``python-docx`` 按路径读取，本模块不再单独抽纯文本。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from hubstudio_python.models import SourceDocument


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def scan_spreadsheets(
    doc_dir: Path,
    *,
    only_names: list[str] | None = None,
) -> list[SourceDocument]:
    """
    递归扫描 ``.xlsx`` / ``.xlsm``（含子目录，如 ``kb/input/Linknlatch/*.xlsx``）。

    ``only_names`` 可写文件名或相对 ``doc_dir`` 的路径（如 ``Linknlatch/AI话术参考.xlsx``）。
    """
    allow = {n.strip().replace("\\", "/") for n in only_names} if only_names else None
    documents: list[SourceDocument] = []
    if not doc_dir.exists():
        return documents

    for path in sorted(doc_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith("~$"):
            continue
        suffix = path.suffix.lower()
        if suffix not in {".xlsx", ".xlsm"}:
            continue
        key = _source_file_key(path, doc_dir)
        if allow is not None and key not in allow and path.name not in allow:
            continue
        raw = path.read_bytes()
        documents.append(
            SourceDocument(
                path=path,
                source_file=key,
                checksum=_sha256_bytes(raw),
            )
        )
    return documents


def _source_file_key(path: Path, doc_dir: Path) -> str:
    """manifest / chunk 上的 ``source_file``：子目录内文件用相对路径 ``toolant/foo.html``。"""
    try:
        rel = path.relative_to(doc_dir)
        if rel.parent != Path("."):
            return rel.as_posix()
    except ValueError:
        pass
    return path.name


def scan_html_documents(doc_dir: Path) -> list[SourceDocument]:
    """
    递归扫描 ``doc_dir`` 下 ``.html`` / ``.htm``（含子目录，如 ``input/toolant/*.html``）。
    """
    documents: list[SourceDocument] = []
    if not doc_dir.exists():
        return documents

    for path in sorted(doc_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith("~$"):
            continue
        if path.suffix.lower() not in {".html", ".htm"}:
            continue
        raw = path.read_bytes()
        documents.append(
            SourceDocument(
                path=path,
                source_file=_source_file_key(path, doc_dir),
                checksum=_sha256_bytes(raw),
            )
        )
    return documents


def scan_documents(doc_dir: Path) -> list[SourceDocument]:
    """
    遍历目录下文件，仅处理 ``.docx``；子目录与扩展名不符的文件会忽略。

    返回列表按文件名排序，保证多次 build 顺序稳定。
    """
    documents: list[SourceDocument] = []
    if not doc_dir.exists():
        return documents

    for path in sorted(doc_dir.iterdir()):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix != ".docx":
            continue
        # Word 打开时的锁文件，非合法 zip
        if path.name.startswith("~$"):
            continue
        raw = path.read_bytes()
        documents.append(
            SourceDocument(
                path=path,
                source_file=path.name,
                checksum=_sha256_bytes(raw),
            )
        )
    return documents


def scan_gen_policy_documents(doc_dir: Path) -> list[SourceDocument]:
    """``input/gen.txt``：跨店 GEN 全局策略（仅根目录单文件）。"""
    path = doc_dir / "gen.txt"
    if not path.is_file():
        return []
    raw = path.read_bytes()
    return [
        SourceDocument(
            path=path,
            source_file="gen.txt",
            checksum=_sha256_bytes(raw),
        )
    ]
