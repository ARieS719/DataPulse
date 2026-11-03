# DataPulse 路线图与验收标准

> 原则：**主线先行（M0–M6 完成并留证据后，才铺支线）**；每个里程碑交付"代码 + 测试 + 证据"三件套。
> 状态图例：🚧 进行中 · ⬜ 未开始 · ✅ 已完成

## M0 仓库与环境启动 🚧

- [x] 项目评估与定位策略文档（一主线、双镜头）
- [x] 仓库骨架：目录结构 / 主线 README / pyproject / docker-compose（profile 分组）
- [x] 零依赖四表数据生成器
- [x] scripts/check_env.ps1 与 setup.ps1
- [ ] 环境自检通过（Python 3.10–3.12 + Java 17 + Docker 可选）
- [ ] **WAP 分支 POC 全 PASS，报告留痕 docs/poc/wap_poc_result.md**
- [ ] git 首次提交

**验收**：poc_wap.py 5 项检查全 PASS；small 规模数据生成成功。

## M1 湖仓底座 🚧（代码全部就绪，待运行验证）

- [x] 四表生成器（small/medium/large 三档；medium 运行验证待执行）
- [x] ODS 落盘（foundation/etl/etl_ods.py）
- [x] DWD：空值规整 + user_key 匿名化 + 枚举标准化（etl_dwd.py）
- [x] DWS：日×渠道 交易额/订单量/问答量聚合（etl_dws.py）
- [x] ADS：GMV 口径视图 + 渠道排行（etl_ads.py）
- [x] 流水线执行器 scripts/run_pipeline.py（gen→ods→dwd→dws→ads，耗时留痕 docs/benchmarks/）
- [x] 生成器单元测试 12 项（tests/test_data_gen.py，含确定性/主键/外键/值域）
- [ ] 全链路运行验证（small 冒烟 + medium 基准跑通，耗时记录）

**验收**：DWS 金额 = DWD 聚合金额（对账脚本，M1 收尾时补）；Iceberg 每层可查快照。

## M2 规则引擎 🚧（核心纯 Python 部分就绪，Spark 执行器待 M3 联调）

- [x] YAML 契约 schema（pydantic，8 类规则强类型校验 + 防注入标识符校验）
- [x] 编译器：N 规则 → 单条聚合 SQL（跨表规则编译为标量子查询预聚合）
- [x] 判定器 evaluator：P0/P1 分级 + 阈值判定 + 空表策略 + verdict（PASS/WARN/BLOCK）
- [x] 高层 API engine.py（load → compile → evaluate）
- [x] 示例契约 fact_order / dim_user（core/dq_engine/rules/examples/）
- [x] AI 规则 Copilot 纯 Python 核心：DDL 解析 + CSV 元数据探查 + 结构化提示词（ai/rules_copilot.py）
- [x] 21 项单元测试（含 SQLite 真实执行端到端语义验证，Spark/SQLite 双方言兼容）
- [ ] Spark 执行器 + WAP 审计分支集成（M3）
- [ ] 规则数 vs 扫描次数曲线证据（M3 基准）

**验收**：20 条规则合并为 1 次扫描（SQLite 端到端已证明语义，Spark 执行留痕待 M3）。

## M3 WAP 熔断流水线 🚧（纯 Python 核心就绪，Spark/Redis/Airflow 集成待本机联调）

- [x] 3σ 滑动水位线：zscore_detect + median-MAD 稳健变体 + 短窗口相对变化降级（anomaly/stats.py）
- [x] PSI 分布漂移检测 + 0.1/0.25 分级（psi / psi_from_histograms / psi_level）
- [x] AI 语义分层抽样：2% 分层 + 每层保底 + 确定性 seed（ai/sampling.py）
- [x] WAP 门禁执行器 run_wap_gate：分支扫描 → evaluate → fast_forward/熔断（quality_gate.py）
- [x] 21 项新单测（anomaly 14 + sampling 7），累计 54 项
- [ ] Redis 30 天水位线读写集成（本机验证）
- [x] WAP 门禁 CLI：ods_wap_write.py（分支写入）+ quality_gate_cli.py（分级熔断，非零退出码挂起下游）
- [x] Airflow DAG：gen → 审计写入 → 门禁 → dwd/dws/ads → 对账（airflow/dags/etl_pipeline.py + 自建镜像 Dockerfile.airflow）
- [x] 血缘演示 SQL 集（sql/，6 个分层脚本）+ 血缘扫描 CLI（scripts/lineage_scan.py）
- [x] 跨层对账脚本（scripts/reconcile.py，M1 验收物）
- [ ] Spark 执行器端到端：审计分支写入 → 门禁 → 原子合并（本机验证）
- [ ] Airflow 容器启动与 DAG 首次运行（本机验证）

**验收**：混沌前 4 类（重复/孤儿/精度/时区）P0 拦截率 100%。

## M4 血缘与 AI-RCA 🚧（纯 Python 核心就绪，熔断事件联动待本机联调）

- [x] SQLGlot 字段级血缘解析器：投影/别名/聚合/CTE/嵌套子查询/通配符/CTAS（lineage/parser.py）
- [x] NetworkX 双层血缘图（字段级+表级）+ BFS 爆炸半径/上下游/环安全（lineage/graph.py）
- [x] 目录级扫描器：CREATE TABLE 目标识别 + 单脚本容错 + 计时（lineage/scanner.py）
- [x] AI-RCA：规则化基线分类（8 类故障映射）+ LLM 提示词构建 + 回退韧性（ai/rca.py）
- [x] 23 项新单测（lineage 14 + rca 9），累计 77 项
- [ ] 与熔断事件的联动：quality_gate → blast_radius → RCA 报告（本机验证）
- [ ] 30 脚本 <500ms 严格基准证据（本机运行留痕）

**验收**：熔断表 1 秒内列出全部下游报表（BFS 已在单测中锁定语义，计时证据待本机）。

## M5 服务与大盘 🚧（代码就绪，待本机启动演示）

- [x] FastAPI 控制面：规则校验 / 血缘爆炸半径与上游 / 质检上报与历史 / 健康度 / 混沌触发占位（web/api.py）
- [x] 持久化层：PostgreSQL（psycopg3）+ 内存降级模式，质检历史与 RCA 报告双表（web/db.py）
- [x] Streamlit 大盘：健康度指标卡 + 血缘标红拓扑（graphviz DOT）+ 历史明细 + 诊断书（web/dashboard.py）
- [x] LineageGraph.to_dot 导出（标红高亮支持）
- [x] 12 项新单测（API 10 + DOT 2），累计 89 项
- [ ] 本机启动演示：docker compose --profile core up -d → uvicorn web.api:app / streamlit run web/dashboard.py
- [ ] M3 门禁结果 → API 上报联调（Spark 端到端验证后）

**验收**：docker compose 一键起全栈，前端可完整演示。

## M6 混沌与评测 🚧（框架与代码就绪，联调实验与完整报告待本机）

- [x] 混沌注入器 8 类故障（chaos/injector.py，确定性 seed + 副本隔离 + 报告留痕；第 9 类 data_skew 归入 S2）
- [x] 规则生成 Golden Dataset 评测框架 + 3 例标准答案（eval/golden_rules.py + eval/golden/cases/，其余 12 例随 Copilot 工作流补齐）
- [x] AI-RCA 命中率评测框架 + 8 场景 Ground Truth（eval/rca_eval.py + rca_golden.json）
- [x] Markdown 评测报告生成器（eval/report.py → docs/eval_report.md）
- [x] GitHub Actions CI：ruff + pytest + 覆盖率 ≥85% 门禁（.github/workflows/ci.yml）
- [x] 混沌联调实验编排器：4 检测通道 + 8 阳性/8 阴性对照设计（scripts/run_chaos_experiment.py）
- [x] 混沌规则集 4 份（eval/golden/chaos_rules/）
- [x] 20 项新单测（chaos 11 + eval 9），累计 109 项
- [x] 7 项 Copilot 单测，累计 116 项
- [ ] 混沌联调实验运行：注入 → ETL → WAP 门禁 → 拦截判定矩阵（本机 Spark 就绪后）
- [ ] 完整评测报告 + 量化指标证据（P0 拦截率 100%、召回 ≥96.5%、误报 <2.5%）

**验收**：量化数字全部由可复现脚本 + 留痕报告证明。

## 支线（主线代码已闭环，按优先级推进）

| 编号 | 内容 | 优先级 | 状态 |
| --- | --- | --- | --- |
| S1 | dbt 建模（星型模型 + SCD2 + dbt test 与自研引擎对比） | P0 | ✅ 代码就绪（foundation/dbt/，运行验证待本机） |
| S2 | Spark 调优实战（倾斜治理/AQE/小文件治理，第 9 类混沌故障） | P0 | 🚧 就绪：data_skew 注入 + 调优实验脚本 + docs/tuning.md；耗时对比证据待本机运行 |
| S3 | 指标口径层 + 质量运营指标 + 数据安全分级 | P0 | 🚧 口径层就绪（metrics 包：字典/口径 diff/血缘联动，10 项单测）；运营指标与安全分级待联调 |
| S4 | 生产化云映射文档 docs/production.md | P1 | ✅ 已交付 |
| S5 | Flink/Kafka 实时水位线（先 Spark SS 纯 Python 版） | P2 | 🚧 就绪：stream_simulator + realtime_waterline + docs/realtime.md（含 Flink 生产对照表）；运行验证待本机 |

## 缝合怪五条红线（全程警戒）

1. 主线未闭环不铺广度（支线全部排在 M6 之后）
2. 简历按投递方向拆分（DQE 版 / 数开版各一份）
3. 不为堆技术栈加无关组件（Doris/K8s 只进 production.md）
4. 主线要能扛 30 分钟深挖（规则编译/分支合并/血缘消歧）
5. 量化数字必有可复现证据（评测脚本 + 报告进仓库）
