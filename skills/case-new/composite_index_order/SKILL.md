---
id: composite_index_order
name: "慢SQL - 复合索引列顺序与查询不匹配导致额外排序"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "复合索引列顺序未遵循最左前缀原则或未与 ORDER BY 方向匹配，导致索引使用不当或额外排序开销"

symptoms:
  - "查询有复合索引但执行时间仍超过2秒"
  - "执行计划显示 Index Scan 但仍有 Sort 节点"
  - "idx_scan 计数较低，索引实际未被充分利用"
  - "按 status 过滤后再 ORDER BY created_at 查询慢"

keywords:
  - 复合索引
  - composite index
  - 索引顺序
  - 最左前缀
  - leftmost prefix
  - ORDER BY 索引
  - 索引排序
  - 额外排序

triggers:
  - "Sort.*cost=.*rows=.*width="
  - "Index Scan Backward"
  - "duration:\\s*\\d{3,} ms"

evidence_required:
  - sql_text
  - explain_plan
  - index_info
evidence_optional:
  - table_stats

---

## 根因

复合索引 `(status, created_at)` 的设计初衷是先按 status 过滤再按 created_at 排序。但当查询为 `WHERE status = 'active' ORDER BY created_at DESC LIMIT 100` 时，虽然索引可以利用，但如果 status 字段选择性很低（如只有 active/inactive 两种值，各占 50%），索引扫描仍需扫描大量行。针对不同排序方向或不同字段组合的查询，需要调整索引列顺序和排序方向，使查询能直接从索引获取有序结果，避免额外排序。

## 诊断步骤

### 步骤 1

**操作**：查看索引未充分利用的慢查询

- 工具：`execute_sql`
- 条件：有索引但查询仍然慢
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
ORDER BY duration DESC
LIMIT 10;
```

**现象**：`pg_stat_activity` 中出现含 WHERE status 和 ORDER BY created_at 的查询响应超预期。

**现象分析**：有索引还慢，通常是索引设计与查询模式不匹配，需检查执行计划中是否有额外排序节点。

---
### 步骤 2

**操作**：获取查询执行计划，确认排序节点

- 工具：`execute_sql`
- 条件：已收集 sql_text
- 执行语句：

```sql
EXPLAIN (ANALYZE, VERBOSE, BUFFERS, FORMAT TEXT)
<slow_sql>
```

```sql
EXPLAIN ANALYZE
<slow_sql>
```

**现象**：执行计划显示先走 Index Scan 扫描大量行，再执行 Sort，说明索引无法直接提供有序结果。

**现象分析**：如果索引可以以正确方向提供有序数据，Sort 节点应该消失；其存在说明索引列顺序或方向需要调整。

---
### 步骤 3

**操作**：检查 users 表现有索引定义和使用率

- 工具：`execute_sql`
- 条件：确认索引设计问题
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
SELECT indexname, indexdef FROM pg_indexes WHERE tablename = '<target_table>';
SELECT indexrelname, idx_scan, idx_tup_read
FROM pg_stat_user_indexes WHERE tablename = '<target_table>';
SELECT status, COUNT(*) FROM users GROUP BY status; -- 检查选择性
```

**现象**：`idx_status_created` 定义为 `(status, created_at)` 升序，当 ORDER BY created_at DESC 时需要 Index Scan Backward，效率下降；当 status 选择性低时，扫描行数仍然很大。

**现象分析**：低选择性的前缀字段（status）限制了索引的过滤效率，需调整索引结构或添加专门的排序索引。

---
## 恢复手段

### 根因1：创建与查询排序方向匹配的复合索引（主要修复）

**描述**：为具体的查询模式（status 过滤 + created_at 降序排列）创建精确匹配的索引，使查询可直接从索引获取有序结果，消除额外 Sort 节点。

**具体命令**：

```sql
-- 删除旧索引
DROP INDEX IF EXISTS idx_status_created;

-- 为 "WHERE status='active' ORDER BY created_at DESC" 创建精确索引
CREATE INDEX CONCURRENTLY idx_status_created_desc
    ON users(status, created_at DESC);

-- 验证 Sort 节点消失
EXPLAIN ANALYZE
<slow_sql>
```

### 根因2：添加覆盖索引减少回表（辅助优化）

**描述**：在索引中包含查询所需的所有列，使查询走 Index Only Scan，完全避免回表操作，进一步提升性能。

**具体命令**：

```sql
-- 覆盖索引（包含查询需要的所有列）
CREATE INDEX CONCURRENTLY idx_status_created_covering
    ON users(status, created_at DESC, user_id, username, email, phone);

-- 验证 Index Only Scan
EXPLAIN ANALYZE
<slow_sql>
```

### 根因3：监控并清理未使用的索引（预防）

**描述**：定期检查索引使用率，删除 idx_scan=0 的冗余索引，减少写操作时的索引维护开销。

**具体命令**：

```sql
-- 查找未使用的索引（idx_scan=0）
SELECT indexrelname,
       pg_size_pretty(pg_relation_size(indexrelid)) AS index_size,
       idx_scan
FROM pg_stat_user_indexes
WHERE tablename = '<target_table>'
  AND idx_scan = 0
ORDER BY pg_relation_size(indexrelid) DESC;

-- 确认无用后删除
-- DROP INDEX CONCURRENTLY idx_status_created;
```
