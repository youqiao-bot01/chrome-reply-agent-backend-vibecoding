"""
命令行入口：``hubstudio-kb`` / ``python main.py <子命令>``。

约定：标准输出打印 JSON；错误信息打到 stderr 并以非零退出码结束。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from hubstudio_python.config import bootstrap_rag_env
from hubstudio_python.kb.service.pipelines import (
    build_chroma,
    build_embeddings,
    build_excel_knowledge_base,
    build_html_knowledge_base,
    build_shop_tier_intent_hierarchy,
    build_knowledge_base,
    structure_playbook_chunks,
    structure_playbook_document,
    run_incremental_build,
    run_incremental_chroma,
    run_incremental_embed,
)
from hubstudio_python.reply import generate_reply, result_to_dict, serve_fastapi
from hubstudio_python.kb.service.pipelines.vocabulary_docs import validate_structured_records_file
from hubstudio_python.models.knowledge_chunk_vocabulary import default_vocabulary_path


def _incremental_list_path(args: argparse.Namespace) -> Path | None:
    raw = getattr(args, "incremental_yaml", None)
    return Path(raw) if raw else None


def _incremental_mode(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "incremental", False) or getattr(args, "incremental_yaml", None))


def _add_incremental_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--incremental",
        action="store_true",
        help="读取 incremental_update.yaml（或 --incremental-yaml），按清单增量处理本子命令职责。",
    )
    p.add_argument(
        "--incremental-yaml",
        dest="incremental_yaml",
        metavar="PATH",
        default=None,
        help="变动清单路径（默认 rag_data/kb/incremental_update.yaml）；可与 --incremental 同用，单独指定时也启用增量。",
    )


def build_parser() -> argparse.ArgumentParser:
    """定义子命令：build / embed / chroma / shop-tier-intent。"""
    parser = argparse.ArgumentParser(prog="hubstudio-kb")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser(
        "build",
        help="切片：Word（docx 标题大纲）或 Excel（xlsx 按行），由 --source 选择。",
    )
    build_parser.add_argument(
        "--source",
        choices=("word", "excel", "html"),
        default="word",
        help="word=docx+递归html；excel=xlsx；html=仅 html（配合 --file 指定单个文件）。",
    )
    build_parser.add_argument(
        "--file",
        dest="build_file",
        metavar="NAME",
        default=None,
        help="html 源：相对 rag_data/kb/input/ 的路径或文件名，如 toolant/linsey-agent-playbook_2.html",
    )
    _add_incremental_args(build_parser)

    embed_parser = subparsers.add_parser("embed", help="由 chunks.json 生成 embeddings.json。")
    _add_incremental_args(embed_parser)

    chroma_parser = subparsers.add_parser("chroma", help="将 embeddings 写入 ChromaDB。")
    _add_incremental_args(chroma_parser)

    st_parser = subparsers.add_parser(
        "shop-tier-intent",
        help="由 output 下全部 *.chunks.json 生成 shop_tier_intent_hierarchy.json（店铺→档位→达人进度→意图数组）。",
    )
    st_parser.add_argument(
        "--output",
        dest="shop_tier_intent_output",
        metavar="NAME",
        default="shop_tier_intent_hierarchy.json",
        help="输出文件名（默认 rag_data/kb/output/shop_tier_intent_hierarchy.json）。",
    )

    sp_parser = subparsers.add_parser(
        "structure-playbook",
        help="DeepSeek Playbook 流水线：文档解释 → schema → 结构化记录。",
    )
    sp_parser.add_argument(
        "--mode",
        choices=("document", "chunks"),
        default="document",
        help="document=整篇 HTML 一次分析（推荐）；chunks=先切块再逐块结构化。",
    )
    sp_parser.add_argument(
        "--html-file",
        dest="structure_html_file",
        metavar="PATH",
        default="toolant/linsey-agent-playbook_2.html",
        help="document 模式：相对 rag_data/kb/input/ 的 HTML 路径。",
    )
    sp_parser.add_argument(
        "--supplement",
        dest="structure_supplements",
        action="append",
        metavar="PATH",
        default=None,
        help="document 模式：补充说明（可多次指定，相对 rag_data/kb/input/）。",
    )
    sp_parser.add_argument(
        "--input",
        dest="structure_input",
        metavar="NAME",
        default="toolant-linsey-agent-playbook-2.chunks.json",
        help="chunks 模式：输入 chunks 文件名（默认 rag_data/kb/output/ 下）。",
    )
    sp_parser.add_argument(
        "--output",
        dest="structure_output",
        metavar="NAME",
        default=None,
        help="输出文件名（默认 <input>.structured.chunks.json）。",
    )
    sp_parser.add_argument(
        "--skip-analyze",
        action="store_true",
        help="跳过 DeepSeek 文档解释，复用 output/*.analysis.json 后合并 schema 并结构化。",
    )
    sp_parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="仅文档解释 + 合并 vocabulary/intent/restore（不产出 structured.full.json）。",
    )

    vocab_parser = subparsers.add_parser(
        "vocabulary",
        help="字段词汇表：渲染维护文档 / 校验 structured JSON。",
    )
    vocab_parser.add_argument(
        "--sync-from-chunks",
        dest="vocabulary_sync_from",
        nargs="?",
        const="",
        metavar="FILE",
        default=None,
        help="从 *.chunks.json 合并枚举到 vocabulary.yaml（默认 ai话术参考.chunks.json）",
    )
    vocab_parser.add_argument(
        "--init",
        action="store_true",
        help="合并 seed、restore；缺失时对 kb/input/<shop>/ DeepSeek 生成 intent_classification",
    )
    vocab_parser.add_argument(
        "--force",
        action="store_true",
        help="与 --init 联用：覆盖已有 schema 文件",
    )
    vocab_parser.add_argument(
        "--skip-generate-intent",
        action="store_true",
        help="与 --init 联用：不调用 DeepSeek 生成 intent_classification",
    )
    vocab_parser.add_argument(
        "--force-intent",
        action="store_true",
        help="与 --init 联用：强制重生成全部店铺的 intent_classification",
    )
    vocab_parser.add_argument(
        "--generate-intent",
        dest="vocabulary_generate_intent",
        metavar="SHOP",
        nargs="?",
        const="",
        default=None,
        help="DeepSeek 读 kb/input/<shop>/ 生成 schema/intent/<shop>.yaml（默认全部 kb/input 店铺）",
    )
    vocab_parser.add_argument(
        "--sync-restore-values",
        dest="vocabulary_sync_restore",
        metavar="SHOP",
        nargs="?",
        const="toolant",
        default=None,
        help="从 rag_data/kb/input/ 源文件抽取 URL 写入 schema/restore/<shop>.yaml（默认 toolant）",
    )
    vocab_parser.add_argument(
        "--list-conditions",
        action="store_true",
        help="列出规则匹配字段与 other_creator_conditions 标志分组",
    )
    vocab_parser.add_argument(
        "--validate",
        dest="vocabulary_validate",
        metavar="PATH",
        default=None,
        help="校验 structured JSON（records 数组）字段取值是否在词汇表内。",
    )

    intent_parser = subparsers.add_parser(
        "intent",
        help="达人消息 → intent_category 归类（见 schema/intent/generic.yaml + intent/<shop>.yaml）",
    )
    intent_parser.add_argument(
        "--classify",
        dest="intent_classify",
        metavar="MESSAGE",
        default=None,
        help="分析一条达人消息，输出 intent_category 及匹配依据",
    )
    intent_parser.add_argument(
        "--shop",
        dest="intent_shop",
        metavar="SHOP",
        default=None,
        help="店铺 slug（加载 intent/generic + intent/<shop>.yaml）",
    )
    intent_parser.add_argument(
        "--context",
        dest="intent_context",
        metavar="TEXT",
        default=None,
        help="可选会话上下文，供 LLM 消歧",
    )
    intent_parser.add_argument(
        "--llm",
        dest="intent_use_llm",
        action="store_true",
        help="强制使用 DeepSeek 做意图分类（跳过关键词）",
    )
    intent_parser.add_argument(
        "--no-llm",
        dest="intent_no_llm",
        action="store_true",
        help="禁用 LLM，仅 exact/关键词",
    )
    intent_parser.add_argument(
        "--list",
        dest="intent_list",
        action="store_true",
        help="列出全部 intent 规则（不分析消息）",
    )

    reply_parser = subparsers.add_parser(
        "reply",
        help="根据会话上下文 + 知识库检索 + DeepSeek 生成达人回复",
    )
    reply_parser.add_argument(
        "--request",
        dest="reply_request",
        metavar="PATH",
        default="-",
        help="JSON 请求文件路径；默认 ``-`` 表示从 stdin 读取",
    )
    reply_parser.add_argument(
        "--no-trace",
        action="store_true",
        help="关闭分步追踪（默认打印到 stderr，并在 JSON 中返回 steps）",
    )

    reply_serve_parser = subparsers.add_parser(
        "reply-serve",
        help="启动 FastAPI 服务：POST /api/reply（JSON）→ 回复，内置 CORS",
    )
    reply_serve_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="监听地址（默认 127.0.0.1）",
    )
    reply_serve_parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="监听端口（默认 8765）",
    )
    reply_serve_parser.add_argument(
        "--no-trace",
        action="store_true",
        help="关闭 stderr 分步日志（默认开启；可用 ?trace=1 拿完整 JSON）",
    )

    feishu_parser = subparsers.add_parser("feishu", help="飞书日报与数据维护")
    feishu_sub = feishu_parser.add_subparsers(dest="feishu_command", required=True)

    feishu_daily = feishu_sub.add_parser("daily", help="运行自动巡航日报（统计 + 多维表格 + 群推送）")
    feishu_daily.add_argument(
        "--webhook-url",
        action="append",
        default=[],
        help="飞书机器人 webhook_url；可多次传入",
    )

    feishu_toolant = feishu_sub.add_parser("toolant", help="运行 Toolant 自动巡航日报")
    feishu_toolant.add_argument(
        "--webhook-url",
        action="append",
        default=[],
        help="飞书机器人 webhook_url；可多次传入",
    )
    feishu_toolant.add_argument("--no-send", action="store_true", help="仅同步/打印，不发送 webhook")
    feishu_toolant.add_argument("--start", help="统计开始时间：YYYY-MM-DD HH:MM:SS")
    feishu_toolant.add_argument("--end", help="统计结束时间：YYYY-MM-DD HH:MM:SS")

    feishu_dedup = feishu_sub.add_parser("dedup", help="清理 auto_reply_plugin_ai_reply_info 重复记录")
    feishu_dedup.add_argument("--dry-run", action="store_true", help="只统计将删除多少行，不执行删除")
    feishu_dedup.add_argument("--batch", type=int, default=500, help="每批 DELETE 的 id 数量")

    return parser


def main() -> None:
    """加载 ``config.yaml``（rag → 环境变量）、再加载 ``.env`` 补缺，最后分发子命令。"""
    paths = bootstrap_rag_env()
    parser = build_parser()
    args = parser.parse_args()

    try:
        if args.command == "build":
            if _incremental_mode(args):
                inc_out = run_incremental_build(paths, list_file=_incremental_list_path(args))
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "command": "build",
                            "incremental": True,
                            **asdict(inc_out),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
            source = getattr(args, "source", "word")
            if source == "excel":
                results = build_excel_knowledge_base(paths)
            elif source == "html":
                html_files = None
                one = getattr(args, "build_file", None)
                if one and str(one).strip():
                    html_files = [str(one).strip()]
                results = build_html_knowledge_base(paths, html_files=html_files)
            else:
                results = build_knowledge_base(paths)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "command": "build",
                        "source": source,
                        "count": len(results),
                        "results": [asdict(item) for item in results],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        if args.command == "embed":
            if _incremental_mode(args):
                results = run_incremental_embed(paths, list_file=_incremental_list_path(args))
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "command": "embed",
                            "incremental": True,
                            "count": len(results),
                            "results": [asdict(item) for item in results],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
            results = build_embeddings(paths.output_dir)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "command": "embed",
                        "incremental": False,
                        "count": len(results),
                        "results": [asdict(item) for item in results],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        if args.command == "chroma":
            if _incremental_mode(args):
                inc_out = run_incremental_chroma(paths, list_file=_incremental_list_path(args))
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "command": "chroma",
                            "incremental": True,
                            **asdict(inc_out),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
            result = build_chroma(paths.output_dir)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "command": "chroma",
                        "incremental": False,
                        "chunks_added": result.chunks_added,
                        "total_chunks": result.total_chunks,
                        "collection_name": result.collection_name,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        if args.command == "shop-tier-intent":
            out_name = str(
                getattr(args, "shop_tier_intent_output", "shop_tier_intent_hierarchy.json")
                or "shop_tier_intent_hierarchy.json"
            ).strip() or "shop_tier_intent_hierarchy.json"
            st = build_shop_tier_intent_hierarchy(paths.output_dir, output_filename=out_name)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "command": "shop-tier-intent",
                        **asdict(st),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        if args.command == "intent":
            from hubstudio_python.models.intent_classification import (
                classify_user_message,
                get_intent_classifier,
                resolve_intent_classification_path,
            )

            if getattr(args, "intent_list", False):
                clf = get_intent_classifier()
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "command": "intent",
                            "action": "list",
                            "yaml_path": str(resolve_intent_classification_path().resolve()),
                            "intents": [
                                {
                                    "id": r.id,
                                    "label_zh": r.label_zh,
                                    "tier": r.tier,
                                    "priority": r.priority,
                                }
                                for r in clf.rules
                            ],
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return

            msg = getattr(args, "intent_classify", None)
            if msg is not None and str(msg).strip():
                shop_arg = getattr(args, "intent_shop", None)
                use_llm: bool | None = None
                if getattr(args, "intent_use_llm", False):
                    use_llm = True
                elif getattr(args, "intent_no_llm", False):
                    use_llm = False
                ctx_arg = getattr(args, "intent_context", None)
                result = classify_user_message(
                    str(msg),
                    shop=str(shop_arg).strip() if shop_arg else None,
                    context_text=str(ctx_arg or ""),
                    use_llm=use_llm,
                )
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "command": "intent",
                            "action": "classify",
                            **asdict(result),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return

            print(
                json.dumps(
                    {
                        "ok": True,
                        "command": "intent",
                        "yaml_path": str(resolve_intent_classification_path().resolve()),
                        "hint": "使用 --classify \"消息\" 分析意图；--list 列出规则",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        if args.command == "reply":
            req_path = str(getattr(args, "reply_request", "-") or "-").strip()
            if req_path == "-":
                raw = sys.stdin.read()
            else:
                p = Path(req_path)
                if not p.is_absolute():
                    p = paths.project_root / p
                raw = p.read_text(encoding="utf-8")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("request JSON must be an object")
            if getattr(args, "no_trace", False):
                payload["trace"] = False
            result = generate_reply(payload)
            print(json.dumps(result_to_dict(result), ensure_ascii=False, indent=2))
            if not result.ok:
                raise SystemExit(1)
            return

        if args.command == "reply-serve":
            serve_fastapi(
                host=str(getattr(args, "host", "127.0.0.1") or "127.0.0.1"),
                port=int(getattr(args, "port", 8765) or 8765),
                trace=not getattr(args, "no_trace", False),
            )
            return

        if args.command == "structure-playbook":
            mode = str(getattr(args, "mode", "document") or "document").strip()
            out_name = getattr(args, "structure_output", None)
            out_name = str(out_name).strip() if out_name else None
            if mode == "document":
                html_file = str(
                    getattr(args, "structure_html_file", "toolant/linsey-agent-playbook_2.html")
                    or "toolant/linsey-agent-playbook_2.html"
                ).strip()
                from hubstudio_python.kb.service.pipelines.authoritative_sources import (
                    resolve_structure_supplements,
                )

                shop_slug = html_file.replace("\\", "/").split("/")[0]
                supplements = getattr(args, "structure_supplements", None) or []
                if not supplements:
                    supplements = resolve_structure_supplements(
                        shop_slug,
                        project_root=paths.project_root,
                    )
                sp = structure_playbook_document(
                    paths,
                    html_file=html_file,
                    supplement_files=supplements,
                    output_filename=out_name or None,
                    skip_analyze=bool(getattr(args, "skip_analyze", False)),
                    analyze_only=bool(getattr(args, "analyze_only", False)),
                )
            else:
                in_name = str(
                    getattr(args, "structure_input", "toolant-linsey-agent-playbook-2.chunks.json")
                    or "toolant-linsey-agent-playbook-2.chunks.json"
                ).strip()
                sp = structure_playbook_chunks(
                    paths,
                    input_filename=in_name,
                    output_filename=out_name or None,
                )
            payload = asdict(sp)
            if getattr(sp, "phases", None):
                payload["phases"] = list(sp.phases)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "command": "structure-playbook",
                        **payload,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

        if args.command == "vocabulary":
            if getattr(args, "init", False):
                from hubstudio_python.kb.service.pipelines.bootstrap_schema import bootstrap_schema

                br = bootstrap_schema(
                    force=bool(getattr(args, "force", False)),
                    generate_intent=not bool(getattr(args, "skip_generate_intent", False)),
                    force_intent=bool(getattr(args, "force_intent", False)),
                )
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "command": "vocabulary",
                            "action": "init",
                            **asdict(br),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
            gen_intent = getattr(args, "vocabulary_generate_intent", None)
            if gen_intent is not None:
                from hubstudio_python.kb.service.pipelines.extract_shop_vocabulary import (
                    discover_doc_shops,
                    run_shop_document_analysis,
                )

                shop_arg = str(gen_intent).strip()
                shops = [shop_arg] if shop_arg else discover_doc_shops()
                if not shops:
                    raise SystemExit("kb/input/ 下未发现店铺目录")
                results = []
                for shop in shops:
                    _, meta, apply_result = run_shop_document_analysis(
                        shop,
                        project_root=paths.project_root,
                        output_dir=paths.output_dir,
                        force=True,
                    )
                    results.append(
                        {
                            "shop": shop,
                            "analysis_path": meta.analysis_path,
                            "intent_count": meta.intent_count,
                            "intent_yaml_path": apply_result.intent_yaml_path,
                            "changes": apply_result.changes,
                        }
                    )
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "command": "vocabulary",
                            "action": "generate-intent",
                            "shops": results,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
            if getattr(args, "list_conditions", False):
                from hubstudio_python.reply.service.rules.schema import CONDITION_SCALAR_FIELDS
                from hubstudio_python.models.knowledge_chunk_vocabulary import get_vocabulary

                vocab = get_vocabulary()
                occ = vocab.field_spec("other_creator_conditions")
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "command": "vocabulary",
                            "action": "list-conditions",
                            "scalar_fields": list(CONDITION_SCALAR_FIELDS),
                            "flag_field": "other_creator_conditions",
                            "flag_groups": occ.get("groups") or {},
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
            sync_restore = getattr(args, "vocabulary_sync_restore", None)
            if sync_restore is not None:
                from hubstudio_python.kb.service.pipelines.extract_shop_restore_values import run_restore_extract_pipeline

                shop = str(sync_restore).strip() or "toolant"
                result = run_restore_extract_pipeline(shop, write_candidates=True)
                from hubstudio_python.models.shop_restore_values import load_shop_restore_slots

                load_shop_restore_slots.cache_clear()
                slots = load_shop_restore_slots(shop)
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "command": "vocabulary",
                            "action": "sync-restore-values",
                            "shop": shop,
                            "yaml_path": result.yaml_path,
                            "candidates_path": result.candidates_path,
                            "slot_count": len(slots),
                            "mapped_count": len(result.slots),
                            "unmapped_slot_ids": result.unmapped_slot_ids,
                            "slots": slots,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
            sync_from = getattr(args, "vocabulary_sync_from", None)
            if sync_from is not None:
                from hubstudio_python.kb.service.pipelines.vocabulary_sync import sync_vocabulary_from_chunks

                name = (
                    str(sync_from).strip()
                    if str(sync_from).strip()
                    else "linknlatch-ai话术参考.chunks.json"
                )
                p = Path(name)
                if not p.is_absolute():
                    p = paths.output_dir / p.name if not p.parent or str(p.parent) == "." else paths.project_root / p
                sr = sync_vocabulary_from_chunks(p)
                print(
                    json.dumps(
                        {
                            "ok": True,
                            "command": "vocabulary",
                            "action": "sync-from-chunks",
                            **asdict(sr),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
            validate_path = getattr(args, "vocabulary_validate", None)
            if validate_path:
                p = Path(str(validate_path))
                if not p.is_absolute():
                    p = paths.output_dir / p.name if p.parent == Path(".") else paths.project_root / p
                vr = validate_structured_records_file(p)
                print(
                    json.dumps(
                        {
                            "ok": vr.error_count == 0,
                            "command": "vocabulary",
                            "action": "validate",
                            **asdict(vr),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                if vr.error_count:
                    raise SystemExit(1)
                return
            print(
                json.dumps(
                    {
                        "ok": True,
                        "command": "vocabulary",
                        "yaml_path": str(default_vocabulary_path().resolve()),
                        "hint": "使用 --init 重建 schema；--list-conditions 列出匹配字段；--sync-restore-values [SHOP]；--sync-from-chunks [FILE]；--validate",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        if args.command == "feishu":
            from datetime import datetime

            from hubstudio_python.feishu.interface.dedup_cleanup import run_dedup_cleanup
            from hubstudio_python.feishu.service.daily_report import main as feishu_daily_main
            from hubstudio_python.feishu.service.toolant_report import main as feishu_toolant_main

            def _parse_feishu_dt(value: str | None) -> datetime | None:
                if not value:
                    return None
                return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")

            sub = getattr(args, "feishu_command", None)
            if sub == "daily":
                urls = [u for u in (args.webhook_url or []) if isinstance(u, str) and u.strip()]
                feishu_daily_main(webhook_urls=urls or None)
                print(json.dumps({"ok": True, "command": "feishu", "action": "daily"}, ensure_ascii=False, indent=2))
                return
            if sub == "toolant":
                urls = [u for u in (args.webhook_url or []) if isinstance(u, str) and u.strip()]
                feishu_toolant_main(
                    webhook_urls=urls or None,
                    send=not getattr(args, "no_send", False),
                    start=_parse_feishu_dt(getattr(args, "start", None)),
                    end=_parse_feishu_dt(getattr(args, "end", None)),
                )
                print(json.dumps({"ok": True, "command": "feishu", "action": "toolant"}, ensure_ascii=False, indent=2))
                return
            if sub == "dedup":
                result = run_dedup_cleanup(dry_run=bool(getattr(args, "dry_run", False)), batch=getattr(args, "batch", 500))
                print(
                    json.dumps(
                        {"ok": True, "command": "feishu", "action": "dedup", **result},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
    except Exception as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "command": args.command,
                    "error": str(error),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from error
