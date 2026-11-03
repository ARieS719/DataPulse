# Spark 调优实战手册（S2 支线）

> 目标：把"100 万行 3 秒"升级为"我知道 10 亿行怎么保性能"的完整论据。
> 每个知识点 = 现象 → 成因 → 解法 → 本仓库实验 → 面试话术。

## 1. 数据倾斜（必考）

- **现象**：Spark UI 中个别 task 耗时远长于其余（长尾）、executor GC 告警、整体耗时被单 task 卡死。
- **成因**：热点 key（如 80% 订单集中在单一渠道）使 shuffle 后个别分区数据量数倍于均值。
- **解法**：
  1. **两阶段聚合/加盐**：先按 `(key, salt)` 预聚合打散热点，再二次汇总（本仓库 `scripts/tuning_experiment.py` 的 `skewed_salted` 用例，salt = `pmod(hash(order_id), 8)`）；
  2. **AQE 倾斜 Join 优化**：`spark.sql.adaptive.skewJoin.enabled=true`（Spark 3 默认），自动拆分大分区；
  3. **小表广播**：维度表 < 10MB 走 BroadcastHashJoin 消除 shuffle（`spark.sql.autoBroadcastJoinThreshold`）。
- **实验**：`python scripts/tuning_experiment.py --scale medium` 对比 baseline/skewed/skewed_salted/skewed_aqe 四组耗时。
- **话术**："第 9 类混沌故障就是专门制造倾斜的：改写渠道分布让 80% 订单集中到一个 key，再用加盐两阶段聚合把耗时拉回来，对比数据在 tuning_runs.md。"

## 2. AQE（自适应查询执行，Spark 3 默认开启）

三个能力：运行时**动态合并 shuffle 分区**、**倾斜 Join 自动拆分**、**动态切换 Join 策略**（sort-merge ↔ broadcast）。
- **实验**：`aqe` vs `baseline` 用例（`spark.sql.adaptive.enabled` 开/关对比）。
- **话术**："AQE 解决的是'事前猜不准'——分区数和 Join 策略在运行时按真实数据量自适应，这也是我为什么不需要为每张表手工调 shuffle 分区数。"

## 3. Shuffle 分区数

- 原则：`spark.sql.shuffle.partitions` ≈ 2–3 × executor 总核数，或按"单分区 128–200MB"反推；
- 本仓库单机 demo 设 4（local[2]）；生产按集群核数放大——这是"单机配置 ≠ 生产配置"的典型例子。

## 4. 存储与文件治理（真实 JD 原词）

- **Parquet + zstd**：列存 + 压缩率均衡，Iceberg 默认写格式；
- **小文件合并**：`CALL local.system.rewrite_data_files('ods.fact_order')`——小文件过多导致扫描开销与 NameNode 压力，定时 compaction；
- **Sort Order / Z-order**：按高频过滤列排序写入，分区内裁剪（data skipping）减少扫描量；
- **分区裁剪**：质检 SQL 带 `partition_filter`（我们的 `compile_ruleset` 已参数化）——增量日分区扫描只读 1/30 数据量，这是规模演进的核心手段（见 docs/production.md §3）。

## 5. 质检引擎的性能设计（本项目特有论据）

- **合并扫描**：N 条规则 → 1 条 SQL 的 N 个聚合表达式，全表只扫一次（规则数增加不增加扫描次数）；
- **对比实验**：`eval/` 评测中输出"规则数 vs 扫描耗时"曲线（10/50/100 条规则）；
- **复杂度话术**："耗时 ≈ O(扫描行数)，规则数只是常数项系数——20 条规则和 200 条规则的差距远小于多扫一次表。"

## 6. 面试追问预案

| 追问 | 应答要点 |
| --- | --- |
| "100 万行 3 秒，10 亿行呢？" | 增量分区扫描（1/30）+ AQE + 倾斜治理 + 广播 Join；给出 tuning_runs.md 的倾斜治理对比 |
| "遇到倾斜你怎么定位？" | Spark UI 看 task 时长分布/数据量分布 → 找到热点 key（我们直接造了 data_skew 故障复现） |
| "小文件怎么治理？" | rewrite_data_files 定时合并 + 写入侧控制（按分区批量提交） |
| "为什么要 DECIMAL 不用 FLOAT？" | 金额精度（0.3% 截断是我们的混沌用例）、sum_align 高精度对齐的前提 |
