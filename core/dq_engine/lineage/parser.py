"""M4：SQLGlot AST 字段级血缘解析器（纯 Python，无外部图数据库）。

解析能力（自研实现，直接操作 sqlglot AST，对应立项规格书 §三.4）：
  * 投影血缘：SELECT 的每个输出列 → 来源列集合（含别名、表达式、聚合、CASE）
  * 跨作用域消解：JOIN 别名限定（t.col）、CTE、嵌套子查询、子查询通配符展开
  * 表级/字段级双粒度；常量列（无来源）自然产生零边
  * WHERE/JOIN ON 条件列不计入字段映射（它们影响行集而非字段来源，面试可展开）

已知边界（诚实声明，面试主动交代）：
  * 未限定列在多表 JOIN 中按"唯一子查询输出匹配 → 否则第一来源"确定性归因；
    生产级消歧需 schema + qualify（sqlglot optimizer），见 docs/production.md；
  * 递归 CTE / UNION / 复杂 window 归因不在本版本范围。

用法：
  extractor = LineageExtractor(dialect="spark")
  lineage = extractor.extract(sql, target_table="dws_trade_daily")
  for e in lineage.edges: ...
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

MAX_DEPTH = 8


@dataclass(frozen=True)
class ColumnRef:
    table: str
    column: str


@dataclass(frozen=True)
class ColumnEdge:
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    expression: str


@dataclass
class QueryLineage:
    target_table: str
    edges: list[ColumnEdge] = field(default_factory=list)

    @property
    def source_tables(self) -> list[str]:
        return sorted({e.source_table for e in self.edges})


@dataclass
class _Source:
    alias: str
    kind: str  # table / subquery / derived
    name: str  # 表名（kind=table 时有效；CTE 名也可存这里）
    node: object  # exp.Table / exp.Subquery / CTE query


def _output_name(proj: exp.Expression, dialect: str | None) -> str:
    if isinstance(proj, exp.Alias):
        return proj.alias
    if isinstance(proj, exp.Column):
        return proj.name
    return proj.sql(dialect=dialect)


def _select_of(node) -> exp.Select | None:
    if isinstance(node, exp.Select):
        return node
    if isinstance(node, exp.Subquery):
        return node.this if isinstance(node.this, exp.Select) else None
    return None


class LineageExtractor:
    def __init__(self, dialect: str | None = None, schema: dict | None = None):
        """dialect：如 "spark"；schema：{表名: [列名...]}，用于通配符展开（缺省时通配符留痕为 *）。"""
        self.dialect = dialect
        self.schema = schema or {}
        self._ctes: dict[str, object] = {}

    # ------------------------------------------------------------ 入口

    def extract(self, sql: str, target_table: str) -> QueryLineage:
        self._ctes = {}
        tree = sqlglot.parse_one(sql, read=self.dialect)
        select = _select_of(tree)
        if select is None and isinstance(tree, exp.Create):
            select = _select_of(tree.expression)  # 支持 CREATE TABLE ... AS SELECT
        if select is None:
            raise ValueError(f"未识别出 SELECT 查询: {sql[:60]!r}...")
        self._collect_ctes(select)
        lineage = QueryLineage(target_table=target_table)
        self._walk(select, target_table, lineage.edges, depth=0)
        return lineage

    # ------------------------------------------------------------ CTE

    def _collect_ctes(self, tree) -> None:
        # sqlglot 26+ 将 FROM 等关键字参数键改为 from_/with_，双键兼容（见 debug_sqlglot.py 实证）
        with_expr = tree.args.get("with") or tree.args.get("with_")
        if with_expr is None:
            return
        for cte in with_expr.expressions or []:
            if isinstance(cte, exp.CTE):
                self._ctes[cte.alias] = cte.this

    # ------------------------------------------------------------ 主遍历

    def _walk(self, select: exp.Select, target_table: str, edges: list[ColumnEdge], depth: int) -> None:
        if depth > MAX_DEPTH:
            return
        sources = self._sources(select)
        for proj in select.expressions:
            if isinstance(proj, exp.Star):
                self._expand_star(proj, sources, target_table, edges, depth)
                continue
            out_name = _output_name(proj, self.dialect)
            for col in proj.find_all(exp.Column):
                refs = self._resolve_column(col, sources, depth)
                for ref in refs:
                    edges.append(
                        ColumnEdge(
                            source_table=ref.table,
                            source_column=ref.column,
                            target_table=target_table,
                            target_column=out_name,
                            expression=proj.sql(dialect=self.dialect),
                        )
                    )

    def _expand_star(self, proj: exp.Star, sources: list[_Source], target_table: str, edges, depth: int) -> None:
        for s in sources:
            if s.kind == "table" and s.name not in self._ctes:
                cols = self.schema.get(s.name)
                if cols:
                    for c in cols:
                        edges.append(ColumnEdge(s.name, c, target_table, c, "*"))
                else:
                    edges.append(ColumnEdge(s.name, "*", target_table, "*", "*"))
            else:
                sel = _select_of(self._ctes.get(s.name) if s.kind == "table" else s.node)
                if sel is None:
                    continue
                for out_name in self._outputs(sel):
                    refs = self._resolve_source_column(s, out_name, depth + 1)
                    for ref in refs:
                        edges.append(ColumnEdge(ref.table, ref.column, target_table, out_name, "*"))

    # ------------------------------------------------------------ 来源解析

    def _sources(self, select: exp.Select) -> list[_Source]:
        out: list[_Source] = []
        # sqlglot 26+ 将参数键 from 改为 from_，双键兼容（25.x 实测 + 30.x AST dump 实证）
        frm = select.args.get("from") or select.args.get("from_")
        if frm is not None:
            self._add_source(frm.this, out)
        for join in select.args.get("joins") or []:
            self._add_source(join.this, out)
        return out

    def _add_source(self, node, out: list[_Source]) -> None:
        if isinstance(node, exp.Table):
            alias = node.alias_or_name
            out.append(_Source(alias=alias, kind="table", name=node.name, node=node))
        elif isinstance(node, exp.Subquery):
            out.append(_Source(alias=node.alias or "", kind="subquery", name="", node=node))
        else:
            alias = getattr(node, "alias", None) or ""
            out.append(_Source(alias=alias, kind="derived", name="", node=node))

    def _resolve_column(self, col: exp.Column, sources: list[_Source], depth: int) -> list[ColumnRef]:
        name = col.name
        qual = col.table or ""
        candidates: list[_Source] = []
        if qual:
            candidates = [s for s in sources if s.alias == qual]
            if not candidates and qual in self._ctes:
                candidates = [_Source(alias=qual, kind="table", name=qual, node=None)]
        else:
            # 未限定：唯一子查询输出匹配优先；否则第一个来源（文档化确定性归因）
            subq_hits = [s for s in sources if s.kind != "table" and name in self._outputs_of_source(s)]
            candidates = subq_hits or sources[:1]

        refs: list[ColumnRef] = []
        for s in candidates:
            refs.extend(self._resolve_source_column(s, name, depth))
        return refs

    def _outputs_of_source(self, s: _Source) -> list[str]:
        sel = _select_of(self._ctes.get(s.name) if s.kind == "table" else s.node)
        return self._outputs(sel) if sel is not None else []

    def _outputs(self, select: exp.Select) -> list[str]:
        return [_output_name(p, self.dialect) for p in select.expressions]

    def _resolve_source_column(self, s: _Source, col_name: str, depth: int) -> list[ColumnRef]:
        if depth > MAX_DEPTH:
            return []
        if s.kind == "table":
            # CTE 引用：钻入 CTE 查询
            if s.name in self._ctes:
                return self._resolve_source_column(
                    _Source(alias=s.alias, kind="subquery", name="", node=self._ctes[s.name]), col_name, depth
                )
            return [ColumnRef(table=s.name, column=col_name)]
        if s.kind in ("subquery", "derived"):
            sel = _select_of(s.node)
            if sel is None:
                return []
            sub_sources = self._sources(sel)
            refs: list[ColumnRef] = []
            for proj in sel.expressions:
                if _output_name(proj, self.dialect) == col_name:
                    for c in proj.find_all(exp.Column):
                        refs.extend(self._resolve_column(c, sub_sources, depth + 1))
                    return refs
            return []
        return []
