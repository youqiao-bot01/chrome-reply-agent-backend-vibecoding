"""
知识库构建流水线：docx → 切片 JSON + manifest（Word 大纲分块）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc

from hubstudio_python.kb.service.chunking import split_document_by_html_outline, split_document_by_word_outline
from hubstudio_python.kb.service.chunking.excel_row_chunker import ExcelChunkConfig, split_spreadsheet_by_rows
from hubstudio_python.kb.service.chunking.zh_translate import (
    ChunkTranslateConfig,
    enrich_chunks_zh_with_deepseek,
)
from hubstudio_python.config import PathsConfig, load_project_yaml
from hubstudio_python.kb.service.ingest import scan_documents, scan_html_documents
from hubstudio_python.models import KnowledgeChunk, ManifestEntry
from hubstudio_python.kb.service.storage import write_chunk_records_json, write_chunks_json


@dataclass(frozen=True)
class BuildResult:
    """单次 build 中，单个源文档对应的产出摘要。"""

    source_file: str
    output_file: str
    chunk_count: int
    source_type: str = "word"


def output_name_for_source(source_file: str) -> str:
    """
    由源文件路径生成 ``*.chunks.json`` 文件名。

    - ``AI话术参考.xlsx`` → ``ai话术参考.chunks.json``
    - ``toolant/linsey-agent-playbook_2.html`` → ``toolant-linsey-agent-playbook-2.chunks.json``（含父目录，避免与其它 HTML 重名）
    """
    p = Path(source_file.replace("\\", "/"))
    if len(p.parts) > 1:
        raw = "-".join((*p.parts[:-1], p.stem))
    else:
        raw = p.stem
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in raw)
    while "--" in safe:
        safe = safe.replace("--", "-")
    safe = safe.strip("-") or "knowledge"
    return f"{safe}.chunks.json"


def preview_name_for_chunks(chunks_json_name: str) -> str:
    """``foo.chunks.json`` → ``foo.chunks.preview.md``（可读中间稿）。"""
    if chunks_json_name.endswith(".chunks.json"):
        return chunks_json_name.replace(".chunks.json", ".chunks.preview.md")
    return f"{chunks_json_name}.preview.md"


def translate_config_from_project(project_root: Path) -> ChunkTranslateConfig | None:
    yaml_data = load_project_yaml(project_root)
    ai = yaml_data.get("ai")
    if isinstance(ai, dict):
        return ChunkTranslateConfig.from_ai_yaml_section(ai)
    return None


def persist_document_chunks(
    paths: PathsConfig,
    document,
    chunks: list[KnowledgeChunk],
    *,
    source_type: str,
    output_basename: str | None = None,
    write_preview: bool = False,
) -> BuildResult:
    output_name = output_basename or output_name_for_source(document.source_file)
    output_path = paths.output_dir / output_name
    write_chunks_json(output_path, chunks)
    if write_preview:
        from hubstudio_python.kb.service.storage.json_store import write_chunks_preview_md

        preview_path = paths.output_dir / preview_name_for_chunks(output_name)
        write_chunks_preview_md(preview_path, chunks)
    from hubstudio_python.kb.service.ingest.manifest import upsert_manifest_entry

    upsert_manifest_entry(
        paths.manifest_file,
        ManifestEntry(
            source_file=document.source_file,
            checksum=document.checksum,
            output_file=output_name,
            chunk_count=len(chunks),
            updated_at=datetime.now(UTC).isoformat(),
        ),
    )
    return BuildResult(
        source_file=document.source_file,
        output_file=output_name,
        chunk_count=len(chunks),
        source_type=source_type,
    )


def persist_playbook_structured_chunks(
    paths: PathsConfig,
    document,
    records: list[dict],
) -> BuildResult:
    """从 ``*.structured.full.json`` 展平写入 ``*.chunks.json``（Playbook 规则库）。"""
    output_name = output_name_for_source(document.source_file)
    output_path = paths.output_dir / output_name
    write_chunk_records_json(output_path, records)
    from hubstudio_python.kb.service.ingest.manifest import upsert_manifest_entry

    upsert_manifest_entry(
        paths.manifest_file,
        ManifestEntry(
            source_file=document.source_file,
            checksum=document.checksum,
            output_file=output_name,
            chunk_count=len(records),
            updated_at=datetime.now(UTC).isoformat(),
        ),
    )
    return BuildResult(
        source_file=document.source_file,
        output_file=output_name,
        chunk_count=len(records),
        source_type="playbook",
    )


def build_documents(
    paths: PathsConfig,
    documents: list,
    *,
    translate_cfg: ChunkTranslateConfig | None = None,
    refresh_shop_tier: bool = True,
    html_write_preview: bool = False,
) -> list[BuildResult]:
    """对给定 ``SourceDocument`` 列表切块并写盘（Word / Excel / HTML）。"""
    if translate_cfg is None:
        translate_cfg = translate_config_from_project(paths.project_root)
    yaml_data = load_project_yaml(paths.project_root)
    rag = yaml_data.get("rag") if isinstance(yaml_data.get("rag"), dict) else None
    excel_cfg = ExcelChunkConfig.from_yaml(rag)

    results: list[BuildResult] = []
    for document in documents:
        suffix = document.path.suffix.lower()
        if suffix in {".xlsx", ".xlsm"}:
            chunks = split_spreadsheet_by_rows(document, excel_cfg)
            chunks = enrich_chunks_zh_with_deepseek(chunks, translate_cfg)
            results.append(
                persist_document_chunks(paths, document, chunks, source_type="excel")
            )
        elif suffix in {".html", ".htm"}:
            from hubstudio_python.kb.service.pipelines.structure_playbook_chunks import (
                flatten_structured_playbook_to_chunk_records,
                structured_full_json_path,
            )

            spath = structured_full_json_path(paths.output_dir, document.source_file)
            if spath.is_file():
                records = flatten_structured_playbook_to_chunk_records(
                    paths,
                    html_source_file=document.source_file,
                    structured_path=spath,
                )
                if not records:
                    raise ValueError(
                        f"{spath} 无有效 records；请重新运行 structure-playbook --mode document"
                    )
                results.append(persist_playbook_structured_chunks(paths, document, records))
            else:
                raise FileNotFoundError(
                    f"Playbook 需先有 {spath.name}（DeepSeek 结构化产出）。\n"
                    "请先运行: uv run python main.py structure-playbook --mode document\n"
                    "再运行: uv run python main.py build --incremental"
                )
        elif document.source_file.replace("\\", "/").endswith("gen.txt"):
            from hubstudio_python.kb.service.chunking.gen_policy_chunker import split_gen_policy_to_records

            records = split_gen_policy_to_records(document)
            if not records:
                raise ValueError(f"{document.source_file} 无有效 GEN 策略条目")
            results.append(persist_playbook_structured_chunks(paths, document, records))
            from hubstudio_python.kb.service.pipelines.sync_runtime_assets import sync_runtime_kb_assets

            sync_runtime_kb_assets(paths.output_dir)
        else:
            chunks = split_document_by_word_outline(document)
            chunks = enrich_chunks_zh_with_deepseek(chunks, translate_cfg)
            results.append(
                persist_document_chunks(paths, document, chunks, source_type="word")
            )
    if refresh_shop_tier:
        from hubstudio_python.kb.service.pipelines.build_shop_tier_intent_hierarchy import (
            build_shop_tier_intent_hierarchy,
        )

        build_shop_tier_intent_hierarchy(paths.output_dir)
    return results


def build_knowledge_base(paths: PathsConfig) -> list[BuildResult]:
    """扫描 ``doc_dir`` 下 ``.docx`` 与递归 ``.html`` / ``.htm``，按标题大纲切分。"""
    documents = scan_documents(paths.input_dir) + scan_html_documents(paths.input_dir)
    return build_documents(paths, documents)
