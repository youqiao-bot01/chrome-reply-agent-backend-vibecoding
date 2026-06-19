# rag_data — 知识库构建专用数据

**仅用于离线 build / embed / chroma**；构建结束后回复链路不再读取本目录（运行期数据在 `code/assets/`）。

```
rag_data/
└── kb/
    ├── input/                   源文档：gen.txt、各店 xlsx/html/txt
    ├── output/                  中间产物：*.chunks.json、*.embeddings.json、manifest …
    ├── incremental_update.yaml  增量变动清单
    └── authoritative_sources.yaml
```

| 目录 | 命令 |
|------|------|
| `kb/input` + `kb/output` | `build` / `embed` / `chroma` / `structure-playbook` |

## 与 `code/assets/` 的关系

| 构建产出（`kb/output/`） | 同步到 `code/assets/kb/` | 回复运行期 |
|--------------------------|--------------------------|------------|
| `gen.chunks.json` | ✅ build 后自动 sync | `global_policy.py` |
| `shop_tier_intent_hierarchy.json` | ✅ shop-tier-intent / build 后 sync | `shop_intent_hierarchy.py` |

`schema/`、`prompt/` 已迁至 [`code/assets/`](../code/assets/README.md)。

## 注意

- **`gen.txt`** 放在 `kb/input/` 根目录，`applicable_shops=GEN`。
- 方案见根目录 [`PLAN.md`](../PLAN.md)。
