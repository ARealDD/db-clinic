---
id: missing_index_login
name: "慢SQL - 登录验证字段缺索引导致全表扫描"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "用户登录时按 username 等非主键字段查询，字段无索引，高频全表扫描导致登录超时"

symptoms:
  - "用户登录响应时间超过2秒"
  - "执行计划显示 Seq Scan on users"
  - "登录高峰期 CPU 使用率高"
  - "多用户并发登录时系统响应明显变慢"

keywords:
  - 登录验证
  - username 索引
  - missing index
  - 全表扫描
  - seq scan
  - 高频查询
  - 唯一索引
  - 用户认证

triggers:
  - "Seq Scan on users"
  - "Seq Scan on"
  - "duration:\\s*\\d{3,} ms"

evidence_required:
  - sql_text
  - explain_plan
evidence_optional:
  - index_info

---

## 根因

users 表的 `username` 字段没有索引，登录验证时每次查询都需要全表顺序扫描。username 是高频查询字段（每次登录都用），且登录并发量大，全表扫描造成的 CPU 和 I/O 压力在高峰期会急剧放大。

## 诊断步骤

### 步骤 1

**操作**：查看登录验证相关的慢查询

- 工具：`execute_sql`
- 条件：用户反馈登录缓慢
- 执行语句：

```sql
SELECT wait_event_type, wait_event, COUNT(*) AS session_count,
       ARRAY_AGG(pid ORDER BY query_start) AS pids
FROM pg_stat_activity
WHERE wait_event IS NOT NULL AND pid <> pg_backend_pid()
GROUP BY wait_event_type, wait_event
ORDER BY session_count DESC;
```

```sql
SELECT pid, state, query_start, now() - query_start AS duration, query
FROM pg_stat_activity
WHERE state = 'active'
  AND query LIKE '%username%'
ORDER BY duration DESC
LIMIT 10;
```

**现象**：`pg_stat_activity` 中出现针对 users 表按 username 过滤的查询持续活跃。

**现象分析**：登录查询出现在慢查询列表说明是高频且低效的操作，需检查 username 字段的索引状态。

---
### 步骤 2

**操作**：获取登录 SQL 执行计划

- 工具：`execute_sql`
- 条件：已收集 sql_text
- 执行语句：

```sql
EXPLAIN (ANALYZE, VERBOSE, BUFFERS, FORMAT TEXT)
<slow_sql>
```

```sql
EXPLAIN ANALYZE SELECT * FROM users WHERE username = 'zhangsan'
<slow_sql>
```

**现象**：执行计划显示 `Seq Scan on users`，行数估算为全表规模，无 Index Scan 节点。

**现象分析**：username 字段无索引，优化器被迫全表扫描，在 10 万+ 记录的表上每次查询开销极大。

---
### 步骤 3

**操作**：检查 users 表的索引覆盖情况

- 工具：`execute_sql`
- 条件：已确认 Seq Scan
- 执行语句：

```sql
SELECT schemaname, relname AS table_name, indexrelname AS index_name,
       idx_scan AS index_scans, idx_tup_read AS tuples_read, idx_tup_fetch AS tuples_fetched
FROM pg_stat_user_indexes
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY idx_scan ASC, relname
LIMIT 30;
```

```sql
SELECT * FROM pg_stat_user_indexes WHERE relname = '<target_table>';
SELECT * FROM pg_indexes WHERE tablename = '<target_table>';
```

**现象**：`pg_indexes` 中 users 表只有主键 user_id 索引，username 字段无任何索引。

**现象分析**：username 是登录验证的核心过滤字段，缺少索引是根本原因，需立即创建唯一索引。

---
## 恢复手段

### 根因1：为 username 字段创建唯一索引（主要修复）

**描述**：为 username 添加唯一索引，确保每次登录验证走 Index Scan，同时保证用户名唯一性。

**具体命令**：

```sql
-- 创建唯一索引（CONCURRENTLY 避免锁表）
CREATE UNIQUE INDEX CONCURRENTLY idx_users_username ON users(username);

-- 验证索引生效
EXPLAIN ANALYZE SELECT * FROM users WHERE username = 'zhangsan'
<slow_sql>
```

### 根因2：优化查询列表并使用覆盖索引（辅助优化）

**描述**：将 `SELECT *` 改为只选登录验证需要的列，并创建覆盖索引，触发 Index Only Scan，彻底避免回表。

**具体命令**：

```sql
-- 优化 SQL：只选必要列
SELECT user_id, username, password, status, real_name, department
FROM users
WHERE username = 'zhangsan';

-- 创建覆盖索引（包含登录验证所需所有列）
CREATE INDEX CONCURRENTLY idx_users_username_cover
    ON users(username, password, status, user_id, real_name, department);

-- 验证 Index Only Scan
EXPLAIN ANALYZE
<slow_sql>
```
