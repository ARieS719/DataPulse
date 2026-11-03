"""M1 验收对账：DWS 聚合金额 vs DWD 明细金额（跨层一致性）。

用法：
  python scripts/reconcile.py
输出：
  控制台对账结果 + docs/benchmarks/reconcile_result.md 留痕
"""

from __future__ import annotations

import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from foundation.etl.spark import build_spark  # noqa: E402

CHECKS: list[tuple[bool, str]] = []


def _num(v) -> Decimal | None:
    if v is None:
        return None
    return Decimal(str(v))


def check(ok: bool, label: str) -> None:
    CHECKS.append((bool(ok), label))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


def main() -> int:
    spark = build_spark("datapulse-reconcile", driver_memory="2g")
    try:
        # 1) 交易额：DWS 聚合 == DWD 明细（已支付订单）
        dwd_sum = _num(spark.sql("SELECT SUM(amount) FROM local.dwd.fact_order WHERE status = '已支付'").collect()[0][0])
        dws_sum = _num(spark.sql("SELECT SUM(order_amount) FROM local.dws.dws_trade_daily").collect()[0][0])
        check(
            dwd_sum is not None and dws_sum is not None and abs(dwd_sum - dws_sum) < Decimal("0.01"),
            f"DWS 交易额 {dws_sum} == DWD 明细合计 {dwd_sum}",
        )

        # 2) GMV：DWS 支付 GMV == DWD 成功流水金额合计
        dwd_gmv = _num(
            spark.sql("SELECT SUM(pay_amount) FROM local.dwd.fact_payment WHERE pay_status = '成功'").collect()[0][0]
        )
        dws_gmv = _num(spark.sql("SELECT SUM(gmv) FROM local.dws.dws_pay_daily").collect()[0][0])
        check(
            dwd_gmv is not None and dws_gmv is not None and abs(dwd_gmv - dws_gmv) < Decimal("0.01"),
            f"DWS GMV {dws_gmv} == DWD 成功流水合计 {dwd_gmv}",
        )

        # 3) 行数守恒：DWD 订单行数 == ODS 订单行数
        ods_rows = spark.table("local.ods.fact_order").count()
        dwd_rows = spark.table("local.dwd.fact_order").count()
        check(ods_rows == dwd_rows, f"DWD 行数 {dwd_rows} == ODS 行数 {ods_rows}")
    finally:
        spark.stop()

    ok = all(c for c, _ in CHECKS)
    out = REPO_ROOT / "docs" / "benchmarks"
    out.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 跨层对账结果（M1 验收）",
        "",
        f"- 执行时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        f"- 结论：{'✅ 全部一致' if ok else '❌ 存在差异'}",
        "",
        "| 检查项 | 结果 |",
        "| --- | --- |",
    ]
    lines += [f"| {label} | {'✅' if ok_ else '❌'} |" for ok_, label in CHECKS]
    (out / "reconcile_result.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"结论：{'✅ 四层链路数据一致' if ok else '❌ 见上方差异'}")
    print("留痕：docs/benchmarks/reconcile_result.md")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
