"""
安装 ``code/assets/schema/``（按职责分目录，每目录 generic + 各店）::

- ``vocabulary/generic.yaml`` + ``vocabulary/<shop>.yaml`` → ``vocabulary/merged.yaml``
- ``intent/generic.yaml`` + ``intent/<shop>.yaml``
- ``restore/generic.yaml`` + ``restore/<shop>.yaml``（定值来自 doc 抽取）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from hubstudio_python.models.intent_classification import shop_intent_classification_path
from hubstudio_python.models.knowledge_chunk_vocabulary import default_vocabulary_path
from hubstudio_python.models.schema_layout import (
    discover_vocabulary_shop_layers,
    restore_shop_path,
    schema_dir,
    vocabulary_generic_path,
)
from hubstudio_python.models.shop_restore_values import shop_restore_values_path
from hubstudio_python.kb.service.pipelines.vocabulary_merge import merge_vocabulary_from_layers, write_merged_vocabulary


def _shop_restore_values_from_doc(shop: str) -> dict:
    """从 ``kb/input/<shop>/`` 抽取 restore 定值。"""
    from hubstudio_python.kb.service.pipelines.extract_shop_restore_values import run_restore_extract_pipeline

    result = run_restore_extract_pipeline(shop, write_candidates=True)
    path = shop_restore_values_path(shop)
    if path.is_file():
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        "shop": shop,
        "description": result.yaml_path,
        "slots": result.slots,
    }


def _discover_vocabulary_shops() -> list[str]:
    return [p.stem for p in discover_vocabulary_shop_layers()]


def _discover_all_shops() -> list[str]:
    from hubstudio_python.kb.service.pipelines.extract_shop_vocabulary import discover_doc_shops

    return sorted(set(_discover_vocabulary_shops()) | set(discover_doc_shops()))


@dataclass
class BootstrapSchemaResult:
    files_written: list[str] = field(default_factory=list)
    intent_generated: list[str] = field(default_factory=list)


def bootstrap_schema(
    *,
    force: bool = False,
    generate_intent: bool = True,
    force_intent: bool = False,
) -> BootstrapSchemaResult:
    """合并 vocabulary、抽取 restore、补全 intent/<shop>.yaml。"""
    from hubstudio_python.kb.service.pipelines.extract_shop_vocabulary import ensure_shop_intent_classification

    root = schema_dir()
    root.mkdir(parents=True, exist_ok=True)
    if not vocabulary_generic_path().is_file():
        raise FileNotFoundError(f"缺少通用词汇表: {vocabulary_generic_path()}")

    written: list[str] = []
    intent_shops: list[str] = []

    vocab_path = default_vocabulary_path()
    if force or not vocab_path.is_file():
        header, merged = merge_vocabulary_from_layers()
        write_merged_vocabulary(vocab_path, header=header, data=merged)
        written.append(str(vocab_path.resolve()))

    for shop in _discover_all_shops():
        restore_path = restore_shop_path(shop)
        if force or not restore_path.is_file():
            restore_path.parent.mkdir(parents=True, exist_ok=True)
            restore_path.write_text(
                yaml.dump(
                    _shop_restore_values_from_doc(shop),
                    allow_unicode=True,
                    sort_keys=False,
                    default_flow_style=False,
                    width=120,
                ),
                encoding="utf-8",
            )
            written.append(str(restore_path.resolve()))

        if generate_intent and (force_intent or not shop_intent_classification_path(shop).is_file()):
            apply_result = ensure_shop_intent_classification(shop, force=force_intent)
            if apply_result and apply_result.intent_yaml_path:
                written.append(apply_result.intent_yaml_path)
                intent_shops.append(shop)

    return BootstrapSchemaResult(
        files_written=written,
        intent_generated=intent_shops,
    )
