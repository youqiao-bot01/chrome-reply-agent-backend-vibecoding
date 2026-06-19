"""将 ``SourceDocument`` 切分为 ``KnowledgeChunk`` 列表。"""

from .chunk_factory import make_knowledge_chunk
from .excel_row_chunker import ExcelChunkConfig, split_spreadsheet_by_rows
from .html_outline_chunker import split_document_by_html_outline
from .word_outline_chunker import split_document_by_word_outline

__all__ = [
    "ExcelChunkConfig",
    "make_knowledge_chunk",
    "split_document_by_html_outline",
    "split_document_by_word_outline",
    "split_spreadsheet_by_rows",
]
