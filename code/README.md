# 代码实现部分

> 代码文档只在本目录维护。方案与 §0 规则见 [`PLAN.md`](../PLAN.md)；AI 输入见 [`ai/`](../ai/)。

## 自治原则

- 本目录（Phase 2 完成后）须能 **单独 `uv sync` + 运行**，不 import `ai/`、不读取 `PLAN.md`。
- 运行期依赖：`rag_data/kb/`（构建）、`code/assets/`（schema/prompt/运行快照）、`.env`、`chroma_db/`、`test_mock/`。
- 变更流程：先改 `PLAN.md` → 再改本目录 → 在 `ai/artifacts/` 留检验记录。

## 长任务与卡住（§0.5）

构建、测试、并行 agent、服务启动等超过预期时长时：**前台说明原因**，并 **请求授权** 后再做终端/进程检查。详见 [`PLAN.md` §0.5](../PLAN.md#05-长任务超时--卡住人机协作强制)。

## 大任务多 Agent 并发（§0.6）

Phase 级改动须拆成多 agent：各 agent **先自规划、目录独占、并发执行**；有依赖时分波（基础设施 → 模块并行 → 集成）。详见 [`PLAN.md` §0.6](../PLAN.md#06-大任务多-agent-并发人机协作强制)。

## §5 分步同意后再执行（§0.7）

每个 Phase 开工前：先输出 **思考 + 执行清单 + 检验标准** → 用户同意 → 再改代码。详见 [`PLAN.md` §0.7](../PLAN.md#07-5-分步执行先方案后动手人机协作强制)。

## 模块划分（§0.3）

| 编号 | 模块 | 目标路径 | 当前路径（过渡期） |
|------|------|----------|-------------------|
| 1.1 | 知识库数据处理 | `src/hubstudio_python/kb/` | `chunking/` `ingest/` `pipelines/` `embedding/` `storage/` |
| 1.2 | 回复内容生成 | `src/hubstudio_python/reply/` | `reply/` `conditions/` `db/` |
| 1.3 | 飞书汇报实现 | `src/hubstudio_python/feishu/` | 待自 `chrome-reply-feishu` 合并 |

各模块内：**SQL 层 → 服务层 → 接口层**（详见 `PLAN.md` §2.3、§3.3）。

## 目录布局

### 目标态（Phase 2）

```
code/
├── README.md              # 本文件
├── pyproject.toml
├── main.py                # 统一 CLI：kb / reply / feishu
├── config.yaml            # rag + ai + db + mysql + feishu + pg
├── src/hubstudio_python/
│   ├── kb/
│   ├── reply/
│   ├── feishu/
│   ├── models/
│   └── cli.py
└── tests/
    ├── kb/
    ├── reply/
    └── feishu/
```

### 过渡期（v0.1.x）

实现仍在仓库根 `src/`、`tests/`、`main.py`；Phase 2 整体迁入本目录。

## 构建与运行

```bash
cd code
uv sync --extra dev
uv run python main.py build --incremental
uv run python main.py reply-serve --host 127.0.0.1 --port 8766
uv run python main.py feishu daily --no-send   # 仅统计不推送
uv run pytest tests/ -q
```

仓库根 `python main.py` 会转发至 `code/main.py`。

## 在线回复链路（§0.2）

```
意图识别 → if-then 前置过滤 → RAG 检索 → LLM 生成
```

实现入口：`src/hubstudio_python/reply/generate.py`（Phase 2 迁至 `reply/service/`）。

## 飞书模块（待合并）

源仓库：`E:\project\LingLang\chrome-reply\chrome-reply-feishu`

| 源文件 | 目标 |
|--------|------|
| `feishu_daily_report.py` | `feishu/service/daily_report.py` |
| `toolant_daily_report.py` | `feishu/service/toolant_report.py` |
| `history_builder.py` | `feishu/service/history_builder.py` |
| `feishu_bitable.py` | `feishu/interface/bitable.py` |
| `main.py` | 并入 `cli.py` feishu 子命令 |
| `cleanup_mysql_dedup_ai_reply.py` | `feishu/interface/dedup_cleanup.py` |
