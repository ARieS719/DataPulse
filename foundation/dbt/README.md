# dbt 建模工程（S1 支线）

> 目标：用声明式建模（dbt + Jinja + 测试 + 文档 + 血缘图）补齐数据开发方向，
> 并说明"**dbt test 与自研引擎什么关系**"。

## 运行方式（本机，DuckDB 零服务）

```powershell
pip install "dbt-duckdb>=1.8"                     # 建模 extras：pip install -e ".[modeling]"
cp foundation\dbt\profiles.yml $env:USERPROFILE\.dbt\profiles.yml   # 或放到 dbt 项目目录
dbt debug   --project-dir foundation\dbt
dbt build   --project-dir foundation\dbt          # 建模型 + 跑 dbt test + 建 SCD2 快照
dbt docs generate --project-dir foundation\dbt
dbt docs serve --project-dir foundation\dbt       # 数据资产目录 + 血缘图（浏览器查看）
```

## 建模内容

| 目录 | 内容 | 说明 |
| --- | --- | --- |
| models/staging/ | 三张贴源清洗视图（类型转换/空值规整，复用 macro） | staging/marts 分层规范 |
| models/marts/ | 星型模型：dim_user（代理键）+ fact_order + gmv_daily | 维度建模、指标口径统一 |
| snapshots/ | user_scd2 缓慢变化维（check 策略） | SCD2 缓慢变化维 |
| macros/ | normalize_amount（口径变更只改一处） | 口径集中管理 |
| models/schema.yml | not_null/unique/relationships/accepted_values 测试 | dbt test 四大基础测试 |

## dbt test 与自研质量引擎的分层对比

| | dbt test | DataPulse 自研引擎 |
| --- | --- | --- |
| 时机 | 建模期（开发态） | 发布期（生产写入前） |
| 定位 | SQL 逻辑正确性（单测） | 数据内容异常（熔断门禁） |
| 能力 | 唯一/非空/关系/枚举 | + 跨表总分对齐、3σ 水位线、PSI、语义质检 |
| 失败后果 | 构建失败，修复后重跑 | WAP 分支不合并，下游不可见 + 血缘定责 |
| 结论 | **互补而非替代**：生产上两者都配 | —— 这句话直接消解"为什么不用 dbt test"的质疑 |

## 生产映射

本机 dbt-duckdb 用于验证建模方法论（零内存开销）；
生产环境同一套模型无缝迁移到 dbt-spark（Iceberg）或 dbt-bigquery——只换 profiles，不改模型。
