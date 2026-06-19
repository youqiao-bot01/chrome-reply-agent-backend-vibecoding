"""ai_acation 注册表与副作用 handler 测试。"""

from __future__ import annotations

from unittest.mock import patch

from hubstudio_python.reply.sql.ai_action_effects import (
    AiActionContext,
    apply_ai_action_effects,
    normalize_ai_action,
)


def test_normalize_send_flat_fee_offer():
    assert normalize_ai_action("Send flat fee offer") == "send_flat_fee_offer"
    assert normalize_ai_action("send flat fee offer") == "send_flat_fee_offer"


def test_normalize_log_creator_quote():
    assert (
        normalize_ai_action("Log creator's minimum rate to quote sheet")
        == "log_creator_quote"
    )


def test_send_flat_fee_offer_writes_expert_quote_from_creator():
    ctx = AiActionContext(
        creator_id="c1",
        shop="toolant",
        ai_acation="Send flat fee offer",
        latest_message="My flat fee is $150 per video",
    )
    with patch("hubstudio_python.reply.sql.expert_quote.update_expert_quote") as mock_eq:
        mock_eq.return_value = {"updated": True}
        result = apply_ai_action_effects(ctx)
    assert result is not None
    assert result["action"] == "send_flat_fee_offer"
    assert result["expert_quote"] == 150.0
    mock_eq.assert_called_once_with("c1", 150.0)


def test_log_creator_quote_from_message():
    ctx = AiActionContext(
        creator_id="c2",
        shop="toolant",
        ai_acation="Log creator's minimum rate to quote sheet",
        latest_message="My minimum rate is $200 per video",
    )
    with patch("hubstudio_python.reply.sql.expert_quote.update_expert_quote") as mock_eq:
        mock_eq.return_value = {"updated": True}
        result = apply_ai_action_effects(ctx)
    assert result is not None
    assert result["action"] == "log_creator_quote"
    assert result["expert_quote"] == 200.0
    mock_eq.assert_called_once_with("c2", 200.0)


def test_unknown_action_returns_none():
    assert (
        apply_ai_action_effects(
            AiActionContext(creator_id="x", shop="toolant", ai_acation="totally_unknown_xyz")
        )
        is None
    )
