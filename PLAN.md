# 跨境电商智能客服 RAG 重构方案

> 唯一方案文档。按目录层级组织；代码变更须先更新本文对应章节与版本记录，再实施。

---

## 版本记录


| 版本     | 日期         | 作者  | 变更摘要                         | 代码状态        |
| ------ | ---------- | --- | ---------------------------- | ----------- |
| v0.3.0 | 2026-06-19 | —   | Phase 3a：运行期数据迁入 `code/assets/`；`rag_data/` 仅 kb 构建 | Phase 3a 已完成 |
| v0.2.7 | 2026-06-19 | —   | §0.8 Git：远程 `chrome-reply-agent-backend-vibecoding`、分支 `dev`、分步 commit 推送 | 已完成 |
| v0.2.6 | 2026-06-19 | —   | 目录重命名：`data/`→`test_mock/`、`rag_file/`→`rag_data/`（见名知意） | 已完成 |
| v0.2.5 | 2026-06-19 | —   | Phase 2.2：删除兼容 shim，全量改 import；顶层仅 kb/reply/feishu/models/cli/config | Phase 2.2 已完成 |
| v0.2.4 | 2026-06-19 | —   | §0.7 §5 分步：先思考/清单/检验，用户同意后再执行 | 文档更新 |
| v0.2.3 | 2026-06-19 | —   | 新增 `test_mock/` 测试数据目录；本地冒烟与表结构文档；修复 config/sqlite 路径 | Phase 2.1 已完成 |
| v0.2.2 | 2026-06-19 | —   | §0.6 大任务：多 agent 自规划、互不干扰并发执行 | 文档更新 |
| v0.2.1 | 2026-06-19 | —   | §0.5 长任务超时/卡住：须前台说明原因并请求授权检查 | 文档更新 |
| v0.2.0 | 2026-06-19 | —   | Phase 2：code/ 迁入、kb/reply/feishu 三分模块、测试重组、兼容 shim | Phase 2 已完成 |
| v0.1.1 | 2026-06-19 | —   | 修正在线链路顺序；§0 实施规则；§2.3 三分模块（含飞书）；code/ 独立打包目标 | 已完成 |
| v0.1.0 | 2026-06-19 | —   | 初版：问题定义、目标架构、Phase 1 文件结构重组  | Phase 1 已完成 |
| v0.0.0 | —          | —   | 重构前基线（hubstudio-python 单包结构） | 已归档         |


**版本规则**

- 方案变更 → 递增 minor（v0.1 → v0.2）；架构级重设计 → 递增 major。
- 每次变更在「版本记录」追加一行，并在正文对应章节标注 `[vX.Y.Z]`。
- 代码 PR / commit 须引用方案版本号（如 `plan:v0.1.0`）。

---

## 0. 实施规则（全流程强制遵守）

> **每次开始思考、改方案、写代码、做检验前，须先阅读本节。** Agent 与人均不得跳过。

### 0.1 设计理念

| 原则 | 要求 |
|------|------|
| 方案驱动 | 先更新 `PLAN.md` 版本与对应章节，再改代码；禁止「先写代码后补方案」 |
| 人机协作 | vibecoding agent 高度介入：基于 RAG、if-then 专家经验、前置过滤等技术栈做可解释迭代 |
| 开发范式 | **问题定义 → 思考 → 执行 → 检验**；每 Phase 须有 artifacts 检验记录 |
| 论文复现 | 本文件为唯一方案；目录层级即设计结构；变更可追溯至版本记录 |
| 文档分离 | AI 输入与思考文档只在 `ai/`；代码说明只在 `code/`；方案只在 `PLAN.md` |
| 代码自治 | `code/` 须能 **单独打包、安装、编译运行**；不得 import `ai/` 或依赖方案文档路径 |
| 最小耦合 | 运行期仅依赖 `rag_data/`（数据）、`config.yaml`、`.env`；与 `ai/`、`PLAN.md` 无编译期耦合 |

### 0.2 在线回复链路（固定顺序，不可颠倒）

```
会话上下文 + 达人档位 / 进度 / 情绪
  → 意图识别
  → if-then 前置过滤
  → RAG 检索
  → LLM 生成英文回复
```

### 0.3 代码模块划分（固定三分法）

所有实现与测试均按以下业务模块组织；模块内再分 **SQL 层 → 服务层 → 接口层**：

| 编号 | 模块 | 职责 |
|------|------|------|
| 1.1 | 知识库数据处理 | 离线 build / embed / chroma、schema 引导 |
| 1.2 | 回复内容生成 | 在线 intent / 过滤 / RAG / LLM / HTTP API |
| 1.3 | 飞书汇报实现 | 日报统计、多维表格同步、Webhook 推送（源自 `chrome-reply-feishu`，待合并） |

### 0.4 执行检查清单（每轮必做）

1. 本节与目标 Phase 章节是否一致？
2. 变更是否写入版本记录？
3. 代码是否仅改在 `code/` 对应模块下？
4. 是否补全 / 更新 `code/tests/` 对应用例？
5. 是否在 `ai/artifacts/` 留检验记录？

### 0.5 长任务超时 / 卡住（人机协作强制）

执行 Phase、并行 agent、构建、测试、服务启动等 **预期超过 1–2 分钟** 的操作时，Agent 须遵守：

| 情况 | 必须做的事 |
|------|-----------|
| **超过预期时长** | 停止盲等；在对话中 **前台** 说明：当前在做什么、已等多久、可能卡在哪 |
| **进程无输出 / 疑似卡住** | 同上，并 **主动请求授权** 做诊断（读终端、跑短命令、查 import、看日志） |
| **并行 agent 被中断** | 立即汇报：哪些任务完成、哪些半完成、仓库是否处于不一致状态 |
| **收到授权后** | 先定位根因并 **把结论写清楚**，再续执行；禁止静默重试同一失败路径 |

**前台输出最低要求**（给用户，不要只写在内部思考里）：

1. 现象：命令/任务名、耗时、exit code 或错误类型  
2. 根因：一句话 + 关键报错或文件路径  
3. 请求：如需读终端、跑 `uv run`、查进程，**明确说「请授权我继续检查」**  
4. 下一步：修复方案或备选路径  

人在 Loop 中；Agent 不得长时间后台空转。用户授权后，再继续执行或修复。

### 0.6 大任务：多 Agent 并发（人机协作强制）

Phase 级重构、跨模块迁移、大批量改动等 **大任务**，须拆成多个 agent **并发**执行；每个 agent **先自规划**再动手，且 **互不干扰**。

#### 拆分原则

| 原则 | 要求 |
|------|------|
| 按模块切分 | 优先按 §0.3 三分法：`kb/`、`reply/`、`feishu/` 各一个 agent；基础设施（迁入 `code/`）单独一波 |
| 目录独占 | 每个 agent 只读写 **明确列出的目录/文件**；禁止多 agent 同时改 `cli.py`、同一测试文件 |
| 先规划后执行 | 每个 agent 启动时先输出：目标、范围、不触碰项、检验方式，再改代码 |
| 波次顺序 | 有依赖时分波：如 **Wave 1 基础设施** → **Wave 2 kb / reply / feishu 并行** → **Wave 3 集成**（cli、测试、PLAN） |
| 集成收口 | 并行结束后由主线程或集成 agent 统一：import、shim、pytest、版本记录 |

#### 推荐并发映射（示例）

```
Wave 1  Agent-Infra   → code/ 根、pyproject、路径、main 转发
Wave 2  Agent-KB      → code/src/.../kb/          （仅 kb）
        Agent-Reply   → code/src/.../reply/       （仅 reply + db/conditions shim）
        Agent-Feishu  → code/src/.../feishu/      （仅 feishu + tests/feishu）
Wave 3  集成          → cli.py、全量 pytest、PLAN.md、artifacts 检验记录
```

#### 禁止

- 多个 agent 无边界地改同一文件  
- 未写清「DO NOT touch」就并发启动  
- 并行 agent 中断后不做 §0.5 前台汇报、不评估半迁移状态  

#### 与 §0.5 的关系

并发 agent **超时或被中断** → 立即按 §0.5 前台说明 → 请求授权检查 → 再决定续跑 agent 或改由主线程收口。

### 0.7 §5 分步执行：先方案、后动手（人机协作强制）

`§5 执行计划` 中 **每一个 Phase / 子步骤**，须严格按以下顺序；**未经用户同意，不得开始改代码或跑破坏性命令**。

```
① 思考 → ② 执行清单 → ③ 检验标准 → ④ 用户同意 → ⑤ 按清单执行 → ⑥ 检验并记录
```

| 阶段 | 产出 | 要求 |
|------|------|------|
| **思考** | 目标、范围、依赖、风险、与 §0 一致性 | 写入对话；复杂项另存 `ai/artifacts/YYYY-MM-DD-<phase>-plan.md` |
| **执行清单** | 编号步骤，可勾选；标明改哪些目录 | 一步一项，可对应 commit / agent 边界 |
| **检验标准** | 可验证的通过条件（命令、pytest、冒烟脚本） | 与 §6 通用标准不冲突；可量化 |
| **用户同意** | 用户明确回复同意 / 调整意见 | 未同意前 **仅可** 调研、读代码、写方案草稿 |
| **执行** | 严格按已同意清单 | 清单外变更须回到 ① 重新过审 |
| **检验** | `ai/artifacts/` 检验记录；更新 §5 勾选与版本记录 | 未通过则回到 ①，不强行标记完成 |

**对话中呈现模板**（开做任一 Phase 前必用）：

```markdown
## Phase X · [名称] — 执行前方案（待同意）

### 思考
- …

### 执行清单
1. …
2. …

### 检验标准
- [ ] …

**请确认是否按此清单执行；如需调整请说明。**
```

#### 与其他 §0 条款的关系

- 大任务拆 agent → §0.6；每个 agent 内部也须先交本子规划，再等集成波次同意。
- 执行中超时 / 卡住 → §0.5。
- 检验通过后 → 更新 `PLAN.md` 版本记录与 §5 该 Phase 状态。

### 0.8 Git 仓库与分步推送（工程强制）[v0.2.7]

| 项 | 约定 |
|----|------|
| 远程仓库 | `git@github.com:youqiao-bot01/chrome-reply-agent-backend-vibecoding.git`（`origin`） |
| 开发分支 | `dev`（日常提交与推送） |
| 跟踪范围 | 以 `code/` 实现为主；`PLAN.md`、`ai/`、`rag_data/`、`test_mock/` 与仓库根 shim 一并入库 |
| 分步推送 | §5 每个 Phase / 子步骤 **检验通过后** 单独 commit 并 `git push origin dev`；禁止攒多 Phase 一次推送 |
| 前台进度 | 每次 commit 前在对话标明 **步骤序号 / 总数**、本步范围、commit 信息；推送后回报 `git log -1` 与 push 结果 |

**Commit 信息格式**（须含方案版本）：

```
<type>(plan:vX.Y.Z): <中文或英文简述>

<type>：feat | fix | refactor | chore | docs | test
```

示例：`refactor(plan:v0.2.0): 代码迁入 code/ 并拆分 kb/reply/feishu`

**禁止**：提交 `.env`、`test_mock/local/`、`chroma_db/`、含密钥的本地配置（`config.yaml` 若含密钥须用模板或环境变量替代后再入库）。

---

## 1. 问题定义

### 1.1 业务目标

为 TikTok 等跨境电商平台的达人私信场景，提供 **可解释、可迭代** 的智能客服：

- 离线：店铺话术 / Playbook / 策略文档 → 结构化切片 → 向量索引。
- 在线：会话上下文 + 达人档位/进度/情绪 → **意图识别 → if-then 前置过滤 → RAG 检索 → LLM 生成** 英文回复。
- 集成：MySQL 业务字段、SQLite 本地状态、Chrome 插件 HTTP 接口。

### 1.2 技术约束


| 约束     | 说明                                                                 |
| ------ | ------------------------------------------------------------------ |
| RAG 框架 | ChromaDB + 外部 Embedding API                                        |
| 专家规则   | if-then 条件匹配（`conditions/`）、意图规则（`schema/intent/`）、跨店策略（`gen.txt`） |
| 前置过滤   | 意图识别之后、RAG 之前：档位/进度/情绪/店铺范围/谈判轮次/劝退等 if-then 规则收窄或拦截 |
| 人机协作   | vibecoding agent 高度介入：方案驱动代码，非直接堆代码                                |
| 开发范式   | **问题定义 → 思考 → 执行 → 检验**                                            |


### 1.3 重构动机

1. **AI 思考与代码实现混放**：业务数据、专家经验、方案、源码无清晰边界。
2. **路径与文档不一致**：遗留 `doc/` 引用、重复增量清单、README 与目录不符。
3. **模块未分层**：DB / 服务 / 接口耦合在 `reply/generate.py` 等单文件中，扩展第三店铺成本高。
4. **无版本化方案**：变更缺乏「论文 → 复现」式的可追溯设计文档。

---

## 2. 现状分析（v0.0.0 基线）

### 2.1 仓库顶层 [v0.1.1]

```
chrome-reply-rag/
├── PLAN.md                 # 【方案】唯一设计文档 + 实施规则（§0）
├── ai/                     # 【AI 思考】输入文档 + 过程产物（§0.1 文档分离）
├── code/                   # 【代码】实现 + 测试 + 代码文档（可独立打包运行，目标态）
│   ├── README.md           #   代码说明、模块索引、构建运行方式
│   ├── pyproject.toml      #   （Phase 2 迁入）包定义
│   ├── main.py             #   （Phase 2 迁入）统一 CLI 入口
│   ├── config.yaml         #   （Phase 2 迁入）运行配置
│   ├── src/hubstudio_python/
│   └── tests/
├── rag_data/               # 【真实业务数据】KB 源文件、schema、回复样例
├── README.md               # 仓库入口说明（指向 PLAN / ai / code）
├── chroma_db/              # 本地向量库（gitignore）
└── test_mock/              # 【测试 Mock 数据】fixture、冒烟脚本、本地 .db
```

> **过渡期（v0.1.x）**：`main.py`、`config.yaml`、`pyproject.toml`、`src/`、`tests/` 仍在仓库根；Phase 2 整体迁入 `code/` 后删除根目录重复项。

### 2.2 数据与 schema（`rag_data/`）


| 路径                             | 职责                                         |
| ------------------------------ | ------------------------------------------ |
| `kb/input/`                    | 源文件：gen.txt、各店 xlsx/html/txt               |
| `kb/output/`                   | chunks / embeddings / manifest / hierarchy |
| `kb/incremental_update.yaml`   | 增量变动清单（唯一有效）                               |
| `schema/vocabulary/`           | 条件字段枚举 → `merged.yaml`                     |
| `schema/intent/`               | 意图 if-then 规则                              |
| `schema/restore/`              | 回复占位符还原                                    |
| `reply/examples/`              | API 请求样例                                   |
| `reply/ai_prompt_sections.txt` | 提示词片段                                      |


### 2.3 代码模块 [v0.1.1]

按 **实现代码 / 测试代码** 二分，再按业务模块 **1.1 / 1.2 / 1.3** 三分。各模块下一层再拆 **SQL 层 → 服务层 → 接口层**。

> 代码已迁入 `code/`（Phase 2）。`hubstudio_python/` 顶层仅保留 `kb/`、`reply/`、`feishu/`、`models/`、`cli.py`、`config.py`（Phase 2.2 删除兼容 shim）。

#### 2.3.1 实现代码（目标路径 `code/src/hubstudio_python/`）

**1.1 知识库数据处理 `kb/`**

| 层 | 目标目录 | 当前代码 | 职责 |
|----|----------|----------|------|
| SQL 层 | — | — | 无独立 DB；读写 `rag_data/kb/output/` JSON |
| 服务层 | `kb/service/` | `chunking/` `ingest/` `pipelines/` `embedding/` `storage/`（已迁入，shim 已删） | build / embed / chroma / 增量 / Playbook 结构化 / vocabulary |
| 接口层 | `kb/interface/` | `cli.py`（build/embed/chroma/vocabulary 子命令） | CLI 参数解析、调度 pipeline |
| 共享 | `models/`（部分） | `models/chunk.py` `manifest.py` `rag_layout.py` … | 切片、manifest、路径布局 |

**1.2 回复内容生成 `reply/`**

| 层 | 目标目录 | 当前代码 | 职责 |
|----|----------|----------|------|
| SQL 层 | `reply/sql/` | `db/`（已迁入，shim 已删） | MySQL 审计、SQLite 达人状态、报价 |
| 服务层 | `reply/service/` | `conditions/`、`reply/*.py` 根级模块（已迁入，shim 已删） | 意图 → 过滤 → RAG → LLM；店铺特例 |
| 接口层 | `reply/interface/` | `reply/app.py` `reply/http.py` `cli.py`（reply/reply-serve） | FastAPI `/api/reply` `/api/intent`、CLI |
| 共享 | `models/`（部分） | `intent_*` `toolant_*` `shop_*` … | 意图、店铺模型 |

**1.3 飞书汇报实现 `feishu/`**（待合并）

| 层 | 目标目录 | 源仓库 `chrome-reply-feishu` | 职责 |
|----|----------|------------------------------|------|
| SQL 层 | `feishu/sql/` | 各 report 内嵌 SQL（MariaDB + PostgreSQL） | 统计查询、历史行为、去重清理 |
| 服务层 | `feishu/service/` | `feishu_daily_report.py` `toolant_daily_report.py` `history_builder.py` | 日报拼装、完整历史串、Toolant 专项 |
| 接口层 | `feishu/interface/` | `feishu_bitable.py` + `main.py` + `cleanup_mysql_dedup_ai_reply.py` | Bitable 同步、Webhook 推送、CLI 入口 |
| 配置 | — | `config.yaml`（db + pg + feishu webhook/bitable） | 合并入根 `config.yaml` 的 `feishu:` / `pg:` 段 |

#### 2.3.2 测试代码（目标路径 `code/tests/`）

| 模块 | 目标目录 | 当前测试 | 覆盖重点 |
|------|----------|----------|----------|
| 1.1 KB | `tests/kb/` | （缺）`test_gen_policy_chunker.py` 等可迁入 | 切块、增量、GEN 策略 |
| 1.2 回复 | `tests/reply/` | `test_toolant_*.py` `test_reply_*.py` `test_intent_*` … | 意图、过滤、RAG 守卫、店铺特例 |
| 1.3 飞书 | `tests/feishu/` | （缺，源仓库无 pytest） | 日报统计、Bitable 字段映射、历史拼接 |

#### 2.3.3 模块依赖（允许方向）

```
kb/     ──读取──► rag_data/kb/（构建）；code/assets/（运行期 schema + kb 快照）
reply/  ──读取──► code/assets/、chroma_db/、config.yaml
        ──调用──► kb/ 产出（Chroma collection，非 import kb 服务）
feishu/ ──读取──► MySQL / PostgreSQL（与 reply/ 共用 MySQL 连接配置，不 import reply 服务）
```

**禁止**：`code/` 内任何模块 import `ai/` 或读取 `PLAN.md` / `ai/artifacts/`。


### 2.4 已知问题（Phase 1 处理项）

- 删除 `rag_data/incremental_update.yaml`（根目录遗留副本）
- 统一 `doc/` → `kb/input/` 注释与错误信息
- 建立 `ai/` 思考目录与本文档
- 清理 `nonexistent_sub/`、`normalize/` 孤儿缓存
- `reply/rules/` 文档与 `gen.txt` 实际位置对齐
- 包名 / 仓库名统一（Phase 3）
- 代码三层拆分（Phase 2）

---

## 3. 目标架构

### 3.1 顶层：方案 / AI 思考 / 代码 / 数据 [v0.1.1]

```
chrome-reply-rag/
│
├── PLAN.md                         # 【方案】唯一设计文档 + §0 实施规则
│
├── ai/                             # 【AI 思考】仅文档与草稿，不参与打包
│   ├── README.md                   #   AI 部分说明
│   ├── inputs/
│   │   ├── data/                   #   数据输入索引 → 实际在 rag_data/
│   │   ├── experience/             #   if-then 专家经验摘要
│   │   └── references/             #   外部参考
│   └── artifacts/                  #   思考 / 检验过程产物
│
├── code/                           # 【代码】可独立 uv pip install -e . && 运行
│   ├── README.md                   #   代码文档（构建、模块、API）
│   ├── pyproject.toml              #   Phase 2 迁入
│   ├── main.py                     #   Phase 2 迁入
│   ├── config.yaml                 #   Phase 2 迁入（含 feishu / pg 段）
│   ├── src/hubstudio_python/
│   │   ├── kb/                     #   1.1 知识库（service + interface）
│   │   ├── reply/                  #   1.2 回复（sql + service + interface）
│   │   ├── feishu/                 #   1.3 飞书（sql + service + interface）
│   │   ├── models/                 #   跨模块共享模型
│   │   └── cli.py                  #   顶层 CLI 分发
│   └── tests/
│       ├── kb/
│       ├── reply/
│       └── feishu/
│
├── rag_data/                       # 【业务数据】KB 源文件、schema
├── test_mock/                           # 【测试数据】fixture、schema 文档、本地 .db [v0.2.3]
│   ├── schema/ fixtures/ scripts/
│   ├── config.test.yaml
│   └── local/                      # gitignore
│
├── README.md                       # 仓库总览
└── chroma_db/                      # 向量库持久化
```

**边界原则**

- **文档**：AI 输入只在 `ai/`；代码说明只在 `code/`；方案与 §0 规则只在 `PLAN.md`。
- **打包**：`code/` 目录单独拷贝 + `rag_data/` + `config.yaml` + `.env` 即可运行；不依赖 `ai/`、`PLAN.md`。
- **数据**：`ai/inputs/data/` 仅索引，不复制 `rag_data/`；代码通过 `config.yaml` 配置 `rag_data` 路径。
- **方案驱动**：代码不得包含未写入方案的业务假设；变更先改 `PLAN.md` 再改 `code/`。

### 3.2 在线回复链路 [v0.1.1]

```
HTTP POST /api/reply
  → 接口层：参数校验、序列化
  → 服务层：意图识别
  → 服务层：if-then 前置过滤（conditions / reply_guard；未通过则 withdraw 或收窄条件）
  → 服务层：RAG 检索（Embedding + Chroma + metadata 过滤）+ 注入 GEN 策略
  → 服务层：店铺特例（WhatsApp / 合作意向 / 报价）
  → 服务层：LLM 生成 + restore 占位符
  → SQL 层：审计写入 MySQL、状态写 SQLite
  → 接口层：返回 reply / withdraw
```

### 3.3 代码三层目标（Phase 2）[v0.1.1]

各业务模块（1.1 / 1.2 / 1.3）内部统一三层：

| 层 | 职责 | 1.1 kb | 1.2 reply | 1.3 feishu |
|----|------|--------|-----------|------------|
| SQL 层 | 连接、查询、持久化；无业务分支 | —（JSON 文件） | `reply/sql/` | `feishu/sql/` |
| 服务层 | 领域逻辑、编排 | `kb/service/` | `reply/service/`（含 `rules/`） | `feishu/service/` |
| 接口层 | HTTP / CLI / Webhook | `kb/interface/` | `reply/interface/` ← 现 `reply/app.py` | `feishu/interface/` |

当前 FastAPI 在 `reply/app.py` + `reply/http.py`；Phase 2 迁至 `reply/interface/`。

---

## 4. 功能模块地图

### 4.1 离线 KB


| 功能           | 命令                   | 关键代码                                     | 数据                                     |
| ------------ | -------------------- | ---------------------------------------- | -------------------------------------- |
| 切块           | `build`              | `pipelines/build_*` `chunking/`          | `kb/input` → `kb/output/*.chunks.json` |
| 向量化          | `embed`              | `pipelines/build_embeddings.py`          | `*.embeddings.json`                    |
| 入库           | `chroma`             | `pipelines/build_chroma.py`              | `chroma_db/`                           |
| 增量           | `--incremental`      | `pipelines/build_incremental.py`         | `incremental_update.yaml`              |
| Playbook 结构化 | `structure-playbook` | `pipelines/structure_playbook_chunks.py` | `*.structured.full.json`               |
| Schema 引导    | `vocabulary`         | `pipelines/extract_shop_vocabulary.py`   | `schema/`                              |


### 4.2 在线回复


| 功能         | 顺序 | 入口                 | 关键代码                                         | 规则来源                          |
| ---------- | -- | ------------------ | -------------------------------------------- | ----------------------------- |
| 意图识别       | ①  | `POST /api/intent` | `reply/generate.py`                          | `schema/intent/`              |
| if-then 前置过滤 | ②  | —                  | `conditions/match.py` `reply/reply_guard.py` | vocabulary + if-then          |
| RAG 检索     | ③  | —                  | `reply/generate.py` `conditions/chroma_where.py` | Chroma + conditions + gen.txt |
| LLM 生成回复   | ④  | `POST /api/reply`  | `reply/generate.py`                          | 检索结果 + restore              |
| toolant 特例 | ③④ 间 | —                  | `reply/toolant_*.py`                         | 店铺插件（Phase 3 抽象）              |


### 4.3 测试对应 [v0.1.1]

```
code/tests/kb/      ↔  1.1 知识库（chunking、增量、GEN）
code/tests/reply/   ↔  1.2 回复（意图、过滤、守卫、店铺特例）
code/tests/feishu/  ↔  1.3 飞书（日报、Bitable、历史；Phase 2 新建）
```

过渡期：根目录 `tests/test_*` 在 Phase 2 按上表迁入 `code/tests/` 子目录。

---

## 5. 执行计划

> **流程强制（§0.7）**：下列每个 Phase 开工前，须先产出 **思考 + 执行清单 + 检验标准**，经用户同意后再执行；完成后更新勾选与 `ai/artifacts/` 检验记录。已完成的 Phase 保留记录供复现。

### Phase 1：文件结构重组 [v0.1.0] — 已完成

**思考**

- 先统一「数据在哪、文档说什么」，不动业务逻辑。
- 建立方案单一事实来源（`PLAN.md`）与 `ai/` 输入索引。

**执行清单**

1. 创建 `PLAN.md`（本文件）
2. 创建 `ai/inputs/{data,experience,references}`、`ai/artifacts/`
3. 删除遗留 `rag_data/incremental_update.yaml`
4. 全局 `doc/` → `kb/input/`（注释、错误信息、schema description）
5. 更新 `README.md`、`rag_data/README.md` 指向方案
6. 清理 `nonexistent_sub/`、`tmp_reply_out.txt`、孤儿 `normalize/__pycache__`

**检验**

- `grep doc/` 无运行时误导性引用（历史说明可保留「曾用 doc/」一句）
- CLI `build --help` / 增量 yaml 路径说明正确
- `tests/` 可收集（需 dev 依赖 pytest，Phase 2）
- 现有 API 与增量三步流程行为不变（未改业务逻辑，待人工冒烟）

### Phase 2：代码迁入 code/ + 三分模块 + 飞书合并 [v0.2.0] — 已完成

**思考**

- `code/` 成为唯一可打包根；根目录 `src/`、`tests/`、`main.py` 迁入后删除。
- 按 §2.3 拆分 kb / reply / feishu 三层；合并 `chrome-reply-feishu`。
- 在线链路按 §0.2 顺序重构 `reply/service/generate.py`（先过滤后 RAG）→ **延至 Phase 3**。

**执行清单**

1. [x] 创建 `code/pyproject.toml`，合并 feishu 依赖（psycopg2-binary 等）
2. [x] 根目录 `src/`、`tests/`、`main.py`、`config.yaml` 迁入 `code/`
3. [x] 新建 `feishu/`，自 `chrome-reply-feishu` 迁入并改包名
4. [x] 拆分 `reply/`：`sql/` `service/` `interface/`；`conditions/` 并入 `reply/service/rules/`
5. [x] 拆分 `kb/`：chunking/pipelines 等归入 `kb/service/`
6. [x] `tests/` 按 `kb/` `reply/` `feishu/` 子目录重组
7. [x] `config.yaml` 增加 `feishu:`、`pg:` 段；CLI 增加 `feishu` 子命令
8. [x] 旧路径兼容 shim（Phase 2.2 已删除）

**检验**

- [x] `cd code && uv run python main.py --help` 正常
- [x] `pytest tests/`：66 passed / 1 failed（`test_refine_downgrades_false_strong_intent` 既有逻辑问题）
- [x] `reply-serve` 本地冒烟：`test_mock/scripts/smoke_reply_serve.py` + `chroma_db/`
- [x] `feishu daily` 本地冒烟：SQLite `test_mock/local/feishu_test.db`
- [ ] 在线 generate.py 链路顺序重构（Phase 3）

### Phase 2.2：删除兼容 shim、统一 import [v0.2.5] — 已完成

**思考**

- Phase 2 遗留 `chunking/` `db/` `conditions/` 等顶层 shim 与 `reply/*.py` 转发模块，导致目录膨胀、import 路径不统一。
- 收口后 `hubstudio_python/` 顶层仅 `kb/` `reply/` `feishu/` `models/` `cli.py` `config.py`。

**执行清单**

1. [x] 全仓库改 import：`kb.service.*` / `reply.sql` / `reply.service.rules` / `reply.service.*`
2. [x] 删除 shim 目录：`chunking/` `ingest/` `pipelines/` `embedding/` `storage/` `db/` `conditions/`
3. [x] 删除 `reply/` 根级 10 个 shim 文件；保留 `__init__.py` + `sql/` `service/` `interface/`
4. [x] 删除误路径 `code/src/data/`
5. [x] 更新 `config.py`：`reply.sql.config` / `reply.sql.mysql_config`

**检验**

- [x] `grep` 无 `hubstudio_python.(db|conditions|chunking|pipelines|embedding|storage)` 旧 import
- [x] `uv run pytest`：66 passed / 2 failed（`test_refine_downgrades_false_strong_intent` 既有；`test_feishu_config_imports` 受 `HUBSTUDIO_CONFIG_FILE` 环境影响）
- [x] `uv run python main.py --help` 正常

### Phase 2.1：本地测试数据与冒烟 [v0.2.3] — 已完成

1. [x] 创建 `test_mock/`（与 `ai/`、`code/` 并列）
2. [x] `test_mock/schema/feishu_mysql.md`、`reply_sqlite.md`
3. [x] `test_mock/scripts/seed_local_test_data.py`、smoke 脚本
4. [x] `feishu_db.driver: sqlite` 本地日报统计
5. [x] 修复 `reply/sql/config.py` 仓库根路径、`config.yaml` 解析

**失败用例说明**（`test_refine_downgrades_false_strong_intent`）：

- 期望：空 context + 裸 `Interested!` → 降级 `GEN`
- 实际：`refine_toolant_intent` 仅在「context 含 1样2视频 pitch 但未确认」时降级；空 context 保留 `interest`
- 处置：Phase 3 修代码或统一产品定义后改测试

### Phase 3a：运行期数据迁入 code/assets [v0.3.0] — 已完成

**边界**：`rag_data/` 仅 `kb/`（构建输入/中间产物）；回复持续读取的 `schema/`、`prompt/`、`gen.chunks.json`、`shop_tier_intent_hierarchy.json` → `code/assets/`。

1. [x] 迁移 `schema/`、`ai_prompt_sections.txt` → `code/assets/`
2. [x] `gen.chunks.json` / `shop_tier_intent_hierarchy.json` → `code/assets/kb/`（build 后 sync）
3. [x] `reply/examples/` → `test_mock/fixtures/reply/`
4. [x] 更新 `rag_layout` / `schema_layout` / 回复模块路径
5. [x] `pytest` 回归

### Phase 3：回复链路重排 + 店铺插件化 [v0.3.0] — 未开始

> 开工前须按 §0.7 提交执行前方案，待用户同意后再动手。

**思考**（草案，待开 Phase 前定稿）

- `reply/service/generate.py` 按 §0.2 重排：意图 → 过滤 → RAG → LLM
- 修复 `refine_toolant_intent` 空 context 降级（或统一测试定义）
- 店铺特例迁入 `reply/service/shops/<shop>/`

**执行清单**（草案）

1. 梳理 `generate.py` 现有调用顺序，列出搬迁函数清单
2. 实现新顺序；补链路顺序单测
3. 修复 `test_refine_downgrades_false_strong_intent`
4. 抽 toolant 插件目录
5. `test_mock/schema/` 补全 MySQL/PG fixture

**检验标准**（草案）

- [ ] 单测覆盖意图 → 过滤 → RAG 调用顺序
- [ ] `pytest tests/reply/` 全绿
- [ ] `test_mock/scripts/smoke_reply_serve.py` 通过
- [ ] §0.2 与代码一致

### Phase 4：agent 工作流固化 [v0.4.0] — 未开始

1. `ai/artifacts/` 模板：问题分析、方案 diff、检验报告
2. CI：方案版本号与变更文件校验

---

## 6. 检验标准（通用）


| 阶段  | 检验项        | 方法                                                 |
| --- | ---------- | -------------------------------------------------- |
| 方案  | 版本记录完整     | 人工 review `PLAN.md`                                |
| 数据  | 增量 yaml 唯一 | 仅 `kb/incremental_update.yaml`                     |
| 离线  | 增量三步成功     | `build/embed/chroma --incremental`                 |
| 在线  | 样例请求       | `reply/examples/reply_request_toolant_sample.json` |
| 规则  | 测试回归       | `pytest tests/`                                    |
| 文档  | 路径一致       | README 与 `rag_layout.py` 一致                        |


---

## 7. 附录

### 7.1 关键路径速查


| 用途   | 路径                                       |
| ---- | ---------------------------------------- |
| 源文件  | `rag_data/kb/input/`                     |
| 增量清单 | `rag_data/kb/incremental_update.yaml`    |
| 条件词汇 | `rag_data/schema/vocabulary/merged.yaml` |
| 意图规则 | `rag_data/schema/intent/`                |
| 跨店策略 | `rag_data/kb/input/gen.txt`              |
| 配置   | `config.yaml` + `.env`                   |
| 方案   | `PLAN.md`                                |


### 7.2 店铺现状


| 店铺         | 源文件类型                    | 特例代码                                           |
| ---------- | ------------------------ | ---------------------------------------------- |
| toolant    | HTML Playbook + add1.txt | `toolant_whatsapp.py` `toolant_cooperation.py` |
| Linknlatch | Excel                    | —                                              |
| GEN        | gen.txt（跨店）              | `global_policy.py`                             |


### 7.3 相关文档

| 类型 | 路径 |
|------|------|
| 方案 + §0 规则 | `PLAN.md` |
| AI 输入与思考 | `ai/README.md`、`ai/inputs/*`、`ai/artifacts/` |
| 代码说明 | `code/README.md`（Phase 2 补模块子文档） |
| 仓库操作 | `README.md` |
| 数据目录 | `rag_data/README.md` |
| Schema | `rag_data/schema/README.md` |
| 飞书源仓库（合并前） | `E:\project\LingLang\chrome-reply\chrome-reply-feishu\README.md` |

---

*文档结束 · 当前方案版本 v0.2.4*