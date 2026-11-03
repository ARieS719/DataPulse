"""M1 里程碑：DWS → ADS 高层报表层。

产出：
  ads_gmv_daily            GMV 口径日报（交易 × 支付 × 问答 三域对齐到日）
  ads_channel_rank_daily   渠道 GMV 日排行

说明：健康度评分视图在 M5 接入质检引擎后追加（需要引擎产出的质检历史数据）。

用法：
  python foundation/etl/etl_ads.py
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
    "ads_gmv_daily": {
        "sql": """
            WITH pay AS (
                SELECT ds, SUM(gmv) AS gmv, SUM(pay_cnt) AS pay_cnt, SUM(success_cnt) AS success_cnt
                FROM local.dws.dws_pay_daily GROUP BY ds
            ),
            trade AS (
                SELECT ds, SUM(order_cnt) AS order_cnt
                FROM local.dws.dws_trade_daily GROUP BY ds
            ),
            qa AS (
                SELECT ds, SUM(qa_cnt) AS qa_cnt
                FROM local.dws.dws_qa_daily GROUP BY ds
            )
            SELECT COALESCE(p.ds, t.ds, q.ds) AS ds,
                   COALESCE(p.gmv, 0) AS gmv,
                   COALESCE(t.order_cnt, 0) AS order_cnt,
                   COALESCE(p.pay_cnt, 0) AS pay_cnt,
                   CASE WHEN COALESCE(p.pay_cnt, 0) > 0
                        THEN ROUND(COALESCE(p.success_cnt, 0) / p.pay_cnt, 4)
                        ELSE 0 END AS pay_success_rate,
                   COALESCE(q.qa_cnt, 0) AS qa_cnt
            FROM pay p
            FULL OUTER JOIN trade t ON p.ds = t.ds
            FULL OUTER JOIN qa q ON COALESCE(p.ds, t.ds) = q.ds
            ORDER BY ds
        """,
        "ddl": (
            "ds DATE, gmv DECIMAL(24,2), order_cnt BIGINT, pay_cnt BIGINT, "
            "pay_success_rate DECIMAL(8,4), qa_cnt BIGINT"
        ),
        "partition": "days(ds)",
        "casts": {"gmv": "decimal(24,2)", "pay_success_rate": "decimal(8,4)"},
    },
    "ads_channel_rank_daily": {
        "sql": """
            SELECT ds, channel, gmv, pay_cnt,
                   ROW_NUMBER() OVER (PARTITION BY ds ORDER BY gmv DESC) AS rk
            FROM local.dws.dws_pay_daily
        """,
        "ddl": "ds DATE, channel STRING, gmv DECIMAL(20,2), pay_cnt BIGINT, rk INT",
        "partition": "days(ds)",
        "casts": {"gmv": "decimal(20,2)", "rk": "int"},
    },
}


def build_ads(spark, catalog: str = "local", ns: str = "ads") -> dict:
    counts = {}
    for name, meta in TABLES.items():
        table = f"{catalog}.{ns}.{name}"
        df = spark.sql(meta["sql"].replace("local.dws", f"{catalog}.dws"))
        for col_, dtype_ in meta["casts"].items():
            df = df.withColumn(col_, F.col(col_).cast(dtype_))
        spark.sql(f"DROP TABLE IF EXISTS {table}")
        spark.sql(
            f"CREATE TABLE {table} ({meta['ddl']}) USING iceberg PARTITIONED BY ({meta['partition']}) "
            f"TBLPROPERTIES ('read.parquet.vectorization.enabled'='false')"
        )
        df.writeTo(table).append()
        counts[name] = spark.table(table).count()
        print(f"  {table:<28} {counts[name]:>10,} 行")
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description="ADS 高层报表层")
    ap.add_argument("--catalog", default="local")
    args = ap.parse_args()

    spark = build_spark("datapulse-ads")
    try:
        t0 = time.time()
        counts = build_ads(spark, catalog=args.catalog)
        print(f"ADS 构建完成：{sum(counts.values()):,} 行，耗时 {time.time() - t0:.1f}s")
    finally:
        spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
