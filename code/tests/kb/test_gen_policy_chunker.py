"""gen.txt 全局策略切片测试。"""

from __future__ import annotations

from pathlib import Path

from hubstudio_python.kb.service.chunking.gen_policy_chunker import split_gen_policy_to_records
from hubstudio_python.models import SourceDocument


def test_split_gen_policy_numbered_items():
    doc = SourceDocument(
        path=Path("gen.txt"),
        source_file="gen.txt",
        checksum="test",
    )
    doc.path.write_text(
        "1. 拒绝则标记 shop_rejected。\n"
        "2. Linknlatch 引流 Creator Portal。\n"
        "3. toolant 引流 WhatsApp。\n"
        "3. toolant 不要出现 portal 链接。\n"
        "4. 回复要简洁。\n",
        encoding="utf-8",
    )
    try:
        records = split_gen_policy_to_records(doc)
        assert len(records) == 5
        assert records[0]["applicable_shops"] == "GEN"
        assert records[0]["rule_type"] == "策略说明"
        assert records[0]["intent_category"] == "GEN"
        assert "shop_rejected" in records[0]["content"]
        assert records[2]["id"] == "GEN-3"
        assert "WhatsApp" in records[2]["content"]
        assert records[3]["id"] == "GEN-4"
    finally:
        doc.path.unlink(missing_ok=True)
