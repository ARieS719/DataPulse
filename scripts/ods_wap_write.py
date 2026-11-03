"""M3：以 WAP 模式把待审计数据写入 ODS 审计分支（Airflow DAG 的 ods 阶段）。

写入发生在 audit 分支：main 分支快照不变，脏数据对下游不可见；
质检通过后由 quality_gate_cli 执行 fast_forward 原子合并。

用法：
  python scripts/ods_wap_write.py --raw-dir data/raw
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from foundation.etl.etl_ods import TABLES, load_ods  # noqa: E402
from foundation.etl.spark import build_spark  # noqa: E402

ODS_TABLES = ["dim_user", "fact_order", "fact_payment", "ai_qa_log"]


def _ensure_table(spark, fqn: str, tbl: str) -> None:
    """首次运行（全新 warehouse）自动建空表：批数据只进审计分支，main 空快照，
    质检通过后 fast_forward 原子发布（WAP 语义的完整闭环）。"""
    if spark.catalog.tableExists(fqn):
        return
    meta = TABLES[tbl]
    spark.sql(
        f"CREATE TABLE {fqn} ({meta['ddl']}) USING iceberg "
        f"PARTITIONED BY ({meta['partition']}) TBLPROPERTIES "
        f"('write.wap.enabled'='true', 'read.parquet.vectorization.enabled'='false')"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="WAP 写入 ODS 审计分支")
    ap.add_argument("--raw-dir", default="data/raw")
    ap.add_argument("--branch", default="audit")
    args = ap.parse_args()

    spark = build_spark("datapulse-ods-wap", driver_memory="2g")
    branch = args.branch
    try:
        # 从干净的 main 重建审计分支（幂等；首跑自动建空表）
        for tbl in ODS_TABLES:
            fqn = f"local.ods.{tbl}"
            _ensure_table(spark, fqn, tbl)
            try:
                spark.sql(f"ALTER TABLE {fqn} DROP BRANCH IF EXISTS {branch}")
            except Exception:
                pass
            spark.sql(f"ALTER TABLE {fqn} CREATE BRANCH {branch}")

        spark.conf.set("spark.wap.branch", branch)
        load_ods(spark, raw_dir=args.raw_dir, replace=False)
        spark.conf.unset("spark.wap.branch")
    finally:
        spark.stop()
    print(f"✅ 待审计数据已写入 {branch} 分支（main 未受影响）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
