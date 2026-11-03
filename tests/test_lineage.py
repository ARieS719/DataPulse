"""血缘模块单元测试：SQLGlot 字段级提取 + NetworkX 图/BFS + 目录扫描器。"""

from __future__ import annotations

import pytest
from dq_engine.lineage.graph import LineageGraph
from dq_engine.lineage.parser import ColumnEdge, LineageExtractor
from dq_engine.lineage.scanner import detect_target_table, scan_directory


def _edges(sql: str, target: str = "out_tbl", dialect: str | None = None, schema=None) -> list[ColumnEdge]:
    return LineageExtractor(dialect=dialect, schema=schema).extract(sql, target).edges


def _pairs(sql: str, target: str = "out_tbl", dialect: str | None = None, schema=None):
    return {(e.source_table, e.source_column, e.target_column) for e in _edges(sql, target, dialect, schema)}


# ---------------------------------------------------------------- 提取


def test_simple_projection():
    pairs = _pairs("SELECT user_id, amount FROM fact_order", target="dwd_fact_order")
    assert pairs == {("fact_order", "user_id", "user_id"), ("fact_order", "amount", "amount")}


def test_alias_and_expression():
    pairs = _pairs("SELECT a AS x, a + b AS c FROM t", target="out")
    assert pairs == {("t", "a", "x"), ("t", "a", "c"), ("t", "b", "c")}


def test_qualified_join_columns_only():
    sql = "SELECT o.order_id, p.pay_amount FROM fact_order o JOIN fact_payment p ON o.order_id = p.order_id"
    edges = _edges(sql, target="out")
    pairs = {(e.source_table, e.source_column, e.target_column) for e in edges}
    assert pairs == {("fact_order", "order_id", "order_id"), ("fact_payment", "pay_amount", "pay_amount")}
    # JOIN ON 条件列不产生字段映射
    assert not any(e.source_table == "fact_payment" and e.source_column == "order_id" for e in edges)


def test_aggregate_expression():
    pairs = _pairs("SELECT SUM(amount) AS total FROM fact_order", target="dws")
    assert pairs == {("fact_order", "amount", "total")}


def test_cte_resolution():
    pairs = _pairs("WITH x AS (SELECT user_id FROM dim_user) SELECT user_id FROM x", target="out")
    assert pairs == {("dim_user", "user_id", "user_id")}


def test_nested_subquery_star_expansion():
    pairs = _pairs("SELECT * FROM (SELECT user_id, age FROM dim_user) u", target="out")
    assert pairs == {("dim_user", "user_id", "user_id"), ("dim_user", "age", "age")}


def test_constant_produces_no_edge():
    assert _edges("SELECT 1 AS one FROM t", target="out") == []


def test_where_columns_not_mapped():
    edges = _edges("SELECT user_id FROM t WHERE status = '已支付'", target="out")
    assert len(edges) == 1 and edges[0].source_column == "user_id"


def test_unqualified_multi_source_first_table():
    edges = _edges(
        "SELECT user_id FROM fact_order o JOIN dim_user u ON o.user_id = u.user_id", target="out"
    )
    assert {e.source_table for e in edges} == {"fact_order"}  # 确定性归因：第一来源


def test_star_without_schema_marks_wildcard():
    pairs = _pairs("SELECT * FROM dim_user", target="out")
    assert pairs == {("dim_user", "*", "*")}


def test_star_with_schema_expands():
    pairs = _pairs("SELECT * FROM dim_user", target="out", schema={"dim_user": ["user_id", "age"]})
    assert pairs == {("dim_user", "user_id", "user_id"), ("dim_user", "age", "age")}


def test_spark_backtick_identifiers():
    pairs = _pairs("SELECT `a` FROM `db`.`t1`", target="out", dialect="spark")
    assert pairs == {("t1", "a", "a")}


def test_ctas_extraction():
    pairs = _pairs("CREATE TABLE dwd.fact_order AS SELECT order_id, amount FROM ods_fact_order", target="fact_order")
    assert pairs == {("ods_fact_order", "order_id", "order_id"), ("ods_fact_order", "amount", "amount")}


def test_invalid_sql_raises():
    with pytest.raises(ValueError, match="SELECT"):
        _edges("DELETE FROM t", target="out")


# ---------------------------------------------------------------- 图 / BFS


def _chain_graph():
    g = LineageGraph()
    g.add_column_edges(
        [
            ColumnEdge("t1", "a", "t2", "a", "a"),
            ColumnEdge("t2", "a", "t3", "a", "a"),
            ColumnEdge("t3", "a", "t4", "a", "a"),
        ]
    )
    return g


def test_table_graph_aggregation_and_bfs():
    g = _chain_graph()
    assert g.table_downstream("t1") == ["t2", "t3", "t4"]
    assert g.table_upstream("t4") == ["t3", "t2", "t1"]
    assert g.table_downstream("t4") == []
    assert g.table_upstream("t1") == []


def test_blast_radius_depths():
    g = _chain_graph()
    r = g.blast_radius("t1")
    assert r.downstream_tables == ["t2", "t3", "t4"]
    assert r.depth_map == {"t1": 0, "t2": 1, "t3": 2, "t4": 3}
    assert r.max_depth == 3


def test_bfs_terminates_on_cycle():
    g = _chain_graph()
    g.add_column_edges([ColumnEdge("t2", "a", "t1", "a", "a")])  # 制造环
    assert g.table_downstream("t1") == ["t2", "t3", "t4"]
    assert g.blast_radius("t1").max_depth == 3


def test_column_downstream():
    g = _chain_graph()
    cols = g.column_downstream("t1", "a")
    assert cols == [("t2", "a"), ("t3", "a"), ("t4", "a")]
    assert g.column_downstream("t9") == []


def test_stats():
    g = _chain_graph()
    assert g.stats() == {
        "column_nodes": 4,
        "column_edges": 3,
        "table_nodes": 4,
        "table_edges": 3,
    }


def test_to_dot_shape():
    g = _chain_graph()
    dot = g.to_dot()
    assert dot.startswith("digraph lineage")
    assert dot.count("->") == 3
    for t in ("t1", "t2", "t3", "t4"):
        assert f'"{t}"' in dot
    assert "fillcolor" not in dot  # 无标红


def test_to_dot_highlight():
    g = _chain_graph()
    dot = g.to_dot(highlight=["t2"])
    assert '"t2" [style="rounded,filled", fillcolor="#ffb3b3"]' in dot
    assert dot.count("fillcolor") == 1


# ---------------------------------------------------------------- 扫描器


def test_detect_target_table():
    assert detect_target_table("CREATE TABLE ods.fact_order AS SELECT ...", "x") == "fact_order"
    assert detect_target_table("CREATE OR REPLACE TABLE `dws`.`dws_trade` AS ...", "x") == "dws_trade"
    assert detect_target_table("SELECT 1", "my_script") == "my_script"


def test_scan_directory_chain(tmp_path):
    (tmp_path / "01_ods.sql").write_text(
        "CREATE TABLE ods.fact_order AS SELECT order_id, amount FROM source_trade", encoding="utf-8"
    )
    (tmp_path / "dwd_fact_order.sql").write_text(
        "SELECT order_id, amount FROM fact_order WHERE status = '已支付'", encoding="utf-8"
    )
    (tmp_path / "03_dws.sql").write_text(
        "CREATE TABLE `dws`.`dws_trade_daily` AS "
        "SELECT date(order_time) AS ds, SUM(amount) AS order_amount "
        "FROM dwd_fact_order GROUP BY date(order_time)",
        encoding="utf-8",
    )
    graph, files, elapsed = scan_directory(tmp_path)
    assert elapsed >= 0
    assert all(f.error is None for f in files)
    assert {f.target_table for f in files} == {"fact_order", "dwd_fact_order", "dws_trade_daily"}
    assert graph.table_downstream("fact_order") == ["dwd_fact_order", "dws_trade_daily"]


def test_scan_directory_benchmark_smoke(tmp_path):
    """30 个脚本的扫描功能性 + 宽松计时冒烟（严格 <500ms 证据在 M4 基准运行留痕）。"""
    for i in range(30):
        (tmp_path / f"s{i:02d}.sql").write_text(
            f"SELECT a, b FROM t{i - 1}" if i else "SELECT a, b FROM source", encoding="utf-8"
        )
    graph, files, elapsed = scan_directory(tmp_path)
    assert len(files) == 30 and all(f.error is None for f in files)
    assert graph.stats()["column_edges"] == 60  # 30 脚本 × 2 列
    assert elapsed < 2.0
