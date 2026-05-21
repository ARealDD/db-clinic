---
id: gaussdb_slow_sql_inspection
name: "GaussDB 慢 SQL 查看方式"
category: gaussdb_specifics
description: "GaussDB 慢 SQL 使用 dbe_perf.statement_history 查看，而非 pg_stat_statements"
keywords:
  - statement_history
  - 慢SQL
  - slow query
  - 慢查询
  - dbe_perf
  - pg_stat_statements
  - slow_query_threshold
  - track_stmt_stat_level
tags:
  - gaussdb
  - slow_sql
  - performance
version: "1.0"
author: "运维团队"
---

## 核心差异

GaussDB **不使用** `pg_stat_statements` 扩展查看慢 SQL。
正确方式是查询内置的 `dbe_perf.statement_history` 视图。

| 功能 | PostgreSQL | GaussDB / openGauss |
|------|------------|---------------------|
| 慢 SQL 历史 | `pg_stat_statements`（需安装扩展）| `dbe_perf.statement_history`（内置）|
| 当前执行中的慢 SQL | `pg_stat_activity` | `pg_stat_activity` 或 `dbe_perf.active_session` |
| 慢 SQL 阈值参数 | `log_min_duration_statement` | `log_min_duration_statement` + `track_stmt_stat_level` |

## 查询慢 SQL 历史

```sql
-- 查询最近的慢 SQL（执行时间超过 1 秒）
SELECT start_time,
       finish_time,
       extract(epoch from (finish_time - start_time)) * 1000 AS duration_ms,
       query,
       n_returned_rows,
       n_tuples_fetched,
       db_name,
       application_name,
       client_addr
FROM dbe_perf.statement_history
WHERE finish_time > now() - interval '1 hour'
  AND extract(epoch from (finish_time - start_time)) * 1000 > 1000
ORDER BY duration_ms DESC
LIMIT 20;
```

```sql
-- 查询某条具体 SQL 的执行计划（如已记录）
SELECT query, query_plan, start_time, duration
FROM dbe_perf.statement_history
WHERE query LIKE '%orders%'
ORDER BY start_time DESC
LIMIT 5;
```

## 关键字段说明

| 字段 | 含义 |
|------|------|
| `start_time` / `finish_time` | 开始/结束时间 |
| `duration` | 执行时长（microseconds）|
| `query` | SQL 文本 |
| `query_plan` | 执行计划（需开启记录）|
| `n_returned_rows` | 返回行数 |
| `n_tuples_fetched` | 读取元组数（越大越可能全表扫描）|
| `lock_wait_start` / `lock_wait_end` | 锁等待时间段 |

## 开启慢 SQL 记录

```sql
-- 查看当前阈值（毫秒，-1 表示禁用）
SHOW log_min_duration_statement;

-- 设置慢 SQL 阈值为 1 秒（需超级用户）
ALTER SYSTEM SET log_min_duration_statement = 1000;
SELECT pg_reload_conf();

-- 开启 statement_history 详细记录级别
ALTER SYSTEM SET track_stmt_stat_level = 'L1,L2';  -- L1=基础, L2=含执行计划
SELECT pg_reload_conf();
```

## 常见错误用法（禁止）

```sql
-- ❌ 错误：GaussDB 默认无此扩展，查询会失败
SELECT * FROM pg_stat_statements ORDER BY total_exec_time DESC;

-- ✅ 正确：使用内置视图
SELECT * FROM dbe_perf.statement_history ORDER BY duration DESC;
```
