from pathlib import Path

from hubstudio_python.config import bootstrap_rag_env
from hubstudio_python.kb.service.pipelines.authoritative_sources import (
    authoritative_materializers,
    resolve_structure_supplements,
)
from hubstudio_python.kb.service.pipelines.playbook_supplement_materializer import (
    apply_authoritative_supplement_records,
)


def test_toolant_add1_materializer():
    paths = bootstrap_rag_env()
    records: list[dict] = [{"source_chunk_id": "WA-Joined", "title": "old", "content": "old"}]
    applied = apply_authoritative_supplement_records(
        records,
        shop="toolant",
        doc_dir=paths.input_dir,
        project_root=paths.project_root,
    )
    assert len(applied) == 23
    assert not any(str(r.get("source_chunk_id", "")).startswith("WA-Joined") for r in records)
    assert any(r.get("source_chunk_id") == "Add1-1-WA-Invite-Gte1k" for r in records)
    assert any(r.get("source_chunk_id") == "Add1-2-Affiliate-Lt1k" for r in records)
    assert any(r.get("source_chunk_id") == "Add1-3-AI-UGC-FirstVideoBonus" for r in records)
    joined = [r for r in records if r.get("source_chunk_id") == "Add1-4-WA-Joined-SampleReview"]
    assert len(joined) == 1
    assert joined[0]["ai_acation"] == "join_wa_1"
    assert "我加了" in joined[0]["title"]
    assert joined[0]["creator_progress"] == "已申请批样"
    for r in records:
        flags = r.get("other_creator_conditions") or []
        assert not any(str(f).startswith("gmv_") for f in flags), flags


def test_manifest_resolves_toolant_add1():
    paths = bootstrap_rag_env()
    files = resolve_structure_supplements("toolant", project_root=paths.project_root)
    assert "toolant/add1.txt" in files
    mats = authoritative_materializers("toolant", project_root=paths.project_root)
    assert any(m.materializer == "toolant_add1" for m in mats)


if __name__ == "__main__":
    test_manifest_resolves_toolant_add1()
    test_toolant_add1_materializer()
    print("ok")
