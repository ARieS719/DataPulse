"""M1 里程碑：ODS → DWD 明细清洗层。

清洗规则（对应立项规格书 §三.1 与 docs/architecture.md §1）：
  * 空值规整：age NULL→-1、gender NULL→'U'、city_tier NULL→0、latency_ms NULL→0
  * 匿名化：user_id → user_key（sha256）；ODS 保留原始值，下游只消费 user_key
  * 枚举标准化：状态/渠道/意图字段 trim 后白名单校验，白名单外归为 '其他'
  * 数值规整：金额 NULL 或 <=0 → 0

用法：
  python foundation/etl/etl_dwd.py
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pyspark.sql import functions as F  # noqa: E402

from foundation.etl.spark import build_spark  # noqa: E402

STATUS_WHITELIST = ("已支付", "待支付", "已取消", "已退款")
PAY_STATUS_WHITELIST = ("成功", "失败")
INTENT_WHITELIST = ("商品咨询", "价格咨询", "物流查询", "售后处理", "闲聊")

TABLES = {
    "dim_user": {
        "ddl": (
            "user_key STRING, register_time TIMESTAMP, age INT, gender STRING, "
            "city_tier INT, channel_source STRING, is_vip INT"
        ),
        "partition": "days(register_time)",
    },
    "fact_order": {
        "ddl": (
            "order_id STRING, user_key STRING, order_time TIMESTAMP, channel STRING, "
            "category STRING, amount DECIMAL(12,2), coupon_discount DECIMAL(12,2), status STRING"
        ),
        "partition": "days(order_time)",
    },
    "fact_payment": {
        "ddl": (
            "payment_id STRING, order_id STRING, user_key STRING, pay_time TIMESTAMP, "
            "pay_channel STRING, pay_amount DECIMAL(12,2), currency STRING, pay_status STRING"
        ),
        "partition": "days(pay_time)",
    },
    "ai_qa_log": {
        "ddl": (
            "qa_id STRING, user_key STRING, session_time TIMESTAMP, intent STRING, "
            "question STRING, answer STRING, model_version STRING, latency_ms INT, session_len INT"
        ),
        "partition": "days(session_time)",
    },
}


def _dim_user(spark, catalog: str):
    df = spark.table(f"{catalog}.ods.dim_user")
    return df.select(
        F.sha2(F.col("user_id"), 256).alias("user_key"),
        F.col("register_time"),
        F.when(F.col("age").isNull(), -1).otherwise(F.col("age")).alias("age"),
        F.when(F.col("gender").isNull(), F.lit("U")).otherwise(F.col("gender")).alias("gender"),
        F.when(F.col("city_tier").isNull(), 0).otherwise(F.col("city_tier")).alias("city_tier"),
        F.col("channel_source"),
        F.col("is_vip"),
    )


def _fact_order(spark, catalog: str):
    df = spark.table(f"{catalog}.ods.fact_order")
    status = F.when(F.trim(F.col("status")).isin(*STATUS_WHITELIST), F.trim(F.col("status"))).otherwise(
        F.lit("其他")
    )
    amount = F.when(F.col("amount").isNull() | (F.col("amount") <= 0), F.lit(0)).otherwise(F.col("amount"))
    coupon = F.when(
        F.col("coupon_discount").isNull() | (F.col("coupon_discount") < 0), F.lit(0)
    ).otherwise(F.col("coupon_discount"))
    return df.select(
        F.col("order_id"),
        F.sha2(F.col("user_id"), 256).alias("user_key"),
        F.col("order_time"),
        F.col("channel"),
        F.col("category"),
        amount.alias("amount"),
        coupon.alias("coupon_discount"),
        status.alias("status"),
    )


def _fact_payment(spark, catalog: str):
    df = spark.table(f"{catalog}.ods.fact_payment")
    pay_status = F.when(
        F.trim(F.col("pay_status")).isin(*PAY_STATUS_WHITELIST), F.trim(F.col("pay_status"))
    ).otherwise(F.lit("其他"))
    pay_amount = F.when(F.col("pay_amount").isNull() | (F.col("pay_amount") < 0), F.lit(0)).otherwise(
        F.col("pay_amount")
    )
    return df.select(
        F.col("payment_id"),
        F.col("order_id"),
        F.sha2(F.col("user_id"), 256).alias("user_key"),
        F.col("pay_time"),
        F.col("pay_channel"),
        pay_amount.alias("pay_amount"),
        F.upper(F.coalesce(F.col("currency"), F.lit("CNY"))).alias("currency"),
        pay_status.alias("pay_status"),
    )


def _ai_qa_log(spark, catalog: str):
    df = spark.table(f"{catalog}.ods.ai_qa_log")
    intent = F.when(F.trim(F.col("intent")).isin(*INTENT_WHITELIST), F.trim(F.col("intent"))).otherwise(
        F.lit("其他")
    )
    return df.select(
        F.col("qa_id"),
        F.sha2(F.col("user_id"), 256).alias("user_key"),
        F.col("session_time"),
        intent.alias("intent"),
        F.col("question"),
        F.col("answer"),
        F.col("model_version"),
        F.coalesce(F.col("latency_ms"), F.lit(0)).alias("latency_ms"),
        F.coalesce(F.col("session_len"), F.lit(1)).alias("session_len"),
    )


BUILDERS = {
    "dim_user": _dim_user,
    "fact_order": _fact_order,
    "fact_payment": _fact_payment,
    "ai_qa_log": _ai_qa_log,
}


def build_dwd(spark, catalog: str = "local", ns: str = "dwd") -> dict:
    counts = {}
    for name, meta in TABLES.items():
        table = f"{catalog}.{ns}.{name}"
        spark.sql(f"DROP TABLE IF EXISTS {table}")
        spark.sql(
            f"CREATE TABLE {table} ({meta['ddl']}) USING iceberg PARTITIONED BY ({meta['partition']}) "
            f"TBLPROPERTIES ('read.parquet.vectorization.enabled'='false')"
        )
        BUILDERS[name](spark, catalog).writeTo(table).append()
        counts[name] = spark.table(table).count()
        print(f"  {table:<28} {counts[name]:>10,} 行")
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description="DWD 明细清洗层")
    ap.add_argument("--catalog", default="local")
    args = ap.parse_args()

    spark = build_spark("datapulse-dwd")
    try:
        t0 = time.time()
        counts = build_dwd(spark, catalog=args.catalog)
        print(f"DWD 构建完成：{sum(counts.values()):,} 行，耗时 {time.time() - t0:.1f}s")
    finally:
        spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
