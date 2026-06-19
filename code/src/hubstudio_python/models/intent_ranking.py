"""多意图排序：Top-K + 数值置信度，供 ``/api/intent`` 使用。"""

from __future__ import annotations

from dataclasses import dataclass

from hubstudio_python.models.intent_classification import (
    IntentClassifier,
    IntentClassificationResult,
    get_intent_classifier,
)
from hubstudio_python.models.intent_llm import rank_intents_with_llm
from hubstudio_python.models.knowledge_chunk_vocabulary import get_vocabulary

CONFIDENCE_LABEL_TO_FLOAT = {"high": 0.9, "medium": 0.65, "low": 0.35}


@dataclass(frozen=True)
class RankedIntent:
    intent_category: str
    matched_rule: str
    confidence: float
    matched_by: str
    reason: str = ""


def _label_to_float(conf: str) -> float:
    return CONFIDENCE_LABEL_TO_FLOAT.get(str(conf or "").strip().lower(), 0.35)


def _keyword_ranked_candidates(
    text: str,
    clf: IntentClassifier,
    *,
    shop_key: str | None,
) -> list[RankedIntent]:
    lower = text.lower()
    vocab = get_vocabulary()
    scored: list[tuple[float, str, int]] = []
    for rule in clf.rules:
        if rule.id in {"GEN", "No obvious intention", "no_obvious_intention"}:
            continue
        score = 0
        for kw in rule.keywords_en:
            if kw in lower:
                score += 2 if len(kw) > 4 else 1
        for kw in rule.keywords_zh:
            if kw in text:
                score += 2
        if score > 0:
            scored.append((score, rule.id, rule.priority))

    scored.sort(key=lambda x: (-x[0], -x[2], x[1]))
    out: list[RankedIntent] = []
    max_score = scored[0][0] if scored else 1
    for score, rule_id, _pri in scored[:8]:
        conf = min(0.92, 0.45 + (score / max(max_score, 1)) * 0.45)
        canon = vocab.canonicalize_intent(rule_id, shop=shop_key) or rule_id
        out.append(
            RankedIntent(
                intent_category=canon,
                matched_rule=rule_id,
                confidence=conf,
                matched_by="keyword",
                reason=f"keyword_score={score}",
            )
        )
    return out


def _merge_ranked(candidates: list[RankedIntent]) -> list[RankedIntent]:
    best: dict[str, RankedIntent] = {}
    for c in candidates:
        prev = best.get(c.intent_category)
        if prev is None or c.confidence > prev.confidence:
            best[c.intent_category] = c
    merged = list(best.values())
    merged.sort(key=lambda x: -x.confidence)
    return merged


def _apply_toolant_rank_boost(
    candidates: list[RankedIntent],
    *,
    message: str,
    context_text: str,
    shop: str,
    creator_type: str,
) -> list[RankedIntent]:
    from hubstudio_python.models.toolant_creator_type import is_toolant_head_tier

    if shop.strip().lower() != "toolant" or not is_toolant_head_tier(creator_type):
        return candidates
    from hubstudio_python.models.toolant_intent import is_a1_commission_price_reject

    if not is_a1_commission_price_reject(message=message, context_text=context_text):
        return candidates
    boosted: list[RankedIntent] = []
    has_cpm = False
    for c in candidates:
        if c.intent_category == "CPM Rate":
            has_cpm = True
            boosted.append(
                RankedIntent(
                    intent_category=c.intent_category,
                    matched_rule="CPM Rate",
                    confidence=max(c.confidence, 0.92),
                    matched_by="toolant_refine",
                    reason="a1_reject_price_too_low_to_a2_cpm",
                )
            )
        else:
            boosted.append(c)
    if not has_cpm:
        boosted.insert(
            0,
            RankedIntent(
                intent_category="CPM Rate",
                matched_rule="CPM Rate",
                confidence=0.92,
                matched_by="toolant_refine",
                reason="a1_reject_price_too_low_to_a2_cpm",
            ),
        )
    boosted.sort(key=lambda x: -x.confidence)
    return boosted


def _in_allowed(intent_category: str, allowed: list[str]) -> bool:
    if not allowed:
        return True
    from hubstudio_python.reply.service.rules.match import intent_matches_allowed_scope

    return intent_matches_allowed_scope(intent_category, allowed)


def rank_user_message_intents(
    message: str,
    *,
    shop: str | None,
    creator_type: str = "",
    context_text: str = "",
    use_llm: bool | None = None,
    allowed_intents: list[str] | None = None,
    min_confidence: float = 0.5,
    top_k: int = 3,
) -> list[RankedIntent]:
    text = (message or "").strip()
    if not text:
        return []

    clf = get_intent_classifier(shop)
    shop_key = str(shop or "").strip() or None
    allowed = list(allowed_intents or [])

    exact = clf._try_exact(text, shop_key=shop_key)
    if exact is not None:
        conf = _label_to_float(exact.confidence)
        if conf >= min_confidence and _in_allowed(exact.intent_category, allowed):
            return [
                RankedIntent(
                    intent_category=exact.intent_category,
                    matched_rule=exact.matched_rule,
                    confidence=conf,
                    matched_by=exact.matched_by,
                    reason="exact_match",
                )
            ]

    from hubstudio_python.models.intent_llm import intent_llm_mode

    mode = intent_llm_mode()
    if use_llm is True:
        mode = "always"
    elif use_llm is False:
        mode = "off"

    candidates: list[RankedIntent] = []
    candidates.extend(_keyword_ranked_candidates(text, clf, shop_key=shop_key))

    if mode != "off":
        try:
            candidates.extend(
                rank_intents_with_llm(
                    text,
                    clf.rules,
                    shop=shop_key,
                    context_text=context_text,
                    top_k=5,
                )
            )
        except Exception:
            pass

    if mode == "off" and not candidates:
        single = clf.classify(text, shop=shop, context_text=context_text, use_llm=False)
        if single.matched_by != "none":
            candidates.append(
                RankedIntent(
                    intent_category=single.intent_category,
                    matched_rule=single.matched_rule,
                    confidence=_label_to_float(single.confidence),
                    matched_by=single.matched_by,
                    reason=";".join(single.hints[:2]),
                )
            )

    merged = _merge_ranked(candidates)
    merged = _apply_toolant_rank_boost(
        merged,
        message=text,
        context_text=context_text,
        shop=str(shop or ""),
        creator_type=creator_type,
    )

    filtered: list[RankedIntent] = []
    for c in merged:
        if c.confidence < min_confidence:
            continue
        if allowed and not _in_allowed(c.intent_category, allowed):
            continue
        filtered.append(c)
        if len(filtered) >= top_k:
            break
    return filtered


def ranked_to_single_result(ranked: list[RankedIntent], message: str) -> IntentClassificationResult:
    if not ranked:
        return IntentClassificationResult(
            message=message,
            intent_category="GEN",
            confidence="low",
            matched_by="none",
            hints=["no_intent_above_threshold"],
        )
    top = ranked[0]
    conf_label = "high" if top.confidence >= 0.75 else "medium" if top.confidence >= 0.5 else "low"
    return IntentClassificationResult(
        message=message,
        intent_category=top.intent_category,
        confidence=conf_label,
        matched_by=top.matched_by,
        matched_rule=top.matched_rule,
        hints=[top.reason] if top.reason else [],
    )
