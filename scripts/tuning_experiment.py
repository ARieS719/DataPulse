"""S2：Spark 调优实验 —— 倾斜治理 / AQE / shuffle 分区 / 加盐两阶段聚合的量化对比。

实验设计：
  baseline       干净数据 + AQE 关（对照）
  aqe            干净数据 + AQE 开（自适应执行收益）
  skewed         data_skew 故障数据 + AQE 关（热点 key 对耗时的影响）
  skewed_salted  同上 + 加盐两阶段聚合（倾斜治理收益）

每次运行把耗时追加到 docs/benchmarks/tuning_runs.md（调优证据留痕）。

用法：
  python scripts/tuning_experiment.py --scale small   # 冒烟
  python scripts/tuning_experiment.py --scale medium # 基准（100 万订单）
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from chaos.injector import inject  # noqa: E402

# 待测聚合：按渠道分组统计（data_skew 下"抖音"成为热点 key）
SCAN_SQL = "SELECT channel, COUNT(*) AS cnt, SUM(amount) AS amt FROM fact_order GROUP BY channel"
# 加盐两阶段聚合：先按 (channel, salt) 预聚合打散热点，再二次汇总
SALTED_SQL = """
SELECT channel, SUM(cnt) AS cnt, SUM(amt) AS amt
FROM (
    SELECT channel, pmod(hash(order_id), 8) AS salt,
           COUNT(*) AS cnt, SUM(amount) AS amt
    FROM fact_order
    GROUP BY channel, pmod(hash(order_id), 8)
) GROUP BY channel
"""


def _spark(app: str, aqe: bool):
    from pyspark.sql import SparkSession

    return (
        SparkSession.builder.appName(app)
        .master("local[2]")
        .config("spark.driver.memory", "3g")
        .config("spark.sql.adaptive.enabled", "true" if aqe else "false")
        .config("spark.ui.showConsoleProgress", "false")
        .getOrCreate()
    )


def _load(spark, csv_path: Path):
    spark.read.option("header", True).csv(str(csv_path)).createOrReplaceTempView("fact_order")


def _time(spark, sql: str) -> float:
    t0 = time.perf_counter()
    spark.sql(sql).collect()
    return time.perf_counter() - t0


def main() -> int:
    ap = argparse.ArgumentParser(description="Spark 调优实验")
    ap.add_argument("--scale", default="small")
    ap.add_argument("--raw-dir", default="data/raw")
    ap.add_argument("--chaos-root", default="data/chaos")
    args = ap.parse_args()

    clean = Path(args.raw_dir) / "fact_order.csv"
    if not clean.exists():
        print(f"缺少 {clean}，请先运行 python scripts/run_pipeline.py --scale {args.scale} --stages gen", file=sys.stderr)
        return 2
    inject("data_skew", args.raw_dir, args.chaos_root)
    skewed = Path(args.chaos_root) / "data_skew" / "fact_order.csv"

    cases = []

    spark = _spark("datapulse-tuning-baseline", aqe=False)
    try:
        _load(spark, clean)
        cases.append(("baseline", _time(spark, SCAN_SQL)))
        _load(spark, skewed)
        cases.append(("skewed", _time(spark, SCAN_SQL)))
        cases.append(("skewed_salted", _time(spark, SALTED_SQL)))
    finally:
        spark.stop()

    spark = _spark("datapulse-tuning-aqe", aqe=True)
    try:
        _load(spark, clean)
        cases.append(("aqe", _time(spark, SCAN_SQL)))
        _load(spark, skewed)
        cases.append(("skewed_aqe", _time(spark, SCAN_SQL)))
    finally:
        spark.stop()

    # 留痕
    out = REPO_ROOT / "docs" / "benchmarks"
    out.mkdir(parents=True, exist_ok=True)
    bench = out / "tuning_runs.md"
    if not bench.exists():
        bench.write_text("| 时间 | scale | 用例 | 耗时(秒) |\n| --- | --- | --- | --- |\n", encoding="utf-8")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with bench.open("a", encoding="utf-8") as f:
        for name, secs in cases:
            f.write(f"| {ts} | {args.scale} | {name} | {secs:.3f} |\n")

    print("调优对比（耗时秒）：")
    for name, secs in cases:
        print(f"  [{name:<14}] {secs:.3f}")
    print("留痕：docs/benchmarks/tuning_runs.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
