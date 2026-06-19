"""toolant 达人档位：对外称呼 ↔ KB A/B/C-level 匹配。"""

from hubstudio_python.reply.service.rules.match import matches_rule
from hubstudio_python.reply.service.rules.schema import CreatorRuleContext
from hubstudio_python.reply.service.rules.normalize import normalize_context
from hubstudio_python.models.toolant_creator_type import (
    canonical_toolant_creator_type,
    expand_toolant_creator_types,
    is_toolant_ai_ugc_tier,
    is_toolant_head_tier,
    normalize_toolant_creator_type,
)


def test_canonical_ai_ugc_from_c_level():
    assert canonical_toolant_creator_type("C-level") == "AI UGC达人"
    assert canonical_toolant_creator_type("AI UGC达人") == "AI UGC达人"


def test_canonical_pure_commission_aliases():
    assert canonical_toolant_creator_type("B-level") == "纯佣带货达人"
    assert canonical_toolant_creator_type("纯佣寄样达人") == "纯佣带货达人"


def test_expand_ai_ugc_matches_c_level_kb():
    assert "C-level" in expand_toolant_creator_types("AI UGC达人")
    assert "AI UGC达人" in expand_toolant_creator_types("C-level")


def test_matches_rule_ai_ugc_user_type_vs_c_level_chunk():
    ctx = CreatorRuleContext(
        applicable_shops="toolant",
        shop="toolant",
        creator_type="AI UGC达人",
        creator_progress=["未加窗", "未申请批样"],
        intent_category="AI-UGC Earnings",
        creator_emotion="感兴趣或同意",
        other_creator_conditions=["reply_interested", "reply_yes"],
    )
    ctx = normalize_context(ctx)
    rule = {
        "applicable_shops": "toolant",
        "creator_type": "C-level",
        "creator_progress": "GEN",
        "intent_category": "AI-UGC Earnings",
        "creator_emotion": "GEN",
        "other_creator_conditions": ["reply_ai"],
    }
    assert not matches_rule(rule, ctx)

    rule_interested = {
        **rule,
        "other_creator_conditions": ["reply_interested"],
    }
    assert matches_rule(rule_interested, ctx)


def test_head_tier_from_user_name():
    assert is_toolant_head_tier("高GMV达人")
    assert is_toolant_head_tier("A-level")
    assert not is_toolant_head_tier("AI UGC达人")


def test_normalize_list():
    assert normalize_toolant_creator_type(["C-level", "AI UGC达人"]) == "AI UGC达人"


def test_is_toolant_ai_ugc_tier():
    assert is_toolant_ai_ugc_tier("AI UGC达人")
    assert is_toolant_ai_ugc_tier("C-level")
