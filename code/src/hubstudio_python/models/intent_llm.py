"""DeepSeek 意图分类：在关键词不足时用大模型从 intent 规则集中选型。"""

from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING, Any

from hubstudio_python.kb.service.chunking.zh_translate import ChunkTranslateConfig, _post_chat_with_retry
from hubstudio_python.config import load_paths, load_project_yaml

if TYPE_CHECKING:
    from hubstudio_python.models.intent_classification import IntentClassificationResult, IntentRule

_INTENT_LLM_JSON_RE = re.compile(r"\{[\s\S]*\}")


def intent_llm_mode() -> str:
    """
    ``fallback``（默认）：仅当关键词未命中或置信度非 high 时调 LLM。
    ``always``：除 exact 外一律 LLM。
    ``off``：仅关键词。
    """
    raw = os.environ.get("HUBSTUDIO_INTENT_LLM_MODE", "fallback").strip().lower()
    if raw in {"0", "false", "off", "no"}:
        return "off"
    if raw in {"always", "on", "1", "true"}:
        return "always"
    return "fallback"


def _ai_config_for_intent() -> ChunkTranslateConfig:
    paths = load_paths()
    ai = load_project_yaml(paths.project_root).get("ai")
    if not isinstance(ai, dict):
        raise ValueError("config.yaml 缺少 ai 节，无法进行 LLM 意图分类")
    cfg = ChunkTranslateConfig.from_ai_yaml_section(ai)
    if cfg is None:
        raise ValueError("config.yaml ai.api_key 未配置")
    return cfg


def _catalog_lines(rules: list[IntentRule]) -> list[str]:
    skip = {"GEN", "No obvious intention", "no_obvious_intention"}
    lines: list[str] = []
    for rule in rules:
        if rule.id in skip:
            continue
        parts = [
            f"- id: {rule.id}",
            f"  label: {rule.label_zh}",
            f"  scenario: {rule.scenario_zh}",
            f"  tier: {rule.tier}",
        ]
        if rule.examples:
            parts.append(f"  examples: {' | '.join(rule.examples[:5])}")
        if rule.not_this_intent:
            neg = "; ".join(f"{x.get('id')} when {x.get('when')}" for x in rule.not_this_intent if isinstance(x, dict))
            if neg:
                parts.append(f"  not_this: {neg}")
        lines.append("\n".join(parts))
    lines.append("- id: GEN\n  label: 通用意图\n  scenario: 无法归入以上任一意图")
    lines.append("- id: no_obvious_intention\n  label: 无明显意图\n  scenario: 寒暄、表情、过短且无业务含义")
    return lines


def _parse_llm_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    m = _INTENT_LLM_JSON_RE.search(text)
    if not m:
        raise ValueError("LLM response is not JSON")
    data = json.loads(m.group())
    if not isinstance(data, dict):
        raise ValueError("LLM JSON must be an object")
    return data


def classify_intent_with_llm(
    message: str,
    rules: list[IntentRule],
    *,
    shop: str | None,
    context_text: str = "",
) -> IntentClassificationResult:
    from hubstudio_python.models.intent_classification import IntentClassificationResult
    from hubstudio_python.models.knowledge_chunk_vocabulary import get_vocabulary

    text = (message or "").strip()
    allowed_ids = {r.id for r in rules} | {"GEN", "no_obvious_intention", "No obvious intention"}

    cfg = _ai_config_for_intent()
    catalog = "\n\n".join(_catalog_lines(rules))
    system = (
        "You classify a TikTok creator's latest DM into exactly ONE intent id from the catalog. "
        "Reply with ONLY a JSON object: "
        '{"intent_id":"<id>","confidence":"high|medium|low","reason":"<one short sentence>"}. '
        "Pick the most specific intent; use GEN if unclear but still business-related; "
        "use no_obvious_intention for greetings/thanks/emoji-only."
    )
    user_parts = [
        f"Shop: {shop or 'unknown'}",
        "",
        "INTENT CATALOG:",
        catalog,
        "",
        "Latest creator message:",
        text,
    ]
    if context_text.strip():
        user_parts.extend(["", "Recent conversation (for disambiguation):", context_text.strip()[:4000]])
    user = "\n".join(user_parts)

    raw = _post_chat_with_retry(cfg, system, user)
    data = _parse_llm_json(raw)
    intent_id = str(data.get("intent_id") or data.get("intent") or "GEN").strip()
    if intent_id not in allowed_ids:
        intent_id = "GEN"
    conf = str(data.get("confidence") or "medium").strip().lower()
    if conf not in {"high", "medium", "low"}:
        conf = "medium"
    reason = str(data.get("reason") or "").strip()

    vocab = get_vocabulary()
    canon = vocab.canonicalize_intent(intent_id, shop=shop) or intent_id
    hints = ["llm"]
    if reason:
        hints.append(reason[:200])
    return IntentClassificationResult(
        message=text,
        intent_category=canon,
        confidence=conf,
        matched_by="llm",
        matched_rule=intent_id,
        hints=hints,
    )


_CONFIDENCE_LABEL_TO_FLOAT = {"high": 0.9, "medium": 0.65, "low": 0.35}


def rank_intents_with_llm(
    message: str,
    rules: list[IntentRule],
    *,
    shop: str | None,
    context_text: str = "",
    top_k: int = 5,
) -> list:
    """返回按置信度降序的多意图候选（数值 confidence 0–1）。"""
    from hubstudio_python.models.intent_ranking import RankedIntent
    from hubstudio_python.models.knowledge_chunk_vocabulary import get_vocabulary

    text = (message or "").strip()
    allowed_ids = {r.id for r in rules} | {"GEN", "no_obvious_intention", "No obvious intention"}

    cfg = _ai_config_for_intent()
    catalog = "\n\n".join(_catalog_lines(rules))
    system = (
        "You classify a TikTok creator's latest DM into up to "
        f"{top_k} intent ids from the catalog, ranked by relevance. "
        "Reply with ONLY a JSON object: "
        '{"intents":[{"intent_id":"<id>","confidence":0.0,"reason":"<short>"}, ...]}. '
        "confidence must be a float between 0 and 1. "
        "Include only intents you are reasonably sure about; omit weak guesses. "
        "Use GEN if unclear but still business-related; "
        "use no_obvious_intention for greetings/thanks/emoji-only."
    )
    user_parts = [
        f"Shop: {shop or 'unknown'}",
        "",
        "INTENT CATALOG:",
        catalog,
        "",
        "Latest creator message:",
        text,
    ]
    if context_text.strip():
        user_parts.extend(["", "Recent conversation (for disambiguation):", context_text.strip()[:4000]])
    user = "\n".join(user_parts)

    raw = _post_chat_with_retry(cfg, system, user)
    data = _parse_llm_json(raw)
    items = data.get("intents")
    if not isinstance(items, list):
        single_id = str(data.get("intent_id") or data.get("intent") or "GEN").strip()
        conf_raw = data.get("confidence")
        if isinstance(conf_raw, (int, float)):
            conf_f = max(0.0, min(1.0, float(conf_raw)))
        else:
            conf_f = _CONFIDENCE_LABEL_TO_FLOAT.get(str(conf_raw or "medium").strip().lower(), 0.65)
        items = [{"intent_id": single_id, "confidence": conf_f, "reason": data.get("reason", "")}]

    vocab = get_vocabulary()
    out: list = []
    for item in items[:top_k]:
        if not isinstance(item, dict):
            continue
        intent_id = str(item.get("intent_id") or item.get("intent") or "GEN").strip()
        if intent_id not in allowed_ids:
            intent_id = "GEN"
        conf_raw = item.get("confidence")
        if isinstance(conf_raw, (int, float)):
            conf_f = max(0.0, min(1.0, float(conf_raw)))
        else:
            conf_f = _CONFIDENCE_LABEL_TO_FLOAT.get(str(conf_raw or "medium").strip().lower(), 0.65)
        reason = str(item.get("reason") or "").strip()
        canon = vocab.canonicalize_intent(intent_id, shop=shop) or intent_id
        out.append(
            RankedIntent(
                intent_category=canon,
                matched_rule=intent_id,
                confidence=conf_f,
                matched_by="llm",
                reason=reason[:200],
            )
        )
    out.sort(key=lambda x: -x.confidence)
    return out
