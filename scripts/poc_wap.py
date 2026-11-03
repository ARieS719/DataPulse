"""M0 风险验证：Iceberg WAP（Write-Audit-Publish）分支写入最小 POC。

验证点（对应立项规格书 §三.3 的可行性前提）：
  1) CREATE BRANCH 语法可用（Spark 3.4+ / Iceberg 1.4+）；
  2) 会话级 spark.wap.branch 使写入进入审计分支，main 分支快照不变（脏数据零暴露）；
  3) fast_forward 原子合并，main 一次性看到全部数据。

若 spark.wap.branch 在本版本不可用，备选方案是直接写分支名：
  INSERT INTO local.poc.<tbl>.branch_audit VALUES ...（同样受支持）。

运行：
  python scripts/poc_wap.py          # 首次运行会拉取 iceberg-spark-runtime jar
输出：
  控制台 PASS/FAIL + docs/poc/wap_poc_result.md 留痕报告
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from foundation.etl.spark import build_spark  # noqa: E402

CHECKS: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    CHECKS.append((bool(ok), label))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


def main() -> int:
    spark = build_spark("datapulse-poc-wap", driver_memory="2g")
    tbl_name = f"wap_orders_{int(time.time())}"
    table = f"local.poc.{tbl_name}"
    try:
        spark.sql(
            f"""
            CREATE TABLE {table} (
                id BIGINT,
                order_time TIMESTAMP,
                channel STRING,
                amount DECIMAL(10,2)
            ) USING iceberg
            PARTITIONED BY (days(order_time))
            TBLPROPERTIES ('write.wap.enabled'='true')
            """
        )

        spark.sql(
            f"""
            INSERT INTO {table} VALUES
            (1, TIMESTAMP '2025-01-01 10:00:00', 'taobao', 100.50),
            (2, TIMESTAMP '2025-01-01 11:00:00', 'douyin', 200.00),
            (3, TIMESTAMP '2025-01-02 09:30:00', 'jd', 59.90)
            """
        )
        main1 = spark.table(table).count()
        check(main1 == 3, f"main 初始快照行数 = {main1}（期望 3）")

        spark.sql(f"ALTER TABLE {table} CREATE BRANCH audit")
        check(True, "CREATE BRANCH audit 执行成功")

        spark.conf.set("spark.wap.branch", "audit")
        spark.sql(
            f"""
            INSERT INTO {table} VALUES
            (4, TIMESTAMP '2025-01-02 12:00:00', 'xiaohongshu', 88.00),
            (5, TIMESTAMP '2025-01-03 10:00:00', 'taobao', 305.20),
            (6, TIMESTAMP '2025-01-03 18:00:00', 'douyin', 12.90)
            """
        )
        spark.conf.unset("spark.wap.branch")

        main_mid = spark.table(table).count()
        check(main_mid == 3, f"WAP 写入后 main 仍 = {main_mid}（期望 3：脏数据未暴露）")
        branch_mid = spark.table(f"{table}.branch_audit").count()
        check(branch_mid == 6, f"审计分支可见全量 = {branch_mid}（期望 6）")

        # 模拟"质检通过"：fastForwardBranch(from, to) 语义 = 把 from 引用快进到 to 的快照，
        # 即 main 追到 audit 的 tip（审计分支数据原子发布到 main）
        spark.sql(f"CALL local.system.fast_forward('poc.{tbl_name}', 'main', 'audit')")
        main_final = spark.table(table).count()
        check(main_final == 6, f"fast_forward 后 main = {main_final}（期望 6：原子发布）")

        spark.sql(f"DROP TABLE IF EXISTS {table}")
    finally:
        spark.stop()

    ok = all(c for c, _ in CHECKS)
    report_dir = REPO_ROOT / "docs" / "poc"
    report_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "# WAP 分支 POC 验证报告",
        "",
        f"- 执行时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        f"- 结论：{'✅ 全部通过' if ok else '❌ 存在失败项'}",
        "",
        "| 检查项 | 结果 |",
        "| --- | --- |",
    ]
    lines += [f"| {label} | {'✅' if ok_ else '❌'} |" for ok_, label in CHECKS]
    lines += ["", "> 本报告由 scripts/poc_wap.py 自动生成，用于里程碑 M0 的风险验证留痕。"]
    (report_dir / "wap_poc_result.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"\n结论：{'✅ WAP 方案可行，阶段二/三可据此落地熔断流水线' if ok else '❌ 见上方 FAIL 项，先解决再进入后续阶段'}")
    print("报告：docs/poc/wap_poc_result.md")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
