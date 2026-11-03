"""单行聚合结果 → 逐规则判定（纯 Python，无 Spark 依赖）。

evaluate_result(compiled, row) 接收合并扫描 SQL 的执行结果（单行 dict），
输出每条规则的 pass/fail 与可读详情，并聚合出 P0/P1 分级结论——
P0 任一失败即触发熔断（M3），P1 失败仅告警放行。

空表策略：total_rows == 0 时比率类规则跳过并标记通过
（行数类规则仍正常判定，如 min_rows > 0 会失败）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field

from dq_engine.rules.compiler import TOTAL_ALIAS, CompiledQuery
from dq_engine.rules.schema import (
    AcceptedValuesRule,
    FkIntegrityRule,
    NotNullRule,
    RangeRule,
    RowCountRule,
    Rule,
    SumAlignRule,
    TimelinessRule,
    UniqueRule,
)


@dataclass(frozen=True)
class RuleOutcome:
    rule_name: str
    rule_type: str
    level: str
    passed: bool
    metric: float | None
    threshold: str
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RulesetOutcome:
    table: str
    total_rows: int
    outcomes: list[RuleOutcome] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(o.passed for o in self.outcomes)

    @property
    def p0_failed(self) -> list[RuleOutcome]:
        return [o for o in self.outcomes if not o.passed and o.level == "P0"]

    @property
    def p1_failed(self) -> list[RuleOutcome]:
        return [o for o in self.outcomes if not o.passed and o.level == "P1"]

    @property
    def verdict(self) -> str:
        """分级熔断决策：P0 失败 → BLOCK；否则 P1 失败 → WARN；全过 → PASS。"""
        if self.p0_failed:
            return "BLOCK"
        if self.p1_failed:
            return "WARN"
        return "PASS"

    def to_dict(self) -> dict:
        return {
            "table": self.table,
            "total_rows": self.total_rows,
            "verdict": self.verdict,
            "p0_failed": [o.to_dict() for o in self.p0_failed],
            "p1_failed": [o.to_dict() for o in self.p1_failed],
            "outcomes": [o.to_dict() for o in self.outcomes],
        }


def _num(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _judge(rule: Rule, total: int, metric: float | None) -> RuleOutcome:
    t = rule.type
    name, level = rule.name, rule.level

    if isinstance(rule, NotNullRule):
        if total == 0:
            return RuleOutcome(name, t, level, True, None, f"填充率 >= {rule.min_fill_ratio}", "空表跳过")
        nulls = metric or 0.0
        fill = 1.0 - nulls / total
        passed = fill >= rule.min_fill_ratio
        return RuleOutcome(
            name, t, level, passed, round(fill, 6), f"填充率 >= {rule.min_fill_ratio}",
            f"空值 {int(nulls)}/{total}，填充率 {fill:.4f}",
        )

    if isinstance(rule, UniqueRule):
        dups = int(metric or 0)
        return RuleOutcome(
            name, t, level, dups == 0, float(dups), "重复行数 = 0",
            f"重复行 {dups} 行（基于 {rule.column}）",
        )

    if isinstance(rule, AcceptedValuesRule):
        if total == 0:
            return RuleOutcome(name, t, level, True, None, f"白名单覆盖率 >= {rule.min_match_ratio}", "空表跳过")
        matched = metric or 0.0
        ratio = matched / total
        passed = ratio >= rule.min_match_ratio
        return RuleOutcome(
            name, t, level, passed, round(ratio, 6), f"白名单覆盖率 >= {rule.min_match_ratio}",
            f"匹配 {int(matched)}/{total}，覆盖率 {ratio:.4f}（NULL 计入不匹配）",
        )

    if isinstance(rule, RangeRule):
        if total == 0:
            return RuleOutcome(name, t, level, True, None, f"违规比例 <= {rule.max_violation_ratio}", "空表跳过")
        violations = metric or 0.0
        ratio = violations / total
        passed = ratio <= rule.max_violation_ratio
        return RuleOutcome(
            name, t, level, passed, round(ratio, 6), f"违规比例 <= {rule.max_violation_ratio}",
            f"越界 {int(violations)}/{total}，比例 {ratio:.4f}",
        )

    if isinstance(rule, RowCountRule):
        lo_ok = rule.min_rows is None or total >= rule.min_rows
        hi_ok = rule.max_rows is None or total <= rule.max_rows
        passed = lo_ok and hi_ok
        bounds = f"[{rule.min_rows if rule.min_rows is not None else 0}, {rule.max_rows if rule.max_rows is not None else '∞'}]"
        return RuleOutcome(name, t, level, passed, float(total), f"行数 ∈ {bounds}", f"当前 {total} 行")

    if isinstance(rule, TimelinessRule):
        flag = metric or 0.0
        return RuleOutcome(
            name, t, level, flag == 1.0, flag, f"最新时间 >= {rule.expect_not_before}",
            "通过" if flag == 1.0 else f"最新时间早于 {rule.expect_not_before}（数据可能未及时更新）",
        )

    if isinstance(rule, FkIntegrityRule):
        if metric is None:
            return RuleOutcome(name, t, level, True, None, f"孤儿比例 <= {rule.max_orphan_ratio}", "空表跳过")
        ratio = metric
        passed = ratio <= rule.max_orphan_ratio
        return RuleOutcome(
            name, t, level, passed, round(ratio, 6), f"孤儿比例 <= {rule.max_orphan_ratio}",
            f"孤儿外键比例 {ratio:.4f}（{rule.fk_column} → {rule.ref_table}.{rule.ref_column}，NULL 计入孤儿）",
        )

    if isinstance(rule, SumAlignRule):
        if metric is None:
            return RuleOutcome(name, t, level, True, None, f"对称差比例 <= {rule.max_diff_ratio}", "任一侧合计为 0，跳过")
        ratio = metric
        passed = ratio <= rule.max_diff_ratio
        agg_label = rule.agg_expr if rule.agg_expr is not None else rule.agg_column
        return RuleOutcome(
            name, t, level, passed, round(ratio, 6), f"对称差比例 <= {rule.max_diff_ratio}",
            f"跨表金额对称差 {ratio:.4f}（{agg_label} vs {rule.ref_table}.{rule.ref_agg_column}）",
        )

    raise TypeError(f"未知规则类型: {type(rule)}")


def evaluate_result(compiled: CompiledQuery, row: Mapping) -> RulesetOutcome:
    total = int(row.get(TOTAL_ALIAS) or 0)
    outcome = RulesetOutcome(table=compiled.table, total_rows=total)
    for m in compiled.metrics:
        outcome.outcomes.append(_judge(m.rule, total, _num(row.get(m.alias))))
    return outcome
