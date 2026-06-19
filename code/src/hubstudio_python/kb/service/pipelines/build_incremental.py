"""
增量更新：各子命令通过 ``--incremental`` 读取 ``rag_data/incremental_update.yaml``，分步执行。

- **build --incremental**：``deleted`` → 删 output 中间 json、manifest、``kb/input/`` 源文件；``added``/``modified`` → 切块写 manifest
- **embed --incremental**：仅对 ``added``/``modified`` 对应的 ``*.chunks.json`` 请求 Embedding
- **chroma --incremental**：``deleted`` 与 ``added``/``modified`` 在 Chroma 中按 ``source_file`` 删旧向量，再 upsert 清单里重建后的切片

须按顺序执行：``build --incremental`` → ``embed --incremental`` → ``chroma --incremental``。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from hubstudio_python.config import PathsConfig
from hubstudio_python.kb.service.ingest.incremental_targets import (
    IncrementalChanges,
    align_incremental_changes_to_catalog,
    load_incremental_changes,
    resolve_documents,
)
from hubstudio_python.kb.service.ingest.manifest import load_manifest, remove_manifest_entries
from hubstudio_python.kb.service.pipelines.build_chroma import upsert_chunks_files
from hubstudio_python.kb.service.pipelines.build_embeddings import build_embeddings
from hubstudio_python.kb.service.pipelines.build_knowledge_base import (
    BuildResult,
    build_documents,
    output_name_for_source,
)
from hubstudio_python.kb.service.pipelines.incremental_shop_plan import plan_incremental_build
from hubstudio_python.kb.service.storage import ChromaConfig, ChromaStore


def _load_resolved_changes(
    paths: PathsConfig,
    list_file: Path | None,
    *,
    extra_name_pool: Sequence[str] | None = None,
) -> tuple[IncrementalChanges, list[str]]:
    list_path = list_file or paths.incremental_update_file
    changes = load_incremental_changes(list_path)
    aligned, warns = align_incremental_changes_to_catalog(
        changes, paths, extra_pool=extra_name_pool
    )
    for line in warns:
        print(line, file=sys.stderr)
    return aligned, warns


def _chunks_paths(paths: PathsConfig, source_files: list[str]) -> list[Path]:
    return [paths.output_dir / output_name_for_source(name) for name in source_files]


def _output_names_for_sources(paths: PathsConfig, source_files: list[str]) -> list[str]:
    """解析要删除的 chunks/embeddings 文件名（优先 manifest，否则按 slug 推算）。"""
    manifest = load_manifest(paths.manifest_file)
    names: list[str] = []
    for source_file in source_files:
        entry = manifest.get(source_file)
        if isinstance(entry, dict) and entry.get("output_file"):
            names.append(str(entry["output_file"]))
        else:
            names.append(output_name_for_source(source_file))
    return names


def _artifact_paths_to_remove(paths: PathsConfig, source_files: list[str]) -> list[Path]:
    """manifest 记录的 output 名 + 按文件名 slug 推算名，避免漏删中间 json。"""
    candidates: list[str] = []
    for name in _output_names_for_sources(paths, source_files):
        if name not in candidates:
            candidates.append(name)
    for source_file in source_files:
        slug_name = output_name_for_source(source_file)
        if slug_name not in candidates:
            candidates.append(slug_name)
    paths_out: list[Path] = []
    for output_name in candidates:
        stem = output_name.replace(".chunks.json", "")
        for name in (output_name, f"{stem}.embeddings.json"):
            paths_out.append(paths.output_dir / name)
    return paths_out


def _remove_local_artifacts(paths: PathsConfig, source_files: list[str]) -> list[str]:
    """删除 output 下的 chunks / embeddings 中间文件。"""
    removed: list[str] = []
    for path in _artifact_paths_to_remove(paths, source_files):
        if path.is_file():
            path.unlink()
            removed.append(path.name)
    return removed


def _remove_source_files_from_input_dir(paths: PathsConfig, source_files: list[str]) -> list[str]:
    """下架：从 rag_data/kb/input 删除源文件（若仍存在）。"""
    removed: list[str] = []
    for source_file in source_files:
        input_path = paths.input_dir / source_file
        if input_path.is_file():
            input_path.unlink()
            removed.append(source_file)
    return removed


@dataclass(frozen=True)
class IncrementalBuildOutput:
    """``build --incremental`` 结果。"""

    added: list[str]
    deleted: list[str]
    modified: list[str]
    manifest_entries_removed: int
    local_artifacts_removed: list[str]
    doc_files_removed: list[str]
    build_results: list[BuildResult]


@dataclass(frozen=True)
class IncrementalChromaOutput:
    """``chroma --incremental`` 结果。"""

    added: list[str]
    deleted: list[str]
    modified: list[str]
    chroma_vectors_deleted: int
    chunks_added: int
    total_chunks_in_files: int
    collection_name: str


def run_incremental_build(
    paths: PathsConfig,
    *,
    list_file: Path | None = None,
) -> IncrementalBuildOutput:
    """
    **deleted**：删 ``output`` 中间 json、manifest 条目、``input/`` 源文件（不连 Chroma）。

    **added / modified**：店铺 supplement（如 ``toolant/add1.txt``）会自动
    ``structure-playbook`` 并 rebuild 该店 playbook HTML。
    """
    changes, _ = _load_resolved_changes(paths, list_file)
    plan = plan_incremental_build(changes, project_root=paths.project_root)

    manifest_removed = 0
    local_removed: list[str] = []
    doc_removed: list[str] = []
    if plan.chroma_delete_files:
        local_removed = _remove_local_artifacts(paths, plan.chroma_delete_files)
        manifest_removed = remove_manifest_entries(paths.manifest_file, plan.chroma_delete_files)
        doc_removed = _remove_source_files_from_input_dir(paths, changes.deleted)

    build_results: list[BuildResult] = []
    if plan.structure_playbooks:
        from hubstudio_python.kb.service.pipelines.structure_playbook_chunks import structure_playbook_document

        for _shop, html_file, supplements in plan.structure_playbooks:
            print(
                f"[incremental] structure-playbook: {html_file}"
                + (f" (+ {', '.join(supplements)})" if supplements else ""),
                file=sys.stderr,
            )
            structure_playbook_document(
                paths,
                html_file=html_file,
                supplement_files=supplements,
            )

    if plan.build_upsert_files:
        documents = resolve_documents(paths.input_dir, plan.build_upsert_files)
        build_results = build_documents(paths, documents)
    else:
        from hubstudio_python.kb.service.pipelines.build_shop_tier_intent_hierarchy import (
            build_shop_tier_intent_hierarchy,
        )

        build_shop_tier_intent_hierarchy(paths.output_dir)

    return IncrementalBuildOutput(
        added=changes.added,
        deleted=changes.deleted,
        modified=changes.modified,
        manifest_entries_removed=manifest_removed,
        local_artifacts_removed=local_removed,
        doc_files_removed=doc_removed,
        build_results=build_results,
    )


def run_incremental_embed(
    paths: PathsConfig,
    *,
    list_file: Path | None = None,
):
    """仅对 ``added``/``modified`` 对应的 ``*.chunks.json`` 生成 embeddings。"""
    changes, _ = _load_resolved_changes(paths, list_file)
    plan = plan_incremental_build(changes, project_root=paths.project_root)
    if not plan.build_upsert_files:
        return []
    chunk_paths = _chunks_paths(paths, plan.build_upsert_files)
    return build_embeddings(paths.output_dir, chunk_files=chunk_paths)


def run_incremental_chroma(
    paths: PathsConfig,
    *,
    list_file: Path | None = None,
) -> IncrementalChromaOutput:
    """
    **deleted**：按 ``source_file`` 删 Chroma 向量。

    **added / modified**：先删该 ``source_file`` 旧向量，再 upsert 当前 ``output`` 中的切片。

    对齐清单文件名时会把 **Chroma 集合里已有的 ``source_file`` 元数据** 并入候选池，
    以便在 ``build --incremental`` 已从 manifest 移除下线文档后，仍能模糊对齐并删除对应向量。
    """
    config = ChromaConfig.from_env()
    store = ChromaStore(config)
    chroma_name_pool = store.distinct_metadata_source_files()
    changes, _ = _load_resolved_changes(paths, list_file, extra_name_pool=chroma_name_pool)
    plan = plan_incremental_build(changes, project_root=paths.project_root)
    chroma_vectors_deleted = 0

    if plan.chroma_delete_files:
        chroma_vectors_deleted += store.delete_by_source_files(plan.chroma_delete_files)

    refresh_sources = plan.chroma_source_files
    if refresh_sources:
        chroma_vectors_deleted += store.delete_by_source_files(refresh_sources)

    chunk_paths = _chunks_paths(paths, plan.build_upsert_files) if plan.build_upsert_files else []
    chunks_added = 0
    total_chunks = 0
    if chunk_paths:
        chunks_added, total_chunks = upsert_chunks_files(store, chunk_paths)

    return IncrementalChromaOutput(
        added=changes.added,
        deleted=changes.deleted,
        modified=changes.modified,
        chroma_vectors_deleted=chroma_vectors_deleted,
        chunks_added=chunks_added,
        total_chunks_in_files=total_chunks,
        collection_name=config.collection_name,
    )
