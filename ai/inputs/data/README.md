# 数据输入索引

运行期数据不在此目录复制。

## 知识库构建（`rag_data/kb/`）

| 路径 | 说明 |
|------|------|
| `rag_data/kb/input/gen.txt` | 跨店 GEN 策略源文件 |
| `rag_data/kb/input/<shop>/` | 各店 xlsx / html / txt |
| `rag_data/kb/output/` | chunks、embeddings、manifest |
| `rag_data/kb/incremental_update.yaml` | 增量变动 |

## 回复运行期（`code/assets/`）

| 路径 | 说明 |
|------|------|
| `code/assets/schema/vocabulary/` | 条件字段 → `merged.yaml` |
| `code/assets/schema/intent/` | 意图 if-then 规则 |
| `code/assets/schema/restore/` | 占位符还原 |
| `code/assets/prompt/ai_prompt_sections.txt` | 提示词片段 |
| `code/assets/kb/gen.chunks.json` | GEN 策略（build 同步） |
| `code/assets/kb/shop_tier_intent_hierarchy.json` | 意图层级 |

## 测试样例（`test_mock/`）

| 路径 | 说明 |
|------|------|
| `test_mock/fixtures/reply/` | API / CLI 请求 JSON |

详见 [`rag_data/README.md`](../../../rag_data/README.md)、[`code/assets/README.md`](../../../code/assets/README.md)。
