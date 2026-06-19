"""权威文档 manifest：指定哪些 supplement 必须确定性写入知识库。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hubstudio_python.models.rag_layout import kb_authoritative_sources_file


@dataclass(frozen=True)
class SupplementSpec:
    file: str
    priority: str = "reference"
    materializer: str | None = None
    note: str = ""


@dataclass(frozen=True)
class ShopAuthoritativeConfig:
    shop: str
    playbook: str | None
    supplements: tuple[SupplementSpec, ...]


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml

    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def load_authoritative_manifest(*, project_root: Path | None = None) -> dict[str, ShopAuthoritativeConfig]:
    path = kb_authoritative_sources_file(base=project_root)
    raw = _load_yaml(path)
    shops_raw = raw.get("shops")
    if not isinstance(shops_raw, dict):
        return {}

    out: dict[str, ShopAuthoritativeConfig] = {}
    for shop_key, cfg in shops_raw.items():
        if not isinstance(cfg, dict):
            continue
        shop = str(shop_key).strip()
        specs: list[SupplementSpec] = []
        for item in cfg.get("supplements") or []:
            if not isinstance(item, dict):
                continue
            rel = str(item.get("file") or "").strip().replace("\\", "/")
            if not rel:
                continue
            specs.append(
                SupplementSpec(
                    file=rel,
                    priority=str(item.get("priority") or "reference").strip().lower(),
                    materializer=str(item.get("materializer") or "").strip() or None,
                    note=str(item.get("note") or "").strip(),
                )
            )
        playbook = str(cfg.get("playbook") or "").strip().replace("\\", "/") or None
        out[shop] = ShopAuthoritativeConfig(
            shop=shop,
            playbook=playbook,
            supplements=tuple(specs),
        )
    return out


def resolve_structure_supplements(
    shop: str,
    *,
    project_root: Path | None = None,
    cli_overrides: list[str] | None = None,
) -> list[str]:
    """
    structure-playbook 应加载的 supplement 相对路径（kb/input/ 下）。

    CLI ``--supplement`` 显式传入时优先；否则读 manifest；再否则 toolant 默认 add1。
    """
    if cli_overrides:
        return [s.replace("\\", "/").strip() for s in cli_overrides if str(s).strip()]

    manifest = load_authoritative_manifest(project_root=project_root)
    cfg = manifest.get(shop) or manifest.get(shop.lower())
    if cfg and cfg.supplements:
        return [s.file for s in cfg.supplements]

    if shop.lower() == "toolant":
        return ["toolant/add1.txt"]
    return []


def authoritative_materializers(
    shop: str,
    *,
    project_root: Path | None = None,
) -> list[SupplementSpec]:
    manifest = load_authoritative_manifest(project_root=project_root)
    cfg = manifest.get(shop) or manifest.get(shop.lower())
    if not cfg:
        return []
    return [s for s in cfg.supplements if s.priority == "authoritative" and s.materializer]


def format_authoritative_supplements_for_prompt(
    shop: str,
    *,
    project_root: Path | None = None,
) -> str:
    """供 structure-playbook Step 2 附带的强制约束（authoritative 条目）。"""
    specs = authoritative_materializers(shop, project_root=project_root)
    if not specs:
        return ""

    lines = [
        "## Authoritative supplements (MANDATORY — override LLM on same topic)",
        "",
        "The following files are **binding operational rules**. "
        "You MUST emit matching records; deterministic post-processing will replace incomplete LLM output.",
        "",
    ]
    for spec in specs:
        lines.append(f"- `{spec.file}` → materializer `{spec.materializer}`")
        if spec.note:
            lines.append(f"  - {spec.note}")
    lines.extend(
        [
            "",
            "**toolant_add1 全文章节（必须完整落地）：**",
            "- §1 WhatsApp 群链接：A/B 档 vs C 档（`creator_type`，`Add1-1-*`）",
            "- §2 加窗挂链：5% / 1% 佣金（`Add1-2-*`，`creator_type` 分流）",
            "- §3 AI 视频：首条$5、后续$1（`Add1-3-*`，`creator_type: C-level`）",
            "- §4 WA 反馈：joined/not_found/no_wa/shared × 进度 × 达人类型（`Add1-4-*`）",
            "",
        ]
    )
    return "\n".join(lines)
