"""从章节上下文构造 ``KnowledgeChunk``（大纲分块等共用）。"""

from __future__ import annotations

import re

from hubstudio_python.models import KnowledgeChunk


def _strip_field(text: str) -> str:
    return (text or "").strip()


def _slugify(name: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9]+", name)
    slug = "-".join(token.lower() for token in tokens)
    return slug or "knowledge"


def _detect_language(text: str) -> str:
    ascii_letters = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    non_ascii = sum(1 for ch in text if not ch.isascii())
    return "en" if ascii_letters > non_ascii else "zh"


def _infer_level(section: str, subsection: str, title: str, content: str) -> str:
    match = re.match(r"^(S|A|B)-\d{2}", title.strip(), re.IGNORECASE)
    if match:
        return match.group(1).upper()
    lowered = f"{section}\n{subsection}\n{title}\n{content}".lower()
    if "s-level" in lowered or "s 级" in lowered or "S-level" in lowered:
        return "S"
    if "a-level" in lowered or "a 级" in lowered or "A-level" in lowered:
        return "A"
    if "b-level" in lowered or "b 级" in lowered or "B-level" in lowered:
        return "B"
    return "GEN"


def make_knowledge_chunk(
    source_file: str,
    section: str,
    subsection: str,
    title: str,
    content: str,
    chunk_id: str | None = None,
    *,
    creator_type_override: str | None = None,
) -> KnowledgeChunk:
    normalized_title = _strip_field(title)
    normalized_content = _strip_field(content)
    creator_type = (
        creator_type_override
        if creator_type_override
        else _infer_level(section, subsection, normalized_title, normalized_content)
    )
    if not chunk_id:
        policy_match = re.match(r"^((S|A|B)-\d{2})", normalized_title, re.IGNORECASE)
        chunk_id = policy_match.group(1).upper() if policy_match else _slugify(normalized_title).upper()
    intent_category = _strip_field(subsection) or _strip_field(section) or "General"
    final_title = normalized_title or "Untitled"
    final_content = normalized_content
    language = _detect_language(final_content or final_title)
    if language == "en":
        entitle = final_title
        encontent = final_content
    else:
        entitle = ""
        encontent = ""
    return KnowledgeChunk(
        id=chunk_id,
        intent_category=intent_category,
        creator_type=creator_type,
        title=final_title,
        language=language,
        content=final_content,
        entitle=entitle,
        encontent=encontent,
        source_file=source_file,
    )
