"""rules：YAML 规则契约与单次扫描编译器（M2）。

已实现：
  schema.py     8 类规则的 pydantic 强类型契约（加载即校验）
  compiler.py   N 条规则 → 单条合并聚合 SQL（含跨表标量子查询）
  evaluator.py  聚合结果 → 逐规则判定 + P0/P1 分级熔断决策
  engine.py     高层 API：load → compile → evaluate
  examples/     示例契约（fact_order / dim_user）

验证：tests/test_rules_compiler.py（含 SQLite 真实执行端到端语义测试）。
M3 将接入 Spark 执行器与 WAP 审计分支。
"""

from dq_engine.rules.engine import (
    CompiledQuery,
    RulesetOutcome,
    compile_ruleset,
    evaluate_result,
    load_ruleset,
    run_ruleset,
)
from dq_engine.rules.schema import Ruleset

__all__ = [
    "CompiledQuery",
    "Ruleset",
    "RulesetOutcome",
    "compile_ruleset",
    "evaluate_result",
    "load_ruleset",
    "run_ruleset",
]
