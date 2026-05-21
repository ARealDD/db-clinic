---
id: missing_index_order_query
name: "慢SQL - 订单查询缺少索引导致全表扫描"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "订单/业务表因查询条件字段缺少索引，导致多表JOIN查询全表扫描，响应超时"

symptoms:
  - "按用户ID查询订单响应超过3秒"
  - "多表JOIN查询执行计划出现 Seq Scan"
  - "高峰期 CPU 使用率达到 80% 以上"
  - "I/O 等待时间增加"

keywords:
  - 缺索引
  - missing index
  - 全表扫描
  - seq scan
  - 订单查询
  - user_id
  - 慢查询
  - join慢

triggers:
  - "Seq Scan on orders"
  - "Seq Scan on"
  - "duration:\\s*\\d{4,} ms"

evidence_required:
  - sql_text
  - explain_plan
evidence_optional:
  - index_info
  - table_stats
---

## 根因

orders 表的查询条件字段（如 user_id）缺少索引，导致每次查询都需要对全表进行顺序扫描。在数据量达到百万级时，即使主键索引存在，若 WHERE 条件命中的是无索引字段，仍会触发全表扫描，配合 JOIN 和 ORDER BY 操作，查询耗时急剧上升。

## 诊断步骤

### 步骤 1

**操作**：查看当前慢查询及其持续时间

- 工具：`execute_sql`
- 条件：用户反馈查询慢或监控发现慢查询告警

**现象**：`pg_stat_activity` 中出现同一查询持续活跃，duration 超过 3s。

**现象分析**：持续活跃的查询往往是全表扫描或缺索引导致的，需要获取 SQL 文本进行进一步分析。

```sql
SELECT pid, state, query_start, now() - query_start AS duration, query
FROM pg_stat_activity
WHERE state = 'active'
ORDER BY duration DESC
LIMIT 10;
```

---

### 步骤 2

**操作**：获取慢 SQL 执行计划

- 工具：`execute_sql`
- 条件：已收集 sql_text

```sql
-- 将 <your_sql_here> 替换为实际的慢查询语句
EXPLAIN (ANALYZE, VERBOSE, BUFFERS, FORMAT TEXT)
<your_sql_here>;
```

**现象**：执行计划显示 `Seq Scan on orders`，orders 表行数估算与实际相符，但无 Index Scan 节点。

**现象分析**：查询条件（如 `WHERE o.user_id = 12345`）命中的字段无索引，优化器别无选择只能全表扫描。

```sql
EXPLAIN ANALYZE
SELECT o.order_id, o.order_status, o.create_time, o.total_amount,
       o.payment_method, p.product_name, oi.quantity, oi.unit_price
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
JOIN products p ON oi.product_id = p.product_id
WHERE o.user_id = 12345
ORDER BY o.create_time DESC;
```

---

### 步骤 3

**操作**：检查相关表的索引覆盖情况

- 工具：`execute_sql`
- 条件：已确认 Seq Scan 存在

```sql
SELECT schemaname, relname AS table_name, indexrelname AS index_name,
       idx_scan AS index_scans, idx_tup_read AS tuples_read, idx_tup_fetch AS tuples_fetched
FROM pg_stat_user_indexes
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY idx_scan ASC, relname
LIMIT 30;
```

**现象**：`pg_indexes` 中 orders 表只有主键 order_id 索引，user_id 字段无任何索引。

**现象分析**：user_id 是高频过滤字段，缺少索引是根本原因，需要创建索引。

```sql
SELECT * FROM pg_stat_user_indexes WHERE relname = 'orders';
SELECT * FROM pg_indexes WHERE tablename = 'orders';
```

---

### 步骤 4

**操作**：确认统计信息时效性

- 工具：`execute_sql`
- 条件：需要排除统计信息陈旧导致的误判

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

**现象**：`last_analyze` / `last_autoanalyze` 在合理时间内，统计信息不是问题根因。

**现象分析**：排除统计信息陈旧因素，确认是索引缺失导致优化器选择全表扫描。

```sql
SELECT relname, last_analyze, last_autoanalyze, n_live_tup, n_dead_tup
FROM pg_stat_user_tables
WHERE relname = 'orders';
```

---

## 恢复手段

### 根因1：查询条件字段缺少索引（主要修复）

**描述**：为 WHERE 条件字段和排序字段创建适合查询模式的复合索引，使优化器可以使用 Index Scan 替代 Seq Scan。使用 CONCURRENTLY 选项避免锁表。

**具体命令**：

```sql
-- 单列索引（快速修复）
CREATE INDEX CONCURRENTLY idx_orders_user_id ON orders(user_id);

-- 复合索引（同时优化排序，推荐）
CREATE INDEX CONCURRENTLY idx_orders_user_id_create_time
    ON orders(user_id, create_time DESC);

-- 验证索引是否生效
EXPLAIN ANALYZE
SELECT o.order_id, o.order_status, o.create_time, o.total_amount,
       o.payment_method, p.product_name, oi.quantity, oi.unit_price
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
JOIN products p ON oi.product_id = p.product_id
WHERE o.user_id = 12345
ORDER BY o.create_time DESC
LIMIT 100;
```

### 根因2：SQL 返回行数过多（辅助优化）

**描述**：查询未添加 LIMIT 约束，即使创建索引后仍可能返回大量数据。添加分页查询以限制返回行数。

**具体命令**：

```sql
-- 添加 LIMIT 限制返回行数
SELECT o.order_id, o.order_status, o.create_time, o.total_amount,
       o.payment_method, p.product_name, oi.quantity, oi.unit_price
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
JOIN products p ON oi.product_id = p.product_id
WHERE o.user_id = 12345
ORDER BY o.create_time DESC
LIMIT 100;
```

### 根因3：JOIN 表缺少外键索引（完整修复）

**描述**：order_items 和 products 表的 JOIN 字段也需要索引支撑，确保整个查询链路高效。

**具体命令**：

```sql
CREATE INDEX CONCURRENTLY idx_order_items_order_id ON order_items(order_id);
CREATE INDEX CONCURRENTLY idx_order_items_product_id ON order_items(product_id);
ANALYZE orders;
ANALYZE order_items;
```
