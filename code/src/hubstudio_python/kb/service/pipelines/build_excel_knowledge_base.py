"""
Excel 知识库构建：``rag_data/kb/input/*.xlsx`` → ``*.chunks.json`` + manifest。
"""

from __future__ import annotations

from typing import Any

from hubstudio_python.kb.service.chunking.excel_row_chunker import ExcelChunkConfig, split_spreadsheet_by_rows
from hubstudio_python.kb.service.chunking.zh_translate import enrich_chunks_zh_with_deepseek
from hubstudio_python.config import PathsConfig, load_project_yaml
from hubstudio_python.kb.service.ingest import scan_spreadsheets
from hubstudio_python.kb.service.pipelines.build_knowledge_base import (
    BuildResult,
    persist_document_chunks,
    translate_config_from_project,
)


def _excel_file_list(yaml_data: dict[str, Any]) -> list[str] | None:
    rag = yaml_data.get("rag")
    if not isinstance(rag, dict):
        return None
    excel = rag.get("excel")
    if not isinstance(excel, dict):
        return None
    files = excel.get("files")
    if not isinstance(files, list) or not files:
        return None
    return [str(f).strip() for f in files if str(f).strip()]


def build_excel_knowledge_base(paths: PathsConfig) -> list[BuildResult]:
    """
    扫描配置的 xlsx（或 doc 下全部 xlsx），按行切片并写 manifest。

    配置见 ``config.yaml`` → ``rag.excel``。
    """
    yaml_data = load_project_yaml(paths.project_root)
    rag = yaml_data.get("rag") if isinstance(yaml_data.get("rag"), dict) else None
    excel_cfg = ExcelChunkConfig.from_yaml(rag)

    only = _excel_file_list(yaml_data)
    documents = scan_spreadsheets(paths.input_dir, only_names=only)
    if not documents:
        hint = f" (rag.excel.files={only})" if only else ""
        raise FileNotFoundError(
            f"No .xlsx found under {paths.input_dir}{hint}. "
            "Place .xlsx under rag_data/kb/input/ (or subfolders like Linknlatch/) or set rag.excel.files."
        )

    translate_cfg = translate_config_from_project(paths.project_root)
    results: list[BuildResult] = []
    for document in documents:
        chunks = split_spreadsheet_by_rows(document, excel_cfg)
        chunks = enrich_chunks_zh_with_deepseek(chunks, translate_cfg)
        results.append(
            persist_document_chunks(
                paths,
                document,
                chunks,
                source_type="excel",
            )
        )
    from hubstudio_python.kb.service.pipelines.build_shop_tier_intent_hierarchy import (
        build_shop_tier_intent_hierarchy,
    )

    build_shop_tier_intent_hierarchy(paths.output_dir)
    return results
