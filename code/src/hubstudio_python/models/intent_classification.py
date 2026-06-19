"""加载 ``intent/generic.yaml`` + ``intent/<shop>.yaml``，将达人消息映射到 canonical ``intent_category``。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from hubstudio_python.models.schema_layout import (
    discover_intent_shops,
    intent_generic_path,
    intent_shop_path,
)


def default_intent_classification_path() -> Path:
    """无 shop 时的 fallback（第一个有 intent 文件的店铺）。"""
    shops = discover_intent_shops()
    if shops:
        return intent_shop_path(shops[0])
    return intent_shop_path("")


def shop_intent_classification_path(shop: str) -> Path:
    return intent_shop_path(shop)


def discover_shops_with_intent() -> list[str]:
    return discover_intent_shops()


def resolve_intent_classification_path(shop: str | None = None) -> Path:
    if shop:
        return intent_shop_path(shop)
    shops = discover_intent_shops()
    if shops:
        return intent_shop_path(shops[0])
    return intent_shop_path("")


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _merge_intent_layers(shop: str | None) -> dict[str, Any]:
    """generic + shop；同 id 时 shop 覆盖 generic。"""
    generic = _load_yaml(intent_generic_path())
    merged: dict[str, Any] = {
        "_meta": dict(generic.get("_meta") or {}),
        "intents": [],
    }
    by_id: dict[str, dict[str, Any]] = {}
    for item in generic.get("intents") or []:
        if isinstance(item, dict) and item.get("id"):
            by_id[str(item["id"])] = dict(item)
    if shop:
        shop_data = _load_yaml(intent_shop_path(shop))
        shop_meta = shop_data.get("_meta")
        if isinstance(shop_meta, dict):
            merged["_meta"] = {**merged["_meta"], **shop_meta}
            merged["_meta"]["shop"] = shop
        for item in shop_data.get("intents") or []:
            if isinstance(item, dict) and item.get("id"):
                by_id[str(item["id"])] = dict(item)
    merged["intents"] = list(by_id.values())
    return merged


@dataclass(frozen=True)
class IntentRule:
    id: str
    label_zh: str
    scenario_zh: str
    tier: str
    priority: int
    exact_replies: tuple[str, ...] = ()
    keywords_en: tuple[str, ...] = ()
    keywords_zh: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    not_this_intent: tuple[dict[str, str], ...] = ()
    typical_co_conditions: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass
class IntentClassificationResult:
    message: str
    intent_category: str
    confidence: str  # high | medium | low
    matched_by: str  # exact | keyword | llm | none
    matched_rule: str = ""
    hints: list[str] = field(default_factory=list)


class IntentClassifier:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self.meta = raw.get("_meta") or {}
        self.rules: list[IntentRule] = []
        for item in raw.get("intents") or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            self.rules.append(
                IntentRule(
                    id=str(item["id"]),
                    label_zh=str(item.get("label_zh") or ""),
                    scenario_zh=str(item.get("scenario_zh") or ""),
                    tier=str(item.get("tier") or "ABC"),
                    priority=int(item.get("priority") or 500),
                    exact_replies=tuple(str(x) for x in (item.get("exact_replies") or [])),
                    keywords_en=tuple(str(x).lower() for x in (item.get("keywords_en") or [])),
                    keywords_zh=tuple(str(x) for x in (item.get("keywords_zh") or [])),
                    examples=tuple(str(x) for x in (item.get("examples") or [])),
                    not_this_intent=tuple(
                        x for x in (item.get("not_this_intent") or []) if isinstance(x, dict)
                    ),
                    typical_co_conditions=tuple(str(x) for x in (item.get("typical_co_conditions") or [])),
                    notes=tuple(str(x) for x in (item.get("notes") or [])),
                )
            )
        self.rules.sort(key=lambda r: (-r.priority, r.id))

    @classmethod
    def load(cls, path: Path | None = None, *, shop: str | None = None) -> IntentClassifier:
        if shop:
            return cls(_merge_intent_layers(shop))
        p = path or resolve_intent_classification_path()
        if p.is_file():
            return cls(_load_yaml(p))
        shop_guess = p.stem if p.stem else None
        if shop_guess:
            return cls(_merge_intent_layers(shop_guess))
        return cls(_merge_intent_layers(None))

    def _try_exact(self, text: str, *, shop_key: str | None) -> IntentClassificationResult | None:
        from hubstudio_python.models.knowledge_chunk_vocabulary import get_vocabulary

        lower = text.lower()
        vocab = get_vocabulary()
        for rule in self.rules:
            if rule.id == "GEN":
                continue
            for exact in rule.exact_replies:
                if lower == exact.lower():
                    canon = vocab.canonicalize_intent(rule.id, shop=shop_key) or rule.id
                    return IntentClassificationResult(
                        message=text,
                        intent_category=canon,
                        confidence="high",
                        matched_by="exact",
                        matched_rule=rule.id,
                    )
        return None

    def _classify_keyword(self, text: str, *, shop_key: str | None) -> IntentClassificationResult | None:
        from hubstudio_python.models.knowledge_chunk_vocabulary import get_vocabulary

        lower = text.lower()
        vocab = get_vocabulary()
        best: IntentRule | None = None
        best_score = 0
        for rule in self.rules:
            if rule.id in {"GEN", "No obvious intention", "no_obvious_intention"}:
                continue
            score = 0
            for kw in rule.keywords_en:
                if kw in lower:
                    score += 2 if len(kw) > 4 else 1
            for kw in rule.keywords_zh:
                if kw in text:
                    score += 2
            if score > best_score:
                best_score = score
                best = rule

        if best and best_score > 0:
            canon = vocab.canonicalize_intent(best.id, shop=shop_key) or best.id
            conf = "high" if best_score >= 3 else "medium"
            return IntentClassificationResult(
                message=text,
                intent_category=canon,
                confidence=conf,
                matched_by="keyword",
                matched_rule=best.id,
                hints=[f"score={best_score}"],
            )

        if re.match(r"^(hi|hello|hey|thanks|thank you)\b", lower):
            return IntentClassificationResult(
                message=text,
                intent_category="No obvious intention",
                confidence="medium",
                matched_by="keyword",
                matched_rule="No obvious intention",
            )
        return None

    def _classify_llm(
        self,
        text: str,
        *,
        shop_key: str | None,
        context_text: str,
    ) -> IntentClassificationResult | None:
        from hubstudio_python.models.intent_llm import classify_intent_with_llm

        try:
            return classify_intent_with_llm(
                text,
                self.rules,
                shop=shop_key,
                context_text=context_text,
            )
        except Exception as exc:
            return IntentClassificationResult(
                message=text,
                intent_category="GEN",
                confidence="low",
                matched_by="none",
                hints=[f"llm_failed: {exc}"],
            )

    def classify(
        self,
        message: str,
        *,
        shop: str | None = None,
        context_text: str = "",
        use_llm: bool | None = None,
    ) -> IntentClassificationResult:
        from hubstudio_python.models.intent_llm import intent_llm_mode

        text = (message or "").strip()
        if not text:
            return IntentClassificationResult(
                message=text,
                intent_category="GEN",
                confidence="low",
                matched_by="none",
                hints=["空消息"],
            )

        shop_key = str(shop or self.meta.get("shop") or "").strip() or None

        exact = self._try_exact(text, shop_key=shop_key)
        if exact is not None:
            return exact

        mode = intent_llm_mode()
        if use_llm is True:
            mode = "always"
        elif use_llm is False:
            mode = "off"

        if mode == "always":
            llm_result = self._classify_llm(text, shop_key=shop_key, context_text=context_text)
            if llm_result and llm_result.matched_by == "llm":
                return llm_result
            kw = self._classify_keyword(text, shop_key=shop_key)
            if kw is not None:
                return kw
            return llm_result or IntentClassificationResult(
                message=text,
                intent_category="GEN",
                confidence="low",
                matched_by="none",
                hints=["未命中规则"],
            )

        kw = self._classify_keyword(text, shop_key=shop_key)
        if mode == "off":
            if kw is not None:
                return kw
            return IntentClassificationResult(
                message=text,
                intent_category="GEN",
                confidence="low",
                matched_by="none",
                hints=["未命中规则，关键词模式未启用 LLM"],
            )

        if kw is not None and kw.confidence == "high":
            return kw

        llm_result = self._classify_llm(text, shop_key=shop_key, context_text=context_text)
        if llm_result and llm_result.matched_by == "llm":
            return llm_result
        if kw is not None:
            return kw
        return llm_result or IntentClassificationResult(
            message=text,
            intent_category="GEN",
            confidence="low",
            matched_by="none",
            hints=["未命中规则，建议走向量检索或人工复核"],
        )


@lru_cache(maxsize=32)
def _cached_intent_classifier(shop_key: str) -> IntentClassifier:
    shop = None if shop_key == "__generic__" else shop_key
    return IntentClassifier.load(shop=shop)


def get_intent_classifier(shop: str | None = None) -> IntentClassifier:
    key = str(shop or "").strip() or "__generic__"
    return _cached_intent_classifier(key)


def reload_intent_classifier() -> None:
    _cached_intent_classifier.cache_clear()


def classify_user_message(
    message: str,
    *,
    shop: str | None = None,
    context_text: str = "",
    use_llm: bool | None = None,
) -> IntentClassificationResult:
    result = get_intent_classifier(shop).classify(
        message,
        shop=shop,
        context_text=context_text,
        use_llm=use_llm,
    )
    if str(shop or "").strip().lower() == "toolant":
        from hubstudio_python.models.toolant_intent import refine_toolant_intent

        result = refine_toolant_intent(result, message=message, context_text=context_text)
    return result
