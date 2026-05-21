---
id: correlated_subquery
name: "慢SQL - 相关子查询导致多次重复扫描"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "SELECT 子句中使用多个相关子查询，对同一表执行多次独立扫描，导致 CPU 飙升、响应超时"

symptoms:
  - "含多个子查询的统计报表响应时间超过5秒"
  - "CPU 使用率达到 90% 以上"
  - "执行计划出现多次 Seq Scan 或 Index Scan 在同一表上"
  - "高峰期系统响应缓慢，影响其他查询"

keywords:
  - 相关子查询
  - correlated subquery
  - 多次扫描
  - 重复计算
  - group by
  - 聚合
  - COUNT AVG SUM
  - 分类统计

triggers:
  - "SubPlan"
  - "InitPlan"
  - "duration:\\s*\\d{4,} ms"

evidence_required:
  - sql_text
  - explain_plan
evidence_optional:
  - index_info
  - table_stats

---

## 根因

`SELECT` 子句中的相关子查询（如 `(SELECT COUNT(*) FROM products WHERE category_id = c.category_id)`）对每一个外层行都独立执行一次内层查询。若有 N 个分类、M 个子查询，则 products 表会被扫描 N×M 次，产生大量重复 I/O 和 CPU 计算。使用 `LEFT JOIN ... GROUP BY` 替代后，products 表只需扫描一次。

## 诊断步骤

### 步骤 1

**操作**：查看慢查询，确认相关子查询存在

- 工具：`execute_sql`
- 条件：报表生成缓慢或 CPU 持续高负荷
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

**现象**：`pg_stat_activity` 中出现长时间活跃的统计查询，duration 超过 5s。

**现象分析**：统计查询通常对大表多次聚合，是相关子查询性能问题的典型场景。

---
### 步骤 2

**操作**：获取慢 SQL 执行计划，确认 SubPlan 节点

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

**现象**：执行计划中出现多个 `SubPlan` 节点，每个节点都对 products 表独立扫描。

**现象分析**：`SubPlan` 表明优化器无法将相关子查询合并为一次扫描，必须逐行执行。

---
### 步骤 3

**操作**：检查 products 表 category_id 字段索引情况

- 工具：`execute_sql`
- 条件：已确认多次子查询扫描
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

**现象**：products 表可能有 category_id 索引，但多次子查询调用仍产生大量 I/O。

**现象分析**：即使有索引，N 分类 × 3 子查询 = 大量独立查询仍是主要瓶颈；根本解决需改写 SQL。

---
## 恢复手段

### 根因1：相关子查询改写为 JOIN + GROUP BY（主要修复）

**描述**：将多个相关子查询合并为一次 `LEFT JOIN ... GROUP BY` 聚合，products 表只扫描一次，大幅降低 CPU 和 I/O 开销。

**具体命令**：

```sql
-- 优化后的 SQL（一次扫描完成所有聚合）
SELECT c.category_id, c.category_name,
       COUNT(p.product_id)  AS product_count,
       AVG(p.price)         AS avg_price,
       SUM(p.stock)         AS total_stock
FROM <target_table> c
LEFT JOIN <join_table_1> p ON c.category_id = p.category_id
GROUP BY c.category_id, c.category_name
ORDER BY c.category_id;

-- 确保 JOIN 字段有索引
CREATE INDEX CONCURRENTLY <index_name> ON <target_table>(<filter_col>);
```

### 根因2：高频统计查询使用物化视图缓存（辅助优化）

**描述**：对频繁访问的分类统计数据，创建物化视图并定期刷新，避免每次报表都实时聚合。

**具体命令**：

```sql
-- 创建物化视图
CREATE MATERIALIZED VIEW <mv_name> AS
SELECT c.category_id, c.category_name,
       COUNT(p.product_id)  AS product_count,
       AVG(p.price)         AS avg_price,
       SUM(p.stock)         AS total_stock
FROM <target_table> c
LEFT JOIN <join_table_2> p ON c.category_id = p.category_id
GROUP BY c.category_id, c.category_name;

-- 定期刷新（可通过 pg_cron 或外部调度）
REFRESH MATERIALIZED VIEW <mv_name>;

-- 查询物化视图（毫秒级响应）
SELECT * FROM <target_table_2> ORDER BY category_id;
```
