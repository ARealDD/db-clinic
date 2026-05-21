---
id: gaussdb_explain_settings
name: "GaussDB EXPLAIN 与 explain_perf_mode 设置"
category: config
description: "GaussDB 中 EXPLAIN 的使用方式与 explain_perf_mode 参数的有效值"
keywords:
  - explain
  - explain_perf_mode
  - 执行计划
  - EXPLAIN ANALYZE
  - EXPLAIN PERFORMANCE
  - perf mode
  - query plan
tags:
  - gaussdb
  - explain
  - config
version: "1.0"
author: "运维团队"
---

## GaussDB EXPLAIN 命令差异

GaussDB 在 PostgreSQL `EXPLAIN` 基础上扩展了 `EXPLAIN PERFORMANCE` 语法，
同时通过 `explain_perf_mode` 参数控制输出详细程度。

## explain_perf_mode 参数

| 值 | 行为 |
|----|------|
| `normal` | 标准 PostgreSQL 风格文本输出（默认）|
| `pretty` | 带缩进的格式化文本输出（易读性更好）|
| `summary` | 仅输出汇总统计（节点总耗时、行数）|
| `run` | 实际执行并输出运行时统计（等同于 EXPLAIN ANALYZE）|

```sql
-- 查看当前设置
SHOW explain_perf_mode;

-- 会话级别切换（不影响其他连接）
SET explain_perf_mode = 'pretty';
SET explain_perf_mode = 'summary';
SET explain_perf_mode = 'run';

-- 恢复默认
SET explain_perf_mode = 'normal';
```

## GaussDB EXPLAIN 语法

```sql
-- 基础查看（不执行）
EXPLAIN SELECT * FROM orders WHERE user_id = 100;

-- 带实际执行统计（会真正执行！）
EXPLAIN ANALYZE SELECT * FROM orders WHERE user_id = 100;

-- GaussDB 扩展语法：更详细的性能信息
EXPLAIN PERFORMANCE SELECT * FROM orders WHERE user_id = 100;

-- 输出格式选项
EXPLAIN (FORMAT JSON) SELECT * FROM orders WHERE user_id = 100;
EXPLAIN (FORMAT TEXT, ANALYZE, BUFFERS) SELECT * FROM orders WHERE user_id = 100;
```

## EXPLAIN PERFORMANCE vs EXPLAIN ANALYZE

| 特性 | EXPLAIN ANALYZE | EXPLAIN PERFORMANCE |
|------|-----------------|---------------------|
| 是否执行 | 是 | 是 |
| 节点耗时 | 是 | 是 |
| 缓冲区信息 | 需加 BUFFERS | 自动包含 |
| 内存使用 | 否 | 是 |
| 并行执行信息 | 有限 | 详细 |
| GaussDB 专属统计 | 否 | 是（如 WLM 资源、DN 分布）|

## 注意事项

- `EXPLAIN ANALYZE` 和 `EXPLAIN PERFORMANCE` **会真正执行 SQL**，对 DML（INSERT/UPDATE/DELETE）需要注意，建议在事务中执行后回滚：
  ```sql
  BEGIN;
  EXPLAIN ANALYZE DELETE FROM orders WHERE id = 999;
  ROLLBACK;
  ```
- 在 GaussDB 分布式版（GaussDB for DWS）中，`EXPLAIN PERFORMANCE` 还会显示每个 DN（DataNode）的执行统计，用于定位数据倾斜。
- `explain_perf_mode = 'run'` 等同于 `EXPLAIN ANALYZE`，设置后所有 `EXPLAIN` 都会实际执行，生产环境需谨慎。
