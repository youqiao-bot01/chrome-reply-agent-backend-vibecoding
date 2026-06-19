"""店铺作用域：禁止跨店知识库引用。"""

from __future__ import annotations

from hubstudio_python.reply.service.rules.match import matches_rule
from hubstudio_python.reply.service.rules.schema import CreatorRuleContext
from hubstudio_python.reply.service.rules.shop_scope import rule_allowed_for_shop


def test_toolant_rejects_linknlatch_rule():
    rule = {
        "source_file": "Linknlatch/AI话术参考.xlsx",
        "applicable_shops": ["hbhaven", "findry"],
        "intent_category": "Follower Growth",
        "creator_type": "GEN",
    }
    assert not rule_allowed_for_shop(rule, "toolant")


def test_toolant_accepts_own_rule():
    rule = {
        "source_file": "toolant/linsey-agent-playbook_2.html",
        "applicable_shops": "toolant",
        "intent_category": "Pure Commission",
        "creator_type": "A-level",
    }
    assert rule_allowed_for_shop(rule, "toolant")


def test_linknlatch_subshop_accepts_listed():
    rule = {
        "source_file": "Linknlatch/AI话术参考.xlsx",
        "applicable_shops": ["hbhaven", "findry"],
    }
    assert rule_allowed_for_shop(rule, "hbhaven")
    assert rule_allowed_for_shop(rule, "findry")
    assert not rule_allowed_for_shop(rule, "toolant")


def test_gen_rule_allowed_any_shop():
    rule = {"applicable_shops": "GEN", "source_file": "gen.txt"}
    assert rule_allowed_for_shop(rule, "toolant")
    assert rule_allowed_for_shop(rule, "hbhaven")


def test_matches_rule_enforces_shop_scope():
    ctx = CreatorRuleContext(applicable_shops="toolant", shop="toolant", creator_type="GEN")
    foreign = {
        "applicable_shops": ["hbhaven"],
        "source_file": "Linknlatch/x.xlsx",
        "creator_type": "GEN",
    }
    assert not matches_rule(foreign, ctx)
