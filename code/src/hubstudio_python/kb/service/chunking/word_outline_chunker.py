"""
Word 标题大纲分块（``python main.py build`` 使用的默认切片策略）。

一级标题 → ``section``（意图类别），二级 → ``creator_type``（达人类型 / S/A/B/GEN，由二级标题文字解析），
三级 → ``title``，其下正文与表格 → ``content``；**每个三级标题一条切片**。

依赖 ``python-docx``；通过 ``w:outlineLvl`` 或样式名（Heading 1/标题 1 等）识别级别。
若文档无任何可识别标题结构，返回空列表（流水线仍会写出空的 chunks 数组，便于发现版式问题）。
"""

from __future__ import annotations

import re

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

from hubstudio_python.kb.service.chunking.chunk_factory import make_knowledge_chunk
from hubstudio_python.models import KnowledgeChunk, SourceDocument


def _table_to_plain(table: Table) -> str:
    rows_out: list[str] = []
    for row in table.rows:
        cells = [
            re.sub(r"\s+", " ", cell.text.replace("\n", " ").replace("\r", "").strip())
            for cell in row.cells
        ]
        if any(cells):
            rows_out.append(" | ".join(cells))
    body = "\n".join(rows_out).strip()
    return body if body else "（空表格）"


def _style_name_level(style_name: str) -> int | None:
    if not style_name:
        return None
    s = style_name.strip().lower()
    if re.search(r"heading\s*1|标题\s*1", s):
        return 0
    if re.search(r"heading\s*2|标题\s*2", s):
        return 1
    if re.search(r"heading\s*3|标题\s*3", s):
        return 2
    return None


def _paragraph_outline_level(p: Paragraph) -> int | None:
    p_pr = p._element.pPr
    if p_pr is not None:
        el = p_pr.find(qn("w:outlineLvl"))
        if el is not None:
            raw = el.get(qn("w:val"))
            if raw is not None:
                try:
                    return int(str(raw), 10)
                except ValueError:
                    pass
    return _style_name_level(p.style.name or "")


def _tier_heading_to_level(heading: str) -> str:
    h = heading.strip()
    if re.search(r"(?i)S\s*级|S[\s\-_/]档|^S\s", h) or h.upper().startswith("S级") or "S-level" in h:
        return "S"
    if re.search(r"(?i)A\s*级|^A\s", h) or "A级" in h or "A-level" in h:
        return "A"
    if re.search(r"(?i)B\s*级|^B\s", h) or "B级" in h or "B-level" in h:
        return "B"
    return "GEN"


def _iter_body_blocks(doc: DocxDocument):
    for child in doc.element.body:
        if isinstance(child, CT_P):
            yield "p", Paragraph(child, doc)
        elif isinstance(child, CT_Tbl):
            yield "tbl", Table(child, doc)


def split_document_by_word_outline(document: SourceDocument) -> list[KnowledgeChunk]:
    path = document.path
    if not path.exists() or path.suffix.lower() != ".docx":
        return []

    doc = Document(str(path))
    intent = "未分类"
    tier_heading = "通用"
    h3_title: str | None = None
    content_lines: list[str] = []
    chunks: list[KnowledgeChunk] = []
    seq = 1
    saw_heading = False

    def flush_chunk() -> None:
        nonlocal h3_title, content_lines, seq
        if h3_title is None:
            return
        body = "\n".join(content_lines).strip()
        chunks.append(
            make_knowledge_chunk(
                document.source_file,
                intent,
                "",
                h3_title,
                body,
                chunk_id=str(seq),
                creator_type_override=_tier_heading_to_level(tier_heading),
            )
        )
        seq += 1
        h3_title = None
        content_lines = []

    for kind, block in _iter_body_blocks(doc):
        if kind == "tbl":
            if h3_title is not None:
                content_lines.append("[表格]")
                content_lines.append(_table_to_plain(block))
            continue

        p = block
        text = (p.text or "").strip()
        lvl = _paragraph_outline_level(p)
        if lvl is not None:
            saw_heading = True
        if lvl == 0 and text:
            flush_chunk()
            intent = text
            h3_title = None
            continue
        if lvl == 1 and text:
            flush_chunk()
            tier_heading = text
            h3_title = None
            continue
        if lvl == 2 and text:
            flush_chunk()
            h3_title = text
            content_lines = []
            continue
        if text:
            if h3_title is not None:
                content_lines.append(text)
            elif lvl is None and not saw_heading:
                continue

    flush_chunk()
    if not saw_heading:
        return []
    return chunks
