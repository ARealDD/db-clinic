---
id: gaussdb_system_views
name: "GaussDB 系统视图对照表"
category: system_views
description: "GaussDB 系统视图与 PostgreSQL 的对应关系，以及 dbe_perf schema 的使用"
keywords:
  - gs_stat_activity
  - dbe_perf
  - pg_stat_activity
  - active_session
  - system view
  - 系统视图
  - gs_wlm_session_info
  - statement_history
  - pg_locks
  - pg_stat_user_tables
tags:
  - gaussdb
  - system_views
  - dbe_perf
version: "1.0"
author: "运维团队"
---

## 核心系统视图对照

| 用途 | PostgreSQL | GaussDB / openGauss | 说明 |
|------|------------|---------------------|------|
| 当前活跃会话 | `pg_stat_activity` | `pg_stat_activity` 或 `dbe_perf.active_session` | GaussDB 两者均可用，`dbe_perf.active_session` 字段更丰富 |
| 慢 SQL 历史 | `pg_stat_statements`（扩展）| `dbe_perf.statement_history` | GaussDB 内置，无需安装扩展 |
| 表统计信息 | `pg_stat_user_tables` | `pg_stat_user_tables` 或 `dbe_perf.stat_user_tables` | 两者均可 |
| 锁信息 | `pg_locks` | `pg_locks` + `dbe_perf.locks` | `dbe_perf.locks` 包含更多上下文 |
| 索引统计 | `pg_stat_user_indexes` | `pg_stat_user_indexes` | 与 PostgreSQL 相同 |
| 工作负载管理 | 无 | `gs_wlm_session_info` | GaussDB 独有，查看 WLM 资源使用 |
| 资源池 | 无 | `pg_resource_pool` | GaussDB 独有 |
| 等待事件 | `pg_stat_activity.wait_event_type` | `pg_stat_activity.wait_status` | **字段名不同，见等待事件专项知识** |

## dbe_perf Schema 说明

`dbe_perf` 是 GaussDB 内置的性能监控 Schema，包含比 `pg_catalog` 更丰富的性能数据：

```sql
-- 查看 dbe_perf 下的所有视图
SELECT viewname FROM pg_views WHERE schemaname = 'dbe_perf' ORDER BY viewname;
```

### 常用 dbe_perf 视图

```sql
-- 当前活跃会话（含等待状态、资源使用）
SELECT pid, state, wait_status, wait_event,
       query_start, query, application_name
FROM dbe_perf.active_session
WHERE state != 'idle';

-- 慢 SQL 历史（最近1小时）
SELECT start_time, finish_time,
       extract(epoch from (finish_time - start_time)) * 1000 AS ms,
       query
FROM dbe_perf.statement_history
WHERE start_time > now() - interval '1 hour'
ORDER BY ms DESC LIMIT 10;

-- 表膨胀检查
SELECT schemaname, tablename,
       n_dead_tup, n_live_tup,
       round(n_dead_tup::numeric / NULLIF(n_live_tup + n_dead_tup, 0) * 100, 2) AS dead_ratio
FROM dbe_perf.stat_user_tables
WHERE n_dead_tup > 1000
ORDER BY dead_ratio DESC;
```

## WLM（工作负载管理）相关视图

```sql
-- 查看当前 WLM 资源使用
SELECT session_id, query_id, nodegroup, queue, priority,
       used_mem_kb, cpu_time
FROM gs_wlm_session_info
WHERE status = 'running'
ORDER BY used_mem_kb DESC;

-- 查看资源池定义
SELECT respool_name, mem_percent, cpu_affinity, max_dop
FROM pg_resource_pool;
```

## 注意事项

- `pg_stat_activity` 和 `dbe_perf.active_session` 的字段有部分差异，优先使用 `dbe_perf.active_session` 以获得 GaussDB 专属字段（如 `wait_status`、资源使用等）。
- 访问 `dbe_perf` 视图需要有相应权限；普通用户可能只能看到自己的会话数据，`pg_monitor` 角色可以看到全部数据。
- `gs_wlm_session_info` 在不启用 WLM 的 GaussDB 实例上可能为空，这是正常情况。
