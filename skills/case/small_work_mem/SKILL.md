---
id: small_work_mem
name: "慢SQL - work_mem 过小导致频繁临时文件排序"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "work_mem 默认值 4MB 过小，复杂报表的 Hash Join、HashAggregate、Sort 节点频繁落盘为临时文件，查询性能急剧下降"

symptoms:
  - "复杂报表查询时间超过5分钟"
  - "执行计划中 Sort/HashAggregate 节点显示 external merge Disk"
  - "pg_stat_database.temp_bytes 持续增长"
  - "磁盘 I/O 持续高位，查询 I/O 等待长"

keywords:
  - work_mem
  - 临时文件
  - temp file
  - external merge
  - HashAggregate
  - hash join
  - 内存排序
  - 磁盘排序

triggers:
  - "Sort Method: external merge Disk"
  - "Batches:.*[2-9][0-9]*"
  - "temporary file size exceeds"
  - "duration:\\s*\\d{5,} ms"

evidence_required:
  - sql_text
  - explain_plan
evidence_optional:
  - system_view

---

## 根因

PostgreSQL 的 `work_mem` 是每个查询节点（Sort、Hash、HashAggregate 等）独立分配的内存上限。默认值 4MB 在处理千万级行的 Hash Join 或 GROUP BY 聚合时严重不足，数据库被迫将排序/哈希数据落盘为临时文件。磁盘 I/O 比内存操作慢 100-1000 倍，是查询性能骤降的直接原因。

## 诊断步骤

### 步骤 1

**操作**：检查当前 work_mem 配置和临时文件使用统计

- 工具：`execute_sql`
- 条件：查询执行时间异常长，磁盘 I/O 高

**现象**：`work_mem` 仅 4MB，`pg_stat_database.temp_bytes` 显示大量临时文件数据。

**现象分析**：4MB 对于有大量数据参与排序/聚合的查询来说严重不足，需立即调整。

```sql
SHOW work_mem;

SELECT datname, temp_files, pg_size_pretty(temp_bytes) AS temp_size
FROM pg_stat_database
ORDER BY temp_bytes DESC;
```

---

### 步骤 2

**操作**：获取报表 SQL 执行计划，确认落盘节点

- 工具：`execute_sql`
- 条件：已收集 sql_text

**现象**：执行计划中 Sort 节点显示 `Sort Method: external merge Disk`，HashAggregate 显示 `Batches: N`（N > 1），说明在多个节点落盘。

**现象分析**：多节点同时落盘累积的临时文件量可以达到数 GB，磁盘随机 I/O 是主要性能瓶颈。

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT p.category_id, p.brand_id,
       COUNT(DISTINCT o.order_id)           AS order_count,
       SUM(oi.quantity)                      AS total_quantity,
       SUM(oi.quantity * oi.unit_price)      AS total_amount
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
JOIN products p     ON oi.product_id = p.product_id
WHERE o.order_date BETWEEN '2023-01-01' AND '2023-01-31'
GROUP BY p.category_id, p.brand_id
ORDER BY total_amount DESC;
```

---

### 步骤 3

**操作**：评估安全的 work_mem 上调范围

- 工具：`execute_sql`
- 条件：准备调整 work_mem

**现象**：当前 max_connections=200，总内存=32GB，按最坏情况每连接每节点消耗 work_mem，需防止 OOM。

**现象分析**：work_mem 应在 `总内存 × 安全比例 / (max_connections × 平均节点数)` 范围内，或仅在报表专用会话级调整。

```sql
SHOW max_connections;
SELECT pg_size_pretty(pg_database_size(current_database()));
-- 系统总内存需从 OS 获取：SELECT * FROM pg_settings WHERE name = 'work_mem';
```

---

## 恢复手段

### 根因1：会话级提升 work_mem（安全的即时修复）

**描述**：为报表专用会话设置更高的 work_mem，不影响其他连接，避免 OOM 风险。

**具体命令**：

```sql
-- 仅对当前报表会话生效
SET work_mem = '256MB';

-- 执行报表查询（此时 Sort/HashAgg 在内存中完成）
SELECT p.category_id, p.brand_id,
       COUNT(DISTINCT o.order_id)       AS order_count,
       SUM(oi.quantity)                  AS total_quantity,
       SUM(oi.quantity * oi.unit_price)  AS total_amount
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
JOIN products p     ON oi.product_id = p.product_id
WHERE o.order_date BETWEEN '2023-01-01' AND '2023-01-31'
GROUP BY p.category_id, p.brand_id
ORDER BY total_amount DESC;
```

### 根因2：全局调整 work_mem（主要修复）

**描述**：根据服务器总内存和并发连接数，将全局 work_mem 调整到合理范围（通常 16-64MB），同时为报表角色/用户单独设置更高值。

**具体命令**：

```sql
-- 全局调整（需综合评估内存）
ALTER SYSTEM SET work_mem = '32MB';
SELECT pg_reload_conf();

-- 为报表专用角色单独设置更高 work_mem
ALTER ROLE report_user SET work_mem = '256MB';

-- 验证：用 EXPLAIN ANALYZE 确认排序不再落盘
EXPLAIN (ANALYZE, BUFFERS)
SELECT p.category_id, COUNT(*) AS cnt
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
JOIN products p ON oi.product_id = p.product_id
WHERE o.order_date BETWEEN '2023-01-01' AND '2023-01-31'
GROUP BY p.category_id;
```

### 根因3：物化视图预计算月度报表（完整修复）

**描述**：对固定周期的月度统计报表，创建物化视图预先完成聚合，消除实时查询时的大内存需求。

**具体命令**：

```sql
CREATE MATERIALIZED VIEW monthly_sales_report AS
SELECT date_trunc('month', o.order_date) AS month,
       p.category_id, p.brand_id,
       COUNT(DISTINCT o.order_id)          AS order_count,
       SUM(oi.quantity)                     AS total_quantity,
       SUM(oi.quantity * oi.unit_price)     AS total_amount
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
JOIN products p     ON oi.product_id = p.product_id
GROUP BY date_trunc('month', o.order_date), p.category_id, p.brand_id;

CREATE INDEX ON monthly_sales_report(month, total_amount DESC);

REFRESH MATERIALIZED VIEW CONCURRENTLY monthly_sales_report;
```
