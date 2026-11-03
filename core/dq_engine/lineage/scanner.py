"""M4：血缘扫描器 —— 遍历数仓 SQL 脚本目录，构建全链路血缘图。

目标表识别规则：
  1. 脚本内 CREATE [OR REPLACE] TABLE [IF NOT EXISTS] `<table>` → 以建表名为目标表；
  2. 否则以脚本文件名（去扩展名）为目标表。

用法：
  graph, files, elapsed = scan_directory(Path("sql_scripts"), dialect="spark")
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from dq_engine.lineage.graph import LineageGraph
from dq_engine.lineage.parser import ColumnEdge, LineageExtractor

CREATE_TABLE_RE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(`?(?:\w+`?\.`?)*\w+`?)",
    re.IGNORECASE,
)


@dataclass
class FileLineage:
    path: str
    target_table: str
    edges: list[ColumnEdge] = field(default_factory=list)
    error: str | None = None


def detect_target_table(sql: str, stem: str) -> str:
    m = CREATE_TABLE_RE.search(sql)
    if m:
        return m.group(1).replace("`", "").split(".")[-1]
    return stem


def scan_directory(
    sql_dir: str | Path,
    dialect: str | None = "spark",
    schema: dict | None = None,
    extractor: LineageExtractor | None = None,
):
    """扫描目录下所有 *.sql，返回 (LineageGraph, [FileLineage], elapsed_seconds)。"""
    root = Path(sql_dir)
    if not root.is_dir():
        raise NotADirectoryError(str(root))

    extractor = extractor or LineageExtractor(dialect=dialect, schema=schema)
    graph = LineageGraph()
    files: list[FileLineage] = []
    t0 = time.perf_counter()

    for path in sorted(root.rglob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        fl = FileLineage(path=str(path), target_table=detect_target_table(sql, path.stem))
        try:
            lineage = extractor.extract(sql, target_table=fl.target_table)
            fl.edges = lineage.edges
            graph.add_column_edges(lineage.edges)
        except Exception as exc:  # 单脚本失败不阻塞整体扫描，错误留痕
            fl.error = f"{type(exc).__name__}: {exc}"
        files.append(fl)

    return graph, files, time.perf_counter() - t0
