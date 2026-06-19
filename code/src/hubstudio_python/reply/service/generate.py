"""
在线回复（``hubstudio_python/reply/``）：解析前端请求 → 意图分类 → Chroma 检索 → 规则过滤 → DeepSeek 生成回复。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from hubstudio_python.kb.service.chunking.zh_translate import ChunkTranslateConfig, _post_chat_with_retry
from hubstudio_python.reply.service.rules.derive import message_to_reply_flags
from hubstudio_python.reply.service.rules.match import matches_rule
from hubstudio_python.reply.service.rules.normalize import normalize_context
from hubstudio_python.reply.service.rules.schema import CreatorRuleContext, GENERAL
from hubstudio_python.reply.service.rules.shop_scope import rule_allowed_for_shop
from hubstudio_python.config import load_paths, load_project_yaml
from hubstudio_python.kb.service.embedding import EmbeddingClient, EmbeddingConfig
from hubstudio_python.models.intent_classification import IntentClassificationResult, classify_user_message
from hubstudio_python.models.knowledge_desensitize import resolve_key_information_for_record
from hubstudio_python.models.shop_restore_values import apply_restore_slots
from hubstudio_python.reply.service.trace import ReplyTracer, stderr_trace_enabled, trace_enabled
from hubstudio_python.kb.service.storage.chroma_store import ChromaConfig, ChromaStore

_CONTEXT_LINE_RE = re.compile(
    r"^\[(?P<time>[^\]]*)\]\[(?P<role>[^\]:]+)(?::(?P<speaker>[^\]]*))?\]\s*(?P<body>.*)$",
    re.MULTILINE,
)
_JSON_META_KEYS = frozenset({
    "other_creator_conditions",
    "creator_progress",
    "applicable_shops",
    "creator_type",
    "creator_type_and",
    "key_information",
    "restore_slots",
    "intent_category",
})


@dataclass
class ReplyOptions:
    tone: str = "professional"
    length: str = "medium"
    language: str = "English"
    extra_instruction: str = ""
    max_context_messages: int = 40
    max_context_chars: int = 12000
    knowledge_top_k: int = 3


@dataclass
class GenerateReplyRequest:
    shop: str
    creator_id: str = ""
    creator_name: str = ""
    creator_type: str = GENERAL
    creator_progress: list[str] = field(default_factory=list)
    context_text: str = ""
    options: ReplyOptions = field(default_factory=ReplyOptions)
    creator_emotion: str = GENERAL
    monthly_gmv: float | None = None
    avg_video_views: int | None = None
    video_count: int = 4
    other_creator_conditions: list[str] = field(default_factory=list)
    affiliate_center_refused: bool = False
    product_id: str = ""
    campaign_id: str = ""

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> GenerateReplyRequest:
        opts_raw = data.get("optionsBase") or data.get("options") or {}
        if not isinstance(opts_raw, dict):
            opts_raw = {}
        options = ReplyOptions(
            tone=str(opts_raw.get("tone") or "professional"),
            length=str(opts_raw.get("length") or "medium"),
            language=str(opts_raw.get("language") or "English"),
            extra_instruction=str(opts_raw.get("extraInstruction") or opts_raw.get("extra_instruction") or ""),
            max_context_messages=int(opts_raw.get("maxContextMessages") or opts_raw.get("max_context_messages") or 40),
            max_context_chars=int(opts_raw.get("maxContextChars") or opts_raw.get("max_context_chars") or 12000),
            knowledge_top_k=int(opts_raw.get("knowledgeTopK") or opts_raw.get("knowledge_top_k") or 3),
        )
        prog = data.get("creatorProgress") or data.get("creator_progress") or []
        if isinstance(prog, str):
            prog = [p.strip() for p in re.split(r"[,，;；]+", prog) if p.strip()]
        elif not isinstance(prog, list):
            prog = []
        occ = data.get("otherCreatorConditions") or data.get("other_creator_conditions") or []
        if not isinstance(occ, list):
            occ = []
        from hubstudio_python.reply.service.reply_audit import parse_bool_flag

        return cls(
            shop=str(data.get("shop") or "").strip(),
            creator_id=str(data.get("creatorId") or data.get("creator_id") or "").strip(),
            creator_name=str(data.get("creatorName") or data.get("creator_name") or "").strip(),
            creator_type=str(data.get("creatorType") or data.get("creator_type") or GENERAL).strip() or GENERAL,
            creator_progress=[str(p).strip() for p in prog if str(p).strip()],
            context_text=str(data.get("contextText") or data.get("context_text") or ""),
            options=options,
            creator_emotion=str(data.get("creatorEmotion") or data.get("creator_emotion") or GENERAL),
            monthly_gmv=_float_or_none(data.get("monthlyGmv") or data.get("monthly_gmv")),
            avg_video_views=_parse_avg_video_views_from_payload(data),
            video_count=_parse_video_count_from_payload(data),
            other_creator_conditions=[str(x).strip() for x in occ if str(x).strip()],
            affiliate_center_refused=parse_bool_flag(
                data.get("affiliateCenterRefused", data.get("affiliate_center_refused"))
            ),
            product_id=str(data.get("productId") or data.get("product_id") or "").strip(),
            campaign_id=str(data.get("campaignId") or data.get("campaign_id") or "").strip(),
        )

    @property
    def mysql_lookup_key(self) -> str:
        from hubstudio_python.reply.sql.mysql_creator import resolve_mysql_creator_key

        return resolve_mysql_creator_key(self.creator_id, self.creator_name)


@dataclass
class MatchedChunk:
    chunk_id: str
    source_file: str
    intent_category: str
    rule_type: str
    distance: float | None
    answer_purpose: str
    reference_script: str


@dataclass
class GenerateReplyResult:
    ok: bool
    reply: str
    shop: str
    latest_creator_message: str
    intent_category: str
    intent_confidence: str
    intent_matched_by: str
    matched_chunks: list[MatchedChunk]
    context_used_chars: int
    silent: bool = False
    withdraw: bool = False
    db_updates: dict[str, Any] | None = None
    steps: list[dict[str, Any]] | None = None
    error: str | None = None


def _float_or_none(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _parse_avg_video_views_from_payload(data: dict[str, Any]) -> int | None:
    from hubstudio_python.reply.service.earnings_restore import parse_avg_video_views

    return parse_avg_video_views(data)


def _parse_video_count_from_payload(data: dict[str, Any]) -> int:
    from hubstudio_python.reply.service.earnings_restore import parse_video_count

    return parse_video_count(data)


def parse_context_messages(context_text: str) -> list[dict[str, str]]:
    """解析 ``[time][role:speaker] body`` 行；忽略无方括号前缀的元数据行。"""
    messages: list[dict[str, str]] = []
    for line in (context_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = _CONTEXT_LINE_RE.match(line)
        if not m:
            continue
        messages.append({
            "time": m.group("time").strip(),
            "role": m.group("role").strip().lower(),
            "speaker": (m.group("speaker") or "").strip(),
            "body": m.group("body").strip(),
        })
    return messages


def latest_creator_message(context_text: str) -> str:
    msgs = parse_context_messages(context_text)
    for item in reversed(msgs):
        if item.get("role") == "creator" and item.get("body"):
            return item["body"]
    return ""


def trim_context_text(context_text: str, *, max_chars: int, max_messages: int) -> str:
    msgs = parse_context_messages(context_text)
    if not msgs:
        raw = (context_text or "").strip()
        return raw[:max_chars] if len(raw) > max_chars else raw
    tail = msgs[-max_messages:] if max_messages > 0 else msgs
    lines = [
        f"[{m['time']}][{m['role']}{':' + m['speaker'] if m['speaker'] else ''}] {m['body']}"
        for m in tail
    ]
    out = "\n".join(lines)
    if len(out) > max_chars:
        return out[-max_chars:]
    return out


def build_rule_context(
    req: GenerateReplyRequest,
    *,
    intent_category: str,
    intent_result: IntentClassificationResult | None = None,
) -> CreatorRuleContext:
    flags = list(req.other_creator_conditions)
    latest = latest_creator_message(req.context_text)
    for f in message_to_reply_flags(latest):
        if f not in flags:
            flags.append(f)
    if intent_result and intent_result.matched_rule:
        from hubstudio_python.models.intent_classification import get_intent_classifier

        clf = get_intent_classifier(req.shop)
        for rule in clf.rules:
            if rule.id == intent_result.matched_rule:
                for f in rule.typical_co_conditions:
                    if f not in flags:
                        flags.append(f)
                break
    if req.shop.strip().lower() == "toolant":
        from hubstudio_python.models.toolant_intent import enrich_toolant_match_context

        flags, inferred_emotion = enrich_toolant_match_context(
            message=latest,
            context_text=req.context_text,
            flags=flags,
            creator_emotion=req.creator_emotion,
        )
        if inferred_emotion and inferred_emotion != GENERAL:
            req.creator_emotion = inferred_emotion
    progress: str | list[str]
    if req.creator_progress:
        progress = req.creator_progress if len(req.creator_progress) > 1 else req.creator_progress[0]
    else:
        progress = GENERAL
    ctx = CreatorRuleContext(
        applicable_shops=req.shop,
        creator_type=req.creator_type,
        creator_progress=progress,
        intent_category=intent_category,
        creator_emotion=req.creator_emotion,
        other_creator_conditions=flags,
        shop=req.shop,
        monthly_gmv=req.monthly_gmv,
        avg_video_views=req.avg_video_views,
        latest_message=latest,
    )
    ctx = normalize_context(ctx)
    if req.shop.strip():
        ctx.applicable_shops = req.shop.strip()
    return ctx


def _parse_meta_field(key: str, val: object) -> object:
    if val is None:
        return None
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        if key in _JSON_META_KEYS and s.startswith("["):
            try:
                parsed = json.loads(s)
                return parsed
            except json.JSONDecodeError:
                return s
        return s
    return val


def chroma_metadata_to_rule(meta: dict[str, Any]) -> dict[str, Any]:
    rule: dict[str, Any] = {}
    for k, v in meta.items():
        if k in {"original_id", "title", "content", "entitle", "encontent", "language"}:
            rule[k] = v
            continue
        if k.startswith("col_"):
            continue
        parsed = _parse_meta_field(k, v)
        if parsed is not None and parsed != "":
            rule[k] = parsed
    return rule


def _reference_body(rule: dict[str, Any]) -> str:
    return str(rule.get("encontent") or rule.get("content") or "").strip()


def _prepare_reference_script(rule: dict[str, Any], shop: str, *, req: GenerateReplyRequest | None = None) -> str:
    body = _reference_body(rule)
    if not body:
        return ""
    rec = dict(rule)
    rec.setdefault("applicable_shops", shop)
    ki = resolve_key_information_for_record(rec, shop)
    if ki:
        rec["key_information"] = ki
    extra = None
    if req is not None:
        from hubstudio_python.reply.service.earnings_restore import earnings_restore_extra_for_request

        extra = earnings_restore_extra_for_request(req, rule=rule)
    return apply_restore_slots(body, shop, extra=extra or None)


def _retrieval_where(ctx: CreatorRuleContext) -> dict[str, Any]:
    """向量召回预过滤：仅店铺（+ 高置信 intent）；progress/type 交给 ``matches_rule``。"""
    data = ctx.to_match_dict()
    clauses: list[dict[str, Any]] = []
    shop = data.get("applicable_shops")
    if shop and str(shop).strip().upper() != GENERAL:
        shop_eq = str(shop).strip()
        clauses.append(
            {
                "$or": [
                    {"applicable_shops": {"$eq": shop_eq}},
                    {"applicable_shops": {"$eq": "GEN"}},
                ]
            }
        )
    intent = data.get("intent_category")
    if intent and str(intent).strip().upper() != GENERAL:
        clauses.append({"intent_category": {"$eq": str(intent).strip()}})
    if not clauses:
        return {}
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def retrieve_matching_chunks(
    req: GenerateReplyRequest,
    ctx: CreatorRuleContext,
    *,
    store: ChromaStore | None = None,
    embed_client: EmbeddingClient | None = None,
    tracer: ReplyTracer | None = None,
) -> tuple[list[tuple[dict[str, Any], float | None]], str]:
    """
    Chroma 向量召回 + ``matches_rule`` 精筛。

    :return: (规则 dict 列表, query 文本)
    """
    query_text = latest_creator_message(req.context_text) or req.context_text.strip()
    if not query_text:
        if tracer:
            tracer.record("知识库检索", "无 query 文本，跳过向量检索")
        return [], query_text

    if store is None:
        store = ChromaStore(ChromaConfig.from_env())
    if embed_client is None:
        embed_client = EmbeddingClient(EmbeddingConfig.from_env())

    vectors = embed_client.embed_texts([query_text])
    if not vectors:
        if tracer:
            tracer.record("知识库检索", "embedding 失败，无向量")
        return [], query_text

    top_k = max(1, req.options.knowledge_top_k)
    fetch_n = min(max(top_k * 8, 20), 80)

    where = _retrieval_where(ctx)
    query_kwargs: dict[str, Any] = {
        "query_embeddings": vectors,
        "n_results": fetch_n,
        "include": ["metadatas", "documents", "distances"],
    }
    if where:
        query_kwargs["where"] = where

    where_fallback = False
    try:
        raw = store.collection.query(**query_kwargs)
    except Exception as exc:
        where_fallback = True
        shop_only = {"applicable_shops": {"$eq": req.shop}} if req.shop else None
        raw = store.collection.query(
            query_embeddings=vectors,
            n_results=fetch_n,
            include=["metadatas", "documents", "distances"],
            **({"where": shop_only} if shop_only else {}),
        )
        if tracer:
            tracer.record(
                "Chroma 预过滤",
                "intent+shop 组合 where 查询失败，回退仅 shop 过滤",
                error=str(exc),
                fallback_where=shop_only,
            )

    metas = (raw.get("metadatas") or [[]])[0]
    dists = (raw.get("distances") or [[]])[0]
    ids = (raw.get("ids") or [[]])[0]

    def _chunk_brief(rule: dict[str, Any], dist: float | None, cid: str) -> dict[str, Any]:
        return {
            "chunk_id": cid or str(rule.get("original_id") or rule.get("id") or ""),
            "intent_category": rule.get("intent_category"),
            "rule_type": rule.get("rule_type"),
            "distance": round(dist, 4) if dist is not None else None,
            "answer_purpose": (str(rule.get("answer_purpose") or ""))[:120],
        }

    def _collect(*, strict: bool) -> tuple[list[tuple[dict[str, Any], float | None, str]], list[dict[str, Any]]]:
        out: list[tuple[dict[str, Any], float | None, str]] = []
        rejected: list[dict[str, Any]] = []
        for idx, meta in enumerate(metas):
            if not isinstance(meta, dict):
                continue
            rule = chroma_metadata_to_rule(meta)
            dist = dists[idx] if idx < len(dists) else None
            cid = str(meta.get("original_id") or (ids[idx] if idx < len(ids) else ""))
            if not rule_allowed_for_shop(rule, req.shop):
                rejected.append({**_chunk_brief(rule, dist, cid), "reject_reason": "shop_scope"})
                continue
            if strict and not matches_rule(rule, ctx):
                rejected.append({**_chunk_brief(rule, dist, cid), "reject_reason": "matches_rule"})
                continue
            out.append((rule, dist, cid))
        out.sort(key=lambda x: (x[1] if x[1] is not None else 999.0))
        return out, rejected

    scored_strict, rejected_strict = _collect(strict=True)
    used_fallback = False
    if not scored_strict:
        used_fallback = True
        scored, rejected_relaxed = _collect(strict=False)
    else:
        scored = scored_strict
        rejected_relaxed = []

    final = [(r, d) for r, d, _ in scored[:top_k]]

    if tracer:
        tracer.record(
            "知识库检索",
            f"向量召回 {len(metas)} 条 → strict 命中 {len(scored_strict)} 条"
            + (f" → 放宽规则后 {len(scored)} 条" if used_fallback else "")
            + f" → 取 top {len(final)}",
            query=query_text[:300],
            chroma_where=where or "(none)",
            where_fallback=where_fallback,
            fetch_n=fetch_n,
            top_k=top_k,
            rule_context=ctx.to_match_dict(),
            vector_hits=len(metas),
            strict_pass=len(scored_strict),
            relaxed_pass=len(scored) if used_fallback else None,
            rejected_by_rule_sample=rejected_strict[:5],
            selected=[_chunk_brief(r, d, str(r.get("original_id") or r.get("id") or "")) for r, d in final],
        )

    return final, query_text


def _ai_config_for_reply() -> ChunkTranslateConfig:
    paths = load_paths()
    ai = load_project_yaml(paths.project_root).get("ai")
    if not isinstance(ai, dict):
        raise ValueError("config.yaml 缺少 ai 节，无法调用 DeepSeek 生成回复")
    cfg = ChunkTranslateConfig.from_ai_yaml_section(ai)
    if cfg is None:
        raise ValueError("config.yaml ai.api_key 未配置")
    return cfg


def _participant_list(context_text: str) -> str:
    names: list[str] = []
    seen: set[str] = set()
    for msg in parse_context_messages(context_text):
        speaker = (msg.get("speaker") or msg.get("role") or "").strip()
        if speaker and speaker not in seen:
            seen.add(speaker)
            names.append(speaker)
    return ", ".join(names) if names else "(unknown)"


def _format_knowledge_blocks(
    references: list[tuple[dict[str, Any], str]],
    shop: str,
) -> tuple[str, str, str]:
    """返回 (chunk_bodies, key_information, bundle_for_validation)。"""
    bodies: list[str] = []
    ki_lines: list[str] = []
    bundle_parts: list[str] = []
    for i, (rule, script) in enumerate(references, start=1):
        rec = dict(rule)
        rec.setdefault("applicable_shops", shop)
        ki = resolve_key_information_for_record(rec, shop) or []
        bodies.append(
            "\n".join(
                [
                    f"#### Chunk {i}",
                    f"intent_category: {rule.get('intent_category', '')}",
                    f"answer_purpose: {rule.get('answer_purpose', '')}",
                    f"reference_content:\n{script}",
                ]
            )
        )
        if ki:
            from hubstudio_python.reply.service.reply_guard import required_urls_for_top_rule

            urls = required_urls_for_top_rule(rule, shop, script=script)
            for tag in ki:
                line = f"- {tag}"
                if urls:
                    line += f": {urls[0]}"
                ki_lines.append(line)
        bundle_parts.append(script)
    chunk_section = "\n\n".join(bodies) if bodies else "(none)"
    ki_section = "\n".join(ki_lines) if ki_lines else "(none)"
    return chunk_section, ki_section, "\n".join(bundle_parts)


def _intent_mode_block(intent_category: str) -> str:
    from hubstudio_python.reply.service.prompt_sections import get_prompt_section

    ic = (intent_category or "").strip()
    if ic in {"GEN", "No obvious intention", "no_obvious_intention", "其他", "other"}:
        return get_prompt_section("intent_other_neutral")
    return ""


def _build_reply_prompt(
    req: GenerateReplyRequest,
    ctx: CreatorRuleContext,
    *,
    intent_result: Any,
    references: list[tuple[dict[str, Any], str]],
    context_snippet: str,
) -> tuple[str, str]:
    from hubstudio_python.reply.service.prompt_sections import get_prompt_section

    lang = req.options.language or "English"
    tone = req.options.tone or "professional"
    length = req.options.length or "medium"
    length_key = f"length_hint.{length}" if length in {"short", "medium", "long"} else "length_hint.medium"
    length_hint = get_prompt_section(length_key, default="About 3–5 short lines.")

    chunk_bodies, key_info, _bundle = _format_knowledge_blocks(references, req.shop)
    intent_block = _intent_mode_block(intent_result.intent_category)

    from hubstudio_python.reply.service.global_policy import format_global_policy_for_prompt

    global_policy = format_global_policy_for_prompt(shop=req.shop)

    system_parts = [
        get_prompt_section("system"),
        "",
        get_prompt_section("policy"),
    ]
    if global_policy:
        system_parts.extend(["", global_policy])
    if intent_block:
        system_parts.extend(["", intent_block])
    system = "\n".join(p for p in system_parts if p).strip()

    user_tpl = get_prompt_section("user_template")
    user = (
        user_tpl.replace("{{language}}", lang)
        .replace("{{tone}}", tone)
        .replace("{{length}}", length)
        .replace("{{length_hint}}", length_hint)
        .replace("{{creator_emotion}}", req.creator_emotion or GENERAL)
        .replace("{{extra_instruction}}", req.options.extra_instruction.strip() or "(none)")
        .replace("{{participant_list}}", _participant_list(context_snippet))
        .replace("{{message_count}}", str(len(parse_context_messages(context_snippet))))
        .replace("{{context}}", context_snippet)
    )
    user += (
        "\n\n### Knowledge chunk bodies\n"
        f"{chunk_bodies}\n\n"
        "### Key information\n"
        f"{key_info}\n"
    )
    if intent_block:
        user += f"\n{intent_block}\n"
    if req.avg_video_views is not None:
        from hubstudio_python.reply.service.earnings_restore import cpm_summary_for_prompt

        user += (
            "\n\n### CPM earnings inputs (avgVV = average video views, NOT monthly GMV)\n"
            f"{cpm_summary_for_prompt(req.avg_video_views, video_count=req.video_count)}\n"
            "When reference_content includes per-video or monthly $ estimates, "
            "you MUST include those exact dollar amounts in your reply — do not ask the creator "
            "to share avg views if avgVV is already provided above.\n"
        )
    return system, user


def _finalize_reply(
    reply: str,
    *,
    references: list[tuple[dict[str, Any], str]],
    shop: str,
    context_snippet: str,
    reference_bundle: str,
    tracer: ReplyTracer | None,
    gen_mode: str,
    restore_extra: dict[str, str] | None = None,
) -> tuple[str, str, bool]:
    """apply_restore_slots + 署名 + 校验；失败则尝试 Top1 参考脚本，仍失败则空回复。"""
    from hubstudio_python.reply.service.reply_guard import ensure_linsey_signature, validate_reply_output

    body = apply_restore_slots((reply or "").strip(), shop, extra=restore_extra or None)
    body = ensure_linsey_signature(body)
    validation = validate_reply_output(
        body,
        references=references,
        shop=shop,
        context_text=context_snippet,
        reference_bundle=reference_bundle,
    )
    if validation.ok:
        if tracer:
            tracer.record(
                "回复校验",
                "通过（— Linsey 署名 + key_information 落地）",
                gen_mode=gen_mode,
                validation=validation.__dict__,
            )
        return body, gen_mode, False

    if references:
        script_reply = ensure_linsey_signature(references[0][1].strip())
        v2 = validate_reply_output(
            script_reply,
            references=references,
            shop=shop,
            context_text=context_snippet,
            reference_bundle=reference_bundle,
        )
        if v2.ok:
            if tracer:
                tracer.record(
                    "回复校验",
                    f"LLM 未通过({validation.reason})，回退 Top1 参考脚本",
                    gen_mode="reference_script",
                    failed_validation=validation.__dict__,
                )
            return script_reply, "reference_script", False

    if tracer:
        tracer.record(
            "回复校验",
            f"拒绝返回前端：{validation.reason}",
            gen_mode=gen_mode,
            validation=validation.__dict__,
        )
    return "", gen_mode, True


def _with_steps(result: GenerateReplyResult, tracer: ReplyTracer | None) -> GenerateReplyResult:
    if tracer and tracer.steps:
        result.steps = tracer.to_dict_list()
    return result


def _finalize_with_audit(
    result: GenerateReplyResult,
    req: GenerateReplyRequest,
    *,
    context_snippet: str,
    message_input_ai: str = "",
    matched_rules: list[tuple[dict[str, Any], float | None]] | None = None,
) -> GenerateReplyResult:
    from hubstudio_python.reply.service.reply_audit import persist_ai_reply_log

    try:
        log = persist_ai_reply_log(
            shop=req.shop,
            creator_name=req.creator_name or req.mysql_lookup_key,
            context_text=req.context_text or context_snippet,
            message_input_ai=message_input_ai,
            reply_result=result.reply or "",
            creator_emotion=req.creator_emotion,
            product_id=req.product_id,
            campaign_id=req.campaign_id,
            matched_rules=matched_rules,
        )
    except Exception as exc:
        log = {"inserted": False, "reason": str(exc) or exc.__class__.__name__}
    updates = dict(result.db_updates or {})
    updates["ai_reply_log"] = log
    result.db_updates = updates
    return result


def generate_reply(payload: dict[str, Any]) -> GenerateReplyResult:
    """主入口：前端 JSON → 回复文本。"""
    from hubstudio_python.reply.sql.creator_state import (
        apply_post_reply_updates,
        merge_db_into_request,
    )

    tracer = ReplyTracer(enabled=stderr_trace_enabled(payload))
    req = GenerateReplyRequest.from_payload(payload)

    tracer.record(
        "解析请求",
        f"shop={req.shop or '(empty)'}",
        shop=req.shop,
        creator_id=req.creator_id or None,
        creator_name=req.creator_name or None,
        creator_type=req.creator_type,
        creator_progress=req.creator_progress or None,
        creator_emotion=req.creator_emotion,
        monthly_gmv=req.monthly_gmv,
        avg_video_views=req.avg_video_views,
        video_count=req.video_count,
        other_creator_conditions=req.other_creator_conditions or None,
        options=asdict(req.options),
        context_message_count=len(parse_context_messages(req.context_text)),
        context_chars=len(req.context_text or ""),
    )

    if not req.shop:
        return _with_steps(
            GenerateReplyResult(
                ok=False,
                reply="",
                shop="",
                latest_creator_message="",
                intent_category="GEN",
                intent_confidence="low",
                intent_matched_by="none",
                matched_chunks=[],
                context_used_chars=0,
                error="missing shop",
            ),
            tracer,
        )

    req_before_db = asdict(req)
    req, db_state = merge_db_into_request(req)
    latest = latest_creator_message(req.context_text)

    if db_state:
        merged_fields = []
        for key in ("creator_type", "creator_progress", "creator_emotion", "monthly_gmv", "other_creator_conditions"):
            before = req_before_db.get(key)
            after = getattr(req, key)
            if before != after:
                merged_fields.append(key)
        tracer.record(
            "数据库合并",
            f"命中 creator_id={req.creator_id}，补全 {len(merged_fields)} 个字段",
            creator_id=req.creator_id,
            shop=req.shop,
            db_state=db_state.to_dict(),
            merged_fields=merged_fields or None,
            after_merge={
                "creator_type": req.creator_type,
                "creator_progress": req.creator_progress,
                "creator_emotion": req.creator_emotion,
                "monthly_gmv": req.monthly_gmv,
                "other_creator_conditions": req.other_creator_conditions,
            },
        )
    elif req.creator_id:
        tracer.record("数据库合并", "无历史记录，使用请求内字段", creator_id=req.creator_id)
    else:
        tracer.record("数据库合并", "未传 creatorId，跳过 DB 读取")

    if req.affiliate_center_refused:
        tracer.record(
            "联盟中心拒答",
            "affiliateCenterRefused=true，劝退不生成回复",
            product_id=req.product_id or None,
            campaign_id=req.campaign_id or None,
        )
        result = _with_steps(
            GenerateReplyResult(
                ok=True,
                reply="",
                shop=req.shop,
                latest_creator_message=latest,
                intent_category="GEN",
                intent_confidence="high",
                intent_matched_by="affiliate_center",
                matched_chunks=[],
                context_used_chars=0,
                silent=True,
                withdraw=True,
                error="affiliate_center_refused",
            ),
            tracer,
        )
        return _finalize_with_audit(
            result,
            req,
            context_snippet=req.context_text,
            message_input_ai="withdraw:affiliate_center_refused",
        )

    if db_state and db_state.shop_rejected:
        tracer.record(
            "拒绝预检",
            "DB 已标记 shop_rejected，静默返回",
            shop_rejected=True,
            creator_progress=db_state.creator_progress,
        )
        return _with_steps(
            GenerateReplyResult(
                ok=True,
                reply="",
                shop=req.shop,
                latest_creator_message=latest,
                intent_category="GEN",
                intent_confidence="high",
                intent_matched_by="db",
                matched_chunks=[],
                context_used_chars=0,
                silent=True,
                db_updates={"shop_rejected": True, "reason": "already_rejected"},
            ),
            tracer,
        )

    from hubstudio_python.reply.service.reject_outreach import try_handle_outreach_reject

    reject_flow = try_handle_outreach_reject(
        shop=req.shop,
        creator_type=req.creator_type,
        creator_id=req.creator_id,
        creator_name=req.creator_name,
        latest_message=latest,
        context_text=req.context_text,
        language=req.options.language,
        already_rejected=bool(db_state and db_state.shop_rejected),
    )
    if reject_flow is not None:
        if reject_flow.silent:
            tracer.record("拒绝预检", "DB 已标记 shop_rejected，静默返回")
            result = _with_steps(
                GenerateReplyResult(
                    ok=True,
                    reply="",
                    shop=req.shop,
                    latest_creator_message=latest,
                    intent_category="Reject",
                    intent_confidence="high",
                    intent_matched_by="gen_policy",
                    matched_chunks=[],
                    context_used_chars=0,
                    silent=True,
                    db_updates=reject_flow.db_updates,
                    error="already_rejected",
                ),
                tracer,
            )
        else:
            tracer.record(
                "GEN 拒答",
                "首次拒绝 → 劝退话术 + shop_rejected",
                db_updates=reject_flow.db_updates,
            )
            result = _with_steps(
                GenerateReplyResult(
                    ok=True,
                    reply=reject_flow.reply,
                    shop=req.shop,
                    latest_creator_message=latest,
                    intent_category="Reject",
                    intent_confidence="high",
                    intent_matched_by="gen_policy",
                    matched_chunks=[],
                    context_used_chars=len(req.context_text or ""),
                    db_updates=reject_flow.db_updates,
                ),
                tracer,
            )
        return _finalize_with_audit(
            result,
            req,
            context_snippet=req.context_text,
            message_input_ai="gen_policy:outreach_reject_first_time",
        )

    context_snippet = trim_context_text(
        req.context_text,
        max_chars=req.options.max_context_chars,
        max_messages=req.options.max_context_messages,
    )
    derived_flags = message_to_reply_flags(latest)
    tracer.record(
        "上下文解析",
        f"最新达人消息 {len(latest)} 字，截断后 {len(context_snippet)} 字",
        latest_creator_message=latest or "(empty)",
        derived_flags_from_message=derived_flags or None,
        context_snippet_preview=context_snippet[:400] if context_snippet else None,
    )

    wa_flow = None
    if latest and req.shop.strip().lower() == "toolant":
        from hubstudio_python.reply.service.toolant_whatsapp import try_handle_whatsapp_response

        wa_flow = try_handle_whatsapp_response(
            shop=req.shop,
            creator_id=req.creator_id,
            creator_name=req.creator_name,
            latest_message=latest,
            creator_progress=req.creator_progress,
            monthly_gmv=req.monthly_gmv,
        )
        if wa_flow:
            tracer.record(
                "WhatsApp 反馈",
                f"{wa_flow.response_type} → join_WA={wa_flow.join_wa_value} → {wa_flow.conversion_beat}",
                wa_flow=wa_flow.to_dict(),
                mysql=wa_flow.mysql,
            )

    intent_result = classify_user_message(
        latest or req.context_text[:500],
        shop=req.shop,
        context_text=context_snippet,
    )
    from hubstudio_python.models.toolant_creator_type import is_toolant_head_tier

    if (
        req.shop.strip().lower() == "toolant"
        and is_toolant_head_tier(req.creator_type)
        and intent_result.intent_category != "CPM Rate"
    ):
        from dataclasses import replace

        from hubstudio_python.models.toolant_intent import is_a1_commission_price_reject

        if is_a1_commission_price_reject(message=latest, context_text=context_snippet):
            intent_result = replace(
                intent_result,
                intent_category="CPM Rate",
                confidence="high",
                matched_by="toolant_refine",
                matched_rule="CPM Rate",
                hints=[*intent_result.hints, "generate_force_a2_cpm_after_commission_reject"],
            )
    tracer.record(
        "意图分类",
        f"{intent_result.intent_category} ({intent_result.confidence}, {intent_result.matched_by})",
        input_message=(latest or req.context_text[:500])[:300],
        intent_category=intent_result.intent_category,
        confidence=intent_result.confidence,
        matched_by=intent_result.matched_by,
        matched_rule=intent_result.matched_rule or None,
        hints=intent_result.hints or None,
    )

    ctx = build_rule_context(
        req,
        intent_category=intent_result.intent_category or "GEN",
        intent_result=intent_result,
    )
    neg_state = None
    if req.shop.strip().lower() == "toolant":
        from hubstudio_python.reply.service.negotiation_sync import apply_negotiation_state_for_request

        neg_state = apply_negotiation_state_for_request(
            ctx,
            creator_id=req.mysql_lookup_key,
            creator_progress=req.creator_progress,
            latest_message=latest,
            intent_category=intent_result.intent_category or "GEN",
        )
    tracer.record(
        "规则上下文",
        "组装 CreatorRuleContext 供 matches_rule 精筛",
        match_dict=ctx.to_match_dict(),
        creator_type=ctx.creator_type,
        creator_progress=ctx.creator_progress,
        other_creator_conditions=ctx.other_creator_conditions or None,
        negotiation=neg_state,
    )

    matched_rules: list[tuple[dict[str, Any], float | None]] = []
    message_input_ai = ""

    if wa_flow:
        db_updates: dict[str, Any] | None = {"whatsapp": wa_flow.to_dict()}
        if wa_flow.mysql:
            db_updates["mysql_join_wa"] = wa_flow.mysql
        message_input_ai = f"whatsapp_flow:{wa_flow.response_type}"
        result = _with_steps(
            GenerateReplyResult(
                ok=True,
                reply=wa_flow.reply,
                shop=req.shop,
                latest_creator_message=latest,
                intent_category=intent_result.intent_category,
                intent_confidence=intent_result.confidence,
                intent_matched_by=intent_result.matched_by,
                matched_chunks=[],
                context_used_chars=len(context_snippet),
                db_updates=db_updates,
            ),
            tracer,
        )
        return _finalize_with_audit(
            result,
            req,
            context_snippet=context_snippet,
            message_input_ai=message_input_ai,
        )

    matched_rules, query_text = retrieve_matching_chunks(req, ctx, tracer=tracer)
    strict_rules = [(r, d) for r, d in matched_rules if matches_rule(r, ctx)]
    if matched_rules and not strict_rules:
        tracer.record(
            "知识库精筛",
            f"向量召回 {len(matched_rules)} 条，strict matches_rule 命中 0 条 — 不生成回复",
            relaxed_only=True,
        )
    matched_rules = strict_rules

    references: list[tuple[dict[str, Any], str]] = []
    matched_chunks: list[MatchedChunk] = []
    top_restore_extra: dict[str, str] = {}
    for rule, dist in matched_rules:
        script = _prepare_reference_script(rule, req.shop, req=req)
        if script:
            references.append((rule, script))
        if not top_restore_extra and req.avg_video_views is not None:
            from hubstudio_python.reply.service.earnings_restore import earnings_restore_extra_for_request

            top_restore_extra = earnings_restore_extra_for_request(req, rule=rule)
        matched_chunks.append(
            MatchedChunk(
                chunk_id=str(rule.get("original_id") or rule.get("id") or ""),
                source_file=str(rule.get("source_file") or ""),
                intent_category=str(rule.get("intent_category") or ""),
                rule_type=str(rule.get("rule_type") or ""),
                distance=dist,
                answer_purpose=str(rule.get("answer_purpose") or ""),
                reference_script=script[:240],
            )
        )

    tracer.record(
        "参考话术",
        f"命中 {len(matched_chunks)} 条，可用脚本 {len(references)} 条",
        references=[
            {
                "chunk_id": str(r.get("original_id") or r.get("id") or ""),
                "intent_category": r.get("intent_category"),
                "rule_type": r.get("rule_type"),
                "script_preview": s[:200],
            }
            for r, s in references
        ] or None,
    )

    if not references:
        from hubstudio_python.reply.service.prompt_sections import get_prompt_section

        tracer.record("生成回复", get_prompt_section("knowledge_empty") or "无知识库 strict 命中，不回复")
        result = _with_steps(
            GenerateReplyResult(
                ok=True,
                reply="",
                shop=req.shop,
                latest_creator_message=latest,
                intent_category=intent_result.intent_category,
                intent_confidence=intent_result.confidence,
                intent_matched_by=intent_result.matched_by,
                matched_chunks=matched_chunks,
                context_used_chars=len(context_snippet),
                silent=True,
                error="no_knowledge_match",
            ),
            tracer,
        )
        return _finalize_with_audit(
            result,
            req,
            context_snippet=context_snippet,
            message_input_ai=message_input_ai or context_snippet,
            matched_rules=matched_rules,
        )

    _, _, reference_bundle = _format_knowledge_blocks(references, req.shop)
    top_rule = matched_rules[0][0] if matched_rules else {}
    from hubstudio_python.reply.service.earnings_restore import (
        earnings_restore_extra_for_request,
        reply_lacks_cpm_numbers,
        rule_needs_avgvv,
    )

    restore_extra = earnings_restore_extra_for_request(req, rule=top_rule) or top_restore_extra
    use_filled_reference = bool(
        references
        and restore_extra
        and rule_needs_avgvv(top_rule)
    )

    if use_filled_reference:
        reply_raw = references[0][1]
        gen_mode = "reference_script"
        message_input_ai = f"reference_script:\n{reply_raw[:8000]}"
        tracer.record(
            "DeepSeek 润色",
            "命中 avgVV 话术，直接使用已填数的参考脚本",
            avg_video_views=req.avg_video_views,
            video_count=req.video_count,
            restore_extra=restore_extra,
            script_preview=reply_raw[:300],
        )
    else:
        cfg = _ai_config_for_reply()
        system, user = _build_reply_prompt(
            req,
            ctx,
            intent_result=intent_result,
            references=references,
            context_snippet=context_snippet,
        )
        message_input_ai = f"SYSTEM:\n{system}\n\nUSER:\n{user}"
        tracer.record(
            "DeepSeek 润色",
            f"模型 {cfg.model}，参考 {len(references)} 条话术",
            model=cfg.model,
            system_preview=system[:300],
            user_preview=user[:500],
        )
        reply_raw = _post_chat_with_retry(cfg, system, user).strip()
        gen_mode = "deepseek"
        if (
            references
            and restore_extra
            and rule_needs_avgvv(top_rule)
            and reply_lacks_cpm_numbers(reply_raw, extra=restore_extra)
        ):
            reply_raw = references[0][1]
            gen_mode = "reference_script"
            tracer.record(
                "DeepSeek 润色",
                "LLM 未写入 CPM 金额，回退已填数的参考脚本",
                restore_extra=restore_extra,
            )

    reply, gen_mode, rejected = _finalize_reply(
        reply_raw,
        references=references,
        shop=req.shop,
        context_snippet=context_snippet,
        reference_bundle=reference_bundle,
        tracer=tracer,
        gen_mode=gen_mode,
        restore_extra=restore_extra or None,
    )
    if rejected:
        result = _with_steps(
            GenerateReplyResult(
                ok=True,
                reply="",
                shop=req.shop,
                latest_creator_message=latest,
                intent_category=intent_result.intent_category,
                intent_confidence=intent_result.confidence,
                intent_matched_by=intent_result.matched_by,
                matched_chunks=matched_chunks,
                context_used_chars=len(context_snippet),
                silent=True,
                error="reply_validation_failed",
            ),
            tracer,
        )
        return _finalize_with_audit(
            result,
            req,
            context_snippet=context_snippet,
            message_input_ai=message_input_ai,
            matched_rules=matched_rules,
        )
    tracer.record("生成完成", f"模式={gen_mode}，返回 {len(reply)} 字", reply_preview=reply[:300])

    db_updates = None
    quote_updates = None
    if req.mysql_lookup_key:
        top_rule = matched_rules[0][0] if matched_rules else {}
        db_updates = apply_post_reply_updates(
            creator_id=req.mysql_lookup_key,
            shop=req.shop,
            intent_category=intent_result.intent_category,
            matched_rule=intent_result.matched_rule,
            ai_acation=str(top_rule.get("ai_acation") or top_rule.get("ai_action") or ""),
            creator_name=req.creator_name,
            latest_message=latest,
            generated_reply=reply,
            matched_rule_record=top_rule,
            ctx=ctx,
        )
        if db_updates and db_updates.get("ai_acation"):
            tracer.record(
                "AI执行动作",
                str(db_updates["ai_acation"].get("action") or ""),
                effect=db_updates["ai_acation"],
            )
        from hubstudio_python.reply.service.quote_extract import try_extract_and_save_expert_quote

        quote_updates = try_extract_and_save_expert_quote(
            creator_id=req.mysql_lookup_key,
            shop=req.shop,
            ctx=ctx,
            latest_message=latest,
            context_text=req.context_text,
            generated_reply=reply,
            intent_category=intent_result.intent_category,
            matched_rules=matched_rules,
        )
        if quote_updates and quote_updates.get("parsed") and db_updates is None:
            db_updates = {}
        if quote_updates and db_updates is not None:
            db_updates["expert_quote"] = quote_updates
        tracer.record(
            "报价解析",
            quote_updates.get("scenario", "未触发") if quote_updates else "未触发讨价还价写库",
            quote=quote_updates,
        )
        from hubstudio_python.reply.sql.mysql_config import mysql_config_from_env

        mysql_cfg = mysql_config_from_env()
        tracer.record(
            "数据库写回",
            "有变更" if db_updates else "无变更（未命中需写库的 AI 动作/谈判节点）",
            lookup_key=req.mysql_lookup_key,
            mysql_id_column=mysql_cfg.creator_id_column if mysql_cfg.enabled else None,
            ai_acation=str(top_rule.get("ai_acation") or top_rule.get("ai_action") or "") or None,
            db_updates=db_updates,
        )
    else:
        tracer.record("数据库写回", "未传 creatorId/creatorName，跳过")

    return _finalize_with_audit(
        _with_steps(
            GenerateReplyResult(
                ok=True,
                reply=reply,
                shop=req.shop,
                latest_creator_message=latest,
                intent_category=intent_result.intent_category,
                intent_confidence=intent_result.confidence,
                intent_matched_by=intent_result.matched_by,
                matched_chunks=matched_chunks,
                context_used_chars=len(context_snippet),
                db_updates=db_updates,
            ),
            tracer,
        ),
        req,
        context_snippet=context_snippet,
        message_input_ai=message_input_ai,
        matched_rules=matched_rules,
    )


def _fallback_scripts(matched: list[tuple[dict[str, Any], float | None]], shop: str) -> list[str]:
    out: list[str] = []
    for rule, _ in matched:
        script = _prepare_reference_script(rule, shop)
        if script:
            out.append(script)
    return out


def result_to_dict(result: GenerateReplyResult) -> dict[str, Any]:
    d = asdict(result)
    d["matched_chunks"] = [asdict(c) for c in result.matched_chunks]
    return d
