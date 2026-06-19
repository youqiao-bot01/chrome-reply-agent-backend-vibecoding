# rag_data — 真实业务数据

**知识库（离线切片）** 与 **在线回复** 分两大目录；**schema** 为二者共用。

```
rag_data/
├── kb/                          知识库 · 文档切片（build / embed / chroma）
│   ├── input/
│   │   ├── gen.txt              跨店 GEN 全局策略（参与 embed，回复时始终注入）
│   │   └── <shop>/              店铺原始源：xlsx、html、txt
│   ├── output/                  派生产物：*.chunks.json、*.embeddings.json、manifest.json …
│   ├── incremental_update.yaml  增量变动清单（唯一有效；路径相对 input/）
│   └── authoritative_sources.yaml
│
├── reply/                       在线回复（reply / reply-serve）
│   ├── examples/                请求 JSON 样例
│   ├── ai_prompt_sections.txt   提示词片段
│   └── rules/                   不参与 embed 的规则说明（见 rules/README.md）
│
└── schema/                      共用：vocabulary / intent / restore（见 schema/README.md）
```

## 职责边界

| 目录 | 命令 | 说明 |
|------|------|------|
| `kb/input` + `kb/output` | `build` / `embed` / `chroma` / `structure-playbook` | 文档 → 切片 → 向量 → Chroma |
| `reply/` | `reply` / `reply-serve` | 读 Chroma + schema，生成达人回复 |
| `schema/` | `vocabulary` / `intent` | 构建与回复都会读 |

## 注意

- **`gen.txt`** 放在 `kb/input/` **根目录**（不是 `<shop>/` 下）：跨店策略，`applicable_shops=GEN`。
- **不要**在 `kb/input/<shop>/` 下放跨店业务规则；店铺话术放各店目录。
- 字段取值 **`GEN`**（不限）与 **`generic.yaml`**（通用配置层）不是同一概念；勿再建 `input/GEN/` 店铺目录。
- 重构方案与目录规划见根目录 [`PLAN.md`](../PLAN.md)。
