---
id: order_by_random
name: "慢SQL - ORDER BY RANDOM() 强制全表扫描"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "ORDER BY RANDOM() 为每行计算随机值后全排序，无法利用任何索引，即使有 WHERE 条件也必须先扫描所有满足条件的行再排序，并发时 CPU 和 I/O 双高"

symptoms:
  - "随机推荐/随机抽样查询响应超过1秒"
  - "执行计划显示 Seq Scan + Sort，无 Index 节点"
  - "高并发时 CPU 使用率急剧升高"
  - "去掉 ORDER BY RANDOM() 后查询瞬间完成"

keywords:
  - ORDER BY RANDOM
  - 随机查询
  - 全表扫描
  - 随机推荐
  - TABLESAMPLE
  - 随机排序
  - random_value
  - 物化视图随机

triggers:
  - "Sort.*random"
  - "Seq Scan on products"
  - "order by random"
  - "duration:\\s*\\d{3,} ms"

evidence_required:
  - sql_text
  - explain_plan
evidence_optional:
  - table_stats

---

## 根因

`ORDER BY RANDOM()` 要求为结果集中每一行计算一个随机浮点数，然后按该值全量排序。由于随机值在排序前才生成，优化器无法预知行的最终排序位置，必须先扫描所有满足 WHERE 条件的行，再对全部结果排序，任何 B-tree 索引均无法加速此排序过程。百万行表每次请求都触发百万行扫描 + 百万行排序，高并发下资源消耗叠加，性能急剧下降。

## 诊断步骤

### 步骤 1

**操作**：获取随机查询的执行计划，确认全表扫描 + Sort

- 工具：`execute_sql`
- 条件：随机推荐查询响应慢

**现象**：执行计划显示 `Seq Scan on products` → `Sort (cost=... rows=...)` → `Limit`，Sort 节点的 Sort Key 包含 `random()`，无任何 Index 节点。

**现象分析**：Seq Scan + Sort on random() 是性能瓶颈，需用其他方法替代 ORDER BY RANDOM()。

```sql
EXPLAIN ANALYZE
SELECT product_id, product_name, price, stock
FROM products
WHERE category_id = 101 AND stock > 0
ORDER BY RANDOM()
LIMIT 10;
```

---

### 步骤 2

**操作**：对比不使用 RANDOM() 的执行计划

- 工具：`execute_sql`
- 条件：已确认 ORDER BY RANDOM() 是瓶颈

**现象**：去掉 ORDER BY RANDOM() 后，执行计划走 Index Scan on idx_category_id，执行时间从秒级降至毫秒级，证明索引有效，问题完全在 RANDOM() 排序。

**现象分析**：索引存在且有效，只需替换随机取样方式即可利用索引，无需建新索引。

```sql
-- 对比：不使用 RANDOM() 的查询（验证索引是否有效）
EXPLAIN ANALYZE
SELECT product_id, product_name, price, stock
FROM products
WHERE category_id = 101 AND stock > 0
LIMIT 10;

-- 查看索引使用情况
SELECT indexrelname, idx_scan
FROM pg_stat_user_indexes
WHERE tablename = 'products'
ORDER BY idx_scan DESC;
```

---

### 步骤 3

**操作**：选择合适的随机采样替代方案

- 工具：`execute_sql`
- 条件：已确认需要替换 ORDER BY RANDOM()

**现象**：TABLESAMPLE SYSTEM(N%) 直接在块级采样，绕过行级扫描，速度可提升 100 倍以上；但随机性较弱。随机 ID 范围采样借助主键索引，速度也接近索引查询。

**现象分析**：根据业务对随机均匀性的要求选择方案：高性能优先用 TABLESAMPLE；高均匀性需求考虑随机偏移或预计算 random_value 字段。

```sql
-- 测试 TABLESAMPLE 性能
EXPLAIN ANALYZE
SELECT product_id, product_name, price, stock
FROM products TABLESAMPLE SYSTEM(1)  -- 采样 1% 的数据块
WHERE category_id = 101 AND stock > 0
LIMIT 10;
```

---

## 恢复手段

### 根因1：使用 TABLESAMPLE 替代 ORDER BY RANDOM()（主要修复）

**描述**：`TABLESAMPLE SYSTEM(pct)` 基于物理块随机采样，不需要扫描所有行，性能接近 O(1)，适合对随机均匀性要求不高的推荐场景。

**具体命令**：

```sql
-- SYSTEM 方法：块级采样，速度最快（随机性较弱）
SELECT product_id, product_name, price, stock
FROM products TABLESAMPLE SYSTEM(5)  -- 采样约 5% 的数据块
WHERE category_id = 101 AND stock > 0
LIMIT 10;

-- BERNOULLI 方法：行级采样，随机性更好（速度稍慢）
SELECT product_id, product_name, price, stock
FROM products TABLESAMPLE BERNOULLI(1)  -- 每行有 1% 概率被选中
WHERE category_id = 101 AND stock > 0
LIMIT 10;
```

### 根因2：基于 ID 范围随机偏移（辅助优化）

**描述**：利用主键索引获取满足条件的 ID 范围，在范围内生成随机 ID，通过主键索引点查，全程走索引扫描，避免全表扫描。

**具体命令**：

```sql
-- 方法：在满足条件的 ID 范围内随机选择
WITH id_range AS (
    SELECT MIN(product_id) AS min_id, MAX(product_id) AS max_id
    FROM products
    WHERE category_id = 101 AND stock > 0
),
random_ids AS (
    SELECT FLOOR(RANDOM() * (max_id - min_id + 1) + min_id)::BIGINT AS random_id
    FROM id_range, generate_series(1, 30)  -- 生成 30 个候选 ID（确保有足够命中）
)
SELECT p.product_id, p.product_name, p.price, p.stock
FROM products p
JOIN random_ids r ON p.product_id = r.random_id
WHERE p.category_id = 101 AND p.stock > 0
LIMIT 10;
```

### 根因3：预计算 random_value 字段 + 物化视图（完整修复）

**描述**：在表中添加 random_value 字段并建索引，定期批量刷新，查询时通过 random_value 范围过滤替代全排序，真正利用索引实现 O(log N) 随机推荐。

**具体命令**：

```sql
-- 添加预计算随机值字段
ALTER TABLE products ADD COLUMN IF NOT EXISTS random_value DOUBLE PRECISION DEFAULT RANDOM();
CREATE INDEX CONCURRENTLY idx_products_random ON products(random_value);

-- 定期（如每小时）批量刷新随机值
UPDATE products SET random_value = RANDOM();

-- 使用随机值范围查询（利用索引）
WITH pivot AS (SELECT RANDOM() AS r)
SELECT product_id, product_name, price, stock
FROM products, pivot
WHERE category_id = 101
  AND stock > 0
  AND random_value >= pivot.r
ORDER BY random_value
LIMIT 10;
-- 如结果不足 10 条，再查 random_value < pivot.r 的部分

-- 或使用物化视图（定期刷新）
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_random_products AS
SELECT product_id, product_name, price, stock, category_id,
       RANDOM() AS random_sort
FROM products
WHERE stock > 0;

CREATE INDEX ON mv_random_products(category_id, random_sort);
REFRESH MATERIALIZED VIEW mv_random_products;  -- 定期执行

SELECT product_id, product_name, price, stock
FROM mv_random_products
WHERE category_id = 101
ORDER BY random_sort
LIMIT 10;
```
