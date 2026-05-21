# skills — 诊断知识库

结构化的 DBA 诊断知识体系，包含 35 个诊断案例剧本（case skills）和 4 类领域参考知识（knowledge skills），通过语义匹配动态注入 system prompt。

## 目录结构

```
skills/
├── base.py        # Skill、DiagnosticStep、RootCause 数据类
├── registry.py    # SkillRegistry：加载并管理所有 Skill
├── matcher.py     # SkillMatcher：语义匹配，选出 top-K 相关 Skill
├── case/          # 35 个诊断案例 Skill（每个为独立目录，含 SKILL.md）
└── knowledge/     # 4 个领域参考知识（每个为独立目录）
```

## case/ — 诊断案例 Skill（35 个）

每个 case 目录包含一个 `SKILL.md` 文件，使用带 frontmatter 的 Markdown 格式。

### 完整 Skill 列表

| Skill | 诊断场景 |
|-------|---------|
| `slow_sql_diagnosis` | 通用慢 SQL 诊断 |
| `missing_index_order_query` | 订单/范围查询缺少索引 |
| `missing_index_login` | 登录场景缺少索引 |
| `stale_statistics_wrong_plan` | 统计信息过期导致错误执行计划 |
| `lock_analysis` | 锁竞争分析 |
| `deadlock_lock_order` | 死锁（加锁顺序不一致） |
| `large_transaction_lock` | 大事务导致锁等待 |
| `concurrent_lock_contention` | 并发写入锁争用 |
| `wait_event_analysis` | 等待事件分析 |
| `index_advisor` | 索引设计建议 |
| `index_bloat_vacuum` | 索引膨胀与 VACUUM |
| `complex_join_missing_index` | 复杂 JOIN 缺少索引 |
| `improper_join_order` | JOIN 顺序不当 |
| `correlated_subquery` | 相关子查询性能问题 |
| `nested_views_perf` | 嵌套视图性能 |
| `function_disables_index` | 函数调用导致索引失效 |
| `type_mismatch_index_fail` | 类型不匹配导致索引失效 |
| `like_leading_wildcard` | LIKE 前缀通配符索引失效 |
| `composite_index_order` | 复合索引列顺序问题 |
| `data_skew_plan_deviation` | 数据倾斜导致计划偏差 |
| `slow_sql_nestloop` | NestLoop 导致的慢 SQL |
| `excessive_sorting` | 过多排序操作 |
| `slow_groupby_report` | 慢 GROUP BY / 报表查询 |
| `order_by_random` | ORDER BY RANDOM() 性能 |
| `select_star_wide_table` | SELECT * 宽表问题 |
| `multiple_seq_scans` | 多次全表扫描 |
| `connection_pool_overflow` | 连接池溢出 |
| `small_work_mem` | work_mem 过小 |
| `temp_space_exhausted` | 临时空间耗尽 |
| `default_config_params` | 默认配置参数未调优 |
| `no_partitioning_history` | 历史数据未分区 |
| `bulk_update_no_where` | 大批量 UPDATE 无 WHERE |
| `fk_cascade_slow_delete` | 外键级联慢删除 |
| `frequent_commit_batch` | 频繁小事务提交 |
| `recursive_query_perf` | 递归查询性能 |

### Skill 文件格式（Markdown + Frontmatter）

```markdown
---
id: slow_sql_diagnosis
name: "Slow SQL Diagnosis"
category: slow_sql
keywords:
  - slow
  - "seq scan"
  - "full table scan"
triggers:
  - "duration:\\s*\\d+ ms"
symptoms:
  - "Query takes much longer than expected"
evidence_required:
  - sql_text
  - explain_plan
---

## 诊断步骤

1. 获取完整的慢 SQL 文本
2. 使用 `analyze_sql` 进行 SQL 结构分析
3. 使用 `get_explain_plan` 获取执行计划

## 根因与修复

### 缺少索引（概率 75%）
- **识别特征**：大表上的 Seq Scan，cost 估算很高
- **修复方案**：`CREATE INDEX CONCURRENTLY idx_name ON table(col);`

### 统计信息过期（概率 65%）
- **识别特征**：estimated rows >> actual rows
- **修复方案**：`VACUUM ANALYZE table_name;`
```

---

## knowledge/ — 领域参考知识（4 个）

| 目录 | 内容 |
|------|------|
| `gaussdb_system_views` | GaussDB 系统视图使用指南（gs_stat_activity 等） |
| `gaussdb_wait_events` | 等待事件类型说明与诊断映射 |
| `gaussdb_slow_sql_inspection` | GaussDB 慢 SQL 诊断工具和视图 |
| `gaussdb_explain_settings` | EXPLAIN 参数和输出解读 |

---

## registry.py — SkillRegistry

启动时自动扫描并加载所有 Skill：

```python
registry = SkillRegistry()
# 自动扫描 skills/case/ 和 skills/knowledge/

skills = registry.get_all()                    # 获取所有 Skill
skill = registry.get_by_id("slow_sql_diagnosis")  # 按 ID 获取
```

---

## matcher.py — SkillMatcher

基于关键词、症状、正则触发器和已有证据类型的综合评分算法：

```
score = 0
+ 2.0 × keyword 命中次数（精确匹配）
+ 1.5 × symptom 词汇重叠（≥2 个词匹配）
+ 3.0 × trigger 正则匹配次数（最高权重）
+ 1.5 × evidence_required 中已收集的证据类型数量
+ 1.0 × category 关键词命中
```

```python
matcher = SkillMatcher(registry)

# 选出 top-K 相关 Skill
matched = matcher.match(
    query="我的 SELECT 查询跑了 30 秒",
    evidence_store=session.evidence,
    top_k=3
)

# 调试：查看各 Skill 的评分详情
matcher.explain(query, evidence_store)
```

---

## 添加新 Skill

1. 在 `skills/case/` 下创建新目录：`mkdir skills/case/<skill_name>`
2. 创建 `SKILL.md`，参考现有文件填写 frontmatter 和内容
3. 重启服务，`SkillRegistry` 自动加载（无需修改代码）
4. 验证：`GET /api/skills` 确认新 Skill 出现

**命名建议**：目录名使用小写加下划线，与 `id` 字段保持一致。
