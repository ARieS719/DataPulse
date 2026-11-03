"""S3：指标口径层（Metrics Layer）—— 统一指标字典与口径 diff。

解决"**同一指标两张报表数字不一致**"这一常见问题的三件套：
  1. MetricRegistry：指标名 → 口径 SQL / 负责人 / 关联表（单一事实来源）；
  2. diff_calibers：SQLGlot AST 对比两个口径，输出差异子句（表/过滤/聚合/列）；
  3. metric_downstream_tables：指标 → 血缘图联动，秒出报表影响面（与 M4 血缘闭环）。

全部纯 Python（SQLGlot），可脱离 Spark 单测。

注意：本模块实现为包（metrics/__init__.py）而非单文件，
以避免与 examples/ 数据目录的同名遮蔽问题。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

import sqlglot
import yaml
from sqlglot import exp

from dq_engine.lineage.graph import LineageGraph


@dataclass
class MetricDef:
    name: str
    owner: str
    caliber_sql: str
    description: str = ""
    tables: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class MetricRegistry:
    """指标字典（内存版；生产持久化到 PostgreSQL，接口不变）。"""

    def __init__(self) -> None:
        self._metrics: dict[str, MetricDef] = {}

    def register(self, metric: MetricDef) -> None:
        if metric.name in self._metrics:
            raise ValueError(f"指标重复注册：{metric.name}")
        self._metrics[metric.name] = metric

    def get(self, name: str) -> MetricDef | None:
        return self._metrics.get(name)

    def list(self) -> list[MetricDef]:
        return list(self._metrics.values())

    def load_yaml(self, path: str | Path) -> int:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        n = 0
        for item in data.get("metrics", []):
            self.register(MetricDef(**item))
            n += 1
        return n

    def dump_yaml(self, path: str | Path) -> None:
        Path(path).write_text(
            yaml.safe_dump({"metrics": [m.to_dict() for m in self.list()]}, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )


# ---------------------------------------------------------------- 口径 diff


def _norm(node) -> str | None:
    if node is None:
        return None
    return node.sql(dialect="spark").lower().replace(" ", "")


def _set_diff(a: set, b: set) -> dict:
    return {"only_left": sorted(a - b), "only_right": sorted(b - a), "common": sorted(a & b)}


@dataclass
class CaliberDiff:
    same: bool
    tables_diff: dict = field(default_factory=dict)
    filters_diff: dict = field(default_factory=dict)
    aggs_diff: dict = field(default_factory=dict)
    columns_diff: dict = field(default_factory=dict)
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def diff_calibers(sql_a: str, sql_b: str) -> CaliberDiff:
    """对比两个指标口径 SQL 的 AST，定位差异维度（表/过滤/聚合/列）。"""
    a = sqlglot.parse_one(sql_a)
    b = sqlglot.parse_one(sql_b)

    tables_a = {t.name for t in a.find_all(exp.Table)}
    tables_b = {t.name for t in b.find_all(exp.Table)}
    aggs_a = {type(x).__name__ for x in a.find_all(exp.AggFunc)}
    aggs_b = {type(x).__name__ for x in b.find_all(exp.AggFunc)}
    cols_a = {c.name for c in a.find_all(exp.Column)}
    cols_b = {c.name for c in b.find_all(exp.Column)}

    wa, wb = _norm(a.find(exp.Where)), _norm(b.find(exp.Where))
    filters_same = wa == wb

    same = tables_a == tables_b and aggs_a == aggs_b and cols_a == cols_b and filters_same

    detail_parts = []
    if tables_a != tables_b:
        detail_parts.append(f"表差异：左={sorted(tables_a)} 右={sorted(tables_b)}")
    if not filters_same:
        detail_parts.append(f"过滤差异：左[{wa}] 右[{wb}]")
    if aggs_a != aggs_b:
        detail_parts.append(f"聚合差异：左={sorted(aggs_a)} 右={sorted(aggs_b)}")
    if cols_a != cols_b:
        detail_parts.append(f"列差异：左={sorted(cols_a)} 右={sorted(cols_b)}")

    return CaliberDiff(
        same=same,
        tables_diff=_set_diff(tables_a, tables_b),
        filters_diff={"left": wa, "right": wb, "same": filters_same},
        aggs_diff=_set_diff(aggs_a, aggs_b),
        columns_diff=_set_diff(cols_a, cols_b),
        detail="；".join(detail_parts) or "口径完全一致",
    )


def metric_downstream_tables(metric: MetricDef, graph: LineageGraph) -> list[str]:
    """指标 → 血缘：引用表的所有下游表（报表影响面，供"口径变更影响评估"）。"""
    out: list[str] = []
    for t in metric.tables:
        for d in graph.table_downstream(t):
            if d not in out:
                out.append(d)
    return out


__all__ = ["CaliberDiff", "MetricDef", "MetricRegistry", "diff_calibers", "metric_downstream_tables"]
