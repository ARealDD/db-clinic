---
id: lock_analysis
name: Lock Contention & Deadlock Analysis
category: lock
version: '1.0'
description: 'Diagnose lock contention, long lock waits, and deadlocks in GaussDB
  / openGauss. Covers table-level locks, row-level locks, advisory locks, and lock
  chains.

  '
symptoms:
- Query hanging / not completing
- Deadlock detected error
- 'ERROR: deadlock detected'
- Lock wait timeout exceeded
- Application transactions stacking up
- UPDATE/DELETE very slow despite index existing
- High number of idle-in-transaction sessions
keywords:
- lock
- deadlock
- 死锁
- 锁等待
- lock wait
- lock timeout
- blocking
- blocked
- idle in transaction
- lock contention
triggers:
- deadlock detected
- lock wait timeout
- waiting for.*lock
- blocked_by
evidence_required:
- lock_info
- system_view
evidence_optional:
- sql_text
- log_snippet
diagnosis_steps:
- step: 1
  action: Check current lock chain — who is blocking whom
  tool: execute_sql
  expected_evidence: lock_info
- step: 2
  action: Identify blocking session's query and user
  tool: execute_sql
  expected_evidence: system_view
- step: 3
  action: Parse database logs for deadlock traces
  tool: parse_db_logs
  expected_evidence: log_snippet
  condition: user has log snippet
- step: 4
  action: Identify the SQL statements involved in the deadlock cycle
  tool: analyze_sql
  condition: sql_text from lock analysis available
root_causes:
- id: idle_in_transaction
  description: Long-running idle-in-transaction session holding locks
  probability: 0.6
  indicators:
  - state = 'idle in transaction'
  - session open for > 30 seconds without executing
  - lock held by idle session
  recommendations:
  - 'Kill the idle-in-transaction session: SELECT pg_terminate_backend(<pid>);'
  - 'Set idle_in_transaction_session_timeout to auto-kill these: SET idle_in_transaction_session_timeout
    = ''30s'';'
  - Fix application to use shorter transactions and close connections properly
  config_fixes:
  - idle_in_transaction_session_timeout = '30s'  -- in gaussdb.conf
  - lock_timeout = '10s'  -- so waiting queries fail fast
  sql_fixes:
  - SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle in
    transaction' AND query_start < now() - interval '1 minute';
- id: missing_index_causing_lock_escalation
  description: Table-level lock due to missing index on DELETE/UPDATE
  probability: 0.4
  indicators:
  - ExclusiveLock on relation (whole table)
  - DELETE/UPDATE without WHERE index match
  - Seq Scan inside DELETE/UPDATE plan
  recommendations:
  - Create index on the WHERE column used in DELETE/UPDATE
  - This allows row-level locks instead of table-level locks
  sql_fixes:
  - CREATE INDEX CONCURRENTLY idx_orders_status ON orders(status);
- id: application_deadlock_cycle
  description: Application accesses tables in inconsistent order causing deadlock
    cycle
  probability: 0.5
  indicators:
  - deadlock detected in pg log
  - two or more sessions in lock cycle
  - different table access order in concurrent transactions
  recommendations:
  - Enforce consistent table access order in application code
  - Acquire all needed locks at start of transaction (SELECT ... FOR UPDATE)
  - Use advisory locks for application-level coordination
  - Reduce transaction scope to minimize lock hold time
  sql_fixes:
  - SELECT * FROM accounts WHERE id IN (1, 2) ORDER BY id FOR UPDATE;  -- consistent
    ordering
- id: hot_row_contention
  description: Multiple sessions competing to update the same row
  probability: 0.35
  indicators:
  - RowExclusiveLock on same tuple
  - Many sessions waiting for same relation+tuple
  - Counter or sequence table being updated frequently
  recommendations:
  - Batch updates or use sequence objects instead of counter tables
  - Partition the hot row (sharding by key)
  - Use SKIP LOCKED for queue-like patterns
  sql_fixes:
  - SELECT id FROM jobs WHERE status='pending' LIMIT 1 FOR UPDATE SKIP LOCKED;
references:
- GaussDB lock monitoring documentation
- PostgreSQL lock documentation
---

## 根因

Long-running idle-in-transaction session holding locks；Table-level lock due to missing index on DELETE/UPDATE；Application accesses tables in inconsistent order causing deadlock cycle

## 诊断步骤

### 步骤 1

**操作**：Check current lock chain — who is blocking whom

- 工具：`execute_sql`
- 执行语句：

```sql
SELECT kl.pid AS blocking_pid, ka.usename AS blocking_user, ka.state AS blocking_state,
       LEFT(ka.query, 100) AS blocking_query,
       bl.pid AS blocked_pid, ba.usename AS blocked_user,
       LEFT(ba.query, 100) AS blocked_query,
       bl.locktype, bl.mode,
       ROUND(EXTRACT(EPOCH FROM (now() - ba.query_start))::numeric, 2) AS blocked_duration_sec
FROM pg_locks bl
JOIN pg_locks kl
    ON kl.locktype = bl.locktype
   AND kl.database IS NOT DISTINCT FROM bl.database
   AND kl.relation IS NOT DISTINCT FROM bl.relation
   AND kl.page IS NOT DISTINCT FROM bl.page
   AND kl.tuple IS NOT DISTINCT FROM bl.tuple
   AND kl.transactionid IS NOT DISTINCT FROM bl.transactionid
   AND kl.classid IS NOT DISTINCT FROM bl.classid
   AND kl.objid IS NOT DISTINCT FROM bl.objid
   AND kl.granted = true AND bl.granted = false AND kl.pid != bl.pid
JOIN pg_stat_activity ka ON kl.pid = ka.pid
JOIN pg_stat_activity ba ON bl.pid = ba.pid
ORDER BY blocked_duration_sec DESC;
```

- 收集：lock_info

---
### 步骤 2

**操作**：Identify blocking session's query and user

- 工具：`execute_sql`
- 执行语句：

```sql
SELECT pid, usename, datname, state, wait_event_type, wait_event,
       ROUND(EXTRACT(EPOCH FROM (now() - query_start))::numeric, 2) AS duration_sec,
       LEFT(query, 200) AS query_preview
FROM pg_stat_activity
WHERE pid <> pg_backend_pid() AND state = 'active'
ORDER BY duration_sec DESC NULLS LAST
LIMIT 50;
```

- 收集：system_view

---
### 步骤 3

**操作**：Parse database logs for deadlock traces

- 工具：`parse_db_logs`
- 条件：user has log snippet
- 收集：log_snippet

---

### 步骤 4

**操作**：Identify the SQL statements involved in the deadlock cycle

- 工具：`analyze_sql`
- 条件：sql_text from lock analysis available

---

## 恢复手段

### 根因1：Long-running idle-in-transaction session holding locks（概率 60%）

**描述**：Kill the idle-in-transaction session: SELECT pg_terminate_backend(<pid>);; Set idle_in_transaction_session_timeout to auto-kill these: SET idle_in_transaction_session_timeout = '30s';; Fix application to use shorter transactions and close connections properly

**判断依据**：
- state = 'idle in transaction'
- session open for > 30 seconds without executing
- lock held by idle session

**具体命令**：

```sql
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle in transaction' AND query_start < now() - interval '1 minute';
idle_in_transaction_session_timeout = '30s'  -- in gaussdb.conf
lock_timeout = '10s'  -- so waiting queries fail fast
```

### 根因2：Application accesses tables in inconsistent order causing deadlock cycle（概率 50%）

**描述**：Enforce consistent table access order in application code; Acquire all needed locks at start of transaction (SELECT ... FOR UPDATE); Use advisory locks for application-level coordination; Reduce transaction scope to minimize lock hold time

**判断依据**：
- deadlock detected in pg log
- two or more sessions in lock cycle
- different table access order in concurrent transactions

**具体命令**：

```sql
SELECT * FROM <target_table> WHERE id IN (1, 2) ORDER BY id FOR UPDATE;  -- consistent ordering
```

### 根因3：Table-level lock due to missing index on DELETE/UPDATE（概率 40%）

**描述**：Create index on the WHERE column used in DELETE/UPDATE; This allows row-level locks instead of table-level locks

**判断依据**：
- ExclusiveLock on relation (whole table)
- DELETE/UPDATE without WHERE index match
- Seq Scan inside DELETE/UPDATE plan

**具体命令**：

```sql
CREATE INDEX CONCURRENTLY <index_name> ON <target_table>(<filter_col>);
```

### 根因4：Multiple sessions competing to update the same row（概率 35%）

**描述**：Batch updates or use sequence objects instead of counter tables; Partition the hot row (sharding by key); Use SKIP LOCKED for queue-like patterns

**判断依据**：
- RowExclusiveLock on same tuple
- Many sessions waiting for same relation+tuple
- Counter or sequence table being updated frequently

**具体命令**：

```sql
SELECT id FROM <target_table> WHERE status='pending' LIMIT 1 FOR UPDATE <target_table> LOCKED;
```
