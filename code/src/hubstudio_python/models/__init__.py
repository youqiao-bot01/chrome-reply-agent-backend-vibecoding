"""数据模型：文档、切片、manifest 条目。"""

from .chunk import KnowledgeChunk, chunk_embedding_text
from .document import SourceDocument
from .manifest import ManifestEntry

__all__ = ["KnowledgeChunk", "chunk_embedding_text", "SourceDocument", "ManifestEntry"]
