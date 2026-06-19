"""toolant 强意向 vs 申请样品 意图校正测试。"""

from __future__ import annotations

from hubstudio_python.models.intent_classification import classify_user_message, reload_intent_classifier
from hubstudio_python.models.toolant_intent import confirms_one_sample_two_videos, refine_toolant_intent
from hubstudio_python.models.intent_classification import IntentClassificationResult

PITCH_CTX = (
    "[2025-01-01 10:00][seller:Linsey] Would you be interested in a free sample? "
    "If yes, we'd ask for 2 short videos back—we'll run paid traffic behind them."
)


def setup_module() -> None:
    reload_intent_classifier()


def test_sample_question_is_not_strong_intent():
    r = classify_user_message(
        "How do I apply for the sample?",
        shop="toolant",
        use_llm=False,
    )
    assert r.intent_category == "Ask for samples"
    assert r.matched_by == "toolant_refine"


def test_bare_interested_without_pitch_not_strong_intent():
    r = classify_user_message("Yes, I am interested!", shop="toolant", use_llm=False)
    assert r.intent_category != "interest"
    assert r.intent_category in {"GEN", "ok"}


def test_interested_after_two_video_pitch_is_strong_intent():
    r = classify_user_message(
        "Interested!",
        shop="toolant",
        context_text=PITCH_CTX,
        use_llm=False,
    )
    assert r.intent_category == "interest"
    assert r.confidence == "high"


def test_confirms_one_sample_two_videos_explicit():
    assert confirms_one_sample_two_videos(
        message="Yes I'm in for 2 short videos",
        context_text="",
    )


def test_refine_downgrades_false_strong_intent():
    raw = IntentClassificationResult(
        message="interested",
        intent_category="interest",
        confidence="high",
        matched_by="keyword",
        matched_rule="interest",
    )
    out = refine_toolant_intent(raw, message="Interested!", context_text="")
    assert out.intent_category == "GEN"


def test_cpm_negotiation_refines_to_cpm_rate():
    msg = "I need a flat fee on top of commission. Can you do CPM? What is the rate?"
    r = classify_user_message(msg, shop="toolant", use_llm=False)
    assert r.intent_category == "CPM Rate"
    assert r.matched_by == "toolant_refine"


def test_a2_offer_matches_after_cpm_refine():
    from hubstudio_python.reply.service.rules.match import matches_rule
    from hubstudio_python.reply.service.rules.schema import CreatorRuleContext

    msg = "I need a flat fee on top of commission. Can you do CPM? What is the rate?"
    r = classify_user_message(msg, shop="toolant", use_llm=False)
    ctx = CreatorRuleContext(
        applicable_shops="toolant",
        creator_type="A-level",
        creator_progress="GEN",
        intent_category=r.intent_category,
        other_creator_conditions=["priority_node:A2", "prev_node:A1"],
    )
    rule = {
        "applicable_shops": "toolant",
        "creator_type": "A-level",
        "creator_progress": "GEN",
        "intent_category": "CPM Rate",
        "other_creator_conditions": ["priority_node:A2", "prev_node:A1"],
    }
    assert matches_rule(rule, ctx)


def test_price_too_low_after_commission_pitch_refines_to_cpm():
    ctx = "[Mon][seller:toolant] We work commission-only... Sound good?"
    r = classify_user_message("you proce is so low", shop="toolant", context_text=ctx, use_llm=False)
    assert r.intent_category == "CPM Rate"
    assert r.matched_by == "toolant_refine"


def test_price_too_low_a2_match_with_avg():
    from hubstudio_python.reply.service.rules.match import matches_rule
    from hubstudio_python.reply.service.rules.schema import CreatorRuleContext
    from hubstudio_python.reply.service.negotiation_sync import apply_negotiation_state_for_request

    ctx_text = "[Mon][seller:toolant] We work commission-only... Sound good?"
    msg = "you proce is so low"
    r = classify_user_message(msg, shop="toolant", context_text=ctx_text, use_llm=False)
    ctx = CreatorRuleContext(
        applicable_shops="toolant",
        creator_type="A-level",
        creator_progress=["negotiation_rounds=0", "未签约"],
        intent_category=r.intent_category,
    )
    apply_negotiation_state_for_request(
        ctx,
        creator_progress=ctx.creator_progress,
        latest_message=msg,
        intent_category=r.intent_category,
    )
    rule = {
        "applicable_shops": "toolant",
        "creator_type": "A-level",
        "creator_progress": "GEN",
        "intent_category": "CPM Rate",
        "other_creator_conditions": ["priority_node:A2", "prev_node:A1"],
    }
    assert "priority_node:A2" in ctx.other_creator_conditions
    assert matches_rule(rule, ctx)


def test_ai_ugc_pitch_yes_maps_to_ai_ugc_earnings():
    ctx = (
        "[seller:toolant] Easy Collab: ready-to-post AI video, scan the QR to join our WA group"
    )
    raw = IntentClassificationResult(
        message="Yes",
        intent_category="interest",
        confidence="high",
        matched_by="llm",
        matched_rule="interest",
    )
    out = refine_toolant_intent(raw, message="Yes", context_text=ctx)
    assert out.intent_category == "AI-UGC Earnings"
    assert out.matched_by == "toolant_refine"
