"""GEN 首次拒答劝退。"""

from __future__ import annotations

from hubstudio_python.reply.service.reject_outreach import (
    is_outreach_reject_message,
    is_toolant_a_negotiation_reject,
    try_handle_outreach_reject,
)


def test_c_level_no_gets_goodbye():
    r = try_handle_outreach_reject(
        shop="toolant",
        creator_type="C-level",
        creator_id="apieceofmines3",
        creator_name="apieceofmines3",
        latest_message="no",
        context_text="[Mon][seller:toolant] We work commission-only... Sound good?",
        language="English",
    )
    assert r is not None
    assert not r.silent
    assert "sorry" in r.reply.lower() or "遗憾" in r.reply
    assert "Linsey" in r.reply
    assert r.db_updates.get("shop_rejected") is True


def test_a_level_no_is_negotiation_not_goodbye():
    r = try_handle_outreach_reject(
        shop="toolant",
        creator_type="A-level",
        creator_id="x",
        creator_name="x",
        latest_message="no",
        context_text="[Mon][seller:toolant] We work commission-only... Sound good?",
    )
    assert r is None


def test_is_outreach_reject_no():
    assert is_outreach_reject_message("no", "")


def test_already_rejected_silent():
    r = try_handle_outreach_reject(
        shop="toolant",
        creator_type="C-level",
        creator_id="x",
        creator_name="x",
        latest_message="no",
        context_text="commission-only",
        already_rejected=True,
    )
    assert r is not None
    assert r.silent
