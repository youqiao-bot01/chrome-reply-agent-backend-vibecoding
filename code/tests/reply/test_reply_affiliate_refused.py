"""回复劝退与审计日志。"""

from __future__ import annotations

from hubstudio_python.reply import GenerateReplyRequest, generate_reply


def test_affiliate_center_refused_withdraws():
    result = generate_reply(
        {
            "shop": "toolant",
            "creatorName": "demo",
            "affiliateCenterRefused": True,
            "productId": "12345",
            "campaignId": "camp-1",
            "contextText": "[Mon][creator]hello",
        }
    )
    assert result.ok
    assert result.withdraw is True
    assert result.reply == ""
    assert result.error == "affiliate_center_refused"
    assert result.db_updates is not None
    assert "ai_reply_log" in result.db_updates
