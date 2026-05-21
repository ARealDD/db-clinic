---
id: improper_join_order
name: "慢SQL - 多表 JOIN 顺序不当导致中间结果集膨胀"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "多表 JOIN 顺序未以最小结果集的表驱动，产生大量中间结果，导致 CPU 和 I/O 开销倍增"

symptoms:
  - "订单详情等多表 JOIN 查询响应超过4秒"
  - "执行计划中间节点 rows 估算远大于最终结果"
  - "高并发下系统 CPU 持续高位"
  - "相同表的单表查询速度正常，JOIN 后性能骤降"

keywords:
  - join顺序
  - join order
  - 中间结果集
  - 多表join
  - 执行计划
  - Hash Join
  - Nested Loop
  - 驱动表

triggers:
  - "Hash Join"
  - "Nested Loop"
  - "duration:\\s*\\d{4,} ms"

evidence_required:
  - sql_text
  - explain_plan
evidence_optional:
  - table_stats
  - index_info

---

## 根因

多表 JOIN 时，如果先 JOIN 大表（如 customers、order_items），会产生庞大的中间结果集，后续每一步 JOIN 都要在大结果集上操作。正确做法是以已过滤至最小行数的表（如按主键定位的 orders 行）为驱动，优先 JOIN 能快速缩减结果集的表，避免中间结果膨胀。

## 诊断步骤

### 步骤 1

**操作**：查看多表 JOIN 慢查询

- 工具：`execute_sql`
- 条件：页面加载慢或接口超时
- 执行语句：

```sql
SELECT pid, state, query_start, now() - query_start AS duration, query
FROM pg_stat_activity
WHERE state = 'active'
ORDER BY duration DESC
LIMIT 10;
```

**现象**：`pg_stat_activity` 中出现含多个 JOIN 的查询持续活跃，duration 超过 4s。

**现象分析**：多表 JOIN 性能问题通常由 JOIN 顺序不当或外键索引缺失引起，需结合执行计划分析。

---
### 步骤 2

**操作**：获取 SQL 执行计划，分析各 JOIN 节点的 rows

- 工具：`execute_sql`
- 条件：已收集 sql_text
- 执行语句：

```sql
EXPLAIN ANALYZE
<slow_sql>
```

**现象**：执行计划中某些 Hash Join/Nested Loop 节点的 rows 估算远大于预期，说明中间结果集过大。

**现象分析**：中间结果集膨胀是 JOIN 顺序不当的直接证据，需将过滤最严格的表提前 JOIN。

---
### 步骤 3

**操作**：检查所有 JOIN 字段的索引覆盖情况

- 工具：`execute_sql`
- 条件：已确认 JOIN 性能问题
- 执行语句：

```sql
SELECT * FROM pg_stat_user_indexes
WHERE relname IN ('<target_table_1>', '<target_table_2>', '<target_table_3>', '<target_table_4>', '<target_table_5>');
SELECT * FROM pg_indexes
WHERE tablename IN ('<target_table_1>', '<target_table_2>', '<target_table_3>', '<target_table_4>', '<target_table_5>');
```

**现象**：order_items、shipping_info 等表的外键字段（order_id、product_id）可能缺少索引。

**现象分析**：外键索引缺失会导致 JOIN 时必须全表扫描关联表，是 JOIN 慢查询的常见根因之一。

---
## 恢复手段

### 根因1：为 JOIN 外键字段创建索引（主要修复）

**描述**：确保所有参与 JOIN 的外键字段均有索引，使每次 JOIN 可以走 Index Scan 而非 Seq Scan。

**具体命令**：

```sql
CREATE INDEX CONCURRENTLY <index_name>   ON <target_table>(<filter_col>);
CREATE INDEX CONCURRENTLY <index_name> ON <target_table>(<filter_col>);
CREATE INDEX CONCURRENTLY <index_name> ON <target_table>(<filter_col>);
CREATE INDEX CONCURRENTLY <index_name>     ON <target_table>(<filter_col>);
```

### 根因2：使用 CTE 先过滤驱动表（辅助优化）

**描述**：用 CTE 或子查询先定位订单主键行，再依次 JOIN 其他表，让优化器以最小结果集为起点构建执行计划。

**具体命令**：

```sql
WITH order_base AS (
    SELECT order_id, order_status, total_amount, payment_method, create_time, customer_id
    FROM <target_table>
    WHERE order_id = 123456
)
SELECT ob.order_id, ob.order_status, ob.total_amount, ob.payment_method, ob.create_time,
       c.customer_name, c.phone, c.email,
       i.item_id, p.product_name, i.quantity, i.unit_price, i.subtotal,
       s.shipping_method, s.tracking_number, s.shipping_address, s.status AS shipping_status
FROM <target_table_2> ob
JOIN <join_table_2> i   ON ob.order_id = i.order_id
JOIN <join_table_3> p      ON i.product_id = p.product_id
JOIN <join_table_4> c     ON ob.customer_id = c.customer_id
JOIN <join_table_5> s ON ob.order_id = s.order_id;
```

### 根因3：添加覆盖索引减少回表（完整修复）

**描述**：对高频 JOIN 查询涉及的列组合添加覆盖索引，让各表可走 Index Only Scan，消除回表开销。

**具体命令**：

```sql
CREATE INDEX CONCURRENTLY <index_name>
    ON <target_table>(<filter_col>, <groupby_col>, <join_col>, <order_col>, <col_5>, <col_6>);
CREATE INDEX CONCURRENTLY <index_name>
    ON <target_table>(<filter_col>, <groupby_col>, <join_col>, <order_col>, <col_5>);
ANALYZE <target_table>; ANALYZE <target_table>; ANALYZE <target_table>;
```
