---
id: gaussdb_tuning_params
name: "GaussDB 调优参数速查表"
category: config
description: "GaussDB/openGauss 性能调优相关的核心参数名称及功能说明，防止 Agent 生成不存在的参数（如 statement_mem）"
keywords:
  - work_mem
  - shared_buffers
  - effective_cache_size
  - maintenance_work_mem
  - 参数调优
  - 调优
  - tuning
  - config
  - 配置
  - 内存
  - memory
  - statement_mem
  - random_page_cost
  - wal
  - checkpoint
  - autovacuum
  - 并行
  - parallel
  - 连接数
  - max_connections
tags:
  - gaussdb
  - tuning
  - config
  - performance
version: "1.0"
author: "运维团队"
---

## 一、内存相关参数

| 参数名 | 功能说明 |
|--------|----------|
| `shared_buffers` | 数据库共享缓冲区大小，存放热数据页面。修改后需**重启**生效 |
| `work_mem` | 每个查询节点（Sort、Hash Join、HashAggregate）可用的内存上限。注意：一条 SQL 可能有多个节点同时使用 work_mem |
| `maintenance_work_mem` | VACUUM、CREATE INDEX、ALTER TABLE ADD FOREIGN KEY 等维护操作使用的内存上限 |
| `effective_cache_size` | 告知优化器操作系统和数据库可用的缓存总量（不分配实际内存），影响优化器对索引扫描代价的估算 |
| `wal_buffers` | WAL 日志缓冲区大小。-1 表示 shared_buffers 的 1/32，通常自动值即可 |
| `temp_buffers` | 每个会话用于访问临时表的缓冲区大小。仅影响临时表，**不影响排序和哈希操作** |
| `cstore_buffers` | 列存表的共享缓冲区大小（仅列存引擎使用）。修改后需**重启**生效 |

## 二、优化器代价参数

| 参数名 | 功能说明 |
|--------|----------|
| `random_page_cost` | 随机页面读取的代价因子。SSD 应调低，引导优化器更积极地选择索引扫描 |
| `seq_page_cost` | 顺序页面读取的代价因子 |
| `cpu_tuple_cost` | 处理每行数据的 CPU 代价因子 |
| `cpu_index_tuple_cost` | 处理每个索引条目的 CPU 代价因子 |
| `cpu_operator_cost` | 执行每个运算符或函数的 CPU 代价因子 |
| `effective_io_concurrency` | 可以同时发起的并发磁盘 I/O 操作数。SSD 支持高并发 I/O，应大幅调高 |
| `default_statistics_target` | 统计信息采样精度。增大可提高基数估算准确性，但 ANALYZE 耗时增加 |

## 三、连接与并发参数

| 参数名 | 功能说明 |
|--------|----------|
| `max_connections` | 最大并发连接数。每个连接占用内存（work_mem 等），不宜过高。修改后需**重启**生效 |
| `max_prepared_transactions` | 最大预备事务数。分布式事务场景需开启。修改后需**重启**生效 |

## 四、并行查询参数

| 参数名 | 功能说明 |
|--------|----------|
| `query_dop` | GaussDB 特有参数，控制查询并行度（Degree of Parallelism）。设为 0 表示自适应 |
| `max_parallel_workers_per_gather` | 每个 Gather 节点最大并行 Worker 数 |
| `max_parallel_workers` | 系统允许的最大并行 Worker 总数 |
| `max_parallel_maintenance_workers` | 维护操作（如 CREATE INDEX）可用的最大并行 Worker 数 |
| `parallel_tuple_cost` | Worker 向 Gather 传递每行的代价因子 |
| `parallel_setup_cost` | 启动并行 Worker 的代价因子 |
| `min_parallel_table_scan_size` | 表的最小扫描量，低于此值不启用并行扫描 |

## 五、WAL 与检查点参数

| 参数名 | 功能说明 |
|--------|----------|
| `max_wal_size` | 触发检查点前允许的最大 WAL 日志量。增大可减少检查点频率，降低 I/O 峰值 |
| `min_wal_size` | WAL 日志回收的最小保留量 |
| `checkpoint_timeout` | 两次自动检查点的最大间隔时间 |
| `checkpoint_completion_target` | 检查点写入的平滑因子。值越高，写入越平滑，I/O 峰值越低 |
| `wal_level` | WAL 日志级别。logical 支持逻辑复制但开销稍大 |
| `synchronous_commit` | 是否等待 WAL 刷盘后返回。关闭可提升写入性能但有少量数据丢失风险 |
| `full_page_writes` | 检查点后首次修改页面时写入完整页面。生产环境建议保持开启 |
| `wal_writer_delay` | WAL Writer 进程的刷盘间隔 |

## 六、VACUUM 与自动清理参数

| 参数名 | 功能说明 |
|--------|----------|
| `autovacuum` | 是否开启自动 VACUUM。生产环境必须保持开启 |
| `autovacuum_max_workers` | 同时运行的最大 autovacuum Worker 数 |
| `autovacuum_vacuum_threshold` | 触发 VACUUM 的最小死亡元组数 |
| `autovacuum_vacuum_scale_factor` | 表中死亡元组比例超过此值时触发 VACUUM。大表应调小 |
| `autovacuum_analyze_threshold` | 触发 ANALYZE 的最小变更行数 |
| `autovacuum_analyze_scale_factor` | 表中变更行比例超过此值时触发 ANALYZE。大表应调小 |
| `autovacuum_vacuum_cost_limit` | autovacuum 的 I/O 代价上限。增大可加速清理但增加 I/O 压力 |
| `autovacuum_vacuum_cost_delay` | autovacuum I/O 节流的延迟时间。减小可加速清理 |

## 七、GaussDB/openGauss 特有参数

| 参数名 | 功能说明 |
|--------|----------|
| `query_dop` | 查询并行度。GaussDB 用此参数替代单纯依赖 PostgreSQL 的 parallel 参数体系 |
| `enable_codegen` | 是否启用 LLVM JIT 代码生成加速查询执行 |
| `codegen_cost_threshold` | 触发代码生成的最低代价阈值 |
| `explain_perf_mode` | EXPLAIN 输出模式，详见 gaussdb_explain_settings 知识 |
| `enable_hashjoin` | 是否允许优化器选择 Hash Join |
| `enable_mergejoin` | 是否允许优化器选择 Merge Join |
| `enable_nestloop` | 是否允许优化器选择 Nested Loop Join |
| `enable_seqscan` | 是否允许优化器选择顺序扫描 |
| `enable_indexscan` | 是否允许优化器选择索引扫描 |
| `enable_indexonlyscan` | 是否允许优化器选择仅索引扫描 |
| `enable_bitmapscan` | 是否允许优化器选择位图扫描 |
| `instr_unique_sql_count` | 保留的唯一 SQL 指纹数量（用于 `dbe_perf.statement` 视图） |
| `track_activity_query_size` | `pg_stat_activity` 中记录的最大 SQL 文本长度（字节） |
| `log_min_duration_statement` | 执行时间超过此阈值的 SQL 会被记录到日志。-1 表示关闭 |
| `enable_resource_track` | 是否启用资源追踪（WLM 相关） |
| `resource_track_duration` | 执行时间超过此值的语句会被资源追踪记录 |
| `resource_track_cost` | 代价超过此值的语句才会被资源追踪 |

## 八、会话级参数调整方法

```sql
-- 查看参数当前值
SHOW work_mem;
SHOW shared_buffers;

-- 查看参数详细信息（含来源、是否需要重启）
SELECT name, setting, unit, context, source
FROM pg_settings
WHERE name = 'work_mem';

-- 会话级调整（仅影响当前连接，断开后失效）
SET work_mem = '256MB';

-- 事务级调整（仅在当前事务内生效）
SET LOCAL work_mem = '256MB';

-- 恢复默认值
RESET work_mem;

-- 全局持久化修改（写入配置文件，reload 或重启后生效）
ALTER SYSTEM SET work_mem = '32MB';
SELECT pg_reload_conf();  -- 部分参数需重启

-- 查看所有非默认参数
SELECT name, setting, unit, source
FROM pg_settings
WHERE source NOT IN ('default', 'override')
ORDER BY name;
```
