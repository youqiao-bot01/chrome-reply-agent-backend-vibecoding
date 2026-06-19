"""增量清单：店铺 supplement / playbook 归并为同一 build 目标。"""

from __future__ import annotations

from dataclasses import dataclass

from hubstudio_python.kb.service.ingest.incremental_targets import IncrementalChanges
from hubstudio_python.kb.service.pipelines.authoritative_sources import (
    ShopAuthoritativeConfig,
    load_authoritative_manifest,
    resolve_structure_supplements,
)


@dataclass(frozen=True)
class IncrementalBuildPlan:
    """用户 yaml 原文 + 流水线实际执行的 build / structure 计划。"""

    user: IncrementalChanges
    build_upsert_files: list[str]
    chroma_source_files: list[str]
    chroma_delete_files: list[str]
    structure_playbooks: list[tuple[str, str, list[str]]]


def _norm(path: str) -> str:
    return str(path or "").strip().replace("\\", "/")


def _shop_config_for_path(
    path: str,
    manifest: dict[str, ShopAuthoritativeConfig],
) -> ShopAuthoritativeConfig | None:
    p = _norm(path)
    if not p:
        return None
    for cfg in manifest.values():
        if cfg.playbook and _norm(cfg.playbook) == p:
            return cfg
        for sup in cfg.supplements:
            if _norm(sup.file) == p:
                return cfg
    return None


def _build_target_for_path(
    path: str,
    manifest: dict[str, ShopAuthoritativeConfig],
) -> str:
    """supplement → 所属店 playbook；playbook / 其它文件保持原路径。"""
    cfg = _shop_config_for_path(path, manifest)
    if cfg and cfg.playbook:
        return _norm(cfg.playbook)
    return _norm(path)


def plan_incremental_build(
    changes: IncrementalChanges,
    *,
    project_root,
) -> IncrementalBuildPlan:
    manifest = load_authoritative_manifest(project_root=project_root)

    build_upsert: list[str] = []
    structure_shops: list[str] = []

    for raw in changes.upsert_files:
        p = _norm(raw)
        if not p:
            continue
        target = _build_target_for_path(p, manifest)
        if target not in build_upsert:
            build_upsert.append(target)
        cfg = _shop_config_for_path(p, manifest)
        if cfg and cfg.playbook and cfg.shop not in structure_shops:
            structure_shops.append(cfg.shop)

    structure_playbooks: list[tuple[str, str, list[str]]] = []
    for shop in structure_shops:
        cfg = manifest.get(shop)
        if not cfg or not cfg.playbook:
            continue
        supplements = resolve_structure_supplements(shop, project_root=project_root)
        structure_playbooks.append((shop, _norm(cfg.playbook), supplements))

    chroma_upsert = [_build_target_for_path(p, manifest) for p in changes.upsert_files]
    chroma_delete = [_build_target_for_path(p, manifest) for p in changes.deleted]

    def _dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item and item not in seen:
                seen.add(item)
                out.append(item)
        return out

    return IncrementalBuildPlan(
        user=changes,
        build_upsert_files=_dedupe(build_upsert),
        chroma_source_files=_dedupe(chroma_upsert),
        chroma_delete_files=_dedupe(chroma_delete),
        structure_playbooks=structure_playbooks,
    )
