"""M3：WAP 质量门禁执行器（Spark + Iceberg）—— 把 M2 规则引擎挂到审计分支上。

实现"写入前审计、通过后原子发布"：
  调用契约（与批式流水线配合）：
    1) 调用方先设置 spark.conf.set("spark.wap.branch", "audit") 完成待审计数据的写入；
    2) 调用 run_wap_gate(...)：
       a. 确保审计分支存在（不存在则从 main 创建）；
       b. 编译规则集为单条合并扫描 SQL——主表指向审计分支、引用表读已发布状态；
       c. 在分支上执行扫描 → evaluate_result 得出 PASS/WARN/BLOCK；
       d. PASS/WARN：fast_forward 原子合并到 main 并清理分支；
          BLOCK：保留分支（数据对下游不可见），供排查与回滚；
    3) 返回 GateResult（含 outcome 与本次执行 SQL 留痕）。

首次运行依赖 docs/poc/wap_poc_result.md 已验证的分支语法。
"""

from __future__ import annotations

from dataclasses import dataclass

from dq_engine.rules.engine import compile_ruleset, evaluate_result, load_ruleset
from dq_engine.rules.evaluator import RulesetOutcome

DEFAULT_BRANCH = "audit"


@dataclass
class GateResult:
    outcome: RulesetOutcome
    branch: str
    merged: bool  # 是否已 fast_forward 合并
    sql: str  # 本次执行的合并扫描 SQL（留痕/复盘用）


def run_wap_gate(
    spark,
    ruleset_path: str,
    table_fqn: str,  # 如 "local.dwd.fact_order"
    ref_namespace: str | None = None,  # 引用表命名空间，默认与主表同层
    partition_filter: str | None = None,
    branch: str = DEFAULT_BRANCH,
) -> GateResult:
    catalog, ns, tbl = table_fqn.split(".")
    ruleset = load_ruleset(ruleset_path)
    if ruleset.table != tbl:
        raise ValueError(f"规则集 table={ruleset.table} 与目标表 {tbl} 不一致")

    ref_ns = ref_namespace or ns

    def mapper(name: str) -> str:
        # 主表扫描审计分支（含未合并的待审计数据）；引用表读已发布状态
        if name == ruleset.table:
            return f"{catalog}.{ns}.{name}.branch_{branch}"
        return f"{catalog}.{ref_ns}.{name}"

    compiled = compile_ruleset(ruleset, partition_filter=partition_filter, table_mapper=mapper)

    # 1) 确保审计分支存在（已存在则保留其中已写入的待审计数据）
    try:
        spark.sql(f"ALTER TABLE {table_fqn} CREATE BRANCH {branch}")
    except Exception:
        pass

    # 2) 在审计分支上执行单次合并扫描
    row = spark.sql(compiled.sql).collect()[0].asDict()
    outcome = evaluate_result(compiled, row)

    # 3) 分级熔断决策
    merged = False
    if outcome.verdict in ("PASS", "WARN"):
        # fastForwardBranch(from, to) 语义：把 from 引用快进到 to 的快照（to 不变），
        # 即 main 追到 audit 分支 tip —— 审计分支数据原子发布到 main
        spark.sql(f"CALL {catalog}.system.fast_forward('{ns}.{tbl}', 'main', '{branch}')")
        merged = True
        try:
            spark.sql(f"ALTER TABLE {table_fqn} DROP BRANCH IF EXISTS {branch}")
        except Exception:
            pass
    # BLOCK：分支保留，数据对下游不可见

    return GateResult(outcome=outcome, branch=branch, merged=merged, sql=compiled.sql)
