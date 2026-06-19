"""
DeepSeek 文档解释器：从 Playbook HTML + 补充说明抽取店铺词汇表、意图分类、条件关系。

产出写入 ``schema/vocabulary/<shop>.yaml`` 并合并到 ``vocabulary/merged.yaml``；
``intent/<shop>.yaml`` 由模型从文档生成（通用规则见 ``intent/generic.yaml``）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from hubstudio_python.kb.service.chunking.zh_translate import (
    ChunkTranslateConfig,
    _post_chat_with_retry,
    _strip_markdown_json_fence,
)
from hubstudio_python.config import _project_root, load_project_yaml
from hubstudio_python.models.intent_classification import (
    reload_intent_classifier,
    shop_intent_classification_path,
)
from hubstudio_python.models.knowledge_chunk_vocabulary import (
    default_vocabulary_path,
    reload_vocabulary,
)
from hubstudio_python.models.schema_layout import (
    restore_shop_path,
    vocabulary_generic_path,
    vocabulary_shop_path,
)
from hubstudio_python.kb.service.pipelines.vocabulary_merge import (
    load_yaml_dict,
    merge_vocabulary_from_layers,
    read_yaml_header,
    write_merged_vocabulary,
)

logger = logging.getLogger(__name__)

_ANALYZE_SYSTEM = """You are a **document interpreter** for creator-ops playbooks (TikTok affiliate / BD scripts).

Read the FULL document (HTML playbook + supplement notes). Extract machine-readable schema artifacts — **no manual curation assumed**.

## Output (ONLY one JSON object, no markdown)

```json
{
  "shop": "<slug from doc path, e.g. toolant>",
  "document_summary_zh": "<2-4 sentences: what this shop playbook covers>",
  "vocabulary_overlay": {
    "shop": "<same slug>",
    "_meta": {
      "negotiation": {
        "scope": "<when multi-round price negotiation applies; tier/shop constraints>",
        "rounds": [
          {"round": 0, "node": "A1", "offer": "<human offer label>", "flags": "priority_node:A1,reply_ok"}
        ],
        "notes": ["<policy notes>"]
      },
      "extracted_from": ["<source filenames>"]
    },
    "fields": {
      "intent_category": {"values": ["..."], "aliases": {"canonical": ["alias"]}},
      "restore_slots": {"values": ["snake_case_slot_id"], "note": "..."},
      "other_creator_conditions": {
        "groups": {
          "negotiation_priority": ["priority_node:A1"],
          "negotiation_prev": ["prev_node:A1"],
          "gmv_tiers": ["gmv_gte_1000", "gmv_lt_1000"],
          "creator_reply": ["reply_ok"]
        }
      },
      "creator_progress": {"values": ["已签约", "未发视频"]},
      "creator_type": {"values": ["A-level", "B-level"]},
      "key_information": {"values": ["whatsapp_group"], "aliases": {"whatsapp_group": ["WhatsApp Group Link"]}}
    }
  },
  "constants": [
    {
      "slot_id": "wa_group_gmv_gte_1k",
      "value_kind": "url|percent|money|text",
      "description": "...",
      "gmv_or_condition": "gmv_gte_1000",
      "source_hint": "<quote fragment, no full secrets if long>"
    }
  ],
  "condition_relationships": [
    {
      "name": "A-tier pure commission open",
      "logic": "AND",
      "conditions": {
        "applicable_shops": "toolant",
        "creator_type": "A-level",
        "other_creator_conditions": ["priority_node:A1"]
      },
      "description_zh": "头部档开启纯佣议价第一轮"
    }
  ],
  "intent_classification": {
    "_meta": {
      "version": "1.0",
      "shop": "<slug>",
      "description": "Auto-extracted intent rules for classify → intent_category"
    },
    "intents": [
      {
        "id": "ok",
        "label_zh": "同意/确认",
        "scenario_zh": "达人接受当前方案",
        "tier": "ABC",
        "priority": 50,
        "exact_replies": ["OK", "Sure"],
        "keywords_en": ["sounds good", "let's do it"],
        "keywords_zh": [],
        "examples": ["OK!", "Sure I accept", "Sounds good to me"],
        "typical_co_conditions": ["reply_ok"],
        "not_this_intent": [{"id": "Reject", "when": "明确拒绝"}],
        "notes": []
      }
    ]
  }
}
```

## Rules
1. **Reuse generic vocabulary** where possible (see BASE VOCABULARY below). Only add shop-specific values.
2. **restore_slots**: snake_case IDs for {{placeholders}}; split by GMV tier when doc routes differently (e.g. wa_group_gmv_gte_1k vs wa_group_gmv_lt_1k).
3. **Negotiation**: A1–A4 multi-round haggling ONLY for head tier (A-level) when doc says so; B/C use B1/B2/C1/C2 sample/portal nodes — never price_negotiation_rounds in creator_progress.
4. **other_creator_conditions**: ONLY standard flags (priority_node:*, prev_node:*, gmv_*, reply_*, channel_*). Put GMV thresholds here, not in creator_progress.
5. **intent_classification**: Cover EVERY distinct trigger scenario in the doc. Include **2–5 mock creator DM examples** per intent in ``examples`` (English, TikTok tone). ``exact_replies`` for short triggers (OK, AI, Stop, WA). **Do NOT duplicate generic intents** already in base vocabulary (``GEN``, ``No obvious intention``) — those live in the generic layer only.
6. **constants**: List operational fixed values (URLs, %, $) mapped to restore_slots; real secrets may be abbreviated in source_hint.
7. **key_information**: ONLY tags for items listed in ``constants`` / ``restore_slots`` that have **authoritative fixed values** (URLs, links, rates from supplements like add1.txt). Do NOT add tags for policy prose, CPM formulas, sample forms, or anything without a restore value. Map slot prefixes: ``wa_group_*`` → ``whatsapp_group``, ``affiliate_link_*`` → ``affiliate_link``, etc.
8. **condition_relationships**: Document AND logic across fields for major branches (tier × GMV × negotiation node).
9. Include ``GEN`` and ``No obvious intention`` intents if applicable **only in intent_classification output**, not in ``vocabulary_overlay.fields.intent_category.values``.
10. Reply with **ONLY** the JSON object.

## BASE VOCABULARY (prefer these canonical values)
<<BASE_VOCAB>>
"""


def _schema_dir() -> Path:
    from hubstudio_python.models.schema_layout import schema_dir

    return schema_dir()


def _analysis_config(project_root: Path) -> ChunkTranslateConfig | None:
    yaml_data = load_project_yaml(project_root)
    ai = yaml_data.get("ai")
    if not isinstance(ai, dict):
        return None
    cfg = ChunkTranslateConfig.from_ai_yaml_section(ai)
    if cfg is None:
        return None
    return ChunkTranslateConfig(
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        model=cfg.model,
        api_path=cfg.api_path,
        temperature=min(cfg.temperature, 0.2),
        max_tokens=max(cfg.max_tokens, 16384),
        timeout_seconds=max(cfg.timeout_seconds, 240.0),
        batch_size=1,
        max_retries=cfg.max_retries,
        batch_sleep_seconds=cfg.batch_sleep_seconds,
    )


def _base_vocab_for_prompt() -> str:
    seed_path = vocabulary_generic_path()
    if not seed_path.is_file():
        return "(no base vocabulary generic)"
    data = load_yaml_dict(seed_path)
    fields = data.get("fields") or {}
    lines: list[str] = []
    for fname, spec in fields.items():
        if not isinstance(spec, dict):
            continue
        vals = spec.get("values")
        if isinstance(vals, list) and vals:
            preview = ", ".join(str(v) for v in vals[:12])
            if len(vals) > 12:
                preview += ", …"
            lines.append(f"- **{fname}**: {preview}")
        groups = spec.get("groups")
        if isinstance(groups, dict):
            for gname, gvals in groups.items():
                if isinstance(gvals, list):
                    lines.append(f"  - groups.{gname}: {', '.join(str(v) for v in gvals[:8])}")
    return "\n".join(lines) if lines else "(empty)"


def _parse_analysis_json(raw: str) -> dict[str, Any]:
    text = _strip_markdown_json_fence(raw)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("document analysis must be a JSON object")
    return data


@dataclass(frozen=True)
class PlaybookAnalysisResult:
    shop: str
    analysis_path: str
    document_chars: int
    intent_count: int
    restore_slot_count: int
    condition_relationship_count: int


@dataclass
class ApplyPlaybookAnalysisResult:
    shop: str
    overlay_path: str = ""
    vocabulary_path: str = ""
    intent_yaml_path: str = ""
    restore_yaml_path: str = ""
    changes: list[str] = field(default_factory=list)


def analyze_playbook_document(
    *,
    document_text: str,
    source_file: str,
    shop: str,
    supplement_files: list[str],
    output_dir: Path,
    project_root: Path,
    output_stem: str | None = None,
) -> tuple[dict[str, Any], PlaybookAnalysisResult]:
    """DeepSeek 读整篇文档，产出 vocabulary overlay + intent_classification + 条件关系。"""
    cfg = _analysis_config(project_root)
    if cfg is None:
        raise ValueError("Missing ai.api_key in config.yaml for DeepSeek document analysis")

    system = _ANALYZE_SYSTEM.replace("<<BASE_VOCAB>>", _base_vocab_for_prompt())
    user = (
        f"Analyze this playbook for shop `{shop}`.\n"
        f"Primary source: {source_file}\n"
        f"Supplements: {', '.join(supplement_files) or '(none)'}\n\n"
        f"{document_text}"
    )
    logger.info("DeepSeek document analysis: shop=%s, %s chars", shop, len(document_text))
    raw = _post_chat_with_retry(cfg, system, user)
    analysis = _parse_analysis_json(raw)

    detected_shop = str(shop).strip() or str(analysis.get("shop") or "").strip()
    analysis["shop"] = detected_shop
    overlay = analysis.get("vocabulary_overlay")
    if isinstance(overlay, dict):
        overlay["shop"] = detected_shop
        meta = overlay.setdefault("_meta", {})
        if isinstance(meta, dict):
            meta.setdefault("extracted_from", [source_file, *supplement_files])
            meta["analyzed_at"] = str(date.today())

    stem = output_stem or "playbook"
    out_name = f"{detected_shop}-{stem}.analysis.json"
    out_path = output_dir / out_name
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    intents = (analysis.get("intent_classification") or {}).get("intents") or []
    slots = ((overlay or {}).get("fields") or {}).get("restore_slots") or {}
    slot_vals = slots.get("values") if isinstance(slots, dict) else []
    rels = analysis.get("condition_relationships") or []

    result = PlaybookAnalysisResult(
        shop=detected_shop,
        analysis_path=str(out_path.resolve()),
        document_chars=len(document_text),
        intent_count=len(intents) if isinstance(intents, list) else 0,
        restore_slot_count=len(slot_vals) if isinstance(slot_vals, list) else 0,
        condition_relationship_count=len(rels) if isinstance(rels, list) else 0,
    )
    return analysis, result


def _xlsx_to_document_text(path: Path, *, max_rows: int = 500) -> str:
    try:
        import openpyxl
    except ImportError:
        return path.read_text(encoding="utf-8", errors="replace")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    lines: list[str] = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i >= max_rows:
            lines.append("... (truncated)")
            break
        cells = [str(c).strip() if c is not None else "" for c in row]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def load_shop_document_text(shop: str, *, doc_root: Path | None = None) -> tuple[list[str], str]:
    """读取 ``kb/input/<shop>/`` 下全部源文件，拼成 DeepSeek 文档解释输入。"""
    from hubstudio_python.kb.service.pipelines.extract_shop_restore_values import (
        _html_to_plain_text,
        discover_shop_source_files,
    )

    from hubstudio_python.models.rag_layout import kb_input_dir, kb_output_dir

    root = doc_root or kb_input_dir()
    paths = discover_shop_source_files(shop, doc_root=root)
    if not paths:
        raise FileNotFoundError(f"kb/input/{shop}/ 下无可读源文件")

    parts: list[str] = []
    sources: list[str] = []
    for path in paths:
        rel = path.relative_to(root).as_posix()
        sources.append(rel)
        suffix = path.suffix.lower()
        if suffix in {".html", ".htm"}:
            text = _html_to_plain_text(path.read_text(encoding="utf-8", errors="replace"))
            parts.append(f"=== PLAYBOOK: {rel} ===\n{text}")
        elif suffix in {".txt", ".md", ".json", ".csv"}:
            parts.append(f"=== SUPPLEMENT: {rel} ===\n{path.read_text(encoding='utf-8', errors='replace')}")
        elif suffix in {".xlsx", ".xls"}:
            parts.append(f"=== EXCEL REFERENCE: {rel} ===\n{_xlsx_to_document_text(path)}")
        else:
            parts.append(f"=== {rel} ===\n{path.read_text(encoding='utf-8', errors='replace')}")
    return sources, "\n\n".join(parts)


def write_intent_classification_for_shop(shop: str, intent_raw: dict[str, Any]) -> str:
    """写入 ``schema/intent/<shop>.yaml``。"""
    meta = intent_raw.setdefault("_meta", {})
    if isinstance(meta, dict):
        meta["shop"] = shop
        meta["updated"] = str(date.today())
        meta.setdefault("source", "deepseek-document-analysis")

    shop_yaml = shop_intent_classification_path(shop)
    shop_yaml.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.dump(intent_raw, allow_unicode=True, sort_keys=False, default_flow_style=False, width=120)
    shop_yaml.write_text(body, encoding="utf-8")

    reload_intent_classifier()
    return str(shop_yaml.resolve())


def run_shop_document_analysis(
    shop: str,
    *,
    project_root: Path | None = None,
    output_dir: Path | None = None,
    force: bool = False,
) -> tuple[dict[str, Any], PlaybookAnalysisResult, ApplyPlaybookAnalysisResult]:
    """
    对任意 ``kb/input/<shop>/``（HTML / Excel / txt）跑 DeepSeek 文档解释：
    vocabulary overlay + intent_classification + restore。
    """
    root = project_root or _project_root()
    from hubstudio_python.models.rag_layout import kb_output_dir

    out_dir = output_dir or kb_output_dir(base=root)
    intent_path = shop_intent_classification_path(shop)
    if intent_path.is_file() and not force:
        analysis_path = out_dir / f"{shop}-document.analysis.json"
        if analysis_path.is_file():
            analysis = load_playbook_analysis(analysis_path)
            apply_result = apply_playbook_analysis(analysis, shop=shop, skip_if_intent_exists=True)
            meta = PlaybookAnalysisResult(
                shop=shop,
                analysis_path=str(analysis_path.resolve()),
                document_chars=0,
                intent_count=len((analysis.get("intent_classification") or {}).get("intents") or []),
                restore_slot_count=0,
                condition_relationship_count=len(analysis.get("condition_relationships") or []),
            )
            return analysis, meta, apply_result

    sources, document_text = load_shop_document_text(shop)
    primary = sources[0] if sources else f"{shop}/"
    analysis, meta = analyze_playbook_document(
        document_text=document_text,
        source_file=primary,
        shop=shop,
        supplement_files=sources[1:],
        output_dir=out_dir,
        project_root=root,
        output_stem="document",
    )
    apply_result = apply_playbook_analysis(analysis, shop=meta.shop, force=True)
    return analysis, meta, apply_result


def discover_doc_shops(doc_root: Path | None = None) -> list[str]:
    from hubstudio_python.models.rag_layout import kb_input_dir, kb_output_dir

    root = doc_root or kb_input_dir()
    if not root.is_dir():
        return []
    # kb/input/GEN/ 等为历史误目录，不是店铺；通用配置见 schema/vocabulary/generic.yaml
    skip = frozenset({"GEN"})
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and d.name not in skip
    )


def ensure_shop_intent_classification(
    shop: str,
    *,
    force: bool = False,
    project_root: Path | None = None,
) -> ApplyPlaybookAnalysisResult | None:
    """若缺少 ``schema/intent/<shop>.yaml`` 则从 doc 生成。"""
    if shop_intent_classification_path(shop).is_file() and not force:
        return None
    try:
        _, _, apply_result = run_shop_document_analysis(shop, project_root=project_root, force=force)
        return apply_result
    except FileNotFoundError:
        logger.warning("跳过 intent 生成：kb/input/%s/ 无源文件", shop)
        return None
    except Exception as exc:
        logger.warning("店铺 %s intent 生成失败: %s", shop, exc)
        return None


def _shop_vocab_header(shop: str) -> str:
    return f"""# 店铺词汇表 — {shop}
# 由 DeepSeek 文档解释器自动生成，请勿手改。
# 重跑: uv run python main.py vocabulary --generate-intent {shop}
# 或 Playbook: uv run python main.py structure-playbook --mode document

"""


def apply_playbook_analysis(
    analysis: dict[str, Any],
    *,
    shop: str | None = None,
    force: bool = True,
    skip_if_intent_exists: bool = False,
) -> ApplyPlaybookAnalysisResult:
    """将分析结果写入 vocabulary/<shop>.yaml、合并 merged.yaml、生成 intent/<shop>.yaml。"""
    detected = str(shop or analysis.get("shop") or "").strip()
    if not detected:
        raise ValueError("analysis missing shop slug")

    result = ApplyPlaybookAnalysisResult(shop=detected)

    overlay = analysis.get("vocabulary_overlay")
    if not isinstance(overlay, dict):
        raise ValueError("analysis missing vocabulary_overlay")

    base_path = vocabulary_generic_path()
    base_fields = load_yaml_dict(base_path).get("fields") or {}
    overlay_fields = overlay.get("fields")
    if isinstance(overlay_fields, dict):
        from hubstudio_python.models.vocabulary_constants import (
            SHOP_SCOPED_FIELD_NAMES,
            generic_values_for_field,
            strip_generic_from_field_spec,
        )

        for fname, fspec in list(overlay_fields.items()):
            if fname not in SHOP_SCOPED_FIELD_NAMES or not isinstance(fspec, dict):
                continue
            generic_vals = generic_values_for_field(str(fname), base_fields)
            overlay_fields[str(fname)] = strip_generic_from_field_spec(fspec, generic_vals)

    overlay_path = vocabulary_shop_path(detected)
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    overlay_body = yaml.dump(
        overlay,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=120,
    )
    overlay_path.write_text(_shop_vocab_header(detected) + overlay_body, encoding="utf-8")
    result.overlay_path = str(overlay_path.resolve())
    result.changes.append(f"vocabulary → {overlay_path.name}")

    if not base_path.is_file():
        raise FileNotFoundError(f"缺少通用词汇表: {base_path}")
    header, merged = merge_vocabulary_from_layers()
    vocab_path = default_vocabulary_path()
    write_merged_vocabulary(vocab_path, header=header, data=merged)
    result.vocabulary_path = str(vocab_path.resolve())
    result.changes.append("merged vocabulary → schema/vocabulary/merged.yaml")
    reload_vocabulary()

    intent_raw = analysis.get("intent_classification")
    intent_exists = shop_intent_classification_path(detected).is_file()
    if (
        isinstance(intent_raw, dict)
        and intent_raw.get("intents")
        and (force or not intent_exists)
        and not (skip_if_intent_exists and intent_exists)
    ):
        yaml_path = write_intent_classification_for_shop(detected, intent_raw)
        result.intent_yaml_path = yaml_path
        result.changes.append(f"intent → intent/{detected}.yaml")

    from hubstudio_python.kb.service.pipelines.extract_shop_restore_values import run_restore_extract_pipeline

    restore_result = run_restore_extract_pipeline(detected, write_candidates=True)
    restore_path = restore_shop_path(detected)
    if restore_path.is_file():
        result.restore_yaml_path = str(restore_path.resolve())
        result.changes.append(f"restore ({len(restore_result.slots)} slots)")

    return result


def load_playbook_analysis(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a JSON object")
    return data


def default_analysis_path(output_dir: Path, shop: str, html_stem: str | None = None) -> Path:
    if html_stem:
        return output_dir / f"{shop}-{html_stem}.analysis.json"
    return output_dir / f"{shop}-playbook.analysis.json"


def ensure_generic_vocabulary_installed(*, force: bool = False) -> None:
    """若 merged 词汇表不存在，先写 generic-only 或全量合并。"""
    vocab_path = default_vocabulary_path()
    if vocab_path.is_file() and not force:
        return
    base_path = vocabulary_generic_path()
    if not base_path.is_file():
        raise FileNotFoundError(f"缺少通用词汇表: {base_path}")
    header, merged = merge_vocabulary_from_layers()
    write_merged_vocabulary(vocab_path, header=header, data=merged)
    reload_vocabulary()
