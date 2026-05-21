---
id: slow_sql_nestloop
name: "慢SQL - NESTLOOP关联查询性能问题"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "三表关联查询中NESTLOOP算子导致的慢SQL问题诊断与修复"

symptoms:
  - "多表关联查询执行时间过长"
  - "查询计划中出现大表 NESTLOOP"
  - "关联查询 CPU 占用高"
  - "原本较快的关联查询突然变慢"

keywords:
  - nestloop
  - nested loop
  - 关联查询
  - join慢
  - 慢查询
  - slow sql
  - 多表关联
triggers:
  - "Nested Loop"
  - "NESTLOOP"
  - "duration:\\s*\\d{4,} ms"

evidence_required:
  - sql_text
  - explain_plan
evidence_optional:
  - table_stats
  - wait_events
---

## 根因

SQL 为三表关联查询，查询计划中 c 表与 b 表通过 NESTLOOP 做两表 join，在大数据量下时间复杂度为 O(n²)，导致执行耗时急剧上升，是本次慢 SQL 的根本原因。

## 诊断步骤

### 步骤 1

**操作**：获取慢 SQL 文本

- 工具：`analyze_sql`
- 条件：用户已提供 SQL 文本或可从慢查询日志获取

**现象**：获取到完整 SQL，为三表关联查询（a JOIN b JOIN c），关联字段分别为 a.id=b.a_id、b.id=c.b_id。

**现象分析**：SQL 涉及三表关联，结构较复杂，需进一步分析执行计划以定位性能瓶颈。

---

### 步骤 2

**操作**：获取 SQL 执行计划

- 工具：`execute_sql`
- 条件：已收集 sql_text
- 执行语句：

```sql
EXPLAIN (ANALYZE, VERBOSE, BUFFERS, FORMAT TEXT)
<slow_sql>
```

**现象**：执行计划显示关联路径中存在 NESTLOOP（历史信息显示 c 表与 b 表通过 NESTLOOP 做两表 join），外表 b 数据量约 100 万行，内表 c 约 500 万行。

**现象分析**：NESTLOOP 算子在大数据量场景下时间复杂度为 O(n×m)，当前外表×内表组合将产生约 5×10¹² 次扫描，为主要耗时瓶颈，需确认是否存在更优的 Hash Join 路径。

---
### 步骤 3

**操作**：分析执行计划详情

- 工具：`analyze_execution_plan`
- 条件：已收集 explain_plan

**现象**：analyze_execution_plan 分析显示 NESTLOOP 节点实际执行耗时 45 秒，占总耗时的 92%；内表 c 上的索引扫描命中率低（index_scan_ratio < 0.01），退化为顺序扫描。

**现象分析**：确认 NESTLOOP 为根因。内表 c 在关联列上缺少高选择性索引，导致每次外表驱动行都触发内表顺序扫描，应引导优化器选择 Hash Join 以降低时间复杂度为 O(n+m)。

---

### 步骤 4

**操作**：检查关联列索引覆盖情况

- 工具：`execute_sql`
- 条件：analyze_execution_plan 确认内表为顺序扫描
- 执行语句：

```sql
SELECT schemaname, relname AS table_name, indexrelname AS index_name,
       idx_scan AS index_scans, idx_tup_read AS tuples_read, idx_tup_fetch AS tuples_fetched
FROM pg_stat_user_indexes
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY idx_scan ASC, relname
LIMIT 30;
```

**现象**：pg_stat_user_indexes 显示 c 表关联列 c.b_id 上无任何索引，pg_stat_user_tables 显示 c 表 seq_scan 计数持续增长。

**现象分析**：缺少关联列索引是导致 NESTLOOP 内表退化为顺序扫描的直接原因。创建索引或改用 Hash Join 均可改善性能，需根据业务读写比决策。

---
## 恢复手段

### 根因1：NESTLOOP 算子选择不当（短期恢复）

**描述**：通过会话级或数据库级参数关闭 NESTLOOP，引导优化器选择 Hash Join，快速恢复查询性能。操作分两步：先在会话中测试效果，确认后再数据库级固化。

**具体命令**：

```sql
-- 第一步：会话级测试，不影响其他连接
SET enable_nestloop = off;

-- 第二步：执行原 SQL，验证执行计划已变更为 Hash Join
EXPLAIN (ANALYZE, BUFFERS)
<slow_sql>
```

### 根因2：关联列缺少索引（根本修复）

**描述**：在内表关联列上创建索引，使 NESTLOOP 内表扫描从顺序扫描变为索引扫描，同时也为后续查询提供长期收益。使用 CONCURRENTLY 避免锁表。

**具体命令**：

```sql
-- 在内表关联列上创建索引（不锁表）
CREATE INDEX CONCURRENTLY idx_c_b_id ON c(b_id);

-- 创建完成后更新统计信息
ANALYZE c;

-- 验证索引被使用（执行计划应显示 Index Scan）
EXPLAIN SELECT ...;  -- 替换为实际慢 SQL
<slow_sql>
```

### 根因3：统计信息陈旧导致优化器误判（辅助修复）

**描述**：若关联表统计信息陈旧，优化器可能错误估算行数而选择 NESTLOOP。更新统计信息后优化器可能自动选择更优路径。

**具体命令**：

```sql
-- 更新所有关联表的统计信息
ANALYZE a;
ANALYZE b;
ANALYZE c;

-- 若死元组过多，先执行 VACUUM
VACUUM ANALYZE a;
VACUUM ANALYZE b;
VACUUM ANALYZE c;

-- 重新观察执行计划
EXPLAIN SELECT ...;  -- 替换为实际慢 SQL
<slow_sql>
```
