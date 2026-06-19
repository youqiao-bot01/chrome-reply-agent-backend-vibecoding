"""词汇表：校验 structured JSON。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hubstudio_python.models.knowledge_chunk_vocabulary import (
    KnowledgeChunkVocabulary,
    get_vocabulary,
    reload_vocabulary,
)


@dataclass(frozen=True)
class VocabularyValidateResult:
    input_path: str
    record_count: int
    error_count: int
    errors: list[dict[str, Any]]


def validate_structured_records_file(
    input_path: Path,
    *,
    yaml_path: Path | None = None,
) -> VocabularyValidateResult:
    vocab = KnowledgeChunkVocabulary.load(yaml_path) if yaml_path else reload_vocabulary()
    data = json.loads(input_path.read_text(encoding="utf-8"))
    records = data.get("records", data if isinstance(data, list) else [])
    if not isinstance(records, list):
        records = []

    all_errors: list[dict[str, Any]] = []
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue
        errs = vocab.validate_record(rec)
        for msg in errs:
            all_errors.append(
                {
                    "index": i,
                    "id": rec.get("id"),
                    "message": msg,
                }
            )

    return VocabularyValidateResult(
        input_path=str(input_path.resolve()),
        record_count=len(records),
        error_count=len(all_errors),
        errors=all_errors,
    )
