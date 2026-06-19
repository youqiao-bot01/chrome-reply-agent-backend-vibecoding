#!/usr/bin/env python3
"""查询 ChromaDB 集合内容（配置加载方式与 ``code/main.py`` 一致）。"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

CODE = Path(__file__).resolve().parents[3]
CODE_SRC = CODE / "src"
sys.path.insert(0, str(CODE_SRC))

from hubstudio_python.config import bootstrap_rag_env
from hubstudio_python.kb.service.storage.chroma_store import ChromaConfig, ChromaStore


def main() -> int:
    bootstrap_rag_env()
    config = ChromaConfig.from_env()

    try:
        store = ChromaStore(config)
        if config.host and config.port:
            mode = f"remote ({config.host}:{config.port})"
        elif config.persist_directory:
            mode = f"local persist ({config.persist_directory})"
        else:
            mode = "in-memory (no host/port/persist_dir — 查不到 main.py 写入的数据)"

        print(
            f"Chroma config: host={config.host} port={config.port} "
            f"persist_directory={config.persist_directory} collection={config.collection_name}"
        )
        print(f"mode: {mode}")
        print(f"client_type: {store.client.__class__.__name__}")

        collection = store.collection
        results = collection.get()

        print("=== ChromaDB 数据查询结果 ===")
        print(f"总文档数: {len(results['ids'])}")
        print()

        for i, (doc_id, document, metadata) in enumerate(
            zip(results["ids"], results["documents"], results["metadatas"], strict=True)
        ):
            preview = (document or "")[:100]
            preview_safe = preview.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
                sys.stdout.encoding or "utf-8", errors="replace"
            )
            print(f"文档 {i + 1}:")
            print(f"ID: {doc_id}")
            print(f"内容: {preview_safe}...")
            print(f"元数据: {json.dumps(metadata, ensure_ascii=False, indent=2)}")
            print()

    except Exception as e:
        print(f"查询失败: {type(e).__name__}: {repr(e)}")
        print("---- traceback ----")
        print(traceback.format_exc())
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
