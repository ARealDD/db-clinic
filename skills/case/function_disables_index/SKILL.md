---
id: function_disables_index
name: "慢SQL - WHERE 条件中使用函数导致索引失效"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "WHERE 子句中对索引列应用 UPPER()、LOWER()、DATE() 等函数，导致 B-tree 索引无法使用，触发全表扫描"

symptoms:
  - "查询响应时间超过3秒，表上有索引但未被使用"
  - "执行计划显示 Seq Scan，尽管查询字段有索引"
  - "库存或业务查询高峰期 CPU 升高"
  - "对相同表的不含函数查询速度正常"

keywords:
  - 函数索引失效
  - UPPER LOWER
  - function on indexed column
  - 索引失效
  - 全表扫描
  - seq scan
  - 函数式索引
  - 大小写不敏感

triggers:
  - "Seq Scan on warehouses"
  - "Seq Scan on"
  - "Filter:.*upper\\("
  - "Filter:.*lower\\("
  - "duration:\\s*\\d{3,} ms"

evidence_required:
  - sql_text
  - explain_plan
evidence_optional:
  - index_info

---

## 根因

在 WHERE 条件中对已建索引的列使用 `UPPER(warehouse_name)` 等函数，PostgreSQL 无法将函数结果映射到原始 B-tree 索引，只能对每行计算函数后与常量比较，即全表扫描。索引是针对原始列值构建的，函数改变了值域，导致索引失效。解决方法：去除 WHERE 中的函数调用，或建立函数式索引。

## 诊断步骤

### 步骤 1

**操作**：查看相关慢查询

- 工具：`execute_sql`
- 条件：业务查询响应慢，表上已有索引

**现象**：`pg_stat_activity` 中出现查询活跃时间异常，查询 WHERE 中包含 UPPER/LOWER 等函数调用。

**现象分析**：有索引却还慢，首先怀疑函数导致的索引失效，需通过执行计划确认。

```sql
SELECT pid, state, query_start, now() - query_start AS duration, query
FROM pg_stat_activity
WHERE state = 'active'
ORDER BY duration DESC
LIMIT 10;
```

---

### 步骤 2

**操作**：获取 SQL 执行计划，确认全表扫描原因

- 工具：`execute_sql`
- 条件：已收集 sql_text

```sql
-- 将 <your_sql_here> 替换为实际的慢查询语句
EXPLAIN (ANALYZE, VERBOSE, BUFFERS, FORMAT TEXT)
<your_sql_here>;
```

**现象**：执行计划显示 `Seq Scan on warehouses`，Filter 行包含 `upper(warehouse_name::text)`，无 Index Scan。

**现象分析**：`UPPER()` 函数阻止了索引使用，优化器无法利用 warehouse_name 上的 B-tree 索引。

```sql
EXPLAIN ANALYZE
SELECT i.inventory_id, i.product_id, i.warehouse_id, i.sku_id,
       i.quantity, i.reserved_quantity, i.available_quantity,
       i.last_update_time
FROM inventory i
JOIN warehouses w ON i.warehouse_id = w.warehouse_id
WHERE i.product_id = 12345
  AND UPPER(w.warehouse_name) = 'BEIJING_WAREHOUSE';
```

---

### 步骤 3

**操作**：检查 warehouses 表的索引情况

- 工具：`execute_sql`
- 条件：已确认 Seq Scan

```sql
SELECT schemaname, relname AS table_name, indexrelname AS index_name,
       idx_scan AS index_scans, idx_tup_read AS tuples_read, idx_tup_fetch AS tuples_fetched
FROM pg_stat_user_indexes
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY idx_scan ASC, relname
LIMIT 30;
```

**现象**：warehouses 表有 warehouse_name 普通索引，但由于函数调用，idx_scan 计数为 0。

**现象分析**：索引存在但 idx_scan=0 是函数导致索引失效的典型特征，需建函数式索引或改写 SQL。

```sql
SELECT * FROM pg_stat_user_indexes WHERE relname = 'warehouses';
SELECT * FROM pg_indexes WHERE tablename = 'warehouses';
```

---

## 恢复手段

### 根因1：去除 WHERE 中的函数调用（主要修复）

**描述**：将查询条件改为直接与字符串字面量比较（统一大小写存储规范），让 B-tree 索引正常工作。

**具体命令**：

```sql
-- 修复后的 SQL（去除 UPPER 函数）
SELECT i.inventory_id, i.product_id, i.warehouse_id, i.sku_id,
       i.quantity, i.reserved_quantity, i.available_quantity,
       i.last_update_time
FROM inventory i
JOIN warehouses w ON i.warehouse_id = w.warehouse_id
WHERE i.product_id = 12345
  AND w.warehouse_name = 'BEIJING_WAREHOUSE';

-- 确保 inventory.product_id 有索引
CREATE INDEX CONCURRENTLY idx_inventory_product_id ON inventory(product_id);
-- 确保 warehouses.warehouse_name 有索引
CREATE INDEX CONCURRENTLY idx_warehouses_warehouse_name ON warehouses(warehouse_name);
```

### 根因2：创建函数式索引（大小写不敏感场景）

**描述**：如果业务必须支持大小写不敏感查询，在 `UPPER(warehouse_name)` 上建立函数式索引，使含 UPPER 的 WHERE 条件也能走索引。

**具体命令**：

```sql
-- 方法1：创建函数式索引（推荐，无需改表结构）
CREATE INDEX CONCURRENTLY idx_warehouses_name_upper
    ON warehouses(UPPER(warehouse_name));

-- 方法2：使用 citext 扩展（大小写不敏感类型）
CREATE EXTENSION IF NOT EXISTS citext;
ALTER TABLE warehouses ALTER COLUMN warehouse_name TYPE citext;

-- 验证函数式索引生效
EXPLAIN ANALYZE
SELECT warehouse_id, warehouse_name
FROM warehouses
WHERE UPPER(warehouse_name) = 'BEIJING_WAREHOUSE';
```
