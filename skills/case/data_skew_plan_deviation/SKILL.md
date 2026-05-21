---
id: data_skew_plan_deviation
name: "慢SQL - 数据倾斜导致统计信息失真和执行计划偏差"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "某些字段值数据分布极度不均（如某分类占70%数据），默认统计信息无法准确反映分布，优化器行数估算偏差导致低效执行计划"

symptoms:
  - "某分类/某值的查询特别慢，其他值查询正常"
  - "EXPLAIN 预估行数与实际行数相差数十倍"
  - "执行计划在数据量大的类别下选择了错误的 Join 方式"
  - "增加统计信息采样率后执行计划改善"

keywords:
  - 数据倾斜
  - data skew
  - 统计信息失真
  - 行数估算偏差
  - MCV
  - most common values
  - statistics target
  - 执行计划偏差

triggers:
  - "rows=.*actual rows=.*loops="
  - "HashAggregate.*Batches"
  - "duration:\\s*\\d{5,} ms"

evidence_required:
  - sql_text
  - explain_plan
  - table_stats
evidence_optional:
  - index_info

---

## 根因

当 category_id 等字段存在严重数据倾斜（某个值占 70% 数据），PostgreSQL 默认统计信息采样精度（statistics target=100）可能无法准确捕获最高频值的真实频率，导致优化器的行数估算严重偏低。基于错误的行数估算，优化器可能选择了 Nested Loop（适合小表）而非 Hash Join（适合大表），或分配了不足的 work_mem，在大数据量场景下性能骤降。

## 诊断步骤

### 步骤 1

**操作**：检查数据分布，确认倾斜字段

- 工具：`execute_sql`
- 条件：特定查询条件下性能异常差

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

**现象**：查询数据分布后发现某个 category_id 值占总销售记录的 50-70%，其他值各占极小比例。

**现象分析**：严重的数据倾斜是统计信息失真的根本原因，需要提高该字段的统计采样精度。

```sql
-- 检查数据分布
SELECT p.category_id, COUNT(*) AS sale_count,
       ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) AS pct
FROM products p
JOIN sales_records sr ON p.product_id = sr.product_id
GROUP BY p.category_id
ORDER BY sale_count DESC
LIMIT 10;

-- 检查 product_id 级别的倾斜
SELECT product_id, COUNT(*) AS sale_count
FROM sales_records
GROUP BY product_id
ORDER BY sale_count DESC
LIMIT 10;
```

---

### 步骤 2

**操作**：获取倾斜场景下的执行计划，确认行数估算偏差

- 工具：`execute_sql`
- 条件：已识别数据倾斜字段

```sql
-- 将 <your_sql_here> 替换为实际的慢查询语句
EXPLAIN (ANALYZE, VERBOSE, BUFFERS, FORMAT TEXT)
<your_sql_here>;
```

**现象**：执行计划中节点的 `rows=500`（估算）vs `actual rows=350000000`（实际），估算严重偏低，导致 Nested Loop 被选用。

**现象分析**：estimated rows 与 actual rows 差距超过 100 倍是统计信息失真的直接证据，需立即提高统计采样精度并重新 ANALYZE。

```sql
EXPLAIN ANALYZE
SELECT p.category_id, COUNT(*) AS sale_count,
       SUM(sr.amount) AS total_amount
FROM products p
JOIN sales_records sr ON p.product_id = sr.product_id
GROUP BY p.category_id
ORDER BY total_amount DESC;
```

---

### 步骤 3

**操作**：检查关键字段的统计信息采样目标

- 工具：`execute_sql`
- 条件：确认统计信息不准确

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

**现象**：`pg_stats` 显示 product_id 和 category_id 的 statistics target 为 100（默认），而数据分布极度不均需要更高的采样精度。

**现象分析**：statistics target 控制统计信息的采样粒度，默认 100 对倾斜字段不足，需提升到 500-1000 以更准确捕获 MCV（最高频值）分布。

```sql
SELECT tablename, attname, n_distinct, most_common_vals, most_common_freqs
FROM pg_stats
WHERE tablename = 'sales_records'
  AND attname IN ('product_id');

SELECT attname, attstattarget
FROM pg_attribute
WHERE attrelid = 'sales_records'::regclass
  AND attname = 'product_id';
```

---

## 恢复手段

### 根因1：提高倾斜字段的统计信息采样精度（主要修复）

**描述**：对数据倾斜严重的字段增大 statistics target，重新 ANALYZE，使优化器获得更准确的频率分布，从而选择正确的执行计划。

**具体命令**：

```sql
-- 提高关键倾斜字段的统计精度（默认100，最大10000）
ALTER TABLE sales_records ALTER COLUMN product_id  SET STATISTICS 1000;
ALTER TABLE products      ALTER COLUMN category_id SET STATISTICS 1000;

-- 重新分析以应用新的采样目标
ANALYZE VERBOSE sales_records;
ANALYZE VERBOSE products;

-- 验证：再次执行计划，确认 estimated rows 与 actual rows 接近
EXPLAIN ANALYZE
SELECT p.category_id, COUNT(*) AS sale_count
FROM products p
JOIN sales_records sr ON p.product_id = sr.product_id
GROUP BY p.category_id
ORDER BY sale_count DESC;
```

### 根因2：为 JOIN 字段创建复合索引（辅助优化）

**描述**：在 sales_records 上创建包含 product_id 和常用查询字段的复合索引，在统计信息准确的前提下进一步提升 JOIN 效率。

**具体命令**：

```sql
-- 支持 JOIN 和日期范围过滤的复合索引
CREATE INDEX CONCURRENTLY idx_sales_product_date_amount
    ON sales_records(product_id, sale_date, amount);

-- 支持分类 JOIN 的索引
CREATE INDEX CONCURRENTLY idx_products_category_product
    ON products(category_id, product_id);
```

### 根因3：物化视图预计算分类统计（完整修复）

**描述**：对于按分类统计的高频报表，创建物化视图预计算各分类的聚合结果，完全避免实时 JOIN 5亿条记录的开销。

**具体命令**：

```sql
CREATE MATERIALIZED VIEW category_sales_summary AS
SELECT p.category_id,
       COUNT(*)         AS sale_count,
       SUM(sr.amount)   AS total_amount,
       AVG(sr.amount)   AS avg_amount,
       MIN(sr.sale_date) AS first_sale,
       MAX(sr.sale_date) AS last_sale
FROM products p
JOIN sales_records sr ON p.product_id = sr.product_id
GROUP BY p.category_id;

CREATE INDEX ON category_sales_summary(category_id);
CREATE INDEX ON category_sales_summary(total_amount DESC);

-- 定期刷新
REFRESH MATERIALIZED VIEW CONCURRENTLY category_sales_summary;
```
