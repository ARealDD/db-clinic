---
id: nested_views_perf
name: "慢SQL - 视图嵌套过深导致重复计算和条件无法下推"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "4层嵌套视图中，WHERE 条件无法下推至底层，每层都对全量数据执行聚合计算，产生巨大中间结果集；优化器无法看到完整查询，无法选择最优执行计划"

symptoms:
  - "嵌套视图查询执行时间超过10分钟"
  - "数据库 CPU 和 I/O 同时持续高位"
  - "出现临时表空间不足或内存溢出错误"
  - "单独查询底层视图比完整查询快很多"

keywords:
  - 视图嵌套
  - 嵌套视图
  - view nesting
  - 条件下推
  - predicate pushdown
  - 重复计算
  - 中间结果
  - 物化视图
  - CTE

triggers:
  - "HashAggregate.*Batches"
  - "Materialize"
  - "duration:\\s*\\d{5,} ms"

evidence_required:
  - sql_text
  - explain_plan
evidence_optional:
  - table_stats

---

## 根因

PostgreSQL 对普通视图会尝试将外层 WHERE 条件下推（predicate pushdown），但多层聚合视图嵌套后，优化器无法跨聚合边界下推过滤条件。外层查询的 `WHERE sale_month BETWEEN ...` 无法传递给底层 v_product_sales，底层必须聚合全量历史数据，再在外层过滤。4层嵌套相当于对同一亿条销售记录执行4次聚合，产生大量中间结果。解决方案是改写为单一 SQL 或使用物化视图预计算。

## 诊断步骤

### 步骤 1

**操作**：逐层分析各视图的执行时间，定位膨胀层

- 工具：`execute_sql`
- 条件：嵌套视图查询超时

**现象**：逐层测试发现第一层视图 v_product_sales 已需要扫描全量 sales 记录（1亿行），即使只查询3个月数据；后续层在此基础上继续全量聚合，时间叠加。

**现象分析**：WHERE 条件无法下推到含 GROUP BY 的视图层是根本问题，需改写查询结构，把过滤条件放到最内层。

```sql
-- 逐层测试执行时间，定位哪一层开始变慢
EXPLAIN ANALYZE
SELECT COUNT(*) FROM v_product_sales
WHERE sale_month BETWEEN '2023-01-01' AND '2023-03-31';

EXPLAIN ANALYZE
SELECT COUNT(*) FROM v_category_sales
WHERE sale_month BETWEEN '2023-01-01' AND '2023-03-31';

-- 查看视图定义
SELECT viewname, definition FROM pg_views WHERE viewname LIKE 'v_%';
```

---

### 步骤 2

**操作**：获取完整嵌套视图查询的执行计划

- 工具：`execute_sql`
- 条件：已识别性能瓶颈在视图嵌套

```sql
-- 将 <your_sql_here> 替换为实际的慢查询语句
EXPLAIN (ANALYZE, VERBOSE, BUFFERS, FORMAT TEXT)
<your_sql_here>;
```

**现象**：执行计划显示底层 Seq Scan on sales 扫描全部1亿行，多处 HashAggregate 节点处理大量行，Batches > 1 说明聚合溢出到磁盘；外层 Filter 才过滤月份。

**现象分析**：Batches > 1 是内存不足、中间结果溢出磁盘的信号；根本解决是改写查询，将 sale_date 过滤下推到 sales 表扫描阶段。

```sql
EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
SELECT *
FROM v_sales_report
WHERE sale_month BETWEEN '2023-01-01' AND '2023-03-31'
ORDER BY region_id, category_id, sale_month;
```

---

### 步骤 3

**操作**：验证改写为单层查询或物化视图后的性能

- 工具：`execute_sql`
- 条件：已准备改写方案

```sql
-- 将 <your_sql_here> 替换为实际的慢查询语句
EXPLAIN (ANALYZE, VERBOSE, BUFFERS, FORMAT TEXT)
<your_sql_here>;
```

**现象**：改写为直接 JOIN sales + products + customers、WHERE 子句限制 sale_date 范围后，执行计划中 sales 扫描行数从1亿降至目标月份的实际行数，总执行时间从分钟级降至秒级。

**现象分析**：日期过滤在 sales 表上有 idx_sale_date 索引支撑，改写后可走 Index Scan 或高效 Bitmap Index Scan，大幅减少处理数据量。

```sql
-- 验证改写后的执行计划
EXPLAIN ANALYZE
SELECT c.region_id, p.category_id,
       DATE_TRUNC('month', s.sale_date) AS sale_month,
       SUM(s.amount) AS region_category_amount
FROM sales s
JOIN products p ON s.product_id = p.product_id
JOIN customers c ON s.customer_id = c.customer_id
WHERE s.sale_date BETWEEN '2023-01-01' AND '2023-03-31'
GROUP BY c.region_id, p.category_id, DATE_TRUNC('month', s.sale_date)
ORDER BY c.region_id, p.category_id, DATE_TRUNC('month', s.sale_date);
```

---

## 恢复手段

### 根因1：将嵌套视图改写为单一查询（主要修复）

**描述**：消除视图嵌套，将过滤条件直接写在最内层的 sales 表上，确保优化器能使用 sale_date 索引，避免处理全量历史数据。

**具体命令**：

```sql
-- 改写为单一查询，日期过滤直接作用于 sales 表
SELECT
    c.region_id,
    p.category_id,
    DATE_TRUNC('month', s.sale_date) AS sale_month,
    SUM(s.amount) AS region_category_amount,
    LAG(SUM(s.amount)) OVER (
        PARTITION BY c.region_id, p.category_id
        ORDER BY DATE_TRUNC('month', s.sale_date)
    ) AS previous_month_amount,
    ROUND(
        (SUM(s.amount) - LAG(SUM(s.amount)) OVER (
            PARTITION BY c.region_id, p.category_id
            ORDER BY DATE_TRUNC('month', s.sale_date)
        )) * 100.0 /
        NULLIF(LAG(SUM(s.amount)) OVER (
            PARTITION BY c.region_id, p.category_id
            ORDER BY DATE_TRUNC('month', s.sale_date)
        ), 0),
        2
    ) AS growth_rate
FROM sales s
JOIN products  p ON s.product_id  = p.product_id
JOIN customers c ON s.customer_id = c.customer_id
WHERE s.sale_date BETWEEN '2023-01-01' AND '2023-03-31'
GROUP BY c.region_id, p.category_id, DATE_TRUNC('month', s.sale_date)
ORDER BY c.region_id, p.category_id, DATE_TRUNC('month', s.sale_date);
```

### 根因2：将视图层级压缩到最多2层（辅助优化）

**描述**：保留视图抽象，但将4层压缩为2层：第一层 v_sales_base 合并3表 JOIN（不聚合），第二层直接完成聚合和窗口函数，减少中间结果层级。

**具体命令**：

```sql
-- 第一层：仅做 JOIN，不聚合（优化器可推入过滤条件）
CREATE OR REPLACE VIEW v_sales_base AS
SELECT s.sale_date,
       DATE_TRUNC('month', s.sale_date) AS sale_month,
       p.category_id,
       c.region_id,
       s.amount
FROM sales s
JOIN products  p ON s.product_id  = p.product_id
JOIN customers c ON s.customer_id = c.customer_id;

-- 第二层：聚合 + 窗口函数（WHERE 可下推到第一层）
CREATE OR REPLACE VIEW v_sales_report_v2 AS
SELECT region_id, category_id, sale_month,
       SUM(amount) AS region_category_amount,
       LAG(SUM(amount)) OVER (
           PARTITION BY region_id, category_id ORDER BY sale_month
       ) AS previous_month_amount
FROM v_sales_base
GROUP BY region_id, category_id, sale_month;

-- 使用时带上过滤条件
SELECT * FROM v_sales_report_v2
WHERE sale_month BETWEEN '2023-01-01' AND '2023-03-31';
```

### 根因3：物化视图预计算月度汇总（完整修复）

**描述**：对变更不频繁的报表数据，创建物化视图预先完成 JOIN + 聚合，查询时直接按索引过滤，响应时间从分钟级降至毫秒级。

**具体命令**：

```sql
-- 创建月度汇总物化视图
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_monthly_sales_report AS
SELECT c.region_id, p.category_id,
       DATE_TRUNC('month', s.sale_date)::DATE AS sale_month,
       SUM(s.amount) AS region_category_amount
FROM sales s
JOIN products  p ON s.product_id  = p.product_id
JOIN customers c ON s.customer_id = c.customer_id
GROUP BY c.region_id, p.category_id, DATE_TRUNC('month', s.sale_date);

CREATE INDEX ON mv_monthly_sales_report(sale_month);
CREATE INDEX ON mv_monthly_sales_report(region_id, category_id, sale_month);

-- 每天/每月刷新
REFRESH MATERIALIZED VIEW CONCURRENTLY mv_monthly_sales_report;

-- 查询时直接走索引（毫秒级响应）
SELECT region_id, category_id, sale_month, region_category_amount,
       LAG(region_category_amount) OVER (
           PARTITION BY region_id, category_id ORDER BY sale_month
       ) AS previous_month_amount
FROM mv_monthly_sales_report
WHERE sale_month BETWEEN '2023-01-01' AND '2023-03-31'
ORDER BY region_id, category_id, sale_month;
```
