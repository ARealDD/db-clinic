---
id: slow_query_diagnosis
name: "慢查询诊断 - 基于 GaussDB 慢 SQL 视图与执行计划的慢查询根因分析"
category: diagnose
version: "1.1"
author: "运维团队"
description: "通过 GaussDB 慢查询视图、表结构信息和执行计划，逐步定位慢查询根因，重点识别缺失索引并给出 DDL 建议"
symptoms:
  - "用户反馈某类查询响应时间明显变长"
  - "监控显示数据库整体查询延迟上升"
  - "dbe_perf.statement 中出现 avg_elapsed_time 异常高的 SQL"
  - "应用层超时报错频率增加"
keywords:
  - 慢查询
  - slow query
  - statement_history
  - dbe_perf.statement
  - EXPLAIN PERFORMANCE
  - EXPLAIN ANALYZE
  - 缺失索引
  - missing index
  - seq scan
  - 执行计划
  - query plan
  - index suggestion
triggers:
  - "Seq Scan on"
  - "avg_elapsed_time 超过阈值"
  - "查询执行时间突增"
  - "A-rows 与 E-rows 偏差大"
evidence_required:
  - sql_text
  - explain_plan
evidence_optional:
  - table_schema
  - statement_history_output
---
## 根因
慢查询通常由以下原因引起：表上缺少合适的索引导致全表扫描、统计信息陈旧导致优化器选择次优计划、JOIN 涉及的大表缺少连接键索引、或查询参数类型与列类型不匹配导致索引失效。核心诊断路径是：定位慢 SQL → 获取表结构 → 分析执行计划 → 识别全表扫描节点 → 给出索引建议。
## 诊断步骤
### 步骤 1
**目的**：获取当前数据库中执行最慢的历史查询列表
- **所需信息**：慢 SQL 列表，包含 SQL 文本、平均执行时间、调用次数、总耗时
- **信息来源**：请用户执行以下 SQL 并提供结果，或由 Agent 直接查询
**关注点**：重点关注执行时间长且调用次数较多的查询，这类查询对系统整体影响最大。优先选择 SELECT 类型的查询进行分析，跳过涉及 `pg_catalog`、`information_schema` 的系统内省查询，以及业务无关的框架表查询。
GaussDB 提供三个互补的慢 SQL 入口：
**方式一：查询历史慢 SQL（推荐，支持离线分析）**
```sql
-- 查询当前节点的历史慢 SQL（需 monadmin 或初始用户权限）
-- 前提：track_stmt_stat_level 须设为 L0 及以上
SELECT
    unique_query_id,
    query,
    start_time,
    finish_time,
    (extract(epoch from (finish_time - start_time)) * 1000)::numeric(12,2) AS elapsed_ms,
    query_plan
FROM statement_history
WHERE finish_time > now() - interval '1 hour'
ORDER BY elapsed_ms DESC
LIMIT 20;
-- 分布式部署：通过集群级函数查询所有节点的慢 SQL
SELECT *
FROM DBE_PERF.get_global_slow_sql_by_timestamp(
    now() - interval '1 hour',
    now()
)
ORDER BY finish_time - start_time DESC
LIMIT 20;
```
**方式二：查询当前实时活跃慢查询**
```sql
SELECT
    current_timestamp - query_start AS runtime,
    datname,
    usename,
    state,
    query
FROM pg_stat_activity
WHERE state != 'idle'
  AND query_start < now() - interval '5 seconds'
ORDER BY runtime DESC;
```
**方式三：查询 unique SQL 累计统计（类似 pg_stat_statements）**
```sql
-- 前提：enable_resource_track=on, instr_unique_sql_count>0
SELECT
    unique_sql_id,
    n_calls,
    round((total_elapse_time / n_calls)::numeric, 2) AS avg_elapsed_ms,
    total_elapse_time,
    query
FROM dbe_perf.statement
WHERE n_calls > 0
ORDER BY avg_elapsed_ms DESC
LIMIT 20;
```
---
### 步骤 2
**目的**：选定目标慢查询，确认其涉及的表及所属 Schema
- **所需信息**：步骤 1 中选定的 SQL 文本；目标表的 schema 归属
- **信息来源**：请用户提供该 SQL 涉及的主表名，或由 Agent 从 SQL 文本中解析表名后查询
**关注点**：一条 SQL 可能涉及多张表，优先关注数据量最大、扫描代价最高的表。需确认表所在 schema（默认为 `public`），以便后续 EXPLAIN 时传入正确的 search_path。
```sql
-- 确认表所属 schema
SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_name = '<target_table_name>';
```
---
### 步骤 3
**目的**：获取目标表的完整结构定义，包括列类型、已有索引、约束
- **所需信息**：表的列定义（列名、数据类型）；已有索引列表
- **信息来源**：请用户提供 `\d <table_name>` 输出，或由 Agent 执行以下查询
**关注点**：列的数据类型决定查询参数是否能命中索引（类型不匹配会导致隐式转换，索引失效）。已有索引列表用于判断是否真的缺索引，还是索引存在但未被使用。
```sql
-- 获取表结构
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = '<schema_name>'
  AND table_name   = '<table_name>'
ORDER BY ordinal_position;
-- 获取已有索引
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = '<schema_name>'
  AND tablename  = '<table_name>';
```
---
### 步骤 4
**目的**：对目标慢查询执行执行计划分析，获取真实运行信息
- **所需信息**：完整 SQL 文本；表的 schema；查询参数的实际值（用于替换占位符）
- **信息来源**：请用户提供实际参数值，或由 Agent 根据列数据类型自行生成合理的代入值
**关注点**：GaussDB 执行计划中重点识别以下信号：
- `Seq Scan`（全表扫描）出现在大表上 → 缺少合适索引
- `E-rows`（估算行数）与 `A-rows`（实际行数）偏差超过 10 倍 → 统计信息陈旧
- `Sort Method: external merge Disk` → work_mem 不足，查询落盘
- `Hash Batches` 大于 1 → Hash Join 内存不足，分批处理
- `loops` 值极大的嵌套循环节点 → NestLoop 在大表上重复扫描
- `Streaming (type: REDISTRIBUTE)` 频繁出现 → 分布式数据倾斜或分布键选择不合理
```sql
-- EXPLAIN ANALYZE：获取实际执行时间和行数（集中式部署推荐）
EXPLAIN ANALYZE
<替换参数后的完整 SQL>;
-- EXPLAIN PERFORMANCE：获取每个 DN 的详细执行信息（分布式部署推荐）
-- 输出包含 A-time、A-rows、E-rows、Peak Memory 等字段
EXPLAIN PERFORMANCE
<替换参数后的完整 SQL>;
```
> ⚠️ GaussDB 不支持 `EXPLAIN (BUFFERS)` 语法，勿使用 PostgreSQL 风格的 `EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)`。
---
### 步骤 5
**目的**：根据执行计划分析结果，给出索引建议或其他优化建议
- **所需信息**：步骤 4 的执行计划输出
- **信息来源**：Agent 分析执行计划后输出结论
**判断逻辑**：
| 执行计划信号 | 根因 | 建议操作 |
|---|---|---|
| 大表上出现 `Seq Scan` | WHERE/JOIN 列缺少索引 | 创建对应列的 B-tree 索引 |
| `E-rows` 与 `A-rows` 严重偏差 | 统计信息陈旧 | 执行 `ANALYZE <table>` 更新统计信息 |
| `Sort: external merge Disk` | `work_mem` 不足 | 适当增大 `work_mem`，或添加索引消除排序 |
| 多列组合过滤但单列索引 | 索引选择性不足 | 创建覆盖查询条件的复合索引 |
| 参数类型与列类型不一致 | 隐式类型转换导致索引失效 | 修正应用层参数类型，或添加函数索引 |
**索引 DDL 示例**（Agent 根据实际列名生成具体语句）：
```sql
-- 单列索引（WHERE 条件列）
-- ⚠️ CONCURRENTLY 不支持列存表、分区表、临时表
CREATE INDEX CONCURRENTLY idx_<table>_<col>
    ON <schema>.<table>(<col>);
-- 复合索引（多列组合过滤或 JOIN 键 + 过滤列）
CREATE INDEX CONCURRENTLY idx_<table>_<col1>_<col2>
    ON <schema>.<table>(<col1>, <col2>);
-- 覆盖索引（避免回表，适合高频 SELECT 少量列场景）
CREATE INDEX CONCURRENTLY idx_<table>_covering
    ON <schema>.<table>(<filter_col>) INCLUDE (<select_col1>, <select_col2>);
```
---
## 恢复手段
### 根因1：缺失索引（最常见）
**描述**：WHERE 条件列、JOIN 连接键、ORDER BY 列上缺少索引，导致优化器选择全表扫描。
**具体命令**：
```sql
-- 创建索引（CONCURRENTLY 避免锁表，生产环境推荐）
-- ⚠️ 不支持列存表、分区表、临时表，此类表需使用普通 CREATE INDEX
CREATE INDEX CONCURRENTLY idx_<table>_<column> ON <schema>.<table>(<column>);
-- 验证索引已被使用（重新执行 EXPLAIN）
EXPLAIN ANALYZE
<原 SQL（替换参数）>;
```
### 根因2：统计信息陈旧
**描述**：大批量数据写入后未及时更新统计信息，导致优化器行数估算严重偏差，选择错误执行计划。
**具体命令**：
```sql
-- 更新单表统计信息
ANALYZE <schema>.<table>;
-- 更新全库统计信息
ANALYZE;
-- 确认统计信息最后更新时间
SELECT schemaname, tablename, last_analyze, last_autoanalyze, n_live_tup, n_dead_tup
FROM pg_stat_user_tables
WHERE tablename = '<table_name>';
```
### 根因3：work_mem 不足导致落盘排序
**描述**：排序或 Hash Join 节点超出内存限制，落盘为临时文件，大幅增加执行时间。
**具体命令**：
```sql
-- 会话级临时增大（推荐，不影响其他连接）
SET work_mem = '64MB';
-- 全局持久化调整方式一：SQL 命令（SIGHUP 级参数立即生效，无需重启）
ALTER SYSTEM SET work_mem = '32MB';
-- ⚠️ GaussDB 的 ALTER SYSTEM SET 对 SIGHUP 级参数立即生效，无需调用 pg_reload_conf()
-- ⚠️ POSTMASTER 级参数（如 max_connections、shared_buffers）仍需重启数据库
-- 全局持久化调整方式二：gs_guc 工具（集群场景推荐，支持同时修改所有节点）
-- gs_guc reload -N all -I all -c "work_mem = 32MB"
```
## 注意事项
- **慢 SQL 视图权限**：`statement_history` 和 `dbe_perf.statement` 需要初始用户或 `monadmin` 权限；普通用户无法查看其他用户的 SQL 文本。
- **慢 SQL 记录前提**：`statement_history` 收录条件为执行时间超过 `log_min_duration_statement` 阈值，且 `track_stmt_stat_level` 须设为 `L0` 或以上；`dbe_perf.statement` 需 `enable_resource_track=on` 且 `instr_unique_sql_count>0`。
- **不要分析系统内省查询**：涉及 `pg_catalog`、`information_schema` 的慢查询通常是工具或框架产生的，优化价值低，应跳过。
- **GaussDB 不支持 `EXPLAIN (BUFFERS)`**：使用 `EXPLAIN ANALYZE` 或 `EXPLAIN PERFORMANCE`；分布式场景优先使用 `EXPLAIN PERFORMANCE` 以获取各 DN 的详细执行数据。
- **CONCURRENTLY 索引限制**：GaussDB 的 `CREATE INDEX CONCURRENTLY` 不支持列存表、分区表和临时表；对这类表需使用普通 `CREATE INDEX`（会短暂锁表写操作）。
- **ALTER SYSTEM SET 行为**：GaussDB 的 `ALTER SYSTEM SET` 对 SIGHUP 级参数立即生效，无需额外调用 `pg_reload_conf()`；POSTMASTER 级参数需重启。集群多节点场景建议改用 `gs_guc reload` 工具，可同时修改所有节点。
- **参数替换要匹配列类型**：EXPLAIN 时将 `?` 或 `$1`、`$2` 替换为实际值，且数据类型必须与列定义一致，否则执行计划与真实情况不符。
- **索引有维护成本**：每个索引会增加写操作（INSERT/UPDATE/DELETE）的开销，不应无节制地添加索引。
