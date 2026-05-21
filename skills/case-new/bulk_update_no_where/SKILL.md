---
id: bulk_update_no_where
name: "危险操作 - 批量 UPDATE/DELETE 缺少 WHERE 条件导致全表误更新"
category: dangerous_operation
version: "1.0"
author: "运维团队"
description: "UPDATE/DELETE 语句遗漏 WHERE 条件，导致全表数据被误更新或删除，系统卡死并造成业务中断"

symptoms:
  - "UPDATE/DELETE 执行时间超过 30 秒，系统卡顿"
  - "CPU 使用率 100%，磁盘 I/O 极度频繁"
  - "数据库几乎无法响应其他请求"
  - "业务数据全部被错误修改，需要从备份恢复"

keywords:
  - 全表更新
  - UPDATE without WHERE
  - DELETE without WHERE
  - 数据灾难
  - 误操作
  - WAL 暴增
  - 锁表
  - 紧急恢复

triggers:
  - "UPDATE.*SET.*status"
  - "rows affected.*[1-9][0-9]{4,}"

evidence_required:
  - sql_text
evidence_optional:
  - system_view

---

## 根因

`UPDATE products SET status = 'inactive'` 没有 WHERE 条件，数据库会逐行更新表中每一条记录。百万级记录的全表更新产生大量 WAL 日志、持有表级或行级锁、消耗所有 CPU 和磁盘 I/O，导致其他查询全部阻塞。更糟的是数据层面的破坏——所有商品被错误标记，业务立即中断。

## 诊断步骤

### 步骤 1

**操作**：确认是否有异常的全表 UPDATE/DELETE 操作正在执行

- 工具：`execute_sql`
- 条件：系统突然卡死或数据异常
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
ORDER BY duration DESC
LIMIT 10;
```

**现象**：`pg_stat_activity` 中出现执行时间异常长（数十秒以上）的 UPDATE/DELETE 语句，无 WHERE 条件。

**现象分析**：无 WHERE 条件的 DML 操作是最高优先级紧急事件，需立即评估是否终止进程。

---
### 步骤 2

**操作**：检查锁等待情况，确认阻塞范围

- 工具：`execute_sql`
- 条件：已确认全表 DML 正在执行
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
SELECT l.locktype, l.relation::regclass, l.pid, l.mode, l.granted,
       a.query, a.state
FROM pg_locks l
LEFT JOIN pg_stat_activity a ON l.pid = a.pid
WHERE l.relation IS NOT NULL
ORDER BY l.granted DESC;
```

**现象**：大量查询在等待被全表 UPDATE 持有的锁，系统几乎全部阻塞。

**现象分析**：锁等待队列越长，终止错误操作后的影响恢复越快，需尽快决策是否 `pg_terminate_backend`。

---
### 步骤 3

**操作**：评估数据损坏范围，检查备份状态

- 工具：`execute_sql`
- 条件：操作已完成或需要制定恢复计划
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
-- 检查数据损坏范围
SELECT status, COUNT(*) FROM <target_table> GROUP BY status;

-- 检查归档/备份状态
SELECT * FROM pg_stat_archiver;
SELECT now() AS current_time, pg_current_wal_lsn() AS current_lsn;
```

**现象**：查询 products 表中各 status 分布，若所有记录都变为 'inactive' 则确认全表误更新。

**现象分析**：明确数据损坏范围后，决定走 PITR 时间点恢复还是从逻辑备份恢复。

---
## 恢复手段

### 根因1：立即终止错误操作（紧急响应）

**描述**：如果全表 UPDATE 仍在执行，立即终止对应进程，将损坏范围限制在已更新的行数。PostgreSQL 会自动回滚未提交的事务。

**具体命令**：

```sql
-- 找到问题进程
SELECT pid, query
FROM pg_stat_activity
WHERE query LIKE 'UPDATE products%'
  AND state = 'active';

-- 终止进程（事务自动回滚）
SELECT pg_terminate_backend(<pid>);

-- 验证操作被回滚
SELECT status, COUNT(*) FROM products GROUP BY status;
```

### 根因2：PITR 时间点恢复（数据已损坏）

**描述**：如果操作已提交，需要通过 Point-In-Time Recovery 恢复到错误操作之前的时间点（需要提前配置 WAL 归档）。

**具体命令**：

```sql
-- 在 recovery.conf 或 gaussdb.conf 中配置恢复目标时间
-- restore_command = 'cp /archive/%f %p'
-- recovery_target_time = '2024-01-15 14:30:00'  -- 操作发生前的时间点
-- recovery_target_action = 'promote'

-- 恢复后验证
SELECT status, COUNT(*) FROM <target_table> GROUP BY status;
```

### 根因3：规范批量操作，防止再次发生（预防）

**描述**：所有批量 DML 操作必须包含 WHERE 条件，使用 LIMIT 分批执行，执行前先用 SELECT 验证条件覆盖范围。

**具体命令**：

```sql
-- 执行前先 SELECT 验证 WHERE 条件
SELECT COUNT(*) FROM <target_table>
WHERE stock = 0 AND last_update_time < now() - interval '90 days';

-- 正确的分批更新
UPDATE <target_table>
SET status = 'inactive', last_update_time = now()
WHERE stock = 0 AND last_update_time < now() - interval '90 days'
LIMIT 10000;
-- 重复执行直到影响行数为 0

-- 为 WHERE 条件字段添加索引
CREATE INDEX CONCURRENTLY <index_name>
    ON <target_table>(<filter_col>, <groupby_col>);
```
