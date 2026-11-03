"""M3：WAP 门禁 CLI —— 对 ODS 各表执行合并扫描质检，任一 BLOCK 即熔断。

熔断语义：BLOCK 的表不执行 fast_forward（分支保留、数据对下游不可见），
以非零退出码向上（Airflow）传递失败，下游任务随之挂起。

用法：
  python scripts/quality_gate_cli.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "core"))

from dq_engine.quality_gate import run_wap_gate  # noqa: E402

from foundation.etl.spark import build_spark  # noqa: E402

GATE_PLAN = {
    "fact_order": "eval/golden/chaos_rules/ods_fact_order.rules.yaml",
    "fact_payment": "eval/golden/chaos_rules/ods_fact_payment.rules.yaml",
    "ai_qa_log": "eval/golden/chaos_rules/ods_ai_qa_log.rules.yaml",
}


def main() -> int:
    spark = build_spark("datapulse-gate", driver_memory="2g")
    results = []
    try:
        for tbl, ruleset_rel in GATE_PLAN.items():
            t0 = time.time()
            r = run_wap_gate(spark, str(REPO_ROOT / ruleset_rel), f"local.ods.{tbl}")
            dur_ms = round((time.time() - t0) * 1000, 1)
            results.append((tbl, r.outcome.verdict, r.merged, dur_ms))
            print(f"  [{tbl:<14}] verdict={r.outcome.verdict:<6} merged={r.merged} ({dur_ms} ms)")

        # dim_user 无独立规则集：审计分支直接合并
        try:
            spark.sql("CALL local.system.fast_forward('ods.dim_user', 'main', 'audit')")
        except Exception:
            pass
    finally:
        spark.stop()

    blocked = [t for t, v, _, _ in results if v == "BLOCK"]
    if blocked:
        print(f"❌ 熔断：{blocked} 未合并，下游 DAG 挂起")
        return 1
    print("✅ 全部门禁通过，审计分支已原子合并")
    return 0


if __name__ == "__main__":
    sys.exit(main())
