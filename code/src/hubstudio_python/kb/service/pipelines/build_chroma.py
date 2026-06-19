"""
ChromaDB 构建：把 ``*.chunks.json`` + 同名 ``*.embeddings.json`` 写入向量库。

步骤概要
--------
1. 按环境变量连接 Chroma（本地持久化 / Http / 内存）。
2. 删除并重建集合（全量刷新）。
3. 对每个 chunks 文件：按 ``id`` 对齐 embedding，批量 ``collection.add``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hubstudio_python.models import chunk_embedding_text
from hubstudio_python.kb.service.storage import ChromaStore, ChromaConfig, read_embeddings_json


def _chroma_meta_value(value: object) -> str:
    """Chroma 元数据值须为可索引标量：列表（来自 chunks JSON 数组）序列化为 JSON 字符串。"""
    if value is None:
        return ""
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


@dataclass(frozen=True)
class ChromaResult:
    """ChromaDB 构建结果类"""
    # 成功添加的 chunks 数量
    chunks_added: int
    # 总 chunks 数量
    total_chunks: int
    # 集合名称
    collection_name: str


def _read_chunks_json(path: Path) -> list[dict[str, Any]]:
    """读取 chunks.json 文件

    Args:
        path: 文件路径

    Returns:
        chunks 数据列表
    """
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _build_metadata(chunk: dict[str, Any]) -> dict[str, Any]:
    """构建元数据字典（含 Excel 行内其它列；``id`` 单独写入 ``original_id``）。"""
    ic = chunk.get("intent_category", chunk.get("section", ""))
    meta: dict[str, Any] = {
        "intent_category": _chroma_meta_value(ic),
        "creator_type": _chroma_meta_value(
            chunk.get("creator_type") or chunk.get("level", "")
        ),
        "title": str(chunk.get("title", "") or ""),
        "language": str(chunk.get("language", "") or ""),
        "content": str(chunk.get("content", "") or ""),
        "entitle": str(chunk.get("entitle", "") or ""),
        "encontent": str(chunk.get("encontent", "") or ""),
        "source_file": str(chunk.get("source_file", "") or ""),
    }
    _excel_flat_keys = (
        "rule_type",
        "applicable_shops",
        "creator_reply_frequency",
        "creator_emotion",
        "ai_acation",
        "other_creator_conditions",
        "creator_type_and",
        "creator_progress",
        "key_information",
        "restore_slots",
        "answer_purpose",
        "creator_action_guide",
    )
    sf = str(chunk.get("source_file", "") or "").lower()
    is_excel_like = chunk.get("chunk_source_type") == "excel" or sf.endswith(
        (".xlsx", ".xlsm")
    )
    for flat_key in _excel_flat_keys:
        val: object | None = chunk.get(flat_key)
        if flat_key == "creator_type_and" and val is None and "creator_type_or" in chunk:
            val = chunk.get("creator_type_or")
        if flat_key == "ai_acation" and val is None:
            val = chunk.get("ai_action")
        if is_excel_like:
            if val is None:
                continue
            if isinstance(val, str) and not val.strip():
                continue
            if isinstance(val, list) and len(val) == 0:
                continue
            s = _chroma_meta_value(val)
            if not str(s).strip():
                continue
            meta[flat_key] = s
            continue
        in_doc = flat_key in chunk or (
            flat_key == "creator_type_and" and "creator_type_or" in chunk
        ) or (flat_key == "ai_acation" and "ai_action" in chunk)
        if in_doc and val is not None:
            meta[flat_key] = _chroma_meta_value(val)
    extra = chunk.get("extra_metadata")
    if isinstance(extra, dict):
        for key, value in extra.items():
            if value is None:
                continue
            safe_key = f"col_{key}" if not str(key).startswith("col_") else str(key)
            meta[safe_key] = str(value)
    return meta


def _chroma_doc_id(chunks_file: Path, index: int, chunk_id: object) -> str:
    """含 chunks 文件 stem，避免不同源文档间 ``chunk_{i}_{id}`` 冲突。"""
    return f"{chunks_file.stem}_{index}_{chunk_id}"


def upsert_chunks_files(
    store: ChromaStore,
    chunks_files: list[Path],
) -> tuple[int, int]:
    """
    将指定 ``*.chunks.json`` + 同名 embeddings 增量写入 Chroma（不重建集合）。

    :return: ``(chunks_added, total_chunks_in_files)``
    """
    total_chunks = 0
    chunks_added = 0
    for chunks_file in chunks_files:
        chunks = _read_chunks_json(chunks_file)
        embeddings_file = chunks_file.parent / (
            chunks_file.stem.replace(".chunks", "") + ".embeddings.json"
        )
        embeddings = read_embeddings_json(embeddings_file)
        if not embeddings:
            print(f"Warning: No embeddings found for {chunks_file.name}, skipping...")
            continue
        embedding_map = {item.get("id"): item.get("embedding") for item in embeddings}
        ids: list[str] = []
        vectors: list[list[float]] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []
        for i, chunk in enumerate(chunks):
            chunk_id = chunk.get("id")
            embedding = embedding_map.get(chunk_id)
            if not embedding:
                print(f"Warning: No embedding found for chunk {chunk_id}, skipping...")
                continue
            sequence_id = _chroma_doc_id(chunks_file, i, chunk_id)
            metadata = _build_metadata(chunk)
            metadata["original_id"] = chunk_id
            ids.append(sequence_id)
            vectors.append(embedding)
            documents.append(chunk_embedding_text(chunk))
            metadatas.append(metadata)
            chunks_added += 1
        total_chunks += len(chunks)
        if ids:
            store.add_documents(
                ids=ids,
                embeddings=vectors,
                documents=documents,
                metadatas=metadatas,
            )
    return chunks_added, total_chunks


def build_chroma(output_dir: Path, config: ChromaConfig | None = None) -> ChromaResult:
    """构建 ChromaDB 向量数据库

    Args:
        output_dir: 输出目录路径
        config: ChromaDB 配置对象

    Returns:
        构建结果对象

    Raises:
        ValueError: 如果没有找到 chunks 文件
    """
    # 如果没有提供配置，从环境变量加载
    if config is None:
        config = ChromaConfig.from_env()

    # 初始化 ChromaDB 存储
    store = ChromaStore(config)

    # 删除旧集合并重新创建（清空数据）
    store.delete_collection()

    # 查找所有 chunks.json 文件
    chunks_files = list(output_dir.glob("*.chunks.json"))
    if not chunks_files:
        raise ValueError(f"No chunks files found in {output_dir}")

    chunks_added, total_chunks = upsert_chunks_files(store, chunks_files)

    # 返回构建结果
    return ChromaResult(
        chunks_added=chunks_added,
        total_chunks=total_chunks,
        collection_name=config.collection_name,
    )