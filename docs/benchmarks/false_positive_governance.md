# 误报治理记录：误报率 0.5 → 0.0 的四轮归因与修复

- 最终基准：medium 档混沌矩阵 **召回 1.0 / 误报率 0.0**（8 故障全检出、8 阴性对照全放行）
- 核心方法论：**阳性用例证明"拦得住"，阴性对照证明"不误杀"**——误报率是质量门禁的第二生命线

## 误报率演进（同口径复现：small 档混沌矩阵）

| 阶段 | 误报率 | 假熔断的阴性对照 |
| --- | --- | --- |
| 初始 | 0.5 | duplicate_pk_clean / orphan_fk_clean / amount_truncate_clean / timestamp_mix_clean |
| 第一轮修复后 | 0.25 | amount_truncate_clean / schema_drift_clean |
| 第二轮修复后 | **0.0** | 无 |

> 趋势数据来自各阶段 chaos_matrix.md 留痕；修复代码全部在库，可复现。

## 四轮归因 → 修复（每轮一个根因）

| # | 假熔断根因 | 修复 | 落地 |
| --- | --- | --- | --- |
| 1 | 阴性对照把干净数据**重复加载**进审计分支（分支=main 干净态+再 append 一遍干净批 → 主键必然重复） | 阴性对照跳过数据加载，直接对干净分支跑门禁 | `scripts/run_chaos_experiment.py` 的 `skip_load=True` |
| 2 | schema_drift 规则阈值 0.7 **恰好卡在** 30% 漂移后的填充率边界（70% ≥ 70% 放行） | 阈值收紧至 0.9（分支含历史干净数据时仍能检出漂移批） | `eval/golden/chaos_rules/ods_ai_qa_log.rules.yaml` |
| 3 | amount_truncate 的跨表对齐**业务口径错误**：对比全表 amount 与支付流水，干净数据本身就不对齐（未支付订单+优惠券） | 规则引擎新增 `agg_expr`（应付金额 = amount - coupon_discount）与 `agg_filter`（仅已支付订单），对齐口径与业务一致 | `core/dq_engine/rules/schema.py`、`compiler.py` |
| 4 | **基线污染**：历史实验把混沌数据 fast_forward 进 main，跨运行累积（实测 main 被污染到 30000 行） | 实验开始前强制重建干净基线、结束后恢复 | `scripts/run_chaos_experiment.py` 的 `ensure_clean_baseline()` |

## 方法论沉淀（误报治理三原则）

1. **阴性对照成对设计**：每类故障必须配一个干净对照组，且对照组走与阳性完全相同的执行路径——否则测不出"路径自身"的假阳性（第 1 轮根因正是路径 bug，而非规则 bug）；
2. **阈值与边界对齐**：阈值必须相对"业务语义 + 数据分布"双重校准，恰好卡在故障注入比例的阈值是伪阈值（第 2 轮）；
3. **基线隔离**：评测环境与演示数据隔离，任何"可写"实验必须自愈式恢复基线（第 4 轮）——否则误报率测的是被污染的基线，没有意义。

## 复现方式

```powershell
python scripts\run_pipeline.py --scale medium          # 干净基线
python scripts\run_chaos_experiment.py --scale medium  # 混沌矩阵（召回/误报率）
```

> 本记录由 docs/benchmarks/chaos_matrix.md 的历次留痕与上述代码修复交叉印证。
