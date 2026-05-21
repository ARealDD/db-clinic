---
id: stale_statistics_wrong_plan
name: "慢SQL - 统计信息过期导致优化器选择错误执行计划"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "表统计信息长期未更新，优化器基于过时数据分布选择了低效的 Nested Loop 或全表扫描，查询性能骤降"

symptoms:
  - "查询执行时间超过30秒，历史上曾经很快"
  - "执行计划显示 Seq Scan 或 Nested Loop，但预估行数与实际行数差距悬殊"
  - "last_analyze 显示统计信息超过30天未更新"
  - "系统 CPU 升高，连接数堆积"

keywords:
  - 统计信息
  - statistics
  - ANALYZE
  - autovacuum
  - 执行计划错误
  - Nested Loop
  - rows estimate
  - 行数估算偏差

triggers:
  - "Rows Removed by Filter.*[1-9][0-9]{4,}"
  - "actual rows=.*estimated rows="
  - "Nested Loop.*cost=.*actual time="

evidence_required:
  - sql_text
  - explain_plan
  - table_stats
evidence_optional:
  - index_info

---

## 根因

PostgreSQL 优化器依赖表的统计信息（行数、列值分布、MCV 等）来估算各执行计划节点的代价。当 `last_analyze` 时间过久（如超过 30 天），且表数据量发生了显著变化，统计信息与实际数据分布严重偏离，优化器的行数估算出现量级级别的偏差，进而选错了连接方式（如 Nested Loop 替代 Hash Join）或扫描方式（如 Seq Scan 替代 Index Scan），导致查询时间从秒级劣化为分钟级。

## 诊断步骤

### 步骤 1

**操作**：查看关键表的统计信息新鲜度

- 工具：`execute_sql`
- 条件：查询性能突然下降

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

**现象**：`pg_stat_user_tables` 中 `last_analyze` / `last_autoanalyze` 显示超过 30 天，`n_live_tup` 与实际行数差异大。

**现象分析**：统计信息过期是执行计划选择错误的最常见根因，尤其是数据量增长后未及时 ANALYZE。

```sql
SELECT schemaname, tablename, last_analyze, last_autoanalyze,
       n_live_tup, n_dead_tup
FROM pg_stat_user_tables
WHERE tablename IN ('orders', 'order_items')
ORDER BY tablename;
```

---

### 步骤 2

**操作**：获取慢查询执行计划，确认估算行数偏差

- 工具：`execute_sql`
- 条件：已确认统计信息过期

```sql
-- 将 <your_sql_here> 替换为实际的慢查询语句
EXPLAIN (ANALYZE, VERBOSE, BUFFERS, FORMAT TEXT)
<your_sql_here>;
```

**现象**：执行计划中节点的 `rows=` 估算与 `actual rows=` 实际值相差数十倍乃至数百倍，优化器因此选了 Nested Loop 和 Seq Scan。

**现象分析**：行数估算偏差是统计信息不准确的直接证据，`ANALYZE` 后重新查看执行计划往往可以立即修复。

```sql
EXPLAIN ANALYZE
SELECT o.customer_id,
       COUNT(DISTINCT o.order_id)             AS order_count,
       SUM(oi.quantity * oi.unit_price)        AS total_amount
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
WHERE o.order_date BETWEEN '2023-01-01' AND '2023-03-31'
GROUP BY o.customer_id
ORDER BY total_amount DESC
LIMIT 100;
```

---

### 步骤 3

**操作**：检查 autovacuum 相关配置，确认自动统计信息更新是否过于保守

- 工具：`execute_sql`
- 条件：已确认 ANALYZE 频率不足

**现象**：`autovacuum_analyze_scale_factor` 为 0.2（默认），对于千万级表意味着需要 200 万行变更才触发分析，频率过低。

**现象分析**：大表需要设置更低的分析阈值，使 autovacuum 更频繁地更新统计信息。

```sql
SHOW autovacuum_analyze_scale_factor;
SHOW autovacuum_analyze_threshold;
SELECT name, setting FROM pg_settings WHERE name LIKE '%analyze%';
```

---

## 恢复手段

### 根因1：立即手动执行 ANALYZE（紧急修复）

**描述**：对统计信息过期的表立即执行 ANALYZE，优化器重新获得准确的数据分布信息后，通常会自动选择正确的执行计划。

**具体命令**：

```sql
-- 立即更新统计信息
ANALYZE VERBOSE orders;
ANALYZE VERBOSE order_items;

-- 或同时执行 VACUUM 清理死元组
VACUUM ANALYZE orders;
VACUUM ANALYZE order_items;

-- 验证执行计划已改善
EXPLAIN ANALYZE
SELECT o.customer_id, COUNT(DISTINCT o.order_id) AS order_count
FROM orders o
JOIN order_items oi ON o.order_id = oi.order_id
WHERE o.order_date BETWEEN '2023-01-01' AND '2023-03-31'
GROUP BY o.customer_id
ORDER BY order_count DESC
LIMIT 100;
```

### 根因2：调整 autovacuum 使统计信息保持新鲜（主要修复）

**描述**：降低大表的 autovacuum 分析阈值，使统计信息随数据增长持续保持准确，防止再次发生统计过期问题。

**具体命令**：

```sql
-- 为大表单独设置更激进的分析策略
ALTER TABLE orders      SET (autovacuum_analyze_scale_factor = 0.05,
                             autovacuum_analyze_threshold = 100);
ALTER TABLE order_items SET (autovacuum_analyze_scale_factor = 0.05,
                             autovacuum_analyze_threshold = 100);

-- 全局调整（影响所有表）
ALTER SYSTEM SET autovacuum_analyze_scale_factor = 0.05;
ALTER SYSTEM SET autovacuum_analyze_threshold = 50;
SELECT pg_reload_conf();
```

### 根因3：增大统计信息采样精度（辅助优化）

**描述**：对选择性较高或数据分布不均匀的列，增大 `statistics` 采样目标，使优化器获得更准确的列值分布估算。

**具体命令**：

```sql
-- 增大关键列的统计信息精度（默认100，最大10000）
ALTER TABLE orders ALTER COLUMN order_date     SET STATISTICS 500;
ALTER TABLE orders ALTER COLUMN customer_id    SET STATISTICS 500;
ALTER TABLE order_items ALTER COLUMN order_id  SET STATISTICS 500;

-- 重新分析以应用新采样目标
ANALYZE orders;
ANALYZE order_items;
```
