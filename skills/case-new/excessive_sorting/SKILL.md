---
id: excessive_sorting
name: "慢SQL - 多字段排序导致临时文件和 work_mem 不足"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "报表查询包含多个 ORDER BY 字段，work_mem 不足时触发磁盘临时文件排序，CPU 和 I/O 开销剧增"

symptoms:
  - "含多字段排序的报表查询响应超过8秒"
  - "CPU 使用率达到 95% 以上，临时表空间激增"
  - "执行计划出现 Sort Method: external merge Disk"
  - "高峰期系统几乎无法响应其他请求"

keywords:
  - 排序
  - ORDER BY
  - work_mem
  - 临时文件
  - external merge
  - sort
  - 多字段排序
  - 报表慢

triggers:
  - "Sort Method: external merge Disk"
  - "Sort Method: external"
  - "Batches:.*[2-9][0-9]*"
  - "duration:\\s*\\d{4,} ms"

evidence_required:
  - sql_text
  - explain_plan
evidence_optional:
  - table_stats

---

## 根因

报表查询包含多个 ORDER BY 排序字段（如 `ORDER BY sales_amount DESC, sales_quantity DESC, order_count DESC, product_name ASC`），在 `work_mem` 配置过小的情况下，排序操作无法在内存中完成，触发磁盘临时文件（external merge sort），导致大量额外 I/O。配合 GROUP BY 和多表 JOIN，问题被指数级放大。

## 诊断步骤

### 步骤 1

**操作**：查看慢查询，确认含多字段排序的报表 SQL

- 工具：`execute_sql`
- 条件：报表生成缓慢或 CPU 持续高负荷
- 执行语句：

```sql
SELECT pid, state, query_start, now() - query_start AS duration, query
FROM pg_stat_activity
WHERE state = 'active'
ORDER BY duration DESC
LIMIT 10;
```

**现象**：`pg_stat_activity` 中出现长时间活跃的报表查询，包含多个 ORDER BY 字段。

**现象分析**：多字段排序与聚合组合是报表慢查询的典型特征，需检查 work_mem 和执行计划。

---
### 步骤 2

**操作**：获取执行计划，确认是否触发磁盘排序

- 工具：`execute_sql`
- 条件：已收集 sql_text
- 执行语句：

```sql
EXPLAIN ANALYZE
<slow_sql>
```

**现象**：执行计划出现 `Sort Method: external merge Disk`，说明排序数据量超出 work_mem 限制。

**现象分析**：磁盘排序比内存排序慢 1-2 个数量级，是主要性能瓶颈，需增加 work_mem 或减少排序数据量。

---
### 步骤 3

**操作**：检查当前 work_mem 配置值

- 工具：`execute_sql`
- 条件：已确认磁盘排序
- 执行语句：

```sql
SHOW work_mem;

-- 检查临时表空间使用
SELECT spcname, pg_size_pretty(pg_tablespace_size(oid)) AS size
FROM pg_tablespace
WHERE spcname LIKE 'pg_temp%';
```

**现象**：`work_mem` 仅为 4MB（默认值），远不足以支撑大数据量的多字段内存排序。

**现象分析**：每个排序操作只能使用 work_mem 大小的内存，4MB 对于百万级数据的多字段排序严重不足。

---
### 步骤 4

**操作**：检查报表涉及表的索引覆盖情况

- 工具：`execute_sql`
- 条件：确认缺少过滤字段索引
- 执行语句：

```sql
SELECT * FROM pg_stat_user_indexes WHERE relname IN ('<target_table_1>', '<target_table_2>');
SELECT * FROM pg_indexes WHERE tablename IN ('<target_table_1>', '<target_table_2>');
```

**现象**：`sales_orders` 的 `order_date` 字段及 JOIN 字段缺少索引，导致大量数据参与后续排序。

**现象分析**：索引缺失使得过滤效率低，传入排序阶段的数据量更大，加剧 work_mem 压力。

---
## 恢复手段

### 根因1：增加 work_mem 并减少排序字段（主要修复）

**描述**：提高 work_mem 使排序在内存中完成，同时精简 ORDER BY 字段并添加 LIMIT 减少排序数据量。

**具体命令**：

```sql
-- 会话级临时增加 work_mem（立即生效，不影响其他会话）
SET work_mem = '64MB';

-- 精简排序字段并添加 LIMIT
SELECT p.product_id, p.product_name, c.category_name, p.brand,
       SUM(oi.quantity) AS sales_quantity,
       SUM(oi.subtotal) AS sales_amount,
       COUNT(DISTINCT o.order_id) AS order_count
FROM <target_table> o
JOIN <join_table_1> oi ON o.order_id = oi.order_id
JOIN <join_table_2> p ON oi.product_id = p.product_id
JOIN <join_table_3> c ON p.category_id = c.category_id
WHERE o.order_date BETWEEN '<start_date>' AND '<end_date>'
GROUP BY p.product_id, p.product_name, c.category_name, p.brand
ORDER BY sales_amount DESC, sales_quantity DESC
LIMIT 1000;

-- 全局调整（需评估总内存：work_mem × max_connections）
ALTER SYSTEM SET work_mem = '64MB';
SELECT pg_reload_conf();
```

### 根因2：为过滤和 JOIN 字段添加索引（辅助优化）

**描述**：为 WHERE 条件和 JOIN 字段添加索引，减少参与排序的数据量，从源头降低排序压力。

**具体命令**：

```sql
CREATE INDEX CONCURRENTLY <index_name>
    ON <target_table>(<filter_col>);
CREATE INDEX CONCURRENTLY <index_name>
    ON <target_table>(<filter_col>);
CREATE INDEX CONCURRENTLY <index_name>
    ON <target_table>(<filter_col>);
CREATE INDEX CONCURRENTLY <index_name>
    ON <target_table>(<filter_col>);
```

### 根因3：高频报表使用物化视图（完整修复）

**描述**：对日报、月报等固定周期查询，创建物化视图预先聚合数据，查询时只排序少量汇总行。

**具体命令**：

```sql
CREATE MATERIALIZED VIEW <mv_name> AS
SELECT o.order_date::date AS sale_date,
       p.product_id, p.product_name, c.category_name, p.brand,
       SUM(oi.quantity) AS sales_quantity,
       SUM(oi.subtotal) AS sales_amount,
       COUNT(DISTINCT o.order_id) AS order_count
FROM <target_table> o
JOIN <join_table_1> oi ON o.order_id = oi.order_id
JOIN <join_table_2> p ON oi.product_id = p.product_id
JOIN <join_table_3> c ON p.category_id = c.category_id
GROUP BY o.order_date::date, p.product_id, p.product_name, c.category_name, p.brand;

-- 定期刷新（每天凌晨执行）
REFRESH MATERIALIZED VIEW <mv_name>;
```
