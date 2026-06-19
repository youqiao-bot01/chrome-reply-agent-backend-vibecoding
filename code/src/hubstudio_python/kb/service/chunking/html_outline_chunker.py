"""
HTML 按标题分块（``h1`` / ``h2`` / ``h3`` → 切片）。

使用 LangChain ``HTMLHeaderTextSplitter`` 解析正文；``style`` / ``script`` 等不会进入文本。
"""

from __future__ import annotations

import re
from pathlib import Path

from langchain_text_splitters import HTMLHeaderTextSplitter

from hubstudio_python.kb.service.chunking.chunk_factory import make_knowledge_chunk
from hubstudio_python.models import KnowledgeChunk, SourceDocument

_HEADER_SPLITS: list[tuple[str, str]] = [
    ("h1", "section"),
    ("h2", "intent"),
    ("h3", "title"),
]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def split_document_by_html_outline(document: SourceDocument) -> list[KnowledgeChunk]:
    """读取 ``.html`` / ``.htm``，按 ``h1→h2→h3`` 切块为 ``KnowledgeChunk``。"""
    path = document.path
    if not path.exists() or path.suffix.lower() not in {".html", ".htm"}:
        return []

    html = path.read_text(encoding="utf-8")
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    splitter = HTMLHeaderTextSplitter(headers_to_split_on=_HEADER_SPLITS)
    splits = splitter.split_text(html)

    chunks: list[KnowledgeChunk] = []
    seq = 1
    doc_section = ""
    default_intent = ""

    for split in splits:
        meta = split.metadata or {}
        section = _norm(str(meta.get("section") or ""))
        intent = _norm(str(meta.get("intent") or ""))
        title = _norm(str(meta.get("title") or ""))
        content = (split.page_content or "").strip()

        if section:
            doc_section = section
        if intent:
            default_intent = intent

        intent_category = intent or default_intent or doc_section or "General"
        chunk_title = title or intent or doc_section or f"HTML块-{seq}"
        body = content if content else chunk_title

        if _norm(body) == _norm(chunk_title) and len(body) < 40:
            continue
        if chunk_title.startswith("HTML块-") and intent_category == "General" and len(body) < 150:
            continue

        chunks.append(
            make_knowledge_chunk(
                document.source_file,
                intent_category,
                "",
                chunk_title,
                body,
                chunk_id=str(seq),
                creator_type_override="GEN",
            )
        )
        seq += 1

    if chunks:
        return chunks

    # 无可用标题结构：整页正文作为一条
    from langchain_community.document_loaders import BSHTMLLoader

    docs = BSHTMLLoader(str(path), open_encoding="utf-8").load()
    if not docs:
        return []
    full = (docs[0].page_content or "").strip()
    if not full:
        return []
    return [
        make_knowledge_chunk(
            document.source_file,
            doc_section or Path(path).stem,
            "",
            doc_section or Path(path).stem,
            full,
            chunk_id="1",
            creator_type_override="GEN",
        )
    ]
