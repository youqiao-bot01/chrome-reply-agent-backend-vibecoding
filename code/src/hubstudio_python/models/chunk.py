"""知识库切片：写入 ``*.chunks.json`` 的单条记录结构。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass


_INTENT_CATEGORY_SYNONYMS: dict[str, str] = {
    "polite greeting": "No obvious intention",
}


def canonical_intent_category(raw: str) -> str:
    """将历史或表内别名意图合并为统一英文标签（大小写不敏感）。"""
    t = (raw or "").strip()
    if not t:
        return t
    return _INTENT_CATEGORY_SYNONYMS.get(t.casefold(), t)


_MULTI_VALUE_SPLIT = re.compile(r"[,，;；]+")


def _excel_cell_to_json_value(raw: str) -> str | list[str]:
    """
    写入 ``*.chunks.json`` 时：无多值分隔符则保留标量字符串；否则按 **英文/中文逗号、分号** 拆成 **JSON 数组**
    （trim 后非空段；仅一段则仍为字符串，避免 ``[\"x\"]`` 噪音）。

    适用于 ``applicable_shops``、``other_creator_conditions``、``key_information`` 等单元格内多条件列举。
    """
    s = (raw or "").strip()
    if not s:
        return ""
    if not _MULTI_VALUE_SPLIT.search(s):
        return s
    parts = [p.strip() for p in _MULTI_VALUE_SPLIT.split(s) if p.strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return parts


def _excel_json_value_absent(v: object) -> bool:
    """Excel 写出 ``*.chunks.json`` 时：视为「无此字段」则不在 JSON 里包含该键。"""
    if v is None:
        return True
    if isinstance(v, list):
        return len(v) == 0
    if isinstance(v, str):
        return not v.strip()
    return False


@dataclass(frozen=True)
class KnowledgeChunk:
    id: str
    intent_category: str
    creator_type: str  # Excel「或关系」：本字段多值=满足其一；与其它条件字段=全部须满足（AND）
    title: str
    language: str
    creator_type_and: str = ""  # Excel「且关系」：本字段多值=全部须满足；与其它条件字段亦 AND
    content: str = ""
    entitle: str = ""
    encontent: str = ""
    source_file: str = ""
    chunk_source_type: str = "word"  # word | excel
    extra_metadata: dict[str, str] | None = None  # Word 或未展平的附加列；Excel 标准表用展平字段
    # Excel 展平（与表头中文列对应，写入 *.chunks.json 为英文键）
    rule_type: str = ""
    applicable_shops: str = ""
    creator_reply_frequency: str = ""
    creator_emotion: str = ""
    ai_acation: str = ""  # Excel「AI执行动作」等；写入 chunks 键名为 ``ai_acation``
    other_creator_conditions: str = ""  # Excel「其他达人条件（且关系）」；数组内 AND，与其它条件字段亦 AND
    key_information: str = ""  # Excel「关键信息」：需并入最终回复的具体内容
    creator_progress: str = ""  # Excel「三级分类-达人进度」；与达人类型、意图并列的筛选维度

    def to_dict(self) -> dict[str, object]:
        if self.chunk_source_type == "excel":
            out: dict[str, object] = {
                "id": self.id,
                "language": self.language,
                "title": self.title,
                "content": self.content,
                "entitle": self.entitle or "",
                "encontent": self.encontent or "",
                "source_file": self.source_file,
            }
            optional_pairs: tuple[tuple[str, object], ...] = (
                ("rule_type", _excel_cell_to_json_value(self.rule_type)),
                ("applicable_shops", _excel_cell_to_json_value(self.applicable_shops)),
                ("creator_type", _excel_cell_to_json_value(self.creator_type)),
                ("creator_type_and", _excel_cell_to_json_value(self.creator_type_and)),
                ("creator_progress", _excel_cell_to_json_value(self.creator_progress)),
                ("intent_category", _excel_cell_to_json_value(self.intent_category)),
                (
                    "creator_reply_frequency",
                    _excel_cell_to_json_value(self.creator_reply_frequency),
                ),
                ("creator_emotion", _excel_cell_to_json_value(self.creator_emotion)),
                ("ai_acation", _excel_cell_to_json_value(self.ai_acation)),
                (
                    "other_creator_conditions",
                    _excel_cell_to_json_value(self.other_creator_conditions),
                ),
                ("key_information", _excel_cell_to_json_value(self.key_information)),
            )
            for key, val in optional_pairs:
                if not _excel_json_value_absent(val):
                    out[key] = val
            return out
        data = asdict(self)
        data.pop("chunk_source_type", None)
        for k in (
            "rule_type",
            "applicable_shops",
            "creator_reply_frequency",
            "creator_emotion",
            "ai_acation",
            "other_creator_conditions",
            "creator_type_and",
            "creator_progress",
            "key_information",
        ):
            if not data.get(k):
                data.pop(k, None)
        if not data.get("extra_metadata"):
            data.pop("extra_metadata", None)
        return data


def chunk_embedding_text(chunk: Mapping[str, object]) -> str:
    """向量化用：优先 ``entitle`` + ``encontent``，为空时回退 ``title`` + ``content``；Excel 达人进度追加在末尾。"""
    prog = str(chunk.get("creator_progress") or "").strip()
    prog_suffix = f"\n三级分类-达人进度: {prog}" if prog else ""

    entitle = str(chunk.get("entitle") or "").strip()
    encontent = str(chunk.get("encontent") or "").strip()
    en = f"{entitle}\n{encontent}".strip()
    if en:
        return (en + prog_suffix).strip()
    title = str(chunk.get("title") or "").strip()
    content = str(chunk.get("content") or "").strip()
    base = f"{title}\n{content}".strip()
    if prog_suffix:
        base = (base + prog_suffix).strip()
    return base
