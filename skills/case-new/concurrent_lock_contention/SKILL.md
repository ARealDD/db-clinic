---
id: concurrent_lock_contention
name: "锁竞争 - 并发更新热点行导致锁等待超时"
category: lock_contention
version: "1.0"
author: "运维团队"
description: "高并发场景下多个事务同时更新同一行（如库存表），行锁竞争导致等待超时和系统吞吐量下降"

symptoms:
  - "订单/库存更新操作响应超过3秒，高峰期达10秒"
  - "pg_stat_activity 中出现大量 Lock 等待事件"
  - "系统日志出现 lock timeout 或 deadlock detected 错误"
  - "UPDATE 语句行数小但执行时间长"

keywords:
  - 锁竞争
  - lock contention
  - 行锁
  - row lock
  - 并发更新
  - 热点行
  - 乐观锁
  - FOR UPDATE

triggers:
  - "Lock.*wait"
  - "lock timeout"
  - "deadlock"
  - "LockRows"

evidence_required:
  - sql_text
  - system_view
evidence_optional:
  - index_info

---

## 根因

高并发订单支付时，多个事务同时对 inventory 表的同一行（同一产品同一仓库的库存记录）执行 `UPDATE`，后到的事务必须等待先到的事务释放行锁。如果事务持锁时间较长（包含复杂业务逻辑），锁等待队列快速堆积，最终超时。根本解决需缩短事务、精准索引定位、或采用乐观锁策略。

## 诊断步骤

### 步骤 1

**操作**：检查当前锁等待情况

- 工具：`execute_sql`
- 条件：系统出现锁超时或响应慢
- 执行语句：

```sql
-- 查看锁等待情况
SELECT pid, state, wait_event_type, wait_event,
       now() - query_start AS duration, query
FROM pg_stat_activity
WHERE wait_event_type = 'Lock'
ORDER BY duration DESC
LIMIT 20;

-- 查看阻塞关系
SELECT blocked.pid AS blocked_pid,
       blocker.pid AS blocker_pid,
       blocked.query AS blocked_query,
       blocker.query AS blocker_query
FROM pg_stat_activity blocked
JOIN pg_stat_activity blocker
    ON blocked.wait_event_type = 'Lock'
WHERE blocker.state = 'active';
```

**现象**：`pg_stat_activity` 中大量 UPDATE 语句的 wait_event_type 为 'Lock'，被同一行的其他事务阻塞。

**现象分析**：Lock 等待说明存在热点行竞争，需找出持锁事务和被阻塞事务的关系链。

---
### 步骤 2

**操作**：查看持锁与被阻塞关系的详情

- 工具：`execute_sql`
- 条件：已确认存在锁等待
- 执行语句：

```sql
SELECT l.locktype, l.relation::regclass, l.pid, l.mode, l.granted,
       a.query, a.state, now() - a.query_start AS duration
FROM pg_locks l
LEFT JOIN pg_stat_activity a ON l.pid = a.pid
WHERE l.relation::regclass::text IN ('<target_table_1>', '<target_table_2>')
ORDER BY l.granted, l.pid;
```

**现象**：pg_locks 显示多个进程在等待同一关系上的同一行锁（tuple lock）。

**现象分析**：行级锁（tuple lock）竞争是并发更新热点行的典型特征。

---
### 步骤 3

**操作**：检查 inventory 表的索引，确认 UPDATE WHERE 条件是否走索引

- 工具：`execute_sql`
- 条件：已确认热点行竞争
- 执行语句：

```sql
SELECT * FROM pg_indexes WHERE tablename = 'inventory';
EXPLAIN ANALYZE
<slow_sql>
```

**现象**：inventory 表的 (product_id, warehouse_id) 复合字段可能缺少索引，UPDATE 时扫描范围过大。

**现象分析**：缺少精确索引会使 UPDATE 锁定更多行，进一步加剧竞争；添加精确索引可缩小锁定范围。

---
## 恢复手段

### 根因1：终止长时间持锁事务（紧急响应）

**描述**：对于已造成大量等待的长事务，先终止持锁进程，释放等待队列，恢复系统响应能力。

**具体命令**：

```sql
-- 找出持锁时间最长的进程
SELECT pid, now() - xact_start AS lock_duration, query
FROM pg_stat_activity
WHERE xact_start IS NOT NULL
ORDER BY lock_duration DESC
LIMIT 10;

-- 尝试取消查询（较温和）
SELECT pg_cancel_backend(<pid>);
-- 无效则强制终止
SELECT pg_terminate_backend(<pid>);
```

### 根因2：为热点更新字段添加精确索引（主要修复）

**描述**：确保 UPDATE 的 WHERE 条件字段有复合索引，使每次更新只精确定位一行，而非扫描多行后逐一加锁。

**具体命令**：

```sql
-- 为库存表更新条件字段添加复合索引
CREATE INDEX CONCURRENTLY idx_inventory_product_warehouse
    ON inventory(product_id, warehouse_id);

-- 验证 UPDATE 走精确索引
EXPLAIN ANALYZE
<slow_sql>
```

### 根因3：缩短事务范围并使用 NOWAIT（完整修复）

**描述**：将业务逻辑从事务中移出，仅将数据库 DML 保留在事务内；使用 `FOR UPDATE NOWAIT` 快速失败而非无限等待。配合 idle_in_transaction_session_timeout 防止空闲事务长期持锁。

**具体命令**：

```sql
-- 优化后的事务（精简、快速）
BEGIN;
-- NOWAIT：获取不到锁立即报错，由应用层重试，而非无限等待
SELECT inventory_id FROM <target_table>
WHERE product_id = 7890 AND warehouse_id = 1
FOR UPDATE <target_table>;

UPDATE <target_table>
SET quantity = quantity - 1,
    available_quantity = available_quantity - 1,
    last_update_time = now()
WHERE product_id = 7890 AND warehouse_id = 1;
COMMIT;
-- 事务外执行业务日志、消息推送等非数据库操作

-- 设置空闲事务超时，防止持锁不提交
ALTER SYSTEM SET idle_in_transaction_session_timeout = '30000'; -- 30秒
SELECT pg_reload_conf();
```
