# 回复模块 · SQLite（达人 × 店铺状态）

**库文件**：`tests/test_mock/local/reply.db`（测试） / 配置项 `db.path`

## 表：creator_shop_state

| 字段 | 类型 | 说明 |
|------|------|------|
| creator_id | TEXT | 达人标识（TikTok 账号名） |
| shop | TEXT | 店铺 slug |
| creator_progress | TEXT | 进度文案 |
| creator_emotion | TEXT | 情绪 |
| shop_rejected | INTEGER | 是否劝退 |
| negotiation_rounds | INTEGER | 谈判轮次 |
| updated_at | TEXT | 更新时间 |

**代码**：`code/src/hubstudio_python/reply/sql/creator_state.py`

## 与 MySQL 分工

| 存储 | 用途 |
|------|------|
| SQLite `reply.db` | 本地达人×店铺状态、谈判轮次缓存 |
| MySQL `us_region_creator` | expert_quote、join_WA、cooperation_intention |
| MySQL `auto_reply_plugin_ai_reply_info` | AI 回复审计（见 feishu 文档） |
