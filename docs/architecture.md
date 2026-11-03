# DataPulse 架构设计

> 主线叙事：用现代数据栈（Iceberg 湖仓 + dbt 建模 + Airflow 编排）搭建一个**自带写入前质量熔断门禁**的数仓平台——先按规范建仓，再让自研质检引擎在每一层数据发布前做审计（WAP），最后用字段级血缘与 AI 完成根因闭环。

## 1. 数仓四层与加工规则

| 层 | 职责 | 加工规则示例 | 质检时机 |
| --- | --- | --- | --- |
| ODS 原始贴源 | 四张源表原样落 Iceberg，按天分区 | 不加工 | 落盘后：行数/时间戳格式/主键唯一 |
| DWD 明细清洗 | 空值规整、脱敏、枚举标准化 | age 缺失补默认值；手机号脱敏；状态码映射 | 落盘后：非空率、枚举范围、跨表关联完整性 |
| DWS 轻度汇总 | 日×渠道 交易额/订单量/问答量 | SUM(amount)、COUNT(DISTINCT user_id) | 落盘后：3σ 水位线、跨表总分对齐 |
| ADS 报表层 | 健康度评分、GMV 口径视图 | 各层质检结果聚合 | 发布前：口径一致性校验 |

## 2. 源表数据模型

| 表 | 主键 | 关键字段 | 支撑的质量维度 |
| --- | --- | --- | --- |
| dim_user | user_id | age / gender / city_tier / channel_source / is_vip | 完整性、有效性、PSI 漂移 |
| fact_order | order_id | user_id / order_time / channel / category / amount / status | 唯一性、及时性、3σ 行数 |
| fact_payment | payment_id | order_id / pay_time / pay_channel / pay_amount | 跨表一致性（order↔payment 金额对齐） |
| ai_qa_log | qa_id | session_time / intent / question / answer / model_version | 语义质检（乱码/敏感词/答非所问） |

## 3. WAP 熔断时序（核心机制）

```
ETL 清洗完成
   │
   ▼
写入 Iceberg 审计分支 (spark.wap.branch = audit)          ← 脏数据此刻对 main 不可见
   │
   ▼
质检引擎四路并发：
   ① 静态硬规则：YAML → 单条 Spark SQL 聚合扫描（空值率/重复/枚举/跨表差额）
   ② 动态可观测：Redis 30 天水位线 → 滑动窗口 3σ；PSI 分布漂移（>0.25 告警）
   ③ AI 语义质检：ai_qa_log 2% 分层抽样，LLM 评估乱码/敏感/文不对题
   │
   ├─ 全过 ──────────────► fast_forward('audit' → 'main')   ← 原子发布
   ├─ P1 警告级 ─────────► 告警 + fast_forward（放行）
   └─ P0 阻断级 ─────────► 熔断：分支不合并，下游 DAG 挂起 ─► 血缘 BFS 爆炸半径 ─► AI-RCA 诊断书
```

## 4. 组件与职责

| 组件 | 职责 | 里程碑 |
| --- | --- | --- |
| foundation/data_gen | 零依赖四表生成器（确定性、可复现） | M0 ✅ |
| foundation/etl | ODS/DWD/DWS/ADS 落盘与清洗 | M1 |
| core/dq_engine/rules | YAML 契约 schema + 单次扫描编译器 | M2 |
| core/dq_engine/anomaly | 3σ 水位线 + PSI | M3 |
| core/dq_engine/lineage | SQLGlot AST → NetworkX 血缘图 + BFS | M4 |
| core/dq_engine/ai | 规则 Copilot / 语义质检 / AI-RCA | M2–M4 |
| chaos/ | 8+1 类脏数据故障注入器 | M6 |
| eval/ | Golden Dataset 评测与量化证据 | M6 |
| web/ | FastAPI 控制面 + Streamlit 大盘 | M5 |

## 5. 技术选型权衡（速查）

| 选型 | 放弃方案 | 核心原因 |
| --- | --- | --- |
| PySpark + Iceberg | Hadoop + Hive | 零伪分布式开销；分支/快照原生支持 WAP |
| 自研规则编译器 | Great Expectations | N 条规则合并为 1 次全表扫描 |
| SQLGlot | Apache Atlas | 纯 Python AST 解析，无需外部图数据库 |
| Airflow + Redis | Jenkins + MySQL | DAG 数据依赖编排 + 毫秒级水位线 |
| 在线 API + ChromaDB | 本地开源大模型 | 零本地显卡负担，结构化输出稳定 |

## 6. 面试要点映射（架构决策 → 必问问题）

| 设计决策 | 对应面试问题 |
| --- | --- |
| WAP 审计分支 | "分支怎么建、怎么合并、并发写怎么办？" |
| 规则合并单次扫描 | "规则从 20 条涨到 200 条，耗时怎么变？" |
| 3σ 水位线 | "为什么是 3σ？节假日/大促怎么办？" |
| PSI 阈值 0.25 | "0.25 的依据是什么？误报率怎么控制？" |
| SQLGlot 血缘 | "CTE/子查询/同名字段怎么消歧？" |
| 混沌注入 + Golden Dataset | "你怎么证明你的平台自己是对的？" |
