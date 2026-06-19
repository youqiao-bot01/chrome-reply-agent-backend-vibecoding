"""
店铺还原值抽取流水线（通用，不绑定具体文档格式或 slot 语义）。

阶段：
1. **discover** — 扫描 ``kb/input/<shop>/`` 下可读源文件
2. **extract** — 按值类型（url / percent / money）抽取候选及上下文
3. **match** — 与词汇表 ``restore_slots`` 做 slot_id ↔ 候选 的通用匹配
4. **write** — 写入 ``schema/restore/<shop>.yaml``，并可选输出 ``restore/candidates/<shop>.json``
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

import yaml

from hubstudio_python.config import _project_root
from hubstudio_python.models.knowledge_chunk_vocabulary import get_vocabulary
from hubstudio_python.models.schema_layout import restore_candidates_path
from hubstudio_python.models.shop_restore_values import shop_restore_values_path

ValueKind = Literal["url", "percent", "money", "text"]
CandidateRole = Literal["authoritative", "illustrative", "policy"]

# ── 通用值检测（与业务 slot 名无关）────────────────────────────────────────
# 通用 URL（不含中文标点，避免整行粘连）
_URL_RE = re.compile(r"https?://[^\s\"'<>()\[\]{}，,；;]+", re.I)

# 金额：支持 $5,000；避免把 $5,000 拆成 $5
_MONEY_RE = re.compile(r"\$(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")
_MONEY_VALUE_RE = re.compile(r"^\$(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)$")

# 运营补充说明（定值来源，如 add1.txt）
_AUTHORITATIVE_SOURCE_RE = re.compile(
    r"(?:^|[\\/])(?:add\d+|supplement|restore[_-]?config)(?:[._-].*)?\.(?:txt|md)$",
    re.I,
)

# Playbook / HTML 中的举例演算、Case 字段（不可写入 restore slot）
_ILLUSTRATIVE_CONTEXT_RE = re.compile(
    r"示例计算|示例|"
    r"creator_handle|monthly_gmv|avg_vv|action_taken|priority_node|prev_node|"
    r"平均\s*VV|单条封顶\s*=|单条底金\s*=|"
    r"[①②③④⑤]|"
    r"@\w+|layer\s*[ABC]\s*·",
    re.I,
)

# 策略/计价说明（$2 CPM、50% 首付等，非 restore 定值）
_POLICY_CONTEXT_RE = re.compile(
    r"PRIORITY\s*0|CPM\s*底金|CPM\s*计价|一口价\s*底金|"
    r"草稿\s*50%|发布\s*50%|首付\s*=|尾款\s*=|"
    r"\$0\.00\d+\s*/?\s*VV|"
    r"月\s*GMV\s*[><≤≥]|"
    r"100%\s*可见|四级优先链",
    re.I,
)

# 不参与还原的 CDN / 静态资源主机（通用 Web 卫生，非业务规则）
_SKIP_URL_HOST_RE = re.compile(
    r"(^|\.)("
    r"fonts\.googleapis|fonts\.gstatic|googleapis\.com|gstatic\.com|w3\.org"
    r")",
    re.I,
)

# CSS / 样式上下文中的百分比（非业务佣金）
_CSS_PERCENT_CONTEXT_RE = re.compile(
    r"width\s*:|height\s*:|font-size\s*:|margin\s*:|padding\s*:|color\s*:|top\s*:|left\s*:|right\s*:|transform\s*:",
    re.I,
)
_PERCENT_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*%")

_READABLE_SUFFIXES = frozenset({".txt", ".md", ".html", ".htm", ".json", ".csv", ".xlsx", ".xls"})


@dataclass(frozen=True)
class RestoreCandidate:
    """从源文档抽出的可还原字面量。"""

    value: str
    kind: ValueKind
    source_file: str
    context: str = ""
    role: CandidateRole = "illustrative"
    value_at: int = 0  # value 在 context 内的起始下标
    span_start: int = -1  # value 在源文件全文中的起始下标（去重/定位用）


@dataclass
class RestoreExtractResult:
    shop: str
    yaml_path: str
    slots: dict[str, str] = field(default_factory=dict)
    candidates: list[RestoreCandidate] = field(default_factory=list)
    unmapped_slot_ids: list[str] = field(default_factory=list)
    candidates_path: str = ""


# ── Stage 1: discover ────────────────────────────────────────────────────────


def discover_shop_source_files(
    shop: str,
    *,
    doc_root: Path | None = None,
) -> list[Path]:
    from hubstudio_python.models.rag_layout import kb_input_dir

    root = doc_root or kb_input_dir()
    shop_dir = root / shop
    if not shop_dir.is_dir():
        return []
    out: list[Path] = []
    for path in sorted(shop_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in _READABLE_SUFFIXES:
            out.append(path)
    return out


# ── Stage 2: extract ─────────────────────────────────────────────────────────


def _format_money(raw: str) -> str:
    return f"${raw}"


def _is_authoritative_source(source_file: str) -> bool:
    return bool(_AUTHORITATIVE_SOURCE_RE.search(source_file.replace("\\", "/")))


def _classify_candidate_role(cand: RestoreCandidate) -> CandidateRole:
    """区分运营定值（authoritative）与 Playbook 举例/策略（illustrative / policy）。"""
    if _is_authoritative_source(cand.source_file):
        return "authoritative"
    if _POLICY_CONTEXT_RE.search(cand.context):
        return "policy"
    if _ILLUSTRATIVE_CONTEXT_RE.search(cand.context):
        return "illustrative"
    if cand.source_file.lower().endswith((".html", ".htm")):
        return "illustrative"
    return "illustrative"


def _with_role(cand: RestoreCandidate) -> RestoreCandidate:
    role = _classify_candidate_role(cand)
    if role == cand.role:
        return cand
    return RestoreCandidate(
        value=cand.value,
        kind=cand.kind,
        source_file=cand.source_file,
        context=cand.context,
        role=role,
        value_at=cand.value_at,
        span_start=cand.span_start,
    )


def _normalize_url(raw: str) -> str:
    u = raw.rstrip(".,;)")
    u = re.sub(r"(?<=\d)(You|you)$", "", u)
    return u.strip()


def _html_to_plain_text(html: str) -> str:
    """HTML → 纯文本（抽取候选与上下文用，避免 div/span 标签泄漏）。"""
    if "<" not in html:
        return html
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)


def _normalize_context(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _context_window(text: str, start: int, end: int, *, radius: int = 96) -> str:
    snippet = text[max(0, start - radius) : min(len(text), end + radius)].strip()
    return _normalize_context(snippet)


def _tokenize(text: str) -> set[str]:
    normalized = text.lower().replace("-", "_")
    tokens = set(re.findall(r"[a-z0-9]+", normalized))
    for m in re.finditer(r"\d+k", normalized):
        tokens.add(m.group())
    return tokens


def _comparison_signals(text: str) -> set[str]:
    """从上下文识别通用比较方向（gte / lt），用于区分同档不同 slot。"""
    s = text.lower()
    out: set[str] = set()
    if any(x in s for x in ("以上", "gte", ">=", "≥", "above", "over", "minimum")):
        out.add("gte")
    if any(x in s for x in ("以下", "一下", "lt", "<", "below", "under", "less")):
        out.add("lt")
    return out


def extract_candidates_from_text(
    text: str,
    *,
    source_file: str,
) -> list[RestoreCandidate]:
    """从任意文本抽取 url / percent / money 候选（带上下文窗口）。"""
    seen: set[tuple[str, ValueKind]] = set()
    out: list[RestoreCandidate] = []

    def add(value: str, kind: ValueKind, start: int, end: int) -> None:
        ctx_start = max(0, start - 96)
        ctx = _normalize_context(text[ctx_start : min(len(text), end + 96)])
        value_at = start - ctx_start
        key = (value, kind, ctx[:48], start)
        if key in seen or not value.strip():
            return
        seen.add(key)
        out.append(_with_role(
            RestoreCandidate(
                value=value,
                kind=kind,
                source_file=source_file,
                context=ctx,
                value_at=value_at,
                span_start=start,
            )
        ))

    for m in _URL_RE.finditer(text):
        url = _normalize_url(m.group())
        if _SKIP_URL_HOST_RE.search(url):
            continue
        ctx = _context_window(text, m.start(), m.end())
        cand = RestoreCandidate(value=url, kind="url", source_file=source_file, context=ctx)
        if _is_asset_url_candidate(cand):
            continue
        add(url, "url", m.start(), m.end())

    for m in _PERCENT_RE.finditer(text):
        pct = f"{m.group(1)}%"
        cand = RestoreCandidate(
            value=pct,
            kind="percent",
            source_file=source_file,
            context=_context_window(text, m.start(), m.end()),
        )
        if _is_noise_percent_candidate(cand):
            continue
        add(pct, "percent", m.start(), m.end())

    for m in _MONEY_RE.finditer(text):
        val = _format_money(m.group(1))
        cand = RestoreCandidate(
            value=val,
            kind="money",
            source_file=source_file,
            context=_context_window(text, m.start(), m.end()),
        )
        if _is_noise_money_candidate(cand):
            continue
        add(val, "money", m.start(), m.end())

    return out


def extract_candidates_from_path(path: Path, *, doc_root: Path) -> list[RestoreCandidate]:
    rel = str(path.relative_to(doc_root)).replace("\\", "/")
    suffix = path.suffix.lower()

    if suffix in {".txt", ".md", ".html", ".htm", ".json", ".csv"}:
        text = path.read_text(encoding="utf-8", errors="replace")
        if suffix in {".html", ".htm"}:
            text = _html_to_plain_text(text)
        return extract_candidates_from_text(text, source_file=rel)

    if suffix in {".xlsx", ".xls"}:
        return _extract_candidates_from_spreadsheet(path, source_file=rel)

    return []


def _extract_candidates_from_spreadsheet(path: Path, *, source_file: str) -> list[RestoreCandidate]:
    try:
        import openpyxl
    except ImportError:
        return []

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    headers = [str(h or "").strip() for h in rows[0]]
    header_blob = " | ".join(headers)
    merged: list[RestoreCandidate] = []

    for row in rows:
        cells = [str(c).strip() if c is not None else "" for c in row]
        for i, cell in enumerate(cells):
            if not cell:
                continue
            col_name = headers[i] if i < len(headers) else ""
            blob = f"{header_blob}\n{col_name}\n{cell}"
            for cand in extract_candidates_from_text(blob, source_file=source_file):
                merged.append(
                    _with_role(
                        RestoreCandidate(
                            value=cand.value,
                            kind=cand.kind,
                            source_file=cand.source_file,
                            context=cand.context or cell[:200],
                            value_at=cand.value_at,
                            span_start=cand.span_start,
                        )
                    )
                )
    return merged


def extract_all_candidates(
    shop: str,
    *,
    doc_root: Path | None = None,
    extra_paths: Iterable[Path] | None = None,
) -> list[RestoreCandidate]:
    from hubstudio_python.models.rag_layout import kb_input_dir

    root = doc_root or kb_input_dir()
    paths = discover_shop_source_files(shop, doc_root=root)
    if extra_paths:
        paths = sorted(set(paths) | {Path(p) for p in extra_paths})

    merged: list[RestoreCandidate] = []
    seen: set[tuple[str, ValueKind, str, int]] = set()
    for path in paths:
        for cand in extract_candidates_from_path(path, doc_root=root):
            key = (cand.value, cand.kind, cand.source_file, cand.span_start)
            if key in seen:
                continue
            seen.add(key)
            merged.append(cand)
    return merged


# ── Stage 3: match ───────────────────────────────────────────────────────────


# 上下文含下列片段的 URL 视为页面资产，不参与还原（通用，非业务域名表）
_ASSET_CONTEXT_RE = re.compile(
    r"font|stylesheet|preconnect|\.css|w3\.org|schema\.org|rel=.stylesheet",
    re.I,
)


def _infer_slot_value_kind(slot_id: str) -> ValueKind | Literal["any"]:
    sid = slot_id.lower()
    if sid.endswith("_pct") or "commission_rate" in sid or sid.endswith("_rate"):
        return "percent"
    if "_usd" in sid or sid.endswith("_usd") or "bonus" in sid or "per_video" in sid:
        return "money"
    if any(k in sid for k in ("link", "portal", "form", "group", "url")):
        return "url"
    return "any"


def _is_asset_url_candidate(candidate: RestoreCandidate) -> bool:
    if candidate.kind != "url":
        return False
    if _ASSET_CONTEXT_RE.search(candidate.context):
        return True
    return bool(_SKIP_URL_HOST_RE.search(candidate.value))


def _is_noise_percent_candidate(candidate: RestoreCandidate) -> bool:
    if candidate.kind != "percent":
        return False
    if _CSS_PERCENT_CONTEXT_RE.search(candidate.context):
        return True
    if _is_authoritative_source(candidate.source_file):
        return False
    return bool(_POLICY_CONTEXT_RE.search(candidate.context))


def _is_noise_money_candidate(candidate: RestoreCandidate) -> bool:
    """丢弃不应进入候选清单的金额（仅针对运营补充文件）。"""
    if candidate.kind != "money":
        return False
    if not _is_authoritative_source(candidate.source_file):
        return False
    if _POLICY_CONTEXT_RE.search(candidate.context):
        return True
    if _ILLUSTRATIVE_CONTEXT_RE.search(candidate.context):
        return True
    raw = candidate.value.lstrip("$").replace(",", "")
    try:
        amount = float(raw)
    except ValueError:
        return False
    if amount >= 1000 and re.search(r"GMV|gmv|monthly", candidate.context, re.I):
        return True
    return False


def _is_valid_mapped_value(slot_id: str, value: str) -> bool:
    kind = _infer_slot_value_kind(slot_id)
    v = value.strip()
    if not v:
        return False
    if kind == "url":
        urls = _URL_RE.findall(v)
        if len(urls) != 1 or urls[0] != v:
            return False
        if _SKIP_URL_HOST_RE.search(v):
            return False
        return True
    if kind == "percent":
        return bool(_PERCENT_RE.fullmatch(v) or re.fullmatch(r"\d+(?:\.\d+)?%", v))
    if kind == "money":
        return bool(_MONEY_VALUE_RE.fullmatch(v))
    return True


def _kind_compatible(slot_kind: ValueKind | Literal["any"], cand_kind: ValueKind) -> bool:
    if slot_kind == "any":
        return True
    return slot_kind == cand_kind


def _candidate_tokens(candidate: RestoreCandidate) -> set[str]:
    return _tokenize(candidate.context) | _tokenize(candidate.value)


def _fuzzy_value_overlap(slot_tokens: set[str], value: str) -> float:
    """slot_id token 与候选值（尤其 URL 主机名）的模糊重合度。"""
    val_blob = value.lower()
    val_tokens = _tokenize(value)
    if not slot_tokens:
        return 0.0
    hits = 0.0
    for st in slot_tokens:
        if st in val_tokens:
            hits += 1.0
        elif len(st) >= 2 and st in val_blob:
            hits += 1.0
    return hits / len(slot_tokens)


def _value_local_context(candidate: RestoreCandidate, *, radius: int = 40) -> str:
    """值在 context 中的局部窗口（避免 add1 一行内多个比例互相干扰）。"""
    start, end = _value_span(candidate)
    return candidate.context[max(0, start - radius) : min(len(candidate.context), end + radius)]


def _value_span(candidate: RestoreCandidate) -> tuple[int, int]:
    pos = candidate.value_at
    if pos < 0 or pos >= len(candidate.context):
        pos = candidate.context.find(candidate.value)
    if pos < 0:
        pos = 0
    return pos, pos + len(candidate.value)


def _context_before_value(candidate: RestoreCandidate, *, radius: int = 48) -> str:
    start, _ = _value_span(candidate)
    return candidate.context[max(0, start - radius) : start]


def _context_after_value(candidate: RestoreCandidate, *, radius: int = 24) -> str:
    _, end = _value_span(candidate)
    return candidate.context[end : min(len(candidate.context), end + radius)]


def _local_context_role(text: str, value: str, *, at: int | None = None) -> set[str]:
    """在值出现位置之前的短窗口内识别 first / per / gte / lt。"""
    pos = at if at is not None and at >= 0 else text.find(value)
    if pos < 0:
        return set()
    before = text[max(0, pos - 32) : pos]
    out: set[str] = set()
    if re.search(r"第一个|首次|first|1st", before, re.I):
        out.add("first")
    if re.search(r"每发|每条|per video|each video|per post", before, re.I):
        out.add("per")
    if any(x in before for x in ("以上", "gte", ">=", "≥")):
        out.add("gte")
    if any(x in before for x in ("以下", "一下", "lt", "<")):
        out.add("lt")
    return out


def _match_score(slot_id: str, candidate: RestoreCandidate) -> float:
    slot_tokens = _tokenize(slot_id.replace("_", " "))
    if not slot_tokens:
        return 0.0
    overlap = len(slot_tokens & _candidate_tokens(candidate))
    base = overlap / len(slot_tokens)
    base += 0.35 * _fuzzy_value_overlap(slot_tokens, candidate.value)

    local = _value_local_context(candidate)
    vstart, _ = _value_span(candidate)
    before = _context_before_value(candidate)
    after = _context_after_value(candidate)
    roles = _local_context_role(candidate.context, candidate.value, at=vstart)
    ctx_cmp = roles or _comparison_signals(before)
    if "gte" in slot_tokens and "gte" in ctx_cmp:
        base += 0.25
    if "lt" in slot_tokens and "lt" in ctx_cmp:
        base += 0.25
    if "gte" in slot_tokens and "lt" in ctx_cmp:
        base -= 0.35
    if "lt" in slot_tokens and "gte" in ctx_cmp:
        base -= 0.35
    if "first" in slot_tokens and "first" in roles:
        base += 0.35
    if "per" in slot_tokens and "per" in roles:
        base += 0.35
    if "first" in slot_tokens and "per" in roles and "first" not in roles:
        base -= 0.4
    if "per" in slot_tokens and "first" in roles and "per" not in roles:
        base -= 0.4
    if _infer_slot_value_kind(slot_id) == candidate.kind:
        base += 0.05
    if _infer_slot_value_kind(slot_id) == "percent" and "commission" in slot_tokens:
        if not re.search(r"commission|佣金|rate|percent", candidate.context, re.I):
            base -= 0.4
    if "ai_video" in slot_id and "commission" in slot_id:
        if re.search(r"AI视频|ai video|带货成功", before + after, re.I):
            base += 0.65
        elif re.search(r"这条链接|挂链|affiliate", before, re.I):
            base -= 0.55
    if slot_id == "commission_rate_gmv_gte_1k":
        if re.search(r"1k以上|1k 以上", before, re.I):
            base += 0.65
        if re.search(r"1k以下|1k 以下", before, re.I):
            base -= 0.55
    if slot_id == "commission_rate_gmv_lt_1k":
        if re.search(r"这条链接", before, re.I):
            base += 0.7
        if re.search(r"1k以上|1k 以上", before, re.I):
            base -= 0.55
        if re.search(r"AI视频|ai video|带货成功", before + after, re.I):
            base -= 0.55
    return base


def match_slots_to_candidates(
    slot_ids: list[str],
    candidates: list[RestoreCandidate],
    *,
    min_score: float = 0.15,
) -> tuple[dict[str, str], dict[str, dict[str, str]], list[str], list[RestoreCandidate]]:
    """
    为每个 slot_id 选取得分最高的候选；仅 ``role=authoritative`` 参与映射。
    返回 (映射, 溯源, 未映射 slot_id, 未使用候选)。
    """
    authoritative = [c for c in candidates if c.role == "authoritative"]
    scored: list[tuple[str, RestoreCandidate, float]] = []
    for slot_id in slot_ids:
        slot_kind = _infer_slot_value_kind(slot_id)
        for cand in authoritative:
            if not _kind_compatible(slot_kind, cand.kind):
                continue
            score = _match_score(slot_id, cand)
            if score >= min_score:
                scored.append((slot_id, cand, score))

    scored.sort(key=lambda x: x[2], reverse=True)
    mapping: dict[str, str] = {}
    provenance: dict[str, dict[str, str]] = {}
    used_urls: set[str] = set()
    used_slots: set[str] = set()
    used_candidates: set[tuple[str, ValueKind, str, int]] = set()

    for slot_id, cand, _ in scored:
        if slot_id in used_slots:
            continue
        ckey = (cand.value, cand.kind, cand.source_file, cand.span_start)
        if ckey in used_candidates:
            continue
        if cand.kind == "url" and cand.value in used_urls:
            continue
        if not _is_valid_mapped_value(slot_id, cand.value):
            continue
        mapping[slot_id] = cand.value
        provenance[slot_id] = {
            "value": cand.value,
            "source_file": cand.source_file,
            "role": cand.role,
        }
        used_slots.add(slot_id)
        used_candidates.add(ckey)
        if cand.kind == "url":
            used_urls.add(cand.value)

    unmapped = [s for s in slot_ids if s not in mapping]
    unused = [c for c in authoritative if c.value not in set(mapping.values()) or c.kind != "url"]
    return mapping, provenance, unmapped, unused


def slot_ids_for_shop(shop: str) -> list[str]:
    """该店 restore_slots（overlay 声明 + 已有 yaml 键），不含其它店铺。"""
    from hubstudio_python.kb.service.pipelines.vocabulary_merge import restore_slot_ids_for_shop

    return restore_slot_ids_for_shop(shop)


# ── Stage 4: orchestrate & write ─────────────────────────────────────────────


def run_restore_extract_pipeline(
    shop: str,
    *,
    doc_root: Path | None = None,
    extra_paths: Iterable[Path] | None = None,
    min_score: float = 0.15,
    write_candidates: bool = True,
) -> RestoreExtractResult:
    candidates = extract_all_candidates(shop, doc_root=doc_root, extra_paths=extra_paths)
    slot_ids = slot_ids_for_shop(shop)
    mapping, provenance, unmapped, _unused = match_slots_to_candidates(
        slot_ids, candidates, min_score=min_score
    )

    auth_sources = sorted(
        {c.source_file for c in candidates if c.role == "authoritative"}
    )
    yaml_path = write_shop_restore_values_yaml(
        shop,
        mapping,
        merge_existing=False,
        description=(
            f"{shop} restore slots（定值来自 kb/input/{shop}/ 运营补充如 add1.txt；"
            f"Playbook HTML 内金额为示例/策略，不参与自动映射）"
        ),
    )

    candidates_path = ""
    if write_candidates:
        authoritative = [c for c in candidates if c.role == "authoritative"]
        illustrative = [c for c in candidates if c.role == "illustrative"]
        policy = [c for c in candidates if c.role == "policy"]
        cand_file = restore_candidates_path(shop)
        cand_file.parent.mkdir(parents=True, exist_ok=True)
        cand_file.write_text(
            json.dumps(
                {
                    "shop": shop,
                    "note": (
                        "authoritative=运营定死的还原值（可写入 shop_restore_values.yaml）；"
                        "illustrative/policy=Playbook 举例演算或策略说明，禁止当作 restore 定值"
                    ),
                    "authoritative_sources": auth_sources,
                    "restore_slots_mapped": mapping,
                    "slot_provenance": provenance,
                    "unmapped_slot_ids": unmapped,
                    "authoritative_candidates": [asdict(c) for c in authoritative],
                    "illustrative_candidates": [asdict(c) for c in illustrative],
                    "policy_candidates": [asdict(c) for c in policy],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        candidates_path = str(cand_file.resolve())

    return RestoreExtractResult(
        shop=shop,
        yaml_path=str(yaml_path.resolve()),
        slots=mapping,
        candidates=candidates,
        unmapped_slot_ids=unmapped,
        candidates_path=candidates_path,
    )


def write_shop_restore_values_yaml(
    shop: str,
    slots: dict[str, str],
    *,
    description: str | None = None,
    merge_existing: bool = True,
) -> Path:
    path = shop_restore_values_path(shop)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, Any] = {}
    if merge_existing and path.is_file():
        existing = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    old_slots = existing.get("slots") if isinstance(existing.get("slots"), dict) else {}
    if merge_existing:
        cleaned_old = {
            k: str(v)
            for k, v in old_slots.items()
            if k not in slots and _is_valid_mapped_value(str(k), str(v))
        }
        combined = {**cleaned_old, **slots}
    else:
        combined = dict(slots)
    combined = {k: v for k, v in combined.items() if _is_valid_mapped_value(str(k), str(v))}

    payload = {
        "shop": shop,
        "description": description or existing.get("description") or f"{shop} restore slots",
        "slots": combined,
    }
    path.write_text(
        yaml.dump(payload, allow_unicode=True, sort_keys=False, default_flow_style=False, width=120),
        encoding="utf-8",
    )
    return path


# 兼容旧调用名
def extract_shop_restore_slots(shop: str, **kwargs: Any) -> dict[str, str]:
    return run_restore_extract_pipeline(shop, **kwargs).slots
