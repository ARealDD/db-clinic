---
id: index_advisor
name: Index Design & Advisor
category: index
version: '1.0'
description: 'Advise on index creation, deletion, and optimization for GaussDB / openGauss.
  Covers B-tree, composite, partial, expression, and covering indexes. Also identifies
  redundant/unused indexes that add write overhead.

  '
symptoms:
- Sequence scan on large table despite adding index
- Index exists but is not being used
- Write performance degraded after adding many indexes
- Need to speed up query but unsure which index to create
- Index bloat / large index size
- Inconsistent query performance (sometimes fast, sometimes slow)
keywords:
- index
- 索引
- create index
- missing index
- unused index
- index not used
- b-tree
- composite index
- partial index
- covering index
- index bloat
- bloat
triggers:
- Seq Scan
- idx_scan.*=.*0
- CREATE INDEX
- DROP INDEX
evidence_required:
- sql_text
- index_info
evidence_optional:
- explain_plan
- table_stats
diagnosis_steps:
- step: 1
  action: Analyze the SQL for filter and join columns that need indexing
  tool: analyze_sql
  expected_evidence: null
- step: 2
  action: Get execution plan to confirm index usage
  tool: execute_sql
  expected_evidence: explain_plan
- step: 3
  action: Check existing indexes on relevant tables
  tool: execute_sql
  expected_evidence: index_info
- step: 4
  action: Check table statistics to confirm ANALYZE was run
  tool: execute_sql
  expected_evidence: table_stats
root_causes:
- id: index_column_order
  description: Composite index column order doesn't match query access pattern
  probability: 0.55
  indicators:
  - Index on (a, b) but query filters on b only
  - Seq Scan despite composite index existing
  - Index is used for one query but not another similar query
  recommendations:
  - Create a separate index with columns ordered to match filter selectivity (most
    selective first)
  - For range queries, the range column should be LAST in composite index
  - For equality filters, order doesn't matter much; for mixed, equality columns first
  sql_fixes:
  - CREATE INDEX idx_orders_user_status ON orders(user_id, status);  -- equality+equality
  - CREATE INDEX idx_orders_status_date ON orders(status, created_at);  -- equality+range
- id: missing_partial_index
  description: Full index on table but query always filters by a low-cardinality column
  probability: 0.4
  indicators:
  - Large index but most queries filter WHERE status='active'
  - Index on status but only 5% of rows have status='active'
  - Planner chooses seq scan over index due to low selectivity estimate
  recommendations:
  - 'Create a partial index: only covers the commonly queried subset'
  - Partial indexes are smaller, faster, and easier to maintain
  sql_fixes:
  - CREATE INDEX idx_orders_active ON orders(created_at) WHERE status = 'active';
  - CREATE INDEX idx_users_unverified ON users(created_at) WHERE email_verified =
    false;
- id: expression_index_needed
  description: Query applies function to column in WHERE, preventing index use
  probability: 0.35
  indicators:
  - WHERE LOWER(email) = 'user@example.com'
  - WHERE DATE(created_at) = '2024-01-01'
  - Function applied to column in Filter node
  recommendations:
  - Create expression (functional) index matching the expression in WHERE
  - Or rewrite query to avoid function on column side
  sql_fixes:
  - CREATE INDEX idx_users_lower_email ON users(LOWER(email));
  - CREATE INDEX idx_orders_date ON orders(DATE(created_at));
- id: unused_indexes
  description: Indexes exist but are never (or rarely) used, adding write overhead
  probability: 0.5
  indicators:
  - idx_scan = 0 after significant uptime
  - Many indexes on high-write table
  - Write performance degraded over time
  recommendations:
  - Review indexes with idx_scan = 0 after > 1 week of normal operation
  - Drop unused indexes (one at a time, after verifying)
  - Use CREATE INDEX CONCURRENTLY to avoid impact; DROP INDEX CONCURRENTLY to remove
  sql_fixes:
  - DROP INDEX CONCURRENTLY idx_unused_column;
- id: index_bloat
  description: Index has grown large due to dead tuples / frequent updates
  probability: 0.3
  indicators:
  - Index size >> table size
  - Index is on frequently updated column
  - Performance degraded over time without schema changes
  recommendations:
  - REINDEX CONCURRENTLY to rebuild index and reclaim space
  - Enable autovacuum more aggressively on this table
  sql_fixes:
  - REINDEX INDEX CONCURRENTLY idx_orders_status;
  - ALTER TABLE orders SET (autovacuum_vacuum_scale_factor = 0.05);
references:
- GaussDB index documentation
- 'Partial indexes: https://www.postgresql.org/docs/current/indexes-partial.html'
---

## 根因

Composite index column order doesn't match query access pattern；Full index on table but query always filters by a low-cardinality column；Query applies function to column in WHERE, preventing index use

## 诊断步骤

### 步骤 1

**操作**：Analyze the SQL for filter and join columns that need indexing

- 工具：`analyze_sql`

---

### 步骤 2

**操作**：Get execution plan to confirm index usage

- 工具：`execute_sql`
- 执行语句：

```sql
EXPLAIN (ANALYZE, VERBOSE, BUFFERS, FORMAT TEXT)
<slow_sql>
```

- 收集：explain_plan

---
### 步骤 3

**操作**：Check existing indexes on relevant tables

- 工具：`execute_sql`
- 执行语句：

```sql
SELECT schemaname, relname AS table_name, indexrelname AS index_name,
       idx_scan AS index_scans, idx_tup_read AS tuples_read, idx_tup_fetch AS tuples_fetched
FROM pg_stat_user_indexes
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY idx_scan ASC, relname
LIMIT 30;
```

- 收集：index_info

---
### 步骤 4

**操作**：Check table statistics to confirm ANALYZE was run

- 工具：`execute_sql`
- 执行语句：

```sql
SELECT schemaname, relname AS table_name,
       n_live_tup AS live_rows, n_dead_tup AS dead_rows,
       ROUND(100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0), 2) AS dead_pct,
       seq_scan, idx_scan, last_vacuum, last_autovacuum, last_analyze, last_autoanalyze
FROM pg_stat_user_tables
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY seq_scan DESC, dead_pct DESC NULLS LAST
LIMIT 30;
```

- 收集：table_stats

---
## 恢复手段

### 根因1：Composite index column order doesn't match query access pattern（概率 55%）

**描述**：Create a separate index with columns ordered to match filter selectivity (most selective first); For range queries, the range column should be LAST in composite index; For equality filters, order doesn't matter much; for mixed, equality columns first

**判断依据**：
- Index on (a, b) but query filters on b only
- Seq Scan despite composite index existing
- Index is used for one query but not another similar query

**具体命令**：

```sql
CREATE INDEX <index_name> ON <target_table>(<filter_col>, <groupby_col>);  -- equality+equality
CREATE INDEX <index_name> ON <target_table>(<filter_col>, <groupby_col>);  -- equality+range
```

### 根因2：Indexes exist but are never (or rarely) used, adding write overhead（概率 50%）

**描述**：Review indexes with idx_scan = 0 after > 1 week of normal operation; Drop unused indexes (one at a time, after verifying); Use CREATE INDEX CONCURRENTLY to avoid impact; DROP INDEX CONCURRENTLY to remove

**判断依据**：
- idx_scan = 0 after significant uptime
- Many indexes on high-write table
- Write performance degraded over time

**具体命令**：

```sql
DROP INDEX CONCURRENTLY idx_unused_column;
```

### 根因3：Full index on table but query always filters by a low-cardinality column（概率 40%）

**描述**：Create a partial index: only covers the commonly queried subset; Partial indexes are smaller, faster, and easier to maintain

**判断依据**：
- Large index but most queries filter WHERE status='active'
- Index on status but only 5% of rows have status='active'
- Planner chooses seq scan over index due to low selectivity estimate

**具体命令**：

```sql
CREATE INDEX <index_name> ON <target_table>(<filter_col>) WHERE status = 'active';
CREATE INDEX <index_name> ON <target_table>(<filter_col>) WHERE email_verified = false;
```

### 根因4：Query applies function to column in WHERE, preventing index use（概率 35%）

**描述**：Create expression (functional) index matching the expression in WHERE; Or rewrite query to avoid function on column side

**判断依据**：
- WHERE LOWER(email) = 'user@example.com'
- WHERE DATE(created_at) = '2024-01-01'
- Function applied to column in Filter node

**具体命令**：

```sql
CREATE INDEX <index_name> ON <target_table>(LOWER(<filter_col>));
CREATE INDEX <index_name> ON <target_table>(DATE(<filter_col>));
```

### 根因5：Index has grown large due to dead tuples / frequent updates（概率 30%）

**描述**：REINDEX CONCURRENTLY to rebuild index and reclaim space; Enable autovacuum more aggressively on this table

**判断依据**：
- Index size >> table size
- Index is on frequently updated column
- Performance degraded over time without schema changes

**具体命令**：

```sql
REINDEX INDEX CONCURRENTLY idx_orders_status;
ALTER TABLE orders SET (autovacuum_vacuum_scale_factor = 0.05);
```
