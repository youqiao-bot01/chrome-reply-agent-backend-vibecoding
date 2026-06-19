"""
读取 ``rag_data/kb/incremental_update.yaml``：支持文档 **新增 / 删除 / 修改** 三类变动。
"""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from hubstudio_python.config import PathsConfig


@dataclass(frozen=True)
class IncrementalChanges:
    """
    增量变动清单（对应 ``incremental_update.yaml`` 三个列表）。

    - ``added``：新纳入的 docx，需 build + embed + 写入 Chroma
    - ``deleted``：下线的 docx，清 Chroma / output / manifest
    - ``modified``：内容已改的 docx，先删 Chroma 旧向量再重建
    """

    added: list[str]      # 新增文件名列表
    deleted: list[str]    # 删除文件名列表（kb/input/ 可已无此文件）
    modified: list[str]   # 修改文件名列表（kb/input/ 中须仍存在）

    @property
    def upsert_files(self) -> list[str]:
        """需要重新切片并写入向量库的文件（added + modified，顺序：先 added 后 modified）。"""
        return [*self.added, *self.modified]

    def is_empty(self) -> bool:
        return not self.added and not self.deleted and not self.modified


def default_incremental_update_path(rag_root: Path) -> Path:
    return rag_root / "kb" / "incremental_update.yaml"


def _normalize_file_list(raw: object | None) -> list[str]:
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for item in raw:
        name = str(item).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def load_incremental_changes(path: Path) -> IncrementalChanges:
    """解析 YAML 中的 ``added`` / ``deleted`` / ``modified``。"""
    if not path.is_file():
        raise FileNotFoundError(
            f"Incremental update list not found: {path}. "
            "Create rag_data/kb/incremental_update.yaml with added/deleted/modified."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid incremental update yaml (expected mapping): {path}")

    if raw.get("files") is not None:
        raise ValueError(
            f"Deprecated key 'files' in {path}. Use 'added', 'deleted', or 'modified' instead."
        )

    added = _normalize_file_list(raw.get("added"))
    deleted = _normalize_file_list(raw.get("deleted"))
    modified = _normalize_file_list(raw.get("modified"))

    changes = IncrementalChanges(added=added, deleted=deleted, modified=modified)
    if changes.is_empty():
        raise ValueError(
            f"No changes in {path}. Set at least one of: added, deleted, modified."
        )
    _validate_no_overlap(changes)
    return changes


def _catalog_name_pool(
    paths: PathsConfig,
    extra: Sequence[str] | None = None,
) -> list[str]:
    """``manifest`` 中的 ``source_file`` + ``kb/input/`` 下实际文件名，去重保序。

    ``extra``：额外候选名（例如 Chroma 里已有向量的 ``source_file`` 元数据），
    用于 ``build --incremental`` 已清 manifest 后 ``chroma --incremental`` 仍能按模糊名对齐已删文档。
    """
    from hubstudio_python.kb.service.ingest.manifest import load_manifest

    seen: set[str] = set()
    out: list[str] = []
    manifest = load_manifest(paths.manifest_file)
    for k in manifest.keys():
        s = str(k).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    if paths.input_dir.is_dir():
        from hubstudio_python.kb.service.ingest.scanner import _source_file_key

        for p in sorted(paths.input_dir.rglob("*")):
            if not p.is_file() or p.name.startswith("~$"):
                continue
            key = _source_file_key(p, paths.input_dir)
            if key not in seen:
                seen.add(key)
                out.append(key)
    if extra:
        for raw in extra:
            s = str(raw).strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return out


def _normalize_declared_typo(declared: str, pool: list[str]) -> str | None:
    """常见笔误：``toolant/add1.txt.html`` → ``toolant/add1.txt``。"""
    if declared in pool:
        return declared
    if declared.endswith(".txt.html"):
        alt = declared[: -len(".html")]
        if alt in pool:
            return alt
    if declared.endswith(".html.html"):
        alt = declared[: -len(".html")]
        if alt in pool:
            return alt
    return None


def _resolve_declared_source_file(declared: str, pool: list[str]) -> tuple[str, str | None]:
    """
    清单里的文件名与 ``manifest`` / ``kb/input/`` 完全一致时直接返回；
    否则在 ``pool`` 中找唯一高相似度候选（常见：括号与 ``.docx`` 之间多空格）。
    """
    if not declared or not pool:
        return declared, None
    if declared in pool:
        return declared, None
    typo_fixed = _normalize_declared_typo(declared, pool)
    if typo_fixed:
        return typo_fixed, (
            f"[incremental] path typo corrected: {declared!r} -> {typo_fixed!r}"
        )
    threshold = 0.96
    scored: list[tuple[float, str]] = [
        (difflib.SequenceMatcher(None, declared, cand).ratio(), cand) for cand in pool
    ]
    scored.sort(key=lambda x: -x[0])
    top = [c for r, c in scored if r >= threshold]
    if len(top) == 1:
        r0 = scored[0][0]
        return top[0], (
            f"[incremental] name aligned to catalog: {declared!r} -> {top[0]!r} "
            f"(similarity {r0:.3f}); prefer exact filename in incremental_update.yaml."
        )
    return declared, None


def align_incremental_changes_to_catalog(
    changes: IncrementalChanges,
    paths: PathsConfig,
    *,
    extra_pool: Sequence[str] | None = None,
) -> tuple[IncrementalChanges, list[str]]:
    """
    将 ``added`` / ``deleted`` / ``modified`` 中的声明文件名对齐到目录/manifest 中的规范名。

    ``extra_pool``：并入候选名（典型：``chroma --incremental`` 前从向量库读取的 ``source_file``），
    避免清单里写的下线文件名与库里元数据略有出入时删不干净。

    :return: ``(aligned_changes, stderr_warning_lines)``
    """
    pool = _catalog_name_pool(paths, extra=extra_pool)
    warnings: list[str] = []

    def align_one(name: str) -> str:
        resolved, warn = _resolve_declared_source_file(name, pool)
        if warn:
            warnings.append(warn)
        return resolved

    aligned = IncrementalChanges(
        added=[align_one(n) for n in changes.added],
        deleted=[align_one(n) for n in changes.deleted],
        modified=[align_one(n) for n in changes.modified],
    )
    _validate_no_overlap(aligned)
    return aligned, warnings


def _validate_no_overlap(changes: IncrementalChanges) -> None:
    sets = {
        "added": set(changes.added),
        "deleted": set(changes.deleted),
        "modified": set(changes.modified),
    }
    for a_key, a_set in sets.items():
        for b_key, b_set in sets.items():
            if a_key >= b_key:
                continue
            overlap = a_set & b_set
            if overlap:
                raise ValueError(
                    f"File(s) appear in both '{a_key}' and '{b_key}': {sorted(overlap)}"
                )


def resolve_documents(doc_dir: Path, source_files: list[str]):
    """按文件名在 ``doc_dir`` 中定位 ``SourceDocument``（支持 ``.docx`` / ``.xlsx``）；缺失则抛错。"""
    from hubstudio_python.kb.service.ingest.scanner import (
        scan_documents,
        scan_gen_policy_documents,
        scan_html_documents,
        scan_spreadsheets,
    )

    by_name: dict[str, object] = {}
    for doc in scan_documents(doc_dir):
        by_name[doc.source_file] = doc
    for doc in scan_html_documents(doc_dir):
        by_name[doc.source_file] = doc
    for doc in scan_spreadsheets(doc_dir, only_names=None):
        by_name[doc.source_file] = doc
    for doc in scan_gen_policy_documents(doc_dir):
        by_name[doc.source_file] = doc
    missing = [name for name in source_files if name not in by_name]
    if missing:
        raise FileNotFoundError(
            f"Source file(s) not found under {doc_dir}: {missing}. "
            "Expected .docx, .html, .xlsx, or gen.txt under rag_data/kb/input/. "
            "For 'deleted' entries the file may already be removed from kb/input/."
        )
    return [by_name[name] for name in source_files]
