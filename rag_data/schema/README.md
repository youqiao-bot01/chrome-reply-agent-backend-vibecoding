# schema 目录说明

按**职责**分子目录；每个子目录里 **只有一个 generic.yaml（通用）**，其余 `<店铺>.yaml` 是各店扩充。

```
schema/
├── vocabulary/          条件 + 名词枚举（chunks 字段能填什么值）
│   ├── generic.yaml     ← 唯一通用：插件 TS 契约（意图白名单、情绪、档位、进度…）
│   ├── toolant.yaml     ← 仅 toolant 多出来的（CPM Rate、priority_node:A*…）
│   ├── Linknlatch.yaml  ← 仅 Linknlatch 多出来的（AMOS 等 slug、Excel 独有进度…）
│   └── merged.yaml      ← 【运行期 · 勿手改】= generic + 全部店铺 合并后的全集
│
├── intent/              达人消息 → intent 的匹配规则（关键词 / 精确回复）
│   ├── generic.yaml     ← 跨店兜底（GEN、no_obvious_intention）
│   └── <shop>.yaml      ← 各店专用规则（加载时与 generic 合并）
│
└── restore/             回复里 {占位符} 的真实 URL/定值
    ├── generic.yaml     ← 通常为空
    ├── <shop>.yaml
    └── candidates/<shop>.json
```

## merged.yaml 是什么？

**就是「all」**：`generic.yaml` + 每个 `vocabulary/<shop>.yaml` 合成一份，给程序读。

| 文件 | 谁维护 | 程序是否直接读 |
|------|--------|----------------|
| `generic.yaml` | 人（对齐插件 TS） | 否 |
| `toolant.yaml` 等 | 人 / DeepSeek 生成 | 否 |
| `merged.yaml` | `vocabulary --init` 自动生成 | **是** |

改枚举只改 `generic.yaml` 或某店 yaml，然后：

```bash
uv run python main.py vocabulary --init --force
```

## 和「GEN」的区别

- **generic** = 通用层（英文文件名 `generic.yaml`）
- **GEN** = 字段取值里的通配「不限」(`applicable_shops: GEN` 等)，或 intent 兜底意图 id
- **reply/rules/gen.txt** = 跨店通用回复说明（原误放在 `kb/input/GEN/`）；**不是**店铺目录
- 不要 `vocabulary/GEN.yaml`
