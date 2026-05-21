---
id: like_leading_wildcard
name: "慢SQL - LIKE '%关键词%' 前导通配符导致全表扫描"
category: slow_sql
version: "1.0"
author: "运维团队"
description: "商品搜索使用 LIKE '%keyword%' 前导通配符，B-tree 索引完全失效，大表全表扫描导致搜索超时"

symptoms:
  - "商品搜索响应时间超过5秒"
  - "执行计划显示 Seq Scan，查询中含 LIKE '%...%'"
  - "搜索高峰期 CPU 使用率 90% 以上"
  - "商品数量增加后搜索性能持续劣化"

keywords:
  - like
  - 前导通配符
  - leading wildcard
  - 全文搜索
  - full text search
  - tsvector
  - gin index
  - 模糊匹配
  - 全表扫描

triggers:
  - "Seq Scan on products"
  - "Filter:.*LIKE"
  - "duration:\\s*\\d{4,} ms"

evidence_required:
  - sql_text
  - explain_plan
evidence_optional:
  - index_info
  - table_stats

---

## 根因

`LIKE '%关键词%'` 以通配符 `%` 开头，PostgreSQL 的 B-tree 索引是有序前缀索引，无法在前缀未知的情况下定位入口点，因此完全跳过索引进行全表行级字符串匹配。同时 OR 连接多个 LIKE 条件使情况更糟。对于中文全文搜索，应使用 GIN 全文索引（`tsvector`）替代。

## 诊断步骤

### 步骤 1

**操作**：查看包含 LIKE 的慢查询

- 工具：`execute_sql`
- 条件：搜索功能响应慢

**现象**：`pg_stat_activity` 中出现含 `LIKE '%...%'` 的查询持续活跃，duration 超过 5s。

**现象分析**：前导通配符 LIKE 查询是全表扫描的常见原因，需通过执行计划确认并评估全文搜索方案。

```sql
SELECT pid, state, query_start, now() - query_start AS duration, query
FROM pg_stat_activity
WHERE state = 'active'
  AND query LIKE '%LIKE%'
ORDER BY duration DESC
LIMIT 10;
```

---

### 步骤 2

**操作**：获取 LIKE 查询执行计划

- 工具：`execute_sql`
- 条件：已收集 sql_text

**现象**：执行计划显示 `Seq Scan on products`，Filter 行包含 `like` 操作，无任何 Index Scan。

**现象分析**：前导 `%` 使 B-tree 索引完全失效，必须全表扫描后逐行做字符串匹配，OR 条件使扫描翻倍。

```sql
EXPLAIN ANALYZE
SELECT p.product_id, p.product_name, p.description, p.price, p.stock, p.status,
       c.category_name
FROM products p
JOIN categories c ON p.category_id = c.category_id
WHERE p.product_name LIKE '%手机%'
   OR p.description LIKE '%手机%'
ORDER BY p.price ASC
LIMIT 100;
```

---

### 步骤 3

**操作**：检查 products 表是否有全文搜索索引

- 工具：`execute_sql`
- 条件：已确认全表扫描

**现象**：`pg_indexes` 中 products 表只有 B-tree 索引，无 GIN 全文索引，`to_tsvector` 列也未建立。

**现象分析**：缺少 GIN 全文索引是根本原因，需根据文本搜索需求建立对应索引类型。

```sql
SELECT * FROM pg_indexes WHERE tablename = 'products';
SELECT * FROM pg_stat_user_indexes WHERE relname = 'products';
```

---

## 恢复手段

### 根因1：使用 GIN 全文索引替代 LIKE（主要修复）

**描述**：为 product_name 和 description 建立 GIN 全文索引，使用 `@@` 操作符替代 `LIKE`，搜索效率从全表扫描提升至索引扫描。

**具体命令**：

```sql
-- 创建 GIN 全文索引（中文需安装 zhparser 等分词扩展）
CREATE INDEX CONCURRENTLY idx_products_search
    ON products USING GIN(to_tsvector('simple', product_name || ' ' || description));

-- 使用全文搜索查询
SELECT p.product_id, p.product_name, p.description, p.price, p.stock, p.status,
       c.category_name
FROM products p
JOIN categories c ON p.category_id = c.category_id
WHERE to_tsvector('simple', p.product_name || ' ' || p.description)
      @@ plainto_tsquery('simple', '手机')
ORDER BY p.price ASC
LIMIT 100;
```

### 根因2：改为前缀匹配 LIKE 'keyword%'（有限场景）

**描述**：如果搜索词总是出现在字段起始位置（如品牌前缀），改为后缀通配符的 `LIKE 'keyword%'`，B-tree 索引可以正常使用。

**具体命令**：

```sql
-- 前缀匹配可以使用索引
CREATE INDEX CONCURRENTLY idx_products_name ON products(product_name);

EXPLAIN ANALYZE
SELECT product_id, product_name, price
FROM products
WHERE product_name LIKE '苹果%'
ORDER BY price ASC
LIMIT 100;
```

### 根因3：使用 pg_trgm 扩展支持任意位置模糊匹配（完整修复）

**描述**：安装 `pg_trgm` 扩展并在文本字段上建立 GIN Trigram 索引，支持 `LIKE '%keyword%'` 走索引扫描。

**具体命令**：

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX CONCURRENTLY idx_products_name_trgm
    ON products USING GIN(product_name gin_trgm_ops);

-- 原始 LIKE 查询此时可以走 GIN Trigram 索引
EXPLAIN ANALYZE
SELECT product_id, product_name, price
FROM products
WHERE product_name LIKE '%手机%'
LIMIT 100;
```
