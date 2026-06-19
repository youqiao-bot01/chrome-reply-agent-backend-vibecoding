# 数据输入索引

运行期数据不在此目录复制，统一放在 `rag_data/`。

## 知识库源文件

| 路径 | 说明 |
|------|------|
| `rag_data/kb/input/gen.txt` | 跨店 GEN 策略，`applicable_shops=GEN` |
| `rag_data/kb/input/<shop>/` | 各店 xlsx / html / txt |
| `rag_data/kb/output/` | chunks、embeddings、manifest、hierarchy |
| `rag_data/kb/incremental_update.yaml` | 增量变动（路径相对 `input/`） |
| `rag_data/kb/authoritative_sources.yaml` | supplement 优先级 |

## Schema（KB 与 reply 共用）

| 路径 | 说明 |
|------|------|
| `rag_data/schema/vocabulary/` | 条件字段 → `merged.yaml` |
| `rag_data/schema/intent/` | 意图 if-then 规则 |
| `rag_data/schema/restore/` | 回复占位符 `{slot}` 还原 |

## 在线回复辅助

| 路径 | 说明 |
|------|------|
| `rag_data/reply/examples/` | API / CLI 请求 JSON 样例 |
| `rag_data/reply/ai_prompt_sections.txt` | 提示词片段模板 |

详见 [`rag_data/README.md`](../../../rag_data/README.md)。
