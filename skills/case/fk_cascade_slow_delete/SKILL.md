---
id: fk_cascade_slow_delete
name: "慢SQL - 外键级联删除缺少索引导致全表扫描"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "多层外键级联 DELETE 时，子表外键列缺少索引，触发全表扫描来定位需要删除的关联行，造成删除操作超时"

symptoms:
  - "删除父表记录执行时间超过30秒"
  - "执行计划显示子表全表扫描（Seq Scan）"
  - "操作期间 CPU 和 I/O 高位"
  - "出现锁等待或连接池耗尽"

keywords:
  - 外键级联
  - cascade delete
  - 外键索引
  - foreign key index
  - 级联删除
  - 全表扫描
  - seq scan
  - 锁持有

triggers:
  - "Seq Scan on tasks"
  - "Seq Scan on task_history"
  - "duration:\\s*\\d{4,} ms"

evidence_required:
  - sql_text
  - explain_plan
  - index_info
evidence_optional:
  - table_stats

---

## 根因

执行 `DELETE FROM projects WHERE project_id = 123` 时，PostgreSQL 先通过外键约束级联删除 tasks 表中关联行，再级联删除 task_history 表。如果 tasks.project_id 和 task_history.task_id 没有索引，数据库需要全表扫描来找到需要删除的行。百万级子表的全表扫描会导致操作超时，同时持有锁阻塞其他操作。

## 诊断步骤

### 步骤 1

**操作**：查看外键约束删除的执行计划

- 工具：`execute_sql`
- 条件：删除操作缓慢

**现象**：执行计划显示在 tasks 和 task_history 表上进行 Seq Scan，Filter 为外键字段的等值条件。

**现象分析**：级联删除的子表全表扫描是性能瓶颈，说明外键列缺少索引。

```sql
EXPLAIN ANALYZE
DELETE FROM projects WHERE project_id = 123;

-- 查看外键约束定义
SELECT conname, conrelid::regclass AS child_table,
       confrelid::regclass AS parent_table,
       pg_get_constraintdef(oid) AS constraint_def
FROM pg_constraint
WHERE confrelid = 'projects'::regclass
   OR conrelid = 'tasks'::regclass;
```

---

### 步骤 2

**操作**：检查子表外键列的索引覆盖情况

- 工具：`execute_sql`
- 条件：已确认级联删除全表扫描

**现象**：`pg_indexes` 中 tasks 表没有 project_id 索引，task_history 表没有 task_id 索引，idx_scan=0。

**现象分析**：外键列缺少索引是级联删除慢的根本原因，PostgreSQL 不会自动为外键列创建索引（不同于某些其他数据库）。

```sql
SELECT indexname, indexdef FROM pg_indexes
WHERE tablename IN ('tasks', 'task_history');

SELECT relname, seq_scan, idx_scan
FROM pg_stat_user_tables
WHERE relname IN ('tasks', 'task_history');
```

---

### 步骤 3

**操作**：评估删除操作的数据量

- 工具：`execute_sql`
- 条件：准备制定恢复方案

**现象**：tasks 表中关联到 project_id=123 的行有数万条，task_history 中关联行更多。

**现象分析**：数据量大时，即使添加索引后也建议分批删除，减少单事务锁持有时间。

```sql
SELECT COUNT(*) FROM tasks WHERE project_id = 123;
SELECT COUNT(*) FROM task_history
WHERE task_id IN (SELECT task_id FROM tasks WHERE project_id = 123);
```

---

## 恢复手段

### 根因1：为外键列创建索引（主要修复）

**描述**：为所有外键列添加索引，使级联删除时的子表查找走 Index Scan 而非全表扫描，性能提升可达数十倍。

**具体命令**：

```sql
-- 为子表外键列添加索引
CREATE INDEX CONCURRENTLY idx_tasks_project_id     ON tasks(project_id);
CREATE INDEX CONCURRENTLY idx_task_history_task_id ON task_history(task_id);

-- 验证索引被使用
EXPLAIN ANALYZE DELETE FROM projects WHERE project_id = 999; -- 用测试数据验证
```

### 根因2：手动控制删除顺序避免级联（辅助优化）

**描述**：对于大量关联数据的删除，手动按层次先删子表、再删父表，并分批提交以减少锁持有时间。

**具体命令**：

```sql
BEGIN;

-- 1. 先删最深层子表（分批）
DELETE FROM task_history
WHERE task_id IN (SELECT task_id FROM tasks WHERE project_id = 123)
LIMIT 10000;
-- 重复执行直到影响行数为 0，每批提交

COMMIT;
BEGIN;

-- 2. 删中间层
DELETE FROM tasks WHERE project_id = 123;

-- 3. 删父表
DELETE FROM projects WHERE project_id = 123;

COMMIT;
```

### 根因3：使用软删除替代物理删除（完整修复）

**描述**：对于业务上可以接受的场景，改用软删除（is_deleted=true 标记），避免物理级联删除的高开销，定期在低峰期批量清理。

**具体命令**：

```sql
-- 添加软删除标记字段
ALTER TABLE projects    ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE;
ALTER TABLE tasks       ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE;
ALTER TABLE task_history ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE;

-- 软删除操作（瞬间完成）
UPDATE projects SET is_deleted = TRUE WHERE project_id = 123;
UPDATE tasks    SET is_deleted = TRUE WHERE project_id = 123;

-- 查询时过滤已删除数据
SELECT * FROM projects WHERE is_deleted = FALSE;

-- 低峰期定期物理清理（分批）
DELETE FROM task_history WHERE is_deleted = TRUE LIMIT 50000;
DELETE FROM tasks        WHERE is_deleted = TRUE LIMIT 50000;
DELETE FROM projects     WHERE is_deleted = TRUE LIMIT 50000;
```
