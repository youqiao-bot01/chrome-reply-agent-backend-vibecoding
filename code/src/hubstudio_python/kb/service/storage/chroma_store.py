"""
ChromaDB 存储模块

提供 ChromaDB 向量数据库的封装和操作接口
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import request as url_request

# 尝试导入 chromadb，如果未安装则设置为 None
try:
    import chromadb
    from chromadb.config import Settings
except ImportError:
    chromadb = None


def _allow_reset_from_env() -> bool:
    """
    是否允许客户端发起 reset 类高危操作（``Settings.allow_reset``）。

    远程 Chroma 若对未声明 allow_reset 的客户端限制部分 API，可显式设为 ``true``。
    环境变量 ``HUBSTUDIO_CHROMA_ALLOW_RESET``：未设置时默认为 ``true``。
    """
    raw = os.environ.get("HUBSTUDIO_CHROMA_ALLOW_RESET", "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


@dataclass(frozen=True)
class ChromaConfig:
    """ChromaDB 配置类"""
    collection_name: str
    persist_directory: str | None = None
    host: str | None = None
    port: int | None = None
    ssl: bool = False
    headers: dict[str, str] | None = None
    tenant: str = "default_tenant"
    database: str = "default_database"

    @classmethod
    def from_env(cls) -> "ChromaConfig":
        """从环境变量加载配置"""
        collection_name = os.environ.get("HUBSTUDIO_CHROMA_COLLECTION", "knowledge_base").strip()
        persist_directory = os.environ.get("HUBSTUDIO_CHROMA_PERSIST_DIR", "").strip() or None
        host = os.environ.get("HUBSTUDIO_CHROMA_HOST", "").strip() or None
        port_str = os.environ.get("HUBSTUDIO_CHROMA_PORT", "").strip()
        port = int(port_str) if port_str else None

        ssl_raw = os.environ.get("HUBSTUDIO_CHROMA_SSL", "").strip().lower()
        ssl = ssl_raw in ("1", "true", "yes", "on")

        headers: dict[str, str] | None = None
        raw_h = os.environ.get("HUBSTUDIO_CHROMA_HEADERS_JSON", "").strip()
        if raw_h:
            try:
                parsed = json.loads(raw_h)
                if isinstance(parsed, dict):
                    headers = {str(k): str(v) for k, v in parsed.items()}
            except json.JSONDecodeError:
                headers = None

        tenant = os.environ.get("HUBSTUDIO_CHROMA_TENANT", "default_tenant").strip() or "default_tenant"
        database = os.environ.get("HUBSTUDIO_CHROMA_DATABASE", "default_database").strip() or "default_database"

        return cls(
            collection_name=collection_name,
            persist_directory=persist_directory,
            host=host,
            port=port,
            ssl=ssl,
            headers=headers,
            tenant=tenant,
            database=database,
        )


class ChromaStore:
    """ChromaDB 存储类

    提供向量数据库的连接、存储和查询功能
    """

    def __init__(self, config: ChromaConfig) -> None:
        """初始化 ChromaDB 连接

        Args:
            config: ChromaDB 配置对象

        Raises:
            ImportError: 如果 chromadb 未安装
        """
        self.config = config

        # 根据配置选择不同的客户端
        if config.host and config.port:
            # 远程服务器连接：可能出现偶发 502/超时/卡住，先做短超时探活 + 再重试连接
            timeout_s = float(os.environ.get("HUBSTUDIO_CHROMA_TIMEOUT_SECONDS", "10") or "10")
            scheme = "https" if config.ssl else "http"
            probe_url = f"{scheme}://{config.host}:{config.port}/api/v2/auth/identity"
            try:
                req = url_request.Request(probe_url, headers=dict(config.headers or {}))
                with url_request.urlopen(req, timeout=max(1.0, timeout_s)) as resp:
                    _ = resp.read(256)
            except Exception as exc:
                raise RuntimeError(
                    f"Remote ChromaDB identity probe failed at {config.host}:{config.port} "
                    f"(set HUBSTUDIO_CHROMA_TIMEOUT_SECONDS to tune): {exc!r}"
                ) from exc

            last_exc: Exception | None = None
            for attempt in range(1, 6):
                try:
                    settings = Settings(
                        anonymized_telemetry=False,
                        allow_reset=_allow_reset_from_env(),
                        chroma_query_request_timeout_seconds=max(1, int(timeout_s)),
                        chroma_sysdb_request_timeout_seconds=max(1, int(timeout_s)),
                        chroma_logservice_request_timeout_seconds=max(1, int(timeout_s)),
                    )
                    self.client = chromadb.HttpClient(
                        host=config.host,
                        port=config.port,
                        ssl=config.ssl,
                        headers=config.headers,
                        tenant=config.tenant,
                        database=config.database,
                        settings=settings,
                    )
                    print(f"Connected to remote ChromaDB at {config.host}:{config.port} (attempt {attempt})")
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    if attempt < 5:
                        time.sleep(2)
                    continue
            if last_exc is not None:
                raise ValueError(
                    f"Failed to connect to remote ChromaDB at {config.host}:{config.port}: {last_exc!r}"
                ) from last_exc
        elif config.persist_directory:
            # 本地持久化存储
            self.client = chromadb.PersistentClient(path=config.persist_directory)
        else:
            # 内存存储（临时）
            self.client = chromadb.Client()

        # 获取或创建集合，使用余弦相似度
        self.collection = self.client.get_or_create_collection(
            name=config.collection_name,
            metadata={"hnsw:space": "cosine"}
        )

    def add_documents(
        self,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """添加文档到 ChromaDB

        Args:
            ids: 文档唯一标识符列表
            embeddings: 向量嵌入列表
            documents: 原始文档内容列表
            metadatas: 元数据列表
        """
        self.collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

    def query(
        self,
        query_embeddings: list[list[float]] | None = None,
        query_texts: list[str] | None = None,
        n_results: int = 10,
        where: dict[str, Any] | None = None,
        where_document: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """查询相似文档

        Args:
            query_embeddings: 查询向量列表
            query_texts: 查询文本列表
            n_results: 返回结果数量
            where: 元数据过滤条件
            where_document: 文档内容过滤条件

        Returns:
            查询结果字典
        """
        return self.collection.query(
            query_embeddings=query_embeddings,
            query_texts=query_texts,
            n_results=n_results,
            where=where,
            where_document=where_document,
        )

    def count(self) -> int:
        """获取集合中文档数量

        Returns:
            文档数量
        """
        return self.collection.count()

    def distinct_metadata_source_files(self) -> list[str]:
        """集合中所有 ``metadata.source_file`` 的去重列表（用于增量下线文件名对齐）。"""
        got = self.collection.get(include=["metadatas"])
        metas = got.get("metadatas") or []
        seen: set[str] = set()
        for m in metas:
            if not isinstance(m, dict):
                continue
            raw = m.get("source_file")
            if raw is None:
                continue
            s = str(raw).strip()
            if s:
                seen.add(s)
        return sorted(seen)

    def delete_by_where(self, where: dict[str, Any]) -> None:
        """按元数据 where 条件删除向量（增量 ``chroma --incremental`` 等使用）。"""
        self.collection.delete(where=where)

    def delete_by_source_files(self, source_files: list[str]) -> int:
        """
        按元数据 ``source_file`` 删除某 docx 在库中的全部切片向量。

        先 ``get(where=...)`` 取 id 再 ``delete(ids=...)``，远程 Chroma 上更可靠；
        若无命中则再尝试 ``delete(where=...)``。

        :return: 实际删除的向量条数。
        """
        total_deleted = 0
        for name in source_files:
            cleaned = str(name).strip()
            if not cleaned:
                continue
            where = {"source_file": {"$eq": cleaned}}
            ids: list[str] = []
            try:
                got = self.collection.get(where=where, include=[])
                ids = [str(i) for i in (got.get("ids") or []) if i]
            except Exception:
                ids = []
            if ids:
                self.collection.delete(ids=ids)
                total_deleted += len(ids)
            else:
                try:
                    self.delete_by_where(where)
                except Exception:
                    pass
        return total_deleted

    def delete_collection(self) -> None:
        """删除并重新创建集合（清空数据）"""
        self.client.delete_collection(name=self.config.collection_name)
        self.collection = self.client.get_or_create_collection(
            name=self.config.collection_name,
            metadata={"hnsw:space": "cosine"}
        )

    def get_by_ids(self, ids: list[str]) -> dict[str, Any]:
        """根据 ID 获取文档

        Args:
            ids: 文档 ID 列表

        Returns:
            文档数据字典
        """
        return self.collection.get(ids=ids)