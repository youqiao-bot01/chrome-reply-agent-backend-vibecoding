# 专家经验（if-then 规则）

本目录存放 **可读的规则摘要与决策说明**。机器可执行规则仍在 `rag_data/schema/` 与 `src/hubstudio_python/conditions/`。

## 规则来源地图

| 类型 | 位置 | 作用阶段 |
|------|------|----------|
| 意图 if-then | `rag_data/schema/intent/*.yaml` | 意图分类 |
| 检索条件 | `rag_data/schema/vocabulary/merged.yaml` | Chroma metadata 过滤 |
| 匹配引擎 | `src/hubstudio_python/conditions/match.py` | 切片是否命中会话上下文 |
| 跨店策略 | `rag_data/kb/input/gen.txt` | 始终注入 LLM 上下文 |
| 回复守卫 | `src/hubstudio_python/reply/reply_guard.py` | LLM 前/后拦截 |
| 店铺特例 | `src/hubstudio_python/reply/toolant_*.py` | toolant WhatsApp、合作意向等 |

## 编写约定

- 新增业务规则：先在 `PLAN.md` 写清 if-then，再改 schema / 代码。
- 本目录文件命名：`<主题>.md`，引用具体 yaml 键名而非重复全文。
