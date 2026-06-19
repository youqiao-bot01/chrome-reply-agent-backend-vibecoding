"""creator_emotion 软匹配：仅不感兴趣类严格。"""

from __future__ import annotations

from hubstudio_python.reply.service.rules.match import matches_rule
from hubstudio_python.reply.service.rules.schema import CreatorRuleContext


def _rule(**kwargs: object) -> dict:
    base = {
        "applicable_shops": "toolant",
        "creator_type": "A-level",
        "creator_progress": "GEN",
        "intent_category": "GEN",
        "creator_emotion": "GEN",
    }
    base.update(kwargs)
    return base


def test_soft_interested_rule_passes_when_ctx_gen():
    ctx = CreatorRuleContext(
        applicable_shops="toolant",
        creator_type="A-level",
        creator_emotion="GEN",
    )
    rule = _rule(creator_emotion="感兴趣或同意")
    assert matches_rule(rule, ctx)


def test_strict_not_interested_rule_fails_when_ctx_gen():
    ctx = CreatorRuleContext(
        applicable_shops="toolant",
        creator_type="A-level",
        creator_emotion="GEN",
    )
    rule = _rule(creator_emotion="拒绝合作")
    assert not matches_rule(rule, ctx)


def test_strict_not_interested_rule_passes_when_ctx_matches():
    ctx = CreatorRuleContext(
        applicable_shops="toolant",
        creator_type="A-level",
        creator_emotion="拒绝合作",
    )
    rule = _rule(creator_emotion="拒绝合作")
    assert matches_rule(rule, ctx)


def test_soft_interested_rule_fails_when_ctx_rejects():
    ctx = CreatorRuleContext(
        applicable_shops="toolant",
        creator_type="A-level",
        creator_emotion="当前聊天主题不感兴趣",
    )
    rule = _rule(creator_emotion="感兴趣或同意")
    assert not matches_rule(rule, ctx)
