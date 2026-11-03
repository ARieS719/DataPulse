"""M1 里程碑：四张源表 CSV → ODS 层 Iceberg 表（原始贴源，仅落盘与分区，不做加工）。

用法：
  python foundation/etl/etl_ods.py --raw-dir data/raw
重复运行会先 DROP 再重建（开发期幂等；生产由 Airflow 调度保证不重跑）。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pyspark.sql import functions as F  # noqa: E402

from foundation.etl.spark import build_spark  # noqa: E402

TABLES = {
    "dim_user": {
        "ddl": (
            "user_id STRING, register_time TIMESTAMP, age INT, gender STRING, "
            "city_tier INT, channel_source STRING, is_vip INT"
        ),
        "partition": "days(register_time)",
        "ts_col": "register_time",
        "casts": {"age": "int", "city_tier": "int", "is_vip": "int"},
    },
    "fact_order": {
        "ddl": (
            "order_id STRING, user_id STRING, order_time TIMESTAMP, channel STRING, "
            "category STRING, amount DECIMAL(12,2), coupon_discount DECIMAL(12,2), status STRING"
        ),
        "partition": "days(order_time)",
        "ts_col": "order_time",
        "casts": {"amount": "decimal(12,2)", "coupon_discount": "decimal(12,2)"},
    },
    "fact_payment": {
        "ddl": (
            "payment_id STRING, order_id STRING, user_id STRING, pay_time TIMESTAMP, "
            "pay_channel STRING, pay_amount DECIMAL(12,2), currency STRING, pay_status STRING"
        ),
        "partition": "days(pay_time)",
        "ts_col": "pay_time",
        "casts": {"pay_amount": "decimal(12,2)"},
    },
    "ai_qa_log": {
        "ddl": (
            "qa_id STRING, user_id STRING, session_time TIMESTAMP, intent STRING, "
            "question STRING, answer STRING, model_version STRING, latency_ms INT, session_len INT"
        ),
        "partition": "days(session_time)",
        "ts_col": "session_time",
        "casts": {"latency_ms": "int", "session_len": "int"},
    },
}


def load_ods(spark, raw_dir: str = "data/raw", catalog: str = "local", ns: str = "ods", replace: bool = True) -> dict:
    """replace=True：DROP 后重建（开发期/全量重跑）；replace=False：增量追加（日常批次与混沌实验）。"""
    raw = Path(raw_dir)
    counts = {}
    for name, meta in TABLES.items():
        csv_path = raw / f"{name}.csv"
        if not csv_path.exists():
            raise FileNotFoundError(f"缺少 {csv_path}，请先运行 python foundation/data_gen/generate.py")

        df = spark.read.option("header", True).csv(str(csv_path))
        df = df.withColumn(meta["ts_col"], F.to_timestamp(F.col(meta["ts_col"]), "yyyy-MM-dd HH:mm:ss"))
        for col_, dtype_ in meta["casts"].items():
            df = df.withColumn(col_, F.col(col_).cast(dtype_))

        table = f"{catalog}.{ns}.{name}"
        if replace:
            spark.sql(f"DROP TABLE IF EXISTS {table}")
        spark.sql(
            f"CREATE TABLE IF NOT EXISTS {table} ({meta['ddl']}) USING iceberg "
            f"PARTITIONED BY ({meta['partition']}) TBLPROPERTIES "
            f"('write.wap.enabled'='true', 'read.parquet.vectorization.enabled'='false')"
        )
        df.writeTo(table).append()
        counts[name] = spark.table(table).count()
        print(f"  {table:<28} {counts[name]:>10,} 行")
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description="ODS 层落盘")
    ap.add_argument("--raw-dir", default="data/raw")
    args = ap.parse_args()

    spark = build_spark("datapulse-ods")
    try:
        t0 = time.time()
        counts = load_ods(spark, raw_dir=args.raw_dir)
        total = sum(counts.values())
        print(f"ODS 落盘完成：{total:,} 行，耗时 {time.time() - t0:.1f}s")
    finally:
        spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
