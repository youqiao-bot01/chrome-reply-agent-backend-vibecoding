# 飞书日报 · MySQL / SQLite 表结构

**库**：生产 `controlpastmessagesdata`（MySQL）；本地测试 `test_mock/local/feishu_test.db`（SQLite，`feishu_db.driver: sqlite`）

## 1. auto_reply_plugin_ai_reply_info

AI 自动回复审计；日报统计 AI 回复次数、按 source 分组、Bitable 行来源。

| 字段 | 类型 | 必填（统计） | 说明 |
|------|------|-------------|------|
| id | BIGINT | — | 主键 |
| create_time | DATETIME | ✅ | 统计时间窗过滤 |
| source | VARCHAR | ✅ | 店铺/来源 slug，非空 |
| creator_name | VARCHAR | ✅ | 达人名，非空 |
| message_info | TEXT | — | 达人原消息 |
| reply_result | TEXT | — | AI 回复正文 |
| discuss_product | VARCHAR | — | 讨论商品 id |

**统计 SQL**（`feishu/service/daily_report.py`）：

- `COUNT(*)` WHERE `create_time` 在昨日 16:30～现在
- `GROUP BY source`

## 2. auto_reply_plugin_keywords_reply_info

关键词自动回复；统计关键词触发次数。

| 字段 | 类型 | 必填（统计） | 说明 |
|------|------|-------------|------|
| id | BIGINT | — | 主键 |
| create_time | DATETIME | ✅ | 时间窗 |
| source | VARCHAR | ✅ | 来源 |
| creator_name | VARCHAR | 部分环境 | 达人名 |
| key_words | VARCHAR | — | 触发关键词 |

**统计 SQL**：`GROUP BY key_words`

## 3. 关联表（完整历史 · history_builder）

生产环境另查 MariaDB / PostgreSQL，本地测试可跳过 PG。

| 库 | 表/用途 | 行为类型 |
|----|---------|----------|
| controlpastmessagesdata | 上表 + 关键词表 | AI/关键词回复 |
| influencer_platform | 签约、加窗、申样等 | TTO/CAP/加窗/申样 |
| platform (PG) | tto_market.video_users | TAP/TTO 发视频 |

详见 `code/src/hubstudio_python/feishu/service/history_builder.py`。

## 4. 本地测试种子

运行 `test_mock/scripts/seed_local_test_data.py` 写入：

- 2 条 AI 回复（source=toolant / Linknlatch）
- 1 条关键词回复（key_words=price_too_low）
- `create_time` 设为「当前时间窗内」以便 `feishu daily --no-send` 计数 > 0

## 5. 待补充（Phase 3）

- [ ] influencer_platform 本地 fixture（完整历史冒烟）
- [ ] PostgreSQL video_users fixture
- [ ] Bitable 字段映射表
