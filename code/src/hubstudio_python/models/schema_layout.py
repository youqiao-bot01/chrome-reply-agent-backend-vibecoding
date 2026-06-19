"""
``code/assets/schema/`` 目录布局（按职责分目录，每目录含 generic + 各店）::

    vocabulary/   generic.yaml + <shop>.yaml  → merged.yaml（运行期）
    intent/       generic.yaml + <shop>.yaml
    restore/      generic.yaml + <shop>.yaml + candidates/<shop>.json
"""

from __future__ import annotations

from pathlib import Path

from hubstudio_python.models.rag_layout import assets_root

_RESERVED_VOCAB_FILES = frozenset({"generic.yaml", "merged.yaml"})


def schema_dir(*, base: Path | None = None) -> Path:
    return assets_root(base=base) / "schema"


def vocabulary_dir(*, base: Path | None = None) -> Path:
    return schema_dir(base=base) / "vocabulary"


def vocabulary_generic_path(*, base: Path | None = None) -> Path:
    return vocabulary_dir(base=base) / "generic.yaml"


def vocabulary_shop_path(shop: str, *, base: Path | None = None) -> Path:
    return vocabulary_dir(base=base) / f"{shop}.yaml"


def vocabulary_merged_path(*, base: Path | None = None) -> Path:
    return vocabulary_dir(base=base) / "merged.yaml"


def discover_vocabulary_shop_layers(*, base: Path | None = None) -> list[Path]:
    root = vocabulary_dir(base=base)
    if not root.is_dir():
        return []
    return sorted(
        p
        for p in root.glob("*.yaml")
        if p.is_file() and p.name not in _RESERVED_VOCAB_FILES
    )


def intent_dir(*, base: Path | None = None) -> Path:
    return schema_dir(base=base) / "intent"


def intent_generic_path(*, base: Path | None = None) -> Path:
    return intent_dir(base=base) / "generic.yaml"


def intent_shop_path(shop: str, *, base: Path | None = None) -> Path:
    return intent_dir(base=base) / f"{shop}.yaml"


def discover_intent_shops(*, base: Path | None = None) -> list[str]:
    root = intent_dir(base=base)
    if not root.is_dir():
        return []
    out: list[str] = []
    for p in sorted(root.glob("*.yaml")):
        if p.name == "generic.yaml":
            continue
        out.append(p.stem)
    return out


def restore_dir(*, base: Path | None = None) -> Path:
    return schema_dir(base=base) / "restore"


def restore_generic_path(*, base: Path | None = None) -> Path:
    return restore_dir(base=base) / "generic.yaml"


def restore_shop_path(shop: str, *, base: Path | None = None) -> Path:
    return restore_dir(base=base) / f"{shop}.yaml"


def restore_candidates_path(shop: str, *, base: Path | None = None) -> Path:
    return restore_dir(base=base) / "candidates" / f"{shop}.json"


def discover_restore_shops(*, base: Path | None = None) -> list[str]:
    root = restore_dir(base=base)
    if not root.is_dir():
        return []
    out: list[str] = []
    for p in sorted(root.glob("*.yaml")):
        if p.name == "generic.yaml":
            continue
        out.append(p.stem)
    return out
