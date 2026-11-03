"""M4：血缘 DAG（NetworkX）与 BFS 爆炸半径计算（毫秒级）。

双层图：
  * 字段级：节点 = (table, column)，边 = ColumnEdge
  * 表级：  由字段级边自动聚合（列 → 列 的边投影为 表 → 表 的边）

爆炸半径：某表熔断后，BFS 遍历下游得到全部受波及表与报表，
并按距离分层输出（供大盘标红拓扑与 AI-RCA 证据组装）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

import networkx as nx

from dq_engine.lineage.parser import ColumnEdge


@dataclass
class BlastReport:
    table: str
    downstream_tables: list[str] = field(default_factory=list)
    depth_map: dict = field(default_factory=dict)
    max_depth: int = 0

    def to_dict(self) -> dict:
        return {
            "table": self.table,
            "downstream_tables": self.downstream_tables,
            "depth_map": self.depth_map,
            "max_depth": self.max_depth,
        }


class LineageGraph:
    def __init__(self) -> None:
        self._col = nx.DiGraph()
        self._tbl = nx.DiGraph()

    # ------------------------------------------------------------ 构建

    def add_column_edges(self, edges: Iterable[ColumnEdge]) -> None:
        for e in edges:
            self._col.add_edge((e.source_table, e.source_column), (e.target_table, e.target_column), expression=e.expression)
            if e.source_table != e.target_table:
                self._tbl.add_edge(e.source_table, e.target_table)

    # ------------------------------------------------------------ 爆炸半径

    def table_downstream(self, table: str) -> list[str]:
        """BFS 全下游（发现序，即按层展开）。"""
        if table not in self._tbl:
            return []
        seen: list[str] = []
        visited = {table}
        frontier = [table]
        while frontier:
            nxt = []
            for node in frontier:
                for succ in sorted(self._tbl.successors(node)):
                    if succ not in visited:
                        visited.add(succ)
                        seen.append(succ)
                        nxt.append(succ)
            frontier = nxt
        return seen

    def table_upstream(self, table: str) -> list[str]:
        if table not in self._tbl:
            return []
        seen: list[str] = []
        visited = {table}
        frontier = [table]
        while frontier:
            nxt = []
            for node in frontier:
                for pred in sorted(self._tbl.predecessors(node)):
                    if pred not in visited:
                        visited.add(pred)
                        seen.append(pred)
                        nxt.append(pred)
            frontier = nxt
        return seen

    def blast_radius(self, table: str) -> BlastReport:
        """某表熔断的爆炸半径：下游表 + 距离分层（BFS）。"""
        report = BlastReport(table=table)
        if table not in self._tbl:
            return report
        depth_map = {table: 0}
        frontier = [table]
        depth = 0
        while frontier:
            nxt = []
            for node in frontier:
                for succ in sorted(self._tbl.successors(node)):
                    if succ not in depth_map:
                        depth_map[succ] = depth + 1
                        report.downstream_tables.append(succ)
                        nxt.append(succ)
            frontier = nxt
            depth += 1
        report.depth_map = depth_map
        report.max_depth = max(depth_map.values()) if depth_map else 0
        return report

    def column_downstream(self, table: str, column: str | None = None) -> list[tuple[str, str]]:
        """字段级下游：列出受波及的 (table, column)。"""
        starts = [n for n in self._col.nodes if n[0] == table and (column is None or n[1] == column)]
        if not starts:
            return []
        seen: list[tuple[str, str]] = []
        visited = set(starts)
        frontier = list(starts)
        while frontier:
            nxt = []
            for node in frontier:
                for succ in sorted(self._col.successors(node)):
                    if succ not in visited:
                        visited.add(succ)
                        seen.append(succ)
                        nxt.append(succ)
            frontier = nxt
        return seen

    # ------------------------------------------------------------ 统计

    def stats(self) -> dict:
        return {
            "column_nodes": self._col.number_of_nodes(),
            "column_edges": self._col.number_of_edges(),
            "table_nodes": self._tbl.number_of_nodes(),
            "table_edges": self._tbl.number_of_edges(),
        }

    def to_networkx(self, level: str = "table"):
        return self._tbl if level == "table" else self._col

    def to_dot(self, highlight: list[str] | None = None) -> str:
        """导出 Graphviz DOT（Streamlit 大盘 st.graphviz_chart 直接渲染；highlight 标红）。"""
        hl = set(highlight or [])
        lines = ["digraph lineage {", '  rankdir="LR";', '  node [shape=box, style="rounded"];']
        for node in sorted(self._tbl.nodes):
            if node in hl:
                lines.append(f'  "{node}" [style="rounded,filled", fillcolor="#ffb3b3"];')
            else:
                lines.append(f'  "{node}";')
        for u, v in sorted(self._tbl.edges):
            lines.append(f'  "{u}" -> "{v}";')
        lines.append("}")
        return "\n".join(lines)
