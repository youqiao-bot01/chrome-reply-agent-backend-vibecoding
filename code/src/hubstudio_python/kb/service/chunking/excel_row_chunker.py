"""
Excel 按行切片：表头一行，每行一条知识块。

向量化文本为 ``title`` + ``content``；业务列按中文表头映射为 ``KnowledgeChunk`` 上的英文展平字段，
写入 ``*.chunks.json`` 时无 ``extra_metadata`` 嵌套（与 Word 切片区分）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from hubstudio_python.kb.service.chunking.chunk_factory import _detect_language, _strip_field
from hubstudio_python.models import KnowledgeChunk, SourceDocument

_TITLE_ALIASES = (
    "标题",
    "主题",
    "问题",
    "场景",
    "意图",
    "分类",
    "达人可能发的话",
    "title",
    "intent",
    "scene",
)
_CONTENT_ALIASES = (
    "内容",
    "话术",
    "回复",
    "答案",
    "正文",
    "参考话术",
    "我方回复",
    "content",
    "script",
    "reply",
    "answer",
)
_ID_ALIASES = ("序号", "id", "编号", "no", "#")
_INTENT_ALIASES = ("意图", "场景", "分类", "intent_category", "intent", "section")
_LEVEL_ALIASES = ("档位", "等级", "level", "分层", "级别")
# Excel 达人类型：当前标准表为「（或关系）」「（且关系）」两列；旧表仅「二级分类-达人类型」归入且列
_EXCEL_CREATOR_TYPE_AND_HEADERS = (
    "二级分类-达人类型（且关系）",
    "二级分类-达人类型(且关系)",
    "二级分类-达人类型",
)
_EXCEL_CREATOR_TYPE_OR_HEADERS = (
    "二级分类-达人类型（或关系）",
    "二级分类-达人类型(或关系)",
)
# Excel 常见中文表头（具体列名优先于泛化别名）
_EXCEL_INTENT_HEADERS = (
    "三级分类-达人消息意图",
    "三级分类-达人消息意图中信息",
    "四级分类-达人消息意图",
    "四级分类-达人消息意图中信息",
)
_EXCEL_CREATOR_PROGRESS_HEADERS = ("三级分类-达人进度",)
_EXCEL_LEVEL_ALIASES = ("档位", "等级", "分层", "级别")
_RULE_TYPE_HEADERS = ("规则类型",)
_APPLICABLE_SHOPS_HEADERS = ("一级分类-适用店铺",)
_REPLY_FREQ_HEADERS = ("达人回复频率",)
_EMOTION_HEADERS = ("达人情绪",)
_AI_ACATION_HEADERS = (
    "AI执行动作",
    "AI动作",
    "ai动作",
)
# 「其他达人条件（且关系）」：单元格内多条件为且关系，英文键 other_creator_conditions
_OTHER_CREATOR_CONDITIONS_HEADERS = (
    "其他达人条件（且关系）",
    "其他达人条件(且关系)",
    "其他达人条件",
)
_KEY_INFORMATION_HEADERS = ("关键信息", "key information", "key_info", "关键信息说明")


@dataclass(frozen=True)
class ExcelChunkConfig:
    """``config.yaml`` → ``rag.excel``。"""

    sheet: str | None = None
    header_row: int = 1
    id_column: str | None = None
    title_column: str | None = None
    content_column: str | None = None
    intent_column: str | None = None
    level_column: str | None = None
    metadata_columns: tuple[str, ...] = ()

    @classmethod
    def from_yaml(cls, rag_section: dict[str, Any] | None) -> "ExcelChunkConfig":
        if not isinstance(rag_section, dict):
            return cls()
        raw = rag_section.get("excel")
        if not isinstance(raw, dict):
            return cls()
        meta = raw.get("metadata_columns")
        meta_cols: tuple[str, ...] = ()
        if isinstance(meta, list):
            meta_cols = tuple(str(c).strip() for c in meta if str(c).strip())
        return cls(
            sheet=_opt_str(raw.get("sheet")),
            header_row=max(1, int(raw.get("header_row", 1) or 1)),
            id_column=_opt_str(raw.get("id_column")),
            title_column=_opt_str(raw.get("title_column")),
            content_column=_opt_str(raw.get("content_column")),
            intent_column=_opt_str(raw.get("intent_column")),
            level_column=_opt_str(raw.get("level_column")),
            metadata_columns=meta_cols,
        )


def _opt_str(value: object | None) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _cell_str(value: object | None) -> str:
    if value is None:
        return ""
    return _strip_field(str(value))


def _norm_header(name: str) -> str:
    """去空白并统一常见破折号，便于与 Excel 表头对齐。"""
    s = str(name).strip()
    for ch in ("\u2013", "\u2014", "\uff0d", "\u2212"):  # en/em dash、全角连字符、减号
        s = s.replace(ch, "-")
    return re.sub(r"\s+", "", s).lower()


def _pick_column(headers: list[str], explicit: str | None, aliases: tuple[str, ...]) -> str | None:
    if explicit and explicit in headers:
        return explicit
    norm_map = {_norm_header(h): h for h in headers}
    for alias in aliases:
        key = _norm_header(alias)
        if key in norm_map:
            return norm_map[key]
    return None


def _row_dict(headers: list[str], row: tuple[object, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i, header in enumerate(headers):
        if not header:
            continue
        val = row[i] if i < len(row) else None
        text = _cell_str(val)
        if text:
            out[header] = text
    return out


def _excel_row_has_metadata_besides_title_content(
    cells: dict[str, str],
    *,
    rule_col: str | None,
    intent_col: str | None,
    level_and_col: str | None,
    level_or_col: str | None,
    shops_col: str | None,
    reply_col: str | None,
    emotion_col: str | None,
    ai_acation_col: str | None,
    other_creator_conditions_col: str | None,
    key_information_col: str | None,
    progress_col: str | None,
) -> bool:
    """标题/话术列为空时，是否仍应保留该行（例如规则类型为「AI执行动作」且其它业务列有信息）。"""
    for c in (
        rule_col,
        intent_col,
        level_and_col,
        level_or_col,
        shops_col,
        reply_col,
        emotion_col,
        ai_acation_col,
        other_creator_conditions_col,
        key_information_col,
        progress_col,
    ):
        if c and _strip_field(cells.get(c, "")):
            return True
    return False


def _synthesize_title_content_when_body_empty(
    cells: dict[str, str],
    *,
    id_col: str | None,
    title_col: str | None,
    content_col: str | None,
    intent_col: str | None,
    rule_col: str | None,
    level_and_col: str | None,
    level_or_col: str | None,
    shops_col: str | None,
    reply_col: str | None,
    emotion_col: str | None,
    ai_acation_col: str | None,
    other_creator_conditions_col: str | None,
    key_information_col: str | None,
    progress_col: str | None,
    excel_row_1based: int,
) -> tuple[str, str]:
    """
    标题与话术单元格为空，但业务列有值：合成可检索的 title + content（向量化仍用二者拼接）。
    title 优先：意图 / 达人进度 / 规则类型 / 达人类型；否则 ``Excel行-{行号}``。
    content：除 id 外所有非空列的 ``列名: 值``（含规则类型、AI 动作等）。
    """
    title_parts: list[str] = []
    for c in (intent_col, progress_col, rule_col, level_or_col, level_and_col, key_information_col):
        if c:
            v = _strip_field(cells.get(c, ""))
            if v:
                title_parts.append(v)
    title = " / ".join(title_parts) if title_parts else f"Excel行-{excel_row_1based}"
    lines: list[str] = []
    for k, v in cells.items():
        if id_col and k == id_col:
            continue
        if (title_col and k == title_col) or (content_col and k == content_col):
            continue
        vv = _strip_field(v)
        if vv:
            lines.append(f"{k}: {vv}")
    content = "\n".join(lines)
    if not _strip_field(content):
        content = title
    return title, content


def split_spreadsheet_by_rows(
    document: SourceDocument,
    cfg: ExcelChunkConfig | None = None,
) -> list[KnowledgeChunk]:
    """读取 ``.xlsx``，每行生成一条 ``KnowledgeChunk``（内部标记为 Excel 行以决定 ``to_dict`` 展平形状）。"""
    path = document.path
    if not path.exists() or path.suffix.lower() not in {".xlsx", ".xlsm"}:
        return []

    cfg = cfg or ExcelChunkConfig()
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        if cfg.sheet:
            if cfg.sheet not in wb.sheetnames:
                raise ValueError(
                    f"Sheet {cfg.sheet!r} not in {document.source_file}; "
                    f"available: {wb.sheetnames}"
                )
            ws = wb[cfg.sheet]
        else:
            ws = wb[wb.sheetnames[0]]

        rows = list(ws.iter_rows(values_only=True))
    finally:
        wb.close()

    if not rows:
        return []

    header_idx = cfg.header_row - 1
    if header_idx >= len(rows):
        return []

    headers = [_cell_str(c) for c in rows[header_idx]]
    headers = [h if h else f"列{i + 1}" for i, h in enumerate(headers)]

    id_col = _pick_column(headers, cfg.id_column, _ID_ALIASES)
    title_col = _pick_column(headers, cfg.title_column, _TITLE_ALIASES)
    content_col = _pick_column(headers, cfg.content_column, _CONTENT_ALIASES)
    intent_col = _pick_column(
        headers, cfg.intent_column, _EXCEL_INTENT_HEADERS + _INTENT_ALIASES
    )
    level_and_col = _pick_column(
        headers,
        cfg.level_column,
        _EXCEL_CREATOR_TYPE_AND_HEADERS + _EXCEL_LEVEL_ALIASES,
    )
    level_or_col = _pick_column(headers, None, _EXCEL_CREATOR_TYPE_OR_HEADERS)

    rule_col = _pick_column(headers, None, _RULE_TYPE_HEADERS)
    shops_col = _pick_column(headers, None, _APPLICABLE_SHOPS_HEADERS)
    reply_col = _pick_column(headers, None, _REPLY_FREQ_HEADERS)
    emotion_col = _pick_column(headers, None, _EMOTION_HEADERS)
    ai_acation_col = _pick_column(headers, None, _AI_ACATION_HEADERS)
    other_creator_conditions_col = _pick_column(
        headers, None, _OTHER_CREATOR_CONDITIONS_HEADERS
    )
    key_information_col = _pick_column(headers, None, _KEY_INFORMATION_HEADERS)
    progress_col = _pick_column(headers, None, _EXCEL_CREATOR_PROGRESS_HEADERS)

    mapped_cols = {
        c
        for c in (
            id_col,
            title_col,
            content_col,
            intent_col,
            level_and_col,
            level_or_col,
            rule_col,
            shops_col,
            reply_col,
            emotion_col,
            ai_acation_col,
            other_creator_conditions_col,
            key_information_col,
            progress_col,
        )
        if c
    }
    chunks: list[KnowledgeChunk] = []
    seq = 0
    for row_i, row in enumerate(rows[header_idx + 1 :]):
        if not row or not any(_cell_str(c) for c in row):
            continue
        cells = _row_dict(headers, row)
        if not cells:
            continue

        excel_row_1based = cfg.header_row + 1 + row_i

        title = cells.get(title_col, "") if title_col else ""
        content = cells.get(content_col, "") if content_col else ""
        if not title and not content:
            # 未识别标题/内容列时：除序号与已映射业务列外，第一列作标题，其余合并为正文
            keys = [h for h in headers if cells.get(h)]
            if not keys:
                continue
            skip = set(mapped_cols)
            body_keys = [k for k in keys if k not in skip]
            if not body_keys:
                # 仅有业务列（规则类型、意图、AI 动作等）无标题/话术列内容
                if _excel_row_has_metadata_besides_title_content(
                    cells,
                    rule_col=rule_col,
                    intent_col=intent_col,
                    level_and_col=level_and_col,
                    level_or_col=level_or_col,
                    shops_col=shops_col,
                    reply_col=reply_col,
                    emotion_col=emotion_col,
                    ai_acation_col=ai_acation_col,
                    other_creator_conditions_col=other_creator_conditions_col,
                    key_information_col=key_information_col,
                    progress_col=progress_col,
                ):
                    title, content = _synthesize_title_content_when_body_empty(
                        cells,
                        id_col=id_col,
                        title_col=title_col,
                        content_col=content_col,
                        intent_col=intent_col,
                        rule_col=rule_col,
                        level_and_col=level_and_col,
                        level_or_col=level_or_col,
                        shops_col=shops_col,
                        reply_col=reply_col,
                        emotion_col=emotion_col,
                        ai_acation_col=ai_acation_col,
                        other_creator_conditions_col=other_creator_conditions_col,
                        key_information_col=key_information_col,
                        progress_col=progress_col,
                        excel_row_1based=excel_row_1based,
                    )
                else:
                    continue
            else:
                title = cells.get(body_keys[0], "")
                content = "\n".join(f"{k}: {cells[k]}" for k in body_keys[1:] if cells.get(k))

        if not title and not content:
            if _excel_row_has_metadata_besides_title_content(
                cells,
                rule_col=rule_col,
                intent_col=intent_col,
                level_and_col=level_and_col,
                level_or_col=level_or_col,
                shops_col=shops_col,
                reply_col=reply_col,
                emotion_col=emotion_col,
                ai_acation_col=ai_acation_col,
                other_creator_conditions_col=other_creator_conditions_col,
                key_information_col=key_information_col,
                progress_col=progress_col,
            ):
                title, content = _synthesize_title_content_when_body_empty(
                    cells,
                    id_col=id_col,
                    title_col=title_col,
                    content_col=content_col,
                    intent_col=intent_col,
                    rule_col=rule_col,
                    level_and_col=level_and_col,
                    level_or_col=level_or_col,
                    shops_col=shops_col,
                    reply_col=reply_col,
                    emotion_col=emotion_col,
                    ai_acation_col=ai_acation_col,
                    other_creator_conditions_col=other_creator_conditions_col,
                    key_information_col=key_information_col,
                    progress_col=progress_col,
                    excel_row_1based=excel_row_1based,
                )
            else:
                continue

        seq += 1
        chunk_id = cells.get(id_col, str(seq)) if id_col else str(seq)
        intent = cells.get(intent_col, "") if intent_col else ""
        if not intent.strip():
            intent = "GEN"
        if level_or_col:
            creator_type = cells.get(level_or_col, "")
            creator_type_and = cells.get(level_and_col, "") if level_and_col else ""
        else:
            # 无「或关系」列（含旧表仅「二级分类-达人类型」）：仍写入 creator_type
            creator_type = cells.get(level_and_col, "") if level_and_col else "GEN"
            creator_type_and = ""
        rule_type = cells.get(rule_col, "") if rule_col else ""
        applicable_shops = cells.get(shops_col, "") if shops_col else ""
        creator_reply_frequency = cells.get(reply_col, "") if reply_col else ""
        creator_emotion = cells.get(emotion_col, "") if emotion_col else ""
        ai_acation = cells.get(ai_acation_col, "") if ai_acation_col else ""
        other_creator_conditions = (
            cells.get(other_creator_conditions_col, "")
            if other_creator_conditions_col
            else ""
        )
        key_information = (
            cells.get(key_information_col, "") if key_information_col else ""
        )
        creator_progress = cells.get(progress_col, "") if progress_col else ""

        language = _detect_language(content or title)
        entitle, encontent = ("", "")
        if language == "en":
            entitle, encontent = title, content

        chunks.append(
            KnowledgeChunk(
                id=str(chunk_id),
                intent_category=intent,
                creator_type=creator_type,
                creator_type_and=creator_type_and,
                title=title or "",
                language=language,
                content=content,
                entitle=entitle,
                encontent=encontent,
                source_file=document.source_file,
                chunk_source_type="excel",
                extra_metadata=None,
                rule_type=rule_type,
                applicable_shops=applicable_shops,
                creator_reply_frequency=creator_reply_frequency,
                creator_emotion=creator_emotion,
                ai_acation=ai_acation,
                other_creator_conditions=other_creator_conditions,
                key_information=key_information,
                creator_progress=creator_progress,
            )
        )
    return chunks
