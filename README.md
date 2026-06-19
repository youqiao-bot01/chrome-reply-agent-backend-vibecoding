# chrome-reply-rag

源文件：`code/assets/rag_data/kb/input/`  
变动清单：`code/assets/rag_data/kb/incremental_update.yaml`（路径相对 `input/`，**改哪个写哪个**）

---

## 改了什么 → yaml 写什么

| 你改的文件 | yaml |
|-----------|------|
| `gen.txt` | `modified: ["gen.txt"]` |
| `Linknlatch/AI话术参考.xlsx` | `modified: ["Linknlatch/AI话术参考.xlsx"]` |
| `toolant/linsey-agent-playbook_2.html` | `modified: ["toolant/linsey-agent-playbook_2.html"]` |
| `toolant/add1.txt` | `modified: ["toolant/add1.txt"]` |
| 新文件 | `added: ["…"]` |
| 删除 | `deleted: ["…"]` |

**说明：** `add1.txt` 和 html 同属 toolant 一家，改 add1 时 yaml 写 add1 即可；`build --incremental` 会自动跑 `structure-playbook` 并重建该店 html 的向量。不必再手动写 html。

---

## 增量更新（三步）

```yaml
added: []
modified:
  - "toolant/add1.txt"
deleted: []
```

```bash
uv run python main.py build --incremental
uv run python main.py embed --incremental
uv run python main.py chroma --incremental
```

跑完清空 yaml → 重启 `reply-serve`：

```bash
uv run python main.py reply-serve --host 127.0.0.1 --port 8766
```

---

## 在线接口（`reply-serve`）

先启动：

```bash
uv run python main.py reply-serve --host 127.0.0.1 --port 8766
```

### 意图识别 `POST /api/intent`

```json
{
  "shop": "toolant",
  "creatorType": "A-level",
  "creatorProgress": "未签约",
  "contextText": "[Mon][seller:toolant] We work commission-only...\n[Mon][creator]you proce is so low"
}
```

返回 ``success`` + ``topIntents``（最多 3 条）；失败时 ``success: false`` 且带 ``error``。

### 回复生成 `POST /api/reply`

与会话字段对齐，在同一场景上直接要回复文案：

```json
{
  "shop": "toolant",
  "creatorName": "risingwithkat",
  "creatorType": "A-level",
  "creatorProgress": "未签约",
  "avgVideoViews": 12300,
  "productId": "7123456789",
  "campaignId": "camp-abc",
  "affiliateCenterRefused": false,
  "contextText": "[Mon][seller:toolant] We work commission-only on Open Plan+. Sound good?\n[Mon][creator]you proce is so low"
}
```

| 字段 | 说明 |
|------|------|
| `affiliateCenterRefused` | 达人已在联盟中心拒绝该店 → 不生成回复，返回 `withdraw: true` |
| `productId` | 当前讨论商品 id → 写入审计表 `discuss_product` |
| `campaignId` | 当前 campaign id → 附在 `message_input_ai` 备注 |

返回 ``ok`` + ``reply``；劝退时 ``withdraw: true`` + ``reason``。生成后写入 MySQL ``controlpastmessagesdata.auto_reply_plugin_ai_reply_info``。

`creatorName` 与 `creatorId` 二选一即可（MySQL 按 `creators_name` 写 ``join_WA`` / ``negotiation_rounds`` 等）。

---

## 其它

```bash
uv run python main.py reply --request tests/test_mock/fixtures/reply/reply_request_toolant_sample.json
uv run python test_chroma_query.py
```

店铺 supplement 绑定见 `code/assets/rag_data/kb/authoritative_sources.yaml`。目录说明见 [`code/assets/README.md`](code/assets/README.md)。
