"""意图识别 API 测试。"""

from __future__ import annotations

from hubstudio_python.models.intent_ranking import rank_user_message_intents
from hubstudio_python.reply.service.intent_classify import classify_creator_intent


def test_toolant_price_low_classifies_cpm():
    r = classify_creator_intent(
        {
            "shop": "toolant",
            "creatorType": "A-level",
            "creatorProgress": "未签约",
            "contextText": "[Mon][seller:toolant] We work commission-only... Sound good?\n[Mon][creator]you proce is so low",
        }
    )
    assert r.ok
    assert r.intent_category == "CPM Rate"
    assert r.creator_type == "A-level"
    api = r.to_api_dict()
    assert api["success"] is True
    assert set(api.keys()) <= {"success", "topIntents", "error"}
    assert len(api["topIntents"]) >= 1
    assert api["topIntents"][0]["intentCategory"] == "CPM Rate"
    assert api["topIntents"][0]["confidence"] >= 0.5
    for item in api["topIntents"]:
        assert item["confidence"] > 0.5
    assert len(api["topIntents"]) <= 3


def test_top_intents_respects_confidence_threshold():
    ranked = rank_user_message_intents(
        "How do I apply for the sample?",
        shop="toolant",
        creator_type="A-level",
        use_llm=False,
        allowed_intents=["Sample Request", "GEN"],
        min_confidence=0.5,
        top_k=3,
    )
    for item in ranked:
        assert item.confidence >= 0.5
    assert len(ranked) <= 3


def test_intent_api_error_response():
    api = classify_creator_intent({"shop": ""}).to_api_dict()
    assert api["success"] is False
    assert api["topIntents"] == []
    assert api.get("error")


def test_allowed_intents_non_empty_for_toolant_a():
    r = classify_creator_intent(
        {
            "shop": "toolant",
            "creatorType": "A-level",
            "contextText": "[Mon][creator]How do I apply for the sample?",
        }
    )
    assert r.ok
    assert len(r.allowed_intents) > 0
