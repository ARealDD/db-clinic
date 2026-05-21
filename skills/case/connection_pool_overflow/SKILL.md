---
id: connection_pool_overflow
name: "连接耗尽 - 连接池配置不当导致连接数超限"
category: resource_exhaustion
version: "1.0"
author: "运维团队"
description: "应用层连接池最大连接数超过 PostgreSQL max_connections，或连接泄露导致连接数持续增长，最终报错 connection limit exceeded"

symptoms:
  - "数据库连接数达到 max_connections 上限"
  - "应用报错 'connection limit exceeded' 或 'too many connections'"
  - "应用服务器响应变慢，请求积压"
  - "pg_stat_activity 中大量 idle 状态连接"

keywords:
  - 连接池
  - connection pool
  - max_connections
  - 连接泄露
  - PgBouncer
  - 连接耗尽
  - idle connection
  - connection limit

triggers:
  - "connection limit exceeded"
  - "too many connections"
  - "remaining connection slots"

evidence_required:
  - system_view
evidence_optional:
  - sql_text

---

## 根因

应用层连接池设置的最大连接数（如 500）远超 PostgreSQL 的 `max_connections`（如 100），当并发请求量增大时，连接池尝试创建超出数据库承受能力的连接数。同时，连接泄露（应用代码未正确关闭连接）使得空闲连接持续积累，即使业务量不大也会耗尽连接槽。核心解决是合理设置连接池大小并引入连接池代理（如 PgBouncer）。

## 诊断步骤

### 步骤 1

**操作**：检查当前连接数和状态分布

- 工具：`execute_sql`
- 条件：应用报连接失败错误

**现象**：`pg_stat_activity` 显示连接总数接近或达到 max_connections，其中大量为 idle 或 idle in transaction 状态。

**现象分析**：大量 idle 连接说明连接池持有远超实际需要的连接数，或存在连接泄露，需立即清理并重新评估配置。

```sql
-- 连接数按状态统计
SELECT state, COUNT(*) FROM pg_stat_activity GROUP BY state;

-- 连接数按应用分组
SELECT application_name, COUNT(*)
FROM pg_stat_activity
GROUP BY application_name
ORDER BY COUNT(*) DESC;

-- 查看当前 max_connections
SHOW max_connections;
```

---

### 步骤 2

**操作**：识别长时间空闲的连接

- 工具：`execute_sql`
- 条件：已确认连接数异常

**现象**：大量连接 state='idle'，`state_change` 时间超过数小时，说明应用未正确释放连接或连接池空闲回收策略配置不当。

**现象分析**：长时间空闲连接是连接泄露或空闲超时配置过大的证据，需在应用层修复或配置 TCP keepalive。

```sql
SELECT pid, usename, application_name, client_addr,
       state, now() - state_change AS idle_duration
FROM pg_stat_activity
WHERE state = 'idle'
  AND now() - state_change > interval '30 minutes'
ORDER BY idle_duration DESC
LIMIT 20;
```

---

### 步骤 3

**操作**：验证连接池代理（PgBouncer）的必要性

- 工具：`execute_sql`
- 条件：业务高峰期连接数持续超限

**现象**：业务高峰期活跃连接数远超真实并发查询数，大多数连接处于 idle 状态空耗连接槽。

**现象分析**：这是缺少连接复用的典型特征。PgBouncer 的连接池化可以将数千应用连接复用为数十数据库连接。

```sql
SELECT state, COUNT(*),
       MAX(now() - state_change) AS max_idle_time
FROM pg_stat_activity
GROUP BY state;
```

---

## 恢复手段

### 根因1：清理空闲连接（紧急恢复）

**描述**：立即终止长时间空闲的连接，释放连接槽，恢复系统可用性。

**具体命令**：

```sql
-- 终止空闲超过1小时的连接
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE state = 'idle'
  AND now() - state_change > interval '1 hour';

-- 终止长时间运行的查询
SELECT pg_cancel_backend(pid)
FROM pg_stat_activity
WHERE state = 'active'
  AND now() - query_start > interval '10 minutes';
```

### 根因2：调整 max_connections 并配置合理的连接池（主要修复）

**描述**：根据服务器内存调整 max_connections，并将应用层连接池最大连接数设置在 max_connections 的 80% 以内；配置合理的空闲超时使连接及时回收。

**具体命令**：

```sql
-- 调整数据库最大连接数（需重启数据库才生效）
ALTER SYSTEM SET max_connections = '200';

-- 同步调整共享内存（每连接约需 5-8MB）
ALTER SYSTEM SET shared_buffers = '2GB';

-- 设置服务器端空闲连接超时（超过10分钟自动断开）
ALTER SYSTEM SET idle_session_timeout = '600000';  -- 10分钟（ms）

SELECT pg_reload_conf();
-- 注意：max_connections 和 shared_buffers 变更需要重启 PostgreSQL
```

### 根因3：部署 PgBouncer 连接池代理（完整修复）

**描述**：部署 PgBouncer 作为连接池代理，应用连接到 PgBouncer，PgBouncer 维护少量到 PostgreSQL 的真实连接，实现连接复用，从根本上解决连接数问题。

**具体命令**：

```sql
-- 以下为 PgBouncer pgbouncer.ini 关键配置示例（在 OS 上配置）
-- [databases]
-- mydb = host=127.0.0.1 port=5432 dbname=mydb
--
-- [pgbouncer]
-- pool_mode = transaction        -- 事务级连接池（推荐）
-- max_client_conn = 1000         -- 接受应用连接数
-- default_pool_size = 25         -- 到 PostgreSQL 的实际连接数
-- min_pool_size = 5
-- reserve_pool_size = 5
-- server_idle_timeout = 600

-- 部署后验证：应用连连接到 PgBouncer 端口（默认6432）
-- PostgreSQL 侧活跃连接数应保持在 default_pool_size 附近

-- 监控连接使用情况
SELECT state, COUNT(*) FROM pg_stat_activity GROUP BY state;
```
