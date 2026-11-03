# 实时数据质量链路（S5 支线）

> 目标：补齐实时方向的 Flink/Kafka 链路设计，并回答"实时链路的数据质量如何保障"。

## 1. 架构：批流一体共用判定核心

```
                      ┌─────────────────────────────────────────┐
                      │        规则元数据 + 判定核心（唯一来源）      │
                      │  YAML 契约 · zscore_detect · psi · 分级   │
                      └──────────────┬──────────────────────────┘
                    ┌────────────────┴────────────────┐
                    ▼                                 ▼
        批式（PySpark + Iceberg WAP）        流式（Spark SS / Flink SQL）
        写入审计分支 → 合并扫描 → 原子发布      1 分钟窗口聚合 → 3σ 实时水位线
        粒度：日分区全量审计                  粒度：分钟级趋势告警
```

**设计说明**："批式管'写入前的最终审计'，流式管'写入中的趋势预警'，两者共用同一套规则元数据与 3σ/PSI 判定函数——规则只定义一次，这是批流一体的最小实现。"

## 2. 本机实现（已交付脚本）

| 组件 | 脚本 | 说明 |
| --- | --- | --- |
| 订单流模拟器 | scripts/stream_simulator.py | 向 Kafka 发送 JSON 订单事件（含注入故障的通道开关） |
| 实时水位线 | scripts/realtime_waterline.py | Spark Structured Streaming：1 分钟窗口 + Watermark(2min) + 3σ 判定 |

运行：`docker compose --profile realtime up -d` → 起模拟器 → 起水位线（内存预算约 2GB，见内存预算表）。

## 3. 生产版：Flink SQL

| 维度 | Spark SS（本机版） | Flink SQL（生产版） |
| --- | --- | --- |
| 处理模型 | 微批（~100ms 级） | 真流式（毫秒级，事件驱动） |
| 窗口 | 滚动窗口 + Watermark | TUMBLE/HOP + Watermark + 迟到侧输出 |
| 状态 | 有限 | State TTL + RocksDB（可恢复大状态） |
| 一致性 | 微批原子提交 | Checkpoint + Exactly-Once（两阶段提交） |
| 同步链路 | — | Flink CDC（MySQL binlog）→ Iceberg/Kafka |

设计要点：
- **Watermark 与迟到数据**：容忍窗 + 迟到侧输出，对应混沌故障 timestamp_mix 的流式形态；
- **State 与恢复**：分钟级窗口状态定期 Checkpoint，故障后从最近检查点恢复不丢不重；
- **CDC 的 Exactly-Once**：binlog offset 与 checkpoint 绑定，端到端两阶段提交；
- **与批式门禁的协作**：流式只做趋势预警（P1），最终裁决仍由批式 WAP 审计（P0）——避免流式误报阻断整条链路（分级熔断原则）。

## 4. 硬件预算（16GB 内存分时运行）

`realtime` profile：Kafka(KRaft) ~1GB + MySQL ~0.7GB + Spark SS ~2GB ≈ 4GB，与 `core` profile 分时复用。
