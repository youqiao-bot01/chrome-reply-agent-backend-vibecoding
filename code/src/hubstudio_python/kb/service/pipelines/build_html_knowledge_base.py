"""
单独构建 HTML 知识库：递归扫描或按文件名指定，产出命名区分的 ``*.chunks.json`` 与 ``*.chunks.preview.md``。

不刷新 ``shop_tier_intent_hierarchy.json``（该文件仅汇总 Excel 业务维度）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from hubstudio_python.config import PathsConfig
from hubstudio_python.kb.service.ingest.scanner import scan_html_documents
from hubstudio_python.models import SourceDocument
from hubstudio_python.kb.service.pipelines.build_knowledge_base import BuildResult, build_documents


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_file_key(path: Path, doc_dir: Path) -> str:
    try:
        rel = path.relative_to(doc_dir)
        if rel.parent != Path("."):
            return rel.as_posix()
    except ValueError:
        pass
    return path.name


def resolve_html_document(doc_dir: Path, name: str) -> SourceDocument:
    """
    在 ``doc_dir`` 下定位 HTML 源文件。

    ``name`` 可为 ``linsey-agent-playbook_2.html`` 或 ``toolant/linsey-agent-playbook_2.html``。
    """
    key = name.replace("\\", "/").strip()
    direct = doc_dir / key
    if direct.is_file():
        path = direct
    else:
        basename = Path(key).name
        matches = sorted(doc_dir.rglob(basename))
        if not matches:
            raise FileNotFoundError(
                f"HTML not found under {doc_dir}: {name!r}. "
                "Try toolant/linsey-agent-playbook_2.html"
            )
        if len(matches) > 1:
            exact = [p for p in matches if _source_file_key(p, doc_dir) == key]
            path = exact[0] if exact else matches[0]
        else:
            path = matches[0]
    raw = path.read_bytes()
    return SourceDocument(
        path=path,
        source_file=_source_file_key(path, doc_dir),
        checksum=_sha256_bytes(raw),
    )


def build_html_knowledge_base(
    paths: PathsConfig,
    *,
    html_files: list[str] | None = None,
) -> list[BuildResult]:
    """
    切块 HTML 并写入 ``rag_data/kb/output/``。

    :param html_files: 指定相对 ``doc_dir`` 的文件名列表；``None`` 时处理全部 HTML。
    """
    if html_files:
        documents = [resolve_html_document(paths.input_dir, name) for name in html_files]
    else:
        documents = scan_html_documents(paths.input_dir)
    if not documents:
        return []
    return build_documents(
        paths,
        documents,
        refresh_shop_tier=False,
        html_write_preview=True,
    )
