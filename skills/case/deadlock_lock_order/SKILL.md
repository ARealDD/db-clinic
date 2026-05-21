---
id: deadlock_lock_order
name: "死锁 - 并发事务锁获取顺序不一致"
category: lock_contention
version: "1.0"
author: "运维团队"
description: "并发转账等场景中，两个事务以相反顺序锁定同一批行（事务1: A→B，事务2: B→A），形成循环依赖，PostgreSQL 检测到死锁后强制回滚其中一个事务"

symptoms:
  - "应用日志出现 ERROR: deadlock detected"
  - "事务执行时间不稳定，有时快有时慢"
  - "出现随机的事务回滚，业务操作失败"
  - "数据库 CPU 使用率升高，锁等待队列变长"

keywords:
  - 死锁
  - deadlock
  - 循环依赖锁
  - 锁获取顺序
  - 转账
  - 并发事务
  - deadlock detected
  - lock_timeout

triggers:
  - "deadlock detected"
  - "Lock wait timeout"
  - "ERROR.*deadlock"

evidence_required:
  - sql_text
  - wait_events
evidence_optional:
  - table_stats

---

## 根因

事务1持有账户 A 的行锁，等待账户 B；事务2持有账户 B 的行锁，等待账户 A。两个事务形成循环依赖，PostgreSQL 死锁检测器（每隔 `deadlock_timeout` 检测一次，默认 1s）发现后强制回滚代价较小的事务。根本原因是不同代码路径以不同顺序操作同一批行，消除死锁的关键是统一锁获取顺序（如按 account_id 升序操作）。

## 诊断步骤

### 步骤 1

**操作**：确认死锁发生，查看阻塞关系

- 工具：`execute_sql`
- 条件：应用报 deadlock detected 错误

**现象**：`pg_locks` 中出现多个进程相互等待对方持有的锁，形成环形依赖；`pg_stat_activity` 中可见 Lock 等待状态的会话。

**现象分析**：循环锁等待是死锁的直接证据，需结合事务 SQL 判断哪些行被以不一致顺序获取。

```sql
-- 查看当前锁等待关系
SELECT
    blocked_locks.pid        AS blocked_pid,
    blocked_activity.query   AS blocked_query,
    blocking_locks.pid       AS blocking_pid,
    blocking_activity.query  AS blocking_query
FROM pg_locks blocked_locks
JOIN pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
JOIN pg_locks blocking_locks
     ON blocking_locks.locktype = blocked_locks.locktype
    AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
    AND blocking_locks.tuple    IS NOT DISTINCT FROM blocked_locks.tuple
    AND blocking_locks.pid     != blocked_locks.pid
JOIN pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid
WHERE NOT blocked_locks.granted;

-- 查看长时间运行的事务
SELECT pid, usename, now() - xact_start AS duration, state, query
FROM pg_stat_activity
WHERE xact_start IS NOT NULL
ORDER BY duration DESC
LIMIT 10;
```

---

### 步骤 2

**操作**：获取死锁事务的 SQL 文本，分析行锁顺序

- 工具：`execute_sql`
- 条件：已确认死锁发生

**现象**：两个事务 SQL 对同一批账户行执行 UPDATE，但顺序相反（T1: A001→B002，T2: B002→A001），构成互相等待。

**现象分析**：锁获取顺序不一致是根因，修复方案是在应用层统一操作顺序（如始终按 account_id 字典序升序 UPDATE）。

```sql
-- 查看 PostgreSQL 日志中的死锁详情（需开启 log_lock_waits）
SHOW log_lock_waits;
SHOW deadlock_timeout;

-- 查看当前活跃会话的完整 SQL
SELECT pid, state, now() - query_start AS query_age, query
FROM pg_stat_activity
WHERE state = 'active'
ORDER BY query_age DESC
LIMIT 20;
```

---

### 步骤 3

**操作**：验证修复后死锁消除

- 工具：`execute_sql`
- 条件：已按统一顺序重写事务代码

**现象**：统一锁顺序后，pg_stat_activity 中不再出现循环等待；deadlock 错误日志停止产生。

**现象分析**：死锁消除后，两个事务会依次排队执行，不再产生回滚。同时配置合理的 lock_timeout 和 deadlock_timeout 可加速死锁检测和失败快返回。

```sql
-- 确认 lock_timeout 配置
SHOW lock_timeout;
SHOW deadlock_timeout;

-- 监控死锁率（通过日志或 pg_stat_database）
SELECT datname, deadlocks FROM pg_stat_database WHERE datname = current_database();
```

---

## 恢复手段

### 根因1：统一锁获取顺序（主要修复）

**描述**：修改业务代码，确保所有涉及多行 UPDATE 的事务始终按统一顺序（如 account_id 升序）操作，消除循环依赖。

**具体命令**：

```sql
-- 修复后的转账事务（两个方向都按 account_id 升序操作）
-- 转账：A001 → B002
BEGIN;
UPDATE accounts SET balance = balance - 1000 WHERE account_id = 'A001'; -- 先锁小
UPDATE accounts SET balance = balance + 1000 WHERE account_id = 'B002'; -- 后锁大
INSERT INTO transactions (from_account, to_account, amount, transaction_date, status)
VALUES ('A001', 'B002', 1000, CURRENT_TIMESTAMP, 'SUCCESS');
COMMIT;

-- 转账：B002 → A001（同样按升序：先 A001，再 B002）
BEGIN;
UPDATE accounts SET balance = balance + 500 WHERE account_id = 'A001'; -- 先锁小
UPDATE accounts SET balance = balance - 500 WHERE account_id = 'B002'; -- 后锁大
INSERT INTO transactions (from_account, to_account, amount, transaction_date, status)
VALUES ('B002', 'A001', 500, CURRENT_TIMESTAMP, 'SUCCESS');
COMMIT;
```

### 根因2：配置 lock_timeout 快速失败（辅助优化）

**描述**：为事务设置 lock_timeout，在锁等待超时时立即报错返回，避免长时间挂起；同时调短 deadlock_timeout 加快死锁检测。

**具体命令**：

```sql
-- 会话级锁超时（在应用连接初始化时设置）
SET lock_timeout = '5s';

-- 加快死锁检测（全局）
ALTER SYSTEM SET deadlock_timeout = '500ms';
SELECT pg_reload_conf();

-- 开启锁等待日志，便于分析
ALTER SYSTEM SET log_lock_waits = on;
SELECT pg_reload_conf();
```

### 根因3：使用 SELECT FOR UPDATE 预锁（完整修复）

**描述**：在事务开始时用 SELECT FOR UPDATE 显式按统一顺序预先获取所有需要的行锁，防止后续 UPDATE 时形成循环等待；结合 NOWAIT 或 SKIP LOCKED 实现快速失败或跳过。

**具体命令**：

```sql
-- 预锁方式：事务开始时按升序锁定所有涉及账户
BEGIN;
-- 按 account_id 升序同时锁定两行
SELECT account_id, balance
FROM accounts
WHERE account_id IN ('A001', 'B002')
ORDER BY account_id
FOR UPDATE;

-- 检查余额
-- 执行转账更新
UPDATE accounts SET balance = balance - 1000 WHERE account_id = 'A001';
UPDATE accounts SET balance = balance + 1000 WHERE account_id = 'B002';
INSERT INTO transactions (from_account, to_account, amount, transaction_date, status)
VALUES ('A001', 'B002', 1000, CURRENT_TIMESTAMP, 'SUCCESS');
COMMIT;
```
