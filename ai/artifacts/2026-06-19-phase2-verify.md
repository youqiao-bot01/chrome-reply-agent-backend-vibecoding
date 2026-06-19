# Phase 2 代码迁入 · 检验记录

**方案版本**: v0.2.0  
**日期**: 2026-06-19

## 执行项

| Agent / 任务 | 范围 | 状态 |
|-------------|------|------|
| Agent 1 基础设施 | `code/` 迁入 src/tests/main/config/pyproject | ✅ |
| Agent 2 KB 模块 | `kb/service/` + 旧路径 shim | ✅（上轮 agent 部分完成，本轮补 shim） |
| Agent 3 Reply 模块 | `reply/sql|service|interface/` + shim | ✅ |
| Agent 4 Feishu 模块 | `feishu/` + config + CLI | ✅（上轮 agent 部分完成） |
| 集成 | reply 顶层 shim、pytest 配置、测试路径修复 | ✅ |

## 检验

```bash
cd code
uv sync --extra dev
uv run python main.py --help          # ✅ 含 feishu 子命令
uv run pytest tests/ -q                 # 66 passed, 1 failed
```

### 失败用例（非迁移引入）

- `test_toolant_intent.py::test_refine_downgrades_false_strong_intent` — 空 context 时未降级为 GEN（既有逻辑/测试不一致）

## 目录现状

```
code/
├── main.py / config.yaml / pyproject.toml
├── src/hubstudio_python/
│   ├── kb/service/          # 1.1 知识库
│   ├── reply/sql|service|interface/  # 1.2 回复
│   ├── feishu/              # 1.3 飞书
│   ├── models/
│   ├── cli.py
│   └── {chunking,db,conditions,pipelines,...}/  # 兼容 shim
└── tests/{kb,reply,feishu}/
```

## 待 Phase 3

- `reply/service/generate.py` 按 §0.2 重排：意图 → 过滤 → RAG → LLM
- 清理兼容 shim 层（可选）
- 修复 `test_refine_downgrades_false_strong_intent`
