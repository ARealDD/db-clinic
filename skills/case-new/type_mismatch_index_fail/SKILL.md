---
id: type_mismatch_index_fail
name: "慢SQL - 数据类型不匹配导致隐式转换使索引失效"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "VARCHAR 类型字段（user_id, phone）使用整数字面量查询，PostgreSQL 对表中每行执行隐式类型转换（行值→整数），导致 B-tree 索引完全失效，强制全表扫描"

symptoms:
  - "有索引的字段查询响应超过1秒，执行计划显示 Seq Scan"
  - "执行计划中出现 Filter: (user_id = 123456) 而非 Index Cond"
  - "使用字符串字面量查询同字段时瞬间完成"
  - "idx_scan 统计为 0，但表上确有索引"

keywords:
  - 数据类型不匹配
  - 隐式转换
  - type mismatch
  - VARCHAR
  - 索引失效
  - implicit cast
  - 字符串索引
  - 类型转换
  - 参数化查询

triggers:
  - "Filter:.*::integer"
  - "Seq Scan on users"
  - "implicit.*cast"
  - "duration:\\s*\\d{3,} ms"

evidence_required:
  - sql_text
  - explain_plan
  - index_info
evidence_optional:
  - table_stats

---

## 根因

`users.user_id` 字段类型为 VARCHAR，索引 `idx_user_id` 基于 VARCHAR 构建。当查询使用整数字面量 `WHERE user_id = 123456` 时，PostgreSQL 会将 VARCHAR 字段中的每一行值转换为整数（而非将字面量转换为字符串），转换操作作用于索引键，导致索引无法按原始键值匹配。优化器因此放弃索引，改为全表扫描 + 行级转换，处理百万行时性能急剧下降。修复方法是查询时使用字符串字面量（加引号）确保类型匹配。

## 诊断步骤

### 步骤 1

**操作**：对比两种写法的执行计划

- 工具：`execute_sql`
- 条件：有索引但查询走全表扫描
- 执行语句：

```sql
EXPLAIN (ANALYZE, VERBOSE, BUFFERS, FORMAT TEXT)
<slow_sql>
```

```sql
-- 整数字面量（索引失效）
EXPLAIN ANALYZE
<slow_sql>
```

**现象**：`WHERE user_id = 123456`（整数）执行计划显示 `Seq Scan on users`，Filter 节点应用类型转换；而 `WHERE user_id = '123456'`（字符串）执行计划显示 `Index Scan using idx_user_id`，执行时间从秒级降至毫秒级。

**现象分析**：两种写法执行时间相差数百倍，确认是类型不匹配导致索引失效。

---
### 步骤 2

**操作**：确认字段实际类型和索引定义

- 工具：`execute_sql`
- 条件：已确认整数字面量导致全表扫描
- 执行语句：

```sql
SELECT schemaname, relname AS table_name, indexrelname AS index_name,
       idx_scan AS index_scans, idx_tup_read AS tuples_read, idx_tup_fetch AS tuples_fetched
FROM pg_stat_user_indexes
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY idx_scan ASC, relname
LIMIT 30;
```

```sql
-- 检查字段类型
SELECT column_name, data_type, character_maximum_length
FROM information_schema.columns
WHERE table_name = 'users'
  AND column_name IN ('user_id', 'phone');

-- 检查索引定义
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename = '<target_table>';

-- 检查索引使用统计
SELECT indexrelname, idx_scan, idx_tup_read, idx_tup_fetch
FROM pg_stat_user_indexes
WHERE tablename = '<target_table>';
```

**现象**：`information_schema.columns` 显示 user_id 和 phone 为 `character varying`；`pg_indexes` 显示对应索引存在且定义正常；`pg_stat_user_indexes` 中 idx_scan 接近0，说明索引一直未被使用。

**现象分析**：索引存在但 idx_scan=0 是类型不匹配导致索引始终未命中的典型特征，无需重建索引，只需修正查询写法。

---
### 步骤 3

**操作**：检查应用层查询是否使用参数化查询

- 工具：`execute_sql`
- 条件：已确认类型不匹配根因
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
-- 查看慢查询记录中的 user_id 相关查询
SELECT query, calls, total_exec_time, mean_exec_time
FROM dbe_perf.statement_history
WHERE query LIKE '%user_id%' OR query LIKE '%phone%'
ORDER BY mean_exec_time DESC
LIMIT 10;

-- 查看表的全表扫描次数（应接近0）
SELECT relname, seq_scan, idx_scan
FROM pg_stat_user_tables
WHERE relname = '<target_table>';
```

**现象**：`dbe_perf.statement_history` 中可见大量 `WHERE user_id = $1` 但参数类型为整数的查询（或直接拼接整数的 SQL），说明应用层未正确处理数据类型。

**现象分析**：类型问题通常来源于应用代码，需同时修正 SQL 写法和应用层参数绑定，确保传入的参数为字符串类型。

---
## 恢复手段

### 根因1：修正查询使用字符串字面量（主要修复）

**描述**：将查询中的整数字面量改为字符串字面量（加引号），确保类型与 VARCHAR 字段匹配，使 B-tree 索引立即生效，无需任何数据库改动。

**具体命令**：

```sql
-- 修正 user_id 查询（VARCHAR 字段用字符串字面量）
SELECT user_id, username, email, phone, status
FROM users
WHERE user_id = '123456';   -- 加引号

-- 修正 phone 查询
SELECT user_id, username, email, phone, status
FROM users
WHERE phone = '13812345678';  -- 加引号

-- 验证索引被使用（执行计划应显示 Index Scan）
EXPLAIN ANALYZE
<slow_sql>
```

### 根因2：应用层使用参数化查询并绑定正确类型（辅助优化）

**描述**：通过参数化查询（PreparedStatement/绑定参数）并明确绑定字符串类型，从根源上避免类型不匹配，同时防止 SQL 注入。

**具体命令**：

```sql
-- 应用层规范（以 Python psycopg2 为例）
-- 错误写法（拼接整数）：
--   sql = f"SELECT * FROM <target_table> WHERE user_id = {user_id}"
-- 正确写法（绑定字符串参数）：
--   sql = "SELECT * FROM <target_table> WHERE user_id = %s"
--   cursor.execute(sql, (str(<filter_col>),))

-- 验证：使用 PREPARE 测试不同参数类型
PREPARE get_user_by_id(<filter_col>) AS
    SELECT user_id, username FROM <target_table> WHERE user_id = $1;
EXECUTE get_user_by_id('123456');  -- 正确：走索引

DEALLOCATE get_user_by_id;

-- 查找 dbe_perf.statement_history 中类型不匹配的慢查询模式
SELECT query, calls, ROUND(mean_exec_time::numeric, 2) AS mean_ms
FROM dbe_perf.statement_history
WHERE (query LIKE '%user_id = %' OR query LIKE '%phone = %')
  AND mean_exec_time > 100  -- 超过 100ms 的慢查询
ORDER BY mean_exec_time DESC;
```

### 根因3：如字段设计合理可考虑修改字段类型（完整修复）

**描述**：若 user_id 实际为纯数字且无前导零需求，可将字段从 VARCHAR 改为 BIGINT，从根本上消除类型不匹配风险，并减少存储空间。此操作需评估对应用代码的影响。

**具体命令**：

```sql
-- 评估影响：检查是否有前导零等非数字特征
SELECT user_id FROM users WHERE user_id ~ '^0[0-9]' LIMIT 5;  -- 检查前导零
SELECT user_id FROM users WHERE user_id !~ '^[0-9]+$' LIMIT 5; -- 检查非纯数字

-- 如果安全，修改字段类型
ALTER TABLE users ALTER COLUMN user_id TYPE BIGINT USING user_id::BIGINT;

-- 重建索引（类型变更后自动失效）
DROP INDEX IF EXISTS idx_user_id;
CREATE INDEX CONCURRENTLY idx_user_id ON users(user_id);

-- 验证：整数字面量现在也能走索引
EXPLAIN ANALYZE SELECT * FROM users WHERE user_id = 123456
<slow_sql>
```
