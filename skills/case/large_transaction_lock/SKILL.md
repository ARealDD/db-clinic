---
id: large_transaction_lock
name: "锁阻塞 - 大事务长时间持锁导致连接堆积"
category: lock_contention
version: "1.0"
author: "运维团队"
description: "事务中包含非数据库操作或复杂业务逻辑，事务持续时间过长，锁长时间不释放，引发级联等待和连接池耗尽"

symptoms:
  - "转账/业务操作从秒级变为分钟级"
  - "数据库连接池耗尽，新连接请求被拒绝"
  - "出现 lock timeout 或 deadlock detected 错误"
  - "长时间运行的事务（xact_start 超过5分钟）持续存在"

keywords:
  - 大事务
  - long transaction
  - 持锁
  - lock hold
  - idle in transaction
  - 连接耗尽
  - FOR UPDATE
  - idle_in_transaction_session_timeout

triggers:
  - "idle in transaction"
  - "lock timeout"
  - "xact_start.*[0-9]+ min"

evidence_required:
  - system_view
evidence_optional:
  - sql_text

---

## 根因

事务中不仅包含 SQL 操作，还包含外部 API 调用、业务计算、日志写入等耗时非数据库操作，导致事务生命周期被拉长。在 `BEGIN` 和 `COMMIT` 之间，所有获取的行锁都被持有，其他想要更新同一行的事务只能等待。当此类"大事务"大量并发时，锁等待链快速扩散，连接被阻塞占用，连接池很快耗尽。

## 诊断步骤

### 步骤 1

**操作**：查看长时间运行的事务

- 工具：`execute_sql`
- 条件：系统响应变慢，连接数接近上限

**现象**：`pg_stat_activity` 中出现 state='idle in transaction' 的会话，`xact_start` 显示事务已持续数分钟。

**现象分析**：'idle in transaction' 是事务持锁的典型标志，说明应用层在持有数据库锁期间执行了缓慢的非数据库操作。

```sql
-- 查看长时间运行的事务
SELECT pid, usename, datname,
       now() - xact_start AS xact_duration,
       state,
       query
FROM pg_stat_activity
WHERE xact_start IS NOT NULL
  AND now() - xact_start > interval '1 minute'
ORDER BY xact_duration DESC;
```

---

### 步骤 2

**操作**：查看当前锁阻塞关系

- 工具：`execute_sql`
- 条件：已确认存在长事务

**现象**：`pg_locks` 显示长事务持有的锁阻塞了大量其他查询，形成等待链。

**现象分析**：阻塞链越长说明影响越大，持锁的根事务是需要优先处理的对象。

```sql
SELECT blocked.pid     AS blocked_pid,
       blocked.usename AS blocked_user,
       blocker.pid     AS blocker_pid,
       blocker.usename AS blocker_user,
       now() - blocked.query_start AS wait_duration,
       blocked.query  AS blocked_query,
       blocker.query  AS blocker_query
FROM pg_locks blocked_locks
JOIN pg_stat_activity blocked
    ON blocked.pid = blocked_locks.pid
JOIN pg_locks blocker_locks
    ON blocker_locks.locktype = blocked_locks.locktype
   AND blocker_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
   AND blocker_locks.pid != blocked_locks.pid
JOIN pg_stat_activity blocker
    ON blocker.pid = blocker_locks.pid
WHERE NOT blocked_locks.GRANTED;
```

---

### 步骤 3

**操作**：检查 idle_in_transaction_session_timeout 配置

- 工具：`execute_sql`
- 条件：确认事务超时机制缺失

**现象**：`idle_in_transaction_session_timeout` 为 0（禁用），事务可以无限期持锁。

**现象分析**：缺少事务超时机制是大事务问题持续存在的重要原因，应配置合理的超时值。

```sql
SHOW idle_in_transaction_session_timeout;
SHOW lock_timeout;
SHOW statement_timeout;
```

---

## 恢复手段

### 根因1：终止长时间持锁事务（紧急恢复）

**描述**：立即终止超过阈值时长的事务，释放被阻塞的连接队列，恢复系统响应能力。

**具体命令**：

```sql
-- 找出超过5分钟的长事务
SELECT pid, now() - xact_start AS duration, state, query
FROM pg_stat_activity
WHERE xact_start IS NOT NULL
  AND now() - xact_start > interval '5 minutes'
ORDER BY duration DESC;

-- 取消查询（事务保持）
SELECT pg_cancel_backend(<pid>);

-- 若无效则强制终止（回滚整个事务）
SELECT pg_terminate_backend(<pid>);
```

### 根因2：缩短事务范围，业务逻辑移到事务外（主要修复）

**描述**：将外部 API 调用、业务计算、日志写入等操作移到 BEGIN/COMMIT 之外，事务只包含最小必要的 SQL 操作，并使用 NOWAIT 快速失败而非等待锁。

**具体命令**：

```sql
-- 优化后的事务设计
-- 1. 事务外：预处理、业务校验、参数计算

BEGIN;
-- 2. 精简事务：只包含数据库 DML
SELECT balance FROM accounts WHERE account_id = 'A001' FOR UPDATE NOWAIT;
UPDATE accounts SET balance = balance - 1000 WHERE account_id = 'A001';
UPDATE accounts SET balance = balance + 1000 WHERE account_id = 'B002';
INSERT INTO transactions (from_account, to_account, amount, transaction_date, status)
VALUES ('A001', 'B002', 1000, CURRENT_TIMESTAMP, 'SUCCESS');
COMMIT;

-- 3. 事务外：发送通知、写操作日志、触发异步任务
```

### 根因3：配置事务超时防止再次发生（预防）

**描述**：设置 `idle_in_transaction_session_timeout` 自动终止长时间未提交的事务，防止悬挂事务永久持锁。

**具体命令**：

```sql
-- 设置空闲事务超时（超过30秒自动断开）
ALTER SYSTEM SET idle_in_transaction_session_timeout = '30000';

-- 设置语句超时（单个语句超过60秒自动取消）
ALTER SYSTEM SET statement_timeout = '60000';

-- 设置锁等待超时（等锁超过5秒立即报错）
ALTER SYSTEM SET lock_timeout = '5000';

SELECT pg_reload_conf();
```
