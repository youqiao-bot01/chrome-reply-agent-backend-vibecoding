"""
为 ``rag_data/kb/output/*.chunks.json`` 批量请求 Embedding API，写出 ``*.embeddings.json``。

每个切片优先用 ``entitle + "\\n" + encontent`` 拼成一段文本再向量化（为空则回退 ``title``/``content``）；
``id`` 与 chunks 文件中的 ``id`` 对齐，供后续 Chroma 或自建索引关联。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from hubstudio_python.kb.service.embedding import EmbeddingClient, EmbeddingConfig
from hubstudio_python.models import chunk_embedding_text
from hubstudio_python.kb.service.storage import write_embeddings_json


@dataclass(frozen=True)
class EmbeddingBuildResult:
    """单个 chunks 文件对应的 embedding 生成结果。"""

    input_file: str
    output_file: str
    row_count: int


def _iter_chunk_files(output_dir: Path) -> list[Path]:
    """列出输出目录下所有 ``*.chunks.json``（排序保证顺序稳定）。"""
    if not output_dir.exists():
        return []
    return sorted(path for path in output_dir.glob("*.chunks.json") if path.is_file())


def _embedding_output_path(chunk_file: Path) -> Path:
    """``foo.chunks.json`` → ``foo.embeddings.json``。"""
    return chunk_file.with_name(chunk_file.name.replace(".chunks.json", ".embeddings.json"))


def build_embeddings(
    output_dir: Path,
    *,
    chunk_files: list[Path] | None = None,
) -> list[EmbeddingBuildResult]:
    """
    遍历每个 chunks 文件，调用 ``EmbeddingClient.embed_texts``，写出同名 embeddings 文件。

    :param chunk_files: 仅处理这些 ``*.chunks.json``；为 ``None`` 时处理 output 目录下全部。

    环境变量：``HUBSTUDIO_EMBED_*``（见 ``EmbeddingConfig.from_env``）。
    """
    client = EmbeddingClient(EmbeddingConfig.from_env())
    results: list[EmbeddingBuildResult] = []
    targets = chunk_files if chunk_files is not None else _iter_chunk_files(output_dir)
    for chunk_file in targets:
        chunks = json.loads(chunk_file.read_text(encoding="utf-8"))

        # 与 chunks 数组顺序一致：一行文本对应一个向量。
        texts = [
            chunk_embedding_text(item)
            for item in chunks
            if isinstance(item, dict)
        ]
        ids = [
            str(item.get("id", "")).strip()
            for item in chunks
            if isinstance(item, dict)
        ]
        if not texts:
            continue
        vectors = client.embed_texts(texts)
        rows = [
            {
                "id": chunk_id,
                "embedding": vector,
            }
            for chunk_id, vector in zip(ids, vectors, strict=True)
            if chunk_id
        ]
        output_path = _embedding_output_path(chunk_file)
        write_embeddings_json(output_path, rows)
        results.append(
            EmbeddingBuildResult(
                input_file=chunk_file.name,
                output_file=output_path.name,
                row_count=len(rows),
            )
        )
    return results
