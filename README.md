# DataPulse：以「写入前质量熔断」为核心的湖仓数仓平台

[![CI](https://github.com/ARieS719/DataPulse/actions/workflows/ci.yml/badge.svg)](https://github.com/ARieS719/DataPulse/actions/workflows/ci.yml)

> 该项目源于一个实际存在的数据质量痛点：**脏数据总是在落盘之后才被发现；等告警出来，下游报表和模型已经被污染了。**
>
> 因此该项目没有停留在"又一个数仓 demo"，而是为数仓加了一扇**发布前的质量闸门**：数据先写入 Iceberg 审计分支（main 零暴露）→ 规则引擎 + 动态水位线合并扫描 → 通过才 `fast_forward` 原子发布，不通过即熔断、下游 DAG 自动挂起。再叠加字段级血缘计算爆炸半径、AI 生成根因诊断书，把"发现 → 定位 → 归因"整条链路闭环。

## 📊 结果速览（100 万行基准档 · 单机实测）

> 技术主管可以只看这张表：每行结论都有可复现的证据文件，点开即是原始记录。

| 能力 | 实测结果 | 证据 |
| --- | --- | --- |
| 四层数仓全链路（100 万行订单） | gen 30.2s → ODS 27.6s → DWD 16.6s → DWS 7.7s → ADS 2.1s | [medium_pipeline_run.txt](docs/benchmarks/medium_pipeline_run.txt) |
| 跨层对账（金额精确到分） | 3/3 PASS：DWS 交易额 667,251,156.06 == DWD 明细合计 | [medium_reconcile.txt](docs/benchmarks/medium_reconcile.txt) |
| 混沌拦截矩阵 | **召回 1.0（8/8 故障全检出）、误报 0.0（8 个阴性对照全放行）** | [chaos_matrix.md](docs/benchmarks/chaos_matrix.md) |
| WAP 写入前熔断（POC） | 5/5：脏数据写入审计分支时 main 零暴露，校验通过后原子发布 | [wap_poc_result.md](docs/poc/wap_poc_result.md) |
| Airflow 编排 | DAG 7/7 success；门禁 3 表 `verdict=PASS merged=True`；对账 3/3 | [airflow_verification.md](docs/benchmarks/airflow_verification.md) |
| Web 控制面 | 8 个端点全通过（PostgreSQL 持久化 + 血缘 blast radius） | [web_verification.md](docs/benchmarks/web_verification.md) |
| 测试与 CI 门禁 | 147 项测试、覆盖率 **95.34%**（门禁 ≥85%）、ruff 0 违规 | [ci.yml](.github/workflows/ci.yml) |
| 字段级血缘 | 6 个 SQL 脚本 → 42 列节点 / 40 条边；blast radius 一次 BFS 12ms | [lineage_stats.json](docs/benchmarks/lineage_stats.json) |

## 该项目要解决的四个问题

- **规则配置成本高**：上千张表靠手写 SQL 巡检，业务一迭代就漏配 → **事前** 由 AI 生成 YAML 规则契约，人只做审阅。
- **事后告警太晚**：脏数据先落盘再告警，下游报表已经脏了 → **事中** WAP：写审计分支，校验通过才原子发布，脏数据永远到不了下游。
- **静态规则有盲区**：拦得住主键重复，拦不住"渠道断流暴跌"和"AI 答非所问" → **动态** 3σ 水位线 + PSI 漂移 + LLM 语义抽样。
- **排障链路割裂**：多源同步延迟要人工翻几十个 ETL 脚本 → **事后** SQLGlot 字段级血缘 + AI-RCA 根因诊断书。

## 架构

```
                        ┌───────────────────────────────────────────────┐
                        │          控制面 (FastAPI + Streamlit)           │
                        │   规则审批 · 血缘查询 · 混沌触发 · 健康大盘      │
                        └───────────────┬───────────────────────────────┘
                                        │ 质检结果 / 水位线 / 血缘图
        ┌───────────────┬───────────────┼────────────────┬──────────────┐
        ▼               ▼               ▼                ▼              ▼
 ┌────────────┐  ┌────────────┐  ┌────────────┐   ┌──────────┐   ┌───────────┐
 │ AI 规则助手 │  │  质检引擎   │  │  SQLGlot   │   │  AI-RCA  │   │ 混沌注入器 │
 │ (规则Copilot)│  │ YAML→Spark │  │  血缘解析   │   │  根因诊断 │   │  8+1类故障 │
 └────────────┘  │  3σ / PSI  │  └─────┬──────┘   └────┬─────┘   └───────────┘
                 └──────┬─────┘        │ NetworkX DAG  │
                        │ 审计分支       ▼               ▼
        ┌───────────────▼──────────────────────────────────────────────┐
        │            湖仓底座  PySpark + Apache Iceberg (WAP)            │
        │   ODS(原始贴源) → DWD(明细清洗) → DWS(轻度汇总) → ADS(报表)     │
        └───────────────▲──────────────────────────────────────────────┘
                        │
        ┌───────────────┴───────────────┐
        │ 数据生成器：用户/订单/支付/AI问答 │
        └───────────────────────────────┘
```

## 关键设计决策

| 决策 | 理由 |
| --- | --- |
| 用 Iceberg 分支做 WAP，而不是"影子表" | 分支是表内一等公民：审计分支与 main 共享元数据与数据文件，写完直接 `fast_forward` 原子切换——不用双写、不用搬家、不丢历史快照 |
| 把 N 条 YAML 规则编译成**一次** Spark SQL 扫描 | 逐条规则要扫 N 遍表；所有规则合成一个聚合查询，单次扫描出全部指标（混沌实验里 4 张表 × 多规则的门禁扫描为秒级） |
| 静态规则 + 3σ/MAD 水位线 + PSI + LLM 语义抽样（四层） | 静态规则拦结构脏数据；水位线拦"渠道断流"这类量级异常；PSI 拦分布漂移；LLM 拦语义污染——混沌矩阵证明四类故障全部命中 |
| sqlglot + networkx 做血缘 | 纯 Python AST 解析，不引入图数据库；blast radius 就是一次 BFS（42 节点/40 边的图，实测 12ms） |
| 9 类故障注入器 **+ 阴性对照** | 只证明"能拦住"是自欺欺人，必须同时证明"干净数据不误杀"。矩阵中 8 个阴性对照即为此设计——也是最终把误报率压到 0.0 的方式 |

## 技术栈（现代数据栈，单机可跑）

| 层级 | 选型 | 理由 |
| --- | --- | --- |
| 湖仓存储与计算 | PySpark + Apache Iceberg | 零伪分布式开销；分支/快照原生支持 WAP |
| 质量校验引擎 | 自研 YAML→Spark SQL 编译器 | N 条规则合并为单次聚合扫描 |
| 字段级血缘 | SQLGlot + NetworkX | 纯 Python AST 解析，无外部图数据库 |
| 调度与状态 | Airflow (LocalExecutor) + Redis | DAG 数据依赖编排 + 毫秒级水位线 |
| AI 提效 | 在线 API（可插拔 LLM + 规则基线兜底） | 零本地显卡负担，结构化输出稳定 |
| 服务与大盘 | FastAPI + Streamlit + PostgreSQL | 控制面 + 可观测大盘 + 质检历史（PG 不可用时自动降级内存模式） |

## 目录结构

```
lakehouse-dq-engine/
├── core/            # ★ 主线：质检引擎（rules 编译器 / quality_gate / anomaly / lineage / ai）
├── foundation/      # 底座：零依赖数据生成器 + 四层 ETL（SparkSession 工厂、WAP 表属性）
├── chaos/           # 混沌注入器（8+1 类故障，含数据倾斜）
├── eval/            # Golden Dataset 规则评测 + 混沌专用规则集
├── web/             # FastAPI 控制面 + Streamlit 大盘（PG/内存双模）
├── airflow/         # 自定义镜像 + WAP 熔断 DAG（LocalExecutor）
├── scripts/         # POC / 对账 / 血缘扫描 / 混沌实验 / 实时水位线 / Windows 补丁
├── tests/           # 147 项单元测试（覆盖率 95%）
└── docs/            # 架构 / 路线图 / 运行排查手册 / 基准证据（benchmarks/）
```

## 快速开始

**环境要求**：Python 3.11 · Java 17（PySpark 必需，推荐 [Temurin](https://adoptium.net/)）· Docker Desktop（可选：Postgres/Redis/Airflow）

```powershell
# 0) 环境自检
powershell -ExecutionPolicy Bypass -File scripts\check_env.ps1

# 1) 虚拟环境 + 依赖
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

# 1.5) 【Windows 必需】JVM 参数与路径补丁（venv 重装后需重跑）
python scripts\apply_win_patches.py

# 2) 生成样本数据（small=1 万 / medium=100 万，量化指标基准档）
python foundation\data_gen\generate.py --scale medium

# 3) 四层全链路 + 对账
python scripts\run_pipeline.py --scale medium
python scripts\reconcile.py

# 4) WAP 写入前熔断 POC（首次运行拉取约 30MB jar）
python scripts\poc_wap.py

# 5) 混沌拦截矩阵（8 故障 + 8 阴性对照）
python scripts\run_chaos_experiment.py --scale medium

# 6) 起服务（可选）
docker compose --profile core up -d          # Postgres + Redis
uvicorn scripts.run_api_pg:app --port 8000   # FastAPI 控制面
streamlit run web/dashboard.py               # Streamlit 大盘
docker compose --profile airflow up -d --build
```

## 里程碑

| 里程碑 | 内容 | 状态 |
| --- | --- | --- |
| M0 | 环境启动 + WAP 分支 POC 风险验证 | ✅ 5/5（[wap_poc_result.md](docs/poc/wap_poc_result.md)） |
| M1 | 湖仓底座：四表生成 + ODS/DWD/DWS/ADS 落盘 | ✅ small + medium 100 万行，对账 3/3 |
| M2 | 规则引擎：YAML 契约 + 单次扫描编译 | ✅ 8 类规则 + 防注入表达式，147 测试 95% 覆盖 |
| M3 | WAP 熔断流水线 + 3σ/PSI + 分级决策 + Airflow | ✅ 流水线经混沌验证；DAG 7/7 success |
| M4 | 字段级血缘 + AI-RCA | 🚧 血缘 ✅（42 节点/40 边）；AI-RCA 已实现待全量演示 |
| M5 | FastAPI + Streamlit 大盘 | ✅ PG 全接线，8 端点验证通过 |
| M6 | 混沌注入 + Golden Dataset + CI 门禁 + 量化指标 | 🚧 混沌召回 1.0 / 误报 0.0 ✅；Golden ✅；CI 就绪 |
| S1–S5 | 数开支线（dbt / Spark 调优 / 指标口径 / 云映射 / Flink） | ⬜ 未启动 |

完整计划与验收标准见 [docs/roadmap.md](docs/roadmap.md)。

## 诚实边界

该项目是**单机演示级实现**：在笔记本上以 100 万行为基准验证完整闭环，不是生产集群。多租户、规则市场、云平台映射、资源成本模型不在仓库范围内（生产化思路见 docs/production.md）。数开支线 S1–S5（dbt 建模、Spark 调优实验、Flink 实时链路）仅保留骨架，尚未启动。
