---
id: no_partitioning_history
name: "慢SQL - 历史数据大表未分区导致查询全表扫描"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "亿级历史数据存储在单表中，时间范围查询即使有索引仍慢，分区裁剪可将扫描范围缩小数十倍"

symptoms:
  - "历史数据时间范围查询响应超过10秒"
  - "交易表数据量超过亿级，持续增长"
  - "备份和 VACUUM 操作耗时过长"
  - "即使有 transaction_date 索引性能仍然较差"

keywords:
  - 分区表
  - table partition
  - 历史数据
  - 大表
  - 分区裁剪
  - partition pruning
  - 时间分区
  - RANGE PARTITION

triggers:
  - "Seq Scan on transactions"
  - "Bitmap Index Scan.*transactions"
  - "duration:\\s*\\d{4,} ms"

evidence_required:
  - sql_text
  - explain_plan
evidence_optional:
  - table_stats

---

## 根因

transactions 等历史数据表数据量达亿级，所有数据存在单一物理表中。即使有 customer_id、transaction_date 复合索引，索引本身体积也达 GB 级，B-tree 遍历成本高。使用按月/年分区后，查询的 WHERE 条件包含分区键（transaction_date），优化器直接跳过不相关分区（分区裁剪），将扫描范围缩减为目标月份，性能提升可达 10-100 倍。

## 诊断步骤

### 步骤 1

**操作**：查看历史数据查询的慢查询情况

- 工具：`execute_sql`
- 条件：历史查询响应慢
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
SELECT pid, state, query_start, now() - query_start AS duration, query
FROM pg_stat_activity
WHERE state = 'active'
  AND query LIKE '%transactions%'
ORDER BY duration DESC
LIMIT 10;
```

**现象**：`pg_stat_activity` 中历史数据查询长时间活跃，duration 超过 10s。

**现象分析**：单表亿级数据查询必然慢，需确认是否有分区，以及索引是否足够高效。

---
### 步骤 2

**操作**：获取历史查询执行计划，确认是否有分区裁剪

- 工具：`execute_sql`
- 条件：已收集 sql_text
- 执行语句：

```sql
EXPLAIN (ANALYZE, VERBOSE, BUFFERS, FORMAT TEXT)
<slow_sql>
```

```sql
EXPLAIN ANALYZE
<slow_sql>
```

**现象**：执行计划显示直接扫描 transactions 大表，无 Partition 裁剪节点，实际扫描行数达到亿级。

**现象分析**：无分区裁剪意味着即使 WHERE 条件包含日期范围，仍需扫描整张大表的索引。

---
### 步骤 3

**操作**：检查表数据量和索引大小

- 工具：`execute_sql`
- 条件：确认是大表且无分区
- 执行语句：

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

```sql
SELECT pg_size_pretty(pg_total_relation_size('transactions')) AS total_size;
SELECT COUNT(*) FROM transactions;
SELECT relname, last_analyze, last_autoanalyze, n_live_tup
FROM pg_stat_user_tables WHERE relname = '<target_table>';
```

**现象**：transactions 表总数据量超过亿行，索引大小达 GB 级，`last_analyze` 显示分析间隔过长。

**现象分析**：大表索引维护成本高，统计信息也难以保持准确，分区是根本解决方案。

---
## 恢复手段

### 根因1：创建按月分区的分区表（主要修复）

**描述**：将 transactions 重建为 RANGE 分区表，按 transaction_date 月度分区，每个分区单独建索引，查询时自动触发分区裁剪。

**具体命令**：

```sql
-- 创建分区主表
CREATE TABLE transactions_partitioned (
    transaction_id   BIGINT NOT NULL,
    customer_id      BIGINT NOT NULL,
    account_id       BIGINT NOT NULL,
    transaction_type VARCHAR(20) NOT NULL,
    amount           DECIMAL(12,2) NOT NULL,
    transaction_date TIMESTAMP NOT NULL,
    status           VARCHAR(20) NOT NULL,
    description      <target_table>(200),
    create_time      TIMESTAMP NOT NULL
) PARTITION BY RANGE (<filter_col>);

-- 创建月度分区（示例：2023年）
CREATE TABLE transactions_202301 PARTITION OF transactions_partitioned
    FOR VALUES FROM ('<start_date>') TO ('<end_date>');
CREATE TABLE transactions_202302 PARTITION OF transactions_partitioned
    FOR VALUES FROM ('<date>') TO ('<date>');
-- ... 依此类推到 transactions_202312

-- 为每个分区创建复合索引
CREATE INDEX <index_name> ON <target_table>(<filter_col>, <groupby_col>);
CREATE INDEX <index_name> ON <target_table>(<filter_col>, <groupby_col>);
-- ... 依此类推
```

### 根因2：数据迁移和自动分区管理（完整修复）

**描述**：将历史数据批量迁移到分区表，并创建自动建分区的函数，通过定时任务提前创建未来分区。

**具体命令**：

```sql
-- 分批迁移数据（减少对业务影响）
INSERT INTO transactions_partitioned
SELECT * FROM transactions
WHERE transaction_date >= '2023-01-01' AND transaction_date < '2023-02-01';
-- 重复执行，按月迁移

-- 自动创建未来分区的函数
CREATE OR REPLACE FUNCTION create_monthly_partition()
RETURNS void AS $$
DECLARE
    v_month DATE;
    v_name  TEXT;
BEGIN
    FOR i IN 0..2 LOOP
        v_month := date_trunc('month', now()) + (i || ' month')::interval;
        v_name  := 'transactions_' || to_char(v_month, 'YYYYMM');
        IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = v_name) THEN
            EXECUTE format(
                'CREATE TABLE %I PARTITION OF transactions_partitioned
                 FOR VALUES FROM (%L) TO (%L)',
                v_name,
                v_month,
                v_month + '1 month'::interval
            );
            EXECUTE format(
                'CREATE INDEX idx_%I_cust_date ON %I(customer_id, transaction_date)',
                v_name, v_name
            );
        END IF;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

SELECT create_monthly_partition();

-- 验证分区裁剪效果
EXPLAIN ANALYZE
<slow_sql>
```
