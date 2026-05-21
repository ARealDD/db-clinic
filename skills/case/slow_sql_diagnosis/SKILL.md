---
id: slow_sql_diagnosis
name: Slow SQL Diagnosis
category: slow_sql
version: '1.0'
description: 'Systematic diagnosis of slow SQL queries in GaussDB / openGauss. Covers
  missing indexes, bad execution plans, statistics staleness, cardinality mis-estimates,
  and parameter sniffing issues.

  '
symptoms:
- Query runs much slower than expected
- Query that used to be fast is now slow
- High CPU usage during query execution
- Full table scan on large table
- Query execution time > 1 second
- Application timeout on a specific query
- dbe_perf.statement_history shows high avg_elapse_time for a query
keywords:
- slow
- slowquery
- slow query
- timeout
- 超时
- 慢查询
- slow sql
- performance
- 性能
- execution time
- duration
- seq scan
- full table scan
- index not used
triggers:
- duration:\s*\d+ ms
- Seq Scan on
- Planning Time.*Execution Time
evidence_required:
- sql_text
- explain_plan
evidence_optional:
- system_view
- table_stats
- index_info
- wait_events
diagnosis_steps:
- step: 1
  action: Obtain the exact slow SQL text
  tool: null
  expected_evidence: sql_text
- step: 2
  action: Run SQL structural analysis to identify anti-patterns
  tool: analyze_sql
  expected_evidence: null
- step: 3
  action: Get EXPLAIN (non-ANALYZE) to see the plan without executing
  tool: execute_sql
  expected_evidence: explain_plan
  condition: sql_text collected
- step: 4
  action: Analyze the execution plan for expensive nodes and cardinality errors
  tool: analyze_execution_plan
  expected_evidence: null
  condition: explain_plan collected
- step: 5
  action: Check pg_stat_user_tables for table statistics freshness
  tool: execute_sql
  expected_evidence: table_stats
  condition: seq_scan or cardinality issue found
- step: 6
  action: Check existing index coverage on filter/join columns
  tool: execute_sql
  expected_evidence: index_info
  condition: seq_scan detected
- step: 7
  action: If query is still running, check wait events
  tool: execute_sql
  expected_evidence: wait_events
  condition: query is actively slow
- step: 8
  action: Get EXPLAIN ANALYZE if safe (SELECT only) to confirm actual vs estimated
    rows
  tool: execute_sql
  expected_evidence: explain_plan
  condition: cardinality mis-estimate suspected AND query is SELECT
root_causes:
- id: missing_index
  description: Missing index on filter or join column causing Seq Scan
  probability: 0.75
  indicators:
  - Seq Scan on large table
  - no index_scans in pg_stat_user_indexes for this table
  - WHERE clause on unindexed column
  recommendations:
  - 'Create index on the filter column: CREATE INDEX CONCURRENTLY idx_<table>_<col>
    ON <table>(<col>)'
  - For multi-column filters, create a composite index matching the WHERE clause order
  - Use CONCURRENTLY to avoid table lock during index creation
  sql_fixes:
  - CREATE INDEX CONCURRENTLY idx_orders_created_at ON orders(created_at);
  - CREATE INDEX CONCURRENTLY idx_orders_status_user ON orders(status, user_id) WHERE
    status = 'pending';
- id: stale_statistics
  description: Outdated table statistics causing cardinality mis-estimate
  probability: 0.65
  indicators:
  - estimated rows >> actual rows
  - last_analyze is old (> 1 day on active table)
  - n_dead_tup / n_live_tup ratio is high
  - Sequential scan despite index existing
  recommendations:
  - 'Run ANALYZE on the affected table: ANALYZE <table>;'
  - 'If many dead tuples: VACUUM ANALYZE <table>;'
  - 'Check autovacuum settings: autovacuum_analyze_scale_factor may be too high'
  sql_fixes:
  - ANALYZE orders;
  - VACUUM ANALYZE orders;
  - ALTER TABLE orders SET (autovacuum_analyze_scale_factor = 0.01);
- id: bad_join_order
  description: Planner chose suboptimal join order or join strategy
  probability: 0.45
  indicators:
  - Nested Loop with large inner table
  - Hash Join with row count mismatch
  - Multiple tables joined, small result but high cost
  recommendations:
  - Run ANALYZE on all joined tables to refresh statistics
  - Check join columns are indexed on both sides
  - Consider SET join_collapse_limit / enable_hashjoin / enable_nestloop hints
  - Rewrite query to provide explicit join hints if GaussDB supports them
  config_fixes:
  - SET enable_nestloop = off;  -- test in session only
  - SET join_collapse_limit = 1;  -- force explicit join order
- id: implicit_type_conversion
  description: Column type mismatch causing implicit cast that prevents index use
  probability: 0.35
  indicators:
  - 'Filter: <type_cast> vs <column>'
  - WHERE varchar_col = 12345 (int literal on varchar)
  - WHERE date_col = '2024-01-01' (string on date)
  recommendations:
  - Match the literal type to the column type in the WHERE clause
  - If column type cannot change, create a function-based index
  sql_fixes:
  - '-- Change: WHERE user_id = ''12345'' TO: WHERE user_id = 12345'
  - CREATE INDEX idx_cast ON orders ((user_id::text));
- id: high_row_estimate_or_actual
  description: Query returns or processes too many rows
  probability: 0.3
  indicators:
  - actual rows >> expected, result set is very large
  - No LIMIT clause
  - No WHERE clause or very broad filter
  recommendations:
  - Add LIMIT to paginate results
  - Narrow the WHERE clause
  - Consider partial index for common filters
  sql_fixes:
  - SELECT ... FROM orders WHERE status = 'pending' LIMIT 100 OFFSET 0;
case_examples:
- summary: 'ORDER table with 50M rows; query filtering on (status, created_at) was
    doing Seq Scan. Root cause: composite index existed on (created_at, status) but
    query predicate order was (status, created_at), causing index skip. Fix: CREATE
    INDEX on (status, created_at) — query went from 45s to 0.3s.

    '
  sql: SELECT * FROM orders WHERE status = 'pending' AND created_at > now() - interval
    '7 days'
  root_cause: missing_index
- summary: 'After bulk-loading 10M rows, queries became slow. Last ANALYZE was 2 weeks
    ago. ANALYZE updated statistics and query time dropped from 12s to 0.4s.

    '
  root_cause: stale_statistics
references:
- https://docs.opengauss.org/zh/docs/latest/docs/DatabaseAdministrationGuide/Routine-Maintenance.html
- dbe_perf.statement_history documentation
- GaussDB query plan hints documentation
---

## 根因

Missing index on filter or join column causing Seq Scan；Outdated table statistics causing cardinality mis-estimate；Planner chose suboptimal join order or join strategy

## 诊断步骤

### 步骤 1

**操作**：Obtain the exact slow SQL text

- 收集：sql_text

---

### 步骤 2

**操作**：Run SQL structural analysis to identify anti-patterns

- 工具：`analyze_sql`

---

### 步骤 3

**操作**：Get EXPLAIN (non-ANALYZE) to see the plan without executing

- 工具：`execute_sql`
- 条件：sql_text collected
- 收集：explain_plan

```sql
-- 将 <your_sql_here> 替换为实际的慢查询语句
EXPLAIN (ANALYZE, VERBOSE, BUFFERS, FORMAT TEXT)
<your_sql_here>;
```

---

### 步骤 4

**操作**：Analyze the execution plan for expensive nodes and cardinality errors

- 工具：`analyze_execution_plan`
- 条件：explain_plan collected

---

### 步骤 5

**操作**：Check pg_stat_user_tables for table statistics freshness

- 工具：`execute_sql`
- 条件：seq_scan or cardinality issue found
- 收集：table_stats

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

---

### 步骤 6

**操作**：Check existing index coverage on filter/join columns

- 工具：`execute_sql`
- 条件：seq_scan detected
- 收集：index_info

```sql
SELECT schemaname, relname AS table_name, indexrelname AS index_name,
       idx_scan AS index_scans, idx_tup_read AS tuples_read, idx_tup_fetch AS tuples_fetched
FROM pg_stat_user_indexes
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY idx_scan ASC, relname
LIMIT 30;
```

---

### 步骤 7

**操作**：If query is still running, check wait events

- 工具：`execute_sql`
- 条件：query is actively slow
- 收集：wait_events

```sql
SELECT wait_status, COUNT(*) AS session_count,
       ARRAY_AGG(pid) AS pids
FROM pg_stat_activity
WHERE wait_status IS NOT NULL AND pid <> pg_backend_pid()
GROUP BY wait_status
ORDER BY session_count DESC;
```

---

### 步骤 8

**操作**：Get EXPLAIN ANALYZE if safe (SELECT only) to confirm actual vs estimated rows

- 工具：`execute_sql`
- 条件：cardinality mis-estimate suspected AND query is SELECT
- 收集：explain_plan

```sql
-- 将 <your_sql_here> 替换为实际的慢查询语句
EXPLAIN (ANALYZE, VERBOSE, BUFFERS, FORMAT TEXT)
<your_sql_here>;
```

---

## 恢复手段

### 根因1：Missing index on filter or join column causing Seq Scan（概率 75%）

**描述**：Create index on the filter column: CREATE INDEX CONCURRENTLY idx_<table>_<col> ON <table>(<col>); For multi-column filters, create a composite index matching the WHERE clause order; Use CONCURRENTLY to avoid table lock during index creation

**判断依据**：
- Seq Scan on large table
- no index_scans in pg_stat_user_indexes for this table
- WHERE clause on unindexed column

**具体命令**：

```sql
CREATE INDEX CONCURRENTLY idx_orders_created_at ON orders(created_at);
CREATE INDEX CONCURRENTLY idx_orders_status_user ON orders(status, user_id) WHERE status = 'pending';
```

### 根因2：Outdated table statistics causing cardinality mis-estimate（概率 65%）

**描述**：Run ANALYZE on the affected table: ANALYZE <table>;; If many dead tuples: VACUUM ANALYZE <table>;; Check autovacuum settings: autovacuum_analyze_scale_factor may be too high

**判断依据**：
- estimated rows >> actual rows
- last_analyze is old (> 1 day on active table)
- n_dead_tup / n_live_tup ratio is high
- Sequential scan despite index existing

**具体命令**：

```sql
ANALYZE orders;
VACUUM ANALYZE orders;
ALTER TABLE orders SET (autovacuum_analyze_scale_factor = 0.01);
```

### 根因3：Planner chose suboptimal join order or join strategy（概率 45%）

**描述**：Run ANALYZE on all joined tables to refresh statistics; Check join columns are indexed on both sides; Consider SET join_collapse_limit / enable_hashjoin / enable_nestloop hints; Rewrite query to provide explicit join hints if GaussDB supports them

**判断依据**：
- Nested Loop with large inner table
- Hash Join with row count mismatch
- Multiple tables joined, small result but high cost

**具体命令**：

```sql
SET enable_nestloop = off;  -- test in session only
SET join_collapse_limit = 1;  -- force explicit join order
```

### 根因4：Column type mismatch causing implicit cast that prevents index use（概率 35%）

**描述**：Match the literal type to the column type in the WHERE clause; If column type cannot change, create a function-based index

**判断依据**：
- Filter: <type_cast> vs <column>
- WHERE varchar_col = 12345 (int literal on varchar)
- WHERE date_col = '2024-01-01' (string on date)

**具体命令**：

```sql
-- Change: WHERE user_id = '12345' TO: WHERE user_id = 12345
CREATE INDEX idx_cast ON orders ((user_id::text));
```

### 根因5：Query returns or processes too many rows（概率 30%）

**描述**：Add LIMIT to paginate results; Narrow the WHERE clause; Consider partial index for common filters

**判断依据**：
- actual rows >> expected, result set is very large
- No LIMIT clause
- No WHERE clause or very broad filter

**具体命令**：

```sql
SELECT ... FROM orders WHERE status = 'pending' LIMIT 100 OFFSET 0;
```
