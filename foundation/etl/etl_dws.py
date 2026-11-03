"""M1 里程碑：DWD → DWS 轻度汇总层（日 × 渠道/意图 粒度）。

产出：
  dws_trade_daily  按日×渠道：订单量 / 用户数 / 订单金额 / 客单价
  dws_pay_daily    按日×支付渠道：支付笔数 / GMV / 支付成功笔数（GMV = 支付成功金额）
  dws_qa_daily     按日×意图：问答量 / 平均延迟 / 用户数

用法：
  python foundation/etl/etl_dws.py
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
    "dws_trade_daily": {
        "sql": """
            SELECT date(order_time) AS ds, channel,
                   COUNT(*) AS order_cnt,
                   COUNT(DISTINCT user_key) AS user_cnt,
                   SUM(amount) AS order_amount,
                   ROUND(AVG(amount), 2) AS avg_order_amount
            FROM local.dwd.fact_order
            WHERE status = '已支付'
            GROUP BY date(order_time), channel
        """,
        "ddl": (
            "ds DATE, channel STRING, order_cnt BIGINT, user_cnt BIGINT, "
            "order_amount DECIMAL(20,2), avg_order_amount DECIMAL(20,2)"
        ),
        "partition": "days(ds)",
        "casts": {"order_amount": "decimal(20,2)", "avg_order_amount": "decimal(20,2)"},
    },
    "dws_pay_daily": {
        "sql": """
            SELECT date(pay_time) AS ds, pay_channel AS channel,
                   COUNT(*) AS pay_cnt,
                   SUM(CASE WHEN pay_status = '成功' THEN pay_amount ELSE 0 END) AS gmv,
                   COUNT(DISTINCT user_key) AS pay_user_cnt,
                   SUM(CASE WHEN pay_status = '成功' THEN 1 ELSE 0 END) AS success_cnt
            FROM local.dwd.fact_payment
            GROUP BY date(pay_time), pay_channel
        """,
        "ddl": "ds DATE, channel STRING, pay_cnt BIGINT, gmv DECIMAL(20,2), pay_user_cnt BIGINT, success_cnt BIGINT",
        "partition": "days(ds)",
        "casts": {"gmv": "decimal(20,2)"},
    },
    "dws_qa_daily": {
        "sql": """
            SELECT date(session_time) AS ds, intent,
                   COUNT(*) AS qa_cnt,
                   ROUND(AVG(latency_ms), 1) AS avg_latency_ms,
                   COUNT(DISTINCT user_key) AS user_cnt
            FROM local.dwd.ai_qa_log
            GROUP BY date(session_time), intent
        """,
        "ddl": "ds DATE, intent STRING, qa_cnt BIGINT, avg_latency_ms DECIMAL(10,1), user_cnt BIGINT",
        "partition": "days(ds)",
        "casts": {"avg_latency_ms": "decimal(10,1)"},
    },
}


def build_dws(spark, catalog: str = "local", ns: str = "dws") -> dict:
    counts = {}
    for name, meta in TABLES.items():
        table = f"{catalog}.{ns}.{name}"
        df = spark.sql(meta["sql"].replace("local.dwd", f"{catalog}.dwd"))
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
    ap = argparse.ArgumentParser(description="DWS 轻度汇总层")
    ap.add_argument("--catalog", default="local")
    args = ap.parse_args()

    spark = build_spark("datapulse-dws")
    try:
        t0 = time.time()
        counts = build_dws(spark, catalog=args.catalog)
        print(f"DWS 构建完成：{sum(counts.values()):,} 行，耗时 {time.time() - t0:.1f}s")
    finally:
        spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
