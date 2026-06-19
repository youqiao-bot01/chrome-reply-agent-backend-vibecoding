"""离线流水线：build / embed / chroma / shop-tier-intent；增量通过各命令 ``--incremental`` 读清单。"""

from .build_chroma import build_chroma
from .build_embeddings import build_embeddings
from .build_excel_knowledge_base import build_excel_knowledge_base
from .build_incremental import (
    IncrementalBuildOutput,
    IncrementalChromaOutput,
    run_incremental_build,
    run_incremental_chroma,
    run_incremental_embed,
)
from .build_shop_tier_intent_hierarchy import (
    ShopTierIntentHierarchyResult,
    build_shop_tier_intent_hierarchy,
)
from .build_html_knowledge_base import build_html_knowledge_base
from .structure_playbook_chunks import (
    StructurePlaybookResult,
    structure_playbook_chunks,
    structure_playbook_document,
)
from .build_knowledge_base import BuildResult, build_knowledge_base

__all__ = [
    "IncrementalBuildOutput",
    "IncrementalChromaOutput",
    "run_incremental_build",
    "run_incremental_embed",
    "run_incremental_chroma",
    "build_html_knowledge_base",
    "build_knowledge_base",
    "build_excel_knowledge_base",
    "build_embeddings",
    "build_chroma",
    "build_shop_tier_intent_hierarchy",
    "ShopTierIntentHierarchyResult",
    "structure_playbook_chunks",
    "structure_playbook_document",
    "StructurePlaybookResult",
    "BuildResult",
]
