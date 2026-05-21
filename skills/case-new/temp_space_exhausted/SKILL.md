---
id: temp_space_exhausted
name: "资源耗尽 - 临时文件空间不足导致复杂查询失败"
category: resource_exhaustion
version: "1.0"
author: "运维团队"
description: "复杂报表查询中的 ORDER BY/GROUP BY 在 work_mem 不足时落盘为临时文件，临时表空间耗尽导致查询报错"

symptoms:
  - "复杂报表查询执行时间超过20秒甚至直接失败"
  - "报错 'could not write to file' 或 'out of temporary space'"
  - "临时表空间（pg_temp）使用率接近100%"
  - "并发执行多个报表时系统资源耗尽"

keywords:
  - 临时表空间
  - temp tablespace
  - temp file
  - work_mem
  - external merge
  - 临时文件
  - 磁盘排序
  - 报表失败

triggers:
  - "Sort Method: external merge Disk"
  - "could not write to file"
  - "temporary file"
  - "Batches:.*[1-9][0-9]+"

evidence_required:
  - sql_text
  - explain_plan
evidence_optional:
  - system_view

---

## 根因

报表查询中的多个 JOIN、GROUP BY、ORDER BY 操作各自需要内存来完成排序/哈希聚合。当 `work_mem` 配置过小（如默认 4MB），每个节点的数据超出内存限制后都会落盘为临时文件。多个并发报表同时落盘，临时表空间容量耗尽，后续查询失败或报错。核心解决是增大 work_mem 减少落盘，同时为查询添加合适索引。

## 诊断步骤

### 步骤 1

**操作**：检查当前临时表空间使用情况

- 工具：`execute_sql`
- 条件：报表报错或响应极慢
- 执行语句：

```sql
-- 检查临时表空间使用
SELECT spcname, pg_size_pretty(pg_tablespace_size(oid)) AS size
FROM pg_tablespace
WHERE spcname LIKE 'pg_temp%';

-- 检查当前活跃的大查询
SELECT pid, state, query_start, now() - query_start AS duration, query
FROM pg_stat_activity
WHERE state = 'active'
ORDER BY duration DESC
LIMIT 10;
```

**现象**：pg_temp 表空间使用量接近满，多个活跃查询长时间处于排序或哈希聚合阶段。

**现象分析**：临时空间接近满是紧急信号，需立即评估是终止部分查询还是扩容临时空间。

---
### 步骤 2

**操作**：获取报表 SQL 执行计划，确认临时文件使用情况

- 工具：`execute_sql`
- 条件：已收集 sql_text
- 执行语句：

```sql
EXPLAIN ANALYZE
<slow_sql>
```

**现象**：执行计划多处出现 `Sort Method: external merge Disk`，Batches 数量大（表示 Hash 分批处理），每个节点都在落盘。

**现象分析**：多节点同时落盘说明 work_mem 严重不足，每次查询产生数 GB 临时文件。

---
### 步骤 3

**操作**：检查 work_mem 和临时表空间限制配置

- 工具：`execute_sql`
- 条件：已确认大量临时文件产生
- 执行语句：

```sql
SHOW work_mem;
SHOW temp_file_limit;
SHOW temp_tablespaces;
SHOW maintenance_work_mem;
```

**现象**：`work_mem` 仅为 4MB（默认），`temp_file_limit` 可能设置较低，多并发查询很快耗尽临时空间。

**现象分析**：work_mem 是每个排序/哈希节点独立使用的，一个复杂查询可能有 5-10 个这样的节点，乘以并发数会放大对临时空间的消耗。

---
## 恢复手段

### 根因1：增大 work_mem 减少落盘（主要修复）

**描述**：适当增大 work_mem，使排序/哈希在内存中完成，从根本上减少临时文件产生。注意 work_mem 是每节点每连接独立的，需防止内存溢出（总内存 = work_mem × 节点数 × 并发数）。

**具体命令**：

```sql
-- 会话级临时增大（不影响其他连接，推荐报表专用连接使用）
SET work_mem = '64MB';

-- 全局调整（需根据 max_connections 和总内存谨慎计算）
ALTER SYSTEM SET work_mem = '32MB';
SELECT pg_reload_conf();

-- 为报表添加合适索引，减少参与排序的数据量
CREATE INDEX CONCURRENTLY <index_name> ON <target_table>(<filter_col>);
CREATE INDEX CONCURRENTLY <index_name> ON <target_table>(<filter_col>, <groupby_col>, <join_col>);
```

### 根因2：扩展临时表空间容量（紧急恢复）

**描述**：在磁盘允许的情况下，配置更大的临时表空间目录，或增加 `temp_file_limit` 允许单查询使用更多临时空间。

**具体命令**：

```sql
-- 创建新的临时表空间（需要在 OS 上先创建目录并设置权限）
-- mkdir -p /data/pg_temp && chown postgres:postgres /data/pg_temp
CREATE TABLESPACE temp_large LOCATION '/data/pg_temp';
ALTER SYSTEM SET temp_tablespaces = 'temp_large';
SELECT pg_reload_conf();

-- 增大单查询临时文件限制（-1 表示无限制）
ALTER SYSTEM SET temp_file_limit = -1;
SELECT pg_reload_conf();
```

### 根因3：使用物化视图消除实时大排序（完整修复）

**描述**：对高频复杂报表创建物化视图，预先完成聚合和排序，消除实时查询时的临时文件需求。

**具体命令**：

```sql
CREATE MATERIALIZED VIEW <mv_name> AS
SELECT c.category_name, p.brand, s.store_name, s.city,
       EXTRACT(YEAR FROM <target_table>.sale_date)::INT  AS sale_year,
       EXTRACT(MONTH FROM <target_table>.sale_date)::INT AS sale_month,
       COUNT(*)          AS sale_count,
       SUM(sa.amount)    AS total_amount,
       SUM(sa.quantity)  AS total_quantity,
       AVG(sa.amount)    AS avg_amount
FROM <target_table_2> sa
JOIN <join_table_2> p   ON sa.product_id = p.product_id
JOIN <join_table_3> c ON p.category_id = c.category_id
JOIN <join_table_4> s     ON sa.store_id = s.store_id
GROUP BY c.category_name, p.brand, s.store_name, s.city,
         EXTRACT(YEAR FROM <target_table>.sale_date), EXTRACT(MONTH FROM <target_table>.sale_date);

CREATE INDEX <index_name> sales_monthly_report(<filter_col>, <groupby_col>);

-- 定期刷新
REFRESH MATERIALIZED VIEW <mv_name> <mv_name>;
```
