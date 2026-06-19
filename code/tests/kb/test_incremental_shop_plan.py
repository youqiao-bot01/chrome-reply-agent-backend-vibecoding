"""incremental：supplement 登记 → 自动 rebuild 所属店 playbook。"""

from __future__ import annotations

from pathlib import Path

from hubstudio_python.kb.service.ingest.incremental_targets import (
    IncrementalChanges,
    _resolve_declared_source_file,
)
from hubstudio_python.kb.service.pipelines.incremental_shop_plan import plan_incremental_build

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_add1_modified_expands_to_toolant_playbook(tmp_path):
    plan = plan_incremental_build(
        IncrementalChanges(
            added=[],
            deleted=[],
            modified=["toolant/add1.txt"],
        ),
        project_root=PROJECT_ROOT,
    )
    assert plan.build_upsert_files == ["toolant/linsey-agent-playbook_2.html"]
    assert plan.chroma_source_files == ["toolant/linsey-agent-playbook_2.html"]
    assert len(plan.structure_playbooks) == 1
    assert plan.structure_playbooks[0][1] == "toolant/linsey-agent-playbook_2.html"


def test_gen_txt_unchanged(tmp_path):
    plan = plan_incremental_build(
        IncrementalChanges(added=[], deleted=[], modified=["gen.txt"]),
        project_root=PROJECT_ROOT,
    )
    assert plan.build_upsert_files == ["gen.txt"]
    assert plan.structure_playbooks == []


def test_typo_txt_html_corrected():
    pool = ["toolant/add1.txt", "gen.txt"]
    resolved, warn = _resolve_declared_source_file("toolant/add1.txt.html", pool)
    assert resolved == "toolant/add1.txt"
    assert warn and "typo corrected" in warn
