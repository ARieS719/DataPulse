"""YAML 规则集 → 单条合并聚合 SQL 编译器（M2 核心）。

合并扫描原理（面试要点）：
  同一主表上的 N 条规则被编译为一条 SQL 中的 N 个聚合表达式，
  聚合函数在同一个 TableScan 内并行求值——规则数量增加不增加全表扫描次数；
  跨表规则（fk_integrity / sum_align）编译为标量子查询预聚合，同样并入这条 SQL。

SQL 方言兼容目标：
  * Spark SQL —— 生产执行器（M3 在 Iceberg 审计分支上执行）
  * SQLite —— 单元测试语义验证（无 Spark 依赖地证明合并扫描正确性）

NULL 语义约定（面试可展开）：
  * not_null / unique 直接统计 NULL；
  * accepted_values：NULL 计入不匹配（更严格）；
  * range：NULL 不违规（由 not_null 单独管辖，职责分离）；
  * fk_integrity：NULL 外键计入孤儿。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from dq_engine.rules.schema import (
    AcceptedValuesRule,
    FkIntegrityRule,
    NotNullRule,
    RangeRule,
    RowCountRule,
    Rule,
    Ruleset,
    SumAlignRule,
    TimelinessRule,
    UniqueRule,
)

TOTAL_ALIAS = "__total_rows"


@dataclass
class CompiledMetric:
    alias: str
    rule: Rule


@dataclass
class CompiledQuery:
    table: str
    sql: str
    metrics: list[CompiledMetric] = field(default_factory=list)
    partition_filter: str | None = None


def _lit(v) -> str:
    """SQL 字面量转义（字符串单引号翻倍；数值原样输出）。"""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def _metric_expr(rule: Rule, main_table: str, mapper: Callable[[str], str]) -> str:
    if isinstance(rule, NotNullRule):
        return f"SUM(CASE WHEN {rule.column} IS NULL THEN 1 ELSE 0 END)"
    if isinstance(rule, UniqueRule):
        return f"COUNT(*) - COUNT(DISTINCT {rule.column})"
    if isinstance(rule, AcceptedValuesRule):
        vals = ", ".join(_lit(v) for v in rule.values)
        return f"SUM(CASE WHEN {rule.column} IN ({vals}) THEN 1 ELSE 0 END)"
    if isinstance(rule, RangeRule):
        conds = []
        if rule.min is not None:
            conds.append(f"{rule.column} < {_lit(rule.min)}")
        if rule.max is not None:
            conds.append(f"{rule.column} > {_lit(rule.max)}")
        cond = " OR ".join(conds)
        return f"SUM(CASE WHEN {rule.column} IS NOT NULL AND ({cond}) THEN 1 ELSE 0 END)"
    if isinstance(rule, RowCountRule):
        return "COUNT(*)"
    if isinstance(rule, TimelinessRule):
        return f"CASE WHEN MAX({rule.column}) >= '{rule.expect_not_before}' THEN 1 ELSE 0 END"
    if isinstance(rule, FkIntegrityRule):
        return (
            "(SELECT (SUM(CASE WHEN r.__ref IS NULL THEN 1 ELSE 0 END) * 1.0) / NULLIF(COUNT(*), 0) "
            f"FROM {main_table} f "
            f"LEFT JOIN (SELECT DISTINCT {rule.ref_column} AS __ref FROM {mapper(rule.ref_table)}) r "
            f"ON f.{rule.fk_column} = r.__ref)"
        )
    if isinstance(rule, SumAlignRule):
        agg = rule.agg_expr if rule.agg_expr is not None else rule.agg_column
        main_where = f" WHERE {rule.agg_filter}" if rule.agg_filter else ""
        return (
            "(SELECT ABS(a.__s - b.__s) / NULLIF(ABS(a.__s) + ABS(b.__s), 0) "
            f"FROM (SELECT COALESCE(SUM({agg}), 0) AS __s FROM {main_table}{main_where}) a "
            f"CROSS JOIN (SELECT COALESCE(SUM({rule.ref_agg_column}), 0) AS __s FROM {mapper(rule.ref_table)}) b)"
        )
    raise TypeError(f"未知规则类型: {type(rule)}")


def compile_ruleset(
    ruleset: Ruleset,
    partition_filter: str | None = None,
    table_mapper: Callable[[str], str] | None = None,
) -> CompiledQuery:
    """把整个规则集编译为一条 SQL。

    partition_filter：可选分区裁剪条件（由执行器传入，如 "ds = '2025-01-02'"），
    仅作用于主表扫描；跨表标量子查询默认全量引用（子查询自身的扫描与主扫描共享同一 Job）。
    table_mapper：逻辑表名 → 全限定名（如 lambda t: f"local.dwd.{t}"）。
    """
    mapper = table_mapper or (lambda t: t)
    main = mapper(ruleset.table)

    exprs = [f"COUNT(*) AS {TOTAL_ALIAS}"]
    metrics: list[CompiledMetric] = []
    for i, rule in enumerate(ruleset.rules):
        alias = f"__m{i}"
        exprs.append(f"{_metric_expr(rule, main, mapper)} AS {alias}")
        metrics.append(CompiledMetric(alias=alias, rule=rule))

    where = f" WHERE {partition_filter}" if partition_filter else ""
    sql = "SELECT " + ",\n       ".join(exprs) + f"\nFROM {main}{where}"
    return CompiledQuery(table=ruleset.table, sql=sql, metrics=metrics, partition_filter=partition_filter)
