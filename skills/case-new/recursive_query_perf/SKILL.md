---
id: recursive_query_perf
name: "慢SQL - 递归 CTE 的 parent_id 字段缺少索引"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "WITH RECURSIVE 递归查询中，JOIN 条件使用的 parent_dept_id 等外键字段缺少索引，每次递归迭代都需要全表扫描，层级越深越慢"

symptoms:
  - "递归层级结构查询（组织架构/分类树）响应超过1秒"
  - "层级越深查询越慢，呈线性或指数增长"
  - "执行计划中递归迭代部分出现 Seq Scan"
  - "并发递归查询时系统 CPU 持续高位"

keywords:
  - 递归查询
  - WITH RECURSIVE
  - 递归CTE
  - parent_id
  - parent_dept_id
  - 层级结构
  - 树形查询
  - 递归迭代

triggers:
  - "CTE Scan"
  - "WorkTable Scan"
  - "Seq Scan on departments"
  - "duration:\\s*\\d{3,} ms"

evidence_required:
  - sql_text
  - explain_plan
  - index_info
evidence_optional:
  - table_stats

---

## 根因

`WITH RECURSIVE` 每次迭代都执行一次 `JOIN departments d ON d.parent_dept_id = sd.dept_id`。如果 `departments.parent_dept_id` 没有索引，每次迭代都需要全表扫描 departments 来找到子节点。层级深度 N 意味着 N 次全表扫描，时间复杂度 O(N × 表大小)。为 `parent_dept_id` 添加索引后，每次迭代从 O(表大小) 降为 O(log N + 结果数)。

## 诊断步骤

### 步骤 1

**操作**：查看递归查询的慢查询情况

- 工具：`execute_sql`
- 条件：层级结构查询响应慢
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

**现象**：`pg_stat_activity` 中出现 WITH RECURSIVE 查询持续活跃，层级越深越慢。

**现象分析**：递归查询的性能问题通常由 JOIN 字段缺索引引起，需结合执行计划确认。

---
### 步骤 2

**操作**：获取递归 SQL 的执行计划

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

**现象**：执行计划的递归部分（Recursive Union）中，对 departments 表的扫描显示为 `Seq Scan`，随着递归层数增加，总执行时间线性增长。

**现象分析**：递归部分的 Seq Scan 是性能瓶颈，意味着每次迭代都要全表扫描寻找子节点。

---
### 步骤 3

**操作**：检查 departments 表的 parent_dept_id 索引状态

- 工具：`execute_sql`
- 条件：已确认递归 Seq Scan
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
SELECT indexrelname, idx_scan FROM pg_stat_user_indexes WHERE tablename = '<target_table>';
SELECT seq_scan, idx_scan FROM pg_stat_user_tables WHERE relname = '<target_table>';
```

**现象**：`pg_indexes` 显示 departments 表只有主键 dept_id，parent_dept_id 无索引；idx_scan 统计中 parent_dept_id 相关计数为 0。

**现象分析**：parent_dept_id 缺少索引是根本原因，添加索引后递归迭代可走 Index Scan，性能可提升数十倍。

---
## 恢复手段

### 根因1：为 parent_dept_id 创建索引（主要修复）

**描述**：为递归查询 JOIN 条件中使用的 parent_dept_id 字段创建索引，使每次递归迭代可走 Index Scan，性能提升可达数十倍。

**具体命令**：

```sql
-- 为递归 JOIN 字段创建索引
CREATE INDEX CONCURRENTLY idx_departments_parent_dept_id
    ON departments(parent_dept_id);

-- 复合索引（同时支持查找子节点和返回基本信息）
CREATE INDEX CONCURRENTLY idx_departments_parent_dept
    ON departments(parent_dept_id, dept_id, dept_name);

-- 验证索引被递归查询使用
EXPLAIN ANALYZE
<slow_sql>
```

### 根因2：使用物化路径替代实时递归查询（辅助优化）

**描述**：在 departments 表中增加 `path` 字段存储从根到当前节点的路径（如 "1.2.5.10"），查询子节点只需 `WHERE path LIKE '1.%'`，完全避免递归。

**具体命令**：

```sql
-- 添加物化路径字段
ALTER TABLE departments ADD COLUMN path TEXT;

-- 初始化路径（根节点）
UPDATE <target_table> SET path = dept_id::TEXT WHERE parent_dept_id IS NULL;

-- 递归构建路径（执行一次即可）
WITH RECURSIVE build_path AS (
    SELECT dept_id, dept_id::TEXT AS path
    FROM <target_table> WHERE parent_dept_id IS NULL
    UNION ALL
    SELECT d.dept_id, bp.path || '.' || d.dept_id::TEXT
    FROM <target_table> d
    JOIN <target_table_2> bp ON d.parent_dept_id = bp.dept_id
)
UPDATE <target_table> d SET path = bp.path
FROM <target_table_2> bp WHERE d.dept_id = bp.dept_id;

-- 为 path 字段创建索引（支持前缀 LIKE）
CREATE INDEX CONCURRENTLY <index_name> ON <target_table>(path text_pattern_ops);

-- 查询子部门（无需递归，毫秒级响应）
SELECT * FROM <target_table>
WHERE path LIKE (SELECT path FROM <target_table> WHERE dept_id = 1) || '.%'
ORDER BY path;
```

### 根因3：物化视图缓存层级结果（完整修复）

**描述**：对于不频繁变化的组织架构，创建包含完整层级信息的物化视图，并定期刷新，避免每次查询时重新执行递归。

**具体命令**：

```sql
CREATE MATERIALIZED VIEW <mv_name> AS
WITH RECURSIVE dept_tree AS (
    SELECT dept_id, dept_name, parent_dept_id,
           1 AS level, dept_id::TEXT AS path
    FROM <target_table> WHERE parent_dept_id IS NULL
    UNION ALL
    SELECT d.dept_id, d.dept_name, d.parent_dept_id,
           dt.level + 1, dt.path || '.' || d.dept_id::TEXT
    FROM <target_table> d
    JOIN <target_table_2> dt ON d.parent_dept_id = dt.dept_id
)
SELECT * FROM <target_table_2>;

CREATE INDEX <index_name> department_hierarchy(<filter_col>);
CREATE INDEX <index_name> department_hierarchy(<filter_col>);
CREATE INDEX <index_name> department_hierarchy(path text_pattern_ops);

-- 组织架构变更时刷新
REFRESH MATERIALIZED VIEW <mv_name>;
```
