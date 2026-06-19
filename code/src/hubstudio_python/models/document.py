"""源文档：扫描 ``rag_data/kb/input`` 时的磁盘条目（路径 + 校验和）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceDocument:
    """
    :param path: 磁盘路径（供 ``python-docx`` 等按路径再读）。
    :param source_file: 仅文件名，用于 manifest 与 chunk 上的 ``source_file``。
    :param checksum: 源 ``.docx`` 文件字节的 SHA256，用于判断文件是否变更。
    """

    path: Path
    source_file: str
    checksum: str
