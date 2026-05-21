---
id: slow_groupby_report
name: "慢SQL - GROUP BY 大表聚合缺索引导致报表超时"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "统计报表 GROUP BY 在大表上执行，缺少合适的复合索引，配合多表 JOIN 导致 CPU 飙升、响应超时"

symptoms:
  - "销售统计报表生成时间超过10秒"
  - "CPU 使用率 95% 以上，I/O 频繁"
  - "执行计划出现 GroupAggregate 或 HashAggregate 处理大量行"
  - "多个报表并发时系统几乎无法响应"

keywords:
  - GROUP BY
  - 聚合慢
  - HashAggregate
  - GroupAggregate
  - 报表
  - 统计查询
  - work_mem
  - 复合索引
  - 临时文件

triggers:
  - "HashAggregate"
  - "GroupAggregate"
  - "Sort Method: external"
  - "duration:\\s*\\d{4,} ms"

evidence_required:
  - sql_text
  - explain_plan
evidence_optional:
  - table_stats
  - index_info

---

## 根因

GROUP BY 操作需要对满足条件的所有行先过滤、再哈希或排序聚合。当 WHERE 字段缺少索引时，数据库先全表扫描后再聚合，数据量巨大；`work_mem` 不足时还会触发磁盘临时文件，大幅增加 I/O；多表 JOIN 使数据量进一步扩大。增加索引、提高 work_mem、使用物化视图是三层递进的优化手段。

## 诊断步骤

### 步骤 1

**操作**：查看含 GROUP BY 的慢查询

- 工具：`execute_sql`
- 条件：报表生成慢或 CPU 持续高位

**现象**：`pg_stat_activity` 中出现含 `GROUP BY` 的统计查询持续活跃，duration 超过 10s。

**现象分析**：大表 GROUP BY 是典型的报表性能瓶颈，需结合执行计划确认瓶颈点。

```sql
SELECT pid, state, query_start, now() - query_start AS duration, query
FROM pg_stat_activity
WHERE state = 'active'
  AND query LIKE '%GROUP BY%'
ORDER BY duration DESC
LIMIT 10;
```

---

### 步骤 2

**操作**：获取报表 SQL 执行计划

- 工具：`execute_sql`
- 条件：已收集 sql_text

**现象**：执行计划出现 `HashAggregate` 或 `GroupAggregate`，处理行数与全表规模相当，可能有 `external merge` 排序。

**现象分析**：未能在聚合前有效过滤数据，是执行时间长的根本原因。

```sql
EXPLAIN ANALYZE
SELECT c.category_name, s.region,
       COUNT(*) AS sale_count,
       SUM(s.amount) AS total_amount,
       SUM(s.quantity) AS total_quantity,
       AVG(s.amount) AS avg_amount
FROM sales s
JOIN products p ON s.product_id = p.product_id
JOIN categories c ON p.category_id = c.category_id
WHERE s.sale_date BETWEEN '2024-01-01' AND '2024-01-31'
GROUP BY c.category_name, s.region
ORDER BY total_amount DESC;
```

---

### 步骤 3

**操作**：检查 sales 表过滤字段索引情况

- 工具：`execute_sql`
- 条件：已确认聚合操作处理数据量大

**现象**：sales 表缺少 `sale_date` 或复合索引（sale_date, region, product_id），导致全表扫描后聚合。

**现象分析**：过滤字段有索引可大幅减少参与聚合的行数，是降低聚合开销最直接的手段。

```sql
SELECT * FROM pg_stat_user_indexes WHERE relname = 'sales';
SELECT * FROM pg_indexes WHERE tablename = 'sales';
SHOW work_mem;
```

---

## 恢复手段

### 根因1：添加覆盖报表查询的复合索引（主要修复）

**描述**：为 WHERE 条件字段和 GROUP BY 字段创建复合索引，让数据库在聚合前先通过索引高效过滤数据。

**具体命令**：

```sql
-- 为过滤字段添加索引
CREATE INDEX CONCURRENTLY idx_sales_sale_date ON sales(sale_date);

-- 复合索引覆盖 WHERE + GROUP BY
CREATE INDEX CONCURRENTLY idx_sales_date_region_product
    ON sales(sale_date, region, product_id);

-- 确保 JOIN 字段有索引
CREATE INDEX CONCURRENTLY idx_sales_product_id     ON sales(product_id);
CREATE INDEX CONCURRENTLY idx_products_category_id ON products(category_id);
```

### 根因2：增加 work_mem 并优化 SQL 结构（辅助优化）

**描述**：提高 work_mem 使聚合在内存中完成，同时使用 CTE 先过滤再 JOIN，减少参与聚合的数据量。

**具体命令**：

```sql
SET work_mem = '128MB';

-- 先过滤再 JOIN（减少聚合数据量）
WITH filtered_sales AS (
    SELECT product_id, region, amount, quantity
    FROM sales
    WHERE sale_date BETWEEN '2024-01-01' AND '2024-01-31'
)
SELECT c.category_name, fs.region,
       COUNT(*)         AS sale_count,
       SUM(fs.amount)   AS total_amount,
       SUM(fs.quantity) AS total_quantity,
       AVG(fs.amount)   AS avg_amount
FROM filtered_sales fs
JOIN products p   ON fs.product_id = p.product_id
JOIN categories c ON p.category_id = c.category_id
GROUP BY c.category_name, fs.region
ORDER BY total_amount DESC;
```

### 根因3：使用物化视图预聚合（完整修复）

**描述**：对高频报表创建物化视图，将聚合结果持久化，查询时直接读取预计算结果，毫秒级响应。

**具体命令**：

```sql
CREATE MATERIALIZED VIEW sales_stats_by_category_region AS
SELECT c.category_name, s.region,
       s.sale_date,
       COUNT(*)        AS sale_count,
       SUM(s.amount)   AS total_amount,
       SUM(s.quantity) AS total_quantity,
       AVG(s.amount)   AS avg_amount
FROM sales s
JOIN products p   ON s.product_id = p.product_id
JOIN categories c ON p.category_id = c.category_id
GROUP BY c.category_name, s.region, s.sale_date;

CREATE INDEX ON sales_stats_by_category_region(sale_date);

-- 定期刷新（每日/每小时）
REFRESH MATERIALIZED VIEW CONCURRENTLY sales_stats_by_category_region;
```
