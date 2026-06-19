# code/assets — 回复运行期数据

与 ``rag_data/kb/``（构建输入/中间产物）分离；随 ``code/`` 版本管理。

| 路径 | 用途 | 写入方 |
|------|------|--------|
| `schema/` | vocabulary / intent / restore（意图、if-then、占位符） | kb build / vocabulary 命令 |
| `prompt/ai_prompt_sections.txt` | LLM 提示词片段 | 人工维护 |
| `kb/gen.chunks.json` | GEN 全局策略（整文件） | build 后从 `rag_data/kb/output/` 同步 |
| `kb/shop_tier_intent_hierarchy.json` | 店铺×档位×进度→意图 | shop-tier-intent / build 后同步 |

构建命令结束时会调用 ``sync_runtime_kb_assets`` 将 ``gen.chunks.json`` 与 ``shop_tier_intent_hierarchy.json`` 复制到本目录。
