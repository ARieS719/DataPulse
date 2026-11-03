"""规则引擎高层 API（M2 里程碑）。

典型用法（M3 的 Spark 执行器照此调用）：

    rs = load_ruleset("rules/fact_order.rules.yaml")
    cq = compile_ruleset(rs, partition_filter="ds = '2025-01-02'",
                         table_mapper=lambda t: f"local.dwd.{t}")
    row = spark.sql(cq.sql).collect()[0].asDict()   # 单行聚合结果
    outcome = evaluate_result(cq, row)              # 逐规则判定 + P0/P1 分级
    if outcome.verdict == "BLOCK": ...              # 熔断：分支不合并

本模块（schema/compiler/evaluator）全部为纯 Python，不依赖 Spark——
单元测试用 SQLite 真实执行生成的 SQL 完成语义验证。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import yaml

from dq_engine.rules.compiler import CompiledQuery, compile_ruleset
from dq_engine.rules.evaluator import RulesetOutcome, evaluate_result
from dq_engine.rules.schema import Ruleset

__all__ = ["load_ruleset", "compile_ruleset", "evaluate_result", "CompiledQuery", "RulesetOutcome", "Ruleset"]


def load_ruleset(path: str | Path) -> Ruleset:
    """加载并校验 YAML 规则契约（语法错误/非法参数在此时被拒绝）。"""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Ruleset.model_validate(data)


def run_ruleset(
    ruleset: Ruleset,
    result_row: dict,
    partition_filter: str | None = None,
    table_mapper: Callable[[str], str] | None = None,
) -> RulesetOutcome:
    """便捷入口：编译 + 判定（result_row 为合并扫描 SQL 的单行执行结果）。"""
    compiled = compile_ruleset(ruleset, partition_filter=partition_filter, table_mapper=table_mapper)
    return evaluate_result(compiled, result_row)
