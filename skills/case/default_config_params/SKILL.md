---
id: default_config_params
name: "慢SQL - PostgreSQL 默认参数配置过小导致性能瓶颈"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "PostgreSQL 默认配置面向最小化资源占用（shared_buffers=128MB, work_mem=4MB），在生产服务器上未调优，导致大量磁盘 I/O、频繁磁盘排序和不合理执行计划"

symptoms:
  - "复杂查询频繁出现 Sort Method: external merge Disk"
  - "缓存命中率（blks_hit/blks_read）低于 90%"
  - "优化器选择 Nested Loop 而非 Hash Join 处理大表关联"
  - "VACUUM/REINDEX 等维护操作执行极慢"

keywords:
  - shared_buffers
  - work_mem
  - effective_cache_size
  - maintenance_work_mem
  - random_page_cost
  - effective_io_concurrency
  - 默认配置
  - 参数调优
  - PostgreSQL 配置
  - pgTune

triggers:
  - "Sort Method: external merge Disk"
  - "shared_buffers"
  - "work_mem"

evidence_required:
  - system_view
evidence_optional:
  - explain_plan

---

## 根因

PostgreSQL 安装后的默认参数（shared_buffers=128MB, work_mem=4MB, maintenance_work_mem=64MB）适用于极小型环境，在 16-64GB 内存的生产服务器上严重低配：128MB shared_buffers 缓存命中率极低，大量查询每次都读磁盘；4MB work_mem 导致几乎所有 Sort 和 Hash Join 溢出到磁盘。正确配置应根据实际内存按比例调整（shared_buffers=25% RAM, effective_cache_size=75% RAM），可使系统性能提升数倍。

## 诊断步骤

### 步骤 1

**操作**：检查关键配置参数当前值

- 工具：`execute_sql`
- 条件：系统整体性能不佳，无明显单点 SQL 问题

**现象**：`pg_settings` 显示 shared_buffers=128MB（对于16GB内存服务器应为4GB）、work_mem=4MB（应为16-64MB）、maintenance_work_mem=64MB（应为1-2GB），参数严重低于推荐值。

**现象分析**：参数偏低是系统性问题，所有查询都受影响，需按服务器实际内存系统性调整。

```sql
-- 查看内存相关关键参数
SELECT name, setting, unit, source
FROM pg_settings
WHERE name IN (
    'shared_buffers', 'work_mem', 'maintenance_work_mem',
    'effective_cache_size', 'wal_buffers',
    'max_connections', 'random_page_cost',
    'effective_io_concurrency', 'checkpoint_timeout', 'max_wal_size'
)
ORDER BY name;
```

---

### 步骤 2

**操作**：确认缓存命中率和磁盘读写状况

- 工具：`execute_sql`
- 条件：已确认参数低于推荐值

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

**现象**：`pg_stat_database` 中 blks_read（磁盘读）远高于预期，cache_hit_pct 低于 90%；`pg_stat_bgwriter` 中 buffers_clean（主动清理）频率高，说明 shared_buffers 不足以缓存热数据。

**现象分析**：缓存命中率是最直观的参数充足性指标，目标应达到 95%+；低命中率直接导致大量物理 I/O，查询延迟增加数倍。

```sql
-- 查看缓存命中率
SELECT datname,
       blks_hit,
       blks_read,
       ROUND(blks_hit * 100.0 / NULLIF(blks_hit + blks_read, 0), 1) AS cache_hit_pct,
       xact_commit,
       xact_rollback
FROM pg_stat_database
WHERE datname = current_database();

-- 查看 bgwriter 统计（反映 shared_buffers 压力）
SELECT buffers_clean, maxwritten_clean, buffers_backend,
       buffers_alloc, checkpoint_write_time
FROM pg_stat_bgwriter;
```

---

### 步骤 3

**操作**：验证磁盘排序和执行计划问题

- 工具：`execute_sql`
- 条件：已确认内存参数过小

```sql
-- 将 <your_sql_here> 替换为实际的慢查询语句
EXPLAIN (ANALYZE, VERBOSE, BUFFERS, FORMAT TEXT)
<your_sql_here>;
```

**现象**：执行计划中出现 `Sort Method: external merge Disk: NNkB`（溢出磁盘排序）或 Hash Join 的 `Batches: N`（Hash 表溢出），直接说明 work_mem 不足。

**现象分析**：提高 work_mem 后这些节点会变为内存排序（`Sort Method: quicksort Memory`），性能提升明显。

```sql
-- 检查典型分析查询是否有磁盘排序
EXPLAIN ANALYZE
SELECT customer_id, SUM(total_amount)
FROM orders
GROUP BY customer_id
ORDER BY SUM(total_amount) DESC
LIMIT 100;

-- 会话级临时提高 work_mem 测试效果
SET work_mem = '256MB';
EXPLAIN ANALYZE
SELECT customer_id, SUM(total_amount)
FROM orders
GROUP BY customer_id
ORDER BY SUM(total_amount) DESC
LIMIT 100;
RESET work_mem;
```

---

## 恢复手段

### 根因1：调整内存核心参数（主要修复）

**描述**：按服务器实际内存调整 shared_buffers、work_mem、maintenance_work_mem 和 effective_cache_size，这四个参数对性能影响最大。修改后需重启（shared_buffers）或 reload（其余）生效。

**具体命令**：

```sql
-- 根据服务器内存调整（以 16GB 内存为例）
-- shared_buffers = 25% RAM = 4GB
ALTER SYSTEM SET shared_buffers = '4GB';

-- work_mem = (RAM * 0.25) / max_connections，至少 16MB
-- 16GB * 25% / 100 = 40MB，保守取 16MB
ALTER SYSTEM SET work_mem = '16MB';

-- maintenance_work_mem = 10-20% RAM
ALTER SYSTEM SET maintenance_work_mem = '2GB';

-- effective_cache_size = 75% RAM（告知优化器可用缓存量）
ALTER SYSTEM SET effective_cache_size = '12GB';

-- 需要重启使 shared_buffers 生效；其余参数 reload 即可
SELECT pg_reload_conf();

-- 重启后验证
SHOW shared_buffers;
SHOW work_mem;
```

### 根因2：调整 I/O 和 WAL 相关参数（辅助优化）

**描述**：根据存储设备类型调整 random_page_cost 和 effective_io_concurrency（SSD vs HDD），调整 WAL 相关参数减少检查点 I/O 峰值。

**具体命令**：

```sql
-- SSD 存储：降低随机读代价，鼓励优化器使用索引
ALTER SYSTEM SET random_page_cost = '1.1';       -- HDD 默认 4.0
ALTER SYSTEM SET effective_io_concurrency = '200'; -- HDD 默认 1

-- 顺序页代价（SSD 可保持 1.0）
ALTER SYSTEM SET seq_page_cost = '1.0';

-- WAL 配置优化
ALTER SYSTEM SET wal_buffers = '64MB';             -- 默认 -1（约 3MB），推荐 16-64MB
ALTER SYSTEM SET checkpoint_timeout = '15min';     -- 默认 5min
ALTER SYSTEM SET max_wal_size = '4GB';             -- 默认 1GB
ALTER SYSTEM SET checkpoint_completion_target = '0.9'; -- 平滑写入

SELECT pg_reload_conf();
```

### 根因3：使用 pgTune 生成完整配置建议（完整修复）

**描述**：pgTune 工具根据服务器规格（内存、CPU、磁盘类型、连接数、工作负载类型）自动生成完整的 gaussdb.conf 推荐参数，适合系统性调优。

**具体命令**：

```sql
-- 查看当前所有非默认参数（了解已有自定义配置）
SELECT name, setting, source
FROM pg_settings
WHERE source NOT IN ('default', 'override')
ORDER BY name;

-- 应用 pgTune 推荐（以 16GB RAM, 8 CPU, SSD, OLTP 负载, 200 连接为例）
ALTER SYSTEM SET max_connections = '200';
ALTER SYSTEM SET shared_buffers = '4GB';
ALTER SYSTEM SET effective_cache_size = '12GB';
ALTER SYSTEM SET maintenance_work_mem = '1GB';
ALTER SYSTEM SET checkpoint_completion_target = '0.9';
ALTER SYSTEM SET wal_buffers = '16MB';
ALTER SYSTEM SET default_statistics_target = '100';
ALTER SYSTEM SET random_page_cost = '1.1';
ALTER SYSTEM SET effective_io_concurrency = '200';
ALTER SYSTEM SET work_mem = '5242kB';   -- (4GB * 0.25) / (200 * 2)
ALTER SYSTEM SET min_wal_size = '1GB';
ALTER SYSTEM SET max_wal_size = '4GB';

-- 参数配置文件位置
SHOW config_file;
SELECT pg_reload_conf();
```
