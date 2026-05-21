---
id: select_star_wide_table
name: "慢SQL - SELECT * 宽表查询传输开销大"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "宽表（含大字段）使用 SELECT * 查询，导致不必要的数据传输和内存开销，高并发下吞吐量下降"

symptoms:
  - "查询响应时间超过1秒，高并发下显著恶化"
  - "网络传输量大，内存使用增加"
  - "客户查询高峰期系统吞吐量下降"
  - "表字段增加后性能持续劣化"

keywords:
  - select star
  - SELECT *
  - 宽表
  - 大字段
  - 索引覆盖
  - index only scan
  - 数据传输
  - 冗余字段

triggers:
  - "Index Only Scan.*FALSE"
  - "Heap Fetches.*[1-9][0-9]{3,}"
  - "duration:\\s*\\d{4,} ms"

evidence_required:
  - sql_text
  - explain_plan
evidence_optional:
  - table_stats

---

## 根因

customers 等宽表字段众多（包含 TEXT/BLOB 大字段），使用 `SELECT *` 时，即使通过主键索引快速定位记录，仍需读取所有列数据并通过网络传输。这不仅无法触发 Index Only Scan（仅索引扫描），还占用额外内存，在高并发场景下对系统吞吐量影响显著，且随着表字段增加问题持续恶化。

## 诊断步骤

### 步骤 1

**操作**：查看当前慢查询，定位 SELECT * 相关语句

- 工具：`execute_sql`
- 条件：系统响应慢或监控告警

**现象**：`pg_stat_activity` 中出现包含 `SELECT *` 的查询持续活跃，duration 超过 1s。

**现象分析**：SELECT * 在宽表上执行时传输的数据量远超实际需要，是高并发下的性能瓶颈。

```sql
SELECT pid, state, query_start, now() - query_start AS duration, query
FROM pg_stat_activity
WHERE state = 'active'
  AND query LIKE '%SELECT *%'
ORDER BY duration DESC
LIMIT 10;
```

---

### 步骤 2

**操作**：获取慢 SQL 执行计划，观察是否为 Heap Fetches 极高

- 工具：`execute_sql`
- 条件：已收集 sql_text

```sql
-- 将 <your_sql_here> 替换为实际的慢查询语句
EXPLAIN (ANALYZE, VERBOSE, BUFFERS, FORMAT TEXT)
<your_sql_here>;
```

**现象**：执行计划显示 Index Scan 或 Seq Scan，Heap Fetches 数量高，无 Index Only Scan。

**现象分析**：SELECT * 导致无法走 Index Only Scan，必须回表读取所有列，产生大量 Heap Fetches。

```sql
EXPLAIN ANALYZE SELECT * FROM customers WHERE customer_id = 67890;
```

---

### 步骤 3

**操作**：检查表结构，确认宽表字段及大字段存在

- 工具：`execute_sql`
- 条件：已确认查询低效

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

**现象**：customers 表含 TEXT 类型字段（如 remark），平均行宽较大，总数据量可观。

**现象分析**：大字段是造成数据传输开销的直接原因，明确字段后可针对性优化查询列表。

```sql
SELECT column_name, data_type, character_maximum_length
FROM information_schema.columns
WHERE table_name = 'customers'
ORDER BY ordinal_position;

SELECT pg_size_pretty(AVG(pg_column_size(c)))
FROM customers c;
```

---

### 步骤 4

**操作**：验证指定列查询的执行计划是否触发 Index Only Scan

- 工具：`execute_sql`
- 条件：已明确业务需要的列

```sql
-- 将 <your_sql_here> 替换为实际的慢查询语句
EXPLAIN (ANALYZE, VERBOSE, BUFFERS, FORMAT TEXT)
<your_sql_here>;
```

**现象**：精简列查询显示 `Index Only Scan`，Heap Fetches 为 0，执行时间显著降低。

**现象分析**：缩减列列表后，覆盖索引可被充分利用，避免回表，验证优化有效。

```sql
EXPLAIN ANALYZE
SELECT customer_id, customer_name, phone_number, email, create_time
FROM customers
WHERE customer_id = 67890;
```

---

## 恢复手段

### 根因1：SELECT * 导致冗余数据传输（主要修复）

**描述**：将 `SELECT *` 替换为业务实际需要的字段列表，减少数据传输量，并可能触发 Index Only Scan。

**具体命令**：

```sql
-- 原始问题 SQL
-- SELECT * FROM customers WHERE customer_id = 67890;

-- 修复：只选取业务需要的列
SELECT customer_id, customer_name, phone_number, email, create_time
FROM customers
WHERE customer_id = 67890;
```

### 根因2：缺少覆盖索引（辅助优化）

**描述**：对高频查询的列组合添加覆盖索引，使查询能走 Index Only Scan，彻底避免回表。

**具体命令**：

```sql
-- 添加覆盖索引（包含高频查询所需列）
CREATE INDEX CONCURRENTLY idx_customers_id_cover
    ON customers(customer_id, customer_name, phone_number, email, create_time);

-- 验证是否触发 Index Only Scan
EXPLAIN ANALYZE
SELECT customer_id, customer_name, phone_number, email, create_time
FROM customers
WHERE customer_id = 67890;
```
