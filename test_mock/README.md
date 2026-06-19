# test_mock — 测试 Mock 数据

与 `ai/`（思考）、`code/`（实现）、`rag_data/`（业务 KB）并列。

| 路径 | 用途 | 入库 |
|------|------|------|
| `schema/` | 表结构设计文档（MySQL / SQLite / PG） | ✅ |
| `fixtures/` | 固定种子 JSON / SQL | ✅ |
| `scripts/` | 初始化本地库、冒烟脚本 | ✅ |
| `config.test.yaml` | 本地冒烟用配置（SQLite + 本地 Chroma） | ✅ |
| `local/` | 运行时生成的 `.db`（gitignore） | ❌ |

## 初始化

```bash
cd code
uv run python ../test_mock/scripts/seed_local_test_data.py
```

## 冒烟

```bash
# reply API（本地 Chroma + 测试配置）
uv run python ../test_mock/scripts/smoke_reply_serve.py

# feishu 日报统计（本地 SQLite，不推送）
set HUBSTUDIO_CONFIG_FILE=../test_mock/config.test.yaml
uv run python main.py feishu daily --no-send

# 查看本地 Chroma 集合
uv run python ../test_mock/scripts/query_chroma.py
```

## 边界

- 测试数据 **不参与** Python 包打包；`code/` 通过 `config.test.yaml` 或环境变量指向本目录。
- 生产仍用 `code/config.yaml` + 远程 MySQL。
