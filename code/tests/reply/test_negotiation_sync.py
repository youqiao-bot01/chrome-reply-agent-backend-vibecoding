"""谈判轮次 MySQL 同步测试。"""

from __future__ import annotations

from hubstudio_python.reply.service.rules.schema import CreatorRuleContext
from hubstudio_python.reply.service.negotiation_sync import (
    infer_effective_negotiation_round,
    priority_node_from_rule,
    strip_a_tier_negotiation_flags,
)


def test_infer_reject_advances_round():
    rnd, skip = infer_effective_negotiation_round(
        0,
        latest_message="No, that's too low for me",
        intent_category="GEN",
    )
    assert rnd == 1
    assert skip is False


def test_infer_flat_fee_only_skips_a2():
    rnd, skip = infer_effective_negotiation_round(
        0,
        latest_message="Flat fee only please",
        intent_category="Flat Fee",
    )
    assert rnd == 2
    assert skip is True


def test_strip_old_negotiation_flags():
    flags = ["priority_node:A1", "reply_ok", "prev_node:A2", "gmv_gt_5000"]
    assert strip_a_tier_negotiation_flags(flags) == ["reply_ok", "gmv_gt_5000"]


def test_priority_node_from_rule():
    rule = {"other_creator_conditions": ["priority_node:A3", "prev_node:A2"]}
    assert priority_node_from_rule(rule) == "A3"
