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

**现象**：`pg_stat_activity` 中出现含多个 JOIN 的报表查询持续活跃，duration 超过 10s。

**现象分析**：多表 JOIN 慢查询需结合执行计划分析各个连接节点的行数估算和扫描方式。

```sql
SELECT pid, state, query_start, now() - query_start AS duration, query
FROM pg_stat_activity
WHERE state = 'active'
ORDER BY duration DESC
LIMIT 10;
```

---

### 步骤 2

**操作**：获取完整报表 SQL 的执行计划

- 工具：`execute_sql`
- 条件：已收集 sql_text

```sql
-- 将 <your_sql_here> 替换为实际的慢查询语句
EXPLAIN (ANALYZE, VERBOSE, BUFFERS, FORMAT TEXT)
<your_sql_here>;
```

**现象**：执行计划出现多处 Seq Scan（order_items, customers）或 Nested Loop 处理大结果集，总执行时间超过 10s。

**现象分析**：每处 Seq Scan 或不合理的 Nested Loop 都是独立的性能瓶颈，需逐一确认对应字段是否有索引。

```sql
EXPLAIN ANALYZE
SELECT c.customer_id, c.username, c.email,
       o.order_id, o.order_date, o.total_amount,
       p.product_id, p.product_name, p.category_id,
       oi.quantity, oi.unit_price
FROM customers c
JOIN orders     o  ON c.customer_id = o.customer_id
JOIN order_items oi ON o.order_id = oi.order_id
JOIN products   p  ON oi.product_id = p.product_id
WHERE o.order_date BETWEEN '2023-01-01' AND '2023-01-31'
  AND p.category_id = 101
ORDER BY o.order_date DESC, o.total_amount DESC
LIMIT 1000;
```

---

### 步骤 3

**操作**：检查各表关键字段的索引状态

- 工具：`execute_sql`
- 条件：已确认执行计划瓶颈点

```sql
SELECT schemaname, relname AS table_name, indexrelname AS index_name,
       idx_scan AS index_scans, idx_tup_read AS tuples_read, idx_tup_fetch AS tuples_fetched
FROM pg_stat_user_indexes
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY idx_scan ASC, relname
LIMIT 30;
```

**现象**：orders 表缺少 (order_date DESC, total_amount DESC) 复合索引，order_items 缺少覆盖 JOIN 的索引。

**现象分析**：明确需要补充的索引后，可针对性建立复合索引，一次性解决 WHERE 过滤、JOIN 连接和 ORDER BY 排序三类需求。

```sql
SELECT tablename, indexname, indexdef
FROM pg_indexes
WHERE tablename IN ('customers', 'orders', 'order_items', 'products');

SELECT relname, seq_scan, idx_scan
FROM pg_stat_user_tables
WHERE relname IN ('customers', 'orders', 'order_items', 'products');
```

---

## 恢复手段

### 根因1：为 WHERE/ORDER BY/JOIN 字段创建复合索引（主要修复）

**描述**：针对报表查询的 WHERE 条件、ORDER BY 和 JOIN 字段分别建立复合索引，使优化器在每个节点都能走 Index Scan。

**具体命令**：

```sql
-- orders: 支持日期范围过滤和排序
CREATE INDEX CONCURRENTLY idx_orders_date_amount
    ON orders(order_date DESC, total_amount DESC);

-- order_items: 支持 JOIN 连接
CREATE INDEX CONCURRENTLY idx_order_items_order_product
    ON order_items(order_id, product_id);

-- products: 支持分类过滤和 JOIN
CREATE INDEX CONCURRENTLY idx_products_category_id_product
    ON products(category_id, product_id);

-- customers: 支持 JOIN 并覆盖查询列
CREATE INDEX CONCURRENTLY idx_customers_cover
    ON customers(customer_id, username, email);

-- 更新统计信息
ANALYZE orders; ANALYZE order_items; ANALYZE products; ANALYZE customers;
```

### 根因2：先过滤再 JOIN 减少数据量（辅助优化）

**描述**：使用 CTE 先将 orders（按日期）和 products（按分类）分别过滤到最小结果集，再进行 JOIN，减少 JOIN 阶段处理的数据量。

**具体命令**：

```sql
WITH filtered_orders AS (
    SELECT order_id, customer_id, order_date, total_amount
    FROM orders
    WHERE order_date BETWEEN '2023-01-01' AND '2023-01-31'
),
filtered_products AS (
    SELECT product_id, product_name, category_id
    FROM products
    WHERE category_id = 101
)
SELECT c.customer_id, c.username, c.email,
       o.order_id, o.order_date, o.total_amount,
       p.product_id, p.product_name, p.category_id,
       oi.quantity, oi.unit_price
FROM filtered_orders o
JOIN customers     c  ON c.customer_id = o.customer_id
JOIN order_items   oi ON o.order_id = oi.order_id
JOIN filtered_products p ON oi.product_id = p.product_id
ORDER BY o.order_date DESC, o.total_amount DESC
LIMIT 1000;
```

### 根因3：物化视图预计算高频报表（完整修复）

**描述**：对于固定模式的分析报表，创建物化视图预先完成 JOIN 和聚合，查询时直接按索引过滤预计算结果。

**具体命令**：

```sql
CREATE MATERIALIZED VIEW order_analysis_mv AS
SELECT c.customer_id, c.username, c.email,
       o.order_id, o.order_date, o.total_amount,
       p.product_id, p.product_name, p.category_id,
       oi.quantity, oi.unit_price
FROM customers c
JOIN orders      o  ON c.customer_id = o.customer_id
JOIN order_items oi ON o.order_id = oi.order_id
JOIN products    p  ON oi.product_id = p.product_id;

CREATE INDEX ON order_analysis_mv(order_date DESC, total_amount DESC);
CREATE INDEX ON order_analysis_mv(category_id);

REFRESH MATERIALIZED VIEW CONCURRENTLY order_analysis_mv;
```
