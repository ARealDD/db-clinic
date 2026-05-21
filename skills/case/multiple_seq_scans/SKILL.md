---
id: multiple_seq_scans
name: "慢SQL - 多个字段缺少索引导致频繁全表扫描"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "业务表多种查询模式（名称模糊、价格范围、库存过滤）所涉及的字段均无索引，导致每次查询都需全表扫描"

symptoms:
  - "多种不同查询条件的查询响应均超过2秒"
  - "pg_stat_user_tables.seq_scan 数值持续快速增长"
  - "idx_scan 远低于 seq_scan，索引命中率极低"
  - "并发查询时 CPU 和 I/O 持续高位"

keywords:
  - 多字段缺索引
  - 频繁全表扫描
  - seq_scan
  - price index
  - stock index
  - product_name index
  - B-tree index
  - 范围查询

triggers:
  - "Seq Scan on products"
  - "seq_scan.*idx_scan"
  - "duration:\\s*\\d{3,} ms"

evidence_required:
  - sql_text
  - explain_plan
  - index_info
evidence_optional:
  - table_stats

---

## 根因

products 表存在多种查询模式（按名称模糊搜索、按价格范围过滤、按库存阈值过滤），但 price、stock 等字段均无索引。每种查询都独立触发全表扫描，高并发下多个全表扫描叠加，CPU 和 I/O 被完全占满。针对不同查询模式分别建立对应索引（B-tree 范围索引、GIN 全文索引等）是根本解决方案。

## 诊断步骤

### 步骤 1

**操作**：检查 products 表的全表扫描频率

- 工具：`execute_sql`
- 条件：多种查询响应均慢

```sql
SELECT schemaname, relname AS table_name, indexrelname AS index_name,
       idx_scan AS index_scans, idx_tup_read AS tuples_read, idx_tup_fetch AS tuples_fetched
FROM pg_stat_user_indexes
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY idx_scan ASC, relname
LIMIT 30;
```

**现象**：`pg_stat_user_tables` 中 seq_scan 数值极高，远超 idx_scan，说明大量查询走全表扫描。

**现象分析**：seq_scan >> idx_scan 是索引严重不足的典型信号，需对高频查询的 WHERE 字段逐一补充索引。

```sql
SELECT relname, seq_scan, idx_scan,
       ROUND(idx_scan::numeric / NULLIF(seq_scan + idx_scan, 0) * 100, 1) AS idx_ratio_pct
FROM pg_stat_user_tables
WHERE relname = 'products';
```

---

### 步骤 2

**操作**：获取各查询类型的执行计划

- 工具：`execute_sql`
- 条件：已识别多种慢查询

```sql
-- 将 <your_sql_here> 替换为实际的慢查询语句
EXPLAIN (ANALYZE, VERBOSE, BUFFERS, FORMAT TEXT)
<your_sql_here>;
```

**现象**：价格范围查询、库存过滤查询均显示 Seq Scan on products，Filter 行包含字段条件但无 Index 相关节点。

**现象分析**：price 和 stock 字段缺少 B-tree 索引，范围查询无法利用索引；product_name LIKE '%...%' 还需要 GIN 全文索引。

```sql
-- 价格范围查询
EXPLAIN ANALYZE
SELECT product_id, product_name, price, stock, category_id
FROM products
WHERE price BETWEEN 1000 AND 5000
ORDER BY price DESC
LIMIT 100;

-- 库存查询
EXPLAIN ANALYZE
SELECT product_id, product_name, price, stock, category_id
FROM products
WHERE stock < 10
ORDER BY stock ASC
LIMIT 100;
```

---

### 步骤 3

**操作**：确认各查询字段的索引状态

- 工具：`execute_sql`
- 条件：已确认全表扫描

```sql
SELECT schemaname, relname AS table_name, indexrelname AS index_name,
       idx_scan AS index_scans, idx_tup_read AS tuples_read, idx_tup_fetch AS tuples_fetched
FROM pg_stat_user_indexes
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY idx_scan ASC, relname
LIMIT 30;
```

**现象**：pg_indexes 中 products 表只有主键 product_id 以及 idx_category_id、idx_brand_id，price、stock、product_name 均无索引。

**现象分析**：明确需要补充的索引清单：price（B-tree）、stock（B-tree）、product_name（GIN Trigram 或全文索引）。

```sql
SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'products';
SELECT indexrelname, idx_scan FROM pg_stat_user_indexes WHERE tablename = 'products';
```

---

## 恢复手段

### 根因1：为范围查询字段创建 B-tree 索引（主要修复）

**描述**：为 price 和 stock 字段创建 B-tree 索引，支持等值、范围、排序查询，消除这两类查询的全表扫描。

**具体命令**：

```sql
-- 价格索引（支持范围查询和排序）
CREATE INDEX CONCURRENTLY idx_products_price ON products(price);

-- 库存索引（支持条件过滤和排序）
CREATE INDEX CONCURRENTLY idx_products_stock ON products(stock);

-- 常用查询组合的复合索引
CREATE INDEX CONCURRENTLY idx_products_category_price
    ON products(category_id, price);
CREATE INDEX CONCURRENTLY idx_products_brand_stock
    ON products(brand_id, stock);

-- 验证
EXPLAIN ANALYZE
SELECT product_id, product_name, price, stock FROM products
WHERE price BETWEEN 1000 AND 5000 ORDER BY price DESC LIMIT 100;
```

### 根因2：为 LIKE 模糊搜索创建 GIN Trigram 索引（辅助优化）

**描述**：安装 pg_trgm 扩展，在 product_name 上创建 GIN Trigram 索引，使 `LIKE '%...%'` 前导通配符查询也能走索引扫描。

**具体命令**：

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX CONCURRENTLY idx_products_name_trgm
    ON products USING GIN(product_name gin_trgm_ops);

-- 前导通配符 LIKE 现在可以走索引
EXPLAIN ANALYZE
SELECT product_id, product_name, price FROM products
WHERE product_name LIKE '%手机%'
LIMIT 100;
```

### 根因3：监控索引效果，持续优化（预防）

**描述**：定期检查各索引的使用率，确认新建索引已被查询利用，同时监控全表扫描比率，发现新的索引缺失场景。

**具体命令**：

```sql
-- 监控索引使用率
SELECT indexrelname,
       idx_scan,
       pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes
WHERE tablename = 'products'
ORDER BY idx_scan DESC;

-- 监控全表扫描与索引扫描比率
SELECT relname, seq_scan, idx_scan,
       ROUND(idx_scan::numeric / NULLIF(seq_scan + idx_scan, 0) * 100, 1) AS idx_pct
FROM pg_stat_user_tables
WHERE relname = 'products';
```
