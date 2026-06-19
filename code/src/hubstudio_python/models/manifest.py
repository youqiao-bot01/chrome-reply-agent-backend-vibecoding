"""manifest.json 中单条源文件对应的元数据。"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ManifestEntry:
    """描述某个 ``docx`` 构建产出的 chunks 文件名与切片数量等。"""

    source_file: str
    checksum: str
    output_file: str
    chunk_count: int
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
