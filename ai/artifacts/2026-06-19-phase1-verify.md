# Phase 1 文件结构重组 · 检验记录

**方案版本**: v0.1.0  
**日期**: 2026-06-19

## 执行项

| 项 | 状态 |
|----|------|
| 创建 `PLAN.md` | 完成 |
| 创建 `ai/` 目录索引 | 完成 |
| 创建 `code/README.md` | 完成 |
| 删除 `rag_data/incremental_update.yaml`（根目录遗留） | 完成 |
| 删除 `tmp_reply_out.txt`、`nonexistent_sub/`、孤儿 `normalize/` | 完成 |
| `doc/` → `kb/input/` 注释与错误信息 | 完成 |
| `rag_data/reply/rules/README.md` 对齐 gen.txt | 完成 |

## 待 Phase 2

- 代码 SQL / 服务 / 接口三层拆分
- 店铺插件化
- pytest 加入 dev 依赖

## 备注

当前环境未安装 pytest（`pyproject.toml` 无 dev 依赖），测试收集需在 Phase 2 一并补齐。
