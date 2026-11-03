"""S5：实时水位线质检（Spark Structured Streaming 微批版，纯 Python）。

消费 Kafka 订单流 → 1 分钟滚动窗口聚合（订单量/金额）→
与历史基线（clean 数据逐分钟统计）做 3σ 判定 → 越界告警输出。

设计说明（核心）：
  * 批式 WAP 与流式水位线**共用同一套判定核心**（dq_engine.anomaly.stats 的
    zscore_detect / psi），只是触发时机不同——这是"批流一体"的最小实现；
  * Watermark 处理迟到数据（2 分钟容忍窗，对应混沌故障 timestamp_mix 的流式形态）；
  * 生产版将 Spark SS 替换为 Flink SQL（Watermark/State/Checkpoint 语义更完整），
    见 docs/realtime.md 的对照表。

用法：
  docker compose --profile realtime up -d
  python scripts/stream_simulator.py --rate 20 &
  python scripts/realtime_waterline.py --bootstrap localhost:9092 --topic datapulse.orders
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "core"))

from dq_engine.anomaly.stats import zscore_detect  # noqa: E402

EVENT_SCHEMA = (
    "order_id STRING, user_id STRING, order_time TIMESTAMP, channel STRING, amount DECIMAL(12,2)"
)


def _load_baseline(raw_dir: Path) -> list[float]:
    """干净数据的逐分钟订单量为历史水位线（生产为 Redis 中 30 天同期窗口）。"""
    per_minute: Counter[str] = Counter()
    with (raw_dir / "fact_order.csv").open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            per_minute[row["order_time"][:16]] += 1
    return list(per_minute.values())


def _evaluate(batch_df, baseline: list[float], k: float) -> None:
    rows = [r.asDict() for r in batch_df.collect()]
    for r in rows:
        result = zscore_detect(baseline, float(r["cnt"]), k=k)
        if result.flagged:
            print(
                f"🚨 实时水位线告警：窗口 {r['window_start']} 渠道 {r['channel']} "
                f"订单量 {r['cnt']}（z={result.z}，方向 {result.direction}）"
            )


def main() -> int:
    ap = argparse.ArgumentParser(description="实时水位线质检（Spark SS 版）")
    ap.add_argument("--bootstrap", default="localhost:9092")
    ap.add_argument("--topic", default="datapulse.orders")
    ap.add_argument("--raw-dir", default="data/raw")
    ap.add_argument("--k", type=float, default=3.0)
    args = ap.parse_args()

    raw = Path(args.raw_dir)
    if not (raw / "fact_order.csv").exists():
        print(f"缺少 {raw / 'fact_order.csv'}，请先运行数据生成", file=sys.stderr)
        return 2

    from pyspark.sql import SparkSession
    from pyspark.sql import functions as F

    spark = (
        SparkSession.builder.appName("datapulse-realtime-waterline")
        .master("local[2]")
        .config("spark.driver.memory", "2g")
        .config("spark.ui.showConsoleProgress", "false")
        .config(
            "spark.jars.packages",
            "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.4",
        )
        .getOrCreate()
    )

    baseline = _load_baseline(raw)

    stream = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", args.bootstrap)
        .option("subscribe", args.topic)
        .option("startingOffsets", "latest")
        .load()
        .selectExpr("CAST(value AS STRING) AS json")
        .select(F.from_json("json", EVENT_SCHEMA).alias("e"))
        .select("e.*")
    )

    windowed = (
        stream.withWatermark("order_time", "2 minutes")
        .groupBy(F.window("order_time", "1 minute"), "channel")
        .agg(F.count("*").alias("cnt"), F.sum("amount").alias("amt"))
        .select(
            F.col("window.start").alias("window_start"),
            "channel",
            "cnt",
            "amt",
        )
    )

    query = (
        windowed.writeStream.outputMode("append")
        .foreachBatch(lambda df, _epoch: _evaluate(df, baseline, args.k))
        .start()
    )
    print(f"实时水位线已启动（topic={args.topic}, 3σ 阈值 k={args.k}），Ctrl+C 停止")
    query.awaitTermination()
    return 0


if __name__ == "__main__":
    sys.exit(main())
