---
id: wait_event_analysis
name: Wait Event Analysis
category: wait_events
version: '1.0'
description: 'Diagnose database performance using wait event sampling from pg_stat_activity.
  Each wait event type indicates a specific resource bottleneck.

  '
symptoms:
- High CPU but queries seem fast
- Intermittent slowdowns not tied to a single query
- Database feels slow overall without obvious cause
- Monitoring shows high wait_event counts
keywords:
- wait event
- wait_event
- 等待事件
- LWLock
- Lock
- IO
- ClientRead
- BufferMapping
- WALInsert
triggers:
- wait_status
- LWLock.*Buffer
- Lock.*relation
evidence_required:
- wait_events
evidence_optional:
- system_view
- metric
diagnosis_steps:
- step: 1
  action: Collect wait event snapshot
  tool: execute_sql
  expected_evidence: wait_events
- step: 2
  action: Collect active sessions for context
  tool: execute_sql
  expected_evidence: system_view
root_causes:
- id: io_wait
  description: Disk I/O bottleneck — sessions waiting for data to be read
  probability: 0.5
  indicators:
  - wait_status = IO
  - DataFileRead or DataFileWrite
  recommendations:
  - Increase shared_buffers to cache more data
  - Check disk I/O throughput with iostat
  - Consider SSD storage or I/O acceleration
  config_fixes:
  - shared_buffers = 25% of RAM
  - effective_cache_size = 75% of RAM
- id: lwlock_buffer_mapping
  description: High LWLock contention on buffer mapping — hot blocks
  probability: 0.4
  indicators:
  - LWLock BufferMapping
  - LWLock buffer_mapping
  recommendations:
  - Identify hot tables and partition them
  - Increase shared_buffers
  - Reduce sequential scan frequency with indexes
- id: wal_lock
  description: WAL insert lock contention — high write throughput
  probability: 0.35
  indicators:
  - LWLock WALInsert
  - LWLock WALBufMapping
  recommendations:
  - Increase wal_buffers
  - Check commit_delay and commit_siblings settings
  - Consider synchronous_commit = off for non-critical workloads
  config_fixes:
  - wal_buffers = 64MB
  - checkpoint_completion_target = 0.9
- id: client_read
  description: Sessions waiting for client to send data or read results
  probability: 0.25
  indicators:
  - wait_status = ClientRead
  - Many sessions in state 'idle'
  recommendations:
  - Check for application connection pool exhaustion
  - Tune application query batching
references:
- PostgreSQL wait event documentation
- GaussDB performance monitoring guide
---

## 根因

Disk I/O bottleneck — sessions waiting for data to be read；High LWLock contention on buffer mapping — hot blocks；WAL insert lock contention — high write throughput

## 诊断步骤

### 步骤 1

**操作**：Collect wait event snapshot

- 工具：`execute_sql`
- 收集：wait_events

```sql
SELECT wait_status, COUNT(*) AS session_count,
       ARRAY_AGG(pid) AS pids
FROM pg_stat_activity
WHERE wait_status IS NOT NULL AND pid <> pg_backend_pid()
GROUP BY wait_status
ORDER BY session_count DESC;
```

---

### 步骤 2

**操作**：Collect active sessions for context

- 工具：`execute_sql`
- 收集：system_view

```sql
SELECT pid, usename, datname, state, wait_status,
       ROUND(EXTRACT(EPOCH FROM (now() - query_start))::numeric, 2) AS duration_sec,
       LEFT(query, 200) AS query_preview
FROM pg_stat_activity
WHERE pid <> pg_backend_pid() AND state = 'active'
ORDER BY duration_sec DESC NULLS LAST
LIMIT 50;
```

---

## 恢复手段

### 根因1：Disk I/O bottleneck — sessions waiting for data to be read（概率 50%）

**描述**：Increase shared_buffers to cache more data; Check disk I/O throughput with iostat; Consider SSD storage or I/O acceleration

**判断依据**：
- wait_status = IO
- DataFileRead or DataFileWrite

**具体命令**：

```sql
shared_buffers = 25% of RAM
effective_cache_size = 75% of RAM
```

### 根因2：High LWLock contention on buffer mapping — hot blocks（概率 40%）

**描述**：Identify hot tables and partition them; Increase shared_buffers; Reduce sequential scan frequency with indexes

**判断依据**：
- LWLock BufferMapping
- LWLock buffer_mapping

### 根因3：WAL insert lock contention — high write throughput（概率 35%）

**描述**：Increase wal_buffers; Check commit_delay and commit_siblings settings; Consider synchronous_commit = off for non-critical workloads

**判断依据**：
- LWLock WALInsert
- LWLock WALBufMapping

**具体命令**：

```sql
wal_buffers = 64MB
checkpoint_completion_target = 0.9
```

### 根因4：Sessions waiting for client to send data or read results（概率 25%）

**描述**：Check for application connection pool exhaustion; Tune application query batching

**判断依据**：
- wait_status = ClientRead
- Many sessions in state 'idle'
