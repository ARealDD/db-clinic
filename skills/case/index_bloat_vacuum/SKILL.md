---
id: index_bloat_vacuum
name: "慢SQL - 索引膨胀导致扫描性能下降"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "频繁 UPDATE 操作在 MVCC 机制下产生大量死元组，VACUUM 未及时清理导致索引页积累无效条目，索引扫描需读取更多页面，I/O 开销持续增大"

symptoms:
  - "查询执行时间逐渐变长，从毫秒级退化为秒级"
  - "索引扫描节点的 actual time 明显增大"
  - "磁盘空间使用量持续增长，超出数据实际大小"
  - "VACUUM 执行时间越来越长"

keywords:
  - 索引膨胀
  - index bloat
  - 死元组
  - dead tuples
  - VACUUM
  - autovacuum
  - MVCC
  - REINDEX
  - n_dead_tup

triggers:
  - "n_dead_tup"
  - "dead_tuple_percent"
  - "duration:\\s*\\d{3,} ms"

evidence_required:
  - table_stats
  - index_info
evidence_optional:
  - explain_plan

---

## 根因

PostgreSQL MVCC 机制下，UPDATE 操作不修改原行而是插入新版本，旧版本成为死元组（dead tuple）。死元组同时残留在堆表和 B-tree 索引页中，占用空间但不服务查询。如果 autovacuum 触发阈值过高或执行不及时，死元组持续积累，索引页面膨胀，扫描需读取的物理页增多，I/O 开销线性增长。执行 VACUUM 清理死元组、REINDEX 重建索引可恢复性能。

## 诊断步骤

### 步骤 1

**操作**：检查表的死元组积累情况

- 工具：`execute_sql`
- 条件：查询性能逐渐下降

**现象**：`pg_stat_user_tables` 中 n_dead_tup 数值很高，dead_tuple_percent 超过 10%，last_autovacuum 时间久远或为空。

**现象分析**：死元组比例高说明 autovacuum 未及时清理，是性能下降的根本原因。

```sql
-- 检查死元组情况
SELECT relname,
       n_live_tup,
       n_dead_tup,
       ROUND(100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0), 2) AS dead_pct,
       last_vacuum,
       last_autovacuum,
       vacuum_count,
       autovacuum_count
FROM pg_stat_user_tables
WHERE relname = 'products'
ORDER BY n_dead_tup DESC;
```

---

### 步骤 2

**操作**：检查索引大小和膨胀程度

- 工具：`execute_sql`
- 条件：已确认死元组积累

**现象**：`pg_stat_user_indexes` 显示索引物理大小远超预期，部分索引的 idx_scan 较低但体积庞大；pgstattuple 扩展可查看索引内死亡条目比例。

**现象分析**：索引膨胀时即使走 Index Scan，也需要读取更多页面，I/O 开销和执行时间同步增大，需要 REINDEX 重建。

```sql
-- 查看索引大小
SELECT indexrelname AS index_name,
       pg_size_pretty(pg_relation_size(indexrelid)) AS index_size,
       idx_scan
FROM pg_stat_user_indexes
WHERE tablename = 'products'
ORDER BY pg_relation_size(indexrelid) DESC;

-- 表整体大小对比
SELECT relname,
       pg_size_pretty(pg_relation_size(relid))       AS table_size,
       pg_size_pretty(pg_indexes_size(relid))         AS index_size,
       pg_size_pretty(pg_total_relation_size(relid))  AS total_size
FROM pg_stat_user_tables
WHERE relname = 'products';
```

---

### 步骤 3

**操作**：分析 autovacuum 配置是否充分

- 工具：`execute_sql`
- 条件：确认 autovacuum 未及时清理

**现象**：autovacuum_vacuum_scale_factor 为默认 0.2（20% 死元组才触发），对于频繁更新的大表该阈值过高，导致 autovacuum 触发不及时。

**现象分析**：降低表级别的 autovacuum_vacuum_scale_factor 和 threshold，使 autovacuum 更频繁地清理该表。

```sql
-- 查看 autovacuum 全局配置
SHOW autovacuum_vacuum_scale_factor;
SHOW autovacuum_vacuum_threshold;
SHOW autovacuum_naptime;

-- 查看表级别 autovacuum 参数
SELECT reloptions FROM pg_class WHERE relname = 'products';

-- 使用 pgstattuple 查看索引死亡条目（如已安装扩展）
-- CREATE EXTENSION IF NOT EXISTS pgstattuple;
-- SELECT * FROM pgstatindex('idx_category_id');
```

---

## 恢复手段

### 根因1：执行 VACUUM 清理死元组（紧急恢复）

**描述**：立即手动执行 VACUUM ANALYZE 清理死元组并更新统计信息，不锁表（可在线执行）；如膨胀严重可用 VACUUM FULL 或 REINDEX 彻底重建，但会锁表。

**具体命令**：

```sql
-- 在线清理（不锁表，推荐优先使用）
SET maintenance_work_mem = '1GB';
VACUUM VERBOSE ANALYZE products;

-- 查看清理效果
SELECT relname, n_dead_tup, last_vacuum FROM pg_stat_user_tables WHERE relname = 'products';

-- 如死元组无法被普通 VACUUM 清理（如存在长事务阻塞），在低峰期执行
-- VACUUM FULL ANALYZE products;  -- 会锁表，谨慎使用

-- 在线重建膨胀严重的索引（不锁表）
REINDEX INDEX CONCURRENTLY idx_category_id;
REINDEX INDEX CONCURRENTLY idx_price;
REINDEX INDEX CONCURRENTLY idx_stock;
```

### 根因2：调整 autovacuum 参数防止再次膨胀（主要修复）

**描述**：降低频繁更新表的 autovacuum 触发阈值，使其更激进地清理死元组，避免膨胀再次累积。

**具体命令**：

```sql
-- 为 products 表设置更激进的 autovacuum 参数
ALTER TABLE products SET (
    autovacuum_enabled = true,
    autovacuum_vacuum_threshold = 100,        -- 默认 50
    autovacuum_vacuum_scale_factor = 0.02,    -- 默认 0.20，降低触发门槛
    autovacuum_analyze_threshold = 100,
    autovacuum_analyze_scale_factor = 0.02,
    autovacuum_vacuum_cost_delay = 2          -- ms，加快清理速度
);

-- 验证配置
SELECT reloptions FROM pg_class WHERE relname = 'products';

-- 手动触发一次 autovacuum 检测
SELECT schemaname, tablename, last_autovacuum FROM pg_stat_user_tables WHERE relname = 'products';
```

### 根因3：监控并预防索引膨胀（预防）

**描述**：建立索引膨胀监控查询，定期检查各表死元组比例和索引体积，结合 pg_cron 定期执行 VACUUM，避免膨胀积累。

**具体命令**：

```sql
-- 监控所有表的膨胀情况
SELECT relname,
       n_dead_tup,
       ROUND(100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0), 1) AS dead_pct,
       last_autovacuum,
       pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_stat_user_tables
WHERE n_dead_tup > 10000
ORDER BY n_dead_tup DESC;

-- 监控未使用的索引（候选删除对象）
SELECT indexrelname, idx_scan,
       pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes
WHERE idx_scan < 10
  AND pg_relation_size(indexrelid) > 1024 * 1024  -- 大于 1MB
ORDER BY pg_relation_size(indexrelid) DESC;
```
