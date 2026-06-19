"""回复校验与 prompt 段落测试。"""

from __future__ import annotations

from hubstudio_python.reply.service.prompt_sections import get_prompt_section, load_prompt_sections
from hubstudio_python.reply.service.reply_guard import (
    ensure_linsey_signature,
    has_linsey_signature,
    validate_reply_output,
)


def test_prompt_sections_loaded():
    sections = load_prompt_sections()
    assert "system" in sections
    assert "policy" in sections
    assert "user_template" in sections
    assert "{{language}}" in sections["user_template"]


def test_validate_rejects_unauthorized_url():
    rule = {
        "applicable_shops": "toolant",
        "restore_slots": ["wa_group_gmv_gte_1k"],
        "key_information": ["WhatsApp Group Link"],
        "content": "Join us: https://chat.whatsapp.com/G8qWdKifMTw2HGrthlGlLm",
        "encontent": "Join us: https://chat.whatsapp.com/G8qWdKifMTw2HGrthlGlLm",
    }
    script = "Join us: https://chat.whatsapp.com/G8qWdKifMTw2HGrthlGlLm"
    refs = [(rule, script)]
    bad = validate_reply_output(
        "Please use https://evil.example.com/join\n\n— Linsey",
        references=refs,
        shop="toolant",
        context_text="",
        reference_bundle=script,
    )
    assert not bad.ok
    assert bad.reason == "unauthorized_url"


def test_validate_requires_key_information_url():
    rule = {
        "applicable_shops": "toolant",
        "restore_slots": ["wa_group_gmv_gte_1k"],
        "key_information": ["WhatsApp Group Link"],
        "content": "Join: https://chat.whatsapp.com/G8qWdKifMTw2HGrthlGlLm",
        "encontent": "Join: https://chat.whatsapp.com/G8qWdKifMTw2HGrthlGlLm",
    }
    url = "https://chat.whatsapp.com/G8qWdKifMTw2HGrthlGlLm"
    script = f"Join our group:\n{url}"
    refs = [(rule, script)]
    missing = validate_reply_output(
        "Thanks for your interest!\n\n— Linsey",
        references=refs,
        shop="toolant",
        context_text="",
        reference_bundle=script,
    )
    assert not missing.ok
    assert missing.reason == "missing_required_url"
    ok = validate_reply_output(
        f"Join here:\n{url}\n\n— Linsey",
        references=refs,
        shop="toolant",
        context_text="",
        reference_bundle=script,
    )
    assert ok.ok


def test_ensure_linsey_signature():
    assert ensure_linsey_signature("Hello") == "Hello\n\n— Linsey"
    assert ensure_linsey_signature("Hello\n\n— Linsey") == "Hello\n\n— Linsey"
    assert ensure_linsey_signature("Hello\n- Linsey") == "Hello\n\n— Linsey"
    assert has_linsey_signature("x\n\n— Linsey")


def test_validate_rejects_missing_signature():
    rule = {
        "applicable_shops": "toolant",
        "content": "Thanks!",
        "encontent": "Thanks!",
    }
    refs = [(rule, "Thanks!")]
    bad = validate_reply_output(
        "Thanks!",
        references=refs,
        shop="toolant",
        context_text="",
        reference_bundle="Thanks!",
    )
    assert not bad.ok
    assert bad.reason == "missing_linsey_signature"
