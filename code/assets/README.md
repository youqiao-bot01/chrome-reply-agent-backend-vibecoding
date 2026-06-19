# code/assets — 业务数据与运行期资产

| 路径 | 用途 | 读者 |
|------|------|------|
| `rag_data/kb/input/` | 源文档（xlsx/html/txt、gen.txt） | kb build |
| `rag_data/kb/output/` | chunks、embeddings、manifest | kb build → chroma |
| `rag_data/kb/incremental_update.yaml` | 增量变动清单 | kb build |
| `rag_data/kb/authoritative_sources.yaml` | Playbook supplement 绑定 | structure-playbook |
| `schema/` | vocabulary / intent / restore | kb build + reply |
| `prompt/` | LLM 提示词片段 | reply |
| `kb/` | gen.chunks.json、shop_tier_intent_hierarchy.json（build 后 sync） | reply |

构建结束后 `sync_runtime_kb_assets` 将 output 中上述两个 json 复制到 `kb/`。
