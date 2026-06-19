"""
用 DeepSeek 将 HTML Playbook 结构化为与 Excel ``*.chunks.json`` 同构的记录。

支持两种模式：
- **document**：整篇 HTML（+ 可选补充文本）一次分析，避免先切块丢上下文；
- **chunks**：逐块结构化（兼容旧流程）。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hubstudio_python.kb.service.chunking.zh_translate import (
    ChunkTranslateConfig,
    _post_chat_with_retry,
    _strip_markdown_json_fence,
)
from hubstudio_python.config import PathsConfig, load_project_yaml
from hubstudio_python.models.chunk import _excel_json_value_absent
from hubstudio_python.models.knowledge_desensitize import (
    desensitize_playbook_record,
    format_restore_slots_for_prompt,
)
from hubstudio_python.models.playbook_query_flags import (
    format_condition_vocabulary_for_prompt,
    normalize_playbook_record_fields,
)

logger = logging.getLogger(__name__)

_SCHEMA_FIELDS = (
    "rule_type",
    "applicable_shops",
    "creator_type",
    "creator_type_and",
    "creator_progress",
    "intent_category",
    "creator_reply_frequency",
    "creator_emotion",
    "ai_acation",
    "other_creator_conditions",
    "key_information",
    "restore_slots",
    "answer_purpose",
    "creator_action_guide",
    "title",
    "content",
    "language",
    "entitle",
    "encontent",
)

_SYSTEM_TEMPLATE = """You structure creator-ops **playbook** chunks into RAG records matching our Excel knowledge schema.

## Rule matching (critical)
Every **non-`GEN` condition field** on a record is a **mandatory** requirement — the creator/session must satisfy **ALL** of them simultaneously (**AND** across fields).
- **`GEN`** = wildcard on that dimension (no constraint).
- **`creator_type`** with multiple values = **OR within that field only** (Excel「或关系」).
- **`creator_type_and`** / **`other_creator_conditions`** = **AND within the field** (every flag/tier required).
- If playbook branches differ by tier (A vs B) with **different replies**, output **separate records** — do not merge incompatible OR branches into one record.

## A-tier price negotiation (toolant head creators only)
- Multi-round haggling **A1 → A2 → A3 → A4** applies **ONLY** to ``applicable_shops: toolant`` + ``creator_type: A-level`` (head tier).
- On those records only: set ``other_creator_conditions`` with matching ``priority_node:Ax`` / ``prev_node:Ax`` (see ``_meta.negotiation`` in vocabulary).
- **Do not** put ``price_negotiation_rounds=N`` in ``creator_progress`` — that column is Excel「达人进度」only.
- **B-level / AI UGC / other tiers**: **no** multi-round price ladder — if they reject an offer, use polite close / alternate path; use ``priority_node:B1``/``B2`` or ``C1``/``C2`` for sample/portal flows, **never** A1–A4 or ``price_negotiation_rounds``.

## Field semantics (same as Excel ``AI话术参考``)
- **title** / **entitle**: **Simulated creator messages** — what the creator might actually type in DM (English, TikTok creator tone). **NOT** internal labels like ``OK / Sure (A-level)`` or ``doc-1``.
  - Write **2–4 alternative phrasings** per record, **one per line** (same intent, different wording).
  - If the playbook gives an exact trigger keyword (``AI``, ``WhatsApp``, ``WA``, ``Stop``), include that verbatim **and** natural paraphrases.
  - Put tier/GMV routing in the structured condition fields below — **never** free-text descriptions.
- **content** / **encontent**: **Our reply script** Linsey sends (English). Expand brief playbook bullets into send-ready messages when needed. Keep ``{{placeholders}}`` unchanged.
- **intent_category**: **minimal** scenario label (**1–4 English words**), e.g. ``ok``, ``Pure Commission``, ``CPM Rate``, ``Sample Request``, ``AI UGC``, ``WhatsApp``, ``Reject``. NOT ``Pure Commission Negotiation``.
- **creator_type**: tier — ``A-level``, ``B-level``, ``S-level``, ``SS-level``, ``AI UGC达人``, ``高GMV达人``, ``纯佣寄样达人``, or ``GEN``.
- **creator_progress**: creator stage if inferable, else ``GEN``.
- **creator_emotion**: ``GEN`` unless specified.
- **creator_reply_frequency**: ``GEN`` unless specified.
- **applicable_shops**: shop slug from source path (e.g. doc subfolder ``toolant`` → ``toolant``). Use ``GEN`` only if truly cross-shop.
- **rule_type**: ``AI参考话术`` (has reply script), ``AI执行动作`` (action only, no script), ``策略说明`` (policy/context only).
- **ai_acation**: system/AI action after trigger (e.g. ``push commission rate and sample form``, ``one_sample_two_videos`` for B-level sample-out → DB ``cooperation_intention``).
- **other_creator_conditions**: JSON **array** of **standard query flags only** (AND logic) — see vocabulary below. **No prose.**
- **key_information**: JSON **array** or string — **only** supplementary items that this record's ``restore_slots`` will restore from ``shop_restore_values.yaml`` (see allowed list in restore section). Use human-readable labels (e.g. ``WhatsApp Group Link``). **Do NOT** list CPM rates, sample forms, commission rates, or policy prose unless a matching restore slot has a configured value. Omit if nothing to deliver.
- **restore_slots**: JSON **array** of placeholder IDs used in ``content`` (e.g. ``wa_group_gmv_gte_1k``) — real values restored at reply time.
- **answer_purpose**: one sentence — what this rule clarifies for the creator.
- **creator_action_guide**: one sentence — what the creator should **do** next (join group / add link / reply AI / fill form).
- **language**: always ``en`` when title/content are English scripts.

## Supplement & desensitization
- Merge **SUPPLEMENT** blocks (e.g. ``add1.txt``) with playbook HTML — same rules, split by GMV/conditions.
- **Never** store real URLs, commission links, or shop secrets in JSON text fields; use ``{{slot_id}}`` placeholders from **restore_slots** vocabulary.
- Each record must state **answer_purpose** + **creator_action_guide** so retrieval knows how to guide the user.

## Vocabulary priority (Excel ``AI话术参考`` + ``knowledge_chunk_vocabulary.yaml``)
1. **First** map every field to an **existing** canonical value from the vocabulary section below.
2. Use **aliases** mentally (e.g. ``执行动作`` → ``AI执行动作``, ``Sample Interest`` → ``Sample Request``).
3. Only create a **new** canonical ``intent_category`` / ``restore_slots`` id when nothing fits — keep it minimal. ``key_information`` must map to document-extracted tags **with** configured restore values only.
4. Unmapped new terms are **auto-appended** to the vocabulary file after structuring for human review.

## Rules
1. One input chunk may yield **0..N** records — split each distinct **trigger → reply** pair.
2. Pure overview/policy with no actionable trigger→reply: output **one** ``策略说明`` record or **[]** if nothing usable.
3. Use ``GEN`` for unknown universal dimensions; **omit** keys whose values would be empty.
4. Preserve numeric thresholds ($5000 GMV, $2 CPM, etc.) as **query flags** in ``other_creator_conditions`` — policy prose belongs in ``encontent`` only; **do not** put undeliverable policy in ``key_information``.
5. **Every** ``AI参考话术`` / ``AI执行动作`` record MUST have realistic multi-line ``title`` (creator sample messages). Reject label-only titles.
6. Reply with **ONLY** a JSON array (no markdown). Each object must include ``source_chunk_id`` (same as input ``id``).

{condition_vocab}

{restore_vocab}

## Reference examples from Excel chunks
{examples}
"""

_FULL_DOC_SYSTEM_TEMPLATE = """You structure a **full creator-ops playbook document** into RAG records matching our Excel knowledge schema.

## Rule matching (critical)
Every **non-`GEN` condition field** on a record is a **mandatory** requirement — the creator/session must satisfy **ALL** of them simultaneously (**AND** across fields).
- **`GEN`** = wildcard on that dimension (no constraint).
- **`creator_type`** with multiple values = **OR within that field only** (Excel「或关系」).
- **`creator_type_and`** / **`other_creator_conditions`** = **AND within the field** (every flag/tier required).
- If playbook branches differ by tier (A vs B) with **different replies**, output **separate records** — do not merge incompatible OR branches into one record.

## A-tier price negotiation (toolant head creators only)
- Multi-round haggling **A1 → A2 → A3 → A4** applies **ONLY** to ``applicable_shops: toolant`` + ``creator_type: A-level`` (head tier).
- On those records only: set ``other_creator_conditions`` with matching ``priority_node:Ax`` / ``prev_node:Ax`` (see ``_meta.negotiation`` in vocabulary).
- **Do not** put ``price_negotiation_rounds=N`` in ``creator_progress`` — that column is Excel「达人进度」only.
- **B-level / AI UGC / other tiers**: **no** multi-round price ladder — if they reject an offer, use polite close / alternate path; use ``priority_node:B1``/``B2`` or ``C1``/``C2`` for sample/portal flows, **never** A1–A4 or ``price_negotiation_rounds``.

## Goal
Extract **every** distinct **trigger condition → reply script / action** pair across the entire document.
Do not stop at section headers — parse SCRIPT blocks, negotiation branches (A1/A2/…), GMV tiers, and supplementary notes.

## Field semantics (same as Excel ``AI话术参考``)
- **title** / **entitle**: **Simulated creator messages** — what the creator might actually type in DM (English, TikTok creator tone). **NOT** internal labels like ``OK / Sure (A-level)`` or ``doc-1``.
  - Write **2–4 alternative phrasings** per record, **one per line** (same intent, different wording).
  - If the playbook gives an exact trigger keyword (``AI``, ``WhatsApp``, ``WA``, ``Stop``), include that verbatim **and** natural paraphrases.
  - Put tier/GMV routing in the structured condition fields below — **never** free-text descriptions.
- **content** / **encontent**: **Our reply script** Linsey sends (English). Expand brief playbook bullets into send-ready messages when needed. Keep ``{{placeholders}}`` unchanged.
- **intent_category**: **minimal** scenario label (**1–4 English words**), e.g. ``ok``, ``Pure Commission``, ``CPM Rate``, ``Sample Request``, ``AI UGC``, ``WhatsApp``, ``Reject``. NOT ``Pure Commission Negotiation``.
- **creator_type**: tier — ``A-level``, ``B-level``, ``S-level``, ``SS-level``, ``AI UGC达人``, ``高GMV达人``, ``纯佣寄样达人``, or ``GEN``.
- **creator_progress**: creator stage if inferable, else ``GEN``.
- **creator_emotion**: ``GEN`` unless specified.
- **creator_reply_frequency**: ``GEN`` unless specified.
- **applicable_shops**: shop slug from source path (e.g. doc subfolder ``toolant`` → ``toolant``). Use ``GEN`` only if truly cross-shop.
- **rule_type**: ``AI参考话术`` (has reply script), ``AI执行动作`` (action only, no script), ``策略说明`` (policy/context only).
- **ai_acation**: system/AI action after trigger (e.g. push commission form, write Feishu row, join WhatsApp group).
- **other_creator_conditions**: JSON **array** of **standard query flags only** (AND logic) — see vocabulary below. **No prose.**
- **key_information**: JSON **array** or string — **only** supplementary items that this record's ``restore_slots`` will restore from ``shop_restore_values.yaml`` (see allowed list in restore section). Use human-readable labels (e.g. ``WhatsApp Group Link``). **Do NOT** list CPM rates, sample forms, commission rates, or policy prose unless a matching restore slot has a configured value. Omit if nothing to deliver.
- **restore_slots**: JSON **array** of placeholder IDs used in ``content`` (e.g. ``wa_group_gmv_gte_1k``) — real values restored at reply time.
- **answer_purpose**: one sentence — what this rule clarifies for the creator.
- **creator_action_guide**: one sentence — what the creator should **do** next (join group / add link / reply AI / fill form).
- **language**: always ``en`` when title/content are English scripts.
- **source_chunk_id**: short section label (e.g. ``A-Pure-Commission``, ``Supplement-1``) for traceability.

## Supplement & desensitization
- **SUPPLEMENT** text (e.g. ``add1.txt``) is mandatory reference — merge WhatsApp tiers, affiliate links, AI video incentives into separate records.
- **Never** store real URLs or shop secrets in JSON; use ``{{restore_slot}}`` placeholders listed in **restore_slots**.
- Every ``AI参考话术`` / ``AI执行动作`` record MUST include **answer_purpose** and **creator_action_guide**.

## Vocabulary priority (Excel ``AI话术参考`` + ``knowledge_chunk_vocabulary.yaml``)
1. **First** map every field to an **existing** canonical value from the vocabulary section below.
2. Use **aliases** mentally (e.g. ``执行动作`` → ``AI执行动作``, ``Sample Interest`` → ``Sample Request``).
3. Only create a **new** canonical ``intent_category`` / ``restore_slots`` id when nothing fits — keep it minimal. ``key_information`` must map to document-extracted tags **with** configured restore values only.
4. Unmapped new terms are **auto-appended** to the vocabulary file after structuring for human review.

## Rules
1. Read the **whole** document holistically — cross-reference GMV routing, tier downgrade, and script variants.
2. Split each distinct **trigger → reply/action** into its own record; prefer **more granular** records over dumping whole sections.
3. Include supplement rules (WhatsApp tiers, affiliate links, AI video pay) as separate records with correct GMV flags.
4. Pure overview with no actionable pair: one ``策略说明`` record max; skip empty boilerplate.
5. Use ``GEN`` for unknown dimensions; **omit** keys whose values would be empty.
6. **Every** ``AI参考话术`` / ``AI执行动作`` record MUST have realistic multi-line ``title`` (2–4 creator sample messages). Never use routing labels alone as ``title``.
7. Reply with **ONLY** a JSON array (no markdown).

{analysis_context}

{condition_vocab}

{restore_vocab}

## Reference examples from Excel chunks
{examples}
"""


def _html_to_document_text(path: Path) -> str:
    from bs4 import BeautifulSoup

    html = path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()

    lines: list[str] = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        text = tag.get_text(" ", strip=True)
        if text:
            level = int(tag.name[1])
            lines.append(f"\n{'#' * level} {text}\n")

    body = soup.get_text("\n", strip=True)
    if lines:
        return "\n".join(lines) + "\n\n" + body
    return body


def _load_playbook_document(
    doc_dir: Path,
    *,
    html_file: str,
    supplement_files: list[str] | None = None,
) -> tuple[str, str]:
    html_path = doc_dir / html_file
    if not html_path.is_file():
        raise FileNotFoundError(html_path)

    parts = [f"=== PLAYBOOK: {html_file} ===\n", _html_to_document_text(html_path)]
    for rel in supplement_files or []:
        sup_path = doc_dir / rel
        if not sup_path.is_file():
            logger.warning("Supplement not found, skipped: %s", sup_path)
            continue
        parts.append(f"\n\n=== SUPPLEMENT (must merge into rules): {rel} ===\n")
        parts.append(
            "The following shop-specific operational notes MUST be reflected in structured records "
            "(split by GMV/conditions). Store secrets as {restore_slot} placeholders only.\n"
            "**If this file is listed as `authoritative` in authoritative_sources.yaml, "
            "deterministic materializer output overrides LLM on the same topic.**\n\n"
        )
        parts.append(sup_path.read_text(encoding="utf-8").strip())

    source_file = html_file.replace("\\", "/")
    return source_file, "\n".join(parts)


def _load_reference_examples(output_dir: Path, *, max_items: int = 3) -> str:
    ref_path = output_dir / "ai话术参考.chunks.json"
    if not ref_path.is_file():
        return "(no reference file)"
    rows = json.loads(ref_path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        return "(invalid reference)"
    picked: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("rule_type") == "AI参考话术" and row.get("content"):
            slim = {k: row[k] for k in row if k in (*_SCHEMA_FIELDS, "id", "source_file") and row.get(k)}
            picked.append(slim)
        if len(picked) >= max_items:
            break
    return json.dumps(picked, ensure_ascii=False, indent=2)


def _structure_config_from_project(
    project_root: Path,
    *,
    full_document: bool = False,
) -> ChunkTranslateConfig | None:
    yaml_data = load_project_yaml(project_root)
    ai = yaml_data.get("ai")
    if not isinstance(ai, dict):
        return None
    cfg = ChunkTranslateConfig.from_ai_yaml_section(ai)
    if cfg is None:
        return None
    min_tokens = 16384 if full_document else 8192
    return ChunkTranslateConfig(
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        model=cfg.model,
        api_path=cfg.api_path,
        temperature=min(cfg.temperature, 0.3),
        max_tokens=max(cfg.max_tokens, min_tokens),
        timeout_seconds=max(cfg.timeout_seconds, 180.0 if full_document else 120.0),
        batch_size=1,
        max_retries=cfg.max_retries,
        batch_sleep_seconds=max(cfg.batch_sleep_seconds, 0.5),
    )


def _shop_slug_from_source_path(source_file: str) -> str | None:
    """``toolant/playbook.html`` → ``toolant``；根目录单文件则返回 ``None``。"""
    rel = (source_file or "").replace("\\", "/").strip().lstrip("/")
    parts = [p for p in rel.split("/") if p and p not in {".", ".."}]
    if len(parts) >= 2:
        return parts[0]
    return None


def _normalize_record(
    raw: dict[str, Any],
    *,
    source_file: str,
    source_chunk_id: str,
    default_shop: str | None = None,
) -> dict[str, Any]:
    raw = normalize_playbook_record_fields(raw)
    raw = desensitize_playbook_record(raw)
    out: dict[str, Any] = {
        "source_chunk_id": source_chunk_id,
        "source_file": source_file,
    }
    for key in _SCHEMA_FIELDS:
        if key not in raw:
            continue
        val = raw[key]
        if _excel_json_value_absent(val):
            continue
        out[key] = val

    lang = str(out.get("language") or "en").strip() or "en"
    out["language"] = lang

    title = str(out.get("title") or "").strip()
    content = str(out.get("content") or "").strip()
    if title:
        out["title"] = title
    if content:
        out["content"] = content
    out["entitle"] = str(out.get("entitle") or title or "").strip()
    out["encontent"] = str(out.get("encontent") or content or "").strip()

    shop = default_shop or _shop_slug_from_source_path(source_file)
    existing_shop = str(out.get("applicable_shops") or "").strip()
    if shop and (not existing_shop or existing_shop.upper() == "GEN"):
        out["applicable_shops"] = shop

    rule_type = str(out.get("rule_type") or "").strip()
    if rule_type == "AI执行动作" and out.get("ai_acation"):
        if not title:
            out["title"] = str(out.get("intent_category") or "Action trigger").strip()
            out["entitle"] = out["title"]
        return out
    if rule_type == "策略说明":
        if title or content:
            return out
        return {}
    if not title and not content:
        return {}
    return out


def _structure_one_chunk(
    cfg: ChunkTranslateConfig,
    system: str,
    chunk: dict[str, Any],
) -> list[dict[str, Any]]:
    cid = str(chunk.get("id", "")).strip()
    payload = {
        "id": cid,
        "intent_category": chunk.get("intent_category", ""),
        "creator_type": chunk.get("creator_type", ""),
        "title": chunk.get("title", ""),
        "content": chunk.get("content", ""),
        "entitle": chunk.get("entitle", ""),
        "encontent": chunk.get("encontent", ""),
    }
    user = json.dumps(payload, ensure_ascii=False, indent=2)
    raw = _post_chat_with_retry(cfg, system, user)
    raw = _strip_markdown_json_fence(raw)
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError(f"chunk {cid}: expected JSON array")

    source_file = str(chunk.get("source_file") or "")
    default_shop = _shop_slug_from_source_path(source_file)
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        rec = _normalize_record(
            item,
            source_file=source_file,
            source_chunk_id=cid,
            default_shop=default_shop,
        )
        if rec:
            out.append(rec)
    return out


def _structure_full_document(
    cfg: ChunkTranslateConfig,
    system: str,
    *,
    document_text: str,
    source_file: str,
    default_shop: str | None = None,
    analysis: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    shop = default_shop or _shop_slug_from_source_path(source_file)
    shop_line = (
        f'Set applicable_shops to "{shop}" for all records (source under kb/input/{shop}/).\n'
        if shop
        else ""
    )
    user = (
        "Extract all trigger→reply/action records from this playbook document.\n"
        "For each record, simulate 2–4 realistic English creator DM messages in title/entitle "
        "(one per line), matching the Excel reference style.\n"
        "You MUST implement the full negotiation ladder and condition_relationships from "
        "Document analysis below — including accept AND reject/escalate paths at each A-tier node.\n"
        f"{shop_line}\n"
        f"source_file: {source_file}\n\n"
    )
    if analysis:
        from hubstudio_python.kb.service.pipelines.playbook_structure_context import (
            analysis_negotiation_summary_json,
        )

        user += (
            "=== STEP-1 ANALYSIS (binding constraints) ===\n"
            f"{analysis_negotiation_summary_json(analysis)}\n\n"
        )
    user += f"=== PLAYBOOK DOCUMENT ===\n{document_text}"
    raw = _post_chat_with_retry(cfg, system, user)
    raw = _strip_markdown_json_fence(raw)
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("expected JSON array from full-document structuring")

    out: list[dict[str, Any]] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        chunk_id = str(item.get("source_chunk_id") or f"doc-{i + 1}").strip()
        rec = _normalize_record(
            item,
            source_file=source_file,
            source_chunk_id=chunk_id,
            default_shop=shop,
        )
        if rec:
            out.append(rec)
    return out


@dataclass(frozen=True)
class StructurePlaybookResult:
    input_path: str
    output_path: str
    source_chunks: int
    structured_records: int
    mode: str = "chunks"
    shop: str = ""
    analysis_path: str = ""
    vocabulary_path: str = ""
    intent_yaml_path: str = ""
    restore_yaml_path: str = ""
    phases: tuple[dict[str, Any], ...] = ()


def structure_playbook_document(
    paths: PathsConfig,
    *,
    html_file: str = "toolant/linsey-agent-playbook_2.html",
    supplement_files: list[str] | None = None,
    output_filename: str | None = None,
    skip_analyze: bool = False,
    analyze_only: bool = False,
) -> StructurePlaybookResult:
    """
    完整 Playbook 流水线（document 模式）：

    1. **文档解释**（DeepSeek）— 抽取变量/常量/条件/意图 → vocabulary overlay + intent_classification
    2. **合并 schema** — 写入 vocabulary/<shop>.yaml，合并 merged.yaml，抽取 restore/<shop>.yaml
    3. **结构化**（DeepSeek）— 产出 trigger→reply 记录 ``*.structured.full.json``
    """
    from hubstudio_python.kb.service.pipelines.extract_shop_vocabulary import (
        analyze_playbook_document,
        apply_playbook_analysis,
        ensure_generic_vocabulary_installed,
        load_playbook_analysis,
    )

    ensure_generic_vocabulary_installed()

    source_file, document_text = _load_playbook_document(
        paths.input_dir,
        html_file=html_file,
        supplement_files=supplement_files,
    )
    shop = _shop_slug_from_source_path(source_file) or "unknown"
    supplements = [s.replace("\\", "/") for s in (supplement_files or [])]
    phases: list[dict[str, Any]] = []

    stem = Path(html_file).stem.replace("_", "-")
    analysis_path = paths.output_dir / f"{shop}-{stem}.analysis.json"

    if skip_analyze:
        if not analysis_path.is_file():
            fallback = paths.output_dir / f"{shop}-playbook.analysis.json"
            analysis_path = fallback if fallback.is_file() else analysis_path
        if not analysis_path.is_file():
            raise FileNotFoundError(
                f"缺少分析文件 {analysis_path}；请先运行 structure-playbook --mode document（不加 --skip-analyze）"
            )
        analysis = load_playbook_analysis(analysis_path)
        phases.append({"phase": "analyze", "skipped": True, "analysis_path": str(analysis_path.resolve())})
    else:
        analysis, analyze_result = analyze_playbook_document(
            document_text=document_text,
            source_file=source_file.replace("\\", "/"),
            shop=shop,
            supplement_files=supplements,
            output_dir=paths.output_dir,
            project_root=paths.project_root,
            output_stem=stem,
        )
        analysis_path = Path(analyze_result.analysis_path)
        shop = analyze_result.shop
        phases.append({"phase": "analyze", **{k: v for k, v in analyze_result.__dict__.items()}})

    apply_result = apply_playbook_analysis(analysis, shop=shop)
    phases.append(
        {
            "phase": "apply_schema",
            "overlay_path": apply_result.overlay_path,
            "vocabulary_path": apply_result.vocabulary_path,
            "intent_yaml_path": apply_result.intent_yaml_path,
            "restore_yaml_path": apply_result.restore_yaml_path,
            "changes": apply_result.changes,
        }
    )

    if analyze_only:
        in_path = paths.input_dir / html_file
        return StructurePlaybookResult(
            input_path=str(in_path.resolve()),
            output_path="",
            source_chunks=0,
            structured_records=0,
            mode="document",
            shop=shop,
            analysis_path=str(analysis_path.resolve()),
            vocabulary_path=apply_result.vocabulary_path,
            intent_yaml_path=apply_result.intent_yaml_path,
            restore_yaml_path=apply_result.restore_yaml_path,
            phases=tuple(phases),
        )

    cfg = _structure_config_from_project(paths.project_root, full_document=True)
    if cfg is None:
        raise ValueError("Missing ai.api_key in config.yaml for DeepSeek structuring")

    logger.info("Full-document playbook structuring: %s chars, shop=%s", len(document_text), shop)

    if output_filename is None:
        parent = Path(html_file).parent
        prefix = f"{parent.name}-" if parent.parts and parent.name not in {".", ""} else ""
        output_filename = f"{prefix}{stem}.structured.full.json"

    out_path = paths.output_dir / output_filename
    examples = _load_reference_examples(paths.output_dir, max_items=5)
    condition_vocab = format_condition_vocabulary_for_prompt(shop)
    restore_vocab = format_restore_slots_for_prompt(shop)
    from hubstudio_python.kb.service.pipelines.playbook_structure_context import (
        align_structured_records_with_analysis,
        format_analysis_for_structure_prompt,
    )
    from hubstudio_python.kb.service.pipelines.authoritative_sources import (
        format_authoritative_supplements_for_prompt,
    )

    analysis_context = format_analysis_for_structure_prompt(analysis)
    auth_block = format_authoritative_supplements_for_prompt(shop, project_root=paths.project_root)
    if auth_block:
        analysis_context = f"{analysis_context}\n\n{auth_block}"
    system = _FULL_DOC_SYSTEM_TEMPLATE.format(
        examples=examples,
        condition_vocab=condition_vocab,
        restore_vocab=restore_vocab,
        analysis_context=analysis_context,
    )

    structured = _structure_full_document(
        cfg,
        system,
        document_text=document_text,
        source_file=source_file,
        default_shop=shop,
        analysis=analysis,
    )
    structured = align_structured_records_with_analysis(
        structured,
        analysis,
        shop=shop,
        source_file=source_file.replace("\\", "/"),
        doc_dir=paths.input_dir,
        project_root=paths.project_root,
    )
    for i, rec in enumerate(structured, start=1):
        rec["id"] = str(rec.get("id") or i)

    from hubstudio_python.kb.service.pipelines.vocabulary_sync import extend_vocabulary_from_records

    vocab_extend = extend_vocabulary_from_records(structured, source_label=out_path.name)
    if vocab_extend.added:
        logger.info("Auto-added vocabulary: %s", vocab_extend.added)

    phases.append(
        {
            "phase": "structure",
            "structured_records": len(structured),
            "vocabulary_auto_added": vocab_extend.added,
        }
    )

    payload = {
        "_meta": {
            "description": "DeepSeek Playbook 流水线：文档解释 → schema → 结构化记录",
            "mode": "document",
            "source_html": html_file.replace("\\", "/"),
            "supplement_files": supplements,
            "analysis_file": analysis_path.name,
            "document_chars": len(document_text),
            "structured_record_count": len(structured),
            "reference_file": "ai话术参考.chunks.json",
            "vocabulary_file": "rag_data/schema/vocabulary/merged.yaml",
            "intent_file": f"rag_data/schema/intent/{shop}.yaml",
            "vocabulary_auto_added": vocab_extend.added,
        },
        "records": structured,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    in_path = paths.input_dir / html_file
    return StructurePlaybookResult(
        input_path=str(in_path.resolve()),
        output_path=str(out_path.resolve()),
        source_chunks=0,
        structured_records=len(structured),
        mode="document",
        shop=shop,
        analysis_path=str(analysis_path.resolve()),
        vocabulary_path=apply_result.vocabulary_path,
        intent_yaml_path=apply_result.intent_yaml_path,
        restore_yaml_path=apply_result.restore_yaml_path,
        phases=tuple(phases),
    )


def structure_playbook_chunks(
    paths: PathsConfig,
    *,
    input_filename: str = "toolant-linsey-agent-playbook-2.chunks.json",
    output_filename: str | None = None,
) -> StructurePlaybookResult:
    """
    读取 ``output_dir`` 下 playbook ``*.chunks.json``，调用 DeepSeek 结构化，写出 ``*.structured.chunks.json``。
    """
    cfg = _structure_config_from_project(paths.project_root, full_document=False)
    if cfg is None:
        raise ValueError("Missing ai.api_key in config.yaml for DeepSeek structuring")

    in_path = paths.output_dir / input_filename
    if not in_path.is_file():
        raise FileNotFoundError(in_path)

    if output_filename is None:
        stem = in_path.name.replace(".chunks.json", "")
        output_filename = f"{stem}.structured.chunks.json"

    out_path = paths.output_dir / output_filename
    rows = json.loads(in_path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"{in_path.name} must be a JSON array")

    examples = _load_reference_examples(paths.output_dir)
    default_shop = _shop_slug_from_source_path(str(rows[0].get("source_file", "")) if rows else "")
    condition_vocab = format_condition_vocabulary_for_prompt(default_shop)
    restore_vocab = format_restore_slots_for_prompt(default_shop)
    system = _SYSTEM_TEMPLATE.format(
        examples=examples,
        condition_vocab=condition_vocab,
        restore_vocab=restore_vocab,
    )

    structured: list[dict[str, Any]] = []
    seq = 0
    for i, chunk in enumerate(rows):
        if not isinstance(chunk, dict):
            continue
        cid = str(chunk.get("id", i + 1))
        try:
            records = _structure_one_chunk(cfg, system, chunk)
            logger.info("Structured chunk %s → %s record(s)", cid, len(records))
        except Exception as exc:
            logger.warning("Structure chunk %s failed: %s", cid, exc)
            records = []
        for rec in records:
            seq += 1
            rec["id"] = str(rec.get("id") or f"{cid}-{seq}")
            structured.append(rec)
        if cfg.batch_sleep_seconds > 0 and i + 1 < len(rows):
            time.sleep(cfg.batch_sleep_seconds)

    from hubstudio_python.kb.service.pipelines.vocabulary_sync import extend_vocabulary_from_records

    vocab_extend = extend_vocabulary_from_records(structured, source_label=out_path.name)

    payload = {
        "_meta": {
            "description": "DeepSeek 结构化：Playbook 切块 → Excel 同构字段（条件→回复）",
            "mode": "chunks",
            "source_chunks_file": in_path.name,
            "source_chunk_count": len(rows),
            "structured_record_count": len(structured),
            "reference_file": "ai话术参考.chunks.json",
            "vocabulary_file": "rag_data/schema/vocabulary/merged.yaml",
            "vocabulary_auto_added": vocab_extend.added,
        },
        "records": structured,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return StructurePlaybookResult(
        input_path=str(in_path.resolve()),
        output_path=str(out_path.resolve()),
        source_chunks=len(rows),
        structured_records=len(structured),
        mode="chunks",
    )


def structured_full_json_path(output_dir: Path, html_source_file: str) -> Path:
    """``toolant/linsey-agent-playbook_2.html`` → ``toolant-linsey-agent-playbook-2.structured.full.json``。"""
    from hubstudio_python.kb.service.pipelines.build_knowledge_base import output_name_for_source

    chunks_name = output_name_for_source(html_source_file)
    return output_dir / chunks_name.replace(".chunks.json", ".structured.full.json")


def load_structured_playbook_records(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        recs = data.get("records")
        if isinstance(recs, list):
            return [r for r in recs if isinstance(r, dict)]
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return []


def flatten_structured_playbook_to_chunk_records(
    paths: PathsConfig,
    *,
    html_source_file: str,
    structured_path: Path | None = None,
) -> list[dict[str, Any]]:
    """
    将 ``*.structured.full.json`` 的 ``records`` 展平为可入库的 ``*.chunks.json`` 数组。

    含 ``other_creator_conditions``（讨价还价节点）、``entitle`` 用户问话样例、``restore_slots`` 等。
    """
    spath = structured_path or structured_full_json_path(paths.output_dir, html_source_file)
    if not spath.is_file():
        raise FileNotFoundError(
            f"Playbook 结构化文件不存在: {spath}. "
            "请先运行: uv run python main.py structure-playbook --mode document"
        )
    default_shop = _shop_slug_from_source_path(html_source_file)
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(load_structured_playbook_records(spath), start=1):
        chunk_id = str(raw.get("source_chunk_id") or raw.get("id") or i).strip()
        rec = _normalize_record(
            raw,
            source_file=html_source_file,
            source_chunk_id=chunk_id,
            default_shop=default_shop,
        )
        if not rec:
            continue
        rec["id"] = str(raw.get("id") or len(out) + 1)
        out.append(rec)
    return out

