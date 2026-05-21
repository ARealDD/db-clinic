---
id: gaussdb_wait_events
name: "GaussDB 等待事件字段说明"
category: gaussdb_specifics
description: "GaussDB 中等待事件字段与 PostgreSQL 不同，不存在 wait_event_type 字段"
keywords:
  - wait_event
  - wait_event_type
  - wait_status
  - 等待事件
  - lwlock
  - clientread
  - io wait
tags:
  - gaussdb
  - wait_events
  - pg_stat_activity
version: "1.0"
author: "运维团队"
---

## 字段差异（PostgreSQL vs GaussDB）

| 场景 | PostgreSQL | GaussDB / openGauss |
|------|------------|---------------------|
| 等待事件类型 | `wait_event_type` | **不存在此字段** — 用 `wait_status` 替代 |
| 等待事件名称 | `wait_event` | `wait_event`（含义有差异，部分值不同）|
| 查询视图 | `pg_stat_activity` | `pg_stat_activity` 或 `dbe_perf.active_session` |

**关键差异**：在 GaussDB 中使用 `wait_event_type` 字段会报错"列不存在"，必须改用 `wait_status`。

## 正确的 GaussDB 查询写法

```sql
-- 查看当前等待中的会话
SELECT pid, state, wait_status, wait_event, query
FROM pg_stat_activity
WHERE wait_status != 'none'
  AND wait_status IS NOT NULL;
```

```sql
-- 按等待类型分组统计
SELECT wait_status, wait_event, count(*)
FROM pg_stat_activity
WHERE state != 'idle'
GROUP BY wait_status, wait_event
ORDER BY count(*) DESC;
```

## wait_status 常见枚举值

| 值 | 含义 |
|----|------|
| `none` | 无等待（正常运行中）|
| `acquire_lock` | 等待获取常规锁（行锁/表锁）|
| `acquire_lwlock` | 等待获取轻量锁（内部结构锁）|
| `io` | 等待 I/O（磁盘读写）|
| `network` | 等待网络（客户端读/写）|
| `cpu` | CPU 密集型运算 |
| `vacuum` | 等待 vacuum 操作 |
| `transaction` | 等待事务锁 |

## 常见错误用法（禁止）

```sql
-- ❌ 错误：GaussDB 无此字段，会报 "column does not exist"
SELECT wait_event_type, wait_event FROM pg_stat_activity;

-- ✅ 正确：用 wait_status 替代 wait_event_type
SELECT wait_status, wait_event FROM pg_stat_activity;
```
