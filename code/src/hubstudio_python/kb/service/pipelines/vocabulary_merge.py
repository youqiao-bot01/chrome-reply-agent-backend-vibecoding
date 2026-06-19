"""
合并 ``vocabulary/generic.yaml``（通用）与各店 ``vocabulary/<shop>.yaml``。

- **通用** ``fields``：字段定义 + 跨店枚举
- **店铺** ``_meta.shop_extensions.<shop>.condition_fields``：筛选条件取值
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from hubstudio_python.models.schema_layout import (
    discover_vocabulary_shop_layers,
    restore_candidates_path,
    restore_shop_path,
    vocabulary_generic_path,
    vocabulary_merged_path,
    vocabulary_shop_path,
)
from hubstudio_python.models.vocabulary_constants import (
    SHOP_SCOPED_FIELD_NAMES,
    generic_values_for_field,
    strip_generic_from_field_spec,
)


def _union_list(base: list[Any], extra: list[Any]) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for item in [*base, *extra]:
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _deep_merge_dict(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, val in overlay.items():
        if key in out and isinstance(out[key], dict) and isinstance(val, dict):
            out[key] = _deep_merge_dict(out[key], val)
        else:
            out[key] = val
    return out


def _merge_field_spec_into(target: dict[str, Any], overlay: dict[str, Any]) -> None:
    if "values" in overlay and isinstance(overlay["values"], list):
        target["values"] = _union_list(list(target.get("values") or []), overlay["values"])
    if "aliases" in overlay and isinstance(overlay["aliases"], dict):
        aliases = dict(target.get("aliases") or {})
        for canon, alias_list in overlay["aliases"].items():
            aliases[str(canon)] = _union_list(list(aliases.get(canon) or []), list(alias_list or []))
        target["aliases"] = aliases
    if "groups" in overlay and isinstance(overlay["groups"], dict):
        groups = dict(target.get("groups") or {})
        for gname, gvals in overlay["groups"].items():
            if isinstance(gvals, list):
                groups[str(gname)] = _union_list(list(groups.get(gname) or []), gvals)
        target["groups"] = groups
    for key in ("note", "label", "type", "excel_header"):
        if overlay.get(key):
            target[key] = overlay[key]


def merge_shop_overlay(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """将单店 layer 合并进 base（原地修改并返回 base）。"""
    shop = str(overlay.get("shop") or "").strip()
    if not shop:
        stem = overlay.get("_source_stem")
        if stem:
            shop = str(stem).strip()
    if not shop:
        return base

    meta = base.setdefault("_meta", {})
    shop_extensions = meta.setdefault("shop_extensions", {})

    overlay_meta = overlay.get("_meta")
    if isinstance(overlay_meta, dict) and overlay_meta:
        shop_extensions[shop] = _deep_merge_dict(dict(shop_extensions.get(shop) or {}), overlay_meta)

    overlay_fields = overlay.get("fields")
    if isinstance(overlay_fields, dict):
        fields = base.setdefault("fields", {})
        shop_ext = shop_extensions.setdefault(shop, {})
        condition_fields = shop_ext.setdefault("condition_fields", {})
        for fname, fspec in overlay_fields.items():
            if not isinstance(fspec, dict):
                continue
            fname = str(fname)
            if fname in SHOP_SCOPED_FIELD_NAMES:
                generic_vals = generic_values_for_field(fname, fields)
                fspec = strip_generic_from_field_spec(fspec, generic_vals)
                target = condition_fields.setdefault(fname, {})
                if isinstance(target, dict):
                    _merge_field_spec_into(target, fspec)
                if fname == "restore_slots":
                    slot_vals = fspec.get("values")
                    if isinstance(slot_vals, list) and slot_vals:
                        shop_ext["restore_slots"] = _union_list(
                            list(shop_ext.get("restore_slots") or []),
                            [str(v) for v in slot_vals],
                        )
                    if fspec.get("note"):
                        shop_ext["restore_slots_note"] = str(fspec["note"])
                continue
            target = fields.setdefault(fname, {})
            if isinstance(target, dict):
                _merge_field_spec_into(target, fspec)

    return base


def shop_overlay_path(shop: str, *, vocab_dir: Path | None = None) -> Path:
    return vocabulary_shop_path(shop, base=vocab_dir.parent if vocab_dir else None)


def shop_condition_field_spec(
    shop: str,
    field_name: str,
    *,
    vocab_data: dict[str, Any] | None = None,
    vocab_dir: Path | None = None,
) -> dict[str, Any]:
    """读取单店 condition_fields[field_name]。"""
    if vocab_data is None:
        from hubstudio_python.models.knowledge_chunk_vocabulary import default_vocabulary_path

        path = default_vocabulary_path()
        vocab_data = load_yaml_dict(path) if path.is_file() else {}

    ext = ((vocab_data.get("_meta") or {}).get("shop_extensions") or {}).get(shop) or {}
    if isinstance(ext, dict):
        cf = ext.get("condition_fields") or {}
        spec = cf.get(field_name) if isinstance(cf, dict) else None
        if isinstance(spec, dict):
            return dict(spec)

    overlay_path = vocabulary_shop_path(shop)
    if overlay_path.is_file():
        overlay = load_yaml_dict(overlay_path)
        spec = (overlay.get("fields") or {}).get(field_name)
        if isinstance(spec, dict):
            return dict(spec)
    return {}


def shop_names_from_vocab(vocab_data: dict[str, Any] | None = None) -> list[str]:
    if vocab_data is None:
        from hubstudio_python.models.knowledge_chunk_vocabulary import default_vocabulary_path

        path = default_vocabulary_path()
        vocab_data = load_yaml_dict(path) if path.is_file() else {}
    ext = (vocab_data.get("_meta") or {}).get("shop_extensions") or {}
    if isinstance(ext, dict):
        return sorted(str(k) for k in ext)
    return []


def restore_slot_ids_for_shop(shop: str) -> list[str]:
    """仅返回该店声明的 restore_slots。"""
    ids: set[str] = set()

    from hubstudio_python.models.knowledge_chunk_vocabulary import default_vocabulary_path

    vocab_path = default_vocabulary_path()
    if vocab_path.is_file():
        data = load_yaml_dict(vocab_path)
        ext = ((data.get("_meta") or {}).get("shop_extensions") or {}).get(shop) or {}
        if isinstance(ext, dict):
            for v in ext.get("restore_slots") or []:
                ids.add(str(v))
            rs = (ext.get("condition_fields") or {}).get("restore_slots") or {}
            if isinstance(rs, dict):
                for v in rs.get("values") or []:
                    ids.add(str(v))

    overlay_path = vocabulary_shop_path(shop)
    if overlay_path.is_file():
        overlay = load_yaml_dict(overlay_path)
        rs = (overlay.get("fields") or {}).get("restore_slots") or {}
        if isinstance(rs, dict):
            for v in rs.get("values") or []:
                ids.add(str(v))

    restore_path = restore_shop_path(shop)
    if restore_path.is_file():
        data = yaml.safe_load(restore_path.read_text(encoding="utf-8")) or {}
        slots = data.get("slots")
        if isinstance(slots, dict):
            ids.update(str(k) for k in slots)

    return sorted(ids)


def read_yaml_header(path: Path) -> str:
    """保留文件开头连续 ``#`` 注释行（合并写回时勿丢注释）。"""
    if not path.is_file():
        return ""
    header_lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            header_lines.append(line)
            continue
        break
    if not header_lines:
        return ""
    return "\n".join(header_lines) + "\n"


def load_yaml_dict(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def merge_vocabulary_from_layers(*, vocab_dir: Path | None = None) -> tuple[str, dict[str, Any]]:
    """读取 ``vocabulary/generic.yaml`` + 各店 ``vocabulary/<shop>.yaml``，返回 (header, merged)。"""
    root = vocabulary_generic_path().parent if vocab_dir is None else vocab_dir
    base_path = root / "generic.yaml"
    if not base_path.is_file():
        raise FileNotFoundError(f"缺少通用词汇表: {base_path}")

    header = read_yaml_header(base_path)
    merged = load_yaml_dict(base_path)

    shop_layers = (
        discover_vocabulary_shop_layers(base=root.parent)
        if vocab_dir is None
        else sorted(p for p in root.glob("*.yaml") if p.name not in {"generic.yaml", "merged.yaml"})
    )
    for layer_path in shop_layers:
        overlay = load_yaml_dict(layer_path)
        if not overlay.get("shop"):
            overlay = dict(overlay)
            overlay["shop"] = layer_path.stem
        merge_shop_overlay(merged, overlay)

    return header, merged


# 兼容旧名
merge_vocabulary_from_seed = merge_vocabulary_from_layers


def write_merged_vocabulary(path: Path | None = None, *, header: str, data: dict[str, Any]) -> None:
    out = path or vocabulary_merged_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=120,
    )
    merged_banner = """# ════════════════════════════════════════════════════════════
# 运行期合并文件 merged.yaml（勿手改）
# ════════════════════════════════════════════════════════════
# 含义：generic.yaml（通用/插件契约）+ 全部 vocabulary/<店铺>.yaml 合并后的「全集」。
# 用途：Python / 结构化 / Chroma 运行时只读此文件做枚举收敛与校验。
# 维护：改 generic.yaml 或 toolant.yaml / Linknlatch.yaml … 后执行：
#       uv run python main.py vocabulary --init --force
# 注意：只有一个「通用」源文件 generic.yaml；不存在 GEN.yaml（GEN 是误建的假店铺目录，已废弃）。
# ════════════════════════════════════════════════════════════

"""
    out.write_text(f"{merged_banner}{body}", encoding="utf-8")
