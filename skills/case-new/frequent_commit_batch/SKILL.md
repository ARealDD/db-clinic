---
id: frequent_commit_batch
name: "慢SQL - 批量处理逐行提交导致 WAL I/O 瓶颈"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "批量订单处理中每条记录独立提交（每个 INSERT 一个 COMMIT），导致 WAL 日志刷盘频率极高，磁盘 I/O 成为瓶颈，处理 1000 条记录需数分钟"

symptoms:
  - "批量数据处理速度极慢，处理1000条记录需数分钟"
  - "数据库服务器磁盘 I/O 使用率接近 100%"
  - "WAL 写入量远超实际数据量"
  - "事务提交延迟（pg_stat_bgwriter 中 checkpoints_req 频繁）"

keywords:
  - 频繁提交
  - 批量处理
  - WAL
  - synchronous_commit
  - 批量插入
  - COPY
  - 事务合并
  - wal_buffers
  - checkpoint

triggers:
  - "xact_commit.*high"
  - "checkpoints_req"
  - "duration:\\s*\\d{3,} ms"

evidence_required:
  - sql_text
  - system_view
evidence_optional:
  - explain_plan

---

## 根因

每条 INSERT 包裹在独立事务中，每次 COMMIT 都触发 WAL 日志同步刷盘（fsync）。1000 条记录产生 1000 次磁盘 fsync，而磁盘 fsync 延迟通常在 1-10ms，1000 次 × 5ms = 5 秒仅花在 I/O 等待上。将多条 INSERT 合并到一个事务批量提交，可将 fsync 次数从 N 降至 1（每批），吞吐量提升数十倍。

## 诊断步骤

### 步骤 1

**操作**：确认批量处理存在过多事务提交

- 工具：`execute_sql`
- 条件：批量处理性能异常慢
- 执行语句：

```sql
SELECT wait_event_type, wait_event, COUNT(*) AS session_count,
       ARRAY_AGG(pid ORDER BY query_start) AS pids
FROM pg_stat_activity
WHERE wait_event IS NOT NULL AND pid <> pg_backend_pid()
GROUP BY wait_event_type, wait_event
ORDER BY session_count DESC;
```

```sql
-- 查看事务提交速率
SELECT datname, xact_commit, xact_rollback,
       blks_read, blks_hit,
       ROUND(blks_hit * 100.0 / NULLIF(blks_hit + blks_read, 0), 1) AS cache_hit_pct
FROM pg_stat_database
WHERE datname = current_database();

-- 查看检查点统计
SELECT checkpoints_timed, checkpoints_req,
       checkpoint_write_time, checkpoint_sync_time
FROM pg_stat_bgwriter;

-- 查看 WAL 配置
SHOW wal_buffers;
SHOW checkpoint_timeout;
SHOW max_wal_size;
SHOW synchronous_commit;
```

**现象**：`pg_stat_database` 中 xact_commit 快速增长，速率远超预期；同时 `pg_stat_bgwriter` 中 checkpoints_req（强制检查点）比例高，说明 WAL 产生速率超过配置上限。

**现象分析**：高频 xact_commit 与批量数据量不成比例，说明存在逐行提交模式，需修改为批量事务。

---
### 步骤 2

**操作**：对比单条提交与批量提交的执行时间

- 工具：`execute_sql`
- 条件：已确认逐行提交模式
- 执行语句：

```sql
EXPLAIN (ANALYZE, VERBOSE, BUFFERS, FORMAT TEXT)
<slow_sql>
```

```sql
-- 查看当前活跃批处理会话
SELECT pid, state, now() - query_start AS query_age, query
FROM pg_stat_activity
WHERE state = 'active' AND query LIKE '%INSERT%'
ORDER BY query_age DESC;

-- 查看 WAL 当前位置（用于计算 WAL 产生速率）
SELECT pg_current_wal_lsn(),
       pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), '0/00000000')) AS wal_total;
```

**现象**：单条 INSERT + COMMIT 执行时间约 1-10ms（主要是 fsync 延迟），100条独立提交耗时 100-1000ms；而 100条合并为一个事务仅需 5-20ms。

**现象分析**：性能差距主要来自 fsync 次数，每个事务提交都需要等待 WAL 写入磁盘，合并批次是最直接的优化。

---
### 步骤 3

**操作**：确认批次大小和 WAL 配置是否匹配

- 工具：`execute_sql`
- 条件：已决定改为批量提交
- 执行语句：

```sql
SELECT wait_event_type, wait_event, COUNT(*) AS session_count,
       ARRAY_AGG(pid ORDER BY query_start) AS pids
FROM pg_stat_activity
WHERE wait_event IS NOT NULL AND pid <> pg_backend_pid()
GROUP BY wait_event_type, wait_event
ORDER BY session_count DESC;
```

```sql
-- 确认 synchronous_commit 设置
SHOW synchronous_commit;

-- 估算合理批次大小（每批 work 的内存使用）
SELECT current_setting('work_mem') AS work_mem,
       current_setting('wal_buffers') AS wal_buffers;
```

**现象**：synchronous_commit=on（默认），每次 COMMIT 都同步等待 WAL 落盘；wal_buffers 为默认 4MB，无法缓冲大批量写入峰值。

**现象分析**：批量处理时建议每 100-1000 条提交一次；对于允许少量数据丢失的非关键批量导入，可设置 synchronous_commit=off 进一步提速。

---
## 恢复手段

### 根因1：将逐行提交改为批量事务提交（主要修复）

**描述**：将批量处理循环改为每 N 条记录提交一次事务，将 fsync 次数从 N 降至 N/batch_size，是最直接有效的优化。

**具体命令**：

```sql
-- 优化后：100条为一批，一次提交
BEGIN;
INSERT INTO <target_table> (<filter_col>, <groupby_col>, <join_col>, <order_col>, <col_5>)
VALUES
    (7777777, 12345, CURRENT_TIMESTAMP, 1000, 'pending'),
    (7777778, 12346, CURRENT_TIMESTAMP, 2000, 'pending'),
    -- ... 更多行 ...
    (7777876, 12444, CURRENT_TIMESTAMP, 1800, 'pending');

INSERT INTO <target_table> (<filter_col>, <groupby_col>, <join_col>, <order_col>, <col_5>)
VALUES
    (77777771, 7777777, 1001, 2, 500),
    (77777772, 7777777, 1002, 1, 500),
    -- ... 更多行 ...
    (77778761, 7777876, 1005, 3, 600);
COMMIT;
-- 每 100 个订单 COMMIT 一次，减少 99% 的 fsync
```

### 根因2：使用 COPY 命令批量导入（辅助优化）

**描述**：COPY 命令直接写入数据文件，WAL 量最小，比 INSERT 快 10 倍以上；适合初始化数据加载或大批量历史数据迁移场景。

**具体命令**：

```sql
-- 从 CSV 文件批量导入（速度最快）
COPY orders (<filter_col>, <groupby_col>, <join_col>, <order_col>, <col_5>)
FROM '/path/to/orders.csv'
WITH (FORMAT csv, HEADER true, DELIMITER ',');

COPY order_items (<filter_col>, <groupby_col>, <join_col>, <order_col>, <col_5>)
FROM '/path/to/order_items.csv'
WITH (FORMAT csv, HEADER true, DELIMITER ',');

-- 导入完成后更新统计信息
ANALYZE <target_table>;
ANALYZE <target_table>;
```

### 根因3：批量导入期间临时调整 synchronous_commit（完整修复）

**描述**：对于允许极小数据丢失风险（最多丢失最近 wal_writer_delay 约 200ms 的数据）的批量导入场景，临时关闭 synchronous_commit 可省去 fsync 等待，性能再提升 2-5 倍；导入完成后立即恢复。

**具体命令**：

```sql
-- 批量导入前关闭同步提交（仅限当前会话）
SET synchronous_commit = off;

-- 执行批量 INSERT（配合批量事务）
BEGIN;
INSERT INTO <target_table> ... ;
INSERT INTO <target_table> ... ;
COMMIT;
-- 无需等待 WAL fsync，速度提升显著

-- 导入完成后恢复
RESET synchronous_commit;

-- 调整 WAL 缓冲区（全局，需重启）
-- ALTER SYSTEM SET wal_buffers = '64MB';
-- SELECT pg_reload_conf();
```
