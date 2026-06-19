from hubstudio_python.reply.service.rules.schema import CreatorRuleContext
from hubstudio_python.reply.service.quote_extract import (
    detect_quote_scenario,
    extract_quote_for_scenario,
    extract_usd_quote_from_text,
)


def test_creator_minimum_quote():
    q = extract_usd_quote_from_text("My minimum is $50 per video")
    assert q is not None
    assert q.amount_usd == 50.0
    assert q.confidence == "high"


def test_flat_fee_agreed_from_seller_context():
    ctx = CreatorRuleContext(
        applicable_shops="toolant",
        creator_type="A-level",
        shop="toolant",
        other_creator_conditions=["priority_node:A3", "gmv_gt_5000"],
    )
    context = "[2025-01-01 10:00][seller:Linsey] that's $36 per video. Deal?"
    scenario = detect_quote_scenario(
        shop="toolant",
        ctx=ctx,
        latest_message="Deal!",
        context_text=context,
        intent_category="Flat Fee",
        matched_rules=[],
    )
    assert scenario == "flat_fee_agreed"
    ex = extract_quote_for_scenario(
        scenario=scenario,
        latest_message="Deal!",
        context_text=context,
        generated_reply="",
    )
    assert ex is not None
    assert ex.amount_usd == 36.0
    assert ex.quote_type == "flat_fee_agreed"


if __name__ == "__main__":
    test_creator_minimum_quote()
    test_flat_fee_agreed_from_seller_context()
    print("ok")
