---
id: complex_join_missing_index
name: "慢SQL - 复杂多表 JOIN 缺少复合索引"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "多表 JOIN 报表查询中，过滤字段和 JOIN 字段缺少复合索引，执行计划选择 Nested Loop 或大量 Seq Scan，响应超时"

symptoms:
  - "4表以上 JOIN 的综合报表响应超过10秒"
  - "执行计划出现 Nested Loop 或多处 Seq Scan"
  - "CPU 和 I/O 同时高位，查询日志中出现连接堆积"
  - "并发分析查询时系统响应急剧下降"

keywords:
  - 多表join
  - 复合索引
  - JOIN索引
  - 分析报表
  - nested loop
  - hash join
  - 索引缺失
  - 执行计划低效

triggers:
  - "Nested Loop"
  - "Seq Scan on order_items"
  - "Seq Scan on customers"
  - "duration:\\s*\\d{4,} ms"

evidence_required:
  - sql_text
  - explain_plan
  - index_info
evidence_optional:
  - table_stats

---

## 根因

多表 JOIN 报表查询涉及 customers、orders、order_items、products 四张大表，WHERE 条件（order_date 范围、category_id 过滤）和 ORDER BY（order_date, total_amount）字段缺少专门的复合索引，优化器没有可用的高效路径，被迫选择 Nested Loop 或全表扫描组合，在千万级数据量下执行时间超时。

## 诊断步骤

### 步骤 1

**操作**：查看多表 JOIN 慢查询

- 工具：`execute_sql`
- 条件：报表分析请求超时
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

**现象**：`pg_stat_activity` 中出现含多个 JOIN 的报表查询持续活跃，duration 超过 10s。

**现象分析**：多表 JOIN 慢查询需结合执行计划分析各个连接节点的行数估算和扫描方式。

---
### 步骤 2

**操作**：获取完整报表 SQL 的执行计划

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

**现象**：执行计划出现多处 Seq Scan（order_items, customers）或 Nested Loop 处理大结果集，总执行时间超过 10s。

**现象分析**：每处 Seq Scan 或不合理的 Nested Loop 都是独立的性能瓶颈，需逐一确认对应字段是否有索引。

---
### 步骤 3

**操作**：检查各表关键字段的索引状态

- 工具：`execute_sql`
- 条件：已确认执行计划瓶颈点
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
SELECT tablename, indexname, indexdef
FROM pg_indexes
WHERE tablename IN ('<target_table_1>', '<target_table_2>', '<target_table_3>', '<target_table_4>');

SELECT relname, seq_scan, idx_scan
FROM pg_stat_user_tables
WHERE relname IN ('<target_table_1>', '<target_table_2>', '<target_table_3>', '<target_table_4>');
```

**现象**：orders 表缺少 (order_date DESC, total_amount DESC) 复合索引，order_items 缺少覆盖 JOIN 的索引。

**现象分析**：明确需要补充的索引后，可针对性建立复合索引，一次性解决 WHERE 过滤、JOIN 连接和 ORDER BY 排序三类需求。

---
## 恢复手段

### 根因1：为 WHERE/ORDER BY/JOIN 字段创建复合索引（主要修复）

**描述**：针对报表查询的 WHERE 条件、ORDER BY 和 JOIN 字段分别建立复合索引，使优化器在每个节点都能走 Index Scan。

**具体命令**：

```sql
-- orders: 支持日期范围过滤和排序
CREATE INDEX CONCURRENTLY <index_name>
    ON <target_table>(<filter_col>, <groupby_col>);

-- order_items: 支持 JOIN 连接
CREATE INDEX CONCURRENTLY <index_name>
    ON <target_table>(<filter_col>, <groupby_col>);

-- products: 支持分类过滤和 JOIN
<target_table> INDEX CONCURRENTLY <index_name>
    ON <target_table>(<filter_col>, <groupby_col>);

-- customers: 支持 JOIN 并覆盖查询列
CREATE INDEX CONCURRENTLY <index_name>
    ON <target_table>(<filter_col>, <groupby_col>, <join_col>);

-- 更新统计信息
ANALYZE <target_table>; ANALYZE <target_table>; ANALYZE <target_table>; ANALYZE <target_table>;
```

### 根因2：先过滤再 JOIN 减少数据量（辅助优化）

**描述**：使用 CTE 先将 orders（按日期）和 products（按分类）分别过滤到最小结果集，再进行 JOIN，减少 JOIN 阶段处理的数据量。

**具体命令**：

```sql
WITH filtered_orders AS (
    SELECT order_id, customer_id, order_date, total_amount
    FROM <target_table>
    WHERE order_date BETWEEN '<start_date>' AND '<end_date>'
),
filtered_products AS (
    SELECT product_id, product_name, category_id
    FROM <target_table_2>
    WHERE category_id = 101
)
SELECT c.customer_id, c.username, c.email,
       o.order_id, o.order_date, o.total_amount,
       p.product_id, p.product_name, p.category_id,
       oi.quantity, oi.unit_price
FROM <target_table_3> o
JOIN <join_table_3>     c  ON c.customer_id = o.customer_id
JOIN <join_table_4>   oi ON o.order_id = oi.order_id
JOIN <join_table_5> p ON oi.product_id = p.product_id
ORDER BY o.order_date DESC, o.total_amount DESC
LIMIT 1000;
```

### 根因3：物化视图预计算高频报表（完整修复）

**描述**：对于固定模式的分析报表，创建物化视图预先完成 JOIN 和聚合，查询时直接按索引过滤预计算结果。

**具体命令**：

```sql
CREATE MATERIALIZED VIEW <mv_name> AS
SELECT c.customer_id, c.username, c.email,
       o.order_id, o.order_date, o.total_amount,
       p.product_id, p.product_name, p.category_id,
       oi.quantity, oi.unit_price
FROM <target_table> c
JOIN <join_table_1>      o  ON c.customer_id = o.customer_id
JOIN <join_table_2> oi ON o.order_id = oi.order_id
JOIN <join_table_3>    p  ON oi.product_id = p.product_id;

CREATE INDEX <index_name> order_analysis_mv(<filter_col>, <groupby_col>);
CREATE INDEX <index_name> order_analysis_mv(<filter_col>);

REFRESH MATERIALIZED VIEW <mv_name> <mv_name>;
```
